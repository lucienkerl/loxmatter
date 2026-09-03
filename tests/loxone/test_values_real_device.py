"""Prueft die Skalierung an der aufgezeichneten Steckdose."""

import json
from pathlib import Path

import pytest

from loxmatter.loxone.values import format_value, to_loxone_value
from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def plug() -> NodeSnapshot:
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_mains_voltage_lands_near_230_volt():
    """2/144/4 ist RMSVoltage in mV - die Steckdose hing an 230 V."""
    snap = plug()
    ref = next(s for s in extract_signals(snap) if s.cluster_id == 144 and s.element_id == 4)
    assert to_loxone_value(ref, snap.attributes[ref.path]) == pytest.approx(230.0)


def test_exactly_110_signals_yield_a_value():
    """Spec 6.6: von 159 Attributsignalen erreichen 110 einen UDP-Eingang -
    seit Aufgabe 5 zaehlt der aus der Struktur gezogene Zaehlerstand mit."""
    snap = plug()
    values = [to_loxone_value(s, snap.attributes.get(s.path)) for s in extract_signals(snap)]
    assert sum(1 for v in values if v is not None) == 110


def test_no_value_formats_to_scientific_notation():
    """Loxone kann "1e-05" nicht lesen - das waere ein stiller Ausfall."""
    snap = plug()
    for ref in extract_signals(snap):
        value = to_loxone_value(ref, snap.attributes.get(ref.path))
        if value is not None:
            assert "e" not in format_value(value).lower()
