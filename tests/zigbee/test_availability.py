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

"""`AvailabilityChecker` against a fake radio (design task 8).

Every threshold and grace-period test below drives `is_available` or
`AvailabilityChecker` with an injected `now`/clock rather than a real
`asyncio.sleep`, for the reason `sources/supervisor.py` already documents:
a test that sleeps for real gets skipped as slow at the next rework and then
checks nothing at all.

**Nothing here imports zigpy** - the same split `test_source.py` explains:
`tests/zigbee/fakes.py` mimics exactly the zigpy surface this feature reads
(`Device.last_seen`, `Device.node_desc`, `Device.manufacturer`,
`Cluster.read_attributes`), and `tests/zigbee/test_zigpy_names.py` is what
keeps the fake honest against the installed library.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from fakes import (
    FakeApplication,
    FakeApplicationFactory,
    FakeCluster,
    FakeDevice,
    FakeEndpoint,
    FakeNodeDescriptor,
    colour_lamp,
    contact_sensor,
)

from loxmatter.matter.models import NodeSnapshot
from loxmatter.radios.fingerprints import Fingerprint
from loxmatter.zigbee import source as source_module
from loxmatter.zigbee.availability import (
    BATTERY_THRESHOLD_SECONDS,
    MAINS_THRESHOLD_SECONDS,
    AvailabilityChecker,
    is_available,
)
from loxmatter.zigbee.source import ZigbeeSource

FINGERPRINT = Fingerprint(
    name="SONOFF ZBDongle-E V2",
    radio_type="ezsp",
    baudrate=115200,
    flow_control="software",
)


class RecordingHandler:
    """A `RuntimeEventHandler` that remembers only what `AvailabilityChecker`
    is allowed to call - the same shape `test_source.py`'s own
    `RecordingHandler` has, trimmed to the one method this feature uses."""

    def __init__(self) -> None:
        self.online: list[tuple[int, bool]] = []

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None:
        raise AssertionError("AvailabilityChecker must never call on_attribute")

    async def on_event(self, device_id: int, path: str) -> None:
        raise AssertionError("AvailabilityChecker must never call on_event")

    async def set_online(self, device_id: int, online: bool) -> None:
        self.online.append((device_id, online))

    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None:
        raise AssertionError("AvailabilityChecker must never call on_node_snapshot")


def _resolver(mapping: dict[str, int]) -> Any:
    def resolve(address: str) -> int | None:
        return mapping.get(address)

    return resolve


def _fixed_clock(moment: float) -> Any:
    def now() -> float:
        return moment

    return now


async def _settle() -> None:
    """Lets a task spawned via `ZigbeeSource._spawn` (a synchronous
    listener callback) actually run - the same two extra rounds
    `test_source.py`'s own `_settle` uses for exactly the same reason."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.fixture
