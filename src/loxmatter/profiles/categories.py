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

"""Coarse device category derived from the Matter device types.

Answers exactly one question that `relevance.py` does not answer: not
"which signals does someone want to see", but "what kind of thing is this
in the first place". The answer carries three things at once in the UI -
the sort order within a room, the tile's icon, and the search term under
which one finds all plugs in the house.

Why alongside and not inside: `relevance.is_functional` decides about a
single signal, `category_for` about an entire device. Both read the same
source (`device_types_by_endpoint`), but with a different output and no
shared state.

**The source of the type numbers** is the same as in `relevance.py`:
`matter_server.client.models.device_types`, per its own module docstring
machine-generated from the CSA specification's
`zcl/data-model/chip/matter-devices.xml`. A new entry in the table below
needs the number from that file, not from memory;
`test_every_mapped_type_exists_in_the_matter_table` checks that.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from loxmatter.profiles.relevance import POWER_SOURCE_DEVICE_TYPE, UTILITY_DEVICE_TYPES


class Category(str, Enum):
    """The order of this declaration IS the sort rank (see
    `CATEGORY_RANK`) - deliberately not the alphabetical order of the
    translated names, which would change with the language.

    The order itself follows how often one touches a device of this kind
    in a room: light and plug first, then the controls, and at the very
    back what one sets up once and then leaves alone. `OTHER` always comes
    last - a device whose types have not yet been backfilled also ends up
    there.

    `str, Enum` instead of `StrEnum`, because `Exportability` in
    `profiles/table.py` does the same thing - a second notation for the
    same thing would gain nothing."""

    LIGHT = "light"
    SOCKET = "socket"
    SWITCH = "switch"
    COVERING = "covering"
    CLIMATE = "climate"
    SENSOR = "sensor"
    LOCK = "lock"
    OTHER = "other"


CATEGORY_RANK: dict[Category, int] = {category: rank for rank, category in enumerate(Category)}

# Device types that say nothing about what the device DOES in the house -
# the same set `relevance.is_functional` already treats as management,
# plus PowerSource: a battery level does not turn a button into its own
# category.
_IGNORED_DEVICE_TYPES: frozenset[int] = UTILITY_DEVICE_TYPES | {POWER_SOURCE_DEVICE_TYPE}

# Mapping Matter device type -> category. Every number comes from
# `matter_server.client.models.device_types` (see module docstring); the
# comments name the class name there, so a lookup is possible without
# conversion.
#
# Not listed and therefore `OTHER`: appliances (0x0070-0x007C), media
# (0x0022-0x002A), energy (0x050C-0x050F), network infrastructure (0x0090,
# 0x0091), bridge management (0x000E Aggregator, 0x0013 Bridged Node). They
# either never occur on a Loxone connection at all, or would not have
# earned their own rank in a room list.
CATEGORY_BY_DEVICE_TYPE: dict[int, Category] = {
    0x0100: Category.LIGHT,  # OnOffLight
    0x0101: Category.LIGHT,  # DimmableLight
    0x010C: Category.LIGHT,  # ColorTemperatureLight
    0x010D: Category.LIGHT,  # ExtendedColorLight
    # MountedOnOffControl / MountedDimmableLoadControl are permanently
    # wired load switches - in practice a light sits behind them, not a
    # plug (that carries its own type, see below).
    0x010F: Category.LIGHT,  # MountedOnOffControl
    0x0110: Category.LIGHT,  # MountedDimmableLoadControl
    0x010A: Category.SOCKET,  # OnOffPlugInUnit
    0x010B: Category.SOCKET,  # DimmablePlugInUnit
    0x000F: Category.SWITCH,  # GenericSwitch
    0x0103: Category.SWITCH,  # OnOffLightSwitch
    0x0104: Category.SWITCH,  # DimmerSwitch
    0x0105: Category.SWITCH,  # ColorDimmerSwitch
    0x0840: Category.SWITCH,  # ControlBridge
    0x0202: Category.COVERING,  # WindowCovering
    0x0203: Category.COVERING,  # WindowCoveringController
    0x0300: Category.CLIMATE,  # HeatingCoolingUnit
    0x0301: Category.CLIMATE,  # Thermostat
    0x0309: Category.CLIMATE,  # HeatPump
    0x002B: Category.CLIMATE,  # Fan
    0x002D: Category.CLIMATE,  # AirPurifier
    0x0072: Category.CLIMATE,  # RoomAirConditioner
    0x0015: Category.SENSOR,  # ContactSensor
    0x002C: Category.SENSOR,  # AirQualitySensor
    0x0041: Category.SENSOR,  # WaterFreezeDetector
    0x0043: Category.SENSOR,  # WaterLeakDetector
    0x0044: Category.SENSOR,  # RainSensor
    0x0076: Category.SENSOR,  # SmokeCoAlarm
    0x0106: Category.SENSOR,  # LightSensor
    0x0107: Category.SENSOR,  # OccupancySensor
    0x0302: Category.SENSOR,  # TemperatureSensor
    0x0305: Category.SENSOR,  # PressureSensor
    0x0306: Category.SENSOR,  # FlowSensor
    0x0307: Category.SENSOR,  # HumiditySensor
    0x0510: Category.SENSOR,  # ElectricalSensor
    0x0850: Category.SENSOR,  # OnOffSensor
    0x000A: Category.LOCK,  # DoorLock
    0x000B: Category.LOCK,  # DoorLockController
}


def category_for(device_types: Mapping[int, frozenset[int]] | None) -> Category:
    """A device's category derived from its device types per endpoint.

    `None` (device types not yet backfilled, see
    `Store.backfill_device_types`) yields `OTHER` - the same answer as for
    a device whose types nobody can map. The UI does not distinguish the
    two cases: in both, the device is fully operable under "other", and
    the first case resolves itself on the next bridge start.

    The rule in four steps (design 5.2):

    1. Management types are dropped (`_IGNORED_DEVICE_TYPES`).
    2. Of the rest, the LOWEST endpoint counts - with Matter usually
       endpoint 1, the application endpoint. A plug with a temperature
       sensor on endpoint 2 stays a plug.
    3. If this endpoint carries several mappable types, the one with the
       lowest rank wins. That way the result does not depend on the order
       in which the device enumerates its types - a `frozenset` has none
       anyway.
    4. Nothing mappable -> `OTHER`.
    """
    if not device_types:
        return Category.OTHER

    useful = {
        endpoint: ids - _IGNORED_DEVICE_TYPES
        for endpoint, ids in device_types.items()
        if ids - _IGNORED_DEVICE_TYPES
    }
    if not useful:
        return Category.OTHER

    primary = useful[min(useful)]
    mapped = [CATEGORY_BY_DEVICE_TYPE[t] for t in primary if t in CATEGORY_BY_DEVICE_TYPE]
    if not mapped:
        return Category.OTHER
    return min(mapped, key=lambda category: CATEGORY_RANK[category])
