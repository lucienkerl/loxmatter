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

"""Group templates - design 2026-09-10, section 7."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.export.documents import filename_for, render_virtual_out
from loxmatter.export.outputs import to_group_outputs
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
def group(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    members = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = load(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot))
        members.append(device_id)
    created = store.create_group("Living room", members)
    yield store, created
    store.close()


def test_a_group_filename_uses_the_g_prefix(group):
    _store, created = group
    assert (
        filename_for("VO", created.id, created.label, kind="g")
        == f"VO_g{created.id}_Living_room.xml"
    )


def test_a_device_filename_is_unchanged(group):
    """The default must stay exactly what it was - every existing export
    keeps its name."""
    assert filename_for("VO", 4, "Lamp 1") == "VO_d4_Lamp_1.xml"


def test_on_and_off_are_paired_without_an_endpoint(group):
    """`to_outputs` pairs over (endpoint, cluster); a group command has no
    endpoint, so the group variant pairs over the cluster alone."""
    store, created = group
    outputs = to_group_outputs(store.group_commands(created.id))
    # `off_path` defaults to `""`, not `None` (see `LoxoneCommand`) - an
    # unpaired output still has an `off_path` that is merely falsy, so the
    # filter below must test truthiness, not identity with `None`. With
    # `is not None` every output would satisfy it and this test would pass
    # whether or not the group path pairs correctly at all.
    combined = [o for o in outputs if o.off_path]
    assert len(combined) == 1
    assert "/cmd/g" in combined[0].path
    assert "/cmd/g" in combined[0].off_path


def test_every_group_command_becomes_an_output(group):
    store, created = group
    outputs = to_group_outputs(store.group_commands(created.id))
    keys = {o.key for o in outputs}
    for command in store.group_commands(created.id):
        assert command.key in keys


def test_a_value_command_is_analog(group):
    store, created = group
    outputs = {o.key: o for o in to_group_outputs(store.group_commands(created.id))}
    level = next(c for c in store.group_commands(created.id) if c.slug == "level")
    assert outputs[level.key].analog is True
    assert "<v>" in outputs[level.key].path


def test_the_group_template_names_itself_a_group(group):
    store, created = group
    xml = render_virtual_out(
        created.label,
        "http://192.168.1.2:8080",
        to_group_outputs(store.group_commands(created.id)),
        is_group=True,
    ).decode("utf-8")
    assert "Group" in xml or "Gruppe" in xml
    assert created.label in xml
