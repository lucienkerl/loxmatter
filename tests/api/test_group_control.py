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
from dataclasses import replace

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter import i18n
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store
from loxmatter.sources import DeviceCall, SourceNotConfiguredError


@pytest.fixture
def invocations() -> list[DeviceCall]:
    return []


@pytest.fixture
def failing_nodes() -> set[str]:
    """Addresses whose invocation raises - the 502 path."""
    return set()


@pytest.fixture
def unconfigured_nodes() -> set[str]:
    """Addresses whose invocation raises `SourceNotConfiguredError` - the
    503 path (boundary design open point 12): the member was never asked,
    unlike `failing_nodes`, which was asked and did not answer."""
    return set()


@pytest.fixture
async def api(
    tmp_path, invocations, failing_nodes, unconfigured_nodes, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    store = Store(tmp_path / "t.sqlite")
    member_ids = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = load_snapshot(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot))
        member_ids.append(device_id)
    group = store.create_group("Living room", member_ids)

    async def invoke(call: DeviceCall) -> None:
        if call.address in unconfigured_nodes:
            raise SourceNotConfiguredError(call.technology)
        if call.address in failing_nodes:
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
    addresses = {device.address for device in store.group_members(group_id)}
    assert {call.address for call in invocations} == addresses


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
    failing_nodes.add(members[0].address)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")
    response = await client.get(f"/cmd/{key}/1")
    assert response.status_code == 502
    assert members[0].label in response.json()["detail"]
    # the reachable member was still switched
    assert {call.address for call in invocations} == {members[1].address}


async def test_a_device_key_still_works_unchanged(api, invocations):
    """The group path is a fallback, not a replacement (design 3)."""
    client, store, group_id = api
    device = store.group_members(group_id)[0]
    key = next(c.key for c in store.commands(device.id) if c.slug == "on")
    assert (await client.get(f"/cmd/{key}/1")).status_code == 200
    assert [call.address for call in invocations] == [device.address]


async def test_the_webui_route_drives_a_group_too(api, invocations):
    """No second control endpoint - the shared key namespace means a group
    tile makes the same call a device tile makes (design 5)."""
    client, store, group_id = api
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")
    response = await client.post(f"/api/commands/{key}", json={"value": "1"})
    assert response.status_code == 200
    addresses = {device.address for device in store.group_members(group_id)}
    assert {call.address for call in invocations} == addresses


async def test_the_webui_route_and_the_loxone_route_translate_identically(api, invocations):
    """Spec 4.2: one translation, two callers, or they drift."""
    client, store, group_id = api
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "level")
    await client.get(f"/cmd/{key}/50")
    from_loxone = list(invocations)
    invocations.clear()
    await client.post(f"/api/commands/{key}", json={"value": "50"})
    assert sorted(from_loxone, key=lambda c: c.address) == sorted(
        invocations, key=lambda c: c.address
    )


async def test_an_unknown_key_is_a_404_on_the_webui_route(api):
    client, _store, _group_id = api
    response = await client.post("/api/commands/g99_on", json={"value": "1"})
    assert response.status_code == 404


async def test_a_failing_member_is_a_502_on_the_webui_route(api, failing_nodes):
    client, store, group_id = api
    members = store.group_members(group_id)
    failing_nodes.add(members[0].address)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")
    response = await client.post(f"/api/commands/{key}", json={"value": "1"})
    assert response.status_code == 502
    assert members[0].label in response.json()["detail"]


async def test_an_all_unconfigured_group_is_a_503_naming_the_technology_and_the_count(
    api, unconfigured_nodes
):
    """Boundary design open point 12: every member was never ASKED - its
    technology has no running source right now - so this must not read
    like "did not answer" (502).

    The detail is asserted, not just the status: a 503 whose text is built
    from an empty technology and a zero count is the same 503 to a status
    assertion, and that is exactly what the review found (`technology=""`
    and `total=0` both survived the whole suite).

    Faults to prove it: pass `technology=""` to `i18n.t` in the 503 branch
    of `loxone/server.py`, and separately pass `total=0`."""
    client, store, group_id = api
    members = store.group_members(group_id)
    unconfigured_nodes.update(m.address for m in members)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")
    response = await client.get(f"/cmd/{key}/1")
    assert response.status_code == 503
    assert response.json()["detail"] == i18n.t(
        "api.errors.group_source_not_configured_many",
        technology="Matter",
        total=len(members),
    )


