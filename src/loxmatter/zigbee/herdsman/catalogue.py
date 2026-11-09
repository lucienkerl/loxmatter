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

"""The catalogue `scripts/herdsman/build.mjs` generates, and the matcher.

The catalogue is about 9 MB of JSON for 4653 definitions. It is read once,
off the event loop, only when a Zigbee radio is configured, and only the
part needed to match a device is kept: the per-model index and the match
keys. A definition's full entry is kept for the devices that actually match
one (design 2026-11-09, section 5).

**The matcher is a port of the library's own `findDefinition`**
(`index.js`, zigbee-herdsman-converters 26.128.0), not an approximation of
it: the same model index, the same "a single candidate with a zigbeeModel
wins", the same fingerprint fields and priorities, the same fallback to
`zigbeeModel` equality. The build writes a golden table - every fingerprint
and every zigbeeModel, with the definition the library itself picks for it
- and `tests/zigbee/herdsman/test_catalogue.py` checks this port against all
of it (section 6)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "herdsman_data"
CATALOGUE_FILE = DATA_DIR / "catalogue.json"
GOLDEN_FILE = DATA_DIR / "golden.json"
ENGINE_FILE = DATA_DIR / "engine.js"

# Access bits of an expose (`lib/exposes.ts`, `access`).
ACCESS_STATE = 1
ACCESS_SET = 2
ACCESS_GET = 4


@dataclass(frozen=True)
class Expose:
    """One property a definition exposes, flattened: the features of a
    light, switch, cover, lock, climate or fan carry their parent's type in
    `parent`."""

    name: str
    property: str
    type: str
    access: int
    endpoint: str | None = None
    unit: str = ""
    value_min: float | None = None
    value_max: float | None = None
    value_step: float | None = None
    values: tuple[Any, ...] = ()
    value_on: Any = None
    value_off: Any = None
    label: str | None = None
    category: str | None = None
    parent: str | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Expose:
        return cls(
            name=str(data.get("name") or data.get("property") or ""),
            property=str(data.get("property") or data.get("name") or ""),
            type=str(data.get("type") or ""),
            access=int(data.get("access") or 0),
            endpoint=data.get("endpoint"),
            unit=str(data.get("unit") or ""),
            value_min=data.get("value_min"),
            value_max=data.get("value_max"),
            value_step=data.get("value_step"),
            values=tuple(data.get("values") or ()),
            value_on=data.get("value_on"),
            value_off=data.get("value_off"),
            label=data.get("label"),
            category=data.get("category"),
            parent=data.get("parent"),
        )


def flatten_exposes(raw: Sequence[Mapping[str, Any]]) -> tuple[Expose, ...]:
    """The engine answers with the library's own expose JSON, which nests
    the features of a light and its kin. The catalogue is flat already;
    this makes the engine's answer the same shape."""
    flat: list[Expose] = []
    for item in raw:
        kind = item.get("type")
        if kind in ("light", "switch", "lock", "cover", "climate", "fan"):
            for feature in item.get("features") or ():
                flat.append(Expose.from_json({**feature, "parent": kind}))
        elif kind in ("numeric", "binary", "enum", "text"):
            flat.append(Expose.from_json(item))
    return tuple(flat)


@dataclass(frozen=True)
class Attribute:
    """A setting or reading on a ZCL attribute, from `numeric`,
    `enumLookup` or `binary`."""

    kind: str
    property: str
    endpoint: int | None
    cluster: int
    attribute: int
    type: int
    manufacturer_code: int | None
    access: int
    scale: float | None = None
    precision: int | None = None
    lookup: tuple[tuple[str, int], ...] = ()
    value_on: tuple[Any, Any] | None = None
    value_off: tuple[Any, Any] | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Attribute:
        return cls(
            kind=str(data["kind"]),
            property=str(data["property"]),
            endpoint=data.get("endpoint"),
            cluster=int(data["cluster"]),
            attribute=int(data["attribute"]),
            type=int(data["type"]),
            manufacturer_code=data.get("manufacturer_code"),
            access=int(data.get("access") or 0),
            scale=data.get("scale"),
            precision=data.get("precision"),
            lookup=tuple((str(k), v) for k, v in data.get("lookup") or ()),
            value_on=tuple(data["value_on"]) if data.get("value_on") else None,
            value_off=tuple(data["value_off"]) if data.get("value_off") else None,
        )


