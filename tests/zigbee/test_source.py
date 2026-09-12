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

"""`ZigbeeSource` against a fake radio (design 2026-09-12, sections 4 and
10.1).

**Nothing in this module imports zigpy.** Every zigpy behaviour these tests
depend on is mimicked by `tests/zigbee/fakes.py`, and every zigpy NAME the
source depends on is checked against the installed library by
`tests/zigbee/test_zigpy_names.py`. The split is deliberate: zigpy's own
startup takes 7.5 s to time out against a silent port, so a suite that drove
the real thing would be abandoned within a week - but a suite that drove
only a fake would happily stay green against a library that had renamed
everything underneath it.

There is no Zigbee stick on the test Pi (design 10.3). These tests are the
only evidence this source works that exists before one is bought, and they
are written as if they were the last line of defence, because they are.
"""

from __future__ import annotations

import asyncio
import errno
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fakes import (
    ControllerException,
    DeliveryError,
    FakeApplication,
    FakeApplicationFactory,
    NetworkSettingsInconsistent,
    ZigbeeException,
    colour_lamp,
    contact_sensor,
)

from loxmatter import i18n
from loxmatter.matter.models import NodeSnapshot
from loxmatter.radios.fingerprints import Fingerprint
from loxmatter.sources import DeviceCall, DeviceUnreachableError
from loxmatter.zigbee import source as source_module
from loxmatter.zigbee.source import ZIGBEE_CHANNELS, ZigbeeSource, ZigbeeUnavailableError

LAMP_IEEE = "00:12:4b:00:1c:a1:b2:c3"
SENSOR_IEEE = "00:15:8d:00:02:aa:bb:cc"

FINGERPRINT = Fingerprint(
    name="SONOFF ZBDongle-E V2",
    radio_type="ezsp",
    baudrate=115200,
    flow_control="software",
)


class RecordingHandler:
    """A `RuntimeEventHandler` that remembers what it was told.

    `fail_next` makes the next call raise, which is how the dispatch loop's
    `except Exception` is measured rather than asserted."""

    def __init__(self) -> None:
        self.attributes: list[tuple[int, str, Any]] = []
        self.events: list[tuple[int, str]] = []
        self.online: list[tuple[int, bool]] = []
        self.snapshots: list[tuple[int, NodeSnapshot]] = []
        self.fail_next = 0

    def _maybe_fail(self) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("the store is locked")

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None:
        self._maybe_fail()
        self.attributes.append((device_id, path, raw))

    async def on_event(self, device_id: int, path: str) -> None:
        self._maybe_fail()
        self.events.append((device_id, path))

    async def set_online(self, device_id: int, online: bool) -> None:
        self._maybe_fail()
        self.online.append((device_id, online))

    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None:
        self._maybe_fail()
        self.snapshots.append((device_id, snapshot))


class Harness:
    """The source, the applications it will be handed, and the order in
    which the steps of `connect()` actually ran."""

    def __init__(
        self,
        source: ZigbeeSource,
        factory: FakeApplicationFactory,
        applications: list[FakeApplication],
        order: list[str],
    ) -> None:
        self.source = source
        self.factory = factory
        self.applications = applications
        self.order = order
        self.handler = RecordingHandler()

    @property
    def app(self) -> FakeApplication:
        return self.applications[0]


