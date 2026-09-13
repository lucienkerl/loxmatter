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

import pytest

from loxmatter.commands.coalesce import CommandGate, slot_of
from loxmatter.sources import DeviceCall


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
    assert await after is True


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
