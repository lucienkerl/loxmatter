# loxmatter - connects Matter devices to a Loxone Miniserver.
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

from loxmatter import i18n
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
    """Previously digital. On the Miniserver it turned out (2026-09-03) that
    a digital UDP input already triggers on recognizing the pattern and does
    not evaluate the value behind it: `d1_1_onoff:1` and `d1_1_onoff:0` both
    match `...:\v`, so the input stayed permanently on.

    Analog has Loxone read the number, and 1 and 0 become distinguishable.
    The value stays boolean - only the input type changes."""
    inputs = to_inputs([signal("d1_1_onoff", exportability=Exportability.DIGITAL)], 1, "Steckdose")
    assert inputs[0].analog is True
    assert inputs[0].check_suffix == "\\v"
    assert inputs[0].unit_format == ""


def test_event_becomes_a_pulse_and_a_counter():
    """Spec 6.3: the pulse creates the edge, the counter survives a lost packet."""
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


def test_event_pulse_and_counter_title_and_comment_follow_the_current_language():
    def build():
        return to_inputs(
            [signal("d3_1_press", kind=SignalKind.EVENT, exportability=Exportability.DIGITAL)],
            3,
            "Taster",
        )

    inputs = build()
    pulse = next(i for i in inputs if i.key == "d3_1_press")
    counter = next(i for i in inputs if i.key == "d3_1_press_n")
    assert pulse.comment == "Taster · 1/6/0 · pulse"
    assert counter.title == "d3_1_press counter"
    assert counter.comment == "Taster · 1/6/0 · counter"

    i18n.set_language("de")
    inputs = build()
    pulse = next(i for i in inputs if i.key == "d3_1_press")
    counter = next(i for i in inputs if i.key == "d3_1_press_n")
    assert pulse.comment == "Taster · 1/6/0 · Impuls"
    assert counter.title == "d3_1_press Zähler"
    assert counter.comment == "Taster · 1/6/0 · Zähler"


def test_non_exportable_signals_are_skipped():
    """Spec 6.6: lists and structs never become Loxone objects."""
    inputs = to_inputs([signal("d1_1_parts", exportability=Exportability.NONE)], 1, "X")
    assert [i.key for i in inputs] == ["d1_online"]


def test_text_signals_are_skipped_for_now():
    """The virtual text input is a template type of its own - a later expansion stage."""
    inputs = to_inputs([signal("d1_1_vendor", exportability=Exportability.TEXT)], 1, "X")
    assert [i.key for i in inputs] == ["d1_online"]


def test_online_signal_is_added_once_per_device():
    """Spec 6.5: costs nothing and answers the most common question."""
    inputs = to_inputs([signal("d1_1_a"), signal("d1_1_b")], 1, "Device")
    assert [i.key for i in inputs].count("d1_online") == 1
    online = next(i for i in inputs if i.key == "d1_online")
    # A state, not a pulse - so analog, for the same reason as
    # `onoff` (see test_a_boolean_state_becomes_an_analog_input).
    assert online.analog is True


def test_online_title_follows_the_current_language():
    online = next(i for i in to_inputs([], 7, "Leer") if i.key == "d7_online")
    assert online.title == "Leer reachable"

    i18n.set_language("de")
    online = next(i for i in to_inputs([], 7, "Leer") if i.key == "d7_online")
    assert online.title == "Leer erreichbar"


def test_unit_no_longer_lands_in_the_comment():
    """The unit used to be in the comment; now unit_format carries it (Spec 7.3)."""
    inputs = to_inputs([signal("d1_1_power", unit="kW")], 1, "Steckdose")
    power = next(i for i in inputs if i.key == "d1_1_power")
    assert "kW" not in power.comment
    assert power.unit_format


def test_power_unit_uses_the_finest_format_loxone_accepts():
    """Previously <v.6>, because a 300 mW standby load with three digits
    disappears as 0.000 (Spec 7.3). But the Miniserver accepts at most
    three - checked on the device on 2026-09-03 - and a rejected format
    string would be worse than a coarse display. Only the presentation is
    affected, not the value Loxone computes with."""
    inputs = to_inputs([signal("d1_1_power", unit="kW")], 1, "Steckdose")
    power = next(i for i in inputs if i.key == "d1_1_power")
    assert power.unit_format == "<v.3> kW"


