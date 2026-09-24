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

"""The commands a light group adapts per member (design 2026-09-13).

Pairs of (cluster, command). Its own module in `profiles` rather than in
`commands`, because `model.store` needs it and `commands` imports the
store - a constant here keeps that import one-way.
"""

from __future__ import annotations

from typing import Final

OFF: Final = (6, 0)
ON: Final = (6, 1)
TOGGLE: Final = (6, 2)
LEVEL: Final = (8, 0)
LEVEL_ONOFF: Final = (8, 4)
COLOUR_HS: Final = (768, 6)
COLOUR_XY: Final = (768, 7)
COLOUR_TEMPERATURE: Final = (768, 10)

# The lighting controller output (design 2026-09-24): not a Matter command
# but one Loxone value - an RGB colour or a Lumitech white, each with its
# brightness - that `commands/adapt.py` turns into whatever the light
# carries. A negative cluster id exists in neither Matter nor Zigbee, so the
# pair can never coincide with a real command.
LUMITECH: Final = (-1, 0)
LUMITECH_SLUG: Final = "lumitech"

LIGHT_COMMAND_PAIRS: Final = frozenset(
    {OFF, ON, TOGGLE, LEVEL, LEVEL_ONOFF, COLOUR_HS, COLOUR_XY, COLOUR_TEMPERATURE, LUMITECH}
)


def is_expert_light_command(pair: tuple[int, int], endpoint_has_lumitech: bool) -> bool:
    """Whether a command belongs in the expert area rather than the export
    by default (design 2026-09-24, 4.2): a single light command beside a
    `lumitech` output, which already carries all of it. Whether the endpoint
    is a light is read from the rows themselves - it has a `lumitech` row -
    not from a second source."""
    return endpoint_has_lumitech and pair in LIGHT_COMMAND_PAIRS and pair != LUMITECH
