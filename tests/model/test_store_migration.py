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
    """Backfill von `signal.exported` beim Sprung von Schema-Version 0 auf
    die aktuelle Version - seit Aufgabe 7 (`_migrate_to_v3`) nicht mehr nur
    `is_exportable`, sondern zusaetzlich `is_functional`. Keine der vier
    Zeilen dieser Fixture besteht das: `d1_1_onoff` liegt auf Endpunkt 0
    (dem Verwaltungs-Endpunkt, siehe `_migrate_to_v3`) und ist dort kein
    PowerSource; `d1_1_power` benutzt Cluster 6 (OnOff) mit einer
    Element-ID, die die Tabelle nicht als "onoff" fuehrt (nur Element 0 ist
    benannt) - beide waeren nach der reinen `is_exportable`-Regel von
    frueher (Review-Fix Important #1) noch exportiert gewesen, jetzt nicht
    mehr. `d1_1_vendor` (TEXT) und `d1_1_liste` (NONE) waren es nach keiner
    der beiden Regeln je."""
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
    # Text und "none" waren es nie -> exported=False, nicht der Spalten-Default 1.
    assert signals["d1_1_vendor"].exported is False
    assert signals["d1_1_liste"].exported is False


def test_migrating_an_old_database_sets_the_schema_version(tmp_path):
    path = tmp_path / "old.sqlite"
    build_old_database(path)
    assert user_version(path) == 0

    store = Store(path)
    store.close()

    assert user_version(path) == 4


def test_reopening_an_already_migrated_store_is_a_noop(tmp_path):
    """Kein erneuter Backfill beim zweiten Oeffnen: ein zwischenzeitlich vom
    Nutzer bewusst auf True gesetztes `exported` (fuer ein Signal, das keine
    Backfill-Regel je als exportiert einstufen wuerde, siehe
    `test_opening_a_pre_exported_database_backfills_the_column_correctly`)
    darf beim naechsten Start nicht stillschweigend zurueckgesetzt werden."""
    path = tmp_path / "old.sqlite"
    build_old_database(path)

    first = Store(path)
    first.set_exported("d1_1_power", True)
    first.close()
    assert user_version(path) == 4

    second = Store(path)
    try:
        power = next(s for s in second.signals(1) if s.key == "d1_1_power")
    finally:
        second.close()

    assert power.exported is True
    assert user_version(path) == 4


def test_a_fresh_database_is_already_at_the_latest_version(tmp_path):
    path = tmp_path / "fresh.sqlite"
    store = Store(path)
    store.close()
    assert user_version(path) == 4


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
    assert user_version(path) == 4


def test_opening_a_v1_database_only_runs_the_v2_migration(tmp_path):
    """Der bislang unbelegte Zwischenschritt: eine Datenbank, die genau auf
    Version 1 steht (`signal.exported` vorhanden, `device.exported_at`/
    `updated_at` noch nicht), oeffnet sich fehlerfrei und landet auf der
    aktuellen Version - `_migrate_to_v1` darf dabei NICHT erneut laufen
    (sonst scheiterte `ALTER TABLE signal ADD COLUMN exported` mit
    "duplicate column", weil die Spalte schon da ist); `_migrate_to_v2`,
    `_migrate_to_v3` und `_migrate_to_v4` laufen alle drei, in dieser
    Reihenfolge.

    `signal.key` bleibt dabei unangetastet (Hauptdokument 6.2) - `title`
    und `exported` dagegen nicht mehr: Cluster 6 (OnOff) ist der Tabelle
    bekannt, aber nur Element 0 heisst dort "onoff" - Element 1 (diese
    Fixture) faellt seit Aufgabe 7 (`_migrate_to_v3`) durch
    `is_functional`, unabhaengig vom hier hinterlegten `exported=1`."""
    path = tmp_path / "v1.sqlite"
    build_v1_database(path)
    assert user_version(path) == 1

    store = Store(path)
    try:
        (device,) = store.devices()
        (signal,) = store.signals(1)
    finally:
        store.close()

    assert user_version(path) == 4
    assert device.exported_at is None
    assert device.updated_at is None
    assert signal.key == "d1_1_power"
    assert signal.exported is False


def test_reopening_an_already_v2_database_is_a_noop(tmp_path):
    """Ergaenzt `test_opening_a_v1_database_only_runs_the_v2_migration`:
    ein zweites Oeffnen nach der Migration darf keine weitere Schreibung
    ausloesen und die Werte unveraendert lassen."""
    path = tmp_path / "v1.sqlite"
    build_v1_database(path)

    first = Store(path)
    first.close()
    assert user_version(path) == 4

    second = Store(path)
    try:
        (device,) = second.devices()
    finally:
        second.close()

    assert user_version(path) == 4
    assert device.exported_at is None
    assert device.updated_at is None


