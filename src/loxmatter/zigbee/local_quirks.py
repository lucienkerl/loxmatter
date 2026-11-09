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

"""Quirks loxmatter ships itself, for devices zha-quirks does not know.

Written with zha-quirks' own `QuirkBuilder`, so `zigbee/options.py` reads
their settings exactly like an upstream quirk's, and each one could be
contributed upstream unchanged. A quirk belongs here only for a device that
is actually in use and that zha-quirks lacks; the source of every attribute
is named next to it.

Imported only by `quirks._default_setup`, after `zhaquirks.setup()`: the
import is what registers them, and it needs the registry zhaquirks fills.
Registered after upstream's on purpose - should zha-quirks gain a quirk for
the same device, the registry keeps the first match, and upstream's wins.
"""

from __future__ import annotations

from typing import Final

import zigpy.types as t
from zhaquirks.builder import QuirkBuilder
from zhaquirks.clusters import CustomCluster
from zigpy.zcl.clusters.security import IasZone
from zigpy.zcl.foundation import DataTypeId, ZCLAttributeAccess, ZCLAttributeDef

# --------------------------------------------------------- HOBEIAN ZG-204Z --
#
# A Tuya-made PIR motion sensor sold as HOBEIAN, three of which the
# maintainer runs. It reports `HOBEIAN` / `ZG-204Z` in its Basic cluster
# (read from the bridge's zigbee database on 8 November 2026) and so matches
# none of zha-quirks' `TS0601` quirks - its siblings ZG-204ZL and ZG-204ZM
# are covered, it is not. Occupancy and battery arrive through the standard
# IAS Zone and PowerConfiguration clusters and need no quirk; the two
# settings do. Both are written on IAS Zone, as zigbee2mqtt's definition
# does (`zigbee-herdsman-converters`, `src/devices/hobeian.ts`, `zg204_attr`):
#
# - keep time: attribute 0xF001, written as uint8 (data type 0x20), WITHOUT
#   a manufacturer code: 0 = 30 s, 1 = 60 s, 2 = 120 s;
# - sensitivity: the standard `current_zone_sensitivity_level` (0x0013):
#   0 = low, 1 = medium, 2 = high.

ZG204Z_MANUFACTURER: Final = "HOBEIAN"
ZG204Z_MODEL: Final = "ZG-204Z"
ZG204Z_KEEP_TIME_ATTRIBUTE: Final = 0xF001


class ZG204ZKeepTime(t.enum8):
    """How long the sensor stays occupied after a detection."""

    _30_seconds = 0x00
    _60_seconds = 0x01
    _120_seconds = 0x02


class ZG204ZSensitivity(t.enum8):
    """The PIR sensitivity."""

    Low = 0x00
    Medium = 0x01
    High = 0x02


class ZG204ZIasZone(CustomCluster, IasZone):  # type: ignore[misc]  # zhaquirks has no stubs
    """IAS Zone with the sensor's keep-time attribute declared."""

    class AttributeDefs(IasZone.AttributeDefs):
        keep_time: Final = ZCLAttributeDef(
            id=ZG204Z_KEEP_TIME_ATTRIBUTE,  # type: ignore[arg-type]  # zigpy converts it
            type=ZG204ZKeepTime,
            # zigbee2mqtt writes the value as uint8, not as enum8: the
            # declared type would otherwise put 0x30 on the wire.
            zcl_type=DataTypeId.uint8,
            access=ZCLAttributeAccess.Read | ZCLAttributeAccess.Write,
            is_manufacturer_specific=False,
        )


(
    QuirkBuilder(ZG204Z_MANUFACTURER, ZG204Z_MODEL)
    .replaces(ZG204ZIasZone)
    .enum(
        ZG204ZIasZone.AttributeDefs.keep_time.name,
        ZG204ZKeepTime,
        IasZone.cluster_id,
        translation_key="keep_time",
        fallback_name="Keep time",
    )
    .enum(
        IasZone.AttributeDefs.current_zone_sensitivity_level.name,
        ZG204ZSensitivity,
        IasZone.cluster_id,
        translation_key="sensitivity",
        fallback_name="Sensitivity",
    )
    .add_to_registry()
)
