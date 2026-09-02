"""Tests fuer die Token-Absicherung der `/api`-Routen (Task 8, Phase 5, Spec 9).

Die Kernfrage dieser Datei: schuetzt `build_api_guard` genau das, was Spec 9
verlangt - jede Route unter `/api`, einschliesslich der WebSocket-Route
`/api/live` und `GET /api/diagnostics/fabric-backup` - und laesst dabei
`/cmd` und `/resync` unveraendert offen, weil der Miniserver keinen Header
mitschicken kann?

Drei Gruppen:

- `test_guard_*` - `build_api_guard` selbst, ganz ohne FastAPI-App: die
  reine Entscheidungslogik (kein Token konfiguriert -> immer durch; Token
  konfiguriert -> nur der exakt passende `Authorization`-Header kommt
  durch).
- `test_*` mit `secured_client`/`open_client` - dieselbe Aufgabe wie oben,
  aber durch die tatsaechliche ASGI-App hindurch: jede der fuenf `/api`-
  Router UND `/cmd`/`/resync` einzeln angefragt, damit ein Router, der aus
  Versehen ohne `dependencies=api_guard` eingebunden wuerde, hier auffiele
  statt sich auf den Router-Praefix zu verlassen.
- `test_websocket_*` - `/api/live` ist keine gewoehnliche Route: die
  Ablehnung passiert VOR `websocket.accept()`, ueber die ASGI-„Denial
  Response"-Erweiterung (siehe `_websocket_handshake_status` unten und
  `build_api_guard`s Docstring in `loxone/server.py`).
- `test_warn_if_missing_api_token_*` - die Warnung aus `cli.py`, die einen
  Betrieb ohne Token trotzdem sichtbar machen soll.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from conftest import load_snapshot
from fastapi import HTTPException

from loxmatter.cli import _warn_if_missing_api_token
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_api_guard, build_app
from loxmatter.model.store import Store


class FakeSender:
    """Wie in tests/loxone/test_server.py - genuegt fuer `Runtime`, ohne
    einen echten UDP-Socket zu oeffnen. Gebraucht hier (statt der
    einfacheren `FakeRuntime` aus conftest.py), weil `/resync` echte
    `Runtime.resend_all()`-Unterstuetzung braucht, `FakeRuntime` das aber
    nicht implementiert."""

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        return True

    async def close(self) -> None:
        return None


def _matter_data_dir(tmp_path: Path) -> Path:
    """Wie in tests/api/test_diagnostics.py - ein Verzeichnis mit einer
    harmlosen Testdatei, steht fuer das matter-server-Datenverzeichnis,
    ohne echtes Schluesselmaterial zu beruehren."""
    directory = tmp_path / "matter-data"
    directory.mkdir()
    (directory / "credentials.json").write_text('{"fixture": "keine echten Schluessel"}')
    return directory


async def _build_client(
    tmp_path: Path, no_invoke: Any, *, api_token: str | None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, int]]:
    """Baut Store, eine ECHTE `Runtime` (fuer `/resync`) und die App mit dem
    gegebenen `api_token` - gemeinsamer Aufbau fuer `secured_client` und
    `open_client` unten, die sich nur in `api_token` unterscheiden."""
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
        yield client, app, device_id
    store.close()


@pytest.fixture
async def secured_client(tmp_path, no_invoke):
    """Die App mit gesetztem Token `"secret"` - fuer jeden Test, der
    pruefen will, dass der Waechter tatsaechlich greift. `client=None`
    (kein Matter-Client): Einlernen/Entfernen sind nicht Gegenstand dieser
    Datei, die beiden Routen dafuer antworten unveraendert mit 503 (siehe
    server.py-Moduldocstring)."""
    async for item in _build_client(tmp_path, no_invoke, api_token="secret"):
        yield item


@pytest.fixture
async def open_client(tmp_path, no_invoke):
    """Dieselbe App, aber ohne Token - der Zustand vor Task 8 bzw. eine
    Installation, die (noch) keins gesetzt hat."""
    async for item in _build_client(tmp_path, no_invoke, api_token=None):
        yield item


# ---------------------------------------------------------------------------
# build_api_guard selbst - ohne FastAPI-App, reine Entscheidungslogik.
# ---------------------------------------------------------------------------


async def test_guard_lets_everything_through_when_no_token_is_configured():
    guard = build_api_guard(None)
    await guard(authorization=None)  # wirft nicht
    await guard(authorization="Bearer irgendwas")  # wirft auch dann nicht


async def test_guard_rejects_a_missing_header_when_a_token_is_configured():
    guard = build_api_guard("secret")
    with pytest.raises(HTTPException) as excinfo:
        await guard(authorization=None)
    assert excinfo.value.status_code == 401


async def test_guard_rejects_a_wrong_token():
    guard = build_api_guard("secret")
    with pytest.raises(HTTPException) as excinfo:
        await guard(authorization="Bearer falsch")
    assert excinfo.value.status_code == 401


async def test_guard_accepts_the_exact_bearer_token():
    guard = build_api_guard("secret")
    await guard(authorization="Bearer secret")  # wirft nicht


# ---------------------------------------------------------------------------
# Ohne Token: /api ist offen (Zustand vor Task 8, unveraendertes Verhalten).
# ---------------------------------------------------------------------------


async def test_without_token_devices_route_is_open(open_client):
    client, _, _ = open_client
    response = await client.get("/api/devices")
    assert response.status_code == 200


async def test_without_token_export_status_is_open(open_client):
    client, _, _ = open_client
    response = await client.get("/api/export/status")
    assert response.status_code == 200


async def test_without_token_controls_route_is_open(open_client):
    client, _, device_id = open_client
    response = await client.get(f"/api/devices/{device_id}/controls")
    assert response.status_code == 200


async def test_without_token_diagnostics_commands_is_open(open_client):
    client, _, _ = open_client
    response = await client.get("/api/diagnostics/commands")
    assert response.status_code == 200


async def test_without_token_fabric_backup_is_open(open_client):
    client, _, _ = open_client
    response = await client.get("/api/diagnostics/fabric-backup")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Mit Token: jeder der fuenf /api-Router verlangt ihn einzeln - nicht nur
# "irgendeine" Route, jede. Ein Router, der versehentlich ohne
# dependencies=api_guard eingebunden wuerde, faellt hier auf, statt sich
# darauf zu verlassen, dass der Praefix /api schon irgendwie schuetzt.
# ---------------------------------------------------------------------------


async def test_with_token_devices_route_needs_the_header(secured_client):
    client, _, _ = secured_client
    without_header = await client.get("/api/devices")
    assert without_header.status_code == 401

    with_header = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
    assert with_header.status_code == 200


async def test_with_token_export_router_needs_the_header(secured_client):
    client, _, _ = secured_client
    without_header = await client.get("/api/export/status")
    assert without_header.status_code == 401

    with_header = await client.get("/api/export/status", headers={"Authorization": "Bearer secret"})
    assert with_header.status_code == 200


async def test_with_token_control_router_needs_the_header(secured_client):
    client, _, device_id = secured_client
    without_header = await client.get(f"/api/devices/{device_id}/controls")
    assert without_header.status_code == 401

    with_header = await client.get(
        f"/api/devices/{device_id}/controls", headers={"Authorization": "Bearer secret"}
    )
    assert with_header.status_code == 200


async def test_with_token_diagnostics_router_needs_the_header(secured_client):
    client, _, _ = secured_client
    without_header = await client.get("/api/diagnostics/commands")
    assert without_header.status_code == 401

    with_header = await client.get(
        "/api/diagnostics/commands", headers={"Authorization": "Bearer secret"}
    )
    assert with_header.status_code == 200


async def test_with_wrong_token_is_rejected_too(secured_client):
    client, _, _ = secured_client
    response = await client.get("/api/devices", headers={"Authorization": "Bearer falsch"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Die Fabric-Sicherung: der eigentliche Grund fuer diese Phase (siehe
# Spec 4.1). Ein eigener Test, nicht nur einer von vielen /api-Routen, weil
# genau diese Route der Anlass fuer Task 8 ist.
# ---------------------------------------------------------------------------


async def test_fabric_backup_is_401_without_a_header_even_with_a_configured_directory(
    secured_client,
):
    """`matter_data_dir` ist gesetzt (siehe `_build_client`) - ohne Token
    waere die Route also tatsaechlich in der Lage, echte Daten
    auszuliefern. Genau das darf ohne Header nicht passieren."""
    client, _, _ = secured_client
    response = await client.get("/api/diagnostics/fabric-backup")
    assert response.status_code == 401


async def test_fabric_backup_is_reachable_with_the_correct_header(secured_client):
    client, _, _ = secured_client
    response = await client.get(
        "/api/diagnostics/fabric-backup", headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


# ---------------------------------------------------------------------------
# /cmd und /resync: der Miniserver-Pfad. Muss UNVERAENDERT offen bleiben,
# auch wenn ein Token konfiguriert ist - der Miniserver kann keinen Header
# mitschicken (siehe build_api_guard-Docstring, loxone/server.py).
# ---------------------------------------------------------------------------


async def test_with_token_cmd_route_stays_open(secured_client):
    client, _, device_id = secured_client
    response = await client.get(f"/cmd/d{device_id}_1_on/1")
    assert response.status_code == 200


async def test_with_token_resync_route_stays_open(secured_client):
    client, _, _ = secured_client
    response = await client.get("/resync")
    assert response.status_code == 200


async def test_with_token_health_route_stays_open(secured_client):
    """`/health` liegt wie `/cmd`/`/resync` ausserhalb von `/api` - kein
    Diagnose-Endpunkt, der Bestandsdaten preisgibt, muss also ebenfalls
    unabhaengig vom Token erreichbar bleiben."""
    client, _, _ = secured_client
    response = await client.get("/health")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /api/live: keine gewoehnliche Route. Die Ablehnung passiert VOR dem
# WebSocket-Handshake (ASGI-„Denial Response"), nicht durch ein Annehmen
# und anschliessendes Schliessen - deshalb ein eigener, roher ASGI-Aufruf
# statt httpx2, das keinen abgelehnten Handshake abbilden kann.
# ---------------------------------------------------------------------------


async def _websocket_handshake_status(
    app: Any, path: str, headers: list[tuple[bytes, bytes]]
) -> int | None:
    """Fuehrt nur den WebSocket-Handshake gegen `app` aus und meldet
    zurueck, ob der Server ihn angenommen hat (`None`) oder ueber die
    ASGI-„Denial Response"-Erweiterung abgelehnt hat (der Statuscode).

    Kein Text-/JSON-Versand, kein Ping/Pong: mehr als den Handshake selbst
    braucht kein Test dieser Datei - Live-Werte NACH einem Accept prueft
    bereits tests/api/test_live.py."""
    to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    from_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def receive() -> dict[str, Any]:
        return await to_app.get()

    async def send(message: dict[str, Any]) -> None:
        await from_app.put(message)

    scope: dict[str, Any] = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("testclient", 123),
        "server": ("testserver", 80),
        "subprotocols": [],
        "state": {},
        "extensions": {"websocket.http.response": {}},
    }
    task = asyncio.create_task(app(scope, receive, send))
    await to_app.put({"type": "websocket.connect"})
    message = await from_app.get()

    if message["type"] == "websocket.accept":
        await to_app.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, timeout=2)
        return None

    assert message["type"] == "websocket.http.response.start", message
    status = message["status"]
    await from_app.get()  # websocket.http.response.body - Rumpf abholen, Task sauber beenden
    await asyncio.wait_for(task, timeout=2)
    return status


async def test_websocket_live_is_rejected_with_401_without_a_header(secured_client):
    _, app, _ = secured_client
    status = await _websocket_handshake_status(app, "/api/live", headers=[])
    assert status == 401


async def test_websocket_live_is_accepted_with_the_correct_header(secured_client):
    _, app, _ = secured_client
    status = await _websocket_handshake_status(
        app, "/api/live", headers=[(b"authorization", b"Bearer secret")]
    )
    assert status is None


async def test_websocket_live_is_accepted_without_a_header_when_no_token_is_configured(
    open_client,
):
    _, app, _ = open_client
    status = await _websocket_handshake_status(app, "/api/live", headers=[])
    assert status is None


# ---------------------------------------------------------------------------
# Die Warnung im Log (cli.py) - sichtbar fuer einen Betrieb ohne Token.
# ---------------------------------------------------------------------------


def test_warn_if_missing_api_token_logs_a_clear_warning(caplog):
    with caplog.at_level(logging.WARNING):
        _warn_if_missing_api_token(None)
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert "Kein API-Token gesetzt" in caplog.records[0].message
    assert "LOXMATTER_API_TOKEN" in caplog.records[0].message


def test_warn_if_missing_api_token_stays_silent_when_a_token_is_set(caplog):
    with caplog.at_level(logging.WARNING):
        _warn_if_missing_api_token("secret")
    assert caplog.records == []