@pytest.fixture
def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Builds a `ZigbeeSource` around prepared fake applications.

    The quirks warm-up is replaced here rather than left alone: the real one
    imports 462 quirk modules and costs 2-3 s on an M1 (research F.4). The
    replacement records that it ran, and WHEN, which is what
    `test_the_quirks_registry_is_warmed_up_before_any_application_is_built`
    reads."""

    def _build(
        *applications: FakeApplication,
        on_connection_change: Any = None,
        thread_channel: int | None = None,
    ) -> Harness:
        order: list[str] = []
        prepared = list(applications) or [FakeApplication()]

        async def fake_ensure_quirks_loaded() -> float:
            order.append(f"quirks:{source.progress().state}")
            return 0.0

        monkeypatch.setattr(source_module, "ensure_quirks_loaded", fake_ensure_quirks_loaded)

        factory = FakeApplicationFactory(applications=list(prepared))
        inner = factory.__call__

        async def recording_factory(config: dict[str, Any]) -> FakeApplication:
            order.append(f"application:{source.progress().state}")
            return await inner(config)

        source = ZigbeeSource(
            path="/dev/serial/by-id/usb-SONOFF_Zigbee_3.0_USB_Dongle_Plus_V2-if00",
            fingerprint=FINGERPRINT,
            database=tmp_path / "zigbee.sqlite",
            application_factory=recording_factory,
            on_connection_change=on_connection_change,
            thread_channel=thread_channel,
        )
        return Harness(source, factory, prepared, order)

    return _build


async def _settle(source: ZigbeeSource) -> None:
    """Waits until the dispatch task has worked through everything queued.

    `Queue.join()` rather than a handful of `asyncio.sleep(0)` rounds: the
    dispatch loop calls `task_done()` for every item it takes, so this is a
    real barrier that cannot go hollow when the number of awaits inside the
    loop changes."""
    queue = source._queue
    if queue is not None:
        await asyncio.wait_for(queue.join(), 2)
    # One more round for the tasks a listener schedules (the connection
    # hook), which do not run through the queue.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def _lamp_resolver(device_id: int = 7):
    def resolve(address: str) -> int | None:
        return device_id if address == LAMP_IEEE else None

    return resolve


# --------------------------------------------------------------- lifecycle --


async def test_connect_builds_a_fresh_application_every_time(build) -> None:
    """zigpy's teardown cancels tasks and calls `on_remove()` on every
    device, and an object whose `startup()` failed is in an unknown state.
    Home Assistant reloads the whole config entry on a lost connection for
    exactly this reason.

    Fault to prove it: reuse `self._app` when it already exists. The second
    connect then runs against an application that was already shut down."""
    first = FakeApplication(devices=[colour_lamp()])
    second = FakeApplication(devices=[colour_lamp()])
    harness = build(first, second)

    await harness.source.connect()
    await harness.source.connect()

    assert harness.factory.calls == 2
    # The old one is shut down BEFORE anything else, and with `db=True`, so
    # its half of the zigpy database is flushed rather than abandoned.
    assert first.shutdown_calls == [True]
    assert second.startup_calls == [True]
    assert second.started is True


async def test_a_failed_startup_is_shut_down_and_not_kept(build) -> None:
    """Fault to prove it: leave `self._app` set after a failed startup. The
    next `connect()` then reuses a half-initialised object, and `snapshots()`
    in between reads from one whose radio thread is gone."""
    # The broken one carries a device on purpose: if the source kept it,
    # `snapshots()` below would answer with that device's snapshot, read out
    # of an object that has already been shut down.
    broken = FakeApplication(devices=[colour_lamp()], startup_error=TimeoutError())
    healthy = FakeApplication(devices=[colour_lamp()])
    harness = build(broken, healthy)

    with pytest.raises(ZigbeeUnavailableError):
        await harness.source.connect()

    assert broken.shutdown_calls == [True]
    assert harness.source.connected is False
    assert await harness.source.snapshots() == []

    await harness.source.connect()

    assert harness.factory.calls == 2
    assert healthy.started is True
    assert [snapshot.address for snapshot in await harness.source.snapshots()] == [LAMP_IEEE]


@pytest.mark.parametrize(
    ("raised", "key"),
    [
        # The busy case comes FIRST although it is the third row of the
        # table: `OSError` is the base of the two rows above it, so a
        # matcher that tested `isinstance(exc, OSError)` before the two
        # specific ones would answer "busy" for all three, and an ordering
        # mistake would otherwise hide behind the table's own order.
        (
            OSError(errno.EBUSY, "/dev/ttyUSB0 is already locked by another process"),
            "api.errors.zigbee_stick_busy",
        ),
        (
            FileNotFoundError(errno.ENOENT, "no such file or directory"),
            "api.errors.zigbee_stick_missing",
        ),
        (
            PermissionError(errno.EACCES, "permission denied"),
            "api.errors.zigbee_no_device_permission",
        ),
        (TimeoutError(), "api.errors.zigbee_not_a_coordinator"),
    ],
)
async def test_each_startup_failure_gets_its_own_words(build, raised, key) -> None:
    """Measured in research E.2: a missing device raises `FileNotFoundError`
    IMMEDIATELY - not zigpy's `TransientConnectionError`, which only covers
    ENETUNREACH - and a silent port raises `TimeoutError` after 7.5 s. Each
    one sends the user somewhere different: to the radios card, to the
    compose stack, to their second loxmatter instance, or to the firmware on
    the stick. One generic "connection failed" sends them nowhere.

    Fault to prove it: map them all to one message."""
    harness = build(FakeApplication(startup_error=raised))

    with pytest.raises(ZigbeeUnavailableError) as caught:
        await harness.source.connect()

    assert str(caught.value) == i18n.t(key)
    # The card reads the same words out of `progress()`, already translated.
    assert harness.source.progress().state == "failed"
    assert harness.source.progress().error == i18n.t(key)


def test_the_four_startup_messages_are_four_different_sentences() -> None:
    """The point of the table above is that each cause names a different
    next step. Four keys that happen to carry the same text would pass every
    test above and still send the user nowhere.

    Fault to prove it: give two of the keys the same `en` text."""
    keys = [
        "api.errors.zigbee_stick_missing",
        "api.errors.zigbee_no_device_permission",
        "api.errors.zigbee_stick_busy",
        "api.errors.zigbee_not_a_coordinator",
        "api.errors.zigbee_network_mismatch",
    ]
    for language in ("en", "de"):
        i18n.set_language(language)
        try:
            texts = [i18n.t(key) for key in keys]
        finally:
            i18n.set_language("en")
        assert len(set(texts)) == len(keys), f"{language}: {texts}"


async def test_connected_is_an_explicit_flag_cleared_on_loss(build) -> None:
    """`bellows.is_controller_running` is NEVER cleared on a lost link
    (R1 section 2), so reading it would report a dead radio as healthy
    forever - which is precisely the 8 September outage the supervisor
    exists to prevent, one layer down.

    Fault to prove it: report the fake's own `is_running` instead."""
    harness = build(FakeApplication(devices=[colour_lamp()]))
    await harness.source.connect()

    assert harness.source.connected is True

    harness.app.fire_connection_lost(OSError("ASH stopped acknowledging"))
    await _settle(harness.source)

    assert harness.source.connected is False
    # What reading bellows' own flag would have answered instead - still
    # True, long after the radio stopped answering.
    assert harness.app.is_running is True


