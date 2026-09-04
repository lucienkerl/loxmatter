# Periodischer Resend als Opt-in - Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der periodische Voll-Resend (aktuell alle 300s, jeder bekannte Wert) wird durch eine explizite Opt-in-Auswahl pro Signal ersetzt, um den Rate-Limiter-Burst bei vielen Geräten zu verkleinern; das Intervall wird zur Laufzeit über die WebUI konfigurierbar.

**Architecture:** Ein neues `resend`-Flag pro Signal (Store, wie das bestehende `exported`-Flag) plus ein globales Intervall in der bereits vorhandenen `setting`-Tabelle (wie die Spracheinstellung). `Runtime.resend_all()` bleibt unverändert der volle Restore-Pfad für `/resync` und den Bridge-Start; eine neue `Runtime.resend_marked()` filtert auf das Flag und wird ausschließlich vom periodischen Timer aufgerufen, der das Intervall live aus dem Store liest.

**Tech Stack:** Python, SQLite (über `sqlite3`), FastAPI/Pydantic, Alpine.js (`web/app.js`/`web/index.html`), pytest (`asyncio_mode = auto`).

## Global Constraints

- Der Schlüssel eines Signals bleibt in jedem Fall unangetastet (Spec 6.2) - keine der hier beschriebenen Änderungen schreibt je in `signal.key`.
- Default für `resend`, bestehend wie neu registriert: `false` - nach diesem Update wird zunächst nichts mehr automatisch periodisch resent (Spec-Abschnitt 3).
- `Runtime.resend_all()` bleibt in Verhalten und Signatur unverändert - `/resync` (`server.py`) und der Bridge-Start (`cli.py`, nach `seed_from_snapshot`) müssen weiterhin JEDEN bekannten Wert wiederherstellen, unabhängig vom `resend`-Flag (Spec-Abschnitt 6, Korrektur vom 2026-09-04).
- Online-Status (`d<id>_online`), Pulszähler (`_n`-Suffix) und der Heartbeat (`bridge_alive`) bekommen kein `resend`-Flag und bleiben von dieser Änderung unberührt (Spec-Abschnitt 7).
- Kein CLI-Flag für das Intervall - ausschließlich über `GET`/`PATCH /api/settings/resend-interval` änderbar (Spec-Abschnitt 5/7).
- Neue Store-Klassen folgen dem Muster von `LocaleStore`/`BridgeSettingsStore`: eigenes Modul, eigene Klasse, Sicht auf dieselbe `setting`-Tabelle, kein zweiter Verbindungsaufbau.
- Kommentare/Docstrings in diesem Projekt sind deutsche Prosa, die das WARUM erklärt, nicht das WAS - neuer Code hält sich an diesen Stil (siehe existierende Dateien).

---

### Task 1: Store - `signal.resend`-Spalte, Flag und globale Schlüsselabfrage

**Files:**
- Modify: `src/loxmatter/model/store.py`
- Test: `tests/model/test_store.py`
- Test: `tests/model/test_store_migration.py`

**Interfaces:**
- Produces: `StoredSignal.resend: bool` (neues Feld), `Store.set_resend(key: str, resend: bool) -> None`, `Store.resend_keys() -> list[str]` (Schlüssel aller Signale mit `resend = true`, nur aktive Geräte).

- [ ] **Step 1: Schema, Migration und Dataclass-Feld ergänzen**

In `src/loxmatter/model/store.py`:

Die `signal`-Tabelle in `_SCHEMA` bekommt die neue Spalte (nach `functional`):

```python
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
    functional    INTEGER NOT NULL DEFAULT 1,
    resend        INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session (
    id         TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
"""
```

`_SCHEMA_VERSION` von 5 auf 6, mit einem Kommentar-Absatz analog zu den bestehenden:

```python
# ... (bestehender Kommentar bleibt) ... Version 6 (Entwurf periodischer
# Resend, 2026-09-04) fuegt `signal.resend` hinzu, siehe `_migrate_to_v6` -
# kein Backfill, jede Bestandszeile startet beim Spalten-Default (0/aus).
_SCHEMA_VERSION = 6
```

Neue Migrationsfunktion, direkt nach `_migrate_to_v5`:

```python
def _migrate_to_v6(db: sqlite3.Connection) -> None:
    """Fuegt `signal.resend` hinzu (periodischer Resend als Opt-in, Entwurf
    2026-09-04) - kein Backfill: jede Bestandszeile startet bei `resend = 0`,
    genau der Spalten-Default. Anders als `exported` (`_migrate_to_v1`) gibt
    es hier keinen Bestandswert, aus dem sich ein sinnvoller Vorgabewert
    ableiten liesse - im Gegenteil ist "aus" hier ausdruecklich die
    gewuenschte Vorgabe (siehe Entwurf, Abschnitt 3): der periodische
    Voll-Resend soll nach diesem Update fuer JEDES Signal erst durch eine
    bewusste Nutzerentscheidung wieder anspringen."""
    _add_column_if_missing(db, "signal", "resend", "INTEGER NOT NULL DEFAULT 0")
```

`_MIGRATIONS` um den neuen Eintrag ergänzen:

```python
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
    3: _migrate_to_v3,
    4: _migrate_to_v4,
    5: _migrate_to_v5,
    6: _migrate_to_v6,
}
```

`StoredSignal` bekommt das neue Feld (am Ende, nach `functional`):

```python
@dataclass(frozen=True)
class StoredSignal:
    key: str
    ref: SignalRef
    title: str
    unit: str
    exportability: Exportability
    device_id: int
    exported: bool
    functional: bool
    # resend (Entwurf periodischer Resend, 2026-09-04): ob dieses Signal vom
    # periodischen Timer erneut gesendet werden soll, auch wenn es sich
    # nicht geaendert hat - vom Nutzer umschaltbar (`PATCH
    # /api/signals/{key}`), unabhaengig von `exported`/`functional`. Betrifft
    # NUR `Runtime.resend_marked()` (den periodischen Timer); `resend_all()`
    # (fuer `/resync` und den Bridge-Start) ignoriert dieses Feld bewusst und
    # sendet weiterhin jeden bekannten Wert, siehe dortigen Docstring.
    resend: bool
```

`_as_signal` liest die neue Spalte:

```python
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
            functional=bool(row["functional"]),
            resend=bool(row["resend"]),
        )
