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

from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.paths import parse_attribute_path

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
