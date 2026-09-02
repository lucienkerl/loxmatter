import pytest

from loxmatter.export.signals import to_inputs
from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.model.store import StoredSignal
from loxmatter.profiles.table import Exportability


def signal(key, kind=SignalKind.ATTRIBUTE, exportability=Exportability.ANALOG, unit=""):
    return StoredSignal(
        key=key,
        ref=SignalRef(1, 6, 0, kind),
        title=key,
        unit=unit,
        exportability=exportability,
    )


def test_analog_attribute_becomes_one_analog_input():
    inputs = to_inputs([signal("d1_1_temp", unit="°C")], 1, "Wohnzimmer")
    assert [i.key for i in inputs] == ["d1_1_temp", "d1_online"]
    assert inputs[0].analog is True
    assert inputs[0].unit_format == "<v.1> °C"


def test_digital_attribute_becomes_one_digital_input():
    inputs = to_inputs([signal("d1_1_onoff", exportability=Exportability.DIGITAL)], 1, "Steckdose")
    assert inputs[0].analog is False
    assert inputs[0].unit_format == ""


def test_event_becomes_a_pulse_and_a_counter():
    """Spec 6.3: der Impuls erzeugt die Flanke, der Zaehler ueberlebt ein verlorenes Paket."""
    inputs = to_inputs(
        [signal("d1_1_press", kind=SignalKind.EVENT, exportability=Exportability.DIGITAL)],
        1,
        "Taster",
    )
    keys = [i.key for i in inputs]
    assert "d1_1_press" in keys
    assert "d1_1_press_n" in keys
    pulse = next(i for i in inputs if i.key == "d1_1_press")
    counter = next(i for i in inputs if i.key == "d1_1_press_n")
    assert pulse.analog is False
    assert counter.analog is True
    assert pulse.unit_format == ""
    assert counter.unit_format == ""


def test_non_exportable_signals_are_skipped():
    """Spec 6.6: Listen und Strukturen werden nie zu Loxone-Objekten."""
    inputs = to_inputs([signal("d1_1_parts", exportability=Exportability.NONE)], 1, "X")
    assert [i.key for i in inputs] == ["d1_online"]


def test_text_signals_are_skipped_for_now():
    """Der virtuelle Texteingang ist ein eigener Vorlagentyp — spaetere Ausbaustufe."""
    inputs = to_inputs([signal("d1_1_vendor", exportability=Exportability.TEXT)], 1, "X")
    assert [i.key for i in inputs] == ["d1_online"]


def test_online_signal_is_added_once_per_device():
    """Spec 6.5: kostet nichts und beantwortet die haeufigste Frage."""
    inputs = to_inputs([signal("d1_1_a"), signal("d1_1_b")], 1, "Geraet")
    assert [i.key for i in inputs].count("d1_online") == 1
    online = next(i for i in inputs if i.key == "d1_online")
    assert online.analog is False


def test_unit_no_longer_lands_in_the_comment():
    """Die Einheit stand frueher im Kommentar; jetzt traegt sie unit_format (Spec 7.3)."""
    inputs = to_inputs([signal("d1_1_power", unit="kW")], 1, "Steckdose")
    power = next(i for i in inputs if i.key == "d1_1_power")
    assert "kW" not in power.comment
    assert power.unit_format


def test_power_unit_gets_the_widened_six_decimal_format():
    """Spec 7.3: mit dem sonst ueblichen <v.3> zeigt ein 300-mW-Standby-
    Verbraucher 0.000 an — deshalb <v.6> fuer Leistung."""
    inputs = to_inputs([signal("d1_1_power", unit="kW")], 1, "Steckdose")
    power = next(i for i in inputs if i.key == "d1_1_power")
    assert power.unit_format == "<v.6> kW"


def test_empty_signal_list_still_yields_the_online_input():
    assert [i.key for i in to_inputs([], 7, "Leer")] == ["d7_online"]


def test_event_counter_key_colliding_with_another_signal_raises():
    """Regression: die `_n`-Endung ist nirgends reserviert. Ein `clusters.yaml`-
    Slug kann zufaellig genau auf den Zaehler-Schluessel eines Events treffen —
    das darf nie still zwei identische `LoxoneInput`s erzeugen (siehe Review)."""
    event = signal("d3_1_press", kind=SignalKind.EVENT, exportability=Exportability.DIGITAL)
    collider = signal("d3_1_press_n")
    with pytest.raises(ValueError, match="d3_1_press_n"):
        to_inputs([event, collider], 3, "Taster")


def test_signal_from_a_different_device_raises():
    """Regression: der Praefix wurde frueher aus den Daten geraten und ist
    jetzt ein expliziter Parameter — ein falsch zugeordnetes Signal muss laut
    scheitern statt ein Geraet stillschweigend falsch zu beschriften."""
    foreign = signal("d9_1_temp")
    with pytest.raises(ValueError, match="d9_1_temp"):
        to_inputs([foreign], 3, "Taster")
