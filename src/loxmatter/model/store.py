"""SQLite-Ablage fuer Geraete und Signale.

Der Schluessel eines Signals ist die Verdrahtung in Loxone (Spec 6.2). Er wird
einmal vergeben und danach nie geaendert — weder beim Umbenennen noch bei einem
erneuten Einlesen desselben Geraets. Deshalb liegt er in einer Datenbank und
nicht in einer Ableitung zur Laufzeit.

device_id wird nie wiederverwendet: ein entferntes und neu eingelerntes Geraet
bekommt neue Schluessel, damit es keine alte Verdrahtung stillschweigend erbt.

Schluesselformat (Spec 6.2): ``d<device_id>_<endpoint>_<slug>``, z. B.
``d12_1_temp``. Zwei Signale auf demselben Endpoint koennen denselben
Profil-Slug tragen (z. B. mehrere Events desselben Clusters, die zufaellig
gleich benannt sind, oder ein generischer Slug fuer zwei unbekannte
Attribute) — in dem Fall haengt ``_assign_key`` die Element-ID an
(``d12_1_temp_5``), um die von der Tabelle ``signal.key`` erzwungene
Eindeutigkeit zu erhalten, ohne den Schluessel eines schon vergebenen
Signals zu aendern.

Eine Store-Instanz gehoert genau einem Thread und genau einer Event-Loop -
`sqlite3.Connection` ist ohne `check_same_thread=False` an ihren Erzeuger-
Thread gebunden, und dieses Modul weicht davon bewusst nicht ab.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from loxmatter.export.commands import DeviceCommand
from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.profiles.relevance import (
    ROOT_NODE_DEVICE_TYPE,
    UTILITY_ENDPOINT_KEEP_CLUSTERS,
    device_types_by_endpoint,
    is_functional,
)
from loxmatter.profiles.table import Exportability, is_exportable, lookup, struct_field
from loxmatter.timestamps import now_iso

DEFAULT_UDP_PORT = 7000

# Schema-Version dieses Moduls, verwaltet ueber `PRAGMA user_version` (Review-Fix
# Important #1, 2026-09-02). `CREATE TABLE IF NOT EXISTS` allein erreicht eine
# bereits bestehende Tabelle nie mit einer neuen Spalte - eine Datenbank, die vor
# dem `exported`-Feld angelegt wurde, blieb bislang ohne Migration dauerhaft ohne
# diese Spalte, und `Store.signals()` scheiterte mit `IndexError`. Version 0 ist
# "vor dieser Migrationslogik" (jede bestehende Datenbank, `PRAGMA user_version`
# noch nie gesetzt); Version 1 fuegt `signal.exported` hinzu und befuellt
# Bestandszeilen zurueckwirkend, siehe `_migrate_to_v1`. Version 2 (Task 5,
# Phase 5) fuegt `device.exported_at` und `device.updated_at` hinzu, siehe
# `_migrate_to_v2`. Version 3 (Aufgabe 7, Phase 6) fuegt keine Spalte hinzu -
# sie leitet `signal.title`, `signal.unit` und den Vorgabewert von
# `signal.exported` fuer BESTEHENDE Zeilen aus der Profiltabelle neu ab, siehe
# `_migrate_to_v3`.
_SCHEMA_VERSION = 3

_SCHEMA = """
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


def _add_column_if_missing(db: sqlite3.Connection, table: str, column: str, ddl: str) -> bool:
    """Fuegt `column` zu `table` hinzu, falls sie fehlt - gemeinsame Absicherung
    fuer `_migrate_to_v1` und `_migrate_to_v2` (Review-Fix Minor #3, 2026-09-02:
    beide pruefen `PRAGMA table_info`, dieselbe Falle, dieselbe Idee, zuvor
    zweimal von Hand hingeschrieben statt einmal geteilt).

    Die Falle, gegen die der Spaltencheck schuetzt: eine frisch angelegte
    Datenbank hat eine neue Spalte durch `_SCHEMA`s `CREATE TABLE IF NOT
    EXISTS` bereits, waehrend `PRAGMA user_version` bei ihr ebenfalls noch auf
    0 steht (siehe `_migrate`). `ALTER TABLE ... ADD COLUMN` liefe dort gegen
    eine schon vorhandene Spalte und scheiterte mit "duplicate column".

    Gibt zurueck, ob die Spalte neu hinzugefuegt wurde (`False`, wenn sie
    schon da war) - `_migrate_to_v1` braucht das, um seinen Backfill nur bei
    einer echten Alt-Datenbank auszufuehren, nicht bei einer frischen."""
    columns = {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})")}
    if column in columns:
        return False
    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return True


def _migrate_to_v1(db: sqlite3.Connection) -> None:
    """Fuegt `signal.exported` hinzu und befuellt bestehende Zeilen anhand
    ihrer `exportability` (Review-Fix Important #1 und #2, 2026-09-02) -
    dieselbe Regel wie bei einem frisch registrierten Signal, siehe
    `profiles.table.is_exportable`.

    Der Backfill laeuft nur, wenn `_add_column_if_missing` die Spalte
    tatsaechlich neu angelegt hat - bei einer frisch erzeugten Datenbank
    (Spalte schon durch `_SCHEMA` da) gibt es keine Bestandszeilen, die
    rueckwirkend befuellt werden muessten.
    """
    if not _add_column_if_missing(db, "signal", "exported", "INTEGER NOT NULL DEFAULT 1"):
        return
    # Aus `is_exportable` abgeleitet statt hier ein drittes Mal von Hand
    # aufgezaehlt (Review-Fix Fix 8, 2026-09-03, zusammen mit den beiden
    # Kopien in `cli.py` und `api/export.py`): eine SQL-Abfrage braucht
    # die Werte als Liste, nicht die Funktion - die Liste selbst kommt
    # jetzt trotzdem aus derselben einen Quelle.
    exportable_values = tuple(e.value for e in Exportability if is_exportable(e))
    placeholders = ", ".join("?" for _ in exportable_values)
    db.execute(
        f"UPDATE signal SET exported = CASE WHEN exportability IN ({placeholders})"
        " THEN 1 ELSE 0 END",
        exportable_values,
    )


