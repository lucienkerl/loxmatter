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

Lumitech (brightness + color temperature) - verified since September 8, 2026,
not for years before. In the official Loxone documentation
(Knowledge Base page "Lighting Controller", Structure File PDF) the
formula is not there yet. The only find was a forum post with self-
logged DMX values, which guessed the format "AABBBCCCC" (AA=20 as
white marker, BBB=brightness 0-100, CCCC=Kelvin) - the author explicitly called
this a guess:
https://www.loxforum.com/forum/hardware-zubehoer-sensorik/143867-lumitech-
ausgang-dmx-dimmer (post #2, Jan W., 01.12.2018).

This guess is now confirmed on a real installation - see
`lumitech_to_kelvin` below for the 24 measured values. The critical part was
not the quantity, but that two different brightness levels
appeared (28 % and 100 %): only this shows that the middle field
moves independently from the back one, rather than just happening to match.

The finding came from an error pattern, not from research: the
white controller in the Loxone app had no effect, and the command log of the
bridge showed 50 rejections with 400. The light control block
sends color AND white through the same analog output, and the bridge read
each white value as a color with a channel far over 100 percent.

This caveat never applied to RGB - `translate.py` had
wrongly applied it also to the RGB encoding until September 7, 2026
and therefore blocked command 6 (see draft 2026-09-07,
section 1).

The color output therefore carries both meanings: `translate.py` asks
`is_lumitech` and sends either MoveToHueAndSaturation or
MoveToColorTemperature accordingly. This is not a heuristic - the value ranges
of both encodings cannot overlap (see `is_lumitech`).

The `colortemp` output still accepts a plain Kelvin number,
not the raw Lumitech number: it is the path for a light control that
outputs color temperature separately, and the WebUI uses it as well.
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


def rgb_to_brightness(r: int, g: int, b: int) -> float:
    """The brightness of a Loxone color number, in percent.

    Loxone does NOT encode brightness separately, but in the magnitude of the three
    channels: the same color at half brightness is the same hue with halved
    percentage values. This is the Value component of HSV - exactly the one that
    `rgb_to_hue_saturation` discards because Matter manages color and brightness in
    two separate clusters (ColorControl and LevelControl).

    Verified with two values from the same installation (September 8, 2026):

        18004020 -> (20,  4, 18)  hue 307.5 degrees  saturation 80.0 %  V 20 %
        85019094 -> (94, 19, 85)  hue 307.2 degrees  saturation 79.8 %  V 94 %

    Same color, dimmed once. Whoever sends only Hue and Saturation discards
    the dimming - and that's exactly what the bridge did until September 8, 2026:
    the brightness controller in the Loxone app moved, but the lamp
    stayed equally bright.
    """
    _, _, value = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return value * 100


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


# Lumitech: identifier, brightness, Kelvin in one number - `AA BBB CCCC`.
# See `is_lumitech` and `lumitech_to_kelvin` below.
LUMITECH_MARKER = 20
_LUMITECH_MIN = 200_000_000
_LUMITECH_MAX = 209_999_999


def is_lumitech(value: int) -> bool:
    """Whether this Loxone number carries a color temperature instead of a color.

    The light control block sends both over THE SAME
    analog output: RGB according to the formula in the module docstring above, white tones in
    Lumitech format `AA BBB CCCC` (identifier 20, brightness 0-100, Kelvin).
    Whoever only unpacks RGB reads a white value as a color with a channel
    far over 100 percent - that's exactly what happened in operation (September 8,
    2026): the white controller in the Loxone app moved, the bridge
    responded 50 times with 400, and nothing changed on the lamp.

    **The two formats cannot overlap**, and this is
    not a lucky rule of thumb, but mathematically: the largest
    RGB number is 100 + 100*1000 + 100*1_000_000 = 100_100_100, the
    smallest Lumitech number is 200_000_000. A value can therefore never mean both
    - which is why this distinction can be made automatically at all.

    The digit count is part of the condition: `20100270` (eight digits) carries
    the same first digits, but is a regular RGB number
    (r=270? no - 270 > 100, it would be invalid) or simply
    not a Lumitech value. The range comparison covers both.
    """
    return _LUMITECH_MIN <= value <= _LUMITECH_MAX


def lumitech_to_brightness(value: int) -> int:
    """The brightness from a Lumitech number, in percent (field `BBB`).

    Counterpart to `lumitech_to_kelvin`. Separate functions instead of a
    tuple because the two values in Matter go to two different clusters
    - LevelControl and ColorControl - and callers therefore
    need them individually anyway.
    """
    if not is_lumitech(value):
        raise ValueError(f"Not a Lumitech number (identifier {LUMITECH_MARKER} missing): {value}")
    brightness = value // 10_000 % 1000
    if brightness > 100:
        raise ValueError(
            f"Lumitech brightness is {brightness} %, allowed are 0-100 (number {value})"
        )
    return brightness


def lumitech_to_kelvin(value: int) -> int:
    """The color temperature from a Lumitech number, in Kelvin.

    **This encoding was considered undocumented in this project for a long time** (see
    module docstring above, Lumitech section): the only source was a
    forum post whose author explicitly called his format a guess.
    It is verified since September 8, 2026 on a real
    Loxone installation with Lumitech DMX output - 24 different values from
    the command log of the bridge, including two different brightness levels
    that move the middle field independently from the back one:

        200283057 -> identifier 20 | brightness  28 % | 3057 K
        200285742 -> identifier 20 | brightness  28 % | 5742 K
        201002700 -> identifier 20 | brightness 100 % | 2700 K
        201006500 -> identifier 20 | brightness 100 % | 6500 K

    The Kelvin values span 2700 to 6500 - the normal range of a
    tunable white lamp, with 6500 K as the slider limit.

    **The brightness is discarded**, just like on the RGB path: it goes
    via LevelControl, not via the color command (see
    `rgb_to_hue_saturation` and the docstring of
    `commands/translate.py`). A Lumitech value therefore triggers exactly one
    Matter command, not two - the same restraint as
    everywhere else here, and a partial state after a
    failed second call simply cannot occur.
    """
    if not is_lumitech(value):
        raise ValueError(f"Not a Lumitech number (identifier {LUMITECH_MARKER} missing): {value}")
    brightness = value // 10_000 % 1000
    kelvin = value % 10_000
    if brightness > 100:
        raise ValueError(
            f"Lumitech brightness is {brightness} %, allowed are 0-100 (number {value})"
        )
    if kelvin <= 0:
        raise ValueError(f"Lumitech color temperature must be greater than 0 K (number {value})")
    return kelvin


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
    #
    # The two halves of each pair were a German label and an English slug
    # until this file was translated, and are now identical. Collapsing the
    # pair is the obvious tidy-up, but it changes code rather than prose and
    # so was left for a separate change.
    channels = (("red", "red"), ("green", "green"), ("blue", "blue"))
    for (channel_name, channel_slug), percent in zip(channels, percents, strict=True):
        if percent > 100:
            raise LoxoneColourError(
                f"Channel {channel_name} is at {percent} %, allowed is 0-100 "
                f"(Loxone color number {packed})",
                kind="channel_out_of_range",
                channel=channel_slug,
                percent=percent,
                packed=packed,
            )
    red, green, blue = (round(percent * 255 / 100) for percent in percents)
    return red, green, blue
