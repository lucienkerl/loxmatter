# Geräte-Dashboard: immer offene Karten, Export pro Gerät — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Geräte-Kacheln im Dashboard zeigen Werte und Bedienelemente immer offen (kein Klick mehr nötig), tragen einen Status-Farbstreifen/Icon, und lassen sich einzeln exportieren; ein neuer Einstellungen-Tab verwaltet die Bridge-Verbindungsdaten serverseitig, der bisherige Export-Tab zeigt sie nur noch schreibgeschützt an.

**Architecture:** Backend (FastAPI/SQLite, `src/loxmatter/`) bekommt eine neue, kleine `BridgeSettingsStore`-Klasse (liest/schreibt die bereits vorhandene generische `setting`-Tabelle, analog zu `AuthStore`), einen neuen `/api/settings`-Router, und einen optionalen `device_id`-Parameter an `GET /api/export/download`. Frontend (Alpine.js, kein Build-Schritt, `src/loxmatter/web/`) bekommt neue CSS-Tokens/-Klassen, einen fünften Tab, und eine überarbeitete Geräte-Kachel — alles im bestehenden Stil (`.card`, `.row`, `.hint` …), keine neue Abhängigkeit.

**Tech Stack:** Python 3.12, FastAPI, SQLite (`sqlite3`), Pydantic v2, pytest/httpx2 (Backend); Alpine.js 3.17 (vendort), reines CSS, kein Bundler (Frontend).

## Global Constraints

- Referenz-Spec: `docs/superpowers/specs/2026-09-03-geraete-dashboard-und-export-design.md` — jede Abweichung unten ist explizit benannt.
- Akzentfarbe (Kupfer/Amber, vom Auftraggeber freigegeben): `#a15a2c` hell / `#e2915c` dunkel, Kontrastfarbe `#ffffff` hell / `#2a1508` dunkel. Statusfarben (`--ok` grün, `--warn` amber, neu `--off` grau) bleiben davon unabhängig.
- Deutsch in jedem Text, der auf dem Bildschirm oder in einer Fehlermeldung landet; Englisch in alle Bezeichnern (Variablen, Funktionen, Endpunkt-Felder) — bestehende Konvention, siehe `app.js`-Kopfkommentar.
- Kein `console.log`, kein neues externes Skript/CDN in `index.html` — die Oberfläche läuft offline (siehe `index.html`-Kopfkommentar zu Alpine.js).
- **Abweichung von Spec Abschnitt 3 (bewusst, siehe Abschnitt 9.1 der Spec, der das offen lässt):** kein Icon pro Gerätetyp (Stecker/Bewegung/Lamellen) — das bräuchte eine gegen die Matter Device Library belegte Zuordnungstabelle, die nirgends im Code oder in den Specs dieses Projekts bereits verifiziert vorliegt. Stattdessen EIN generisches Geräte-Icon für jede Karte; Status-Icons (Warndreieck, Offline) sind davon unbenommen, sie hängen nur an bereits vorhandenen Feldern (`online`, `changed_since_export`), keine Matter-Typerkennung nötig.
- Keine Frontend-Testinfrastruktur in diesem Repo (kein `tests/web/`, kein JS-Test-Runner) — Frontend-Tasks unten werden über einen neuen Hilfsserver (Task 4) manuell im Browser verifiziert, Backend-Tasks per `pytest`.

---

## Task 1: `BridgeSettingsStore` — Speicherung der Bridge-Einstellungen

**Files:**
- Modify: `src/loxmatter/model/store.py:61` (neue Konstante), `src/loxmatter/model/store.py:696-705` (Import + Verdrahtung in `Store.__init__`)
- Create: `src/loxmatter/model/settings_store.py`
- Test: `tests/model/test_settings_store.py`

**Interfaces:**
- Produces: `loxmatter.model.store.DEFAULT_LISTEN_PORT: int` (= 8080). `loxmatter.model.settings_store.BridgeSettings` (frozen dataclass: `bridge_ip: str | None`, `udp_port: int`, `listen_port: int`, `saved_at: str | None`). `loxmatter.model.settings_store.BridgeSettingsStore(db: sqlite3.Connection)` mit `.get() -> BridgeSettings` und `.save(*, bridge_ip: str, udp_port: int, listen_port: int) -> BridgeSettings`. `Store.settings: BridgeSettingsStore` (Attribut, wie `Store.auth`).
- Consumes: nichts Neues — nutzt die bereits existierende Tabelle `setting` (`store.py:128-131`, seit Schema-Version 5 auf jeder Datenbank vorhanden, keine neue Migration nötig).

- [ ] **Step 1: Schreibe die fehlschlagenden Tests**

Lege `tests/model/test_settings_store.py` an:

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

"""Tests fuer `BridgeSettingsStore` - den Teil des Stores, der die
Verbindungsdaten zur Bruecke (IP, Ports) verwaltet, analog zu `AuthStore`.

Siehe docs/superpowers/specs/2026-09-03-geraete-dashboard-und-export-design.md,
Abschnitt 4."""

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
    """Serverseitig statt localStorage (Entwurf Abschnitt 4): der Punkt ist
    genau, dass es einen Prozessneustart uebersteht."""
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

