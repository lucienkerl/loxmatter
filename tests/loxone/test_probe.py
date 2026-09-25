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
"""Tests for `loxone/probe.py` - design "The Miniserver's address is set in
the web interface" (2026-09-25), section 8. A real aiohttp server on
127.0.0.1 stands in for the Miniserver; nothing leaves the machine."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from loxmatter.loxone.probe import ProbeOutcome, probe_miniserver

FIXTURE = Path(__file__).parents[1] / "fixtures" / "miniserver" / "jdev_cfg_api.json"

Handler = Callable[[web.Request], Any]


@pytest.fixture
async def serve() -> AsyncIterator[Callable[[Handler], Any]]:
    servers: list[TestServer] = []

    async def start(handler: Handler) -> int:
        app = web.Application()
        app.router.add_get("/jdev/cfg/api", handler)
        server = TestServer(app, host="127.0.0.1")
        await server.start_server()
        servers.append(server)
        assert server.port is not None
        return server.port

    yield start
    for server in servers:
        await server.close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def test_a_miniserver_answer_is_found_with_serial_and_firmware(
    serve: Callable[[Handler], Any],
) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text=FIXTURE.read_text(), content_type="application/json")

    port = await serve(handler)
    result = await probe_miniserver("127.0.0.1", port=port)
    assert result.outcome is ProbeOutcome.FOUND
    assert result.found
    assert result.serial == "50:4F:94:00:00:01"
    assert result.firmware == "17.3.9.18"


async def test_something_else_answering_is_not_a_miniserver(
    serve: Callable[[Handler], Any],
) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text="<html>router login</html>", content_type="text/html")

    port = await serve(handler)
    result = await probe_miniserver("127.0.0.1", port=port)
    assert result.outcome is ProbeOutcome.NOT_A_MINISERVER
    assert not result.found


async def test_an_http_error_status_is_reported_with_its_code(
    serve: Callable[[Handler], Any],
) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=503)

    port = await serve(handler)
    result = await probe_miniserver("127.0.0.1", port=port)
    assert result.outcome is ProbeOutcome.HTTP_ERROR
    assert result.http_status == 503


async def test_a_slow_answer_is_a_timeout(serve: Callable[[Handler], Any]) -> None:
    async def handler(request: web.Request) -> web.Response:
        await asyncio.sleep(2)
        return web.Response(text=FIXTURE.read_text())

    port = await serve(handler)
    result = await probe_miniserver("127.0.0.1", port=port, timeout=0.2)
    assert result.outcome is ProbeOutcome.TIMEOUT


async def test_a_closed_port_is_no_connection() -> None:
    result = await probe_miniserver("127.0.0.1", port=_free_port(), timeout=1.0)
    assert result.outcome is ProbeOutcome.NO_CONNECTION
