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

from loxmatter import i18n
from loxmatter.profiles import endpoints


def test_two_button_endpoints_are_numbered():
    """Der Fall, wegen dessen es dieses Modul gibt: die Fernbedienung traegt
    denselben Geraetetyp auf zwei Endpunkten. Ohne Nummerierung stuenden im
    Modal zwei Gruppen namens "Taste", und `press` waere weiter zweimal
    dasselbe Wort ohne Auskunft, welche Taste gemeint ist."""
    i18n.set_language("de")
    labels = endpoints.endpoint_labels(
        {0: frozenset({0x0016, 0x0011}), 1: frozenset({0x000F}), 2: frozenset({0x000F})}
    )
    assert labels[1] == "Taste 1"
    assert labels[2] == "Taste 2"


def test_a_single_endpoint_of_a_type_is_not_numbered():
    """Eine Steckdose hat genau einen Nutz-Endpunkt. "Steckdose 1" waere
    eine Nummer ohne Gegenstueck."""
    i18n.set_language("de")
    labels = endpoints.endpoint_labels({0: frozenset({0x0016}), 1: frozenset({0x010A})})
    assert labels[1] == "Steckdose"


def test_a_utility_endpoint_is_called_the_device():
    i18n.set_language("de")
    labels = endpoints.endpoint_labels({0: frozenset({0x0016, 0x0011})})
    assert labels[0] == "Gerät"


def test_an_unmapped_type_falls_back_to_the_endpoint_number():
    """Die Tabelle ist bewusst klein und deckt nur belegte Geraetetypen ab.
    Alles andere bekommt einen Namen, der immer stimmt."""
    i18n.set_language("de")
    labels = endpoints.endpoint_labels({3: frozenset({0x0302})})
    assert labels[3] == "Endpunkt 3"


def test_device_types_never_backfilled_yields_an_empty_mapping():
    """`device.device_types` ist `NULL`, solange `backfill_device_types`
    nicht lief (siehe `_migrate_to_v7`). Das ist kein Fehlerfall, sondern
    derselbe Ruecktritt wie bei `category_for(None)` - und diese Funktion
    hier schuldet dafuer nur ein leeres Woerterbuch, keinen Rueckfalltext:
    den bildet der Aufrufer (`api/devices._signal_out`), der `labels.get(...)`
    ohnehin gegen einen fehlenden Endpunkt absichern muss (siehe dort und
    `tests/api/test_devices.py`). Ein testeigener Helfer, der diesen
    Ruecktritt hier nachbaut, wuerde nur die eigene Kopie der Regel pruefen,
    nicht die ausgelieferte Stelle - das war frueher hier der Fall."""
    assert endpoints.endpoint_labels(None) == {}


def test_every_mapped_type_exists_in_the_matter_table():
    """Dieselbe Absicherung wie `test_categories.py` sie fuer
    CATEGORY_BY_DEVICE_TYPE hat: eine Nummer aus dem Gedaechtnis statt aus
    der Spezifikation faellt sonst nie auf."""
    from matter_server.client.models.device_types import ALL_TYPES

    unknown = sorted(
        hex(t) for t in endpoints.ENDPOINT_NAME_KEY_BY_DEVICE_TYPE if t not in ALL_TYPES
    )
    assert unknown == []
