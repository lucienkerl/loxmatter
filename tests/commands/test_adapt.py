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

"""Per-member adaptation of a light group command - design 2026-09-13, 3.2."""

from __future__ import annotations

import pytest

from loxmatter import i18n
from loxmatter.commands.adapt import adapt_group_command
from loxmatter.commands.color import kelvin_to_cie_xy, kelvin_to_hue_saturation, rgb_to_cie_xy
from loxmatter.commands.translate import UnsupportedValueError, level_from_percent
from loxmatter.model.store import StoredCommand
from loxmatter.profiles.light_commands import (
    COLOUR_HS,
    COLOUR_TEMPERATURE,
    COLOUR_XY,
    LEVEL,
    LEVEL_ONOFF,
    OFF,
    ON,
    TOGGLE,
)

_SLUGS = {
    OFF: "off",
    ON: "on",
    TOGGLE: "toggle",
    LEVEL: "level",
    LEVEL_ONOFF: "level_onoff",
    COLOUR_HS: "color",
    COLOUR_XY: "color_xy",
    COLOUR_TEMPERATURE: "colortemp",
}


def rows(address: str, *pairs: tuple[int, int], endpoint: int = 1) -> list[StoredCommand]:
    return [
        StoredCommand(
            key=f"d9_{endpoint}_{_SLUGS[pair]}",
            slug=_SLUGS[pair],
            technology="matter",
            address=address,
            endpoint=endpoint,
            cluster_id=pair[0],
            command_id=pair[1],
            takes_value=pair not in (OFF, ON, TOGGLE),
            device_id=9,
        )
        for pair in pairs
    ]


# The stored light rows of real devices, as the store holds them.
# KAJPLATS E14 CWS and E27 WS: tests/fixtures/nodes/ikea_kajplats_{cws,ws}_lamp.json.
CWS = rows("21", OFF, ON, TOGGLE, LEVEL, LEVEL_ONOFF, COLOUR_HS, COLOUR_XY, COLOUR_TEMPERATURE)
WS = rows("14", OFF, ON, TOGGLE, LEVEL, LEVEL_ONOFF, COLOUR_TEMPERATURE)
# TRADFRI bulb E27 WW (Zigbee), read from the maintainer's Pi on 13 September
# 2026 - captured, there is no fixture for it.
WW = rows("14:b4:57:ff:fe:7f:f5:da", OFF, ON, TOGGLE, LEVEL, LEVEL_ONOFF)
# No on/off-only light exists in the setup: this pair set is constructed.
ONOFF = rows("30", OFF, ON, TOGGLE)
# A colour lamp without colour temperature, XY only and HS only: constructed.
XY_ONLY = rows("31", OFF, ON, LEVEL_ONOFF, COLOUR_XY)
HS_ONLY = rows("32", OFF, ON, LEVEL_ONOFF, COLOUR_HS)

BLUE_60 = "60000000"  # Loxone BBBGGGRRR: blue 60 %, green 0, red 0
WHITE_2700_30 = "200302700"  # Lumitech: marker 20, brightness 030, 2700 K
EIF = {"optionsMask": 1, "optionsOverride": 1}


def shape(calls):
    return [(c.cluster_id, c.command_id, c.payload) for c in calls]


def test_colour_on_the_colour_lamp_sends_the_named_colour_command_then_brightness():
    got = shape(adapt_group_command(COLOUR_HS, CWS, BLUE_60))
    assert [(cluster, command) for cluster, command, _ in got] == [(768, 6), (8, 4)]
    assert got[1][2] == {"level": 152, "transitionTime": 0}


def test_color_xy_on_the_colour_lamp_sends_xy():
    got = shape(adapt_group_command(COLOUR_XY, CWS, BLUE_60))
    x, y = rgb_to_cie_xy(0, 0, 153)
    assert got[0] == (768, 7, {"colorX": x, "colorY": y, "transitionTime": 0, **EIF})


def test_colour_falls_back_to_the_other_colour_command_when_the_named_one_is_missing():
    assert [c.command_id for c in adapt_group_command(COLOUR_HS, XY_ONLY, BLUE_60)] == [7, 4]
    assert [c.command_id for c in adapt_group_command(COLOUR_XY, HS_ONLY, BLUE_60)] == [6, 4]


@pytest.mark.parametrize("member", [WS, WW], ids=["tunable white", "dim only"])
def test_colour_on_a_lamp_without_colour_sets_brightness_only(member):
    """Decision 2: a colour never changes a white lamp's temperature.

    Fault to prove it: send the colour temperature derived from nothing -
    or drop the brightness fallback - and this fails."""
    assert shape(adapt_group_command(COLOUR_HS, member, BLUE_60)) == [
        (8, 4, {"level": 152, "transitionTime": 0})
    ]


