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

"""Tests for the control API (Task 4, Phase 5) - see api/control.py."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.commands.translate import MatterCall
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store
from loxmatter.profiles.table import command_slug


@pytest.fixture
def invocations() -> list[MatterCall]:
    """Collects every `MatterCall` accepted by the ONE invoker that `api`
    passes through to both the Loxone endpoint (`/cmd`) and the WebUI route
    (`/api/commands`) - the basis for
    `test_the_same_translation_as_the_loxone_endpoint` (Spec 4.2)."""
    return []


@pytest.fixture
async def api(
    tmp_path, invocations, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    """Like the `api` fixture in `test_devices.py`, but with a RECORDING
    invoker instead of `no_invoke`: `test_the_same_translation_as_the_loxone_
    endpoint` below needs the actually translated `MatterCall`s, not just
    that some invoker exists."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    async def invoke(call: MatterCall) -> None:
        invocations.append(call)

    app = build_app(store, invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, device_id
    store.close()


@pytest.fixture
async def api_button(
    tmp_path, no_invoke, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    """A button - an input device with no output commands (Spec 6.7)."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(store, no_invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, device_id
    store.close()


@pytest.fixture
async def api_failing_invoke(
    tmp_path, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    """Like `api`, but the invoker stands in for a device that doesn't
    answer - for `test_a_device_that_does_not_answer_yields_502`."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    async def invoke(call: MatterCall) -> None:
        raise RuntimeError("device does not respond")

    app = build_app(store, invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, device_id
    store.close()


@pytest.fixture
async def api_raw_commands(
    tmp_path, no_invoke, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    """Like `api`, but with additional raw (unnamed) commands - for
    `test_hidden_raw_commands_are_counted` (review fix Minor #4,
    2026-09-02). `extract_commands(snapshot, raw=True)` demonstrably yields
    more commands for this template than normal mode does (see
    `tests/export/test_commands.py::test_raw_mode_adds_unknown_clusters_
    but_not_administrative_ones`) - the difference is exactly the commands
    `GET /api/devices/{device_id}/controls` filters out."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot, raw=True), snapshot.node_id)

    app = build_app(store, no_invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, device_id
    store.close()


async def test_plug_offers_exactly_its_three_commands(api):
    """Spec 6.7: output commands come from AcceptedCommandList, not from attributes."""
    client, _, device_id = api
    controls = (await client.get(f"/api/devices/{device_id}/controls")).json()
    assert sorted(c["slug"] for c in controls["commands"]) == ["off", "on", "toggle"]
    assert controls["hidden_raw_commands"] == 0


async def test_button_offers_no_controls(api_button):
    """A button is an input device."""
    client, _, device_id = api_button
    controls = (await client.get(f"/api/devices/{device_id}/controls")).json()
    assert controls["commands"] == []
    assert controls["hidden_raw_commands"] == 0


async def test_hidden_raw_commands_are_counted(api_raw_commands):
    """An unnamed command stays filtered, but not without a trace (review
    fix Minor #4, 2026-09-02)."""
    client, store, device_id = api_raw_commands
    stored = store.commands(device_id)
    named = sum(1 for c in stored if command_slug(c.cluster_id, c.command_id) is not None)
    assert named < len(stored)  # otherwise this test wouldn't be meaningful

    controls = (await client.get(f"/api/devices/{device_id}/controls")).json()
    assert len(controls["commands"]) == named
    assert controls["hidden_raw_commands"] == len(stored) - named
    assert controls["hidden_raw_commands"] > 0


async def test_executing_a_command_reaches_matter(api):
    client, _, device_id = api
    response = await client.post(f"/api/commands/d{device_id}_1_on", json={"value": "1"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_the_same_translation_as_the_loxone_endpoint(api, invocations):
    """Spec 4.2: one conversion, two callers - otherwise they drift apart."""
    client, _, device_id = api
    key = f"d{device_id}_1_on"
    await client.post(f"/api/commands/{key}", json={"value": "1"})
    await client.get(f"/cmd/{key}/1")
    assert len(invocations) == 2
    assert invocations[0] == invocations[1]


async def test_unknown_command_yields_404(api):
    client, _, _ = api
    response = await client.post("/api/commands/d1_1_gibtsnicht", json={"value": "1"})
    assert response.status_code == 404


async def test_a_device_that_does_not_answer_yields_502(api_failing_invoke):
    client, _, device_id = api_failing_invoke
    response = await client.post(f"/api/commands/d{device_id}_1_on", json={"value": "1"})
    assert response.status_code == 502
    assert "Traceback" not in response.text
    assert response.json()["detail"] == "device unreachable: device does not respond"


async def test_a_device_that_does_not_answer_yields_502_in_german(api_failing_invoke):
    """German companion test to test_a_device_that_does_not_answer_yields_502."""
    client, store, device_id = api_failing_invoke
    store.locale.set_language("de")
    response = await client.post(f"/api/commands/d{device_id}_1_on", json={"value": "1"})
    assert response.status_code == 502
    assert "Traceback" not in response.text
    assert response.json()["detail"] == "Geraet nicht erreichbar: device does not respond"


async def test_raw_write_of_a_non_writable_attribute_is_refused(api):
    """A clear refusal beats a write attempt that silently does nothing."""
    client, store, device_id = api
    key = next(s.key for s in store.signals(device_id) if s.ref.cluster_id == 40)
    response = await client.post(f"/api/signals/{key}/write", json={"value": "42"})
    assert response.status_code == 400
    assert response.json()["detail"] == (
        f"The writability of attribute {key!r} cannot be confirmed, so it is not on "
        "the allow-list of writable attributes. If it really is writable, it can be "
        "added there."
    )


async def test_raw_write_of_a_non_writable_attribute_is_refused_in_german(api):
    """German companion test to
    test_raw_write_of_a_non_writable_attribute_is_refused."""
    client, store, device_id = api
    key = next(s.key for s in store.signals(device_id) if s.ref.cluster_id == 40)
    store.locale.set_language("de")
    response = await client.post(f"/api/signals/{key}/write", json={"value": "42"})
    assert response.status_code == 400
    assert response.json()["detail"] == (
        f"Die Beschreibbarkeit von Attribut {key!r} laesst sich nicht bestaetigen, "
        "es steht deshalb nicht auf der Erlaubnisliste beschreibbarer Attribute. Ist "
        "es tatsaechlich beschreibbar, kann es dort ergaenzt werden."
    )


async def test_raw_write_of_a_writable_attribute_is_not_yet_wired(api):
    """Allowed, but not wired up yet - see the module docstring, "Open
    point" paragraph."""
    client, store, device_id = api
    key = next(
        s.key for s in store.signals(device_id) if (s.ref.cluster_id, s.ref.element_id) == (40, 5)
    )
    response = await client.post(f"/api/signals/{key}/write", json={"value": "42"})
    assert response.status_code == 501
    assert response.json()["detail"] == (
        f"Attribute {key!r} is writable, but raw writing is not yet connected to matter-server."
    )


async def test_raw_write_of_a_writable_attribute_is_not_yet_wired_in_german(api):
    """German companion test to
    test_raw_write_of_a_writable_attribute_is_not_yet_wired."""
    client, store, device_id = api
    key = next(
        s.key for s in store.signals(device_id) if (s.ref.cluster_id, s.ref.element_id) == (40, 5)
    )
    store.locale.set_language("de")
    response = await client.post(f"/api/signals/{key}/write", json={"value": "42"})
    assert response.status_code == 501
    assert response.json()["detail"] == (
        f"Attribut {key!r} ist beschreibbar, aber das rohe Schreiben ist noch nicht "
        "an matter-server angebunden."
    )


async def test_writing_an_unknown_signal_yields_404(api):
    client, _, _ = api
    response = await client.post("/api/signals/d1_1_gibtsnicht/write", json={"value": "42"})
    assert response.status_code == 404
    assert response.json()["detail"] == "unknown signal key 'd1_1_gibtsnicht'"


async def test_writing_an_unknown_signal_yields_404_in_german(api):
    client, store, _ = api
    store.locale.set_language("de")
    response = await client.post("/api/signals/d1_1_gibtsnicht/write", json={"value": "42"})
    assert response.status_code == 404
    assert response.json()["detail"] == "unbekannter Signal-Schluessel 'd1_1_gibtsnicht'"


async def test_writing_a_signal_at_a_removed_device_is_refused(api):
    """The same check as with PATCH /api/signals/{key} (api/devices.py,
    review fix Important #4), now for the raw write path."""
    client, store, device_id = api
    key = store.signals(device_id)[0].key
    store.forget_device(device_id)
    response = await client.post(f"/api/signals/{key}/write", json={"value": "42"})
    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"signal {key!r} belongs to device {device_id}, which was removed"
    )


async def test_writing_a_signal_at_a_removed_device_is_refused_in_german(api):
    """German companion test to
    test_writing_a_signal_at_a_removed_device_is_refused."""
    client, store, device_id = api
    key = store.signals(device_id)[0].key
    store.forget_device(device_id)
    store.locale.set_language("de")
    response = await client.post(f"/api/signals/{key}/write", json={"value": "42"})
    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"Signal {key!r} gehoert zu Geraet {device_id}, das entfernt wurde"
    )


async def test_command_at_a_removed_device_is_refused(api):
    """RED reproduction: the same gap as with signals (Task 2), now for
    commands (review fix Important #1, 2026-09-02)."""
    client, store, device_id = api
    key = f"d{device_id}_1_on"
    store.forget_device(device_id)
    response = await client.post(f"/api/commands/{key}", json={"value": "1"})
    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"command {key!r} belongs to device {device_id}, which was removed"
    )


async def test_command_at_a_removed_device_is_refused_in_german(api):
    """German companion test to test_command_at_a_removed_device_is_refused."""
    client, store, device_id = api
    key = f"d{device_id}_1_on"
    store.forget_device(device_id)
    store.locale.set_language("de")
    response = await client.post(f"/api/commands/{key}", json={"value": "1"})
    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"Kommando {key!r} gehoert zu Geraet {device_id}, das entfernt wurde"
    )
