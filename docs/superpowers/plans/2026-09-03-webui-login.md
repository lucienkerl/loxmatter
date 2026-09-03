# WebUI-Login Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die WebUI bekommt eine Anmeldung mit Passwort samt Ersteinrichtung über die Oberfläche; die Token-Eingabe im Browser entfällt, und ohne gesetztes Passwort liefert keine `/api`-Route mehr Daten aus.

**Architecture:** Der Passwort-Hash und die Sitzungen liegen in zwei neuen Tabellen desselben SQLite-Stores (Schema v4). Ein neues Paket `loxmatter.auth` kapselt Hashing, Sitzungen und Login-Drosselung als reine Logik ohne FastAPI-Bezug; `api/auth.py` ist der Router darüber; `build_api_guard` akzeptiert künftig Sitzungs-Cookie ODER Bearer-Token und lässt ohne beides nichts mehr durch. Die Oberfläche schaltet anhand von `GET /auth-info` zwischen Einrichtung, Login und App um.

**Tech Stack:** Python 3.12, FastAPI, SQLite über `sqlite3`, `hashlib.scrypt` und `secrets` aus der Standardbibliothek (keine neue Abhängigkeit), Alpine.js im Browser, pytest mit `asyncio_mode = "auto"`, `httpx2` als Testclient.

**Spec:** `docs/superpowers/specs/2026-09-03-webui-login-design.md` — bei jedem Zweifel gilt die Spec, nicht dieser Plan.

## Global Constraints

- **Sprache:** Prosa, Docstrings, Kommentare und Fehlermeldungen auf Deutsch. Bezeichner im Code und Schlüssel in JSON-Antworten auf Englisch (Review-Fix M9, 2026-09-02).
- **Keine neue Laufzeitabhängigkeit.** `pyproject.toml` bleibt im Abschnitt `[project].dependencies` unverändert.
- **Zeilenlänge 100** (`[tool.ruff]`), **mypy strict** über `src` und `scripts`.
- **Kommentardichte:** Dieses Repository begründet im Code, *warum* etwas so ist, nicht *was* es tut. Neue Module und jede nicht offensichtliche Entscheidung bekommen einen Docstring in diesem Stil. Ein Kommentar, der nur den Code wiederholt, ist keiner.
- **Geheimnisse gehören nie ins Log und nie in eine Antwort:** kein Passwort, kein Hash, keine Sitzungskennung, kein Token — in keinem Zweig, auch keinem Fehlerzweig.
- **Testlauf:** `uv run pytest`. Linting: `uv run ruff check src tests`. Typen: `uv run mypy`.
- **Commit-Format:** `<typ>(<bereich>): <beschreibung>` auf Deutsch, wie in der bisherigen Historie (`feat(store):`, `fix(profiles):`, `test(profiles):`).
- **Cookie-Name:** `loxmatter_session`. **Sitzungsdauer:** 30 Tage. **Mindestlänge Passwort:** 8 Zeichen. **Drosselung:** ab 5 Fehlversuchen je Peer-Adresse, dann 30 Sekunden Sperre.

---

## File Structure

**Neu:**

| Datei | Verantwortung |
| --- | --- |
| `src/loxmatter/model/auth_store.py` | Datenzugriff auf `setting` und `session`. Kennt SQL, kennt keine Kryptografie und kein HTTP. |
| `src/loxmatter/auth/__init__.py` | Leeres Paketmodul mit Docstring, der die drei Module darunter einordnet. |
| `src/loxmatter/auth/passwords.py` | `hash_password` / `verify_password` über `hashlib.scrypt`. Kennt weder Store noch HTTP. |
| `src/loxmatter/auth/sessions.py` | Sitzungen anlegen und prüfen, Cookie-Name und Laufzeit. Kennt den `AuthStore`, kein HTTP. |
| `src/loxmatter/auth/throttle.py` | `LoginThrottle` — Fehlversuche je Aufrufer, rein im Speicher. |
| `src/loxmatter/api/auth.py` | Router `/auth-info`, `/auth/setup`, `/auth/login`, `/auth/logout`. |
| `tests/model/test_auth_store.py`, `tests/auth/test_passwords.py`, `tests/auth/test_sessions.py`, `tests/auth/test_throttle.py`, `tests/api/test_auth.py` | Tests dazu. |

Die Spec nennt in Abschnitt 12 eine einzige Datei `tests/api/test_auth.py`. Der Plan teilt die Einheitentests der drei `auth`-Module in `tests/auth/` ab und lässt in `tests/api/test_auth.py` nur die Routen — die Testdateien folgen damit den Modulen, wie im übrigen Repository auch (`tests/model/`, `tests/loxone/`).

**Geändert:** `src/loxmatter/model/store.py` (Schema v4, `Store.auth`), `src/loxmatter/loxone/server.py` (Wächter, Router), `src/loxmatter/api/diagnostics.py` (403-Zweig entfällt), `src/loxmatter/cli.py` (Warnung, neuer Befehl), `src/loxmatter/web/{index.html,app.js,style.css}`, `tests/api/conftest.py` und die Testdateien mit `build_app(...)`-Aufrufen, `README.md`, `deploy/testhost/.env.example`, `deploy/testhost/docker-compose.yml`.

**Reihenfolge-Logik:** Die Tasks 1–7 sind rein additiv — nach jedem einzelnen läuft die Suite grün und die Oberfläche unverändert weiter. Erst Task 8 schaltet den bisher offenen Zustand ab und zieht deshalb alle Testfixtures nach. Die Oberfläche (Task 7) steht bewusst **vor** Task 8, damit es keinen Zwischenstand gibt, in dem der Browser ausgesperrt ist.

---

### Task 1: Schema v4 und `AuthStore`

**Files:**
- Create: `src/loxmatter/model/auth_store.py`
- Modify: `src/loxmatter/model/store.py` (`_SCHEMA`, `_SCHEMA_VERSION`, `_MIGRATIONS`, `Store.__init__`)
- Test: `tests/model/test_auth_store.py`, `tests/model/test_store_migration.py`

**Interfaces:**
- Consumes: nichts.
- Produces: `AuthStore` mit `password_hash() -> str | None`, `set_password_hash_if_unset(value: str) -> bool`, `set_password_hash(value: str) -> None`, `create_session(session_id: str, *, created_at: int, expires_at: int) -> None`, `session_expires_at(session_id: str) -> int | None`, `extend_session(session_id: str, *, expires_at: int) -> None`, `delete_session(session_id: str) -> None`, `delete_all_sessions() -> None`, `purge_expired_sessions(now: int) -> None`. Erreichbar als `Store.auth`.

- [ ] **Step 1: Write the failing test**

`tests/model/test_auth_store.py`:

```python
"""Tests fuer `AuthStore` - den Teil des Stores, der den Zugang verwaltet.

Die Kernfrage: haelt `setting` genau einen Passwort-Hash, und laesst sich
`session` so fuehren, dass eine abgelaufene Sitzung nicht mehr gilt und eine
geloeschte sofort weg ist?
"""

from __future__ import annotations

from loxmatter.model.store import Store


def test_password_hash_is_none_on_a_fresh_store(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.auth.password_hash() is None
    finally:
        store.close()


def test_set_password_hash_if_unset_wins_once_and_then_never_again(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.auth.set_password_hash_if_unset("erster") is True
        assert store.auth.set_password_hash_if_unset("zweiter") is False
        assert store.auth.password_hash() == "erster"
    finally:
        store.close()


def test_set_password_hash_replaces_an_existing_one(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.auth.set_password_hash_if_unset("alt")
        store.auth.set_password_hash("neu")
        assert store.auth.password_hash() == "neu"
    finally:
        store.close()


def test_sessions_are_stored_read_extended_and_deleted(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.auth.create_session("abc", created_at=100, expires_at=200)
        assert store.auth.session_expires_at("abc") == 200
        store.auth.extend_session("abc", expires_at=300)
        assert store.auth.session_expires_at("abc") == 300
        store.auth.delete_session("abc")
        assert store.auth.session_expires_at("abc") is None
    finally:
        store.close()


def test_unknown_session_has_no_expiry(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        assert store.auth.session_expires_at("gibt-es-nicht") is None
    finally:
        store.close()


def test_purge_removes_only_expired_sessions(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.auth.create_session("alt", created_at=1, expires_at=100)
        store.auth.create_session("frisch", created_at=1, expires_at=500)
        store.auth.purge_expired_sessions(200)
        assert store.auth.session_expires_at("alt") is None
        assert store.auth.session_expires_at("frisch") == 500
    finally:
        store.close()


def test_delete_all_sessions_leaves_the_password_untouched(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.auth.set_password_hash_if_unset("hash")
        store.auth.create_session("a", created_at=1, expires_at=500)
        store.auth.create_session("b", created_at=1, expires_at=500)
        store.auth.delete_all_sessions()
        assert store.auth.session_expires_at("a") is None
        assert store.auth.session_expires_at("b") is None
        assert store.auth.password_hash() == "hash"
    finally:
        store.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/model/test_auth_store.py -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'auth'`

- [ ] **Step 3: Write `AuthStore`**

`src/loxmatter/model/auth_store.py`:

```python
"""Der Teil des Stores, der den Zugang verwaltet - Passwort-Hash und
Sitzungen - statt Geraete, Signale und Kommandos.

Eigenes Modul und eigene Klasse, nicht weitere Methoden an `Store`: dort
liegen inzwischen ueber neunhundert Zeilen zum Geraetemodell, und der Zugang
hat damit fachlich nichts zu tun. Die Verbindung gehoert trotzdem weiterhin
`Store` - diese Klasse ist eine Sicht darauf, kein zweiter Verbindungsaufbau
auf dieselbe Datei (das waere eine zweite Sperrdomaene fuer dieselben Daten).

Was hier NICHT stattfindet: Kryptografie und HTTP. Diese Klasse legt einen
Hash ab und liest ihn wieder, ohne zu wissen, wie er entsteht (siehe
`loxmatter.auth.passwords`), und sie kennt weder Cookies noch Statuscodes
(siehe `loxmatter.auth.sessions` und `loxmatter.api.auth`). Wer das hier
vermischt, hat am Ende drei Stellen, an denen ein Geheimnis auftauchen kann,
statt einer.

Das Schema der beiden Tabellen steht in `store.py` bei `_SCHEMA` und
`_migrate_to_v4` - Schema-Definitionen bleiben an einem Ort, auch wenn der
Zugriff darauf hier liegt.
"""

from __future__ import annotations

import sqlite3

# Der einzige Schluessel, den `setting` bislang traegt. Die Tabelle ist
# trotzdem generisch (Schluessel/Wert) angelegt, weil die uebrige
# Konfiguration denselben Weg gehen soll (Spec 14.2) - eine Tabelle
# `password` mit einer Spalte waere in dem Moment wieder umzubauen.
_PASSWORD_KEY = "password_hash"


class AuthStore:
    """Zugriff auf `setting` und `session` ueber die Verbindung des Stores."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def password_hash(self) -> str | None:
        """Der abgelegte Hash - `None`, solange kein Passwort vergeben ist.

        `None` ist der Zustand, an dem der gesamte Zugang haengt: er
        bedeutet "Ersteinrichtung noch offen" und laesst nach
        `loxone.server.build_api_guard` keine einzige `/api`-Route zu."""
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_PASSWORD_KEY,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def set_password_hash_if_unset(self, value: str) -> bool:
        """Legt den Hash an, aber nur, wenn noch keiner da ist - `True`, wenn
        dieser Aufruf ihn gesetzt hat.

        `INSERT OR IGNORE` und nicht "erst pruefen, dann schreiben": SQLite
        entscheidet das in einer einzigen Anweisung, zwei gleichzeitige
        Einrichtungsversuche koennen sich also nicht gegenseitig
        ueberschreiben. Genau darauf verlaesst sich `POST /auth/setup`, um
        nach dem ersten Erfolg dauerhaft mit 409 zu antworten."""
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO setting (key, value) VALUES (?, ?)",
            (_PASSWORD_KEY, value),
        )
        self._db.commit()
        return cursor.rowcount == 1

    def set_password_hash(self, value: str) -> None:
        """Setzt den Hash und ueberschreibt einen vorhandenen.

        Der Weg fuer `loxmatter set-password` auf dem Host (Spec 9), NICHT
        fuer die Oberflaeche - die benutzt ausschliesslich
        `set_password_hash_if_unset`."""
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_PASSWORD_KEY, value),
        )
        self._db.commit()

    def create_session(self, session_id: str, *, created_at: int, expires_at: int) -> None:
        self._db.execute(
            "INSERT INTO session (id, created_at, expires_at) VALUES (?, ?, ?)",
            (session_id, created_at, expires_at),
        )
        self._db.commit()

    def session_expires_at(self, session_id: str) -> int | None:
        """Ablaufzeitpunkt als Unix-Sekunden - `None`, wenn es die Sitzung
        nicht (mehr) gibt. Ob sie damit noch gilt, entscheidet
        `loxmatter.auth.sessions`, nicht diese Klasse."""
        row = self._db.execute(
            "SELECT expires_at FROM session WHERE id = ?", (session_id,)
        ).fetchone()
        return None if row is None else int(row["expires_at"])

    def extend_session(self, session_id: str, *, expires_at: int) -> None:
        self._db.execute(
            "UPDATE session SET expires_at = ? WHERE id = ?", (expires_at, session_id)
        )
        self._db.commit()

    def delete_session(self, session_id: str) -> None:
        self._db.execute("DELETE FROM session WHERE id = ?", (session_id,))
        self._db.commit()

    def delete_all_sessions(self) -> None:
        """Meldet jeden ab. Aufgerufen von `loxmatter set-password`: wer das
        Passwort zuruecksetzt, will nicht, dass eine alte Sitzung
        weiterlaeuft."""
        self._db.execute("DELETE FROM session")
        self._db.commit()

    def purge_expired_sessions(self, now: int) -> None:
        """Raeumt abgelaufene Zeilen weg. Aufgerufen beim Anlegen einer neuen
        Sitzung - kein Hintergrundjob fuer eine Tabelle, die im Normalfall
        eine Handvoll Zeilen haelt."""
        self._db.execute("DELETE FROM session WHERE expires_at <= ?", (now,))
        self._db.commit()
```