async def test_wait_for_link_loss_returns_at_once_when_already_disconnected(build) -> None:
    """The contract `BridgeMatterClient.wait_for_link_loss` has, and what
    puts the supervisor into its 1 s -> 60 s backoff loop for a source that
    never came up at all.

    Fault to prove it: await the event unconditionally. `supervise()` then
    blocks forever on a source that was never connected, and the radio is
    never retried."""
    harness = build(FakeApplication(devices=[colour_lamp()]))

    # Never connected: `supervise()` must fall straight through into its own
    # connect-and-back-off loop.
    await asyncio.wait_for(harness.source.wait_for_link_loss(), 1)

    await harness.source.connect()
    waiting = asyncio.create_task(harness.source.wait_for_link_loss())
    await asyncio.sleep(0)
    assert not waiting.done(), "a live link must keep the supervisor waiting"

    harness.app.fire_connection_lost()
    await asyncio.wait_for(waiting, 1)


async def test_snapshots_and_subscribe_tolerate_being_disconnected(build) -> None:
    """`sources.supervisor.attach()` calls both unconditionally, and
    `cli._run` runs `attach` for every source at startup. A source that
    raised here would take the whole bridge down because a USB stick was
    unplugged - and Matter is the mandatory source, not this one.

    `snapshots()` still returns the device catalogue: `new(start_radio=False)`
    loads zigpy's database without touching the radio (research E.2), so the
    devices are known even when the stick is gone - with `available=False`,
    which is the truth.

    Fault to prove it: raise `ZigbeeUnavailableError` from `snapshots()`
    when not connected."""
    harness = build(FakeApplication(devices=[colour_lamp()]))

    # Before any connect at all - `attach()` does not ask first.
    await harness.source.subscribe(_lamp_resolver(), harness.handler)
    assert await harness.source.snapshots() == []

    await harness.source.connect()
    harness.app.fire_connection_lost()
    await _settle(harness.source)

    snapshots = await harness.source.snapshots()
    assert [snapshot.address for snapshot in snapshots] == [LAMP_IEEE]
    assert snapshots[0].available is False
    await harness.source.subscribe(_lamp_resolver(), harness.handler)


async def test_the_quirks_registry_is_warmed_up_before_any_application_is_built(build) -> None:
    """The one ordering guarantee of this file: quirks are in the registry
    before zigpy builds a single device object, or a Tuya sensor is built
    from the wrong class and stays wrong until the next restart.

    It lives INSIDE `connect()` so a second caller cannot forget it - which
    is safe only because every caller is a background worker. The warm-up
    costs 2-3 s on an M1 and an estimated 9-15 s on a Pi 4 (research F.4);
    on a request path that is a web UI that stops answering.

    Fault to prove it: move `ensure_quirks_loaded()` after
    `_new_application()`. The order below then reads the other way round."""
    harness = build(FakeApplication(devices=[colour_lamp()]))

    await harness.source.connect()

    assert harness.order == ["quirks:loading_quirks", "application:opening_radio"]


