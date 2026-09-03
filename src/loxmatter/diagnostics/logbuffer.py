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
stiller Deadlock. `collections.deque.append` ist unter CPython atomar -
NICHT weil "jede Bytecode-Operation ohne Zwischenausstieg laeuft"
(`deque.append` ist ueberhaupt keine Folge von Python-Bytecode-Operationen),
sondern weil es ein einziger C-Aufruf ist, der die GIL fuer seine gesamte
Dauer haelt und sie nie zwischendurch freigibt - kein anderer Thread kann
mitten in einem `append` zum Zug kommen. Deshalb braucht der Ring hier
KEIN zusaetzliches Schloss fuer das ANHAENGEN, obwohl mehrere Threads
gleichzeitig anhaengen koennen.

**Das deckt nur die Schreibseite ab.** Ein Lesezugriff auf Python-Ebene
ist keine einzelne atomare C-Operation und deshalb NICHT auf dieselbe Art
geschuetzt: `RingBuffer.__iter__` (siehe `api.diagnostics`) gibt einen
lebenden `deque`-Iterator zurueck, und `deque` erkennt eine Mutation
waehrend einer laufenden Iteration und bricht mit
`RuntimeError('deque mutated during iteration')` ab, sobald ein
gleichzeitiges `append` (bei vollem Ring: eine Verdraengung) dazwischen
faehrt. Eine einfache `for entry in ring:`-Schleife - genau die Form, die
`api.diagnostics` heute fuer `sender.datagram_log` und `command_log`
benutzt - ist deshalb nicht mehr sicher, sobald der Ring aus mehr als
einem Thread beschrieben werden kann. Bislang war das kein Problem, weil
jeder bisherige Ring nur aus einem einzigen (Event-Loop-)Pfad heraus
beschrieben wurde; `LogBufferHandler` ist der erste Schreiber aus
BELIEBIGEN Threads. Ein Leser muss deshalb `list(ring)` aufrufen, um eine
Momentaufnahme zu nehmen (wie `RingBuffer.append` selbst ein einziger,
sicherer C-Aufruf) - siehe `RingBuffer` in `api.diagnostics` fuer denselben
Hinweis von der Leserseite aus.

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
Rekursionslimit anschlaegt.

**Warum thread-lokal und nicht ein einzelnes, handlerweites Flag - die
richtige Begruendung, nachdem die urspruengliche sich als falsch
herausstellte.** Ein frueherer Entwurf dieses Docstrings behauptete, ein
handlerweites Flag wuerde einen Thread B blockieren, waehrend Thread A
gerade in `emit()` steckt, und belegte das mit
`test_a_line_from_another_thread_arrives`. Beides war falsch: dieser Test
ist `thread.start(); thread.join()` - streng sequenziell, keine echte
Nebenlaeufigkeit - und selbst mit zwei tatsaechlich gleichzeitigen Threads
blockiert Thread B ohnehin an `logging.Handler.lock` (ein `RLock`, das
`Handler.handle()` fuer die gesamte Dauer von `emit()` haelt), unabhaengig
davon, ob das Wiedereintritts-Flag pro Thread oder handlerweit gefuehrt
wird. Gemessen: Thread B wartete 0,41 s, waehrend ein Beobachter von
Thread A 0,4 s schlief - exakt die Wartezeit, die `Handler.lock` ohnehin
erzwingt.

