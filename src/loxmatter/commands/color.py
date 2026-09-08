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

Checked against hardware on September 8, 2026, on the checked-in
IKEA KAJPLATS E14 CWS (`tests/fixtures/nodes/ikea_kajplats_cws_lamp.json`,
node 21). Until then the warning here said this part had never run against
a real light - none was available at build time.

What was measured was not the conversion by itself, but the whole chain:
packed Loxone number -> `loxone_rgb_to_rgb` -> `rgb_to_hue_saturation` ->
MoveToHueAndSaturation -> what the light itself reports back
(CurrentHue 1/768/0, CurrentSaturation 1/768/1):

    packed 100        -> light reports   0.0 degrees / 100 %   (red)
    packed 100000     -> light reports 120.5 degrees / 100 %   (green)
    packed 100000000  -> light reports 239.5 degrees / 100 %   (blue)
    packed 100100     -> light reports  59.5 degrees / 100 %   (yellow)
    packed 100100100  -> light reports   0.0 degrees /   0 %   (white)

The deviations of at most 0.5 degrees are the Matter quantization
(360/254 = 1.417 degrees per step), not a conversion error. ColorMode
(1/768/8) jumped, as expected, from 2 (color temperature) to 0
(hue/saturation) in the process - so the light actually executed the
command and did not just acknowledge it.

The reason this even exists here still holds unchanged: of all the
mappings in the project this is the most error-prone, and an error
here looks like a device fault, not a
conversion error. Whoever touches it should measure again.

The measurement above starts at the packed number. The piece before it -
from the mouse click to this number - was checked in the browser against
the running application on the same day: four clicks into the color area
produced four commands (sent only on release) with the numbers
1002100, 1100001, 100001001, and 99099100 for red, green, blue, and white.
In the process the color light got both mode tabs and a Kelvin slider
1801-6535 K, the white-tone light only the Kelvin slider with its own
limits 2202-6535 K and no tabs - so the gradation actually results from
what the device can do, without the UI knowing its model.

Together, both halves cover the chain without gaps: click -> packed
number (browser) and packed number -> color on the light (above).

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
ausgang-dmx-dimmer (post #2, Jan W., 2018-12-01). That is not a source that
should be relied on - which is why decoding the raw Loxone Lumitech number
stays open here (see spec 7.3 / open points).
This reservation never applied to RGB - `translate.py` wrongly
applied it to the RGB encoding too, until September 7, 2026,
and therefore blocked command 6 (see design 2026-09-07,
section 1).

`to_matter_call` in `translate.py` therefore deliberately accepts an
already-unpacked Kelvin value for color temperature, not the raw
Loxone number - unpacking is the caller's job (task 6 / WebUI), once
a reliable source for it exists.

The two functions here only implement the (uncontroversial) Matter-side
conversion: Kelvin -> mired and RGB -> hue/saturation.
"""

from __future__ import annotations

import colorsys
from typing import Literal

LoxoneColourErrorKind = Literal["not_integer", "negative", "channel_out_of_range"]


class LoxoneColourError(ValueError):
    """Invalid Loxone color number - details as fields, not only as a sentence.

    `str(self)` still returns the plain sentence from `loxone_rgb_to_rgb`
    below (for the server log - this module is a pure computation layer with
    no i18n dependency, see the module docstring). `kind` plus the fields
    set depending on the case (`value`, `packed`, `channel`, `percent`)
    carry the same information in a machine-readable way, so that a caller
    with its own translation (currently `commands/translate.py`,
    `_payload_hue_saturation`) can build an equally precise but
    language-dependent message without parsing the sentence.

    A dedicated exception class instead of an upfront check in
    `translate.py`, because the validation rules (what counts as an
    "invalid" Loxone color number) would otherwise have to live in two
    places and would be guaranteed to drift apart - the same reason
    `_PAYLOAD_BUILDERS` there is the single place for
    supported commands (see the module docstring of `translate.py`). Exactly
    one of the optional fields is filled in depending on `kind`:
    - "not_integer": `value` (the entered, non-integer number).
    - "negative": `packed` (the entered, negative integer).
    - "channel_out_of_range": `channel`, `percent`, and `packed` together.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: LoxoneColourErrorKind,
        value: float | None = None,
        packed: int | None = None,
        channel: str | None = None,
        percent: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.value = value
        self.packed = packed
        self.channel = channel
        self.percent = percent


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


def loxone_rgb_to_rgb(value: float) -> tuple[int, int, int]:
    """Unpacks the Loxone color number into three channels of 0-255 each.

    The encoding is backed by an official source above in the module
    docstring: `AQa = red% + green% * 1000 + blue% * 1_000_000`. It carries
    only whole percent per channel - so the color is already quantized on
    leaving Loxone, and this function cannot recover that
    (design 2026-09-07, section 9.1).

    Accepted as an integer rather than rounded: a fractional number does
    not occur in this encoding, and silently rounding it would mean
    waving through a completely different number - say, an already
    unpacked channel - as a valid color.

    Raises `LoxoneColourError` (a `ValueError` subclass, see its
    docstring above) instead of a bare `ValueError` - callers that only
    check for `ValueError` (e.g. `tests/commands/test_color.py`) notice
    nothing different; callers that want to translate the details (e.g.
    `translate.py`) can read out the fields.
    """
    if value != int(value):
        raise LoxoneColourError(
            f"Loxone color number must be an integer, was {value}",
            kind="not_integer",
            value=value,
        )
    packed = int(value)
    if packed < 0:
        raise LoxoneColourError(
            f"Loxone color number must not be negative, was {packed}",
            kind="negative",
            packed=packed,
        )

    percents = (packed % 1000, packed // 1000 % 1000, packed // 1_000_000)
    # Channel name for the server log (str(exc)); the same English slug
    # also serves as the language-neutral field for callers such as
    # `translate.py` - see `LoxoneColourError.channel` above.
    channels = (("red", "red"), ("green", "green"), ("blue", "blue"))
    for (channel_de, channel_slug), percent in zip(channels, percents, strict=True):
        if percent > 100:
            raise LoxoneColourError(
                f"Channel {channel_de} is at {percent} %, allowed is 0-100 "
                f"(Loxone color number {packed})",
                kind="channel_out_of_range",
                channel=channel_slug,
                percent=percent,
                packed=packed,
            )
    red, green, blue = (round(percent * 255 / 100) for percent in percents)
    return red, green, blue
