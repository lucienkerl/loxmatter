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

from loxmatter.api.live import BEARER_SUBPROTOCOL
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
    """Spec 8.2: Inbetriebnahme- und Diagnosewerkzeug, keine Smart-Home-Oberflaeche.

    Prueft nicht nur `index.html`, sondern auch `/static/app.js` (Review-Fix
    Minor #3, 2026-09-02): die vier Woerter sind heute in keiner der beiden
    Dateien vorhanden, das war also bislang kein falsches Gruen - aber ein
    kuenftiges Feature, dessen deutsche Texte nur in JavaScript entstehen
    (z. B. dynamisch zusammengesetzt statt im Markup), zoege sonst an dieser
    Sperre vorbei, ohne dass sie es je bemerkt."""
    client, _, _ = api
    page = (await client.get("/")).text.lower()
    script = (await client.get("/static/app.js")).text.lower()
    for absent in ("szene", "zeitplan", "automatisierung", "favorit"):
        assert absent not in page
        assert absent not in script


async def test_static_files_do_not_escape_their_directory(api):
    client, _, _ = api
    response = await client.get("/static/../../../etc/passwd")
    assert response.status_code in (404, 400)


# ---------------------------------------------------------------------------
# Das API-Token in der Oberflaeche (Review-Fix Fix 1, 2026-09-03). Ohne
# Browser laesst sich hier nicht klicken - pruefbar ist aber, dass die
# ausgelieferten Dateien die Eigenschaften tragen, ohne die die Bedienung
# nachweislich nicht funktionieren KANN.
# ---------------------------------------------------------------------------


async def test_the_interface_offers_a_field_to_enter_the_token(api):
    """Ohne Eingabemoeglichkeit sperrt ein gesetztes Token den Betreiber aus
    seiner eigenen Oberflaeche aus - genau der gemeldete Fehler. Typ
    `password`, damit es nicht ueber der Schulter mitlesbar ist."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert 'type="password"' in page
    assert "Token" in page


async def test_no_plain_link_points_at_a_token_protected_route(api):
    """Ein `<a href>` kann keinen `Authorization`-Header tragen: bei
    gesetztem Token wuerde ein Klick darauf die Seite durch die rohe
    401-Antwort ersetzen. Jeder Download unter `/api` muss deshalb ueber
    `fetch()` laufen (siehe `requestDownload` in app.js)."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert 'href="/api' not in page


async def test_the_token_never_travels_in_a_url(api):
    """Ein Token als Query-Parameter landet in Server-Logs, Proxy-Logs und
    der Browser-History - deshalb Header bzw. WebSocket-Subprotokoll (siehe
    `loxone.server.build_api_guard` und `api.diagnostics`s Moduldocstring
    zur selben Ueberlegung beim Kommando-Log)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "Authorization" in script
    for forbidden in ("?token=", "&token=", "?api_token=", "&api_token="):
        assert forbidden not in script


async def test_the_browser_and_the_server_agree_on_the_websocket_bearer_marker(api):
    """Die beiden Seiten des Subprotokoll-Wegs stehen in verschiedenen
    Dateien und verschiedenen Sprachen - waere der Marker auf einer Seite
    ein anderer, schluege der Handshake bei gesetztem Token fehl, und keine
    der beiden Dateien saehe fuer sich genommen falsch aus."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert f'"{BEARER_SUBPROTOCOL}"' in script