def _migrate_to_v2(db: sqlite3.Connection) -> None:
    """Fuegt `device.exported_at` und `device.updated_at` hinzu (Task 5,
    Phase 5) - Grundlage fuer `GET /api/export/status`: wann ein Geraet
    zuletzt exportiert wurde, und ob sich seither etwas geaendert hat.

    Beide Spalten bleiben bei einer bereits bestehenden Zeile NULL statt
    rueckwirkend befuellt zu werden - anders als bei `_migrate_to_v1` gibt es
    hier keinen Bestandswert, aus dem sich ein sinnvoller Zeitpunkt ableiten
    liesse. `NULL` bedeutet fuer `exported_at` "noch nie exportiert" (dieselbe
    Bedeutung wie bei einem frisch registrierten Geraet) und fuer
    `updated_at` "unbekannt" - `api.export._status_for` behandelt ein
    unbekanntes `updated_at` als "seither geaendert", die vorsichtigere der
    beiden moeglichen Annahmen.

    Jede Spalte einzeln ueber `_add_column_if_missing` geprueft, weil eine
    Datenbank, die genau auf Version 1 steht (`signal.exported` vorhanden,
    beide Spalten hier noch nicht), von einer echten Alt-Datenbank (Version
    0, laeuft `_migrate_to_v1` und `_migrate_to_v2` nacheinander in
    demselben Lauf) nicht zu unterscheiden sein muss - beide landen hier mit
    fehlenden Spalten und bekommen sie angelegt."""
    _add_column_if_missing(db, "device", "exported_at", "TEXT")
    _add_column_if_missing(db, "device", "updated_at", "TEXT")


