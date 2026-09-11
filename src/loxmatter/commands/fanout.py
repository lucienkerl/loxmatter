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

from loxmatter.commands.translate import to_device_calls
from loxmatter.model.store import GroupTarget
from loxmatter.sources import DeviceCall

__all__ = ["MemberPlan", "dispatch_group", "plan_group_calls"]


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


def plan_group_calls(targets: Sequence[GroupTarget], value: str) -> list[MemberPlan]:
    """Translates the group's value once per member command.

    Raises `UnsupportedValueError` before anything is sent - all members
    share the same (cluster, command) pair, so the translation either
    works for all of them or for none, and finding out halfway through a
    fan-out would leave a partial state for a value that was never valid.
    """
    plans: list[MemberPlan] = []
    for target in targets:
        calls: list[DeviceCall] = []
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


async def _run_member(plan: MemberPlan, invoke: Callable[[DeviceCall], Awaitable[None]]) -> None:
    """One member's calls, strictly in order.

    Sequential within the member and NOT gathered: the colour path sends
    colour first and brightness second to the same device, and that order
    is deliberate - `_EXECUTE_IF_OFF` in `commands/translate.py` records
    the measurement behind it (a switched-off KAJPLATS CWS turned white
    at full brightness instead of green when the colour command arrived
    too late). Flattening every member's calls into one gather would
    destroy exactly that.
    """
    for call in plan.calls:
        await invoke(call)


async def dispatch_group(
    plans: Sequence[MemberPlan], invoke: Callable[[DeviceCall], Awaitable[None]]
) -> list[str]:
    """Runs every member concurrently and returns the labels that failed.

    `return_exceptions=True` rather than letting the first failure
    propagate: a group of six with two dead lamps must switch the other
    four and then say which two did not answer. Reporting only the first
    exception would name one lamp when three are unreachable, which reads
    like a single device fault instead of a network fault.

    The returned list is in plan order, so the message a caller builds
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
    implementation for both routes, see the module docstring).
    """
    results = await asyncio.gather(
        *(_run_member(plan, invoke) for plan in plans), return_exceptions=True
    )
    # BaseException, not Exception: cancelling the task that awaits this
    # function still propagates CancelledError out, because CPython's
    # _GatheringFuture re-raises it regardless of return_exceptions - so
    # nothing is silently swallowed at shutdown. What this widening buys
    # is a member whose own `invoke` raises CancelledError being reported
    # as a failed label instead of propagating - `Exception` alone would
    # silently count a cancelled member as a successful switch.
    failed = [
        plan
        for plan, result in zip(plans, results, strict=True)
        if isinstance(result, BaseException)
    ]
    label_counts = Counter(plan.device_label for plan in failed)
    return [
        plan.device_label
        if label_counts[plan.device_label] == 1
        else f"{plan.device_label} ({plan.device_id})"
        for plan in failed
    ]
