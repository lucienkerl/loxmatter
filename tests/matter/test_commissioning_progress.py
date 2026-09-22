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
"""The phases of a commissioning attempt (design 2026-09-22, section 5) and
the failure reasons (section 7.2). BlueZ and the kernel log are fakes; the
matter-server texts are the ones logged on pi3-andi on 21 September 2026."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from loxmatter.matter.commissioning_progress import (
    CommissioningTracker,
    Discriminator,
    classify_failure,
)
from loxmatter.radios.bluetooth_health import KernelFinding
from loxmatter.radios.bluez import AdapterState, BluezSnapshot, MatterAdvert


def _advert(
    discriminator: int, *, connected: bool = False, address: str = "E0:55:02:06:D3:79"
) -> MatterAdvert:
    return MatterAdvert(
        address=address,
        name=None,
        rssi=-60,
        discriminator=discriminator,
        vendor_id=4476,
        product_id=36871,
        connected=connected,
        adapter="/org/bluez/hci0",
    )


class FakeBluez:
    def __init__(self) -> None:
        self.snapshots: list[BluezSnapshot | None] = []
        self.last: BluezSnapshot | None = None

    async def snapshot(self) -> BluezSnapshot | None:
        if self.snapshots:
            self.last = self.snapshots.pop(0)
        return self.last


class FakeKernel:
    def __init__(self) -> None:
        self.now = 1_000_000_000
        self.log: list[KernelFinding] | None = []

    def findings(self) -> list[KernelFinding] | None:
        return self.log

    def now_usec(self) -> int | None:
        return self.now


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 22, 7, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


SCANNING = AdapterState(name="hci0", powered=True, discovering=True)


def _tracker() -> tuple[CommissioningTracker, FakeBluez, FakeKernel, Clock]:
    bluez, kernel, clock = FakeBluez(), FakeKernel(), Clock()
    return CommissioningTracker(bluez=bluez, kernel=kernel, clock=clock), bluez, kernel, clock


def test_a_short_discriminator_matches_the_top_four_bits() -> None:
    assert Discriminator(4, "short").matches(1059)
    assert not Discriminator(9, "short").matches(1059)
    assert Discriminator(1059, "long").matches(1059)
    assert not Discriminator(1058, "long").matches(1059)


@pytest.mark.parametrize(
    ("text", "reached", "reason"),
    [
        (
            "Commissioning failed: Commission failed: discovery of node with discriminator 9 failed: No commissionable device was discovered",
            "searching",
            "not_found",
        ),
        (
            "Commissioning failed: Commission failed: discovery of node with discriminator 1 failed: No device could be commissioned (1 of 1 started attempt(s) failed, 1 discovered)",
            "searching",
            "connection_lost",
        ),
        ("Commissioning failed: something else", "connected", "connection_lost"),
        ("Commissioning failed: something else", "searching", "other"),
    ],
)
def test_failures_are_classified(text: str, reached: str, reason: str) -> None:
    assert classify_failure(text, reached) == reason  # type: ignore[arg-type]


async def test_the_phases_follow_bluez_and_node_added() -> None:
    tracker, bluez, _, _ = _tracker()
    tracker.start(Discriminator(1, "short"))
    assert tracker.phase == "searching"

    bluez.snapshots = [BluezSnapshot([_advert(0x100 | 5)], SCANNING)]  # short discriminator 1
    await tracker.sample()
    assert tracker.phase == "found"

    bluez.snapshots = [BluezSnapshot([_advert(0x105, connected=True)], SCANNING)]
    await tracker.sample()
    assert tracker.phase == "connected"

    tracker.node_added(3)
    assert tracker.phase == "joined"

    await tracker.finish(None)
    assert tracker.phase == "done"


async def test_phases_never_move_backwards() -> None:
    tracker, bluez, _, _ = _tracker()
    tracker.start(Discriminator(1, "short"))
    tracker.node_added(3)
    bluez.snapshots = [BluezSnapshot([_advert(0x105)], SCANNING)]
    await tracker.sample()
    assert tracker.phase == "joined"


async def test_an_ip_attempt_goes_from_searching_to_joined() -> None:
    """An IP-commissioned device (no discriminator) skips BLE phases and goes
    straight from searching to joined when node_added fires. Verify that
    sample() discovers it: the nearby list contains the advertised device with
    matches=False because the attempt has no discriminator to match against."""
    tracker, bluez, _, _ = _tracker()
    tracker.start(None)
    bluez.snapshots = [BluezSnapshot([_advert(1059)], SCANNING)]
    await tracker.sample()
    assert tracker.phase == "searching"
    nearby = tracker.status()["attempt"]["nearby"]  # type: ignore[index]
    assert len(nearby) == 1
    assert nearby[0]["matches"] is False
    tracker.node_added(4)
    assert tracker.phase == "joined"


async def test_a_long_discriminator_drives_found_and_connected() -> None:
    tracker, bluez, _, _ = _tracker()
    tracker.start(Discriminator(1059, "long"))

    bluez.snapshots = [BluezSnapshot([_advert(1059)], SCANNING)]
    await tracker.sample()
    assert tracker.phase == "found"

    bluez.snapshots = [BluezSnapshot([_advert(1059, connected=True)], SCANNING)]
    await tracker.sample()
    assert tracker.phase == "connected"


async def test_the_status_carries_nearby_devices_and_marks_the_match() -> None:
    tracker, bluez, _, _ = _tracker()
    tracker.start(Discriminator(9, "short"))
    bluez.snapshots = [BluezSnapshot([_advert(1059, address="FB:73:82:07:E3:AD")], SCANNING)]
    await tracker.sample()
    nearby = tracker.status()["attempt"]["nearby"]  # type: ignore[index]
    assert nearby == [
        {
            "address": "FB:73:82:07:E3:AD",
            "name": None,
            "rssi": -60,
            "discriminator": 1059,
            "vendor_id": 4476,
            "product_id": 36871,
            "connected": False,
            "matches": False,
        }
    ]


async def test_bluetooth_findings_are_counted_during_the_attempt_and_the_hour() -> None:
    tracker, bluez, kernel, _ = _tracker()
    kernel.log = [KernelFinding("power", kernel.now - 30 * 60 * 1_000_000)]  # before the attempt
    tracker.start(Discriminator(1, "short"))
    kernel.log = kernel.log + [KernelFinding("transport", kernel.now + 5)]
    bluez.snapshots = [BluezSnapshot([], SCANNING)]
    await tracker.sample()
    bluetooth = tracker.status()["attempt"]["bluetooth"]  # type: ignore[index]
    assert bluetooth["available"] is True
    assert bluetooth["during_attempt"] == {"transport": 1, "stuck": 0, "power": 0}
    assert bluetooth["last_hour"] == {"transport": 1, "stuck": 0, "power": 1}
    assert bluetooth["stuck_now"] is False


async def test_an_adapter_that_stops_scanning_while_searching_is_stuck() -> None:
    """Measured 21 September 22:26: `Discovering: false` while matter-server
    waited for an advertisement. Only after `stuck_after` seconds, because an
    attempt's first sample can come before matter-server has started to scan."""
    tracker, bluez, _, clock = _tracker()
    tracker.start(Discriminator(4, "short"))
    bluez.snapshots = [BluezSnapshot([], AdapterState("hci0", True, False))]
    await tracker.sample()
    assert tracker.status()["attempt"]["bluetooth"]["stuck_now"] is False  # type: ignore[index]
    clock.now += timedelta(seconds=11)
    await tracker.sample()
    assert tracker.status()["attempt"]["bluetooth"]["stuck_now"] is True  # type: ignore[index]


