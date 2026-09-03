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
  `build_api_guard`s Docstring in `loxone/server.py`). Seit Review-Fix
  Fix 1c (2026-09-03) kommt hier der zweite Uebertragungsweg dazu: ein
  Browser-`WebSocket` kann keinen `Authorization`-Header setzen und schickt
  das Token deshalb als Subprotokoll `bearer, <Token>` mit.
- `test_normalize_api_token_*` / `test_whitespace_*` - ein Token aus reinem
  Leerraum ist kein Token (Review-Fix Fix 2, 2026-09-03).
- `test_fabric_backup_without_a_token_*` - ohne konfiguriertes Token wird die
  Fabric-Sicherung gar nicht erst ausgeliefert (Review-Fix Fix 3,
  2026-09-03), waehrend jede andere `/api`-Route offen bleibt.
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

from loxmatter.auth.passwords import hash_password
from loxmatter.cli import _warn_if_missing_api_token
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_api_guard, build_app, normalize_api_token
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


@pytest.fixture
def guard_store(tmp_path):
    """Ein leerer Store fuer die Tests, die `build_api_guard` direkt aufrufen -
    ohne Passwort und ohne Sitzung, damit dort weiterhin allein das Token
    ueber Durchlassen oder Ablehnen entscheidet."""
    store = Store(tmp_path / "guard.sqlite")
    yield store
    store.close()


class _FakeConnection:
    """Genuegt `guard` als `conn`-Argument in den `test_guard_*`-Tests unten -
    die pruefen ausschliesslich die Token-Logik und brauchen dafuer nur ein
    Objekt mit `.cookies`, keine echte `HTTPConnection` aus einer laufenden
    App (die gibt es hier, anders als bei `secured_client`/`open_client`,
    nicht)."""

    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}


async def _call_guard(
    guard: Any, *, authorization: str | None = None, subprotocol: str | None = None
) -> None:
    """Ruft den Waechter direkt auf - mit BEIDEN Headerparametern, immer.

    Ein weggelassener Parameter bekaeme sonst FastAPIs `Header(...)`-Objekt
    als Wert (der Default der Signatur), nicht `None`: ausserhalb einer
    laufenden App loest niemand die Abhaengigkeit auf. Dieser Helfer haelt
    diese Falle an genau einer Stelle statt in jedem Test."""
    await guard(
        _FakeConnection(), authorization=authorization, sec_websocket_protocol=subprotocol
    )


async def test_guard_lets_everything_through_when_no_token_is_configured(guard_store):
    guard = build_api_guard(None, guard_store)
    await _call_guard(guard)  # wirft nicht
    await _call_guard(guard, authorization="Bearer irgendwas")  # wirft auch dann nicht


async def test_guard_rejects_a_missing_header_when_a_token_is_configured(guard_store):
    guard = build_api_guard("secret", guard_store)
    with pytest.raises(HTTPException) as excinfo:
        await _call_guard(guard)
    assert excinfo.value.status_code == 401


async def test_guard_rejects_a_wrong_token(guard_store):
    guard = build_api_guard("secret", guard_store)
    with pytest.raises(HTTPException) as excinfo:
        await _call_guard(guard, authorization="Bearer falsch")
    assert excinfo.value.status_code == 401


async def test_guard_accepts_the_exact_bearer_token(guard_store):
    guard = build_api_guard("secret", guard_store)
    await _call_guard(guard, authorization="Bearer secret")  # wirft nicht


async def test_guard_rejects_a_non_ascii_token_with_401_not_a_crash(guard_store):
    """`secrets.compare_digest` wirft bei `str`-Argumenten `TypeError`, sobald
    eines davon Nicht-ASCII enthaelt (Review-Fix Fix 2). Ein Angreifer koennte
    damit sonst mit einem einzigen Umlaut im Header einen 500er statt eines
    401 ausloesen - der Waechter vergleicht deshalb UTF-8-Bytes."""
    guard = build_api_guard("secret", guard_store)
    with pytest.raises(HTTPException) as excinfo:
        await _call_guard(guard, authorization="Bearer gehe\u00dfimnis")
    assert excinfo.value.status_code == 401