- [ ] **Step 2: Lauf bestätigen, dass die Tests fehlschlagen**

Run: `uv run pytest tests/model/test_settings_store.py -v`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'settings'` (und `ImportError` für `DEFAULT_LISTEN_PORT`, falls die Sammlung schon dort scheitert)

- [ ] **Step 3: Lege `settings_store.py` an**

Erstelle `src/loxmatter/model/settings_store.py`:

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

"""Zugriff auf die Verbindungsdaten dieser Bruecke - IP und Ports, wie sie
heute schon im Export-Tab eingegeben werden (`api/export.py`).

Eigenes Modul und eigene Klasse, analog zu `auth_store.py`: die `setting`-
Tabelle ist generisch (Schluessel/Wert) angelegt, genau damit weitere
Konfiguration wie diese hier denselben Weg gehen kann (siehe dortiger
Moduldocstring, Spec 14.2 des Login-Entwurfs). Diese Klasse ist eine weitere
Sicht auf dieselbe Tabelle und dieselbe Verbindung, kein zweiter
Verbindungsaufbau.

Siehe docs/superpowers/specs/2026-09-03-geraete-dashboard-und-export-design.md,
Abschnitt 4: serverseitig statt `localStorage`, weil die Bridge-Adresse eine
Eigenschaft der Installation ist, nicht des Browsers."""

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
    """`bridge_ip`/`saved_at` sind `None`, solange niemand gespeichert hat -
    die Ports fallen in dem Fall auf dieselben Vorgaben zurueck wie der
    bisherige Export-Tab (`DEFAULT_UDP_PORT`/`DEFAULT_LISTEN_PORT`)."""

    bridge_ip: str | None
    udp_port: int
    listen_port: int
    saved_at: str | None


class BridgeSettingsStore:
    """Zugriff auf `setting` ueber die Verbindung des Stores - wie
    `AuthStore`, nur fuer andere Schluessel."""

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
        """Schreibt alle drei Werte und den Zeitstempel in einer Transaktion
        - kein Teil-Update: die drei Felder gehoeren fachlich zusammen."""
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

- [ ] **Step 4: Verdrahte `DEFAULT_LISTEN_PORT` und `Store.settings`**

In `src/loxmatter/model/store.py`, Zeile 61 (`DEFAULT_UDP_PORT = 7000`), ergänze direkt danach:

```python
DEFAULT_UDP_PORT = 7000
# `_DEFAULT_LISTEN_PORT` von `api/export.py` hierher gehoben (Geraete-
# Dashboard-Entwurf, Abschnitt 4): der neue `BridgeSettingsStore` unten
# braucht denselben Vorgabewert, und ein zweiter, unabhaengig gepflegter
# Literal `8080` waere genau die Art Drift, vor der `api/export.py`s eigener
# Moduldocstring (Entscheidung 2) bereits warnt.
DEFAULT_LISTEN_PORT = 8080
```

Ergänze den Import am Kopf der Datei (nach der bestehenden `AuthStore`-Zeile, ca. Zeile 51):

```python
from loxmatter.model.auth_store import AuthStore
from loxmatter.model.settings_store import BridgeSettingsStore
```

Und in `Store.__init__` (Zeile 696-705), direkt nach `self.auth = AuthStore(self._db)`:

```python
        self.auth = AuthStore(self._db)
        # Sicht auf dieselbe Verbindung - siehe `settings_store.py`.
        self.settings = BridgeSettingsStore(self._db)
```

- [ ] **Step 5: Lauf bestätigen, dass die Tests durchlaufen**

Run: `uv run pytest tests/model/test_settings_store.py -v`
Expected: PASS (4 Tests)

- [ ] **Step 6: `export.py` auf die zentrale Konstante umstellen**

`src/loxmatter/api/export.py` definiert bislang selbst `_DEFAULT_LISTEN_PORT = 8080` (Zeile 109). Ersetze den Import (Zeile 106) und die Konstante:

```python
from loxmatter.model.store import (
    DEFAULT_LISTEN_PORT,
    DEFAULT_UDP_PORT,
    Store,
    StoredCommand,
    StoredDevice,
)
```

Entferne Zeile 109 (`_DEFAULT_LISTEN_PORT = 8080`) und ersetze die einzige Verwendung in der `download`-Route (Query-Default für `listen`, aktuell `_DEFAULT_LISTEN_PORT`) durch `DEFAULT_LISTEN_PORT`.

- [ ] **Step 7: Bestehende Export-Tests laufen weiter**

Run: `uv run pytest tests/api/test_export_api.py -v`
Expected: PASS (keine Verhaltensänderung, nur derselbe Wert aus einem anderen Modul)

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

## Task 2: `/api/settings` — REST-Endpunkte

**Files:**
- Modify: `src/loxmatter/api/models.py` (neue Modelle anhängen)
- Create: `src/loxmatter/api/settings.py`
- Modify: `src/loxmatter/loxone/server.py` (Router einhängen)
- Test: `tests/api/test_settings_api.py`

**Interfaces:**
- Consumes: `Store.settings` aus Task 1 (`BridgeSettingsStore.get()`/`.save(...)`).
- Produces: `GET /api/settings` und `PATCH /api/settings`, beide `-> BridgeSettingsOut` (JSON: `bridge_ip: str | None`, `udp_port: int`, `listen_port: int`, `saved_at: str | None`). `loxmatter.api.settings.build_settings_router(store: Store) -> APIRouter`.

- [ ] **Step 1: Schreibe die fehlschlagenden Tests**

Lege `tests/api/test_settings_api.py` an:

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

"""Tests fuer den Einstellungen-Endpunkt (`api/settings.py`) - siehe
docs/superpowers/specs/2026-09-03-geraete-dashboard-und-export-design.md,
Abschnitt 4."""

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
    """Kein zweiter, unabhaengiger Speicher (dieselbe Ueberlegung wie
    `api/export.py`s Moduldocstring fuer den Store insgesamt)."""
    client, store = api
    await client.patch(
        "/api/settings", json={"bridge_ip": "10.0.0.5", "udp_port": 7000, "listen_port": 8080}
    )
    assert store.settings.get().bridge_ip == "10.0.0.5"


async def test_settings_route_requires_a_session(tmp_path, no_invoke, fake_runtime):
    """Wie jede andere `/api`-Route seit dem WebUI-Login (Spec 9) - kein
    eigener Test noetig fuer den Waechter selbst (der ist bereits in
    `tests/api/test_security.py` fuer alle fuenf Router belegt), nur dass
    dieser sechste Router tatsaechlich dazugehoert."""
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/settings")
    store.close()
    assert response.status_code == 401
```

