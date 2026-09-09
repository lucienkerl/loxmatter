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

"""The live channel for logs, recording and command log (Task 4, Phase 5,
Spec 10.5) - see api/diagnostics_live.py.

The token protection of this route is deliberately NOT here, but with the
rest of the WebSocket security tests in tests/api/test_security.py (see
there, the section on `/api/live` - `/api/diagnostics/live` follows the
same pattern next to it)."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

import httpx2 as httpx
import pytest
from conftest import WebSocketClient, authenticate

from loxmatter.api.diagnostics import RingBuffer
from loxmatter.api.diagnostics_live import SNAPSHOT_LIMIT
from loxmatter.diagnostics.logbuffer import LogBufferHandler
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_app


async def _drain_snapshot(socket: Any, *, timeout: float = 0.5) -> None:
    """Reads away the snapshot until nothing new arrives for a short while -
    never waits unboundedly while doing so (see the brief: "a test that
    hangs instead of failing is worse than none")."""
    while True:
        try:
            await asyncio.wait_for(socket.receive_json(), timeout=timeout)
        except TimeoutError:
            return


async def test_a_fresh_datagram_arrives_as_a_message(api_with_runtime):
    """The stream hangs off the SENDER, not the runtime: only there is it
    visible what was actually on the wire - including full resend and
    pulse-end, which the runtime observers leave out."""
    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        await _drain_snapshot(socket)
        await runtime.on_attribute(device_id, "2/144/4", 230000)
        message = await asyncio.wait_for(socket.receive_json(), timeout=2)

    assert message["kind"] == "datagram"
    assert message["key"] == f"d{device_id}_2_voltage"


async def test_a_datagram_message_carries_why_it_was_sent(api_with_runtime):
    """Follow-up fix, Task 6 (2026-09-03): the WebUI recognizes a
    non-essential datagram (heartbeat, full resend) by the message's
    `forced` field - no longer by the arrival rate in the browser (see
    `DatagramLogEntry.forced` and `app.js`, `visibleDatagrams`, for the
    reasoning). A real value change (`on_attribute`, no `force`) must
    carry `forced: false`, a `resend_all()` (the same `force=True` caller
    as the heartbeat) `forced: true`."""
    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        await _drain_snapshot(socket)

        await runtime.on_attribute(device_id, "2/144/4", 230000)
        changed = await asyncio.wait_for(socket.receive_json(), timeout=2)

        await runtime.resend_all()
        resent = await asyncio.wait_for(socket.receive_json(), timeout=2)

    assert changed["kind"] == "datagram"
    assert changed["forced"] is False
    assert resent["kind"] == "datagram"
    assert resent["forced"] is True


async def test_a_fresh_log_line_arrives_as_a_message(api_with_runtime):
    client, _, _ = api_with_runtime
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        await _drain_snapshot(socket)
        logging.getLogger("loxmatter.test").warning("Miniserver unreachable")
        message = await asyncio.wait_for(socket.receive_json(), timeout=2)

    assert message["kind"] == "log"
    assert message["level"] == "WARNING"
    assert message["message"] == "Miniserver unreachable"


async def test_a_log_line_from_a_real_other_thread_arrives(api_with_runtime):
    """Review fix Important #1, 2026-09-03: `on_log` hangs off
    `LogBufferHandler.add_observer`, whose contract explicitly says that the
    observer "runs in the thread that produced the line" - in this project
    aiohttp and the chip SDK, so a FOREIGN thread, not this route's event
    loop thread. A plain `queue.put(...)` touches `asyncio.Queue`
    internals (`put_nowait` -> `Future.set_result` -> `loop.call_soon`) -
    from a foreign thread, `loop.call_soon` does not wake an already
    blocked event loop, only `call_soon_threadsafe` does (see
    api/diagnostics_live.py, module docstring, "Logs" section). No test up
    to this point had found that, because every log line so far came from
    the same coroutine the loop itself runs on.

    This test therefore produces the line from a REAL, separate
    `threading.Thread` - not just sequentially like
    `test_a_line_from_another_thread_arrives` in test_logbuffer.py (there
    `start(); join()` BEFORE the next step), but while the connection is
    already genuinely IDLE: `send_loop` is sitting in `await queue.get()`,
    otherwise nothing happens on the loop - exactly the state in which a
    plain `call_soon` from a foreign thread does not wake the blocked
    selector. The 0.3-second delay in the producer thread gives the
    consumer coroutine time to actually arrive at this waiting state
    BEFORE the line is produced.

    `asyncio.wait_for(..., timeout=2)` gives the test a time limit, as
    required: if the connection keeps sleeping, the test RAISES (instead of
    hanging). The additional `elapsed < 1.0` check below also makes the
    test robust against the edge case that `wait_for`'s own timer (the
    only other event scheduled in the test) happens to wake the blocked
    selector at the same time as the delayed line itself: with the fix,
    the message arrives within milliseconds of the producer thread's
    0.3s, well BEFORE the time limit - if it instead arrived late (or not
    at all, see `wait_for`'s own `TimeoutError`), the test fails in every
    case, never just by chance."""
    client, _runtime, _device_id = api_with_runtime
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        await _drain_snapshot(socket)

        def emit_from_another_thread() -> None:
            time.sleep(0.3)
            logging.getLogger("loxmatter.test").warning("from a real foreign thread")

        thread = threading.Thread(target=emit_from_another_thread)
        thread.start()
        try:
            started = asyncio.get_running_loop().time()
            message = await asyncio.wait_for(socket.receive_json(), timeout=2)
            elapsed = asyncio.get_running_loop().time() - started
        finally:
            thread.join()

    assert message["kind"] == "log"
    assert message["message"] == "from a real foreign thread"
    assert elapsed < 1.0


async def test_the_connection_starts_with_a_snapshot(api_with_runtime):
    """Without the snapshot there would be a gap between 'fetch once' and
    'listen from now on' - and the view would be empty when opened, until
    something happened to occur."""
    client, runtime, device_id = api_with_runtime
    await runtime.on_attribute(device_id, "2/144/4", 230000)

    async with client.websocket_connect("/api/diagnostics/live") as socket:
        first = await asyncio.wait_for(socket.receive_json(), timeout=2)

    assert first["kind"] == "datagram"
    assert first["key"] == f"d{device_id}_2_voltage"


async def test_a_fresh_command_arrives_as_a_message(api_with_runtime):
    """The third stream: the command log ring from loxone/server.py, via the
    new observer chain on `RingBuffer` (see api/diagnostics.py)."""
    client, _runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        await _drain_snapshot(socket)
        response = await client.get(f"/api/devices/{device_id}/controls")
        assert response.status_code == 200
        message = await asyncio.wait_for(socket.receive_json(), timeout=2)

    assert message["kind"] == "command"
    assert message["method"] == "GET"
    assert message["path"] == f"/api/devices/{device_id}/controls"
    assert message["status"] == 200


async def test_the_snapshot_is_capped_per_stream(api_with_runtime):
    """`SNAPSHOT_LIMIT` caps the snapshot per stream - see the docstring
    there: 500 entries x 3 streams all at once would be a noticeable
    message when the view is opened."""
    client, _runtime, device_id = api_with_runtime
    extra_commands = SNAPSHOT_LIMIT + 5
    for _ in range(extra_commands):
        response = await client.get(f"/api/devices/{device_id}/controls")
        assert response.status_code == 200

    async with client.websocket_connect("/api/diagnostics/live") as socket:
        commands = []
        while True:
            try:
                message = await asyncio.wait_for(socket.receive_json(), timeout=0.5)
            except TimeoutError:
                break
            if message["kind"] == "command":
                commands.append(message)

    assert len(commands) == SNAPSHOT_LIMIT


async def test_observers_are_unsubscribed_after_disconnect(api_with_runtime, caplog, monkeypatch):
    """In the `finally`, all three observers are unregistered again -
    activity AFTER the disconnect must neither raise an error nor affect
    the next connection.

    Follow-up fix, Task 7, Fix 3c: the previous version only checked that
    no ERROR lines resulted afterward and that the next connection worked -
    both still hold, even if all three observers stayed completely leaked
    (a test that simply CANNOT fail that way). This test instead counts
    the actual subscribe/unsubscribe calls - following the example of
    `runtime.observer_count()` in `test_live.py`, which isn't directly
    applicable here though: the `api_with_runtime` fixture hands out
    neither `sender` nor the local `command_log` ring (a plain variable in
    `loxone.server.build_app`) to the outside. A spy on the two public
    methods that `api.diagnostics_live` actually calls
    (`RingBuffer.add_observer`/`remove_observer` - since the Task 7, Fix 2
    follow-up also the path taken by
    `UdpSender.add_datagram_observer`, see there - and
    `LogBufferHandler.add_observer`/`remove_observer`), makes the count
    visible independent of that: two `RingBuffer` subscriptions
    (`sender.datagram_log` AND `command_log`) and one `LogBufferHandler`
    subscription on connect, the same three objects unsubscribed exactly
    once on disconnect."""
    ring_added: list[RingBuffer[Any]] = []
    ring_removed: list[RingBuffer[Any]] = []
    log_added: list[LogBufferHandler] = []
    log_removed: list[LogBufferHandler] = []
    original_ring_add = RingBuffer.add_observer
    original_ring_remove = RingBuffer.remove_observer
    original_log_add = LogBufferHandler.add_observer
    original_log_remove = LogBufferHandler.remove_observer

    def spy_ring_add(self: RingBuffer[Any], callback: Any) -> None:
        ring_added.append(self)
        original_ring_add(self, callback)

    def spy_ring_remove(self: RingBuffer[Any], callback: Any) -> None:
        ring_removed.append(self)
        original_ring_remove(self, callback)

    def spy_log_add(self: LogBufferHandler, callback: Any) -> None:
        log_added.append(self)
        original_log_add(self, callback)

    def spy_log_remove(self: LogBufferHandler, callback: Any) -> None:
        log_removed.append(self)
        original_log_remove(self, callback)

    monkeypatch.setattr(RingBuffer, "add_observer", spy_ring_add)
    monkeypatch.setattr(RingBuffer, "remove_observer", spy_ring_remove)
    monkeypatch.setattr(LogBufferHandler, "add_observer", spy_log_add)
    monkeypatch.setattr(LogBufferHandler, "remove_observer", spy_log_remove)

    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        await _drain_snapshot(socket)

    assert len(ring_added) == 2
    assert len(log_added) == 1
    assert len(ring_removed) == 2
    assert len(log_removed) == 1
    assert set(ring_added) == set(ring_removed)
    assert set(log_added) == set(log_removed)

    # After the disconnect: new entries on all three streams must not run
    # into a dead connection.
    await runtime.on_attribute(device_id, "2/144/4", 231000)
    logging.getLogger("loxmatter.test").warning("after the disconnect")
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)

    async with client.websocket_connect("/api/diagnostics/live") as socket:
        first = await asyncio.wait_for(socket.receive_json(), timeout=2)
    assert first["kind"] == "datagram"
    assert first["key"] == f"d{device_id}_2_voltage"


