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

"""Checks the table against the real devices from phase 1."""

import json
from pathlib import Path

from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot
from loxmatter.profiles.table import Exportability, lookup

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_plug_matches_the_breakdown_recorded_in_spec_6_6():
    """Spec 6.6, table: 102 analog, 7 digital, 13 text, 37 not mappable -
    plus task 5: the meter reading (2/145/1) is a struct with a numeric
    element and has since moved from NONE to ANALOG (103/36)."""
    snap = load("ikea_grillplats_plug.json")
    signals = extract_signals(snap)
    counts = {kind: 0 for kind in Exportability}
    for ref in signals:
        counts[lookup(ref, snap.attributes.get(ref.path)).exportability] += 1

    assert len(signals) == 159
    assert counts[Exportability.ANALOG] == 103
    assert counts[Exportability.DIGITAL] == 7
    assert counts[Exportability.TEXT] == 13
    assert counts[Exportability.NONE] == 36  # 32 lists/structs - 1 + 5 null values


def test_only_110_of_the_plugs_signals_reach_a_udp_input():
    """Not 45 but 49 drop out - the 5 null values are added to the
    remaining 44 lists/structs (task 5 pulls the meter reading out of its
    struct and makes it mappable)."""
    snap = load("ikea_grillplats_plug.json")
    exportable = [
        ref
        for ref in extract_signals(snap)
        if lookup(ref, snap.attributes.get(ref.path)).exportability
        in (Exportability.ANALOG, Exportability.DIGITAL)
    ]
    assert len(exportable) == 110


def test_plug_power_attribute_carries_kw():
    snap = load("ikea_grillplats_plug.json")
    ref = next(s for s in extract_signals(snap) if s.cluster_id == 144 and s.element_id == 8)
    assert lookup(ref, snap.attributes.get(ref.path)).unit == "kW"


def test_every_button_event_is_named():
    snap = load("ikea_bilresa_button.json")
    events = [s for s in extract_signals(snap) if s.cluster_id == 59 and s.kind.value == "event"]
    assert len(events) == 12
    assert all(not lookup(e, None).slug.startswith("c59_e") for e in events)
