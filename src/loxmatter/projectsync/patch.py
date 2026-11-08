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

Two changes `apply_plan` makes outside the plan: the output ALWAYS
carries a BOM (`export.xml.BOM`, the same constant as the template files)
- added if the original had none, otherwise carried over unchanged.
Anyone checking byte identity against the input must account for that.
And when the plan changes anything and a `saved_at` is given, the
document's "last saved" stamp (`Date`/`DateS`, see
`projectsync.savedate`) moves to that moment - the file was changed
then, even if Loxone Config did not do it."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
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
from loxmatter.projectsync.reconcile import owner_cmds, ref_blocks
from loxmatter.projectsync.rooms import RoomStatus, room_key
from loxmatter.projectsync.savedate import saved_date_attrs
from loxmatter.projectsync.scan import Element
from loxmatter.projectsync.schema import (
    find_any_iodata_attrs,
    new_caption_open_tag,
    new_cmd_children_xml,
    new_input_cmd_open_tag,
    new_input_container_open_tag,
    new_output_cmd_open_tag,
    new_output_container_open_tag,
    new_place_tag,
    new_right_group_tag,
    sibling_iodata_attrs,
)

__all__ = ["apply_plan"]


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


def _room_edits(index: ProjectIndex, plan: SyncPlan) -> tuple[dict[str, str], list[_Edit], int]:
    """Where every room of the plan lives in the patched file (design
    2026-09-29, section 6): returns `room_key` -> `Place.U` for every room
    a new object can point at, the insertions for rooms this sync creates,
    and the number of objects created (for `NextObj`).

    All new rooms go in ONE insertion at the end of the `PlaceCaption`,
    and all new rights groups in one at the end of their caption: two
    insertions at the same position would come out in reverse order
    (`_apply_edits` writes back to front)."""
    place_us: dict[str, str] = {
        room_key(a.name): a.place_u
        for a in plan.rooms
        if a.status in (RoomStatus.FOUND, RoomStatus.AMBIGUOUS) and a.place_u
    }
    to_create = [a for a in plan.rooms if a.status is RoomStatus.CREATED]
    if not to_create:
        return place_us, [], 0

    caption = index.place_caption
    # `rooms.can_create_rooms` is why an assignment is CREATED at all.
    assert caption is not None and caption.inner_end is not None
    rights = index.right_group_caption
    rights_end = None if rights is None else rights.inner_end
    # The default room (`First="true"`) carries its own `WF` and marks
    # itself as the default room - never a template for an ordinary one.
    template = next((p.attrs for p in index.places if p.attrs.get("First") != "true"), None)

    places_xml: list[str] = []
    rights_xml: list[str] = []
    for assignment in to_create:
        title = assignment.loxone_title or assignment.name.strip()
        place_u = new_unique_id(index.all_u_values)
        rights_u = None if rights_end is None else new_unique_id(index.all_u_values)
        places_xml.append(new_place_tag(title, place_u, rights_u, template))
        if rights_u is not None:
            rights_xml.append(new_right_group_tag(title, rights_u))
        place_us[room_key(assignment.name)] = place_u

    edits = [_Edit(caption.inner_end, caption.inner_end, "".join(places_xml))]
    if rights_end is not None and rights_xml:
        edits.append(_Edit(rights_end, rights_end, "".join(rights_xml)))
    return place_us, edits, len(places_xml) + len(rights_xml)


def _place_u_for(place_us: Mapping[str, str], entry: PlanEntry) -> str | None:
    return None if entry.room is None else place_us.get(room_key(entry.room))


def _container_at(index: ProjectIndex, entry: PlanEntry) -> Element:
    containers = index.input_containers if entry.kind == "input" else index.output_containers
    return next(c for c in containers.values() if c.open_start == entry.container_at)


