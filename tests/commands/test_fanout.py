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

"""The group fan-out - see design 2026-09-10, section 3."""

from __future__ import annotations

import asyncio

import pytest

from loxmatter.commands.fanout import dispatch_group, plan_group_calls
from loxmatter.commands.translate import MatterCall, UnsupportedValueError
from loxmatter.model.store import GroupTarget, StoredCommand


def command(
    device_id: int,
    node_id: int,
    endpoint: int,
    cluster_id: int,
    command_id: int,
    slug: str,
    takes_value: bool,
) -> StoredCommand:
    return StoredCommand(
        key=f"d{device_id}_{endpoint}_{slug}",
        slug=slug,
        node_id=node_id,
        endpoint=endpoint,
        cluster_id=cluster_id,
        command_id=command_id,
        takes_value=takes_value,
        device_id=device_id,
    )


def on_target(device_id: int, node_id: int, label: str) -> GroupTarget:
    return GroupTarget(
        device_id=device_id,
        device_label=label,
        commands=(command(device_id, node_id, 1, 6, 1, "on", False),),
    )


def colour_target(device_id: int, node_id: int, label: str) -> GroupTarget:
    return GroupTarget(
        device_id=device_id,
        device_label=label,
        commands=(command(device_id, node_id, 1, 768, 6, "color", True),),
    )


def test_one_plan_per_member_carrying_that_member_s_node_id():
    plans = plan_group_calls([on_target(1, 11, "A"), on_target(2, 22, "B")], "1")
    assert [p.device_label for p in plans] == ["A", "B"]
    assert [call.node_id for p in plans for call in p.calls] == [11, 22]


def test_a_member_with_two_endpoints_gets_two_calls():
    target = GroupTarget(
        device_id=1,
        device_label="A",
        commands=(
            command(1, 11, 1, 6, 1, "on", False),
            command(1, 11, 2, 6, 1, "on", False),
        ),
    )
    (plan,) = plan_group_calls([target], "1")
    assert [call.endpoint for call in plan.calls] == [1, 2]


def test_a_bad_value_is_reported_before_anything_is_sent():
    with pytest.raises(UnsupportedValueError):
        plan_group_calls([colour_target(1, 11, "A")], "not a number")


async def test_the_calls_of_one_member_keep_their_order():
    """The colour path sends colour and then brightness to ONE device, and
    that order is deliberate (`_EXECUTE_IF_OFF` in translate.py). A flat
    gather over all calls of all members would destroy it."""
    plans = plan_group_calls([colour_target(1, 11, "A"), colour_target(2, 22, "B")], "60100060")
    seen: list[tuple[int, int]] = []

    async def invoke(call: MatterCall) -> None:
        seen.append((call.node_id, call.command_id))
        await asyncio.sleep(0)

    assert await dispatch_group(plans, invoke) == []
    for node_id in (11, 22):
        own = [command_id for node, command_id in seen if node == node_id]
        expected = [
            call.command_id
            for plan in plans
            if plan.calls[0].node_id == node_id
            for call in plan.calls
        ]
        assert own == expected


async def test_a_member_s_second_call_waits_for_the_first_to_return():
    """The test above records *when invoke is entered*, which a flat
    gather would still get right by accident: `asyncio.gather` starts every
    task in the order it was created, so a member's own two calls are
    still entered in order even if they were gathered independently rather
    than awaited one after another - the reordering a flat gather actually
    causes only shows up while the first call is still IN FLIGHT.

    This test blocks the colour call until released, and checks that the
    brightness call has not been *invoked at all* while colour is still
    pending - not merely that it hasn't finished. `_run_member` cannot
    call `invoke` for the second call until the `await` on the first one
    returns; a flat gather over both calls would start both immediately.
    """
    (plan,) = plan_group_calls([colour_target(1, 11, "A")], "60100060")
    colour_released = asyncio.Event()
    level_invoked = False

    async def invoke(call: MatterCall) -> None:
        nonlocal level_invoked
        if call.cluster_id == 768:  # ColorControl - the first call
            await asyncio.wait_for(colour_released.wait(), timeout=2)
        else:  # LevelControl - must not run before colour is released
            level_invoked = True

    task = asyncio.ensure_future(dispatch_group([plan], invoke))
    # `dispatch_group` needs a tick to spawn its member task(s), and that
    # task needs a further tick to actually reach and block on the colour
    # call - a real (if tiny) sleep, rather than a single `sleep(0)`,
    # covers that without hard-coding how many bare hops apart it is.
    await asyncio.sleep(0.01)
    assert level_invoked is False, "level was invoked before colour returned"
    colour_released.set()
    assert await task == []
    assert level_invoked is True


async def test_members_are_dispatched_concurrently():
    """Two members whose invocations each block until the other has
    started. A sequential dispatcher deadlocks here and the test times
    out; a concurrent one passes."""
    started = asyncio.Event()
    second = asyncio.Event()
    plans = plan_group_calls([on_target(1, 11, "A"), on_target(2, 22, "B")], "1")

    async def invoke(call: MatterCall) -> None:
        if call.node_id == 11:
            started.set()
            await asyncio.wait_for(second.wait(), timeout=2)
        else:
            await asyncio.wait_for(started.wait(), timeout=2)
            second.set()

    assert await dispatch_group(plans, invoke) == []


async def test_a_failing_member_does_not_stop_the_others():
    plans = plan_group_calls(
        [on_target(1, 11, "A"), on_target(2, 22, "B"), on_target(3, 33, "C")], "1"
    )
    reached: list[int] = []

    async def invoke(call: MatterCall) -> None:
        if call.node_id == 22:
            raise RuntimeError("no route to host")
        reached.append(call.node_id)

    assert await dispatch_group(plans, invoke) == ["B"]
    assert sorted(reached) == [11, 33]


async def test_every_failing_member_is_named_not_just_the_first():
    plans = plan_group_calls(
        [on_target(1, 11, "A"), on_target(2, 22, "B"), on_target(3, 33, "C")], "1"
    )

    async def invoke(call: MatterCall) -> None:
        if call.node_id in (11, 33):
            raise RuntimeError("no route to host")

    assert await dispatch_group(plans, invoke) == ["A", "C"]
