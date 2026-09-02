"""Tests fuer die Auslieferung der WebUI (Task 7, Phase 5) - siehe
`loxone/server.py` (Routen `/` und `/static`) und `web/` (die eigentliche
Oberflaeche).

`api` folgt demselben Muster wie in `test_diagnostics.py`: eine Testdatei,
eine lokale `api`-Fixture, aufgebaut aus den gemeinsamen Bausteinen in
`conftest.py` (`no_invoke`, `fake_runtime`, `fake_client`). Diese Tests
brauchen kein Geraet im Store - die Oberflaeche wird ausgeliefert, bevor
ueberhaupt ein Klick passiert -, bauen aber trotzdem eines auf, damit ein
spaeterer Test in dieser Datei (z. B. eine Stichprobe auf `/api/devices`)
ohne eine zweite Fixture auskommt.
"""

from __future__ import annotations

import httpx2 as httpx
import pytest
from conftest import load_snapshot

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime, fake_client):
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(store, no_invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store, device_id
    store.close()


async def test_root_serves_the_interface(api):
    client, _, _ = api
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_alpine_is_served_locally_not_from_a_cdn(api):
    """Die Bruecke laeuft in Installationen ohne Internet."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert "cdn." not in page
    assert "unpkg" not in page
    assert (await client.get("/static/vendor/alpine.min.js")).status_code == 200


async def test_the_page_names_all_four_views(api):
    client, _, _ = api
    page = (await client.get("/")).text
    for view in ("Geräte", "Signale", "Export", "System"):
        assert view in page


async def test_the_page_does_not_promise_what_the_spec_excludes(api):
    """Spec 8.2: Inbetriebnahme- und Diagnosewerkzeug, keine Smart-Home-Oberflaeche."""
    client, _, _ = api
    page = (await client.get("/")).text.lower()
    for absent in ("szene", "zeitplan", "automatisierung", "favorit"):
        assert absent not in page


async def test_static_files_do_not_escape_their_directory(api):
    client, _, _ = api
    response = await client.get("/static/../../../etc/passwd")
    assert response.status_code in (404, 400)
