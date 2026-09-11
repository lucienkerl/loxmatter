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
    commands = store.register_commands(device_id, extract_commands(snap))
    return device_id, snap, commands


def test_plug_commands_are_resolvable_by_their_exported_key(store):
    device_id, snap, _ = registered(store, "ikea_grillplats_plug.json")
    resolved = store.resolve_command(f"d{device_id}_1_on")
    assert resolved.cluster_id == 6
    assert resolved.command_id == 1
    assert resolved.endpoint == 1
    assert resolved.address == snap.address


def test_unknown_key_raises_with_a_clear_message(store):
    registered(store, "ikea_grillplats_plug.json")
    with pytest.raises(KeyError, match="unknown command key") as excinfo:
        store.resolve_command("d1_1_gibtsnicht")
    # Review-Fix Minor: str(KeyError(...)) would otherwise wrap repr() quotes
    # around the whole message — that would disfigure task 6's HTTP body.
    assert str(excinfo.value) == "unknown command key 'd1_1_gibtsnicht'"


def test_unknown_key_raises_with_a_german_message(store):
    """German counterpart to `test_unknown_key_raises_with_a_clear_message`
    above."""
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
    again = store.register_commands(device_id, extract_commands(snap))
    assert [c.key for c in again] == [c.key for c in first]


def test_command_keys_match_the_exported_scheme(store):
    device_id, _, commands = registered(store, "ikea_grillplats_plug.json")
    assert sorted(c.key for c in commands) == [
        f"d{device_id}_1_off",
        f"d{device_id}_1_on",
        f"d{device_id}_1_toggle",
    ]


def test_a_command_carries_its_owning_devices_address(store):
    """A command's `(technology, address)` come from the owning device via
    the join in `Store._COMMAND_SELECT`, not a value passed in at
    registration time - see `register_commands`."""
    _, snap, commands = registered(store, "ikea_grillplats_plug.json")
    assert {c.address for c in commands} == {snap.address}


def test_command_key_collision_raises_instead_of_dropping_silently(store, monkeypatch):
    """Review-Fix Important #1: two commands of different clusters on the
    same endpoint can get the same slug — a future entry in `clusters.yaml`
    for a second cluster on an endpoint that already shares a slug with
    `onoff`/`level` is a perfectly ordinary Matter arrangement.
    `command_slug` is deliberately forced to a fixed value here to reproduce
    exactly that: cluster 3 (Identify) gets the same slug "on" on endpoint 1
    as cluster 6's command 1. `register_commands` must not silently resolve
    that with `INSERT OR IGNORE` (the danger from the module docstring of
    `register_signals`) — it must fail loudly, and the device must not
    contain any commands from this failed call afterward."""
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

    with pytest.raises(ValueError, match="key collision"):
        store.register_commands(device_id, commands)

    assert store.commands(device_id) == []


def test_takes_value_change_is_picked_up_on_reregistration(store):
    """Review-Fix Important #2: unlike for signals, `register_commands` froze
    `takes_value` forever on first commissioning. A correction in
    `clusters.yaml` — a command later recognized as taking a value — never
    reached a command that was already stored. The key must stay unchanged
    in the process (spec 6.2)."""
    device_id, _snap, first = registered(store, "ikea_grillplats_plug.json")
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
    again = store.register_commands(device_id, updated)

    on_after = next(c for c in again if c.key == on_before.key)
    assert on_after.takes_value is True
    assert on_after.key == on_before.key


def test_backfill_adds_a_command_that_was_locked_when_the_device_was_learned(tmp_path):
    """The case from production (September 8, 2026): an RGB lamp was
    commissioned while `MoveToHueAndSaturation` (768/6) was still locked.

    `extract_commands` discarded the command back then, and the table has
    had no row for it ever since. A code update does not backfill it -
    `register_commands` used to run only during commissioning and on
    CLI export -, so the tile showed no color control, even though the
    lamp had long been capable of it.
    """
    store = Store(tmp_path / "t.sqlite")
    try:
        snapshot = load("ikea_kajplats_cws_lamp.json")
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        # The old state: the same extraction without the pair that was locked back then.
        alt = [c for c in extract_commands(snapshot) if (c.cluster_id, c.command_id) != (768, 6)]
        store.register_commands(device_id, alt)
        assert not any(c.cluster_id == 768 and c.command_id == 6 for c in store.commands(device_id))

        assert store.backfill_commands([snapshot]) == 1

        farbe = [c for c in store.commands(device_id) if (c.cluster_id, c.command_id) == (768, 6)]
        assert len(farbe) == 1
        assert farbe[0].slug == "color"
        assert farbe[0].takes_value is True
    finally:
        store.close()


def test_backfill_keeps_the_keys_of_commands_that_already_exist(tmp_path):
    """The key is the wiring in Loxone and must never
    move. `backfill_commands` runs on EVERY start - if it were to
    reassign existing keys, the first restart after an
    update would smash every Loxone configuration."""
    store = Store(tmp_path / "t.sqlite")
    try:
        snapshot = load("ikea_kajplats_cws_lamp.json")
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        alt = [c for c in extract_commands(snapshot) if (c.cluster_id, c.command_id) != (768, 6)]
        store.register_commands(device_id, alt)
        vorher = {(c.cluster_id, c.command_id): c.key for c in store.commands(device_id)}

        store.backfill_commands([snapshot])

        nachher = {(c.cluster_id, c.command_id): c.key for c in store.commands(device_id)}
        for paar, key in vorher.items():
            assert nachher[paar] == key

    finally:
        store.close()


def test_backfill_reports_nothing_to_do_when_every_command_is_present(tmp_path):
    """Second start after the update: nothing left to backfill.

    The return value counts devices where a command was ADDED - not
    write operations. `backfill_commands` deliberately refreshes `slug`
    and `takes_value` on every start (see there), so that a rename in
    `clusters.yaml` also reaches an existing device; still, only the
    change that someone actually cares about is reported."""
    store = Store(tmp_path / "t.sqlite")
    try:
        snapshot = load("ikea_kajplats_cws_lamp.json")
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot))

        assert store.backfill_commands([snapshot]) == 0
    finally:
        store.close()


def test_backfill_leaves_a_device_missing_from_the_snapshots_untouched(tmp_path):
    """A device that is offline right at start is missing from
    `client.snapshots()`. It must not lose anything because of that - the
    same rule as for `backfill_device_types`."""
    store = Store(tmp_path / "t.sqlite")
    try:
        lampe = load("ikea_kajplats_cws_lamp.json")
        stecker = load("ikea_grillplats_plug.json")
        lampen_id = store.register_device(lampe)
        store.register_signals(lampen_id, lampe)
        alt = [c for c in extract_commands(lampe) if (c.cluster_id, c.command_id) != (768, 6)]
        store.register_commands(lampen_id, alt)
        stecker_id = store.register_device(stecker)
        store.register_signals(stecker_id, stecker)
        store.register_commands(stecker_id, extract_commands(stecker))
        stecker_vorher = len(store.commands(stecker_id))

        # Only the lamp's snapshot is present - the plug is currently offline.
        assert store.backfill_commands([lampe]) == 1
        assert len(store.commands(stecker_id)) == stecker_vorher
    finally:
        store.close()