def test_the_quirks_warm_up_has_exactly_one_caller_in_the_source_tree() -> None:
    """What keeps the warm-up off a request path: nothing but the source
    calls it.

    `connect()` is reached from `cli._run` at startup and from
    `sources/supervisor.py`'s loop, both background workers. The moment a
    second caller appears - an HTTP handler that "just makes sure quirks are
    loaded" - this fails and says so.

    Fault to prove it: call `ensure_quirks_loaded` from a second module."""
    root = Path(__file__).resolve().parents[2] / "src" / "loxmatter"
    callers = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "ensure_quirks_loaded" in path.read_text(encoding="utf-8")
    )
    assert callers == ["zigbee/quirks.py", "zigbee/source.py"]


async def test_the_connection_hook_reports_every_change(build) -> None:
    """`Runtime.set_zigbee_connected` (Task 10) and the radios card (Task
    11) both hang off this hook, and both are wrong in the same way if a
    link loss does not reach it: the badge says connected while no value
    arrives.

    Fault to prove it: call the hook from `connect()` only."""
    changes: list[bool] = []

    async def record(connected: bool) -> None:
        changes.append(connected)

    first = FakeApplication(devices=[colour_lamp()])
    second = FakeApplication(devices=[colour_lamp()])
    harness = build(first, second, on_connection_change=record)

    await harness.source.connect()
    assert changes == [True]

    first.fire_connection_lost()
    await _settle(harness.source)
    assert changes == [True, False]

    await harness.source.connect()
    assert changes == [True, False, True]

    await harness.source.disconnect()
    assert changes == [True, False, True, False]
    assert second.shutdown_calls == [True]


async def test_progress_counts_failed_attempts_and_clears_them_on_success(build) -> None:
    """The radios card polls this while the supervisor works, so it has to
    distinguish "still trying, 4 attempts" from a first try that is about to
    succeed - and it must stop saying "failed" once one succeeds.

    Fault to prove it: keep the attempt count after a successful connect.
    The card then says a working radio has failed four times."""
    harness = build(
        FakeApplication(startup_error=TimeoutError()),
        FakeApplication(startup_error=TimeoutError()),
        FakeApplication(devices=[colour_lamp()]),
    )

    assert harness.source.progress().state == "idle"
    assert harness.source.progress().attempts == 0
    assert harness.source.progress().error is None

    for expected in (1, 2):
        with pytest.raises(ZigbeeUnavailableError):
            await harness.source.connect()
        assert harness.source.progress().state == "failed"
        assert harness.source.progress().attempts == expected

    await harness.source.connect()
    assert harness.source.progress().state == "connected"
    assert harness.source.progress().attempts == 0
    assert harness.source.progress().error is None

    await harness.source.disconnect()
    assert harness.source.progress().state == "idle"


# ------------------------------------------------------------ configuration --


async def test_ota_is_off_and_the_database_is_next_to_the_store(build, tmp_path: Path) -> None:
    """zigpy's OTA is ON by default with three internet providers and a
    broadcast every 3.9 h. A bridge that silently updates the user's lamps
    from the internet is not what this project promises.

    Fault to prove it: leave the OTA config at its default."""
    harness = build(FakeApplication())
    await harness.source.connect()

    config = harness.factory.configs[0]
    assert config["ota"] == {"enabled": False}
    assert config["database_path"] == str(tmp_path / "zigbee.sqlite")
    # Kept at its 4 h default: it is what makes neighbour tables and, later,
    # "join via this router" possible (design 8.5).
    assert config["topology_scan_enabled"] is True
    assert config["device"] == {
        "path": "/dev/serial/by-id/usb-SONOFF_Zigbee_3.0_USB_Dongle_Plus_V2-if00",
        "baudrate": 115200,
        "flow_control": "software",
    }


async def test_a_stick_carrying_another_network_stops_with_a_clear_message(build) -> None:
    """zigpy ADOPTS whatever is on a stick - it only forms when the network
    is not formed. With `validate_network_settings = True` a mismatch
    against the stored backup raises `NetworkSettingsInconsistent`, and the
    bridge stops there and does NOT overwrite: the other option is
    destructive and needs the write-once-EUI64 confirmation, which is 2b.

    Fault to prove it: pass `validate_network_settings=False`. The bridge
    then silently adopts or overwrites somebody's network."""
    harness = build(
        FakeApplication(startup_error=NetworkSettingsInconsistent("PAN ID differs from the backup"))
    )

    with pytest.raises(ZigbeeUnavailableError) as caught:
        await harness.source.connect()

    assert str(caught.value) == i18n.t("api.errors.zigbee_network_mismatch")
    assert harness.factory.configs[0]["validate_network_settings"] is True
    assert harness.app.shutdown_calls == [True]


