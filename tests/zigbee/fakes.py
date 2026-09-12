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

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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
        self.sent: list[tuple[int, dict[str, Any]]] = []
        self.read_error: Exception | None = None
        self.command_error: Exception | None = None
        self.command_status: int = 0
        self._listeners: dict[str, list[Callable[[Any], None]]] = {}
        self.endpoint: FakeEndpoint | None = None

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

    async def read_attributes(
        self, attributes: list[int], allow_cache: bool = False
    ) -> tuple[dict[int, Any], dict[int, Any]]:
        self.reads.append(list(attributes))
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
    ) -> None:
        self.endpoint_id = endpoint_id
        self.profile_id = profile_id
        self.device_type = device_type
        self.in_clusters: dict[int, FakeCluster] = {
            cluster.cluster_id: cluster for cluster in in_clusters
        }
        self.out_clusters: dict[int, FakeCluster] = {}
        self.device_ieee = ""
        for cluster in self.in_clusters.values():
            cluster.endpoint = self


@dataclass
class FakeNodeDescriptor:
    is_mains_powered: bool = True


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
    ) -> None:
        self.ieee = ieee
        self.nwk = 0x1234
        self.manufacturer = manufacturer
        self.model = model
        self.node_desc = node_desc if node_desc is not None else FakeNodeDescriptor()
        self.is_initialized = True
        self._endpoints = list(endpoints)
        for endpoint in self._endpoints:
            endpoint.device_ieee = ieee
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

    def add_listener(self, listener: Any) -> int:
        self.listeners.append(listener)
        return id(listener)

    def clusters(self) -> list[FakeCluster]:
        return [
            cluster for endpoint in self._endpoints for cluster in endpoint.in_clusters.values()
        ]


# ---------------------------------------------------------- the application --


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
        self.startup_error = startup_error
        self.permit_error = permit_error
        self.remove_error = remove_error
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

    def fire_device_initialized(self, device: FakeDevice) -> None:
        self.devices[device.ieee] = device
        self.listener_event("device_initialized", device)

    def fire_device_joined(self, device: FakeDevice) -> None:
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
