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

"""Sends one group command to every member of the group.

Its own module rather than a branch inside the two routes that use it:
`/cmd/{key}/{value}` (loxone/server.py) and `POST /api/commands/{key}`
(api/control.py) must fan out identically, and a copy in each would drift
- the same reason `commands.translate` exists as one module for both
(Main Spec 4.2).

Deliberately knows nothing about HTTP or the store. It receives targets
and hands back which members failed; turning that into a 400 or a 502 is
the routes' business, and keeping it out of here is what makes the
ordering and concurrency below testable without a server.

**Why this is a fan-out at all** is not this module's decision to
defend: no Matter server this bridge can use exposes group messaging, so
there is nothing to send a single group command to (design 2026-09-10,
section 1). If that ever changes, this module is the one that would gain
a second implementation, and nothing else in the feature would move.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from loxmatter.commands.adapt import adapt_group_command
from loxmatter.commands.translate import to_device_calls
from loxmatter.model.store import GroupTarget, StoredGroupCommand
from loxmatter.profiles.light_commands import LIGHT_COMMAND_PAIRS
from loxmatter.sources import DeviceCall, SourceNotConfiguredError

__all__ = ["GroupOutcome", "MemberPlan", "dispatch_group", "plan_group_calls"]


@dataclass(frozen=True)
class MemberPlan:
    """Everything one member is to receive, in the order it is to receive it.

    `device_id` disambiguates `device_label` in the failure report
    `dispatch_group` builds: device labels are not unique (nothing in this
    codebase requires it), so two failed members named the same would
    otherwise both read as plain "Lamp" in a 502 detail - see
    `dispatch_group`'s docstring."""

    device_id: int
    device_label: str
    calls: tuple[DeviceCall, ...]


def plan_group_calls(
    command: StoredGroupCommand, targets: Sequence[GroupTarget], value: str
) -> list[MemberPlan]:
    """Translates the group's value once per member.

    A light command (design 2026-09-13) goes through
    `commands.adapt.adapt_group_command`, which gives each member the part of
    the value it can carry - possibly nothing, which is not a failure: such a
    member gets an empty plan and `dispatch_group` sends it nothing. Any
    other command is translated per stored row, as before.

    Raises `UnsupportedValueError` before anything is sent: the value is
    decoded again for every member - `adapt_group_command` parses it fresh
    each time it is called - and an invalid value fails at the first member
    in `targets`. That still happens before any calls are dispatched,
    because this whole loop runs to completion before `dispatch_group` is
    ever awaited, so a value that was never valid still cannot leave a
    fan-out half done.
    """
    pair = (command.cluster_id, command.command_id)
    plans: list[MemberPlan] = []
    for target in targets:
        calls: list[DeviceCall] = []
        if pair in LIGHT_COMMAND_PAIRS:
            calls.extend(adapt_group_command(pair, target.commands, value))
        else:
            for stored in target.commands:
                calls.extend(to_device_calls(stored, value))
        plans.append(
            MemberPlan(
                device_id=target.device_id,
                device_label=target.device_label,
                calls=tuple(calls),
            )
        )
    return plans


Invoker = Callable[[DeviceCall], Awaitable[None]]
Runner = Callable[[Sequence[DeviceCall]], Awaitable[bool]]


async def _run_member(plan: MemberPlan, invoke: Invoker | None, run: Runner | None) -> None:
    """One member's calls, strictly in order.

    Sequential within the member and NOT gathered: the colour path sends
    colour first and brightness second to the same device, and that order
    is deliberate - `_EXECUTE_IF_OFF` in `commands/translate.py` records
    the measurement behind it (a switched-off KAJPLATS CWS turned white
    at full brightness instead of green when the colour command arrived
    too late). Flattening every member's calls into one gather would
    destroy exactly that.

    With `run` the whole plan goes to it as one request, which keeps that
    order too: `CommandGate.run` never splits a request. Its `False` - a
    newer value for this member replaced the plan before it started - is
    returned from here like a success, so the member counts as reached.
    """
    if run is not None:
        await run(plan.calls)
        return
    assert invoke is not None, "dispatch_group needs `invoke` when no `run` is given"
    for call in plan.calls:
        await invoke(call)


