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
from dataclasses import replace
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.model.store import (
    Store,
    UnknownDeviceError,
    _decode_device_types,
    _encode_device_types,
    _signal_order,
)
from loxmatter.profiles.relevance import device_types_by_endpoint, is_functional
from loxmatter.profiles.table import Exportability, Profile, lookup

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite")
    yield s
    s.close()


def test_device_id_is_stable_across_registrations(store):
    snap = load("ikea_grillplats_plug.json")
    first = store.register_device(snap)
    assert store.register_device(snap) == first


def test_device_id_is_never_reused(store):
    """Two *different* devices get different ids.

    This only proves that AUTOINCREMENT works — it would also pass if the
    `active` column did not exist at all and `register_device` blindly
    inserted a new row on every call. The property actually worth
    protecting — the same physical device gets a new id and new keys after
    `forget_device` + recommissioning — is instead checked by
    `test_recommissioned_device_gets_fresh_id_and_keys`.
    """
    plug = load("ikea_grillplats_plug.json")
    button = load("ikea_bilresa_button.json")
    first = store.register_device(plug)
    store.forget_device(first)
    assert store.register_device(button) != first


def test_recommissioned_device_gets_fresh_id_and_keys(store):
    """The same physical device, forgotten and recommissioned, must get a
    new device_id and new keys — otherwise it would silently inherit the
    old Loxone wiring of a previous owner (see the module docstring in
    store.py). That is the property the `WHERE unique_id = ? AND active = 1`
    in `register_device` actually protects; drop the `active = 1` and the
    query finds the old, forgotten row again and this test fails.
    """
    snap = load("ikea_grillplats_plug.json")

    old_id = store.register_device(snap)
    old_keys = {s.key for s in store.register_signals(old_id, snap)}

    store.forget_device(old_id)

    new_id = store.register_device(snap)
    assert new_id != old_id
    assert store.signals(new_id) == []

    new_keys = {s.key for s in store.register_signals(new_id, snap)}
    assert new_keys.isdisjoint(old_keys)


def test_key_format_matches_spec_6_2(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    onoff = next(s for s in signals if s.ref.cluster_id == 6 and s.ref.element_id == 0)
    assert onoff.key == f"d{device_id}_1_onoff"


def test_key_survives_a_title_change(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    before = {s.ref: s.key for s in store.register_signals(device_id, snap)}
    store.set_title(before_key := next(iter(before.values())), "Kaffeemaschine")
    after = {s.ref: s.key for s in store.signals(device_id)}
    assert after == before
    assert any(s.title == "Kaffeemaschine" for s in store.signals(device_id) if s.key == before_key)


def test_keys_are_unique_within_a_device(store):
    snap = load("ikea_bilresa_button.json")
    device_id = store.register_device(snap)
    keys = [s.key for s in store.register_signals(device_id, snap)]
    assert len(keys) == len(set(keys))


def test_disambiguates_when_two_signals_share_a_slug_on_the_same_endpoint(store, monkeypatch):
    """This project's fixtures do not (yet) produce a real slug collision
    (see the comment in store.py). To still exercise the fallback strategy,
    `lookup` is deliberately forced to a fixed slug for a single cluster:
    cluster 3 carries two attributes on endpoint 1 (element id 0 and 1) of
    the IKEA outlet, which thereby get the same slug "fake"."""
    real_lookup = lookup

    def fake_lookup(ref, value):
        if ref.endpoint == 1 and ref.cluster_id == 3:
            return Profile(slug="fake", title="fake", unit="", exportability=Exportability.DIGITAL)
        return real_lookup(ref, value)

    monkeypatch.setattr("loxmatter.model.store.lookup", fake_lookup)

    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)

    keys = [s.key for s in signals]
    assert len(keys) == len(set(keys))
    assert f"d{device_id}_1_fake" in keys
    assert f"d{device_id}_1_fake_1" in keys


def test_irreconcilable_key_collision_raises_instead_of_dropping_silently(store, monkeypatch):
    """Three signals that carry both the same slug and the same element id
    (0) on the same endpoint can no longer be told apart even by the
    fallback strategy extended with the element id. register_signals must
    not silently resolve that by dropping the third signal (the danger of an
    `INSERT OR IGNORE`, see the module docstring in store.py) — it must fail
    loudly, and the device must not contain any signals from this failed
    call afterward."""
    real_lookup = lookup

    def fake_lookup(ref, value):
        if ref.endpoint == 1 and ref.element_id == 0 and ref.cluster_id in (3, 4, 6):
            return Profile(slug="fake", title="fake", unit="", exportability=Exportability.DIGITAL)
        return real_lookup(ref, value)

    monkeypatch.setattr("loxmatter.model.store.lookup", fake_lookup)

    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)

    with pytest.raises(ValueError):
        store.register_signals(device_id, snap)

    assert store.signals(device_id) == []