```

Neue Methoden, direkt nach `set_exported`:

```python
    def set_resend(self, key: str, resend: bool) -> None:
        """Setzt das Resend-Flag eines Signals (`PATCH /api/signals/{key}`,
        Entwurf periodischer Resend, 2026-09-04). Wie `set_exported` ohne
        Existenzpruefung - siehe dort."""
        self._touch_owning_device(key)
        self._db.execute("UPDATE signal SET resend = ? WHERE key = ?", (int(resend), key))
        self._db.commit()
```

Direkt nach `signal_by_key` (beide lesen aus derselben Tabelle, gehören fachlich zusammen):

```python
    def resend_keys(self) -> list[str]:
        """Alle Signal-Schluessel mit `resend = true`, ueber alle AKTIVEN
        Geraete hinweg - fuer `Runtime.resend_marked()` (periodischer Resend
        als Opt-in, Entwurf 2026-09-04). Ein Signal eines entfernten Geraets
        (`forget_device`) taucht hier nicht mehr auf, genau wie bei
        `devices()`."""
        rows = self._db.execute(
            "SELECT signal.key FROM signal"
            " JOIN device ON device.id = signal.device_id"
            " WHERE signal.resend = 1 AND device.active = 1"
        ).fetchall()
        return [str(r["key"]) for r in rows]
