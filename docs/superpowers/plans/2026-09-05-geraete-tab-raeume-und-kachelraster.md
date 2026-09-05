# Geräte-Tab: Räume, Kategorien und Kachelraster — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Geräte-Tab bekommt Räume, aus Matter abgeleitete Gerätekategorien und ein mehrspaltiges Kachelraster, damit er auch bei 20+ Geräten bedienbar bleibt.

**Architecture:** Zwei neue Spalten an `device` (Migration v7) tragen den frei gewählten Raum und die rohen Matter-Gerätetypen. Die Kategorie wird daraus bei jedem Lesen abgeleitet (neues Modul `profiles/categories.py`) statt gespeichert. Die API erweitert bestehende Routen und bekommt genau eine neue (`POST /api/rooms/rename`). Das gesamte Filtern, Gruppieren, Sortieren und Suchen passiert clientseitig in `app.js` über die Liste, die `GET /api/devices` ohnehin liefert.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite (`sqlite3`, Schema-Versionierung über `PRAGMA user_version`), Alpine.js (vendored), pytest / pytest-asyncio, httpx2.

**Spec:** `docs/superpowers/specs/2026-09-05-geraete-tab-raeume-und-kachelraster-design.md` — bei jedem Zweifel gilt die Spec, nicht dieser Plan.

## Global Constraints

- **Entwickler-Prosa auf Deutsch.** Docstrings, Kommentare und Commit-Nachrichten in dichtem, begründendem Deutsch, das das *Warum* nennt. Ausnahme: der GPL-Kopf jeder Quelldatei bleibt im englischen FSF-Wortlaut.
- **Jeder nutzersichtbare Text läuft über `i18n.t()`** mit `en`- **und** `de`-Eintrag in `src/loxmatter/i18n/strings.yaml`. Kein hartkodierter deutscher Text in `index.html`, `app.js` oder API-Fehlermeldungen.
- **Schlüssel in `strings.yaml` sind flach und punktiert** (`web.devices.room_all`), keine verschachtelte YAML-Struktur.
- **`web.*`-Schlüssel dürfen keine `{platzhalter}` enthalten**, die serverseitig nicht befüllt werden: `api/language.py:_web_strings()` ruft `i18n.t(key)` ohne Werte auf, ein Platzhalter wirft dort `KeyError` und reißt die **gesamte** `GET /api/i18n`-Antwort mit. Platzhalter werden clientseitig von `t(key, {…})` in `app.js` eingesetzt — dafür muss der Schlüssel serverseitig trotzdem ohne `KeyError` auflösen. Prüfen: nach jeder Schlüsseländerung `pytest tests/api/test_language.py -q`.
- **Kommandos laufen mit `uv`**: `uv run pytest …`, `uv run ruff check .`, `uv run mypy src`.
- **Keine externen Frontend-Abhängigkeiten.** Icons sind inline-SVG-`<symbol>`s in `index.html`, keine Icon-Bibliothek, kein CDN — die Oberfläche läuft offline.
- **Migration:** `_SCHEMA_VERSION` wird auf `7` gesetzt, `_migrate_to_v7` in `_MIGRATIONS` eingetragen. Neue Spalten immer über `_add_column_if_missing`, nie über nacktes `ALTER TABLE`.
- **`set_room` und `backfill_device_types` fassen `updated_at` NICHT an.** Der Raum landet in keiner Exportvorlage; ein Aufräumen der Raumzuordnung darf kein Gerät als „geändert seit Export" markieren. `rename_device` behält sein `updated_at` unverändert.

---

## File Structure

**Neu:**
- `src/loxmatter/profiles/categories.py` — Kategorien, Rang, Matter-Typ-Zuordnung, `category_for()`. Liegt neben `relevance.py`, weil es dieselbe Quelle (`device_types_by_endpoint`) auswertet.
- `tests/profiles/test_categories.py` — Tabelle und Primärtyp-Regel.
- `tests/api/test_rooms.py` — die neue Raum-Route.

**Geändert:**
- `src/loxmatter/model/store.py` — Schema v7, `StoredDevice`, `_as_device`, `register_device`, `set_room`, `rename_room`, `backfill_device_types`, JSON-Kodierung der Gerätetypen.
- `src/loxmatter/api/models.py` — `DeviceOut` (+3 Felder), `DeviceRename` → `DevicePatch`, `CommissionRequest` (+`room`), neu `RoomRename`.
- `src/loxmatter/api/devices.py` — `_device_out`, PATCH-Route, Commission-Route, neue Rooms-Route.
- `src/loxmatter/cli.py` — `backfill_device_types` beim Brückenstart.
- `src/loxmatter/i18n/strings.yaml` — neue `web.devices.*`, `web.devices.category.*`, `api.devices.*`-Schlüssel.
- `src/loxmatter/web/app.js` — Raum-/Such-/Sortierlogik, Leitwert, Raum speichern, Raum umbenennen, Einlernen mit Raum.
- `src/loxmatter/web/index.html` — Raumleiste, Kachel-Umbau (Mix 2), Einlern-Feld, acht Kategorie-Icons.
- `src/loxmatter/web/style.css` — Kachelraster, Kopfzeile mit Leitwert, Raumleiste, Fußzeile.
- `tests/model/test_store.py`, `tests/model/test_store_migration.py`, `tests/api/test_devices.py`, `tests/api/test_web.py` — neue Tests.

---

### Task 1: Migration v7 — die zwei Spalten

**Files:**
- Modify: `src/loxmatter/model/store.py` (Kopfkommentar zu `_SCHEMA_VERSION` ab Zeile 73, `_SCHEMA_VERSION` Zeile 100, `_SCHEMA` Zeile 103-112, `_MIGRATIONS` Zeile 576-583, `StoredDevice` Zeile 681-707, `_as_device` Zeile 838-847)
- Test: `tests/model/test_store_migration.py`

**Interfaces:**
- Consumes: nichts.
- Produces: `StoredDevice.room: str | None`, `StoredDevice.device_types: dict[int, frozenset[int]] | None`, Modulfunktionen `_encode_device_types(Mapping[int, frozenset[int]]) -> str` und `_decode_device_types(str | None) -> dict[int, frozenset[int]] | None`, Migrationsfunktion `_migrate_to_v7(sqlite3.Connection) -> None`.

- [ ] **Step 1: Write the failing test**

An `tests/model/test_store_migration.py` anhängen (die Datei importiert `sqlite3`, `load` und `user_version` bereits — am Kopf der Datei nachsehen und nichts doppelt importieren):

```python
def test_migration_to_v7_adds_room_and_device_types_as_null(tmp_path):
    """Eine Bestandsdatenbank auf Version 6 bekommt beide Spalten per
    Migration. Kein Backfill: `room = NULL` bedeutet "Ohne Raum", genau wie
    bei einem frisch eingelernten Geraet ohne Raumwahl, und
    `device_types = NULL` bedeutet "noch nicht nachgetragen" - dafuer ist
    `backfill_device_types` beim Bruueckenstart zustaendig, nicht die
    Migration (siehe Entwurf 3.4)."""
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
        assert user_version(path) == 7
        device = store.device(device_id)
        assert device.room is None
        assert device.device_types is None
    finally:
        store.close()


def test_a_fresh_database_survives_the_v7_migration_without_duplicate_column(tmp_path):
    """Eine frisch angelegte Datenbank hat beide Spalten bereits durch
    `_SCHEMA`. `_add_column_if_missing` muss das erkennen - sonst scheiterte
    der allererste Start mit "duplicate column name", dieselbe Falle, gegen
    die schon `_migrate_to_v1` abgesichert ist."""
    path = tmp_path / "neu.sqlite"
    store = Store(path)
    store.close()

    db = sqlite3.connect(str(path))
    db.execute("PRAGMA user_version = 6")
    db.commit()
    db.close()

    store = Store(path)
    try:
        assert user_version(path) == 7
    finally:
        store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/model/test_store_migration.py -k v7 -v`
Expected: FAIL — `sqlite3.OperationalError: no such column: room` beim `DROP COLUMN`, bzw. `AttributeError: 'StoredDevice' object has no attribute 'room'`.

- [ ] **Step 3: Schema und Version anheben**

In `src/loxmatter/model/store.py`: die `device`-Tabelle in `_SCHEMA` um zwei Spalten erweitern:

```python
CREATE TABLE IF NOT EXISTS device (
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
```

`_SCHEMA_VERSION = 6` wird zu `_SCHEMA_VERSION = 7`, und an den Kopfkommentar darüber (er zählt jede Version einzeln auf) kommt ein Absatz:

```python
# Version 7 (Entwurf Geraete-Tab, 2026-09-05) fuegt `device.room` und
# `device.device_types` hinzu, siehe `_migrate_to_v7` - kein Backfill fuer
# beide, aber aus zwei verschiedenen Gruenden: `room = NULL` IST die
# richtige Bedeutung ("Ohne Raum"), waehrend `device_types = NULL` nur
# "noch nicht nachgetragen" heisst und beim naechsten Bruueckenstart aus
# den ohnehin geholten Abbildern gefuellt wird (`backfill_device_types`).
# Eine Migration kann das nicht: sie sieht nur die Datenbank, nie ein
# `NodeSnapshot`.
```

- [ ] **Step 4: Migration schreiben und eintragen**

Direkt nach `_migrate_to_v6` einfügen:

```python
def _migrate_to_v7(db: sqlite3.Connection) -> None:
    """Fuegt `device.room` und `device.device_types` hinzu (Entwurf
    Geraete-Tab, 2026-09-05, Abschnitt 3.1).

    Zwei Spalten in einem Schritt, wie `_migrate_to_v2` - beide gehoeren zu
    demselben Vorhaben und kaemen nie einzeln vor.

    Kein Backfill. Fuer `room` gibt es keinen Bestandswert, aus dem sich ein
    Raum ableiten liesse, und `NULL` ist ohnehin die gewollte Bedeutung
    ("Ohne Raum"). Fuer `device_types` gaebe es einen - die Matter-
    Geraetetypen stehen im `NodeSnapshot` -, aber genau der liegt einer
    Migration nicht vor: sie bekommt eine `sqlite3.Connection` und sonst
    nichts. Das Nachtragen uebernimmt `Store.backfill_device_types` beim
    Start der Bruecke, wo die Abbilder ohnehin geholt werden."""
    _add_column_if_missing(db, "device", "room", "TEXT")
    _add_column_if_missing(db, "device", "device_types", "TEXT")
```

Und in `_MIGRATIONS`:

```python
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
    3: _migrate_to_v3,
    4: _migrate_to_v4,
    5: _migrate_to_v5,
    6: _migrate_to_v6,
    7: _migrate_to_v7,
}
```

- [ ] **Step 5: `StoredDevice` und `_as_device` erweitern**

Ganz oben in `store.py` `import json` zu den Imports hinzufügen (alphabetisch vor `sqlite3`).

Zwei Modulfunktionen, direkt vor `class StoredDevice`:

```python
def _encode_device_types(types: Mapping[int, frozenset[int]]) -> str:
    """Die Ausgabe von `relevance.device_types_by_endpoint` als JSON fuer die
    Spalte `device.device_types`.

    Endpunkte werden zu Zeichenketten, weil JSON keine ganzzahligen
    Schluessel kennt; die IDs werden sortiert abgelegt, damit zwei gleiche
    Abbilder auch denselben Text ergeben - das macht einen Vergleich in
    einem Test lesbar und verhindert, dass ein bedeutungsloser
    Reihenfolgewechsel wie eine Aenderung aussieht."""
    return json.dumps({str(endpoint): sorted(ids) for endpoint, ids in sorted(types.items())})


def _decode_device_types(raw: str | None) -> dict[int, frozenset[int]] | None:
    """Gegenstueck zu `_encode_device_types`. `None` heisst "noch nicht
    nachgetragen" (siehe `_migrate_to_v7`).

    Unlesbares JSON wird ebenfalls zu `None` statt zu einer Ausnahme: eine
    von Hand verstellte Zeile darf die gesamte Geraeteliste nicht
    unbenutzbar machen: das Geraet landet dann in der Kategorie "Sonstige"
    und wird beim naechsten Bruueckenstart neu befuellt - dieselbe
    Behandlung wie eine nie gefuellte Zeile."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return {int(endpoint): frozenset(int(i) for i in ids) for endpoint, ids in parsed.items()}
```

`Mapping` zu den `collections.abc`-Importen hinzufügen: `from collections.abc import Callable, Mapping, Sequence`.

An `StoredDevice` zwei Felder anhängen (nach `updated_at`):

```python
    # Raum und Geraetetypen (Entwurf Geraete-Tab, 2026-09-05). `room` ist ein
    # frei gewaehlter Name, `None` heisst "Ohne Raum" - es gibt bewusst keine
    # Raum-Tabelle, ein Raum existiert genau so lange, wie ein aktives Geraet
    # seinen Namen traegt.
    #
    # `device_types` traegt die ROHE Auskunft des Geraets (Endpunkt ->
    # Matter-Typ-IDs), nicht die daraus abgeleitete Kategorie. Der Grund
    # steht in der Geschichte dieses Moduls: `signal.functional` und
    # `signal.title` waren gespeicherte Ableitungen, und `_migrate_to_v3`
    # musste sie fuer Bestandszeilen nachtraeglich neu berechnen, als sich
    # die Regel verbesserte. Eine Zuordnungstabelle Matter-Typ -> Kategorie
    # wird wachsen; wird nur die Quelle gespeichert, ist das ein
    # Codewechsel ohne Migration.
    room: str | None
    device_types: dict[int, frozenset[int]] | None
```

Und `_as_device`:

```python
    @staticmethod
    def _as_device(row: sqlite3.Row) -> StoredDevice:
        return StoredDevice(
            id=int(row["id"]),
            node_id=int(row["node_id"]),
            unique_id=str(row["unique_id"]),
            label=str(row["label"]),
            exported_at=row["exported_at"],
            updated_at=row["updated_at"],
            room=row["room"],
            device_types=_decode_device_types(row["device_types"]),
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/model -q`
Expected: PASS, alle Tests der Datei — insbesondere die bestehenden v1–v6-Migrationstests, die durch die neue Version mitlaufen.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/model/store.py tests/model/test_store_migration.py
git commit -m "$(cat <<'EOF'
feat(store): Schema v7 mit Raum und rohen Geraetetypen am Geraet

`device.room` (NULL = "Ohne Raum") und `device.device_types` (JSON,
Endpunkt -> Matter-Typ-IDs). Gespeichert wird bewusst die rohe Auskunft
des Geraets, nicht die daraus abgeleitete Kategorie: `_migrate_to_v3`
musste `signal.functional` genau deshalb schon einmal rueckwirkend neu
berechnen, als sich die Ableitungsregel verbesserte.

Kein Backfill in der Migration - fuer `room` waere keiner moeglich, fuer
`device_types` braeuchte er ein NodeSnapshot, das einer Migration nicht
vorliegt.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Raum schreiben, umbenennen, beim Registrieren mitgeben

**Files:**
- Modify: `src/loxmatter/model/store.py` (`register_device` Zeile 808-826, neue Methoden nach `rename_device` Zeile 867-882)
- Test: `tests/model/test_store.py`

**Interfaces:**
- Consumes: `StoredDevice.room` aus Task 1.
- Produces: `Store.set_room(device_id: int, room: str | None) -> None`, `Store.rename_room(old: str, new: str) -> int` (Anzahl geänderter Geräte, `ValueError` bei leerem Zielnamen), `Store.register_device(snapshot: NodeSnapshot, room: str | None = None) -> int`, Modulfunktion `_normalized_room(str | None) -> str | None`.

- [ ] **Step 1: Write the failing test**

An `tests/model/test_store.py` anhängen (die Datei hat bereits `Store` und einen `load`-Helfer für Fixtures; den vorhandenen Namen übernehmen, nicht neu erfinden):

```python
def test_set_room_stores_the_name_and_trims_it(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"))
        store.set_room(device_id, "  Wohnzimmer  ")
        assert store.device(device_id).room == "Wohnzimmer"
    finally:
        store.close()


def test_set_room_with_blank_input_clears_the_room(tmp_path):
    """Ein Name aus reinem Leerraum hat eine eindeutige Bedeutung - "kein
    Raum" - und ist deshalb kein Fehlerfall, sondern derselbe Weg wie ein
    ausdrueckliches `None`."""
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"))
        store.set_room(device_id, "Bad")
        store.set_room(device_id, "   ")
        assert store.device(device_id).room is None
    finally:
        store.close()


def test_set_room_does_not_touch_updated_at(tmp_path):
    """Der Kern der Entscheidung aus Abschnitt 3.3 des Entwurfs: der Raum
    landet in KEINER Exportvorlage. Wuerde `set_room` `updated_at` mitsetzen,
    bekaeme beim ersten Aufraeumen der Raumzuordnung jedes Geraet eine amber
    "geaendert seit Export"-Pille und die Aufforderung zu einem Export, der
    Byte fuer Byte dieselben Dateien erzeugt. `rename_device` setzt es
    dagegen zu Recht - das Label wird als `Title` exportiert."""
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"))
        before = store.device(device_id).updated_at
        store.set_room(device_id, "Flur")
        assert store.device(device_id).updated_at == before
    finally:
        store.close()


def test_register_device_takes_a_room(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"), room="Küche")
        assert store.device(device_id).room == "Küche"
    finally:
        store.close()


def test_rename_room_moves_every_device_and_reports_the_count(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        plug = store.register_device(load("ikea_grillplats_plug.json"), room="Küche")
        button = store.register_device(load("ikea_bilresa_button.json"), room="Küche")
        assert store.rename_room("Küche", "Essbereich") == 2
        assert store.device(plug).room == "Essbereich"
        assert store.device(button).room == "Essbereich"
    finally:
        store.close()


def test_rename_room_merges_into_an_existing_room(tmp_path):
    """Ein Zielname, den es schon gibt, fuehrt beide Raeume zusammen - die
    naheliegende Bedeutung von "nenne Kueche jetzt Essbereich", wenn es
    einen Essbereich schon gibt. Die Oberflaeche fragt vorher nach; der
    Store fuehrt nur aus."""
    store = Store(tmp_path / "t.sqlite")
    try:
        plug = store.register_device(load("ikea_grillplats_plug.json"), room="Küche")
        button = store.register_device(load("ikea_bilresa_button.json"), room="Essbereich")
        assert store.rename_room("Küche", "Essbereich") == 1
        assert store.device(plug).room == "Essbereich"
        assert store.device(button).room == "Essbereich"
    finally:
        store.close()


def test_rename_room_leaves_removed_devices_alone(tmp_path):
    """`active = 1` in der Bedingung, aus demselben Grund, aus dem
    `Store.devices()` danach filtert: ein entferntes Geraet ist aus Sicht
    der Oberflaeche nicht mehr da und soll nicht stillschweigend mitwandern."""
    store = Store(tmp_path / "t.sqlite")
    try:
        gone = store.register_device(load("ikea_grillplats_plug.json"), room="Küche")
        store.forget_device(gone)
        assert store.rename_room("Küche", "Essbereich") == 0
        row = store._db.execute("SELECT room FROM device WHERE id = ?", (gone,)).fetchone()
        assert row["room"] == "Küche"
    finally:
        store.close()


def test_rename_room_rejects_an_empty_target(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.register_device(load("ikea_grillplats_plug.json"), room="Küche")
        with pytest.raises(ValueError):
            store.rename_room("Küche", "   ")
    finally:
        store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/model/test_store.py -k "room" -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'set_room'`.

- [ ] **Step 3: Implementieren**

Modulfunktion, direkt neben `_decode_device_types`:

```python
def _normalized_room(room: str | None) -> str | None:
    """Ein Raumname ohne aeusseren Leerraum; was danach leer ist, wird `None`.

    Eine Stelle statt drei: `set_room`, `register_device` und `rename_room`
    stellen dieselbe Frage, und ein Raum " Bad" neben "Bad" waeren zwei
    Raeume in der Oberflaeche, ohne dass jemand den Unterschied saehe."""
    if room is None:
        return None
    return room.strip() or None
```

`register_device` bekommt den Parameter (der Frueh-Ausstieg für ein bereits registriertes Gerät bleibt **unverändert** — ein schon bekanntes Gerät behält seinen Raum, ein erneutes Einlernen soll ihn nicht überschreiben):

```python
    def register_device(self, snapshot: NodeSnapshot, room: str | None = None) -> int:
        identity = self._device_identity(snapshot)
        row = self._db.execute(
            "SELECT id FROM device WHERE unique_id = ? AND active = 1", (identity,)
        ).fetchone()
        if row is not None:
            return int(row["id"])

        label = f"{snapshot.vendor_name} {snapshot.product_name}".strip() or identity
        cur = self._db.execute(
            "INSERT INTO device (unique_id, node_id, label, udp_port, updated_at, room)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                identity,
                snapshot.node_id,
                label,
                DEFAULT_UDP_PORT,
                self._now(),
                _normalized_room(room),
            ),
        )
        self._db.commit()
        device_id = cur.lastrowid
        assert device_id is not None
        return int(device_id)
```

Zwei neue Methoden, direkt nach `rename_device`:

```python
    def set_room(self, device_id: int, room: str | None) -> None:
        """Setzt den Raum eines Geraets (`PATCH /api/devices/{device_id}`).

        **Fasst `updated_at` bewusst NICHT an** - der eine Punkt, an dem
        diese Methode von `rename_device` direkt darueber abweicht. Dessen
        Docstring nennt den Grund fuer das Gegenteil: das Label landet im
        naechsten Export als `Title` in der Vorlage, also fuehrt
        `GET /api/export/status` das Geraet danach zu Recht als "seither
        geaendert". Der Raum landet in keiner Vorlage. Wuerde er `updated_at`
        mitsetzen, bekaeme beim ersten Aufraeumen der Raumzuordnung jedes
        Geraet eine amber Pille und die Aufforderung zu einem Export, der
        genau dieselben Dateien erzeugt wie der letzte.

        Wie `rename_device` ohne Existenzpruefung: die aufrufende Route
        prueft ueber `device()` und meldet 404, bevor es hierher kommt."""
        self._db.execute(
            "UPDATE device SET room = ? WHERE id = ?", (_normalized_room(room), device_id)
        )
        self._db.commit()

    def rename_room(self, old: str, new: str) -> int:
        """Benennt einen Raum an allen aktiven Geraeten um und gibt zurueck,
        wie viele es waren (`POST /api/rooms/rename`).

        Es gibt keine Raum-Tabelle (Entwurf 3.2), also ist "Raum umbenennen"
        kein Schreibvorgang auf einem Objekt, sondern dieser eine
        Massenschreibvorgang. Die Alternative waere, an jedem Geraet einzeln
        einen neuen Raumnamen einzutippen - bei fuenf Geraeten fuenf
        Gelegenheiten fuer einen Tippfehler, der einen sechsten Raum erzeugt.

        `active = 1` aus demselben Grund, aus dem `devices()` danach filtert:
        ein entferntes Geraet ist aus Sicht der Oberflaeche nicht mehr da.

        Ein bereits belegter Zielname fuehrt beide Raeume zusammen; die
        Rueckfrage davor ist Sache der Oberflaeche, nicht dieser Methode.
        Ein leerer Zielname dagegen wird hier abgewiesen: "umbenennen" ist
        nicht der Weg, einen Raum aufzuloesen - dafuer gibt es `set_room`
        mit `None` an jedem einzelnen Geraet."""
        target = _normalized_room(new)
        if target is None:
            raise ValueError(i18n.t("api.devices.room_name_required"))
        cur = self._db.execute(
            "UPDATE device SET room = ? WHERE room = ? AND active = 1", (target, old)
        )
        self._db.commit()
        return int(cur.rowcount)
```

- [ ] **Step 4: Übersetzungsschlüssel für die `ValueError`-Nachricht anlegen**

In `src/loxmatter/i18n/strings.yaml`, im `api.devices.*`-Block:

```yaml
api.devices.room_name_required:
  en: "A room name is required."
  de: "Ein Raumname ist erforderlich."
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/model/test_store.py -k "room" -v && uv run pytest tests/model tests/api -q`
Expected: PASS. Prüfen, dass in `tests/model/test_store.py` `import pytest` bereits am Kopf steht — sonst ergänzen.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/model/store.py src/loxmatter/i18n/strings.yaml tests/model/test_store.py
git commit -m "$(cat <<'EOF'
feat(store): Raum setzen, umbenennen und beim Registrieren mitgeben

`set_room` fasst `updated_at` bewusst nicht an, anders als das direkt
darueberstehende `rename_device`: das Label wird als `Title` exportiert,
der Raum in keiner Vorlage. Ohne diese Trennung markierte das erste
Aufraeumen der Raumzuordnung jedes Geraet als "geaendert seit Export"
und forderte einen Export an, der dieselben Dateien erzeugt.

`rename_room` fasst nur aktive Geraete an - dieselbe Grenze wie
`devices()`. Ein belegter Zielname fuehrt zusammen; die Rueckfrage davor
gehoert in die Oberflaeche.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Gerätetypen schreiben und beim Brückenstart nachtragen

**Files:**
- Modify: `src/loxmatter/model/store.py` (`register_device`, neue Methode `backfill_device_types`, Import aus `relevance`)
- Modify: `src/loxmatter/cli.py:606`
- Test: `tests/model/test_store.py`

**Interfaces:**
- Consumes: `_encode_device_types` (Task 1), `register_device(snapshot, room)` (Task 2).
- Produces: `Store.backfill_device_types(snapshots: Sequence[NodeSnapshot]) -> int` (Anzahl gefüllter Zeilen); `register_device` schreibt `device_types` bei der Registrierung mit.

- [ ] **Step 1: Write the failing test**

An `tests/model/test_store.py` anhängen:

```python
def test_register_device_stores_the_matter_device_types(tmp_path):
    """Endpunkt 1 der Steckdose meldet 266 (0x010A, On/Off Plug-in Unit),
    Endpunkt 0 die Verwaltungstypen - beide werden roh abgelegt, gefiltert
    wird erst beim Ableiten der Kategorie."""
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"))
        types = store.device(device_id).device_types
        assert types is not None
        assert types[1] == frozenset({0x010A})
    finally:
        store.close()


def test_backfill_fills_only_rows_that_have_none(tmp_path):
    """Eine Bestandszeile bekommt ihre Typen beim naechsten Bruueckenstart -
    eine bereits gefuellte wird nicht bei jedem Start neu geschrieben."""
    store = Store(tmp_path / "t.sqlite")
    try:
        snapshot = load("ikea_grillplats_plug.json")
        device_id = store.register_device(snapshot)
        store._db.execute("UPDATE device SET device_types = NULL WHERE id = ?", (device_id,))
        store._db.commit()

        assert store.backfill_device_types([snapshot]) == 1
        assert store.device(device_id).device_types is not None
        assert store.backfill_device_types([snapshot]) == 0
    finally:
        store.close()


def test_backfill_leaves_a_device_missing_from_the_snapshots_untouched(tmp_path):
    """Ein Geraet, das beim Start gerade offline ist, fehlt in
    `client.snapshots()`. Es darf dadurch nichts verlieren - deshalb wird
    nur geschrieben, wo ein Abbild vorliegt, und nie geleert."""
    store = Store(tmp_path / "t.sqlite")
    try:
        plug = load("ikea_grillplats_plug.json")
        button = load("ikea_bilresa_button.json")
        plug_id = store.register_device(plug)
        button_id = store.register_device(button)
        store._db.execute("UPDATE device SET device_types = NULL")
        store._db.commit()

        assert store.backfill_device_types([plug]) == 1
        assert store.device(plug_id).device_types is not None
        assert store.device(button_id).device_types is None
    finally:
        store.close()


def test_backfill_does_not_touch_updated_at(tmp_path):
    """Dieselbe Begruendung wie bei `set_room`: die Geraetetypen landen in
    keiner Exportvorlage. Ein Bruueckenstart darf nicht die halbe
    Geraeteliste als "geaendert seit Export" markieren."""
    store = Store(tmp_path / "t.sqlite")
    try:
        snapshot = load("ikea_grillplats_plug.json")
        device_id = store.register_device(snapshot)
        store._db.execute("UPDATE device SET device_types = NULL WHERE id = ?", (device_id,))
        store._db.commit()
        before = store.device(device_id).updated_at

        store.backfill_device_types([snapshot])
        assert store.device(device_id).updated_at == before
    finally:
        store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/model/test_store.py -k "device_types or backfill" -v`
