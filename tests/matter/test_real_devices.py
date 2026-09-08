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

"""Checks spec 3.5 against snapshots of real devices.

If one of these tests fails, the test is not wrong — the generic
decomposition does not hold, and the spec must change.
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
    assert REAL_DEVICES, "task 7 step 2 was not run — no real snapshots present"


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
    """Switches are the special case from spec 6.3 — without one the assumption is half-checked.

    The IKEA BILRESA switch (node 4) reports no EventList; its events come
    exclusively from the FeatureMap derivation in discovery.py.
    """
    with_events = [
        p for p in REAL_DEVICES if any(s.kind is SignalKind.EVENT for s in extract_signals(load(p)))
    ]
    assert with_events, "no captured device delivers events — switch missing"


def test_at_least_one_fixture_carries_energy_measurement():
    """Spec 7.3: metering plug, cluster 144 ElectricalPowerMeasurement."""
    with_energy = [
        p for p in REAL_DEVICES if any(s.cluster_id == 144 for s in extract_signals(load(p)))
    ]
    assert with_energy, "no captured device measures power"