def test_reregistering_keeps_existing_keys_and_adds_new_ones(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    before = {s.ref: s.key for s in store.register_signals(device_id, snap)}
    again = {s.ref: s.key for s in store.register_signals(device_id, snap)}
    assert again == before


def test_null_attribute_becomes_exportable_once_it_reports_a_real_value(store):
    """Review-Fix Important #2: `1/6/16387` (StartUpOnOff) initially reports
    `null` on the IKEA outlet and is therefore exportability=none — not
    because the attribute is generally unexportable, but because no value is
    present yet. If the device later starts reporting a real value, the next
    registration must catch up without changing the key already assigned.
    Previously, `register_signals` froze `unit` and `exportability` forever
    once a signal was known."""
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    before = {s.ref: s for s in store.register_signals(device_id, snap)}
    target = SignalRef(1, 6, 16387, SignalKind.ATTRIBUTE)
    assert before[target].exportability == Exportability.NONE

    updated_attributes = dict(snap.attributes)
    updated_attributes["1/6/16387"] = True
    updated_snap = replace(snap, attributes=updated_attributes)

    after = {s.ref: s for s in store.register_signals(device_id, updated_snap)}
    assert after[target].exportability == Exportability.DIGITAL
    assert after[target].key == before[target].key


def test_changed_unit_in_the_table_reaches_an_already_stored_signal(store, monkeypatch):
    """Review-Fix Important #2: a correction in `clusters.yaml` must reach a
    signal that is already stored. Previously the only remedy was deleting
    the entire database — which would have destroyed every key too."""
    real_lookup = lookup
    target = SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)  # onoff

    def make_fake(unit: str):
        def fake(ref: SignalRef, value: object) -> Profile:
            if ref == target:
                return Profile(
                    slug="onoff", title="onoff", unit=unit, exportability=Exportability.DIGITAL
                )
            return real_lookup(ref, value)

        return fake

    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)

    monkeypatch.setattr("loxmatter.model.store.lookup", make_fake("alte_einheit"))
    before = {s.ref: s for s in store.register_signals(device_id, snap)}
    assert before[target].unit == "alte_einheit"

    monkeypatch.setattr("loxmatter.model.store.lookup", make_fake("neue_einheit"))
    after = {s.ref: s for s in store.register_signals(device_id, snap)}
    assert after[target].unit == "neue_einheit"
    assert after[target].key == before[target].key


def test_user_set_title_survives_reregistration(store):
    """Review-Fix Important #2: `title` becomes user-owned once `set_title`
    has set it, and must not be overwritten by a subsequent
    `register_signals` — unlike `unit`/`exportability`."""
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    onoff_key = next(s.key for s in signals if s.ref.cluster_id == 6 and s.ref.element_id == 0)

    store.set_title(onoff_key, "Kaffeemaschine")
    again = store.register_signals(device_id, snap)

    renamed = next(s for s in again if s.key == onoff_key)
    assert renamed.title == "Kaffeemaschine"
    assert renamed.key == onoff_key


def test_all_devices_share_the_default_udp_port(store):
    plug = store.register_device(load("ikea_grillplats_plug.json"))
    button = store.register_device(load("ikea_bilresa_button.json"))
    assert store.udp_port(plug) == store.udp_port(button) == 7000


