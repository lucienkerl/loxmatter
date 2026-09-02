"""Prüft Spec 3.5 gegen Abbilder echter Geräte.

Schlägt einer dieser Tests fehl, ist nicht der Test falsch — dann trägt die
generische Zerlegung nicht, und die Spec muss geändert werden.
"""

import json
from pathlib import Path

import pytest

from loxmatter.matter.discovery import (
    extract_signals,
    find_unparsable_paths,
    find_unreported_attributes,
)
from loxmatter.matter.models import NodeSnapshot, SignalKind

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "nodes"
REAL_DEVICES = sorted(p for p in FIXTURE_DIR.glob("*.json") if not p.name.startswith("example_"))


def load(path: Path) -> NodeSnapshot:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_real_device_fixtures_exist():
    assert REAL_DEVICES, "Task 7 Schritt 2 wurde nicht ausgeführt — keine echten Abbilder da"


@pytest.mark.parametrize("path", REAL_DEVICES, ids=lambda p: p.stem)
def test_every_path_is_parsable(path):
    assert find_unparsable_paths(load(path)) == []


@pytest.mark.parametrize("path", REAL_DEVICES, ids=lambda p: p.stem)
def test_no_claimed_attribute_is_missing(path):
    assert find_unreported_attributes(load(path)) == []


@pytest.mark.parametrize("path", REAL_DEVICES, ids=lambda p: p.stem)
def test_device_yields_at_least_one_signal(path):
    assert extract_signals(load(path))


def test_at_least_one_fixture_carries_events():
    """Taster sind der Sonderfall aus Spec 6.3 — ohne sie ist die Annahme halb geprüft.

    Der IKEA BILRESA-Taster (node 4) führt keine EventList; die Events kommen
    ausschließlich über die FeatureMap-Ableitung in discovery.py.
    """
    with_events = [
        p for p in REAL_DEVICES if any(s.kind is SignalKind.EVENT for s in extract_signals(load(p)))
    ]
    assert with_events, "kein aufgenommenes Gerät liefert Events — Taster fehlt"


def test_at_least_one_fixture_carries_energy_measurement():
    """Spec 7.3: messende Steckdose, Cluster 144 ElectricalPowerMeasurement."""
    with_energy = [
        p for p in REAL_DEVICES if any(s.cluster_id == 144 for s in extract_signals(load(p)))
    ]
    assert with_energy, "kein aufgenommenes Gerät misst Leistung"
