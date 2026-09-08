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

"""Tests fuer GET /api/version.

Die `api`-Fixture folgt demselben Muster wie in `test_language.py`: eine
lokale, bereits ANGEMELDETE Fixture. `unauthenticated_api` daneben belegt,
dass diese Route KEINE der drei bewussten Ausnahmen von der
Anmeldepflicht ist (`/cmd`, `/resync`, `GET /api/i18n`)."""

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


async def test_im_entwicklungscheckout_antwortet_sie_trotzdem(api, monkeypatch):
    """Kein 500, wenn die Variablen fehlen - sonst waere die Oberflaeche
    ausserhalb von Docker unbenutzbar."""
    for name in ("LOXMATTER_VERSION", "LOXMATTER_COMMIT", "LOXMATTER_BUILT_AT"):
        monkeypatch.delenv(name, raising=False)
    response = await api.get("/api/version")
    assert response.status_code == 200
    assert response.json()["version"] == "dev"
    assert response.json()["commit"] is None


async def test_ohne_sitzung_kein_zugriff(unauthenticated_api):
    response = await unauthenticated_api.get("/api/version")
    assert response.status_code == 401


async def test_die_oberflaeche_kennt_alle_texte_der_versionskarte():
    """Ein fehlender Schluessel faellt sonst erst im Browser auf - als
    leeres Feld, nicht als Fehler. Die Liste hier ist die Verbindung
    zwischen index.html und strings.yaml, die sonst niemand prueft."""
    from loxmatter import i18n

    for key in (
        "web.system.version_heading",
        "web.system.version_running",
        "web.system.version_commit",
        "web.system.version_built_at",
        "web.system.version_dev_hint",
    ):
        assert i18n.raw_template(key)
        assert key in i18n.strings_with_prefix("web.")