# Schema-Stand 2 (Aufgabe 5, Phase 5): alle heutigen Spalten sind schon da -
# anders als `_OLD_SCHEMA`/`_V1_SCHEMA` oben geht es hier nicht um eine
# fehlende Spalte, sondern um veraltete WERTE in genau diesen Spalten
# (`title`, `unit`, `exported`), die Aufgabe 7 rueckwirkend korrigiert.
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
    """Tut so, als kaeme `(ref.cluster_id, ref.element_id)` in KEINER
    Fassung von `clusters.yaml` vor - unabhaengig davon, ob die Tabelle es
    HEUTE tatsaechlich benennt. Baut slug/title/unit/exportability genauso
    wie der "kein Eintrag"-Zweig von `profiles.table.lookup`, nur ohne je
    den "Eintrag vorhanden"-Zweig zu betreten.

    Simuliert damit ein Geraet, das lange vor jeder clusters.yaml-Korrektur
    eingelernt wurde - genau der Fall, den Aufgabe 7 nachtraeglich
    repariert: der Batteriestand (Cluster 47, Element 12) bekaeme so den
    Schluessel `d1_0_c47_a12`, obwohl `clusters.yaml` das Element inzwischen
    "battery" nennt. `slug` haengt nur an `cluster_id`/`element_id`, nie an
    `title`/`unit`/`exportability` - zwei verschiedene Refs koennen deshalb
    hier nie denselben Schluessel bekommen, `_assign_key`s
    Element-ID-Zusatz bei einer Kollision (siehe `model.store`) ist fuer
    diesen rein generischen Fall nicht noetig.
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
    """Baut (bzw. erweitert) eine Datenbank auf Schema-Stand 2 - dem Stand
    VOR Aufgabe 7, aber schon NACH Aufgabe 5 (`signal.exported`,
    `device.exported_at`/`updated_at` sind vorhanden) - direkt per
    `sqlite3`, wie `_OLD_SCHEMA`/`_V1_SCHEMA` oben, nicht ueber `Store`
    (siehe Modul-Docstring: `Store` legt heute schon alles frisch an und
    wuerde das eigentliche Problem nicht reproduzieren).

    Schreibt Schluessel und Titel ueber `_pretend_unnamed_profile`, NICHT
    ueber `profiles.table.lookup` direkt - `lookup` kennt die HEUTIGE
    `clusters.yaml` und wuerde fuer den Batteriestand sofort den neuen
    Schluessel `d1_0_battery` vergeben, womit der Testfall (ein Schluessel
    aus der Zeit VOR dieser Benennung) gar nicht erst entstuende.
    `exported` ist auf die Vor-Aufgabe-6-Regel gesetzt: 1 fuer alles
    technisch Exportierbare (`profiles.table.is_exportable`), unabhaengig
    davon, ob es jemand standardmaessig WILL
    (`profiles.relevance.is_functional`) - genau die "Signalflut", die
    Aufgabe 6 fuer neu eingelernte Geraete behoben hat und Aufgabe 7 fuer
    Bestandsgeraete nachholt.

    `device_id` erlaubt mehrere Geraete in derselben Datenbank (Aufruf
    mehrfach mit verschiedenen `device_id`/`image`-Werten gegen denselben
    `path`) - fuer die Gegenprobe aus Schritt 5 gegen beide eingecheckten
    Abbilder.

    `extra_row` haengt eine zusaetzliche Zeile an (Schluessel, Endpunkt,
    Cluster-ID, Element-ID, Art) fuer einen Cluster, den die Profiltabelle
    nicht kennt - fuer
    `test_a_row_with_an_unknown_cluster_still_gets_rewritten`.

    Gibt die Menge der in DIESEM Aufruf vergebenen Schluessel zurueck
    (inklusive `extra_row`, falls gesetzt).
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
    """Die eiserne Regel (Hauptdokument 6.2). Ein umbenannter Schluessel
    waere ein stillschweigend toter Funktionsbaustein in einer fremden
    Config - kein Fehler, den irgendjemand von aussen sehen wuerde."""
    path = tmp_path / "s.sqlite"
    keys_before = _build_store_at_schema_v2(path)
    store = Store(path)  # oeffnet und migriert
    assert {s.key for s in store.signals(1)} == keys_before


def test_the_migration_refreshes_title_and_unit_from_the_table(tmp_path):
    """Ohne diesen Schritt erreichte eine Korrektur in clusters.yaml ein
    schon gespeichertes Signal nie."""
    path = tmp_path / "s.sqlite"
    _build_store_at_schema_v2(path)
    store = Store(path)
    battery = next(s for s in store.signals(1) if s.ref.cluster_id == 47 and s.ref.element_id == 12)
    assert battery.key == "d1_0_c47_a12", "Schluessel bleibt der alte"
    assert battery.title == "battery"
    assert battery.unit == "%"