```

- [ ] **Step 2: Failing Tests fuer `set_resend`/`resend_keys` schreiben**

An `tests/model/test_store.py` anhängen (nach `test_exported_flag_survives_reregistration`):

```python
def test_set_resend_toggles_the_flag_without_touching_the_key(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    target = signals[0]
    assert target.resend is False  # Vorgabewert (Entwurf, Abschnitt 3)

    store.set_resend(target.key, True)
    after = next(s for s in store.signals(device_id) if s.key == target.key)
    assert after.resend is True
    assert after.key == target.key


def test_resend_flag_survives_reregistration(store):
    """Wie `exported`: einmal vom Nutzer gesetzt, darf ein erneutes
    `register_signals` das Resend-Flag nicht zuruecksetzen."""
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    target = signals[0]

    store.set_resend(target.key, True)
    again = store.register_signals(device_id, snap)
    after = next(s for s in again if s.key == target.key)
    assert after.resend is True


def test_resend_keys_lists_only_flagged_signals(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    marked, other = signals[0], signals[1]
    store.set_resend(marked.key, True)

    keys = store.resend_keys()
    assert keys == [marked.key]
    assert other.key not in keys


def test_resend_keys_excludes_signals_of_a_removed_device(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    store.set_resend(signals[0].key, True)

    store.forget_device(device_id)

    assert store.resend_keys() == []
```

- [ ] **Step 3: Tests laufen lassen, bevor die Implementierung existiert**

Run: `pytest tests/model/test_store.py -k resend -v`
Expected: FAIL (`AttributeError: 'StoredSignal' object has no attribute 'resend'` bzw. `'Store' object has no attribute 'set_resend'`)

- [ ] **Step 4: Implementierung aus Step 1 eintragen, Tests gruen bekommen**

Run: `pytest tests/model/test_store.py -k resend -v`
Expected: PASS (4 Tests)

- [ ] **Step 5: Migrationstest schreiben**

An `tests/model/test_store_migration.py` anhängen (ans Dateiende, nach `test_migration_to_v5_adds_the_auth_tables_without_touching_devices`):

```python
def test_migration_to_v6_adds_the_resend_column_defaulting_to_off(tmp_path):
    """Eine Bestandsdatenbank auf Version 5 (vor `signal.resend`) bekommt die
    Spalte per Migration, jede Zeile startet bei `resend = 0` - kein
    Backfill, siehe `_migrate_to_v6`-Docstring."""
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
        assert user_version(path) == 6
        assert store.signal_by_key(key).resend is False
    finally:
        store.close()
```

- [ ] **Step 6: Migrationstest laufen lassen (muss vor Step 1 fehlschlagen, jetzt aber grün sein)**

Run: `pytest tests/model/test_store_migration.py -k v6 -v`
Expected: PASS

- [ ] **Step 7: Ganze Store-Testsuite laufen lassen**

Run: `pytest tests/model/ -v`
Expected: PASS (keine Regression in `exported`/`functional`/anderen Migrationen)

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/model/store.py tests/model/test_store.py tests/model/test_store_migration.py
git commit -m "$(cat <<'EOF'
feat(store): signal.resend-Flag fuer periodischen Resend als Opt-in

Neue Spalte (Migration v6, Default aus), StoredSignal.resend,
Store.set_resend und Store.resend_keys() fuer die globale Abfrage
markierter Signale ueber alle aktiven Geraete.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Store - `ResendSettingsStore` für das globale Intervall

**Files:**
- Create: `src/loxmatter/model/resend_settings_store.py`
- Modify: `src/loxmatter/model/store.py`
- Test: `tests/model/test_resend_settings_store.py`

**Interfaces:**
- Consumes: die generische `setting`-Tabelle (bereits vorhanden seit `_migrate_to_v5`/`_SCHEMA`).
- Produces: `ResendSettingsStore.get_interval_seconds() -> float`, `ResendSettingsStore.set_interval_seconds(seconds: float) -> None` (wirft `ValueError` unter `MIN_RESEND_INTERVAL_SECONDS`), Konstanten `DEFAULT_RESEND_INTERVAL_SECONDS = 300.0`, `MIN_RESEND_INTERVAL_SECONDS = 10.0`. Erreichbar als `Store.resend_settings`.

- [ ] **Step 1: Failing Tests schreiben**

Neue Datei `tests/model/test_resend_settings_store.py`:

```python
# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

"""Tests fuer `ResendSettingsStore` - das Intervall des periodischen
Resends, gehalten in derselben `setting`-Tabelle wie `LocaleStore.language`
(siehe dortiges test_locale_store.py fuer das gleiche Muster)."""

from __future__ import annotations

import pytest

from loxmatter.model.resend_settings_store import (
    DEFAULT_RESEND_INTERVAL_SECONDS,
    MIN_RESEND_INTERVAL_SECONDS,
)
from loxmatter.model.store import Store


def test_interval_defaults_on_a_fresh_store(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.resend_settings.get_interval_seconds() == DEFAULT_RESEND_INTERVAL_SECONDS
    finally:
        store.close()


def test_set_interval_persists_and_is_read_back(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.resend_settings.set_interval_seconds(60.0)
        assert store.resend_settings.get_interval_seconds() == 60.0
    finally:
        store.close()


def test_set_interval_rejects_a_value_below_the_minimum(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        with pytest.raises(ValueError):
            store.resend_settings.set_interval_seconds(MIN_RESEND_INTERVAL_SECONDS - 1)
        # Kein Teil-Erfolg: der Vorgabewert gilt weiterhin.
        assert store.resend_settings.get_interval_seconds() == DEFAULT_RESEND_INTERVAL_SECONDS
    finally:
        store.close()


def test_interval_survives_reopening_the_same_database(tmp_path):
    path = tmp_path / "t.sqlite"
    store = Store(path)
    try:
        store.resend_settings.set_interval_seconds(120.0)
    finally:
        store.close()

    reopened = Store(path)
    try:
        assert reopened.resend_settings.get_interval_seconds() == 120.0
    finally:
        reopened.close()
```

- [ ] **Step 2: Tests laufen lassen, bevor das Modul existiert**

Run: `pytest tests/model/test_resend_settings_store.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'loxmatter.model.resend_settings_store'`)

- [ ] **Step 3: `ResendSettingsStore` implementieren**

Neue Datei `src/loxmatter/model/resend_settings_store.py`:

```python
# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

"""Das Intervall des periodischen Resends - EINE Einstellung fuer die
gesamte Bruecke, zur Laufzeit ueber die WebUI/API aenderbar statt einer beim
Start fixierten Konstante. Siehe
docs/superpowers/specs/2026-09-04-periodischer-resend-design.md, Abschnitt 4.

Eigenes Modul und eigene Klasse, analog zu `locale_store.py`: die
`setting`-Tabelle ist generisch angelegt, genau damit weitere Konfiguration
wie diese hier denselben Weg gehen kann. Diese Klasse ist eine weitere Sicht
auf dieselbe Tabelle und dieselbe Verbindung, kein zweiter Verbindungsaufbau."""

from __future__ import annotations

import sqlite3

_INTERVAL_KEY = "resend_interval_seconds"

DEFAULT_RESEND_INTERVAL_SECONDS = 300.0
# Untergrenze (Entwurf, Abschnitt 5): schuetzt vor einem versehentlich zu
# kurzen Intervall, das bei vielen markierten Signalen genau den Burst
# erzeugen wuerde, den dieser Entwurf eigentlich vermeiden soll.
MIN_RESEND_INTERVAL_SECONDS = 10.0


class ResendSettingsStore:
    """Zugriff auf `setting` ueber die Verbindung des Stores - wie
    `LocaleStore`, nur fuer den Schluessel `"resend_interval_seconds"`."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_interval_seconds(self) -> float:
        """Der gespeicherte Wert - `DEFAULT_RESEND_INTERVAL_SECONDS`, solange
        nichts gespeichert ist. Wirft nie."""
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_INTERVAL_KEY,)
        ).fetchone()
        if row is None:
            return DEFAULT_RESEND_INTERVAL_SECONDS
        return float(row["value"])

    def set_interval_seconds(self, seconds: float) -> None:
        if seconds < MIN_RESEND_INTERVAL_SECONDS:
            raise ValueError(
                f"Resend-Intervall muss mindestens {MIN_RESEND_INTERVAL_SECONDS}s betragen, "
                f"bekommen: {seconds}"
            )
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_INTERVAL_KEY, str(seconds)),
        )
        self._db.commit()
```

In `src/loxmatter/model/store.py` den Import ergänzen (bei den übrigen `loxmatter.model.*`-Importen):

```python
from loxmatter.model.locale_store import LocaleStore
from loxmatter.model.resend_settings_store import ResendSettingsStore
from loxmatter.model.settings_store import BridgeSettingsStore
```

Und in `Store.__init__`, direkt nach `self.locale = LocaleStore(self._db)`:

```python
        # Sicht auf dieselbe Verbindung - siehe `resend_settings_store.py`.
        self.resend_settings = ResendSettingsStore(self._db)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `pytest tests/model/test_resend_settings_store.py -v`
Expected: PASS (4 Tests)

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/model/resend_settings_store.py src/loxmatter/model/store.py tests/model/test_resend_settings_store.py
git commit -m "$(cat <<'EOF'
feat(store): ResendSettingsStore fuer das globale Resend-Intervall

Analog zu LocaleStore/BridgeSettingsStore: eigene Sicht auf die
generische setting-Tabelle, Default 300s, Untergrenze 10s.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: API - `resend`-Flag pro Signal lesen und setzen

**Files:**
- Modify: `src/loxmatter/api/models.py`
- Modify: `src/loxmatter/api/devices.py`
- Test: `tests/api/test_devices.py`

**Interfaces:**
- Consumes: `StoredSignal.resend` (Task 1), `Store.set_resend` (Task 1).
- Produces: `SignalOut.resend: bool`, `SignalPatch.resend: bool | None`.

- [ ] **Step 1: Failing Tests schreiben**

An `tests/api/test_devices.py` anhängen (nach `test_exporting_a_signal_can_be_turned_off`):

```python
async def test_the_signal_payload_says_whether_resend_is_flagged(api):
    """Periodischer Resend als Opt-in (Entwurf 2026-09-04) - Vorgabewert aus."""
    client, _, device_id, _ = api
    signals = (await client.get(f"/api/devices/{device_id}/signals")).json()
    assert signals
    assert all(s["resend"] is False for s in signals)


async def test_resend_can_be_turned_on_through_the_api(api):
    client, store, device_id, _ = api
    key = store.signals(device_id)[0].key
    response = await client.patch(f"/api/signals/{key}", json={"resend": True})
    assert response.status_code == 200
    assert response.json()["resend"] is True
    assert next(s for s in store.signals(device_id) if s.key == key).resend is True


async def test_resend_and_exported_are_independent_fields(api):
    client, store, device_id, _ = api
    key = store.signals(device_id)[0].key
    await client.patch(f"/api/signals/{key}", json={"exported": False})

    response = await client.patch(f"/api/signals/{key}", json={"resend": True})

    body = response.json()
    assert body["resend"] is True
    assert body["exported"] is False
```

- [ ] **Step 2: Tests laufen lassen, bevor das Feld existiert**

Run: `pytest tests/api/test_devices.py -k resend -v`
Expected: FAIL (`KeyError: 'resend'` beim Zugriff auf `signals[...]["resend"]`, da `SignalOut` das Feld noch nicht kennt)

- [ ] **Step 3: `SignalOut`/`SignalPatch` erweitern**

In `src/loxmatter/api/models.py`, `SignalOut` (Feld am Ende ergänzen, Docstring-Absatz anfügen):

```python
class SignalOut(BaseModel):
    """`exportable`/`reason` (Spec 6.6) und `exported` (vom Nutzer umschaltbar,
    siehe `model.store.StoredSignal.exported`) sagen, was TECHNISCH auf einen
    Loxone-Eingang passt und was DAVON in den naechsten Export soll -
    `functional` (Aufgabe 8) beantwortet eine dritte, unabhaengige Frage: ob
    `profiles.relevance.is_functional` dieses Signal fuer den GERAETETYP als
    gewollt einstuft. Die Oberflaeche nutzt allein dieses Feld, um die
    Signalliste in "Funktional" und "Experte" zu gliedern (`api.devices.
    _signal_out` liest es unveraendert aus `StoredSignal.functional`) - eine
    zweite Berechnung der Regel gibt es weder in der API-Schicht noch in
    JavaScript.

    `resend` (Entwurf periodischer Resend, 2026-09-04) ist eine VIERTE,
    wieder unabhaengige Frage: ob der periodische Timer (`Runtime.
    resend_marked`) dieses Signal auch ohne Aenderung erneut senden soll.
    Betrifft `/resync` und den Bridge-Start (`Runtime.resend_all`) nicht -
    die ignorieren dieses Feld bewusst, siehe dortigen Docstring."""

    model_config = ConfigDict(frozen=True)

    key: str
    path: str
    kind: str
    title: str
    unit: str
    value: float | bool | str | None
    exportable: bool
    reason: str | None
    exported: bool
    functional: bool
    resend: bool
```

`SignalPatch` um das optionale Feld ergänzen:

```python
class SignalPatch(BaseModel):
    """Was sich an einem Signal ueberhaupt aendern laesst.

    Spec 6.2: der Schluessel ist die Verdrahtung in Loxone. Waere er hier
    aenderbar, koennte ein Klick in der Oberflaeche einen Baustein im Haus
    still totlegen - deshalb kennt dieses Modell gar kein `key`-Feld. Ein
    mitgeschicktes `key` landet bei Pydantic niemals auf dem Objekt und wird
    von `devices.rename_signal` entsprechend nie gelesen, geschweige denn
    angewendet - das ist keine Frage der Sorgfalt im Handler, sondern eine,
    die dieses Modell strukturell unmoeglich macht. Grund ist Pydantic v2s
    eigener Default fuer unbekannte Felder, `extra="ignore"` (Berichtigung
    M1, Review 2026-09-02: hier stand faelschlich `extra="allow"` als
    Default - das Gegenteil, es wuerde unbekannte Felder gerade behalten).
    """

    model_config = ConfigDict(frozen=True)

    title: str | None = None
    exported: bool | None = None
    resend: bool | None = None
```

- [ ] **Step 4: `_signal_out`/`rename_signal` in `devices.py` erweitern**

In `src/loxmatter/api/devices.py`, `_signal_out`:

```python
def _signal_out(signal: StoredSignal, values: dict[str, float | bool]) -> SignalOut:
    """..."""  # Docstring unveraendert
    exportable = is_exportable(signal.exportability)
    reason = None if exportable else _UNEXPORTABLE_REASONS.get(signal.exportability)
    return SignalOut(
        key=signal.key,
        path=signal.ref.path,
        kind=signal.ref.kind.value,
        title=signal.title,
        unit=signal.unit,
        value=values.get(signal.key),
        exportable=exportable,
        reason=reason,
        exported=signal.exported,
        functional=signal.functional,
        resend=signal.resend,
    )
```

In `rename_signal`, nach der `exported`-Zeile:

```python
        if patch.title is not None:
            store.set_title(key, patch.title)
        if patch.exported is not None:
            store.set_exported(key, patch.exported)
        if patch.resend is not None:
            store.set_resend(key, patch.resend)
```

- [ ] **Step 5: Tests laufen lassen**

Run: `pytest tests/api/test_devices.py -v`
Expected: PASS (alle Tests dieser Datei, keine Regression bei `exported`/`functional`)

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/api/models.py src/loxmatter/api/devices.py tests/api/test_devices.py
git commit -m "$(cat <<'EOF'
feat(api): resend-Flag pro Signal ueber PATCH /api/signals/{key}

SignalOut/SignalPatch bekommen das vierte, von exported/functional
unabhaengige Feld - gleiches Muster wie exported.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: API - Resend-Intervall lesen und setzen

**Files:**
- Modify: `src/loxmatter/api/models.py`
- Modify: `src/loxmatter/api/settings.py`
- Test: `tests/api/test_settings_api.py`

**Interfaces:**
- Consumes: `Store.resend_settings` (Task 2).
- Produces: `GET /api/settings/resend-interval` und `PATCH /api/settings/resend-interval`, Antwortmodell `{"interval_seconds": float}`.

- [ ] **Step 1: Failing Tests schreiben**

An `tests/api/test_settings_api.py` anhängen (Import am Dateianfang ergänzen, Tests ans Ende):

```python
from loxmatter.model.resend_settings_store import (
    DEFAULT_RESEND_INTERVAL_SECONDS,
    MIN_RESEND_INTERVAL_SECONDS,
)
```

```python
async def test_a_fresh_installation_has_the_default_resend_interval(api):
    client, _ = api
    body = (await client.get("/api/settings/resend-interval")).json()
    assert body["interval_seconds"] == DEFAULT_RESEND_INTERVAL_SECONDS


async def test_patch_saves_and_returns_the_new_interval(api):
    client, _ = api
    response = await client.patch(
        "/api/settings/resend-interval", json={"interval_seconds": 60.0}
    )
    assert response.status_code == 200
    assert response.json()["interval_seconds"] == 60.0


async def test_a_later_get_sees_what_patch_saved_for_the_interval(api):
    client, _ = api
    await client.patch("/api/settings/resend-interval", json={"interval_seconds": 45.0})
    body = (await client.get("/api/settings/resend-interval")).json()
    assert body["interval_seconds"] == 45.0


async def test_an_interval_below_the_minimum_yields_422(api):
    client, _ = api
    response = await client.patch(
        "/api/settings/resend-interval",
        json={"interval_seconds": MIN_RESEND_INTERVAL_SECONDS - 1},
    )
    assert response.status_code == 422


async def test_a_non_positive_interval_yields_422(api):
    client, _ = api
    response = await client.patch("/api/settings/resend-interval", json={"interval_seconds": 0})
    assert response.status_code == 422


async def test_resend_interval_route_requires_a_session(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/settings/resend-interval")
    store.close()
    assert response.status_code == 401
```

- [ ] **Step 2: Tests laufen lassen, bevor die Route existiert**

Run: `pytest tests/api/test_settings_api.py -k resend_interval -v`
Expected: FAIL (404, da die Route noch nicht existiert)

- [ ] **Step 3: Modelle ergänzen**

In `src/loxmatter/api/models.py`, direkt nach `BridgeSettingsIn`:

```python
class ResendIntervalOut(BaseModel):
    """Antwort von `GET`/`PATCH /api/settings/resend-interval` (Entwurf
    periodischer Resend, 2026-09-04, Abschnitt 5)."""

    model_config = ConfigDict(frozen=True)

    interval_seconds: float


class ResendIntervalIn(BaseModel):
    """Rumpf von `PATCH /api/settings/resend-interval`. `gt=0` faengt einen
    nicht-positiven Wert bereits hier ab (422 ohne eigenen Validator); die
    tatsaechliche Untergrenze (`MIN_RESEND_INTERVAL_SECONDS`) prueft
    `ResendSettingsStore.set_interval_seconds` selbst, siehe dort."""

    model_config = ConfigDict(frozen=True)

    interval_seconds: float = Field(gt=0)
```

- [ ] **Step 4: Route in `api/settings.py` ergänzen**

`src/loxmatter/api/settings.py` komplett:

```python
# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

"""Verbindungseinstellungen der Bruecke (IP, Ports) und das Intervall des
periodischen Resends ueber die API - Geraete-Dashboard-Entwurf
(2026-09-03), Abschnitt 4, und Entwurf periodischer Resend (2026-09-04),
Abschnitt 5.

`build_settings_router` baut einen `APIRouter` mit Praefix `/api`, genau wie
`api.devices.build_device_router` - eingebunden in `loxone.server.build_app`
neben den uebrigen Routern dieser Phase, hinter demselben `api_guard`."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from loxmatter.api.models import (
    BridgeSettingsIn,
    BridgeSettingsOut,
    ResendIntervalIn,
    ResendIntervalOut,
)
from loxmatter.model.store import Store


def _settings_out(store: Store) -> BridgeSettingsOut:
    settings = store.settings.get()
    return BridgeSettingsOut(
        bridge_ip=settings.bridge_ip,
        udp_port=settings.udp_port,
        listen_port=settings.listen_port,
        saved_at=settings.saved_at,
    )


def _resend_interval_out(store: Store) -> ResendIntervalOut:
    return ResendIntervalOut(interval_seconds=store.resend_settings.get_interval_seconds())


def build_settings_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/settings")
    async def get_settings() -> BridgeSettingsOut:
        return _settings_out(store)

    @router.patch("/settings")
    async def save_settings(patch: BridgeSettingsIn) -> BridgeSettingsOut:
        store.settings.save(
            bridge_ip=patch.bridge_ip,
            udp_port=patch.udp_port,
            listen_port=patch.listen_port,
        )
        return _settings_out(store)

    @router.get("/settings/resend-interval")
    async def get_resend_interval() -> ResendIntervalOut:
        return _resend_interval_out(store)

    @router.patch("/settings/resend-interval")
    async def save_resend_interval(patch: ResendIntervalIn) -> ResendIntervalOut:
        try:
            store.resend_settings.set_interval_seconds(patch.interval_seconds)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _resend_interval_out(store)

    return router
```

- [ ] **Step 5: Tests laufen lassen**

Run: `pytest tests/api/test_settings_api.py -v`
Expected: PASS (alle Tests dieser Datei, keine Regression bei `/api/settings`)

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/api/models.py src/loxmatter/api/settings.py tests/api/test_settings_api.py
git commit -m "$(cat <<'EOF'
feat(api): GET/PATCH /api/settings/resend-interval

Liest/setzt das globale Resend-Intervall ueber ResendSettingsStore,
422 bei einem Wert unter der Untergrenze.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Runtime - `resend_marked()` neben unverändertem `resend_all()`

**Files:**
- Modify: `src/loxmatter/loxone/runtime.py`
- Test: `tests/loxone/test_runtime.py`

**Interfaces:**
- Consumes: `Store.resend_keys()` (Task 1).
- Produces: `Runtime.resend_marked() -> int` (async), gemeinsam mit `resend_all()` gestützt auf eine neue private `Runtime._force_resend(keys: Sequence[str]) -> int`.

- [ ] **Step 1: Failing Tests schreiben**

An `tests/loxone/test_runtime.py` anhängen (nach `test_resend_of_an_empty_runtime_sends_nothing`):

```python
async def test_resend_marked_only_sends_flagged_signals(environment):
    runtime, sender, store, device_id, _ = environment
    voltage_key = f"d{device_id}_2_voltage"
    current_key = f"d{device_id}_2_current"
    await runtime.on_attribute(device_id, "2/144/4", 230000)  # voltage
    await runtime.on_attribute(device_id, "2/144/5", 100)  # current
    store.set_resend(voltage_key, True)
    sender.sent.clear()

    count = await runtime.resend_marked()

    assert count == 1
    assert sender.keys() == [voltage_key]
    assert sender.sent[0][2] is True
    assert current_key not in sender.keys()


async def test_resend_marked_of_no_flagged_signals_sends_nothing(environment):
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    sender.sent.clear()

    assert await runtime.resend_marked() == 0
    assert sender.sent == []


async def test_resend_all_ignores_the_resend_flag_and_sends_everything(environment):
    """/resync und der Bruecken-Start verlassen sich auf `resend_all()` als
    vollstaendige Zustands-Wiederherstellung (Spec 6.4) - das `resend`-Flag
    (Entwurf periodischer Resend, Abschnitt 6) darf das NICHT einschraenken,
    sonst blieben nach einem Miniserver-Neustart die meisten virtuellen
    Eingaenge auf ihrem Defaultwert stehen."""
    runtime, sender, store, device_id, _ = environment
    voltage_key = f"d{device_id}_2_voltage"
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert store.signal_by_key(voltage_key).resend is False  # Vorgabewert
    sender.sent.clear()

    count = await runtime.resend_all()

    assert count == 1
    assert sender.keys() == [voltage_key]
```

- [ ] **Step 2: Tests laufen lassen, bevor `resend_marked` existiert**

Run: `pytest tests/loxone/test_runtime.py -k resend_marked -v`
Expected: FAIL (`AttributeError: 'Runtime' object has no attribute 'resend_marked'`)

- [ ] **Step 3: `resend_all`/`resend_marked`/`_force_resend` in `runtime.py` implementieren**

In `src/loxmatter/loxone/runtime.py` den bestehenden `resend_all` ersetzen durch:

```python
    async def resend_all(self) -> int:
        """Schickt JEDEN bekannten Wert erneut, an der Entprellung vorbei -
        unabhaengig vom `resend`-Flag (Entwurf periodischer Resend,
        2026-09-04, Abschnitt 6). Bleibt bewusst unveraendert der volle
        Restore-Pfad fuer `/resync` (`loxone.server`) und den Bruecken-Start
        (`cli.py`, direkt nach `seed_from_snapshot`) - beide muessen nach
        einem Miniserver-Neustart JEDEN virtuellen Eingang wiederherstellen
        (Spec 6.4), unabhaengig davon, ob jemand das Signal fuer den
        periodischen Timer markiert hat. Der periodische Timer selbst ruft
        stattdessen `resend_marked()` auf, siehe dort.

        Iteriert nur die Schluessel als Momentaufnahme, liest den Wert aber
        JE SCHLUESSEL erst unmittelbar vor dem Senden aus `_last_values`
        nach (Review-Fix I4, 2026-09-02). Der alte Code erfasste `(key,
        value)`-Paare gemeinsam als eine Momentaufnahme und wartete dann -
        durch die Entprellung im `UdpSender` - bis zu ein paar Sekunden fuer
        rund 110 Signale. Eine gleichzeitige Aktualisierung waehrend dieser
        Zeit schrieb ihren neuen Wert schon in `_last_values` und schickte
        ihn selbst sofort, aber der lang laufende Resend traf mit seiner
        laengst veralteten Momentaufnahme danach noch einmal ein und
        ueberschrieb den frischen Wert in Loxone wieder mit dem alten. Der
        Fehler heilt sich erst beim naechsten echten Update selbst - aber
        der Ausloeser hier ist `/resync`, verdrahtet an den
        Systemstart-Baustein, und feuert also genau dann, wenn jemand
        zusieht.
        """
        return await self._force_resend(list(self._last_values))

    async def resend_marked(self) -> int:
        """Wie `resend_all`, aber nur fuer Signale mit `resend = true`
        (Entwurf periodischer Resend, 2026-09-04, Abschnitt 6) - der
        Gegenpart zu `resend_all`s bewusster Ignoranz dieses Flags. Nur
        `_resend_loop` ruft diese Methode auf."""
        keys = self._store.resend_keys()
        return await self._force_resend(keys)

    async def _force_resend(self, keys: Sequence[str]) -> int:
        """Gemeinsamer Kern von `resend_all`/`resend_marked` - siehe
        `resend_all` fuer die Begruendung, warum der Wert JE SCHLUESSEL erst
        unmittelbar vor dem Senden aus `_last_values` nachgelesen wird
        (Review-Fix I4)."""
        count = 0
        for key in keys:
            value = self._last_values.get(key)
            if value is None:
                # Zwischen der Momentaufnahme der Schluessel oben und diesem
                # Zugriff kann ein Schluessel theoretisch verschwunden sein -
                # praktisch nie, aber `_last_values` kennt kein Loeschen, nur
                # Ueberschreiben. Sicherer Ueberspringen statt eines
                # `None`-Werts auf der Leitung.
                continue
            # Bewusst kein `_notify_observers(...)` hier (Review-Fix Minor
            # #3, 2026-09-02): ein Resend verschickt nur Werte, die ein
            # Beobachter (z. B. die WebUI) laengst als aktuell gesehen hat -
            # kein neuer Wert, also auch keine neue Benachrichtigung noetig.
            await self._sender.send(key, value, force=True)
            count += 1
        return count
```

(`Sequence` ist in dieser Datei bereits importiert: `from collections.abc import Callable, Sequence`.)

- [ ] **Step 4: Tests laufen lassen**

Run: `pytest tests/loxone/test_runtime.py -v`
Expected: PASS (alle Tests dieser Datei, insbesondere die bestehenden `resend_all`-Tests unverändert grün)

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/loxone/runtime.py tests/loxone/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(runtime): resend_marked() fuer den periodischen Timer

resend_all() bleibt unveraendert der volle Restore-Pfad fuer /resync
und den Bruecken-Start; resend_marked() filtert auf das resend-Flag
und teilt sich die Sende-Logik ueber die neue _force_resend().

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Runtime - `_resend_loop` mit live-konfigurierbarem Intervall

**Files:**
- Modify: `src/loxmatter/loxone/runtime.py`
- Test: `tests/loxone/test_runtime.py`

**Interfaces:**
- Consumes: `Runtime.resend_marked()` (Task 5), `Store.resend_settings.get_interval_seconds()` (Task 2).
- Produces: `Runtime.__init__(..., *, heartbeat_seconds: float = 30.0, resend_poll_seconds: float = 5.0)` - der bisherige Parameter `resend_seconds` entfällt ersatzlos (kein bestehender Aufrufer nutzt ihn, siehe Suche unten).

- [ ] **Step 1: Failing Tests schreiben**

An `tests/loxone/test_runtime.py` anhängen (nach den drei Tests aus Task 5):

```python
async def test_resend_loop_never_sends_an_unmarked_signal(environment):
    _, sender, store, device_id, _ = environment
    marked_key = f"d{device_id}_2_voltage"
    unmarked_key = f"d{device_id}_2_current"
    store.set_resend(marked_key, True)
    store.resend_settings.set_interval_seconds(0.01)

    runtime = Runtime(store, sender, resend_poll_seconds=0.02)
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    await runtime.on_attribute(device_id, "2/144/5", 100)
    sender.sent.clear()

    await runtime.start()
    await asyncio.sleep(0.09)
    await runtime.stop()

    forced = {k for k, _, forced in sender.sent if forced}
    assert marked_key in forced
    assert unmarked_key not in forced


async def test_resend_loop_reacts_to_a_lowered_interval_without_a_restart(environment):
    """Eine Aenderung ueber die WebUI (`PATCH /api/settings/resend-interval`)
    wirkt innerhalb weniger Sekunden, ohne Prozess-Neustart (Entwurf,
    Abschnitt 6)."""
    _, sender, store, device_id, _ = environment
    key = f"d{device_id}_2_voltage"
    store.set_resend(key, True)
    store.resend_settings.set_interval_seconds(10.0)

    runtime = Runtime(store, sender, resend_poll_seconds=0.02)
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    sender.sent.clear()

    await runtime.start()
    try:
        await asyncio.sleep(0.09)
        assert sender.keys() == []  # 10s-Intervall ist noch lange nicht um

        store.resend_settings.set_interval_seconds(0.01)
        await asyncio.sleep(0.09)
    finally:
        await runtime.stop()

    assert key in sender.keys()
```

- [ ] **Step 2: Tests laufen lassen, bevor die Schleife umgebaut ist**

Run: `pytest tests/loxone/test_runtime.py -k resend_loop -v`
Expected: FAIL (`TypeError: Runtime.__init__() got an unexpected keyword argument 'resend_poll_seconds'`)

- [ ] **Step 3: `__init__` und `_resend_loop` umbauen**

In `src/loxmatter/loxone/runtime.py`, `Runtime.__init__`:

```python
    def __init__(
        self,
        store: Store,
        sender: Sender,
        *,
        heartbeat_seconds: float = 30.0,
        resend_poll_seconds: float = 5.0,
    ) -> None:
        self._store = store
        self._sender = sender
        self._heartbeat_seconds = heartbeat_seconds
        self._resend_poll_seconds = resend_poll_seconds
        self._last_values: dict[str, float | bool] = {}
```

(restlicher Rumpf von `__init__` unverändert - nur die Zeile `self._resend_seconds = resend_seconds` entfällt, ersetzt durch `self._resend_poll_seconds = resend_poll_seconds` oben.)

`_resend_loop` komplett ersetzen durch:

```python
    async def _resend_loop(self) -> None:
        """Schickt periodisch nur die markierten Signale erneut
        (`resend_marked`) - anders als der einmalige Voll-Restore bei
        `/resync` und beim Bruecken-Start (`resend_all`, siehe dort). Das
        Intervall selbst ist eine zur Laufzeit ueber die WebUI aenderbare
        Einstellung (`store.resend_settings`, Entwurf periodischer Resend,
        Abschnitt 4/6) statt einer beim Start fixierten Konstante: dieser
        Takt liest sie bei JEDEM Poll frisch, alle `resend_poll_seconds`
        (Default 5s) - eine Aenderung ueber die WebUI wirkt sich damit binnen
        weniger Sekunden aus, ohne Prozess-Neustart."""
        loop = asyncio.get_running_loop()
        last_resend = loop.time()
        while True:
            await asyncio.sleep(self._resend_poll_seconds)
            interval = self._store.resend_settings.get_interval_seconds()
            if loop.time() - last_resend < interval:
                continue
            try:
                await self.resend_marked()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Markierter Resend fehlgeschlagen - Schleife laeuft weiter")
            last_resend = loop.time()
```

- [ ] **Step 4: Tests laufen lassen**

Run: `pytest tests/loxone/test_runtime.py -v`
Expected: PASS (alle Tests dieser Datei)

- [ ] **Step 5: Ganze Testsuite laufen lassen - Suche nach `resend_seconds` als Regressionscheck**

```bash
grep -rn "resend_seconds=" src/ tests/
pytest tests/ -v
```

Expected: `grep` findet keinen Treffer mehr (der Parameter hiess vorher `resend_seconds`, kein bestehender Aufrufer nutzte ihn als Keyword - siehe Recherche zu Beginn dieses Plans); komplette Suite grün.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/loxone/runtime.py tests/loxone/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(runtime): _resend_loop liest das Intervall live aus dem Store

resend_seconds (feste Konstruktor-Konstante) entfaellt, ersetzt durch
resend_poll_seconds (Takt, mit dem die Schleife das aktuelle Intervall
aus ResendSettingsStore abfragt) - eine Aenderung ueber die WebUI
wirkt so ohne Prozess-Neustart.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: WebUI - Checkbox pro Signal und Intervall-Einstellung

**Files:**
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/app.js`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `PATCH /api/signals/{key}` mit `resend` (Task 3), `GET`/`PATCH /api/settings/resend-interval` (Task 4).
- Produces: keine neuen Programmierschnittstellen - reine Oberflaeche.

- [ ] **Step 1: Failing Test schreiben**

An `tests/api/test_web.py` anhängen (nach `test_the_signal_view_ships_a_functional_and_an_expert_block`):

```python
async def test_the_signal_row_offers_a_resend_checkbox(api):
    """Periodischer Resend als Opt-in (Entwurf 2026-09-04) - dieselbe Art
    Beleg wie beim Funktional/Experte-Test oben: nur, dass die Bausteine
    ausgeliefert werden und `signal.resend` lesen/schreiben, nicht dass
    Alpine sie zur Laufzeit korrekt rendert (siehe dortiger Docstring)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    page = (await client.get("/")).text
    assert "toggleResend" in script
    assert "signal.resend" in page


async def test_the_settings_view_offers_a_resend_interval_field(api):
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert "resendIntervalDraft" in page
    assert "saveResendInterval" in script
```

- [ ] **Step 2: Test laufen lassen, bevor die Oberfläche angepasst ist**

Run: `pytest tests/api/test_web.py -k resend -v`
Expected: FAIL (`assert "toggleResend" in script` etc. schlagen fehl, Bausteine existieren noch nicht)

- [ ] **Step 3: Checkbox in `index.html` ergänzen**

In `src/loxmatter/web/index.html`, direkt nach dem bestehenden „exportieren"-Label (im Block um `toggleExported`):

```html
                            <label x-show="signal.exportable">
                              <input
                                type="checkbox"
                                :checked="signal.exported"
                                @change="toggleExported(signal)"
                              />
                              exportieren
                            </label>
                            <label>
                              <input
                                type="checkbox"
                                :checked="signal.resend"
                                @change="toggleResend(signal)"
                              />
                              periodisch erneut senden
                            </label>
                            <span
                              class="badge warn"
                              x-show="!signal.exportable"
                              x-text="signal.reason"
                            ></span>
```

(Kein `x-show="signal.exportable"` auf der neuen Checkbox: ein Resend ist auch für ein Signal sinnvoll, das nicht exportierbar - aber `resend` nicht das gleiche wie `exported` ist. Anders als beim Export-Haken gibt es hier keine technische Einschränkung, die eine Checkbox ausblenden müsste.)

- [ ] **Step 4: Intervall-Feld in `index.html` ergänzen**

Die bestehende Platzhalter-Karte am Ende der Einstellungen-Ansicht ersetzen:

```html
        <div class="card">
          <h2>Periodischer Resend</h2>
          <p class="hint">
            Markierte Signale (Haken „periodisch erneut senden" in der Signalliste) werden in
            diesem Takt zwangsweise erneut gesendet, auch ohne Änderung - unabhängig davon
            läuft ein voller Restore weiterhin einmalig beim Bridge-Start und über
            „Erneut synchronisieren" im Bereich Diagnose.
          </p>
          <div class="row">
            <label
              >Intervall in Sekunden
              <input type="number" x-model.number="resendIntervalDraft" />
            </label>
            <button class="primary" @click="saveResendInterval()" :disabled="resendIntervalBusy">
              Speichern
            </button>
          </div>
          <p x-show="resendIntervalError" x-cloak class="banner danger" x-text="resendIntervalError"></p>
        </div>
```

- [ ] **Step 5: `app.js` um Zustand und Methoden ergänzen**

In `src/loxmatter/web/app.js`, `data()`-Objekt direkt nach `settingsError: null,`:

```js
    resendInterval: { interval_seconds: 300 },
    resendIntervalDraft: 300,
    resendIntervalBusy: false,
    resendIntervalError: null,
```

In `startApp()`, die bestehende `Promise.all([...])` um den neuen Ladeaufruf ergänzen:

```js
      await Promise.all([
        ...this.devices.map((device) => this.loadControls(device.id)),
        ...this.devices.map((device) => this.loadSignals(device.id)),
        this.loadExportStatus(),
        this.loadSettings(),
        this.loadResendInterval(),
      ]);
```

Direkt nach `toggleExported(signal) { ... }` die neue Methode:

```js
    async toggleResend(signal) {
      try {
        const updated = await this.request("PATCH", `/api/signals/${signal.key}`, {
          resend: !signal.resend,
        });
        Object.assign(signal, updated);
      } catch (error) {
        this.signalsError = `Resend-Kennzeichen konnte nicht geaendert werden: ${error.message}`;
      }
    },
```

Direkt nach `saveSettings() { ... }` (Ende des „Einstellungen"-Abschnitts) die beiden neuen Methoden:

```js
    async loadResendInterval() {
      this.resendIntervalError = null;
      try {
        this.resendInterval = await this.request("GET", "/api/settings/resend-interval");
        this.resendIntervalDraft = this.resendInterval.interval_seconds;
      } catch (error) {
        this.resendIntervalError = `Resend-Intervall konnte nicht geladen werden: ${error.message}`;
      }
    },

    async saveResendInterval() {
      this.resendIntervalError = null;
      this.resendIntervalBusy = true;
      try {
        this.resendInterval = await this.request("PATCH", "/api/settings/resend-interval", {
          interval_seconds: Number(this.resendIntervalDraft),
        });
        this.showToast("Resend-Intervall gespeichert.");
      } catch (error) {
        this.resendIntervalError = `Resend-Intervall konnte nicht gespeichert werden: ${error.message}`;
      } finally {
        this.resendIntervalBusy = false;
      }
    },
```

- [ ] **Step 6: Tests laufen lassen**

Run: `pytest tests/api/test_web.py -v`
Expected: PASS (alle Tests dieser Datei, keine Regression bei den bestehenden Signal-/Einstellungen-Prüfungen)

- [ ] **Step 7: Manuell im Browser verifizieren**

```bash
python -m loxmatter run --miniserver 127.0.0.1 --url ws://localhost:5580/ws
```

Im Browser `http://localhost:8080` öffnen, ein Gerät aufklappen: die neue Checkbox „periodisch erneut senden" muss neben „exportieren" erscheinen und beim Klick per Netzwerk-Tab sichtbar `PATCH /api/signals/...` mit `{"resend": true}` senden. Im Tab „Einstellungen" muss die neue Karte „Periodischer Resend" ein Intervall-Feld zeigen, „Speichern" muss `PATCH /api/settings/resend-interval` auslösen und eine Bestätigung einblenden.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/app.js tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): Resend-Checkbox pro Signal und Intervall-Einstellung

Neue Checkbox neben "exportieren", neue Karte im Einstellungen-Tab -
beide ueber die in Task 3/4 gebauten Endpunkte.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec-Abdeckung** (gegen `docs/superpowers/specs/2026-09-04-periodischer-resend-design.md`):

- Abschnitt 4 (Datenmodell: `signal.resend`, `Store.set_resend`, `ResendSettingsStore`) → Task 1, Task 2.
- Abschnitt 5 (API: `PATCH /api/signals/{key}` um `resend` erweitert, neuer Intervall-Endpunkt mit Untergrenze) → Task 3, Task 4.
- Abschnitt 6 (Runtime: `resend_all()` unverändert, `resend_marked()` neu, `_resend_loop` live-konfigurierbar) → Task 5, Task 6.
- Abschnitt 7 (synthetische Keys außen vor, kein CLI-Flag) → erfüllt sich von selbst: `resend_keys()` fragt ausschließlich die `signal`-Tabelle ab (Task 1), der Online-Key/Pulszähler/Heartbeat leben nie dort; kein Task fügt ein CLI-Flag hinzu.
- Abschnitt 8 (Oberfläche: Checkbox, Intervall-Feld) → Task 7.
- Abschnitt 9 (Prüfung) → jeder dort genannte Fall hat einen konkreten Test in Task 1, 3, 5 oder 6.

**Platzhalter-Scan:** keine `TBD`/`TODO`/„siehe oben, analog" ohne ausgeschriebenen Code - jeder Schritt enthält den vollständigen Code oder das vollständige Testskript.

**Typkonsistenz:** `Sequence[str]` für `_force_resend` deckt sowohl `list(self._last_values)` (Dict-Keys-View zu Liste, in `resend_all`) als auch `list[str]` (`Store.resend_keys()`-Rückgabetyp, in `resend_marked`) ab. `ResendIntervalOut`/`ResendIntervalIn.interval_seconds` sind durchgängig `float`, passend zu `ResendSettingsStore.get_interval_seconds() -> float`/`set_interval_seconds(seconds: float)`. `SignalOut.resend`/`SignalPatch.resend` und `StoredSignal.resend` sind durchgängig `bool`.

Execution Handoff folgt nach diesem Dokument.
