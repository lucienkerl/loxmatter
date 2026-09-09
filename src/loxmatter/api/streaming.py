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

"""The WebSocket mechanics shared by several live routes (Task 1, factored
out of `api.live`): a queue per connection, noticing a disconnect, and
negotiating the subprotocol for the token in the handshake. `api.live` (the
values route) was the first user; a second channel (diagnostics feed) is
being added without building this mechanism a second time - exactly what
the first fix to it (the unbounded queue, review fix Phase 5) already made
necessary once, when there was only one user.

**The queue is bounded (review fix Important #1, 2026-09-02).** This
bridge runs unattended in someone's home for weeks, not as a
request-scoped web server - a browser tab in the background or a laptop
that has gone to sleep and stops reading is not the exception there but the
everyday case. An unbounded queue would grow without limit in that case.
`BoundedQueue` below therefore caps it at `QUEUE_MAXSIZE` and, on overflow,
drops the OLDEST entry, not the newest - a live view wants the most current
state, the stale entry is the dispensable one. For why exactly
`QUEUE_MAXSIZE`, see there.

Deliberately NOT implemented: actively disconnecting the connection when it
stays permanently full. The bound above already caps the one danger the
review named (unbounded growth) - a permanently full queue now costs only
the fixed, small size of `QUEUE_MAXSIZE` entries, no longer a growing
problem. Actively disconnecting would need its own, well-justified time
threshold ("how many minutes without progress count as dead?") - a
wrongly chosen threshold would kick out a session that only briefly
stalled from OS throttling of a background tab, and live values are pure
display: a few missed intermediate values have no consequence beyond a
briefly stale display. The debug log below (see `BoundedQueue.put`) still
makes a hanging connection discoverable, without taking on this risk.

**A disconnected client is reliably noticed.** A pure send route itself
expects no incoming messages - yet `watch_for_disconnect` runs alongside it
and calls `websocket.receive_text()` in a loop. Reason: per the ASGI
specification, the server only delivers the `websocket.disconnect` event
via `receive()` - a route that only sends and never receives would never
notice a closed browser tab and would leave its observer registered
forever. `asyncio.wait(..., return_when=FIRST_COMPLETED)` in the caller
lets the route react as soon as either of the two sub-tasks ends -
disconnect OR a send error - and cleanly tears down the other one.

**The token travels here in the subprotocol, not in the header (review fix
Fix 1c, 2026-09-03).** The browser `WebSocket` API has no parameter for
custom headers - `Authorization` is therefore impossible on these routes.
`app.js` therefore connects with `new WebSocket(url, ["bearer", token])`,
which the browser sends as `Sec-WebSocket-Protocol: bearer, <token>`;
`loxone.server.build_api_guard` reads the token out there, and
`accepted_subprotocol` below returns the marker `bearer` in the accept
(never the token itself), because otherwise the browser aborts the
handshake per RFC 6455.

A `WebSocketDisconnect` is the normal case - a browser tab being closed or
reloaded - not an error, and therefore writes nothing to the log. **A bare
`WebSocketDisconnect` is not enough, though (review fix Important #2,
2026-09-02):** if a `send_json` call in `send_loop` hangs at exactly the
moment the client disconnects, the ASGI layer - depending on the server -
sometimes throws not a `WebSocketDisconnect` but a `RuntimeError` about an
already-closed connection. `send_loop` catches that right at the send
point and treats it like a normal disconnect: debug log instead of
`logger.error`, the observer is still deregistered (that remains the
caller's job, see its `finally`) - a closed browser tab is not a program
bug, whichever exception the ASGI layer happens to choose for it."""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)

BEARER_SUBPROTOCOL = "bearer"
"""The marker with which a browser WebSocket sends its token along in the
handshake (`new WebSocket(url, ["bearer", token])`).

The ONE definition of this value on the server side: `loxone.server`
imports it (via `api.live`, which passes it on from here) for reading it
out (`build_api_guard`), `accepted_subprotocol` below uses it for the
response side - the chosen subprotocol must come back in the accept. Two
separate constants in two modules could drift apart without either one
looking wrong on its own. Public (no underscore) because `loxone.server`
and `tests/api/test_web.py` genuinely need it from outside."""

QUEUE_MAXSIZE = 512
"""Upper bound of the queue per WebSocket connection (review fix
Important #1, 2026-09-02).

Must absorb a full resend burst without complaint: `/resync` (Spec 6.4)
resends every known value via `Runtime.resend_all()`, and a single device
like the test suite's IKEA plug already comes to around 110 datagrams on
its own - with several devices on the same bridge process this adds up.
512 leaves ample headroom for that (more than four times the single-device
burst), without a permanently hanging connection holding more than a few
hundred small tuples in memory."""


