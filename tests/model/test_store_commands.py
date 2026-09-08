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

from loxmatter import i18n
from loxmatter.export.commands import DeviceCommand, extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.profiles import table

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    yield s
    s.close()


def registered(store: Store, name: str):
    snap = load(name)
    device_id = store.register_device(snap)
    store.register_signals(device_id, snap)
    commands = store.register_commands(device_id, extract_commands(snap), snap.node_id)
    return device_id, snap, commands


def test_plug_commands_are_resolvable_by_their_exported_key(store):
    device_id, snap, _ = registered(store, "ikea_grillplats_plug.json")
    resolved = store.resolve_command(f"d{device_id}_1_on")
    assert resolved.cluster_id == 6
    assert resolved.command_id == 1
    assert resolved.endpoint == 1
    assert resolved.node_id == snap.node_id


def test_unknown_key_raises_with_a_clear_message(store):
    registered(store, "ikea_grillplats_plug.json")
    with pytest.raises(KeyError, match="unknown command key") as excinfo:
        store.resolve_command("d1_1_gibtsnicht")
    # Review-Fix Minor: str(KeyError(...)) haengt sonst repr()-Anfuehrungszeichen
    # um die ganze Nachricht — das wuerde Task 6s HTTP-Body verunstalten.
    assert str(excinfo.value) == "unknown command key 'd1_1_gibtsnicht'"


def test_unknown_key_raises_with_a_german_message(store):
    """Deutsches Gegenstueck zu `test_unknown_key_raises_with_a_clear_message`
    oben."""
    i18n.set_language("de")
    registered(store, "ikea_grillplats_plug.json")
    with pytest.raises(KeyError, match="unbekannter Kommando-Schluessel") as excinfo:
        store.resolve_command("d1_1_gibtsnicht")
    assert str(excinfo.value) == "unbekannter Kommando-Schluessel 'd1_1_gibtsnicht'"


def test_button_registers_no_commands(store):
    _, _, commands = registered(store, "ikea_bilresa_button.json")
    assert commands == []


def test_reregistering_is_idempotent(store):
    device_id, snap, first = registered(store, "ikea_grillplats_plug.json")
    again = store.register_commands(device_id, extract_commands(snap), snap.node_id)
    assert [c.key for c in again] == [c.key for c in first]


def test_command_keys_match_the_exported_scheme(store):
    device_id, _, commands = registered(store, "ikea_grillplats_plug.json")
    assert sorted(c.key for c in commands) == [
        f"d{device_id}_1_off",
        f"d{device_id}_1_on",
        f"d{device_id}_1_toggle",
    ]


def test_node_id_is_stored_so_the_runtime_can_address_the_device(store):
    _, snap, commands = registered(store, "ikea_grillplats_plug.json")
    assert {c.node_id for c in commands} == {snap.node_id}


def test_command_key_collision_raises_instead_of_dropping_silently(store, monkeypatch):
    """Review-Fix Important #1: zwei Kommandos verschiedener Cluster auf
    demselben Endpoint koennen denselben Slug bekommen — ein zukuenftiger
    Eintrag in `clusters.yaml` fuer einen zweiten Cluster auf einem Endpoint,
    der sich schon einen Slug mit `onoff`/`level` teilt, ist eine ganz
    gewoehnliche Matter-Anordnung. `command_slug` wird hier gezielt auf einen
    festen Wert gezwungen, um genau das nachzustellen: Cluster 3 (Identify)
    bekommt auf Endpoint 1 denselben Slug "on" wie Cluster 6s Kommando 1.
    Das darf `register_commands` nicht stillschweigend mit `INSERT OR
    IGNORE` loesen (die Gefahr aus dem Modul-Docstring von `register_signals`)
    — es muss laut scheitern, und das Geraet darf danach keine Kommandos aus
    diesem gescheiterten Aufruf enthalten."""
    real_command_slug = table.command_slug

    def fake_command_slug(cluster_id: int, command_id: int) -> str | None:
        if cluster_id == 3 and command_id == 0:
            return "on"
        return real_command_slug(cluster_id, command_id)

    monkeypatch.setattr("loxmatter.export.commands.command_slug", fake_command_slug)

    snap = load("ikea_grillplats_plug.json")
    device_id = store.register_device(snap)
    commands = extract_commands(snap)
    assert {(c.cluster_id, c.command_id, c.slug) for c in commands} >= {
        (3, 0, "on"),
        (6, 1, "on"),
    }

    with pytest.raises(ValueError, match="Schluessel-Kollision"):
        store.register_commands(device_id, commands, snap.node_id)

    assert store.commands(device_id) == []


