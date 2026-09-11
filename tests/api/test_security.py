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

"""Tests for the token protection of the `/api` routes (Task 8, Phase 5, Spec 9).

The core question of this file: does `build_api_guard` protect exactly what
Spec 9 demands - every route under `/api`, including the WebSocket route
`/api/live` and `GET /api/diagnostics/fabric-backup` - while leaving `/cmd`
and `/resync` open, unchanged, because the Miniserver can't send along a
header?

Seven groups:

- `test_guard_*` - `build_api_guard` itself, with no FastAPI app at all: the
  pure decision logic (since Task 8 there is no more open state - without a
  valid session, only the exact matching `Authorization` header decides, and
  with no token at all, every request without a session stays rejected).
- `test_*` with `secured_client`/`open_client` - the same job as above, but
  through the actual ASGI app: each of the six `/api` routers AND
  `/cmd`/`/resync` requested individually, so that a router accidentally
  wired up without `dependencies=api_guard` would show up here instead of
  relying on the router prefix.
- `test_websocket_*` - `/api/live` and, since Task 4 of this phase,
  `/api/diagnostics/live` are not ordinary routes: rejection happens BEFORE
  `websocket.accept()`, via the ASGI "denial response" extension (see
  `_websocket_handshake_status` below and `build_api_guard`'s docstring in
  `loxone/server.py`). Since review fix Fix 1c (2026-09-03), the second
  transmission path is added here too: a browser `WebSocket` cannot set an
  `Authorization` header and instead sends the token as the subprotocol
  `bearer, <Token>`.
- `test_normalize_api_token_*` / `test_whitespace_*` - a token made of pure
  whitespace is not a token (review fix Fix 2, 2026-09-03).
- `test_warn_if_no_password_*` - the warning from `cli.py` that is meant to
  make an operation without a password visible (Task 8: no longer the
  token - a configured token no longer silences it).
- `test_without_a_password_*` / `test_a_password_alone_is_enough` /
  `test_a_valid_token_wins_*` (Task 8) - the actual tightening in this task:
  with no proof at all (neither session nor token), EVERY `/api` route ends
  in 401, `/cmd`/`/resync`/`/health` stay open unchanged, and the order of
  the two proofs (cookie first, token in addition) is preserved even with a
  simultaneously invalid cookie.
- `test_fabric_backup_is_served_after_a_login_without_any_token` (Task 9,
  WebUI login) - the 403 without a configured token that used to be tested
  here specifically has gone away: a login is the stronger proof, and the
  route now behaves like any other `/api` route (see
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
    """Like in tests/loxone/test_server.py - enough for `Runtime` without
    opening a real UDP socket. Needed here (instead of the simpler
    `FakeRuntime` from conftest.py) because `/resync` needs real
    `Runtime.resend_all()` support, which `FakeRuntime` doesn't implement."""

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        return True

    async def close(self) -> None:
        return None


def _matter_data_dir(tmp_path: Path) -> Path:
    """Like in tests/api/test_diagnostics.py - a directory with a harmless
    test file, standing in for the matter-server data directory without
    touching any real key material."""
    directory = tmp_path / "matter-data"
    directory.mkdir()
    (directory / "credentials.json").write_text('{"fixture": "no real keys"}')
    return directory


