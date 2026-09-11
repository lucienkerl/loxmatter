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

"""Device sources: what produces devices, and the call that reaches them
(design 2026-09-11, section 3).

Matter's data model stays loxmatter's internal language. Every source
translates into it at its own edge, so everything past this package -
discovery, profiles, runtime, export, groups - does not know which source
a device came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loxmatter.matter.models import Technology

__all__ = ["DeviceCall", "Technology"]


@dataclass(frozen=True)
class DeviceCall:
    """One command to one endpoint of one device. Formerly `MatterCall`.

    `cluster_id`, `command_id` and the `payload` field names follow the
    Matter data model (`commands/translate.py` builds them); a non-Matter
    source renames the fields at its own edge. Zigbee's IDs for every
    command translated today are identical (design 2026-09-11, 1.1)."""

    technology: Technology
    address: str
    endpoint: int
    cluster_id: int
    command_id: int
    payload: dict[str, object] = field(default_factory=dict)