@dataclass(frozen=True)
class GroupOutcome:
    """Which members failed, and why - the distinction the single-device
    path has had since the boundary design and the group path had not
    (boundary design open point 12).

    A member whose technology has no running source was never ASKED; a
    member that did not answer was. Reporting both as "no answer from X"
    told a user whose Zigbee stick they had just removed from the
    configuration that six lamps were unreachable, which sent them looking
    at the lamps.
    """

    # PLAN ORDER, every failed member, exactly the list `dispatch_group`
    # returned before this change - same content, same order, same
    # disambiguation. Stored rather than derived from the two lists below,
    # and that is the whole point of the field: `unreachable + unconfigured`
    # would silently regroup the members by KIND, so a group whose second
    # and third members failed for different reasons would be reported in an
    # order that depends on the failure, not on the group. This module's
    # docstring promises the opposite - "the returned list is in plan order,
    # so the message a caller builds from it is reproducible" - and both
    # `api/control.py` and `loxone/server.py` build their 502 detail from
    # it.
    failed: list[str]
    # Subsets of `failed`, each itself in plan order, for the callers that
    # need to tell a 502 from a 503.
    unreachable: list[str]
    unconfigured: list[str]
    # The technology of each unconfigured member, same index, same plan
    # order as `unconfigured` - so a caller naming ONE technology in a 503
    # detail names the FIRST member's, not whichever coroutine happened to
    # raise first. Both routes used to observe this through a wrapper around
    # `invoke`, which recorded completion order and only coincided with plan
    # order because `Sources.send` raises before its first `await`; a single
    # `await` ahead of that raise would have flipped it silently.
    #
    # A list rather than one value: two technologies can be unconfigured at
    # once (a Zigbee stick removed while the Matter server is down), and a
    # caller that must show one name should be able to see that it is
    # choosing among several.
    unconfigured_technologies: list[str]


async def dispatch_group(
    plans: Sequence[MemberPlan], invoke: Invoker | None, *, run: Runner | None = None
) -> GroupOutcome:
    """Runs every member concurrently and returns which labels failed, and
    why.

    **`run` (design 2026-09-13, command coalescing).** Both group routes
    pass `CommandGate.run`, so each member's plan waits for whatever is
    still running on that device, and a newer value replaces one still
    waiting. A member whose plan was replaced that way returns normally: it
    is neither failed nor unconfigured, because the newer value is on its
    way to it. Without `run`, each member's calls go straight to `invoke`,
    one after another, and `invoke` may be `None` only when `run` is given.

    `return_exceptions=True` rather than letting the first failure
    propagate: a group of six with two dead lamps must switch the other
    four and then say which two did not answer. Reporting only the first
    exception would name one lamp when three are unreachable, which reads
    like a single device fault instead of a network fault.

    `GroupOutcome.failed` is in plan order, so the message a caller builds
    from it is reproducible.

    **Disambiguation (final review, Item 3).** Device labels are not
    unique - two lamps can both be named "Lamp" - so a failed member is
    reported as its bare label only while that label is unique among the
    OTHER failed members of this call; a label shared by more than one
    failed member gets its `device_id` appended (`"Lamp (12)"`), read
    from `MemberPlan.device_id`. Uniqueness is checked against the failed
    set, not the whole group, so the common case - distinct labels -
    reads exactly as before; only the ambiguous case gains anything,
    which is deliberately not a bare id list (`api/control.py` and
    `loxone/server.py` both build their 502 detail and log line from this
    return value, so fixing the ambiguity once here keeps the two in
    sync automatically - the same reason this module exists as one
    implementation for both routes, see the module docstring). The
    disambiguation is computed once over the whole failed set, so a label
    reads the same in `failed` as it does in whichever of `unreachable`/
    `unconfigured` it lands in.

    **`unreachable` vs `unconfigured` (boundary design open point 12).** A
    member classified by `isinstance(result, SourceNotConfiguredError)`:
    that one was never asked (its technology has no running source right
    now), everything else in `failed` was asked and did not answer. Both
    subsets preserve plan order, same as `failed` itself, and
    `unconfigured_technologies` is read off the very exceptions that did the
    classifying, so it lines up with `unconfigured` index for index.
    """
    results = await asyncio.gather(
        *(_run_member(plan, invoke, run) for plan in plans), return_exceptions=True
    )
    # BaseException, not Exception: cancelling the task that awaits this
    # function still propagates CancelledError out, because CPython's
    # _GatheringFuture re-raises it regardless of return_exceptions - so
    # nothing is silently swallowed at shutdown. What this widening buys
    # is a member whose own `invoke` raises CancelledError being reported
    # as a failed label instead of propagating - `Exception` alone would
    # silently count a cancelled member as a successful switch.
    failed_pairs = [
        (plan, result)
        for plan, result in zip(plans, results, strict=True)
        if isinstance(result, BaseException)
    ]
    label_counts = Counter(plan.device_label for plan, _ in failed_pairs)
    labels = [
        plan.device_label
        if label_counts[plan.device_label] == 1
        else f"{plan.device_label} ({plan.device_id})"
        for plan, _ in failed_pairs
    ]
    unconfigured = [
        label
        for (_, result), label in zip(failed_pairs, labels, strict=True)
        if isinstance(result, SourceNotConfiguredError)
    ]
    unreachable = [
        label
        for (_, result), label in zip(failed_pairs, labels, strict=True)
        if not isinstance(result, SourceNotConfiguredError)
    ]
    # The exception objects are right here, so the technology is read off
    # them in plan order - no caller has to watch exceptions fly past to
    # learn it.
    unconfigured_technologies = [
        result.technology
        for _, result in failed_pairs
        if isinstance(result, SourceNotConfiguredError)
    ]
    return GroupOutcome(
        failed=labels,
        unreachable=unreachable,
        unconfigured=unconfigured,
        unconfigured_technologies=unconfigured_technologies,
    )
