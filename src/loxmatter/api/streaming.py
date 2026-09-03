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

"""Die WebSocket-Mechanik, die sich mehrere Live-Routen teilen (Task 1,
Herausloesung aus `api.live`): eine Warteschlange pro Verbindung, das
Bemerken einer Trennung und die Aushandlung des Subprotokolls fuer das
Token im Handshake. `api.live` (die Werte-Route) war der erste Nutzer;
ein zweiter Kanal (Diagnose-Feed) kommt hinzu, ohne diese Mechanik ein
zweites Mal zu bauen - genau das hat die erste Korrektur daran (die
unbegrenzte Warteschlange, Review-Fix Phase 5) schon einmal noetig
gemacht, als es nur einen Nutzer gab.

**Die Warteschlange ist begrenzt (Review-Fix Important #1, 2026-09-02).**
Diese Bruecke laeuft wochenlang unbeaufsichtigt in jemandes Zuhause, nicht
als anfragegebundener Webserver - ein Browser-Tab im Hintergrund oder ein
eingeschlafenes Laptop, der/das nicht mehr liest, ist dort keine
Ausnahme, sondern Alltag. Eine unbegrenzte Warteschlange wuerde in diesem
Fall unbegrenzt wachsen. `BoundedQueue` unten deckelt sie deshalb bei
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
veralteten Anzeige. Das Debug-Log unten (siehe `BoundedQueue.put`) macht
eine haengende Verbindung trotzdem auffindbar, ohne dieses Risiko
einzugehen.

**Ein getrennter Client wird zuverlaessig bemerkt.** Eine reine Sende-Route
erwartet selbst keine eingehenden Nachrichten - trotzdem laeuft
`watch_for_disconnect` nebenher und ruft `websocket.receive_text()` in
einer Schleife auf. Grund: nach ASGI-Spezifikation liefert der Server das
`websocket.disconnect`-Ereignis nur ueber `receive()` aus - eine Route, die
nur sendet und nie empfaengt, wuerde einen geschlossenen Browser-Tab nie
bemerken und ihren Beobachter fuer immer angemeldet lassen. `asyncio.wait(
..., return_when=FIRST_COMPLETED)` im Aufrufer laesst die Route reagieren,
sobald einer der beiden Teil-Tasks endet - Trennung ODER ein Sendefehler -,
und raeumt den jeweils anderen sauber ab.

**Das Token reist hier im Subprotokoll, nicht im Header (Review-Fix Fix 1c,
2026-09-03).** Die Browser-`WebSocket`-API kennt keinen Parameter fuer eigene
Header - `Authorization` ist bei diesen Routen also unmoeglich. `app.js`
verbindet sich deshalb mit `new WebSocket(url, ["bearer", token])`, was der
Browser als `Sec-WebSocket-Protocol: bearer, <Token>` sendet;
`loxone.server.build_api_guard` liest das Token dort aus, und
`accepted_subprotocol` unten gibt den Marker `bearer` im Accept zurueck (nie
das Token selbst), weil der Browser den Handshake nach RFC 6455 sonst
abbricht.

Ein `WebSocketDisconnect` ist der Normalfall - ein Browser-Tab, der
geschlossen oder neu geladen wird - kein Fehler, und schreibt deshalb
nichts ins Log. **Ein blosses `WebSocketDisconnect` reicht aber nicht
(Review-Fix Important #2, 2026-09-02):** haengt ein `send_json`-Aufruf in
`send_loop` genau dann, wenn der Client trennt, wirft die ASGI-Schicht -
je nach Server - manchmal kein `WebSocketDisconnect`, sondern ein
`RuntimeError` ueber eine bereits geschlossene Verbindung. `send_loop`
faengt das direkt am Sendepunkt ab und behandelt es wie eine normale
Trennung: Debug-Log statt `logger.error`, Beobachter wird trotzdem
abgemeldet (das bleibt Sache des Aufrufers, siehe dessen `finally`) - ein
geschlossener Browser-Tab ist kein Programmfehler, gleich welche Exception
die ASGI-Schicht dafuer gerade waehlt."""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)

