# WebUI Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The WebUI gets a password login with first-time setup through the interface; token entry in the browser goes away, and without a password set, no `/api` route delivers data anymore.

**Architecture:** The password hash and the sessions live in two new tables of the same SQLite store (schema v4). A new package `loxmatter.auth` encapsulates hashing, sessions, and login throttling as pure logic with no FastAPI reference; `api/auth.py` is the router on top of it; `build_api_guard` will accept a session cookie OR a bearer token and let nothing through without either. The interface switches between setup, login, and the app based on `GET /auth-info`.

**Tech Stack:** Python 3.12, FastAPI, SQLite via `sqlite3`, `hashlib.scrypt` and `secrets` from the standard library (no new dependency), Alpine.js in the browser, pytest with `asyncio_mode = "auto"`, `httpx2` as the test client.

**Spec:** `docs/superpowers/specs/2026-09-03-webui-login-design.md` — whenever in doubt, the spec governs, not this plan.

## Global Constraints

- **Language:** Prose, docstrings, comments, and error messages in German. Identifiers in code and keys in JSON responses in English (review fix M9, 2026-09-02).
- **No new runtime dependency.** `pyproject.toml` stays unchanged in the `[project].dependencies` section.
- **Line length 100** (`[tool.ruff]`), **mypy strict** across `src` and `scripts`.
- **Comment density:** This repository justifies in code *why* something is the way it is, not *what* it does. New modules and every non-obvious decision get a docstring in this style. A comment that only repeats the code is not one.
- **Secrets never belong in the log and never in a response:** no password, no hash, no session id, no token — in no branch, not even an error branch.
- **Test run:** `uv run pytest`. Linting: `uv run ruff check src tests`. Types: `uv run mypy`.
- **Commit format:** `<type>(<scope>): <description>` in German, as in the existing history (`feat(store):`, `fix(profiles):`, `test(profiles):`).
- **Cookie name:** `loxmatter_session`. **Session duration:** 30 days. **Minimum password length:** 8 characters. **Throttling:** from 5 failed attempts per peer address, then a 30-second lock.

---

## File Structure

**New:**

| File | Responsibility |
| --- | --- |
| `src/loxmatter/model/auth_store.py` | Data access for `setting` and `session`. Knows SQL, knows no cryptography and no HTTP. |
| `src/loxmatter/auth/__init__.py` | Empty package module with a docstring that places the three modules underneath. |
| `src/loxmatter/auth/passwords.py` | `hash_password` / `verify_password` via `hashlib.scrypt`. Knows neither store nor HTTP. |
| `src/loxmatter/auth/sessions.py` | Creates and checks sessions, cookie name, and duration. Knows the `AuthStore`, no HTTP. |
| `src/loxmatter/auth/throttle.py` | `LoginThrottle` — failed attempts per caller, purely in memory. |
| `src/loxmatter/api/auth.py` | Router `/auth-info`, `/auth/setup`, `/auth/login`, `/auth/logout`. |
| `tests/model/test_auth_store.py`, `tests/auth/test_passwords.py`, `tests/auth/test_sessions.py`, `tests/auth/test_throttle.py`, `tests/api/test_auth.py` | Tests for these. |

The spec names a single file `tests/api/test_auth.py` in section 12. The plan splits the unit tests of the three `auth` modules into `tests/auth/` and leaves only the routes in `tests/api/test_auth.py` — the test files thereby follow the modules, as elsewhere in the repository too (`tests/model/`, `tests/loxone/`).

**Changed:** `src/loxmatter/model/store.py` (schema v4, `Store.auth`), `src/loxmatter/loxone/server.py` (guard, router), `src/loxmatter/api/diagnostics.py` (403 branch goes away), `src/loxmatter/cli.py` (warning, new command), `src/loxmatter/web/{index.html,app.js,style.css}`, `tests/api/conftest.py` and the test files with `build_app(...)` calls, `README.md`, `deploy/testhost/.env.example`, `deploy/testhost/docker-compose.yml`.

**Ordering logic:** Tasks 1–7 are purely additive — after each one, the suite runs green and the interface keeps working unchanged. Only task 8 switches off the previously open state and therefore pulls all test fixtures along with it. The interface (task 7) deliberately comes **before** task 8, so that there is no intermediate state in which the browser is locked out.

---

### Task 1: Schema v4 and `AuthStore`

**Files:**
- Create: `src/loxmatter/model/auth_store.py`
- Modify: `src/loxmatter/model/store.py` (`_SCHEMA`, `_SCHEMA_VERSION`, `_MIGRATIONS`, `Store.__init__`)
- Test: `tests/model/test_auth_store.py`, `tests/model/test_store_migration.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `AuthStore` with `password_hash() -> str | None`, `set_password_hash_if_unset(value: str) -> bool`, `set_password_hash(value: str) -> None`, `create_session(session_id: str, *, created_at: int, expires_at: int) -> None`, `session_expires_at(session_id: str) -> int | None`, `extend_session(session_id: str, *, expires_at: int) -> None`, `delete_session(session_id: str) -> None`, `delete_all_sessions() -> None`, `purge_expired_sessions(now: int) -> None`. Reachable as `Store.auth`.

- [ ] **Step 1: Write the failing test**

`tests/model/test_auth_store.py`:

```python
"""Tests for `AuthStore` - the part of the store that manages access.

The core question: does `setting` hold exactly one password hash, and can
`session` be managed so that an expired session no longer counts and a
deleted one is gone immediately?
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
"""The part of the store that manages access - password hash and
sessions - instead of devices, signals, and commands.

Its own module and its own class, not further methods on `Store`: that
already holds over nine hundred lines for the device model, and access has
nothing to do with that domain. The connection still belongs to `Store`
regardless - this class is a view onto it, not a second connection setup
onto the same file (that would be a second locking domain for the same
data).

What does NOT happen here: cryptography and HTTP. This class stores a
hash and reads it back without knowing how it comes about (see
`loxmatter.auth.passwords`), and it knows neither cookies nor status codes
(see `loxmatter.auth.sessions` and `loxmatter.api.auth`). Whoever mixes
this together ends up with three places where a secret can surface,
instead of one.

