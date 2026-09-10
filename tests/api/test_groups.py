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

"""The groups router - design 2026-09-10, sections 5 and 6."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


@pytest.fixture
async def api(
    tmp_path, no_invoke, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, list[int], int]]:
    store = Store(tmp_path / "t.sqlite")
    lamps: list[int] = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = load_snapshot(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
        lamps.append(device_id)
    plug_snapshot = load_snapshot("ikea_grillplats_plug.json")
    plug = store.register_device(plug_snapshot)
    store.register_signals(plug, plug_snapshot)
    store.register_commands(plug, extract_commands(plug_snapshot), plug_snapshot.node_id)

    app = build_app(store, no_invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, lamps, plug
    store.close()


async def test_creating_and_listing_a_group(api):
    client, _store, lamps, _plug = api
    created = await client.post(
        "/api/groups", json={"label": "Living room", "member_ids": lamps, "room": "Living room"}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["category"] == "light"
    assert body["member_ids"] == lamps
    listed = await client.get("/api/groups")
    assert [g["id"] for g in listed.json()] == [body["id"]]


async def test_a_member_of_another_category_is_a_400(api):
    client, _store, lamps, plug = api
    response = await client.post(
        "/api/groups", json={"label": "Mixed", "member_ids": [lamps[0], plug]}
    )
    assert response.status_code == 400


async def test_an_empty_member_list_is_a_400(api):
    client, _store, _lamps, _plug = api
    response = await client.post("/api/groups", json={"label": "Empty", "member_ids": []})
    assert response.status_code == 400


async def test_label_and_room_can_be_patched(api):
    client, _store, lamps, _plug = api
    group_id = (await client.post("/api/groups", json={"label": "A", "member_ids": lamps})).json()[
        "id"
    ]
    response = await client.patch(f"/api/groups/{group_id}", json={"label": "B", "room": "Hallway"})
    assert response.status_code == 200
    assert (response.json()["label"], response.json()["room"]) == ("B", "Hallway")


async def test_replacing_the_members_recomputes_the_commands(api):
    client, _store, lamps, _plug = api
    group_id = (
        await client.post("/api/groups", json={"label": "A", "member_ids": [lamps[0]]})
    ).json()["id"]
    before = (await client.get(f"/api/groups/{group_id}/controls")).json()
    response = await client.put(f"/api/groups/{group_id}/members", json={"member_ids": lamps})
    assert response.status_code == 200
    after = (await client.get(f"/api/groups/{group_id}/controls")).json()
    slugs_before = {c["slug"] for c in before["commands"]}
    slugs_after = {c["slug"] for c in after["commands"]}
    assert "color" in slugs_before
    assert "color" not in slugs_after


async def test_a_group_can_be_deleted(api):
    client, _store, lamps, _plug = api
    group_id = (await client.post("/api/groups", json={"label": "A", "member_ids": lamps})).json()[
        "id"
    ]
    assert (await client.delete(f"/api/groups/{group_id}")).status_code == 204
    assert (await client.get(f"/api/groups/{group_id}")).status_code == 404


async def test_an_unknown_group_is_a_404(api):
    """Every route behind `_require` shares its guard - `GET .../controls`,
    `PATCH`, `PUT .../members` and `DELETE` all need to answer 404 for a
    group id that does not exist, not just the plain `GET`."""
    client, _store, lamps, _plug = api
    assert (await client.get("/api/groups/999")).status_code == 404
    assert (await client.get("/api/groups/999/controls")).status_code == 404
    assert (await client.patch("/api/groups/999", json={"label": "X"})).status_code == 404
    assert (
        await client.put("/api/groups/999/members", json={"member_ids": lamps})
    ).status_code == 404
    assert (await client.delete("/api/groups/999")).status_code == 404


async def test_a_duplicate_member_id_is_a_400(api):
    """`Store.set_group_members` raises a bare `ValueError` for a
    duplicate member id (see the `except (CategoryMismatchError,
    ValueError)` in `replace_members`) - without that case in the except
    tuple, this would surface as a 500 instead of a 400."""
    client, store, lamps, _plug = api
    group_id = (await client.post("/api/groups", json={"label": "A", "member_ids": lamps})).json()[
        "id"
    ]
    response = await client.put(
        f"/api/groups/{group_id}/members", json={"member_ids": [lamps[0], lamps[0]]}
    )
    assert response.status_code == 400
    assert [device.id for device in store.group_members(group_id)] == lamps


async def test_the_controls_name_the_device_the_initial_value_came_from(api):
    """Six lamps have six brightnesses; the tile attributes the number it
    shows instead of presenting it as the group's (design 5)."""
    client, _store, lamps, _plug = api
    group_id = (await client.post("/api/groups", json={"label": "A", "member_ids": lamps})).json()[
        "id"
    ]
    body = (await client.get(f"/api/groups/{group_id}/controls")).json()
    assert body["seed_device_id"] == lamps[0]
    assert body["seed_device_label"]


async def test_groups_need_authentication(tmp_path, no_invoke, fake_runtime, fake_client):
    """Every /api route sits behind the guard - this fixture deliberately
    never calls `authenticate`. With no token configured and no session,
    the guard answers 401 (tests/api/test_security.py,
    test_guard_rejects_everything_when_no_token_is_configured_and_no_session_exists)."""
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/groups")
    store.close()
    assert response.status_code == 401
