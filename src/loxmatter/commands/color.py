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

Lumitech (Helligkeit + Farbtemperatur) - seit dem 8. September 2026 belegt,
davor jahrelang nicht. In der offiziellen Loxone-Dokumentation
(Knowledge-Base-Seite "Lighting Controller", Structure-File-PDF) steht die
Formel bis heute nicht. Der einzige Fund war ein Forumsbeitrag mit selbst
mitgeloggten DMX-Werten, der das Format "AABBBCCCC" vermutete (AA=20 als
Weiss-Marker, BBB=Helligkeit 0-100, CCCC=Kelvin) - der Autor nannte das
ausdruecklich eine Vermutung:
https://www.loxforum.com/forum/hardware-zubehoer-sensorik/143867-lumitech-
ausgang-dmx-dimmer (Beitrag #2, Jan W., 01.12.2018).

Diese Vermutung ist jetzt an einer echten Anlage bestaetigt - siehe
`lumitech_to_kelvin` unten fuer die 24 gemessenen Werte. Entscheidend war
dabei nicht die Menge, sondern dass zwei verschiedene Helligkeiten
auftraten (28 % und 100 %): erst das zeigt, dass das mittlere Feld sich
unabhaengig vom hinteren bewegt, statt zufaellig zu passen.

Der Befund kam aus einem Fehlerbild, nicht aus einer Recherche: der
Weiss-Regler der Loxone-App bewirkte nichts, und das Kommando-Log der
Bruecke zeigte 50 Ablehnungen mit 400. Der Lichtsteuerungs-Baustein
schickt Farbe UND Weiss ueber denselben Analogausgang, und die Bruecke las
jeden Weisswert als Farbe mit einem Kanal weit ueber 100 Prozent.

Zu keiner Zeit hat dieser Vorbehalt fuer RGB gegolten - `translate.py` hat
ihn bis zum 7. September 2026 faelschlich auch auf die RGB-Codierung
bezogen und deshalb Kommando 6 gesperrt (siehe Entwurf 2026-09-07,
Abschnitt 1).

Der Farb-Ausgang traegt deshalb beide Bedeutungen: `translate.py` fragt
`is_lumitech` und schickt je nachdem MoveToHueAndSaturation oder
MoveToColorTemperature. Das ist keine Heuristik - die Wertebereiche beider
Codierungen koennen sich nicht ueberschneiden (siehe `is_lumitech`).

Der `colortemp`-Ausgang nimmt weiterhin eine blanke Kelvinzahl entgegen,
nicht die rohe Lumitech-Zahl: er ist der Weg fuer eine Lichtsteuerung, die
Farbtemperatur getrennt ausgibt, und die WebUI benutzt ihn ebenso.
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
    """Die Helligkeit einer Loxone-Farbzahl, in Prozent.

    Loxone codiert die Helligkeit NICHT getrennt, sondern im Betrag der drei
    Kanaele: dieselbe Farbe halb so hell ist derselbe Farbton mit halbierten
    Prozentwerten. Das ist der Value-Anteil von HSV - genau der, den
    `rgb_to_hue_saturation` verwirft, weil Matter Farbe und Helligkeit in
    zwei getrennten Clustern fuehrt (ColorControl und LevelControl).

    Belegt an zwei Werten derselben Anlage (8. September 2026):

        18004020 -> (20,  4, 18)  Farbton 307,5 Grad  Saettigung 80,0 %  V 20 %
        85019094 -> (94, 19, 85)  Farbton 307,2 Grad  Saettigung 79,8 %  V 94 %

    Gleiche Farbe, einmal gedimmt. Wer nur Hue und Saturation schickt, wirft
    das Dimmen weg - und genau das hat die Bruecke bis zum 8. September 2026
    getan: der Helligkeitsregler der Loxone-App bewegte sich, die Leuchte
    blieb gleich hell.
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


# Lumitech: Kennung, Helligkeit, Kelvin in einer Zahl - `AA BBB CCCC`.
# Siehe `is_lumitech` und `lumitech_to_kelvin` unten.
LUMITECH_MARKER = 20
_LUMITECH_MIN = 200_000_000
_LUMITECH_MAX = 209_999_999


def is_lumitech(value: int) -> bool:
    """Ob diese Loxone-Zahl eine Farbtemperatur traegt statt einer Farbe.

    Der Lichtsteuerungs-Baustein schickt beides ueber DENSELBEN
    Analogausgang: RGB nach der Formel im Moduldocstring oben, Weisstoene im
    Lumitech-Format `AA BBB CCCC` (Kennung 20, Helligkeit 0-100, Kelvin).
    Wer nur RGB entpackt, liest einen Weisswert als Farbe mit einem Kanal
    weit ueber 100 Prozent - genau das ist im Betrieb passiert (8. September
    2026): der Weiss-Regler in der Loxone-App bewegte sich, die Bruecke
    antwortete 50-mal mit 400, und an der Leuchte aenderte sich nichts.

    **Die beiden Formate koennen sich nicht ueberschneiden**, und das ist
    keine gluecklose Faustregel, sondern rechnerisch: die groesste
    RGB-Zahl ist 100 + 100*1000 + 100*1_000_000 = 100_100_100, die
    kleinste Lumitech-Zahl 200_000_000. Ein Wert kann also nie beides
    bedeuten - deshalb darf diese Unterscheidung ueberhaupt automatisch
    getroffen werden.

    Die Stellenzahl gehoert zur Bedingung: `20100270` (acht Stellen) traegt
    zwar dieselben ersten Ziffern, ist aber eine gewoehnliche RGB-Zahl
    (r=270? nein - 270 > 100, sie waere ungueltig) beziehungsweise schlicht
    kein Lumitech-Wert. Der Bereichsvergleich deckt beides ab.
    """
    return _LUMITECH_MIN <= value <= _LUMITECH_MAX


def lumitech_to_brightness(value: int) -> int:
    """Die Helligkeit aus einer Lumitech-Zahl, in Prozent (Feld `BBB`).

    Gegenstueck zu `lumitech_to_kelvin`. Getrennte Funktionen statt eines
    Tupels, weil die beiden Werte in Matter in zwei verschiedene Cluster
    gehen - LevelControl und ColorControl - und die Aufrufer sie deshalb
    ohnehin einzeln brauchen.
    """
    if not is_lumitech(value):
        raise ValueError(f"Keine Lumitech-Zahl (Kennung {LUMITECH_MARKER} fehlt): {value}")
    brightness = value // 10_000 % 1000
    if brightness > 100:
        raise ValueError(
            f"Lumitech-Helligkeit liegt bei {brightness} %, erlaubt sind 0-100 (Zahl {value})"
        )
    return brightness


def lumitech_to_kelvin(value: int) -> int:
    """Die Farbtemperatur aus einer Lumitech-Zahl, in Kelvin.

    **Diese Codierung galt in diesem Projekt lange als unbelegt** (siehe
    Moduldocstring oben, Abschnitt Lumitech): die einzige Quelle war ein
    Forumsbeitrag, dessen Autor sein Format ausdruecklich eine Vermutung
    nannte. Belegt ist sie seit dem 8. September 2026 an einer echten
    Loxone-Installation mit Lumitech-DMX-Ausgang - 24 verschiedene Werte aus
    dem Kommando-Log der Bruecke, darunter zwei verschiedene Helligkeiten,
    die das mittlere Feld unabhaengig vom hinteren bewegen:

        200283057 -> Kennung 20 | Helligkeit  28 % | 3057 K
        200285742 -> Kennung 20 | Helligkeit  28 % | 5742 K
        201002700 -> Kennung 20 | Helligkeit 100 % | 2700 K
        201006500 -> Kennung 20 | Helligkeit 100 % | 6500 K

    Die Kelvinwerte spannen 2700 bis 6500 - der uebliche Bereich einer
    Tunable-White-Leuchte, mit 6500 K als Reglerende.

    **Die Helligkeit wird verworfen**, so wie beim RGB-Weg auch: sie laeuft
    ueber LevelControl, nicht ueber das Farbkommando (siehe
    `rgb_to_hue_saturation` und den Docstring von
    `commands/translate.py`). Ein Lumitech-Wert loest deshalb genau ein
    Matter-Kommando aus, nicht zwei - dieselbe Zurueckhaltung wie
    ueberall sonst hier, und ein halb gesetzter Zustand nach einem
    fehlgeschlagenen zweiten Aufruf kann so gar nicht entstehen.
    """
    if not is_lumitech(value):
        raise ValueError(f"Keine Lumitech-Zahl (Kennung {LUMITECH_MARKER} fehlt): {value}")
    brightness = value // 10_000 % 1000
    kelvin = value % 10_000
    if brightness > 100:
        raise ValueError(
            f"Lumitech-Helligkeit liegt bei {brightness} %, erlaubt sind 0-100 (Zahl {value})"
        )
    if kelvin <= 0:
        raise ValueError(f"Lumitech-Farbtemperatur muss groesser als 0 K sein (Zahl {value})")
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
