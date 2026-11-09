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

"""The zigbee2mqtt device library, as the Zigbee source sees it
(design 2026-11-09, sections 7 to 9).

One `Library` per Zigbee source. For each zigpy device it decides which
library speaks for it (`bind`), sees its frames before zigpy parses them
(the raw tap), keeps the properties the device reported, and answers the
source's three questions: which snapshot paths and values the library adds,
which device types, and which configuration options.

**Which library speaks for a device** (section 7):

- a device zha-quirks transformed keeps its readings exactly as today; the
  catalogue adds only settings on ZCL attributes the quirk does not
  describe;
- a device with no quirk and a `catalogue` definition gets its attribute
  and Tuya data-point readings and settings from the catalogue, run here in
  Python;
- a device with no quirk and an `engine` definition gets everything the
  library does for it from the engine (`engine.py`); if the engine cannot
  run, it falls back to what the catalogue holds of the definition.

**Failures stay with the device.** An engine call that fails costs that
one message; three failures in a row stop the engine for that device until
the bridge restarts, and it falls back to the catalogue.

**State** - each device's last property values and, for engine devices,
zigbee-herdsman's own device record - is kept in one JSON file next to
zigpy's database, so a restart shows the last values and the engine finds
what a definition's `configure` stored."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loxmatter.device_config import ConfigOption
from loxmatter.zigbee.herdsman import properties, tuya, zcl
from loxmatter.zigbee.herdsman.catalogue import (
    ACCESS_GET,
    ACCESS_SET,
    Attribute,
    Catalogue,
    Datapoint,
    Definition,
    DeviceKeys,
    Expose,
    flatten_exposes,
)
from loxmatter.zigbee.herdsman.engine import Engine, EngineError, EngineUnavailableError

logger = logging.getLogger(__name__)

STATE_SUFFIX = ".herdsman.json"

# Engine failures in a row after which a device falls back to the catalogue.
MAX_ENGINE_FAILURES = 3

# How long property changes are collected before the state file is written.
_SAVE_DELAY_SECONDS = 10.0

_HA_PROFILE = 0x0104
_BASIC = 0x0000
# zigbee-herdsman's names for the node descriptor's logical type.
_LOGICAL_TYPES = {0: "Coordinator", 1: "Router", 2: "EndDevice"}
# `Zcl.POWER_SOURCES`.
_POWER_SOURCES = {
    0: "Unknown",
    1: "Mains (single phase)",
    2: "Mains (3 phase)",
    3: "Battery",
    4: "DC Source",
    5: "Emergency mains constantly powered",
    6: "Emergency mains and transfer switch",
}
_MCU_SYNC_TIME = 0x24
# 1 January 2000, 00:00 UTC, in seconds - `constants.OneJanuary2000`.
_ONE_JANUARY_2000 = 946684800

Notify = Callable[[str], None]

_active: Library | None = None


def set_active(library: Library | None) -> None:
    """The library of the running Zigbee source, for `options.write_pending`,
    which is handed a device and the store but not the source."""
    global _active
    _active = library


def active() -> Library | None:
    return _active


@functools.cache
def shared_catalogue() -> Catalogue:
    """The catalogue, read once per process: every source - and every test
    that builds one - shares it."""
    return Catalogue.load()


def herdsman_ieee(address: str) -> str:
    """zigpy's `00:12:4b:...` as zigbee-herdsman spells it, `0x00124b...`."""
    return "0x" + address.replace(":", "").lower()


@dataclass
class _Device:
    """One device the library speaks for."""

    address: str
    definition: Definition
    # "catalogue", "engine" or "settings" (a quirked device: settings only).
    mode: str
    exposes: dict[str, Expose]
    endpoints: dict[str, int] | None
    default_endpoint: int
    values: dict[str, Any] = field(default_factory=dict)
    engine_failures: int = 0
    attached: bool = False
    # The quirk's own options, which the library's must not repeat.
    taken: frozenset[tuple[int, int, int]] = frozenset()


@dataclass(frozen=True)
class Contribution:
    """What the library adds to one device's snapshot."""

    attributes: Mapping[str, object] = field(default_factory=dict)
    device_types: Mapping[int, int] = field(default_factory=dict)
    options: tuple[ConfigOption, ...] = ()


_NOTHING = Contribution()