Der tatsaechliche Grund: die Sperre haelt auch dann, wenn ein Aufrufer
`handler.emit()` DIREKT aufruft und damit `Handler.handle()` samt
`Handler.lock` umgeht - `logging.Handler` erlaubt das ausdruecklich, und
nichts in diesem Projekt verbietet es einem kuenftigen Aufrufer. Ein
einfaches, handlerweites Instanz-Flag wuerde im HEUTIGEN Aufbau
(Zugriff ausschliesslich ueber `Logger.callHandlers` -> `Handler.handle()`
-> `Handler.lock`) nachweislich keine einzige Benachrichtigung verlieren,
die die thread-lokale Fassung nicht auch verloeren wuerde - die
Thread-Lokalitaet ist also eine Absicherung gegen einen heute nicht
auftretenden, aber moeglichen Fall (direkter `emit()`-Aufruf), nicht eine
Notwendigkeit fuer echte Nebenlaeufigkeit ueber `handle()`. Das hier
festzuhalten, verhindert, dass ein spaeterer Leser die Sperre fuer
notwendiger haelt, als sie ist.
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
        """Meldet einen Beobachter an - MIT EINEM VERTRAG, DER VON
        `Runtime.add_observer`/`UdpSender.add_datagram_observer` ABWEICHT,
        nicht deren Wiederholung:

        - **Der Beobachter sieht NICHT jede Zeile.** Eine Zeile, die
          synchron AUS einem Beobachter heraus protokolliert wird (derselbe
          Logger, derselbe Thread), landet zwar noch im Ring, erreicht aber
          KEINEN Beobachter - auch nicht die, die mit der Rekursion nichts
          zu tun haben (siehe Klassen-/Moduldocstring, Wiedereintritts-
          sperre). `UdpSender.add_datagram_observer` liefert dagegen
          tatsaechlich jeden Eintrag - als Vorbild fuer DIESE Methode waere
          das irrefuehrend.
        - **Laeuft im Thread, der die Zeile erzeugt hat** - nicht im
          Event-Loop. `logging.Logger.callHandlers` ruft `emit()` synchron
          im aufrufenden Stack auf, und `emit()` ruft die Beobachter direkt
          von dort aus auf.
        - **Laeuft, waehrend `logging.Handler.lock` gehalten wird**
          (`Handler.handle()` haelt dieses Schloss ueber die gesamte Dauer
          von `emit()`).

        **Deshalb darf ein Beobachter niemals blockieren und muss zuegig
        zurueckkehren.** Ein Beobachter, der auf ein Schloss wartet, das
        ein anderer, gerade protokollierender Thread haelt, kann in einen
        Deadlock laufen: dieser andere Thread haengt seinerseits an
        `Handler.lock`, das der erste Thread waehrend seines
        Beobachteraufrufs haelt. Kein Absturz - ein Haenger, in einem
        Dienst, der wochenlang unbeaufsichtigt laeuft."""
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

        **Luecke, bewusst in Kauf genommen:** Die Wiedereintrittssperre
        deckt nur die Beobachterschleife unten ab, NICHT `self.format(record)`
        selbst. Ein Log-Argument, dessen `__str__`/`__repr__` seinerseits
        ueber denselben Logger protokolliert, rekursiert durch `format()`
        OHNE die Sperre zu durchlaufen - das waere echte, unbegrenzte
        Rekursion bis `RecursionError`, nicht durch das Flag gebremst. Kein
        bekannter Aufrufer im Projekt tut das heute (alle `%`-Argumente sind
        einfache Werte), deshalb bewusst nicht zusaetzlich abgesichert - wer
        die Sperre ueber `format()` auszudehnen erwaegt, muss dann aber auch
        bei Wiedereintritt weiterhin an den Ring anhaengen (siehe oben:
        "die Zeile geht nicht verloren").

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
    dessen Stufe UND die Stufe des Loggers selbst, und gibt den Handler
    zurueck.

    Standardmaessig an `loxmatter`, NICHT an den Root-Logger - siehe
    Moduldocstring. Ein Aufrufer, der wirklich alles mitschneiden will
    (z. B. ein Test), kann `logger_name` explizit ueberschreiben.

    **Warum diese Funktion auch `logging.getLogger(logger_name).setLevel(
    level)` setzt, nicht nur `handler.setLevel(level)`.** Was einen Handler
    ueberhaupt erreicht, entscheidet nicht die Stufe des Handlers, sondern
    zuerst `Logger.isEnabledFor` auf dem protokollierenden Logger selbst -
    ein `logger.info(...)`-Aufruf, dessen Logger effektiv auf WARNING steht,
    wird verworfen, BEVOR irgendein Handler ihn zu Gesicht bekommt, egal
    welche Stufe der Handler traegt. Ohne diese Zeile bliebe `loxmatter`
    (und mit ihm jeder Modul-Logger des Projekts - alle sind
    `logging.getLogger(__name__)`, also Kinder von `loxmatter`, ohne
    eigene, explizit gesetzte Stufe) auf der von Python vorgegebenen
    effektiven Stufe WARNING: nirgends im Projekt steht ein `basicConfig`,
    `setLevel` oder `dictConfig` fuer `loxmatter`, und
    `uvicorn.Config(log_level="info")` in `cli.py` setzt ausschliesslich
    die `uvicorn.*`-Logger, nicht `loxmatter`. Mit der Vorgabe `level=
    logging.INFO` dieser Funktion waere `install_log_buffer()` ohne diese
    Zeile praktisch wirkungslos gewesen: jede `logger.info(...)`-Zeile im
    ganzen Projekt haette den Handler nie erreicht, nur `logger.warning(...)`
    und hoeher waeren angekommen - obwohl der Entwurf (Live-Feed-Spec,
    Abschnitt 4, "Stufenfilter Logs") ausdruecklich "ab INFO" verlangt.

    **Nebenwirkung, die ein Aufrufer kennen muss:** dies setzt eine
    EXPLIZITE Stufe auf dem Logger `logger_name` selbst, nicht nur auf
    diesem einen Handler - das wirkt auf JEDEN Handler, der heute oder
    kuenftig ebenfalls an diesem Logger (oder einem seiner Kinder ohne
    eigene Stufe) haengt, und zwar in BEIDE Richtungen: das Gate liegt ab
    jetzt bei genau `level`, nicht mehr bei der zuvor geltenden effektiven
    Stufe. Mit der Vorgabe (INFO, niedriger als das bisherige WARNING)
    kommt fuer jeden anderen Handler an diesem Logger MEHR durch als
    vorher - ein anderer Handler mit eigener, hoeherer `Handler.setLevel(
    ...)` filtert das selbst wieder heraus und sieht nichts zusaetzliches.
    Ruft ein Aufrufer diese Funktion dagegen mit einer `level` auf, die
    HOEHER liegt als das bisherige WARNING (z. B. ERROR), kommt fuer JEDEN
    Handler an diesem Logger WENIGER durch als vorher - auch fuer einen,
    der selbst auf einer niedrigeren Stufe stehen wuerde. Heute haengt kein
    zweiter Handler an `loxmatter`; ein kuenftiger Aufrufer, der einem
    zweiten Handler am selben Logger eine eigene, davon unabhaengige Stufe
    sichern will, muss die Logger-Stufe nach diesem Aufruf selbst erneut
    setzen."""
    handler = LogBufferHandler()
    handler.setLevel(level)
    target_logger = logging.getLogger(logger_name)
    target_logger.setLevel(level)
    target_logger.addHandler(handler)
    return handler