BEARER_SUBPROTOCOL = "bearer"
"""Der Marker, mit dem ein Browser-WebSocket sein Token im Handshake
mitschickt (`new WebSocket(url, ["bearer", token])`).

Die EINE Definition dieses Wertes auf der Serverseite: `loxone.server`
importiert ihn (ueber `api.live`, das ihn von hier weiterreicht) fuer das
Auslesen (`build_api_guard`), `accepted_subprotocol` unten benutzt ihn fuer
die Antwortseite - das gewaehlte Subprotokoll muss im Accept zurueckkommen.
Zwei eigene Konstanten in zwei Modulen koennten auseinanderlaufen, ohne dass
eine davon fuer sich falsch aussaehe. Oeffentlich (ohne Unterstrich), weil
`loxone.server` und `tests/api/test_web.py` ihn tatsaechlich von aussen
brauchen."""

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


class BoundedQueue:
    """Warteschlange mit fester Obergrenze fuer eine einzelne
    WebSocket-Verbindung - wirft bei Ueberlauf den AELTESTEN Eintrag weg,
    nicht den neuesten (siehe Modul-Docstring, Review-Fix Important #1).

    `put` laeuft synchron im Beobachter-Aufrufpfad (`_notify_observers`) und
    darf deshalb nie blockieren oder werfen: `queue.full()`, `get_nowait()`
    und `put_nowait()` enthalten keinen `await` und laufen damit atomar
    innerhalb EINES Schritts der Event-Loop - kein anderer Task (insb. nicht
    `send_loop`, der ueber `get()` liest) kann dazwischenfunken.

    Traegt EIN Nutzlast-Objekt (`dict[str, object]`) statt eines festen
    `(key, value)`-Zweiertupels: verschiedene Kanaele (Werte-Stream,
    Diagnose-Feed) schicken verschiedene Nachrichtenarten, ein fest
    verdrahtetes Zweiertupel passt nicht mehr fuer beide.

    `connection_label` dient nur dem Log unten: er macht eine haengende
    Verbindung im Betrieb auffindbar (z. B. `('192.168.1.5', 54321)`)."""

    def __init__(self, maxsize: int, connection_label: str) -> None:
        self._queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=maxsize)
        self._maxsize = maxsize
        self._connection_label = connection_label
        self._dropping = False

    def put(self, payload: dict[str, object]) -> None:
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
        self._queue.put_nowait(payload)

    async def get(self) -> dict[str, object]:
        return await self._queue.get()


async def watch_for_disconnect(websocket: WebSocket) -> None:
    """Endet ueber die von `receive_text` geworfene `WebSocketDisconnect`,
    sobald der Client die Verbindung schliesst - siehe Modul-Docstring."""
    while True:
        await websocket.receive_text()


async def send_loop(websocket: WebSocket, queue: BoundedQueue) -> None:
    """Pumpt die Warteschlange des Beobachters auf die WebSocket-Leitung."""
    while True:
        payload = await queue.get()
        try:
            await websocket.send_json(payload)
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


def accepted_subprotocol(websocket: WebSocket) -> str | None:
    """Das gewaehlte Subprotokoll fuer `websocket.accept(subprotocol=...)`.

    MUSS im Accept zurueckkommen, sonst bricht der Browser den Handshake
    nach RFC 6455 ab (Review-Fix Fix 1c, 2026-09-03). `app.js` verbindet
    sich mit `new WebSocket(url, ["bearer", token])`, wenn ein Token gesetzt
    ist - das ist der einzige Kanal, ueber den ein Browser-WebSocket ein
    Geheimnis in den Handshake bekommt (siehe
    `loxone.server.build_api_guard`, der es dort ausliest). Echoed wird
    ausschliesslich der Marker `bearer`, NIE der zweite Wert: der ist das
    Token, und ein Server, der es im Accept-Header zurueckspiegelt, schriebe
    es in jedes Proxy- und Browser-Protokoll auf dem Weg.

    Nur echoen, wenn der Client den Marker auch angeboten hat: ein
    Subprotokoll, das der Client nicht in seiner Liste hatte, ist nach
    RFC 6455 ebenso ein Handshake-Fehler - eine Verbindung ohne Token (kein
    Token gesetzt, oder ein anderer Client wie `websockets` mit echtem
    `Authorization`-Header) muss deshalb weiterhin ohne Subprotokoll
    angenommen werden."""
    offered: list[str] = websocket.scope.get("subprotocols", [])
    return BEARER_SUBPROTOCOL if BEARER_SUBPROTOCOL in offered else None
