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
    """Pins the order `invoke` is *entered* for one member's calls, end to
    end through `plan_group_calls` and `dispatch_group`: colour before
    brightness, per member, matching the plan.

    This does not by itself catch a flat gather over every member's calls:
    `seen.append` runs before this coroutine's only `await`, and
    `asyncio.gather` starts every coroutine's synchronous prefix in
    creation order, so entry order comes out right even under a flat
    gather. The guarantee that actually rules a flat gather out - a
    member's second call not starting until its first has returned - is
    `test_a_member_s_second_call_waits_for_the_first_to_return`'s job; do
    not delete this test as redundant with that one, each pins a
    different half of the ordering contract."""
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

    task = asyncio.create_task(dispatch_group([plan], invoke))
    # `dispatch_group` needs a tick to spawn its member task(s), and that
    # task needs a further tick to actually reach and block on the colour
    # call - a real (if tiny) sleep, rather than a single `sleep(0)`,
    # covers that without hard-coding how many bare hops apart it is.
    await asyncio.sleep(0.01)
    try:
        assert level_invoked is False, "level was invoked before colour returned"
    finally:
        # Always release the member task, even if the assertion above
        # fires - otherwise a failure here leaves it parked on its 2s
        # `wait_for` and the real assertion error is buried under a
        # timeout from the still-running task.
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


async def test_duplicate_labels_among_the_failed_members_are_disambiguated_by_id():
    """Regression for the unread `MemberPlan.device_id` (final review,
    Item 3): two members named "Lamp" that both fail must not both come
    back as the bare string "Lamp" - `api/control.py` and
    `loxone/server.py` build "no answer from: {devices}" straight from
    this list, and "no answer from: Lamp, Lamp" does not tell a reader
    which of the two is actually unreachable."""
    plans = plan_group_calls([on_target(1, 11, "Lamp"), on_target(2, 22, "Lamp")], "1")

    async def invoke(call: MatterCall) -> None:
        raise RuntimeError("no route to host")

    assert await dispatch_group(plans, invoke) == ["Lamp (1)", "Lamp (2)"]


async def test_a_unique_label_among_the_failed_members_stays_bare():
    """Uniqueness is checked against the FAILED set, not the whole group:
    two members share the label "Lamp", but only one of them fails, so
    that failure is not ambiguous among failures and must stay bare - the
    disambiguating id is for when two *failed* members would otherwise
    read the same, not for every label the group happens to repeat
    somewhere among its reachable members."""
    plans = plan_group_calls(
        [on_target(1, 11, "Lamp"), on_target(2, 22, "Lamp"), on_target(3, 33, "Kitchen Lamp")],
        "1",
    )

    async def invoke(call: MatterCall) -> None:
        if call.node_id == 11:
            return
        raise RuntimeError("no route to host")

    assert await dispatch_group(plans, invoke) == ["Lamp", "Kitchen Lamp"]


async def test_every_failing_member_is_named_not_just_the_first():
    """Failures must come back in *plan* order, not completion order, so a
    caller's message is reproducible. A is planned before C but made to
    fail later in wall-clock time (the `sleep` below); a dispatcher that
    collected labels as members completed would return ["C", "A"] here."""
    plans = plan_group_calls(
        [on_target(1, 11, "A"), on_target(2, 22, "B"), on_target(3, 33, "C")], "1"
    )

    async def invoke(call: MatterCall) -> None:
        if call.node_id == 11:
            await asyncio.sleep(0.01)
            raise RuntimeError("no route to host")
        if call.node_id == 33:
            raise RuntimeError("no route to host")

    assert await dispatch_group(plans, invoke) == ["A", "C"]
