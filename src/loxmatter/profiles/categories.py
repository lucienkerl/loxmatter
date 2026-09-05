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

"""Grobe Geraetekategorie aus den Matter-Geraetetypen.

Beantwortet genau eine Frage, die `relevance.py` nicht beantwortet: nicht
"welche Signale will jemand sehen", sondern "was fuer ein Ding ist das
ueberhaupt". Die Antwort traegt in der Oberflaeche drei Dinge auf einmal -
die Sortierung innerhalb eines Raums, das Icon der Kachel und den
Suchbegriff, unter dem man alle Steckdosen des Hauses findet.

Warum daneben und nicht darin: `relevance.is_functional` entscheidet ueber
ein einzelnes Signal, `category_for` ueber ein ganzes Geraet. Beide lesen
dieselbe Quelle (`device_types_by_endpoint`), aber mit verschiedenem
Ausgang und ohne gemeinsamen Zustand.

**Die Quelle der Typ-Nummern** ist dieselbe wie in `relevance.py`:
`matter_server.client.models.device_types`, laut eigenem Modul-Docstring
maschinell erzeugt aus `zcl/data-model/chip/matter-devices.xml` der
CSA-Spezifikation. Ein neuer Eintrag in der Tabelle unten braucht die
Nummer aus dieser Datei, nicht aus dem Gedaechtnis;
`test_every_mapped_type_exists_in_the_matter_table` prueft das ab.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from loxmatter.profiles.relevance import POWER_SOURCE_DEVICE_TYPE, UTILITY_DEVICE_TYPES


class Category(str, Enum):
    """Die Reihenfolge dieser Deklaration IST der Sortierrang (siehe
    `CATEGORY_RANK`) - bewusst nicht die alphabetische Reihenfolge der
    uebersetzten Namen, die sich mit der Sprache aendern wuerde.

    Die Reihenfolge selbst folgt der Haeufigkeit, mit der man ein Geraet
    dieser Art in einem Raum anfasst: Licht und Steckdose zuerst, danach die
    Bedienelemente, ganz hinten das, was man einmal einrichtet und dann in
    Ruhe laesst. `OTHER` steht immer am Ende - dort landet auch jedes
    Geraet, dessen Typen noch nicht nachgetragen sind.

    `str, Enum` statt `StrEnum`, weil `Exportability` in `profiles/table.py`
    es genauso macht - eine zweite Schreibweise fuer dieselbe Sache waere
    ohne Gewinn."""

    LIGHT = "light"
    SOCKET = "socket"
    SWITCH = "switch"
    COVERING = "covering"
    CLIMATE = "climate"
    SENSOR = "sensor"
    LOCK = "lock"
    OTHER = "other"


CATEGORY_RANK: dict[Category, int] = {category: rank for rank, category in enumerate(Category)}

# Geraetetypen, die nichts darueber sagen, was das Geraet im Haus TUT -
# dieselbe Menge, die `relevance.is_functional` schon als Verwaltung
# behandelt, plus PowerSource: ein Batteriestand macht aus einem Taster
# keine eigene Kategorie.
_IGNORED_DEVICE_TYPES: frozenset[int] = UTILITY_DEVICE_TYPES | {POWER_SOURCE_DEVICE_TYPE}

# Zuordnung Matter-Geraetetyp -> Kategorie. Jede Nummer stammt aus
# `matter_server.client.models.device_types` (siehe Modul-Docstring); die
# Kommentare nennen den dortigen Klassennamen, damit ein Nachschlagen ohne
# Umrechnung moeglich ist.
#
# Nicht aufgefuehrt und damit `OTHER`: Haushaltsgeraete (0x0070-0x007C),
# Medien (0x0022-0x002A), Energie (0x050C-0x050F), Netzwerk-Infrastruktur
# (0x0090, 0x0091), Bruecken-Verwaltung (0x000E Aggregator, 0x0013 Bridged
# Node). Sie kommen an einer Loxone-Anbindung entweder gar nicht vor oder
# haetten in einer Raumliste keinen eigenen Rang verdient.
CATEGORY_BY_DEVICE_TYPE: dict[int, Category] = {
    0x0100: Category.LIGHT,  # OnOffLight
    0x0101: Category.LIGHT,  # DimmableLight
    0x010C: Category.LIGHT,  # ColorTemperatureLight
    0x010D: Category.LIGHT,  # ExtendedColorLight
    # MountedOnOffControl / MountedDimmableLoadControl sind fest verbaute
    # Lastschalter - in der Praxis sitzt dahinter eine Leuchte, nicht eine
    # Steckdose (die traegt einen eigenen Typ, siehe unten).
    0x010F: Category.LIGHT,  # MountedOnOffControl
    0x0110: Category.LIGHT,  # MountedDimmableLoadControl
    0x010A: Category.SOCKET,  # OnOffPlugInUnit
    0x010B: Category.SOCKET,  # DimmablePlugInUnit
    0x000F: Category.SWITCH,  # GenericSwitch
    0x0103: Category.SWITCH,  # OnOffLightSwitch
    0x0104: Category.SWITCH,  # DimmerSwitch
    0x0105: Category.SWITCH,  # ColorDimmerSwitch
    0x0840: Category.SWITCH,  # ControlBridge
    0x0202: Category.COVERING,  # WindowCovering
    0x0203: Category.COVERING,  # WindowCoveringController
    0x0300: Category.CLIMATE,  # HeatingCoolingUnit
    0x0301: Category.CLIMATE,  # Thermostat
    0x0309: Category.CLIMATE,  # HeatPump
    0x002B: Category.CLIMATE,  # Fan
    0x002D: Category.CLIMATE,  # AirPurifier
    0x0072: Category.CLIMATE,  # RoomAirConditioner
    0x0015: Category.SENSOR,  # ContactSensor
    0x002C: Category.SENSOR,  # AirQualitySensor
    0x0041: Category.SENSOR,  # WaterFreezeDetector
    0x0043: Category.SENSOR,  # WaterLeakDetector
    0x0044: Category.SENSOR,  # RainSensor
    0x0076: Category.SENSOR,  # SmokeCoAlarm
    0x0106: Category.SENSOR,  # LightSensor
    0x0107: Category.SENSOR,  # OccupancySensor
    0x0302: Category.SENSOR,  # TemperatureSensor
    0x0305: Category.SENSOR,  # PressureSensor
    0x0306: Category.SENSOR,  # FlowSensor
    0x0307: Category.SENSOR,  # HumiditySensor
    0x0510: Category.SENSOR,  # ElectricalSensor
    0x0850: Category.SENSOR,  # OnOffSensor
    0x000A: Category.LOCK,  # DoorLock
    0x000B: Category.LOCK,  # DoorLockController
}


def category_for(device_types: Mapping[int, frozenset[int]] | None) -> Category:
    """Die Kategorie eines Geraets aus seinen Geraetetypen je Endpunkt.

    `None` (Geraetetypen noch nicht nachgetragen, siehe
    `Store.backfill_device_types`) ergibt `OTHER` - dieselbe Antwort wie fuer
    ein Geraet, dessen Typen niemand zuordnen kann. Die Oberflaeche
    unterscheidet beide Faelle nicht: in beiden steht das Geraet vollstaendig
    bedienbar unter "Sonstige", der erste Fall behebt sich beim naechsten
    Bruueckenstart von selbst.

    Die Regel in vier Schritten (Entwurf 5.2):

    1. Verwaltungstypen fallen weg (`_IGNORED_DEVICE_TYPES`).
    2. Vom Rest zaehlt der NIEDRIGSTE Endpunkt - bei Matter ueblicherweise
       Endpunkt 1, der Anwendungs-Endpunkt. Eine Steckdose mit einem
       Temperaturfuehler auf Endpunkt 2 bleibt eine Steckdose.
    3. Traegt dieser Endpunkt mehrere zuordenbare Typen, gewinnt der mit dem
       niedrigsten Rang. Damit haengt das Ergebnis nicht daran, in welcher
       Reihenfolge das Geraet seine Typen aufzaehlt - ein `frozenset` hat
       ohnehin keine.
    4. Nichts Zuordenbares -> `OTHER`.
    """
    if not device_types:
        return Category.OTHER

    useful = {
        endpoint: ids - _IGNORED_DEVICE_TYPES
        for endpoint, ids in device_types.items()
        if ids - _IGNORED_DEVICE_TYPES
    }
    if not useful:
        return Category.OTHER

    primary = useful[min(useful)]
    mapped = [CATEGORY_BY_DEVICE_TYPE[t] for t in primary if t in CATEGORY_BY_DEVICE_TYPE]
    if not mapped:
        return Category.OTHER
    return min(mapped, key=lambda category: CATEGORY_RANK[category])
