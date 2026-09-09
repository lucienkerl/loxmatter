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

"""A single real WebSocket handshake against a real `uvicorn`
(review fix Important #1, 2026-09-02).

**Why this test is needed even though `tests/api/test_live.py` already has
eight green tests for `/api/live`:** those tests all run over
`_InProcessWebSocket` (see `tests/api/conftest.py`) - an ASGI app, driven
directly as an `asyncio.Task`, with no real server in between at all. That
was already the case when this bug was discovered: `uvicorn` alone (without
the "standard" extra) ships no WebSocket implementation at all, `GET
/api/live` answered a real `uvicorn.run` of this service with 404
"Unsupported upgrade request" - and yet the entire test suite stayed green,
because the in-process path simply never runs through uvicorn's own
HTTP/WebSocket switch. `websockets>=12` in `pyproject.toml` (see the comment
there) has since been the only safeguard against this - a dependency
update, a cleanup ("nobody imports `websockets` directly anyway"), or a
switch from `uvicorn[standard]` to plain `uvicorn` would silently break the
WebUI's live update again, and `uv run pytest` would not notice - because
that is exactly what happened.

This test closes exactly that gap: it starts a REAL `uvicorn.Server` on a
loopback port and runs a real WebSocket handshake (RFC 6455) against
`/api/live` over it - **without using a WebSocket client library itself**
(see `_perform_raw_handshake` below). That's deliberate: if the client used
the `websockets` package here instead, a `websockets` removed from the
environment would already make the TEST CLIENT fail with an `ImportError`,
long before the actual server (uvicorn) is even asked - the test would go
red, but for the wrong reason, and switching the test client to a different
library could reopen the gap. A raw TCP socket with hand-built upgrade
headers depends on nothing that could itself mask the absence of
`websockets` - if `uvicorn` falls back to no WebSocket implementation for
lack of `websockets` (and without `wsproto`, which this project also
doesn't install), the server answers with `404`, and that's exactly what
the assertion below catches.

**Binds to `127.0.0.1`, port `0`.** `127.0.0.1` never leaves this machine -
no network access in the sense of the project constraint (see the task
brief). Port `0` lets the operating system assign a free port
(`socket.getsockname()` returns it afterward), so this test never collides
with an already-occupied port, no matter how often or how parallel it runs.

**As its own marker (`slow`), but without a default exclusion.** Starting
and stopping a real `uvicorn` process (in a thread) noticeably costs more
than the milliseconds of an in-process test - but a test that had to be
specifically opted into (`-m slow`, or conversely deliberately skipped with
`-m "not slow"`) would be a test that gets forgotten. The marker therefore
exists only so CI can specifically filter it out or specifically isolate
and rerun it when needed - `uv run pytest` with no filter always runs it
along with everything else."""

from __future__ import annotations

import base64
import contextlib
import os
import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn

from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_app

STARTUP_TIMEOUT_S = 5.0
SHUTDOWN_TIMEOUT_S = 5.0


class _NullSender:
    """Like `_NullSender` in `conftest.py` - pure filler for
    `Runtime.__init__`, this test only checks the handshake, not any data
    flow over the UDP bridge."""

    async def send(self, key: str, value: float | bool, *, force: bool = False) -> bool:
        return True

    async def close(self) -> None:
        return None


def _perform_raw_handshake_response(
    host: str, port: int, path: str, *, timeout: float, subprotocols: str | None = None
) -> str:
    """Performs the WebSocket handshake (RFC 6455) itself, over a raw TCP
    socket - see the module docstring for why no WebSocket client is used
    here. Returns the FULL response (status line and headers).

    `subprotocols` sets the `Sec-WebSocket-Protocol` header - exactly what a
    browser does with `new WebSocket(url, ["bearer", token])` and what
    `loxone.server.build_api_guard` reads as the second transmission path
    for the token (review fix Fix 1c, 2026-09-03)."""
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    protocol_header = (
        f"Sec-WebSocket-Protocol: {subprotocols}\r\n" if subprotocols is not None else ""
    )
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"{protocol_header}"
        "\r\n"
    ).encode("ascii")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(request)
        response = sock.recv(4096)
    return response.decode("iso-8859-1")


def _perform_raw_handshake(
    host: str, port: int, path: str, *, timeout: float, subprotocols: str | None = None
) -> str:
    """Only the status line of the response (e.g. "HTTP/1.1 101 Switching
    Protocols" on success, "HTTP/1.1 404 Not Found" for the regression case
    examined here)."""
    response = _perform_raw_handshake_response(
        host, port, path, timeout=timeout, subprotocols=subprotocols
    )
    return response.split("\r\n", 1)[0]


