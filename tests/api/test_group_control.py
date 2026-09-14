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

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

import httpx2 as httpx
import pytest
from conftest import SlowDevices, authenticate, load_snapshot, settle_until

from loxmatter import i18n
from loxmatter.export.commands import DeviceCommand, extract_commands
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
    hand-built snapshot - it has to carry a light's device types and real
    light commands, or the group's category check refuses it and it has no
    `on` row for the group command to reach.
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


async def test_one_colour_value_gives_colour_to_the_colour_lamp_and_brightness_to_the_white_one(
    api, invocations
):
    """Design 2026-09-13, 3.2, end to end through `/cmd`: blue at 60 % turns
    the CWS lamp blue at 60 % and sets only the brightness of the WS lamp.

    Fault to prove it: route light commands through `to_device_calls` again
    in `plan_group_calls` - every light row `group_targets` hands over is
    then translated on its own, so both lamps receive a call for every light
    command they carry - off, on and toggle first."""
    client, store, group_id = api
    cws, ws = store.group_members(group_id)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "color")
    response = await client.get(f"/cmd/{key}/60000000")
    assert response.status_code == 200
    by_address: dict[str, list[tuple[int, int]]] = {}
    for call in invocations:
        by_address.setdefault(call.address, []).append((call.cluster_id, call.command_id))
    assert by_address[cws.address] == [(768, 6), (8, 4)]
    assert by_address[ws.address] == [(8, 4)]


async def test_a_lumitech_white_gives_both_lamps_their_white_temperature(api, invocations):
    client, store, group_id = api
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "color")
    response = await client.get(f"/cmd/{key}/200302700")
    assert response.status_code == 200
    per_address: dict[str, list[tuple[int, int]]] = {}
    for call in invocations:
        per_address.setdefault(call.address, []).append((call.cluster_id, call.command_id))
    assert all(calls == [(768, 10), (8, 4)] for calls in per_address.values())
    assert len(per_address) == 2


@pytest.fixture
async def dim_only_api(
    tmp_path, invocations, failing_nodes, unconfigured_nodes, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    """The CWS lamp plus a dim-only lamp, so a `colortemp` value gives the
    second member an empty plan (design 2026-09-13, 3.2).

    The dim-only lamp is the WS fixture with its `colortemp` row held back:
    what is left is (6, 0), (6, 1), (6, 2), (8, 0), (8, 4) on endpoint 1, the
    pair set the spec records as captured from the TRADFRI bulb E27 WW, for
    which no fixture exists."""
    store = Store(tmp_path / "t.sqlite")
    member_ids = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = load_snapshot(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        commands = extract_commands(snapshot)
        if name == "ikea_kajplats_ws_lamp.json":
            commands = [c for c in commands if c.slug != "colortemp"]
        store.register_commands(device_id, commands)
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


async def _send(client: httpx.AsyncClient, route: str, key: str, value: str) -> httpx.Response:
    if route == "loxone":
        return await client.get(f"/cmd/{key}/{value}")
    return await client.post(f"/api/commands/{key}", json={"value": value})


@pytest.mark.parametrize("route", ["loxone", "webui"])
async def test_a_member_given_nothing_is_a_plain_success(dim_only_api, invocations, route):
    """`colortemp` reaches the CWS lamp and gives the dim-only lamp nothing.
    That member neither failed nor was left unconfigured, so the response
    is 200 - the adapter's empty plan must not surface as an error."""
    client, store, group_id = dim_only_api
    cws, dim_only = store.group_members(group_id)
    assert (768, 10) not in {(c.cluster_id, c.command_id) for c in store.commands(dim_only.id)}
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "colortemp")

    response = await _send(client, route, key, "2700")

    assert response.status_code == 200
    assert [call.address for call in invocations] == [cws.address]


@pytest.mark.parametrize("route", ["loxone", "webui"])
async def test_a_group_whose_only_asked_member_has_no_source_is_a_503(
    dim_only_api, invocations, unconfigured_nodes, route
):
    """The CWS lamp has no source, and the dim-only lamp was given nothing
    to do. Nothing was sent to anyone, and the one member that would have
    received something was never asked - that is the 503 "not set up"
    case, counted over the members given something to do. Counting the
    empty plan as well made this a 502 "reached 1 of 2 members; no answer
    from" the CWS lamp: a member reached that was never sent anything, and
    "no answer" from one that was never asked.

    Fault to prove it: compare `len(outcome.unconfigured)` with `len(plans)`
    again in `loxone/server.py` and in `api/control.py`."""
    client, store, group_id = dim_only_api
    cws, _dim_only = store.group_members(group_id)
    unconfigured_nodes.add(cws.address)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "colortemp")

    response = await _send(client, route, key, "2700")

    assert response.status_code == 503
    assert response.json()["detail"] == i18n.t(
        "api.errors.group_source_not_configured_one", technology="Matter"
    )
    assert invocations == []


