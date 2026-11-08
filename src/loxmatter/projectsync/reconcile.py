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

"""What differs between loxmatter and the project file beyond the managed
attributes, in both directions (design `docs/superpowers/specs/
2026-11-08-two-way-project-sync-design.md`).

Three kinds of finding, each a decision for the user rather than an edit
made on their behalf:

- **Room.** loxmatter keeps one room per device or group; Loxone keeps one
  per object (`IoData.Pr`). When all of an owner's objects sit in the same
  Loxone room and that room differs from loxmatter's, the user picks the
  side that is right. Objects spread over several rooms are a deliberate
  split in Loxone Config and are left alone.
- **Name.** The device's container `Title` - the name Loxone shows - can
  be renamed in Loxone Config, and loxmatter's label can change after the
  export. A container title that is neither the label nor shared by the
  owner's other containers is a difference to decide.
- **Orphans.** Objects whose key loxmatter no longer exports: a removed
  device, a lamp now switched through its group, a signal switched off.
  They are offered for deletion, grouped by owner, together with the
  program pages they are still wired into.

Pure decisions over the parsed file; the text edits that carry them out
live in `projectsync.patch`."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from loxmatter import i18n
from loxmatter.export.documents import output_title
from loxmatter.model.sync_baseline_store import OwnerBaseline, SyncBaseline
from loxmatter.projectsync.diff import PlanEntry, PlanStatus
from loxmatter.projectsync.index import ProjectIndex
from loxmatter.projectsync.rooms import room_key
from loxmatter.projectsync.scan import Element
from loxmatter.projectsync.schema import sibling_iodata_attrs

__all__ = [
    "LinkedBlock",
    "NameDifference",
    "OrphanOwner",
    "Owner",
    "Reconciliation",
    "RoomDifference",
    "Side",
    "SplitRooms",
    "SyncDecisions",
    "container_titles",
    "find_differences",
    "find_orphans",
    "new_entry_room",
    "owner_cmds",
    "owner_containers",
    "owner_prefix",
    "ref_blocks",
]

Side = Literal["loxone", "loxmatter"]


def owner_prefix(owner_kind: str, owner_id: int) -> str:
    """The key prefix of an owner's objects: `d5_` for device 5, `g2_` for
    group 2 (design 2026-09-10, section 8)."""
    return f"{'g' if owner_kind == 'group' else 'd'}{owner_id}_"


@dataclass(frozen=True)
class Owner:
    """A device or group as loxmatter has it now."""

    kind: str  # "device" | "group"
    id: int
    label: str
    room: str | None

    @property
    def ref(self) -> str:
        """How the API and the baseline name this owner: `device:5`."""
        return f"{self.kind}:{self.id}"

    @property
    def prefix(self) -> str:
        return owner_prefix(self.kind, self.id)


@dataclass(frozen=True)
class SyncDecisions:
    """What the user chose. An owner missing from `rooms`/`names` is
    skipped: both sides stay as they are."""

    rooms: Mapping[str, Side] = field(default_factory=dict)
    names: Mapping[str, Side] = field(default_factory=dict)
    # Orphan owner prefixes without the trailing `_` (`d8`, `g2`).
    delete: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RoomDifference:
    owner: Owner
    # `None`: the objects sit in Loxone's default room ("not assigned").
    loxone: str | None
    preselect: Side


@dataclass(frozen=True)
class NameDifference:
    owner: Owner
    loxone: str
    preselect: Side


@dataclass(frozen=True)
class SplitRooms:
    """An owner whose objects Loxone spreads over several rooms - shown,
    never changed."""

    owner: Owner
    rooms: list[str]


@dataclass(frozen=True)
class LinkedBlock:
    """An `InputRef`/`OutputRef` on a program page that wires an object."""

    title: str
    page: str


@dataclass(frozen=True)
class OrphanOwner:
    # `d8`, `g2` - the owner segment of the objects' keys.
    prefix: str
    # The Loxone container's title, or the loxmatter label if the owner
    # still exists.
    label: str
    # "removed": loxmatter has no such device/group any more.
    # "via_group": a group member that is now switched through its group.
    # "signal_dropped": the owner exists, these signals are no longer
    # exported.
    reason: str
    group_labels: list[str]
    entries: list[PlanEntry]
    links: list[LinkedBlock]
    delete_by_default: bool


@dataclass(frozen=True)
class Reconciliation:
    rooms: list[RoomDifference] = field(default_factory=list)
    names: list[NameDifference] = field(default_factory=list)
    split: list[SplitRooms] = field(default_factory=list)
    orphans: list[OrphanOwner] = field(default_factory=list)
    # Loxone's room per owner ref, where all its objects agree - what new
    # objects of that owner join when the user decides nothing.
    shared_rooms: dict[str, str | None] = field(default_factory=dict)


def owner_cmds(index: ProjectIndex, prefix: str) -> list[Element]:
    """Every existing command object of the owner, inputs first."""
    return [e for k, e in index.input_cmds.items() if k.startswith(prefix)] + [
        e for k, e in index.output_cmds.items() if k.startswith(prefix)
    ]


def owner_containers(index: ProjectIndex, prefix: str) -> list[Element]:
    """The owner's containers, each once, in document order."""
    found: dict[int, Element] = {}
    for containers in (index.input_containers, index.output_containers):
        for key, container in containers.items():
            if key.startswith(prefix):
                found.setdefault(container.open_start, container)
    return [found[start] for start in sorted(found)]


