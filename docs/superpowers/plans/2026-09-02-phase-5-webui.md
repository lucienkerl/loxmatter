# Phase 5: WebUI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A device can be commissioned, viewed, controlled, and exported through the browser — and when something doesn't work, the UI shows on which side the problem lies.

**Architecture:** The FastAPI service from Phase 4 gets a second face. Under `/cmd` and `/resync` it keeps talking to the Miniserver; under `/api` it talks to the UI. The domain logic is not duplicated: control goes through the same `commands/` that the Loxone endpoint uses, and the live values come from the same Matter subscription that feeds the UDP sender. The UI itself is static HTML with bundled Alpine.js, served directly by FastAPI.

**Tech Stack:** Python 3.12, `uv`, `pytest`, `ruff`, `mypy` (strict), FastAPI, `httpx2` for tests, Alpine.js (bundled, no build step).

## Global Constraints

- **Tests run without hardware and without network access.** A UDP socket on `127.0.0.1` and FastAPI's in-process test client do not count as network access (Spec 10.1).
- **German in prose, comments, docstrings, help text, error messages, and in the UI**, English in identifiers — **also in tests, also in JavaScript, also in JSON field names**. This rule was violated six times in Phase 4 and corrected six times; it applies to everything that goes into the repository.
- **All data classes immutable** (`frozen=True`), unless there is a reason against it.
- **Keys are immutable** (Spec 6.2). This phase displays them and never changes them. The title is freely editable, the key is not — the UI must make that visible.
- **No second conversion.** Control goes through `commands.translate`, values through `loxone.values`. A copy in the API drifts (Spec 4.2).
- **No build step in the frontend.** Alpine.js is bundled as a file, not loaded from a CDN: the bridge runs in installations without internet.
- `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy` must stay clean. ruff also formats Python blocks in Markdown.
- The unsanitized templates under `tests/fixtures/VirtualIn/` and `tests/fixtures/VirtualOut/` contain credentials of a real installation and are git-ignored. **Do not read.**

---

## What this phase explicitly does not build

Spec 8.2 draws the line, and it's important enough to repeat here: **a commissioning and diagnostics tool, not a smart-home UI.** No scenes, no schedules, no automation, no favorites pages, no rooms, no user management, no app. All of that is Loxone's job, and a half-baked second control UI next to it would be worse than none.

If, while building, the urge comes up to "just quickly" add a grouping or a scene: don't. It stands as a non-goal in the spec.

## Why control is not a convenience feature

Spec 8.1: View 1 is the project's diagnostics tool. If a lamp doesn't switch via Loxone, a click in the UI separates the two possible causes — if the device reacts here, the fault is in the Loxone wiring or the export; if it doesn't react, the fault is in Matter, Thread, or at the device.

For a tool that runs in other people's installations, that is the difference between an answerable and an unanswerable bug report. Every decision in this phase that has to choose between "prettier" and "states more precisely where the fault sits" chooses the latter.

## Security: what this phase makes reachable

So far the HTTP service was an endpoint for the Miniserver. From this phase on, it is a control UI that commissions, removes, and switches devices — **without any authentication, bound to all interfaces.** This was noted in Phase 4 as a deliberately accepted point, because only `/cmd` and `/resync` were reachable.

With commissioning, the weight changes: whoever reaches the port can throw devices out of the fabric. Task 8 of this phase addresses this explicitly; until then, the service is considered not exposable, and that belongs in the operating instructions, not in a silent assumption.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/loxmatter/matter/client.py` | additionally `commission_with_code`, `remove_node`, `set_thread_dataset` |
| `src/loxmatter/api/__init__.py` | — |
| `src/loxmatter/api/models.py` | response models of the REST API, separate from the storage models |
| `src/loxmatter/api/devices.py` | devices and signals: read, rename, export flag, remove |
| `src/loxmatter/api/control.py` | control and raw attribute writing |
| `src/loxmatter/api/export.py` | preview and download of the templates |
| `src/loxmatter/api/diagnostics.py` | UDP capture, command log, system check |
| `src/loxmatter/api/live.py` | WebSocket for live values |
| `src/loxmatter/loxone/server.py` | wires in the API routers, serves the UI |
| `src/loxmatter/web/index.html` | the four views |
| `src/loxmatter/web/app.js` | state and calls |
| `src/loxmatter/web/vendor/alpine.min.js` | bundled, no CDN |

---

### Task 1: Commissioning and Removal

So far the project could only read devices. They were commissioned in Phase 1 with a
throwaway script — that is the last gap between "reads a device" and "operates a
bridge".

**Files:**
- Modify: `src/loxmatter/matter/client.py`
- Create: `tests/matter/test_client_commissioning.py`

**Interfaces:**
- Consumes: the existing `session_factory`/`http_session_factory` seam
- Produces:
  - `async def commission_with_code(self, code: str) -> NodeSnapshot`
  - `async def remove_node(self, node_id: int) -> None`
  - `async def set_thread_dataset(self, dataset: str) -> None`
  - `CommissioningError(RuntimeError)` — German text

- [ ] **Step 1: Check the upstream signatures, don't assume them**

In Phase 4, two assumptions of this plan about `python-matter-server` were wrong, and
both would have caused silent failure. Check before you write:

```bash
uv run python -c "
import inspect
from matter_server.client.client import MatterClient
for name in ('commission_with_code', 'remove_node', 'set_thread_operational_dataset'):
    print(name, inspect.signature(getattr(MatterClient, name)))
"
```

Enter the actual signatures and return types into the docstring of `client.py`.
If something deviates, the library is authoritative — report it, instead of making it fit.

- [ ] **Step 2: Write the failing test**

`tests/matter/test_client_commissioning.py`:

```python
import pytest

from loxmatter.matter.client import BridgeMatterClient, CommissioningError


class FakeNode:
    def __init__(self, node_id: int, attributes: dict[str, object]):
        self.node_id = node_id
        self.available = True
        self.node_data = type("Data", (), {"attributes": attributes})()


class FakeUpstream:
    def __init__(self) -> None:
        self.nodes: list[FakeNode] = []
        self.removed: list[int] = []
        self.datasets: list[str] = []
        self.fail_with: Exception | None = None

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def start_listening(self, ready=None) -> None:
        if ready is not None:
            ready.set()

    def get_nodes(self) -> list[FakeNode]:
        return self.nodes

    async def commission_with_code(self, code: str, network_only: bool = False):
        if self.fail_with is not None:
            raise self.fail_with
        node = FakeNode(7, {"0/40/1": "IKEA of Sweden", "1/6/0": True})
        self.nodes.append(node)
        return node

    async def remove_node(self, node_id: int) -> None:
        self.removed.append(node_id)

    async def set_thread_operational_dataset(self, dataset: str) -> None:
        self.datasets.append(dataset)


@pytest.fixture
def client() -> tuple[BridgeMatterClient, FakeUpstream]:
    upstream = FakeUpstream()
    return (
        BridgeMatterClient(
            "ws://test/ws",
            session_factory=lambda _session: upstream,
            http_session_factory=lambda: type("S", (), {"close": lambda self: None})(),
        ),
        upstream,
    )


async def test_commissioning_returns_a_snapshot(client):
    bridge, _ = client
    await bridge.connect()
    snapshot = await bridge.commission_with_code("MT:ABC123")
    assert snapshot.node_id == 7
    assert snapshot.vendor_name == "IKEA of Sweden"
    await bridge.disconnect()