def test_device_id_for_resolves_a_registered_device(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    assert store.device_id_for("matter", snap.address) == device_id


def test_device_id_for_is_none_for_an_unknown_node(store):
    assert store.device_id_for("matter", "999") is None


def test_device_id_for_ignores_a_forgotten_device(store):
    """A removed device must no longer be findable via its old node id -
    otherwise a runtime subscription would assign values to an inactive
    device."""
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    store.forget_device(device_id)
    assert store.device_id_for("matter", snap.address) is None


def test_devices_lists_only_active_devices(store):
    plug_id = store.register_device(load("ikea_grillplats_plug.json"))
    button_id = store.register_device(load("ikea_bilresa_button.json"))
    store.forget_device(button_id)
    assert [d.id for d in store.devices()] == [plug_id]


def test_device_returns_the_stored_row(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    device = store.device(device_id)
    assert device.id == device_id
    assert device.address == snap.address
    assert "GRILLPLATS" in device.label


def test_device_raises_for_an_unknown_id(store):
    with pytest.raises(UnknownDeviceError):
        store.device(999)


def test_device_raises_for_a_forgotten_device(store):
    device_id = store.register_device(load("ikea_grillplats_plug.json"))
    store.forget_device(device_id)
    with pytest.raises(UnknownDeviceError):
        store.device(device_id)


def test_rename_device_changes_the_label(store):
    device_id = store.register_device(load("ikea_grillplats_plug.json"))
    store.rename_device(device_id, "Grillplatz Steckdose")
    assert store.device(device_id).label == "Grillplatz Steckdose"


def test_signal_by_key_finds_a_registered_signal(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    target = signals[0]
    found = store.signal_by_key(target.key)
    assert found is not None
    assert found.key == target.key
    assert found.device_id == device_id


def test_signal_by_key_is_none_for_an_unknown_key(store):
    assert store.signal_by_key("d1_1_gibtsnicht") is None


def test_new_signal_is_exported_exactly_when_it_is_exportable_and_functional(store):
    """Task 6: `expected` below is deliberately NOT computed via
    `profiles.table.is_exportable`, since that is exactly one half of
    `register_signals`'s own formula - a test that calls the same function
    as production can never catch a bug in that very function. The technical
    half therefore stays the independently formulated rule from spec 6.6
    (only ANALOG/DIGITAL fit a Loxone input - Review-Fix Important #2,
    2026-09-02).

    The second half, `is_functional`, is deliberately reused here instead of
    being rebuilt by hand: a first attempt to copy the profile table's fine
    selection (which element of cluster 144/145 is named) into this test
    module by hand drifted from the real state immediately while writing it
    (12 wrong predictions in a test run against the real store).
    `is_functional` is not a function of this task but a preliminary stage
    already independently checked against exactly this descriptor data in
    `tests/profiles/test_relevance.py` (tasks 1-2) - rebuilding it here a
    second time would have produced only a second, constantly-maintained
    copy of the same table, not a stronger test."""
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    device_types = device_types_by_endpoint(snap)
    for signal in signals:
        technically_exportable = signal.exportability in (
            Exportability.ANALOG,
            Exportability.DIGITAL,
        )
        expected = technically_exportable and is_functional(signal.ref, device_types)
        assert signal.exported is expected, signal.key


def test_a_freshly_registered_plug_exports_only_its_meaningful_values(store):
    """The goal of this whole design, on the real device: five values that
    mean something, instead of 110 technically mappable ones (draft section
    1 and 4.4 - 109 was the state before section 5, the counter reading, was
    implemented)."""
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)

    exported = {s.key for s in store.signals(device_id) if s.exported}
    assert exported == {
        "d1_1_onoff",
        "d1_2_voltage",
        "d1_2_current",
        "d1_2_power",
        "d1_2_energy_imported",
    }


def test_a_freshly_registered_button_keeps_both_rockers_and_the_battery(store):
    """The case that shows whether the rule is too greedy: all six events of
    both rockers must get through, plus the battery level."""
    snap = load("ikea_bilresa_button.json")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)

    exported = {s.key for s in store.signals(device_id) if s.exported}
    for endpoint in (1, 2):
        for slug in (
            "press",
            "longpress",
            "shortrelease",
            "longrelease",
            "multipress_ongoing",
            "multipress",
        ):
            assert f"d1_{endpoint}_{slug}" in exported
    assert "d1_0_battery" in exported
    assert len(exported) == 17


def test_a_thread_counter_is_stored_but_not_exported(store):
    """Not deleted, only deselected: the expert block should be able to
    unlock it without the device being recommissioned."""
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)

    counters = [s for s in store.signals(device_id) if s.ref.cluster_id == 53]
    assert counters, "Thread counters should still be stored"
    assert all(not s.exported for s in counters)
    assert all(s.exportability is Exportability.ANALOG for s in counters[:1])


