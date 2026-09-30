"""The firmware routes (design 2026-09-30, section 9.1)."""

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
    KAJPLATS_OFFER,
    FakeFirmwareSource,
    idle_facts,
    register,
)


@pytest.fixture
async def firmware_api(tmp_path, no_invoke, fake_runtime, fake_otbr):
    store = Store(tmp_path / "t.sqlite")
    lamp_id, lamp = register(store, "ikea_kajplats_cws_lamp.json")
    source = FakeFirmwareSource()
    source.facts[lamp] = idle_facts(16842752, "1.1.0")
    source.offers[lamp] = KAJPLATS_OFFER
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
        yield client, store, source, firmware, lamp_id, lamp
    await firmware.jobs.stop()
    firmware.checker.cancel()
    store.close()


async def test_overview_lists_the_device_unchecked(firmware_api):
    client, _, _, _, lamp_id, _ = firmware_api
    data = (await client.get("/api/firmware")).json()
    assert data["supported"] is True
    assert data["daily_check_enabled"] is True
    row = next(d for d in data["devices"] if d["device_id"] == lamp_id)
    assert row["state"] == "unchecked"
    assert row["installed"] == "1.1.0"
    assert row["matter_version"] == "1.4"
    assert row["online"] is True


async def test_check_all_returns_at_once_and_fills_the_overview(firmware_api):
    client, _, _, firmware, lamp_id, _ = firmware_api
    response = await client.post("/api/firmware/check")
    assert response.status_code == 202
    await firmware.checker.check_all()
    row = next(
        d
        for d in (await client.get("/api/firmware")).json()["devices"]
        if d["device_id"] == lamp_id
    )
    assert row["state"] == "available"
    assert row["offer"]["version_string"] == "1.2.0"


async def test_check_one(firmware_api):
    client, _, source, _, lamp_id, lamp = firmware_api
    response = await client.post(f"/api/devices/{lamp_id}/firmware/check")
    assert response.status_code == 200
    assert response.json()["state"] == "available"
    assert source.checked == [lamp]


async def test_install_starts_the_job(firmware_api):
    client, _, _, firmware, lamp_id, _ = firmware_api
    await client.post(f"/api/devices/{lamp_id}/firmware/check")
    response = await client.post(
        f"/api/devices/{lamp_id}/firmware/update", json={"software_version": 16908288}
    )
    assert response.status_code == 202
    assert response.json()["state"] == "transferring"
    assert firmware.jobs.running_device_id == lamp_id


async def test_install_with_a_stale_version_is_409(firmware_api):
    client, _, _, _, lamp_id, _ = firmware_api
    await client.post(f"/api/devices/{lamp_id}/firmware/check")
    response = await client.post(
        f"/api/devices/{lamp_id}/firmware/update", json={"software_version": 1}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == i18n.t("api.firmware.fail_offer_changed")


async def test_a_second_install_is_409_naming_the_first_device(firmware_api):
    client, store, source, _, lamp_id, _ = firmware_api
    button_id, button = register(store, "ikea_bilresa_button.json")
    source.facts[button] = idle_facts(17301509, "1.8.5")
    await client.post(f"/api/devices/{lamp_id}/firmware/check")
    await client.post(
        f"/api/devices/{lamp_id}/firmware/update", json={"software_version": 16908288}
    )
    response = await client.post(
        f"/api/devices/{button_id}/firmware/update", json={"software_version": 1}
    )
    assert response.status_code == 409
    label = store.device(lamp_id).label
    assert response.json()["detail"] == i18n.t("api.firmware.fail_busy", device=label)


async def test_install_on_an_offline_device_is_409(firmware_api):
    client, _, source, _, lamp_id, lamp = firmware_api
    await client.post(f"/api/devices/{lamp_id}/firmware/check")
    source.facts[lamp] = idle_facts(16842752, "1.1.0", available=False)
    response = await client.post(
        f"/api/devices/{lamp_id}/firmware/update", json={"software_version": 16908288}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == i18n.t("api.firmware.fail_offline")


async def test_unknown_device_is_404(firmware_api):
    client, *_ = firmware_api
    response = await client.post("/api/devices/999/firmware/check")
    assert response.status_code == 404


async def test_an_unsupported_server_is_409(firmware_api):
    client, _, source, *_ = firmware_api
    source.supported = False
    assert (await client.get("/api/firmware")).json()["supported"] is False
    response = await client.post("/api/firmware/check")
    assert response.status_code == 409
    assert response.json()["detail"] == i18n.t("api.firmware.fail_unsupported")


async def test_the_daily_check_can_be_switched_off(firmware_api):
    client, store, *_ = firmware_api
    response = await client.put("/api/firmware/settings", json={"daily_check_enabled": False})
    assert response.status_code == 200
    assert response.json()["daily_check_enabled"] is False
    assert store.firmware_settings.get_daily_check_enabled() is False


async def test_the_routes_need_a_login(tmp_path, no_invoke, fake_runtime, fake_otbr):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(
        store, no_invoke, fake_runtime(store), sources=Sources([]), thread_dataset_source=fake_otbr
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/firmware")).status_code == 401
    store.close()


async def test_without_a_firmware_source_the_overview_says_unsupported(
    tmp_path, no_invoke, fake_runtime, fake_client, fake_otbr
):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(
        store, no_invoke, fake_runtime(store), client=fake_client, thread_dataset_source=fake_otbr
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        assert (await client.get("/api/firmware")).json()["supported"] is False
    store.close()
