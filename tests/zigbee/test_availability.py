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
    CHECK_INTERVAL_SECONDS,
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
    `RecordingHandler` has, trimmed to the one method this feature uses.

    `failing_device_ids` makes `set_online` raise for those devices and only
    those. The exception is the one the real sender raises: `UdpSender.send`
    answers `RuntimeError("the UDP sender is closed")` once its socket is
    gone, which is exactly the shape a shutdown racing a lost link
    produces.

    It is a mutable set rather than the frozen argument it arrives as, so a
    test can let the handler RECOVER partway through - a socket that is
    replaced, which is the only way to ask whether a failed report is ever
    retried (`test_a_device_the_handler_could_not_be_told_about_is_told_again`)."""

    def __init__(self, *, failing_device_ids: frozenset[int] = frozenset()) -> None:
        self.online: list[tuple[int, bool]] = []
        self.failing_device_ids: set[int] = set(failing_device_ids)

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None:
        raise AssertionError("AvailabilityChecker must never call on_attribute")

    async def on_event(self, device_id: int, path: str) -> None:
        raise AssertionError("AvailabilityChecker must never call on_event")

    async def set_online(self, device_id: int, online: bool) -> None:
        if device_id in self.failing_device_ids:
            raise RuntimeError("the UDP sender is closed")
        self.online.append((device_id, online))

    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None:
        raise AssertionError("AvailabilityChecker must never call on_node_snapshot")


class Metronome:
    """An injected `sleep` that hands control back to the test.

    `AvailabilityChecker` takes its own `sleep`, so the periodic loop can be
    driven one interval at a time instead of waiting out thirty real
    seconds - the reason this file's docstring gives for injecting a clock,
    applied to the lifecycle as well. Each `tick()` releases exactly one
    sleep and returns only once the task has reached the NEXT one, so a
    sweep is complete by the time the assertions run."""

    def __init__(self) -> None:
        self.slept: list[float] = []
        self._asleep = asyncio.Event()
        self._resume = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self._asleep.set()
        await self._resume.wait()
        self._resume.clear()

    async def wait_until_asleep(self) -> None:
        await asyncio.wait_for(self._asleep.wait(), timeout=1.0)
        self._asleep.clear()

    async def tick(self) -> None:
        self._resume.set()
        await self.wait_until_asleep()


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
    ieee: str,
    *,
    manufacturer: str = "ORVIBO",
    last_seen: float = 0.0,
    answers: str | None = None,
) -> tuple[FakeDevice, FakeCluster]:
    """A mains-powered device with a Basic cluster that never answers - the
    fixture `test_a_mains_device_is_pinged_twice_before_it_is_declared_offline`
    and `test_lumi_devices_are_never_pinged` both need: something to try to
    ping, whether or not the checker is supposed to actually try.

    `answers` makes the ping succeed, by giving the cluster a readable
    `Basic.manufacturer` - what a device that was alive all along but simply
    had nothing new to report sends back."""
    basic = FakeCluster(
        0x0000,
        declared=[0x0004],
        readable=None if answers is None else {0x0004: answers},
    )
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


class _MovingClock:
    """A clock a test advances by hand, for the sweeps that have to happen
    at more than one moment."""

    def __init__(self, moment: float) -> None:
        self.moment = moment

    def __call__(self) -> float:
        return self.moment


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


async def test_a_sweep_does_not_bring_a_device_back_while_the_link_is_down(build_source) -> None:
    """THE defect `mark_all_offline` alone does not close, and the only one
    of this module's promises that nothing else measures.

    Losing the coordinator does not touch `last_seen`, and `is_available`
    reads nothing but `last_seen` - so a sweep that consults the device
    alone answers "online" for every device that was heard from in the last
    two hours, thirty seconds after the link went down. The motion sensor
    this module's docstring is about would be back to reading "no motion"
    within half a minute of the radio dying, and `UdpSender.send`
    de-duplicates by VALUE, so `False` -> `True` is a genuine change that
    really goes out on the wire.

    The sweep therefore consults the link state as well, the same way
    `ZigbeeSource._facts` already writes `self._connected and
    is_available(device)`.

    Fault to prove it: drop the `if not self._source.connected` gate from
    `_check_one`."""
    lamp = colour_lamp()
    sensor = contact_sensor()
    source, app = build_source(lamp, sensor)
    await source.connect()
    handler = RecordingHandler()
    await source.subscribe(_resolver({lamp.ieee: 1, sensor.ieee: 2}), handler)
    checker = source._availability_checker
    assert checker is not None

    # Both devices were heard from moments ago, so `last_seen` alone says
    # "online" for both - and goes on saying it for hours after the radio is
    # gone, because nothing advances or retracts it.
    assert is_available(lamp) is True
    assert is_available(sensor) is True

    app.fire_connection_lost()
    await _settle()
    assert sorted(handler.online) == [(1, False), (2, False)]

    # Ten sweeps, five minutes of wall clock on the real interval. Not one
    # of them may report either device as reachable again.
    for _ in range(10):
        await checker._sweep()

    assert sorted(handler.online) == [(1, False), (2, False)], (
        "a sweep with no link must not undo mark_all_offline - and must not "
        "re-announce the same answer over and over either"
    )
    assert source.connected is False


async def test_one_device_that_cannot_be_told_does_not_silence_the_rest(build_source) -> None:
    """`mark_all_offline` runs under `_spawn`, so an exception escaping it
    reaches nothing but asyncio's "never retrieved" logger - and every
    device after the one that raised keeps its last value forever, which is
    the precise outcome this method exists to prevent.

    `set_online` really does raise: `UdpSender.send` answers
    `RuntimeError("the UDP sender is closed")` once its socket is gone.

    Fault to prove it: drop the per-device `try` from `mark_all_offline`.
    Only device 1 is then marked offline and devices 3 and 4 keep reporting
    yesterday's readings."""
    first = colour_lamp(ieee="00:12:4b:00:00:00:00:01")
    second = colour_lamp(ieee="00:12:4b:00:00:00:00:02")
    third = colour_lamp(ieee="00:12:4b:00:00:00:00:03")
    fourth = colour_lamp(ieee="00:12:4b:00:00:00:00:04")
    source, app = build_source(first, second, third, fourth)
    await source.connect()
    handler = RecordingHandler(failing_device_ids=frozenset({2}))
    await source.subscribe(
        _resolver({first.ieee: 1, second.ieee: 2, third.ieee: 3, fourth.ieee: 4}), handler
    )

    app.fire_connection_lost()
    await _settle()

    assert sorted(handler.online) == [(1, False), (3, False), (4, False)], (
        "a sender that is closed for one device must not strand every device behind it"
    )