@pytest.mark.parametrize("route", ["loxone", "webui"])
async def test_the_502_counts_only_the_members_given_something_to_do(
    dim_only_api, failing_nodes, route
):
    """The CWS lamp does not answer, and the dim-only lamp was given
    nothing. "reached 1 of 2 members" would count the dim-only lamp as
    reached although nothing was sent to it; the honest count is 0 of 1.

    Fault to prove it: count `len(plans)` again for `total` and `reached`
    in `loxone/server.py` and in `api/control.py`."""
    client, store, group_id = dim_only_api
    cws, _dim_only = store.group_members(group_id)
    failing_nodes.add(cws.address)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "colortemp")

    response = await _send(client, route, key, "2700")

    assert response.status_code == 502
    assert response.json()["detail"] == i18n.t(
        "api.errors.group_partially_unreachable", reached=0, total=1, devices=cws.label
    )


@pytest.mark.parametrize("route", ["loxone", "webui"])
async def test_the_502_counts_members_not_calls(api, invocations, failing_nodes, route):
    """Blue at 60 % gives the CWS lamp two calls (colour, brightness) and the
    WS lamp one. The CWS lamp does not answer: one of two MEMBERS was
    reached, whatever the number of calls.

    Fault to prove it: count `asked` as the sum of the plans' calls in
    `loxone/server.py` or `api/control.py` - the detail then reads "reached
    2 of 3". In every earlier 502 test each member asked got exactly one
    call, and there the two counts agree."""
    client, store, group_id = api
    cws, ws = store.group_members(group_id)
    failing_nodes.add(cws.address)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "color")

    response = await _send(client, route, key, "60000000")

    assert response.status_code == 502
    assert response.json()["detail"] == i18n.t(
        "api.errors.group_partially_unreachable", reached=1, total=2, devices=cws.label
    )
    assert [(call.address, call.cluster_id, call.command_id) for call in invocations] == [
        (ws.address, 8, 4)
    ]


@pytest.mark.parametrize("route", ["loxone", "webui"])
async def test_a_stale_group_command_row_that_gives_every_member_nothing_is_a_plain_success(
    dim_only_api, invocations, route
):
    """A group of the dim-only lamp alone never offers `colortemp`: no
    member carries it. The row is inserted directly, as a stale row would
    stand after a member's commands changed underneath it. Every member's
    plan is then empty - nobody failed, nobody was unconfigured - and the
    answer is 200 with nothing sent.

    Fault to prove it: drop the `outcome.unconfigured and` guard from the
    503 condition in `loxone/server.py` or `api/control.py` - zero
    unconfigured then equals zero asked, the 503 branch reads the first
    technology of an empty list, and the request dies with an `IndexError`."""
    client, store, group_id = dim_only_api
    _cws, dim_only = store.group_members(group_id)
    group = store.create_group("Dim only", [dim_only.id])
    assert "colortemp" not in {c.slug for c in store.group_commands(group.id)}
    key = f"g{group.id}_colortemp"
    store._db.execute(
        "INSERT INTO group_command (group_id, cluster_id, command_id, key, slug, takes_value)"
        " VALUES (?, 768, 10, ?, 'colortemp', 1)",
        (group.id, key),
    )
    store._db.commit()

    response = await _send(client, route, key, "2700")

    assert response.status_code == 200
    assert invocations == []


@pytest.mark.parametrize("route", ["loxone", "webui"])
async def test_a_non_light_group_command_is_still_translated_per_row(api, invocations, route):
    """Identify (3, 0), carried by both lamps, is offered by the group -
    the intersection rule for pairs outside `LIGHT_COMMAND_PAIRS`. It has no
    payload builder, so `to_device_calls` answers 400 "not supported" and
    nothing is sent; it must not become a quiet 200.

    Faults to prove it: in `plan_group_calls`, drop the non-light branch -
    no calls, 200. Or treat every pair as a light command in both
    `plan_group_calls` and `Store.group_targets` - the members' light rows
    carry no (3, 0), the adapter builds nothing, 200. (Routing only
    `plan_group_calls` through the adapter is not caught, and cannot be:
    for a non-light pair the rows `group_targets` hands over all carry that
    pair, and the adapter passes such a row to `to_device_calls` itself.)"""
    client, store, group_id = api
    for member in store.group_members(group_id):
        store.register_commands(
            member.id,
            [
                DeviceCommand(
                    endpoint=1, cluster_id=3, command_id=0, slug="identify", takes_value=True
                )
            ],
        )
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "identify")

    response = await _send(client, route, key, "5")

    assert response.status_code == 400
    assert response.json()["detail"] == i18n.t(
        "api.errors.command_unsupported", cluster_id=3, command_id=0
    )
    assert invocations == []


