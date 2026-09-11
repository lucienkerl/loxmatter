# Device Source Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the Matter client behind a technology-neutral `DeviceSource` interface, replace the Matter node ID as device identity with technology plus address, and show a Thread/IP badge on every device tile — with no other change a user can see.

**Architecture:** Matter's data model stays loxmatter's internal language; `NodeSnapshot`, `SignalRef`, discovery, profiles, runtime, export and groups are untouched in behaviour. A new package `loxmatter/sources/` holds `DeviceCall` (formerly `MatterCall`), the `DeviceSource` protocol, the `Sources` registry that dispatches by technology, and the connection supervisor. The store moves to schema version 9 (`device.technology`, `device.address`, `device.network_features`; `node_id` dropped from `device` and `command`). The tile badge is derived when read, from the raw NetworkCommissioning FeatureMap.

**Tech Stack:** Python 3.12, FastAPI, SQLite (3.46.1 in the image), Alpine.js (vendored, no build step), pytest with `asyncio_mode = "auto"`, node for `app.js` tests, Playwright for the layout check.

**Spec:** `docs/superpowers/specs/2026-09-11-device-source-boundary-design.md` — read sections 2–7 before starting any task.

## Global Constraints

- Worktree (absolute path, use it in every command): `/Users/lucienkerl/Development/matter-loxone/.claude/worktrees/german-to-english-translation-f84003`
- Everything is written in English: code, comments, docstrings, test names, commit messages. German appears only as `de:` values in `src/loxmatter/i18n/strings.yaml`.
- Every string a user can see goes through `i18n.t(...)` with an `en` and a `de` value. `web.*` values must not contain `{placeholders}` (`GET /api/i18n` calls `t(key)` without values and would fail for all keys).
- New source and test files start with the 15-line GPL header, copied verbatim from `src/loxmatter/profiles/categories.py` lines 1–15.
- Schema version goes from **8 to 9**. SQLite in `python:3.12-slim` and in the running Pi container is **3.46.1** (measured 11 September 2026), so `ALTER TABLE … DROP COLUMN` is available; no table rebuild.
- A Matter device's `address` is `str(node_id)`. Integer node IDs stay inside `src/loxmatter/matter/client.py`; everything else uses `technology` + `address`.
- `unique_id` stays as it is for Matter, including the fallback `node:<address>` (identical text to today's `node:<node_id>`).
- No new dependency, no change to `deploy/` or `install.sh`, no Zigbee code.
- **Every new test that names a protection must be shown to catch it:** introduce the stated fault, run the test, see it FAIL, revert the fault, see it PASS. Paste both outputs into the task report. A test that stays green with the fault in place is wrong and must be rewritten.
- Some tasks leave a marked `# TRANSITIONAL (Task N)` expression that a later task removes. Task 9 greps that none remain.
- The full `uv run pytest` takes about eight minutes. That is not a hang.
- Run `uv run ruff format .` before the checks; several code blocks below are shown unwrapped and the formatter owns line breaks.
- A comment or docstring that names a renamed thing (`MatterCall`, `to_matter_calls`, `send_command`, `remove_node`, `follow_node`, `device_id_for_node`, `matter.supervisor`) is updated in the same task as the rename. A stale name in prose misleads the next reader as much as stale code. History quotations (test IDs recorded in old specs under `docs/`) stay as they are.
- Checks CI runs (all must pass at the end of every task): `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest -q`, `uv run python scripts/check_language.py`.
- Commit messages: Conventional Commits, English, ending with the line `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## File Map

| File | Responsibility | Task |
|---|---|---|
| `src/loxmatter/profiles/transport.py` (new) | `transport_for`, `network_features_of` | 1 |
| `src/loxmatter/matter/models.py` | `Technology`, `parse_technology`; `NodeSnapshot.technology/address` | 2, 3 |
| `src/loxmatter/model/store.py` | schema 9, migration, identity API, `backfill_network_features` | 2, 3 |
| `src/loxmatter/sources/__init__.py` (new) | `DeviceCall`, `RuntimeEventHandler`, `DeviceSource`, `Sources`, `SourceNotConfiguredError` | 4, 5 |
| `src/loxmatter/sources/supervisor.py` (moved from `matter/`) | `attach`, `supervise` over any `DeviceSource` | 6 |
| `src/loxmatter/matter/client.py` | conforms to `DeviceSource` | 3, 4, 5 |
| `src/loxmatter/commands/translate.py`, `commands/fanout.py` | produce/consume `DeviceCall` | 2, 4 |
| `src/loxmatter/loxone/server.py`, `api/control.py`, `api/devices.py`, `api/models.py` | wiring, 503 mapping, `DeviceOut` | 2, 4, 5, 7, 8 |
| `src/loxmatter/loxone/runtime.py` | identity lookup | 2, 3 |
| `src/loxmatter/cli.py` | startup wiring | 2, 3, 4, 5, 6 |
| `scripts/dev_web_server.py`, `scripts/record_node.py` | follow renames (mypy checks `scripts/`) | 2, 3, 4 |
| `src/loxmatter/web/index.html`, `app.js`, `style.css` | tile badge | 8 |
| `src/loxmatter/i18n/strings.yaml` | new keys | 5, 8 |
| `CHANGELOG.md` | user-facing note | 8 |

---

### Task 1: The Transport Rule

**Files:**
- Create: `src/loxmatter/profiles/transport.py`
- Test: `tests/profiles/test_transport.py`

**Interfaces:**
- Consumes: `loxmatter.matter.models.NodeSnapshot` (unchanged).
- Produces: `Transport = Literal["thread", "ip", "zigbee"]`; `NETWORK_FEATURES_PATH = "0/49/65532"`; `network_features_of(snapshot: NodeSnapshot) -> int | None`; `transport_for(technology: str, network_features: int | None) -> Transport | None`.

- [ ] **Step 0: Baseline.** Before touching anything, confirm the merged branch is green so later failures are yours:

Run: `cd /Users/lucienkerl/Development/matter-loxone/.claude/worktrees/german-to-english-translation-f84003 && uv run pytest -q`
Expected: all pass (about eight minutes). If anything fails, stop and report — do not start Task 1 on a red suite.

- [ ] **Step 1: Write the failing test** `tests/profiles/test_transport.py` (GPL header first):

```python
"""Thread or IP, read from NetworkCommissioning's FeatureMap (design
2026-09-11, section 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot
from loxmatter.profiles.transport import network_features_of, transport_for

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def _fixture(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.mark.parametrize(
    ("technology", "features", "expected"),
    [
        # Measured on the test Pi, 11 September 2026:
        ("matter", 2, "thread"),  # every IKEA device, all on Thread
        ("matter", 4, "ip"),  # Tasmota-Plug-4, on Wi-Fi, reports Ethernet
        ("matter", 5, "ip"),  # Tasmota-Plug-6, on Wi-Fi, reports Wi-Fi + Ethernet
        # Not measured, follow from the bit layout:
        ("matter", 1, "ip"),  # Wi-Fi bit alone
        ("matter", 3, "thread"),  # Thread wins over an IP bit
        ("matter", 6, "thread"),
        ("matter", 0, None),  # no network interface bit at all
        ("matter", None, None),  # attribute missing
        ("zigbee", None, "zigbee"),
        ("zigbee", 2, "zigbee"),  # a Zigbee device's features are irrelevant
    ],
)
def test_transport_for(technology, features, expected):
    assert transport_for(technology, features) == expected


def test_network_features_are_read_from_a_real_thread_device():
    assert network_features_of(_fixture("ikea_kajplats_ws_lamp.json")) == 2


def test_a_device_without_network_commissioning_has_no_features():
    assert network_features_of(_fixture("example_light.json")) is None


@pytest.mark.parametrize("value", [True, "2", 2.0, None, [2]])
def test_only_a_plain_integer_counts_as_features(value):
    snapshot = NodeSnapshot.from_raw(1, {"attributes": {"0/49/65532": value}})
    assert network_features_of(snapshot) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/profiles/test_transport.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.profiles.transport'`.

- [ ] **Step 3: Write the implementation** `src/loxmatter/profiles/transport.py` (GPL header first):

```python
"""How a device is connected: Thread, IP or Zigbee (design 2026-09-11,
section 5).

Sits next to `categories.py` for the reason `categories.py` exists: the
answer is derived when read, from a raw value the store keeps
(`device.network_features`), so a better rule later is a code change and
not a migration.

**The evidence**, read from the live matter-server on the test Pi on 11
September 2026:

| Nodes | Devices | Actual link | `0/49/65532` |
|---|---|---|---|
| 4, 8, 11-16, 21, 22 | IKEA BILRESA, GRILLPLATS, MYGGBETT, ALPSTUGA, MYGGSPRAY, KAJPLATS (x3), TIMMERFLOTTE, KLIPPBOK | Thread | 2 |
| 23 | Tasmota-Plug-4 | Wi-Fi | 4 (Ethernet bit) |
| 24 | Tasmota-Plug-6 | Wi-Fi | 5 (Wi-Fi and Ethernet bits) |

NetworkCommissioning's FeatureMap has bit 0 for Wi-Fi, bit 1 for Thread,
bit 2 for Ethernet. Both Tasmota plugs are on Wi-Fi and neither says so
correctly - so Wi-Fi and Ethernet are not told apart here, only Thread and
IP.

**Thread wins when both kinds of bit are set.** That is a judgement, not a
measurement: no device reporting the Thread bit together with an IP bit
has been seen, but the Tasmota plugs show IP bits set without meaning,
while no false Thread bit has been observed. Revisit the rule, not the
stored data, when a counterexample turns up.
"""

from __future__ import annotations

from typing import Final, Literal

from loxmatter.matter.models import NodeSnapshot

Transport = Literal["thread", "ip", "zigbee"]

# Endpoint 0, NetworkCommissioning (0x0031), FeatureMap (0xFFFC). Discovery
# never turns this into a signal - FeatureMap is one of the global
# attributes `extract_signals` skips - so it is read here directly.
NETWORK_FEATURES_PATH: Final = "0/49/65532"

_WIFI_BIT: Final = 0x1
_THREAD_BIT: Final = 0x2
_ETHERNET_BIT: Final = 0x4


def network_features_of(snapshot: NodeSnapshot) -> int | None:
    """The raw FeatureMap, or `None` when the device reports none.

    `bool` is excluded explicitly: it is a subclass of `int` in Python, and
    `True` would otherwise read as "Wi-Fi"."""
    value = snapshot.attributes.get(NETWORK_FEATURES_PATH)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def transport_for(technology: str, network_features: int | None) -> Transport | None:
    if technology == "zigbee":
        return "zigbee"
    if network_features is None:
        return None
    if network_features & _THREAD_BIT:
        return "thread"
    if network_features & (_WIFI_BIT | _ETHERNET_BIT):
        return "ip"
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/profiles/test_transport.py -q`
Expected: PASS (17 tests).

- [ ] **Step 5: Prove the tests catch the fault.** In `transport_for`, swap the two checks' bit masks (`network_features & (_WIFI_BIT | _ETHERNET_BIT)` first returning `"thread"`, `_THREAD_BIT` returning `"ip"`). Run the test file: expected FAIL on at least the `2`, `4`, `5` cases. Revert, run again: PASS. Then remove the `isinstance(value, bool)` guard: expected FAIL on `True`. Revert. Paste all four outputs into the report.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/profiles/transport.py tests/profiles/test_transport.py
git commit -m "feat(profiles): derive Thread or IP from NetworkCommissioning's FeatureMap

Tasmota plugs on Wi-Fi report the Ethernet bit, so only Thread and IP are
distinguished. The rule reads a raw value so it can improve without a
migration.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Device Identity in the Store (Schema 9)

**Files:**
- Modify: `src/loxmatter/matter/models.py` (add `Technology`, `parse_technology`)
- Modify: `src/loxmatter/model/store.py` (schema, migration 9, `StoredDevice`, `StoredCommand`, identity API, backfills)
- Modify: `src/loxmatter/loxone/runtime.py` (`seed_from_snapshot`)
- Modify: `src/loxmatter/matter/supervisor.py` (`attach`)
- Modify: `src/loxmatter/commands/translate.py` (reads `command.address`)
- Modify: `src/loxmatter/api/devices.py`, `src/loxmatter/api/models.py` (`DeviceOut`)
- Modify: `src/loxmatter/cli.py`, `scripts/dev_web_server.py` (`register_commands` calls)
- Modify tests: `tests/model/test_store_migration.py`, `tests/model/test_store.py`, `tests/api/conftest.py`, `tests/matter/test_supervisor.py`, and every test hit by the rules in Step 9
- Create: `tests/model/test_store_identity.py`

**Interfaces:**
- Consumes: `network_features_of` from Task 1.
- Produces:
  - `loxmatter.matter.models.Technology = Literal["matter", "zigbee"]`
  - `loxmatter.matter.models.parse_technology(value: str) -> Technology` (raises `ValueError` for anything else)
  - `StoredDevice(id: int, technology: Technology, address: str, unique_id: str, label: str, exported_at: str | None, updated_at: str | None, room: str | None, device_types: dict[int, frozenset[int]] | None, network_features: int | None)`
  - `StoredCommand(key: str, slug: str, technology: Technology, address: str, endpoint: int, cluster_id: int, command_id: int, takes_value: bool, device_id: int)`
  - `Store.device_id_for(technology: str, address: str) -> int | None` (replaces `device_id_for_node`)
  - `Store.register_commands(device_id: int, commands: Sequence[DeviceCommand]) -> list[StoredCommand]` (third parameter gone)
  - `Store.backfill_network_features(snapshots: Sequence[NodeSnapshot]) -> int`
  - `DeviceOut` has `technology: str` and `address: str` instead of `node_id: int`

- [ ] **Step 1: Add `Technology` to `src/loxmatter/matter/models.py`.** Change the imports and add below `_UNIQUE_ID_PATH`:

```python
from typing import Any, Final, Literal, cast, get_args
```

```python
# Which kind of source a device comes from (design 2026-09-11, section
# 3.1). Lives here, next to `NodeSnapshot`, and not in `loxmatter.sources`:
# `sources` imports this module, so the reverse import would be a cycle.
Technology = Literal["matter", "zigbee"]

_TECHNOLOGIES: Final = frozenset(get_args(Technology))


def parse_technology(value: str) -> Technology:
    """Narrows a stored string to `Technology`, loudly.

    A value the code does not know means the database was written by a
    newer loxmatter than the one reading it; reading on as if it were
    Matter would send Matter commands to a device that is not one."""
    if value not in _TECHNOLOGIES:
        raise ValueError(f"unknown device technology {value!r}")
    return cast(Technology, value)
```

- [ ] **Step 2: Write the failing tests** `tests/model/test_store_identity.py` (GPL header first):

```python
"""Device identity as technology plus address (design 2026-09-11,
section 4)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot, parse_technology
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"

# The `device` and `command` tables exactly as schema version 8 created
# them - copied from `_SCHEMA` at commit 13541c4, not derived from today's.
_V8_DEVICE_AND_COMMAND = """
CREATE TABLE device (
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
CREATE TABLE signal (
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
CREATE TABLE command (
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


def _fixture(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def _columns(path: Path, table: str) -> set[str]:
    db = sqlite3.connect(path)
    try:
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    finally:
        db.close()


def _user_version(path: Path) -> int:
    db = sqlite3.connect(path)
    try:
        return int(db.execute("PRAGMA user_version").fetchone()[0])
    finally:
        db.close()


def _build_v8_database(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(_V8_DEVICE_AND_COMMAND)
    db.execute(
        "INSERT INTO device (id, unique_id, node_id, label, udp_port)"
        " VALUES (1, 'CAA68CE5422C3C29', 23, 'Lamp', 7000)"
    )
    db.execute(
        "INSERT INTO signal (device_id, endpoint, cluster_id, element_id, kind, key,"
        " title, unit, exportability) VALUES (1, 1, 6, 0, 'attribute', 'd1_1_onoff',"
        " 'On', '', 'digital')"
    )
    db.execute(
        "INSERT INTO command (device_id, node_id, endpoint, cluster_id, command_id, key,"
        " slug, takes_value) VALUES (1, 23, 1, 6, 1, 'd1_1_on', 'on', 0)"
    )
    db.execute("PRAGMA user_version = 8")
    db.commit()
    db.close()


def test_a_v8_database_moves_node_ids_into_addresses(tmp_path):
    """Protects: the `address` backfill, the two dropped columns, and that
    no Loxone key changes. Fault to prove it: comment out the `UPDATE
    device SET address = ...` line in `_migrate_to_v9`."""
    path = tmp_path / "v8.sqlite"
    _build_v8_database(path)

    store = Store(path)
    try:
        assert _user_version(path) == 9
        device = store.device(1)
        assert (device.technology, device.address) == ("matter", "23")
        assert store.device_id_for("matter", "23") == 1
        assert store.signal_by_key("d1_1_onoff") is not None
        command = store.resolve_command("d1_1_on")
        assert (command.technology, command.address) == ("matter", "23")
    finally:
        store.close()
    assert "node_id" not in _columns(path, "device")
    assert "node_id" not in _columns(path, "command")


def test_a_fresh_database_runs_migration_9_without_duplicate_column(tmp_path):
    """A fresh database already has the v9 columns from `_SCHEMA` and still
    runs the chain from `user_version = 0`. Fault to prove it: replace the
    `_add_column_if_missing(db, "device", "technology", ...)` call in
    `_migrate_to_v9` with a bare `db.execute("ALTER TABLE device ADD COLUMN
    technology TEXT NOT NULL DEFAULT 'matter'")`."""
    path = tmp_path / "fresh.sqlite"
    Store(path).close()
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version = 8")
    db.commit()
    db.close()

    Store(path).close()

    assert _user_version(path) == 9


def test_a_failing_migration_9_leaves_version_8_intact(tmp_path, monkeypatch):
    """Protects: migration 9 runs inside `_migrate`'s transaction. The
    stand-in fails AFTER the real migration has written, not before - a
    failure before any write would pass without a transaction too. Fault
    to prove it: add `db.commit()` as the first line after the
    `_add_column_if_missing(db, "device", "technology", ...)` call in
    `_migrate_to_v9`."""
    from loxmatter.model import store as store_module

    path = tmp_path / "v8.sqlite"
    _build_v8_database(path)

    def migrate_then_crash(db: sqlite3.Connection) -> None:
        store_module._migrate_to_v9(db)
        raise RuntimeError("simulated crash after migration 9 wrote")

    migrations = dict(store_module._MIGRATIONS)
    migrations[9] = migrate_then_crash
    monkeypatch.setattr(store_module, "_MIGRATIONS", migrations)

    with pytest.raises(RuntimeError):
        Store(path)

    assert _user_version(path) == 8
    assert "node_id" in _columns(path, "device")
    assert "technology" not in _columns(path, "device")


def test_device_id_for_tells_technologies_apart(tmp_path):
    """Fault to prove it: drop `technology = ? AND` from the query in
    `device_id_for` (and its parameter)."""
    store = Store(tmp_path / "s.sqlite")
    try:
        snapshot = _fixture("ikea_kajplats_ws_lamp.json")
        device_id = store.register_device(snapshot)
        assert store.device_id_for("matter", "14") == device_id
        assert store.device_id_for("zigbee", "14") is None
    finally:
        store.close()


def test_register_device_stores_the_raw_network_features(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    try:
        thread = store.register_device(_fixture("ikea_kajplats_ws_lamp.json"))
        unknown = store.register_device(_fixture("example_light.json"))
        assert store.device(thread).network_features == 2
        assert store.device(unknown).network_features is None
    finally:
        store.close()


def test_backfill_network_features_fills_only_null_and_only_reachable_devices(tmp_path):
    """Fault to prove it: remove `network_features IS NULL AND` from the
    SELECT in `backfill_network_features` - the device with the manually
    set 7 is then overwritten with 2."""
    store = Store(tmp_path / "s.sqlite")
    try:
        preset = _fixture("ikea_kajplats_ws_lamp.json")
        missing = _fixture("ikea_kajplats_cws_lamp.json")
        offline = _fixture("ikea_grillplats_plug.json")
        preset_id = store.register_device(preset)
        missing_id = store.register_device(missing)
        offline_id = store.register_device(offline)
        store._db.execute("UPDATE device SET network_features = NULL")
        store._db.execute("UPDATE device SET network_features = 7 WHERE id = ?", (preset_id,))
        store._db.commit()

        filled = store.backfill_network_features([preset, missing])

        assert filled == 1
        assert store.device(preset_id).network_features == 7
        assert store.device(missing_id).network_features == 2
        assert store.device(offline_id).network_features is None
    finally:
        store.close()


def test_commands_carry_the_owning_devices_identity(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    try:
        snapshot = _fixture("ikea_kajplats_ws_lamp.json")
        device_id = store.register_device(snapshot)
        commands = store.register_commands(device_id, extract_commands(snapshot))
        assert commands
        assert {(c.technology, c.address) for c in commands} == {("matter", "14")}
    finally:
        store.close()


def test_parse_technology_rejects_an_unknown_value():
    assert parse_technology("zigbee") == "zigbee"
    with pytest.raises(ValueError, match="bluetooth"):
        parse_technology("bluetooth")
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/model/test_store_identity.py -q`
Expected: FAIL — `AttributeError`/`TypeError` on `technology`, `device_id_for`, `backfill_network_features`, and `user_version == 8`.

- [ ] **Step 4: Change `_SCHEMA` in `src/loxmatter/model/store.py`.** In the `device` table replace the line `node_id      INTEGER NOT NULL,` with:

```sql
    technology       TEXT NOT NULL DEFAULT 'matter',
    address          TEXT NOT NULL DEFAULT '',
```

and add after `device_types TEXT` (mind the comma on the previous line):

```sql
    device_types     TEXT,
    network_features INTEGER
```

In the `command` table delete the line `node_id     INTEGER NOT NULL,`.

- [ ] **Step 5: Add migration 9.** Append to the version comment block above `_SCHEMA_VERSION`:

```python
# Version 9 (device source boundary, design 2026-09-11) replaces the Matter
# node ID as device identity with `device.technology` + `device.address`,
# adds `device.network_features`, and drops `node_id` from `device` and from
# `command` (where it was a redundant copy), see `_migrate_to_v9`.
```

Set `_SCHEMA_VERSION = 9`. Add below `_add_column_if_missing`:

```python
def _drop_column_if_present(db: sqlite3.Connection, table: str, column: str) -> None:
    """The mirror image of `_add_column_if_missing`, for the same pitfall:
    a fresh database never had `column`, and still runs every migration."""
    columns = {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})")}
    if column in columns:
        db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
```

Add after `_migrate_to_v8`:

```python
def _migrate_to_v9(db: sqlite3.Connection) -> None:
    """Technology plus address instead of the Matter node ID (design
    2026-09-11, section 4.1).

    `node_id` is dropped rather than left as a dead column: it is `NOT
    NULL`, and a Zigbee row would have to invent a node ID to satisfy it.
    `DROP COLUMN` needs SQLite 3.35; the image ships 3.46.1 (measured 11
    September 2026).

    `address` is `NOT NULL DEFAULT ''` in both this migration and `_SCHEMA`,
    so a migrated and a fresh database end up with the same column
    definition. The empty default never survives: this backfill fills every
    existing row, and `register_device` always writes a real address.

    No backfill for `network_features`: the value lives in the snapshot,
    which a migration never sees - `Store.backfill_network_features` fills
    it at startup, the same split `_migrate_to_v7` documents for
    `device_types`."""
    _add_column_if_missing(db, "device", "technology", "TEXT NOT NULL DEFAULT 'matter'")
    _add_column_if_missing(db, "device", "address", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(db, "device", "network_features", "INTEGER")
    device_columns = {str(row["name"]) for row in db.execute("PRAGMA table_info(device)")}
    if "node_id" in device_columns:
        db.execute("UPDATE device SET address = CAST(node_id AS TEXT) WHERE address = ''")
    _drop_column_if_present(db, "device", "node_id")
    _drop_column_if_present(db, "command", "node_id")
```

Add `9: _migrate_to_v9,` to `_MIGRATIONS`.

- [ ] **Step 6: Change the row types.** Add to the store's imports: `from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef, Technology, parse_technology` and `from loxmatter.profiles.transport import network_features_of`.

In `StoredCommand` replace `node_id: int` with:

```python
    technology: Technology
    address: str
```

In `StoredDevice` replace `node_id: int` with the same two lines, and add at the end of the class:

```python
    # The raw FeatureMap of NetworkCommissioning (`0/49/65532`), `None` for
    # a device that reports none or has not been backfilled yet. Stored raw
    # for the reason `device_types` is - see `profiles/transport.py`.
    network_features: int | None
```

Update the docstring of `StoredDevice` only where it mentions the node ID (none today) — leave the rest.

- [ ] **Step 7: Change the identity methods.** Replace `_device_identity` and add `_identity_of` directly below it:

```python
    def _device_identity(self, snapshot: NodeSnapshot) -> str:
        """Falls back to the node ID: some devices do not report a unique ID (Spec 7.2)."""
        return snapshot.unique_id or f"node:{self._identity_of(snapshot)[1]}"

    @staticmethod
    def _identity_of(snapshot: NodeSnapshot) -> tuple[str, str]:
        """`(technology, address)` of a snapshot - the one place the store
        derives it, so the snapshot's own fields replace it in one edit."""
        return ("matter", str(snapshot.node_id))  # TRANSITIONAL (Task 3)
```

In `register_device`, replace the INSERT statement and its parameter tuple with:

```python
        technology, address = self._identity_of(snapshot)
        cur = self._db.execute(
            "INSERT INTO device"
            " (unique_id, technology, address, label, udp_port, updated_at, room,"
            " device_types, network_features)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                identity,
                technology,
                address,
                label,
                DEFAULT_UDP_PORT,
                self._now(),
                _normalized_room(room),
                _encode_device_types(device_types_by_endpoint(snapshot)),
                network_features_of(snapshot),
            ),
        )
