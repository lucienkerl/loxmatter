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

"""The outputs router - listing a device's or group's outputs and choosing
which to export (design 2026-09-24, 4.5)."""

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
        store.register_commands(device_id, extract_commands(snapshot))
        lamps.append(device_id)
    plug_snapshot = load_snapshot("ikea_grillplats_plug.json")
    plug = store.register_device(plug_snapshot)
    store.register_signals(plug, plug_snapshot)
    store.register_commands(plug, extract_commands(plug_snapshot))

    app = build_app(store, no_invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, lamps, plug
    store.close()


async def test_a_lamps_outputs_list_lumitech_first_as_functional(api):
    client, _store, lamps, _plug = api
    body = (await client.get(f"/api/devices/{lamps[0]}/outputs")).json()
    by_slug = {o["slug"]: o for o in body}
    assert by_slug["lumitech"] == {
        "key": f"d{lamps[0]}_1_lumitech",
        "slug": "lumitech",
        "title": "Lumitech / RGB",
        "exported": True,
        "functional": True,
    }
    assert {(o["exported"], o["functional"]) for s, o in by_slug.items() if s != "lumitech"} == {
        (False, False)
    }


async def test_patch_selects_a_single_command_for_export(api):
    """Fault to prove it: have the route ignore `exported` - the flag stays False."""
    client, store, lamps, _plug = api
    key = f"d{lamps[0]}_1_color"
    response = await client.patch(f"/api/commands/{key}", json={"exported": True})
    assert response.status_code == 200
    assert response.json()["exported"] is True
    assert store.resolve_command(key).exported is True


async def test_patch_reaches_a_group_command_too(api):
    client, _store, lamps, _plug = api
    group = (await client.post("/api/groups", json={"label": "G", "member_ids": lamps})).json()
    key = f"g{group['id']}_lumitech"
    response = await client.patch(f"/api/commands/{key}", json={"exported": False})
    assert response.status_code == 200
    outputs = (await client.get(f"/api/groups/{group['id']}/outputs")).json()
    assert {o["key"]: o["exported"] for o in outputs}[key] is False


async def test_patch_on_an_unknown_key_is_a_404(api):
    client, *_ = api
    assert (
        await client.patch("/api/commands/d999_1_on", json={"exported": True})
    ).status_code == 404


async def test_patch_on_a_removed_devices_command_is_a_404(api):
    client, _store, lamps, _plug = api
    await client.delete(f"/api/devices/{lamps[0]}")
    response = await client.patch(f"/api/commands/d{lamps[0]}_1_color", json={"exported": True})
    assert response.status_code == 404


async def test_the_outputs_of_an_unknown_device_are_a_404(api):
    client, *_ = api
    assert (await client.get("/api/devices/999/outputs")).status_code == 404