The schema of the two tables lives in `store.py` under `_SCHEMA` and
`_migrate_to_v4` - schema definitions stay in one place, even though
access to them lives here.
"""

from __future__ import annotations

import sqlite3

# The only key that `setting` carries so far. The table is nonetheless laid
# out generically (key/value) because the rest of the configuration is
# meant to go the same way (spec 14.2) - a table `password` with one
# column would need rebuilding again at that point.
_PASSWORD_KEY = "password_hash"


class AuthStore:
    """Access to `setting` and `session` via the store's connection."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def password_hash(self) -> str | None:
        """The stored hash - `None` as long as no password has been set.

        `None` is the state the whole access system hinges on: it
        means "first-time setup still open" and, per
        `loxone.server.build_api_guard`, admits not a single `/api` route."""
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_PASSWORD_KEY,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def set_password_hash_if_unset(self, value: str) -> bool:
        """Sets the hash, but only if none exists yet - `True` if
        this call is the one that set it.

        `INSERT OR IGNORE` and not "check first, then write": SQLite
        decides this in a single statement, so two simultaneous
        setup attempts cannot overwrite each other. This is exactly what
        `POST /auth/setup` relies on to keep answering with 409
        permanently after the first success."""
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO setting (key, value) VALUES (?, ?)",
            (_PASSWORD_KEY, value),
        )
        self._db.commit()
        return cursor.rowcount == 1

    def set_password_hash(self, value: str) -> None:
        """Sets the hash and overwrites an existing one.

        The path for `loxmatter set-password` on the host (spec 9), NOT
        for the interface - that exclusively uses
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
        """Expiry time as Unix seconds - `None` if the session no longer
        exists (or never did). Whether it is still valid is decided by
        `loxmatter.auth.sessions`, not this class."""
        row = self._db.execute(
            "SELECT expires_at FROM session WHERE id = ?", (session_id,)
        ).fetchone()
        return None if row is None else int(row["expires_at"])

    def extend_session(self, session_id: str, *, expires_at: int) -> None:
        self._db.execute("UPDATE session SET expires_at = ? WHERE id = ?", (expires_at, session_id))
        self._db.commit()

    def delete_session(self, session_id: str) -> None:
        self._db.execute("DELETE FROM session WHERE id = ?", (session_id,))
        self._db.commit()

    def delete_all_sessions(self) -> None:
        """Logs everyone out. Called by `loxmatter set-password`: whoever
        resets the password does not want an old session to keep
        running."""
        self._db.execute("DELETE FROM session")
        self._db.commit()

    def purge_expired_sessions(self, now: int) -> None:
        """Clears away expired rows. Called when creating a new
        session - no background job for a table that normally
        holds a handful of rows."""
        self._db.execute("DELETE FROM session WHERE expires_at <= ?", (now,))
        self._db.commit()
```

- [ ] **Step 4: Add the tables to the schema and the migration**

In `src/loxmatter/model/store.py`, raise `_SCHEMA_VERSION` from `3` to `4` and extend the comment block above it by one sentence:

```python
# ... Version 3 (task 7, phase 6) does not add a column -
# it re-derives `signal.title`, `signal.unit`, and the default value of
# `signal.exported` for EXISTING rows from the profile table, see
# `_migrate_to_v3`. Version 4 (WebUI login) adds the tables `setting` and
# `session`, see `_migrate_to_v4` - both already exist for a fresh
# database via `_SCHEMA`, so the migration is only needed for
# existing databases.
_SCHEMA_VERSION = 4
```

Append to `_SCHEMA` (after the `command` block, within the same string):

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

Insert directly before `_MIGRATIONS`:

```python
def _migrate_to_v4(db: sqlite3.Connection) -> None:
    """Creates `setting` and `session` (WebUI login).

    `CREATE TABLE IF NOT EXISTS` and not `CREATE TABLE`: a freshly
    created database already has both tables via `_SCHEMA`, but it is
    likewise at `PRAGMA user_version = 0` and therefore runs through
    the same migration chain (see `_migrate` and
    `_add_column_if_missing` for the same trap with columns).

    No backfill: an existing database has no password and no
    session, and that is exactly the right state - after the
    update it goes through first-time setup (spec 5)."""
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

Extend `_MIGRATIONS` with the entry:

```python
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
    3: _migrate_to_v3,
    4: _migrate_to_v4,
}
```

- [ ] **Step 5: Hang `AuthStore` on `Store`**

In `src/loxmatter/model/store.py`, add the import and extend `Store.__init__`:

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
        # A view onto the same connection, no second connection setup -
        # see the module docstring of `auth_store.py`.
        self.auth = AuthStore(self._db)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/model/test_auth_store.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Add the migration test**

Append to `tests/model/test_store_migration.py` — reuse the existing test setup of this file (it already shows how to create a database with an older `user_version`; follow that pattern):

```python
def test_migration_to_v4_adds_the_auth_tables_without_touching_devices(tmp_path):
    """An existing database at version 3 gets `setting` and `session`,
    and its device rows stay untouched."""
    path = tmp_path / "alt.sqlite"
    store = Store(path)
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    signals_before = len(store.signals(device_id))
    store.close()

    # Reset to version 3 and remove both tables - this is what a
    # database created before this change looks like.
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
Expected: PASS — everything green, this change is purely additive.

- [ ] **Step 9: Lint and typecheck**

Run: `uv run ruff check src tests && uv run mypy`
Expected: no messages.

- [ ] **Step 10: Commit**

```bash
git add src/loxmatter/model/auth_store.py src/loxmatter/model/store.py tests/model/test_auth_store.py tests/model/test_store_migration.py
git commit -m "feat(store): Tabellen fuer Passwort und Sitzungen (Schema v4)"
```

---

### Task 2: Password hash with scrypt

**Files:**
- Create: `src/loxmatter/auth/__init__.py`, `src/loxmatter/auth/passwords.py`
- Test: `tests/auth/test_passwords.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `hash_password(password: str) -> str`, `verify_password(password: str, stored: str) -> bool`, `MIN_PASSWORD_LENGTH: int = 8`.

- [ ] **Step 1: Write the failing test**

`tests/auth/test_passwords.py`:

```python
"""Tests for password hashing (Spec 6).

The core question: does the right password match, does every other one
fail, and does `verify_password` survive a broken or foreign hash without
raising? The last point is not an edge case: the value comes from a file
that an operator may have edited by hand.
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
    """Otherwise the salt wouldn't be one - two installations with the same
    password would have the same hash."""
    assert hash_password("gleiches-passwort") != hash_password("gleiches-passwort")


def test_the_stored_form_names_its_scheme_and_parameters():
    stored = hash_password("egal-hauptsache-lang")
    scheme, n, r, p, salt, key = stored.split("$")
    assert scheme == "scrypt"
    assert (int(n), int(r), int(p)) == (2**14, 8, 1)
    assert len(bytes.fromhex(salt)) == 16
    assert len(bytes.fromhex(key)) == 32


def test_a_hash_with_other_parameters_still_verifies():
    """The reason the parameters live in the value: a later change to the
    cost factors must not invalidate old hashes.

    The comparison value here is computed with DIFFERENT cost factors
    (n = 1024), not the module's own - otherwise the test would only check
    that a constant matches itself."""
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
"""Access to the interface: password, session, throttling.

Three modules, deliberately separated and deliberately without FastAPI
reference:

- `passwords` computes hashes and checks them. Knows neither database nor HTTP.
- `sessions` creates sessions and checks them. Knows the `AuthStore`, no HTTP.
- `throttle` counts failed attempts. Knows nothing at all except the clock.

The HTTP part lives in `loxmatter.api.auth`, the guard in
`loxmatter.loxone.server`. This separation is why the logic here is
checkable without an ASGI test client - and why a secret can surface only
at the places that genuinely need it.
"""
```

- [ ] **Step 4: Write `passwords.py`**

`src/loxmatter/auth/passwords.py`:

```python
"""Password hashing with `hashlib.scrypt` (spec 6).

**Why scrypt and not Argon2 or bcrypt:** both would need a new
runtime dependency (`argon2-cffi` or `passlib` respectively) for exactly one
hash in this project. scrypt is memory-hard, is in the standard library, and
is sufficient for this purpose. The dependency list in `pyproject.toml`
therefore stays unchanged.

**Why the parameters live in the stored value** (`scrypt$n$r$p$salt$hash`):
if the cost factors are raised later, already-stored hashes must remain
checkable - otherwise an update would lock the operator out of their
own bridge. `verify_password` therefore reads the parameters from the
value and not from this module's constants.

The memory requirement of scrypt is 128 * n * r, here 16 MiB. That is
below the limit that `hashlib.scrypt` allows without a set `maxmem`
(32 MiB) - which is why no `maxmem` argument appears there.
"""

