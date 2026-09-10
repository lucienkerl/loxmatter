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
    UnknownCommandError,
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


def _slugs(store, group_id):
    return sorted(c.slug for c in store.group_commands(group_id))


@pytest.fixture
def lamps_with_commands(store, lamps):
    """Registers the command rows the intersection is computed from."""
    from loxmatter.export.commands import extract_commands

    for device_id, name in zip(
        lamps, ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"), strict=True
    ):
        snapshot = load(name)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
    return lamps


def test_the_group_offers_only_what_every_member_accepts(store, lamps_with_commands):
    colour_only = store.create_group("Colour", [lamps_with_commands[0]])
    both = store.create_group("Both", lamps_with_commands)
    assert "color" in _slugs(store, colour_only.id)
    assert "color" not in _slugs(store, both.id)
    assert {"on", "off", "toggle"} <= set(_slugs(store, both.id))


def _rowid_for(store, key):
    return store._db.execute("SELECT rowid FROM group_command WHERE key = ?", (key,)).fetchone()[0]


def test_a_surviving_command_keeps_its_key(store, lamps_with_commands):
    """A row that survives a membership change must be the SAME row, not
    a fresh one that merely landed on an identical key. `key` is
    `f"g{group_id}_{sample.slug}"` (`register_group_commands`) - fully
    deterministic from `group_id` and `slug`, with no randomness and no
    counter. That means a naive implementation that deletes every row for
    the group and reinserts them all on each call would still produce
    `after["on"] == before["on"]` - same group, same slug, same string.
    Comparing the key alone cannot tell "preserved through an UPDATE"
    apart from "destroyed and reborn identical". SQLite's `rowid` can:
    `group_command.id` is `INTEGER PRIMARY KEY AUTOINCREMENT`, so a
    DELETE-then-INSERT gets a strictly new, never-reused rowid even
    though the key string comes out the same, while an UPDATE of the
    existing row keeps it. Asserting the rowid is unchanged is therefore
    the assertion that actually distinguishes the two implementations.
    """
    group = store.create_group("Colour", [lamps_with_commands[0]])
    before = {c.slug: c.key for c in store.group_commands(group.id)}
    before_rowid = _rowid_for(store, before["on"])
    store.set_group_members(group.id, lamps_with_commands)
    after = {c.slug: c.key for c in store.group_commands(group.id)}
    assert after["on"] == before["on"]
    assert _rowid_for(store, after["on"]) == before_rowid


def test_a_command_that_leaves_the_intersection_stops_resolving(store, lamps_with_commands):
    group = store.create_group("Colour", [lamps_with_commands[0]])
    key = next(c.key for c in store.group_commands(group.id) if c.slug == "color")
    store.set_group_members(group.id, lamps_with_commands)
    with pytest.raises(UnknownCommandError):
        store.resolve_group_command(key)


def test_the_group_survives_losing_every_member(store, lamps_with_commands):
    group = store.create_group("Colour", lamps_with_commands)
    store.set_group_members(group.id, [])
    assert store.group(group.id).label == "Colour"
    assert store.group_commands(group.id) == []


def test_forgetting_a_member_recomputes_the_intersection(store, lamps_with_commands):
    group = store.create_group("Both", lamps_with_commands)
    assert "color" not in _slugs(store, group.id)
    store.forget_device(lamps_with_commands[1])
    assert "color" in _slugs(store, group.id)


def test_an_offline_member_changes_nothing(store, lamps_with_commands):
    """Design 2026-09-10, section 10: "An offline member changes
    nothing." There is no way to stage that scenario at this layer, and
    staging a fake one would misrepresent what the store can even
    express: `StoredDevice`'s docstring is explicit that reachability is
    `Runtime` state fed from Matter subscriptions, never a stored column
    - a `Store` opened directly, as every fixture in this module does,
    has no slot to hold "offline" in the first place, and this test file
    never touches `Runtime` at all.

    So the requirement holds structurally, not by any check this test
    could add: `register_group_commands` reads each member's accepted
    commands through `self.commands(device.id)` (the `command` table,
    populated once per interview by `register_commands`), and
    `group_members` filters candidates on `device.active` - "not
    forgotten", not "currently reachable". Neither reads `Runtime` or
    anything that could vary with a real device going on- or offline.
    There is simply no code path between a device's live reachability
    and this computation.

    What this test CAN demonstrate is the closest honest approximation:
    recomputing the intersection for a group of still-registered
    (`active`) members, with no device or runtime state touched anywhere
    in between, changes nothing - which is what "an offline member
    changes nothing" reduces to for a component that has no notion of
    "online" to begin with.
    """
    group = store.create_group("Both", lamps_with_commands)
    before = _slugs(store, group.id)

    store.register_group_commands(group.id)

    after = _slugs(store, group.id)
    assert after == before
    assert before  # not a vacuous comparison of two empty sets


# A fixed sentinel far in the past. `_now()` always produces a real
# UTC timestamp (2026 or later, given `now_iso`'s implementation), so no
# genuine call can ever coincidentally reproduce this exact string - unlike
# comparing two `_now()` calls against each other, which could in principle
# land on the same microsecond and make an "it advanced" assertion flaky.
_LONG_AGO = "2000-01-01T00:00:00.000000+00:00"


