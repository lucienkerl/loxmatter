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
"""Bluetooth and power faults from the kernel log (design 2026-09-22,
section 7.1). The fixture holds the 1131 `Bluetooth: hci` and `hwmon` records
read from `/dev/kmsg` on pi3-andi on 22 September 2026, uptime 31536.15 s."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from loxmatter.radios import bluetooth_health
from loxmatter.radios.bluetooth_health import (
    _MAX_CONSECUTIVE_BROKEN_PIPES,
    KernelFinding,
    KernelLog,
    classify_kmsg_record,
    counts_since,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "kmsg" / "pi3-andi-2026-09-22.txt"


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (
            "3,1019,1019389516,-;Bluetooth: hci0: Frame reassembly failed (-84)",
            KernelFinding("transport", 1019389516),
        ),
        (
            "3,20,77,-;Bluetooth: hci0: Received unexpected HCI Event 0x00",
            KernelFinding("transport", 77),
        ),
        (
            "3,21,78,-;Bluetooth: hci0: unexpected event 0x00 length: 2 < 3",
            KernelFinding("transport", 78),
        ),
        (
            "3,22,79,-;Bluetooth: hci0: ACL packet for unknown connection handle 64",
            KernelFinding("transport", 79),
        ),
        ("3,1,5,-;Bluetooth: hci0: Unable to disable scanning: -16", KernelFinding("stuck", 5)),
        (
            "3,2,6,-;Bluetooth: hci0: stop background scanning failed: -16",
            KernelFinding("stuck", 6),
        ),
        ("3,3,7,-;Bluetooth: hci0: command 0x200c tx timeout", KernelFinding("stuck", 7)),
        ("3,4,8,-;Bluetooth: hci0: Opcode 0x200c failed: -110", KernelFinding("stuck", 8)),
        ("2,5,9,-;hwmon hwmon1: Undervoltage detected!", KernelFinding("power", 9)),
        ("6,6,10,-;hwmon hwmon1: Voltage normalised", None),
        ("6,390,17931589,-;Bluetooth: hci0: BCM: chip id 94", None),
        ("not a kmsg record", None),
    ],
)
def test_records_are_classified(record: str, expected: KernelFinding | None) -> None:
    assert classify_kmsg_record(record) == expected


def test_the_measured_log_counts() -> None:
    findings = KernelLog(FIXTURE).findings()
    assert findings is not None
    assert counts_since(findings, 0) == {"transport": 435, "stuck": 97, "power": 296}


def test_counts_respect_the_window() -> None:
    findings = [
        KernelFinding("transport", 100),
        KernelFinding("transport", 300),
        KernelFinding("power", 50),
    ]
    assert counts_since(findings, 200) == {"transport": 1, "stuck": 0, "power": 0}


def test_now_comes_from_proc_uptime(tmp_path: Path) -> None:
    uptime = tmp_path / "uptime"
    uptime.write_text("31536.15 105813.72\n", encoding="utf-8")
    assert KernelLog(FIXTURE, uptime).now_usec() == 31_536_150_000


def test_an_unreadable_log_reads_as_not_available(tmp_path: Path) -> None:
    assert KernelLog(tmp_path / "missing").findings() is None
    assert KernelLog(tmp_path / "missing", tmp_path / "missing").now_usec() is None


class _Clock:
    """An injectable `monotonic` stand-in - starts at 0, advances only when
    a test moves it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_findings_reuses_a_reading_within_the_cache_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final review item 4: `findings()` used to re-open and re-drain
    `/dev/kmsg` on every call. A counting wrapper around the module's own
    `_records` proves two calls inside `cache_seconds` read the file once,
    and a third call after the window has passed reads it again."""
    reads: list[Path] = []
    real_records = bluetooth_health._records

    def counting_records(path: Path):
        reads.append(path)
        yield from real_records(path)

    monkeypatch.setattr(bluetooth_health, "_records", counting_records)
    clock = _Clock()
    log = KernelLog(FIXTURE, cache_seconds=2.0, monotonic=clock)

    log.findings()
    clock.now = 1.0
    log.findings()
    assert len(reads) == 1

    clock.now = 5.0
    log.findings()
    assert len(reads) == 2


async def test_findings_async_reuses_the_same_cache_and_runs_off_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`findings_async()` shares the cache with `findings()` (whichever runs
    first pays the read), and its own read happens via `asyncio.to_thread` -
    proven here by recording the thread id the read ran on, which must differ
    from this test's own (the event loop's) thread."""
    import threading

    reader_thread_ids: list[int] = []
    real_records = bluetooth_health._records

    def recording_records(path: Path):
        reader_thread_ids.append(threading.get_ident())
        yield from real_records(path)

    monkeypatch.setattr(bluetooth_health, "_records", recording_records)
    clock = _Clock()
    log = KernelLog(FIXTURE, cache_seconds=2.0, monotonic=clock)

    findings = await log.findings_async()
    assert findings is not None
    assert len(reader_thread_ids) == 1
    assert reader_thread_ids[0] != threading.get_ident()

    clock.now = 0.5
    await log.findings_async()
    assert len(reader_thread_ids) == 1  # still cached, no second read

    clock.now = 3.0
    cached = log.findings()  # the sync accessor reads the same cache
    assert cached == findings
    assert len(reader_thread_ids) == 2  # the window passed, one fresh read


