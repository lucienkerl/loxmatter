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

"""WebSocket fuer den Diagnose-Livestream der WebUI (Task 4, Phase 5, Spec 10.5).

Die Systemseite holt Logs, UDP-Mitschnitt und Kommando-Log heute (Task 6)
nur einmalig beim Oeffnen ab - `GET /api/diagnostics/{datagrams,commands}`
und, sobald verdrahtet, ein Logs-Aequivalent. Diese Datei baut die laufende
Variante: EIN WebSocket, `/api/diagnostics/live`, der alle drei Quellen
gemeinsam schiebt, statt dass die Oberflaeche pollt.

`build_diagnostics_live_router` folgt bewusst demselben Muster wie
`api.live.build_live_router` (dort ausfuehrlich begruendet, hier nicht
wiederholt): eine begrenzte Warteschlange pro Verbindung
(`api.streaming.BoundedQueue`) entkoppelt den (synchron laufenden)
Beobachter-Aufruf vom (asynchronen) Versand, `watch_for_disconnect` und
`send_loop` laufen nebeneinander, das Subprotokoll traegt bei Bedarf das
Token. Neu gegenueber `api.live` ist einzig, dass hier DREI Quellen statt
einer angezapft werden - mit drei unabhaengigen, optionalen Beobachterketten
statt einer.

**Drei Quellen, drei Vertraege, ein gemeinsames Nachrichtenformat.** Jede
Nachricht traegt `kind` (`"datagram"`, `"command"` oder `"log"`) und
GENAU die Felder des jeweiligen Eintragstyps aus
`api.diagnostics.DatagramLogEntry`, `api.diagnostics.CommandLogEntry` bzw.
`diagnostics.logbuffer.LogEntry` - nicht neu erfundene Namen, sonst hiesse
dieselbe Angabe (z. B. der Zeitstempel) in zwei Antworten zweimal anders.

- **Datagramme:** `sender.add_datagram_observer` (Task 2) - sieht jedes
  TATSAECHLICH gesendete Datagramm, einschliesslich Full-Resend und
  Impulsende, die `Runtime`s eigene Beobachterkette (`api.live`) bewusst
  auslaesst (siehe dort). Genau deshalb haengt dieser Zweig am `sender`,
  nicht an `runtime` - ein zweiter `Runtime`-Beobachter waere eine
  ABWEICHENDE, nicht dieselbe Sicht.
- **Kommandos:** `command_log.add_observer` (Task 4, neu in
  `api.diagnostics.RingBuffer` - siehe dort fuer die Begruendung, warum die
  Beobachterkette am Ring selbst haengt und nicht an der Middleware
  `_record_command`: die Signatur dieser Funktion nimmt bereits den fertig
  gebauten Ring entgegen, `add_observer` darauf aufzurufen braucht keine
  weitere Kopplung an `loxone.server`, und `command_log` hat - anders als
  `UdpSender`/`LogBufferHandler` - keinen eigenen Besitzer-Typ, an dem eine
  Kette sonst haengen koennte).
- **Logs:** `log_handler.add_observer` (Task 3) - **NICHT jede Zeile**, wie
  dort dokumentiert (eine Zeile, die synchron AUS einem Beobachter heraus
  protokolliert wird, erreicht keinen Beobachter, landet aber im Ring). Der
  Beobachter darf laut Vertrag von `LogBufferHandler.add_observer` NICHT
  blockieren und muss zuegig zurueckkehren: er laeuft im Thread, der die
  Zeile erzeugt hat, unter `logging.Handler.lock` - ein wartender Beobachter
  koennte in einen Deadlock laufen (siehe dort). `queue.put(...)`
  (`BoundedQueue.put`, Task 1) ist genau dafuer gebaut: rein synchron, kein
  `await`, kann nicht blockieren.

**`sender` und `log_handler` sind optional** (siehe `build_app` in
`loxone.server`): `None` bedeutet "dieser Teil des Livestreams ist fuer
diesen Lauf nicht verfuegbar", nicht "die Route insgesamt fehlt" - fehlt
einer, entfaellt sein Zweig (kein Anmelden, kein Abmelden, keine
Momentaufnahme), die uebrigen beiden laufen unveraendert weiter.
`command_log` ist dagegen nicht optional: `loxone.server.build_app` legt
ihn immer an, unabhaengig von `sender`/`client`/`log_handler`.

**Die Momentaufnahme laeuft VOR dem Anmelden der Beobachter** - sonst
klaffte eine Luecke zwischen "einmal abrufen" und "ab jetzt zuhoeren", und
ein Eintrag, der genau dazwischen entsteht, ginge verloren (derselbe Grund,
aus dem `api.live` keine Momentaufnahme braucht: dort gibt es keine
Historie, nur den naechsten Wert). `SNAPSHOT_LIMIT` begrenzt sie je Strom -
siehe dort fuer die Begruendung der Zahl.

**Im `finally` werden alle DREI - beziehungsweise nur die tatsaechlich
angemeldeten - Beobachter wieder abgemeldet.** Ein `sender`/`log_handler`
von `None` bedeutet: dieser Zweig wurde nie angemeldet, also muss er auch
nicht abgemeldet werden - die Bedingung ist bei An- und Abmeldung
identisch, damit kein Zweig verwaist."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from loxmatter.api.diagnostics import CommandLogEntry, DatagramLogEntry, RingBuffer
from loxmatter.api.streaming import (
    QUEUE_MAXSIZE,
    BoundedQueue,
    accepted_subprotocol,
    send_loop,
    watch_for_disconnect,
)
from loxmatter.diagnostics.logbuffer import LogBufferHandler, LogEntry

if TYPE_CHECKING:
    # Ausschliesslich fuer Typannotationen - dieselbe Begruendung wie in
    # `api.diagnostics` (siehe dort): `from __future__ import annotations`
    # wertet Annotationen ohnehin nur als Zeichenketten aus, dieser Block
    # existiert einzig fuer mypy.
    from loxmatter.loxone.sender import UdpSender

__all__ = ["build_diagnostics_live_router"]

SNAPSHOT_LIMIT = 50
"""Obergrenze je Strom fuer die Momentaufnahme beim Verbindungsaufbau.

