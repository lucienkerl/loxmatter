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

"""Tests fuer die Token-Absicherung der `/api`-Routen (Task 8, Phase 5, Spec 9).

Die Kernfrage dieser Datei: schuetzt `build_api_guard` genau das, was Spec 9
verlangt - jede Route unter `/api`, einschliesslich der WebSocket-Route
`/api/live` und `GET /api/diagnostics/fabric-backup` - und laesst dabei
`/cmd` und `/resync` unveraendert offen, weil der Miniserver keinen Header
mitschicken kann?

Sieben Gruppen:

- `test_guard_*` - `build_api_guard` selbst, ganz ohne FastAPI-App: die
  reine Entscheidungslogik (seit Task 8 gibt es keinen offenen Zustand mehr -
  ohne gueltige Sitzung entscheidet ausschliesslich der exakt passende
  `Authorization`-Header, und ganz ohne Token bleibt jede Anfrage ohne
  Sitzung abgelehnt).
- `test_*` mit `secured_client`/`open_client` - dieselbe Aufgabe wie oben,
  aber durch die tatsaechliche ASGI-App hindurch: jede der sechs `/api`-
  Router UND `/cmd`/`/resync` einzeln angefragt, damit ein Router, der aus
  Versehen ohne `dependencies=api_guard` eingebunden wuerde, hier auffiele
  statt sich auf den Router-Praefix zu verlassen.
- `test_websocket_*` - `/api/live` und, seit Task 4 dieser Phase,
  `/api/diagnostics/live` sind keine gewoehnlichen Routen: die Ablehnung
  passiert VOR `websocket.accept()`, ueber die ASGI-„Denial
  Response"-Erweiterung (siehe `_websocket_handshake_status` unten und
  `build_api_guard`s Docstring in `loxone/server.py`). Seit Review-Fix
  Fix 1c (2026-09-03) kommt hier der zweite Uebertragungsweg dazu: ein
  Browser-`WebSocket` kann keinen `Authorization`-Header setzen und schickt
  das Token deshalb als Subprotokoll `bearer, <Token>` mit.
- `test_normalize_api_token_*` / `test_whitespace_*` - ein Token aus reinem
  Leerraum ist kein Token (Review-Fix Fix 2, 2026-09-03).
- `test_warn_if_no_password_*` - die Warnung aus `cli.py`, die einen Betrieb
  ohne Passwort sichtbar machen soll (Task 8: nicht mehr das Token - ein
  konfiguriertes Token bringt sie nicht zum Schweigen).
- `test_without_a_password_*` / `test_a_password_alone_is_enough` /
  `test_a_valid_token_wins_*` (Task 8) - die eigentliche Verschaerfung
  dieses Tasks: ohne jeden Nachweis (weder Sitzung noch Token) endet JEDE
  `/api`-Route mit 401, `/cmd`/`/resync`/`/health` bleiben unveraendert
  offen, und die Reihenfolge der beiden Nachweise (Cookie zuerst, Token
  zusaetzlich) bleibt auch bei einem gleichzeitig ungueltigen Cookie
  erhalten.
- `test_fabric_backup_is_served_after_a_login_without_any_token` (Task 9,
  WebUI-Login) - der frueher hier eigens getestete 403 ohne konfiguriertes
  Token ist entfallen: ein Login ist der staerkere Ausweis, die Route
  verhaelt sich seither wie jede andere `/api`-Route (siehe
  `api.diagnostics.fabric_backup`).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot
from fastapi import HTTPException

from loxmatter import i18n
from loxmatter.auth.passwords import hash_password
from loxmatter.auth.sessions import SESSION_COOKIE
from loxmatter.cli import _warn_if_no_password
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
    """Dieselbe App, aber ohne konfiguriertes `LOXMATTER_API_TOKEN` - eine
    Installation, die ausschliesslich auf die Anmeldung setzt. Anders als
    der Name nahelegt, ist das seit Task 8 (Spec 4) kein offener Zustand:
    ohne Anmeldung antwortet jede `/api`-Route weiterhin mit 401 (siehe
    `test_without_a_password_every_api_route_is_closed` unten) - "offen"
    heisst hier nur "kein zweiter, tokenbasierter Nachweis daneben"."""
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
    await guard(_FakeConnection(), authorization=authorization, sec_websocket_protocol=subprotocol)


async def test_guard_rejects_everything_when_no_token_is_configured_and_no_session_exists(
    guard_store,
):
    """Task 8: der bis dahin offene Zustand (kein Token konfiguriert -> der
    Waechter laesst durch) entfaellt ersatzlos. Ohne Sitzung UND ohne Token
    bleibt jede Anfrage abgelehnt, ganz gleich, was im Authorization-Header
    steht - `guard_store` hat weder ein Passwort noch eine Sitzung."""
    guard = build_api_guard(None, guard_store)
    with pytest.raises(HTTPException) as excinfo:
        await _call_guard(guard)
    assert excinfo.value.status_code == 401
    with pytest.raises(HTTPException) as excinfo:
        await _call_guard(guard, authorization="Bearer irgendwas")
    assert excinfo.value.status_code == 401


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


async def test_a_whitespace_only_token_behaves_like_no_token_at_all(guard_store):
    """Der urspruenglich gemeldete Fehler: der Waechter hielt Leerraum fuer
    ein echtes Geheimnis, das kein HTTP-Header je uebertragen konnte, und
    sperrte den Dienst dauerhaft - ohne dass die Startwarnung darauf
    hingewiesen haette. Seit Task 8 bedeutet "kein Token" nicht mehr offen,
    sondern denselben 401 wie ganz ohne Token (siehe
    `test_guard_rejects_everything_when_no_token_is_configured_and_no_
    session_exists` oben) - ein Leerraum-Token darf sich davon nicht
    unterscheiden, sonst waeren Waechter und `normalize_api_token` wieder
    auseinandergelaufen."""
    guard = build_api_guard("   ", guard_store)
    with pytest.raises(HTTPException) as excinfo:
        await _call_guard(guard)
    assert excinfo.value.status_code == 401


async def test_a_token_with_a_trailing_newline_is_usable_over_http(tmp_path, no_invoke):
    """Der Fall aus der kopierten `.env`: das Token traegt einen
    Zeilenumbruch, der Browser kann ihn nicht mitschicken. Nach der
    Normalisierung passt das abgeschnittene Geheimnis."""
    async for client, _, _, _ in _build_client(tmp_path, no_invoke, api_token="secret\n"):
        response = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Ohne Token, aber angemeldet: jede /api-Route ist offen - auch die
# Fabric-Sicherung (Task 8: der Zustand OHNE Anmeldung ist inzwischen
# ausnahmslos 401, siehe oben `test_without_a_password_every_api_route_is_
# closed`. Diese Tests hier pruefen die verbliebene Frage - reicht die
# Anmeldung allein, ohne Token, fuer die vier gewoehnlichen `/api`-Router?).
# ---------------------------------------------------------------------------


async def test_without_a_token_a_signed_in_devices_route_is_open(open_client):
    client, _, _, store = open_client
    await authenticate(store, client)
    response = await client.get("/api/devices")
    assert response.status_code == 200


async def test_without_a_token_a_signed_in_export_status_is_open(open_client):
    client, _, _, store = open_client
    await authenticate(store, client)
    response = await client.get("/api/export/status")
    assert response.status_code == 200


async def test_without_a_token_a_signed_in_controls_route_is_open(open_client):
    client, _, device_id, store = open_client
    await authenticate(store, client)
    response = await client.get(f"/api/devices/{device_id}/controls")
    assert response.status_code == 200


async def test_without_a_token_a_signed_in_diagnostics_commands_is_open(open_client):
    client, _, _, store = open_client
    await authenticate(store, client)
    response = await client.get("/api/diagnostics/commands")
    assert response.status_code == 200


async def test_the_other_api_routes_stay_open_for_a_signed_in_client_without_a_token(open_client):
    """Die Gegenprobe zu den vier Tests darueber: jede weitere `/api`-Route
    bleibt fuer eine angemeldete Sitzung offen, auch ohne konfiguriertes
    Token - nichts an dieser Sitzung ist auf ein Token angewiesen."""
    client, _, device_id, store = open_client
    await authenticate(store, client)
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
# Mit Token: jeder der sechs /api-Router verlangt ihn einzeln - nicht nur
# "irgendeine" Route, jede. Ein Router, der versehentlich ohne
# dependencies=api_guard eingebunden wuerde, faellt hier auf, statt sich
# darauf zu verlassen, dass der Praefix /api schon irgendwie schuetzt. Vier
# davon als gewoehnliche HTTP-Tests direkt unten (devices, export, control,
# diagnostics) - die beiden WebSocket-Router (`/api/live`,
# `/api/diagnostics/live`) folgen demselben Prinzip in der Gruppe
# `test_websocket_*` weiter unten, siehe Moduldocstring oben.
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
    `Authorization`-Header durch die hier geprueften Router-Gruppen (device-,
    export- und diagnostics-Router) - eine Stichprobe von dreien, nicht eine
    vollstaendige Aufzaehlung aller sechs `/api`-Router wie bei den
    Token-Tests weiter oben."""
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


