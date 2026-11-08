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

"""Ties parsing, diffing and patching together into a single call - what
`api.project_sync` invokes (design section 4: one request, no
intermediate state on the server)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, timezone

from loxmatter import i18n
from loxmatter.export.scope import export_mode, exports_on_its_own, scoped
from loxmatter.model.store import (
    Store,
    StoredCommand,
    StoredDevice,
    StoredGroup,
    StoredGroupCommand,
    StoredSignal,
)
from loxmatter.model.sync_baseline_store import OwnerBaseline, SyncBaseline
from loxmatter.projectsync.diff import SyncPlan, build_plan
from loxmatter.projectsync.index import ProjectFormatError, ProjectIndex, build_index
from loxmatter.projectsync.patch import apply_plan
from loxmatter.projectsync.reconcile import (
    Owner,
    Reconciliation,
    SyncDecisions,
    container_titles,
    find_differences,
    find_orphans,
    new_entry_room,
)
from loxmatter.projectsync.savedate import recorded_utc_offset

__all__ = ["ProjectFormatError", "ProjectSyncResult", "apply_decisions_to_store", "run_sync"]


@dataclass(frozen=True)
class ProjectSyncResult:
    plan: SyncPlan
    patched: bytes
    reconciliation: Reconciliation = field(default_factory=Reconciliation)
    decisions: SyncDecisions = field(default_factory=SyncDecisions)


def _default_room_title(index: ProjectIndex) -> str | None:
    """The title of Loxone's default room - where an object goes when the
    user decides it belongs in no room, as loxmatter has it."""
    return next(
        (p.attrs.get("Title") for p in index.places if p.attrs.get("First") == "true"), None
    )


def _decided_owner(owner: Owner, reconciliation: Reconciliation, decisions: SyncDecisions) -> Owner:
    """`owner` as it will be once the user's "Loxone is right" choices are
    stored."""
    for difference in reconciliation.rooms:
        if difference.owner.ref == owner.ref and decisions.rooms.get(owner.ref) == "loxone":
            owner = replace(owner, room=difference.loxone)
    for name in reconciliation.names:
        if name.owner.ref == owner.ref and decisions.names.get(owner.ref) == "loxone":
            owner = replace(owner, label=name.loxone)
    return owner


def run_sync(
    raw: bytes,
    store: Store,
    *,
    bridge_ip: str,
    port: int,
    listen: int,
    miniserver_ip: str | None = None,
    utc_offset: timedelta | None = None,
    now: datetime | None = None,
    decisions: SyncDecisions | None = None,
    baseline: SyncBaseline | None = None,
) -> ProjectSyncResult:
    """`miniserver_ip` selects the `LoxLIVE` block (= Miniserver) to compare
    against, if the project file has several configured (see
    `index.build_index`/`index.AmbiguousMiniserverError`) - with exactly
    one Miniserver in the file it remains optional.

    `utc_offset` is the user's, for the wall-clock half of the "last
    saved" stamp (`projectsync.savedate`). The bridge's own clock zone is
    no substitute: in a container it is usually UTC. Without one, the
    offset the file itself last recorded stands in - right unless the
    clocks changed in between - and UTC only when the file records none.
    `now` exists for tests.

    `decisions` are the user's answers to the two-way sync's questions
    (design 2026-11-08); without them, every difference is skipped and
    nothing is deleted, which is what the first, read-only look at a file
    gets. The decisions shape the file only - `apply_decisions_to_store`
    carries the "Loxone is right" ones into loxmatter. `baseline` defaults
    to the store's."""
    decisions = decisions or SyncDecisions()
    if baseline is None:
        baseline = store.sync_baseline.get()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        # A wrong file (image, ZIP, UTF-16 export) must not deliver a bare
        # UnicodeDecodeError here - at the endpoint that would be an HTTP
        # 500 instead of a comprehensible message (design section 8).
        raise ProjectFormatError(i18n.t("projectsync.not_utf8")) from exc
    # `AmbiguousMiniserverError` (subclass of `ProjectFormatError`) is
    # deliberately left unhandled here and propagates up to
    # `api.project_sync`. There it is caught SPECIFICALLY (not just via
    # the generic `except ProjectFormatError` -> HTTP 400): if it carries
    # `candidates` (several Miniservers found), the endpoint returns,
    # instead of an error, a 200 response with
    # `needs_miniserver_selection=True` for the selection field in the
    # WebUI (user request after the review) - only the "none configured at
    # all" case (empty `candidates`) remains a genuine 400. Without an
    # unambiguous Miniserver, there is no place to compare against at all.
    index = build_index(text, miniserver_ip)
    # Each device cut down to its group export mode (design 2026-11-07,
    # section 2) - the same `export.scope` the template ZIP uses. A member
    # switched only through its group is left out entirely: even with no
    # signals it would still bring its online input along (`to_inputs`).
    member_ids = store.group_member_ids()
    devices: list[StoredDevice] = []
    signals_by_device: dict[int, Sequence[StoredSignal]] = {}
    commands_by_device: dict[int, Sequence[StoredCommand]] = {}
    for device in store.devices():
        mode = export_mode(device, member_ids)
        if not exports_on_its_own(mode):
            continue
        devices.append(device)
        signals_by_device[device.id], commands_by_device[device.id] = scoped(
            mode, store.signals(device.id), store.commands(device.id)
        )
    groups = store.groups()
    commands_by_group: dict[int, Sequence[StoredGroupCommand]] = {
        group.id: store.group_commands(group.id) for group in groups
    }

    owners = [Owner("device", d.id, d.label, d.room) for d in devices] + [
        Owner("group", g.id, g.label, g.room) for g in groups
    ]
    reconciliation = find_differences(index, owners, baseline)
    decided = {o.ref: _decided_owner(o, reconciliation, decisions) for o in owners}
    devices = [_with_owner(d, decided[f"device:{d.id}"]) for d in devices]
    groups = [_with_owner(g, decided[f"group:{g.id}"]) for g in groups]

    room_overrides = {
        (o.kind, o.id): new_entry_room(o, reconciliation, decisions) for o in decided.values()
    }
    default_room = _default_room_title(index)
    room_moves: dict[str, str] = {}
    renames: dict[int, str] = {}
    for difference in reconciliation.rooms:
        ref = difference.owner.ref
        if decisions.rooms.get(ref) == "loxmatter":
            target = difference.owner.room or default_room
            if target:
                room_moves[difference.owner.prefix] = target
    for name in reconciliation.names:
        choice = decisions.names.get(name.owner.ref)
        if choice is not None:
            new_name = name.loxone if choice == "loxone" else name.owner.label
            renames.update(container_titles(index, name.owner, new_name))

    plan = build_plan(
        index,
        devices,
        signals_by_device,
        commands_by_device,
        groups=groups,
        commands_by_group=commands_by_group,
        bridge_ip=bridge_ip,
        port=port,
        listen=listen,
        room_overrides=room_overrides,
        extra_room_names=list(room_moves.values()),
    )

    all_groups = store.groups()
    groups_of_device: dict[int, list[str]] = {}
    for group in all_groups:
        for member in store.group_members(group.id):
            groups_of_device.setdefault(member.id, []).append(group.label)
    orphans = find_orphans(
        index,
        plan.entries,
        device_labels={d.id: d.label for d in store.devices()},
        group_labels={g.id: g.label for g in all_groups},
        groups_of_device=groups_of_device,
        exported_devices={d.id for d in devices},
        kept=baseline.kept,
    )
    reconciliation = replace(reconciliation, orphans=orphans)
    # A `ProjectFormatError` from `_installation_suffix` (finding N1 from
    # the re-review: via `apply_plan` -> `_new_signal_edit`/
    # `_new_device_edit` -> `new_unique_id`, as soon as an entry needs a
    # new ID) is deliberately left unhandled: it means "this file's ID
    # format cannot be recognised at all" (design section 10), which should
    # rightly fail the whole upload (`api.project_sync` already catches
    # `ProjectFormatError` into a comprehensible 400).
    if utc_offset is None:
        recorded = None if index.document is None else recorded_utc_offset(index.document.attrs)
        utc_offset = recorded if recorded is not None else timedelta(0)
    saved_at = (now or datetime.now(UTC)).astimezone(timezone(utc_offset))
    patched = apply_plan(
        index,
        plan,
        devices,
        signals_by_device,
        commands_by_device,
        groups=groups,
        commands_by_group=commands_by_group,
        bridge_ip=bridge_ip,
        port=port,
        listen=listen,
        saved_at=saved_at,
        room_moves=room_moves,
        renames=renames,
        deletions=decisions.delete,
    )
    return ProjectSyncResult(plan, patched, reconciliation, decisions)


