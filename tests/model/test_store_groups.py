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
import sqlite3
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


def test_create_group_rejects_a_duplicated_member_and_leaves_no_ghost_row(store, lamps, tmp_path):
    """Regression for the missing rollback guard: a duplicate id used to
    raise `sqlite3.IntegrityError` from mid-loop, after `device_group` and
    some `device_group_member` rows had already been executed but not
    committed - and since nothing rolled the transaction back, those rows
    sat in the connection's open implicit transaction until any later,
    completely unrelated write committed them.

    Asserted against a REOPENED `Store` on the same file: that is exactly
    the bug's signature - `store.groups()` looking empty on the live
    connection proves nothing once an uncommitted row can still be
    flushed to disk by someone else's `commit()`.
    """
    with pytest.raises(ValueError):
        store.create_group("Dup", [lamps[0], lamps[0]])
    assert store.groups() == []

    # An unrelated write that commits - if the group row survived
    # uncommitted, this is what would resurrect it on disk.
    store.rename_device(lamps[0], "Renamed")
    store.close()

    reopened = Store(tmp_path / "test.sqlite")
    try:
        assert reopened.groups() == []
    finally:
        reopened.close()


def test_set_group_members_rejects_a_duplicated_member_and_keeps_the_old_membership(store, lamps):
    group = store.create_group("Living room", lamps)
    with pytest.raises(ValueError):
        store.set_group_members(group.id, [lamps[0], lamps[0]])
    assert [d.id for d in store.group_members(group.id)] == lamps


class _FailSecondMemberInsert:
    """Proxies a real `sqlite3.Connection`, forcing the SECOND `INSERT INTO
    device_group_member` to fail as `sqlite3.IntegrityError` - standing in
    for any write-time SQLite error that reaches the write loop after the
    duplicate-id and category checks have already passed, not only a
    duplicate id. `sqlite3.Connection.execute` cannot be monkeypatched
    directly (it is a read-only attribute of an immutable C type), so this
    wraps the connection instead and is installed in place of `store._db`.
    """

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self._insert_count = 0

    def execute(self, sql, parameters=()):
        if sql.startswith("INSERT INTO device_group_member"):
            self._insert_count += 1
            if self._insert_count == 2:
                raise sqlite3.IntegrityError("simulated failure past the duplicate check")
        return self._real.execute(sql, parameters)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_create_group_rolls_back_a_write_time_failure_and_leaves_no_ghost_row(
    store, lamps, tmp_path, monkeypatch
):
    """Regression for the `except (ValueError, sqlite3.Error):
    self._db.rollback(); raise` guard itself, as distinct from the
    duplicate-id test above: here both members are distinct and valid, so
    the upfront duplicate check passes and the write loop actually starts.
    The second `INSERT INTO device_group_member` then fails the way a real
    SQLite error would - a full disk, a corrupted index, anything past
    dedup - and without `self._db.rollback()` in the `except` clause,
    `device_group` and the first `device_group_member` row would stay in
    the connection's open transaction, waiting for some later, unrelated
    commit to flush them to disk - exactly the mechanism the duplicate-id
    test's docstring above describes, reached from the other branch.
    """
    monkeypatch.setattr(store, "_db", _FailSecondMemberInsert(store._db))

    with pytest.raises(sqlite3.IntegrityError):
        store.create_group("Flaky", lamps)
    assert store.groups() == []

    # An unrelated write that commits - if the group row survived
    # uncommitted, this is what would resurrect it on disk.
    store.rename_device(lamps[0], "Renamed")
    store.close()

    reopened = Store(tmp_path / "test.sqlite")
    try:
        assert reopened.groups() == []
    finally:
        reopened.close()


def test_set_group_members_rolls_back_a_write_time_failure_and_keeps_the_old_membership(
    store, lamps, tmp_path, monkeypatch
):
    """Same construction as the `create_group` test above, applied to
    `set_group_members`: the DELETE and the first re-INSERT succeed, the
    second re-INSERT fails, and without the rollback guard the DELETE and
    that first re-INSERT would survive uncommitted, to be flushed to disk
    by a later unrelated commit - as a membership of `[lamps[0]]`, not the
    `[lamps[1]]` that was actually there before this call.

    The old membership is deliberately `[lamps[1]]`, not `[lamps[0]]`: the
    attempted new list is `[lamps[0], lamps[1]]`, so the first (successful)
    re-INSERT writes `lamps[0]` - the same id an unguarded rollback would
    leave behind. Starting from `[lamps[0]]` would make that wrong interim
    state look identical to the correct restored one and the assertion
    below would pass whether or not the guard exists.
    """
    group = store.create_group("Living room", [lamps[1]])

    monkeypatch.setattr(store, "_db", _FailSecondMemberInsert(store._db))

    with pytest.raises(sqlite3.IntegrityError):
        store.set_group_members(group.id, [lamps[0], lamps[1]])
    assert [d.id for d in store.group_members(group.id)] == [lamps[1]]

    # An unrelated write that commits - if the DELETE and the first
    # re-INSERT survived uncommitted, this is what would flush the wrong
    # membership (`[lamps[0]]`) to disk.
    store.rename_device(lamps[0], "Renamed")
    store.close()

    reopened = Store(tmp_path / "test.sqlite")
    try:
        assert [d.id for d in reopened.group_members(group.id)] == [lamps[1]]
    finally:
        reopened.close()