async def test_an_all_unconfigured_group_is_a_503_on_the_webui_route(api, unconfigured_nodes):
    """The same assertion on the other route - the two must not drift.

    Faults to prove it: `technology=""` and `total=0` in the 503 branch of
    `api/control.py`."""
    client, store, group_id = api
    members = store.group_members(group_id)
    unconfigured_nodes.update(m.address for m in members)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")
    response = await client.post(f"/api/commands/{key}", json={"value": "1"})
    assert response.status_code == 503
    assert response.json()["detail"] == i18n.t(
        "api.errors.group_source_not_configured_many",
        technology="Matter",
        total=len(members),
    )


async def test_a_single_member_group_reads_in_the_singular(api, unconfigured_nodes):
    """A group of one used to report "1 members were not reached", and a
    group of one is the common case. `i18n.t` has no plural rule, so the
    branch picks between two keys.

    Fault to prove it: always use `api.errors.group_source_not_configured_many`."""
    client, store, _group_id = api
    lonely = store.devices()[0]
    group = store.create_group("Hallway", [lonely.id])
    unconfigured_nodes.add(lonely.address)
    key = next(c.key for c in store.group_commands(group.id) if c.slug == "on")
    response = await client.get(f"/cmd/{key}/1")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail == i18n.t("api.errors.group_source_not_configured_one", technology="Matter")
    assert "1 member was not reached" in detail