Expected: FAIL — `assert types is not None` schlägt fehl (Spalte wird noch nicht beschrieben) bzw. `AttributeError: 'Store' object has no attribute 'backfill_device_types'`.

- [ ] **Step 3: Implementieren**

In `store.py` den bestehenden Import aus `loxmatter.profiles.relevance` um `device_types_by_endpoint` erweitern (er importiert dort bereits `is_functional`).

`register_device` schreibt die Typen mit:

```python
        label = f"{snapshot.vendor_name} {snapshot.product_name}".strip() or identity
        cur = self._db.execute(
            "INSERT INTO device"
            " (unique_id, node_id, label, udp_port, updated_at, room, device_types)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                identity,
                snapshot.node_id,
                label,
                DEFAULT_UDP_PORT,
                self._now(),
                _normalized_room(room),
                _encode_device_types(device_types_by_endpoint(snapshot)),
            ),
        )
```

Neue Methode, direkt nach `set_room`:

```python
    def backfill_device_types(self, snapshots: Sequence[NodeSnapshot]) -> int:
        """Traegt `device.device_types` fuer Geraete nach, die noch keine
        haben, und gibt zurueck, wie viele das waren.

        Aufgerufen beim Start der Bruecke, direkt neben
        `runtime.seed_from_snapshot(await client.snapshots())` (`cli.py`) -
        die Abbilder aller erreichbaren Knoten sind dort bereits geholt, ein
        zweiter Abruf waere reine Verschwendung.

        **Nur `device_types IS NULL`.** Ein bereits nachgetragenes Geraet
        wird nicht bei jedem Start neu geschrieben, und ein Geraet, das
        gerade offline ist und deshalb in `snapshots()` fehlt, verliert
        seine Typen nicht - hier wird ausschliesslich gefuellt, nie geleert.

        Ob ein Geraet, dessen Typen sich beim erneuten Interview aendern
        (etwa nach einem Firmware-Update), eine Aktualisierung bekommen
        soll, ist bewusst offen gelassen (Entwurf, offener Punkt 2): der
        Fall ist nie beobachtet worden und bekommt keine Mechanik auf
        Verdacht.

        Fasst `updated_at` nicht an - dieselbe Begruendung wie bei
        `set_room`: die Geraetetypen landen in keiner Exportvorlage."""
        by_node = {snapshot.node_id: snapshot for snapshot in snapshots}
        rows = self._db.execute(
            "SELECT id, node_id FROM device WHERE device_types IS NULL AND active = 1"
        ).fetchall()
        filled = 0
        for row in rows:
            snapshot = by_node.get(int(row["node_id"]))
            if snapshot is None:
                continue
            self._db.execute(
                "UPDATE device SET device_types = ? WHERE id = ?",
                (_encode_device_types(device_types_by_endpoint(snapshot)), int(row["id"])),
            )
            filled += 1
        self._db.commit()
        return filled
```

- [ ] **Step 4: Beim Brückenstart aufrufen**

In `src/loxmatter/cli.py`, unmittelbar nach der bestehenden Zeile 606:

```python
        await runtime.seed_from_snapshot(await client.snapshots())
```

wird daraus:

```python
        snapshots = await client.snapshots()
        await runtime.seed_from_snapshot(snapshots)
        # Geraetetypen von Bestandsgeraeten nachtragen (Entwurf Geraete-Tab,
        # 2026-09-05, Abschnitt 3.4): die Abbilder sind gerade geholt, ein
        # zweiter Abruf nur fuer diesen Zweck waere Verschwendung. Fuellt nur
        # Zeilen ohne Typen; ein Geraet, das gerade offline ist und deshalb
        # hier fehlt, behaelt seine und wird beim naechsten Start erreicht.
        store.backfill_device_types(snapshots)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/model tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/model/store.py src/loxmatter/cli.py tests/model/test_store.py
git commit -m "$(cat <<'EOF'
feat(store): Matter-Geraetetypen speichern und beim Start nachtragen

`register_device` legt die Ausgabe von `device_types_by_endpoint` roh ab.
Bestandsgeraete holt `backfill_device_types` beim Bruueckenstart aus den
Abbildern, die `seed_from_snapshot` ohnehin gerade geladen hat - ein
zweiter Abruf nur dafuer waere Verschwendung.

Gefuellt wird nur, wo nichts steht, und nie geleert: ein beim Start
offline stehendes Geraet fehlt in `snapshots()` und soll dadurch nichts
verlieren.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `profiles/categories.py` — Kategorie aus den Gerätetypen

**Files:**
- Create: `src/loxmatter/profiles/categories.py`
- Create: `tests/profiles/test_categories.py`

**Interfaces:**
- Consumes: `UTILITY_DEVICE_TYPES`, `POWER_SOURCE_DEVICE_TYPE` aus `profiles/relevance.py`; `StoredDevice.device_types` aus Task 1.
- Produces: `Category` (`str, Enum` mit Werten `light|socket|switch|covering|climate|sensor|lock|other`), `CATEGORY_RANK: dict[Category, int]`, `CATEGORY_BY_DEVICE_TYPE: dict[int, Category]`, `category_for(device_types: Mapping[int, frozenset[int]] | None) -> Category`.

- [ ] **Step 1: Write the failing test**

`tests/profiles/test_categories.py` neu anlegen — mit dem GPL-Kopf, den jede Quelldatei dieses Projekts trägt (aus einer bestehenden Testdatei kopieren, unverändert im englischen FSF-Wortlaut):

```python
"""Grobe Geraetekategorie aus den Matter-Geraetetypen."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot
from loxmatter.profiles.categories import (
    CATEGORY_BY_DEVICE_TYPE,
    CATEGORY_RANK,
    Category,
    category_for,
)
from loxmatter.profiles.relevance import device_types_by_endpoint

# Derselbe Weg zu den Abbildern wie in `test_relevance.py` nebenan:
# `tests/profiles/` hat keine `conftest.py`, und `load_snapshot` aus
# `tests/api/conftest.py` ist von hier aus nicht importierbar - die beiden
# Verzeichnisse teilen keinen `sys.path`-Eintrag.
FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load_snapshot(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_the_rank_follows_the_declaration_order():
    """Der Rang ist fest verdrahtet und NICHT die alphabetische Reihenfolge
    der uebersetzten Namen: ein Sprachwechsel wuerde die Gruppen sonst
    umsortieren, und eine Ansicht, die je nach Sprache anders aufgebaut ist,
    ist zweimal zu erklaeren."""
    assert [c.value for c in Category] == [
        "light",
        "socket",
        "switch",
        "covering",
        "climate",
        "sensor",
        "lock",
        "other",
    ]
    assert CATEGORY_RANK[Category.LIGHT] == 0
    assert CATEGORY_RANK[Category.OTHER] == 7


def test_the_plug_fixture_is_a_socket():
    """Endpunkt 0 traegt Root Node und OTA Requestor, Endpunkt 1 die
    On/Off Plug-in Unit (0x010A) - der Verwaltungs-Endpunkt wird
    uebersprungen."""
    types = device_types_by_endpoint(load_snapshot("ikea_grillplats_plug.json"))
    assert category_for(types) is Category.SOCKET


def test_the_button_fixture_is_a_switch():
    types = device_types_by_endpoint(load_snapshot("ikea_bilresa_button.json"))
    assert category_for(types) is Category.SWITCH


def test_the_color_light_fixture_is_a_light():
    types = device_types_by_endpoint(load_snapshot("synthetic_color_light.json"))
    assert category_for(types) is Category.LIGHT


def test_a_snapshot_without_descriptors_is_other():
    """`example_light.json` meldet kein einziges Descriptor-Attribut - genau
    der Zustand, in dem auch ein noch nicht nachgetragenes Bestandsgeraet
    steht."""
    types = device_types_by_endpoint(load_snapshot("example_light.json"))
    assert category_for(types) is Category.OTHER


def test_none_and_empty_are_other():
    assert category_for(None) is Category.OTHER
    assert category_for({}) is Category.OTHER


def test_only_utility_types_are_other():
    """Root Node, OTA Requestor und PowerSource sagen nichts darueber, was
    das Geraet im Haus tut - bleibt nichts uebrig, ist die Kategorie
    "Sonstige", nicht etwa die des Verwaltungs-Endpunkts."""
    assert category_for({0: frozenset({0x0016, 0x0012, 0x0011})}) is Category.OTHER


def test_the_lowest_non_utility_endpoint_decides():
    """Bei Matter ist Endpunkt 1 ueblicherweise der Anwendungs-Endpunkt. Ein
    zweiter Endpunkt mit einem anderen Typ darf ihn nicht ueberstimmen."""
    types = {
        0: frozenset({0x0016}),
        1: frozenset({0x010A}),
        2: frozenset({0x0302}),
    }
    assert category_for(types) is Category.SOCKET


def test_several_types_on_one_endpoint_resolve_by_rank():
    """Damit das Ergebnis unabhaengig davon ist, in welcher Reihenfolge das
    Geraet seine Typen aufzaehlt - ein `frozenset` hat gar keine."""
    assert category_for({1: frozenset({0x010A, 0x0100})}) is Category.LIGHT


def test_an_unknown_device_type_is_other():
    assert category_for({1: frozenset({0x0FFF})}) is Category.OTHER


@pytest.mark.parametrize(
    ("device_type", "expected"),
    [
        (0x0100, Category.LIGHT),
        (0x010D, Category.LIGHT),
        (0x010A, Category.SOCKET),
        (0x010B, Category.SOCKET),
        (0x000F, Category.SWITCH),
        (0x0104, Category.SWITCH),
        (0x0202, Category.COVERING),
        (0x0301, Category.CLIMATE),
        (0x002B, Category.CLIMATE),
        (0x0302, Category.SENSOR),
        (0x0107, Category.SENSOR),
        (0x000A, Category.LOCK),
    ],
)
def test_the_table_maps_the_types_it_claims_to(device_type, expected):
    assert CATEGORY_BY_DEVICE_TYPE[device_type] is expected


def test_every_mapped_type_exists_in_the_matter_table():
    """Die Zuordnung muss pro ID gegen die maschinell erzeugte Tabelle von
    matter-server belegt sein, nicht geraten - genau die Quelle, auf die
    sich auch `relevance.py` beruft. Ein Tippfehler in einer ID faellt hier
    auf und nicht erst an einem echten Geraet."""
    from matter_server.client.models.device_types import ALL_TYPES

    unknown = sorted(hex(t) for t in CATEGORY_BY_DEVICE_TYPE if t not in ALL_TYPES)
    assert unknown == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/profiles/test_categories.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.profiles.categories'`.

- [ ] **Step 3: Modul schreiben**

`src/loxmatter/profiles/categories.py` (GPL-Kopf wie in jeder anderen Quelldatei voranstellen):

```python
"""Grobe Geraetekategorie aus den Matter-Geraetetypen.

Beantwortet genau eine Frage, die `relevance.py` nicht beantwortet: nicht
"welche Signale will jemand sehen", sondern "was fuer ein Ding ist das
ueberhaupt". Die Antwort traegt in der Oberflaeche drei Dinge auf einmal -
die Sortierung innerhalb eines Raums, das Icon der Kachel und den
Suchbegriff, unter dem man alle Steckdosen des Hauses findet.

Warum daneben und nicht darin: `relevance.is_functional` entscheidet ueber
ein einzelnes Signal, `category_for` ueber ein ganzes Geraet. Beide lesen
dieselbe Quelle (`device_types_by_endpoint`), aber mit verschiedenem
Ausgang und ohne gemeinsamen Zustand.

**Die Quelle der Typ-Nummern** ist dieselbe wie in `relevance.py`:
`matter_server.client.models.device_types`, laut eigenem Modul-Docstring
maschinell erzeugt aus `zcl/data-model/chip/matter-devices.xml` der
CSA-Spezifikation. Ein neuer Eintrag in der Tabelle unten braucht die
Nummer aus dieser Datei, nicht aus dem Gedaechtnis;
`test_every_mapped_type_exists_in_the_matter_table` prueft das ab.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from loxmatter.profiles.relevance import POWER_SOURCE_DEVICE_TYPE, UTILITY_DEVICE_TYPES


