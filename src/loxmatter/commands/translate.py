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

# `OptionsBitmap.kExecuteIfOff` from ColorControl (verified against installed SDK
# in chip.clusters.Objects.ColorControl.Bitmaps.OptionsBitmap).
#
# Without this bit, a color command has no effect on a SWITCHED-OFF lamp -
# measured on the checked-in KAJPLATS CWS on September 8, 2026: the value
# 60100060 (green at 60 %) turned it white and to 100 % brightness, the color
# never arrived. This is Matter specification, not a device error.
#
# The alternative would be to send the brightness level first. Then the
# lamp would visibly power on in the OLD color and then change - a
# flash that this bit avoids by having the color already set before
# the light comes on.
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
    """What a payload builder returns.

    Usually just the payload; the command is then in the
    table entry. A builder sets `command_id` only when the VALUE requires
    a different command in the same cluster than the entry - this
    case exists exactly once, see `_payload_hue_saturation`: the
    Loxone light control block sends color and white through the same
    output.

    A separate return type instead of a second table "value -> command"
    alongside `_PAYLOAD_BUILDERS`: the decision of WHICH command a value
    means, and the payload for it belong inseparably together. Two
    tables for this would diverge sooner or later, and that only
    shows up on a real device.
    """

    payload: dict[str, object]
    command_id: int | None = None
    # Percent if this value ALSO carries a brightness level. Loxone
    # encodes it in the same value as the color (in the magnitude of the RGB number or
    # in the BBB field of Lumitech), Matter manages it in a different cluster -
    # one Loxone call thereby becomes two Matter commands.
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

    # White instead of color: the same Loxone output carries both meanings,
    # distinguished by identifier 20 (see `color.is_lumitech` for the
    # evidence and why the value ranges cannot overlap
    # with each other). Before September 8, 2026, such a value fell through into the
    # RGB unpacking, failed on a channel over 100 percent, and came
    # back as 400 - the white controller in the Loxone app had no effect.
    # Only integer values are even a possibility - `is_lumitech`
    # expects an integer, and a fractional number is not supported in either
    # of the two encodings.
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


# The only place that defines which (cluster ID, command ID) pairs
# are served. The dispatch in `to_matter_calls` only
# reads this mapping - supporting another command is a data change
# here, not a new branch there, and the set of served pairs is
# complete at a glance.
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
    """The Matter calls for an exported command key.

    **A list, not a single call**, because a Loxone value can mean more than
    one thing: the color output of the light control carries
    color AND brightness in one number, Matter manages both in separate
    clusters (ColorControl and LevelControl). Until September 8, 2026, this
    function only returned the color part - the brightness controller in the
    Loxone app therefore had no effect.

    **The order is mandatory: color first, then level.**
    `MoveToLevelWithOnOff` switches on a switched-off lamp; if the
    level came first, it would visibly power on in the old color and then change.
    Conversely, the color change in the switched-off state
    goes unnoticed by anyone - provided it gets there at all, and that's exactly
    why the color payload carries `ExecuteIfOff` (see
    `_OPTIONS_EXECUTE_IF_OFF`). Measured on the lamp: starting from the
    OFF state, 36060036 brought it to 60% with hue 120.5 degrees
    and saturation 39.8% - color and brightness both. Without the bit it came
    up white.

    **A failure on the second call leaves a partial
    state** - the color is set, the brightness is not. That's the price
    for Loxone sending both in one value while Matter requires them separately;
    rolling back would be a second call that could fail the same way.
    The caller reports the failure (502) instead of swallowing it.

    The level goes via `MoveToLevelWithOnOff` (8/4), not via
    `MoveToLevel` (8/0): Loxone really means OFF with 0. With 8/0 the
    lamp would remain on at brightness 0. Both checked-in
    lamps support 8/4, and device types 268/268 require
    LevelControl anyway - a lamp without this cluster rejects the
    second call, and that shows up as 502 instead of silently failing.
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

    # At brightness 0 there is no color to set - and a color command
    # would be not only superfluous but visibly wrong here: Loxone
    # sends the value 0 to turn off, and in RGB encoding that is
    # saturation 0, so WHITE. The lamp turned white WHILE it
    # was still on, and only then turned off - a bright flash when
    # turning off (observed on the lamp, September 8, 2026; white uses
    # all LEDs, saturated red only the red ones, so the flash was even
    # brighter than the image before).
    #
    # The comparison uses the ROUNDED level, not the percentage: what
    # rounds to level 0 turns off anyway, and the color command before would be
    # the same flash.
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
            # The value can determine the command - see `_Built`.
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