def test_takes_value_change_is_picked_up_on_reregistration(store):
    """Review-Fix Important #2: anders als bei Signalen fror `register_commands`
    `takes_value` beim ersten Einlernen fuer immer ein. Eine Korrektur in
    `clusters.yaml` — ein Kommando, das nachtraeglich als wertnehmend erkannt
    wird — erreichte ein schon gespeichertes Kommando nie. Der Schluessel
    muss dabei unveraendert bleiben (Spec 6.2)."""
    device_id, snap, first = registered(store, "ikea_grillplats_plug.json")
    on_before = next(c for c in first if c.slug == "on")
    assert on_before.takes_value is False

    updated = [
        DeviceCommand(
            endpoint=on_before.endpoint,
            cluster_id=on_before.cluster_id,
            command_id=on_before.command_id,
            slug=on_before.slug,
            takes_value=True,
        )
    ]
    again = store.register_commands(device_id, updated, snap.node_id)

    on_after = next(c for c in again if c.key == on_before.key)
    assert on_after.takes_value is True
    assert on_after.key == on_before.key


def test_backfill_adds_a_command_that_was_locked_when_the_device_was_learned(tmp_path):
    """Der Fall aus dem Betrieb (8. September 2026): eine RGB-Leuchte wurde
    eingelernt, als `MoveToHueAndSaturation` (768/6) noch gesperrt war.

    `extract_commands` verwarf den Befehl damals, und in der Tabelle steht
    seither keine Zeile dafuer. Ein Update des Codes traegt sie nicht nach -
    `register_commands` lief bis dahin nur beim Einlernen und beim
    CLI-Export -, und die Kachel zeigte deshalb kein Farb-Bedienelement,
    obwohl die Leuchte es laengst konnte.
    """
    store = Store(tmp_path / "t.sqlite")
    try:
        snapshot = load("ikea_kajplats_cws_lamp.json")
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        # Der alte Stand: dieselbe Extraktion ohne das damals gesperrte Paar.
        alt = [c for c in extract_commands(snapshot) if (c.cluster_id, c.command_id) != (768, 6)]
        store.register_commands(device_id, alt, snapshot.node_id)
        assert not any(c.cluster_id == 768 and c.command_id == 6 for c in store.commands(device_id))

        assert store.backfill_commands([snapshot]) == 1

        farbe = [c for c in store.commands(device_id) if (c.cluster_id, c.command_id) == (768, 6)]
        assert len(farbe) == 1
        assert farbe[0].slug == "color"
        assert farbe[0].takes_value is True
    finally:
        store.close()


def test_backfill_keeps_the_keys_of_commands_that_already_exist(tmp_path):
    """Der Schluessel ist die Verdrahtung in Loxone und darf sich nie
    bewegen. `backfill_commands` laeuft bei JEDEM Start - wuerde es
    bestehende Schluessel neu vergeben, zerschoesse der erste Neustart nach
    einem Update jede Loxone-Konfiguration."""
    store = Store(tmp_path / "t.sqlite")
    try:
        snapshot = load("ikea_kajplats_cws_lamp.json")
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        alt = [c for c in extract_commands(snapshot) if (c.cluster_id, c.command_id) != (768, 6)]
        store.register_commands(device_id, alt, snapshot.node_id)
        vorher = {(c.cluster_id, c.command_id): c.key for c in store.commands(device_id)}

        store.backfill_commands([snapshot])

        nachher = {(c.cluster_id, c.command_id): c.key for c in store.commands(device_id)}
        for paar, key in vorher.items():
            assert nachher[paar] == key

    finally:
        store.close()


def test_backfill_reports_nothing_to_do_when_every_command_is_present(tmp_path):
    """Zweiter Start nach dem Update: nichts mehr nachzutragen.

    Der Rueckgabewert zaehlt Geraete, bei denen ein Kommando DAZUKAM - nicht
    Schreibvorgaenge. `backfill_commands` frischt `slug` und `takes_value`
    bewusst bei jedem Start auf (siehe dort), damit auch eine Umbenennung in
    `clusters.yaml` ein Bestandsgeraet erreicht; gemeldet wird trotzdem nur
    die Aenderung, die jemanden interessiert."""
    store = Store(tmp_path / "t.sqlite")
    try:
        snapshot = load("ikea_kajplats_cws_lamp.json")
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

        assert store.backfill_commands([snapshot]) == 0
    finally:
        store.close()


def test_backfill_leaves_a_device_missing_from_the_snapshots_untouched(tmp_path):
    """Ein Geraet, das beim Start gerade offline ist, fehlt in
    `client.snapshots()`. Es darf dadurch nichts verlieren - dieselbe Regel
    wie bei `backfill_device_types`."""
    store = Store(tmp_path / "t.sqlite")
    try:
        lampe = load("ikea_kajplats_cws_lamp.json")
        stecker = load("ikea_grillplats_plug.json")
        lampen_id = store.register_device(lampe)
        store.register_signals(lampen_id, lampe)
        alt = [c for c in extract_commands(lampe) if (c.cluster_id, c.command_id) != (768, 6)]
        store.register_commands(lampen_id, alt, lampe.node_id)
        stecker_id = store.register_device(stecker)
        store.register_signals(stecker_id, stecker)
        store.register_commands(stecker_id, extract_commands(stecker), stecker.node_id)
        stecker_vorher = len(store.commands(stecker_id))

        # Nur das Abbild der Lampe liegt vor - der Stecker ist gerade offline.
        assert store.backfill_commands([lampe]) == 1
        assert len(store.commands(stecker_id)) == stecker_vorher
    finally:
        store.close()
