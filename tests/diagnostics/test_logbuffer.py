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

"""The log ring the UI gets its lines from."""

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
    """Sets up a fresh logger with a guaranteed-unique name and an attached
    `LogBufferHandler`, and unregisters the handler again after the test.

    `uuid4().hex` instead of `id(object())` (as in the original task brief):
    `id()` returns an object's memory address, and CPython almost always hands
    the address of an immediately-freed temporary object straight to the next
    `object()` - a run of 200 calls to `id(object())` produced exactly ONE
    distinct id, not 200 unique names. Without this change, several tests
    would have shared the same logger name (each with its own
    `LogBufferHandler` attached) - harmless today, since no test makes an
    assertion about another, simultaneously active `test.*` logger, but that's
    a coincidence, not a design, and a silent source of failure the moment a
    future test checks exactly that.

    Unregisters the handler via `removeHandler` (not `.handlers.clear()`, see
    also `_cleanup_test_recursion_proof_logger` below): without that, five
    handlers would stay attached to five `test.*` loggers (one per test
    function below that uses this fixture) - harmless today, since every
    logger name is unique and nobody looks at them again, but needless
    baggage in `logging.Logger.manager.loggerDict` that would suddenly become
    visible the moment a name is deliberately reused in the future."""
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
    logger.warning("Miniserver unreachable")

    entries = list(handler.entries)
    assert [e.message for e in entries] == ["Miniserver unreachable"]
    assert entries[0].level == "WARNING"


def test_a_line_from_another_thread_arrives(
    logger_with_handler: tuple[logging.Logger, LogBufferHandler],
) -> None:
    """In this project, log lines also originate in foreign threads - aiohttp
    and the chip SDK. `emit` runs wherever the line originates.

    Strictly sequential (`start()` then immediately `join()`), no real
    concurrency - that's enough to prove that `emit()` works from a foreign
    thread, but proves NOTHING about concurrent logging from two threads (see
    the module docstring, section "Why thread-local...", where an earlier
    version of this docstring wrongly claimed exactly that)."""
    logger, handler = logger_with_handler
    thread = threading.Thread(target=lambda: logger.info("from a thread"))
    thread.start()
    thread.join()

    assert [e.message for e in handler.entries] == ["from a thread"]


def _throwing_observer(entry: LogEntry) -> None:
    """Replaces the task-brief lambda `lambda entry: (_ for _ in ()).throw(...)`
    - as in Task 2, the throwing lambda can't be cleanly typed under
    `from __future__ import annotations` and Mypy strict, and Ruff flags the
    generator-throw trick regardless (see Task 2 report, deviation 1). The
    behavior under test is unchanged: an observer that raises."""
    raise RuntimeError("kaputt")


def test_a_throwing_observer_neither_breaks_logging_nor_logs(
    logger_with_handler: tuple[logging.Logger, LogBufferHandler],
) -> None:
    """The one place in the project where a swallowed error must NOT be
    compensated for with a log entry: the compensation would itself be a log
    line calling the same handler - an infinite loop."""
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

    logger.info("a line")

    assert [e.message for e in seen] == ["a line"]


def test_an_exception_is_kept_as_text(
    logger_with_handler: tuple[logging.Logger, LogBufferHandler],
) -> None:
    """During a failure, the traceback is the most interesting part - it must
    not be lost just because it doesn't live in `message`."""
    logger, handler = logger_with_handler
    try:
        raise ValueError("etwas ging schief")
    except ValueError:
        logger.exception("while sending")

    entry = next(iter(handler.entries))
    assert "ValueError" in entry.message
    assert "etwas ging schief" in entry.message


def test_remove_observer_stops_further_notifications(
    logger_with_handler: tuple[logging.Logger, LogBufferHandler],
) -> None:
    """Untested so far, even though it's part of the interface (Task 3
    brief): an unregistered observer must not see any later line, even
    though the line itself still lands in the ring."""
    logger, handler = logger_with_handler
    seen: list[LogEntry] = []
    handler.add_observer(seen.append)

    logger.info("erste")
    handler.remove_observer(seen.append)
    logger.info("zweite")

    assert [e.message for e in seen] == ["erste"]
    assert [e.message for e in handler.entries] == ["erste", "zweite"]


