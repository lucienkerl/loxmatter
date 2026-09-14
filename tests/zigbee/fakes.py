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

"""A stand-in for zigpy's ControllerApplication.

Every test of `ZigbeeSource` drives this instead of a radio. That is not
only because there is no Zigbee stick on the test Pi (design 10.3): zigpy's
own startup takes 7.5 s to time out against a silent port, and a suite that
paid that per test would be abandoned within a week.

It mimics exactly the behaviour the source depends on and nothing else:
listener registration, `startup` raising what research E.2 MEASURED, the
`connection_lost` callback, and a device catalogue that is readable while
the radio is gone.

**Every method here was checked against the installed zigpy 2.2.0 before it
was written, and `tests/zigbee/test_zigpy_names.py` keeps it honest.** A
fake that answers a call the library does not have is worse than no fake at
all: it makes a test green for behaviour that cannot happen. One such trap
was found while writing this file - `Cluster._attr_cache` is an
`AttributeCache` object in zigpy 2.2.0, not a `dict`, and it has no
`items()`, so a fake offering a plain dict there would have hidden an
`AttributeError` that every real device would have hit. The source reads the
cache through the public `Cluster.get(attribute_id)` instead, and that is
what this file offers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# `FakeDevice.__init__`'s default for `last_seen`: distinguishes "the
# caller did not pass one - use a fresh timestamp, the way a device that
# just finished its interview would" from an explicit `last_seen=None`,
# which is zigpy's own spelling for "this device has never sent a single
# packet" (`zigpy.device.Device.last_seen`, verified against zigpy 2.2.0).
# `availability.is_available` treats the two very differently, so a fake
# that could not tell them apart would hide exactly the branch that reads
# `None` as "never seen".
#
# A NAMED CLASS rather than a bare `object()`, so the parameter it defaults
# can be annotated truthfully. `last_seen: float | None = object()` is a lie
# no tool here catches - mypy is configured for `src/` only - and the next
# reader would reasonably conclude that passing `None` and passing nothing
# do the same thing.
class _LastSeenUnset:
    """The absence of an explicit `last_seen`, as a type."""


_LAST_SEEN_UNSET = _LastSeenUnset()


# ------------------------------------------------- zigpy's exception names --
# Deliberately the same NAMES, and the same hierarchy, as
# `zigpy.exceptions` (verified against zigpy 2.2.0: `DeliveryError` and
# `ControllerException` both derive from `ZigbeeException`, and so does
# `NetworkSettingsInconsistent`). The source matches on the names in an
# exception's MRO, so these stand in for the real ones without this suite
# importing zigpy.


class ZigbeeException(Exception):
    """Stands in for `zigpy.exceptions.ZigbeeException`."""


class DeliveryError(ZigbeeException):
    """Stands in for `zigpy.exceptions.DeliveryError`."""


class ControllerException(ZigbeeException):
    """Stands in for `zigpy.exceptions.ControllerException`.

    NOTE: the design calls this one `ControllerError`. zigpy 2.2.0 spells
    it `ControllerException`; the name here is the library's, not the
    document's."""


class NetworkSettingsInconsistent(ZigbeeException):
    """Stands in for `zigpy.exceptions.NetworkSettingsInconsistent`.

    Takes the message alone, where the real one takes
    `(message, new_state, old_state)` and carries both network backups.
    That difference is safe and deliberate: the source only ever CATCHES
    this exception, by name, and never builds one - and a fake that
    demanded two backup objects would say nothing more than this comment
    does. `tests/zigbee/test_zigpy_names.py` builds the real one."""


# --------------------------------------------------------- command schemas --


@dataclass(frozen=True)
class FakeAttributeDef:
    """What `Cluster.attributes[attribute_id]` answers.

    zigpy's real `ZCLAttributeDef` carries far more, but the two fields
    below are the two the source touches - and, crucially, the fact that it
    is an OBJECT rather than an int is what makes `FakeCluster.get` able to
    tell a definition lookup from an id lookup, the same way
    `Cluster.find_attribute` does."""

    id: int
    name: str


@dataclass(frozen=True)
class FakeField:
    """One field of a ZCL command schema (`schema.fields[n].name`)."""

    name: str


@dataclass(frozen=True)
class FakeSchema:
    fields: tuple[FakeField, ...]


@dataclass(frozen=True)
class FakeCommandDef:
    """What `Cluster.server_commands[command_id]` answers.

    The field names below are the ones the installed zigpy declares - among
    them `color_temp_mireds` (NOT `color_temperature_mireds`) and a
    `move_to_level_with_on_off` that carries no options fields at all."""

    id: int
    name: str
    schema: FakeSchema


def command_def(command_id: int, name: str, *field_names: str) -> FakeCommandDef:
    return FakeCommandDef(
        id=command_id,
        name=name,
        schema=FakeSchema(fields=tuple(FakeField(field_name) for field_name in field_names)),
    )