- [ ] **Step 4: Add the tables to the schema and the migration**

In `src/loxmatter/model/store.py`, `_SCHEMA_VERSION` von `3` auf `4` heben und den Kommentarblock darüber um einen Satz ergänzen:

```python
# ... Version 3 (Aufgabe 7, Phase 6) fuegt keine Spalte hinzu -
# sie leitet `signal.title`, `signal.unit` und den Vorgabewert von
# `signal.exported` fuer BESTEHENDE Zeilen aus der Profiltabelle neu ab, siehe
# `_migrate_to_v3`. Version 4 (WebUI-Login) fuegt die Tabellen `setting` und
# `session` hinzu, siehe `_migrate_to_v4` - beide sind bei einer frischen
# Datenbank bereits durch `_SCHEMA` da, die Migration ist deshalb nur fuer
# Bestandsdatenbanken noetig.
_SCHEMA_VERSION = 4
```

An `_SCHEMA` anhängen (nach dem `command`-Block, innerhalb desselben Strings):

```sql
CREATE TABLE IF NOT EXISTS setting (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session (
    id         TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
```

Direkt vor `_MIGRATIONS` einfügen:

```python
def _migrate_to_v4(db: sqlite3.Connection) -> None:
    """Legt `setting` und `session` an (WebUI-Login).

    `CREATE TABLE IF NOT EXISTS` und nicht `CREATE TABLE`: eine frisch
    angelegte Datenbank hat beide Tabellen bereits durch `_SCHEMA`, steht
    dabei aber ebenfalls auf `PRAGMA user_version = 0` und laeuft deshalb
    durch dieselbe Migrationskette (siehe `_migrate` und
    `_add_column_if_missing` zur gleichen Falle bei Spalten).

    Kein Backfill: eine Bestandsdatenbank hat kein Passwort und keine
    Sitzung, und genau das ist der richtige Zustand - sie geht nach dem
    Update durch die Ersteinrichtung (Spec 5)."""
    db.executescript(
        """
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
    )
```

`_MIGRATIONS` um den Eintrag erweitern:

```python
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
    3: _migrate_to_v3,
    4: _migrate_to_v4,
}
```

- [ ] **Step 5: Hang `AuthStore` on `Store`**

In `src/loxmatter/model/store.py` den Import ergänzen und `Store.__init__` erweitern:

```python
from loxmatter.model.auth_store import AuthStore
```

```python
class Store:
    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()
        _migrate(self._db)
        # Sicht auf dieselbe Verbindung, kein zweiter Verbindungsaufbau -
        # siehe Moduldocstring von `auth_store.py`.
        self.auth = AuthStore(self._db)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/model/test_auth_store.py -v`
Expected: PASS (7 Tests)

- [ ] **Step 7: Add the migration test**

An `tests/model/test_store_migration.py` anhängen — den vorhandenen Testaufbau dieser Datei übernehmen (dort steht bereits, wie eine Datenbank mit älterer `user_version` erzeugt wird; diesem Muster folgen):

```python
def test_migration_to_v4_adds_the_auth_tables_without_touching_devices(tmp_path):
    """Eine Bestandsdatenbank auf Version 3 bekommt `setting` und `session`,
    und ihre Geraetezeilen bleiben unangetastet."""
    path = tmp_path / "alt.sqlite"
    store = Store(path)
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    signals_before = len(store.signals(device_id))
    store.close()

    # Auf Version 3 zuruecksetzen und beide Tabellen entfernen - so sieht
    # eine Datenbank aus, die vor dieser Aenderung angelegt wurde.
    db = sqlite3.connect(str(path))
    db.executescript("DROP TABLE session; DROP TABLE setting; PRAGMA user_version = 3;")
    db.commit()
    db.close()

    store = Store(path)
    try:
        assert int(store._db.execute("PRAGMA user_version").fetchone()[0]) == 4
        assert store.auth.password_hash() is None
        store.auth.create_session("a", created_at=1, expires_at=2)
        assert store.auth.session_expires_at("a") == 2
        assert len(store.signals(device_id)) == signals_before
    finally:
        store.close()
```

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`
Expected: PASS — alles grün, diese Änderung ist rein additiv.

- [ ] **Step 9: Lint and typecheck**

Run: `uv run ruff check src tests && uv run mypy`
Expected: keine Meldungen.

- [ ] **Step 10: Commit**

```bash
git add src/loxmatter/model/auth_store.py src/loxmatter/model/store.py tests/model/test_auth_store.py tests/model/test_store_migration.py
git commit -m "feat(store): Tabellen fuer Passwort und Sitzungen (Schema v4)"
```

---

### Task 2: Passwort-Hash mit scrypt

**Files:**
- Create: `src/loxmatter/auth/__init__.py`, `src/loxmatter/auth/passwords.py`
- Test: `tests/auth/test_passwords.py`

**Interfaces:**
- Consumes: nichts.
- Produces: `hash_password(password: str) -> str`, `verify_password(password: str, stored: str) -> bool`, `MIN_PASSWORD_LENGTH: int = 8`.

- [ ] **Step 1: Write the failing test**

`tests/auth/test_passwords.py`:

```python
"""Tests fuer das Passwort-Hashing (Spec 6).

Die Kernfrage: passt das richtige Passwort, faellt jedes andere durch, und
verkraftet `verify_password` einen kaputten oder fremden Hash, ohne zu
werfen? Der letzte Punkt ist kein Randfall: der Wert kommt aus einer Datei,
die ein Betreiber von Hand bearbeitet haben kann.
"""

from __future__ import annotations

import hashlib

from loxmatter.auth.passwords import hash_password, verify_password


def test_the_right_password_verifies():
    stored = hash_password("richtig-und-lang-genug")
    assert verify_password("richtig-und-lang-genug", stored) is True


def test_a_wrong_password_does_not_verify():
    stored = hash_password("richtig-und-lang-genug")
    assert verify_password("falsch-und-lang-genug", stored) is False


def test_the_same_password_hashes_differently_every_time():
    """Sonst waere das Salz keins - zwei Installationen mit demselben
    Passwort haetten denselben Hash."""
    assert hash_password("gleiches-passwort") != hash_password("gleiches-passwort")


def test_the_stored_form_names_its_scheme_and_parameters():
    stored = hash_password("egal-hauptsache-lang")
    scheme, n, r, p, salt, key = stored.split("$")
    assert scheme == "scrypt"
    assert (int(n), int(r), int(p)) == (2**14, 8, 1)
    assert len(bytes.fromhex(salt)) == 16
    assert len(bytes.fromhex(key)) == 32


def test_a_hash_with_other_parameters_still_verifies():
    """Der Grund, warum die Parameter im Wert stehen: ein spaeterer Wechsel
    der Kostenfaktoren darf alte Hashes nicht entwerten.

    Der Vergleichswert wird hier mit ANDEREN Kostenfaktoren (n = 1024) selbst
    gerechnet, nicht mit denen des Moduls - sonst pruefte der Test nur, dass
    eine Konstante mit sich selbst uebereinstimmt."""
    salt = bytes.fromhex("00112233445566778899aabbccddeeff")
    key = hashlib.scrypt(b"geheim-und-lang", salt=salt, n=1024, r=8, p=1, dklen=32)
    stored = f"scrypt$1024$8$1${salt.hex()}${key.hex()}"
    assert verify_password("geheim-und-lang", stored) is True


def test_a_broken_stored_value_never_raises():
    for kaputt in ["", "keinDollar", "scrypt$1$2", "argon2$1$2$3$4$5", "scrypt$a$b$c$d$e"]:
        assert verify_password("irgendwas", kaputt) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/auth/test_passwords.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.auth'`

- [ ] **Step 3: Write the package docstring**

`src/loxmatter/auth/__init__.py`:

```python
"""Zugang zur Oberflaeche: Passwort, Sitzung, Drosselung.

Drei Module, absichtlich getrennt und absichtlich ohne FastAPI-Bezug:

- `passwords` rechnet Hashes und prueft sie. Kennt weder Datenbank noch HTTP.
- `sessions` legt Sitzungen an und prueft sie. Kennt den `AuthStore`, kein HTTP.
- `throttle` zaehlt Fehlversuche. Kennt gar nichts ausser der Uhr.

Der HTTP-Teil liegt in `loxmatter.api.auth`, der Waechter in
`loxmatter.loxone.server`. Diese Trennung ist der Grund, warum die Logik
hier ohne ASGI-Testclient pruefbar ist - und warum ein Geheimnis nur an den
Stellen auftauchen kann, die es wirklich brauchen.
"""
```

- [ ] **Step 4: Write `passwords.py`**

`src/loxmatter/auth/passwords.py`:

```python
"""Passwort-Hashing mit `hashlib.scrypt` (Spec 6).

**Warum scrypt und nicht Argon2 oder bcrypt:** beide brauchten eine neue
Laufzeitabhaengigkeit (`argon2-cffi` bzw. `passlib`) fuer genau einen Hash in
diesem Projekt. scrypt ist speicherhart, in der Standardbibliothek und fuer
diesen Zweck ausreichend. Die Abhaengigkeitsliste in `pyproject.toml` bleibt
dadurch unveraendert.

**Warum die Parameter im gespeicherten Wert stehen** (`scrypt$n$r$p$salt$hash`):
werden die Kostenfaktoren spaeter angehoben, muessen bereits abgelegte Hashes
weiter pruefbar bleiben - sonst sperrt ein Update den Betreiber aus seiner
eigenen Bruecke aus. `verify_password` liest deshalb die Parameter aus dem
Wert und nicht aus den Konstanten dieses Moduls.

Der Speicherbedarf von scrypt ist 128 * n * r, hier also 16 MiB. Das liegt
unter der Vorgabe, die `hashlib.scrypt` ohne gesetztes `maxmem` durchlaesst
(32 MiB) - deshalb steht dort kein `maxmem`-Argument.
"""

from __future__ import annotations

import hashlib
import secrets

# Kein Wert aus einem Sicherheitsvakuum, sondern der uebliche interaktive
# Arbeitspunkt fuer scrypt: rund 16 MiB Speicher und ein Bruchteil einer
# Sekunde je Pruefung. Hoeher gesetzt wuerde jeder Login auf einem
# Raspberry Pi spuerbar traege.
_N = 2**14
_R = 8
_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32

_SCHEME = "scrypt"

# Kuerzer waere ein Passwort, das eine Drosselung von 30 Sekunden je fuenf
# Versuchen nicht mehr rettet (siehe `throttle`). Laenger vorzuschreiben
# fuehrt erfahrungsgemaess zu einem Zettel am Bildschirm.
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    """Rechnet den abzulegenden Wert - mit frischem Salz bei jedem Aufruf."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_KEY_BYTES
    )
    return f"{_SCHEME}${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Prueft `password` gegen einen abgelegten Wert.

    Gibt bei jedem unlesbaren, fremden oder verstuemmelten `stored` schlicht
    `False` zurueck, statt zu werfen: der Wert kommt aus einer Datei auf der
    Platte des Betreibers, und ein Tippfehler darin soll einen 401 ergeben,
    keinen 500 mit Traceback im Log."""
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _SCHEME:
        return False
    _, n, r, p, salt_hex, key_hex = parts
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(key_hex)
        key = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except ValueError:
        # Unleserliche Hex-Zeichen, unsinnige Parameter (n keine Zweierpotenz,
        # dklen 0) - alles derselbe Fall: dieser Wert ist kein Hash.
        return False
    return secrets.compare_digest(key, expected)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/auth/test_passwords.py -v`
