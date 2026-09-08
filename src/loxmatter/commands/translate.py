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

"""Uebersetzt einen Wunschzustand in ein Matter-Kommando.

Dieses Modul hat spaeter zwei Aufrufer: den HTTP-Endpoint fuer die virtuellen
Ausgaenge (Task 6) und die WebUI (Phase 5). Laege die Logik in einem von
beiden, gaebe es die Umrechnung zweimal - mit garantiert auseinanderdriftendem
Verhalten (Spec 4.2).

Was nicht in `_PAYLOAD_BUILDERS` steht, wirft. Ein Kommando mit erfundener
Nutzlast an ein echtes Geraet zu schicken ist schlechter als ein klarer
Fehler. Das gilt nicht nur fuer einen voellig unbekannten Cluster, sondern
auch fuer ein bekanntes Cluster mit einer unbekannten Kommando-ID darin: der
Dispatch schluesselt auf das Paar (Cluster-ID, Kommando-ID), nie auf die
Cluster-ID allein - siehe `test_known_cluster_with_unknown_command_raises`
(Cluster 768/ColorControl, Kommando 7),
`test_onoff_cluster_with_unknown_command_raises` (Cluster 6) und
`test_level_cluster_with_unknown_command_raises` (Cluster 8) in
`tests/commands/test_translate.py`. Cluster 768 Kommando 6 (Hue/Saturation)
wird seit dem 7. September 2026 bedient: die Loxone-seitige RGB-Codierung
ist in `color.py` mit offizieller Quelle belegt (Knowledge Base, "RGB
Lighting Controller"). Bis dahin stand hier die Begruendung, sie sei
unbelegt - das verwechselte RGB mit **Lumitech**, dem kombinierten
Helligkeits- und Kelvin-Ausgang, der weiterhin ohne belastbare Quelle ist
(siehe `color.py` und Entwurf 2026-09-07, Abschnitt 10.1). Nicht bedient
bleiben MoveToHue (0), MoveToSaturation (3), MoveToColor (7, xy) und
Enhanced (67) - die Bedienflaeche setzt Farbton und Saettigung in einem
Kommando, alles weitere waere unbelegte Flaeche. Cluster 6 und 8 kennen
jenseits von Off/On/Toggle bzw. MoveToLevel(WithOnOff) hier schlicht keine
weiteren Kommandos - das ist besonders beim Rohexport (`raw`) relevant, der
auch Kommandos ohne Eintrag in `clusters.yaml` durchlaesst, etwa
LevelControl Move/Step/Stop. Faelschlich ein Kommando zu bauen, nur weil der
Cluster bekannt ist, waere genau der Fehler, den diese Funktion vermeiden
soll.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from loxmatter import i18n
from loxmatter.commands.color import (
    LoxoneColourError,
    is_lumitech,
    kelvin_to_mireds,
    loxone_rgb_to_rgb,
    lumitech_to_brightness,
    lumitech_to_kelvin,
    rgb_to_brightness,
    rgb_to_hue_saturation,
)
from loxmatter.model.store import StoredCommand

LEVEL_MAX = 254

_CLUSTER_ONOFF = 6
_CLUSTER_LEVEL = 8
_CLUSTER_COLOR = 768

_COMMAND_OFF = 0
_COMMAND_ON = 1
_COMMAND_TOGGLE = 2
_COMMAND_MOVE_TO_LEVEL = 0
_COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF = 4
_COMMAND_COLOR_TEMPERATURE = 10

# `OptionsBitmap.kExecuteIfOff` von ColorControl (gegen das installierte SDK
# belegt: chip.clusters.Objects.ColorControl.Bitmaps.OptionsBitmap).
#
# Ohne dieses Bit verpufft ein Farbbefehl an einer AUSGESCHALTETEN Leuchte -
# an der eingecheckten KAJPLATS CWS am 8. September 2026 gemessen: der Wert
# 60100060 (gruen bei 60 %) brachte sie weiss und auf 100 % hoch, die Farbe
# kam nie an. Das ist Matter-Spezifikation, kein Geraetefehler.
#
# Die Alternative waere, den Pegel zuerst zu schicken. Dann ginge die
# Leuchte aber sichtbar in der ALTEN Farbe an und wechselte danach - ein
# Blitzen, das dieses Bit vermeidet, indem die Farbe schon sitzt, bevor
# Licht da ist.
_EXECUTE_IF_OFF = 1
_OPTIONS_EXECUTE_IF_OFF: dict[str, object] = {
    "optionsMask": _EXECUTE_IF_OFF,
    "optionsOverride": _EXECUTE_IF_OFF,
}
_COMMAND_HUE_SATURATION = 6


class UnsupportedValueError(ValueError):
    """Der Wert passt nicht zu diesem Kommando."""


@dataclass(frozen=True)
class MatterCall:
    node_id: int
    endpoint: int
    cluster_id: int
    command_id: int
    payload: dict[str, object] = field(default_factory=dict)


def _as_number(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise UnsupportedValueError(i18n.t("api.errors.value_not_a_number", value=value)) from exc
    if not math.isfinite(result):
        # float() liest "nan"/"inf"/"-inf" anstandslos ein. Liesse man das
        # durch, wuerde spaeter `round()` mit einem englischen `ValueError`
        # abstuerzen (nan) oder `kelvin_to_mireds` seine <=0-Pruefung
        # unbemerkt umgehen (nan ist nie <= 0) - beides ist hier keine Zahl,
        # die ein Kommando tragen kann.
        raise UnsupportedValueError(i18n.t("api.errors.value_not_a_number", value=value))
    return result


def _level(value: str) -> int:
    percent = _as_number(value)
    return max(0, min(LEVEL_MAX, round(percent * LEVEL_MAX / 100)))


@dataclass(frozen=True)
class _Built:
    """Was ein Payload-Bauer liefert.

    Normalerweise nur die Nutzlast; das Kommando steht dann im
    Tabelleneintrag. `command_id` setzt ein Bauer nur, wenn der WERT ein
    anderes Kommando desselben Clusters verlangt als der Eintrag - der
    Fall existiert genau einmal, siehe `_payload_hue_saturation`: der
    Loxone-Lichtsteuerungsbaustein schickt Farbe und Weiss ueber denselben
    Ausgang.

    Ein eigener Rueckgabetyp statt einer zweiten Tabelle "Wert -> Kommando"
    neben `_PAYLOAD_BUILDERS`: die Entscheidung, WELCHES Kommando ein Wert
    bedeutet, und die Nutzlast dafuer gehoeren untrennbar zusammen. Zwei
    Tabellen dafuer liefen frueher oder spaeter auseinander, und das faellt
    erst am echten Geraet auf.
    """

    payload: dict[str, object]
    command_id: int | None = None
    # Prozent, wenn dieser Wert AUSSERDEM eine Helligkeit traegt. Loxone
    # codiert sie im selben Wert wie die Farbe (im Betrag der RGB-Zahl bzw.
    # im Feld BBB von Lumitech), Matter fuehrt sie in einem anderen Cluster -
    # ein Loxone-Aufruf wird dadurch zu zwei Matter-Kommandos.
    brightness_percent: float | None = None


def _payload_none(_value: str) -> _Built:
    return _Built({})


def _payload_level(value: str) -> _Built:
    return _Built({"level": _level(value), "transitionTime": 0})


def _payload_color_temperature(value: str) -> _Built:
    return _Built(
        {
            "colorTemperatureMireds": kelvin_to_mireds(_as_number(value)),
            **_OPTIONS_EXECUTE_IF_OFF,
        }
    )


# Kanal-Kuerzel aus `LoxoneColourError.channel` (siehe `commands/color.py`)
# auf den zugehoerigen i18n-Schluessel fuer den uebersetzten Kanalnamen.
_LOXONE_COLOUR_CHANNEL_KEYS: dict[str, str] = {
    "red": "api.errors.loxone_colour_channel_red",
    "green": "api.errors.loxone_colour_channel_green",
    "blue": "api.errors.loxone_colour_channel_blue",
}


def _translate_loxone_colour_error(exc: LoxoneColourError) -> str:
    """Baut aus den Feldern von `LoxoneColourError` eine uebersetzte Meldung.

    `str(exc)` selbst ist hart Deutsch (siehe `LoxoneColourError`-Docstring
    in `color.py`) - hier wird stattdessen aus `exc.kind` und den je nach
    Fall gesetzten Feldern neu zusammengesetzt, ueber `i18n.t()` wie jeder
    andere Fehlerpfad in diesem Modul (Muster: `_as_number` oben). Die
    `assert`s narrowen fuer mypy nur, was `kind` bereits festlegt - siehe
    Docstring von `LoxoneColourError`, welches Feld zu welchem `kind`
    gehoert.
    """
    if exc.kind == "not_integer":
        assert exc.value is not None
        return i18n.t("api.errors.loxone_colour_not_integer", value=exc.value)
    if exc.kind == "negative":
        assert exc.packed is not None
        return i18n.t("api.errors.loxone_colour_negative", packed=exc.packed)
    assert exc.kind == "channel_out_of_range"
    assert exc.channel is not None
    assert exc.percent is not None
    assert exc.packed is not None
    channel = i18n.t(_LOXONE_COLOUR_CHANNEL_KEYS[exc.channel])
    return i18n.t(
        "api.errors.loxone_colour_channel_out_of_range",
        channel=channel,
        percent=exc.percent,
        packed=exc.packed,
    )


def _payload_hue_saturation(value: str) -> _Built:
    """Gepackte Loxone-Farbzahl -> Matter-Hue/Saturation.

    Zwei Umrechnungen hintereinander, beide in `commands/color.py` belegt:
    die Loxone-Codierung entpacken und das Ergebnis nach HSV wandeln.
    `loxone_rgb_to_rgb` wirft `LoxoneColourError` fuer eine unmoegliche Zahl
    - hier wird daraus `UnsupportedValueError`, damit der Aufrufer wie bei
    jedem anderen unpassenden Wert mit 400 antwortet und nicht mit 500.

    Die Meldung dafuer kommt NICHT aus `str(exc)` - das waere hart Deutsch
    (siehe `LoxoneColourError`-Docstring in `color.py`), waehrend jeder
    andere Fehlerpfad in diesem Modul ueber `i18n.t()` laeuft (Review-Fix,
    2026-09-07: `_payload_hue_saturation` reichte den deutschen `str(exc)`
    bis dahin unveraendert als HTTP-400-`detail` durch, auch bei
    englischer Sprachwahl). `_translate_loxone_colour_error` oben baut aus
    den Feldern von `LoxoneColourError` dieselbe Genauigkeit (welcher Kanal,
    welcher Wert) neu auf, nur uebersetzt.

    ACHTUNG fuer alle, die den Loxone-RGB-Baustein an dieses Kommando
    verdrahten (Befund I-3, Abschluss-Review 2026-09-08): der AQa-Ausgang
    dieses Bausteins traegt Farbe UND Helligkeit in EINER Zahl, aber diese
    Funktion entpackt daraus nur die Farbe - `MoveToHueAndSaturation` hat
    kein Feld fuer Helligkeit, die laeuft ausschliesslich ueber
    LevelControl (siehe `_payload_level` oben). Nachgerechnet: AQa 100, 50
    und 25 (Rot bei 100 %, 50 %, 25 % Helligkeit im Loxone-Baustein)
    ergeben alle drei `hue 0, sat 254` - identische Kommandos. Dimmen im
    Loxone-Baustein bewirkt an der Leuchte also NICHTS. AQa 0 ergibt
    `hue 0, sat 0`, also Weiss statt Aus - stumpfe Saettigung 0 ist Weiss,
    kein Ausschalten. Kein Programmierfehler, sondern Folge der bewussten
    Beschraenkung auf dieses eine Farbkommando (Entwurf 2026-09-07,
    Abschnitt 9.3) - aber mangels erreichbarer Hardware mit
    angeschlossenem Loxone-RGB-Baustein bislang UNGETESTET. Offener Punkt,
    siehe Entwurf Abschnitt 10.
    """
    number = _as_number(value)

    # Weiss statt Farbe: derselbe Loxone-Ausgang traegt beide Bedeutungen,
    # unterschieden durch die Kennung 20 (siehe `color.is_lumitech` fuer den
    # Beleg und dafuer, warum sich die Wertebereiche nicht ueberschneiden
    # koennen). Vor dem 8. September 2026 fiel ein solcher Wert hier in die
    # RGB-Entpackung, scheiterte an einem Kanal ueber 100 Prozent und kam
    # als 400 zurueck - der Weiss-Regler der Loxone-App bewirkte nichts.
    # Nur ganzzahlige Werte kommen ueberhaupt in Frage - `is_lumitech`
    # erwartet eine Ganzzahl, und eine gebrochene Zahl ist in keiner der
    # beiden Codierungen vorgesehen.
    if number == int(number) and is_lumitech(int(number)):
        try:
            kelvin = lumitech_to_kelvin(int(number))
        except ValueError as exc:
            raise UnsupportedValueError(
                i18n.t("api.errors.lumitech_malformed", value=value)
            ) from exc
        return _Built(
            {"colorTemperatureMireds": kelvin_to_mireds(kelvin), **_OPTIONS_EXECUTE_IF_OFF},
            command_id=_COMMAND_COLOR_TEMPERATURE,
            brightness_percent=lumitech_to_brightness(int(number)),
        )

    try:
        red, green, blue = loxone_rgb_to_rgb(number)
    except LoxoneColourError as exc:
        raise UnsupportedValueError(_translate_loxone_colour_error(exc)) from exc
    hue, saturation = rgb_to_hue_saturation(red, green, blue)
    return _Built(
        {
            "hue": hue,
            "saturation": saturation,
            "transitionTime": 0,
            **_OPTIONS_EXECUTE_IF_OFF,
        },
        brightness_percent=rgb_to_brightness(red, green, blue),
    )


# Einziger Ort, an dem festgelegt ist, welche (Cluster-ID, Kommando-ID)-Paare
# bedient werden. Der Dispatch in `to_matter_calls` liest diese Zuordnung nur
# noch aus - ein weiteres Kommando zu unterstuetzen ist eine Datenaenderung
# hier, keine neue Verzweigung dort, und die Menge der bedienten Paare ist auf
# einen Blick vollstaendig.
_PAYLOAD_BUILDERS: dict[tuple[int, int], Callable[[str], _Built]] = {
    (_CLUSTER_ONOFF, _COMMAND_OFF): _payload_none,
    (_CLUSTER_ONOFF, _COMMAND_ON): _payload_none,
    (_CLUSTER_ONOFF, _COMMAND_TOGGLE): _payload_none,
    (_CLUSTER_LEVEL, _COMMAND_MOVE_TO_LEVEL): _payload_level,
    (_CLUSTER_LEVEL, _COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF): _payload_level,
    (_CLUSTER_COLOR, _COMMAND_COLOR_TEMPERATURE): _payload_color_temperature,
    (_CLUSTER_COLOR, _COMMAND_HUE_SATURATION): _payload_hue_saturation,
}


def to_matter_calls(command: StoredCommand, value: str) -> list[MatterCall]:
    """Die Matter-Aufrufe zu einem exportierten Kommando-Schluessel.

    **Eine Liste, kein einzelner Aufruf**, weil ein Loxone-Wert mehr als
    eine Sache bedeuten kann: der Farb-Ausgang der Lichtsteuerung traegt
    Farbe UND Helligkeit in einer Zahl, Matter fuehrt beides in getrennten
    Clustern (ColorControl und LevelControl). Bis zum 8. September 2026 gab
    diese Funktion nur den Farbteil zurueck - der Helligkeitsregler der
    Loxone-App bewirkte deshalb nichts.

    **Die Reihenfolge ist verbindlich: erst die Farbe, dann der Pegel.**
    `MoveToLevelWithOnOff` schaltet eine ausgeschaltete Leuchte ein; kaeme
    der Pegel zuerst, ginge sie sichtbar in der alten Farbe an und wechselte
    danach. Umgekehrt faellt der Farbwechsel im ausgeschalteten Zustand
    niemandem auf - vorausgesetzt, er kommt dort ueberhaupt an, und genau
    dafuer traegt die Farbnutzlast `ExecuteIfOff` (siehe
    `_OPTIONS_EXECUTE_IF_OFF`). An der Leuchte gemessen: aus dem
    AUS-Zustand heraus brachte 36060036 sie auf 60 % mit Farbton 120,5 Grad
    und Saettigung 39,8 % - Farbe und Helligkeit beide. Ohne das Bit kam sie
    weiss hoch.

    **Ein Fehlschlag beim zweiten Aufruf hinterlaesst einen halben
    Zustand** - die Farbe sitzt, die Helligkeit nicht. Das ist der Preis
    dafuer, dass Loxone beides in einem Wert schickt und Matter es getrennt
    verlangt; ein Zurueckrollen waere ein zweiter Aufruf, der genauso
    scheitern kann. Der Aufrufer meldet den Fehlschlag (502), statt ihn zu
    verschlucken.

    Der Pegel geht ueber `MoveToLevelWithOnOff` (8/4), nicht ueber
    `MoveToLevel` (8/0): Loxone meint mit 0 wirklich AUS. Mit 8/0 bliebe die
    Leuchte bei Helligkeit 0 eingeschaltet stehen. Beide eingecheckten
    Leuchten fuehren 8/4, und die Geraetetypen 268/268 verlangen
    LevelControl ohnehin - eine Leuchte ohne diesen Cluster lehnt den
    zweiten Aufruf ab, und das faellt als 502 auf statt still zu wirken.
    """

    build_payload = _PAYLOAD_BUILDERS.get((command.cluster_id, command.command_id))
    if build_payload is None:
        raise UnsupportedValueError(
            i18n.t(
                "api.errors.command_unsupported",
                cluster_id=command.cluster_id,
                command_id=command.command_id,
            )
        )

    built = build_payload(value)

    # Bei Helligkeit 0 gibt es keine Farbe zu setzen - und ein Farbkommando
    # waere hier nicht nur ueberfluessig, sondern sichtbar falsch: Loxone
    # schickt zum Ausschalten den Wert 0, und der ist in der RGB-Codierung
    # Saettigung 0, also WEISS. Die Leuchte faerbte sich weiss, WAEHREND sie
    # noch leuchtete, und ging erst danach aus - ein heller Blitz beim
    # Ausschalten (an der Leuchte beobachtet, 8. September 2026; weiss nutzt
    # alle LEDs, gesaettigtes Rot nur die roten, der Blitz war deshalb sogar
    # heller als das Bild davor).
    #
    # Verglichen wird der GERUNDETE Pegel, nicht die Prozentzahl: was auf
    # Stufe 0 rundet, schaltet ohnehin aus, und der Farbbefehl davor waere
    # derselbe Blitz.
    level = _level(str(built.brightness_percent)) if built.brightness_percent is not None else None
    if level == 0:
        return [
            MatterCall(
                node_id=command.node_id,
                endpoint=command.endpoint,
                cluster_id=_CLUSTER_LEVEL,
                command_id=_COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF,
                payload={"level": 0, "transitionTime": 0},
            )
        ]

    calls = [
        MatterCall(
            node_id=command.node_id,
            endpoint=command.endpoint,
            cluster_id=command.cluster_id,
            # Der Wert darf das Kommando bestimmen - siehe `_Built`.
            command_id=command.command_id if built.command_id is None else built.command_id,
            payload=built.payload,
        )
    ]
    if built.brightness_percent is not None:
        calls.append(
            MatterCall(
                node_id=command.node_id,
                endpoint=command.endpoint,
                cluster_id=_CLUSTER_LEVEL,
                command_id=_COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF,
                payload={"level": level, "transitionTime": 0},
            )
        )
    return calls