async def test_guard_accepts_a_non_ascii_token_that_actually_matches(guard_store):
    """Die Kehrseite des Tests darueber: ein Nicht-ASCII-Token wird nicht
    pauschal abgelehnt, es wird nur nicht mehr zum Absturz."""
    guard = build_api_guard("gehe\u00dfimnis", guard_store)
    await _call_guard(guard, authorization="Bearer gehe\u00dfimnis")  # wirft nicht


# ---------------------------------------------------------------------------
# Der zweite Uebertragungsweg: Sec-WebSocket-Protocol (Review-Fix Fix 1c).
# Ein Browser-`WebSocket` kann keinen `Authorization`-Header setzen.
# ---------------------------------------------------------------------------


async def test_guard_accepts_the_token_from_the_websocket_subprotocol(guard_store):
    guard = build_api_guard("secret", guard_store)
    await _call_guard(guard, subprotocol="bearer, secret")  # wirft nicht


async def test_guard_rejects_a_wrong_token_in_the_websocket_subprotocol(guard_store):
    guard = build_api_guard("secret", guard_store)
    with pytest.raises(HTTPException) as excinfo:
        await _call_guard(guard, subprotocol="bearer, falsch")
    assert excinfo.value.status_code == 401


async def test_guard_rejects_a_subprotocol_without_the_bearer_marker(guard_store):
    """Nur die Form `bearer, <Token>` gilt - ein einzelner Wert ist kein
    Token, auch wenn er zufaellig dem Geheimnis gleicht."""
    guard = build_api_guard("secret", guard_store)
    with pytest.raises(HTTPException) as excinfo:
        await _call_guard(guard, subprotocol="secret")
    assert excinfo.value.status_code == 401


async def test_guard_rejects_a_subprotocol_with_more_than_two_values(guard_store):
    guard = build_api_guard("secret", guard_store)
    with pytest.raises(HTTPException) as excinfo:
        await _call_guard(guard, subprotocol="bearer, secret, extra")
    assert excinfo.value.status_code == 401


async def test_an_authorization_header_still_wins_over_a_wrong_subprotocol(guard_store):
    """Der `Authorization`-Header bleibt der Hauptweg: ist er korrekt, kommt
    der Aufruf durch, gleich was im Subprotokoll steht."""
    guard = build_api_guard("secret", guard_store)
    await _call_guard(guard, authorization="Bearer secret", subprotocol="bearer, falsch")


# ---------------------------------------------------------------------------
# Ein Token aus reinem Leerraum ist kein Token (Review-Fix Fix 2).
# ---------------------------------------------------------------------------


def test_normalize_api_token_treats_whitespace_only_as_no_token():
    assert normalize_api_token(None) is None
    assert normalize_api_token("") is None
    assert normalize_api_token("   ") is None
    assert normalize_api_token("\n") is None


def test_normalize_api_token_strips_the_outer_whitespace_of_a_real_token():
    """Ein `LOXMATTER_API_TOKEN` mit angehaengtem Zeilenumbruch soll das
    Geheimnis ohne den Zeilenumbruch sein - ein Geheimnis, das sich nicht in
    einem HTTP-Header uebertragen laesst, waere keins."""
    assert normalize_api_token("  secret\n") == "secret"


async def test_a_whitespace_only_token_leaves_the_api_open(guard_store):
    """Der gemeldete Fehler: der Waechter hielt Leerraum fuer ein echtes
    Geheimnis und sperrte den Dienst dauerhaft - ohne dass die Startwarnung
    darauf hingewiesen haette. Offen MIT Warnung ist besser als gesperrt
    ohne jede Diagnose."""
    guard = build_api_guard("   ", guard_store)
    await _call_guard(guard)  # wirft nicht


def test_a_whitespace_only_token_triggers_the_startup_warning(caplog):
    """Waechter und Warnung duerfen nicht auseinanderlaufen - beide fragen
    `normalize_api_token`."""
    with caplog.at_level(logging.WARNING):
        _warn_if_missing_api_token("   ")
    assert len(caplog.records) == 1
    assert "Kein API-Token gesetzt" in caplog.records[0].message


