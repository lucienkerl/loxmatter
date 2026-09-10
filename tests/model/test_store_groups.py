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

"""Groups in the store - see design 2026-09-10, sections 2 and 4."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import (
    CategoryMismatchError,
    Store,
    UnknownGroupError,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite")
    yield s
    s.close()


@pytest.fixture
def lamps(store):
    """Two LIGHT devices of differing capability - the intersection case."""
    ids = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = load(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        ids.append(device_id)
    return ids


@pytest.fixture
def plug(store):
    snapshot = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    return device_id


def test_a_new_group_takes_its_category_from_its_first_member(store, lamps):
    group = store.create_group("Living room", lamps)
    assert group.category == "light"
    assert group.label == "Living room"
    assert group.room is None


def test_a_member_of_another_category_is_refused(store, lamps, plug):
    with pytest.raises(CategoryMismatchError):
        store.create_group("Mixed", [lamps[0], plug])


def test_the_category_is_still_enforced_after_the_group_ran_empty(store, lamps, plug):
    """The category is stored, not derived - an emptied group must keep
    refusing, which a derivation from the members could not do."""
    group = store.create_group("Living room", lamps)
    store.set_group_members(group.id, [])
    with pytest.raises(CategoryMismatchError):
        store.set_group_members(group.id, [plug])


def test_a_device_may_belong_to_several_groups(store, lamps):
    first = store.create_group("Living room", lamps)
    second = store.create_group("Ground floor", [lamps[0]])
    assert [d.id for d in store.group_members(first.id)] == lamps
    assert [d.id for d in store.group_members(second.id)] == [lamps[0]]


def test_room_and_label_survive_a_round_trip(store, lamps):
    group = store.create_group("Living room", lamps, room="Living room")
    store.rename_group(group.id, "Ceiling")
    store.set_group_room(group.id, "Hallway")
    reread = store.group(group.id)
    assert (reread.label, reread.room) == ("Ceiling", "Hallway")


def test_an_empty_room_name_becomes_no_room(store, lamps):
    """The same encoding as `device.room` - `_normalized_room` turns a
    blank name into NULL, so "" can never be a real room."""
    group = store.create_group("Living room", lamps, room="  ")
    assert store.group(group.id).room is None


def test_an_unknown_group_raises_without_repr_quotes(store):
    with pytest.raises(UnknownGroupError) as excinfo:
        store.group(999)
    assert not str(excinfo.value).startswith("'")


def test_a_deleted_group_is_gone(store, lamps):
    group = store.create_group("Living room", lamps)
    store.delete_group(group.id)
    with pytest.raises(UnknownGroupError):
        store.group(group.id)
    assert store.groups() == []


def test_a_group_needs_at_least_one_member_at_creation(store):
    """The first member is what fixes the category (design 2.1)."""
    with pytest.raises(ValueError):
        store.create_group("Empty", [])


def test_membership_of_a_forgotten_device_is_dropped(store, lamps):
    group = store.create_group("Living room", lamps)
    store.forget_device(lamps[1])
    assert [d.id for d in store.group_members(group.id)] == [lamps[0]]
