# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

"""Tests fuer den Einstellungen-Endpunkt (`api/settings.py`) - siehe
docs/superpowers/specs/2026-09-03-geraete-dashboard-und-export-design.md,
Abschnitt 4."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import authenticate

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
    """Kein zweiter, unabhaengiger Speicher (dieselbe Ueberlegung wie
    `api/export.py`s Moduldocstring fuer den Store insgesamt)."""
    client, store = api
    await client.patch(
        "/api/settings", json={"bridge_ip": "10.0.0.5", "udp_port": 7000, "listen_port": 8080}
    )
    assert store.settings.get().bridge_ip == "10.0.0.5"


async def test_settings_route_requires_a_session(tmp_path, no_invoke, fake_runtime):
    """Wie jede andere `/api`-Route seit dem WebUI-Login (Spec 9) - kein
    eigener Test noetig fuer den Waechter selbst (der ist bereits in
    `tests/api/test_security.py` fuer alle fuenf Router belegt), nur dass
    dieser sechste Router tatsaechlich dazugehoert."""
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
    response = await client.patch(
        "/api/settings/resend-interval", json={"interval_seconds": 60.0}
    )
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
