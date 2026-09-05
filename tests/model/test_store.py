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
from dataclasses import replace
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.model.store import (
    Store,
    UnknownDeviceError,
    _decode_device_types,
    _encode_device_types,
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
    """Zwei *verschiedene* Geraete bekommen unterschiedliche ids.

    Das beweist nur, dass AUTOINCREMENT funktioniert — es waere auch dann
    gruen, wenn die Spalte `active` gar nicht existierte und
    `register_device` bei jedem Aufruf blind eine neue Zeile anlegte. Die
    eigentlich schuetzenswerte Eigenschaft — dasselbe physische Geraet
    bekommt nach `forget_device` + erneutem Einlernen eine neue id und neue
    Schluessel — prueft stattdessen
    `test_recommissioned_device_gets_fresh_id_and_keys`.
    """
    plug = load("ikea_grillplats_plug.json")
    button = load("ikea_bilresa_button.json")
    first = store.register_device(plug)
    store.forget_device(first)
    assert store.register_device(button) != first


def test_recommissioned_device_gets_fresh_id_and_keys(store):
    """Dasselbe physische Geraet, vergessen und neu eingelernt, muss eine
    neue device_id und neue Schluessel bekommen — sonst wuerde es die alte
    Loxone-Verdrahtung eines frueheren Eigentuemers stillschweigend erben
    (siehe Modul-Docstring in store.py). Das ist die Eigenschaft, die das
    `WHERE unique_id = ? AND active = 1` in `register_device` tatsaechlich
    schuetzt; faellt das `active = 1` weg, findet die Abfrage die alte,
    vergessene Zeile wieder und dieser Test schlaegt fehl.
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
    """Die Fixtures dieses Projekts erzeugen (noch) keine echte Slug-Kollision
    (siehe Kommentar in store.py). Damit die Ausweichstrategie trotzdem
    geprueft ist, wird `lookup` fuer ein einzelnes Cluster gezielt auf einen
    fixen Slug gezwungen: Cluster 3 traegt auf Endpoint 1 zwei Attribute
    (Element-ID 0 und 1) der IKEA-Steckdose, die dadurch denselben Slug
    "fake" erhalten."""
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
    """Drei Signale, die auf demselben Endpoint sowohl denselben Slug als auch
    dieselbe Element-ID (0) tragen, koennen selbst die um die Element-ID
    erweiterte Ausweichstrategie nicht mehr auseinanderhalten. Das darf
    register_signals nicht stillschweigend loesen, indem es das dritte Signal
    verwirft (die Gefahr eines `INSERT OR IGNORE`, siehe Modul-Docstring in
    store.py) — es muss laut scheitern, und das Geraet darf danach keine
    Signale aus diesem gescheiterten Aufruf enthalten."""
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
    """Review-Fix Important #2: `1/6/16387` (StartUpOnOff) meldet bei der
    IKEA-Steckdose anfangs `null` und ist deshalb exportability=none — nicht
    weil das Attribut generell unexportierbar waere, sondern weil gerade kein
    Wert vorliegt. Faengt das Geraet spaeter an, einen echten Wert zu
    melden, muss die naechste Registrierung das nachziehen, ohne den einmal
    vergebenen Schluessel zu aendern. Vorher fror `register_signals` `unit`
    und `exportability` fuer immer ein, sobald ein Signal einmal bekannt war."""
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
    """Review-Fix Important #2: eine Korrektur in `clusters.yaml` muss ein
    schon gespeichertes Signal erreichen. Vorher war die einzige Abhilfe das
    Loeschen der gesamten Datenbank — was auch jeden Schluessel zerstoert
    haette."""
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
    """Review-Fix Important #2: `title` ist Nutzereigentum, sobald `set_title`
    es gesetzt hat, und darf von einem erneuten `register_signals` — anders
    als `unit`/`exportability` — nicht ueberschrieben werden."""
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


def test_device_id_for_node_resolves_a_registered_device(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    assert store.device_id_for_node(snap.node_id) == device_id


def test_device_id_for_node_is_none_for_an_unknown_node(store):
    assert store.device_id_for_node(999) is None


def test_device_id_for_node_ignores_a_forgotten_device(store):
    """Ein entferntes Geraet darf ueber seine alte Node-ID nicht mehr auffindbar sein -
    sonst wuerde eine Laufzeit-Subscription Werte einem inaktiven Geraet zuordnen."""
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    store.forget_device(device_id)
    assert store.device_id_for_node(snap.node_id) is None


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
    assert device.node_id == snap.node_id
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
    """Aufgabe 6: `expected` unten wird bewusst NICHT ueber
    `profiles.table.is_exportable` berechnet, das ist genau die eine Haelfte
    von `register_signals`s eigener Formel - ein Test, der dieselbe Funktion
    wie die Produktion aufruft, kann einen Fehler in genau dieser Funktion
    nie auffangen. Die technische Haelfte bleibt deshalb die unabhaengig
    ausformulierte Regel aus Spec 6.6 (nur ANALOG/DIGITAL passen auf einen
    Loxone-Eingang - Review-Fix Important #2, 2026-09-02).

    Die zweite Haelfte, `is_functional`, wird hier dagegen bewusst
    wiederverwendet statt von Hand nachgebaut: ein erster Versuch, die
    Feinauswahl der Profiltabelle (welches Element von Cluster 144/145
    benannt ist) von Hand in dieses Testmodul zu kopieren, driftete beim
    Schreiben sofort vom echten Stand ab (12 falsche Vorhersagen bei einem
    Testlauf gegen den echten Store). `is_functional` ist keine Funktion
    dieser Aufgabe, sondern eine bereits in `tests/profiles/test_relevance.py`
    unabhaengig gegen genau diese Descriptor-Daten gepruefte Vorstufe
    (Aufgaben 1-2) - sie hier ein zweites Mal nachzubauen haette nur ein
    zweites, staendig nachzupflegendes Abbild derselben Tabelle ergeben,
    keinen staerkeren Test."""
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
    """Das Ziel dieses ganzen Entwurfs, am echten Geraet: fuenf Werte, die
    etwas bedeuten, statt 110 technisch abbildbarer (Entwurf Abschnitt 1 und
    4.4 - 109 war der Stand vor Umsetzung von Abschnitt 5, dem Zaehlerstand)."""
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
    """Der Fall, an dem sich zeigt, ob die Regel zu gierig ist: alle sechs
    Ereignisse beider Wippen muessen durchkommen, dazu der Batteriestand."""
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
    """Nicht geloescht, nur abgewaehlt: der Experten-Block soll ihn
    freischalten koennen, ohne dass das Geraet neu eingelernt wird."""
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)

    counters = [s for s in store.signals(device_id) if s.ref.cluster_id == 53]
    assert counters, "Thread-Zaehler sollen weiterhin gespeichert werden"
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
    """Wie `title`: einmal vom Nutzer gesetzt, darf ein erneutes
    `register_signals` das Export-Flag nicht zuruecksetzen."""
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
    assert target.resend is False  # Vorgabewert (Entwurf, Abschnitt 3)

    store.set_resend(target.key, True)
    after = next(s for s in store.signals(device_id) if s.key == target.key)
    assert after.resend is True
    assert after.key == target.key


def test_resend_flag_survives_reregistration(store):
    """Wie `exported`: einmal vom Nutzer gesetzt, darf ein erneutes
    `register_signals` das Resend-Flag nicht zuruecksetzen."""
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
    """Der einfache Fall: keine offene Transaktion, kein Fehler."""
    store.check_writable()


def test_check_writable_recovers_from_a_leftover_open_transaction(store):
    """Review-Fix Minor (2026-09-02): `rename_device`, `mark_exported`,
    `set_title` und `set_exported` legen kein eigenes try/except um ihr
    `UPDATE ...` plus `commit()` (anders als z. B. `register_signals`) -
    scheitert dort das `UPDATE` selbst oder erst das `commit()`, bleibt die
    von Python vor dem `UPDATE` automatisch eroeffnete Transaktion auf der
    Verbindung offen. Dieser Test simuliert genau das (ein `UPDATE` ohne
    anschliessendes `commit()`/`rollback()`) und prueft, dass
    `check_writable` das nicht mit "nicht beschreibbar" verwechselt - siehe
    Docstring dort."""
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)

    store._db.execute("UPDATE device SET label = ? WHERE id = ?", ("Zwischenstand", device_id))
    assert store._db.in_transaction

    store.check_writable()  # darf trotz der offenen Transaktion nicht werfen


def test_decode_device_types_roundtrips_encode_device_types():
    types = {0: frozenset({22, 18}), 1: frozenset({266})}
    assert _decode_device_types(_encode_device_types(types)) == types


def test_decode_device_types_of_none_is_none():
    assert _decode_device_types(None) is None


def test_decode_device_types_of_syntactically_broken_json_is_none():
    assert _decode_device_types("{nicht json") is None


def test_decode_device_types_of_non_integer_endpoint_key_is_none():
    """Review-Fix (2026-09-05): `int("x")` wirft `ValueError`, nicht die
    bisher abgefangenen `json.JSONDecodeError`/`TypeError` - eine von Hand
    verstellte Zeile mit einem nicht-numerischen Endpunkt-Schluessel liess
    `_decode_device_types` bisher durchbrechen."""
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
    """Ein Name aus reinem Leerraum hat eine eindeutige Bedeutung - "kein
    Raum" - und ist deshalb kein Fehlerfall, sondern derselbe Weg wie ein
    ausdrueckliches `None`."""
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"))
        store.set_room(device_id, "Bad")
        store.set_room(device_id, "   ")
        assert store.device(device_id).room is None
    finally:
        store.close()


def test_set_room_does_not_touch_updated_at(tmp_path):
    """Der Kern der Entscheidung aus Abschnitt 3.3 des Entwurfs: der Raum
    landet in KEINER Exportvorlage. Wuerde `set_room` `updated_at` mitsetzen,
    bekaeme beim ersten Aufraeumen der Raumzuordnung jedes Geraet eine amber
    "geaendert seit Export"-Pille und die Aufforderung zu einem Export, der
    Byte fuer Byte dieselben Dateien erzeugt. `rename_device` setzt es
    dagegen zu Recht - das Label wird als `Title` exportiert."""
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
    """Ein Zielname, den es schon gibt, fuehrt beide Raeume zusammen - die
    naheliegende Bedeutung von "nenne Kueche jetzt Essbereich", wenn es
    einen Essbereich schon gibt. Die Oberflaeche fragt vorher nach; der
    Store fuehrt nur aus."""
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
    """`active = 1` in der Bedingung, aus demselben Grund, aus dem
    `Store.devices()` danach filtert: ein entferntes Geraet ist aus Sicht
    der Oberflaeche nicht mehr da und soll nicht stillschweigend mitwandern."""
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


def test_register_device_stores_the_matter_device_types(tmp_path):
    """Endpunkt 1 der Steckdose meldet 266 (0x010A, On/Off Plug-in Unit),
    Endpunkt 0 die Verwaltungstypen - beide werden roh abgelegt, gefiltert
    wird erst beim Ableiten der Kategorie."""
    store = Store(tmp_path / "t.sqlite")
    try:
        device_id = store.register_device(load("ikea_grillplats_plug.json"))
        types = store.device(device_id).device_types
        assert types is not None
        assert types[1] == frozenset({0x010A})
    finally:
        store.close()


def test_backfill_fills_only_rows_that_have_none(tmp_path):
    """Eine Bestandszeile bekommt ihre Typen beim naechsten Bruueckenstart -
    eine bereits gefuellte wird nicht bei jedem Start neu geschrieben."""
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
    """Ein Geraet, das beim Start gerade offline ist, fehlt in
    `client.snapshots()`. Es darf dadurch nichts verlieren - deshalb wird
    nur geschrieben, wo ein Abbild vorliegt, und nie geleert."""
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
    """Dieselbe Begruendung wie bei `set_room`: die Geraetetypen landen in
    keiner Exportvorlage. Ein Bruueckenstart darf nicht die halbe
    Geraeteliste als "geaendert seit Export" markieren."""
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