def test_removing_an_unknown_observer_is_ignored() -> None:
    """An observer that was never registered (or already removed) is not an
    error - the same rule as for `Runtime.remove_observer` (see the
    docstring of `remove_observer`), untested so far."""
    handler = LogBufferHandler()
    handler.remove_observer(lambda entry: None)


def test_the_ring_evicts_the_oldest_entry_once_full(
    logger_with_handler: tuple[logging.Logger, LogBufferHandler],
) -> None:
    """`LOG_BUFFER_SIZE` is part of the public interface (Task 3 brief), but
    the eviction itself was untested so far - only `RingBuffer` (in
    `api.diagnostics`) has its own eviction test, not this handler that
    uses it."""
    logger, handler = logger_with_handler
    for i in range(LOG_BUFFER_SIZE + 1):
        logger.info("Zeile %d", i)

    entries = list(handler.entries)
    assert len(entries) == LOG_BUFFER_SIZE
    assert entries[0].message == "Zeile 1"
    assert entries[-1].message == f"Zeile {LOG_BUFFER_SIZE}"


def _log_via_same_logger_observer(entry: LogEntry) -> None:
    """Observer that itself logs through the SAME logger - i.e. triggers a
    call that ends up back at `LogBufferHandler.emit`. See
    `test_an_observer_that_logs_through_the_same_handler_terminates` for the
    proof of why this still terminates."""
    logging.getLogger("test.recursion-proof").info("Beobachter-Echo: %s", entry.message)