def device_keys(device: Any) -> DeviceKeys:
    """What the matcher needs, read off a zigpy device and its Basic cluster."""
    basic = _basic_cache(device)
    node_desc = getattr(device, "node_desc", None)
    logical = getattr(node_desc, "logical_type", None)
    endpoints = []
    for endpoint in getattr(device, "non_zdo_endpoints", ()):
        endpoints.append(
            (
                int(endpoint.endpoint_id),
                _int_or_none(getattr(endpoint, "device_type", None)),
                _int_or_none(getattr(endpoint, "profile_id", None)),
                tuple(int(c) for c in endpoint.in_clusters),
                tuple(int(c) for c in endpoint.out_clusters),
            )
        )
    power = basic.get(7)
    return DeviceKeys(
        model_id=getattr(device, "model", None) or None,
        manufacturer_name=getattr(device, "manufacturer", None) or None,
        type=_LOGICAL_TYPES.get(int(logical), "EndDevice") if logical is not None else "EndDevice",
        manufacturer_id=_int_or_none(getattr(node_desc, "manufacturer_code", None)),
        application_version=_int_or_none(basic.get(1)),
        stack_version=_int_or_none(basic.get(2)),
        zcl_version=_int_or_none(basic.get(0)),
        hardware_version=_int_or_none(basic.get(3)),
        date_code=_str_or_none(basic.get(6)),
        software_build_id=_str_or_none(basic.get(0x4000)),
        power_source=_power_source(device, power),
        ieee=herdsman_ieee(str(device.ieee)),
        endpoints=tuple(endpoints),
    )


# The Basic attributes the matcher reads: ZCL, application, stack and
# hardware version, date code, power source, software build.
_BASIC_ATTRIBUTES = (0, 1, 2, 3, 6, 7, 0x4000)


def _basic_cache(device: Any) -> dict[int, Any]:
    """The Basic cluster's cached values, through the public `Cluster.get`:
    zigpy 2.2's attribute cache is no mapping (see `tests/zigbee/fakes.py`)."""
    for endpoint in getattr(device, "non_zdo_endpoints", ()):
        cluster = endpoint.in_clusters.get(_BASIC)
        if cluster is not None:
            values: dict[int, Any] = {}
            for attribute_id in _BASIC_ATTRIBUTES:
                try:
                    value = cluster.get(attribute_id)
                except (KeyError, ValueError):
                    value = None
                if value is not None:
                    values[attribute_id] = value
            return values
    return {}


def _power_source(device: Any, raw: object) -> str | None:
    number = _int_or_none(raw)
    if number is not None:
        return _POWER_SOURCES.get(number & 0x7F, "Unknown")
    node_desc = getattr(device, "node_desc", None)
    if node_desc is None:
        return None
    return "Mains (single phase)" if node_desc.is_mains_powered else "Battery"