class Category(str, Enum):
    """Die Reihenfolge dieser Deklaration IST der Sortierrang (siehe
    `CATEGORY_RANK`) - bewusst nicht die alphabetische Reihenfolge der
    uebersetzten Namen, die sich mit der Sprache aendern wuerde.

    Die Reihenfolge selbst folgt der Haeufigkeit, mit der man ein Geraet
    dieser Art in einem Raum anfasst: Licht und Steckdose zuerst, danach die
    Bedienelemente, ganz hinten das, was man einmal einrichtet und dann in
    Ruhe laesst. `OTHER` steht immer am Ende - dort landet auch jedes
    Geraet, dessen Typen noch nicht nachgetragen sind.

    `str, Enum` statt `StrEnum`, weil `Exportability` in `profiles/table.py`
    es genauso macht - eine zweite Schreibweise fuer dieselbe Sache waere
    ohne Gewinn."""

    LIGHT = "light"
    SOCKET = "socket"
    SWITCH = "switch"
    COVERING = "covering"
    CLIMATE = "climate"
    SENSOR = "sensor"
    LOCK = "lock"
    OTHER = "other"


CATEGORY_RANK: dict[Category, int] = {category: rank for rank, category in enumerate(Category)}

# Geraetetypen, die nichts darueber sagen, was das Geraet im Haus TUT -
# dieselbe Menge, die `relevance.is_functional` schon als Verwaltung
# behandelt, plus PowerSource: ein Batteriestand macht aus einem Taster
# keine eigene Kategorie.
_IGNORED_DEVICE_TYPES: frozenset[int] = UTILITY_DEVICE_TYPES | {POWER_SOURCE_DEVICE_TYPE}

# Zuordnung Matter-Geraetetyp -> Kategorie. Jede Nummer stammt aus
# `matter_server.client.models.device_types` (siehe Modul-Docstring); die
# Kommentare nennen den dortigen Klassennamen, damit ein Nachschlagen ohne
# Umrechnung moeglich ist.
#
# Nicht aufgefuehrt und damit `OTHER`: Haushaltsgeraete (0x0070-0x007C),
# Medien (0x0022-0x002A), Energie (0x050C-0x050F), Netzwerk-Infrastruktur
# (0x0090, 0x0091), Bruecken-Verwaltung (0x000E Aggregator, 0x0013 Bridged
# Node). Sie kommen an einer Loxone-Anbindung entweder gar nicht vor oder
# haetten in einer Raumliste keinen eigenen Rang verdient.
CATEGORY_BY_DEVICE_TYPE: dict[int, Category] = {
    0x0100: Category.LIGHT,  # OnOffLight
    0x0101: Category.LIGHT,  # DimmableLight
    0x010C: Category.LIGHT,  # ColorTemperatureLight
    0x010D: Category.LIGHT,  # ExtendedColorLight
    # MountedOnOffControl / MountedDimmableLoadControl sind fest verbaute
    # Lastschalter - in der Praxis sitzt dahinter eine Leuchte, nicht eine
    # Steckdose (die traegt einen eigenen Typ, siehe unten).
    0x010F: Category.LIGHT,  # MountedOnOffControl
    0x0110: Category.LIGHT,  # MountedDimmableLoadControl
    0x010A: Category.SOCKET,  # OnOffPlugInUnit
    0x010B: Category.SOCKET,  # DimmablePlugInUnit
    0x000F: Category.SWITCH,  # GenericSwitch
    0x0103: Category.SWITCH,  # OnOffLightSwitch
    0x0104: Category.SWITCH,  # DimmerSwitch
    0x0105: Category.SWITCH,  # ColorDimmerSwitch
    0x0840: Category.SWITCH,  # ControlBridge
    0x0202: Category.COVERING,  # WindowCovering
    0x0203: Category.COVERING,  # WindowCoveringController
    0x0300: Category.CLIMATE,  # HeatingCoolingUnit
    0x0301: Category.CLIMATE,  # Thermostat
    0x0309: Category.CLIMATE,  # HeatPump
    0x002B: Category.CLIMATE,  # Fan
    0x002D: Category.CLIMATE,  # AirPurifier
    0x0072: Category.CLIMATE,  # RoomAirConditioner
    0x0015: Category.SENSOR,  # ContactSensor
    0x002C: Category.SENSOR,  # AirQualitySensor
    0x0041: Category.SENSOR,  # WaterFreezeDetector
    0x0043: Category.SENSOR,  # WaterLeakDetector
    0x0044: Category.SENSOR,  # RainSensor
    0x0076: Category.SENSOR,  # SmokeCoAlarm
    0x0106: Category.SENSOR,  # LightSensor
    0x0107: Category.SENSOR,  # OccupancySensor
    0x0302: Category.SENSOR,  # TemperatureSensor
    0x0305: Category.SENSOR,  # PressureSensor
    0x0306: Category.SENSOR,  # FlowSensor
    0x0307: Category.SENSOR,  # HumiditySensor
    0x0510: Category.SENSOR,  # ElectricalSensor
    0x0850: Category.SENSOR,  # OnOffSensor
    0x000A: Category.LOCK,  # DoorLock
    0x000B: Category.LOCK,  # DoorLockController
}


def category_for(device_types: Mapping[int, frozenset[int]] | None) -> Category:
    """Die Kategorie eines Geraets aus seinen Geraetetypen je Endpunkt.

    `None` (Geraetetypen noch nicht nachgetragen, siehe
    `Store.backfill_device_types`) ergibt `OTHER` - dieselbe Antwort wie fuer
    ein Geraet, dessen Typen niemand zuordnen kann. Die Oberflaeche
    unterscheidet beide Faelle nicht: in beiden steht das Geraet vollstaendig
    bedienbar unter "Sonstige", der erste Fall behebt sich beim naechsten
    Bruueckenstart von selbst.

    Die Regel in vier Schritten (Entwurf 5.2):

    1. Verwaltungstypen fallen weg (`_IGNORED_DEVICE_TYPES`).
    2. Vom Rest zaehlt der NIEDRIGSTE Endpunkt - bei Matter ueblicherweise
       Endpunkt 1, der Anwendungs-Endpunkt. Eine Steckdose mit einem
       Temperaturfuehler auf Endpunkt 2 bleibt eine Steckdose.
    3. Traegt dieser Endpunkt mehrere zuordenbare Typen, gewinnt der mit dem
       niedrigsten Rang. Damit haengt das Ergebnis nicht daran, in welcher
       Reihenfolge das Geraet seine Typen aufzaehlt - ein `frozenset` hat
       ohnehin keine.
    4. Nichts Zuordenbares -> `OTHER`.
    """
    if not device_types:
        return Category.OTHER

    useful = {
        endpoint: ids - _IGNORED_DEVICE_TYPES
        for endpoint, ids in device_types.items()
        if ids - _IGNORED_DEVICE_TYPES
    }
    if not useful:
        return Category.OTHER

    primary = useful[min(useful)]
    mapped = [CATEGORY_BY_DEVICE_TYPE[t] for t in primary if t in CATEGORY_BY_DEVICE_TYPE]
    if not mapped:
        return Category.OTHER
    return min(mapped, key=lambda category: CATEGORY_RANK[category])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/profiles/test_categories.py -v`
Expected: PASS, alle 20 Testfälle (die parametrisierte Tabellenprüfung zählt zwölf davon).

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/profiles/categories.py tests/profiles/
git commit -m "$(cat <<'EOF'
feat(profiles): Geraetekategorie aus den Matter-Geraetetypen ableiten

Beantwortet die eine Frage, die `relevance.py` nicht beantwortet: nicht
"welche Signale will jemand sehen", sondern "was fuer ein Ding ist das".
Die Antwort traegt in der Oberflaeche Sortierung, Icon und Suchbegriff.

Der Rang der Kategorien ist die Deklarationsreihenfolge, ausdruecklich
nicht die alphabetische der uebersetzten Namen - sonst sortierte ein
Sprachwechsel die Gruppen um. Jede Typ-Nummer stammt aus der maschinell
erzeugten Tabelle von matter-server, abgeprueft durch einen Test gegen
deren ALL_TYPES.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: API — Modelle und Routen

**Files:**
- Modify: `src/loxmatter/api/models.py` (`DeviceOut` Zeile 62-99, `DeviceRename` Zeile ~106, `CommissionRequest` Zeile 162-176)
- Modify: `src/loxmatter/api/devices.py` (Imports Zeile 77-90, `_device_out` Zeile 171-199, PATCH-Route Zeile 261-265, Commission-Route Zeile 395-397)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_devices.py`, `tests/api/test_rooms.py` (neu)

**Interfaces:**
- Consumes: `Store.set_room`, `Store.rename_room`, `Store.register_device(snapshot, room)` (Tasks 2–3); `category_for`, `Category`, `CATEGORY_RANK` (Task 4).
- Produces: `DeviceOut` mit `room: str | None`, `category: str`, `category_rank: int`; `DevicePatch(label: str | None, room: str | None)`; `RoomRename(from_room, to_room)` mit Aliassen `from`/`to`; Routen `PATCH /api/devices/{id}` (erweitert), `POST /api/rooms/rename` (neu), `POST /api/devices/commission` (erweitert).

- [ ] **Step 1: Write the failing test**

An `tests/api/test_devices.py` anhängen:

```python
async def test_the_device_list_carries_room_and_category(api):
    client, _store, device_id, _fake = api
    devices = (await client.get("/api/devices")).json()
    device = next(d for d in devices if d["id"] == device_id)
    assert device["room"] is None
    assert device["category"] == "socket"
    assert device["category_rank"] == 1


async def test_patching_only_the_room_leaves_the_label_alone(api):
    client, store, device_id, _fake = api
    before = store.device(device_id).label
    response = await client.patch(f"/api/devices/{device_id}", json={"room": "  Küche  "})
    assert response.status_code == 200
    assert response.json()["room"] == "Küche"
    assert store.device(device_id).label == before


async def test_patching_only_the_label_leaves_the_room_alone(api):
    client, store, device_id, _fake = api
    store.set_room(device_id, "Bad")
    response = await client.patch(f"/api/devices/{device_id}", json={"label": "Steckdose"})
    assert response.status_code == 200
    assert response.json()["room"] == "Bad"
    assert response.json()["label"] == "Steckdose"


async def test_an_empty_room_string_clears_the_room(api):
    """`""` heisst "Raum entfernen", `null`/weggelassen heisst
    "unveraendert" - dasselbe Prinzip wie bei `SignalPatch`."""
    client, store, device_id, _fake = api
    store.set_room(device_id, "Bad")
    response = await client.patch(f"/api/devices/{device_id}", json={"room": ""})
    assert response.status_code == 200
    assert response.json()["room"] is None


async def test_patching_the_room_does_not_make_the_device_pending(api):
    """Der Raum landet in keiner Exportvorlage - ein frisch exportiertes
    Geraet darf durch eine Raumzuweisung nicht wieder ausstehend werden
    (Entwurf 3.3).

    Der Export vorweg ist noetig, damit der Ausgangszustand eindeutig ist:
    ein nie exportiertes Geraet gilt immer als ausstehend, dort waere die
    Aussage dieses Tests nicht zu erkennen.

    Die Gegenprobe - eine Umbenennung MUSS das Geraet als ausstehend
    fuehren - steht bereits in `tests/api/test_export_api.py` (der Test um
    Zeile 280, "Umbenennung … muss `GET /api/export/status` melden") und
    wird hier nicht ein zweites Mal geschrieben. Sie ist der Grund, warum
    dieser Test nicht dadurch gruen werden kann, dass `updated_at`
    versehentlich gar nicht mehr gesetzt wird.

    `GET /api/export/status` antwortet mit einer LISTE, nicht mit einem
    Objekt (`-> list[ExportStatusOut]`, `api/export.py:362`)."""
    client, store, device_id, _fake = api
    store.mark_exported(device_id)

    status = (await client.get("/api/export/status")).json()
    entry = next(e for e in status if e["device_id"] == device_id)
    assert entry["changed_since_export"] is False

    await client.patch(f"/api/devices/{device_id}", json={"room": "Flur"})

    status = (await client.get("/api/export/status")).json()
    entry = next(e for e in status if e["device_id"] == device_id)
    assert entry["changed_since_export"] is False


async def test_commissioning_accepts_a_room(api):
    client, _store, _device_id, fake_client = api
    fake_client.snapshot_to_return = load_snapshot("ikea_bilresa_button.json")
    response = await client.post(
        "/api/devices/commission", json={"code": "1234-567-8901", "room": "Küche"}
    )
    assert response.status_code == 201
    assert response.json()["room"] == "Küche"
    assert response.json()["category"] == "switch"
```

Zum letzten Test: den Namen prüfen, unter dem `FakeMatterClient` in `tests/api/conftest.py` das zurückzugebende Abbild entgegennimmt, und ihn hier einsetzen — die bestehenden Commissioning-Tests in derselben Datei machen es bereits vor.

`tests/api/test_rooms.py` neu anlegen (GPL-Kopf voranstellen, `api`-Fixture aus `test_devices.py` nachbauen oder — falls sie inzwischen in `tests/api/conftest.py` steht — von dort beziehen):

```python
async def test_renaming_a_room_moves_every_device(api):
    client, store, device_id, _fake = api
    store.set_room(device_id, "Küche")
    response = await client.post("/api/rooms/rename", json={"from": "Küche", "to": "Essbereich"})
    assert response.status_code == 200
    assert response.json() == {"renamed": 1}
    assert store.device(device_id).room == "Essbereich"


async def test_renaming_an_unknown_room_is_a_404(api):
    """Analog zu `GET /devices/{id}` fuer ein entferntes Geraet: was nicht da
    ist, wird nicht stillschweigend zu einem Erfolg mit null Aenderungen -
    sonst saehe ein Tippfehler im Quellnamen wie ein geglueckter Vorgang aus."""
    client, _store, _device_id, _fake = api
    response = await client.post("/api/rooms/rename", json={"from": "Keller", "to": "Bad"})
    assert response.status_code == 404


async def test_renaming_to_an_empty_name_is_a_422(api):
    client, store, device_id, _fake = api
    store.set_room(device_id, "Küche")
    response = await client.post("/api/rooms/rename", json={"from": "Küche", "to": "   "})
    assert response.status_code == 422
    assert store.device(device_id).room == "Küche"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_devices.py tests/api/test_rooms.py -k "room or category" -v`
