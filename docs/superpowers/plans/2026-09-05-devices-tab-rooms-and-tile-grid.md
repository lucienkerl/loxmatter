# Devices tab: rooms, categories, and tile grid — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The devices tab gets rooms, device categories derived from Matter, and a multi-column tile grid, so it stays usable even with 20+ devices.

**Architecture:** Two new columns on `device` (migration v7) carry the freely chosen room and the raw Matter device types. The category is derived from that on every read (new module `profiles/categories.py`) instead of stored. The API extends existing routes and gets exactly one new one (`POST /api/rooms/rename`). All filtering, grouping, sorting, and searching happens client-side in `app.js` over the list that `GET /api/devices` already delivers.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite (`sqlite3`, schema versioning via `PRAGMA user_version`), Alpine.js (vendored), pytest / pytest-asyncio, httpx2.

**Spec:** `docs/superpowers/specs/2026-09-05-devices-tab-rooms-and-tile-grid-design.md` — whenever in doubt, the spec governs, not this plan.

## Global Constraints

- **Developer prose in German.** Docstrings, comments, and commit messages in dense, reasoning German that states the *why*. Exception: the GPL header of every source file stays in the English FSF wording.
- **Every user-visible text goes through `i18n.t()`** with an `en` **and** `de` entry in `src/loxmatter/i18n/strings.yaml`. No hardcoded German text in `index.html`, `app.js`, or API error messages.
- **Keys in `strings.yaml` are flat and dotted** (`web.devices.room_all`), no nested YAML structure.
- **`web.*` keys may carry `{placeholders}`**, and the error messages under `web.devices.*` do too. `api/language.py:_web_strings()` delivers them to the browser via `i18n.raw_template()` **unresolved** (`language.py:56-63`), specifically so that `t(key, {…})` in `app.js` can fill them client-side. New messages therefore follow the form of their neighbor `web.devices.label_save_error` (`"… : {message}"`) and are not assembled by string concatenation. Check: after every key change, `uv run pytest tests/api/test_language.py -q`.
- **Commands run with `uv`**: `uv run pytest …`, `uv run ruff check .`, `uv run mypy src`.
- **No external frontend dependencies.** Icons are inline SVG `<symbol>`s in `index.html`, no icon library, no CDN — the UI runs offline.
- **Migration:** `_SCHEMA_VERSION` is set to `7`, `_migrate_to_v7` is entered in `_MIGRATIONS`. New columns always via `_add_column_if_missing`, never via a bare `ALTER TABLE`.
- **`set_room` and `backfill_device_types` do NOT touch `updated_at`.** The room ends up in no export template; tidying up the room assignment must not mark a device as "changed since export". `rename_device` keeps its `updated_at` unchanged.

---

## File Structure

**New:**
- `src/loxmatter/profiles/categories.py` — categories, rank, Matter type mapping, `category_for()`. Sits next to `relevance.py`, because it evaluates the same source (`device_types_by_endpoint`).
- `tests/profiles/test_categories.py` — table and primary-type rule.
- `tests/api/test_rooms.py` — the new room route.

**Changed:**
- `src/loxmatter/model/store.py` — schema v7, `StoredDevice`, `_as_device`, `register_device`, `set_room`, `rename_room`, `backfill_device_types`, JSON encoding of device types.
- `src/loxmatter/api/models.py` — `DeviceOut` (+3 fields), `DeviceRename` → `DevicePatch`, `CommissionRequest` (+`room`), new `RoomRename`.
- `src/loxmatter/api/devices.py` — `_device_out`, PATCH route, commission route, new rooms route.
- `src/loxmatter/cli.py` — `backfill_device_types` at bridge startup.
- `src/loxmatter/i18n/strings.yaml` — new `web.devices.*`, `web.devices.category.*`, `api.devices.*` keys.
- `src/loxmatter/web/app.js` — room/search/sort logic, primary signal, save room, rename room, commissioning with a room.
- `src/loxmatter/web/index.html` — room bar, tile rework (mix 2), commissioning field, eight category icons.
- `src/loxmatter/web/style.css` — tile grid, header with primary signal, room bar, footer.
- `tests/model/test_store.py`, `tests/model/test_store_migration.py`, `tests/api/test_devices.py`, `tests/api/test_web.py` — new tests.

---

### Task 1: Migration v7 — the two columns

**Files:**
- Modify: `src/loxmatter/model/store.py` (header comment on `_SCHEMA_VERSION` starting at line 73, `_SCHEMA_VERSION` line 100, `_SCHEMA` line 103-112, `_MIGRATIONS` line 576-583, `StoredDevice` line 681-707, `_as_device` line 838-847)
- Test: `tests/model/test_store_migration.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `StoredDevice.room: str | None`, `StoredDevice.device_types: dict[int, frozenset[int]] | None`, module functions `_encode_device_types(Mapping[int, frozenset[int]]) -> str` and `_decode_device_types(str | None) -> dict[int, frozenset[int]] | None`, migration function `_migrate_to_v7(sqlite3.Connection) -> None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/model/test_store_migration.py` (the file already imports `sqlite3`, `load`, and `user_version` — check the top of the file and don't import anything twice):

```python
def test_migration_to_v7_adds_room_and_device_types_as_null(tmp_path):
    """An existing database at version 6 gets both columns via
    migration. No backfill: `room = NULL` means "no room", exactly like
    a freshly commissioned device with no room chosen, and
    `device_types = NULL` means "not backfilled yet" - `backfill_device_types`
    is responsible for that at bridge startup, not the
    migration (see design 3.4)."""
    path = tmp_path / "old.sqlite"
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
    """A freshly created database already has both columns via
    `_SCHEMA`. `_add_column_if_missing` has to recognize that - otherwise
    the very first startup would fail with "duplicate column name", the same trap
    `_migrate_to_v1` is already guarded against."""
    path = tmp_path / "new.sqlite"
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
Expected: FAIL — `sqlite3.OperationalError: no such column: room` on `DROP COLUMN`, or `AttributeError: 'StoredDevice' object has no attribute 'room'`.

- [ ] **Step 3: Raise the schema and version**

In `src/loxmatter/model/store.py`: extend the `device` table in `_SCHEMA` by two columns:

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

`_SCHEMA_VERSION = 6` becomes `_SCHEMA_VERSION = 7`, and the header comment above it (which lists every version individually) gets a paragraph added:

```python
# Version 7 (devices tab design, 2026-09-05) adds `device.room` and
# `device.device_types`, see `_migrate_to_v7` - no backfill for
# either, but for two different reasons: `room = NULL` IS the
# correct meaning ("no room"), while `device_types = NULL` only
# means "not backfilled yet" and gets filled at the next bridge startup
# from the snapshots fetched anyway (`backfill_device_types`).
# A migration cannot do that: it only sees the database, never a
# `NodeSnapshot`.
```

- [ ] **Step 4: Write and register the migration**

Insert directly after `_migrate_to_v6`:

```python
def _migrate_to_v7(db: sqlite3.Connection) -> None:
    """Adds `device.room` and `device.device_types` (devices tab
    design, 2026-09-05, section 3.1).

    Two columns in one step, like `_migrate_to_v2` - both belong to
    the same effort and would never occur separately.

    No backfill. For `room` there is no existing value from which a
    room could be derived, and `NULL` is the intended meaning anyway
    ("no room"). For `device_types` there would be one - the Matter
    device types are in the `NodeSnapshot` -, but a migration does not
    have access to exactly that: it gets an `sqlite3.Connection` and
    nothing else. `Store.backfill_device_types` takes over the backfill
    at bridge startup, where the snapshots are fetched anyway."""
    _add_column_if_missing(db, "device", "room", "TEXT")
    _add_column_if_missing(db, "device", "device_types", "TEXT")
```

And in `_MIGRATIONS`:

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