@contextlib.contextmanager
def _running_server(app: object) -> Iterator[int]:
    """Starts a real `uvicorn` on `127.0.0.1` with a port assigned by the
    operating system and returns that port - see the module docstring for
    both. As a context manager, ever since a second test (the token
    handshake below) needs the same setup: two copies of this setup and
    teardown would sooner or later drift apart."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    # `bind_socket()` already binds (with a port assigned by the operating
    # system, since `port=0`) - the actual port is then in
    # `sock.getsockname()`, long before `server.run()` even starts.
    sock = config.bind_socket()
    port: int = sock.getsockname()[1]

    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while not server.started:
            if time.monotonic() > deadline:
                raise TimeoutError(f"uvicorn did not start within {STARTUP_TIMEOUT_S}s")
            time.sleep(0.01)
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=SHUTDOWN_TIMEOUT_S)


@pytest.mark.slow
def test_a_real_uvicorn_upgrades_api_live_to_a_websocket(plug_store, no_invoke):
    """Regression for the 404 failure from the module docstring: a real
    `uvicorn` server must answer `GET /api/live` with an upgrade to `101
    Switching Protocols`, not with `404 Unsupported upgrade request`.

    Deliberately an ORDINARY (non-async) test body: `uvicorn.Server.run`
    builds its own `asyncio` event loop in its own thread (see
    `capture_signals` in `uvicorn/server.py` - signals are explicitly
    handled only in the main thread there, so a server run in a side
    thread is supported), independent of the loop `pytest-asyncio` sets up
    for the async tests in this suite."""
    store, _device_id = plug_store
    # Since Task 8, the guard lets nothing through without proof - here a
    # token instead of a signed-in session, deliberately: `Store` belongs,
    # per its own module docstring, to "exactly one thread and exactly one
    # event loop", but `server.run()` below runs in its OWN thread (see
    # this function's docstring). A session cookie would make the guard
    # call `store.auth.session_expires_at` from exactly that foreign
    # thread and crash with `sqlite3.ProgrammingError`; the token
    # comparison (`_tokens_match`) is a pure string comparison and doesn't
    # touch the store at all.
    runtime = Runtime(store, _NullSender())
    app = build_app(store, no_invoke, runtime, api_token="secret")

    with _running_server(app) as port:
        status_line = _perform_raw_handshake(
            "127.0.0.1",
            port,
            "/api/live",
            timeout=STARTUP_TIMEOUT_S,
            subprotocols="bearer, secret",
        )
        assert "101" in status_line, f"WebSocket upgrade failed: {status_line!r}"


@pytest.mark.slow
def test_a_real_uvicorn_upgrades_api_diagnostics_live_to_a_websocket(plug_store, no_invoke):
    """The same regression as above, for this bridge's second WebSocket
    route (Task 4, Phase 5, Spec 10.5): `/api/diagnostics/live` hangs off
    the same `uvicorn` setup, a missing `websockets` installation would hit
    both routes equally - this test covers them independently of each
    other, so that a regression found in one doesn't leave the other
    unchecked."""
    store, _device_id = plug_store
    runtime = Runtime(store, _NullSender())
    app = build_app(store, no_invoke, runtime, api_token="secret")

    with _running_server(app) as port:
        status_line = _perform_raw_handshake(
            "127.0.0.1",
            port,
            "/api/diagnostics/live",
            timeout=STARTUP_TIMEOUT_S,
            subprotocols="bearer, secret",
        )
        assert "101" in status_line, f"WebSocket upgrade failed: {status_line!r}"


@pytest.mark.slow
def test_a_real_uvicorn_accepts_the_token_from_the_websocket_subprotocol(plug_store, no_invoke):
    """The path the browser UI takes when a token is configured - here for
    once against a REAL server instead of against the ASGI app directly
    (review fix Fix 1c, 2026-09-03).

    The in-process test in `tests/api/test_security.py` fills the scope
    field `subprotocols` by hand; only here does `uvicorn` actually derive
    it from the `Sec-WebSocket-Protocol` header, and only here does it show
    whether the response contains the chosen subprotocol - without that, a
    browser aborts the handshake per RFC 6455, and the test suite would not
    have noticed (as already happened once before with the 404 above)."""
    store, _device_id = plug_store
    runtime = Runtime(store, _NullSender())
    app = build_app(store, no_invoke, runtime, api_token="secret")

    with _running_server(app) as port:
        accepted = _perform_raw_handshake_response(
            "127.0.0.1",
            port,
            "/api/live",
            timeout=STARTUP_TIMEOUT_S,
            subprotocols="bearer, secret",
        )
        rejected = _perform_raw_handshake_response(
            "127.0.0.1",
            port,
            "/api/live",
            timeout=STARTUP_TIMEOUT_S,
            subprotocols="bearer, falsch",
        )

    status_line = accepted.split("\r\n", 1)[0]
    assert "101" in status_line, f"Handshake with token failed: {status_line!r}"
    # The marker must come back, the token must appear NOWHERE in the
    # response - neither in the subprotocol header nor anywhere else.
    assert "sec-websocket-protocol: bearer" in accepted.lower(), accepted
    assert "secret" not in accepted

    assert "401" in rejected.split("\r\n", 1)[0], rejected