from __future__ import annotations

import hashlib
import secrets

# Not a value from a security vacuum, but the usual interactive
# working point for scrypt: around 16 MiB of memory and a fraction of a
# second per check. Set higher, every login on a
# Raspberry Pi would become noticeably sluggish.
_N = 2**14
_R = 8
_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32

_SCHEME = "scrypt"

# A shorter password would no longer be saved by a throttle of 30 seconds
# per five attempts (see `throttle`). Requiring it to be longer tends,
# in practice, to end up on a sticky note on the screen.
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    """Computes the value to store - with a fresh salt on every call."""
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_KEY_BYTES)
    return f"{_SCHEME}${_N}${_R}${_P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Checks `password` against a stored value.

    Simply returns `False` for any unreadable, foreign, or malformed
    `stored`, rather than raising: the value comes from a file on the
    operator's disk, and a typo in it should produce a 401,
    not a 500 with a traceback in the log."""
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
        # Unreadable hex characters, nonsensical parameters (n not a power
        # of two, dklen 0) - all the same case: this value is not a hash.
        return False
    return secrets.compare_digest(key, expected)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/auth/test_passwords.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uv run ruff check src tests && uv run mypy
git add src/loxmatter/auth tests/auth/test_passwords.py
git commit -m "feat(auth): Passwort-Hashing mit scrypt aus der Standardbibliothek"
```

---

### Task 3: Sessions

**Files:**
- Create: `src/loxmatter/auth/sessions.py`
- Test: `tests/auth/test_sessions.py`

**Interfaces:**
- Consumes: `AuthStore` from task 1.
- Produces: `SESSION_COOKIE: str = "loxmatter_session"`, `SESSION_LIFETIME_SECONDS: int`, `open_session(auth: AuthStore, *, now: int | None = None) -> str`, `session_is_valid(auth: AuthStore, session_id: str, *, now: int | None = None) -> bool`.

- [ ] **Step 1: Write the failing test**

`tests/auth/test_sessions.py`:

```python
"""Tests for session management (spec 7).

`now` is a parameter in both functions so that these tests can let time
pass without sleeping - a session with a 30-day lifetime could otherwise
not be tested at all.
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
    """Otherwise expired rows would just sit there until someone happens to
    create a new session."""
    store = _store(tmp_path)
    try:
        session_id = open_session(store.auth, now=1000)
        later = 1000 + SESSION_LIFETIME_SECONDS + 1
        session_is_valid(store.auth, session_id, now=later)
        assert store.auth.session_expires_at(session_id) is None
    finally:
        store.close()


def test_a_session_is_extended_only_after_a_day(tmp_path):
    """Sliding extension without a write access on EVERY call: an
    interface with a live view sends many requests per minute, and every
    one of them being a SQLite write would be pure waste."""
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
"""Sessions: create, check, extend on a sliding basis (spec 7).

**Why in the database and not in memory:** the service runs with
`restart: unless-stopped` (see `deploy/testhost/docker-compose.yml`). A
restart - after an update, after a power outage, after a crash - would
otherwise log out every logged-in browser, and the operator would see a
login screen instead of their bridge, with no idea why.

**Why a server-side entry and not a signed cookie:** a signed cookie could
not be revoked. `POST /auth/logout` and `loxmatter set-password` are meant
to actually end a session, not just ask the browser to forget it.

`now` is an optional parameter (Unix seconds) in both functions.
Production code never passes it; the tests need it to let thirty days
pass without sleeping.
"""

from __future__ import annotations

import secrets
import time

from loxmatter.model.auth_store import AuthStore

# The cookie name. Lives here and not in `api/auth.py` because two places
# need it: the router sets it, the guard in `loxone/server.py` reads it.
# Two different spellings of the same name would be a bug that no test
# would notice, because both sides work fine on their own.
SESSION_COOKIE = "loxmatter_session"

SESSION_LIFETIME_SECONDS = 30 * 24 * 60 * 60

# Only extended once more than one day of the lifetime has been used up -
# see `session_is_valid`.
_EXTEND_AFTER_SECONDS = 24 * 60 * 60


def open_session(auth: AuthStore, *, now: int | None = None) -> str:
    """Creates a session and returns its identifier.

    32 bytes from `secrets.token_hex` - the same order of magnitude as the
    recommended API token (`openssl rand -hex 32`), because this
    identifier is worth exactly the same: whoever holds it is logged in."""
    moment = int(time.time()) if now is None else now
    auth.purge_expired_sessions(moment)
    session_id = secrets.token_hex(32)
    auth.create_session(session_id, created_at=moment, expires_at=moment + SESSION_LIFETIME_SECONDS)
    return session_id


def session_is_valid(auth: AuthStore, session_id: str, *, now: int | None = None) -> bool:
    """Is this session still valid? Extends it on a sliding basis while at it.

    The extension happens at most once per `_EXTEND_AFTER_SECONDS`, not on
    every call: this function runs on EVERY request to `/api`, and the
    interface issues several per second while in use. An `UPDATE` per
    request would be a SQLite write operation for nothing.

    An expired session is deleted right here - the cleanup path that would
    otherwise never run without a fresh login."""
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
Expected: PASS (7 tests)

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check src tests && uv run mypy
git add src/loxmatter/auth/sessions.py tests/auth/test_sessions.py
git commit -m "feat(auth): Sitzungen in der Datenbank, gleitend verlaengert"
```

---

### Task 4: Throttling against brute-forcing

**Files:**
- Create: `src/loxmatter/auth/throttle.py`
- Test: `tests/auth/test_throttle.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `LoginThrottle` with `retry_after(client: str, *, now: float | None = None) -> int`, `record_failure(client: str, *, now: float | None = None) -> None`, `record_success(client: str) -> None`; constants `FAILURES_BEFORE_THROTTLING = 5`, `THROTTLE_SECONDS = 30`.

- [ ] **Step 1: Write the failing test**

`tests/auth/test_throttle.py`:

```python
"""Tests for login throttling (Spec 8).

The core question: does it slow things down after enough failed
attempts, does it let the legitimate operator back through afterward,
and does it really only hit the address that got it wrong?
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
    """Otherwise the operator would lock themselves out after five typos,
    even though they have since entered the password correctly."""
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
"""Brake against brute-forcing passwords (spec 8).

The reason this module exists at all: a password is guessable, a token
from `openssl rand -hex 32` is not. Without a brake, login would therefore
be the weaker way into the same service - and this design would have made
the security worse while making it more convenient.

**In memory and not in the database:** this is transient state that does
not justify a write on every failed attempt. A restart clears it - but an
attacker cannot trigger one, and an operator who restarts to be able to
log back in faster does not consider their own password an attack anyway.

**`time.monotonic` and not `time.time`:** a clock change or an NTP jump
must neither extend nor lift a lockout.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

FAILURES_BEFORE_THROTTLING = 5
THROTTLE_SECONDS = 30


@dataclass
class LoginThrottle:
    """Counts failed attempts per caller. One instance per router, see
    `api.auth.build_auth_router`."""

    _failures: dict[str, int] = field(default_factory=dict)
    _blocked_until: dict[str, float] = field(default_factory=dict)

    def retry_after(self, client: str, *, now: float | None = None) -> int:
        """How many seconds this caller still has to wait - `0` if they may
        try again immediately.

        Rounded up so the message in the interface ("possible again in X
        seconds") never invites a retry too early."""
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
        """Resets counter and lockout - whoever knows the password is not
        an attacker, even if they mistyped it five times before."""
        self._failures.pop(client, None)
        self._blocked_until.pop(client, None)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/auth/test_throttle.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check src tests && uv run mypy
git add src/loxmatter/auth/throttle.py tests/auth/test_throttle.py
git commit -m "feat(auth): Drosselung nach fuenf Fehlversuchen je Adresse"
```

