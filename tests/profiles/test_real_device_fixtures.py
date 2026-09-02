"""Prueft die Tabelle an den echten Geraeten aus Phase 1."""

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
    """Spec 6.6, Tabelle: 102 analog, 7 digital, 13 Text, 37 nicht abbildbar."""
    snap = load("ikea_grillplats_plug.json")
    signals = extract_signals(snap)
    zaehlung = {kind: 0 for kind in Exportability}
    for ref in signals:
        zaehlung[lookup(ref, snap.attributes.get(ref.path)).exportability] += 1

    assert len(signals) == 159
    assert zaehlung[Exportability.ANALOG] == 102
    assert zaehlung[Exportability.DIGITAL] == 7
    assert zaehlung[Exportability.TEXT] == 13
    assert zaehlung[Exportability.NONE] == 37  # 32 Listen/Structs + 5 Nullwerte


def test_only_109_of_the_plugs_signals_reach_a_udp_input():
    """Nicht 45, sondern 50 fallen weg - die 5 Nullwerte kommen zu den 45 dazu."""
    snap = load("ikea_grillplats_plug.json")
    abbildbar = [
        ref
        for ref in extract_signals(snap)
        if lookup(ref, snap.attributes.get(ref.path)).exportability
        in (Exportability.ANALOG, Exportability.DIGITAL)
    ]
    assert len(abbildbar) == 109


def test_plug_power_attribute_carries_kw():
    snap = load("ikea_grillplats_plug.json")
    ref = next(s for s in extract_signals(snap) if s.cluster_id == 144 and s.element_id == 8)
    assert lookup(ref, snap.attributes.get(ref.path)).unit == "kW"


def test_every_button_event_is_named():
    snap = load("ikea_bilresa_button.json")
    events = [s for s in extract_signals(snap) if s.cluster_id == 59 and s.kind.value == "event"]
    assert len(events) == 12
    assert all(not lookup(e, None).slug.startswith("c59_e") for e in events)
