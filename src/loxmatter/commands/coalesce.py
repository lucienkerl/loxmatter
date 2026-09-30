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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

from loxmatter import i18n, sources
from loxmatter.sources import DeviceCall, DeviceUnreachableError

__all__ = ["VALUE_INTERVAL_SECONDS", "CommandGate", "slot_of"]

Invoker = Callable[[DeviceCall], Awaitable[None]]
Slot = tuple[int, str]

# The least time between the starts of two brightness or colour requests for
# one device (design 2026-09-30, section 3). On the test Pi one colour value
# - a colour call and a brightness call - took 160-250 ms on 30 September
# 2026, so this leaves the radio a short rest between them: at most about
# five Thread calls a second per lamp, and a lamp at most one interval and
# one round trip behind the slider.
VALUE_INTERVAL_SECONDS = 0.4

_CLUSTER_LEVEL = 8
_CLUSTER_COLOUR = 768
_LEVEL_COMMANDS = frozenset({0, 4})
# MoveToLevelWithOnOff. Unlike MoveToLevel (8, 0) it also switches the lamp:
# on for a level above the minimum, off at it. See `_strength_of`.
_LEVEL_WITH_ON_OFF = 4
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


def _strength_of(call: DeviceCall) -> int:
    """How much of its slot a call sets. A newer call replaces an older one
    only if it is at least as strong.

    (8, 4) sets the level AND switches the lamp, (8, 0) only sets the level.
    A waiting "level 0 with on/off" - an "off" - replaced by a plain level
    would be lost: the lamp would stay on. The other way round nothing is
    lost."""
    if call.cluster_id == _CLUSTER_LEVEL and call.command_id == _LEVEL_WITH_ON_OFF:
        return 1
    return 0


def _slots(calls: Sequence[DeviceCall]) -> dict[Slot, int] | None:
    """Each slot a request sets, with the strongest call that sets it, or
    `None` when a call has no slot."""
    slots: dict[Slot, int] = {}
    for device_call in calls:
        slot = slot_of(device_call)
        if slot is None:
            return None
        slots[slot] = max(slots.get(slot, 0), _strength_of(device_call))
    return slots


def _covers(newer: Mapping[Slot, int], older: Mapping[Slot, int]) -> bool:
    return all(slot in newer and newer[slot] >= strength for slot, strength in older.items())


def _consume(future: asyncio.Future[bool]) -> None:
    """Marks an outcome as seen, so a request whose caller went away does not
    log "exception was never retrieved" when its call fails.

    `asyncio.shield` does not do it for us: once the caller's side is
    cancelled, it removes its callback from the outcome."""
    if not future.cancelled():
        future.exception()


def _worker_cancelled() -> bool:
    """Whether the running task itself is being cancelled, as opposed to a
    `CancelledError` that only came out of a call it awaited."""
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


@dataclass(eq=False)
class _Request:
    calls: tuple[DeviceCall, ...]
    slots: dict[Slot, int] | None
    outcome: asyncio.Future[bool]
    # Event loop time at which the request joined its queue.
    queued_at: float = 0.0


@dataclass(eq=False)
class _Lane:
    waiting: list[_Request] = field(default_factory=list)
    worker: asyncio.Future[None] | None = None
    # Event loop time at which a call for this device last returned.
    progress_at: float = 0.0