@pytest.mark.parametrize(
    ("thread_channel", "expected"),
    [
        # The first and the last entry of the list, so an off-by-one in the
        # exclusion cannot hide in the middle of it.
        (11, [15, 20, 25]),
        (25, [11, 15, 20]),
        (15, [11, 20, 25]),
        # No border router, or one on a channel Zigbee would not have
        # picked anyway: the full list. A missing border router must never
        # stop Zigbee from forming.
        (None, [11, 15, 20, 25]),
        (26, [11, 15, 20, 25]),
    ],
)
async def test_the_thread_channel_is_left_out_of_the_candidate_list(
    build, thread_channel, expected
) -> None:
    """Zigbee and Thread share the 2.4 GHz band, and the Pi runs both on one
    host. Forming a Zigbee network on the channel the border router is
    already using is the one avoidable collision here.

    Fault to prove it: pass the full list regardless. Zigbee then forms on
    the Thread channel one time in four."""
    harness = build(FakeApplication(), thread_channel=thread_channel)
    await harness.source.connect()

    assert harness.factory.configs[0]["network"]["channels"] == expected
    assert list(ZIGBEE_CHANNELS) == [11, 15, 20, 25]


# ----------------------------------------------------------------- snapshots --


async def test_colour_capabilities_reach_the_endpoint_facts(build) -> None:
    """Without `ColorCapabilities` (0x400A) every Zigbee lamp loses its
    colour picker.

    `profiles.capabilities` gates the colour controls on `FeatureMap`
    (0xFFFC) with a fallback to `ColorCapabilities`, and zigpy hands out no
    FeatureMap at all - so this one attribute decides whether the export
    offers MoveToColor and MoveToHueAndSaturation. The interview does not
    read it (zigpy reads only Basic's manufacturer and model), so the source
    reads it itself, and what comes back has to reach `EndpointFacts` -
    silently swallowing the read would fail SAFE and still be wrong.

    Fault to prove it: skip the read. The lamp below keeps only the colour
    temperature command it gets from its readable `color_temperature`, and
    the XY command - the one ZHA actually sends - disappears."""
    # Nothing cached, but the lamp answers a read: exactly a freshly joined
    # lamp before any configure-on-join has run.
    lamp = colour_lamp(colour_capabilities=None, readable_capabilities=0x08)
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()

    snapshot = (await harness.source.snapshots())[0]

    colour = lamp.endpoints[1].in_clusters[0x0300]
    assert colour.reads == [[0x400A]], "the capabilities are read when they are not cached"
    assert snapshot.attributes["1/768/16394"] == 0x08
    assert snapshot.attributes["1/768/65529"] == [7, 10]


async def test_cached_colour_capabilities_are_used_without_a_second_read(build) -> None:
    """The read above is the fallback, not the rule: a lamp whose
    capabilities are already in zigpy's cache must not be woken for them.

    Fault to prove it: read unconditionally. Every snapshot then costs one
    round trip per colour lamp."""
    lamp = colour_lamp(colour_capabilities=0x1F)
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()

    snapshot = (await harness.source.snapshots())[0]

    assert lamp.endpoints[1].in_clusters[0x0300].reads == []
    assert snapshot.attributes["1/768/16394"] == 0x1F
    assert snapshot.attributes["1/768/65529"] == [6, 7, 10]


async def test_a_lamp_that_never_answers_is_asked_once_and_then_left_alone(build) -> None:
    """A battery device asleep, or a lamp switched off at the wall, answers
    nothing - and `snapshots()` runs on every reconnect. Retrying the read
    each time would be traffic nobody asked for, and a failed read must
    never take the whole catalogue down.

    Fault to prove it: let the read's failure escape. One unreachable lamp
    then empties the device list."""
    lamp = colour_lamp(colour_capabilities=None)
    lamp.endpoints[1].in_clusters[0x0300].read_error = DeliveryError("no route to device")
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()

    first = await harness.source.snapshots()
    second = await harness.source.snapshots()

    assert [snapshot.address for snapshot in first] == [LAMP_IEEE]
    assert [snapshot.address for snapshot in second] == [LAMP_IEEE]
    assert len(lamp.endpoints[1].in_clusters[0x0300].reads) == 1