```

Replace `_as_device`:

```python
    @staticmethod
    def _as_device(row: sqlite3.Row) -> StoredDevice:
        return StoredDevice(
            id=int(row["id"]),
            technology=parse_technology(str(row["technology"])),
            address=str(row["address"]),
            unique_id=str(row["unique_id"]),
            label=str(row["label"]),
            exported_at=row["exported_at"],
            updated_at=row["updated_at"],
            room=row["room"],
            device_types=_decode_device_types(row["device_types"]),
            network_features=(
                None if row["network_features"] is None else int(row["network_features"])
            ),
        )
```

In `backfill_commands` replace the three node lines:

```python
        by_identity = {self._identity_of(snapshot): snapshot for snapshot in snapshots}
        gained = 0
        for device in self.devices():
            snapshot = by_identity.get((device.technology, device.address))
            if snapshot is None:
                continue
            before = len(self.commands(device.id))
            self.register_commands(device.id, extract_commands(snapshot))
```

In `backfill_device_types` replace the lookup part:

```python
        by_identity = {self._identity_of(snapshot): snapshot for snapshot in snapshots}
        rows = self._db.execute(
            "SELECT id, technology, address FROM device"
            " WHERE device_types IS NULL AND active = 1"
        ).fetchall()
        filled = 0
        for row in rows:
            snapshot = by_identity.get((str(row["technology"]), str(row["address"])))