class CommandGate:
    """Serialises requests per `(technology, address)`.

    One per application: `build_app` creates it and hands it to both command
    routes, so a Loxone value and a web UI click for the same lamp share one
    queue.

    `wait_timeout` bounds how long a request may wait while its device makes
    no progress. `None` reads `sources.SOURCE_CALL_TIMEOUT_SECONDS` each
    time a request waits, not once here: it is the bound of one call, so a
    test that shortens that constant shortens both.

    `min_interval` is the least time between the starts of two requests
    with slots for one device (design 2026-09-30). A request that would
    start sooner waits in the queue, where a newer value can still replace
    it. A request without slots - on, off, toggle - is never held back, and
    does not start the clock either. `build_app` passes
    `VALUE_INTERVAL_SECONDS`; the default of 0 keeps the gate as it was."""

    def __init__(
        self,
        invoke: Invoker,
        *,
        wait_timeout: float | None = None,
        min_interval: float = 0.0,
    ) -> None:
        self._invoke = invoke
        self._wait_timeout = wait_timeout
        self._min_interval = min_interval
        self._lanes: dict[tuple[str, str], _Lane] = {}
        # Event loop time at which the last request with slots started, per
        # device: what `min_interval` counts from. Kept here rather than in
        # the lane, because a lane goes when its queue drains, and a slider
        # whose values arrive a little slower than a lamp answers drains it
        # after every value.
        self._paced_at: dict[tuple[str, str], float] = {}
        # The outcome tasks `submit` started, held so that the event loop,
        # which keeps only weak references to tasks, does not collect one
        # while its request still waits.
        self._submitted: set[asyncio.Task[bool]] = set()

    async def run(self, calls: Sequence[DeviceCall]) -> bool:
        """Runs `calls` on their device after every request already running
        or waiting for it. Returns `False` when a newer request replaced
        these calls before they started, and raises what a call raised.

        A request that is still waiting when its device has not finished a
        call for the wait bound is taken out of the queue and raises
        `DeviceUnreachableError`, the 502 a silent device gets anywhere else:
        a device that has stopped answering is almost certainly not going to
        answer this one, and a Miniserver waits on the answer. The clock
        starts when the request joins the queue and starts over each time a
        call for the device returns, so a slow device that still answers -
        a colour value's two calls on a congested mesh - does not fail the
        requests behind it. A call that raises is not progress: it is how a
        silent device's call ends. A request that has started is waited for
        to the end, because each of its calls has a bound of its own.

        A caller that is cancelled while waiting does not take its request
        out of the queue: the value was sent to the bridge, and a Miniserver
        dropping the connection does not mean it no longer wants it."""
        queued = self._enqueue(calls)
        if queued is None:
            return True
        return await self._outcome(*queued)

    def submit(self, calls: Sequence[DeviceCall]) -> asyncio.Future[bool]:
        """Queues `calls` like `run`, but without waiting for them: the
        request is in its device's queue when this returns, in the order
        `submit` was called, and the returned future holds what `run` would
        have returned or raised (design 2026-09-30).

        Whoever needs to know how the request ended adds a callback to the
        future; an outcome nobody looks at is not logged as unretrieved.
        The wait bound applies as it does to `run`, so a request for a
        device that stopped answering still leaves the queue."""
        queued = self._enqueue(calls)
        if queued is None:
            done: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            done.set_result(True)
            return done
        task = asyncio.ensure_future(self._outcome(*queued))
        self._submitted.add(task)
        task.add_done_callback(self._submitted.discard)
        task.add_done_callback(_consume)
        return task

    def _enqueue(
        self, calls: Sequence[DeviceCall]
    ) -> tuple[tuple[str, str], _Lane, _Request] | None:
        """Puts a request into its device's queue and makes sure a worker
        runs it. Awaits nothing, so two requests queue in the order they
        were handed in. `None` for an empty request, which has nothing to
        queue."""
        if not calls:
            return None
        key = (calls[0].technology, calls[0].address)
        if any((c.technology, c.address) != key for c in calls):
            raise ValueError("one request must address one device")
        request = _Request(
            calls=tuple(calls),
            slots=_slots(calls),
            outcome=asyncio.get_running_loop().create_future(),
            queued_at=asyncio.get_running_loop().time(),
        )
        request.outcome.add_done_callback(_consume)
        lane = self._lanes.setdefault(key, _Lane())
        if request.slots is not None:
            _supersede(lane, request.slots)
        lane.waiting.append(request)
        # `done()` as well as `None`: a worker cancelled before its first
        # step never reaches its `finally`, and would otherwise stay in the
        # lane and strand every request for the device.
        if lane.worker is None or lane.worker.done():
            lane.worker = asyncio.ensure_future(self._drain(key, lane))
        return key, lane, request

    async def _outcome(self, key: tuple[str, str], lane: _Lane, request: _Request) -> bool:
        seconds = (
            sources.SOURCE_CALL_TIMEOUT_SECONDS
            if self._wait_timeout is None
            else self._wait_timeout
        )
        loop = asyncio.get_running_loop()
        remaining = seconds
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(request.outcome), remaining)
            except TimeoutError:
                pass
            # Decided from state, not from the timer: between the timer
            # firing and this line the worker may have run. Nothing below
            # awaits before the request is out of `waiting`, so the worker
            # cannot pop it after this check has seen it there.
            if request.outcome.done():
                # Finished after all - or a call itself raised TimeoutError,
                # which `result()` hands on unchanged.
                return request.outcome.result()
            if request not in lane.waiting:
                # Started: its calls are bounded one by one.
                return await asyncio.shield(request.outcome)
            idle = loop.time() - max(request.queued_at, lane.progress_at)
            if idle < seconds:
                # The device finished a call while this request waited: the
                # clock starts over from that call.
                remaining = seconds - idle
                continue
            lane.waiting.remove(request)
            error = DeviceUnreachableError(i18n.t("api.errors.device_timed_out", seconds=seconds))
            request.outcome.set_exception(error)
            if (
                not lane.waiting
                and (lane.worker is None or lane.worker.done())
                and self._lanes.get(key) is lane
            ):
                del self._lanes[key]
            raise error

    async def _drain(self, key: tuple[str, str], lane: _Lane) -> None:
        """Runs one device's queue until it is empty.

        Nothing is awaited between the last emptiness check and the `finally`
        below, so a request appended while the last one runs is always
        picked up by this loop, never stranded between two workers."""
        loop = asyncio.get_running_loop()
        try:
            while lane.waiting:
                pause = self._pause_before(key, lane.waiting[0], loop.time())
                if pause > 0:
                    # The request stays in the queue while it waits for its
                    # turn, so a newer value can still replace it, and the
                    # loop looks again at whatever is first afterwards: the
                    # replacement, or nothing if the wait bound took the
                    # request out.
                    await asyncio.sleep(pause)
                    continue
                request = lane.waiting.pop(0)
                if request.slots is not None:
                    self._paced_at[key] = loop.time()
                try:
                    for device_call in request.calls:
                        await self._invoke(device_call)
                        lane.progress_at = asyncio.get_running_loop().time()
                except asyncio.CancelledError:
                    if _worker_cancelled():
                        request.outcome.cancel()
                        raise
                    # Not this task: the call's own future was cancelled.
                    # matter-server's client does that to every call still
                    # waiting when its websocket closes. That request
                    # failed; the ones behind it did not.
                    request.outcome.set_exception(
                        DeviceUnreachableError(i18n.t("api.errors.device_call_cut_off"))
                    )
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

    def _pause_before(self, key: tuple[str, str], request: _Request, now: float) -> float:
        """How long `request` must still wait before it may start, which is
        only ever more than 0 for a request with slots that would follow the
        last one sooner than `min_interval`."""
        paced_at = self._paced_at.get(key)
        if request.slots is None or paced_at is None:
            return 0.0
        return paced_at + self._min_interval - now


def _supersede(lane: _Lane, slots: Mapping[Slot, int]) -> None:
    """Replaces every waiting request whose slots `slots` covers.

    Only among the requests after the last waiting one that cannot be
    replaced: a new value must not jump past a toggle, on or off that was
    sent after the value it would replace. What a toggle does depends on
    the state the value before it left, so the device sees the two in the
    order they were sent, and only values that would have run one straight
    after the other collapse into the newest."""
    start = 0
    for index, waiting in enumerate(lane.waiting):
        if waiting.slots is None:
            start = index + 1
    for waiting in lane.waiting[start:]:
        if waiting.slots is not None and _covers(slots, waiting.slots):
            lane.waiting.remove(waiting)
            waiting.outcome.set_result(False)
