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

"""Conversions that `scale` cannot express (design 2026-10-01, section 3).

`scale` in `clusters.yaml` multiplies. Two values need more than that: the
ZCL/Matter illuminance encoding is logarithmic, and Kelvin is the
reciprocal of mired. An attribute entry names one of the functions below
with `convert:`; nothing from the YAML file is ever evaluated, it only
picks an entry of `CONVERSIONS`.

Each function takes the raw device number and returns the Loxone value, or
`None` for "not sendable" - the same meaning `loxone.values.to_loxone_value`
gives `None`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final


def lux_from_log(value: float) -> float | None:
    """`10 ** ((value - 1) / 10000)`, the inverse of the encoding that ZCL
    and Matter share for `IlluminanceMeasurement.MeasuredValue`.

    0 means "too low to be measured" in both specifications and becomes
    exactly 0 lx; the formula would give 0.99977. The invalid marker
    (Matter `null`, ZCL 0xFFFF) never arrives here: `classify` rejects
    `None`, and the Zigbee edge suppresses 0xFFFF
    (`zigbee.translate`)."""
    if value == 0:
        return 0.0
    return float(10 ** ((value - 1) / 10000))


def kelvin_from_mired(value: float) -> float | None:
    """`1e6 / value`. Mired outside the specified 1-65279 cannot be a
    colour temperature; 0 would divide by zero. Such a value is not sent
    rather than replaced by an invented one."""
    if value <= 0:
        return None
    return 1_000_000 / value


CONVERSIONS: Final[Mapping[str, Callable[[float], float | None]]] = {
    "lux_from_log": lux_from_log,
    "kelvin_from_mired": kelvin_from_mired,
}
