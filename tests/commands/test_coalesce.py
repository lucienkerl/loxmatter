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

"""One request per device at a time - design 2026-09-13 (command coalescing)."""

from __future__ import annotations

import asyncio
import gc

import pytest

from loxmatter import i18n
from loxmatter.commands.coalesce import CommandGate, slot_of
from loxmatter.sources import DeviceCall, DeviceUnreachableError


def call(
    address: str, cluster_id: int, command_id: int, level: int | None = None, endpoint: int = 1
) -> DeviceCall:
    payload: dict[str, object] = {} if level is None else {"level": level, "transitionTime": 0}
    return DeviceCall(
        technology="matter",
        address=address,
        endpoint=endpoint,
        cluster_id=cluster_id,
        command_id=command_id,
        payload=payload,
    )


def level(address: str, value: int, endpoint: int = 1) -> list[DeviceCall]:
    return [call(address, 8, 4, value, endpoint)]


def colour_and_level(address: str, value: int, endpoint: int = 1) -> list[DeviceCall]:
    return [call(address, 768, 6, endpoint=endpoint), call(address, 8, 4, value, endpoint)]


class SlowDevices:
    """An invoker whose calls block until the test releases them, and which
    records what ran and how many calls overlapped per address."""

    def __init__(self) -> None:
        self.ran: list[DeviceCall] = []
        self.active: dict[str, int] = {}
        self.max_active: dict[str, int] = {}
        self.release = asyncio.Event()
        self.failing: set[tuple[str, int, int]] = set()

    async def __call__(self, device_call: DeviceCall) -> None:
        address = device_call.address
        self.active[address] = self.active.get(address, 0) + 1
        self.max_active[address] = max(self.max_active.get(address, 0), self.active[address])
        try:
            await self.release.wait()
            if (address, device_call.cluster_id, device_call.command_id) in self.failing:
                raise RuntimeError(f"no answer from {address}")
            self.ran.append(device_call)
        finally:
            self.active[address] -= 1


async def _let_run() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def test_slots():
    assert slot_of(call("a", 8, 0)) == (1, "level")
    assert slot_of(call("a", 8, 4)) == (1, "level")
    for command_id in (6, 7, 10):
        assert slot_of(call("a", 768, command_id)) == (1, "colour")
    for pair in ((6, 0), (6, 1), (6, 2), (3, 0), (8, 1)):
        assert slot_of(call("a", *pair)) is None


async def test_one_device_never_runs_two_requests_at_once_but_two_devices_do():
    """Fault to prove it: call `invoke` directly in `run` without the queue -
    `max_active["lamp"]` becomes 2."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    tasks = [
        asyncio.ensure_future(gate.run(level("lamp", 10))),
        asyncio.ensure_future(gate.run([call("lamp", 6, 2)])),
        asyncio.ensure_future(gate.run(level("other", 20))),
    ]
    await _let_run()
    assert devices.active == {"lamp": 1, "other": 1}
    devices.release.set()
    assert await asyncio.gather(*tasks) == [True, True, True]
    assert devices.max_active == {"lamp": 1, "other": 1}


async def test_only_the_newest_waiting_value_is_sent():
    """Three brightness values while the lamp is busy with the first: the
    second is superseded, the third runs.

    Fault to prove it: never supersede - the lamp receives 10, 20 and 30."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    first = asyncio.ensure_future(gate.run(level("lamp", 10)))
    await _let_run()
    second = asyncio.ensure_future(gate.run(level("lamp", 20)))
    await _let_run()
    third = asyncio.ensure_future(gate.run(level("lamp", 30)))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(first, second, third) == [True, False, True]
    assert [c.payload["level"] for c in devices.ran] == [10, 30]


async def test_a_colour_value_supersedes_a_waiting_brightness_on_its_endpoint_only():
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run(level("lamp", 1)))
    await _let_run()
    same_endpoint = asyncio.ensure_future(gate.run(level("lamp", 20, endpoint=1)))
    other_endpoint = asyncio.ensure_future(gate.run(level("lamp", 30, endpoint=2)))
    await _let_run()
    newer = asyncio.ensure_future(gate.run(colour_and_level("lamp", 40, endpoint=1)))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(busy, same_endpoint, other_endpoint, newer) == [
        True,
        False,
        True,
        True,
    ]


