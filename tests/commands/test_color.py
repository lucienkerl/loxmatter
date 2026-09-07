# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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
    """Referenzwerte aus der HSV-Definition, nicht aus einem Geraet."""
    h, s = rgb_to_hue_saturation(*rgb)
    assert h == pytest.approx(hue, abs=1)
    assert s == pytest.approx(saturation, abs=1)


@pytest.mark.parametrize(
    ("packed", "rgb"),
    [
        # Das Beispiel aus der Loxone-Knowledge-Base, im Moduldocstring
        # zitiert: 20040060 = 60 % Rot, 40 % Gruen, 20 % Blau.
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
    """Lieber ein klarer Fehler als eine erfundene Farbe am echten Geraet -
    dieselbe Haltung wie `kelvin_to_mireds` bei 0 Kelvin."""
    with pytest.raises(ValueError):
        loxone_rgb_to_rgb(packed)


def test_a_fractional_number_is_rejected():
    """Die Loxone-Codierung ist ganzzahlig; 20040060.5 waere ein Zeichen
    dafuer, dass hier eine ganz andere Zahl ankommt."""
    with pytest.raises(ValueError):
        loxone_rgb_to_rgb(20040060.5)
