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

import pytest

from loxmatter.commands.color import (
    kelvin_to_mireds,
    loxone_rgb_to_rgb,
    rgb_to_hue_saturation,
)


def test_mireds_are_the_reciprocal_of_kelvin():
    assert kelvin_to_mireds(2700) == 370
    assert kelvin_to_mireds(6500) == 153


def test_mireds_reject_zero_kelvin():
    with pytest.raises(ValueError, match="Kelvin"):
        kelvin_to_mireds(0)


@pytest.mark.parametrize(
    ("rgb", "hue", "saturation"),
    [
        ((255, 0, 0), 0, 254),
        ((0, 255, 0), 85, 254),
        ((0, 0, 255), 169, 254),
        ((255, 255, 255), 0, 0),
        ((0, 0, 0), 0, 0),
    ],
)
def test_primary_colours_map_to_known_hues(rgb, hue, saturation):
    """Reference values from the HSV definition, not from a device."""
    h, s = rgb_to_hue_saturation(*rgb)
    assert h == pytest.approx(hue, abs=1)
    assert s == pytest.approx(saturation, abs=1)


@pytest.mark.parametrize(
    ("packed", "rgb"),
    [
        # The example from the Loxone knowledge base, quoted in the module
        # docstring: 20040060 = 60% red, 40% green, 20% blue.
        (20040060, (153, 102, 51)),
        (0, (0, 0, 0)),
        (100100100, (255, 255, 255)),
        (100, (255, 0, 0)),
        (100000, (0, 255, 0)),
        (100000000, (0, 0, 255)),
    ],
)
def test_the_packed_loxone_number_splits_into_three_channels(packed, rgb):
    assert loxone_rgb_to_rgb(packed) == rgb


@pytest.mark.parametrize("packed", [-1, 101, 101000, 101000000, 999999999])
def test_a_channel_above_100_percent_is_rejected(packed):
    """A clear error is better than a made-up color on the real device -
    the same stance as `kelvin_to_mireds` at 0 Kelvin."""
    with pytest.raises(ValueError):
        loxone_rgb_to_rgb(packed)


def test_a_fractional_number_is_rejected():
    """The Loxone encoding is integer; 20040060.5 would be a sign
    that a completely different number is arriving here."""
    with pytest.raises(ValueError):
        loxone_rgb_to_rgb(20040060.5)


# --- Lumitech ---------------------------------------------------------------
# The measured values come from a real Loxone installation with
# Lumitech DMX output (operating 8 September 2026, bridge command log):
# 24 different white values, including two different brightness levels.
from loxmatter.commands.color import is_lumitech, lumitech_to_kelvin


@pytest.mark.parametrize(
    ("packed", "kelvin"),
    [
        (200283057, 3057),
        (200284407, 4407),
        (200285742, 5742),
        (201002700, 2700),
        (201003030, 3030),
        (201004324, 4324),
        (201006018, 6018),
        (201006500, 6500),
    ],
)
def test_lumitech_values_from_a_real_installation_decode_to_their_kelvin(packed, kelvin):
    assert is_lumitech(packed)
    assert lumitech_to_kelvin(packed) == kelvin


def test_the_two_encodings_cannot_overlap():
    """The largest possible RGB number is 100100100 (three full channels), the
    smallest possible Lumitech number 200000000. There is space between them,
    so the distinction is unambiguous and not heuristic."""
    assert not is_lumitech(100100100)
    assert is_lumitech(200000000)
    assert not is_lumitech(199999999)


@pytest.mark.parametrize("packed", [0, 1, 100, 100100100, 20100270, 2010027000])
def test_ordinary_and_malformed_numbers_are_not_lumitech(packed):
    """Neither the too-short nor the too-long number: the identifier alone
    does not make a Lumitech value; the digit count is part of it."""
    assert not is_lumitech(packed)


def test_a_lumitech_brightness_above_100_is_rejected():
    """20|101|2700 - a brightness that cannot exist. Better a
    clear error than a color temperature from a number that violates the format."""
    with pytest.raises(ValueError):
        lumitech_to_kelvin(201012700)


def test_a_lumitech_value_without_kelvin_is_rejected():
    """20|100|0000 - 0 Kelvin does not exist, and `kelvin_to_mireds`
    shares that knowledge later."""
    with pytest.raises(ValueError):
        lumitech_to_kelvin(201000000)


def test_lumitech_rejects_a_number_that_is_not_lumitech():
    """The caller must check first. An RGB value here would be a
    programming error, not an input error - and silently computing the wrong thing
    would be worse than aborting."""
    with pytest.raises(ValueError):
        lumitech_to_kelvin(100100100)


# --- Brightness from both encodings -----------------------------------------
from loxmatter.commands.color import lumitech_to_brightness, rgb_to_brightness


@pytest.mark.parametrize(
    ("rgb", "percent"),
    [
        ((255, 255, 255), 100),
        ((0, 0, 0), 0),
        ((255, 0, 0), 100),
        ((128, 0, 0), 50),
        ((51, 10, 46), 20),
    ],
)
def test_brightness_is_the_v_of_hsv(rgb, percent):
    """Loxone encodes brightness in the magnitude of the RGB number, not separately.
    Proven by two measured values from the same installation: 18004020 = (20,4,18)
    and 85019094 = (94,19,85) have the same hue (307.5 / 307.2 degrees)
    and the same saturation (80.0 / 79.8 %), but 20 % vs 94 %
    brightness - the same color, once dimmed."""
    assert rgb_to_brightness(*rgb) == pytest.approx(percent, abs=1)


def test_the_two_measured_values_differ_only_in_brightness():
    """The counterproof to the docstring above, using the actual numbers."""
    from loxmatter.commands.color import loxone_rgb_to_rgb, rgb_to_hue_saturation

    dark = loxone_rgb_to_rgb(18004020)
    bright = loxone_rgb_to_rgb(85019094)
    assert rgb_to_hue_saturation(*dark)[0] == pytest.approx(
        rgb_to_hue_saturation(*bright)[0], abs=2
    )
    assert rgb_to_brightness(*dark) == pytest.approx(20, abs=1)
    assert rgb_to_brightness(*bright) == pytest.approx(94, abs=1)


@pytest.mark.parametrize(
    ("packed", "prozent"),
    [(200283057, 28), (201002700, 100), (200000001, 0)],
)
def test_lumitech_brightness_is_the_middle_field(packed, prozent):
    assert lumitech_to_brightness(packed) == prozent


def test_lumitech_brightness_rejects_a_number_that_is_not_lumitech():
    with pytest.raises(ValueError):
        lumitech_to_brightness(100100100)