async def test_a_snapshot_carries_the_matter_paths_the_rest_of_the_bridge_reads(build) -> None:
    """`snapshots()` is the whole interface to everything downstream
    (design 5.1), and a battery sensor is where the translation actually
    moves something: the IAS bitmap becomes `1/69/0` and the battery
    percentage moves off its Zigbee endpoint onto the synthesised root.

    Fault to prove it: build `EndpointFacts` from the ZDO endpoint too. The
    device type list then lands on endpoint 0 and the sensor loses its
    category."""
    sensor = contact_sensor()
    harness = build(FakeApplication(devices=[sensor]))
    await harness.source.connect()

    snapshot = (await harness.source.snapshots())[0]

    assert snapshot.technology == "zigbee"
    assert snapshot.address == SENSOR_IEEE
    assert snapshot.attributes["0/40/1"] == "LUMI"
    assert snapshot.attributes["0/40/3"] == "lumi.sensor_magnet"
    # Contact switch, zone status 0 (closed) - BooleanState is inverted
    # against IAS, so a closed contact is True.
    assert snapshot.attributes["1/69/0"] is True
    assert snapshot.attributes["0/47/12"] == 150
    assert snapshot.attributes["1/29/0"] == [{"0": 0x0015, "1": 1}]


# -------------------------------------------------------------------- events --


async def test_cluster_events_are_queued_not_awaited_in_the_callback(build) -> None:
    """zigpy's cluster events are SYNCHRONOUS callbacks that do not catch
    exceptions (R1 section 6, verified against `EventBase.emit` in zigpy
    2.2.0). A handler that raises inside one would tear down zigpy's event
    emission itself, so the callback does nothing but `put_nowait` and one
    dispatch task does the awaiting work - the same shape as
    `BridgeMatterClient._dispatch_loop`.

    The fault is injected through `resolve_device_id`, because that is the
    piece of work most easily pulled forward into the callback: it reads
    SQLite, and SQLite can be locked by the resend loop at any moment.

    Fault to prove it: resolve the device inside the callback. The report
    below then raises out of `emit` - into whatever zigpy was doing when the
    attribute arrived - and delivery stops for every device."""
    lamp = colour_lamp()
    sensor = contact_sensor()
    harness = build(FakeApplication(devices=[lamp, sensor]))
    await harness.source.connect()

    locked = {"now": False}

    def resolve(address: str) -> int | None:
        if locked["now"] and address == LAMP_IEEE:
            raise RuntimeError("database is locked")
        return 7 if address == LAMP_IEEE else 9

    await harness.source.subscribe(resolve, harness.handler)
    locked["now"] = True

    # Straight out of zigpy's emit: if the callback did the work, this line
    # would raise.
    lamp.endpoints[1].in_clusters[0x0006].report(0x0000, False)
    await _settle(harness.source)

    # And the next device is still served.
    sensor.endpoints[1].in_clusters[0x0500].report(0x0002, 1)
    await _settle(harness.source)

    # The contact opened, and BooleanState is inverted against IAS.
    assert harness.handler.attributes == [(9, "1/69/0", False)]


async def test_one_failing_update_does_not_end_delivery(build) -> None:
    """Fault to prove it: remove the `except Exception` in the dispatch
    loop."""
    lamp = colour_lamp()
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()
    await harness.source.subscribe(_lamp_resolver(), harness.handler)
    await _settle(harness.source)
    harness.handler.snapshots.clear()

    harness.handler.fail_next = 1
    lamp.endpoints[1].in_clusters[0x0006].report(0x0000, False)
    await _settle(harness.source)

    lamp.endpoints[1].in_clusters[0x0008].report(0x0000, 120)
    await _settle(harness.source)

    assert (7, "1/8/0", 120) in harness.handler.attributes


async def test_a_path_seen_for_the_first_time_arrives_as_a_snapshot(build) -> None:
    """A path that was absent at join and present later must reach
    `on_node_snapshot`, not `on_attribute`.

    `Store.register_signals` computes `exported` only when the row is
    CREATED, and only `on_node_snapshot` creates rows. A value delivered as
    a bare attribute for a path that has no row yet is discarded in silence -
    and for a sensor whose first reading is also its first appearance, that
    is every reading it will ever send (design 5.5, research D.1).

    Fault to prove it: deliver every update as `on_attribute`. The
    temperature below then never gets a row."""
    lamp = colour_lamp()
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()
    await harness.source.subscribe(_lamp_resolver(), harness.handler)
    await _settle(harness.source)

    # A known path with a new value: the row exists, so the value goes
    # straight through as an attribute.
    harness.handler.snapshots.clear()
    lamp.endpoints[1].in_clusters[0x0008].report(0x0000, 42)
    await _settle(harness.source)
    assert harness.handler.attributes == [(7, "1/8/0", 42)]
    assert harness.handler.snapshots == []

    # A path nobody has seen before: a snapshot, so the row is created with
    # `exported` computed from a value that exists.
    lamp.endpoints[1].in_clusters[0x0300].report(0x0003, 19000)
    await _settle(harness.source)
    assert [device_id for device_id, _ in harness.handler.snapshots] == [7]
    assert harness.handler.snapshots[-1][1].attributes["1/768/3"] == 19000


