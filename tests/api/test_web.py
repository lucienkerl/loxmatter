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

import json
import re
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import httpx2 as httpx
import pytest
from conftest import load_snapshot

from loxmatter.api.diagnostics import FABRIC_BACKUP_NAME
from loxmatter.api.export import ARCHIVE_NAME
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store

WEB_DIR = Path(__file__).resolve().parents[2] / "src" / "loxmatter" / "web"


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
    """Aufgabe 10 bindet die Reiterleiste an `t('web.nav.*')` (Aufgabe 9) statt
    die deutschen Namen fest ins Markup zu schreiben - der ausgelieferte
    Quelltext traegt deshalb keinen der alten Literale mehr, sondern die
    fuenf `x-text`-Bindungen (die Uebersetzung selbst passiert erst zur
    Laufzeit im Browser, siehe `test_load_i18n_...` in Aufgabe 8)."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert ">Geräte<" not in page
    for key in ("devices", "export", "system", "settings"):
        assert f"x-text=\"t('web.nav.{key}')\"" in page


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


async def test_the_page_carries_an_icon_that_is_actually_ausgeliefert(api):
    """Ein `link rel="icon"` ins Leere faellt niemandem auf - der Browser zeigt
    dann still sein Standardblatt. Deshalb hier beides in einem Test: dass die
    Seite das Icon nennt UND dass unter dem genannten Pfad etwas liegt."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert 'href="/static/favicon.svg"' in page
    response = await client.get("/static/favicon.svg")
    assert response.status_code == 200
    assert "svg" in response.headers["content-type"]


async def test_the_mark_stands_in_the_interface_in_both_its_sizes(api):
    """Das Zeichen sass bislang nur im Browser-Tab, nicht in der Oberflaeche.

    Geprueft wird die Groessenregel, die der Kopfkommentar in `icon.svg`
    aufstellt: die grosse Fassung gilt "ab etwa 32 px", darunter greift
    `favicon.svg` mit kraeftigeren Strichen, weil die sechs Aussenpunkte sonst
    ineinanderfliessen. Die Kopfzeile (24 px) muss deshalb `favicon.svg`
    nennen, die beiden Anmeldebildschirme (64 px) `icon.svg` - ein vertauschtes
    Paar sieht im Test wie im Browser gleich "richtig" aus und faellt nur bei
    genauem Hinsehen auf.

    `alt=""` gehoert mitgeprueft: "loxmatter" steht in allen drei
    Ueberschriften direkt daneben, ein gefuellter Alt-Text liesse einen
    Screenreader den Namen zweimal vorlesen."""
    client, _, _ = api
    page = (await client.get("/")).text

    assert '<img class="brand-mark" src="/static/favicon.svg" alt=""' in page
    # Zweimal: einmal ueber der Anmeldung, einmal ueber der Ersteinrichtung.
    assert page.count('<img class="auth-mark" src="/static/icon.svg" alt=""') == 2

    for name in ("icon.svg", "favicon.svg"):
        response = await client.get(f"/static/{name}")
        assert response.status_code == 200
        assert "svg" in response.headers["content-type"]


def test_the_icons_are_well_formed_xml():
    """Ein SVG, das nicht als XML parst, zeigt KEIN Browser an - er blendet es
    still als kaputtes Bild aus, ohne Meldung irgendwo.

    Genau das ist beim ersten Anlauf passiert: der Kopfkommentar in icon.svg
    nannte die Akzentfarbe `--accent` beim CSS-Namen, und zwei aufeinander-
    folgende Bindestriche sind in einem XML-Kommentar verboten. Die Datei war
    auf GitHub und im Browser-Tab gleichermassen unsichtbar. Ein Blick in die
    Datei verraet das nicht, ein Parser schon."""
    for name in ("icon.svg", "favicon.svg"):
        ElementTree.parse(WEB_DIR / name)


async def test_the_inline_icon_symbols_are_well_formed_xml(api):
    """Derselbe Befund wie oben, aber fuer das inline `<svg style="display:
    none">` in `index.html` statt fuer die beiden Einzeldateien - dort
    parst bislang niemand mit. Dabei gilt fuer ein `<symbol>` genau dasselbe
    wie fuer eine eigene SVG-Datei: ein `<use xlink:href="#i-...">`, das auf
    ein Symbol zeigt, dessen Markup nicht als XML durchgeht, zeichnet
    STILLSCHWEIGEND nichts - keine Fehlermeldung in der Konsole, nur eine
    Kachel ohne Icon, siehe der Kommentar zu `i-cat-other` in `index.html`.

    Der Block traegt inzwischen sechzehn `<symbol>`-Definitionen, acht davon
    aus dem Geraete-Tab-Umbau (Entwurf 2026-09-05, Abschnitt 6.5) - keine
    davon war bislang durch einen Parser gelaufen. Ein einzelner falscher
    Bindestrich oder ein nicht geschlossenes Tag in einem neuen Symbol waere
    also erst im Browser aufgefallen, und selbst dort nur als leere Flaeche,
    nie als Meldung."""
    client, _, _ = api
    page = (await client.get("/")).text
    match = re.search(r'<svg style="display: none".*?</svg>', page, flags=re.DOTALL)
    assert match, "inline SVG-Symbolblock nicht gefunden"
    ElementTree.fromstring(match.group(0))


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
    assert "x-text=\"t('web.auth.setup_submit')\"" in page
    assert "x-text=\"t('web.auth.login_submit')\"" in page
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


async def test_reconnecting_the_diagnostics_channel_clears_the_three_buffers_first(api):
    """Nachbesserung Task 6 (2026-09-03): jede (Wieder-)Verbindung des
    Diagnose-Kanals (`/api/diagnostics/live`) bekommt vom Server eine
    Momentaufnahme von bis zu `SNAPSHOT_LIMIT` Eintraegen je Strom, in
    GENAU derselben Nachrichtenform wie eine laufende Zeile - ohne
    Kennzeichnung als Momentaufnahme (siehe `api/diagnostics_live.py`).
    Ohne ein Leeren der drei gehaltenen Straeme VOR jedem (Wieder-)Aufbau
    haengte sich diese Momentaufnahme einfach an das bereits Gehaltene an:
    ein Wechsel weg von "System" und zurueck, oder jede automatische
    Wiederverbindung nach einem Netzhaenger, haette bis zu 150 bereits
    vorhandene Zeilen ein zweites Mal angehaengt.

    **Was dieser Test belegt und was nicht.** Ohne Browser-Engine laesst sich
    hier nicht ausfuehren, dass `connectDiagnosticsLive()` zur Laufzeit
    tatsaechlich `this.datagrams`/`this.commandLog`/`this.diagnosticsLogs`
    leert, oder dass ein Wechsel der Ansicht diese Funktion ueberhaupt
    aufruft. Belegt wird nur, dass der AUSGELIEFERTE Quelltext innerhalb des
    Rumpfs von `connectDiagnosticsLive()` `clearDiagnosticsBuffers()` ruft -
    und zwar VOR dem Aufbau des neuen `WebSocket`, nicht erst danach (sonst
    liefe die Momentaufnahme der alten Verbindung dem Leeren noch in die
    Quere)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    start = script.index("connectDiagnosticsLive() {")
    end = script.index("disconnectDiagnosticsLive() {", start)
    body = script[start:end]

    assert "clearDiagnosticsBuffers()" in body
    assert body.index("clearDiagnosticsBuffers()") < body.index("new WebSocket(")


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

    **Was dieser Test belegt und was nicht.** Er belegt, dass keiner der
    ausgelieferten `x-init`-Ausdruecke `init()` aufruft. Er belegt NICHT,
    dass ein echter Seitenaufruf am Ende genau einen Beobachter
    hinterlaesst - dafuer braeuchte es eine Browser-Engine, die Alpine
    tatsaechlich ausfuehrt, und die gibt es in dieser Suite nicht
    (`Runtime.observer_count()` nach einem simulierten Aufruf waere das
    direkte Mass gewesen). Ein zweiter Aufruf auf einem anderen Weg - ein
    `Alpine.start()` von Hand, ein zweites `x-data="app()"` - liefe an
    dieser Sperre vorbei.

    2026-09-05/06: die Signalliste im Geraete-Modal nutzt seither selbst
    `x-init`, um den Anfangszustand ihrer beiden `<details>`-Gruppen zu
    setzen (siehe `index.html`), ohne mit dem hier bewachten Fehler etwas
    zu tun zu haben. Die Sperre prueft daher seither nicht mehr, ob
    `x-init` ueberhaupt vorkommt, sondern nur noch, ob einer seiner
    Ausdruecke `init(` aufruft - fuer den bewachten Fehler ist das
    mindestens so scharf wie vorher: ein `x-init="init()"` auf einem
    verschachtelten Element, das die alte pauschale Pruefung nur zufaellig
    mit erfasste, faellt der neuen absichtlich auf.

    Der Regex erfasst `x-init="..."` UND `x-init='...'` - alle Fundstellen
    in dieser Codebasis sind heute doppelt zitiert, aber die Pruefung soll
    nicht stillschweigend an einer einfach zitierten Fundstelle vorbeilaufen
    (Fund 2, Review der Nacharbeit, 2026-09-06).

    Die Schleife allein prueft nichts, wenn der Regex ins Leere trifft -
    faende eine kuenftige `x-init`-Schreibweise (andere Anfuehrung, anderes
    Attribut-Format) keinen Treffer mehr, liefe die Sperre stillschweigend
    leer statt fehlzuschlagen. Der zusaetzliche `assert` unten haelt die
    Sperre scharf, indem er mindestens einen Treffer verlangt (Fund 8,
    Review der Nacharbeit, 2026-09-06)."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert 'x-data="app()"' in markup
    expressions = re.findall(r"x-init=[\"']([^\"']*)[\"']", markup)
    assert expressions, "kein x-init in der ausgelieferten Seite gefunden"
    for expression in expressions:
        assert "init(" not in expression, f"x-init ruft init() auf: {expression}"


async def test_the_signal_view_ships_a_functional_and_an_expert_block(api):
    """Aufgabe 8: die Signalliste soll sich in „Funktional“ (offen) und
    „Experte“ (zugeklappt, mit Anzahl) gliedern, statt alle 159 Signale
    eines Geraets flach untereinander zu zeigen.

    **Was dieser Test belegt und was nicht.** Belegt wird nur, dass die
    ausgelieferten Dateien (`index.html`, `app.js`) die dafuer noetigen
    Bausteine enthalten: beide Ueberschriften und - im Skript - dass beide
    Listen tatsaechlich ueber `signal.functional` unterschieden werden
    statt ueber eine zweite, in JavaScript nachgebaute Relevanz-Regel.
    NICHT belegt wird, dass Alpine daraus zur Laufzeit tatsaechlich zwei
    getrennte, korrekt gefilterte Bloecke macht, oder dass die Gliederung
    fuer ein echtes Geraet richtig aussieht - dafuer braeuchte es eine
    Browser-Engine, die es in dieser Suite nicht gibt (siehe
    `test_the_page_does_not_call_init_a_second_time` oben).

    Aufgabe 12: die beiden Gruppentitel tragen seither `t(...)` statt
    fester deutscher Literale - siehe
    `test_the_signal_group_titles_are_translated` fuer die Bindung selbst;
    hier bleibt nur der Beleg, dass die Gruppierung (`signal.functional`)
    unveraendert ist.

    Aufgabe 6: die eine „Funktional"-Gruppe wich Endpunkt-Gruppen (siehe
    `test_the_groups_follow_the_ranking_not_the_endpoint_number` fuer deren
    Reihenfolge und Inhalt). Der Schluessel `group_functional` gibt es
    seither nicht mehr; die urspruengliche Zusicherung dieses Tests -
    Gruppentitel ueber `t(...)` statt fest verdrahtet - bleibt gueltig,
    zielt jetzt aber auf den Endpunkt-Untertitel."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert 't("web.signals.group_endpoint_subtitle"' in script
    assert 't("web.signals.group_expert")' in script
    # Beide Listen lesen nur das von der API mitgelieferte Feld, keine
    # eigene JavaScript-Fassung von `profiles.relevance.is_functional`.
    assert "signal.functional" in script


async def test_the_signal_row_offers_a_resend_checkbox(api):
    """Periodischer Resend als Opt-in (Entwurf 2026-09-04) - dieselbe Art
    Beleg wie beim Funktional/Experte-Test oben: nur, dass die Bausteine
    ausgeliefert werden und `signal.resend` lesen/schreiben, nicht dass
    Alpine sie zur Laufzeit korrekt rendert (siehe dortiger Docstring).

    Zusaetzlich (finaler Review, Important #3): das umschliessende `<label>`
    der Checkbox selbst muss dasselbe `x-show="signal.exportable"` tragen
    wie das „exportieren“-Label direkt darueber - sonst bleibt die Checkbox
    auch fuer nicht-exportierbare Signale sichtbar, obwohl `resend_marked()`
    dort (`_last_values` bleibt fuer sie leer, siehe `Runtime._cache_attribute`)
    nie etwas bewirken kann. Der Substring-Test allein wuerde das nicht
    belegen - `x-show="signal.exportable"` steht bereits beim „exportieren“-
    Label - deshalb wird hier gezielt das `<label>` extrahiert, das die
    Resend-Checkbox umschliesst, und NUR darin nach dem Guard gesucht."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    page = (await client.get("/")).text
    assert "toggleResend" in script
    assert "signal.resend" in page

    resend_idx = page.index("signal.resend")
    label_start = page.rindex("<label", 0, resend_idx)
    label_end = page.index("</label>", resend_idx) + len("</label>")
    resend_label = page[label_start:label_end]
    assert 'x-show="signal.exportable"' in resend_label


async def test_the_settings_view_offers_a_resend_interval_field(api):
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert "resendIntervalDraft" in page
    assert "saveResendInterval" in script


async def test_the_device_tile_no_longer_promises_a_ranking_it_does_not_have(api):
    """Review-Fix Fix 9 (2026-09-03) hatte die Ueberschrift „Wichtigste
    Werte“ absichtlich in „Signale (Anfang der Liste)“ umbenannt, weil die
    gezeigten Signale damals nur nach `exportable` gefiltert waren - bei
    der Testvorlage NetworkCommissioning und BasicInformation statt Ein/Aus
    und Leistung. Seit `signal.functional` das echte Auswahlkriterium
    mitliefert, ist die alte, ehrlichere Formulierung wieder zutreffend.

    Task 8 (Raster-Umbau, 2026-09-05) hat die eigene Werte-Ueberschrift
    danach ganz entfernt: die Kachel zeigt den Leitwert jetzt in der
    Kopfzeile und den Rest als fluchtendes Raster ohne Abschnittstitel -
    eine Ueberschrift ueber der einzigen Werteliste einer sonst schon
    kompakten Kachel waere reiner Platzverbrauch gewesen. Der Schluessel
    `web.devices.values_heading` ist deshalb (Task 9) aus `strings.yaml`
    entfernt und taucht im ausgelieferten Markup nicht mehr auf. Die
    urspruengliche Sorge des
    Tests - eine Ueberschrift, die mehr verspricht als die Kachel haelt -
    bleibt trotzdem gueltig zu pruefen: die beiden ueberholten
    Formulierungen duerfen nirgends mehr auftauchen."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert "x-text=\"t('web.devices.values_heading')\"" not in page
    assert "Signale (Anfang der Liste)" not in page
    assert "Funktionale Signale" not in page


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
    assert "x-text=\"t('web.export.col_expert_withheld')\"" in page
    assert 'x-text="device.hidden_count"' in page


# ---------------------------------------------------------------------------
# Live-Diagnose (Aufgabe 6, Spec 10.5): die Ansicht „System" holt Logs,
# UDP-Mitschnitt und Kommando-Log seither laufend ueber
# `/api/diagnostics/live` statt einmalig per GET. Wie bei den uebrigen
# Markup-Tests dieser Datei gilt: ohne Browser-Engine ist nur nachweisbar,
# dass etwas ausgeliefert wird - nicht, dass es zur Laufzeit funktioniert.
# ---------------------------------------------------------------------------


async def test_the_system_view_connects_to_the_diagnostics_live_socket(api):
    """Aufgabe 6, Schritt 1+2: der Diagnose-Kanal folgt demselben Muster wie
    `connectLive()` fuer den Wertekanal, oeffnet aber nur beim Wechsel auf
    „System" und schliesst beim Verlassen - `selectView` (app.js) ist dafuer
    die einzige Stelle.

    **Was dieser Test belegt und was nicht.** Belegt wird, dass `app.js`
    tatsaechlich `/api/diagnostics/live` anspricht und dass `selectView`
    sowohl den oeffnenden als auch den schliessenden Aufruf enthaelt. NICHT
    belegt wird, dass ein echter Seitenaufruf am Ende genau eine Verbindung
    haelt, dass sie beim Verlassen der Ansicht tatsaechlich schliesst, oder
    dass keine Wiederverbindung mehr geplant wird, nachdem sie geschlossen
    wurde - dafuer braeuchte es eine Browser-Engine, die es in dieser Suite
    nicht gibt (siehe `test_the_page_does_not_call_init_a_second_time`)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "/api/diagnostics/live" in script
    select_view_start = script.index("async selectView(view)")
    select_view_body = script[select_view_start : select_view_start + 800]
    assert "connectDiagnosticsLive()" in select_view_body
    assert "disconnectDiagnosticsLive()" in select_view_body


async def test_the_system_view_offers_the_four_diagnostics_controls(api):
    """Aufgabenstellung, Schritt 3: „Drei Bereiche, darüber die vier
    Bedienelemente aus dem Entwurf" - Pause/Fortsetzen, der Rauschfilter,
    die Log-Stufe und eine Möglichkeit, die gehaltenen Zeilen zu leeren.

    Belegt nur, dass Markup und Skript die vier Bindungen tragen - nicht,
    dass ein Klick im Browser tatsaechlich etwas umschaltet (siehe
    Testdocstring oben).

    Seit Aufgabe 14 tragen die Beschriftungen `t(...)`-Aufrufe statt fester
    deutscher Literale - hier wird nur noch belegt, dass die Bindungen
    selbst (Attribute, Handler, Vorgabewerte) unveraendert sind."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert 'x-model="hideNoise"' in page
    assert "x-text=\"t('web.system.hide_noise')\"" in page
    assert 'x-model="logLevel"' in page
    assert "x-text=\"t('web.system.log_level_label')\"" in page
    assert "diagnosticsPaused = !diagnosticsPaused" in page
    assert "x-text=\"diagnosticsPaused ? t('web.system.resume') : t('web.system.pause')\"" in page
    assert "clearDiagnosticsBuffers()" in page
    assert "x-text=\"t('web.system.clear')\"" in page
    # Vorgaben aus der Aufgabenstellung (Schritt 2): Filter aus, Log-Stufe
    # "INFO".
    assert "hideNoise: true" in script
    assert 'logLevel: "INFO"' in script


async def test_the_diagnostics_filter_only_affects_display_not_held_lines(api):
    """Entwurf 4 (Aufgabenstellung): ein Filter darf nur die Anzeige
    betreffen, nicht die gehaltenen Zeilen - wer ihn ausschaltet, muss die
    vorhandenen Zeilen sofort sehen, nicht auf neue warten. Umgesetzt als
    zwei getrennte Dinge in `app.js`: `datagrams`/`diagnosticsLogs` halten
    JEDE eingetroffene Zeile, `visibleDatagrams()`/`visibleDiagnosticsLogs()`
    filtern erst beim Anzeigen daraus.

    Belegt wird, dass das Markup tatsaechlich an die filternden Funktionen
    bindet statt an die rohen Listen, und dass diese Funktionen die rohen
    Listen unveraendert lassen (kein `datagrams =`/`diagnosticsLogs =`
    innerhalb ihres Rumpfs). NICHT belegt wird, dass ein Umschalten im
    Browser die Anzeige tatsaechlich ohne Verzoegerung aktualisiert - dafuer
    braeuchte es eine Browser-Engine."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert "in visibleDatagrams()" in page
    assert "in visibleDiagnosticsLogs()" in page
    for name in ("visibleDatagrams", "visibleDiagnosticsLogs"):
        start = script.index(f"    {name}(")
        body = script[start : script.index("},", start)]
        assert "datagrams =" not in body
        assert "diagnosticsLogs =" not in body


async def test_the_noise_rule_is_written_down(api):
    """Die Aufgabenstellung ueberlaesst bewusst, woran „Rauschen" (der
    Heartbeat und ein Full-Resend) erkannt wird - verlangt aber, dass die
    gewaehlte Regel als Kommentar nachlesbar ist, nicht nur implizit im Code
    steckt: „ein Filter, dessen Kriterium niemand nachlesen kann, ist beim
    naechsten Zweifel wertlos."

    Seit der Nachbesserung (Task 6, 2026-09-03) liegt das Kriterium NICHT
    mehr im Browser: eine fruehere Regel ueber die Ankunftsrate markierte
    jeden schnell aufeinanderfolgenden, aber echten Wertewechsel (z. B.
    Impuls und Zaehler aus `Runtime.on_event`) faelschlich als Rauschen -
    das gewaehlte Kriterium ist stattdessen das vom Server mitgeschickte
    `forced`-Feld (`DatagramLogEntry.forced`).

    Belegt nur, dass ein solcher Kommentar existiert und Feld, Quelle und
    die widerlegte fruehere Regel beim Namen nennt - nicht, dass die
    Unterscheidung zur Laufzeit korrekt zwischen Rauschen und echten
    Aenderungen trennt (siehe dafuer `tests/loxone/test_sender.py`,
    `test_the_forced_field_reflects_why_a_datagram_was_sent_not_when`, und
    `tests/api/test_diagnostics_live.py`,
    `test_a_datagram_message_carries_why_it_was_sent`)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "message.forced" in script
    assert "DatagramLogEntry.forced" in script
    assert "Schwall" in script
    assert "Full-Resend" in script


async def test_the_export_field_asks_for_the_bridge_not_the_miniserver(api):
    """Der Wert dieses Feldes wird zur `Address` des virtuellen
    UDP-Eingangs und zum Rumpf der Kommando-URLs (`http://<ip>:<listen>`) -
    beides die Adresse DIESER Bruecke, nie die des Miniservers. War das Feld
    mit "Miniserver-IP" beschriftet, trug der Anwender folgerichtig die
    falsche der beiden Adressen ein und bekam Vorlagen, die richtig aussehen
    und stumm bleiben: die Kommandos gingen an den Miniserver selbst zurueck,
    und dessen Adressfilter verwarf die Datagramme der Bruecke - ohne
    Fehlermeldung, genau der Fehlschlagtyp, den Spec 8.1 ausschliessen
    will.

    Zwei Stellen zeigen das Feld (Geraete-Dashboard-Entwurf, Abschnitt 4/5):
    editierbar in Einstellungen (`settingsDraft.bridge_ip`) - dort tippt
    jemand tatsaechlich hinein, dort waere eine falsche Beschriftung am
    teuersten - und schreibgeschuetzt im Export-Tab (`bridgeSettings.
    bridge_ip`), das denselben Wert nur noch anzeigt.

    Beide Stellen tragen inzwischen `t('web.bridge_ip_label')` - denselben
    Schluessel: der Export-Tab seit Aufgabe 13, die Einstellungen-Ansicht
    seit Aufgabe 15."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    editable_label = _label_around(markup, 'x-model="settingsDraft.bridge_ip')
    assert "Miniserver" not in editable_label, editable_label
    assert "x-text=\"t('web.bridge_ip_label')\"" in editable_label, editable_label
    assert "IP dieser Brücke" not in editable_label, editable_label

    readonly_label = _label_around(markup, ':value="bridgeSettings.bridge_ip')
    assert "Miniserver" not in readonly_label, readonly_label
    assert "x-text=\"t('web.bridge_ip_label')\"" in readonly_label, readonly_label
    assert "IP dieser Brücke" not in readonly_label, readonly_label


async def test_the_age_is_a_tooltip_and_the_change_is_a_highlight(api):
    """Die Altersangabe stand frueher neben dem Wert und aenderte jede
    Sekunde ihre Breite - das schob die Zeile hin und her und zog den Blick
    auf die Bewegung statt auf die Aenderung (2026-09-03).

    Jetzt traegt sie der `title` der Zelle, und die Aenderung zeigt eine
    Hervorhebung, die wieder verblasst. Belegt ist damit, dass beides
    ausgeliefert wird - NICHT, dass es im Browser so aussieht: in dieser
    Suite laeuft keine Engine, die CSS anwendet oder Alpine ausfuehrt.
    """
    client, _, _ = api
    page = (await client.get("/")).text
    assert "signalAgeTitle(signal)" in page
    assert "'value-fresh': signalIsFresh(signal)" in page
    # Nirgends mehr im Textfluss - das war die Ursache des Zappelns.
    assert 'x-text="signalSeenText(signal)"' not in page


async def test_the_highlight_cannot_change_the_width_of_a_cell(api):
    """Polster und Radius muessen am Grundzustand haengen, nicht an der
    Hervorhebung: kaemen sie mit ihr dazu, waere das Zappeln zurueck - nur
    an einer anderen Stelle."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    base = css.split(".value {", 1)[1].split("}", 1)[0]
    highlight = css.split(".value-fresh {", 1)[1].split("}", 1)[0]
    assert "padding" in base
    assert "padding" not in highlight
    assert "border-radius" in base
    assert "border-radius" not in highlight


# ---------------------------------------------------------------------------
# Ein Geraet ohne Leitsignal (Kachel-Kopfzeile).
#
# Die einzige Stelle dieser Suite, die `app.js` wirklich AUSFUEHRT, statt den
# ausgelieferten Text zu lesen. Der Grund: der Fehler, um den es hier geht,
# steckt nicht im Markup, sondern im Verhalten der drei Helfer - eine
# Textstichprobe auf `if (!signal)` wuerde auch dann gruen bleiben, wenn die
# Bedingung das Falsche tut. Alpine laeuft trotzdem nicht mit; gepruefte
# Einheit ist das von `app()` gelieferte Zustandsobjekt.
# ---------------------------------------------------------------------------

NODE = shutil.which("node")


