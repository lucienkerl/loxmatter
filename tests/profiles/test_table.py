from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles.table import Exportability, classify, lookup, unit_format


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


def test_unit_format_widens_power_to_six_decimals():
    """Spec 7.3: mit <v.3> zeigt ein 300-mW-Standby-Verbraucher 0.000 an."""
    assert unit_format("kW") == "<v.6> kW"
    assert unit_format("kWh") == "<v.6> kWh"


def test_unit_format_uses_one_decimal_for_the_common_units():
    assert unit_format("°C") == "<v.1> °C"
    assert unit_format("%") == "<v.1>%"
    assert unit_format("V") == "<v.1> V"
    assert unit_format("A") == "<v.1> A"


def test_unit_format_for_empty_unit_is_empty():
    assert unit_format("") == ""
