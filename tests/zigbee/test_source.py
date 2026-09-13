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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fakes import (
    ControllerException,
    DeliveryError,
    FakeApplication,
    FakeApplicationFactory,
    FakeCluster,
    FakeDevice,
    FakeEndpoint,
    FakeNodeDescriptor,
    NetworkSettingsInconsistent,
    ZigbeeException,
    colour_lamp,
    contact_sensor,
)

from loxmatter import i18n
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.radios.fingerprints import Fingerprint
from loxmatter.sources import DeviceCall, DeviceUnreachableError
from loxmatter.zigbee import source as source_module
from loxmatter.zigbee.source import ZIGBEE_CHANNELS, ZigbeeSource, ZigbeeUnavailableError

LAMP_IEEE = "00:12:4b:00:1c:a1:b2:c3"
SENSOR_IEEE = "00:15:8d:00:02:aa:bb:cc"

# TemperatureMeasurement, `measured_value` - one of `REPORTING`'s own rows,
# so a cluster of this number really is one configure-on-join would bind.
TEMPERATURE_CLUSTER = 0x0402

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
        store: Any = None,
        open_guard: Any = None,
        thread_channel_lookup: Any = None,
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
            store=store,
            open_guard=open_guard,
            thread_channel_lookup=thread_channel_lookup,
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
    # The card reads the same words out of `progress()` - kept as the
    # message and translated when the route answers.
    assert harness.source.progress().state == "failed"
    assert harness.source.progress().as_json()["error"] == i18n.t(key)


async def test_a_stick_the_open_guard_refuses_is_never_touched(build) -> None:
    """The Thread lock-out, at the one place every open passes through.
    `thread_lockout.open_refusal` is asked with the stick's path before the
    quirks warm-up and before any application is built; a refusal fails
    the attempt with its own sentence, counted like any other failed
    attempt, so the supervisor backs off and the card shows why. The next
    attempt asks again, and opens once the guard allows it.

    Fault to prove it: skip the guard in `connect()` (an application is
    built and started on the refused stick), or call it after
    `ensure_quirks_loaded()` (the warm-up shows up in `order`)."""
    answers: list[i18n.Message | None] = [
        i18n.Message.of("api.errors.zigbee_open_is_thread_stick"),
        None,
        None,
    ]
    asked: list[str] = []

    def guard(path: str) -> i18n.Message | None:
        asked.append(path)
        return answers.pop(0)

    harness = build(FakeApplication(), open_guard=guard)

    with pytest.raises(ZigbeeUnavailableError) as caught:
        await harness.source.connect()

    assert str(caught.value) == i18n.t("api.errors.zigbee_open_is_thread_stick")
    assert asked == [harness.source._path]
    assert harness.order == []
    assert harness.app.startup_calls == []
    progress = harness.source.progress()
    assert (progress.state, progress.attempts) == ("failed", 1)
    assert progress.as_json()["error"] == i18n.t("api.errors.zigbee_open_is_thread_stick")

    await harness.source.connect()
    assert harness.source.connected
    # Asked twice on the attempt that opened: before the warm-up, and again
    # right before the port - see the test below.
    assert len(asked) == 3
    await harness.source.disconnect()


async def test_the_open_guard_is_asked_again_right_before_the_port_is_opened(
    build, monkeypatch
) -> None:
    """The guard used to be answered once, before a warm-up that takes
    9-15 s on a Pi 4 and before the application is built - and the answer
    was then assumed for the moment the port opened. Thread moved onto the
    stored stick in between (a hand edit of `.env`, the one path the
    product does not close) would have had zigpy open the border router's
    radio anyway.

    So it is asked again immediately before `startup()`. A refusal there
    shuts the application that was just built down unstarted, and fails
    the attempt with the guard's sentence like any other refusal.

    Fault to prove it: drop the second ask (the application is started on
    the Thread stick), or skip the shutdown of the unstarted application
    (`shutdown_calls` stays empty)."""
    thread_moved = [False]
    asked: list[str] = []

    def guard(path: str) -> i18n.Message | None:
        asked.append(path)
        return (
            i18n.Message.of("api.errors.zigbee_open_is_thread_stick") if thread_moved[0] else None
        )

    application = FakeApplication()
    harness = build(application, open_guard=guard)

    async def warm_up_while_thread_moves() -> float:
        thread_moved[0] = True
        return 0.0

    monkeypatch.setattr(source_module, "ensure_quirks_loaded", warm_up_while_thread_moves)

    with pytest.raises(ZigbeeUnavailableError) as caught:
        await harness.source.connect()

    assert str(caught.value) == i18n.t("api.errors.zigbee_open_is_thread_stick")
    assert len(asked) == 2
    assert application.startup_calls == []
    assert application.shutdown_calls == [True]
    assert not harness.source.connected
    progress = harness.source.progress()
    assert (progress.state, progress.attempts) == ("failed", 1)


async def test_the_thread_channel_is_read_once_per_source_before_anything_forms(build) -> None:
    """The border router's channel is what a new Zigbee network must stay
    off, and it used to be fetched while the source was BUILT - at bridge
    startup, ahead of matter-server's connection and the web UI, with a 5 s
    timeout an OTBR slow to start could spend in full.

    It is read on the first connect instead, which runs in the supervisor's
    background task: after the open guard, before the warm-up and before
    any application exists, so no network can form before the channel is
    known. It is read once per source, not once per retry, and an answer of
    "no border router" is an answer.

    Fault to prove it: read it after the application is built (the
    candidate list still carries 15), or on every connect (two reads)."""
    reads: list[str] = []

    async def lookup() -> int | None:
        reads.append(harness.source.progress().state)
        harness.order.append("thread_channel")
        return 15

    harness = build(FakeApplication(), FakeApplication(), thread_channel_lookup=lookup)

    await harness.source.connect()
    await harness.source.connect()

    assert harness.order[:3] == [
        "thread_channel",
        "quirks:loading_quirks",
        "application:opening_radio",
    ]
    assert reads == ["loading_quirks"]
    assert [config["network"]["channels"] for config in harness.factory.configs] == [
        [11, 20, 25],
        [11, 20, 25],
    ]
    await harness.source.disconnect()


async def test_a_thread_channel_read_that_fails_forms_on_every_channel_and_asks_again(
    build, caplog
) -> None:
    """`current_thread_channel` answers `None` for every way OTBR can be
    missing; anything it raises beyond that is a defect, and must neither
    stop Zigbee from connecting nor be remembered as an answer. The attempt
    goes ahead on the full list - what a missing border router already gets
    - with a warning, and the next connect asks again.

    Fault to prove it: let the exception fail the attempt (the first
    connect raises), or remember the failure (the second connect does not
    ask)."""
    answers: list[Any] = [RuntimeError("dataset parser broke"), 20]

    async def lookup() -> int | None:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    harness = build(FakeApplication(), FakeApplication(), thread_channel_lookup=lookup)
    caplog.set_level("INFO", logger="loxmatter.zigbee.source")

    await harness.source.connect()
    await harness.source.connect()

    assert [config["network"]["channels"] for config in harness.factory.configs] == [
        [11, 15, 20, 25],
        [11, 15, 25],
    ]
    assert any(
        r.levelname == "WARNING" and "Thread channel" in r.getMessage() for r in caplog.records
    )
    await harness.source.disconnect()