def test_empty_signal_list_still_yields_the_online_input():
    assert [i.key for i in to_inputs([], 7, "Leer")] == ["d7_online"]


def test_event_counter_key_colliding_with_another_signal_raises():
    """Regression: the `_n` suffix is not reserved anywhere. A `clusters.yaml`
    slug can coincidentally hit exactly the counter key of an event - that
    must never silently produce two identical `LoxoneInput`s (see review)."""
    event = signal("d3_1_press", kind=SignalKind.EVENT, exportability=Exportability.DIGITAL)
    collider = signal("d3_1_press_n")
    with pytest.raises(ValueError, match="d3_1_press_n"):
        to_inputs([event, collider], 3, "Taster")


def test_signal_from_a_different_device_raises():
    """Regression: the prefix used to be guessed from the data and is now an
    explicit parameter - a misattributed signal must fail loudly instead of
    silently mislabeling a device."""
    foreign = signal("d9_1_temp")
    with pytest.raises(ValueError, match="d9_1_temp"):
        to_inputs([foreign], 3, "Taster")


def test_an_unexported_analog_signal_produces_no_input():
    """Review-Fix Important #3: `exported=False` previously had no effect -
    `to_inputs` filtered exclusively by `exportability`, regardless of what
    the flag from `PATCH /api/signals/{key}` said."""
    inputs = to_inputs([signal("d1_1_temp", exported=False)], 1, "X")
    assert [i.key for i in inputs] == ["d1_online"]


def test_an_unexported_event_produces_neither_pulse_nor_counter():
    event = signal(
        "d1_1_press", kind=SignalKind.EVENT, exportability=Exportability.DIGITAL, exported=False
    )
    inputs = to_inputs([event], 1, "Taster")
    assert [i.key for i in inputs] == ["d1_online"]


def test_the_online_signal_is_unaffected_by_any_signals_export_flag():
    """The online signal belongs to the device, not to any single signal
    (Spec 6.5) - it stays present even when not a single signal is
    exported."""
    inputs = to_inputs([signal("d1_1_a", exported=False), signal("d1_1_b", exported=False)], 1, "X")
    assert [i.key for i in inputs] == ["d1_online"]


def test_plug_fixture_yields_6_inputs_with_the_relevance_default(tmp_path):
    """Task 6: since then, the `exported` default no longer means only
    `profiles.table.is_exportable` (technically mappable), but additionally
    `profiles.relevance.is_functional` (also actually wanted) - of the
    110 technically mappable signals of the IKEA outlet (see
    `tests/api/test_devices.py::test_signal_tree_marks_what_cannot_be_exported`)
    only the five remain that mean something: `onoff` plus voltage, current,
    active power, and the energy meter reading (see
    `tests/model/test_store.py::test_a_freshly_registered_plug_exports_only_its_meaningful_values`).
    Plus the online signal, makes 6."""
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
    """Regression Important #3: unchecking exactly one signal in the WebUI
    must shrink the generated export by exactly one input - an attribute,
    not an event, so the effect does not jump to two inputs via
    pulse+counter. Base count since Task 6: 6 (see
    `test_plug_fixture_yields_6_inputs_with_the_relevance_default`), so 5
    after unchecking."""
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


def test_the_template_lists_the_button_press_before_the_battery(tmp_path):
    """The VIU template inherits the order from `Store.signals` (api/export.py
    calls `to_inputs(store.signals(...))`). In the Loxone tree, since the
    ranking (task 2), the button press sits above the battery - this
    is intentional, not a side effect, and is therefore pinned down here."""
    store = Store(tmp_path / "t.sqlite")
    try:
        snap = load("ikea_bilresa_button.json")
        device_id = store.register_device(snap)
        store.register_signals(device_id, snap)
        signals = store.signals(device_id)
    finally:
        store.close()

    inputs = to_inputs(signals, device_id, "Taster")
    keys = [i.key for i in inputs]

    assert keys.index(f"d{device_id}_1_press") < keys.index(f"d{device_id}_0_battery")