class _RecordingSender:
    """Like `RecordingSender` in test_live.py - satisfies `Runtime` without
    keeping a real UDP recording. For the proof below that the route also
    answers WITHOUT `sender`/`log_handler` passed to `build_app`."""

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
async def api_without_diagnostics_streams(plug_store, no_invoke, fake_client):
    """Like `api_with_runtime`, but WITHOUT `sender`/`log_handler` passed to
    `build_app` - for the proof that `GET /api/diagnostics/live` still
    answers and only the two missing branches are absent (see
    api/diagnostics_live.py, module docstring)."""
    store, device_id = plug_store
    runtime = Runtime(store, _RecordingSender())
    app = build_app(store, no_invoke, runtime, client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield WebSocketClient(client, app), runtime, device_id


async def test_without_a_sender_or_log_handler_the_route_still_answers(
    api_without_diagnostics_streams,
):
    """`sender` and `log_handler` are optional (see `build_app`) - if one is
    missing, its branch is absent, not the route: the login itself (`POST
    /auth/login`) still lands in the command log branch."""
    client, _runtime, _device_id = api_without_diagnostics_streams
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        message = await asyncio.wait_for(socket.receive_json(), timeout=2)

    assert message["kind"] == "command"
    assert message["path"] == "/auth/login"