- [ ] **Step 2: Lauf bestätigen, dass die Tests fehlschlagen**

Run: `uv run pytest tests/api/test_settings_api.py -v`
Expected: FAIL — `404 Not Found` für `/api/settings` (Router existiert noch nicht)

- [ ] **Step 3: Modelle ergänzen**

In `src/loxmatter/api/models.py`, am Dateiende anhängen:

```python
class BridgeSettingsOut(BaseModel):
    """Antwort von `GET`/`PATCH /api/settings` (Geraete-Dashboard-Entwurf,
    Abschnitt 4). `bridge_ip`/`saved_at` sind `None`, solange niemand die
    Verbindung zum Miniserver eingerichtet hat - der Fall, in dem die
    Oberflaeche den Export-Knopf an jeder Geraetekarte deaktiviert."""

    model_config = ConfigDict(frozen=True)

    bridge_ip: str | None
    udp_port: int
    listen_port: int
    saved_at: str | None


class BridgeSettingsIn(BaseModel):
    """Rumpf von `PATCH /api/settings` - alle drei Felder zusammen, kein
    Teil-Update: sie gehoeren fachlich zusammen (dieselbe virtuelle
    Verbindung), ein Teil-Update koennte sonst eine gueltige IP mit einem
    inzwischen falschen Port stehen lassen. `min_length=1` auf `bridge_ip`
    ergibt 422 bei leerem Feld, ohne einen eigenen Validator."""

    model_config = ConfigDict(frozen=True)

    bridge_ip: str = Field(min_length=1)
    udp_port: int
    listen_port: int
```

Ergänze den Import am Kopf der Datei:

```python
from pydantic import BaseModel, ConfigDict, Field
```

- [ ] **Step 4: Router anlegen**

Erstelle `src/loxmatter/api/settings.py`:

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

"""Verbindungseinstellungen der Bruecke (IP, Ports) ueber die API - Geraete-
Dashboard-Entwurf (2026-09-03), Abschnitt 4.

`build_settings_router` baut einen `APIRouter` mit Praefix `/api`, genau wie
`api.devices.build_device_router` - eingebunden in `loxone.server.build_app`
neben den uebrigen Routern dieser Phase, hinter demselben `api_guard`."""

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

- [ ] **Step 5: In `build_app` einhängen**

In `src/loxmatter/loxone/server.py`, Import ergänzen (bei den übrigen `api.*`-Importen, ca. Zeile 119):

```python
from loxmatter.api.devices import build_device_router
from loxmatter.api.settings import build_settings_router
```

Und nach der bestehenden `app.include_router(build_export_router(store), dependencies=api_guard)`-Zeile (ca. Zeile 415):

```python
    app.include_router(build_export_router(store), dependencies=api_guard)
    app.include_router(build_settings_router(store), dependencies=api_guard)
```

- [ ] **Step 6: Lauf bestätigen, dass die Tests durchlaufen**

Run: `uv run pytest tests/api/test_settings_api.py tests/api/test_security.py -v`
Expected: PASS. (`tests/api/test_security.py` prüft den Wächter anhand einzeln benannter Routen wie `/api/devices` oder `/api/export/status`, keine generische Schleife über alle Router — der neue `/api/settings`-Router braucht dort keine Ergänzung; `test_settings_route_requires_a_session` oben deckt ihn bereits ab.)

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

## Task 3: `device_id` an `/api/export/download`

**Files:**
- Modify: `src/loxmatter/api/export.py`
- Modify (Erweiterung, nicht Ersetzung): `tests/api/test_export_api.py`

