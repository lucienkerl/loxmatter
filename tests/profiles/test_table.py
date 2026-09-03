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

import pytest

from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles.table import (
    _UNIT_DECIMALS,
    MAX_LOXONE_DECIMALS,
    Exportability,
    classify,
    is_exportable,
    known_attribute_section,
    lookup,
    names_element,
    scale_factor,
    unit_format,
)


def test_bool_is_digital():
    assert classify(True) is Exportability.DIGITAL


def test_numbers_are_analog():
    assert classify(0) is Exportability.ANALOG
    assert classify(-42) is Exportability.ANALOG
    assert classify(1.5) is Exportability.ANALOG


def test_strings_are_text():
    assert classify("IKEA of Sweden") is Exportability.TEXT


def test_lists_and_structs_are_not_exportable():
    """Spec 6.6: Loxone hat fuer verschachtelte Werte keine Entsprechung."""
    assert classify([29, 31, 40]) is Exportability.NONE
    assert classify([{"0": 5, "1": True}]) is Exportability.NONE
    assert classify({"0": 5}) is Exportability.NONE


def test_null_is_not_exportable():
    """Spec 6.6: gelieferte Nullwerte sind eine eigene Kategorie."""
    assert classify(None) is Exportability.NONE


def test_known_attribute_gets_name_and_unit():
    ref = SignalRef(1, 1026, 0, SignalKind.ATTRIBUTE)  # TemperatureMeasurement
    profile = lookup(ref, 2150)
    assert profile.slug == "temp"
    assert profile.unit == "°C"
    assert profile.exportability is Exportability.ANALOG


def test_power_is_named_and_carries_kw():
    """Spec 7.3: Zieleinheit ist die des Loxone-Bausteins, nicht die SI-Einheit."""
    ref = SignalRef(2, 144, 8, SignalKind.ATTRIBUTE)  # ActivePower
    profile = lookup(ref, 5000)
    assert profile.slug == "power"
    assert profile.unit == "kW"


def test_unknown_cluster_still_gets_a_profile():
    """Spec 3.5: die Tabelle ist Anreicherung, kein Gatekeeper."""
    ref = SignalRef(1, 64999, 7, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 42)
    assert profile.exportability is Exportability.ANALOG
    assert profile.slug == "c64999_a7"
    assert profile.unit == ""


def test_unknown_cluster_with_unmappable_value_is_not_exportable():
    ref = SignalRef(1, 64999, 7, SignalKind.ATTRIBUTE)
    assert lookup(ref, [1, 2, 3]).exportability is Exportability.NONE


def test_events_are_digital_regardless_of_value():
    """Spec 6.3: ein Event wird zum Impuls, es hat keinen Wert."""
    ref = SignalRef(1, 59, 1, SignalKind.EVENT)
    assert lookup(ref, None).exportability is Exportability.DIGITAL


def test_power_gets_the_finest_resolution_loxone_accepts():
    """Frueher forderte dieser Test `<v.6>`, mit gutem Grund: von mW nach kW
    liegen sechs Groessenordnungen, und mit drei Stellen verschwindet ein
    300-mW-Standby-Verbraucher als 0.000 (Spec 7.3).

    Der Miniserver nimmt sechs Stellen aber nicht an - am Geraet geprueft am
    2026-09-03. Drei ist damit nicht die gewuenschte, sondern die
    erreichbare Aufloesung, und ein Formatstring, den der Miniserver
    ablehnt, waere schlimmer als eine grobe Anzeige.

    Verloren geht dabei nur die DARSTELLUNG unterhalb eines Watts: der
    Formatstring beschreibt, wie Loxone die Zahl zeigt, nicht welche Zahl
    ankommt. Bausteine und Statistik rechnen weiter mit dem vollen Wert.
    """
    assert unit_format("kW") == "<v.3> kW"
    assert unit_format("kWh") == "<v.3> kWh"


def test_unit_format_uses_one_decimal_for_the_common_units():
    assert unit_format("°C") == "<v.1> °C"
    assert unit_format("%") == "<v.1>%"
    assert unit_format("V") == "<v.1> V"
    assert unit_format("A") == "<v.1> A"


def test_unit_format_for_empty_unit_is_empty():
    assert unit_format("") == ""


def test_names_element_separates_named_from_generic_within_a_known_cluster():
    """Die Tabelle kennt Cluster 6 und benennt dort nur Attribut 0. Genau
    diese Unterscheidung traegt die Feinauswahl: `onoff` ist gewollt,
    StartUpOnOff (0x4003) nicht."""
    known = SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)
    generic = SignalRef(1, 6, 0x4003, SignalKind.ATTRIBUTE)
    assert names_element(known) is True
    assert names_element(generic) is False


def test_names_element_is_false_for_a_cluster_the_table_does_not_know():
    """Ein unbekannter Cluster benennt nichts. Der Aufrufer (relevance)
    darf daraus NICHT 'alles aus' folgern - siehe dort."""
    assert names_element(SignalRef(1, 4711, 0, SignalKind.ATTRIBUTE)) is False


def test_names_element_covers_events_too():
    """Cluster 59 benennt seine Ereignisse; die Feinauswahl darf einen
    Tastendruck nicht als unbenannt verwerfen."""
    assert names_element(SignalRef(1, 59, 1, SignalKind.EVENT)) is True


def test_known_attribute_section_true_for_a_cluster_that_names_attributes():
    assert known_attribute_section(6) is True


def test_known_attribute_section_false_for_a_cluster_the_table_does_not_know():
    assert known_attribute_section(4711) is False


