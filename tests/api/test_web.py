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

import re

import httpx2 as httpx
import pytest
from conftest import load_snapshot

from loxmatter.api.diagnostics import FABRIC_BACKUP_NAME
from loxmatter.api.export import ARCHIVE_NAME
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


def _without_comments(markup: str) -> str:
    """Die Seite ohne ihre HTML-Kommentare.

    Die Kommentare in `index.html` sind ausfuehrlich und nennen Attribute
    und Beschriftungen beim Namen - unter anderem, um zu begruenden, warum
    sie dort NICHT stehen. Eine Suche ueber die rohe Datei findet deshalb
    auch das, wovor der Kommentar gerade warnt."""
    return re.sub(r"<!--.*?-->", "", markup, flags=re.DOTALL)


def _label_around(markup: str, needle: str) -> str:
    """Das `<label>`-Element, das `needle` enthaelt - die Beschriftung, die
    neben einem Eingabefeld tatsaechlich auf dem Bildschirm steht."""
    position = markup.index(needle)
    start = markup.rindex("<label", 0, position)
    end = markup.index("</label", position)
    return markup[start:end]


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
# Einrichtung und Login statt eines Token-Feldes (Task 7, WebUI-Login). Ohne
# Browser laesst sich hier nicht klicken - pruefbar ist aber, dass die
# ausgelieferten Dateien die Eigenschaften tragen, ohne die die Bedienung
# nachweislich nicht funktionieren KANN.
# ---------------------------------------------------------------------------


async def test_the_interface_offers_setup_and_login_instead_of_a_token_field(api):
    """Nachfolger von `test_the_interface_offers_a_field_to_enter_the_token`
    (Review-Fix Fix 1). Beide Bildschirme stehen unbedingt im ausgelieferten
    Markup - Alpine blendet sie erst im Browser per `x-if`/`x-show` ein oder
    aus, ein Test ohne Browser-Engine sieht deshalb immer beide. Geprueft
    wird: drei Passwortfelder vom Typ `password` (zwei fuer die Einrichtung,
    eins fuer den Login - Typ `password`, damit nichts ueber der Schulter
    mitlesbar ist), die beiden Absende-Beschriftungen, und dass die alte
    Token-Eingabe verschwunden ist."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert page.count('type="password"') == 3
    assert "Passwort vergeben" in page
    assert "Anmelden" in page
    assert "token-box" not in page
    assert "token-input" not in page


async def test_no_plain_link_points_at_a_token_protected_route(api):
    """Ein `<a href>` haette bei jeder Fehlerantwort (heute z. B. eine 401
    nach abgelaufener Sitzung) die Seite durch deren rohen Text ersetzt.
    Jeder Download unter `/api` muss deshalb ueber `fetch()` laufen (siehe
    `requestDownload` in app.js)."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert 'href="/api' not in page


async def test_no_secret_travels_in_a_url_or_local_storage(api):
    """Nachfolger von `test_the_token_never_travels_in_a_url`: seit dem
    WebUI-Login (Task 7) gibt es kein Token mehr, das ueber den Browser
    haette lecken koennen - das Passwort geht ausschliesslich im Rumpf eines
    POST an `/auth/setup` bzw. `/auth/login`, die Sitzung ausschliesslich als
    `HttpOnly`-Cookie, das dieses Skript nie anfasst. Belegt wird, dass beide
    frueheren Wege dafuer aus der Oberflaeche verschwunden sind: kein
    `Bearer`-Header mehr (ein Kommentar in `requestJson` nennt das Wort
    `Authorization` zwar noch beim Erklaeren, WARUM es fehlt - das ist kein
    falsches Gruen, das hier bewusst nicht mitgeprueft wird), kein
    `localStorage`, kein Geheimnis in einer URL."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "Bearer" not in script
    assert "localStorage" not in script
    for forbidden in (
        "?token=",
        "&token=",
        "?api_token=",
        "&api_token=",
        "?password=",
        "&password=",
    ):
        assert forbidden not in script


async def test_the_browser_and_the_server_agree_on_the_download_filenames(api):
    """Seit die beiden Downloads ueber `fetch` statt ueber einen Link laufen,
    vergibt der Browser den Dateinamen selbst - der Server schickt seinen
    trotzdem weiter mit. Zwei Namen fuer dieselbe Datei an zwei Orten waeren
    fuer sich genommen beide plausibel; ein Auseinanderlaufen faellt erst
    dem Anwender auf, der die falsch benannte Datei in der Hand haelt."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert f'"{ARCHIVE_NAME}"' in script
    assert f'"{FABRIC_BACKUP_NAME}"' in script


