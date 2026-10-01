"""The firmware update queue routes (design 2026-10-01, section 5.1)."""

import asyncio
import sys
from pathlib import Path

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter import i18n
from loxmatter.firmware.service import FirmwareService
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store
from loxmatter.sources import Sources

sys.path.insert(0, str(Path(__file__).parents[1] / "firmware"))
from firmware_fakes import (
    BILRESA_OFFER,
    KAJPLATS_OFFER,
    FakeFirmwareSource,
    idle_facts,
    register,
)


@pytest.fixture
async def queue_api(tmp_path, no_invoke, fake_runtime, fake_otbr):
    store = Store(tmp_path / "t.sqlite")
    lamp_id, lamp = register(store, "ikea_kajplats_cws_lamp.json")
    button_id, button = register(store, "ikea_bilresa_button.json")
    source = FakeFirmwareSource()
    source.facts[lamp] = idle_facts(16842752, "1.1.0")
    source.facts[button] = idle_facts(17301509, "1.8.5")
    store.firmware_status.record_check(lamp_id, KAJPLATS_OFFER, "t0")
    store.firmware_status.record_check(button_id, BILRESA_OFFER, "t0")
    firmware = FirmwareService(store, Sources([source]))
    app = build_app(
        store,
        no_invoke,
        fake_runtime(store),
        sources=Sources([source]),
        thread_dataset_source=fake_otbr,
        firmware=firmware,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, source, firmware, lamp_id, button_id
    await firmware.queue.stop()
    await firmware.jobs.stop()
    await firmware.checker.stop()
    store.close()


async def test_enqueue_starts_the_first_device_and_keeps_the_rest_queued(queue_api):
    client, _, _, firmware, lamp_id, button_id = queue_api
    response = await client.post("/api/firmware/queue", json={"device_ids": [lamp_id, button_id]})
    assert response.status_code == 202
    assert response.json()["queue"]["active"] is True

    async def first_started() -> None:
        while firmware.jobs.running_device_id != lamp_id:
            await asyncio.sleep(0)

    await asyncio.wait_for(first_started(), 5)
    data = (await client.get("/api/firmware")).json()
    assert data["updating_device_id"] == lamp_id
    assert data["queue"]["device_ids"] == [button_id]
    assert data["queue"]["active"] is True
    rows = {d["device_id"]: d for d in data["devices"]}
    assert rows[button_id]["queue_position"] == 1
    assert rows[lamp_id]["queue_position"] is None


async def test_enqueue_without_an_offer_is_409(queue_api):
    client, store, _, _, _, button_id = queue_api
    store.firmware_status.record_check(button_id, None, "t1")
    response = await client.post("/api/firmware/queue", json={"device_ids": [button_id]})
    assert response.status_code == 409
    assert response.json()["detail"] == i18n.t("api.firmware.queue_nothing_to_install")


async def test_enqueue_when_unsupported_is_409(queue_api):
    client, _, source, _, lamp_id, _ = queue_api
    source.supported = False
    response = await client.post("/api/firmware/queue", json={"device_ids": [lamp_id]})
    assert response.status_code == 409
    assert response.json()["detail"] == i18n.t("api.firmware.fail_unsupported")


async def test_delete_empties_the_queue(queue_api):
    client, store, _, _, lamp_id, button_id = queue_api
    store.firmware_status.enqueue([lamp_id, button_id], "t1")
    store.firmware_settings.set_queue_halted_reason("api.firmware.queue_halted_disconnected")
    response = await client.delete("/api/firmware/queue")
    assert response.status_code == 200
    assert response.json()["queue"]["device_ids"] == []
    assert response.json()["queue"]["halted_reason"] is None


async def test_a_halted_queue_is_reported_and_resume_clears_the_halt(queue_api):
    client, store, *_ = queue_api
    store.firmware_settings.set_queue_halted_reason("api.firmware.queue_halted_disconnected")
    data = (await client.get("/api/firmware")).json()
    assert data["queue"]["halted_reason"] == i18n.t("api.firmware.queue_halted_disconnected")
    assert data["queue"]["active"] is False
    response = await client.post("/api/firmware/queue/resume")
    assert response.status_code == 200
    assert response.json()["queue"]["halted_reason"] is None


async def test_the_nothing_to_install_detail_follows_the_language(queue_api):
    client, store, _, _, _, button_id = queue_api
    store.firmware_status.record_check(button_id, None, "t1")
    patched = await client.patch("/api/language", json={"language": "de"})
    assert patched.status_code == 200
    response = await client.post("/api/firmware/queue", json={"device_ids": [button_id]})
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Keines der ausgewählten Geräte hat ein Update zum Installieren."
    )
