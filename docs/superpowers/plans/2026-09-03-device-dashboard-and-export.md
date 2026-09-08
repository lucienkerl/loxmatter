# Device Dashboard: always-open cards, export per device — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Device tiles in the dashboard show values and controls always open (no more click needed), carry a status color stripe/icon, and can be exported individually; a new settings tab manages the bridge connection data server-side, the previous export tab now only shows it read-only.

**Architecture:** Backend (FastAPI/SQLite, `src/loxmatter/`) gets a new, small `BridgeSettingsStore` class (reads/writes the already existing generic `setting` table, analogous to `AuthStore`), a new `/api/settings` router, and an optional `device_id` parameter on `GET /api/export/download`. Frontend (Alpine.js, no build step, `src/loxmatter/web/`) gets new CSS tokens/classes, a fifth tab, and a reworked device tile — all in the existing style (`.card`, `.row`, `.hint` …), no new dependency.

**Tech Stack:** Python 3.12, FastAPI, SQLite (`sqlite3`), Pydantic v2, pytest/httpx2 (backend); Alpine.js 3.17 (vendored), plain CSS, no bundler (frontend).

## Global Constraints

- Reference spec: `docs/superpowers/specs/2026-09-03-device-dashboard-and-export-design.md` — every deviation below is explicitly named.
- Accent color (copper/amber, approved by the client): `#a15a2c` light / `#e2915c` dark, contrast color `#ffffff` light / `#2a1508` dark. Status colors (`--ok` green, `--warn` amber, new `--off` gray) remain independent of it.
- German in every piece of text that ends up on the screen or in an error message; English in all identifiers (variables, functions, endpoint fields) — existing convention, see the `app.js` header comment.
- No `console.log`, no new external script/CDN in `index.html` — the UI runs offline (see the `index.html` header comment on Alpine.js).
- **Deviation from spec section 3 (deliberate, see section 9.1 of the spec, which leaves this open):** no icon per device type (plug/motion/blinds) — that would need a mapping table established against the Matter Device Library, which is not already verified anywhere in the code or specs of this project. Instead, ONE generic device icon for every card; status icons (warning triangle, offline) are unaffected by this, they only hang off already existing fields (`online`, `changed_since_export`), no Matter type detection needed.
- No frontend test infrastructure in this repo (no `tests/web/`, no JS test runner) — frontend tasks below are verified manually in the browser via a new helper server (task 4), backend tasks via `pytest`.

---

## Task 1: `BridgeSettingsStore` — storing the bridge settings

**Files:**
- Modify: `src/loxmatter/model/store.py:61` (new constant), `src/loxmatter/model/store.py:696-705` (import + wiring in `Store.__init__`)
- Create: `src/loxmatter/model/settings_store.py`
- Test: `tests/model/test_settings_store.py`

**Interfaces:**
- Produces: `loxmatter.model.store.DEFAULT_LISTEN_PORT: int` (= 8080). `loxmatter.model.settings_store.BridgeSettings` (frozen dataclass: `bridge_ip: str | None`, `udp_port: int`, `listen_port: int`, `saved_at: str | None`). `loxmatter.model.settings_store.BridgeSettingsStore(db: sqlite3.Connection)` with `.get() -> BridgeSettings` and `.save(*, bridge_ip: str, udp_port: int, listen_port: int) -> BridgeSettings`. `Store.settings: BridgeSettingsStore` (attribute, like `Store.auth`).
- Consumes: nothing new — uses the already existing table `setting` (`store.py:128-131`, present on every database since schema version 5, no new migration needed).

- [ ] **Step 1: Write the failing tests**

Create `tests/model/test_settings_store.py`:

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

"""Tests for `BridgeSettingsStore` - the part of the store that manages
the connection data to the bridge (IP, ports), analogous to `AuthStore`.

See docs/superpowers/specs/2026-09-03-device-dashboard-and-export-design.md,
section 4."""

from __future__ import annotations

from loxmatter.model.store import DEFAULT_LISTEN_PORT, DEFAULT_UDP_PORT, Store


