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

"""Colour-space conversion between Loxone and Matter.

WARNING - this part is NOT validated against hardware. No Matter light was
available while it was built; it is checked only against the HSV
definition's reference values and against the Loxone documentation cited
below. Of all the mappings in the project, this is the most error-prone,
and a bug here looks like a device fault, not a conversion bug. Cross-check
before the first use against a real light.

Research findings on Loxone-side colour encoding (step 1 of this task):

RGB - documented. The "RGB Lighting Controller" block outputs colour on a
single analog output (AQa) as one decimal number that concatenates three
percentage values (each 0-100) digit by digit:

    AQa = red% + green% * 1000 + blue% * 1_000_000

e.g. 20040060 = 60% red, 40% green, 20% blue. Source: Loxone
Knowledge Base, "RGB Lighting Controller", section "Outputs", entry
AQa: "%-value red + %-value green * 1000 + %-value blue * 1000000".
https://www.loxone.com/enen/kb/rgb-scene-controller/ (retrieved 2026-09-02).
Confirmed by the community documentation "Loxone RGB in echtes RGB
umrechnen" (same digit split, explained with Shelly RGBW examples):
https://loxwiki.atlassian.net/wiki/spaces/LOX/pages/1602650263 (community
wiki, unofficial, cited here only as confirmation of the official source).

Lumitech (brightness + colour temperature) - NOT documented. No formula
could be found in the official Loxone documentation (the "Lighting
Controller" knowledge-base page, the structure-file PDF) for the
"Lumitech" output mode of the light controller (brightness plus Kelvin in
one number). The only hit is a forum post with self-logged DMX values that
guesses at a format "AABBBCCCC" (AA=20 as a white marker, BBB=brightness
0-100, CCCC=Kelvin); the author himself explicitly calls this a guess, not
a documented source:
https://www.loxforum.com/forum/hardware-zubehoer-sensorik/143867-lumitech-
ausgang-dmx-dimmer (post #2, Jan W., 2018-12-01). That is not a source to
rely on - so decoding the raw Loxone Lumitech number is left open here (see
spec 7.3 / open points). `to_matter_call` in `translate.py` therefore
deliberately accepts an already-unpacked Kelvin value for colour
temperature, not the raw Loxone number - unpacking it is the caller's job
(task 6 / WebUI) once a reliable source exists for it.

The two functions here only implement the (uncontroversial) Matter-side
conversion: Kelvin -> mired and RGB -> hue/saturation.
"""

from __future__ import annotations

import colorsys


def kelvin_to_mireds(kelvin: float) -> int:
    """Matter measures colour temperature in mired, the reciprocal of Kelvin.

    Truncated rather than rounded: only this way does the function hit the
    reference values pinned by the tests (e.g. 153 mired for 6500 K) -
    rounded, 6500 K would incorrectly become 154 mired. That truncation is
    also generally the convention used in Zigbee/Matter - unlike the RGB
    encoding above, this is not backed here by a source; what is backed is
    only agreement with the cited reference values.
    """
    if kelvin <= 0:
        raise ValueError(f"Kelvin must be greater than 0, was {kelvin}")
    return int(1_000_000 / kelvin)


def rgb_to_hue_saturation(r: int, g: int, b: int) -> tuple[int, int]:
    """RGB (0-255) to Matter hue and saturation (both 0-254).

    `colorsys.rgb_to_hsv` returns the triple in the order (h, s, v) - the
    third value is value/brightness, not saturation, and is discarded here.
    For white (saturation 0, brightness 1) a swapped assignment would show
    up immediately; for the pure primary colours, s and v would both
    happen to be 1 and the bug would have stayed invisible.
    """
    h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return round(h * 254), round(s * 254)