```

Add directly after `backfill_device_types`:

```python
    def backfill_network_features(self, snapshots: Sequence[NodeSnapshot]) -> int:
        """Backfills `device.network_features` for devices that do not yet
        have it, and returns how many that was.

        The same rules as `backfill_device_types`, for the same reasons:
        only `NULL` is filled, a set value is never overwritten, a device
        missing from `snapshots` (offline) is left alone, and `updated_at`
        is not touched - the value ends up in no export template. A
        snapshot that reports no FeatureMap leaves the row at `NULL`, so
        the next start asks again."""
        by_identity = {self._identity_of(snapshot): snapshot for snapshot in snapshots}
        rows = self._db.execute(
            "SELECT id, technology, address FROM device"
            " WHERE network_features IS NULL AND active = 1"
        ).fetchall()
        filled = 0
        for row in rows:
            snapshot = by_identity.get((str(row["technology"]), str(row["address"])))
            if snapshot is None:
                continue
            features = network_features_of(snapshot)
            if features is None:
                continue
            self._db.execute(
                "UPDATE device SET network_features = ? WHERE id = ?",
                (features, int(row["id"])),
            )
            filled += 1
        self._db.commit()
        return filled
```

Replace `device_id_for_node` entirely:

```python
    def device_id_for(self, technology: str, address: str) -> int | None:
        """Maps a source's address to the associated, stable `device_id`.

        For the runtime: an incoming update carries only the address its
        source uses, but the signal keys hang off the `device_id` (see
        module docstring - an address can change, the `device_id` never
        does). The technology is part of the lookup because two sources
        can use the same address text. `None` if no active device matches,
        e.g. because it was never exported or has since been removed
        (`forget_device`) - a removed device's address must not point to
        its old, inactive `device_id`.
        """
        row = self._db.execute(
            "SELECT id FROM device WHERE technology = ? AND address = ? AND active = 1",
            (technology, address),
        ).fetchone()
        return int(row["id"]) if row is not None else None
```

Also update the one docstring sentence in `devices()` that names `device_id_for_node` to name `device_id_for`.

- [ ] **Step 8: Change the command methods.** `register_commands`: remove the parameter `node_id: int` from the signature (it becomes `def register_commands(self, device_id: int, commands: Sequence[DeviceCommand]) -> list[StoredCommand]:`), and replace the INSERT:

```python
                self._db.execute(
                    "INSERT INTO command "
                    "(device_id, endpoint, cluster_id, command_id, key, slug, takes_value)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        device_id,
                        command.endpoint,
                        command.cluster_id,
                        command.command_id,
                        key,
                        command.slug,
                        int(command.takes_value),
                    ),
                )
```

Replace `commands`, `resolve_command` and `_as_command`:

```python
    # The owning device's identity travels with every command row through
    # this join instead of a stored copy (design 2026-09-11, section 4.1):
    # `command.node_id` used to duplicate `device.node_id`, and a copy is a
    # second place that can disagree.
    _COMMAND_SELECT = (
        "SELECT command.*, device.technology AS technology, device.address AS address"
        " FROM command JOIN device ON device.id = command.device_id"
    )

    def commands(self, device_id: int) -> list[StoredCommand]:
        rows = self._db.execute(
            f"{self._COMMAND_SELECT} WHERE command.device_id = ?"
            " ORDER BY command.endpoint, command.cluster_id, command.command_id",
            (device_id,),
        ).fetchall()
        return [self._as_command(r) for r in rows]

    def resolve_command(self, key: str) -> StoredCommand:
        row = self._db.execute(
            f"{self._COMMAND_SELECT} WHERE command.key = ?", (key,)
        ).fetchone()
        if row is None:
            raise UnknownCommandError(i18n.t("api.errors.unknown_command", command_key=key))
        return self._as_command(row)

    @staticmethod
    def _as_command(row: sqlite3.Row) -> StoredCommand:
        return StoredCommand(
            key=row["key"],
            slug=row["slug"],
            technology=parse_technology(str(row["technology"])),
            address=str(row["address"]),
            endpoint=int(row["endpoint"]),
            cluster_id=int(row["cluster_id"]),
            command_id=int(row["command_id"]),
            takes_value=bool(row["takes_value"]),
            device_id=int(row["device_id"]),
        )
```

Remove the sentence in `register_commands`' docstring that is about `node_id` if any (there is none today); do not rewrite the rest.

- [ ] **Step 9: Update the callers in `src/` and `scripts/`.**

`src/loxmatter/loxone/runtime.py`, in `seed_from_snapshot`:

```python
            device_id = self._store.device_id_for(
                "matter", str(snapshot.node_id)  # TRANSITIONAL (Task 3)
            )
```

`src/loxmatter/matter/supervisor.py`, in `attach`:

```python
    await client.subscribe(
        lambda node_id: store.device_id_for("matter", str(node_id)),  # TRANSITIONAL (Task 5)
        runtime,
    )
```

`src/loxmatter/commands/translate.py`: in all three `MatterCall(...)` constructions replace `node_id=command.node_id,` with `node_id=int(command.address),  # TRANSITIONAL (Task 4)`.

`src/loxmatter/api/models.py`, `DeviceOut`: replace `node_id: int` with:

```python
    # Which source the device belongs to, and its address there (design
    # 2026-09-11, section 5.3). Not used by the web UI today.
    technology: str
    address: str
```

`src/loxmatter/api/devices.py`: in `_device_out` replace `node_id=device.node_id,` with `technology=device.technology,` and `address=device.address,`. In the commissioning route replace `store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)` with `store.register_commands(device_id, extract_commands(snapshot))`. In `remove_device` replace `device.node_id` with `int(device.address)  # TRANSITIONAL (Task 5)`.

`src/loxmatter/cli.py` and `scripts/dev_web_server.py`: drop the third argument from every `register_commands(...)` call (one in `cli.py`, three in the script).

- [ ] **Step 10: Update the existing tests mechanically.** Run `uv run pytest -q -x` repeatedly and fix each failure with exactly these rules — nothing else:

| Old | New |
|---|---|
| `store.register_commands(a, b, x.node_id)` or `…, 3)` | `store.register_commands(a, b)` |
| `StoredCommand(…, node_id=N, …)` | `StoredCommand(…, technology="matter", address="N", …)` |
| `StoredDevice(…, node_id=N, …)` | `StoredDevice(…, technology="matter", address="N", …, network_features=None)` |
| `device.node_id` / `command.node_id` on a stored row | `device.address` / `command.address` (a `str` — compare with `"3"`, not `3`) |
| `store.device_id_for_node(x)` | `store.device_id_for("matter", str(x))` |
| `FakeStore.device_id_for_node` in `tests/matter/test_supervisor.py` | `def device_id_for(self, technology: str, address: str) -> int | None: return None` |
| `assert user_version(path) == 8` (13 places in `tests/model/test_store_migration.py`) | `== 9` |
| `test_a_fresh_database_ends_at_version_eight` | rename to `test_a_fresh_database_ends_at_version_nine`, assert `== 9` |
| `tests/model/test_store.py::test_device_id_for_node_*` (3 tests) | rename to `test_device_id_for_*`, call `device_id_for("matter", …)` |
| `tests/api/conftest.py` `FakeMatterClient.follow_node`: `self.store.device_id_for_node(node_id)` | `self.store.device_id_for("matter", str(node_id))  # TRANSITIONAL (Task 5)` |

