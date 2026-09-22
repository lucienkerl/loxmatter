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

import os
import re
import stat
from collections.abc import Iterable, Iterator
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
        while True:
            try:
                chunk = os.read(fd, _READ_SIZE)
            except BlockingIOError:
                return
            except BrokenPipeError:
                # The record we were about to read was overwritten; the next
                # read continues with the oldest one still there.
                continue
            if not chunk:
                return
            yield chunk.decode("utf-8", errors="replace")
    finally:
        os.close(fd)


class KernelLog:
    def __init__(
        self, path: Path = Path("/dev/kmsg"), uptime_path: Path = Path("/proc/uptime")
    ) -> None:
        self._path = path
        self._uptime_path = uptime_path

    def findings(self) -> list[KernelFinding] | None:
        """`None` when the log cannot be read (not mounted, `dmesg_restrict`)."""
        try:
            return [
                finding
                for record in _records(self._path)
                if (finding := classify_kmsg_record(record)) is not None
            ]
        except OSError:
            return None

    def now_usec(self) -> int | None:
        try:
            seconds = float(self._uptime_path.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            return None
        return round(seconds * 1_000_000)