async def test_the_connect_line_names_the_channel_and_whether_the_database_knew_the_network(
    build, caplog
) -> None:
    """A stick that already carries a network is adopted with its own
    channel and key, and zigpy says so only at INFO on its own logger,
    which the bridge does not show. The hardware checklist has to record
    which channel the network ended up on and whether it was one this
    bridge's database already knew, so the connect line carries both: the
    channel and PAN IDs from `app.state.network_info`, and whether zigpy's
    database held a network backup BEFORE the port was opened. Never the
    network key.

    Fault to prove it: read the backups after `startup()` (a network formed
    during it would read as known), or drop the channel from the line."""
    known = FakeApplication()
    known.backups.backups.append(object())
    known.state.network_info.channel = 25
    fresh = FakeApplication()
    harness = build(known, fresh)
    caplog.set_level("INFO", logger="loxmatter.zigbee.source")

    original_startup = fresh.startup

    async def startup_that_forms(*, auto_form: bool = False) -> None:
        await original_startup(auto_form=auto_form)
        fresh.backups.backups.append(object())

    fresh.startup = startup_that_forms  # type: ignore[method-assign]

    await harness.source.connect()
    await harness.source.connect()

    lines = [
        r.getMessage() for r in caplog.records if "Zigbee coordinator connected" in r.getMessage()
    ]
    assert "channel 25" in lines[0]
    assert "PAN ID 0x1A62" in lines[0]
    assert "already in this bridge's database" in lines[0]
    assert "channel 15" in lines[1]
    assert "not in this bridge's database" in lines[1]
    assert all("key" not in line.lower() for line in lines)
    await harness.source.disconnect()


def test_zigpy_opens_the_node_under_the_mount_and_falls_back_to_the_given_path(
    tmp_path: Path,
) -> None:
    """Three installations, one rule.

    - In the container the stick is opened under the host-dev mount, and
      the source still calls it by its host path.
    - Outside the container (`--zigbee-device /dev/ttyUSB0` on bare metal)
      there is nothing under the mount, and the given path is what exists.
    - A stick plugged in after the source was built appears under the
      mount later, so the choice is made per open and not once.

    Fault to prove it: always map (the bare-metal half opens a path that
    does not exist), never map (the container half fails), or decide once
    in `__init__` (the hotplug half keeps the host path)."""
    host_dev = tmp_path / "host-dev"
    by_id = "/dev/serial/by-id/usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2-if00-port0"

    def source(path: str) -> ZigbeeSource:
        return ZigbeeSource(
            path=path,
            fingerprint=FINGERPRINT,
            database=tmp_path / "zigbee.sqlite",
            host_dev=host_dev,
        )

    bare_metal = source("/dev/ttyUSB0")
    assert bare_metal._config()["device"]["path"] == "/dev/ttyUSB0"

    hotplugged = source(by_id)
    assert hotplugged._config()["device"]["path"] == by_id
    (host_dev / "serial" / "by-id").mkdir(parents=True)
    (host_dev / "ttyUSB1").write_text("", encoding="utf-8")
    (host_dev / "serial" / "by-id" / Path(by_id).name).symlink_to(Path("../..") / "ttyUSB1")
    assert hotplugged._config()["device"]["path"] == f"{host_dev}/serial/by-id/{Path(by_id).name}"
    assert hotplugged._path == by_id


async def test_each_successful_connect_logs_the_firmware_once_at_info(build, caplog) -> None:
    """The hardware checklist asks for the coordinator's firmware, and
    bellows logs its stack version only at DEBUG while the bridge logs at
    INFO - so it was nowhere a user could read it. After `startup()` the
    source reads `app.state.node_info` (the attribute zigpy's radio
    libraries fill; pinned in `test_zigpy_names.py`) and logs the radio
    type and firmware at INFO, once per successful connect, and keeps them
    for `GET /api/zigbee/radio`.

    Fault to prove it: log at DEBUG (the INFO line is missing), read a
    different attribute than `version` (the firmware reads "unknown"), or
    call `_note_coordinator` from both `connect()` and `subscribe()` (two
    lines per connect)."""
    first, second = FakeApplication(), FakeApplication()
    second.state.node_info.version = "7.5.0.0 build 12"
    harness = build(first, second)
    caplog.set_level("INFO", logger="loxmatter.zigbee.source")

    assert harness.source.coordinator() is None
    await harness.source.connect()
    lines = [r for r in caplog.records if "Zigbee coordinator connected" in r.getMessage()]
    assert [r.levelname for r in lines] == ["INFO"]
    assert "7.4.4.0 build 0" in lines[0].getMessage()
    assert "ezsp" in lines[0].getMessage()
    assert harness.source.coordinator().as_json() == {
        "radio_type": "ezsp",
        "manufacturer": "ITEAD",
        "model": "SONOFF Zigbee 3.0 USB Dongle Plus V2",
        "firmware": "7.4.4.0 build 0",
    }

    await harness.source.connect()
    lines = [r for r in caplog.records if "Zigbee coordinator connected" in r.getMessage()]
    assert len(lines) == 2
    assert "7.5.0.0 build 12" in lines[1].getMessage()
    assert harness.source.coordinator().firmware == "7.5.0.0 build 12"
    await harness.source.disconnect()


async def test_a_radio_that_reports_no_firmware_says_unknown_and_invents_none(
    build, caplog
) -> None:
    """A library that leaves `version` unset - or an empty string - is
    reported as unknown, never as a made-up value.

    Fault to prove it: fall back to the fingerprint's name or the library
    version for `firmware`."""
    application = FakeApplication()
    application.state.node_info.version = ""
    application.state.node_info.model = None
    harness = build(application)
    caplog.set_level("INFO", logger="loxmatter.zigbee.source")

    await harness.source.connect()

    assert harness.source.coordinator().firmware is None
    assert harness.source.coordinator().model is None
    line = next(r for r in caplog.records if "Zigbee coordinator connected" in r.getMessage())
    assert "firmware unknown" in line.getMessage()
    await harness.source.disconnect()


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


async def test_a_retry_carries_the_previous_reason_and_a_success_drops_it(build) -> None:
    """The radios card's retry line says WHY it is trying again. Each retry
    used to start with `error: None`, so the reason vanished for the length
    of every attempt and came back when that attempt failed too; the card
    patched over it with a copy of its own, which then outlived a reload
    and a language switch. The source carries it instead.

    It is carried only through a RETRY. A first attempt has nothing to
    explain, and the reconnect after a lost link (`attempts` still 0)
    starts clean because "the link is down" is what it is fixing.

    Fault to prove it: set the two working states with `error=None`
    (the retry loses its reason), or carry the error whatever `attempts`
    is (the reconnect after a lost link keeps a stale one)."""
    seen: list[dict[str, object]] = []

    class Observed(FakeApplication):
        async def startup(self, *, auto_form: bool = False) -> None:
            seen.append(harness.source.progress().as_json())
            await super().startup(auto_form=auto_form)

    first_ok = Observed(devices=[colour_lamp()])
    harness = build(
        FakeApplication(startup_error=TimeoutError()),
        first_ok,
        Observed(devices=[colour_lamp()]),
    )

    with pytest.raises(ZigbeeUnavailableError):
        await harness.source.connect()
    await harness.source.connect()
    assert seen[0]["state"] == "opening_radio"
    assert seen[0]["attempts"] == 1
    assert seen[0]["error"] == i18n.t("api.errors.zigbee_not_a_coordinator")
    assert harness.source.progress().error is None

    first_ok.fire_connection_lost()
    assert harness.source.progress().as_json()["error"] == i18n.t("api.errors.zigbee_not_connected")
    await harness.source.connect()
    assert (seen[1]["attempts"], seen[1]["error"]) == (0, None)

    await harness.source.disconnect()