# The three commands the tests send, with zigpy 2.2.0's own field names.
ON_OFF_COMMANDS: dict[int, FakeCommandDef] = {
    0: command_def(0, "off"),
    1: command_def(1, "on"),
    2: command_def(2, "toggle"),
}
LEVEL_COMMANDS: dict[int, FakeCommandDef] = {
    0: command_def(
        0, "move_to_level", "level", "transition_time", "options_mask", "options_override"
    ),
    # No options fields: verified against zigpy 2.2.0.
    4: command_def(4, "move_to_level_with_on_off", "level", "transition_time"),
}
COLOR_COMMANDS: dict[int, FakeCommandDef] = {
    6: command_def(
        6,
        "move_to_hue_and_saturation",
        "hue",
        "saturation",
        "transition_time",
        "options_mask",
        "options_override",
    ),
    7: command_def(
        7,
        "move_to_color",
        "color_x",
        "color_y",
        "transition_time",
        "options_mask",
        "options_override",
    ),
    10: command_def(
        10,
        "move_to_color_temp",
        "color_temp_mireds",
        "transition_time",
        "options_mask",
        "options_override",
    ),
}


@dataclass
class FakeDefaultResponse:
    """What `Cluster.command()` answers: a ZCL Default Response whose
    `status` is 0 for SUCCESS."""

    status: int = 0


@dataclass
class FakeWriteStatusRecord:
    """One record of `Cluster.write_attributes`' answer."""

    status: int = 0


@dataclass(frozen=True)
class FakeAttributeEvent:
    """One of zigpy's four attribute events.

    Field names copied from `zigpy.zcl.AttributeReportedEvent` and its three
    siblings."""

    event_type: str
    device_ieee: str
    endpoint_id: int
    cluster_type: int
    cluster_id: int
    attribute_id: int
    value: Any


# -------------------------------------------------------------- the cluster --