def _place_titles(index: ProjectIndex) -> tuple[dict[str, str], set[str]]:
    """`Place.U` -> title, and the `U` of the default room, which Loxone
    shows as "not assigned"."""
    titles: dict[str, str] = {}
    default: set[str] = set()
    for place in index.places:
        u = place.attrs.get("U")
        if not u:
            continue
        titles[u] = place.attrs.get("Title", "")
        if place.attrs.get("First") == "true":
            default.add(u)
    return titles, default


def _cmd_room(
    index: ProjectIndex, cmd: Element, titles: Mapping[str, str], default: Collection[str]
) -> str | None:
    pr = (sibling_iodata_attrs(index.text, cmd) or {}).get("Pr")
    if not pr or pr in default:
        return None
    return titles.get(pr)


def _preselect(loxone: str | None, baseline: str | None, has_baseline: bool) -> Side:
    """The side that changed since the last sync is the one that is right
    (design section 4). Loxone still showing the baseline means loxmatter
    moved. Without a baseline, Loxone wins when it has a value: a change
    made by hand in Loxone Config is deliberate, loxmatter's room and name
    are often still what commissioning gave the device."""
    if has_baseline:
        return "loxmatter" if room_key(loxone or "") == room_key(baseline or "") else "loxone"
    return "loxone" if loxone else "loxmatter"


def find_differences(
    index: ProjectIndex, owners: Iterable[Owner], baseline: SyncBaseline
) -> Reconciliation:
    """Room and name differences for every owner the file already holds."""
    titles, default = _place_titles(index)
    rooms: list[RoomDifference] = []
    names: list[NameDifference] = []
    split: list[SplitRooms] = []
    shared: dict[str, str | None] = {}
    for owner in owners:
        cmds = owner_cmds(index, owner.prefix)
        if not cmds:
            continue
        before = baseline.owners.get(owner.ref)

        found = {_cmd_room(index, cmd, titles, default) for cmd in cmds}
        by_key = {room_key(room or ""): room for room in found}
        if len(by_key) > 1:
            split.append(SplitRooms(owner, sorted(room or "" for room in by_key.values())))
        else:
            loxone_room = next(iter(by_key.values()))
            shared[owner.ref] = loxone_room
            if room_key(loxone_room or "") != room_key(owner.room or ""):
                rooms.append(
                    RoomDifference(
                        owner,
                        loxone_room,
                        _preselect(
                            loxone_room,
                            None if before is None else before.room,
                            before is not None,
                        ),
                    )
                )

        bare = {
            _bare_name(owner, c.attrs.get("Title", "").strip())
            for c in owner_containers(index, owner.prefix)
        }
        others = {name for name in bare if name and name != owner.label.strip()}
        if len(others) == 1:
            loxone_name = next(iter(others))
            names.append(NameDifference(owner, loxone_name, _name_preselect(loxone_name, before)))
    return Reconciliation(rooms=rooms, names=names, split=split, shared_rooms=shared)


def _title_forms(owner_kind: str) -> list[tuple[str, str]]:
    """The `(before, after)` text loxmatter puts around a label in a
    container title it creates: "Matter — {label}" for a device, the group
    title in every language for a group - the file may have been written
    under either."""
    marker = "\x00"
    if owner_kind == "group":
        templates = [t.replace("{label}", marker) for t in i18n.all_templates("export.group_title")]
    else:
        templates = [output_title(marker)]
    return [tuple(t.split(marker, 1)) for t in templates if marker in t]  # type: ignore[misc]


def _bare_name(owner: Owner, title: str) -> str:
    """The name inside a container title: the label loxmatter wrapped, or
    the whole title when the user replaced it with a plain name."""
    for before, after in _title_forms(owner.kind):
        if title.startswith(before) and title.endswith(after) and len(title) > len(before + after):
            return title[len(before) : len(title) - len(after)].strip()
    return title