async def test_a_failure_is_translated_when_it_is_read_not_when_it_happened(build) -> None:
    """The radios card shows a failure for as long as the supervisor
    retries - minutes, or forever for an unplugged stick. A sentence
    translated when the attempt failed stayed in that language after the
    user switched, beside the attempt counter the browser already rendered
    in the new one: one line, two languages.

    Fault to prove it: store `i18n.t(key)` in `progress().error` instead of
    the message (in `_startup_message` or `_handle_connection_lost`)."""
    harness = build(
        FakeApplication(startup_error=FileNotFoundError()),
        FakeApplication(devices=[colour_lamp()]),
    )

    i18n.set_language("de")
    with pytest.raises(ZigbeeUnavailableError):
        await harness.source.connect()
    i18n.set_language("en")
    assert harness.source.progress().as_json()["error"] == i18n.t("api.errors.zigbee_stick_missing")

    await harness.source.connect()
    i18n.set_language("de")
    harness.applications[1].fire_connection_lost()
    i18n.set_language("en")
    assert harness.source.progress().as_json()["error"] == i18n.t("api.errors.zigbee_not_connected")
    await harness.source.disconnect()


async def test_a_connect_cancelled_mid_build_shuts_the_half_built_application_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The radios card lets the user change the stick while the supervisor
    retries a failing one, and the change cancels the supervisor wherever it
    is. zigpy's `ControllerApplication.new(start_radio=False)` has opened
    the network database by the time it returns, so an application that
    finished building after its caller was cancelled was dropped with that
    database still open - on the same file the next source opens.

    Fault to prove it: await `self._new_application()` directly in
    `connect()`. The application is then never shut down."""

    async def no_warm_up() -> float:
        return 0.0

    monkeypatch.setattr(source_module, "ensure_quirks_loaded", no_warm_up)
    application = FakeApplication()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_factory(config: dict[str, Any]) -> FakeApplication:
        entered.set()
        await release.wait()
        return application

    source = ZigbeeSource(
        path="/dev/serial/by-id/usb-SONOFF_Zigbee_3.0_USB_Dongle_Plus_V2-if00",
        fingerprint=FINGERPRINT,
        database=tmp_path / "zigbee.sqlite",
        application_factory=slow_factory,
    )
    attempt = asyncio.ensure_future(source.connect())
    await entered.wait()
    attempt.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await attempt

    assert application.shutdown_calls == [True]
    assert application.started is False


async def test_a_twice_cancelled_connect_still_shuts_the_half_built_application_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Task.cancel()` can keep delivering a fresh `CancelledError` at
    every await point until the coroutine actually returns - so a SECOND
    cancellation, arriving while `_new_application_even_if_cancelled` is
    already inside its own wait for the half-built application, must not
    abandon that wait either. It would leave the eventual application (and
    the network database it opened) built and never shut down - the same
    outcome `test_a_connect_cancelled_mid_build_shuts_the_half_built_application_down`
    exists to rule out for a SINGLE cancellation.

    Fault to prove it: replace the `while not build.done(): ...` loop in
    `_new_application_even_if_cancelled` with a bare `application = await
    build` (one catch only, as the single-cancellation code path used to
    read). The second `cancel()` below then raises straight out of that
    bare `await`, and `application.shutdown_calls` stays empty."""

    async def no_warm_up() -> float:
        return 0.0

    monkeypatch.setattr(source_module, "ensure_quirks_loaded", no_warm_up)
    application = FakeApplication()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_factory(config: dict[str, Any]) -> FakeApplication:
        entered.set()
        await release.wait()
        return application

    source = ZigbeeSource(
        path="/dev/serial/by-id/usb-SONOFF_Zigbee_3.0_USB_Dongle_Plus_V2-if00",
        fingerprint=FINGERPRINT,
        database=tmp_path / "zigbee.sqlite",
        application_factory=slow_factory,
    )
    attempt = asyncio.ensure_future(source.connect())
    await entered.wait()
    attempt.cancel()
    await asyncio.sleep(0)
    # `slow_factory` is still blocked on `release`, so this second
    # cancellation lands on the wait loop's OWN await, not on the first
    # one - the case a single `except` cannot survive.
    attempt.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await attempt

    assert application.shutdown_calls == [True]
    assert application.started is False


async def test_a_connect_cancelled_while_releasing_the_old_application_finishes_releasing_it(
    build,
) -> None:
    """The reconnect after a lost link shuts the previous application down
    first - and a radio change arriving at that moment cancels it halfway.
    The application is already detached from the source by then, so nothing
    would ever call `shutdown()` on it again, and the serial port it holds
    stays open for the stick the user may pick again a moment later.

    Fault to prove it: `await app.shutdown(db=True)` directly in
    `connect()`. The shutdown is then abandoned at its first `await`."""

    class SlowShutdown(FakeApplication):
        def __init__(self, **fields: Any) -> None:
            super().__init__(**fields)
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = False

        async def shutdown(self, *, db: bool = True) -> None:
            self.shutdown_calls.append(db)
            self.entered.set()
            await self.release.wait()
            self.started = False
            self.finished = True

    old = SlowShutdown(devices=[colour_lamp()])
    harness = build(old, FakeApplication())
    await harness.source.connect()
    old.fire_connection_lost()

    attempt = asyncio.ensure_future(harness.source.connect())
    await old.entered.wait()
    attempt.cancel()
    await asyncio.sleep(0)
    old.release.set()
    with pytest.raises(asyncio.CancelledError):
        await attempt

    assert old.finished is True
    assert harness.factory.calls == 1, "nothing new was opened behind the cancellation"


async def test_a_twice_cancelled_release_still_finishes_shutting_the_old_application_down(
    build,
) -> None:
    """`Task.cancel()` can keep delivering a fresh `CancelledError` at
    every await point until the coroutine actually returns - so a SECOND
    cancellation, arriving while `_shutdown_even_if_cancelled` is already
    inside its own wait for the old application's `shutdown()`, must not
    abandon that wait either. The application is already detached from the
    source by then, so nothing would ever call `shutdown()` on it again,
    and the serial port it holds would stay open - the same outcome
    `test_a_connect_cancelled_while_releasing_the_old_application_finishes_releasing_it`
    exists to rule out for a SINGLE cancellation.

    Fault to prove it: replace the `while not shutdown.done(): ...` loop
    in `_shutdown_even_if_cancelled` with a bare `with
    contextlib.suppress(Exception): await shutdown` (one catch only, as
    the single-cancellation code path used to read). The second `cancel()`
    below then raises straight out of that bare `await`, and `old.finished`
    stays `False`."""

    class SlowShutdown(FakeApplication):
        def __init__(self, **fields: Any) -> None:
            super().__init__(**fields)
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = False

        async def shutdown(self, *, db: bool = True) -> None:
            self.shutdown_calls.append(db)
            self.entered.set()
            await self.release.wait()
            self.started = False
            self.finished = True

    old = SlowShutdown(devices=[colour_lamp()])
    harness = build(old, FakeApplication())
    await harness.source.connect()
    old.fire_connection_lost()

    attempt = asyncio.ensure_future(harness.source.connect())
    await old.entered.wait()
    attempt.cancel()
    await asyncio.sleep(0)
    # `old.shutdown()` is still blocked on `release`, so this second
    # cancellation lands on the wait loop's OWN await, not on the first
    # one - the case a single `except` cannot survive.
    attempt.cancel()
    await asyncio.sleep(0)
    old.release.set()
    with pytest.raises(asyncio.CancelledError):
        await attempt

    assert old.finished is True
    assert harness.factory.calls == 1, "nothing new was opened behind the cancellation"


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