async def _build_client(
    tmp_path: Path, no_invoke: Any, *, api_token: str | None
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, int, Store]]:
    """Builds a store, a REAL `Runtime` (for `/resync`) and the app with the
    given `api_token` - the shared setup for `secured_client` and
    `open_client` below, which differ only in `api_token`.

    Since the WebUI login, also hands out the `Store`: the tests need it to
    set a password and log in."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot))
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
    """The app with the token `"secret"` set - for every test that wants to
    check that the guard actually kicks in. `client=None` (no Matter
    client): commissioning/removal are not the subject of this file, the
    two routes for that keep answering with 503 unchanged (see the
    server.py module docstring)."""
    async for item in _build_client(tmp_path, no_invoke, api_token="secret"):
        yield item


@pytest.fixture
async def open_client(tmp_path, no_invoke):
    """The same app, but with no `LOXMATTER_API_TOKEN` configured - an
    installation relying purely on login. Unlike the name suggests, this is
    not an open state since Task 8 (Spec 4): without a login, every `/api`
    route still answers with 401 (see
    `test_without_a_password_every_api_route_is_closed` below) - "open"
    here only means "no second, token-based proof alongside it"."""
    async for item in _build_client(tmp_path, no_invoke, api_token=None):
        yield item


# ---------------------------------------------------------------------------
# build_api_guard itself - with no FastAPI app, pure decision logic.
# ---------------------------------------------------------------------------


@pytest.fixture
def guard_store(tmp_path):
    """An empty store for the tests that call `build_api_guard` directly -
    no password and no session, so that only the token decides between
    passing through and rejecting there."""
    store = Store(tmp_path / "guard.sqlite")
    yield store
    store.close()


class _FakeConnection:
    """Satisfies `guard` as the `conn` argument in the `test_guard_*` tests
    below - which check only the token logic and only need an object with
    `.cookies` for that, not a real `HTTPConnection` from a running app
    (which doesn't exist here, unlike with `secured_client`/`open_client`)."""

    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}


async def _call_guard(
    guard: Any, *, authorization: str | None = None, subprotocol: str | None = None
) -> None:
    """Calls the guard directly - with BOTH header parameters, always.

    An omitted parameter would otherwise get FastAPI's `Header(...)` object
    as its value (the signature's default), not `None`: outside a running
    app, nothing resolves the dependency. This helper contains that trap in
    exactly one place instead of in every test."""
    await guard(_FakeConnection(), authorization=authorization, sec_websocket_protocol=subprotocol)


async def test_guard_rejects_everything_when_no_token_is_configured_and_no_session_exists(
    guard_store,
):
    """Task 8: the previously open state (no token configured -> the guard
    lets it through) is gone with nothing to replace it. Without a session
    AND without a token, every request stays rejected, no matter what the
    Authorization header says - `guard_store` has neither a password nor a
    session."""
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
    await _call_guard(guard, authorization="Bearer secret")  # does not raise


async def test_guard_rejects_a_non_ascii_token_with_401_not_a_crash(guard_store):
    """`secrets.compare_digest` raises `TypeError` on `str` arguments as soon
    as one of them contains non-ASCII (review fix Fix 2). An attacker could
    otherwise trigger a 500 instead of a 401 with a single umlaut in the
    header - the guard therefore compares UTF-8 bytes."""
    guard = build_api_guard("secret", guard_store)
    with pytest.raises(HTTPException) as excinfo:
        await _call_guard(guard, authorization="Bearer geheßimnis")
    assert excinfo.value.status_code == 401


async def test_guard_accepts_a_non_ascii_token_that_actually_matches(guard_store):
    """The flip side of the test above: a non-ASCII token isn't rejected
    outright, it just no longer causes a crash."""
    guard = build_api_guard("geheßimnis", guard_store)
    await _call_guard(guard, authorization="Bearer geheßimnis")  # does not raise


# ---------------------------------------------------------------------------
# The second transmission path: Sec-WebSocket-Protocol (review fix Fix 1c).
# A browser `WebSocket` cannot set an `Authorization` header.
# ---------------------------------------------------------------------------


async def test_guard_accepts_the_token_from_the_websocket_subprotocol(guard_store):
    guard = build_api_guard("secret", guard_store)
    await _call_guard(guard, subprotocol="bearer, secret")  # does not raise


async def test_guard_rejects_a_wrong_token_in_the_websocket_subprotocol(guard_store):
    guard = build_api_guard("secret", guard_store)
    with pytest.raises(HTTPException) as excinfo:
        await _call_guard(guard, subprotocol="bearer, falsch")
    assert excinfo.value.status_code == 401


