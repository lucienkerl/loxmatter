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

"""Derives from AcceptedCommandList what Loxone is allowed to tell a device.

Not from the attributes: Matter attributes are overwhelmingly read-only,
and an output command per readable attribute would be pointless 95 percent
of the time.

An allow list rather than a deny list. For attributes, unknowns are passed
through generously; for commands that would be the wrong way round,
because the accepted commands include the administrative clusters -
RemoveFabric, commissioning, TestEventTrigger. ADMINISTRATIVE_CLUSTERS
stays blocked even in raw mode.
"""

from __future__ import annotations

from dataclasses import dataclass

from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.paths import ACCEPTED_COMMAND_LIST_ID, parse_attribute_path
from loxmatter.profiles.table import (
    ADMINISTRATIVE_CLUSTERS,
    command_slug,
    command_takes_value,
)


@dataclass(frozen=True, order=True)
class DeviceCommand:
    endpoint: int
    cluster_id: int
    command_id: int
    slug: str
    takes_value: bool


def extract_commands(snapshot: NodeSnapshot, *, raw: bool = False) -> list[DeviceCommand]:
    """All commands that may appear as a Loxone output."""
    commands: list[DeviceCommand] = []

    for path, value in snapshot.attributes.items():
        try:
            endpoint, cluster_id, attribute_id = parse_attribute_path(path)
        except ValueError:
            continue
        if attribute_id != ACCEPTED_COMMAND_LIST_ID:
            continue
        if cluster_id in ADMINISTRATIVE_CLUSTERS:
            continue
        if not isinstance(value, (list, tuple)):
            continue

        for command_id in (int(c) for c in value if isinstance(c, (int, float))):
            slug = command_slug(cluster_id, command_id)
            if slug is None:
                if not raw:
                    continue
                slug = f"c{cluster_id}_cmd{command_id}"
            commands.append(
                DeviceCommand(
                    endpoint=endpoint,
                    cluster_id=cluster_id,
                    command_id=command_id,
                    slug=slug,
                    takes_value=command_takes_value(cluster_id, command_id),
                )
            )

    return sorted(commands)