async def test_toggle_is_never_superseded_and_keeps_its_order():
    """Fault to prove it: give (6, 2) a slot - the second toggle disappears."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run(level("lamp", 1)))
    await _let_run()
    first = asyncio.ensure_future(gate.run([call("lamp", 6, 2)]))
    second = asyncio.ensure_future(gate.run([call("lamp", 6, 2)]))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(busy, first, second) == [True, True, True]
    assert [(c.cluster_id, c.command_id) for c in devices.ran] == [(8, 4), (6, 2), (6, 2)]


async def test_a_waiting_request_with_an_unslotted_call_is_never_superseded():
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run(level("lamp", 1)))
    await _let_run()
    mixed = asyncio.ensure_future(gate.run([call("lamp", 6, 1), call("lamp", 8, 4, 50)]))
    await _let_run()
    newer = asyncio.ensure_future(gate.run(colour_and_level("lamp", 60)))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(busy, mixed, newer) == [True, True, True]


async def test_an_error_reaches_its_own_caller_and_the_next_request_still_runs():
    devices = SlowDevices()
    gate = CommandGate(devices)
    devices.failing.add(("lamp", 6, 1))
    failing = asyncio.ensure_future(gate.run([call("lamp", 6, 1)]))
    await _let_run()
    after = asyncio.ensure_future(gate.run([call("lamp", 6, 0)]))
    await _let_run()
    devices.release.set()
    with pytest.raises(RuntimeError):
        await failing
    # Bounded: if the error ended the worker, `after` would wait forever and
    # the test would hang instead of failing.
    assert await asyncio.wait_for(after, timeout=1) is True


async def test_the_device_entry_is_dropped_once_its_queue_drains():
    """Fault to prove it: never delete the lane - `_lanes` keeps the key."""
    devices = SlowDevices()
    devices.release.set()
    gate = CommandGate(devices)
    assert await gate.run(level("lamp", 10)) is True
    await _let_run()
    assert gate._lanes == {}


async def test_a_cancelled_caller_does_not_stop_the_requests_behind_it():
    """Fault to prove it: await the outcome without `asyncio.shield` - the
    caller's cancellation cancels the outcome, the worker's `set_result` on
    it raises and ends the worker, and `behind` is never run."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run([call("lamp", 6, 1)]))
    await _let_run()
    impatient = asyncio.ensure_future(gate.run([call("lamp", 6, 2)]))
    behind = asyncio.ensure_future(gate.run([call("lamp", 6, 0)]))
    await _let_run()
    impatient.cancel()
    devices.release.set()
    assert await busy is True
    # Bounded, not a bare await: a worker that died on the cancelled request
    # leaves `behind` waiting forever, and the test would hang, not fail.
    assert await asyncio.wait_for(behind, timeout=1) is True
    assert [(c.cluster_id, c.command_id) for c in devices.ran] == [(6, 1), (6, 2), (6, 0)]


async def test_an_empty_request_runs_nothing_and_succeeds():
    gate = CommandGate(SlowDevices())
    assert await gate.run([]) is True


async def test_calls_for_two_devices_in_one_request_are_refused():
    gate = CommandGate(SlowDevices())
    with pytest.raises(ValueError):
        await gate.run([call("a", 6, 1), call("b", 6, 1)])


def level_only(address: str, value: int, endpoint: int = 1) -> list[DeviceCall]:
    """MoveToLevel (8, 0): a level without switching the lamp."""
    return [call(address, 8, 0, value, endpoint)]


