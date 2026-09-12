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

"""Configure-on-join: what makes a paired Zigbee device actually talk.

**zigpy binds nothing and configures no reporting.** That was ZHA's entity
layer's job, and without it a device pairs, appears in the catalogue, shows
its manufacturer and model - and never sends a single reading (design
2026-09-12, section 6; boundary design section 1.1). This module is the
part of the feature that decides whether it works at all.

Four things in here are easy to get subtly wrong, and each failure looks
exactly like success:

- **The quirk hook runs FIRST.** `apply_custom_configuration()` is what
  casts the Tuya "spell" - a specific `Basic` read - and many Tuya devices
  send nothing at all without it. It also has to precede the binds, because
  the binds go through the quirk's own cluster objects, which is what makes
  overrides such as `TuyaNoBindPowerConfigurationCluster` apply.
- **IAS alarms arrive as a CLIENT COMMAND, never as an attribute report.**
  A design that only subscribes to reports sees a contact or motion sensor
  that pairs, configures, goes green - and never fires. `status_change_
  notification` is client command 0 and is handled as such; `IasZone` is
  bound and deliberately never configured for reporting, because a
  reporting configuration on `zone_status` is useless and on some devices
  fails in a way that aborts the rest of the pass.
- **Many devices never ask to be enrolled.** So the `enroll_response` is
  sent UNSOLICITED, and an `enroll` request (client command 1) is answered
  as well, permanently - some sensors ask again on every rejoin and stay
  unenrolled until they get an answer.
- **The final read is uncached.** A rejoining device has values in zigpy's
  database from before it left; `allow_cache=True` would hand those back
  unread, and the bridge would publish a stale state as though it had just
  measured it.

**Nothing here imports zigpy at module import time**, for the reason
`source.py`'s docstring gives: an installation with no Zigbee stick must
not pay for the radio libraries. Numbers are spelled out as constants below
and every one of them is checked against the installed library by
`tests/zigbee/test_zigpy_names.py`. The two things that cannot be a number -
`ReportingConfig` and the failure vocabulary - are imported inside the
functions that need them, by which time a radio is up anyway.

**A deferred cluster is a row in the store, written BEFORE the attempt.**
See `model/zigbee_pending_store.py`: that ordering is the whole interruption
story. `configure_reporting` to a sleeping device fails after up to ~28 s
per attempt, and the row says "this cluster still needs configuring" whether
the run finished, failed, or was killed halfway through.

**No timeout is imposed on the device calls here.** `bounded_source_call`
exists for the request paths, where a user is waiting; this routine is a
background task started by a join, and cutting a sleepy device's ~28 s
attempt short would only turn a slow success into a deferral."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final

logger = logging.getLogger(__name__)

__all__ = [
    "REPORTING",
    "ConfigureOutcome",
    "PollingLoop",
    "PollingSchedule",
    "configure_device",
    "read_current_values",
    "retry_pending",
    "watch_for_wakeups",
]


# --------------------------------------------------------------- constants --

# Cluster numbers. Zigbee's and Matter's are identical for everything this
# bridge touches (see `translate.py`), so these read the same in both.
BASIC_CLUSTER: Final = 0x0000
POWER_CONFIGURATION_CLUSTER: Final = 0x0001
IDENTIFY_CLUSTER: Final = 0x0003
POLL_CONTROL_CLUSTER: Final = 0x0020
COLOR_CLUSTER: Final = 0x0300
IAS_ZONE_CLUSTER: Final = 0x0500

# IasZone's attributes (`zigpy.zcl.clusters.security.IasZone`).
IAS_ZONE_TYPE_ATTRIBUTE: Final = 0x0001
IAS_ZONE_STATUS_ATTRIBUTE: Final = 0x0002
IAS_CIE_ADDRESS_ATTRIBUTE: Final = 0x0010

# IasZone's commands. The two client commands are the ones that matter:
# an alarm is command 0 and an enrolment request is command 1. The server
# command 0 is the answer to both, and to nobody at all.
IAS_ENROLL_RESPONSE_COMMAND: Final = 0
IAS_STATUS_CHANGE_NOTIFICATION_COMMAND: Final = 0
IAS_ENROLL_REQUEST_COMMAND: Final = 1
# `IasZone.EnrollResponse.Success`.
IAS_ENROLL_SUCCESS: Final = 0

# PollControl's `checkin`, client command 0: a sleepy device announcing that
# it is awake and listening, which is the best moment there is to talk to it.
POLL_CONTROL_CHECKIN_COMMAND: Final = 0

# Identify's `identify(identify_time)`, in seconds. A light that flashes is
# the cheapest "it worked" the user can get, and ZHA does the same.
IDENTIFY_COMMAND: Final = 0
IDENTIFY_SECONDS: Final = 2

# `zigpy.types.PacketPriority.HIGH`. The whole routine runs at this
# priority, as ZHA does: a joining device is awake now and will not be
# again for hours.
PACKET_PRIORITY_HIGH: Final = 1

# `zigpy.zcl.foundation.Status.SUCCESS`.
_ZCL_SUCCESS: Final = 0

# ZHA's polling interval for lights whose reporting configuration was
# refused (research D.3). A range, not a value: a network full of lamps
# that all refused must not poll in lockstep.
POLL_INTERVAL_SECONDS: Final[tuple[float, float]] = (2700.0, 4500.0)

# The attribute this module's own listener hangs off a device, so that a
# second configuration pass does not stack a second listener on it.
_WATCHER_ATTRIBUTE: Final = "_loxmatter_wakeup_watcher"
_IAS_LISTENER_ATTRIBUTE: Final = "_loxmatter_ias_listener"


# ZHA's reporting values, which are the only field-proven set (design
# section 6.2, research D.1, R1 section 7). `(cluster, attribute) ->
# (minimum interval, maximum interval, reportable change)`, all in seconds
# except the change, which is in the attribute's own units.
#
# **IasZone is deliberately absent.** Its alarms are a client command, so a
# reporting configuration on `zone_status` would be useless at best; on
# several devices it answers with a failure that takes the rest of the
# configuration pass down with it.
#
# The maxima are 900 s rather than something livelier because a Zigbee
# network is shared: a lamp that reports every 60 s is fifteen times the
# traffic on a mesh whose sleepy devices have to get a word in too.
REPORTING: Final[dict[tuple[int, int], tuple[int, int, int]]] = {
    # OnOff `on_off`
    (0x0006, 0x0000): (0, 900, 1),
    # LevelControl `current_level`
    (0x0008, 0x0000): (1, 900, 1),
    # Color `current_x`, `current_y`, `color_temperature`
    (0x0300, 0x0003): (30, 900, 1),
    (0x0300, 0x0004): (30, 900, 1),
    (0x0300, 0x0007): (30, 900, 1),
    # IlluminanceMeasurement `measured_value`
    (0x0400, 0x0000): (30, 900, 1),
    # TemperatureMeasurement `measured_value`, change 50 = 0.5 degrees
    (0x0402, 0x0000): (30, 900, 50),
    # RelativeHumidity `measured_value`, change 100 = 1 %
    (0x0405, 0x0000): (30, 900, 100),
    # OccupancySensing `occupancy`
    (0x0406, 0x0000): (0, 900, 1),
    # PowerConfiguration `battery_voltage`, `battery_percentage_remaining`.
    # Hours, not minutes, and only on battery devices - see
    # `_wanted_reporting`.
    (0x0001, 0x0020): (3600, 10800, 1),
    (0x0001, 0x0021): (3600, 10800, 1),
}

# The static facts that decide a device's type and its controls, read once
# before anything is bound (design section 6.1, step 2). `zone_type` picks
# the Matter target cluster and the polarity; the three Color attributes are
# what `profiles.capabilities` gates the colour picker on and what bounds
# the colour-temperature slider; `power_source` is Basic's own answer to
# "is this thing on batteries".
STATIC_READS: Final[dict[int, tuple[int, ...]]] = {
    BASIC_CLUSTER: (0x0007,),
    COLOR_CLUSTER: (0x400A, 0x400B, 0x400C),
    IAS_ZONE_CLUSTER: (IAS_ZONE_TYPE_ATTRIBUTE,),
}


# ----------------------------------------------------------------- results --


@dataclass(frozen=True)
class ConfigureOutcome:
    """What one configuration pass achieved.

    `configured` and `deferred` are CLUSTER IDS in the order they were met,
    so a cluster that exists on two endpoints appears twice - which is the
    truth, because each endpoint is bound separately. What is in neither is
    a cluster this bridge has no reporting row for and therefore never
    touched."""

    configured: tuple[int, ...]
    deferred: tuple[int, ...]
    quirk_applied: bool


@dataclass
class PollingSchedule:
    """Clusters that were reached and REFUSED a reporting configuration.

    Common on cheap lamps: `configure_reporting` answers with a status other
    than SUCCESS. ZHA polls those instead, every 2700-4500 s, and that poll
    doubles as the liveness check Task 8's availability sweep uses. Treating
    a non-SUCCESS status as success is the failure this exists to prevent:
    the lamp then shows a stale state in Loxone forever and nothing
    anywhere reports that it does.

    Held in memory on purpose, unlike the pending table. A refusal is a
    permanent property of the device and is rediscovered on the next
    configuration pass; a deferral is a moment in time and is not."""

    interval: tuple[float, float] = POLL_INTERVAL_SECONDS
    jitter: Callable[[float, float], float] = random.uniform
    due: dict[tuple[str, int, int, int], float] = field(default_factory=dict)

    def schedule(
        self, address: str, endpoint: int, cluster_id: int, attribute_id: int, *, at: float
    ) -> None:
        self.due[(address, endpoint, cluster_id, attribute_id)] = at + self.jitter(*self.interval)

    def forget(self, address: str) -> None:
        for key in [key for key in self.due if key[0] == address]:
            del self.due[key]


class PollingLoop:
    """Runs `PollingSchedule.due` for real, on a real periodic tick.

    `PollingSchedule.schedule()` has recorded WHEN a cluster that refused a
    reporting configuration should next be read since configure-on-join
    landed - nothing before this class ever looked at `.due` at all: a lamp
    that refused reporting showed its last value forever, and lost the
    liveness proxy the availability sweep leans on for it besides
    (`PollingSchedule`'s own docstring: "That poll doubles as the liveness
    check Task 8's availability sweep uses").

    Owned and started by `ZigbeeSource` exactly the way `AvailabilityChecker`
    (`availability.py`) is: built fresh in `subscribe()`, started there,
    stopped in `disconnect()` and before a fresh one replaces it in a later
    `subscribe()` call. The two loops are siblings on purpose."""

    def __init__(
        self,
        source: Any,
        schedule: PollingSchedule,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], float] = time.time,
        tick: float = 60.0,
    ) -> None:
        self._source = source
        self._schedule = schedule
        self._sleep = sleep
        self._now = now
        self._tick = tick
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        while True:
            await self._sleep(self._tick)
            await self._poll_due()

    async def _poll_due(self) -> None:
        """One tick.

        **The connected check comes FIRST and gates everything else.** A
        device that `ZigbeeSource._device_or_none(address)` answers `None`
        for while the radio is merely down looks identical, from here, to
        one that was genuinely removed - `_devices()` returns `[]` either
        way once `self._app is None`. Treating the two the same would
        silently and permanently drop every scheduled poll on every device
        on the first USB blip, which is worse than the missing consumer
        this class exists to add. Only while the source IS connected does
        `_cluster_or_none` returning `None`, below, mean "this is really
        gone" rather than "the radio is briefly away"."""
        if not self._source.connected:
            return
        moment = self._now()
        due = [key for key, at in self._schedule.due.items() if at <= moment]
        for address, endpoint_id, cluster_id, attribute_id in due:
            device = self._source._device_or_none(address)
            cluster = _cluster_or_none(device, endpoint_id, cluster_id, attribute_id)
            if cluster is None:
                del self._schedule.due[(address, endpoint_id, cluster_id, attribute_id)]
                continue
            # Rescheduled BEFORE the read is attempted - the same "write the
            # debt before the attempt" ordering `_configure_cluster` already
            # uses on the pending table, and for the same reason: a read
            # that fails or hangs must not leave this entry stuck in the
            # past, where the very next tick would retry it at once instead
            # of waiting out the normal interval.
            self._schedule.schedule(address, endpoint_id, cluster_id, attribute_id, at=moment)
            try:
                await cluster.read_attributes([attribute_id], allow_cache=False)
            except Exception as exc:  # noqa: BLE001 — a missed poll is not an error here
                logger.info(
                    "polling %s cluster %#06x attribute %#06x failed: %s",
                    address,
                    cluster_id,
                    attribute_id,
                    _describe(exc),
                )


def _cluster_or_none(
    device: Any, endpoint_id: int, cluster_id: int, attribute_id: int
) -> Any | None:
    """The cluster one due poll targets, or `None` when the device, its
    endpoint, its cluster or the attribute itself is no longer there - a
    rejoin (`test_a_rejoin_with_a_new_nwk_configures_again`) can reshape any
    of the four."""
    if device is None:
        return None
    endpoint = next(
        (endpoint for endpoint in device.non_zdo_endpoints if endpoint.endpoint_id == endpoint_id),
        None,
    )
    if endpoint is None:
        return None
    cluster = endpoint.in_clusters.get(cluster_id)
    if cluster is None or cluster.attributes.get(attribute_id) is None:
        return None
    return cluster


# --------------------------------------------------------------- the routine --


async def configure_device(
    device: Any,
    *,
    store: Any,
    now: Callable[[], float] = time.time,
    polling: PollingSchedule | None = None,
) -> ConfigureOutcome:
    """Binds a freshly joined device's clusters and makes it report.

    The order is design section 6.1's, and it is not a matter of taste: the
    quirk hook first, then the static reads, then bind and reporting through
    the quirk's cluster objects, then IAS enrolment, then an uncached read
    of everything that becomes a signal, then the identify blink.

    The whole pass runs inside `app.request_priority(PacketPriority.HIGH)`
    and, when the device offers it, inside `device.fast_poll_mode()` - which
    binds PollControl and writes `fast_poll_timeout`, keeping the device
    polling its parent for the WHOLE configuration rather than only for the
    first command. Bracketing just the binds would leave the reads and the
    enrolment outside it, which is where a sleepy device is most likely to
    have gone back to sleep.

    Safe to run again. A device that rejoins with a new NWK has been factory
    reset and has lost its bindings, and it fires `device_joined` and
    `device_initialized` a second time; this routine must do its work again
    then, so there is deliberately no "already configured" guard."""
    address = str(device.ieee)
    quirk_applied = hasattr(device, "_quirk_registry_entry")

    # 1. The quirk hook, before anything else - the binds below depend on
    #    the cluster objects it configures.
    if hasattr(device, "apply_custom_configuration"):
        await device.apply_custom_configuration()

    # An enrolment request may arrive at any time, including from a device
    # that refuses everything else, so the handler goes on before the first
    # packet leaves. Installing it costs nothing on the air.
    _install_ias_handlers(device)

    if getattr(device, "skip_configuration", False):
        # 79 shipped quirks set this, and they set it because binding or
        # reporting actively breaks those devices.
        logger.info("%s asks not to be configured - leaving it alone", address)
        return ConfigureOutcome(configured=(), deferred=(), quirk_applied=quirk_applied)

    configured: list[int] = []
    deferred: list[int] = []

    async with contextlib.AsyncExitStack() as stack:
        application = getattr(device, "application", None)
        if application is not None and hasattr(application, "request_priority"):
            await stack.enter_async_context(application.request_priority(PACKET_PRIORITY_HIGH))
        if hasattr(device, "fast_poll_mode"):
            await stack.enter_async_context(device.fast_poll_mode())

        await _read_static_facts(device)

        for endpoint in device.non_zdo_endpoints:
            for cluster_id, cluster in sorted(endpoint.in_clusters.items()):
                if cluster_id == IAS_ZONE_CLUSTER:
                    continue  # bound and enrolled below, never reported
                if not _wanted_reporting(device, cluster, cluster_id):
                    continue
                done = await _configure_cluster(
                    device,
                    endpoint,
                    cluster,
                    cluster_id,
                    store=store,
                    now=now,
                    polling=polling,
                )
                (configured if done else deferred).append(cluster_id)

        for endpoint in device.non_zdo_endpoints:
            cluster = endpoint.in_clusters.get(IAS_ZONE_CLUSTER)
            if cluster is None:
                continue
            done = await _configure_cluster(
                device, endpoint, cluster, IAS_ZONE_CLUSTER, store=store, now=now, polling=polling
            )
            (configured if done else deferred).append(IAS_ZONE_CLUSTER)

        await read_current_values(device)
        await _identify_blink(device)

    if deferred:
        watch_for_wakeups(device, store=store, now=now, polling=polling)
    return ConfigureOutcome(
        configured=tuple(configured), deferred=tuple(deferred), quirk_applied=quirk_applied
    )


async def _configure_cluster(
    device: Any,
    endpoint: Any,
    cluster: Any,
    cluster_id: int,
    *,
    store: Any,
    now: Callable[[], float],
    polling: PollingSchedule | None,
) -> bool:
    """One cluster's whole share of the pass. `True` when it is done with.

    **The pending row is written first and cleared last.** That is what
    makes an interrupted run recoverable: a process killed between the two
    leaves a row that says exactly what is still owed, and a run that never
    started leaves nothing to be stuck on."""
    address = str(device.ieee)
    endpoint_id = endpoint.endpoint_id
    store.zigbee_pending.mark_pending(address, endpoint_id, cluster_id)
    try:
        if cluster_id == IAS_ZONE_CLUSTER:
            await _enrol_ias_zone(device, cluster)
        else:
            await _bind_and_report(
                cluster,
                _wanted_reporting(device, cluster, cluster_id),
                address=address,
                endpoint_id=endpoint_id,
                cluster_id=cluster_id,
                now=now,
                polling=polling,
            )
    except Exception as exc:
        if not _is_unreachable(exc):
            # A bug in this file or in a quirk, not a sleeping device.
            # Logged loudly and still deferred rather than raised: one
            # cluster must never take the rest of the pass - or the IAS
            # enrolment - down with it.
            logger.exception("configuring cluster %#06x of %s failed", cluster_id, address)
        else:
            logger.info(
                "%s did not answer for cluster %#06x on endpoint %s (%s) - deferred until it "
                "is next heard from",
                address,
                cluster_id,
                endpoint_id,
                _describe(exc),
            )
        return False
    store.zigbee_pending.clear(address, endpoint_id, cluster_id)
    return True


async def _bind_and_report(
    cluster: Any,
    wanted: dict[Any, tuple[int, int, int]],
    *,
    address: str,
    endpoint_id: int,
    cluster_id: int,
    now: Callable[[], float],
    polling: PollingSchedule | None,
) -> None:
    """Binds one cluster and asks it to report.

    Through `cluster` - the object `endpoint.in_clusters` holds, which on a
    quirked device IS the quirk's own subclass. That matters: a bind sent to
    the base class's `bind` bypasses exactly the override the quirk exists
    to provide, and `TuyaNoBindPowerConfigurationCluster` is a quirk whose
    entire purpose is to NOT send that bind.

    `configure_reporting_multiple` rather than a call per attribute: zigpy
    batches by effective manufacturer code and by frame size, which for a
    colour lamp is one request instead of three."""
    from zigpy.zcl.helpers import ReportingConfig

    await cluster.bind()
    statuses = await cluster.configure_reporting_multiple(
        {
            definition: ReportingConfig(
                min_interval=minimum, max_interval=maximum, reportable_change=change
            )
            for definition, (minimum, maximum, change) in wanted.items()
        }
    )
    for definition, status in statuses.items():
        if int(status) == _ZCL_SUCCESS:
            continue
        logger.info(
            "%s refused reporting for %#06x/%#06x with status %s - polling it instead",
            address,
            cluster_id,
            definition.id,
            int(status),
        )
        if polling is not None:
            polling.schedule(address, endpoint_id, cluster_id, definition.id, at=now())


def _wanted_reporting(
    device: Any, cluster: Any, cluster_id: int
) -> dict[Any, tuple[int, int, int]]:
    """The reporting rows that apply to this cluster, keyed by the
    attribute DEFINITION zigpy wants.

    The definition and never the bare id, for the reason `source.py`'s
    `_endpoint_facts` spells out at length: `Cluster.find_attribute(int)`
    raises for every id a quirk declares twice, and 28 shipped quirks do.

    Battery reporting is skipped on a mains-powered device: the attributes
    are usually absent there, and asking for them costs a round trip and an
    `UNSUPPORTED_ATTRIBUTE` for nothing."""
    if cluster_id == POWER_CONFIGURATION_CLUSTER and _is_mains_powered(device):
        return {}
    wanted: dict[Any, tuple[int, int, int]] = {}
    for (row_cluster, attribute_id), config in REPORTING.items():
        if row_cluster != cluster_id:
            continue
        definition = cluster.attributes.get(attribute_id)
        if definition is None:
            continue
        wanted[definition] = config
    return wanted


def _is_mains_powered(device: Any) -> bool:
    """The same reading `source._facts` takes: no node descriptor yet means
    battery, which is the conservative half of the pair."""
    node_desc = getattr(device, "node_desc", None)
    return bool(node_desc is not None and node_desc.is_mains_powered)


# ------------------------------------------------------------------ reading --


async def _read_static_facts(device: Any) -> None:
    """The facts that decide type and controls, read before the binds.

    Failures are swallowed per cluster: a device that will not answer
    `color_capabilities` still deserves its reporting configuration, and the
    static value is read again on the next pass."""
    for endpoint in device.non_zdo_endpoints:
        for cluster_id, attribute_ids in STATIC_READS.items():
            cluster = endpoint.in_clusters.get(cluster_id)
            if cluster is None:
                continue
            present = [
                attribute_id
                for attribute_id in attribute_ids
                if cluster.attributes.get(attribute_id) is not None
            ]
            if not present:
                continue
            try:
                await cluster.read_attributes(present, allow_cache=False)
            except Exception as exc:  # noqa: BLE001 — a static fact is not worth the pass
                logger.info(
                    "could not read the static facts of cluster %#06x on %s endpoint %s: %s",
                    cluster_id,
                    device.ieee,
                    endpoint.endpoint_id,
                    _describe(exc),
                )


async def read_current_values(device: Any) -> None:
    """Reads every value that becomes a Loxone signal, UNCACHED.

    This is what makes the translation's `None` rule satisfiable rather than
    merely stated: `Store.register_signals` computes `exported` only when a
    row is CREATED, and `build_snapshot` never writes a path whose value is
    `None` - so without a real read at join, every mapped path would be
    absent and the first row would be created later, if at all.

    `allow_cache=False` is the load-bearing half. zigpy's
    `read_attributes(allow_cache=True)` answers from `_attr_cache` and
    SKIPS the read for anything it already holds - and a device that has
    rejoined has a database full of values from before it left. The bridge
    would publish those as though it had just measured them: a contact
    sensor that was open when it dropped off the network would come back
    "open" without anybody having asked it.

    Also the whole of what startup does. A full reconfiguration at every
    start would wake every battery device on the network on every restart of
    the bridge, and ZHA does not do it either."""
    for endpoint in device.non_zdo_endpoints:
        for cluster_id, cluster in sorted(endpoint.in_clusters.items()):
            wanted = sorted(
                {attribute_id for (row, attribute_id) in REPORTING if row == cluster_id}
                | ({IAS_ZONE_STATUS_ATTRIBUTE} if cluster_id == IAS_ZONE_CLUSTER else set())
            )
            present = [
                attribute_id
                for attribute_id in wanted
                if cluster.attributes.get(attribute_id) is not None
            ]
            if not present:
                continue
            try:
                await cluster.read_attributes(present, allow_cache=False)
            except Exception as exc:  # noqa: BLE001 — a silent device is not an error here
                logger.info(
                    "could not read the current values of cluster %#06x on %s endpoint %s: %s",
                    cluster_id,
                    device.ieee,
                    endpoint.endpoint_id,
                    _describe(exc),
                )


async def _identify_blink(device: Any) -> None:
    """Two seconds of blinking, and never a reason to fail anything."""
    for endpoint in device.non_zdo_endpoints:
        cluster = endpoint.in_clusters.get(IDENTIFY_CLUSTER)
        if cluster is None:
            continue
        try:
            await cluster.command(IDENTIFY_COMMAND, identify_time=IDENTIFY_SECONDS)
        except Exception as exc:  # noqa: BLE001 — cosmetic
            logger.debug("%s did not blink: %s", device.ieee, _describe(exc))
        return


# ----------------------------------------------------------- IAS enrolment --


async def _enrol_ias_zone(device: Any, cluster: Any) -> None:
    """The four steps without which a contact, motion or leak sensor never
    reports anything (design section 6.3).

    zigpy defines the commands and does not enroll. ZHA's sequence, copied:
    bind, read `zone_type`, write `cie_addr` with the COORDINATOR's own
    IEEE, and then send an `enroll_response` that nobody asked for - because
    many devices never ask, and a device that is not enrolled sends no
    alarms at all.

    No reporting configuration is attempted here, ever. See `REPORTING`."""
    await cluster.bind()
    if cluster.attributes.get(IAS_ZONE_TYPE_ATTRIBUTE) is not None:
        await cluster.read_attributes([IAS_ZONE_TYPE_ATTRIBUTE], allow_cache=False)
    coordinator = _coordinator_ieee(device)
    if coordinator is not None:
        await cluster.write_attributes({IAS_CIE_ADDRESS_ATTRIBUTE: coordinator})
    await cluster.command(
        IAS_ENROLL_RESPONSE_COMMAND,
        enroll_response_code=IAS_ENROLL_SUCCESS,
        zone_id=0,
    )


def _coordinator_ieee(device: Any) -> Any | None:
    """`app.state.node_info.ieee` - the address the sensor must send its
    alarms to. A device with no application behind it (a test double, a
    device zigpy has already dropped) simply gets no CIE write."""
    application = getattr(device, "application", None)
    state = getattr(application, "state", None)
    node_info = getattr(state, "node_info", None)
    return getattr(node_info, "ieee", None)


class _IasZoneListener:
    """The permanent handler for IasZone's two client commands.

    Registered with `Cluster.add_listener`, which dispatches BY METHOD NAME
    and swallows every exception a listener raises - so this class has
    exactly one public method, spelled the way zigpy spells the event, and
    it does as little as possible.

    Command 0, `status_change_notification`, IS how an alarm arrives. It is
    not an attribute report and never will be; writing it into the
    attribute cache with `update_attribute` is what turns it into one, which
    is what the rest of the bridge - the cluster listeners in `source.py`,
    `translate.py`'s IAS mapping - is watching for.

    Command 1, `enroll`, is a device asking to be enrolled. Some sensors ask
    on every rejoin and stay unenrolled, and therefore silent, until they
    get an answer."""

    def __init__(self, cluster: Any) -> None:
        self._cluster = cluster
        self._tasks: set[asyncio.Task[Any]] = set()

    def cluster_command(self, tsn: int, command_id: int, args: Any) -> None:
        if command_id == IAS_STATUS_CHANGE_NOTIFICATION_COMMAND:
            # `args` is the deserialised command schema; field 0 is
            # `zone_status`.
            self._cluster.update_attribute(IAS_ZONE_STATUS_ATTRIBUTE, args[0])
        elif command_id == IAS_ENROLL_REQUEST_COMMAND:
            self._spawn(
                self._cluster.command(
                    IAS_ENROLL_RESPONSE_COMMAND,
                    enroll_response_code=IAS_ENROLL_SUCCESS,
                    zone_id=0,
                    # The answer carries the request's own transaction
                    # sequence number, which is what makes it an answer.
                    tsn=tsn,
                )
            )

    def _spawn(self, coroutine: Any) -> None:
        try:
            task = asyncio.ensure_future(coroutine)
        except RuntimeError:  # pragma: no cover - no running loop
            coroutine.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _install_ias_handlers(device: Any) -> None:
    """One listener per IasZone cluster, once.

    A device is configured again on every rejoin, and a listener stacked per
    pass would answer one enrolment request four times."""
    for endpoint in device.non_zdo_endpoints:
        cluster = endpoint.in_clusters.get(IAS_ZONE_CLUSTER)
        if cluster is None or getattr(cluster, _IAS_LISTENER_ATTRIBUTE, None) is not None:
            continue
        listener = _IasZoneListener(cluster)
        setattr(cluster, _IAS_LISTENER_ATTRIBUTE, listener)
        cluster.add_listener(listener)


# ------------------------------------------------------- sleepy end devices --


class _WakeUpWatcher:
    """Retries a device's deferred clusters the moment anything is heard
    from it.

    Right after a device transmits it polls its parent, so a queued request
    has its best chance THEN - and every incoming packet fires
    `device_last_seen_updated`. A fixed timer would be the obvious
    alternative and is the wrong one: the request would almost always arrive
    while the device was asleep again, and each such attempt costs ~28 s.

    `checkin` (PollControl client command 0) is the other moment worth
    taking: it is a sleepy device explicitly announcing that it is
    listening."""

    def __init__(
        self,
        device: Any,
        *,
        store: Any,
        now: Callable[[], float],
        polling: PollingSchedule | None,
    ) -> None:
        self._device = device
        self._store = store
        self._now = now
        self._polling = polling
        self._task: asyncio.Task[ConfigureOutcome] | None = None

    def device_last_seen_updated(self, last_seen: float) -> None:
        self._retry()

    def cluster_command(self, tsn: int, command_id: int, args: Any) -> None:
        if command_id == POLL_CONTROL_CHECKIN_COMMAND:
            self._retry()

    def _retry(self) -> None:
        if self._task is not None and not self._task.done():
            return
        try:
            self._task = asyncio.ensure_future(
                retry_pending(self._device, store=self._store, now=self._now, polling=self._polling)
            )
        except RuntimeError:  # pragma: no cover - no running loop
            self._task = None


def watch_for_wakeups(
    device: Any,
    *,
    store: Any,
    now: Callable[[], float] = time.time,
    polling: PollingSchedule | None = None,
) -> None:
    """Listens for the device saying anything at all, once per device."""
    if getattr(device, _WATCHER_ATTRIBUTE, None) is not None:
        return
    watcher = _WakeUpWatcher(device, store=store, now=now, polling=polling)
    setattr(device, _WATCHER_ATTRIBUTE, watcher)
    device.add_listener(watcher)
    for endpoint in device.non_zdo_endpoints:
        poll_control = endpoint.in_clusters.get(POLL_CONTROL_CLUSTER)
        if poll_control is not None:
            poll_control.add_listener(watcher)


async def retry_pending(
    device: Any,
    *,
    store: Any,
    now: Callable[[], float] = time.time,
    polling: PollingSchedule | None = None,
) -> ConfigureOutcome:
    """Works through whatever the pending table still says this device owes.

    Reads the debt from the store rather than from memory, which is the
    whole point of the table: the bridge may have restarted three times
    since the device last said anything."""
    address = str(device.ieee)
    endpoints = {endpoint.endpoint_id: endpoint for endpoint in device.non_zdo_endpoints}
    configured: list[int] = []
    deferred: list[int] = []
    for endpoint_id, cluster_id in store.zigbee_pending.pending_for(address):
        endpoint = endpoints.get(endpoint_id)
        cluster = None if endpoint is None else endpoint.in_clusters.get(cluster_id)
        if cluster is None:
            # The endpoint or cluster is gone - a reinterview reshaped the
            # device. The row would otherwise be retried forever.
            store.zigbee_pending.clear(address, endpoint_id, cluster_id)
            continue
        done = await _configure_cluster(
            device, endpoint, cluster, cluster_id, store=store, now=now, polling=polling
        )
        (configured if done else deferred).append(cluster_id)
    return ConfigureOutcome(
        configured=tuple(configured),
        deferred=tuple(deferred),
        quirk_applied=hasattr(device, "_quirk_registry_entry"),
    )


# ------------------------------------------------------ the one vocabulary --


def _is_unreachable(exc: BaseException) -> bool:
    """`source.py`'s failure vocabulary, and deliberately not a second one.

    Imported INSIDE the function on purpose: `source.py` imports this module
    at module level to run the routine on `device_initialized`, so a
    module-level import back would be a cycle. The names themselves stay in
    exactly one place, checked against the installed zigpy by
    `tests/zigbee/test_zigpy_names.py`."""
    from loxmatter.zigbee.source import _is_unreachable as source_is_unreachable

    return source_is_unreachable(exc)


def _describe(exc: BaseException | None) -> str:
    from loxmatter.zigbee.source import _describe as source_describe

    return source_describe(exc)
