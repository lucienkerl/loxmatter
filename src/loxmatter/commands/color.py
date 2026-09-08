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

An Hardware gegengeprueft am 8. September 2026, an der eingecheckten
IKEA KAJPLATS E14 CWS (`tests/fixtures/nodes/ikea_kajplats_cws_lamp.json`,
Node 21). Bis dahin stand hier die Warnung, dieser Teil sei nie an einer
echten Leuchte gelaufen - beim Bau stand keine zur Verfuegung.

Gemessen wurde nicht die Umrechnung fuer sich, sondern die ganze Kette:
gepackte Loxone-Zahl -> `loxone_rgb_to_rgb` -> `rgb_to_hue_saturation` ->
MoveToHueAndSaturation -> was die Leuchte selbst zurueckmeldet
(CurrentHue 1/768/0, CurrentSaturation 1/768/1):

    gepackt 100        -> Leuchte meldet   0,0 Grad / 100 %   (Rot)
    gepackt 100000     -> Leuchte meldet 120,5 Grad / 100 %   (Gruen)
    gepackt 100000000  -> Leuchte meldet 239,5 Grad / 100 %   (Blau)
    gepackt 100100     -> Leuchte meldet  59,5 Grad / 100 %   (Gelb)
    gepackt 100100100  -> Leuchte meldet   0,0 Grad /   0 %   (Weiss)

Die Abweichungen von hoechstens 0,5 Grad sind die Matter-Quantisierung
(360/254 = 1,417 Grad je Schritt), kein Umrechnungsfehler. ColorMode
(1/768/8) sprang dabei erwartungsgemaess von 2 (Farbtemperatur) auf 0
(Hue/Saturation) - die Leuchte hat das Kommando also tatsaechlich
ausgefuehrt und nicht bloss quittiert.

Der Satz, warum das hier ueberhaupt steht, gilt unveraendert: von allen
Abbildungen im Projekt ist diese die fehleranfaelligste, und ein Fehler
sieht hier nach einem Geraetefehler aus, nicht nach einem
Umrechnungsfehler. Wer sie anfasst, misst besser noch einmal nach.

Die Messung oben beginnt bei der gepackten Zahl. Das Stueck davor - vom
Mausklick bis zu dieser Zahl - ist am selben Tag im Browser gegen die
laufende Anwendung geprueft worden: vier Klicks in die Farbflaeche
erzeugten vier Kommandos (Senden erst beim Loslassen) mit den Zahlen
1002100, 1100001, 100001001 und 99099100 fuer Rot, Gruen, Blau und Weiss.
Dabei bekam die Farbleuchte beide Modus-Reiter und einen Kelvin-Regler
1801-6535 K, die Weisston-Leuchte nur den Kelvin-Regler mit ihren eigenen
Grenzen 2202-6535 K und keine Reiter - die Abstufung entsteht also
tatsaechlich aus dem, was das Geraet kann, ohne dass die Oberflaeche sein
Modell kennt.