Files you will touch (from `grep -rln "node_id\|register_commands" tests`): `tests/model/test_store*.py`, `tests/api/*.py`, `tests/commands/test_fanout.py`, `tests/commands/test_translate.py`, `tests/commands/test_translate_error_messages.py`, `tests/projectsync/test_patch.py`, `tests/projectsync/test_diff.py`, `tests/projectsync/test_sync.py`, `tests/projectsync/test_group_sync.py`, `tests/export/*.py`, `tests/loxone/*.py`, `tests/test_cli.py`, `tests/test_export_cli.py`, `tests/test_store_path.py`, `tests/matter/test_supervisor.py`. **Do not** change `NodeSnapshot(node_id=…)`, `snapshot.node_id`, `from_raw(raw["node_id"], raw)`, `MatterCall(node_id=…)` or `call.node_id` — those belong to Tasks 3 and 4.

If a test's *expectation* (not a name or argument) has to change beyond the table, stop and report it as a finding with the reason.

- [ ] **Step 11: Run the new tests, then prove each protection.**

Run: `uv run pytest tests/model/test_store_identity.py -q` — Expected: PASS (8 tests).
Then introduce each fault named in the test docstrings (backfill line commented out; bare `ALTER`; `db.commit()` after the first add; `technology` dropped from `device_id_for`; `IS NULL` dropped from `backfill_network_features`), run the file, see the matching test FAIL, revert, see PASS. Paste every output.

- [ ] **Step 12: Run the full checks**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q && uv run python scripts/check_language.py`
Expected: all pass. `grep -rn "device_id_for_node" src tests scripts` prints nothing.

- [ ] **Step 13: Commit**

```bash
git add -A src tests scripts
git commit -m "refactor(store): identify devices by technology and address (schema 9)

The Matter node ID stops being the device identity so a second device
source can share the store. Migration 9 copies node_id into address and
drops node_id from device and command; commands take their identity from
the owning device through a join instead of a stored copy. Also stores
the raw NetworkCommissioning FeatureMap for the tile's transport badge.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `NodeSnapshot` Carries Technology and Address

**Files:**
- Modify: `src/loxmatter/matter/models.py`, `src/loxmatter/model/store.py`, `src/loxmatter/loxone/runtime.py`, `src/loxmatter/matter/client.py`, `src/loxmatter/api/devices.py`, `src/loxmatter/cli.py`, `scripts/record_node.py`
- Modify tests: `tests/matter/test_models.py`, `tests/api/conftest.py`, `tests/loxone/test_runtime.py`, `tests/export/test_commands.py`, and every test using `snapshot.node_id`
- Test: `tests/matter/test_models.py` (new test)

**Interfaces:**
- Consumes: `Technology` from Task 2.
- Produces: `NodeSnapshot(technology: Technology, address: str, vendor_name: str, product_name: str, unique_id: str, attributes: Mapping[str, Any] = {}, available: bool = True)`; `NodeSnapshot.from_raw(node_id: int, raw: Mapping[str, Any]) -> NodeSnapshot` keeps its signature and sets `technology="matter"`, `address=str(node_id)`.

- [ ] **Step 1: Write the failing test.** Append to `tests/matter/test_models.py`:

```python
def test_from_raw_is_the_matter_factory():
    """Protects the identity every other module now reads. Fault to prove
    it: set `address=""` in `from_raw`."""
    snapshot = NodeSnapshot.from_raw(23, {"attributes": {}})
    assert snapshot.technology == "matter"
    assert snapshot.address == "23"
    assert not hasattr(snapshot, "node_id")
```

(Add `from loxmatter.matter.models import NodeSnapshot` to the imports if the file does not import it.)

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/matter/test_models.py -q`
Expected: FAIL with `AttributeError: 'NodeSnapshot' object has no attribute 'technology'`.

- [ ] **Step 3: Change `NodeSnapshot`.** Replace the field `node_id: int` with:

```python
    # Which source produced the snapshot and the device's address there
    # (design 2026-09-11, section 3.3). For Matter the address is the node
    # ID as text; `BridgeMatterClient` converts back where matter-server
    # needs an integer.
    technology: Technology
    address: str
```

In `from_raw` replace `node_id=node_id,` with:

```python
            technology="matter",
            address=str(node_id),
```

- [ ] **Step 4: Remove the transitional expressions of Task 2 that read the snapshot.**

`src/loxmatter/model/store.py`, `_identity_of`:

```python
        return (snapshot.technology, snapshot.address)
```

`src/loxmatter/loxone/runtime.py`, `seed_from_snapshot`:

```python
            device_id = self._store.device_id_for(snapshot.technology, snapshot.address)
            if device_id is None:
                logger.info(
                    "no known device for %s address %s - skipping snapshot during seeding",
                    snapshot.technology,
                    snapshot.address,
                )
                continue
```

- [ ] **Step 5: Update the remaining readers.**

`src/loxmatter/matter/client.py`, `snapshot()`:

```python
    async def snapshot(self, node_id: int) -> NodeSnapshot:
        for candidate in await self.snapshots():
            if candidate.address == str(node_id):
                return candidate
        raise MatterUnavailableError(i18n.t("api.errors.unknown_node", node_id=node_id))
```

`src/loxmatter/api/devices.py`, commissioning route: `await active_client.follow_node(int(snapshot.address), seed_even_without_new_paths=True)  # TRANSITIONAL (Task 5)`.

`src/loxmatter/cli.py`: in `render_report` use `f"Node {snapshot.address}: {snapshot.vendor_name} {snapshot.product_name}".rstrip()`; in `export` use `… or f"Node {snapshot.address}"`.

`scripts/record_node.py`: `{"node_id": int(snapshot.address), "attributes": dict(snapshot.attributes)}`.

- [ ] **Step 6: Update the tests mechanically** with exactly these rules:

| Old | New |
|---|---|
| `NodeSnapshot(node_id=N, …)` (in `tests/api/conftest.py`, `tests/loxone/test_runtime.py`, `tests/export/test_commands.py`) | `NodeSnapshot(technology="matter", address="N", …)` |
| `snapshot.node_id` / `snap.node_id` / `plug.node_id` on a `NodeSnapshot` | `.address` — compare with a string |
| `NodeSnapshot.from_raw(raw["node_id"], raw)` | unchanged |

`tests/matter/test_client.py` keeps every `node_id` that belongs to the fake upstream's nodes (`node.node_id`, `_AttributeUpdate(node_id=…)`); only assertions on a returned `NodeSnapshot` change.

Run: `grep -rn "\.node_id" src scripts tests --include='*.py'`
Expected afterwards: hits only in `src/loxmatter/matter/client.py` (upstream nodes and the internal update records), `src/loxmatter/commands/translate.py` / `src/loxmatter/matter/client.py` / tests on `MatterCall` (`call.node_id`, Task 4), and `tests/matter/test_client*.py` on fake upstream nodes.

- [ ] **Step 7: Run the new test, prove it, run the checks**

Run: `uv run pytest tests/matter/test_models.py -q` — PASS. Introduce `address=""` in `from_raw`: FAIL. Revert: PASS. Paste outputs.
Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q && uv run python scripts/check_language.py` — all pass.

- [ ] **Step 8: Commit**

```bash
git add -A src tests scripts
git commit -m "refactor(matter): let a snapshot carry technology and address

NodeSnapshot now says which source produced it and the device's address
there; from_raw stays the Matter factory. The store, the runtime and the
CLI read that identity instead of the node ID.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `DeviceCall` Replaces `MatterCall`

**Files:**
- Create: `src/loxmatter/sources/__init__.py`
- Modify: `src/loxmatter/commands/translate.py`, `src/loxmatter/commands/fanout.py`, `src/loxmatter/loxone/server.py`, `src/loxmatter/api/control.py`, `src/loxmatter/matter/client.py`, `src/loxmatter/cli.py`, `scripts/dev_web_server.py`
- Modify tests: `tests/commands/test_translate.py`, `tests/commands/test_translate_error_messages.py`, `tests/commands/test_fanout.py`, `tests/matter/test_client.py`, `tests/api/conftest.py`, `tests/loxone/test_server.py`, `tests/api/test_control.py`, `tests/api/test_group_control.py`, and any other file `grep -rln "MatterCall\|to_matter_calls\|send_command" tests` lists

**Interfaces:**
- Consumes: `Technology` (Task 2), `StoredCommand.technology/address` (Task 2).
- Produces: `loxmatter.sources.DeviceCall(technology: Technology, address: str, endpoint: int, cluster_id: int, command_id: int, payload: dict[str, object] = {})`; `loxmatter.commands.translate.to_device_calls(command: StoredCommand, value: str) -> list[DeviceCall]`; `BridgeMatterClient.send(call: DeviceCall) -> None`; `Invoker = Callable[[DeviceCall], Awaitable[None]]` in `server.py` and `control.py`.

- [ ] **Step 1: Write the failing test.** In `tests/commands/test_translate.py`, change the import block to import `DeviceCall` from `loxmatter.sources` and `to_device_calls` from `loxmatter.commands.translate`, and add:

```python
def test_a_call_carries_the_commands_technology_and_address():
    """Protects that the call is addressed through the stored identity, not
    a constant. Fault to prove it: hardcode `technology="matter"` in
    `to_device_calls`."""
    command = StoredCommand(
        key="d1_1_on",
        slug="on",
        technology="zigbee",
        address="00:12:4b:00:1c:a1:b2:c3",
        endpoint=1,
        cluster_id=6,
        command_id=1,
        takes_value=False,
        device_id=1,
    )
    (call,) = to_device_calls(command, "1")
    assert (call.technology, call.address) == ("zigbee", "00:12:4b:00:1c:a1:b2:c3")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/commands/test_translate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.sources'`.

- [ ] **Step 3: Create `src/loxmatter/sources/__init__.py`** (GPL header first):

```python
"""Device sources: what produces devices, and the call that reaches them
(design 2026-09-11, section 3).

Matter's data model stays loxmatter's internal language. Every source
translates into it at its own edge, so everything past this package -
discovery, profiles, runtime, export, groups - does not know which source
a device came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loxmatter.matter.models import Technology

__all__ = ["DeviceCall", "Technology"]


@dataclass(frozen=True)
class DeviceCall:
    """One command to one endpoint of one device. Formerly `MatterCall`.

    `cluster_id`, `command_id` and the `payload` field names follow the
    Matter data model (`commands/translate.py` builds them); a non-Matter
    source renames the fields at its own edge. Zigbee's IDs for every
    command translated today are identical (design 2026-09-11, 1.1)."""

    technology: Technology
    address: str
    endpoint: int
    cluster_id: int
    command_id: int
    payload: dict[str, object] = field(default_factory=dict)
```

- [ ] **Step 4: Switch the producers and consumers.**

`src/loxmatter/commands/translate.py`: delete the `MatterCall` class; add `from loxmatter.sources import DeviceCall`; rename `to_matter_calls` to `to_device_calls` (definition, docstrings that name it); in the three constructions replace `MatterCall(` with `DeviceCall(` and the `node_id=int(command.address),  # TRANSITIONAL (Task 4)` line with:

```python
                technology=command.technology,
                address=command.address,
```

`src/loxmatter/commands/fanout.py`: `from loxmatter.commands.translate import to_device_calls` and `from loxmatter.sources import DeviceCall`; replace every `MatterCall` with `DeviceCall` and `to_matter_calls` with `to_device_calls`.

`src/loxmatter/loxone/server.py` and `src/loxmatter/api/control.py`: import `UnsupportedValueError, to_device_calls` from `loxmatter.commands.translate` and `DeviceCall` from `loxmatter.sources`; `Invoker = Callable[[DeviceCall], Awaitable[None]]`; replace each `to_matter_calls(` with `to_device_calls(`; change the log line `"Matter call for key %r failed"` to `"device call for key %r failed"` in both files; update docstring mentions of `MatterCall` / `to_matter_calls`.

