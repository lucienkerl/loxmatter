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

"""WebSocket for live values of the WebUI (Spec 8.3).

`build_live_router` builds an `APIRouter` with the route `GET /api/live` -
a WebSocket, not a REST endpoint. Every connection registers with
`Runtime.add_observer` and deregisters again on disconnect (`Runtime.
remove_observer`) - the same subscription that also feeds the UDP sender
(see `loxone.runtime.Runtime`). No second path, no polling: what arrives
here is literally the same key/value that `Runtime` has already sent to
Loxone.

**The observer never blocks.** A dedicated queue per connection decouples
the observer call - which runs synchronously and in-line in the call path of
`Runtime.on_attribute`/`on_event`/`set_online`, see `_notify_observers`
there - from the actual delivery over the WebSocket, which is asynchronous
and could wait on a slow or hanging browser tab. The observer itself
therefore only does `queue.put(...)` - which cannot block -, and
`streaming.send_loop` pumps the queue onto the wire, in its own task. If the
observer instead called `await websocket.send_json(...)` directly, a browser
tab that stops reading (tab in the background, network gone, laptop asleep)
would ultimately hang the UDP bridge itself - exactly the class of failure
that Spec 8.3's "same subscription... no second path" is meant to rule out
not only a duplicate read path but also a shared blocking path.

The queue, disconnect and subprotocol mechanics themselves (bounded size,
drop-oldest, noticing a disconnect, echoing the bearer marker) were factored
out into `api.streaming` by Task 1, because a second channel (diagnostics
feed) needs them unchanged - see there for the full rationale."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from loxmatter.api.streaming import (
    BEARER_SUBPROTOCOL,
    QUEUE_MAXSIZE,
    BoundedQueue,
    accepted_subprotocol,
    send_loop,
    watch_for_disconnect,
)

__all__ = ["BEARER_SUBPROTOCOL", "ObservableRuntime", "build_live_router"]
"""`BEARER_SUBPROTOCOL` only passes through here: `loxone.server` still
imports it from here (see its definition in `api.streaming`), left
unchanged so as not to alter that import. `__all__` turns this into an
explicit re-export instead of an implicit one (mypy strict requires that),
without the unused `as X` alias that Ruff (PLC0414) flags."""

Observer = Callable[[str, object], None]


class ObservableRuntime(Protocol):
    """What this route needs from `runtime` - `loxone.runtime.Runtime`
    already satisfies this unchanged."""

    def add_observer(self, callback: Observer) -> None: ...

    def remove_observer(self, callback: Observer) -> None: ...


def build_live_router(runtime: ObservableRuntime) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.websocket("/live")
    async def live(websocket: WebSocket) -> None:
        # Subprotocol negotiation and queue are shared mechanics, see
        # `api.streaming` for the rationale (review fix Fix 1c / Important
        # #1, documented there verbatim).
        subprotocol = accepted_subprotocol(websocket)
        await websocket.accept(subprotocol=subprotocol)
        queue = BoundedQueue(QUEUE_MAXSIZE, connection_label=str(websocket.client))

        def observer(key: str, value: object) -> None:
            queue.put({"key": key, "value": value})

        runtime.add_observer(observer)
        watcher = asyncio.create_task(watch_for_disconnect(websocket))
        pump = asyncio.create_task(send_loop(websocket, queue))
        try:
            done, pending = await asyncio.wait({watcher, pump}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
        except WebSocketDisconnect:
            pass
        finally:
            runtime.remove_observer(observer)

    return router
