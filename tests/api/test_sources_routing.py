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

"""Removal and commands reach a device through the source of its
technology, and a missing source answers 503 (design 2026-09-11,
sections 6.2 and 6.3)."""

from __future__ import annotations

import asyncio

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter import i18n
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store
from loxmatter.sources import DeviceCall, DeviceUnreachableError, Sources


class _FakeZigbeeSource:
    technology = "zigbee"
    connected = True

    def __init__(self) -> None:
        self.removed: list[str] = []
        self.sent: list[DeviceCall] = []
        # A source that does not answer removal (boundary design open
        # point 11) - unlike `MatterUnavailableError`, this comes from a
        # NON-Matter source, so `remove_device` must widen past the single
        # type it has always caught here to still answer 502 rather than
        # an unhandled 500.
        self.fail_remove_with: BaseException | None = None
        # A source that never answers removal at all - what a zigpy
        # `remove()` on a sleeping end device looks like from here. Set,
        # `remove` waits forever, and only the route's own bound can end
        # the request.
        self.remove_hangs = False
        # Seconds a removal takes before it answers - a device that is slow
        # to be reached, rather than one that never answers.
        self.remove_delay = 0.0

    async def remove(self, address: str) -> None:
        if self.remove_hangs:
            await asyncio.sleep(3600)
        if self.remove_delay:
            await asyncio.sleep(self.remove_delay)
        if self.fail_remove_with is not None:
            raise self.fail_remove_with
        self.removed.append(address)

    async def send(self, call: DeviceCall) -> None:
        self.sent.append(call)


def _as_zigbee(store: Store, device_id: int) -> None:
    store._db.execute(
        "UPDATE device SET technology = 'zigbee', address = '00:12:4b:00:1c:a1:b2:c3' WHERE id = ?",
        (device_id,),
    )
    store._db.commit()


@pytest.fixture
async def zigbee_plug(tmp_path, fake_runtime, fake_client):
    """A plug stored as a Zigbee device, served by an app whose `Sources`
    either includes a Zigbee source or does not."""
    opened: list[tuple[httpx.AsyncClient, Store]] = []

    async def make(*, with_zigbee: bool):
        store = Store(tmp_path / f"s-{with_zigbee}.sqlite")
        snapshot = load_snapshot("ikea_grillplats_plug.json")
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot))
        _as_zigbee(store, device_id)
        zigbee = _FakeZigbeeSource()
        sources = Sources([fake_client, zigbee] if with_zigbee else [fake_client])
        app = build_app(
            store, sources.send, fake_runtime(store), client=fake_client, sources=sources
        )
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        await authenticate(store, client)
        opened.append((client, store))
        on_key = next(c.key for c in store.commands(device_id) if c.slug == "on")
        return client, device_id, on_key, zigbee

    yield make
    for client, store in opened:
        await client.aclose()
        store.close()


async def test_removal_goes_to_the_devices_own_source(zigbee_plug, fake_client):
    """Fault to prove it: in `remove_device`, call
    `_require_client().remove(device.address)` instead of going through
    `sources.get(device.technology)`."""
    client, device_id, _, zigbee = await zigbee_plug(with_zigbee=True)

    response = await client.delete(f"/api/devices/{device_id}")

    assert response.status_code == 204
    assert zigbee.removed == ["00:12:4b:00:1c:a1:b2:c3"]
    assert fake_client.removed == []


async def test_removal_without_the_devices_source_is_503(zigbee_plug):
    """Fault to prove it: map `SourceNotConfiguredError` to 502 in
    `remove_device`."""
    client, device_id, _, _ = await zigbee_plug(with_zigbee=False)

    response = await client.delete(f"/api/devices/{device_id}")

    assert response.status_code == 503
    # "Zigbee", not "zigbee": the detail goes through
    # `technology_display_name` now (boundary design open point 13), not
    # the raw stored value.
    assert "Zigbee" in response.json()["detail"]