def test_set_exported_toggles_the_flag_without_touching_the_key(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    target = next(s for s in signals if s.exported)

    store.set_exported(target.key, False)
    after = next(s for s in store.signals(device_id) if s.key == target.key)
    assert after.exported is False
    assert after.key == target.key


def test_exported_flag_survives_reregistration(store):
    """Like `title`: once set by the user, a subsequent `register_signals`
    must not reset the export flag."""
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    target = next(s for s in signals if s.exported)

    store.set_exported(target.key, False)
    again = store.register_signals(device_id, snap)
    after = next(s for s in again if s.key == target.key)
    assert after.exported is False


def test_set_resend_toggles_the_flag_without_touching_the_key(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    target = signals[0]
    assert target.resend is False  # default value (draft, section 3)

    store.set_resend(target.key, True)
    after = next(s for s in store.signals(device_id) if s.key == target.key)
    assert after.resend is True
    assert after.key == target.key


def test_resend_flag_survives_reregistration(store):
    """Like `exported`: once set by the user, a subsequent `register_signals`
    must not reset the resend flag."""
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    target = signals[0]

    store.set_resend(target.key, True)
    again = store.register_signals(device_id, snap)
    after = next(s for s in again if s.key == target.key)
    assert after.resend is True


def test_resend_keys_lists_only_flagged_signals(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    marked, other = signals[0], signals[1]
    store.set_resend(marked.key, True)

    keys = store.resend_keys()
    assert keys == [marked.key]
    assert other.key not in keys


def test_resend_keys_excludes_signals_of_a_removed_device(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    store.set_resend(signals[0].key, True)

    store.forget_device(device_id)

    assert store.resend_keys() == []


def test_store_survives_reopening(tmp_path):
    path = tmp_path / "persist.sqlite"
    snap = load("ikea_grillplats_plug.json")
    first = Store(path)
    device_id = first.register_device(snap)
    keys = {s.key for s in first.register_signals(device_id, snap)}
    first.close()

    second = Store(path)
    assert second.register_device(snap) == device_id
    assert {s.key for s in second.signals(device_id)} == keys
    second.close()


def test_check_writable_succeeds_on_a_healthy_database(store):
    """The simple case: no open transaction, no error."""
    store.check_writable()


def test_check_writable_recovers_from_a_leftover_open_transaction(store):
    """Review-Fix Minor (2026-09-02): `rename_device`, `mark_exported`,
    `set_title` and `set_exported` do not wrap their `UPDATE ...` plus
    `commit()` in their own try/except (unlike e.g. `register_signals`) - if
    the `UPDATE` itself fails there, or only the `commit()` does, the
    transaction Python automatically opened before the `UPDATE` stays open
    on the connection. This test simulates exactly that (an `UPDATE` without
    a following `commit()`/`rollback()`) and checks that `check_writable`
    does not mistake that for "not writable" - see the docstring there."""
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)

    store._db.execute("UPDATE device SET label = ? WHERE id = ?", ("Zwischenstand", device_id))
    assert store._db.in_transaction

    store.check_writable()  # must not raise despite the open transaction


def test_decode_device_types_roundtrips_encode_device_types():
    types = {0: frozenset({22, 18}), 1: frozenset({266})}
    assert _decode_device_types(_encode_device_types(types)) == types


def test_decode_device_types_of_none_is_none():
    assert _decode_device_types(None) is None


def test_decode_device_types_of_syntactically_broken_json_is_none():
    assert _decode_device_types("{nicht json") is None


def test_decode_device_types_of_non_integer_endpoint_key_is_none():
    """Review-Fix (2026-09-05): `int("x")` raises `ValueError`, not the
    `json.JSONDecodeError`/`TypeError` caught until now - a manually
    tampered row with a non-numeric endpoint key previously let
    `_decode_device_types` break through."""
    assert _decode_device_types('{"x": [1, 2]}') is None


def test_decode_device_types_of_non_integer_type_id_is_none():
    assert _decode_device_types('{"1": ["abc"]}') is None


def test_decode_device_types_of_non_iterable_id_list_is_none():
    assert _decode_device_types('{"1": 5}') is None


def test_decode_device_types_of_non_object_json_is_none():
    assert _decode_device_types("[1, 2]") is None


def test_set_room_stores_the_name_and_trims_it(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"))
        store.set_room(device_id, "  Wohnzimmer  ")
        assert store.device(device_id).room == "Wohnzimmer"
    finally:
        store.close()


def test_set_room_with_blank_input_clears_the_room(tmp_path):
    """A name made of pure whitespace has an unambiguous meaning - "no
    room" - and is therefore not an error case, but the same path as an
    explicit `None`."""
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"))
        store.set_room(device_id, "Bad")
        store.set_room(device_id, "   ")
        assert store.device(device_id).room is None
    finally:
        store.close()


def test_set_room_does_not_touch_updated_at(tmp_path):
    """The core of the decision from section 3.3 of the draft: the room
    ends up in NO export template. If `set_room` also set `updated_at`,
    every device would get an amber "changed since export" pill and a
    prompt for an export that produces byte-for-byte the same files the
    first time the room assignment is cleaned up. `rename_device`, in
    contrast, rightly does set it - the label is exported as `Title`."""
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"))
        before = store.device(device_id).updated_at
        store.set_room(device_id, "Flur")
        assert store.device(device_id).updated_at == before
    finally:
        store.close()


def test_register_device_takes_a_room(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"), room="Küche")
        assert store.device(device_id).room == "Küche"
    finally:
        store.close()


def test_rename_room_moves_every_device_and_reports_the_count(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        plug = store.register_device(load("ikea_grillplats_plug.json"), room="Küche")
        button = store.register_device(load("ikea_bilresa_button.json"), room="Küche")
        assert store.rename_room("Küche", "Essbereich") == 2
        assert store.device(plug).room == "Essbereich"
        assert store.device(button).room == "Essbereich"
    finally:
        store.close()


def test_rename_room_merges_into_an_existing_room(tmp_path):
    """A target name that already exists merges both rooms - the natural
    meaning of "rename Kitchen to Dining Area now" when a Dining Area
    already exists. The UI asks for confirmation beforehand; the store just
    executes."""
    store = Store(tmp_path / "t.sqlite")
    try:
        plug = store.register_device(load("ikea_grillplats_plug.json"), room="Küche")
        button = store.register_device(load("ikea_bilresa_button.json"), room="Essbereich")
        assert store.rename_room("Küche", "Essbereich") == 1
        assert store.device(plug).room == "Essbereich"
        assert store.device(button).room == "Essbereich"
    finally:
        store.close()


def test_rename_room_leaves_removed_devices_alone(tmp_path):
    """`active = 1` in the condition, for the same reason `Store.devices()`
    filters by it afterward: a removed device is, from the UI's point of
    view, no longer there and should not silently tag along."""
    store = Store(tmp_path / "t.sqlite")
    try:
        gone = store.register_device(load("ikea_grillplats_plug.json"), room="Küche")
        store.forget_device(gone)
        assert store.rename_room("Küche", "Essbereich") == 0
        row = store._db.execute("SELECT room FROM device WHERE id = ?", (gone,)).fetchone()
        assert row["room"] == "Küche"
    finally:
        store.close()


def test_rename_room_rejects_an_empty_target(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    try:
        store.register_device(load("ikea_grillplats_plug.json"), room="Küche")
        with pytest.raises(ValueError):
            store.rename_room("Küche", "   ")
    finally:
        store.close()


def test_rename_room_normalizes_the_source_name_too(tmp_path):
    """Review finding for task 2: previously only `new` went through
    `_normalized_room`, `old` was compared raw in `WHERE room = ?` -
    harmless as long as `old` came exclusively from values read back (those
    are already trimmed). But the API route (task 5) passes `from` through
    as free text from the JSON body; " Küche " would otherwise match zero
    rows there and look like a typo for a nonexistent room, even though the
    room quite obviously exists."""
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"), room="Küche")
        assert store.rename_room("  Küche  ", "Essbereich") == 1
        assert store.device(device_id).room == "Essbereich"
    finally:
        store.close()


def test_rename_room_moves_a_group_carrying_the_room_too(tmp_path):
    """A group is as much a room carrier as a device (design 6, section 2):
    it has its OWN `room` column, deliberately never derived from its
    members - a derived room would move the group on its own the moment
    one lamp is re-roomed. `rename_room` only ever wrote to `device`
    before this fix, so renaming "Kueche" moved the plug but left the
    group's `room` column exactly as it was: the chip bar (`roomChips()`,
    `app.js`) would then show BOTH "Essbereich (1)", populated by the
    device that actually moved, AND "Kueche (1)", populated by nothing but
    the group nobody touched - the old room still standing.

    Two rows moved, one call: the return value is the combined count
    (device rows AND group rows), matching what `POST /api/rooms/rename`
    reports back to the WebUI as "renamed"."""
    store = Store(tmp_path / "t.sqlite")
    try:
        plug = store.register_device(load("ikea_grillplats_plug.json"), room="Küche")
        group = store.create_group("Lampengruppe", [plug], room="Küche")
        assert store.rename_room("Küche", "Essbereich") == 2
        assert store.device(plug).room == "Essbereich"
        assert store.group(group.id).room == "Essbereich"
    finally:
        store.close()


def test_rename_room_moves_a_room_carried_only_by_a_group(tmp_path):
    """The case a user hits when every device that used to live in a room
    has since been re-roomed away, leaving only a group behind (design 6:
    a group's room is its own field, never derived from members - it does
    not disappear just because the last device left).

    Before this fix, `rename_room` matched zero rows for a room like this
    (it only ever looked at `device`) and returned 0 - which `POST
    /api/rooms/rename` (`api/devices.py`) turns into a 404 with "unknown
    room", even though the room is visibly still on screen, populated by
    the group below."""
    store = Store(tmp_path / "t.sqlite")
    try:
        # No room at all for the device - only the group carries "Küche".
        plug = store.register_device(load("ikea_grillplats_plug.json"))
        group = store.create_group("Lampengruppe", [plug], room="Küche")
        assert store.device(plug).room is None
        assert store.rename_room("Küche", "Essbereich") == 1
        assert store.group(group.id).room == "Essbereich"
    finally:
        store.close()


def test_rename_room_does_not_touch_updated_at(tmp_path):
    """Same rationale as for `set_room` and `backfill_device_types`: the
    room ends up in NO export template, so cleaning up room names must not
    require an export that produces byte-for-byte the same files as the
    last one. `rename_room` is the third write path to `device.room` -
    unlike its two siblings, it previously had no test of its own for this,
    even though the SQL is correct today. That is exactly what makes the
    gap risky: it is the one place where a future change could break this
    guarantee without a test noticing."""
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"), room="Küche")
        before = store.device(device_id).updated_at

        assert store.rename_room("Küche", "Essbereich") == 1
        # Not passing vacuously: the rename must actually have taken
        # place, otherwise an unchanged `updated_at` would prove nothing.
        assert store.device(device_id).room == "Essbereich"
        assert store.device(device_id).updated_at == before
    finally:
        store.close()


def test_register_device_stores_the_matter_device_types(tmp_path):
    """Endpoint 1 of the outlet reports 266 (0x010A, On/Off Plug-in Unit),
    endpoint 0 the administrative types - both are stored raw, filtering
    only happens when the category is derived."""
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"))
        types = store.device(device_id).device_types
        assert types is not None
        assert types[1] == frozenset({0x010A})
    finally:
        store.close()


def test_backfill_fills_only_rows_that_have_none(tmp_path):
    """An existing row gets its types on the next bridge startup - one
    already filled in is not rewritten on every startup."""
    store = Store(tmp_path / "t.sqlite")
    try:
        snapshot = load("ikea_grillplats_plug.json")
        device_id = store.register_device(snapshot)
        store._db.execute("UPDATE device SET device_types = NULL WHERE id = ?", (device_id,))
        store._db.commit()

        assert store.backfill_device_types([snapshot]) == 1
        assert store.device(device_id).device_types is not None
        assert store.backfill_device_types([snapshot]) == 0
    finally:
        store.close()


def test_backfill_leaves_a_device_missing_from_the_snapshots_untouched(tmp_path):
    """A device that happens to be offline at startup is missing from
    `client.snapshots()`. It must not lose anything because of that -
    that is why writes only happen where a snapshot is present, and it is
    never cleared."""
    store = Store(tmp_path / "t.sqlite")
    try:
        plug = load("ikea_grillplats_plug.json")
        button = load("ikea_bilresa_button.json")
        plug_id = store.register_device(plug)
        button_id = store.register_device(button)
        store._db.execute("UPDATE device SET device_types = NULL")
        store._db.commit()

        assert store.backfill_device_types([plug]) == 1
        assert store.device(plug_id).device_types is not None
        assert store.device(button_id).device_types is None
    finally:
        store.close()


def test_backfill_does_not_touch_updated_at(tmp_path):
    """Same rationale as for `set_room`: the device types end up in no
    export template. A bridge startup must not mark half the device list
    as "changed since export"."""
    store = Store(tmp_path / "t.sqlite")
    try:
        snapshot = load("ikea_grillplats_plug.json")
        device_id = store.register_device(snapshot)
        store._db.execute("UPDATE device SET device_types = NULL WHERE id = ?", (device_id,))
        store._db.commit()
        before = store.device(device_id).updated_at

        store.backfill_device_types([snapshot])
        assert store.device(device_id).updated_at == before
    finally:
        store.close()


def test_the_button_leads_with_the_button_press(tmp_path):
    """Replaces `test_the_button_leads_with_a_switch_signal_not_the_battery`,
    which only checked `cluster_id == 59`. That was too weak: `positions`
    (NumberOfPositions, element 0) carries the same cluster and used to sort
    before it - the tile then led with the static fact that this
    button has two positions. The test still said yes.

    This version names the signal explicitly. A test that only checks the
    cluster lets through exactly the bug that the whole rework was meant
    to prevent."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    functional = [s for s in store.signals(device_id) if s.functional]

    assert functional[0].title == "press"
    assert functional[0].ref.kind is SignalKind.EVENT
    assert functional[-1].ref.cluster_id == 47


def test_the_static_position_count_sorts_behind_every_button_event(tmp_path):
    """`positions` never changes - it belongs at the end of the button
    group, not at its start."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    endpoint1 = [s for s in store.signals(device_id) if s.functional and s.ref.endpoint == 1]
    titles = [s.title for s in endpoint1]

    assert titles[0] == "press"
    assert titles[-1] == "positions"


def test_the_plug_still_leads_with_onoff(tmp_path):
    """The two devices that are correct today must not move."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    functional = [s for s in store.signals(device_id) if s.functional]

    assert functional[0].ref.cluster_id == 6
    assert functional[0].title == "onoff"


def test_signals_of_the_same_cluster_keep_the_previous_order(tmp_path):
    """The ranking orders the CLUSTERS relative to each other. Within a
    cluster, endpoint/element/kind keeps the old order - UNLESS an element
    itself carries a rank (task 12, so far only cluster 59).

    Replaces the version from task 2, which claimed this across the board
    for EVERY cluster, using cluster 59 as its example. That has not
    been true since task 12 - `press` (element 1) there deliberately sorts
    before `positions` (element 0), not by element ID. That is not a
    random deviation from the old order, but the whole point of the
    task, so this test does not check cluster 59 against the old order,
    but cluster 47 (PowerSource) - the only other cluster with multiple
    elements on this device, and one that to this day carries no element
    rank."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    signals = store.signals(device_id)
    cluster_47 = [s for s in signals if s.ref.cluster_id == 47]

    # Sort the same signals by the old order (without the ranking)
    old_order_sorted = sorted(
        cluster_47, key=lambda s: (s.ref.endpoint, s.ref.element_id, s.ref.kind.value)
    )

    # The current order must match the old order
    assert cluster_47 == old_order_sorted


def test_the_order_is_total(tmp_path):
    """No signal shares its sort key with another.

    That is the property the export relies on: `signals()`
    feeds `to_inputs` and thereby the order of the inputs in the
    VIU template. If two keys were equal, the input order of
    `sorted` would decide - and that comes from SQLite, so it is
    nothing a file may rely on.

    An earlier attempt compared two calls to `signals()` against each
    other. That was not an assertion: without randomness in the path, two
    calls on unchanged data are ALWAYS equal, even with colliding keys.

    Finding (final review): `assert keys == sorted(keys)` cannot fail for
    ANY implementation - `keys` results from the output of `signals()`,
    which is already sorted by `_signal_order`, so it is necessarily
    non-decreasing regardless of what `_signal_order` does. The comment
    above it claimed a statement of its own ("the delivered order
    must follow the sorted key") that this assert cannot actually
    verify, because it checks nothing INDEPENDENT. Removed rather than
    replaced: the actual sort order (cluster rank before endpoint
    before element rank, `positions` behind `press`, PowerSource behind
    everything functional) already has its own, independently computed
    tests - `test_the_static_position_count_sorts_behind_every_button_event`,
    `test_the_plug_still_leads_with_onoff` and
    `test_signals_of_the_same_cluster_keep_the_previous_order`. This test
    sticks to its one sound statement: the sort key
    is TOTAL, no signal shares it with another."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    signals = store.signals(device_id)
    keys = [_signal_order(s) for s in signals]

    # No sort key may occur twice (totality) - that is
    # the only property this test can check independently.
    assert len(set(keys)) == len(keys)