---

### Task 5: The auth router

**Files:**
- Create: `src/loxmatter/api/auth.py`
- Modify: `src/loxmatter/loxone/server.py` (import and `app.include_router`)
- Test: `tests/api/test_auth.py`

**Interfaces:**
- Consumes: `hash_password`, `verify_password`, `MIN_PASSWORD_LENGTH` (task 2); `SESSION_COOKIE`, `SESSION_LIFETIME_SECONDS`, `open_session`, `session_is_valid` (task 3); `LoginThrottle` (task 4); `Store.auth` (task 1).
- Produces: `build_auth_router(store: Store) -> APIRouter` with the four routes; the response models `AuthInfoOut` (`password_set: bool`, `authenticated: bool`) and `StatusOut` (`status: str`).

- [ ] **Step 1: Write the failing test**

`tests/api/test_auth.py`:

```python
"""Tests for the four access routes (Spec 8).

They are the only ones hanging under `/auth` outside the guard - they must
be reachable while signed out, or nobody could ever sign in.

`httpx.AsyncClient` carries its own cookie store: whatever `POST
/auth/login` sets, every further request from the same client sends along
on its own. That's exactly how the browser behaves too.
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
async def auth_client(
    tmp_path: Path, no_invoke: Any
) -> AsyncIterator[tuple[httpx.AsyncClient, Store]]:
    """An app with no password set - the state of initial setup."""
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
    """409, not 401: there is no password this call could ever succeed
    with - retrying with credentials doesn't help."""
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
    """Not just clearing the cookie: the same value must no longer be valid
    afterward, or a stolen identifier keeps living on."""
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
Expected: FAIL — 404 on all four routes (the router does not yet exist)

- [ ] **Step 3: Write the router**

`src/loxmatter/api/auth.py`:

```python
"""The four access routes: `/auth-info`, `/auth/setup`, `/auth/login`,
`/auth/logout` (Spec 8).

**These are the only routes that do NOT hang behind `build_api_guard`** -
they must be reachable while logged out, otherwise no one could log in.
`loxone.server.build_app` therefore deliberately wires them in without
`dependencies=api_guard`, alongside `/health`.

What they therefore do NOT deliver: anything about the state of the
bridge. `/auth-info` states exactly two booleans - whether a password is
set and whether THIS caller is logged in. A caller learns both anyway from
how `/api/devices` responds to it; it is stated here only so the UI does
not have to guess which screen to show.

**No secret leaves this module.** Neither password nor hash nor session id
appears in a response (the id travels exclusively in the `Set-Cookie`) or
in a log - in any branch.
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
    # One instance per app, not per request - otherwise it would count nothing.
    throttle = LoginThrottle()

    def _require_length(password: str) -> None:
        """Its own check instead of `Field(min_length=...)` on the model: the
        message ends up in the UI and should be in German there
        and say what to do - not as a pydantic error list."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=(f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben."),
            )

    def _start_session(response: Response) -> None:
        """Creates a session and attaches the cookie to the response.

        `secure` is DELIBERATELY missing here and must not be added "for
        security's sake": this service speaks HTTP (spec 14.1), a
        `Secure` cookie would be discarded by the browser and no one could
        get in anymore. `samesite="strict"` is at the same time the CSRF
        protection - a foreign site cannot use it to trigger a
        state-changing request within a logged-in session, which is why
        there is no separate CSRF token."""
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
            authenticated=(session_id is not None and session_is_valid(store.auth, session_id)),
        )

    @router.post("/auth/setup")
    async def setup(body: PasswordIn, response: Response) -> StatusOut:
        """Initial setup - with no further proof, as long as no password
        is set (spec 5, trust on first use).

        This is a deliberately made trade-off, not a forgotten
        check: between the start with no password and this assignment,
        anyone who reaches the service can take it over. Decided on
        September 3, 2026 against a setup code in the log, a time window, and
        an initial password via CLI, so that setup can remain headless via
        the interface - and expressly also for an
        existing system with an already-configured token, which is NOT
        additionally asked for here.

        `set_password_hash_if_unset` decides in a single
        SQL statement whether this call was the first - so two
        simultaneous setups cannot overwrite each other."""
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
        # The peer address of the connection, NOT `X-Forwarded-For`: every
        # caller sets that header itself, and the throttle could
        # be bypassed by claiming a different address per attempt.
        client = request.client.host if request.client is not None else "unknown"
        wait = throttle.retry_after(client)
        if wait:
            raise HTTPException(
                status_code=429,
                detail=f"Zu viele Fehlversuche – in {wait} Sekunden wieder möglich.",
            )
        stored = store.auth.password_hash()
        if stored is None:
            # 409 and not 401: there is no password this call could
            # ever succeed with - retrying with credentials does not
            # help (the same distinction as in RFC 9110).
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
        """Ends the session SERVER-SIDE and only then clears the cookie.

        The order is the point: a logout that only clears the cookie
        would leave an already-leaked identifier alive for thirty
        days."""
        session_id = request.cookies.get(SESSION_COOKIE)
        if session_id is not None:
            store.auth.delete_session(session_id)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return StatusOut(status="ok")

    return router
```

- [ ] **Step 4: Wire the router into the app**

In `src/loxmatter/loxone/server.py`, add the import:

```python
from loxmatter.api.auth import build_auth_router
```

and hang it into `build_app` directly before `app.mount("/static", ...)`:

```python
    # WITHOUT `dependencies=api_guard` - just like `/health`, `/cmd`, and
    # `/resync` further below. Anyone not yet logged in must be able to
    # reach these four routes, otherwise there is no way in
    # (see api/auth.py, module docstring).
    app.include_router(build_auth_router(store))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_auth.py -v`
Expected: PASS (10 tests)

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS — the new routes change nothing about the existing ones.

- [ ] **Step 7: Lint, typecheck, commit**

```bash
uv run ruff check src tests && uv run mypy
git add src/loxmatter/api/auth.py src/loxmatter/loxone/server.py tests/api/test_auth.py
git commit -m "feat(api): Routen fuer Ersteinrichtung, Login und Logout"
```

---

### Task 6: The guard accepts the session cookie

**Files:**
- Modify: `src/loxmatter/loxone/server.py` (`build_api_guard`, `build_app`)
- Test: `tests/api/test_security.py`

**Interfaces:**
- Consumes: `SESSION_COOKIE`, `session_is_valid` (task 3); `Store` (task 1).
- Produces: `build_api_guard(token: str | None, store: Store)` — **the signature gets a second, mandatory parameter.** Every caller must pass the store along.

This task is additive: the guard additionally lets cookies through, but does not yet refuse anything it lets through today. The lockdown comes in task 8.

- [ ] **Step 1: Write the failing test**

The tests need access to the app's store to set a password. So **first**, change `_build_client` and both fixtures in `tests/api/test_security.py` so they pass it along:

```python
async def _build_client(
    tmp_path: Path, no_invoke: Any, *, api_token: str | None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, int, Store]]:
    """Builds a store, a REAL `Runtime` (for `/resync`) and the app with the
    given `api_token` - the shared setup for `secured_client` and
    `open_client` below, which differ only in `api_token`.

    Since the WebUI login, also hands out the `Store`: the tests need it to
    set a password and log in."""
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