async def test_a_sender_that_fails_every_device_logs_one_traceback(build_source, caplog) -> None:
    """`ZigbeeSource.disconnect()` now marks every device offline, and the
    bridge's shutdown closes the UDP sender before it disconnects the
    sources - so on every stop, every device's report fails the same way.
    A traceback apiece put one per device into the container log for a
    shutdown that went exactly as planned.

    The first failure keeps its traceback, so a real cause is still
    explained; the rest are one line each, and every device is still tried.

    Fault to prove it: log every failure with `logger.exception` again."""
    lamps = [colour_lamp(ieee=f"00:12:4b:00:00:00:00:2{n}") for n in range(3)]
    source, _app = build_source(*lamps)
    await source.connect()
    handler = RecordingHandler(failing_device_ids=frozenset({1, 2, 3}))
    await source.subscribe(_resolver({lamp.ieee: n + 1 for n, lamp in enumerate(lamps)}), handler)
    checker = source._availability_checker
    assert checker is not None
    caplog.set_level("INFO", logger="loxmatter.zigbee.availability")

    await checker.mark_all_offline()

    failures = [r for r in caplog.records if "could not mark Zigbee device" in r.getMessage()]
    assert len(failures) == 3
    assert [bool(r.exc_info) for r in failures] == [True, False, False]
    await source.disconnect()