def test_forgetting_a_member_that_shrinks_the_intersection_advances_updated_at(
    store, lamps_with_commands
):
    """Regression for the design 4.3 gap: `forget_device` recomputes the
    intersection via `register_group_commands`, and a command that drops
    out of it (here: `color`, once the colour-only lamp is removed) makes
    the group's exported command set stale - the export tab must be able
    to see that, exactly as for a device (section 4.3). Before this fix,
    `register_group_commands` never touched `device_group.updated_at` at
    all, so the group would still read "unchanged" here even though a key
    it used to export just stopped resolving.

    `updated_at` is pinned to `_LONG_AGO` directly (bypassing `_now()`)
    rather than compared against a timestamp read a moment earlier in the
    test: two real `_now()` calls in the same test carry a - remote but
    real - chance of landing on the same microsecond, which would make
    an inequality assertion pass or fail by luck. A fixed sentinel from
    the year 2000 cannot equal anything `_now()` produces today, so the
    assertion below can only pass because the stamp genuinely moved.
    """
    group = store.create_group("Both", lamps_with_commands)
    store._db.execute("UPDATE device_group SET updated_at = ? WHERE id = ?", (_LONG_AGO, group.id))
    store._db.commit()

    store.forget_device(lamps_with_commands[1])

    assert store.group(group.id).updated_at != _LONG_AGO


def test_a_recompute_that_changes_nothing_leaves_updated_at_alone(store, lamps_with_commands):
    """The flip side of the gap above: `register_group_commands` runs on
    every membership change, including ones where the intersection ends up
    identical to what it already was. Stamping `updated_at` unconditionally
    on every call - rather than only when a row was actually inserted,
    deleted, or altered - would make every group permanently read "changed
    since the last export", which tells the export tab nothing, the same
    uselessness as never stamping at all.

    This compares the stored value to itself rather than to a freshly
    taken timestamp, so it cannot pass by accident: if the recompute below
    touches the row, the stored string changes to whatever `_now()`
    produced at that moment, and the equality fails regardless of how
    close together the two calls landed.
    """
    group = store.create_group("Both", lamps_with_commands)
    before = store.group(group.id).updated_at

    store.register_group_commands(group.id)

    assert store.group(group.id).updated_at == before


def test_group_keys_can_never_collide_with_device_keys(store, lamps_with_commands):
    """The `d`/`g` prefixes are a convention, not an SQL guarantee - this
    is the assertion `resolve_command`'s two-step lookup rests on."""
    group = store.create_group("Both", lamps_with_commands)
    device_keys = {c.key for d in lamps_with_commands for c in store.commands(d)}
    group_keys = {c.key for c in store.group_commands(group.id)}
    assert device_keys and group_keys
    assert device_keys.isdisjoint(group_keys)


class _FailSecondGroupCommandWrite:
    """Proxies a real `sqlite3.Connection`, forcing the SECOND write against
    `group_command` (DELETE, UPDATE or INSERT, whichever the scenario
    produces) to fail as `sqlite3.IntegrityError` - standing in for any
    write-time SQLite error reaching `register_group_commands`'s own
    DELETE/INSERT/UPDATE loops, the same construction as
    `_FailSecondMemberInsert` above but aimed at `register_group_commands`
    instead of the membership writers it is called from.
    """

    _WRITE_PREFIXES = (
        "DELETE FROM group_command",
        "UPDATE group_command",
        "INSERT INTO group_command",
    )

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real
        self._write_count = 0

    def execute(self, sql, parameters=()):
        if sql.startswith(self._WRITE_PREFIXES):
            self._write_count += 1
            if self._write_count == 2:
                raise sqlite3.IntegrityError("simulated failure past the first write")
        return self._real.execute(sql, parameters)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_register_group_commands_rolls_back_a_write_time_failure_and_leaves_the_old_rows_intact(
    store, lamps_with_commands, tmp_path, monkeypatch
):
    """Regression for the rollback guard this task adds to
    `register_group_commands` itself - the defect Task 1 left behind in
    `create_group`/`set_group_members`, reproduced here in the sibling
    method the brief warns carries the identical shape.

    Setup: a group of the single colour-capable member already has several
    committed `group_command` rows (including `color`). Widening
    membership to both lamps would shrink the intersection - some rows
    DELETEd, the survivors UPDATEd - but the second write into
    `group_command` is forced to fail. Without `self._db.rollback()` in the
    `except` clause, the first (successful) write would sit in the
    connection's open implicit transaction rather than being undone,
    waiting for a later unrelated `commit()` to flush a half-recomputed,
    inconsistent command list to disk - proven here the same way as the
    membership tests above: an unrelated committing write, then a REOPENED
    `Store` on the same file.
    """
    group = store.create_group("Colour", [lamps_with_commands[0]])
    before = {(c.slug, c.key) for c in store.group_commands(group.id)}
    assert before  # the colour-only member has commands to lose

    monkeypatch.setattr(store, "_db", _FailSecondGroupCommandWrite(store._db))

    with pytest.raises(sqlite3.IntegrityError):
        store.set_group_members(group.id, lamps_with_commands)

    # Read through the still-proxied connection first - a plain SELECT
    # never matches `_WRITE_PREFIXES`, so this is unaffected by the forced
    # failure and confirms the rollback took effect immediately.
    assert {(c.slug, c.key) for c in store.group_commands(group.id)} == before

    # An unrelated write that commits - if the first write of the failed
    # recompute survived uncommitted, this is what would flush the
    # half-recomputed, inconsistent row set to disk.
    store.rename_device(lamps_with_commands[0], "Renamed")
    store.close()

    reopened = Store(tmp_path / "test.sqlite")
    try:
        assert {(c.slug, c.key) for c in reopened.group_commands(group.id)} == before
    finally:
        reopened.close()
