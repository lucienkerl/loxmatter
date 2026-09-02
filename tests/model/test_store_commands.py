import json
from pathlib import Path

import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

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


def test_unknown_key_raises_with_a_german_message(store):
    registered(store, "ikea_grillplats_plug.json")
    with pytest.raises(KeyError, match="unbekannter Kommando-Schluessel"):
        store.resolve_command("d1_1_gibtsnicht")


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
