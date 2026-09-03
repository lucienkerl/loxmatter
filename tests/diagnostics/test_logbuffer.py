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

"""Der Log-Ring, aus dem die Oberflaeche ihre Zeilen bekommt."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from uuid import uuid4

import pytest

from loxmatter.diagnostics.logbuffer import (
    LOG_BUFFER_SIZE,
    LogBufferHandler,
    LogEntry,
    install_log_buffer,
)


@pytest.fixture
def logger_with_handler() -> Iterator[tuple[logging.Logger, LogBufferHandler]]:
    """Legt einen frischen Logger mit garantiert eindeutigem Namen und
    angehaengtem `LogBufferHandler` an, und meldet den Handler nach dem Test
    wieder ab.

    `uuid4().hex` statt `id(object())` (wie im urspruenglichen Brief-Code):
    `id()` liefert die Speicheradresse eines Objekts, und CPython gibt die
    Adresse eines sofort wieder freigegebenen temporaeren Objekts fast immer
    an das naechste `object()` weiter - ein Lauf von 200 Aufrufen von
    `id(object())` ergab genau EINE einzige unterschiedliche ID, nicht 200
    eindeutige Namen. Ohne diese Aenderung haetten mehrere Tests denselben
    Loggernamen geteilt (mit je einem eigenen `LogBufferHandler` daran) -
    heute folgenlos, weil kein Test eine Aussage ueber einen ANDEREN,
    gleichzeitig aktiven `test.*`-Logger trifft, aber ein Zufall, kein
    Entwurf, und beim naechsten Test, der genau das prueft, eine stille
    Fehlerquelle.

    Meldet den Handler ueber `removeHandler` wieder ab (nicht
    `.handlers.clear()`, siehe auch `_cleanup_test_recursion_proof_logger`
    unten): ohne das blieben fuenf Handler an fuenf `test.*`-Loggern haengen
    (einer je Testfunktion unten, die diese Fixture benutzt) - heute
    folgenlos, weil jeder Loggername einzigartig ist und niemand mehr
    hinschaut, aber unnoetiger Ballast in `logging.Logger.manager.
    loggerDict`, der bei einem kuenftigen, absichtlichen Wiederverwenden
    eines Namens ploetzlich sichtbar wuerde."""
    logger = logging.getLogger(f"test.{uuid4().hex}")
    logger.setLevel(logging.INFO)
    handler = LogBufferHandler()
    logger.addHandler(handler)
    yield logger, handler
    logger.removeHandler(handler)


def test_a_log_line_lands_in_the_ring(
    logger_with_handler: tuple[logging.Logger, LogBufferHandler],
) -> None:
    logger, handler = logger_with_handler
    logger.warning("Miniserver nicht erreichbar")

    entries = list(handler.entries)
    assert [e.message for e in entries] == ["Miniserver nicht erreichbar"]
    assert entries[0].level == "WARNING"


def test_a_line_from_another_thread_arrives(
    logger_with_handler: tuple[logging.Logger, LogBufferHandler],
) -> None:
    """Logzeilen entstehen in diesem Projekt auch in fremden Threads - aiohttp
    und das chip-SDK. `emit` laeuft dort, wo die Zeile entsteht.

    Streng sequenziell (`start()` dann sofort `join()`), keine echte
    Nebenlaeufigkeit - das reicht, um zu belegen, dass `emit()` aus einem
    fremden Thread funktioniert, beweist aber NICHTS ueber gleichzeitiges
    Protokollieren aus zwei Threads (siehe Moduldocstring, Abschnitt
    "Warum thread-lokal...", wo eine fruehere Fassung dieses Docstrings
    genau das faelschlich behauptet hatte)."""
    logger, handler = logger_with_handler
    thread = threading.Thread(target=lambda: logger.info("aus einem Thread"))
    thread.start()
    thread.join()

    assert [e.message for e in handler.entries] == ["aus einem Thread"]


def _throwing_observer(entry: LogEntry) -> None:
    """Ersetzt das Brief-Lambda `lambda entry: (_ for _ in ()).throw(...)` -
    wie in Task 2 laesst sich das werfende Lambda unter
    `from __future__ import annotations` und Mypy strict nicht sauber
    typisieren, und Ruff flaggt den Generator-throw-Trick ohnehin
    (siehe Task-2-Bericht, Abweichung 1). Die gepruefte Aussage ist
    unveraendert: ein Beobachter, der wirft."""
    raise RuntimeError("kaputt")


def test_a_throwing_observer_neither_breaks_logging_nor_logs(
    logger_with_handler: tuple[logging.Logger, LogBufferHandler],
) -> None:
    """Die eine Stelle im Projekt, an der ein verschluckter Fehler NICHT
    durch einen Logeintrag ausgeglichen werden darf: der Ausgleich waere
    selbst eine Logzeile, die denselben Handler aufruft - eine
    Endlosschleife."""
    logger, handler = logger_with_handler
    handler.add_observer(_throwing_observer)

    logger.info("erste")
    logger.info("zweite")

    assert [e.message for e in handler.entries] == ["erste", "zweite"]


def test_the_observer_sees_each_entry_once(
    logger_with_handler: tuple[logging.Logger, LogBufferHandler],
) -> None:
    logger, handler = logger_with_handler
    seen: list[LogEntry] = []
    handler.add_observer(seen.append)

    logger.info("eine Zeile")

    assert [e.message for e in seen] == ["eine Zeile"]


def test_an_exception_is_kept_as_text(
    logger_with_handler: tuple[logging.Logger, LogBufferHandler],
) -> None:
    """Bei einer Stoerung ist der Traceback das Interessanteste - er darf
    nicht verlorengehen, nur weil er nicht in `message` steht."""
    logger, handler = logger_with_handler
    try:
        raise ValueError("etwas ging schief")
    except ValueError:
        logger.exception("beim Senden")

    entry = next(iter(handler.entries))
    assert "ValueError" in entry.message
    assert "etwas ging schief" in entry.message


def test_remove_observer_stops_further_notifications(
    logger_with_handler: tuple[logging.Logger, LogBufferHandler],
) -> None:
    """Bislang ungeprueft, obwohl Teil des Interfaces (Task-3-Brief): eine
    abgemeldete Beobachterin darf keine spaetere Zeile mehr sehen, auch
    wenn die Zeile selbst weiterhin im Ring landet."""
    logger, handler = logger_with_handler
    seen: list[LogEntry] = []
    handler.add_observer(seen.append)

    logger.info("erste")
    handler.remove_observer(seen.append)
    logger.info("zweite")

    assert [e.message for e in seen] == ["erste"]
    assert [e.message for e in handler.entries] == ["erste", "zweite"]


def test_removing_an_unknown_observer_is_ignored() -> None:
    """Ein nie angemeldeter (oder bereits entfernter) Beobachter ist kein
    Fehler - dieselbe Regel wie bei `Runtime.remove_observer` (siehe
    Docstring von `remove_observer`), bislang ungeprueft."""
    handler = LogBufferHandler()
    handler.remove_observer(lambda entry: None)


def test_the_ring_evicts_the_oldest_entry_once_full(
    logger_with_handler: tuple[logging.Logger, LogBufferHandler],
) -> None:
    """`LOG_BUFFER_SIZE` ist Teil des oeffentlichen Interfaces (Task-3-Brief),
    aber die Verdraengung selbst war bislang ungeprueft - nur `RingBuffer`
    (in `api.diagnostics`) hat einen eigenen Verdraengungstest, nicht dieser
    Handler, der ihn benutzt."""
    logger, handler = logger_with_handler
    for i in range(LOG_BUFFER_SIZE + 1):
        logger.info("Zeile %d", i)

    entries = list(handler.entries)
    assert len(entries) == LOG_BUFFER_SIZE
    assert entries[0].message == "Zeile 1"
    assert entries[-1].message == f"Zeile {LOG_BUFFER_SIZE}"


def _log_via_same_logger_observer(entry: LogEntry) -> None:
    """Beobachter, der selbst ueber den GLEICHEN Logger protokolliert -
    also einen Aufruf ausloest, der wieder bei `LogBufferHandler.emit`
    ankommt. Siehe `test_an_observer_that_logs_through_the_same_handler_
    terminates` fuer die Beweisfuehrung, warum das trotzdem terminiert."""
    logging.getLogger("test.recursion-proof").info("Beobachter-Echo: %s", entry.message)


def test_an_observer_that_logs_through_the_same_handler_terminates() -> None:
    """Beweist, dass ein Beobachter, der selbst ueber denselben Logger
    protokolliert, KEINE Endlosschleife ausloest.

    Ohne Gegenmassnahme WAERE das echt rekursiv: der Beobachter unten ruft
    `logger.info` auf demselben Logger auf, an dem `handler` haengt - das
    fuehrt zu einem zweiten, verschachtelten `emit()`-Aufruf im selben
    Thread-Stack (`Logger.callHandlers` ruft Handler synchron im
    aufrufenden Stack auf, siehe Aufgabenbeschreibung). Dieser zweite
    Aufruf haengt seinen eigenen Eintrag an den Ring und ruft seinerseits
    die Beobachterliste auf - darunter wieder denselben Beobachter, der
    wieder protokolliert, mit einer bei jeder Ebene laenger werdenden
    "Beobachter-Echo: ..."-Zeile. Das ist eine ECHTE, unbegrenzte
    Python-Rekursion ohne eingebaute Bremse - genau der Fund aus Schritt 5
    des Auftrags: `LogBufferHandler.emit` traegt deshalb ein
    Thread-lokales Wiedereintritts-Flag (`_ThreadState.active`, siehe
    Klassendocstring): der ZWEITE, verschachtelte `emit()`-Aufruf im selben
    Thread haengt seinen Eintrag zwar noch an den Ring an (die Zeile geht
    nicht verloren), benachrichtigt aber KEINE Beobachter mehr - die Kette
    bricht dort garantiert nach genau einer Ebene ab, nicht erst, wenn
    Pythons Rekursionslimit anschlaegt.

    Genau zwei Zeilen landen deshalb im Ring: die urspruengliche und GENAU
    EINE Echo-Zeile - keine dritte, vierte, ... Ebene.
    """
    logger = logging.getLogger("test.recursion-proof")
    logger.setLevel(logging.INFO)
    handler = LogBufferHandler()
    logger.addHandler(handler)
    try:
        handler.add_observer(_log_via_same_logger_observer)

        logger.info("erste Zeile")

        entries = [e.message for e in handler.entries]
        assert entries == ["erste Zeile", "Beobachter-Echo: erste Zeile"]
    finally:
        logger.removeHandler(handler)


def test_a_directly_nested_emit_call_bypassing_the_lock_still_terminates(
    logger_with_handler: tuple[logging.Logger, LogBufferHandler],
) -> None:
    """Belegt die im Moduldocstring (Abschnitt "Warum thread-lokal...")
    genannte tatsaechliche Notwendigkeit der Wiedereintrittssperre: nicht
    Nebenlaeufigkeit (dagegen schuetzt bereits `logging.Handler.lock`),
    sondern ein Beobachter, der `handler.emit(...)` DIREKT aufruft und damit
    `Handler.handle()` samt Schloss vollstaendig umgeht. Auch dieser Fall
    bricht nach genau einer Ebene ab - die Sperre wirkt unabhaengig davon,
    ueber welchen Weg der verschachtelte Aufruf ankommt."""
    logger, handler = logger_with_handler
    direct_record = logging.LogRecord(
        name=logger.name,
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="direkt emittiert, am Schloss vorbei",
        args=(),
        exc_info=None,
    )

    def _direct_emit_observer(entry: LogEntry) -> None:
        handler.emit(direct_record)  # bewusst NICHT ueber logger.info() -> Handler.handle()

    handler.add_observer(_direct_emit_observer)
    logger.info("erste Zeile")

    assert [e.message for e in handler.entries] == [
        "erste Zeile",
        "direkt emittiert, am Schloss vorbei",
    ]


def test_install_log_buffer_attaches_to_the_loxmatter_logger_only() -> None:
    """`install_log_buffer` haengt NICHT an den Root-Logger - Zeilen fremder
    Bibliotheken (aiohttp, chip-SDK) gehoeren nicht in eine Bedienoberflaeche
    (siehe Moduldocstring)."""
    handler = install_log_buffer()
    try:
        assert handler in logging.getLogger("loxmatter").handlers
        assert handler not in logging.getLogger().handlers

        logging.getLogger("loxmatter").warning("aus der Bruecke")
        logging.getLogger("root-fremd").warning("sollte NICHT im Ring landen")

        messages = [e.message for e in handler.entries]
        assert "aus der Bruecke" in messages
        assert "sollte NICHT im Ring landen" not in messages
    finally:
        # Aufraeumen: "loxmatter" ist ein globaler, ueber die Testsuite
        # geteilter Logger - ohne das bliebe dieser Handler an ihm haengen
        # und wuerde spaetere Tests verfaelschen (Randbedingung des Auftrags).
        # Seit `install_log_buffer` auch die Logger-Stufe selbst setzt (Fix
        # 3, siehe Docstring dort), muss diese Stufe hier ebenfalls
        # zurueckgesetzt werden - sonst bliebe `loxmatter` fuer den Rest des
        # Testlaufs auf INFO stehen statt auf der urspruenglichen,
        # unveraenderten Stufe (NOTSET).
        logging.getLogger("loxmatter").removeHandler(handler)
        logging.getLogger("loxmatter").setLevel(logging.NOTSET)


def test_install_log_buffer_default_level_captures_info_lines() -> None:
    """Reproduziert den Fund aus Fix 3: mit der Vorgabe (Stufe INFO) muss
    eine INFO-Zeile im Ring landen. Vor der Behebung wurde sie von
    `Logger.isEnabledFor` verworfen, bevor sie den Handler je erreichte -
    `install_log_buffer` setzte nur `handler.setLevel(level)`, nicht die
    Stufe des Loggers `loxmatter` selbst, und dessen effektive Stufe blieb
    ohne das auf der von Python vorgegebenen WARNING (kein `basicConfig`,
    kein `setLevel`, kein `dictConfig` fuer `loxmatter` irgendwo im
    Projekt). `install_log_buffer()` waere damit fuer die im Entwurf
    (Live-Feed-Spec, Abschnitt 4) verlangte Stufe "ab INFO" praktisch
    wirkungslos gewesen."""
    handler = install_log_buffer()
    try:
        logging.getLogger("loxmatter").info("Testzeile auf INFO")
        assert [e.message for e in handler.entries] == ["Testzeile auf INFO"]
    finally:
        logging.getLogger("loxmatter").removeHandler(handler)
        logging.getLogger("loxmatter").setLevel(logging.NOTSET)


def test_install_log_buffer_only_captures_from_the_given_level() -> None:
    """Ersetzt `test_install_log_buffer_sets_the_given_level` (Fix 3): die
    alte Fassung pruefte nur `handler.level == logging.WARNING` - eine
    Wiederholung der Zuweisung eine Zeile zuvor, die auch dann gruen
    gemeldet haette, wenn `install_log_buffer` die Stufe des LOGGERS gar
    nicht gesetzt haette (der eigentliche Fehler aus Fix 3). Diese
    verhaltensbezogene Fassung protokolliert tatsaechlich unterhalb UND auf
    der gesetzten Stufe und prueft, was davon im Ring ankommt: eine
    INFO-Zeile muss verworfen werden, eine WARNING-Zeile muss ankommen."""
    handler = install_log_buffer(level=logging.WARNING)
    try:
        assert handler.level == logging.WARNING

        logging.getLogger("loxmatter").info("sollte NICHT im Ring landen")
        logging.getLogger("loxmatter").warning("sollte im Ring landen")

        assert [e.message for e in handler.entries] == ["sollte im Ring landen"]
    finally:
        logging.getLogger("loxmatter").removeHandler(handler)
        logging.getLogger("loxmatter").setLevel(logging.NOTSET)


@pytest.fixture(autouse=True)
def _cleanup_test_recursion_proof_logger() -> Iterator[None]:
    """Der Rekursionsbeweis-Test haengt einen Handler an
    `test.recursion-proof` - kein globaler Logger, aber zur Sicherheit
    trotzdem im `finally` des jeweiligen Tests abgemeldet. Dieses Fixture
    ist ein zusaetzliches Netz, falls ein kuenftiger Test denselben Namen
    wiederverwendet.

    `-> Iterator[None]`, nicht `-> None`: diese Funktion enthaelt `yield`
    und ist damit ein Generator, keine gewoehnliche Funktion - die
    Rueckgabeannotation hatte das bislang verschwiegen. Entfernt Handler
    einzeln ueber `removeHandler` statt pauschal ueber `.handlers.clear()`:
    Letzteres wuerde JEDEN Handler an diesem Logger entfernen, auch einen,
    der - anders als heute - aus einem anderen Grund als diesem Testnetz
    dort haengen sollte; `removeHandler` je Handler ist die dafuer
    vorgesehene, gezielte Methode."""
    yield
    recursion_logger = logging.getLogger("test.recursion-proof")
    for handler in list(recursion_logger.handlers):
        recursion_logger.removeHandler(handler)