class FakeCluster:
    """One in-cluster of one endpoint."""

    def __init__(
        self,
        cluster_id: int,
        *,
        declared: Iterable[int] = (),
        cached: Mapping[int, Any] | None = None,
        commands: Mapping[int, FakeCommandDef] | None = None,
        readable: Mapping[int, Any] | None = None,
        shadowed: Mapping[int, Any] | None = None,
    ) -> None:
        self.cluster_id = cluster_id
        # `Cluster.attributes` is a dict of attribute ID -> DEFINITION, and
        # the definition is what `Cluster.get` must be handed - see `get`.
        self.attributes: dict[int, FakeAttributeDef] = {
            attribute_id: FakeAttributeDef(attribute_id, f"attribute_{attribute_id:#06x}")
            for attribute_id in {*declared, *(cached or {}), *(readable or {})}
        }
        # Attribute ids for which this cluster class declares TWO
        # definitions - one standard, one manufacturer-specific - the way
        # every `zhaquirks/ubisys/` cluster does. `attributes` shows only
        # the survivor; `Cluster._attributes_by_id` keeps both, which is
        # what makes a lookup BY ID ambiguous.
        #
        # The value mapped here is the SURVIVOR's cached value, and it is
        # deliberately not the same object as `cached[id]`: on a real
        # `UbisysLevelControl` the survivor is `minimum_on_level`, so
        # `_attr_cache.get_value(definition)` answers the minimum-on level
        # while `cached[0x0000]` is what `current_level` would have said.
        # Modelling the two separately is the only way a test can measure
        # the residual the fix leaves behind rather than assume it.
        self.shadowed: dict[int, Any] = dict(shadowed or {})
        self.server_commands: dict[int, FakeCommandDef] = dict(commands or {})
        self._cached: dict[int, Any] = dict(cached or {})
        # What a read over the air would answer. An attribute that is
        # neither cached nor readable is a device that does not answer.
        self.readable: dict[int, Any] = dict(readable or {})
        self.reads: list[list[int]] = []
        # Whether each of those reads allowed the cache. A stale value in
        # `cached` and a fresh one in `readable` is the only way to tell a
        # cached read from a real one, which is exactly zigpy's own
        # behaviour: `allow_cache=True` answers from `_attr_cache` and skips
        # the request entirely for anything it already holds.
        self.cached_reads: list[bool] = []
        self.sent: list[tuple[int, dict[str, Any]]] = []
        self.read_error: Exception | None = None
        self.command_error: Exception | None = None
        self.command_status: int = 0
        self._listeners: dict[str, list[Callable[[Any], None]]] = {}
        self.endpoint: FakeEndpoint | None = None

        # -- configure-on-join ---------------------------------------------
        # How often `bind()` was reached, what `configure_reporting_multiple`
        # was handed, and what it should answer. `reporting_statuses` is per
        # ATTRIBUTE ID, because that is the granularity zigpy answers at: a
        # lamp that refuses colour temperature and accepts on/off is a real
        # device, not a hypothesis.
        self.binds: int = 0
        self.bind_error: Exception | None = None
        self.reporting: list[dict[Any, Any]] = []
        self.reporting_error: Exception | None = None
        self.reporting_statuses: dict[int, int] = {}
        self.writes: list[dict[Any, Any]] = []
        self.write_error: Exception | None = None
        # `zigpy.util.ListenableMixin`, which is a SEPARATE mechanism from
        # the `EventBase` one above: `add_listener` registers an object and
        # `listener_event` calls it by method name. Client commands - among
        # them an IAS alarm - arrive only this way.
        self.command_listeners: list[Any] = []

    # -- what zigpy's `EventBase` offers -----------------------------------

    def on_event(self, event_name: str, callback: Callable[[Any], None]) -> Callable[[], None]:
        listeners = self._listeners.setdefault(event_name, [])
        listeners.append(callback)

        def unsubscribe() -> None:
            if callback in listeners:
                listeners.remove(callback)

        return unsubscribe

    def emit(self, event_name: str, event: Any) -> None:
        """Calls every listener SYNCHRONOUSLY and catches nothing.

        That is `zigpy.event.event_base.EventBase.emit` exactly (verified
        against zigpy 2.2.0), and it is the whole reason the source's
        callbacks may do nothing but `put_nowait`: an exception raised in
        here escapes into whatever zigpy was doing when the attribute
        arrived."""
        for callback in list(self._listeners.get(event_name, [])):
            callback(event)

    @property
    def listener_count(self) -> int:
        return sum(len(listeners) for listeners in self._listeners.values())

    # -- what zigpy's `ListenableMixin` offers ------------------------------

    def add_listener(self, listener: Any) -> int:
        self.command_listeners.append(listener)
        return id(listener)

    def listener_event(self, method_name: str, *args: Any) -> None:
        """`zigpy.util.ListenableMixin.listener_event` - by method name,
        with every listener exception CAUGHT AND DISCARDED (verified against
        zigpy 2.2.0).

        The swallowing is the point: a handler that raises here goes
        silently dead, which is why every method name this bridge relies on
        is pinned in `test_zigpy_names.py`."""
        for listener in list(self.command_listeners):
            method = getattr(listener, method_name, None)
            if method is None:
                continue
            try:
                method(*args)
            except Exception:
                logger.debug("listener %r raised on %s", listener, method_name, exc_info=True)

    def receive_command(self, command_id: int, args: Any, *, tsn: int = 7) -> None:
        """A CLIENT command arriving from the device.

        `Cluster.handle_message` dispatches a cluster-frame command as
        `listener_event("cluster_command", hdr.tsn, hdr.command_id, args)`
        and NOT as any attribute event - which is exactly why an IAS alarm
        is invisible to a bridge that only subscribes to reports."""
        self.listener_event("cluster_command", tsn, command_id, args)

    # -- what the source reads ---------------------------------------------

    def get(self, key: int | FakeAttributeDef, default: Any | None = None) -> Any:
        """`Cluster.get` - the cache, never the air, and it RAISES.

        zigpy's `Cluster.get` calls `find_attribute(key)` outside its own
        `try`, so two lookups by bare id raise `KeyError` rather than
        answering the default:

        - an id this cluster class does not declare at all, and
        - an id with two definitions (`shadowed` above), where zigpy says
          "Multiple definitions exist for attribute ID ..., please specify a
          manufacturer code".

        A lookup by DEFINITION raises neither: `find_attribute` returns the
        first candidate at once for any non-integer key - and answers with
        THAT definition's cached value, which for a shadowed id is the
        survivor's and not the standard attribute's.

        This fake used to answer `self._cached.get(attribute_id, default)`
        for everything, and that forgiveness is exactly why a crash that
        would have stopped the bridge from starting on any Ubisys device sat
        in `_endpoint_facts` behind a green suite."""
        if isinstance(key, FakeAttributeDef):
            if key.id in self.shadowed:
                return self.shadowed[key.id]
            return self._cached.get(key.id, default)
        if key in self.shadowed:
            raise KeyError(
                f"Multiple definitions exist for attribute ID {key:#06x}, "
                f"please specify a manufacturer code"
            )
        if key not in self.attributes:
            raise KeyError(key)
        return self._cached.get(key, default)

    # -- what configure-on-join does ---------------------------------------

    def _note(self, what: str) -> None:
        """Appends to the device's ordered journal, when there is one.

        Order is the only way to measure "the quirk hook runs before
        anything else" and "fast poll mode brackets the WHOLE routine";
        counting calls cannot tell a correct pass from one that does the
        right things in the wrong sequence."""
        endpoint = self.endpoint
        device = None if endpoint is None else endpoint.device
        if device is not None:
            device.journal.append(what)

    async def bind(self, **kwargs: Any) -> list[int]:
        """`Cluster.bind()` - the ZDO Bind_req for this cluster.

        Real zigpy resolves this through `self._endpoint.device.zdo.bind`,
        so a quirk that overrides `bind` (and several do - the whole purpose
        of `TuyaNoBindPowerConfigurationCluster` is to NOT send it) is only
        honoured when the call goes through the cluster OBJECT."""
        self._note(f"bind:{self.cluster_id:#06x}")
        self.binds += 1
        if self.bind_error is not None:
            raise self.bind_error
        return [0]

    async def configure_reporting_multiple(self, config: Mapping[Any, Any]) -> dict[Any, int]:
        """`Cluster.configure_reporting_multiple(dict[ZCLAttributeDef, ReportingConfig])`.

        Keyed by the attribute DEFINITION, exactly as zigpy 2.2.0 is: it
        reads `attr_def.id` and `attr_def.zcl_type` off every key. A bare
        integer key raises here for the same reason it would raise there,
        rather than being quietly accepted - a fake that forgave it would
        hide the `find_attribute` trap that 28 shipped quirks walk into.

        The answer is a status PER ATTRIBUTE, which is also zigpy's: a lamp
        that accepts on/off and refuses colour temperature answers exactly
        that."""
        for key in config:
            if not isinstance(key, FakeAttributeDef):
                raise TypeError(
                    f"{key!r} has no attribute 'id' - configure_reporting_multiple is "
                    f"keyed by the attribute definition, not by its id"
                )
        self._note(f"report:{self.cluster_id:#06x}")
        self.reporting.append(dict(config))
        if self.reporting_error is not None:
            raise self.reporting_error
        return {definition: self.reporting_statuses.get(definition.id, 0) for definition in config}

    async def write_attributes(
        self, attributes: Mapping[Any, Any], **kwargs: Any
    ) -> list[list[FakeWriteStatusRecord]]:
        self._note(f"write:{self.cluster_id:#06x}")
        self.writes.append(dict(attributes))
        if self.write_error is not None:
            raise self.write_error
        for key, value in attributes.items():
            attribute_id = key.id if isinstance(key, FakeAttributeDef) else key
            if attribute_id not in self.attributes:
                # `Cluster.write_attributes` resolves every key through
                # `find_attribute`, which raises for an id the cluster does
                # not declare. Forgiving that here would let a write to the
                # wrong attribute number pass the whole suite.
                raise KeyError(attribute_id)
            self._cached[attribute_id] = value
        return [[FakeWriteStatusRecord(status=0)]]

    def update_attribute(self, attribute_id: int, value: Any) -> None:
        """`Cluster.update_attribute` - the cache, then the
        `attribute_updated` event, which is how a value that arrived as a
        COMMAND becomes indistinguishable from a reported one."""
        self.report(attribute_id, value, event="attribute_updated")

    async def read_attributes(
        self, attributes: list[int], allow_cache: bool = False
    ) -> tuple[dict[int, Any], dict[int, Any]]:
        self._note(f"read:{self.cluster_id:#06x}")
        self.reads.append(list(attributes))
        self.cached_reads.append(bool(allow_cache))
        if self.read_error is not None:
            raise self.read_error
        success: dict[int, Any] = {}
        failure: dict[int, Any] = {}
        for attribute_id in attributes:
            if allow_cache and attribute_id in self._cached:
                success[attribute_id] = self._cached[attribute_id]
            elif attribute_id in self.readable:
                value = self.readable[attribute_id]
                self._cached[attribute_id] = value
                success[attribute_id] = value
            else:
                failure[attribute_id] = 0x86  # UNSUPPORTED_ATTRIBUTE
        return success, failure

    async def command(self, command_id: int, **kwargs: Any) -> FakeDefaultResponse:
        self._note(f"command:{self.cluster_id:#06x}:{command_id}")
        self.sent.append((command_id, dict(kwargs)))
        if self.command_error is not None:
            raise self.command_error
        return FakeDefaultResponse(status=self.command_status)

    # -- what a device does ------------------------------------------------

    def report(self, attribute_id: int, value: Any, *, event: str = "attribute_report") -> None:
        """A reported value: the cache first, then the event.

        That order is zigpy's (`Cluster._update_attribute` calls
        `set_value` and only then `emit`), and the source depends on it -
        it reads the cache when the event wakes it."""
        self._cached[attribute_id] = value
        self.attributes.setdefault(
            attribute_id, FakeAttributeDef(attribute_id, f"attribute_{attribute_id:#06x}")
        )
        endpoint = self.endpoint
        self.emit(
            event,
            FakeAttributeEvent(
                event_type=event,
                device_ieee="" if endpoint is None else endpoint.device_ieee,
                endpoint_id=0 if endpoint is None else endpoint.endpoint_id,
                cluster_type=0,
                cluster_id=self.cluster_id,
                attribute_id=attribute_id,
                value=value,
            ),
        )