def _update_edits(index: ProjectIndex, entry: PlanEntry) -> list[_Edit]:
    if entry.container_at is not None:
        element = _container_at(index, entry)
    else:
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
    place_u: str | None,
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
        place_u=place_u,
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
    place_u: str | None,
) -> tuple[_Edit, int]:
    """ONE new container for ALL `NEW_DEVICE` entries that share one
    `(kind, owner_kind, device_id)` - the same grouping key
    `apply_plan` uses when it builds `entries` (`device_id` is a device id
    when `owner_kind == "device"`, a GROUP id when `owner_kind ==
    "group"`; the two counters both start at 1, so the id alone would not
    distinguish them). Note "group" is overloaded in this module: here in
    the prose it means "the set of `PlanEntry` objects sharing that key",
    not a device group - `entries[0].owner_kind` is what tells this
    function whether it is building a device's container or a device
    GROUP's. Returns the edit AND the number of newly created `<C>`
    objects (for the `NextObj` counter in `apply_plan`).

    Deliberately entries sharing a key rather than a single entry each:
    for a device, `export.signals.to_inputs` always additionally produces
    an online signal per device, so a genuinely new device practically
    never has just one entry; for a device group (which has no inputs and
    no online signal, design 2), the same batching still applies because
    a group can offer several commands at once. A container per entry
    would produce several same-named `VirtualUdpIn`/`VirtualOut`
    containers with identical address and port, each with exactly one
    command in it - structurally wrong, not just unattractive.

    If the matching `VirtualInCaption`/`VirtualOutCaption` section is
    missing entirely (a Miniserver that has never had a virtual input or
    output of this kind before), this function creates it itself - as an
    additional child object directly before the closing tag of the
    selected `LoxLIVE` block (`index.target_loxlive`, design section 8:
    the special case of creating one).
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
            place_u=place_u,
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


def _saved_date_edits(index: ProjectIndex, saved_at: datetime) -> list[_Edit]:
    """Replaces `Date` and `DateS` on the document where they exist.

    A missing attribute is not added: every file Loxone Config writes has
    both, so a file without them came from somewhere else, and inventing a
    field it never had is not this module's business."""
    document = index.document
    if document is None:
        return []
    edits: list[_Edit] = []
    for name, value in saved_date_attrs(saved_at).items():
        span = _attr_span(index.text, document.open_start, document.open_end, name)
        if span is not None:
            edits.append(_Edit(span[0], span[1], f'{name}="{value}"'))
    return edits


# --- Two-way sync (design 2026-11-08) ---------------------------------------

_IODATA_TAG = re.compile(r"<IoData\b[^>]*/>")
_CO_U = re.compile(r'<Co\b[^>]*?\bU="([^"]*)"')
# A connector with incoming links: `<Co ...>` + `<In .../>` children +
# `</Co>`. Loxone Config writes `Nc` as the number of `In` children and
# leaves it out, self-closing the `Co`, when there is none (measured on
# the maintainer's file: 0 exceptions among 2,239 connectors).
_CO_WITH_INPUTS = re.compile(r"<Co\b([^>]*?)(?<!/)>((?:\s*<In\b[^>]*/>)+)\s*</Co>")
_IN_TAG = re.compile(r"<In\b[^>]*/>")
_INPUT_ATTR = re.compile(r'\bInput="([^"]*)"')
_NC_ATTR = re.compile(r'\s+Nc="[^"]*"')
_NC_VALUE = re.compile(r'\bNc="[^"]*"')


def _input_of(tag: str) -> str | None:
    match = _INPUT_ATTR.search(tag)
    return None if match is None else match.group(1)


def _line_span(text: str, element: Element) -> tuple[int, int]:
    """The element's span, widened to its whole line when it stands on one
    of its own, so a deletion leaves no blank, indented line behind."""
    start, end = element.open_start, element.outer_end
    line_start = start
    while line_start > 0 and text[line_start - 1] in " \t":
        line_start -= 1
    if line_start == 0 or text[line_start - 1] == "\n":
        start = line_start
        if text.startswith("\r\n", end):
            end += 2
        elif text.startswith("\n", end):
            end += 1
    return start, end