@pytest.mark.parametrize("route", ["loxone", "webui"])
async def test_a_lumitech_white_dims_the_dim_only_member_and_whitens_the_colour_lamp(
    dim_only_api, invocations, route
):
    """2700 K at 30 % on `color`: the CWS lamp gets its white temperature and
    then brightness, the dim-only lamp only the brightness - level 76, the
    same level as the CWS lamp's.

    Fault to prove it: give a member without any colour command nothing
    for a white in `commands/adapt.py` - the dim-only lamp stays at its old
    brightness."""
    client, store, group_id = dim_only_api
    cws, dim_only = store.group_members(group_id)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "color")

    response = await _send(client, route, key, "200302700")

    assert response.status_code == 200
    per_address: dict[str, list[tuple[int, int, dict[str, object]]]] = {}
    for call in invocations:
        per_address.setdefault(call.address, []).append(
            (call.cluster_id, call.command_id, dict(call.payload))
        )
    assert [(cluster, command) for cluster, command, _ in per_address[cws.address]] == [
        (768, 10),
        (8, 4),
    ]
    assert per_address[cws.address][1][2]["level"] == 76
    assert per_address[dim_only.address] == [(8, 4, {"level": 76, "transitionTime": 0})]


@pytest.fixture
async def three_lamp_api(
    tmp_path, invocations, failing_nodes, unconfigured_nodes, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    """The maintainer's real "Lampengruppe 1" (Minor M9): two Matter KAJPLATS
    CWS colour lamps plus the TRADFRI WW dim-only lamp. This is the group the
    final whole-branch review traced by hand through a throwaway probe; no
    route test in this suite used all three shapes together until now.

    Both CWS lamps come from the same checked-in fixture. `register_device`
    folds a second registration into the first when `_device_identity`
    matches - which it does on `unique_id`, a fixed attribute the raw
    fixture carries - so the second lamp is re-stamped with its own address
    (Matter's address is the node id as text) and its own unique id, the
    same technique `mixed_technology_api` above uses to re-stamp a
    technology and address.

    The WW lamp is the WS fixture with its `colortemp` row held back, the
    same dim-only construction `dim_only_api` uses: what is left is
    (6, 0), (6, 1), (6, 2), (8, 0), (8, 4) on endpoint 1, the TRADFRI bulb
    E27 WW's captured shape."""
    store = Store(tmp_path / "t.sqlite")
    member_ids: list[int] = []

    first_cws = load_snapshot("ikea_kajplats_cws_lamp.json")
    device_id = store.register_device(first_cws)
    store.register_signals(device_id, first_cws)
    store.register_commands(device_id, extract_commands(first_cws))
    member_ids.append(device_id)

    second_cws = replace(first_cws, address="22", unique_id=f"{first_cws.unique_id}-2")
    device_id = store.register_device(second_cws)
    store.register_signals(device_id, second_cws)
    store.register_commands(device_id, extract_commands(second_cws))
    member_ids.append(device_id)

    ws_snapshot = load_snapshot("ikea_kajplats_ws_lamp.json")
    device_id = store.register_device(ws_snapshot)
    store.register_signals(device_id, ws_snapshot)
    dim_only_commands = [c for c in extract_commands(ws_snapshot) if c.slug != "colortemp"]
    store.register_commands(device_id, dim_only_commands)
    member_ids.append(device_id)

    group = store.create_group("Lampengruppe 1", member_ids)

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


@pytest.mark.parametrize("route", ["loxone", "webui"])
async def test_the_three_lamp_group_gives_each_cws_lamp_colour_and_the_ww_lamp_only_brightness(
    three_lamp_api, invocations, route
):
    """Minor M9, end to end on the maintainer's own three-lamp group: blue at
    60 % gives each CWS lamp its named colour then brightness, and gives the
    dim-only WW lamp only the brightness - both at level 152 (60 %). This
    mirrors the final review's traced table exactly (final-review.md, the
    maintainer's case).

    Fault to prove it: give a member without any colour command nothing at
    all for a colour value in `commands/adapt.py` - the WW lamp then gets no
    brightness call either, so its assertion below fails."""
    client, store, group_id = three_lamp_api
    cws_one, cws_two, ww = store.group_members(group_id)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "color")

    response = await _send(client, route, key, "60000000")

    assert response.status_code == 200
    per_address: dict[str, list[tuple[int, int, dict[str, object]]]] = {}
    for call in invocations:
        per_address.setdefault(call.address, []).append(
            (call.cluster_id, call.command_id, dict(call.payload))
        )
    for cws in (cws_one, cws_two):
        assert [(cluster, command) for cluster, command, _ in per_address[cws.address]] == [
            (768, 6),
            (8, 4),
        ]
        assert per_address[cws.address][1][2]["level"] == 152
    assert per_address[ww.address] == [(8, 4, {"level": 152, "transitionTime": 0})]


