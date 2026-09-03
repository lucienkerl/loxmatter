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

import pytest

from loxmatter.diagnostics.logbuffer import LogBufferHandler, LogEntry, install_log_buffer


def _logger_with_handler() -> tuple[logging.Logger, LogBufferHandler]:
    logger = logging.getLogger(f"test.{id(object())}")
    logger.setLevel(logging.INFO)
    handler = LogBufferHandler()
    logger.addHandler(handler)
    return logger, handler


def test_a_log_line_lands_in_the_ring() -> None:
    logger, handler = _logger_with_handler()
    logger.warning("Miniserver nicht erreichbar")

    entries = list(handler.entries)
    assert [e.message for e in entries] == ["Miniserver nicht erreichbar"]
    assert entries[0].level == "WARNING"


def test_a_line_from_another_thread_arrives() -> None:
    """Logzeilen entstehen in diesem Projekt auch in fremden Threads - aiohttp
    und das chip-SDK. `emit` laeuft dort, wo die Zeile entsteht."""
    logger, handler = _logger_with_handler()
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


def test_a_throwing_observer_neither_breaks_logging_nor_logs() -> None:
    """Die eine Stelle im Projekt, an der ein verschluckter Fehler NICHT
    durch einen Logeintrag ausgeglichen werden darf: der Ausgleich waere
    selbst eine Logzeile, die denselben Handler aufruft - eine
    Endlosschleife."""
    logger, handler = _logger_with_handler()
    handler.add_observer(_throwing_observer)

    logger.info("erste")
    logger.info("zweite")

    assert [e.message for e in handler.entries] == ["erste", "zweite"]


def test_the_observer_sees_each_entry_once() -> None:
    logger, handler = _logger_with_handler()
    seen: list[LogEntry] = []
    handler.add_observer(seen.append)

    logger.info("eine Zeile")

    assert [e.message for e in seen] == ["eine Zeile"]


def test_an_exception_is_kept_as_text() -> None:
    """Bei einer Stoerung ist der Traceback das Interessanteste - er darf
    nicht verlorengehen, nur weil er nicht in `message` steht."""
    logger, handler = _logger_with_handler()
    try:
        raise ValueError("etwas ging schief")
    except ValueError:
        logger.exception("beim Senden")

    entry = next(iter(handler.entries))
    assert "ValueError" in entry.message
    assert "etwas ging schief" in entry.message


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
        logging.getLogger("loxmatter").removeHandler(handler)


def test_install_log_buffer_sets_the_given_level() -> None:
    handler = install_log_buffer(level=logging.WARNING)
    try:
        assert handler.level == logging.WARNING
    finally:
        logging.getLogger("loxmatter").removeHandler(handler)


@pytest.fixture(autouse=True)
def _cleanup_test_recursion_proof_logger() -> None:
    """Der Rekursionsbeweis-Test haengt einen Handler an
    `test.recursion-proof` - kein globaler Logger, aber zur Sicherheit
    trotzdem im `finally` des jeweiligen Tests abgemeldet. Dieses Fixture
    ist ein zusaetzliches Netz, falls ein kuenftiger Test denselben Namen
    wiederverwendet."""
    yield
    logging.getLogger("test.recursion-proof").handlers.clear()