Both fixtures (`secured_client`, `open_client`) pass the four-tuple through unchanged; every existing test in this file that unpacks `client, app, device_id = ...` gets extended to `client, app, device_id, store = ...` (the test run shows which ones those are).

Then the actual new test:

```python
async def test_a_session_cookie_opens_every_api_router(secured_client):
    """The second proof alongside the token: whoever is signed in gets
    through each of the five router groups without an `Authorization`
    header."""
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
        assert response.status_code == 200, f"{path} required a login despite the session"


async def test_an_invalid_session_cookie_does_not_open_anything(secured_client):
    client, _app, _device_id, _store = secured_client
    client.cookies.set("loxmatter_session", "erfunden")
    assert (await client.get("/api/devices")).status_code == 401


async def test_the_token_still_works_next_to_the_cookie(secured_client):
    """The path for scripts stays unchanged - it's the reason the
    token exists at all."""
    client, _app, _device_id, _store = secured_client
    response = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200


async def test_the_live_websocket_connects_with_a_cookie_and_no_subprotocol(secured_client):
    """The point where the detour via the subprotocol becomes unnecessary:
    the cookie travels along with the handshake on its own, because this
    WebSocket has the same origin as the page. `app.js` relies exactly on
    that, ever since it started using `new WebSocket(url)` with no second
    argument."""
    client, app, _device_id, store = secured_client
    store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
    login = await client.post("/auth/login", json={"password": "ein-gutes-passwort"})
    assert login.status_code == 200
    session_id = client.cookies.get("loxmatter_session")
    assert session_id is not None

    status = await _websocket_handshake_status(
        app, headers=[(b"cookie", f"loxmatter_session={session_id}".encode())]
    )
    assert status is None, "The handshake was rejected despite a valid session"
```

`_websocket_handshake_status` already exists in this file (it currently uses it to check the rejection before `websocket.accept()`); the call needs to be extended with a `headers` parameter if it doesn't already have one — the existing structure of the function shows how the handshake header lines are set there. `None` stands for "not rejected", i.e. a handshake that went through.

Add an import at the top of the file:

```python
from loxmatter.auth.passwords import hash_password
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_security.py -v`
Expected: FAIL — `test_a_session_cookie_opens_every_api_router` gets 401, because the guard doesn't know the cookie yet.

- [ ] **Step 3: Teach the guard about cookies**

In `src/loxmatter/loxone/server.py`, add the imports:

```python
from starlette.requests import HTTPConnection

from loxmatter.auth.sessions import SESSION_COOKIE, session_is_valid
```

Rewrite `build_api_guard` (signature and body; the existing docstring stays and gets the new section):

```python
def build_api_guard(token: str | None, store: Store) -> Callable[..., Awaitable[None]]:
    """Protects the `/api` routes, not the Miniserver's (task 8, phase 5).

    ... (existing docstring unchanged) ...

    **Since the WebUI login there are two proofs instead of one.** First
    the session cookie (`loxmatter_session`, see `auth.sessions`), then
    the bearer token. The cookie is the browser's path, the token that of
    scripts and `curl` - which is why the cookie is checked first: it is
    the more common case, and it costs one SELECT instead of a hash
    comparison.

    `HTTPConnection` instead of `Request`: it is the shared base type of
    `Request` and `WebSocket`, and the same dependency hangs off both
    kinds of routes - `/api/live` is a WebSocket route, in which a
    `Request` parameter could not be resolved at all. The cookie travels
    along with the WebSocket handshake by itself (same origin), which is
    why the browser no longer needs a subprotocol there since the login.
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

Adjust the call in `build_app`:

```python
    api_guard = [Depends(build_api_guard(api_token, store))]
```

- [ ] **Step 4: Fix the direct guard tests**

`tests/api/test_security.py` contains `test_guard_*` tests that call `build_api_guard` without an app. They now need a store — add a helper in this file and switch all `build_api_guard(...)` calls to use it:

```python
@pytest.fixture
def guard_store(tmp_path):
    """An empty store for the tests that call `build_api_guard` directly -
    with no password and no session, so that the token alone continues to
    decide there between letting through or refusing."""
    store = Store(tmp_path / "guard.sqlite")
    yield store
    store.close()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_security.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS — `build_api_guard` only gained one parameter, `build_app` passes it through itself; no other caller exists.

- [ ] **Step 7: Lint, typecheck, commit**

```bash
uv run ruff check src tests && uv run mypy
git add src/loxmatter/loxone/server.py tests/api/test_security.py
git commit -m "feat(server): Waechter akzeptiert das Sitzungs-Cookie neben dem Token"
```

---

### Task 7: The interface — setup, login, no token box

**Files:**
- Modify: `src/loxmatter/web/app.js`, `src/loxmatter/web/index.html`, `src/loxmatter/web/style.css`
- Test: `tests/api/test_web.py` (only the delivery tests, see step 6)

**Interfaces:**
- Consumes: `GET /auth-info`, `POST /auth/setup`, `POST /auth/login`, `POST /auth/logout` (task 5); the cookie is set by the browser and sent along automatically.
- Produces: nothing for later tasks.

- [ ] **Step 1: Strip the token plumbing out of `app.js`**

Delete outright, with nothing replacing them: the constants `TOKEN_STORAGE_KEY` and `WEBSOCKET_BEARER_MARKER` along with their comment blocks (lines around 33–62), the functions `readStoredToken` and `authHeaders` (around 65–90), and in `app()` the methods `tokenStatusText`, `startTokenEdit`, `cancelTokenEdit`, `saveToken`, `clearToken`, `reloadAfterTokenChange` as well as the state fields `tokenIsSet`, `tokenEditing`, `tokenDraft`.

- [ ] **Step 2: Point the two `fetch` calls at the cookie**

In `requestJson`, replace the headers block:

```javascript
    response = await fetch(path, {
      method,
      // The session cookie instead of a token in the header: `same-origin`
      // sends it along to exactly the origin this page was loaded from,
      // and to no other. An `Authorization` header is no longer set
      // here - the path via the token still exists, but for scripts, not
      // for this browser (see api/auth.py).
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
```

In `requestDownload` accordingly:

```javascript
    response = await fetch(path, { credentials: "same-origin" });
```

Adjust the docstring above `requestDownload` — it currently references `authHeaders()`:

```javascript
/**
 * Downloads a file from `/api`. Via `fetch` and not via an
 * `<a href>`, because a 401 would otherwise land as raw error text in the
 * browser window instead of in the UI - and because the blob download can
 * set the file name this way.
 */
```

- [ ] **Step 3: Rewrite `UnauthorizedError` and `noteAuthError`**