async def test_updates_for_a_device_the_store_does_not_know_are_dropped(build) -> None:
    """A device zigpy knows and the store does not - one paired while the
    pairing tab was open, before it was registered. `Runtime` hangs its keys
    off the `device_id`, so there is nothing to address.

    Fault to prove it: pass `None` through as a device id."""
    lamp = colour_lamp()
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()
    await harness.source.subscribe(lambda _address: None, harness.handler)

    lamp.endpoints[1].in_clusters[0x0006].report(0x0000, False)
    await _settle(harness.source)

    assert harness.handler.snapshots == []
    assert harness.handler.attributes == []


async def test_cluster_listeners_are_registered_again_after_a_reconnect(build) -> None:
    """zigpy builds new device objects on every `connect()` - the old ones
    were torn down with `on_remove()`. A source that registered its
    listeners once would go silent after the first reconnect and look
    perfectly healthy doing it (R1 section 6).

    Fault to prove it: register the cluster listeners in `subscribe()`
    alone."""
    first_lamp = colour_lamp()
    second_lamp = colour_lamp()
    harness = build(
        FakeApplication(devices=[first_lamp]),
        FakeApplication(devices=[second_lamp]),
    )
    await harness.source.connect()
    await harness.source.subscribe(_lamp_resolver(), harness.handler)
    await _settle(harness.source)

    harness.applications[0].fire_connection_lost()
    await harness.source.connect()
    await _settle(harness.source)
    harness.handler.attributes.clear()

    second_lamp.endpoints[1].in_clusters[0x0008].report(0x0000, 33)
    await _settle(harness.source)

    assert (7, "1/8/0", 33) in harness.handler.attributes


# ------------------------------------------------------------------ commands --


async def test_send_renames_the_payload_and_drops_what_the_command_lacks(build) -> None:
    """Fault to prove it: pass the Matter payload through unchanged. zigpy
    then rejects every command with a schema error."""
    lamp = colour_lamp()
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()

    await harness.source.send(
        DeviceCall(
            technology="zigbee",
            address=LAMP_IEEE,
            endpoint=1,
            cluster_id=768,
            command_id=10,
            payload={
                "colorTemperatureMireds": 370,
                "transitionTime": 5,
                "optionsMask": 0,
                "optionsOverride": 0,
            },
        )
    )
    # `color_temp_mireds`, NOT the mechanical `color_temperature_mireds`.
    assert lamp.endpoints[1].in_clusters[0x0300].sent == [
        (
            10,
            {
                "color_temp_mireds": 370,
                "transition_time": 5,
                "options_mask": 0,
                "options_override": 0,
            },
        )
    ]

    await harness.source.send(
        DeviceCall(
            technology="zigbee",
            address=LAMP_IEEE,
            endpoint=1,
            cluster_id=8,
            command_id=4,
            payload={
                "level": 128,
                "transitionTime": 5,
                "optionsMask": 0,
                "optionsOverride": 0,
            },
        )
    )
    # `move_to_level_with_on_off` declares no options fields at all, so the
    # two the Matter payload carries are DROPPED rather than passed on.
    assert lamp.endpoints[1].in_clusters[0x0008].sent == [(4, {"level": 128, "transition_time": 5})]


@pytest.mark.parametrize(
    ("raised", "status"),
    [
        (DeliveryError("no route to device"), 0),
        (ControllerException("controller is not running"), 0),
        (ZigbeeException("the radio said no"), 0),
        (TimeoutError(), 0),
        # A command that was delivered and refused: no exception at all,
        # and the same thing to the caller - asked, no answer.
        (None, 0x86),
    ],
)
async def test_every_zigpy_failure_leaves_as_device_unreachable(build, raised, status) -> None:
    """`DeliveryError`, `ControllerError`, `ZigbeeException`, `TimeoutError`
    and a non-SUCCESS ZCL status all mean the same thing to a caller: asked,
    no answer. They are translated HERE, so no `except` clause in shared
    code ever names a zigpy type - and so `api/devices.py`'s removal route,
    which used to catch `MatterUnavailableError` alone, does not turn a
    Zigbee removal failure into an unhandled 500.

    (zigpy 2.2.0 spells the design's `ControllerError`
    `ControllerException`; the fake follows the library, not the document.)

    Fault to prove it: let `DeliveryError` escape. The removal route then
    answers 500 instead of 502."""
    lamp = colour_lamp()
    lamp.endpoints[1].in_clusters[0x0006].command_error = raised
    lamp.endpoints[1].in_clusters[0x0006].command_status = status
    harness = build(FakeApplication(devices=[lamp], remove_error=raised))
    await harness.source.connect()

    with pytest.raises(DeviceUnreachableError) as caught:
        await harness.source.send(
            DeviceCall(
                technology="zigbee",
                address=LAMP_IEEE,
                endpoint=1,
                cluster_id=6,
                command_id=1,
            )
        )
    # A 502 whose detail is blank tells the person reading it nothing, and
    # `str(TimeoutError())` is the empty string.
    assert str(caught.value).strip()
    assert "unreachable" in str(caught.value).lower()

    if raised is not None:
        with pytest.raises(DeviceUnreachableError):
            await harness.source.remove(LAMP_IEEE)