def container_titles(index: ProjectIndex, owner: Owner, name: str) -> dict[int, str]:
    """The new `Title` of each of the owner's containers (by `open_start`)
    once its name is `name`, keeping each container's style: one that
    carries loxmatter's wrapping keeps it, a plain one stays plain.
    Containers that already carry that title are left out."""
    forms = _title_forms(owner.kind)
    titles: dict[int, str] = {}
    for container in owner_containers(index, owner.prefix):
        old = container.attrs.get("Title", "").strip()
        new = name
        for before, after in forms:
            if old.startswith(before) and old.endswith(after) and len(old) > len(before + after):
                new = f"{before}{name}{after}"
                break
        if new != container.attrs.get("Title"):
            titles[container.open_start] = new
    return titles


def _name_preselect(loxone: str, before: OwnerBaseline | None) -> Side:
    if before is None or before.name is None:
        return "loxone"
    return "loxmatter" if loxone == before.name else "loxone"


def new_entry_room(
    owner: Owner, reconciliation: Reconciliation, decisions: SyncDecisions
) -> str | None:
    """The room a NEW object of `owner` goes into: the one the user chose,
    else the room all its existing objects share in Loxone - so a new
    signal joins its siblings - else loxmatter's room."""
    choice = decisions.rooms.get(owner.ref)
    if choice == "loxmatter":
        return owner.room
    if owner.ref in reconciliation.shared_rooms:
        shared = reconciliation.shared_rooms[owner.ref]
        if shared is not None:
            return shared
    return owner.room


def ref_blocks(index: ProjectIndex) -> dict[str, list[tuple[Element, str]]]:
    """`Ref` -> the blocks carrying it, each with the title of the program
    page it sits on. An `InputRef`/`OutputRef` names the object it wires by
    that object's `U`; nothing else in a project file points at a virtual
    command's own `U` (measured on the maintainer's file, 2026-11-08)."""
    found: dict[str, list[tuple[Element, str]]] = {}

    def walk(elements: Sequence[Element], page: str) -> None:
        for element in elements:
            here = element.attrs.get("Title", "") if element.type == "Page" else page
            ref = element.attrs.get("Ref")
            if ref:
                found.setdefault(ref, []).append((element, here))
            walk(element.children, here)

    walk(index.top_level, "")
    return found


def find_orphans(
    index: ProjectIndex,
    entries: Sequence[PlanEntry],
    *,
    device_labels: Mapping[int, str],
    group_labels: Mapping[int, str],
    groups_of_device: Mapping[int, Sequence[str]],
    exported_devices: Collection[int],
    kept: Collection[str],
) -> list[OrphanOwner]:
    """The plan's ORPHANED entries, one `OrphanOwner` per owner prefix.

    `device_labels`/`group_labels` hold every device and group loxmatter
    still has, exported or not; `exported_devices` the ones that get
    objects of their own; `groups_of_device` a member's group labels."""
    refs = ref_blocks(index)
    by_prefix: dict[str, list[PlanEntry]] = {}
    for entry in entries:
        if entry.status is PlanStatus.ORPHANED:
            by_prefix.setdefault(entry.key.split("_", 1)[0], []).append(entry)

    orphans: list[OrphanOwner] = []
    for prefix, owned in by_prefix.items():
        kind, number = prefix[0], prefix[1:]
        owner_id = int(number) if number.isdigit() else -1
        groups: list[str] = []
        if kind == "g":
            label = group_labels.get(owner_id)
            reason = "removed" if label is None else "signal_dropped"
        else:
            label = device_labels.get(owner_id)
            if label is None:
                reason = "removed"
            elif owner_id not in exported_devices:
                reason = "via_group"
                groups = list(groups_of_device.get(owner_id, []))
            else:
                reason = "signal_dropped"
        if label is None:
            label = _container_title(index, owned) or prefix

        links: list[LinkedBlock] = []
        for entry in owned:
            cmd = (index.input_cmds if entry.kind == "input" else index.output_cmds)[entry.key]
            for block, page in refs.get(cmd.attrs.get("U", ""), []):
                links.append(LinkedBlock(block.attrs.get("Title", ""), page))
        orphans.append(
            OrphanOwner(
                prefix=prefix,
                label=label,
                reason=reason,
                group_labels=groups,
                entries=owned,
                links=links,
                delete_by_default=prefix not in kept,
            )
        )
    return orphans


def _container_title(index: ProjectIndex, entries: Sequence[PlanEntry]) -> str | None:
    for entry in entries:
        containers = index.input_containers if entry.kind == "input" else index.output_containers
        container = containers.get(entry.key)
        if container is not None and container.attrs.get("Title"):
            return container.attrs["Title"]
    return None
