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

"""Tests for the settings endpoint (`api/settings.py`) - see
docs/superpowers/specs/2026-09-03-device-dashboard-and-export-design.md,
section 4."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter.api import settings as settings_api
from loxmatter.loxone.probe import MiniserverProbe, ProbeOutcome
from loxmatter.loxone.sender import UdpSender
from loxmatter.loxone.server import build_app
from loxmatter.model.resend_settings_store import (
    DEFAULT_RESEND_INTERVAL_SECONDS,
    MIN_RESEND_INTERVAL_SECONDS,
)
from loxmatter.model.store import DEFAULT_LISTEN_PORT, DEFAULT_UDP_PORT, Store


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[tuple[httpx.AsyncClient, Store]]:
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store
    store.close()


async def test_a_fresh_installation_has_no_bridge_ip_but_default_ports(api):
    client, _ = api
    body = (await client.get("/api/settings")).json()
    assert body["bridge_ip"] is None
    assert body["udp_port"] == DEFAULT_UDP_PORT
    assert body["listen_port"] == DEFAULT_LISTEN_PORT
    assert body["saved_at"] is None


async def test_patch_saves_and_returns_the_new_values(api):
    client, _ = api
    response = await client.patch(
        "/api/settings",
        json={"bridge_ip": "192.168.1.20", "udp_port": 7001, "listen_port": 8081},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["bridge_ip"] == "192.168.1.20"
    assert body["udp_port"] == 7001
    assert body["listen_port"] == 8081
    assert body["saved_at"] is not None


async def test_a_later_get_sees_what_patch_saved(api):
    client, _ = api
    await client.patch(
        "/api/settings",
        json={"bridge_ip": "192.168.1.20", "udp_port": 7001, "listen_port": 8081},
    )
    body = (await client.get("/api/settings")).json()
    assert body["bridge_ip"] == "192.168.1.20"


async def test_an_empty_bridge_ip_yields_422(api):
    client, _ = api
    response = await client.patch(
        "/api/settings", json={"bridge_ip": "", "udp_port": 7000, "listen_port": 8080}
    )
    assert response.status_code == 422


async def test_settings_are_stored_in_the_same_database_the_export_router_reads(api):
    """No second, independent store (same reasoning as `api/export.py`'s
    module docstring for the store overall)."""
    client, store = api
    await client.patch(
        "/api/settings", json={"bridge_ip": "10.0.0.5", "udp_port": 7000, "listen_port": 8080}
    )
    assert store.settings.get().bridge_ip == "10.0.0.5"


async def test_settings_route_requires_a_session(tmp_path, no_invoke, fake_runtime):
    """Like every other `/api` route since WebUI login (Spec 9) - no
    separate test needed for the guard itself (it is already covered in
    `tests/api/test_security.py` for all five routers), only that this
    sixth router actually belongs."""
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/settings")
    store.close()
    assert response.status_code == 401


async def test_a_fresh_installation_has_the_default_resend_interval(api):
    client, _ = api
    body = (await client.get("/api/settings/resend-interval")).json()
    assert body["interval_seconds"] == DEFAULT_RESEND_INTERVAL_SECONDS


async def test_patch_saves_and_returns_the_new_interval(api):
    client, _ = api
    response = await client.patch("/api/settings/resend-interval", json={"interval_seconds": 60.0})
    assert response.status_code == 200
    assert response.json()["interval_seconds"] == 60.0


async def test_a_later_get_sees_what_patch_saved_for_the_interval(api):
    client, _ = api
    await client.patch("/api/settings/resend-interval", json={"interval_seconds": 45.0})
    body = (await client.get("/api/settings/resend-interval")).json()
    assert body["interval_seconds"] == 45.0


async def test_an_interval_below_the_minimum_yields_422(api):
    client, _ = api
    response = await client.patch(
        "/api/settings/resend-interval",
        json={"interval_seconds": MIN_RESEND_INTERVAL_SECONDS - 1},
    )
    assert response.status_code == 422


async def test_a_non_positive_interval_yields_422(api):
    client, _ = api
    response = await client.patch("/api/settings/resend-interval", json={"interval_seconds": 0})
    assert response.status_code == 422


async def test_resend_interval_route_requires_a_session(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/settings/resend-interval")
    store.close()
    assert response.status_code == 401


@pytest.fixture
def probed(monkeypatch):
    """Replaces the network probe - `tests/loxone/test_probe.py` tests the
    real one against a local server. Returns the list of probed IPs and lets
    a test choose the answer."""
    calls: list[str] = []
    answer = {
        "probe": MiniserverProbe(
            ProbeOutcome.FOUND, serial="50:4F:94:00:00:01", firmware="17.3.9.18"
        )
    }

    async def fake_probe(ip: str, **_: object) -> MiniserverProbe:
        calls.append(ip)
        return answer["probe"]

    monkeypatch.setattr(settings_api, "probe_miniserver", fake_probe)
    return calls, answer


@pytest.fixture
async def wired_api(tmp_path, no_invoke, fake_runtime, probed):
    """Like `api`, with a sender the route can retarget and the runtime it
    resends through."""
    store = Store(tmp_path / "t.sqlite")
    runtime = fake_runtime(store)
    sender = UdpSender(None, DEFAULT_UDP_PORT)
    app = build_app(store, no_invoke, runtime, sender=sender)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, sender, runtime
    await sender.close()
    store.close()


async def _until(condition, timeout: float = 1.0) -> None:
    """The resend runs as a background task; wait for it instead of sleeping
    a fixed time."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition never became true"
        await asyncio.sleep(0.01)


