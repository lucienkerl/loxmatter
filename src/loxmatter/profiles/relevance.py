"""Welche Signale ein Anwender standardmaessig will (Entwurf 2026-09-03, 4.1).

Getrennt von `Exportability` und mit Absicht in einem eigenen Modul: die
Frage "laesst sich der Wert auf einen UDP-Eingang abbilden" (table.py) und
die Frage "will ihn jemand" sind verschiedene Fragen mit verschiedenen
Antworten. Ein Thread-Funkzaehler ist exportierbar, aber nicht relevant.

Die Auswahl stuetzt sich nicht auf eine Liste von Cluster-Nummern, die
jemand fuer langweilig haelt, sondern auf Matters eigenen Aufbau: der
Descriptor-Cluster traegt auf jedem Endpunkt eine standardisierte
Geraetetyp-Liste. Ein Geraet ohne diese Angabe wird nicht zertifiziert -
die Regel traegt damit fuer jeden Hersteller und jeden Geraetetyp, auch
fuer solche, die dieses Werkzeug nie gesehen hat.
"""

from __future__ import annotations

from typing import Any

from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.matter.paths import parse_attribute_path
from loxmatter.profiles.table import knows_cluster, names_element

DESCRIPTOR_CLUSTER_ID = 29
DEVICE_TYPE_LIST_ID = 0

# Quelle: `matter_server.client.models.device_types` (installiert unter
# .venv/lib/python3.12/site-packages/matter_server/client/models/
# device_types.py), laut eigenem Modul-Docstring maschinell erzeugt aus
# `zcl/data-model/chip/matter-devices.xml` der CSA-Spezifikation. Das
# installierte chip-SDK selbst (chip.clusters.Objects) enthaelt dagegen nur
# die Cluster-Struktur des Descriptor-Attributs `DeviceTypeList`, keine
# Tabelle der Geraetetyp-Nummern (in Task 1 geprueft). Gegengeprueft an den
# eingecheckten Abbildern tests/fixtures/nodes/ikea_grillplats_plug.json und
# ikea_bilresa_button.json: deren "<endpoint>/29/0"-Werte enthalten genau
# diese drei Nummern wie erwartet.
ROOT_NODE_DEVICE_TYPE = 0x0016
OTA_REQUESTOR_DEVICE_TYPE = 0x0012
POWER_SOURCE_DEVICE_TYPE = 0x0011

UTILITY_DEVICE_TYPES: frozenset[int] = frozenset({ROOT_NODE_DEVICE_TYPE, OTA_REQUESTOR_DEVICE_TYPE})


def _device_type_ids(raw: object) -> frozenset[int]:
    """Die Geraetetyp-Nummern aus einem `DeviceTypeList`-Wert.

    matter-server liefert Strukturen als Woerterbuch mit dem Feld-Tag als
    ZEICHENKETTE, nicht mit dem Feldnamen: eine DeviceTypeStruct kommt als
    ``{"0": <Typ>, "1": <Revision>}`` an. Beides - Zeichenkette und Zahl -
    wird akzeptiert, weil eine andere Serialisierung dieselbe Struktur
    genauso plausibel als ``{0: ...}`` liefern koennte.

    Alles Unerwartete ergibt eine leere Menge statt einer Ausnahme: ein
    nicht konformes Geraet soll die Zerlegung nicht anhalten.
    """
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    ids: set[int] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        value: Any = entry.get("0", entry.get(0))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        ids.add(int(value))
    return frozenset(ids)


def device_types_by_endpoint(snapshot: NodeSnapshot) -> dict[int, frozenset[int]]:
    """Die deklarierten Geraetetypen je Endpunkt.

    Ein Endpunkt ohne Descriptor taucht gar nicht auf - der Aufrufer
    unterscheidet damit "nicht gemeldet" von "gemeldet, aber leer", auch
    wenn beide spaeter zur selben Entscheidung fuehren.
    """
    result: dict[int, frozenset[int]] = {}
    for path, value in snapshot.attributes.items():
        try:
            endpoint, cluster_id, attribute_id = parse_attribute_path(path)
        except ValueError:
            continue
        if cluster_id != DESCRIPTOR_CLUSTER_ID or attribute_id != DEVICE_TYPE_LIST_ID:
            continue
        result[endpoint] = _device_type_ids(value)
    return result


