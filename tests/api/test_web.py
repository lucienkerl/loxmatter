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

    Aufgabe 8 hat die Kachel seither erneut umgebaut (immer offen statt
    per Klick aufgeklappt) und dabei die Ueberschrift auf das schlichte
    „Werte“ verkuerzt - ein eigener Hinweis auf "funktional" waere dort
    ueberfluessig geworden, denn die Kachel zeigt ohnehin ausschliesslich
    die funktionalen Signale, ohne Umschalter und ohne Anspruch auf eine
    Rangfolge. Die urspruengliche Sorge des Tests - eine Ueberschrift, die
    mehr verspricht als die Kachel haelt - bleibt trotzdem gueltig zu
    pruefen: die alte, falsche Formulierung darf nirgends mehr auftauchen,
    und die aktuelle Ueberschrift muss ausgeliefert werden.

    Belegt nur, dass die aktuelle Beschriftung ausgeliefert wird und die
    alte verschwunden ist - nicht, dass die damit gezeigten Signale zur
    Laufzeit tatsaechlich die funktionalen sind (siehe Testdocstring
    oben).

    Aufgabe 11: die Ueberschrift traegt seither `x-text="t('web.devices.
    values_heading')"` statt des festen Literals "Werte" - siehe
    `test_the_device_card_static_text_is_translated` fuer die Bindung
    selbst; hier bleibt nur der Beleg, dass die beiden ueberholten
    Formulierungen nicht zurueckgekehrt sind."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert "x-text=\"t('web.devices.values_heading')\"" in page
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
    assert "Als Experte zurückgehalten" in page
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
    Testdocstring oben)."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert 'x-model="hideNoise"' in page
    assert "Heartbeat und Full-Resend ausblenden" in page
    assert 'x-model="logLevel"' in page
    assert "Log-Stufe" in page
    assert "diagnosticsPaused = !diagnosticsPaused" in page
    assert "Pausieren" in page and "Fortsetzen" in page
    assert "clearDiagnosticsBuffers()" in page
    assert "Leeren" in page
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
    bridge_ip`), das denselben Wert nur noch anzeigt."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    editable_label = _label_around(markup, 'x-model="settingsDraft.bridge_ip')
    assert "Miniserver" not in editable_label, editable_label
    assert "IP dieser Brücke" in editable_label, editable_label

    readonly_label = _label_around(markup, ':value="bridgeSettings.bridge_ip')
    assert "Miniserver" not in readonly_label, readonly_label
    assert "IP dieser Brücke" in readonly_label, readonly_label


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
    Zugriff auf `this` des Alpine-Bauteils. Beide trugen denselben deutschen
    Literal (siehe Inventar §13); beide muessen jetzt denselben
    `t("web.errors.bridge_unreachable")`-Aufruf tragen, keinen mehr fest im
    Text."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "Die Brücke ist nicht erreichbar" not in script
    assert script.count('t("web.errors.bridge_unreachable")') == 2
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
        assert (
            f"@click=\"selectView('{view_key}')\" x-text=\"t('web.nav.{view_key}')\"" in page
        )


async def test_the_header_logout_and_connection_banner_are_translated(api):
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "x-text=\"t('web.header.logout')\"" in markup
    assert ">Abmelden<" not in markup
    assert "x-text=\"t('web.connection.lost_banner')\"" in markup
    assert "Die Live-Verbindung wurde unterbrochen" not in markup
    assert ':title="t(\'web.header.toast_dismiss_tooltip\')"' in markup
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
        'return t("web.header.time_ago_hours", { hours: Math.round(minutes / 60) });'
        in since_body
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
    assert (
        'toLocaleString(this.language === "de" ? "de-DE" : "en-US")' in ts_body
    )
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
    assert ':placeholder="t(\'web.devices.code_placeholder\')"' in markup
    assert "Pairing-Code (11-stellig" not in markup
    assert ':placeholder="t(\'web.devices.thread_dataset_placeholder\')"' in markup
    assert "Thread-Datensatz" not in markup
    assert "x-text=\"t('web.devices.commission_submit')\"" in markup
    assert ">Einlernen<" not in markup
    assert "x-text=\"t('web.devices.commission_hint')\"" in markup
    assert "Hängt das Gerät schon in Apple" not in markup
    assert "x-text=\"t('web.devices.empty')\"" in markup
    assert "Noch kein Gerät eingelernt." not in markup


async def test_the_device_card_static_text_is_translated(api):
    """Aufgabe 11, Schritt 3: Statuspillen, Entfernen-Knopf, die beiden
    Werte-/Bedienungs-Abschnittsueberschriften mit ihren Ladehinweisen und
    Leerzustaenden, der Wert-Platzhalter, der Senden-Knopf und der
    Exportieren-Knopf der Geraetekarte - jeweils als reiner `x-text`, weil
    keiner dieser Schluessel eingebettetes HTML traegt."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "x-text=\"t('web.devices.changed_since_export')\"" in markup
    assert "Geändert seit Export" not in markup
    assert "x-text=\"t('web.devices.offline')\"" in markup
    assert ">Offline<" not in markup
    assert "x-text=\"t('web.devices.remove')\"" in markup
    assert ">Entfernen<" not in markup
    assert "x-text=\"t('web.devices.values_heading')\"" in markup
    assert ">Werte<" not in markup
    assert "x-text=\"t('web.devices.signals_loading')\"" in markup
    assert "Signale werden geladen" not in markup
    assert "x-text=\"t('web.devices.no_functional_signals')\"" in markup
    assert "Keine funktionalen Signale" not in markup
    assert "x-text=\"t('web.devices.controls_heading')\"" in markup
    assert ">Bedienung<" not in markup
    assert "x-text=\"t('web.devices.controls_loading')\"" in markup
    assert "Bedienelemente werden geladen" not in markup
    assert "x-text=\"t('web.devices.no_known_commands')\"" in markup
    assert "Keine bekannten Ausgangsbefehle" not in markup
    assert ':placeholder="t(\'web.devices.value_placeholder\')"' in markup
    assert 'placeholder="Wert"' not in markup
    assert "x-text=\"t('web.devices.send')\"" in markup
    assert ">Senden<" not in markup
    assert "x-text=\"t('web.devices.export')\"" in markup
    assert ">Exportieren<" not in markup


async def test_the_remaining_count_hints_keep_their_dynamic_span_and_translate_the_rest(api):
    """Aufgabe 11, Schritt 3: die beiden "N weitere..."-Hinweise bestehen aus
    einem dynamischen Zaehler-`<span>` (`remainingSignalCount`/
    `hiddenRawCommandsFor`) gefolgt von statischem Text - nur der statische
    Teil wandert auf `t(...)`, der Zaehler-Ausdruck bleibt unveraendert."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert '<span x-text="remainingSignalCount(device.id)"></span>' in markup
    assert "x-text=\"t('web.devices.more_in_signals_view')\"" in markup
    assert "weitere in der Ansicht" not in markup
    assert '<span x-text="hiddenRawCommandsFor(device.id)"></span>' in markup
    assert "x-text=\"t('web.devices.more_commands_unnamed')\"" in markup
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
    assert '@click.prevent="selectView(\'settings\')"' in devices_markup


async def test_the_device_list_dynamic_errors_and_toasts_are_translated(api):
    """Aufgabe 11, Schritt 4: die Lade-/Speicher-/Entfernen-Fehler, die
    Export-Hinweise (`exportHintFor`) und die beiden Kommando-Toasts der
    Geraeteliste tragen jetzt `t(...)` mit denselben Platzhaltern wie
    vorher die Template-Strings."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert 'this.devicesError = t("web.devices.list_load_error", { message: error.message });' in script
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
    assert 'this.commissionMessage = t("web.devices.commission_success", { label: device.label });' in body
    assert "wurde eingelernt" not in body
    assert 'this.commissionMessage = t("web.devices.commission_failed", { message: error.message });' in body
    assert "Einlernen fehlgeschlagen" not in body


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