Expected: PASS (6 Tests)

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uv run ruff check src tests && uv run mypy
git add src/loxmatter/auth tests/auth/test_passwords.py
git commit -m "feat(auth): Passwort-Hashing mit scrypt aus der Standardbibliothek"
```

---

### Task 3: Sitzungen

**Files:**
- Create: `src/loxmatter/auth/sessions.py`
- Test: `tests/auth/test_sessions.py`

**Interfaces:**
- Consumes: `AuthStore` aus Task 1.
- Produces: `SESSION_COOKIE: str = "loxmatter_session"`, `SESSION_LIFETIME_SECONDS: int`, `open_session(auth: AuthStore, *, now: int | None = None) -> str`, `session_is_valid(auth: AuthStore, session_id: str, *, now: int | None = None) -> bool`.

- [ ] **Step 1: Write the failing test**

`tests/auth/test_sessions.py`:

```python
"""Tests fuer die Sitzungsverwaltung (Spec 7).

`now` ist in beiden Funktionen ein Parameter, damit diese Tests Zeit
vergehen lassen koennen, ohne zu schlafen - eine Sitzung mit 30 Tagen
Laufzeit liesse sich sonst gar nicht pruefen.
"""

from __future__ import annotations

from loxmatter.auth.sessions import (
    SESSION_LIFETIME_SECONDS,
    open_session,
    session_is_valid,
)
from loxmatter.model.store import Store


def _store(tmp_path):
    return Store(tmp_path / "t.sqlite")


def test_a_fresh_session_is_valid(tmp_path):
    store = _store(tmp_path)
    try:
        session_id = open_session(store.auth, now=1000)
        assert session_is_valid(store.auth, session_id, now=1000) is True
    finally:
        store.close()


def test_two_sessions_never_share_an_id(tmp_path):
    store = _store(tmp_path)
    try:
        assert open_session(store.auth, now=1000) != open_session(store.auth, now=1000)
    finally:
        store.close()


def test_an_unknown_id_is_not_valid(tmp_path):
    store = _store(tmp_path)
    try:
        assert session_is_valid(store.auth, "erfunden", now=1000) is False
    finally:
        store.close()


def test_a_session_expires(tmp_path):
    store = _store(tmp_path)
    try:
        session_id = open_session(store.auth, now=1000)
        later = 1000 + SESSION_LIFETIME_SECONDS + 1
        assert session_is_valid(store.auth, session_id, now=later) is False
    finally:
        store.close()


def test_an_expired_session_is_removed_when_it_is_checked(tmp_path):
    """Sonst blieben abgelaufene Zeilen liegen, bis zufaellig jemand eine
    neue Sitzung anlegt."""
    store = _store(tmp_path)
    try:
        session_id = open_session(store.auth, now=1000)
        later = 1000 + SESSION_LIFETIME_SECONDS + 1
        session_is_valid(store.auth, session_id, now=later)
        assert store.auth.session_expires_at(session_id) is None
    finally:
        store.close()


def test_a_session_is_extended_only_after_a_day(tmp_path):
    """Gleitende Verlaengerung ohne Schreibzugriff bei JEDEM Aufruf: eine
    Oberflaeche mit Live-Ansicht stellt viele Anfragen je Minute, und jede
    davon eine SQLite-Schreiboperation waere reine Verschwendung."""
    store = _store(tmp_path)
    try:
        session_id = open_session(store.auth, now=1000)
        first = store.auth.session_expires_at(session_id)

        session_is_valid(store.auth, session_id, now=1000 + 60)
        assert store.auth.session_expires_at(session_id) == first

        session_is_valid(store.auth, session_id, now=1000 + 2 * 24 * 60 * 60)
        assert store.auth.session_expires_at(session_id) > first
    finally:
        store.close()


def test_opening_a_session_purges_expired_ones(tmp_path):
    store = _store(tmp_path)
    try:
        alt = open_session(store.auth, now=1000)
        open_session(store.auth, now=1000 + SESSION_LIFETIME_SECONDS + 1)
        assert store.auth.session_expires_at(alt) is None
    finally:
        store.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/auth/test_sessions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.auth.sessions'`

- [ ] **Step 3: Write `sessions.py`**

`src/loxmatter/auth/sessions.py`:

```python
"""Sitzungen: anlegen, pruefen, gleitend verlaengern (Spec 7).

**Warum in der Datenbank und nicht im Speicher:** der Dienst laeuft mit
`restart: unless-stopped` (siehe `deploy/testhost/docker-compose.yml`). Ein
Neustart - nach einem Update, nach einem Stromausfall, nach einem Absturz -
duerfte sonst jeden angemeldeten Browser abmelden, und der Betreiber saehe
statt seiner Bruecke einen Login-Bildschirm, ohne zu wissen warum.

**Warum ein serverseitiger Eintrag und kein signiertes Cookie:** ein
signiertes Cookie liesse sich nicht zurueckziehen. `POST /auth/logout` und
`loxmatter set-password` sollen eine Sitzung wirklich beenden koennen, nicht
nur den Browser bitten, sie zu vergessen.

`now` ist in beiden Funktionen ein optionaler Parameter (Unix-Sekunden).
Produktivcode uebergibt ihn nie; die Tests brauchen ihn, um dreissig Tage
vergehen zu lassen, ohne zu schlafen.
"""

from __future__ import annotations

import secrets
import time

from loxmatter.model.auth_store import AuthStore

# Der Cookie-Name. Steht hier und nicht in `api/auth.py`, weil ihn zwei
# Stellen brauchen: der Router setzt ihn, der Waechter in
# `loxone/server.py` liest ihn. Zwei Schreibweisen desselben Namens waeren
# ein Fehler, den niemand im Test bemerkt, weil beide Seiten fuer sich
# funktionieren.
SESSION_COOKIE = "loxmatter_session"

SESSION_LIFETIME_SECONDS = 30 * 24 * 60 * 60

# Verlaengert wird erst, wenn mehr als ein Tag der Laufzeit verbraucht ist -
# siehe `session_is_valid`.
_EXTEND_AFTER_SECONDS = 24 * 60 * 60


def open_session(auth: AuthStore, *, now: int | None = None) -> str:
    """Legt eine Sitzung an und gibt ihre Kennung zurueck.

    32 Byte aus `secrets.token_hex` - dieselbe Groessenordnung wie das
    empfohlene API-Token (`openssl rand -hex 32`), weil diese Kennung
    genau dasselbe wert ist: wer sie hat, ist angemeldet."""
    moment = int(time.time()) if now is None else now
    auth.purge_expired_sessions(moment)
    session_id = secrets.token_hex(32)
    auth.create_session(
        session_id, created_at=moment, expires_at=moment + SESSION_LIFETIME_SECONDS
    )
    return session_id


def session_is_valid(auth: AuthStore, session_id: str, *, now: int | None = None) -> bool:
    """Gilt diese Sitzung noch? Verlaengert sie dabei gleitend.

    Die Verlaengerung passiert hoechstens einmal je `_EXTEND_AFTER_SECONDS`
    und nicht bei jedem Aufruf: diese Funktion laeuft in JEDER Anfrage an
    `/api`, und die Oberflaeche stellt beim Bedienen mehrere je Sekunde. Ein
    `UPDATE` pro Anfrage waere eine SQLite-Schreiboperation fuer nichts.

    Eine abgelaufene Sitzung wird hier gleich geloescht - der Aufraeumpfad,
    der ohne einen neuen Login nie liefe."""
    moment = int(time.time()) if now is None else now
    expires_at = auth.session_expires_at(session_id)
    if expires_at is None:
        return False
    if expires_at <= moment:
        auth.delete_session(session_id)
        return False
    if expires_at - moment <= SESSION_LIFETIME_SECONDS - _EXTEND_AFTER_SECONDS:
        auth.extend_session(session_id, expires_at=moment + SESSION_LIFETIME_SECONDS)
    return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/auth/test_sessions.py -v`
Expected: PASS (7 Tests)

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check src tests && uv run mypy
git add src/loxmatter/auth/sessions.py tests/auth/test_sessions.py
git commit -m "feat(auth): Sitzungen in der Datenbank, gleitend verlaengert"
```

---

### Task 4: Drosselung gegen Durchprobieren

**Files:**
- Create: `src/loxmatter/auth/throttle.py`
- Test: `tests/auth/test_throttle.py`

**Interfaces:**
- Consumes: nichts.
- Produces: `LoginThrottle` mit `retry_after(client: str, *, now: float | None = None) -> int`, `record_failure(client: str, *, now: float | None = None) -> None`, `record_success(client: str) -> None`; Konstanten `FAILURES_BEFORE_THROTTLING = 5`, `THROTTLE_SECONDS = 30`.

- [ ] **Step 1: Write the failing test**

`tests/auth/test_throttle.py`:

```python
"""Tests fuer die Login-Drosselung (Spec 8).

Die Kernfrage: bremst sie nach genug Fehlversuchen, laesst sie den
rechtmaessigen Betreiber danach wieder durch, und trifft sie wirklich nur
die Adresse, die daneben lag?
"""

from __future__ import annotations

from loxmatter.auth.throttle import (
    FAILURES_BEFORE_THROTTLING,
    THROTTLE_SECONDS,
    LoginThrottle,
)


def test_the_first_attempt_is_never_throttled():
    throttle = LoginThrottle()
    assert throttle.retry_after("10.0.0.1", now=0.0) == 0


def test_throttling_starts_after_the_configured_number_of_failures():
    throttle = LoginThrottle()
    for _ in range(FAILURES_BEFORE_THROTTLING - 1):
        throttle.record_failure("10.0.0.1", now=0.0)
    assert throttle.retry_after("10.0.0.1", now=0.0) == 0

    throttle.record_failure("10.0.0.1", now=0.0)
    assert throttle.retry_after("10.0.0.1", now=0.0) > 0


def test_the_block_expires():
    throttle = LoginThrottle()
    for _ in range(FAILURES_BEFORE_THROTTLING):
        throttle.record_failure("10.0.0.1", now=0.0)
    assert throttle.retry_after("10.0.0.1", now=THROTTLE_SECONDS + 1) == 0


def test_a_success_clears_the_counter():
    """Sonst sperrte sich der Betreiber nach fuenf Vertippern selbst aus,
    obwohl er das Passwort inzwischen richtig eingegeben hat."""
    throttle = LoginThrottle()
    for _ in range(FAILURES_BEFORE_THROTTLING):
        throttle.record_failure("10.0.0.1", now=0.0)
    throttle.record_success("10.0.0.1")
    assert throttle.retry_after("10.0.0.1", now=0.0) == 0


def test_one_address_does_not_block_another():
    throttle = LoginThrottle()
    for _ in range(FAILURES_BEFORE_THROTTLING):
        throttle.record_failure("10.0.0.1", now=0.0)
    assert throttle.retry_after("10.0.0.2", now=0.0) == 0


def test_retry_after_counts_down():
    throttle = LoginThrottle()
    for _ in range(FAILURES_BEFORE_THROTTLING):
        throttle.record_failure("10.0.0.1", now=0.0)
    early = throttle.retry_after("10.0.0.1", now=1.0)
    late = throttle.retry_after("10.0.0.1", now=THROTTLE_SECONDS - 1.0)
    assert early > late > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/auth/test_throttle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.auth.throttle'`

- [ ] **Step 3: Write `throttle.py`**

`src/loxmatter/auth/throttle.py`:

```python
"""Bremse gegen das Durchprobieren von Passwoertern (Spec 8).

Der Grund, warum es dieses Modul ueberhaupt gibt: ein Passwort ist ratbar,
ein Token aus `openssl rand -hex 32` nicht. Ohne Bremse waere der Login also
der schwaechere Weg in denselben Dienst - und dieser Entwurf haette die
Absicherung verschlechtert, waehrend er sie bequemer macht.

**Im Speicher und nicht in der Datenbank:** das ist fluechtiger Zustand, der
keinen Schreibzugriff je Fehlversuch rechtfertigt. Ein Neustart loescht ihn -
nur kann ein Angreifer keinen ausloesen, und ein Betreiber, der neu startet,
um sich schneller wieder anmelden zu koennen, betrachtet sein eigenes
Passwort ohnehin nicht als Angriff.

**`time.monotonic` und nicht `time.time`:** eine Zeitumstellung oder ein
NTP-Sprung darf eine Sperre weder verlaengern noch aufheben.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

FAILURES_BEFORE_THROTTLING = 5
THROTTLE_SECONDS = 30


@dataclass
class LoginThrottle:
    """Zaehlt Fehlversuche je Aufrufer. Eine Instanz je Router, siehe
    `api.auth.build_auth_router`."""

    _failures: dict[str, int] = field(default_factory=dict)
    _blocked_until: dict[str, float] = field(default_factory=dict)

    def retry_after(self, client: str, *, now: float | None = None) -> int:
        """Wie viele Sekunden dieser Aufrufer noch warten muss - `0`, wenn er
        es sofort versuchen darf.

        Aufgerundet, damit die Meldung in der Oberflaeche ("in X Sekunden
        wieder moeglich") nie zu frueh zum Wiederholen einlaedt."""
        moment = time.monotonic() if now is None else now
        blocked_until = self._blocked_until.get(client)
        if blocked_until is None or blocked_until <= moment:
            return 0
        return int(blocked_until - moment) + 1

    def record_failure(self, client: str, *, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        count = self._failures.get(client, 0) + 1
        self._failures[client] = count
        if count >= FAILURES_BEFORE_THROTTLING:
            self._blocked_until[client] = moment + THROTTLE_SECONDS

    def record_success(self, client: str) -> None:
        """Setzt Zaehler und Sperre zurueck - wer das Passwort kennt, ist
        kein Angreifer, auch wenn er sich vorher fuenfmal vertippt hat."""
        self._failures.pop(client, None)
        self._blocked_until.pop(client, None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/auth/test_throttle.py -v`
Expected: PASS (6 Tests)

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check src tests && uv run mypy
git add src/loxmatter/auth/throttle.py tests/auth/test_throttle.py
git commit -m "feat(auth): Drosselung nach fuenf Fehlversuchen je Adresse"
```

---

### Task 5: Der Auth-Router

**Files:**
- Create: `src/loxmatter/api/auth.py`
- Modify: `src/loxmatter/loxone/server.py` (Import und `app.include_router`)
- Test: `tests/api/test_auth.py`

**Interfaces:**
- Consumes: `hash_password`, `verify_password`, `MIN_PASSWORD_LENGTH` (Task 2); `SESSION_COOKIE`, `SESSION_LIFETIME_SECONDS`, `open_session`, `session_is_valid` (Task 3); `LoginThrottle` (Task 4); `Store.auth` (Task 1).
- Produces: `build_auth_router(store: Store) -> APIRouter` mit den vier Routen; die Antwortmodelle `AuthInfoOut` (`password_set: bool`, `authenticated: bool`) und `StatusOut` (`status: str`).

- [ ] **Step 1: Write the failing test**

`tests/api/test_auth.py`:

```python
"""Tests fuer die vier Zugangs-Routen (Spec 8).

Sie haengen als einzige unter `/auth` ausserhalb des Waechters - sie muessen
unangemeldet erreichbar sein, sonst koennte sich niemand anmelden.

`httpx.AsyncClient` fuehrt einen eigenen Cookie-Speicher: was `POST
/auth/login` setzt, schickt jede weitere Anfrage desselben Clients von
selbst mit. Genau so verhaelt sich auch der Browser.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from conftest import load_snapshot

from loxmatter.auth.passwords import hash_password
from loxmatter.auth.throttle import FAILURES_BEFORE_THROTTLING
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store

PASSWORT = "ein-gutes-passwort"


class _NullSender:
    def send(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture
async def auth_client(tmp_path: Path, no_invoke: Any) -> AsyncIterator[tuple[httpx.AsyncClient, Store]]:
    """Eine App ohne gesetztes Passwort - der Zustand der Ersteinrichtung."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    runtime = Runtime(store, _NullSender())
    app = build_app(store, no_invoke, runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store
    store.close()


async def test_auth_info_reports_an_unconfigured_service(auth_client):
    client, _ = auth_client
    response = await client.get("/auth-info")
    assert response.status_code == 200
    assert response.json() == {"password_set": False, "authenticated": False}


async def test_setup_sets_the_password_and_logs_in(auth_client):
    client, store = auth_client
    response = await client.post("/auth/setup", json={"password": PASSWORT})
    assert response.status_code == 200
    assert store.auth.password_hash() is not None
    assert (await client.get("/auth-info")).json() == {
        "password_set": True,
        "authenticated": True,
    }


async def test_setup_is_closed_for_good_once_a_password_is_set(auth_client):
    client, _ = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})
    second = await client.post("/auth/setup", json={"password": "ein-anderes-passwort"})
    assert second.status_code == 409


async def test_setup_rejects_a_short_password(auth_client):
    client, store = auth_client
    response = await client.post("/auth/setup", json={"password": "kurz"})
    assert response.status_code == 422
    assert store.auth.password_hash() is None


async def test_login_with_the_right_password_authenticates(auth_client):
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 200
    assert (await client.get("/auth-info")).json()["authenticated"] is True


async def test_login_with_a_wrong_password_is_rejected(auth_client):
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    response = await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    assert response.status_code == 401
    assert (await client.get("/auth-info")).json()["authenticated"] is False


async def test_login_before_setup_says_so(auth_client):
    """409 und nicht 401: es gibt kein Passwort, mit dem dieser Aufruf
    gelingen koennte - eine Wiederholung mit Zugangsdaten hilft nicht."""
    client, _ = auth_client
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 409


async def test_repeated_wrong_passwords_are_throttled(auth_client):
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    for _ in range(FAILURES_BEFORE_THROTTLING):
        await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 429


async def test_logout_ends_the_session_on_the_server(auth_client):
    """Nicht nur das Cookie loeschen: derselbe Wert darf danach nicht mehr
    gelten, sonst lebt eine gestohlene Kennung weiter."""
    client, store = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})
    session_id = client.cookies.get("loxmatter_session")
    assert session_id is not None

    await client.post("/auth/logout")
    assert (await client.get("/auth-info")).json()["authenticated"] is False

    client.cookies.set("loxmatter_session", session_id)
    assert (await client.get("/auth-info")).json()["authenticated"] is False


async def test_no_response_ever_contains_the_password_or_its_hash(auth_client):
    client, store = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})
    stored = store.auth.password_hash()
    assert stored is not None
    for response in [
        await client.get("/auth-info"),
        await client.post("/auth/login", json={"password": PASSWORT}),
    ]:
        assert PASSWORT not in response.text
        assert stored not in response.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_auth.py -v`
Expected: FAIL — 404 auf allen vier Routen (der Router existiert noch nicht)

- [ ] **Step 3: Write the router**

`src/loxmatter/api/auth.py`:

```python
"""Die vier Zugangs-Routen: `/auth-info`, `/auth/setup`, `/auth/login`,
`/auth/logout` (Spec 8).

**Diese Routen haengen als einzige NICHT hinter `build_api_guard`** - sie
muessen unangemeldet erreichbar sein, sonst koennte sich niemand anmelden.
Sie werden in `loxone.server.build_app` deshalb bewusst ohne
`dependencies=api_guard` eingebunden, neben `/health`.

Was sie deshalb NICHT ausliefern: irgendetwas ueber den Zustand der Bruecke.
`/auth-info` sagt genau zwei Wahrheitswerte - ob ein Passwort gesetzt ist und
ob DIESER Aufrufer angemeldet ist. Beides erfaehrt ein Aufrufer ohnehin
daran, wie `/api/devices` ihm antwortet; hier steht es nur so, dass die
Oberflaeche nicht raten muss, welchen Bildschirm sie zeigt.

**Kein Geheimnis verlaesst dieses Modul.** Weder Passwort noch Hash noch
Sitzungskennung erscheinen in einer Antwort (die Kennung reist
ausschliesslich im `Set-Cookie`) oder in einem Log - in keinem Zweig.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from loxmatter.auth.passwords import MIN_PASSWORD_LENGTH, hash_password, verify_password
from loxmatter.auth.sessions import (
    SESSION_COOKIE,
    SESSION_LIFETIME_SECONDS,
    open_session,
    session_is_valid,
)
from loxmatter.auth.throttle import LoginThrottle
from loxmatter.model.store import Store


class PasswordIn(BaseModel):
    password: str


class AuthInfoOut(BaseModel):
    password_set: bool
    authenticated: bool


class StatusOut(BaseModel):
    status: str


def build_auth_router(store: Store) -> APIRouter:
    router = APIRouter()
    # Eine Instanz je App, nicht je Anfrage - sonst zaehlte sie nichts.
    throttle = LoginThrottle()

    def _require_length(password: str) -> None:
        """Eigene Pruefung statt `Field(min_length=...)` am Modell: die
        Meldung landet in der Oberflaeche und soll dort auf Deutsch stehen
        und sagen, was zu tun ist - nicht als pydantic-Fehlerliste."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben."
                ),
            )

    def _start_session(response: Response) -> None:
        """Legt eine Sitzung an und haengt das Cookie an die Antwort.

        `secure` fehlt hier ABSICHTLICH und darf nicht "der Sicherheit
        halber" ergaenzt werden: dieser Dienst spricht HTTP (Spec 14.1), ein
        `Secure`-Cookie wuerde vom Browser verworfen und niemand kaeme mehr
        hinein. `samesite="strict"` ist zugleich der CSRF-Schutz - eine
        fremde Seite kann damit keine zustandsaendernde Anfrage in einer
        angemeldeten Sitzung ausloesen, weshalb es kein eigenes CSRF-Token
        gibt."""
        response.set_cookie(
            SESSION_COOKIE,
            open_session(store.auth),
            max_age=SESSION_LIFETIME_SECONDS,
            httponly=True,
            samesite="strict",
            path="/",
        )

    @router.get("/auth-info")
    async def auth_info(request: Request) -> AuthInfoOut:
        session_id = request.cookies.get(SESSION_COOKIE)
        return AuthInfoOut(
            password_set=store.auth.password_hash() is not None,
            authenticated=(
                session_id is not None and session_is_valid(store.auth, session_id)
            ),
        )

    @router.post("/auth/setup")
    async def setup(body: PasswordIn, response: Response) -> StatusOut:
        """Ersteinrichtung - ohne weiteren Nachweis, solange kein Passwort
        gesetzt ist (Spec 5, Trust on first use).

        Das ist eine bewusst getroffene Abwaegung und keine vergessene
        Pruefung: zwischen dem Start ohne Passwort und dieser Vergabe kann
        jeder, der den Dienst erreicht, ihn uebernehmen. Entschieden am
        3. September 2026 gegen Einrichtungscode im Log, Zeitfenster und
        Erstpasswort per CLI, damit die Einrichtung headless ueber die
        Oberflaeche moeglich bleibt - und ausdruecklich auch fuer ein
        Bestandssystem mit bereits konfiguriertem Token, das hier NICHT
        zusaetzlich abgefragt wird.

        `set_password_hash_if_unset` entscheidet in einer einzigen
        SQL-Anweisung, ob dieser Aufruf der erste war - deshalb koennen zwei
        gleichzeitige Einrichtungen sich nicht ueberschreiben."""
        _require_length(body.password)
        if not store.auth.set_password_hash_if_unset(hash_password(body.password)):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Für diesen Dienst ist bereits ein Passwort vergeben – die "
                    "Ersteinrichtung ist damit dauerhaft abgeschlossen. Passwort "
                    "vergessen? `loxmatter set-password` auf dem Host setzt es neu."
                ),
            )
        _start_session(response)
        return StatusOut(status="ok")

    @router.post("/auth/login")
    async def login(body: PasswordIn, request: Request, response: Response) -> StatusOut:
        # Die Peer-Adresse der Verbindung, NICHT `X-Forwarded-For`: den
        # Header setzt jeder Aufrufer selbst, und die Drosselung liesse sich
        # damit umgehen, indem man je Versuch eine andere Adresse behauptet.
        client = request.client.host if request.client is not None else "unbekannt"
        wait = throttle.retry_after(client)
        if wait:
            raise HTTPException(
                status_code=429,
                detail=f"Zu viele Fehlversuche – in {wait} Sekunden wieder möglich.",
            )
        stored = store.auth.password_hash()
        if stored is None:
            # 409 und nicht 401: es gibt kein Passwort, mit dem dieser Aufruf
            # gelingen koennte - eine Wiederholung mit Zugangsdaten hilft
            # nicht (dieselbe Unterscheidung wie in RFC 9110).
            raise HTTPException(
                status_code=409,
                detail=(
                    "Für diesen Dienst ist noch kein Passwort vergeben – bitte zuerst "
                    "die Ersteinrichtung abschließen."
                ),
            )
        if not verify_password(body.password, stored):
            throttle.record_failure(client)
            raise HTTPException(status_code=401, detail="Falsches Passwort.")
        throttle.record_success(client)
        _start_session(response)
        return StatusOut(status="ok")

    @router.post("/auth/logout")
    async def logout(request: Request, response: Response) -> StatusOut:
        """Beendet die Sitzung SERVERSEITIG und raeumt danach das Cookie ab.

        Die Reihenfolge ist der Punkt: ein Logout, der nur das Cookie
        loescht, laesst eine bereits abgeflossene Kennung dreissig Tage
        weiterleben."""
        session_id = request.cookies.get(SESSION_COOKIE)
        if session_id is not None:
            store.auth.delete_session(session_id)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return StatusOut(status="ok")

    return router
