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

"""An On before a brightness that may find its lamp off - design 2026-09-30
(switch on before brightness)."""

from __future__ import annotations

import pytest

from loxmatter.commands.switch_on import SwitchOnFirst
from loxmatter.sources import DeviceCall


def call(
    cluster_id: int, command_id: int, level: int | None = None, endpoint: int = 1
) -> DeviceCall:
    payload: dict[str, object] = {} if level is None else {"level": level, "transitionTime": 0}
    return DeviceCall(
        technology="matter",
        address="26",
        endpoint=endpoint,
        cluster_id=cluster_id,
        command_id=command_id,
        payload=payload,
    )


def level(value: int, endpoint: int = 1) -> DeviceCall:
    return call(8, 4, value, endpoint)


ON, OFF, TOGGLE = call(6, 1), call(6, 0), call(6, 2)
COLOUR_TEMPERATURE = call(768, 10)


class Lamp:
    """Records what was sent, and what the lamp reported for OnOff."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, int]] = []
        self.reported: bool | None = None
        self.failing: set[tuple[int, int]] = set()
        self.accepts_on = True

    async def __call__(self, device_call: DeviceCall) -> None:
        pair = (device_call.cluster_id, device_call.command_id)
        if pair in self.failing:
            raise RuntimeError("no answer")
        self.sent.append(pair)


def switch_for(lamp: Lamp) -> SwitchOnFirst:
    return SwitchOnFirst(
        lamp, reported_on=lambda _call: lamp.reported, accepts_on=lambda _call: lamp.accepts_on
    )


async def test_a_brightness_for_a_lamp_nothing_is_known_about_is_switched_on_first():
    """The measured case: the bridge has just started, and the lamp may be
    off. Fault to prove it: never insert the On - the lamp comes on dim."""
    lamp = Lamp()
    await switch_for(lamp)(level(224))
    assert lamp.sent == [(6, 1), (8, 4)]


async def test_a_brightness_after_an_off_this_bridge_sent_is_switched_on_first():
    """The lamp's report of the "off" may not have arrived yet: it still
    reads on. The bridge knows it sent the off, and that is enough.

    Fault to prove it: decide from the report alone."""
    lamp = Lamp()
    lamp.reported = True
    switch = switch_for(lamp)
    await switch(level(224))
    await switch(level(0))
    lamp.sent.clear()
    await switch(level(224))
    assert lamp.sent == [(6, 1), (8, 4)]


@pytest.mark.parametrize("off", [OFF, level(0)])
async def test_either_kind_of_off_counts(off):
    lamp = Lamp()
    lamp.reported = True
    switch = switch_for(lamp)
    await switch(ON)
    await switch(off)
    lamp.sent.clear()
    await switch(level(100))
    assert lamp.sent == [(6, 1), (8, 4)]


async def test_a_lamp_the_bridge_switched_on_is_dimmed_without_an_extra_call():
    """A slider dragged along a lamp that is on costs nothing extra.

    Fault to prove it: always insert the On."""
    lamp = Lamp()
    lamp.reported = True
    switch = switch_for(lamp)
    await switch(level(100))
    lamp.sent.clear()
    await switch(COLOUR_TEMPERATURE)
    await switch(level(150))
    await switch(level(200))
    assert lamp.sent == [(768, 10), (8, 4), (8, 4)]


async def test_a_lamp_reported_off_from_elsewhere_is_switched_on_first():
    """Switched off by a wall switch or another app: only its report says so.

    Fault to prove it: decide from what the bridge sent alone."""
    lamp = Lamp()
    lamp.reported = True
    switch = switch_for(lamp)
    await switch(level(100))
    lamp.reported = False
    lamp.sent.clear()
    await switch(level(150))
    assert lamp.sent == [(6, 1), (8, 4)]


async def test_after_a_toggle_the_state_is_unknown():
    lamp = Lamp()
    lamp.reported = True
    switch = switch_for(lamp)
    await switch(level(100))
    await switch(TOGGLE)
    lamp.sent.clear()
    await switch(level(150))
    assert lamp.sent == [(6, 1), (8, 4)]


async def test_after_a_failed_call_the_state_is_unknown():
    """A call that failed may or may not have reached the lamp."""
    lamp = Lamp()
    lamp.reported = True
    switch = switch_for(lamp)
    await switch(level(100))
    lamp.failing.add((6, 0))
    with pytest.raises(RuntimeError):
        await switch(OFF)
    lamp.failing.clear()
    lamp.sent.clear()
    await switch(level(150))
    assert lamp.sent == [(6, 1), (8, 4)]


async def test_a_level_of_zero_is_never_preceded_by_an_on():
    lamp = Lamp()
    await switch_for(lamp)(level(0))
    assert lamp.sent == [(8, 4)]


async def test_a_plain_move_to_level_and_other_calls_pass_unchanged():
    """(8, 0) does not switch a lamp on, and is not meant to."""
    lamp = Lamp()
    switch = switch_for(lamp)
    for other in (call(8, 0, 100), COLOUR_TEMPERATURE, ON, OFF, TOGGLE):
        await switch(other)
    assert lamp.sent == [(8, 0), (768, 10), (6, 1), (6, 0), (6, 2)]


async def test_an_endpoint_without_on_gets_the_level_call_alone():
    lamp = Lamp()
    lamp.accepts_on = False
    await switch_for(lamp)(level(224))
    assert lamp.sent == [(8, 4)]


async def test_endpoints_are_kept_apart():
    lamp = Lamp()
    lamp.reported = True
    switch = switch_for(lamp)
    await switch(level(100, endpoint=1))
    lamp.sent.clear()
    await switch(level(100, endpoint=2))
    assert lamp.sent == [(6, 1), (8, 4)]


async def test_a_failing_on_stops_the_level_call():
    """The On is part of the request: if it fails, the request fails, as
    any of its calls would."""
    lamp = Lamp()
    lamp.failing.add((6, 1))
    with pytest.raises(RuntimeError):
        await switch_for(lamp)(level(224))
    assert lamp.sent == []