**Interfaces:**
- Consumes: `store.device(device_id) -> StoredDevice`, wirft `UnknownDeviceError` (bereits vorhanden, `model/store.py`).
- Produces: `GET /api/export/download` akzeptiert einen neuen optionalen Query-Parameter `device_id: int | None`. Gesetzt, überschreibt er `only_pending` (das Gerät wird immer exportiert) und beschränkt das Archiv auf genau dieses eine Gerät; ein unbekanntes `device_id` ergibt 404.

- [ ] **Step 1: Schreibe die fehlschlagenden Tests**

Ergänze am Ende von `tests/api/test_export_api.py`:

```python
# ---------------------------------------------------------------------------
# device_id: Export eines einzelnen Geraets ueber den Export-Knopf an der
# Geraetekarte (Geraete-Dashboard-Entwurf, 2026-09-03, Abschnitt 6). Kein
# eigener Endpunkt - derselbe `/api/export/download`, nur auf ein Geraet
# eingeschraenkt.
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
    """`device_id` gewinnt gegen `only_pending` (Entwurf Abschnitt 6): das
    angeforderte Geraet wird exportiert, auch wenn es laut
    `changed_since_export` gar nicht ausstuende."""
    client, store, first_id = api
    await client.get(f"/api/export/download?bridge_ip=192.168.1.50&device_id={first_id}")
    assert store.device(first_id).exported_at is not None  # bereits exportiert, "nicht aenderend"

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

- [ ] **Step 2: Lauf bestätigen, dass die Tests fehlschlagen**

Run: `uv run pytest tests/api/test_export_api.py -k device_id -v`
Expected: FAIL — `device_id` wird als unbekannter Query-Parameter ignoriert, alle vier Tests scheitern (die ersten drei, weil das ZIP beide/kein Gerät statt nur eines enthält bzw. beide markiert werden; der letzte, weil die Antwort 200 statt 404 ist).

- [ ] **Step 3: `download` um `device_id` erweitern**

In `src/loxmatter/api/export.py`:

Import ergänzen (Zeile 93-94, bei den bestehenden `fastapi`-Importen):

```python
from fastapi import APIRouter, HTTPException, Query
```

Import ergänzen (Zeile 106, bei den bestehenden `model.store`-Importen):

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

Die `download`-Route (Zeile 238-341) wird zu:

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
        """Baut das ZIP im Speicher - keine temporaere Datei, kein
        Zwischenzustand auf der Platte.

        Markiert jedes ausgelieferte Geraet ueber `Store.mark_exported` als
        exportiert (Entscheidung 1 im Modul-Docstring) - aber ERST, nachdem
        das Archiv vollstaendig aufgebaut ist, nicht Geraet fuer Geraet
        waehrend des Aufbaus (Review-Fix Important #1, 2026-09-02).
        Waere zwischen zwei Geraeten ein Fehler aufgetreten - ein Rendern,
        das wirft, ein `store.commands`/`store.signals`, das scheitert, ein
        `forget_device` aus einer parallelen Anfrage -, haette FastAPI 500
        geantwortet und der Client kein ZIP erhalten, waehrend jedes bis
        dahin verarbeitete Geraet trotzdem dauerhaft als exportiert
        vermerkt gewesen waere. Dieselbe Disziplin wie in `cli.py`s
        `export`-Kommando.

        **`device_id` (Geraete-Dashboard-Entwurf, 2026-09-03, Abschnitt 6).**
        Gesetzt, beschraenkt sich die Auswahl auf genau dieses eine Geraet,
        unabhaengig von `only_pending` - der Export-Knopf an einer
        Geraetekarte fragt nie, ob das Geraet "ansteht", er exportiert das
        eine Geraet, das gerade sichtbar ist. Ein unbekanntes oder
        entferntes Geraet ergibt 404, geprueft VOR dem Aufbau des Archivs."""
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

(Die Schleifenvariable im letzten `for` heißt jetzt `device_id_written`, nicht mehr `device_id` — der Parameter `device_id` der Route bliebe sonst ab dieser Zeile überschrieben, unschön beim Lesen, auch wenn es funktional keine Rolle spielt, weil er zu diesem Zeitpunkt nicht mehr gebraucht wird.)

- [ ] **Step 4: Lauf bestätigen, dass die neuen und alten Tests durchlaufen**

Run: `uv run pytest tests/api/test_export_api.py -v`
Expected: PASS (alle bisherigen plus die vier neuen Tests — insbesondere `test_an_unfiltered_download_still_contains_every_device` und die `only_pending`-Tests bleiben unverändert grün, weil `device_id` dort nie gesetzt ist)

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

## Task 4: Hilfsserver für die manuelle Verifikation der Frontend-Tasks

Dieses Repo hat keine Frontend-Testinfrastruktur (kein JS-Test-Runner, `app.js`/`index.html`/`style.css` sind unveränderter statischer Code ohne Build-Schritt). `loxmatter run` selbst braucht eine echte `matter-server`-Verbindung. Für die Tasks 5-9 unten wird deshalb ein kleiner Entwicklungsserver gebraucht, der die WebUI mit zwei Beispielgeräten aus den vorhandenen Test-Fixtures zeigt, ohne Matter-Hardware.

**Files:**
- Create: `scripts/dev_web_server.py`

**Interfaces:**
- Consumes: `loxmatter.loxone.server.build_app`, `loxmatter.model.store.Store`, Fixtures unter `tests/fixtures/nodes/`.
- Produces: ein lokal erreichbarer HTTP-Server unter `http://127.0.0.1:8420`, der von Task 5 an zur manuellen Verifikation dient.