def test_known_attribute_section_false_for_a_cluster_known_only_for_its_commands(
    monkeypatch,
):
    """Review-Fix 1b (Nachbesserung Phase 6): ein Cluster kann in der
    Tabelle stehen, ohne etwas ueber seine Attribute zu sagen - genau die
    Form, in der Cluster 768 (ColorControl) bis zu dieser Nachbesserung
    stand (nur `commands:`, kein `attributes:`). Mit einer synthetischen
    Tabelle statt an Cluster 768 selbst geprueft, damit dieser Test die
    Falle als solche festhaelt und nicht verstummt, jetzt wo 768 einen
    `attributes:`-Abschnitt hat."""
    monkeypatch.setattr(
        "loxmatter.profiles.table._table",
        lambda: {
            999: {
                "name": "commands_only",
                "commands": {1: {"slug": "go", "takes_value": False}},
            }
        },
    )
    assert known_attribute_section(999) is False


def test_the_battery_level_is_named_and_scaled_to_percent():
    """Matter zaehlt BatPercentRemaining in halben Prozent (0-200). Ohne
    den Faktor zeigte Loxone bei voller Batterie 200 %."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 190)
    assert profile.slug == "battery"
    assert profile.unit == "%"
    assert scale_factor(ref) == pytest.approx(0.5)


def test_a_generic_signal_keeps_its_slug_but_gains_a_readable_title():
    """Der Schluessel bleibt generisch - er ist die Verdrahtung in Loxone
    und darf sich nie bewegen. Nur die Anzeige wird lesbar."""
    ref = SignalRef(0, 51, 1, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 3)
    assert profile.slug == "c51_a1"
    assert profile.title != "c51_a1"


def test_a_table_named_signal_uses_its_own_name_for_both():
    """Wo die eigene Tabelle etwas weiss, gewinnt sie: `onoff` ist
    sprechender als `OnOff`, und die Einheit kennt das SDK ohnehin nicht."""
    profile = lookup(SignalRef(1, 6, 0, SignalKind.ATTRIBUTE), True)
    assert profile.slug == "onoff"
    assert profile.title == "onoff"


def test_a_signal_the_catalog_does_not_know_falls_back_to_the_slug():
    ref = SignalRef(1, 4711, 3, SignalKind.ATTRIBUTE)
    profile = lookup(ref, 1)
    assert profile.slug == "c4711_a3"
    assert profile.title == "c4711_a3"


_ENERGY = SignalRef(2, 145, 1, SignalKind.ATTRIBUTE)


def test_a_struct_member_becomes_an_analog_signal():
    """Matter liefert den Zaehlerstand als Struktur aus Wert und
    Zeitstempeln. Ohne das Herausziehen faellt er als 'nicht abbildbar'
    durch - und das ist der Wert, wegen dem man eine messende Steckdose
    kauft."""
    raw = {"0": 12_345_678, "1": 1_700_000_000, "2": 1_700_003_600}
    assert lookup(_ENERGY, raw).exportability is Exportability.ANALOG
    assert lookup(_ENERGY, raw).slug == "energy_imported"


def test_a_struct_without_the_named_member_stays_unexportable():
    """Nicht raten. Eine erfundene Zahl an einem echten Energiebaustein
    waere schlimmer als ein fehlender Wert."""
    assert lookup(_ENERGY, {"1": 1_700_000_000}).exportability is Exportability.NONE


def test_a_struct_member_that_is_not_a_number_stays_unexportable():
    """Abweichung vom Aufgabenzettel: der extrahierte Wert laeuft durch das
    unveraenderte `classify` (Aufgabenvorgabe), und das stuft eine
    Zeichenkette als TEXT ein, nicht als NONE - beides zaehlt aber laut
    `is_exportable` (Spec 6.6) als nicht exportierbar. Die urspruengliche
    Zusicherung `is Exportability.NONE` waere nur erfuellbar gewesen, wenn
    `struct_member` selbst zwischen Zahl und Text unterschieden haette -
    das ist Aufgabe von `classify`, nicht von `struct_member`, sonst gaebe
    es die Einstufungslogik zweimal."""
    assert not is_exportable(lookup(_ENERGY, {"0": "viel"}).exportability)


def test_a_null_value_stays_unexportable_even_with_a_field():
    assert lookup(_ENERGY, None).exportability is Exportability.NONE


def test_an_integer_key_is_accepted_as_well_as_a_string_key():
    """Die Zeichenkette ist, was matter-server heute liefert; eine andere
    Serialisierung derselben Struktur waere mit Zahl genauso plausibel."""
    assert lookup(_ENERGY, {0: 5_000_000}).exportability is Exportability.ANALOG


def test_a_cluster_without_a_field_entry_still_sees_the_whole_value():
    """Nur ein Cluster, den die Tabelle kennt, darf ein Element benennen.
    Eine unbekannte Struktur bleibt unbekannt."""
    ref = SignalRef(1, 4711, 0, SignalKind.ATTRIBUTE)
    assert lookup(ref, {"0": 5}).exportability is Exportability.NONE


def test_no_unit_format_exceeds_what_loxone_accepts():
    """Der Miniserver nimmt hoechstens drei Nachkommastellen an; `<v.4>` und
    mehr funktioniert nicht (am Geraet geprueft, 2026-09-03).

    Die Pruefung laeuft ueber ALLE Eintraege der Tabelle, nicht ueber eine
    Auswahl: der Fehler entstand dadurch, dass jemand - zu Recht - mehr
    Stellen fuer die Leistung wollte und niemand die Grenze kannte. Ein
    Formatstring, den der Miniserver ablehnt, faellt sonst erst beim Import
    auf, also beim Anwender.
    """
    for unit in _UNIT_DECIMALS:
        rendered = unit_format(unit)
        decimals = int(rendered.split("<v.")[1].split(">")[0])
        assert decimals <= MAX_LOXONE_DECIMALS, f"{unit!r} ergibt {rendered!r}"
