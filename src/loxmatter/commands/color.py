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

ACHTUNG - dieser Teil ist NICHT an Hardware validiert. Beim Bau stand keine
Matter-Leuchte zur Verfuegung; geprueft ist er ausschliesslich gegen
Referenzwerte der HSV-Definition und gegen die unten zitierte
Loxone-Dokumentation. Von allen Abbildungen im Projekt ist diese die
fehleranfaelligste, und ein Fehler sieht hier nach einem Geraetefehler aus,
nicht nach einem Umrechnungsfehler. Vor dem ersten Einsatz an einer echten
Leuchte gegenpruefen.

Stand 8. September 2026: Der Weg WebUI -> `MoveToHueAndSaturation` ist im
Browser gegen die laufende Anwendung durchgespielt worden. Belegt sind
Reiterleiste und Farbflaeche nur bei Leuchten, die beides koennen (die
Farbleuchte bekam beide Reiter und einen Kelvin-Regler 1801-6535 K, die
Weisston-Leuchte nur den Kelvin-Regler mit ihren echten Grenzen
2202-6535 K und keine Reiter), Startwerte werden aus den echten
Geraetesignalen gelesen, und vier Klicks in die Farbflaeche erzeugten vier
Kommandos (Senden beim Loslassen) mit den richtigen gepackten
Loxone-Zahlen - Rot 1002100, Gruen 1100001, Blau 100001001, Weiss
99099100. Diese vier Zahlen sind der aussagekraeftigste Beleg, weil sie die
gesamte Kette von Mausklick bis gepackter Loxone-Zahl bestaetigen. Nicht
belegt ist weiterhin, ob eine echte Leuchte tatsaechlich in der erwarteten
Farbe leuchtet: Beide Testleuchten waren beim Durchgang am matter-server
als offline gemeldet (`available=False`, stromlos oder ausserhalb der
Thread-Reichweite), und kein einziges Kommando hat ein Geraet erreicht.

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

Lumitech (Helligkeit + Farbtemperatur) - NICHT belegt. Fuer den
"Lumitech"-Ausgabemodus der Lichtsteuerung (Helligkeit plus Kelvin in einer
Zahl) hat sich in der offiziellen Loxone-Dokumentation (Knowledge-Base-Seite
"Lighting Controller", Structure-File-PDF) keine Formel finden lassen. Der
einzige Treffer ist ein Forumsbeitrag mit selbst mitgeloggten DMX-Werten,
der ein Format "AABBBCCCC" vermutet (AA=20 als Weiss-Marker, BBB=Helligkeit
0-100, CCCC=Kelvin), der Autor selbst nennt das ausdruecklich eine Vermutung
und keine dokumentierte Quelle:
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

Die beiden Funktionen hier bilden nur die (unstrittige) Matter-seitige
Umrechnung ab: Kelvin -> Mired und RGB -> Hue/Saturation.
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