def test_an_observer_that_logs_through_the_same_handler_terminates() -> None:
    """Proves that an observer which itself logs through the same logger does
    NOT trigger an infinite loop.

    Without a countermeasure this WOULD be genuinely recursive: the observer
    below calls `logger.info` on the same logger `handler` is attached to -
    that leads to a second, nested `emit()` call on the same thread stack
    (`Logger.callHandlers` calls handlers synchronously on the calling stack,
    see the task description). This second call appends its own entry to the
    ring and in turn calls the observer list - which includes the same
    observer again, which logs again, with a "Beobachter-Echo: ..." line that
    grows longer at every level. That is REAL, unbounded Python recursion
    with no built-in brake - exactly the finding from step 5 of the task:
    `LogBufferHandler.emit` therefore carries a thread-local reentrancy flag
    (`_ThreadState.active`, see the class docstring): the SECOND, nested
    `emit()` call on the same thread still appends its entry to the ring (the
    line is not lost), but no longer notifies any observers - the chain is
    guaranteed to break there after exactly one level, rather than only when
    Python's recursion limit kicks in.

    Exactly two lines therefore land in the ring: the original one and EXACTLY
    ONE echo line - no third, fourth, ... level.
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
    """Substantiates the actual need for the reentrancy lock named in the
    module docstring (section "Why thread-local..."): not concurrency
    (already guarded against by `logging.Handler.lock`), but an observer
    that calls `handler.emit(...)` DIRECTLY and thereby bypasses
    `Handler.handle()` and its lock entirely. This case, too, breaks off
    after exactly one level - the lock works regardless of which path the
    nested call arrives by."""
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
        handler.emit(direct_record)  # deliberately NOT via logger.info() -> Handler.handle()

    handler.add_observer(_direct_emit_observer)
    logger.info("erste Zeile")

    assert [e.message for e in handler.entries] == [
        "erste Zeile",
        "direkt emittiert, am Schloss vorbei",
    ]


def test_install_log_buffer_attaches_to_the_loxmatter_logger_only() -> None:
    """`install_log_buffer` does NOT attach to the root logger - lines from
    foreign libraries (aiohttp, chip SDK) don't belong in a UI (see the
    module docstring)."""
    handler = install_log_buffer()
    try:
        assert handler in logging.getLogger("loxmatter").handlers
        assert handler not in logging.getLogger().handlers

        logging.getLogger("loxmatter").warning("from the bridge")
        logging.getLogger("root-fremd").warning("should NOT land in the ring")

        messages = [e.message for e in handler.entries]
        assert "from the bridge" in messages
        assert "should NOT land in the ring" not in messages
    finally:
        # Cleanup: "loxmatter" is a global logger shared across the test
        # suite - without this, the handler would stay attached to it and
        # taint later tests (a constraint of the task). Since
        # `install_log_buffer` now also sets the logger level itself (Fix
        # 3, see the docstring there), that level must be reset here as
        # well - otherwise `loxmatter` would stay at INFO for the rest of
        # the test run instead of its original, unchanged level (NOTSET).
        logging.getLogger("loxmatter").removeHandler(handler)
        logging.getLogger("loxmatter").setLevel(logging.NOTSET)


def test_install_log_buffer_default_level_captures_info_lines() -> None:
    """Reproduces the finding from Fix 3: with the default (level INFO), an
    INFO line must land in the ring. Before the fix it was discarded by
    `Logger.isEnabledFor` before it ever reached the handler -
    `install_log_buffer` only set `handler.setLevel(level)`, not the level of
    the `loxmatter` logger itself, and its effective level, without that,
    stayed at the WARNING Python defaults to (no `basicConfig`, no
    `setLevel`, no `dictConfig` for `loxmatter` anywhere in the project).
    `install_log_buffer()` would thus have been practically ineffective for
    the "from INFO up" level the design (live-feed spec, section 4)
    requires."""
    handler = install_log_buffer()
    try:
        logging.getLogger("loxmatter").info("Testzeile auf INFO")
        assert [e.message for e in handler.entries] == ["Testzeile auf INFO"]
    finally:
        logging.getLogger("loxmatter").removeHandler(handler)
        logging.getLogger("loxmatter").setLevel(logging.NOTSET)


def test_install_log_buffer_only_captures_from_the_given_level() -> None:
    """Replaces `test_install_log_buffer_sets_the_given_level` (Fix 3): the
    old version only checked `handler.level == logging.WARNING` - a repeat
    of the assignment one line above, which would have reported green even
    if `install_log_buffer` had never set the LOGGER's level at all (the
    actual bug from Fix 3). This behavior-based version actually logs both
    below AND at the configured level and checks what of that arrives in the
    ring: an INFO line must be discarded, a WARNING line must arrive."""
    handler = install_log_buffer(level=logging.WARNING)
    try:
        assert handler.level == logging.WARNING

        logging.getLogger("loxmatter").info("should NOT land in the ring")
        logging.getLogger("loxmatter").warning("should land in the ring")

        assert [e.message for e in handler.entries] == ["should land in the ring"]
    finally:
        logging.getLogger("loxmatter").removeHandler(handler)
        logging.getLogger("loxmatter").setLevel(logging.NOTSET)


@pytest.fixture(autouse=True)
def _cleanup_test_recursion_proof_logger() -> Iterator[None]:
    """The recursion-proof test attaches a handler to `test.recursion-proof`
    - not a global logger, but unregistered in the respective test's
    `finally` anyway, for safety. This fixture is an extra safety net in
    case a future test reuses the same name.

    `-> Iterator[None]`, not `-> None`: this function contains `yield` and is
    thus a generator, not an ordinary function - the return annotation had
    concealed that so far. Removes handlers individually via `removeHandler`
    instead of wholesale via `.handlers.clear()`: the latter would remove
    EVERY handler on this logger, including one that - unlike today - should
    be hanging there for some reason other than this test safety net;
    `removeHandler` per handler is the targeted method meant for that."""
    yield
    recursion_logger = logging.getLogger("test.recursion-proof")
    for handler in list(recursion_logger.handlers):
        recursion_logger.removeHandler(handler)
