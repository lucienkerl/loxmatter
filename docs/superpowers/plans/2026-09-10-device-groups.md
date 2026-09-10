# Device Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let several devices of the same category be driven by one Loxone virtual output, with the bridge fanning the command out to each member individually.

**Architecture:** A group is a stored entity with a label, room, category and members, but no node. Its commands are the intersection of its members' commands, stored under keys in the *same namespace* as device command keys (`g{id}_{slug}` beside `d{id}_{ep}_{slug}`), so `/cmd/{key}/{value}`, the export and the project sync treat a group output as an ordinary output. Dispatch fans out concurrently across members and sequentially within one member.

**Tech Stack:** Python 3.12, FastAPI, SQLite (stdlib `sqlite3`), Pydantic v2, pytest + `httpx2`, Alpine.js (vendored) for the WebUI, `uv` for everything.

**Design document:** `docs/superpowers/specs/2026-09-10-device-groups-design.md`. Read it before Task 1; the section numbers referenced below are its.

**Correction, made during execution:** the tests below originally used the
slug `hue_saturation` for ColorControl command 6. The real slug in
`profiles/clusters.yaml` is **`color`** (its control type is `hue_sat`);
command 10 is `colortemp` (control `kelvin`), and LevelControl carries both
`level` (8/0) and `level_onoff` (8/4). Confirmed by calling
`profiles.table.command_slug` directly rather than reading the YAML.

## Global Constraints

- **English everywhere** — code, comments, docstrings, test names, commit messages. The only German that may be written is a `de:` value in `src/loxmatter/i18n/strings.yaml`.
- **Every user-visible string** (WebUI text, `HTTPException` detail) goes through `i18n.t(...)` with BOTH an `en:` and a `de:` value in `src/loxmatter/i18n/strings.yaml`. Never hardcode.
- **Every new source file** starts with the 15-line GPL header used by every existing file under `src/` and `tests/` — copy it verbatim from `src/loxmatter/api/control.py`.
- **Commit messages** follow Conventional Commits, e.g. `feat(store): add device groups`. Say what changed and why.
- **The checks CI runs**, all five, from the repository root:
  ```bash
  uv run ruff check .
  uv run ruff format --check .
  uv run mypy
  uv run pytest -v
  uv run python scripts/check_language.py
  ```
- **The full pytest suite takes about 8 minutes.** That is normal. Do not kill it thinking it hung. While iterating on one task, run only that task's test file.
- **Keys are opaque** (Main Spec 6.2). Never parse a key to recover the endpoint or the ID; read the stored fields.

## File Structure

**Created:**

| File | Responsibility |
| --- | --- |
| `src/loxmatter/commands/fanout.py` | Turning one group command plus its targets into per-member call plans, and dispatching them concurrently. No HTTP, no store. |
| `src/loxmatter/api/groups.py` | The `/api/groups` CRUD router and the group controls route. |
| `tests/model/test_store_groups.py` | Group rows, membership, intersection, key stability. |
| `tests/commands/test_fanout.py` | Ordering, concurrency, failure collection. |
| `tests/api/test_groups.py` | The CRUD router and controls route. |
| `tests/api/test_group_control.py` | `POST /api/commands/{key}` and `/cmd/{key}/{value}` against a group. |
| `tests/export/test_group_outputs.py` | Group templates and the pairing key. |
| `tests/projectsync/test_group_sync.py` | Group containers in the plan and the patch. |

**Modified:**

| File | Change |
| --- | --- |
| `src/loxmatter/model/store.py` | Schema v8, three tables, group CRUD, intersection, `resolve_group_command`, `group_targets`, membership cleanup in `forget_device`. |
| `src/loxmatter/loxone/server.py` | `/cmd/{key}/{value}` falls through to the group path; router wiring for `build_groups_router`. |
| `src/loxmatter/api/control.py` | `POST /api/commands/{key}` falls through to the group path. |
| `src/loxmatter/api/models.py` | `GroupOut`, `GroupIn`, `GroupMembersIn`, `GroupPatch`. |
| `src/loxmatter/export/outputs.py` | `to_outputs` takes its pairing key as a parameter; `to_group_outputs`. |
| `src/loxmatter/export/documents.py` | `filename_for` takes the ID prefix as a parameter; one shared title helper. |
| `src/loxmatter/projectsync/diff.py` | `PlanEntry.owner_kind`; group entries in `build_plan`. |
| `src/loxmatter/projectsync/patch.py` | Group containers in `apply_plan`. |
| `src/loxmatter/projectsync/sync.py` | Pass groups through. |
| `src/loxmatter/api/export.py`, `src/loxmatter/cli.py` | Group templates in both export paths. |
| `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js` | Group tiles, creation dialog, kebab entries. |
| `src/loxmatter/i18n/strings.yaml` | All new strings, `en` + `de`. |
| `CHANGELOG.md` | The feature entry. |

---

### Task 1: Store — schema, migration 8, group CRUD

**Files:**
- Modify: `src/loxmatter/model/store.py`
- Test: `tests/model/test_store_groups.py` (create)
- Test: `tests/model/test_store_migration.py` (extend)

**Interfaces:**
- Consumes: `Store`, `StoredDevice`, `UnknownDeviceError`, `_normalized_room`, `_SCHEMA`, `_SCHEMA_VERSION`, `_MIGRATIONS` — all existing in `store.py`. `loxmatter.profiles.categories.Category` and `category_for`.
- Produces:
  - `StoredGroup(id: int, label: str, room: str | None, category: str, exported_at: str | None, updated_at: str | None)`
  - `UnknownGroupError(KeyError)`, `CategoryMismatchError(ValueError)`
  - `Store.create_group(label: str, member_ids: Sequence[int], room: str | None = None) -> StoredGroup`
  - `Store.groups() -> list[StoredGroup]`
  - `Store.group(group_id: int) -> StoredGroup`
  - `Store.rename_group(group_id: int, label: str) -> None`
  - `Store.set_group_room(group_id: int, room: str | None) -> None`
  - `Store.delete_group(group_id: int) -> None`
  - `Store.group_members(group_id: int) -> list[StoredDevice]`
  - `Store.set_group_members(group_id: int, member_ids: Sequence[int]) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/model/test_store_groups.py` with the GPL header, then:

```python
"""Groups in the store - see design 2026-09-10, sections 2 and 4."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import (
    CategoryMismatchError,
    Store,
    UnknownGroupError,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite")
    yield s
    s.close()


@pytest.fixture
def lamps(store):
    """Two LIGHT devices of differing capability - the intersection case."""
    ids = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = load(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        ids.append(device_id)
    return ids


@pytest.fixture
def plug(store):
    snapshot = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    return device_id


def test_a_new_group_takes_its_category_from_its_first_member(store, lamps):
    group = store.create_group("Living room", lamps)
    assert group.category == "light"
    assert group.label == "Living room"
    assert group.room is None


def test_a_member_of_another_category_is_refused(store, lamps, plug):
    with pytest.raises(CategoryMismatchError):
        store.create_group("Mixed", [lamps[0], plug])


def test_the_category_is_still_enforced_after_the_group_ran_empty(store, lamps, plug):
    """The category is stored, not derived - an emptied group must keep
    refusing, which a derivation from the members could not do."""
    group = store.create_group("Living room", lamps)
    store.set_group_members(group.id, [])
    with pytest.raises(CategoryMismatchError):
        store.set_group_members(group.id, [plug])


def test_a_device_may_belong_to_several_groups(store, lamps):
    first = store.create_group("Living room", lamps)
    second = store.create_group("Ground floor", [lamps[0]])
    assert [d.id for d in store.group_members(first.id)] == lamps
    assert [d.id for d in store.group_members(second.id)] == [lamps[0]]


def test_room_and_label_survive_a_round_trip(store, lamps):
    group = store.create_group("Living room", lamps, room="Living room")
    store.rename_group(group.id, "Ceiling")
    store.set_group_room(group.id, "Hallway")
    reread = store.group(group.id)
    assert (reread.label, reread.room) == ("Ceiling", "Hallway")


def test_an_empty_room_name_becomes_no_room(store, lamps):
    """The same encoding as `device.room` - `_normalized_room` turns a
    blank name into NULL, so "" can never be a real room."""
    group = store.create_group("Living room", lamps, room="  ")
    assert store.group(group.id).room is None


def test_an_unknown_group_raises_without_repr_quotes(store):
    with pytest.raises(UnknownGroupError) as excinfo:
        store.group(999)
    assert not str(excinfo.value).startswith("'")


def test_a_deleted_group_is_gone(store, lamps):
    group = store.create_group("Living room", lamps)
    store.delete_group(group.id)
    with pytest.raises(UnknownGroupError):
        store.group(group.id)
    assert store.groups() == []


def test_a_group_needs_at_least_one_member_at_creation(store):
    """The first member is what fixes the category (design 2.1)."""
    with pytest.raises(ValueError):
        store.create_group("Empty", [])


def test_membership_of_a_forgotten_device_is_dropped(store, lamps):
    group = store.create_group("Living room", lamps)
    store.forget_device(lamps[1])
    assert [d.id for d in store.group_members(group.id)] == [lamps[0]]
```

And in `tests/model/test_store_migration.py` add:

```python
def test_a_v7_database_gains_the_group_tables(tmp_path):
    """`_migrate_to_v8`. The reverse direction matters too: a fresh
    database also runs the whole chain (the `_migrate_to_v5` pitfall)."""
    import sqlite3

    path = tmp_path / "legacy.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE device (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " unique_id TEXT NOT NULL, node_id INTEGER NOT NULL, label TEXT NOT NULL,"
        " udp_port INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,"
        " exported_at TEXT, updated_at TEXT, room TEXT, device_types TEXT);"
    )
    db.execute("PRAGMA user_version = 7")
    db.commit()
    db.close()

    store = Store(path)
    tables = {
        row[0]
        for row in store._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"device_group", "device_group_member", "group_command"} <= tables
    store.close()


def test_a_fresh_database_ends_at_version_eight(tmp_path):
    store = Store(tmp_path / "fresh.sqlite")
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 8
    store.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/model/test_store_groups.py -v
```

Expected: collection error, `ImportError: cannot import name 'CategoryMismatchError'`.

- [ ] **Step 3: Extend `_SCHEMA` and add the migration**