`src/loxmatter/matter/client.py`: import `DeviceCall` from `loxmatter.sources` instead of `MatterCall`; rename `send_command` to `send`:

```python
    async def send(self, call: DeviceCall) -> None:
```

keep its docstring (replace "`MatterCall`" with "`DeviceCall`" and the test name it cites with `test_send_passes_the_payload_as_command_fields`), and change the last line to:

```python
        await upstream.send_device_command(int(call.address), call.endpoint, command)
```

`src/loxmatter/cli.py`: import `DeviceCall` from `loxmatter.sources`; in `_run`:

```python
    async def invoke(call: DeviceCall) -> None:
        await client.send(call)
```

`scripts/dev_web_server.py`: `from loxmatter.sources import DeviceCall` and `async def _invoke(call: DeviceCall) -> None:`.

- [ ] **Step 5: Update the tests mechanically** with exactly these rules:

| Old | New |
|---|---|
| `from loxmatter.commands.translate import MatterCall` | `from loxmatter.sources import DeviceCall` |
| `MatterCall(node_id=N, endpoint=…` | `DeviceCall(technology="matter", address="N", endpoint=…` |
| `to_matter_calls` | `to_device_calls` |
| `client.send_command(` | `client.send(` |
| `test_send_command_*` test names | `test_send_*` |
| `call.node_id` | `call.address` (string) |

- [ ] **Step 6: Run, prove, check**

Run: `uv run pytest tests/commands -q` — PASS. Hardcode `technology="matter"` in the `calls = [DeviceCall(...)]` construction of `to_device_calls` (the one the OnOff command takes — not the level-0 early return above it): the new test FAILS. Revert: PASS. Paste outputs.
Run: `grep -rn "MatterCall\|to_matter_calls\|send_command" src scripts tests` — Expected: no hits outside `docs/`.
Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q && uv run python scripts/check_language.py` — all pass.

- [ ] **Step 7: Commit**

```bash
git add -A src tests scripts
git commit -m "refactor(commands): address calls by technology and address

MatterCall becomes DeviceCall in the new sources package and carries the
owning device's technology and address instead of a node ID, so the
invoker can later route a call to the source it belongs to.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The `DeviceSource` Protocol, the `Sources` Registry, and the Matter Client Conforming

**Files:**
- Modify: `src/loxmatter/sources/__init__.py`
- Modify: `src/loxmatter/matter/client.py`, `src/loxmatter/matter/supervisor.py`, `src/loxmatter/api/devices.py`, `src/loxmatter/cli.py`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Create: `tests/sources/test_sources.py`
- Modify tests: `tests/api/conftest.py`, `tests/api/test_devices.py`, `tests/matter/test_client.py`, `tests/matter/test_client_commissioning.py`

**Interfaces:**
- Consumes: `DeviceCall` (Task 4), `Store.device_id_for` (Task 2).
- Produces:
  - `loxmatter.sources.RuntimeEventHandler` (moved unchanged from `matter/client.py`)
  - `loxmatter.sources.DeviceSource` (Protocol, section 3.1 of the spec)
  - `loxmatter.sources.SourceNotConfiguredError(technology: str)` with attribute `.technology` and an i18n message
  - `loxmatter.sources.Sources(sources: Iterable[DeviceSource])` with `get(technology: str) -> DeviceSource`, `all() -> list[DeviceSource]`, `all_connected() -> bool`, `async send(call: DeviceCall) -> None`
  - `BridgeMatterClient.technology: Technology = "matter"`, `follow(address: str, *, seed_even_without_new_paths: bool = False)`, `remove(address: str)`, `subscribe(resolve_device_id: Callable[[str], int | None], handler: RuntimeEventHandler)`

- [ ] **Step 1: Write the failing tests** `tests/sources/test_sources.py` (GPL header first; the directory needs no `__init__.py` — `tests/matter/` has none either):