# ------------------------------------------------------------- the endpoint --


class FakeEndpoint:
    def __init__(
        self,
        endpoint_id: int,
        *,
        profile_id: int | None,
        device_type: int | None,
        in_clusters: Iterable[FakeCluster] = (),
        out_clusters: Iterable[FakeCluster] = (),
    ) -> None:
        self.endpoint_id = endpoint_id
        self.profile_id = profile_id
        self.device_type = device_type
        self.in_clusters: dict[int, FakeCluster] = {
            cluster.cluster_id: cluster for cluster in in_clusters
        }
        # A device's CLIENT-side clusters - the classic TRADFRI motion
        # sensor's whole reason for being unable to report anything without
        # `configure.py`'s dedicated handling: it sends `OnOff` commands
        # from here, not attribute reports from `in_clusters`.
        self.out_clusters: dict[int, FakeCluster] = {
            cluster.cluster_id: cluster for cluster in out_clusters
        }
        self.device_ieee = ""
        # Set by `FakeDevice.__init__`. `Cluster.endpoint.device` is the
        # path zigpy itself uses (`Cluster.bind` reaches the ZDO through
        # it), and it is what lets a cluster write into the device's
        # ordered journal.
        self.device: FakeDevice | None = None
        for cluster in (*self.in_clusters.values(), *self.out_clusters.values()):
            cluster.endpoint = self