```javascript
/**
 * The error of a call with no valid session - its own class, so that the
 * UI can distinguish this case from every other failure without
 * checking a message text.
 */
class UnauthorizedError extends Error {
  constructor() {
    super("Die Sitzung ist abgelaufen – bitte erneut anmelden.");
    this.name = "UnauthorizedError";
  }
}
```

and in `app()`:

```javascript
    /**
     * A 401 in the middle of operation means: the session has expired or
     * was ended elsewhere. Then back to the login screen - an error
     * message pointing at an input field that no longer exists would be
     * worse than none at all.
     */
    noteAuthError(error) {
      if (error instanceof UnauthorizedError) {
        this.authenticated = false;
        this.authError = error.message;
      }
    },
```

- [ ] **Step 4: Add the auth state and screens to `app()`**

The state fields (in place of the deleted access block):

```javascript
    // --- Access -------------------------------------------------------
    // `authReady` prevents the wrong screen from flashing up: until
    // `/auth-info` has answered, the page doesn't know whether to show
    // setup, login, or the app, and so shows none of them.
    authReady: false,
    passwordSet: false,
    authenticated: false,
    passwordDraft: "",
    passwordRepeatDraft: "",
    authBusy: false,
    authError: null,
```

`init()` and the access methods:

```javascript
    async init() {
      await this.loadAuthInfo();
      if (this.authenticated) {
        await this.startApp();
      }
    },

    /** Queries the access state - the first call on every page load. */
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
     * Everything that requires a logged-in session. Kept separate from
     * `init`, because it has to run a second time after login - then
     * without reloading the page.
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
     * The shared part of setup and login: submit, show errors, start the
     * app on success. The server sets the cookie, this page never
     * touches it (it is `HttpOnly`).
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
        // In every case: a password does not stay behind in the page's
        // memory, not even after a failed attempt.
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
        // Even a failed logout is meant to log out: the reload below
        // discards any loaded state, and without a valid session the
        // page only gets as far as the login screen anyway.
      }
      window.location.reload();
    },
```

On a 401, `requestJson` throws an `UnauthorizedError`, whose text would be wrong for the login screen ("session expired" for a typo in the password). So in `submitPassword`, do **not** use `this.request`, but `requestJson` directly — the server's 401 text ("Falsches Passwort.") then does not come through. To keep the message correct, restrict the 401 branch in `requestJson` to the path:

```javascript
  if (response.status === 401 && !path.startsWith("/auth/")) {
    throw new UnauthorizedError();
  }
  if (!response.ok) {
    throw new Error(await readErrorDetail(response));
  }
```

This way a failed login carries the server's text ("Falsches Passwort.", "Zu viele Fehlversuche – in X Sekunden wieder möglich."), while a 401 on `/api` still leads to the login screen.

- [ ] **Step 5: Simplify `connectLive`**

Replace the entire token block (comment, `readStoredToken`, the `try`/`catch` around the constructor) with:

```javascript
    connectLive() {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${protocol}//${window.location.host}/api/live`;
      // No subprotocol anymore: the session cookie travels along with the
      // handshake by itself, because this WebSocket has the same origin as
      // the page. The detour previously needed, `new WebSocket(url,
      // ["bearer", token])` - and with it the special case where a token
      // with a space made the constructor throw synchronously - goes away
      // with nothing replacing it. The server still reads the subprotocol,
      // but for scripts (see `loxone.server.build_api_guard`).
      const socket = new WebSocket(url);
```

The rest of the method (the three `addEventListener`) stays unchanged.

- [ ] **Step 6: Replace the token box in `index.html`**

Replace the entire `<div class="token-box">` block along with the HTML comment above it with a logout button:

```html
        <!--
          Access: since the WebUI login, there is no token field here
          anymore, only the way out. The way in are the two screens below
          this header.
        -->
        <button class="logout" x-show="authenticated" x-cloak @click="logout()">
          Abmelden
        </button>
```

Insert the two screens directly after `<body x-data="app()">` and **before** `<header>`; the previous content (`<header>` through `</main>`) is hung into a `<template x-if="authenticated">`, so that without a session none of it appears:

```html
    <!-- Until `/auth-info` has answered, the page deliberately shows
         nothing - otherwise the wrong screen would flash up depending on
         the answer. -->
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

In `style.css`, delete the rules for `.token-box` and `.token-input` and replace them:

```css
/* The setup and login screens: a narrow column in the middle of the
   page, so it is clear there is nothing else to do here. */
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

Open `http://localhost:8099/` in the browser. Expected, in order:
1. Setup screen with the warning, no token field anywhere.
2. Two unequal entries → "Die beiden Eingaben stimmen nicht überein."
3. A password under 8 characters → "Das Passwort muss mindestens 8 Zeichen haben."
4. A valid password twice → the app appears without a reload, the connection indicator goes to "connected" (the WebSocket now carries the cookie).
5. Reload the page → still logged in.
6. "Abmelden" → login screen. Wrong password → "Falsches Passwort.". Five failed attempts → "Zu viele Fehlversuche – in X Sekunden wieder möglich.".
7. Right password → app back again.

Then `rm -f /tmp/loxmatter-ui.sqlite`.

- [ ] **Step 8: Run the full suite, lint, commit**

```bash
uv run pytest && uv run ruff check src tests && uv run mypy
git add src/loxmatter/web
git commit -m "feat(web): Einrichtungs- und Login-Bildschirm statt Token-Eingabe"
```

---

### Task 8: Without a password `/api` no longer delivers anything

**Files:**
- Modify: `src/loxmatter/loxone/server.py` (`build_api_guard`), `src/loxmatter/cli.py` (`_warn_if_missing_api_token` → `_warn_if_no_password`)
- Modify: `tests/api/conftest.py` (new helper) and every test file with `build_app(...)` calls
- Test: `tests/api/test_security.py`

**Interfaces:**
- Consumes: everything from tasks 1–6.
- Produces: `tests/api/conftest.py` exports `TEST_PASSWORD: str` and `async def authenticate(store: Store, client: httpx.AsyncClient) -> None`.

**This is the breaking task.** After step 3, all API tests fail until step 5 catches the fixtures up. That is expected and the reason both live in one task.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_security.py`:

```python
async def test_without_a_password_every_api_route_is_closed(open_client):
    """The tightening from Spec 4: until now, exactly this state - no
    password, no token - was completely open, with nothing but a warning in
    the log."""
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
    """`/cmd` and `/resync` stay open in EVERY state - the Miniserver can
    send neither a header nor a cookie."""
    client, _app, _device_id, _store = open_client
    assert (await client.get("/resync")).status_code == 200
    assert (await client.get("/health")).status_code == 200


async def test_without_a_password_a_configured_token_still_works(secured_client):
    """The existing-installation case right after the update: the password
    is still missing, the token is in the `.env` - scripts must not break
    because of this."""
    client, _app, _device_id, _store = secured_client
    response = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200


async def test_a_password_alone_is_enough(open_client):
    """No token configured, but signed in - the normal case after initial
    setup."""
    client, _app, _device_id, store = open_client
    store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
    await client.post("/auth/login", json={"password": "ein-gutes-passwort"})
    assert (await client.get("/api/devices")).status_code == 200
