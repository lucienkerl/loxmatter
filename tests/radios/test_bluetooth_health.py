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

from pathlib import Path

import pytest

from loxmatter.radios.bluetooth_health import (
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
