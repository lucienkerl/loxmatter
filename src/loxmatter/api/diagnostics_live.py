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

"""WebSocket for the diagnostics live stream of the WebUI (Task 4, Phase 5, Spec 10.5).

The system page today (Task 6) fetches logs, UDP capture and command log
only once, when opened - `GET /api/diagnostics/{datagrams,commands}` and,
once wired up, a logs equivalent. This file builds the running variant:
ONE WebSocket, `/api/diagnostics/live`, that pushes all three sources
together instead of the UI polling.

`build_diagnostics_live_router` deliberately follows the same pattern as
`api.live.build_live_router` (justified there at length, not repeated
here): a bounded queue per connection (`api.streaming.BoundedQueue`)
decouples the (synchronously running) observer call from the
(asynchronous) delivery, `watch_for_disconnect` and `send_loop` run
alongside each other, the subprotocol carries the token when needed. The
only thing new compared to `api.live` is that THREE sources are tapped
here instead of one - with three independent, optional observer chains
instead of one.

**Three sources, three contracts, one shared message format.** Every
message carries `kind` (`"datagram"`, `"command"` or `"log"`) and EXACTLY
the fields of the respective entry type from
`api.diagnostics.DatagramLogEntry`, `api.diagnostics.CommandLogEntry` and
`diagnostics.logbuffer.LogEntry` respectively - not newly invented names,
otherwise the same piece of data (e.g. the timestamp) would be called two
different things in two responses.

- **Datagrams:** `sender.add_datagram_observer` (Task 2) - sees every
  datagram ACTUALLY sent, including full resend and pulse ends, which
  `Runtime`'s own observer chain (`api.live`) deliberately leaves out
  (see there). Exactly for that reason this branch hangs off `sender`,
  not `runtime` - a second `Runtime` observer would be a DIFFERENT view,
  not the same one.
- **Commands:** `command_log.add_observer` (Task 4, new in
  `api.diagnostics.RingBuffer` - see there for why the observer chain
  hangs off the ring itself rather than off the `_record_command`
  middleware: this function's signature already receives the fully built
  ring, calling `add_observer` on it needs no further coupling to
  `loxone.server`, and `command_log` - unlike `UdpSender`/
  `LogBufferHandler` - has no owner type of its own that a chain could
  otherwise hang off).
- **Logs:** `log_handler.add_observer` (Task 3) - **NOT every line**, as
  documented there (a line logged synchronously FROM within an observer
  never reaches an observer, but does land in the ring). Per the
  contract of `LogBufferHandler.add_observer`, the observer must NOT
  block and must return promptly: it runs on the thread that produced
  the line, under `logging.Handler.lock` - a waiting observer could run
  into a deadlock (see there).

  **And that very thread is NOT the event-loop thread of this route**
  (review fix Important #1, 2026-09-03). `LogBufferHandler.add_observer`
  says it literally: the observer runs "on the thread that produced the
  line" - and this project logs from aiohttp and the chip SDK, i.e. from
  foreign threads, not only from this route's event-loop thread. `on_log`
  must therefore NOT simply call `queue.put(...)`: `put`, via
  `BoundedQueue`, touches `asyncio.Queue` internals (`put_nowait`/
  `get_nowait`, including `Future.set_result`, which wakes a waiting
  `await queue.get()` via `loop.call_soon`) - and `asyncio.Queue` is NOT
  thread-safe, `loop.call_soon` from a foreign thread does not wake an
  already-blocked event loop (only `call_soon_threadsafe` writes to the
  loop's self-pipe for that). A quiet connection - `send_loop` hanging in
  `await queue.get()`, with nothing else happening on the loop right now
  - could therefore, under some circumstances, NOT see a log line from a
  genuinely foreign thread AT ALL, until something unrelated wakes the
  loop for another reason: no crash, no error message, the log branch of
  the stream simply stays empty. `live()` therefore holds on to the
  running loop (`asyncio.get_running_loop()`) BEFORE defining `on_log`,
  and `on_log` enqueues its payload via `loop.call_soon_threadsafe(
  queue.put, ...)` instead of calling `queue.put(...)` directly - that
  writes to the loop's self-pipe and reliably wakes it, even from a
  foreign thread. `call_soon_threadsafe` throws `RuntimeError` if the
  loop is already closed (possible during shutdown, if a log line is
  produced at exactly that moment) - `on_log` catches that and logs
  NOTHING in the process, otherwise that would be exactly the recursion
  that Task 3 rules out for this handler (see the
  `diagnostics.logbuffer` module docstring, "The one rule...").

  `on_datagram` and `on_command` stick with a plain `queue.put(...)`:
  both run exclusively on this route's event-loop thread (see the two
  sections above - `UdpSender.send` holds its own `asyncio.Lock`, the
  command middleware is an ordinary ASGI middleware), the thread warning
  from `LogBufferHandler.add_observer` applies to neither. For them,
  `call_soon_threadsafe` would not be an error but an unnecessary detour
  (an extra round trip through the loop's self-pipe for a call that is
  already on the right thread anyway) - and it would hide, behind the
  same line, the difference between the three branches that matters most
  to a READER (which of them comes from a foreign thread), instead of
  leaving it visible as it is here.

**`sender` and `log_handler` are optional** (see `build_app` in
`loxone.server`): `None` means "this part of the live stream is not
available for this run", not "the route as a whole is missing" - if one
is missing, its branch is skipped (no registering, no deregistering, no
snapshot), the other two keep running unchanged. `command_log`, on the
other hand, is not optional: `loxone.server.build_app` always creates it,
independent of `sender`/`client`/`log_handler`.

**The snapshot runs BEFORE registering the observers - and it is exactly
THIS order that can lose an entry, not the reverse one** (corrected in
follow-up Task 7, Fix 3a: an earlier version of this docstring claimed
the opposite). An entry that is produced exactly between "snapshot taken"
and "observer registered" lands in NEITHER of the two paths - the
snapshot had already been taken, the observer was not yet registered -
and is thereby lost. The reverse order (register first, then take the
snapshot) would instead have the other problem: an entry from exactly
that window would appear TWICE, once live via the freshly registered
observer and once in the snapshot taken afterwards. Losing rather than
duplicating is the deliberate choice here - one line too few is barely
noticeable in a live view, one line too many is.

For datagrams and commands this is inconsequential anyway: there is no
`await` between `list(...)` and the respective `add_observer`, and both
possible writers (`UdpSender.send`, the command middleware) themselves
run on this route's event-loop thread - without an intervening yield,
nothing can interleave there, the window is real but empty. For **log
lines from a foreign thread**, on the other hand, the gap is genuine:
`LogBufferHandler.emit()` runs synchronously on WHATEVER thread happens
to be logging (see the `diagnostics.logbuffer` module docstring), not on
this route's event-loop thread - an `emit()` call from aiohttp or the
chip SDK can interleave at any time, regardless of what the event loop
is doing right now. `SNAPSHOT_LIMIT` bounds the snapshot per stream - see
there for the rationale of the number.

**In the `finally`, all THREE - or rather only the ones actually
registered - observers are deregistered again.** A `sender`/`log_handler`
of `None` means: this branch was never registered, so it does not need
to be deregistered either - the condition is identical for registering
and deregistering, so that no branch is orphaned."""

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
    # Exclusively for type annotations - the same rationale as in
    # `api.diagnostics` (see there): `from __future__ import annotations`
    # evaluates annotations only as strings anyway, this block exists
    # solely for mypy.
    from loxmatter.loxone.sender import UdpSender

