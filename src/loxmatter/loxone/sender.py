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

"""Sends values as UDP datagrams to the Miniserver.

Knows nothing about Matter. It receives finished keys and finished values.

Two properties are not optional:

Debouncing - a Matter device is happy to report a measured value once a
second, even when it does not change. Sending unchanged values again costs
only load, and the Miniserver does not like a UDP storm.

Rate limiting - during a full resend after a Miniserver restart, hundreds
of datagrams are due at once. They should arrive staggered, not in a burst
(spec 6.4).

**Recording (spec 10.5, task 6, phase 5).** `send()` keeps a ring buffer
of the datagrams that most recently ACTUALLY went over the socket
(`datagram_log`) - for `GET /api/diagnostics/datagrams`. Deliberately
placed HERE and not in `Runtime.on_attribute`/`on_event` (which call
`send()`): a recording before that point would show what SHOULD be sent;
this recording, right next to the `sendto()` call, shows what actually
went out - after debouncing, after the rate-limit wait. A value skipped
due to debouncing (the earlier `return False` above in `send()`)
consequently and correctly does NOT end up in the recording - it was
never sent. `RingBuffer`/`DatagramLogEntry` come from `api.diagnostics`
(see that module's docstring for the rationale for this import direction,
otherwise unusual for this project).

The recording itself is wrapped in its own try/except (`_record_sent`): a
diagnostic tool that could crash the very path it observes would be worse
than no diagnostic tool at all - see the task 6 report, point 1 (cost in
the hot path). The cost itself is minimal: one `deque.append` onto an
already bounded buffer, no I/O, no allocation beyond the single
`DatagramLogEntry`.

**Observer chain (task 2, phase 5) - since the task 7, fix 2 follow-up, ONE
subscribe/unsubscribe mechanism, not two.** `add_datagram_observer`/
`remove_datagram_observer` below used to be a separate, second list with
its own copy-while-iterating and its own log-and-skip logic - word for word
the same mechanism that `api.diagnostics.RingBuffer` (`self._datagram_log`,
see `datagram_log` below) already provides for exactly the same purpose,
just on a second, parallel list. Both methods are now thin forwards onto
`self._datagram_log.add_observer`/`remove_observer` - `_notify_datagram_observers`
is removed with nothing replacing it, `RingBuffer.append` already notifies
its observers itself (see there). The two methods nonetheless remain as
their own public interface, not replaced by a reference to
`sender.datagram_log.add_observer`: this keeps the `DatagramLogEntry` type
visible in this class's signature, and a caller needs no knowledge that
the recording is internally a `RingBuffer`. An observer thereby still runs,
as before, inside `send()`'s `async with self._lock` (see there and
`api.diagnostics.RingBuffer`'s class docstring, section on
`UdpSender.datagram_log`, for the consequence of that - no risk of
recursion, but a slow observer delays every subsequent send over the same
sender).
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable

from loxmatter.api.diagnostics import DatagramLogEntry, RingBuffer
from loxmatter.loxone.values import datagram
from loxmatter.timestamps import now_iso

RATE_LIMIT_PER_SECOND = 50.0
DATAGRAM_LOG_SIZE = 500

logger = logging.getLogger(__name__)


class UdpSender:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        rate_limit: float = RATE_LIMIT_PER_SECOND,
        log_size: int = DATAGRAM_LOG_SIZE,
    ) -> None:
        """Sets up the UDP socket. A rate_limit of 0 or below means: no rate limit."""
        self._target = (host, port)
        self._interval = 1.0 / rate_limit if rate_limit > 0 else 0.0
        self._socket: socket.socket | None = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._last_sent: dict[str, str] = {}
        self._next_send_time = 0.0
        self._lock = asyncio.Lock()
        self._datagram_log: RingBuffer[DatagramLogEntry] = RingBuffer(maxlen=log_size)

    @property
    def target(self) -> tuple[str, int]:
        """Target host/port - for the diagnostics system check
        (`api.diagnostics._check_miniserver`), otherwise purely internal."""
        return self._target

    @property
    def datagram_log(self) -> RingBuffer[DatagramLogEntry]:
        """The datagrams most recently actually sent - see the module
        docstring, "Recording" section. Read-only from the outside: the
        ring buffer itself offers no `clear()`/no mutation besides
        `append()` anyway (see `api.diagnostics.RingBuffer`), and this
        property additionally prevents a caller from replacing
        `self._datagram_log` with a completely different object."""
        return self._datagram_log

    def add_datagram_observer(self, callback: Callable[[DatagramLogEntry], None]) -> None:
        """Registers an observer that sees every datagram actually sent -
        including the ones `Runtime._notify_observers` deliberately skips
        (full resend, decaying a pulse; see that docstring). This is
        exactly why this chain hangs off the sender here and not off the
        runtime: see the module docstring, "Recording" section.

        Thin forward onto `self._datagram_log.add_observer` since the
        task 7, fix 2 follow-up - see the module docstring, "Observer
        chain" section, for why this method nonetheless remains its own
        public interface instead of being replaced by
        `sender.datagram_log.add_observer`.

        The observer is called from within `_record_sent`, i.e. AFTER the
        `sendto()` and AFTER the append to `datagram_log`."""
        self._datagram_log.add_observer(callback)

    def remove_datagram_observer(self, callback: Callable[[DatagramLogEntry], None]) -> None:
        """Unsubscribes an observer. An unknown observer (e.g. unsubscribed
        twice) is not an error, it is silently ignored - the same rule as
        for `Runtime.remove_observer` (taken over here via
        `self._datagram_log.remove_observer`, see there)."""
        self._datagram_log.remove_observer(callback)

    async def send(self, key: str, value: float | bool, *, force: bool = False) -> bool:
        """Sends when the value has changed or force is set."""
        if self._socket is None:
            raise RuntimeError("UdpSender ist geschlossen")

        packet = datagram(key, value)
        text = packet.decode()
        if not force and self._last_sent.get(key) == text:
            return False

        async with self._lock:
            if self._socket is None:
                raise RuntimeError("UdpSender ist geschlossen")
            loop = asyncio.get_running_loop()
            wait_time = self._next_send_time - loop.time()
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._socket.sendto(packet, self._target)
            self._next_send_time = loop.time() + self._interval
            # Only AFTER the actual sendto() - see the module docstring.
            # A skipped (debounced) value above never reaches this line,
            # whereas a force=True resend does: both are correct, since
            # both describe what actually went over the wire. `force` is
            # passed through unchanged (task 6 follow-up, 2026-09-03):
            # `DatagramLogEntry.forced` thereby records WHY it was sent -
            # the only reliable source of this distinction, see that
            # docstring.
            self._record_sent(key, text, force)

        self._last_sent[key] = text
        return True

    def _record_sent(self, key: str, text: str, forced: bool) -> None:
        """Appends an entry to `datagram_log` - isolated in its own
        try/except (task 6 report, point 1): a failure while recording
        (none apparent today, but a later refactor could introduce one)
        must never retroactively turn an already-completed send into a
        failure - `send()` has already sent its datagram long before this
        point.

        Notifying the observer chain is handled by `RingBuffer.append`
        itself (since the task 7, fix 2 follow-up - see the module
        docstring, "Observer chain" section), no longer by a dedicated
        `_notify_datagram_observers` method here: `append` already calls
        every registered observer after appending, using the same
        copy-while-iterating and the same log-and-skip rule (see
        `api.diagnostics.RingBuffer`).

        `forced` is `send()`'s `force` argument, passed through unchanged -
        see `DatagramLogEntry.forced` for why exactly this spot knows it
        and a timing heuristic in the browser cannot replace it."""
        try:
            _, _, value_text = text.partition(":")
            entry = DatagramLogEntry(key=key, value=value_text, timestamp=now_iso(), forced=forced)
            self._datagram_log.append(entry)
        except Exception:
            logger.exception(
                "recording the sent datagram for key %r failed - "
                "the send itself is not affected by this",
                key,
            )

    async def close(self) -> None:
        """Closes the socket. Takes the same lock as send(), so that a send
        currently stuck in the rate-limit sleep does not hit an already
        closed socket. Calling it more than once remains harmless.
        """
        async with self._lock:
            if self._socket is not None:
                self._socket.close()
                self._socket = None
