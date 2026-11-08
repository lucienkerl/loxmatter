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

"""Capabilities: what the export screen names instead of single signals
(design 2026-11-07, section 3).

A lamp exports `onoff`, `on`, `off`, `toggle`, `level`, `level_onoff`,
`color_x`, `color_y`, `colormode`, `lumitech`, ... - a dozen names for four
things a person recognises: on/off, brightness, colour temperature, colour.
This module folds signals and commands into those few things, by cluster
rather than by slug or title: a slug is technical and a title may have been
renamed by the user, while the cluster says what the value is about.

Anything outside the table keeps its own title, so nothing exported ever
disappears from the overview - it is only named less nicely.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from loxmatter.matter.models import SignalKind
from loxmatter.model.store import StoredCommand, StoredGroupCommand, StoredSignal
from loxmatter.profiles.light_commands import LUMITECH
from loxmatter.profiles.table import is_exportable

_COLOUR_CONTROL = 768
# Colour-control attributes that describe the colour temperature rather
# than the colour (`clusters.yaml`: colortemp_mireds and its physical
# limits).
_COLOUR_TEMPERATURE_ATTRIBUTES = frozenset({7, 16395, 16396})

# Cluster id -> capability. Matter ids; Zigbee devices arrive translated to
# the same clusters (`zigbee.translate`), plus the two Zigbee-only ids for
# battery (Power Configuration) and metering that have no Matter twin.
_BY_CLUSTER: dict[int, str] = {
    6: "onoff",
    8: "level",
    1: "battery",
    47: "battery",
    1026: "temperature",
    1029: "humidity",
    1030: "motion",
    69: "contact",
    1024: "illuminance",
    144: "power",
    2820: "power",
    145: "energy",
    1794: "energy",
    59: "button",
    1280: "alarm",
}

# The order chips appear in: what a person looks for first.
_ORDER = (
    "light",
    "onoff",
    "level",
    "colortemp",
    "color",
    "motion",
    "contact",
    "temperature",
    "humidity",
    "illuminance",
    "button",
    "power",
    "energy",
    "alarm",
    "battery",
)
_RANK = {key: rank for rank, key in enumerate(_ORDER)}


@dataclass(frozen=True)
class Capability:
    """One chip. `key` names a known capability the web UI translates;
    `None` means "not in the table", and `label` is then the signal's title
    or the command's slug, shown as it is."""

    key: str | None
    label: str


def _signal_capability(signal: StoredSignal) -> Capability:
    cluster = signal.ref.cluster_id
    if cluster == _COLOUR_CONTROL:
        key = "colortemp" if signal.ref.element_id in _COLOUR_TEMPERATURE_ATTRIBUTES else "color"
        return Capability(key, key)
    known = _BY_CLUSTER.get(cluster)
    return Capability(known, known) if known else Capability(None, signal.title)


def _command_capability(cluster_id: int, command_id: int, slug: str) -> Capability:
    if (cluster_id, command_id) == LUMITECH:
        # The lighting controller drives everything at once - on/off,
        # brightness and colour in one Loxone object.
        return Capability("light", "light")
    if cluster_id == _COLOUR_CONTROL:
        key = "colortemp" if slug.startswith("colortemp") else "color"
        return Capability(key, key)
    known = _BY_CLUSTER.get(cluster_id)
    return Capability(known, known) if known else Capability(None, slug)


def _folded(capabilities: Iterable[Capability]) -> list[Capability]:
    """Each capability once, known ones in `_ORDER`, unknown ones after
    them in the order they came."""
    seen: dict[tuple[str | None, str], Capability] = {}
    for capability in capabilities:
        seen.setdefault((capability.key, capability.label), capability)
    return sorted(
        seen.values(),
        key=lambda c: _RANK.get(c.key, len(_ORDER)) if c.key is not None else len(_ORDER),
    )


def input_capabilities(signals: Sequence[StoredSignal]) -> list[Capability]:
    """What a device's inputs carry to Loxone - the same signals `to_inputs`
    writes (exported, and an event or a value Loxone can hold), folded. The
    online signal every device has is left out: it says nothing about the
    device."""
    return _folded(
        _signal_capability(s)
        for s in signals
        if s.exported and (s.ref.kind is SignalKind.EVENT or is_exportable(s.exportability))
    )


def output_capabilities(
    commands: Sequence[StoredCommand] | Sequence[StoredGroupCommand],
) -> list[Capability]:
    """What Loxone can tell a device or a group to do - the same commands
    `to_outputs`/`to_group_outputs` write (exported ones), folded."""
    return _folded(
        _command_capability(c.cluster_id, c.command_id, c.slug) for c in commands if c.exported
    )