def test_colour_on_an_on_off_light_switches_it_on():
    assert shape(adapt_group_command(COLOUR_HS, ONOFF, BLUE_60)) == [(6, 1, {})]


_OFF_BY_LEVEL = [(8, 4, {"level": 0, "transitionTime": 0})]


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        (CWS, _OFF_BY_LEVEL),
        (WS, _OFF_BY_LEVEL),
        (WW, _OFF_BY_LEVEL),
        (XY_ONLY, _OFF_BY_LEVEL),
        (ONOFF, [(6, 0, {})]),
    ],
    ids=["CWS", "WS", "WW", "XY_ONLY", "ONOFF"],
)
def test_brightness_zero_is_a_single_off_and_no_colour(member, expected):
    assert shape(adapt_group_command(COLOUR_HS, member, "0")) == expected


def test_white_on_a_lamp_with_colour_temperature_sends_the_temperature():
    got = shape(adapt_group_command(COLOUR_HS, WS, WHITE_2700_30))
    assert got == [
        (768, 10, {"colorTemperatureMireds": 370, **EIF}),
        (8, 4, {"level": 76, "transitionTime": 0}),
    ]
    assert shape(adapt_group_command(COLOUR_HS, CWS, WHITE_2700_30))[0][:2] == (768, 10)


def test_white_on_a_colour_lamp_without_temperature_is_reproduced_as_a_colour_point():
    """Decision 3.

    Fault to prove it: return no colour call when the member has no
    colour-temperature command - both assertions fail."""
    x, y = kelvin_to_cie_xy(2700)
    assert shape(adapt_group_command(COLOUR_XY, XY_ONLY, WHITE_2700_30))[0] == (
        768,
        7,
        {"colorX": x, "colorY": y, "transitionTime": 0, **EIF},
    )
    hue, saturation = kelvin_to_hue_saturation(2700)
    assert shape(adapt_group_command(COLOUR_HS, HS_ONLY, WHITE_2700_30))[0] == (
        768,
        6,
        {"hue": hue, "saturation": saturation, "transitionTime": 0, **EIF},
    )


def test_white_on_a_dim_only_lamp_sets_brightness_only():
    assert shape(adapt_group_command(COLOUR_HS, WW, WHITE_2700_30)) == [
        (8, 4, {"level": 76, "transitionTime": 0})
    ]


def test_level_on_an_on_off_light_switches_it():
    assert shape(adapt_group_command(LEVEL_ONOFF, ONOFF, "40")) == [(6, 1, {})]
    assert shape(adapt_group_command(LEVEL_ONOFF, ONOFF, "0")) == [(6, 0, {})]


def test_level_on_a_dimmable_member_sends_the_same_command_it_names():
    assert shape(adapt_group_command(LEVEL, WW, "40")) == [
        (8, 0, {"level": 102, "transitionTime": 0})
    ]


def test_colortemp_follows_the_member():
    assert shape(adapt_group_command(COLOUR_TEMPERATURE, WS, "2700")) == [
        (768, 10, {"colorTemperatureMireds": 370, **EIF})
    ]
    assert shape(adapt_group_command(COLOUR_TEMPERATURE, XY_ONLY, "2700"))[0][:2] == (768, 7)


@pytest.mark.parametrize("member", [WW, ONOFF])
def test_colortemp_on_a_member_without_colour_is_nothing_not_an_error(member):
    assert adapt_group_command(COLOUR_TEMPERATURE, member, "2700") == []


def test_on_off_and_toggle_pass_through():
    assert shape(adapt_group_command(TOGGLE, WW, "1")) == [(6, 2, {})]


def test_a_member_with_the_light_on_two_endpoints_gets_calls_on_both_in_order():
    two = rows("40", ON, OFF, LEVEL_ONOFF, endpoint=1) + rows(
        "40", ON, OFF, LEVEL_ONOFF, endpoint=2
    )
    calls = adapt_group_command(COLOUR_HS, two, BLUE_60)
    assert [(c.endpoint, c.command_id) for c in calls] == [(1, 4), (2, 4)]


def test_an_invalid_colour_value_raises_before_any_call_is_built():
    with pytest.raises(UnsupportedValueError):
        adapt_group_command(COLOUR_HS, WW, "banana")


@pytest.mark.parametrize("value", ["0", "-5"])
@pytest.mark.parametrize(
    "member",
    [CWS, WS, WW, ONOFF, XY_ONLY, HS_ONLY],
    ids=["CWS", "WS", "WW dim only", "ONOFF", "XY_ONLY", "HS_ONLY"],
)
def test_a_colour_temperature_of_zero_or_below_raises_for_every_member(member, value):
    """0 K does not exist. Before, a tunable-white member raised a plain
    `ValueError` midway while a colour-only member clamped and sent - which
    one happened depended on the group's members."""
    with pytest.raises(UnsupportedValueError):
        adapt_group_command(COLOUR_TEMPERATURE, member, value)


