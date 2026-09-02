"""Tests fuer die Bedienungs-API (Task 4, Phase 5) - siehe api/control.py."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import load_snapshot

from loxmatter.commands.translate import MatterCall
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


@pytest.fixture
def invocations() -> list[MatterCall]:
    """Sammelt jeden `MatterCall`, den der EINE Invoker entgegennimmt, der in
    `api` sowohl an den Loxone-Endpunkt (`/cmd`) als auch an die
    WebUI-Route (`/api/commands`) durchgereicht wird - Grundlage fuer
    `test_the_same_translation_as_the_loxone_endpoint` (Spec 4.2)."""
    return []


@pytest.fixture
async def api(
    tmp_path, invocations, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    """Wie die `api`-Fixture in `test_devices.py`, aber mit einem
    AUFZEICHNENDEN Invoker statt `no_invoke`: `test_the_same_translation_
    as_the_loxone_endpoint` unten braucht die tatsaechlich uebersetzten
    `MatterCall`s, nicht nur, dass irgendein Invoker existiert."""
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
        yield client, store, device_id
    store.close()


@pytest.fixture
async def api_button(
    tmp_path, no_invoke, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    """Ein Taster - Eingabegeraet ohne Ausgangsbefehle (Spec 6.7)."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(store, no_invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store, device_id
    store.close()


@pytest.fixture
async def api_failing_invoke(
    tmp_path, fake_runtime, fake_client
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    """Wie `api`, aber der Invoker steht fuer ein Geraet, das nicht antwortet
    - fuer `test_a_device_that_does_not_answer_yields_502`."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    async def invoke(call: MatterCall) -> None:
        raise RuntimeError("Geraet antwortet nicht")

    app = build_app(store, invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store, device_id
    store.close()


async def test_plug_offers_exactly_its_three_commands(api):
    """Spec 6.7: Ausgangsbefehle stammen aus AcceptedCommandList, nicht aus Attributen."""
    client, _, device_id = api
    controls = (await client.get(f"/api/devices/{device_id}/controls")).json()
    assert sorted(c["slug"] for c in controls) == ["off", "on", "toggle"]


async def test_button_offers_no_controls(api_button):
    """Ein Taster ist ein Eingabegeraet."""
    client, _, device_id = api_button
    assert (await client.get(f"/api/devices/{device_id}/controls")).json() == []


async def test_executing_a_command_reaches_matter(api):
    client, _, device_id = api
    response = await client.post(f"/api/commands/d{device_id}_1_on", json={"value": "1"})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_the_same_translation_as_the_loxone_endpoint(api, invocations):
    """Spec 4.2: eine Umrechnung, zwei Aufrufer - sonst driften sie."""
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


async def test_raw_write_of_a_non_writable_attribute_is_refused(api):
    """Lieber eine klare Absage als ein Schreibversuch, der still nichts tut."""
    client, store, device_id = api
    key = next(s.key for s in store.signals(device_id) if s.ref.cluster_id == 40)
    response = await client.post(f"/api/signals/{key}/write", json={"value": "42"})
    assert response.status_code == 400