def test_the_migration_applies_the_new_default_to_existing_devices(tmp_path):
    path = tmp_path / "s.sqlite"
    _build_store_at_schema_v2(path)
    store = Store(path)
    exported = {s.key for s in store.signals(1) if s.exported}
    assert len(exported) < 30, "die Signalflut muss auch rueckwirkend weg sein"


def test_a_row_with_an_unknown_cluster_still_gets_rewritten(tmp_path):
    """Umbenannt (Nachbesserung Fix 3, Aufgabe 7) - der urspruengliche Name
    behauptete, dieser Test belege das `except` in `_migrate_to_v3`s
    Zeilenschleife. Das stimmt nicht: `lookup()` faengt einen unbekannten
    Cluster selbst ab (generischer Slug/Titel-Zweig) und wirft nie - die
    Zeile ueberlebt zwar, wird dabei aber umgeschrieben (`title='kaputt'`
    wird zu `title='c4711_a0'`, dem generischen Titel). Dieser Test prueft
    also nur, dass ein unbekannter Cluster KEINEN Fehler ausloest, nicht die
    Ausfalleigenschaft selbst - die steckt in
    `test_an_unparseable_row_survives_the_migration_untouched` unten."""
    path = tmp_path / "s.sqlite"
    _build_store_at_schema_v2(path, extra_row=("d1_9_kaputt", 9, 4711, 0, "attribute"))
    store = Store(path)
    try:
        (row,) = [s for s in store.signals(1) if s.key == "d1_9_kaputt"]
    finally:
        store.close()
    assert row.title == "c4711_a0"


def test_an_unparseable_row_survives_the_migration_untouched(tmp_path):
    """Die tragende Ausfalleigenschaft (Entwurf 8, Nachbesserung Fix 3,
    Aufgabe 7) direkt am `except (ValueError, KeyError)` in
    `_migrate_to_v3`s Zeilenschleife, nicht nur an ihrer Aussenwirkung.

    `Store._as_signal` parst `kind` ueber `SignalKind(row["kind"])` genauso
    ungeschuetzt wie `_migrate_to_v3` selbst - `store.signals(1)` scheitert
    deshalb an dieser Zeile ebenso (belegt: eine fruehere Fassung dieses
    Tests rief genau das auf und bekam denselben `ValueError`, den
    `_migrate_to_v3` selbst abfaengt). Direkt per `sqlite3` (wie
    `build_old_database`/`build_v1_database` oben) ist die Zeile dagegen
    problemlos anzulegen, ohne je durch `Store` zu gehen: `SignalKind('kaputt')`
    innerhalb von `_migrate_to_v3` wirft `ValueError`, BEVOR `lookup()`
    ueberhaupt aufgerufen wird, landet im `except` und bleibt Wort fuer Wort
    stehen - inklusive des ansonsten immer neu abgeleiteten `title`. Die
    Kehrseite gilt beim LESEN nach der Migration genauso: die gesunde
    Batteriezeile wird gezielt ueber `signal_by_key` geholt (beruehrt nur
    diese eine Zeile), die kaputte danach roh per `sqlite3` - `store.signals(1)`
    liefe fuer BEIDE Zeilen durch `_as_signal` und schluege mit der kaputten
    noch in der Tabelle sofort fehl. Die gesunde Zeile im selben Aufruf
    zeigt gleichzeitig: die kaputte Zeile reisst die Transaktion nicht mit,
    die gesunde wird migriert."""
    path = tmp_path / "s.sqlite"
    db = sqlite3.connect(str(path))
    try:
        db.executescript(_V2_SCHEMA)
        db.execute(
            "INSERT INTO device (id, unique_id, node_id, label, udp_port, active)"
            " VALUES (1, 'dev-1', 42, 'Testgeraet', 7000, 1)"
        )
        # exportability=analog wie bei einer echten Registrierung: der
        # Batteriestand liefert schon damals eine Zahl (siehe
        # `clusters.yaml`, Cluster 47), `exported=1` wie bei der
        # Vor-Aufgabe-6-Regel (nur `is_exportable`, ohne `is_functional`,
        # siehe `_build_store_at_schema_v2` oben).
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

    # `store.signals(1)` geht fuer BEIDE Zeilen durch `_as_signal`, das
    # `kind` genauso ungeschuetzt parst wie `_migrate_to_v3` - mit der
    # kaputten Zeile noch in der Tabelle wuerde das hier selbst scheitern
    # (Store._as_signal). Deshalb: die gesunde Zeile gezielt ueber
    # `signal_by_key` (beruehrt nur diese eine Zeile), die kaputte danach
    # roh per `sqlite3` - exakt der Weg, den ein `kind`, das `Store` selbst
    # nie mehr einliest, tatsaechlich noch pruefbar macht.
    store = Store(path)
    try:
        battery = store.signal_by_key("d1_0_c47_a12")
    finally:
        store.close()
    assert battery is not None

    # Die gesunde Zeile wurde neu abgeleitet ...
    assert battery.title == "battery"
    assert battery.unit == "%"
    assert battery.exportability is Exportability.ANALOG
    assert battery.exported is True

    # ... die kaputte blieb Wort fuer Wort stehen, kein Abbruch - belegt
    # roh per `sqlite3`, weil `Store` selbst an ihrem `kind` scheitern
    # wuerde (siehe oben).
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
    """Haelt die in Fix 1 bewusst hingenommene Grenze fest (Nachbesserung
    Aufgabe 7, siehe `_migrate_to_v3`-Docstring, Abschnitt "Zwei offene
    Grenzen dieser Ausnahme"): die Feldnummer-Ausnahme sieht den
    Laufzeitwert nicht.

    Diese Zeile steht hier so, wie sie eine echte Registrierung VOR
    Einfuehrung des `field:0`-Eintrags fuer Cluster 145 angelegt haette,
    UND wie sie eine Registrierung anlegen wuerde, waere der zugrunde
    liegende Zaehler noch nie gemessen worden (Matter liefert dafuer
    `null`, `classify(None)` ergibt `none` - siehe `_pretend_unnamed_profile`
    und `profiles.table.classify`): `exportability=none`, `exported=0`.

    Die Migration hebt sie trotzdem auf ANALOG und exportiert - unabhaengig
    davon, ob der Zaehler inzwischen wirklich einen Wert hat, denn
    `_migrate_to_v3` ruft `lookup(ref, None)` immer mit `value=None` auf.
    Zur Laufzeit bleibt das folgenlos (`to_loxone_value` liefert fuer einen
    weiterhin `null`en Wert ebenfalls `None`), aber die erzeugte
    Loxone-Vorlage bekommt einen Eingang, der nie einen Wert traegt - siehe
    Docstring."""
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

    assert signal.key == "d1_2_c145_a2", "Schluessel bleibt der alte"
    assert signal.exportability is Exportability.ANALOG
    assert signal.exported is True