@dataclass
class FakeNodeDescriptor:
    """The three `zigpy.zdo.types.NodeDescriptor` properties this bridge
    reads.

    All three are derived properties over the same two bytes in the real
    descriptor - `is_mains_powered` and `is_receiver_on_when_idle` are two
    separate bits of `mac_capability_flags`, `is_coordinator` reads
    `logical_type`. Verified against zigpy 2.2.0:
    `NodeDescriptor(logical_type=LogicalType.Coordinator,
    mac_capability_flags=0x8E)` answers `True` to all three, one built with
    `LogicalType.EndDevice` and `0x80` answers `False` to all three, and a
    descriptor with no fields set at all - what an uninterviewed device
    carries - answers `None`, never `False`.

    `is_receiver_on_when_idle` defaults to MIRRORING `is_mains_powered`,
    which is what the two bits do on every mains-powered router: a device
    that is plugged in keeps its receiver on. The point of the separate
    field is that a test can pull them apart, because nothing in the
    protocol ties them together."""

    is_mains_powered: bool = True
    is_coordinator: bool | None = False
    is_receiver_on_when_idle: bool | None = None

    def __post_init__(self) -> None:
        if self.is_receiver_on_when_idle is None:
            self.is_receiver_on_when_idle = self.is_mains_powered


# --------------------------------------------------------------- the device --


class FakeDevice:
    def __init__(
        self,
        ieee: str,
        *,
        manufacturer: str = "IKEA of Sweden",
        model: str = "TRADFRI bulb",
        endpoints: Iterable[FakeEndpoint] = (),
        node_desc: FakeNodeDescriptor | None = None,
        quirk_applied: bool = False,
        last_seen: float | None | _LastSeenUnset = _LAST_SEEN_UNSET,
        custom_configuration: bool = False,
        skip_configuration: bool = False,
        fast_poll: bool = True,
    ) -> None:
        self.ieee = ieee
        self.nwk = 0x1234
        self.manufacturer = manufacturer
        self.model = model
        self.node_desc = node_desc if node_desc is not None else FakeNodeDescriptor()
        self.last_seen: float | None = (
            time.time() if isinstance(last_seen, _LastSeenUnset) else last_seen
        )
        self.is_initialized = True
        # How often `schedule_initialize()` really started an interview -
        # which, exactly as in zigpy, is never for a device that is already
        # initialized. See that method.
        self.initializations = 0
        # Everything the device was asked to do, in order. See
        # `FakeCluster._note`.
        self.journal: list[str] = []
        self.application: FakeApplication | None = None
        # `Device.skip_configuration` is a plain property on the real
        # thing; 79 shipped quirks set it, and they set it because binding
        # or configuring reporting actively breaks those devices.
        self.skip_configuration = skip_configuration
        self.command_listeners: list[Any] = []
        self._fast_poll = fast_poll
        if custom_configuration:
            # `apply_custom_configuration` exists ONLY on a quirked device
            # (`CustomDevice`, `CustomZigpyDevice`); the base
            # `zigpy.device.Device` has no such method at all, which is why
            # the routine tests for it with `hasattr` rather than calling
            # it blind.
            self.apply_custom_configuration = self._apply_custom_configuration
        self._endpoints = list(endpoints)
        for endpoint in self._endpoints:
            endpoint.device_ieee = ieee
            endpoint.device = self
        # zigpy keeps the ZDO at endpoint 0, which carries no ZCL clusters -
        # hence `non_zdo_endpoints`, which is what the source iterates.
        self.endpoints: dict[int, Any] = {0: object()}
        self.endpoints.update({endpoint.endpoint_id: endpoint for endpoint in self._endpoints})
        if quirk_applied:
            # The attribute `zha.quirks.DeviceRegistry.resolve` sets on a
            # device it has transformed (`QUIRK_REGISTRY_ENTRY_ATTR`).
            self._quirk_registry_entry = object()
        self.listeners: list[Any] = []

    @property
    def non_zdo_endpoints(self) -> list[FakeEndpoint]:
        return list(self._endpoints)

    async def _apply_custom_configuration(self, *args: Any, **kwargs: Any) -> None:
        """The Tuya "spell". On a real quirked device this walks every
        custom cluster and calls its own `apply_custom_configuration`, which
        for the Tuya quirks is a specific `Basic` read of
        `[4, 0, 1, 5, 7, 0xFFFE]` - without which many of those devices
        never send anything at all."""
        self.journal.append("apply_custom_configuration")

    @property
    def fast_poll_mode(self) -> Any:
        """`Device.fast_poll_mode()` - an ASYNC CONTEXT MANAGER on zigpy
        2.2.0, not a coroutine, which is what lets it bracket a whole
        routine instead of one call.

        Absent entirely when the device was built with `fast_poll=False`,
        so a test can drive an object that has none."""
        if not self._fast_poll:
            raise AttributeError("fast_poll_mode")
        return self._fast_poll_mode

    @contextlib.asynccontextmanager
    async def _fast_poll_mode(self, initial_timeout: float = 4.0) -> AsyncIterator[None]:
        self.journal.append("fast_poll:start")
        try:
            yield
        finally:
            self.journal.append("fast_poll:stop")

    def cancel_initialization(self) -> None:
        """`Device.cancel_initialization()` - cancels an interview that is
        still running. A no-op here beyond the record, because this fake
        never has one running."""
        self.journal.append("cancel_initialization")

    def schedule_initialize(self) -> None:
        """`Device.schedule_initialize()`, in the two shapes zigpy 2.2.0
        gives it.

        An already-initialized device is NOT re-interviewed: zigpy returns
        early and calls `self._application.device_initialized(self)`, which
        re-announces it. Only a device whose interview never finished -
        `is_initialized` is `node_desc is not None and all_endpoints_init` -
        actually starts one, and it starts it as a TASK, which is why the
        method is synchronous and why nothing here awaits an interview."""
        self.journal.append("schedule_initialize")
        self.cancel_initialization()
        if self.is_initialized:
            if self.application is not None:
                self.application.device_initialized(self)
            return
        self.initializations += 1

    def add_listener(self, listener: Any) -> int:
        self.listeners.append(listener)
        self.command_listeners.append(listener)
        return id(listener)

    def listener_event(self, method_name: str, *args: Any) -> None:
        """`zigpy.util.ListenableMixin.listener_event` on the DEVICE, which
        is where `device_last_seen_updated` is announced."""
        for listener in list(self.command_listeners):
            method = getattr(listener, method_name, None)
            if method is None:
                continue
            try:
                method(*args)
            except Exception:
                logger.debug("listener %r raised on %s", listener, method_name, exc_info=True)

    def heard_from(self, moment: float | None = None) -> None:
        """A packet arrived. zigpy sets `last_seen` and fires
        `device_last_seen_updated` with the new timestamp - EVERY incoming
        packet does, which is what makes it the cheapest possible signal
        that a sleepy device is awake right now."""
        self.last_seen = time.time() if moment is None else moment
        self.listener_event("device_last_seen_updated", self.last_seen)

    def clusters(self) -> list[FakeCluster]:
        return [
            cluster
            for endpoint in self._endpoints
            for cluster in (*endpoint.in_clusters.values(), *endpoint.out_clusters.values())
        ]


