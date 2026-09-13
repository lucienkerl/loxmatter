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

"""One request per device at a time, and only the newest value waits.

Design 2026-09-13 (command coalescing). A Loxone slider sends a value about
once a second without waiting for the answer; sent as they came, those
values overlapped on the same lamps and queued up on the Thread radio until
the OpenThread agent gave up (13 September 2026, 19:27-19:28 UTC). Here each
device gets one queue: a request runs only when the previous one for that
device has finished, and a brightness or colour value that is still waiting
is replaced by a newer one of the same kind.

A *request* is the list of calls one command produces for one device - the
unit whose order matters, because the colour call has to reach a lamp before
the brightness call that switches it on (`commands/translate.py`,
`to_device_calls`). The gate never splits one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from loxmatter.sources import DeviceCall

__all__ = ["CommandGate", "slot_of"]

Invoker = Callable[[DeviceCall], Awaitable[None]]
Slot = tuple[int, str]

_CLUSTER_LEVEL = 8
_CLUSTER_COLOUR = 768
_LEVEL_COMMANDS = frozenset({0, 4})
# MoveToHueAndSaturation, MoveToColor and MoveToColorTemperature: colour and
# white are the same output of a lamp, so a newer one of either replaces an
# older one of either.
_COLOUR_COMMANDS = frozenset({6, 7, 10})


def slot_of(call: DeviceCall) -> Slot | None:
    """What a call sets, if it sets a value a newer call may replace.

    On, off and toggle have no slot: two toggles are not one toggle, and a
    lamp told "off" then "on" must end up on."""
    if call.cluster_id == _CLUSTER_LEVEL and call.command_id in _LEVEL_COMMANDS:
        return (call.endpoint, "level")
    if call.cluster_id == _CLUSTER_COLOUR and call.command_id in _COLOUR_COMMANDS:
        return (call.endpoint, "colour")
    return None


def _slots(calls: Sequence[DeviceCall]) -> frozenset[Slot] | None:
    slots: set[Slot] = set()
    for device_call in calls:
        slot = slot_of(device_call)
        if slot is None:
            return None
        slots.add(slot)
    return frozenset(slots)


def _consume(future: asyncio.Future[bool]) -> None:
    """Marks an outcome as seen, so a request whose caller went away does not
    log "exception was never retrieved" when its call fails."""
    if not future.cancelled():
        future.exception()


@dataclass(eq=False)
class _Request:
    calls: tuple[DeviceCall, ...]
    slots: frozenset[Slot] | None
    outcome: asyncio.Future[bool]


@dataclass(eq=False)
class _Lane:
    waiting: list[_Request] = field(default_factory=list)
    worker: asyncio.Future[None] | None = None


class CommandGate:
    """Serialises requests per `(technology, address)`.

    One per application: `build_app` creates it and hands it to both command
    routes, so a Loxone value and a web UI click for the same lamp share one
    queue."""

    def __init__(self, invoke: Invoker) -> None:
        self._invoke = invoke
        self._lanes: dict[tuple[str, str], _Lane] = {}

    async def run(self, calls: Sequence[DeviceCall]) -> bool:
        """Runs `calls` on their device after every request already running
        or waiting for it. Returns `False` when a newer request replaced
        these calls before they started, and raises what a call raised.

        A caller that is cancelled while waiting does not take its request
        out of the queue: the value was sent to the bridge, and a Miniserver
        dropping the connection does not mean it no longer wants it."""
        if not calls:
            return True
        key = (calls[0].technology, calls[0].address)
        if any((c.technology, c.address) != key for c in calls):
            raise ValueError("one request must address one device")
        request = _Request(
            calls=tuple(calls),
            slots=_slots(calls),
            outcome=asyncio.get_running_loop().create_future(),
        )
        request.outcome.add_done_callback(_consume)
        lane = self._lanes.setdefault(key, _Lane())
        if request.slots is not None:
            for waiting in list(lane.waiting):
                if waiting.slots is not None and waiting.slots <= request.slots:
                    lane.waiting.remove(waiting)
                    waiting.outcome.set_result(False)
        lane.waiting.append(request)
        if lane.worker is None:
            lane.worker = asyncio.ensure_future(self._drain(key, lane))
        return await asyncio.shield(request.outcome)

    async def _drain(self, key: tuple[str, str], lane: _Lane) -> None:
        """Runs one device's queue until it is empty.

        Nothing is awaited between the last emptiness check and the `finally`
        below, so a request appended while the last one runs is always
        picked up by this loop, never stranded between two workers."""
        try:
            while lane.waiting:
                request = lane.waiting.pop(0)
                try:
                    for device_call in request.calls:
                        await self._invoke(device_call)
                except asyncio.CancelledError:
                    request.outcome.cancel()
                    raise
                except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's `run`
                    request.outcome.set_exception(exc)
                else:
                    request.outcome.set_result(True)
        except asyncio.CancelledError:
            for waiting in lane.waiting:
                waiting.outcome.cancel()
            lane.waiting.clear()
            raise
        finally:
            lane.worker = None
            if not lane.waiting and self._lanes.get(key) is lane:
                del self._lanes[key]