def _migrate_to_v3(db: sqlite3.Connection) -> None:
    """Leitet `title`, `unit` und den Vorgabewert von `exported` fuer
    BESTEHENDE Signale neu ab (Aufgabe 7) - der Schluessel bleibt dabei in
    jedem Fall unangetastet, siehe Modul-Docstring und Hauptdokument 6.2.

    **Warum rueckwirkend, nicht nur fuer neu eingelernte Geraete:** Aufgabe 6
    hat `profiles.relevance.is_functional` bereits verdrahtet, aber nur in
    `register_signals` - ein Geraet, das gestern eingelernt wurde, sieht die
    Korrektur nie, ausser es wird komplett neu eingelernt. Zwei
    Regelsaetze, deren Unterschied allein am Einlerndatum haengt, waeren
    niemandem zu erklaeren.

    **Der Schluessel bleibt unangetastet.** Diese Migration schreibt nie in
    die Spalte `key`. Folge: ein vor diesem Update eingelerntes Geraet
    behaelt z. B. `d2_0_c47_a12` und heisst ab jetzt "battery"; ein danach
    eingelerntes Geraet bekommt fuer denselben Wert den neuen Schluessel
    `d2_0_battery`. Zwei Schluessel fuer denselben Wert, je nach
    Einlerndatum - haesslich, aber Absicht (Hauptdokument 6.2): die
    Alternative waere ein stillschweigend toter Funktionsbaustein in einer
    fremden Loxone-Konfiguration.

    **Woher die Geraetetypen je Endpunkt kommen (die im Aufgabenzuschnitt
    bewusst offen gelassene Entscheidung):** `is_functional` braucht die vom
    Geraet deklarierten Geraetetypen je Endpunkt, um einen
    Verwaltungs-Endpunkt (Root Node, OTA Requestor) von einem Nutz-Endpunkt
    zu unterscheiden - diese Angabe steht im Geraeteabbild
    (Descriptor-Cluster), nicht in dieser Datenbank.

    Eine neue Spalte, die `register_device`/`register_signals` ab sofort
    mitschreibt, loest das NICHT: eine Migration laeuft beim Oeffnen einer
    Datenbank (`_migrate` ruft sie mit `db: sqlite3.Connection` auf, nie mit
    einem `NodeSnapshot`) und hat deshalb NIE ein Abbild zur Hand - auch in
    einer kuenftigen Migration nicht. Und genau die hier zu migrierenden
    Bestandszeilen sind vor einer solchen Spalte entstanden, haetten also
    ohnehin nichts, das sie befuellen koennte. Eine neue Spalte waere damit
    fuer DIESE Migration wertlos; sie haette nur helfen koennen, wenn sie
    schon bei der urspruenglichen Registrierung existiert haette.

    Deshalb der zweite Weg aus dem Aufgabenzuschnitt: eine Ersatzregel aus
    den ohnehin gespeicherten Cluster-/Endpunkt-Nummern. Matter garantiert
    strukturell, dass Endpunkt 0 immer der Root-Node-Endpunkt ist (Core-
    Spezifikation 9.2.1) - das ist die einzige Aussage ueber einen
    Verwaltungs-Endpunkt, die sich OHNE Abbild sicher treffen laesst; jeder
    andere Endpunkt gilt hier als gewoehnlicher Nutz-Endpunkt. Fuer den
    bislang einzigen belegten Ausnahmefall
    (`relevance.UTILITY_ENDPOINT_KEEP_CLUSTERS`: PowerSource, Cluster 47)
    gilt der zugehoerige Nutz-Geraetetyp als erklaert, sobald Endpunkt 0
    ueberhaupt ein Signal dieses Clusters traegt - ein Geraet exponiert den
    PowerSource-Cluster auf seinem Root-Endpunkt nur, wenn es dort
    tatsaechlich einen Batteriestand zu melden hat. Gegengeprueft an beiden
    eingecheckten Abbildern (`tests/fixtures/nodes/`, siehe
    `test_store_migration.py`,
    `test_the_migration_reproduces_the_functional_export_counts_of_both_fixtures`):
    diese Ersatzregel liefert fuer den Stecker und den Taster exakt
    dieselbe Anzahl exportierter Signale wie eine frische Registrierung mit
    echtem Abbild - 5 bzw. 17.

    **`exportability` wird nur in einem einzigen, eng begrenzten Fall
    angehoben, sonst unangetastet gelassen:** `classify()` (Spec 6.6)
    braucht grundsaetzlich einen echten Laufzeitwert, den eine Migration
    nie hat - der gespeicherte Wert bleibt deshalb im Regelfall die beste
    verfuegbare Wahrheit. Die eine Ausnahme: traegt der Tabelleneintrag
    heute eine Feldnummer (`profiles.table.struct_field` - Aufgabe 5, der
    Zaehlerstand), gilt das daraus gezogene Element als abbildbar (ANALOG),
    UNABHAENGIG vom gespeicherten Wert. Begruendung: eine Feldnummer traegt
    nur ein, wer im Matter-Spezifikationstext nachgesehen hat, dass genau
    dieses Struktur-Element numerisch ist (Cluster-Autor-Wissen, keine
    Laufzeit-Eigenschaft) - eine Zeile, deren Struktur zur Registrierzeit
    noch nicht auslesbar war (kein `field:` in der damaligen
    `clusters.yaml`, deshalb `exportability=none`, Spec 6.6), wird durch
    diese Migration genau wie durch ein Neu-Einlesen mit echtem Wert auf
    ANALOG gehoben. Fuer jeden benannten Eintrag OHNE Feldnummer ist
    `classify(struct_member(ref, value))` ohnehin identisch mit
    `classify(value)` - Benennung allein aendert die Klassifizierung nie,
    nur eine neue Feldnummer tut das.

    **Zwei offene Grenzen dieser Ausnahme, bewusst hingenommen statt
    beseitigt (Aufgabe 7, Nachbesserung Fix 1):**
    - Sie sieht den Laufzeitwert nicht: ein Zaehler, der noch NIE gemessen
      wurde (Matter liefert dafuer `null`, kein Zahlenwert), wird trotzdem
      auf ANALOG gehoben und gilt danach als exportiert - ein
      Loxone-Eingang, der nie einen Wert traegt, eine Checkbox in der
      Oberflaeche, die luegt. Zur Laufzeit passiert dabei nichts Falsches:
      `to_loxone_value` leitet unabhaengig aus dem echten Wert ab und
      liefert dafuer weiterhin `None` (Spec 6.6), es fliesst also nie ein
      erfundener Wert - nur die erzeugte Vorlage bekommt einen Eingang zu
      viel. Erwogen und verworfen wurde, die Ausnahme deswegen ganz zu
      streichen: der Stecker (`tests/fixtures/nodes/ikea_grillplats_plug.json`)
      meldet fuer `energy_imported` (Cluster 145, Element 1) bereits einen
      echten Wert (0, kein `null`) - eine frische Registrierung stuft dieses
      Signal ueber genau diese Ausnahme als ANALOG und damit exportiert ein
      (siehe `lookup`). Ohne die Ausnahme bliebe die migrierte
      Exportability beim vor-Aufgabe-6-Wert NONE stehen (siehe
      `_pretend_unnamed_profile` in `test_store_migration.py`), und die
      Gegenprobe
      (`test_the_migration_reproduces_the_functional_export_counts_of_both_fixtures`)
      fiele fuer den Stecker von 5 auf 4 - also just die Zahl, die dieses
      Modul selbst als Beleg fuer die Ersatzregel nennt. Streichen wuerde
      damit ein bewiesen richtiges Verhalten (der Stecker) gegen ein rein
      hypothetisches, an keinem der beiden eingecheckten Abbilder
      beobachtbares (der nie gemessene Zaehler) eintauschen - die Ausnahme
      bleibt deshalb bestehen, mit dieser Grenze hier offen benannt statt
      stillschweigend in Kauf genommen. Siehe
      `test_a_never_measured_energy_counter_is_still_promoted_by_the_field_number_exception`.
    - Sie ist hart auf ANALOG verdrahtet, nicht auf die tatsaechliche
      Struktur-Semantik: beide heute bekannten Faelle
      (`energy_imported`/`energy_exported`, `EnergyMeasurementStruct.energy`)
      sind laut Matter-Spezifikation numerisch - ANALOG ist also heute in
      jedem Fall richtig. Traegt `clusters.yaml` kuenftig ein `field:` auf
      ein boolesches Strukturelement ein, laege diese Zeile falsch (DIGITAL
      waere richtig): die Tabelle kennt bislang keinen Typ je Feld, nur die
      Feldnummer selbst. Unbelegt, weil es noch keinen solchen Fall gibt -
      wer den ersten Fall dieser Art anlegt, muss diese Stelle mitdenken.

    **Was diese Migration sonst NICHT kann:**
    - Jenseits der Feldnummer-Ausnahme oben wird `exportability` NICHT neu
      klassifiziert. Eine Korrektur in `clusters.yaml`, die aus einem
      anderen Grund die Klassifizierung eines Werts aendern wuerde, erreicht
      ein schon gespeichertes Signal deshalb weiterhin erst beim naechsten
      echten Neu-Einlesen des Geraets (`register_signals`), nicht durch
      diese Migration. `title` und `unit` sind davon nicht betroffen: beide
      haengen in `profiles.table.lookup` nie vom Laufzeitwert ab.
    - Ein vom Nutzer ueber `set_title` personalisierter Titel wird von
      dieser einmaligen Migration ebenfalls ueberschrieben - die Datenbank
      unterscheidet nicht, ob ein gespeicherter Titel der automatische
      Vorgabewert ist oder eine bewusste Umbenennung.
    - Dasselbe gilt fuer `exported`: laut `register_signals`/`set_exported`
      gehoert der Wert ab dem ersten Bekanntsein eines Signals dem Nutzer -
      ein bewusst umgeschaltetes Signal bleibt bei jedem weiteren
      Neu-Einlesen unangetastet (siehe dort). Diese Migration kann das
      nicht einhalten: sie schreibt `exported` fuer JEDE Zeile einmalig neu,
      weil das Schema keine Spalte kennt, die "vom Nutzer gesetzt" von
      "automatischer Vorgabewert" unterscheidet - innerhalb des
      Aufgabenzuschnitts unvermeidbar. Ein Betreiber, der zwoelf Signale
      von Hand freigeschaltet (oder abgeschaltet) hat, verliert diese
      Auswahl bei diesem einen Update. Entwarnung: der Laufzeitpfad
      (`loxone.runtime`) filtert beim Senden NICHT auf `exported` - eine
      bestehende UDP-Verdrahtung stirbt dadurch nicht. Betroffen ist erst
      eine NEU erzeugte Loxone-Vorlage nach diesem Update
      (`export.signals.to_inputs` filtert dort auf `exported`, siehe dessen
      Docstring).
    - Ein Verwaltungs-Endpunkt jenseits von Endpunkt 0 (aus Matters Sicht
      nicht ausgeschlossen, an den beiden bislang bekannten echten Geraeten
      aber nie beobachtet) wird von der Ersatzregel nicht erkannt; ein
      solches Geraet bliebe nach der Migration grosszuegiger exportiert, als
      es eine echte Neuregistrierung waere. Das ist aber NICHT die einzige
      Abweichung von `device_types_by_endpoint`, und nicht immer die
      grosszuegigere Richtung - "hoechstens grosszuegiger" waere hier eine
      falsche Zusicherung, siehe die beiden folgenden Punkte.
    - Die Ersatzregel schliesst von "Cluster 47 (PowerSource) liegt auf
      Endpunkt 0" auf "PowerSource ist dort als Geraetetyp deklariert" -
      `is_functional` fragt aber nach der Geraetetyp-Deklaration, nicht nach
      blosser Cluster-Anwesenheit. Bei einem Taster, dessen Descriptor auf
      Endpunkt 0 tatsaechlich nur Root Node nennt (der PowerSource-Cluster
      liegt zwar vor, ist dort aber nicht als Geraetetyp gemeldet): eine
      frische Registrierung exportiert 16 Signale, diese Migration 17 - in
      diesem Fall IST die Migration grosszuegiger.
    - Meldet ein Abbild fuer Endpunkt 0 gar keine `DeviceTypeList`, behandelt
      `is_functional` bei einer frischen Registrierung Endpunkt 0 wie einen
      gewoehnlichen NUTZ-Endpunkt (`profiles.relevance.device_types_by_endpoint`:
      ein Endpunkt ohne Descriptor-Eintrag taucht dort gar nicht auf, und
      eine fehlende Deklaration schliesst layer 2 in `is_functional` damit
      aus). Diese Migration nimmt dagegen IMMER Endpunkt 0 = Root Node an,
      egal ob ein Abbild das je bestaetigt hat. Beispiel: frisch 108
      exportierte Signale, migriert 17 - hier ist die Migration deutlich
      WENIGER grosszuegig, die entgegengesetzte Richtung der beiden Punkte
      oben.
    - Scheitert die Neuableitung fuer eine einzelne Zeile (z. B. ein
      unerwarteter Wert in `kind`), bleibt GENAU DIESE Zeile unveraendert
      stehen - kein Abbruch der gesamten Migration, keine halb migrierte
      Datenbank (Entwurf 8, siehe
      `test_an_unparseable_row_survives_the_migration_untouched`).
    """
    rows = db.execute(
        "SELECT device_id, key, endpoint, cluster_id, element_id, kind, exportability FROM signal"
    ).fetchall()

    # Cluster-IDs je (Geraet, Endpunkt 0) - die einzige Grundlage, die zur
    # Migrationszeit ueber ein Geraet ueberhaupt bekannt ist (siehe Docstring
    # oben). Nur Endpunkt 0 wird gesondert betrachtet, siehe dort.
    clusters_on_endpoint0: dict[int, set[int]] = {}
    for row in rows:
        if int(row["endpoint"]) == 0:
            clusters_on_endpoint0.setdefault(int(row["device_id"]), set()).add(
                int(row["cluster_id"])
            )

    # Die Ersatzregel selbst, einmal je Geraet gebaut statt je Zeile neu:
    # Endpunkt 0 gilt immer als Root Node, PowerSource zusaetzlich, sobald
    # Endpunkt 0 ueberhaupt ein Signal dieses Clusters traegt (siehe
    # Docstring oben).
    device_types_by_device: dict[int, dict[int, frozenset[int]]] = {}
    for device_id, endpoint0_clusters in clusters_on_endpoint0.items():
        declared = {ROOT_NODE_DEVICE_TYPE}
        for cluster_id, required_type in UTILITY_ENDPOINT_KEEP_CLUSTERS.items():
            if cluster_id in endpoint0_clusters:
                declared.add(required_type)
        device_types_by_device[device_id] = {0: frozenset(declared)}

    for row in rows:
        try:
            ref = SignalRef(
                int(row["endpoint"]),
                int(row["cluster_id"]),
                int(row["element_id"]),
                SignalKind(row["kind"]),
            )
            # `value=None`: `lookup` braucht den Laufzeitwert nur fuer die
            # Exportability-Klassifizierung - `title` und `unit` haengen in
            # `profiles.table.lookup` nie von `value` ab (siehe Docstring
            # oben).
            profile = lookup(ref, None)
            # Kein Eintrag noetig, wenn dieses Geraet gar kein Signal auf
            # Endpunkt 0 hat - `is_functional` behandelt einen fehlenden
            # Endpunkt darin ohnehin wie einen gewoehnlichen Nutz-Endpunkt.
            device_types = device_types_by_device.get(int(row["device_id"]), {})
            exportability = Exportability(row["exportability"])
            if struct_field(ref) is not None:
                # Die einzige Ausnahme, in der diese Migration eine
                # Klassifizierung OHNE Laufzeitwert anhebt (siehe Docstring,
                # "Feldnummer").
                exportability = Exportability.ANALOG
            exported = int(is_exportable(exportability) and is_functional(ref, device_types))
        except (ValueError, KeyError):
            # Diese eine Zeile bleibt unveraendert (siehe Docstring, "Was
            # diese Migration sonst NICHT kann") - kein Abbruch der
            # Transaktion.
            continue
        db.execute(
            "UPDATE signal SET title = ?, unit = ?, exportability = ?, exported = ? WHERE key = ?",
            (profile.title, profile.unit, exportability.value, exported, row["key"]),
        )


