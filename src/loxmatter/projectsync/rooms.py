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

"""Matches loxmatter's room names against the rooms of a Loxone project
file (design `docs/superpowers/specs/
2026-09-29-loxone-rooms-in-project-sync-design.md`, sections 4 and 6).

Pure decisions, no text editing and no IDs: which Loxone room a name
means, and whether a missing one can be created. `patch.py` generates
the `U` values of created rooms, like every other new object's."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from loxmatter.projectsync.index import ProjectIndex
from loxmatter.projectsync.scan import Element

__all__ = ["RoomAssignment", "RoomStatus", "assign_rooms", "can_create_rooms", "room_key"]


class RoomStatus(StrEnum):
    FOUND = "found"
    CREATED = "created"
    # Several Loxone rooms share the folded title; the first in document
    # order is used (design section 4).
    AMBIGUOUS = "ambiguous"
    # No match, and the file has no room list to add one to (section 6.3).
    NOT_CREATABLE = "not_creatable"


@dataclass(frozen=True)
class RoomAssignment:
    # The room name as loxmatter stores it - the first spelling met, when
    # several devices spell the same room differently.
    name: str
    status: RoomStatus
    # The title of the Loxone room new objects go into: the existing
    # room's for FOUND/AMBIGUOUS, the stripped loxmatter name for CREATED.
    loxone_title: str | None
    # `U` of the existing Loxone room; `None` for CREATED (generated at
    # patch time) and NOT_CREATABLE.
    place_u: str | None

    @property
    def targets_a_room(self) -> bool:
        return self.status is not RoomStatus.NOT_CREATABLE


def room_key(name: str) -> str:
    """The form in which two room names are compared: normalized for
    case-insensitive and whitespace-insensitive matching to find the same room
    in Loxone Config instead of creating a second room."""
    return name.strip().casefold()


def can_create_rooms(index: ProjectIndex) -> bool:
    """Whether a new `Place` has somewhere to go. A self-closing
    `PlaceCaption` has no content range to append to - Loxone Config never
    writes one, since Loxone always creates a default room marked `First="true"`."""
    caption = index.place_caption
    return caption is not None and not caption.self_closing and caption.inner_end is not None


def assign_rooms(index: ProjectIndex, names: Iterable[str]) -> list[RoomAssignment]:
    """One assignment per distinct room (by `room_key`), in the order the
    names first occur; blank names are skipped."""
    places_by_key: dict[str, list[Element]] = {}
    for place in index.places:
        places_by_key.setdefault(room_key(place.attrs.get("Title", "")), []).append(place)
    creatable = can_create_rooms(index)

    assignments: list[RoomAssignment] = []
    seen: set[str] = set()
    for name in names:
        key = room_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        matches = places_by_key.get(key, [])
        if matches:
            first = matches[0]
            status = RoomStatus.AMBIGUOUS if len(matches) > 1 else RoomStatus.FOUND
            assignments.append(
                RoomAssignment(name, status, first.attrs.get("Title", name), first.attrs.get("U"))
            )
        elif creatable:
            assignments.append(RoomAssignment(name, RoomStatus.CREATED, name.strip(), None))
        else:
            assignments.append(RoomAssignment(name, RoomStatus.NOT_CREATABLE, None, None))
    return assignments