async def test_a_reinterviewed_device_is_listened_to_again(build) -> None:
    """A reinterview replaces the device OBJECT under the same IEEE.

    zigpy's `_device_reinterviewed` calls `old_device.on_remove()`, builds a
    new device with new clusters through `_finalize_device`, and then emits
    `device_reinterviewed` - deliberately not `device_initialized`. Both
    halves of that are load-bearing here: without a method by that name
    nothing re-binds, and with an address-keyed guard even a later
    `device_initialized` would return early because the IEEE is already in
    `_listening`. Either way the device reports nothing until the next
    `connect()` while the bridge keeps saying the link is fine.

    Two faults prove it, both observed. (a) Delete
    `_ApplicationListener.device_reinterviewed`: the replacement's report
    reaches nobody. (b) Keep `device_reinterviewed` but restore the
    address-keyed early return (`if address in self._listening: return`):
    the replacement's report reaches nobody either, for the second reason."""
    original = colour_lamp()
    harness = build(FakeApplication(devices=[original]))
    await harness.source.connect()
    await harness.source.subscribe(_lamp_resolver(), harness.handler)
    await _settle(harness.source)

    # The original really was being listened to - otherwise the assertion
    # below could pass against a source that listens to nothing at all.
    original.endpoints[1].in_clusters[0x0008].report(0x0000, 11)
    await _settle(harness.source)
    assert (7, "1/8/0", 11) in harness.handler.attributes

    replacement = colour_lamp()
    assert replacement is not original
    harness.app.fire_device_reinterviewed(replacement)
    await _settle(harness.source)
    harness.handler.attributes.clear()

    # The original's clusters are on an object zigpy has dropped; a report
    # through them must reach nobody, and a report through the replacement's
    # must arrive.
    original.endpoints[1].in_clusters[0x0008].report(0x0000, 99)
    await _settle(harness.source)
    assert harness.handler.attributes == []

    replacement.endpoints[1].in_clusters[0x0008].report(0x0000, 33)
    await _settle(harness.source)
    assert (7, "1/8/0", 33) in harness.handler.attributes


async def test_a_device_whose_quirk_declares_a_duplicate_attribute_id_does_not_stop_the_bridge(
    build,
) -> None:
    """The Ubisys shape: one cluster class, two definitions for one id.

    `Cluster.get(int)` resolves the id OUTSIDE its own `try`, so an
    ambiguous id raises `KeyError` rather than answering the default -
    28 (cluster, attribute) pairs in the installed zha-quirks 2.2.2 are
    like that, `UbisysLevelControl` 0x0000 among them. `_endpoint_facts`
    feeds `_facts` feeds `_snapshot` feeds `snapshots()`, and `cli._run`
    calls `attach()` unguarded, so one Ubisys dimmer in the network used to
    mean the bridge did not start at all - before uvicorn, with no HTTP
    surface to report it through.

    Fault to prove it: pass the bare id to `cluster.get` in
    `_endpoint_facts`, as the code did. `snapshots()` then raises
    `KeyError`, and so does every delivery for that device.

    What is asserted is BOTH halves: the call survives, and the endpoint's
    other attributes still come out. A source that swallowed the whole
    endpoint would also "not crash"."""
    lamp = colour_lamp()
    level = lamp.endpoints[1].in_clusters[0x0008]
    # `UbisysLevelControl`: `current_level` (standard, cached as 254 by
    # `colour_lamp`) and `minimum_on_level` (manufacturer-specific, code
    # 0x1092) share id 0x0000, and the manufacturer-specific one is the
    # definition `attributes` keeps.
    level.shadowed[0x0000] = 25
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()

    snapshots = await harness.source.snapshots()

    assert len(snapshots) == 1
    # THE RESIDUAL, measured rather than assumed: the path is exported, and
    # what it carries is the surviving definition's value - the dimmer's
    # minimum-on level (25) where its current level (254) belongs. This is
    # the cost the fix accepts, and it is written down so that nobody reads
    # "does not crash" as "is correct".
    assert snapshots[0].attributes["1/8/0"] == 25
    # And nothing else on the device is disturbed.
    assert snapshots[0].attributes["1/6/0"] is True
    assert snapshots[0].attributes["1/768/7"] == 370
    assert snapshots[0].attributes["1/768/16394"] == 0x1F

    # The same crash reached `_deliver` through the dispatch loop, where it
    # was swallowed - a permanently silent device plus a log line.
    await harness.source.subscribe(_lamp_resolver(), harness.handler)
    lamp.endpoints[1].in_clusters[0x0006].report(0x0000, False)
    await _settle(harness.source)
    assert (7, "1/6/0", False) in harness.handler.attributes


async def test_removal_releases_the_removed_devices_listeners(build) -> None:
    """The third of the brief's three re-subscription points - "after every
    reconnect, reinterview and REMOVAL".

    Two things are wrong without it. The removed device's clusters keep
    callbacks that enqueue an address nothing will ever resolve again, on
    objects zigpy has dropped; and a device that is removed and then rejoins
    - a factory reset, the ordinary repair - comes back as a NEW object
    whose listeners are never bound, because the source still believes it is
    listening to that address.

    Fault to prove it: drop the `_release_device_listeners(address)` line
    from `_forget`. `listener_count` below stays at its bound value, and the
    rejoined lamp's report reaches nobody.

    `listener_count` is counted rather than inferred: four events on each of
    three clusters is twelve, and "some listeners" would not distinguish a
    release from a partial one."""
    lamp = colour_lamp()
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()
    await harness.source.subscribe(_lamp_resolver(), harness.handler)
    await _settle(harness.source)

    bound = [cluster.listener_count for cluster in lamp.clusters()]
    assert bound == [4, 4, 4], bound

    await harness.source.remove(LAMP_IEEE)
    await _settle(harness.source)

    assert [cluster.listener_count for cluster in lamp.clusters()] == [0, 0, 0]
    assert LAMP_IEEE not in harness.source._listening

    # And the same lamp rejoining - a new object, the same IEEE - is bound
    # again rather than skipped as "already listening".
    rejoined = colour_lamp()
    harness.app.fire_device_initialized(rejoined)
    await _settle(harness.source)
    harness.handler.attributes.clear()

    rejoined.endpoints[1].in_clusters[0x0008].report(0x0000, 77)
    await _settle(harness.source)
    assert (7, "1/8/0", 77) in harness.handler.attributes


async def test_a_colour_cluster_that_does_not_declare_the_capabilities_is_skipped(
    build,
) -> None:
    """The second `Cluster.get` in this file, and the second way it raises.

    `find_attribute` raises `KeyError` for an id the cluster class does not
    declare AT ALL, not only for an ambiguous one - so the pre-read's
    `cluster.get(0x400A)` had the same crash in it as `_endpoint_facts`, on
    the path that runs before every snapshot. No quirk ships a `Color`
    subclass without 0x400A today (all 13 were checked), which is why this
    is a guard rather than a bug report; it must still not be the thing that
    stops `snapshots()`.

    Fault to prove it: look the attribute up with `cluster.get(0x400A)`
    again instead of through `cluster.attributes.get(...)`."""
    lamp = colour_lamp(colour_capabilities=None)
    colour = lamp.endpoints[1].in_clusters[0x0300]
    # A Color cluster whose class declares no ColorCapabilities at all.
    del colour.attributes[0x400A]
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()

    snapshots = await harness.source.snapshots()

    assert len(snapshots) == 1
    # No read was attempted for an attribute the cluster does not have...
    assert colour.reads == []
    assert "1/768/16394" not in snapshots[0].attributes
    # ...and the rest of the lamp still came through.
    assert snapshots[0].attributes["1/768/7"] == 370


async def test_disconnect_wakes_a_supervisor_waiting_on_the_link(build) -> None:
    """`wait_for_link_loss()` must return when the caller itself takes the
    radio away, not only when the radio dies.

    Task 11 clears the radio setting by calling `disconnect()`; a supervisor
    parked in `wait_for_link_loss()` holds nothing but that event, so a
    `disconnect()` that cleared `_connected` without setting it would leave
    the supervisor waiting for a link that is already gone and can never be
    lost again.

    Fault to prove it: remove `self._link_lost.set()` from `disconnect()`.
    The `wait_for` below then times out."""
    harness = build(FakeApplication(devices=[colour_lamp()]))
    await harness.source.connect()

    waiting = asyncio.ensure_future(harness.source.wait_for_link_loss())
    await asyncio.sleep(0)
    assert not waiting.done(), "the supervisor must still be waiting while the link holds"

    await harness.source.disconnect()

    await asyncio.wait_for(waiting, 1)


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