def _with_owner[T: (StoredDevice, StoredGroup)](stored: T, owner: Owner) -> T:
    if stored.label == owner.label and stored.room == owner.room:
        return stored
    return replace(stored, label=owner.label, room=owner.room)


def apply_decisions_to_store(store: Store, result: ProjectSyncResult) -> None:
    """Stores the "Loxone is right" choices in loxmatter and records the
    new baseline (design 2026-11-08, section 4) - called once the user has
    taken the patched file, never on the first look at a file.

    An owner the user skipped keeps its old baseline, so the next sync
    still knows which side had moved."""
    reconciliation, decisions = result.reconciliation, result.decisions
    for difference in reconciliation.rooms:
        if decisions.rooms.get(difference.owner.ref) != "loxone":
            continue
        if difference.owner.kind == "group":
            store.set_group_room(difference.owner.id, difference.loxone)
        else:
            store.set_room(difference.owner.id, difference.loxone)
    for name in reconciliation.names:
        if decisions.names.get(name.owner.ref) != "loxone":
            continue
        if name.owner.kind == "group":
            store.rename_group(name.owner.id, name.loxone)
        else:
            store.rename_device(name.owner.id, name.loxone)

    before = store.sync_baseline.get()
    skipped_rooms = {
        d.owner.ref for d in reconciliation.rooms if d.owner.ref not in decisions.rooms
    }
    skipped_names = {
        n.owner.ref for n in reconciliation.names if n.owner.ref not in decisions.names
    }
    owners: dict[str, OwnerBaseline] = dict(before.owners)
    member_ids = store.group_member_ids()
    current = [
        Owner("device", d.id, d.label, d.room)
        for d in store.devices()
        if exports_on_its_own(export_mode(d, member_ids))
    ] + [Owner("group", g.id, g.label, g.room) for g in store.groups()]
    for owner in current:
        old = before.owners.get(owner.ref)
        owners[owner.ref] = OwnerBaseline(
            room=(old.room if old else None) if owner.ref in skipped_rooms else owner.room,
            name=(old.name if old else None) if owner.ref in skipped_names else owner.label,
        )
    kept = frozenset(o.prefix for o in reconciliation.orphans if o.prefix not in decisions.delete)
    store.sync_baseline.set(SyncBaseline(owners, kept))
