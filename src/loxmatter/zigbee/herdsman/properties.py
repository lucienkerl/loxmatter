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

"""From a library property to a snapshot path, a value and an option
(design 2026-11-09, section 8.4).

Every property the library reports - from a ZCL attribute, a Tuya data
point or a converter in the engine - arrives here the same way: a name
(`keep_time`), a value as the library spells it (`120`, `"medium"`,
`true`) and the definition's expose describing it.

**Matter first.** Where a Matter cluster means the same thing, the property
goes to that Matter path, scaled to Matter's unit: a Tuya sensor's
`temperature` is then the same `1026/0` a Zigbee temperature sensor
reports, and everything downstream treats it the same. The table is
`_MATTER`, below.

**Everything else** goes to one manufacturer cluster loxmatter reserves,
`VENDOR_PROPERTIES_CLUSTER`, under the property's number from
`profiles/vendor_properties.json`. That file is committed and only ever
appended to, so a property keeps its path - and its signal its Loxone key -
across library updates.

**One value scale** for what Loxone sees, the same rule as design
2026-11-08 section 2: a number is the number; a binary is 1 or 0; an enum
whose values are distinct numbers (`keep_time`: 30, 60, 120) is the number,
any other enum (`sensitivity`: low, medium, high) is its position."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from loxmatter.device_config import Choice, ConfigOption
from loxmatter.profiles.table import VENDOR_PROPERTIES_CLUSTER, vendor_property_ids

__all__ = [
    "Placement",
    "device_type_for",
    "endpoint_of",
    "is_reading",
    "is_setting",
    "library_device_types",
    "library_value",
    "loxone_value",
    "option_for",
    "place",
    "vendor_path",
    "vendor_property_ids",
]
from loxmatter.zigbee.herdsman.catalogue import ACCESS_SET, ACCESS_STATE, Expose

# The Matter paths a property means the same as: (endpoint or None for the
# device's own endpoint, cluster, attribute, conversion). The endpoint 0 of
# PowerSource is the root endpoint, as `translate.py` writes it.
_Conversion = Callable[[Any, Expose], object | None]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return float(value)


def _times(factor: float) -> _Conversion:
    def convert(value: Any, _expose: Expose) -> object | None:
        number = _number(value)
        return None if number is None else round(number * factor)

    return convert


def _flag(value: Any, expose: Expose) -> object | None:
    if expose.value_on is not None and value == expose.value_on:
        return True
    if expose.value_off is not None and value == expose.value_off:
        return False
    return value if isinstance(value, bool) else None


def _occupancy(value: Any, expose: Expose) -> object | None:
    flag = _flag(value, expose)
    return None if flag is None else int(bool(flag))


def _illuminance(value: Any, _expose: Expose) -> object | None:
    # Matter and the ZCL both carry 10000 * log10(lux) + 1; 0 means too dark
    # to measure.
    number = _number(value)
    if number is None:
        return None
    return 0 if number <= 0 else round(10000 * math.log10(number) + 1)


def _unit_is(unit: str, convert: _Conversion) -> _Conversion:
    """`voltage` is a battery's millivolts on one device and a socket's volts
    on the next; the expose's unit says which."""

    def guarded(value: Any, expose: Expose) -> object | None:
        return convert(value, expose) if expose.unit == unit else None

    return guarded


@dataclass(frozen=True)
class _MatterTarget:
    endpoint: int | None
    cluster: int
    attribute: int
    convert: _Conversion
    # The Matter device type an endpoint with this reading is, when the
    # endpoint itself declares none that fits (a Tuya sensor declares a
    # smart plug).
    device_type: int | None = None


_MATTER: dict[str, tuple[_MatterTarget, ...]] = {
    "occupancy": (_MatterTarget(None, 1030, 0, _occupancy, 0x0107),),
    # A radar's `presence` is what Matter calls occupancy: someone is there.
    "presence": (_MatterTarget(None, 1030, 0, _occupancy, 0x0107),),
    "contact": (_MatterTarget(None, 69, 0, _flag, 0x0015),),
    "water_leak": (_MatterTarget(None, 69, 0, _flag, 0x0043),),
    "temperature": (_MatterTarget(None, 1026, 0, _times(100), 0x0302),),
    "humidity": (_MatterTarget(None, 1029, 0, _times(100), 0x0307),),
    "illuminance": (_MatterTarget(None, 1024, 0, _illuminance, 0x0106),),
    "battery": (_MatterTarget(0, 47, 12, _times(2)),),
    "voltage": (
        _MatterTarget(0, 47, 11, _unit_is("mV", _times(1))),
        _MatterTarget(None, 144, 4, _unit_is("V", _times(1000))),
    ),
    "power": (_MatterTarget(None, 144, 8, _unit_is("W", _times(1000))),),
    "current": (_MatterTarget(None, 144, 5, _unit_is("A", _times(1000))),),
}

# Which device type wins when an endpoint has several readings: a motion
# sensor that also measures light is a motion sensor.
_DEVICE_TYPE_ORDER = (
    "occupancy",
    "presence",
    "contact",
    "water_leak",
    "temperature",
    "humidity",
    "illuminance",
)

