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

"""`POST /api/rooms/rename` (Task 5, device-tab design).

There is no separate room resource (design 3.2) - therefore no separate
`test_devices.py` namespace for `GET /api/rooms`, which does not exist.
This file checks exclusively the one route that exists for rooms: renaming
across all devices of a room.

The `api` fixture is the same as in `test_devices.py` - built independently
rather than imported, as the other API test files (`test_export_api.py`,
`test_language.py`, ...) also do respectively."""

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime, fake_client, fake_otbr):
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
    fake_client.store = store

    app = build_app(
        store,
        no_invoke,
        fake_runtime(store),
        client=fake_client,
        thread_dataset_source=fake_otbr,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await authenticate(store, c)
        yield c, store, device_id, fake_client
    store.close()


async def test_renaming_a_room_moves_every_device(api):
    client, store, device_id, _fake = api
    store.set_room(device_id, "Küche")
    response = await client.post("/api/rooms/rename", json={"from": "Küche", "to": "Essbereich"})
    assert response.status_code == 200
    assert response.json() == {"renamed": 1}
    assert store.device(device_id).room == "Essbereich"


async def test_renaming_an_unknown_room_is_a_404(api):
    """Analogous to `GET /devices/{id}` for a removed device: what does not
    exist is not silently turned into a success with zero changes - otherwise
    a typo in the source name would look like a successful operation."""
    client, _store, _device_id, _fake = api
    response = await client.post("/api/rooms/rename", json={"from": "Keller", "to": "Bad"})
    assert response.status_code == 404


async def test_renaming_to_an_empty_name_is_a_422(api):
    client, store, device_id, _fake = api
    store.set_room(device_id, "Küche")
    response = await client.post("/api/rooms/rename", json={"from": "Küche", "to": "   "})
    assert response.status_code == 422
    assert store.device(device_id).room == "Küche"


async def test_renaming_a_room_carried_only_by_a_group_is_no_longer_a_404(api):
    """Final fix pass, item 1: a group carries its own room, independent of
    its members (design 2026-09-10, section 6) - the rename pencil on the
    devices tab appears for such a room exactly as it does for any other
    (`roomChips()` in `app.js` counts groups). Before `Store.rename_room`
    also wrote to `device_group`, this request matched zero DEVICE rows and
    the route (`api/devices.py`) turned that into a 404 "unknown room" -
    for a room that was visibly still on screen, populated by the group
    below."""
    client, store, device_id, _fake = api
    # The device itself carries no room at all - only the group does.
    group = store.create_group("Steckdosen", [device_id], room="Küche")
    assert store.device(device_id).room is None

    response = await client.post("/api/rooms/rename", json={"from": "Küche", "to": "Essbereich"})

    assert response.status_code == 200
    assert response.json() == {"renamed": 1}
    assert store.group(group.id).room == "Essbereich"


async def test_renaming_a_room_with_surrounding_whitespace_in_the_source_still_matches(api):
    """Regression test for the `_normalized_room` fix in `Store.rename_room`
    (Review-Found Task 2): `from` comes here as free text from the JSON body,
    not as a value read back from storage - " Küche " matched zero rows before
    the fix and would have incorrectly passed as a 404."""
    client, store, device_id, _fake = api
    store.set_room(device_id, "Küche")
    response = await client.post(
        "/api/rooms/rename", json={"from": "  Küche  ", "to": "Essbereich"}
    )
    assert response.status_code == 200
    assert response.json() == {"renamed": 1}
    assert store.device(device_id).room == "Essbereich"
