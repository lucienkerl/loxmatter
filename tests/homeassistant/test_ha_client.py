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

"""The Home Assistant client against a small aiohttp server that speaks
Home Assistant's sign-in and WebSocket protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from aiohttp import WSMsgType, web

from loxmatter.homeassistant.client import (
    HomeAssistantAuthError,
    HomeAssistantError,
    HomeAssistantSession,
    exchange_code,
    needs_refresh,
    normalised_url,
    probe,
    refresh_tokens,
)

GOOD_TOKEN = "good-token"


def build_server(seen: dict[str, Any]) -> web.Application:
    async def providers(request: web.Request) -> web.Response:
        return web.json_response({"providers": [{"type": "homeassistant"}]})

    async def token(request: web.Request) -> web.Response:
        form = dict(await request.post())
        seen.setdefault("token_forms", []).append(form)
        if form.get("code") == "bad" or form.get("refresh_token") == "revoked":
            return web.json_response({"error": "invalid_request"}, status=400)
        return web.json_response(
            {"access_token": GOOD_TOKEN, "expires_in": 1800, "refresh_token": "r1"}
            if form["grant_type"] == "authorization_code"
            else {"access_token": GOOD_TOKEN, "expires_in": 1800}
        )

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": "2026.11.0"})
        auth = await ws.receive_json()
        if auth.get("access_token") != GOOD_TOKEN:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": "2026.11.0"})
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                break
            body = message.json()
            seen.setdefault("commands", []).append(body)
            # An event for another subscription first: the client must skip it.
            await ws.send_json({"id": 999, "type": "event", "event": {}})
            if body["type"] == "config/area_registry/list":
                await ws.send_json(
                    {"id": body["id"], "type": "result", "success": True, "result": [{"a": 1}]}
                )
            elif body["type"] == "admin/only":
                await ws.send_json(
                    {
                        "id": body["id"],
                        "type": "result",
                        "success": False,
                        "error": {"code": "unauthorized", "message": "Unauthorized"},
                    }
                )
            else:
                await ws.send_json(
                    {
                        "id": body["id"],
                        "type": "result",
                        "success": False,
                        "error": {"code": "unknown_command", "message": "Unknown command."},
                    }
                )
        return ws

    async def delete_entry(request: web.Request) -> web.Response:
        seen["deleted_auth"] = request.headers.get("Authorization")
        return web.json_response({"require_restart": False})

    app = web.Application()
    app.router.add_get("/auth/providers", providers)
    app.router.add_post("/auth/token", token)
    app.router.add_get("/api/websocket", websocket)
    app.router.add_delete("/api/config/config_entries/entry/{entry_id}", delete_entry)
    return app


@pytest.fixture
async def server() -> AsyncIterator[tuple[str, dict[str, Any]]]:
    seen: dict[str, Any] = {}
    runner = web.AppRunner(build_server(seen))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    yield f"http://127.0.0.1:{port}", seen
    await runner.cleanup()


def test_addresses_are_normalised():
    assert normalised_url("homeassistant.local:8123") == "http://homeassistant.local:8123"
    assert normalised_url(" https://ha.example/lovelace ") == "https://ha.example"
    with pytest.raises(HomeAssistantError):
        normalised_url("ftp://ha")


def test_a_token_is_refreshed_shortly_before_it_runs_out():
    assert needs_refresh(None) is False
    assert needs_refresh(1000.0, now=500.0) is False
    assert needs_refresh(1000.0, now=900.0) is True


async def test_probe_finds_home_assistant(server):
    url, _ = server

    assert await probe(url) is True
    assert await probe("http://127.0.0.1:9") is False


async def test_the_code_is_exchanged_for_tokens(server):
    url, seen = server

    tokens = await exchange_code(url, client_id="http://bridge/", code="c0de")

    assert tokens.access_token == GOOD_TOKEN
    assert tokens.refresh_token == "r1"
    assert tokens.expires_at is not None
    assert seen["token_forms"] == [
        {"grant_type": "authorization_code", "code": "c0de", "client_id": "http://bridge/"}
    ]


async def test_a_refresh_keeps_the_refresh_token(server):
    url, _ = server

    tokens = await refresh_tokens(url, client_id="http://bridge/", refresh_token="r1")

    assert tokens.refresh_token == "r1"


async def test_a_refused_code_is_an_auth_error(server):
    url, _ = server

    with pytest.raises(HomeAssistantAuthError):
        await exchange_code(url, client_id="http://bridge/", code="bad")


async def test_a_call_returns_its_own_result_and_skips_events(server):
    url, seen = server

    async with HomeAssistantSession(url, GOOD_TOKEN) as session:
        result = await session.call("config/area_registry/list")

    assert result == [{"a": 1}]
    assert seen["commands"] == [{"id": 1, "type": "config/area_registry/list"}]


async def test_a_wrong_token_is_an_auth_error(server):
    url, _ = server

    with pytest.raises(HomeAssistantAuthError):
        async with HomeAssistantSession(url, "wrong"):
            pass


async def test_a_refused_command_names_it(server):
    url, _ = server

    async with HomeAssistantSession(url, GOOD_TOKEN) as session:
        with pytest.raises(HomeAssistantError) as raised:
            await session.call("nope")
        with pytest.raises(HomeAssistantAuthError):
            await session.call("admin/only")

    assert "nope" in str(raised.value)


async def test_delete_sends_the_token_as_bearer(server):
    url, seen = server

    async with HomeAssistantSession(url, GOOD_TOKEN) as session:
        await session.delete("/api/config/config_entries/entry/abc")

    assert seen["deleted_auth"] == f"Bearer {GOOD_TOKEN}"


async def test_an_unreachable_home_assistant_is_reported_without_the_token():
    with pytest.raises(HomeAssistantError) as raised:
        async with HomeAssistantSession("http://127.0.0.1:9", GOOD_TOKEN):
            pass

    assert GOOD_TOKEN not in str(raised.value)
