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

"""Tests for `projectsync.rooms` - design 2026-09-29, sections 4 and 6."""

from __future__ import annotations

from loxmatter.projectsync.index import build_index
from loxmatter.projectsync.rooms import RoomAssignment, RoomStatus, assign_rooms, room_key


def test_room_key_ignores_case_and_surrounding_whitespace():
    assert room_key("  Küche ") == room_key("küche") == room_key("KÜCHE")


def test_an_existing_room_is_found_whatever_the_case(rooms_project):
    index = build_index(rooms_project)
    assert assign_rooms(index, ["küche"]) == [
        RoomAssignment("küche", RoomStatus.FOUND, "Küche", "3000-0002-0000-aaaaaaaaaaaaaaaa")
    ]


def test_a_missing_room_is_created_under_its_loxmatter_name(rooms_project):
    index = build_index(rooms_project)
    [assignment] = assign_rooms(index, ["Werkstatt"])
    assert assignment == RoomAssignment("Werkstatt", RoomStatus.CREATED, "Werkstatt", None)
    assert assignment.targets_a_room


def test_one_assignment_per_room_however_many_names_share_it(rooms_project):
    index = build_index(rooms_project)
    assignments = assign_rooms(index, ["Werkstatt", "Küche", "werkstatt", "Küche"])
    assert [a.name for a in assignments] == ["Werkstatt", "Küche"]


def test_blank_names_get_no_assignment(rooms_project):
    index = build_index(rooms_project)
    assert assign_rooms(index, ["", "   "]) == []


def test_two_loxone_rooms_with_one_title_use_the_first(rooms_project):
    duplicate = (
        '<C Type="Place" V="175" U="3000-0003-0000-aaaaaaaaaaaaaaaa" Title="KÜCHE" WF="16384"/>'
    )
    anchor = '\t\t</C>\r\n\t\t<C Type="LoxCaption"'
    assert anchor in rooms_project
    project = rooms_project.replace(anchor, duplicate + anchor, 1)
    index = build_index(project)
    assert assign_rooms(index, ["Küche"]) == [
        RoomAssignment("Küche", RoomStatus.AMBIGUOUS, "Küche", "3000-0002-0000-aaaaaaaaaaaaaaaa")
    ]


def test_without_a_room_list_nothing_can_be_created(sample_project):
    index = build_index(sample_project)
    [assignment] = assign_rooms(index, ["Küche"])
    assert assignment == RoomAssignment("Küche", RoomStatus.NOT_CREATABLE, None, None)
    assert not assignment.targets_a_room


def test_an_old_project_without_rights_groups_can_still_get_rooms(places_only_project):
    index = build_index(places_only_project)
    assert assign_rooms(index, ["Werkstatt"])[0].status is RoomStatus.CREATED