```python
"""The registry that dispatches by technology (design 2026-09-11,
section 3.1)."""

from __future__ import annotations

import pytest

from loxmatter import i18n
from loxmatter.sources import DeviceCall, SourceNotConfiguredError, Sources


class _FakeSource:
    def __init__(self, technology: str, *, connected: bool = True) -> None:
        self.technology = technology
        self.connected = connected
        self.sent: list[DeviceCall] = []

    async def send(self, call: DeviceCall) -> None:
        self.sent.append(call)


def _call(technology: str) -> DeviceCall:
    return DeviceCall(
        technology=technology,
        address="1",
        endpoint=1,
        cluster_id=6,
        command_id=1,
    )


async def test_send_reaches_the_source_of_the_calls_technology():
    """Two sources, so a registry that always answers with the first one
    cannot pass. Fault to prove it: make `get` return
    `next(iter(self._by_technology.values()))`."""
    matter = _FakeSource("matter")
    zigbee = _FakeSource("zigbee")
    sources = Sources([matter, zigbee])

    await sources.send(_call("zigbee"))

    assert zigbee.sent == [_call("zigbee")]
    assert matter.sent == []


async def test_an_unconfigured_technology_raises_with_its_name():
    sources = Sources([_FakeSource("matter")])
    with pytest.raises(SourceNotConfiguredError) as caught:
        await sources.send(_call("zigbee"))
    assert caught.value.technology == "zigbee"
    assert str(caught.value) == i18n.t("api.errors.source_not_configured", technology="zigbee")


def test_two_sources_of_one_technology_are_a_wiring_error():
    with pytest.raises(ValueError, match="matter"):
        Sources([_FakeSource("matter"), _FakeSource("matter")])


def test_all_connected_needs_every_source():
    """Fault to prove it: replace `all(` with `any(` in `all_connected`."""
    assert Sources([_FakeSource("matter"), _FakeSource("zigbee")]).all_connected()
    assert not Sources(
        [_FakeSource("matter"), _FakeSource("zigbee", connected=False)]
    ).all_connected()


def test_all_keeps_the_order_sources_were_given_in():
    matter = _FakeSource("matter")
    zigbee = _FakeSource("zigbee")
    assert Sources([matter, zigbee]).all() == [matter, zigbee]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/sources/test_sources.py -q`
Expected: FAIL with `ImportError: cannot import name 'SourceNotConfiguredError'`.

- [ ] **Step 3: Add the i18n key** to `src/loxmatter/i18n/strings.yaml`, directly after `api.errors.device_unreachable`:

```yaml
api.errors.source_not_configured:
  # A stored device belongs to a technology no running source serves -
  # possible once a second source exists and its radio is removed from
  # the configuration (design 2026-09-11, section 6.3). 503, not 502:
  # nothing was asked of the device.
  en: "{technology} is not set up in this installation"
  de: "{technology} ist in dieser Installation nicht eingerichtet"
```

- [ ] **Step 4: Extend `src/loxmatter/sources/__init__.py`.** Replace the import block and `__all__`, keep `DeviceCall`, and append the rest:

```python
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from loxmatter import i18n
from loxmatter.matter.models import NodeSnapshot, Technology

__all__ = [
    "DeviceCall",
    "DeviceSource",
    "RuntimeEventHandler",
    "SourceNotConfiguredError",
    "Sources",
    "Technology",
]
```

Move the class `RuntimeEventHandler` (with its docstring, unchanged apart from the word "`subscribe()`" now meaning `DeviceSource.subscribe`) from `src/loxmatter/matter/client.py` into this module, below `DeviceCall`. Then append:

```python
class DeviceSource(Protocol):
    """What shared code needs from a source - measured against what it
    called on the Matter client, not against what a source could offer
    (design 2026-09-11, section 3.1).

    Commissioning is deliberately absent: Matter takes a code and returns
    one device, Zigbee opens the network and devices arrive later. Each
    commissioning route calls its own source by name (section 3.2)."""

    @property
    def technology(self) -> Technology: ...

    @property
    def connected(self) -> bool: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def wait_for_link_loss(self) -> None: ...

    async def snapshots(self) -> list[NodeSnapshot]: ...

    async def subscribe(
        self,
        resolve_device_id: Callable[[str], int | None],
        handler: RuntimeEventHandler,
    ) -> None: ...

    async def follow(self, address: str, *, seed_even_without_new_paths: bool = False) -> None: ...

    async def send(self, call: DeviceCall) -> None: ...

    async def remove(self, address: str) -> None: ...


class SourceNotConfiguredError(LookupError):
    """A stored device belongs to a technology no running source serves.

    A `LookupError`, not a `KeyError`: `str()` of a `KeyError` wraps its
    message in quotes, and this message reaches the web UI."""

    def __init__(self, technology: str) -> None:
        super().__init__(i18n.t("api.errors.source_not_configured", technology=technology))
        self.technology = technology


class Sources:
    """The only place that dispatches by technology."""

    def __init__(self, sources: Iterable[DeviceSource]) -> None:
        self._by_technology: dict[str, DeviceSource] = {}
        for source in sources:
            if source.technology in self._by_technology:
                raise ValueError(f"two sources for technology {source.technology!r}")
            self._by_technology[source.technology] = source

    def get(self, technology: str) -> DeviceSource:
        try:
            return self._by_technology[technology]
        except KeyError:
            raise SourceNotConfiguredError(technology) from None

    def all(self) -> list[DeviceSource]:
        return list(self._by_technology.values())

    def all_connected(self) -> bool:
        return all(source.connected for source in self._by_technology.values())

    async def send(self, call: DeviceCall) -> None:
        await self.get(call.technology).send(call)
```

- [ ] **Step 5: Make `BridgeMatterClient` conform.** In `src/loxmatter/matter/client.py`:

1. Import: `from loxmatter.matter.models import NodeSnapshot, Technology` and `from loxmatter.sources import DeviceCall, RuntimeEventHandler`. Delete the moved `RuntimeEventHandler` class.
2. Add as the first line of the class body:

```python
    technology: Technology = "matter"
```

3. Replace `remove_node`:

```python
    async def remove(self, address: str) -> None:
        """Removes a device from the fabric."""
        await self._require_upstream().remove_node(int(address))
```

4. Change `subscribe`'s signature to `resolve_device_id: Callable[[str], int | None]` and, as the first lines after `upstream = self._require_upstream()`, add:

```python
        # matter-server speaks integer node IDs; the store speaks addresses.
        # The conversion happens once, here, and everything below this
        # point keeps working with node IDs.
        def resolve_node(node_id: int) -> int | None:
            return resolve_device_id(str(node_id))
```

Then use `resolve_node` wherever the body used `resolve_device_id` (`self._resolve_device_id = resolve_node` and the `_dispatch_loop(queue, resolve_node, handler)` call). Update the docstring's first example from `Store.device_id_for_node` to `Store.device_id_for`.

5. Rename `follow_node` to `_follow_node` (keep its body, signature `node_id: int`, and docstring) and update the one call in `_dispatch_loop` to `await self._follow_node(item.node_id)`. Add the public method directly above it:

```python
    async def follow(self, address: str, *, seed_even_without_new_paths: bool = False) -> None:
        """Catches up a device's subscriptions - see `_follow_node`, which
        does the work in matter-server's integer node IDs."""
        await self._follow_node(int(address), seed_even_without_new_paths=seed_even_without_new_paths)
```

- [ ] **Step 6: Update the callers.**

`src/loxmatter/matter/supervisor.py`, `attach`:

```python
    await client.subscribe(
        partial(store.device_id_for, "matter"),  # TRANSITIONAL (Task 6)
        runtime,
    )
```

(with `from functools import partial`).

`src/loxmatter/api/devices.py`: commissioning route `await active_client.follow(snapshot.address, seed_even_without_new_paths=True)`; `remove_device`: `await active_client.remove(device.address)`.

`src/loxmatter/cli.py`, `_run`: add `from loxmatter.sources import Sources` and replace the construction and the invoke closure:

```python
    sender = UdpSender(miniserver, port)
    client = _build_client(url)
    sources = Sources([client])
    runtime = Runtime(store, sender, link_ok=sources.all_connected)
    invoke = sources.send
```

(`DeviceCall` is no longer needed in `cli.py`; remove the import if unused.) This assignment is also what makes mypy check that `BridgeMatterClient` satisfies `DeviceSource`.

- [ ] **Step 7: Update the tests mechanically** with exactly these rules:

| Old | New |
|---|---|
| `client.follow_node(N, …)` in `tests/matter/test_client.py` | `client.follow("N", …)` |
| `test_follow_node_*` test names | `test_follow_*` |
| `client.remove_node(N)` / `test_remove_node_reaches_upstream` | `client.remove("N")` / `test_remove_reaches_upstream` |
| a `resolve_device_id` passed to `client.subscribe(…)` that looks up integer keys, e.g. `{4: 1}.get` or `lambda node_id: 1 if node_id == 4 else None` | the same lookup with string keys: `{"4": 1}.get`, `lambda address: 1 if address == "4" else None` |
| `FakeMatterClient` in `tests/api/conftest.py` | add `technology = "matter"` as a class attribute; rename `remove_node(self, node_id: int)` → `remove(self, address: str)` appending `address`; rename `follow_node(self, node_id: int, …)` → `follow(self, address: str, …)` appending `address` and resolving with `self.store.device_id_for("matter", address)` (drop the `TRANSITIONAL (Task 5)` comment) |
| assertions such as `fake_client.removed == [3]`, `fake_client.followed == [100]` | `== ["3"]`, `== ["100"]` |

- [ ] **Step 8: Run, prove, check**

Run: `uv run pytest tests/sources -q` — PASS (5 tests). Introduce the two faults named in the docstrings one at a time: the matching test FAILS each time; revert: PASS. Paste outputs.
Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q && uv run python scripts/check_language.py` — all pass.

- [ ] **Step 9: Commit**

```bash
git add -A src tests
git commit -m "feat(sources): add the DeviceSource protocol and the Sources registry

The Matter client now satisfies a technology-neutral protocol (follow,
remove, send, subscribe by address) and startup routes calls and the
heartbeat's link check through a registry that dispatches by technology.
Commissioning stays outside the protocol because Matter and Zigbee pair
in incompatible ways.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The Supervisor Serves Any Source

**Files:**
- Move: `src/loxmatter/matter/supervisor.py` → `src/loxmatter/sources/supervisor.py`
- Move: `tests/matter/test_supervisor.py` → `tests/sources/test_supervisor.py`
- Modify: `src/loxmatter/cli.py`, `tests/test_cli.py` (docstring mention of `matter.supervisor` only)

**Interfaces:**
- Consumes: `DeviceSource`, `Sources` (Task 5), `Store.backfill_network_features` (Task 2).
- Produces: `loxmatter.sources.supervisor.attach(source: DeviceSource, store: Store, runtime: Runtime) -> int` and `supervise(source: DeviceSource, store: Store, runtime: Runtime, *, sleep=…, backoff_start=…, backoff_max=…) -> None`.

- [ ] **Step 1: Move the files**

```bash
cd /Users/lucienkerl/Development/matter-loxone/.claude/worktrees/german-to-english-translation-f84003
git mv src/loxmatter/matter/supervisor.py src/loxmatter/sources/supervisor.py
git mv tests/matter/test_supervisor.py tests/sources/test_supervisor.py
```

In the moved test file change the import to `from loxmatter.sources.supervisor import attach, supervise`. In `src/loxmatter/cli.py` change the import to `from loxmatter.sources.supervisor import attach, supervise`.

- [ ] **Step 2: Write the failing tests.** In `tests/sources/test_supervisor.py`, give `FakeClient` a technology and `FakeStore` recording methods, then add two tests.

Change `FakeClient.__init__` to accept `technology: str = "matter"` and store it as `self.technology`; change `FakeClient.subscribe` to keep the resolver:

```python
    async def subscribe(self, resolve_device_id, handler) -> None:
        self.subscribe_calls += 1
        self.resolver = resolve_device_id
```

Change `FakeStore`:

```python
class FakeStore:
    def __init__(self) -> None:
        self.backfill_types_calls = 0
        self.backfill_commands_calls = 0
        self.backfill_features_calls = 0
        self.lookups: list[tuple[str, str]] = []

    def device_id_for(self, technology: str, address: str) -> int | None:
        self.lookups.append((technology, address))
        return None

    def backfill_device_types(self, snapshots) -> int:
        self.backfill_types_calls += 1
        return 0

    def backfill_network_features(self, snapshots) -> int:
        self.backfill_features_calls += 1
        return 0

    def backfill_commands(self, snapshots) -> int:
        self.backfill_commands_calls += 1
        return 3
```

Add:

```python
async def test_attach_resolves_addresses_in_the_sources_own_technology():
    """A non-Matter source, so a resolver bound to "matter" cannot pass.
    Fault to prove it: bind `partial(store.device_id_for, "matter")` in
    `attach` instead of `source.technology`."""
    source = FakeClient(technology="zigbee")
    store = FakeStore()

    await attach(source, store, FakeRuntime())
    source.resolver("00:12:4b:00:1c:a1:b2:c3")

    assert store.lookups == [("zigbee", "00:12:4b:00:1c:a1:b2:c3")]


async def test_attach_backfills_network_features():
    """Fault to prove it: remove the `store.backfill_network_features(...)`
    line from `attach`."""
    store = FakeStore()
    await attach(FakeClient(), store, FakeRuntime())
    assert store.backfill_features_calls == 1
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/sources/test_supervisor.py -q`
Expected: FAIL — the lookup records `("matter", …)`, and `backfill_features_calls == 0`.

- [ ] **Step 4: Generalise `src/loxmatter/sources/supervisor.py`.** Imports:

```python
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import partial

from loxmatter.loxone.runtime import Runtime
from loxmatter.model.store import Store
from loxmatter.sources import DeviceSource
```

(remove the `BridgeMatterClient` import). Rename the parameter `client` to `source` in `attach` and `supervise` and type it `DeviceSource`. `attach` body:

```python
    await source.subscribe(partial(store.device_id_for, source.technology), runtime)
    snapshots = await source.snapshots()
    await runtime.seed_from_snapshot(snapshots)
    store.backfill_device_types(snapshots)
    store.backfill_network_features(snapshots)
    gained: int = store.backfill_commands(snapshots)
    await runtime.resend_all()
    return gained
```

In `supervise`, replace the three log messages with:

```python
            logger.warning("connection of source %s lost - rebuilding it", source.technology)
```

```python
                logger.warning(
                    "rebuild of source %s failed (%s) - next attempt in %.0f s",
                    source.technology,
                    exc,
                    delay,
                    exc_info=exc,
                )
```

```python
                logger.info(
                    "connection of source %s restored (%d commands backfilled)",
                    source.technology,
                    gained,
                )
```

Add one paragraph at the end of the module docstring: "Moved from `matter/supervisor.py` on 11 September 2026 (design device source boundary, section 3.3): nothing in it was Matter-specific except the type of its argument, and a second source needs the same guarantees." Leave the rest of the docstrings as they are, replacing the word "client" with "source" only where it names the parameter.

- [ ] **Step 5: Wire every source at startup.** In `src/loxmatter/cli.py`, `_run`, replace the attach/supervise part:

```python
        await runtime.start()
        gained = 0
        for source in sources.all():
            gained += await attach(source, store, runtime)
        if gained:
            typer.echo(i18n.t("cli.run.echo_commands_backfilled", count=gained))
        supervisor_tasks = [
            asyncio.ensure_future(supervise(source, store, runtime)) for source in sources.all()
        ]
```

Declare `supervisor_tasks: list[asyncio.Task[None]] = []` where `supervisor_task` was declared, and replace the `finally` block's supervisor part with:

```python
        for supervisor_task in supervisor_tasks:
            supervisor_task.cancel()
            try:
                await supervisor_task
            except asyncio.CancelledError:
                if not supervisor_task.cancelled():
                    raise
            except Exception:
                logger.exception("Supervisor of a device source ended with an error")
```

Replace the single `client.disconnect()` cleanup with one guarded disconnect per source:

```python
        for source in sources.all():
            try:
                await source.disconnect()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Source %s could not be disconnected cleanly on shutdown", source.technology
                )
```

Keep `await client.connect()` with its `CannotConnect` / `MatterUnavailableError` handling exactly as it is — matter-server stays mandatory. In `tests/test_cli.py` only the docstring of `_SpySupervisor` names `matter.supervisor.supervise`; change it to `sources.supervisor.supervise`. `test_run_starts_the_supervisor_with_the_same_client_store_and_runtime` must pass unchanged.

- [ ] **Step 6: Run, prove, check**

Run: `uv run pytest tests/sources tests/test_cli.py -q` — PASS. Introduce each fault named in the two new docstrings: the matching test FAILS; revert: PASS. Paste outputs.
Run: `grep -rn "matter.supervisor\|matter/supervisor" src tests scripts` — no hits.
Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q && uv run python scripts/check_language.py` — all pass.

- [ ] **Step 7: Commit**

```bash
git add -A src tests
git commit -m "refactor(sources): supervise and attach any device source

The supervisor moves out of the matter package and works on the
DeviceSource protocol: each source gets its own supervising loop, its
resolver is bound to its own technology, and attach now also backfills the
network features the tile badge reads.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Routes Reach Devices Through `Sources`

**Files:**
- Modify: `src/loxmatter/loxone/server.py`, `src/loxmatter/api/devices.py`, `src/loxmatter/api/control.py`, `src/loxmatter/cli.py`
- Test: `tests/api/test_sources_routing.py` (new)

**Interfaces:**
- Consumes: `Sources`, `SourceNotConfiguredError` (Task 5).
- Produces: `build_app(..., client: BridgeMatterClient | None = None, sources: Sources | None = None, ...)`; `build_device_router(store, client, runtime, thread_dataset_source=None, sources=None)`. When `sources` is `None` and `client` is not, `build_app` uses `Sources([client])`.

- [ ] **Step 1: Write the failing tests** `tests/api/test_sources_routing.py` (GPL header first):

```python
"""Removal and commands reach a device through the source of its
technology, and a missing source answers 503 (design 2026-09-11,
sections 6.2 and 6.3)."""

from __future__ import annotations

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store
from loxmatter.sources import DeviceCall, Sources


class _FakeZigbeeSource:
    technology = "zigbee"
    connected = True

    def __init__(self) -> None:
        self.removed: list[str] = []
        self.sent: list[DeviceCall] = []

    async def remove(self, address: str) -> None:
        self.removed.append(address)

    async def send(self, call: DeviceCall) -> None:
        self.sent.append(call)


def _as_zigbee(store: Store, device_id: int) -> None:
    store._db.execute(
        "UPDATE device SET technology = 'zigbee', address = '00:12:4b:00:1c:a1:b2:c3'"
        " WHERE id = ?",
        (device_id,),
    )
    store._db.commit()


@pytest.fixture
async def zigbee_plug(tmp_path, fake_runtime, fake_client):
    """A plug stored as a Zigbee device, served by an app whose `Sources`
    either includes a Zigbee source or does not."""
    opened: list[tuple[httpx.AsyncClient, Store]] = []

    async def make(*, with_zigbee: bool):
        store = Store(tmp_path / f"s-{with_zigbee}.sqlite")
        snapshot = load_snapshot("ikea_grillplats_plug.json")
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot))
        _as_zigbee(store, device_id)
        zigbee = _FakeZigbeeSource()
        sources = Sources([fake_client, zigbee] if with_zigbee else [fake_client])
        app = build_app(
            store, sources.send, fake_runtime(store), client=fake_client, sources=sources
        )
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        await authenticate(store, client)
        opened.append((client, store))
        on_key = next(c.key for c in store.commands(device_id) if c.slug == "on")
        return client, device_id, on_key, zigbee

    yield make
    for client, store in opened:
        await client.aclose()
        store.close()


async def test_removal_goes_to_the_devices_own_source(zigbee_plug, fake_client):
    """Fault to prove it: in `remove_device`, call
    `_require_client().remove(device.address)` instead of going through
    `sources.get(device.technology)`."""
    client, device_id, _, zigbee = await zigbee_plug(with_zigbee=True)

    response = await client.delete(f"/api/devices/{device_id}")

    assert response.status_code == 204
    assert zigbee.removed == ["00:12:4b:00:1c:a1:b2:c3"]
    assert fake_client.removed == []


async def test_removal_without_the_devices_source_is_503(zigbee_plug):
    """Fault to prove it: map `SourceNotConfiguredError` to 502 in
    `remove_device`."""
    client, device_id, _, _ = await zigbee_plug(with_zigbee=False)

    response = await client.delete(f"/api/devices/{device_id}")

    assert response.status_code == 503
    assert "zigbee" in response.json()["detail"]


async def test_cmd_without_the_devices_source_is_503(zigbee_plug):
    """Fault to prove it: delete the `except SourceNotConfiguredError`
    branch in `/cmd/{key}/{value}` - the generic handler then answers 502."""
    client, _, on_key, _ = await zigbee_plug(with_zigbee=False)

    response = await client.get(f"/cmd/{on_key}/1")

    assert response.status_code == 503


async def test_the_control_route_without_the_devices_source_is_503(zigbee_plug):
    """Fault to prove it: delete the `except SourceNotConfiguredError`
    branch in `POST /api/commands/{key}`."""
    client, _, on_key, _ = await zigbee_plug(with_zigbee=False)

    response = await client.post(f"/api/commands/{on_key}", json={"value": "1"})

    assert response.status_code == 503


async def test_cmd_reaches_the_zigbee_source(zigbee_plug):
    client, _, on_key, zigbee = await zigbee_plug(with_zigbee=True)

    response = await client.get(f"/cmd/{on_key}/1")

    assert response.status_code == 200
    assert [call.technology for call in zigbee.sent] == ["zigbee"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/api/test_sources_routing.py -q`
Expected: FAIL with `TypeError: build_app() got an unexpected keyword argument 'sources'`.

- [ ] **Step 3: Accept `sources` in `build_app`.** In `src/loxmatter/loxone/server.py` import `from loxmatter.sources import DeviceCall, SourceNotConfiguredError, Sources`, add the parameter after `client`:

```python
    sources: Sources | None = None,
```

and as the first lines of the body:

```python
    # Callers that predate the device source boundary pass only `client`;
    # for them the registry is the Matter client alone, which is exactly
    # what they had (design 2026-09-11, section 6.2).
    if sources is None and client is not None:
        sources = Sources([client])
```

Pass it on: `build_device_router(store, client, runtime, thread_dataset_source, sources)`.

In `command` (`/cmd/{key}/{value}`), insert before `except Exception as exc:  # every device problem becomes 502`:

```python
        except SourceNotConfiguredError as exc:
            # Nothing was asked of the device, so this is not 502.
            raise HTTPException(status_code=503, detail=str(exc)) from exc
```

- [ ] **Step 4: Same branch in `src/loxmatter/api/control.py`.** Import `SourceNotConfiguredError` from `loxmatter.sources` and insert the identical `except SourceNotConfiguredError` branch before `except Exception as exc:  # every device problem becomes 502` in the device half of `POST /api/commands/{key}`.

- [ ] **Step 5: Removal through `Sources`.** In `src/loxmatter/api/devices.py` import `from loxmatter.sources import SourceNotConfiguredError, Sources`, add `sources: Sources | None = None` as the last parameter of `build_device_router`, and replace `remove_device`:

```python
    @router.delete("/devices/{device_id}", status_code=204)
    async def remove_device(device_id: int) -> None:
        device = _require_device(device_id)
        if sources is None:
            # `build_app` derives `sources` from `client`, so this is the
            # app started without any device connection - the same 503 the
            # route answered before.
            raise HTTPException(
                status_code=503,
                detail=i18n.t("api.devices.fail_no_matter_client"),
            )
        try:
            source = sources.get(device.technology)
        except SourceNotConfiguredError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        try:
            # Order: see module docstring - the fabric first, then the store.
            await source.remove(device.address)
        except MatterUnavailableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        store.forget_device(device.id)
```

- [ ] **Step 6: Pass `sources` from startup.** In `src/loxmatter/cli.py`, `_run`, add `sources=sources,` to the `build_app(...)` call after `client=client,`.

- [ ] **Step 7: Run, prove, check**

Run: `uv run pytest tests/api/test_sources_routing.py tests/api/test_devices.py tests/api/test_control.py tests/loxone/test_server.py -q` — PASS. Introduce each fault named in the new docstrings one at a time; see the matching test FAIL; revert; PASS. Paste outputs.
Run: `grep -rn "TRANSITIONAL" src tests scripts` — Expected: no hits.
Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q && uv run python scripts/check_language.py` — all pass.

- [ ] **Step 8: Commit**

```bash
git add -A src tests
git commit -m "feat(api): reach devices through the source of their technology

Removal and commands now go through the Sources registry. A device whose
technology has no running source answers 503 with its own message instead
of 502, because nothing was asked of the device.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: The Transport Badge on the Tile

**Files:**
- Modify: `src/loxmatter/api/models.py`, `src/loxmatter/api/devices.py`
- Modify: `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`, `CHANGELOG.md`
- Test: `tests/api/test_devices.py`, `tests/api/test_web.py`

**Interfaces:**
- Consumes: `transport_for` (Task 1), `StoredDevice.technology/network_features` (Task 2).
- Produces: `DeviceOut.transport: str | None`; `app.js` method `transportBadge(device) -> {symbol: string, label: string} | null`; sprite symbols `i-transport-thread`, `i-transport-ip`; i18n keys `web.devices.transport_thread`, `web.devices.transport_ip`.

- [ ] **Step 1: Write the failing API test.** Append to `tests/api/test_devices.py`:

```python
async def test_the_device_list_says_how_a_device_is_connected(api):
    """The GRILLPLATS fixture reports NetworkCommissioning FeatureMap 2.
    Fault to prove it: return `None` from `transport_for` for Thread."""
    client, _, _, _ = api
    devices = (await client.get("/api/devices")).json()
    assert devices[0]["transport"] == "thread"
    assert devices[0]["technology"] == "matter"
    assert "node_id" not in devices[0]
```

- [ ] **Step 2: Write the failing web tests.** Append to `tests/api/test_web.py`:

```python
@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_transport_badge_helper_maps_transports_to_symbols_and_labels():
    """Runs the real `transportBadge` in node. Fault to prove it: swap the
    two symbol names in its map."""
    values = _app_state(
        """
        console.log(JSON.stringify({
          thread: state.transportBadge({ transport: "thread" }),
          ip: state.transportBadge({ transport: "ip" }),
          none: state.transportBadge({ transport: null }),
          zigbee: state.transportBadge({ transport: "zigbee" }),
        }));
        """,
        translations={
            "web.devices.transport_thread": "Matter over Thread",
            "web.devices.transport_ip": "Matter over IP",
        },
    )
    assert values["thread"] == {"symbol": "i-transport-thread", "label": "Matter over Thread"}
    assert values["ip"] == {"symbol": "i-transport-ip", "label": "Matter over IP"}
    assert values["none"] is None
    # No Zigbee glyph before the Zigbee spec; no badge beats a wrong one.
    assert values["zigbee"] is None


async def test_every_transport_badge_symbol_exists(api):
    """A `<use>` pointing at a missing symbol silently draws nothing - the
    same reason `test_every_category_has_an_icon_symbol` exists."""
    client, _, _ = api
    page = (await client.get("/")).text
    for symbol in ("i-transport-thread", "i-transport-ip"):
        assert f'<symbol id="{symbol}"' in page, symbol


def _element_at(markup: str, open_index: int, tag: str) -> str:
    """The complete element whose opening tag starts at `open_index`,
    found by counting nested opening and closing tags of the same name -
    so "inside" means inside, not merely "somewhere after"."""
    depth = 0
    for match in re.finditer(rf"<{tag}\b|</{tag}>", markup[open_index:]):
        depth += -1 if match.group().startswith("</") else 1
        if depth == 0:
            return markup[open_index : open_index + match.end()]
    raise AssertionError(f"unbalanced <{tag}> starting at {open_index}")


async def test_the_badge_sits_inside_the_device_tiles_category_icon(api):
    """Only the device tile - a group has no single transport. Checks the
    badge is nested in the `.type-badge` that shows `device.category`, not
    merely present somewhere after it."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    icon = page.index("'#i-cat-' + device.category")
    type_badge = _element_at(page, page.rindex('<span class="type-badge">', 0, icon), "span")
    assert "transportBadge(device)" in type_badge
    assert "transportBadge(deviceGroup)" not in page


def test_the_transport_labels_exist_in_both_languages():
    from loxmatter import i18n

    for key in ("web.devices.transport_thread", "web.devices.transport_ip"):
        english = i18n.t(key)
        i18n.set_language("de")
        try:
            german = i18n.t(key)
        finally:
            i18n.set_language("en")
        assert english and german and english != key
```

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/api/test_devices.py::test_the_device_list_says_how_a_device_is_connected tests/api/test_web.py -q -k "transport or badge"`
Expected: FAIL — `KeyError: 'transport'`, `state.transportBadge is not a function`, missing symbols, missing i18n keys.

- [ ] **Step 4: API.** `src/loxmatter/api/models.py`, `DeviceOut`, after `address: str`:

```python
    # "thread", "ip", "zigbee" or None - see `profiles/transport.py`. The
    # tile shows a badge only when this is not None.
    transport: str | None
```

`src/loxmatter/api/devices.py`: import `from loxmatter.profiles.transport import transport_for`; in `_device_out` add `transport=transport_for(device.technology, device.network_features),` after `address=device.address,`.

- [ ] **Step 5: i18n.** Add to `src/loxmatter/i18n/strings.yaml` directly after `web.devices.offline`:

```yaml
web.devices.transport_thread:
  # Tooltip and accessible name of the small badge on a tile's category
  # icon (design 2026-09-11, section 5.3). No official logo is used:
  # "Matter" is a trademark of the Connectivity Standards Alliance.
  en: "Matter over Thread"
  de: "Matter über Thread"
web.devices.transport_ip:
  en: "Matter over IP"
  de: "Matter über IP"
```

- [ ] **Step 6: `app.js`.** Add directly after `deviceCardClass(device) { … },`:

```javascript
    // The badge on a tile's category icon (design 2026-09-11, section 5.3).
    // `null` hides it: a device whose transport is unknown gets no badge
    // rather than a guessed one, and Zigbee has no glyph before the Zigbee
    // spec adds one. The map is the only place a transport meets its
    // symbol - index.html just renders what this returns.
    transportBadge(device) {
      const symbols = { thread: "i-transport-thread", ip: "i-transport-ip" };
      const symbol = symbols[device.transport];
      if (!symbol) return null;
      return { symbol, label: t("web.devices.transport_" + device.transport) };
    },
```

- [ ] **Step 7: Sprite symbols.** In `src/loxmatter/web/index.html`, directly before the closing `</svg>` of the sprite block (after the `i-battery` symbol):

```html
      <!-- Transport badges on a device tile's category icon (design
           2026-09-11, section 5.3). Neutral pictograms, not the Matter or
           Thread logos: those are trademarks. Thread is a small mesh of
           three nodes, IP a globe. -->
      <symbol id="i-transport-thread" viewBox="0 0 24 24">
        <circle cx="12" cy="5" r="2.4" />
        <circle cx="5" cy="18" r="2.4" />
        <circle cx="19" cy="18" r="2.4" />
        <path d="M10.8 7.1 6.2 15.9M13.2 7.1l4.6 8.8M7.4 18h9.2" />
      </symbol>
      <symbol id="i-transport-ip" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="9" />
        <path d="M3 12h18" />
        <path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18z" />
      </symbol>
```

- [ ] **Step 8: Tile markup.** In the device tile (the `.type-badge` whose icon uses `device.category`, not `deviceGroup.category` and not the group candidate picker), replace:

```html
                    <span class="type-badge">
                      <svg class="icon"><use :href="'#i-cat-' + device.category"></use></svg>
                    </span>
```

with:

```html
                    <span class="type-badge">
                      <svg class="icon"><use :href="'#i-cat-' + device.category"></use></svg>
                      <!-- How the device is connected, as a badge on WHAT it
                           is (design 2026-09-11, section 5.3). Chosen over an
                           icon beside the name, which competes with the
                           offline pill, and over the footer, which carries
                           export state. -->
                      <template x-if="transportBadge(device)">
                        <span
                          class="transport-badge"
                          role="img"
                          :title="transportBadge(device).label"
                          :aria-label="transportBadge(device).label"
                        >
                          <svg class="icon" aria-hidden="true"><use :href="'#' + transportBadge(device).symbol"></use></svg>
                        </span>
                      </template>
                    </span>
```

- [ ] **Step 9: CSS.** In `src/loxmatter/web/style.css`, add `position: relative;` to the existing `.type-badge` rule, and directly after that rule:

```css
/* The transport badge sits on the category icon's lower right corner
   (design 2026-09-11, section 5.3). `.device-card` already has
   `overflow: visible`, so the overhang is not clipped, and the head's
   0.4rem gap is wider than the 0.35rem overhang, so it never touches
   the name field. */
.transport-badge {
  position: absolute;
  right: -0.35rem;
  bottom: -0.35rem;
  width: 1.05rem;
  height: 1.05rem;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--surface);
  color: var(--text-muted);
  border: 1px solid var(--border);
}
.transport-badge .icon {
  width: 0.72rem;
  height: 0.72rem;
  stroke-width: 2.2;
}
```

- [ ] **Step 10: CHANGELOG.** Under `## [Unreleased]` → `### Added` in `CHANGELOG.md`, append:

```markdown
- **Thread or IP at a glance.** Every device tile now carries a small badge on
  its icon showing whether the device talks to the bridge over Thread or over
  your IP network (Wi-Fi or Ethernet). Hover it for the name. A device that
  does not say how it is connected gets no badge rather than a guess.
```

- [ ] **Step 11: Run, prove, check**

Run: `uv run pytest tests/api/test_devices.py tests/api/test_web.py -q` — PASS. Faults: swap the two symbol names in `transportBadge` → the helper test FAILS; revert. Return `None` for the Thread branch in `transport_for` → the API test FAILS; revert. Move the `<template x-if="transportBadge(device)">` block after the closing `</span>` of `.type-badge` → the nesting test FAILS; revert. Paste outputs.
Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q && uv run python scripts/check_language.py` — all pass (`test_the_stylesheet_has_balanced_braces` included).

- [ ] **Step 12: Look at it.** The string tests above cannot see layout. The spec (7.3) names a cut-out harness; the demo server is used instead because it serves the real page with real fixture devices, so the badge's data comes through the actual API rather than invented JSON — and the `transportBadge` behaviour is already run for real in node by Step 2. Start the demo server and check the grid at the narrowest real tile width (260 px, two columns at a 572 px window):

```bash
cd /Users/lucienkerl/Development/matter-loxone/.claude/worktrees/german-to-english-translation-f84003
uv run python scripts/dev_web_server.py --demo
```

In a second shell, save as `$SCRATCHPAD/badge_check.py` (use the session scratchpad, not the repo) and run with `uv run --with playwright python $SCRATCHPAD/badge_check.py`:

```python
import asyncio

