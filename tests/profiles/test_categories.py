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

"""Coarse device category from the Matter device types."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot
from loxmatter.profiles.categories import (
    CATEGORY_BY_DEVICE_TYPE,
    CATEGORY_RANK,
    Category,
    category_for,
)
from loxmatter.profiles.relevance import device_types_by_endpoint

# Same path to the snapshots as in `test_relevance.py` next door:
# `tests/profiles/` has no `conftest.py`, and `load_snapshot` from
# `tests/api/conftest.py` can't be imported from here - the two directories
# don't share a `sys.path` entry.
FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load_snapshot(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_the_rank_follows_the_declaration_order():
    """The rank is hardcoded and NOT the alphabetical order of the
    translated names: a language switch would otherwise reorder the
    groups, and a view that's laid out differently per language needs
    explaining twice."""
    assert [c.value for c in Category] == [
        "light",
        "socket",
        "switch",
        "covering",
        "climate",
        "sensor",
        "lock",
        "other",
    ]
    assert CATEGORY_RANK[Category.LIGHT] == 0
    assert CATEGORY_RANK[Category.OTHER] == 7


def test_the_plug_fixture_is_a_socket():
    """Endpoint 0 carries Root Node and OTA Requestor, endpoint 1 the
    On/Off Plug-in Unit (0x010A) - the management endpoint is
    skipped."""
    types = device_types_by_endpoint(load_snapshot("ikea_grillplats_plug.json"))
    assert category_for(types) is Category.SOCKET


def test_the_button_fixture_is_a_switch():
    types = device_types_by_endpoint(load_snapshot("ikea_bilresa_button.json"))
    assert category_for(types) is Category.SWITCH


def test_the_color_light_fixture_is_a_light():
    types = device_types_by_endpoint(load_snapshot("ikea_kajplats_cws_lamp.json"))
    assert category_for(types) is Category.LIGHT


def test_a_snapshot_without_descriptors_is_other():
    """`example_light.json` reports not a single descriptor attribute -
    exactly the state an existing device that hasn't been backfilled yet
    is also in."""
    types = device_types_by_endpoint(load_snapshot("example_light.json"))
    assert category_for(types) is Category.OTHER


def test_none_and_empty_are_other():
    assert category_for(None) is Category.OTHER
    assert category_for({}) is Category.OTHER


def test_only_utility_types_are_other():
    """Root Node, OTA Requestor and PowerSource say nothing about what the
    device does in the house - if nothing else is left, the category is
    "Other", not the one of the management endpoint."""
    assert category_for({0: frozenset({0x0016, 0x0012, 0x0011})}) is Category.OTHER


def test_the_lowest_non_utility_endpoint_decides():
    """In Matter, endpoint 1 is usually the application endpoint. A second
    endpoint with a different type must not override it."""
    types = {
        0: frozenset({0x0016}),
        1: frozenset({0x010A}),
        2: frozenset({0x0302}),
    }
    assert category_for(types) is Category.SOCKET


def test_several_types_on_one_endpoint_resolve_by_rank():
    """So the result doesn't depend on the order in which the device
    enumerates its types - a `frozenset` has none anyway."""
    assert category_for({1: frozenset({0x010A, 0x0100})}) is Category.LIGHT


def test_an_unknown_device_type_is_other():
    assert category_for({1: frozenset({0x0FFF})}) is Category.OTHER


@pytest.mark.parametrize(
    ("device_type", "expected"),
    [
        (0x0100, Category.LIGHT),
        (0x010D, Category.LIGHT),
        (0x010A, Category.SOCKET),
        (0x010B, Category.SOCKET),
        (0x000F, Category.SWITCH),
        (0x0104, Category.SWITCH),
        (0x0202, Category.COVERING),
        (0x0301, Category.CLIMATE),
        (0x002B, Category.CLIMATE),
        (0x0302, Category.SENSOR),
        (0x0107, Category.SENSOR),
        (0x000A, Category.LOCK),
    ],
)
def test_the_table_maps_the_types_it_claims_to(device_type, expected):
    assert CATEGORY_BY_DEVICE_TYPE[device_type] is expected


def test_every_mapped_type_exists_in_the_matter_table():
    """The mapping must be backed per ID by matter-server's
    machine-generated table, not guessed - the exact source `relevance.py`
    also relies on. A typo in an ID shows up here rather than only on a
    real device."""
    from matter_server.client.models.device_types import ALL_TYPES

    unknown = sorted(hex(t) for t in CATEGORY_BY_DEVICE_TYPE if t not in ALL_TYPES)
    assert unknown == []
