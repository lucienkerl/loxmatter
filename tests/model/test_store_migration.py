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

"""Migration tests for `signal.exported` (Review-Fix Important #1, 2026-09-02).

Deliberately builds a database at the SCHEMA STATE BEFORE `exported`
(commit 00902fc~1 in `src/loxmatter/model/store.py`) directly via `sqlite3`,
instead of creating it through `Store` - `Store` today always already
creates the new column, so that would NOT reproduce the problem of a real
old database that was created with `loxmatter export` or `loxmatter run`
before this review fix.

Deliberately no `try/except` in `Store` itself to somehow make old databases
"compatible" - see `model.store._migrate`: `PRAGMA user_version` cleanly
distinguishes between "version 0, never migrated" and "already at the
latest state", and the migration runs transactionally.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.model.store import DEFAULT_UDP_PORT, Store
from loxmatter.profiles.catalog import element_name
from loxmatter.profiles.table import Exportability, classify, is_exportable

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


_OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS device (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    unique_id  TEXT NOT NULL,
    node_id    INTEGER NOT NULL,
    label      TEXT NOT NULL,
    udp_port   INTEGER NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS signal (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     INTEGER NOT NULL REFERENCES device(id),
    endpoint      INTEGER NOT NULL,
    cluster_id    INTEGER NOT NULL,
    element_id    INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    key           TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    unit          TEXT NOT NULL,
    exportability TEXT NOT NULL,
    UNIQUE (device_id, endpoint, cluster_id, element_id, kind)
);
CREATE TABLE IF NOT EXISTS command (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL REFERENCES device(id),
    node_id     INTEGER NOT NULL,
    endpoint    INTEGER NOT NULL,
    cluster_id  INTEGER NOT NULL,
    command_id  INTEGER NOT NULL,
    key         TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL,
    takes_value INTEGER NOT NULL,
    UNIQUE (device_id, endpoint, cluster_id, command_id)
);
"""