async def test_commissioning_without_connection_raises(client):
    bridge, _ = client
    with pytest.raises(Exception, match="nicht verbunden"):
        await bridge.commission_with_code("MT:ABC123")


async def test_a_failed_commissioning_says_so_in_german(client):
    bridge, upstream = client
    upstream.fail_with = RuntimeError("device not found")
    await bridge.connect()
    with pytest.raises(CommissioningError, match="Einlernen fehlgeschlagen"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_remove_node_reaches_upstream(client):
    bridge, upstream = client
    await bridge.connect()
    await bridge.remove_node(7)
    assert upstream.removed == [7]
    await bridge.disconnect()


async def test_thread_dataset_reaches_upstream(client):
    """Without a dataset, matter-server cannot tell a Thread device about a network."""
    bridge, upstream = client
    await bridge.connect()
    await bridge.set_thread_dataset("0e08...")
    assert upstream.datasets == ["0e08..."]
    await bridge.disconnect()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/matter/test_client_commissioning.py -v`
Expected: FAIL with `ImportError: cannot import name 'CommissioningError'`

- [ ] **Step 4: Write minimal implementation**

Add to `src/loxmatter/matter/client.py`:

```python
class CommissioningError(RuntimeError):
    """Commissioning a device failed at the device itself (e.g.
    wrong code, device already sits in another ecosystem, timeout
    during the interview).

    A connection loss to matter-server WHILE commissioning is
    explicitly separated from this: commission_with_code() catches
    `NotConnected`/`ConnectionClosed`/`CannotConnect` separately and raises
    `MatterUnavailableError` for that instead, because only that way can it be
    distinguished whether the device refused or matter-server was
    unreachable (Spec 8.1/9). The original exception is preserved via
    `__cause__`."""


async def commission_with_code(self, code: str) -> NodeSnapshot:
    """Commissions a device via its pairing code.

    The code is the 11-digit number or the 21-character MT: code from the
    device or its packaging. If the device already sits in another
    ecosystem, the printed code no longer works - then a multi-admin code is
    needed from there (Spec 7.1).
    """
    upstream = self._require_upstream()

    # Lazily imported like _default_session_factory: tests with a
    # fake upstream should never have to load matter_server.
    from matter_server.client.exceptions import CannotConnect, ConnectionClosed, NotConnected

    try:
        node = await upstream.commission_with_code(code)
    except (NotConnected, ConnectionClosed, CannotConnect) as exc:
        # A connection loss to matter-server is not a refusal by the
        # device — must come BEFORE the generic except Exception below,
        # otherwise it would be caught there too and reported as
        # CommissioningError (Spec 8.1/9 requires the distinction).
        msg = f"matter-server nicht erreichbar: {exc}"
        raise MatterUnavailableError(msg) from exc
    except Exception as exc:
        raise CommissioningError(f"Einlernen fehlgeschlagen: {exc}") from exc
    return NodeSnapshot.from_raw(node.node_id, {"attributes": node.node_data.attributes})


async def remove_node(self, node_id: int) -> None:
    """Removes a device from the fabric."""
    await self._require_upstream().remove_node(node_id)


async def set_thread_dataset(self, dataset: str) -> None:
    """Passes the Thread credentials to matter-server.

    Without this step, commissioning a Thread device fails with
    "Required network information not provided" - the controller finds the
    device via BLE but cannot tell it about a network.
    """
    await self._require_upstream().set_thread_operational_dataset(dataset)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/matter/test_client_commissioning.py -v`
Expected: PASS, 8 tests

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/matter/client.py tests/matter/test_client_commissioning.py
git commit -m "feat(matter): Geraete einlernen und entfernen"
```

---

### Task 2: Device and Signal API

**Files:**
- Create: `src/loxmatter/api/__init__.py`
- Create: `src/loxmatter/api/models.py`
- Create: `src/loxmatter/api/devices.py`
- Create: `tests/api/test_devices.py`
- Modify: `src/loxmatter/model/store.py` (queries the API needs; **new column
  `signal.exported` — see "Schema migration" below, do NOT simply append to `_SCHEMA`)
- Create: `tests/model/test_store_migration.py`

**Interfaces:**
- Consumes: `Store`, `StoredSignal`, `BridgeMatterClient`, `Runtime`
- Produces:
  - `DeviceOut`, `SignalOut` — frozen Pydantic models
  - `build_device_router(store, client, runtime) -> APIRouter` with prefix `/api`

**Schema migration (review fix Important #1, 2026-09-02 — added here because an
earlier version of this plan taught a new column without providing a migration
for it):**

`_SCHEMA` uses `CREATE TABLE IF NOT EXISTS` — that never reaches an already
existing table with a new column. Simply appending the `signal.exported` column to
this string is therefore NOT enough: against a database that was created before
this task (`loxmatter export`/`loxmatter run` from Phase 4 or earlier runs of this
phase), it stays invisible, and `Store.signals()` fails with `IndexError: No item
with that key`. Because the database carries the signal keys — the wiring in
Loxone, see the module docstring of `store.py` — the only remedy without a
migration is deleting the entire database, which destroys every key and every
existing wiring in the house.

The migration manages `PRAGMA user_version` as the schema version:

- Version 0 is "before this migration logic" — any database whose
  `user_version` has never been set, both a genuine old database and (before the
  first `Store(...)` call stamps it) a freshly created one.
- Version 1 adds `signal.exported` (`ALTER TABLE ... ADD COLUMN`) and
  backfills existing rows retroactively — **not** uniformly with the
  column default, but following the same rule as a freshly registered
  signal: exportable (ANALOG/DIGITAL) → `True`, otherwise (TEXT, NONE) → `False`
  (see `is_exportable` below).
- Runs in a transaction: `ALTER TABLE ADD COLUMN` is fully transactional in
  SQLite, a `db.rollback()` on failure also reverts steps of this run that
  already executed, `PRAGMA user_version` is only incremented on complete
  success.
- Already up to date: no write access, a genuine no-op — every start except
  the very first one after a schema change.

Tests for this (`tests/model/test_store_migration.py`) build the old database
directly via `sqlite3` with the schema state BEFORE `exported` (not through
`Store`, which already creates the column), insert a device and several signals
with different `exportability`, and then open them with the current `Store`:
reading works correctly, the backfill is right, the version is then at 1, and
reopening is a no-op (an `exported` set by the user in the meantime is preserved
instead of being overwritten by the backfill).

**Watch out, signature change:** `build_app` from Phase 4 currently takes
`(store, invoke, runtime)`. For commissioning it additionally needs the Matter client:

```python
def build_app(
    store: Store,
    invoke: Invoker,
    runtime: Runtime,
    client: BridgeMatterClient | None = None,
) -> FastAPI:
```

`client=None` means: the commissioning routes respond with 503 and a message that
says why. That way the existing tests from Phase 4 that call `build_app` with
three arguments stay valid — check that instead of assuming it.

Routes:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/devices` | list with online status and signal count |
| GET | `/api/devices/{device_id}` | one device with the most important live values |
| GET | `/api/devices/{device_id}/signals` | complete tree |
| PATCH | `/api/devices/{device_id}` | rename device |
| PATCH | `/api/signals/{key}` | change title, set export flag |
| POST | `/api/devices/commission` | commission pairing code |
| DELETE | `/api/devices/{device_id}` | remove device |

- [ ] **Step 1: Write the failing test**

`tests/api/test_devices.py`:

```python
import json
from pathlib import Path

import httpx2 as httpx
import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
async def api(tmp_path):
    from loxmatter.loxone.server import build_app

    store = Store(tmp_path / "t.sqlite")
    snapshot = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(store, _no_invoke, _fake_runtime(store), client=_FakeClient())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, store, device_id
    store.close()


async def test_device_list_carries_name_and_signal_count(api):
    client, _, device_id = api
    response = await client.get("/api/devices")
    assert response.status_code == 200
    devices = response.json()
    assert len(devices) == 1
    assert devices[0]["id"] == device_id
    assert "GRILLPLATS" in devices[0]["label"]
    assert devices[0]["signal_count"] == 159


async def test_signal_tree_marks_what_cannot_be_exported(api):
    """Spec 6.6: values that cannot be mapped are displayed, but not exportable."""
    client, _, device_id = api
    signals = (await client.get(f"/api/devices/{device_id}/signals")).json()
    assert len(signals) == 159
    assert sum(1 for s in signals if s["exportable"]) == 109
    unexportable = next(s for s in signals if not s["exportable"])
    assert unexportable["reason"]


async def test_signal_carries_its_immutable_key_and_editable_title(api):
    client, _, device_id = api
    signals = (await client.get(f"/api/devices/{device_id}/signals")).json()
    signal = signals[0]
    assert signal["key"].startswith(f"d{device_id}_")
    assert "title" in signal


async def test_renaming_a_signal_leaves_its_key_alone(api):
    """Spec 6.2: the key is the wiring in Loxone."""
    client, store, device_id = api
    before = {s.ref: s.key for s in store.signals(device_id)}
    key = next(iter(before.values()))
    response = await client.patch(f"/api/signals/{key}", json={"title": "Kaffeemaschine"})
    assert response.status_code == 200
    assert {s.ref: s.key for s in store.signals(device_id)} == before
    assert any(s.title == "Kaffeemaschine" for s in store.signals(device_id))


async def test_the_key_cannot_be_changed_through_the_api(api):
    client, store, device_id = api
    key = store.signals(device_id)[0].key
    response = await client.patch(f"/api/signals/{key}", json={"key": "d99_9_boese"})
    assert response.status_code in (200, 422)
    assert any(s.key == key for s in store.signals(device_id))


async def test_unknown_signal_yields_404(api):
    client, _, _ = api
    assert (
        await client.patch("/api/signals/d1_1_gibtsnicht", json={"title": "x"})
    ).status_code == 404


async def test_unknown_device_yields_404(api):
    client, _, _ = api
    assert (await client.get("/api/devices/999/signals")).status_code == 404
```

The helper functions `_no_invoke`, `_fake_runtime`, and `_FakeClient` belong in a
`tests/api/conftest.py` — every task of this phase needs them.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_devices.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.api'`

- [ ] **Step 3: Response models**

`src/loxmatter/api/models.py`:

```python
"""Response models of the REST API.

Deliberately separate from the storage models in `model.store`: what the
UI sees is a view onto the state, not a mapping of the tables. If the
schema changes, the API does not necessarily change.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SignalOut(BaseModel):
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


class DeviceOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    node_id: int
    label: str
    online: bool
    signal_count: int
    exportable_count: int
```

- [ ] **Step 4: Write the router**

`src/loxmatter/api/devices.py` builds the router. The key points:

```python
@router.patch("/signals/{key}")
async def rename_signal(key: str, patch: SignalPatch) -> SignalOut:
    """Changes title and export flag. The key stays untouched.

    Spec 6.2: the key is the wiring in Loxone. If it were changeable here,
    a click in the UI could silently kill a block in the house. The model
    `SignalPatch` therefore has no field for it at all - a `key` sent along
    is discarded, not applied.
    """
```

`SignalPatch` carries exclusively `title: str | None` and `exported: bool | None`.

`rename_signal` must — like every device-bound route of this router — first
check whether the device behind `signal_by_key(key).device_id` is still active
before changing anything (review fix Important #4, 2026-09-02): otherwise the
row of a device removed via `DELETE /api/devices/{id}` stays readable and
mutable through its key, even though `GET /api/devices/{id}` for the same device
already reports 404. 404 with a German message that says the device was
removed — not the generic "unknown device ID" message from `_require_device`,
which does not distinguish between "never existed" and "removed".

`register_signals` in `store.py` sets the default of `exported` when a signal
is first registered to `is_exportable(profile.exportability)` — the same
function that `_signal_out`/`_device_out` below call for `exportable`/
`exportable_count` (`profiles.table.is_exportable`, exportable exactly
for ANALOG/DIGITAL). A second, independently written version of the same
rule (say, `exportability is not Exportability.NONE`, which would wrongly
include TEXT) is exactly what review fix Important #2 (2026-09-02) had to
fix — both places in this task must call the same function, not
independently reproduce the same idea.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/api/ -v`
Expected: PASS, 22 tests (7 from this plan draft plus 15 that were actually added
in the course of this task: commissioning, removal, the export flag, and the
review fix for signal access to an already-removed device)

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/api tests/api
git commit -m "feat(api): Geraete und Signale lesen und benennen"
```

---

### Task 3: WebSocket for Live Values

Spec 8.3: the same subscription that feeds the UDP sender — no second path, no
polling.

**Files:**
- Create: `src/loxmatter/api/live.py`
- Modify: `src/loxmatter/loxone/runtime.py` (observer)
- Create: `tests/api/test_live.py`

**Interfaces:**
- Produces:
  - `Runtime.add_observer(callback)` / `remove_observer(callback)`
  - `build_live_router(runtime) -> APIRouter` with `/api/live`

- [ ] **Step 1: Write the failing test**

`tests/api/test_live.py`:

```python
import asyncio

import pytest

from loxmatter.loxone.runtime import Runtime


class RecordingSender:
    async def send(self, key, value, *, force=False) -> bool:
        return True

    async def close(self) -> None: ...


async def test_observer_sees_every_value_the_sender_sees(tmp_path, plug_store):
    """Spec 8.3: one path, not two."""
    store, device_id = plug_store
    seen: list[tuple[str, object]] = []
    runtime = Runtime(store, RecordingSender())
    runtime.add_observer(lambda key, value: seen.append((key, value)))
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert seen == [(f"d{device_id}_2_voltage", pytest.approx(230.0))]


async def test_a_failing_observer_does_not_stop_the_udp_sender(tmp_path, plug_store):
    """The UI must not be allowed to take the bridge down with it."""
    store, device_id = plug_store
    sent: list[str] = []

    class Sender:
        async def send(self, key, value, *, force=False) -> bool:
            sent.append(key)
            return True

        async def close(self) -> None: ...

    runtime = Runtime(store, Sender())

    def boom(key: str, value: object) -> None:
        raise RuntimeError("Beobachter kaputt")

    runtime.add_observer(boom)
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert sent == [f"d{device_id}_2_voltage"]


async def test_removed_observer_stops_receiving(tmp_path, plug_store):
    store, device_id = plug_store
    seen: list[str] = []
    runtime = Runtime(store, RecordingSender())
    observer = lambda key, value: seen.append(key)  # noqa: E731
    runtime.add_observer(observer)
    runtime.remove_observer(observer)
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert seen == []


async def test_websocket_delivers_a_value(api_with_runtime):
    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/live") as ws:
        await runtime.on_attribute(device_id, "2/144/4", 230000)
        message = await asyncio.wait_for(ws.receive_json(), timeout=2)
    assert message["key"] == f"d{device_id}_2_voltage"
    assert message["value"] == pytest.approx(230.0)


async def test_a_disconnecting_client_is_dropped_without_noise(api_with_runtime):
    """A closed browser tab must not write an error to the log."""
    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/live"):
        pass
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert runtime.observer_count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_live.py -v`
Expected: FAIL with `AttributeError: 'Runtime' object has no attribute 'add_observer'`

- [ ] **Step 3: Observer in the runtime**

Add to `Runtime`. Two rules that must be in the docstring:

- The observer is called **after** sending. The bridge to Loxone is the
  purpose; the UI watches.
- An observer that throws is logged and skipped. It must not be allowed to take
  down the UDP path with it — the same rule that hardened the heartbeat loop in
  Phase 4.

- [ ] **Step 4: WebSocket router**

Every connection registers itself as an observer and unregisters again on
disconnect. A `WebSocketDisconnect` is the normal case, not an error — it must not
write anything to the log.

The queue per connection is **bounded** (`QUEUE_MAXSIZE = 512`, review fix
Important #1, 2026-09-02) — not unbounded, as an earlier version assumed. This
bridge runs unattended for weeks in someone's home; a browser tab in the
background or a laptop that has gone to sleep and no longer reads is everyday life
there, not an edge case. The limit is chosen so that it absorbs a full resend
burst (`/resync`, Spec 6.4 — even a single device like the test-suite plug comes
to ~110 datagrams) without complaint, with clear headroom above that. On
overflow, the **oldest** entry is dropped, not the newest — a live view wants the
most current state. A debug log fires on the transition into dropping (not on
every further drop), so a stuck connection stays discoverable during operation.
Deliberately NOT implemented: actively closing the connection if it stays full
for good — the bound already caps the one real danger (unbounded growth) to a
fixed, small size; an additional time threshold would need its own
hard-to-justify calibration and would risk throwing out a session that was only
briefly throttled, for a gain that is small given the already-capped memory.

To robustly handle a client that is stuck mid-send while disconnecting
(review fix Important #2, 2026-09-02): `_send_loop` catches not only
`WebSocketDisconnect`, but also `RuntimeError` directly at the send point —
some ASGI servers raise exactly that instead of `WebSocketDisconnect` on a send
attempt to an already-lost connection. Both are the same case (a browser tab that
is gone, not a program error) and therefore land on `logger.debug`, never on
`logger.error` — and the route unregisters the observer in the `finally` block
regardless.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/api/test_live.py -v`
Expected: PASS, 9 tests (5 from the original Task 3, plus 4 from the review fix of
2026-09-02: queue overflow drops the oldest entry and leaves the UDP path and
observer registration untouched, a `RuntimeError` on send is treated like a
disconnect without an error log, and two simultaneous connections stay
isolated from each other)

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/api/live.py src/loxmatter/loxone/runtime.py tests/api/test_live.py
git commit -m "feat(api): WebSocket fuer Live-Werte aus derselben Subscription"
```

---

### Task 4: Control and Raw Attribute Writing

The heart of the diagnostic capability from Spec 8.1.

**Files:**
- Create: `src/loxmatter/api/control.py`
- Create: `tests/api/test_control.py`

**Interfaces:**
- Consumes: `Store.resolve_command`, `commands.translate.to_matter_call`, the `invoke` callback from Phase 4
- Produces: `build_control_router(store, invoke) -> APIRouter`

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/devices/{device_id}/controls` | which controls this device has |
| POST | `/api/commands/{key}` | execute a command, value in the body |
| POST | `/api/signals/{key}/write` | set an attribute raw |

- [ ] **Step 1: Write the failing test**

`tests/api/test_control.py`:

```python
import pytest


async def test_plug_offers_exactly_its_three_commands(api):
    """Spec 6.7: output commands come from AcceptedCommandList, not from attributes."""
    client, _, device_id = api
    controls = (await client.get(f"/api/devices/{device_id}/controls")).json()
    assert sorted(c["slug"] for c in controls) == ["off", "on", "toggle"]


async def test_button_offers_no_controls(api_button):
    """A button is an input device."""
    client, _, device_id = api_button
    assert (await client.get(f"/api/devices/{device_id}/controls")).json() == []


async def test_executing_a_command_reaches_matter(api):
    client, _, device_id = api
    response = await client.post(f"/api/commands/d{device_id}_1_on", json={"value": "1"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_the_same_translation_as_the_loxone_endpoint(api, invocations):
    """Spec 4.2: one conversion, two callers - otherwise they drift."""
    client, _, device_id = api
    key = f"d{device_id}_1_on"
    await client.post(f"/api/commands/{key}", json={"value": "1"})
    await client.get(f"/cmd/{key}/1")
    assert len(invocations) == 2
    assert invocations[0] == invocations[1]


async def test_unknown_command_yields_404(api):
    client, _, _ = api
    response = await client.post("/api/commands/d1_1_gibtsnicht", json={"value": "1"})
    assert response.status_code == 404


async def test_a_device_that_does_not_answer_yields_502(api_failing_invoke):
    client, _, device_id = api_failing_invoke
    response = await client.post(f"/api/commands/d{device_id}_1_on", json={"value": "1"})
    assert response.status_code == 502
    assert "Traceback" not in response.text


async def test_raw_write_of_a_non_writable_attribute_is_refused(api):
    """A clear refusal is better than a write attempt that silently does nothing."""
    client, store, device_id = api
    key = next(s.key for s in store.signals(device_id) if s.ref.cluster_id == 40)
    response = await client.post(f"/api/signals/{key}/write", json={"value": "42"})
    assert response.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_control.py -v`
Expected: FAIL with `404` on `/api/devices/{id}/controls` — the route does not exist

- [ ] **Step 3: Write minimal implementation**

`src/loxmatter/api/control.py`. The docstring records why this module exists:

```python
"""Control of a device from the UI.

This is not a convenience feature (Spec 8.1). If a lamp doesn't switch via
Loxone, a click here separates the two possible causes: if the device
reacts, the fault is in the Loxone wiring or the export; if it doesn't
react, the fault is in Matter, Thread, or at the device.

The translation comes from `commands.translate` - the same one the
Loxone endpoint uses. A separate copy here would drift, and then the
diagnostics would have exactly the fault it is supposed to find (Spec 4.2).
"""
```

The status codes follow the Loxone endpoint from Phase 4: 404 unknown key,
400 mismatched value, 502 device does not respond.

For raw writing: the writability of an attribute is not in the snapshot.
**Check whether `python-matter-server` makes it accessible**, and if not, refuse
write attempts on attributes that are not on an allow list — the same
asymmetry as with the commands in Spec 6.7. Enter the finding into the spec.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_control.py -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/api/control.py tests/api/test_control.py
git commit -m "feat(api): Geraete aus der Oberflaeche bedienen"
```

**Review fix (2026-09-02), two Important and two Minor findings:**

1. **Important — `POST /api/commands/{key}` never checked whether the command's
   device is still active.** `Store.resolve_command` resolves the key solely via
   the `command` table, and `forget_device` does not delete a row there — it only
   sets `device.active = 0`. A command against an already-removed device could
   therefore still be triggered, while `GET /api/devices/{id}/controls` for the
   same device correctly reported 404. Fixed following the same pattern as
   `write_signal` (already present there) and `PATCH /api/signals/{key}`
   (`api/devices.py`, review fix Important #4 from Task 2): `StoredCommand` now
   carries `device_id`, and `execute_command` checks `store.device(stored.device_id)`
   before translating and triggering — a removed device returns 404 with a
   German message. New test:
   `test_command_at_a_removed_device_is_refused`. `GET /api/devices/{id}/controls`
   and `POST /api/signals/{key}/write` were re-checked in the process — both were
   already secured (`_require_device` and the existing check in
   `write_signal` respectively), no change needed.
2. **Important — Spec 8.4 and the module docstring wrongly claimed that a
   full-text search for “writable” had turned up no hit.** In fact,
   `chip/clusters/CHIPClusters.py` (part of the installed `chip` package) carries
   250 occurrences of `”writable”: True`, including for `BasicInformation` exactly
   the three attributes the allow list had already arrived at independently. The
   information does exist, then — it just sits in a module that is not
   importable in this distribution (`ImportError: cannot import name 'exceptions'
   from 'chip'`, because `home_assistant_chip_clusters` ships `CHIPClusters.py`
   without the accompanying `chip/exceptions.py`) and that python-matter-server
   uses nowhere. The practical consequence (the allow list stays correct) does not
   change as a result, but the justification was corrected in Spec 8.4 and in
   the module docstring. Spec 12 gets a new point 7 for this: the hand-maintained
   allow list does not scale beyond a handful of devices and could be replaced
   once this module becomes importable or parsing it as data proves justified.
3. **Minor — the 400/501 responses of `POST /api/signals/{key}/write` referred to
   “the module docstring of api/control.py”**, useful in a log, but meaningless
   for the UI. Both messages now say themselves, in German, what's going on and
   what can be done, without pointing to a file.
4. **Minor — `GET /api/devices/{id}/controls` did not show filtered raw commands
   at all**, which is correct (Spec 6.7), but a person diagnosing an unfamiliar
   device lost the information that they even exist in the process. The route
   now returns `{“commands”: [...], “hidden_raw_commands”: N}` instead of a bare
   list (new model `ControlsOut`). New test:
   `test_hidden_raw_commands_are_counted`.

Two new tests (`test_command_at_a_removed_device_is_refused`,
`test_hidden_raw_commands_are_counted`) added to the original seven — **Expected:
PASS, 9 tests** in `tests/api/test_control.py`.

```bash
git add src tests docs
git commit -m "fix(api): Kommandos an entfernte Geraete abweisen"
```

---

### Task 5: Export via the API

**Files:**
- Create: `src/loxmatter/api/export.py`
- Create: `tests/api/test_export_api.py`

**Interfaces:**
- Consumes: `export.documents`, `export.signals`, `Store`
- Produces: `build_export_router(store) -> APIRouter`

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/export/preview` | what would result: files, objects, commands, skipped items |
| GET | `/api/export/download` | ZIP with all templates and the quick-start guide |
| GET | `/api/export/status` | per device: when last exported, changed since then |

- [ ] **Step 1: Write the failing test**

`tests/api/test_export_api.py`:

```python
import io
import zipfile


async def test_preview_reports_what_would_be_written(api):
    client, _, device_id = api
    preview = (await client.get("/api/export/preview?bridge_ip=192.168.1.50")).json()
    device = next(d for d in preview["devices"] if d["device_id"] == device_id)
    assert device["inputs"] == 110
    assert device["commands"] == 3
    assert device["skipped"] == 50


async def test_preview_does_not_write_anything(api, tmp_path):
    """Preview means preview."""
    client, _, _ = api
    before = set(tmp_path.iterdir())
    await client.get("/api/export/preview?bridge_ip=192.168.1.50")
    assert set(tmp_path.iterdir()) == before


async def test_download_returns_a_zip_with_both_templates(api):
    client, _, device_id = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert any(n.startswith(f"VIU_d{device_id}_") for n in names)
    assert any(n.startswith(f"VO_d{device_id}_") for n in names)


async def test_zip_contains_the_system_templates_and_a_readme(api):
    client, _, _ = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50&system=true")
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert "VIU_Matter_System.xml" in names
    assert "VO_Matter_System.xml" in names
    assert any(n.lower().endswith(".md") or n.lower().endswith(".txt") for n in names)


async def test_files_in_the_zip_keep_bom_and_crlf(api):
    """Spec 6.1: the format is measured, not negotiable - even in the archive."""
    client, _, _ = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    name = next(n for n in archive.namelist() if n.startswith("VIU_"))
    raw = archive.read(name)
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in raw.replace(b"\r\n", b"")


async def test_status_marks_a_device_as_never_exported(api):
    client, _, device_id = api
    status = (await client.get("/api/export/status")).json()
    entry = next(s for s in status if s["device_id"] == device_id)
    assert entry["exported_at"] is None


async def test_missing_bridge_ip_yields_422(api):
    client, _, _ = api
    assert (await client.get("/api/export/preview")).status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_export_api.py -v`
Expected: FAIL — the routes do not exist

- [ ] **Step 3: Write minimal implementation**

The download builds the ZIP in memory. The quick-start guide inside it names the
target folders (`Templates\VirtualIn\` and `Templates\VirtualOut\`), the import path
in Loxone Config, and the note that the system templates are only needed once.

**Export via the API must write to the same database as `loxmatter export`.**
Otherwise the UI assigns different keys than the CLI, and a user who uses both
gets two sets of templates for the same device. A test for this belongs here
too.

**`download` marks only AFTER the complete archive, never during it**
(review fix Important #1, 2026-09-02 — see below): `Store.mark_exported` is
collected per device, but only called after the `with zipfile.ZipFile(...)` loop
has completed and the ZIP has been fully built in memory. The same discipline as
in `cli.py`'s `export` command, which likewise only executes its
`mark_exported` call after both successful `write_bytes` calls: if building one
device fails partway through the loop (a render that throws, a `store.commands`/
`store.signals` that fails, a `forget_device` from a parallel request), there is
no ZIP for the client — and then no previously processed device may be wrongly
shown as exported.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_export_api.py -v`
Expected: PASS, 14 tests (more than the 7 in the original test draft above — in
the course of the task, additional cases were added, among them the
empty-installation and the removed-device scenario)

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/api/export.py tests/api/test_export_api.py
git commit -m "feat(api): Vorlagen als Vorschau und als ZIP"
```

**Review fix (2026-09-02), one Important and three Minor findings:**

1. **Important — `download` marked a device as exported before the ZIP was
   finished.** `store.mark_exported(device.id)` used to sit IN the loop,
   directly after the two `archive.writestr` calls for this device, and
   committed immediately. If building a later device failed (a render that
   throws, a `store.commands`/`store.signals` that fails, a `forget_device`
   from a parallel request), FastAPI responded with 500 — the client got no
   ZIP at all — while every device processed up to that point still stayed
   permanently marked as exported. `GET /api/export/status` afterward wrongly
   reported such a device as "unchanged since", even though nobody ever
   received the associated template. The CLI path (`cli.py`'s `export`
   command) was already hardened against exactly this case — the API did not
   adopt the discipline. Fixed: `download` collects the device IDs during
   building in a list and calls `store.mark_exported` only AFTER the fully
   built archive, immediately before the response. New test:
   `test_a_failure_partway_through_the_archive_marks_no_device` — two devices
   in the store, the second deliberately makes `to_inputs` fail; the first
   device is already fully written into the archive by that point. After the
   fix, neither of the two stays marked regardless.
2. **Minor — no test for the migration path v1 → v2.** The existing
   test suite (`tests/model/test_store_migration.py`) only covered an old
   database at version 0 and a fresh one at the respective latest
   version; no database at exactly version 1 (`signal.exported`
   present, `device.exported_at`/`updated_at` not yet) was ever
   opened. This phase had already once shipped a schema change without
   a migration (see the schema migration note in Task 2) — the
   intermediate step therefore deserves a direct test instead of just an
   assumption derived from the loop-based `_migrate` logic. New
   tests in `tests/model/test_store_migration.py`:
   `test_opening_a_v1_database_only_runs_the_v2_migration` and
   `test_reopening_an_already_v2_database_is_a_noop`, with a new
   `build_v1_database` helper function following the same pattern as
   `build_old_database`.
3. **Minor — duplicated migration safeguard.** `_migrate_to_v1` and
   `_migrate_to_v2` both read `PRAGMA table_info`, checked for a
   missing column, and conditionally ran an `ALTER TABLE` — written out
   by hand twice instead of shared once, and given a second schema
   change in one phase, a third is likely. Extracted
   into `_add_column_if_missing(db, table, column, ddl) -> bool` in
   `model/store.py`; the return value (was the column newly created?)
   still lets `_migrate_to_v1` run its backfill only against a genuine
   old database.
4. **Minor — `preview` requires a parameter it never uses.**
   `bridge_ip` is mandatory on `/api/export/preview`, but appears in no
   field of the response — justifiable (the same 422 test as with
   `download`, the same signature as with `download`), but a future caller
   would wonder why. The docstring of `preview` (becomes the
   OpenAPI description) now says so explicitly in an additional sentence:
   `bridge_ip` deliberately appears in no field of
   `ExportPreviewOut`, even though it is a required parameter.

Three new tests in total: one in `tests/api/test_export_api.py`
(`test_a_failure_partway_through_the_archive_marks_no_device`) and two in
`tests/model/test_store_migration.py`
(`test_opening_a_v1_database_only_runs_the_v2_migration`,
`test_reopening_an_already_v2_database_is_a_noop`) — **Expected: PASS, 398
tests** in the entire suite (395 before this review fix plus the three
new ones).

```bash
git add src tests docs
git commit -m "fix(api): Export erst nach fertigem Archiv vermerken"
```

---

### Task 6: Diagnostics

Spec 10.5. These four things are the reason a bug report from someone else's
installation becomes answerable.

**Files:**
- Create: `src/loxmatter/api/diagnostics.py`
- Modify: `src/loxmatter/loxone/sender.py` (capture)
- Modify: `src/loxmatter/loxone/server.py` (command log)
- Modify: `src/loxmatter/cli.py` (`--matter-data-dir` option, basis for the backup)
- Create: `tests/api/test_diagnostics.py`

**Interfaces:**
- Produces:
  - `class RingBuffer` — fixed size, oldest fall out
  - `build_diagnostics_router(...) -> APIRouter`

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/diagnostics/datagrams` | the last N sent, filterable per device |
| GET | `/api/diagnostics/commands` | incoming HTTP calls with result |
| GET | `/api/diagnostics/system` | system check, every line green or red |
| GET | `/api/diagnostics/fabric-backup` | backup of the fabric credentials as a download |

**The backup is not a side point.** Spec 4.1 names the volume holding the
fabric credentials the only irreplaceable state of the entire system: if it is
lost, every device must be commissioned anew — and for Thread devices that means
resetting, throwing out of the old network, re-pairing. Spec 8 therefore
explicitly lists the backup in the system view.

The endpoint delivers the contents of the matter-server data directory as an
archive. **This file is key material**, not a log: it allows taking over the
fabric. The download therefore belongs behind the token from Task 8, and the
UI must write next to it what is being downloaded there — not just show a
button labeled "Backup".

**The compose wiring stays commented out until Task 8 (review fix Critical,
2026-09-02).** `--matter-data-dir` in `cli.py` is harmless — an option that is
off by default and only feeds the route with real data once someone explicitly
sets it. The volume mount `./data:/matter-data:ro` in
`deploy/testhost/docker-compose.yml`, which would do exactly that, would be the
opposite: this service runs there with `network_mode: host`, deliberately, so
that the Miniserver can reach it — and that means anyone on the same network
reaches it too. Without the token protection from Task 8, this one line in the
compose file turns a theoretical weakness into an actually exploitable one: `GET
/api/diagnostics/fabric-backup` is completely unprotected until then (see its
docstring). This task therefore delivers only the code path and the CLI option; the
volume line and `--matter-data-dir` in `command:` stay commented out in
`deploy/testhost/docker-compose.yml`, with a reference to Task 8, until
its token protection is in place. Task 8 re-enables both lines — see its
step 3.

- [ ] **Step 1: Write the failing test**

`tests/api/test_diagnostics.py`:

```python
import pytest

from loxmatter.api.diagnostics import RingBuffer


def test_ring_buffer_drops_the_oldest():
    buffer = RingBuffer(maxlen=3)
    for i in range(5):
        buffer.append(i)
    assert list(buffer) == [2, 3, 4]


def test_ring_buffer_of_a_long_running_bridge_stays_bounded():
    """A bridge runs for months - the capture must not grow along with it."""
    buffer = RingBuffer(maxlen=100)
    for i in range(1_000_000):
        buffer.append(i)
    assert len(list(buffer)) == 100


async def test_datagram_log_shows_what_was_sent(api_with_sender):
    client, sender, device_id = api_with_sender
    await sender.send(f"d{device_id}_2_voltage", 230.0)
    entries = (await client.get("/api/diagnostics/datagrams")).json()
    assert entries[-1]["key"] == f"d{device_id}_2_voltage"
    assert entries[-1]["value"] == "230"
    assert entries[-1]["timestamp"]


async def test_datagram_log_filters_by_device(api_with_sender):
    client, sender, device_id = api_with_sender
    await sender.send(f"d{device_id}_2_voltage", 230.0)
    await sender.send("bridge_alive", True)
    entries = (await client.get(f"/api/diagnostics/datagrams?device_id={device_id}")).json()
    assert all(e["key"].startswith(f"d{device_id}_") for e in entries)


async def test_command_log_records_the_result(api):
    client, _, device_id = api
    await client.get(f"/cmd/d{device_id}_1_on/1")
    await client.get("/cmd/d1_1_gibtsnicht/1")
    entries = (await client.get("/api/diagnostics/commands")).json()
    assert entries[-2]["status"] == 200
    assert entries[-1]["status"] == 404


async def test_system_check_reports_each_line_with_a_verdict(api):
    client, _, _ = api
    checks = (await client.get("/api/diagnostics/system")).json()
    names = {c["name"] for c in checks}
    assert {"matter-server", "store", "ipv6"} <= names
    for check in checks:
        assert check["ok"] in (True, False)
        assert check["detail"]


async def test_fabric_backup_is_a_real_archive(api):
    """Spec 4.1: the one irreplaceable piece of data in the system."""
    client, _, _ = api
    response = await client.get("/api/diagnostics/fabric-backup")
    assert response.status_code == 200
    assert response.headers["content-type"] in ("application/zip", "application/gzip")
    assert len(response.content) > 0


async def test_a_failing_check_says_what_to_do(api_without_matter):
    """A red dot without a note helps nobody."""
    client, _, _ = api_without_matter
    checks = (await client.get("/api/diagnostics/system")).json()
    failing = next(c for c in checks if not c["ok"])
    assert len(failing["detail"]) > 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_diagnostics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.api.diagnostics'`

- [ ] **Step 3: Write minimal implementation**

```python
class RingBuffer[T]:
    """Holds the last N entries, older ones fall out.

    A bridge runs for months. A capture that keeps growing eventually becomes
    the largest object in the process - and the interesting part is the last
    few minutes anyway.
    """

    def __init__(self, maxlen: int = 500) -> None:
        self._items: collections.deque[T] = collections.deque(maxlen=maxlen)

    def append(self, item: T) -> None:
        self._items.append(item)

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)
```

The capture hooks into `UdpSender`, not alongside it — otherwise it shows what
*should* be sent instead of what *was* sent. That is the distinction that
matters for diagnostics.

The system check checks at least: matter-server connected, database writable,
IPv6 present, Miniserver reachable. **Every red line carries a concrete note**
about what to do — a red dot without an explanation just relocates the puzzle.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/api/test_diagnostics.py -v`
Expected: PASS, 8 tests

**Review fix (2026-09-02), one Critical and one Important finding:** the
`fabric-backup` route initially hung unprotected off a compose mount that fed it
with real data, without the token protection from Task 8 already being in place —
see above, "The compose wiring stays commented out until Task 8". In addition,
both 503 branches of the route (`matter_data_dir is None`,
`not matter_data_dir.is_dir()`) were untested. Six new tests in
`tests/api/test_diagnostics.py`:
`test_command_log_does_not_record_diagnostics_polling`,
`test_command_log_never_carries_a_query_string`,
`test_a_check_that_raises_unexpectedly_fails_gracefully` (system-check
robustness, already added before this review fix), as well as
`test_fabric_backup_is_503_without_a_configured_directory`,
`test_fabric_backup_is_503_when_the_configured_directory_is_missing`, and
`test_fabric_backup_is_503_when_the_configured_path_is_a_file` (the two 503
branches plus the previously unconsidered case "path exists but is not a
file").

Run: `uv run pytest tests/api/test_diagnostics.py -v`
Expected: PASS, 14 tests (8 from this plan draft plus the six above)

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/api/diagnostics.py src/loxmatter/loxone tests/api/test_diagnostics.py
git commit -m "feat(api): Mitschnitt, Kommando-Log und Systemcheck"
```

---

### Task 7: The UI

**Files:**
- Create: `src/loxmatter/web/index.html`
- Create: `src/loxmatter/web/app.js`
- Create: `src/loxmatter/web/style.css`
- Create: `src/loxmatter/web/vendor/alpine.min.js`
- Modify: `src/loxmatter/loxone/server.py`
- Create: `tests/api/test_web.py`

**Interfaces:**
- Produces: the four views from Spec 8, served under `/`

- [ ] **Step 1: Bundle Alpine.js**

Download `alpine.min.js` in the current 3.x version and place it under
`src/loxmatter/web/vendor/`. **No CDN reference in the HTML** — the bridge runs in
installations without internet, and a UI that stays blank there is worthless.

Note version and provenance in a comment at the top of `index.html`.

- [ ] **Step 2: Write the failing test**

`tests/api/test_web.py`:

```python
async def test_root_serves_the_interface(api):
    client, _, _ = api
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_alpine_is_served_locally_not_from_a_cdn(api):
    """The bridge runs in installations without internet."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert "cdn." not in page
    assert "unpkg" not in page
    assert (await client.get("/static/vendor/alpine.min.js")).status_code == 200


async def test_the_page_names_all_four_views(api):
    client, _, _ = api
    page = (await client.get("/")).text
    for view in ("Geräte", "Signale", "Export", "System"):
        assert view in page


async def test_the_page_does_not_promise_what_the_spec_excludes(api):
    """Spec 8.2: a commissioning and diagnostics tool, not a smart-home UI."""
    client, _, _ = api
    page = (await client.get("/")).text.lower()
    for absent in ("szene", "zeitplan", "automatisierung", "favorit"):
        assert absent not in page


async def test_static_files_do_not_escape_their_directory(api):
    client, _, _ = api
    response = await client.get("/static/../../../etc/passwd")
    assert response.status_code in (404, 400)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: FAIL with `404` on `/`

- [ ] **Step 4: Build the four views**

Serving it in `server.py`:

```python
_WEB_DIR = Path(__file__).parents[1] / "web"

app.mount("/static", StaticFiles(directory=_WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serves the UI. No build step, no CDN dependency."""
    return FileResponse(_WEB_DIR / "index.html")
```

`index.html` carries all four views, switched via Alpine without a page change.

**Devices view.** List with online dot, name, signal count. Per device, the controls
from `/api/devices/{id}/controls` and the most important live values. An input
field for the pairing code with a note next to it: if the device already sits in
Apple, Google, or a DIRIGERA, the printed code doesn't work — generate a
multi-admin code there (Spec 7.1). That is the most common stumbling block and
belongs in the UI, not in a manual nobody reads.

**Signals view.** The complete tree per device with live value. The **key is
displayed but not editable** — with a short note why: it is the wiring in
Loxone. Values that cannot be exported get the reason displayed instead of the
checkbox (Spec 6.6).

**Export view.** Enter the IP **of this bridge** (from the Miniserver's point of
view, i.e. the value that becomes the `Address` of the virtual UDP input) and the
ports, view the preview, download the ZIP. Per device, visible when it was last
exported.

> Until 2026-09-03 this line said "Miniserver IP", and the UI adopted the
> label; corrected in fix 1 of the follow-up (see Spec 8, view 3).

**System view.** The system check as a list of green and red lines, below it the
UDP capture and the command log.

The live values come over the WebSocket from Task 3. If it drops, the UI shows
that and reconnects — a UI that displays frozen values as current is worse than
one that says it has lost the connection.

**Review fix, 2026-09-02 — four findings, two of them with test consequences:**

**Important #1 — the WebSocket safeguard had no regression test.** While
building this task, it turned out that `uvicorn` alone (without the
`"standard"` extra) does not bring a WebSocket implementation — a real
`uvicorn` process answered `GET /api/live` with `404 Unsupported upgrade
request`, while the entire test suite stayed green, because
`tests/api/test_live.py` runs exclusively over the in-process ASGI path
(`_InProcessWebSocket` in `tests/api/conftest.py`) and therefore never
traverses uvicorn's own HTTP/WebSocket switch. `websockets>=12` was therefore
added as its own line to `pyproject.toml` (with a justification comment
there) — since then the only safeguard against this, and one that a later
dependency update, a cleanup ("nobody imports `websockets` directly anyway"),
or a switch to bare `uvicorn` could silently tear down again without `uv run
pytest` noticing. New: `tests/api/test_live_smoke.py` starts a REAL
`uvicorn.Server` on `127.0.0.1`, port `0` (never collides, never leaves the
machine), for this, and performs a real WebSocket handshake per RFC 6455
against `/api/live` over it — over a raw TCP socket, deliberately without
using a WebSocket client library (otherwise a `websockets` removed from the
environment would already make the test client fail with an `ImportError`,
not the server actually under test). Marked as `@pytest.mark.slow`
(registered in `pyproject.toml`), but without a default exclusion — it runs
along, but costs under a second.

**Important #2 — a dropped connection spoke English.** `requestJson` in
`app.js` only generated a German fallback text if the server responded at
all. If `fetch()` itself throws (connection refused, bridge process down,
network unreachable), the raw browser text ("Failed to fetch") passed
through unchanged into the UI — English and browser jargon, in the one tool
whose purpose is to honestly show a failure (Spec 8.1). `requestJson` now
catches this case in its own `try`/`catch` around the `fetch()` call and
instead throws a German text that says the bridge is unreachable and may not
be running.

**Minor #3 — the block list only looked at the delivery, not the JavaScript.**
`test_the_page_does_not_promise_what_the_spec_excludes` (Spec 8.2) only
searched the delivered `index.html` for "szene", "zeitplan", "automatisierung",
"favorit". The four words are absent from `app.js` today too, so it wasn't a
false green — but a future feature whose German text originates only in
JavaScript would have slipped past it. The test now also loads
`/static/app.js` and checks the same block list against it.

**Minor #4 — a first connection that never succeeded looked like "still
connecting".** If the very first WebSocket handshake failed permanently,
`socketEverConnected` stayed at `false` — neither the red banner nor the
sharper header text (both tied to `socketEverConnected`) ever appeared, the
header stayed indefinitely at the neutral "Connecting…" wording, while
retries kept happening silently in the background. No data risk (there are
no live values yet that could look stale), but a weaker diagnostic signal
than the lost-connection case. `app.js` now counts failed attempts of the
very first connection (`initialConnectFailures`, capped at
`INITIAL_CONNECT_FAILURES_BEFORE_GIVING_UP_ON_SILENCE = 3`), and
`connectionStatusText()` (the header logic, now its own function instead of
a nested condition in `index.html`) then switches to a clear text that says
no connection came about.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/api/test_web.py tests/api/test_live_smoke.py -v`
Expected: PASS, 6 tests (5 from `test_web.py`, unchanged in count since the
original Task 7 — review fix Minor #3 extended an existing assertion, did not
add a new test — plus 1 new test in `test_live_smoke.py` from the review fix
of 2026-09-02, Important #1)

- [ ] **Step 6: Look at it by hand**

```bash
uv run loxmatter run --url ws://<matter-server>:5580/ws --miniserver 127.0.0.1 --port 7000
```

Then open `http://localhost:8080` and go through all four views. Whatever
stands out here belongs in the report — a UI cannot be judged by tests
alone.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/web src/loxmatter/loxone/server.py tests/api/test_web.py
git commit -m "feat(web): vier Ansichten ohne Build-Schritt"
```

---

### Task 8: Assembly, Hardening, End-to-End Test

**Files:**
- Modify: `src/loxmatter/loxone/server.py`, `src/loxmatter/cli.py`
- Modify: `deploy/testhost/docker-compose.yml`
- Modify: `README.md`
- Create: `tests/api/test_security.py`

- [ ] **Step 1: The hardening this phase makes necessary**

Up to Phase 4, the service offered `/cmd` and `/resync`. From now on it
commissions devices and removes them — **whoever reaches the port can throw a
device out of the fabric.**

That is not a theoretical point: `run` binds on `0.0.0.0` with no option, the
prior default.

Build at least:

- a `--host` option with default `0.0.0.0` (the Miniserver must be able to reach
  the service),
- an optional token via `--api-token` or `LOXMATTER_API_TOKEN` that protects
  **only** the `/api` routes, not `/cmd` and `/resync` — the Miniserver cannot
  send a header along,
- and a clear warning in the log at startup if no token is set.

```python
def build_api_guard(token: str | None) -> Callable[..., None]:
    """Protects the /api routes, not the Miniserver's.

    The Miniserver calls virtual outputs without a header - it cannot send a
    token along. /cmd and /resync must therefore stay open, and that is a
    deliberate boundary, not carelessness: whoever reaches the port can still
    switch devices. What the token prevents is commissioning, removal, and
    the download of the fabric backup - i.e. everything that changes the
    inventory.
    """

    async def guard(authorization: str | None = Header(default=None)) -> None:
        if token is None:
            return
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="Ungültiges oder fehlendes Token")

    return guard
```

At startup without a token:

```python
    if api_token is None:
        logger.warning(
            "Kein API-Token gesetzt — die Oberfläche ist für jeden erreichbar, "
            "der den Port erreicht, einschließlich Einlernen und Entfernen von "
            "Geräten. Setze LOXMATTER_API_TOKEN oder --api-token."
        )
```

Tests: without a token, the `/api` routes are open and the warning appears; with
a token they respond with 401 without a header, but `/cmd` and `/resync` stay
unchanged; and the fabric backup is reachable only with a header even with a
token set.

Enter the decision into Spec 9 — together with the justification for why the
Loxone path must stay unprotected.

- [ ] **Step 2: Connect everything**

`build_app` wires in the five routers and serves the UI. `run` passes the
Matter client through so commissioning works.

- [ ] **Step 3: Compose and README**

The `loxmatter` service in `deploy/testhost/docker-compose.yml` now publishes a
port carrying a control UI. Note that there and in the README, together with the
note about the token.

**Re-enable the fabric backup mount (review fix Critical, 2026-09-02).** Task 6
deliberately commented out the volume line `./data:/matter-data:ro` and
`--matter-data-dir /matter-data` in the `command:` of the `loxmatter` service,
with a reference pointing exactly here — without a token, `GET
/api/diagnostics/fabric-backup` would otherwise have been reachable by anyone on
the same network (see Task 6, "The compose wiring stays commented out until
Task 8"). Now that `build_api_guard` is in place: re-enable both lines, rewrite
the explanatory comment there to "active since Task 8" instead of deleting it
outright (the justification for why it was dangerous before stays valuable for
the next reader), and afterward confirm by hand that
`/api/diagnostics/fabric-backup` responds with 401 without an `Authorization`
header.

- [ ] **Step 4: Full check**

```bash
uv run pytest -v && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [ ] **Step 5: End-to-end test on real hardware**

**This step needs a human.** It is the purpose of the phase.

With matter-server running:

1. **Commission** a device through the UI — enter the pairing code, the device
   appears.

   **Expected, not a bug: the freshly commissioned device shows "online" and
   green, but a dash for every signal** (Spec 12.3, added 2026-09-03).
   `BridgeMatterClient.subscribe()` runs exactly once when the bridge starts and
   registers only the (node, path) pairs known at that time; the online status,
   by contrast, comes from the NODE_ADDED event and is there immediately. For
   step 2, therefore, **restart the bridge once** — after that, `subscribe()`
   knows the new node and the values start coming in. The success message in the
   UI says the same thing. This is a known open point, not a deviation that
   would need to be entered into the spec.
2. See the live values in the signals view and change a title; check that the
   key stays unchanged.
3. **Switch** the device through the UI and observe the reaction at the device.
4. Download templates as a ZIP and check the contents.
5. Provoke an error in the system check (stop matter-server) and see whether the
   red line gives the right note.
6. **Remove** a device again.

Whatever deviates goes into the spec — **not** into an adjustment of the tests.

- [ ] **Step 6: Commit**

```bash
git add src tests deploy README.md docs
git commit -m "feat(web): Oberflaeche verbunden und abgesichert"
```

---

## Completion of the phase

The phase is done when:

1. `uv run pytest` passes without hardware and without network,
2. the six points from Task 8 Step 5 are confirmed on real hardware,
3. the hardening decision is in Spec 9,
4. deviations are in the spec.

**Not part of this phase:** everything from Spec 8.2 — scenes, schedules, automation,
rooms, user management. And color control stays as unvalidated as in Phase 4,
as long as no Matter light is available; the UI shows the sliders, but
nobody has seen whether the color is right.
