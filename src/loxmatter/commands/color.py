"""Farbraum-Umrechnung zwischen Loxone und Matter.

ACHTUNG - dieser Teil ist NICHT an Hardware validiert. Beim Bau stand keine
Matter-Leuchte zur Verfuegung; geprueft ist er ausschliesslich gegen
Referenzwerte der HSV-Definition und gegen die unten zitierte
Loxone-Dokumentation. Von allen Abbildungen im Projekt ist diese die
fehleranfaelligste, und ein Fehler sieht hier nach einem Geraetefehler aus,
nicht nach einem Umrechnungsfehler. Vor dem ersten Einsatz an einer echten
Leuchte gegenpruefen.

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
`to_matter_call` in `translate.py` nimmt fuer Farbtemperatur deshalb
bewusst einen bereits entpackten Kelvin-Wert entgegen, nicht die rohe
Loxone-Zahl - das Entpacken ist Aufgabe der Aufrufer (Task 6 / WebUI), sobald
eine verlaessliche Quelle dafuer vorliegt.

Die beiden Funktionen hier bilden nur die (unstrittige) Matter-seitige
Umrechnung ab: Kelvin -> Mired und RGB -> Hue/Saturation.
"""

from __future__ import annotations

import colorsys


def kelvin_to_mireds(kelvin: float) -> int:
    """Matter misst Farbtemperatur in Mired, dem Kehrwert von Kelvin.

    Abgeschnitten statt gerundet: die in Zigbee/Matter gebraeuchlichen
    Referenzwerte (z. B. 153 Mired fuer 6500 K) entstehen durch Abschneiden
    der Nachkommastellen, nicht durch kaufmaennisches Runden - gerundet waere
    6500 K faelschlich 154 Mired.
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
