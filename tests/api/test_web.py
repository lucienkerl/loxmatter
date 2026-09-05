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

import re
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
    for key in ("devices", "signals", "export", "system", "settings"):
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

    Der Block traegt inzwischen zwoelf `<symbol>`-Definitionen, acht davon
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
    `test_the_page_does_not_call_init_a_second_time` oben).

    Aufgabe 12: die beiden Gruppentitel und der Schaltertext tragen seither
    `t(...)` statt fester deutscher Literale - siehe
    `test_the_signal_group_titles_and_toggle_are_translated` fuer die
    Bindung selbst; hier bleibt nur der Beleg, dass die Gruppierung
    (`signal.functional`) unveraendert ist."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert 't("web.signals.group_functional")' in script
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
    kompakten Kachel waere reiner Platzverbrauch gewesen. Der
    Schluessel `web.devices.values_heading` bleibt deshalb ungenutzt in
    `strings.yaml` liegen (Task 9 raeumt ihn auf) und taucht im
    ausgelieferten Markup nicht mehr auf. Die urspruengliche Sorge des
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
    init_start = script.index("async init()")
    body = script[init_start : init_start + 800]
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


async def test_the_nav_tabs_bind_to_translation_keys_without_altering_click_handlers(api):
    """Aufgabe 10, Schritt 3: `x-text` ersetzt den Textknoten jedes
    Reiterknopfs, `@click`/`:class` bleiben unangetastet - ein falsch
    gebundener Reiter waere im Browser sofort sichtbar, aber dieser Test
    ohne Browser-Engine kann nur pruefen, dass Handler und Bindung
    NEBENEINANDER auf demselben Element stehen, nicht dass ein Klick
    tatsaechlich die Ansicht wechselt."""
    client, _, _ = api
    page = (await client.get("/")).text
    for view_key in ("devices", "signals", "export", "system", "settings"):
        assert f"@click=\"selectView('{view_key}')\" x-text=\"t('web.nav.{view_key}')\"" in page


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