def _int_or_none(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except (TypeError, ValueError):
        return None


def _str_or_none(value: object) -> str | None:
    return None if value is None else str(value)


def herdsman_record(
    device: Any, keys: DeviceKeys, stored: Mapping[str, Any] | None
) -> dict[str, Any]:
    """zigbee-herdsman's database entry for a zigpy device: what
    `Device.fromDatabaseEntry` builds its device from. What the engine saved
    before - `meta`, attribute values a converter kept - is carried over."""
    stored = stored or {}
    raw_endpoints = stored.get("endpoints")
    stored_endpoints: Mapping[str, Any] = raw_endpoints if isinstance(raw_endpoints, dict) else {}
    endpoints: dict[str, Any] = {}
    for endpoint_id, device_type, profile_id, inputs, outputs in keys.endpoints:
        previous = stored_endpoints.get(str(endpoint_id)) or {}
        endpoints[str(endpoint_id)] = {
            "profId": profile_id if profile_id is not None else _HA_PROFILE,
            "epId": endpoint_id,
            "devId": device_type if device_type is not None else 0,
            "inClusterList": list(inputs),
            "outClusterList": list(outputs),
            "clusters": previous.get("clusters") or {},
            "binds": previous.get("binds") or [],
            "configuredReportings": previous.get("configuredReportings") or [],
            "meta": previous.get("meta") or {},
        }
    return {
        "id": 1,
        "type": keys.type,
        "ieeeAddr": keys.ieee,
        "nwkAddr": int(getattr(device, "nwk", 0) or 0),
        "manufId": keys.manufacturer_id,
        "manufName": keys.manufacturer_name,
        "powerSource": keys.power_source,
        "modelId": keys.model_id,
        "appVersion": keys.application_version,
        "stackVersion": keys.stack_version,
        "zclVersion": keys.zcl_version,
        "hwVersion": keys.hardware_version,
        "dateCode": keys.date_code,
        "swBuildId": keys.software_build_id,
        "epList": [e[0] for e in keys.endpoints],
        "endpoints": endpoints,
        "interviewCompleted": True,
        "interviewState": "SUCCESSFUL",
        "meta": stored.get("meta") or {},
    }


class Library:
    """The device library for one Zigbee source."""

    def __init__(
        self,
        *,
        state_file: Path | None,
        notify: Notify,
        catalogue_loader: Callable[[], Catalogue] | None = None,
        engine_factory: Callable[..., Engine] = Engine,
    ) -> None:
        self._state_file = state_file
        self._notify = notify
        self._catalogue_loader = catalogue_loader
        self._engine_factory = engine_factory
        self._catalogue: Catalogue | None = None
        self._catalogue_failed = False
        self._engine: Engine | None = None
        self._engine_failed = False
        self._app: Any = None
        self._devices: dict[str, _Device] = {}
        self._zigpy: dict[str, Any] = {}
        self._tapped: dict[str, Any] = {}
        self._state: dict[str, dict[str, Any]] = self._read_state()
        self._save_handle: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._tuya_seq = 0
        # The last radio error of an engine send per device - see `write`.
        self._send_failures: dict[str, Exception] = {}

    # -------------------------------------------------------------- loading --

    async def load(self) -> bool:
        """Reads the catalogue, once, off the event loop. A missing or broken
        catalogue switches the library off and never stops the source."""
        if self._catalogue is not None:
            return True
        if self._catalogue_failed:
            return False
        try:
            # The module's `shared_catalogue` is looked up here, not bound as
            # a default: the test suite switches the library off through it.
            loader = self._catalogue_loader or shared_catalogue
            self._catalogue = await asyncio.get_running_loop().run_in_executor(None, loader)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            self._catalogue_failed = True
            # Missing data is an installation without the build step (a
            # development checkout); anything else is worth a warning.
            level = logging.INFO if isinstance(exc, FileNotFoundError) else logging.WARNING
            logger.log(level, "the zigbee2mqtt device library is not available: %s", exc)
            return False
        logger.info("zigbee2mqtt device library %s loaded", self._catalogue.version)
        return True

    def use_application(self, app: Any) -> None:
        self._app = app

    # ----------------------------------------------------------------- state --

    def _read_state(self) -> dict[str, dict[str, Any]]:
        if self._state_file is None or not self._state_file.is_file():
            return {}
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("ignoring the unreadable library state %s: %s", self._state_file, exc)
            return {}
        devices = data.get("devices") if isinstance(data, dict) else None
        return (
            {str(k): v for k, v in devices.items() if isinstance(v, dict)}
            if isinstance(devices, dict)
            else {}
        )

    def _schedule_save(self) -> None:
        if self._state_file is None:
            return
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            if self._save_handle is None:
                self._save_handle = loop.call_later(_SAVE_DELAY_SECONDS, self.save)

    def save(self) -> None:
        """Writes the state file now; atomic, so a power cut leaves the old one."""
        self._save_handle = None
        if self._state_file is None:
            return
        for address, device in self._devices.items():
            entry = self._state.setdefault(address, {})
            entry["definition"] = device.definition.id
            entry["values"] = device.values
        temporary = self._state_file.with_suffix(self._state_file.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps({"devices": self._state}, sort_keys=True), encoding="utf-8"
            )
            os.replace(temporary, self._state_file)
        except OSError as exc:
            logger.warning("could not write the library state %s: %s", self._state_file, exc)

    async def close(self) -> None:
        if self._save_handle is not None:
            self._save_handle.cancel()
        self.save()
        for address in list(self._tapped):
            self._untap(address)
        for bound in self._devices.values():
            bound.attached = False
        engine, self._engine = self._engine, None
        if engine is not None:
            await engine.close()

    # --------------------------------------------------------------- binding --

    def bind(self, device: Any) -> bool:
        """Decides which library speaks for a device and installs the raw tap.
        Answers whether the library speaks for it at all. Cheap and
        synchronous; attaching an engine device follows in `attach`."""
        catalogue = self._catalogue
        if catalogue is None:
            return False
        address = str(device.ieee)
        self._zigpy[address] = device
        try:
            keys = device_keys(device)
            definition_id = catalogue.match(keys)
        except Exception:
            logger.exception("matching %s against the device library failed", address)
            return False
        if definition_id is None:
            self.forget(address, keep_state=True)
            return False
        definition = catalogue.definition(definition_id)
        quirked = hasattr(device, "_quirk_registry_entry")
        if quirked:
            mode = "settings"
        elif definition.is_engine and not self._engine_failed:
            mode = "engine"
        else:
            mode = "catalogue"
        current = self._devices.get(address)
        if current is not None and current.definition.id == definition.id and current.mode == mode:
            self._tap(device)
            return True
        endpoints = dict(definition.endpoints) if definition.endpoints else None
        bound = _Device(
            address=address,
            definition=definition,
            mode=mode,
            exposes={e.property: e for e in definition.exposes},
            endpoints=endpoints,
            default_endpoint=_default_endpoint(device),
            taken=_quirk_options(device) if quirked else frozenset(),
        )
        stored = self._state.get(address) or {}
        if stored.get("definition") == definition.id and isinstance(stored.get("values"), dict):
            bound.values = dict(stored["values"])
        self._devices[address] = bound
        logger.info(
            "%s is %s %s in the zigbee2mqtt device library (%s)",
            address,
            definition.vendor,
            definition.model,
            {
                "settings": "settings next to its quirk",
                "engine": "engine",
                "catalogue": "catalogue",
            }[mode],
        )
        self._tap(device)
        return True

    async def attach(self, device: Any) -> None:
        """Attaches an engine device to the engine, starting the engine the
        first time. A failure falls back to the catalogue for that device."""
        address = str(device.ieee)
        bound = self._devices.get(address)
        if bound is None or bound.mode != "engine" or bound.attached:
            return
        try:
            engine = await self._running_engine()
            keys = device_keys(device)
            stored = (self._state.get(address) or {}).get("record")
            attached = await engine.attach(herdsman_record(device, keys, stored), bound.values)
        except EngineUnavailableError:
            self._fall_back(bound, "the engine is not available")
            return
        except (TimeoutError, EngineError) as exc:
            self._fall_back(bound, f"attaching it failed: {exc}")
            return
        if attached is None:
            self._fall_back(bound, "the engine found no definition")
            return
        bound.attached = True
        if attached.exposes:
            bound.exposes = {e.property: e for e in flatten_exposes(attached.exposes)}
        if attached.endpoints:
            bound.endpoints = dict(attached.endpoints)
        self._notify(address)

    def _fall_back(self, bound: _Device, why: str) -> None:
        logger.warning(
            "%s: %s - using the catalogue for %s %s",
            bound.address,
            why,
            bound.definition.vendor,
            bound.definition.model,
        )
        bound.mode = "catalogue"
        bound.attached = False
        bound.exposes = {e.property: e for e in bound.definition.exposes}

    async def _running_engine(self) -> Engine:
        if self._engine_failed:
            raise EngineUnavailableError("the engine failed to start before")
        if self._engine is None:
            self._engine = self._engine_factory(
                send=self._engine_send,
                bind=self._engine_bind,
                save=self._engine_save,
                publish=self._engine_publish,
                next_tsn=self._next_tsn,
            )
        if not self._engine.started:
            try:
                await self._engine.start(self._coordinator_ieee())
            except EngineUnavailableError as exc:
                self._engine_failed = True
                logger.warning("the zigbee2mqtt device library engine did not start: %s", exc)
                raise
        return self._engine

    def _coordinator_ieee(self) -> str:
        app = self._app
        ieee = getattr(getattr(getattr(app, "state", None), "node_info", None), "ieee", None)
        return herdsman_ieee(str(ieee)) if ieee is not None else "0x0000000000000000"

    def _next_tsn(self) -> int:
        app = self._app
        if app is not None and hasattr(app, "get_sequence"):
            return int(app.get_sequence())
        self._tuya_seq = (self._tuya_seq + 1) & 0xFF
        return self._tuya_seq

    def forget(self, address: str, *, keep_state: bool = False) -> None:
        self._untap(address)
        bound = self._devices.pop(address, None)
        self._zigpy.pop(address, None)
        if not keep_state:
            self._state.pop(address, None)
            self._schedule_save()
        if bound is not None and bound.attached and self._engine is not None:
            self._spawn(self._engine.detach(herdsman_ieee(address)))

    def speaks_for(self, address: str) -> bool:
        return address in self._devices

    def mode(self, address: str) -> str | None:
        bound = self._devices.get(address)
        return None if bound is None else bound.mode

    # --------------------------------------------------------------- the tap --

    def _tap(self, device: Any) -> None:
        """Sees every frame of the device before zigpy parses it - and after
        zigpy's own duplicate filter, so a repeated frame is not taken twice.
        The hook is the device's `_should_filter_packet`, which zigpy 2.2
        calls first for every packet; it is checked to exist by the tests."""
        address = str(device.ieee)
        if self._tapped.get(address) is device:
            return
        self._untap(address)
        original = getattr(device, "_should_filter_packet", None)
        if original is None:
            logger.warning("%s: cannot tap its frames on this zigpy version", address)
            return

        def should_filter(packet: Any) -> bool:
            filtered = bool(original(packet))
            if not filtered:
                try:
                    self.on_packet(address, packet)
                except Exception:
                    logger.exception("%s: the device library failed on a frame", address)
            return filtered

        device._should_filter_packet = should_filter
        self._tapped[address] = device

    def _untap(self, address: str) -> None:
        device = self._tapped.pop(address, None)
        if device is not None:
            with contextlib.suppress(AttributeError):
                del device._should_filter_packet

    def on_packet(self, address: str, packet: Any) -> None:
        bound = self._devices.get(address)
        if bound is None:
            return
        source_endpoint = _int_or_none(getattr(packet, "src_ep", None))
        if source_endpoint is None or source_endpoint == 0:
            return  # ZDO
        cluster = int(packet.cluster_id)
        data = packet.data.serialize() if hasattr(packet.data, "serialize") else bytes(packet.data)
        if bound.mode == "engine" and bound.attached:
            lqi = _int_or_none(getattr(packet, "lqi", None)) or 0
            destination = _int_or_none(getattr(packet, "dst_ep", None)) or 1
            self._spawn(
                self._engine_frame(bound, source_endpoint, cluster, bytes(data), lqi, destination)
            )
            return
        changed = self._catalogue_frame(bound, source_endpoint, cluster, bytes(data))
        if changed:
            self._changed(bound)

    async def _engine_frame(
        self, bound: _Device, endpoint: int, cluster: int, data: bytes, lqi: int, destination: int
    ) -> None:
        engine = self._engine
        if engine is None:
            return
        try:
            await engine.frame(
                herdsman_ieee(bound.address), endpoint, cluster, data, lqi, destination
            )
        except (TimeoutError, EngineError) as exc:
            self._engine_failed_for(bound, f"a frame on cluster {cluster:#06x}: {exc}")
            return
        bound.engine_failures = 0

    def _engine_failed_for(self, bound: _Device, what: str) -> None:
        bound.engine_failures += 1
        logger.warning(
            "%s (%s %s): the engine failed on %s",
            bound.address,
            bound.definition.vendor,
            bound.definition.model,
            what,
        )
        if bound.engine_failures >= MAX_ENGINE_FAILURES and bound.mode == "engine":
            self._fall_back(bound, f"{MAX_ENGINE_FAILURES} engine failures in a row")
            if self._engine is not None:
                self._spawn(self._engine.detach(herdsman_ieee(bound.address)))
            self._notify(bound.address)

    # ------------------------------------------------- stage 1: the catalogue --

    def _catalogue_frame(self, bound: _Device, endpoint: int, cluster: int, data: bytes) -> bool:
        """Stage 1 on one frame. A quirked device's Tuya frames are its
        quirk's; only the attributes behind the library's settings are read."""
        definition = bound.definition
        if cluster == tuya.TUYA_CLUSTER and definition.datapoints and bound.mode == "catalogue":
            return self._tuya_frame(bound, endpoint, data)
        if not definition.attributes:
            return False
        changed = False
        for value in zcl.parse_attribute_frame(endpoint, cluster, data):
            for attribute in definition.attributes:
                if not zcl.matches(attribute, value):
                    continue
                converted = zcl.from_device(attribute, value.value)
                if converted is not None and bound.values.get(attribute.property) != converted:
                    bound.values[attribute.property] = converted
                    changed = True
        return changed

    def _tuya_frame(self, bound: _Device, endpoint: int, data: bytes) -> bool:
        try:
            header_length = 5 if data and data[0] & 0x04 else 3
            frame_control, command = data[0], data[header_length - 1]
        except IndexError:
            return False
        if frame_control & 0x03 != 0x01:
            return False  # not a cluster-specific command
        payload = data[header_length:]
        if command == _MCU_SYNC_TIME:
            self._answer_time_sync(bound, endpoint)
            return False
        if command not in tuya.REPORT_COMMANDS:
            return False
        try:
            _seq, values = tuya.parse_dp_values(payload)
        except ValueError:
            return False
        changed = False
        by_dp: dict[int, Datapoint] = {d.dp: d for d in bound.definition.datapoints}
        for dp_value in values:
            datapoint = by_dp.get(dp_value.dp)
            if datapoint is None:
                continue
            try:
                converted = tuya.from_device(datapoint.converter, tuya.data_value(dp_value))
            except tuya.ConverterError as exc:
                logger.debug("%s: data point %d not converted: %s", bound.address, dp_value.dp, exc)
                continue
            if isinstance(converted, bytes):
                continue
            if bound.values.get(datapoint.property) != converted:
                bound.values[datapoint.property] = converted
                changed = True
        return changed

    def _answer_time_sync(self, bound: _Device, endpoint: int) -> None:
        setup = bound.definition.tuya
        if setup is None or setup.time_start == "off":
            return
        offset = _ONE_JANUARY_2000 if setup.time_start == "2000" else 0
        utc = round(time.time()) - offset
        local = utc + (time.localtime().tm_gmtoff or 0)
        payload = (
            (8).to_bytes(2, "little")
            + (utc & 0xFFFFFFFF).to_bytes(4, "big")
            + (local & 0xFFFFFFFF).to_bytes(4, "big")
        )
        tsn = self._next_tsn()
        frame = bytes((0x11, tsn, _MCU_SYNC_TIME)) + payload
        self._spawn(
            self._send_quietly(bound.address, endpoint, tuya.TUYA_CLUSTER, frame, "time sync")
        )

    async def _send_quietly(
        self, address: str, endpoint: int, cluster: int, frame: bytes, what: str
    ) -> None:
        try:
            await self._send(address, endpoint, cluster, frame)
        except Exception as exc:  # noqa: BLE001 - a missed answer is retried by the device
            logger.info("%s: %s not sent: %s", address, what, exc)

    def _changed(self, bound: _Device) -> None:
        self._schedule_save()
        self._notify(bound.address)

    # -------------------------------------------------- what the source asks --

    def contribution(self, device: Any) -> Contribution:
        """Paths, device types and options the library adds to a device."""
        bound = self._devices.get(str(device.ieee))
        if bound is None:
            return _NOTHING
        try:
            return self._contribution(bound, device)
        except Exception:
            logger.exception("%s: the device library's contribution failed", bound.address)
            return _NOTHING

    def _contribution(self, bound: _Device, device: Any) -> Contribution:
        options = self._options(bound)
        attributes: dict[str, object] = {}
        if bound.mode == "settings":
            option_paths = {o.path for o in options}
            for prop, value in bound.values.items():
                placed = self._place(bound, prop, value)
                if placed is not None and placed.path in option_paths:
                    attributes[placed.path] = placed.value
            return Contribution(attributes=attributes, options=options)
        names_by_endpoint: dict[int, set[str]] = {}
        for prop, value in bound.values.items():
            placed = self._place(bound, prop, value)
            if placed is None:
                continue
            attributes[placed.path] = placed.value
            expose = bound.exposes.get(prop)
            if expose is not None and placed.device_type is not None:
                endpoint = properties.endpoint_of(expose, bound.endpoints, bound.default_endpoint)
                names_by_endpoint.setdefault(endpoint, set()).add(expose.name)
        device_types: dict[int, int] = {}
        tuya_endpoints = {
            int(e.endpoint_id)
            for e in getattr(device, "non_zdo_endpoints", ())
            if tuya.TUYA_CLUSTER in e.in_clusters
        }
        for endpoint, names in names_by_endpoint.items():
            device_type = properties.device_type_for(names)
            if device_type is not None and endpoint in tuya_endpoints:
                device_types[endpoint] = device_type
        return Contribution(attributes=attributes, device_types=device_types, options=options)

    def _place(self, bound: _Device, prop: str, value: Any) -> properties.Placement | None:
        expose = bound.exposes.get(prop)
        if expose is None:
            return None
        endpoint = properties.endpoint_of(expose, bound.endpoints, bound.default_endpoint)
        return properties.place(expose, value, endpoint=endpoint)

    def _options(self, bound: _Device) -> tuple[ConfigOption, ...]:
        writable = self._writable(bound)
        options: list[ConfigOption] = []
        for prop in writable:
            expose = bound.exposes.get(prop)
            if expose is None:
                continue
            endpoint = properties.endpoint_of(expose, bound.endpoints, bound.default_endpoint)
            option = properties.option_for(expose, endpoint=endpoint)
            if option is not None:
                options.append(option)
        return tuple(options)

    def _writable(self, bound: _Device) -> list[str]:
        """The properties this device's library can actually write, in the
        definition's order."""
        definition = bound.definition
        if bound.mode == "engine":
            return [p for p, e in bound.exposes.items() if e.access & ACCESS_SET]
        names: list[str] = []
        for attribute in definition.attributes:
            if not attribute.access & ACCESS_SET:
                continue
            if bound.mode == "settings":
                endpoint = (
                    attribute.endpoint if attribute.endpoint is not None else bound.default_endpoint
                )
                if (endpoint, attribute.cluster, attribute.attribute) in bound.taken:
                    continue
            names.append(attribute.property)
        if bound.mode == "catalogue":
            names.extend(d.property for d in definition.datapoints if tuya.can_write(d.converter))
        seen: set[str] = set()
        return [n for n in names if not (n in seen or seen.add(n))]  # type: ignore[func-returns-value]

    # --------------------------------------------------------------- writing --

    def property_of(self, address: str, attribute_id: int) -> str | None:
        bound = self._devices.get(address)
        if bound is None:
            return None
        for prop in bound.exposes:
            if properties.vendor_property_ids().get(prop) == attribute_id:
                return prop
        return None

    async def write(self, device: Any, attribute_id: int, raw: float | bool) -> None:
        """Writes one option value, given on the option's raw scale. Raises
        what the radio raised when the device does not answer, and
        `ValueError` for a value or property the device cannot take."""
        address = str(device.ieee)
        bound = self._devices.get(address)
        prop = self.property_of(address, attribute_id)
        if bound is None or prop is None:
            raise ValueError(f"no library property {attribute_id} on {address}")
        expose = bound.exposes[prop]
        value = properties.library_value(expose, raw)
        if bound.mode == "engine" and bound.attached and self._engine is not None:
            self._send_failures.pop(address, None)
            try:
                await self._engine.set(
                    herdsman_ieee(address), _key_of(expose), value, expose.endpoint
                )
            except EngineError as exc:
                # The engine only knows the radio's error as text; the
                # original tells the caller whether the device was asleep.
                failure = self._send_failures.pop(address, None)
                if failure is not None:
                    raise failure from exc
                if "Timeout" in str(exc):
                    raise TimeoutError(str(exc)) from exc
                raise ValueError(str(exc)) from exc
            return
        for attribute in bound.definition.attributes:
            if attribute.property == prop and attribute.access & ACCESS_SET:
                await self._write_attribute(device, bound, attribute, value)
                self._set_value(bound, prop, value)
                return
        for datapoint in bound.definition.datapoints:
            if datapoint.property == prop and tuya.can_write(datapoint.converter):
                await self._write_datapoint(device, bound, datapoint, value)
                if datapoint.optimistic:
                    self._set_value(bound, prop, value)
                return
        raise ValueError(f"{prop} cannot be written on {address}")

    def _set_value(self, bound: _Device, prop: str, value: Any) -> None:
        if bound.values.get(prop) != value:
            bound.values[prop] = value
            self._changed(bound)

    async def _write_attribute(
        self, device: Any, bound: _Device, attribute: Attribute, value: Any
    ) -> None:
        from zigpy.zcl import foundation

        raw = zcl.to_device(attribute, value)
        endpoint_id = (
            attribute.endpoint
            if attribute.endpoint is not None
            else _endpoint_with(device, attribute.cluster)
        )
        endpoint = device.endpoints.get(endpoint_id)
        cluster = None if endpoint is None else endpoint.in_clusters.get(attribute.cluster)
        if cluster is None:
            raise ValueError(
                f"{bound.address} has no cluster {attribute.cluster:#06x} on endpoint {endpoint_id}"
            )
        record = foundation.Attribute(
            attrid=attribute.attribute, value=zcl.typed_value(attribute.type, raw)
        )
        result = await cluster.write_attributes_raw(
            [record], manufacturer_code=attribute.manufacturer_code
        )
        status = _write_status(result)
        if status is not None:
            raise ValueError(f"{bound.address} refused {attribute.property} (status {status:#04x})")

    async def _write_datapoint(
        self, device: Any, bound: _Device, datapoint: Datapoint, value: Any
    ) -> None:
        converted = tuya.to_device(datapoint.converter, value)
        if converted is None:
            return
        dp_value = tuya.dp_value(datapoint.dp, converted)
        command = tuya.SEND_COMMANDS.get(bound.definition.tuya_send_command, tuya.DATA_REQUEST)
        tsn = self._next_tsn()
        # `tz.datapoints` always sends sequence 1 and asks for no default response.
        frame = bytes((0x11, tsn, command)) + tuya.encode_dp_values(1, [dp_value])
        endpoint = _endpoint_with(device, tuya.TUYA_CLUSTER)
        await self._send(bound.address, endpoint, tuya.TUYA_CLUSTER, frame)

    async def read_missing(self, device: Any) -> None:
        """Asks an awake device for the settings whose value is unknown."""
        address = str(device.ieee)
        bound = self._devices.get(address)
        if bound is None:
            return
        missing = [p for p in self._writable(bound) if p not in bound.values]
        if not missing:
            return
        if bound.mode == "engine" and bound.attached and self._engine is not None:
            for prop in missing:
                expose = bound.exposes.get(prop)
                if expose is None or not expose.access & ACCESS_GET:
                    continue
                try:
                    await self._engine.get(herdsman_ieee(address), _key_of(expose), expose.endpoint)
                except EngineError as exc:
                    logger.info("%s: reading %s failed: %s", address, prop, exc)
                    return
            return
        wanted: dict[tuple[int, int, int | None], list[int]] = {}
        for attribute in bound.definition.attributes:
            if attribute.property in missing:
                endpoint = (
                    attribute.endpoint
                    if attribute.endpoint is not None
                    else _endpoint_with(device, attribute.cluster)
                )
                wanted.setdefault(
                    (endpoint, attribute.cluster, attribute.manufacturer_code), []
                ).append(attribute.attribute)
        for (endpoint_id, cluster_id, manufacturer), ids in wanted.items():
            endpoint = device.endpoints.get(endpoint_id)
            cluster = None if endpoint is None else endpoint.in_clusters.get(cluster_id)
            if cluster is None:
                continue
            try:
                # The answer comes back through the tap like any report.
                await cluster.read_attributes_raw(ids, manufacturer=manufacturer)
            except Exception as exc:  # noqa: BLE001 - retried on the next wake-up
                logger.info("%s: reading its library settings failed: %s", address, exc)
                return

    # ----------------------------------------------------------- configuring --

    async def configure(self, device: Any) -> None:
        """The library's part of configure-on-join, after loxmatter's own:
        the definition's `configure` for an engine device, Tuya's magic
        packet and data-point query for a catalogue one."""
        address = str(device.ieee)
        bound = self._devices.get(address)
        if bound is None or bound.mode == "settings":
            return
        if bound.mode == "engine":
            await self.attach(device)
        if bound.mode == "engine" and bound.attached and self._engine is not None:
            try:
                await self._engine.configure(herdsman_ieee(address))
            except EngineError as exc:
                logger.warning("%s: the library's configure failed: %s", address, exc)
            return
        setup = bound.definition.tuya
        if setup is None:
            return
        basic = device.endpoints.get(_first_endpoint(device))
        cluster = None if basic is None else basic.in_clusters.get(_BASIC)
        if cluster is not None:
            with contextlib.suppress(Exception):
                # Tuya's "magic packet": without it many Tuya devices never
                # start reporting.
                await cluster.read_attributes_raw([4, 0, 1, 5, 7, 0xFFFE])
        if setup.query_on_configure:
            frame = bytes((0x11, self._next_tsn(), tuya.DATA_QUERY))
            await self._send_quietly(
                address, _first_endpoint(device), tuya.TUYA_CLUSTER, frame, "data query"
            )

    # ---------------------------------------------------- engine host side --

    async def _send(
        self,
        address: str,
        endpoint: int,
        cluster: int,
        frame: bytes,
        *,
        source: int = 1,
        profile: int = _HA_PROFILE,
    ) -> None:
        device = self._zigpy.get(address)
        if device is None:
            raise ValueError(f"{address} is not known")
        tsn = frame[3] if frame and frame[0] & 0x04 else frame[1]
        await device.request(profile, cluster, source, endpoint, tsn, frame, expect_reply=False)

    async def _engine_send(
        self, ieee: str, endpoint: int, cluster: int, frame: bytes, source: int, profile: int
    ) -> None:
        address = self._address_of(ieee)
        try:
            await self._send(address, endpoint, cluster, frame, source=source, profile=profile)
        except Exception as exc:
            self._send_failures[address] = exc
            raise

    async def _engine_bind(self, kind: str, ieee: str, endpoint: int, cluster: int) -> None:
        from zigpy.zdo import types as zdo_types

        address = self._address_of(ieee)
        device = self._zigpy.get(address)
        app = self._app
        if device is None or app is None:
            raise ValueError(f"{address} is not known")
        # What `get_dst_address` builds for a cluster object, which a
        # cluster zigpy does not know has none of: the coordinator, by IEEE.
        destination: Any = zdo_types.MultiAddress()
        destination.addrmode = 3
        destination.ieee = app.state.node_info.ieee
        destination.endpoint = 1
        request = device.zdo.Bind_req if kind == "bind" else device.zdo.Unbind_req
        result = await request(device.ieee, endpoint, cluster, destination)
        status = result[0] if isinstance(result, (list, tuple)) and result else None
        if status is not None and int(status) != 0:
            raise RuntimeError(
                f"{kind} of cluster {cluster:#06x} refused (status {int(status):#04x})"
            )

    def _engine_save(self, ieee: str, record: dict[str, Any]) -> None:
        address = self._address_of(ieee)
        self._state.setdefault(address, {})["record"] = record
        self._schedule_save()

    def _engine_publish(self, ieee: str, payload: dict[str, Any]) -> None:
        address = self._address_of(ieee)
        bound = self._devices.get(address)
        if bound is None:
            return
        changed = False
        for prop, value in payload.items():
            if bound.values.get(prop) != value:
                bound.values[prop] = value
                changed = True
        if changed:
            self._changed(bound)

    def _address_of(self, ieee: str) -> str:
        bare = ieee.removeprefix("0x")
        return ":".join(bare[i : i + 2] for i in range(0, len(bare), 2))

    def _spawn(self, coroutine: Any) -> None:
        try:
            task: asyncio.Task[None] = asyncio.ensure_future(coroutine)
        except RuntimeError:
            coroutine.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


def _key_of(expose: Expose) -> str:
    """The key a toZigbee converter is looked up by: the expose's name, not
    the endpoint-suffixed property."""
    return expose.name


def _default_endpoint(device: Any) -> int:
    endpoints = [int(e.endpoint_id) for e in getattr(device, "non_zdo_endpoints", ())]
    return endpoints[0] if endpoints else 1


def _first_endpoint(device: Any) -> int:
    return _default_endpoint(device)


def _endpoint_with(device: Any, cluster: int) -> int:
    """`determineEndpoint`: the first endpoint with the cluster, else the first."""
    for endpoint in getattr(device, "non_zdo_endpoints", ()):
        if cluster in endpoint.in_clusters:
            return int(endpoint.endpoint_id)
    return _default_endpoint(device)


def _quirk_options(device: Any) -> frozenset[tuple[int, int, int]]:
    from loxmatter.zigbee.options import config_options

    return frozenset((o.endpoint, o.cluster_id, o.attribute_id) for o in config_options(device))


def _write_status(result: object) -> int | None:
    records: list[Any] = []
    if isinstance(result, (list, tuple)):
        for item in result:
            if isinstance(item, (list, tuple)):
                records.extend(item)
            else:
                records.append(item)
    for record in records:
        status = getattr(record, "status", 0)
        if int(status) != 0:
            return int(status)
    return None