In `src/loxmatter/model/store.py`, append to the `_SCHEMA` string (before its closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS device_group (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    room        TEXT,
    category    TEXT NOT NULL,
    exported_at TEXT,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS device_group_member (
    group_id  INTEGER NOT NULL REFERENCES device_group(id),
    device_id INTEGER NOT NULL REFERENCES device(id),
    UNIQUE (group_id, device_id)
);
CREATE TABLE IF NOT EXISTS group_command (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    INTEGER NOT NULL REFERENCES device_group(id),
    cluster_id  INTEGER NOT NULL,
    command_id  INTEGER NOT NULL,
    key         TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL,
    takes_value INTEGER NOT NULL,
    UNIQUE (group_id, cluster_id, command_id)
);
```

Set `_SCHEMA_VERSION = 8`, extend the comment block above it with one sentence in the established style ("Version 8 (device groups, design 2026-09-10) adds the three tables `device_group`, `device_group_member` and `group_command`, see `_migrate_to_v8` - all three are already present in a fresh database via `_SCHEMA`, so the migration is only needed for existing databases."), and add:

```python
def _migrate_to_v8(db: sqlite3.Connection) -> None:
    """Adds the three group tables (design 2026-09-10, section 4.2).

    `CREATE TABLE IF NOT EXISTS` and not `CREATE TABLE`, for the same
    reason as in `_migrate_to_v5`: a freshly created database already has
    all three via `_SCHEMA` and is nevertheless at `PRAGMA user_version =
    0`, so it runs through this migration too. No backfill - no existing
    database has groups.
    """
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS device_group (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            label       TEXT NOT NULL,
            room        TEXT,
            category    TEXT NOT NULL,
            exported_at TEXT,
            updated_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS device_group_member (
            group_id  INTEGER NOT NULL REFERENCES device_group(id),
            device_id INTEGER NOT NULL REFERENCES device(id),
            UNIQUE (group_id, device_id)
        );
        CREATE TABLE IF NOT EXISTS group_command (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER NOT NULL REFERENCES device_group(id),
            cluster_id  INTEGER NOT NULL,
            command_id  INTEGER NOT NULL,
            key         TEXT NOT NULL UNIQUE,
            slug        TEXT NOT NULL,
            takes_value INTEGER NOT NULL,
            UNIQUE (group_id, cluster_id, command_id)
        );
        """
    )
```

and register it: `_MIGRATIONS[8] = _migrate_to_v8` (add `8: _migrate_to_v8,` to the existing dict literal).

- [ ] **Step 4: Add the dataclass, the errors and the CRUD methods**

Next to `StoredDevice`:

```python
@dataclass(frozen=True)
class StoredGroup:
    """A row from `device_group` (design 2026-09-10, section 2).

    Carries no member list and no command list: both are separate tables
    with their own lifetime, and a copy frozen in here would be stale the
    moment a member changes. `category` is the `value` of a
    `profiles.categories.Category`, stored rather than derived - an
    emptied group must still know what it accepts (design 2.1).
    """

    id: int
    label: str
    room: str | None
    category: str
    exported_at: str | None
    updated_at: str | None
```

Next to `UnknownDeviceError`:

```python
class UnknownGroupError(KeyError):
    """Like `UnknownCommandError`: `KeyError.__str__` would wrap the
    message in `repr()`, and this text becomes an HTTP 404 body."""

    def __str__(self) -> str:
        return str(self.args[0])


class CategoryMismatchError(ValueError):
    """A member whose category differs from the group's (design 2.1)."""
```

Methods on `Store`:

```python
    @staticmethod
    def _as_group(row: sqlite3.Row) -> StoredGroup:
        return StoredGroup(
            id=int(row["id"]),
            label=str(row["label"]),
            room=row["room"],
            category=str(row["category"]),
            exported_at=row["exported_at"],
            updated_at=row["updated_at"],
        )

    def _category_of(self, device_id: int) -> str:
        return category_for(self.device(device_id).device_types).value

    def _check_members(self, category: str, member_ids: Sequence[int]) -> None:
        """Every member must exist, be active and match `category`.

        Checked BEFORE anything is written, so a rejected member never
        leaves a half-applied membership behind - the same all-or-nothing
        stance as `register_commands`.
        """
        for device_id in member_ids:
            actual = self._category_of(device_id)
            if actual != category:
                raise CategoryMismatchError(
                    i18n.t(
                        "api.errors.group_category_mismatch",
                        device_id=device_id,
                        actual=actual,
                        expected=category,
                    )
                )

    def create_group(
        self, label: str, member_ids: Sequence[int], room: str | None = None
    ) -> StoredGroup:
        """The first member fixes the category, so there must be one."""
        if not member_ids:
            raise ValueError(i18n.t("api.errors.group_needs_a_member"))
        category = self._category_of(member_ids[0])
        self._check_members(category, member_ids)
        cur = self._db.execute(
            "INSERT INTO device_group (label, room, category, updated_at) VALUES (?, ?, ?, ?)",
            (label, _normalized_room(room), category, self._now()),
        )
        group_id = cur.lastrowid
        assert group_id is not None
        for device_id in member_ids:
            self._db.execute(
                "INSERT INTO device_group_member (group_id, device_id) VALUES (?, ?)",
                (int(group_id), device_id),
            )
        self._db.commit()
        return self.group(int(group_id))

    def groups(self) -> list[StoredGroup]:
        rows = self._db.execute("SELECT * FROM device_group ORDER BY id").fetchall()
        return [self._as_group(r) for r in rows]

    def group(self, group_id: int) -> StoredGroup:
        row = self._db.execute("SELECT * FROM device_group WHERE id = ?", (group_id,)).fetchone()
        if row is None:
            raise UnknownGroupError(i18n.t("api.errors.unknown_group", group_id=group_id))
        return self._as_group(row)

    def rename_group(self, group_id: int, label: str) -> None:
        self.group(group_id)
        self._db.execute(
            "UPDATE device_group SET label = ?, updated_at = ? WHERE id = ?",
            (label, self._now(), group_id),
        )
        self._db.commit()

    def set_group_room(self, group_id: int, room: str | None) -> None:
        self.group(group_id)
        self._db.execute(
            "UPDATE device_group SET room = ?, updated_at = ? WHERE id = ?",
            (_normalized_room(room), self._now(), group_id),
        )
        self._db.commit()

    def delete_group(self, group_id: int) -> None:
        self.group(group_id)
        self._db.execute("DELETE FROM group_command WHERE group_id = ?", (group_id,))
        self._db.execute("DELETE FROM device_group_member WHERE group_id = ?", (group_id,))
        self._db.execute("DELETE FROM device_group WHERE id = ?", (group_id,))
        self._db.commit()

    def group_members(self, group_id: int) -> list[StoredDevice]:
        """Active members only.

        `forget_device` already deletes the membership rows (see there),
        so this filter should never have anything to do. It is here
        anyway: a read that cannot return a removed device makes the
        correctness of that write not load-bearing.
        """
        self.group(group_id)
        rows = self._db.execute(
            "SELECT d.* FROM device_group_member m"
            " JOIN device d ON d.id = m.device_id"
            " WHERE m.group_id = ? AND d.active = 1"
            " ORDER BY d.id",
            (group_id,),
        ).fetchall()
        return [self._as_device(r) for r in rows]

    def set_group_members(self, group_id: int, member_ids: Sequence[int]) -> None:
        """Replaces the whole membership in one transaction.

        The complete list rather than add/remove: the command
        intersection is recomputed after every change anyway, and two
        single removals would recompute it twice and pass through an
        intermediate state nobody asked for - including keys that
        briefly vanish and come back (design 5).
        """
        group = self.group(group_id)
        self._check_members(group.category, member_ids)
        self._db.execute("DELETE FROM device_group_member WHERE group_id = ?", (group_id,))
        for device_id in member_ids:
            self._db.execute(
                "INSERT INTO device_group_member (group_id, device_id) VALUES (?, ?)",
                (group_id, device_id),
            )
        self._db.execute(
            "UPDATE device_group SET updated_at = ? WHERE id = ?", (self._now(), group_id)
        )
        self._db.commit()
```

Add `from loxmatter.profiles.categories import category_for` to the imports at the top of `store.py`.

Extend `forget_device` so the membership goes with the device:

```python
    def forget_device(self, device_id: int) -> None:
        """Marks a device as removed. The id stays assigned (Spec 6.2).

        Its group memberships do NOT stay: `register_device` matches on
        `unique_id AND active = 1`, so recommissioning the same physical
        device produces a NEW row with a new id - the old membership
        could therefore never come back to life, and would only sit
        around pointing at a device nobody can reach.
        """
        self._db.execute("DELETE FROM device_group_member WHERE device_id = ?", (device_id,))
        self._db.execute("UPDATE device SET active = 0 WHERE id = ?", (device_id,))
        self._db.commit()
```

- [ ] **Step 5: Add the four new strings**

In `src/loxmatter/i18n/strings.yaml`, next to the other `api.errors.*` entries:

```yaml
api.errors.unknown_group:
  en: "unknown group {group_id}"
  de: "unbekannte Gruppe {group_id}"
api.errors.group_needs_a_member:
  en: "a new group needs at least one member; it determines the category"
  de: "eine neue Gruppe braucht mindestens ein Mitglied, es bestimmt die Kategorie"
api.errors.group_category_mismatch:
  en: "device {device_id} is a {actual}, the group takes {expected}"
  de: "Gerät {device_id} ist ein {actual}, die Gruppe nimmt {expected}"
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run pytest tests/model/test_store_groups.py tests/model/test_store_migration.py -v
```

Expected: PASS, all of them.

- [ ] **Step 7: Run the full check set and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
```

```bash
git add src/loxmatter/model/store.py src/loxmatter/i18n/strings.yaml tests/model/test_store_groups.py tests/model/test_store_migration.py
git commit -m "feat(store): add device groups with schema version 8

A group carries a label, room, category and members, but no node. The
category is stored rather than derived from the members, so a group that
has run empty still refuses a member of the wrong kind.

forget_device now drops the membership rows: register_device matches on
unique_id AND active = 1, so a recommissioned device gets a new id and
the old membership could never come back to life."
```

---

### Task 2: Store — the command intersection

**Files:**
- Modify: `src/loxmatter/model/store.py`
- Test: `tests/model/test_store_groups.py` (extend)

**Interfaces:**
- Consumes: `StoredGroup`, `Store.group_members`, `Store.commands` (existing), `StoredCommand` (existing).
- Produces:
  - `StoredGroupCommand(key: str, slug: str, group_id: int, cluster_id: int, command_id: int, takes_value: bool)`
  - `Store.group_commands(group_id: int) -> list[StoredGroupCommand]`
  - `Store.resolve_group_command(key: str) -> StoredGroupCommand`
  - `Store.register_group_commands(group_id: int) -> list[StoredGroupCommand]` — called by `create_group`, `set_group_members` and `forget_device`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/model/test_store_groups.py`:

```python
def _slugs(store, group_id):
    return sorted(c.slug for c in store.group_commands(group_id))


@pytest.fixture
def lamps_with_commands(store, lamps):
    """Registers the command rows the intersection is computed from."""
    from loxmatter.export.commands import extract_commands

    for device_id, name in zip(
        lamps, ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"), strict=True
    ):
        snapshot = load(name)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
    return lamps


def test_the_group_offers_only_what_every_member_accepts(store, lamps_with_commands):
    colour_only = store.create_group("Colour", [lamps_with_commands[0]])
    both = store.create_group("Both", lamps_with_commands)
    assert "color" in _slugs(store, colour_only.id)
    assert "color" not in _slugs(store, both.id)
    assert {"on", "off", "toggle"} <= set(_slugs(store, both.id))


def test_a_surviving_command_keeps_its_key(store, lamps_with_commands):
    group = store.create_group("Colour", [lamps_with_commands[0]])
    before = {c.slug: c.key for c in store.group_commands(group.id)}
    store.set_group_members(group.id, lamps_with_commands)
    after = {c.slug: c.key for c in store.group_commands(group.id)}
    assert after["on"] == before["on"]


def test_a_command_that_leaves_the_intersection_stops_resolving(store, lamps_with_commands):
    group = store.create_group("Colour", [lamps_with_commands[0]])
    key = next(c.key for c in store.group_commands(group.id) if c.slug == "color")
    store.set_group_members(group.id, lamps_with_commands)
    with pytest.raises(UnknownCommandError):
        store.resolve_group_command(key)


def test_the_group_survives_losing_every_member(store, lamps_with_commands):
    group = store.create_group("Colour", lamps_with_commands)
    store.set_group_members(group.id, [])
    assert store.group(group.id).label == "Colour"
    assert store.group_commands(group.id) == []


def test_forgetting_a_member_recomputes_the_intersection(store, lamps_with_commands):
    group = store.create_group("Both", lamps_with_commands)
    assert "color" not in _slugs(store, group.id)
    store.forget_device(lamps_with_commands[1])
    assert "color" in _slugs(store, group.id)


def test_group_keys_can_never_collide_with_device_keys(store, lamps_with_commands):
    """The `d`/`g` prefixes are a convention, not an SQL guarantee - this
    is the assertion `resolve_command`'s two-step lookup rests on."""
    group = store.create_group("Both", lamps_with_commands)
    device_keys = {c.key for d in lamps_with_commands for c in store.commands(d)}
    group_keys = {c.key for c in store.group_commands(group.id)}
    assert device_keys and group_keys
    assert device_keys.isdisjoint(group_keys)
```

Add `UnknownCommandError` to the `from loxmatter.model.store import (...)` list at the top of the file.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/model/test_store_groups.py -k intersection -v
```

Expected: FAIL, `AttributeError: 'Store' object has no attribute 'group_commands'`.

- [ ] **Step 3: Implement the intersection**

Next to `StoredCommand` in `store.py`:

```python
@dataclass(frozen=True)
class StoredGroupCommand:
    """A row from `group_command` (design 2026-09-10, section 4.1).

    Carries NO endpoint, unlike `StoredCommand`. Each member has its own,
    and two lamps of different make can hold the same cluster on
    different endpoints - the endpoint therefore belongs to the member and
    is looked up per member at dispatch time (`Store.group_targets`).
    """

    key: str
    slug: str
    group_id: int
    cluster_id: int
    command_id: int
    takes_value: bool
```

On `Store`:

```python
    @staticmethod
    def _as_group_command(row: sqlite3.Row) -> StoredGroupCommand:
        return StoredGroupCommand(
            key=str(row["key"]),
            slug=str(row["slug"]),
            group_id=int(row["group_id"]),
            cluster_id=int(row["cluster_id"]),
            command_id=int(row["command_id"]),
            takes_value=bool(row["takes_value"]),
        )

    def group_commands(self, group_id: int) -> list[StoredGroupCommand]:
        rows = self._db.execute(
            "SELECT * FROM group_command WHERE group_id = ? ORDER BY cluster_id, command_id",
            (group_id,),
        ).fetchall()
        return [self._as_group_command(r) for r in rows]

    def resolve_group_command(self, key: str) -> StoredGroupCommand:
        row = self._db.execute("SELECT * FROM group_command WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise UnknownCommandError(i18n.t("api.errors.unknown_command", command_key=key))
        return self._as_group_command(row)

    def register_group_commands(self, group_id: int) -> list[StoredGroupCommand]:
        """Recomputes the group's command list from its current members.

        Called after every membership change. Mirrors `register_commands`,
        which likewise re-adopts `slug` and `takes_value` on every call so
        that a correction in `clusters.yaml` reaches an already stored
        command - a frozen list would be the opposite of that and would
        let a group claim a capability no member has left (design 4.3).

        A command that survives keeps its key. One that drops out of the
        intersection loses its row and its key answers 404 from then on -
        deliberately, because that 404 stands in the log and points at the
        one line in the Loxone project that needs attention, whereas a
        silently vanished key leaves an output nobody can trace.

        The intersection runs over `(cluster_id, command_id)` pairs, NOT
        over endpoints: a member that carries the pair on several
        endpoints still counts as one member that accepts it (see
        `group_targets`, which then sends to all of them).
        """
        members = self.group_members(group_id)
        by_member = [
            {(c.cluster_id, c.command_id): c for c in self.commands(device.id)}
            for device in members
        ]
        shared: dict[tuple[int, int], StoredCommand] = {}
        if by_member:
            common = set(by_member[0])
            for other in by_member[1:]:
                common &= set(other)
            shared = {pair: by_member[0][pair] for pair in common}

        keep = set(shared)
        for existing in self.group_commands(group_id):
            if (existing.cluster_id, existing.command_id) not in keep:
                self._db.execute("DELETE FROM group_command WHERE key = ?", (existing.key,))

        for (cluster_id, command_id), sample in sorted(shared.items()):
            row = self._db.execute(
                "SELECT key FROM group_command"
                " WHERE group_id = ? AND cluster_id = ? AND command_id = ?",
                (group_id, cluster_id, command_id),
            ).fetchone()
            if row is not None:
                self._db.execute(
                    "UPDATE group_command SET slug = ?, takes_value = ? WHERE key = ?",
                    (sample.slug, int(sample.takes_value), row["key"]),
                )
                continue
            self._db.execute(
                "INSERT INTO group_command"
                " (group_id, cluster_id, command_id, key, slug, takes_value)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    group_id,
                    cluster_id,
                    command_id,
                    f"g{group_id}_{sample.slug}",
                    sample.slug,
                    int(sample.takes_value),
                ),
            )
        self._db.commit()
        return self.group_commands(group_id)
```

Then call it from the three places that change membership: at the end of `create_group` (before the `return`, after `self._db.commit()`), at the end of `set_group_members`, and at the end of `forget_device` for every group the device was in — capture them before the delete:

```python
    def forget_device(self, device_id: int) -> None:
        affected = [
            int(row["group_id"])
            for row in self._db.execute(
                "SELECT group_id FROM device_group_member WHERE device_id = ?", (device_id,)
            ).fetchall()
        ]
        self._db.execute("DELETE FROM device_group_member WHERE device_id = ?", (device_id,))
        self._db.execute("UPDATE device SET active = 0 WHERE id = ?", (device_id,))
        self._db.commit()
        for group_id in affected:
            self.register_group_commands(group_id)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/model/test_store_groups.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the check set and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest tests/model -v
```

```bash
git add src/loxmatter/model/store.py tests/model/test_store_groups.py
git commit -m "feat(store): derive a group's commands as its members' intersection

Recomputed on every membership change, mirroring register_commands: a
surviving command keeps its key, one that drops out loses its row and
answers 404 from then on. That 404 points at the line in the Loxone
project that needs attention; a silently vanished key would not."
```

---

### Task 3: Store — dispatch targets

**Files:**
- Modify: `src/loxmatter/model/store.py`
- Test: `tests/model/test_store_groups.py` (extend)

**Interfaces:**
- Consumes: `StoredGroupCommand`, `Store.group_members`, `Store.commands`.
- Produces:
  - `GroupTarget(device_id: int, device_label: str, commands: tuple[StoredCommand, ...])`
  - `Store.group_targets(command: StoredGroupCommand) -> list[GroupTarget]`

- [ ] **Step 1: Write the failing test**

Append to `tests/model/test_store_groups.py`:

```python
def test_targets_carry_one_entry_per_member_with_that_member_s_own_rows(
    store, lamps_with_commands
):
    group = store.create_group("Both", lamps_with_commands)
    on = next(c for c in store.group_commands(group.id) if c.slug == "on")
    targets = store.group_targets(on)
    assert [t.device_id for t in targets] == lamps_with_commands
    assert all(t.device_label for t in targets)
    for target in targets:
        assert target.commands
        for command in target.commands:
            assert (command.cluster_id, command.command_id) == (on.cluster_id, on.command_id)


def test_a_member_carrying_the_pair_on_two_endpoints_gets_both(store, lamps_with_commands):
    """A two-channel device has one command row per endpoint; the group
    has one command. The only reading that does not surprise is "the
    whole member" (design 4.3)."""
    group = store.create_group("Both", lamps_with_commands)
    on = next(c for c in store.group_commands(group.id) if c.slug == "on")
    store._db.execute(
        "INSERT INTO command"
        " (device_id, node_id, endpoint, cluster_id, command_id, key, slug, takes_value)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            lamps_with_commands[0],
            store.device(lamps_with_commands[0]).node_id,
            99,
            on.cluster_id,
            on.command_id,
            f"d{lamps_with_commands[0]}_99_on",
            "on",
            0,
        ),
    )
    store._db.commit()
    targets = store.group_targets(on)
    first = next(t for t in targets if t.device_id == lamps_with_commands[0])
    assert sorted(c.endpoint for c in first.commands) == sorted(
        {c.endpoint for c in first.commands}
    )
    assert 99 in {c.endpoint for c in first.commands}
    assert len(first.commands) >= 2
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/model/test_store_groups.py -k targets -v
```

Expected: FAIL, `AttributeError: 'Store' object has no attribute 'group_targets'`.

- [ ] **Step 3: Implement**

Next to `StoredGroupCommand`:

```python
@dataclass(frozen=True)
class GroupTarget:
    """One member of a group, with the command rows that carry out one
    group command on it (design 2026-09-10, section 3).

    `device_label` travels along because the 502 detail names the members
    that failed, and the dispatcher must not have to reach back into the
    store to find out who it was talking to.
    """

    device_id: int
    device_label: str
    commands: tuple[StoredCommand, ...]
```

On `Store`:

```python
    def group_targets(self, command: StoredGroupCommand) -> list[GroupTarget]:
        """The per-member command rows for one group command.

        A member may carry the pair on several endpoints; all of them are
        returned, ordered by endpoint, and all of them get the command
        (design 4.3). Members without a matching row are skipped rather
        than returned empty - by construction of the intersection there
        should be none, and an empty target would only make the
        dispatcher guard against a case the store already rules out.
        """
        targets: list[GroupTarget] = []
        for device in self.group_members(command.group_id):
            rows = tuple(
                stored
                for stored in self.commands(device.id)
                if stored.cluster_id == command.cluster_id
                and stored.command_id == command.command_id
            )
            if not rows:
                continue
            targets.append(
                GroupTarget(device_id=device.id, device_label=device.label, commands=rows)
            )
        return targets
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/model/test_store_groups.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/model/store.py tests/model/test_store_groups.py
git commit -m "feat(store): resolve a group command to its per-member targets

A member carrying the same cluster/command pair on several endpoints is
returned with all of them: it has one command row per endpoint, the group
has one command, and 'the whole member' is the only reading of that which
does not leave half a device unswitched without saying why."
```

---

### Task 4: The fan-out

**Files:**
- Create: `src/loxmatter/commands/fanout.py`
- Test: `tests/commands/test_fanout.py` (create)

**Interfaces:**
- Consumes: `GroupTarget` (Task 3), `MatterCall`, `to_matter_calls`, `UnsupportedValueError` (all existing in `commands/translate.py`).
- Produces:
  - `MemberPlan(device_id: int, device_label: str, calls: tuple[MatterCall, ...])`
  - `plan_group_calls(targets: Sequence[GroupTarget], value: str) -> list[MemberPlan]` — raises `UnsupportedValueError`
  - `async dispatch_group(plans: Sequence[MemberPlan], invoke: Callable[[MatterCall], Awaitable[None]]) -> list[str]` — returns the labels of the members that failed, in plan order; empty means every member succeeded.

- [ ] **Step 1: Write the failing tests**

Create `tests/commands/test_fanout.py` with the GPL header, then:

```python
"""The group fan-out - see design 2026-09-10, section 3."""

from __future__ import annotations

import asyncio

import pytest

from loxmatter.commands.fanout import dispatch_group, plan_group_calls
from loxmatter.commands.translate import MatterCall, UnsupportedValueError
from loxmatter.model.store import GroupTarget, StoredCommand


def command(device_id: int, node_id: int, endpoint: int, cluster_id: int, command_id: int, slug: str, takes_value: bool) -> StoredCommand:
    return StoredCommand(
        key=f"d{device_id}_{endpoint}_{slug}",
        slug=slug,
        node_id=node_id,
        endpoint=endpoint,
        cluster_id=cluster_id,
        command_id=command_id,
        takes_value=takes_value,
        device_id=device_id,
    )


def on_target(device_id: int, node_id: int, label: str) -> GroupTarget:
    return GroupTarget(
        device_id=device_id,
        device_label=label,
        commands=(command(device_id, node_id, 1, 6, 1, "on", False),),
    )


def colour_target(device_id: int, node_id: int, label: str) -> GroupTarget:
    return GroupTarget(
        device_id=device_id,
        device_label=label,
        commands=(command(device_id, node_id, 1, 768, 6, "color", True),),
    )


def test_one_plan_per_member_carrying_that_member_s_node_id():
    plans = plan_group_calls([on_target(1, 11, "A"), on_target(2, 22, "B")], "1")
    assert [p.device_label for p in plans] == ["A", "B"]
    assert [call.node_id for p in plans for call in p.calls] == [11, 22]


def test_a_member_with_two_endpoints_gets_two_calls():
    target = GroupTarget(
        device_id=1,
        device_label="A",
        commands=(
            command(1, 11, 1, 6, 1, "on", False),
            command(1, 11, 2, 6, 1, "on", False),
        ),
    )
    (plan,) = plan_group_calls([target], "1")
    assert [call.endpoint for call in plan.calls] == [1, 2]


def test_a_bad_value_is_reported_before_anything_is_sent():
    with pytest.raises(UnsupportedValueError):
        plan_group_calls([colour_target(1, 11, "A")], "not a number")


async def test_the_calls_of_one_member_keep_their_order():
    """The colour path sends colour and then brightness to ONE device, and
    that order is deliberate (`_EXECUTE_IF_OFF` in translate.py). A flat
    gather over all calls of all members would destroy it."""
    plans = plan_group_calls([colour_target(1, 11, "A"), colour_target(2, 22, "B")], "60100060")
    seen: list[tuple[int, int]] = []

    async def invoke(call: MatterCall) -> None:
        seen.append((call.node_id, call.command_id))
        await asyncio.sleep(0)

    assert await dispatch_group(plans, invoke) == []
    for node_id in (11, 22):
        own = [command_id for node, command_id in seen if node == node_id]
        expected = [call.command_id for plan in plans if plan.calls[0].node_id == node_id for call in plan.calls]
        assert own == expected


async def test_members_are_dispatched_concurrently():
    """Two members whose invocations each block until the other has
    started. A sequential dispatcher deadlocks here and the test times
    out; a concurrent one passes."""
    started = asyncio.Event()
    second = asyncio.Event()
    plans = plan_group_calls([on_target(1, 11, "A"), on_target(2, 22, "B")], "1")

    async def invoke(call: MatterCall) -> None:
        if call.node_id == 11:
            started.set()
            await asyncio.wait_for(second.wait(), timeout=2)
        else:
            await asyncio.wait_for(started.wait(), timeout=2)
            second.set()

    assert await dispatch_group(plans, invoke) == []


async def test_a_failing_member_does_not_stop_the_others():
    plans = plan_group_calls(
        [on_target(1, 11, "A"), on_target(2, 22, "B"), on_target(3, 33, "C")], "1"
    )
    reached: list[int] = []

    async def invoke(call: MatterCall) -> None:
        if call.node_id == 22:
            raise RuntimeError("no route to host")
        reached.append(call.node_id)

    assert await dispatch_group(plans, invoke) == ["B"]
    assert sorted(reached) == [11, 33]


async def test_every_failing_member_is_named_not_just_the_first():
    plans = plan_group_calls(
        [on_target(1, 11, "A"), on_target(2, 22, "B"), on_target(3, 33, "C")], "1"
    )

    async def invoke(call: MatterCall) -> None:
        if call.node_id in (11, 33):
            raise RuntimeError("no route to host")

    assert await dispatch_group(plans, invoke) == ["A", "C"]
```

Check whether `tests/commands/` already has an `asyncio_mode = auto` setting in `pyproject.toml` (`grep -n "asyncio_mode" pyproject.toml`). If it does not, add `@pytest.mark.asyncio` to each `async def test_...` above.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/commands/test_fanout.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'loxmatter.commands.fanout'`.

- [ ] **Step 3: Implement `fanout.py`**

Create `src/loxmatter/commands/fanout.py` with the GPL header, then:

```python
"""Sends one group command to every member of the group.

Its own module rather than a branch inside the two routes that use it:
`/cmd/{key}/{value}` (loxone/server.py) and `POST /api/commands/{key}`
(api/control.py) must fan out identically, and a copy in each would drift
- the same reason `commands.translate` exists as one module for both
(Main Spec 4.2).

Deliberately knows nothing about HTTP or the store. It receives targets
and hands back which members failed; turning that into a 400 or a 502 is
the routes' business, and keeping it out of here is what makes the
ordering and concurrency below testable without a server.

**Why this is a fan-out at all** is not this module's decision to
defend: no Matter server this bridge can use exposes group messaging, so
there is nothing to send a single group command to (design 2026-09-10,
section 1). If that ever changes, this module is the one that would gain
a second implementation, and nothing else in the feature would move.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from loxmatter.commands.translate import MatterCall, to_matter_calls
from loxmatter.model.store import GroupTarget

__all__ = ["MemberPlan", "dispatch_group", "plan_group_calls"]


@dataclass(frozen=True)
class MemberPlan:
    """Everything one member is to receive, in the order it is to receive it."""

    device_id: int
    device_label: str
    calls: tuple[MatterCall, ...]


def plan_group_calls(targets: Sequence[GroupTarget], value: str) -> list[MemberPlan]:
    """Translates the group's value once per member command.

    Raises `UnsupportedValueError` before anything is sent - all members
    share the same (cluster, command) pair, so the translation either
    works for all of them or for none, and finding out halfway through a
    fan-out would leave a partial state for a value that was never valid.
    """
    plans: list[MemberPlan] = []
    for target in targets:
        calls: list[MatterCall] = []
        for stored in target.commands:
            calls.extend(to_matter_calls(stored, value))
        plans.append(
            MemberPlan(
                device_id=target.device_id,
                device_label=target.device_label,
                calls=tuple(calls),
            )
        )
    return plans


async def _run_member(plan: MemberPlan, invoke: Callable[[MatterCall], Awaitable[None]]) -> None:
    """One member's calls, strictly in order.

    Sequential within the member and NOT gathered: the colour path sends
    colour first and brightness second to the same device, and that order
    is deliberate - `_EXECUTE_IF_OFF` in `commands/translate.py` records
    the measurement behind it (a switched-off KAJPLATS CWS turned white
    at full brightness instead of green when the colour command arrived
    too late). Flattening every member's calls into one gather would
    destroy exactly that.
    """
    for call in plan.calls:
        await invoke(call)


async def dispatch_group(
    plans: Sequence[MemberPlan], invoke: Callable[[MatterCall], Awaitable[None]]
) -> list[str]:
    """Runs every member concurrently and returns the labels that failed.

    `return_exceptions=True` rather than letting the first failure
    propagate: a group of six with two dead lamps must switch the other
    four and then say which two did not answer. Reporting only the first
    exception would name one lamp when three are unreachable, which reads
    like a single device fault instead of a network fault.

    The returned list is in plan order, so the message a caller builds
    from it is reproducible.
    """
    results = await asyncio.gather(
        *(_run_member(plan, invoke) for plan in plans), return_exceptions=True
    )
    return [
        plan.device_label
        for plan, result in zip(plans, results, strict=True)
        if isinstance(result, BaseException)
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/commands/test_fanout.py -v
```

Expected: PASS, seven tests.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/commands/fanout.py tests/commands/test_fanout.py
git commit -m "feat(commands): fan one group command out to its members

Concurrent across members, strictly sequential within one member: the
colour path sends colour before brightness to the same device on purpose,
and a flat gather over every call would lose that ordering.

Failures are collected rather than raised at the first one, so a group of
six with two dead lamps still switches the other four and can say which
two did not answer."
```

---

### Task 5: The Loxone endpoint

**Files:**
- Modify: `src/loxmatter/loxone/server.py:579-613`
- Test: `tests/api/test_group_control.py` (create)

**Interfaces:**
- Consumes: `Store.resolve_group_command`, `Store.group_targets` (Tasks 2–3), `plan_group_calls`, `dispatch_group` (Task 4).
- Produces: nothing new; `/cmd/{key}/{value}` gains the group path.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_group_control.py` with the GPL header, then:

```python
"""A group behind `/cmd` and `POST /api/commands` - design 2026-09-10, section 3."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.commands.translate import MatterCall
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


@pytest.fixture
def invocations() -> list[MatterCall]:
    return []


@pytest.fixture
def failing_nodes() -> set[int]:
    """Node IDs whose invocation raises - the 502 path."""
    return set()


@pytest.fixture
async def api(
    tmp_path, invocations, failing_nodes, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    store = Store(tmp_path / "t.sqlite")
    member_ids = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = load_snapshot(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
        member_ids.append(device_id)
    group = store.create_group("Living room", member_ids)

    async def invoke(call: MatterCall) -> None:
        if call.node_id in failing_nodes:
            raise RuntimeError("no route to host")
        invocations.append(call)

    app = build_app(store, invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, group.id
    store.close()


async def test_one_loxone_call_reaches_every_member(api, invocations):
    client, store, group_id = api
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")
    response = await client.get(f"/cmd/{key}/1")
    assert response.status_code == 200
    node_ids = {device.node_id for device in store.group_members(group_id)}
    assert {call.node_id for call in invocations} == node_ids


async def test_an_unknown_group_key_is_a_404(api):
    client, _store, _group_id = api
    assert (await client.get("/cmd/g99_on/1")).status_code == 404


async def test_a_bad_value_is_a_400_and_sends_nothing(api, invocations):
    client, store, group_id = api
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "level")
    assert (await client.get(f"/cmd/{key}/banana")).status_code == 400
    assert invocations == []


async def test_a_failing_member_yields_502_and_names_it(api, invocations, failing_nodes):
    client, store, group_id = api
    members = store.group_members(group_id)
    failing_nodes.add(members[0].node_id)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")
    response = await client.get(f"/cmd/{key}/1")
    assert response.status_code == 502
    assert members[0].label in response.json()["detail"]
    # the reachable member was still switched
    assert {call.node_id for call in invocations} == {members[1].node_id}


async def test_a_device_key_still_works_unchanged(api, invocations):
    """The group path is a fallback, not a replacement (design 3)."""
    client, store, group_id = api
    device = store.group_members(group_id)[0]
    key = next(c.key for c in store.commands(device.id) if c.slug == "on")
    assert (await client.get(f"/cmd/{key}/1")).status_code == 200
    assert [call.node_id for call in invocations] == [device.node_id]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/api/test_group_control.py -v
```

Expected: FAIL — `test_one_loxone_call_reaches_every_member` gets 404, because `resolve_command` does not know group keys.

- [ ] **Step 3: Implement the group path in `/cmd`**

Replace the body of the `command` route in `src/loxmatter/loxone/server.py` with:

```python
    @app.get("/cmd/{key}/{value}")
    async def command(key: str, value: str) -> dict[str, str]:
        try:
            stored = store.resolve_command(key)
        except KeyError:
            # A group key, or nothing at all. The device table is asked
            # first so that a device key costs exactly what it always did;
            # the two can never collide (`d` vs `g` prefix, asserted in
            # test_group_keys_can_never_collide_with_device_keys).
            return await _group_command(key, value)

        try:
            calls = to_matter_calls(stored, value)
        except UnsupportedValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            for call in calls:
                await invoke(call)
        except Exception as exc:  # every device problem becomes 502
            logger.exception("Matter call for key %r failed", key)
            raise HTTPException(
                status_code=502, detail=i18n.t("api.errors.device_unreachable", exc=exc)
            ) from exc

        return {"status": "ok", "key": key}

    async def _group_command(key: str, value: str) -> dict[str, str]:
        """The group half of `/cmd/{key}/{value}` (design 2026-09-10, 3).

        The status codes are the device path's, unchanged: 404 unknown
        key, 400 unsuitable value, 502 at least one member did not
        answer. The Miniserver evaluates none of them - they are for the
        human reading the log, which is also why the 502 detail names the
        members instead of just counting them.
        """
        try:
            group_command = store.resolve_group_command(key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        targets = store.group_targets(group_command)
        try:
            plans = plan_group_calls(targets, value)
        except UnsupportedValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        failed = await dispatch_group(plans, invoke)
        if failed:
            logger.warning(
                "group command %r reached %d of %d members", key, len(plans) - len(failed), len(plans)
            )
            raise HTTPException(
                status_code=502,
                detail=i18n.t(
                    "api.errors.group_partially_unreachable",
                    reached=len(plans) - len(failed),
                    total=len(plans),
                    devices=", ".join(failed),
                ),
            )
        return {"status": "ok", "key": key}
```

Add to the imports at the top of `loxone/server.py`:

```python
from loxmatter.commands.fanout import dispatch_group, plan_group_calls
```

- [ ] **Step 4: Add the string**

```yaml
api.errors.group_partially_unreachable:
  en: "reached {reached} of {total} members; no answer from: {devices}"
  de: "{reached} von {total} Mitgliedern erreicht, keine Antwort von: {devices}"
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/api/test_group_control.py -v
```

Expected: PASS, five tests.

- [ ] **Step 6: Run the regression set and commit**

```bash
uv run pytest tests/api tests/loxone -v && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
```

```bash
git add src/loxmatter/loxone/server.py src/loxmatter/i18n/strings.yaml tests/api/test_group_control.py
git commit -m "feat(loxone): drive a group from one /cmd call

The device table is asked first, so a device key costs exactly what it
always did and the group path is a fallback rather than a replacement.
The 502 detail names the members that did not answer: the Miniserver
evaluates no status code at all, so the text exists for the person
reading the log."
```

---

### Task 6: The WebUI control route

**Files:**
- Modify: `src/loxmatter/api/control.py:290-341`
- Test: `tests/api/test_group_control.py` (extend)

**Interfaces:**
- Consumes: the same as Task 5.
- Produces: nothing new; `POST /api/commands/{key}` gains the group path.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_group_control.py`:

```python
async def test_the_webui_route_drives_a_group_too(api, invocations):
    """No second control endpoint - the shared key namespace means a group
    tile makes the same call a device tile makes (design 5)."""
    client, store, group_id = api
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")
    response = await client.post(f"/api/commands/{key}", json={"value": "1"})
    assert response.status_code == 200
    node_ids = {device.node_id for device in store.group_members(group_id)}
    assert {call.node_id for call in invocations} == node_ids


async def test_the_webui_route_and_the_loxone_route_translate_identically(api, invocations):
    """Spec 4.2: one translation, two callers, or they drift."""
    client, store, group_id = api
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "level")
    await client.get(f"/cmd/{key}/50")
    from_loxone = list(invocations)
    invocations.clear()
    await client.post(f"/api/commands/{key}", json={"value": "50"})
    assert sorted(from_loxone, key=lambda c: c.node_id) == sorted(
        invocations, key=lambda c: c.node_id
    )


async def test_an_unknown_key_is_a_404_on_the_webui_route(api):
    client, _store, _group_id = api
    response = await client.post("/api/commands/g99_on", json={"value": "1"})
    assert response.status_code == 404


async def test_a_failing_member_is_a_502_on_the_webui_route(api, failing_nodes):
    client, store, group_id = api
    members = store.group_members(group_id)
    failing_nodes.add(members[0].node_id)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")
    response = await client.post(f"/api/commands/{key}", json={"value": "1"})
    assert response.status_code == 502
    assert members[0].label in response.json()["detail"]
```

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run pytest tests/api/test_group_control.py -k webui -v
```

Expected: FAIL with 404 — `resolve_command` in `api/control.py` does not know group keys.

- [ ] **Step 3: Implement**

In `src/loxmatter/api/control.py`, change the start of `execute_command` and add the group half:

```python
    @router.post("/commands/{key}")
    async def execute_command(key: str, body: ValueIn) -> dict[str, str]:
        try:
            stored = store.resolve_command(key)
        except UnknownCommandError:
            # A group key, or nothing at all - the same two-step lookup as
            # the Loxone endpoint, and deliberately no second control
            # route for groups: the key namespace is shared, so a group
            # tile makes the same call a device tile makes (Spec 4.2).
            return await _execute_group_command(key, body)
        ...  # the rest of the existing body stays exactly as it is
```

and, inside `build_control_router`, next to it:

```python
    async def _execute_group_command(key: str, body: ValueIn) -> dict[str, str]:
        """The group half of `POST /api/commands/{key}`.

        Deliberately no `store.device(...)` check as in the device half
        above: a group has no device of its own, and its members are
        filtered by `group_members`, which returns active devices only. A
        member removed with `forget_device` therefore cannot be reached
        through a group either - the same gap that review fix Important #1
        closed for device commands, closed here by construction rather
        than by a second check.
        """
        try:
            group_command = store.resolve_group_command(key)
        except UnknownCommandError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        targets = store.group_targets(group_command)
        try:
            plans = plan_group_calls(targets, body.value)
        except UnsupportedValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        failed = await dispatch_group(plans, invoke)
        if failed:
            logger.warning(
                "group command %r reached %d of %d members",
                key,
                len(plans) - len(failed),
                len(plans),
            )
            raise HTTPException(
                status_code=502,
                detail=i18n.t(
                    "api.errors.group_partially_unreachable",
                    reached=len(plans) - len(failed),
                    total=len(plans),
                    devices=", ".join(failed),
                ),
            )
        return {"status": "ok", "key": key}
```

Add to the imports of `api/control.py`:

```python
from loxmatter.commands.fanout import dispatch_group, plan_group_calls
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/api/test_group_control.py -v
```

Expected: PASS, nine tests.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/api/control.py tests/api/test_group_control.py
git commit -m "feat(api): drive a group from POST /api/commands/{key}

No second control endpoint: the key namespace is shared, so a group tile
in the WebUI makes exactly the call a device tile makes, and the one
translation keeps serving both callers (Spec 4.2)."
```

---

### Task 7: The groups router

**Files:**
- Create: `src/loxmatter/api/groups.py`
- Modify: `src/loxmatter/api/models.py`, `src/loxmatter/loxone/server.py` (router wiring)
- Test: `tests/api/test_groups.py` (create)

**Interfaces:**
- Consumes: every `Store` method from Tasks 1–3; `ControlRange`, `CommandOut`, `ControlsOut` from `api/models.py`; `command_control`, `command_slug` from `profiles/table.py`.
- Produces:
  - `GroupIn(label: str, member_ids: list[int], room: str | None = None)`
  - `GroupPatch(label: str | None = None, room: str | None = None)`
  - `GroupMembersIn(member_ids: list[int])`
  - `GroupOut(id, label, room, category, member_ids, member_labels, command_count)`
  - `GroupControlsOut(commands: list[CommandOut], hidden_raw_commands: int, seed_device_id: int | None, seed_device_label: str | None)`
  - `build_groups_router(store: Store, values: ValueReader) -> APIRouter`

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_groups.py` with the GPL header, then:

```python
"""The groups router - design 2026-09-10, sections 5 and 6."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime, fake_client) -> AsyncIterator[tuple[httpx.AsyncClient, Store, list[int], int]]:
    store = Store(tmp_path / "t.sqlite")
    lamps: list[int] = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = load_snapshot(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
        lamps.append(device_id)
    plug_snapshot = load_snapshot("ikea_grillplats_plug.json")
    plug = store.register_device(plug_snapshot)
    store.register_signals(plug, plug_snapshot)
    store.register_commands(plug, extract_commands(plug_snapshot), plug_snapshot.node_id)

    app = build_app(store, no_invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, lamps, plug
    store.close()


async def test_creating_and_listing_a_group(api):
    client, _store, lamps, _plug = api
    created = await client.post(
        "/api/groups", json={"label": "Living room", "member_ids": lamps, "room": "Living room"}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["category"] == "light"
    assert body["member_ids"] == lamps
    listed = await client.get("/api/groups")
    assert [g["id"] for g in listed.json()] == [body["id"]]


async def test_a_member_of_another_category_is_a_400(api):
    client, _store, lamps, plug = api
    response = await client.post(
        "/api/groups", json={"label": "Mixed", "member_ids": [lamps[0], plug]}
    )
    assert response.status_code == 400


async def test_an_empty_member_list_is_a_400(api):
    client, _store, _lamps, _plug = api
    response = await client.post("/api/groups", json={"label": "Empty", "member_ids": []})
    assert response.status_code == 400


async def test_label_and_room_can_be_patched(api):
    client, _store, lamps, _plug = api
    group_id = (await client.post("/api/groups", json={"label": "A", "member_ids": lamps})).json()["id"]
    response = await client.patch(f"/api/groups/{group_id}", json={"label": "B", "room": "Hallway"})
    assert response.status_code == 200
    assert (response.json()["label"], response.json()["room"]) == ("B", "Hallway")


async def test_replacing_the_members_recomputes_the_commands(api):
    client, _store, lamps, _plug = api
    group_id = (await client.post("/api/groups", json={"label": "A", "member_ids": [lamps[0]]})).json()["id"]
    before = (await client.get(f"/api/groups/{group_id}/controls")).json()
    response = await client.put(f"/api/groups/{group_id}/members", json={"member_ids": lamps})
    assert response.status_code == 200
    after = (await client.get(f"/api/groups/{group_id}/controls")).json()
    slugs_before = {c["slug"] for c in before["commands"]}
    slugs_after = {c["slug"] for c in after["commands"]}
    assert "color" in slugs_before
    assert "color" not in slugs_after


async def test_a_group_can_be_deleted(api):
    client, _store, lamps, _plug = api
    group_id = (await client.post("/api/groups", json={"label": "A", "member_ids": lamps})).json()["id"]
    assert (await client.delete(f"/api/groups/{group_id}")).status_code == 204
    assert (await client.get(f"/api/groups/{group_id}")).status_code == 404


async def test_an_unknown_group_is_a_404(api):
    client, _store, _lamps, _plug = api
    assert (await client.get("/api/groups/999")).status_code == 404
    assert (await client.get("/api/groups/999/controls")).status_code == 404


async def test_the_controls_name_the_device_the_initial_value_came_from(api):
    """Six lamps have six brightnesses; the tile attributes the number it
    shows instead of presenting it as the group's (design 5)."""
    client, _store, lamps, _plug = api
    group_id = (await client.post("/api/groups", json={"label": "A", "member_ids": lamps})).json()["id"]
    body = (await client.get(f"/api/groups/{group_id}/controls")).json()
    assert body["seed_device_id"] == lamps[0]
    assert body["seed_device_label"]


async def test_groups_need_authentication(tmp_path, no_invoke, fake_runtime, fake_client):
    """Every /api route sits behind the guard - this fixture deliberately
    never calls `authenticate`. With no token configured and no session,
    the guard answers 401 (tests/api/test_security.py,
    test_guard_rejects_everything_when_no_token_is_configured_and_no_session_exists)."""
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/groups")
    store.close()
    assert response.status_code == 401
```

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run pytest tests/api/test_groups.py -v
```

Expected: FAIL with 404 on every route — no router is mounted yet.

- [ ] **Step 3: Add the models**

In `src/loxmatter/api/models.py`:

```python
class GroupIn(BaseModel):
    """Body of `POST /api/groups`.

    `member_ids` is required and must not be empty: the first member fixes
    the group's category (design 2026-09-10, section 2).
    """

    model_config = ConfigDict(frozen=True)

    label: str
    member_ids: list[int]
    room: str | None = None


class GroupPatch(BaseModel):
    """Body of `PATCH /api/groups/{id}` - label and room, both optional."""

    model_config = ConfigDict(frozen=True)

    label: str | None = None
    room: str | None = None


class GroupMembersIn(BaseModel):
    """Body of `PUT /api/groups/{id}/members` - the COMPLETE list.

    Not add/remove: the command intersection is recomputed after every
    change anyway, and two single removals would pass through an
    intermediate state nobody asked for (design 5).
    """

    model_config = ConfigDict(frozen=True)

    member_ids: list[int]


class GroupOut(BaseModel):
    """A group for the WebUI. `member_labels` travels with `member_ids` so
    a tile can name its members without one request per member."""

    model_config = ConfigDict(frozen=True)

    id: int
    label: str
    room: str | None
    category: str
    member_ids: list[int]
    member_labels: list[str]
    command_count: int


class GroupControlsOut(BaseModel):
    """Response of `GET /api/groups/{id}/controls`.

    `seed_device_id`/`seed_device_label` name the member the sliders'
    initial values were read from. A group has no state of its own, and
    the lamp-controls design ruled out showing a slider with no initial
    value at all - so the number is shown and attributed rather than
    presented as the group's (design 5). Both are `None` for an empty
    group.
    """

    model_config = ConfigDict(frozen=True)

    commands: list[CommandOut]
    hidden_raw_commands: int
    seed_device_id: int | None
    seed_device_label: str | None
```

- [ ] **Step 4: Write the router**

Create `src/loxmatter/api/groups.py` with the GPL header, then:

```python
"""Creating, changing and inspecting groups (design 2026-09-10).

A group is a named sender without a node of its own: several devices of
the same category behind one Loxone virtual output. It has no signals, no
live values and no online state - those are properties of a node, and
inventing an aggregate for six lamps with six brightnesses is exactly the
quiet fiction Spec 8.1 exists to prevent.

**There is no control route here.** A group is driven through `POST
/api/commands/{key}` like a device, because group and device command keys
share one namespace - see `api/control.py`. Only the *reading* half, the
controls route, lives here, because its answer differs: value ranges are
intersected across the members and the initial values carry an
attribution.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from loxmatter import i18n
from loxmatter.api.control import ValueReader
from loxmatter.api.models import (
    CommandOut,
    ControlRange,
    GroupControlsOut,
    GroupIn,
    GroupMembersIn,
    GroupOut,
    GroupPatch,
)
from loxmatter.model.store import (
    CategoryMismatchError,
    Store,
    StoredGroup,
    UnknownDeviceError,
    UnknownGroupError,
)
from loxmatter.profiles.table import command_control, command_slug

_CLUSTER_COLOR = 768
_ATTR_CT_PHYS_MIN_MIREDS = 16395
_ATTR_CT_PHYS_MAX_MIREDS = 16396


def build_groups_router(store: Store, values: ValueReader) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _out(group: StoredGroup) -> GroupOut:
        members = store.group_members(group.id)
        return GroupOut(
            id=group.id,
            label=group.label,
            room=group.room,
            category=group.category,
            member_ids=[device.id for device in members],
            member_labels=[device.label for device in members],
            command_count=len(store.group_commands(group.id)),
        )

    def _require(group_id: int) -> StoredGroup:
        try:
            return store.group(group_id)
        except UnknownGroupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _kelvin_range(group_id: int) -> ControlRange | None:
        """The members' ranges, intersected.

        Maximum of the minima, minimum of the maxima: a slider offering a
        span that half the group silently clamps is the same silent
        failure the lamp-controls design removed for a single device.

        An empty intersection (two members whose ranges do not overlap at
        all) yields `None`, and the caller then offers no slider - a
        degenerate span no value satisfies would be worse than none. The
        command itself stays in the group and stays exported; `/cmd` with
        an explicit value still reaches every member that accepts it.
        """
        lows: list[int] = []
        highs: list[int] = []
        for device in store.group_members(group_id):
            wanted = (_ATTR_CT_PHYS_MIN_MIREDS, _ATTR_CT_PHYS_MAX_MIREDS)
            keys = {
                signal.ref.element_id: signal.key
                for signal in store.signals(device.id)
                if signal.ref.cluster_id == _CLUSTER_COLOR and signal.ref.element_id in wanted
            }
            current = values.last_values_for(device.id)
            mireds: list[float] = []
            for element_id in wanted:
                key = keys.get(element_id)
                value = current.get(key) if key is not None else None
                if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                    mireds = []
                    break
                mireds.append(float(value))
            if not mireds:
                continue
            kelvins = sorted(int(1_000_000 / mired) for mired in mireds)
            lows.append(kelvins[0])
            highs.append(kelvins[1])

        if not lows:
            return None
        low, high = max(lows), min(highs)
        if low >= high:
            return None
        return ControlRange(min=low, max=high)

    @router.get("/groups")
    async def list_groups() -> list[GroupOut]:
        return [_out(group) for group in store.groups()]

    @router.post("/groups", status_code=201)
    async def create_group(body: GroupIn) -> GroupOut:
        try:
            group = store.create_group(body.label, body.member_ids, body.room)
        except (CategoryMismatchError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _out(group)

    @router.get("/groups/{group_id}")
    async def read_group(group_id: int) -> GroupOut:
        return _out(_require(group_id))

    @router.patch("/groups/{group_id}")
    async def patch_group(group_id: int, body: GroupPatch) -> GroupOut:
        _require(group_id)
        if body.label is not None:
            store.rename_group(group_id, body.label)
        if body.room is not None:
            store.set_group_room(group_id, body.room)
        return _out(store.group(group_id))

    @router.put("/groups/{group_id}/members")
    async def replace_members(group_id: int, body: GroupMembersIn) -> GroupOut:
        _require(group_id)
        try:
            store.set_group_members(group_id, body.member_ids)
        except CategoryMismatchError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _out(store.group(group_id))

    @router.delete("/groups/{group_id}", status_code=204)
    async def delete_group(group_id: int) -> None:
        _require(group_id)
        store.delete_group(group_id)

    @router.get("/groups/{group_id}/controls")
    async def group_controls(group_id: int) -> GroupControlsOut:
        _require(group_id)
        stored = store.group_commands(group_id)
        members = store.group_members(group_id)
        seed = members[0] if members else None
        named: list[CommandOut] = []
        for command in stored:
            if command_slug(command.cluster_id, command.command_id) is None:
                continue
            control = command_control(command.cluster_id, command.command_id)
            control_range = _kelvin_range(group_id) if control == "kelvin" else None
            if control == "kelvin" and control_range is None:
                # No usable common range - see `_kelvin_range`. The command
                # stays; only its slider has nothing to offer.
                control = None
            named.append(
                CommandOut(
                    key=command.key,
                    slug=command.slug,
                    takes_value=command.takes_value,
                    control=control,
                    range=control_range,
                )
            )
        return GroupControlsOut(
            commands=named,
            hidden_raw_commands=len(stored) - len(named),
            seed_device_id=seed.id if seed is not None else None,
            seed_device_label=seed.label if seed is not None else None,
        )

    return router
```

If `ValueReader` cannot be imported from `api/control.py` without a circular import, move that `Protocol` into `api/models.py` and import it from there in both files — check with `uv run mypy` after wiring.

- [ ] **Step 5: Wire the router**

In `src/loxmatter/loxone/server.py`, next to the other guarded routers:

```python
    # Same guard as every other `/api` router. `runtime` satisfies
    # `ValueReader` here for the same reason it does in the control
    # router - the group controls route reads last values, nothing more.
    app.include_router(build_groups_router(store, runtime), dependencies=api_guard)
```

and import `from loxmatter.api.groups import build_groups_router`.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run pytest tests/api/test_groups.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/api/groups.py src/loxmatter/api/models.py src/loxmatter/loxone/server.py tests/api/test_groups.py
git commit -m "feat(api): add the groups router

CRUD plus a controls route. No control endpoint of its own: a group is
driven through POST /api/commands/{key} like a device. Only the reading
half differs, because value ranges are intersected across the members and
the sliders' initial value is attributed to the member it came from."
```

---

### Task 8: Export templates

**Files:**
- Modify: `src/loxmatter/export/documents.py`, `src/loxmatter/export/outputs.py`, `src/loxmatter/projectsync/schema.py:159-172`, `src/loxmatter/api/export.py`, `src/loxmatter/cli.py`
- Test: `tests/export/test_group_outputs.py` (create)

**Interfaces:**
- Consumes: `StoredGroupCommand` (Task 2), `Store.groups`, `Store.group_commands`.
- Produces:
  - `documents.output_title(label: str) -> str` — `"Matter — {label}"`
  - `documents.group_output_title(label: str) -> str`
  - `documents.filename_for(prefix: str, owner_id: int, owner_label: str, *, kind: str = "d") -> str`
  - `documents.render_virtual_out(label, base_url, commands, *, is_group: bool = False) -> bytes`
  - `outputs.to_group_outputs(commands: Sequence[StoredGroupCommand]) -> list[LoxoneCommand]`

- [ ] **Step 1: Write the failing tests**

Create `tests/export/test_group_outputs.py` with the GPL header, then:

```python
"""Group templates - design 2026-09-10, section 7."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.export.documents import filename_for, render_virtual_out
from loxmatter.export.outputs import to_group_outputs
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
def group(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    members = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = load(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
        members.append(device_id)
    created = store.create_group("Living room", members)
    yield store, created
    store.close()


def test_a_group_filename_uses_the_g_prefix(group):
    _store, created = group
    assert filename_for("VO", created.id, created.label, kind="g") == f"VO_g{created.id}_Living_room.xml"


def test_a_device_filename_is_unchanged(group):
    """The default must stay exactly what it was - every existing export
    keeps its name."""
    assert filename_for("VO", 4, "Lamp 1") == "VO_d4_Lamp_1.xml"


def test_on_and_off_are_paired_without_an_endpoint(group):
    """`to_outputs` pairs over (endpoint, cluster); a group command has no
    endpoint, so the group variant pairs over the cluster alone."""
    store, created = group
    outputs = to_group_outputs(store.group_commands(created.id))
    combined = [o for o in outputs if o.off_path is not None]
    assert len(combined) == 1
    assert "/cmd/g" in combined[0].path
    assert "/cmd/g" in combined[0].off_path


def test_every_group_command_becomes_an_output(group):
    store, created = group
    outputs = to_group_outputs(store.group_commands(created.id))
    keys = {o.key for o in outputs}
    for command in store.group_commands(created.id):
        assert command.key in keys


def test_a_value_command_is_analog(group):
    store, created = group
    outputs = {o.key: o for o in to_group_outputs(store.group_commands(created.id))}
    level = next(c for c in store.group_commands(created.id) if c.slug == "level")
    assert outputs[level.key].analog is True
    assert "<v>" in outputs[level.key].path


def test_the_group_template_names_itself_a_group(group):
    store, created = group
    xml = render_virtual_out(
        created.label,
        "http://192.168.1.2:8080",
        to_group_outputs(store.group_commands(created.id)),
        is_group=True,
    ).decode("utf-8")
    assert "Group" in xml or "Gruppe" in xml
    assert created.label in xml
```

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run pytest tests/export/test_group_outputs.py -v
```

Expected: collection error, `ImportError: cannot import name 'to_group_outputs'`.

- [ ] **Step 3: Generalise `filename_for` and the titles**

In `src/loxmatter/export/documents.py`, change the signature and the one line that builds the stem:

```python
def filename_for(prefix: str, owner_id: int, owner_label: str, *, kind: str = "d") -> str:
    """Filename per spec 6.1, normalised to ASCII.

    `kind` distinguishes a device (`d`, the default and the shape every
    existing export already has) from a group (`g`, design 2026-09-10,
    section 7). It is a parameter rather than a second function because
    everything below it - the lossy normalisation, and the reason the ID
    must stay in the name - applies identically to both.
    """
```

and inside it, replace `stem = f"{prefix}_d{device_id}"` with `stem = f"{prefix}_{kind}{owner_id}"`, renaming the two other uses of `device_id`/`device_label` in the body to `owner_id`/`owner_label`. Leave the rest of the docstring as it is.

Add, above `render_virtual_out`:

```python
def output_title(label: str) -> str:
    """The `Title` of a virtual output, for the template AND for the
    container the project sync creates.

    One helper for both: the string used to exist as two separate format
    literals, here and in `projectsync.schema.new_output_container_open_tag`,
    which agreed only by hand. Groups would have made that four.
    """
    return f"Matter — {label}"


def group_output_title(label: str) -> str:
    return i18n.t("export.group_title", label=label)
```

In `render_virtual_out`, add the parameter and use the helpers:

```python
def render_virtual_out(
    device_label: str,
    base_url: str,
    commands: Sequence[LoxoneCommand],
    *,
    is_group: bool = False,
) -> bytes:
```

and replace the `("Title", f"Matter — {device_label}"),` line with:

```python
            ("Title", group_output_title(device_label) if is_group else output_title(device_label)),
```

In `src/loxmatter/projectsync/schema.py`, replace the inline literal in `new_output_container_open_tag`:

```python
        ("Title", output_title(device_label)),
```

with `from loxmatter.export.documents import output_title` at the top of that file. (`projectsync` already depends on `export` — `patch.py` imports `to_inputs`/`to_outputs`.)

- [ ] **Step 4: Generalise the pairing in `outputs.py`**

Replace the head of `to_outputs` and add the group variant:

```python
class OutputCommand(Protocol):
    """What `to_outputs` needs of a command: a key, a name and whether it
    carries a value. `StoredCommand` and `StoredGroupCommand` both satisfy
    it; the endpoint, which only one of them has, is supplied through
    `pair_key` instead of being read here."""

    key: str
    slug: str
    takes_value: bool


def _device_pair_key(command: StoredCommand) -> tuple[int, int]:
    return (command.endpoint, command.cluster_id)


def _group_pair_key(command: StoredGroupCommand) -> tuple[int, int]:
    """A group command has no endpoint (design 4.1), so the cluster alone
    decides what belongs together. `0` keeps the tuple shape so both keys
    are the same type."""
    return (0, command.cluster_id)


def to_outputs(
    commands: Sequence[OutputCommand],
    *,
    pair_key: Callable[[Any], tuple[int, int]] = _device_pair_key,
) -> list[LoxoneCommand]:
```

Inside the body, replace the grouping line with:

```python
        by_group.setdefault(pair_key(command), {})[command.slug] = command
```

and change the annotation of `by_group`, `pairs` and `result` from `StoredCommand` to `OutputCommand`. Add to the docstring, after the "Only what belongs together is paired" paragraph:

```
    `pair_key` says what "belongs together" means, because a GROUP command
    has no endpoint at all - there the cluster alone decides (design
    2026-09-10, section 7). A parameter rather than a second copy of this
    function: two copies would drift, and they would drift SILENTLY, since
    an unpaired `on` does not raise but simply passes through as its own
    output - the missing off-path would show up only as a switch in Loxone
    that never turns anything off.
```

Then:

```python
def to_group_outputs(commands: Sequence[StoredGroupCommand]) -> list[LoxoneCommand]:
    """A group's virtual outputs. See `to_outputs`, whose pairing rule
    this only re-parameterises."""
    return to_outputs(commands, pair_key=_group_pair_key)
```

Add the imports `from typing import Any, Protocol`, `from collections.abc import Callable` and `from loxmatter.model.store import StoredGroupCommand` at the top of `outputs.py`.

- [ ] **Step 5: Add the string**

```yaml
export.group_title:
  en: "Matter — Group: {label}"
  de: "Matter — Gruppe: {label}"
```

- [ ] **Step 6: Emit group templates from both export paths**

In `src/loxmatter/api/export.py`'s `download` route, after the loop that writes each device's two files, add a second loop:

```python
            for group in store.groups():
                commands = to_group_outputs(store.group_commands(group.id))
                if not commands:
                    # An emptied group has no outputs to offer. It keeps
                    # existing (design 4.3); it just has nothing to export.
                    continue
                archive.writestr(
                    filename_for("VO", group.id, group.label, kind="g"),
                    render_virtual_out(
                        group.label, f"http://{bridge_ip}:{listen}", commands, is_group=True
                    ),
                )
```

Match the surrounding code's exact `archive`/`zipfile` idiom — read lines 320-345 of that file first and mirror them, including how the device loop writes its entries.

In `src/loxmatter/cli.py`'s `export` command, after the per-device write loop, add the equivalent that writes to the output directory:

```python
    for group in store.groups():
        commands = to_group_outputs(store.group_commands(group.id))
        if not commands:
            continue
        target = out / filename_for("VO", group.id, group.label, kind="g")
        target.write_bytes(
            render_virtual_out(
                group.label, f"http://{bridge_ip}:{listen}", commands, is_group=True
            )
        )
```

Import `to_group_outputs` in both files.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
uv run pytest tests/export tests/api/test_export_api.py tests/test_export_cli.py -v
```

Expected: PASS, including every pre-existing export test — the device path must be byte-identical to before.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/export src/loxmatter/projectsync/schema.py src/loxmatter/api/export.py src/loxmatter/cli.py src/loxmatter/i18n/strings.yaml tests/export/test_group_outputs.py
git commit -m "feat(export): write a template per group

to_outputs takes its pairing key as a parameter instead of gaining a
copy: a group command has no endpoint, so the cluster alone decides what
pairs. Two copies would drift silently, because an unpaired on does not
raise, it just becomes its own output.

The output title now comes from one helper. It used to be two format
literals that agreed by hand; groups would have made it four."
```

---

### Task 9: Project sync

**Files:**
- Modify: `src/loxmatter/projectsync/diff.py`, `src/loxmatter/projectsync/patch.py`, `src/loxmatter/projectsync/sync.py`
- Test: `tests/projectsync/test_group_sync.py` (create)

**Interfaces:**
- Consumes: `to_group_outputs` (Task 8), `Store.groups`, `Store.group_commands`, `StoredGroup`.
- Produces:
  - `PlanEntry.owner_kind: str` (`"device"` or `"group"`, default `"device"`)
  - `build_plan(index, devices, signals_by_device, commands_by_device, groups=(), commands_by_group=None) -> SyncPlan`
  - `apply_plan(..., groups=(), commands_by_group=None, ...)`

- [ ] **Step 1: Write the failing tests**

Create `tests/projectsync/test_group_sync.py` with the GPL header. The project fixture is `sample_project` from `tests/projectsync/conftest.py` — a `str` holding one `LoxLIVE` block. It is picked up automatically as a parameter; do not build your own.

```python
"""Groups in the project sync - design 2026-09-10, section 8."""

from __future__ import annotations

from loxmatter.projectsync.diff import PlanStatus, build_plan
from loxmatter.projectsync.index import build_index


def test_a_group_appears_as_a_new_device_container(sample_project, group_store):
    """A group with no output in the project yet is NEW_DEVICE, the same
    status a never-exported device gets - it needs its own container."""
    store, group = group_store
    index = build_index(sample_project)
    plan = build_plan(
        index,
        store.devices(),
        {},
        {},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
    )
    group_entries = [e for e in plan.entries if e.owner_kind == "group"]
    assert group_entries
    assert all(e.status is PlanStatus.NEW_DEVICE for e in group_entries)


def test_a_group_id_does_not_collide_with_a_device_id(sample_project, group_store):
    """Both counters start at 1. Keying the new-container grouping by ID
    alone would merge a group's outputs into a device's container."""
    store, group = group_store
    index = build_index(sample_project)
    plan = build_plan(
        index,
        store.devices(),
        {d.id: store.signals(d.id) for d in store.devices()},
        {d.id: store.commands(d.id) for d in store.devices()},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
    )
    owners = {(e.owner_kind, e.device_id) for e in plan.entries}
    assert ("group", group.id) in owners
    assert any(kind == "device" for kind, _ in owners)


def test_a_patched_project_keeps_matching_after_a_rename(sample_project, group_store):
    """Containers are matched by key, and keys come from the ID - so a
    rename leaves a stale title, not a broken sync (design 7)."""
    from loxmatter.projectsync.patch import apply_plan

    store, group = group_store
    index = build_index(sample_project)
    plan = build_plan(
        index, [], {}, {}, groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
    )
    patched = apply_plan(
        index, plan, [], {}, {},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
        include_new_devices=True,
        bridge_ip="192.168.1.2",
        port=7000,
        listen=8080,
    )
    store.rename_group(group.id, "Something else")
    second_index = build_index(patched.decode("utf-8"))
    second = build_plan(
        second_index, [], {}, {}, groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
    )
    assert not any(e.status is PlanStatus.NEW_DEVICE for e in second.entries)
```

Write a `group_store` fixture in the same file that builds the two lamps and one group, following `tests/model/test_store_groups.py`'s `lamps_with_commands`.

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run pytest tests/projectsync/test_group_sync.py -v
```

Expected: FAIL, `TypeError: build_plan() got an unexpected keyword argument 'groups'`.

- [ ] **Step 3: Add `owner_kind` and generalise `_plan_outputs`**

In `src/loxmatter/projectsync/diff.py`, add the field to `PlanEntry` **at the end**, with a default, so every existing positional construction keeps working:

```python
    # "device" or "group" (design 2026-09-10, section 8). A group and a
    # device can carry the SAME numeric id - both counters start at 1 -
    # so anything that groups entries by owner must key on this as well,
    # or a group's outputs land in a device's container.
    owner_kind: str = "device"
```

Replace `_plan_outputs`'s signature and prefix:

```python
def _plan_outputs(
    index: ProjectIndex,
    owner_kind: str,
    owner_id: int,
    owner_label: str,
    commands: Sequence[LoxoneCommand],
) -> list[PlanEntry]:
    prefix = f"{'g' if owner_kind == 'group' else 'd'}{owner_id}_"
```

and inside it replace every `device.id` with `owner_id`, every `device.label` with `owner_label`, and add `owner_kind` as the last argument of each `PlanEntry(...)` construction.

Then extend `build_plan`:

```python
def build_plan(
    index: ProjectIndex,
    devices: Sequence[StoredDevice],
    signals_by_device: dict[int, Sequence[StoredSignal]],
    commands_by_device: dict[int, Sequence[StoredCommand]],
    groups: Sequence[StoredGroup] = (),
    commands_by_group: dict[int, Sequence[StoredGroupCommand]] | None = None,
) -> SyncPlan:
    entries: list[PlanEntry] = []
    known_input_keys: set[str] = set()
    known_output_keys: set[str] = set()

    for device in devices:
        inputs = to_inputs(signals_by_device.get(device.id, []), device.id, device.label)
        outputs = to_outputs(commands_by_device.get(device.id, []))
        known_input_keys.update(entry.key for entry in inputs)
        known_output_keys.update(command.key for command in outputs)
        entries += _plan_inputs(index, device, inputs)
        entries += _plan_outputs(index, "device", device.id, device.label, outputs)

    # Groups have outputs only - no signals, no inputs (design 2).
    for group in groups:
        outputs = to_group_outputs((commands_by_group or {}).get(group.id, []))
        known_output_keys.update(command.key for command in outputs)
        entries += _plan_outputs(index, "group", group.id, group.label, outputs)

    entries += _orphaned_entries(index, known_input_keys, known_output_keys)
    return SyncPlan(entries)
```

Import `StoredGroup`, `StoredGroupCommand` and `to_group_outputs`.

- [ ] **Step 4: Teach `apply_plan` about group containers**

In `src/loxmatter/projectsync/patch.py`:

1. Add the same two optional parameters to `apply_plan` (`groups: Sequence[StoredGroup] = ()`, `commands_by_group: dict[int, Sequence[StoredGroupCommand]] | None = None`).
2. After the loop that fills `desired_outputs` from devices, add:

```python
    for group in groups:
        for output_item in to_group_outputs((commands_by_group or {}).get(group.id, [])):
            desired_outputs[output_item.key] = output_item
```

3. Change the new-container grouping key so a group id cannot collide with a device id:

```python
    # (kind, owner_kind, id) - NOT (kind, id): both counters start at 1,
    # so group 1 and device 1 would otherwise share one container.
    new_device_groups: dict[tuple[str, str, int], list[PlanEntry]] = {}
```

with the two places that use it updated:

```python
            new_device_groups.setdefault(
                (entry.kind, entry.owner_kind, entry.device_id), []
            ).append(entry)
```

```python
    for (kind, _owner_kind, _owner_id), group_entries in new_device_groups.items():
```

4. In `_new_device_edit`, pick the title by owner kind:

```python
        container_open = new_output_container_open_tag(
            first.device_label,
            f"http://{bridge_ip}:{listen}",
            container_iname,
            container_u,
            is_group=first.owner_kind == "group",
        )
```

and give `new_output_container_open_tag` that keyword in `projectsync/schema.py`:

```python
def new_output_container_open_tag(
    device_label: str, base_url: str, iname: str, u: str, *, is_group: bool = False
) -> str:
    """Like `new_input_container_open_tag`, for `VirtualOut`."""
    attrs = [
        ("Type", "VirtualOut"),
        ("IName", iname),
        ("V", "178"),
        ("U", u),
        ("Title", group_output_title(device_label) if is_group else output_title(device_label)),
        ...
```

importing `group_output_title` alongside `output_title`.

- [ ] **Step 5: Pass groups through `run_sync`**

In `src/loxmatter/projectsync/sync.py`, read the groups from the store the same way it already reads devices, and hand them to both `build_plan` and the two `apply_plan` calls:

```python
    groups = store.groups()
    commands_by_group = {group.id: store.group_commands(group.id) for group in groups}
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
uv run pytest tests/projectsync -v
```

Expected: PASS, including every pre-existing sync test.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/projectsync tests/projectsync/test_group_sync.py
git commit -m "feat(projectsync): patch group outputs into a project file

PlanEntry gains owner_kind, and the new-container grouping keys on it:
a group id and a device id can be the same number, and without it a
group's outputs would land inside a device's container.

Containers keep being matched by key, so renaming a group leaves a stale
title rather than a broken sync."
```

---

### Task 10: The WebUI

**Files:**
- Modify: `src/loxmatter/web/app.js`, `src/loxmatter/web/index.html`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py` (extend)

**Interfaces:**
- Consumes: every `/api/groups` route from Task 7 and `POST /api/commands/{key}` from Task 6.
- Produces: nothing other code depends on.

**Before starting:** read the existing device tile block in `index.html` (`x-for` over the visible devices, the kebab menu, the control bar `device-commands`) and the corresponding Alpine state in `app.js` (`devices`, `roomChips`, `visibleDevices`, `roomKeyOf`, `categoryLabel`, the command-sending function). The group tile is that tile with a different data source — copy its structure rather than inventing a second style.

- [ ] **Step 1: Add the state and the fetches**

In `app.js`, next to `devices`:

```javascript
    // Groups (design 2026-09-10, section 6). A separate list, not mixed
    // into `devices`: a group has no node, so every field the device
    // tile reads from a node (online, last heard, signals) is absent, and
    // a merged list would need a guard at each of them.
    groups: [],
    // The group whose member editor is open, or null.
    groupEditor: null,
    // Draft state of the create/edit dialog.
    groupDraft: { label: "", room: "", memberIds: [] },
```

and the loader, called wherever `devices` is loaded:

```javascript
    async loadGroups() {
      const response = await this.api("/api/groups");
      this.groups = response.ok ? await response.json() : [];
    },
```

- [ ] **Step 2: Make groups part of the room chips and the search**

`roomChips()` counts devices today. Extend it to count groups as well, so a room chip's number matches what the grid shows, and add groups to the filtered list:

```javascript
    // A group carries its OWN room (design 6): it is not derived from the
    // members, so it does not move on its own when a lamp is re-roomed.
    roomKeyOfGroup(group) {
      return group.room || "";
    },

    visibleGroups() {
      const needle = this.search.trim().toLowerCase();
      return this.groups.filter(
        (group) =>
          (this.roomFilter === null || this.roomKeyOfGroup(group) === this.roomFilter) &&
          (needle === "" ||
            [group.label, this.categoryLabelOf(group.category), this.roomKeyOfGroup(group)]
              .join(" ")
              .toLowerCase()
              .includes(needle))
      );
    },
```

Mirror the exact predicate `visibleDevices` uses — read it first; the room filter and the search field act together (AND), and the group list must behave identically or the two halves of one grid will filter differently.

- [ ] **Step 3: Render the group tiles**

In `index.html`, immediately before the device `x-for`, add the group `x-for` using the same tile markup with these differences: a badge showing `t('web.groups.badge')`, a member count `t('web.groups.member_count', {count: group.member_ids.length})`, no online dot, no "last heard" line, no signals link. The kebab menu gets `t('web.groups.edit_members')`, `t('web.groups.rename')`, `t('web.groups.room')` and `t('web.groups.delete')`.

The control bar loads from `/api/groups/{id}/controls` and posts to `/api/commands/{key}` — the same posting function the device tile uses, unchanged. Under a slider, render the attribution when `seed_device_label` is present:

```html
<p class="hint" x-show="controls.seed_device_label"
   x-text="t('web.groups.seed_from', { device: controls.seed_device_label })"></p>
```

- [ ] **Step 4: The creation dialog**

A "New group" button beside the search field opens a dialog with a name field, a room field (reusing the room input the device kebab menu already has) and a member list. The member list greys out every device whose category differs from the first selected one:

```javascript
    // The first pick fixes the category; everything else of another kind
    // is disabled WITH a reason on the entry, not silently (design 6).
    groupCandidateState(device) {
      if (this.groupDraft.memberIds.length === 0) return { disabled: false, reason: "" };
      const first = this.devices.find((d) => d.id === this.groupDraft.memberIds[0]);
      if (!first || first.category === device.category) return { disabled: false, reason: "" };
      return {
        disabled: true,
        reason: this.t("web.groups.other_category", {
          category: this.categoryLabelOf(device.category),
        }),
      };
    },

    // Prefilled from the members when they agree, and the user's from
    // then on.
    groupRoomSuggestion() {
      const rooms = new Set(
        this.groupDraft.memberIds
          .map((id) => this.devices.find((d) => d.id === id))
          .filter(Boolean)
          .map((d) => d.room || "")
      );
      return rooms.size === 1 ? [...rooms][0] : "";
    },
```

Saving posts to `/api/groups`, and on a 400 shows the response's `detail` — the store's message already names the offending device and both categories.

- [ ] **Step 5: Add the strings**

```yaml
web.groups.badge:
  en: "Group"
  de: "Gruppe"
web.groups.new:
  en: "New group"
  de: "Neue Gruppe"
web.groups.member_count:
  en: "{count} devices"
  de: "{count} Geräte"
web.groups.edit_members:
  en: "Edit members…"
  de: "Mitglieder bearbeiten…"
web.groups.rename:
  en: "Rename…"
  de: "Umbenennen…"
web.groups.room:
  en: "Room…"
  de: "Raum…"
web.groups.delete:
  en: "Delete group"
  de: "Gruppe löschen"
web.groups.other_category:
  en: "is a {category} — a group takes one kind only"
  de: "ist ein {category} — eine Gruppe nimmt nur eine Sorte"
web.groups.seed_from:
  en: "Initial value from {device}"
  de: "Ausgangswert von {device}"
web.groups.empty:
  en: "No groups yet"
  de: "Noch keine Gruppen"
```

- [ ] **Step 6: Verify the bindings actually run**

A test that asserts the HTML contains a string only proves it was **delivered**, not that Alpine evaluates it. Build a throwaway harness under the scratchpad directory that loads `index.html` plus the vendored Alpine against a stub `fetch` returning two groups and one device, and check in a browser that:

- both group tiles render with their badge and member count,
- the room chips count groups as well as devices,
- selecting a lamp in the dialog greys out the plug with its reason,
- pressing a group's `on` button issues one `POST /api/commands/g<id>_on`.

Delete the harness afterwards; it is not part of the repository.

Then add the delivery-level test to `tests/api/test_web.py` in the style already there:

```python
async def test_the_group_markup_is_delivered(api):
    client = api
    body = (await client.get("/static/index.html")).text
    assert "web.groups.badge" in body
    assert "/api/groups" in (await client.get("/static/app.js")).text
```

- [ ] **Step 7: Run the tests and commit**

```bash
uv run pytest tests/api/test_web.py -v && uv run python scripts/check_language.py
```

```bash
git add src/loxmatter/web src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(web): show groups as tiles beside the devices

A group carries its own room rather than deriving one from its members,
so it does not wander to another chip when a lamp is re-roomed. The tile
deliberately has no online dot, no last-heard and no signal list: those
are properties of a node, and a group is not one."
```

---

### Task 11: Documentation and the full check

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the changelog entry**

Follow the existing format of `CHANGELOG.md` exactly — read the most recent released section first. The entry names the feature and the one limitation a user needs to know:

```markdown
### Added

- **Device groups.** Several devices of the same category can be driven
  from a single Loxone virtual output. The bridge fans the command out to
  every member; if one does not answer, the others are still switched and
  the log names the one that failed. Groups are software groups inside the
  bridge — Matter's own group messaging is not exposed by any Matter
  server this bridge can use, so nothing is created on the devices
  themselves.
```

- [ ] **Step 2: Run every check CI runs**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -v
uv run python scripts/check_language.py
```

The pytest run takes about 8 minutes. Every one of the five must pass. If `ruff format --check` reports a Markdown file, that is the fenced Python inside a document being reformatted — run `uv run ruff format .` and inspect the diff before accepting it.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): record device groups"
```

- [ ] **Step 4: Hand back**

Report which of the five checks passed, with the pytest summary line quoted verbatim. Do not claim the feature works on hardware — nothing in this plan touches a real Thread network, and the concurrency behaviour under a real mesh is an open point (design section 12, point 1).

---

## Notes for the Implementer

**Read the design document.** Every task references its section numbers, and several decisions here look arbitrary without the reasoning there — particularly why the fan-out is sequential within a member (section 3.1), why a command dropping out of the intersection answers 404 rather than disappearing (4.3), and why the group tile shows less than a device tile (2).

**The three places German is still correct** are listed in `CLAUDE.md`. In this plan that means exactly one thing: the `de:` values in `strings.yaml`. Everything else — including test names and commit messages — is English.

**If a test in this plan is wrong, fix the test, but say so.** These were written against the code as it stood on 10 September 2026 without being executed. A test that does not compile is a defect in this plan, not a licence to skip the behaviour it was checking.