async def test_the_reason_a_command_failed_is_in_the_language_of_the_sentence_around_it(
    build,
) -> None:
    """`api.errors.device_unreachable` wraps a reason, and three of the
    reasons this source gives were English literals - so a German user read
    "Geraet nicht erreichbar: unknown Zigbee device ...". Each reason is a
    string of its own now, resolved when the command fails.

    Fault to prove it: put back any of the three English literals."""
    lamp = colour_lamp()
    lamp.endpoints[1].in_clusters[0x0006].command_status = 0x86
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()
    i18n.set_language("de")

    def call(address: str, *, cluster_id: int = 6, command_id: int = 1) -> DeviceCall:
        return DeviceCall(
            technology="zigbee",
            address=address,
            endpoint=1,
            cluster_id=cluster_id,
            command_id=command_id,
        )

    reasons = []
    for failing in (
        call("00:00:00:00:00:00:00:01"),
        call(LAMP_IEEE, cluster_id=0x0102),
        call(LAMP_IEEE),
    ):
        with pytest.raises(DeviceUnreachableError) as caught:
            await harness.source.send(failing)
        reasons.append(str(caught.value))

    assert reasons == [
        i18n.t(
            "api.errors.device_unreachable",
            exc=i18n.t(
                "api.errors.zigbee_device_not_in_network", address="00:00:00:00:00:00:00:01"
            ),
        ),
        i18n.t(
            "api.errors.device_unreachable",
            exc=i18n.t(
                "api.errors.zigbee_no_such_command",
                endpoint=1,
                address=LAMP_IEEE,
                command_id=1,
                cluster_id=0x0102,
            ),
        ),
        i18n.t(
            "api.errors.device_unreachable",
            exc=i18n.t("api.errors.zigbee_command_refused", command_id=1, status=0x86),
        ),
    ]
    for english in ("unknown Zigbee device", "has no command", "answered command"):
        assert all(english not in reason for reason in reasons)
    # Read back as text too: the comparison above is against the same keys
    # the source uses, and would pass for a key that resolves to the wrong
    # sentence altogether.
    assert "unbekanntes Zigbee-Gerät 00:00:00:00:00:00:00:01" in reasons[0]
    assert "hat keinen Befehl 1 im Cluster 258" in reasons[1]
    assert "mit Status 134 beantwortet" in reasons[2]
    i18n.set_language("en")
    await harness.source.disconnect()


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


async def test_removing_a_device_zigpy_does_not_know_is_already_done(build) -> None:
    """A stored Zigbee device whose radio came back with a new or reset
    database, or another coordinator: zigpy has never heard of it, and the
    removal answered "unknown Zigbee device" - a 502, forever, for a tile
    nothing could delete. zigpy's own `ControllerApplication.remove()`
    returns at once for an unknown IEEE; the source says the same.

    With the link down it still fails: nothing can be said then about what
    the database knows.

    Fault to prove it: look the device up with `_require_device` again."""
    harness = build(FakeApplication(devices=[colour_lamp()]))
    await harness.source.connect()
    stranger = "00:12:4b:00:00:00:99:99"

    await asyncio.wait_for(harness.source.remove(stranger), 1)

    assert harness.app.removed == [], "zigpy was asked about a device it does not have"
    assert [row.ieee for row in harness.source.pairing_rows()] != [stranger]

    harness.app.fire_connection_lost()
    await _settle(harness.source)
    with pytest.raises(DeviceUnreachableError):
        await harness.source.remove(stranger)


async def test_removing_a_device_zigpy_no_longer_knows_still_clears_its_own_state(
    build,
) -> None:
    """N-3 (verification 2026-09-13): the test above proves `remove()` on an
    address zigpy never had returns cleanly, but not that `_forget(address)`
    - the bridge's OWN cleanup, unrelated to whatever zigpy's database
    knows - still runs on that branch. It does, in one call outside the
    `if device is not None:` above it, but nothing pinned that: a stale
    pairing row, delivered-once mark, polling entry or cluster listener for
    an address zigpy has dropped could survive a removal and nothing here
    would have noticed.

    The lamp joins for real first - a genuine pairing row, with a listener
    bound on every cluster - and THEN zigpy's own database drops it (a
    reset, or another coordinator; `app.devices` is the only place that
    happens, `_pairing` is the bridge's own and does not hear about it).
    Removing it through the bridge must still clear the bridge's half.

    Fault to prove it: call `self._forget(address)` only inside the
    `if device is not None:` branch, so the `else` (unknown to zigpy) skips
    it."""
    lamp = colour_lamp()
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()
    await harness.source.subscribe(_lamp_resolver(), harness.handler)
    harness.app.fire_device_initialized(lamp)
    await _settle(harness.source)
    assert [row.ieee for row in harness.source.pairing_rows()] == [LAMP_IEEE]
    assert [cluster.listener_count for cluster in lamp.clusters()] == [4, 4, 4]

    # zigpy's own database has dropped it; the bridge's pairing row does not
    # know that yet.
    del harness.app.devices[lamp.ieee]

    await asyncio.wait_for(harness.source.remove(LAMP_IEEE), 1)

    assert harness.app.removed == [], "zigpy was asked about a device it no longer has"
    assert harness.source.pairing_rows() == []
    assert [cluster.listener_count for cluster in lamp.clusters()] == [0, 0, 0]
    assert LAMP_IEEE not in harness.source._listening


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


async def test_a_device_that_arrives_as_the_window_closes_is_still_a_join(build) -> None:
    """A device announces itself through its parent, so an announcement sent
    inside the window can reach the coordinator after it has run out. The
    last device to squeeze into a 254 s window is exactly the one the user
    is standing in front of, and reporting it as "was already on the
    network" is both false and backwards: `discovered` exists to stop the
    bridge claiming a join it did not see, not to deny one it did.

    Fault to prove it: decide `discovered` from `_window_is_open()` - the
    open window and nothing else. The device below, which arrives one
    second after a window that was open, is then labelled as having been
    there all along.

    The other half of the grace is measured too: a device that turns up
    long after the window is a discovery again, or the grace would just be
    a window that never closes."""
    harness = build(FakeApplication())
    await harness.source.connect()

    await harness.source.permit(1)
    # The window is now over - `permit(1)` put its end one second out and
    # this moves the clock past it without sleeping. The grace is not.
    harness.source._permit_until = datetime.now(UTC) - timedelta(seconds=1)

    late = colour_lamp("00:12:4b:00:1c:00:00:03")
    harness.app.fire_device_joined(late)
    await _settle(harness.source)
    rows = {row.ieee: row for row in harness.source.pairing_rows()}
    assert rows[late.ieee].discovered is False, "a device let in by the window, called a stranger"
    # And the window itself reads as shut, which is what the tab counts.
    assert harness.source.permit_until() is None

    # Past the grace, the same arrival is a device that was already there.
    harness.source._permit_until = datetime.now(UTC) - timedelta(
        seconds=source_module.PERMIT_JOIN_GRACE_SECONDS + 1
    )
    stranger = colour_lamp("00:12:4b:00:1c:00:00:04")
    harness.app.fire_device_joined(stranger)
    await _settle(harness.source)
    rows = {row.ieee: row for row in harness.source.pairing_rows()}
    assert rows[stranger.ieee].discovered is True