# ---------------------------------------------------------- the application --


@dataclass
class FakeNodeInfo:
    """`zigpy.state.NodeInfo`, as far as this bridge reads it. Every field
    name here is checked against the real dataclass in
    `test_zigpy_names.py`; the three text values are what bellows fills in
    from an EFR32 stick with EmberZNet firmware."""

    ieee: str = "00:12:4b:00:ff:ee:dd:cc"
    nwk: int = 0x0000
    manufacturer: str | None = "ITEAD"
    model: str | None = "SONOFF Zigbee 3.0 USB Dongle Plus V2"
    version: str | None = "7.4.4.0 build 0"


@dataclass
class FakeNetworkInfo:
    """`zigpy.state.NetworkInfo`, as far as the connect line reads it. The
    field names are checked against the real dataclass in
    `test_zigpy_names.py`. Deliberately no key: nothing here may read one."""

    channel: int = 15
    pan_id: int = 0x1A62
    extended_pan_id: str = "dd:dd:dd:dd:dd:dd:dd:dd"


@dataclass
class FakeApplicationState:
    """`zigpy.state.State`. `node_info` and `network_info` are read here."""

    node_info: FakeNodeInfo = field(default_factory=FakeNodeInfo)
    network_info: FakeNetworkInfo = field(default_factory=FakeNetworkInfo)


@dataclass
class FakeBackups:
    """`zigpy.backups.BackupManager`: the network backups zigpy's database
    holds, newest last."""

    backups: list[Any] = field(default_factory=list)

    def most_recent_backup(self) -> Any | None:
        return self.backups[-1] if self.backups else None


