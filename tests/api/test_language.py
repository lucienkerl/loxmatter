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

"""Tests for GET /api/i18n (unprotected) and PATCH /api/language
(protected), as well as the sync_language middleware that reads the stored
language setting fresh on every request.

`api` follows the same pattern as in `test_settings_api.py`: a local,
already SIGNED-IN fixture (`authenticate` from `conftest.py`) that returns
`(client, store)`. `GET /api/i18n` is the third, deliberate exception to
the login requirement (Spec section 5, alongside `/cmd` and `/resync`) -
for that, `unauthenticated_client` below builds the same app WITHOUT
`authenticate()`, exactly like
`test_settings_route_requires_a_session` in `test_settings_api.py`."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter import i18n
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[tuple[httpx.AsyncClient, Store]]:
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store
    store.close()


@pytest.fixture
async def unauthenticated_api(
    tmp_path, no_invoke, fake_runtime
) -> AsyncIterator[tuple[httpx.AsyncClient, Store]]:
    """The same app as `api`, but without `authenticate()` - for the two
    tests meant to prove exactly a route's freedom from (or requirement
    for) login."""
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store
    store.close()


async def test_get_i18n_works_without_a_session(unauthenticated_api):
    """The third, deliberate exception to the login requirement (Spec
    section 5) - no cookie, no token, still 200."""
    client, _ = unauthenticated_api
    response = await client.get("/api/i18n")
    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "en"
    assert isinstance(body["strings"], dict)


async def test_get_i18n_only_returns_the_web_namespace(unauthenticated_api):
    client, _ = unauthenticated_api
    response = await client.get("/api/i18n")
    body = response.json()
    assert all(key.startswith("web.") for key in body["strings"])


async def test_get_i18n_does_not_crash_on_a_web_key_with_a_placeholder(
    unauthenticated_api, monkeypatch
):
    """The actual regression test for the finding from the Task 8 report
    (see web.test.smoke in strings.yaml): `_web_strings()` used to call
    `i18n.t(key)` with NO `values` for EVERY `web.*` key - `t()` always
    calls `.format(**values)`, and a key with a `{placeholder}` made that
    crash there with `KeyError`. Because `_web_strings()` does this in a
    single dict comprehension, that took down not just the one key, but the
    ENTIRE `GET /api/i18n` response (confirmed by four independent tests in
    this file, merged long ago, that suddenly started failing because of
    it).

    A temporary `web.*` key inserted into `i18n._STRINGS` via `monkeypatch`
    instead of a permanent addition to `strings.yaml`: this test is only
    meant to check the interplay of `_web_strings()` and `i18n`, not to add
    yet another permanent test entry to the real table (one already exists
    for that, `web.test.smoke` - deliberately WITHOUT a placeholder).

    Expects 200 with the UNRESOLVED template in the response body -
    `i18n.raw_template()` instead of `i18n.t()`, because the browser fills
    in the placeholder itself (see app.js, `t()`), with values the server
    cannot know."""
    monkeypatch.setitem(
        i18n._STRINGS,
        "web.test.placeholder_smoke",
        {"en": "{seconds}s ago"},
    )
    client, _ = unauthenticated_api
    response = await client.get("/api/i18n")
    assert response.status_code == 200
    assert response.json()["strings"]["web.test.placeholder_smoke"] == "{seconds}s ago"


async def test_get_i18n_carries_the_real_placeholder_keys_unresolved(unauthenticated_api):
    """The same finding, but on a REAL entry of the table instead of an
    injected stand-in: `web.devices.commission_success` has always carried
    `{label}`, and the commissioning branch added another web.* entry with
    `web.devices.thread_dataset_hint`. If `_web_strings()` were ever
    switched back to `i18n.t()`, this exact test would fail - with 500 for
    the entire response, not just for the one key."""
    client, _ = unauthenticated_api

    response = await client.get("/api/i18n")

    assert response.status_code == 200
    strings = response.json()["strings"]
    assert "{label}" in strings["web.devices.commission_success"]
    # The boundary from Spec 12.3 no longer exists - so the text must no
    # longer announce it either.
    assert "Spec 12.3" not in strings["web.devices.commission_success"]
    assert "from now on" in strings["web.devices.commission_success"]
    assert strings["web.devices.thread_dataset_hint"]


async def test_patch_language_requires_a_session(unauthenticated_api):
    client, _ = unauthenticated_api
    response = await client.patch("/api/language", json={"language": "de"})
    assert response.status_code == 401


async def test_patch_language_persists_and_is_reflected_by_the_next_request(api):
    """Proves the middleware, not just the route: a SECOND, independent
    request (here /api/i18n, which needs no login) must see the new
    language - not just store.locale directly."""
    client, store = api
    response = await client.patch("/api/language", json={"language": "de"})
    assert response.status_code == 200
    assert store.locale.get_language() == "de"

    follow_up = await client.get("/api/i18n")
    assert follow_up.json()["language"] == "de"


async def test_patch_language_rejects_an_unsupported_value(api):
    client, _ = api
    response = await client.patch("/api/language", json={"language": "fr"})
    assert response.status_code == 400


async def test_sync_language_middleware_sees_a_change_made_directly_through_the_store(
    unauthenticated_api,
):
    """The gap from Spec section 4: a change that did NOT go through PATCH
    /api/language (here directly via store.locale, the way
    `loxmatter set-language` would in another process) must still be seen
    by the NEXT request."""
    client, store = unauthenticated_api
    store.locale.set_language("de")
    response = await client.get("/api/i18n")
    assert response.json()["language"] == "de"


def test_a_request_does_not_leak_language_state_to_i18n_t_outside_the_request():
    """After every request, the global i18n language should be back to the
    value set by tests/conftest.py's reset_language fixture - this test
    only documents the expectation; reset_language itself does the actual
    enforcing."""
    assert i18n.current_language() == i18n.DEFAULT_LANGUAGE