async def test_without_sources_bluetooth_is_not_available() -> None:
    tracker = CommissioningTracker()
    tracker.start(Discriminator(4, "short"))
    await tracker.sample()
    assert tracker.status()["attempt"]["bluetooth"]["available"] is False  # type: ignore[index]


async def test_a_failure_keeps_the_last_attempt_with_its_reason() -> None:
    tracker, _, _, _ = _tracker()
    assert tracker.status()["attempt"] is None
    tracker.start(Discriminator(9, "short"))
    await tracker.finish("not_found")
    attempt = tracker.status()["attempt"]
    assert attempt["phase"] == "failed"  # type: ignore[index]
    assert attempt["reason"] == "not_found"  # type: ignore[index]
    assert attempt["discriminator"] == {"value": 9, "kind": "short"}  # type: ignore[index]
    assert tracker.status()["bridge_started_at"] == "2026-09-22T07:00:00Z"


@pytest.mark.parametrize("reason", ["no_thread_network", "matter_server_unreachable"])
async def test_finish_ends_the_attempt_as_failed_with_its_reason(reason: str) -> None:
    tracker, _, _, _ = _tracker()
    tracker.start(Discriminator(9, "short"))
    await tracker.finish(reason)  # type: ignore[arg-type]
    attempt = tracker.status()["attempt"]
    assert attempt["phase"] == "failed"  # type: ignore[index]
    assert attempt["reason"] == reason  # type: ignore[index]


async def test_sample_leaves_a_finished_attempt_alone() -> None:
    """A status route that samples must not overwrite the finished attempt's
    `nearby` list - it is what the "not found" message shows."""
    tracker, bluez, _, _ = _tracker()
    tracker.start(Discriminator(9, "short"))
    await tracker.finish("not_found")
    before = tracker.status()["attempt"]

    bluez.snapshots = [BluezSnapshot([_advert(1059, address="FB:73:82:07:E3:AD")], SCANNING)]
    await tracker.sample()

    after = tracker.status()["attempt"]
    assert after["nearby"] == before["nearby"]  # type: ignore[index]
    assert after["phase"] == "failed"  # type: ignore[index]
    assert after["reason"] == "not_found"  # type: ignore[index]