__all__ = ["build_diagnostics_live_router"]

SNAPSHOT_LIMIT = 50
"""Upper bound per stream for the snapshot taken when a connection opens.

Each of the three rings holds up to 500 entries (`DATAGRAM_LOG_SIZE`,
`COMMAND_LOG_SIZE`, `LOG_BUFFER_SIZE`) - sending all three at once would
be 1500 messages when the view is opened, noticeable both for the
connection and for a person who wants to see the last few minutes, not
the last few hours. 50 per stream (150 in total) is comfortably enough
for that - a system check that needs something older still has the
one-shot `GET /api/diagnostics/{datagrams,commands}` routes with their
full 500 entries."""


def build_diagnostics_live_router(
    sender: UdpSender | None,
    command_log: RingBuffer[CommandLogEntry],
    log_handler: LogBufferHandler | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/diagnostics")

    @router.websocket("/live")
    async def live(websocket: WebSocket) -> None:
        # Subprotocol negotiation and queue are shared mechanics, see
        # `api.streaming` for the rationale.
        subprotocol = accepted_subprotocol(websocket)
        await websocket.accept(subprotocol=subprotocol)
        queue = BoundedQueue(QUEUE_MAXSIZE, connection_label=str(websocket.client))
        # Captured BEFORE `on_log` is defined - see module docstring,
        # section "Logs": `on_log` needs it to reliably enqueue from a
        # foreign thread via `call_soon_threadsafe`.
        loop = asyncio.get_running_loop()

        def on_datagram(entry: DatagramLogEntry) -> None:
            queue.put(
                {
                    "kind": "datagram",
                    "key": entry.key,
                    "value": entry.value,
                    "timestamp": entry.timestamp,
                    # Follow-up Task 6 (2026-09-03): the WebUI recognises a
                    # dispensable datagram (heartbeat, full resend) by this
                    # - no longer by the arrival rate in the browser, which
                    # would have wrongly caught every rapid succession of
                    # REAL value changes too (e.g. pulse + counter from
                    # `Runtime.on_event`). See `DatagramLogEntry.forced`
                    # for the rationale.
                    "forced": entry.forced,
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
            # May be running on a FOREIGN thread (see module docstring,
            # section "Logs") - so do NOT call `queue.put` directly,
            # enqueue it via `call_soon_threadsafe` instead, which
            # reliably wakes the event loop even from a foreign thread.
            # Annotated rather than left to mypy's type inference result:
            # without the explicit `dict[str, object]`, mypy infers
            # `dict[str, str]` here from the exclusively string-valued
            # fields - but `BoundedQueue.put` (and hence
            # `call_soon_threadsafe(queue.put, ...)` below) expects
            # `dict[str, object]`, the same payload shape as
            # `on_datagram`/`on_command`.
            payload: dict[str, object] = {
                "kind": "log",
                "level": entry.level,
                "logger": entry.logger,
                "message": entry.message,
                "timestamp": entry.timestamp,
            }
            try:
                loop.call_soon_threadsafe(queue.put, payload)
            except RuntimeError:
                # The loop is already closed (shutdown, while a log line
                # is produced at exactly this moment) - the same rule as
                # everywhere in `LogBufferHandler`: an observer must never
                # throw back into the logging path, and it must not log
                # anything here, otherwise that would be the recursion
                # that Task 3 rules out for this handler (see the
                # `diagnostics.logbuffer` module docstring, "The one
                # rule...").
                pass

        # Snapshot BEFORE registering the observers (see module docstring)
        # - `list(...)` per ring, NEVER a plain `for` loop over the ring
        # itself: `command_log` and `log_handler.entries` can be written
        # to meanwhile from another path (HTTP middleware or a foreign
        # logging thread respectively), and `RingBuffer.__iter__` returns
        # a live `deque` iterator that aborts with `RuntimeError` on a
        # mutation during iteration (see there).
        if sender is not None:
            for datagram_entry in list(sender.datagram_log)[-SNAPSHOT_LIMIT:]:
                on_datagram(datagram_entry)
        for command_entry in list(command_log)[-SNAPSHOT_LIMIT:]:
            on_command(command_entry)
        if log_handler is not None:
            for log_entry in list(log_handler.entries)[-SNAPSHOT_LIMIT:]:
                on_log(log_entry)

        # All three registrations INSIDE the `try` (review fix Minor #2,
        # 2026-09-03): if the second or third one were before it and
        # threw, the first would stay registered forever because the
        # `finally` would never get to see it. `list.append` (both
        # `add_observer` methods) does not throw in practice - it is
        # still structurally correct, though, because
        # `api.live.build_live_router` (the model for this) has only a
        # single registration and therefore never even raises the
        # question.
        try:
            if sender is not None:
                sender.add_datagram_observer(on_datagram)
            command_log.add_observer(on_command)
            if log_handler is not None:
                log_handler.add_observer(on_log)

            watcher = asyncio.create_task(watch_for_disconnect(websocket))
            pump = asyncio.create_task(send_loop(websocket, queue))
            done, pending = await asyncio.wait({watcher, pump}, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
        except WebSocketDisconnect:
            pass
        finally:
            # The same condition as when registering above - a branch that
            # was never registered (sender/log_handler is None) must not
            # be deregistered either. A branch that SHOULD have been
            # registered but, due to an early error, never actually was,
            # still deregisters here without consequence - both
            # `remove_observer` methods silently ignore an unknown
            # observer (see there).
            if sender is not None:
                sender.remove_datagram_observer(on_datagram)
            command_log.remove_observer(on_command)
            if log_handler is not None:
                log_handler.remove_observer(on_log)

    return router
