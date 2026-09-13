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

LIGHT_COMMAND_PAIRS: Final = frozenset(
    {OFF, ON, TOGGLE, LEVEL, LEVEL_ONOFF, COLOUR_HS, COLOUR_XY, COLOUR_TEMPERATURE}
)