Jeder der drei Ringe fasst bis zu 500 Eintraege (`DATAGRAM_LOG_SIZE`,
`COMMAND_LOG_SIZE`, `LOG_BUFFER_SIZE`) - alle drei auf einen Schlag zu
schicken waeren beim Oeffnen der Ansicht 1500 Nachrichten, spuerbar sowohl
fuer die Verbindung als auch fuer eine Person, die die letzten Minuten
sehen will, nicht die letzten Stunden. 50 je Strom (150 insgesamt) reicht
dafuer bequem - ein Systemcheck, der etwas Aelteres braucht, hat weiterhin
die einmalig abrufbaren `GET /api/diagnostics/{datagrams,commands}`-Routen
mit ihren vollen 500 Eintraegen."""


def build_diagnostics_live_router(
    sender: UdpSender | None,
    command_log: RingBuffer[CommandLogEntry],
    log_handler: LogBufferHandler | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/diagnostics")

    @router.websocket("/live")
    async def live(websocket: WebSocket) -> None:
        # Subprotokoll-Aushandlung und Warteschlange sind gemeinsame
        # Mechanik, siehe `api.streaming` fuer die Begruendung.
        subprotocol = accepted_subprotocol(websocket)
        await websocket.accept(subprotocol=subprotocol)
        queue = BoundedQueue(QUEUE_MAXSIZE, connection_label=str(websocket.client))

        def on_datagram(entry: DatagramLogEntry) -> None:
            queue.put(
                {
                    "kind": "datagram",
                    "key": entry.key,
                    "value": entry.value,
                    "timestamp": entry.timestamp,
                }
            )

        def on_command(entry: CommandLogEntry) -> None:
            queue.put(
                {
                    "kind": "command",
                    "method": entry.method,
                    "path": entry.path,
                    "status": entry.status,
                    "timestamp": entry.timestamp,
                }
            )

        def on_log(entry: LogEntry) -> None:
            queue.put(
                {
                    "kind": "log",
                    "level": entry.level,
                    "logger": entry.logger,
                    "message": entry.message,
                    "timestamp": entry.timestamp,
                }
            )

        # Momentaufnahme VOR dem Anmelden der Beobachter (siehe
        # Moduldocstring) - `list(...)` je Ring, NIE eine blosse
        # `for`-Schleife ueber den Ring selbst: `command_log` und
        # `log_handler.entries` koennen waehrenddessen aus einem anderen
        # Pfad (HTTP-Middleware bzw. einem fremden Logging-Thread)
        # beschrieben werden, und `RingBuffer.__iter__` gibt einen lebenden
        # `deque`-Iterator zurueck, der bei einer Mutation waehrend der
        # Iteration mit `RuntimeError` abbricht (siehe dort).
        if sender is not None:
            for datagram_entry in list(sender.datagram_log)[-SNAPSHOT_LIMIT:]:
                on_datagram(datagram_entry)
        for command_entry in list(command_log)[-SNAPSHOT_LIMIT:]:
            on_command(command_entry)
        if log_handler is not None:
            for log_entry in list(log_handler.entries)[-SNAPSHOT_LIMIT:]:
                on_log(log_entry)

        if sender is not None:
            sender.add_datagram_observer(on_datagram)
        command_log.add_observer(on_command)
        if log_handler is not None:
            log_handler.add_observer(on_log)

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
            # Dieselbe Bedingung wie beim Anmelden oben - ein Zweig, der nie
            # angemeldet wurde (sender/log_handler ist None), darf auch
            # nicht abgemeldet werden.
            if sender is not None:
                sender.remove_datagram_observer(on_datagram)
            command_log.remove_observer(on_command)
            if log_handler is not None:
                log_handler.remove_observer(on_log)

    return router
