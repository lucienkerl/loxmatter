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

"""How much of a device goes to Loxone, given its groups (design
2026-11-07, section 2).

A lamp in a group is switched through the group; exporting it on its own
as well gave Loxone two objects for the same light. So a group member is
exported according to its mode - only through the group (the default),
with its status values only, or in full - and a device in no group is
always exported in full.

One function for every export path: the template ZIP and the project-file
sync both cut a device's lists down here, so a member left out of the one
cannot turn up in the other.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Final, Literal, get_args

from loxmatter.model.store import StoredCommand, StoredDevice, StoredSignal

ExportMode = Literal["group", "status", "full"]
EXPORT_MODES: Final = frozenset(get_args(ExportMode))
DEFAULT_MEMBER_MODE: Final[ExportMode] = "group"


def export_mode(device: StoredDevice, member_ids: Collection[int]) -> ExportMode:
    """The mode that applies to `device` now.

    Outside a group a stored mode is ignored rather than cleared: it comes
    back into force when the device joins a group again, which is what the
    user last chose for it. An unknown stored value (a newer schema after a
    rollback) falls back to the default instead of failing the export."""
    if device.id not in member_ids:
        return "full"
    stored = device.group_export
    if stored in EXPORT_MODES:
        return stored  # type: ignore[return-value]
    return DEFAULT_MEMBER_MODE


def scoped(
    mode: ExportMode,
    signals: Sequence[StoredSignal],
    commands: Sequence[StoredCommand],
) -> tuple[Sequence[StoredSignal], Sequence[StoredCommand]]:
    """A device's signals and commands, cut down to what its mode exports."""
    if mode == "group":
        return [], []
    if mode == "status":
        return signals, []
    return signals, commands


def exports_on_its_own(mode: ExportMode) -> bool:
    """Whether the device gets Loxone objects of its own at all."""
    return mode != "group"
