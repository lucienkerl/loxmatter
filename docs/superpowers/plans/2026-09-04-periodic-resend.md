# Periodic resend as opt-in - implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The periodic full resend (currently every 300s, every known value) is replaced by an explicit opt-in selection per signal, to shrink the rate-limiter burst with many devices; the interval becomes configurable at runtime through the WebUI.

**Architecture:** A new `resend` flag per signal (store, like the existing `exported` flag) plus a global interval in the already-existing `setting` table (like the language setting). `Runtime.resend_all()` stays unchanged as the full restore path for `/resync` and bridge start; a new `Runtime.resend_marked()` filters on the flag and is called exclusively by the periodic timer, which reads the interval live from the store.

**Tech Stack:** Python, SQLite (via `sqlite3`), FastAPI/Pydantic, Alpine.js (`web/app.js`/`web/index.html`), pytest (`asyncio_mode = auto`).

## Global Constraints

- A signal's key stays untouched in every case (Spec 6.2) - none of the changes described here ever writes to `signal.key`.
- Default for `resend`, existing as well as newly registered: `false` - after this update, nothing is automatically resent periodically any more to start with (spec section 3).
- `Runtime.resend_all()` stays unchanged in behavior and signature - `/resync` (`server.py`) and bridge start (`cli.py`, after `seed_from_snapshot`) must continue to restore EVERY known value, regardless of the `resend` flag (spec section 6, correction from 2026-09-04).
- Online status (`d<id>_online`), pulse counters (`_n` suffix), and the heartbeat (`bridge_alive`) get no `resend` flag and stay unaffected by this change (spec section 7).
- No CLI flag for the interval - changeable exclusively via `GET`/`PATCH /api/settings/resend-interval` (spec section 5/7).
- New store classes follow the pattern of `LocaleStore`/`BridgeSettingsStore`: own module, own class, a view onto the same `setting` table, no second connection setup.
- Comments/docstrings in this project are German prose that explains the WHY, not the WHAT - new code follows this style (see existing files).

---

### Task 1: Store - `signal.resend` column, flag, and global key lookup

**Files:**
- Modify: `src/loxmatter/model/store.py`
- Test: `tests/model/test_store.py`
- Test: `tests/model/test_store_migration.py`

**Interfaces:**
- Produces: `StoredSignal.resend: bool` (new field), `Store.set_resend(key: str, resend: bool) -> None`, `Store.resend_keys() -> list[str]` (keys of all signals with `resend = true`, active devices only).

- [ ] **Step 1: Add the schema, migration, and dataclass field**

In `src/loxmatter/model/store.py`:

The `signal` table in `_SCHEMA` gets the new column (after `functional`):

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

`_SCHEMA_VERSION` from 5 to 6, with a comment paragraph analogous to the existing ones:

```python
# ... (existing comment stays) ... Version 6 (periodic resend design,
# 2026-09-04) adds `signal.resend`, see `_migrate_to_v6` - no backfill,
# every existing row starts at the column default (0/off).
_SCHEMA_VERSION = 6
```

New migration function, directly after `_migrate_to_v5`:

```python
def _migrate_to_v6(db: sqlite3.Connection) -> None:
    """Adds `signal.resend` (periodic resend as opt-in, design 2026-09-04)
    - no backfill: every existing row starts at `resend = 0`, exactly the
    column default. Unlike `exported` (`_migrate_to_v1`), there is no
    existing value here from which a sensible default could be derived -
    on the contrary, "off" is explicitly the desired default here (see
    design, section 3): the periodic full resend should only start again
    for EVERY signal after this update through a deliberate user
    decision."""
    _add_column_if_missing(db, "signal", "resend", "INTEGER NOT NULL DEFAULT 0")
```

Add the new entry to `_MIGRATIONS`:

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

`StoredSignal` gets the new field (at the end, after `functional`):

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
    # resend (periodic resend design, 2026-09-04): whether this signal
    # should be sent again by the periodic timer, even if it has not
    # changed - togglable by the user (`PATCH /api/signals/{key}`),
    # independent of `exported`/`functional`. Affects ONLY
    # `Runtime.resend_marked()` (the periodic timer); `resend_all()` (for
    # `/resync` and bridge start) deliberately ignores this field and
    # continues to send every known value, see its docstring.
    resend: bool