@dataclass(frozen=True)
class Datapoint:
    """One Tuya data point and its converter as data (`zigbee/herdsman/tuya.py`)."""

    dp: int
    property: str
    converter: Mapping[str, Any]
    optimistic: bool = True


@dataclass(frozen=True)
class TuyaSetup:
    """What `tuyaBase` does when a device is configured."""

    query_on_configure: bool = False
    bind_basic_on_configure: bool = False
    time_start: str = "off"


@dataclass(frozen=True)
class Definition:
    id: str
    vendor: str
    model: str
    description: str
    mode: str
    reasons: tuple[str, ...] = ()
    endpoints: Mapping[str, int] | None = None
    exposes: tuple[Expose, ...] = ()
    attributes: tuple[Attribute, ...] = ()
    datapoints: tuple[Datapoint, ...] = ()
    tuya: TuyaSetup | None = None
    tuya_send_command: str = "dataRequest"
    multi_endpoint: bool = False

    @property
    def is_engine(self) -> bool:
        return self.mode == "engine"

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> Definition:
        tuya = data.get("tuya")
        endpoints = data.get("endpoints")
        return cls(
            id=str(data["id"]),
            vendor=str(data.get("vendor") or ""),
            model=str(data.get("model") or ""),
            description=str(data.get("description") or ""),
            mode=str(data.get("mode") or "engine"),
            reasons=tuple(data.get("reasons") or ()),
            endpoints=(
                {str(k): int(v) for k, v in endpoints.items() if isinstance(v, int)}
                if isinstance(endpoints, dict)
                else None
            ),
            exposes=tuple(Expose.from_json(e) for e in data.get("exposes") or ()),
            attributes=tuple(Attribute.from_json(a) for a in data.get("attributes") or ()),
            datapoints=tuple(
                Datapoint(
                    dp=int(d["dp"]),
                    property=str(d["property"]),
                    converter=d["converter"],
                    optimistic=bool(d.get("optimistic", True)),
                )
                for d in data.get("datapoints") or ()
            ),
            tuya=TuyaSetup(
                query_on_configure=bool(tuya.get("query_on_configure")),
                bind_basic_on_configure=bool(tuya.get("bind_basic_on_configure")),
                time_start=str(tuya.get("time_start") or "off"),
            )
            if isinstance(tuya, dict)
            else None,
            tuya_send_command=str(data.get("tuya_send_command") or "dataRequest"),
            multi_endpoint=bool(data.get("multi_endpoint")),
        )


# --------------------------------------------------------------- matching --


@dataclass(frozen=True)
class DeviceKeys:
    """What the matcher reads off a device, in the library's own field
    names. `endpoints` holds `(ID, deviceID, profileID, inputClusters,
    outputClusters)` per endpoint, in the device's order."""

    model_id: str | None
    manufacturer_name: str | None = None
    type: str = "EndDevice"
    manufacturer_id: int | None = None
    application_version: int | None = None
    stack_version: int | None = None
    zcl_version: int | None = None
    hardware_version: int | None = None
    date_code: str | None = None
    software_build_id: str | None = None
    power_source: str | None = None
    ieee: str = ""
    endpoints: tuple[tuple[int, int | None, int | None, tuple[int, ...], tuple[int, ...]], ...] = ()


_NUL_TAIL = re.compile(r"\0(.|\n)*$")


def normalize_model_id(model_id: str) -> str:
    """`normalizeModelID`: everything from the first NUL on is dropped, then
    whitespace is trimmed. Some devices pad their model with NULs."""
    return _NUL_TAIL.sub("", model_id).strip()


def _ieee_matches(pattern: object, ieee: str) -> bool:
    """A fingerprint's `ieeeAddr` is a JavaScript regular expression, which
    the build writes as `{"regexp": source}`. The ones in the library are
    plain enough (`^0x00000000005.....$`) to mean the same in Python."""
    if not pattern:
        return True
    source = pattern.get("regexp") if isinstance(pattern, dict) else pattern
    return source is not None and re.search(str(source), ieee) is not None


