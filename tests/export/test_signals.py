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

import json
from pathlib import Path

import pytest

from loxmatter.export.signals import to_inputs
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.model.store import Store, StoredSignal
from loxmatter.profiles.table import Exportability

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def signal(
    key,
    kind=SignalKind.ATTRIBUTE,
    exportability=Exportability.ANALOG,
    unit="",
    device_id=1,
    exported=True,
    functional=True,
    resend=False,
):
    return StoredSignal(
        key=key,
        ref=SignalRef(1, 6, 0, kind),
        title=key,
        unit=unit,
        exportability=exportability,
        device_id=device_id,
        exported=exported,
        functional=functional,
        resend=resend,
    )


def test_analog_attribute_becomes_one_analog_input():
    inputs = to_inputs([signal("d1_1_temp", unit="°C")], 1, "Wohnzimmer")
    assert [i.key for i in inputs] == ["d1_1_temp", "d1_online"]
    assert inputs[0].analog is True
    assert inputs[0].unit_format == "<v.1> °C"


def test_a_boolean_state_becomes_an_analog_input():
    """Frueher digital. Am Miniserver zeigte sich (2026-09-03), dass ein
    digitaler UDP-Eingang schon beim Erkennen des Musters ausloest und den
    Wert dahinter nicht auswertet: `d1_1_onoff:1` und `d1_1_onoff:0` passen
    beide auf `...:\v`, der Eingang stand also dauerhaft auf Ein.

    Analog liest Loxone die Zahl, und 1 und 0 werden unterscheidbar. Der
    Wert bleibt boolesch - nur der Eingangstyp aendert sich."""
    inputs = to_inputs([signal("d1_1_onoff", exportability=Exportability.DIGITAL)], 1, "Steckdose")
    assert inputs[0].analog is True
    assert inputs[0].check_suffix == "\\v"
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
    # Ein Zustand, kein Impuls - also analog, aus demselben Grund wie
    # `onoff` (siehe test_a_boolean_state_becomes_an_analog_input).
    assert online.analog is True


def test_unit_no_longer_lands_in_the_comment():
    """Die Einheit stand frueher im Kommentar; jetzt traegt sie unit_format (Spec 7.3)."""
    inputs = to_inputs([signal("d1_1_power", unit="kW")], 1, "Steckdose")
    power = next(i for i in inputs if i.key == "d1_1_power")
    assert "kW" not in power.comment
    assert power.unit_format


def test_power_unit_uses_the_finest_format_loxone_accepts():
    """Frueher <v.6>, weil ein 300-mW-Standby-Verbraucher mit drei Stellen
    als 0.000 verschwindet (Spec 7.3). Der Miniserver nimmt aber hoechstens
    drei an — am Geraet geprueft am 2026-09-03 —, und ein abgelehnter
    Formatstring waere schlimmer als eine grobe Anzeige. Betroffen ist nur
    die Darstellung, nicht der Wert, mit dem Loxone rechnet."""
    inputs = to_inputs([signal("d1_1_power", unit="kW")], 1, "Steckdose")
    power = next(i for i in inputs if i.key == "d1_1_power")
    assert power.unit_format == "<v.3> kW"


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


def test_an_unexported_analog_signal_produces_no_input():
    """Review-Fix Important #3: `exported=False` war bisher wirkungslos -
    `to_inputs` filterte ausschliesslich nach `exportability`, egal was das
    Flag aus `PATCH /api/signals/{key}` sagte."""
    inputs = to_inputs([signal("d1_1_temp", exported=False)], 1, "X")
    assert [i.key for i in inputs] == ["d1_online"]


def test_an_unexported_event_produces_neither_pulse_nor_counter():
    event = signal(
        "d1_1_press", kind=SignalKind.EVENT, exportability=Exportability.DIGITAL, exported=False
    )
    inputs = to_inputs([event], 1, "Taster")
    assert [i.key for i in inputs] == ["d1_online"]


def test_the_online_signal_is_unaffected_by_any_signals_export_flag():
    """Das Online-Signal gehoert dem Geraet, nicht einem einzelnen Signal
    (Spec 6.5) - es bleibt auch da, wenn kein einziges Signal exportiert
    wird."""
    inputs = to_inputs([signal("d1_1_a", exported=False), signal("d1_1_b", exported=False)], 1, "X")
    assert [i.key for i in inputs] == ["d1_online"]


def test_plug_fixture_yields_6_inputs_with_the_relevance_default(tmp_path):
    """Aufgabe 6: der `exported`-Default heisst seither nicht mehr nur
    `profiles.table.is_exportable` (technisch abbildbar), sondern zusaetzlich
    `profiles.relevance.is_functional` (auch tatsaechlich gewollt) - von den
    110 technisch abbildbaren Signalen der IKEA-Steckdose (siehe
    `tests/api/test_devices.py::test_signal_tree_marks_what_cannot_be_exported`)
    bleiben nur die fuenf uebrig, die etwas bedeuten: `onoff` sowie Spannung,
    Strom, Wirkleistung und Zaehlerstand der Energiemessung (siehe
    `tests/model/test_store.py::test_a_freshly_registered_plug_exports_only_its_meaningful_values`).
    Plus das Online-Signal, macht 6."""
    snap = load("ikea_grillplats_plug.json")
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(snap)
        signals = store.register_signals(device_id, snap)
    finally:
        store.close()

    label = f"{snap.vendor_name} {snap.product_name}".strip()
    inputs = to_inputs(signals, device_id, label)
    assert len(inputs) == 6


def test_unchecking_one_signal_reduces_the_plug_fixtures_input_count_by_one(tmp_path):
    """Regression Important #3: das Abschalten genau eines Signals in der
    WebUI muss den erzeugten Export exakt um einen Eingang verkleinern - ein
    Attribut, kein Event, damit der Effekt nicht durch Impuls+Zaehler auf
    zwei Eingaenge springt. Basiszahl seit Aufgabe 6: 6 (siehe
    `test_plug_fixture_yields_6_inputs_with_the_relevance_default`), also
    5 nach dem Abschalten."""
    snap = load("ikea_grillplats_plug.json")
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(snap)
        signals = store.register_signals(device_id, snap)
        target = next(s for s in signals if s.exported and s.ref.kind is SignalKind.ATTRIBUTE)
        store.set_exported(target.key, False)
        signals = store.signals(device_id)
    finally:
        store.close()

    label = f"{snap.vendor_name} {snap.product_name}".strip()
    inputs = to_inputs(signals, device_id, label)
    assert len(inputs) == 5