```

`_as_signal` reads the new column:

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

New methods, directly after `set_exported`:

```python
    def set_resend(self, key: str, resend: bool) -> None:
        """Sets a signal's resend flag (`PATCH /api/signals/{key}`,
        periodic resend design, 2026-09-04). Like `set_exported`, without
        an existence check - see there."""
        self._touch_owning_device(key)
        self._db.execute("UPDATE signal SET resend = ? WHERE key = ?", (int(resend), key))
        self._db.commit()
```

Directly after `signal_by_key` (both read from the same table, they belong together functionally):

```python
    def resend_keys(self) -> list[str]:
        """All signal keys with `resend = true`, across all ACTIVE
        devices - for `Runtime.resend_marked()` (periodic resend as
        opt-in, design 2026-09-04). A signal of a removed device
        (`forget_device`) no longer shows up here, exactly as with
        `devices()`."""
        rows = self._db.execute(
            "SELECT signal.key FROM signal"
            " JOIN device ON device.id = signal.device_id"
            " WHERE signal.resend = 1 AND device.active = 1"
        ).fetchall()
        return [str(r["key"]) for r in rows]
```

- [ ] **Step 2: Write failing tests for `set_resend`/`resend_keys`**

Append to `tests/model/test_store.py` (after `test_exported_flag_survives_reregistration`):

```python
def test_set_resend_toggles_the_flag_without_touching_the_key(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    target = signals[0]
    assert target.resend is False  # default value (design, section 3)

    store.set_resend(target.key, True)
    after = next(s for s in store.signals(device_id) if s.key == target.key)
    assert after.resend is True
    assert after.key == target.key


def test_resend_flag_survives_reregistration(store):
    """Like `exported`: once set by the user, a repeated
    `register_signals` must not reset the resend flag."""
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

- [ ] **Step 3: Run the tests before the implementation exists**

Run: `pytest tests/model/test_store.py -k resend -v`
Expected: FAIL (`AttributeError: 'StoredSignal' object has no attribute 'resend'` resp. `'Store' object has no attribute 'set_resend'`)

- [ ] **Step 4: Enter the implementation from step 1, get the tests green**

Run: `pytest tests/model/test_store.py -k resend -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the migration test**

Append to `tests/model/test_store_migration.py` (at the end of the file, after `test_migration_to_v5_adds_the_auth_tables_without_touching_devices`):

```python
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
        assert user_version(path) == 6
        assert store.signal_by_key(key).resend is False
    finally:
        store.close()
```

- [ ] **Step 6: Run the migration test (must fail before step 1, but be green now)**

Run: `pytest tests/model/test_store_migration.py -k v6 -v`
Expected: PASS

- [ ] **Step 7: Run the whole store test suite**

Run: `pytest tests/model/ -v`
Expected: PASS (no regression in `exported`/`functional`/other migrations)

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

### Task 2: Store - `ResendSettingsStore` for the global interval

**Files:**
- Create: `src/loxmatter/model/resend_settings_store.py`
- Modify: `src/loxmatter/model/store.py`
- Test: `tests/model/test_resend_settings_store.py`

**Interfaces:**
- Consumes: the generic `setting` table (already present since `_migrate_to_v5`/`_SCHEMA`).
- Produces: `ResendSettingsStore.get_interval_seconds() -> float`, `ResendSettingsStore.set_interval_seconds(seconds: float) -> None` (raises `ValueError` below `MIN_RESEND_INTERVAL_SECONDS`), constants `DEFAULT_RESEND_INTERVAL_SECONDS = 300.0`, `MIN_RESEND_INTERVAL_SECONDS = 10.0`. Reachable as `Store.resend_settings`.

- [ ] **Step 1: Write the failing tests**

New file `tests/model/test_resend_settings_store.py`:

```python
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

"""Tests for `ResendSettingsStore` - the periodic resend interval, held in
the same `setting` table as `LocaleStore.language` (see its
test_locale_store.py for the same pattern)."""

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
        # No partial success: the default value still applies.
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

- [ ] **Step 2: Run the tests before the module exists**

Run: `pytest tests/model/test_resend_settings_store.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'loxmatter.model.resend_settings_store'`)

- [ ] **Step 3: Implement `ResendSettingsStore`**

New file `src/loxmatter/model/resend_settings_store.py`:

```python
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

"""The periodic resend interval - ONE setting for the whole bridge,
changeable at runtime through the WebUI/API instead of a constant fixed at
start. See
docs/superpowers/specs/2026-09-04-periodic-resend-design.md, section 4.

Own module and own class, analogous to `locale_store.py`: the `setting`
table is deliberately generic, exactly so that further configuration like
this one can follow the same path. This class is another view onto the
same table and the same connection, not a second connection setup."""

from __future__ import annotations

import sqlite3

_INTERVAL_KEY = "resend_interval_seconds"

DEFAULT_RESEND_INTERVAL_SECONDS = 300.0
# Lower bound (design, section 5): protects against an accidentally too
# short interval, which with many marked signals would produce exactly
# the burst this design is meant to avoid in the first place.
MIN_RESEND_INTERVAL_SECONDS = 10.0


class ResendSettingsStore:
    """Access to `setting` via the store's connection - like `LocaleStore`,
    just for the key `"resend_interval_seconds"`."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_interval_seconds(self) -> float:
        """The stored value - `DEFAULT_RESEND_INTERVAL_SECONDS`, as long as
        nothing is stored. Never raises."""
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

In `src/loxmatter/model/store.py`, add the import (with the other `loxmatter.model.*` imports):

```python
from loxmatter.model.locale_store import LocaleStore
from loxmatter.model.resend_settings_store import ResendSettingsStore
from loxmatter.model.settings_store import BridgeSettingsStore
```

And in `Store.__init__`, directly after `self.locale = LocaleStore(self._db)`:

```python
        # View onto the same connection - see `resend_settings_store.py`.
        self.resend_settings = ResendSettingsStore(self._db)
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/model/test_resend_settings_store.py -v`
Expected: PASS (4 tests)

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

### Task 3: API - read and set the `resend` flag per signal

**Files:**
- Modify: `src/loxmatter/api/models.py`
- Modify: `src/loxmatter/api/devices.py`
- Test: `tests/api/test_devices.py`

**Interfaces:**
- Consumes: `StoredSignal.resend` (task 1), `Store.set_resend` (task 1).
- Produces: `SignalOut.resend: bool`, `SignalPatch.resend: bool | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_devices.py` (after `test_exporting_a_signal_can_be_turned_off`):

```python
async def test_the_signal_payload_says_whether_resend_is_flagged(api):
    """Periodic resend as opt-in (design 2026-09-04) - default off."""
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

- [ ] **Step 2: Run the tests before the field exists**

