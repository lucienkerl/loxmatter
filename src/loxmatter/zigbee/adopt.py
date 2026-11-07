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

"""Making a Zigbee device one of the bridge's own - the store half.

Two callers: the pairing tab's naming route (`api/zigbee.py`) and the Home
Assistant migration (`homeassistant/migration.py`). One sequence, because
the order is the point: `register_device` before `register_signals` before
`register_commands`, since the last two need the freshly assigned id, and
`set_room` afterwards whenever a room was given, because `register_device`'s
own `room` argument only takes effect on a newly inserted row.
"""

from __future__ import annotations

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store


def register_zigbee_device(
    store: Store, snapshot: NodeSnapshot, *, name: str | None, room: str | None
) -> int:
    device_id = store.register_device(snapshot, room=room)
    if name is not None:
        store.rename_device(device_id, name)
    if room is not None:
        store.set_room(device_id, room)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot))
    return device_id
