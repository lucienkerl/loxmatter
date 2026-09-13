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

"""What one member of a light group receives for one group command.

Design 2026-09-13, section 3.2. The group's value is decoded once; each
member gets the calls for the parts of it the member can carry - colour for
colour lamps, white temperature for tunable-white lamps, brightness for every
dimmable lamp, and on/off for the rest. A member that can carry none of it
gets no calls, which is not a failure.

Pure: no store, no HTTP, no sources. `commands/fanout.py` calls it per
member, and the device path (`translate.to_device_calls`) is untouched - the
shared parts are `decode_loxone_colour` and the payload helpers, so both
paths build byte-identical payloads for the same case.
"""

from __future__ import annotations

from collections.abc import Sequence

from loxmatter.commands.color import (
    kelvin_to_cie_xy,
    kelvin_to_hue_saturation,
    rgb_to_cie_xy,
    rgb_to_hue_saturation,
)
from loxmatter.commands.translate import (
    LoxoneColour,
    colour_temperature_payload,
    decode_loxone_colour,
    hue_saturation_payload,
    level_from_percent,
    parse_kelvin,
    parse_number,
    to_device_calls,
    xy_payload,
)
from loxmatter.model.store import StoredCommand
from loxmatter.profiles.light_commands import (
    COLOUR_HS,
    COLOUR_TEMPERATURE,
    COLOUR_XY,
    LEVEL,
    LEVEL_ONOFF,
    OFF,
    ON,
)
from loxmatter.sources import DeviceCall

__all__ = ["adapt_group_command"]

Pair = tuple[int, int]


def _call(sample: StoredCommand, pair: Pair, payload: dict[str, object]) -> DeviceCall:
    return DeviceCall(
        technology=sample.technology,
        address=sample.address,
        endpoint=sample.endpoint,
        cluster_id=pair[0],
        command_id=pair[1],
        payload=payload,
    )


def _brightness(
    here: dict[Pair, StoredCommand], sample: StoredCommand, percent: float
) -> list[DeviceCall]:
    """Brightness the way this endpoint can take it: (8, 4) switches off at
    0 and on above it, so it is preferred; `on`/`off` for a light that
    cannot dim.

    (8, 0) only when it is all there is, and then with the switching (8, 4)
    would have done: MoveToLevel(0) leaves a lamp on, and MoveToLevel above
    0 does not bring a lamp that is off back on (see `to_device_calls`). So
    level 0 is `off` where the member carries it, and a level above 0 is
    `on` first where the member carries it."""
    level = level_from_percent(percent)
    if LEVEL_ONOFF in here:
        return [_call(sample, LEVEL_ONOFF, {"level": level, "transitionTime": 0})]
    if LEVEL in here:
        if level == 0:
            if OFF in here:
                return [_call(sample, OFF, {})]
            return [_call(sample, LEVEL, {"level": 0, "transitionTime": 0})]
        switch_on = [_call(sample, ON, {})] if ON in here else []
        return switch_on + [_call(sample, LEVEL, {"level": level, "transitionTime": 0})]
    if ON in here and OFF in here:
        return [_call(sample, ON if level > 0 else OFF, {})]
    return []


def _colour_order(named: Pair) -> tuple[Pair, Pair]:
    """The group command's own colour command first, the other one second."""
    return (named, COLOUR_XY if named == COLOUR_HS else COLOUR_HS)


def _colour_point(
    here: dict[Pair, StoredCommand], sample: StoredCommand, kelvin: float
) -> list[DeviceCall]:
    """A white temperature: the temperature command if carried, else the
    white reproduced as a colour point (design 3.3) - XY when the member
    carries it, HS only when it does not, whichever colour command the group
    command names. The white is the same white on `color`, `color_xy` and
    `colortemp`."""
    if COLOUR_TEMPERATURE in here:
        return [_call(sample, COLOUR_TEMPERATURE, colour_temperature_payload(kelvin))]
    for pair in (COLOUR_XY, COLOUR_HS):
        if pair in here and pair == COLOUR_XY:
            return [_call(sample, COLOUR_XY, xy_payload(*kelvin_to_cie_xy(kelvin)))]
        if pair in here and pair == COLOUR_HS:
            return [
                _call(sample, COLOUR_HS, hue_saturation_payload(*kelvin_to_hue_saturation(kelvin)))
            ]
    return []


def _colour(
    here: dict[Pair, StoredCommand], sample: StoredCommand, named: Pair, colour: LoxoneColour
) -> list[DeviceCall]:
    if colour.kelvin is not None:
        return _colour_point(here, sample, colour.kelvin)
    assert colour.rgb is not None
    for pair in _colour_order(named):
        if pair in here and pair == COLOUR_XY:
            return [_call(sample, COLOUR_XY, xy_payload(*rgb_to_cie_xy(*colour.rgb)))]
        if pair in here and pair == COLOUR_HS:
            return [
                _call(
                    sample, COLOUR_HS, hue_saturation_payload(*rgb_to_hue_saturation(*colour.rgb))
                )
            ]
    return []


def _endpoint_calls(
    pair: Pair, here: dict[Pair, StoredCommand], value: str, decoded: LoxoneColour | None
) -> list[DeviceCall]:
    sample = next(iter(here.values()))
    if decoded is not None:
        if level_from_percent(decoded.brightness_percent) == 0:
            return _brightness(here, sample, 0)
        return _colour(here, sample, pair, decoded) + _brightness(
            here, sample, decoded.brightness_percent
        )
    if pair == COLOUR_TEMPERATURE:
        if COLOUR_TEMPERATURE in here:
            return to_device_calls(here[COLOUR_TEMPERATURE], value)
        return _colour_point(here, sample, parse_number(value))
    if pair in here:
        return to_device_calls(here[pair], value)
    if pair in (LEVEL, LEVEL_ONOFF):
        return _brightness(here, sample, parse_number(value))
    return []


def adapt_group_command(pair: Pair, rows: Sequence[StoredCommand], value: str) -> list[DeviceCall]:
    """The calls one member receives for the light group command `pair`.

    `rows` are the member's stored LIGHT commands on every endpoint
    (`Store.group_targets`). Endpoints are handled in ascending order and
    each gets its own calls, colour before brightness - the order
    `to_device_calls` documents. Raises `UnsupportedValueError` for a value
    that cannot mean anything, before any call is built."""
    decoded = decode_loxone_colour(value) if pair in (COLOUR_HS, COLOUR_XY) else None
    if pair == COLOUR_TEMPERATURE:
        parse_kelvin(value)
    if pair in (LEVEL, LEVEL_ONOFF):
        parse_number(value)
    calls: list[DeviceCall] = []
    for endpoint in sorted({row.endpoint for row in rows}):
        here = {(row.cluster_id, row.command_id): row for row in rows if row.endpoint == endpoint}
        calls.extend(_endpoint_calls(pair, here, value, decoded))
    return calls