async def test_a_waiting_request_is_not_superseded_by_a_mixed_request():
    """A request with an unslotted call supersedes nothing: the new request
    switches the lamp on AND sets a level, and the waiting level still runs.

    Fault to prove it: let a mixed request supersede with the slots of its
    slotted calls - the waiting level 20 disappears."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run(level("lamp", 1)))
    await _let_run()
    waiting = asyncio.ensure_future(gate.run(level("lamp", 20)))
    await _let_run()
    mixed = asyncio.ensure_future(gate.run([call("lamp", 6, 1), call("lamp", 8, 4, 50)]))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(busy, waiting, mixed) == [True, True, True]
    assert [c.payload.get("level") for c in devices.ran] == [1, 20, None, 50]


async def test_a_plain_level_does_not_supersede_a_waiting_level_with_on_off():
    """A waiting (8, 4) at level 0 is an "off". A newer (8, 0) sets the level
    only, so replacing the (8, 4) with it would lose the "off": both run.

    Fault to prove it: give (8, 0) the strength of (8, 4) - the "off" is
    superseded and never sent."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run(level("lamp", 1)))
    await _let_run()
    off = asyncio.ensure_future(gate.run(level("lamp", 0)))
    await _let_run()
    plain = asyncio.ensure_future(gate.run(level_only("lamp", 50)))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(busy, off, plain) == [True, True, True]
    assert [(c.command_id, c.payload["level"]) for c in devices.ran] == [(4, 1), (4, 0), (0, 50)]


async def test_a_level_with_on_off_supersedes_a_waiting_plain_level():
    """The other way round nothing is lost: (8, 4) sets everything a waiting
    (8, 0) sets, and replaces it."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run(level("lamp", 1)))
    await _let_run()
    plain = asyncio.ensure_future(gate.run(level_only("lamp", 50)))
    await _let_run()
    with_on_off = asyncio.ensure_future(gate.run(level("lamp", 60)))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(busy, plain, with_on_off) == [True, False, True]
    assert [(c.command_id, c.payload["level"]) for c in devices.ran] == [(4, 1), (4, 60)]


async def test_a_new_value_does_not_jump_past_a_waiting_toggle():
    """Level 10, then a toggle, both waiting: a newer level supersedes
    nothing before the toggle, so the device still sees 10, toggle, 30.

    Fault to prove it: scan every waiting request - level 10 disappears."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run(level("lamp", 1)))
    await _let_run()
    before = asyncio.ensure_future(gate.run(level("lamp", 10)))
    toggle = asyncio.ensure_future(gate.run([call("lamp", 6, 2)]))
    await _let_run()
    after = asyncio.ensure_future(gate.run(level("lamp", 30)))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(busy, before, toggle, after) == [True, True, True, True]
    assert [(c.cluster_id, c.payload.get("level")) for c in devices.ran] == [
        (8, 1),
        (8, 10),
        (6, None),
        (8, 30),
    ]


async def test_a_call_cancelled_by_its_source_fails_only_its_own_request():
    """matter-server's client cancels the future of every call still waiting
    for an answer when its websocket closes. That `CancelledError` reaches
    the worker although nobody cancelled the worker. It is that request's
    failure - a `DeviceUnreachableError`, which the routes answer with 502 -
    and the request behind it still runs.

    Fault to prove it: re-raise every `CancelledError` as a cancellation of
    the worker - the caller gets `CancelledError` and `behind` is cancelled
    unsent."""
    loop = asyncio.get_running_loop()
    answer: asyncio.Future[None] = loop.create_future()
    ran: list[DeviceCall] = []

    async def invoke(device_call: DeviceCall) -> None:
        if device_call.command_id == 1:
            await answer
        ran.append(device_call)

    gate = CommandGate(invoke)
    cut_off = asyncio.ensure_future(gate.run([call("lamp", 6, 1)]))
    await _let_run()
    behind = asyncio.ensure_future(gate.run([call("lamp", 6, 0)]))
    await _let_run()
    answer.cancel()
    with pytest.raises(DeviceUnreachableError) as caught:
        await asyncio.wait_for(cut_off, timeout=1)
    assert str(caught.value) == i18n.t("api.errors.device_call_cut_off")
    assert await asyncio.wait_for(behind, timeout=1) is True
    assert [(c.cluster_id, c.command_id) for c in ran] == [(6, 0)]


async def test_a_request_waiting_too_long_is_refused_and_never_sent():
    """Behind a device that does not answer, a request waits at most the
    wait bound, then raises `DeviceUnreachableError` and leaves the queue.

    Fault to prove it: await the outcome without the bound - the caller is
    still waiting after a second; or leave the request in `waiting` - the
    device receives it once released."""
    devices = SlowDevices()
    gate = CommandGate(devices, wait_timeout=0.05)
    busy = asyncio.ensure_future(gate.run([call("lamp", 6, 1)]))
    await _let_run()
    waiting = asyncio.ensure_future(gate.run([call("lamp", 6, 0)]))
    with pytest.raises(DeviceUnreachableError) as caught:
        await asyncio.wait_for(waiting, timeout=1)
    assert str(caught.value) == i18n.t("api.errors.device_timed_out", seconds=0.05)
    devices.release.set()
    assert await asyncio.wait_for(busy, timeout=1) is True
    await _let_run()
    assert [(c.cluster_id, c.command_id) for c in devices.ran] == [(6, 1)]
    assert gate._lanes == {}


