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

"""Der Log-Ring, aus dem die Systemseite der Oberflaeche ihre Zeilen bekommt.

Heute gibt es dafuer ueberhaupt keine Erfassung - Logzeilen gehen nur nach
`docker logs`, und genau dann, wenn man sie braucht (eine Person meldet "es
geht nicht", siehe `api.diagnostics`), sitzt man nicht zwingend vor dem
Terminal. `LogBufferHandler` haengt sich wie jeder andere `logging.Handler`
an einen Logger und haelt die letzten `LOG_BUFFER_SIZE` Zeilen in einem
`RingBuffer` (siehe `api.diagnostics.RingBuffer`, hier bewusst importiert,
nicht neu gebaut - dieselbe Begruendung wie in `loxone.sender`: ein
generischer, laufzeitunabhaengiger Ringpuffer an einer Stelle).

**Die eine Regel, die diese Datei von jeder anderen im Projekt
unterscheidet: `LogBufferHandler` darf NIEMALS selbst protokollieren -
auch nicht im Fehlerfall.** Ueberall sonst im Projekt gilt "einen
Beobachterfehler verschlucken, aber loggen" (siehe z. B.
`loxone.sender._notify_datagram_observers`). Hier waere der Logeintrag
selbst der naechste Aufruf DESSELBEN Handlers - `logger.exception(...)`
in `emit()` liefe direkt wieder bei `emit()` ein und erzeugte eine
Endlosschleife. Deshalb faengt `emit()` jeden Fehler ab, den ein
Beobachter wirft, OHNE ihn zu protokollieren und ohne ihn weiterzureichen
(siehe `test_a_throwing_observer_neither_breaks_logging_nor_logs`). Selbst
ein Fehler beim Formatieren/Anhaengen des Eintrags selbst laeuft nicht
ueber `logging`, sondern ueber `self.handleError(record)` - die von
`logging.Handler` vorgesehene Ausweichroute, die den Traceback direkt
(per `traceback.print_exc`) auf `sys.stderr` schreibt, OHNE einen Logger
aufzurufen. Das ist keine Ausnahme von der Regel, sondern der einzige
Weg, sie einzuhalten: `sys.stderr` ist kein `logging`-Aufruf und kann
deshalb nicht rekursiv wieder bei diesem Handler ankommen.

**`emit()` laeuft im aufrufenden Thread, nicht im Event-Loop.** Logzeilen
entstehen in diesem Projekt auch in fremden Threads - aiohttp und das
chip-SDK protokollieren aus ihren eigenen Threads heraus, und
`logging.Logger.callHandlers` ruft jeden Handler synchron im genau
diesem Thread auf. `emit()` darf deshalb keine asyncio-Primitive
benutzen und nie warten (kein `await`, kein `asyncio.Lock`) - ein Aufruf
aus dem falschen Thread waere entweder ein Laufzeitfehler oder ein
stiller Deadlock. `collections.deque.append` ist unter CPython dank GIL
atomar (jede einzelne Bytecode-Operation eines `deque.append` laeuft ohne
Zwischenausstieg des Interpreters) - deshalb braucht der Ring hier KEIN
zusaetzliches Schloss, obwohl mehrere Threads gleichzeitig anhaengen
koennen.

**Der Zeitstempel kommt aus `loxmatter.timestamps.now_iso`** - derselben
Funktion, die auch `DatagramLogEntry.timestamp` (siehe `api.diagnostics`)
und `CommandLogEntry.timestamp` benutzen. Zwei verschiedene Zeitformate
nebeneinander in derselben Systemseite waeren fuer den Leser ein Raetsel.

**Am Logger `loxmatter`, nicht am Root-Logger** (siehe `install_log_buffer`
unten). Die Zeilen fremder Bibliotheken (aiohttp, uvicorn, das chip-SDK)
gehoeren nicht in eine Bedienoberflaeche fuer diese Bruecke.

**Der echte Fund beim Beleg der Rekursionsfreiheit (Schritt 5 des
Auftrags).** Ein Beobachter, der selbst ueber DENSELBEN Logger
protokolliert (an dem `LogBufferHandler` haengt), loest einen
verschachtelten, zweiten `emit()`-Aufruf im selben Thread-Stack aus -
`Logger.callHandlers` haengt Handler synchron in den aufrufenden Stack.
Ohne Gegenmassnahme waere das eine ECHTE, unbegrenzte Python-Rekursion
(der Beobachter des zweiten Aufrufs protokolliert erneut, ausgeloest vom
selben Beobachter, mit einer bei jeder Ebene laengeren "Echo"-Zeile) -
kein Sonderfall, der sich von selbst erledigt. Deshalb traegt
`LogBufferHandler` ein Thread-lokales Wiedereintritts-Flag
(`_ThreadState.active`): der Eintrag eines verschachtelten `emit()`-Aufrufs
im selben Thread landet zwar noch im Ring (die Zeile geht nicht verloren),
aber seine Beobachter werden NICHT erneut benachrichtigt - die Kette
bricht garantiert nach genau einer Ebene ab, nicht erst, wenn Pythons
Rekursionslimit anschlaegt. Thread-lokal (nicht ein einzelnes,
handlerweites Flag), damit ein Thread, der gerade selbst in `emit()`
steckt, keinen anderen, gleichzeitig protokollierenden Thread blockiert -
siehe `test_a_line_from_another_thread_arrives`, das echte Nebenlaeufigkeit
voraussetzt.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from loxmatter.api.diagnostics import RingBuffer
from loxmatter.timestamps import now_iso

LOG_BUFFER_SIZE = 500


@dataclass(frozen=True)
class LogEntry:
    """Eine einzelne Logzeile aus dem ueberwachten Logger.

    `message` ist bereits die fertig formatierte Nachricht INKLUSIVE
    Traceback, falls einer anhaengt (`self.format(record)` in `emit()`
    liefert das) - nicht das rohe `record.msg` mit unaufgeloesten
    `%s`-Platzhaltern. Bei einer Stoerung ist der Traceback das
    Interessanteste an der Zeile; er darf nicht verlorengehen, nur weil er
    nicht in einem eigenen Feld steht (siehe
    `test_an_exception_is_kept_as_text`)."""

    timestamp: str
    level: str
    logger: str
    message: str


class _ThreadState(threading.local):
    """Traegt das Wiedereintritts-Flag je Thread - siehe Moduldocstring,
    Abschnitt "Der echte Fund...". Ein eigenes `__init__`, weil
    `threading.local`-Unterklassen es fuer JEDEN Thread neu aufrufen, der
    zum ersten Mal ein Attribut auf der Instanz anfasst (siehe
    `threading.local`-Dokumentation) - so startet `active` in jedem Thread
    zuverlaessig bei `False`, ohne dass ein Aufrufer das Attribut selbst
    vorbelegen muesste."""

    def __init__(self) -> None:
        self.active = False


class LogBufferHandler(logging.Handler):
    """`logging.Handler`, der die letzten `LOG_BUFFER_SIZE` Zeilen in einem
    `RingBuffer` haelt und optionale Beobachter benachrichtigt - siehe
    Moduldocstring fuer die Regeln, die `emit()` einhalten muss (keine
    eigene Protokollierung, kein asyncio, Thread-lokale
    Wiedereintrittssperre)."""

    def __init__(self) -> None:
        super().__init__()
        self.entries: RingBuffer[LogEntry] = RingBuffer(maxlen=LOG_BUFFER_SIZE)
        self._observers: list[Callable[[LogEntry], None]] = []
        self._state = _ThreadState()

    def add_observer(self, callback: Callable[[LogEntry], None]) -> None:
        """Meldet einen Beobachter an, der jede neue Logzeile sieht - nach
        demselben Muster wie `Runtime.add_observer`/
        `UdpSender.add_datagram_observer`."""
        self._observers.append(callback)

    def remove_observer(self, callback: Callable[[LogEntry], None]) -> None:
        """Meldet einen Beobachter wieder ab. Ein unbekannter Beobachter
        (z. B. doppelt abgemeldet) ist kein Fehler, sondern wird still
        ignoriert - dieselbe Regel wie bei `Runtime.remove_observer`."""
        try:
            self._observers.remove(callback)
        except ValueError:
            pass

    def emit(self, record: logging.LogRecord) -> None:
        """Formt den Datensatz zu einem `LogEntry`, haengt ihn an den Ring
        und benachrichtigt danach die Beobachter - siehe Moduldocstring fuer
        die Begruendung jeder einzelnen Eigenschaft unten.

        Formatieren und Anhaengen laufen in einem eigenen try/except: ein
        Fehler dabei (z. B. eine Formatzeichenkette mit fehlendem Argument
        in `self.format(record)`) geht NICHT ueber `logging` - das waere
        bereits die verbotene Selbst-Protokollierung -, sondern ueber
        `self.handleError(record)`, die Standard-Ausweichroute von
        `logging.Handler`, die direkt auf `sys.stderr` schreibt.

        Jeder Beobachter laeuft in seinem eigenen try/except, das nichts
        protokolliert und nichts weiterreicht (siehe Moduldocstring, "Die
        eine Regel..."). Vor der Benachrichtigung prueft `emit()` das
        Thread-lokale Wiedereintritts-Flag: steckt dieser Thread bereits in
        einem laufenden `emit()`-Aufruf (ein Beobachter hat selbst ueber
        denselben Logger protokolliert), landet der neue Eintrag zwar noch
        im Ring, aber seine Beobachter werden NICHT erneut benachrichtigt -
        siehe Moduldocstring, "Der echte Fund...", und
        `test_an_observer_that_logs_through_the_same_handler_terminates`."""
        try:
            entry = LogEntry(
                timestamp=now_iso(),
                level=record.levelname,
                logger=record.name,
                message=self.format(record),
            )
            self.entries.append(entry)
        except Exception:  # noqa: BLE001 — kein `logging.exception(...)` moeglich (siehe
            # Moduldocstring, "Die eine Regel..."): genau das waere die verbotene
            # Selbst-Protokollierung. `self.handleError` ist die vorgesehene
            # Ausweichroute von `logging.Handler` und schreibt direkt auf `sys.stderr`.
            self.handleError(record)
            return

        if self._state.active:
            return

        self._state.active = True
        try:
            for observer in list(self._observers):
                try:
                    observer(entry)
                except Exception:  # noqa: BLE001, S110 — bewusst weit gefangen und bewusst
                    # NICHT protokolliert: der Ausgleich waere selbst eine Logzeile, die
                    # denselben Handler erneut aufruft (siehe Moduldocstring, "Die eine
                    # Regel..."). Das ist die einzige Stelle im Projekt, an der ein
                    # verschluckter Fehler NICHT durch einen Logeintrag ausgeglichen wird.
                    pass
        finally:
            self._state.active = False


def install_log_buffer(
    logger_name: str = "loxmatter", level: int = logging.INFO
) -> LogBufferHandler:
    """Haengt einen neuen `LogBufferHandler` an den benannten Logger, setzt
    dessen Stufe und gibt ihn zurueck.

    Standardmaessig an `loxmatter`, NICHT an den Root-Logger - siehe
    Moduldocstring. Ein Aufrufer, der wirklich alles mitschneiden will
    (z. B. ein Test), kann `logger_name` explizit ueberschreiben."""
    handler = LogBufferHandler()
    handler.setLevel(level)
    logging.getLogger(logger_name).addHandler(handler)
    return handler
