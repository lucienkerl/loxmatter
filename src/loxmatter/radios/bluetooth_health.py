# loxmatter - connects Matter devices to a Loxone Miniserver.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Bluetooth and power faults, counted from the kernel log.

Design 2026-09-22, section 7.1. On 21 September 2026 every commissioning
failure on pi3-andi had its cause in the kernel log and nowhere else:
`Frame reassembly failed` while PASE ran, `Unable to disable scanning` while
matter-server waited for advertisements, and the next morning `Undervoltage
detected!`. The bridge container reads `/dev/kmsg` (mounted read-only,
section 8); `dmesg` itself is blocked by Docker's seccomp profile.

Raw lines never leave this module. Callers get a category and the kernel's
timestamp (microseconds since boot), nothing else.
"""

from __future__ import annotations

import asyncio
import os
import re
import stat
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

Category = Literal["transport", "stuck", "power"]
CATEGORIES: Final[tuple[Category, ...]] = ("transport", "stuck", "power")

# `prio,seq,usec,flags;message` - the record format of /dev/kmsg.
_RECORD: Final = re.compile(r"^\d+,\d+,(?P<usec>\d+),[^;]*;(?P<message>.*)$")
_PATTERNS: Final[tuple[tuple[Category, re.Pattern[str]], ...]] = (
    (
        "transport",
        re.compile(
            r"^Bluetooth: hci\d+: (Frame reassembly failed|Received unexpected HCI Event"
            r"|unexpected event 0x|\w+ packet for unknown connection handle)"
        ),
    ),
    (
        "stuck",
        re.compile(
            r"^Bluetooth: hci\d+: (Unable to disable scanning|stop background scanning failed"
            r"|command 0x[0-9a-f]+ tx timeout|Opcode 0x[0-9a-f]+ failed)"
        ),
    ),
    ("power", re.compile(r"^hwmon hwmon\d+: Undervoltage detected!")),
)
_READ_SIZE: Final = 8192
# The char-device branch of `_records` below continues past a
# `BrokenPipeError` (the record it was about to read was overwritten - the
# next read continues with the oldest one still there). A ring buffer that
# is being overwritten faster than this generator can keep up with would
# otherwise spin on that `continue` forever; this many consecutive misses
# gives up instead (final review item 6) - normal operation, even on a busy
# log, sees at most a handful.
_MAX_CONSECUTIVE_BROKEN_PIPES: Final = 300


@dataclass(frozen=True)
class KernelFinding:
    category: Category
    usec: int


def classify_kmsg_record(record: str) -> KernelFinding | None:
    match = _RECORD.match(record.strip())
    if match is None:
        return None
    message = match.group("message")
    for category, pattern in _PATTERNS:
        if pattern.match(message):
            return KernelFinding(category, int(match.group("usec")))
    return None


def counts_since(findings: Iterable[KernelFinding], since_usec: int) -> dict[str, int]:
    counts: dict[str, int] = dict.fromkeys(CATEGORIES, 0)
    for finding in findings:
        if finding.usec >= since_usec:
            counts[finding.category] += 1
    return counts


def _records(path: Path) -> Iterator[str]:
    """Every record currently in the ring buffer. `/dev/kmsg` returns one
    record per `read()` and `EAGAIN` at the end when opened non-blocking; a
    regular file (the test fixture) is read line by line."""
    if not stat.S_ISCHR(path.stat().st_mode):
        yield from path.read_text(encoding="utf-8", errors="replace").splitlines()
        return
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        broken_pipes = 0
        while True:
            try:
                chunk = os.read(fd, _READ_SIZE)
            except BlockingIOError:
                return
            except BrokenPipeError:
                # The record we were about to read was overwritten; the next
                # read continues with the oldest one still there - but only
                # up to a bound (see `_MAX_CONSECUTIVE_BROKEN_PIPES` above),
                # so a ring buffer being overwritten faster than this
                # generator can keep up with cannot spin here forever.
                broken_pipes += 1
                if broken_pipes >= _MAX_CONSECUTIVE_BROKEN_PIPES:
                    return
                continue
            broken_pipes = 0
            if not chunk:
                return
            yield chunk.decode("utf-8", errors="replace")
    finally:
        os.close(fd)


class KernelLog:
    """`findings()` re-opens `/dev/kmsg` and drains the whole ring buffer -
    one syscall per record, three regexes each. Called every `sample_interval`
    (2 s) from `CommissioningTracker`, that is cheap enough on its own, but it
    is blocking I/O, and the tracker calls it from async code that shares an
    event loop with the matter-server WebSocket - on a Raspberry Pi 3 that can
    stall it (final review item 4). Two independent fixes:

    - **A cache.** A reading is reused for `cache_seconds` instead of re-read
      on every call - `findings()` and `findings_async()` share the same
      cache, so whichever one runs first within the window pays the cost and
      the other reads its answer.
    - **`findings_async()`**, which runs the actual read in a thread
      (`asyncio.to_thread`) instead of on the calling task's own turn of the
      event loop. `CommissioningTracker.sample()` - the path that runs once
      per `sample_interval` from the POST route, sharing the loop with
      everything else the bridge is doing - uses this one exclusively.
      `findings()` stays for the diagnostics check (`api/diagnostics.py`),
      which runs once per request, not once per second, and reads
      synchronously like every other check there."""

    def __init__(
        self,
        path: Path = Path("/dev/kmsg"),
        uptime_path: Path = Path("/proc/uptime"),
        *,
        cache_seconds: float = 2.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = path
        self._uptime_path = uptime_path
        self._cache_seconds = cache_seconds
        self._monotonic = monotonic
        self._cache: list[KernelFinding] | None = None
        self._cache_at: float | None = None

    def _read(self) -> list[KernelFinding] | None:
        """`None` when the log cannot be read (not mounted, `dmesg_restrict`).
        The actual, potentially blocking read - never call this directly
        from async code (see class docstring); `findings()` and
        `findings_async()` are the cached entry points."""
        try:
            return [
                finding
                for record in _records(self._path)
                if (finding := classify_kmsg_record(record)) is not None
            ]
        except OSError:
            return None

    def _cache_is_fresh(self) -> bool:
        return (
            self._cache_at is not None
            and (self._monotonic() - self._cache_at) < self._cache_seconds
        )

    def findings(self) -> list[KernelFinding] | None:
        """The synchronous, cached accessor - for the diagnostics check
        (`api/diagnostics.py`), which runs per request, not per second, and
        has no event loop of its own to block. `CommissioningTracker` uses
        `findings_async()` instead (see class docstring)."""
        if self._cache_is_fresh():
            return self._cache
        self._cache = self._read()
        self._cache_at = self._monotonic()
        return self._cache

    async def findings_async(self) -> list[KernelFinding] | None:
        """The cached accessor for async callers - the actual read, on a
        cache miss, happens in a thread (`asyncio.to_thread`) so the calling
        event loop is never blocked by it (see class docstring)."""
        if self._cache_is_fresh():
            return self._cache
        self._cache = await asyncio.to_thread(self._read)
        self._cache_at = self._monotonic()
        return self._cache

    def now_usec(self) -> int | None:
        try:
            seconds = float(self._uptime_path.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            return None
        return round(seconds * 1_000_000)