async def test_a_request_that_has_started_is_not_failed_by_the_wait_bound():
    """The wait bound is for waiting. A request still running when it
    passes is waited for, because its calls have a bound of their own.

    Fault to prove it: drop the "still in `waiting`" check - the running
    request raises `DeviceUnreachableError`."""
    devices = SlowDevices()
    gate = CommandGate(devices, wait_timeout=0.05)
    running = asyncio.ensure_future(gate.run([call("lamp", 6, 1)]))
    await _let_run()
    assert devices.active == {"lamp": 1}
    await asyncio.sleep(0.15)
    assert not running.done()
    devices.release.set()
    assert await asyncio.wait_for(running, timeout=1) is True


async def test_a_worker_cancelled_before_its_first_step_does_not_strand_the_device():
    """A worker task cancelled before it ever ran never reaches its
    `finally`, so the lane keeps a finished worker. The next request starts
    a new one, which runs both.

    Fault to prove it: start a worker only when `lane.worker is None` - the
    second request is never run."""
    devices = SlowDevices()
    devices.release.set()
    gate = CommandGate(devices)
    first = asyncio.ensure_future(gate.run([call("lamp", 6, 1)]))
    await asyncio.sleep(0)  # `run` has created the worker; it has not run
    worker = gate._lanes[("matter", "lamp")].worker
    assert worker is not None
    worker.cancel()
    await _let_run()
    assert worker.cancelled()
    assert devices.max_active == {}
    second = asyncio.ensure_future(gate.run([call("lamp", 6, 0)]))
    assert await asyncio.wait_for(asyncio.gather(first, second), timeout=1) == [True, True]
    assert [(c.cluster_id, c.command_id) for c in devices.ran] == [(6, 1), (6, 0)]


async def test_cancelling_the_worker_cancels_the_requests_waiting_for_it():
    """At shutdown the worker is cancelled. The running request and every
    waiting one end cancelled, so no caller waits for a worker that is gone.

    Fault to prove it: do not cancel the waiting outcomes - `waiting` is
    still pending a second later."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    running = asyncio.ensure_future(gate.run([call("lamp", 6, 1)]))
    await _let_run()
    waiting = asyncio.ensure_future(gate.run([call("lamp", 6, 0)]))
    await _let_run()
    worker = gate._lanes[("matter", "lamp")].worker
    assert worker is not None
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, timeout=1)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiting, timeout=1)
    assert devices.ran == []


async def test_a_failure_nobody_waits_for_is_not_logged_as_unretrieved():
    """A caller cancelled while its request waits leaves the outcome with no
    reader: `asyncio.shield` takes its callback off once its own side is
    cancelled. When that request then fails, the outcome's exception must
    still count as seen, or asyncio logs "Future exception was never
    retrieved" when the outcome is collected.

    Fault to prove it: drop `add_done_callback(_consume)` - the loop's
    exception handler receives that message."""
    loop = asyncio.get_running_loop()
    reported: list[dict[str, object]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    try:
        devices = SlowDevices()
        devices.failing.add(("lamp", 6, 0))
        gate = CommandGate(devices)
        busy = asyncio.ensure_future(gate.run([call("lamp", 6, 1)]))
        await _let_run()
        impatient = asyncio.ensure_future(gate.run([call("lamp", 6, 0)]))
        await _let_run()
        impatient.cancel()
        await _let_run()
        del impatient
        devices.release.set()
        assert await asyncio.wait_for(busy, timeout=1) is True
        await _let_run()
        assert gate._lanes == {}
        gc.collect()
        await _let_run()
    finally:
        loop.set_exception_handler(previous)
    assert [c["message"] for c in reported] == []


class TimedDevices:
    """An invoker whose calls take `seconds` each, except the calls listed in
    `hanging`, which never return. Records how many calls overlapped per
    address."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.hanging: set[tuple[str, int, int]] = set()
        self.ran: list[DeviceCall] = []
        self.active: dict[str, int] = {}
        self.max_active: dict[str, int] = {}

    async def __call__(self, device_call: DeviceCall) -> None:
        address = device_call.address
        self.active[address] = self.active.get(address, 0) + 1
        self.max_active[address] = max(self.max_active.get(address, 0), self.active[address])
        try:
            if (address, device_call.cluster_id, device_call.command_id) in self.hanging:
                await asyncio.Event().wait()
            await asyncio.sleep(self.seconds)
            self.ran.append(device_call)
        finally:
            self.active[address] -= 1


