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

"""Configure-on-join against a fake radio (design 2026-09-12, section 6).

**This is the module that decides whether a paired sensor ever says
anything.** zigpy binds nothing and configures no reporting, so every
protection here is the difference between a device that reports and one
that pairs, goes green in the web UI, and is silent forever.

Three of them are worth naming before the tests, because all three fail in
the same invisible way - the device looks perfectly healthy:

- an IAS alarm arrives as CLIENT COMMAND 0, never as an attribute report;
- many devices never ask to be enrolled, so the `enroll_response` has to go
  out unsolicited;
- `IasZone` is bound and deliberately never configured for reporting.

Nothing here imports zigpy. Every name the routine depends on is checked
against the installed library by `tests/zigbee/test_zigpy_names.py`, and
`fakes.py` mimics zigpy's shapes rather than something convenient - the
reporting call is keyed by the attribute DEFINITION and raises for a bare
id, `write_attributes` raises for an attribute the cluster does not declare,
and `read_attributes(allow_cache=True)` answers from the cache and skips the
read, which is the only reason the uncached-read test can measure
anything."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fakes import (
    COLOR_COMMANDS,
    LEVEL_COMMANDS,
    ON_OFF_COMMANDS,
    DeliveryError,
    FakeApplication,
    FakeCluster,
    FakeDevice,
    FakeEndpoint,
    FakeNoBindCluster,
    FakeNodeDescriptor,
    command_def,
)

from loxmatter.model.store import Store
from loxmatter.radios.fingerprints import Fingerprint
from loxmatter.zigbee.configure import (
    REPORTING,
    PollingLoop,
    PollingSchedule,
    configure_device,
    read_current_values,
)
from loxmatter.zigbee.source import ZigbeeSource

LAMP = "00:12:4b:00:1c:a1:b2:c3"
SENSOR = "00:15:8d:00:02:aa:bb:cc"

ON_OFF = 0x0006
LEVEL = 0x0008
COLOR = 0x0300
IDENTIFY = 0x0003
IAS_ZONE = 0x0500
POWER_CONFIGURATION = 0x0001
POLL_CONTROL = 0x0020

ZONE_TYPE = 0x0001
ZONE_STATUS = 0x0002
CIE_ADDRESS = 0x0010

IDENTIFY_COMMANDS = {0: command_def(0, "identify", "identify_time")}
IAS_ZONE_COMMANDS = {0: command_def(0, "enroll_response", "enroll_response_code", "zone_id")}


# ------------------------------------------------------------ the hardware --


@dataclass(frozen=True)
class ClientCommandArgs:
    """What zigpy hands a `cluster_command` listener: the deserialised
    command schema.

    A `zigpy.types.Struct`, which is indexable in field order AND carries
    the field names - `status_change_notification(zone_status=..., ...)`.
    Both halves are modelled because the routine reads `args[0]`, which is
    only correct as long as `zone_status` really is the first field (checked
    against the installed library in `test_zigpy_names.py`)."""

    fields: tuple[Any, ...]

    def __getitem__(self, index: int) -> Any:
        return self.fields[index]


def lamp(ieee: str = LAMP, **kwargs: Any) -> FakeDevice:
    """A mains-powered colour lamp: the device every reporting row but the
    battery ones applies to.

    Every attribute is BOTH cached with one value and readable with a
    different one, which is what makes "was the final read cached?"
    measurable at all - see `test_current_values_are_read_uncached_at_the_end`."""
    return FakeDevice(
        ieee,
        manufacturer="IKEA of Sweden",
        model="TRADFRI bulb",
        node_desc=FakeNodeDescriptor(is_mains_powered=True),
        endpoints=[
            FakeEndpoint(
                1,
                profile_id=0x0104,
                device_type=0x0102,
                in_clusters=[
                    FakeCluster(IDENTIFY, declared=[0x0000], commands=IDENTIFY_COMMANDS),
                    FakeCluster(
                        ON_OFF,
                        declared=[0x0000],
                        cached={0x0000: True},
                        readable={0x0000: False},
                        commands=ON_OFF_COMMANDS,
                    ),
                    FakeCluster(
                        LEVEL,
                        declared=[0x0000],
                        cached={0x0000: 254},
                        readable={0x0000: 120},
                        commands=LEVEL_COMMANDS,
                    ),
                    FakeCluster(
                        COLOR,
                        declared=[0x0003, 0x0004, 0x0007, 0x400A, 0x400B, 0x400C],
                        cached={0x0007: 370},
                        readable={
                            0x0003: 100,
                            0x0004: 200,
                            0x0007: 250,
                            0x400A: 0x1F,
                            0x400B: 153,
                            0x400C: 500,
                        },
                        commands=COLOR_COMMANDS,
                    ),
                ],
            )
        ],
        **kwargs,
    )


def sensor(
    ieee: str = SENSOR,
    *,
    no_bind_battery: bool = False,
    poll_control: bool = True,
    **kwargs: Any,
) -> FakeDevice:
    """A battery IAS contact sensor - the device this whole task exists for.

    `cie_addr` (0x0010) is declared because the real `IasZone` declares it
    and enrolment writes it; `FakeCluster.write_attributes` raises for an
    attribute the cluster does not declare, exactly as zigpy does, so a
    write to the wrong number could not pass unnoticed."""
    battery_class = FakeNoBindCluster if no_bind_battery else FakeCluster
    clusters: list[FakeCluster] = [
        FakeCluster(
            IAS_ZONE,
            declared=[0x0000, ZONE_TYPE, ZONE_STATUS, CIE_ADDRESS],
            cached={ZONE_TYPE: 0x0015, ZONE_STATUS: 0},
            readable={ZONE_TYPE: 0x0015, ZONE_STATUS: 0},
            commands=IAS_ZONE_COMMANDS,
        ),
        battery_class(
            POWER_CONFIGURATION,
            declared=[0x0020, 0x0021],
            cached={0x0021: 150},
            readable={0x0020: 30, 0x0021: 190},
        ),
    ]
    if poll_control:
        clusters.append(FakeCluster(POLL_CONTROL, declared=[0x0000, 0x0003]))
    return FakeDevice(
        ieee,
        manufacturer="LUMI",
        model="lumi.sensor_magnet",
        node_desc=FakeNodeDescriptor(is_mains_powered=False),
        endpoints=[FakeEndpoint(1, profile_id=0x0104, device_type=0x0402, in_clusters=clusters)],
        **kwargs,
    )


def on_a_network(device: FakeDevice) -> FakeApplication:
    """Gives a device the application it needs: the coordinator's IEEE for
    the CIE write, and `request_priority` for the whole pass."""
    return FakeApplication(devices=[device])


@pytest.fixture
def store(tmp_path: Path):
    opened = Store(tmp_path / "loxmatter.sqlite")
    yield opened
    opened.close()


def cluster_of(device: FakeDevice, cluster_id: int, endpoint_id: int = 1) -> FakeCluster:
    return device.endpoints[endpoint_id].in_clusters[cluster_id]


async def settle() -> None:
    """Lets every task a synchronous listener started run to completion."""
    for _ in range(10):
        await asyncio.sleep(0)


# ----------------------------------------------------------------- the pass --


async def test_the_quirk_hook_runs_before_anything_else(store) -> None:
    """`apply_custom_configuration()` is what casts the Tuya "spell" - a
    specific Basic read of [4, 0, 1, 5, 7, 0xFFFE] - and without it many
    Tuya devices never send anything at all. It has to run FIRST, because
    the binds that follow depend on the quirk's own cluster objects.

    Fault to prove it: run it after the binds."""
    device = lamp(custom_configuration=True, quirk_applied=True)
    on_a_network(device)

    outcome = await configure_device(device, store=store)

    assert device.journal, "the device was never asked to do anything"
    assert device.journal[0] == "apply_custom_configuration"
    # And not merely first among the reads: before every single bind, which
    # is the ordering the quirk's cluster overrides depend on.
    assert all(
        device.journal.index("apply_custom_configuration") < position
        for position, entry in enumerate(device.journal)
        if entry.startswith("bind:")
    )
    assert outcome.quirk_applied is True


async def test_a_device_that_asks_to_skip_configuration_is_left_alone(store) -> None:
    """79 quirks set `skip_configuration`, and they set it because binding
    or reporting actively breaks those devices.

    Fault to prove it: bind anyway."""
    device = lamp(custom_configuration=True, skip_configuration=True)
    on_a_network(device)

    outcome = await configure_device(device, store=store)

    assert outcome.configured == ()
    assert outcome.deferred == ()
    assert [cluster.binds for cluster in device.clusters()] == [0, 0, 0, 0]
    assert all(cluster.reporting == [] for cluster in device.clusters())
    assert store.zigbee_pending.pending_for(device.ieee) == []
    # The quirk hook is not part of what is skipped: it is what makes such a
    # device answer at all, and it costs nothing on a device that is fine.
    assert device.journal == ["apply_custom_configuration"]


async def test_binding_goes_through_the_quirks_cluster_object(store) -> None:
    """So overrides such as `TuyaNoBindPowerConfigurationCluster` apply. A
    bind sent to the raw cluster bypasses the very fix the quirk exists to
    provide.

    Fault to prove it: bind the raw endpoint cluster - `await
    FakeCluster.bind(cluster)` instead of `await cluster.bind()`, which is
    what reaching past the quirk to the base class looks like."""
    device = sensor(no_bind_battery=True)
    on_a_network(device)

    await configure_device(device, store=store)

    battery = cluster_of(device, POWER_CONFIGURATION)
    assert isinstance(battery, FakeNoBindCluster)
    assert battery.no_bind_calls == 1, "the quirk's own bind never ran"
    assert battery.binds == 0, "the bind reached the base class and was sent after all"
    # The quirk suppressed the bind and the reporting configuration still
    # went out, which is exactly what that quirk is for.
    assert battery.reporting


def test_the_reporting_table_is_zhas_field_proven_set() -> None:
    """These intervals are the only ones with field evidence behind them
    (research D.1, R1 section 7). Read from the module rather than retyped.

    Fault to prove it: change OnOff's max from 900 to 60 - the lamp then
    reports fifteen times as often, on a network shared with sleepy
    devices."""
    assert REPORTING[(0x0006, 0x0000)] == (0, 900, 1)
    assert REPORTING[(0x0008, 0x0000)] == (1, 900, 1)
    assert REPORTING[(0x0402, 0x0000)] == (30, 900, 50)
    assert REPORTING[(0x0405, 0x0000)] == (30, 900, 100)
    assert REPORTING[(0x0406, 0x0000)] == (0, 900, 1)
    assert REPORTING[(0x0400, 0x0000)] == (30, 900, 1)
    assert REPORTING[(0x0001, 0x0021)] == (3600, 10800, 1)


def test_the_ias_zone_cluster_is_bound_but_never_configured_for_reporting() -> None:
    """Alarms arrive as a CLIENT COMMAND, never as an attribute report, so a
    reporting configuration on `zone_status` is both useless and, on some
    devices, a failure that aborts the rest of the pass.

    Fault to prove it: add IasZone to the reporting table."""
    assert not [row for row in REPORTING if row[0] == IAS_ZONE]


async def test_the_ias_zone_cluster_is_bound_and_never_asked_to_report(store) -> None:
    """The same rule, measured on the routine rather than on the table: the
    bind happens, the reporting configuration never does."""
    device = sensor()
    on_a_network(device)

    await configure_device(device, store=store)

    ias = cluster_of(device, IAS_ZONE)
    assert ias.binds == 1
    assert ias.reporting == []


async def test_ias_enrolment_writes_the_cie_address_and_answers_unsolicited(store) -> None:
    """WITHOUT THIS A CONTACT, MOTION OR LEAK SENSOR NEVER REPORTS ANYTHING.
    zigpy defines the commands but does not enroll. Four steps: bind, read
    `zone_type`, write `cie_addr` with the coordinator's own IEEE, then send
    an UNSOLICITED `enroll_response(Success, zone_id=0)`.

    Fault to prove it: skip the unsolicited enroll_response. The sensor
    pairs, configures, goes green - and never fires."""
    device = sensor()
    application = on_a_network(device)

    await configure_device(device, store=store)

    ias = cluster_of(device, IAS_ZONE)
    assert ias.binds == 1
    assert [ZONE_TYPE] in ias.reads
    # The COORDINATOR's IEEE, not the device's - the address the sensor has
    # to send its alarms to.
    assert ias.writes == [{CIE_ADDRESS: application.state.node_info.ieee}]
    assert (0, {"enroll_response_code": 0, "zone_id": 0}) in ias.sent
    # Unsolicited: nobody asked, so there is no transaction number to quote.
    enrolments = [payload for command_id, payload in ias.sent if command_id == 0]
    assert enrolments and "tsn" not in enrolments[0]


async def test_a_status_change_notification_updates_the_zone_status_attribute(store) -> None:
    """This is HOW alarms arrive: as client command 0, not as a report. A
    design that only subscribes to reports sees a sensor that works
    perfectly and never triggers.

    Fault to prove it: handle only attribute reports."""
    device = sensor()
    on_a_network(device)
    await configure_device(device, store=store)

    ias = cluster_of(device, IAS_ZONE)
    definition = ias.attributes[ZONE_STATUS]
    assert ias.get(definition) == 0, "the door starts closed"

    heard: list[Any] = []
    ias.on_event("attribute_updated", heard.append)

    # The door opens. Nothing is reported; the sensor sends
    # `status_change_notification(zone_status=1, ...)` as a cluster command.
    ias.receive_command(0, ClientCommandArgs((1, 0, 0, 0)))

    assert ias.get(definition) == 1, "the alarm never reached the attribute"
    assert heard, "nothing downstream was told the door opened"


async def test_an_enroll_request_from_the_device_is_answered(store) -> None:
    """Client command 1, permanently handled - some sensors ask on every
    rejoin and stay unenrolled until they get an answer.

    Fault to prove it: ignore command 1."""
    device = sensor()
    on_a_network(device)
    await configure_device(device, store=store)

    ias = cluster_of(device, IAS_ZONE)
    before = len([command_id for command_id, _ in ias.sent if command_id == 0])

    ias.receive_command(1, ClientCommandArgs((0x0015, 0x1037)), tsn=42)
    await settle()

    answers = [payload for command_id, payload in ias.sent if command_id == 0]
    assert len(answers) == before + 1, "the enrolment request went unanswered"
    assert answers[-1] == {"enroll_response_code": 0, "zone_id": 0, "tsn": 42}


async def test_current_values_are_read_uncached_at_the_end(store) -> None:
    """This is what makes the `None` rule SATISFIABLE rather than merely
    stated: without a real read, every mapped path would be absent at join
    and the first row would only be created later, if at all.

    Fault to prove it: read with `allow_cache=True`. zigpy then answers from
    `_attr_cache` and SKIPS the request for everything it already holds - so
    a device that has rejoined hands back whatever it last said before it
    left, and the bridge publishes that as though it had just measured it.

    (The brief's own wording for this one - that the values "come back from
    zigpy's cache as `None`" - does not match the installed library:
    `Cluster.read_attributes` reads an UNCACHED attribute over the air even
    with `allow_cache=True`. The defect the flag really causes is a stale
    value, which is what is measured here.)"""
    device = lamp()
    on_a_network(device)
    on_off = cluster_of(device, ON_OFF)
    assert on_off.get(on_off.attributes[0x0000]) is True, "stale, from before the rejoin"

    await configure_device(device, store=store)

    assert on_off.get(on_off.attributes[0x0000]) is False, "the stale cached value survived"
    assert cluster_of(device, LEVEL).get(cluster_of(device, LEVEL).attributes[0x0000]) == 120
    assert all(allowed is False for allowed in on_off.cached_reads)
    # And at the END: after the last bind and the last reporting
    # configuration, so the values are the ones the device holds once it is
    # set up rather than the ones it held before.
    last_write = max(
        position
        for position, entry in enumerate(device.journal)
        if entry.startswith(("bind:", "report:"))
    )
    assert device.journal.index(f"read:{ON_OFF:#06x}") > last_write


async def test_fast_poll_mode_brackets_the_whole_routine_when_available(store) -> None:
    """`device.fast_poll_mode()` binds PollControl and writes
    `fast_poll_timeout`, keeping the device polling its parent for the whole
    configuration rather than only for the first command.

    Fault to prove it: wrap only the binds."""
    device = sensor()
    on_a_network(device)

    await configure_device(device, store=store)

    assert "fast_poll:start" in device.journal
    start = device.journal.index("fast_poll:start")
    stop = device.journal.index("fast_poll:stop")
    work = [
        position
        for position, entry in enumerate(device.journal)
        if not entry.startswith("fast_poll:") and entry != "apply_custom_configuration"
    ]
    assert work, "the device was never asked to do anything"
    assert start < min(work), "the binds ran before fast polling began"
    assert stop > max(work), "fast polling stopped before the routine was done"


async def test_the_whole_pass_runs_at_high_packet_priority(store) -> None:
    """A joining device is awake NOW and will not be again for hours, so
    everything this routine sends jumps the per-device queue, as ZHA does.

    Fault to prove it: drop the `request_priority` context manager. The
    reads and binds then queue behind whatever else the network is doing."""
    device = sensor()
    application = on_a_network(device)

    await configure_device(device, store=store)

    assert application.priorities == [1], "PacketPriority.HIGH, exactly once"
    assert application.priority_depth == 0, "the priority was never given back"


# ------------------------------------------------------- sleepy end devices --


async def test_a_sleeping_device_defers_instead_of_failing_the_whole_pass(store) -> None:
    """A `configure_reporting` to a sleeping device fails after up to ~28 s
    PER ATTEMPT. One such cluster must not take the other clusters - or the
    IAS enrolment - down with it.

    Fault to prove it: let the first DeliveryError abort the routine."""
    device = sensor()
    on_a_network(device)
    cluster_of(device, POWER_CONFIGURATION).bind_error = DeliveryError("no response")

    outcome = await configure_device(device, store=store)

    assert POWER_CONFIGURATION in outcome.deferred
    assert store.zigbee_pending.pending_for(device.ieee) == [(1, POWER_CONFIGURATION)]
    # And the IAS enrolment - the one thing this sensor cannot do without -
    # went through anyway.
    ias = cluster_of(device, IAS_ZONE)
    assert ias.binds == 1
    assert ias.writes
    assert IAS_ZONE in outcome.configured


async def test_a_deferred_cluster_is_retried_when_the_device_is_next_heard_from(store) -> None:
    """Right after a device transmits it polls its parent, so a queued
    request has its best chance THEN. Every incoming packet fires
    `device_last_seen_updated`; the routine also retries on `device_joined`
    and on `checkin`.

    Fault to prove it: retry on a fixed timer instead. The request then
    almost always arrives while the device is asleep again."""
    device = sensor()
    on_a_network(device)
    battery = cluster_of(device, POWER_CONFIGURATION)
    battery.bind_error = DeliveryError("no response")

    await configure_device(device, store=store)
    assert store.zigbee_pending.pending_for(device.ieee) == [(1, POWER_CONFIGURATION)]

    # Time alone changes nothing - which is the whole difference between
    # this and a timer.
    await settle()
    assert store.zigbee_pending.pending_for(device.ieee) == [(1, POWER_CONFIGURATION)]

    # The device wakes up and sends something. Anything.
    battery.bind_error = None
    device.heard_from()
    await settle()

    assert store.zigbee_pending.pending_for(device.ieee) == []
    # Twice: the attempt that failed while it was asleep, and the one that
    # went through the moment it spoke. The reporting configuration is only
    # reached after a bind that did not raise.
    assert battery.binds == 2
    assert battery.reporting


async def test_a_checkin_is_the_other_moment_a_deferred_cluster_is_retried(store) -> None:
    """PollControl's `checkin` (client command 0) is a sleepy device
    explicitly announcing that it is listening right now.

    Fault to prove it: listen only to `device_last_seen_updated`."""
    device = sensor()
    on_a_network(device)
    battery = cluster_of(device, POWER_CONFIGURATION)
    battery.bind_error = DeliveryError("no response")

    await configure_device(device, store=store)
    battery.bind_error = None

    cluster_of(device, POLL_CONTROL).receive_command(0, ClientCommandArgs(()))
    await settle()

    assert store.zigbee_pending.pending_for(device.ieee) == []
    assert battery.binds == 2
    assert battery.reporting


async def test_an_interrupted_configuration_run_leaves_retryable_rows_not_a_stuck_state(
    store,
) -> None:
    """The lesson from the radios sidecar, applied here: any state a process
    can be interrupted in must be recoverable. A row in this table IS the
    recovery - it says "this cluster still needs configuring", which is true
    whether the run finished, failed, or was killed halfway through. There
    is no non-terminal phase that can freeze, and nothing needs an SSH
    session to clear.

    Fault to prove it: write the rows only AFTER a successful pass. A run
    killed mid-way then leaves no trace, and the device is never retried.

    (Lives here rather than in `tests/model/test_zigbee_pending_store.py`
    because it has to drive the real routine, and the Zigbee fakes are only
    importable from this directory.)"""
    device = sensor()
    on_a_network(device)
    # The process is killed while the battery cluster is being bound.
    # `CancelledError` is a BaseException, so it really does unwind the
    # routine rather than being caught and deferred like a sleeping device.
    cluster_of(device, POWER_CONFIGURATION).bind_error = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await configure_device(device, store=store)

    # The row the interrupted cluster wrote before it started is still
    # there, and it says exactly what is still owed.
    assert store.zigbee_pending.pending_for(device.ieee) == [(1, POWER_CONFIGURATION)]
    # Nothing else is in a half-finished state: no other row, and no phase
    # a later run has to be told about.
    assert store.zigbee_pending.addresses_with_pending() == [device.ieee]


async def test_a_lamp_that_refuses_reporting_falls_back_to_polling(store) -> None:
    """Common on cheap lamps: `configure_reporting` answers a status other
    than SUCCESS. ZHA polls those every 2700-4500 s, and that poll doubles
    as the liveness check Task 8 uses.

    Fault to prove it: treat a non-SUCCESS status as success. The lamp then
    shows a stale state in Loxone forever and nothing reports it."""
    device = lamp()
    on_a_network(device)
    # 0x87 is UNSUPPORTED_ATTRIBUTE: reached, answered, refused.
    cluster_of(device, ON_OFF).reporting_statuses = {0x0000: 0x87}
    polling = PollingSchedule(jitter=lambda low, high: low)

    outcome = await configure_device(device, store=store, now=lambda: 1000.0, polling=polling)

    assert polling.due == {(device.ieee, 1, ON_OFF, 0x0000): 1000.0 + 2700.0}
    # Reached and answered is not deferred: there is nothing to retry when
    # the device wakes up, because it was awake.
    assert ON_OFF in outcome.configured
    assert store.zigbee_pending.pending_for(device.ieee) == []
    # And the attributes it did accept are not dragged into polling with it.
    assert not [key for key in polling.due if key[2] == COLOR]


# -------------------------------------------------------- rejoin and startup --


async def test_a_rejoin_with_a_new_nwk_configures_again(store) -> None:
    """A factory-reset device has lost its bindings, and it fires
    `device_joined` plus `device_initialized` again. A rejoin with the same
    NWK fires nothing and keeps its bindings anyway.

    Fault to prove it: configure only on first join. A reset sensor then
    pairs and stays silent."""
    device = sensor()
    on_a_network(device)

    await configure_device(device, store=store)
    await configure_device(device, store=store)

    assert cluster_of(device, IAS_ZONE).binds == 2
    assert cluster_of(device, POWER_CONFIGURATION).binds == 2
    # And the enrolment went out both times: a reset sensor has forgotten
    # its CIE address along with its bindings.
    assert len([1 for command_id, _ in cluster_of(device, IAS_ZONE).sent if command_id == 0]) == 2
    # The permanent client-command handler is NOT stacked a second time,
    # which would answer one enrolment request twice.
    assert len(cluster_of(device, IAS_ZONE).command_listeners) == 1


async def test_startup_reads_values_and_does_not_reconfigure_everything(store) -> None:
    """ZHA does not either, and a full reconfiguration at every start would
    wake every battery device on the network on every restart of the bridge.

    Fault to prove it: run the full routine for every device at startup."""
    device = lamp()
    on_a_network(device)

    await read_current_values(device)

    assert [cluster.binds for cluster in device.clusters()] == [0, 0, 0, 0]
    assert all(cluster.reporting == [] for cluster in device.clusters())
    assert all(cluster.writes == [] for cluster in device.clusters())
    # It really did read, and uncached: the values are the device's, not
    # the ones the database was carrying.
    on_off = cluster_of(device, ON_OFF)
    assert on_off.get(on_off.attributes[0x0000]) is False


# ----------------------------------------------------------------- the wiring --


async def test_a_device_that_joins_is_configured_before_it_is_delivered(store, tmp_path) -> None:
    """The half of this task that lives in `source.py`: a device whose
    interview has finished is configured, and only then announced. The other
    order would hand the runtime a device whose attribute cache nothing had
    filled - and `Store.register_signals` computes `exported` only when a
    row is CREATED, so those rows would be created empty, once, forever.

    Fault to prove it: announce the device first and configure afterwards -
    `queue.put_nowait(address)` before `await configure_device(...)`. The
    handler below then sees a device with nothing bound."""
    device = sensor()
    application = FakeApplication()
    source = ZigbeeSource(
        path="/dev/ttyUSB0",
        fingerprint=Fingerprint(
            name="SONOFF ZBDongle-E V2",
            radio_type="ezsp",
            baudrate=115200,
            flow_control="software",
        ),
        database=tmp_path / "zigbee.sqlite",
        application_factory=_hands_out(application),
        store=store,
    )
    # What the handler saw of the device AT THE MOMENT it was announced.
    # Counting binds afterwards would pass whichever order the two ran in.
    seen_at_announcement: list[int] = []

    class _Handler:
        async def on_node_snapshot(self, device_id: int, snapshot: Any) -> None:
            seen_at_announcement.append(cluster_of(device, IAS_ZONE).binds)

        async def on_attribute(self, device_id: int, path: str, raw: object) -> None: ...
        async def on_event(self, device_id: int, path: str) -> None: ...
        async def set_online(self, device_id: int, online: bool) -> None: ...

    await source.connect()
    try:
        await source.subscribe(lambda address: 1, _Handler())
        application.fire_device_initialized(device)
        await settle()

        assert cluster_of(device, IAS_ZONE).binds == 1
        assert cluster_of(device, IAS_ZONE).writes
        assert seen_at_announcement == [1], (
            "the device was announced before its clusters were bound"
        )
    finally:
        await source.disconnect()


def _hands_out(application: FakeApplication):
    async def factory(config: dict[str, Any]) -> FakeApplication:
        application.config = config
        return application

    return factory


# ------------------------------------------------------- the polling consumer --
#
# `PollingSchedule.schedule()` has recorded WHEN a cluster that refused a
# reporting configuration should next be read since this module was written,
# and nothing read `.due` at all until `PollingLoop`. A lamp that refused
# reporting showed its last value forever, and lost the liveness proxy the
# availability sweep leans on for it besides.


def _source_for(application: FakeApplication, store: Any, tmp_path: Path) -> ZigbeeSource:
    return ZigbeeSource(
        path="/dev/ttyUSB0",
        fingerprint=Fingerprint(
            name="SONOFF ZBDongle-E V2",
            radio_type="ezsp",
            baudrate=115200,
            flow_control="software",
        ),
        database=tmp_path / "zigbee.sqlite",
        application_factory=_hands_out(application),
        store=store,
    )


def _fixed_schedule(interval: float = 100.0) -> PollingSchedule:
    """A schedule whose jitter is a constant, so "was it rescheduled?" is a
    question with an exact answer rather than a range."""
    return PollingSchedule(jitter=lambda _low, _high: interval)


async def test_the_polling_schedule_is_actually_read_and_rescheduled(store, tmp_path) -> None:
    """`PollingSchedule` records WHEN a cluster that refused reporting should
    next be polled - the module docstring's own words - and nothing read
    `.due` before `PollingLoop`. Without a consumer, a lamp that refused
    reporting shows its last value forever.

    Fault to prove it: let `_poll_due` read the due attribute without
    calling `self._schedule.schedule(...)` again afterwards. The first poll
    after the interval elapses then happens - and the SECOND tick polls the
    same entry all over again, because nothing put a new due time into
    `.due`, so the entry stays stuck in the past forever."""
    device = lamp()
    application = on_a_network(device)
    source = _source_for(application, store, tmp_path)
    await source.connect()
    try:
        schedule = _fixed_schedule()
        schedule.schedule(LAMP, 1, ON_OFF, 0x0000, at=0.0)  # due at 100.0
        clock = [200.0]
        loop = PollingLoop(source, schedule, now=lambda: clock[0])

        await loop._poll_due()

        on_off = cluster_of(device, ON_OFF)
        assert on_off.reads == [[0x0000]]
        # UNCACHED, for the reason `read_current_values` gives: a cached
        # answer would republish a value nobody measured.
        assert on_off.cached_reads == [False]
        # Rescheduled a full interval into the future, off the moment the
        # poll was attempted at.
        assert schedule.due[(LAMP, 1, ON_OFF, 0x0000)] == 300.0

        # The tick right afterwards must find nothing to do. Without the
        # reschedule the entry is still due at 100.0 and is polled again.
        await loop._poll_due()

        assert on_off.reads == [[0x0000]]
    finally:
        await source.disconnect()


async def test_polling_pauses_while_disconnected_instead_of_forgetting_the_schedule(
    store, tmp_path
) -> None:
    """While the radio is down, `ZigbeeSource._device_or_none` answers `None`
    for every address, because `_devices()` returns `[]` once `self._app is
    None` - the exact shape `_poll_due` uses, further down, to notice a
    device that has genuinely been removed and retire its schedule entry.
    Without an explicit `self._source.connected` check FIRST, a USB replug is
    indistinguishable from a removed device, and every scheduled poll on
    every device is silently and permanently dropped on the first blip.

    Fault to prove it: delete the `if not self._source.connected: return`
    line from `_poll_due`, so it falls through to the same device-lookup path
    a real removal uses. The disconnected tick then empties
    `PollingSchedule.due` instead of leaving it for the next tick to find
    once the radio is back, and the lamp never polls again even after the
    stick is reconnected."""
    device = lamp()
    application = on_a_network(device)
    source = _source_for(application, store, tmp_path)
    await source.connect()
    try:
        schedule = _fixed_schedule()
        schedule.schedule(LAMP, 1, ON_OFF, 0x0000, at=0.0)  # due at 100.0
        clock = [200.0]
        loop = PollingLoop(source, schedule, now=lambda: clock[0])

        # The blip: `connect()`'s own failure path and `disconnect()` both
        # leave `_app` at `None` while the device is still perfectly real.
        await source.disconnect()
        assert source.connected is False
        await loop._poll_due()

        assert cluster_of(device, ON_OFF).reads == [], "polled over a radio that is gone"
        assert schedule.due[(LAMP, 1, ON_OFF, 0x0000)] == 100.0, (
            "the blip retired the schedule entry as though the device were gone"
        )

        # The stick is back. The debt the blip left alone is now collected.
        await source.connect()
        await loop._poll_due()

        assert cluster_of(device, ON_OFF).reads == [[0x0000]]
    finally:
        await source.disconnect()


async def test_a_polled_devices_stale_entry_is_dropped_once_the_device_is_really_gone(
    store, tmp_path
) -> None:
    """A removed device, or one that rejoined and lost the endpoint, cluster
    or attribute a due entry names, must not be polled forever into the void
    - `_cluster_or_none` answering `None` IS the retirement signal, but only
    while the source is genuinely connected (the previous test is why the
    order matters).

    Fault to prove it: leave the stale entry in `PollingSchedule.due` instead
    of deleting it when `_cluster_or_none` returns `None`. `_poll_due` then
    retries the same dead reference on every tick, forever, logging one
    failure per tick for a device that will never answer again."""
    device = lamp()
    application = on_a_network(device)
    source = _source_for(application, store, tmp_path)
    await source.connect()
    try:
        schedule = _fixed_schedule()
        gone = "00:12:4b:00:1c:00:00:99"
        # Four ways one entry can go stale, one per line: the device itself,
        # its endpoint, its cluster, and the attribute on an existing
        # cluster - a rejoin can reshape any of them.
        schedule.schedule(gone, 1, ON_OFF, 0x0000, at=0.0)
        schedule.schedule(LAMP, 9, ON_OFF, 0x0000, at=0.0)
        schedule.schedule(LAMP, 1, 0x0402, 0x0000, at=0.0)
        schedule.schedule(LAMP, 1, ON_OFF, 0x4242, at=0.0)
        loop = PollingLoop(source, schedule, now=lambda: 200.0)

        await loop._poll_due()

        assert schedule.due == {}
        assert cluster_of(device, ON_OFF).reads == []
    finally:
        await source.disconnect()