# A colour lamp carrying both colour commands but no colour temperature:
# constructed.
BOTH_COLOURS = rows("33", OFF, ON, LEVEL_ONOFF, COLOUR_HS, COLOUR_XY)


@pytest.mark.parametrize(
    ("pair", "value"),
    [(COLOUR_HS, WHITE_2700_30), (COLOUR_XY, WHITE_2700_30), (COLOUR_TEMPERATURE, "2700")],
    ids=["color", "color_xy", "colortemp"],
)
def test_a_white_as_a_colour_point_is_xy_whichever_colour_command_the_group_names(pair, value):
    """Design 3.3: XY lamps get XY, HS-only lamps get HS. Before, a member
    with both got HS for a Lumitech white on `color` but XY for the same
    white on `colortemp`."""
    got = shape(adapt_group_command(pair, BOTH_COLOURS, value))
    assert got[0][:2] == (768, 7)


# A dimmable light with MoveToLevel but not MoveToLevelWithOnOff, with and
# without on/off: constructed.
LEVEL_ONLY = rows("34", OFF, ON, LEVEL)
BARE_LEVEL = rows("35", LEVEL)


def test_brightness_zero_on_a_member_without_level_onoff_switches_it_off():
    """MoveToLevel(0) leaves a lamp on, so `off` goes where the member has it."""
    assert shape(adapt_group_command(COLOUR_HS, LEVEL_ONLY, "0")) == [(6, 0, {})]
    assert shape(adapt_group_command(LEVEL_ONOFF, LEVEL_ONLY, "0")) == [(6, 0, {})]
    assert shape(adapt_group_command(COLOUR_HS, BARE_LEVEL, "0")) == [
        (8, 0, {"level": 0, "transitionTime": 0})
    ]


def test_brightness_above_zero_on_a_member_without_level_onoff_switches_it_on_first():
    """MoveToLevel does not bring a lamp that is off back on, so `on` goes
    first where the member has it."""
    assert shape(adapt_group_command(COLOUR_HS, LEVEL_ONLY, BLUE_60)) == [
        (6, 1, {}),
        (8, 0, {"level": 152, "transitionTime": 0}),
    ]
    assert shape(adapt_group_command(LEVEL_ONOFF, LEVEL_ONLY, "40")) == [
        (6, 1, {}),
        (8, 0, {"level": 102, "transitionTime": 0}),
    ]
    assert shape(adapt_group_command(COLOUR_HS, BARE_LEVEL, BLUE_60)) == [
        (8, 0, {"level": 152, "transitionTime": 0})
    ]


@pytest.mark.parametrize(
    ("pair", "member"),
    [(COLOUR_TEMPERATURE, WW), (LEVEL, ONOFF)],
    ids=["colortemp on dim only", "level on on/off"],
)
def test_a_value_that_is_not_a_number_raises_even_where_the_member_takes_nothing(pair, member):
    """The value is validated once, up front: a member that would receive no
    call for it does not turn an invalid value into a quiet success."""
    with pytest.raises(UnsupportedValueError):
        adapt_group_command(pair, member, "banana")


def test_toggle_on_a_member_without_toggle_is_nothing():
    assert adapt_group_command(TOGGLE, XY_ONLY, "1") == []


def test_the_on_off_fallback_needs_both_on_and_off():
    only_on = rows("36", ON)
    assert adapt_group_command(LEVEL_ONOFF, only_on, "40") == []
    assert adapt_group_command(COLOUR_HS, only_on, BLUE_60) == []


def test_on_or_off_is_decided_by_the_rounded_level_not_the_percent():
    """0.1 % rounds to level 0, and level 0 is off."""
    assert level_from_percent(0.1) == 0
    assert shape(adapt_group_command(LEVEL_ONOFF, ONOFF, "0.1")) == [(6, 0, {})]


def test_a_fractional_value_in_the_lumitech_range_is_not_a_lumitech_white():
    """Pinned from the unmodified code on 13 September 2026: 200302700.5 is
    rejected as a colour number that is not an integer, not decoded as
    2700 K at 30 %."""
    with pytest.raises(UnsupportedValueError) as info:
        adapt_group_command(COLOUR_HS, CWS, "200302700.5")
    assert str(info.value) == i18n.t("api.errors.loxone_colour_not_integer", value=200302700.5)


def test_the_warm_end_of_the_locus_clips_negative_srgb_channels():
    """1667 K lies outside the sRGB gamut, so its blue channel comes out
    negative before clipping. Pinned from the unmodified code on
    13 September 2026: (19, 254), a fully saturated orange-red."""
    hue, saturation = kelvin_to_hue_saturation(1667)
    assert saturation == 254
    assert 0 <= hue <= 30
    assert (hue, saturation) == (19, 254)