```

- [ ] **Step 4: Wire the router into the app**

In `src/loxmatter/loxone/server.py` den Import ergänzen:

```python
from loxmatter.api.auth import build_auth_router
```

und in `build_app` direkt vor `app.mount("/static", ...)` einhängen:

```python
    # OHNE `dependencies=api_guard` - genau wie `/health`, `/cmd` und
    # `/resync` weiter unten. Wer sich noch nicht angemeldet hat, muss diese
    # vier Routen erreichen koennen, sonst gibt es keinen Weg hinein
    # (siehe api/auth.py, Moduldocstring).
    app.include_router(build_auth_router(store))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_auth.py -v`
Expected: PASS (10 Tests)

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS — die neuen Routen ändern an den bestehenden nichts.

- [ ] **Step 7: Lint, typecheck, commit**

```bash
uv run ruff check src tests && uv run mypy
git add src/loxmatter/api/auth.py src/loxmatter/loxone/server.py tests/api/test_auth.py
git commit -m "feat(api): Routen fuer Ersteinrichtung, Login und Logout"
```

---

### Task 6: Der Wächter akzeptiert das Sitzungs-Cookie

**Files:**
- Modify: `src/loxmatter/loxone/server.py` (`build_api_guard`, `build_app`)
- Test: `tests/api/test_security.py`

**Interfaces:**
- Consumes: `SESSION_COOKIE`, `session_is_valid` (Task 3); `Store` (Task 1).
- Produces: `build_api_guard(token: str | None, store: Store)` — **die Signatur bekommt einen zweiten, verpflichtenden Parameter.** Jeder Aufrufer muss den Store mitgeben.

Dieser Task ist additiv: der Wächter lässt zusätzlich Cookies durch, verweigert aber noch nichts, was er heute durchlässt. Die Sperre kommt in Task 8.

- [ ] **Step 1: Write the failing test**

Die Tests brauchen Zugriff auf den Store der App, um ein Passwort zu setzen. Deshalb **zuerst** in `tests/api/test_security.py` `_build_client` und die beiden Fixtures so ändern, dass sie ihn mitgeben:

```python
async def _build_client(
    tmp_path: Path, no_invoke: Any, *, api_token: str | None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, int, Store]]:
    """Baut Store, eine ECHTE `Runtime` (fuer `/resync`) und die App mit dem
    gegebenen `api_token` - gemeinsamer Aufbau fuer `secured_client` und
    `open_client` unten, die sich nur in `api_token` unterscheiden.

    Gibt seit dem WebUI-Login auch den `Store` mit heraus: die Tests
    brauchen ihn, um ein Passwort zu setzen und sich anzumelden."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
    runtime = Runtime(store, FakeSender())

    app = build_app(
        store,
        no_invoke,
        runtime,
        matter_data_dir=_matter_data_dir(tmp_path),
        api_token=api_token,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, app, device_id, store
    store.close()
```

Beide Fixtures (`secured_client`, `open_client`) geben das Vier-Tupel unverändert weiter; jeder bestehende Test in dieser Datei, der `client, app, device_id = ...` auspackt, wird auf `client, app, device_id, store = ...` erweitert (der Testlauf zeigt, welche das sind).

Danach der eigentliche neue Test:

```python
async def test_a_session_cookie_opens_every_api_router(secured_client):
    """Der zweite Nachweis neben dem Token: wer angemeldet ist, kommt ohne
    `Authorization`-Header durch jede der fuenf Router-Gruppen."""
    client, _app, device_id, store = secured_client
    store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
    assert (
        await client.post("/auth/login", json={"password": "ein-gutes-passwort"})
    ).status_code == 200

    for path in [
        "/api/devices",
        f"/api/devices/{device_id}/controls",
        "/api/export/status",
        "/api/diagnostics/system",
    ]:
        response = await client.get(path)
        assert response.status_code == 200, f"{path} verlangte trotz Sitzung eine Anmeldung"


async def test_an_invalid_session_cookie_does_not_open_anything(secured_client):
    client, _app, _device_id, _store = secured_client
    client.cookies.set("loxmatter_session", "erfunden")
    assert (await client.get("/api/devices")).status_code == 401


async def test_the_token_still_works_next_to_the_cookie(secured_client):
    """Der Weg fuer Skripte bleibt unveraendert - er ist der Grund, warum
    das Token ueberhaupt bestehen bleibt."""
    client, _app, _device_id, _store = secured_client
    response = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200


async def test_the_live_websocket_connects_with_a_cookie_and_no_subprotocol(secured_client):
    """Der Punkt, an dem der Umweg ueber das Subprotokoll ueberfluessig wird:
    das Cookie reist beim Handshake von selbst mit, weil dieser WebSocket
    denselben Ursprung hat wie die Seite. Genau darauf verlaesst sich
    `app.js`, seit dort `new WebSocket(url)` ohne zweites Argument steht."""
    client, app, _device_id, store = secured_client
    store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
    login = await client.post("/auth/login", json={"password": "ein-gutes-passwort"})
    assert login.status_code == 200
    session_id = client.cookies.get("loxmatter_session")
    assert session_id is not None

    status = await _websocket_handshake_status(
        app, headers=[(b"cookie", f"loxmatter_session={session_id}".encode())]
    )
    assert status is None, "Der Handshake wurde trotz gueltiger Sitzung abgelehnt"
```

`_websocket_handshake_status` gibt es in dieser Datei bereits (sie prüft damit heute die Ablehnung vor `websocket.accept()`); der Aufruf ist um einen `headers`-Parameter zu erweitern, falls er ihn noch nicht kennt — der bestehende Aufbau der Funktion zeigt, wie die Handshake-Kopfzeilen dort gesetzt werden. `None` steht für „nicht abgelehnt", also einen zustande gekommenen Handshake.

Import am Kopf der Datei ergänzen:

```python
from loxmatter.auth.passwords import hash_password
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_security.py -v`
Expected: FAIL — `test_a_session_cookie_opens_every_api_router` bekommt 401, weil der Wächter das Cookie noch nicht kennt.

- [ ] **Step 3: Teach the guard about cookies**

In `src/loxmatter/loxone/server.py` die Imports ergänzen:

```python
from starlette.requests import HTTPConnection

from loxmatter.auth.sessions import SESSION_COOKIE, session_is_valid
```

`build_api_guard` umschreiben (Signatur und Rumpf; der vorhandene Docstring bleibt und bekommt den neuen Abschnitt):

```python
def build_api_guard(token: str | None, store: Store) -> Callable[..., Awaitable[None]]:
    """Schuetzt die `/api`-Routen, nicht die des Miniservers (Task 8, Phase 5).

    ... (bestehender Docstring unveraendert) ...

    **Seit dem WebUI-Login gibt es zwei Nachweise statt einem.** Zuerst das
    Sitzungs-Cookie (`loxmatter_session`, siehe `auth.sessions`), dann das
    Bearer-Token. Das Cookie ist der Weg des Browsers, das Token der von
    Skripten und `curl` - deshalb wird das Cookie zuerst geprueft: es ist
    der haeufigere Fall, und es kostet einen SELECT statt eines
    Hash-Vergleichs.

    `HTTPConnection` statt `Request`: es ist der gemeinsame Basistyp von
    `Request` und `WebSocket`, und dieselbe Abhaengigkeit haengt an beiden
    Sorten von Routen - `/api/live` ist eine WebSocket-Route, in der ein
    `Request`-Parameter gar nicht aufloesbar waere. Das Cookie reist beim
    WebSocket-Handshake von selbst mit (gleicher Ursprung), weshalb der
    Browser dort seit dem Login kein Subprotokoll mehr braucht.
    """
    expected = normalize_api_token(token)

    async def guard(
        conn: HTTPConnection,
        authorization: str | None = Header(default=None),
        sec_websocket_protocol: str | None = Header(default=None),
    ) -> None:
        session_id = conn.cookies.get(SESSION_COOKIE)
        if session_id is not None and session_is_valid(store.auth, session_id):
            return
        if expected is None:
            return
        presented = _token_from_authorization(authorization)
        if presented is None:
            presented = _token_from_websocket_subprotocol(sec_websocket_protocol)
        if presented is None or not _tokens_match(presented, expected):
            raise HTTPException(status_code=401, detail="Ungültiges oder fehlendes Token")

    return guard
```

In `build_app` den Aufruf anpassen:

```python
    api_guard = [Depends(build_api_guard(api_token, store))]
```

- [ ] **Step 4: Fix the direct guard tests**

`tests/api/test_security.py` enthält `test_guard_*`-Tests, die `build_api_guard` ohne App aufrufen. Sie brauchen jetzt einen Store — in dieser Datei einen Helfer ergänzen und alle `build_api_guard(...)`-Aufrufe darauf umstellen:

```python
@pytest.fixture
def guard_store(tmp_path):
    """Ein leerer Store fuer die Tests, die `build_api_guard` direkt aufrufen -
    ohne Passwort und ohne Sitzung, damit dort weiterhin allein das Token
    ueber Durchlassen oder Ablehnen entscheidet."""
    store = Store(tmp_path / "guard.sqlite")
    yield store
    store.close()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_security.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS — `build_api_guard` hat nur einen Parameter dazubekommen, `build_app` reicht ihn selbst durch; kein anderer Aufrufer existiert.

- [ ] **Step 7: Lint, typecheck, commit**

```bash
uv run ruff check src tests && uv run mypy
git add src/loxmatter/loxone/server.py tests/api/test_security.py
git commit -m "feat(server): Waechter akzeptiert das Sitzungs-Cookie neben dem Token"
```

---

### Task 7: Die Oberfläche — Einrichtung, Login, keine Token-Box

**Files:**
- Modify: `src/loxmatter/web/app.js`, `src/loxmatter/web/index.html`, `src/loxmatter/web/style.css`
- Test: `tests/api/test_web.py` (nur die Auslieferungstests, siehe Schritt 6)

**Interfaces:**
- Consumes: `GET /auth-info`, `POST /auth/setup`, `POST /auth/login`, `POST /auth/logout` (Task 5); das Cookie wird vom Browser gesetzt und mitgeschickt.
- Produces: nichts für spätere Tasks.

- [ ] **Step 1: Strip the token plumbing out of `app.js`**

Ersatzlos löschen: die Konstanten `TOKEN_STORAGE_KEY` und `WEBSOCKET_BEARER_MARKER` samt ihrer Kommentarblöcke (Zeilen um 33–62), die Funktionen `readStoredToken` und `authHeaders` (um 65–90), und in `app()` die Methoden `tokenStatusText`, `startTokenEdit`, `cancelTokenEdit`, `saveToken`, `clearToken`, `reloadAfterTokenChange` sowie die Zustandsfelder `tokenIsSet`, `tokenEditing`, `tokenDraft`.

- [ ] **Step 2: Point the two `fetch` calls at the cookie**

In `requestJson` den Kopfzeilen-Block ersetzen:

```javascript
    response = await fetch(path, {
      method,
      // Das Sitzungs-Cookie statt eines Tokens im Header: `same-origin`
      // schickt es an genau den Ursprung mit, von dem diese Seite geladen
      // wurde, und an keinen anderen. Ein `Authorization`-Header wird hier
      // nicht mehr gesetzt - der Weg ueber das Token gibt es weiterhin,
      // aber fuer Skripte, nicht fuer diesen Browser (siehe api/auth.py).
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
```

In `requestDownload` entsprechend:

```javascript
    response = await fetch(path, { credentials: "same-origin" });
```

Den Docstring über `requestDownload` anpassen — er verweist heute auf `authHeaders()`:

```javascript
/**
 * Laedt eine Datei von `/api` herunter. Ueber `fetch` und nicht ueber ein
 * `<a href>`, weil eine 401 sonst als roher Fehlertext im Browserfenster
 * landete statt in der Oberflaeche - und weil der Blob-Download so den
 * Dateinamen setzen kann.
 */
```

- [ ] **Step 3: Rewrite `UnauthorizedError` and `noteAuthError`**

```javascript
/**
 * Fehler eines Aufrufs ohne gueltige Sitzung - eigene Klasse, damit die
 * Oberflaeche diesen Fall von jedem anderen Fehlschlag unterscheiden kann,
 * ohne auf einen Meldungstext zu pruefen.
 */
class UnauthorizedError extends Error {
  constructor() {
    super("Die Sitzung ist abgelaufen – bitte erneut anmelden.");
    this.name = "UnauthorizedError";
  }
}
```

und in `app()`:

```javascript
    /**
     * Eine 401 mitten im Betrieb heisst: die Sitzung ist abgelaufen oder
     * wurde anderswo beendet. Dann zurueck auf den Login-Bildschirm - eine
     * Fehlermeldung, die auf ein Eingabefeld verweist, das es nicht mehr
     * gibt, waere schlimmer als gar keine.
     */
    noteAuthError(error) {
      if (error instanceof UnauthorizedError) {
        this.authenticated = false;
        this.authError = error.message;
      }
    },
```

- [ ] **Step 4: Add the auth state and screens to `app()`**

Die Zustandsfelder (an die Stelle des gelöschten Zugangs-Blocks):

```javascript
    // --- Zugang -----------------------------------------------------------
    // `authReady` verhindert das Aufblitzen des falschen Bildschirms: bis
    // `/auth-info` geantwortet hat, weiss die Seite nicht, ob sie Einrichtung,
    // Login oder die App zeigen muss, und zeigt deshalb keines davon.
    authReady: false,
    passwordSet: false,
    authenticated: false,
    passwordDraft: "",
    passwordRepeatDraft: "",
    authBusy: false,
    authError: null,
```

`init()` und die Zugangs-Methoden:

```javascript
    async init() {
      await this.loadAuthInfo();
      if (this.authenticated) {
        await this.startApp();
      }
    },

    /** Fragt den Zustand des Zugangs ab - der erste Aufruf jeder Seite. */
    async loadAuthInfo() {
      try {
        const info = await requestJson("GET", "/auth-info");
        this.passwordSet = info.password_set;
        this.authenticated = info.authenticated;
      } catch (error) {
        this.authError = error.message;
      } finally {
        this.authReady = true;
      }
    },

    /**
     * Alles, was eine angemeldete Sitzung voraussetzt. Getrennt von `init`,
     * weil es nach dem Login ein zweites Mal laufen muss - dann ohne
     * Neuladen der Seite.
     */
    async startApp() {
      await this.loadDevices();
      this.connectLive();
    },

    async submitSetup() {
      if (this.passwordDraft !== this.passwordRepeatDraft) {
        this.authError = "Die beiden Eingaben stimmen nicht überein.";
        return;
      }
      await this.submitPassword("/auth/setup");
    },

    async submitLogin() {
      await this.submitPassword("/auth/login");
    },

    /**
     * Der gemeinsame Teil von Einrichtung und Login: absenden, Fehler
     * anzeigen, bei Erfolg die App starten. Das Cookie setzt der Server,
     * diese Seite fasst es nie an (es ist `HttpOnly`).
     */
    async submitPassword(path) {
      this.authBusy = true;
      this.authError = null;
      try {
        await requestJson("POST", path, { password: this.passwordDraft });
      } catch (error) {
        this.authError = error.message;
        return;
      } finally {
        this.authBusy = false;
        // In jedem Fall: ein Passwort bleibt nicht im Speicher der Seite
        // stehen, auch nicht nach einem Fehlversuch.
        this.passwordDraft = "";
        this.passwordRepeatDraft = "";
      }
      this.passwordSet = true;
      this.authenticated = true;
      await this.startApp();
    },

    async logout() {
      try {
        await requestJson("POST", "/auth/logout");
      } catch {
        // Auch ein fehlgeschlagener Logout soll abmelden: das Neuladen
        // unten verwirft jeden geladenen Stand, und ohne gueltige Sitzung
        // kommt die Seite ohnehin nur bis zum Login-Bildschirm.
      }
      window.location.reload();
    },
```

`requestJson` wirft bei 401 einen `UnauthorizedError`, dessen Text für den Login-Bildschirm falsch wäre („Sitzung abgelaufen" bei einem Tippfehler im Passwort). Deshalb in `submitPassword` **nicht** `this.request` verwenden, sondern `requestJson` direkt — der 401-Text des Servers („Falsches Passwort.") kommt dann nicht durch. Damit die Meldung stimmt, in `requestJson` den 401-Zweig auf den Pfad einschränken:

```javascript
  if (response.status === 401 && !path.startsWith("/auth/")) {
    throw new UnauthorizedError();
  }
  if (!response.ok) {
    throw new Error(await readErrorDetail(response));
  }
```

So trägt ein fehlgeschlagener Login den Servertext („Falsches Passwort.", „Zu viele Fehlversuche – in X Sekunden wieder möglich."), während eine 401 an `/api` weiterhin auf den Login-Bildschirm führt.

- [ ] **Step 5: Simplify `connectLive`**

Den gesamten Token-Block (Kommentar, `readStoredToken`, `try`/`catch` um den Konstruktor) ersetzen durch:

```javascript
    connectLive() {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${protocol}//${window.location.host}/api/live`;
      // Kein Subprotokoll mehr: das Sitzungs-Cookie reist beim Handshake von
      // selbst mit, weil dieser WebSocket denselben Ursprung hat wie die
      // Seite. Der frueher noetige Umweg `new WebSocket(url, ["bearer",
      // token])` - und mit ihm der Sonderfall, dass ein Token mit Leerzeichen
      // den Konstruktor synchron werfen liess - entfaellt ersatzlos. Der
      // Server liest das Subprotokoll weiterhin, aber fuer Skripte (siehe
      // `loxone.server.build_api_guard`).
      const socket = new WebSocket(url);
```

Der Rest der Methode (die drei `addEventListener`) bleibt unverändert.

- [ ] **Step 6: Replace the token box in `index.html`**

Den kompletten `<div class="token-box">`-Block samt HTML-Kommentar darüber ersetzen durch einen Abmelde-Knopf:

```html
        <!--
          Zugang: seit dem WebUI-Login gibt es hier kein Token-Feld mehr,
          sondern nur den Weg hinaus. Der Weg hinein sind die beiden
          Bildschirme unter dieser Kopfzeile.
        -->
        <button class="logout" x-show="authenticated" x-cloak @click="logout()">
          Abmelden
        </button>
```

Direkt nach `<body x-data="app()">` und **vor** `<header>` die beiden Bildschirme einfügen; der bisherige Inhalt (`<header>` bis `</main>`) wird in ein `<template x-if="authenticated">` gehängt, damit ohne Sitzung nichts davon steht:

```html
    <!-- Bis `/auth-info` geantwortet hat, zeigt die Seite bewusst nichts -
         sonst blitzte je nach Antwort der falsche Bildschirm auf. -->
    <template x-if="authReady && !authenticated && !passwordSet">
      <main class="auth-screen">
        <h1>loxmatter einrichten</h1>
        <p class="banner warn">
          Für diese Brücke ist noch kein Passwort vergeben. Bis das geschehen ist, kann
          <strong>jeder im Netz</strong> sie übernehmen, indem er dieses Formular ausfüllt.
          Schließe die Einrichtung deshalb jetzt ab und nicht später.
        </p>
        <label>
          Passwort
          <input type="password" autocomplete="new-password" x-model="passwordDraft"
                 @keydown.enter="submitSetup()" />
        </label>
        <label>
          Passwort wiederholen
          <input type="password" autocomplete="new-password" x-model="passwordRepeatDraft"
                 @keydown.enter="submitSetup()" />
        </label>
        <p class="hint">
          Mindestens 8 Zeichen. Dieser Dienst spricht HTTP ohne Verschlüsselung – nimm ein
          Passwort, das du nirgendwo sonst benutzt.
        </p>
        <p class="banner danger" x-show="authError" x-cloak x-text="authError"></p>
        <button class="primary" :disabled="authBusy" @click="submitSetup()">
          Passwort vergeben
        </button>
      </main>
    </template>

    <template x-if="authReady && !authenticated && passwordSet">
      <main class="auth-screen">
        <h1>loxmatter</h1>
        <label>
          Passwort
          <input type="password" autocomplete="current-password" x-model="passwordDraft"
                 @keydown.enter="submitLogin()" />
        </label>
        <p class="banner danger" x-show="authError" x-cloak x-text="authError"></p>
        <button class="primary" :disabled="authBusy" @click="submitLogin()">Anmelden</button>
      </main>
    </template>
```

In `style.css` die Regeln für `.token-box` und `.token-input` löschen und ersetzen:

```css
/* Der Einrichtungs- und der Login-Bildschirm: eine schmale Spalte in der
   Seitenmitte, damit klar ist, dass hier nichts anderes zu tun ist. */
.auth-screen {
  max-width: 26rem;
  margin: 4rem auto;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.auth-screen label {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.auth-screen input {
  padding: 0.5rem;
  font-size: 1rem;
}

.auth-screen .hint {
  font-size: 0.85rem;
  opacity: 0.8;
}
```

- [ ] **Step 7: Verify by hand**

Run:

```bash
rm -f /tmp/loxmatter-ui.sqlite && uv run loxmatter run --miniserver 192.0.2.1 --store-path /tmp/loxmatter-ui.sqlite --listen 8099
```

Im Browser `http://localhost:8099/` öffnen. Erwartet, der Reihe nach:
1. Einrichtungsbildschirm mit der Warnung, kein Token-Feld irgendwo.
2. Zwei ungleiche Eingaben → „Die beiden Eingaben stimmen nicht überein."
3. Ein Passwort unter 8 Zeichen → „Das Passwort muss mindestens 8 Zeichen haben."
4. Gültiges Passwort zweimal → die App erscheint ohne Neuladen, der Verbindungspunkt geht auf „verbunden" (der WebSocket trägt jetzt das Cookie).
5. Seite neu laden → weiterhin angemeldet.
6. „Abmelden" → Login-Bildschirm. Falsches Passwort → „Falsches Passwort.". Fünf Fehlversuche → „Zu viele Fehlversuche – in X Sekunden wieder möglich.".
7. Richtiges Passwort → App wieder da.

Danach `rm -f /tmp/loxmatter-ui.sqlite`.

- [ ] **Step 8: Run the full suite, lint, commit**

```bash
uv run pytest && uv run ruff check src tests && uv run mypy
git add src/loxmatter/web
git commit -m "feat(web): Einrichtungs- und Login-Bildschirm statt Token-Eingabe"
```

---

### Task 8: Ohne Passwort liefert `/api` nichts mehr aus

**Files:**
- Modify: `src/loxmatter/loxone/server.py` (`build_api_guard`), `src/loxmatter/cli.py` (`_warn_if_missing_api_token` → `_warn_if_no_password`)
- Modify: `tests/api/conftest.py` (neuer Helfer) und jede Testdatei mit `build_app(...)`
- Test: `tests/api/test_security.py`

**Interfaces:**
- Consumes: alles aus Task 1–6.
- Produces: `tests/api/conftest.py` exportiert `TEST_PASSWORD: str` und `async def authenticate(store: Store, client: httpx.AsyncClient) -> None`.

**Das ist der brechende Task.** Nach Schritt 3 schlagen alle API-Tests fehl, bis Schritt 5 die Fixtures nachgezogen hat. Das ist erwartet und der Grund, warum beides in einem Task liegt.

- [ ] **Step 1: Write the failing test**

An `tests/api/test_security.py` anhängen:

```python
async def test_without_a_password_every_api_route_is_closed(open_client):
    """Die Verschaerfung aus Spec 4: bis hierher war genau dieser Zustand -
    kein Passwort, kein Token - vollstaendig offen, mit nichts als einer
    Warnung im Log."""
    client, _app, device_id, _store = open_client
    for path in [
        "/api/devices",
        f"/api/devices/{device_id}/controls",
        "/api/export/status",
        "/api/diagnostics/system",
        "/api/diagnostics/fabric-backup",
    ]:
        response = await client.get(path)
        assert response.status_code == 401, f"{path} lieferte ohne Passwort noch Daten aus"


async def test_without_a_password_the_miniserver_routes_stay_open(open_client):
    """`/cmd` und `/resync` bleiben in JEDEM Zustand offen - der Miniserver
    kann weder Header noch Cookie mitschicken."""
    client, _app, _device_id, _store = open_client
    assert (await client.get("/resync")).status_code == 200
    assert (await client.get("/health")).status_code == 200


async def test_without_a_password_a_configured_token_still_works(secured_client):
    """Der Bestandsfall unmittelbar nach dem Update: das Passwort fehlt
    noch, das Token steht in der `.env` - Skripte duerfen dadurch nicht
    abreissen."""
    client, _app, _device_id, _store = secured_client
    response = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200


async def test_a_password_alone_is_enough(open_client):
    """Kein Token konfiguriert, aber angemeldet - der Normalfall nach der
    Ersteinrichtung."""
    client, _app, _device_id, store = open_client
    store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
    await client.post("/auth/login", json={"password": "ein-gutes-passwort"})
    assert (await client.get("/api/devices")).status_code == 200
```

Die bestehenden Tests dieser Datei, die auf `open_client` einen offenen Zugriff erwarten, kehren ihre Erwartung um: was dort `200` erwartete, erwartet jetzt `401`. Der Testlauf in Schritt 4 zeigt, welche das sind; ihre Docstrings sind entsprechend nachzuziehen (sie beschreiben heute „der Zustand vor Task 8 bzw. eine Installation, die (noch) keins gesetzt hat").

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_security.py -v`
Expected: FAIL — `test_without_a_password_every_api_route_is_closed` bekommt 200 statt 401.

- [ ] **Step 3: Close the guard**

In `build_api_guard` den Zweig `if expected is None: return` entfernen und den Rumpf so umschreiben:

```python
    async def guard(
        conn: HTTPConnection,
        authorization: str | None = Header(default=None),
        sec_websocket_protocol: str | None = Header(default=None),
    ) -> None:
        session_id = conn.cookies.get(SESSION_COOKIE)
        if session_id is not None and session_is_valid(store.auth, session_id):
            return
        if expected is not None:
            presented = _token_from_authorization(authorization)
            if presented is None:
                presented = _token_from_websocket_subprotocol(sec_websocket_protocol)
            if presented is not None and _tokens_match(presented, expected):
                return
        raise HTTPException(
            status_code=401,
            detail=(
                "Anmeldung erforderlich – bitte die Oberfläche öffnen und anmelden. "
                "Skripte verwenden `Authorization: Bearer <Token>` mit dem unter "
                "LOXMATTER_API_TOKEN gesetzten Wert."
            ),
        )
```

Den Docstring-Abschnitt „Kein Token gesetzt …" ersetzen:

```
    **Es gibt keinen offenen Zustand mehr.** Bis hierher liess ein Dienst
    ohne konfiguriertes Token jede `/api`-Route durch und begnuegte sich mit
    einer Warnung im Log - wer die Warnung ueberlas, betrieb eine offene
    Bruecke, ohne es zu merken. Seit dem WebUI-Login gilt: ohne gueltiges
    Cookie und ohne gueltiges Token endet jede Anfrage hier mit 401, auch
    wenn weder Passwort noch Token eingerichtet sind. Der Weg hinein ist
    dann ausschliesslich die Ersteinrichtung unter `/auth/setup`, die
    ausserhalb dieses Waechters haengt (siehe `api/auth.py`).
```

- [ ] **Step 4: Run the suite and see what breaks**

Run: `uv run pytest`
Expected: FAIL — jeder Test in `tests/api/`, der `/api` ohne Passwort aufruft. Die Liste dieses Laufs ist die Arbeitsliste für Schritt 5.

- [ ] **Step 5: Add the helper and authenticate every fixture**

An `tests/api/conftest.py` anhängen:

```python
# Das Passwort, mit dem sich jede Testfixture anmeldet. Ein fester Wert und
# kein zufaelliger: er taucht in Fehlermeldungen fehlschlagender Tests auf,
# und dort ist "test-passwort" hilfreicher als eine Zufallsfolge.
TEST_PASSWORD = "test-passwort"


async def authenticate(store: Store, client: httpx.AsyncClient) -> None:
    """Setzt ein Passwort und meldet `client` an.

    Gebraucht seit der Waechter ohne Nachweis nichts mehr durchlaesst (Spec 4):
    eine Testfixture, die `/api` aufruft, muss angemeldet sein wie ein
    Browser. `httpx.AsyncClient` fuehrt einen eigenen Cookie-Speicher, ein
    einziger Aufruf hier genuegt also fuer alle folgenden Anfragen desselben
    Clients."""
    store.auth.set_password_hash(hash_password(TEST_PASSWORD))
    response = await client.post("/auth/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 200, "Anmeldung in der Testfixture fehlgeschlagen"
```

mit den Importen `import httpx2 as httpx`, `from loxmatter.auth.passwords import hash_password`, `from loxmatter.model.store import Store` am Kopf, falls dort noch nicht vorhanden.

Dann in **jeder** Fixture, die einen `httpx.AsyncClient` über `build_app` baut, direkt nach dem `async with` und vor dem `yield` eine Zeile ergänzen. Beispiel `tests/api/conftest.py::api_with_runtime`:

```python
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield WebSocketClient(client, app), runtime, device_id
```

Dieselbe Zeile in: `tests/api/test_devices.py:19`, `tests/api/test_web.py:57`, `tests/api/test_export_api.py:35,205,251`, `tests/api/test_diagnostics.py:82,105,129,151,227,247,272`, `tests/api/test_live_smoke.py:170`. In `tests/api/test_security.py` **nicht** — diese Datei prüft gerade den unangemeldeten Zustand und meldet sich nur dort an, wo ein Test das ausdrücklich tut.

`tests/api/test_live_smoke.py:193` baut die App bereits mit `api_token="secret"` und schickt das Token mit — dieser Test bleibt unverändert.

`tests/loxone/test_server.py` ruft nur `/cmd`, `/resync` und `/` auf; die vier `build_app`-Aufrufe dort brauchen keine Anmeldung. Sollte der Lauf dort trotzdem etwas melden, gilt dieselbe Zeile.

- [ ] **Step 6: Update the startup warning**

In `src/loxmatter/cli.py` `_warn_if_missing_api_token` durch `_warn_if_no_password` ersetzen:

```python
def _warn_if_no_password(store_path: Path) -> None:
    """Warnt beim Start deutlich, solange kein Passwort vergeben ist.

    Die Warnung gilt seit dem WebUI-Login dem Passwort und NICHT mehr dem
    Token: ein konfiguriertes Token bringt sie nicht zum Schweigen, denn es
    ist der Weg fuer Skripte und kein Ersatz fuer die Ersteinrichtung.

    Der Zustand, vor dem sie warnt, ist ein anderer als frueher. Bis hierher
    lief ein Dienst ohne Token vollstaendig offen. Jetzt liefert er ohne
    Passwort gar nichts mehr aus - dafuer kann bis zur Passwortvergabe jeder,
    der ihn erreicht, ihn uebernehmen, indem er die Ersteinrichtung
    abschliesst (Spec 5, bewusst so entschieden). Genau darauf zielt dieser
    Text.

    Eigene Funktion statt einer Zeile inline in `run`/`_run`, damit ein Test
    sie ohne laufenden Server aufrufen kann - siehe
    `tests/api/test_security.py`."""
    store = Store(store_path)
    try:
        if store.auth.password_hash() is not None:
            return
    finally:
        store.close()
    logger.warning(
        "Für diese Brücke ist noch kein Passwort vergeben. Bis das geschehen ist, "
        "liefert keine /api-Route Daten aus — und jeder, der den Port erreicht, kann "
        "die Ersteinrichtung abschließen und die Brücke damit übernehmen. Öffne die "
        "Oberfläche jetzt und vergib ein Passwort."
    )
```

In `run` den Aufruf ersetzen — er steht dort heute vor `asyncio.run(_run(...))` und bekommt jetzt den aufgelösten Pfad statt des Tokens:

```python
    resolved_store_path = _resolve_store_path(store_path)
    _warn_if_no_password(resolved_store_path)
```

(Die Zeile `resolved_store_path = _resolve_store_path(store_path)` steht bereits in `run` — die Warnung wird dahinter geschoben, statt eine zweite Auflösung einzufügen.)

Die Tests `test_warn_if_missing_api_token_*` in `tests/api/test_security.py` entsprechend umschreiben: warnt ohne Passwort, schweigt mit gesetztem, warnt auch bei konfiguriertem Token.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 8: Lint, typecheck, commit**

```bash
uv run ruff check src tests && uv run mypy
git add src tests
git commit -m "feat(server): ohne gesetztes Passwort liefert keine /api-Route mehr aus"
```

---

### Task 9: Der 403-Zweig der Fabric-Sicherung entfällt

**Files:**
- Modify: `src/loxmatter/api/diagnostics.py` (`build_diagnostics_router`, `fabric_backup`), `src/loxmatter/loxone/server.py` (Aufruf)
- Test: `tests/api/test_security.py`, `tests/api/test_diagnostics.py`

**Interfaces:**
- Consumes: der geschlossene Wächter aus Task 8.
- Produces: `build_diagnostics_router(store, command_log, client, sender, matter_data_dir)` — **der Parameter `api_token_configured` entfällt ersatzlos.**

- [ ] **Step 1: Write the failing test**

An `tests/api/test_security.py` anhängen:

```python
async def test_fabric_backup_is_served_after_a_login_without_any_token(open_client):
    """Nach dem Login ist auch die Fabric-Sicherung frei (Spec 11): ein Login
    ist der staerkere Ausweis, und ein zweites Geheimnis danach schuetzte
    nichts, das nicht schon geschuetzt waere."""
    client, _app, _device_id, store = open_client
    store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
    await client.post("/auth/login", json={"password": "ein-gutes-passwort"})
    response = await client.get("/api/diagnostics/fabric-backup")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_security.py::test_fabric_backup_is_served_after_a_login_without_any_token -v`
Expected: FAIL — 403, weil `api_token_configured` bei dieser Fixture `False` ist.

- [ ] **Step 3: Remove the parameter and the branch**

In `src/loxmatter/api/diagnostics.py`:
1. Den Parameter `api_token_configured: bool = False` aus `build_diagnostics_router` streichen.
2. In `fabric_backup` den gesamten `if not api_token_configured:`-Block samt der 403-`HTTPException` streichen; der Kommentarblock darüber („Diese Pruefung steht VOR jeder anderen …") gehört mit ihm weg.
3. Den Docstring von `fabric_backup` neu fassen — er beschreibt heute zwei Bedingungen, von denen eine wegfällt:

```python
    @router.get("/fabric-backup")
    async def fabric_backup() -> Response:
        """**WER DIESE ROUTE ABRUFEN KANN, KANN DIE FABRIC UEBERNEHMEN.** Das
        ist der erste Satz dieses Docstrings mit Absicht.

        Der Schutz sitzt nicht an dieser Funktion, sondern einheitlich am
        gesamten Router (`loxone.server.build_api_guard`): ohne gueltiges
        Sitzungs-Cookie und ohne gueltiges Bearer-Token endet der Aufruf mit
        401, bevor diese Funktion ueberhaupt laeuft.

        **Der frueher hier stehende 403-Zweig ist entfallen** (WebUI-Login,
        Spec 11). Er verteidigte den Fall "der Dienst laeuft ohne jedes
        Zugangsmittel, also sind alle `/api`-Routen offen" - genau diesen
        Fall gibt es nicht mehr: ohne gesetztes Passwort laesst der Waechter
        keine `/api`-Route zu, und wer hier ankommt, hat einen Nachweis
        vorgezeigt. Ein unerreichbarer Zweig, dessen Docstring eine Lage
        beschreibt, die es nicht mehr gibt, waere schlimmer als kein Zweig:
        der naechste Leser verliesse sich auf eine Bedingung, die nichts
        mehr prueft. Dass der Waechter tatsaechlich an JEDEM der fuenf Router
        haengt, prueft `tests/api/test_security.py` Router fuer Router
        einzeln, statt sich auf den gemeinsamen Praefix zu verlassen.

        503 bleibt fuer "das Datenverzeichnis ist nicht eingehaengt bzw.
        existiert nicht" (unten) - eine Konfigurationsluecke, die diese
        Faehigkeit ueberhaupt erst herstellen wuerde.

        Sicherung des matter-server-Datenverzeichnisses (Spec 4.1, 8) als
        Download.

        Loggt bewusst NICHTS - weder den aufgeloesten Pfad noch die
        enthaltenen Dateinamen (siehe Moduldocstring)."""
```

4. Den Abschnitt des Moduldocstrings von `diagnostics.py`, der die 403-Regel erklärt (um Zeile 58–68), auf denselben Stand bringen.

In `src/loxmatter/loxone/server.py` das Argument aus dem Aufruf entfernen, samt seines Kommentarblocks:

```python
    app.include_router(
        build_diagnostics_router(
            store,
            command_log,
            client,
            sender,
            matter_data_dir,
        ),
        dependencies=api_guard,
    )
```

- [ ] **Step 4: Update the tests that asserted the 403**

In `tests/api/test_security.py` die `test_fabric_backup_without_a_token_*`-Tests entfernen — sie prüfen einen Zustand, den es nicht mehr gibt; der neue Test aus Schritt 1 und `test_without_a_password_every_api_route_is_closed` aus Task 8 decken die Route ab. In `tests/api/test_diagnostics.py` alle `build_diagnostics_router(..., api_token_configured=...)`- bzw. `build_app(..., api_token=...)`-Aufrufe, die auf den 403 zielen, auf den neuen Stand bringen (der Lauf in Schritt 5 zeigt sie).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uv run ruff check src tests && uv run mypy
git add src tests
git commit -m "refactor(diagnostics): 403-Zweig der Fabric-Sicherung entfaellt mit seinem Anlass"
```

---

### Task 10: `loxmatter set-password` als Notausgang

**Files:**
- Modify: `src/loxmatter/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `hash_password` (Task 2), `Store.auth` (Task 1), `_resolve_store_path` (bestehend).
- Produces: CLI-Befehl `loxmatter set-password [--store-path PATH]`.

- [ ] **Step 1: Write the failing test**

An `tests/test_cli.py` anhängen (dem dort vorhandenen `CliRunner`-Muster folgen). Am Kopf der Datei ergänzen, falls noch nicht vorhanden: `from loxmatter.auth.passwords import hash_password, verify_password` und `from loxmatter.model.store import Store`.

```python
def test_set_password_writes_a_hash_and_clears_sessions(tmp_path):
    """Der Notausgang aus Spec 9: ein headless aufgesetzter Dienst mit
    vergessenem Passwort waere sonst endgueltig verloren."""
    path = tmp_path / "t.sqlite"
    store = Store(path)
    store.auth.set_password_hash(hash_password("altes-passwort"))
    store.auth.create_session("alte-sitzung", created_at=1, expires_at=2**31)
    store.close()

    result = runner.invoke(
        app, ["set-password", "--store-path", str(path)], input="neues-passwort\nneues-passwort\n"
    )
    assert result.exit_code == 0

    store = Store(path)
    try:
        stored = store.auth.password_hash()
        assert stored is not None
        assert verify_password("neues-passwort", stored) is True
        # Wer das Passwort zuruecksetzt, will nicht, dass eine alte Sitzung
        # weiterlaeuft.
        assert store.auth.session_expires_at("alte-sitzung") is None
    finally:
        store.close()
    # Das Passwort selbst darf in keiner Ausgabe stehen.
    assert "neues-passwort" not in result.output


def test_set_password_rejects_a_short_password(tmp_path):
    path = tmp_path / "t.sqlite"
    Store(path).close()
    result = runner.invoke(
        app, ["set-password", "--store-path", str(path)], input="kurz\nkurz\n"
    )
    assert result.exit_code != 0
    store = Store(path)
    try:
        assert store.auth.password_hash() is None
    finally:
        store.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k set_password -v`
Expected: FAIL — `No such command 'set-password'`

- [ ] **Step 3: Add the command**

In `src/loxmatter/cli.py`, nach dem `run`-Befehl:

```python
@app.command()
def set_password(
    store_path: Path | None = typer.Option(  # noqa: B008
        None, help="Datenbank mit den Signalschlüsseln. Siehe --store-path bei `export`."
    ),
) -> None:
    """Setzt das Passwort der Oberfläche neu — der Notausgang für den Fall,
    dass es vergessen wurde.

    Ohne diesen Befehl wäre eine headless aufgesetzte Installation mit
    vergessenem Passwort endgültig verloren: die Ersteinrichtung ist nach
    der ersten Passwortvergabe dauerhaft geschlossen (409), und einen
    zweiten Weg hinein gibt es nicht. Wer diesen Befehl ausführen kann, hat
    Zugriff auf die Datenbankdatei selbst — der Befehl macht daraus nur
    einen benutzbaren Weg statt eines Bastelns am SQLite.

    Meldet dabei alle offenen Sitzungen ab: wer das Passwort zurücksetzt,
    will nicht, dass eine alte Sitzung weiterläuft.
    """
    password = typer.prompt("Neues Passwort", hide_input=True, confirmation_prompt=True)
    if len(password) < MIN_PASSWORD_LENGTH:
        _fail(f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben.")
    store = Store(_resolve_store_path(store_path))
    try:
        store.auth.set_password_hash(hash_password(password))
        store.auth.delete_all_sessions()
    finally:
        store.close()
    # Bewusst ohne das Passwort in der Ausgabe - auch nicht verkuerzt.
    typer.echo("Passwort gesetzt. Alle offenen Sitzungen wurden abgemeldet.")
```

Die Importe am Kopf ergänzen:

```python
from loxmatter.auth.passwords import MIN_PASSWORD_LENGTH, hash_password
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k set_password -v`
Expected: PASS (2 Tests)

- [ ] **Step 5: Run the full suite, lint, commit**

```bash
uv run pytest && uv run ruff check src tests && uv run mypy
git add src/loxmatter/cli.py tests/test_cli.py
git commit -m "feat(cli): set-password als Notausgang fuer ein vergessenes Passwort"
```

---

### Task 11: Dokumentation

**Files:**
- Modify: `README.md`, `deploy/testhost/.env.example`, `deploy/testhost/docker-compose.yml`, `src/loxmatter/loxone/server.py` (Moduldocstring), `src/loxmatter/web/index.html` (HTML-Kommentar am Kopf), `src/loxmatter/web/app.js` (Moduldocstring)
- Create: `docs/superpowers/plans/2026-09-03-webui-login-release-hinweis.md`

**Interfaces:** keine — reine Prosa.

Spec 13 zählt auf, was nachzuziehen ist. Der Kern: **für Installationen ohne Token ist dieses Update ein Bruch** — der Dienst liefert nichts mehr aus, bis ein Passwort steht. Das darf niemand erst am schweigenden Dienst bemerken.

- [ ] **Step 1: Write the release note**

`docs/superpowers/plans/2026-09-03-webui-login-release-hinweis.md`:

```markdown
# Release-Hinweis: Login statt Token-Eingabe

**Was sich ändert.** Die Oberfläche hat jetzt eine Anmeldung mit Passwort.
Das Feld für das API-Token ist verschwunden.

**Was zu tun ist — sofort nach dem Ausrollen.** Öffne die Oberfläche
(`http://<Host>:8080/`) und vergib ein Passwort. Bis das geschehen ist,
liefert keine `/api`-Route Daten aus, und die Oberfläche zeigt nichts als
den Einrichtungsbildschirm.

**Warum sofort.** Die Ersteinrichtung verlangt keinen weiteren Nachweis —
wer zuerst kommt, vergibt das Passwort. Zwischen dem Update und deiner
Anmeldung kann also jeder, der die Brücke im Netz erreicht, sie übernehmen.
Bewusst so entschieden, damit die Einrichtung ohne Shell auf dem Host
möglich ist; der Preis ist dieses Fenster, und es sollte Minuten dauern und
nicht Tage.

**Was gleich bleibt.** `LOXMATTER_API_TOKEN` gilt weiter — als Weg für
Skripte und `curl`, nicht mehr für den Browser. Bestehende
Automatisierungen brechen durch dieses Update nicht ab, auch nicht vor der
Passwortvergabe. `/cmd` und `/resync` für den Miniserver bleiben wie immer
ohne jede Absicherung erreichbar.

**Passwort vergessen.** `uv run loxmatter set-password` auf dem Host setzt
es neu und meldet alle offenen Sitzungen ab.

**Ein Hinweis zum Passwort.** Der Dienst spricht HTTP ohne Verschlüsselung;
das Passwort geht beim Anmelden im Klartext über das Netz. Nimm eines, das
du nirgendwo sonst benutzt.
```

- [ ] **Step 2: Update `README.md`**

Den Abschnitt zur Absicherung ersetzen: Login statt Token-Eingabe, Passwortvergabe beim ersten Aufruf, `loxmatter set-password` als Notausgang, der Klartext-Hinweis aus Spec 14.1, und dass das Token nur noch für Skripte da ist.

- [ ] **Step 3: Update `deploy/testhost/.env.example`**

Der Kommentarblock an `LOXMATTER_API_TOKEN` leitet heute zur Eingabe in der Oberfläche an („Danach in der Browser-Oberfläche oben rechts unter ‚Token' eintragen") — dieser Satz ist falsch geworden. Neu: Das Token ist optional und dient Skripten; der Zugang zur Oberfläche läuft über das beim ersten Aufruf vergebene Passwort. Der Hinweis zum Zeichensatz (`openssl rand -hex 32`, keine Leerzeichen) bleibt, er gilt weiterhin für den Header.

- [ ] **Step 4: Update `deploy/testhost/docker-compose.yml`**

Zwei Kommentarblöcke: der an `LOXMATTER_API_TOKEN` (dieselbe Korrektur wie in Schritt 3) und der an der Volume-Zeile `./data:/matter-data:ro`. Letzterer sagt heute, Einhängung und Token gehörten zusammen und ohne Token sei die Einhängung wirkungslos. Das trägt jetzt das Passwort: die Einhängung ist vertretbar, weil ohne Nachweis keine `/api`-Route mehr antwortet.

- [ ] **Step 5: Update the module docstrings**

- `src/loxmatter/loxone/server.py`, Kopf: der Abschnitt über `api_token` als einzigen Ausweis beschreibt jetzt zwei Nachweise und den Wegfall des offenen Zustands.
- `src/loxmatter/api/diagnostics.py`, Kopf: bereits in Task 9 Schritt 3 erledigt — hier nur gegenlesen.
- `src/loxmatter/web/app.js`, Kopf, und `src/loxmatter/web/index.html`, HTML-Kommentar am Kopf: beide beschreiben eine Oberfläche mit Token-Feld und ohne Login.

- [ ] **Step 6: Check that no stale reference survives**

Run:

```bash
grep -rn "Token eingeben\|token-box\|localStorage\|api_token_configured\|_warn_if_missing_api_token" src README.md deploy
```

Expected: keine Treffer. Jeder Treffer ist eine Stelle, die dieser Task übersehen hat.

- [ ] **Step 7: Run the full suite and commit**

```bash
uv run pytest && uv run ruff check src tests && uv run mypy
git add README.md deploy src docs
git commit -m "docs: Login statt Token-Eingabe in README, Deployment und Docstrings"
```

---

## Abschluss

Nach Task 11 ist die Spec vollständig umgesetzt. Zur Abnahme:

```bash
uv run pytest && uv run ruff check src tests && uv run mypy
```

Danach der Durchlauf von Hand aus Task 7 Schritt 7 auf einer frischen Datenbank — er ist der einzige Teil, den keine Testdatei abdeckt, weil er den echten Browser braucht: Einrichtung, Neuladen, Abmelden, Fehlversuche, Anmelden.