def build_old_database(path: Path) -> None:
    """Creates a database at the schema state before `exported` and fills it
    with one device and one signal per `Exportability` value - directly via
    `sqlite3`, without using `Store` (see the module docstring)."""
    db = sqlite3.connect(str(path))
    try:
        db.executescript(_OLD_SCHEMA)
        db.execute(
            "INSERT INTO device (id, unique_id, node_id, label, udp_port, active)"
            " VALUES (1, 'dev-1', 42, 'Testgeraet', 7000, 1)"
        )
        rows = [
            (1, 1, 0, 6, 0, "attribute", "d1_1_onoff", "Ein/Aus", "", Exportability.DIGITAL.value),
            (
                2,
                1,
                1,
                6,
                1,
                "attribute",
                "d1_1_power",
                "Leistung",
                "kW",
                Exportability.ANALOG.value,
            ),
            (
                3,
                1,
                1,
                40,
                1,
                "attribute",
                "d1_1_vendor",
                "Hersteller",
                "",
                Exportability.TEXT.value,
            ),
            (4, 1, 1, 6, 2, "attribute", "d1_1_liste", "Liste", "", Exportability.NONE.value),
        ]
        db.executemany(
            "INSERT INTO signal"
            " (id, device_id, endpoint, cluster_id, element_id, kind, key, title, unit,"
            " exportability) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        db.commit()
    finally:
        db.close()


_V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS device (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    unique_id  TEXT NOT NULL,
    node_id    INTEGER NOT NULL,
    label      TEXT NOT NULL,
    udp_port   INTEGER NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS signal (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     INTEGER NOT NULL REFERENCES device(id),
    endpoint      INTEGER NOT NULL,
    cluster_id    INTEGER NOT NULL,
    element_id    INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    key           TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    unit          TEXT NOT NULL,
    exportability TEXT NOT NULL,
    exported      INTEGER NOT NULL DEFAULT 1,
    UNIQUE (device_id, endpoint, cluster_id, element_id, kind)
);
CREATE TABLE IF NOT EXISTS command (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL REFERENCES device(id),
    node_id     INTEGER NOT NULL,
    endpoint    INTEGER NOT NULL,
    cluster_id  INTEGER NOT NULL,
    command_id  INTEGER NOT NULL,
    key         TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL,
    takes_value INTEGER NOT NULL,
    UNIQUE (device_id, endpoint, cluster_id, command_id)
);
"""


def build_v1_database(path: Path) -> None:
    """Creates a database at EXACTLY schema version 1 (Review-Fix Minor
    #2, 2026-09-02): `signal.exported` is already there (unlike in
    `build_old_database`, version 0), but `device.exported_at`/`updated_at`
    are not yet (those only come with `_migrate_to_v2`).

    So far this file only tested the jump from version 0 to the current
    version - never opening a database that was actually created between
    two schema changes of this phase (say, by an installation that ran
    `loxmatter export` or `loxmatter run` exactly between task 2 and task 5
    of this phase). That `_migrate` runs step by step over
    `range(version + 1, _SCHEMA_VERSION + 1)` makes this intermediate step
    plausible, but it was not proven until now."""
    db = sqlite3.connect(str(path))
    try:
        db.executescript(_V1_SCHEMA)
        db.execute(
            "INSERT INTO device (id, unique_id, node_id, label, udp_port, active)"
            " VALUES (1, 'dev-1', 42, 'Testgeraet', 7000, 1)"
        )
        db.execute(
            "INSERT INTO signal"
            " (id, device_id, endpoint, cluster_id, element_id, kind, key, title, unit,"
            " exportability, exported) VALUES"
            " (1, 1, 1, 6, 1, 'attribute', 'd1_1_power', 'Leistung', 'kW', ?, 1)",
            (Exportability.ANALOG.value,),
        )
        db.execute("PRAGMA user_version = 1")
        db.commit()
    finally:
        db.close()


def user_version(path: Path) -> int:
    db = sqlite3.connect(str(path))
    try:
        return int(db.execute("PRAGMA user_version").fetchone()[0])
    finally:
        db.close()


def test_opening_a_pre_exported_database_backfills_the_column_correctly(tmp_path):
    """Backfill of `signal.exported` when jumping from schema version 0 to
    the current version - since task 7 (`_migrate_to_v3`) no longer just
    `is_exportable`, but also `is_functional`. None of this fixture's four
    rows pass that: `d1_1_onoff` sits on endpoint 0 (the administrative
    endpoint, see `_migrate_to_v3`) and is not a PowerSource there;
    `d1_1_power` uses cluster 6 (OnOff) with an element id the table does
    not list as "onoff" (only element 0 is named) - both would still have
    been exported under the old, pure `is_exportable` rule (Review-Fix
    Important #1), but no longer are. `d1_1_vendor` (TEXT) and `d1_1_liste`
    (NONE) never were under either rule."""
    path = tmp_path / "old.sqlite"
    build_old_database(path)

    store = Store(path)
    try:
        signals = {s.key: s for s in store.signals(1)}
    finally:
        store.close()

    assert len(signals) == 4
    assert signals["d1_1_onoff"].exported is False
    assert signals["d1_1_power"].exported is False
    # Text and "none" never were -> exported=False, not the column default of 1.
    assert signals["d1_1_vendor"].exported is False
    assert signals["d1_1_liste"].exported is False


def test_migrating_an_old_database_sets_the_schema_version(tmp_path):
    path = tmp_path / "old.sqlite"
    build_old_database(path)
    assert user_version(path) == 0

    store = Store(path)
    store.close()

    assert user_version(path) == 9


def test_reopening_an_already_migrated_store_is_a_noop(tmp_path):
    """No backfill again on the second opening: an `exported` deliberately
    set to True by the user in the meantime (for a signal no backfill rule
    would ever classify as exported, see
    `test_opening_a_pre_exported_database_backfills_the_column_correctly`)
    must not be silently reset on the next startup."""
    path = tmp_path / "old.sqlite"
    build_old_database(path)

    first = Store(path)
    first.set_exported("d1_1_power", True)
    first.close()
    assert user_version(path) == 9

    second = Store(path)
    try:
        power = next(s for s in second.signals(1) if s.key == "d1_1_power")
    finally:
        second.close()

    assert power.exported is True
    assert user_version(path) == 9


def test_a_fresh_database_is_already_at_the_latest_version(tmp_path):
    path = tmp_path / "fresh.sqlite"
    store = Store(path)
    store.close()
    assert user_version(path) == 9


def test_migration_failure_leaves_the_database_unchanged(tmp_path, monkeypatch):
    """If a migration fails, neither the new column nor the version number
    may be left behind - see `model.store._migrate`."""
    path = tmp_path / "old.sqlite"
    build_old_database(path)

    def boom(db: sqlite3.Connection) -> None:
        db.execute("ALTER TABLE signal ADD COLUMN exported INTEGER NOT NULL DEFAULT 1")
        raise RuntimeError("simulated crash in the middle of the migration")

    monkeypatch.setattr("loxmatter.model.store._MIGRATIONS", {1: boom})

    with pytest.raises(RuntimeError):
        Store(path)

    assert user_version(path) == 0
    db = sqlite3.connect(str(path))
    try:
        columns = {row[1] for row in db.execute("PRAGMA table_info(signal)")}
    finally:
        db.close()
    assert "exported" not in columns


def test_migrating_an_old_database_adds_exported_at_and_updated_at_as_null(tmp_path):
    """Migration v2 (task 5, phase 5) - see `model.store._migrate_to_v2`.
    An old database has no export timestamp; `None` instead of a guessed
    value is the only honest answer here."""
    path = tmp_path / "old.sqlite"
    build_old_database(path)

    store = Store(path)
    try:
        (device,) = store.devices()
    finally:
        store.close()

    assert device.exported_at is None
    assert device.updated_at is None
    assert user_version(path) == 9


def test_opening_a_v1_database_only_runs_the_v2_migration(tmp_path):
    """The intermediate step not covered until now: a database that sits
    exactly at version 1 (`signal.exported` present, `device.exported_at`/
    `updated_at` not yet) opens without error and ends up at the current
    version - `_migrate_to_v1` must NOT run again in the process (otherwise
    `ALTER TABLE signal ADD COLUMN exported` would fail with "duplicate
    column", because the column is already there); `_migrate_to_v2`,
    `_migrate_to_v3` and `_migrate_to_v4` all three run, in that order.

    `signal.key` stays untouched in the process (main document 6.2) -
    `title` and `exported`, however, no longer do: cluster 6 (OnOff) is
    known to the table, but only element 0 is named "onoff" there - element
    1 (this fixture) has failed `is_functional` since task 7
    (`_migrate_to_v3`), independent of the `exported=1` stored here."""
    path = tmp_path / "v1.sqlite"
    build_v1_database(path)
    assert user_version(path) == 1

    store = Store(path)
    try:
        (device,) = store.devices()
        (signal,) = store.signals(1)
    finally:
        store.close()

    assert user_version(path) == 9
    assert device.exported_at is None
    assert device.updated_at is None
    assert signal.key == "d1_1_power"
    assert signal.exported is False


def test_reopening_an_already_v2_database_is_a_noop(tmp_path):
    """Complements `test_opening_a_v1_database_only_runs_the_v2_migration`:
    a second opening after the migration must not trigger any further write
    and must leave the values unchanged."""
    path = tmp_path / "v1.sqlite"
    build_v1_database(path)

    first = Store(path)
    first.close()
    assert user_version(path) == 9

    second = Store(path)
    try:
        (device,) = second.devices()
    finally:
        second.close()

    assert user_version(path) == 9
    assert device.exported_at is None
    assert device.updated_at is None


# Schema state 2 (task 5, phase 5): all of today's columns are already
# there - unlike `_OLD_SCHEMA`/`_V1_SCHEMA` above, this is not about a
# missing column, but about outdated VALUES in exactly these columns
# (`title`, `unit`, `exported`), which task 7 corrects retroactively.
_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS device (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    unique_id   TEXT NOT NULL,
    node_id     INTEGER NOT NULL,
    label       TEXT NOT NULL,
    udp_port    INTEGER NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    exported_at TEXT,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS signal (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id     INTEGER NOT NULL REFERENCES device(id),
    endpoint      INTEGER NOT NULL,
    cluster_id    INTEGER NOT NULL,
    element_id    INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    key           TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    unit          TEXT NOT NULL,
    exportability TEXT NOT NULL,
    exported      INTEGER NOT NULL DEFAULT 1,
    UNIQUE (device_id, endpoint, cluster_id, element_id, kind)
);
CREATE TABLE IF NOT EXISTS command (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL REFERENCES device(id),
    node_id     INTEGER NOT NULL,
    endpoint    INTEGER NOT NULL,
    cluster_id  INTEGER NOT NULL,
    command_id  INTEGER NOT NULL,
    key         TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL,
    takes_value INTEGER NOT NULL,
    UNIQUE (device_id, endpoint, cluster_id, command_id)
);
"""


def _pretend_unnamed_profile(ref: SignalRef, value: object) -> tuple[str, str, str, Exportability]:
    """Pretends that `(ref.cluster_id, ref.element_id)` does not appear in
    ANY version of `clusters.yaml` - regardless of whether the table
    actually names it TODAY. Builds slug/title/unit/exportability exactly
    like the "no entry" branch of `profiles.table.lookup`, just without ever
    entering the "entry present" branch.

    This simulates a device that was commissioned long before any
    clusters.yaml correction - exactly the case task 7 retroactively fixes:
    the battery level (cluster 47, element 12) would thus get the key
    `d1_0_c47_a12`, even though `clusters.yaml` now calls the element
    "battery". `slug` depends only on `cluster_id`/`element_id`, never on
    `title`/`unit`/`exportability` - two different refs can therefore never
    get the same key here, `_assign_key`'s element-id suffix on a collision
    (see `model.store`) is not needed for this purely generic case.
    """
    if ref.kind is SignalKind.EVENT:
        slug = f"c{ref.cluster_id}_e{ref.element_id}"
        return slug, element_name(ref) or slug, "", Exportability.DIGITAL
    slug = f"c{ref.cluster_id}_a{ref.element_id}"
    return slug, element_name(ref) or slug, "", classify(value)


def _build_store_at_schema_v2(
    path: Path,
    *,
    device_id: int = 1,
    image: str = "ikea_bilresa_button.json",
    extra_row: tuple[str, int, int, int, str] | None = None,
) -> set[str]:
    """Builds (or extends) a database at schema state 2 - the state BEFORE
    task 7, but already AFTER task 5 (`signal.exported`,
    `device.exported_at`/`updated_at` are present) - directly via `sqlite3`,
    like `_OLD_SCHEMA`/`_V1_SCHEMA` above, not through `Store` (see the
    module docstring: `Store` today already creates everything fresh and
    would not reproduce the actual problem).

    Writes key and title via `_pretend_unnamed_profile`, NOT directly via
    `profiles.table.lookup` - `lookup` knows TODAY's `clusters.yaml` and
    would immediately assign the new key `d1_0_battery` for the battery
    level, which would mean the test case (a key from before this naming)
    would never even arise. `exported` is set to the pre-task-6 rule: 1 for
    everything technically exportable (`profiles.table.is_exportable`),
    regardless of whether anyone WANTS it by default
    (`profiles.relevance.is_functional`) - exactly the "signal flood" that
    task 6 fixed for newly commissioned devices and task 7 catches up on
    for existing devices.

    `device_id` allows multiple devices in the same database (call multiple
    times with different `device_id`/`image` values against the same
    `path`) - for the cross-check from step 5 against both checked-in
    images.

    `extra_row` appends an additional row (key, endpoint, cluster id,
    element id, kind) for a cluster the profile table does not know - for
    `test_a_row_with_an_unknown_cluster_still_gets_rewritten`.

    Returns the set of keys assigned in THIS call (including `extra_row`,
    if set).
    """
    snap = load(image)
    db = sqlite3.connect(str(path))
    try:
        db.executescript(_V2_SCHEMA)
        db.execute(
            "INSERT INTO device (id, unique_id, node_id, label, udp_port, active)"
            " VALUES (?, ?, ?, ?, ?, 1)",
            (
                device_id,
                f"dev-{device_id}",
                snap.node_id,
                f"{snap.vendor_name} {snap.product_name}".strip(),
                DEFAULT_UDP_PORT,
            ),
        )
        keys: set[str] = set()
        rows = []
        for ref in extract_signals(snap):
            slug, title, unit, exportability = _pretend_unnamed_profile(
                ref, snap.attributes.get(ref.path)
            )
            key = f"d{device_id}_{ref.endpoint}_{slug}"
            keys.add(key)
            rows.append(
                (
                    key,
                    device_id,
                    ref.endpoint,
                    ref.cluster_id,
                    ref.element_id,
                    ref.kind.value,
                    title,
                    unit,
                    exportability.value,
                    int(is_exportable(exportability)),
                )
            )
        if extra_row is not None:
            key, endpoint, cluster_id, element_id, kind = extra_row
            rows.append(
                (
                    key,
                    device_id,
                    endpoint,
                    cluster_id,
                    element_id,
                    kind,
                    "kaputt",
                    "",
                    Exportability.ANALOG.value,
                    1,
                )
            )
            keys.add(key)
        db.executemany(
            "INSERT INTO signal"
            " (key, device_id, endpoint, cluster_id, element_id, kind, title, unit,"
            " exportability, exported) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        db.execute("PRAGMA user_version = 2")
        db.commit()
    finally:
        db.close()
    return keys


def test_the_migration_never_changes_a_key(tmp_path):
    """The iron rule (main document 6.2). A renamed key would be a
    silently dead functional component in a foreign config - not an error
    anyone would see from outside."""
    path = tmp_path / "s.sqlite"
    keys_before = _build_store_at_schema_v2(path)
    store = Store(path)  # opens and migrates
    assert {s.key for s in store.signals(1)} == keys_before


def test_the_migration_refreshes_title_and_unit_from_the_table(tmp_path):
    """Without this step, a correction in clusters.yaml never reached a
    signal that was already stored."""
    path = tmp_path / "s.sqlite"
    _build_store_at_schema_v2(path)
    store = Store(path)
    battery = next(s for s in store.signals(1) if s.ref.cluster_id == 47 and s.ref.element_id == 12)
    assert battery.key == "d1_0_c47_a12", "key stays the old one"
    assert battery.title == "battery"
    assert battery.unit == "%"


def test_the_migration_applies_the_new_default_to_existing_devices(tmp_path):
    path = tmp_path / "s.sqlite"
    _build_store_at_schema_v2(path)
    store = Store(path)
    exported = {s.key for s in store.signals(1) if s.exported}
    assert len(exported) < 30, "the signal flood must also be gone retroactively"


def test_a_row_with_an_unknown_cluster_still_gets_rewritten(tmp_path):
    """Renamed (follow-up fix 3, task 7) - the original name claimed this
    test proved the `except` in `_migrate_to_v3`'s row loop. That is not
    true: `lookup()` catches an unknown cluster itself (generic slug/title
    branch) and never raises - the row does survive, but gets rewritten in
    the process (`title='kaputt'` becomes `title='c4711_a0'`, the generic
    title). So this test only checks that an unknown cluster triggers NO
    error, not the failure property itself - that lives in
    `test_an_unparseable_row_survives_the_migration_untouched` below."""
    path = tmp_path / "s.sqlite"
    _build_store_at_schema_v2(path, extra_row=("d1_9_kaputt", 9, 4711, 0, "attribute"))
    store = Store(path)
    try:
        (row,) = [s for s in store.signals(1) if s.key == "d1_9_kaputt"]
    finally:
        store.close()
    assert row.title == "c4711_a0"


def test_an_unparseable_row_survives_the_migration_untouched(tmp_path):
    """The load-bearing failure property (draft 8, follow-up fix 3, task 7)
    directly at the `except (ValueError, KeyError)` in `_migrate_to_v3`'s
    row loop, not just at its external effect.

    `Store._as_signal` parses `kind` via `SignalKind(row["kind"])` just as
    unprotected as `_migrate_to_v3` itself - `store.signals(1)` therefore
    also fails on this row (proven: an earlier version of this test called
    exactly that and got the same `ValueError` that `_migrate_to_v3` itself
    catches). Directly via `sqlite3` (like `build_old_database`/
    `build_v1_database` above), on the other hand, the row is easy to
    create without ever going through `Store`: `SignalKind('kaputt')` inside
    `_migrate_to_v3` raises `ValueError` BEFORE `lookup()` is even called,
    lands in the `except`, and stays word for word unchanged - including
    the `title`, which would otherwise always be re-derived. The flip side
    applies equally when READING after the migration: the healthy battery
    row is fetched specifically via `signal_by_key` (touches only this one
    row), the broken one afterward raw via `sqlite3` - `store.signals(1)`
    would run BOTH rows through `_as_signal` and would immediately fail on
    the broken one still being in the table. The healthy row in the same
    call shows at the same time: the broken row does not drag down the
    transaction, the healthy one gets migrated."""
    path = tmp_path / "s.sqlite"
    db = sqlite3.connect(str(path))
    try:
        db.executescript(_V2_SCHEMA)
        db.execute(
            "INSERT INTO device (id, unique_id, node_id, label, udp_port, active)"
            " VALUES (1, 'dev-1', 42, 'Testgeraet', 7000, 1)"
        )
        # exportability=analog as with a real registration: the battery
        # level already reported a number back then (see `clusters.yaml`,
        # cluster 47), `exported=1` as with the pre-task-6 rule (only
        # `is_exportable`, without `is_functional`, see
        # `_build_store_at_schema_v2` above).
        db.execute(
            "INSERT INTO signal"
            " (device_id, endpoint, cluster_id, element_id, kind, key, title, unit,"
            " exportability, exported) VALUES"
            " (1, 0, 47, 12, 'attribute', 'd1_0_c47_a12', 'Batterie-Alt', '', ?, 1)",
            (Exportability.ANALOG.value,),
        )
        db.execute(
            "INSERT INTO signal"
            " (device_id, endpoint, cluster_id, element_id, kind, key, title, unit,"
            " exportability, exported) VALUES"
            " (1, 9, 4711, 0, 'kaputt', 'd1_9_kaputt', 'kaputt', '', ?, 1)",
            (Exportability.ANALOG.value,),
        )
        db.execute("PRAGMA user_version = 2")
        db.commit()
    finally:
        db.close()

    # `store.signals(1)` runs BOTH rows through `_as_signal`, which parses
    # `kind` just as unprotected as `_migrate_to_v3` - with the broken row
    # still in the table, this would fail here too (Store._as_signal).
    # Hence: the healthy row specifically via `signal_by_key` (touches only
    # this one row), the broken one afterward raw via `sqlite3` - exactly
    # the way that actually makes a `kind` `Store` itself never reads back
    # in still checkable.
    store = Store(path)
    try:
        battery = store.signal_by_key("d1_0_c47_a12")
    finally:
        store.close()
    assert battery is not None

    # The healthy row was re-derived ...
    assert battery.title == "battery"
    assert battery.unit == "%"
    assert battery.exportability is Exportability.ANALOG
    assert battery.exported is True

    # ... the broken one stayed word for word unchanged, no abort -
    # verified raw via `sqlite3`, because `Store` itself would fail on its
    # `kind` (see above).
    db = sqlite3.connect(str(path))
    try:
        db.row_factory = sqlite3.Row
        broken = db.execute(
            "SELECT title, unit, exportability, exported, kind FROM signal WHERE key = ?",
            ("d1_9_kaputt",),
        ).fetchone()
    finally:
        db.close()
    assert broken is not None
    assert broken["kind"] == "kaputt"
    assert broken["title"] == "kaputt"
    assert broken["unit"] == ""
    assert broken["exportability"] == Exportability.ANALOG.value
    assert broken["exported"] == 1


def test_a_never_measured_energy_counter_is_still_promoted_by_the_field_number_exception(
    tmp_path,
):
    """Records the boundary deliberately accepted in fix 1 (follow-up
    task 7, see the `_migrate_to_v3` docstring, section "Two open
    boundaries of this exception"): the field-number exception does not see
    the runtime value.

    This row is set up here the way a real registration BEFORE the
    introduction of the `field:0` entry for cluster 145 would have created
    it, AND the way a registration would create it if the underlying
    counter had never been measured (Matter reports `null` for that,
    `classify(None)` yields `none` - see `_pretend_unnamed_profile` and
    `profiles.table.classify`): `exportability=none`, `exported=0`.

    The migration nevertheless promotes it to ANALOG and exports it -
    regardless of whether the counter has meanwhile actually gotten a
    value, because `_migrate_to_v3` always calls `lookup(ref, None)` with
    `value=None`. At runtime this stays consequence-free (`to_loxone_value`
    also returns `None` for a value that is still `null`), but the
    generated Loxone template gets an input that never carries a value -
    see the docstring."""
    path = tmp_path / "s.sqlite"
    db = sqlite3.connect(str(path))
    try:
        db.executescript(_V2_SCHEMA)
        db.execute(
            "INSERT INTO device (id, unique_id, node_id, label, udp_port, active)"
            " VALUES (1, 'dev-1', 42, 'Testgeraet', 7000, 1)"
        )
        db.execute(
            "INSERT INTO signal"
            " (device_id, endpoint, cluster_id, element_id, kind, key, title, unit,"
            " exportability, exported) VALUES"
            " (1, 2, 145, 2, 'attribute', 'd1_2_c145_a2', 'c145_a2', '', ?, 0)",
            (Exportability.NONE.value,),
        )
        db.execute("PRAGMA user_version = 2")
        db.commit()
    finally:
        db.close()

    store = Store(path)
    try:
        (signal,) = store.signals(1)
    finally:
        store.close()

    assert signal.key == "d1_2_c145_a2", "key stays the old one"
    assert signal.exportability is Exportability.ANALOG
    assert signal.exported is True


def test_the_migration_reproduces_the_functional_export_counts_of_both_fixtures(tmp_path):
    """The cross-check from the draft (step 5): a database built from both
    checked-in images, set up under the old schema and migrated, must
    produce the same number of exported signals as a fresh registration
    (`tests/model/test_store.py`:
    `test_a_freshly_registered_plug_exports_only_its_meaningful_values` = 5,
    `test_a_freshly_registered_button_keeps_both_rockers_and_the_battery` =
    17) - proof that the fallback rule for the administrative endpoint in
    `_migrate_to_v3` (see there) loses nothing on the two only real devices
    of this project. The actual output value of this test is recorded in
    the report for task 7."""
    path = tmp_path / "both.sqlite"
    _build_store_at_schema_v2(path, device_id=1, image="ikea_grillplats_plug.json")
    _build_store_at_schema_v2(path, device_id=2, image="ikea_bilresa_button.json")

    store = Store(path)  # opens and migrates
    try:
        counts = {
            d.label: len([s for s in store.signals(d.id) if s.exported]) for d in store.devices()
        }
    finally:
        store.close()

    assert sorted(counts.values()) == [5, 17]


def test_the_v4_migration_backfills_functional_independently_of_exported(tmp_path):
    """Extends the cross-check above with the new column (`_migrate_to_v4`,
    task 8): `functional` is derived via the same fallback rule as
    `exported` in `_migrate_to_v3` (`_endpoint0_device_types`), but ends up
    in its OWN column - for both checked-in images, every exported signal
    here is also functional and vice versa, so the same two numbers as
    above must come out, even though `_migrate_to_v4` recomputes them
    independently of `exported`."""
    path = tmp_path / "both-functional.sqlite"
    _build_store_at_schema_v2(path, device_id=1, image="ikea_grillplats_plug.json")
    _build_store_at_schema_v2(path, device_id=2, image="ikea_bilresa_button.json")

    store = Store(path)  # opens and migrates (v2 -> v3 -> v4)
    try:
        counts = {
            d.label: len([s for s in store.signals(d.id) if s.functional]) for d in store.devices()
        }
    finally:
        store.close()

    assert sorted(counts.values()) == [5, 17]


def test_migration_to_v5_adds_the_auth_tables_without_touching_devices(tmp_path):
    """An existing database at version 4 gets `setting` and `session`, and
    its device rows stay untouched.

    Version 4 and not 3 as the starting point: the login move got 5 when
    merging, because phase 6 had already claimed 4 (see the comment on
    `_SCHEMA_VERSION`). A database that stays stuck there is exactly the
    case this migration has to handle."""
    path = tmp_path / "alt.sqlite"
    store = Store(path)
    snapshot = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    signals_before = len(store.signals(device_id))
    store.close()

    # Reset to version 4 and drop both tables - this is what a database
    # looks like that has seen phase 6 but not the login yet.
    db = sqlite3.connect(str(path))
    db.executescript("DROP TABLE session; DROP TABLE setting; PRAGMA user_version = 4;")
    db.commit()
    db.close()

    store = Store(path)
    try:
        assert user_version(path) == 9
        assert store.auth.password_hash() is None
        store.auth.create_session("a", created_at=1, expires_at=2)
        assert store.auth.session_expires_at("a") == 2
        assert len(store.signals(device_id)) == signals_before
    finally:
        store.close()


def test_migration_to_v6_adds_the_resend_column_defaulting_to_off(tmp_path):
    """An existing database at version 5 (before `signal.resend`) gets the
    column via migration, every row starts at `resend = 0` - no backfill,
    see the `_migrate_to_v6` docstring."""
    path = tmp_path / "alt.sqlite"
    store = Store(path)
    snapshot = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    key = store.signals(device_id)[0].key
    store.close()

    db = sqlite3.connect(str(path))
    db.executescript("ALTER TABLE signal DROP COLUMN resend; PRAGMA user_version = 5;")
    db.commit()
    db.close()

    store = Store(path)
    try:
        assert user_version(path) == 9
        assert store.signal_by_key(key).resend is False
    finally:
        store.close()


def test_migration_to_v7_adds_room_and_device_types_as_null(tmp_path):
    """An existing database at version 6 gets both columns via migration.
    No backfill: `room = NULL` means "no room", exactly as for a freshly
    commissioned device without a room chosen, and `device_types = NULL`
    means "not backfilled yet" - `backfill_device_types` is responsible for
    that at bridge startup, not the migration (see draft 3.4)."""
    path = tmp_path / "alt.sqlite"
    store = Store(path)
    snapshot = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.close()

    db = sqlite3.connect(str(path))
    db.executescript(
        "ALTER TABLE device DROP COLUMN room;"
        " ALTER TABLE device DROP COLUMN device_types;"
        " PRAGMA user_version = 6;"
    )
    db.commit()
    db.close()

    store = Store(path)
    try:
        assert user_version(path) == 9
        device = store.device(device_id)
        assert device.room is None
        assert device.device_types is None
    finally:
        store.close()


def test_a_fresh_database_survives_the_v7_migration_without_duplicate_column(tmp_path):
    """A freshly created database already has both columns via `_SCHEMA`.
    `_add_column_if_missing` must recognize that - otherwise the very first
    startup would fail with "duplicate column name", the same trap
    `_migrate_to_v1` already guards against."""
    path = tmp_path / "neu.sqlite"
    store = Store(path)
    store.close()

    db = sqlite3.connect(str(path))
    db.execute("PRAGMA user_version = 6")
    db.commit()
    db.close()

    store = Store(path)
    try:
        assert user_version(path) == 9
    finally:
        store.close()


def test_a_v7_database_gains_the_group_tables(tmp_path):
    """`_migrate_to_v8`. The reverse direction matters too: a fresh
    database also runs the whole chain (the `_migrate_to_v5` pitfall)."""
    import sqlite3

    path = tmp_path / "legacy.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE device (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " unique_id TEXT NOT NULL, node_id INTEGER NOT NULL, label TEXT NOT NULL,"
        " udp_port INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,"
        " exported_at TEXT, updated_at TEXT, room TEXT, device_types TEXT);"
    )
    db.execute("PRAGMA user_version = 7")
    db.commit()
    db.close()

    store = Store(path)
    tables = {
        row[0] for row in store._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"device_group", "device_group_member", "group_command"} <= tables
    store.close()


def test_a_fresh_database_ends_at_version_nine(tmp_path):
    store = Store(tmp_path / "fresh.sqlite")
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 9
    store.close()
