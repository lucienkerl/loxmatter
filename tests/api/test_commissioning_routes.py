"""The `/api/commissioning*` routes (design 2026-10-02, section 10).

A thin layer over `CommissioningSession`: these tests drive the session
built from `commissioning_fakes` through HTTP and check the status codes,
the translated details and that no pairing code ever reaches a response."""

import httpx2 as httpx
import pytest
from commissioning_fakes import (
    KAJPLATS_E27,
    QR_CODE,
    QR_CODE_ON_NETWORK,
    advert,
    build_session,
)
from conftest import authenticate, settle_until

from loxmatter import i18n
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store
from loxmatter.sources import IdentifyUnsupportedError


@pytest.fixture
async def api(tmp_path, no_invoke, fake_client, fake_runtime, fake_otbr):
    h = build_session(tmp_path, fake_client, fake_runtime, fake_otbr)
    app = build_app(
        h.store,
        no_invoke,
        fake_runtime(h.store),
        client=fake_client,
        thread_dataset_source=fake_otbr,
        commissioning_session=h.session,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(h.store, client)
        yield client, h, transport
    h.gate.release_all()
    await h.session.aclose()
    h.store.close()


async def idle(h):
    await settle_until(
        lambda: not any(c["state"] in ("queued", "running") for c in h.session.view()["cards"]),
        "the worker finished its queue",
    )


async def naming_card(client, h):
    """A card whose device was commissioned and now waits for its name."""
    response = await client.post(
        "/api/commissioning/codes", json={"code": QR_CODE_ON_NETWORK, "room": None}
    )
    assert response.status_code == 201
    card_id = response.json()["id"]
    assert (await client.post("/api/commissioning/start")).status_code == 202
    await idle(h)
    return card_id


def card_in(view, card_id):
    return next(card for card in view["cards"] if card["id"] == card_id)


async def test_get_returns_the_view(api):
    client, h, _ = api
    response = await client.get("/api/commissioning")
    assert response.status_code == 200
    assert response.json() == h.session.view()


async def test_routes_need_a_login(api):
    _, _, transport = api
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as anonymous:
        assert (await anonymous.get("/api/commissioning")).status_code == 401
        response = await anonymous.post(
            "/api/commissioning/codes", json={"code": QR_CODE, "room": None}
        )
        assert response.status_code == 401


async def test_qr_code_after_a_scan_makes_the_card_ready_and_never_returns_the_code(api):
    client, h, _ = api
    h.reader.adverts = [advert("AA:01", 3840, KAJPLATS_E27, -60)]
    scan = await client.post("/api/commissioning/scan", json={"automatic": False})
    assert scan.status_code == 202
    assert len(scan.json()["cards"]) == 1
    response = await client.post(
        "/api/commissioning/codes", json={"code": QR_CODE, "room": "Küche"}
    )
    assert response.status_code == 201
    card = response.json()
    assert card["state"] == "ready"
    assert card["has_code"] is True
    assert card["room"] == "Küche"
    assert "Y.K9042C00KA0648G00" not in response.text
    assert "Y.K9042C00KA0648G00" not in (await client.get("/api/commissioning")).text


async def test_scan_body_is_optional(api):
    client, h, _ = api
    response = await client.post("/api/commissioning/scan")
    assert response.status_code == 202
    assert h.scanner.scans == [10.0]


async def test_a_typo_is_422_and_a_duplicate_409(api):
    client, _, _ = api
    typo = await client.post("/api/commissioning/codes", json={"code": "34970112333", "room": None})
    assert typo.status_code == 422
    assert typo.json()["detail"] == i18n.t("api.commissioning.fail_typo")
    first = await client.post("/api/commissioning/codes", json={"code": QR_CODE, "room": None})
    assert first.status_code == 201
    again = await client.post("/api/commissioning/codes", json={"code": QR_CODE, "room": None})
    assert again.status_code == 409
    assert again.json()["detail"] == i18n.t("api.commissioning.fail_duplicate")
    assert QR_CODE not in again.text


async def test_a_typo_detail_is_german_under_the_german_locale(api):
    client, h, _ = api
    h.store.locale.set_language("de")
    typo = await client.post("/api/commissioning/codes", json={"code": "34970112333", "room": None})
    assert typo.status_code == 422
    assert typo.json()["detail"] == (
        "Die Prüfziffer stimmt nicht. Bitte prüfen Sie den Code auf einen Tippfehler."
    )


async def test_scan_while_the_worker_runs_is_409(api):
    client, h, _ = api
    h.gate.held = True
    card = await client.post(
        "/api/commissioning/codes", json={"code": QR_CODE_ON_NETWORK, "room": None}
    )
    await client.post("/api/commissioning/start")
    await settle_until(
        lambda: card_in(h.session.view(), card.json()["id"])["state"] == "running", "running"
    )
    response = await client.post("/api/commissioning/scan", json={"automatic": True})
    assert response.status_code == 409
    assert response.json()["detail"] == i18n.t("api.commissioning.fail_busy")
    assert h.scanner.scans == []


async def test_a_skipped_automatic_scan_is_still_202(api):
    client, h, _ = api
    assert (
        await client.post("/api/commissioning/scan", json={"automatic": True})
    ).status_code == 202
    again = await client.post("/api/commissioning/scan", json={"automatic": True})
    assert again.status_code == 202
    assert len(h.scanner.scans) == 1


async def test_patch_sets_name_and_room_and_clears_the_room(api):
    client, _, _ = api
    created = await client.post("/api/commissioning/codes", json={"code": QR_CODE, "room": "Bad"})
    card_id = created.json()["id"]
    named = await client.patch(f"/api/commissioning/cards/{card_id}", json={"name": "Spiegel"})
    assert named.status_code == 200
    assert named.json()["name"] == "Spiegel"
    # Leaving `room` out keeps it.
    assert named.json()["room"] == "Bad"
    moved = await client.patch(f"/api/commissioning/cards/{card_id}", json={"room": "Flur"})
    assert moved.json()["room"] == "Flur"
    assert moved.json()["name"] == "Spiegel"
    cleared = await client.patch(f"/api/commissioning/cards/{card_id}", json={"room": None})
    assert cleared.json()["room"] is None
    assert card_in((await client.get("/api/commissioning")).json(), card_id)["name"] == "Spiegel"


async def test_unknown_card_is_404(api):
    client, _, _ = api
    for response in (
        await client.patch("/api/commissioning/cards/99", json={"name": "x"}),
        await client.post("/api/commissioning/cards/99/confirm-name"),
        await client.post("/api/commissioning/cards/99/skip-name"),
        await client.post("/api/commissioning/cards/99/force"),
        await client.post("/api/commissioning/cards/99/identify", json={"on": True}),
        await client.delete("/api/commissioning/cards/99"),
    ):
        assert response.status_code == 404
        assert response.json()["detail"] == i18n.t("api.commissioning.fail_unknown_card")


async def test_start_runs_the_queue_into_the_naming_line(api):
    client, h, _ = api
    card_id = await naming_card(client, h)
    view = (await client.get("/api/commissioning")).json()
    assert card_in(view, card_id)["state"] == "naming"
    assert view["naming"] == [card_id]


async def test_confirm_name_needs_a_name(api):
    client, h, _ = api
    card_id = await naming_card(client, h)
    missing = await client.post(f"/api/commissioning/cards/{card_id}/confirm-name")
    assert missing.status_code == 422
    assert missing.json()["detail"] == i18n.t("api.commissioning.fail_name_missing")
    await client.patch(f"/api/commissioning/cards/{card_id}", json={"name": "Flurlampe"})
    done = await client.post(f"/api/commissioning/cards/{card_id}/confirm-name")
    assert done.status_code == 200
    assert done.json()["state"] == "done"
    assert h.store.device(done.json()["device_id"]).label == "Flurlampe"


async def test_skip_name_ends_the_naming_turn(api):
    client, h, _ = api
    card_id = await naming_card(client, h)
    skipped = await client.post(f"/api/commissioning/cards/{card_id}/skip-name")
    assert skipped.status_code == 200
    assert skipped.json()["state"] == "done"


async def test_force_queues_a_card_that_is_not_nearby(api):
    client, h, _ = api
    h.gate.held = True
    created = await client.post("/api/commissioning/codes", json={"code": QR_CODE, "room": None})
    card_id = created.json()["id"]
    assert created.json()["state"] == "not_nearby"
    forced = await client.post(f"/api/commissioning/cards/{card_id}/force")
    assert forced.status_code == 202
    assert forced.json()["state"] in ("queued", "running")


async def test_delete_refuses_a_running_card(api):
    client, h, _ = api
    h.gate.held = True
    created = await client.post(
        "/api/commissioning/codes", json={"code": QR_CODE_ON_NETWORK, "room": None}
    )
    card_id = created.json()["id"]
    await client.post("/api/commissioning/start")
    await settle_until(lambda: card_in(h.session.view(), card_id)["state"] == "running", "running")
    refused = await client.delete(f"/api/commissioning/cards/{card_id}")
    assert refused.status_code == 409
    assert refused.json()["detail"] == i18n.t("api.commissioning.fail_card_running")


async def test_delete_and_clear_remove_cards(api):
    client, _, _ = api
    one = (
        await client.post("/api/commissioning/codes", json={"code": QR_CODE, "room": None})
    ).json()
    await client.post("/api/commissioning/codes", json={"code": QR_CODE_ON_NETWORK, "room": None})
    deleted = await client.delete(f"/api/commissioning/cards/{one['id']}")
    assert deleted.status_code == 204
    assert len((await client.get("/api/commissioning")).json()["cards"]) == 1
    cleared = await client.post("/api/commissioning/clear")
    assert cleared.status_code == 200
    assert cleared.json()["cards"] == []


async def test_identify_a_card_records_the_blink(api):
    client, h, _ = api
    card_id = await naming_card(client, h)
    device_id = card_in(h.session.view(), card_id)["device_id"]
    response = await client.post(f"/api/commissioning/cards/{card_id}/identify", json={"on": True})
    assert response.status_code == 204
    assert h.identify.starts()[-1] == ("start", device_id, False, 30)
    off = await client.post(f"/api/commissioning/cards/{card_id}/identify", json={"on": False})
    assert off.status_code == 204
    assert h.identify.blinking is None


async def test_identify_on_a_device_without_identify_is_409(api, monkeypatch):
    client, h, _ = api
    card_id = await naming_card(client, h)

    async def unsupported(device_id, *, renew, seconds=30):
        raise IdentifyUnsupportedError("no Identify cluster")

    monkeypatch.setattr(h.identify, "start", unsupported)
    response = await client.post(f"/api/commissioning/cards/{card_id}/identify", json={"on": True})
    assert response.status_code == 409
    assert response.json()["detail"] == i18n.t("api.commissioning.fail_no_identify")


async def test_without_a_matter_client_there_are_no_routes(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        assert (await client.get("/api/commissioning")).status_code == 404
    store.close()


async def test_build_app_makes_a_session_when_a_client_exists(
    tmp_path, no_invoke, fake_client, fake_runtime
):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        response = await client.get("/api/commissioning")
        assert response.status_code == 200
        assert response.json()["cards"] == []
    store.close()


async def test_a_device_tile_blink_shows_on_its_card(
    tmp_path, no_invoke, fake_client, fake_runtime, fake_otbr
):
    """Design 9.2: `build_app` builds ONE coordinator for the device tiles and
    the session it makes itself, so a blink started from a tile is the
    card's blink too. Fault to prove it: let `build_device_router` build
    its own coordinator again - `blinking_card` then stays `None`."""
    store = Store(tmp_path / "t.sqlite")
    fake_client.store = store
    app = build_app(
        store, no_invoke, fake_runtime(store), client=fake_client, thread_dataset_source=fake_otbr
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        created = await client.post(
            "/api/commissioning/codes", json={"code": QR_CODE_ON_NETWORK, "room": None}
        )
        card_id = created.json()["id"]
        await client.post("/api/commissioning/start")

        async def state() -> str:
            return card_in((await client.get("/api/commissioning")).json(), card_id)["state"]

        for _ in range(1000):
            if await state() == "naming":
                break
        assert await state() == "naming"
        await client.post(f"/api/commissioning/cards/{card_id}/skip-name")
        view = (await client.get("/api/commissioning")).json()
        assert view["blinking_card"] is None
        device_id = card_in(view, card_id)["device_id"]

        on = await client.post(f"/api/devices/{device_id}/identify", json={"on": True})
        assert on.status_code == 204
        assert (await client.get("/api/commissioning")).json()["blinking_card"] == card_id
        await client.post(f"/api/devices/{device_id}/identify", json={"on": False})
    store.close()
