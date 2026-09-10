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

"""A group behind `/cmd` and `POST /api/commands` - design 2026-09-10, section 3."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.commands.translate import MatterCall
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


@pytest.fixture
def invocations() -> list[MatterCall]:
    return []


@pytest.fixture
def failing_nodes() -> set[int]:
    """Node IDs whose invocation raises - the 502 path."""
    return set()


@pytest.fixture
async def api(
    tmp_path, invocations, failing_nodes, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    store = Store(tmp_path / "t.sqlite")
    member_ids = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = load_snapshot(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
        member_ids.append(device_id)
    group = store.create_group("Living room", member_ids)

    async def invoke(call: MatterCall) -> None:
        if call.node_id in failing_nodes:
            raise RuntimeError("no route to host")
        invocations.append(call)

    app = build_app(store, invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, group.id
    store.close()


async def test_one_loxone_call_reaches_every_member(api, invocations):
    client, store, group_id = api
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")
    response = await client.get(f"/cmd/{key}/1")
    assert response.status_code == 200
    node_ids = {device.node_id for device in store.group_members(group_id)}
    assert {call.node_id for call in invocations} == node_ids


async def test_an_unknown_group_key_is_a_404(api):
    client, _store, _group_id = api
    assert (await client.get("/cmd/g99_on/1")).status_code == 404


async def test_a_bad_value_is_a_400_and_sends_nothing(api, invocations):
    client, store, group_id = api
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "level")
    assert (await client.get(f"/cmd/{key}/banana")).status_code == 400
    assert invocations == []


async def test_a_failing_member_yields_502_and_names_it(api, invocations, failing_nodes):
    client, store, group_id = api
    members = store.group_members(group_id)
    failing_nodes.add(members[0].node_id)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")
    response = await client.get(f"/cmd/{key}/1")
    assert response.status_code == 502
    assert members[0].label in response.json()["detail"]
    # the reachable member was still switched
    assert {call.node_id for call in invocations} == {members[1].node_id}


async def test_a_device_key_still_works_unchanged(api, invocations):
    """The group path is a fallback, not a replacement (design 3)."""
    client, store, group_id = api
    device = store.group_members(group_id)[0]
    key = next(c.key for c in store.commands(device.id) if c.slug == "on")
    assert (await client.get(f"/cmd/{key}/1")).status_code == 200
    assert [call.node_id for call in invocations] == [device.node_id]