- [ ] **Step 5: Extend `StoredDevice` and `_as_device`**

At the very top of `store.py`, add `import json` to the imports (alphabetically before `sqlite3`).

Two module functions, directly before `class StoredDevice`:

```python
def _encode_device_types(types: Mapping[int, frozenset[int]]) -> str:
    """The output of `relevance.device_types_by_endpoint` as JSON for the
    `device.device_types` column.

    Endpoints become strings, because JSON has no integer
    keys; the IDs are stored sorted, so that two identical
    snapshots also produce the same text - that makes a comparison in
    a test readable and prevents a meaningless
    order change from looking like an actual change."""
    return json.dumps({str(endpoint): sorted(ids) for endpoint, ids in sorted(types.items())})


def _decode_device_types(raw: str | None) -> dict[int, frozenset[int]] | None:
    """Counterpart to `_encode_device_types`. `None` means "not
    backfilled yet" (see `_migrate_to_v7`).

    Unreadable JSON also becomes `None` instead of an exception: a
    row someone tampered with by hand must not make the entire device
    list unusable: the device then ends up in the "other" category
    and gets refilled at the next bridge startup - the same
    treatment as a row that was never filled."""
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

Add `Mapping` to the `collections.abc` imports: `from collections.abc import Callable, Mapping, Sequence`.

Append two fields to `StoredDevice` (after `updated_at`):

```python
    # Room and device types (devices tab design, 2026-09-05). `room` is a
    # freely chosen name, `None` means "no room" - there is deliberately no
    # room table, a room exists for exactly as long as an active device
    # carries its name.
    #
    # `device_types` carries the device's RAW information (endpoint ->
    # Matter type IDs), not the category derived from it. The reason
    # is in this module's history: `signal.functional` and
    # `signal.title` were stored derivations, and `_migrate_to_v3`
    # had to recompute them retroactively for existing rows when
    # the rule improved. A mapping table Matter type -> category
    # will grow; if only the source is stored, that is a
    # code change without a migration.
    room: str | None
    device_types: dict[int, frozenset[int]] | None
```

And `_as_device`:

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
Expected: PASS, all tests in the file — in particular the existing v1–v6 migration tests, which run along with the new version.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/model/store.py tests/model/test_store_migration.py
git commit -m "$(cat <<'EOF'
feat(store): schema v7 with room and raw device types on the device

`device.room` (NULL = "no room") and `device.device_types` (JSON,
endpoint -> Matter type IDs). What is stored is deliberately the raw
information from the device, not the category derived from it:
`_migrate_to_v3` already had to recompute `signal.functional`
retroactively once for exactly this reason, when the derivation rule
improved.

No backfill in the migration - for `room` none would be possible, for
`device_types` it would need a NodeSnapshot, which a migration does not
have access to.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Write and rename the room, pass it along when registering

**Files:**
- Modify: `src/loxmatter/model/store.py` (`register_device` line 808-826, new methods after `rename_device` line 867-882)
- Test: `tests/model/test_store.py`

**Interfaces:**
- Consumes: `StoredDevice.room` from task 1.
- Produces: `Store.set_room(device_id: int, room: str | None) -> None`, `Store.rename_room(old: str, new: str) -> int` (count of changed devices, `ValueError` on an empty target name), `Store.register_device(snapshot: NodeSnapshot, room: str | None = None) -> int`, module function `_normalized_room(str | None) -> str | None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/model/test_store.py` (the file already has `Store` and a `load` helper for fixtures; reuse the existing name, don't reinvent it):

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
    """A name made of pure whitespace has an unambiguous meaning - "no
    room" - and is therefore not an error case but the same path as an
    explicit `None`."""
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"))
        store.set_room(device_id, "Bad")
        store.set_room(device_id, "   ")
        assert store.device(device_id).room is None
    finally:
        store.close()


def test_set_room_does_not_touch_updated_at(tmp_path):
    """The core of the decision from section 3.3 of the design: the room
    ends up in NO export template. If `set_room` also set `updated_at`,
    the first cleanup of room assignments would give every device an amber
    "changed since export" pill and prompt an export that produces
    byte-for-byte the same files. `rename_device`, by contrast, rightly
    does set it - the label gets exported as `Title`."""
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
    """A target name that already exists merges the two rooms - the
    obvious meaning of "rename kitchen to dining area now" when a
    dining area already exists. The UI asks for confirmation beforehand; the
    store only carries it out."""
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
    """`active = 1` in the condition, for the same reason
    `Store.devices()` filters on it afterward: a removed device is no
    longer there from the UI's perspective and should not silently move along."""
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

- [ ] **Step 3: Implement**

Module function, right next to `_decode_device_types`:

```python
def _normalized_room(room: str | None) -> str | None:
    """A room name with no surrounding whitespace; whatever is empty after
    that becomes `None`.

    One spot instead of three: `set_room`, `register_device`, and
    `rename_room` ask the same question, and a room " Bad" next to "Bad"
    would be two rooms in the UI, without anyone seeing the difference."""
    if room is None:
        return None
    return room.strip() or None
```

`register_device` gets the parameter (the early exit for an already-registered device stays **unchanged** — a device already known keeps its room, recommissioning must not overwrite it):

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

Two new methods, directly after `rename_device`:

```python
def set_room(self, device_id: int, room: str | None) -> None:
    """Sets a device's room (`PATCH /api/devices/{device_id}`).

    **Deliberately does NOT touch `updated_at`** - the one point where
    this method diverges from `rename_device` directly above it. Its
    docstring names the reason for the opposite: the label ends up in
    the next export as `Title` in the template, so `GET
    /api/export/status` rightly lists the device afterward as "changed
    since". The room ends up in no template. If it also set `updated_at`,
    the first cleanup of room assignments would give every
    device an amber pill and prompt an export that produces
    exactly the same files as the last one.

    Like `rename_device`, without an existence check: the calling
    route checks via `device()` and reports 404 before it gets here."""
    self._db.execute("UPDATE device SET room = ? WHERE id = ?", (_normalized_room(room), device_id))
    self._db.commit()


def rename_room(self, old: str, new: str) -> int:
    """Renames a room on every active device and returns how many
    there were (`POST /api/rooms/rename`).

    There is no room table (design 3.2), so "rename room" is not
    a write to one object, but this one bulk write.
    The alternative would be typing a new room name individually into each
    device - with five devices, five
    chances for a typo that creates a sixth room.

    `active = 1` for the same reason `devices()` filters on it
    afterward: a removed device is no longer there from the UI's
    perspective.

    A target name that is already taken merges the two rooms; the
    confirmation prompt beforehand is the UI's job, not this method's.
    An empty target name, by contrast, is rejected here: "rename" is
    not the way to dissolve a room - `set_room`
    with `None` on each individual device exists for that."""
    target = _normalized_room(new)
    if target is None:
        raise ValueError(i18n.t("api.devices.room_name_required"))
    cur = self._db.execute(
        "UPDATE device SET room = ? WHERE room = ? AND active = 1", (target, old)
    )
    self._db.commit()
    return int(cur.rowcount)