async def test_a_command_for_a_radio_that_is_down_says_so(build) -> None:
    """503 is for a technology nobody serves; this is 502, because there IS
    a Zigbee source - its stick is simply not answering right now.

    Fault to prove it: send into the torn-down application anyway."""
    lamp = colour_lamp()
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()
    harness.app.fire_connection_lost()
    await _settle(harness.source)

    with pytest.raises(DeviceUnreachableError) as caught:
        await harness.source.send(
            DeviceCall(
                technology="zigbee",
                address=LAMP_IEEE,
                endpoint=1,
                cluster_id=6,
                command_id=1,
            )
        )

    assert str(caught.value) == i18n.t("api.errors.zigbee_not_connected")
    assert lamp.endpoints[1].in_clusters[0x0006].sent == []


async def test_removal_treats_device_removed_as_the_truth(build) -> None:
    """`remove()` deletes the device from zigpy's database whether or not
    the leave request is ever delivered (R1 section 4).

    Fault to prove it: wait for a leave confirmation that never comes."""
    lamp = colour_lamp()
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()
    await harness.source.subscribe(_lamp_resolver(), harness.handler)

    # The fake never sends a leave confirmation, exactly like a device that
    # was already asleep or already gone. The bound is what turns a wait for
    # one into a failure rather than a hung suite.
    await asyncio.wait_for(harness.source.remove(LAMP_IEEE), 1)

    assert harness.app.removed == [LAMP_IEEE]
    assert await harness.source.snapshots() == []
    assert harness.source.pairing_rows() == []


# ------------------------------------------------------------------ pairing --


async def test_the_permit_window_has_the_protocol_maximum_as_its_limit(build) -> None:
    """zigpy asserts `0 <= t <= 254`, and no unlimited mode is offered:
    Zigbee2MQTT removed its permanent option in 2.0 as a security concern.
    The end timestamp comes back, not the duration, so a page reloaded
    halfway through counts down to the truth.

    Fault to prove it: clamp 255 to 254 instead of refusing it. "Forever"
    then looks like it worked."""
    harness = build(FakeApplication())
    await harness.source.connect()

    before = datetime.now(UTC)
    until = await harness.source.permit(254)

    assert harness.app.permits == [(254, None)]
    assert 253 <= (until - before).total_seconds() <= 255

    with pytest.raises(ValueError):
        await harness.source.permit(255)
    with pytest.raises(ValueError):
        await harness.source.permit(-1)

    # Stop closes the window immediately.
    await harness.source.permit(0)
    assert harness.app.permits[-1] == (0, None)


async def test_a_device_that_appears_without_a_window_is_not_reported_as_joined(build) -> None:
    """zigpy interviews devices of an ADOPTED network on its own
    (`_discover_unknown_device`), so devices can appear when nobody opened a
    window (design 4.8). Telling the user "a device joined just now" would
    be false.

    Fault to prove it: report every new device as a join."""
    harness = build(FakeApplication())
    await harness.source.connect()

    discovered = colour_lamp("00:12:4b:00:1c:00:00:01")
    harness.app.fire_device_joined(discovered)
    await _settle(harness.source)
    rows = {row.ieee: row for row in harness.source.pairing_rows()}
    assert rows[discovered.ieee].discovered is True

    await harness.source.permit(254)
    joined = colour_lamp("00:12:4b:00:1c:00:00:02")
    harness.app.fire_device_joined(joined)
    await _settle(harness.source)
    rows = {row.ieee: row for row in harness.source.pairing_rows()}
    assert rows[joined.ieee].discovered is False
    assert rows[joined.ieee].state == "joined"

    harness.app.fire_device_initialized(joined)
    await _settle(harness.source)
    rows = {row.ieee: row for row in harness.source.pairing_rows()}
    assert rows[joined.ieee].state == "ready"
    assert rows[joined.ieee].model == "TRADFRI bulb"