def build_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Builds a `ZigbeeSource` around one prepared fake application.

    The quirks warm-up is replaced exactly as `test_source.py`'s own `build`
    fixture replaces it: the real one imports 462 quirk modules and costs
    2-3 s on an M1 (research F.4), and nothing here tests that ordering."""

    def _build(*devices: FakeDevice) -> tuple[ZigbeeSource, FakeApplication]:
        async def fake_ensure_quirks_loaded() -> None:
            return None

        monkeypatch.setattr(source_module, "ensure_quirks_loaded", fake_ensure_quirks_loaded)
        app = FakeApplication(devices=list(devices))
        source = ZigbeeSource(
            path="/dev/serial/by-id/usb-fake-availability",
            fingerprint=FINGERPRINT,
            database=tmp_path / "zigbee.sqlite",
            application_factory=FakeApplicationFactory(applications=[app]),
        )
        return source, app

    return _build


def _mains_device(
    ieee: str, *, manufacturer: str = "ORVIBO", last_seen: float = 0.0
) -> tuple[FakeDevice, FakeCluster]:
    """A mains-powered device with a Basic cluster that never answers - the
    fixture `test_a_mains_device_is_pinged_twice_before_it_is_declared_offline`
    and `test_lumi_devices_are_never_pinged` both need: something to try to
    ping, whether or not the checker is supposed to actually try."""
    basic = FakeCluster(0x0000, declared=[0x0004])
    device = FakeDevice(
        ieee,
        manufacturer=manufacturer,
        node_desc=FakeNodeDescriptor(is_mains_powered=True),
        last_seen=last_seen,
        endpoints=[
            FakeEndpoint(1, profile_id=0x0104, device_type=0x0051, in_clusters=[basic]),
        ],
    )
    return device, basic


# ------------------------------------------------------------- thresholds --


async def test_a_mains_device_is_offline_after_two_hours_and_a_battery_one_after_six() -> None:
    """From the node descriptor's `is_mains_powered`. One threshold for both
    would either declare every battery sensor dead four times a day, or take
    six hours to notice a dead lamp.

    Fault to prove it: use one threshold for both."""
    mains = FakeDevice(
        "00:00:00:00:00:00:00:01",
        node_desc=FakeNodeDescriptor(is_mains_powered=True),
        last_seen=0.0,
    )
    battery = FakeDevice(
        "00:00:00:00:00:00:00:02",
        node_desc=FakeNodeDescriptor(is_mains_powered=False),
        last_seen=0.0,
    )

    # Just under each device's own threshold: both still count as reachable.
    assert is_available(mains, now=MAINS_THRESHOLD_SECONDS - 1) is True
    assert is_available(battery, now=BATTERY_THRESHOLD_SECONDS - 1) is True

    # A battery device is still well within its own six-hour allowance at
    # the exact moment a mains device's two-hour one runs out - the whole
    # point of picking a threshold per device rather than one for both.
    assert is_available(battery, now=MAINS_THRESHOLD_SECONDS) is True

    # Each device's OWN threshold, reached: no longer reachable.
    assert is_available(mains, now=MAINS_THRESHOLD_SECONDS) is False
    assert is_available(battery, now=BATTERY_THRESHOLD_SECONDS) is False


# ----------------------------------------------------------------- pinging --


async def test_a_mains_device_is_pinged_twice_before_it_is_declared_offline(build_source) -> None:
    """ZHA reads `Basic.manufacturer` with `allow_cache=False`, twice, with
    two grace periods. A mains device that simply had nothing to report is
    not a dead one.

    Fault to prove it: declare it offline on the first expiry. A quiet plug
    then drops offline in Loxone every two hours."""
    device, basic = _mains_device("00:00:00:00:00:00:00:03")
    source, _app = build_source(device)
    await source.connect()
    handler = RecordingHandler()
    checker = AvailabilityChecker(
        source,
        handler,
        _resolver({device.ieee: 1}),
        now=_fixed_clock(MAINS_THRESHOLD_SECONDS + 1),
    )

    await checker._sweep()
    assert handler.online == [], "a mains device gets its first grace ping before being written off"
    assert len(basic.reads) == 1

    await checker._sweep()
    assert handler.online == [], "and its second - two pings, not one, before it is declared dead"
    assert len(basic.reads) == 2

    await checker._sweep()
    assert handler.online == [(1, False)], "grace exhausted after exactly two pings: now offline"
    assert len(basic.reads) == 2, "no third ping is wasted on a device already written off"


async def test_lumi_devices_are_never_pinged(build_source) -> None:
    """They do not answer, so a ping proves nothing about them and only
    wastes a wake-up.

    Fault to prove it: ping them too - the LUMI sensor then reports offline
    despite being alive."""
    device, basic = _mains_device("00:00:00:00:00:00:00:04", manufacturer="LUMI")
    source, _app = build_source(device)
    await source.connect()
    handler = RecordingHandler()
    checker = AvailabilityChecker(
        source,
        handler,
        _resolver({device.ieee: 1}),
        now=_fixed_clock(MAINS_THRESHOLD_SECONDS + 1),
    )

    await checker._sweep()

    assert basic.reads == [], "a LUMI device must never be asked - it will not answer"
    assert handler.online == [(1, False)], (
        "declared offline at once, with no grace period spent pinging"
    )


# ------------------------------------------------------------------- link --


async def test_every_device_goes_offline_when_the_link_is_lost(build_source) -> None:
    """THE one this feature exists for. zigpy has no availability concept at
    all, so without this sweep a pulled coordinator leaves every sensor
    showing its last value in Loxone, forever, with nothing marking it
    stale - a motion sensor that reads "no motion" because the radio is gone
    is worse than one that reads nothing.

    Fault to prove it: drop the link-loss sweep."""
    lamp = colour_lamp()
    sensor = contact_sensor()
    source, app = build_source(lamp, sensor)
    await source.connect()
    handler = RecordingHandler()
    await source.subscribe(_resolver({lamp.ieee: 1, sensor.ieee: 2}), handler)

    assert handler.online == [], "nothing offline yet - the link is still up"

    app.fire_connection_lost()
    await _settle()

    assert (1, False) in handler.online
    assert (2, False) in handler.online


# --------------------------------------------------------------- restarts --


async def test_the_online_state_is_seeded_from_the_database_after_a_restart(build_source) -> None:
    """zigpy persists `last_seen`, so the state after a restart is known
    rather than unknown - and it reaches Loxone through
    `NodeSnapshot.available` at `attach()` time, not through a separate
    path.

    Fault to prove it: seed every device as online."""
    recently = colour_lamp()
    stale = colour_lamp(ieee="00:12:4b:00:1c:a1:b2:c4")
    stale.last_seen = time.time() - MAINS_THRESHOLD_SECONDS - 10
    never_seen = colour_lamp(ieee="00:12:4b:00:1c:a1:b2:c5")
    never_seen.last_seen = None
    source, _app = build_source(recently, stale, never_seen)

    await source.connect()
    snapshots = {snapshot.address: snapshot for snapshot in await source.snapshots()}

    assert snapshots[recently.ieee].available is True
    assert snapshots[stale.ieee].available is False
    assert snapshots[never_seen.ieee].available is False
