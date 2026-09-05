# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

"""Grobe Geraetekategorie aus den Matter-Geraetetypen."""

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

# Derselbe Weg zu den Abbildern wie in `test_relevance.py` nebenan:
# `tests/profiles/` hat keine `conftest.py`, und `load_snapshot` aus
# `tests/api/conftest.py` ist von hier aus nicht importierbar - die beiden
# Verzeichnisse teilen keinen `sys.path`-Eintrag.
FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load_snapshot(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_the_rank_follows_the_declaration_order():
    """Der Rang ist fest verdrahtet und NICHT die alphabetische Reihenfolge
    der uebersetzten Namen: ein Sprachwechsel wuerde die Gruppen sonst
    umsortieren, und eine Ansicht, die je nach Sprache anders aufgebaut ist,
    ist zweimal zu erklaeren."""
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
    """Endpunkt 0 traegt Root Node und OTA Requestor, Endpunkt 1 die
    On/Off Plug-in Unit (0x010A) - der Verwaltungs-Endpunkt wird
    uebersprungen."""
    types = device_types_by_endpoint(load_snapshot("ikea_grillplats_plug.json"))
    assert category_for(types) is Category.SOCKET


def test_the_button_fixture_is_a_switch():
    types = device_types_by_endpoint(load_snapshot("ikea_bilresa_button.json"))
    assert category_for(types) is Category.SWITCH


def test_the_color_light_fixture_is_a_light():
    types = device_types_by_endpoint(load_snapshot("synthetic_color_light.json"))
    assert category_for(types) is Category.LIGHT


def test_a_snapshot_without_descriptors_is_other():
    """`example_light.json` meldet kein einziges Descriptor-Attribut - genau
    der Zustand, in dem auch ein noch nicht nachgetragenes Bestandsgeraet
    steht."""
    types = device_types_by_endpoint(load_snapshot("example_light.json"))
    assert category_for(types) is Category.OTHER


def test_none_and_empty_are_other():
    assert category_for(None) is Category.OTHER
    assert category_for({}) is Category.OTHER


def test_only_utility_types_are_other():
    """Root Node, OTA Requestor und PowerSource sagen nichts darueber, was
    das Geraet im Haus tut - bleibt nichts uebrig, ist die Kategorie
    "Sonstige", nicht etwa die des Verwaltungs-Endpunkts."""
    assert category_for({0: frozenset({0x0016, 0x0012, 0x0011})}) is Category.OTHER


def test_the_lowest_non_utility_endpoint_decides():
    """Bei Matter ist Endpunkt 1 ueblicherweise der Anwendungs-Endpunkt. Ein
    zweiter Endpunkt mit einem anderen Typ darf ihn nicht ueberstimmen."""
    types = {
        0: frozenset({0x0016}),
        1: frozenset({0x010A}),
        2: frozenset({0x0302}),
    }
    assert category_for(types) is Category.SOCKET


def test_several_types_on_one_endpoint_resolve_by_rank():
    """Damit das Ergebnis unabhaengig davon ist, in welcher Reihenfolge das
    Geraet seine Typen aufzaehlt - ein `frozenset` hat gar keine."""
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
    """Die Zuordnung muss pro ID gegen die maschinell erzeugte Tabelle von
    matter-server belegt sein, nicht geraten - genau die Quelle, auf die
    sich auch `relevance.py` beruft. Ein Tippfehler in einer ID faellt hier
    auf und nicht erst an einem echten Geraet."""
    from matter_server.client.models.device_types import ALL_TYPES

    unknown = sorted(hex(t) for t in CATEGORY_BY_DEVICE_TYPE if t not in ALL_TYPES)
    assert unknown == []