async def test_a_device_the_handler_could_not_be_told_about_is_told_again(build_source) -> None:
    """`_report`'s whole guarantee, and until now nothing measured it:
    `self._reported[address] = online` sits AFTER `await set_online(...)`,
    so a handler that raised leaves the change outstanding and the next
    sweep says it again.

    Moving that one line above the call leaves every other test in this file
    green, which is what makes this the cheapest possible way to lose the
    protection. And the loss is not cosmetic: the moment `set_online` is
    most likely to raise is exactly this one - a `UdpSender` whose socket
    has gone during a link loss - so the device filed as already-told is
    stranded at its last value forever, which is the precise outcome
    `mark_all_offline` exists to prevent.

    Fault to prove it: record `self._reported[address] = online` BEFORE the
    `await self._handler.set_online(...)` call. Device 2 below is then never
    told anything, however many sweeps run afterwards."""
    first = colour_lamp(ieee="00:12:4b:00:00:00:00:11")
    second = colour_lamp(ieee="00:12:4b:00:00:00:00:12")
    source, app = build_source(first, second)
    await source.connect()
    handler = RecordingHandler(failing_device_ids=frozenset({2}))
    await source.subscribe(_resolver({first.ieee: 1, second.ieee: 2}), handler)

    app.fire_connection_lost()
    await _settle()
    assert handler.online == [(1, False)], "device 2's sender was closed, so it heard nothing"

    # The sender is replaced - a reconnected UDP socket - and the next
    # scheduled sweep runs. The link is still down, so the answer for both
    # devices is unchanged: device 1 is already told and must stay silent,
    # device 2 is still owed the report that failed.
    handler.failing_device_ids.clear()
    checker = source._availability_checker
    assert checker is not None
    await checker._sweep()

    assert handler.online == [(1, False), (2, False)], (
        "a report the handler could not take is still outstanding, not filed as done"
    )


async def test_a_link_that_dies_between_sweeps_returns_the_grace_counter(build_source) -> None:
    """The grace counter is cleared on FOUR paths, and this is the one
    `mark_all_offline` hides: `_check_one`'s own link-down branch. Every
    other test that loses a link goes through `_handle_connection_lost`,
    which calls `mark_all_offline()` and its `clear()` on the same event -
    so deleting this `pop` changes nothing any of them can see.

    It is reached without `mark_all_offline` whenever a sweep runs while the
    source is disconnected: the tail of a sweep whose link died partway
    through arrives here before the spawned `mark_all_offline` task has run
    at all. `_connected` is therefore set directly below, which is what
    isolates this branch from the `clear()` that would otherwise mask it.

    Without the `pop`, the plug's half-spent grace survives an outage it had
    no part in, and it is written off after ONE ping on the way back rather
    than two.

    Fault to prove it: delete `self._missed_checkins.pop(address, None)`
    from `_check_one`'s `if not self._source.connected` branch."""
    device, basic = _mains_device("00:12:4b:00:00:00:01:10")
    source, _app = build_source(device)
    await source.connect()
    handler = RecordingHandler()
    checker = AvailabilityChecker(
        source,
        handler,
        _resolver({device.ieee: 1}),
        now=_fixed_clock(MAINS_THRESHOLD_SECONDS + 1),
    )

    await checker._sweep()  # quiet: one of two grace pings spent
    assert len(basic.reads) == 1

    # The link dies, and the sweep - not `mark_all_offline` - is what
    # notices. This is the branch under test.
    source._connected = False
    await checker._sweep()
    assert handler.online == [(1, False)], "a sweep with no link reports offline on its own"
    assert len(basic.reads) == 1, "and pings nothing over a radio that is gone"

    # The radio is back and the device is still quiet. It must get BOTH
    # pings again, not the one its pre-outage count would have left it.
    source._connected = True
    await checker._sweep()
    await checker._sweep()
    assert len(basic.reads) == 3, "the outage returned the grace counter to zero"