# Migrationen der Reihe nach, angewandt ab der jeweils gespeicherten Version -
# Erweiterung fuer eine spaetere Schema-Aenderung: einfach anhaengen, mit der
# naechsten Versionsnummer als Schluessel.
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
    3: _migrate_to_v3,
}


def _migrate(db: sqlite3.Connection) -> None:
    """Bringt eine geoeffnete Datenbank auf `_SCHEMA_VERSION`.

    Laeuft in einer Transaktion: scheitert eine Migration, bleibt die
    Datenbank unveraendert (Review-Fix Important #1) - `ALTER TABLE ADD
    COLUMN` ist in SQLite vollstaendig transaktional, weshalb ein expliziter
    Rollback die schon ausgefuehrten Schritte dieses Laufs wieder rueckgaengig
    macht. `PRAGMA user_version` selbst ist ebenfalls Teil dieser Transaktion
    und wird deshalb nur bei vollstaendigem Erfolg auf `_SCHEMA_VERSION`
    gesetzt - ein Absturz mitten in einer Migration hinterlaesst also nicht
    eine halb angewandte Aenderung unter einer bereits erhoehten Version, die
    ein spaeterer Start faelschlich fuer erledigt haelt.

    Bereits auf dem neuesten Stand (der Normalfall bei jedem Start ausser dem
    allerersten nach einer Schema-Aenderung): kein Schreibzugriff, echtes
    No-op.
    """
    version = int(db.execute("PRAGMA user_version").fetchone()[0])
    if version >= _SCHEMA_VERSION:
        return
    db.execute("BEGIN")
    try:
        for target_version in range(version + 1, _SCHEMA_VERSION + 1):
            _MIGRATIONS[target_version](db)
        db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
    except BaseException:
        db.rollback()
        raise
    else:
        db.commit()


