"""Gerätetypen je Endpunkt aus dem Descriptor-Cluster."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.profiles.relevance import (
    OTA_REQUESTOR_DEVICE_TYPE,
    POWER_SOURCE_DEVICE_TYPE,
    ROOT_NODE_DEVICE_TYPE,
    device_types_by_endpoint,
    is_functional,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"

_KIND = SignalKind.ATTRIBUTE


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


_PLUG_TYPES = {0: frozenset({18, 22}), 1: frozenset({266}), 2: frozenset({1296})}
_BUTTON_TYPES = {0: frozenset({17, 18, 22}), 1: frozenset({15}), 2: frozenset({15})}


def test_a_thread_diagnostics_counter_on_the_root_endpoint_is_not_functional():
    ref = SignalRef(0, 53, 4, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _PLUG_TYPES) is False


def test_the_battery_level_on_a_root_endpoint_is_functional():
    """Der Ausnahmefall, den der Descriptor selbst begruendet: der Taster
    deklariert auf Endpunkt 0 zusaetzlich Power Source."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _BUTTON_TYPES) is True


def test_the_battery_cluster_is_not_functional_where_no_power_source_is_declared():
    """Dieselbe Cluster-Nummer auf einem Endpunkt ohne Power-Source-Typ
    bleibt Verwaltung. Die Regel haengt am deklarierten Geraetetyp, nicht an
    der Cluster-Nummer - sonst waere sie doch wieder nur eine Liste."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _PLUG_TYPES) is False


def test_onoff_on_an_application_endpoint_is_functional():
    assert is_functional(SignalRef(1, 6, 0, _KIND), _PLUG_TYPES) is True


def test_a_generic_attribute_of_a_known_cluster_is_not_functional():
    """StartUpOnOff (0x4003) sitzt legitim bei OnOff, will aber niemand in
    Loxone. Die Tabelle kennt Cluster 6 und benennt dort nur Attribut 0."""
    assert is_functional(SignalRef(1, 6, 0x4003, _KIND), _PLUG_TYPES) is False


def test_every_attribute_of_an_unknown_cluster_stays_functional():
    """Die Grundwette des Projekts (Hauptdokument 3.5): ein Geraetetyp, den
    dieses Werkzeug nie gesehen hat, funktioniert trotzdem. Waere das hier
    falsch, laege ein fremdes Geraet stumm - ohne dass jemand merkte, dass
    etwas fehlt."""
    assert is_functional(SignalRef(1, 4711, 99, _KIND), _PLUG_TYPES) is True


def test_identify_groups_and_descriptor_are_never_functional():
    for cluster_id in (3, 4, 29):
        assert is_functional(SignalRef(1, cluster_id, 0, _KIND), _PLUG_TYPES) is False


def test_an_endpoint_without_a_declared_type_counts_as_an_application_endpoint():
    """Im Zweifel ein Eingang zu viel, nie ein fehlender Wert."""
    assert is_functional(SignalRef(9, 4711, 0, _KIND), _PLUG_TYPES) is True


def test_events_of_a_known_cluster_stay_functional():
    """Ein verworfenes Ereignis waere ein Tastendruck, der in Loxone nie
    ankommt - die erste Anforderung dieses Projekts ueberhaupt."""
    for event_id in (1, 2, 3, 4, 5, 6):
        ref = SignalRef(1, 59, event_id, SignalKind.EVENT)
        assert is_functional(ref, _BUTTON_TYPES) is True


def test_an_event_on_a_utility_endpoint_with_an_unknown_cluster_is_not_functional():
    """Ein Ereignis auf dem Verwaltungs-Endpunkt ist nicht funktional, auch wenn es
    ein Ereignis ist. Der Docstring saegt 'Ereignisse unterliegen Schicht 3 nicht',
    aber ein Leser koennte das falsch verstehen als 'Ereignisse werden nicht von
    Schicht 3 gefiltert' und ueberseht dadurch die Filterung durch Schicht 2
    (Verwaltungs-Endpunkte). Wenn jemand spaeter den Sonderfall fuer Ereignisse
    nach vorne zieht, um den Code zu 'vereinfachen', gibt das stillschweigend
    Diagnose-Ereignisse des Verwaltungs-Endpunkts frei - ohne dass ein Test anschlaegt.
    Dieser Test stellt sicher, dass das nicht passiert."""
    ref = SignalRef(0, 51, 0, SignalKind.EVENT)  # Cluster 51: GeneralDiagnostics
    assert is_functional(ref, _PLUG_TYPES) is False


def test_an_unnamed_power_source_attribute_on_the_utility_endpoint_is_not_functional():
    """Aufgabe 6, gefunden beim Verdrahten: Schicht 2 gab bisher bei einem
    zutreffenden `UTILITY_ENDPOINT_KEEP_CLUSTERS`-Eintrag den ganzen Cluster
    frei, statt wie Schicht 3 nur dessen benannte Elemente. `clusters.yaml`
    ist eindeutig ("Nur dieses eine von 37 Attributen ist benannt") - der
    Taster meldet unter 0/47/0 (BatChargeLevel) tatsaechlich einen Wert, und
    der durfte trotzdem nicht durchrutschen, nur weil derselbe Cluster auch
    den Batteriestand traegt."""
    ref = SignalRef(0, 47, 0, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _BUTTON_TYPES) is False
