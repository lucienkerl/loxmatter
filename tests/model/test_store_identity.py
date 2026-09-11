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

"""Device identity as technology plus address (design 2026-09-11,
section 4)."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot, parse_technology
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"

# The `device` and `command` tables exactly as schema version 8 created
# them - copied from `_SCHEMA` at commit 13541c4, not derived from today's.
_V8_DEVICE_AND_COMMAND = """
CREATE TABLE device (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    unique_id    TEXT NOT NULL,
    node_id      INTEGER NOT NULL,
    label        TEXT NOT NULL,
    udp_port     INTEGER NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1,
    exported_at  TEXT,
    updated_at   TEXT,
    room         TEXT,
    device_types TEXT
);
CREATE TABLE signal (
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
    functional    INTEGER NOT NULL DEFAULT 1,
    resend        INTEGER NOT NULL DEFAULT 0,
    UNIQUE (device_id, endpoint, cluster_id, element_id, kind)
);
CREATE TABLE command (
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


def _fixture(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def _columns(path: Path, table: str) -> set[str]:
    db = sqlite3.connect(path)
    try:
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    finally:
        db.close()


def _user_version(path: Path) -> int:
    db = sqlite3.connect(path)
    try:
        return int(db.execute("PRAGMA user_version").fetchone()[0])
    finally:
        db.close()


def _build_v8_database(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(_V8_DEVICE_AND_COMMAND)
    db.execute(
        "INSERT INTO device (id, unique_id, node_id, label, udp_port)"
        " VALUES (1, 'CAA68CE5422C3C29', 23, 'Lamp', 7000)"
    )
    db.execute(
        "INSERT INTO signal (device_id, endpoint, cluster_id, element_id, kind, key,"
        " title, unit, exportability) VALUES (1, 1, 6, 0, 'attribute', 'd1_1_onoff',"
        " 'On', '', 'digital')"
    )
    db.execute(
        "INSERT INTO command (device_id, node_id, endpoint, cluster_id, command_id, key,"
        " slug, takes_value) VALUES (1, 23, 1, 6, 1, 'd1_1_on', 'on', 0)"
    )
    db.execute("PRAGMA user_version = 8")
    db.commit()
    db.close()


def test_a_v8_database_gains_addresses_and_keeps_node_ids(tmp_path, monkeypatch):
    """Protects: the `address` backfill and that no Loxone key changes.
    `node_id` stays on both tables (design 2026-09-11, section 4.1,
    rollback compatibility - see `_migrate_to_v9`), so this no longer
    checks that it is gone; that guarantee moved to
    `test_version_8_code_still_works_on_a_version_9_database`.

    `_repair_rows_written_by_older_versions` runs right after `_migrate` on
    every `Store.__init__` (design 2026-09-11, section 4.1) and happens to
    perform the exact same backfill this test wants to pin on
    `_migrate_to_v9` alone
    (`address = CAST(node_id AS TEXT) WHERE address = ''`) - left wired up,
    it would silently paper over a broken migration and this test would
    stay green regardless. It is monkeypatched to a no-op here so that only
    `_migrate_to_v9`'s own line is exercised; its own behaviour has its own
    test, `test_a_device_added_by_version_8_code_is_addressable_after_rolling_forward`.
    Fault to prove it: comment out the `UPDATE device SET address = ...`
    line in `_migrate_to_v9`."""
    from loxmatter.model import store as store_module

    monkeypatch.setattr(store_module, "_repair_rows_written_by_older_versions", lambda db: None)
    path = tmp_path / "v8.sqlite"
    _build_v8_database(path)

    store = Store(path)
    try:
        assert _user_version(path) == 9
        device = store.device(1)
        assert (device.technology, device.address) == ("matter", "23")
        assert store.device_id_for("matter", "23") == 1
        assert store.signal_by_key("d1_1_onoff") is not None
        command = store.resolve_command("d1_1_on")
        assert (command.technology, command.address) == ("matter", "23")
    finally:
        store.close()
    assert "node_id" in _columns(path, "device")
    assert "node_id" in _columns(path, "command")


def test_a_fresh_database_runs_migration_9_without_duplicate_column(tmp_path):
    """A fresh database already has the v9 columns from `_SCHEMA` and still
    runs the chain from `user_version = 0`. Fault to prove it: replace the
    `_add_column_if_missing(db, "device", "technology", ...)` call in
    `_migrate_to_v9` with a bare `db.execute("ALTER TABLE device ADD COLUMN
    technology TEXT NOT NULL DEFAULT 'matter'")`."""
    path = tmp_path / "fresh.sqlite"
    Store(path).close()
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version = 8")
    db.commit()
    db.close()

    Store(path).close()

    assert _user_version(path) == 9


def test_a_failing_migration_9_leaves_version_8_intact(tmp_path, monkeypatch):
    """Protects: migration 9 runs inside `_migrate`'s transaction. The
    stand-in fails AFTER the real migration has written, not before - a
    failure before any write would pass without a transaction too. Fault
    to prove it: add `db.commit()` as the first line after the
    `_add_column_if_missing(db, "device", "technology", ...)` call in
    `_migrate_to_v9`."""
    from loxmatter.model import store as store_module

    path = tmp_path / "v8.sqlite"
    _build_v8_database(path)

    def migrate_then_crash(db: sqlite3.Connection) -> None:
        store_module._migrate_to_v9(db)
        raise RuntimeError("simulated crash after migration 9 wrote")

    migrations = dict(store_module._MIGRATIONS)
    migrations[9] = migrate_then_crash
    monkeypatch.setattr(store_module, "_MIGRATIONS", migrations)

    with pytest.raises(RuntimeError):
        Store(path)

    assert _user_version(path) == 8
    assert "node_id" in _columns(path, "device")
    assert "technology" not in _columns(path, "device")


def test_version_8_code_still_works_on_a_version_9_database(tmp_path):
    """Protects the rollback promise `deploy/updater/update-once.sh` relies
    on (design 2026-09-11, section 4.1): `update-once.sh` rolls a failed update back to the
    OLD image WITHOUT restoring the database, on the invariant that every
    migration only ADDS columns - so version-8 code, unmodified, must still
    read and write a version-9 database exactly the way it always has.

    Runs the LITERAL version-8 SQL statements (not `Store` methods) against
    a database this version's `Store` created and populated - the only way
    to actually prove old code still works, rather than merely that new
    code still emits the old column.

    Fault to prove it: make `Store._legacy_node_id_for` return `0` for
    Matter too, instead of `int(address)` - the node-14 lookup below then
    finds nothing (it looks for `node_id = 14`, but every row would carry
    `node_id = 0`).

    **The two assertions after the reopen below prove two different
    things.** `resolve_command` reads a command's identity through the
    `command JOIN device` in `_COMMAND_SELECT` (see `_as_command`), not
    from the command row's own `node_id` - so the version-8-style command
    row inserted above (`node_id = 14`, owned by `device_id`, the lamp
    already registered by this version) must resolve to the OWNING
    DEVICE's `(technology, address)`, `("matter", "14")`, regardless of
    the raw `node_id` column. The separate `device_id_for("matter", "99")`
    check proves the OTHER version-8 artefact above - the raw device row
    inserted with no `technology`/`address` of its own, sitting at the
    column defaults `technology = 'matter'`, `address = ''` - is healed by
    `_repair_rows_written_by_older_versions` on THIS reopen, exactly the
    fault `test_a_device_added_by_version_8_code_is_addressable_after_rolling_forward`
    already proves by removing that call; no separate fault is needed
    here for that half."""
    path = tmp_path / "s.sqlite"
    store = Store(path)
    snapshot = _fixture("ikea_kajplats_ws_lamp.json")
    device_id = store.register_device(snapshot)
    store.register_commands(device_id, extract_commands(snapshot))
    store.close()

    db = sqlite3.connect(path)
    try:
        row = db.execute("SELECT id FROM device WHERE node_id = ? AND active = 1", (14,)).fetchone()
        assert row is not None
        assert row[0] == device_id

        node_ids = {
            r[0]
            for r in db.execute("SELECT node_id FROM command WHERE device_id = ?", (device_id,))
        }
        assert node_ids == {14}

        db.execute(
            "INSERT INTO device (unique_id, node_id, label, udp_port, updated_at, room,"
            " device_types) VALUES ('old', 99, 'Old', 7000, NULL, NULL, NULL)"
        )
        # command_id 99: the lamp's own commands already occupy 0/1/2 on
        # cluster 6 (off/on/toggle, see `extract_commands`) - an unused id
        # keeps this a genuinely new row rather than colliding with the
        # UNIQUE (device_id, endpoint, cluster_id, command_id) constraint.
        db.execute(
            "INSERT INTO command (device_id, node_id, endpoint, cluster_id, command_id, key,"
            " slug, takes_value) VALUES (?, 14, 1, 6, 99, ?, 'legacy', 0)",
            (device_id, f"d{device_id}_1_legacy"),
        )
        db.commit()
    finally:
        db.close()

    reopened = Store(path)
    try:
        legacy_command = reopened.resolve_command(f"d{device_id}_1_legacy")
        assert (legacy_command.technology, legacy_command.address) == ("matter", "14")

        old_device_id = reopened.device_id_for("matter", "99")
        assert old_device_id is not None
        assert reopened.device(old_device_id).unique_id == "old"
    finally:
        reopened.close()


def test_a_device_added_by_version_8_code_is_addressable_after_rolling_forward(tmp_path):
    """The other half of the rollback story (design 2026-09-11, section 4.1): a device
    commissioned by version-8 code WHILE rolled back (no `technology`/
    `address` in its INSERT, so both sit at their column defaults - `matter`
    and `''`) must become addressable again as soon as the bridge is rolled
    FORWARD to this version - `_repair_rows_written_by_older_versions` runs
    on every `Store.__init__` for exactly this. Fault to prove it: remove
    the `_repair_rows_written_by_older_versions(self._db)` call from
    `Store.__init__`."""
    path = tmp_path / "s.sqlite"
    Store(path).close()

    db = sqlite3.connect(path)
    try:
        db.execute(
            "INSERT INTO device (unique_id, node_id, label, udp_port, updated_at, room,"
            " device_types) VALUES ('legacy-device', 99, 'Legacy', 7000, NULL, NULL, NULL)"
        )
        db.commit()
    finally:
        db.close()

    store = Store(path)
    try:
        device_id = store.device_id_for("matter", "99")
        assert device_id is not None
        assert store.device(device_id).address == "99"
    finally:
        store.close()


def test_opening_a_read_only_up_to_date_database_does_not_write(tmp_path):
    """Protects: `_repair_rows_written_by_older_versions` must not write on
    every `Store.__init__` - only when a row actually needs healing (final
    fix pass). Before the probe was added, this ran an unconditional
    `UPDATE ...; commit()` on every open: a fully migrated version-9
    database opened from a read-only file then failed with "attempt to
    write a readonly database", even though there was nothing to repair,
    and every CLI invocation took a write lock for a no-op.

    Fault to prove it: replace the `SELECT 1 ... LIMIT 1` probe in
    `_repair_rows_written_by_older_versions` with an unconditional
    `UPDATE`/`commit()` (i.e. remove the `if needs_repair is None: return`
    guard) - this test then fails with `sqlite3.OperationalError: attempt
    to write a readonly database`.

    Skipped when running as root: root ignores file permission bits, so
    the read-only file would still be writable and the test could not
    prove anything."""
    if os.geteuid() == 0:
        pytest.skip("running as root ignores file permissions - cannot test read-only opening")
    path = tmp_path / "s.sqlite"
    store = Store(path)
    device_id = store.register_device(_fixture("ikea_kajplats_ws_lamp.json"))
    store.close()

    os.chmod(path, 0o444)
    os.chmod(tmp_path, 0o555)
    try:
        store = Store(path)
        try:
            devices = store.devices()
            assert [d.id for d in devices] == [device_id]
        finally:
            store.close()
    finally:
        os.chmod(tmp_path, 0o755)
        os.chmod(path, 0o644)


def test_a_device_without_a_unique_id_falls_back_to_its_address(tmp_path):
    """Pins `_device_identity`'s fallback for a device that reports no
    UniqueID at all (Spec 7.2) - `ikea_bilresa_button.json` is exactly such
    a fixture. Fault to prove it: change the `f"node:{...}"` fallback
    string in `_device_identity`, e.g. to drop the `node:` prefix."""
    store = Store(tmp_path / "s.sqlite")
    try:
        device_id = store.register_device(_fixture("ikea_bilresa_button.json"))
        assert store.device(device_id).unique_id == "node:4"
    finally:
        store.close()


def test_device_id_for_tells_technologies_apart(tmp_path):
    """Fault to prove it: drop `technology = ? AND` from the query in
    `device_id_for` (and its parameter)."""
    store = Store(tmp_path / "s.sqlite")
    try:
        snapshot = _fixture("ikea_kajplats_ws_lamp.json")
        device_id = store.register_device(snapshot)
        assert store.device_id_for("matter", "14") == device_id
        assert store.device_id_for("zigbee", "14") is None
    finally:
        store.close()


def test_register_device_stores_the_raw_network_features(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    try:
        thread = store.register_device(_fixture("ikea_kajplats_ws_lamp.json"))
        unknown = store.register_device(_fixture("example_light.json"))
        assert store.device(thread).network_features == 2
        assert store.device(unknown).network_features is None
    finally:
        store.close()


def test_backfill_network_features_fills_only_null_and_only_reachable_devices(tmp_path):
    """Fault to prove it: remove `network_features IS NULL AND` from the
    SELECT in `backfill_network_features` - the device with the manually
    set 7 is then overwritten with 2."""
    store = Store(tmp_path / "s.sqlite")
    try:
        preset = _fixture("ikea_kajplats_ws_lamp.json")
        missing = _fixture("ikea_kajplats_cws_lamp.json")
        offline = _fixture("ikea_grillplats_plug.json")
        preset_id = store.register_device(preset)
        missing_id = store.register_device(missing)
        offline_id = store.register_device(offline)
        store._db.execute("UPDATE device SET network_features = NULL")
        store._db.execute("UPDATE device SET network_features = 7 WHERE id = ?", (preset_id,))
        store._db.commit()

        filled = store.backfill_network_features([preset, missing])

        assert filled == 1
        assert store.device(preset_id).network_features == 7
        assert store.device(missing_id).network_features == 2
        assert store.device(offline_id).network_features is None
    finally:
        store.close()


def test_commands_carry_the_owning_devices_identity(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    try:
        snapshot = _fixture("ikea_kajplats_ws_lamp.json")
        device_id = store.register_device(snapshot)
        commands = store.register_commands(device_id, extract_commands(snapshot))
        assert commands
        assert {(c.technology, c.address) for c in commands} == {("matter", "14")}
    finally:
        store.close()


def test_parse_technology_rejects_an_unknown_value():
    assert parse_technology("zigbee") == "zigbee"
    with pytest.raises(ValueError, match="bluetooth"):
        parse_technology("bluetooth")