async def test_websocket_live_is_rejected_without_a_header_when_no_token_is_configured(
    open_client,
):
    """Task 8: kein Token konfiguriert heisst nicht mehr automatisch offen -
    ohne Sitzungscookie im Handshake bleibt auch `/api/live` bei 401."""
    _, app, _, _ = open_client
    status = await _websocket_handshake_status(app, "/api/live", headers=[])
    assert status == 401


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


async def test_websocket_live_answers_without_a_subprotocol_when_none_was_offered(secured_client):
    """Die Gegenprobe zu `test_websocket_live_echoes_the_bearer_marker_never_
    the_token` oben: ein Client ohne Subprotokoll-Angebot darf keins
    zurueckbekommen - ein nicht angebotenes Subprotokoll ist nach RFC 6455
    ebenso ein Handshake-Fehler. Seit Task 8 braucht auch dieser Handshake
    einen gueltigen Nachweis, um ueberhaupt bis zum Accept zu kommen - hier
    das Sitzungscookie, derselbe Weg wie bei
    `test_the_live_websocket_connects_with_a_cookie_and_no_subprotocol`
    unten (dort ohne Interesse am Subprotokoll-Feld selbst)."""
    client, app, _device_id, store = secured_client
    store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
    login = await client.post("/auth/login", json={"password": "ein-gutes-passwort"})
    assert login.status_code == 200
    session_id = client.cookies.get("loxmatter_session")
    assert session_id is not None

    message = await _websocket_handshake(
        app, "/api/live", headers=[(b"cookie", f"loxmatter_session={session_id}".encode())]
    )
    assert message["type"] == "websocket.accept"
    assert message["subprotocol"] is None