- [ ] **Step 1: Skript anlegen**

Erstelle `scripts/dev_web_server.py`:

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

"""Startet die WebUI mit zwei Beispielgeraeten, ohne matter-server - fuer die
manuelle Ansicht der Geraete-Dashboard-Aenderungen im Browser (siehe
docs/superpowers/plans/2026-09-03-geraete-dashboard-und-export.md, Task 4).

Aufruf: uv run python scripts/dev_web_server.py
Danach: http://127.0.0.1:8420 oeffnen, ein beliebiges Passwort vergeben
(Ersteinrichtung, gilt nur fuer diesen Testlauf).

Die Datenbank liegt in einer festen Datei im Temp-Verzeichnis - ein zweiter
Lauf findet denselben Bestand wieder, statt jedes Mal neu einzulernen."""

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
    """Erfuellt `api.devices.RuntimeValues` mit ein paar erfundenen, aber
    plausiblen Werten - genug, damit die Geraetekarten nicht nur "-" zeigen.
    Kein Ersatz fuer `Runtime`: es gibt keine Live-Verbindung, die Werte
    stehen fest, bis dieser Prozess neu startet."""

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

- [ ] **Step 2: Lauf bestätigen, dass der Server startet und zwei Geräte zeigt**

Run: `uv run python scripts/dev_web_server.py`
Expected: Konsole zeigt `Datenbank: …` und `WebUI: http://127.0.0.1:8420`, Prozess bleibt hängen (läuft), kein Traceback. Im Browser `http://127.0.0.1:8420` öffnen, Passwort vergeben, Tab "Geräte" zeigt zwei Karten ("Steckdose Wohnzimmer", "Taster Flur"). Mit Strg+C beenden.

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

## Task 5: `style.css` — Kupfer/Amber-Akzent, Status- und Icon-Klassen

**Files:**
- Modify: `src/loxmatter/web/style.css`

**Interfaces:**
- Produces: CSS-Tokens `--off`/`--off-bg`/`--type-bg`/`--type-fg` (neu), geänderte `--accent`/`--accent-contrast`. Klassen `.icon`, `.type-badge`, `.status-pill`(`.warn`/`.off`), `.device-card`(`.is-changed`/`.is-offline`), `.value-chips`/`.value-chip` — verwendet von Task 8/9.
- Consumes: nichts Neues.

- [ ] **Step 1: Akzentfarbe umstellen, neue Tokens ergänzen**

In `src/loxmatter/web/style.css`, im `:root`-Block (Zeile 27-43), ersetze:

```css
  --accent: #2d6a4f;
  --accent-contrast: #ffffff;
```

durch:

```css
  /* Kupfer/Amber statt Gruen (Geraete-Dashboard-Entwurf, freigegeben nach
     Vorlage): bewusst getrennt von --ok, das gruen bleibt - eine Kupfer-
     Primaertaste neben einer gruenen "online"-Markierung soll nicht wie
     derselbe Zustand aussehen. */
  --accent: #a15a2c;
  --accent-contrast: #ffffff;
```

Ergänze im selben Block, nach `--warn-bg: #fdf3e0;`:

```css
  /* Offline-Status einer Geraetekarte (Abschnitt 3 des Entwurfs) - eigene
     Farbe statt --danger, das an anderer Stelle (Verbindungsstatus oben in
     der Kopfzeile) weiterhin Rot bleibt. */
  --off: #5b6572;
  --off-bg: #e7e9ec;
  /* Getoenter Hintergrund fuer das Typ-Icon einer Geraetekarte - aus der
     Akzentfarbe abgeleitet, nicht aus --ok: das Icon zeigt "das ist ein
     Geraet", keinen Status. */
  --type-bg: #f4e6da;
  --type-fg: #a15a2c;
```

Im `@media (prefers-color-scheme: dark)`-Block (Zeile 45-61), ersetze:

```css
    --accent: #6fbf9a;
    --accent-contrast: #0c1210;
```

durch:

```css
    --accent: #e2915c;
    --accent-contrast: #2a1508;
```

und ergänze, nach `--warn-bg: #362a10;`:

```css
    --off: #98a3ad;
    --off-bg: #23282d;
    --type-bg: #2e2015;
    --type-fg: #e2915c;
```

- [ ] **Step 2: Neue Komponentenklassen anhängen**

Am Ende von `style.css`, nach `.heartbeat`, anhängen:

```css
/* Geraete-Dashboard-Entwurf (2026-09-03): Icons, Status-Pille und
   Wert-Chips fuer die immer offene Geraetekarte. */

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

/* Linker Farbstreifen an einer Geraetekarte - traegt dieselbe Bedeutung
   wie die Status-Pille daneben, bewusst redundant: faellt beim schnellen
   Scrollen ueber viele Geraete auf, ohne dass die Pille gelesen werden
   muss. Grundzustand (kein Modifikator) ist gruen = unauffaellig. */
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

- [ ] **Step 3: Manuell verifizieren**

Run: `uv run python scripts/dev_web_server.py`
Expected: Im Browser die Kopfzeile prüfen — der Reiter "Geräte" trägt jetzt einen kupferfarbenen Unterstrich statt eines grünen (`nav.tabs button.active { border-bottom-color: var(--accent) }`, unverändert, nur der Token-Wert hat sich geändert). Der "Neues Gerät einlernen"-Knopf ("Einlernen", `.primary`) ist jetzt kupferfarben statt grün. Keine sichtbaren Layoutbrüche. (Die neuen Klassen `.type-badge`/`.status-pill`/`.device-card`/`.value-chip` selbst sind erst ab Task 8 im Markup verwendet — hier nur prüfen, dass nichts Bestehendes bricht.)

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

## Task 6: Tab „Einstellungen"

**Files:**
- Modify: `src/loxmatter/web/index.html` (Icon-Symbole, Nav-Button, neue Sektion)
- Modify: `src/loxmatter/web/app.js` (Zustand, `loadSettings`/`saveSettings`, `selectView`)

**Interfaces:**
- Consumes: `GET`/`PATCH /api/settings` aus Task 2.
- Produces: `app().bridgeSettings: {bridge_ip, udp_port, listen_port, saved_at}` (nach dem Laden befüllt), `app().settingsDraft: {bridge_ip, udp_port, listen_port}` (Eingabefelder), `app().loadSettings()`, `app().saveSettings()` — von Task 7 (Export-Tab) und Task 9 (Export-Knopf an der Karte) gelesen.

- [ ] **Step 1: Icon-Symbole und den neuen Tab in `index.html` ergänzen**

Direkt nach `<body x-data="app()">` (Zeile 55) einfügen — einmalig definierte Icons, per `<use>` überall referenziert:

```html
  <body x-data="app()">
    <!-- Icon-Symbole (Geraete-Dashboard-Entwurf, Abschnitt 3) - inline SVG
         statt einer Icon-Bibliothek: kein Netzwerkverweis, dieselbe
         Begruendung wie beim vendorten Alpine.js oben im Kopfkommentar. -->
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

In `nav.tabs` (Zeile 142-147), nach dem "System"-Button einen fünften Button ergänzen:

```html
    <nav class="tabs">
      <button :class="{ active: view === 'devices' }" @click="selectView('devices')">Geräte</button>
      <button :class="{ active: view === 'signals' }" @click="selectView('signals')">Signale</button>
      <button :class="{ active: view === 'export' }" @click="selectView('export')">Export</button>
      <button :class="{ active: view === 'system' }" @click="selectView('system')">System</button>
      <button :class="{ active: view === 'settings' }" @click="selectView('settings')">Einstellungen</button>
    </nav>
```

Nach der System-Sektion (nach `</section>` in Zeile 587, vor `</main>` in Zeile 588) die neue Sektion einfügen:

```html
      <!-- ================================================================
           Ansicht 5: Einstellungen
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

- [ ] **Step 2: Zustand und Methoden in `app.js` ergänzen**

In der `--- Export ---`-Zustandsgruppe (Zeile 248-257), die drei Felder `exportBridgeIp`/`exportPort`/`exportListenPort` entfernen (sie werden in Task 7 durch `bridgeSettings` ersetzt) und eine neue Gruppe direkt davor einfügen:

```js
    // --- Einstellungen ---------------------------------------------------
    // `bridgeSettings` ist der zuletzt vom Server geladene Stand (auch von
    // Task 7 und Task 9 gelesen); `settingsDraft` sind die drei Eingabefelder
    // auf diesem Tab, erst nach "Speichern" uebernommen.
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

In `startApp()` (Zeile 375-409), `this.settingsError = null;` bei den übrigen Reset-Zeilen ergänzen (nach `this.signalsError = null;`), und `this.loadSettings()` in das bestehende `Promise.all` am Ende der Methode aufnehmen — dieser Schritt wird zusammen mit Task 8 fertiggestellt (dort wird `startApp()` insgesamt neu geschrieben, siehe Task 8 Step 4); für diesen Task genügt ein eigenständiger Aufruf direkt nach `await this.loadDevices();`:

```js
      await this.loadDevices();
      await this.loadSettings();
```

In `selectView(view)` (Zeile 483-500), einen weiteren `else if`-Zweig ergänzen:

```js
      } else if (view === "system") {
        await this.loadSystem();
      } else if (view === "settings") {
        await this.loadSettings();
      }
```

Im Abschnitt „Export" (nach `loadExportStatus`, vor `previewExport`, ca. Zeile 908) `loadSettings`/`saveSettings` einfügen — eigener Abschnittskommentar:

```js
    // ---------------------------------------------------------------------
    // Einstellungen
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

- [ ] **Step 3: Manuell verifizieren**

Run: `uv run python scripts/dev_web_server.py`
Expected: Im Browser erscheint ein fünfter Reiter "Einstellungen". Dort IP `192.168.1.20`, UDP-Port `7000`, HTTP-Port `8080` eintragen, "Speichern" klicken → Kurzmeldung "Einstellungen gespeichert.", Hinweis "Zuletzt gespeichert: …" erscheint. Seite neu laden (F5) → derselbe Tab zeigt weiterhin `192.168.1.20` (serverseitig gespeichert, kein Verlust beim Neuladen).

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

## Task 7: Export-Tab wird schreibgeschützt

**Files:**
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/app.js`