```

The existing tests in this file that expect open access via `open_client` reverse their expectation: what expected `200` there now expects `401`. The test run in step 4 shows which ones those are; their docstrings need to be updated accordingly (they currently describe "the state before task 8, or an installation that has not (yet) set one").

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_security.py -v`
Expected: FAIL — `test_without_a_password_every_api_route_is_closed` gets 200 instead of 401.

- [ ] **Step 3: Close the guard**

In `build_api_guard`, remove the branch `if expected is None: return` and rewrite the body like this:

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

Replace the docstring section "No token set …":

```
    **There is no more open state.** Up to this point, a service without
    a configured token let every `/api` route through and made do with a
    warning in the log - whoever overlooked the warning was running an open
    bridge without noticing. Since the WebUI login, the rule is: without
    a valid cookie and without a valid token, every request here ends with
    401, even if neither a password nor a token has been set up. The only
    way in is then the initial setup under `/auth/setup`, which hangs
    outside this guard (see `api/auth.py`).
```

- [ ] **Step 4: Run the suite and see what breaks**

Run: `uv run pytest`
Expected: FAIL — every test in `tests/api/` that calls `/api` without a password. The list from this run is the work list for step 5.

- [ ] **Step 5: Add the helper and authenticate every fixture**

Append to `tests/api/conftest.py`:

```python
# The password every test fixture logs in with. A fixed value rather than
# a random one: it shows up in failure messages of failing tests, and
# there "test-passwort" is more helpful than a random string.
TEST_PASSWORD = "test-passwort"


async def authenticate(store: Store, client: httpx.AsyncClient) -> None:
    """Sets a password and logs `client` in.

    Needed ever since the guard stopped letting anything through without
    proof (Spec 4): a test fixture that calls `/api` must be logged in like
    a browser. `httpx.AsyncClient` carries its own cookie store, so a single
    call here is enough for every following request from the same client."""
    store.auth.set_password_hash(hash_password(TEST_PASSWORD))
    response = await client.post("/auth/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 200, "Login in the test fixture failed"
```

with the imports `import httpx2 as httpx`, `from loxmatter.auth.passwords import hash_password`, `from loxmatter.model.store import Store` at the top, if not already present there.

Then, in **every** fixture that builds an `httpx.AsyncClient` via `build_app`, add a line directly after the `async with` and before the `yield`. Example `tests/api/conftest.py::api_with_runtime`:

```python
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield WebSocketClient(client, app), runtime, device_id
```

The same line in: `tests/api/test_devices.py:19`, `tests/api/test_web.py:57`, `tests/api/test_export_api.py:35,205,251`, `tests/api/test_diagnostics.py:82,105,129,151,227,247,272`, `tests/api/test_live_smoke.py:170`. **Not** in `tests/api/test_security.py` — this file is specifically testing the unauthenticated state and logs in only where a test explicitly does so.

`tests/api/test_live_smoke.py:193` already builds the app with `api_token="secret"` and sends the token along — this test stays unchanged.

`tests/loxone/test_server.py` only calls `/cmd`, `/resync`, and `/`; the four `build_app` calls there need no login. Should the run there report something anyway, the same line applies.

- [ ] **Step 6: Update the startup warning**

In `src/loxmatter/cli.py`, replace `_warn_if_missing_api_token` with `_warn_if_no_password`:

```python
def _warn_if_no_password(store_path: Path) -> None:
    """Warns clearly at startup for as long as no password has been set.

    Since the WebUI login, the warning is about the password and NO
    LONGER about the token: a configured token does not silence it,
    because it is the route for scripts, not a substitute for the initial
    setup.

    The state it warns about is different from before. Up to this point,
    a service without a token ran completely open. Now it delivers
    nothing at all without a password - but in exchange, until a
    password is set, anyone who can reach it can take it over by
    completing the initial setup (spec 5, a deliberate decision). That is
    exactly what this text targets.

    A dedicated function instead of one line inline in `run`/`_run`, so a
    test can call it without a running server - see
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

In `run`, replace the call — it currently sits before `asyncio.run(_run(...))` and now receives the resolved path instead of the token:

```python
    resolved_store_path = _resolve_store_path(store_path)
    _warn_if_no_password(resolved_store_path)
```

(The line `resolved_store_path = _resolve_store_path(store_path)` already exists in `run` — the warning is moved to right after it, instead of inserting a second resolution.)

Rewrite the tests `test_warn_if_missing_api_token_*` in `tests/api/test_security.py` accordingly: warns without a password, stays silent with one set, warns even with a configured token.

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

### Task 9: The 403 branch of the fabric backup goes away

**Files:**
- Modify: `src/loxmatter/api/diagnostics.py` (`build_diagnostics_router`, `fabric_backup`), `src/loxmatter/loxone/server.py` (call)
- Test: `tests/api/test_security.py`, `tests/api/test_diagnostics.py`

**Interfaces:**
- Consumes: the closed guard from task 8.
- Produces: `build_diagnostics_router(store, command_log, client, sender, matter_data_dir)` — **the parameter `api_token_configured` goes away, with nothing replacing it.**

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_security.py`:

```python
async def test_fabric_backup_is_served_after_a_login_without_any_token(open_client):
    """After login the fabric backup is also free (Spec 11): a login
    is the stronger credential, and a second secret afterward would protect
    nothing that isn't already protected."""
    client, _app, _device_id, store = open_client
    store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
    await client.post("/auth/login", json={"password": "ein-gutes-passwort"})
    response = await client.get("/api/diagnostics/fabric-backup")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_security.py::test_fabric_backup_is_served_after_a_login_without_any_token -v`
Expected: FAIL — 403, because `api_token_configured` is `False` for this fixture.

- [ ] **Step 3: Remove the parameter and the branch**

In `src/loxmatter/api/diagnostics.py`:
1. Remove the parameter `api_token_configured: bool = False` from `build_diagnostics_router`.
2. In `fabric_backup`, remove the entire `if not api_token_configured:` block along with the 403 `HTTPException`; the comment block above it ("This check comes BEFORE every other …") goes away with it.
3. Rewrite the docstring of `fabric_backup` — it currently describes two conditions, one of which goes away:

```python
    @router.get("/fabric-backup")
    async def fabric_backup() -> Response:
        """**WHOEVER CAN CALL THIS ROUTE CAN TAKE OVER THE FABRIC.** That
        is the first sentence of this docstring on purpose.

        The protection does not sit on this function but uniformly on the
        entire router (`loxone.server.build_api_guard`): without a valid
        session cookie and without a valid bearer token, the call ends
        with 401 before this function even runs.

        **The 403 branch that used to be here has been removed** (WebUI
        login, Spec 11). It defended against the case "the service runs
        with no access control at all, so every `/api` route is open" -
        that exact case no longer exists: without a password set, the
        guard allows no `/api` route through, and whoever arrives here has
        presented proof. An unreachable branch whose docstring describes a
        situation that no longer exists would be worse than no branch:
        the next reader would rely on a condition that checks nothing any
        more. That the guard actually hangs off EVERY one of the five
        routers is checked router by router by
        `tests/api/test_security.py`, instead of relying on the shared
        prefix.

        503 remains for "the data directory is not mounted or does not
        exist" (below) - a configuration gap that would only just create
        this capability in the first place.

        Backup of the matter-server data directory (Spec 4.1, 8) as a
        download.

        Deliberately logs NOTHING - neither the resolved path nor the
        file names it contains (see module docstring)."""
```