```

- [ ] **Step 4: Create the translation key for the `ValueError` message**

In `src/loxmatter/i18n/strings.yaml`, in the `api.devices.*` block:

```yaml
api.devices.room_name_required:
  en: "A room name is required."
  de: "Ein Raumname ist erforderlich."
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/model/test_store.py -k "room" -v && uv run pytest tests/model tests/api -q`
Expected: PASS. Check that `import pytest` is already at the top of `tests/model/test_store.py` — add it if not.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/model/store.py src/loxmatter/i18n/strings.yaml tests/model/test_store.py
git commit -m "$(cat <<'EOF'
feat(store): set and rename the room, pass it along when registering

`set_room` deliberately does not touch `updated_at`, unlike
`rename_device` directly above it: the label gets exported as `Title`,
the room in no template. Without this separation, the first cleanup
of room assignments would mark every device as "changed since export"
and prompt an export that produces the same files.

`rename_room` only touches active devices - the same boundary as
`devices()`. A target name already in use merges; the confirmation
prompt beforehand belongs in the UI.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Write device types, backfill them at bridge startup

**Files:**
- Modify: `src/loxmatter/model/store.py` (`register_device`, new method `backfill_device_types`, import from `relevance`)
- Modify: `src/loxmatter/cli.py:606`
- Test: `tests/model/test_store.py`

**Interfaces:**
- Consumes: `_encode_device_types` (task 1), `register_device(snapshot, room)` (task 2).
- Produces: `Store.backfill_device_types(snapshots: Sequence[NodeSnapshot]) -> int` (count of filled rows); `register_device` writes `device_types` along with the registration.

- [ ] **Step 1: Write the failing test**

Append to `tests/model/test_store.py`:

```python
def test_register_device_stores_the_matter_device_types(tmp_path):
    """Endpoint 1 of the plug reports 266 (0x010A, On/Off Plug-in Unit),
    endpoint 0 the management types - both are stored raw, filtering
    only happens when the category is derived."""
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"))
        types = store.device(device_id).device_types
        assert types is not None
        assert types[1] == frozenset({0x010A})
    finally:
        store.close()


def test_backfill_fills_only_rows_that_have_none(tmp_path):
    """An existing row gets its types at the next bridge startup -
    one already filled does not get rewritten on every startup."""
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
    """A device that happens to be offline at startup is missing from
    `client.snapshots()`. It must not lose anything as a result - that's why
    only what has a snapshot gets written, and nothing is ever cleared."""
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
    """The same reasoning as with `set_room`: device types end up in
    no export template. A bridge startup must not mark half the
    device list as "changed since export"."""
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
Expected: FAIL — `assert types is not None` fails (the column is not written yet), or `AttributeError: 'Store' object has no attribute 'backfill_device_types'`.

- [ ] **Step 3: Implement**

In `store.py`, extend the existing import from `loxmatter.profiles.relevance` with `device_types_by_endpoint` (it already imports `is_functional` there).

`register_device` writes the types along with it:

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

New method, directly after `set_room`:

```python
    def backfill_device_types(self, snapshots: Sequence[NodeSnapshot]) -> int:
        """Backfills `device.device_types` for devices that don't yet have
        any, and returns how many that was.

        Called at bridge startup, directly next to
        `runtime.seed_from_snapshot(await client.snapshots())` (`cli.py`) -
        the snapshots of all reachable nodes are already fetched there, a
        second fetch would be pure waste.

        **Only `device_types IS NULL`.** A device already backfilled
        does not get rewritten on every startup, and a device
        that is currently offline and therefore missing from `snapshots()`
        does not lose its types - this only ever fills, never clears.

        Whether a device whose types change on a repeat interview
        (say, after a firmware update) should get an update
        is deliberately left open (design, open point 2): the
        case has never been observed and gets no mechanism on
        spec.

        Does not touch `updated_at` - the same reasoning as with
        `set_room`: device types end up in no export template."""
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

- [ ] **Step 4: Call it at bridge startup**

In `src/loxmatter/cli.py`, right after the existing line 606:

```python
        await runtime.seed_from_snapshot(await client.snapshots())
```

becomes:

```python
        snapshots = await client.snapshots()
        await runtime.seed_from_snapshot(snapshots)
        # Backfill device types for existing devices (devices tab design,
        # 2026-09-05, section 3.4): the snapshots were just fetched, a
        # second fetch just for this purpose would be waste. Only fills
        # rows without types; a device that is currently offline and
        # therefore missing here keeps its own and is reached at the next startup.
        store.backfill_device_types(snapshots)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/model tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/model/store.py src/loxmatter/cli.py tests/model/test_store.py
git commit -m "$(cat <<'EOF'
feat(store): store Matter device types and backfill them at startup

`register_device` stores the output of `device_types_by_endpoint` raw.
`backfill_device_types` fetches existing devices' types at bridge
startup from the snapshots that `seed_from_snapshot` has already
loaded anyway - a second fetch just for this would be waste.

Only fills where nothing is set, and never clears: a device that is
offline at startup is missing from `snapshots()` and must not lose
anything as a result.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `profiles/categories.py` — category from the device types

**Files:**
- Create: `src/loxmatter/profiles/categories.py`
- Create: `tests/profiles/test_categories.py`

**Interfaces:**
- Consumes: `UTILITY_DEVICE_TYPES`, `POWER_SOURCE_DEVICE_TYPE` from `profiles/relevance.py`; `StoredDevice.device_types` from task 1.
- Produces: `Category` (`str, Enum` with values `light|socket|switch|covering|climate|sensor|lock|other`), `CATEGORY_RANK: dict[Category, int]`, `CATEGORY_BY_DEVICE_TYPE: dict[int, Category]`, `category_for(device_types: Mapping[int, frozenset[int]] | None) -> Category`.

- [ ] **Step 1: Write the failing test**

Create `tests/profiles/test_categories.py` new — with the GPL header every source file in this project carries (copy from an existing test file, unchanged, in the English FSF wording):

```python
"""Coarse device category from the Matter device types."""

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

# The same path to the snapshots as in `test_relevance.py` next door:
# `tests/profiles/` has no `conftest.py`, and `load_snapshot` from
# `tests/api/conftest.py` is not importable from here - the two
# directories don't share a `sys.path` entry.
FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load_snapshot(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_the_rank_follows_the_declaration_order():
    """The rank is hardwired and NOT the alphabetical order
    of the translated names: a language switch would otherwise
    reorder the groups, and a view that is laid out differently depending on
    the language has to be explained twice."""
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
    """Endpoint 0 carries Root Node and OTA Requestor, endpoint 1 the
    On/Off Plug-in Unit (0x010A) - the management endpoint is
    skipped."""
    types = device_types_by_endpoint(load_snapshot("ikea_grillplats_plug.json"))
    assert category_for(types) is Category.SOCKET


def test_the_button_fixture_is_a_switch():
    types = device_types_by_endpoint(load_snapshot("ikea_bilresa_button.json"))
    assert category_for(types) is Category.SWITCH


def test_the_color_light_fixture_is_a_light():
    types = device_types_by_endpoint(load_snapshot("synthetic_color_light.json"))
    assert category_for(types) is Category.LIGHT


def test_a_snapshot_without_descriptors_is_other():
    """`example_light.json` reports not a single descriptor attribute - exactly
    the state a not-yet-backfilled existing device is also in."""
    types = device_types_by_endpoint(load_snapshot("example_light.json"))
    assert category_for(types) is Category.OTHER


def test_none_and_empty_are_other():
    assert category_for(None) is Category.OTHER
    assert category_for({}) is Category.OTHER


def test_only_utility_types_are_other():
    """Root Node, OTA Requestor, and PowerSource say nothing about what
    the device does in the house - if nothing is left, the category is
    "other", not, say, that of the management endpoint."""
    assert category_for({0: frozenset({0x0016, 0x0012, 0x0011})}) is Category.OTHER


def test_the_lowest_non_utility_endpoint_decides():
    """In Matter, endpoint 1 is usually the application endpoint. A
    second endpoint with a different type must not override it."""
    types = {
        0: frozenset({0x0016}),
        1: frozenset({0x010A}),
        2: frozenset({0x0302}),
    }
    assert category_for(types) is Category.SOCKET


def test_several_types_on_one_endpoint_resolve_by_rank():
    """So the result is independent of the order in which the
    device enumerates its types - a `frozenset` has none at all."""
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
    """The mapping has to be confirmed per ID against matter-server's
    machine-generated table, not guessed - exactly the source
    `relevance.py` also relies on. A typo in an ID shows up here
    and not only on a real device."""
    from matter_server.client.models.device_types import ALL_TYPES

    unknown = sorted(hex(t) for t in CATEGORY_BY_DEVICE_TYPE if t not in ALL_TYPES)
    assert unknown == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/profiles/test_categories.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.profiles.categories'`.