@dataclass(frozen=True)
class StoredSignal:
    key: str
    ref: SignalRef
    title: str
    unit: str
    exportability: Exportability
    # Beide Felder unten sind bereits Spalten der `signal`-Tabelle - kein
    # Bruch der Schluessel-Opazitaet aus Spec 6.2 (der Schluessel selbst
    # bleibt unangetastet), sondern nur ihre Offenlegung im Dataclass.
    #
    # device_id (Task 2, Phase 5): die Geraete-API loest ein Signal ueber
    # `signal_by_key` OHNE Geraete-Kontext im Pfad auf (`PATCH
    # /api/signals/{key}`) und braucht trotzdem die zugehoerige device_id,
    # um z. B. einen Live-Wert nachzuschlagen. Den device_id aus dem
    # Schluessel-String zu parsen waere ein Bruch von "Keys sind opak"
    # (Spec 6.2) durch die Hintertuer - store.py kennt die device_id ohnehin
    # aus der Zeile, sie muss nur mitgegeben werden.
    device_id: int
    # exported (Spec 5, Datenmodell): ob dieses Signal in den naechsten
    # Export einfliessen soll - vom Nutzer umschaltbar (`PATCH
    # /api/signals/{key}`), unabhaengig von `exportability`. Ein technisch
    # nicht abbildbares Signal (siehe Spec 6.6) hat hier nie eine editierbare
    # Checkbox, siehe `exportable` in `api.models.SignalOut`.
    exported: bool


@dataclass(frozen=True)
class StoredCommand:
    key: str
    slug: str
    node_id: int
    endpoint: int
    cluster_id: int
    command_id: int
    takes_value: bool
    # device_id (Review-Fix Important #1, 2026-09-02): dieselbe Begruendung
    # wie bei `StoredSignal.device_id` oben - `resolve_command` loest einen
    # Kommando-Schluessel OHNE Geraete-Kontext im Pfad auf (`POST
    # /api/commands/{key}`), und die aufrufende Route braucht trotzdem die
    # device_id, um zu pruefen, ob das zugehoerige Geraet noch aktiv ist.
    device_id: int


@dataclass(frozen=True)
class StoredDevice:
    """Eine Zeile aus `device` (Spec 5) - fuer die Geraete-API (Task 2, Phase 5).

    Traegt bewusst keinen `online`-Status: Erreichbarkeit ist Laufzeit-
    Zustand (`Runtime`, gespeist aus Matter-Subscriptions), keine
    gespeicherte Eigenschaft. Ein hier eingefrorenes `online`-Feld koennte
    beim Neustart der Bruecke veraltet sein, bis die naechste Subscription
    eintrifft.
    """

    id: int
    node_id: int
    unique_id: str
    label: str
    # exported_at/updated_at (Task 5, Phase 5) - Grundlage fuer `GET
    # /api/export/status`. Beide sind ISO-8601-Zeitstempel als Text, `None`
    # bedeutet "noch nie exportiert" bzw. "seit der Registrierung nicht mehr
    # angefasst" (siehe `_migrate_to_v2` fuer den Fall einer Alt-Datenbank).
    # `updated_at` ist absichtlich grob: es unterscheidet nicht, WAS sich am
    # Geraet geaendert hat (Label, ein Signaltitel, eine neu entdeckte
    # Signal-Liste, ...), nur DASS sich seit dem letzten Export etwas
    # geaendert haben koennte - fuer "seither geaendert: ja/nein" reicht das.
    exported_at: str | None
    updated_at: str | None


class UnknownCommandError(KeyError):
    """`KeyError.__str__` haengt die Nachricht in `repr()` ein, wodurch
    `str(exc)` zusaetzliche Anfuehrungszeichen um den ganzen deutschen Text
    legt — Task 6 macht daraus einen HTTP-Fehlerkoerper, dem die Klammerung
    nicht anzusehen sein soll. Die Unterklasse gibt die Nachricht
    unveraendert zurueck; `pytest.raises(KeyError, ...)` faengt sie weiterhin,
    da sie von `KeyError` erbt."""

    def __str__(self) -> str:
        return str(self.args[0])


class UnknownDeviceError(KeyError):
    """Wie `UnknownCommandError`, fuer ein unbekanntes oder bereits
    entferntes (`forget_device`) Geraet - dieselbe Begruendung: die
    Geraete-API (Task 2) macht daraus einen HTTP-404-Koerper, der die
    `repr()`-Anfuehrungszeichen von `KeyError.__str__` nicht tragen soll."""

    def __str__(self) -> str:
        return str(self.args[0])


