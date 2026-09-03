"""Gerätetypen je Endpunkt aus dem Descriptor-Cluster."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot
from loxmatter.profiles.relevance import (
    OTA_REQUESTOR_DEVICE_TYPE,
    POWER_SOURCE_DEVICE_TYPE,
    ROOT_NODE_DEVICE_TYPE,
    device_types_by_endpoint,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def _snapshot(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text())
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_the_plug_declares_a_utility_endpoint_and_two_application_endpoints():
    types = device_types_by_endpoint(_snapshot("ikea_grillplats_plug.json"))
    assert ROOT_NODE_DEVICE_TYPE in types[0]
    assert OTA_REQUESTOR_DEVICE_TYPE in types[0]
    assert ROOT_NODE_DEVICE_TYPE not in types[1]
    assert ROOT_NODE_DEVICE_TYPE not in types[2]


def test_the_button_declares_a_power_source_on_its_utility_endpoint():
    """Der Batteriestand liegt nicht zufaellig auf Endpunkt 0 - das Geraet
    deklariert dort den Geraetetyp Power Source. Genau darauf stuetzt sich
    die Ausnahme in Task 2; ohne diese Zusicherung waere sie geraten."""
    types = device_types_by_endpoint(_snapshot("ikea_bilresa_button.json"))
    assert POWER_SOURCE_DEVICE_TYPE in types[0]


def test_an_endpoint_without_a_descriptor_is_absent_rather_than_empty():
    """Fehlt der Descriptor, soll der Aufrufer das unterscheiden koennen von
    'Descriptor da, aber leer' - beides fuehrt spaeter zur selben
    Entscheidung, aber aus verschiedenen Gruenden."""
    snapshot = NodeSnapshot.from_raw(1, {"attributes": {"7/6/0": True}})
    assert device_types_by_endpoint(snapshot) == {}


@pytest.mark.parametrize(
    "raw",
    [
        "kein Wörterbuch",
        [{"1": 3}],
        [{"0": "keine Zahl"}],
        [None],
        42,
    ],
)
def test_an_unexpected_descriptor_shape_yields_no_device_types(raw):
    """Ein nicht konformes Geraet darf keinen Absturz ausloesen. Der
    Endpunkt gilt dann als typlos - und damit spaeter (Task 2) als
    Nutz-Endpunkt: im Zweifel ein Eingang zu viel, nie ein fehlender Wert."""
    snapshot = NodeSnapshot.from_raw(1, {"attributes": {"0/29/0": raw}})
    assert device_types_by_endpoint(snapshot) == {0: frozenset()}