def _inside(position: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def _deletion_edits(
    index: ProjectIndex,
    plan: SyncPlan,
    deletions: Collection[str],
    keep_containers: Collection[str],
) -> tuple[list[_Edit], list[tuple[int, int]]]:
    """Removes the orphaned objects of every owner prefix in `deletions`
    (`d8`), the program-page blocks that wire them, and every link that
    pointed at a removed connector. Returns the edits and the removed
    spans, so other edits can stay clear of them.

    A container whose every command goes is removed whole - unless its
    owner prefix is in `keep_containers`, because this sync puts a new
    command into it."""
    doomed = [
        entry
        for entry in plan.entries
        if entry.status is PlanStatus.ORPHANED and entry.key.split("_", 1)[0] in deletions
    ]
    if not doomed:
        return [], []

    by_container: dict[int, tuple[Element, list[Element], str]] = {}
    for entry in doomed:
        by_key = index.input_cmds if entry.kind == "input" else index.output_cmds
        containers = index.input_containers if entry.kind == "input" else index.output_containers
        container = containers[entry.key]
        slot = by_container.setdefault(
            container.open_start, (container, [], entry.key.split("_", 1)[0])
        )
        slot[1].append(by_key[entry.key])

    removed: list[Element] = []
    for container, cmds, prefix in by_container.values():
        starts = {cmd.open_start for cmd in cmds}
        whole = all(child.open_start in starts for child in container.children)
        if whole and prefix not in keep_containers:
            removed.append(container)
        else:
            removed.extend(cmds)

    refs = ref_blocks(index)
    blocks: list[Element] = []
    for element in removed:
        is_command = element.type in ("VirtualUdpInCmd", "VirtualOutCmd")
        commands = [element] if is_command else list(element.children)
        for command in commands:
            blocks += [block for block, _page in refs.get(command.attrs.get("U", ""), [])]
    removed += blocks

    spans = sorted({_line_span(index.text, element) for element in removed})
    edits = [_Edit(start, end, "") for start, end in spans]

    gone: set[str] = set()
    for element in removed:
        gone.update(_CO_U.findall(index.text, element.open_start, element.outer_end))
    for match in _CO_WITH_INPUTS.finditer(index.text):
        if _inside(match.start(), spans):
            continue
        inputs = _IN_TAG.findall(match.group(2))
        kept = [tag for tag in inputs if _input_of(tag) not in gone]
        if len(kept) == len(inputs):
            continue
        if kept:
            nc = f'Nc="{len(kept)}"'
            attrs = match.group(1)
            attrs = _NC_VALUE.sub(nc, attrs) if _NC_VALUE.search(attrs) else f"{attrs} {nc}"
            replacement = f"<Co{attrs}>{''.join(kept)}</Co>"
        else:
            replacement = f"<Co{_NC_ATTR.sub('', match.group(1)).rstrip()}/>"
        edits.append(_Edit(match.start(), match.end(), replacement))
    return edits, spans


def _room_move_edits(
    index: ProjectIndex,
    moves: Mapping[str, str],
    place_us: Mapping[str, str],
    removed: Sequence[tuple[int, int]],
) -> list[_Edit]:
    """Points every existing object of an owner prefix (`d5_`) in `moves`
    at the Loxone room the user chose loxmatter's room for."""
    edits: list[_Edit] = []
    for prefix, room in moves.items():
        place_u = place_us.get(room_key(room))
        if place_u is None:
            continue
        for cmd in owner_cmds(index, prefix):
            if cmd.inner_end is None or _inside(cmd.open_start, removed):
                continue
            iodata = _IODATA_TAG.search(index.text, cmd.open_end, cmd.inner_end)
            if iodata is None:
                display = index.text.find("<Display", cmd.open_end, cmd.inner_end)
                at = cmd.inner_end if display < 0 else display
                edits.append(_Edit(at, at, f'<IoData Pr="{escape_attr_value(place_u)}"/>'))
                continue
            span = _attr_span(index.text, iodata.start(), iodata.end(), "Pr")
            if span is None:
                at = iodata.end() - 2
                edits.append(_Edit(at, at, f' Pr="{escape_attr_value(place_u)}"'))
            else:
                edits.append(_Edit(span[0], span[1], f'Pr="{escape_attr_value(place_u)}"'))
    return edits


def _rename_edits(
    index: ProjectIndex, titles: Mapping[int, str], removed: Sequence[tuple[int, int]]
) -> list[_Edit]:
    """Sets the `Title` of each container (by `open_start`) in `titles` -
    the names the user settled on (`reconcile.container_titles`)."""
    containers = {
        c.open_start: c
        for c in [*index.input_containers.values(), *index.output_containers.values()]
    }
    edits: list[_Edit] = []
    for start, title in titles.items():
        container = containers[start]
        if _inside(start, removed):
            continue
        replacement = f'Title="{escape_attr_value(title)}"'
        span = _attr_span(index.text, container.open_start, container.open_end, "Title")
        if span is None:
            at = container.open_end - (2 if container.self_closing else 1)
            edits.append(_Edit(at, at, f" {replacement}"))
        else:
            edits.append(_Edit(span[0], span[1], replacement))
    return edits


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
    bridge_ip: str,
    port: int,
    listen: int,
    saved_at: datetime | None = None,
    room_moves: Mapping[str, str] | None = None,
    renames: Mapping[int, str] | None = None,
    deletions: Collection[str] = (),
) -> bytes:
    """Builds the patched file (design section 3.4/7): updates, new signals
    in already-existing device containers, and completely new device
    containers, and, for new objects whose owner has a room, the Loxone rooms
    they go into (design 2026-09-29).

    `saved_at` (time-zone aware) becomes the file's "last saved" stamp if
    anything changed; without it, or without a change, the stamp stays
    as the file had it.

    The two-way sync's decisions (design 2026-11-08): `room_moves` maps an
    owner prefix (`d5_`) to the loxmatter room its existing objects move
    into - the room must be among `plan.rooms`; `renames` maps a
    container's `open_start` to its new title; `deletions` names the orphan
    owner prefixes (`d8`) whose objects are removed, links and all."""
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

    place_us, edits, created_count = _room_edits(index, plan)
    # A container that gets a new command must survive the clean-up.
    keep_containers = {
        f"{'g' if e.owner_kind == 'group' else 'd'}{e.device_id}"
        for e in plan.entries
        if e.status is PlanStatus.NEW_SIGNAL
    }
    deletion_edits, removed = _deletion_edits(index, plan, deletions, keep_containers)
    edits += deletion_edits
    edits += _room_move_edits(index, room_moves or {}, place_us, removed)
    edits += _rename_edits(index, renames or {}, removed)
    # (kind, owner_kind, id) - NOT (kind, id): both counters start at 1,
    # so group 1 and device 1 would otherwise share one container. `dict`
    # preserves the plan's order, so the generated file is reproducible.
    new_device_groups: dict[tuple[str, str, int], list[PlanEntry]] = {}
    for entry in plan.entries:
        if entry.status is PlanStatus.UPDATED:
            target = entry.container_at
            if target is None:
                cmds = index.input_cmds if entry.kind == "input" else index.output_cmds
                target = cmds[entry.key].open_start
            if not _inside(target, removed):
                edits += _update_edits(index, entry)
        elif entry.status is PlanStatus.NEW_SIGNAL:
            source = desired_inputs if entry.kind == "input" else desired_outputs
            edits.append(_new_signal_edit(index, entry, source, _place_u_for(place_us, entry)))
            created_count += 1
        elif entry.status is PlanStatus.NEW_DEVICE:
            new_device_groups.setdefault(
                (entry.kind, entry.owner_kind, entry.device_id), []
            ).append(entry)

    for (kind, _owner_kind, _owner_id), group_entries in new_device_groups.items():
        source = desired_inputs if kind == "input" else desired_outputs
        # The number of new <C> objects comes back from `_new_device_edit`
        # itself: container + one cmd per entry, plus the newly created
        # caption if any.
        edit, group_created_count = _new_device_edit(
            index,
            group_entries,
            source,
            bridge_ip,
            port,
            listen,
            _place_u_for(place_us, group_entries[0]),
        )
        edits.append(edit)
        created_count += group_created_count

    next_obj_edit = _next_obj_edit(index, created_count)
    if next_obj_edit is not None:
        edits.append(next_obj_edit)
    # Only after the plan's own edits: an unchanged file keeps its stamp.
    if edits and saved_at is not None:
        edits += _saved_date_edits(index, saved_at)

    patched_text = _apply_edits(index.text, edits)
    if not patched_text.startswith(BOM):
        patched_text = BOM + patched_text
    return patched_text.encode("utf-8")
