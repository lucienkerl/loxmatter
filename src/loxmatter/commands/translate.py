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

"""Translates a desired state into a Matter command.

This module will later have two callers: the HTTP endpoint for the virtual
outputs (task 6) and the WebUI (phase 5). If the logic lived in either one
of the two, the conversion would exist twice - with guaranteed drift in
behaviour (spec 4.2).

Whatever is not in `_PAYLOAD_BUILDERS` raises. Sending a command with a
made-up payload to a real device is worse than a clear
error. That holds not only for a completely unknown cluster, but
also for a known cluster with an unknown command ID inside it: the
dispatch keys on the pair (cluster ID, command ID), never on the
cluster ID alone - see `test_known_cluster_with_unknown_command_raises`
(cluster 768/ColorControl, command 7),
`test_onoff_cluster_with_unknown_command_raises` (cluster 6) and
`test_level_cluster_with_unknown_command_raises` (cluster 8) in
`tests/commands/test_translate.py`. Cluster 768 command 6 (Hue/Saturation)
has been supported since September 7, 2026: the Loxone-side RGB encoding
is backed by an official source in `color.py` (knowledge base, "RGB
Lighting Controller"). Until then the reasoning here said it was
unbacked - that confused RGB with **Lumitech**, the combined
brightness-and-Kelvin output, which remains without a solid source
(see `color.py` and design 2026-09-07, section 10.1). Not supported
remain MoveToHue (0), MoveToSaturation (3), MoveToColor (7, xy) and
Enhanced (67) - the control UI sets hue and saturation in one
command, anything further would be unbacked territory. Clusters 6 and 8
simply know no further commands here beyond Off/On/Toggle and
MoveToLevel(WithOnOff) respectively - that matters especially for the raw
export (`raw`), which also lets through commands with no entry in
`clusters.yaml`, such as LevelControl Move/Step/Stop. Wrongly building a
command just because the cluster is known would be exactly the error this
function is meant to avoid.
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
    """The value does not fit this command."""


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
        # float() happily parses "nan"/"inf"/"-inf". Letting that through
        # would later either crash `round()` with a `ValueError` (nan) or
        # silently bypass `kelvin_to_mireds`'s <=0 check (nan is never
        # <= 0) - either way, this is not a number that can carry a
        # command.
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


# Channel abbreviation from `LoxoneColourError.channel` (see `commands/color.py`)
# to the associated i18n key for the translated channel name.
_LOXONE_COLOUR_CHANNEL_KEYS: dict[str, str] = {
    "red": "api.errors.loxone_colour_channel_red",
    "green": "api.errors.loxone_colour_channel_green",
    "blue": "api.errors.loxone_colour_channel_blue",
}


def _translate_loxone_colour_error(exc: LoxoneColourError) -> str:
    """Builds a translated message from the fields of `LoxoneColourError`.

    `str(exc)` itself is hard-coded German (see the `LoxoneColourError`
    docstring in `color.py`) - here it is instead reassembled from
    `exc.kind` and the fields set depending on the case, via `i18n.t()`
    like every other error path in this module (pattern: `_as_number`
    above). The `assert`s only narrow for mypy what `kind` already
    determines - see the docstring of `LoxoneColourError` for which field
    belongs to which `kind`.
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

    Two conversions in a row, both backed by a source in `commands/color.py`:
    unpacking the Loxone encoding and converting the result to HSV.
    `loxone_rgb_to_rgb` raises `LoxoneColourError` for an impossible number
    - here that becomes `UnsupportedValueError`, so the caller answers with
    400 rather than 500, as for any other unsuitable value.

    The message for that does NOT come from `str(exc)` - that would be
    hard-coded German (see the `LoxoneColourError` docstring in `color.py`),
    while every other error path in this module goes through `i18n.t()`
    (review fix, 2026-09-07: until then `_payload_hue_saturation` passed
    the German `str(exc)` through unchanged as the HTTP-400 `detail`, even
    with English selected as the language). `_translate_loxone_colour_error`
    above rebuilds the same precision (which channel, which value) from the
    fields of `LoxoneColourError`, just translated.

    WARNING for anyone wiring the Loxone RGB block to this command
    (finding I-3, closing review 2026-09-08): the AQa output of that
    block carries color AND brightness in ONE number, but this
    function unpacks only the color from it - `MoveToHueAndSaturation` has
    no field for brightness, which runs exclusively through
    LevelControl (see `_payload_level` above). Worked out: AQa 100, 50,
    and 25 (red at 100%, 50%, 25% brightness in the Loxone block)
    all three yield `hue 0, sat 254` - identical commands. Dimming in
    the Loxone block therefore does NOTHING to the light. AQa 0 yields
    `hue 0, sat 0`, i.e. white instead of off - flat saturation 0 is white,
    not switching off. Not a programming error, but a consequence of the
    deliberate restriction to this one color command (design 2026-09-07,
    section 9.3) - but so far UNTESTED for lack of reachable hardware
    with a connected Loxone RGB block. Open point,
    see design section 10.
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