def _app_state(setup: str = "") -> dict:
    """Laedt `app.js` in node, ruft `app()` und fuehrt `setup` darauf aus.

    `app.js` ist ein einfaches Skript ohne Modulsystem (bewusst, siehe Kopf
    der Datei) - deshalb `new Function` statt eines Imports.
    """
    script = f"""
      const fs = require("node:fs");
      const src = fs.readFileSync({str(WEB_DIR / "app.js")!r}, "utf8");
      const state = new Function(src + "\\nreturn app();")();
      {setup}
    """
    # `check=False`, weil die Zeile darunter denselben Fehlschlag mit dem
    # nuetzlicheren Text meldet: `stderr` zeigt, WORAN node gescheitert ist,
    # `CalledProcessError` nur, DASS es gescheitert ist.
    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_a_device_without_a_lead_signal_does_not_throw_in_any_binding():
    """Zwischen `GET /api/devices` und `GET /api/devices/<id>/signals` liegt
    ein Rendering-Durchlauf, in dem `signalsByDevice` fuer das Geraet noch
    LEER ist - `leadSignalFor` liefert dann `null`. Das ist kein Sonderfall
    kaputter Daten: es trifft JEDES Geraet einmal, weil die Signale in einer
    zweiten Anfrage nachkommen (2026-09-06).

    `x-show` auf der Huelle half nicht: es setzt nur `display`, es haelt
    Alpine NICHT davon ab, die Ausdruecke der Kinder auszuwerten. Die drei
    Helfer lasen also `signal.key` auf `null` und warfen - dreimal pro
    Geraet, bei jedem Durchlauf.

    Ein Geraet ohne Leitsignal ist ein gueltiger Zustand (die Kachel hat
    dafuer laengst ihren Hinweis), also duerfen die Helfer ihn beantworten,
    statt an ihm zu scheitern.
    """
    values = _app_state(
        """
        state.signalsByDevice = {};
        const lead = state.leadSignalFor(1);
        const out = { lead, calls: {} };
        for (const fn of ["signalIsFresh", "signalAgeTitle", "liveValueOf"]) {
          try {
            out.calls[fn] = { ok: true, value: state[fn](lead) ?? null };
          } catch (error) {
            out.calls[fn] = { ok: false, error: error.message };
          }
        }
        out.formatted = state.formatValue(state.liveValueOf(lead));
        console.log(JSON.stringify(out));
        """
    )

    assert values["lead"] is None, "ohne geladene Signale gibt es kein Leitsignal"
    for name, call in values["calls"].items():
        assert call["ok"], f"{name} warf: {call.get('error')}"

    # Was die Kachel in diesem Zustand zeigt: keine Hervorhebung, kein
    # Tooltip - und der Strich, den `formatValue` fuer "kein Wert" fuehrt.
    assert values["calls"]["signalIsFresh"]["value"] is False
    assert values["calls"]["signalAgeTitle"]["value"] in (None, "")
    assert values["calls"]["liveValueOf"]["value"] is None
    assert values["formatted"] == "-"


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_a_signal_that_exists_is_unaffected_by_the_guard():
    """Die Absicherung darf den Normalfall nicht verbiegen: ein echtes
    Signal muss weiter seinen Live-Wert, seine Hervorhebung und seinen
    Tooltip bekommen."""
    values = _app_state(
        """
        const signal = { key: "d1_1_onoff", title: "Zustand", value: false, functional: true };
        state.signalsByDevice = { 1: [signal] };
        state.liveValues = { d1_1_onoff: true };
        state.liveSeenAt = { d1_1_onoff: 1000 };
        state.nowTick = 1200;
        console.log(JSON.stringify({
          lead: state.leadSignalFor(1).key,
          live: state.liveValueOf(signal),
          fresh: state.signalIsFresh(signal),
          title: state.signalAgeTitle(signal),
        }));
        """
    )

    assert values["lead"] == "d1_1_onoff"
    assert values["live"] is True
    assert values["fresh"] is True
    assert values["title"]


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_the_groups_follow_the_ranking_not_the_endpoint_number():
    """Der Grund fuer die Gruppen: `press` steht zweimal in der Liste -
    1/59/1 und 2/59/1, also zwei verschiedene Tasten derselben
    Fernbedienung. Ohne Gruppe ist das zweimal dasselbe Wort ohne Auskunft,
    welche gemeint ist.

    Und die Reihenfolge: "Geraet" (Endpunkt 0, nur die Batterie) steht
    ZULETZT, obwohl es die kleinste Endpunktnummer traegt - die Gruppen
    uebernehmen die Reihenfolge des ersten Auftretens in der bereits
    gerangten Liste, sie sortieren nicht selbst. Genau das kann eine
    Zeichenketten-Suche in `app.js` nicht belegen.

    `t()` liefert ohne geladene Uebersetzungstabelle den Schluessel selbst
    zurueck (siehe `t` in app.js) - der Titel der Experte-Gruppe ist hier
    deshalb der Schluessel, und das genuegt fuer die Zusicherung."""
    values = _app_state(
        """
        state.signalsByDevice = { 1: [
          { key: "d1_1_press", title: "press", endpoint: 1, cluster_id: 59,
            functional: true, endpoint_label: "Taste 1" },
          { key: "d1_2_press", title: "press", endpoint: 2, cluster_id: 59,
            functional: true, endpoint_label: "Taste 2" },
          { key: "d1_0_battery", title: "battery", endpoint: 0, cluster_id: 47,
            functional: true, endpoint_label: "Gerät" },
          { key: "d1_0_vendor", title: "VendorName", endpoint: 0, cluster_id: 40,
            functional: false, endpoint_label: "Gerät" },
        ] };
        console.log(JSON.stringify(
          state.signalGroupsFor(1).map((g) => ({
            key: g.key, title: g.title, collapsible: g.collapsible,
            signals: g.signals.map((s) => s.key),
          }))
        ));
        """
    )

    assert [g["key"] for g in values] == ["ep1", "ep2", "ep0", "expert"]
    assert [g["title"] for g in values[:3]] == ["Taste 1", "Taste 2", "Gerät"]
    assert values[0]["signals"] == ["d1_1_press"]
    assert values[1]["signals"] == ["d1_2_press"]
    # 156 Signale ueber alle Endpunkte zu gliedern erzeugte nur mehr
    # Ueberschriften - der Experte-Block bleibt EINE zugeklappte Gruppe.
    assert values[3]["signals"] == ["d1_0_vendor"]
    assert values[3]["collapsible"] is True
    assert [g["collapsible"] for g in values[:3]] == [False, False, False]


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_a_device_without_functional_signals_yields_only_the_expert_group():
    """Der Zustand, fuer den der Hinweis `none_functional` jetzt AUSSERHALB
    der Gruppenschleife steht: eine Endpunktgruppe ist nie leer, es gibt
    dann schlicht keine."""
    values = _app_state(
        """
        state.signalsByDevice = { 1: [
          { key: "d1_0_vendor", title: "VendorName", endpoint: 0, cluster_id: 40,
            functional: false, endpoint_label: "Gerät" },
        ] };
        console.log(JSON.stringify(state.signalGroupsFor(1).map((g) => g.key)));
        """
    )

    assert values == ["expert"]


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_live_values_are_shown_with_at_most_two_decimal_places():
    """Die Live-Werte kommen aus Matter-Attributen als ganzzahlige
    Hundertstel; das Umrechnen erbt die Fliesskomma-Unschaerfe, und
    `String(value)` schrieb sie ungekuerzt in die Kachel
    (22.529999999999998 fuer 22.53). Zwei Nachkommastellen sind die
    Genauigkeit, die das Geraet ueberhaupt liefert - alles dahinter ist
    Rauschen, das die Spalte sprengt.

    Geprueft wird hier das VERHALTEN, nicht der ausgelieferte Text: ob
    gerundet oder abgeschnitten wird und was mit einem glatten Wert
    passiert, steht in keiner Zeichenkette, die man in `app.js` suchen
    koennte.
    """
    values = _app_state(
        """
        const cases = [
          22.529999999999998, 21, 21.5, 21.006, -3.14159, 1234.5678, 0.001,
          "22.5299", true, false, null,
        ];
        console.log(JSON.stringify(cases.map((v) => state.formatValue(v))));
        """
    )

    assert values[0] == "22.53", "die Fliesskomma-Unschaerfe verschwindet"
    assert values[1] == "21", "ein glatter Wert bekommt keine Nullen angehaengt"
    assert values[2] == "21.5", "eine einzelne Nachkommastelle bleibt eine"
    assert values[3] == "21.01", "es wird gerundet, nicht abgeschnitten"
    assert values[4] == "-3.14"
    assert values[5] == "1234.57"
    assert values[6] == "0"
    # Was keine Zahl ist, wird auch nicht als eine behandelt: eine
    # Zeichenkette aus der Live-Verbindung zu zerlegen hiesse raten, welcher
    # Teil davon eine Zahl sein soll.
    assert values[7] == "22.5299"
    # Und die beiden Sonderwege von `formatValue` bleiben, wie sie waren
    # (die Uebersetzungstabelle ist in node nicht geladen, deshalb steht
    # hier der Schluessel statt "wahr"/"falsch" - siehe
    # `test_formatting_helpers_translate_and_the_locale_follows_the_language`
    # fuer die Uebersetzung selbst).
    assert values[8] == "web.format.true"
    assert values[9] == "web.format.false"
    assert values[10] == "-"


# ---------------------------------------------------------------------------
# Uebersetzungsmechanismus (Aufgabe 8). Diese Aufgabe uebersetzt noch KEINEN
# eigenen WebUI-Text (das ist Aufgabe 9+) - sie baut nur die Leitung:
# `t()` als globale, top-level Funktion in app.js (erreichbar auch aus
# requestJson/requestDownload, die keinen `this`-Zugriff auf das
# Alpine-Bauteil haben), `stringsReady`/`language`/`loadI18n()` als
# reaktive Bestandteile des `app()`-Objekts, und drei zusaetzliche
# stringsReady-Gatter in index.html nach demselben Muster wie das
# bestehende authReady.
# ---------------------------------------------------------------------------


async def test_the_translation_helper_is_a_global_top_level_function(api):
    """`t()` darf keine Methode von app() sein - `requestJson`/
    `requestDownload` (app.js, vor `function app()`) haben keinen Zugriff
    auf `this` des Alpine-Bauteils, brauchen aber selbst uebersetzten Text
    (spaetere Aufgaben). Deshalb liegt `t()` als Top-Level-Funktion vor
    `function app()`, gestuetzt auf die ebenfalls modul-globale,
    nicht-reaktive Variable `translationStrings` - keins von beidem ein
    Feld des app()-Objekts.

    Belegt nur, dass der ausgelieferte Quelltext diese Bausteine in dieser
    Reihenfolge enthaelt - nicht, dass Alpine `t(...)` zur Laufzeit
    tatsaechlich ueber den umgebenden Skript-Scope aufloest (dafuer
    braeuchte es eine Browser-Engine)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "function t(key" in script
    assert "let translationStrings" in script
    app_index = script.index("function app()")
    assert script.index("let translationStrings") < app_index
    assert script.index("function t(key") < app_index


async def test_strings_ready_and_language_are_reactive_fields_on_app(api):
    """Anders als t()/translationStrings BLEIBEN stringsReady/language
    Felder auf dem app()-Objekt - die muessen reaktiv sein, damit
    x-if="stringsReady && ..." in index.html tatsaechlich neu rendert,
    sobald loadI18n() fertig ist."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    app_body = script[script.index("function app()") :]
    assert "stringsReady: false" in app_body
    assert 'language: "en"' in app_body
    assert "async loadI18n()" in app_body


async def test_load_i18n_sets_the_document_language_via_dom_assignment(api):
    """<html lang> (index.html) liegt AUSSERHALB des x-data-Bereichs (der
    erst bei <body> beginnt) - eine Alpine-Direktive koennte dort nicht
    binden. Gesetzt wird es deshalb per gewoehnlicher DOM-Zuweisung
    innerhalb von loadI18n(), nicht ueber :lang="..." in index.html."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    page = (await client.get("/")).text
    load_i18n_start = script.index("async loadI18n()")
    load_i18n_end = script.index("},", load_i18n_start)
    body = script[load_i18n_start:load_i18n_end]
    assert "document.documentElement.lang = " in body
    assert ':lang="' not in page


async def test_load_i18n_catches_a_failed_request_so_init_still_proceeds(api):
    """Regressionstest: anders als ihre Schwester `loadAuthInfo()` (die ein
    `catch` UND ein `finally` traegt) hatte `loadI18n()` bisher NUR ein
    `finally` - ein Fehlschlag von `GET /api/i18n` (Netzwerkaussetzer, ein
    5xx) lief dadurch als unbehandelte Ablehnung durch `init()`s
    `await Promise.all([this.loadI18n(), this.loadAuthInfo()])` durch, und
    `init()` selbst umschliesst diese Zeile mit keinem eigenen `try`/`catch`
    - mit der Folge, dass `if (this.authenticated) { await this.startApp(); }`
    danach NIE lief, selbst wenn `loadAuthInfo()` fuer sich genommen
    erfolgreich war und die Person angemeldet ist. Sichtbare Auswirkung: ein
    voruebergehender Fehlschlag von `/api/i18n` bei bereits gueltiger
    Sitzung liess die App nie Geraete/Daten laden, ohne jede sichtbare
    Fehlermeldung.

    Belegt nur, dass der ausgelieferte Quelltext innerhalb des Rumpfs von
    `loadI18n()` ein `catch`-Bloch enthaelt, das den Fehler protokolliert -
    nicht, dass ein echter Netzwerkfehler im Browser zur Laufzeit
    tatsaechlich dort landet und `startApp()` danach trotzdem laeuft
    (dafuer braeuchte es eine Browser-Engine, siehe
    `test_the_page_does_not_call_init_a_second_time`)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    load_i18n_start = script.index("async loadI18n()")
    load_i18n_end = script.index("},", load_i18n_start)
    body = script[load_i18n_start:load_i18n_end]
    assert "} catch (" in body
    assert "console.error(" in body
    assert "} finally {" in body
    assert "this.stringsReady = true;" in body


async def test_init_loads_translations_and_auth_info_in_parallel(api):
    """Beide sind unabhaengige, ungeschuetzte Aufrufe, die dieselben
    Auth-Bildschirm-Vorlagen gaten - init() muss sie parallel starten,
    nicht nacheinander."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    # Bis zur naechsten Methode statt ueber eine feste Zeichenzahl: `init()`
    # hat seit der URL-Navigation ein paar Zeilen mehr, und ein Fenster von
    # 800 Zeichen endete mitten im Kommentar davor.
    init_start = script.index("async init()")
    body = script[init_start : script.index("async request(method, path, body)")]
    assert "Promise.all([this.loadI18n(), this.loadAuthInfo()])" in body


async def test_the_three_main_screens_also_wait_for_translations(api):
    """Nach demselben Muster wie authReady (verhindert das Aufblitzen des
    falschen Bildschirms, bis /auth-info geantwortet hat): stringsReady
    gated zusaetzlich alle drei Hauptbereiche (Ersteinrichtung, Anmeldung,
    App), damit keiner davon mit unuebersetzten {key}-Texten aufblitzt,
    bevor GET /api/i18n geantwortet hat."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert 'x-if="stringsReady && authReady && !authenticated && !passwordSet"' in page
    assert 'x-if="stringsReady && authReady && !authenticated && passwordSet"' in page
    assert 'x-if="stringsReady && authenticated"' in page


async def test_get_i18n_returns_a_real_web_namespace_key(api):
    """Der Uebersetzungsmechanismus braucht mindestens einen echten
    web.*-Schluessel, um GET /api/i18n end-to-end zu pruefen, ohne von der
    noch nicht geschriebenen Tabelle aus Aufgabe 9 abzuhaengen - siehe
    strings.yaml, web.test.smoke (eine bewusst test-only benannte
    Schablone, analog zu test.* aus Phase A).

    Bewusst OHNE {platzhalter} in diesem Schluessel (siehe Kommentar bei
    web.test.smoke in strings.yaml sowie den Aufgabe-8-Bericht): der
    urspruengliche Plan sah "smoke test {value}" vor, aber
    `api/language.py:_web_strings()` ruft `i18n.t(key)` fuer jeden
    web.*-Schluessel OHNE Werte auf - ein Platzhalter dort wirft `KeyError`
    und reisst die GESAMTE Antwort mit sich (bestaetigt an vier bereits
    zusammengefuehrten Tests in tests/api/test_language.py, die dadurch
    ploetzlich fehlschlugen). Der eigentliche Fehler liegt in Dateien
    ausserhalb des Kreises dieser Aufgabe und ist hier nicht behoben."""
    client, _, _ = api
    response = await client.get("/api/i18n")
    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "en"
    assert body["strings"]["web.test.smoke"] == "smoke test"


# ---------------------------------------------------------------------------
# Aufgabe 10 - erste inhaltliche WebUI-Uebersetzung: Reiterleiste, Kopfzeile,
# Verbindungsstatus, Formatierungs-/Fehlerhelfer, Zugangsbildschirme. Die
# Uebersetzungstabelle selbst (Aufgabe 9) und die Uebersetzungsmaschine
# (Aufgabe 8) sind bereits gepruefte Bausteine - hier wird nur belegt, dass
# die ausgelieferten Dateien tatsaechlich an diese Bausteine binden, statt
# weiter die deutschen Literale zu tragen. Wie bei den uebrigen Markup-Tests
# dieser Datei gilt: ohne Browser-Engine ist nicht pruefbar, dass Alpine
# `t(...)` zur Laufzeit korrekt aufloest - nur, dass der Quelltext dafuer die
# richtige Bindung traegt.
# ---------------------------------------------------------------------------


async def test_the_generic_network_errors_call_the_global_t_from_a_free_function(api):
    """Aufgabe 10, Schritt 6: der Beleg, dass `t()` auch ausserhalb von
    `app()` funktioniert - `requestJson`/`requestDownload` haben keinen
    Zugriff auf `this` des Alpine-Bauteils. Alle drei trugen denselben
    deutschen Literal (siehe Inventar §13); alle drei muessen jetzt denselben
    `t("web.errors.bridge_unreachable")`-Aufruf tragen, keinen mehr fest im
    Text. `requestUpload` (Projektdatei-Sync-Feature, unabhaengig von dieser
    i18n-Phase auf main entstanden) trug beim Zusammenfuehren der beiden
    Branches denselben Literal noch fest im Text - beim Konfliktaufloesen
    auf denselben `t(...)`-Aufruf umgestellt, damit hier kein drittes,
    unuebersetztes Vorkommen uebrig bleibt."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "Die Brücke ist nicht erreichbar" not in script
    assert script.count('t("web.errors.bridge_unreachable")') == 3
    assert 'return t("web.errors.http_status", { status: response.status });' in script
    assert "`HTTP ${response.status}`" not in script


async def test_the_nav_tabs_bind_to_translation_keys_without_altering_their_links(api):
    """Aufgabe 10, Schritt 3: `x-text` ersetzt den Textknoten jedes Reiters,
    `:class` bleibt unangetastet - ein falsch gebundener Reiter waere im
    Browser sofort sichtbar, aber dieser Test ohne Browser-Engine kann nur
    pruefen, dass Adresse und Bindungen NEBENEINANDER auf demselben Element
    stehen, nicht dass ein Klick tatsaechlich die Ansicht wechselt.

    Seit der URL-Navigation ist jeder Reiter ein `<a href="#/...">` statt
    eines Knopfes mit `@click` (siehe die drei folgenden Tests)."""
    client, _, _ = api
    page = (await client.get("/")).text
    for view_key in ("devices", "export", "system", "settings"):
        assert f'<a href="#/{view_key}" :class="{{ active: view === \'{view_key}\' }}"' in page
        assert f"x-text=\"t('web.nav.{view_key}')\"" in page
    assert "selectView('devices')" not in page


async def test_every_view_has_its_own_url_fragment(api):
    """Der Anlass dieser Aenderung: wer auf "Einstellungen" die Seite neu lud,
    landete wieder im Geraete-Dashboard. Jede Ansicht hat deshalb ein eigenes
    Fragment, und die Reiterleiste besteht aus echten Links darauf.

    Geprueft wird beides zusammen: die Liste in `app.js` (sie entscheidet,
    welches Fragment ueberhaupt angenommen wird) und die vier `href`s im
    Markup. Ein Reiter, der auf ein Fragment zeigt, das `VIEWS` nicht kennt,
    faende nach dem Neuladen wieder das Dashboard vor - genau der Fehler, der
    hier verschwinden soll."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert 'const VIEWS = ["devices", "export", "system", "settings"];' in script
    for view_key in ("devices", "export", "system", "settings"):
        assert f'<a href="#/{view_key}"' in page
        assert f"x-show=\"view === '{view_key}'\"" in page