class FakeApplication:
    """`zigpy.application.ControllerApplication`, as far as the source
    reaches into it."""

    def __init__(
        self,
        *,
        devices: Iterable[FakeDevice] = (),
        startup_error: BaseException | None = None,
        permit_error: BaseException | None = None,
        remove_error: BaseException | None = None,
    ) -> None:
        self.devices: dict[str, FakeDevice] = {device.ieee: device for device in devices}
        for device in self.devices.values():
            device.application = self
        # `app.state.node_info.ieee` - the coordinator's own address, which
        # is what an IAS sensor has to be told to send its alarms to.
        self.state = FakeApplicationState()
        # zigpy's network backups, read before `startup()` to tell a network
        # this database already knew from one it did not.
        self.backups = FakeBackups()
        # Every priority the application was asked to hold, in order, and
        # whether it is holding one right now. `PacketPriority.HIGH` is 1.
        self.priorities: list[int] = []
        self.priority_depth: int = 0
        self.startup_error = startup_error
        self.permit_error = permit_error
        self.remove_error = remove_error
        # Duration -> how many event-loop rounds `permit` yields for before
        # it answers. See that method.
        self.permit_delays: dict[int, int] = {}
        self.config: dict[str, Any] = {}
        self.listeners: list[Any] = []
        self.startup_calls: list[bool] = []
        self.shutdown_calls: list[bool] = []
        self.permits: list[tuple[int, Any]] = []
        self.removed: list[str] = []
        self.started = False
        # bellows' own flag. Set on startup and NEVER cleared afterwards -
        # not by `connection_lost`, not by `shutdown` (R1 section 2). It
        # exists here only so `test_connected_is_an_explicit_flag_cleared_on_loss`
        # has something wrong to be tempted by.
        self.is_running = False

    @contextlib.asynccontextmanager
    async def request_priority(self, priority: int) -> AsyncIterator[None]:
        """`ControllerApplication.request_priority(priority)`, an async
        context manager in zigpy 2.2.0. Everything sent inside it jumps the
        per-device queue, which is what a device that is awake right now
        and will not be again for hours deserves."""
        self.priorities.append(priority)
        self.priority_depth += 1
        try:
            yield
        finally:
            self.priority_depth -= 1

    def add_listener(self, listener: Any) -> int:
        self.listeners.append(listener)
        return id(listener)

    def remove_listener(self, listener: Any) -> None:
        if listener in self.listeners:
            self.listeners.remove(listener)

    async def startup(self, *, auto_form: bool = False) -> None:
        self.startup_calls.append(auto_form)
        if self.startup_error is not None:
            raise self.startup_error
        self.started = True
        self.is_running = True

    async def shutdown(self, *, db: bool = True) -> None:
        self.shutdown_calls.append(db)
        self.started = False

    async def permit(self, time_s: int = 60, node: Any = None) -> None:
        """`ControllerApplication.permit`, which on a real radio is a ZDO
        broadcast plus a call into the NCP - two awaits, not one.

        `permit_delays` is what makes that visible: it maps a duration onto
        the number of event-loop rounds this call yields for before it
        returns, so a test can have a Stop overtake a longer window that is
        still in flight. Without a way to reorder them, two overlapping
        permits are indistinguishable from two sequential ones and the
        source's lock could not be measured at all."""
        for _ in range(self.permit_delays.get(time_s, 0)):
            await asyncio.sleep(0)
        if self.permit_error is not None:
            raise self.permit_error
        self.permits.append((time_s, node))

    async def remove(self, ieee: Any, remove_children: bool = True, rejoin: bool = False) -> None:
        if self.remove_error is not None:
            raise self.remove_error
        self.removed.append(str(ieee))
        device = self.devices.pop(str(ieee), None)
        # zigpy deletes the device from its database whether or not the
        # leave request is ever delivered, and announces it with
        # `device_removed`. It does NOT wait for a leave confirmation, and
        # this fake deliberately never sends one.
        if device is not None:
            self.listener_event("device_removed", device)

    def listener_event(self, method_name: str, *args: Any) -> None:
        """`zigpy.util.ListenableMixin.listener_event`: every listener, by
        method name, with each one's exception caught and logged rather
        than propagated (verified against zigpy 2.2.0)."""
        for listener in list(self.listeners):
            method = getattr(listener, method_name, None)
            if method is None:
                continue
            try:
                method(*args)
            except Exception:
                logger.debug("listener %r raised on %s", listener, method_name, exc_info=True)

    def fire_connection_lost(self, exc: BaseException | None = None) -> None:
        """What bellows does when the serial link dies, and what its
        watchdog does after four failed feeds: one event, exactly once, and
        no reconnection attempt of its own."""
        self.listener_event("connection_lost", exc if exc is not None else OSError("link lost"))

    def device_initialized(self, device: FakeDevice) -> None:
        """`ControllerApplication.device_initialized(device)` - a real
        METHOD on the application, not a listener event, and the one
        `Device.schedule_initialize()` calls for a device that is already
        fully interviewed. It announces the device through the listener
        event of the same name."""
        self.fire_device_initialized(device)

    def fire_device_initialized(self, device: FakeDevice) -> None:
        self.devices[device.ieee] = device
        device.application = self
        self.listener_event("device_initialized", device)

    def fire_device_init_failure(self, device: FakeDevice) -> None:
        """An interview that gave up.

        `zigpy.device.Device.initialize` catches `TimeoutError` and
        `ZigbeeException` and announces this on the APPLICATION's listeners
        - `self.application.listener_event("device_init_failure", self)` -
        even though it is the device that emits it. The device stays in
        `app.devices` and is NOT initialized, which is what lets a Retry
        interview it again."""
        self.devices[device.ieee] = device
        device.application = self
        device.is_initialized = False
        self.listener_event("device_init_failure", device)

    def fire_device_joined(self, device: FakeDevice) -> None:
        device.application = self
        self.listener_event("device_joined", device)

    def fire_device_reinterviewed(self, replacement: FakeDevice) -> None:
        """zigpy's reinterview, in the order `ControllerApplication._device_reinterviewed`
        performs it (verified against zigpy 2.2.0).

        The old device is dropped and a BRAND-NEW object with brand-new
        clusters takes its place under the same IEEE, and the event that
        announces it is `device_reinterviewed` - deliberately NOT
        `device_initialized`, as zigpy's own comment in that method says.
        A bridge with no method by that name keeps its listeners on an
        object nobody will ever report through again."""
        self.devices[replacement.ieee] = replacement
        replacement.application = self
        self.listener_event("device_reinterviewed", replacement)


