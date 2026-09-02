"""WebSocket fuer Live-Werte der WebUI (Spec 8.3).

`build_live_router` baut einen `APIRouter` mit der Route `GET /api/live` -
ein WebSocket, kein REST-Endpunkt. Jede Verbindung meldet sich bei
`Runtime.add_observer` an und beim Trennen wieder ab (`Runtime.
remove_observer`) - dieselbe Subscription, die auch den UDP-Sender speist
(siehe `loxone.runtime.Runtime`). Kein zweiter Pfad, kein Polling: was hier
ankommt, ist wortwoertlich derselbe Schluessel/Wert, den `Runtime` bereits an
Loxone geschickt hat.

**Der Beobachter blockiert nie.** Eine eigene, unbegrenzte Warteschlange pro
Verbindung entkoppelt den Beobachter-Aufruf - der synchron und in-line im
Aufrufpfad von `Runtime.on_attribute`/`on_event`/`set_online` laeuft, siehe
dort `_notify_observers` - vom eigentlichen Versand ueber den WebSocket, der
asynchron ist und auf einen langsamen oder haengenden Browser-Tab warten
koennte. Der Beobachter selbst tut deshalb nur `queue.put_nowait(...)` - das
kann nicht blockieren -, und `_send_loop` unten pumpt die Warteschlange auf
die Leitung, in einem eigenen Task. Wuerde der Beobachter stattdessen direkt
`await websocket.send_json(...)` aufrufen, haenge ein Browser-Tab, der nicht
mehr liest (Tab im Hintergrund, Netz weg, Laptop im Schlaf), am Ende die
UDP-Bruecke selbst auf - genau die Klasse Fehler, vor der Spec 8.3 mit
"dieselbe Subscription... kein zweiter Pfad" nicht nur einen doppelten
Lesepfad, sondern auch einen gemeinsamen Blockierpfad ausschliessen soll.

**Ein getrennter Client wird zuverlaessig bemerkt.** Diese Route erwartet
selbst keine eingehenden Nachrichten - trotzdem laeuft `_watch_for_disconnect`
nebenher und ruft `websocket.receive_text()` in einer Schleife auf. Grund:
nach ASGI-Spezifikation liefert der Server das `websocket.disconnect`-
Ereignis nur ueber `receive()` aus - eine Route, die (wie hier) nur sendet
und nie empfaengt, wuerde einen geschlossenen Browser-Tab nie bemerken und
ihren Beobachter fuer immer angemeldet lassen. `asyncio.wait(...,
return_when=FIRST_COMPLETED)` laesst die Route reagieren, sobald einer der
beiden Teil-Tasks endet - Trennung ODER (theoretisch) ein Sendefehler -, und
raeumt den jeweils anderen sauber ab.

Ein `WebSocketDisconnect` ist der Normalfall - ein Browser-Tab, der
geschlossen oder neu geladen wird - kein Fehler, und schreibt deshalb
nichts ins Log."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Protocol

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

Observer = Callable[[str, object], None]


class ObservableRuntime(Protocol):
    """Was diese Route von `runtime` braucht - `loxone.runtime.Runtime`
    erfuellt das bereits unveraendert."""

    def add_observer(self, callback: Observer) -> None: ...

    def remove_observer(self, callback: Observer) -> None: ...


async def _watch_for_disconnect(websocket: WebSocket) -> None:
    """Endet ueber die von `receive_text` geworfene `WebSocketDisconnect`,
    sobald der Client die Verbindung schliesst - siehe Modul-Docstring."""
    while True:
        await websocket.receive_text()


async def _send_loop(websocket: WebSocket, queue: asyncio.Queue[tuple[str, object]]) -> None:
    """Pumpt die Warteschlange des Beobachters auf die WebSocket-Leitung."""
    while True:
        key, value = await queue.get()
        await websocket.send_json({"key": key, "value": value})


def build_live_router(runtime: ObservableRuntime) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.websocket("/live")
    async def live(websocket: WebSocket) -> None:
        await websocket.accept()
        # Unbegrenzt bewusst: eine Grenze wuerde bedeuten, dass ein
        # langsamer Client irgendwann `put_nowait` zum Werfen bringt - und
        # damit wieder im Beobachter-Pfad landet, den `_notify_observers`
        # bereits gegen genau das absichert (siehe Modul-Docstring).
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

        def observer(key: str, value: object) -> None:
            queue.put_nowait((key, value))

        runtime.add_observer(observer)
        watcher = asyncio.create_task(_watch_for_disconnect(websocket))
        pump = asyncio.create_task(_send_loop(websocket, queue))
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
