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
    rgb_to_cie_xy,
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


def test_rgb_to_cie_xy_against_the_srgb_primaries():
    """The published chromaticities of the sRGB primaries and of the D65
    white point (IEC 61966-2-1). Checked as the ZCL encoding, which is what
    goes on the wire: x = CurrentX / 65536, range 0x0000-0xFEFF.

    Fault to prove it: swap the returned x and y. Red then reports the
    chromaticity of a colour it is not, and the lamp shows it.
    """
    assert rgb_to_cie_xy(255, 0, 0) == (41943, 21627)  # x 0.6400, y 0.3300
    assert rgb_to_cie_xy(0, 255, 0) == (19661, 39322)  # x 0.3000, y 0.6000
    assert rgb_to_cie_xy(0, 0, 255) == (9830, 3932)  # x 0.1500, y 0.0600


def test_rgb_to_cie_xy_puts_white_on_d65():
    """White must land on the illuminant the sRGB standard defines, not
    somewhere near it - a white that drifts is the most visible error this
    conversion can make.

    This test cannot, on its own, tell a gamma-corrected conversion apart
    from one that skips gamma expansion entirely: at r=g=b, every channel
    gets the same treatment regardless, so the ratios - and therefore the
    chromaticity - come out identical either way. See
    `test_rgb_to_cie_xy_applies_the_srgb_gamma_curve_not_a_shortcut` below
    for the fault this test cannot catch."""
    x, y = rgb_to_cie_xy(255, 255, 255)
    assert abs(x / 65536 - 0.3127) < 0.001
    assert abs(y / 65536 - 0.3290) < 0.001


def test_rgb_to_cie_xy_applies_the_srgb_gamma_curve_not_a_shortcut():
    """The primaries (above) and the white point (above) all sit at the 0/1
    extremes of the gamma curve, where `_expand_gamma` and a bare
    `channel / 255` agree exactly - both map 0 to 0 and 255 to 1. Neither
    of those tests can therefore tell a correct, gamma-expanding conversion
    apart from one that skips the curve. Only a genuinely mixed colour,
    partway between two primaries, exercises it - which is what this test
    checks and the others, despite an earlier draft's claim, do not.

    Expected value for orange (255, 128, 0), computed independently from
    the sRGB standard's own EOTF and RGB-to-XYZ matrix (IEC 61966-2-1), the
    same way the primaries above were computed.

    Fault to prove it: skip the gamma expansion (use the raw 0-1 channel
    values), as in the other two tests' docstrings - this is the one test
    of the three that actually fails when that happens.
    """
    assert rgb_to_cie_xy(255, 128, 0) == (35585, 26676)  # x 0.5431, y 0.4071


def test_rgb_to_cie_xy_never_exceeds_the_zcl_maximum():
    """CurrentX/CurrentY are capped at 0xFEFF by the ZCL, not at 0xFFFF. A
    value above it is out of range on the wire.

    This holds trivially for every real colour: the sRGB gamut's most
    extreme chromaticities are its own primaries (x=0.64 for red, y=0.60
    for green), both far under 0xFEFF/65536=0.9961 - no RGB triple in
    `rgb_to_cie_xy`'s domain (0-255 per channel) can reach the cap. The
    loop below therefore cannot fail no matter whether the cap exists; it
    stays as a sanity check that the values are at least in the right
    ballpark, not as the cap's protection.

    Fault to prove it: return `round(x * 65536)` without the cap. As the
    paragraph above explains, that fault does NOT fail here - see
    `test_the_cap_itself_rejects_a_ratio_above_one` below, which is the
    test that actually protects the cap."""
    for colour in ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (0, 0, 0)):
        x, y = rgb_to_cie_xy(*colour)
        assert 0 <= x <= 0xFEFF
        assert 0 <= y <= 0xFEFF


def test_the_cap_itself_rejects_a_ratio_above_one():
    """The counterpart to the test above: since no real colour ever drives
    `rgb_to_cie_xy` anywhere near the ZCL cap, the cap has to be tested
    directly against the internal rounding helper instead.

    Fault to prove it: return `round(ratio * 65536)` from `_to_cie_component`
    without the `min(_CIE_MAX, ...)`."""
    from loxmatter.commands.color import _to_cie_component

    assert _to_cie_component(0.64) == 41943  # a real chromaticity, unaffected
    assert _to_cie_component(1.5) == 0xFEFF  # would be 98304 uncapped


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


# --- White as a colour point -------------------------------------------------
from loxmatter.commands.color import kelvin_to_cie_xy, kelvin_to_hue_saturation, planckian_xy


@pytest.mark.parametrize(
    ("kelvin", "x", "y"),
    [
        # Computed from Kim et al. (2002)'s published coefficients, not from
        # this code: 2700 K is the warm end of Loxone's Lumitech range,
        # 6500 K the cold end, 4000 K the branch boundary of both cubics.
        (2700, 0.4593, 0.4107),
        (4000, 0.3805, 0.3767),
        (6500, 0.3135, 0.3237),
    ],
)
def test_planckian_xy_matches_the_published_approximation(kelvin, x, y):
    got_x, got_y = planckian_xy(kelvin)
    assert got_x == pytest.approx(x, abs=0.0005)
    assert got_y == pytest.approx(y, abs=0.0005)


def test_planckian_xy_clamps_to_the_range_the_approximation_is_valid_for():
    """Outside 1667-25000 K the cubics diverge. A white a lamp cannot reach
    is approximated by the nearest one it can, like a tunable-white lamp
    clamping to its own physical limits.

    Fault to prove it: remove the clamp - 1000 K then lands far off the
    locus and the equality below fails."""
    assert planckian_xy(1000) == planckian_xy(1667)
    assert planckian_xy(30000) == planckian_xy(25000)
    assert planckian_xy(1667) == pytest.approx((0.5646, 0.4029), abs=0.0005)
    assert planckian_xy(25000) == pytest.approx((0.2525, 0.2523), abs=0.0005)


def test_kelvin_to_cie_xy_uses_the_zcl_encoding():
    assert kelvin_to_cie_xy(2700) == (30102, 26913)
    assert kelvin_to_cie_xy(6500) == (20545, 21212)


def test_kelvin_to_hue_saturation_gives_a_warm_white_and_a_near_white():
    """2700 K is an orange-ish, clearly desaturated white; 6500 K is almost
    exactly D65 and therefore nearly unsaturated. Values computed through
    xy -> sRGB (IEC 61966-2-1 inverse matrix and OETF) -> HSV."""
    assert kelvin_to_hue_saturation(2700) == (21, 165)
    _hue, saturation = kelvin_to_hue_saturation(6500)
    assert saturation <= 8
