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

"""Thread or IP, read from NetworkCommissioning's FeatureMap (design
2026-09-11, section 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot
from loxmatter.profiles.transport import network_features_of, transport_for

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def _fixture(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.mark.parametrize(
    ("technology", "features", "expected"),
    [
        # Measured on the test Pi, 11 September 2026:
        ("matter", 2, "thread"),  # every IKEA device, all on Thread
        ("matter", 4, "ip"),  # Tasmota-Plug-4, on Wi-Fi, reports Ethernet
        ("matter", 5, "ip"),  # Tasmota-Plug-6, on Wi-Fi, reports Wi-Fi + Ethernet
        # Not measured, follow from the bit layout:
        ("matter", 1, "ip"),  # Wi-Fi bit alone
        ("matter", 3, "thread"),  # Thread wins over an IP bit
        ("matter", 6, "thread"),
        ("matter", 0, None),  # no network interface bit at all
        ("matter", None, None),  # attribute missing
        ("zigbee", None, "zigbee"),
        ("zigbee", 2, "zigbee"),  # a Zigbee device's features are irrelevant
    ],
)
def test_transport_for(technology, features, expected):
    assert transport_for(technology, features) == expected


def test_network_features_are_read_from_a_real_thread_device():
    assert network_features_of(_fixture("ikea_kajplats_ws_lamp.json")) == 2


def test_a_device_without_network_commissioning_has_no_features():
    assert network_features_of(_fixture("example_light.json")) is None


@pytest.mark.parametrize("value", [True, "2", 2.0, None, [2]])
def test_only_a_plain_integer_counts_as_features(value):
    snapshot = NodeSnapshot.from_raw(1, {"attributes": {"0/49/65532": value}})
    assert network_features_of(snapshot) is None