async def test_a_slow_device_that_still_answers_does_not_fail_the_request_behind_it():
    """A Loxone colour value is two calls. On a congested mesh each may take
    most of the bound, and the request as a whole longer than the bound. An
    "off" waiting behind it is not failed while the device keeps finishing
    calls: the waiting clock starts over with each one.

    Fault to prove it: never set `lane.progress_at` - the "off" raises
    `DeviceUnreachableError` at the bound, while the lamp is answering."""
    bound = 0.4
    devices = TimedDevices(seconds=0.6 * bound)
    gate = CommandGate(devices, wait_timeout=bound)
    colour = asyncio.ensure_future(gate.run(colour_and_level("lamp", 40)))
    await _let_run()
    off = asyncio.ensure_future(gate.run([call("lamp", 6, 0)]))
    assert await asyncio.wait_for(asyncio.gather(colour, off), timeout=3) == [True, True]
    assert [(c.cluster_id, c.command_id) for c in devices.ran] == [(768, 6), (8, 4), (6, 0)]
    assert devices.max_active == {"lamp": 1}


async def test_a_device_that_stops_making_progress_still_fails_its_waiters():
    """The other half: once the device stops finishing calls, a waiting
    request fails one bound after the last call that returned - not at the
    bound counted from its arrival, and not never.

    Faults to prove it, one at a time: never fail a waiting request (always
    `continue`) - the waiting request is still pending after three seconds;
    never set `lane.progress_at` - it fails before the bound after the first
    call has passed."""
    bound = 0.4
    devices = TimedDevices(seconds=0.6 * bound)
    devices.hanging.add(("lamp", 8, 4))
    gate = CommandGate(devices, wait_timeout=bound)
    loop = asyncio.get_running_loop()
    stuck = asyncio.ensure_future(gate.run(colour_and_level("lamp", 40)))
    await _let_run()
    started = loop.time()
    waiting = asyncio.ensure_future(gate.run([call("lamp", 6, 0)]))
    with pytest.raises(DeviceUnreachableError):
        await asyncio.wait_for(waiting, timeout=3)
    # The colour call returned at 0.24 s, so the bound runs out at 0.64 s.
    assert loop.time() - started >= 0.6
    assert [(c.cluster_id, c.command_id) for c in devices.ran] == [(768, 6)]
    worker = gate._lanes[("matter", "lamp")].worker
    assert worker is not None
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(stuck, timeout=1)


async def test_a_lane_whose_worker_still_runs_is_kept_when_a_waiter_times_out():
    """A request that times out leaves an empty queue behind while the
    request before it is still running. The lane must stay: a new request
    has to queue behind the running one, not start a second worker next to
    it.

    Fault to prove it: delete the lane on timeout whenever `waiting` is
    empty, without asking whether the worker runs - the new request runs
    alongside the old one and `max_active["lamp"]` becomes 2."""
    devices = SlowDevices()
    gate = CommandGate(devices, wait_timeout=0.05)
    running = asyncio.ensure_future(gate.run([call("lamp", 6, 1)]))
    await _let_run()
    waiting = asyncio.ensure_future(gate.run([call("lamp", 6, 0)]))
    with pytest.raises(DeviceUnreachableError):
        await asyncio.wait_for(waiting, timeout=1)
    newer = asyncio.ensure_future(gate.run([call("lamp", 6, 2)]))
    await _let_run()
    assert devices.max_active == {"lamp": 1}
    devices.release.set()
    assert await asyncio.wait_for(asyncio.gather(running, newer), timeout=1) == [True, True]
    assert devices.max_active == {"lamp": 1}
    assert [(c.cluster_id, c.command_id) for c in devices.ran] == [(6, 1), (6, 2)]
    await _let_run()
    assert gate._lanes == {}