Expected: FAIL — `KeyError: 'room'` in der Antwort bzw. 404 auf `/api/rooms/rename` (Route existiert nicht).

- [ ] **Step 3: Modelle erweitern**

In `src/loxmatter/api/models.py`, an `DeviceOut` drei Felder anhängen und den Docstring um einen Absatz ergänzen:

```python
    id: int
    node_id: int
    label: str
    online: bool
    signal_count: int
    exportable_count: int
    next_export_count: int
    # Raum und Kategorie (Entwurf Geraete-Tab, 2026-09-05). `room` ist der
    # frei gewaehlte Name, `None` heisst "Ohne Raum". `category` ist die
    # Kennung aus `profiles.categories.Category` (`socket`, `light`, …),
    # NICHT der uebersetzte Name - den setzt die Oberflaeche selbst ueber
    # `t("web.devices.category." + category)`, damit die Suche nach
    # "Steckdose" bzw. "socket" in der jeweils angezeigten Sprache trifft.
    # `category_rank` kommt aus derselben Quelle wie die Kategorie, statt
    # die Reihenfolge ein zweites Mal in JavaScript zu fuehren.
    room: str | None
    category: str
    category_rank: int
```

`DeviceRename` wird zu `DevicePatch`:

```python
class DevicePatch(BaseModel):
    """`PATCH /api/devices/{device_id}` - Label und Raum, sonst nichts.

    Hiess bis zum Geraete-Tab-Entwurf `DeviceRename` und konnte nur das
    Label; der Name zieht mit der Faehigkeit mit. Weder `node_id` noch `id`
    gehoeren hier her, aus demselben Grund wie bei `SignalPatch`: was das
    Modell nicht kennt, kann eine Route nicht versehentlich uebernehmen
    (Pydantic v2 verwirft unbekannte Felder per `extra="ignore"`).

    `None` heisst "unveraendert" - fuer BEIDE Felder, wie bei `SignalPatch`.
    Fuer den Raum braucht es deshalb einen zweiten Weg, ihn zu ENTFERNEN:
    das ist der Leerstring `""`, den `Store.set_room` ueber
    `_normalized_room` zu `NULL` macht. Ein Name aus reinem Leerraum geht
    denselben Weg - er hat dieselbe eindeutige Bedeutung und ist deshalb
    kein 422 wert."""

    model_config = ConfigDict(frozen=True)

    label: str | None = None
    room: str | None = None


class RoomRename(BaseModel):
    """`POST /api/rooms/rename`.

    Die Felder heissen innen `from_room`/`to_room`, weil `from` ein
    Python-Schluesselwort ist; nach aussen tragen sie ueber `alias` die
    kurzen Namen, die im JSON stehen. `populate_by_name` erlaubt beides,
    damit ein Test das Modell auch direkt mit den Python-Namen bauen kann."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    from_room: str = Field(alias="from")
    to_room: str = Field(alias="to")
```

`Field` aus `pydantic` importieren, falls noch nicht vorhanden.

`CommissionRequest` bekommt ein Feld und einen Docstring-Absatz:

```python
    code: str
    thread_dataset: str | None = None
    # Raum (Entwurf Geraete-Tab, 2026-09-05, Abschnitt 6.7): optional, weil
    # ein Geraet ohne Raumwahl unter "Ohne Raum" landet und sich jederzeit
    # nachtraeglich zuordnen laesst. Scheitert das Einlernen, entsteht kein
    # Geraet und damit auch kein Raum.
    room: str | None = None
```

- [ ] **Step 4: Routen anpassen**

In `src/loxmatter/api/devices.py`: den Import `DeviceRename` durch `DevicePatch` ersetzen und `RoomRename` ergänzen; dazu `from loxmatter.profiles.categories import CATEGORY_RANK, category_for`.

`_device_out` erweitern:

```python
    next_export_count = len(to_inputs(signals, device.id, device.label))
    category = category_for(device.device_types)
    return DeviceOut(
        id=device.id,
        node_id=device.node_id,
        label=device.label,
        online=online,
        signal_count=len(signals),
        exportable_count=exportable_count,
        next_export_count=next_export_count,
        room=device.room,
        category=category.value,
        category_rank=CATEGORY_RANK[category],
    )
```

Die PATCH-Route:

```python
    @router.patch("/devices/{device_id}")
    async def patch_device(device_id: int, patch: DevicePatch) -> DeviceOut:
        """Aendert Label und/oder Raum. `None` heisst bei beiden Feldern
        "unveraendert"; der Leerstring im Raum heisst "entfernen".

        Die beiden Schreibwege sind bewusst verschieden: `rename_device`
        setzt `updated_at` mit (das Label wird als `Title` exportiert),
        `set_room` nicht (der Raum wird nirgends exportiert). Siehe die
        Docstrings beider Store-Methoden."""
        device = _require_device(device_id)
        if patch.label is not None:
            store.rename_device(device.id, patch.label)
        if patch.room is not None:
            store.set_room(device.id, patch.room)
        return _device_out(store.device(device.id), store, runtime)
```

Die neue Route, direkt darunter:

```python
    @router.post("/rooms/rename")
    async def rename_room(patch: RoomRename) -> dict[str, int]:
        """Benennt einen Raum an allen aktiven Geraeten um.

        Die einzige Route, die es fuer Raeume ueberhaupt gibt - es gibt keine
        Raum-Objekte (Entwurf 3.2), also auch kein `GET /api/rooms`: die
        Raumliste steckt bereits in `GET /api/devices`, und ein zweiter
        Endpunkt fuer dieselbe Auskunft koennte nur auseinanderlaufen.

        404 statt "0 umbenannt", wenn kein aktives Geraet den Quellnamen
        traegt: ein Tippfehler im Quellnamen saehe sonst wie ein geglueckter
        Vorgang aus."""
        if not patch.to_room.strip():
            raise HTTPException(
                status_code=422, detail=i18n.t("api.devices.room_name_required")
            )
        renamed = store.rename_room(patch.from_room, patch.to_room)
        if renamed == 0:
            raise HTTPException(
                status_code=404,
                detail=i18n.t("api.devices.unknown_room", room=patch.from_room),
            )
        return {"renamed": renamed}
```

In der Commission-Route den Raum durchreichen:

```python
        device_id = store.register_device(snapshot, room=request.room)
```

- [ ] **Step 5: Übersetzungsschlüssel ergänzen**

In `strings.yaml`, im `api.devices.*`-Block (`api.devices.room_name_required` steht seit Task 2 schon dort):

```yaml
api.devices.unknown_room:
  en: "No device is assigned to the room “{room}”."
  de: "Dem Raum „{room}“ ist kein Gerät zugeordnet."
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/api -q && uv run mypy src && uv run ruff check .`
Expected: PASS. Schlägt ein bestehender Test auf `DeviceRename` fehl, ist es genau der beabsichtigte Umbenennungs-Treffer — den Test auf `DevicePatch` umstellen, nicht die Klasse zurückbenennen.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/api tests/api/test_devices.py tests/api/test_rooms.py src/loxmatter/i18n/strings.yaml
git commit -m "$(cat <<'EOF'
feat(api): Raum und Kategorie am Geraet, eine Route zum Raum-Umbenennen

`DeviceRename` heisst jetzt `DevicePatch` - der Name zog mit der
Faehigkeit mit. `None` heisst bei beiden Feldern "unveraendert", der
Leerstring im Raum heisst "entfernen".

`DeviceOut` traegt die Kategorie als Kennung, nicht als uebersetzten
Namen: den setzt die Oberflaeche selbst, damit die Suche in der
angezeigten Sprache trifft. `category_rank` kommt aus derselben Quelle,
statt die Reihenfolge ein zweites Mal in JavaScript zu fuehren.

Kein `GET /api/rooms`: die Raumliste steckt bereits in `GET /api/devices`,
ein zweiter Endpunkt fuer dieselbe Auskunft koennte nur auseinanderlaufen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Übersetzungsschlüssel für die Oberfläche

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml` (`web.devices.*`-Block ab Zeile 611)
- Test: `tests/test_i18n.py` (bestehende Vollständigkeitsprüfung, keine neue Testdatei)

**Interfaces:**
- Consumes: nichts.
- Produces: die Schlüssel, die Tasks 7 und 8 in `app.js` und `index.html` verwenden. **Kein Schlüssel darf einen `{platzhalter}` tragen, der serverseitig nicht auflöst** — siehe Global Constraints.

- [ ] **Step 1: Schlüssel anlegen**

An den `web.devices.*`-Block in `strings.yaml` anhängen:

```yaml
# --- Raeume und Suche (Entwurf Geraete-Tab, 2026-09-05) ---
# Die Kategorienamen unten sind die einzige Stelle, an der eine Kategorie
# ihren lesbaren Namen bekommt - `profiles/categories.py` kennt nur die
# Kennung. Die Suche vergleicht gegen genau diese Texte, deshalb findet
# "Steckdose" auf Deutsch und "socket" auf Englisch dieselben Geraete.
web.devices.category.light:
  en: "Light"
  de: "Licht"
web.devices.category.socket:
  en: "Socket"
  de: "Steckdose"
web.devices.category.switch:
  en: "Switch"
  de: "Taster"
web.devices.category.covering:
  en: "Covering"
  de: "Beschattung"
web.devices.category.climate:
  en: "Climate"
  de: "Klima"
web.devices.category.sensor:
  en: "Sensor"
  de: "Sensor"
web.devices.category.lock:
  en: "Lock"
  de: "Schloss"
web.devices.category.other:
  en: "Other"
  de: "Sonstige"
web.devices.room_all:
  en: "All"
  de: "Alle"
web.devices.room_none:
  en: "No room"
  de: "Ohne Raum"
web.devices.room_label:
  en: "Room"
  de: "Raum"
web.devices.room_new:
  en: "+ New room…"
  de: "+ Neuer Raum…"
web.devices.room_new_placeholder:
  en: "Room name"
  de: "Raumname"
web.devices.room_rename:
  en: "Rename room"
  de: "Raum umbenennen"
web.devices.room_rename_prompt:
  en: "New name for this room:"
  de: "Neuer Name für diesen Raum:"
web.devices.room_rename_merge_confirm:
  en: "A room with that name already exists. Both rooms will be merged — this cannot be undone. Continue?"
  de: "Ein Raum dieses Namens besteht bereits. Beide Räume werden zusammengeführt — das lässt sich nicht rückgängig machen. Fortfahren?"
web.devices.room_save_error:
  en: "Room could not be saved:"
  de: "Raum konnte nicht gespeichert werden:"
web.devices.room_rename_error:
  en: "Room could not be renamed:"
  de: "Raum konnte nicht umbenannt werden:"
web.devices.search_placeholder:
  en: "Search name, category, room"
  de: "Name, Kategorie, Raum suchen"
web.devices.search_empty:
  en: "No device matches this search."
  de: "Kein Gerät passt zu dieser Suche."
web.devices.search_hits_elsewhere:
  en: "further matches in other rooms —"
  de: "weitere Treffer in anderen Räumen —"
web.devices.search_show_all_rooms:
  en: "show all rooms"
  de: "alle Räume anzeigen"
web.devices.more_signals_short:
  en: "more"
  de: "weitere"
web.devices.more_commands_short:
  en: "unnamed"
  de: "unbenannt"
web.devices.commission_room_hint:
  en: "The room is optional — without a selection the device appears under “No room” and can be assigned later."
  de: "Der Raum ist optional — ohne Auswahl erscheint das Gerät unter „Ohne Raum“ und lässt sich später zuordnen."
```

**Warum die Fehler-Schlüssel auf einen Doppelpunkt enden und keinen `{message}`-Platzhalter tragen:** serverseitig ruft `api/language.py:_web_strings()` jeden `web.*`-Schlüssel ohne Werte auf. Ein Platzhalter wirft dort `KeyError` und reißt die gesamte `GET /api/i18n`-Antwort mit. Die bestehenden Schlüssel `web.devices.label_save_error` und `web.devices.list_load_error` prüfen — tragen sie Platzhalter, ist dieser Weg dort bereits gelöst; dann darf sich der neue Text daran anlehnen statt eine zweite Bauform einzuführen. Im Zweifel: kein Platzhalter, die Meldung wird in `app.js` angehängt.

- [ ] **Step 2: Vollständigkeit und Ladbarkeit prüfen**

Run: `uv run pytest tests/test_i18n.py tests/api/test_language.py -q`
Expected: PASS — insbesondere der Test, der `GET /api/i18n` ohne Sitzung abruft: er bricht, sobald ein `web.*`-Schlüssel einen serverseitig unbefüllbaren Platzhalter trägt.

- [ ] **Step 3: Commit**

```bash
git add src/loxmatter/i18n/strings.yaml
git commit -m "$(cat <<'EOF'
feat(i18n): Schluessel fuer Raeume, Kategorien und Geraetesuche

Die acht Kategorienamen sind die einzige Stelle, an der eine Kategorie
ihren lesbaren Namen bekommt - `profiles/categories.py` kennt nur die
Kennung. Die Suche vergleicht gegen genau diese Texte, deshalb findet
"Steckdose" auf Deutsch und "socket" auf Englisch dieselben Geraete.

Die neuen Fehlermeldungen tragen bewusst keinen Platzhalter: `GET
/api/i18n` loest jeden web.*-Schluessel ohne Werte auf, ein Platzhalter
wirft dort KeyError und reisst die gesamte Antwort mit.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `app.js` — Räume, Filter, Suche, Sortierung, Leitwert

