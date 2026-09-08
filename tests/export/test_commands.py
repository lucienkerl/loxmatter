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

import json
from pathlib import Path

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.paths import ACCEPTED_COMMAND_LIST_ID
from loxmatter.profiles import table
from loxmatter.profiles.table import ADMINISTRATIVE_CLUSTERS, command_slug

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_administrative_clusters_are_named():
    """These clusters must never appear as a Loxone output."""
    for cluster in (31, 41, 42, 48, 49, 50, 51, 56, 60, 62, 63):
        assert cluster in ADMINISTRATIVE_CLUSTERS


def test_known_command_has_a_slug():
    assert command_slug(6, 0) == "off"
    assert command_slug(6, 1) == "on"
    assert command_slug(6, 2) == "toggle"


def test_unknown_command_has_none():
    assert command_slug(6, 99) is None
    assert command_slug(64999, 0) is None


def test_plug_yields_only_the_onoff_commands():
    commands = extract_commands(load("ikea_grillplats_plug.json"))
    assert {(c.cluster_id, c.command_id) for c in commands} == {(6, 0), (6, 1), (6, 2)}
    assert all(c.endpoint == 1 for c in commands)


def test_button_yields_no_commands():
    """A button is an input device."""
    assert extract_commands(load("ikea_bilresa_button.json")) == []


def test_administrative_commands_never_appear():
    """Sanity check against the real plug fixture - not proof of the gate.

    In normal mode `command_slug()` returns `None` for every administrative
    cluster anyway, because none of them has a `commands` entry in
    `clusters.yaml`. So this test would still be green even if the
    ADMINISTRATIVE_CLUSTERS gate in `extract_commands()` were removed
    entirely. The actual proof of the gate lives in
    `test_raw_mode_adds_unknown_clusters_but_not_administrative_ones` (raw
    mode) and in `test_gate_blocks_administrative_cluster_even_with_table_entry`
    below, which pins the gate independently of fixture data.
    """
    commands = extract_commands(load("ikea_grillplats_plug.json"))
    assert not any(c.cluster_id in ADMINISTRATIVE_CLUSTERS for c in commands)


def test_raw_mode_adds_unknown_clusters_but_not_administrative_ones():
    """Raw mode extends the allow list - it does not lift the safety rule."""
    plug = load("ikea_grillplats_plug.json")
    roh = extract_commands(plug, raw=True)
    assert not any(c.cluster_id in ADMINISTRATIVE_CLUSTERS for c in roh)
    assert len(roh) > len(extract_commands(plug))
    assert any(c.cluster_id == 4 for c in roh)  # Groups, unknown but harmless


def test_raw_mode_names_unknown_commands_generically():
    roh = extract_commands(load("ikea_grillplats_plug.json"), raw=True)
    unbekannt = next(c for c in roh if c.cluster_id == 4)
    assert unbekannt.slug.startswith("c4_cmd")


def test_gate_blocks_administrative_cluster_even_with_table_entry(monkeypatch):
    """Pins the ADMINISTRATIVE_CLUSTERS gate itself, independently of fixture data.

    Cluster 62 (OperationalCredentials) gets a real `commands` entry in the
    profile table for this test - in normal mode `command_slug()` would then
    find the command and `extract_commands()` would emit it, were the
    ADMINISTRATIVE_CLUSTERS check in `extract_commands()` no longer there. If
    this test fails, the gate was removed or bypassed - regardless of whether
    `clusters.yaml` happens to stay empty for administrative clusters.
    """
    assert 62 in ADMINISTRATIVE_CLUSTERS
    patched = dict(table._table())
    patched[62] = {"commands": {10: {"slug": "remove_fabric", "takes_value": False}}}
    monkeypatch.setattr(table, "_table", lambda: patched)

    path = f"1/62/{ACCEPTED_COMMAND_LIST_ID}"
    snapshot = NodeSnapshot(
        node_id=999,
        vendor_name="test",
        product_name="test",
        unique_id="test",
        attributes={path: [10]},
    )

    assert extract_commands(snapshot) == []
    assert extract_commands(snapshot, raw=True) == []
