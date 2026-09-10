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

"""Applies a `SyncPlan` as targeted text replacement onto the original byte
stream (design section 3.2) - never through an XML serialiser.

Every change is an `_Edit(start, end, replacement)`: `end == start` means a
pure insertion. All edits are collected, sorted DESCENDING by `start`, and
applied back to front - that way earlier positions stay valid without
having to recompute an offset.

`apply_plan` calls `to_inputs`/`to_outputs` itself, exactly like
`diff.build_plan` - the same source for both, so plan and patch can never
drift apart. The reason for not passing this through the `PlanEntry`: it
only carries what the interface needs to show (title/key/status), not the
`unit_format`/`check_suffix`/`off_path` that a newly created object
additionally needs.

The one change `apply_plan` makes outside the plan: the output ALWAYS
carries a BOM (`export.xml.BOM`, the same constant as the template files)
- added if the original had none, otherwise carried over unchanged.
Anyone checking byte identity against the input must account for that."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from loxmatter.export.documents import LoxoneCommand
from loxmatter.export.outputs import to_group_outputs, to_outputs
from loxmatter.export.signals import LoxoneInput, to_inputs
from loxmatter.export.xml import BOM, escape_attr_value
from loxmatter.model.store import (
    StoredCommand,
    StoredDevice,
    StoredGroup,
    StoredGroupCommand,
    StoredSignal,
)
from loxmatter.projectsync.diff import PlanEntry, PlanStatus, SyncPlan
from loxmatter.projectsync.ids import new_iname, new_unique_id
from loxmatter.projectsync.index import ProjectIndex
from loxmatter.projectsync.schema import (
    find_any_iodata_attrs,
    new_caption_open_tag,
    new_cmd_children_xml,
    new_input_cmd_open_tag,
    new_input_container_open_tag,
    new_output_cmd_open_tag,
    new_output_container_open_tag,
    sibling_iodata_attrs,
)

__all__ = ["MissingCaptionError", "apply_plan"]


class MissingCaptionError(ValueError):
    """Historical: used to be thrown when the project file did not (yet)
    have a `VirtualInCaption`/`VirtualOutCaption` section into which a
    completely new device could be inserted. `_new_device_edit` now
    creates this section itself (design section 8: the special case of
    creating one, also behind the experimental flag) - this error is
    therefore no longer triggered in the current codebase. The class
    remains exported and `sync.run_sync` still catches it: as a line of
    defence in case a later caller ever calls `_new_device_edit` under
    conditions where the automatic creation, for whatever reason, does not
    kick in."""


@dataclass(frozen=True)
class _Edit:
    start: int
    end: int
    replacement: str


def _attr_span(text: str, tag_start: int, tag_end: int, name: str) -> tuple[int, int] | None:
    """Byte range of `name="value"` inside a start tag, or `None` if the
    attribute does not occur there.

    The lookbehind `(?<![A-Za-z0-9_])` is not a detail: without it,
    `re.search` for `Title` would also find the second half of a longer
    attribute name (`XTitle="..."`) - and because `search` returns the
    FIRST match in the tag, such an attribute would be silently
    overwritten instead of the one actually meant. An attribute name
    always starts after whitespace or directly after `<C`, never in the
    middle of an identifier."""
    pattern = re.compile(rf'(?<![A-Za-z0-9_]){re.escape(name)}="(?:[^"&]|&[^;]+;)*"')
    match = pattern.search(text, tag_start, tag_end)
    return None if match is None else (match.start(), match.end())


def _display_format(obj: LoxoneInput | LoxoneCommand, is_input: bool) -> tuple[bool, str]:
    """`(analog, unit_format)` for the `<Display>` child that
    `schema.new_cmd_children_xml` writes.

    Only an input carries a unit there (correction after the user report
    of 2026-09-05, see its docstring); for an output the defaults stand,
    so its `<Display>` looks the same as before."""
    if not is_input:
        return False, ""
    entry = cast(LoxoneInput, obj)
    return entry.analog, entry.unit_format


def _update_edits(index: ProjectIndex, entry: PlanEntry) -> list[_Edit]:
    element = (index.input_cmds if entry.kind == "input" else index.output_cmds)[entry.key]
    edits: list[_Edit] = []
    for name, (_, new_value) in entry.changes.items():
        span = _attr_span(index.text, element.open_start, element.open_end, name)
        replacement = f'{name}="{escape_attr_value(new_value)}"'
        if span is None:
            # Attribute is entirely missing from the existing tag (e.g.
            # `CmdOff` on an output without an off command) - insert before
            # the closing '>'.
            insert_at = element.open_end - (2 if element.self_closing else 1)
            edits.append(_Edit(insert_at, insert_at, f" {replacement}"))
        else:
            edits.append(_Edit(span[0], span[1], replacement))
    return edits


def _new_signal_edit(
    index: ProjectIndex,
    entry: PlanEntry,
    entries_by_key: Mapping[str, LoxoneInput] | Mapping[str, LoxoneCommand],
) -> _Edit:
    is_input = entry.kind == "input"
    container = index.input_containers if is_input else index.output_containers
    # A group has outputs only, so `is_input` and `entry.owner_kind ==
    # "group"` never combine - but an output entry needs the same
    # "g"-vs-"d" distinction `diff._plan_outputs` uses, or a NEW_SIGNAL
    # for an already-synced group (id N) would look for a "dN_" container
    # that does not exist, while the real one sits under "gN_".
    prefix = f"{'g' if entry.owner_kind == 'group' else 'd'}{entry.device_id}_"
    matching_container = next(
        (element for key, element in container.items() if key.startswith(prefix)), None
    )
    assert matching_container is not None and matching_container.inner_end is not None

    iname_prefix = "VCI" if is_input else "VQC"
    iname = new_iname(iname_prefix, index.all_inames)
    u = new_unique_id(index.all_u_values)
    iodata = sibling_iodata_attrs(index.text, next(iter(matching_container.children)))

    obj = entries_by_key[entry.key]
    open_tag = (
        new_input_cmd_open_tag(cast(LoxoneInput, obj), iname, u)
        if is_input
        else new_output_cmd_open_tag(cast(LoxoneCommand, obj), iname, u)
    )
    analog, unit_format = _display_format(obj, is_input)
    children_xml = new_cmd_children_xml(
        kind="input" if is_input else "output",
        existing_u=index.all_u_values,
        iodata_attrs=iodata,
        analog=analog,
        unit_format=unit_format,
    )
    full_xml = f"{open_tag}{children_xml}</C>"
    pos = matching_container.inner_end
    return _Edit(pos, pos, full_xml)


def _new_device_edit(
    index: ProjectIndex,
    entries: Sequence[PlanEntry],
    entries_by_key: Mapping[str, LoxoneInput] | Mapping[str, LoxoneCommand],
    bridge_ip: str,
    port: int,
    listen: int,
) -> tuple[_Edit, int]:
    """ONE new device container for ALL `NEW_DEVICE` entries of a device of
    the same kind (`entries` is the group for one `(kind, device_id)`).
    Returns the edit AND the number of newly created `<C>` objects (for
    the `NextObj` counter in `apply_plan`).

    Deliberately a group rather than a single entry: `export.signals.
    to_inputs` always additionally produces an online signal per device,
    so a genuinely new device practically never has just one entry. A
    container per entry would produce several same-named `VirtualUdpIn`
    devices with identical address and port, each with exactly one
    command in it - structurally wrong, not just unattractive.

    If the matching `VirtualInCaption`/`VirtualOutCaption` section is
    missing entirely (a Miniserver that has never had a virtual input or
    output of this kind before), this function creates it itself - as an
    additional child object directly before the closing tag of the
    selected `LoxLIVE` block (`index.target_loxlive`, design section 8:
    the special case of creating one, also behind the experimental flag
    that `apply_plan` already guards via `include_new_devices`).
    Deliberately NOT at the level of the `<ControlList>` root element (an
    earlier bug, found against a real reference file: `VirtualInCaption`/
    `VirtualOutCaption` never hang there directly, but always under
    exactly the `LoxLIVE` block of their Miniserver), and deliberately at
    the END of the `LoxLIVE` content, not at some specific point in
    between: among the many possible sibling object types
    (`InputCaption`, `OutputCaption`, `WeatherCaption`, ...) this project
    knows no "correct" order - append instead of guessing, the same
    principle as everywhere else in this module."""
    first = entries[0]
    is_input = first.kind == "input"
    kind = "input" if is_input else "output"
    caption = index.virtual_in_caption if is_input else index.virtual_out_caption
    caption_exists = caption is not None and caption.inner_end is not None

    container_iname_prefix = "VUI" if is_input else "VQ"
    container_iname = new_iname(container_iname_prefix, index.all_inames)
    container_u = new_unique_id(index.all_u_values)
    if is_input:
        container_open = new_input_container_open_tag(
            first.device_label, bridge_ip, port, container_iname, container_u
        )
    else:
        container_open = new_output_container_open_tag(
            first.device_label,
            f"http://{bridge_ip}:{listen}",
            container_iname,
            container_u,
            is_group=first.owner_kind == "group",
        )

    cmd_iname_prefix = "VCI" if is_input else "VQC"
    # `caption` is `None` when the caption is missing -
    # `find_any_iodata_attrs` already treats that as "no template found"
    # and returns `None` instead of raising.
    iodata = find_any_iodata_attrs(index.text, caption)
    cmds: list[str] = []
    for entry in entries:
        cmd_iname = new_iname(cmd_iname_prefix, index.all_inames)
        cmd_u = new_unique_id(index.all_u_values)
        obj = entries_by_key[entry.key]
        cmd_open = (
            new_input_cmd_open_tag(cast(LoxoneInput, obj), cmd_iname, cmd_u)
            if is_input
            else new_output_cmd_open_tag(cast(LoxoneCommand, obj), cmd_iname, cmd_u)
        )
        analog, unit_format = _display_format(obj, is_input)
        children_xml = new_cmd_children_xml(
            kind=kind,
            existing_u=index.all_u_values,
            iodata_attrs=iodata,
            analog=analog,
            unit_format=unit_format,
        )
        cmds.append(f"{cmd_open}{children_xml}</C>")

    device_xml = f"{container_open}{''.join(cmds)}</C>"
    created_count = 1 + len(entries)  # Container + one cmd each.

    if caption_exists:
        assert caption is not None and caption.inner_end is not None  # for mypy
        return _Edit(caption.inner_end, caption.inner_end, device_xml), created_count

    caption_u = new_unique_id(index.all_u_values)
    caption_open = new_caption_open_tag(kind, caption_u)
    full_xml = f"{caption_open}{device_xml}</C>"
    # `target_loxlive.inner_end` is already validated as not `None` (see
    # `build_index`'s check directly after `_resolve_target_loxlive`).
    assert index.target_loxlive.inner_end is not None  # for mypy
    pos = index.target_loxlive.inner_end
    return _Edit(pos, pos, full_xml), created_count + 1  # + the new caption itself.


def _next_obj_edit(index: ProjectIndex, created_count: int) -> _Edit | None:
    if created_count == 0 or "NextObj" not in index.root_attrs:
        return None
    span = _attr_span(index.text, 0, index.root_open_end, "NextObj")
    if span is None:
        return None
    try:
        current = int(index.root_attrs["NextObj"])
    except ValueError:
        # Per design section 6/10, `NextObj` is only an unverified,
        # conservative best effort anyway - not documented behaviour. A
        # value that cannot be read as a decimal number is therefore no
        # reason to fail the whole (otherwise valid) patch: the actual
        # signal/device edits remain untouched, only this one attribute is
        # left alone.
        return None
    new_value = str(current + created_count)
    return _Edit(span[0], span[1], f'NextObj="{new_value}"')


def _apply_edits(text: str, edits: list[_Edit]) -> str:
    for edit in sorted(edits, key=lambda e: e.start, reverse=True):
        text = text[: edit.start] + edit.replacement + text[edit.end :]
    return text


def apply_plan(
    index: ProjectIndex,
    plan: SyncPlan,
    devices: Sequence[StoredDevice],
    signals_by_device: dict[int, Sequence[StoredSignal]],
    commands_by_device: dict[int, Sequence[StoredCommand]],
    groups: Sequence[StoredGroup] = (),
    commands_by_group: dict[int, Sequence[StoredGroupCommand]] | None = None,
    *,
    include_new_devices: bool,
    bridge_ip: str,
    port: int,
    listen: int,
) -> bytes:
    """Builds the patched file for one of the two download variants (design
    section 3.4/7): `include_new_devices=False` produces only updates and
    new signals in already-existing device containers, `True` also
    produces completely new device containers."""
    desired_inputs: dict[str, LoxoneInput] = {}
    desired_outputs: dict[str, LoxoneCommand] = {}
    for device in devices:
        for input_item in to_inputs(signals_by_device.get(device.id, []), device.id, device.label):
            desired_inputs[input_item.key] = input_item
        for output_item in to_outputs(commands_by_device.get(device.id, [])):
            desired_outputs[output_item.key] = output_item

    for group in groups:
        for output_item in to_group_outputs((commands_by_group or {}).get(group.id, [])):
            desired_outputs[output_item.key] = output_item

    edits: list[_Edit] = []
    created_count = 0
    # (kind, owner_kind, id) - NOT (kind, id): both counters start at 1,
    # so group 1 and device 1 would otherwise share one container. `dict`
    # preserves the plan's order, so the generated file is reproducible.
    new_device_groups: dict[tuple[str, str, int], list[PlanEntry]] = {}
    for entry in plan.entries:
        if entry.status is PlanStatus.UPDATED:
            edits += _update_edits(index, entry)
        elif entry.status is PlanStatus.NEW_SIGNAL:
            source = desired_inputs if entry.kind == "input" else desired_outputs
            edits.append(_new_signal_edit(index, entry, source))
            created_count += 1
        elif entry.status is PlanStatus.NEW_DEVICE and include_new_devices:
            new_device_groups.setdefault(
                (entry.kind, entry.owner_kind, entry.device_id), []
            ).append(entry)

    for (kind, _owner_kind, _owner_id), group_entries in new_device_groups.items():
        source = desired_inputs if kind == "input" else desired_outputs
        # The number of new <C> objects comes back from `_new_device_edit`
        # itself: container + one cmd per entry, plus the newly created
        # caption if any.
        edit, group_created_count = _new_device_edit(
            index, group_entries, source, bridge_ip, port, listen
        )
        edits.append(edit)
        created_count += group_created_count

    next_obj_edit = _next_obj_edit(index, created_count)
    if next_obj_edit is not None:
        edits.append(next_obj_edit)

    patched_text = _apply_edits(index.text, edits)
    if not patched_text.startswith(BOM):
        patched_text = BOM + patched_text
    return patched_text.encode("utf-8")