def test_the_char_device_branch_reads_a_fifo_non_blocking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Final review item 6: only the regular-file branch of `_records` had a
    test - the char-device branch (`os.O_NONBLOCK`, `BlockingIOError` ends
    the read, `BrokenPipeError` continues, chunk decoding) is the only one
    that runs on the Pi, since `/dev/kmsg` is a char device.

    A FIFO is not itself a char device (`stat.S_ISFIFO`, not `S_ISCHR`) -
    unprivileged code cannot create a real one - but `os.O_NONBLOCK` is
    meaningful on it exactly the way it is on `/dev/kmsg`: a read past what
    is buffered raises `BlockingIOError` instead of blocking. Patching
    `stat.S_ISCHR` to true for this path is what sends `_records` into that
    branch; the FIFO itself is what then actually exercises it end to end -
    the non-blocking open, a real `os.read()` of the two records written
    below, and the `BlockingIOError` that ends the generator once the pipe
    is empty (proven implicitly: this test returns rather than hanging).

    `keepalive_fd` is opened `O_RDWR` and kept open for the whole test: a
    FIFO opened write-only blocks until a reader exists, and closing the
    only writer would hand `_records`'s own read a false EOF (`chunk ==
    b""`) instead of the `BlockingIOError` this test means to exercise."""
    fifo_path = tmp_path / "kmsg-fifo"
    os.mkfifo(fifo_path)
    keepalive_fd = os.open(fifo_path, os.O_RDWR | os.O_NONBLOCK)
    try:
        payload = (
            b"3,1,5,-;Bluetooth: hci0: Unable to disable scanning: -16\n"
            b"2,2,9,-;hwmon hwmon1: Undervoltage detected!\n"
        )
        os.write(keepalive_fd, payload)
        monkeypatch.setattr(bluetooth_health.stat, "S_ISCHR", lambda mode: True)
        records = "".join(bluetooth_health._records(fifo_path)).splitlines()
        findings = [f for record in records if (f := classify_kmsg_record(record)) is not None]
        assert findings == [KernelFinding("stuck", 5), KernelFinding("power", 9)]
    finally:
        os.close(keepalive_fd)


def test_a_pathological_run_of_broken_pipes_does_not_spin_forever(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Final review item 6: `BrokenPipeError` makes `_records` `continue` -
    a ring buffer overwritten faster than this generator can keep up with
    must not spin on that forever. `os.read` is patched to always raise it.

    Asserting only that `list(_records(...))` returns proves nothing about
    WHY it returned - a regressed bound that let the loop run far longer
    (thousands of reads instead of `_MAX_CONSECUTIVE_BROKEN_PIPES`) would
    still eventually finish and pass a test that only checks the result,
    while hanging the real event loop on the Pi in practice. Counting the
    `os.read` calls and comparing them against the bound constant itself
    makes a regression in the bound fail this test instead of just running
    slower."""
    path = tmp_path / "fake-char-device"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(bluetooth_health.stat, "S_ISCHR", lambda mode: True)

    read_calls = 0

    def always_broken_pipe(fd: int, size: int) -> bytes:
        nonlocal read_calls
        read_calls += 1
        raise BrokenPipeError

    monkeypatch.setattr(bluetooth_health.os, "read", always_broken_pipe)
    assert list(bluetooth_health._records(path)) == []
    assert read_calls == _MAX_CONSECUTIVE_BROKEN_PIPES