async def test_guard_rejects_a_subprotocol_without_the_bearer_marker(guard_store):
    """Only the form `bearer, <Token>` counts - a single value is not a
    token, even if it happens to match the secret."""
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
    """The `Authorization` header stays the main path: if it's correct, the
    call goes through no matter what's in the subprotocol."""
    guard = build_api_guard("secret", guard_store)
    await _call_guard(guard, authorization="Bearer secret", subprotocol="bearer, falsch")


# ---------------------------------------------------------------------------
# A token made of pure whitespace is not a token (review fix Fix 2).
# ---------------------------------------------------------------------------


def test_normalize_api_token_treats_whitespace_only_as_no_token():
    assert normalize_api_token(None) is None
    assert normalize_api_token("") is None
    assert normalize_api_token("   ") is None
    assert normalize_api_token("\n") is None


def test_normalize_api_token_strips_the_outer_whitespace_of_a_real_token():
    """A `LOXMATTER_API_TOKEN` with a trailing newline should be the secret
    without the newline - a secret that can't be carried in an HTTP header
    wouldn't be one."""
    assert normalize_api_token("  secret\n") == "secret"


async def test_a_whitespace_only_token_behaves_like_no_token_at_all(guard_store):
    """The originally reported bug: the guard treated whitespace as a real
    secret that no HTTP header could ever carry, and locked the service
    permanently - without the startup warning ever pointing that out. Since
    Task 8, "no token" no longer means open, but the same 401 as with no
    token at all (see
    `test_guard_rejects_everything_when_no_token_is_configured_and_no_
    session_exists` above) - a whitespace token must not differ from that,
    or the guard and `normalize_api_token` would drift apart again."""
    guard = build_api_guard("   ", guard_store)
    with pytest.raises(HTTPException) as excinfo:
        await _call_guard(guard)
    assert excinfo.value.status_code == 401


async def test_a_token_with_a_trailing_newline_is_usable_over_http(tmp_path, no_invoke):
    """The case from the copy-pasted `.env`: the token carries a newline
    that the browser can't send along. After normalization, the trimmed
    secret matches."""
    async for client, _, _, _ in _build_client(tmp_path, no_invoke, api_token="secret\n"):
        response = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# No token, but signed in: every /api route is open - even the fabric
# backup (Task 8: the state WITHOUT a login is now, without exception, 401,
# see `test_without_a_password_every_api_route_is_closed` above. These tests
# here check the remaining question - is being signed in alone, without a
# token, enough for the four ordinary `/api` routers?).
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
    """The counter-check to the four tests above: every other `/api` route
    stays open for a signed-in session, even without a configured token -
    nothing about this session depends on a token."""
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
# With a token: each of the six /api routers requires it individually - not
# just "some" route, every one. A router accidentally wired up without
# dependencies=api_guard shows up here instead of relying on the /api prefix
# somehow protecting it already. Four of them as ordinary HTTP tests right
# below (devices, export, control, diagnostics) - the two WebSocket routers
# (`/api/live`, `/api/diagnostics/live`) follow the same principle in the
# `test_websocket_*` group further down, see the module docstring above.
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


async def test_the_401_detail_explains_how_to_sign_in(secured_client):
    """Task 6: `build_api_guard`'s 401 text via `i18n.t`. Through the real
    ASGI app path (not `_call_guard` further up, which calls `guard`
    directly, without going through the `sync_language` middleware)."""
    client, _, _, _ = secured_client
    response = await client.get("/api/devices")
    assert response.status_code == 401
    assert response.json()["detail"] == (
        "Sign-in required — please open the interface and log in. Scripts use "
        "`Authorization: Bearer <Token>` with the value set under LOXMATTER_API_TOKEN."
    )