def _body(**overrides):
    body = {"bridge_ip": "192.168.1.20", "udp_port": 7000, "listen_port": 8080}
    body.update(overrides)
    return body


async def test_a_fresh_installation_has_no_miniserver_ip(api):
    client, _ = api
    body = (await client.get("/api/settings")).json()
    assert body["miniserver_ip"] is None
    assert body["miniserver_check"] is None


async def test_saving_the_miniserver_ip_retargets_the_sender_and_resends(wired_api):
    client, store, sender, runtime = wired_api
    response = await client.patch(
        "/api/settings", json=_body(miniserver_ip="192.168.1.77", udp_port=7001)
    )
    assert response.status_code == 200
    assert response.json()["miniserver_ip"] == "192.168.1.77"
    assert store.settings.get().miniserver_ip == "192.168.1.77"
    assert sender.target == ("192.168.1.77", 7001)
    await _until(lambda: runtime.resend_calls == 1)


async def test_saving_the_same_target_again_does_not_resend(wired_api):
    client, _, _, runtime = wired_api
    await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77"))
    await _until(lambda: runtime.resend_calls == 1)
    await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77", listen_port=8081))
    await asyncio.sleep(0.05)
    assert runtime.resend_calls == 1


async def test_the_response_carries_the_probe_result(wired_api, probed):
    client, _, _, _ = wired_api
    calls, _ = probed
    body = (await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77"))).json()
    assert calls == ["192.168.1.77"]
    check = body["miniserver_check"]
    assert check["found"] is True
    assert check["serial"] == "50:4F:94:00:00:01"
    assert check["firmware"] == "17.3.9.18"
    assert "192.168.1.77" in check["message"]


async def test_an_unreachable_miniserver_is_saved_anyway_with_a_warning(wired_api, probed):
    client, store, sender, _ = wired_api
    _, answer = probed
    answer["probe"] = MiniserverProbe(ProbeOutcome.TIMEOUT)
    response = await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77"))
    assert response.status_code == 200
    assert response.json()["miniserver_check"]["found"] is False
    assert store.settings.get().miniserver_ip == "192.168.1.77"
    assert sender.target == ("192.168.1.77", 7000)


async def test_an_invalid_miniserver_ip_yields_422_and_saves_nothing(wired_api):
    client, store, sender, _ = wired_api
    response = await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1"))
    assert response.status_code == 422
    assert "192.168.1" in response.json()["detail"]
    assert store.settings.get().saved_at is None
    assert sender.target is None


async def test_the_invalid_ip_message_is_german_under_the_german_locale(wired_api):
    client, store, _, _ = wired_api
    store.locale.set_language("de")
    response = await client.patch("/api/settings", json=_body(miniserver_ip="miniserver"))
    assert response.status_code == 422
    assert "IPv4" in response.json()["detail"]
    assert "IP des Miniservers" in response.json()["detail"]


async def test_an_empty_miniserver_ip_clears_the_address_and_the_target(wired_api, probed):
    client, store, sender, _ = wired_api
    calls, _ = probed
    await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77"))
    body = (await client.patch("/api/settings", json=_body(miniserver_ip=""))).json()
    assert body["miniserver_ip"] is None
    assert body["miniserver_check"] is None
    assert store.settings.get().miniserver_ip is None
    assert sender.target is None
    assert calls == ["192.168.1.77"]


async def test_a_body_without_the_field_keeps_the_stored_address(wired_api, probed):
    """A browser tab still running the previous version's app.js after an
    update knows nothing of the field. Saving the bridge's IP from it must
    not erase the Miniserver's address (spec section 7)."""
    client, store, sender, _ = wired_api
    calls, _ = probed
    await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77"))
    await client.patch("/api/settings", json=_body(bridge_ip="192.168.1.21"))
    assert store.settings.get().miniserver_ip == "192.168.1.77"
    assert sender.target == ("192.168.1.77", 7000)
    assert calls == ["192.168.1.77"], "an old tab's save must not re-probe an unchanged address"


async def test_changing_only_the_udp_port_retargets_the_sender(wired_api):
    """The defect in spec section 1: the port in the card used to reach the
    templates only."""
    client, _, sender, runtime = wired_api
    await client.patch("/api/settings", json=_body(miniserver_ip="192.168.1.77"))
    await client.patch("/api/settings", json=_body(udp_port=7009))
    assert sender.target == ("192.168.1.77", 7009)
    await _until(lambda: runtime.resend_calls == 2)
