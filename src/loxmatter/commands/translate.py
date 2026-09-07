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
made-up payload to a real device is worse than a clear error. That holds
not only for a completely unknown cluster, but also for a known cluster
with an unknown command ID in it: the dispatch keys on the pair
(cluster ID, command ID), never on the cluster ID alone - see
`test_known_cluster_with_unknown_command_raises`
(cluster 768/ColorControl, command 6),
`test_onoff_cluster_with_unknown_command_raises` (cluster 6) and
`test_level_cluster_with_unknown_command_raises` (cluster 8) in
`tests/commands/test_translate.py`. Cluster 768 command 6 (hue/saturation)
is deliberately not served because the Loxone-side RGB number is not
reliably documented (see `color.py`); clusters 6 and 8 simply have no
further commands here beyond Off/On/Toggle and MoveToLevel(WithOnOff)
respectively - which matters particularly for the raw export (`raw`),
which also passes through commands with no entry in `clusters.yaml`, such
as LevelControl Move/Step/Stop. Wrongly building a command just because
the cluster is known would be exactly the mistake this function is meant
to avoid.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from loxmatter import i18n
from loxmatter.commands.color import kelvin_to_mireds
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


# The single place that defines which (cluster ID, command ID) pairs are
# served. The dispatch in `to_matter_call` only ever reads this mapping -
# supporting another command is a data change here, not a new branch
# there, and the set of served pairs is complete at a glance.
_PAYLOAD_BUILDERS: dict[tuple[int, int], Callable[[str], dict[str, object]]] = {
    (_CLUSTER_ONOFF, _COMMAND_OFF): _payload_none,
    (_CLUSTER_ONOFF, _COMMAND_ON): _payload_none,
    (_CLUSTER_ONOFF, _COMMAND_TOGGLE): _payload_none,
    (_CLUSTER_LEVEL, _COMMAND_MOVE_TO_LEVEL): _payload_level,
    (_CLUSTER_LEVEL, _COMMAND_MOVE_TO_LEVEL_WITH_ON_OFF): _payload_level,
    (_CLUSTER_COLOR, _COMMAND_COLOR_TEMPERATURE): _payload_color_temperature,
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
