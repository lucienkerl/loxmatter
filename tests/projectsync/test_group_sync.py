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

"""Groups in the project sync - design 2026-09-10, section 8."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.projectsync.diff import PlanStatus, build_plan
from loxmatter.projectsync.index import build_index

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def _load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
def group_store(tmp_path):
    """Two lamps and one group over both - following `tests/model/
    test_store_groups.py`'s `lamps_with_commands`, minus the store/lamps
    fixtures this module does not otherwise need."""
    store = Store(tmp_path / "test.sqlite")
    lamp_ids = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = _load(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
        lamp_ids.append(device_id)
    group = store.create_group("Living room", lamp_ids)
    yield store, group
    store.close()


def test_a_group_appears_as_a_new_device_container(sample_project, group_store):
    """A group with no output in the project yet is NEW_DEVICE, the same
    status a never-exported device gets - it needs its own container."""
    store, group = group_store
    index = build_index(sample_project)
    plan = build_plan(
        index,
        store.devices(),
        {},
        {},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
    )
    group_entries = [e for e in plan.entries if e.owner_kind == "group"]
    assert group_entries
    assert all(e.status is PlanStatus.NEW_DEVICE for e in group_entries)


def test_a_group_id_does_not_collide_with_a_device_id(sample_project, group_store):
    """Both counters start at 1. Keying the new-container grouping by ID
    alone would merge a group's outputs into a device's container.

    The plan-level assertions below (as in the brief) are necessary but
    NOT sufficient: `build_plan` always stamps `owner_kind` correctly, no
    matter how `apply_plan` later groups entries into containers - a bug
    in `apply_plan`'s OWN grouping (keying on id alone, dropping
    `owner_kind`) would not show up here at all. The second half of this
    test manufactures the actual collision: a second group, created over
    the same two lamps, gets id 2 - the SAME id as the second lamp itself
    (both counters start at 1, per `StoredGroup`'s and `StoredDevice`'s
    docstrings). Neither exists in `sample_project` yet (only device 1,
    "Altes Geraet", does - see the fixture's module docstring), so both
    reach `apply_plan` as NEW_DEVICE output entries with `device_id == 2`
    - exactly the condition under which a grouping key of `(kind,
    device_id)` alone would merge them into one container."""
    from loxmatter.projectsync.patch import apply_plan

    store, group = group_store
    devices = store.devices()
    device_two = devices[1]
    second_group = store.create_group("Bedroom", [d.id for d in devices])
    assert second_group.id == device_two.id  # the collision this test manufactures

    index = build_index(sample_project)
    signals_by_device = {d.id: store.signals(d.id) for d in devices}
    commands_by_device = {d.id: store.commands(d.id) for d in devices}
    groups = store.groups()
    commands_by_group = {g.id: store.group_commands(g.id) for g in groups}

    plan = build_plan(
        index,
        devices,
        signals_by_device,
        commands_by_device,
        groups=groups,
        commands_by_group=commands_by_group,
    )
    owners = {(e.owner_kind, e.device_id) for e in plan.entries}
    assert ("group", group.id) in owners
    assert any(kind == "device" for kind, _ in owners)

    patched = apply_plan(
        index,
        plan,
        devices,
        signals_by_device,
        commands_by_device,
        groups=groups,
        commands_by_group=commands_by_group,
        bridge_ip="192.168.1.2",
        port=7000,
        listen=8080,
    )
    patched_index = build_index(patched.decode("utf-8"))

    # If `apply_plan` merged the two NEW_DEVICE groups (id 2 for both the
    # device and `second_group`), every key - "d2_..." and "g2_..." alike
    # - would resolve to the SAME single container object. Two genuinely
    # separate containers is the only correct outcome.
    device_container = next(
        element
        for key, element in patched_index.output_containers.items()
        if key.startswith(f"d{device_two.id}_")
    )
    group_container = next(
        element
        for key, element in patched_index.output_containers.items()
        if key.startswith(f"g{second_group.id}_")
    )
    assert device_container is not group_container


def test_a_command_entering_the_intersection_is_a_new_signal_in_the_group_container(
    sample_project, group_store
):
    """Task 9 review: `_new_signal_edit`'s container prefix now comes from
    `entry.owner_kind` (`g{id}_` for a group, `d{id}_` for a device)
    instead of a hardcoded `d{id}_` - a group output's `device_id` holds
    the GROUP's id, so the old prefix looked for a `d{id}_` container
    that was never created and tripped `assert matching_container is not
    None`, surfacing as a 500. No existing test drove a group through
    `NEW_SIGNAL` at all, so nothing caught it.

    `register_group_commands` intersects over the members, so *adding* a
    member only narrows the command set further - the only way to make a
    command *enter* the intersection is to *remove* a member (or use one
    whose clusters differ from the start). The two checked-in lamps
    differ in exactly this way: `ikea_kajplats_cws_lamp.json` (colour)
    supports `color`, `ikea_kajplats_ws_lamp.json` (white spectrum) does
    not, so the group over both never offers `color` (see
    `tests/model/test_store_groups.py`,
    `test_the_group_offers_only_what_every_member_accepts`). Removing the
    white-spectrum member is therefore the widening step this test needs.
    """
    from loxmatter.projectsync.patch import apply_plan

    store, group = group_store
    colour_lamp, white_spectrum_lamp = store.devices()

    # Step 1: patch once so the group's own container exists at all -
    # otherwise every group command would be NEW_DEVICE, not NEW_SIGNAL,
    # and this test would never reach `_new_signal_edit`.
    index = build_index(sample_project)
    first_plan = build_plan(
        index,
        [],
        {},
        {},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
    )
    assert not any(
        e.owner_kind == "group" and e.key == f"g{group.id}_color" for e in first_plan.entries
    ), "the two-member group must not already offer color"
    first_patched = apply_plan(
        index,
        first_plan,
        [],
        {},
        {},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
        bridge_ip="192.168.1.2",
        port=7000,
        listen=8080,
    )
    first_index = build_index(first_patched.decode("utf-8"))
    group_container_before = next(
        element
        for key, element in first_index.output_containers.items()
        if key.startswith(f"g{group.id}_")
    )
    container_u_before = group_container_before.attrs["U"]

    # Step 2: narrow the membership to just the colour lamp - `color`
    # enters the intersection.
    store.set_group_members(group.id, [colour_lamp.id])
    assert "color" in {c.slug for c in store.group_commands(group.id)}

    # Step 3: re-plan against the patched text. The container from step 1
    # already exists, so the new command must be NEW_SIGNAL, not
    # NEW_DEVICE.
    second_plan = build_plan(
        first_index,
        [],
        {},
        {},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
    )
    color_key = f"g{group.id}_color"
    color_entries = [e for e in second_plan.entries if e.key == color_key]
    assert len(color_entries) == 1
    assert color_entries[0].status is PlanStatus.NEW_SIGNAL
    assert color_entries[0].owner_kind == "group"

    # Step 4: apply, and check the new <C> landed INSIDE the group's own
    # `g{id}_` container - same U as before, not a freshly minted
    # container and not a device's. This is the exact assertion the old
    # hardcoded `d{id}_` prefix could never have satisfied: it would have
    # raised before producing any bytes to check here at all.
    second_patched = apply_plan(
        first_index,
        second_plan,
        [],
        {},
        {},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
        bridge_ip="192.168.1.2",
        port=7000,
        listen=8080,
    )
    second_index = build_index(second_patched.decode("utf-8"))
    color_container = second_index.output_containers[color_key]
    assert color_container.attrs["U"] == container_u_before
    group_container_us = {
        element.attrs["U"]
        for key, element in second_index.output_containers.items()
        if key.startswith(f"g{group.id}_")
    }
    assert group_container_us == {container_u_before}, "no second group container was created"
    device_container_us = {
        element.attrs["U"]
        for key, element in second_index.output_containers.items()
        if key.startswith((f"d{white_spectrum_lamp.id}_", f"d{colour_lamp.id}_"))
    }
    assert container_u_before not in device_container_us


def test_a_patched_project_keeps_matching_after_a_rename(sample_project, group_store):
    """Containers are matched by key, and keys come from the ID - so a
    rename leaves a stale title, not a broken sync (design 7)."""
    from loxmatter.projectsync.patch import apply_plan

    store, group = group_store
    index = build_index(sample_project)
    plan = build_plan(
        index,
        [],
        {},
        {},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
    )
    patched = apply_plan(
        index,
        plan,
        [],
        {},
        {},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
        bridge_ip="192.168.1.2",
        port=7000,
        listen=8080,
    )
    store.rename_group(group.id, "Something else")
    second_index = build_index(patched.decode("utf-8"))
    second = build_plan(
        second_index,
        [],
        {},
        {},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
    )
    assert second.entries
    assert not any(e.status is PlanStatus.NEW_DEVICE for e in second.entries)


def test_a_deleted_groups_leftover_output_is_orphaned(sample_project, group_store):
    """`diff._is_managed_owner_key` review: the orphan guard used to
    accept only keys whose owner segment starts with `d`, so a `g{id}_*`
    command left behind by a deleted group was silently ignored - unlike
    the device equivalent (`d9_9_verwaist` in `sample_project`, covered by
    `test_diff.test_orphaned_signal_is_reported`), which was already
    reported. Patch a group's container into the file, delete the group,
    and confirm its leftover output keys now come back ORPHANED."""
    from loxmatter.projectsync.patch import apply_plan

    store, group = group_store
    index = build_index(sample_project)
    plan = build_plan(
        index,
        [],
        {},
        {},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
    )
    patched = apply_plan(
        index,
        plan,
        [],
        {},
        {},
        groups=store.groups(),
        commands_by_group={group.id: store.group_commands(group.id)},
        bridge_ip="192.168.1.2",
        port=7000,
        listen=8080,
    )
    patched_index = build_index(patched.decode("utf-8"))
    leftover_keys = {key for key in patched_index.output_cmds if key.startswith(f"g{group.id}_")}
    assert leftover_keys, "the group must actually have written commands to leave behind"

    # The group is gone - re-plan the way `sync.run_sync` would after
    # `delete_group`: no group in `groups`, nothing in `commands_by_group`.
    store.delete_group(group.id)
    second_plan = build_plan(patched_index, [], {}, {})
    orphaned_keys = {e.key for e in second_plan.entries if e.status is PlanStatus.ORPHANED}
    assert leftover_keys <= orphaned_keys