from playwright.async_api import async_playwright

PASSWORD = "loxmatter-demo"


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 572, "height": 900})
        await page.goto("http://127.0.0.1:8420/")
        await page.fill('input[type="password"]', PASSWORD)
        await page.click('button:has-text("Log in")')
        await page.wait_for_selector(".device-card")
        report = await page.evaluate(
            """() => [...document.querySelectorAll('.device-card')].map((card) => {
              const badge = card.querySelector('.transport-badge');
              const name = card.querySelector('.device-name').getBoundingClientRect();
              const c = card.getBoundingClientRect();
              if (!badge) return { card: c.width, badge: null };
              const b = badge.getBoundingClientRect();
              return {
                card: Math.round(c.width),
                label: badge.getAttribute('aria-label'),
                insideCard: b.left >= c.left && b.right <= c.right && b.bottom <= c.bottom,
                clearOfName: b.right <= name.left,
              };
            })"""
        )
        print(report)
        await page.screenshot(path="badge_check.png", full_page=True)
        await browser.close()


asyncio.run(main())
```

Expected: the three IKEA demo tiles ("Coffee machine", "Kitchen spots", "Hallway button") report `label: "Matter over Thread"`, `insideCard: true`, `clearOfName: true`, and `card` about 260; the "Living room lamp" tile (`example_light.json`, no NetworkCommissioning) reports `badge: null`. That null is correct behaviour, not a failure. Open `badge_check.png` and look at it. If the login selector differs, read `scripts/capture_screenshots.py` for the one it uses. Report the printed list and attach the screenshot path; do not commit the screenshot.

- [ ] **Step 13: Commit**

```bash
git add -A src tests CHANGELOG.md
git commit -m "feat(web): show on each tile whether a device is on Thread or IP

A small badge on the category icon, derived from the stored FeatureMap.
Neutral pictograms instead of logos, a translated tooltip, and no badge at
all when the device does not say how it is connected.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Verification on the Whole Branch and on the Test Pi

**Files:** none changed unless a check fails.

**Interfaces:** consumes everything above.

- [ ] **Step 1: Leftovers**

Run:
```bash
cd /Users/lucienkerl/Development/matter-loxone/.claude/worktrees/german-to-english-translation-f84003
grep -rn "TRANSITIONAL" src tests scripts
grep -rn "device_id_for_node\|MatterCall\|to_matter_calls\|send_command\|remove_node(\|follow_node(" src tests scripts | grep -v "_follow_node\|upstream.remove_node\|\.remove_node(int("
grep -rn "node_id" src --include='*.py' | grep -v "src/loxmatter/matter/client.py\|src/loxmatter/cli.py\|src/loxmatter/matter/otbr.py\|src/loxmatter/matter/models.py"
```
Expected: the first two print nothing. The third prints only (a) `model/store.py`'s schema, migration 9, its version comment, the rollback-compatibility writer and the startup repair — `node_id` is kept and still written on purpose (spec section 4.1, "Why `node_id` stays"), but no store method may READ it into a row type — and (b) docstring mentions in `api/devices.py`/`api/control.py` of matter-server's own API (`MatterClient.remove_node(node_id)`, `write_attribute(node_id, …)`). Read each hit and confirm it is one of those.

- [ ] **Step 2: Full checks**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q && uv run python scripts/check_language.py`
Expected: all pass.

- [ ] **Step 3: Migrate a copy of the real database.** Never touch the production file in place.

```bash
S=/private/tmp/claude-501/-Users-lucienkerl-Development-matter-loxone--claude-worktrees-german-to-english-translation-f84003/678dba82-5e21-480f-951f-35bd839b010d/scratchpad
# A consistent copy through SQLite's own backup API, taken inside the
# container (which has Python), so a write by the running bridge cannot
# leave a torn file.
ssh pi@10.0.1.56 "docker exec loxmatter python -c \"import sqlite3; s = sqlite3.connect('/data/loxmatter.sqlite'); d = sqlite3.connect('/tmp/loxmatter-copy.sqlite'); s.backup(d); d.close(); s.close()\" && docker cp loxmatter:/tmp/loxmatter-copy.sqlite /tmp/loxmatter-copy.sqlite && docker exec loxmatter rm /tmp/loxmatter-copy.sqlite"
scp pi@10.0.1.56:/tmp/loxmatter-copy.sqlite "$S/pi-v8.sqlite"
ssh pi@10.0.1.56 "rm /tmp/loxmatter-copy.sqlite"
cp "$S/pi-v8.sqlite" "$S/pi-v9.sqlite"
uv run python - "$S/pi-v8.sqlite" "$S/pi-v9.sqlite" <<'EOF'
import sqlite3, sys
from loxmatter.model.store import Store

before = sqlite3.connect(sys.argv[1])
signal_keys = {r[0] for r in before.execute("SELECT key FROM signal")}
command_keys = {r[0] for r in before.execute("SELECT key FROM command")}
nodes = dict(before.execute("SELECT id, node_id FROM device WHERE active = 1"))
before.close()

store = Store(sys.argv[2])
after_signals = {r[0] for r in store._db.execute("SELECT key FROM signal")}
after_commands = {r[0] for r in store._db.execute("SELECT key FROM command")}
assert after_signals == signal_keys, "signal keys changed"
assert after_commands == command_keys, "command keys changed"
for device in store.devices():
    assert device.address == str(nodes[device.id]), device
for key in command_keys:
    store.resolve_command(key)
print(f"{len(signal_keys)} signal keys and {len(command_keys)} command keys unchanged;",
      f"{len(nodes)} devices addressed by their former node IDs")
EOF
```
Expected: the final line with the counts, no assertion error. The copy may be at a schema version below 8 if production runs an older release; the chain migrates it all the same, and the comparison still holds because every version up to 8 has `device.node_id`.

- [ ] **Step 4: Run an own instance against the real devices** (production instance on port 8080 stays untouched):

```bash
uv run loxmatter run --url ws://10.0.1.56:5580/ws --miniserver 127.0.0.1 \
  --listen 8099 --host 127.0.0.1 --store-path "$S/pi-v9.sqlite"
```

Set a password on the copy first if it has none, as described in the memory note on hardware verification (`Store.auth.reset_password(hash_password(...))`), then log in with `POST /auth/login` (not `/api/auth/login`).

- [ ] **Step 5: Switch a lamp through the new path, then restore it.** For a KAJPLATS (node 21 or 22):
1. Read and note its current on/off and level from `GET /api/devices/<id>/signals` of the running instance (not from `snapshots()`, which returns matter-server's cache).
2. `GET /cmd/<on-key>/1`, then `GET /cmd/<level-key>/40`; poll the signals route with retries for up to 20 seconds until on/off and level reflect it.
3. Restore the noted state the same way and confirm it by polling.
Record every request, response code and the observed values.

- [ ] **Step 6: Look at the badges.** Open `http://127.0.0.1:8099/` of that instance in a browser (Playwright or the Browser pane; the embedded browser cannot fill Alpine login forms, so use Playwright's `page.fill` if needed) and confirm: ten tiles with the Thread badge, two with the IP badge (Tasmota-Plug-4 and Tasmota-Plug-6), and the tooltip text. Take a screenshot into the scratchpad and look at it. A device offline at startup keeps `network_features = NULL` until its next start and shows no badge — note which, if any.

- [ ] **Step 7: Report.** Summarise Steps 1–6 with the actual outputs. Stop the instance. Do not commit anything from this task unless a check failed and was fixed, in which case the fix gets its own commit with its own reason.
