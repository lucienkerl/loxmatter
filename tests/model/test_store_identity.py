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


def test_a_v8_database_moves_node_ids_into_addresses(tmp_path):
    """Protects: the `address` backfill, the two dropped columns, and that
    no Loxone key changes. Fault to prove it: comment out the `UPDATE
    device SET address = ...` line in `_migrate_to_v9`."""
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
    assert "node_id" not in _columns(path, "device")
    assert "node_id" not in _columns(path, "command")


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