async def test_websocket_diagnostics_live_is_rejected_with_401_without_a_header(secured_client):
    """`/api/diagnostics/live` (Task 4, Phase 5, Spec 10.5) ist eine zweite
    WebSocket-Route neben `/api/live` - derselbe Waechter, dieselbe
    ASGI-„Denial Response"-Pruefung, damit ein aus Versehen ohne
    `dependencies=api_guard` eingebundener Router hier auffiele."""
    _, app, _, _ = secured_client
    status = await _websocket_handshake_status(app, "/api/diagnostics/live", headers=[])
    assert status == 401


async def test_websocket_diagnostics_live_is_accepted_with_the_correct_header(secured_client):
    _, app, _, _ = secured_client
    status = await _websocket_handshake_status(
        app, "/api/diagnostics/live", headers=[(b"authorization", b"Bearer secret")]
    )
    assert status is None


async def test_websocket_diagnostics_live_is_accepted_with_the_token_in_the_subprotocol(
    secured_client,
):
    _, app, _, _ = secured_client
    headers, subprotocols = _bearer_subprotocol_handshake("secret")
    status = await _websocket_handshake_status(app, "/api/diagnostics/live", headers, subprotocols)
    assert status is None


async def test_websocket_diagnostics_live_is_rejected_with_a_wrong_token_in_the_subprotocol(
    secured_client,
):
    _, app, _, _ = secured_client
    headers, subprotocols = _bearer_subprotocol_handshake("falsch")
    status = await _websocket_handshake_status(app, "/api/diagnostics/live", headers, subprotocols)
    assert status == 401


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
# Die Warnung im Log (cli.py) - sichtbar fuer einen Betrieb ohne Passwort
# (Task 8: nicht mehr fuer einen Betrieb ohne Token - siehe
# `_warn_if_no_password`-Docstring in cli.py).
# ---------------------------------------------------------------------------


def test_warn_if_no_password_logs_a_clear_warning(caplog, tmp_path):
    store = Store(tmp_path / "t.sqlite")  # kein Passwort vergeben
    try:
        with caplog.at_level(logging.WARNING):
            _warn_if_no_password(store)
    finally:
        store.close()
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert "No password has been set" in caplog.records[0].message  # cli.run.warn_no_password


