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

"""Group export modes and capabilities (design 2026-11-07)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from loxmatter.export.capabilities import Capability, input_capabilities, output_capabilities
from loxmatter.export.commands import extract_commands
from loxmatter.export.scope import export_mode, scoped
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite")
    yield s
    s.close()


def _register(store: Store, name: str) -> int:
    snapshot = load(name)
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot))
    return device_id


def test_a_device_in_no_group_is_exported_in_full(store):
    lamp = _register(store, "ikea_kajplats_cws_lamp.json")
    store.set_group_export(lamp, "group")  # ignored outside a group
    assert export_mode(store.device(lamp), store.group_member_ids()) == "full"


def test_a_group_member_defaults_to_the_group(store):
    lamp = _register(store, "ikea_kajplats_cws_lamp.json")
    store.create_group("Hall", [lamp])
    assert export_mode(store.device(lamp), store.group_member_ids()) == "group"


def test_a_members_choice_survives_editing_the_group(store):
    """The mode lives on the device: `set_group_members` replaces every
    membership row, and a choice stored there would be lost."""
    a = _register(store, "ikea_kajplats_cws_lamp.json")
    b = _register(store, "ikea_kajplats_ws_lamp.json")
    group = store.create_group("Hall", [a])
    store.set_group_export(a, "status")
    store.set_group_members(group.id, [a, b])
    assert export_mode(store.device(a), store.group_member_ids()) == "status"


def test_scoped_cuts_the_lists_to_the_mode(store):
    lamp = _register(store, "ikea_kajplats_cws_lamp.json")
    signals, commands = store.signals(lamp), store.commands(lamp)
    assert scoped("group", signals, commands) == ([], [])
    assert scoped("status", signals, commands) == (signals, [])
    assert scoped("full", signals, commands) == (signals, commands)


def test_a_lamp_exports_one_light_capability(store):
    """A lamp's single commands sit in the expert block by default; what
    is left is the lighting controller, which drives everything at once."""
    lamp = _register(store, "ikea_kajplats_cws_lamp.json")
    assert [c.key for c in output_capabilities(store.commands(lamp))] == ["light"]


def test_a_plugs_commands_fold_into_one_on_off(store):
    """`on`, `off` and `toggle` are one thing to a person."""
    plug = _register(store, "ikea_grillplats_plug.json")
    assert [c.key for c in output_capabilities(store.commands(plug))] == ["onoff"]
    assert [c.key for c in input_capabilities(store.signals(plug))] == ["power", "energy"]


def test_a_signal_outside_the_table_keeps_its_title(store):
    plug = _register(store, "ikea_grillplats_plug.json")
    (power, *_) = [s for s in store.signals(plug) if s.exported and s.ref.cluster_id == 144]
    stranger = replace(power, ref=replace(power.ref, cluster_id=0xFC00), title="Vendor value")
    assert input_capabilities([stranger]) == [Capability(None, "Vendor value")]


def test_a_schema_17_database_keeps_exporting_its_members(tmp_path):
    """Before schema 18 every group member was exported on its own as well;
    an existing installation keeps that, written down as `full`."""
    path = tmp_path / "v17.sqlite"
    store = Store(path)
    member = _register(store, "ikea_kajplats_cws_lamp.json")
    loner = _register(store, "ikea_kajplats_ws_lamp.json")
    store.create_group("Hall", [member])
    store.close()
    db = sqlite3.connect(str(path))
    db.executescript("ALTER TABLE device DROP COLUMN group_export; PRAGMA user_version = 17;")
    db.close()

    store = Store(path)
    try:
        assert store.device(member).group_export == "full"
        assert store.device(loner).group_export is None
        assert export_mode(store.device(member), store.group_member_ids()) == "full"
    finally:
        store.close()