class Store:
    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()
        _migrate(self._db)

    def close(self) -> None:
        self._db.close()

    def check_writable(self) -> None:
        """Prueft, ob die Datenbank JETZT tatsaechlich beschreibbar ist -
        nicht nur laut Dateisystem-Bits, sondern durch einen echten,
        sofort zurueckgerollten Schreibversuch. Wirft (typischerweise
        `sqlite3.OperationalError`) bei einer schreibgeschuetzten Ablage,
        einer vollen Platte oder einer exklusiv durch einen anderen Prozess
        gesperrten Datenbank; aendert bei Erfolg nichts an den Daten.

        Fuer den Systemcheck der Diagnose (Spec 10.5, siehe
        `api.diagnostics._check_store`) - der einzige Aufrufer bislang.

        **Vorab: eine eventuell schon offene, implizite Transaktion wird
        zurueckgerollt (Review-Fix Important, 2026-09-02).** Ein paar
        schreibende Methoden dieser Klasse (`rename_device`,
        `mark_exported`, `set_title`, `set_exported`) legen kein eigenes
        try/except um ihr `UPDATE ...` plus `commit()` - anders als z. B.
        `register_signals`, das bei `ValueError`/`sqlite3.Error`
        ausdruecklich zurueckrollt. Scheitert dort das `UPDATE` selbst oder
        sogar erst das `commit()` (z. B. volle Platte), bleibt die von
        Python VOR dem `UPDATE` automatisch eroeffnete Transaktion auf
        dieser Verbindung offen. Ein zweites, direkt darauf folgendes
        `BEGIN IMMEDIATE` wuerde dann IMMER mit `sqlite3.OperationalError:
        cannot start a transaction within a transaction` scheitern -
        unabhaengig davon, ob die Datenbank inzwischen wieder beschreibbar
        ist. Ohne die Behandlung hier wuerde der Systemcheck genau diesen
        Fall faelschlich als "nicht beschreibbar" melden, obwohl die
        Datenbank selbst in Ordnung sein kann.

        Das Zurueckrollen ist hier unbedenklich: jede Store-Instanz gehoert
        genau einem Thread und einer Event-Loop, und jede schreibende
        Methode ist rein synchron - sie haengt nie mitten in ihrer eigenen
        Transaktion an einem `await`. Eine zum Zeitpunkt DIESES Aufrufs
        vorgefundene offene Transaktion kann deshalb nie eine tatsaechlich
        noch laufende, legitime Transaktion sein - sie ist immer der Rest
        eines bereits fehlgeschlagenen, nie committeten Schreibversuchs,
        dessen Ausnahme schon an dessen eigenen Aufrufer weitergereicht
        wurde. Sie zurueckzurollen verwirft deshalb garantiert keine
        erfolgreich geschriebenen Daten."""
        if self._db.in_transaction:
            self._db.rollback()
        self._db.execute("BEGIN IMMEDIATE")
        self._db.rollback()

    @staticmethod
    def _now() -> str:
        """Duenne Bruecke zu `loxmatter.timestamps.now_iso` (Review-Fix
        Minor, 2026-09-02 - siehe dort fuer die Begruendung, warum diese
        Funktion nicht mehr eigenstaendig implementiert ist). Bleibt als
        eigene Methode erhalten, weil `self._now()` bereits an vielen
        Stellen dieser Klasse verdrahtet ist."""
        return now_iso()

    def _device_identity(self, snapshot: NodeSnapshot) -> str:
        """Faellt auf die Node-ID zurueck: manche Geraete melden keine UniqueID (Spec 7.2)."""
        return snapshot.unique_id or f"node:{snapshot.node_id}"

    def register_device(self, snapshot: NodeSnapshot) -> int:
        identity = self._device_identity(snapshot)
        row = self._db.execute(
            "SELECT id FROM device WHERE unique_id = ? AND active = 1", (identity,)
        ).fetchone()
        if row is not None:
            return int(row["id"])

        label = f"{snapshot.vendor_name} {snapshot.product_name}".strip() or identity
        cur = self._db.execute(
            "INSERT INTO device (unique_id, node_id, label, udp_port, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (identity, snapshot.node_id, label, DEFAULT_UDP_PORT, self._now()),
        )
        self._db.commit()
        device_id = cur.lastrowid
        assert device_id is not None
        return int(device_id)

    def forget_device(self, device_id: int) -> None:
        """Markiert ein Geraet als entfernt. Die id bleibt vergeben (Spec 6.2)."""
        self._db.execute("UPDATE device SET active = 0 WHERE id = ?", (device_id,))
        self._db.commit()

    def udp_port(self, device_id: int) -> int:
        row = self._db.execute("SELECT udp_port FROM device WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            raise KeyError(f"unbekanntes Geraet {device_id}")
        return int(row["udp_port"])

    @staticmethod
    def _as_device(row: sqlite3.Row) -> StoredDevice:
        return StoredDevice(
            id=int(row["id"]),
            node_id=int(row["node_id"]),
            unique_id=str(row["unique_id"]),
            label=str(row["label"]),
            exported_at=row["exported_at"],
            updated_at=row["updated_at"],
        )

    def devices(self) -> list[StoredDevice]:
        """Alle aktiven Geraete (Task 2, Phase 5) - fuer `GET /api/devices`.

        Ein entferntes Geraet (`forget_device`) taucht hier nicht mehr auf,
        genau wie bei `device_id_for_node`."""
        rows = self._db.execute("SELECT * FROM device WHERE active = 1 ORDER BY id").fetchall()
        return [self._as_device(r) for r in rows]

    def device(self, device_id: int) -> StoredDevice:
        """Ein einzelnes aktives Geraet - `UnknownDeviceError`, wenn es nie
        registriert wurde oder inzwischen entfernt ist."""
        row = self._db.execute(
            "SELECT * FROM device WHERE id = ? AND active = 1", (device_id,)
        ).fetchone()
        if row is None:
            raise UnknownDeviceError(f"unbekanntes Geraet {device_id}")
        return self._as_device(row)

    def rename_device(self, device_id: int, label: str) -> None:
        """Setzt das Label eines Geraets (`PATCH /api/devices/{device_id}`).

        Wie `set_title` ohne vorherige Existenzpruefung - der Aufrufer (die
        API-Route) prueft ueber `device()` selbst und meldet ein unbekanntes
        Geraet als 404, bevor diese Methode ueberhaupt aufgerufen wird.

        Setzt `updated_at` (Task 5, Phase 5): eine Umbenennung landet im
        naechsten Export als neuer `Title` in der Vorlage - `GET
        /api/export/status` soll das Geraet danach als "seither geaendert"
        fuehren, auch wenn kein Signal betroffen ist."""
        self._db.execute(
            "UPDATE device SET label = ?, updated_at = ? WHERE id = ?",
            (label, self._now(), device_id),
        )
        self._db.commit()

    def mark_exported(self, device_id: int) -> None:
        """Setzt `exported_at` auf jetzt (Task 5, Phase 5).

        Aufgerufen sowohl von `api.export.download` als auch von `cli.py`s
        `export`-Kommando - beide schreiben in dieselbe Datenbank (siehe
        Modul-Docstring von `api/export.py`), und `GET /api/export/status`
        soll "wann zuletzt exportiert" unabhaengig davon beantworten, ueber
        welchen der beiden Wege der letzte Export lief. Ohne diesen Aufruf
        im CLI-Kommando zeigte die WebUI nach einem `loxmatter export`
        weiterhin "nie exportiert" an."""
        self._db.execute("UPDATE device SET exported_at = ? WHERE id = ?", (self._now(), device_id))
        self._db.commit()

    def device_id_for_node(self, node_id: int) -> int | None:
        """Bildet eine Matter-Node-ID auf die zugehoerige, stabile `device_id` ab.

        Fuer die Laufzeit (Task 8): eine eingehende Subscription von
        matter-server traegt nur die Node-ID, aber die Signalschluessel
        haengen an der `device_id` (siehe Modul-Docstring - eine Node-ID kann
        sich aendern, die `device_id` nie). `None`, wenn kein aktives Geraet
        mit dieser Node-ID bekannt ist, etwa weil es noch nie exportiert oder
        inzwischen entfernt (`forget_device`) wurde - eine Node-ID eines
        entfernten Geraets darf nicht auf dessen alte, inaktive `device_id`
        zeigen.
        """
        row = self._db.execute(
            "SELECT id FROM device WHERE node_id = ? AND active = 1", (node_id,)
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def _existing_keys(self, device_id: int) -> set[str]:
        rows = self._db.execute(
            "SELECT key FROM signal WHERE device_id = ?", (device_id,)
        ).fetchall()
        return {str(r["key"]) for r in rows}

    def _assign_key(self, device_id: int, ref: SignalRef, slug: str, taken: set[str]) -> str:
        """Vergibt einen neuen, noch nicht belegten Schluessel fuer ``ref``.

        Der Normalfall ist ``d<device_id>_<endpoint>_<slug>``. Kollidiert das
        mit einem bereits vergebenen Schluessel eines *anderen* Signals
        desselben Geraets, wird die Element-ID angehaengt — das ist der
        einzige zusaetzliche Diskriminator, der garantiert pro Endpoint
        eindeutig ist (siehe Modul-Docstring, Spec 6.2).
        """
        base = f"d{device_id}_{ref.endpoint}_{slug}"
        if base not in taken:
            return base
        disambiguated = f"{base}_{ref.element_id}"
        if disambiguated in taken:
            raise ValueError(
                f"Schluessel-Kollision fuer Geraet {device_id}: {disambiguated!r} bereits vergeben"
            )
        return disambiguated

    def register_signals(self, device_id: int, snapshot: NodeSnapshot) -> list[StoredSignal]:
        """Legt neue Signale an; bekannte behalten Schluessel und Titel, aber
        `unit` und `exportability` werden bei jedem Aufruf neu bestimmt.

        Spec 6.2 verlangt Unveraenderlichkeit ausdruecklich nur fuer den
        Schluessel — nicht fuer `unit` oder `exportability`. Wuerden diese
        beim ersten Einlernen eingefroren, bliebe ein Signal, das nur meldet,
        weil gerade kein Kommissionierungsfenster offen ist, oder ein
        Attribut wie `StartUpOnOff`, das ein Geraet erst spaeter befuellt,
        fuer immer bei `exportability=none` stehen — und eine Korrektur in
        `clusters.yaml` erreichte ein schon gespeichertes Signal nie. Die
        einzige Abhilfe waere das Loeschen der ganzen Datenbank, was jeden
        Schluessel zerstoert. `title` dagegen bleibt unangetastet, sobald
        `set_title` es einmal gesetzt hat — ab dann gehoert es dem Nutzer
        (siehe `test_key_survives_a_title_change`). Nur beim Anlegen (Zweig
        unten ohne `existing`) wird die Titelspalte einmalig aus
        `profile.title` befuellt — bei einem generischen Slug ist das der
        Klartextname aus dem SDK-Katalog (`profiles.table.lookup`,
        `profiles.catalog.element_name`), sonst derselbe Wert wie `slug`.

        Laeuft als eine Transaktion: scheitert die Schluesselvergabe fuer ein
        einzelnes neues Signal (siehe `_assign_key`), wird die gesamte
        Registrierung zurueckgerollt statt das Geraet mit einer Teilmenge
        seiner Signale zu belassen. Absichtlich kein `INSERT OR IGNORE` — das
        wuerde eine echte Schluessel-Kollision nicht melden, sondern das
        zweite Signal stillschweigend verwerfen (siehe Modul-Docstring).
        """
        taken = self._existing_keys(device_id)
        device_types = device_types_by_endpoint(snapshot)
        try:
            for ref in extract_signals(snapshot):
                profile = lookup(ref, snapshot.attributes.get(ref.path))
                existing = self._db.execute(
                    "SELECT key FROM signal WHERE device_id = ? AND endpoint = ? AND cluster_id = ?"
                    " AND element_id = ? AND kind = ?",
                    (device_id, ref.endpoint, ref.cluster_id, ref.element_id, ref.kind.value),
                ).fetchone()
                if existing is not None:
                    self._db.execute(
                        "UPDATE signal SET unit = ?, exportability = ? WHERE key = ?",
                        (profile.unit, profile.exportability.value, existing["key"]),
                    )
                    continue

                key = self._assign_key(device_id, ref, profile.slug, taken)
                taken.add(key)
                # Zwei Fragen, zwei Antworten (Entwurf 2026-09-03, 3):
                # `is_exportable` sagt, ob der Wert ueberhaupt auf einen
                # Loxone-Eingang passt; `is_functional`, ob ihn jemand
                # standardmaessig will. Ein Thread-Funkzaehler ist das
                # erste und nicht das zweite.
                #
                # Nur beim ANLEGEN: der UPDATE-Zweig oben fasst `exported`
                # weiterhin nicht an, sobald ein Signal einmal bekannt ist -
                # ab dann gehoert der Wert dem Nutzer.
                exported = is_exportable(profile.exportability) and is_functional(ref, device_types)
                self._db.execute(
                    "INSERT INTO signal "
                    "(device_id, endpoint, cluster_id, element_id, kind, key, title, unit,"
                    " exportability, exported) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        device_id,
                        ref.endpoint,
                        ref.cluster_id,
                        ref.element_id,
                        ref.kind.value,
                        key,
                        profile.title,
                        profile.unit,
                        profile.exportability.value,
                        int(exported),
                    ),
                )
            # Geraet als "seither geaendert" markieren (Task 5, Phase 5): ein
            # neu entdecktes oder in `unit`/`exportability` korrigiertes
            # Signal soll `GET /api/export/status` erreichen, auch wenn
            # `register_signals` selbst keinen einzigen neuen Schluessel
            # vergeben hat (reines Refresh eines schon bekannten Geraets).
            self._db.execute(
                "UPDATE device SET updated_at = ? WHERE id = ?", (self._now(), device_id)
            )
        except (ValueError, sqlite3.Error):
            self._db.rollback()
            raise
        self._db.commit()
        return self.signals(device_id)

    def set_title(self, key: str, title: str) -> None:
        self._touch_owning_device(key)
        self._db.execute("UPDATE signal SET title = ? WHERE key = ?", (title, key))
        self._db.commit()

    def set_exported(self, key: str, exported: bool) -> None:
        """Setzt das Export-Flag eines Signals (`PATCH /api/signals/{key}`,
        Task 2). Wie `set_title` ohne Existenzpruefung - siehe dort."""
        self._touch_owning_device(key)
        self._db.execute("UPDATE signal SET exported = ? WHERE key = ?", (int(exported), key))
        self._db.commit()

    def _touch_owning_device(self, signal_key: str) -> None:
        """Setzt `updated_at` des Geraets, zu dem `signal_key` gehoert (Task
        5, Phase 5) - `set_title`/`set_exported` bekommen keine `device_id`
        (siehe deren Docstrings), deshalb die Unterabfrage. Ein unbekannter
        Schluessel trifft keine Zeile und bleibt ein stilles No-op, genau wie
        das anschliessende `UPDATE signal` in beiden Aufrufern - der Aufrufer
        (die API-Route) prueft Existenz bereits vorher (siehe
        `api.devices.rename_signal`)."""
        self._db.execute(
            "UPDATE device SET updated_at = ?"
            " WHERE id = (SELECT device_id FROM signal WHERE key = ?)",
            (self._now(), signal_key),
        )

    @staticmethod
    def _as_signal(row: sqlite3.Row) -> StoredSignal:
        return StoredSignal(
            key=row["key"],
            ref=SignalRef(
                row["endpoint"], row["cluster_id"], row["element_id"], SignalKind(row["kind"])
            ),
            title=row["title"],
            unit=row["unit"],
            exportability=Exportability(row["exportability"]),
            device_id=int(row["device_id"]),
            exported=bool(row["exported"]),
        )

    def signals(self, device_id: int) -> list[StoredSignal]:
        rows = self._db.execute(
            "SELECT * FROM signal WHERE device_id = ?"
            " ORDER BY endpoint, cluster_id, element_id, kind",
            (device_id,),
        ).fetchall()
        return [self._as_signal(r) for r in rows]

    def signal_by_key(self, key: str) -> StoredSignal | None:
        """Ein einzelnes Signal ueber seinen Schluessel - fuer `PATCH
        /api/signals/{key}` (Task 2), die keinen Geraete-Pfadparameter hat
        und deshalb nicht ueber `signals(device_id)` gehen kann. `None` statt
        einer Ausnahme, analog zu `device_id_for_node` - der Aufrufer
        entscheidet, ob das ein 404 ist."""
        row = self._db.execute("SELECT * FROM signal WHERE key = ?", (key,)).fetchone()
        return self._as_signal(row) if row is not None else None

    def _existing_command_keys(self, device_id: int) -> set[str]:
        rows = self._db.execute(
            "SELECT key FROM command WHERE device_id = ?", (device_id,)
        ).fetchall()
        return {str(r["key"]) for r in rows}

    def register_commands(
        self, device_id: int, commands: Sequence[DeviceCommand], node_id: int
    ) -> list[StoredCommand]:
        """Macht die exportierten Kommando-Schluessel zur Laufzeit aufloesbar.

        Ohne das schreibt der Exporter Schluessel in die Vorlage, die spaeter
        niemand zurueck auf ein Matter-Kommando abbilden kann. Der Schluessel
        wird ausschliesslich hier zusammengesetzt — der Exporter (cli.py)
        uebernimmt das Ergebnis, statt ihn ein zweites Mal selbst zu bauen.
        Zwei Stellen, die denselben Schluessel unabhaengig zusammensetzen,
        wuerden sonst auseinanderdriften, ohne dass ein Fehler es meldet.

        Ein schon bekanntes Kommando (gleiches device_id/endpoint/cluster_id/
        command_id) behaelt seinen Schluessel, aber `takes_value` und `slug`
        werden bei jedem Aufruf neu uebernommen — genau wie `register_signals`
        `unit` und `exportability` neu bestimmt. Sonst erreichte eine
        Korrektur in `clusters.yaml` (ein Kommando, das nachtraeglich einen
        Wert erwartet, oder umbenannt wird) ein schon gespeichertes Kommando
        nie, und die einzige Abhilfe waere das Loeschen der ganzen Datenbank,
        was jeden Schluessel zerstoert.

        Laeuft als eine Transaktion: scheitert die Schluesselvergabe fuer ein
        einzelnes neues Kommando, wird die gesamte Registrierung
        zurueckgerollt statt das Geraet mit einer Teilmenge seiner Kommandos
        zu belassen. Absichtlich kein `INSERT OR IGNORE` — das wuerde eine
        echte Schluessel-Kollision nicht melden, sondern das zweite Kommando
        stillschweigend verwerfen (siehe Modul-Docstring und
        `register_signals`). Anders als bei Signalen gibt es hier keine
        Ausweichstrategie ueber eine zusaetzliche ID: zwei Kommandos
        verschiedener Cluster auf demselben Endpoint mit gleichem Slug sind
        ein Fehler in `clusters.yaml`, keine ordnungsgemaesse Mehrdeutigkeit.
        """
        taken = self._existing_command_keys(device_id)
        try:
            for command in commands:
                existing = self._db.execute(
                    "SELECT key FROM command WHERE device_id = ? AND endpoint = ?"
                    " AND cluster_id = ? AND command_id = ?",
                    (device_id, command.endpoint, command.cluster_id, command.command_id),
                ).fetchone()
                if existing is not None:
                    self._db.execute(
                        "UPDATE command SET takes_value = ?, slug = ? WHERE key = ?",
                        (int(command.takes_value), command.slug, existing["key"]),
                    )
                    continue

                key = f"d{device_id}_{command.endpoint}_{command.slug}"
                if key in taken:
                    collision = self._db.execute(
                        "SELECT cluster_id, command_id FROM command"
                        " WHERE device_id = ? AND key = ?",
                        (device_id, key),
                    ).fetchone()
                    raise ValueError(
                        f"Schluessel-Kollision fuer Geraet {device_id}: Kommando "
                        f"(cluster_id={command.cluster_id}, command_id={command.command_id}) "
                        f"und (cluster_id={collision['cluster_id']}, "
                        f"command_id={collision['command_id']}) teilen sich den "
                        f"Schluessel {key!r}"
                    )
                taken.add(key)
                self._db.execute(
                    "INSERT INTO command "
                    "(device_id, node_id, endpoint, cluster_id, command_id, key, slug,"
                    " takes_value) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        device_id,
                        node_id,
                        command.endpoint,
                        command.cluster_id,
                        command.command_id,
                        key,
                        command.slug,
                        int(command.takes_value),
                    ),
                )
            # Wie am Ende von `register_signals` (Task 5, Phase 5): auch ein
            # reines Refresh ohne neuen Schluessel zaehlt als "seither
            # geaendert", z. B. wenn `clusters.yaml` einem Kommando
            # nachtraeglich `takes_value` zuweist.
            self._db.execute(
                "UPDATE device SET updated_at = ? WHERE id = ?", (self._now(), device_id)
            )
        except (ValueError, sqlite3.Error):
            self._db.rollback()
            raise
        self._db.commit()
        return self.commands(device_id)

    def commands(self, device_id: int) -> list[StoredCommand]:
        rows = self._db.execute(
            "SELECT * FROM command WHERE device_id = ? ORDER BY endpoint, cluster_id, command_id",
            (device_id,),
        ).fetchall()
        return [self._as_command(r) for r in rows]

    def resolve_command(self, key: str) -> StoredCommand:
        row = self._db.execute("SELECT * FROM command WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise UnknownCommandError(f"unbekannter Kommando-Schluessel {key!r}")
        return self._as_command(row)

    @staticmethod
    def _as_command(row: sqlite3.Row) -> StoredCommand:
        return StoredCommand(
            key=row["key"],
            slug=row["slug"],
            node_id=int(row["node_id"]),
            endpoint=int(row["endpoint"]),
            cluster_id=int(row["cluster_id"]),
            command_id=int(row["command_id"]),
            takes_value=bool(row["takes_value"]),
            device_id=int(row["device_id"]),
        )
