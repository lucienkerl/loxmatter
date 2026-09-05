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

"""`POST /api/rooms/rename` (Task 5, Geraete-Tab-Entwurf).

Es gibt keine eigene Raum-Ressource (Entwurf 3.2) - deshalb auch keine
eigene `test_devices.py`-Nachbarschaft fuer `GET /api/rooms`, die es nicht
gibt. Diese Datei prueft ausschliesslich die eine Route, die es fuer
Raeume gibt: das Umbenennen ueber alle Geraete eines Raums hinweg.

Die `api`-Fixture ist dieselbe wie in `test_devices.py` - eigenstaendig
nachgebaut statt importiert, wie es die anderen API-Testdateien
(`test_export_api.py`, `test_language.py`, ...) ebenfalls jeweils tun."""

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
    """Analog zu `GET /devices/{id}` fuer ein entferntes Geraet: was nicht da
    ist, wird nicht stillschweigend zu einem Erfolg mit null Aenderungen -
    sonst saehe ein Tippfehler im Quellnamen wie ein geglueckter Vorgang aus."""
    client, _store, _device_id, _fake = api
    response = await client.post("/api/rooms/rename", json={"from": "Keller", "to": "Bad"})
    assert response.status_code == 404


async def test_renaming_to_an_empty_name_is_a_422(api):
    client, store, device_id, _fake = api
    store.set_room(device_id, "Küche")
    response = await client.post("/api/rooms/rename", json={"from": "Küche", "to": "   "})
    assert response.status_code == 422
    assert store.device(device_id).room == "Küche"


async def test_renaming_a_room_with_surrounding_whitespace_in_the_source_still_matches(api):
    """Regressionstest zur `_normalized_room`-Korrektur in `Store.rename_room`
    (Review-Fund Task 2): `from` kommt hier als Freitext aus dem JSON-Koerper,
    nicht als aus dem Speicher zurueckgelesener Wert - " Küche " traf vor der
    Korrektur null Zeilen und waere faelschlich als 404 durchgekommen."""
    client, store, device_id, _fake = api
    store.set_room(device_id, "Küche")
    response = await client.post(
        "/api/rooms/rename", json={"from": "  Küche  ", "to": "Essbereich"}
    )
    assert response.status_code == 200
    assert response.json() == {"renamed": 1}
    assert store.device(device_id).room == "Essbereich"