async def test_the_timeout_path_drops_a_lane_with_nothing_left_to_run():
    """When the request that times out was the last one and no worker runs,
    the timeout itself removes the device's entry - no worker's `finally`
    is left to do it. That happens behind a worker cancelled before its
    first step.

    Fault to prove it: remove the `del self._lanes[key]` after a timeout -
    `_lanes` keeps the key."""
    devices = SlowDevices()
    gate = CommandGate(devices, wait_timeout=0.05)
    orphan = asyncio.ensure_future(gate.run([call("lamp", 6, 1)]))
    await asyncio.sleep(0)  # `run` has created the worker; it has not run
    worker = gate._lanes[("matter", "lamp")].worker
    assert worker is not None
    worker.cancel()
    with pytest.raises(DeviceUnreachableError):
        await asyncio.wait_for(orphan, timeout=1)
    assert gate._lanes == {}
    assert devices.ran == []


async def test_without_a_wait_timeout_the_source_call_bound_is_read_when_a_request_waits(
    monkeypatch,
):
    """`CommandGate(invoke)` reads `SOURCE_CALL_TIMEOUT_SECONDS` when a
    request waits, not when the gate is built: `build_app` builds it once at
    startup, and a test that shortens the constant must shorten the wait.

    Fault to prove it: resolve the constant in `__init__` - the request
    waits the full 10 s and `wait_for` gives up after one."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    monkeypatch.setattr("loxmatter.sources.SOURCE_CALL_TIMEOUT_SECONDS", 0.05)
    busy = asyncio.ensure_future(gate.run([call("lamp", 6, 1)]))
    await _let_run()
    waiting = asyncio.ensure_future(gate.run([call("lamp", 6, 0)]))
    with pytest.raises(DeviceUnreachableError) as caught:
        await asyncio.wait_for(waiting, timeout=1)
    assert str(caught.value) == i18n.t("api.errors.device_timed_out", seconds=0.05)
    devices.release.set()
    assert await asyncio.wait_for(busy, timeout=1) is True


async def test_a_plain_level_does_not_supersede_a_waiting_level_with_on_off_at_any_level():
    """(8, 4) switches the lamp at every level, not only at 0: a waiting
    "level 60 with on/off" also switches a lamp that is off on. A newer
    plain level 30 in its place would leave that lamp off.

    Fault to prove it: give (8, 4) its strength only at level 0 - the level
    60 is superseded and never sent."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run(level("lamp", 1)))
    await _let_run()
    on_at_60 = asyncio.ensure_future(gate.run(level("lamp", 60)))
    await _let_run()
    plain = asyncio.ensure_future(gate.run(level_only("lamp", 30)))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(busy, on_at_60, plain) == [True, True, True]
    assert [(c.command_id, c.payload["level"]) for c in devices.ran] == [(4, 1), (4, 60), (0, 30)]


async def test_supersession_starts_after_the_last_waiting_toggle_not_the_first():
    """Waiting: level 10, toggle, level 20, toggle. A newer level 30 comes
    after the second toggle, so it replaces nothing: level 20 waits in
    front of that toggle, and removing it changes what the toggle does.

    Fault to prove it: stop at the first unslotted request (`break` after
    `start = index + 1`) - level 20 is superseded."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run(level("lamp", 1)))
    await _let_run()
    queued = [
        asyncio.ensure_future(gate.run(level("lamp", 10))),
        asyncio.ensure_future(gate.run([call("lamp", 6, 2)])),
        asyncio.ensure_future(gate.run(level("lamp", 20))),
        asyncio.ensure_future(gate.run([call("lamp", 6, 2)])),
    ]
    await _let_run()
    newest = asyncio.ensure_future(gate.run(level("lamp", 30)))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(busy, *queued, newest) == [True] * 6
    assert [(c.cluster_id, c.payload.get("level")) for c in devices.ran] == [
        (8, 1),
        (8, 10),
        (6, None),
        (8, 20),
        (6, None),
        (8, 30),
    ]