4. Bring the section of `diagnostics.py`'s module docstring that explains the 403 rule (around line 58–68) up to the same state.

In `src/loxmatter/loxone/server.py`, remove the argument from the call, along with its comment block:

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

In `tests/api/test_security.py`, remove the `test_fabric_backup_without_a_token_*` tests — they check a state that no longer exists; the new test from step 1 and `test_without_a_password_every_api_route_is_closed` from task 8 cover the route. In `tests/api/test_diagnostics.py`, bring all `build_diagnostics_router(..., api_token_configured=...)` and `build_app(..., api_token=...)` calls that target the 403 up to the new state (the run in step 5 shows them).

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

### Task 10: `loxmatter set-password` as an emergency exit

**Files:**
- Modify: `src/loxmatter/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `hash_password` (task 2), `Store.auth` (task 1), `_resolve_store_path` (existing).
- Produces: CLI command `loxmatter set-password [--store-path PATH]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` (following the `CliRunner` pattern already present there). Add at the top of the file, if not already present: `from loxmatter.auth.passwords import hash_password, verify_password` and `from loxmatter.model.store import Store`.

```python
def test_set_password_writes_a_hash_and_clears_sessions(tmp_path):
    """The emergency exit from Spec 9: a headlessly set up service with
    a forgotten password would otherwise be permanently lost."""
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
        # Whoever resets the password does not want an old session to
        # keep running.
        assert store.auth.session_expires_at("alte-sitzung") is None
    finally:
        store.close()
    # The password itself must not appear in any output.
    assert "neues-passwort" not in result.output


def test_set_password_rejects_a_short_password(tmp_path):
    path = tmp_path / "t.sqlite"
    Store(path).close()
    result = runner.invoke(app, ["set-password", "--store-path", str(path)], input="kurz\nkurz\n")
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

In `src/loxmatter/cli.py`, after the `run` command:

```python
@app.command()
def set_password(
    store_path: Path | None = typer.Option(  # noqa: B008
        None, help="Datenbank mit den Signalschlüsseln. Siehe --store-path bei `export`."
    ),
) -> None:
    """Resets the interface's password — the emergency exit for the case
    where it has been forgotten.

    Without this command, a headlessly set up installation with a
    forgotten password would be permanently lost: initial setup is
    permanently closed (409) after the first password is set, and there
    is no second way in. Whoever can run this command already has
    access to the database file itself — the command merely turns that
    into a usable path instead of tinkering with the SQLite directly.

    Logs out all open sessions in the process: whoever resets the
    password does not want an old session to keep running.
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
    # Deliberately without the password in the output - not even truncated.
    typer.echo("Passwort gesetzt. Alle offenen Sitzungen wurden abgemeldet.")
```

Add the imports at the top:

```python
from loxmatter.auth.passwords import MIN_PASSWORD_LENGTH, hash_password
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k set_password -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite, lint, commit**

```bash
uv run pytest && uv run ruff check src tests && uv run mypy
git add src/loxmatter/cli.py tests/test_cli.py
git commit -m "feat(cli): set-password als Notausgang fuer ein vergessenes Passwort"
```

---

### Task 11: Documentation

**Files:**
- Modify: `README.md`, `deploy/testhost/.env.example`, `deploy/testhost/docker-compose.yml`, `src/loxmatter/loxone/server.py` (module docstring), `src/loxmatter/web/index.html` (HTML comment at the top), `src/loxmatter/web/app.js` (module docstring)
- Create: `docs/superpowers/plans/2026-09-03-webui-login-release-note.md`

**Interfaces:** none — pure prose.

Spec 13 lists what needs to be updated. The core point: **for installations without a token, this update is a break** — the service delivers nothing anymore until a password is set. Nobody should first notice that from a silent service.

- [ ] **Step 1: Write the release note**

`docs/superpowers/plans/2026-09-03-webui-login-release-note.md`:

```markdown
# Release note: login instead of token entry

**What changes.** The interface now has a password login.
The field for the API token is gone.

**What to do — immediately after rollout.** Open the interface
(`http://<host>:8080/`) and set a password. Until that has happened,
no `/api` route delivers data, and the interface shows nothing but
the setup screen.

**Why immediately.** Initial setup requires no further proof —
whoever gets there first sets the password. So between the update and your
own login, anyone who can reach the bridge on the network can take it
over. Deliberately decided this way so that setup is possible without a
shell on the host; the price is this window, and it should take minutes,
not days.

**What stays the same.** `LOXMATTER_API_TOKEN` still applies — as a path
for scripts and `curl`, no longer for the browser. Existing
automations do not break because of this update, not even before the
password is set. `/cmd` and `/resync` for the Miniserver remain reachable
without any protection whatsoever, as always.

**Forgotten password.** `uv run loxmatter set-password` on the host
resets it and logs out all open sessions.

**A note on the password.** The service speaks HTTP without encryption;
the password travels over the network in plain text when logging in. Pick
one you don't use anywhere else.
```

- [ ] **Step 2: Update `README.md`**

Replace the section on securing the service: login instead of token entry, setting a password on first access, `loxmatter set-password` as the emergency exit, the plain-text note from Spec 14.1, and that the token is now only there for scripts.

- [ ] **Step 3: Update `deploy/testhost/.env.example`**

The comment block on `LOXMATTER_API_TOKEN` currently points to entering it in the interface ("Then enter it in the browser interface top right under 'Token'") — that sentence has become wrong. New: the token is optional and serves scripts; access to the interface runs via the password set on first access. The note on the character set (`openssl rand -hex 32`, no spaces) stays, it still applies to the header.

- [ ] **Step 4: Update `deploy/testhost/docker-compose.yml`**

Two comment blocks: the one on `LOXMATTER_API_TOKEN` (the same correction as in step 3) and the one on the volume line `./data:/matter-data:ro`. The latter currently says the mount and the token belong together and that without a token the mount is ineffective. The password now carries that: the mount is justifiable because without proof no `/api` route answers anymore.

- [ ] **Step 5: Update the module docstrings**

- `src/loxmatter/loxone/server.py`, top: the section about `api_token` as the sole credential now describes two proofs and the disappearance of the open state.
- `src/loxmatter/api/diagnostics.py`, top: already done in task 9 step 3 — just proofread here.
- `src/loxmatter/web/app.js`, top, and `src/loxmatter/web/index.html`, HTML comment at the top: both describe an interface with a token field and no login.

- [ ] **Step 6: Check that no stale reference survives**

Run:

```bash
grep -rn "Token eingeben\|token-box\|localStorage\|api_token_configured\|_warn_if_missing_api_token" src README.md deploy
```

Expected: no hits. Every hit is a spot this task overlooked.

- [ ] **Step 7: Run the full suite and commit**

```bash
uv run pytest && uv run ruff check src tests && uv run mypy
git add README.md deploy src docs
git commit -m "docs: Login statt Token-Eingabe in README, Deployment und Docstrings"
```

---

## Completion

After task 11, the spec is fully implemented. For acceptance:

```bash
uv run pytest && uv run ruff check src tests && uv run mypy
```

Then the manual run-through from task 7 step 7 on a fresh database — it is the only part no test file covers, because it needs the real browser: setup, reload, logout, failed attempts, login.