async def test_cmd_without_the_devices_source_is_503(zigbee_plug):
    """Fault to prove it: delete the `except SourceNotConfiguredError`
    branch in `/cmd/{key}/{value}` - the generic handler then answers 502."""
    client, _, on_key, _ = await zigbee_plug(with_zigbee=False)

    response = await client.get(f"/cmd/{on_key}/1")

    assert response.status_code == 503


async def test_the_control_route_without_the_devices_source_is_503(zigbee_plug):
    """Fault to prove it: delete the `except SourceNotConfiguredError`
    branch in `POST /api/commands/{key}`."""
    client, _, on_key, _ = await zigbee_plug(with_zigbee=False)

    response = await client.post(f"/api/commands/{on_key}", json={"value": "1"})

    assert response.status_code == 503


async def test_a_zigbee_removal_that_does_not_answer_is_a_502_not_a_500(zigbee_plug):
    """The real defect this task fixes: `remove_device` used to catch only
    `MatterUnavailableError`, so a non-Matter source raising its own "asked,
    no answer" type on removal surfaced as an unhandled 500.

    Fault to prove it: drop `DeviceUnreachableError` from the removal
    route's exception tuple."""
    client, device_id, _, zigbee = await zigbee_plug(with_zigbee=True)
    zigbee.fail_remove_with = DeviceUnreachableError("the device did not answer within 10 s")

    response = await client.delete(f"/api/devices/{device_id}")

    assert response.status_code == 502


async def test_a_removal_that_never_answers_is_cut_off(zigbee_plug, monkeypatch):
    """The promise of a bound on every device call was true of `Sources.send`
    only: this route awaited `source.remove(...)` with nothing above it, so a zigpy
    `remove()` on a sleeping end device would have held the DELETE open
    indefinitely. The removal now goes through `bounded_source_call` too.

    The detail is asserted, not just the status: the bound's expiry becomes
    `DeviceUnreachableError` carrying the i18n message precisely so the 502
    is not blank (a bare `TimeoutError` stringifies to "").

    Fault to prove it: await `source.remove(device.address)` directly again -
    the request then never returns and the outer `wait_for` below fires
    instead, failing the test with `TimeoutError`."""
    monkeypatch.setattr("loxmatter.sources.SOURCE_REMOVAL_TIMEOUT_SECONDS", 0.05)
    client, device_id, _, zigbee = await zigbee_plug(with_zigbee=True)
    zigbee.remove_hangs = True

    response = await asyncio.wait_for(client.delete(f"/api/devices/{device_id}"), timeout=5)

    assert response.status_code == 502
    assert response.json()["detail"] == i18n.t("api.errors.device_timed_out", seconds=0.05)
    assert "0.05 s" in response.json()["detail"]


async def test_a_removal_is_not_held_to_the_bound_of_a_command(zigbee_plug, monkeypatch):
    """Removal was bounded with the 10 s a Loxone command gets, and for a
    Matter device that is a regression: matter-server forgets the node first
    and then asks the device to leave the fabric, which for an offline or
    sleeping Thread device means establishing a session that routinely takes
    longer than that. The bridge answered 502 and kept the device while
    matter-server had already dropped it.

    A removal has its own, longer bound. Here a removal that takes longer
    than the command bound, and far less than its own, succeeds.

    Fault to prove it: bound the removal with `SOURCE_CALL_TIMEOUT_SECONDS`
    again (the answer is 502 and the device stays)."""
    monkeypatch.setattr("loxmatter.sources.SOURCE_CALL_TIMEOUT_SECONDS", 0.05)
    client, device_id, _, zigbee = await zigbee_plug(with_zigbee=True)
    zigbee.remove_delay = 0.3

    response = await asyncio.wait_for(client.delete(f"/api/devices/{device_id}"), timeout=5)

    assert response.status_code == 204
    assert zigbee.removed == ["00:12:4b:00:1c:a1:b2:c3"]


async def test_cmd_reaches_the_zigbee_source(zigbee_plug):
    client, _, on_key, zigbee = await zigbee_plug(with_zigbee=True)

    response = await client.get(f"/cmd/{on_key}/1")

    assert response.status_code == 200
    assert [call.technology for call in zigbee.sent] == ["zigbee"]
