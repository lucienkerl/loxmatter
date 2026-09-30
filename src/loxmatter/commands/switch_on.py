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

"""An explicit "on" before a brightness that has to switch a lamp on.

Design 2026-09-30 (switch on before brightness). Measured on the test Pi on
30 September 2026 with an IKEA KAJPLATS E27 WS (firmware 1.1.0), watched by
the maintainer: switched off, then sent MoveToLevelWithOnOff (8, 4) at 88 %,
the lamp came on dim - both with and without the colour temperature call
before it - while it reported OnOff true and CurrentLevel 88 % within
0.2 s. Sent On (6, 1) first and then the same (8, 4), it came on bright.
While the lamp is on, (8, 4) sets the brightness correctly.

So every (8, 4) above level 0 that may find its lamp off gets an On on the
same endpoint first. "May find it off" is decided from two things, because
either alone misses a case:

- what this bridge last sent the endpoint - it knows an "off" it sent at
  once, while the lamp's report of it can arrive seconds later;
- what the lamp last reported - the only way to learn of an "off" from
  somewhere else, a wall switch or another app.

Only when the bridge last switched the endpoint on AND the lamp has not
reported it off is the On left out. A lamp being dimmed along a slider is
on, so the slider's values cost no extra call; the first value after an
"off", or after a start of the bridge, does.

It wraps the invoker behind the command gate, which runs one request per
device at a time, so the calls it sees for an endpoint are in the order the
device receives them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from loxmatter.sources import DeviceCall

__all__ = ["SwitchOnFirst"]

Invoker = Callable[[DeviceCall], Awaitable[None]]
Endpoint = tuple[str, str, int]

_ON_OFF = 6
_OFF = 0
_ON = 1
_TOGGLE = 2
_LEVEL = 8
_LEVEL_WITH_ON_OFF = 4


def _endpoint(call: DeviceCall) -> Endpoint:
    return (call.technology, call.address, call.endpoint)


def _switches_on(call: DeviceCall) -> bool | None:
    """Whether `call` leaves its endpoint on (`True`), off (`False`), or
    says nothing about it (`None`)."""
    if call.cluster_id == _ON_OFF and call.command_id in (_ON, _OFF):
        return call.command_id == _ON
    if call.cluster_id == _LEVEL and call.command_id == _LEVEL_WITH_ON_OFF:
        return _level_of(call) > 0
    return None


def _level_of(call: DeviceCall) -> int:
    level = call.payload.get("level", 0)
    return level if isinstance(level, int) else 0


class SwitchOnFirst:
    """An invoker that sends On (6, 1) before a MoveToLevelWithOnOff above
    level 0 whose lamp may be off.

    `reported_on` answers what the lamp last reported for its endpoint's
    OnOff attribute, or `None` when nothing is known. `accepts_on` answers
    whether the endpoint takes On at all; one that does not gets the level
    call alone, as before."""

    def __init__(
        self,
        invoke: Invoker,
        *,
        reported_on: Callable[[DeviceCall], bool | None],
        accepts_on: Callable[[DeviceCall], bool],
    ) -> None:
        self._invoke = invoke
        self._reported_on = reported_on
        self._accepts_on = accepts_on
        # What this bridge last left each endpoint as, from the calls that
        # succeeded. An endpoint missing here is unknown.
        self._sent_on: dict[Endpoint, bool] = {}

    async def __call__(self, call: DeviceCall) -> None:
        if self._needs_on_first(call):
            on = DeviceCall(
                technology=call.technology,
                address=call.address,
                endpoint=call.endpoint,
                cluster_id=_ON_OFF,
                command_id=_ON,
                payload={},
            )
            await self._send(on)
        await self._send(call)

    def _needs_on_first(self, call: DeviceCall) -> bool:
        if not (call.cluster_id == _LEVEL and call.command_id == _LEVEL_WITH_ON_OFF):
            return False
        if _level_of(call) == 0:
            return False
        known_on = (
            self._sent_on.get(_endpoint(call)) is True and self._reported_on(call) is not False
        )
        return not known_on and self._accepts_on(call)

    async def _send(self, call: DeviceCall) -> None:
        endpoint = _endpoint(call)
        try:
            await self._invoke(call)
        except BaseException:
            # A call that failed may or may not have reached the lamp.
            self._sent_on.pop(endpoint, None)
            raise
        if call.cluster_id == _ON_OFF and call.command_id == _TOGGLE:
            self._sent_on.pop(endpoint, None)
            return
        state = _switches_on(call)
        if state is not None:
            self._sent_on[endpoint] = state