**Files:**
- Modify: `src/loxmatter/web/app.js` (Zustand ab Zeile 340, Helfer ab Zeile 874, `saveLabel` Zeile 927, `commissionDevice` Zeile 1063)
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `DeviceOut.room/category/category_rank` und die Routen aus Task 5; die Schlüssel aus Task 6.
- Produces: die Alpine-Methoden, die Task 8 im Markup aufruft — `roomKeyOf(device)`, `roomChips()`, `hasAnyRoom()`, `matchesSearch(device)`, `visibleDevices()`, `hitsOutsideRoom()`, `clearRoomFilter()`, `deviceGroups()`, `categoryLabel(device)`, `leadSignalFor(deviceId)`, `restSignalsFor(deviceId)`, `saveRoom(device, value)`, `beginNewRoom(device)`, `commitNewRoom(device)`, `renameRoom(room)` — und die Zustandsfelder `roomFilter` (`null` = Alle, `""` = Ohne Raum, sonst der Raumname), `deviceSearch`, `newRoomFor`, `newRoomDraft`, `commissionRoom`, `commissionNewRoom`.

- [ ] **Step 1: Write the failing test**

An `tests/api/test_web.py` anhängen:

```python
async def test_the_script_offers_room_filtering_grouping_and_search(api):
    """Die Oberflaeche wird nicht von einem JS-Testlaeufer geprueft (es gibt
    keinen - Alpine laeuft vendored im Browser). Diese Pruefung haelt
    deshalb nur fest, DASS die Bausteine ausgeliefert werden, auf die das
    Markup in index.html sich stuetzt - ein Umbenennen auf einer Seite ohne
    die andere faellt hier auf."""
    script = (await api.get("/app.js")).text
    for name in (
        "roomKeyOf(",
        "roomChips(",
        "hasAnyRoom(",
        "visibleDevices(",
        "deviceGroups(",
        "categoryLabel(",
        "leadSignalFor(",
        "restSignalsFor(",
        "saveRoom(",
        "beginNewRoom(",
        "commitNewRoom(",
        "renameRoom(",
        "hitsOutsideRoom(",
        "clearRoomFilter(",
    ):
        assert name in script, name


async def test_the_search_never_reaches_the_server(api):
    """Die Suche laeuft ueber die ohnehin geladene Geraeteliste - es gibt
    keinen Endpunkt dafuer, und es soll auch keiner entstehen."""
    script = (await api.get("/app.js")).text
    assert "/api/devices/search" not in script
    assert "/api/rooms/rename" in script
```

`api` ist hier die Fixture aus `tests/api/test_web.py`; deren Rückgabewert prüfen (in dieser Datei ist es ein einzelner Client, nicht das Vierer-Tupel aus `test_devices.py`) und die Aufrufe entsprechend schreiben.

**Was diese Tests bewusst NICHT prüfen:** dass `saveRoom` beim Speichern eines Raums kein `label` mitschickt. Ein Textvergleich im ausgelieferten Skript könnte das nur raten, und die Frage ist ohnehin auf der Serverseite belegt — `test_patching_only_the_room_leaves_the_label_alone` und `test_patching_the_room_does_not_make_the_device_pending` aus Task 5 prüfen genau das Verhalten, statt seine Schreibweise.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web.py -k "room or search" -v`
Expected: FAIL — `assert "roomChips(" in script`.

- [ ] **Step 3: Zustand ergänzen**

In `app.js`, im Zustandsobjekt neben `labelDrafts` (Zeile ~346):

```javascript
    labelDrafts: {},
    deviceActionError: null,

    // --- Raeume, Filter, Suche (Entwurf Geraete-Tab, 2026-09-05) ----------
    //
    // DREI Zustaende, nicht zwei, und der Unterschied zwischen den letzten
    // beiden ist der Grund fuer die Kodierung:
    //   null  = "Alle"
    //   ""    = "Ohne Raum" (die Geraete, deren `device.room` NULL ist)
    //   "Bad" = dieser eine Raum
    // "Ohne Raum" ist eine echte Auswahl und muss von "Alle" unterscheidbar
    // bleiben - `null` fuer beide zu verwenden waere der naheliegende und
    // falsche Weg gewesen, weil `device.room` selbst `null` ist. Der
    // Leerstring kann mit keinem echten Raum kollidieren: `set_room` trimmt
    // und macht aus einem leeren Namen NULL, ein Raum namens "" kann also
    // gar nicht entstehen. Er ist ausserdem genau der Wert, den die API
    // fuer "Raum entfernen" erwartet - dieselbe Kodierung auf beiden
    // Seiten, nicht zwei.
    //
    // Bewusst NICHT in localStorage: ein gemerkter Filter erzeugt sonst den
    // Moment, in dem nach zwei Wochen drei von zwoelf Geraeten dastehen und
    // niemand mehr weiss, warum. Nach einem Neuladen steht die Ansicht
    // wieder auf "Alle".
    roomFilter: null,
    deviceSearch: "",
    // Welche Kachel gerade ein Textfeld fuer einen neuen Raumnamen zeigt
    // (Geraete-ID oder null) - der Zustand haengt an der Kachel, nicht
    // global, damit zwei offene Kacheln sich nicht gegenseitig schliessen.
    newRoomFor: null,
    newRoomDraft: "",
```

Und im Einlern-Block neben `commissionThreadDataset`:

```javascript
    commissionRoom: "",
    commissionNewRoom: "",
```

- [ ] **Step 4: Helfer ergänzen**

Direkt nach `remainingSignalCount` (Zeile ~890) einfügen:

```javascript
    // --- Kategorie, Raeume, Sortierung -----------------------------------

    // Der uebersetzte Name der Kategorie. Die API liefert nur die Kennung
    // ("socket"), damit die Suche unten gegen den Text vergleichen kann,
    // den der Bedienende tatsaechlich sieht - auf Deutsch "Steckdose", auf
    // Englisch "socket".
    categoryLabel(device) {
      return t("web.devices.category." + (device.category || "other"));
    },

    // Der Raum eines Geraets in der Kodierung von `roomFilter`: "" statt
    // null/undefined. Eine Stelle, damit die Umrechnung nicht in vier
    // Helfern einzeln steht und einer davon sie irgendwann anders macht.
    roomKeyOf(device) {
      return device.room || "";
    },

    // Alle Raeume mit ihrer Geraetezahl, "Ohne Raum" ganz am Ende.
    // `key` ist der Wert, den `roomFilter` annimmt ("" fuer Ohne Raum),
    // `label` der angezeigte Text.
    roomChips() {
      const counts = new Map();
      for (const device of this.devices) {
        const key = this.roomKeyOf(device);
        counts.set(key, (counts.get(key) || 0) + 1);
      }
      const chips = [...counts.keys()]
        .filter((key) => key !== "")
        .sort((a, b) => a.localeCompare(b))
        .map((key) => ({ key, label: key, count: counts.get(key) }));
      if (counts.has("")) {
        chips.push({ key: "", label: t("web.devices.room_none"), count: counts.get("") });
      }
      return chips;
    },

    // Die Leiste zeigt sich gar nicht, solange kein einziges Geraet einen
    // Raum traegt: bei drei Geraeten und keinem Raum waere sie eine Zeile
    // Laerm ueber einer Liste, die ohnehin auf einen Blick passt.
    hasAnyRoom() {
      return this.devices.some((device) => Boolean(device.room));
    },

    // Trifft der Suchbegriff dieses Geraet? Verglichen wird gegen Name,
    // uebersetzten Kategorienamen und Raumnamen.
    matchesSearch(device) {
      const needle = this.deviceSearch.trim().toLocaleLowerCase();
      if (!needle) {
        return true;
      }
      const haystack = [device.label, this.categoryLabel(device), this.roomKeyOf(device)]
        .join(" ")
        .toLocaleLowerCase();
      return haystack.includes(needle);
    },

    // Die sichtbaren Geraete: Raum-Chip und Suchfeld wirken ZUSAMMEN (UND).
    // Eine Suche greift also nur im gewaehlten Raum - den Fall "kein
    // Treffer hier, aber nebenan" faengt `hitsOutsideRoom()` unten ab.
    visibleDevices() {
      return this.devices.filter(
        (device) =>
          (this.roomFilter === null || this.roomKeyOf(device) === this.roomFilter) &&
          this.matchesSearch(device),
      );
    },

    // Wie viele Geraete der Suchbegriff AUSSERHALB des gewaehlten Raums
    // trifft. Nur dann von Belang, wenn im Raum selbst nichts uebrig
    // bleibt - sonst waere der Hinweis eine Ablenkung.
    hitsOutsideRoom() {
      if (this.roomFilter === null || !this.deviceSearch.trim()) {
        return 0;
      }
      return this.devices.filter(
        (device) => this.roomKeyOf(device) !== this.roomFilter && this.matchesSearch(device),
      ).length;
    },

    clearRoomFilter() {
      this.roomFilter = null;
    },

    // Die Geraete, nach Raum gruppiert und innerhalb eines Raums sortiert:
    // erst nach Kategorierang (alle Steckdosen beisammen, dann alle
    // Taster), darin alphabetisch nach Name.
    //
    // `localeCompare` statt `<`: sonst landete "Ärmelkanal" hinter "Zaun",
    // weil der Code-Punkt von "Ä" hinter dem von "Z" liegt.
    //
    // Bei einem gewaehlten Raum entsteht genau eine Gruppe, und ihr
    // `title` bleibt leer - es gibt nichts zu unterscheiden, und eine
    // Ueberschrift ueber der einzigen Gruppe waere Dopplung der Chip-Leiste.
    deviceGroups() {
      const byRoom = new Map();
      for (const device of this.visibleDevices()) {
        const key = this.roomKeyOf(device);
        if (!byRoom.has(key)) {
          byRoom.set(key, []);
        }
        byRoom.get(key).push(device);
      }
      const sortDevices = (devices) =>
        [...devices].sort(
          (a, b) =>
            (a.category_rank ?? 99) - (b.category_rank ?? 99) ||
            a.label.localeCompare(b.label),
        );
      const groups = [...byRoom.keys()]
        .filter((key) => key !== "")
        .sort((a, b) => a.localeCompare(b))
        .map((key) => ({ key, title: key, devices: sortDevices(byRoom.get(key)) }));
      if (byRoom.has("")) {
        groups.push({
          key: "",
          title: t("web.devices.room_none"),
          devices: sortDevices(byRoom.get("")),
        });
      }
      // Bei einem gewaehlten Raum gibt es nur eine Gruppe - ihre
      // Ueberschrift waere die Dopplung des aktiven Chips direkt darueber.
      if (this.roomFilter !== null) {
        return groups.map((group) => ({ ...group, title: "" }));
      }
      return groups;
    },

    // --- Leitwert (Kachel-Kopfzeile) --------------------------------------

    // Das erste funktionale Signal in der Reihenfolge, die
    // `firstSignalsFor` ohnehin liefert - also die der Profiltabelle.
    // Steckdose -> Zustand, Klimasensor -> Temperatur, Rollo -> Position.
    // Keine eigene Datenhaltung, keine Konfiguration: ein Geraet ohne
    // funktionale Signale hat schlicht keinen Leitwert, und die Kopfzeile
    // bleibt einzeilig.
    leadSignalFor(deviceId) {
      return this.firstSignalsFor(deviceId)[0] || null;
    },

    // Der Rest der Kurzliste. `FUNCTIONAL_PREVIEW_LIMIT` zaehlt den
    // Leitwert MIT (Entwurf 6.2), deshalb hier kein zweites Abschneiden -
    // `firstSignalsFor` hat es bereits getan.
    restSignalsFor(deviceId) {
      return this.firstSignalsFor(deviceId).slice(1);
    },

    // --- Raum eines Geraets aendern ---------------------------------------

    // Sendet AUSSCHLIESSLICH den Raum. Ein mitgeschicktes `label` liesse
    // `rename_device` laufen und setzte `updated_at` - das Geraet stuende
    // danach als "geaendert seit Export", obwohl der Raum in keiner
    // Vorlage landet (Entwurf 3.3).
    //
    // `value` ist bereits in derselben Kodierung wie `roomFilter`: "" heisst
    // "Ohne Raum", und genau das erwartet auch die API fuer "Raum
    // entfernen". Keine Umrechnung an dieser Stelle.
    async saveRoom(device, value) {
      this.deviceActionError = null;
      try {
        const updated = await this.request("PATCH", `/api/devices/${device.id}`, {
          room: value,
        });
        Object.assign(device, updated);
      } catch (error) {
        this.deviceActionError = `${t("web.devices.room_save_error")} ${error.message}`;
      }
    },

    beginNewRoom(device) {
      this.newRoomFor = device.id;
      this.newRoomDraft = "";
    },

    async commitNewRoom(device) {
      const name = this.newRoomDraft.trim();
      this.newRoomFor = null;
      this.newRoomDraft = "";
      if (name) {
        await this.saveRoom(device, name);
      }
    },

    // Benennt einen Raum an allen seinen Geraeten um. Die Rueckfrage vor
    // dem Zusammenfuehren steht hier und nicht im Server: nur die
    // Oberflaeche weiss, ob der Zielname schon belegt ist, ohne dafuer
    // eine zweite Abfrage zu stellen - die Geraeteliste liegt ihr vor.
    async renameRoom(room) {
      const target = window.prompt(t("web.devices.room_rename_prompt"), room);
      if (target === null) {
        return;
      }
      const name = target.trim();
      if (!name || name === room) {
        return;
      }
      const exists = this.devices.some((device) => device.room === name);
      if (exists && !window.confirm(t("web.devices.room_rename_merge_confirm"))) {
        return;
      }
      this.deviceActionError = null;
      try {
        await this.request("POST", "/api/rooms/rename", { from: room, to: name });
        if (this.roomFilter === room) {
          this.roomFilter = name;
        }
        await this.loadDevices();
      } catch (error) {
        this.deviceActionError = `${t("web.devices.room_rename_error")} ${error.message}`;
      }
    },
