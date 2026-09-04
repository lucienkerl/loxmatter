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