# ------------------------------------------------------------ coordinator --


async def test_the_coordinator_is_never_pinged_or_written_off(build_source) -> None:
    """ZHA's `_check_available` opens with `if self.is_active_coordinator:
    return`, and its `DeviceAvailabilityChecker` filters
    `if not dev.is_coordinator`. This module claims to be a port of that
    method, and `ZigbeeSource._devices()` hands over
    `list(app.devices.values())` - which zigpy keeps the coordinator in.

    Without the exemption the checker addresses an over-the-air read to the
    radio it is talking THROUGH, and then declares the radio offline for not
    answering it.

    Fault to prove it: drop the filter from `_devices_to_check`."""
    coordinator, coordinator_basic = _mains_device("00:12:4b:00:00:00:00:aa")
    coordinator.node_desc = FakeNodeDescriptor(is_mains_powered=True, is_coordinator=True)
    lamp, lamp_basic = _mains_device("00:12:4b:00:00:00:00:bb")
    source, app = build_source(coordinator, lamp)
    await source.connect()
    handler = RecordingHandler()
    checker = AvailabilityChecker(
        source,
        handler,
        _resolver({coordinator.ieee: 1, lamp.ieee: 2}),
        now=_fixed_clock(MAINS_THRESHOLD_SECONDS + 1),
    )

    for _ in range(4):
        await checker._sweep()

    assert coordinator_basic.reads == [], "the coordinator must never be asked over the air"
    assert lamp_basic.reads != [], "an ordinary mains device still is"
    assert handler.online == [(2, False)], (
        "only the lamp is written off; the radio's own state is the link, not a device row"
    )

    # And the same exemption on the link-loss path, so a coordinator that
    # DOES have a store row cannot be taken offline by a sweep that will
    # never bring it back.
    app.fire_connection_lost()
    await _settle()
    assert (1, False) not in handler.online


# ---------------------------------------------------- the ping's predicate --


async def test_a_device_that_sleeps_is_not_pinged_even_when_it_is_mains_powered(
    build_source,
) -> None:
    """`is_mains_powered` is not the predicate that decides whether a device
    can answer an unsolicited read - `is_receiver_on_when_idle` is. They are
    two separate bits of the same MAC capability byte in zigpy 2.2.0, and
    the second one is what says whether anybody is listening between the
    device's own transmissions.

    Fault to prove it: ping on `is_mains_powered` again. The sleepy device
    below is then pinged three times and stays "online" for two extra
    sweeps, on the strength of a read nothing could have answered."""
    sleepy, basic = _mains_device("00:12:4b:00:00:00:00:cc")
    sleepy.node_desc = FakeNodeDescriptor(is_mains_powered=True, is_receiver_on_when_idle=False)
    source, _app = build_source(sleepy)
    await source.connect()
    handler = RecordingHandler()
    checker = AvailabilityChecker(
        source,
        handler,
        _resolver({sleepy.ieee: 1}),
        now=_fixed_clock(MAINS_THRESHOLD_SECONDS + 1),
    )

    await checker._sweep()

    assert basic.reads == [], "nothing is listening - there is nobody to ask"
    assert handler.online == [(1, False)], "offline at once, with no grace period spent pinging"


# -------------------------------------------------------------- lifecycle --


