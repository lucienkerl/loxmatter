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

"""The log ring the interface's system page gets its lines from.

Today there is no capture for this at all - log lines only go to `docker
logs`, and precisely when they are needed (someone reports "it's not
working", see `api.diagnostics`), you are not necessarily sitting in front
of the terminal. `LogBufferHandler` attaches to a logger like any other
`logging.Handler` and keeps the last `LOG_BUFFER_SIZE` lines in a
`RingBuffer` (see `api.diagnostics.RingBuffer`, deliberately imported here
rather than rebuilt - the same rationale as in `loxone.sender`: one
generic, runtime-independent ring buffer in one place).

**The one rule that sets this file apart from every other one in the
project: `LogBufferHandler` must NEVER log by itself - not even on
error.** Everywhere else in the project, "swallow an observer error, but
log it" applies (see e.g. `api.diagnostics.RingBuffer.append`, which
`UdpSender.add_datagram_observer` has also hung off since the task 7,
fix 2 correction). Here, the log entry itself would be the next call to
THIS SAME handler - `logger.exception(...)` in `emit()` would come
straight back into `emit()` and create an infinite loop. That is why
`emit()` catches every error an observer raises WITHOUT logging it and
without propagating it (see
`test_a_throwing_observer_neither_breaks_logging_nor_logs`). Even an
error while formatting/appending the entry itself does not go through
`logging`, but through `self.handleError(record)` - the fallback route
`logging.Handler` provides, which writes the traceback directly (via
`traceback.print_exc`) to `sys.stderr`, WITHOUT calling any logger. That
is not an exception to the rule but the only way to follow it: `sys.stderr`
is not a `logging` call and so cannot recursively arrive back at this
handler.

**`emit()` runs in the calling thread, not in the event loop.** Log lines
in this project also arise in foreign threads - aiohttp and the chip SDK
log from their own threads, and `logging.Logger.callHandlers` calls every
handler synchronously in exactly that thread. `emit()` must therefore not
use any asyncio primitives and must never wait (no `await`, no
`asyncio.Lock`) - a call from the wrong thread would be either a runtime
error or a silent deadlock. `collections.deque.append` is atomic under
CPython - NOT because "every bytecode operation runs without an
intermediate exit" (`deque.append` is not a sequence of Python bytecode
operations at all), but because it is a single C call that holds the GIL
for its entire duration and never releases it in between - no other
thread can get a turn in the middle of an `append`. That is why the ring
here needs NO additional lock for APPENDING, even though several threads
can append at the same time.

**That only covers the write side.** A read at the Python level is not a
single atomic C operation and is therefore NOT protected the same way:
`RingBuffer.__iter__` (see `api.diagnostics`) returns a live `deque`
iterator, and `deque` detects a mutation during an ongoing iteration and
aborts with `RuntimeError('deque mutated during iteration')` as soon as a
concurrent `append` (with a full ring: an eviction) intervenes. A plain
`for entry in ring:` loop - exactly the form `api.diagnostics` uses today
for `sender.datagram_log` and `command_log` - is therefore no longer safe
once the ring can be written from more than one thread. So far that was
never a problem, because every existing ring was only ever written from a
single (event-loop) path; `LogBufferHandler` is the first writer from ANY
thread. A reader must therefore call `list(ring)` to take a snapshot (like
`RingBuffer.append` itself a single, safe C call) - see `RingBuffer` in
`api.diagnostics` for the same note from the reader's side.

**The timestamp comes from `loxmatter.timestamps.now_iso`** - the same
function that `DatagramLogEntry.timestamp` (see `api.diagnostics`) and
`CommandLogEntry.timestamp` also use. Two different time formats side by
side on the same system page would be a puzzle for the reader.

**On the logger `loxmatter`, not on the root logger** (see
`install_log_buffer` below). The lines from third-party libraries
(aiohttp, uvicorn, the chip SDK) do not belong in an operator interface
for this bridge.

**The real finding while proving freedom from recursion (step 5 of the
assignment).** An observer that itself logs through the SAME logger (the
one `LogBufferHandler` is attached to) triggers a nested, second `emit()`
call in the same thread stack - `Logger.callHandlers` hangs handlers
synchronously into the calling stack. Without a countermeasure, that would
be REAL, unbounded Python recursion (the second call's observer logs
again, triggered by the same observer, with an "echo" line growing longer
at every level) - not a special case that resolves itself. That is why
`LogBufferHandler` carries a thread-local re-entrancy flag
(`_ThreadState.active`): the entry from a nested `emit()` call in the same
thread still lands in the ring (the line is not lost), but its observers
are NOT notified again - the chain is guaranteed to break after exactly
one level, not only once Python's recursion limit kicks in.

**Why thread-local and not a single, handler-wide flag - the correct
rationale, after the original one turned out to be wrong.** An earlier
draft of this docstring claimed that a handler-wide flag would block a
thread B while thread A is currently in `emit()`, and backed that with
`test_a_line_from_another_thread_arrives`. Both claims were wrong: that
test is `thread.start(); thread.join()` - strictly sequential, no real
concurrency - and even with two genuinely concurrent threads, thread B
blocks on `logging.Handler.lock` anyway (an `RLock` that `Handler.handle()`
holds for the entire duration of `emit()`), regardless of whether the
re-entrancy flag is kept per thread or handler-wide. Measured: thread B
waited 0.41 s while an observer on thread A slept for 0.4 s - exactly the
wait `Handler.lock` enforces anyway.

The actual reason: the lock also holds when a caller invokes
`handler.emit()` DIRECTLY and thereby bypasses `Handler.handle()` along
with `Handler.lock` - `logging.Handler` explicitly permits that, and
nothing in this project forbids a future caller from doing so. A simple,
handler-wide instance flag would, under the CURRENT setup (access
exclusively via `Logger.callHandlers` -> `Handler.handle()` ->
`Handler.lock`), provably not lose a single notification that the
thread-local version would not also lose - thread-locality is therefore a
safeguard against a case that does not occur today but could (a direct
`emit()` call), not a necessity for genuine concurrency via `handle()`.
Recording this here keeps a later reader from thinking the lock is more
necessary than it is.
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
    """A single log line from the monitored logger.

    `message` is already the fully formatted message INCLUDING a
    traceback, if one is attached (`self.format(record)` in `emit()`
    provides that) - not the raw `record.msg` with unresolved `%s`
    placeholders. In the event of a fault, the traceback is the most
    interesting part of the line; it must not be lost just because it does
    not sit in its own field (see `test_an_exception_is_kept_as_text`)."""

    timestamp: str
    level: str
    logger: str
    message: str


class _ThreadState(threading.local):
    """Carries the re-entrancy flag per thread - see the module docstring,
    the "The real finding..." section. A dedicated `__init__` because
    `threading.local` subclasses call it anew for EVERY thread that
    touches an attribute on the instance for the first time (see the
    `threading.local` documentation) - so `active` reliably starts at
    `False` in every thread, without any caller having to pre-set the
    attribute itself."""

    def __init__(self) -> None:
        self.active = False


class LogBufferHandler(logging.Handler):
    """`logging.Handler` that keeps the last `LOG_BUFFER_SIZE` lines in a
    `RingBuffer` and notifies optional observers - see the module
    docstring for the rules `emit()` must follow (no logging of its own,
    no asyncio, thread-local re-entrancy lock)."""

    def __init__(self) -> None:
        super().__init__()
        self.entries: RingBuffer[LogEntry] = RingBuffer(maxlen=LOG_BUFFER_SIZE)
        self._observers: list[Callable[[LogEntry], None]] = []
        self._state = _ThreadState()

    def add_observer(self, callback: Callable[[LogEntry], None]) -> None:
        """Registers an observer - WITH A CONTRACT THAT DIFFERS FROM
        `Runtime.add_observer`/`UdpSender.add_datagram_observer`, not a
        repetition of it:

        - **The observer does NOT see every line.** A line logged
          synchronously FROM WITHIN an observer (same logger, same
          thread) still lands in the ring, but reaches NO observer - not
          even ones that have nothing to do with the recursion (see the
          class/module docstring, re-entrancy lock). `UdpSender.
          add_datagram_observer`, by contrast, genuinely delivers every
          entry - using it as a model for THIS method would be
          misleading.
        - **Runs in the thread that produced the line** - not in the
          event loop. `logging.Logger.callHandlers` calls `emit()`
          synchronously in the calling stack, and `emit()` calls the
          observers directly from there.
        - **Runs while `logging.Handler.lock` is held** (`Handler.
          handle()` holds this lock for the entire duration of `emit()`).

        **That is why an observer must never block and must return
        promptly.** An observer waiting on a lock held by another,
        currently-logging thread can run into a deadlock: that other
        thread is in turn waiting on `Handler.lock`, which the first
        thread holds during its observer call. Not a crash - a hang, in a
        service running unattended for weeks."""
        self._observers.append(callback)

    def remove_observer(self, callback: Callable[[LogEntry], None]) -> None:
        """Deregisters an observer again. An unknown observer (e.g.
        deregistered twice) is not an error but is silently ignored - the
        same rule as for `Runtime.remove_observer`."""
        try:
            self._observers.remove(callback)
        except ValueError:
            pass

    def emit(self, record: logging.LogRecord) -> None:
        """Shapes the record into a `LogEntry`, appends it to the ring, and
        then notifies the observers - see the module docstring for the
        rationale behind each individual property below.

        Formatting and appending run in their own try/except: an error
        during that (e.g. a format string with a missing argument in
        `self.format(record)`) does NOT go through `logging` - that would
        already be the forbidden self-logging - but through
        `self.handleError(record)`, `logging.Handler`'s standard fallback
        route, which writes directly to `sys.stderr`.

        **Gap, deliberately accepted:** the re-entrancy lock only covers
        the observer loop below, NOT `self.format(record)` itself. A log
        argument whose `__str__`/`__repr__` itself logs through the same
        logger recurses through `format()` WITHOUT passing through the
        lock - that would be real, unbounded recursion up to a
        `RecursionError`, not slowed by the flag. No known caller in the
        project does that today (all `%` arguments are simple values), so
        it is deliberately not additionally guarded against - anyone
        considering extending the lock over `format()` must then also
        keep appending to the ring on re-entry (see above: "the line is
        not lost").

        Every observer runs in its own try/except that logs nothing and
        propagates nothing (see the module docstring, "The one rule...").
        Before notifying, `emit()` checks the thread-local re-entrancy
        flag: if this thread is already inside a running `emit()` call (an
        observer itself logged through the same logger), the new entry
        still lands in the ring, but its observers are NOT notified again
        - see the module docstring, "The real finding...", and
        `test_an_observer_that_logs_through_the_same_handler_terminates`."""
        try:
            entry = LogEntry(
                timestamp=now_iso(),
                level=record.levelname,
                logger=record.name,
                message=self.format(record),
            )
            self.entries.append(entry)
        except Exception:  # noqa: BLE001 — `logging.exception(...)` is not possible here
            # (see the module docstring, "The one rule..."): exactly that would be the
            # forbidden self-logging. `self.handleError` is the fallback route
            # `logging.Handler` provides, and writes directly to `sys.stderr`.
            self.handleError(record)
            return

        if self._state.active:
            return

        self._state.active = True
        try:
            for observer in list(self._observers):
                try:
                    observer(entry)
                except Exception:  # noqa: BLE001, S110 — deliberately caught broadly and
                    # deliberately NOT logged: the compensating log line would itself call
                    # this same handler again (see the module docstring, "The one rule...").
                    # This is the one place in the project where a swallowed error is NOT
                    # compensated for by a log entry.
                    pass
        finally:
            self._state.active = False


def install_log_buffer(
    logger_name: str = "loxmatter", level: int = logging.INFO
) -> LogBufferHandler:
    """Attaches a new `LogBufferHandler` to the named logger, sets its
    level AND the level of the logger itself, and returns the handler.

    By default on `loxmatter`, NOT on the root logger - see the module
    docstring. A caller that genuinely wants to capture everything (e.g. a
    test) can explicitly override `logger_name`.

    **Why this function also sets `logging.getLogger(logger_name).
    setLevel(level)`, not just `handler.setLevel(level)`.** What reaches a
    handler at all is decided not by the handler's level, but first by
    `Logger.isEnabledFor` on the logging logger itself - a `logger.
    info(...)` call whose logger effectively sits at WARNING is discarded
    BEFORE any handler ever sees it, regardless of the handler's level.
    Without this line, `loxmatter` (and with it every module logger in
    the project - all of them are `logging.getLogger(__name__)`, i.e.
    children of `loxmatter`, with no explicitly set level of their own)
    would stay at Python's default effective level of WARNING: nowhere in
    the project is there a `basicConfig`, `setLevel` or `dictConfig` for
    `loxmatter`, and `uvicorn.Config(log_level="info")` in `cli.py` sets
    only the `uvicorn.*` loggers, not `loxmatter`. With this function's
    default of `level=logging.INFO`, `install_log_buffer()` would have
    been practically useless without this line: every `logger.info(...)`
    line in the whole project would never have reached the handler, only
    `logger.warning(...)` and above would have arrived - even though the
    design (live-feed spec, section 4, "level filter for logs") explicitly
    requires "from INFO up".

    **Side effect a caller needs to know about:** this sets an EXPLICIT
    level on the logger `logger_name` itself, not only on this one
    handler - that affects EVERY handler attached today or in future to
    this logger (or one of its children with no level of its own), in
    BOTH directions: the gate now sits at exactly `level`, no longer at
    the previously effective level. With the default (INFO, lower than
    the previous WARNING), MORE gets through for every other handler on
    this logger than before - another handler with its own, higher
    `Handler.setLevel(...)` filters that back out itself and sees nothing
    extra. If a caller instead calls this function with a `level` HIGHER
    than the previous WARNING (e.g. ERROR), LESS gets through for EVERY
    handler on this logger than before - even for one that would itself
    sit at a lower level. Today no second handler is attached to
    `loxmatter`; a future caller who wants to secure an independent level
    of its own for a second handler on the same logger must reset the
    logger's level itself after this call."""
    handler = LogBufferHandler()
    handler.setLevel(level)
    target_logger = logging.getLogger(logger_name)
    target_logger.setLevel(level)
    target_logger.addHandler(handler)
    return handler