Beide Haelften zusammen decken die Kette lueckenlos ab: Klick -> gepackte
Zahl (Browser) und gepackte Zahl -> Farbe an der Leuchte (oben).

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
ausgang-dmx-dimmer (Beitrag #2, Jan W., 01.12.2018). Das ist keine Quelle,
auf die man sich verlassen sollte - deshalb bleibt die Dekodierung der
rohen Loxone-Lumitech-Zahl hier offen (siehe Spec 7.3 / Offene Punkte).
Zu keiner Zeit hat dieser Vorbehalt fuer RGB gegolten - `translate.py` hat
ihn bis zum 7. September 2026 faelschlich auch auf die RGB-Codierung
bezogen und deshalb Kommando 6 gesperrt (siehe Entwurf 2026-09-07,
Abschnitt 1).

`to_matter_call` in `translate.py` nimmt fuer Farbtemperatur deshalb
bewusst einen bereits entpackten Kelvin-Wert entgegen, nicht die rohe
Loxone-Zahl - das Entpacken ist Aufgabe der Aufrufer (Task 6 / WebUI), sobald
eine verlaessliche Quelle dafuer vorliegt.

The two functions here only implement the (uncontroversial) Matter-side
conversion: Kelvin -> mired and RGB -> hue/saturation.
"""

from __future__ import annotations

import colorsys
from typing import Literal

LoxoneColourErrorKind = Literal["not_integer", "negative", "channel_out_of_range"]


class LoxoneColourError(ValueError):
    """Ungueltige Loxone-Farbzahl - Einzelheiten als Felder, nicht nur als Satz.

    `str(self)` liefert weiterhin den deutschen Satz von `loxone_rgb_to_rgb`
    unten (fuers Server-Log - dieses Modul ist eine reine Rechenschicht ohne
    i18n-Abhaengigkeit, siehe Moduldocstring). `kind` plus die je nach Fall
    gesetzten Felder (`value`, `packed`, `channel`, `percent`) tragen
    dieselbe Information maschinenlesbar, damit ein Aufrufer mit eigener
    Uebersetzung (aktuell `commands/translate.py`, `_payload_hue_saturation`)
    eine ebenso genaue, aber sprachabhaengige Meldung bauen kann, ohne den
    deutschen Satz zu parsen.

    Eine eigene Ausnahmeklasse statt einer Vorab-Pruefung in `translate.py`,
    weil die Pruefregeln (was ist eine "ungueltige" Loxone-Farbzahl) sonst an
    zwei Stellen leben muessten und garantiert auseinanderdriften wuerden -
    derselbe Grund, aus dem `_PAYLOAD_BUILDERS` dort der einzige Ort fuer
    bediente Kommandos ist (siehe Moduldocstring von `translate.py`). Genau
    eines der optionalen Felder ist je nach `kind` befuellt:
    - "not_integer": `value` (die eingegebene, nicht-ganzzahlige Zahl).
    - "negative": `packed` (die eingegebene, negative Ganzzahl).
    - "channel_out_of_range": `channel`, `percent` und `packed` gemeinsam.
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
    """Entpackt die Loxone-Farbzahl in drei Kanaele zu je 0-255.

    Die Codierung ist oben im Moduldocstring mit offizieller Quelle belegt:
    `AQa = rot% + gruen% * 1000 + blau% * 1_000_000`. Sie transportiert je
    Kanal nur volle Prozent - die Farbe ist also bereits beim Verlassen von
    Loxone quantisiert, und diese Funktion kann das nicht zurueckholen
    (Entwurf 2026-09-07, Abschnitt 9.1).

    Ganzzahlig statt gerundet entgegengenommen: eine gebrochene Zahl kommt
    in dieser Codierung nicht vor, und sie stillschweigend zu runden hiesse,
    eine ganz andere Zahl - etwa einen bereits entpackten Kanal - als
    gueltige Farbe durchzuwinken.

    Wirft `LoxoneColourError` (eine `ValueError`-Unterklasse, siehe deren
    Docstring oben) statt eines nackten `ValueError` - Aufrufer, die nur auf
    `ValueError` pruefen (z. B. `tests/commands/test_color.py`), bemerken
    davon nichts; Aufrufer, die die Einzelheiten uebersetzen wollen (z. B.
    `translate.py`), koennen die Felder auslesen.
    """
    if value != int(value):
        raise LoxoneColourError(
            f"Loxone-Farbzahl muss ganzzahlig sein, war {value}",
            kind="not_integer",
            value=value,
        )
    packed = int(value)
    if packed < 0:
        raise LoxoneColourError(
            f"Loxone-Farbzahl darf nicht negativ sein, war {packed}",
            kind="negative",
            packed=packed,
        )

    percents = (packed % 1000, packed // 1000 % 1000, packed // 1_000_000)
    # Deutscher Kanalname fuers Server-Log (str(exc)), englisches Kuerzel als
    # sprachneutrales Feld fuer Aufrufer wie `translate.py` - siehe
    # `LoxoneColourError.channel` oben.
    channels = (("rot", "red"), ("gruen", "green"), ("blau", "blue"))
    for (channel_de, channel_slug), percent in zip(channels, percents, strict=True):
        if percent > 100:
            raise LoxoneColourError(
                f"Kanal {channel_de} liegt bei {percent} %, erlaubt sind 0-100 "
                f"(Loxone-Farbzahl {packed})",
                kind="channel_out_of_range",
                channel=channel_slug,
                percent=percent,
                packed=packed,
            )
    red, green, blue = (round(percent * 255 / 100) for percent in percents)
    return red, green, blue