def test_warn_if_no_password_logs_a_clear_warning_in_german(caplog, tmp_path):
    """Deutsches Gegenstueck zu `test_warn_if_no_password_logs_a_clear_warning`
    oben."""
    i18n.set_language("de")
    store = Store(tmp_path / "t.sqlite")  # kein Passwort vergeben
    try:
        with caplog.at_level(logging.WARNING):
            _warn_if_no_password(store)
    finally:
        store.close()
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert "kein Passwort" in caplog.records[0].message


def test_warn_if_no_password_stays_silent_once_one_is_set(caplog, tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
        with caplog.at_level(logging.WARNING):
            _warn_if_no_password(store)
    finally:
        store.close()
    assert caplog.records == []


def test_warn_if_no_password_takes_an_already_open_store_not_a_path() -> None:
    """Fund F: `_warn_if_no_password` nahm frueher einen Pfad entgegen und
    oeffnete daraus eine ZWEITE `Store`-Verbindung - direkt nachdem `run`
    bereits eine geoeffnet hatte, die es drei Zeilen spaeter an `_run`
    weiterreicht. Das bedeutete einen doppelten `_migrate`-Lauf, eine zweite
    Sperrdomaene auf derselben Datei und eine Oeffnung ohne den Schutz des
    `try`/`except`, das die erste umgibt. Die Signatur soll das nicht wieder
    zulassen: ein `Store`, kein `Path`.

    Zusammen mit den Tests oben deckt das auch ab, dass ein konfiguriertes
    Token die Warnung weiterhin nicht zum Schweigen bringen kann - seit
    Task 8 gibt es dafuer gar keinen Parameter mehr, ueber den ein Aufrufer
    das versuchen koennte."""
    assert list(inspect.signature(_warn_if_no_password).parameters) == ["store"]


# ---------------------------------------------------------------------------
# Task 8: Ohne Passwort liefert `/api` nichts mehr aus - der bislang offene
# Zustand (kein Token -> Waechter laesst durch) entfaellt ersatzlos.
# ---------------------------------------------------------------------------


async def test_without_a_password_every_api_route_is_closed(open_client):
    """Die Verschaerfung aus Spec 4: bis hierher war genau dieser Zustand -
    kein Passwort, kein Token - vollstaendig offen, mit nichts als einer
    Warnung im Log."""
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
    """`/cmd` und `/resync` bleiben in JEDEM Zustand offen - der Miniserver
    kann weder Header noch Cookie mitschicken."""
    client, _app, _device_id, _store = open_client
    assert (await client.get("/resync")).status_code == 200
    assert (await client.get("/health")).status_code == 200


async def test_without_a_password_a_configured_token_still_works(secured_client):
    """Der Bestandsfall unmittelbar nach dem Update: das Passwort fehlt
    noch, das Token steht in der `.env` - Skripte duerfen dadurch nicht
    abreissen."""
    client, _app, _device_id, _store = secured_client
    response = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200


async def test_a_password_alone_is_enough(open_client):
    """Kein Token konfiguriert, aber angemeldet - der Normalfall nach der
    Ersteinrichtung."""
    client, _app, _device_id, store = open_client
    store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
    await client.post("/auth/login", json={"password": "ein-gutes-passwort"})
    assert (await client.get("/api/devices")).status_code == 200


async def test_a_valid_token_wins_even_with_an_invalid_cookie_alongside(secured_client):
    """Die Reihenfolge der beiden Nachweise (Review-Fund zu Task 6): das
    Cookie wird zuerst geprueft, aber ein ungueltiges oder fremdes Cookie
    darf einen gleichzeitig gueltigen Token-Header nicht ausstechen - sonst
    koennte ein manipulierter Cookie-Wert ein Skript aussperren, das sich
    korrekt mit `Authorization: Bearer <Token>` ausweist."""
    client, _app, _device_id, _store = secured_client
    client.cookies.set(SESSION_COOKIE, "erfunden")
    response = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200


async def test_fabric_backup_is_served_after_a_login_without_any_token(open_client):
    """Nach dem Login ist auch die Fabric-Sicherung frei (Spec 11): ein Login
    ist der staerkere Ausweis, und ein zweites Geheimnis danach schuetzte
    nichts, das nicht schon geschuetzt waere."""
    client, _app, _device_id, store = open_client
    store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
    await client.post("/auth/login", json={"password": "ein-gutes-passwort"})
    response = await client.get("/api/diagnostics/fabric-backup")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