def _fingerprint_matches(fingerprint: Mapping[str, Any], device: DeviceKeys) -> bool:
    """`isFingerprintMatch`, field for field. Note which fields are tested
    with `=== undefined` (a 0 in the fingerprint still has to match) and
    which with a falsy check (an empty string matches anything) - the
    library mixes both, and the port keeps the mix."""

    def defined(key: str, actual: object) -> bool:
        return key not in fingerprint or fingerprint[key] is None or actual == fingerprint[key]

    def truthy(key: str, actual: object) -> bool:
        return not fingerprint.get(key) or actual == fingerprint[key]

    match = (
        defined("applicationVersion", device.application_version)
        and defined("manufacturerID", device.manufacturer_id)
        and truthy("type", device.type)
        and truthy("dateCode", device.date_code)
        and defined("hardwareVersion", device.hardware_version)
        and truthy("manufacturerName", device.manufacturer_name)
        and truthy("modelID", device.model_id)
        and truthy("powerSource", device.power_source)
        and truthy("softwareBuildID", device.software_build_id)
        and defined("stackVersion", device.stack_version)
        and defined("zclVersion", device.zcl_version)
        and _ieee_matches(fingerprint.get("ieeeAddr"), device.ieee)
    )
    wanted = fingerprint.get("endpoints")
    if not match or not wanted:
        return match
    if [e[0] for e in device.endpoints] != [e.get("ID") for e in wanted]:
        return False
    by_id = {e[0]: e for e in device.endpoints}
    for expected in wanted:
        actual = by_id.get(expected.get("ID")) if expected.get("ID") is not None else None
        if expected.get("deviceID") is not None and (
            actual is None or actual[1] != expected["deviceID"]
        ):
            return False
        if expected.get("profileID") is not None and (
            actual is None or actual[2] != expected["profileID"]
        ):
            return False
        if expected.get("inputClusters") and (
            actual is None or list(actual[3]) != list(expected["inputClusters"])
        ):
            return False
        if expected.get("outputClusters") and (
            actual is None or list(actual[4]) != list(expected["outputClusters"])
        ):
            return False
    return True


@dataclass
class Catalogue:
    """The catalogue as the runtime keeps it: the model index and each
    definition's match keys, plus the full entries it was asked for."""

    version: str
    models: dict[str, list[str]]
    zigbee_models: dict[str, tuple[str, ...]]
    fingerprints: dict[str, tuple[Mapping[str, Any], ...]]
    _raw: dict[str, Mapping[str, Any]] = field(default_factory=dict, repr=False)
    _parsed: dict[str, Definition] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path: Path = CATALOGUE_FILE) -> Catalogue:
        data = json.loads(path.read_text(encoding="utf-8"))
        definitions: dict[str, Mapping[str, Any]] = data["definitions"]
        return cls(
            version=str(data.get("version") or ""),
            models={str(k): list(v) for k, v in data["models"].items()},
            zigbee_models={k: tuple(v.get("zigbee_model") or ()) for k, v in definitions.items()},
            fingerprints={k: tuple(v.get("fingerprints") or ()) for k, v in definitions.items()},
            _raw=dict(definitions),
        )

    def definition(self, definition_id: str) -> Definition:
        parsed = self._parsed.get(definition_id)
        if parsed is None:
            parsed = Definition.from_json(self._raw[definition_id])
            self._parsed[definition_id] = parsed
        return parsed

    def _candidates(self, model_id: str | None) -> list[str]:
        lookup = model_id.lower() if model_id else "null"
        found = self.models.get(lookup)
        if found is None:
            found = self.models.get(normalize_model_id(lookup))
        return list(found or ())

    def match(self, device: DeviceKeys) -> str | None:
        """The id of the definition the library would pick, or `None`."""
        candidates = self._candidates(device.model_id)
        if not candidates:
            return None
        if len(candidates) == 1 and self.zigbee_models.get(candidates[0]):
            return candidates[0]
        best: str | None = None
        best_priority: float | None = None
        for candidate in candidates:
            for fingerprint in self.fingerprints.get(candidate, ()):
                priority = fingerprint.get("priority") or 0
                if _fingerprint_matches(fingerprint, device) and (
                    best_priority is None or priority > best_priority
                ):
                    best, best_priority = candidate, priority
        if best is not None:
            return best
        if device.model_id:
            normalized = normalize_model_id(device.model_id)
            for candidate in candidates:
                if any(m in (device.model_id, normalized) for m in self.zigbee_models[candidate]):
                    return candidate
        return None
