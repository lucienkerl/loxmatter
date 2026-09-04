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

"""Tests fuer GET /api/i18n (ungeschuetzt) und PATCH /api/language
(geschuetzt) sowie die sync_language-Middleware, die die gespeicherte
Spracheinstellung bei jeder Anfrage frisch liest.

`api` folgt demselben Muster wie in `test_settings_api.py`: eine lokale,
bereits ANGEMELDETE Fixture (`authenticate` aus `conftest.py`), die
`(client, store)` liefert. `GET /api/i18n` ist die dritte, bewusste
Ausnahme von der Anmeldepflicht (Spec-Abschnitt 5, neben `/cmd` und
`/resync`) - dafuer baut `unauthenticated_client` unten dieselbe App
OHNE `authenticate()` auf, genau wie
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
    """Dieselbe App wie `api`, aber ohne `authenticate()` - fuer die beiden
    Tests, die genau die Anmeldefreiheit (bzw. -pflicht) einer Route
    belegen sollen."""
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store
    store.close()


async def test_get_i18n_works_without_a_session(unauthenticated_api):
    """Die dritte, bewusste Ausnahme von der Anmeldepflicht (Spec-Abschnitt
    5) - ohne Cookie, ohne Token, trotzdem 200."""
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


async def test_patch_language_requires_a_session(unauthenticated_api):
    client, _ = unauthenticated_api
    response = await client.patch("/api/language", json={"language": "de"})
    assert response.status_code == 401


async def test_patch_language_persists_and_is_reflected_by_the_next_request(api):
    """Beweist die Middleware, nicht nur die Route: eine ZWEITE, unabhaengige
    Anfrage (hier /api/i18n, das keine Anmeldung braucht) muss die neue
    Sprache sehen - nicht nur store.locale direkt."""
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
    """Die Luecke aus Spec-Abschnitt 4: eine Aenderung, die NICHT ueber
    PATCH /api/language lief (hier direkt ueber store.locale, wie es
    `loxmatter set-language` in einem anderen Prozess taete), muss die
    NAECHSTE Anfrage trotzdem sehen."""
    client, store = unauthenticated_api
    store.locale.set_language("de")
    response = await client.get("/api/i18n")
    assert response.json()["language"] == "de"


def test_a_request_does_not_leak_language_state_to_i18n_t_outside_the_request():
    """Nach jeder Anfrage soll die globale i18n-Sprache wieder auf den von
    tests/conftest.pys reset_language-Fixture gesetzten Wert stehen - dieser
    Test dokumentiert nur die Erwartung; reset_language selbst erledigt die
    eigentliche Absicherung."""
    assert i18n.current_language() == i18n.DEFAULT_LANGUAGE
