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
member; the device path (`translate.to_device_calls`) is untouched for every
command except `lumitech`, which is no Matter command - `adapt_device_command`
below routes it back through this module's own group adapter, over the
single light's own rows, so both paths build byte-identical payloads for the
same case.
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
    level_payload,
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
    LIGHT_COMMAND_PAIRS,
    LUMITECH,
    OFF,
    ON,
)
from loxmatter.sources import DeviceCall

__all__ = ["adapt_device_command", "adapt_group_command"]

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
    here: dict[Pair, StoredCommand], sample: StoredCommand, percent: float, member_dims: bool
) -> list[DeviceCall]:
    """Brightness the way this endpoint can take it: (8, 4) switches off at
    0 and on above it, so it is preferred; `on`/`off` for a light that
    cannot dim - but only when no endpoint of the member dims
    (`member_dims`). A dimmable light with an on/off relay beside it takes a
    brightness on the light; switching the relay with it would change a
    channel the group command never named.

    (8, 0) only when it is all there is, and then with the switching (8, 4)
    would have done: MoveToLevel(0) leaves a lamp on, and MoveToLevel above
    0 does not bring a lamp that is off back on (see `to_device_calls`). So
    level 0 is `off` where the member carries it, and a level above 0 is
    `on` first where the member carries it."""
    level = level_from_percent(percent)
    if LEVEL_ONOFF in here:
        return [_call(sample, LEVEL_ONOFF, level_payload(level))]
    if LEVEL in here:
        if level == 0:
            if OFF in here:
                return [_call(sample, OFF, {})]
            return [_call(sample, LEVEL, level_payload(0))]
        switch_on = [_call(sample, ON, {})] if ON in here else []
        return switch_on + [_call(sample, LEVEL, level_payload(level))]
    if ON in here and OFF in here and not member_dims:
        return [_call(sample, ON if level > 0 else OFF, {})]
    return []


def _colour_point(
    here: dict[Pair, StoredCommand], sample: StoredCommand, kelvin: float
) -> list[DeviceCall]:
    """A white temperature reproduced as a colour point (design 3.3), for a
    member without the temperature command: XY when the member carries it,
    HS only when it does not, whichever colour command the group command
    names - the white is the same white on `color`, `color_xy` and
    `colortemp`."""
    if COLOUR_XY in here:
        return [_call(sample, COLOUR_XY, xy_payload(*kelvin_to_cie_xy(kelvin)))]
    if COLOUR_HS in here:
        return [_call(sample, COLOUR_HS, hue_saturation_payload(*kelvin_to_hue_saturation(kelvin)))]
    return []


def _colour(
    here: dict[Pair, StoredCommand], sample: StoredCommand, named: Pair, colour: LoxoneColour
) -> list[DeviceCall]:
    """The colour part of a decoded colour-output value. A white goes as the
    temperature command where the member carries it, else as a colour point.
    A colour goes as the colour command the group command names where the
    member carries it, else as the other one."""
    if colour.kelvin is not None:
        if COLOUR_TEMPERATURE in here:
            return [_call(sample, COLOUR_TEMPERATURE, colour_temperature_payload(colour.kelvin))]
        return _colour_point(here, sample, colour.kelvin)
    assert colour.rgb is not None
    # XY when the command names it, and for `lumitech`, which names neither
    # colour command: XY is mandatory for a Matter Extended Color Light and
    # the only colour ZHA sends (design 2026-09-24, 3.1).
    if COLOUR_XY in here and (named in (COLOUR_XY, LUMITECH) or COLOUR_HS not in here):
        return [_call(sample, COLOUR_XY, xy_payload(*rgb_to_cie_xy(*colour.rgb)))]
    if COLOUR_HS in here:
        return [
            _call(sample, COLOUR_HS, hue_saturation_payload(*rgb_to_hue_saturation(*colour.rgb)))
        ]
    return []


def _endpoint_calls(
    pair: Pair,
    here: dict[Pair, StoredCommand],
    value: str,
    decoded: LoxoneColour | None,
    number: float | None,
    member_dims: bool,
) -> list[DeviceCall]:
    sample = next(iter(here.values()))
    if decoded is not None:
        if level_from_percent(decoded.brightness_percent) == 0:
            return _brightness(here, sample, 0, member_dims)
        return _colour(here, sample, pair, decoded) + _brightness(
            here, sample, decoded.brightness_percent, member_dims
        )
    if pair in here:
        return to_device_calls(here[pair], value)
    if pair == COLOUR_TEMPERATURE:
        assert number is not None
        return _colour_point(here, sample, number)
    if pair in (LEVEL, LEVEL_ONOFF):
        assert number is not None
        return _brightness(here, sample, number, member_dims)
    return []


def adapt_group_command(pair: Pair, rows: Sequence[StoredCommand], value: str) -> list[DeviceCall]:
    """The calls one member receives for the light group command `pair`.

    `rows` are the member's stored LIGHT commands on every endpoint
    (`Store.group_targets`). Endpoints are handled in ascending order and
    each gets its own calls, colour before brightness - the order
    `to_device_calls` documents. An endpoint without a level command takes
    a brightness as on/off only when no endpoint of the member has one;
    explicit `on`/`off`/`toggle` reach every endpoint that carries them.

    Raises `UnsupportedValueError` for a value that cannot mean anything,
    before any call is built: the value is parsed here, once, so a member
    that would take nothing from it still rejects it."""
    decoded = decode_loxone_colour(value) if pair in (COLOUR_HS, COLOUR_XY, LUMITECH) else None
    number: float | None = None
    if pair == COLOUR_TEMPERATURE:
        number = parse_kelvin(value)
    elif pair in (LEVEL, LEVEL_ONOFF):
        number = parse_number(value)
    member_dims = any((row.cluster_id, row.command_id) in (LEVEL, LEVEL_ONOFF) for row in rows)
    calls: list[DeviceCall] = []
    for endpoint in sorted({row.endpoint for row in rows}):
        here = {(row.cluster_id, row.command_id): row for row in rows if row.endpoint == endpoint}
        calls.extend(_endpoint_calls(pair, here, value, decoded, number, member_dims))
    return calls


def adapt_device_command(
    command: StoredCommand, device_rows: Sequence[StoredCommand], value: str
) -> list[DeviceCall]:
    """The calls one stored device command produces (design 2026-09-24, 4.1).

    `lumitech` is no Matter command: it goes through `adapt_group_command`
    over the light rows of its own endpoint, the same rules a group member
    follows, so the two paths build byte-identical calls. Every other
    command is `to_device_calls` unchanged. Here rather than in
    `translate.py`, because this module imports that one."""
    if (command.cluster_id, command.command_id) != LUMITECH:
        return to_device_calls(command, value)
    rows = [
        row
        for row in device_rows
        if row.endpoint == command.endpoint
        and (row.cluster_id, row.command_id) in LIGHT_COMMAND_PAIRS
    ]
    return adapt_group_command(LUMITECH, rows, value)