async def test_start_sweeps_on_every_interval_and_stop_ends_it(build_source) -> None:
    """Nothing anywhere called `start()` or `stop()`, so the whole periodic
    half of this module was unmeasured - and it is exactly the half the task
    that wires a source into the running bridge has to trust blind.

    Faults to prove it, one at a time:
      - make `start()` a no-op: the loop never reaches its first sleep;
      - make `_run()` sleep without sweeping: the first tick reports nothing;
      - make `stop()` drop the `cancel()`: the task is still pending after.
    """
    # LUMI, so the verdict lands on the FIRST sweep rather than after two
    # grace pings - this test is about the loop's cadence, not about grace.
    device, _basic = _mains_device("00:12:4b:00:00:00:02:00", manufacturer="LUMI")
    source, _app = build_source(device)
    await source.connect()
    handler = RecordingHandler()
    metronome = Metronome()
    checker = AvailabilityChecker(
        source,
        handler,
        _resolver({device.ieee: 1}),
        sleep=metronome.sleep,
        now=_fixed_clock(MAINS_THRESHOLD_SECONDS + 1),
    )

    checker.start()
    task = checker._task
    assert task is not None, "start() must actually create the sweeping task"
    await metronome.wait_until_asleep()
    assert metronome.slept == [CHECK_INTERVAL_SECONDS], (
        "it waits one interval BEFORE the first sweep"
    )
    assert handler.online == [], "and has swept nothing yet"

    await metronome.tick()
    assert handler.online == [(1, False)], "one interval, one sweep"

    # A second interval, to pin that this is a loop and not a single shot.
    device.last_seen = MAINS_THRESHOLD_SECONDS  # heard from again
    await metronome.tick()
    assert handler.online == [(1, False), (1, True)]
    assert metronome.slept == [CHECK_INTERVAL_SECONDS] * 3

    await checker.stop()
    assert task.done(), "stop() must cancel the task, not merely forget it"
    assert checker._task is None


async def test_a_ping_that_is_answered_resets_the_grace_counter(build_source) -> None:
    """A device that answers is alive, whatever `last_seen` says: on the
    real radio the response updates `last_seen` itself, but nothing here
    depends on that timing lining up with the next sweep.

    Fault to prove it: stop clearing the counter on a successful read. The
    plug below is then declared offline on the third sweep despite having
    answered every single ping."""
    device, basic = _mains_device("00:12:4b:00:00:00:00:dd", answers="ORVIBO")
    source, _app = build_source(device)
    await source.connect()
    handler = RecordingHandler()
    checker = AvailabilityChecker(
        source,
        handler,
        _resolver({device.ieee: 1}),
        now=_fixed_clock(MAINS_THRESHOLD_SECONDS + 1),
    )

    for _ in range(5):
        await checker._sweep()

    assert len(basic.reads) == 5, "every sweep asks again, because every answer resets the count"
    assert handler.online == [], "a device that answers is never written off"


async def test_a_device_that_is_heard_from_again_gets_its_full_grace_back(build_source) -> None:
    """The counter is per run of silence, not a lifetime total. A plug that
    went quiet once, answered, and went quiet again months later must get
    both of its pings again.

    Fault to prove it: stop clearing the count when the device is available.
    The plug is then written off after ONE ping the second time round."""
    device, basic = _mains_device("00:12:4b:00:00:00:00:ee")
    source, _app = build_source(device)
    await source.connect()
    handler = RecordingHandler()
    clock = _MovingClock(MAINS_THRESHOLD_SECONDS + 1)
    checker = AvailabilityChecker(source, handler, _resolver({device.ieee: 1}), now=clock)

    await checker._sweep()  # quiet: first grace ping
    assert len(basic.reads) == 1

    # The device reports of its own accord, which is what advances `last_seen`.
    device.last_seen = clock.moment
    clock.moment += 1
    await checker._sweep()
    assert handler.online == [(1, True)]

    # And months later it goes quiet again.
    clock.moment = device.last_seen + MAINS_THRESHOLD_SECONDS + 1
    await checker._sweep()
    assert len(basic.reads) == 2
    await checker._sweep()
    assert len(basic.reads) == 3, "the second run of silence gets BOTH pings, not one"
    assert handler.online == [(1, True)], "and it is not offline yet"

    await checker._sweep()
    assert handler.online == [(1, True), (1, False)]