```

- [ ] **Step 5: Einlernen um den Raum erweitern**

In `commissionDevice()` den Rumpf des `body` ergänzen und das Zurücksetzen anpassen:

```javascript
        const body = { code: this.commissionCode.trim() };
        if (this.commissionThreadDataset.trim()) {
          body.thread_dataset = this.commissionThreadDataset.trim();
        }
        // Raum (Entwurf 6.7): "" heisst "Ohne Raum" und wird gar nicht erst
        // mitgeschickt; "__new__" ist der Sonderwert des Auswahlfelds, hinter
        // dem das Textfeld `commissionNewRoom` steht.
        const room =
          this.commissionRoom === "__new__"
            ? this.commissionNewRoom.trim()
            : this.commissionRoom.trim();
        if (room) {
          body.room = room;
        }
```

und weiter unten, beim Leeren der Felder:

```javascript
        this.commissionCode = "";
        this.commissionThreadDataset = "";
        // Der Raum bleibt BEWUSST stehen (Entwurf 6.7): wer vier Geraete in
        // der Kueche einlernt, waehlt ihn einmal. Ein Pairing-Code dagegen
        // ist nach Gebrauch wertlos und ein stehengebliebener waere eine
        // Fehlerquelle.
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_web.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/web/app.js tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): Raumfilter, Gruppierung, Kategoriesortierung und Suche

Raum-Chip und Suchfeld wirken zusammen (UND) - die Suche greift also nur
im gewaehlten Raum. Den Fall, den das erzeugt ("kein Treffer", obwohl das
Geraet nebenan steht), faengt `hitsOutsideRoom()` ab und bietet den
Sprung auf "Alle" an, ohne den Suchbegriff zu verlieren.

`saveRoom` sendet ausschliesslich den Raum. Ein mitgeschicktes Label
liesse `rename_device` laufen und markierte das Geraet als "geaendert
seit Export", obwohl der Raum in keiner Vorlage landet.

Der Filterzustand wird nicht gespeichert: ein gemerkter Filter erzeugt
sonst den Moment, in dem nach zwei Wochen drei von zwoelf Geraeten
dastehen und niemand mehr weiss, warum.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `index.html` und `style.css` — Raster, Kachel, Raumleiste, Icons

**Files:**
- Modify: `src/loxmatter/web/index.html` (Icon-Block Zeile 64-86, Geräte-Ansicht Zeile 176-340)
- Modify: `src/loxmatter/web/style.css` (`.device-card` Zeile 632-658, `.value-chips` Zeile 660-672, neue Regeln)
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: alle Methoden aus Task 7, alle Schlüssel aus Task 6.
- Produces: keine für spätere Tasks.

- [ ] **Step 1: Write the failing test**

An `tests/api/test_web.py` anhängen:

```python
async def test_the_page_offers_the_room_bar_and_the_room_picker(api):
    page = (await api.get("/")).text
    assert "roomChips()" in page
    assert "deviceGroups()" in page
    assert "leadSignalFor(" in page
    assert "saveRoom(" in page
    assert "deviceSearch" in page


async def test_every_category_has_an_icon_symbol(api):
    """Acht Kategorien, acht Symbole - "other" eingeschlossen. Ein fehlendes
    Symbol faellt im Browser NICHT auf: ein `<use>` auf eine unbekannte ID
    zeichnet stillschweigend nichts, keine Fehlermeldung. Deshalb faellt es
    hier auf."""
    page = (await api.get("/")).text
    for category in (
        "light",
        "socket",
        "switch",
        "covering",
        "climate",
        "sensor",
        "lock",
        "other",
    ):
        assert f'id="i-cat-{category}"' in page, category


async def test_the_device_grid_is_multi_column(api):
    css = (await api.get("/style.css")).text
    assert "auto-fill" in css
    assert "minmax(260px" in css
```

Zusätzlich: der bestehende Test `test_the_icons_are_well_formed_xml` (Zeile 141) muss die neuen Symbole mit abdecken — prüfen, ob er den gesamten Inline-Block parst; wenn ja, ist nichts zu tun außer wohlgeformtes SVG zu schreiben.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web.py -k "room_bar or icon or grid" -v`
Expected: FAIL — `assert "roomChips()" in page`.

- [ ] **Step 3: Icons ergänzen**

In `index.html`, in den bestehenden `<svg style="display: none">`-Block, sieben Symbole (Strich-Icons im Stil der vorhandenen, `viewBox="0 0 24 24"`, keine Füllung — `.icon` in `style.css` setzt `stroke: currentColor; fill: none`):

```html
      <!-- Kategorie-Icons (Entwurf Geraete-Tab, 2026-09-05, Abschnitt 6.5).
           Ein Symbol je Kategorie, "Sonstige" eingeschlossen - dort landet
           auch jedes Geraet, dessen Typen noch nicht nachgetragen sind.
           Weiterhin inline und ohne Icon-Bibliothek, aus demselben
           Grund wie das eingecheckte vendor/alpine.min.js: die Oberflaeche
           laeuft offline. -->
      <symbol id="i-cat-light" viewBox="0 0 24 24">
        <path d="M9 17.5a5.5 5.5 0 1 1 6 0V19H9v-1.5z" />
        <path d="M10 21.5h4" />
      </symbol>
      <symbol id="i-cat-socket" viewBox="0 0 24 24">
        <rect x="4" y="4" width="16" height="16" rx="3" />
        <path d="M9.5 10v2.2M14.5 10v2.2" />
        <path d="M8.4 13.6a4 4 0 0 0 7.2 0" />
      </symbol>
      <symbol id="i-cat-switch" viewBox="0 0 24 24">
        <rect x="6" y="3.5" width="12" height="17" rx="3" />
        <circle cx="12" cy="9" r="1.8" />
      </symbol>
      <symbol id="i-cat-covering" viewBox="0 0 24 24">
        <rect x="4" y="4" width="16" height="16" rx="2" />
        <path d="M4 9h16M4 13h16M4 17h16" />
      </symbol>
      <symbol id="i-cat-climate" viewBox="0 0 24 24">
        <path d="M10 13.2V5.5a2 2 0 1 1 4 0v7.7" />
        <circle cx="12" cy="16.5" r="3.2" />
      </symbol>
      <symbol id="i-cat-sensor" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="2.4" />
        <path d="M7.4 7.4a6.5 6.5 0 0 0 0 9.2M16.6 16.6a6.5 6.5 0 0 0 0-9.2" />
      </symbol>
      <symbol id="i-cat-lock" viewBox="0 0 24 24">
        <rect x="5" y="10.5" width="14" height="10" rx="2.5" />
        <path d="M8.5 10.5V7.8a3.5 3.5 0 0 1 7 0v2.7" />
      </symbol>
      <!-- ACHT Symbole, nicht sieben: die Kachel bildet die Kennung stur auf
           `#i-cat-<kennung>` ab, und `other` ist eine Kennung wie jede
           andere. Ein `<use>` auf eine nicht vorhandene ID zeichnet
           STILLSCHWEIGEND nichts - kein Fehler in der Konsole, nur eine
           Kachel ohne Icon. Deshalb bekommt "Sonstige" ein eigenes Symbol,
           statt sich auf eine Sonderbehandlung in JavaScript zu verlassen,
           die jemand beim naechsten Umbau uebersieht. Die Form ist dieselbe
           wie bei `#i-device`. -->
      <symbol id="i-cat-other" viewBox="0 0 24 24">
        <rect x="4" y="4" width="16" height="16" rx="3" />
        <circle cx="12" cy="12" r="2.2" />
      </symbol>
```

`#i-device` verliert damit seinen einzigen Nutzer (die alte Kachel-Kopfzeile, `index.html:220`). Es bleibt vorerst stehen; Task 9 prüft, ob es noch irgendwo referenziert wird, und entfernt es andernfalls.

- [ ] **Step 4: Die Geräte-Ansicht umbauen**

In `index.html` die Einlern-Karte um das Raumfeld ergänzen (in die bestehende `.row`, nach dem Thread-Feld):

```html
            <select x-model="commissionRoom">
              <option value="" x-text="t('web.devices.room_none')"></option>
              <template x-for="chip in roomChips().filter((c) => c.key !== '')" :key="chip.key">
                <option :value="chip.key" x-text="chip.key"></option>
              </template>
              <option value="__new__" x-text="t('web.devices.room_new')"></option>
            </select>
            <input
              x-show="commissionRoom === '__new__'"
              x-cloak
              type="text"
              x-model="commissionNewRoom"
              :placeholder="t('web.devices.room_new_placeholder')"
            />
```

und darunter, zu den vorhandenen `<p class="hint">`:

```html
          <p class="hint" x-text="t('web.devices.commission_room_hint')"></p>
```

Die Raumleiste, unmittelbar vor der Geräteliste (nach den Fehler-Bannern):

```html
        <!-- Raumleiste (Entwurf 6.3): zeigt sich gar nicht, solange kein
             einziges Geraet einen Raum traegt - bei drei Geraeten und keinem
             Raum waere sie eine Zeile Laerm ueber einer Liste, die ohnehin
             auf einen Blick passt. Das Suchfeld bleibt in dem Fall trotzdem
             erreichbar, weil es auch ohne Raeume ueber Name und Kategorie
             sucht. -->
        <div class="room-bar" x-show="devices.length > 0" x-cloak>
          <template x-if="hasAnyRoom()">
            <span class="room-chips">
              <button
                class="room-chip"
                :class="{ active: roomFilter === null }"
                @click="roomFilter = null"
                x-text="t('web.devices.room_all') + ' ' + devices.length"
              ></button>
              <template x-for="chip in roomChips()" :key="chip.key">
                <button
                  class="room-chip"
                  :class="{ active: roomFilter === chip.key }"
                  @click="roomFilter = chip.key"
                  x-text="chip.label + ' ' + chip.count"
                ></button>
              </template>
              <!-- `x-show="roomFilter"` ist hier genau richtig und kein
                   Schludern: falsy sind beide Faelle, in denen es nichts
                   umzubenennen gibt - `null` ("Alle") und `""` ("Ohne
                   Raum"). "Ohne Raum" ist kein Raum, sondern die Menge der
                   Geraete ohne Zuordnung; ein Name, den man aendern
                   koennte, ist gerade das, was ihnen fehlt. -->
              <button
                class="room-rename"
                x-show="roomFilter"
                @click="renameRoom(roomFilter)"
                :title="t('web.devices.room_rename')"
                x-text="'✎'"
              ></button>
            </span>
          </template>
          <span style="flex: 1 1 auto"></span>
          <input
            type="search"
            class="device-search"
            x-model="deviceSearch"
            :placeholder="t('web.devices.search_placeholder')"
          />
        </div>

        <p x-show="devices.length > 0 && visibleDevices().length === 0" x-cloak class="hint">
          <span x-text="t('web.devices.search_empty')"></span>
          <template x-if="hitsOutsideRoom() > 0">
            <span>
              <span x-text="hitsOutsideRoom()"></span>
              <span x-text="t('web.devices.search_hits_elsewhere')"></span>
              <a href="#" @click.prevent="clearRoomFilter()" x-text="t('web.devices.search_show_all_rooms')"></a>
            </span>
          </template>
        </p>
```

Die Geräteliste: das bestehende `<template x-for="device in devices">` wird zu zwei geschachtelten Schleifen — außen die Raumgruppen, innen das Raster:

```html
        <template x-for="group in deviceGroups()" :key="group.key">
          <div>
            <h3 class="room-heading" x-show="group.title" x-text="group.title"></h3>
            <div class="device-grid">
              <template x-for="device in group.devices" :key="device.id">
                <!-- die Kachel, siehe unten -->
              </template>
            </div>
          </div>
        </template>
```

Die Kachel selbst (ersetzt den bisherigen Inhalt von `.device-card`) — Kopfzeile mit Leitwert, Werteraster, Bedienleiste, Fußzeile mit Raumwahl:

```html
                <div class="card device-card" :class="deviceCardClass(device)">
                  <div class="device-head">
                    <span class="type-badge">
                      <svg class="icon"><use :href="'#i-cat-' + device.category"></use></svg>
                    </span>
                    <span class="device-ident">
                      <input
                        type="text"
                        class="device-name"
                        :value="device.label"
                        @input="labelDrafts[device.id] = $event.target.value"
                        @change="saveLabel(device)"
                      />
                      <!-- Ist eine Status-Pille faellig, verdraengt sie das
                           Leitwert-Label (Entwurf 6.2): der Zustand der
                           Kachel wiegt schwerer als die Beschriftung einer
                           Zahl, die zwei Zentimeter daneben steht. -->
                      <span class="status-pill warn" x-show="isOnline(device) && changedSinceExport(device.id)">
                        <svg class="icon"><use href="#i-warn"></use></svg>
                        <span x-text="t('web.devices.changed_since_export')"></span>
                      </span>
                      <span class="status-pill off" x-show="!isOnline(device)">
                        <svg class="icon"><use href="#i-offline"></use></svg>
                        <span x-text="t('web.devices.offline')"></span>
                      </span>
                      <span
                        class="lead-label"
                        x-show="isOnline(device) && !changedSinceExport(device.id) && leadSignalFor(device.id)"
                        x-text="leadSignalFor(device.id)?.title"
                      ></span>
                    </span>
                    <span class="lead-value" x-show="leadSignalFor(device.id)">
                      <span
                        :class="{ 'value-fresh': signalIsFresh(leadSignalFor(device.id)) }"
                        :title="signalAgeTitle(leadSignalFor(device.id))"
                        x-text="formatValue(liveValueOf(leadSignalFor(device.id)))"
                      ></span>
                      <small x-text="leadSignalFor(device.id)?.unit"></small>
                    </span>
                  </div>

                  <div class="value-rows" x-show="signalsByDevice[device.id]">
                    <template x-for="signal in restSignalsFor(device.id)" :key="signal.key">
                      <span class="value-row">
                        <span class="value-key" x-text="signal.title"></span>
                        <span
                          class="value"
                          :class="{ 'value-fresh': signalIsFresh(signal) }"
                          :title="signalAgeTitle(signal)"
                          x-text="formatValue(liveValueOf(signal)) + (signal.unit ? ' ' + signal.unit : '')"
                        ></span>
                      </span>
                    </template>
                    <!-- Der Hinweis auf die restlichen Signale ist die letzte
                         Rasterzeile statt eines eigenen Absatzes (Entwurf
                         6.2) - auf einer 260 px breiten Kachel zaehlt jede
                         Zeile. -->
                    <span class="value-row" x-show="remainingSignalCount(device.id) > 0">
                      <a
                        class="value-key"
                        href="#"
                        @click.prevent="selectView('signals')"
                        x-text="'+ ' + remainingSignalCount(device.id) + ' ' + t('web.devices.more_signals_short')"
                      ></a>
                      <span></span>
                    </span>
                  </div>
                  <p class="hint" x-show="!signalsByDevice[device.id]" x-text="t('web.devices.signals_loading')"></p>

                  <div class="device-commands">
                    <template x-for="command in commandsFor(device.id)" :key="command.key">
                      <span class="row">
                        <button
                          x-show="!command.takes_value"
                          @click="executeCommand(device, command)"
                          :disabled="commandBusyKey === command.key || !isOnline(device)"
                          x-text="command.slug"
                        ></button>
                        <span x-show="command.takes_value" class="row">
                          <span x-text="command.slug"></span>
                          <input
                            type="number"
                            style="width: 4.5rem"
                            :placeholder="t('web.devices.value_placeholder')"
                            @input="commandValueDrafts[command.key] = $event.target.value"
                          />
                          <button
                            @click="executeCommand(device, command)"
                            :disabled="commandBusyKey === command.key || !isOnline(device)"
                            x-text="t('web.devices.send')"
                          ></button>
                        </span>
                      </span>
                    </template>
                    <span
                      class="hint"
                      x-show="hiddenRawCommandsFor(device.id) > 0"
                      x-text="'+' + hiddenRawCommandsFor(device.id) + ' ' + t('web.devices.more_commands_short')"
                    ></span>
                  </div>

                  <div class="device-foot">
                    <select
                      class="room-select"
                      :value="device.room || ''"
                      @change="$event.target.value === '__new__' ? beginNewRoom(device) : saveRoom(device, $event.target.value)"
                    >
                      <option value="" x-text="t('web.devices.room_none')"></option>
                      <template x-for="chip in roomChips().filter((c) => c.key !== '')" :key="chip.key">
                        <option :value="chip.key" x-text="chip.key"></option>
                      </template>
                      <option value="__new__" x-text="t('web.devices.room_new')"></option>
                    </select>
                    <input
                      x-show="newRoomFor === device.id"
                      x-cloak
                      type="text"
                      class="room-new"
                      x-model="newRoomDraft"
                      :placeholder="t('web.devices.room_new_placeholder')"
                      @keydown.enter="commitNewRoom(device)"
                      @blur="commitNewRoom(device)"
                    />
                    <span class="hint" x-text="exportHintFor(device.id)"></span>
                    <span style="flex: 1 1 auto"></span>
                    <button
                      class="primary"
                      @click="exportDevice(device)"
                      :disabled="!bridgeSettings.bridge_ip"
                      :title="t('web.devices.export')"
                      x-text="'↓'"
                    ></button>
                    <button
                      class="danger"
                      @click="removeDevice(device)"
                      :title="t('web.devices.remove')"
                      x-text="'🗑'"
                    ></button>
                  </div>
                  <p class="hint" x-show="!bridgeSettings.bridge_ip" x-cloak>
                    <span x-text="t('web.devices.export_hint_prefix')"></span>
                    <a href="#" @click.prevent="selectView('settings')" x-text="t('web.settings.miniserver_link')"></a>
                    <span x-text="t('web.devices.export_hint_suffix')"></span>
                  </p>
                </div>
```

- [ ] **Step 5: `style.css` ergänzen**

Ans Ende von `style.css` anhängen (die vorhandenen `.device-card`-Regeln bleiben, `.value-chips` wird von `.value-rows` abgelöst — die alte Regel erst löschen, wenn kein Markup sie mehr nutzt: `.projectsync-unchanged-chips .value-chip` in Zeile 905 tut es noch, also bleibt `.value-chip` bestehen):

```css
/* Geraete-Tab: Raumleiste, Raster, Kachel (Entwurf 2026-09-05).
 *
 * Das Raster ist der eigentliche Punkt: eine Kachel war bisher so breit
 * wie das Fenster und so hoch wie ihr Inhalt - zwoelf Geraete waren zwoelf
 * Bildschirmhoehen. `auto-fill` mit einer Untergrenze statt eigener
 * Breakpoints: 260 px ist die Breite, ab der Kopfzeile samt Leitwert und
 * die Wertespalte nicht mehr umbrechen. Daraus ergeben sich vier Spalten
 * auf dem Desktop, zwei auf dem Tablet, eine auf dem Telefon, ohne dass
 * eine Media Query die Zahlen ein zweites Mal festlegt. */
.device-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 0.6rem;
  align-items: start;
}

.device-grid .device-card {
  margin-bottom: 0;
}

.room-bar {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
  margin-bottom: 0.8rem;
}

.room-chips {
  display: flex;
  gap: 0.3rem;
  flex-wrap: wrap;
  align-items: center;
}

.room-chip {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-muted);
  border-radius: 999px;
  padding: 0.15rem 0.7rem;
  font-size: 0.8rem;
}

.room-chip.active {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-contrast);
}

.room-rename,
.room-select,
.room-new {
  font-size: 0.75rem;
}

.device-search {
  min-width: 12rem;
  font-size: 0.8rem;
}

.room-heading {
  font-size: 0.75rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-muted);
  margin: 1rem 0 0.4rem;
}

/* Kopfzeile: Name links, Leitwert rechts, beide auf einer Sichtachse.
 * `min-width: 0` an der Mitte, damit ein langer Geraetename kuerzt statt
 * den Leitwert aus der Kachel zu schieben - ohne diese Zeile gewinnt in
 * einem Flex-Element der Inhalt gegen jede Breitenangabe. */
.device-head {
  display: flex;
  align-items: flex-start;
  gap: 0.4rem;
}

.device-ident {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}

.device-name {
  font-weight: 600;
  border: 1px solid transparent;
  background: transparent;
  padding: 0.1rem 0.2rem;
  width: 100%;
}

.device-name:hover,
.device-name:focus {
  border-color: var(--border);
  background: var(--surface);
}

.lead-label {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  padding-left: 0.2rem;
}

.lead-value {
  font-family: var(--mono);
  font-size: 1.35rem;
  font-weight: 600;
  line-height: 1.05;
  flex: 0 0 auto;
  white-space: nowrap;
}

.lead-value small {
  font-size: 0.7rem;
  font-weight: 400;
  color: var(--text-muted);
  margin-left: 0.1rem;
}

/* Werteraster statt Chips: die Werte fluchten ueber alle Kacheln hinweg in
 * einer Spalte, statt als unterschiedlich breite Chips zu maeandern - man
 * scannt eine Spalte statt zwoelf Bausteine. */
.value-rows {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.05rem 0.5rem;
  margin: 0.5rem 0;
}

.value-row {
  display: contents;
}

.value-key {
  font-size: 0.75rem;
  color: var(--text-muted);
}

.value-rows .value {
  font-family: var(--mono);
  font-size: 0.75rem;
  text-align: right;
}

.device-commands {
  display: flex;
  gap: 0.25rem;
  flex-wrap: wrap;
  align-items: center;
  padding-top: 0.45rem;
  border-top: 1px solid var(--border);
}

.device-foot {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  flex-wrap: wrap;
  margin-top: 0.5rem;
  padding-top: 0.45rem;
  border-top: 1px solid var(--border);
  font-size: 0.7rem;
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/api -q`
Expected: PASS. Bestehende Tests, die auf entfallenes Markup prüfen (etwa auf `web.devices.values_heading` oder `web.devices.controls_heading`, deren Überschriften in der kompakten Kachel wegfallen), schlagen hier an — sie gehören mit dem Markup angepasst, und die dadurch unbenutzten Schlüssel aus `strings.yaml` entfernt.

- [ ] **Step 7: In der laufenden Oberfläche ansehen**

```bash
uv run loxmatter run --miniserver 192.168.1.10
```

(Die Adresse ist die des eigenen Miniservers — dieselbe Zeile steht in `README.md:198`. `loxmatter run` gibt beim Start die Adresse der Oberfläche aus; diese im Browser öffnen und zur Ansicht „Geräte" wechseln.)

Fünf Dinge prüfen, die kein Test abdeckt:

1. Breites Fenster → vier Spalten, schmales → eine, ohne dass eine Kachel horizontal scrollt.
2. Die Raumleiste erscheint erst, sobald mindestens ein Gerät einen Raum hat.
3. „+ Neuer Raum …" in der Fußzeile blendet das Textfeld ein, Enter speichert.
4. Ein gewählter Raum-Chip zeigt den Stift, „Alle" und „Ohne Raum" zeigen ihn nicht.
5. Eine Suche im gewählten Raum ohne Treffer bietet „*n* weitere Treffer in anderen Räumen" an, und der Verweis behält den Suchbegriff.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): mehrspaltiges Kachelraster, Raumleiste und Kategorie-Icons

Das Raster ist der eigentliche Punkt: eine Kachel war so breit wie das
Fenster und so hoch wie ihr Inhalt - zwoelf Geraete waren zwoelf
Bildschirmhoehen. `auto-fill` mit 260 px Untergrenze statt eigener
Breakpoints; 260 px ist die Breite, ab der Kopfzeile samt Leitwert und
Wertespalte nicht mehr umbrechen.

Die Kachel behaelt ihren gesamten Inhalt (kein Aufklappen, wie im
Dashboard-Entwurf zugesagt) und ordnet ihn nur neu: Leitwert in der
Kopfzeile, Werte als fluchtendes Raster, Raumwahl in der Fussleiste. Ist
eine Status-Pille faellig, verdraengt sie das Leitwert-Label - der Zustand
wiegt schwerer als die Beschriftung einer Zahl, die daneben steht.

Acht Kategorie-Symbole, "Sonstige" eingeschlossen: die Kachel bildet
die Kennung stur auf `#i-cat-<kennung>` ab, und ein `<use>` auf eine
unbekannte ID zeichnet stillschweigend nichts. Damit ist offener Punkt 1
des Dashboard-Entwurfs erledigt.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Abschluss — volle Suite, Linting, Dokumentation

**Files:**
- Modify: `README.md` (Abschnitt zur Oberfläche, falls er die Geräteansicht beschreibt)
- Modify: `docs/superpowers/specs/2026-09-03-geraete-dashboard-und-export-design.md` (offener Punkt 1)

**Interfaces:**
- Consumes: alles.
- Produces: nichts.

- [ ] **Step 1: Volle Suite und Linting**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: PASS, keine Meldungen. Jeder Fehlschlag wird behoben, nicht unterdrückt.

- [ ] **Step 2: Ungenutzte Übersetzungsschlüssel entfernen**

Prüfen, welche `web.devices.*`-Schlüssel durch den Kachel-Umbau in Task 8 unbenutzt geworden sind:

```bash
for key in $(grep -o '^web\.devices\.[a-z_.]*' src/loxmatter/i18n/strings.yaml | tr -d ':'); do
  short="${key#web.}"
  grep -q "$key" src/loxmatter/web/index.html src/loxmatter/web/app.js || echo "UNBENUTZT: $key"
done
```

Jeden gemeldeten Schlüssel prüfen und entfernen, wenn ihn wirklich nichts mehr verwendet — ein Schlüssel ohne Fundstelle ist Text, den niemand mehr übersetzt sieht und der bei der nächsten Sprachdurchsicht Zeit kostet.

Dieselbe Frage für das alte Symbol `#i-device`, dessen einziger Nutzer die alte Kachel-Kopfzeile war:

```bash
grep -n "i-device" src/loxmatter/web/index.html src/loxmatter/web/app.js
```

Bleibt nur noch die Definition selbst übrig (`<symbol id="i-device">`), wird sie entfernt — `#i-cat-other` hat ihre Aufgabe übernommen.

- [ ] **Step 3: Offenen Punkt der Vorgänger-Spec schließen**

In `docs/superpowers/specs/2026-09-03-geraete-dashboard-und-export-design.md`, Abschnitt „Offene Punkte", Punkt 1 um einen Satz ergänzen:

```markdown
   **Erledigt** durch den [Geräte-Tab-Entwurf vom 5. September 2026](2026-09-05-geraete-tab-raeume-und-kachelraster-design.md):
   die Zuordnung ist `profiles/categories.py`, und sie liefert nicht nur das
   Icon, sondern auch die Sortierung innerhalb eines Raums und den
   Suchbegriff.
```

- [ ] **Step 4: README prüfen**

`README.md` nach Beschreibungen der Geräteansicht durchsehen (`grep -n -i "geräte\|devices" README.md`). Beschreibt er die Liste als einspaltig oder erwähnt er keine Räume, den Absatz anpassen — knapp, im Ton des umgebenden Textes, in der Sprache, in der der README aktuell vorliegt.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
docs: Geraete-Tab dokumentieren und offenen Icon-Punkt schliessen

Der Dashboard-Entwurf liess die Zuordnung Geraetetyp -> Icon offen. Sie
ist jetzt `profiles/categories.py` und traegt dort gleich drei Dinge:
Icon, Sortierung innerhalb eines Raums und Suchbegriff.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```