- [ ] **Step 3: Write the module**

`src/loxmatter/profiles/categories.py` (prepend the GPL header as in every other source file):

```python
"""Coarse device category from the Matter device types.

Answers exactly one question that `relevance.py` doesn't answer: not
"which signals does someone want to see", but "what kind of thing is this
in the first place". The answer carries three things at once in the UI -
sorting within a room, the tile's icon, and the
search term under which one finds all the plugs in the house.

Why next to it and not inside it: `relevance.is_functional` decides about
a single signal, `category_for` about a whole device. Both read
the same source (`device_types_by_endpoint`), but with a different
outcome and no shared state.

**The source of the type numbers** is the same as in `relevance.py`:
`matter_server.client.models.device_types`, per its own module docstring
machine-generated from `zcl/data-model/chip/matter-devices.xml` of the
CSA specification. A new entry in the table below needs the
number from this file, not from memory;
`test_every_mapped_type_exists_in_the_matter_table` checks that.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from loxmatter.profiles.relevance import POWER_SOURCE_DEVICE_TYPE, UTILITY_DEVICE_TYPES


class Category(str, Enum):
    """The order of this declaration IS the sort rank (see
    `CATEGORY_RANK`) - deliberately not the alphabetical order of the
    translated names, which would change with the language.

    The order itself follows how often you touch a device
    of this kind in a room: light and socket first, then the
    controls, and at the very back what you set up once and then leave
    alone. `OTHER` always sits at the end - that's also where every
    device whose types haven't been backfilled yet ends up.

    `str, Enum` instead of `StrEnum`, because `Exportability` in `profiles/table.py`
    does it the same way - a second spelling for the same thing would be
    no gain."""

    LIGHT = "light"
    SOCKET = "socket"
    SWITCH = "switch"
    COVERING = "covering"
    CLIMATE = "climate"
    SENSOR = "sensor"
    LOCK = "lock"
    OTHER = "other"


CATEGORY_RANK: dict[Category, int] = {category: rank for rank, category in enumerate(Category)}

# Device types that say nothing about what the device DOES in the house -
# the same set `relevance.is_functional` already treats as management,
# plus PowerSource: a battery level does not turn a button into a
# category of its own.
_IGNORED_DEVICE_TYPES: frozenset[int] = UTILITY_DEVICE_TYPES | {POWER_SOURCE_DEVICE_TYPE}

# Mapping Matter device type -> category. Every number comes from
# `matter_server.client.models.device_types` (see module docstring); the
# comments name the class name there, so a lookup is possible without
# conversion.
#
# Not listed and therefore `OTHER`: household appliances (0x0070-0x007C),
# media (0x0022-0x002A), energy (0x050C-0x050F), network infrastructure
# (0x0090, 0x0091), bridge management (0x000E Aggregator, 0x0013 Bridged
# Node). They either never occur in a Loxone integration at all, or
# wouldn't deserve their own rank in a room list.
CATEGORY_BY_DEVICE_TYPE: dict[int, Category] = {
    0x0100: Category.LIGHT,  # OnOffLight
    0x0101: Category.LIGHT,  # DimmableLight
    0x010C: Category.LIGHT,  # ColorTemperatureLight
    0x010D: Category.LIGHT,  # ExtendedColorLight
    # MountedOnOffControl / MountedDimmableLoadControl are permanently wired
    # load switches - in practice there's a light behind this, not a
    # plug (which carries its own type, see below).
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
    """The category of a device from its device types per endpoint.

    `None` (device types not backfilled yet, see
    `Store.backfill_device_types`) yields `OTHER` - the same answer as for
    a device whose types nobody can map. The UI does not
    distinguish the two cases: in both, the device sits fully
    controllable under "other", the first case resolves itself at the next
    bridge startup.

    The rule in four steps (design 5.2):

    1. Management types are dropped (`_IGNORED_DEVICE_TYPES`).
    2. Of the rest, the LOWEST endpoint counts - with Matter, usually
       endpoint 1, the application endpoint. A plug with a
       temperature sensor on endpoint 2 stays a plug.
    3. If that endpoint carries several mappable types, the one with the
       lowest rank wins. This makes the result independent of the
       order in which the device enumerates its types - a `frozenset`
       has none anyway.
    4. Nothing mappable -> `OTHER`.
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
Expected: PASS, all 20 test cases (the parametrized table check counts twelve of them).

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/profiles/categories.py tests/profiles/
git commit -m "$(cat <<'EOF'
feat(profiles): derive the device category from the Matter device types

Answers the one question `relevance.py` doesn't answer: not
"which signals does someone want to see", but "what kind of thing is this".
The answer carries sorting, icon, and search term in the UI.

The category rank is the declaration order, expressly
not the alphabetical order of the translated names - otherwise a
language switch would reorder the groups. Every type number comes from
matter-server's machine-generated table, checked against
its ALL_TYPES by a test.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: API — models and routes

**Files:**
- Modify: `src/loxmatter/api/models.py` (`DeviceOut` line 62-99, `DeviceRename` line ~106, `CommissionRequest` line 162-176)
- Modify: `src/loxmatter/api/devices.py` (imports line 77-90, `_device_out` line 171-199, PATCH route line 261-265, commission route line 395-397)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_devices.py`, `tests/api/test_rooms.py` (new)

**Interfaces:**
- Consumes: `Store.set_room`, `Store.rename_room`, `Store.register_device(snapshot, room)` (tasks 2–3); `category_for`, `Category`, `CATEGORY_RANK` (task 4).
- Produces: `DeviceOut` with `room: str | None`, `category: str`, `category_rank: int`; `DevicePatch(label: str | None, room: str | None)`; `RoomRename(from_room, to_room)` with aliases `from`/`to`; routes `PATCH /api/devices/{id}` (extended), `POST /api/rooms/rename` (new), `POST /api/devices/commission` (extended).

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_devices.py`:

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
    """`""` means "remove room", `null`/omitted means
    "unchanged" - the same principle as with `SignalPatch`."""
    client, store, device_id, _fake = api
    store.set_room(device_id, "Bad")
    response = await client.patch(f"/api/devices/{device_id}", json={"room": ""})
    assert response.status_code == 200
    assert response.json()["room"] is None


async def test_patching_the_room_does_not_make_the_device_pending(api):
    """The room ends up in no export template - a freshly exported
    device must not become pending again through a room assignment
    (design 3.3).

    The export beforehand is necessary so the starting state is unambiguous:
    a device never exported always counts as pending, and there
    this test's claim would not be observable.

    The counter-check - a rename MUST leave the device pending -
    already exists in `tests/api/test_export_api.py` (the test around
    line 280, "renaming … must be reported by `GET /api/export/status`")
    and is not written a second time here. It is the reason
    this test cannot pass simply because `updated_at`
    accidentally stops being set at all.

    `GET /api/export/status` responds with a LIST, not an
    object (`-> list[ExportStatusOut]`, `api/export.py:362`)."""
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

On the last test: check under which name `FakeMatterClient` in `tests/api/conftest.py` accepts the snapshot to return, and use it here — the existing commissioning tests in the same file already show the way.

Create `tests/api/test_rooms.py` new (prepend the GPL header, rebuild the `api` fixture from `test_devices.py`, or — if it has meanwhile moved into `tests/api/conftest.py` — take it from there):

```python
async def test_renaming_a_room_moves_every_device(api):
    client, store, device_id, _fake = api
    store.set_room(device_id, "Küche")
    response = await client.post("/api/rooms/rename", json={"from": "Küche", "to": "Essbereich"})
    assert response.status_code == 200
    assert response.json() == {"renamed": 1}
    assert store.device(device_id).room == "Essbereich"