class BoundedQueue:
    """Queue with a fixed upper bound for a single WebSocket connection -
    on overflow, drops the OLDEST entry, not the newest (see module
    docstring, review fix Important #1).

    `put` runs synchronously in the observer call path (`_notify_observers`)
    and must therefore never block or throw: `queue.full()`, `get_nowait()`
    and `put_nowait()` contain no `await` and thus run atomically within
    ONE step of the event loop - no other task (in particular not
    `send_loop`, which reads via `get()`) can interleave.

    Carries ONE payload object (`dict[str, object]`) instead of a fixed
    `(key, value)` pair: different channels (values stream, diagnostics
    feed) send different kinds of messages, a hard-wired pair no longer
    fits both.

    `connection_label` only serves the log below: it makes a hanging
    connection discoverable in operation (e.g. `('192.168.1.5', 54321)`)."""

    def __init__(self, maxsize: int, connection_label: str) -> None:
        self._queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=maxsize)
        self._maxsize = maxsize
        self._connection_label = connection_label
        self._dropping = False

    def put(self, payload: dict[str, object]) -> None:
        """Enqueues, dropping the OLDEST entry on overflow (see class
        docstring).

        **Conditionally dangerous for an `/api/diagnostics/live` connection
        whose `log_handler` branch is wired up** (follow-up Task 7,
        Fix 3b). The `logger.debug(...)` below runs on the loop thread,
        OUTSIDE `LogBufferHandler.emit()` - so without its reentrancy lock
        (see the `diagnostics.logbuffer` module docstring). If the logger
        `loxmatter.api.streaming` ever ran at DEBUG, the transition into
        dropping would produce a new log line that - if `on_log` is
        registered for the same connection - would be enqueued again via
        this very `put`: a dropped log line produces a new one that gets
        enqueued again. Unreachable today, because `install_log_buffer()`
        keeps the `loxmatter` logger at INFO and nothing in the project
        offers a DEBUG switch (see design, section 7, point 2) - the
        `_dropping` state above limits the re-enqueuing even then to a
        single nested `put` call, not unbounded recursion, but a later
        DEBUG switch should be aware of this spot."""
        if self._queue.full():
            self._queue.get_nowait()  # discard the oldest entry, make room for the newest
            if not self._dropping:
                # Only log on the TRANSITION, not on every further drop -
                # a permanently full connection should be discoverable in
                # the log, not flood the log itself (review fix Important
                # #1: "Log at debug level when dropping starts").
                self._dropping = True
                logger.debug(
                    "WebSocket connection %s has stopped reading - queue "
                    "(%d entries) is full, dropping oldest values",
                    self._connection_label,
                    self._maxsize,
                )
        else:
            self._dropping = False
        self._queue.put_nowait(payload)

    async def get(self) -> dict[str, object]:
        return await self._queue.get()


async def watch_for_disconnect(websocket: WebSocket) -> None:
    """Ends via the `WebSocketDisconnect` thrown by `receive_text` as soon
    as the client closes the connection - see module docstring."""
    while True:
        await websocket.receive_text()


async def send_loop(websocket: WebSocket, queue: BoundedQueue) -> None:
    """Pumps the observer's queue onto the WebSocket wire."""
    while True:
        payload = await queue.get()
        try:
            await websocket.send_json(payload)
        except RuntimeError:
            # Review fix Important #2, 2026-09-02: some ASGI servers throw
            # not a `WebSocketDisconnect` but a `RuntimeError` on a send
            # attempt to an already-closed connection (see module
            # docstring). For this route that is the same case as a normal
            # `WebSocketDisconnect`: a browser tab that is gone, not a
            # program bug - so `logger.debug`, not `logger.error`, and the
            # loop ends cleanly instead of raising.
            logger.debug(
                "WebSocket connection lost during send - treating it as a disconnect",
                exc_info=True,
            )
            return


def accepted_subprotocol(websocket: WebSocket) -> str | None:
    """The chosen subprotocol for `websocket.accept(subprotocol=...)`.

    MUST come back in the accept, otherwise the browser aborts the
    handshake per RFC 6455 (review fix Fix 1c, 2026-09-03). `app.js`
    connects with `new WebSocket(url, ["bearer", token])` when a token is
    set - that is the only channel through which a browser WebSocket gets
    a secret into the handshake (see `loxone.server.build_api_guard`,
    which reads it out there). Only the marker `bearer` is echoed, NEVER
    the second value: that is the token, and a server that mirrors it back
    in the accept header would write it into every proxy's and browser's
    log along the way.

    Only echo if the client actually offered the marker: a subprotocol
    that the client did not have in its list is likewise a handshake error
    per RFC 6455 - a connection without a token (no token set, or another
    client such as `websockets` with a real `Authorization` header) must
    therefore still be accepted without a subprotocol."""
    offered: list[str] = websocket.scope.get("subprotocols", [])
    return BEARER_SUBPROTOCOL if BEARER_SUBPROTOCOL in offered else None