async def test_the_page_declares_a_doctype(api):
    """Ohne `<!doctype html>` rendert jeder Browser die Seite im
    Quirks-Modus (`document.compatMode === "BackCompat"`) - einem
    Kompatibilitaetsmodus fuer Seiten aus den Neunzigern, in dem unter
    anderem das Boxmodell und die Prozenthoehen anders rechnen als in jeder
    Vorgabe von `style.css`. Belegt wird hier nur, dass die Deklaration
    ausgeliefert wird; ob das Layout dadurch anders aussieht, kann ohne
    Browser-Engine kein Test dieser Suite sagen."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert page.lower().startswith("<!doctype html>")


async def test_the_page_does_not_call_init_a_second_time(api):
    """Alpine 3 ruft `init()` eines `x-data`-Objekts von sich aus auf. Ein
    zusaetzliches `x-init="init()"` auf demselben Element ruft es ein
    zweites Mal - und `init()` startet nach einer angemeldeten Sitzung den
    Live-WebSocket: jeder offene Tab hielt so zwei Verbindungen, von denen
    nur die zuletzt geoeffnete in `this.socket` landete; die andere blieb
    unsichtbar und lief bis zum Schliessen des Tabs weiter.

    **Was dieser Test belegt und was nicht.** Er belegt, dass die
    ausgelieferte Seite `init()` nicht ausdruecklich ein zweites Mal
    aufruft. Er belegt NICHT, dass ein echter Seitenaufruf am Ende genau
    einen Beobachter hinterlaesst - dafuer braeuchte es eine Browser-Engine,
    die Alpine tatsaechlich ausfuehrt, und die gibt es in dieser Suite
    nicht (`Runtime.observer_count()` nach einem simulierten Aufruf waere
    das direkte Mass gewesen). Ein zweiter Aufruf auf einem anderen Weg -
    ein `x-init` auf einem verschachtelten Element, ein `Alpine.start()` von
    Hand, ein zweites `x-data="app()"` - liefe an dieser Sperre vorbei."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert 'x-data="app()"' in markup
    assert "x-init" not in markup


async def test_the_signal_view_ships_a_functional_and_an_expert_block(api):
    """Aufgabe 8: die Signalliste soll sich in „Funktional“ (offen) und
    „Experte“ (zugeklappt, mit Anzahl, plus Schalter) gliedern, statt alle
    159 Signale eines Geraets flach untereinander zu zeigen.

    **Was dieser Test belegt und was nicht.** Belegt wird nur, dass die
    ausgelieferten Dateien (`index.html`, `app.js`) die dafuer noetigen
    Bausteine enthalten: beide Ueberschriften, den Schaltertext und - im
    Skript - dass beide Listen tatsaechlich ueber `signal.functional`
    unterschieden werden statt ueber eine zweite, in JavaScript
    nachgebaute Relevanz-Regel. NICHT belegt wird, dass Alpine daraus zur
    Laufzeit tatsaechlich zwei getrennte, korrekt gefilterte Bloecke
    macht, dass der Schalter beim Klicken etwas umschaltet, oder dass die
    Gliederung fuer ein echtes Geraet richtig aussieht - dafuer braeuchte
    es eine Browser-Engine, die es in dieser Suite nicht gibt (siehe
    `test_the_page_does_not_call_init_a_second_time` oben)."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert "Funktional" in page
    assert "Experte" in page
    assert "Experten-Signale anzeigen" in page
    # Beide Listen lesen nur das von der API mitgelieferte Feld, keine
    # eigene JavaScript-Fassung von `profiles.relevance.is_functional`.
    assert "signal.functional" in script


async def test_the_device_tile_no_longer_promises_a_ranking_it_does_not_have(api):
    """Review-Fix Fix 9 (2026-09-03) hatte die Ueberschrift „Wichtigste
    Werte“ absichtlich in „Signale (Anfang der Liste)“ umbenannt, weil die
    gezeigten Signale damals nur nach `exportable` gefiltert waren - bei
    der Testvorlage NetworkCommissioning und BasicInformation statt Ein/Aus
    und Leistung. Seit `signal.functional` das echte Auswahlkriterium
    mitliefert, ist die alte, ehrlichere Formulierung wieder zutreffend.

    Belegt nur, dass die neue Beschriftung ausgeliefert wird und die alte
    verschwunden ist - nicht, dass die damit beworbenen Signale zur
    Laufzeit tatsaechlich die funktionalen sind (siehe Testdocstring
    oben)."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert "Funktionale Signale" in page
    assert "Signale (Anfang der Liste)" not in page


async def test_the_export_preview_shows_how_many_signals_are_held_back(api):
    """Nachbesserung Fix 3 (Abschlussreview): `hidden_count` kam schon vorher
    aus `GET /api/export/preview`, aber nirgends in der Oberflaeche an - die
    Vorschautabelle hatte Spalten fuer Eingaenge, Befehle und Uebersprungen,
    keine fuer als Experte zurueckgehaltene Signale. Belegt wie die
    uebrigen Markup-Tests in dieser Datei nur, dass die ausgelieferte Seite
    die Spalte und ihre Bindung an `device.hidden_count` enthaelt - nicht,
    dass Alpine sie zur Laufzeit korrekt befuellt (dafuer braeuchte es eine
    Browser-Engine, siehe `test_the_page_does_not_call_init_a_second_time`)."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert "Als Experte zurückgehalten" in page
    assert 'x-text="device.hidden_count"' in page


async def test_the_export_field_asks_for_the_bridge_not_the_miniserver(api):
    """Der Wert dieses Feldes wird zur `Address` des virtuellen
    UDP-Eingangs und zum Rumpf der Kommando-URLs (`http://<ip>:<listen>`) -
    beides die Adresse DIESER Bruecke, nie die des Miniservers. War das Feld
    mit "Miniserver-IP" beschriftet, trug der Anwender folgerichtig die
    falsche der beiden Adressen ein und bekam Vorlagen, die richtig aussehen
    und stumm bleiben: die Kommandos gingen an den Miniserver selbst zurueck,
    und dessen Adressfilter verwarf die Datagramme der Bruecke - ohne
    Fehlermeldung, genau der Fehlschlagtyp, den Spec 8.1 ausschliessen
    will."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    label = _label_around(markup, 'x-model="exportBridgeIp"')
    assert "Miniserver" not in label, label
    assert "IP dieser Brücke" in label, label