async def test_the_device_card_static_text_is_translated(api):
    """Aufgabe 11, Schritt 3: Statuspillen, Entfernen-Knopf, die beiden
    Werte-/Bedienungs-Abschnittsueberschriften mit ihren Ladehinweisen und
    Leerzustaenden, der Wert-Platzhalter und der Senden-Knopf der
    Geraetekarte - jeweils als reiner `x-text`, weil keiner dieser
    Schluessel eingebettetes HTML traegt.

    Task 8 (Raster-Umbau, 2026-09-05) hat Export und Entfernen zu
    Icon-Knoepfen gemacht (der Platz auf einer 260 px breiten Kachel reicht
    nicht fuer ausgeschriebene Beschriftungen) und die beiden Werte-/
    Bedienungs-Abschnittsueberschriften ersatzlos gestrichen - die Kachel
    zeigt ohnehin nur noch ein einziges Werteraster ohne eigenen Titel.
    `remove`/`export` wandern deshalb vom `x-text` in ein `:title` (die
    Bedeutung eines Icon-only-Knopfs muss trotzdem aus `t(...)` kommen,
    siehe Globale Vorgabe); `values_heading`/`controls_heading` bleiben
    ungenutzt in `strings.yaml` liegen (Task 9 raeumt sie auf).

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
    assert ":title=\"t('web.devices.remove')\"" in markup
    assert "x-text=\"t('web.devices.remove')\"" not in markup
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
    assert ":title=\"t('web.devices.export')\"" in markup
    assert "x-text=\"t('web.devices.export')\"" not in markup
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
    "Erst in Einstellungen -> ... hinterlegen." enthaelt einen echten Link mit
    eigenem `@click.prevent`, der beim Uebersetzen NICHT in einen `x-html`-
    Block verschwinden darf - sonst liesse sich der Klick-Handler nicht mehr
    binden. Drei eigene Elemente (Praefix, Link, Suffix) je mit eigenem
    `x-text` halten den Handler unangetastet."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    device_section_start = markup.index("x-show=\"view === 'devices'\"")
    device_section_end = markup.index("x-show=\"view === 'signals'\"")
    devices_markup = markup[device_section_start:device_section_end]
    assert "x-text=\"t('web.devices.export_hint_prefix')\"" in devices_markup
    assert "x-text=\"t('web.settings.miniserver_link')\"" in devices_markup
    assert "x-text=\"t('web.devices.export_hint_suffix')\"" in devices_markup
    assert "Erst in " not in devices_markup
    assert "Einstellungen → Verbindung zum Miniserver" not in devices_markup
    assert " hinterlegen." not in devices_markup
    assert "@click.prevent=\"selectView('settings')\"" in devices_markup


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


async def test_commission_device_syncs_the_room_draft_and_avoids_duplicate_tiles(api):
    """Fund 3 und Fund 4 (Re-Review 2026-09-05), beide in derselben paar
    Zeilen von `commissionDevice`, die das frisch eingelernte Geraet in
    `this.devices` einsortieren:

    Fund 3 - ohne `syncRoomSelectDraft(device)` blieb
    `roomSelectDrafts[device.id]` `undefined`, Alpine rundet das per
    `x-model` auf `""` ab, und die neue Kachel zeigte "Ohne Raum" in ihrer
    eigenen Auswahlliste, obwohl sie zugleich unter der Gruppen-Ueberschrift
    des gewaehlten Raums stand (`roomKeyOf` liest `device.room` direkt,
    nicht den Entwurf) - der genaue Widerspruch, den diese Fix-Runde
    beseitigen sollte, auf einem Pfad, den der Browser-Check nicht
    abgedeckt hatte. `loadDevices()` erledigt dasselbe fuer jedes Geraet
    beim Start, wird beim Einlernen aber nie erreicht.

    Fund 4 - die Einlern-Route liefert fuer ein schon registriertes Geraet
    dieselbe `device_id` zurueck (siehe den Backend-Test
    `test_recommissioning_a_known_device_applies_the_chosen_room` in
    `tests/api/test_devices.py`). Ein bedingungsloses `push` legte dieses
    Geraet ein zweites Mal in `this.devices` ab: zwei Kacheln mit
    derselben `device.id`, was `x-for`s `:key="device.id"` verletzt und den
    Raum-Chip doppelt zaehlen liess.

    Ohne Browser-Engine laesst sich weder das tatsaechliche
    `roomSelectDrafts` nach einem Klick pruefen noch ein doppelter
    Alpine-Key-Warnhinweis in der Konsole, und schon gar nicht die Race
    gegen ein zeitgleich laufendes `saveRoom`/`saveLabel` (das sich vor
    seinem eigenen `await` eine Objekt-Referenz merkt) - belegt wird
    deshalb der ausgelieferte Methodenkoerper: er sucht per `findIndex`
    nach einem bereits vorhandenen Geraet mit derselben ID, befuellt bei
    einem Treffer das bestehende Objekt per `Object.assign` statt es im
    Array auszutauschen (sonst schriebe ein spaeter aufloesendes
    `saveRoom`/`saveLabel` in ein aus dem Array entkoppeltes Exemplar),
    haengt andernfalls neu an, und ruft in JEDEM Fall
    `syncRoomSelectDraft(device)` auf - vor der ersten `await`-Stelle
    danach (`loadControls`/`loadSignals`), damit die Kachel nie mit einem
    veralteten Entwurf sichtbar wird."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    commission_start = script.index("async commissionDevice() {")
    commission_end = script.index("\n    },", script.index("this.commissionBusy = false;"))
    body = script[commission_start:commission_end]

    push_index = body.index(
        "const existingIndex = this.devices.findIndex((d) => d.id === device.id);"
    )
    assert "this.devices.push(device);" in body
    assert "Object.assign(this.devices[existingIndex], device);" in body
    sync_index = body.index("this.syncRoomSelectDraft(device);")
    controls_index = body.index("await Promise.all([this.loadControls(device.id)")
    assert push_index < sync_index < controls_index, body


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


async def test_remove_device_reconciles_the_room_filter_and_drops_its_draft(api):
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
    Entfernen aus `this.devices` sowohl `reconcileRoomFilter()` aufruft
    als auch den zugehoerigen Eintrag in `roomSelectDrafts` loescht -
    dieselbe Aufraeum-Regel wie fuer `controlsByDevice`/`signalsByDevice`
    zwei Zeilen darueber, die es schon vorher gab."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    remove_start = script.index("async removeDevice(device) {")
    remove_end = script.index("\n    },", remove_start)
    remove_body = script[remove_start:remove_end]

    filter_index = remove_body.index("(d) => d.id !== device.id)")
    reconcile_index = remove_body.index("this.reconcileRoomFilter();")
    drafts_index = remove_body.index("delete this.roomSelectDrafts[device.id];")
    assert filter_index < reconcile_index < drafts_index, remove_body


async def test_the_signal_view_static_text_is_translated(api):
    """Aufgabe 12, Schritt 3: die beiden erklaerenden Hinweise, der
    Schaltertext, der "Signale laden"-Knopf, der leer-Hinweis fuer den
    Funktional-Block, der Schluessel-Tooltip, das "exportieren"-
    Checkbox-Label, der Rohwert-Platzhalter und der Schreiben-Knopf tragen
    jetzt `t(...)` statt fester deutscher Literale - keiner der frueheren
    Literale bleibt im Markup."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "x-text=\"t('web.signals.key_hint')\"" in markup
    assert "ist die Verdrahtung in Loxone" not in markup
    assert "x-text=\"t('web.signals.functional_vs_expert_explanation')\"" in markup
    assert "„Funktional“ sind die Signale" not in markup
    assert "x-text=\"t('web.signals.show_expert')\"" in markup
    assert "Experten-Signale anzeigen" not in markup
    assert "x-text=\"t('web.signals.load_button')\"" in markup
    assert ">Signale laden<" not in markup
    assert "x-text=\"t('web.signals.none_functional')\"" in markup
    assert "Kein Signal dieses Geräts gilt als funktional." not in markup
    assert (
        "x-text=\"t('web.signals.expert_collapsed_hint', { count: group.signals.length })\""
        in markup
    )
    assert "Zugeklappt" not in markup
    assert ":title=\"t('web.signals.key_tooltip')\"" in markup
    assert "Verdrahtung in Loxone – nicht änderbar." not in markup
    assert "x-text=\"t('web.signals.export_checkbox')\"" in markup
    assert ">exportieren<" not in markup
    assert ":placeholder=\"t('web.signals.raw_write_placeholder')\"" in markup
    assert "Rohwert schreiben" not in markup
    assert "x-text=\"t('web.signals.raw_write_submit')\"" in markup
    assert ">Schreiben<" not in markup


async def test_the_signal_group_titles_and_toggle_are_translated(api):
    """Aufgabe 12, Schritt 4: `signalGroupsFor`'s Gruppentitel (Objekt-
    Literale) laufen jetzt ueber `t("web.signals.group_functional")` /
    `t("web.signals.group_expert")` statt fester Literale "Funktional" /
    "Experte"."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    signal_groups_start = script.index("signalGroupsFor(deviceId) {")
    signal_groups_end = script.index("\n    },", signal_groups_start)
    body = script[signal_groups_start:signal_groups_end]
    assert 'title: t("web.signals.group_functional")' in body
    assert 'title: t("web.signals.group_expert")' in body
    assert '"Funktional"' not in body
    assert '"Experte"' not in body


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
    # Der Link selbst bleibt unveraendert (derselbe `@click`-Handler,
    # jetzt mit dem geteilten Schluessel aus Aufgabe 11 uebersetzt).
    export_hint_start = markup.index("web.export.settings_hint_prefix")
    export_hint_end = markup.index("web.export.settings_hint_suffix")
    export_hint = markup[export_hint_start:export_hint_end]
    assert "@click.prevent=\"selectView('settings')\"" in export_hint
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
    assert "@click.prevent=\"selectView('settings')\"" in bridge_hint
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


async def test_the_page_offers_the_room_bar_and_the_room_picker(api):
    client, _, _ = api
    page = (await client.get("/")).text
    assert "roomChips()" in page
    assert "deviceGroups()" in page
    assert "leadSignalFor(" in page
    # `saveRoom(` selbst steht seit Fund 1 nicht mehr im Markup - die
    # Auswahlliste ruft `onRoomSelectChange(device)`, das `saveRoom`
    # innerhalb von app.js aufruft (siehe
    # `test_the_room_select_leaves_new_room_mode_when_a_normal_room_is_picked`
    # fuer den Beleg dieses Aufrufs).
    assert "onRoomSelectChange(" in page
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

    Geprueft wird sowohl das Fehlen der alten Zeichen als `x-text`-Inhalt
    als auch das Vorhandensein der neuen Symbole und ihrer Verwendung -
    inklusive des `:title`, das dabei erhalten bleiben musste."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert "x-text=\"'✎'\"" not in page
    assert "x-text=\"'↓'\"" not in page
    assert "x-text=\"'🗑'\"" not in page
    for symbol_id in ("i-rename", "i-export", "i-remove"):
        assert f'id="{symbol_id}"' in page, symbol_id
        assert f'href="#{symbol_id}"' in page, symbol_id

    rename_start = page.index('class="room-rename"')
    rename_end = page.index("</button>", rename_start)
    rename_button = page[rename_start:rename_end]
    assert 'href="#i-rename"' in rename_button
    assert ":title=\"t('web.devices.room_rename')\"" in rename_button

    export_start = page.index("exportDevice(device)")
    export_button_start = page.rindex("<button", 0, export_start)
    export_button_end = page.index("</button>", export_start)
    export_button = page[export_button_start:export_button_end]
    assert 'href="#i-export"' in export_button
    assert ":title=\"t('web.devices.export')\"" in export_button

    remove_start = page.index("removeDevice(device)")
    remove_button_start = page.rindex("<button", 0, remove_start)
    remove_button_end = page.index("</button>", remove_start)
    remove_button = page[remove_button_start:remove_button_end]
    assert 'href="#i-remove"' in remove_button
    assert ":title=\"t('web.devices.remove')\"" in remove_button


async def test_the_tile_menu_has_its_own_icon_symbol(api):
    """Das Kebab-Symbol wird wie alle anderen inline ausgeliefert - keine
    Icon-Bibliothek, kein CDN, weil die Oberflaeche offline laeuft. Ein
    `<use>` auf eine fehlende ID zeichnet stillschweigend nichts, deshalb
    faellt ein vergessenes Symbol hier auf und nicht erst im Browser."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    assert 'id="i-kebab"' in page


async def test_the_device_grid_is_multi_column(api):
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    assert "auto-fill" in css
    assert "minmax(260px" in css


async def test_the_room_select_uses_a_synced_draft_instead_of_reading_device_room_directly(api):
    """Ersetzt `test_the_room_select_display_depends_only_on_the_device_room`
    (Fund 1, Review vom 2026-09-05): jener Test bewies exakt das, was ein
    Test ohne Browser-Engine NICHT beweisen kann - dass eine Bindung im
    Browser tatsaechlich das Richtige anzeigt. Er pruefte nur, dass
    `:value="device.room || ''"` woertlich im ausgelieferten Markup steht,
    und nannte das (im Docstring) "PER KONSTRUKTION" korrekt. Das war
    falsch: Alpine wendet die Direktiven eines Elements an, BEVOR es dessen
    `x-for`-Kinder aufbaut. Die Raum-`<option>`s dieser `<select>` entstehen
    aber erst durch ein `x-for` IN ihr - beim ersten Lauf von `:value`
    existierten sie schlicht noch nicht, die Zuweisung lief ins Leere, der
    Browser blieb bei der ersten (statischen) Option "Ohne Raum" haengen,
    und der Effekt (haengt nur an `device.room`, das sich dabei nicht
    aendert) lief nie erneut nach. Getestet gegen das eingecheckte
    vendor/alpine.min.js in einem echten Browser (siehe
    .superpowers/sdd/final-fix-web-report.md): JEDE Kachel zeigte dauerhaft
    "Ohne Raum", unabhaengig vom tatsaechlichen Raum - drei Review-Runden
    lang unbemerkt, weil genau dieser servergerenderte Text-Test gruen
    blieb.

    Der Fix ersetzt `:value` durch `x-model="roomSelectDrafts[device.id]"` -
    ein zuweisbares, geraeteweises Feld, das Alpine nach JEDEM Aufbau
    (auch dem der Optionen) aktiv abgleicht, nicht nur bei einer Aenderung
    von `device.room`. `roomSelectDrafts` wird von `syncRoomSelectDraft`
    (app.js) aus `device.room` gehalten - dieser Test kann wieder nur
    belegen, DASS dieses Konstrukt ausgeliefert wird, nicht dass es im
    Browser korrekt anzeigt (das leistet der Harness-Nachweis im Bericht
    oben). Zusaetzlich bleibt die Grundaussage des ersetzten Tests gueltig
    und wird mitgeprueft: `newRoomFor` taucht in der Bindung selbst nicht
    auf, nur noch im `@change`-Handler, der es SCHREIBT."""
    client, _, _ = api
    page = (await client.get("/")).text
    select_start = page.index('class="room-select"')
    select_end = page.index("</select>", select_start)
    room_select = page[select_start:select_end]
    assert 'x-model="roomSelectDrafts[device.id]"' in room_select
    assert "newRoomFor" not in room_select
    assert '@change="onRoomSelectChange(device)"' in room_select

    # `:value=` darf innerhalb der Optionen vorkommen (`<option
    # :value="chip.key">` fuer jeden Raum-Chip) - verboten ist es nur auf
    # der `<select>` selbst, das war die alte, fehlerhafte Bindung.
    select_tag_end = room_select.index(">")
    select_tag = room_select[:select_tag_end]
    assert ":value=" not in select_tag


async def test_the_new_room_option_resets_the_draft_before_the_mode_starts(api):
    """Nachfolger von
    `test_the_new_room_option_resets_the_selects_own_value_before_the_mode_starts`:
    die Logik selbst wanderte mit dem Wechsel auf `x-model` (Fund 1) aus
    dem Inline-`@change`-Ausdruck in index.html in die eigene Methode
    `onRoomSelectChange` (app.js) - ein manuelles `$event.target.value =
    ...` ist mit `x-model` nicht mehr noetig, weil Alpine die `<select>`
    nach einer Aenderung von `roomSelectDrafts[device.id]` von selbst
    abgleicht.

    Der Grund fuer das Zuruecksetzen bleibt derselbe: waere
    `roomSelectDrafts[device.id]` das einzige, was die Anzeige steuert,
    zeigte die Auswahlliste beim Waehlen von "+ Neuer Raum ..." den Wert
    "__new__", bis das Textfeld erscheint. `onRoomSelectChange` setzt den
    Draft im `'__new__'`-Zweig deshalb zuerst auf den aktuellen Raum
    zurueck, bevor es `beginNewRoom` ruft - die Auswahlliste zeigt
    "__new__" dadurch nie, das Textfeld daneben ist die einzige sichtbare
    Neu-Raum-Affordanz.

    Wie beim vorigen Test: ein Lauf ohne Browser-Engine kann nur die
    Reihenfolge im Quelltext pruefen, nicht, dass die Auswahlliste sich im
    Browser tatsaechlich nicht kurz auf "__new__" zeigt (siehe
    .superpowers/sdd/final-fix-web-report.md fuer den Browser-Nachweis)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    method_start = script.index("onRoomSelectChange(device) {")
    method_end = script.index("\n    },", method_start)
    method_body = script[method_start:method_end]
    reset_index = method_body.index('this.roomSelectDrafts[device.id] = device.room || "";')
    begin_index = method_body.index("this.beginNewRoom(device);")
    assert reset_index < begin_index


async def test_the_room_select_leaves_new_room_mode_when_a_normal_room_is_picked(api):
    """Review-Fix Important #2 (zweite Runde auf demselben Fund, vor Fund 1):
    `newRoomFor` wurde bis dahin nur ueber `commitNewRoom` geloescht, also
    nur dann, wenn man das Textfeld beendet oder abbricht (Enter/Blur).
    Rein mit der Maus, ganz ohne Tastatur, blieb aber ein zweiter Weg offen:
    (1) "+ Neuer Raum ..." waehlen - `beginNewRoom` setzt `newRoomFor =
    device.id`; das Textfeld hat kein Autofocus, der Fokus bleibt also auf
    dem `<select>`. (2) Dasselbe, noch fokussierte `<select>` erneut
    oeffnen und einen bestehenden Raum waehlen - `onRoomSelectChange` (seit
    Fund 1 die Heimat dieser Logik, vormals ein Inline-`@change`-Ausdruck)
    nimmt dann den `saveRoom`-Zweig, nicht `beginNewRoom`, und
    `commitNewRoom` wird nie gerufen.

    Das Zuruecksetzen bleibt unabhaengig von Fund 1 richtig: wer einen
    echten Raum waehlt, hat den Neu-Raum-Modus damit verlassen, egal was
    die Auswahlliste zu dem Zeitpunkt zeigt. Ein liegen gebliebenes
    `newRoomFor` wuerde sonst weiterhin das (dann falsch platzierte)
    Neu-Raum-Textfeld einblenden.

    Wie bei den beiden Tests oben kann ein Lauf ohne Browser-Engine nur den
    Quelltext pruefen. Belegt wird, dass der `saveRoom`-Zweig von
    `onRoomSelectChange` `newRoomFor` ausdruecklich auf `null` setzt, BEVOR
    er `saveRoom` ruft."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    method_start = script.index("onRoomSelectChange(device) {")
    method_end = script.index("\n    },", method_start)
    method_body = script[method_start:method_end]
    assert "this.newRoomFor = null;" in method_body
    assert "this.saveRoom(device, value);" in method_body
    null_index = method_body.index("this.newRoomFor = null;")
    save_index = method_body.index("this.saveRoom(device, value);")
    assert null_index < save_index


async def test_the_room_picker_closes_new_room_mode_when_focus_leaves_it_entirely(api):
    """Runde 3, hier erstmals behoben: Waehlt man "+ Neuer Raum ..." und
    klickt dann einfach irgendwo anders hin - ohne das Textfeld zu
    fokussieren, ohne Enter, ohne Tab -, feuert `<select>` kein `change` und
    das Textfeld kein `blur`. Nichts im bisherigen Code haette `newRoomFor`
    je zurueckgesetzt: ein verwaistes, leeres Textfeld waere dauerhaft
    sichtbar geblieben (die Auswahlliste selbst zeigt dank der Entkopplung
    zwar weiterhin korrekt `device.room`, das Textfeld bliebe aber offen).

    Statt eines weiteren Sonderfalls traegt ein gemeinsamer Wrapper
    (`.room-picker`) eine Fokus-Wache: `@focusout` prueft, ob
    `$event.relatedTarget` - das Element, das den Fokus als naechstes
    bekommt - noch INNERHALB des Wrappers liegt. Nur wenn nicht (der Fokus
    verlaesst `<select>` UND das Textfeld), wird `newRoomFor` geloescht.
    Wandert der Fokus von der Auswahlliste ins gerade erschienene Textfeld -
    die normale Fortsetzung, kein Abbruch -, bleibt `relatedTarget`
    innerhalb des Wrappers, und der Modus bleibt bestehen."""
    client, _, _ = api
    page = (await client.get("/")).text
    picker_start = page.index('class="room-picker"')
    span_start = page.rindex("<span", 0, picker_start)
    span_end = page.index("</span>", picker_start)
    room_picker = page[span_start:span_end]
    assert "@focusout=" in room_picker
    assert "$event.relatedTarget" in room_picker
    assert "newRoomFor = null" in room_picker
    assert 'class="room-select"' in room_picker
    assert 'class="room-new"' in room_picker


async def test_reconcile_room_filter_falls_back_to_all_when_the_filtered_room_vanishes(api):
    """Fund 2 (Review vom 2026-09-05), zwei Runden: Verschiebt man per
    Kachel-Auswahlliste das letzte Geraet eines gefilterten Raums in einen
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