async def test_renaming_an_unknown_room_is_a_404(api):
    """Analogous to `GET /devices/{id}` for a removed device: what isn't
    there doesn't silently turn into a success with zero changes -
    otherwise a typo in the source name would look like a successful operation."""
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
Expected: FAIL — `KeyError: 'room'` in the response, or 404 on `/api/rooms/rename` (route doesn't exist).

- [ ] **Step 3: Extend the models**

In `src/loxmatter/api/models.py`, append three fields to `DeviceOut` and add a paragraph to the docstring:

```python
    id: int
    node_id: int
    label: str
    online: bool
    signal_count: int
    exportable_count: int
    next_export_count: int
    # Room and category (devices tab design, 2026-09-05). `room` is the
    # freely chosen name, `None` means "no room". `category` is the
    # identifier from `profiles.categories.Category` (`socket`, `light`, …),
    # NOT the translated name - the UI sets that itself via
    # `t("web.devices.category." + category)`, so a search for
    # "Steckdose" or "socket" matches in whichever language is displayed.
    # `category_rank` comes from the same source as the category, instead
    # of carrying the order a second time in JavaScript.
    room: str | None
    category: str
    category_rank: int
```

`DeviceRename` becomes `DevicePatch`:

```python
class DevicePatch(BaseModel):
    """`PATCH /api/devices/{device_id}` - label and room, nothing else.

    Was called `DeviceRename` until the devices tab design and could only
    handle the label; the name moves along with the capability. Neither
    `node_id` nor `id` belong here, for the same reason as with
    `SignalPatch`: what the model doesn't know, a route cannot
    accidentally accept (Pydantic v2 drops unknown fields via
    `extra="ignore"`).

    `None` means "unchanged" - for BOTH fields, as with `SignalPatch`.
    For the room this therefore needs a second way to REMOVE it:
    that is the empty string `""`, which `Store.set_room` turns into
    `NULL` via `_normalized_room`. A name made of pure whitespace goes
    the same way - it has the same unambiguous meaning and is therefore
    not worth a 422."""

    model_config = ConfigDict(frozen=True)

    label: str | None = None
    room: str | None = None


class RoomRename(BaseModel):
    """`POST /api/rooms/rename`.

    The fields are named `from_room`/`to_room` internally, because `from`
    is a Python keyword; on the outside they carry the short names that
    appear in the JSON, via `alias`. `populate_by_name` allows both,
    so a test can also build the model directly with the Python names."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    from_room: str = Field(alias="from")
    to_room: str = Field(alias="to")
```

Import `Field` from `pydantic`, if not already present.

`CommissionRequest` gets a field and a docstring paragraph:

```python
    code: str
    thread_dataset: str | None = None
    # Room (devices tab design, 2026-09-05, section 6.7): optional, because
    # a device with no room chosen ends up under "no room" and can be
    # assigned afterward at any time. If commissioning fails, no
    # device is created and thus no room either.
    room: str | None = None
```

- [ ] **Step 4: Adjust the routes**

In `src/loxmatter/api/devices.py`: replace the `DeviceRename` import with `DevicePatch` and add `RoomRename`; plus `from loxmatter.profiles.categories import CATEGORY_RANK, category_for`.

Extend `_device_out`:

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

The PATCH route:

```python
    @router.patch("/devices/{device_id}")
    async def patch_device(device_id: int, patch: DevicePatch) -> DeviceOut:
        """Changes label and/or room. For both fields, `None` means
        "unchanged"; the empty string in the room means "remove".

        The two write paths are deliberately different: `rename_device`
        also sets `updated_at` (the label gets exported as `Title`),
        `set_room` does not (the room is exported nowhere). See the
        docstrings of both store methods."""
        device = _require_device(device_id)
        if patch.label is not None:
            store.rename_device(device.id, patch.label)
        if patch.room is not None:
            store.set_room(device.id, patch.room)
        return _device_out(store.device(device.id), store, runtime)
```

The new route, directly below it:

```python
@router.post("/rooms/rename")
async def rename_room(patch: RoomRename) -> dict[str, int]:
    """Renames a room on all active devices.

    The only route that exists for rooms at all - there are no
    room objects (design 3.2), so also no `GET /api/rooms`: the
    room list already lives in `GET /api/devices`, and a second
    endpoint for the same information could only end up diverging.

    404 instead of "0 renamed" when no active device carries the source
    name: otherwise a typo in the source name would look like a
    successful operation."""
    if not patch.to_room.strip():
        raise HTTPException(status_code=422, detail=i18n.t("api.devices.room_name_required"))
    renamed = store.rename_room(patch.from_room, patch.to_room)
    if renamed == 0:
        raise HTTPException(
            status_code=404,
            detail=i18n.t("api.devices.unknown_room", room=patch.from_room),
        )
    return {"renamed": renamed}
```

Pass the room through in the commission route:

```python
        device_id = store.register_device(snapshot, room=request.room)
```

- [ ] **Step 5: Add the translation key**

In `strings.yaml`, in the `api.devices.*` block (`api.devices.room_name_required` has already been there since task 2):

```yaml
api.devices.unknown_room:
  en: "No device is assigned to the room “{room}”."
  de: "Dem Raum „{room}“ ist kein Gerät zugeordnet."
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/api -q && uv run mypy src && uv run ruff check .`
Expected: PASS. If an existing test on `DeviceRename` fails, that's exactly the intended rename hit — switch the test to `DevicePatch`, don't rename the class back.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/api tests/api/test_devices.py tests/api/test_rooms.py src/loxmatter/i18n/strings.yaml
git commit -m "$(cat <<'EOF'
feat(api): room and category on the device, a route to rename a room

`DeviceRename` is now called `DevicePatch` - the name moved along with
the capability. `None` means "unchanged" for both fields, the empty
string in the room means "remove".

`DeviceOut` carries the category as an identifier, not as a translated
name: the UI sets that itself, so search matches in the displayed
language. `category_rank` comes from the same source, instead of
carrying the order a second time in JavaScript.

No `GET /api/rooms`: the room list already lives in `GET /api/devices`,
a second endpoint for the same information could only end up diverging.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Translation keys for the UI

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml` (`web.devices.*` block starting at line 611)
- Test: `tests/test_i18n.py` (existing completeness check, no new test file)

**Interfaces:**
- Consumes: nothing.
- Produces: the keys tasks 7 and 8 use in `app.js` and `index.html`. **No key may carry a `{placeholder}` that doesn't resolve server-side** — see Global Constraints.

- [ ] **Step 1: Create the keys**

Append to the `web.devices.*` block in `strings.yaml`:

```yaml
# --- Rooms and search (devices tab design, 2026-09-05) ---
# The category names below are the only place a category
# gets its readable name - `profiles/categories.py` only knows the
# identifier. Search compares against exactly these texts, which is why
# "Steckdose" in German and "socket" in English find the same devices.
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
web.devices.room_rename_merge_confirm:
  en: "A room with that name already exists. Both rooms will be merged — this cannot be undone. Continue?"
  de: "Ein Raum dieses Namens besteht bereits. Beide Räume werden zusammengeführt — das lässt sich nicht rückgängig machen. Fortfahren?"
web.devices.room_save_error:
  en: "Could not save room: {message}"
  de: "Raum konnte nicht gespeichert werden: {message}"
web.devices.room_rename_error:
  en: "Could not rename room: {message}"
  de: "Raum konnte nicht umbenannt werden: {message}"
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

**Why the error keys carry a `{message}`:** `api/language.py:_web_strings()` delivers `web.*` keys via `i18n.raw_template()` unresolved (`language.py:56-63`) — meant exactly for `t(key, {…})` in `app.js` to fill them. The neighboring keys `web.devices.label_save_error` and `web.devices.remove_error` are already built this way. A second construction (text with a colon, message appended by concatenation) would be exactly the kind of drift this same file has already eliminated elsewhere once.

- [ ] **Step 2: Check completeness and loadability**

Run: `uv run pytest tests/test_i18n.py tests/api/test_language.py -q`
Expected: PASS — in particular the test that fetches `GET /api/i18n` without a session: it breaks as soon as a `web.*` key carries a placeholder that cannot be filled server-side.

- [ ] **Step 3: Commit**

```bash
git add src/loxmatter/i18n/strings.yaml
git commit -m "$(cat <<'EOF'
feat(i18n): keys for rooms, categories, and device search

The eight category names are the only place a category gets its
readable name - `profiles/categories.py` only knows the
identifier. Search compares against exactly these texts, which is why
"Steckdose" in German and "socket" in English find the same devices.

The new error messages deliberately carry no placeholder: `GET
/api/i18n` resolves every web.* key without values, a placeholder
there throws KeyError and takes down the whole response.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `app.js` — rooms, filter, search, sorting, primary signal

**Files:**
- Modify: `src/loxmatter/web/app.js` (state starting at line 340, helpers starting at line 874, `saveLabel` line 927, `commissionDevice` line 1063)
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `DeviceOut.room/category/category_rank` and the routes from task 5; the keys from task 6.
- Produces: the Alpine methods task 8 calls in the markup — `roomKeyOf(device)`, `roomChips()`, `hasAnyRoom()`, `matchesSearch(device)`, `visibleDevices()`, `hitsOutsideRoom()`, `clearRoomFilter()`, `deviceGroups()`, `categoryLabel(device)`, `leadSignalFor(deviceId)`, `restSignalsFor(deviceId)`, `saveRoom(device, value)`, `beginNewRoom(device)`, `commitNewRoom(device)`, `beginRenameRoom(room)`, `commitRenameRoom()`, `cancelRenameRoom()` — and the state fields `roomFilter` (`null` = all, `""` = no room, otherwise the room name), `deviceSearch`, `newRoomFor`, `newRoomDraft`, `commissionRoom`, `commissionNewRoom`.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_web.py`:

```python
async def test_the_script_offers_room_filtering_grouping_and_search(api):
    """The UI is not checked by a JS test runner (there isn't
    one - Alpine runs vendored in the browser). This check
    therefore only records THAT the building blocks are shipped that the
    markup in index.html relies on - a rename on one side without
    the other shows up here."""
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
        "beginRenameRoom(",
        "commitRenameRoom(",
        "hitsOutsideRoom(",
        "clearRoomFilter(",
    ):
        assert name in script, name


async def test_the_search_never_reaches_the_server(api):
    """Search runs over the device list already loaded anyway - there is
    no endpoint for it, and none should be created either."""
    script = (await api.get("/app.js")).text
    assert "/api/devices/search" not in script
    assert "/api/rooms/rename" in script
```

`api` here is the fixture from `tests/api/test_web.py`; check its return value (in this file it's a single client, not the four-tuple from `test_devices.py`) and write the calls accordingly.

**What these tests deliberately do NOT check:** that `saveRoom` sends no `label` along when saving a room. A text comparison in the shipped script could only guess at that, and the question is confirmed on the server side anyway — `test_patching_only_the_room_leaves_the_label_alone` and `test_patching_the_room_does_not_make_the_device_pending` from task 5 check exactly this behavior, rather than how it's written.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web.py -k "room or search" -v`
Expected: FAIL — `assert "roomChips(" in script`.

- [ ] **Step 3: Add state**

In `app.js`, in the state object next to `labelDrafts` (line ~346):

```javascript
    labelDrafts: {},
    deviceActionError: null,

    // --- Rooms, filter, search (devices tab design, 2026-09-05) -----------
    //
    // THREE states, not two, and the difference between the last two
    // is the reason for the encoding:
    //   null  = "all"
    //   ""    = "no room" (devices whose `device.room` is NULL)
    //   "Bad" = this one room
    // "No room" is a real selection and has to stay distinguishable from
    // "all" - using `null` for both would have been the obvious and
    // wrong path, because `device.room` itself is `null`. The
    // empty string cannot collide with any real room: `set_room` trims
    // and turns an empty name into NULL, so a room named "" can
    // never come into existence. It is also exactly the value the API
    // expects for "remove room" - the same encoding on both
    // sides, not two.
    //
    // Deliberately NOT in localStorage: a remembered filter would otherwise create
    // the moment where, after two weeks, three of twelve devices show up and
    // nobody remembers why anymore. After a reload the view is
    // back to "all".
    roomFilter: null,
    deviceSearch: "",
    // Which tile is currently showing a text field for a new room name
    // (device ID or null) - the state hangs off the tile, not
    // global, so two open tiles don't close each other."
    newRoomFor: null,
    newRoomDraft: "",
    // Which room is currently being renamed inline (room name or null).
    renamingRoom: null,
    renameDraft: "",
```

And in the commissioning block next to `commissionThreadDataset`:

```javascript
    commissionRoom: "",
    commissionNewRoom: "",
```

- [ ] **Step 4: Add helpers**

Insert directly after `remainingSignalCount` (line ~890):

```javascript
    // --- Category, rooms, sorting ------------------------------------------

    // The category's translated name. The API only delivers the
    // identifier ("socket"), so the search below can compare against the
    // text the user actually sees - "Steckdose" in German, "socket" in
    // English.
    categoryLabel(device) {
      return t("web.devices.category." + (device.category || "other"));
    },

    // A device's room in `roomFilter`'s encoding: "" instead of
    // null/undefined. One spot, so the conversion doesn't sit
    // separately in four helpers with one of them eventually doing it differently.
    roomKeyOf(device) {
      return device.room || "";
    },

    // All rooms with their device count, "no room" right at the end.
    // `key` is the value `roomFilter` takes on ("" for no room),
    // `label` the displayed text.
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

    // The bar doesn't show at all as long as not a single device carries a
    // room: with three devices and no room, it would be a line of
    // noise above a list that fits in one glance anyway.
    hasAnyRoom() {
      return this.devices.some((device) => Boolean(device.room));
    },

    // Does the search term match this device? Compared against name,
    // translated category name, and room name.
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

    // The visible devices: room chip and search field act TOGETHER (AND).
    // A search only applies within the selected room then - the case "no
    // hit here, but next door" is caught by `hitsOutsideRoom()` below.
    visibleDevices() {
      return this.devices.filter(
        (device) =>
          (this.roomFilter === null || this.roomKeyOf(device) === this.roomFilter) &&
          this.matchesSearch(device),
      );
    },

    // How many devices the search term matches OUTSIDE the selected room.
    // Only relevant when nothing is left within the room itself -
    // otherwise the note would be a distraction.
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

    // The devices, grouped by room and sorted within a room:
    // first by category rank (all plugs together, then all
    // buttons), within that alphabetically by name.
    //
    // `localeCompare` instead of `<`: otherwise "Ärmelkanal" would land after
    // "Zaun", because the code point of "Ä" sits after that of "Z".
    //
    // With a room selected, exactly one group results, and its
    // `title` stays empty - there is nothing to distinguish, and a
    // heading above the single group would duplicate the chip bar.
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
      // With a room selected there is only one group - its
      // heading would duplicate the active chip directly above it.
      if (this.roomFilter !== null) {
        return groups.map((group) => ({ ...group, title: "" }));
      }
      return groups;
    },

    // --- Primary signal (tile header) --------------------------------------

    // The first functional signal in the order `firstSignalsFor`
    // delivers anyway - that is, the profile table's order.
    // Plug -> state, climate sensor -> temperature, cover -> position.
    // No data storage of its own, no configuration: a device with no
    // functional signals simply has no primary signal, and the header
    // stays one line.
    leadSignalFor(deviceId) {
      return this.firstSignalsFor(deviceId)[0] || null;
    },

    // The rest of the short list. `FUNCTIONAL_PREVIEW_LIMIT` counts the
    // primary signal IN (design 6.2), so no second cutoff here -
    // `firstSignalsFor` has already done it.
    restSignalsFor(deviceId) {
      return this.firstSignalsFor(deviceId).slice(1);
    },

    // --- Change a device's room ---------------------------------------

    // Sends EXCLUSIVELY the room. A `label` sent along would make
    // `rename_device` run and set `updated_at` - the device would
    // afterward show as "changed since export", even though the room lands in no
    // template (design 3.3).
    //
    // `value` is already in the same encoding as `roomFilter`: "" means
    // "no room", and that's exactly what the API expects for "remove
    // room" too. No conversion at this spot.
    async saveRoom(device, value) {
      this.deviceActionError = null;
      try {
        const updated = await this.request("PATCH", `/api/devices/${device.id}`, {
          room: value,
        });
        Object.assign(device, updated);
      } catch (error) {
        this.deviceActionError = t("web.devices.room_save_error", { message: error.message });
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

    // Renaming happens INLINE, like every other edit in this
    // UI (device name, signal title): the pencil turns the
    // heading into an input field. A `window.prompt` would have been less
    // markup, but would look different in every browser and would be the
    // only dialog in a view that otherwise gets by without one.
    beginRenameRoom(room) {
      this.renamingRoom = room;
      this.renameDraft = room;
    },

    cancelRenameRoom() {
      this.renamingRoom = null;
      this.renameDraft = "";
    },

    // The confirmation prompt before merging, by contrast, stays a native
    // dialog - the one deliberate difference from renaming itself.
    // Merging is rare and irreversible: afterward nobody
    // knows anymore which device used to be in which of the two rooms. A
    // modal dialog is the honest brake for exactly this kind of action;
    // a banner that can be dismissed without reading it
    // would not be.
    //
    // Whether the target name is already taken is decided by the UI and
    // not the server: it already has the device list, a second
    // query just for this information would be superfluous.
    async commitRenameRoom() {
      const room = this.renamingRoom;
      if (room === null) {
        // Enter has already saved and closed the field; the
        // subsequent `blur` lands here and has nothing left to do.
        return;
      }
      const name = this.renameDraft.trim();
      if (!name || name === room) {
        this.cancelRenameRoom();
        return;
      }
      const exists = this.devices.some((device) => device.room === name);
      if (exists && !window.confirm(t("web.devices.room_rename_merge_confirm"))) {
        // The field stays open: declining the confirmation prompt means "not like this",
        // not "forget what I typed".
        return;
      }
      this.cancelRenameRoom();
      this.deviceActionError = null;
      try {
        await this.request("POST", "/api/rooms/rename", { from: room, to: name });
        if (this.roomFilter === room) {
          this.roomFilter = name;
        }
        await this.loadDevices();
      } catch (error) {
        this.deviceActionError = t("web.devices.room_rename_error", { message: error.message });
      }
    },
```

- [ ] **Step 5: Extend commissioning with the room**

In `commissionDevice()`, extend the body of `body` and adjust the reset:

```javascript
        const body = { code: this.commissionCode.trim() };
        if (this.commissionThreadDataset.trim()) {
          body.thread_dataset = this.commissionThreadDataset.trim();
        }
        // Room (design 6.7): "" means "no room" and isn't sent along
        // at all; "__new__" is the select field's special value, behind
        // which the text field `commissionNewRoom` sits.
        const room =
          this.commissionRoom === "__new__"
            ? this.commissionNewRoom.trim()
            : this.commissionRoom.trim();
        if (room) {
          body.room = room;
        }
```

and further below, when clearing the fields:

```javascript
        this.commissionCode = "";
        this.commissionThreadDataset = "";
        // The room DELIBERATELY stays as is (design 6.7): someone commissioning four
        // devices in the kitchen chooses it once. A pairing code, by contrast,
        // is worthless after use, and a leftover one would be a
        // source of errors.
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_web.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/web/app.js tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): room filter, grouping, category sorting, and search

Room chip and search field act together (AND) - so search only applies
within the selected room. The case this creates ("no hit", even though the
device is right next door) is caught by `hitsOutsideRoom()`, which offers
a jump to "all" without losing the search term.

`saveRoom` sends exclusively the room. A label sent along would
make `rename_device` run and mark the device as "changed
since export", even though the room lands in no template.

The filter state is not saved: a remembered filter would otherwise
create the moment where, after two weeks, three of twelve devices show
up and nobody remembers why anymore.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: `index.html` and `style.css` — grid, tile, room bar, icons

**Files:**
- Modify: `src/loxmatter/web/index.html` (icon block line 64-86, devices view line 176-340)
- Modify: `src/loxmatter/web/style.css` (`.device-card` line 632-658, `.value-chips` line 660-672, new rules)
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: all methods from task 7, all keys from task 6.
- Produces: none for later tasks.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_web.py`:

```python
async def test_the_page_offers_the_room_bar_and_the_room_picker(api):
    page = (await api.get("/")).text
    assert "roomChips()" in page
    assert "deviceGroups()" in page
    assert "leadSignalFor(" in page
    assert "saveRoom(" in page
    assert "deviceSearch" in page


async def test_every_category_has_an_icon_symbol(api):
    """Eight categories, eight symbols - "other" included. A missing
    symbol does NOT show up in the browser: a `<use>` on an unknown ID
    silently draws nothing, no error. That's why it shows up
    here."""
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

Also: the existing test `test_the_icons_are_well_formed_xml` (line 141) has to cover the new symbols too — check whether it parses the entire inline block; if it does, nothing to do beyond writing well-formed SVG.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web.py -k "room_bar or icon or grid" -v`
Expected: FAIL — `assert "roomChips()" in page`.

- [ ] **Step 3: Add the icons**

In `index.html`, into the existing `<svg style="display: none">` block, seven symbols (line icons in the style of the existing ones, `viewBox="0 0 24 24"`, no fill — `.icon` in `style.css` sets `stroke: currentColor; fill: none`):

```html
      <!-- Category icons (devices tab design, 2026-09-05, section 6.5).
           One symbol per category, "other" included - that's also where
           every device whose types haven't been backfilled yet ends up.
           Still inline and without an icon library, for the same
           reason as the checked-in vendor/alpine.min.js: the UI
           runs offline. -->
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
      <!-- EIGHT symbols, not seven: the tile stubbornly maps the
           identifier onto `#i-cat-<identifier>`, and `other` is an
           identifier like any other. A `<use>` on a nonexistent ID draws
           SILENTLY nothing - no console error, just a
           tile with no icon. That's why "other" gets its own symbol,
           instead of relying on special handling in JavaScript
           that someone overlooks at the next rework. The shape is the same
           as `#i-device`'s. -->
      <symbol id="i-cat-other" viewBox="0 0 24 24">
        <rect x="4" y="4" width="16" height="16" rx="3" />
        <circle cx="12" cy="12" r="2.2" />
      </symbol>
```

`#i-device` thus loses its only user (the old tile header, `index.html:220`). It stays in place for now; task 9 checks whether it is still referenced anywhere and removes it otherwise.

- [ ] **Step 4: Rework the devices view**

In `index.html`, extend the commissioning card with the room field (into the existing `.row`, after the Thread field):

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

and below it, next to the existing `<p class="hint">`:

```html
          <p class="hint" x-text="t('web.devices.commission_room_hint')"></p>
```

The room bar, right before the device list (after the error banners):

```html
        <!-- Room bar (design 6.3): doesn't show at all as long as not
             a single device carries a room - with three devices and no
             room it would be a line of noise above a list that fits in
             one glance anyway. The search field stays reachable in that case
             regardless, because it also searches by name and category
             without any rooms. -->
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
              <!-- `x-show="roomFilter"` is exactly right here and not
                   sloppiness: both cases where there's nothing to
                   rename are falsy - `null` ("all") and `""` ("no
                   room"). "No room" is not a room but the set of
                   unassigned devices; a name you could change
                   is precisely what they're missing. -->
              <button
                class="room-rename"
                x-show="roomFilter && renamingRoom !== roomFilter"
                @click="beginRenameRoom(roomFilter)"
                :title="t('web.devices.room_rename')"
                x-text="'✎'"
              ></button>
              <input
                x-show="roomFilter && renamingRoom === roomFilter"
                x-cloak
                type="text"
                class="room-rename-input"
                x-model="renameDraft"
                @keydown.enter="commitRenameRoom()"
                @keydown.escape="cancelRenameRoom()"
                @blur="commitRenameRoom()"
              />
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

The device list: the existing `<template x-for="device in devices">` becomes two nested loops — the room groups on the outside, the grid on the inside:

```html
        <template x-for="group in deviceGroups()" :key="group.key">
          <div>
            <!-- Renaming inline, like the device name in the tile: the
                 pencil swaps the heading for an input field.
                 `group.key` is "" for "no room" - falsy, so no
                 pencil: there's no name to change there. -->
            <h3 class="room-heading" x-show="group.title">
              <span x-show="renamingRoom !== group.key" x-text="group.title"></span>
              <button
                class="room-rename"
                x-show="group.key && renamingRoom !== group.key"
                @click="beginRenameRoom(group.key)"
                :title="t('web.devices.room_rename')"
                x-text="'✎'"
              ></button>
              <input
                x-show="group.key && renamingRoom === group.key"
                x-cloak
                type="text"
                class="room-rename-input"
                x-model="renameDraft"
                @keydown.enter="commitRenameRoom()"
                @keydown.escape="cancelRenameRoom()"
                @blur="commitRenameRoom()"
              />
            </h3>
            <div class="device-grid">
              <template x-for="device in group.devices" :key="device.id">
                <!-- the tile, see below -->
              </template>
            </div>
          </div>
        </template>
```

The tile itself (replaces the previous content of `.device-card`) — header with primary signal, value grid, control bar, footer with room selection:

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
                      <!-- If a status pill is due, it displaces the
                           primary-signal label (design 6.2): the tile's
                           state weighs more than the label of a
                           number sitting two centimeters away. -->
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
                    <!-- The note about the remaining signals is the last
                         grid row instead of its own paragraph (design
                         6.2) - on a 260 px wide tile, every row
                         counts. -->
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

- [ ] **Step 5: Add to `style.css`**

Append to the end of `style.css` (the existing `.device-card` rules stay, `.value-chips` is superseded by `.value-rows` — delete the old rule only once no markup uses it anymore: `.projectsync-unchanged-chips .value-chip` on line 905 still does, so `.value-chip` stays):

```css
/* Devices tab: room bar, grid, tile (design 2026-09-05).
 *
 * The grid is the actual point: until now a tile was as wide
 * as the window and as tall as its content - twelve devices were twelve
 * screen heights. `auto-fill` with a lower bound instead of its own
 * breakpoints: 260 px is the width above which the header along with the primary
 * signal and the value column no longer wrap. This yields four columns
 * on desktop, two on tablet, one on phone, without a
 * media query defining the numbers a second time. */
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

/* The input field for renaming takes on the size of the heading it
 * replaces - otherwise the line jumps when the pencil is clicked. */
.room-rename-input {
  font-size: 0.75rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 0.05rem 0.3rem;
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

/* Header: name on the left, primary signal on the right, both on one
 * visual axis. `min-width: 0` on the middle, so a long device name
 * truncates instead of pushing the primary signal out of the tile - without
 * this line, in a flex element the content wins against any width
 * setting. */
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

/* Value grid instead of chips: values align in one column across all
 * tiles, instead of meandering as chips of varying width - you
 * scan one column instead of twelve pieces. */
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
Expected: PASS. Existing tests that check for markup that's gone (such as `web.devices.values_heading` or `web.devices.controls_heading`, whose headings drop out of the compact tile) fail here — they need to be adjusted along with the markup, and the resulting unused keys removed from `strings.yaml`.

- [ ] **Step 7: Look at it in the running UI**

```bash
uv run loxmatter run --miniserver 192.168.1.10
```

(The address is that of your own Miniserver — the same line is in `README.md:198`. `loxmatter run` prints the UI's address at startup; open that in the browser and switch to the "Devices" view.)

Check five things no test covers:

1. Wide window → four columns, narrow → one, with no tile scrolling horizontally.
2. The room bar only appears once at least one device has a room.
3. "+ New room …" in the footer reveals the text field, Enter saves.
4. A selected room chip shows the pencil, "all" and "no room" don't show it.
5. A search within the selected room with no hits offers "*n* further matches in other rooms", and the link keeps the search term.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): multi-column tile grid, room bar, and category icons

The grid is the actual point: a tile used to be as wide as the
window and as tall as its content - twelve devices were twelve
screen heights. `auto-fill` with a 260 px lower bound instead of its own
breakpoints; 260 px is the width above which the header along with the
primary signal and value column no longer wrap.

The tile keeps its entire content (no collapsing, as promised in the
dashboard design) and only reorders it: primary signal in the
header, values as an aligned grid, room selection in the footer. If
a status pill is due, it displaces the primary-signal label - the state
weighs more than the label of a number sitting next to it.

Eight category symbols, "other" included: the tile stubbornly maps
the identifier onto `#i-cat-<identifier>`, and a `<use>` on an
unknown ID silently draws nothing. This closes open point 1
of the dashboard design.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Wrap-up — full suite, linting, documentation

**Files:**
- Modify: `README.md` (the section on the UI, if it describes the devices view)
- Modify: `docs/superpowers/specs/2026-09-03-device-dashboard-and-export-design.md` (open point 1)

**Interfaces:**
- Consumes: everything.
- Produces: nothing.

- [ ] **Step 1: Full suite and linting**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: PASS, no messages. Every failure gets fixed, not suppressed.

- [ ] **Step 2: Remove unused translation keys**

Check which `web.devices.*` keys have become unused through the tile rework in task 8:

```bash
for key in $(grep -o '^web\.devices\.[a-z_.]*' src/loxmatter/i18n/strings.yaml | tr -d ':'); do
  grep -q "$key" src/loxmatter/web/index.html src/loxmatter/web/app.js || echo "UNUSED: $key"
done
```

Check every reported key and remove it if truly nothing uses it anymore — a key with no hit is text nobody sees translated anymore and that costs time at the next language review.

The same question for the old symbol `#i-device`, whose only user was the old tile header:

```bash
grep -n "i-device" src/loxmatter/web/index.html src/loxmatter/web/app.js
```

If only the definition itself is left (`<symbol id="i-device">`), remove it — `#i-cat-other` has taken over its job.

- [ ] **Step 3: Close the predecessor spec's open point**

In `docs/superpowers/specs/2026-09-03-device-dashboard-and-export-design.md`, section "Open points", add a sentence to point 1:

```markdown
   **Done** via the [devices tab design from September 5, 2026](2026-09-05-devices-tab-rooms-and-tile-grid-design.md):
   the mapping is `profiles/categories.py`, and it delivers not just the
   icon, but also the sorting within a room and the
   search term.
```

- [ ] **Step 4: Check the README**

Look through `README.md` for descriptions of the devices view (`grep -n -i "geräte\|devices" README.md`). If it describes the list as single-column or mentions no rooms, adjust the paragraph — brief, in the tone of the surrounding text, in whatever language the README currently is in.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
docs: document the devices tab and close the open icon point

The dashboard design left the mapping device type -> icon open. It
is now `profiles/categories.py`, and it carries three things at once
there: icon, sorting within a room, and search term.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```
