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
    kelvin_to_mireds,
    loxone_rgb_to_rgb,
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


def _payload_none(_value: str) -> dict[str, object]:
    return {}


def _payload_level(value: str) -> dict[str, object]:
    return {"level": _level(value), "transitionTime": 0}


def _payload_color_temperature(value: str) -> dict[str, object]:
    return {"colorTemperatureMireds": kelvin_to_mireds(_as_number(value))}


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


def _payload_hue_saturation(value: str) -> dict[str, object]:
    """Packed Loxone color number -> Matter hue/saturation.

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
    try:
        red, green, blue = loxone_rgb_to_rgb(_as_number(value))
    except LoxoneColourError as exc:
        raise UnsupportedValueError(_translate_loxone_colour_error(exc)) from exc
    hue, saturation = rgb_to_hue_saturation(red, green, blue)
    return {"hue": hue, "saturation": saturation, "transitionTime": 0}


# The only place that determines which (cluster ID, command ID) pairs
# are supported. The dispatch in `to_matter_call` only reads this mapping
# out - supporting a further command is a data change here, not a new
# branch there, and the set of supported pairs is complete at a
# glance.
_PAYLOAD_BUILDERS: dict[tuple[int, int], Callable[[str], dict[str, object]]] = {
    (_CLUSTER_ONOFF, _COMMAND_OFF): _payload_none,
    (_CLUSTER_ONOFF, _COMMAND_ON): _payload_none,
    (_CLUSTER_ONOFF, _COMMAND_TOGGLE): _payload_none,
    (_CLUSTER_LEVEL, _COMMAND_MOVE_TO_LEVEL): _payload_level,
    (_CLUSTER_LEVEL, _COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF): _payload_level,
    (_CLUSTER_COLOR, _COMMAND_COLOR_TEMPERATURE): _payload_color_temperature,
    (_CLUSTER_COLOR, _COMMAND_HUE_SATURATION): _payload_hue_saturation,
}


def to_matter_call(command: StoredCommand, value: str) -> MatterCall:
    """Builds the Matter call for an exported command key."""

    build_payload = _PAYLOAD_BUILDERS.get((command.cluster_id, command.command_id))
    if build_payload is None:
        raise UnsupportedValueError(
            i18n.t(
                "api.errors.command_unsupported",
                cluster_id=command.cluster_id,
                command_id=command.command_id,
            )
        )

    return MatterCall(
        node_id=command.node_id,
        endpoint=command.endpoint,
        cluster_id=command.cluster_id,
        command_id=command.command_id,
        payload=build_payload(value),
    )