async def test_an_open_window_does_not_outlive_the_radio_that_was_holding_it(build) -> None:
    """A permit lives in the COORDINATOR, not in this object. A stick that
    was unplugged or wedged is not holding a network open for anybody, so a
    tab still counting down four minutes is counting down to a fiction -
    and the next device to appear after the reconnect would be reported as
    a join nobody permitted.

    This is the shape of bug this branch has produced four times: something
    disappears while a window, a sweep or a loop is still running, and the
    state left behind describes a world that no longer exists.

    Fault to prove it: leave `_permit_until` alone in
    `_handle_connection_lost` and in `disconnect()`."""
    harness = build(FakeApplication(), FakeApplication())
    await harness.source.connect()
    await harness.source.permit(254)
    assert harness.source.permit_until() is not None

    harness.app.fire_connection_lost()
    await _settle(harness.source)
    assert harness.source.permit_until() is None
    # And a device turning up now - the radio came back, the window did not
    # - is not credited to a window nobody has open.
    await harness.source.connect()
    late = colour_lamp("00:12:4b:00:1c:00:00:05")
    harness.source._app.fire_device_joined(late)
    await _settle(harness.source)
    rows = {row.ieee: row for row in harness.source.pairing_rows()}
    assert rows[late.ieee].discovered is True

    # The deliberate shutdown half: the same guarantee, and the one that
    # matters for a radio SWAP, where this source is torn down and another
    # takes its place.
    await harness.source.permit(254)
    assert harness.source.permit_until() is not None
    await harness.source.disconnect()
    assert harness.source.permit_until() is None


async def test_two_overlapping_permits_leave_the_window_agreeing_with_the_radio(build) -> None:
    """ "Keep open longer" pressed while a Stop is still in flight - two
    tabs, a phone and a laptop, or one impatient double-click.
    `app.permit` is a ZDO broadcast plus a call into the NCP, so the two
    requests can finish in the opposite order to the one they were made in.

    Whichever call the RADIO finished last is what the network is actually
    doing, and the end time the tab counts down to has to agree with it.
    Without the lock the loser writes its own end time after the winner's,
    and the tab counts down four minutes on a network that is shut - the
    single most dangerous direction for this particular lie, because the
    user walks away believing pairing is still possible while a stranger's
    device cannot in fact get in, or, with the ends reversed, believing the
    network is closed while it is open.

    Fault to prove it: drop `self._permit_lock` from `permit()`."""
    harness = build(FakeApplication())
    await harness.source.connect()
    # The long window's call takes two event-loop rounds to reach the
    # radio; the Stop takes none. Serialised, the Stop still lands last.
    harness.app.permit_delays = {254: 2}

    await asyncio.gather(harness.source.permit(254), harness.source.permit(0))

    assert harness.app.permits == [(254, None), (0, None)]
    assert harness.source.permit_until() is None, "the tab would count down on a shut network"


async def test_a_permit_the_radio_answers_after_the_link_was_lost_opens_no_window(build) -> None:
    """The link is lost while `app.permit` is still in flight, and the call
    then returns normally anyway.

    Reachable, not theoretical: bellows resolves the command's future when
    the NCP's answer frame arrives, and the coroutine waiting on it resumes
    one loop iteration LATER - so a `connection_lost` handled in between
    runs first. It closes the window (`_close_window`), and a `permit()`
    that wrote its end time afterwards reopened it on a dead radio: the tab
    counted down four minutes, and the first device to appear after the
    reconnect was credited to a window no coordinator was holding.

    Fault to prove it: write the end time without looking at the link again
    after the await."""
    harness = build(FakeApplication(), FakeApplication())
    await harness.source.connect()
    reached = asyncio.Event()
    answer = asyncio.Event()
    radio = harness.app
    original = radio.permit

    async def held_permit(time_s: int = 60, node: Any = None) -> None:
        reached.set()
        await answer.wait()
        await original(time_s=time_s, node=node)

    radio.permit = held_permit  # type: ignore[method-assign]
    opening = asyncio.ensure_future(harness.source.permit(254))
    await asyncio.wait_for(reached.wait(), timeout=2)

    radio.fire_connection_lost()
    assert not opening.done(), "the link was lost after the permit finished - nothing interleaved"
    answer.set()

    with pytest.raises(DeviceUnreachableError):
        await asyncio.wait_for(opening, timeout=2)
    assert radio.permits == [(254, None)], "the radio call itself must have completed"
    assert harness.source.permit_until() is None, "a countdown on a dead radio"
    # And the grace a real window would leave behind is not there either.
    await harness.source.connect()
    late = colour_lamp("00:12:4b:00:1c:00:00:06")
    harness.source._app.fire_device_joined(late)
    await _settle(harness.source)
    rows = {row.ieee: row for row in harness.source.pairing_rows()}
    assert rows[late.ieee].discovered is True
    await harness.source.disconnect()


async def test_a_row_is_keyed_by_ieee_and_survives_a_reinterview(build) -> None:
    """A rejoining device gets a new short address, and a reinterview
    replaces the device OBJECT entirely - new clusters, same IEEE.

    Fault to prove it: key the rows by NWK. The device below then appears
    twice, once under each address it has had, and the user removes one of
    the two halves of a device that is perfectly fine."""
    lamp = colour_lamp()
    harness = build(FakeApplication(devices=[lamp]))
    await harness.source.connect()
    await harness.source.subscribe(_lamp_resolver(), harness.handler)

    harness.app.fire_device_joined(lamp)
    await _settle(harness.source)
    assert [row.ieee for row in harness.source.pairing_rows()] == [LAMP_IEEE]

    # The same device, a new short address and a brand-new object - which
    # is exactly what zigpy's `_device_reinterviewed` builds.
    replacement = colour_lamp()
    replacement.nwk = 0x4321
    harness.app.fire_device_reinterviewed(replacement)
    await _settle(harness.source)

    assert [row.ieee for row in harness.source.pairing_rows()] == [LAMP_IEEE]
    assert harness.source.pairing_rows()[0].state == "ready"


async def test_an_interview_that_fails_gets_a_row_that_says_so(build) -> None:
    """ZHA has NO failure state at all - a hung interview there sits on
    "Starting interview" forever (HA core issues 124114, 99497, 123136,
    162426), and that is the single thing this tab exists to do better.

    `device_init_failure` is dispatched to the APPLICATION's listeners even
    though `zigpy.device.Device.initialize` is what emits it, so the
    handler belongs on `_ApplicationListener`. A handler placed on the
    device - where this file's own note used to say it belonged - would
    never be called once, and zigpy swallows every listener exception, so
    nothing anywhere would say so.

    Fault to prove it: drop the `device_init_failure` handler."""
    harness = build(FakeApplication())
    await harness.source.connect()

    await harness.source.permit(254)
    sensor = contact_sensor("00:15:8d:00:02:00:00:09")
    harness.app.fire_device_joined(sensor)
    await _settle(harness.source)
    assert harness.source.pairing_rows()[0].state == "joined"

    harness.app.fire_device_init_failure(sensor)
    await _settle(harness.source)
    row = harness.source.pairing_rows()[0]
    assert row.state == "failed"
    # The row it failed on is the row the user opened the window for, not a
    # device relabelled as having been there all along.
    assert row.discovered is False


