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

"""Vergleicht die gewuenschten Ein-/Ausgaenge (`export.signals.to_inputs`/
`export.outputs.to_outputs` - dieselbe Quelle wie der bestehende Vorlagen-
Export) gegen einen `ProjectIndex` und baut den Diff-Plan (Entwurf Abschnitt
5)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from loxmatter.export.documents import LoxoneCommand
from loxmatter.export.outputs import to_outputs
from loxmatter.export.signals import LoxoneInput, to_inputs
from loxmatter.model.store import StoredCommand, StoredDevice, StoredSignal
from loxmatter.projectsync.index import ProjectIndex
from loxmatter.projectsync.schema import (
    MANAGED_INPUT_CMD_ATTRS,
    MANAGED_OUTPUT_CMD_ATTRS,
    desired_input_cmd_attrs,
    desired_output_cmd_attrs,
)

__all__ = ["PlanEntry", "PlanStatus", "SyncPlan", "build_plan"]

_REQUIRED_INPUT_ATTRS = ("Title", "Check", "Analog")
_REQUIRED_OUTPUT_ATTRS = ("Title", "CmdOn")


class PlanStatus(StrEnum):
    UNCHANGED = "unchanged"
    UPDATED = "updated"
    NEW_SIGNAL = "new_signal"
    NEW_DEVICE = "new_device"
    ORPHANED = "orphaned"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class PlanEntry:
    kind: str  # "input" | "output"
    device_id: int
    device_label: str
    key: str
    title: str
    status: PlanStatus
    # attrname -> (alter Wert, neuer Wert) - nur bei UPDATED nicht leer.
    changes: dict[str, tuple[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class SyncPlan:
    entries: list[PlanEntry]

    @property
    def has_changes(self) -> bool:
        return any(
            entry.status in (PlanStatus.UPDATED, PlanStatus.NEW_SIGNAL, PlanStatus.NEW_DEVICE)
            for entry in self.entries
        )


def _diff_managed_attrs(
    existing: dict[str, str], desired: dict[str, str], managed: Sequence[str]
) -> dict[str, tuple[str, str]]:
    changes: dict[str, tuple[str, str]] = {}
    for name in managed:
        if name not in desired:
            continue
        old = existing.get(name, "")
        new = desired[name]
        if old != new:
            changes[name] = (old, new)
    return changes


def _has_required_attrs(attrs: dict[str, str], required: Sequence[str]) -> bool:
    return all(name in attrs for name in required)


def _plan_inputs(
    index: ProjectIndex, device: StoredDevice, entries: Sequence[LoxoneInput]
) -> list[PlanEntry]:
    prefix = f"d{device.id}_"
    has_existing_container = any(key.startswith(prefix) for key in index.input_containers)
    plan_entries: list[PlanEntry] = []
    for entry in entries:
        existing = index.input_cmds.get(entry.key)
        if existing is None:
            status = PlanStatus.NEW_SIGNAL if has_existing_container else PlanStatus.NEW_DEVICE
            plan_entries.append(
                PlanEntry("input", device.id, device.label, entry.key, entry.title, status)
            )
            continue
        if not _has_required_attrs(existing.attrs, _REQUIRED_INPUT_ATTRS):
            plan_entries.append(
                PlanEntry(
                    "input", device.id, device.label, entry.key, entry.title, PlanStatus.CONFLICT
                )
            )
            continue
        desired = desired_input_cmd_attrs(entry)
        changes = _diff_managed_attrs(existing.attrs, desired, MANAGED_INPUT_CMD_ATTRS)
        status = PlanStatus.UPDATED if changes else PlanStatus.UNCHANGED
        plan_entries.append(
            PlanEntry("input", device.id, device.label, entry.key, entry.title, status, changes)
        )
    return plan_entries


def _plan_outputs(
    index: ProjectIndex, device: StoredDevice, commands: Sequence[LoxoneCommand]
) -> list[PlanEntry]:
    prefix = f"d{device.id}_"
    has_existing_container = any(key.startswith(prefix) for key in index.output_containers)
    plan_entries: list[PlanEntry] = []
    for command in commands:
        existing = index.output_cmds.get(command.key)
        if existing is None:
            status = PlanStatus.NEW_SIGNAL if has_existing_container else PlanStatus.NEW_DEVICE
            plan_entries.append(
                PlanEntry("output", device.id, device.label, command.key, command.title, status)
            )
            continue
        if not _has_required_attrs(existing.attrs, _REQUIRED_OUTPUT_ATTRS):
            plan_entries.append(
                PlanEntry(
                    "output",
                    device.id,
                    device.label,
                    command.key,
                    command.title,
                    PlanStatus.CONFLICT,
                )
            )
            continue
        desired = desired_output_cmd_attrs(command)
        changes = _diff_managed_attrs(existing.attrs, desired, MANAGED_OUTPUT_CMD_ATTRS)
        status = PlanStatus.UPDATED if changes else PlanStatus.UNCHANGED
        plan_entries.append(
            PlanEntry(
                "output", device.id, device.label, command.key, command.title, status, changes
            )
        )
    return plan_entries


def _orphaned_entries(
    index: ProjectIndex, known_input_keys: set[str], known_output_keys: set[str]
) -> list[PlanEntry]:
    orphaned: list[PlanEntry] = []
    for key, element in index.input_cmds.items():
        if key not in known_input_keys and key.split("_", 1)[0].startswith("d"):
            orphaned.append(
                PlanEntry(
                    "input", -1, "", key, element.attrs.get("Title", key), PlanStatus.ORPHANED
                )
            )
    for key, element in index.output_cmds.items():
        if key not in known_output_keys and key.split("_", 1)[0].startswith("d"):
            orphaned.append(
                PlanEntry(
                    "output", -1, "", key, element.attrs.get("Title", key), PlanStatus.ORPHANED
                )
            )
    return orphaned


def build_plan(
    index: ProjectIndex,
    devices: Sequence[StoredDevice],
    signals_by_device: dict[int, Sequence[StoredSignal]],
    commands_by_device: dict[int, Sequence[StoredCommand]],
) -> SyncPlan:
    entries: list[PlanEntry] = []
    known_input_keys: set[str] = set()
    known_output_keys: set[str] = set()

    for device in devices:
        inputs = to_inputs(signals_by_device.get(device.id, []), device.id, device.label)
        outputs = to_outputs(commands_by_device.get(device.id, []))
        known_input_keys.update(entry.key for entry in inputs)
        known_output_keys.update(command.key for command in outputs)
        entries += _plan_inputs(index, device, inputs)
        entries += _plan_outputs(index, device, outputs)

    entries += _orphaned_entries(index, known_input_keys, known_output_keys)
    return SyncPlan(entries)