async def test_a_single_member_group_reads_in_the_singular_on_the_webui_route(
    api, unconfigured_nodes
):
    """The web-UI twin of the test above - `api/control.py` picks between
    the same two keys with its own copy of the branch, and until this test
    existed nothing measured that copy: replacing its `key_for_total` with
    the plural key unconditionally left the whole file green.

    Fault to prove it: in `api/control.py`, always use
    `api.errors.group_source_not_configured_many`."""
    client, store, _group_id = api
    lonely = store.devices()[0]
    group = store.create_group("Hallway", [lonely.id])
    unconfigured_nodes.add(lonely.address)
    key = next(c.key for c in store.group_commands(group.id) if c.slug == "on")
    response = await client.post(f"/api/commands/{key}", json={"value": "1"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail == i18n.t("api.errors.group_source_not_configured_one", technology="Matter")
    assert "1 member was not reached" in detail


@pytest.fixture
async def mixed_technology_api(
    tmp_path, invocations, unconfigured_nodes, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    """A group whose FIRST member is Zigbee and whose second is Matter.

    Separate from `api` because plan order is `group_members`' `ORDER BY
    d.id`, so which technology comes first is decided by registration
    order, and the point of these two tests is which end of
    `GroupOutcome.unconfigured_technologies` the 503 reads. The Zigbee
    member is first deliberately: with Matter first, a detail naming
    "Matter" would also be produced by a route that ignored the field
    entirely, since "matter" is the column default everywhere else in the
    suite.

    The Zigbee member is the Matter fixture re-stamped rather than a
    hand-built snapshot - it has to carry the same device types and the
    same commands as the other member, or the group's category check
    refuses it and its command never lands in the intersection.
    """
    store = Store(tmp_path / "t.sqlite")
    member_ids = []
    for name, technology, address in (
        ("ikea_kajplats_cws_lamp.json", "zigbee", "00:12:4b:00:1c:a1:b2:c3"),
        ("ikea_kajplats_ws_lamp.json", "matter", None),
    ):
        snapshot = load_snapshot(name)
        if address is not None:
            snapshot = replace(snapshot, technology=technology, address=address)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot))
        member_ids.append(device_id)
    group = store.create_group("Living room", member_ids)

    async def invoke(call: DeviceCall) -> None:
        if call.address in unconfigured_nodes:
            raise SourceNotConfiguredError(call.technology)
        invocations.append(call)

    app = build_app(store, invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, group.id
    store.close()


@pytest.mark.parametrize("route", ["loxone", "webui"])
async def test_the_503_names_the_first_members_technology_not_the_last(
    mixed_technology_api, unconfigured_nodes, route
):
    """`GroupOutcome.unconfigured_technologies` is a LIST in plan order, and
    its whole justification is that a caller naming one technology names the
    FIRST member's - not whichever coroutine happened to raise first. Both
    routes read `[0]`, and that index was asserted only inside
    `dispatch_group`; at the routes, `[0]` -> `[-1]` left the suite green,
    because every group in it had exactly one technology.

    A Zigbee lamp and a Matter lamp in one group, both without a source:
    the detail must say Zigbee. It must also NOT say Matter - a route
    reading `[-1]` names the Matter member, which is the wrong half of a
    two-radio outage and sends the owner to restart the wrong service.

    Fault to prove it: read `unconfigured_technologies[-1]` in
    `api/control.py` and in `loxone/server.py`."""
    client, store, group_id = mixed_technology_api
    members = store.group_members(group_id)
    assert [m.technology for m in members] == ["zigbee", "matter"]
    unconfigured_nodes.update(m.address for m in members)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")

    if route == "loxone":
        response = await client.get(f"/cmd/{key}/1")
    else:
        response = await client.post(f"/api/commands/{key}", json={"value": "1"})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail == i18n.t(
        "api.errors.group_source_not_configured_many", technology="Zigbee", total=len(members)
    )
    assert "Matter" not in detail


async def test_a_group_half_of_which_switched_is_not_a_flat_503(
    api, invocations, unconfigured_nodes
):
    """One member switched, the other has no source. Answering 503 "Matter
    is not set up, so 1 members were not reached" would name no member,
    give no reached count, and tell the user nothing happened - while half
    the group had just switched. Any partial success is a 502 with the
    member names and the reached count.

    Fault to prove it: gate the 503 on `outcome.unconfigured and not
    outcome.unreachable` again instead of on every member being
    unconfigured."""
    client, store, group_id = api
    members = store.group_members(group_id)
    unconfigured_nodes.add(members[1].address)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")

    response = await client.get(f"/cmd/{key}/1")

    assert response.status_code == 502
    assert response.json()["detail"] == i18n.t(
        "api.errors.group_partially_unreachable",
        reached=1,
        total=2,
        devices=members[1].label,
    )
    # and the half that worked really did switch
    assert [call.address for call in invocations] == [members[0].address]


async def test_a_group_half_of_which_switched_is_not_a_flat_503_on_the_webui_route(
    api, invocations, unconfigured_nodes
):
    """The same partial success on the other route - see above."""
    client, store, group_id = api
    members = store.group_members(group_id)
    unconfigured_nodes.add(members[1].address)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")

    response = await client.post(f"/api/commands/{key}", json={"value": "1"})

    assert response.status_code == 502
    assert response.json()["detail"] == i18n.t(
        "api.errors.group_partially_unreachable",
        reached=1,
        total=2,
        devices=members[1].label,
    )
    assert [call.address for call in invocations] == [members[0].address]


async def test_a_mix_of_unreachable_and_unconfigured_members_stays_a_502(
    api, failing_nodes, unconfigured_nodes
):
    """As soon as one member was actually asked and did not answer, this is
    not the all-unconfigured 503 case any more - `GroupOutcome.unreachable`
    is non-empty, and the existing 502 path names every failed member."""
    client, store, group_id = api
    members = store.group_members(group_id)
    failing_nodes.add(members[0].address)
    unconfigured_nodes.add(members[1].address)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "on")
    response = await client.get(f"/cmd/{key}/1")
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert members[0].label in detail
    assert members[1].label in detail
