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
        include_new_devices=True,
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
        include_new_devices=True,
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
    assert not any(e.status is PlanStatus.NEW_DEVICE for e in second.entries)