**Interfaces:**
- Consumes: `app().bridgeSettings` aus Task 6.
- Produces: `previewExport()`/`downloadUrl()`/`downloadExport()` lesen `bridgeSettings` statt der entfernten `exportBridgeIp`/`exportPort`/`exportListenPort`-Felder — dasselbe Verhalten wie zuvor, nur mit der neuen Quelle.

- [ ] **Step 1: Eingabefelder in `index.html` schreibgeschützt machen**

Im Export-Abschnitt (Zeile 434-481), ersetze den ersten `<div class="row">` (die drei Eingabefelder) und den folgenden Hinweistext:

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

(Der Rest der Sektion — Vorschau-Tabelle ab `<div class="card" x-show="exportPreview" x-cloak>` — bleibt unverändert.)

- [ ] **Step 2: `app.js` auf `bridgeSettings` umstellen**

`previewExport()` (Zeile 914-933) wird zu:

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

`downloadUrl()` (Zeile 956-965) wird zu:

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

`downloadExport()` (Zeile 980-999) wird zu:

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

- [ ] **Step 3: Manuell verifizieren**

Run: `uv run python scripts/dev_web_server.py`
Expected: Tab "Export" zeigt die drei Felder ausgegraut/schreibgeschützt mit dem zuletzt in "Einstellungen" gespeicherten Wert (zuerst dort `192.168.1.20`/`7000`/`8080` speichern, siehe Task 6 Step 3). Klick auf den Link "Einstellungen → Verbindung zum Miniserver" wechselt den Tab. "Vorschau ansehen" und "ZIP herunterladen" funktionieren weiterhin (Vorschau-Tabelle erscheint, ZIP lädt herunter). Ohne zuvor gespeicherte Einstellungen (frische Datenbank, `--store-path` auf eine neue Datei) zeigt ein Klick auf "Vorschau ansehen" die Fehlermeldung "Bitte zuerst in Einstellungen …" statt eines Server-422.

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

## Task 8: Geräte-Kachel — immer offen, Icon, Status-Streifen

**Files:**
- Modify: `src/loxmatter/web/index.html` (Geräte-Sektion komplett ersetzt)
- Modify: `src/loxmatter/web/app.js` (`startApp`, `commissionDevice`, `removeDevice`, neue Helfer, `toggleExpanded`/`expandedDeviceId` entfernt)

**Interfaces:**
- Consumes: `app().bridgeSettings` (Task 6/7, hier nur mitgelesen, Export-Knopf selbst kommt in Task 9), `GET /api/export/status` (bereits vorhanden).
- Produces: `app().changedSinceExport(deviceId): bool`, `app().exportHintFor(deviceId): string`, `app().deviceCardClass(device): {is-changed, is-offline}` — von der neuen Kachel in `index.html` gelesen. Entfernt: `app().expandedDeviceId`, `app().toggleExpanded`.

- [ ] **Step 1: `startApp()` neu schreiben — alle Karten laden sofort**

Ersetze in `src/loxmatter/web/app.js` die Methode `startApp()` (Zeile 375-409):

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
      // Jede Karte zeigt Werte und Bedienelemente sofort, ohne Klick
      // (Geraete-Dashboard-Entwurf Abschnitt 3) - deshalb laedt startApp()
      // beides fuer JEDES Geraet, nicht erst fuer eines nach einem
      // Aufklappen (das es seit diesem Entwurf nicht mehr gibt).
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

(Das ersetzt zugleich den in Task 6 Step 2 eingefügten eigenständigen `await this.loadSettings();`-Aufruf — er ist jetzt Teil des `Promise.all`.)

- [ ] **Step 2: `expandedDeviceId`/`toggleExpanded` entfernen**

In der `--- Geraete ---`-Zustandsgruppe (Zeile 204-224), die Zeile `expandedDeviceId: null,` entfernen.

Die Methode `toggleExpanded` (Zeile 538-546) vollständig entfernen.

In `removeDevice` (Zeile 693-716), die drei Zeilen

```js
        if (this.expandedDeviceId === device.id) {
          this.expandedDeviceId = null;
        }
```

entfernen.

- [ ] **Step 3: Neue Helfer ergänzen**

Nach `exportedAtFor` (Zeile 591-594) einfügen:

```js
    // Wie `ExportStatusOut.changed_since_export` server-seitig: ohne
    // geladenen Status (z. B. ein gerade erst eingelerntes Geraet, bevor
    // die naechste `loadExportStatus`-Runde durch ist) gilt "geaendert" -
    // dieselbe vorsichtige Annahme wie beim Server (siehe api/export.py,
    // `_changed_since_export`).
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

    // Klassen fuer den Farbstreifen der Kachel (style.css, `.device-card`) -
    // eine Funktion statt eines Inline-Ausdrucks in index.html, weil zwei
    // Bedingungen (online UND geaendert) hier zusammenkommen.
    deviceCardClass(device) {
      return {
        "is-offline": !this.isOnline(device),
        "is-changed": this.isOnline(device) && this.changedSinceExport(device.id),
      };
    },

```

