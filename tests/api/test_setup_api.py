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

"""`/api/setup` - where the first-run assistant stands (design 2026-11-07)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[tuple[httpx.AsyncClient, Store]]:
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store
    store.close()


async def test_a_new_installation_starts_the_assistant(api):
    client, _ = api

    response = await client.get("/api/setup")

    assert response.status_code == 200
    assert response.json() == {"completed": False, "step": None, "path": None, "zigbee": None}


async def test_an_installation_with_devices_from_before_the_assistant_never_sees_it(api):
    client, store = api
    device_id = store.register_device(load_snapshot("example_light.json"))

    first = await client.get("/api/setup")
    store.forget_device(device_id)
    second = await client.get("/api/setup")

    assert first.json()["completed"] is True
    # Written down on the first ask: removing the last device later does
    # not bring the assistant back.
    assert second.json()["completed"] is True


async def test_the_step_and_path_are_kept_for_a_reload(api):
    client, _ = api

    await client.patch("/api/setup", json={"path": "ha"})
    await client.patch("/api/setup", json={"step": "ha_login"})
    await client.patch("/api/setup", json={"zigbee": "own"})
    response = await client.get("/api/setup")

    assert response.json() == {
        "completed": False,
        "step": "ha_login",
        "path": "ha",
        "zigbee": "own",
    }


async def test_an_unknown_zigbee_answer_is_refused(api):
    client, _ = api

    response = await client.patch("/api/setup", json={"zigbee": "zigbee2mqtt"})

    assert response.status_code == 422


async def test_an_unknown_path_is_refused(api):
    client, _ = api

    response = await client.patch("/api/setup", json={"path": "zigbee2mqtt"})

    assert response.status_code == 422


async def test_completing_ends_the_assistant_and_a_patch_afterwards_is_refused(api):
    client, _ = api

    done = await client.post("/api/setup/complete")
    patch = await client.patch("/api/setup", json={"step": "welcome"})

    assert done.json()["completed"] is True
    assert patch.status_code == 409


async def test_restarting_opens_the_assistant_at_the_path_choice(api):
    client, _ = api
    await client.post("/api/setup/complete")

    response = await client.post("/api/setup/restart")

    assert response.json() == {"completed": False, "step": "path", "path": None, "zigbee": None}


async def test_the_routes_need_a_session(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/setup")
    store.close()

    assert response.status_code == 401