def test_the_migration_reproduces_the_functional_export_counts_of_both_fixtures(tmp_path):
    """Die Gegenprobe aus dem Entwurf (Schritt 5): eine Datenbank aus beiden
    eingecheckten Abbildern, nach altem Schema aufgebaut und migriert, muss
    dieselbe Anzahl exportierter Signale ergeben wie eine frische
    Registrierung (`tests/model/test_store.py`:
    `test_a_freshly_registered_plug_exports_only_its_meaningful_values` = 5,
    `test_a_freshly_registered_button_keeps_both_rockers_and_the_battery` =
    17) - der Beleg dafuer, dass die Ersatzregel fuer den
    Verwaltungs-Endpunkt in `_migrate_to_v3` (siehe dort) an den beiden
    einzigen echten Geraeten dieses Projekts nichts verliert. Der
    tatsaechliche Ausgabewert dieses Tests steht im Bericht zu Aufgabe 7."""
    path = tmp_path / "both.sqlite"
    _build_store_at_schema_v2(path, device_id=1, image="ikea_grillplats_plug.json")
    _build_store_at_schema_v2(path, device_id=2, image="ikea_bilresa_button.json")

    store = Store(path)  # oeffnet und migriert
    try:
        counts = {
            d.label: len([s for s in store.signals(d.id) if s.exported]) for d in store.devices()
        }
    finally:
        store.close()

    assert sorted(counts.values()) == [5, 17]


def test_the_v4_migration_backfills_functional_independently_of_exported(tmp_path):
    """Ergaenzt die Gegenprobe oben um die neue Spalte (`_migrate_to_v4`,
    Aufgabe 8): `functional` wird ueber dieselbe Ersatzregel wie `exported`
    in `_migrate_to_v3` hergeleitet (`_endpoint0_device_types`), landet aber
    in einer EIGENEN Spalte - fuer beide eingecheckten Abbilder ist hier
    jedes exportierte Signal auch funktional und umgekehrt, weshalb dieselben
    zwei Zahlen wie oben herauskommen muessen, obwohl `_migrate_to_v4` sie
    unabhaengig von `exported` neu berechnet."""
    path = tmp_path / "both-functional.sqlite"
    _build_store_at_schema_v2(path, device_id=1, image="ikea_grillplats_plug.json")
    _build_store_at_schema_v2(path, device_id=2, image="ikea_bilresa_button.json")

    store = Store(path)  # oeffnet und migriert (v2 -> v3 -> v4)
    try:
        counts = {
            d.label: len([s for s in store.signals(d.id) if s.functional]) for d in store.devices()
        }
    finally:
        store.close()

    assert sorted(counts.values()) == [5, 17]
