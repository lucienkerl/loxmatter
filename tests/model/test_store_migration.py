"""Migrationstests fuer `signal.exported` (Review-Fix Important #1, 2026-09-02).

Baut absichtlich eine Datenbank mit dem SCHEMA-STAND VOR `exported`
(commit 00902fc~1 in `src/loxmatter/model/store.py`) direkt per `sqlite3`
auf, statt sie ueber `Store` zu erzeugen - `Store` legt heute immer schon die
neue Spalte an, das reproduziert also gerade NICHT das Problem einer echten
Alt-Datenbank, die vor diesem Review-Fix mit `loxmatter export` oder
`loxmatter run` angelegt wurde.

Bewusst kein `try/except` in `Store` selbst, um alte Datenbanken irgendwie
"vertraeglich" zu machen - siehe `model.store._migrate`: `PRAGMA
user_version` unterscheidet sauber zwischen "Version 0, noch nie migriert"
und "schon auf dem neuesten Stand", und die Migration laeuft transaktional.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from loxmatter.model.store import Store
from loxmatter.profiles.table import Exportability

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
    """Legt eine Datenbank im Schema-Stand vor `exported` an und befuellt sie
    mit einem Geraet und je einem Signal pro `Exportability`-Wert - direkt
    per `sqlite3`, ohne `Store` zu benutzen (siehe Modul-Docstring)."""
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
    """Legt eine Datenbank GENAU auf Schema-Version 1 an (Review-Fix Minor
    #2, 2026-09-02): `signal.exported` ist schon da (anders als bei
    `build_old_database`, Version 0), `device.exported_at`/`updated_at`
    dagegen noch nicht (die kommen erst mit `_migrate_to_v2`).

    Bislang testete diese Datei nur den Sprung von Version 0 auf die
    aktuelle Version - nie das Oeffnen einer Datenbank, die zwischen zwei
    Schema-Aenderungen dieser Phase tatsaechlich angelegt wurde (etwa von
    einer Installation, die genau zwischen Task 2 und Task 5 dieser Phase
    einmal `loxmatter export` oder `loxmatter run` ausgefuehrt hat). Dass
    `_migrate` schrittweise ueber `range(version + 1, _SCHEMA_VERSION + 1)`
    laeuft, macht diesen Zwischenschritt zwar plausibel - belegt war er
    bislang nicht."""
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
    path = tmp_path / "old.sqlite"
    build_old_database(path)

    store = Store(path)
    try:
        signals = {s.key: s for s in store.signals(1)}
    finally:
        store.close()

    assert len(signals) == 4
    # Digital und analog waren schon vorher exportierbar -> exported=True.
    assert signals["d1_1_onoff"].exported is True
    assert signals["d1_1_power"].exported is True
    # Text und "none" waren es nie -> exported=False, nicht der Spalten-Default 1.
    assert signals["d1_1_vendor"].exported is False
    assert signals["d1_1_liste"].exported is False


def test_migrating_an_old_database_sets_the_schema_version(tmp_path):
    path = tmp_path / "old.sqlite"
    build_old_database(path)
    assert user_version(path) == 0

    store = Store(path)
    store.close()

    assert user_version(path) == 2


def test_reopening_an_already_migrated_store_is_a_noop(tmp_path):
    """Kein erneuter Backfill beim zweiten Oeffnen: ein zwischenzeitlich vom
    Nutzer bewusst auf False gesetztes `exported` (fuer ein Signal, dessen
    Backfill-Regel es eigentlich als True eingestuft haette) darf beim
    naechsten Start nicht stillschweigend zurueckgesetzt werden."""
    path = tmp_path / "old.sqlite"
    build_old_database(path)

    first = Store(path)
    first.set_exported("d1_1_power", False)
    first.close()
    assert user_version(path) == 2

    second = Store(path)
    try:
        power = next(s for s in second.signals(1) if s.key == "d1_1_power")
    finally:
        second.close()

    assert power.exported is False
    assert user_version(path) == 2


def test_a_fresh_database_is_already_at_the_latest_version(tmp_path):
    path = tmp_path / "fresh.sqlite"
    store = Store(path)
    store.close()
    assert user_version(path) == 2


def test_migration_failure_leaves_the_database_unchanged(tmp_path, monkeypatch):
    """Schlaegt eine Migration fehl, darf weder die neue Spalte noch die
    Versionsnummer haengen bleiben - siehe `model.store._migrate`."""
    path = tmp_path / "old.sqlite"
    build_old_database(path)

    def boom(db: sqlite3.Connection) -> None:
        db.execute("ALTER TABLE signal ADD COLUMN exported INTEGER NOT NULL DEFAULT 1")
        raise RuntimeError("simulierter Absturz mitten in der Migration")

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
    """Migration v2 (Task 5, Phase 5) - siehe `model.store._migrate_to_v2`.
    Eine Alt-Datenbank kennt keinen Exportzeitpunkt; `None` statt eines
    erratenen Werts ist hier die einzig ehrliche Antwort."""
    path = tmp_path / "old.sqlite"
    build_old_database(path)

    store = Store(path)
    try:
        (device,) = store.devices()
    finally:
        store.close()

    assert device.exported_at is None
    assert device.updated_at is None
    assert user_version(path) == 2


def test_opening_a_v1_database_only_runs_the_v2_migration(tmp_path):
    """Der bislang unbelegte Zwischenschritt: eine Datenbank, die genau auf
    Version 1 steht (`signal.exported` vorhanden, `device.exported_at`/
    `updated_at` noch nicht), oeffnet sich fehlerfrei und landet auf
    Version 2 - `_migrate_to_v1` darf dabei NICHT erneut laufen (sonst
    scheiterte `ALTER TABLE signal ADD COLUMN exported` mit "duplicate
    column", weil die Spalte schon da ist)."""
    path = tmp_path / "v1.sqlite"
    build_v1_database(path)
    assert user_version(path) == 1

    store = Store(path)
    try:
        (device,) = store.devices()
        (signal,) = store.signals(1)
    finally:
        store.close()

    assert user_version(path) == 2
    assert device.exported_at is None
    assert device.updated_at is None
    # Der v1-Bestandswert bleibt unangetastet - kein erneuter Backfill.
    assert signal.key == "d1_1_power"
    assert signal.exported is True


def test_reopening_an_already_v2_database_is_a_noop(tmp_path):
    """Ergaenzt `test_opening_a_v1_database_only_runs_the_v2_migration`:
    ein zweites Oeffnen nach der Migration darf keine weitere Schreibung
    ausloesen und die Werte unveraendert lassen."""
    path = tmp_path / "v1.sqlite"
    build_v1_database(path)

    first = Store(path)
    first.close()
    assert user_version(path) == 2

    second = Store(path)
    try:
        (device,) = second.devices()
    finally:
        second.close()

    assert user_version(path) == 2
    assert device.exported_at is None
    assert device.updated_at is None