async def test_the_401_detail_is_german_when_the_language_is_de(secured_client):
    """German companion test to test_the_401_detail_explains_how_to_sign_in -
    `store.locale.set_language`, not `i18n.set_language` directly: the
    sync_language middleware reads from the store fresh on every request."""
    client, _, _, store = secured_client
    store.locale.set_language("de")
    response = await client.get("/api/devices")
    assert response.status_code == 401
    assert response.json()["detail"] == (
        "Anmeldung erforderlich – bitte die Oberfläche öffnen und anmelden. "
        "Skripte verwenden `Authorization: Bearer <Token>` mit dem unter "
        "LOXMATTER_API_TOKEN gesetzten Wert."
    )


# ---------------------------------------------------------------------------
# The fabric backup: the actual reason for this phase (see Spec 4.1). A
# dedicated test, not just one of many /api routes, because this exact route
# is the reason for Task 8.
# ---------------------------------------------------------------------------


async def test_fabric_backup_is_401_without_a_header_even_with_a_configured_directory(
    secured_client,
):
    """`matter_data_dir` is set (see `_build_client`) - so without a token
    the route would actually be able to serve real data. Exactly that must
    not happen without a header."""
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
# The session cookie: the second proof alongside the token (Task 6, Phase 6).
# Additive - the guard lets cookies through in addition, without taking
# anything away from the token.
# ---------------------------------------------------------------------------


async def test_a_session_cookie_opens_every_api_router(secured_client):
    """The second proof alongside the token: whoever is signed in gets
    through the router groups checked here (device, export and diagnostics
    routers) without an `Authorization` header - a sample of three, not a
    complete enumeration of all six `/api` routers as in the token tests
    further up."""
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
        assert response.status_code == 200, f"{path} required a login despite the session"


async def test_an_invalid_session_cookie_does_not_open_anything(secured_client):
    client, _app, _device_id, _store = secured_client
    client.cookies.set("loxmatter_session", "erfunden")
    assert (await client.get("/api/devices")).status_code == 401


async def test_the_token_still_works_next_to_the_cookie(secured_client):
    """The path for scripts stays unchanged - it's the reason the token
    exists at all."""
    client, _app, _device_id, _store = secured_client
    response = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /cmd and /resync: the Miniserver path. Must stay open UNCHANGED, even when
# a token is configured - the Miniserver cannot send along a header (see the
# build_api_guard docstring, loxone/server.py).
# ---------------------------------------------------------------------------


async def test_with_token_cmd_route_stays_open(secured_client):
    client, _, device_id, _ = secured_client
    response = await client.get(f"/cmd/d{device_id}_1_on/1")
    assert response.status_code == 200


async def test_with_token_resync_route_stays_open(secured_client):
    client, _, _, _ = secured_client
    response = await client.get("/resync")
    assert response.status_code == 200


async def test_the_webui_resync_route_is_not_open(secured_client):
    """Counter-check to the test above: the same full resend, but via the
    UI's route (`POST /api/diagnostics/resync`, the resync button in the
    system tab). That one stays closed. The exception is for the
    Miniserver, which cannot send a header - not for the effect "resend
    every value" - if the effect were the reason, every `/cmd`-like route
    would have to be open."""
    client, _, _, _ = secured_client
    response = await client.post("/api/diagnostics/resync")
    assert response.status_code == 401


