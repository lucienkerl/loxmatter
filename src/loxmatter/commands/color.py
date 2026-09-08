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

"""Farbraum-Umrechnung zwischen Loxone und Matter.

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

Rechercheergebnis zur Loxone-seitigen Farbcodierung (Schritt 1 dieser Task):

RGB - belegt. Der Baustein "RGB Lighting Controller" gibt Farbe auf einem
einzelnen Analogausgang (AQa) als eine Dezimalzahl aus, die drei
Prozentwerte (je 0-100) dezimal aneinanderreiht:

    AQa = rot% + gruen% * 1000 + blau% * 1_000_000

z. B. 20040060 = 60 % Rot, 40 % Gruen, 20 % Blau. Quelle: Loxone
Knowledge Base, "RGB Lighting Controller", Abschnitt "Outputs", Eintrag
AQa: "%-value red + %-value green * 1000 + %-value blue * 1000000".
https://www.loxone.com/enen/kb/rgb-scene-controller/ (abgerufen 2026-09-02).
Bestaetigt durch die Community-Doku "Loxone RGB in echtes RGB umrechnen"
(gleiche Ziffernaufteilung, an Shelly-RGBW-Beispielen erklaert):
https://loxwiki.atlassian.net/wiki/spaces/LOX/pages/1602650263 (Community-
Wiki, nicht offiziell, hier nur als Bestaetigung der offiziellen Quelle
herangezogen).

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
    """Matter misst Farbtemperatur in Mired, dem Kehrwert von Kelvin.

    Abgeschnitten statt gerundet: nur so trifft diese Funktion die von den
    Tests gepinnten Referenzwerte (z. B. 153 Mired fuer 6500 K) - gerundet
    waere 6500 K faelschlich 154 Mired. Dass Abschneiden auch allgemein die
    in Zigbee/Matter uebliche Konvention ist, ist hier anders als bei der
    RGB-Codierung oben nicht mit einer Quelle belegt; belegt ist nur die
    Uebereinstimmung mit den zitierten Referenzwerten.
    """
    if kelvin <= 0:
        raise ValueError(f"Kelvin muss groesser als 0 sein, war {kelvin}")
    return int(1_000_000 / kelvin)


def rgb_to_hue_saturation(r: int, g: int, b: int) -> tuple[int, int]:
    """RGB (0-255) nach Matter-Hue und -Saturation (beide 0-254).

    `colorsys.rgb_to_hsv` liefert das Tripel in der Reihenfolge (h, s, v) -
    der dritte Wert ist Value/Helligkeit, nicht Saturation, und wird hier
    verworfen. Bei Weiss (Saettigung 0, Helligkeit 1) faellt eine vertauschte
    Zuordnung sofort auf; bei den reinen Grundfarben waeren s und v zufaellig
    beide 1 und der Fehler unsichtbar geblieben.
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
