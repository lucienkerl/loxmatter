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

"""What the assistant's device list makes of Home Assistant's answers."""

from __future__ import annotations

from fake_homeassistant import FakeHomeAssistant

from loxmatter.homeassistant.inventory import Capabilities, read_inventory

EVERYTHING = Capabilities(thread=True, zigbee=True)


async def test_matter_devices_are_listed_with_their_kind_name_and_room():
    inventory = await read_inventory(FakeHomeAssistant(), EVERYTHING)

    plug = inventory.device("dev-plug")
    assert plug is not None
    assert (plug.name, plug.room, plug.kind, plug.availability) == (
        "Kaffeemaschine",
        "Küche",
        "matter_wifi",
        "ready",
    )
    motion = inventory.device("dev-motion")
    assert motion is not None
    assert motion.kind == "matter_thread"


async def test_a_matter_bridge_is_one_entry_with_its_bridged_devices_as_members():
    inventory = await read_inventory(FakeHomeAssistant(), EVERYTHING)

    bridge = inventory.device("dev-bridge")
    assert bridge is not None
    assert bridge.kind == "matter_bridge"
    assert bridge.members == ("Hue lamp",)
    assert inventory.device("dev-bridged") is None


async def test_a_thread_device_needs_a_thread_stick():
    inventory = await read_inventory(FakeHomeAssistant(), Capabilities(thread=False, zigbee=True))

    motion = inventory.device("dev-motion")
    assert motion is not None
    assert motion.availability == "needs_thread"


async def test_zigbee_devices_carry_their_signature_and_a_remote_comes_later():
    inventory = await read_inventory(FakeHomeAssistant(), EVERYTHING)

    door = inventory.device("dev-door")
    assert door is not None
    assert (door.name, door.room, door.kind, door.availability) == (
        "Haustür",
        "Flur",
        "zigbee",
        "ready",
    )
    assert door.zigbee is not None
    assert door.zigbee.ieee == "00:15:8d:00:01:02:03:04"
    remote = inventory.device("dev-remote")
    assert remote is not None
    assert remote.availability == "later"


async def test_a_zigbee_device_without_a_stick_says_so():
    inventory = await read_inventory(FakeHomeAssistant(), Capabilities(thread=True, zigbee=False))

    door = inventory.device("dev-door")
    assert door is not None
    assert door.availability == "needs_zigbee"


async def test_other_devices_are_shown_but_not_offered_and_services_are_left_out():
    inventory = await read_inventory(FakeHomeAssistant(), EVERYTHING)

    sonos = inventory.device("dev-sonos")
    assert sonos is not None
    assert (sonos.kind, sonos.availability, sonos.integration) == ("other", "unsupported", "sonos")
    assert inventory.device("dev-sun") is None


async def test_a_device_moved_before_is_marked_done():
    capabilities = Capabilities(thread=True, zigbee=True, done=frozenset({"dev-plug"}))

    inventory = await read_inventory(FakeHomeAssistant(), capabilities)

    plug = inventory.device("dev-plug")
    assert plug is not None
    assert plug.availability == "done"


async def test_the_preferred_thread_network_and_the_integrations_are_reported():
    ha = FakeHomeAssistant()
    ha.zha_disabled = True

    inventory = await read_inventory(ha, EVERYTHING)

    body = inventory.as_json()
    assert body["thread_network"] == {
        "dataset_id": "ds1",
        "network_name": "ha-thread-4f2a",
        "channel": 15,
        "extended_pan_id": "1111111122222222",
        "preferred": True,
    }
    assert (body["zha"], body["zha_paused"], body["matter"]) == (True, True, True)


async def test_reading_the_inventory_changes_nothing_in_home_assistant():
    ha = FakeHomeAssistant()

    await read_inventory(ha, EVERYTHING)

    assert set(ha.commands()) <= {
        "config/device_registry/list",
        "config/area_registry/list",
        "config_entries/get",
        "matter/node_diagnostics",
        "zha/devices",
        "thread/list_datasets",
    }
