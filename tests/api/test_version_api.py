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

"""Tests for GET /api/version.

The `api` fixture follows the same pattern as in `test_language.py`: a
local, already LOGGED-IN fixture. `unauthenticated_api` alongside proves
that this route is NOT one of the three deliberate exceptions from
login requirements (`/cmd`, `/resync`, `GET /api/i18n`)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store, schema_version


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[httpx.AsyncClient]:
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client
    store.close()


@pytest.fixture
async def unauthenticated_api(
    tmp_path, no_invoke, fake_runtime
) -> AsyncIterator[httpx.AsyncClient]:
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    store.close()


async def test_die_route_nennt_die_vier_angaben(api, monkeypatch):
    monkeypatch.setenv("LOXMATTER_VERSION", "0.3.0")
    monkeypatch.setenv("LOXMATTER_COMMIT", "a3f91c2")
    monkeypatch.setenv("LOXMATTER_BUILT_AT", "2026-09-08T10:00:00Z")
    response = await api.get("/api/version")
    assert response.status_code == 200
    assert response.json() == {
        "version": "0.3.0",
        "commit": "a3f91c2",
        "built_at": "2026-09-08T10:00:00Z",
        "schema_version": schema_version(),
    }


async def test_it_responds_even_in_development_checkout(api, monkeypatch):
    """No 500 when variables are missing - otherwise the UI
    would be unusable outside of Docker."""
    for name in ("LOXMATTER_VERSION", "LOXMATTER_COMMIT", "LOXMATTER_BUILT_AT"):
        monkeypatch.delenv(name, raising=False)
    response = await api.get("/api/version")
    assert response.status_code == 200
    assert response.json()["version"] == "dev"
    assert response.json()["commit"] is None


async def test_no_access_without_session(unauthenticated_api):
    response = await unauthenticated_api.get("/api/version")
    assert response.status_code == 401


async def test_the_ui_knows_all_texts_of_the_version_card():
    """A missing key shows up in the browser otherwise - as an
    empty field, not as an error. This test confirms the existence
    of all six keys. But this doesn't prove both languages
    are present: raw_template() falls back to English, so
    a missing de entry would go undetected here. Also
    tests/test_i18n.py doesn't cover that -
    test_web_namespace_has_no_missing_english_fallback_gaps checks
    only the existence of 'en', not of 'de'. A missing
    German translation thus goes undetected automatically nowhere."""
    from loxmatter import i18n

    for key in (
        "web.system.version_heading",
        "web.system.version_running",
        "web.system.version_commit",
        "web.system.version_built_at",
        "web.system.version_dev_hint",
        "web.system.version_dev_channel_hint",
    ):
        assert i18n.raw_template(key)
        assert key in i18n.strings_with_prefix("web.")
