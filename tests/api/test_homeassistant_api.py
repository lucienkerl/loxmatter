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

"""`/api/homeassistant/*` - the routes in front of the migration."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter.homeassistant.client import HomeAssistantAuthError
from loxmatter.homeassistant.migration import HomeAssistantMigration
from loxmatter.loxone.server import build_app
from loxmatter.matter.commissioning_progress import CommissioningTracker
from loxmatter.model.homeassistant_store import HomeAssistantConnection
from loxmatter.model.store import Store


class TinyHomeAssistant:
    def __init__(self) -> None:
        self.fail: Exception | None = None

    def factory(self, url: str, token: str) -> contextlib.AbstractAsyncContextManager[Any]:
        @contextlib.asynccontextmanager
        async def _session() -> AsyncIterator[TinyHomeAssistant]:
            yield self

        return _session()

    async def call(self, command: str, **payload: Any) -> Any:
        if self.fail is not None:
            raise self.fail
        if command == "config_entries/get":
            return []
        if command == "thread/list_datasets":
            return {"datasets": []}
        return []

    async def delete(self, path: str) -> None:
        return None


@pytest.fixture
async def api(
    tmp_path, no_invoke, fake_runtime
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, TinyHomeAssistant]]:
    store = Store(tmp_path / "t.sqlite")
    ha = TinyHomeAssistant()
    runtime = fake_runtime(store)

    async def no_dataset() -> str:
        raise AssertionError("not needed")

    migration = HomeAssistantMigration(
        store,
        client=None,
        runtime=runtime,
        tracker=CommissioningTracker(),
        fetch_dataset=no_dataset,
        thread_network=None,
        zigbee_runtime=None,
        api_factory=ha.factory,
    )
    app = build_app(store, no_invoke, runtime, homeassistant=migration)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, ha
    store.close()


def sign_in(store: Store) -> None:
    store.homeassistant.save_connection(
        HomeAssistantConnection(
            url="http://ha:8123",
            client_id=None,
            access_token="t",
            refresh_token=None,
            expires_at=None,
        )
    )


async def test_the_status_says_whether_this_bridge_is_signed_in(api):
    client, store, _ = api

    before = await client.get("/api/homeassistant")
    sign_in(store)
    after = await client.get("/api/homeassistant")

    assert before.json()["signed_in"] is False
    assert after.json() == {"signed_in": True, "url": "http://ha:8123", "job": None}


async def test_the_status_never_carries_a_token(api):
    client, store, _ = api
    sign_in(store)

    response = await client.get("/api/homeassistant")

    assert "t" not in response.json().values()
    assert "access_token" not in response.text


async def test_the_sign_in_start_answers_with_home_assistants_authorize_page(api):
    client, _, _ = api

    response = await client.post(
        "/api/homeassistant/auth/start",
        json={"url": "http://ha:8123", "origin": "http://bridge:8080"},
    )

    assert response.status_code == 200
    assert response.json()["authorize_url"].startswith("http://ha:8123/auth/authorize?")


async def test_a_step_before_signing_in_is_403_not_401(api):
    client, _, _ = api

    response = await client.get("/api/homeassistant/inventory")

    # Not 401: the web UI reads a 401 as "your session with the bridge ended".
    assert response.status_code == 403


async def test_an_expired_home_assistant_sign_in_is_403_not_401(api):
    client, store, ha = api
    sign_in(store)
    ha.fail = HomeAssistantAuthError("Home Assistant did not accept the sign-in.")

    response = await client.get("/api/homeassistant/inventory")

    assert response.status_code == 403


async def test_the_inventory_is_served(api):
    client, store, _ = api
    sign_in(store)

    response = await client.get("/api/homeassistant/inventory")

    assert response.status_code == 200
    assert response.json() == {
        "devices": [],
        "thread_network": None,
        "zha": False,
        "zha_paused": False,
        "matter": False,
    }


async def test_matter_without_a_matter_client_is_503(api):
    client, store, _ = api
    sign_in(store)

    response = await client.post("/api/homeassistant/matter", json={"devices": ["x"]})

    assert response.status_code == 503


async def test_cleanup_with_nothing_chosen_is_refused(api):
    client, store, _ = api
    sign_in(store)

    response = await client.post("/api/homeassistant/cleanup", json={"devices": []})

    assert response.status_code == 422
