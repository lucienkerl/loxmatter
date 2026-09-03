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
nicht blockieren -, und `_send_loop` unten pumpt die Warteschlange auf die
Leitung, in einem eigenen Task. Wuerde der Beobachter stattdessen direkt
`await websocket.send_json(...)` aufrufen, haenge ein Browser-Tab, der nicht
mehr liest (Tab im Hintergrund, Netz weg, Laptop im Schlaf), am Ende die
UDP-Bruecke selbst auf - genau die Klasse Fehler, vor der Spec 8.3 mit
"dieselbe Subscription... kein zweiter Pfad" nicht nur einen doppelten
Lesepfad, sondern auch einen gemeinsamen Blockierpfad ausschliessen soll.

**Die Warteschlange ist begrenzt (Review-Fix Important #1, 2026-09-02).**
Diese Bruecke laeuft wochenlang unbeaufsichtigt in jemandes Zuhause, nicht
als anfragegebundener Webserver - ein Browser-Tab im Hintergrund oder ein
eingeschlafenes Laptop, der/das nicht mehr liest, ist dort keine
Ausnahme, sondern Alltag. Eine unbegrenzte Warteschlange wuerde in diesem
Fall unbegrenzt wachsen. `_BoundedQueue` unten deckelt sie deshalb bei
`QUEUE_MAXSIZE` und wirft bei Ueberlauf den AELTESTEN Eintrag weg, nicht den
neuesten - eine Live-Ansicht will den aktuellsten Stand, der veraltete
Eintrag ist der verzichtbare. Warum genau `QUEUE_MAXSIZE`, siehe dort.

Bewusst NICHT umgesetzt: die Verbindung aktiv zu trennen, wenn sie dauerhaft
voll bleibt. Die Begrenzung oben deckelt bereits die einzige Gefahr, die der
Review benannt hat (unbegrenztes Wachstum) - eine dauerhaft volle
Warteschlange kostet jetzt nur noch die feste, kleine Groesse von
`QUEUE_MAXSIZE` Eintraegen, kein wachsendes Problem mehr. Ein aktives
Trennen braeuchte eine eigene, gut begruendete Zeitschwelle ("wie viele
Minuten ohne Fortschritt gelten als tot?") - eine falsch gewaehlte Schwelle
wuerfe eine Sitzung raus, die nur kurz durch OS-Drosselung eines
Hintergrund-Tabs ins Stocken kam, und Live-Werte sind reine Anzeige: ein
paar verpasste Zwischenwerte haben keine Folgen ausser einer kurzzeitig
veralteten Anzeige. Das Debug-Log unten (siehe `_BoundedQueue.put`) macht
eine haengende Verbindung trotzdem auffindbar, ohne dieses Risiko
einzugehen.

**Ein getrennter Client wird zuverlaessig bemerkt.** Diese Route erwartet
selbst keine eingehenden Nachrichten - trotzdem laeuft `_watch_for_disconnect`
nebenher und ruft `websocket.receive_text()` in einer Schleife auf. Grund:
nach ASGI-Spezifikation liefert der Server das `websocket.disconnect`-
Ereignis nur ueber `receive()` aus - eine Route, die (wie hier) nur sendet
und nie empfaengt, wuerde einen geschlossenen Browser-Tab nie bemerken und
ihren Beobachter fuer immer angemeldet lassen. `asyncio.wait(...,
return_when=FIRST_COMPLETED)` laesst die Route reagieren, sobald einer der
beiden Teil-Tasks endet - Trennung ODER ein Sendefehler -, und raeumt den
jeweils anderen sauber ab.

**Das Token reist hier im Subprotokoll, nicht im Header (Review-Fix Fix 1c,
2026-09-03).** Die Browser-`WebSocket`-API kennt keinen Parameter fuer eigene
Header - `Authorization` ist bei dieser einen Route also unmoeglich. `app.js`
verbindet sich deshalb mit `new WebSocket(url, ["bearer", token])`, was der
Browser als `Sec-WebSocket-Protocol: bearer, <Token>` sendet;
`loxone.server.build_api_guard` liest das Token dort aus, und `live()` unten
gibt den Marker `bearer` im Accept zurueck (nie das Token selbst), weil der
Browser den Handshake nach RFC 6455 sonst abbricht.

Ein `WebSocketDisconnect` ist der Normalfall - ein Browser-Tab, der
geschlossen oder neu geladen wird - kein Fehler, und schreibt deshalb
nichts ins Log. **Ein blosses `WebSocketDisconnect` reicht aber nicht
(Review-Fix Important #2, 2026-09-02):** haengt ein `send_json`-Aufruf in
`_send_loop` genau dann, wenn der Client trennt, wirft die ASGI-Schicht -
je nach Server - manchmal kein `WebSocketDisconnect`, sondern ein
`RuntimeError` ueber eine bereits geschlossene Verbindung. `_send_loop`
faengt das direkt am Sendepunkt ab und behandelt es wie eine normale
Trennung: Debug-Log statt `logger.error`, Beobachter wird trotzdem
abgemeldet (siehe `finally` in `live()` unten) - ein geschlossener
Browser-Tab ist kein Programmfehler, gleich welche Exception die
ASGI-Schicht dafuer gerade waehlt."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Protocol

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

Observer = Callable[[str, object], None]

BEARER_SUBPROTOCOL = "bearer"
"""Der Marker, mit dem ein Browser-WebSocket sein Token im Handshake
mitschickt (`new WebSocket(url, ["bearer", token])`).

Die EINE Definition dieses Wertes auf der Serverseite: `loxone.server`
importiert ihn von hier fuer das Auslesen (`build_api_guard`), `live()`
unten benutzt ihn fuer die Antwortseite - das gewaehlte Subprotokoll muss
im Accept zurueckkommen. Zwei eigene Konstanten in zwei Modulen koennten
auseinanderlaufen, ohne dass eine davon fuer sich falsch aussaehe.
Oeffentlich (ohne Unterstrich), weil `loxone.server` und
`tests/api/test_web.py` ihn tatsaechlich von aussen brauchen."""

QUEUE_MAXSIZE = 512
"""Obergrenze der Warteschlange je WebSocket-Verbindung (Review-Fix
Important #1, 2026-09-02).

Muss einen vollen Resend-Burst klaglos aufnehmen: `/resync` (Spec 6.4)
verschickt mit `Runtime.resend_all()` jeden bekannten Wert neu, und schon
ein einzelnes Geraet wie der IKEA-Stecker der Testsuite kommt dabei auf
rund 110 Datagramme - bei mehreren Geraeten am selben Bruecken-Prozess
addiert sich das. 512 laesst dafuer reichlich Luft (mehr als das Vierfache
des Einzelgeraet-Bursts), ohne dass eine dauerhaft haengende Verbindung
mehr als ein paar hundert kleiner Tupel im Speicher haelt."""


class ObservableRuntime(Protocol):
    """Was diese Route von `runtime` braucht - `loxone.runtime.Runtime`
    erfuellt das bereits unveraendert."""

    def add_observer(self, callback: Observer) -> None: ...

    def remove_observer(self, callback: Observer) -> None: ...


class _BoundedQueue:
    """Warteschlange mit fester Obergrenze fuer eine einzelne
    WebSocket-Verbindung - wirft bei Ueberlauf den AELTESTEN Eintrag weg,
    nicht den neuesten (siehe Modul-Docstring, Review-Fix Important #1).

    `put` laeuft synchron im Beobachter-Aufrufpfad (`_notify_observers`) und
    darf deshalb nie blockieren oder werfen: `queue.full()`, `get_nowait()`
    und `put_nowait()` enthalten keinen `await` und laufen damit atomar
    innerhalb EINES Schritts der Event-Loop - kein anderer Task (insb. nicht
    `_send_loop`, der ueber `get()` liest) kann dazwischenfunken.

    `connection_label` dient nur dem Log unten: er macht eine haengende
    Verbindung im Betrieb auffindbar (z. B. `('192.168.1.5', 54321)`)."""

    def __init__(self, maxsize: int, connection_label: str) -> None:
        self._queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue(maxsize=maxsize)
        self._maxsize = maxsize
        self._connection_label = connection_label
        self._dropping = False

    def put(self, key: str, value: object) -> None:
        if self._queue.full():
            self._queue.get_nowait()  # aeltesten Eintrag verwerfen, Platz fuer den neuesten schaffen
            if not self._dropping:
                # Nur beim UEBERGANG loggen, nicht bei jedem weiteren Verwurf
                # - eine dauerhaft volle Verbindung soll im Log auffindbar
                # sein, nicht das Log selbst fluten (Review-Fix Important
                # #1: "Log at debug level when dropping starts").
                self._dropping = True
                logger.debug(
                    "WebSocket-Verbindung %s liest nicht mehr mit - Warteschlange "
                    "(%d Eintraege) ist voll, aelteste Werte werden verworfen",
                    self._connection_label,
                    self._maxsize,
                )
        else:
            self._dropping = False
        self._queue.put_nowait((key, value))

    async def get(self) -> tuple[str, object]:
        return await self._queue.get()


async def _watch_for_disconnect(websocket: WebSocket) -> None:
    """Endet ueber die von `receive_text` geworfene `WebSocketDisconnect`,
    sobald der Client die Verbindung schliesst - siehe Modul-Docstring."""
    while True:
        await websocket.receive_text()


async def _send_loop(websocket: WebSocket, queue: _BoundedQueue) -> None:
    """Pumpt die Warteschlange des Beobachters auf die WebSocket-Leitung."""
    while True:
        key, value = await queue.get()
        try:
            await websocket.send_json({"key": key, "value": value})
        except RuntimeError:
            # Review-Fix Important #2, 2026-09-02: manche ASGI-Server werfen
            # bei einem Sendeversuch auf eine bereits geschlossene Verbindung
            # kein `WebSocketDisconnect`, sondern ein `RuntimeError` (siehe
            # Modul-Docstring). Fuer diese Route ist das derselbe Fall wie
            # ein normaler `WebSocketDisconnect`: ein Browser-Tab, der weg
            # ist, kein Programmfehler - also `logger.debug`, nicht
            # `logger.error`, und die Schleife endet sauber statt zu werfen.
            logger.debug(
                "WebSocket-Verbindung beim Versand verloren - wird wie eine Trennung behandelt",
                exc_info=True,
            )
            return


def build_live_router(runtime: ObservableRuntime) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.websocket("/live")
    async def live(websocket: WebSocket) -> None:
        # Das gewaehlte Subprotokoll MUSS im Accept zurueckkommen, sonst
        # bricht der Browser den Handshake nach RFC 6455 ab (Review-Fix
        # Fix 1c, 2026-09-03). `app.js` verbindet sich mit
        # `new WebSocket(url, ["bearer", token])`, wenn ein Token gesetzt
        # ist - das ist der einzige Kanal, ueber den ein Browser-WebSocket
        # ein Geheimnis in den Handshake bekommt (siehe
        # `loxone.server.build_api_guard`, der es dort ausliest). Echoed wird
        # ausschliesslich der Marker `bearer`, NIE der zweite Wert: der ist
        # das Token, und ein Server, der es im Accept-Header zurueckspiegelt,
        # schriebe es in jedes Proxy- und Browser-Protokoll auf dem Weg.
        #
        # Nur echoen, wenn der Client den Marker auch angeboten hat: ein
        # Subprotokoll, das der Client nicht in seiner Liste hatte, ist nach
        # RFC 6455 ebenso ein Handshake-Fehler - eine Verbindung ohne Token
        # (kein Token gesetzt, oder ein anderer Client wie `websockets` mit
        # echtem `Authorization`-Header) muss deshalb weiterhin ohne
        # Subprotokoll angenommen werden.
        offered: list[str] = websocket.scope.get("subprotocols", [])
        subprotocol = BEARER_SUBPROTOCOL if BEARER_SUBPROTOCOL in offered else None
        await websocket.accept(subprotocol=subprotocol)
        queue = _BoundedQueue(QUEUE_MAXSIZE, connection_label=str(websocket.client))

        def observer(key: str, value: object) -> None:
            queue.put(key, value)

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
