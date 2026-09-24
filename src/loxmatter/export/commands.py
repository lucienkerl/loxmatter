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

The AcceptedCommandList is not the whole truth, though, and the second
filter here says so: a device can accept a command it cannot carry out in
any recognisable way. `profiles.capabilities` holds that knowledge - which
feature a command needs and which attribute declares it - and the gate
applies in raw mode as well, for the same reason ADMINISTRATIVE_CLUSTERS
does: raw mode widens what gets a NAME, it does not widen what a device can
actually do.

One row breaks that rule on purpose: `lumitech` (design 2026-09-24). It
names no Matter command and comes from no AcceptedCommandList - it is the
lighting controller output, appended once per light endpoint that already
carries a real light command, in both normal and raw mode alike.
"""

from __future__ import annotations

from dataclasses import dataclass

from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.paths import ACCEPTED_COMMAND_LIST_ID, parse_attribute_path
from loxmatter.profiles.capabilities import command_needs_missing_feature
from loxmatter.profiles.categories import is_light_endpoint
from loxmatter.profiles.light_commands import LIGHT_COMMAND_PAIRS, LUMITECH, LUMITECH_SLUG
from loxmatter.profiles.relevance import device_types_by_endpoint
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
            if command_needs_missing_feature(snapshot, endpoint, cluster_id, command_id):
                continue
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

    # One lighting controller output per light endpoint (design 2026-09-24,
    # 4.1): appended with its slug, never derived from the
    # AcceptedCommandList, so raw mode cannot turn it into `c-1_cmd0`. Only
    # where the endpoint carries a light command at all - an output that
    # could send nothing would be a promise the light cannot keep.
    device_types = device_types_by_endpoint(snapshot)
    light_endpoints = {
        command.endpoint
        for command in commands
        if (command.cluster_id, command.command_id) in LIGHT_COMMAND_PAIRS
        and is_light_endpoint(device_types.get(command.endpoint, frozenset()))
    }
    for endpoint in light_endpoints:
        commands.append(
            DeviceCommand(
                endpoint=endpoint,
                cluster_id=LUMITECH[0],
                command_id=LUMITECH[1],
                slug=LUMITECH_SLUG,
                takes_value=True,
            )
        )

    return sorted(commands)
