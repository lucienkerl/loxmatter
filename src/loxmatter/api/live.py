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

"""WebSocket fuer Live-Werte der WebUI (Spec 8.3).

`build_live_router` baut einen `APIRouter` mit der Route `GET /api/live` -
ein WebSocket, kein REST-Endpunkt. Jede Verbindung meldet sich bei
`Runtime.add_observer` an und beim Trennen wieder ab (`Runtime.
remove_observer`) - dieselbe Subscription, die auch den UDP-Sender speist
(siehe `loxone.runtime.Runtime`). Kein zweiter Pfad, kein Polling: was hier
ankommt, ist wortwoertlich derselbe Schluessel/Wert, den `Runtime` bereits an
Loxone geschickt hat.

**Der Beobachter blockiert nie.** Eine eigene Warteschlange pro Verbindung
entkoppelt den Beobachter-Aufruf - der synchron und in-line im Aufrufpfad von
`Runtime.on_attribute`/`on_event`/`set_online` laeuft, siehe dort
`_notify_observers` - vom eigentlichen Versand ueber den WebSocket, der
asynchron ist und auf einen langsamen oder haengenden Browser-Tab warten
koennte. Der Beobachter selbst tut deshalb nur `queue.put(...)` - das kann
nicht blockieren -, und `streaming.send_loop` pumpt die Warteschlange auf die
Leitung, in einem eigenen Task. Wuerde der Beobachter stattdessen direkt
`await websocket.send_json(...)` aufrufen, haenge ein Browser-Tab, der nicht
mehr liest (Tab im Hintergrund, Netz weg, Laptop im Schlaf), am Ende die
UDP-Bruecke selbst auf - genau die Klasse Fehler, vor der Spec 8.3 mit
"dieselbe Subscription... kein zweiter Pfad" nicht nur einen doppelten
Lesepfad, sondern auch einen gemeinsamen Blockierpfad ausschliessen soll.

Die Warteschlangen-, Trennungs- und Subprotokoll-Mechanik selbst (begrenzte
Groesse, Drop-Oldest, das Bemerken einer Trennung, das Echoen des
Bearer-Markers) ist Task 1 nach `api.streaming` herausgeloest, weil ein
zweiter Kanal (Diagnose-Feed) sie unveraendert braucht - siehe dort fuer
die vollstaendige Begruendung."""

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
"""`BEARER_SUBPROTOCOL` reist hier nur durch: `loxone.server` importiert sie
weiterhin von hier (siehe deren Definition in `api.streaming`), nicht
umgestellt, um dessen Import unveraendert zu lassen. `__all__` macht daraus
einen expliziten Re-Export statt eines impliziten (mypy strict verlangt
das), ohne den ungenutzten `as X`-Alias, den Ruff (PLC0414) beanstandet."""

Observer = Callable[[str, object], None]


class ObservableRuntime(Protocol):
    """Was diese Route von `runtime` braucht - `loxone.runtime.Runtime`
    erfuellt das bereits unveraendert."""

    def add_observer(self, callback: Observer) -> None: ...

    def remove_observer(self, callback: Observer) -> None: ...


def build_live_router(runtime: ObservableRuntime) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.websocket("/live")
    async def live(websocket: WebSocket) -> None:
        # Subprotokoll-Aushandlung und Warteschlange sind gemeinsame
        # Mechanik, siehe `api.streaming` fuer die Begruendung (Review-Fix
        # Fix 1c / Important #1, wortwoertlich dort dokumentiert).
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