async def test_a_token_with_a_trailing_newline_is_usable_over_http(tmp_path, no_invoke):
    """Der Fall aus der kopierten `.env`: das Token traegt einen
    Zeilenumbruch, der Browser kann ihn nicht mitschicken. Nach der
    Normalisierung passt das abgeschnittene Geheimnis."""
    async for client, _, _, _ in _build_client(tmp_path, no_invoke, api_token="secret\n"):
        response = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Ohne Token: /api ist offen (Zustand vor Task 8, unveraendertes Verhalten).
# ---------------------------------------------------------------------------


async def test_without_token_devices_route_is_open(open_client):
    client, _, _, _ = open_client
    response = await client.get("/api/devices")
    assert response.status_code == 200


async def test_without_token_export_status_is_open(open_client):
    client, _, _, _ = open_client
    response = await client.get("/api/export/status")
    assert response.status_code == 200


async def test_without_token_controls_route_is_open(open_client):
    client, _, device_id, _ = open_client
    response = await client.get(f"/api/devices/{device_id}/controls")
    assert response.status_code == 200


async def test_without_token_diagnostics_commands_is_open(open_client):
    client, _, _, _ = open_client
    response = await client.get("/api/diagnostics/commands")
    assert response.status_code == 200


async def test_fabric_backup_without_a_token_is_refused_with_403(open_client):
    """Die einzige Ausnahme von "ohne Token bleibt `/api` offen" (Review-Fix
    Fix 3, 2026-09-03): `matter_data_dir` ist in dieser Fixture gesetzt, die
    Route KOENNTE also echte Fabric-Zugangsdaten ausliefern. Genau das darf
    ohne konfiguriertes Token nicht passieren - 403, weil eine Wiederholung
    mit Zugangsdaten nicht helfen kann (es gibt keine)."""
    client, _, _, _ = open_client
    response = await client.get("/api/diagnostics/fabric-backup")
    assert response.status_code == 403


async def test_fabric_backup_without_a_token_returns_no_data(open_client):
    """Nicht nur ein anderer Statuscode - kein ZIP, keine Datei, nichts."""
    client, _, _, _ = open_client
    response = await client.get("/api/diagnostics/fabric-backup")
    assert response.headers["content-type"].startswith("application/json")
    assert "content-disposition" not in response.headers
    assert "LOXMATTER_API_TOKEN" in response.json()["detail"]


async def test_fabric_backup_with_a_whitespace_only_token_is_refused_too(tmp_path, no_invoke):
    """Ein Leerraum-Token gilt als "kein Token" - auch hier, sonst haetten
    Waechter und Sicherung zwei verschiedene Vorstellungen davon, was
    "gesetzt" heisst."""
    async for client, _, _, _ in _build_client(tmp_path, no_invoke, api_token="  "):
        response = await client.get("/api/diagnostics/fabric-backup")
        assert response.status_code == 403


async def test_the_other_api_routes_stay_open_without_a_token(open_client):
    """Die Gegenprobe zu den drei Tests darueber: NUR die Fabric-Sicherung
    wird ohne Token verweigert, alles andere bleibt so offen wie vorher."""
    client, _, device_id, _ = open_client
    for path in (
        "/api/devices",
        "/api/export/status",
        f"/api/devices/{device_id}/controls",
        "/api/diagnostics/commands",
        "/api/diagnostics/system",
        "/api/diagnostics/datagrams",
    ):
        assert (await client.get(path)).status_code == 200, path


# ---------------------------------------------------------------------------
# Mit Token: jeder der fuenf /api-Router verlangt ihn einzeln - nicht nur
# "irgendeine" Route, jede. Ein Router, der versehentlich ohne
# dependencies=api_guard eingebunden wuerde, faellt hier auf, statt sich
# darauf zu verlassen, dass der Praefix /api schon irgendwie schuetzt.
# ---------------------------------------------------------------------------


async def test_with_token_devices_route_needs_the_header(secured_client):
    client, _, _, _ = secured_client
    without_header = await client.get("/api/devices")
    assert without_header.status_code == 401

    with_header = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
    assert with_header.status_code == 200


async def test_with_token_export_router_needs_the_header(secured_client):
    client, _, _, _ = secured_client
    without_header = await client.get("/api/export/status")
    assert without_header.status_code == 401

    with_header = await client.get("/api/export/status", headers={"Authorization": "Bearer secret"})
    assert with_header.status_code == 200