async def test_the_view_comes_from_the_url_before_anything_is_loaded(api):
    """`init()` liest das Fragment, BEVOR `startApp()` laeuft - sonst baute
    die Seite erst das Dashboard auf und danach die gewuenschte Ansicht: zwei
    Ladevorgaenge und ein sichtbares Aufblitzen der falschen Ansicht.

    Der `hashchange`-Zuhoerer haengt genau einmal am Fenster. Das ist in
    dieser Datei kein Formalismus: doppelte Zuhoerer und doppelte
    Live-Verbindungen aus einem zweiten `init()` sind hier schon zweimal
    passiert (siehe `test_the_page_does_not_call_init_a_second_time`)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    init_body = script[
        script.index("async init() {") : script.index("async request(method, path, body)")
    ]
    assert "this.view = viewFromHash() ?? DEFAULT_VIEW;" in init_body
    assert init_body.index("this.view = viewFromHash()") < init_body.index("this.startApp()")
    assert script.count('addEventListener("hashchange"') == 1


async def test_the_fragment_and_the_shown_view_cannot_drift_apart(api):
    """Beide Richtungen sind verdrahtet: `selectView` traegt die Ansicht in
    die Adresszeile ein (`writeHash`), `applyHash` liest sie zurueck und
    schaltet um. Ohne den Rueckweg blieben Reiterklick, Zurueck-Knopf und
    Lesezeichen wirkungslos; ohne den Hinweg zeigte die Adresszeile nach
    einem programmatischen Wechsel (z. B. ueber den Hinweis-Link "Erst in
    Einstellungen ... hinterlegen") noch die vorige Ansicht an.

    Die beiden Abbruchbedingungen in `applyHash` sind kein Beiwerk: das
    `hashchange`, das `selectView` ueber `writeHash` selbst ausloest, landet
    wieder hier - ohne den Abgleich `view === this.view` liefe jeder
    programmatische Wechsel zweimal. Und vor der Anmeldung darf gar nichts
    geladen werden, sonst liefe eine von Hand geaenderte Adresse auf dem
    Login-Bildschirm in eine 401."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    apply_start = script.index("async applyHash() {")
    select_start = script.index("async selectView(view) {")
    apply_body = script[apply_start:select_start]
    select_body = script[select_start : script.index("async loadDevices()")]
    assert "writeHash(view);" in select_body
    assert "await this.selectView(view);" in apply_body
    assert "if (view === this.view) {" in apply_body
    assert "if (!this.authenticated) {" in apply_body


async def test_an_unknown_fragment_does_not_leave_the_page_empty(api):
    """Ein Lesezeichen auf die aufgeloeste Signale-Ansicht (`#/signals`, siehe
    `test_the_signals_view_is_gone_from_navigation_and_markup`) oder ein
    Tippfehler in der Adresszeile: `viewFromHash` liefert dann `null`, und
    beide Aufrufer weichen aus - `init()` auf `DEFAULT_VIEW`, `applyHash` auf
    die gerade gezeigte Ansicht, die es zugleich nachtraegt. Ohne diesen
    Rueckfall waere jedes `x-show="view === ..."` falsch: eine Seite mit
    Kopfzeile, Reitern und sonst nichts."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert 'const DEFAULT_VIEW = "devices";' in script
    assert "return VIEWS.includes(name) ? name : null;" in script
    apply_body = script[
        script.index("async applyHash() {") : script.index("async selectView(view) {")
    ]
    assert "if (view === null) {" in apply_body
    assert "writeHash(this.view);" in apply_body


async def test_the_first_visit_does_not_leave_a_dead_history_entry(api):
    """Beim Aufruf ohne Fragment traegt `writeHash` `#/devices` per
    `replaceState` nach statt per `location.hash`: ein eigener Chronikeintrag
    fuehrte beim Zurueck-Knopf auf dieselbe Seite ohne Fragment, die das
    Fragment sofort wieder ergaenzte - eine Schaltflaeche, die sichtbar nichts
    tut. Nur der Wechsel ZWISCHEN zwei gueltigen Ansichten bekommt einen
    Eintrag, damit der Zurueck-Knopf den vorigen Reiter zeigt."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    write_body = script[
        script.index("function writeHash(view) {") : script.index("function app() {")
    ]
    assert "if (viewFromHash() === null) {" in write_body
    assert 'window.history.replaceState(null, "", target);' in write_body
    assert "window.location.hash = target;" in write_body


async def test_the_tab_styling_covers_links_and_buttons(api):
    """`nav.tabs` traegt zweierlei: die Reiterleiste oben, seit der
    URL-Navigation aus `<a href="#/...">`, und die Sprachumschaltung in den
    Einstellungen, die sich dieselbe Leiste fuer zwei Knoepfe borgt (siehe
    `test_the_settings_tab_has_a_language_toggle`). Beide Selektoren muessen
    deshalb stehenbleiben - ein Umbau auf nur `nav.tabs a` liess die
    Sprachknoepfe ohne Polsterung, ohne gedaempfte Schrift und ohne den
    Unterstrich der aktiven Sprache zurueck."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    assert "nav.tabs a,\nnav.tabs button {" in css
    assert "nav.tabs a.active,\nnav.tabs button.active {" in css
    tab_rule = css[css.index("nav.tabs a,") : css.index("main {")]
    # Ein Link erbt sonst die Unterstreichung der Dokument-Linkfarbe.
    assert "text-decoration: none;" in tab_rule
    # Und diese beiden Werte hatten die Reiter als Knoepfe aus der
    # allgemeinen `button`-Regel (weiter unten in dieser Datei) - ein Link
    # bringt sie nicht mit: ohne sie wurde die Leiste vier Pixel hoeher und
    # der Unterstrich des aktiven Reiters eckig statt abgerundet. Beides fiel
    # erst in den byte-genau reproduzierbaren Screenshots auf (siehe
    # scripts/capture_screenshots.py), nicht im Test und nicht beim Hinsehen.
    assert "line-height: 1.3;" in tab_rule
    assert "border-radius: 5px;" in tab_rule
    button_rule = css[css.index("\nbutton {") : css.index("button.primary {")]
    assert "line-height: 1.3;" in button_rule
    assert "border-radius: 5px;" in button_rule


async def test_the_header_logout_and_connection_banner_are_translated(api):
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "x-text=\"t('web.header.logout')\"" in markup
    assert ">Abmelden<" not in markup
    assert "x-text=\"t('web.connection.lost_banner')\"" in markup
    assert "Die Live-Verbindung wurde unterbrochen" not in markup
    assert ":title=\"t('web.header.toast_dismiss_tooltip')\"" in markup
    assert "Zum Ausblenden anklicken" not in markup
    assert "x-text=\"t('web.header.heartbeat_prefix')\"" in markup
    assert "Lebenszeichen" not in markup


async def test_connection_status_text_translates_all_four_branches(api):
    """Aufgabe 10, Schritt 4: `connectionStatusText()` behaelt seine drei
    Bedingungen unveraendert - nur die vier zurueckgegebenen Literale werden
    zu `t(...)`-Aufrufen."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("connectionStatusText() {")
    end = script.index("\n    },", start)
    body = script[start:end]
    assert 'return t("web.connection.live");' in body
    assert 'return t("web.connection.lost_reconnecting");' in body
    assert 'return t("web.connection.never_connected");' in body
    assert 'return t("web.connection.connecting");' in body
    assert "Live-Verbindung aktiv" not in body
    assert "Verbindung verloren" not in body
    assert "Keine Verbindung zur Brücke" not in body
    assert '"Verbinde…"' not in body


async def test_the_relative_time_and_header_helpers_are_translated(api):
    """Aufgabe 10, Schritt 4: die drei Alters-Literale in `sinceText()` und
    die beiden Tooltip-Literale in `signalAgeTitle()` tragen jetzt
    Platzhalter statt Template-Strings - dieselben Variablennamen wie
    zuvor (`seconds`/`minutes`/das gerundete Stunden-Objekt/`text`)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    since_start = script.index("sinceText(timestamp) {")
    since_end = script.index("\n    },", since_start)
    since_body = script[since_start:since_end]
    assert 'return t("web.header.time_ago_seconds", { seconds });' in since_body
    assert 'return t("web.header.time_ago_minutes", { minutes });' in since_body
    assert (
        'return t("web.header.time_ago_hours", { hours: Math.round(minutes / 60) });' in since_body
    )
    assert "`vor" not in since_body

    title_start = script.index("signalAgeTitle(signal) {")
    title_end = script.index("\n    },", title_start)
    title_body = script[title_start:title_end]
    assert '? t("web.header.last_updated", { text })' in title_body
    assert ': t("web.header.unchanged_since_load");' in title_body
    assert "Zuletzt aktualisiert" not in title_body
    assert "unveraendert" not in title_body


async def test_the_setup_screen_is_fully_translated(api):
    """Aufgabe 10, Schritt 5: Ueberschrift, Warnbanner (als `x-html`, weil der
    uebersetzte Text das `<strong>` selbst mitbringt, siehe strings.yaml
    `web.auth.setup_warning`), beide Feldbeschriftungen, der Hinweistext und
    der Absende-Knopf - keiner der frueheren deutschen Literale darf mehr im
    Markup stehen."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "x-text=\"t('web.auth.setup_heading')\"" in markup
    assert "loxmatter einrichten" not in markup
    assert "x-html=\"t('web.auth.setup_warning')\"" in markup
    assert "jeder im Netz" not in markup
    assert markup.count("x-text=\"t('web.auth.password_label')\"") == 2
    assert "x-text=\"t('web.auth.password_repeat_label')\"" in markup
    assert "x-text=\"t('web.auth.password_hint')\"" in markup
    assert "Mindestens 8 Zeichen" not in markup
    assert "x-text=\"t('web.auth.setup_submit')\"" in markup
    assert "Passwort vergeben" not in markup


async def test_the_login_screen_is_translated_and_the_product_name_h1_is_untouched(api):
    """Aufgabe 10, Schritt 5: der Absende-Knopf und das gemeinsame
    Passwort-Label wandern auf `t(...)`, aber BEIDE `<h1>loxmatter</h1>`
    (Kopfzeile und Login-Bildschirm) sowie `<title>loxmatter</title>`
    bleiben unangetastet - das ist der Produktname, keine Textzeichenkette
    (Aufgabe 9's Scope-Hinweis)."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "x-text=\"t('web.auth.login_submit')\"" in markup
    assert ">Anmelden<" not in markup
    assert markup.count("<h1>loxmatter</h1>") == 2
    assert "<title>loxmatter</title>" in markup


async def test_session_expired_and_password_mismatch_are_translated(api):
    """Aufgabe 10, Schritt 5: die drei identischen "Sitzung abgelaufen"-Stellen
    (Konstruktor von `UnauthorizedError`, `handleDiagnosticsDisconnect`,
    `handleLiveDisconnect`) teilen sich denselben Schluessel; der
    Passwort-Abgleich beim Einrichten bekommt seinen eigenen."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "Die Sitzung ist abgelaufen" not in script
    assert script.count('t("web.auth.session_expired")') == 3
    assert 'super(t("web.auth.session_expired"));' in script
    assert "Die beiden Eingaben stimmen nicht überein" not in script
    assert 'this.authError = t("web.auth.password_mismatch");' in script


async def test_formatting_helpers_translate_and_the_locale_follows_the_language(api):
    """Aufgabe 10, Schritt 6: `formatTimestamp`/`formatValue` verlieren ihre
    drei deutschen Literale UND ihr fest verdrahtetes `"de-DE"` - die
    `toLocaleString`-Gebietsschema muss dem aktiven `language`-Feld folgen,
    nicht laenger daran vorbei auf Deutsch stehen bleiben, waehrend der Rest
    der Seite Englisch zeigt."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    format_ts_start = script.index("formatTimestamp(isoTimestamp) {")
    format_value_start = script.index("formatValue(value) {")
    ts_body = script[format_ts_start:format_value_start]
    assert 'return t("web.format.never");' in ts_body
    assert 'toLocaleString(this.language === "de" ? "de-DE" : "en-US")' in ts_body
    assert 'toLocaleString("de-DE")' not in ts_body
    assert "noch nie" not in ts_body

    value_end = script.index("\n    },", format_value_start)
    value_body = script[format_value_start:value_end]
    assert 'value ? t("web.format.true") : t("web.format.false");' in value_body
    assert '"wahr"' not in value_body
    assert '"falsch"' not in value_body


async def test_the_commissioning_card_is_translated(api):
    """Aufgabe 11, Schritt 3: Ueberschrift, beide Platzhalter, der
    Absende-Knopf und der Hinweistext der Einlernen-Karte tragen jetzt
    `t(...)`, keiner der frueheren deutschen Literale bleibt im Markup."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "x-text=\"t('web.devices.commission_heading')\"" in markup
    assert "Neues Gerät einlernen" not in markup
    assert ":placeholder=\"t('web.devices.code_placeholder')\"" in markup
    assert "Pairing-Code (11-stellig" not in markup
    assert ":placeholder=\"t('web.devices.thread_dataset_placeholder')\"" in markup
    assert "Thread-Datensatz" not in markup
    assert "x-text=\"t('web.devices.commission_submit')\"" in markup
    assert ">Einlernen<" not in markup
    assert "x-text=\"t('web.devices.commission_hint')\"" in markup
    assert "Hängt das Gerät schon in Apple" not in markup
    # Der zweite Hinweisabsatz kam mit dem Einlern-Zweig dazu (die Bruecke
    # holt den Thread-Datensatz selbst vom Border Router) und laeuft
    # seither ueber dieselbe Tabelle wie der erste.
    assert "x-text=\"t('web.devices.thread_dataset_hint')\"" in markup
    assert "holt ihn beim" not in markup
    assert "x-text=\"t('web.devices.empty')\"" in markup
    assert "Noch kein Gerät eingelernt." not in markup


async def test_the_commissioning_card_leads_with_a_labelled_code_field(api):
    """Entwurf "Code zuerst" (2026-09-07): der Pairing-Code ist das einzige
    Pflichtfeld dieser Karte und bekommt eine eigene Zeile, eine sichtbare
    Beschriftung und den Absende-Knopf in derselben Umrandung.

    Vorher standen hier vier gleich breite Felder in einer `.row` - der
    Code hatte dasselbe Gewicht wie der Thread-Datensatz, den fast niemand
    je ausfuellt - und die Beschriftungen lebten nur im `placeholder`, der
    beim ersten Tastendruck verschwindet und fuer einen Screenreader keine
    Beschriftung ist, sondern ein Beispiel."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    label = _label_around(markup, "web.devices.code_label")
    assert 'for="commission-code"' in label

    code_start = markup.index('<div class="code-field">')
    code_field = markup[code_start : markup.index("</div>", code_start)]
    assert 'id="commission-code"' in code_field
    assert 'x-model="commissionCode"' in code_field
    # Enter im Feld tut dasselbe wie der Knopf daneben - beide stehen in
    # derselben Umrandung, also muessen sie auch dasselbe ausloesen.
    assert '@keydown.enter="commissionDevice()"' in code_field
    assert '@click="commissionDevice()"' in code_field
    assert "x-text=\"t('web.devices.commission_submit')\"" in code_field

    # Die vier Felder in einer Reihe sind weg.
    assert '<div class="row">\n            <input\n              type="text"' not in markup


async def test_the_two_long_commissioning_hints_moved_into_disclosures(api):
    """Kein Satz der frueheren drei Hinweisabsaetze ist verlorengegangen -
    die beiden langen stehen jetzt in je einer `<details>`-Klappe bei dem
    Feld, um das es geht, statt dauerhaft als 78 Woerter Fliesstext unter
    der Karte.

    Nativ, nicht per Alpine-Zustand: ein Auf-/Zu-Zustand, den niemand
    zuruecksetzen muss, kann auch mit nichts anderem auseinanderlaufen -
    dieselbe Ueberlegung wie beim Kachel-Menue und den Signalgruppen."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    help_start = markup.index('<div class="commission-help">')
    help_block = markup[help_start : markup.index('x-show="commissionStep !== null"')]
    assert help_block.count('<details class="commission-disclosure">') == 2
    assert help_block.count("</details>") == 2

    # Klappe 1: der Multi-Admin-Hinweis, hinter einer Frage, die man bei
    # sich wiedererkennt - nicht hinter dem Namen eines Eingabefeldes.
    assert "x-text=\"t('web.devices.code_help_summary')\"" in help_block
    assert "x-text=\"t('web.devices.commission_hint')\"" in help_block

    # Klappe 2: das Thread-Feld samt seinem Hinweis. Die Beschriftung ist
    # jetzt ein echtes `<label>`; der Platzhalter darf sie nicht ersetzen.
    assert "x-text=\"t('web.devices.thread_summary')\"" in help_block
    assert 'x-model="commissionThreadDataset"' in help_block
    assert "x-text=\"t('web.devices.thread_dataset_hint')\"" in help_block
    thread_label = _label_around(help_block, "web.devices.thread_dataset_label")
    assert 'for="commission-thread"' in thread_label

    # Und keiner der drei Absaetze steht mehr frei in der Karte.
    assert markup.count("x-text=\"t('web.devices.commission_hint')\"") == 1
    assert markup.count("x-text=\"t('web.devices.thread_dataset_hint')\"") == 1


async def test_the_commissioning_message_banner_survived_the_redesign(api):
    """Regression zum Umbau selbst: beim Herausloesen der alten `.row` fiel
    der Meldungsabsatz mit heraus. Der Zustand stimmte weiter - eine leere
    Code-Eingabe setzte `commissionMessage` -, nur zeigte ihn nichts mehr
    an, und keiner der damals 1219 Tests bemerkte das.

    Er steht bewusst AUSSERHALB beider Haelften: die Pruefung auf einen
    leeren Code schlaegt zu, bevor ein Lauf beginnt (also am Formular),
    Erfolg und Fehlschlag melden sich danach (also an der Ablaufanzeige).
    Zwei Kopien waeren zwei Stellen, an denen die Meldung haengenbleiben
    kann - deshalb genau eine."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    assert markup.count('x-show="commissionMessage"') == 1
    banner_start = markup.index('x-show="commissionMessage"')
    banner = markup[markup.rindex("<p", 0, banner_start) : markup.index("</p>", banner_start)]
    assert ":class=\"commissionMessageIsError ? 'banner danger' : 'banner ok'\"" in banner
    assert 'x-text="commissionMessage"' in banner

    # Weder im Formular- noch im Ablauf-Zweig, sondern hinter beiden: sonst
    # verschwaende ein Wechsel zwischen den Haelften die Meldung.
    form_start = markup.index('<div x-show="commissionStep === null">')
    flow_start = markup.index('<div x-show="commissionStep !== null"')
    assert form_start < flow_start < banner_start


async def test_the_commissioning_flow_shows_the_two_phases_it_actually_knows(api):
    """Entwurf "Der Ablauf wird sichtbar": waehrend eines Laufs ersetzt eine
    Ablaufanzeige das Formular. Einlernen dauert zwanzig bis sechzig
    Sekunden und war bis hierher ein grau werdender Knopf - von einer
    haengenden Seite nicht zu unterscheiden.

    ZWEI Schritte, nicht drei: mehr Abschnitte kann diese Oberflaeche nicht
    ehrlich auseinanderhalten. Sie kennt den POST auf
    /api/devices/commission und das anschliessende Nachladen von Signalen
    und Befehlen - einen Zwischenstand aus dem Matter-Stack ("Geraet
    gefunden") meldet ihr niemand. Ein dritter Punkt saehe besser aus und
    waere geraten; dieser Test haelt die Anzeige auf dem fest, was
    tatsaechlich bekannt ist."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    flow_start = markup.index('<div x-show="commissionStep !== null"')
    flow = markup[flow_start : markup.index('x-show="commissionMessage"', flow_start)]

    assert flow.count('<li class="commission-step"') == 2
    assert ':class="commissionStepClass(0)"' in flow
    assert ':class="commissionStepClass(1)"' in flow
    assert ':class="commissionStepClass(2)"' not in flow
    assert "x-text=\"t('web.devices.commission_step_joining')\"" in flow
    assert "x-text=\"t('web.devices.commission_step_loading')\"" in flow

    # Der Code des laufenden Versuchs steht ueber den Schritten - nach einem
    # Erfolg ist das Eingabefeld geleert, die Anzeige stuende sonst ohne
    # den Code da, um den es ging.
    assert 'x-text="commissionRunCode"' in flow

    # Ein Weg zurueck zum Formular, mit zwei Beschriftungen fuer die zwei
    # Bedeutungen: nach einem Erfolg das naechste Geraet, nach einem
    # Fehlschlag derselbe Versuch noch einmal.
    assert '@click="resetCommission()"' in flow
    assert 'x-text="commissionFailed ?' in flow
    assert "t('web.devices.commission_retry')" in flow
    assert "t('web.devices.commission_again')" in flow


async def test_commission_device_drives_the_flow_and_stops_where_it_failed(api):
    """Die Schrittanzeige haengt an `commissionDevice` selbst, nicht an
    einer Zeitschaltung: Schritt 0 ab dem POST, Schritt 1 ab dem Nachladen,
    Schritt 2 am Ende.

    Im Fehlerfall wird der Zaehler ausdruecklich NICHT zurueckgesetzt - er
    zeigt weiter auf den Schritt, auf dem es haengengeblieben ist, und
    genau den faerbt `commissionStepClass` rot. Ein Ruecksetzer hier
    naehme der Anzeige ihre einzige Auskunft: wie weit es gekommen ist."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    commission_start = script.index("async commissionDevice() {")
    commission_end = script.index("\n    },", script.index("this.commissionBusy = false;"))
    body = script[commission_start:commission_end]

    assert "this.commissionStep = 0;" in body
    assert "this.commissionFailed = false;" in body
    assert "this.commissionRunCode = this.commissionCode.trim();" in body
    # Schritt 1 steht VOR dem Nachladen, Schritt 2 dahinter.
    load = body.index(
        "await Promise.all([this.loadControls(device.id), this.loadSignals(device.id)]);"
    )
    assert body.index("this.commissionStep = 1;") < load
    assert load < body.index("this.commissionStep = 2;")
    # Der Fehlerzweig markiert, setzt aber nicht zurueck.
    assert "this.commissionFailed = true;" in body
    assert "this.commissionStep = null;" not in body


async def test_the_step_class_is_derived_and_reset_returns_to_the_form(api):
    """`commissionStepClass` ist ein reiner Ausdruck auf
    `commissionStep`/`commissionFailed`, keine dritte Zustandsvariable mit
    Klassennamen darin: zwei Felder, die dasselbe erzaehlen, laufen frueher
    oder spaeter auseinander - und die Anzeige ist die Stelle, an der das
    niemandem auffiele, weil sie ja irgendetwas zeigt.

    `resetCommission` raeumt beides plus die Meldung (sie gehoert zu dem
    Lauf, den man verlaesst) und setzt den Fokus zurueck ins Codefeld -
    erst im naechsten Tick, weil `x-show` das Formular bis dahin noch auf
    `display: none` haelt und ein `focus()` darauf still nichts tut."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    step_class = script[
        script.index("commissionStepClass(index) {") : script.index(
            "\n    },", script.index("commissionStepClass(index) {")
        )
    ]
    assert 'return "failed";' in step_class
    assert 'return "done";' in step_class
    assert 'return index === this.commissionStep ? "running" : "";' in step_class

    reset = script[
        script.index("resetCommission() {") : script.index(
            "\n    },", script.index("resetCommission() {")
        )
    ]
    assert "this.commissionStep = null;" in reset
    assert "this.commissionFailed = false;" in reset
    assert "this.commissionMessage = null;" in reset
    assert "this.$nextTick(() => this.$refs.commissionCode?.focus());" in reset


async def test_the_commissioning_disclosures_do_not_shift_what_stands_around_them(api):
    """Erster Anlauf war eine Reihe: Raumfeld links, die beiden Klappen per
    Fuellstueck ans rechte Ende geschoben. Im Browser riss ein aufgeklapptes
    `<details>` - ein hohes Flex-Element - diese Reihe auseinander: die
    aufgeklappte Klappe rutschte nach unten, die zweite blieb mittig neben
    deren Erklaertext haengen.

    Sie stehen deshalb untereinander in einem eigenen Behaelter. Ein Auf-
    und Zuklappen darf nicht verschieben, was um es herum steht, und am
    wenigsten die Schaltflaeche, die man gerade angeklickt hat."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text

    block_start = css.index(".commission-help {")
    block = css[block_start : css.index("}", block_start)]
    assert "flex-direction: column;" in block
    assert "align-items: flex-start;" in block

    markup = _without_comments((await client.get("/")).text)
    # Das Fuellstueck des ersten Anlaufs ist restlos weg - sonst bliebe eine
    # Regel ohne Element bzw. ein Element ohne Regel stehen.
    assert "meta-spacer" not in markup
    assert "meta-spacer" not in css


async def test_the_device_card_static_text_is_translated(api):
    """Aufgabe 11, Schritt 3: Statuspillen, Entfernen-Knopf, die beiden
    Werte-/Bedienungs-Abschnittsueberschriften mit ihren Ladehinweisen und
    Leerzustaenden, der Wert-Platzhalter und der Senden-Knopf der
    Geraetekarte - jeweils als reiner `x-text`, weil keiner dieser
    Schluessel eingebettetes HTML traegt.

    Task 8 (Raster-Umbau, 2026-09-05) hatte Export und Entfernen zu
    Icon-Knoepfen mit `:title` gemacht (der Platz auf einer 260 px breiten
    Kachel reicht nicht fuer ausgeschriebene Beschriftungen); Task 2 (Kebab-
    Menue, 2026-09-05) hat beide von der Fusszeile ins Menue verlegt, wo
    genug Platz fuer ausgeschriebenen Text ist - `remove`/`export` stehen
    seither wieder als `x-text`, nicht mehr als `:title`. Die beiden Werte-/
    Bedienungs-Abschnittsueberschriften bleiben von Task 8 ersatzlos
    gestrichen - die Kachel zeigt ohnehin nur noch ein einziges Werteraster
    ohne eigenen Titel; `values_heading`/`controls_heading` sind seit
    Task 9 aus `strings.yaml` entfernt.

    `no_functional_signals`, `controls_loading` und `no_known_commands`
    dagegen wurden von Task 9 zunaechst ebenfalls (verfrueht) entfernt und
    sind seit Fund 1 der Review vom 2026-09-05 wieder da: ohne sie war ein
    Geraet mit leeren funktionalen Signalen bzw. ein noch ladender/
    fehlgeschlagener Befehlsabruf nicht von einer echten Leermenge zu
    unterscheiden - genau die Sorte stillschweigend falscher Zustand, die
    Spec 8.1 ausschliessen will (siehe
    `test_the_command_bar_distinguishes_loading_from_genuinely_empty` fuer
    den ausfuehrlichen Beleg dieses Funds)."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "x-text=\"t('web.devices.changed_since_export')\"" in markup
    assert "Geändert seit Export" not in markup
    assert "x-text=\"t('web.devices.offline')\"" in markup
    assert ">Offline<" not in markup
    assert "x-text=\"t('web.devices.remove')\"" in markup
    assert ":title=\"t('web.devices.remove')\"" not in markup
    assert ">Entfernen<" not in markup
    assert "x-text=\"t('web.devices.values_heading')\"" not in markup
    assert ">Werte<" not in markup
    assert "x-text=\"t('web.devices.signals_loading')\"" in markup
    assert "Signale werden geladen" not in markup
    assert "x-text=\"t('web.devices.no_functional_signals')\"" in markup
    assert "Keine funktionalen Signale" not in markup
    assert "x-text=\"t('web.devices.controls_heading')\"" not in markup
    assert ">Bedienung<" not in markup
    assert "x-text=\"t('web.devices.controls_loading')\"" in markup
    assert "Befehle werden geladen" not in markup
    assert "x-text=\"t('web.devices.no_known_commands')\"" in markup
    assert "Keine bekannten Befehle" not in markup
    assert ":placeholder=\"t('web.devices.value_placeholder')\"" in markup
    assert 'placeholder="Wert"' not in markup
    assert "x-text=\"t('web.devices.send')\"" in markup
    assert ">Senden<" not in markup
    assert "x-text=\"t('web.devices.export')\"" in markup
    assert ":title=\"t('web.devices.export')\"" not in markup
    assert ">Exportieren<" not in markup


async def test_the_remaining_count_hints_keep_their_dynamic_span_and_translate_the_rest(api):
    """Aufgabe 11, Schritt 3: die beiden "N weitere..."-Hinweise bestehen aus
    einem dynamischen Zaehler (`remainingSignalCount`/`hiddenRawCommandsFor`)
    gefolgt von statischem Text - nur der statische Teil wandert auf
    `t(...)`, der Zaehler-Ausdruck bleibt unveraendert.

    Task 8 (Raster-Umbau, 2026-09-05) hat aus dem eigenen `<span>` je
    Zaehler-Ausdruck ein zusammengesetztes `x-text` auf einem einzigen
    Element gemacht (der Hinweis auf die restlichen Signale ist jetzt die
    letzte Zeile des Werterasters statt eines eigenen Absatzes, siehe
    `.value-row` in `index.html`) und die Kurzformen `more_signals_short`/
    `more_commands_short` eingefuehrt - die alten Schluessel
    `more_in_signals_view`/`more_commands_unnamed` bleiben ungenutzt in
    `strings.yaml` liegen (Task 9 raeumt sie auf)."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert (
        "x-text=\"'+ ' + remainingSignalCount(device.id) + ' ' + t('web.devices.more_signals_short')\""
        in markup
    )
    assert "x-text=\"t('web.devices.more_in_signals_view')\"" not in markup
    assert "weitere in der Ansicht" not in markup
    assert (
        "x-text=\"'+' + hiddenRawCommandsFor(device.id) + ' ' + t('web.devices.more_commands_short')\""
        in markup
    )
    assert "x-text=\"t('web.devices.more_commands_unnamed')\"" not in markup
    assert "weitere Kommandos vorhanden" not in markup


async def test_the_bridge_ip_hint_splits_prefix_link_suffix_without_collapsing_to_x_html(api):
    """Aufgabe 11, Schritt 3 (das neue Muster dieser Aufgabe): der Hinweis
    "Erst in Einstellungen -> ... hinterlegen." enthaelt einen echten Link auf
    die Einstellungen, der beim Uebersetzen NICHT in einen `x-html`-Block
    verschwinden darf - sonst bliebe von dem Link nur noch Text uebrig. Drei
    eigene Elemente (Praefix, Link, Suffix) je mit eigenem `x-text` halten ihn
    unangetastet. Seit der URL-Navigation traegt er dieselbe Adresse wie der
    Reiter (`#/settings`) statt eines `href="#"` mit abgefangenem Klick.

    Der Ausschnitt endet an der NAECHSTEN Ansicht, nicht an einem
    schliessenden Tag: `"view === 'signals'"` war dieser Anker, bis der
    Reiter aufgeloest wurde (2026-09-05) - jetzt ist es `'export'`. Ein
    Anker auf `</div>` oder `</section>` waere hier untauglich, davon gibt
    es in der Geraeteansicht Dutzende."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    device_section_start = markup.index("x-show=\"view === 'devices'\"")
    device_section_end = markup.index("x-show=\"view === 'export'\"")
    devices_markup = markup[device_section_start:device_section_end]
    assert "x-text=\"t('web.devices.export_hint_prefix')\"" in devices_markup
    assert "x-text=\"t('web.settings.miniserver_link')\"" in devices_markup
    assert "x-text=\"t('web.devices.export_hint_suffix')\"" in devices_markup
    assert "Erst in " not in devices_markup
    assert "Einstellungen → Verbindung zum Miniserver" not in devices_markup
    assert " hinterlegen." not in devices_markup
    assert 'href="#/settings"' in devices_markup


async def test_the_device_list_dynamic_errors_and_toasts_are_translated(api):
    """Aufgabe 11, Schritt 4: die Lade-/Speicher-/Entfernen-Fehler, die
    Export-Hinweise (`exportHintFor`) und die beiden Kommando-Toasts der
    Geraeteliste tragen jetzt `t(...)` mit denselben Platzhaltern wie
    vorher die Template-Strings."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert (
        'this.devicesError = t("web.devices.list_load_error", { message: error.message });'
        in script
    )
    assert "Geraeteliste konnte nicht geladen werden" not in script
    assert (
        'this.deviceActionError = t("web.devices.controls_load_error", { message: error.message });'
        in script
    )
    assert "Bedienelemente konnten nicht geladen werden" not in script
    assert 'return t("web.devices.export_never");' in script
    assert "Noch nicht exportiert" not in script
    assert (
        'return t("web.devices.export_last", { timestamp: this.formatTimestamp(status.exported_at) });'
        in script
    )
    assert "Zuletzt exportiert am" not in script
    assert (
        'this.deviceActionError = t("web.devices.label_save_error", { message: error.message });'
        in script
    )
    assert "Name konnte nicht gespeichert werden" not in script
    assert (
        'this.deviceActionError = t("web.devices.remove_error", { message: error.message });'
        in script
    )
    assert "Gerät konnte nicht entfernt werden" not in script
    execute_start = script.index("async executeCommand(device, command) {")
    execute_end = script.index("\n    },", execute_start)
    execute_body = script[execute_start:execute_end]
    assert (
        'this.showToast(t("web.devices.command_sent", { slug: command.slug, label: device.label }));'
        in execute_body
    )
    assert "wurde an" not in execute_body
    assert (
        'this.showToast(t("web.devices.command_failed", { slug: command.slug, message: error.message }), true);'
        in execute_body
    )
    assert "ist fehlgeschlagen" not in execute_body

    export_device_start = script.index("async exportDevice(device) {")
    export_device_end = script.index("\n    },", export_device_start)
    export_device_body = script[export_device_start:export_device_end]
    assert (
        'this.showToast(t("web.devices.exported_toast", { label: device.label }));'
        in export_device_body
    )
    assert "wurde exportiert." not in export_device_body
    assert (
        'this.deviceActionError = t("web.devices.export_failed", { message: error.message });'
        in export_device_body
    )
    assert "Export fehlgeschlagen" not in export_device_body


async def test_the_device_card_export_bridge_ip_validation_is_translated(api):
    """Aufgabe 11, Schritt 4: die Brücken-IP-Pruefung in `exportDevice`
    (Geraetekarte) teilt sich den Schluessel `web.export.bridge_ip_missing`
    mit Vorschau/Download (Aufgabe 13) - hier wird nur die Kopie in
    `exportDevice` selbst geprueft."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    export_start = script.index("async exportDevice(device) {")
    export_end = script.index("\n    },", export_start)
    body = script[export_start:export_end]
    assert 'this.deviceActionError = t("web.export.bridge_ip_missing");' in body
    assert "Brücken-IP hinterlegen" not in body


async def test_the_commissioning_flow_messages_are_translated(api):
    """Aufgabe 11, Schritt 4: die leere-Code-Pruefung, die Erfolgsmeldung
    (ein einziger `t(...)`-Aufruf statt vier zusammengesetzter Literale) und
    die Fehlschlag-Meldung von `commissionDevice`."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    commission_start = script.index("async commissionDevice() {")
    commission_end = script.index("\n    },", script.index("this.commissionBusy = false;"))
    body = script[commission_start:commission_end]
    assert 'this.commissionMessage = t("web.devices.commission_code_required");' in body
    assert "Bitte zuerst einen Pairing-Code eingeben." not in body
    assert (
        'this.commissionMessage = t("web.devices.commission_success", { label: device.label });'
        in body
    )
    assert "wurde eingelernt" not in body
    # Der Fehlschlag wird seit dem Zusammenfuehren mit dem Einlern-Zweig am
    # HTTP-Status unterschieden, nicht mehr am Text: eine 422 dieser Route
    # traegt die bereits vom Server gerahmte Meldung (api.errors.
    # commissioning_failed), alles andere bekommt den Rahmen hier. Ein
    # Vergleich auf den Anfang des Meldungstextes waere genau die Stelle,
    # an der die Uebersetzung wieder auseinanderliefe - er kennt immer nur
    # eine der beiden Sprachen.
    assert "error.status === 422" in body
    assert 't("web.devices.commission_failed", { message })' in body
    assert "startsWith" not in body
    assert "Einlernen fehlgeschlagen" not in body


async def test_commission_device_avoids_duplicate_tiles(api):
    """Fund 4 (Re-Review 2026-09-05): die Einlern-Route liefert fuer ein
    schon registriertes Geraet dieselbe `device_id` zurueck (siehe den
    Backend-Test `test_recommissioning_a_known_device_applies_the_chosen_room`
    in `tests/api/test_devices.py`). Ein bedingungsloses `push` legte dieses
    Geraet ein zweites Mal in `this.devices` ab: zwei Kacheln mit derselben
    `device.id`, was `x-for`s `:key="device.id"` verletzt und den Raum-Chip
    doppelt zaehlen liess.

    Ohne Browser-Engine laesst sich weder ein doppelter Alpine-Key-Warnhinweis
    in der Konsole pruefen noch die Race gegen ein zeitgleich laufendes
    `saveRoom`/`saveLabel` (das sich vor seinem eigenen `await` eine
    Objekt-Referenz merkt) - belegt wird deshalb der ausgelieferte
    Methodenkoerper: er sucht per `findIndex` nach einem bereits vorhandenen
    Geraet mit derselben ID, befuellt bei einem Treffer das bestehende
    Objekt per `Object.assign` statt es im Array auszutauschen (sonst
    schriebe ein spaeter aufloesendes `saveRoom`/`saveLabel` in ein aus dem
    Array entkoppeltes Exemplar), und haengt andernfalls neu an."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    commission_start = script.index("async commissionDevice() {")
    commission_end = script.index("\n    },", script.index("this.commissionBusy = false;"))
    body = script[commission_start:commission_end]

    assert "const existingIndex = this.devices.findIndex((d) => d.id === device.id);" in body
    assert "this.devices.push(device);" in body
    assert "Object.assign(this.devices[existingIndex], device);" in body


async def test_the_remove_confirm_dialog_text_comes_from_t(api):
    """Aufgabe 11, Schritt 4: der native `window.confirm(...)` in
    `removeDevice` traegt jetzt einen einzigen `t(...)`-Aufruf mit `label`
    und `id` statt der handgebauten Vorlagen-Zeichenkette - der Dialog
    selbst laesst sich ohne Browser-Engine nicht pruefen, wohl aber, dass
    sein Text jetzt aus der Uebersetzungstabelle kommt."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert (
        'window.confirm(t("web.devices.remove_confirm", { label: device.label, id: device.id }))'
        in script
    )
    assert "wirklich entfernen? Das kann nicht rückgängig gemacht werden" not in script
    assert "In Loxone bleiben danach verwaist" not in script


async def test_remove_device_reconciles_the_room_filter(api):
    """Fund 1 (Re-Review 2026-09-05): `removeDevice` mutierte `this.devices`
    direkt und rief weder `reconcileRoomFilter()` noch ein Neuladen auf.
    Filtert man auf einen Raum und loescht dessen letztes Geraet, bleibt
    `roomFilter` auf dem verschwundenen Namen stehen: keine Kachel mehr
    sichtbar, kein Chip mehr aktiv - und war es der letzte Raum ueberhaupt,
    verschwindet sogar die ganze Chip-Leiste (`hasAnyRoom()` dann false,
    siehe index.html), also auch der "Alle"-Chip, der den Ausweg boete.
    Reines Neuladen der Seite war der einzige Ausweg.

    Ohne eine Browser-Engine laesst sich weder `roomFilter` noch das
    gerenderte Markup nach einem Klick pruefen (siehe die anderen Tests in
    dieser Datei, die dasselbe eingestehen). Belegt wird deshalb, dass der
    ausgelieferte Methodenkoerper von `removeDevice` selbst nach dem
    Entfernen aus `this.devices` `reconcileRoomFilter()` aufruft."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    remove_start = script.index("async removeDevice(device) {")
    remove_end = script.index("\n    },", remove_start)
    remove_body = script[remove_start:remove_end]

    filter_index = remove_body.index("(d) => d.id !== device.id)")
    reconcile_index = remove_body.index("this.reconcileRoomFilter();")
    assert filter_index < reconcile_index, remove_body


async def test_the_signal_modal_static_text_is_translated(api):
    """Frueher `test_the_signal_view_static_text_is_translated`: dieselben
    Zusicherungen, jetzt gegen das Modal statt gegen den aufgeloesten
    Reiter (2026-09-05).

    Zwei davon sind ersatzlos entfallen: `show_expert` (der globale
    Schalter weicht einem `<details>` je Gruppe) und
    `expert_collapsed_hint` (dessen Text auf genau diesen Schalter
    verwies). Die uebrigen Hinweise, Beschriftungen und Platzhalter tragen
    unveraendert `t(...)` - keiner der frueheren deutschen Literale bleibt
    im Markup."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    dialog = _signals_dialog(markup)
    assert "x-text=\"t('web.signals.key_hint')\"" in dialog
    assert "ist die Verdrahtung in Loxone" not in markup
    assert "x-text=\"t('web.signals.functional_vs_expert_explanation')\"" in dialog
    assert "„Funktional“ sind die Signale" not in markup
    assert "x-text=\"t('web.signals.load_button')\"" in dialog
    assert ">Signale laden<" not in markup
    assert "x-text=\"t('web.signals.none_functional')\"" in dialog
    assert "Kein Signal dieses Geräts gilt als funktional." not in markup
    assert ":title=\"t('web.signals.key_tooltip')\"" in dialog
    assert "Verdrahtung in Loxone – nicht änderbar." not in markup
    assert "x-text=\"t('web.signals.export_checkbox')\"" in dialog
    assert ">exportieren<" not in markup
    assert ":placeholder=\"t('web.signals.raw_write_placeholder')\"" in dialog
    assert "Rohwert schreiben" not in markup
    assert "x-text=\"t('web.signals.raw_write_submit')\"" in dialog
    assert ">Schreiben<" not in markup


async def test_the_signal_group_titles_are_translated(api):
    """Aufgabe 12, Schritt 4: `signalGroupsFor`'s Gruppentitel (Objekt-
    Literale) laufen ueber `t(...)` statt fester Literale "Funktional" /
    "Experte".

    Aufgabe 6 aendert, WELCHE Objekt-Literale das sind: die Endpunktgruppen
    tragen `signal.endpoint_label` als Titel (kommt bereits uebersetzt von
    der API, siehe Aufgabe 4) und `t("web.signals.group_endpoint_subtitle")`
    als Untertitel; nur die Experte-Gruppe hat noch einen fest ueber `t(...)`
    gesetzten Titel. Der Schluessel `group_functional` gibt es seither
    nicht mehr. Die urspruengliche Zusicherung - kein fest verdrahtetes
    deutsches Literal im Gruppenaufbau - bleibt erhalten: die Sperre gegen
    `"Experte"` als Literal bleibt, die gegen `"Funktional"` entfaellt mit
    dem Titel, den es nicht mehr gibt."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    signal_groups_start = script.index("signalGroupsFor(deviceId) {")
    signal_groups_end = script.index("\n    },", signal_groups_start)
    body = script[signal_groups_start:signal_groups_end]
    assert 'subtitle: t("web.signals.group_endpoint_subtitle"' in body
    assert 'title: t("web.signals.group_expert")' in body
    assert '"Experte"' not in body


async def test_the_group_header_shows_the_endpoint_as_a_subtitle(api):
    """Aufgabe 6, Schritt 5: die `<summary>` der Gruppe zeigt neben Titel
    und Anzahl jetzt auch `group.subtitle` ("Endpunkt N")."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert 'x-text="group.subtitle"' in page


async def test_the_signal_view_dynamic_errors_and_success_are_translated(api):
    """Aufgabe 12, Schritt 4: der Lade-, Titel-Speicher- und
    Export-Kennzeichen-Fehler sowie die Rohwert-Erfolgsmeldung tragen jetzt
    `t(...)`. Die Fehlermeldung des Rohwert-Schreibens (`app.js`, `writeRaw`
    catch-Zweig) bleibt bewusst unangetastet - sie reicht den bereits vom
    Backend uebersetzten `detail`-Text unveraendert durch (Aufgabe 9s
    Scope-Notiz); dafuer gibt es keinen `web.*`-Schluessel."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    load_signals_start = script.index("async loadSignals(deviceId) {")
    load_signals_end = script.index("\n    },", load_signals_start)
    load_signals_body = script[load_signals_start:load_signals_end]
    assert (
        'this.signalsError = t("web.signals.load_error", { message: error.message });'
        in load_signals_body
    )
    assert "Signale konnten nicht geladen werden" not in load_signals_body

    save_title_start = script.index("async saveTitle(signal) {")
    save_title_end = script.index("\n    },", save_title_start)
    save_title_body = script[save_title_start:save_title_end]
    assert (
        'this.signalsError = t("web.signals.title_save_error", { message: error.message });'
        in save_title_body
    )
    assert "Titel konnte nicht gespeichert werden" not in save_title_body

    toggle_exported_start = script.index("async toggleExported(signal) {")
    toggle_exported_end = script.index("\n    },", toggle_exported_start)
    toggle_exported_body = script[toggle_exported_start:toggle_exported_end]
    assert (
        'this.signalsError = t("web.signals.export_flag_error", { message: error.message });'
        in toggle_exported_body
    )
    assert "Export-Kennzeichen konnte nicht geaendert werden" not in toggle_exported_body

    write_raw_start = script.index("async writeRaw(signal) {")
    write_raw_end = script.index("\n    },", write_raw_start)
    write_raw_body = script[write_raw_start:write_raw_end]
    assert (
        'this.rawWriteMessages[signal.key] = { text: t("web.signals.write_success"), isError: false };'
        in write_raw_body
    )
    assert '"Geschrieben."' not in write_raw_body
    # Bewusst unuebersetzt: gibt den Backend-Fehlertext unveraendert durch.
    assert (
        "this.rawWriteMessages[signal.key] = { text: error.message, isError: true };"
        in write_raw_body
    )


async def test_the_export_tab_static_text_is_translated(api):
    """Aufgabe 13, Schritt 3: die Ueberschrift, die IP-/Port-Labels, der
    Einstellungen-verwaltet-Hinweis (dasselbe Praefix/Link/Suffix-Muster wie
    Aufgabe 11s Bruecken-IP-Hinweis, hier mit `web.export.settings_hint_*`
    und dem geteilten `web.settings.miniserver_link`), die beiden
    Checkbox-Labels, die Filtererklaerung (per `x-html`, sie enthaelt ein
    eingebettetes `<strong>`), die beiden Knoepfe, die Vorschau-Ueberschrift,
    alle acht Spaltenkoepfe, die Experte-zurueckgehalten-Erklaerung und das
    Systemvorlagen-Praefix tragen jetzt `t(...)` statt fester deutscher
    Literale - keiner der frueheren Literale bleibt im Markup. Der
    dynamische `x-text`, der `exportPreview.system_files` anhaengt, bleibt
    unveraendert neben dem uebersetzten Praefix stehen."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    assert "x-text=\"t('web.export.heading')\"" in markup
    assert ">Vorlagen exportieren<" not in markup

    assert "x-text=\"t('web.export.udp_port_label')\"" in markup
    assert ">UDP-Port<" not in markup
    assert "x-text=\"t('web.export.http_port_label')\"" in markup
    assert "HTTP-Port (Kommandos)" not in markup

    assert "x-text=\"t('web.export.settings_hint_prefix')\"" in markup
    assert "x-text=\"t('web.export.settings_hint_suffix')\"" in markup
    assert "Wird in" not in markup
    assert "verwaltet." not in markup
    # Der Link selbst bleibt unveraendert (dieselbe Adresse wie der Reiter,
    # jetzt mit dem geteilten Schluessel aus Aufgabe 11 uebersetzt).
    export_hint_start = markup.index("web.export.settings_hint_prefix")
    export_hint_end = markup.index("web.export.settings_hint_suffix")
    export_hint = markup[export_hint_start:export_hint_end]
    assert 'href="#/settings"' in export_hint
    assert "x-text=\"t('web.settings.miniserver_link')\"" in export_hint
    assert "Einstellungen → Verbindung zum Miniserver" not in export_hint

    assert "x-text=\"t('web.export.include_system')\"" in markup
    assert "Systemvorlagen einschließen" not in markup
    assert "x-text=\"t('web.export.only_pending')\"" in markup
    assert "nur noch nicht exportierte" not in markup

    assert "x-html=\"t('web.export.filter_explanation')\"" in markup
    assert "Der Filter gilt für die Vorschau" not in markup

    assert "x-text=\"t('web.export.preview_button')\"" in markup
    assert ">Vorschau ansehen<" not in markup
    assert "x-text=\"t('web.export.download_button')\"" in markup
    assert ">ZIP herunterladen<" not in markup

    assert "x-text=\"t('web.export.preview_heading')\"" in markup
    assert ">Vorschau<" not in markup

    for key in (
        "col_device",
        "col_viu",
        "col_vo",
        "col_inputs",
        "col_commands",
        "col_skipped",
        "col_expert_withheld",
        "col_last_exported",
    ):
        assert f"x-text=\"t('web.export.{key}')\"" in markup
    for literal in (
        "Gerät<",
        "VIU-Datei<",
        "VO-Datei<",
        "Eingänge<",
        "Befehle<",
        "Zuletzt exportiert<",
    ):
        assert f">{literal}" not in markup
    assert "Übersprungen" not in markup

    assert "x-text=\"t('web.export.expert_withheld_explanation')\"" in markup
    assert "„Als Experte zurückgehalten“ sind Signale" not in markup

    assert "x-text=\"t('web.export.system_files_prefix')\"" in markup
    assert "Systemvorlagen:" not in markup
    assert "x-text=\"exportPreview ? exportPreview.system_files.join(', ') : ''\"" in markup


async def test_the_export_tab_filter_explanation_html_renders_the_bold_tag(api):
    """Aufgabe 13, Schritt 3 (Nachbesserung): `web.export.filter_explanation`
    enthaelt ein eingebettetes `<strong>und</strong>` - die Bindung muss
    `x-html` sein, sonst zeigt der Browser die spitzen Klammern als Text
    statt fett darzustellen. `GET /api/i18n` liefert die rohen Vorlagen (per
    `i18n.raw_template`, siehe `api/language.py`), die `app.js`s `t()` in
    diesen HTML-Block einsetzt - belegt hier nur, dass das rohe `<strong>`
    darin ankommt; die tatsaechliche Fettdarstellung ist Teil der manuellen
    Browserpruefung."""
    client, _, _ = api
    body = (await client.get("/api/i18n")).json()
    template = body["strings"]["web.export.filter_explanation"]
    assert "<strong>" in template and "</strong>" in template


async def test_the_export_tab_dynamic_errors_are_translated(api):
    """Aufgabe 13, Schritt 4: der Status-Ladefehler, die verbleibenden zwei
    von drei Kopien der Brücken-IP-Pruefung (`previewExport`,
    `downloadExport` - die dritte in `exportDevice` gehoert Aufgabe 11 und
    ist bereits uebersetzt, siehe
    `test_the_device_card_export_bridge_ip_validation_is_translated`),
    der Vorschau-Fehler und der Download-Fehler tragen jetzt `t(...)`."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    load_status_start = script.index("async loadExportStatus() {")
    load_status_end = script.index("\n    },", load_status_start)
    load_status_body = script[load_status_start:load_status_end]
    assert (
        'this.exportError = t("web.export.status_load_error", { message: error.message });'
        in load_status_body
    )
    assert "Export-Status konnte nicht geladen werden" not in load_status_body

    preview_start = script.index("async previewExport() {")
    preview_end = script.index("\n    },", preview_start)
    preview_body = script[preview_start:preview_end]
    assert 'this.exportError = t("web.export.bridge_ip_missing");' in preview_body
    assert (
        'this.exportError = t("web.export.preview_failed", { message: error.message });'
        in preview_body
    )
    assert "Brücken-IP hinterlegen" not in preview_body
    assert "Vorschau fehlgeschlagen" not in preview_body

    download_start = script.index("async downloadExport() {")
    download_end = script.index("\n    },", download_start)
    download_body = script[download_start:download_end]
    assert 'this.exportError = t("web.export.bridge_ip_missing");' in download_body
    assert (
        'this.exportError = t("web.export.download_failed", { message: error.message });'
        in download_body
    )
    assert "Brücken-IP hinterlegen" not in download_body
    assert "Download fehlgeschlagen" not in download_body


async def test_the_system_tab_static_text_is_translated(api):
    """Aufgabe 14, Schritt 3: die beiden Karten-Ueberschriften „Systemcheck"
    und „Live-Diagnose" mitsamt „Aktualisieren"-Knopf, die drei
    ternaeren Literale (Systemcheck-Status OK/Fehler, Verbindungsstatus
    live/getrennt - letzterer nutzt den bereits aus Aufgabe 10 bekannten
    Schluessel `web.connection.live` fuer den wahren Zweig -, Pausieren/
    Fortsetzen), das Rauschfilter-Label, die Log-Stufen-Beschriftung samt
    aller vier `<option>`s (der „Fehler"-Eintrag teilt sich
    `web.system.check_error` mit dem Systemcheck-Status), der „Leeren"-
    Knopf, die Pause/Leeren-Erklaerung (als `x-html`, sie enthaelt ein
    eingebettetes `<span class="key">tail -f</span>`), die Ueberschriften
    und Hinweistexte der Logs-, UDP- und Kommando-Log-Karten sowie die
    Sicherungskarte (Ueberschrift, beide Erklaerungsabsaetze, der
    Download-Knopf) tragen jetzt `t(...)` statt fester deutscher Literale
    - keiner der frueheren Literale bleibt im Markup."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    assert "x-text=\"t('web.system.checks_heading')\"" in markup
    assert ">Systemcheck<" not in markup
    assert "x-text=\"t('web.system.refresh')\"" in markup
    assert ">Aktualisieren<" not in markup

    assert "x-text=\"check.ok ? t('web.system.check_ok') : t('web.system.check_error')\"" in markup
    assert "'OK' : 'Fehler'" not in markup

    assert "x-text=\"t('web.system.live_heading')\"" in markup
    assert ">Live-Diagnose<" not in markup
    assert (
        "x-text=\"diagnosticsConnected ? t('web.connection.live') : t('web.system.diag_disconnected')\""
        in markup
    )
    assert "Live-Verbindung aktiv" not in markup
    assert "Verbindung getrennt" not in markup

    assert "x-text=\"diagnosticsPaused ? t('web.system.resume') : t('web.system.pause')\"" in markup
    assert "'Fortsetzen' : 'Pausieren'" not in markup

    assert "x-text=\"t('web.system.hide_noise')\"" in markup
    assert "Heartbeat und Full-Resend ausblenden" not in markup

    assert "x-text=\"t('web.system.log_level_label')\"" in markup
    assert ">Log-Stufe<" not in markup
    assert "x-text=\"t('web.system.log_level_info')\"" in markup
    assert "x-text=\"t('web.system.log_level_warn')\"" in markup
    assert "x-text=\"t('web.system.check_error')\"" in markup
    assert "x-text=\"t('web.system.log_level_critical')\"" in markup
    assert ">Info<" not in markup
    assert ">Warnung<" not in markup
    assert ">Fehler<" not in markup
    assert ">Kritisch<" not in markup

    assert "x-text=\"t('web.system.clear')\"" in markup
    assert ">Leeren<" not in markup

    assert "x-html=\"t('web.system.pause_clear_explanation')\"" in markup
    assert "haelt nur das Anhaengen" not in markup
    assert "wirkt nur auf diese Seite" not in markup

    assert "x-text=\"t('web.system.logs_heading')\"" in markup
    assert ">Logs<" not in markup
    assert "x-text=\"t('web.system.logs_hint')\"" in markup
    assert "Protokollzeilen der Brücke" not in markup

    assert "x-text=\"t('web.system.udp_heading')\"" in markup
    assert ">UDP-Mitschnitt<" not in markup
    assert "x-text=\"t('web.system.udp_hint')\"" in markup
    assert "Tatsächlich über den Draht" not in markup

    assert "x-text=\"t('web.system.command_log_heading')\"" in markup
    assert ">Kommando-Log<" not in markup
    assert "x-text=\"t('web.system.command_log_hint')\"" in markup
    assert "Eingehende HTTP-Aufrufe" not in markup

    assert "x-text=\"t('web.system.backup_heading')\"" in markup
    assert ">Sicherung<" not in markup
    assert "x-text=\"t('web.system.backup_explanation')\"" in markup
    assert "Sicherung der Fabric-Zugangsdaten" not in markup
    assert "x-text=\"t('web.system.backup_access_note')\"" in markup
    assert "Nur nach Anmeldung abrufbar" not in markup
    assert "x-text=\"t('web.system.backup_download')\"" in markup
    assert ">Sicherung herunterladen<" not in markup


async def test_the_system_tab_pause_clear_explanation_html_renders_the_key_span(api):
    """Aufgabe 14, Schritt 3 (Nachbesserung): `web.system.pause_clear_explanation`
    enthaelt ein eingebettetes `<span class="key">tail -f</span>` - die
    Bindung muss `x-html` sein, sonst zeigt der Browser die spitzen Klammern
    als Text statt das Tastatur-Styling anzuwenden. Belegt hier nur, dass
    das rohe Markup im ausgelieferten Sprachstring ankommt; die tatsaechliche
    Darstellung ist Teil der manuellen Browserpruefung."""
    client, _, _ = api
    body = (await client.get("/api/i18n")).json()
    template = body["strings"]["web.system.pause_clear_explanation"]
    assert '<span class="key">tail -f</span>' in template


async def test_the_system_tab_dynamic_errors_are_translated(api):
    """Aufgabe 14, Schritt 4: der Systemcheck-Ladefehler (`loadSystem`) und
    der Sicherungs-Fehler (`downloadFabricBackup`) tragen jetzt `t(...)`
    statt Template-Strings."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    load_system_start = script.index("async loadSystem() {")
    load_system_end = script.index("\n    },", load_system_start)
    load_system_body = script[load_system_start:load_system_end]
    assert (
        'this.systemError = t("web.system.load_error", { message: error.message });'
        in load_system_body
    )
    assert "Diagnose konnte nicht geladen werden" not in load_system_body

    download_backup_start = script.index("async downloadFabricBackup() {")
    download_backup_end = script.index("\n    },", download_backup_start)
    download_backup_body = script[download_backup_start:download_backup_end]
    assert (
        'this.backupError = t("web.system.backup_error", { message: error.message });'
        in download_backup_body
    )
    assert "Sicherung nicht möglich" not in download_backup_body


async def test_the_settings_tab_static_text_is_translated(api):
    """Aufgabe 15, Schritt 3: die Verbindungskarte (Ueberschrift, Erklaerung
    als `x-html` - sie enthaelt ein eingebettetes `<strong>Nicht</strong>`
    und einen `<span class="key">` mit dem URL-Beispiel -, das geteilte
    `web.bridge_ip_label`, der Platzhalter, die Port-Beschriftungen, der
    Speichern-Knopf und der Zuletzt-gespeichert-/Noch-nicht-gespeichert-
    Hinweis) tragen jetzt `t(...)` statt fester deutscher Literale. Die
    „Verbindung zum Miniserver"-Ueberschrift ist der eindeutigste Beleg,
    dass diese Karte ueberhaupt uebersetzt wurde - siehe Schritt 1/2.

    Prueft NICHT mehr die globale Abwesenheit des Textes ueber die ganze
    Seite: das unabhaengig auf main entstandene Projektdatei-Sync-Feature
    (siehe dessen eigene Karte im Export-Tab) verlinkt mit genau derselben
    rohen deutschen Phrase „Einstellungen → Verbindung zum Miniserver" auf
    diese Einstellungskarte - ein eigenes, noch unuebersetztes Feature
    ausserhalb dieser Aufgabe, kein Beleg dafuer, dass die Ueberschrift hier
    selbst unuebersetzt waere. Die praezise `>...<`-Form unten trifft
    ausschliesslich die Ueberschrift als eigenen Textknoten."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    assert "x-text=\"t('web.settings.connection_heading')\"" in markup
    assert ">Verbindung zum Miniserver<" not in markup

    assert "x-html=\"t('web.settings.connection_explanation')\"" in markup
    assert "Gemeint ist die Adresse des Rechners" not in markup

    assert "x-text=\"t('web.bridge_ip_label')\"" in markup

    assert ":placeholder=\"t('web.settings.bridge_ip_placeholder')\"" in markup
    assert 'placeholder="z. B. 192.168.1.20"' not in markup

    assert "x-text=\"t('web.settings.udp_port_label')\"" in markup
    assert ">UDP-Port (virtueller Eingang)<" not in markup

    assert "x-text=\"t('web.settings.http_port_label')\"" in markup
    assert ">HTTP-Port (Befehle empfangen)<" not in markup

    assert "x-text=\"t('web.settings.save')\"" in markup
    assert ">Speichern<" not in markup

    assert "x-text=\"t('web.settings.last_saved_prefix')\"" in markup
    assert "Zuletzt gespeichert:" not in markup
    assert 'x-text="formatTimestamp(bridgeSettings.saved_at)"' in markup

    assert "x-text=\"t('web.settings.never_saved')\"" in markup
    assert "Noch nicht gespeichert." not in markup


async def test_the_settings_tab_connection_explanation_html_renders_inline_markup(api):
    """Aufgabe 15, Schritt 3 (Nachbesserung, analog zu Aufgabe 13/14):
    `web.settings.connection_explanation` enthaelt sowohl ein eingebettetes
    `<strong>Nicht</strong>` als auch einen `<span class="key">`, der das
    URL-Beispiel umschliesst - die Bindung muss deshalb `x-html` sein, sonst
    zeigt der Browser die spitzen Klammern als Text. `GET /api/i18n` liefert
    die rohe Vorlage; die tatsaechliche Darstellung ist Teil der manuellen
    Browserpruefung."""
    client, _, _ = api
    body = (await client.get("/api/i18n")).json()
    template = body["strings"]["web.settings.connection_explanation"]
    assert "<strong>" in template and "</strong>" in template
    assert '<span class="key">' in template


async def test_the_settings_tab_dynamic_errors_are_translated(api):
    """Aufgabe 15, Schritt 6: der Lade- und Speicherfehler sowie die
    Pflichtfeld-Meldung und die Erfolgs-Toast von `loadSettings`/
    `saveSettings` tragen jetzt `t(...)` statt fester deutscher Literale
    oder Template-Strings."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    load_start = script.index("async loadSettings() {")
    load_end = script.index("\n    },", load_start)
    load_body = script[load_start:load_end]
    assert (
        'this.settingsError = t("web.settings.load_error", { message: error.message });'
        in load_body
    )
    assert "Einstellungen konnten nicht geladen werden" not in load_body

    save_start = script.index("async saveSettings() {")
    save_end = script.index("\n    },", save_start)
    save_body = script[save_start:save_end]
    assert 'this.settingsError = t("web.settings.bridge_ip_required");' in save_body
    assert "Bitte die IP dieser Brücke eingeben." not in save_body
    assert 'this.showToast(t("web.settings.saved_toast"));' in save_body
    assert "Einstellungen gespeichert." not in save_body
    assert (
        'this.settingsError = t("web.settings.save_error", { message: error.message });'
        in save_body
    )
    assert "Einstellungen konnten nicht gespeichert werden" not in save_body


async def test_the_settings_tab_has_a_language_toggle(api):
    """Aufgabe 15, Schritt 4: die bisherige Platzhalterkarte „Weitere
    Einstellungen" ist ersetzt (nicht uebersetzt, siehe ihr eigener
    Kommentar in Aufgabe 9) durch zwei Knoepfe, die die aktuelle Sprache
    ueber die bereits vorhandene reaktive `language`-Eigenschaft (Aufgabe 8)
    markieren und beim Anklicken `setLanguage(...)` aufrufen (Schritt 5).
    Wiederverwendet die bereits vorhandene `nav.tabs`/`button.active`-Klasse
    (Reiterleiste oben) statt einer neu erfundenen CSS-Klasse - siehe
    `style.css`, es gibt sonst kein Beispiel fuer eine Knopfreihe mit
    Aktiv-Zustand."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    assert ">Weitere Einstellungen<" not in markup
    assert "Hier entstehen künftig weitere Einstellungen" not in markup

    assert "x-text=\"t('web.settings.language_heading')\"" in markup

    assert ":class=\"{ active: language === 'en' }\"" in markup
    assert "@click=\"setLanguage('en')\"" in markup
    assert "x-text=\"t('web.settings.language_en')\"" in markup

    assert ":class=\"{ active: language === 'de' }\"" in markup
    assert "@click=\"setLanguage('de')\"" in markup
    assert "x-text=\"t('web.settings.language_de')\"" in markup


async def test_app_js_defines_set_language(api):
    """Aufgabe 15, Schritt 1/2 und 5: `app.js` liefert `setLanguage`, das
    `PATCH /api/language` (Aufgabe 1) aufruft und danach die Seite neu
    laedt - siehe die Auftragsbeschreibung fuer die bewusst einfache
    Variante ohne Sonderfall fuer bereits angezeigte Toasts/WebSocket-
    Zustaende."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    assert "async setLanguage(language) {" in script

    start = script.index("async setLanguage(language) {")
    end = script.index("\n    },", start)
    body = script[start:end]
    assert 'await this.request("PATCH", "/api/language", { language });' in body
    assert "window.location.reload();" in body


async def test_set_language_handles_request_failures_like_save_settings(api):
    """Review-Fix Important (Whole-Branch-Review, 2026-09-04): jede andere
    Aktion in app.js (`saveSettings`, `exportDevice`, `commissionDevice`,
    `sendCommand`, ...) kapselt `this.request(...)` in try/catch und
    zeigt einen Fehler ueber ein bestehendes Fehlerfeld an -
    `setLanguage` war bislang die einzige Ausnahme: ein Fehlschlag (z. B.
    400/502, `this.request` wirft erneut ausser bei 401) wurde zu einer
    unbehandelten Promise-Ablehnung ohne jede Rueckmeldung. Dieser Test
    haelt fest, dass `setLanguage` jetzt dasselbe try/catch/finally-Muster
    wie `saveSettings` verwendet (settingsBusy-Wache eingeschlossen)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    start = script.index("async setLanguage(language) {")
    end = script.index("\n    },", start)
    body = script[start:end]
    assert "try {" in body
    assert "} catch (error) {" in body
    assert "} finally {" in body
    assert (
        'this.settingsError = t("web.settings.language_error", { message: error.message });' in body
    )
    assert "this.settingsBusy = true;" in body
    assert "this.settingsBusy = false;" in body


async def test_the_projectsync_card_static_text_is_translated(api):
    """Aufgabe 16 (Projektdatei-Sync-Karte): die Ueberschrift, der Einfuehr-
    ungstext, der Bruecken-IP-Hinweis (Praefix/Link/Suffix - derselbe Aufbau
    wie Aufgabe 11/13, hier mit `web.export.projectsync_bridge_ip_hint_*`
    und dem geteilten `web.settings.miniserver_link`), das Dateifeld-Label,
    der Verarbeitungs-Hinweis, die Miniserver-Auswahl (Label, Platzhalter-
    Option, Mehrfach-Hinweis - Nutzerwunsch nach dem Review: ersetzt seit
    dem Zusammenfuehren mit `main` das fruehere IP-Textfeld komplett, siehe
    `web.export.projectsync_miniserver_select_*`/`_multiple_miniservers_
    hint`), die "Alles aktuell"-Meldung, die fuenf Gesamt-Tally-
    Beschriftungen, die abweichende "alles aktuell"-Kurzform je
    Geraetekarte, die vereinfachte Disclosure (ein einziger `t(...)`-Aufruf
    mit `{count}` statt zwei Elementen), das Checkbox-Label und der
    Download-Knopf tragen jetzt `t(...)` statt fester deutscher Literale -
    keiner der frueheren Literale bleibt im Markup."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    assert "x-text=\"t('web.export.projectsync_heading')\"" in markup
    assert ">Projektdatei-Sync<" not in markup

    assert "x-text=\"t('web.export.projectsync_intro')\"" in markup
    assert "vorhandene virtuelle Ein-/Ausgänge werden" not in markup

    assert "x-text=\"t('web.export.projectsync_bridge_ip_hint_prefix')\"" in markup
    assert "x-text=\"t('web.export.projectsync_bridge_ip_hint_suffix')\"" in markup
    bridge_hint_start = markup.index("web.export.projectsync_bridge_ip_hint_prefix")
    bridge_hint_end = markup.index("web.export.projectsync_bridge_ip_hint_suffix")
    bridge_hint = markup[bridge_hint_start:bridge_hint_end]
    assert 'href="#/settings"' in bridge_hint
    assert "x-text=\"t('web.settings.miniserver_link')\"" in bridge_hint
    assert "Einstellungen → Verbindung zum Miniserver" not in bridge_hint
    assert "die Brücken-IP hinterlegen" not in markup

    assert "x-text=\"t('web.export.projectsync_file_label')\"" in markup
    assert "Projektdatei (.Loxone)<" not in markup

    assert "x-text=\"t('web.export.projectsync_busy')\"" in markup
    assert "Wird verarbeitet …" not in markup

    assert "x-text=\"t('web.export.projectsync_miniserver_select_label')\"" in markup
    assert ">Miniserver<" not in markup
    assert "x-text=\"t('web.export.projectsync_miniserver_select_placeholder')\"" in markup
    assert "Bitte wählen …" not in markup
    assert "x-text=\"t('web.export.projectsync_multiple_miniservers_hint')\"" in markup
    assert "enthält mehrere Miniserver" not in markup

    assert "x-text=\"t('web.export.projectsync_all_current')\"" in markup
    assert "Alles aktuell – keine Änderungen nötig." not in markup

    for key in (
        "projectsync_tally_new",
        "projectsync_tally_updated",
        "projectsync_tally_orphaned",
        "projectsync_tally_conflict",
        "projectsync_tally_unchanged",
    ):
        assert f"x-text=\"t('web.export.{key}')\"" in markup
    assert "&nbsp;neu" not in markup
    assert "&nbsp;aktualisiert" not in markup
    assert "&nbsp;verwaist" not in markup
    assert "&nbsp;Konflikt" not in markup
    assert "&nbsp;unverändert" not in markup

    assert "x-text=\"t('web.export.projectsync_group_all_current')\"" in markup
    assert ">alles aktuell<" not in markup

    assert (
        "x-text=\"t('web.export.projectsync_unchanged_disclosure', "
        '{ count: section.unchanged.length })"' in markup
    )
    assert "unveränderte Signale anzeigen" not in markup

    assert "x-text=\"t('web.export.projectsync_new_devices_checkbox')\"" in markup
    assert "Neue Geräte-Container ebenfalls anlegen" not in markup

    assert "x-text=\"t('web.export.projectsync_download_button')\"" in markup
    assert "Gepatchte Datei herunterladen<" not in markup


async def test_the_projectsync_card_dynamic_strings_are_translated(api):
    """Aufgabe 16 (Projektdatei-Sync-Karte): der Bruecken-IP-Fehler in
    `uploadProjectFile` (teilt sich `web.export.bridge_ip_missing` mit
    Aufgabe 11/13), der Hochladen-fehlgeschlagen-Fehler, die sieben-
    eintragige `projectSyncStatusLabel`-Tabelle, das verwaiste-Geraet-
    Fallback-Label in `projectSyncGroupedEntries`, die beiden
    Ein-/Ausgang-Sektionsbeschriftungen, die fuenf `projectSyncEntryNote`-
    Rueckgaben und die sechs-eintragige `projectSyncAttrLabel`-Tabelle
    tragen jetzt `t(...)` statt fester deutscher Literale."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    upload_start = script.index("async uploadProjectFile(event) {")
    upload_end = script.index("\n    },", script.index('input.value = "";', upload_start))
    upload_body = script[upload_start:upload_end]
    assert 'this.projectSync.error = t("web.export.bridge_ip_missing");' in upload_body
    assert "die Brücken-IP hinterlegen" not in upload_body

    # `_syncProjectFile` ist seit dem Zusammenfuehren mit `main` (Miniserver-
    # Auswahlfeld statt IP-Textfeld) der gemeinsame Kern von
    # `uploadProjectFile` UND `confirmProjectSyncMiniserver` - der Hochladen-
    # fehlgeschlagen-Fehler lebt seither dort, nicht mehr in
    # `uploadProjectFile` selbst.
    sync_start = script.index("async _syncProjectFile(file, miniserverIp) {")
    sync_end = script.index("\n    },", sync_start)
    sync_body = script[sync_start:sync_end]
    assert (
        'this.projectSync.error = t("web.export.projectsync_upload_failed", '
        "{ message: error.message });" in sync_body
    )
    assert "Hochladen fehlgeschlagen" not in sync_body

    status_label_start = script.index("projectSyncStatusLabel(status) {")
    status_label_end = script.index("\n    },", status_label_start)
    status_label_body = script[status_label_start:status_label_end]
    assert 'unchanged: t("web.export.projectsync_status_unchanged"),' in status_label_body
    assert 'updated: t("web.export.projectsync_status_updated"),' in status_label_body
    assert 'new_signal: t("web.export.projectsync_status_new_signal"),' in status_label_body
    assert 'new_device: t("web.export.projectsync_status_new_device"),' in status_label_body
    assert 'orphaned: t("web.export.projectsync_status_orphaned"),' in status_label_body
    assert 'conflict: t("web.export.projectsync_status_conflict"),' in status_label_body
    assert (
        'possible_duplicate: t("web.export.projectsync_status_possible_duplicate"),'
        in status_label_body
    )
    assert '"Unverändert"' not in status_label_body
    assert '"Aktualisiert"' not in status_label_body
    assert '"Neues Signal"' not in status_label_body
    assert '"Neues Gerät"' not in status_label_body
    assert "wird nicht verändert" not in status_label_body
    assert "wird übersprungen" not in status_label_body

    grouped_start = script.index("projectSyncGroupedEntries(entries) {")
    grouped_end = script.index("\n    },", grouped_start)
    grouped_body = script[grouped_start:grouped_end]
    assert (
        't("web.export.projectsync_unassigned_device_label")\n                : entry.device_label'
        in grouped_body
    )
    assert "Nicht mehr zugeordnet" not in grouped_body
    assert 'label: t("web.export.projectsync_section_inputs"),' in grouped_body
    assert 'label: t("web.export.projectsync_section_outputs"),' in grouped_body
    assert '"Eingänge"' not in grouped_body
    assert '"Ausgänge"' not in grouped_body

    note_start = script.index("projectSyncEntryNote(entry) {")
    note_end = script.index("\n    },", note_start)
    note_body = script[note_start:note_end]
    assert 'return t("web.export.projectsync_note_new_device");' in note_body
    assert 'return t("web.export.projectsync_note_new_signal");' in note_body
    assert 'return t("web.export.projectsync_note_orphaned");' in note_body
    assert 'return t("web.export.projectsync_note_conflict");' in note_body
    assert 'return t("web.export.projectsync_note_possible_duplicate");' in note_body
    assert "Neuer virtueller Ein-/Ausgang" not in note_body
    assert "Neues Signal wird im bestehenden" not in note_body
    assert "Gehört zu keinem bekannten Gerät mehr." not in note_body
    assert "Unerwartete Struktur in der Datei." not in note_body
    assert "Ein bestehender Befehl trägt bereits diesen Titel" not in note_body

    attr_label_start = script.index("projectSyncAttrLabel(attr) {")
    attr_label_end = script.index("\n    },", attr_label_start)
    attr_label_body = script[attr_label_start:attr_label_end]
    assert 'Title: t("web.export.projectsync_attr_title"),' in attr_label_body
    assert 'Check: t("web.export.projectsync_attr_check"),' in attr_label_body
    assert 'Analog: t("web.export.projectsync_attr_analog"),' in attr_label_body
    assert 'Unit: t("web.export.projectsync_attr_unit"),' in attr_label_body
    assert 'CmdOn: t("web.export.projectsync_attr_cmd_on"),' in attr_label_body
    assert 'CmdOff: t("web.export.projectsync_attr_cmd_off"),' in attr_label_body
    assert '"Titel"' not in attr_label_body
    assert '"Prüfbefehl"' not in attr_label_body
    assert '"Einheit"' not in attr_label_body
    assert '"Befehl Ein"' not in attr_label_body
    assert '"Befehl Aus"' not in attr_label_body


async def test_the_resend_card_static_text_is_translated(api):
    """Periodischer-Resend-Karte (Settings-Tab, letzte Karte, direkt nach der
    Sprachumschaltung) und das dazugehoerige Checkbox-Label in der
    Signalliste tragen jetzt `t(...)` statt fester deutscher Literale - die
    Speichern-Schaltflaeche teilt sich absichtlich `web.settings.save` mit
    der Verbindungs-Karte darueber (dieselbe Bedeutung, kein eigener
    Schluessel)."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    card_start = markup.rindex('<div class="card">', 0, markup.index("resendIntervalDraft"))
    card_end = markup.index("</section>", card_start)
    card = markup[card_start:card_end]

    assert "x-text=\"t('web.settings.resend_heading')\"" in card
    assert ">Periodischer Resend<" not in card

    assert "x-text=\"t('web.settings.resend_explanation')\"" in card
    assert "Markierte Signale" not in card

    assert "x-text=\"t('web.settings.resend_interval_label')\"" in card
    assert "Intervall in Sekunden" not in card

    assert "x-text=\"t('web.settings.save')\"" in card
    assert ">Speichern<" not in card

    resend_checkbox_label = _label_around(markup, "toggleResend(signal)")
    assert "x-text=\"t('web.signals.resend_checkbox')\"" in resend_checkbox_label
    assert "periodisch erneut senden" not in resend_checkbox_label


async def test_the_resend_card_dynamic_strings_are_translated(api):
    """`toggleResend` (Signalliste), sowie `loadResendInterval` und
    `saveResendInterval` (Settings-Tab) tragen jetzt `t(...)` statt fester
    deutscher Literale fuer ihre Fehlermeldungen, die Validierung und den
    Erfolgs-Toast."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    toggle_resend_start = script.index("async toggleResend(signal) {")
    toggle_resend_end = script.index("\n    },", toggle_resend_start)
    toggle_resend_body = script[toggle_resend_start:toggle_resend_end]
    assert (
        'this.signalsError = t("web.signals.resend_toggle_error", { message: error.message });'
        in toggle_resend_body
    )
    assert "Resend-Kennzeichen konnte nicht geaendert werden" not in toggle_resend_body

    load_interval_start = script.index("async loadResendInterval() {")
    load_interval_end = script.index("\n    },", load_interval_start)
    load_interval_body = script[load_interval_start:load_interval_end]
    assert (
        'this.resendIntervalError = t("web.settings.resend_load_error", { message: error.message });'
        in load_interval_body
    )
    assert "Resend-Intervall konnte nicht geladen werden" not in load_interval_body

    save_interval_start = script.index("async saveResendInterval() {")
    save_interval_end = script.index("\n    },", save_interval_start)
    save_interval_body = script[save_interval_start:save_interval_end]
    assert (
        'this.resendIntervalError = t("web.settings.resend_interval_invalid");'
        in save_interval_body
    )
    assert "Bitte ein Intervall von mindestens 10 Sekunden eingeben." not in save_interval_body
    assert 'this.showToast(t("web.settings.resend_saved_toast"));' in save_interval_body
    assert "Resend-Intervall gespeichert." not in save_interval_body
    assert (
        'this.resendIntervalError = t("web.settings.resend_save_error", { message: error.message });'
        in save_interval_body
    )
    assert "Resend-Intervall konnte nicht gespeichert werden" not in save_interval_body


async def test_the_script_offers_room_filtering_grouping_and_search(api):
    """Die Oberflaeche wird nicht von einem JS-Testlaeufer geprueft (es gibt
    keinen - Alpine laeuft vendored im Browser). Diese Pruefung haelt
    deshalb nur fest, DASS die Bausteine ausgeliefert werden, auf die das
    Markup in index.html sich stuetzt - ein Umbenennen auf einer Seite ohne
    die andere faellt hier auf."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    for name in (
        "roomKeyOf(",
        "roomChips(",
        "hasAnyRoom(",
        "visibleDevices(",
        "deviceGroups(",
        "categoryLabel(",
        "leadSignalFor(",
        "restSignalsFor(",
        "saveRoom(",
        "beginNewRoom(",
        "commitNewRoom(",
        "beginRenameRoom(",
        "commitRenameRoom(",
        "hitsOutsideRoom(",
        "clearRoomFilter(",
    ):
        assert name in script, name


async def test_the_search_never_reaches_the_server(api):
    """Die Suche laeuft ueber die ohnehin geladene Geraeteliste - es gibt
    keinen Endpunkt dafuer, und es soll auch keiner entstehen."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "/api/devices/search" not in script
    assert "/api/rooms/rename" in script


async def test_the_page_offers_the_room_bar(api):
    client, _, _ = api
    page = (await client.get("/")).text
    assert "roomChips()" in page
    assert "deviceGroups()" in page
    assert "leadSignalFor(" in page
    assert "deviceSearch" in page


async def test_every_category_has_an_icon_symbol(api):
    """Acht Kategorien, acht Symbole - "other" eingeschlossen. Ein fehlendes
    Symbol faellt im Browser NICHT auf: ein `<use>` auf eine unbekannte ID
    zeichnet stillschweigend nichts, keine Fehlermeldung. Deshalb faellt es
    hier auf."""
    client, _, _ = api
    page = (await client.get("/")).text
    for category in (
        "light",
        "socket",
        "switch",
        "covering",
        "climate",
        "sensor",
        "lock",
        "other",
    ):
        assert f'id="i-cat-{category}"' in page, category


async def test_the_tile_action_buttons_use_svg_symbols_not_raw_glyphs(api):
    """Fund 6 (Review vom 2026-09-05): Umbenennen-, Export- und
    Entfernen-Schaltflaeche zeigten die rohen Zeichen "✎", "↓" und "🗑"
    statt eines der eigenen `<symbol>`-Icons - Spec 6.5 und der Rest dieser
    Ansicht verlangen inline SVG, keine externe Icon-Bibliothek und keine
    rohen Glyphen. "🗑" rendert unter macOS/Windows zudem als farbiges
    Emoji statt eines einfarbigen Symbols und bricht damit die
    kupfer-/gedeckte Symbolsprache der uebrigen Icons.

    Task 2 (Kebab-Menue, 2026-09-05) hat Export und Entfernen aus der
    Fusszeile ins Menue verlegt und dabei zu ausgeschriebenen
    Text-Schaltflaechen gemacht - dort ist Platz fuer Beschriftung statt
    Icon. Ihre Symbole `#i-export`/`#i-remove` waren seither nirgends mehr
    eingebunden und sind mit dem Abschluss-Review vom 2026-09-05 ganz aus
    dem Symbol-Block entfernt worden. Nur die Umbenennen-Schaltflaeche
    bleibt ein Icon-Knopf und wird hier weiter geprueft; das Kebab-Symbol
    selbst deckt `test_the_tile_menu_has_its_own_icon_symbol` ab."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert "x-text=\"'✎'\"" not in page
    assert "x-text=\"'↓'\"" not in page
    assert "x-text=\"'🗑'\"" not in page
    assert 'id="i-export"' not in page
    assert 'id="i-remove"' not in page

    rename_start = page.index('class="room-rename"')
    rename_end = page.index("</button>", rename_start)
    rename_button = page[rename_start:rename_end]
    assert 'href="#i-rename"' in rename_button
    assert ":title=\"t('web.devices.room_rename')\"" in rename_button


async def test_the_tile_menu_has_its_own_icon_symbol(api):
    """Das Kebab-Symbol wird wie alle anderen inline ausgeliefert - keine
    Icon-Bibliothek, kein CDN, weil die Oberflaeche offline laeuft. Ein
    `<use>` auf eine fehlende ID zeichnet stillschweigend nichts - die
    riskante Richtung ist deshalb nicht die Symbol-Definition, sondern ihre
    Verwendung: ein `<use href="#i-kebab">` ohne passendes `<symbol
    id="i-kebab">` waere hier ebenso stillschweigend leer geblieben wie
    umgekehrt. Beide Seiten werden deshalb geprueft."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    assert 'id="i-kebab"' in page
    assert 'href="#i-kebab"' in page


async def test_the_device_grid_is_multi_column(api):
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    assert "auto-fill" in css
    assert "minmax(260px" in css


async def test_device_tiles_in_the_same_row_stretch_to_equal_height(api):
    """Nacharbeit 2026-09-05, Fund 2: `align-items: start` liess jede
    Kachel auf ihrer eigenen Inhaltshoehe stehen - im Browser gemessen
    zwei Kacheln derselben Reihe bei 277px und 271px (siehe
    Aufgabenbericht). `align-items: stretch` allein reicht nicht: es dehnt
    nur die Karte, der zusaetzliche Freiraum blieb dann als Leerraum
    UNTER der Fusszeile stehen, waehrend die kuerzere Nachbarkachel ihre
    Fusszeile direkt unters letzte Kommando zeichnet (gemessen: zwei
    Fusszeilen derselben Reihe 14px versetzt). Die Karte muss deshalb
    selbst eine Flex-Spalte werden, deren Fusszeile den Freiraum per
    `margin-top: auto` vor sich aufsaugt. Ohne Browser-Engine kann diese
    Suite die Reihen-Hoehen selbst nicht nachrechnen - belegt wird nur,
    dass `.device-grid` auf `align-items: stretch` steht und die Karte
    beides mitbringt: `display: flex; flex-direction: column` sowie ein
    `.device-foot` mit `margin-top: auto`."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    grid_rule = css.split(".device-grid {", 1)[1].split("}", 1)[0]
    assert "align-items: stretch" in grid_rule
    card_rule = css.split(".device-grid .device-card {", 1)[1].split("}", 1)[0]
    assert "display: flex" in card_rule
    assert "flex-direction: column" in card_rule
    foot_rule = css.split(".device-card > .device-foot {", 1)[1].split("}", 1)[0]
    assert "margin-top: auto" in foot_rule


async def test_the_loading_and_no_signals_hints_keep_their_pre_flex_spacing(api):
    """Fund 2 (Review vom 2026-09-05): `display: flex` auf `.device-card`
    (s.o.) hat einen zweiten, unbeabsichtigten Nebeneffekt - Flex-Items
    kollabieren ihre Margins nicht mehr untereinander. `.value-rows`
    (`margin-bottom: 0.5rem`, 8px) und die beiden bedingt sichtbaren
    `<p class="hint">`-Absaetze direkt danach (UA-Standard `margin-block:
    1em`, bei deren `font-size: 0.82rem` 13.12px) kollabierten im Browser
    gemessen vorher zu `max(8px, 13.12px)` = 13.12px, seither addieren sie
    sich zu 21.12px (siehe Aufgabenbericht) - eine Kachel im Lade- oder
    "keine funktionalen Signale"-Zustand wird dadurch sichtbar hoeher, nur
    weil sie zufaellig eine Flex-Spalte geworden ist. Ohne Browser-Engine
    kann diese Suite die tatsaechliche Luecke nicht nachrechnen - belegt
    wird nur, dass die ausgelieferte Regel den beiden betroffenen Absaetzen
    (nicht dem dritten, unabhaengigen Miniserver-Hinweis nach
    `.device-foot`) ein kompensierendes `margin-top` von `0.32rem`
    (13.12px - 8px) mitgibt, statt sie unveraendert zu lassen oder ihr
    Margin komplett auf 0 zu setzen (was die Luecke auf 8px verkleinern,
    nicht wiederherstellen wuerde)."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split(".value-rows + p.hint,\n.value-rows + p.hint + p.hint {", 1)[1].split("}", 1)[
        0
    ]
    assert "margin-top: 0.32rem" in rule


async def test_the_device_card_does_not_clip_its_overflowing_menu(api):
    """Fund 1 (Review 2026-09-05): das Kachel-Menue haengt absolut an
    `.device-card` und oeffnet nach oben ueber deren Rand hinaus. Solange
    `.device-card` (wie zuvor) `overflow: hidden` traegt, schneidet die
    Karte genau diesen Teil des Menues ab - im echten Browser gemessen
    (siehe Aufgabenbericht): `document.elementFromPoint` am oberen
    Menuerand liefert dann die Karte statt des Menues. Diese Suite hat
    keine Browser-Engine und kann das Abschneiden selbst nicht
    nachstellen - belegt wird nur, dass die ausgelieferte Regel
    `overflow: visible` traegt und nicht wieder auf `hidden` zurueckfaellt."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split(".device-card {", 1)[1].split("}", 1)[0]
    assert "overflow: visible" in rule
    assert "hidden" not in rule


async def test_the_device_card_stripe_is_rounded_on_its_own(api):
    """Der Farbstreifen (`.device-card::before`) verdankte seine gerundeten
    Ecken bislang dem `overflow: hidden` der Karte, das ihn wegschnitt statt
    ihn zu runden - dieselbe Regel, die auch das Kachel-Menue abschnitt
    (siehe `test_the_device_card_does_not_clip_its_overflowing_menu`). Mit
    `overflow: visible` (s.o.) muss der Streifen seine linksseitige Rundung
    jetzt selbst tragen, sonst stehen seine oberen/unteren linken Ecken
    eckig neben der runden Karte."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split(".device-card::before {", 1)[1].split("}", 1)[0]
    assert "border-radius" in rule


async def test_the_tile_menu_outranks_the_sticky_header(api):
    """Fund 1 (Nacharbeit 2026-09-05): `.tile-menu-items` oeffnet nach OBEN
    ueber den Kachelrand hinaus (s.o.) und kann dabei bis unter die sticky
    Kopfzeile (`header.app-header`, `z-index: 10`) reichen, sobald eine
    Kachel der ersten Reihe ihr Menue oeffnet. Im echten Browser gemessen
    (siehe Aufgabenbericht): Menue-Oberkante bei y=48, Kopfzeile-Unterkante
    bei y=56, `document.elementFromPoint` an der Menue-Oberkante lieferte
    die Kopfzeile statt des Menues - ohne Browser-Engine kann diese Suite
    das Uebermalen selbst nicht nachstellen, belegt wird nur, dass die
    ausgelieferte Regel einen hoeheren `z-index` traegt als die Kopfzeile
    und nicht wieder darunter faellt.

    Review-Fund 4 (2026-09-05, Review der Nacharbeit): dieser Vergleich
    zweier Zahlen ist eine NOTWENDIGE, aber keine HINREICHENDE Bedingung -
    ein `z-index` gilt nur innerhalb des Stacking-Kontexts seines
    Erzeugers, und `opacity`/`filter`/`transform`/`backdrop-filter`/
    `will-change`/`isolation`/`contain: paint` auf `.device-card`,
    `.device-foot` oder `.tile-menu` spannen einen solchen Kontext auf.
    Gemessen mit `opacity: 0.75` auf `.device-card` (dem Vor-Nacharbeit-
    Zustand): `document.elementFromPoint` an der Menue-Oberkante lieferte
    trotzdem die Kopfzeile, SELBST mit `z-index: 20` hier - die Karten-
    Opacity sperrte das Menue in ihren eigenen Kontext ein, in dem der
    Vergleich nie ankam. Dieser Test kennt keine Stacking-Kontexte, er
    vergleicht nur zwei Zahlen im Stylesheet - kommt einer der genannten
    Nebeneffekte zwischen `.device-card` und `.tile-menu-items` zurueck,
    faengt sich das Menue wieder unter der Kopfzeile, OHNE dass dieser
    Test (oder irgendein anderer in dieser Datei) das meldet."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    header_rule = css.split("header.app-header {", 1)[1].split("}", 1)[0]
    header_z_index = int(re.search(r"z-index:\s*(\d+)", header_rule).group(1))
    menu_rule = css.split(".tile-menu-items {", 1)[1].split("}", 1)[0]
    menu_z_index = int(re.search(r"z-index:\s*(\d+)", menu_rule).group(1))
    assert menu_z_index > header_z_index


async def test_the_tile_menu_caps_its_width_and_truncates_long_room_names(api):
    """Fund 2 (Nacharbeit 2026-09-05): `.tile-menu-item` ist `white-space:
    nowrap`, `.tile-menu-items` hatte nur ein `min-width`, keine Obergrenze
    - ein frei vergebener, langer Raumname blaeht die shrink-to-fit-Box auf
    seine volle Wortbreite auf und laesst sie, weil sie an `right: 0`
    verankert ist, nach LINKS aus der Kachel herauswachsen. Im Browser
    gemessen bei 375 px Breite mit dem Raumnamen "Werkstatt im
    Untergeschoss hinter der Heizung und dem Regal" (siehe
    Aufgabenbericht): Menue 384 px breit, linke Kante bei x=-46, das
    Dokument scrollte horizontal. Diese Suite hat keine Browser-Engine und
    kann das Layout selbst nicht nachrechnen - belegt wird nur, dass die
    ausgelieferte Regel ein `max-width` traegt und `.tile-menu-item` seinen
    Text mit Ellipse statt mit Umbruch kuerzt."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    items_rule = css.split(".tile-menu-items {", 1)[1].split("}", 1)[0]
    assert "max-width" in items_rule
    item_rule = css.split(".tile-menu-item {", 1)[1].split("}", 1)[0]
    assert "text-overflow: ellipsis" in item_rule
    assert "overflow: hidden" in item_rule
    assert "white-space: nowrap" in item_rule


async def test_the_current_room_checkmark_survives_the_width_cap(api):
    """Review-Fund 1 (2026-09-05, Review der Nacharbeit): `.tile-menu-item.
    is-current::after` haengt sein Haekchen als normalen Text ans
    Zeilenende - genau dort, wo `.tile-menu-item`s `overflow: hidden;
    text-overflow: ellipsis` (Fund 2 der Nacharbeit, s.o.) abschneidet. Im
    Browser gemessen mit dem Raumnamen "Werkstatt im Untergeschoss hinter
    der Heizung und dem Regal" (siehe Aufgabenbericht): `clientWidth`
    246px, `scrollWidth` 413px, das `::after` lag 167px hinter der
    Clip-Kante - unsichtbar, der Eintrag zeigte die Ellipse ohne jedes
    Haekchen. Ohne Browser-Engine kann diese Suite das Abschneiden selbst
    nicht nachstellen - belegt wird nur, dass die ausgelieferte Regel das
    Haekchen ausserhalb des geclippten Textflusses positioniert
    (`position: absolute` auf einem `position: relative`-Bezugsrahmen)
    statt es dem Textinhalt zu ueberlassen, und dass der aktuelle Eintrag
    dafuer Platz reserviert."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    item_rule = css.split(".tile-menu-item {", 1)[1].split("}", 1)[0]
    assert "position: relative" in item_rule
    current_rule = css.split(".tile-menu-item.is-current {", 1)[1].split("}", 1)[0]
    assert "padding-right" in current_rule
    after_rule = css.split(".tile-menu-item.is-current::after {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in after_rule


async def test_room_chip_in_the_tile_menu_carries_the_full_name_as_a_title(api):
    """Ergaenzung zu Fund 2: gekuerzt mit Ellipse bleibt der volle Raumname
    nirgends sichtbar, ausser man haelt ueber dem Eintrag oder fokussiert
    ihn - dafuer braucht der Knopf ein natives `title`. `chip.key` ist ein
    vom Nutzer vergebener Raumname, also Daten und keine zu uebersetzende
    Oberflaechen-Zeichenkette - anders als der Rest der Menuetexte laeuft er
    bewusst NICHT durch `t()`. Diese Suite prueft nur, dass das Attribut
    ausgeliefert wird, nicht dass der Browser es beim Hover tatsaechlich
    anzeigt."""
    client, _, _ = api
    page = (await client.get("/")).text
    # `roomChips()` wird zweimal per `x-for` durchlaufen: einmal im nativen
    # `<select>` von "Commission a new device", einmal im Kachel-Menue -
    # gesucht wird deshalb erst ab `.tile-menu-items`, sonst traf der erste
    # (falsche) Treffer und der Test haette nie etwas belegt.
    menu_start = page.index('class="tile-menu-items"')
    chip_start = page.index('x-for="chip in roomChips()', menu_start)
    chip_end = page.index("</template>", chip_start)
    chip_block = page[chip_start:chip_end]
    assert ':title="chip.key"' in chip_block


async def test_offline_dimming_stays_off_the_tile_menu(api):
    """Fund 3 (Nacharbeit 2026-09-05): `.device-card.is-offline { opacity:
    0.75 }` spannte einen Deckkraft-Kontext ueber die GANZE Karte auf, das
    Kachel-Menue eingeschlossen - es haengt als Nachfahre im `.device-foot`.
    Ein offline Geraet dimmte damit sein eigenes, absolut ueber die
    Nachbarkachel schwebendes Menue auf 75 % mit, durch das dann die
    Nachbarkachel sichtbar durchschien. Ohne Browser-Engine kann diese
    Suite das Durchscheinen selbst nicht nachstellen - belegt wird nur, dass
    `.device-card.is-offline` KEIN pauschales `opacity` mehr traegt und
    `.tile-menu` in der stattdessen gezielten Dimmung explizit ausgenommen
    ist."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    # Die pauschale Karten-Regel `.device-card.is-offline { opacity: ... }`
    # gibt es nicht mehr - nur noch als Selektor-Praefix vor `::before` oder
    # `>`. Ein bare `{` direkt danach waere die alte, verworfene Fassung.
    assert ".device-card.is-offline {" not in css
    assert ".device-card.is-offline > *:not(.device-foot) {" in css
    assert ".device-card.is-offline .device-foot > *:not(.tile-menu) {" in css


async def test_offline_stripe_keeps_its_dimmed_look_on_its_own(api):
    """Ergaenzung zu Fund 3: mit der Dimmung weg von `.device-card` dimmt
    sich der Farbstreifen (`.device-card::before`) nicht mehr automatisch
    als deren Nachfahre mit - ohne eigene Regel saehe er in einer offline
    Kachel ploetzlich saettigter aus als vorher. `.device-card.is-offline::
    before` muss das `opacity: 0.75` deshalb jetzt selbst tragen."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    stripe_rule = css.split(".device-card.is-offline::before {", 1)[1].split("}", 1)[0]
    assert "opacity: 0.75" in stripe_rule


async def test_offline_dimming_reaches_the_kebab_trigger_too(api):
    """Review-Fund 2 (2026-09-05, Review der Nacharbeit): die Ausnahme
    `.device-card.is-offline .device-foot > *:not(.tile-menu)` nimmt das
    GESAMTE `<details class="tile-menu">` aus, nicht nur die transiente,
    sich oeffnende Liste - eingeschlossen dessen permanent sichtbaren
    `<summary>`-Kebab. Im Browser gemessen an einer offline Karte:
    `.device-foot` Opacity 1, `.tile-menu > summary` Opacity 1, waehrend
    der `.hint` daneben bei 0.75 lag - der Kebab-Rahmen wirkte dadurch
    heller als alles drumherum. Ohne Browser-Engine kann diese Suite den
    Helligkeitsunterschied selbst nicht nachstellen - belegt wird nur, dass
    der Ausloeser eine eigene, auf ihn gezielte `opacity: 0.75`-Regel
    bekommen hat, statt weiterhin unbehandelt zu bleiben."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    summary_rule = css.split(".device-card.is-offline .tile-menu > summary {", 1)[1].split("}", 1)[
        0
    ]
    assert "opacity: 0.75" in summary_rule


async def test_offline_footer_divider_matches_the_dimmed_commands_divider(api):
    """Review-Fund 3 (2026-09-05, Review der Nacharbeit): `.device-commands`
    und `.device-foot` ziehen beide `border-top: 1px solid var(--border)`.
    Bei einem offline Geraet dimmt die erste Trennlinie als Teil von
    `.device-commands` (ein direktes, komplett gedimmtes Kartenkind, s.o.)
    mit, die zweite nicht - `.device-foot` selbst bleibt von der Dimmung
    ausgenommen (nur seine Kinder ausser `.tile-menu` werden gedimmt), sein
    Rahmen malt also ein paar Pixel darunter in voller Staerke weiter.
    `.device-foot` als Ganzes zu dimmen scheidet aus, das traefe
    `.tile-menu` als Nachfahren gleich mit. Ohne Browser-Engine kann diese
    Suite den Helligkeitsunterschied selbst nicht nachstellen - belegt wird
    nur, dass die ausgelieferte Regel NUR die Randfarbe dimmt (`color-mix`
    auf `border-top-color`), nicht die Deckkraft des ganzen Elements."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    foot_offline_rule = css.split(".device-card.is-offline .device-foot {", 1)[1].split("}", 1)[0]
    assert "border-top-color" in foot_offline_rule
    assert "color-mix" in foot_offline_rule
    assert "75%" in foot_offline_rule
    # Darf NICHT die Deckkraft des ganzen `.device-foot` (und damit seines
    # Nachfahren `.tile-menu`) mitziehen - genau der Fehler, den die
    # `:not(.tile-menu)`-Ausnahme oben verhindern soll.
    assert "opacity" not in foot_offline_rule


async def test_the_footer_pill_wraps_onto_its_own_line_below_the_kebab(api):
    """Der Kebab teilt sich die erste Fusszeilen-Zeile mit dem Export-
    Hinweis, die Pille bricht darunter um (2026-09-06).

    `order: 1` allein reichte nicht: passt bei einer breiten Kachel alles
    in eine Zeile, ueberholt die Pille den Kebab und der steht mitten in
    der Fusszeile statt rechts. `flex-basis: 100%` zwingt die Pille immer
    auf eine eigene Zeile, womit die Aufteilung bei jeder Kachelbreite
    dieselbe ist. Beide Eigenschaften gehoeren zusammen - deshalb prueft
    dieser Test beide, nicht nur die auffaelligere.

    Reiner Auslieferungsbeleg: geprueft wird die Regel im ausgelieferten
    Stylesheet, nicht das Ergebnis im Browser. Wie sie sich tatsaechlich
    auswirkt, ist bei 300, 460 und 526 px Kachelbreite von Hand gemessen
    worden (siehe Commit-Nachricht)."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split(".device-foot > .status-pill {", 1)[1].split("}", 1)[0]
    assert "order: 1" in rule
    assert "flex-basis: 100%" in rule


async def test_the_changed_pill_now_lives_in_the_tile_footer(api):
    """Pille-in-die-Fusszeile-Umbau (2026-09-06): die Geaendert-seit-Export-
    Pille sass bisher in der Kopfzeile (`.device-ident`), wo sie laut dem
    inzwischen entfernten Entwurf-6.2-Kommentar das Leitwert-Label
    verdraengte. Sie steht jetzt in `.device-foot`, direkt neben
    `exportHintFor` - derselbe Exportzustand, dieselbe Zeile, statt Streit
    um die schmale Zeile unter dem Geraetenamen. Die Reihenfolge Hinweis-
    dann-Pille spiegelt den Fliesstext ("Zuletzt exportiert am ... und
    seither geaendert"), nicht umgekehrt.

    Reiner Auslieferungsbeleg (kein Browser-Lauf, kein Alpine-Rendering):
    geprueft wird die Position der Textmarken im ausgelieferten Markup,
    nicht das tatsaechliche Layout - das bestaetigt nur, was ausgeliefert
    wird, nichts ueber Umbruch oder Ueberlauf im Browser (siehe die
    manuelle Pruefung im Aufgabenbericht fuer 261 px/1440 px)."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    foot_pos = markup.index('class="device-foot"')
    hint_pos = markup.index('x-text="exportHintFor(device.id)"')
    pill_pos = markup.index("x-text=\"t('web.devices.changed_since_export')\"")
    offline_pos = markup.index("x-text=\"t('web.devices.offline')\"")
    # Die Pille steht nach dem Fusszeilen-Anfang und nach dem Export-
    # Hinweis - nicht mehr vor `.device-foot` in der Kopfzeile.
    assert foot_pos < hint_pos < pill_pos
    # "Offline" bleibt in der Kopfzeile, also VOR der Fusszeile: das ist
    # Geraetezustand, kein Exportzustand, und bleibt dort, wo der Blick
    # zuerst hinfaellt.
    assert offline_pos < foot_pos

    # Die Pille haengt NICHT mehr an `isOnline` (2026-09-06). Diese Kopplung
    # stammte aus der Kopfzeile, wo sich Pille und Offline-Marke eine Zeile
    # teilten; unten konkurriert nichts, und ob ein Geraet seit dem Export
    # geaendert wurde, ist unabhaengig davon, ob es gerade antwortet. Ohne
    # diese Zusicherung koennte ein spaeterer Umbau die Bedingung
    # stillschweigend wieder mitschleppen - und ein offline stehendes Geraet
    # mit ausstehendem Export saehe wieder aus wie eines ohne, weil auch der
    # Randstreifen `is-changed` an `isOnline` koppelt.
    # Rueckwaerts auf die PILLE ankern, nicht auf das naechste `<span`: das
    # waere das innere Text-Span, das den Beschriftungsschluessel traegt.
    pill_open = markup.rindex('<span class="status-pill warn"', 0, pill_pos)
    pill_tag = markup[pill_open : markup.index(">", pill_open)]
    assert "changedSinceExport(device.id)" in pill_tag
    assert "isOnline" not in pill_tag


async def test_the_lead_label_only_yields_to_the_offline_pill_now(api):
    """Folgeaenderung desselben Umbaus: die Bedingung
    `x-show="isOnline(device) && !changedSinceExport(device.id) &&
    leadSignalFor(device.id)"` galt nur, solange die Geaendert-Pille noch
    in der Kopfzeile stand und sich mit dem Leitwert-Label dieselbe Zeile
    teilte. Mit der Pille in der Fusszeile (s.
    `test_the_changed_pill_now_lives_in_the_tile_footer`) hat ein
    geaendertes, aber online Geraet die Zeile fuer sich - das Label muss
    wieder erscheinen. Nur die Offline-Pille beansprucht die Zeile noch."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "!changedSinceExport(device.id)" not in markup
    assert (
        'x-show="isOnline(device) && leadSignalFor(device.id)"\n'
        '                        x-text="leadSignalFor(device.id)?.title"' in markup
    )


async def test_command_row_wrappers_do_not_stack_their_sibling_margin(api):
    """Nacharbeit 2026-09-05, Fund 3: jeder Befehl steckt in einem eigenen
    `<span class="row">`-Wrapper (index.html), und `.row + .row {
    margin-top: 0.5rem }` (weiter oben in dieser Datei) ist fuer vertikal
    GESTAPELTE Zeilen gedacht. In `.device-commands` sitzen die Wrapper
    aber NEBENEINANDER in derselben zentrierten Flex-Zeile - ein
    Top-Margin schiebt einen Wrapper darin nicht nach unten, sondern
    innerhalb der zentrierten Zeile nach OBEN. Im Browser gemessen: alle
    drei Befehlsknoepfe sind gleich hoch (30.9px), der erste (margin-lose)
    stand aber bei `top: 976.1`, die beiden folgenden bei `top: 980.1`
    (siehe Aufgabenbericht). Ohne Browser-Engine kann diese Suite den
    Versatz selbst nicht nachrechnen - belegt wird nur, dass
    `.device-commands` das Stapel-Margin gezielt auf 0 setzt, statt das
    Element (das Flex/`gap`/Zentrierung weiterhin braucht) ganz von `.row`
    zu loesen."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    override_rule = css.split(".device-commands > .row + .row {", 1)[1].split("}", 1)[0]
    assert "margin-top: 0;" in override_rule
    # Die allgemeine, fuer vertikale Stapel gedachte Regel muss unangetastet
    # bleiben - der Fix ist ein gezielter Override, keine Streichung. Wie
    # jede andere Regelpruefung in dieser Datei wird dafuer der Regelkoerper
    # extrahiert statt der komplette Block samt Klammern und Einrueckung zu
    # matchen (Fund 3, Review vom 2026-09-05) - ein Kommentar in der Regel
    # oder eine andere Reihenfolge der Deklaration duerfte diesen Test nicht
    # brechen, ohne dass sich am Verhalten etwas aendert.
    base_rule = css.split(".row + .row {", 1)[1].split("}", 1)[0]
    assert "margin-top: 0.5rem;" in base_rule


async def test_lead_value_gets_padding_room_for_descenders(api):
    """Fund 4 (Nacharbeit 2026-09-05): `.lead-value` schneidet bei
    `line-height: 1.05` und `overflow: hidden` die Unterlaengen textwertiger
    Leitwerte (`g`/`y`/`p`/`q`) um ein Pixel ab - im Browser gemessen am
    Text `gypq`: `scrollHeight` 24px gegen `clientHeight` 23px. Zahlen ohne
    Unterlaengen sind nicht betroffen.

    Gemessene Wahl: `padding-block` statt einer hoeheren `line-height` -
    Letzteres haette die Zeilenbox und damit die Feldhoehe JEDER Kachel
    vergroessert (auch rein numerischer), `padding-block` zaehlt dagegen
    innerhalb der `overflow: hidden`-Clip-Box (die am Padding-Rand
    schneidet) und schafft nur dort zusaetzlichen Raum. Ohne Browser-Engine
    kann diese Suite `scrollHeight`/`clientHeight` selbst nicht nachrechnen
    (siehe Aufgabenbericht fuer die Messung) - belegt wird nur, dass die
    ausgelieferte Regel ein `padding-block` traegt und `line-height`
    unveraendert bei `1.05` bleibt."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split(".lead-value {", 1)[1].split("}", 1)[0]
    assert "padding-block" in rule
    assert "line-height: 1.05" in rule


async def test_lead_value_does_not_yield_to_the_device_name(api):
    """Nacharbeit 2026-09-05, Fund 1: `flex: 0 1 auto` plus `min-width: 0`
    liess `.lead-value` schon im GEWOEHNLICHEN Fall neben `.device-name`
    schrumpfen, nicht erst im pathologischen, den beide Regeln eigentlich
    eindaemmen sollten - im Browser gemessen bei 1440px: `12.4 %`
    `clientWidth` 64px gegen `scrollWidth` 73px, `true` 51px gegen 54px,
    beide ohne jeden Platzmangel gekuerzt (siehe Aufgabenbericht). Ohne
    Browser-Engine kann diese Suite das Kuerzen selbst nicht nachrechnen -
    belegt wird nur, dass die ausgelieferte Regel das Schrumpfen abstellt
    (`flex: 0 0 auto`), die Absicherung gegen einen pathologisch langen
    Wert stattdessen an ein `max-width` verlegt, und `min-width` (das ohne
    `flex-shrink: 1` keine Funktion mehr haette) nicht mehr traegt.

    Fund 1 (Review vom 2026-09-05): der urspruengliche Deckel von `60%`
    liess an der dokumentierten Grid-Untergrenze (261 px) den gesamten
    Platzmangel beim Namen landen - im Browser gemessen 134 px Leitwert
    gegen nur noch 43 px Name, dort ohne Ellipse mitten im Buchstaben
    gekappt (siehe Aufgabenbericht). Der Deckel ist deshalb auf `50%`
    gesenkt: der Leitwert weicht weiterhin nicht, darf aber hoechstens die
    Haelfte der Kopfzeile beanspruchen, der Rest gehoert dem Namen. Ohne
    Browser-Engine kann diese Suite die tatsaechliche Aufteilung nicht
    nachrechnen - belegt wird nur der genaue Deckelwert."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split(".lead-value {", 1)[1].split("}", 1)[0]
    assert "flex: 0 0 auto" in rule
    assert "max-width: 50%" in rule
    assert "min-width" not in rule
    assert "overflow: hidden" in rule
    assert "text-overflow: ellipsis" in rule


async def test_device_name_truncates_with_an_ellipsis_instead_of_clipping(api):
    """Fund 1 (Review vom 2026-09-05): mit dem auf `50%` gesenkten Deckel an
    `.lead-value` (s.o.) traegt der Name bei der Grid-Untergrenze immer
    noch die Kuerzung - nur jetzt nicht mehr die vollstaendige. Ein
    `<input>` clippt seinen Text intern, sobald er nicht passt, und zwar
    OHNE jedes Zeichen, das anzeigt, dass Text fehlt, solange kein
    `text-overflow` gesetzt ist - im Browser gemessen bei 261 px Kachel-
    breite: Name 65 px, mitten im Buchstaben abgeschnitten, wo `overflow:
    hidden` plus `text-overflow: ellipsis` stattdessen sichtbar kuerzen
    (siehe Aufgabenbericht). Ohne Browser-Engine kann diese Suite das
    Kuerzen selbst nicht nachrechnen - belegt wird nur, dass die
    ausgelieferte Regel beide Eigenschaften traegt."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split(".device-head .device-name {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in rule
    assert "text-overflow: ellipsis" in rule


async def test_reconcile_room_filter_falls_back_to_all_when_the_filtered_room_vanishes(api):
    """Fund 2 (Review vom 2026-09-05), zwei Runden: Verschiebt man ueber das
    Kachel-Menue das letzte Geraet eines gefilterten Raums in einen
    anderen, oder benennt/vereint man den gefilterten Raum weg, aendert
    `roomChips()` sich - `roomFilter` selbst aber nicht, ohne dass etwas
    das nachzieht. Der gefilterte Raum existiert dann nicht mehr, kein Chip
    ist mehr aktiv, keine Kachel mehr sichtbar, und der (nur an `roomFilter`
    haengende) Umbenennen-Stift zeigt weiter auf einen Raum, der bei einem
    Klick 404 liefern wuerde.

    Die ERSTE Fassung dieses Fixes schloss "Ohne Raum" (`roomFilter ===
    ""`) noch bewusst aus, mit der (fuer den Umbenennen-Stift richtigen)
    Begruendung, der zeige sich fuer "Ohne Raum" ohnehin nie. Das Re-Review
    (2026-09-05) zeigte den blinden Fleck: "Ohne Raum" ist der Filter, in
    dem jemand ein unsortiertes Geraet nach dem anderen einem Raum zuweist
    - genau das laesst den "Ohne Raum"-Chip aus `roomChips()`
    verschwinden, sobald das letzte Geraet versorgt ist, mit denselben
    Symptomen (kein Chip aktiv, keine Kachel, kein Ausweg) wie beim echten
    Raumnamen. Der Guard prueft deshalb jetzt nur noch auf "Alle" (`null`)
    - fuer den gibt es nichts zu tun, `roomChips()` kennt dafuer ohnehin
    keinen Chip.

    Belegt wird, dass die Methode existiert, ihr Guard wie beschrieben nur
    noch `null` ausschliesst (nicht mehr `""`), und dass sowohl `saveRoom`
    als auch `commitRenameRoom` sie nach ihrem jeweiligen Schreibvorgang
    aufrufen - nicht, dass Alpine daraufhin tatsaechlich auf den
    "Alle"-Chip umschaltet (dafuer braeuchte es eine Browser-Engine, siehe
    `test_the_page_does_not_call_init_a_second_time`)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    reconcile_start = script.index("reconcileRoomFilter() {")
    reconcile_end = script.index("\n    },", reconcile_start)
    reconcile_body = script[reconcile_start:reconcile_end]
    assert "this.roomFilter === null" in reconcile_body
    assert 'this.roomFilter === ""' not in reconcile_body
    assert 'typeof this.roomFilter !== "string"' not in reconcile_body
    assert "this.roomFilter = null;" in reconcile_body

    save_room_start = script.index("async saveRoom(device, value) {")
    save_room_end = script.index("\n    },", save_room_start)
    save_room_body = script[save_room_start:save_room_end]
    assert "this.reconcileRoomFilter();" in save_room_body

    rename_start = script.index("async commitRenameRoom() {")
    rename_end = script.index("\n    },", rename_start)
    rename_body = script[rename_start:rename_end]
    assert "this.reconcileRoomFilter();" in rename_body


async def test_the_command_bar_distinguishes_loading_from_genuinely_empty(api):
    """Fund 3 (Review vom 2026-09-05): Der Kachel-Umbau hatte die beiden
    Hinweise verloren, die frueher auf `controlsLoaded(device.id)` gattert
    waren, und der nachfolgende i18n-Aufraeumdurchgang loeschte daraufhin
    konsequent - aber verfrueht - die dazugehoerigen Schluessel
    `web.devices.controls_loading` und `web.devices.no_known_commands`.
    `controlsLoaded()` blieb als tote Funktion zurueck, deren eigener
    Docstring ihre Notwendigkeit begruendete (Spec 8.1: ein Fehlschlag darf
    nicht als harmloser Zustand erscheinen) - ohne dass irgendetwas sie
    noch aufrief. Ohne die Unterscheidung rendert ein Geraet, dessen
    `/controls`-Abruf fehlgeschlagen oder noch nicht fertig ist, denselben
    leeren, umrandeten Streifen wie ein Geraet ganz ohne Befehle: die
    stillschweigend falsche Anzeige, die Spec 8.1 gerade ausschliessen
    will.

    Analog fuer Signale: `web.devices.no_functional_signals` wurde
    ebenfalls geloescht, wodurch eine Kachel mit geladenen, aber leeren
    funktionalen Signalen (`leadSignalFor` liefert `null`) zwischen
    Kopfzeile und Befehlsleiste stillschweigend eine Luecke zeigte -
    ununterscheidbar von einer noch ladenden Kachel.

    Belegt wird, dass beide Unterscheidungen wieder ausgeliefert werden und
    `controlsLoaded` dabei tatsaechlich (wieder) verwendet wird - nicht,
    dass Alpine sie im Browser korrekt umschaltet."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text

    assert "controlsLoaded(device.id)" in page
    assert "t('web.devices.controls_loading')" in page
    assert "t('web.devices.no_known_commands')" in page
    assert "t('web.devices.no_functional_signals')" in page

    commands_start = page.index('class="device-commands"')
    commands_end = page.index("</div>", commands_start)
    device_commands = page[commands_start:commands_end]
    assert 'x-show="!controlsLoaded(device.id)"' in device_commands
    assert (
        'x-show="controlsLoaded(device.id) && commandsFor(device.id).length === 0'
        in device_commands
    )

    controls_loaded_start = script.index("controlsLoaded(deviceId) {")
    controls_loaded_end = script.index("\n    },", controls_loaded_start)
    assert controls_loaded_start < controls_loaded_end


async def test_the_tile_menu_is_a_native_details_that_closes_four_ways(api):
    """`<details>` haelt den Auf-/Zu-Zustand im DOM - der Grund, warum das
    Menue ueberhaupt so gebaut ist (siehe Entwurf, Abschnitt 4). Drei der
    vier Schliesswege muessen dennoch von Hand kommen: `<details>` schliesst
    weder bei einem Klick daneben noch bei Escape von selbst, und Enter im
    Neu-Raum-Feld committet den Entwurf statt bloss zu schliessen. Der
    vierte, der Klick auf einen Eintrag (Raum, Export, Entfernen), steht an
    den Eintraegen selbst.

    Geprueft wird ausschliesslich im Kachel-Menue-Block, nicht seitenweit:
    `@keydown.escape` (ohne `.window`) steht auch am Raum-Umbenennen-Feld
    (`room-rename-input`) - ein seitenweiter `in page`-Test bliebe selbst
    dann gruen, wenn der eigene Escape-Wachposten des Menues geloescht
    wuerde, solange irgendwo sonst auf der Seite `@keydown.escape`
    vorkommt. Die uebrigen Wege laufen ueber `closeTileMenu($el)`, das
    JEDER Eintrag beim Klick UND der Enter-Handler des Neu-Raum-Felds
    aufrufen (siehe `closeTileMenu` in app.js, das dort tatsaechlich
    `open = false` setzt)."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert 'class="tile-menu"' in page
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    assert "@click.outside" in menu
    assert "@keydown.escape" in menu
    assert "closeTileMenu($el)" in menu

    close_start = script.index("closeTileMenu(el) {")
    close_end = script.index("\n    },", close_start)
    close_body = script[close_start:close_end]
    assert "open = false" in close_body


async def test_the_tile_menu_resets_new_room_mode_on_every_close(api):
    """Fund 1 (Abschluss-Review 2026-09-05): `newRoomFor` schaltet nur noch
    die Sichtbarkeit des Neu-Raum-Textfelds, blieb dabei aber auf drei von
    vier Schliesswegen unveraendert stehen - nur Enter und das Escape IM
    Feld setzten es zurueck (siehe deren `@keydown`-Handler weiter unten in
    dieser Datei). Ausserhalb geklickt, Escape im Menue oder ein Klick auf
    einen Raumeintrag liessen `newRoomFor` auf der Geraete-ID stehen, obwohl
    das Menue sichtbar zu ist: beim naechsten Oeffnen zeigte die Kachel ein
    leeres Neu-Raum-Feld statt des "+ Neuer Raum"-Knopfes - im Browser
    gemessen: `newRoomFor` blieb nach einem Aussenklick auf der ID des
    Geraets stehen, und beim Wiedereroeffnen war
    `newRoomButtonVisible: false, strayInputVisible: true`.

    Der Fix haengt an `@toggle` statt an jedem der vier Schliesswege
    einzeln: `<details>` feuert `toggle` bei JEDER Zustandsaenderung, gleich
    aus welchem Grund, und ist damit die einzige Stelle, die den
    Neu-Raum-Zustand noch kennen muss.

    Fund 5 (Re-Review 2026-09-05): fixierte diese Pruefung noch den
    gesamten Inline-Ausdruck zeichengenau - Leerzeichen und
    Anfuehrungszeichen eingeschlossen. Der naheliegende naechste Umbau
    (den Ausdruck wie `closeTileMenu` in eine Methode ziehen) haette sie
    bei unveraendertem Verhalten zerschossen. Geprueft wird deshalb nur
    noch die Absicht: das `<details>` traegt ein `@toggle`, und dessen
    Ausdruck setzt bei Uebereinstimmung mit der Geraete-ID `newRoomFor`
    und `newRoomDraft` zurueck - nicht mehr, in welcher exakten Schreibweise
    das geschieht.

    Fund 6 (Selbstverteidigungs-Review 2026-09-05): die drei Substring-
    Assertions unten pruefen nur, dass die drei Bestandteile IRGENDWO im
    Ausdruck auftauchen. Baut man die urspruengliche, in Fund 3 (siehe
    Kommentar ueber dem `<details>` in index.html) verworfene Fassung
    `!$el.open && newRoomFor === device.id` wieder ein, enthaelt der
    Ausdruck `newRoomFor === device.id`, `newRoomFor = null` und
    `newRoomDraft = ''` weiterhin woertlich - alle drei Assertions blieben
    gruen, obwohl genau die Luecke zurueck waere, die Fund 3 geschlossen
    hat: verschwindet eine Kachel mit offenem Menue (Re-Render entfernt den
    Knoten ersatzlos), feuert nie ein schliessendes `toggle`, `newRoomFor`
    bleibt auf der Geraete-ID stehen, und `!$el.open` verwirft dann das
    naechste `toggle` fuer dieselbe ID (ein Oeffnen) als Reset-Anlass - das
    verwaiste Neu-Raum-Feld ist wieder da. Nur eine eigene Negativ-Assertion
    auf `!$el.open` macht diese Abwesenheit explizit pruefbar."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    assert "@toggle=" in menu
    toggle_value_start = menu.index('@toggle="') + len('@toggle="')
    toggle_value_end = menu.index('"', toggle_value_start)
    toggle_expr = menu[toggle_value_start:toggle_value_end]
    assert "newRoomFor === device.id" in toggle_expr
    assert "newRoomFor = null" in toggle_expr
    assert "!$el.open" not in toggle_expr
    assert "newRoomDraft = ''" in toggle_expr


async def test_the_tile_menu_escape_listener_is_window_scoped(api):
    """Fund 2 (Review 2026-09-05): `@keydown.escape` ohne `.window` haengt am
    `<details>`-Teilbaum und feuert nur, wenn der Fokus dort drinsteht.
    Safari auf macOS setzt den Fokus bei einem Mausklick auf `<summary>`
    NICHT dorthin (Standard, "Volle Tastatursteuerung" aus) - danach landet
    Escape auf `document.body` und erreicht das `<details>` nie, das Menue
    bleibt lautlos offen. `.window` haengt den Listener stattdessen an
    `window` und wirkt unabhaengig vom Fokus. Ohne Browser-Engine kann diese
    Suite das Fokusverhalten selbst nicht nachstellen - belegt wird nur,
    dass die ausgelieferte Seite die fokusunabhaengige Form traegt und
    nicht wieder auf die element-gebundene zurueckfaellt."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    assert '@keydown.escape.window="if ($el.open) closeTileMenu($el)"' in menu


async def test_close_tile_menu_restores_focus_conditionally_without_scrolling(api):
    """Selbstverteidigungs-Review 2026-09-05, Fund 3: `closeTileMenu` (app.js)
    holt sich den Fokus nur zurueck, wenn er beim Schliessen tatsaechlich IM
    Menue stand (`hadFocus`), und tut das mit `{ preventScroll: true }`. Bis
    hierher gab es dafuer keine einzige Assertion in dieser Datei -
    `grep -c "preventScroll|hadFocus|tile-menu-new"` liefert vor diesem Test
    0. Ein Refactor haette beides unbemerkt kippen koennen: liesse man die
    `hadFocus`-Wache weg, risse ein Aussenklick oder Escape ausserhalb des
    Menues (die `@click.outside`/`@keydown.escape.window`-Handler rufen
    `closeTileMenu` unbedingt fuer JEDE Kachel auf, s. Test oben) den Fokus
    von genau dem Element weg, das der Nutzer gerade angeklickt hat - selbst
    wenn dessen Menue laengst zu ist. Fehlte `preventScroll`, spraenge die
    Seite bei jedem programmatischen Fokus-Ruf zur `<summary>` zurueck, auch
    wenn die zugehoerige Kachel laengst aus dem sichtbaren Bereich
    gescrollt ist.

    Dies ist eine Assertion gegen die AUSGELIEFERTE Datei, keine
    Verhaltenspruefung: sie belegt, dass beide Bausteine im Quelltext von
    `closeTileMenu` stehen, nicht, dass sich der Browser beim Ausfuehren
    tatsaechlich so verhaelt (dafuer fehlt dieser Suite eine echte
    Browser-Engine, siehe Test oben)."""
    client, _store, _device_id = api
    script = (await client.get("/static/app.js")).text
    close_start = script.index("closeTileMenu(el) {")
    close_end = script.index("\n    },", close_start)
    close_body = script[close_start:close_end]
    assert "hadFocus" in close_body
    assert "preventScroll" in close_body


async def test_the_new_room_escape_handler_focuses_the_new_room_button(api):
    """Selbstverteidigungs-Review 2026-09-05, Fund 3: Escape im Neu-Raum-
    Textfeld blendet das Feld per `x-show` aus, OHNE ihm dabei den Fokus zu
    nehmen (Fund 2, Kommentarblock ueber dem `<details>` in index.html) -
    der Handler muss den Fokus deshalb selbst auf den wieder auftauchenden
    "+ Neuer Raum"-Knopf (`.tile-menu-new`) schicken. Ohne diese Assertion
    koennte der Fokus-Ruf ersatzlos aus dem Escape-Handler verschwinden, und
    die Suite bliebe gruen - der Fokus bliebe dann auf dem unsichtbaren,
    aus dem Tab-Fluss gefallenen Textfeld stranden, ein totes Ende fuer
    Tastatur- und Screenreader-Nutzung.

    Auch dies ist eine Assertion gegen die AUSGELIEFERTE Datei: sie belegt,
    dass der Fokus-Ruf im Quelltext steht, nicht, dass der Browser den
    Fokus beim tatsaechlichen Escape-Druck auch dorthin bewegt."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    assert "@keydown.escape.stop=" in menu
    escape_value_start = menu.index('@keydown.escape.stop="') + len('@keydown.escape.stop="')
    escape_value_end = menu.index('"', escape_value_start)
    escape_expr = menu[escape_value_start:escape_value_end]
    assert "querySelector('.tile-menu-new')" in escape_expr
    assert ".focus()" in escape_expr


async def test_export_and_remove_moved_into_the_tile_menu(api):
    """Beide Aktionen liegen jetzt im Menue und schliessen es beim Klick.
    Der Export-Knopf bleibt an `bridgeSettings.bridge_ip` gebunden - ohne
    hinterlegte Bruecken-IP gibt es nichts zu exportieren."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    assert "exportDevice(device)" in menu
    assert "removeDevice(device)" in menu
    assert "!bridgeSettings.bridge_ip" in menu


async def test_the_rooms_are_menu_entries_and_the_select_is_gone(api):
    """Die Raumzuweisung ist jetzt eine Liste von Eintraegen im Menue. Das
    `<select>` und die gesamte Mechanik, die noetig war, um seinen
    angezeigten Wert mit `device.room` in Deckung zu halten, entfaellt
    ersatzlos - genau darum geht es bei diesem Umbau."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text

    assert "menu_room_heading" in page
    assert "saveRoom(device, chip.key)" in page
    assert "beginNewRoom(device)" in page

    assert "room-select" not in page
    assert "room-picker" not in page
    for gone in ("roomSelectDrafts", "syncRoomSelectDraft", "onRoomSelectChange"):
        assert gone not in script, gone


async def test_the_current_room_is_marked_for_assistive_tech_too(api):
    """Das Haekchen am aktuellen Raum ist rein grafisch. `aria-current`
    traegt dieselbe Auskunft fuer alles, was die Seite nicht sieht - ohne
    das waere der aktuelle Raum im Menue nur eine von mehreren gleich
    aussehenden Zeilen.

    Geprueft wird im Kachel-Menue-Block, nicht seitenweit - ein blosses
    `"aria-current" in page` waere schon durch irgendeine zukuenftige,
    voellig unabhaengige Verwendung des Attributs anderswo auf der Seite
    zufrieden, ohne dass hier ueberhaupt etwas markiert waere. Belegt wird
    ausserdem, dass das Attribut tatsaechlich an `roomKeyOf(device)` haengt
    - demselben Vergleich, der auch `.is-current` (das sichtbare Haekchen)
    steuert - und nicht an einem unabhaengigen, potenziell
    auseinanderlaufenden Ausdruck."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    assert ":aria-current=\"roomKeyOf(device) === '' ? 'true' : null\"" in menu
    assert ":aria-current=\"roomKeyOf(device) === chip.key ? 'true' : null\"" in menu


async def test_the_kebab_button_names_itself_via_aria_label_and_hides_its_icon(api):
    """A11y-Nacharbeit (2026-09-05), Fund 1: der Kebab-Knopf trug bisher nur
    `title`, und `title` ist bei der Berechnung des zugaenglichen Namens
    (accessible name) lediglich der letzte Rueckfallwert - Screenreader
    verlassen sich darauf nicht zuverlaessig. Der Spec-Text zu diesem Menue
    nannte `web.devices.menu` bereits "der zugaengliche Name des
    ⋮-Knopfs" (siehe Kommentar ueber den Keys in strings.yaml), was bis zu
    diesem Fix schlicht nicht stimmte. `aria-label` traegt jetzt denselben
    uebersetzten Wert, `title` bleibt zusaetzlich als Maus-Tooltip stehen -
    unterschiedliche Zwecke, beide Attribute duerfen nebeneinander stehen.

    Das `<svg>` bekommt zugleich `aria-hidden="true"`: ein dekoratives Icon
    in einem bereits benannten Element darf dem Accessibility-Baum keinen
    zweiten, konkurrierenden Namen liefern. Dies ist eine Assertion gegen
    die AUSGELIEFERTE Datei, keine Verhaltenspruefung - eine echte
    Browser-Engine fuer die tatsaechliche Namensberechnung fehlt dieser
    Suite (siehe die uebrigen Kachel-Menue-Tests in dieser Datei)."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    summary = menu.split("<summary", 1)[1].split("</summary>", 1)[0]
    assert ":title=\"t('web.devices.menu')\"" in summary
    assert ":aria-label=\"t('web.devices.menu')\"" in summary
    assert 'aria-hidden="true"' in summary


async def test_the_room_list_is_a_labelled_group_for_assistive_tech(api):
    """A11y-Nacharbeit (2026-09-05), Fund 2: die Ueberschrift
    `.tile-menu-heading` war rein optisch ein Abschnittslabel fuer die
    Raumeintraege - ohne programmatische Verbindung hoert ein
    Screenreader-Nutzer beim Durchtabben nur "Kueche, aktueller Eintrag"
    ohne jede Auskunft, dass das ein Raum ist. `role="group"` plus
    `aria-labelledby` auf einem neuen `.tile-menu-rooms`-Wrapper stellt
    diese Verbindung her.

    Die id der Ueberschrift ist bewusst PRO GERAET abgeleitet
    (`'tile-menu-room-heading-' + device.id`) statt eine feste Konstante:
    die ganze Seite teilt sich ein einziges `x-data`, und dieses Markup
    wird einmal PRO Kachel gerendert - eine feste id waere im
    ausgelieferten Dokument so oft dupliziert wie es Kacheln gibt, und
    `aria-labelledby` traefe dann nur das erste Vorkommen. Die Suche prueft
    deshalb ausdruecklich den `device.id`-Ausdruck, nicht nur, dass
    irgendeine id existiert - ein Rueckbau auf eine Konstante saehe sonst
    zunaechst identisch aus.

    Belegt wird ausserdem, dass die Gruppe tatsaechlich die Raum-Eintraege
    und das Neu-Raum-Feld umschliesst, Export und Entfernen aber aussen vor
    laesst - eine zu weit oder zu eng gezogene Gruppe waere fuer
    Screenreader-Nutzer ebenso falsch wie gar keine.

    Review-Fund 7 (2026-09-05, Review der Nacharbeit): den Schnitt am
    ERSTEN `</div>` nach dem Oeffnungstag der Gruppe enden zu lassen ist
    nur korrekt, solange innerhalb der Gruppe kein verschachteltes `<div>`
    auftaucht - kaeme eines hinzu, faende `menu.index("</div>", ...)` dessen
    schliessendes Tag statt des der Gruppe, `group_body` waere dann zu kurz
    abgeschnitten. Die POSITIVEN Assertions unten wuerden das laut melden
    (der abgeschnittene Text enthaelt "roomChips()" & Co. dann nicht mehr),
    die beiden NEGATIVEN ("not in") dagegen wuerden lautlos weiter
    bestehen, obwohl sie nichts mehr pruefen - ein verschachteltes `<div>`
    faellt schlicht aus dem (jetzt zu kurzen) `group_body` heraus, noch
    bevor Export/Entfernen ueberhaupt drankommen koennten. Verankert wird
    das Ende deshalb an `<hr class="tile-menu-sep"` - dem tatsaechlichen,
    im Markup fest stehenden Ende der Raumgruppe (s. index.html), nicht an
    einem zufaelligen ersten `</div>`."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]

    group_open_start = menu.index('<div class="tile-menu-rooms"')
    group_open_end = menu.index(">", group_open_start) + 1
    group_tag = menu[group_open_start:group_open_end]
    assert 'role="group"' in group_tag
    assert ":aria-labelledby=\"'tile-menu-room-heading-' + device.id\"" in group_tag

    group_body = menu[group_open_end : menu.index('<hr class="tile-menu-sep"', group_open_end)]
    assert ":id=\"'tile-menu-room-heading-' + device.id\"" in group_body
    assert "menu_room_heading" in group_body
    assert "saveRoom(device, '')" in group_body
    assert "roomChips()" in group_body
    assert "beginNewRoom(device)" in group_body
    assert "tile-menu-input" in group_body
    assert "exportDevice(device)" not in group_body
    assert "removeDevice(device)" not in group_body


async def test_the_room_group_wrapper_does_not_disturb_the_menu_layout(api):
    """A11y-Nacharbeit (2026-09-05), Fund 2: `.tile-menu-items` ist eine
    Flex-Spalte, deren Kinder direkt die Menue-Eintraege sind (Begruendung
    bei der Regel selbst, style.css). Der `.tile-menu-rooms`-Wrapper fuer
    `role="group"` ist rein semantisch und darf dieses Layout nicht
    veraendern.

    Review-Fund 5 (2026-09-05, Review der Nacharbeit): urspruenglich per
    `display: contents` geloest (genau wie einst `.room-picker` in
    derselben Datei) - das hat aber eine bekannte WebKit-Einschraenkung:
    Safari laesst `display: contents`-Teilbaeume teils aus dem
    Accessibility-Baum fallen, das `role="group"` samt `aria-labelledby`
    kaeme dort also nie an, obwohl Safari in dieser Datei an anderer Stelle
    ausdruecklich ein Ziel ist. Die ausgelieferte Regel macht den Wrapper
    deshalb stattdessen zu einer echten Box mit demselben Spalten-Flex,
    demselben `gap` und denselben `align-items` wie `.tile-menu-items`
    selbst - layoutidentisch, aber ohne die Browser-Kompatibilitaetsfrage.
    Ohne dieses Nachziehen staenden die Raumeintraege sonst (mit `display:
    contents` oder gaenzlich ohne Sonderbehandlung) nicht mehr auf einer
    Ebene mit "+ Neuer Raum", Trennstrich, Export und Entfernen."""
    client, _store, _device_id = api
    css = (await client.get("/static/style.css")).text
    rule = css.split(".tile-menu-rooms {", 1)[1].split("}", 1)[0]
    assert "display: contents" not in rule
    assert "display: flex" in rule
    assert "flex-direction: column" in rule
    assert "align-items: stretch" in rule
    assert "gap: 1px" in rule


async def test_exactly_one_signals_dialog_is_delivered(api):
    """Entwurf Abschnitt 4: EIN `<dialog>` fuer die ganze Seite, nicht eines
    je Kachel.

    Markup innerhalb `x-for` wird einmal PRO GERAET ausgeliefert - bei
    dreissig Geraeten laegen dreissig vollstaendige Signaltabellen im
    Dokument, und jede `id` darin dreissigfach (derselbe Fallstrick, den
    `aria-labelledby` im Kachel-Menue schon einmal umschiffen musste). Die
    Zaehlung auf 1 ist die einzige Zusicherung, die diesen Rueckfall
    ueberhaupt bemerken wuerde: ein `<dialog>` in der Kachel saehe im
    ausgelieferten Text sonst genauso aus wie eines am Seitenende.

    Die Ortspruefung (nach `</main>`) belegt zusaetzlich, dass es ausserhalb
    der Ansichts-Sections und damit ausserhalb jeder Geraeteschleife steht."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert markup.count("<dialog") == 1
    assert 'x-ref="signalsModal"' in markup
    assert markup.index("<dialog") > markup.index("</main>")


async def test_the_signals_modal_has_exactly_one_place_that_resets_its_state(api):
    """Entwurf Abschnitt 4: `@close` ist die EINZIGE Ruecksetzstelle.

    Das Ereignis feuert auf jedem Schliessweg - Escape, Schliessen-Knopf,
    Backdrop, `close()` aus JavaScript. Ein zweiter Ruecksetzer an einem
    einzelnen Schliessweg waere genau die Verteilung auf mehrere Handler,
    die beim Raum-Auswahlfeld sechs Reviewrunden gekostet hat; deshalb
    zaehlt dieser Test die Vorkommen, statt nur eines zu suchen.

    `@click.self` ist dazu Pflicht und kein Beiwerk: ein `<dialog>`
    schliesst bei einem Klick auf den Backdrop NICHT von selbst.

    Fund 12 (finale Branch-Review, 2026-09-06): `@click.self` allein
    schliesst das Modal auch, wenn ein Mousedown IM Inhalt beginnt (etwa
    beim Markieren eines Signaltitels) und der Mouseup beim Ueberziehen auf
    dem Backdrop landet - das Klick-Ziel ist dann `<dialog>`, obwohl die
    Geste im Inhalt anfing. `@mousedown` haelt seither fest, ob der
    Mousedown SELBST schon auf dem Backdrop war; der Klick-Handler schliesst
    nur noch, wenn beides zutrifft. `signalsModalBackdropMousedown` ist
    dabei reine Praesentations-Buchfuehrung, nicht Teil des hier bewachten
    Modal-Zustands - `@close` bleibt trotzdem die einzige Stelle, die
    `signalsModalDevice` zuruecksetzt.

    Fund 1 (Nachpruefung der Fixes, 2026-09-06): am Mousedown-Handler darf
    KEIN `.self` stehen, und dieser Test ist die einzige Stelle, die das
    festhaelt. Mit `.self` ueberspringt Alpine den Ausdruck ganz, wenn das
    Ziel nicht das `<dialog>` ist - das Feld wuerde dann nur gesetzt, nie
    geleert, und ein Mousedown auf dem Backdrop ohne folgenden Klick darauf
    (Escape mit gedrueckter Maustaste, Loslassen ausserhalb des Fensters)
    liesse es dauerhaft auf `true` stehen. Der naechste Zieh-Vorgang aus dem
    Inhalt heraus schloesse das Modal dann genau wieder so, wie Fund 12 es
    verhindern wollte. Ohne `.self` schreibt jeder Mousedown im Teilbaum das
    Feld neu.

    `isBackdropEvent` (app.js) entscheidet, ob ein Ereignis wirklich auf dem
    Backdrop lag: der eigene Scrollbalken des Modals gehoert ebenfalls dem
    `<dialog>` und liefert dasselbe Ziel, ein Griff daran schloesse das
    Modal sonst - ausgerechnet bei den langen Listen, fuer die es ihn gibt.
    Geprueft wird an BEIDEN Enden der Geste, sonst schloesse auch ein Zug
    vom Backdrop IN den Inhalt hinein: das Klick-Ereignis feuert am
    naechsten gemeinsamen Vorfahren beider Ziele, und das ist dann wieder
    das `<dialog>`.

    Die frueher hier stehende `offsetX < clientWidth`-Bedingung ist
    ersetzt, nicht ergaenzt: sie trennte nur einen Scrollbalken ab, der
    PLATZ RESERVIERT, und lief bei einem ueberlagernden (macOS-
    Voreinstellung, misst 0 px) ins Leere - dazu deckte sie weder einen
    waagerechten Balken noch eine RTL-Anordnung ab. Der Rechteckvergleich
    in `isBackdropEvent` braucht keine dieser Fallunterscheidungen; taucht
    `offsetX` hier je wieder auf, ist das ein Rueckschritt."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    script = (await client.get("/static/app.js")).text
    assert '@close="signalsModalDevice = null"' in markup
    assert '@mousedown="signalsModalBackdropMousedown = isBackdropEvent($event, $el)"' in markup
    assert "@mousedown.self=" not in markup
    assert (
        '@click.self="if (signalsModalBackdropMousedown && isBackdropEvent($event, $el)) '
        '$el.close(); signalsModalBackdropMousedown = false"' in markup
    )
    assert markup.count("signalsModalDevice = null") == 1

    # Der Helfer vergleicht die Lage gegen das Rechteck des Dialogs - nicht
    # `offsetX` gegen `clientWidth`, siehe oben.
    helper_start = script.index("isBackdropEvent(event, el) {")
    helper = script[helper_start : script.index("\n    },", helper_start)]
    assert "event.target !== el" in helper
    assert "getBoundingClientRect()" in helper
    for edge in ("rect.left", "rect.right", "rect.top", "rect.bottom"):
        assert edge in helper
    assert "offsetX" not in helper


async def test_the_two_entry_points_open_the_signals_modal(api):
    """Entwurf Abschnitt 5: das Modal hat genau zwei Einstiege.

    Der Kebab-Eintrag ruft ERST `closeTileMenu($el)`, dann
    `openSignalsModal(device)` - diese Reihenfolge traegt den Fokus:
    `closeTileMenu` setzt ihn auf das `<summary>`, und das unmittelbar
    folgende `showModal()` merkt sich genau diesen Fokus als Rueckkehrpunkt.
    Umgedreht landete der Fokus nach dem Schliessen des Modals im Nichts.

    Der `+ N weitere Signale`-Link sprang bislang per `selectView('signals')`
    in eine Liste ALLER Geraete, in der man das eigene wieder suchen musste -
    er zeigt jetzt auf das Geraet, dessen Signale er verspricht."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert '@click="closeTileMenu($el); openSignalsModal(device)"' in markup
    assert "x-text=\"t('web.devices.menu_signals')\"" in markup
    assert '@click.prevent="openSignalsModal(device)"' in markup

    # Die Reihenfolge NUR innerhalb des Menues vergleichen: der
    # `+ N weitere Signale`-Link steht weiter oben in derselben Kachel und
    # ruft dieselbe Methode, ein `markup.index(...)` ueber die ganze Seite
    # traefe also ihn statt den Menueeintrag und waere immer wahr.
    menu_start = markup.index('<div class="tile-menu-items">')
    menu = markup[menu_start : markup.index("</details>", menu_start)]
    assert menu.index("openSignalsModal(device)") < menu.index("exportDevice(device)")


async def test_open_signals_modal_shows_the_dialog_only_after_alpine_rendered(api):
    """Entwurf Abschnitt 4: `showModal()` erst im `$nextTick`.

    `showModal()` setzt den Anfangsfokus auf das erste fokussierbare Element
    IM Dialog - und das gibt es erst, nachdem Alpine den `x-if`-Inhalt
    aufgebaut hat. Ohne `$nextTick` oeffnet der Dialog leer und der Fokus
    landet auf dem `<dialog>` selbst; die erste Tab-Taste faengt dann am
    Dokumentanfang an statt im Modal."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("openSignalsModal(device) {")
    end = script.index("\n    },", start)
    body = script[start:end]
    assert "this.signalsModalDevice = device.id;" in body
    assert "this.$nextTick(() => this.$refs.signalsModal.showModal());" in body


async def test_opening_the_signals_modal_clears_a_stale_error_from_another_device(api):
    """Fund 2 (finale Branch-Review, 2026-09-06): `signalsError` ist
    seitenweit, das Modal aber pro Geraet. `startApp` laedt die Signale
    aller Geraete parallel, und jedes `loadSignals` leert `signalsError`
    nur VOR seinem eigenen Abruf - scheitert Geraet A und laedt Geraet B
    danach erfolgreich, bleibt Geraet As Fehler stehen. Oeffnet man danach
    das Modal fuer ein drittes, sauber geladenes Geraet, haengt der
    namenlose Fehler ueber dessen Liste, obwohl er nichts mit diesem Geraet
    zu tun hat. `openSignalsModal` muss den Fehler deshalb selbst raeumen,
    als allererste Anweisung.

    Das ist KEIN zweiter Ruecksetzer von `signalsModalDevice` - jene Regel
    (siehe `test_the_signals_modal_has_exactly_one_place_that_resets_its_state`)
    betrifft ausschliesslich dieses eine Feld; `signalsError` ist
    eigenstaendiger Zustand."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("openSignalsModal(device) {")
    end = script.index("\n    },", start)
    body = script[start:end]
    assert "this.signalsError = null;" in body
    assert body.index("this.signalsError = null;") < body.index(
        "this.signalsModalDevice = device.id;"
    )


async def test_removing_a_device_closes_a_signals_modal_that_shows_it(api):
    """Entwurf Abschnitt 4, "Wenn das Geraet verschwindet".

    Ohne diesen Ruf bliebe ein Dialog ueber einem Geraet offen stehen, das
    es nicht mehr gibt - und der `x-if`-Waechter machte ihn zu einem leeren
    Kasten ohne erkennbaren Grund."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("async removeDevice(device) {")
    end = script.index("\n    },", start)
    body = script[start:end]
    assert "if (this.signalsModalDevice === device.id) {" in body
    assert "this.closeSignalsModal();" in body


async def test_losing_authentication_closes_an_open_signals_modal(api):
    """Fund 3 (finale Branch-Review, 2026-09-06): das `<dialog>` steht
    bewusst ausserhalb von `<template x-if="stringsReady && authenticated">`
    (siehe index.html), damit `$refs.signalsModal` immer aufloesbar ist -
    aber nichts schloss es bisher, wenn `authenticated` auf `false`
    kippt. Faellt die Sitzung waehrend das Modal offen ist (Bruecken-
    Neustart via `handleLiveDisconnect` -> `loadAuthInfo`, oder eine 401
    aus einer Modal-Aktion via `noteAuthError`), rendert Alpine den
    Login-Bildschirm HINTER einem offenen `showModal()`-Dialog: alles
    ausserhalb davon ist inert, Passwortfeld und Fehlerbanner unerreichbar.

    Dieser Test belegt nur, dass beide Stellen, an denen `authenticated`
    auf `false` gesetzt wird, `closeSignalsModal()` aufrufen - NICHT, dass
    ein Browser daraus tatsaechlich einen erreichbaren Login-Bildschirm
    macht; dafuer braeuchte es eine echte Rendering-Engine (siehe
    `test_the_page_does_not_call_init_a_second_time` oben)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    note_auth_error_start = script.index("noteAuthError(error) {")
    note_auth_error_end = script.index("\n    },", note_auth_error_start)
    note_auth_error_body = script[note_auth_error_start:note_auth_error_end]
    assert "this.authenticated = false;" in note_auth_error_body
    assert "this.closeSignalsModal();" in note_auth_error_body

    load_auth_info_start = script.index("async loadAuthInfo() {")
    load_auth_info_end = script.index("\n    },", load_auth_info_start)
    load_auth_info_body = script[load_auth_info_start:load_auth_info_end]
    assert "this.authenticated = info.authenticated;" in load_auth_info_body
    assert "if (!this.authenticated) {" in load_auth_info_body
    assert "this.closeSignalsModal();" in load_auth_info_body
    # Der Aufruf haengt am `if (!this.authenticated)`-Zweig, nicht am
    # Erfolgsfall - ein Fund, der nur den blossen `in`-Test bestuende, liesse
    # `closeSignalsModal()` auch dann durchgehen, wenn er unbedingt VOR der
    # Bedingung stuende und das Modal bei jedem Aufruf schloesse.
    guard_index = load_auth_info_body.index("if (!this.authenticated) {")
    call_index = load_auth_info_body.index("this.closeSignalsModal();")
    assert guard_index < call_index


def _signals_dialog(markup: str) -> str:
    """Der Inhalt des Signal-Modals, ohne den Rest der Seite.

    Ein blosses `in markup` wuerde die alte Signal-Section mitzaehlen,
    solange es sie noch gibt (Task 3 loescht sie erst danach) - und traefe
    danach immer noch die Geraetekacheln, die dieselben Helfer benutzen."""
    start = markup.index("<dialog")
    return markup[start : markup.index("</dialog>", start)]


async def test_the_signals_modal_carries_the_complete_signal_row(api):
    """Entwurf Abschnitt 2: die Signalzeile zieht 1:1 um, ohne
    Funktionsverlust - Titel, Export, Resend und Rohwert-Schreiben
    inbegriffen. Genau diese vier Schreibwege sind das, was die alte
    Ansicht als einzige konnte; faellt einer beim Umzug herunter, ist er
    nirgends mehr erreichbar."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    assert '@change="saveTitle(signal)"' in dialog
    assert '@change="toggleExported(signal)"' in dialog
    assert '@change="toggleResend(signal)"' in dialog
    assert '@click="writeRaw(signal)"' in dialog
    assert ":title=\"t('web.signals.key_tooltip')\"" in dialog
    assert "x-text=\"t('web.signals.key_hint')\"" in dialog
    assert "x-text=\"t('web.signals.load_button')\"" in dialog


async def test_the_signals_error_banner_lives_inside_the_modal(api):
    """Entwurf Abschnitt 4, Punkt 2: `signalsError` steht IM Modal.

    Ein `<dialog>` im Top-Layer verdeckt alles darunter samt Backdrop - ein
    Fehlerbanner ausserhalb waere waehrend der einzigen Aktion, die es
    ausloesen kann (Titel speichern, Haken setzen, Rohwert schreiben),
    unsichtbar. Ein unsichtbarer Fehler ist nach Spec 8.1 schlimmer als
    keiner."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    assert 'x-show="signalsError"' in dialog
    assert 'x-text="signalsError"' in dialog


async def test_both_signal_groups_share_one_details_template(api):
    """Entwurf Abschnitt 4, Punkt 5: EINE Vorlage fuer beide Gruppen.

    Zwei Formen (Block hier, `<details>` dort) hiessen zwei Zweige und in
    jedem eine eigene Kopie der Signalzeilen-Vorlage - genau die
    Verdopplung, die `signalGroupsFor` abgeschafft hat (51 doppelte Zeilen,
    siehe dessen Kommentar in app.js).

    Der Startzustand laeuft ueber `x-init` und NICHT ueber ein gebundenes
    `:open`: Alpine wertet Bindungen bei jeder Aenderung ihrer
    Abhaengigkeiten neu aus, und `signalGroupsFor` haengt an
    `signalsByDevice` - ein gespeicherter Signaltitel schriebe ein `:open`
    neu und klappte die gerade geoeffnete Expertengruppe wortlos wieder zu.
    Dieser Test ist die einzige Bremse gegen ein spaeteres, gut gemeintes
    Vereinfachen zu `:open`."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    assert 'x-for="group in signalGroupsFor(signalsModalDevice)"' in dialog
    assert dialog.count("<details") == 1
    assert 'x-init="$el.open = !group.collapsible"' in dialog
    assert ":open=" not in dialog
    assert "x-text=\"t('web.signals.functional_vs_expert_explanation')\"" in dialog
    assert "x-text=\"t('web.signals.none_functional')\"" in dialog


async def test_the_signals_modal_head_ships_a_labelled_heading_and_a_close_button(api):
    """Fund 4 und Fund 5 (finale Branch-Review, 2026-09-06): weder die
    Ueberschrift noch der Schliessen-Knopf noch das `#i-close`-Symbol waren
    bisher irgendwo verankert - ein Edit, der den Knopf loescht (den
    einzigen immer erreichbaren Ausweg aus dem Dialog ausser Escape), liefe
    an dieser Suite ungebremst vorbei.

    Zugleich der Beleg fuer Fund 4: das `<dialog>` traegt
    `aria-labelledby="signals-modal-heading"`, und genau diese `id` sitzt
    an der `<h2>` - ohne das haette eine Screenreader-Ansage "Dialog" ohne
    erkennbares Subjekt. Anders als das `aria-labelledby` am Kachel-Menue
    (das seine id aus `device.id` ableiten muss, weil es einmal PRO KACHEL
    existiert) ist eine feste id hier korrekt, weil es dieses `<dialog>`
    nur ein einziges Mal im Dokument gibt.

    Fund 2 (Nachpruefung der Fixes, 2026-09-06): geprueft wird BEIDE Seiten
    des Icons - der `<use href="#i-close">` im Dialog UND die
    `<symbol id="i-close">`-Definition im Sprite-Block oben. `#i-close` hat
    sonst keinen Verwender; ohne die zweite Zusicherung liesse sich das
    Symbol loeschen, ohne dass diese Suite etwas merkt, und der Knopf
    zeichnete stillschweigend nichts (ein `<use>` auf eine fehlende id
    bleibt leer, ohne Konsolenmeldung). Dasselbe Paar prueft
    `test_the_tile_menu_has_its_own_icon_symbol` weiter oben aus demselben
    Grund."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    dialog = _signals_dialog(markup)
    assert '<symbol id="i-close"' in markup
    assert 'aria-labelledby="signals-modal-heading"' in dialog
    assert 'id="signals-modal-heading"' in dialog
    assert (
        "x-text=\"t('web.signals.modal_heading', { device: signalsModalDeviceObject().label })\""
        in dialog
    )
    assert ":aria-label=\"t('web.signals.modal_close')\"" in dialog
    assert 'href="#i-close"' in dialog


async def test_the_signal_group_summary_suppresses_the_default_marker_and_ships_a_chevron(api):
    """Fund 1 (Review der Nacharbeit, 2026-09-06): der Kommentar am
    `.signal-group`-Block in `style.css` behauptete, der Standard-Marker
    eines `<summary>` bleibe dort absichtlich sichtbar - dabei unterdrueckte
    keine der Regeln ihn: kein `list-style: none`, keine
    `::-webkit-details-marker`-Regel, kein Chevron. Ausgeliefert wurde also
    das browsereigene Aufklapp-Dreieck (Firefox: Umriss, WebKit: gefuellt) -
    das Gegenteil dessen, was der Kommentar behauptete.

    Dieser Test verankert das Gegenstueck: dieselbe doppelte Unterdrueckung
    wie bei `.tile-menu > summary` und `.projectsync-device summary`, plus
    den ersetzenden Chevron (`#i-chevron`), der sich beim Aufklappen dreht.
    Er belegt nur, dass Markup und Stylesheet die dafuer noetigen Knoten
    bzw. Regeln tragen - nicht, dass eine Rendering-Engine daraus
    tatsaechlich einen unsichtbaren Standardmarker und einen sichtbaren,
    rotierenden Pfeil macht; dafuer braeuchte es einen echten Browser. Ein
    kuenftiger Edit, der nur eine Haelfte der Doppelung entfernt und den
    Kommentar stehen laesst, faellt hier durch."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    dialog = _signals_dialog(markup)
    assert 'class="icon chevron" aria-hidden="true"' in dialog
    assert 'href="#i-chevron"' in dialog

    css = (await client.get("/static/style.css")).text
    block_start = css.index("/* Die beiden Signalgruppen im Modal.")
    block = css[block_start:]
    assert ".signal-group > summary {" in block
    assert "list-style: none;" in block
    assert ".signal-group > summary::-webkit-details-marker {" in block
    assert ".signal-group > summary .chevron {" in block
    assert ".signal-group[open] > summary .chevron {" in block
    assert "transform: rotate(90deg);" in block
    assert "ERWUENSCHT" not in block


async def test_the_signals_view_is_gone_from_navigation_and_markup(api):
    """Entwurf Abschnitt 3: der Reiter wird ersatzlos aufgeloest.

    Geprueft wird nicht nur der Nav-Knopf, sondern auch, dass NIRGENDWO
    mehr auf den Ansichtswert `'signals'` geschaltet wird - ein
    stehengebliebener `selectView('signals')` waere ein Klick, der die
    Anwendung in eine Ansicht schickt, die es nicht mehr gibt: alle
    Sections blieben ausgeblendet, die Seite waere leer, ohne
    Fehlermeldung.

    `showExpertSignals` faellt mit: der Auf-/Zu-Zustand lebt jetzt im DOM
    (`<details>` im Modal), ein globales Feld dafuer waere eine zweite
    Wahrheit ohne Leser."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert "t('web.nav.signals')" not in page
    assert "view === 'signals'" not in page
    assert "selectView('signals')" not in page
    assert 'view === "signals"' not in script
    assert "showExpertSignals" not in script
    assert "showExpertSignals" not in page


async def test_the_dropped_signal_keys_are_gone_from_the_translation_table(api):
    """Die drei Schluessel des alten Reiters haben keinen Leser mehr.

    `expert_collapsed_hint` faellt dabei ersatzlos statt umzuziehen: "12
    Expertensignale ausgeblendet" sagt dasselbe wie "Experte (12)" im
    `<summary>`, nur nicht an der Stelle, an der man klickt. Ein
    stehengelassener Schluessel waere nicht bloss tot - er verwiese in
    seinem eigenen Text auf einen Schalter, den es nicht mehr gibt."""
    client, _, _ = api
    strings = (await client.get("/api/i18n")).json()["strings"]
    for key in (
        "web.nav.signals",
        "web.signals.show_expert",
        "web.signals.expert_collapsed_hint",
    ):
        assert key not in strings
    assert "web.devices.menu_signals" in strings
    assert "web.signals.modal_heading" in strings
    assert "web.signals.modal_close" in strings


# ---------------------------------------------------------------------------
# Suchfeld der Geraeteansicht (Entwurf vom 2026-09-06). Das Feld fiel durch
# das CSS-Raster - die Formularregel listet text, number, password und
# select, aber nicht search -, weshalb der Browser es selbst zeichnete.
# ---------------------------------------------------------------------------


async def test_the_search_field_ships_the_words_for_counter_and_clear_button(api):
    """Der Zaehler traegt Text, das Loeschkreuz traegt keinen und braucht
    deshalb einen zugaenglichen Namen - beide muessen uebersetzt beim
    Browser ankommen.

    `{count}` bleibt dabei UNAUFGELOEST: aufgeloest wird es in app.js
    (`t(key, values)`), wenn die Zahl feststeht. Der Server kennt sie nicht,
    und `GET /api/i18n` liefert deshalb die rohe Vorlage - genau das belegt
    der Vergleich auf die Zeichenkette samt geschweifter Klammern."""
    client, _, _ = api
    strings = (await client.get("/api/i18n")).json()["strings"]
    assert strings["web.devices.search_count"] == "{count} found"
    assert strings["web.devices.search_clear"] == "Clear search"


async def test_the_search_field_has_a_magnifier_of_its_own(api):
    """Die Lupe kommt aus dem Inline-Sprite wie jedes andere Symbol -
    dieselbe Begruendung wie beim eingecheckten vendor/alpine.min.js: die
    Oberflaeche laeuft offline.

    Das Loeschkreuz bekommt dagegen KEIN eigenes Symbol, es benutzt das
    vorhandene `#i-close`. Zwei gleiche Formen waeren zwei Orte, an die man
    sich beim naechsten Strichstaerken-Dreh erinnern muss - und an einen
    davon erinnert man sich nicht."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    assert 'id="i-search"' in page
    assert page.count('id="i-close"') == 1


async def test_the_search_field_carries_the_frame_and_the_input_does_not(api):
    """Der Rahmen sitzt am Container, nicht am Feld.

    Traegen beide einen, liegt ein Rahmen im anderen - und der Fokusring
    (naechster Test) haette nichts, woran er sich festmachen koennte, das
    Lupe und Kreuz mit einschliesst."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    field = css.split(".search-field {", 1)[1].split("}", 1)[0]
    inner = css.split('.search-field input[type="search"] {', 1)[1].split("}", 1)[0]
    assert "border: 1px solid var(--border)" in field
    assert "border-radius" in field
    assert "border: none" in inner
    assert "background: none" in inner


async def test_the_search_focus_ring_wraps_the_whole_group(api):
    """`:focus-within` am Container statt `:focus` am Feld: der Ring soll
    Lupe, Zaehler und Kreuz mit einschliessen, nicht nur das Eingabefeld in
    ihrer Mitte. Der browsereigene Umriss am Feld muss dafuer weichen, sonst
    stuenden beide."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    ring = css.split(".search-field:focus-within {", 1)[1].split("}", 1)[0]
    assert "border-color: var(--accent)" in ring
    inner_focus = css.split('.search-field input[type="search"]:focus {', 1)[1].split("}", 1)[0]
    assert "outline: none" in inner_focus


async def test_the_browser_does_not_add_a_second_clear_cross(api):
    """WebKit legt in ein `input[type="search"]` sein eigenes Loeschkreuz -
    daneben stuende unseres ein zweites Mal.

    Abgeschaltet wird es mit `-webkit-appearance` UND `appearance`: das
    Pseudoelement ist herstellerspezifisch, und die Zusicherung auf die
    zweite Form braucht den Zeilenanfang - `"appearance: none"` ist eine
    Teilzeichenkette von `"-webkit-appearance: none"` und waere sonst schon
    von der ersten Zeile erfuellt."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split("::-webkit-search-cancel-button {", 1)[1].split("}", 1)[0]
    assert "-webkit-appearance: none" in rule
    assert re.search(r"^\s*appearance: none", rule, re.MULTILINE)


async def test_the_counter_and_the_cross_appear_only_with_a_query(api):
    """Beide haengen an `deviceSearch` und tragen `x-cloak`: bei leerem Feld
    sind sie weg, und beim ersten Zeichnen blitzen sie nicht auf, bevor
    Alpine initialisiert hat.

    Der Zaehler liest `visibleDevices().length` - also das, was tatsaechlich
    unter der Leiste steht, einschliesslich eines aktiven Raumfilters. Die
    Suchlogik selbst bleibt unberuehrt.

    Zwei `aria-label`: eines am Eingabefeld (es traegt nur einen Platzhalter,
    und der verschwindet genau dann, wenn jemand etwas eingegeben hat), eines
    am Kreuz (es traegt gar kein Wort).

    Das Kreuz muss den Fokus zurueck ins Feld legen: `x-show` setzt beim
    Leeren `display: none` auf das Element, das gerade den Fokus traegt,
    und der Browser wirft ihn dann auf `<body>`. Das native
    Loeschkreuz von WebKit - `::-webkit-search-cancel-button`, andernorts in
    dieser Datei bewusst abgeschaltet - hat genau das getan: den Fokus im
    Feld gehalten. Unser eigenes Kreuz muss dasselbe leisten, sonst
    verliert eine Tastaturbedienung durch das Loeschen den Anschluss und
    muesste sich von ganz oben wieder durch die Seite tabben.

    `aria-live="polite"` am Zaehler: er beantwortet fuer Screenreader-
    Nutzer die Frage, auf die er auch visuell antwortet - wie viele
    Treffer nach der letzten Eingabe uebrig sind, bis hinunter zu null.
    Ohne die Live-Region bliebe das stumm."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    field = page.split('<div class="search-field">', 1)[1].split("</div>", 1)[0]
    assert field.count('x-show="deviceSearch.trim()"') == 2
    assert field.count("x-cloak") == 2
    assert field.count("aria-label") == 2
    assert "visibleDevices().length" in field
    assert "deviceSearch = ''" in field
    assert "t('web.devices.search_clear')" in field
    assert 'href="#i-search"' in field
    assert 'href="#i-close"' in field
    assert 'x-ref="deviceSearchInput"' in field
    assert "$refs.deviceSearchInput.focus()" in field
    assert 'aria-live="polite"' in field


async def test_the_search_field_moves_left_when_there_are_no_rooms(api):
    """Der Abstandhalter, der das Feld nach rechts schiebt, existiert nur
    zusammen mit den Chips, an denen vorbeizuschieben waere.

    Ohne Raeume blendet sich die Chip-Leiste aus (`x-if="hasAnyRoom()"`).
    Stuende der Abstandhalter dann weiter im Markup - so war es -, bliebe
    ein einzelner Kasten rechts in einer sonst leeren Zeile stehen. Mit
    eigenem `x-if` verschwindet er mit den Chips, und das Feld rueckt an die
    linke Kante, auf eine Sichtachse mit dem Kachelraster darunter.

    Zwei `x-if="hasAnyRoom()"` in der Leiste sind also richtig und kein
    Versehen: eines fuer die Chips, eines fuer den Abstandhalter."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    bar = page.split('<div class="room-bar"', 1)[1].split('<div class="search-field">', 1)[0]
    assert 'style="flex: 1 1 auto"' not in bar
    assert bar.count('x-if="hasAnyRoom()"') == 2
    assert '<span class="room-spacer"></span>' in bar
    css = (await client.get("/static/style.css")).text
    assert "flex: 1 1 auto" in css.split(".room-spacer {", 1)[1].split("}", 1)[0]


# ---------------------------------------------------------------------------
# Aufgabe 5: Die Batteriezeile der Kachel. Gegenbuchung zur Cluster-
# Rangliste (Aufgabe 4): mit Rang 90 stuende der Batteriestand hinter allen
# sechzehn anderen funktionalen Signalen des Tasters und fiele damit aus den
# sechs Vorschauzeilen - er waere auf der Kachel gar nicht mehr zu sehen.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_the_battery_is_never_the_lead_and_never_counted_twice():
    """Die drei Zusicherungen der Batteriezeile an EINEM Aufbau, weil sie
    zusammengehoeren: der Batteriestand fuehrt nicht, er steht nicht in der
    Vorschau, und er zaehlt nicht als "weiteres".

    Der Aufbau ist der Taster: 17 funktionale Signale in der Reihenfolge,
    in der die Cluster-Rangliste sie liefert - sechzehn Switch-Signale,
    zuletzt die Batterie. Sechs Vorschauzeilen plus eine Fusszeile lassen
    zehn uebrig. Nennt die Kachel elf, ist die Batterie doppelt gezaehlt -
    genau der Fehler, den der Canvas-Entwurf hatte.

    Als node-Lauf statt als Zeichenketten-Suche in `app.js`: eine Suche
    belegt nur, DASS eine Zeile ausgeliefert wird. Am 2026-09-05 haben drei
    solche Tests einen Critical durchgelassen, weil sie exakt die
    Zeichenketten prueften, die den Fehler erzeugten."""
    values = _app_state(
        """
        const signals = [];
        for (let i = 0; i < 16; i++) {
          signals.push({
            key: "d1_1_s" + i, title: "s" + i,
            endpoint: 1, cluster_id: 59, functional: true,
          });
        }
        signals.push({
          key: "d1_0_battery", title: "battery",
          endpoint: 0, cluster_id: 47, functional: true,
        });
        state.signalsByDevice = { 1: signals };
        console.log(JSON.stringify({
          lead: state.leadSignalFor(1).key,
          battery: state.batterySignalFor(1).key,
          preview: state.firstSignalsFor(1).map((s) => s.key),
          remaining: state.remainingSignalCount(1),
        }));
        """
    )

    assert values["lead"] == "d1_1_s0"
    assert values["battery"] == "d1_0_battery"
    assert "d1_0_battery" not in values["preview"]
    assert len(values["preview"]) == 6
    assert values["remaining"] == 10


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_a_mains_powered_device_has_no_battery_row():
    """Ohne PowerSource-Signal darf die Kachel keine Fusszeile zeigen - und
    der Zaehler muss sich genauso verhalten wie vor dieser Aenderung."""
    values = _app_state(
        """
        state.signalsByDevice = { 1: [
          { key: "d1_1_onoff", title: "onoff", endpoint: 1, cluster_id: 6, functional: true },
          { key: "d1_2_power", title: "power", endpoint: 2, cluster_id: 144, functional: true },
        ] };
        console.log(JSON.stringify({
          battery: state.batterySignalFor(1),
          lead: state.leadSignalFor(1).key,
          remaining: state.remainingSignalCount(1),
        }));
        """
    )

    assert values["battery"] is None
    assert values["lead"] == "d1_1_onoff"
    assert values["remaining"] == 0


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_a_device_whose_only_functional_signal_is_the_battery_has_no_lead():
    """Der Randfall, an dem der Hinweis "keine funktionalen Signale" falsch
    waere: es GIBT eines, es steht nur in der Fusszeile."""
    values = _app_state(
        """
        state.signalsByDevice = { 1: [
          { key: "d1_0_battery", title: "battery", endpoint: 0, cluster_id: 47, functional: true },
        ] };
        console.log(JSON.stringify({
          lead: state.leadSignalFor(1),
          battery: state.batterySignalFor(1).key,
          remaining: state.remainingSignalCount(1),
        }));
        """
    )

    assert values["lead"] is None
    assert values["battery"] == "d1_0_battery"
    assert values["remaining"] == 0


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_a_device_with_two_power_source_endpoints_never_leads_with_battery():
    """Der Randfall eines zusammengesetzten Geraets oder einer Bruecke mit
    zwei Batterien unter einem Datensatz: zwei Signale auf Cluster 47, auf
    verschiedenen Endpunkten.

    `batterySignalFor` waehlt per `.find()` nur das erstplatzierte davon -
    das zweite bliebe, wenn die Vorschaumenge nur DIESES eine ausschliesst
    (Schluesselvergleich statt Cluster-Filter), unbemerkt in der Vorschau
    stehen und koennte als Leitwert enden. Zusicherung hier: der Leitwert
    ist das Nutzsignal, kein Signal der Vorschaumenge traegt
    `cluster_id === 47`, und `batterySignalFor` liefert das erstplatzierte
    der beiden PowerSource-Signale."""
    values = _app_state(
        """
        state.signalsByDevice = { 1: [
          { key: "d1_0_battA", title: "battA", endpoint: 0, cluster_id: 47, functional: true },
          { key: "d1_5_battB", title: "battB", endpoint: 5, cluster_id: 47, functional: true },
          { key: "d1_1_onoff", title: "onoff", endpoint: 1, cluster_id: 6, functional: true },
        ] };
        console.log(JSON.stringify({
          lead: state.leadSignalFor(1).key,
          battery: state.batterySignalFor(1).key,
          preview: state.previewSignalsFor(1).map((s) => s.key),
          previewClusters: state.previewSignalsFor(1).map((s) => s.cluster_id),
        }));
        """
    )

    assert values["lead"] == "d1_1_onoff"
    assert values["battery"] == "d1_0_battA"
    assert 47 not in values["previewClusters"]
    assert values["preview"] == ["d1_1_onoff"]


async def test_the_tile_has_a_battery_row_with_its_own_symbol(api):
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert 'id="i-battery"' in page
    assert "device-battery" in page
    assert 'x-show="batterySignalFor(device.id)"' in page


async def test_the_no_functional_signals_hint_accounts_for_the_battery(api):
    """Ein Geraet, dessen einziges funktionales Signal die Batterie ist, hat
    keinen Leitwert - aber der Satz "keine funktionalen Signale" waere dort
    falsch, denn die Fusszeile zeigt eines."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    hint = page[page.index("no_functional_signals") - 400 : page.index("no_functional_signals")]
    assert "!batterySignalFor(device.id)" in hint