@dataclass
class FakeApplicationFactory:
    """Hands out prepared applications, and records every config it was
    asked to build one from."""

    applications: list[FakeApplication] = field(default_factory=list)
    configs: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0

    async def __call__(self, config: dict[str, Any]) -> FakeApplication:
        self.configs.append(config)
        self.calls += 1
        if self.applications:
            application = self.applications.pop(0)
        else:
            application = FakeApplication()
        application.config = config
        return application


# ------------------------------------------------------------ ready-made kit --


def colour_lamp(
    ieee: str = "00:12:4b:00:1c:a1:b2:c3",
    *,
    colour_capabilities: int | None = 0x1F,
    readable_capabilities: int | None = None,
) -> FakeDevice:
    """A colour lamp on endpoint 1 - the shape of the hardware this
    feature is for.

    `colour_capabilities` is what the interview already left in the cache;
    `readable_capabilities` is what a read over the air would answer. A lamp
    with neither is one whose colour picker disappears, which is what
    `test_colour_capabilities_reach_the_endpoint_facts` is about."""
    cached: dict[int, Any] = {0x0000: 1, 0x0001: 2, 0x0007: 370}
    if colour_capabilities is not None:
        cached[0x400A] = colour_capabilities
    readable: dict[int, Any] = {}
    if readable_capabilities is not None:
        readable[0x400A] = readable_capabilities
    colour = FakeCluster(
        0x0300,
        declared=[0x0000, 0x0001, 0x0003, 0x0004, 0x0007, 0x400A],
        cached=cached,
        commands=COLOR_COMMANDS,
        readable=readable,
    )
    return FakeDevice(
        ieee,
        endpoints=[
            FakeEndpoint(
                1,
                profile_id=0x0104,
                device_type=0x0102,
                in_clusters=[
                    FakeCluster(
                        0x0006, declared=[0x0000], cached={0x0000: True}, commands=ON_OFF_COMMANDS
                    ),
                    FakeCluster(
                        0x0008, declared=[0x0000], cached={0x0000: 254}, commands=LEVEL_COMMANDS
                    ),
                    colour,
                ],
            )
        ],
    )


def contact_sensor(ieee: str = "00:15:8d:00:02:aa:bb:cc") -> FakeDevice:
    """A battery contact sensor: IAS Zone, and a battery percentage on the
    endpoint Zigbee keeps it on."""
    return FakeDevice(
        ieee,
        manufacturer="LUMI",
        model="lumi.sensor_magnet",
        node_desc=FakeNodeDescriptor(is_mains_powered=False),
        endpoints=[
            FakeEndpoint(
                1,
                profile_id=0x0104,
                device_type=0x0402,
                in_clusters=[
                    FakeCluster(
                        0x0500,
                        declared=[0x0001, 0x0002],
                        cached={0x0001: 0x0015, 0x0002: 0},
                    ),
                    FakeCluster(0x0001, declared=[0x0020, 0x0021], cached={0x0021: 150}),
                ],
            )
        ],
    )


def tradfri_motion_sensor(ieee: str = "d0:cf:5e:ff:fe:71:a3:19") -> FakeDevice:
    """The classic IKEA TRADFRI motion sensor (E1525, E1745): no IAS Zone
    cluster and no OccupancySensing cluster at all - `OnOff` is its OUTPUT
    cluster, and reporting motion means sending `on`/`off` commands the way
    it would to a bound lamp (`zhaquirks/ikea/motion.py`, `motionzha.py`)."""
    return FakeDevice(
        ieee,
        manufacturer="IKEA of Sweden",
        model="TRADFRI motion sensor",
        node_desc=FakeNodeDescriptor(is_mains_powered=False),
        endpoints=[
            FakeEndpoint(
                1,
                profile_id=0x0104,
                device_type=0x0850,
                in_clusters=[FakeCluster(0x0001, declared=[0x0020, 0x0021], cached={0x0021: 150})],
                out_clusters=[FakeCluster(0x0006, declared=[0x0000], commands=ON_OFF_COMMANDS)],
            )
        ],
    )


class FakeNoBindCluster(FakeCluster):
    """A quirk that overrides `bind` to do nothing.

    `zhaquirks`' `TuyaNoBindPowerConfigurationCluster` is the real one, and
    its entire purpose is that the bind must NOT be sent - some Tuya devices
    stop answering afterwards. A bind that reaches the base class instead of
    this override is the very defect the quirk exists to prevent, and it is
    invisible to anything that only counts binds."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.no_bind_calls = 0

    async def bind(self, **kwargs: Any) -> list[int]:
        self._note(f"no-bind:{self.cluster_id:#06x}")
        self.no_bind_calls += 1
        return [0]