async def test_with_token_control_router_needs_the_header(secured_client):
    client, _, device_id, _ = secured_client
    without_header = await client.get(f"/api/devices/{device_id}/controls")
    assert without_header.status_code == 401

    with_header = await client.get(
        f"/api/devices/{device_id}/controls", headers={"Authorization": "Bearer secret"}
    )
    assert with_header.status_code == 200


async def test_with_token_diagnostics_router_needs_the_header(secured_client):
    client, _, _, _ = secured_client
    without_header = await client.get("/api/diagnostics/commands")
    assert without_header.status_code == 401

    with_header = await client.get(
        "/api/diagnostics/commands", headers={"Authorization": "Bearer secret"}
    )
    assert with_header.status_code == 200


async def test_with_wrong_token_is_rejected_too(secured_client):
    client, _, _, _ = secured_client
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
    client, _, _, _ = secured_client
    response = await client.get("/api/diagnostics/fabric-backup")
    assert response.status_code == 401


async def test_fabric_backup_is_reachable_with_the_correct_header(secured_client):
    client, _, _, _ = secured_client
    response = await client.get(
        "/api/diagnostics/fabric-backup", headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


# ---------------------------------------------------------------------------
# Das Sitzungs-Cookie: der zweite Nachweis neben dem Token (Task 6, Phase 6).
# Additiv - der Waechter laesst zusaetzlich Cookies durch, ohne dem Token
# etwas wegzunehmen.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# /cmd und /resync: der Miniserver-Pfad. Muss UNVERAENDERT offen bleiben,
# auch wenn ein Token konfiguriert ist - der Miniserver kann keinen Header
# mitschicken (siehe build_api_guard-Docstring, loxone/server.py).
# ---------------------------------------------------------------------------


async def test_with_token_cmd_route_stays_open(secured_client):
    client, _, device_id, _ = secured_client
    response = await client.get(f"/cmd/d{device_id}_1_on/1")
    assert response.status_code == 200


async def test_with_token_resync_route_stays_open(secured_client):
    client, _, _, _ = secured_client
    response = await client.get("/resync")
    assert response.status_code == 200


async def test_with_token_health_route_stays_open(secured_client):
    """`/health` liegt wie `/cmd`/`/resync` ausserhalb von `/api` - kein
    Diagnose-Endpunkt, der Bestandsdaten preisgibt, muss also ebenfalls
    unabhaengig vom Token erreichbar bleiben."""
    client, _, _, _ = secured_client
    response = await client.get("/health")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /api/live: keine gewoehnliche Route. Die Ablehnung passiert VOR dem
# WebSocket-Handshake (ASGI-„Denial Response"), nicht durch ein Annehmen
# und anschliessendes Schliessen - deshalb ein eigener, roher ASGI-Aufruf
# statt httpx2, das keinen abgelehnten Handshake abbilden kann.
# ---------------------------------------------------------------------------


async def _websocket_handshake(
    app: Any,
    path: str,
    headers: list[tuple[bytes, bytes]],
    subprotocols: list[str] | None = None,
) -> dict[str, Any]:
    """Fuehrt nur den WebSocket-Handshake gegen `app` aus und liefert die
    ERSTE Nachricht zurueck, die die App sendet - entweder ein
    `websocket.accept` (mit dem gewaehlten Subprotokoll darin) oder ein
    `websocket.http.response.start` der ASGI-„Denial Response"-Erweiterung.

    `subprotocols` fuellt das gleichnamige Scope-Feld, das ein echter Server
    aus dem `Sec-WebSocket-Protocol`-Header ableitet; der Header selbst muss
    zusaetzlich in `headers` stehen, weil der Waechter ihn dort liest (genau
    wie bei einem echten Browser-Handshake).

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
        "subprotocols": subprotocols or [],
        "state": {},
        "extensions": {"websocket.http.response": {}},
    }
    task = asyncio.create_task(app(scope, receive, send))
    await to_app.put({"type": "websocket.connect"})
    message = await from_app.get()

    if message["type"] == "websocket.accept":
        await to_app.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, timeout=2)
        return message

    assert message["type"] == "websocket.http.response.start", message
    await from_app.get()  # websocket.http.response.body - Rumpf abholen, Task sauber beenden
    await asyncio.wait_for(task, timeout=2)
    return message


async def _websocket_handshake_status(
    app: Any,
    path: str,
    headers: list[tuple[bytes, bytes]],
    subprotocols: list[str] | None = None,
) -> int | None:
    """Wie `_websocket_handshake`, aber nur die Frage "angenommen?" -
    `None` heisst angenommen, sonst der abweisende Statuscode."""
    message = await _websocket_handshake(app, path, headers, subprotocols)
    if message["type"] == "websocket.accept":
        return None
    status: int = message["status"]
    return status


def _bearer_subprotocol_handshake(
    token: str,
) -> tuple[list[tuple[bytes, bytes]], list[str]]:
    """Baut Header UND Scope-Feld so, wie ein Browser sie fuer
    `new WebSocket(url, ["bearer", token])` erzeugt - beides aus einer
    Quelle, damit die beiden nicht auseinanderlaufen koennen."""
    values = ["bearer", token]
    return [(b"sec-websocket-protocol", ", ".join(values).encode())], values


async def test_websocket_live_is_rejected_with_401_without_a_header(secured_client):
    _, app, _, _ = secured_client
    status = await _websocket_handshake_status(app, "/api/live", headers=[])
    assert status == 401


async def test_websocket_live_is_accepted_with_the_correct_header(secured_client):
    _, app, _, _ = secured_client
    status = await _websocket_handshake_status(
        app, "/api/live", headers=[(b"authorization", b"Bearer secret")]
    )
    assert status is None


async def test_websocket_live_is_accepted_without_a_header_when_no_token_is_configured(
    open_client,
):
    _, app, _, _ = open_client
    status = await _websocket_handshake_status(app, "/api/live", headers=[])
    assert status is None


async def test_websocket_live_is_accepted_with_the_token_in_the_subprotocol(secured_client):
    """Der Weg, den die Oberflaeche tatsaechlich geht: ein Browser kann bei
    `new WebSocket(...)` keinen `Authorization`-Header setzen (Review-Fix
    Fix 1c, 2026-09-03)."""
    _, app, _, _ = secured_client
    headers, subprotocols = _bearer_subprotocol_handshake("secret")
    status = await _websocket_handshake_status(app, "/api/live", headers, subprotocols)
    assert status is None


async def test_websocket_live_is_rejected_with_a_wrong_token_in_the_subprotocol(secured_client):
    _, app, _, _ = secured_client
    headers, subprotocols = _bearer_subprotocol_handshake("falsch")
    status = await _websocket_handshake_status(app, "/api/live", headers, subprotocols)
    assert status == 401


async def test_websocket_live_echoes_the_bearer_marker_never_the_token(secured_client):
    """RFC 6455: der Browser bricht den Handshake ab, wenn der Server das
    angebotene Subprotokoll nicht zurueckgibt. Zurueck darf aber
    ausschliesslich der Marker - das Token wuerde sonst in jedem Proxy- und
    Browser-Protokoll auf dem Weg landen."""
    _, app, _, _ = secured_client
    headers, subprotocols = _bearer_subprotocol_handshake("secret")
    message = await _websocket_handshake(app, "/api/live", headers, subprotocols)
    assert message["type"] == "websocket.accept"
    assert message["subprotocol"] == "bearer"


async def test_websocket_live_answers_without_a_subprotocol_when_none_was_offered(open_client):
    """Die Gegenprobe: ein Client ohne Subprotokoll-Angebot (kein Token
    gesetzt, oder ein Nicht-Browser-Client mit echtem `Authorization`-Header)
    darf keins zurueckbekommen - ein nicht angebotenes Subprotokoll ist nach
    RFC 6455 ebenso ein Handshake-Fehler."""
    _, app, _, _ = open_client
    message = await _websocket_handshake(app, "/api/live", headers=[])
    assert message["type"] == "websocket.accept"
    assert message["subprotocol"] is None


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
        app,
        "/api/live",
        headers=[(b"cookie", f"loxmatter_session={session_id}".encode())],
    )
    assert status is None, "Der Handshake wurde trotz gueltiger Sitzung abgelehnt"


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