# Auf jedem Endpunkt Verwaltung, unabhaengig vom Geraetetyp: Identify
# (Blinken zur Identifikation), Groups (Matter-Gruppenverwaltung) und der
# Descriptor selbst. Keiner davon hat eine Bedeutung fuer eine
# Hausautomation.
BOILERPLATE_CLUSTERS: frozenset[int] = frozenset({3, 4, DESCRIPTOR_CLUSTER_ID})

# Cluster, die auf einem Verwaltungs-Endpunkt dennoch gewollt sind - aber
# nur, wenn das Geraet den zugehoerigen Nutz-Geraetetyp dort auch
# deklariert. Der Batteriestand ist der Fall, der das noetig macht.
#
# WICHTIG: Das ist KEINE vollstaendige Nachschlagetabelle aller Geraetetypen
# und ihrer Cluster - nur die Ausnahmen, die an echten Geraeten tatsaechlich
# vorkommen. Ein neuer Fall erfordert eine konkrete Belegung (Link zum
# Geraet oder zur Spezifikation), nicht die Annahme, dass die Tabelle
# von sich aus vollstaendig waere.
UTILITY_ENDPOINT_KEEP_CLUSTERS: dict[int, int] = {
    47: POWER_SOURCE_DEVICE_TYPE,  # PowerSource
}


def is_functional(ref: SignalRef, device_types: dict[int, frozenset[int]]) -> bool:
    """Ob dieses Signal standardmaessig gewollt ist (Entwurf 2026-09-03, 4).

    Drei Schichten, in dieser Reihenfolge:

    1. Boilerplate-Cluster sind nie gewollt, auf keinem Endpunkt.
    2. Auf einem Verwaltungs-Endpunkt (Root Node oder OTA Requestor) ist
       nur ueberhaupt in Betracht, was zu einem dort ebenfalls deklarierten
       Nutz-Geraetetyp gehoert - alles andere scheidet hier sofort aus.
    3. Was Schicht 2 durchlaesst (und jeder Nutz-Endpunkt ohnehin) ist
       gewollt - ausser bei einem Cluster, den die Profiltabelle kennt: dort
       nur die benannten Elemente. Ein unbekannter Cluster bleibt
       vollstaendig gewollt (Hauptdokument 3.5).

    Wichtig: Schicht 2 gewaehrt nur den Cluster einen Platz auf dem
    Verwaltungs-Endpunkt, nicht schon jedes seiner Elemente - Schicht 3
    filtert innerhalb dieses Clusters genauso wie bei einem Nutz-Endpunkt.
    Der Taster deklariert Power Source auf Endpunkt 0 wegen des
    Batteriestands (Element 12), traegt denselben Cluster 47 aber mit 36
    weiteren, nicht benannten Attributen (Ladezustand, Batteriechemie, ANSI-
    Bezeichnungen, Fehlerlisten) - ohne diesen zweiten Filter waeren alle 37
    "gewollt", nur weil einer von ihnen es ist (Aufgabe 6, siehe
    `test_an_unnamed_power_source_attribute_on_the_utility_endpoint_is_not_functional`).

    Ereignisse unterliegen dem Cluster-Tabellen-Filter aus Schicht 3 nicht:
    sie sind in der Tabelle ohnehin namentlich gefuehrt, und ein verworfenes
    Ereignis waere ein Tastendruck, der in Loxone nie ankommt.
    """
    if ref.cluster_id in BOILERPLATE_CLUSTERS:
        return False

    declared = device_types.get(ref.endpoint, frozenset())
    if declared & UTILITY_DEVICE_TYPES:
        required = UTILITY_ENDPOINT_KEEP_CLUSTERS.get(ref.cluster_id)
        if required is None or required not in declared:
            return False

    if ref.kind is SignalKind.EVENT:
        return True
    if knows_cluster(ref.cluster_id):
        return names_element(ref)
    return True
