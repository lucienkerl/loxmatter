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
from typing import Any

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter import i18n
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.model.zigbee_settings_store import settings_for_path
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
        self.store: Store | None = None
        self.fake_client_removed: Any = None

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
        # Handed back on the fake, so a test can read what a removal left in
        # the store without the fixture's return shape changing for the rest.
        zigbee.store = store
        zigbee.fake_client_removed = lambda: list(fake_client.removed)
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
    # And it says what can still be done, in a form the page can recognise
    # without reading the sentence (see the forget-only tests below).
    assert response.json()["offer"] == "forget_only"


ZIGBEE_ADDRESS = "00:12:4b:00:1c:a1:b2:c3"


def _what_is_left(store: Store, device_id: int, group_id: int) -> dict[str, object]:
    """Everything a removal is meant to take away, read straight from the
    store: the device list, the device's group membership rows, the group's
    recomputed commands, the device's unfinished Zigbee configuration, and
    whether its address still resolves for incoming values."""
    memberships = store._db.execute(
        "SELECT COUNT(*) FROM device_group_member WHERE device_id = ?", (device_id,)
    ).fetchone()[0]
    return {
        "listed": device_id in [device.id for device in store.devices()],
        "memberships": memberships,
        "group_commands": [command.slug for command in store.group_commands(group_id)],
        "pending": store.zigbee_pending.pending_for(ZIGBEE_ADDRESS),
        "resolves": store.device_id_for("zigbee", ZIGBEE_ADDRESS),
    }


async def test_a_zigbee_device_without_its_radio_can_be_forgotten_like_a_removal(zigbee_plug):
    """No Zigbee stick any more - the user tried Zigbee and gave it up - and
    every Zigbee tile answered 503 to its removal, forever.

    `?forget_only=true`, the second step the 503 offers, forgets the device
    without any radio, and leaves exactly what a normal removal through the
    source leaves: measured side by side on two installations holding the
    same plug in a group, with a pending configuration row. The value route
    then answers for the plug's key exactly as it does after a removal.

    Fault to prove it: in the forget-only branch, deactivate the device row
    alone instead of calling `store.forget_device` (the membership, the
    group's commands and the pending row stay)."""
    left = {}
    for with_zigbee in (True, False):
        client, device_id, on_key, zigbee = await zigbee_plug(with_zigbee=with_zigbee)
        store = zigbee.store
        group = store.create_group("Plugs", [device_id])
        store.zigbee_pending.mark_pending(ZIGBEE_ADDRESS, 1, 6)
        before = _what_is_left(store, device_id, group.id)
        query = "" if with_zigbee else "?forget_only=true"

        response = await client.delete(f"/api/devices/{device_id}{query}")

        assert response.status_code == 204, response.text
        command = await client.post(f"/api/commands/{on_key}", json={"value": "1"})
        left[with_zigbee] = {
            "state": _what_is_left(store, device_id, group.id),
            "command_status": command.status_code,
        }
        assert zigbee.removed == ([ZIGBEE_ADDRESS] if with_zigbee else [])
    assert before["listed"] is True and before["memberships"] == 1 and before["pending"]
    assert left[False] == left[True]
    assert left[False]["state"] == {
        "listed": False,
        "memberships": 0,
        "group_commands": [],
        "pending": [],
        "resolves": None,
    }


async def test_forgetting_is_refused_while_the_devices_source_is_configured(zigbee_plug, tmp_path):
    """Forget-only exists for a technology with no source. With one, the
    source is how a device is removed - and for Matter, whose matter-server
    is always configured, forgetting a device the fabric still holds is the
    silent leftover `api/devices.py`'s removal order exists to prevent.

    Both technologies answer 409, nothing is removed anywhere, and the
    device stays listed.

    Fault to prove it: accept `forget_only` whether or not the source is
    configured."""
    client, zigbee_device, _, zigbee = await zigbee_plug(with_zigbee=True)
    store = zigbee.store
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    matter_snapshot = NodeSnapshot(
        technology="matter",
        address="77",
        vendor_name=snapshot.vendor_name,
        product_name=snapshot.product_name,
        unique_id="matter-plug-77",
        attributes=snapshot.attributes,
    )
    matter_device = store.register_device(matter_snapshot)

    responses = [
        await client.delete(f"/api/devices/{device_id}?forget_only=true")
        for device_id in (matter_device, zigbee_device)
    ]

    assert [response.status_code for response in responses] == [409, 409]
    assert "Matter" in responses[0].json()["detail"]
    assert zigbee.removed == []
    assert zigbee.fake_client_removed() == []
    listed = [device.id for device in store.devices()]
    assert matter_device in listed and zigbee_device in listed


async def test_forget_only_is_refused_during_a_radio_change(zigbee_plug):
    """A radio change clears the shared `Sources` registry before the old
    source's `disconnect()` returns (`ZigbeeRuntime._release`), which makes
    `sources.get("zigbee")` raise `SourceNotConfiguredError` in exactly the
    same way as "no stick is stored at all" - but the STORED setting still
    names a stick. Offering, let alone accepting, forget-only there would
    let a user permanently forget a device that stays joined to the network
    the swap is about to reopen (verification finding N-1).

    The route must tell the two situations apart by the stored setting, not
    by the registry that is empty in both: refuse `forget_only=true` with
    the ordinary transient 503 the pairing routes already use for a radio
    change in flight, and offer nothing.

    Fault to prove it: drop the `store.zigbee_settings.get().path is not
    None` guard, so the registry being empty is always read as "no stick
    at all"."""
    client, device_id, _, zigbee = await zigbee_plug(with_zigbee=False)
    store = zigbee.store
    store.zigbee_settings.save(settings_for_path("/dev/ttyUSB0", []))

    plain = await client.delete(f"/api/devices/{device_id}")
    refused = await client.delete(f"/api/devices/{device_id}?forget_only=true")

    assert plain.status_code == 503
    assert "offer" not in plain.json()
    assert plain.json()["detail"] == i18n.t("api.zigbee.radio_changing")
    assert refused.status_code == 503
    assert "offer" not in refused.json()
    assert refused.json()["detail"] == i18n.t("api.zigbee.radio_changing")
    assert device_id in [device.id for device in store.devices()]


async def test_forget_only_is_offered_and_accepted_with_no_stick_stored(zigbee_plug):
    """The other half of N-1: with NO stick stored at all - a fresh install,
    or a user who tried Zigbee and gave the stick up - the registry being
    empty means exactly what it says, and forget-only stays offered and
    accepted, unlike during a radio change (see the test above).

    Fault to prove it: same guard as above, inverted - treat a stored path
    of `None` as "a radio change in progress" and refuse forget-only even
    with nothing configured."""
    client, device_id, _, zigbee = await zigbee_plug(with_zigbee=False)
    store = zigbee.store
    assert store.zigbee_settings.get().path is None

    offered = await client.delete(f"/api/devices/{device_id}")

    assert offered.status_code == 503
    assert offered.json()["offer"] == "forget_only"

    accepted = await client.delete(f"/api/devices/{device_id}?forget_only=true")

    assert accepted.status_code == 204
    assert device_id not in [device.id for device in store.devices()]


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