# What the library exposes for a light, a switch and their kin, and for
# actions: operated through the native command path, never as settings.
_NOT_SETTINGS = frozenset(
    {
        "state",
        "brightness",
        "color",
        "color_temp",
        "color_xy",
        "color_hs",
        "position",
        "tilt",
        "effect",
        "identify",
        "action",
    }
)


def _is_numberlike(values: tuple[Any, ...]) -> bool:
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return len(numbers) == len(values) and len(set(numbers)) == len(numbers)


def loxone_value(expose: Expose, value: Any) -> object | None:
    """The value Loxone and the web UI see for a property, or `None` when
    it has none (a text, a structure, a value the expose does not list)."""
    if expose.type == "numeric":
        return _number(value)
    if expose.type == "binary":
        flag = _flag(value, expose)
        return None if flag is None else (1.0 if flag else 0.0)
    if expose.type == "enum":
        if value not in expose.values:
            return None
        if _is_numberlike(expose.values):
            return float(value)
        return float(expose.values.index(value))
    return None


def library_value(expose: Expose, raw: float | bool) -> Any:
    """The library's value for a raw option value (`device_config.to_raw`
    has checked it against the option already): the inverse of the option
    built by `option_for`."""
    if expose.type == "binary":
        return expose.value_on if int(raw) == 1 else expose.value_off
    if expose.type == "enum":
        return expose.values[int(raw)]
    step = expose.value_step
    if step is not None and 0 < step < 1:
        return round(float(raw) * step, 10)
    return raw


@dataclass(frozen=True)
class Placement:
    """Where one property lands in the snapshot."""

    path: str
    value: object
    device_type: int | None = None


def endpoint_of(expose: Expose, endpoints: Mapping[str, int] | None, default: int) -> int:
    if expose.endpoint and endpoints and expose.endpoint in endpoints:
        return endpoints[expose.endpoint]
    return default


def vendor_path(endpoint: int, property_name: str) -> str | None:
    number = vendor_property_ids().get(property_name)
    return None if number is None else f"{endpoint}/{VENDOR_PROPERTIES_CLUSTER}/{number}"


def place(
    expose: Expose,
    value: Any,
    *,
    endpoint: int,
) -> Placement | None:
    """Where a reported property goes, with its value; `None` when it has
    no value Loxone could use."""
    for target in _MATTER.get(expose.name, ()):
        converted = target.convert(value, expose)
        if converted is None:
            continue
        where = endpoint if target.endpoint is None else target.endpoint
        return Placement(
            path=f"{where}/{target.cluster}/{target.attribute}",
            value=converted,
            device_type=target.device_type,
        )
    converted = loxone_value(expose, value)
    if converted is None:
        return None
    path = vendor_path(endpoint, expose.property)
    return None if path is None else Placement(path=path, value=converted)


def device_type_for(names: set[str]) -> int | None:
    """The Matter device type for an endpoint that reports these properties."""
    for name in _DEVICE_TYPE_ORDER:
        if name in names:
            target = _MATTER[name][0]
            return target.device_type
    return None


def library_device_types() -> frozenset[int]:
    """Every Matter device type the property mapping can produce, for
    `translate._all_mapped_matter_types`."""
    return frozenset(
        target.device_type
        for targets in _MATTER.values()
        for target in targets
        if target.device_type is not None
    )


def is_setting(expose: Expose) -> bool:
    """Whether an expose is a configuration value loxmatter offers: settable,
    not something the native command path operates, not part of a light's or
    a switch's controls, and not a reading Matter already names."""
    return (
        bool(expose.access & ACCESS_SET)
        and expose.parent is None
        and expose.name not in _NOT_SETTINGS
        and expose.name not in _MATTER
        and expose.type in ("numeric", "binary", "enum")
        and (expose.type != "enum" or bool(expose.values))
    )


def is_reading(expose: Expose) -> bool:
    return bool(expose.access & ACCESS_STATE) and expose.type in ("numeric", "binary", "enum")


def option_for(expose: Expose, *, endpoint: int) -> ConfigOption | None:
    """The `ConfigOption` for a settable property, on the vendor cluster
    under the property's number - so its command row, its pending write and
    its reading all share one path."""
    number = vendor_property_ids().get(expose.property)
    if number is None or not is_setting(expose):
        return None
    common: dict[str, Any] = {
        "endpoint": endpoint,
        "cluster_id": VENDOR_PROPERTIES_CLUSTER,
        "attribute_id": number,
        "name": expose.property,
        "fallback_title": expose.label or expose.property.replace("_", " ").capitalize(),
        "translation_key": expose.name,
        "unit": expose.unit,
    }
    if expose.type == "binary":
        return ConfigOption(kind="switch", on_raw=1, off_raw=0, **common)
    if expose.type == "enum":
        numeric = _is_numberlike(expose.values)
        choices = tuple(
            Choice(raw=index, name=str(value), number=float(value) if numeric else None)
            for index, value in enumerate(expose.values)
        )
        return ConfigOption(kind="enum", choices=choices, **common)
    step = expose.value_step
    multiplier = float(step) if step is not None and 0 < step < 1 else 1.0
    return ConfigOption(
        kind="number",
        minimum=_number(expose.value_min),
        maximum=_number(expose.value_max),
        step=_number(step),
        multiplier=multiplier,
        **common,
    )