async def test_a_lost_link_clears_the_grace_counters(build_source) -> None:
    """A count of missed check-ins means nothing once the reason nobody
    answered is the link itself. Carrying it across an outage would spend a
    reconnected device's grace on silence the device had no part in.

    Fault to prove it: stop clearing `_missed_checkins` in
    `mark_all_offline`. The plug below then gets one ping after the outage
    instead of two."""
    device, basic = _mains_device("00:12:4b:00:00:00:00:ff")
    source, _app = build_source(device)
    await source.connect()
    handler = RecordingHandler()
    checker = AvailabilityChecker(
        source,
        handler,
        _resolver({device.ieee: 1}),
        now=_fixed_clock(MAINS_THRESHOLD_SECONDS + 1),
    )

    await checker._sweep()  # one grace ping spent
    assert len(basic.reads) == 1

    await checker.mark_all_offline()
    assert handler.online == [(1, False)]

    # The link is back (`source.connected` was never cleared here - this
    # isolates the counter from the link gate above), and the device is
    # still quiet. It must get two fresh pings, not one.
    await checker._sweep()
    await checker._sweep()
    assert len(basic.reads) == 3, "an outage returns the grace counter to zero"


async def test_a_device_with_no_basic_cluster_still_gets_its_grace_periods(build_source) -> None:
    """A ping that cannot even be attempted is a failed ping, not a verdict.
    `_ping` returns quietly when there is no Basic cluster, and the device
    runs out of grace on a later sweep the same way a silent one does -
    deliberately gentler than ZHA, which writes such a device off at once.

    Fault to prove it: have `_ping` set the missed count to the grace limit
    when the cluster is missing. The device is then offline one sweep early,
    on evidence about the bridge's own reading of it rather than about the
    device."""
    device = FakeDevice(
        "00:12:4b:00:00:00:01:00",
        manufacturer="ORVIBO",
        node_desc=FakeNodeDescriptor(is_mains_powered=True),
        last_seen=0.0,
        # On/Off only: no Basic cluster anywhere on the device.
        endpoints=[
            FakeEndpoint(
                1, profile_id=0x0104, device_type=0x0051, in_clusters=[FakeCluster(0x0006)]
            )
        ],
    )
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
    assert handler.online == [], "first grace period, spent on a ping that could not be sent"
    await checker._sweep()
    assert handler.online == [], "second"
    await checker._sweep()
    assert handler.online == [(1, False)], "and only now is it written off"


# ------------------------------------------------------------- ownership --


async def test_subscribing_again_stops_the_previous_checker(build_source) -> None:
    """`attach()` runs on every reconnect, so `subscribe()` runs on every
    reconnect. Assigning a new checker over the old one leaves the old
    `_run()` task pending on an object nobody can reach, holding the device
    ids of a connection that is gone - one leaked sweeper per outage.

    Fault to prove it: assign without `await old.stop()`. The first task is
    then still pending after the second `subscribe()`."""
    lamp = colour_lamp()
    source, _app = build_source(lamp)
    await source.connect()
    handler = RecordingHandler()
    resolve = _resolver({lamp.ieee: 1})

    await source.subscribe(resolve, handler)
    first = source._availability_checker
    assert first is not None
    first.start()
    first_task = first._task
    assert first_task is not None

    await source.subscribe(resolve, handler)
    second = source._availability_checker

    assert second is not first, "a fresh checker per subscribe, as the constructor says"
    assert first_task.done(), "and the previous one is stopped, not orphaned"


async def test_disconnecting_stops_the_checker(build_source) -> None:
    """`disconnect()` tears down the dispatch task and the cluster listeners
    and used to leave the checker sweeping a source with no application at
    all.

    Fault to prove it: leave `_stop_availability_checker()` out of
    `disconnect()`."""
    lamp = colour_lamp()
    source, _app = build_source(lamp)
    await source.connect()
    handler = RecordingHandler()
    await source.subscribe(_resolver({lamp.ieee: 1}), handler)
    checker = source._availability_checker
    assert checker is not None
    checker.start()
    task = checker._task
    assert task is not None

    await source.disconnect()

    assert task.done(), "the sweeper goes down with the connection"
    assert source._availability_checker is None


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