Run: `pytest tests/api/test_devices.py -k resend -v`
Expected: FAIL (`KeyError: 'resend'` when accessing `signals[...]["resend"]`, since `SignalOut` doesn't know the field yet)

- [ ] **Step 3: Extend `SignalOut`/`SignalPatch`**

In `src/loxmatter/api/models.py`, `SignalOut` (add the field at the end, append a docstring paragraph):

```python
class SignalOut(BaseModel):
    """`exportable`/`reason` (Spec 6.6) and `exported` (togglable by the
    user, see `model.store.StoredSignal.exported`) say what TECHNICALLY
    fits a Loxone input and which OF THAT should go into the next export -
    `functional` (task 8) answers a third, independent question: whether
    `profiles.relevance.is_functional` classifies this signal as wanted
    for the DEVICE TYPE. The UI uses only this field to split the signal
    list into "Functional" and "Expert" (`api.devices._signal_out` reads
    it unchanged from `StoredSignal.functional`) - there is no second
    computation of the rule, neither in the API layer nor in JavaScript.

    `resend` (periodic resend design, 2026-09-04) is a FOURTH, again
    independent question: whether the periodic timer
    (`Runtime.resend_marked`) should send this signal again even without a
    change. Does not affect `/resync` or bridge start (`Runtime.
    resend_all`) - they deliberately ignore this field, see their
    docstring."""

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

Add the optional field to `SignalPatch`:

```python
class SignalPatch(BaseModel):
    """What can be changed about a signal at all.

    Spec 6.2: the key is the wiring in Loxone. If it were changeable here,
    a click in the UI could silently kill a component in the house -
    that's why this model has no `key` field at all. A `key` sent along
    with the request never lands on the object with Pydantic and is
    accordingly never read by `devices.rename_signal`, let alone applied -
    that is not a question of care in the handler, but one this model
    makes structurally impossible. The reason is Pydantic v2's own default
    for unknown fields, `extra="ignore"` (correction M1, review
    2026-09-02: this incorrectly said `extra="allow"` as the default - the
    opposite, it would keep unknown fields around).
    """

    model_config = ConfigDict(frozen=True)

    title: str | None = None
    exported: bool | None = None
    resend: bool | None = None
```

- [ ] **Step 4: Extend `_signal_out`/`rename_signal` in `devices.py`**

In `src/loxmatter/api/devices.py`, `_signal_out`:

```python
def _signal_out(signal: StoredSignal, values: dict[str, float | bool]) -> SignalOut:
    """..."""  # docstring unchanged
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

In `rename_signal`, after the `exported` line:

```python
        if patch.title is not None:
            store.set_title(key, patch.title)
        if patch.exported is not None:
            store.set_exported(key, patch.exported)
        if patch.resend is not None:
            store.set_resend(key, patch.resend)
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/api/test_devices.py -v`
Expected: PASS (all tests in this file, no regression on `exported`/`functional`)

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

### Task 4: API - read and set the resend interval

**Files:**
- Modify: `src/loxmatter/api/models.py`
- Modify: `src/loxmatter/api/settings.py`
- Test: `tests/api/test_settings_api.py`

**Interfaces:**
- Consumes: `Store.resend_settings` (task 2).
- Produces: `GET /api/settings/resend-interval` and `PATCH /api/settings/resend-interval`, response model `{"interval_seconds": float}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_settings_api.py` (add the import at the top of the file, tests at the end):

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
    response = await client.patch("/api/settings/resend-interval", json={"interval_seconds": 60.0})
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

- [ ] **Step 2: Run the tests before the route exists**

Run: `pytest tests/api/test_settings_api.py -k resend_interval -v`
Expected: FAIL (404, since the route doesn't exist yet)

- [ ] **Step 3: Add the models**

In `src/loxmatter/api/models.py`, directly after `BridgeSettingsIn`:

```python
class ResendIntervalOut(BaseModel):
    """Response of `GET`/`PATCH /api/settings/resend-interval` (periodic
    resend design, 2026-09-04, section 5)."""

    model_config = ConfigDict(frozen=True)

    interval_seconds: float


class ResendIntervalIn(BaseModel):
    """Body of `PATCH /api/settings/resend-interval`. `gt=0` already
    catches a non-positive value here (422 without a custom validator);
    the actual lower bound (`MIN_RESEND_INTERVAL_SECONDS`) is checked by
    `ResendSettingsStore.set_interval_seconds` itself, see there."""

    model_config = ConfigDict(frozen=True)

    interval_seconds: float = Field(gt=0)
```

- [ ] **Step 4: Add the route in `api/settings.py`**

`src/loxmatter/api/settings.py` in full:

```python
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

"""The bridge's connection settings (IP, ports) and the periodic resend
interval over the API - device dashboard design (2026-09-03), section 4,
and periodic resend design (2026-09-04), section 5.

`build_settings_router` builds an `APIRouter` with the prefix `/api`,
exactly like `api.devices.build_device_router` - wired into
`loxone.server.build_app` next to the other routers of this phase, behind
the same `api_guard`."""

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

- [ ] **Step 5: Run the tests**

Run: `pytest tests/api/test_settings_api.py -v`
Expected: PASS (all tests in this file, no regression on `/api/settings`)

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

### Task 5: Runtime - `resend_marked()` alongside unchanged `resend_all()`

**Files:**
- Modify: `src/loxmatter/loxone/runtime.py`
- Test: `tests/loxone/test_runtime.py`

**Interfaces:**
- Consumes: `Store.resend_keys()` (task 1).
- Produces: `Runtime.resend_marked() -> int` (async), sharing a new private `Runtime._force_resend(keys: Sequence[str]) -> int` with `resend_all()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/loxone/test_runtime.py` (after `test_resend_of_an_empty_runtime_sends_nothing`):

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
    """/resync and bridge start rely on `resend_all()` as a complete
    state restoration (Spec 6.4) - the `resend` flag (periodic resend
    design, section 6) must NOT restrict that, or most virtual inputs
    would stay at their default value after a Miniserver restart."""
    runtime, sender, store, device_id, _ = environment
    voltage_key = f"d{device_id}_2_voltage"
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert store.signal_by_key(voltage_key).resend is False  # default value
    sender.sent.clear()

    count = await runtime.resend_all()

    assert count == 1
    assert sender.keys() == [voltage_key]
```

- [ ] **Step 2: Run the tests before `resend_marked` exists**

Run: `pytest tests/loxone/test_runtime.py -k resend_marked -v`
Expected: FAIL (`AttributeError: 'Runtime' object has no attribute 'resend_marked'`)

- [ ] **Step 3: Implement `resend_all`/`resend_marked`/`_force_resend` in `runtime.py`**

In `src/loxmatter/loxone/runtime.py`, replace the existing `resend_all` with:

```python
async def resend_all(self) -> int:
    """Sends EVERY known value again, past the debouncing - regardless
    of the `resend` flag (periodic resend design, 2026-09-04, section
    6). Deliberately stays unchanged as the full restore path for
    `/resync` (`loxone.server`) and bridge start (`cli.py`, directly
    after `seed_from_snapshot`) - both must restore EVERY virtual input
    after a Miniserver restart (Spec 6.4), regardless of whether anyone
    marked the signal for the periodic timer. The periodic timer itself
    calls `resend_marked()` instead, see there.

    Only iterates the keys as a snapshot, but reads the value from
    `_last_values` freshly PER KEY only immediately before sending
    (review fix I4, 2026-09-02). The old code captured `(key, value)`
    pairs together as one snapshot and then waited - due to the
    debouncing in `UdpSender` - up to a few seconds for around 110
    signals. A concurrent update during that time would already write
    its new value into `_last_values` and send it itself immediately,
    but the long-running resend, with its long-stale snapshot, would
    then arrive a second time afterward and overwrite the fresh value
    in Loxone with the old one again. The bug only heals itself on the
    next real update - but the trigger here is `/resync`, wired to the
    system-start block, and therefore fires exactly when someone is
    watching.
    """
    return await self._force_resend(list(self._last_values))


async def resend_marked(self) -> int:
    """Like `resend_all`, but only for signals with `resend = true`
    (periodic resend design, 2026-09-04, section 6) - the counterpart
    to `resend_all`'s deliberate disregard of this flag. Only
    `_resend_loop` calls this method."""
    keys = self._store.resend_keys()
    return await self._force_resend(keys)


async def _force_resend(self, keys: Sequence[str]) -> int:
    """Shared core of `resend_all`/`resend_marked` - see `resend_all`
    for the reasoning why the value is read from `_last_values` freshly
    PER KEY only immediately before sending (review fix I4)."""
    count = 0
    for key in keys:
        value = self._last_values.get(key)
        if value is None:
            # Between the snapshot of the keys above and this access, a
            # key could theoretically have disappeared - practically
            # never, but `_last_values` knows no deletion, only
            # overwriting. Safer to skip than to put a `None` value on
            # the wire.
            continue
        # Deliberately no `_notify_observers(...)` here (review fix
        # minor #3, 2026-09-02): a resend only sends values an observer
        # (e.g. the WebUI) has long since seen as current - not a new
        # value, so no new notification is needed either.
        await self._sender.send(key, value, force=True)
        count += 1
    return count
```

(`Sequence` is already imported in this file: `from collections.abc import Callable, Sequence`.)

- [ ] **Step 4: Run the tests**

Run: `pytest tests/loxone/test_runtime.py -v`
Expected: PASS (all tests in this file, in particular the existing `resend_all` tests unchanged and green)

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

### Task 6: Runtime - `_resend_loop` with a live-configurable interval

**Files:**
- Modify: `src/loxmatter/loxone/runtime.py`
- Test: `tests/loxone/test_runtime.py`

**Interfaces:**
- Consumes: `Runtime.resend_marked()` (Task 5), `Store.resend_settings.get_interval_seconds()` (Task 2).
- Produces: `Runtime.__init__(..., *, heartbeat_seconds: float = 30.0, resend_poll_seconds: float = 5.0)` - the previous `resend_seconds` parameter is dropped with no replacement (no existing caller uses it, see the search below).

- [ ] **Step 1: Write the failing tests**

Append to `tests/loxone/test_runtime.py` (after the three tests from task 5):

**Correction (discovered while running this plan, before implementing
task 6):** the two tests below originally called
`store.resend_settings.set_interval_seconds(0.01)` to simulate a very
short interval - but that violates exactly the lower bound
`MIN_RESEND_INTERVAL_SECONDS = 10.0` introduced in task 2 itself
([resend_settings_store.py](../../../src/loxmatter/model/resend_settings_store.py)) and makes the real setter fail with
`ValueError` before the test even gets to the actual behavior under test.
The tests below instead use `monkeypatch` (already used elsewhere in this
test module) to replace `get_interval_seconds` directly - that still
checks that `_resend_loop` reads the interval freshly on EVERY poll,
without bypassing or lowering the real setter's lower bound.

```python
async def test_resend_loop_never_sends_an_unmarked_signal(environment, monkeypatch):
    _, sender, store, device_id, _ = environment
    marked_key = f"d{device_id}_2_voltage"
    unmarked_key = f"d{device_id}_2_current"
    store.set_resend(marked_key, True)
    monkeypatch.setattr(store.resend_settings, "get_interval_seconds", lambda: 0.01)

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


async def test_resend_loop_reacts_to_a_lowered_interval_without_a_restart(environment, monkeypatch):
    """A change made through the WebUI (`PATCH /api/settings/resend-interval`)
    takes effect within a few seconds, without a process restart (design,
    section 6). `interval` is a mutable dict instead of a plain variable,
    because the monkeypatch lambda below has to read it via closure after
    the test has already changed its value."""
    _, sender, store, device_id, _ = environment
    key = f"d{device_id}_2_voltage"
    store.set_resend(key, True)
    interval = {"seconds": 10.0}
    monkeypatch.setattr(store.resend_settings, "get_interval_seconds", lambda: interval["seconds"])

    runtime = Runtime(store, sender, resend_poll_seconds=0.02)
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    sender.sent.clear()

    await runtime.start()
    try:
        await asyncio.sleep(0.09)
        # `runtime.start()` also starts the heartbeat loop on the side
        # (here with the default `heartbeat_seconds=30.0`), which already
        # sends once before its own first sleep (see `_heartbeat_loop`) -
        # independent of the resend interval under test here. Without the
        # filter, "bridge_alive" would incorrectly make this check fail,
        # even though the resend itself (what this test is about) hasn't
        # run at all yet. (Discovered while running this plan, before
        # implementing task 6 - the same finding as the monkeypatch
        # correction above, just this time a side effect of the heartbeat
        # loop instead of the store validation.)
        assert [k for k in sender.keys() if k != "bridge_alive"] == []

        interval["seconds"] = 0.01
        await asyncio.sleep(0.09)
    finally:
        await runtime.stop()

    assert key in sender.keys()
```

- [ ] **Step 2: Run the tests before the loop is reworked**

Run: `pytest tests/loxone/test_runtime.py -k resend_loop -v`
Expected: FAIL (`TypeError: Runtime.__init__() got an unexpected keyword argument 'resend_poll_seconds'`)

- [ ] **Step 3: Rework `__init__` and `_resend_loop`**

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

(the rest of `__init__`'s body is unchanged - only the line `self._resend_seconds = resend_seconds` is dropped, replaced by `self._resend_poll_seconds = resend_poll_seconds` above.)

Fully replace `_resend_loop` with:

```python
    async def _resend_loop(self) -> None:
        """Periodically resends only the marked signals
        (`resend_marked`) - unlike the one-time full restore on
        `/resync` and bridge start (`resend_all`, see there). The
        interval itself is a setting changeable at runtime through the
        WebUI (`store.resend_settings`, periodic resend design,
        section 4/6) instead of a constant fixed at start: this cadence
        reads it freshly on EVERY poll, every `resend_poll_seconds`
        (default 5s) - a change through the WebUI thereby takes effect
        within a few seconds, without a process restart."""
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

- [ ] **Step 4: Run the tests**

Run: `pytest tests/loxone/test_runtime.py -v`
Expected: PASS (all tests in this file)

- [ ] **Step 5: Run the whole test suite - search for `resend_seconds` as a regression check**

```bash
grep -rn "resend_seconds=" src/ tests/
pytest tests/ -v
```

Expected: `grep` finds no more hits (the parameter used to be called `resend_seconds`, no existing caller used it as a keyword - see the research at the start of this plan); the complete suite green.

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

### Task 7: WebUI - checkbox per signal and interval setting

**Files:**
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/app.js`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `PATCH /api/signals/{key}` with `resend` (task 3), `GET`/`PATCH /api/settings/resend-interval` (task 4).
- Produces: no new programming interfaces - pure UI.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_web.py` (after `test_the_signal_view_ships_a_functional_and_an_expert_block`):

```python
async def test_the_signal_row_offers_a_resend_checkbox(api):
    """Periodic resend as opt-in (design 2026-09-04) - the same kind of
    proof as the functional/expert test above: only that the elements
    are delivered and read/write `signal.resend`, not that Alpine
    renders them correctly at runtime (see the docstring there)."""
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

- [ ] **Step 2: Run the test before the UI is adapted**

Run: `pytest tests/api/test_web.py -k resend -v`
Expected: FAIL (`assert "toggleResend" in script` etc. fail, the elements don't exist yet)

- [ ] **Step 3: Add the checkbox in `index.html`**

In `src/loxmatter/web/index.html`, directly after the existing "exportieren" label (in the block around `toggleExported`):

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

(No `x-show="signal.exportable"` on the new checkbox: a resend also makes sense for a signal that is not exportable - but `resend` is not the same as `exported`. Unlike the export checkbox, there is no technical restriction here that would need to hide a checkbox.)

- [ ] **Step 4: Add the interval field in `index.html`**

Replace the existing placeholder card at the end of the settings view:

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

- [ ] **Step 5: Extend `app.js` with state and methods**

In `src/loxmatter/web/app.js`, the `data()` object directly after `settingsError: null,`:

```js
    resendInterval: { interval_seconds: 300 },
    resendIntervalDraft: 300,
    resendIntervalBusy: false,
    resendIntervalError: null,
```

In `startApp()`, extend the existing `Promise.all([...])` with the new loading call:

```js
      await Promise.all([
        ...this.devices.map((device) => this.loadControls(device.id)),
        ...this.devices.map((device) => this.loadSignals(device.id)),
        this.loadExportStatus(),
        this.loadSettings(),
        this.loadResendInterval(),
      ]);
```

Directly after `toggleExported(signal) { ... }`, the new method:

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

Directly after `saveSettings() { ... }` (end of the "settings" section), the two new methods:

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

- [ ] **Step 6: Run the tests**

Run: `pytest tests/api/test_web.py -v`
Expected: PASS (all tests in this file, no regression on the existing signal/settings checks)

- [ ] **Step 7: Verify manually in the browser**

```bash
python -m loxmatter run --miniserver 127.0.0.1 --url ws://localhost:5580/ws
```

Open `http://localhost:8080` in the browser, expand a device: the new "periodisch erneut senden" checkbox must appear next to "exportieren" and, on click, visibly send `PATCH /api/signals/...` with `{"resend": true}` in the network tab. In the "Einstellungen" tab, the new "Periodischer Resend" card must show an interval field, "Speichern" must trigger `PATCH /api/settings/resend-interval` and show a confirmation.

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

**Spec coverage** (against `docs/superpowers/specs/2026-09-04-periodic-resend-design.md`):

- Section 4 (data model: `signal.resend`, `Store.set_resend`, `ResendSettingsStore`) → task 1, task 2.
- Section 5 (API: `PATCH /api/signals/{key}` extended with `resend`, new interval endpoint with a lower bound) → task 3, task 4.
- Section 6 (runtime: `resend_all()` unchanged, `resend_marked()` new, `_resend_loop` live-configurable) → task 5, task 6.
- Section 7 (synthetic keys excluded, no CLI flag) → satisfied automatically: `resend_keys()` queries exclusively the `signal` table (task 1), the online key/pulse counter/heartbeat never live there; no task adds a CLI flag.
- Section 8 (UI: checkbox, interval field) → task 7.
- Section 9 (verification) → every case named there has a concrete test in task 1, 3, 5, or 6.

**Placeholder scan:** no `TBD`/`TODO`/"see above, analogous" without spelled-out code - every step contains the complete code or the complete test script.

**Type consistency:** `Sequence[str]` for `_force_resend` covers both `list(self._last_values)` (dict-keys view converted to a list, in `resend_all`) and `list[str]` (`Store.resend_keys()`'s return type, in `resend_marked`). `ResendIntervalOut`/`ResendIntervalIn.interval_seconds` are `float` throughout, matching `ResendSettingsStore.get_interval_seconds() -> float`/`set_interval_seconds(seconds: float)`. `SignalOut.resend`/`SignalPatch.resend` and `StoredSignal.resend` are `bool` throughout.

Execution handoff follows after this document.