def test_a_fresh_store_has_no_bridge_ip_but_default_ports(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        settings = store.settings.get()
        assert settings.bridge_ip is None
        assert settings.udp_port == DEFAULT_UDP_PORT
        assert settings.listen_port == DEFAULT_LISTEN_PORT
        assert settings.saved_at is None
    finally:
        store.close()


def test_save_persists_all_three_values(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        saved = store.settings.save(bridge_ip="192.168.1.20", udp_port=7001, listen_port=8081)
        assert saved.bridge_ip == "192.168.1.20"
        assert saved.udp_port == 7001
        assert saved.listen_port == 8081
        assert saved.saved_at is not None

        reloaded = store.settings.get()
        assert reloaded.bridge_ip == "192.168.1.20"
        assert reloaded.udp_port == 7001
        assert reloaded.listen_port == 8081
        assert reloaded.saved_at == saved.saved_at
    finally:
        store.close()


def test_save_overwrites_a_previous_value(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.settings.save(bridge_ip="10.0.0.1", udp_port=7000, listen_port=8080)
        store.settings.save(bridge_ip="10.0.0.2", udp_port=7002, listen_port=8082)
        settings = store.settings.get()
        assert settings.bridge_ip == "10.0.0.2"
        assert settings.udp_port == 7002
        assert settings.listen_port == 8082
    finally:
        store.close()


def test_settings_survive_a_reopened_connection(tmp_path):
    """Server-side instead of localStorage (design section 4): the point
    is precisely that it survives a process restart."""
    path = tmp_path / "t.sqlite"
    store = Store(path)
    try:
        store.settings.save(bridge_ip="192.168.1.20", udp_port=7000, listen_port=8080)
    finally:
        store.close()

    reopened = Store(path)
    try:
        assert reopened.settings.get().bridge_ip == "192.168.1.20"
    finally:
        reopened.close()
```

- [ ] **Step 2: Confirm the tests fail**

Run: `uv run pytest tests/model/test_settings_store.py -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'settings'` (and `ImportError` for `DEFAULT_LISTEN_PORT`, if collection already fails there)

- [ ] **Step 3: Create `settings_store.py`**

Create `src/loxmatter/model/settings_store.py`:

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

"""Access to this bridge's connection data - IP and ports, as they are
already entered today in the export tab (`api/export.py`).

Its own module and its own class, analogous to `auth_store.py`: the
`setting` table is laid out generically (key/value), precisely so that
further configuration like this one can go the same way (see the module
docstring there, spec 14.2 of the login design). This class is another
view onto the same table and the same connection, not a second
connection setup.

See docs/superpowers/specs/2026-09-03-device-dashboard-and-export-design.md,
section 4: server-side instead of `localStorage`, because the bridge
address is a property of the installation, not of the browser."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from loxmatter.model.store import DEFAULT_LISTEN_PORT, DEFAULT_UDP_PORT
from loxmatter.timestamps import now_iso

_BRIDGE_IP_KEY = "bridge_ip"
_BRIDGE_UDP_PORT_KEY = "bridge_udp_port"
_BRIDGE_LISTEN_PORT_KEY = "bridge_listen_port"
_BRIDGE_SETTINGS_SAVED_AT_KEY = "bridge_settings_saved_at"

_ALL_KEYS = (
    _BRIDGE_IP_KEY,
    _BRIDGE_UDP_PORT_KEY,
    _BRIDGE_LISTEN_PORT_KEY,
    _BRIDGE_SETTINGS_SAVED_AT_KEY,
)


@dataclass(frozen=True)
class BridgeSettings:
    """`bridge_ip`/`saved_at` are `None` as long as nobody has saved
    anything - in that case the ports fall back to the same defaults as
    the previous export tab (`DEFAULT_UDP_PORT`/`DEFAULT_LISTEN_PORT`)."""

    bridge_ip: str | None
    udp_port: int
    listen_port: int
    saved_at: str | None


class BridgeSettingsStore:
    """Access to `setting` via the store's connection - like `AuthStore`,
    just for different keys."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get(self) -> BridgeSettings:
        rows = self._db.execute(
            f"SELECT key, value FROM setting WHERE key IN ({', '.join('?' for _ in _ALL_KEYS)})",
            _ALL_KEYS,
        ).fetchall()
        values = {row["key"]: row["value"] for row in rows}
        return BridgeSettings(
            bridge_ip=values.get(_BRIDGE_IP_KEY),
            udp_port=int(values[_BRIDGE_UDP_PORT_KEY])
            if _BRIDGE_UDP_PORT_KEY in values
            else DEFAULT_UDP_PORT,
            listen_port=int(values[_BRIDGE_LISTEN_PORT_KEY])
            if _BRIDGE_LISTEN_PORT_KEY in values
            else DEFAULT_LISTEN_PORT,
            saved_at=values.get(_BRIDGE_SETTINGS_SAVED_AT_KEY),
        )

    def save(self, *, bridge_ip: str, udp_port: int, listen_port: int) -> BridgeSettings:
        """Writes all three values and the timestamp in one transaction -
        no partial update: the three fields belong together logically."""
        saved_at = now_iso()
        for key, value in (
            (_BRIDGE_IP_KEY, bridge_ip),
            (_BRIDGE_UDP_PORT_KEY, str(udp_port)),
            (_BRIDGE_LISTEN_PORT_KEY, str(listen_port)),
            (_BRIDGE_SETTINGS_SAVED_AT_KEY, saved_at),
        ):
            self._db.execute(
                "INSERT INTO setting (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        self._db.commit()
        return self.get()
```

- [ ] **Step 4: Wire up `DEFAULT_LISTEN_PORT` and `Store.settings`**

In `src/loxmatter/model/store.py`, line 61 (`DEFAULT_UDP_PORT = 7000`), add directly after it:

```python
DEFAULT_UDP_PORT = 7000
# `_DEFAULT_LISTEN_PORT` moved here from `api/export.py` (device
# dashboard design, section 4): the new `BridgeSettingsStore` below
# needs the same default value, and a second, independently maintained
# literal `8080` would be exactly the kind of drift that `api/export.py`'s
# own module docstring (decision 2) already warns against.
DEFAULT_LISTEN_PORT = 8080
```

Add the import at the top of the file (after the existing `AuthStore` line, around line 51):

```python
from loxmatter.model.auth_store import AuthStore
from loxmatter.model.settings_store import BridgeSettingsStore
```

And in `Store.__init__` (line 696-705), directly after `self.auth = AuthStore(self._db)`:

```python
        self.auth = AuthStore(self._db)
        # View onto the same connection - see `settings_store.py`.
        self.settings = BridgeSettingsStore(self._db)
```

- [ ] **Step 5: Confirm the tests pass**

Run: `uv run pytest tests/model/test_settings_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Switch `export.py` to the central constant**

`src/loxmatter/api/export.py` so far defines `_DEFAULT_LISTEN_PORT = 8080` itself (line 109). Replace the import (line 106) and the constant:

```python
from loxmatter.model.store import (
    DEFAULT_LISTEN_PORT,
    DEFAULT_UDP_PORT,
    Store,
    StoredCommand,
    StoredDevice,
)
```

Remove line 109 (`_DEFAULT_LISTEN_PORT = 8080`) and replace its one use in the `download` route (query default for `listen`, currently `_DEFAULT_LISTEN_PORT`) with `DEFAULT_LISTEN_PORT`.

- [ ] **Step 7: Existing export tests keep passing**

Run: `uv run pytest tests/api/test_export_api.py -v`
Expected: PASS (no behavior change, just the same value from a different module)

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/model/store.py src/loxmatter/model/settings_store.py \
        src/loxmatter/api/export.py tests/model/test_settings_store.py
git commit -m "$(cat <<'EOF'
feat(settings): BridgeSettingsStore fuer IP/Ports der Bruecke

Neue, kleine Sicht auf die bestehende `setting`-Tabelle (analog zu
AuthStore) - Grundlage fuer den neuen Einstellungen-Tab. Hebt
DEFAULT_LISTEN_PORT nach store.py, damit api/export.py denselben
Vorgabewert verwendet statt einer zweiten Kopie.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `/api/settings` — REST endpoints

**Files:**
- Modify: `src/loxmatter/api/models.py` (append new models)
- Create: `src/loxmatter/api/settings.py`
- Modify: `src/loxmatter/loxone/server.py` (hook up the router)
- Test: `tests/api/test_settings_api.py`

**Interfaces:**
- Consumes: `Store.settings` from task 1 (`BridgeSettingsStore.get()`/`.save(...)`).
- Produces: `GET /api/settings` and `PATCH /api/settings`, both `-> BridgeSettingsOut` (JSON: `bridge_ip: str | None`, `udp_port: int`, `listen_port: int`, `saved_at: str | None`). `loxmatter.api.settings.build_settings_router(store: Store) -> APIRouter`.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_settings_api.py`:

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

"""Tests for the settings endpoint (`api/settings.py`) - see
docs/superpowers/specs/2026-09-03-device-dashboard-and-export-design.md,
section 4."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter.loxone.server import build_app
from loxmatter.model.store import DEFAULT_LISTEN_PORT, DEFAULT_UDP_PORT, Store


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[tuple[httpx.AsyncClient, Store]]:
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store
    store.close()


async def test_a_fresh_installation_has_no_bridge_ip_but_default_ports(api):
    client, _ = api
    body = (await client.get("/api/settings")).json()
    assert body["bridge_ip"] is None
    assert body["udp_port"] == DEFAULT_UDP_PORT
    assert body["listen_port"] == DEFAULT_LISTEN_PORT
    assert body["saved_at"] is None


async def test_patch_saves_and_returns_the_new_values(api):
    client, _ = api
    response = await client.patch(
        "/api/settings",
        json={"bridge_ip": "192.168.1.20", "udp_port": 7001, "listen_port": 8081},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["bridge_ip"] == "192.168.1.20"
    assert body["udp_port"] == 7001
    assert body["listen_port"] == 8081
    assert body["saved_at"] is not None


async def test_a_later_get_sees_what_patch_saved(api):
    client, _ = api
    await client.patch(
        "/api/settings",
        json={"bridge_ip": "192.168.1.20", "udp_port": 7001, "listen_port": 8081},
    )
    body = (await client.get("/api/settings")).json()
    assert body["bridge_ip"] == "192.168.1.20"


async def test_an_empty_bridge_ip_yields_422(api):
    client, _ = api
    response = await client.patch(
        "/api/settings", json={"bridge_ip": "", "udp_port": 7000, "listen_port": 8080}
    )
    assert response.status_code == 422


async def test_settings_are_stored_in_the_same_database_the_export_router_reads(api):
    """No second, independent storage (the same reasoning as
    `api/export.py`'s module docstring for the store overall)."""
    client, store = api
    await client.patch(
        "/api/settings", json={"bridge_ip": "10.0.0.5", "udp_port": 7000, "listen_port": 8080}
    )
    assert store.settings.get().bridge_ip == "10.0.0.5"


async def test_settings_route_requires_a_session(tmp_path, no_invoke, fake_runtime):
    """Like every other `/api` route since the WebUI login (spec 9) - no
    test of its own needed for the guard itself (that is already covered
    in `tests/api/test_security.py` for all five routers), only that this
    sixth router actually belongs to it."""
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/settings")
    store.close()
    assert response.status_code == 401
```

- [ ] **Step 2: Confirm the tests fail**

Run: `uv run pytest tests/api/test_settings_api.py -v`
Expected: FAIL — `404 Not Found` for `/api/settings` (router doesn't exist yet)

- [ ] **Step 3: Add the models**

In `src/loxmatter/api/models.py`, append at the end of the file:

```python
class BridgeSettingsOut(BaseModel):
    """Response of `GET`/`PATCH /api/settings` (device dashboard design,
    section 4). `bridge_ip`/`saved_at` are `None` as long as nobody has
    set up the connection to the Miniserver - the case in which the UI
    disables the export button on every device card."""

    model_config = ConfigDict(frozen=True)

    bridge_ip: str | None
    udp_port: int
    listen_port: int
    saved_at: str | None


class BridgeSettingsIn(BaseModel):
    """Body of `PATCH /api/settings` - all three fields together, no
    partial update: they belong together logically (the same virtual
    connection), a partial update could otherwise leave a valid IP with
    a now-wrong port. `min_length=1` on `bridge_ip` yields 422 on an
    empty field, without a validator of its own."""

    model_config = ConfigDict(frozen=True)

    bridge_ip: str = Field(min_length=1)
    udp_port: int
    listen_port: int
```

Add the import at the top of the file:

```python
from pydantic import BaseModel, ConfigDict, Field
```

- [ ] **Step 4: Create the router**

Create `src/loxmatter/api/settings.py`:

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

"""Connection settings of the bridge (IP, ports) via the API - device
dashboard design (2026-09-03), section 4.

`build_settings_router` builds an `APIRouter` with prefix `/api`, exactly
like `api.devices.build_device_router` - hooked into
`loxone.server.build_app` next to the other routers of this phase,
behind the same `api_guard`."""

from __future__ import annotations

from fastapi import APIRouter

from loxmatter.api.models import BridgeSettingsIn, BridgeSettingsOut
from loxmatter.model.store import Store


def _settings_out(store: Store) -> BridgeSettingsOut:
    settings = store.settings.get()
    return BridgeSettingsOut(
        bridge_ip=settings.bridge_ip,
        udp_port=settings.udp_port,
        listen_port=settings.listen_port,
        saved_at=settings.saved_at,
    )


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

    return router
```

- [ ] **Step 5: Hook it into `build_app`**

In `src/loxmatter/loxone/server.py`, add the import (next to the other `api.*` imports, around line 119):

```python
from loxmatter.api.devices import build_device_router
from loxmatter.api.settings import build_settings_router
```

And after the existing `app.include_router(build_export_router(store), dependencies=api_guard)` line (around line 415):

```python
    app.include_router(build_export_router(store), dependencies=api_guard)
    app.include_router(build_settings_router(store), dependencies=api_guard)
```

- [ ] **Step 6: Confirm the tests pass**

Run: `uv run pytest tests/api/test_settings_api.py tests/api/test_security.py -v`
Expected: PASS. (`tests/api/test_security.py` checks the guard against individually named routes like `/api/devices` or `/api/export/status`, no generic loop over all routers — the new `/api/settings` router needs no addition there; `test_settings_route_requires_a_session` above already covers it.)

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/api/models.py src/loxmatter/api/settings.py \
        src/loxmatter/loxone/server.py tests/api/test_settings_api.py
git commit -m "$(cat <<'EOF'
feat(settings): GET/PATCH /api/settings fuer die Bridge-Verbindung

Neuer sechster API-Router, hinter demselben Waechter wie die uebrigen
fuenf. Grundlage fuer den neuen Einstellungen-Tab der WebUI.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `device_id` on `/api/export/download`

**Files:**
- Modify: `src/loxmatter/api/export.py`
- Modify (extension, not replacement): `tests/api/test_export_api.py`

**Interfaces:**
- Consumes: `store.device(device_id) -> StoredDevice`, raises `UnknownDeviceError` (already present, `model/store.py`).
- Produces: `GET /api/export/download` accepts a new optional query parameter `device_id: int | None`. When set, it overrides `only_pending` (the device is always exported) and restricts the archive to exactly this one device; an unknown `device_id` yields 404.

- [ ] **Step 1: Write the failing tests**

Add to the end of `tests/api/test_export_api.py`:

```python
# ---------------------------------------------------------------------------
# device_id: export of a single device via the export button on the
# device card (device dashboard design, 2026-09-03, section 6). No
# endpoint of its own - the same `/api/export/download`, just
# restricted to one device.
# ---------------------------------------------------------------------------


async def test_download_with_device_id_contains_only_that_device(api):
    client, store, first_id = api
    second_id = _second_device(store)

    response = await client.get(f"/api/export/download?bridge_ip=192.168.1.50&device_id={first_id}")
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert any(n.startswith(f"VIU_d{first_id}_") for n in names)
    assert not any(n.startswith(f"VIU_d{second_id}_") for n in names)


async def test_download_with_device_id_marks_only_that_device_exported(api):
    client, store, first_id = api
    second_id = _second_device(store)

    await client.get(f"/api/export/download?bridge_ip=192.168.1.50&device_id={first_id}")

    assert store.device(first_id).exported_at is not None
    assert store.device(second_id).exported_at is None


async def test_download_with_device_id_ignores_only_pending(api):
    """`device_id` wins against `only_pending` (design section 6): the
    requested device is exported, even if it wouldn't be pending at all
    per `changed_since_export`."""
    client, store, first_id = api
    await client.get(f"/api/export/download?bridge_ip=192.168.1.50&device_id={first_id}")
    assert store.device(first_id).exported_at is not None  # already exported, "not pending"

    response = await client.get(
        f"/api/export/download?bridge_ip=192.168.1.50&device_id={first_id}&only_pending=true"
    )
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert any(n.startswith(f"VIU_d{first_id}_") for n in names)


async def test_download_with_unknown_device_id_yields_404(api):
    client, _, _ = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50&device_id=999999")
    assert response.status_code == 404
```

- [ ] **Step 2: Confirm the tests fail**

Run: `uv run pytest tests/api/test_export_api.py -k device_id -v`
Expected: FAIL — `device_id` is ignored as an unknown query parameter, all four tests fail (the first three, because the ZIP contains both/neither device instead of just one, or both are marked; the last one, because the response is 200 instead of 404).

- [ ] **Step 3: Extend `download` with `device_id`**

In `src/loxmatter/api/export.py`:

Add the import (line 93-94, next to the existing `fastapi` imports):

```python
from fastapi import APIRouter, HTTPException, Query
```

Add the import (line 106, next to the existing `model.store` imports):

```python
from loxmatter.model.store import (
    DEFAULT_LISTEN_PORT,
    DEFAULT_UDP_PORT,
    Store,
    StoredCommand,
    StoredDevice,
    UnknownDeviceError,
)
```

The `download` route (line 238-341) becomes:

```python
    @router.get("/download")
    async def download(
        bridge_ip: str = Query(..., description="IP der Bruecke, aus Sicht des Miniservers"),
        port: int = Query(DEFAULT_UDP_PORT, description="UDP-Port, auf dem der Miniserver lauscht"),
        listen: int = Query(
            DEFAULT_LISTEN_PORT,
            description="HTTP-Port in der erzeugten Kommando-URL (VO-Vorlage) - muss mit"
            " dem --listen von `loxmatter run` uebereinstimmen (siehe Modul-Docstring,"
            " Entscheidung 2).",
        ),
        system: bool = Query(
            False, description="Auch die geraeteunabhaengigen Systemvorlagen einschliessen."
        ),
        only_pending: bool = Query(
            False,
            description="Nur Geraete, die seit ihrem letzten Export geaendert wurden"
            " (dieselbe Bedingung wie `changed_since_export` in /status). Die uebrigen"
            " kommen weder ins Archiv noch bekommen sie ein neues `exported_at`. Wird"
            " ignoriert, wenn `device_id` gesetzt ist.",
        ),
        device_id: int | None = Query(
            None,
            description="Nur dieses eine Geraet exportieren (Geraete-Dashboard-Entwurf,"
            " Abschnitt 6, Export-Knopf an der Geraetekarte) - ignoriert `only_pending`."
            " 404, wenn das Geraet nicht (mehr) existiert.",
        ),
    ) -> Response:
        """Builds the ZIP in memory - no temporary file, no intermediate
        state on disk.

        Marks every delivered device as exported via `Store.mark_exported`
        (decision 1 in the module docstring) - but ONLY AFTER the archive
        is fully built, not device by device during the build (review fix
        important #1, 2026-09-02). If an error had occurred between two
        devices - a render that raises, a `store.commands`/`store.signals`
        that fails, a `forget_device` from a parallel request - FastAPI
        would have responded 500 and the client would have received no
        ZIP, while every device processed up to that point would
        nonetheless have been permanently recorded as exported. The same
        discipline as in `cli.py`'s `export` command.

        **`device_id` (device dashboard design, 2026-09-03, section 6).**
        When set, the selection is restricted to exactly this one device,
        independent of `only_pending` - the export button on a device
        card never asks whether the device is "pending," it exports the
        one device that is currently visible. An unknown or removed
        device yields 404, checked BEFORE the archive is built."""
        if device_id is not None:
            try:
                store.device(device_id)
            except UnknownDeviceError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

        buffer = io.BytesIO()
        exported_device_ids: list[int] = []
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            if system:
                viu_system, vo_system = render_system_templates(bridge_ip, port, listen)
                archive.writestr("VIU_Matter_System.xml", viu_system)
                archive.writestr("VO_Matter_System.xml", vo_system)

            for device in store.devices():
                if device_id is not None:
                    if device.id != device_id:
                        continue
                elif only_pending and not _changed_since_export(device):
                    continue
                signals = store.signals(device.id)
                commands = _loxone_commands(store.commands(device.id))
                inputs = to_inputs(signals, device.id, device.label)

                archive.writestr(
                    filename_for("VIU", device.id, device.label),
                    render_virtual_in_udp(device.label, bridge_ip, port, inputs),
                )
                if commands:
                    archive.writestr(
                        filename_for("VO", device.id, device.label),
                        render_virtual_out(device.label, f"http://{bridge_ip}:{listen}", commands),
                    )
                exported_device_ids.append(device.id)

            archive.writestr(_README_NAME, _README_TEXT)

        for device_id_written in exported_device_ids:
            store.mark_exported(device_id_written)

        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{ARCHIVE_NAME}"'},
        )
```

(The loop variable in the last `for` is now called `device_id_written`, no longer `device_id` — otherwise the route's `device_id` parameter would be shadowed from this line on, which reads poorly even though it makes no functional difference, since it's no longer needed at that point.)

- [ ] **Step 4: Confirm the new and old tests pass**

Run: `uv run pytest tests/api/test_export_api.py -v`
Expected: PASS (all previous ones plus the four new tests — in particular `test_an_unfiltered_download_still_contains_every_device` and the `only_pending` tests stay green unchanged, because `device_id` is never set there)

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/api/export.py tests/api/test_export_api.py
git commit -m "$(cat <<'EOF'
feat(export): einzelnes Geraet ueber device_id exportierbar

GET /api/export/download akzeptiert jetzt optional device_id - schraenkt
die Auswahl auf genau dieses Geraet ein und ignoriert dabei only_pending.
Grundlage fuer den Export-Knopf an der Geraetekarte.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: helper server for manual verification of the frontend tasks

This repo has no frontend test infrastructure (no JS test runner, `app.js`/`index.html`/`style.css` are unchanged static code with no build step). `loxmatter run` itself needs a real `matter-server` connection. For tasks 5-9 below, a small development server is therefore needed that shows the WebUI with two sample devices from the existing test fixtures, without Matter hardware.

**Files:**
- Create: `scripts/dev_web_server.py`

**Interfaces:**
- Consumes: `loxmatter.loxone.server.build_app`, `loxmatter.model.store.Store`, fixtures under `tests/fixtures/nodes/`.
- Produces: a locally reachable HTTP server at `http://127.0.0.1:8420`, which serves manual verification from task 5 onward.

- [ ] **Step 1: Create the script**

Create `scripts/dev_web_server.py`:

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

"""Starts the WebUI with two sample devices, without matter-server - for
manually viewing the device dashboard changes in the browser (see
docs/superpowers/plans/2026-09-03-device-dashboard-and-export.md, task 4).

Invocation: uv run python scripts/dev_web_server.py
Afterward: open http://127.0.0.1:8420, set any password (initial setup,
only valid for this test run).

The database sits in a fixed file in the temp directory - a second run
finds the same data again, instead of commissioning from scratch every
time."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import uvicorn

from loxmatter.commands.translate import MatterCall
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.profiles.table import Exportability

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures" / "nodes"


def _load_snapshot(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


class _SeededRuntime:
    """Fulfills `api.devices.RuntimeValues` with a few made-up but
    plausible values - enough so the device cards don't just show "-".
    Not a replacement for `Runtime`: there is no live connection, the
    values stay fixed until this process restarts."""

    def __init__(self, values: dict[str, float | bool]) -> None:
        self._values = values

    def last_values_for(self, device_id: int) -> dict[str, float | bool]:
        prefix = f"d{device_id}_"
        return {k: v for k, v in self._values.items() if k.startswith(prefix)}


async def _invoke(call: MatterCall) -> None:
    return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store-path",
        type=Path,
        default=Path(tempfile.gettempdir()) / "loxmatter-dev-web.sqlite",
        help="Datenbankdatei (Default: eine feste Datei im Temp-Verzeichnis).",
    )
    parser.add_argument("--port", type=int, default=8420)
    return parser.parse_args()


def _ensure_devices(store: Store) -> list[int]:
    if store.devices():
        return [device.id for device in store.devices()]

    plug = _load_snapshot("ikea_grillplats_plug.json")
    plug_id = store.register_device(plug)
    store.register_signals(plug_id, plug)
    store.register_commands(plug_id, extract_commands(plug), plug.node_id)
    store.rename_device(plug_id, "Steckdose Wohnzimmer")

    button = _load_snapshot("ikea_bilresa_button.json")
    button_id = store.register_device(button)
    store.register_signals(button_id, button)
    store.register_commands(button_id, extract_commands(button), button.node_id)
    store.rename_device(button_id, "Taster Flur")

    return [plug_id, button_id]


def _seed_values(store: Store, device_ids: list[int]) -> dict[str, float | bool]:
    values: dict[str, float | bool] = {}
    for device_id in device_ids:
        values[f"d{device_id}_online"] = True
        for signal in store.signals(device_id):
            if not signal.functional:
                continue
            if signal.exportability == Exportability.DIGITAL:
                values[signal.key] = True
            elif signal.exportability == Exportability.ANALOG:
                values[signal.key] = 12.4
    return values


def main() -> None:
    args = _parse_args()
    store = Store(args.store_path)
    device_ids = _ensure_devices(store)
    values = _seed_values(store, device_ids)

    runtime = _SeededRuntime(values)
    app = build_app(store, _invoke, runtime)
    print(f"Datenbank: {args.store_path}")
    print(f"WebUI: http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Confirm the server starts and shows two devices**

Run: `uv run python scripts/dev_web_server.py`
Expected: console shows `Datenbank: …` and `WebUI: http://127.0.0.1:8420`, process stays hanging (running), no traceback. Open `http://127.0.0.1:8420` in the browser, set a password, the "Devices" tab shows two cards ("Steckdose Wohnzimmer", "Taster Flur"). Stop with Ctrl+C.

- [ ] **Step 3: Commit**

```bash
git add scripts/dev_web_server.py
git commit -m "$(cat <<'EOF'
chore(dev): Hilfsserver fuer die manuelle WebUI-Ansicht ohne matter-server

Zeigt zwei Beispielgeraete aus den vorhandenen Test-Fixtures - fuer die
manuelle Verifikation der Geraete-Dashboard-Aenderungen im Browser
(dieses Repo hat keine Frontend-Testinfrastruktur).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `style.css` — copper/amber accent, status and icon classes

**Files:**
- Modify: `src/loxmatter/web/style.css`

**Interfaces:**
- Produces: CSS tokens `--off`/`--off-bg`/`--type-bg`/`--type-fg` (new), changed `--accent`/`--accent-contrast`. Classes `.icon`, `.type-badge`, `.status-pill`(`.warn`/`.off`), `.device-card`(`.is-changed`/`.is-offline`), `.value-chips`/`.value-chip` — used by task 8/9.
- Consumes: nothing new.

- [ ] **Step 1: Switch the accent color, add new tokens**

In `src/loxmatter/web/style.css`, in the `:root` block (line 27-43), replace:

```css
  --accent: #2d6a4f;
  --accent-contrast: #ffffff;
```

with:

```css
  /* Copper/amber instead of green (device dashboard design, approved
     against a mockup): deliberately separate from --ok, which stays
     green - a copper primary button next to a green "online" marker
     should not look like the same state. */
  --accent: #a15a2c;
  --accent-contrast: #ffffff;
```

Add, in the same block, after `--warn-bg: #fdf3e0;`:

```css
  /* Offline status of a device card (section 3 of the design) - its own
     color instead of --danger, which elsewhere (connection status up
     in the header) stays red. */
  --off: #5b6572;
  --off-bg: #e7e9ec;
  /* Tinted background for the type icon of a device card - derived from
     the accent color, not from --ok: the icon shows "this is a
     device," not a status. */
  --type-bg: #f4e6da;
  --type-fg: #a15a2c;
```

In the `@media (prefers-color-scheme: dark)` block (line 45-61), replace:

```css
    --accent: #6fbf9a;
    --accent-contrast: #0c1210;
```

with:

```css
    --accent: #e2915c;
    --accent-contrast: #2a1508;
```

and add, after `--warn-bg: #362a10;`:

```css
    --off: #98a3ad;
    --off-bg: #23282d;
    --type-bg: #2e2015;
    --type-fg: #e2915c;
```

- [ ] **Step 2: Append the new component classes**

At the end of `style.css`, after `.heartbeat`, append:

```css
/* Device dashboard design (2026-09-03): icons, status pill, and value
   chips for the always-open device card. */

.icon {
  width: 1.1em;
  height: 1.1em;
  stroke: currentColor;
  fill: none;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
  vertical-align: -0.15em;
  flex: none;
}

.type-badge {
  width: 2.1rem;
  height: 2.1rem;
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--type-bg);
  color: var(--type-fg);
  flex: none;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.28rem 0.65rem;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 600;
  margin-left: auto;
}

.status-pill.warn {
  background: var(--warn-bg);
  color: var(--warn);
}

.status-pill.off {
  background: var(--off-bg);
  color: var(--off);
}

/* Left-hand color stripe on a device card - carries the same meaning
   as the status pill next to it, deliberately redundant: stands out
   when scrolling quickly over many devices, without the pill needing
   to be read. Base state (no modifier) is green = unremarkable. */
.device-card {
  position: relative;
  overflow: hidden;
  padding-left: calc(1rem + 4px);
}

.device-card::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  background: var(--ok);
}

.device-card.is-changed::before {
  background: var(--warn);
}

.device-card.is-offline::before {
  background: var(--off);
}

.device-card.is-offline {
  opacity: 0.75;
}

.value-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.value-chip {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.35rem 0.7rem;
}
```

- [ ] **Step 3: Verify manually**

Run: `uv run python scripts/dev_web_server.py`
Expected: check the header in the browser — the "Devices" tab now carries a copper-colored underline instead of a green one (`nav.tabs button.active { border-bottom-color: var(--accent) }`, unchanged, only the token value has changed). The "Commission new device" button ("Commission", `.primary`) is now copper-colored instead of green. No visible layout breaks. (The new classes `.type-badge`/`.status-pill`/`.device-card`/`.value-chip` themselves aren't used in the markup until task 8 — here only check that nothing existing breaks.)

- [ ] **Step 4: Commit**

```bash
git add src/loxmatter/web/style.css
git commit -m "$(cat <<'EOF'
style(web): Kupfer/Amber-Akzent, neue Status- und Icon-Klassen

Akzentfarbe von Gruen auf Kupfer umgestellt (Statusfarben --ok/--warn
bleiben unangetastet) und die Bausteine ergaenzt, die Task 8/9 fuer die
immer offene Geraetekarte brauchen.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: "Settings" tab

**Files:**
- Modify: `src/loxmatter/web/index.html` (icon symbols, nav button, new section)
- Modify: `src/loxmatter/web/app.js` (state, `loadSettings`/`saveSettings`, `selectView`)

**Interfaces:**
- Consumes: `GET`/`PATCH /api/settings` from task 2.
- Produces: `app().bridgeSettings: {bridge_ip, udp_port, listen_port, saved_at}` (populated after loading), `app().settingsDraft: {bridge_ip, udp_port, listen_port}` (input fields), `app().loadSettings()`, `app().saveSettings()` — read by task 7 (export tab) and task 9 (export button on the card).

- [ ] **Step 1: Add icon symbols and the new tab in `index.html`**

Insert directly after `<body x-data="app()">` (line 55) — icons defined once, referenced everywhere via `<use>`:

```html
  <body x-data="app()">
    <!-- Icon symbols (device dashboard design, section 3) - inline SVG
         instead of an icon library: no network reference, the same
         reasoning as for the vendored Alpine.js above in the header
         comment. -->
    <svg style="display: none" aria-hidden="true">
      <symbol id="i-device" viewBox="0 0 24 24">
        <rect x="4" y="4" width="16" height="16" rx="3" />
        <circle cx="12" cy="12" r="2.2" />
      </symbol>
      <symbol id="i-warn" viewBox="0 0 24 24">
        <path d="M12 3.7 21 19.3H3L12 3.7z" />
        <path d="M12 9.6v4.3M12 16.9v.1" />
      </symbol>
      <symbol id="i-offline" viewBox="0 0 24 24">
        <path d="M2 8.3a15 15 0 0 1 6-3M16.2 5.4a15 15 0 0 1 5.8 2.9" />
        <path d="M5.5 12a10 10 0 0 1 5-2.6M13.7 9.4a10 10 0 0 1 4.8 2.6" />
        <path d="M9 15.6a5 5 0 0 1 3.5-1.4" />
        <circle cx="12" cy="19" r="1.1" fill="currentColor" stroke="none" />
        <line x1="2" y1="2" x2="22" y2="22" />
      </symbol>
    </svg>
```

In `nav.tabs` (line 142-147), add a fifth button after the "System" button:

```html
    <nav class="tabs">
      <button :class="{ active: view === 'devices' }" @click="selectView('devices')">Geräte</button>
      <button :class="{ active: view === 'signals' }" @click="selectView('signals')">Signale</button>
      <button :class="{ active: view === 'export' }" @click="selectView('export')">Export</button>
      <button :class="{ active: view === 'system' }" @click="selectView('system')">System</button>
      <button :class="{ active: view === 'settings' }" @click="selectView('settings')">Einstellungen</button>
    </nav>
```

After the System section (after `</section>` on line 587, before `</main>` on line 588), insert the new section:

```html
      <!-- ================================================================
           View 5: Settings
           ================================================================ -->
      <section x-show="view === 'settings'" x-cloak>
        <div class="card">
          <h2>Verbindung zum Miniserver</h2>
          <p class="hint">
            Gemeint ist die Adresse des Rechners, auf dem loxmatter läuft – so, wie der
            Miniserver ihn sieht. <strong>Nicht</strong> die Adresse des Miniservers. Der
            virtuelle Eingang nimmt Datagramme nur von dieser Adresse an, und die
            Ausgangsbefehle rufen sie als <span class="key">http://&lt;diese IP&gt;:HTTP-Port</span>
            auf. Steht hier die Miniserver-IP, sehen die Vorlagen richtig aus, bleiben aber
            stumm – ohne jede Fehlermeldung.
          </p>
          <div class="row">
            <label
              >IP dieser Brücke
              <input type="text" x-model="settingsDraft.bridge_ip" placeholder="z. B. 192.168.1.20"
            /></label>
            <label>UDP-Port (virtueller Eingang) <input type="number" x-model.number="settingsDraft.udp_port" /></label>
            <label>
              HTTP-Port (Befehle empfangen)
              <input type="number" x-model.number="settingsDraft.listen_port" />
            </label>
          </div>
          <div class="row">
            <button class="primary" @click="saveSettings()" :disabled="settingsBusy">
              Speichern
            </button>
            <span class="hint" x-show="bridgeSettings.saved_at" x-cloak
              >Zuletzt gespeichert: <span x-text="formatTimestamp(bridgeSettings.saved_at)"></span
            ></span>
            <span class="hint" x-show="!bridgeSettings.saved_at" x-cloak
              >Noch nicht gespeichert.</span
            >
          </div>
          <p x-show="settingsError" x-cloak class="banner danger" x-text="settingsError"></p>
        </div>

        <div class="card">
          <h2>Weitere Einstellungen</h2>
          <p class="hint">Hier entstehen künftig weitere Einstellungen, sobald sie gebraucht werden.</p>
        </div>
      </section>
```

- [ ] **Step 2: Add state and methods in `app.js`**

In the `--- Export ---` state group (line 248-257), remove the three fields `exportBridgeIp`/`exportPort`/`exportListenPort` (they are replaced by `bridgeSettings` in task 7) and insert a new group directly before it:

```js
    // --- Settings ----------------------------------------------------------
    // `bridgeSettings` is the state last loaded from the server (also read
    // by task 7 and task 9); `settingsDraft` are the three input fields on
    // this tab, only adopted after "Save."
    bridgeSettings: { bridge_ip: null, udp_port: 7000, listen_port: 8080, saved_at: null },
    settingsDraft: { bridge_ip: "", udp_port: 7000, listen_port: 8080 },
    settingsBusy: false,
    settingsError: null,

    // --- Export --------------------------------------------------------
    exportIncludeSystem: false,
    exportOnlyPending: false,
    exportPreview: null,
    exportStatusByDevice: {},
    exportBusy: false,
    exportError: null,
```

In `startApp()` (line 375-409), add `this.settingsError = null;` next to the other reset lines (after `this.signalsError = null;`), and include `this.loadSettings()` in the existing `Promise.all` at the end of the method — this step is completed together with task 8 (where `startApp()` is rewritten as a whole, see task 8 step 4); for this task, a standalone call directly after `await this.loadDevices();` is enough:

```js
      await this.loadDevices();
      await this.loadSettings();
```

In `selectView(view)` (line 483-500), add another `else if` branch:

```js
      } else if (view === "system") {
        await this.loadSystem();
      } else if (view === "settings") {
        await this.loadSettings();
      }
```

In the "Export" section (after `loadExportStatus`, before `previewExport`, around line 908), insert `loadSettings`/`saveSettings` — its own section comment:

```js
    // ---------------------------------------------------------------------
    // Settings
    // ---------------------------------------------------------------------

    async loadSettings() {
      this.settingsError = null;
      try {
        this.bridgeSettings = await this.request("GET", "/api/settings");
        this.settingsDraft = {
          bridge_ip: this.bridgeSettings.bridge_ip ?? "",
          udp_port: this.bridgeSettings.udp_port,
          listen_port: this.bridgeSettings.listen_port,
        };
      } catch (error) {
        this.settingsError = `Einstellungen konnten nicht geladen werden: ${error.message}`;
      }
    },

    async saveSettings() {
      this.settingsError = null;
      if (!this.settingsDraft.bridge_ip.trim()) {
        this.settingsError = "Bitte die IP dieser Brücke eingeben.";
        return;
      }
      this.settingsBusy = true;
      try {
        this.bridgeSettings = await this.request("PATCH", "/api/settings", {
          bridge_ip: this.settingsDraft.bridge_ip.trim(),
          udp_port: Number(this.settingsDraft.udp_port),
          listen_port: Number(this.settingsDraft.listen_port),
        });
        this.showToast("Einstellungen gespeichert.");
      } catch (error) {
        this.settingsError = `Einstellungen konnten nicht gespeichert werden: ${error.message}`;
      } finally {
        this.settingsBusy = false;
      }
    },

```

- [ ] **Step 3: Verify manually**

Run: `uv run python scripts/dev_web_server.py`
Expected: a fifth tab "Settings" appears in the browser. There, enter IP `192.168.1.20`, UDP port `7000`, HTTP port `8080`, click "Save" → toast "Einstellungen gespeichert.", hint "Zuletzt gespeichert: …" appears. Reload the page (F5) → the same tab still shows `192.168.1.20` (saved server-side, no loss on reload).

- [ ] **Step 4: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/app.js
git commit -m "$(cat <<'EOF'
feat(web): neuer Tab Einstellungen fuer die Miniserver-Verbindung

Fuenfter, gleichrangiger Tab - IP/Ports werden jetzt serverseitig ueber
/api/settings verwaltet statt bei jedem Laden neu einzugeben.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: the export tab becomes read-only

**Files:**
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/app.js`

**Interfaces:**
- Consumes: `app().bridgeSettings` from task 6.
- Produces: `previewExport()`/`downloadUrl()`/`downloadExport()` read `bridgeSettings` instead of the removed `exportBridgeIp`/`exportPort`/`exportListenPort` fields — the same behavior as before, just with the new source.

- [ ] **Step 1: Make the input fields read-only in `index.html`**

In the export section (line 434-481), replace the first `<div class="row">` (the three input fields) and the following hint text:

```html
      <section x-show="view === 'export'" x-cloak>
        <div class="card">
          <h2>Vorlagen exportieren</h2>
          <div class="row">
            <label
              >IP dieser Brücke
              <input type="text" :value="bridgeSettings.bridge_ip || ''" readonly
            /></label>
            <label>UDP-Port <input type="number" :value="bridgeSettings.udp_port" readonly /></label>
            <label>
              HTTP-Port (Kommandos)
              <input type="number" :value="bridgeSettings.listen_port" readonly />
            </label>
          </div>
          <p class="hint">
            Wird in
            <a href="#" @click.prevent="selectView('settings')">Einstellungen → Verbindung zum Miniserver</a>
            verwaltet.
          </p>
          <div class="row">
            <label><input type="checkbox" x-model="exportIncludeSystem" /> Systemvorlagen einschließen</label>
            <label
              ><input type="checkbox" x-model="exportOnlyPending" /> nur noch nicht exportierte
              Geräte</label
            >
          </div>
          <p class="hint">
            Der Filter gilt für die Vorschau <strong>und</strong> für das ZIP: ist er gesetzt,
            enthält der Download nur die Geräte aus der Tabelle unten, und nur diese gelten
            danach als exportiert.
          </p>
          <div class="row">
            <button class="primary" @click="previewExport()" :disabled="exportBusy">
              Vorschau ansehen
            </button>
            <button class="primary" @click="downloadExport()">ZIP herunterladen</button>
          </div>
          <p x-show="exportError" x-cloak class="banner danger" x-text="exportError"></p>
        </div>
```

(The rest of the section — preview table starting at `<div class="card" x-show="exportPreview" x-cloak>` — stays unchanged.)

- [ ] **Step 2: Switch `app.js` to `bridgeSettings`**

`previewExport()` (line 914-933) becomes:

```js
    async previewExport() {
      this.exportError = null;
      if (!this.bridgeSettings.bridge_ip) {
        this.exportError =
          "Bitte zuerst in Einstellungen → Verbindung zum Miniserver die Brücken-IP hinterlegen.";
        return;
      }
      this.exportBusy = true;
      try {
        const params = new URLSearchParams({
          bridge_ip: this.bridgeSettings.bridge_ip,
          system: String(this.exportIncludeSystem),
        });
        this.exportPreview = await this.request("GET", `/api/export/preview?${params}`);
        await this.loadExportStatus();
      } catch (error) {
        this.exportError = `Vorschau fehlgeschlagen: ${error.message}`;
      } finally {
        this.exportBusy = false;
      }
    },
```

`downloadUrl()` (line 956-965) becomes:

```js
    downloadUrl() {
      const params = new URLSearchParams({
        bridge_ip: this.bridgeSettings.bridge_ip,
        port: String(this.bridgeSettings.udp_port),
        listen: String(this.bridgeSettings.listen_port),
        system: String(this.exportIncludeSystem),
        only_pending: String(this.exportOnlyPending),
      });
      return `/api/export/download?${params}`;
    },
```

`downloadExport()` (line 980-999) becomes:

```js
    async downloadExport() {
      this.exportError = null;
      if (!this.bridgeSettings.bridge_ip) {
        this.exportError =
          "Bitte zuerst in Einstellungen → Verbindung zum Miniserver die Brücken-IP hinterlegen.";
        return;
      }
      try {
        await this.download(this.downloadUrl(), "loxmatter-export.zip");
      } catch (error) {
        this.exportError = `Download fehlgeschlagen: ${error.message}`;
        return;
      }
      await this.loadExportStatus();
    },
```

- [ ] **Step 3: Verify manually**

Run: `uv run python scripts/dev_web_server.py`
Expected: the "Export" tab shows the three fields grayed out/read-only with the value last saved in "Settings" (save `192.168.1.20`/`7000`/`8080` there first, see task 6 step 3). Clicking the "Einstellungen → Verbindung zum Miniserver" link switches the tab. "Vorschau ansehen" and "ZIP herunterladen" keep working (the preview table appears, the ZIP downloads). Without previously saved settings (a fresh database, `--store-path` pointed at a new file), clicking "Vorschau ansehen" shows the error message "Bitte zuerst in Einstellungen …" instead of a server 422.

- [ ] **Step 4: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/app.js
git commit -m "$(cat <<'EOF'
feat(web): Export-Tab zeigt Bridge-Einstellungen nur noch an

IP/Ports kommen jetzt aus Einstellungen (schreibgeschuetzt hier) statt
aus eigenen, bei jedem Laden leeren Eingabefeldern.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: device tile — always open, icon, status stripe

**Files:**
- Modify: `src/loxmatter/web/index.html` (device section replaced entirely)
- Modify: `src/loxmatter/web/app.js` (`startApp`, `commissionDevice`, `removeDevice`, new helpers, `toggleExpanded`/`expandedDeviceId` removed)

**Interfaces:**
- Consumes: `app().bridgeSettings` (task 6/7, only read along here, the export button itself comes in task 9), `GET /api/export/status` (already present).
- Produces: `app().changedSinceExport(deviceId): bool`, `app().exportHintFor(deviceId): string`, `app().deviceCardClass(device): {is-changed, is-offline}` — read by the new tile in `index.html`. Removed: `app().expandedDeviceId`, `app().toggleExpanded`.

- [ ] **Step 1: Rewrite `startApp()` — all cards load immediately**

Replace the `startApp()` method in `src/loxmatter/web/app.js` (line 375-409):

```js
    async startApp() {
      this.backupError = null;
      this.exportError = null;
      this.deviceActionError = null;
      this.signalsError = null;
      this.settingsError = null;
      this.controlsByDevice = {};
      this.signalsByDevice = {};
      await this.loadDevices();
      // Every card shows values and controls immediately, with no click
      // needed (device dashboard design section 3) - that's why startApp()
      // loads both for EVERY device, not just for one after it's
      // expanded (which no longer exists as of this design).
      await Promise.all([
        ...this.devices.map((device) => this.loadControls(device.id)),
        ...this.devices.map((device) => this.loadSignals(device.id)),
        this.loadExportStatus(),
        this.loadSettings(),
      ]);
      this.connectLive();
      await this.selectView(this.view);
    },
```

(This also replaces the standalone `await this.loadSettings();` call inserted in task 6 step 2 — it is now part of the `Promise.all`.)

- [ ] **Step 2: Remove `expandedDeviceId`/`toggleExpanded`**

In the `--- Geraete ---` state group (line 204-224), remove the line `expandedDeviceId: null,`.

Remove the method `toggleExpanded` (line 538-546) entirely.

In `removeDevice` (line 693-716), remove the three lines

```js
        if (this.expandedDeviceId === device.id) {
          this.expandedDeviceId = null;
        }
```

.

- [ ] **Step 3: Add new helpers**

Insert after `exportedAtFor` (line 591-594):

```js
    // Like `ExportStatusOut.changed_since_export` server-side: without
    // a loaded status (e.g. a device just now commissioned, before the
    // next `loadExportStatus` round has gone through) it counts as
    // "changed" - the same cautious assumption as on the server (see
    // api/export.py, `_changed_since_export`).
    changedSinceExport(deviceId) {
      const status = this.exportStatusFor(deviceId);
      return status ? status.changed_since_export : true;
    },

    exportHintFor(deviceId) {
      const status = this.exportStatusFor(deviceId);
      if (!status || !status.exported_at) {
        return "Noch nicht exportiert";
      }
      return `Zuletzt exportiert am ${this.formatTimestamp(status.exported_at)}`;
    },

    // Classes for the tile's color stripe (style.css, `.device-card`) -
    // a function instead of an inline expression in index.html, because
    // two conditions (online AND changed) come together here.
    deviceCardClass(device) {
      return {
        "is-offline": !this.isOnline(device),
        "is-changed": this.isOnline(device) && this.changedSinceExport(device.id),
      };
    },

```

- [ ] **Step 4: `commissionDevice()` immediately loads values/controls for the new device**

In `commissionDevice()` (line 791-829), add after `this.devices.push(device);`:

```js
        const device = await this.request("POST", "/api/devices/commission", body);
        this.devices.push(device);
        // The card is visible and always open from this point on
        // (section 3) - without this reload it would show "Signale
        // werden geladen…" permanently, until the view was eventually
        // re-entered.
        await Promise.all([this.loadControls(device.id), this.loadSignals(device.id)]);
```

- [ ] **Step 5: Replace the device section in `index.html`**

Replace the complete block from `<template x-for="device in devices" :key="device.id">` to the corresponding `</template>` (line 191-293) in `src/loxmatter/web/index.html`:

```html
        <template x-for="device in devices" :key="device.id">
          <div class="card device-card" :class="deviceCardClass(device)">
            <div class="row">
              <span class="type-badge">
                <svg class="icon"><use href="#i-device"></use></svg>
              </span>
              <input
                type="text"
                :value="device.label"
                @input="labelDrafts[device.id] = $event.target.value"
                @change="saveLabel(device)"
              />
              <span class="status-pill warn" x-show="isOnline(device) && changedSinceExport(device.id)">
                <svg class="icon"><use href="#i-warn"></use></svg>
                Geändert seit Export
              </span>
              <span class="status-pill off" x-show="!isOnline(device)">
                <svg class="icon"><use href="#i-offline"></use></svg>
                Offline
              </span>
              <button class="danger" @click="removeDevice(device)">Entfernen</button>
            </div>

            <div class="device-controls">
              <h3>Werte</h3>
              <p class="hint" x-show="!signalsByDevice[device.id]">Signale werden geladen…</p>
              <p
                class="hint"
                x-show="signalsByDevice[device.id] && firstSignalsFor(device.id).length === 0"
              >
                Keine funktionalen Signale für dieses Gerät.
              </p>
              <div class="value-chips" x-show="signalsByDevice[device.id]">
                <template x-for="signal in firstSignalsFor(device.id)" :key="signal.key">
                  <span class="value-chip">
                    <span x-text="signal.title"></span>
                    <span
                      class="value"
                      x-text="formatValue(liveValueOf(signal)) + (signal.unit ? ' ' + signal.unit : '')"
                    ></span>
                  </span>
                </template>
              </div>
              <p class="hint" x-show="remainingSignalCount(device.id) > 0">
                <span x-text="remainingSignalCount(device.id)"></span>
                weitere in der Ansicht „Signale".
              </p>
            </div>

            <div class="device-controls">
              <h3>Bedienung</h3>
              <p class="hint" x-show="!controlsLoaded(device.id)">
                Bedienelemente werden geladen…
              </p>
              <p
                class="hint"
                x-show="controlsLoaded(device.id) && commandsFor(device.id).length === 0"
              >
                Keine bekannten Ausgangsbefehle für dieses Gerät.
              </p>
              <div class="row">
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
                        style="width: 5.5rem"
                        placeholder="Wert"
                        @input="commandValueDrafts[command.key] = $event.target.value"
                      />
                      <button
                        @click="executeCommand(device, command)"
                        :disabled="commandBusyKey === command.key || !isOnline(device)"
                      >
                        Senden
                      </button>
                    </span>
                  </span>
                </template>
              </div>
              <p class="hint" x-show="hiddenRawCommandsFor(device.id) > 0">
                <span x-text="hiddenRawCommandsFor(device.id)"></span>
                weitere Kommandos vorhanden, aber nicht benannt.
              </p>
            </div>

            <div class="device-controls row">
              <span class="hint" x-text="exportHintFor(device.id)"></span>
            </div>
          </div>
        </template>
```

(The export button in the last row is deliberately missing here — it comes in task 9, together with the method that triggers it. Without it, the footer only shows the export hint text for this task.)

- [ ] **Step 6: Verify manually**

Run: `uv run python scripts/dev_web_server.py`
Expected: the "Devices" tab shows both cards immediately with values ("Zustand: Ein", "Leistung: 12,4 W" for the plug — thanks to the values seeded in task 4) and controls, with no click on "Details" needed (that button no longer exists). The plug shows an amber "Geändert seit Export" pill (never exported = `changed_since_export: true`) and an amber edge stripe. Renaming still works (input field, Enter/loss of focus). "Entfernen" still works (confirmation prompt, card disappears).

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/app.js
git commit -m "$(cat <<'EOF'
feat(web): Geraetekarte immer offen, mit Status-Streifen und Icon

Kein "Details"-Umschalter mehr - Werte und Bedienelemente stehen sofort
auf jeder Karte, Status (unauffaellig/geaendert/offline) zeigt sich ueber
Rand-Streifen und Pille statt nur ueber Text.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: export button on the device tile

**Files:**
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/app.js`

**Interfaces:**
- Consumes: `GET /api/export/download?device_id=…` (task 3), `app().bridgeSettings` (task 6).
- Produces: `app().exportDevice(device)` — triggers the download for exactly one device.

- [ ] **Step 1: Add `exportDevice` in `app.js`**

In the "Export" section, directly after `downloadExport()` (after the end of the method shown in task 7 step 2), insert:

```js

    // Export button on a single device tile (device dashboard design,
    // section 6) - no preview step: the values are already sitting
    // openly on the card, an additional preview would be duplicate
    // information.
    async exportDevice(device) {
      this.deviceActionError = null;
      if (!this.bridgeSettings.bridge_ip) {
        this.deviceActionError =
          "Bitte zuerst in Einstellungen → Verbindung zum Miniserver die Brücken-IP hinterlegen.";
        return;
      }
      const params = new URLSearchParams({
        bridge_ip: this.bridgeSettings.bridge_ip,
        port: String(this.bridgeSettings.udp_port),
        listen: String(this.bridgeSettings.listen_port),
        device_id: String(device.id),
      });
      try {
        await this.download(`/api/export/download?${params}`, `loxmatter-d${device.id}-export.zip`);
        this.showToast(`${device.label} wurde exportiert.`);
      } catch (error) {
        this.deviceActionError = `Export fehlgeschlagen: ${error.message}`;
        return;
      }
      await this.loadExportStatus();
    },
```

- [ ] **Step 2: Add the button in `index.html`**

The footer of the device tile (from task 8 step 5, last `<div class="device-controls row">`) becomes:

```html
            <div class="device-controls row">
              <span class="hint" x-text="exportHintFor(device.id)"></span>
              <span style="flex: 1 1 auto"></span>
              <button
                class="primary"
                @click="exportDevice(device)"
                :disabled="!bridgeSettings.bridge_ip"
                :title="!bridgeSettings.bridge_ip ? 'Erst in Einstellungen → Verbindung zum Miniserver hinterlegen' : ''"
              >
                Exportieren
              </button>
            </div>
```

- [ ] **Step 3: Verify manually**

Run: `uv run python scripts/dev_web_server.py`

First save IP `192.168.1.20`/ports `7000`/`8080` in "Settings" (if not already done). Then in the "Devices" tab:

Expected: every card shows an "Exportieren" button in the footer. Clicking it on "Steckdose Wohnzimmer" → the browser downloads a file `loxmatter-d<id>-export.zip`, a toast "Steckdose Wohnzimmer wurde exportiert." appears, the amber "Geändert seit Export" pill disappears (status reloaded, the device now counts as exported). Unzip the ZIP file and check: it contains only `VIU_d<id>_….xml` and `VO_d<id>_….xml` of this one device, not the button's.

Without saved settings (a new database via `--store-path` pointed at a new file), the "Exportieren" button is grayed out, with tooltip "Erst in Einstellungen → Verbindung zum Miniserver hinterlegen".

- [ ] **Step 4: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/app.js
git commit -m "$(cat <<'EOF'
feat(export): Export-Knopf an jeder Geraetekarte

Exportiert genau das eine Geraet direkt herunter, ohne in den Export-
Tab wechseln zu muessen - nutzt die in Task 3 ergaenzte device_id an
GET /api/export/download und die Einstellungen aus Task 6.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## After implementation

- `uv run pytest` (the complete suite) should be green — in particular `tests/api/test_security.py` (every router behind `api_guard`) and `tests/api/test_web.py` (static delivery of `index.html`/`app.js`/`style.css` remains reachable unchanged).
- A full manual run-through with `uv run python scripts/dev_web_server.py`: device list → values visible immediately, export button per device → correct single-device ZIP, settings → survives a reload, export tab → shows the same values read-only and still exports all/pending devices.