async def test_a_retry_interviews_the_device_again_and_restarts_its_row(build) -> None:
    """Retry is the other half of having a failure state at all: a row that
    can fail and cannot be retried is a dead end, and the usual cause of a
    failed interview - a battery device that was asleep for it - is fixed
    by asking again while somebody presses its button.

    `schedule_initialize()` is zigpy's own entry point for that, and it
    starts the interview as a TASK, so nothing here waits for it.

    Fault to prove it: have `retry_interview` only rewrite the row. The
    device is then never asked anything again, and the row goes back to
    "interviewing" forever."""
    harness = build(FakeApplication())
    await harness.source.connect()
    sensor = contact_sensor("00:15:8d:00:02:00:00:0a")
    harness.app.fire_device_joined(sensor)
    harness.app.fire_device_init_failure(sensor)
    await _settle(harness.source)
    assert harness.source.pairing_rows()[0].state == "failed"

    harness.source.retry_interview(sensor.ieee)

    assert sensor.initializations == 1, "the device was never actually asked again"
    assert harness.source.pairing_rows()[0].state == "interviewing"

    # A device the radio does not know cannot be retried, and says so in
    # the one vocabulary every source shares.
    with pytest.raises(DeviceUnreachableError):
        harness.source.retry_interview("00:15:8d:00:02:00:00:ff")


async def test_configuring_addresses_covers_the_whole_configuration_pass(
    build, tmp_path, monkeypatch
) -> None:
    """Design 3.1's table: "configuring - `device_initialized`, our
    configure-on-join running - Setting it up". The row has been set to
    "ready" on `device_initialized` since the source was written, with
    `configure_device` only STARTING afterwards, so without this set a
    device that takes the better part of 30 s to bind a sleepy cluster
    reported "Ready to use" a whole configuration pass before it deserved
    to.

    Fault to prove it: never add to `self._configuring`. The set is then
    empty for the whole pass and the pairing route has nothing to overlay.

    The address leaves the set BEFORE the device is delivered, deliberately:
    a snapshot arriving while it still counted as configuring would have the
    pairing tab and the device's own first values disagree about whether it
    was ready."""
    store = Store(tmp_path / "loxmatter.sqlite")
    try:
        lamp = colour_lamp()
        harness = build(FakeApplication(devices=[lamp]), store=store)
        await harness.source.connect()
        await harness.source.subscribe(_lamp_resolver(), harness.handler)

        seen: list[frozenset[str]] = []
        original = source_module.configure_device

        async def watching_configure_device(device: Any, **kwargs: Any) -> Any:
            # Read from inside the pass, which is the only place the claim
            # "for as long as it runs" can actually be measured.
            seen.append(harness.source.configuring_addresses())
            return await original(device, **kwargs)

        monkeypatch.setattr(source_module, "configure_device", watching_configure_device)

        assert harness.source.configuring_addresses() == frozenset()
        harness.app.fire_device_initialized(lamp)
        # The configuration runs as a task the listener started; awaiting
        # the tasks themselves is a real barrier, where a handful of
        # `sleep(0)` rounds would go hollow the moment the routine grows an
        # await.
        await asyncio.gather(*tuple(harness.source._tasks))
        await _settle(harness.source)

        assert seen == [frozenset({LAMP_IEEE})]
        # And it is empty again afterwards, so a finished device does not
        # sit on "Setting it up" forever.
        assert harness.source.configuring_addresses() == frozenset()
        assert harness.source.pairing_rows()[0].state == "ready"
        await harness.source.disconnect()
    finally:
        store.close()


async def test_a_configuration_pass_that_raises_does_not_leave_the_row_setting_up(
    build, tmp_path, monkeypatch
) -> None:
    """A configure-on-join pass that raises instead of returning - a quirk's
    cluster throwing something `configure_device` does not expect.

    The address has to leave the configuring set on that path too. If it
    were cleared only after a pass that returned, the pairing tab would show
    the device as "Setting it up" for as long as the bridge runs, and the
    device would still be delivered underneath that label.

    Fault to prove it: clear the set in the `try`'s `else` branch instead
    of in `finally`."""
    store = Store(tmp_path / "loxmatter.sqlite")
    try:
        lamp = colour_lamp()
        harness = build(FakeApplication(devices=[lamp]), store=store)
        await harness.source.connect()
        await harness.source.subscribe(_lamp_resolver(), harness.handler)
        seen: list[frozenset[str]] = []

        async def raising_configure_device(device: Any, **kwargs: Any) -> Any:
            seen.append(harness.source.configuring_addresses())
            raise KeyError("a cluster the quirk does not declare")

        monkeypatch.setattr(source_module, "configure_device", raising_configure_device)

        harness.app.fire_device_initialized(lamp)
        await asyncio.gather(*tuple(harness.source._tasks))
        await _settle(harness.source)

        # It really ran, and really counted as configuring while it did -
        # otherwise an empty set afterwards would prove nothing.
        assert seen == [frozenset({LAMP_IEEE})]
        assert harness.source.configuring_addresses() == frozenset()
        await harness.source.disconnect()
    finally:
        store.close()


# ------------------------------------------------- the two background loops --
#
# Both are started by `subscribe()` and stopped by `disconnect()` and by the
# next `subscribe()`. They are measured through `asyncio.all_tasks()` rather
# than through the attribute that holds them: a task that was never created
# and a task that was created and leaked look identical from the attribute,
# and the leak is the failure worth catching.


def _live_tasks(qualname: str) -> list[asyncio.Task[Any]]:
    """Every task of this event loop that is currently running `qualname`.

    `asyncio.all_tasks()` lists PENDING tasks only, so a loop that
    `stop()` has cancelled and awaited is gone from this list - which is
    exactly what makes "stopped" and "still running" tell each other
    apart."""
    return [
        task
        for task in asyncio.all_tasks()
        if getattr(task.get_coro(), "__qualname__", "") == qualname
    ]


_SWEEP_TASK = "AvailabilityChecker._run"
_POLLING_TASK = "PollingLoop._run"


async def test_the_availability_sweep_starts_when_the_source_is_subscribed(build) -> None:
    """`AvailabilityChecker` has been rebuilt fresh inside
    `ZigbeeSource.subscribe()` since it was written - but never started, by
    that task's own deliberate scope. Without this, `CHECK_INTERVAL_SECONDS`
    never elapses at all: a mains device that stopped reporting hours ago is
    never pinged and never written off, and the web UI reports it reachable
    forever, even though `mark_all_offline()` still fires correctly the
    moment the coordinator itself is lost.

    Fault to prove it: construct the checker in `subscribe()` without
    calling `start()`. The periodic sweep then never runs, on any
    installation, ever."""
    harness = build(FakeApplication(devices=[colour_lamp()]))
    await harness.source.connect()
    assert _live_tasks(_SWEEP_TASK) == [], "a sweep was running before anything subscribed"

    await harness.source.subscribe(_lamp_resolver(), harness.handler)

    assert len(_live_tasks(_SWEEP_TASK)) == 1
    await harness.source.disconnect()


async def test_a_reconnect_stops_the_previous_sweep_before_starting_a_new_one(build) -> None:
    """`attach()` calls `subscribe()` again on every reconnect (its
    documented contract). Ten reconnects over a flaky USB cable must not
    leave ten sweep tasks running, nine of them reading an application
    object `disconnect()` has already thrown away.

    Fault to prove it: build and start the new checker without first
    awaiting `stop()` on the old one. `asyncio.all_tasks()` grows by one on
    every single `subscribe()` call instead of staying flat."""
    harness = build(FakeApplication(devices=[colour_lamp()]))
    await harness.source.connect()

    for _ in range(10):
        await harness.source.subscribe(_lamp_resolver(), harness.handler)
        # One turn of the loop, so a cancelled task really finishes rather
        # than merely being marked - a cancellation that nobody awaited
        # would otherwise still be counted as pending here.
        await asyncio.sleep(0)

    assert len(_live_tasks(_SWEEP_TASK)) == 1
    assert len(_live_tasks(_POLLING_TASK)) == 1
    await harness.source.disconnect()