- [ ] **Step 4: `commissionDevice()` lädt Werte/Bedienelemente für das neue Gerät sofort**

In `commissionDevice()` (Zeile 791-829), nach `this.devices.push(device);` ergänzen:

```js
        const device = await this.request("POST", "/api/devices/commission", body);
        this.devices.push(device);
        // Karte ist ab sofort sichtbar und immer offen (Abschnitt 3) - ohne
        // dieses Nachladen zeigte sie "Signale werden geladen…" dauerhaft,
        // bis irgendwann die Ansicht neu betreten wuerde.
        await Promise.all([this.loadControls(device.id), this.loadSignals(device.id)]);
```

- [ ] **Step 5: Geräte-Sektion in `index.html` ersetzen**

Ersetze in `src/loxmatter/web/index.html` den kompletten Block von `<template x-for="device in devices" :key="device.id">` bis zum zugehörigen `</template>` (Zeile 191-293):

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

(Der Export-Knopf in der letzten Zeile fehlt hier bewusst — er kommt in Task 9, zusammen mit der Methode, die ihn auslöst. Ohne ihn zeigt die Fußzeile für diesen Task nur den Export-Hinweistext.)

- [ ] **Step 6: Manuell verifizieren**

Run: `uv run python scripts/dev_web_server.py`
Expected: Tab "Geräte" zeigt beide Karten sofort mit Werten ("Zustand: Ein", "Leistung: 12,4 W" bei der Steckdose — dank der in Task 4 geseedeten Werte) und Bedienelementen, ohne einen Klick auf "Details" (den Button gibt es nicht mehr). Steckdose zeigt eine amber "Geändert seit Export"-Pille (noch nie exportiert = `changed_since_export: true`) und einen amber Rand-Streifen. Namen umbenennen funktioniert weiterhin (Eingabefeld, Enter/Fokusverlust). "Entfernen" funktioniert weiterhin (Sicherheitsabfrage, Karte verschwindet).

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

## Task 9: Export-Knopf an der Geräte-Kachel

**Files:**
- Modify: `src/loxmatter/web/index.html`
- Modify: `src/loxmatter/web/app.js`

**Interfaces:**
- Consumes: `GET /api/export/download?device_id=…` (Task 3), `app().bridgeSettings` (Task 6).
- Produces: `app().exportDevice(device)` — löst den Download für genau ein Gerät aus.

- [ ] **Step 1: `exportDevice` in `app.js` ergänzen**

Im Abschnitt „Export", direkt nach `downloadExport()` (nach dem in Task 7 Step 2 gezeigten Ende der Methode), einfügen:

```js

    // Export-Knopf an einer einzelnen Geraetekarte (Geraete-Dashboard-
    // Entwurf, Abschnitt 6) - kein Vorschauschritt: die Werte stehen ja
    // bereits offen auf der Karte, eine zusaetzliche Vorschau waere
    // doppelte Information.
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

- [ ] **Step 2: Knopf in `index.html` ergänzen**

Die Fußzeile der Geräte-Kachel (aus Task 8 Step 5, letzter `<div class="device-controls row">`) wird zu:

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

- [ ] **Step 3: Manuell verifizieren**

Run: `uv run python scripts/dev_web_server.py`

Zuerst in "Einstellungen" IP `192.168.1.20`/Ports `7000`/`8080` speichern (falls noch nicht geschehen). Dann im Tab "Geräte":

Expected: Jede Karte zeigt einen "Exportieren"-Knopf in der Fußzeile. Klick bei "Steckdose Wohnzimmer" → Browser lädt eine Datei `loxmatter-d<id>-export.zip` herunter, Kurzmeldung "Steckdose Wohnzimmer wurde exportiert." erscheint, die amber "Geändert seit Export"-Pille verschwindet (Status neu geladen, Gerät gilt jetzt als exportiert). Die ZIP-Datei entpacken und prüfen: enthält nur `VIU_d<id>_….xml` und `VO_d<id>_….xml` dieses einen Geräts, nicht die des Tasters.

Ohne gespeicherte Einstellungen (neue Datenbank via `--store-path` auf eine neue Datei) ist der "Exportieren"-Knopf ausgegraut, mit Tooltip "Erst in Einstellungen → Verbindung zum Miniserver hinterlegen".

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

## Nach der Umsetzung

- `uv run pytest` (komplette Suite) sollte grün sein — insbesondere `tests/api/test_security.py` (jeder Router hinter `api_guard`) und `tests/api/test_web.py` (statische Auslieferung von `index.html`/`app.js`/`style.css` unverändert erreichbar).
- Manuelle Gesamtprobe mit `uv run python scripts/dev_web_server.py`: Geräteliste → sofort Werte sichtbar, Export-Knopf pro Gerät → korrektes Einzel-ZIP, Einstellungen → übersteht Neuladen, Export-Tab → zeigt dieselben Werte schreibgeschützt und exportiert weiterhin alle/ausstehenden Geräte.