@pytest.mark.parametrize("route", ["loxone", "webui"])
async def test_the_three_lamp_group_gives_each_cws_lamp_white_and_the_ww_lamp_only_brightness(
    three_lamp_api, invocations, route
):
    """The same group with a Lumitech white (2700 K at 30 %): each CWS lamp
    takes its white temperature, not a colour point - it carries (768, 10) -
    then brightness, and the WW lamp again gets only the brightness, both at
    level 76 (30 %).

    Fault to prove it: same as above - a colour-less member given nothing at
    all for a colour value loses its brightness call too."""
    client, store, group_id = three_lamp_api
    cws_one, cws_two, ww = store.group_members(group_id)
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "color")

    response = await _send(client, route, key, "200302700")

    assert response.status_code == 200
    per_address: dict[str, list[tuple[int, int, dict[str, object]]]] = {}
    for call in invocations:
        per_address.setdefault(call.address, []).append(
            (call.cluster_id, call.command_id, dict(call.payload))
        )
    for cws in (cws_one, cws_two):
        assert [(cluster, command) for cluster, command, _ in per_address[cws.address]] == [
            (768, 10),
            (8, 4),
        ]
        assert per_address[cws.address][1][2]["level"] == 76
    assert per_address[ww.address] == [(8, 4, {"level": 76, "transitionTime": 0})]


@pytest.fixture
async def slow_api(
    tmp_path, fake_runtime, fake_client, monkeypatch
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int, SlowDevices, list[int]]]:
    """The two-lamp group of `api`, behind lamps that answer only when the
    test lets them (design 2026-09-13, command coalescing).

    `admitted` counts the group requests that reached `store.group_targets`.
    From that line to the fan-out nothing is awaited, so a request counted
    there has left the transport and the middlewares and is on its way to
    the lamps - the test waits for that instead of for a length of time."""
    store = Store(tmp_path / "t.sqlite")
    member_ids = []
    for name in ("ikea_kajplats_cws_lamp.json", "ikea_kajplats_ws_lamp.json"):
        snapshot = load_snapshot(name)
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot))
        member_ids.append(device_id)
    group = store.create_group("Living room", member_ids)

    admitted = [0]
    group_targets = store.group_targets

    def counting_group_targets(command):
        admitted[0] += 1
        return group_targets(command)

    monkeypatch.setattr(store, "group_targets", counting_group_targets)

    lamps = SlowDevices()
    app = build_app(store, lamps, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, group.id, lamps, admitted
    store.close()


@pytest.mark.parametrize("route", ["loxone", "webui"])
async def test_a_dragged_slider_reaches_each_lamp_one_value_at_a_time(slow_api, route):
    """Five brightness values for a group, each sent while the lamps are
    still busy with the first - the burst a dragged Loxone slider produces,
    which on 13 September 2026 overlapped on the same Thread lamps until the
    OpenThread agent gave up.

    Each request has reached the route before the next is sent, and the
    first is still waiting for the lamps when the fifth arrives - asserted
    below, because that is what makes this a burst: `httpx.ASGITransport`
    holds no lock, so the requests really run side by side in this event
    loop. Each lamp then runs one call at a time, ends on the newest value,
    and skips at least one of the values that were only waiting.

    Fault to prove it: call `dispatch_group(plans, invoke)` without `run`
    in `_group_command` (loxone/server.py) or `_execute_group_command`
    (api/control.py) - the lamps then run five calls at once and receive
    all five values."""
    client, store, group_id, lamps, admitted = slow_api
    addresses = {member.address for member in store.group_members(group_id)}
    key = next(c.key for c in store.group_commands(group_id) if c.slug == "level_onoff")

    requests: list[asyncio.Future[httpx.Response]] = []
    for value in ("10", "20", "30", "40", "50"):
        requests.append(asyncio.ensure_future(_send(client, route, key, value)))
        await settle_until(
            lambda: admitted[0] == len(requests), f"request {len(requests)} reached the route"
        )

    # The ones in between may already have answered: a superseded request
    # answers as soon as a newer value replaces it.
    assert not requests[0].done()
    assert {address: lamps.active.get(address, 0) > 0 for address in addresses} == {
        address: True for address in addresses
    }

    lamps.release.set()
    responses = await asyncio.gather(*requests)

    assert [response.status_code for response in responses] == [200] * 5
    assert {address: lamps.max_active[address] for address in addresses} == {
        address: 1 for address in addresses
    }
    for address in addresses:
        levels = [
            call.payload["level"]
            for call in lamps.ran
            if call.address == address and call.cluster_id == 8
        ]
        assert levels[-1] == 127  # 50 %
        assert len(levels) < 5