async def test_every_known_device_is_reported_offline_when_the_source_disconnects(build) -> None:
    """`disconnect()` is how a radio is given up on purpose - a change to a
    different stick, "No Zigbee stick" on the card, and the bridge's own
    shutdown. It stopped the availability sweep and never said what that
    meant for the devices: every Zigbee tile kept "online" and its last
    value, `d<id>_online` stayed true in Loxone and was re-sent by every
    resync, and a motion sensor whose radio was gone read "no motion,
    online" until the bridge restarted. Only a LOST link marked them.

    Every device the store knows is reported `False`, once; a device the
    store does not know is not reported at all; and one handler failure
    neither stops the others nor stops the disconnect.

    Fault to prove it: drop the marking from `disconnect()` (`online` stays
    empty), or run it after the application is released (no devices left to
    mark)."""
    lamp, sensor = colour_lamp(), contact_sensor()
    stranger = colour_lamp("00:12:4b:00:99:99:99:99")
    ids = {LAMP_IEEE: 7, SENSOR_IEEE: 8}
    harness = build(FakeApplication(devices=[lamp, sensor, stranger]))
    await harness.source.connect()
    await harness.source.subscribe(ids.get, harness.handler)
    await _settle(harness.source)
    harness.handler.fail_next = 1

    await harness.source.disconnect()

    # The lamp comes first in zigpy's catalogue and its report raised; the
    # sensor after it was still told.
    assert harness.handler.online == [(8, False)]
    assert not harness.source.connected
    assert harness.app.shutdown_calls == [True]

    healthy = build(FakeApplication(devices=[colour_lamp(), contact_sensor()]))
    await healthy.source.connect()
    await healthy.source.subscribe(ids.get, healthy.handler)
    await _settle(healthy.source)

    await healthy.source.disconnect()

    assert sorted(healthy.handler.online) == [(7, False), (8, False)]


async def test_the_availability_sweep_stops_on_disconnect(build) -> None:
    """A checker whose sweep task keeps running after `disconnect()` reads
    `_devices()` against an application that no longer exists (harmless - it
    returns `[]`), but the task itself is never cancelled: it leaks for the
    rest of the process, one more each time the source reconnects or a radio
    is swapped out from under it.

    Fault to prove it: drop the `_stop_availability_checker()` call from
    `disconnect()`. `asyncio.all_tasks()` then keeps one dangling sweep task
    per reconnect."""
    harness = build(FakeApplication(devices=[colour_lamp()]))
    await harness.source.connect()
    await harness.source.subscribe(_lamp_resolver(), harness.handler)
    assert len(_live_tasks(_SWEEP_TASK)) == 1

    await harness.source.disconnect()

    assert _live_tasks(_SWEEP_TASK) == []


async def test_the_polling_loop_stops_on_disconnect(build) -> None:
    """The same leak `test_the_availability_sweep_stops_on_disconnect`
    catches, for the second background loop `subscribe()` starts: a
    `PollingLoop` whose task survives `disconnect()` keeps sleeping and
    waking against an application object `disconnect()` has already thrown
    away, one more leaked task per reconnect.

    Fault to prove it: do not call `_stop_polling_loop()` from
    `disconnect()`."""
    harness = build(FakeApplication(devices=[colour_lamp()]))
    await harness.source.connect()
    await harness.source.subscribe(_lamp_resolver(), harness.handler)
    assert len(_live_tasks(_POLLING_TASK)) == 1

    await harness.source.disconnect()

    assert _live_tasks(_POLLING_TASK) == []


async def test_a_device_marked_offline_by_link_loss_does_not_flip_back_on_the_next_sweep(
    build,
) -> None:
    """THE regression a review measured in the committed checker, not merely
    a missing feature: `_sweep()`/`_check_one()` computing availability from
    `is_available(device)` alone reads only `device.last_seen` - and a
    device heard from ten seconds before the coordinator died still passes
    that check for the next two (mains) or six (battery) hours.
    `ZigbeeSource._facts()` gets this right (`self._connected and
    is_available(device)`); the sweep has to as well, now that this is the
    task that starts it.

    Fault to prove it: revert `_sweep()`/`_check_one()` to decide
    availability from `is_available(device)` alone, without checking
    `self._source.connected`. The device is then reported online again one
    sweep after `mark_all_offline()` reported it offline."""
    harness = build(FakeApplication(devices=[colour_lamp()]))
    await harness.source.connect()
    # `colour_lamp()` defaults `last_seen` to "now" (`fakes.py`'s own
    # `_LAST_SEEN_UNSET` sentinel) - heard from moments ago, exactly the
    # shape that made the measured regression invisible to `is_available`
    # alone.
    await harness.source.subscribe(_lamp_resolver(device_id=1), harness.handler)
    harness.app.fire_connection_lost()
    await _settle(harness.source)
    assert harness.handler.online == [(1, False)]

    # The next scheduled tick, invoked directly rather than waited for -
    # `AvailabilityChecker._sweep()` is what a real 30 s timer would call.
    assert harness.source._availability_checker is not None
    await harness.source._availability_checker._sweep()

    assert harness.handler.online == [(1, False)]
    await harness.source.disconnect()


# ----------------------------------------------- picking up unfinished work --


def _thermometer(ieee: str) -> FakeDevice:
    """A battery temperature sensor with one reportable cluster.

    Battery-powered on purpose: it is the device class whose configuration
    is interrupted in the first place, because it is asleep when the bridge
    tries to talk to it."""
    return FakeDevice(
        ieee,
        manufacturer="IKEA of Sweden",
        model="TRADFRI temperature",
        node_desc=FakeNodeDescriptor(is_mains_powered=False),
        endpoints=[
            FakeEndpoint(
                1,
                profile_id=0x0104,
                device_type=0x0302,
                in_clusters=[
                    FakeCluster(
                        TEMPERATURE_CLUSTER,
                        declared=[0x0000],
                        cached={0x0000: 2100},
                        readable={0x0000: 2150},
                    )
                ],
            )
        ],
    )


async def test_a_device_with_pending_configuration_is_watched_again_after_a_restart(
    build, tmp_path
) -> None:
    """`ZigbeePendingStore.addresses_with_pending()` exists for exactly this
    moment - its own docstring says "for a bridge that has just started and
    wants to know which devices to watch for" - and nothing called it before
    this wiring. A device whose configuration was interrupted by a bridge
    restart has a correct row in `zigbee_pending_config` and, without this,
    no watcher: `device_last_seen_updated` and `checkin` on it are never
    noticed, and the row sits there, truthful and useless, until the device
    is factory-reset and re-paired.

    Fault to prove it: skip `_resume_pending_devices()` in `subscribe()`. The
    device below then never has `retry_pending` called on it, no matter how
    many times it reports in afterwards - the retry itself is proven by
    `test_a_deferred_cluster_is_retried_when_the_device_is_next_heard_from`
    in `test_configure.py`; this proves that after a restart anything ever
    asks for it."""
    address = "00:11:22:33:44:55:66:77"
    store = Store(tmp_path / "loxmatter.sqlite")
    try:
        # The row a bridge that was killed mid-configuration leaves behind.
        store.zigbee_pending.mark_pending(address, 1, TEMPERATURE_CLUSTER)
        device = _thermometer(address)
        harness = build(FakeApplication(devices=[device]), store=store)

        await harness.source.connect()
        await harness.source.subscribe(lambda _address: 1, harness.handler)
        # Everything the restart itself did, forgotten: what follows must be
        # caused by the device reporting in, not by the subscribe.
        device.journal.clear()
        assert device.endpoints[1].in_clusters[TEMPERATURE_CLUSTER].binds == 0

        device.heard_from()
        await _settle(harness.source)
        # `retry_pending` runs as a task the listener started; give it room.
        for _ in range(10):
            await asyncio.sleep(0)

        assert device.endpoints[1].in_clusters[TEMPERATURE_CLUSTER].binds == 1
        assert store.zigbee_pending.pending_for(address) == []
        await harness.source.disconnect()
    finally:
        store.close()
