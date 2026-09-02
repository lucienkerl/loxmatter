import json
from dataclasses import replace
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.model.store import Store, UnknownDeviceError
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
            return Profile(slug="fake", unit="", exportability=Exportability.DIGITAL)
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
            return Profile(slug="fake", unit="", exportability=Exportability.DIGITAL)
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
                return Profile(slug="onoff", unit=unit, exportability=Exportability.DIGITAL)
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


def test_new_signal_is_exported_exactly_when_it_is_exportable(store):
    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    signals = store.register_signals(device_id, snap)
    for signal in signals:
        expected = signal.exportability is not Exportability.NONE
        assert signal.exported is expected


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