async def test_the_webui_resync_route_opens_with_a_token(secured_client):
    client, _, _, _ = secured_client
    response = await client.post(
        "/api/diagnostics/resync", headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 200


async def test_with_token_health_route_stays_open(secured_client):
    """`/health` lies outside `/api`, like `/cmd`/`/resync` - no diagnostics
    endpoint that reveals stored data, so it too must stay reachable
    independent of the token."""
    client, _, _, _ = secured_client
    response = await client.get("/health")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# /api/live: not an ordinary route. Rejection happens BEFORE the WebSocket
# handshake (ASGI "denial response"), not by accepting and then closing -
# hence a dedicated, raw ASGI call instead of httpx2, which cannot represent
# a rejected handshake.
# ---------------------------------------------------------------------------


async def _websocket_handshake(
    app: Any,
    path: str,
    headers: list[tuple[bytes, bytes]],
    subprotocols: list[str] | None = None,
) -> dict[str, Any]:
    """Runs only the WebSocket handshake against `app` and returns the FIRST
    message the app sends - either a `websocket.accept` (with the chosen
    subprotocol in it) or a `websocket.http.response.start` from the ASGI
    "denial response" extension.

    `subprotocols` fills the scope field of the same name, which a real
    server derives from the `Sec-WebSocket-Protocol` header; the header
    itself must additionally be present in `headers`, because the guard
    reads it there (exactly as with a real browser handshake).

    No text/JSON send, no ping/pong: no test in this file needs more than
    the handshake itself - live values AFTER an accept are already checked
    by tests/api/test_live.py."""
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
    await from_app.get()  # websocket.http.response.body - drain the body, end the task cleanly
    await asyncio.wait_for(task, timeout=2)
    return message


async def _websocket_handshake_status(
    app: Any,
    path: str,
    headers: list[tuple[bytes, bytes]],
    subprotocols: list[str] | None = None,
) -> int | None:
    """Like `_websocket_handshake`, but only the question "accepted?" -
    `None` means accepted, otherwise the rejecting status code."""
    message = await _websocket_handshake(app, path, headers, subprotocols)
    if message["type"] == "websocket.accept":
        return None
    status: int = message["status"]
    return status


def _bearer_subprotocol_handshake(
    token: str,
) -> tuple[list[tuple[bytes, bytes]], list[str]]:
    """Builds the header AND the scope field the way a browser produces them
    for `new WebSocket(url, ["bearer", token])` - both from one source, so
    the two can't drift apart."""
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
    """Task 8: no token configured no longer automatically means open -
    without a session cookie in the handshake, `/api/live` too stays at
    401."""
    _, app, _, _ = open_client
    status = await _websocket_handshake_status(app, "/api/live", headers=[])
    assert status == 401


async def test_websocket_live_is_accepted_with_the_token_in_the_subprotocol(secured_client):
    """The path the UI actually takes: a browser cannot set an
    `Authorization` header on `new WebSocket(...)` (review fix Fix 1c,
    2026-09-03)."""
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
    """RFC 6455: the browser aborts the handshake if the server doesn't
    return the offered subprotocol. But only the marker may be returned -
    the token would otherwise end up in every proxy and browser log along
    the way."""
    _, app, _, _ = secured_client
    headers, subprotocols = _bearer_subprotocol_handshake("secret")
    message = await _websocket_handshake(app, "/api/live", headers, subprotocols)
    assert message["type"] == "websocket.accept"
    assert message["subprotocol"] == "bearer"


async def test_websocket_live_answers_without_a_subprotocol_when_none_was_offered(secured_client):
    """The counter-check to
    `test_websocket_live_echoes_the_bearer_marker_never_the_token` above: a
    client that offered no subprotocol must not get one back - an unoffered
    subprotocol is, under RFC 6455, just as much a handshake error. Since
    Task 8, this handshake too needs valid proof to even reach the accept -
    here the session cookie, the same path as
    `test_the_live_websocket_connects_with_a_cookie_and_no_subprotocol`
    below (there without interest in the subprotocol field itself)."""
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
    """`/api/diagnostics/live` (Task 4, Phase 5, Spec 10.5) is a second
    WebSocket route alongside `/api/live` - the same guard, the same ASGI
    "denial response" check, so that a router accidentally wired up without
    `dependencies=api_guard` would show up here."""
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
    """The point where the detour via the subprotocol becomes unnecessary:
    the cookie travels along with the handshake on its own, because this
    WebSocket has the same origin as the page. `app.js` relies exactly on
    that, ever since it started using `new WebSocket(url)` with no second
    argument."""
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
    assert status is None, "The handshake was rejected despite a valid session"


# ---------------------------------------------------------------------------
# The warning in the log (cli.py) - visible for an operation without a
# password (Task 8: no longer for an operation without a token - see the
# `_warn_if_no_password` docstring in cli.py).
# ---------------------------------------------------------------------------


def test_warn_if_no_password_logs_a_clear_warning(caplog, tmp_path):
    store = Store(tmp_path / "t.sqlite")  # no password set
    try:
        with caplog.at_level(logging.WARNING):
            _warn_if_no_password(store)
    finally:
        store.close()
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert "No password has been set" in caplog.records[0].message  # cli.run.warn_no_password


def test_warn_if_no_password_logs_a_clear_warning_in_german(caplog, tmp_path):
    """German counterpart to test_warn_if_no_password_logs_a_clear_warning
    above."""
    i18n.set_language("de")
    store = Store(tmp_path / "t.sqlite")  # no password set
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
    """Finding F: `_warn_if_no_password` used to take a path and open a
    SECOND `Store` connection from it - right after `run` had already
    opened one that it passes on to `_run` three lines later. That meant a
    duplicate `_migrate` run, a second lock domain on the same file, and an
    open with none of the protection of the `try`/`except` that surrounds
    the first. The signature is meant to prevent that from happening
    again: a `Store`, not a `Path`.

    Together with the tests above, this also covers that a configured
    token still cannot silence the warning - since Task 8 there is no
    parameter left at all through which a caller could even try that."""
    assert list(inspect.signature(_warn_if_no_password).parameters) == ["store"]


# ---------------------------------------------------------------------------
# Task 8: without a password, `/api` no longer serves anything - the
# previously open state (no token -> guard lets it through) is gone with
# nothing to replace it.
# ---------------------------------------------------------------------------


async def test_without_a_password_every_api_route_is_closed(open_client):
    """The tightening from Spec 4: until now, exactly this state - no
    password, no token - was completely open, with nothing but a warning in
    the log."""
    client, _app, device_id, _store = open_client
    for path in [
        "/api/devices",
        f"/api/devices/{device_id}/controls",
        "/api/export/status",
        "/api/diagnostics/system",
        "/api/diagnostics/fabric-backup",
    ]:
        response = await client.get(path)
        assert response.status_code == 401, f"{path} still served data without a password"


async def test_without_a_password_the_miniserver_routes_stay_open(open_client):
    """`/cmd` and `/resync` stay open in EVERY state - the Miniserver can
    send neither a header nor a cookie."""
    client, _app, _device_id, _store = open_client
    assert (await client.get("/resync")).status_code == 200
    assert (await client.get("/health")).status_code == 200


async def test_without_a_password_a_configured_token_still_works(secured_client):
    """The existing-installation case right after the update: the password
    is still missing, the token is in the `.env` - scripts must not break
    because of this."""
    client, _app, _device_id, _store = secured_client
    response = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200


async def test_a_password_alone_is_enough(open_client):
    """No token configured, but signed in - the normal case after initial
    setup."""
    client, _app, _device_id, store = open_client
    store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
    await client.post("/auth/login", json={"password": "ein-gutes-passwort"})
    assert (await client.get("/api/devices")).status_code == 200


async def test_a_valid_token_wins_even_with_an_invalid_cookie_alongside(secured_client):
    """The order of the two proofs (review finding for Task 6): the cookie
    is checked first, but an invalid or foreign cookie must not be able to
    outweigh a simultaneously valid token header - otherwise a tampered
    cookie value could lock out a script that correctly identifies itself
    with `Authorization: Bearer <Token>`."""
    client, _app, _device_id, _store = secured_client
    client.cookies.set(SESSION_COOKIE, "erfunden")
    response = await client.get("/api/devices", headers={"Authorization": "Bearer secret"})
    assert response.status_code == 200


async def test_fabric_backup_is_served_after_a_login_without_any_token(open_client):
    """After login, the fabric backup is free too (Spec 11): a login is the
    stronger proof, and a second secret afterward would protect nothing
    that wasn't already protected."""
    client, _app, _device_id, store = open_client
    store.auth.set_password_hash(hash_password("ein-gutes-passwort"))
    await client.post("/auth/login", json={"password": "ein-gutes-passwort"})
    response = await client.get("/api/diagnostics/fabric-backup")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
