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

"""Tests for the delivery of the WebUI (Task 7, Phase 5) - see
`loxone/server.py` (routes `/` and `/static`) and `web/` (the actual
UI).

`api` follows the same pattern as `test_diagnostics.py`: one test file,
one local `api` fixture, built from the shared building blocks in
`conftest.py` (`no_invoke`, `fake_runtime`, `fake_client`). These tests
need no device in the store - the UI is delivered before any click ever
happens - but build one anyway, so a later test in this file (e.g. a
spot check on `/api/devices`) can do without a second fixture.
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
    """The page without its HTML comments.

    The comments in `index.html` are extensive and name attributes and
    labels explicitly - among other things, to explain why they are NOT
    there. A search over the raw file therefore also finds the very thing
    the comment is warning about."""
    return re.sub(r"<!--.*?-->", "", markup, flags=re.DOTALL)


def _label_around(markup: str, needle: str) -> str:
    """The `<label>` element that contains `needle` - the label that
    actually appears on screen next to an input field."""
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
    """The bridge runs in installations without internet access."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert "cdn." not in page
    assert "unpkg" not in page
    assert (await client.get("/static/vendor/alpine.min.js")).status_code == 200


async def test_the_page_names_all_four_views(api):
    """Task 10 binds the tab bar to `t('web.nav.*')` (Task 9) instead of
    writing the German names straight into the markup - the delivered
    source therefore no longer carries any of the old literals, but the
    five `x-text` bindings (the translation itself only happens at
    runtime in the browser, see `test_load_i18n_...` in Task 8)."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert ">Geräte<" not in page
    for key in ("devices", "export", "system", "settings"):
        assert f"x-text=\"t('web.nav.{key}')\"" in page


async def test_the_page_does_not_promise_what_the_spec_excludes(api):
    """Spec 8.2: a commissioning and diagnostics tool, not a smart-home UI.

    Checks not only `index.html` but also `/static/app.js` (review fix
    Minor #3, 2026-09-02): the four words are currently absent from both
    files, so this was not a false green so far - but a future feature
    whose German text is only ever assembled in JavaScript (e.g. built
    dynamically instead of appearing in the markup) would otherwise slip
    past this guard without it ever noticing."""
    client, _, _ = api
    page = (await client.get("/")).text.lower()
    script = (await client.get("/static/app.js")).text.lower()
    for absent in ("szene", "zeitplan", "automatisierung", "favorit"):
        assert absent not in page
        assert absent not in script


async def test_the_page_carries_an_icon_that_is_actually_ausgeliefert(api):
    """A `link rel="icon"` pointing at nothing goes unnoticed by anyone - the
    browser just quietly shows its default sheet. Hence both checks in one
    test here: that the page names the icon AND that something actually
    lives at the named path."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert 'href="/static/favicon.svg"' in page
    response = await client.get("/static/favicon.svg")
    assert response.status_code == 200
    assert "svg" in response.headers["content-type"]


async def test_the_mark_stands_in_the_interface_in_both_its_sizes(api):
    """The mark previously sat only in the browser tab, not in the UI.

    Checked here is the size rule that the header comment in `icon.svg`
    sets out: the large version applies "from about 32 px up", below that
    `favicon.svg` takes over with heavier strokes, because the six outer
    dots would otherwise merge into each other. The header (24 px) must
    therefore name `favicon.svg`, and the two login screens (64 px) must
    name `icon.svg` - a swapped pair looks equally "right" in the test and
    in the browser, and only shows up on close inspection.

    `alt=""` needs checking too: "loxmatter" appears right next to it in
    all three headings, and a filled-in alt text would make a screen
    reader read the name out twice."""
    client, _, _ = api
    page = (await client.get("/")).text

    assert '<img class="brand-mark" src="/static/favicon.svg" alt=""' in page
    # Twice: once above the login, once above the initial setup.
    assert page.count('<img class="auth-mark" src="/static/icon.svg" alt=""') == 2

    for name in ("icon.svg", "favicon.svg"):
        response = await client.get(f"/static/{name}")
        assert response.status_code == 200
        assert "svg" in response.headers["content-type"]


def test_the_stylesheet_has_balanced_braces():
    """Eine nicht geschlossene CSS-Regel verschluckt ALLES, was ihr folgt -
    ohne Fehlermeldung, ohne dass ein Test es merkt.

    Genau das ist beim Zusammenfuehren von main passiert (2026-09-08): ein
    Konfliktmarker schnitt mitten durch `.signals-summary`, die Aufloesung
    nahm die schliessende Klammer mit, und damit war das gesamte Stylesheet
    ab dieser Zeile wirkungslos. Das Signal-Modal fiel auf den Zustand
    zurueck, den der ganze Umbau beseitigt hatte: kein Raster, ausgefranste
    Zeilen, der Spaltenkopf als Fliesstext.

    Die Testreihe blieb dabei vollstaendig gruen. Sie kann das auch gar
    nicht sehen: jede CSS-Zusicherung in dieser Datei sucht Zeichenketten in
    der ausgelieferten Datei, und die Zeichenketten standen ja alle noch
    darin - nur eben in totem Text. Aufgefallen ist es erst am neu
    erzeugten Screenshot.

    Dieser Test ist die billigste Absicherung dagegen: er versteht kein CSS,
    er zaehlt nur. Kommentare werden vorher entfernt, weil `{` und `}` darin
    vorkommen duerfen (und in diesem Stylesheet reichlich vorkommen, es ist
    dicht kommentiert)."""
    css = (WEB_DIR / "style.css").read_text(encoding="utf-8")
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

    depth = 0
    for number, line in enumerate(without_comments.split("\n"), start=1):
        depth += line.count("{") - line.count("}")
        assert depth >= 0, f"eine Klammer zuviel geschlossen, Zeile {number}"

    assert depth == 0, f"{depth} Regel(n) nicht geschlossen - alles danach ist wirkungslos"


def test_the_icons_are_well_formed_xml():
    """An SVG that does not parse as XML is displayed by NO browser - it
    silently hides it as a broken image, without a message anywhere.

    That is exactly what happened on the first attempt: the header comment
    in icon.svg named the accent colour `--accent` by its CSS name, and two
    consecutive hyphens are forbidden inside an XML comment. The file was
    invisible on GitHub and in the browser tab alike. A look at the file
    does not reveal that, a parser does."""
    for name in ("icon.svg", "favicon.svg"):
        ElementTree.parse(WEB_DIR / name)


async def test_the_inline_icon_symbols_are_well_formed_xml(api):
    """The same finding as above, but for the inline `<svg style="display:
    none">` in `index.html` instead of the two standalone files - nothing
    has parsed that so far. The same holds for a `<symbol>` as for a
    standalone SVG file: a `<use xlink:href="#i-...">` that points at a
    symbol whose markup does not pass as XML draws SILENTLY nothing - no
    error message in the console, just a tile without an icon, see the
    comment on `i-cat-other` in `index.html`.

    Der Block traegt inzwischen sechzehn `<symbol>`-Definitionen, acht davon
    aus dem Geraete-Tab-Umbau (Entwurf 2026-09-05, Abschnitt 6.5) - keine
    davon war bislang durch einen Parser gelaufen. Ein einzelner falscher
    Bindestrich oder ein nicht geschlossenes Tag in einem neuen Symbol waere
    also erst im Browser aufgefallen, und selbst dort nur als leere Flaeche,
    nie als Meldung."""
    client, _, _ = api
    page = (await client.get("/")).text
    match = re.search(r'<svg style="display: none".*?</svg>', page, flags=re.DOTALL)
    assert match, "inline SVG symbol block not found"
    ElementTree.fromstring(match.group(0))


async def test_static_files_do_not_escape_their_directory(api):
    client, _, _ = api
    response = await client.get("/static/../../../etc/passwd")
    assert response.status_code in (404, 400)


# ---------------------------------------------------------------------------
# Setup and login instead of a token field (Task 7, WebUI login). Without a
# browser there is no clicking to be done here - but what can be checked is
# that the delivered files carry the properties without which the flow
# demonstrably CANNOT work.
# ---------------------------------------------------------------------------


async def test_the_interface_offers_setup_and_login_instead_of_a_token_field(api):
    """Successor to `test_the_interface_offers_a_field_to_enter_the_token`
    (review fix Fix 1). Both screens are unconditionally present in the
    delivered markup - Alpine only shows or hides them in the browser via
    `x-if`/`x-show`, so a test without a browser engine always sees both.
    Checked here: three password fields of type `password` (two for setup,
    one for login - type `password` so nothing can be read over someone's
    shoulder), the two submit labels, and that the old token input is
    gone."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert page.count('type="password"') == 3
    assert "x-text=\"t('web.auth.setup_submit')\"" in page
    assert "x-text=\"t('web.auth.login_submit')\"" in page
    assert "token-box" not in page
    assert "token-input" not in page


async def test_no_plain_link_points_at_a_token_protected_route(api):
    """An `<a href>` would replace the page with the error response's raw
    text on any error response (today, e.g. a 401 after an expired
    session). Every download under `/api` must therefore go through
    `fetch()` (see `requestDownload` in app.js)."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert 'href="/api' not in page


async def test_no_secret_travels_in_a_url_or_local_storage(api):
    """Successor to `test_the_token_never_travels_in_a_url`: since the
    WebUI login (Task 7) there is no longer a token that could leak via the
    browser - the password travels exclusively in the body of a POST to
    `/auth/setup` or `/auth/login`, the session exclusively as an
    `HttpOnly` cookie that this script never touches. What is checked here
    is that both former paths for that have disappeared from the UI: no
    more `Bearer` header (a comment in `requestJson` does still mention the
    word `Authorization` while explaining WHY it is absent - that is not a
    false green, it is deliberately left unchecked here), no `localStorage`,
    no secret in a URL."""
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
    """Follow-up fix, Task 6 (2026-09-03): every (re-)connection of the
    diagnostics channel (`/api/diagnostics/live`) receives a snapshot from
    the server of up to `SNAPSHOT_LIMIT` entries per stream, in EXACTLY the
    same message shape as a live line - with no marker identifying it as a
    snapshot (see `api/diagnostics_live.py`). Without clearing the three
    held streams BEFORE every (re-)build, this snapshot simply appended
    itself to what was already held: switching away from "System" and back,
    or any automatic reconnection after a network hiccup, would have
    appended up to 150 already-present lines a second time.

    **What this test proves and what it does not.** Without a browser
    engine there is no way to run here whether `connectDiagnosticsLive()`
    actually clears `this.datagrams`/`this.commandLog`/`this.diagnosticsLogs`
    at runtime, or whether switching views even calls this function. What is
    proven is only that the DELIVERED source calls `clearDiagnosticsBuffers()`
    within the body of `connectDiagnosticsLive()` - and specifically BEFORE
    building the new `WebSocket`, not only afterwards (otherwise the old
    connection's snapshot could still get in the way of the clearing)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    start = script.index("connectDiagnosticsLive() {")
    end = script.index("disconnectDiagnosticsLive() {", start)
    body = script[start:end]

    assert "clearDiagnosticsBuffers()" in body
    assert body.index("clearDiagnosticsBuffers()") < body.index("new WebSocket(")


async def test_the_browser_and_the_server_agree_on_the_download_filenames(api):
    """Since both downloads run through `fetch` instead of a link, the
    browser assigns the filename itself - the server still sends its own
    along regardless. Two names for the same file in two places would each
    look plausible on their own; a drift between them would only be
    noticed by the user holding the wrongly named file."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert f'"{ARCHIVE_NAME}"' in script
    assert f'"{FABRIC_BACKUP_NAME}"' in script


async def test_the_page_declares_a_doctype(api):
    """Without `<!doctype html>`, every browser renders the page in quirks
    mode (`document.compatMode === "BackCompat"`) - a compatibility mode for
    pages from the nineties, in which, among other things, the box model
    and percentage heights compute differently from any rule in
    `style.css`. What is proven here is only that the declaration is
    delivered; whether the layout actually looks different as a result is
    something no test in this suite can say without a browser engine."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert page.lower().startswith("<!doctype html>")


async def test_the_page_does_not_call_init_a_second_time(api):
    """Alpine 3 calls `init()` of an `x-data` object on its own. An
    additional `x-init="init()"` on the same element calls it a second
    time - and `init()` starts the live WebSocket after a logged-in
    session: every open tab thus held two connections, of which only the
    most recently opened one ended up in `this.socket`; the other one
    stayed invisible and kept running until the tab was closed.

    **What this test proves and what it does not.** It proves that none of
    the delivered `x-init` expressions calls `init()`. It does NOT prove
    that a real page load ends up leaving exactly one observer behind -
    that would need a browser engine that actually runs Alpine, and this
    suite does not have one (`Runtime.observer_count()` after a simulated
    call would have been the direct measure). A second call via a
    different route - an `Alpine.start()` by hand, a second
    `x-data="app()"` - would slip past this guard.

    2026-09-05/06: the signal list in the device modal has since started
    using `x-init` itself, to set the initial state of its two `<details>`
    groups (see `index.html`), without having anything to do with the bug
    guarded against here. Since then the guard therefore no longer checks
    whether `x-init` occurs at all, only whether one of its expressions
    calls `init(` - for the bug being guarded against, this is at least as
    sharp as before: an `x-init="init()"` on a nested element, which the
    old blanket check only caught by coincidence, is deliberately caught by
    the new one.

    The regex matches `x-init="..."` AND `x-init='...'` - every occurrence
    in this codebase today uses double quotes, but the check should not
    silently slip past a singly-quoted occurrence (Finding 2, review of the
    follow-up work, 2026-09-06).

    The loop alone proves nothing if the regex finds no match - if some
    future `x-init` spelling (different quoting, different attribute
    format) no longer matched, the guard would silently run empty instead
    of failing. The extra `assert` below keeps the guard sharp by demanding
    at least one match (Finding 8, review of the follow-up work,
    2026-09-06)."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert 'x-data="app()"' in markup
    expressions = re.findall(r"x-init=[\"']([^\"']*)[\"']", markup)
    assert expressions, "no x-init found in the delivered page"
    for expression in expressions:
        assert "init(" not in expression, f"x-init calls init(): {expression}"


async def test_the_signal_view_ships_a_functional_and_an_expert_block(api):
    """Task 8: the signal list should be organised into "Functional" (open)
    and "Expert" (collapsed, with a count), instead of showing all 159
    signals of a device flatly one after another.

    **What this test proves and what it does not.** What is proven is only
    that the delivered files (`index.html`, `app.js`) contain the building
    blocks needed for that: both headings, and - in the script - that the
    two lists are actually distinguished via `signal.functional` rather
    than via a second relevance rule rebuilt in JavaScript. What is NOT
    proven is that Alpine actually turns this into two separate, correctly
    filtered blocks at runtime, or that the grouping looks right for a real
    device - that would need a browser engine, which this suite does not
    have (see `test_the_page_does_not_call_init_a_second_time` above).

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
    # Both lists only read the field supplied by the API, no separate
    # JavaScript version of `profiles.relevance.is_functional`.
    assert "signal.functional" in script


async def test_the_signal_row_offers_a_resend_checkbox(api):
    """Periodic resend as an opt-in (design 2026-09-04) - the same kind of
    proof as the functional/expert test above: only that the building
    blocks are delivered and read/write `signal.resend`, not that Alpine
    renders them correctly at runtime (see the docstring there).

    Aufgabe 7 ersetzt das fruehere `x-show="signal.exportable"` am
    umschliessenden `<label>` durch `:disabled="!signal.exportable"` am
    `<input>` selbst: die Rasterzeile (`.signal-grid`) braucht in JEDER
    Zeile dieselbe Anzahl Zellen, sonst verschieben sich die Spalten der
    nicht-exportierbaren Signale gegen die Kopfzeile - genau das Fluchten,
    das diese Aufgabe zusichert. Ein deaktiviertes statt verstecktes
    Kaestchen belegt denselben Fall: `resend_marked()` kann fuer ein
    nicht-exportierbares Signal ohnehin nie etwas bewirken (`_last_values`
    bleibt dort leer, siehe `Runtime._cache_attribute`), es laesst sich nur
    nicht mehr anklicken."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    page = (await client.get("/")).text
    assert "toggleResend" in script
    assert "signal.resend" in page

    resend_idx = page.index("signal.resend")
    label_start = page.rindex("<label", 0, resend_idx)
    label_end = page.index("</label>", resend_idx) + len("</label>")
    resend_label = page[label_start:label_end]
    assert ':disabled="!signal.exportable"' in resend_label


async def test_the_settings_view_offers_a_resend_interval_field(api):
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert "resendIntervalDraft" in page
    assert "saveResendInterval" in script


async def test_the_device_tile_no_longer_promises_a_ranking_it_does_not_have(api):
    """Review fix Fix 9 (2026-09-03) had deliberately renamed the heading
    "Most important values" to "Signals (start of the list)", because the
    signals shown at the time were only filtered by `exportable` - for the
    test template that meant NetworkCommissioning and BasicInformation
    instead of on/off and power. Now that `signal.functional` supplies the
    real selection criterion, the old, more honest wording is accurate
    again.

    Task 8 (Raster-Umbau, 2026-09-05) hat die eigene Werte-Ueberschrift
    danach ganz entfernt: die Kachel zeigt heute (seit dem Wegfall des
    Leitwerts, Entwurf 2026-09-07) alle funktionalen Signale gleichrangig
    als fluchtendes Raster ohne Abschnittstitel - eine Ueberschrift ueber
    der einzigen Werteliste einer sonst schon
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
    """Follow-up fix, Fix 3 (final review): `hidden_count` already came
    from `GET /api/export/preview` before this, but arrived nowhere in the
    UI - the preview table had columns for inputs, commands, and skipped
    items, none for signals held back as expert-only. Like the other
    markup tests in this file, this proves only that the delivered page
    contains the column and its binding to `device.hidden_count` - not
    that Alpine actually populates it correctly at runtime (that would
    need a browser engine, see
    `test_the_page_does_not_call_init_a_second_time`)."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert "x-text=\"t('web.export.col_expert_withheld')\"" in page
    assert 'x-text="device.hidden_count"' in page


# ---------------------------------------------------------------------------
# Live diagnostics (Task 6, Spec 10.5): the "System" view has since fetched
# logs, UDP capture, and the command log continuously via
# `/api/diagnostics/live` instead of once via GET. As with the other markup
# tests in this file: without a browser engine only that something is
# delivered can be proven - not that it works at runtime.
# ---------------------------------------------------------------------------


async def test_the_system_view_connects_to_the_diagnostics_live_socket(api):
    """Task 6, steps 1+2: the diagnostics channel follows the same pattern
    as `connectLive()` for the values channel, but only opens on switching
    to "System" and closes on leaving it - `selectView` (app.js) is the
    single place for that.

    **What this test proves and what it does not.** What is proven is that
    `app.js` actually contacts `/api/diagnostics/live` and that
    `selectView` contains both the opening and the closing call. What is
    NOT proven is that a real page load ends up holding exactly one
    connection, that it actually closes on leaving the view, or that no
    reconnection is scheduled anymore after it has closed - that would
    need a browser engine, which this suite does not have (see
    `test_the_page_does_not_call_init_a_second_time`)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "/api/diagnostics/live" in script
    select_view_start = script.index("async selectView(view)")
    select_view_body = script[select_view_start : select_view_start + 800]
    assert "connectDiagnosticsLive()" in select_view_body
    assert "disconnectDiagnosticsLive()" in select_view_body


async def test_the_system_view_offers_the_four_diagnostics_controls(api):
    """Task description, step 3: "Three sections, above them the four
    controls from the design" - pause/resume, the noise filter, the log
    level, and a way to clear the held lines.

    Proves only that markup and script carry the four bindings - not that
    a click in the browser actually toggles anything (see the test
    docstring above).

    Since Task 14 the labels carry `t(...)` calls instead of fixed German
    literals - here it is now only proven that the bindings themselves
    (attributes, handlers, default values) are unchanged."""
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
    # Defaults from the task description (step 2): filter off, log level
    # "INFO".
    assert "hideNoise: true" in script
    assert 'logLevel: "INFO"' in script


async def test_the_diagnostics_filter_only_affects_display_not_held_lines(api):
    """Design item 4 (task description): a filter may only affect the
    display, not the held lines - whoever switches it off must see the
    existing lines immediately, not wait for new ones. Implemented as two
    separate things in `app.js`: `datagrams`/`diagnosticsLogs` hold EVERY
    line that arrives, `visibleDatagrams()`/`visibleDiagnosticsLogs()`
    only filter from them when displaying.

    Proves that the markup actually binds to the filtering functions
    instead of the raw lists, and that these functions leave the raw lists
    unchanged (no `datagrams =`/`diagnosticsLogs =` within their body). It
    does NOT prove that toggling in the browser actually updates the
    display without delay - that would need a browser engine."""
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
    """The task description deliberately leaves open how "noise" (the
    heartbeat and a full resend) is recognised - but requires that the
    chosen rule is readable as a comment, not just implicit in the code:
    "a filter whose criterion nobody can look up is worthless the next
    time it's doubted."

    Since the follow-up fix (Task 6, 2026-09-03), the criterion no longer
    lives in the browser: an earlier rule based on arrival rate wrongly
    marked any fast succession of genuine value changes (e.g. a pulse and
    a counter from `Runtime.on_event`) as noise - the chosen criterion is
    instead the `forced` field sent along by the server
    (`DatagramLogEntry.forced`).

    Proves only that such a comment exists and names the field, its
    source, and the disproven earlier rule by name - not that the
    distinction actually separates noise from genuine changes correctly at
    runtime (for that, see `tests/loxone/test_sender.py`,
    `test_the_forced_field_reflects_why_a_datagram_was_sent_not_when`, and
    `tests/api/test_diagnostics_live.py`,
    `test_a_datagram_message_carries_why_it_was_sent`)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "message.forced" in script
    assert "DatagramLogEntry.forced" in script
    assert "burst" in script
    assert "full resend" in script


async def test_the_export_field_asks_for_the_bridge_not_the_miniserver(api):
    """This field's value becomes the `Address` of the virtual UDP input
    and the body of the command URLs (`http://<ip>:<listen>`) - both of
    them the address of THIS bridge, never the Miniserver's. When the
    field was labelled "Miniserver IP", the user consequently entered the
    wrong one of the two addresses and got configurations that looked
    correct and stayed silent: the commands went back to the Miniserver
    itself, and its address filter discarded the bridge's datagrams - with
    no error message, exactly the kind of silent failure Spec 8.1 aims to
    rule out.

    Two places show the field (device dashboard design, sections 4/5):
    editable in settings (`settingsDraft.bridge_ip`) - where someone
    actually types into it, where a wrong label would be most costly - and
    read-only in the export tab (`bridgeSettings.bridge_ip`), which only
    displays the same value.

    Both places have since carried `t('web.bridge_ip_label')` - the same
    key: the export tab since Task 13, the settings view since Task 15."""
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
    """The age indicator used to sit next to the value and change its
    width every second - that shifted the row back and forth and drew the
    eye to the movement instead of the change (2026-09-03).

    Now the cell's `title` carries it, and the change shows a highlight
    that then fades again. This proves that both are delivered - NOT that
    they look that way in the browser: no engine that applies CSS or runs
    Alpine runs in this suite.
    """
    client, _, _ = api
    page = (await client.get("/")).text
    assert "signalAgeTitle(signal)" in page
    assert "'value-fresh': signalIsFresh(signal)" in page
    # No longer anywhere in the text flow - that was the cause of the jitter.
    assert 'x-text="signalSeenText(signal)"' not in page


async def test_the_highlight_cannot_change_the_width_of_a_cell(api):
    """Padding and radius must belong to the base state, not to the
    highlight: if they were added along with it, the jitter would be back
    - just in a different place."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    base = css.split(".value {", 1)[1].split("}", 1)[0]
    highlight = css.split(".value-fresh {", 1)[1].split("}", 1)[0]
    assert "padding" in base
    assert "padding" not in highlight
    assert "border-radius" in base
    assert "border-radius" not in highlight


# ---------------------------------------------------------------------------
# A device without a primary signal (tile header).
#
# The only place in this suite that actually EXECUTES `app.js`, instead of
# reading the delivered text. The reason: the bug at issue here does not
# live in the markup but in the behaviour of the three helpers - a text
# spot check for `if (!signal)` would stay green even if the condition
# does the wrong thing. Alpine still does not run along with it; the unit
# under test is the state object delivered by `app()`.
# ---------------------------------------------------------------------------

NODE = shutil.which("node")


def _app_state(setup: str = "") -> dict:
    """Loads `app.js` in node, calls `app()`, and runs `setup` on it.

    `app.js` is a simple script with no module system (deliberately, see
    the header of the file) - hence `new Function` instead of an import.
    """
    script = f"""
      const fs = require("node:fs");
      const src = fs.readFileSync({str(WEB_DIR / "app.js")!r}, "utf8");
      const state = new Function(src + "\\nreturn app();")();
      {setup}
    """
    # `check=False`, because the line below reports the same failure with
    # the more useful text: `stderr` shows WHAT node failed on,
    # `CalledProcessError` only THAT it failed.
    result = subprocess.run(
        [NODE, "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_the_signal_helpers_tolerate_null_without_throwing():
    """Frueher `test_a_device_without_a_lead_signal_does_not_throw_in_any_binding`.

    Der Aufhaenger war der Leitwert: zwischen `GET /api/devices` und
    `GET /api/devices/<id>/signals` liegt ein Rendering-Durchlauf, in dem
    `signalsByDevice` fuer das Geraet noch LEER ist - `leadSignalFor`
    lieferte dann `null`. Das `x-show` auf der Huelle half nicht: es setzt
    nur `display`, es haelt Alpine NICHT davon ab, die Ausdruecke der
    Kinder auszuwerten. Die drei Helfer lasen also `signal.key` auf `null`
    und warfen - dreimal pro Geraet, bei jedem Durchlauf.

    Diesen Aufrufer gibt es nicht mehr (Entwurf 2026-09-07): das `x-for`
    des Werterasters laeuft ueber eine leere Liste und wertet gar nichts
    aus. Die Duldsamkeit der Helfer bleibt trotzdem stehen und wird
    weiterhin belegt - der Test fragt sie jetzt direkt statt ueber einen
    Aufrufer, den es nicht mehr gibt.
    """
    values = _app_state(
        """
        state.signalsByDevice = {};
        const out = { calls: {} };
        for (const fn of ["signalIsFresh", "signalAgeTitle", "liveValueOf"]) {
          try {
            out.calls[fn] = { ok: true, value: state[fn](null) ?? null };
          } catch (error) {
            out.calls[fn] = { ok: false, error: error.message };
          }
        }
        out.formatted = state.formatValue(state.liveValueOf(null));
        console.log(JSON.stringify(out));
        """
    )

    for name, call in values["calls"].items():
        assert call["ok"], f"{name} threw: {call.get('error')}"

    # What the tile shows in this state: no highlight, no tooltip - and the
    # dash that `formatValue` uses for "no value".
    assert values["calls"]["signalIsFresh"]["value"] is False
    assert values["calls"]["signalAgeTitle"]["value"] in (None, "")
    assert values["calls"]["liveValueOf"]["value"] is None
    assert values["formatted"] == "-"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_signal_that_exists_is_unaffected_by_the_guard():
    """The safeguard must not distort the normal case: a real signal must
    still get its live value, its highlight, and its tooltip."""
    values = _app_state(
        """
        const signal = { key: "d1_1_onoff", title: "Zustand", value: false, functional: true };
        state.signalsByDevice = { 1: [signal] };
        state.liveValues = { d1_1_onoff: true };
        state.liveSeenAt = { d1_1_onoff: 1000 };
        state.nowTick = 1200;
        console.log(JSON.stringify({
          live: state.liveValueOf(signal),
          fresh: state.signalIsFresh(signal),
          title: state.signalAgeTitle(signal),
        }));
        """
    )

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


def test_first_signals_for_filters_orders_and_caps_at_the_preview_limit():
    """Nacharbeit 2026-09-07, Fund 3: Der Umbau auf das Werteraster (Entwurf
    2026-09-05) hat aus `test_a_signal_that_exists_is_unaffected_by_the_guard`
    die Zeile `assert values["lead"] == "d1_1_onoff"` entfernt - formal eine
    Aussage ueber das inzwischen geloeschte `leadSignalFor`. Tatsaechlich war
    das aber die einzige Assertion im Repo, die `firstSignalsFor` in einem
    echten `node`-Prozess ausfuehrte. `test_the_value_grid_now_carries_every_
    functional_signal` (tests/api/test_web.py) belegt seither nur noch, dass
    `x-for="signal in firstSignalsFor(device.id)"` als String ausgeliefert
    wird - nicht, was der Helfer selbst tut. Dieser Test schliesst die
    Luecke eigenstaendig, statt sie an einen Test mit anderem Zweck
    anzuflanschen.

    Geprueft werden alle drei Aufgaben von `firstSignalsFor` /
    `remainingSignalCount` zusammen (app.js): nicht-funktionale Signale
    fallen durch das `signal.functional`-Sieb, die Reihenfolge der
    Eingabe-Liste bleibt erhalten (kein Sortieren, kein Umschichten), und
    ab mehr als `FUNCTIONAL_PREVIEW_LIMIT` (6) funktionalen Signalen liefert
    `firstSignalsFor` genau sechs zurueck, waehrend `remainingSignalCount`
    den Rest zaehlt."""
    values = _app_state(
        """
        const signals = [
          { key: "s1", functional: true },
          { key: "s2", functional: false },
          { key: "s3", functional: true },
          { key: "s4", functional: true },
          { key: "s5", functional: false },
          { key: "s6", functional: true },
          { key: "s7", functional: true },
          { key: "s8", functional: true },
          { key: "s9", functional: true },
        ];
        state.signalsByDevice = { 1: signals };
        console.log(JSON.stringify({
          first: state.firstSignalsFor(1).map((signal) => signal.key),
          remaining: state.remainingSignalCount(1),
        }));
        """
    )

    # Sieben der neun Signale sind funktional (s1, s3, s4, s6, s7, s8, s9);
    # s2 und s5 muessen draussen bleiben, und die Reihenfolge der restlichen
    # sieben bleibt die der Eingabe-Liste - kein Sortieren nach Schluessel
    # oder sonst etwas.
    assert values["first"] == ["s1", "s3", "s4", "s6", "s7", "s8"]
    # Sieben funktionale Signale, Deckel bei sechs: genau eines bleibt uebrig.
    assert values["remaining"] == 1


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
# Translation mechanism (Task 8). This task does not yet translate any
# WebUI text of its own (that is Task 9+) - it only builds the plumbing:
# `t()` as a global, top-level function in app.js (also reachable from
# requestJson/requestDownload, which have no `this` access to the Alpine
# component), `stringsReady`/`language`/`loadI18n()` as reactive members
# of the `app()` object, and three additional stringsReady gates in
# index.html following the same pattern as the existing authReady.
# ---------------------------------------------------------------------------


async def test_the_translation_helper_is_a_global_top_level_function(api):
    """`t()` must not be a method of app() - `requestJson`/
    `requestDownload` (app.js, before `function app()`) have no access to
    `this` of the Alpine component, but themselves need translated text
    (later tasks). That is why `t()` sits as a top-level function ahead of
    `function app()`, backed by the likewise module-global, non-reactive
    variable `translationStrings` - neither one a field of the app()
    object.

    Proves only that the delivered source contains these building blocks
    in this order - not that Alpine actually resolves `t(...)` at runtime
    via the surrounding script scope (that would need a browser
    engine)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "function t(key" in script
    assert "let translationStrings" in script
    app_index = script.index("function app()")
    assert script.index("let translationStrings") < app_index
    assert script.index("function t(key") < app_index


async def test_strings_ready_and_language_are_reactive_fields_on_app(api):
    """Unlike t()/translationStrings, stringsReady/language REMAIN fields
    on the app() object - they must be reactive, so that
    x-if="stringsReady && ..." in index.html actually re-renders once
    loadI18n() has finished."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    app_body = script[script.index("function app()") :]
    assert "stringsReady: false" in app_body
    assert 'language: "en"' in app_body
    assert "async loadI18n()" in app_body


async def test_load_i18n_sets_the_document_language_via_dom_assignment(api):
    """<html lang> (index.html) sits OUTSIDE the x-data area (which only
    begins at <body>) - an Alpine directive could not bind there. It is
    therefore set via a plain DOM assignment inside loadI18n(), not via
    :lang="..." in index.html."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    page = (await client.get("/")).text
    load_i18n_start = script.index("async loadI18n()")
    load_i18n_end = script.index("},", load_i18n_start)
    body = script[load_i18n_start:load_i18n_end]
    assert "document.documentElement.lang = " in body
    assert ':lang="' not in page


async def test_load_i18n_catches_a_failed_request_so_init_still_proceeds(api):
    """Regression test: unlike its sibling `loadAuthInfo()` (which carries
    both a `catch` AND a `finally`), `loadI18n()` previously had ONLY a
    `finally` - a failure of `GET /api/i18n` (a network hiccup, a 5xx)
    would therefore run as an unhandled rejection through `init()`'s
    `await Promise.all([this.loadI18n(), this.loadAuthInfo()])`, and
    `init()` itself wraps this line in no `try`/`catch` of its own - with
    the consequence that `if (this.authenticated) { await this.startApp(); }`
    would then NEVER run, even if `loadAuthInfo()` on its own succeeded and
    the person is logged in. Visible effect: a transient failure of
    `/api/i18n` with an already-valid session meant the app never loaded
    devices/data, without any visible error message.

    Proves only that the delivered source contains a `catch` block within
    the body of `loadI18n()` that logs the error - not that a real network
    error at runtime in the browser actually lands there and `startApp()`
    still runs afterwards (that would need a browser engine, see
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
    """Both are independent, unauthenticated calls that gate the same
    auth-screen templates - init() must start them in parallel, not one
    after the other."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    # Up to the next method instead of a fixed character count: `init()`
    # has had a few more lines since the URL navigation, and an 800-
    # character window ended in the middle of the comment before it.
    init_start = script.index("async init()")
    body = script[init_start : script.index("async request(method, path, body)")]
    assert "Promise.all([this.loadI18n(), this.loadAuthInfo()])" in body


async def test_the_three_main_screens_also_wait_for_translations(api):
    """Following the same pattern as authReady (prevents the wrong screen
    from flashing up until /auth-info has responded): stringsReady
    additionally gates all three main areas (initial setup, login, app),
    so that none of them flashes up with untranslated {key} text before
    GET /api/i18n has responded."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert 'x-if="stringsReady && authReady && !authenticated && !passwordSet"' in page
    assert 'x-if="stringsReady && authReady && !authenticated && passwordSet"' in page
    assert 'x-if="stringsReady && authenticated"' in page


async def test_get_i18n_returns_a_real_web_namespace_key(api):
    """The translation mechanism needs at least one real `web.*` key to
    check GET /api/i18n end-to-end without depending on the table from
    Task 9, which has not been written yet - see strings.yaml,
    web.test.smoke (a template deliberately named test-only, analogous to
    test.* from Phase A).

    Deliberately WITHOUT a {placeholder} in this key (see the comment on
    web.test.smoke in strings.yaml as well as the Task 8 report): the
    original plan called for "smoke test {value}", but
    `api/language.py:_web_strings()` calls `i18n.t(key)` for every
    `web.*` key WITHOUT values - a placeholder there raises `KeyError` and
    takes the ENTIRE response down with it (confirmed against four already
    merged tests in tests/api/test_language.py, which suddenly failed as a
    result). The actual bug lives in files outside the scope of this task
    and is not fixed here."""
    client, _, _ = api
    response = await client.get("/api/i18n")
    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "en"
    assert body["strings"]["web.test.smoke"] == "smoke test"


# ---------------------------------------------------------------------------
# Task 10 - first substantive WebUI translation: tab bar, header, connection
# status, formatting/error helpers, access screens. The translation table
# itself (Task 9) and the translation engine (Task 8) are already checked
# building blocks - here it is only proven that the delivered files actually
# bind to these building blocks instead of continuing to carry the German
# literals. As with the other markup tests in this file: without a browser
# engine it cannot be checked that Alpine correctly resolves `t(...)` at
# runtime - only that the source carries the right binding for it.
# ---------------------------------------------------------------------------


async def test_the_generic_network_errors_call_the_global_t_from_a_free_function(api):
    """Task 10, step 6: the proof that `t()` also works outside `app()` -
    `requestJson`/`requestDownload` have no access to `this` of the Alpine
    component. All three carried the same German literal (see inventory
    §13); all three must now carry the same `t("web.errors.bridge_unreachable")`
    call, none of them fixed in the text anymore. `requestUpload` (project
    file sync feature, developed independently of this i18n phase on main)
    still carried the same literal fixed in the text when the two branches
    were merged - switched to the same `t(...)` call while resolving the
    conflict, so that no third, untranslated occurrence is left here."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "Die Brücke ist nicht erreichbar" not in script
    assert script.count('t("web.errors.bridge_unreachable")') == 3
    assert 'return t("web.errors.http_status", { status: response.status });' in script
    assert "`HTTP ${response.status}`" not in script


async def test_the_nav_tabs_bind_to_translation_keys_without_altering_their_links(api):
    """Task 10, step 3: `x-text` replaces the text node of every tab,
    `:class` stays untouched - a wrongly bound tab would be immediately
    visible in the browser, but this test without a browser engine can
    only check that the address and the bindings sit SIDE BY SIDE on the
    same element, not that a click actually switches the view.

    Since the URL navigation, every tab is an `<a href="#/...">` instead
    of a button with `@click` (see the three following tests)."""
    client, _, _ = api
    page = (await client.get("/")).text
    for view_key in ("devices", "export", "system", "settings"):
        assert f'<a href="#/{view_key}" :class="{{ active: view === \'{view_key}\' }}"' in page
        assert f"x-text=\"t('web.nav.{view_key}')\"" in page
    assert "selectView('devices')" not in page


async def test_every_view_has_its_own_url_fragment(api):
    """The reason for this change: whoever reloaded the page on "Settings"
    ended up back at the device dashboard. Every view therefore has its
    own fragment, and the tab bar consists of real links to them.

    Both are checked together: the list in `app.js` (it decides which
    fragment is accepted at all) and the four `href`s in the markup. A tab
    pointing at a fragment `VIEWS` does not know would find the dashboard
    again after a reload - exactly the bug meant to disappear here."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert 'const VIEWS = ["devices", "export", "system", "settings"];' in script
    for view_key in ("devices", "export", "system", "settings"):
        assert f'<a href="#/{view_key}"' in page
        assert f"x-show=\"view === '{view_key}'\"" in page


async def test_the_view_comes_from_the_url_before_anything_is_loaded(api):
    """`init()` reads the fragment BEFORE `startApp()` runs - otherwise the
    page would first build the dashboard and only then the requested view:
    two load passes and a visible flash of the wrong view.

    The `hashchange` listener attaches to the window exactly once. That is
    not a formality in this file: duplicate listeners and duplicate live
    connections from a second `init()` have already happened twice here
    (see `test_the_page_does_not_call_init_a_second_time`)."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    init_body = script[
        script.index("async init() {") : script.index("async request(method, path, body)")
    ]
    assert "this.view = viewFromHash() ?? DEFAULT_VIEW;" in init_body
    assert init_body.index("this.view = viewFromHash()") < init_body.index("this.startApp()")
    assert script.count('addEventListener("hashchange"') == 1


async def test_the_fragment_and_the_shown_view_cannot_drift_apart(api):
    """Both directions are wired up: `selectView` writes the view into the
    address bar (`writeHash`), `applyHash` reads it back and switches over.
    Without the return path, tab clicks, the back button, and bookmarks
    would be ineffective; without the forward path, the address bar would
    still show the previous view after a programmatic switch (e.g. via the
    hint link "First set it up in Settings ...").

    The two early-exit conditions in `applyHash` are not incidental: the
    `hashchange` that `selectView` triggers itself via `writeHash` ends up
    back here - without the `view === this.view` check, every programmatic
    switch would run twice. And nothing at all may load before login,
    otherwise a manually changed address on the login screen would run
    into a 401."""
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
    """A bookmark to the dissolved signals view (`#/signals`, see
    `test_the_signals_view_is_gone_from_navigation_and_markup`) or a typo
    in the address bar: `viewFromHash` then returns `null`, and both
    callers fall back - `init()` to `DEFAULT_VIEW`, `applyHash` to the
    currently shown view, which it also writes back at the same time.
    Without this fallback, every `x-show="view === ..."` would be false: a
    page with a header, tabs, and nothing else."""
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
    """When loaded without a fragment, `writeHash` back-fills `#/devices`
    via `replaceState` instead of via `location.hash`: a dedicated history
    entry would lead the back button to the same page without a fragment,
    which would immediately add the fragment back - a button that visibly
    does nothing. Only a switch BETWEEN two valid views gets an entry, so
    that the back button shows the previous tab."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    write_body = script[
        script.index("function writeHash(view) {") : script.index("function app() {")
    ]
    assert "if (viewFromHash() === null) {" in write_body
    assert 'window.history.replaceState(null, "", target);' in write_body
    assert "window.location.hash = target;" in write_body


async def test_the_tab_styling_covers_links_and_buttons(api):
    """`nav.tabs` carries two things: the tab bar up top, made of
    `<a href="#/...">` since the URL navigation, and the language toggle
    in settings, which borrows the same bar for two buttons (see
    `test_the_settings_tab_has_a_language_toggle`). Both selectors must
    therefore remain - a rework down to just `nav.tabs a` left the
    language buttons without padding, without dimmed text, and without
    the active language's underline."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    assert "nav.tabs a,\nnav.tabs button {" in css
    assert "nav.tabs a.active,\nnav.tabs button.active {" in css
    tab_rule = css[css.index("nav.tabs a,") : css.index("main {")]
    # A link would otherwise inherit the underline of the document's link colour.
    assert "text-decoration: none;" in tab_rule
    # And the tabs used to get these two values as buttons from the
    # general `button` rule (further down in this file) - a link does not
    # bring them along: without them the bar became four pixels taller and
    # the active tab's underline square instead of rounded. Both only
    # showed up in the byte-exact reproducible screenshots (see
    # scripts/capture_screenshots.py), not in the test and not on visual
    # inspection.
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
    """Task 10, step 4: `connectionStatusText()` keeps its three
    conditions unchanged - only the four returned literals become `t(...)`
    calls."""
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
    """Task 10, step 4: the three age literals in `sinceText()` and the
    two tooltip literals in `signalAgeTitle()` now carry placeholders
    instead of template strings - the same variable names as before
    (`seconds`/`minutes`/the rounded hours object/`text`)."""
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
    """Task 10, step 5: heading, warning banner (as `x-html`, because the
    translated text brings the `<strong>` along itself, see strings.yaml
    `web.auth.setup_warning`), both field labels, the hint text, and the
    submit button - none of the former German literals may remain in the
    markup."""
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
    """Task 10, step 5: the submit button and the shared password label
    move to `t(...)`, but BOTH `<h1>loxmatter</h1>` (header and login
    screen) as well as `<title>loxmatter</title>` remain untouched - that
    is the product name, not a text string (Task 9's scope note)."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "x-text=\"t('web.auth.login_submit')\"" in markup
    assert ">Anmelden<" not in markup
    assert markup.count("<h1>loxmatter</h1>") == 2
    assert "<title>loxmatter</title>" in markup


async def test_session_expired_and_password_mismatch_are_translated(api):
    """Task 10, step 5: the three identical "session expired" spots
    (`UnauthorizedError`'s constructor, `handleDiagnosticsDisconnect`,
    `handleLiveDisconnect`) share the same key; the password match check
    during setup gets its own."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "Die Sitzung ist abgelaufen" not in script
    assert script.count('t("web.auth.session_expired")') == 3
    assert 'super(t("web.auth.session_expired"));' in script
    assert "Die beiden Eingaben stimmen nicht überein" not in script
    assert 'this.authError = t("web.auth.password_mismatch");' in script


async def test_formatting_helpers_translate_and_the_locale_follows_the_language(api):
    """Task 10, step 6: `formatTimestamp`/`formatValue` lose their three
    German literals AND their hard-wired `"de-DE"` - the `toLocaleString`
    locale must follow the active `language` field, no longer bypassing it
    to stay in German while the rest of the page shows English."""
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
    """Task 11, step 3: heading, both placeholders, the submit button, and
    the hint text of the commissioning card now carry `t(...)`, none of
    the former German literals remain in the markup."""
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
    # The second hint paragraph was added along with the commissioning
    # branch (the bridge fetches the thread dataset itself from the border
    # router) and has since run through the same table as the first.
    assert "x-text=\"t('web.devices.thread_dataset_hint')\"" in markup
    assert "holt ihn beim" not in markup
    assert "x-text=\"t('web.devices.empty')\"" in markup
    assert "Noch kein Gerät eingelernt." not in markup


async def test_the_commissioning_card_leads_with_a_labelled_code_field(api):
    """Design "code first" (2026-09-07): the pairing code is this card's
    only required field and gets its own row, a visible label, and the
    submit button within the same border.

    Previously, four equally wide fields sat here in a `.row` - the code
    had the same weight as the thread dataset, which almost nobody ever
    fills in - and the labels lived only in the `placeholder`, which
    disappears at the first keystroke and, to a screen reader, is not a
    label but an example."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    label = _label_around(markup, "web.devices.code_label")
    assert 'for="commission-code"' in label

    code_start = markup.index('<div class="code-field">')
    code_field = markup[code_start : markup.index("</div>", code_start)]
    assert 'id="commission-code"' in code_field
    assert 'x-model="commissionCode"' in code_field
    # Enter in the field does the same as the button next to it - both sit
    # inside the same border, so they must also trigger the same thing.
    assert '@keydown.enter="commissionDevice()"' in code_field
    assert '@click="commissionDevice()"' in code_field
    assert "x-text=\"t('web.devices.commission_submit')\"" in code_field

    # The four fields in a row are gone.
    assert '<div class="row">\n            <input\n              type="text"' not in markup


async def test_the_pairing_code_field_formats_normalizes_and_labels_itself(api):
    """Entwurf "Pairing-Code: schreiben, wie er auf dem Geraet steht"
    (2026-09-07): das Feld schreibt die Bindestriche beim Tippen mit,
    benennt rechts im Feld, was es erkannt hat, und die Karte schickt den
    NORMALISIERTEN statt den bloss getrimmten Wert ab.

    Fuer den gesamten Umbau gab es zuvor genau EINE Assertion in dieser
    Datei (auf `commissionRunCode`, siehe
    `test_commission_device_drives_the_flow_and_stops_where_it_failed`).
    Nichts sicherte, dass `@input` noch am Feld haengt, dass es den Chip
    gibt, oder dass `commissionDevice` wirklich den normalisierten statt
    des getrimmten Werts verschickt - wer das beim Umsortieren verliert,
    saehe sonst weiterhin lauter gruene Tests."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    code_start = markup.index('<div class="code-field">')
    code_field = markup[code_start : markup.index("</div>", code_start)]
    assert '@input="formatCommissionCode($event.target)"' in code_field
    assert '@keydown="commissionCodeKeydown($event)"' in code_field
    assert 'class="code-detect"' in code_field
    assert ":class=\"'tone-' + commissionCodeBadge().tone\"" in code_field
    assert 'x-text="commissionCodeBadge().text"' in code_field

    # Die Beispielzeile mit den beiden Bauformen, die den Klammerzusatz im
    # frueheren Platzhalter ersetzt.
    example_start = markup.index('<p class="code-examples"')
    example_block = markup[example_start : markup.index("</p>", example_start)]
    assert "x-text=\"t('web.devices.code_example_manual')\"" in example_block
    assert "x-text=\"t('web.devices.code_example_qr')\"" in example_block

    script = (await client.get("/static/app.js")).text
    # `commissionDevice` verschickt den NORMALISIERTEN Wert, nicht mehr
    # `this.commissionCode.trim()` - die Trenner, die das Feld beim Tippen
    # selbst gesetzt hat, gehoeren nicht in den Matter-Stack.
    assert "const code = normalizePairingCode(this.commissionCode);" in script
    assert "const body = { code };" in script

    # Die vier reinen Funktionen, auf Modulebene, ohne geladene
    # Sprachtabelle prueffaehig.
    assert "function isPairingQrCode(" in script
    assert "function formatPairingCode(" in script
    assert "function normalizePairingCode(" in script
    assert "function describePairingCode(" in script


async def test_the_two_long_commissioning_hints_moved_into_disclosures(api):
    """No sentence of the former three hint paragraphs has been lost - the
    two long ones now each sit in a `<details>` disclosure next to the
    field they concern, instead of permanently as 78 words of running
    text below the card.

    Native, not via Alpine state: an open/closed state that nobody needs
    to reset also cannot drift out of sync with anything else - the same
    reasoning as with the tile menu and the signal groups."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    help_start = markup.index('<div class="commission-help">')
    help_block = markup[help_start : markup.index('x-show="commissionStep !== null"')]
    assert help_block.count('<details class="commission-disclosure">') == 2
    assert help_block.count("</details>") == 2

    # Disclosure 1: the multi-admin hint, behind a question the reader
    # recognises in themselves - not behind the name of an input field.
    assert "x-text=\"t('web.devices.code_help_summary')\"" in help_block
    assert "x-text=\"t('web.devices.commission_hint')\"" in help_block

    # Disclosure 2: the thread field along with its hint. The label is now
    # a real `<label>`; the placeholder must not replace it.
    assert "x-text=\"t('web.devices.thread_summary')\"" in help_block
    assert 'x-model="commissionThreadDataset"' in help_block
    assert "x-text=\"t('web.devices.thread_dataset_hint')\"" in help_block
    thread_label = _label_around(help_block, "web.devices.thread_dataset_label")
    assert 'for="commission-thread"' in thread_label

    # And none of the three paragraphs stands freely in the card anymore.
    assert markup.count("x-text=\"t('web.devices.commission_hint')\"") == 1
    assert markup.count("x-text=\"t('web.devices.thread_dataset_hint')\"") == 1


async def test_the_commissioning_message_banner_survived_the_redesign(api):
    """Regression from the rebuild itself: when the old `.row` was pulled
    out, the message paragraph came out with it. The state was still
    correct - an empty code entry set `commissionMessage` - only nothing
    displayed it anymore, and none of the then-1219 tests noticed.

    It deliberately sits OUTSIDE both halves: the check for an empty code
    fires before a run begins (i.e. at the form), success and failure
    report afterwards (i.e. at the progress display). Two copies would be
    two places where the message could get stuck - hence exactly one."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    assert markup.count('x-show="commissionMessage"') == 1
    banner_start = markup.index('x-show="commissionMessage"')
    banner = markup[markup.rindex("<p", 0, banner_start) : markup.index("</p>", banner_start)]
    assert ":class=\"commissionMessageIsError ? 'banner danger' : 'banner ok'\"" in banner
    assert 'x-text="commissionMessage"' in banner

    # Neither in the form branch nor in the progress branch, but behind
    # both: otherwise a switch between the halves would make the message
    # disappear.
    form_start = markup.index('<div x-show="commissionStep === null">')
    flow_start = markup.index('<div x-show="commissionStep !== null"')
    assert form_start < flow_start < banner_start


async def test_the_commissioning_flow_shows_the_two_phases_it_actually_knows(api):
    """Design "the process becomes visible": during a run, a progress
    display replaces the form. Commissioning takes twenty to sixty
    seconds and used to be a button turning grey - indistinguishable from
    a hung page.

    TWO steps, not three: this UI cannot honestly tell apart more
    sections than that. It knows the POST to /api/devices/commission and
    the subsequent reload of signals and commands - nobody reports it an
    intermediate state from the Matter stack ("device found"). A third
    point would look nicer and would be a guess; this test keeps the
    display pinned to what is actually known."""
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

    # The code of the running attempt sits above the steps - after a
    # success the input field is cleared, otherwise the display would sit
    # there without the code it was about.
    assert 'x-text="commissionRunCode"' in flow

    # One way back to the form, with two labels for the two meanings:
    # after a success the next device, after a failure the same attempt
    # once more.
    assert '@click="resetCommission()"' in flow
    assert 'x-text="commissionFailed ?' in flow
    assert "t('web.devices.commission_retry')" in flow
    assert "t('web.devices.commission_again')" in flow


async def test_commission_device_drives_the_flow_and_stops_where_it_failed(api):
    """The step display hangs off `commissionDevice` itself, not off a
    timer: step 0 from the POST, step 1 from the reload, step 2 at the
    end.

    On failure, the counter is explicitly NOT reset - it keeps pointing
    at the step it got stuck on, and `commissionStepClass` colours
    exactly that one red. A reset here would take away the display's only
    piece of information: how far it got."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    commission_start = script.index("async commissionDevice() {")
    commission_end = script.index("\n    },", script.index("this.commissionBusy = false;"))
    body = script[commission_start:commission_end]

    assert "this.commissionStep = 0;" in body
    assert "this.commissionFailed = false;" in body
    assert "this.commissionRunCode = formatPairingCode(this.commissionCode.trim());" in body
    # Schritt 1 steht VOR dem Nachladen, Schritt 2 dahinter.
    load = body.index(
        "await Promise.all([this.loadControls(device.id), this.loadSignals(device.id)]);"
    )
    assert body.index("this.commissionStep = 1;") < load
    assert load < body.index("this.commissionStep = 2;")
    # The error branch marks it, but does not reset it.
    assert "this.commissionFailed = true;" in body
    assert "this.commissionStep = null;" not in body


async def test_the_step_class_is_derived_and_reset_returns_to_the_form(api):
    """`commissionStepClass` is a pure expression on
    `commissionStep`/`commissionFailed`, no third state variable with
    class names in it: two fields telling the same story drift apart
    sooner or later - and the display is the place where nobody would
    notice, because it does show something.

    `resetCommission` clears both plus the message (it belongs to the run
    being left) and resets focus back into the code field - only on the
    next tick, because `x-show` still holds the form at `display: none`
    until then and a `focus()` on it would silently do nothing."""
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
    """The first attempt was a row: the room field on the left, the two
    disclosures pushed to the right end via a spacer. In the browser, an
    opened `<details>` - a tall flex element - tore this row apart: the
    opened disclosure slid downward, the second one stayed stuck centred
    next to its explanatory text.

    They therefore sit stacked in their own container. Opening and closing
    one must not shift what stands around it, least of all the control
    that was just clicked."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text

    block_start = css.index(".commission-help {")
    block = css[block_start : css.index("}", block_start)]
    assert "flex-direction: column;" in block
    assert "align-items: flex-start;" in block

    markup = _without_comments((await client.get("/")).text)
    # The spacer from the first attempt is gone entirely - otherwise a
    # rule without an element, or an element without a rule, would remain.
    assert "meta-spacer" not in markup
    assert "meta-spacer" not in css


async def test_the_device_card_static_text_is_translated(api):
    """Task 11, step 3: status pills, remove button, the two values/
    controls section headings with their loading hints and empty states,
    the value placeholder, and the send button of the device card - each
    as a plain `x-text`, because none of these keys carries embedded HTML.

    Task 8 (grid rebuild, 2026-09-05) had turned export and remove into
    icon buttons with `:title` (the space on a 260 px wide tile is not
    enough for spelled-out labels); Task 2 (kebab menu, 2026-09-05) moved
    both from the footer into the menu, where there is enough room for
    spelled-out text - `remove`/`export` have since gone back to being
    `x-text`, no longer `:title`. The two values/controls section
    headings remain removed without replacement from Task 8 - the tile
    now shows only a single values grid with no title of its own anyway;
    `values_heading`/`controls_heading` have been removed from
    `strings.yaml` since Task 9.

    `no_functional_signals`, `controls_loading`, and `no_known_commands`,
    on the other hand, were also (prematurely) removed by Task 9 at first
    and have been back since Finding 1 of the review on 2026-09-05:
    without them, a device with empty functional signals, or a still-
    loading/failed command fetch, could not be distinguished from a
    genuinely empty set - exactly the kind of silently wrong state Spec
    8.1 aims to rule out (see
    `test_the_command_bar_distinguishes_loading_from_genuinely_empty` for
    the detailed proof of this finding)."""
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
    """Task 11, step 3: the two "N more..." hints consist of a dynamic
    counter (`remainingSignalCount`/`hiddenRawCommandsFor`) followed by
    static text - only the static part moves to `t(...)`, the counter
    expression stays unchanged.

    Task 8 (grid rebuild, 2026-09-05) turned the dedicated `<span>` per
    counter expression into a composed `x-text` on a single element (the
    hint about the remaining signals is now the last row of the values
    grid instead of its own paragraph, see `.value-row` in `index.html`)
    and introduced the short forms `more_signals_short`/
    `more_commands_short` - the old keys `more_in_signals_view`/
    `more_commands_unnamed` remain unused in `strings.yaml` (Task 9
    cleans them up)."""
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
    """Task 11, step 3 (the new pattern for this task): the hint "First
    set it up in Settings -> ..." contains a real link to settings, which
    must NOT disappear into an `x-html` block when translated - otherwise
    only text would remain of the link. Three separate elements (prefix,
    link, suffix), each with its own `x-text`, keep it intact. Since the
    URL navigation it carries the same address as the tab (`#/settings`)
    instead of an `href="#"` with an intercepted click.

    The excerpt ends at the NEXT view, not at a closing tag:
    `"view === 'signals'"` was this anchor, until the tab was dissolved
    (2026-09-05) - now it is `'export'`. An anchor on `</div>` or
    `</section>` would be unsuitable here, there are dozens of those in
    the device view."""
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
    """Task 11, step 4: the load/save/remove errors, the export hints
    (`exportHintFor`), and the two command toasts of the device list now
    carry `t(...)` with the same placeholders that the template strings
    used before."""
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
    """Task 11, step 4: the bridge IP check in `exportDevice` (device
    card) shares the key `web.export.bridge_ip_missing` with the
    preview/download (Task 13) - here only the copy inside `exportDevice`
    itself is checked."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    export_start = script.index("async exportDevice(device) {")
    export_end = script.index("\n    },", export_start)
    body = script[export_start:export_end]
    assert 'this.deviceActionError = t("web.export.bridge_ip_missing");' in body
    assert "Brücken-IP hinterlegen" not in body


async def test_the_commissioning_flow_messages_are_translated(api):
    """Task 11, step 4: the empty-code check, the success message (a
    single `t(...)` call instead of four composed literals), and the
    failure message of `commissionDevice`."""
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
    # Since merging with the commissioning branch, the failure has been
    # distinguished by HTTP status, no longer by text: a 422 from this
    # route carries the message already framed by the server (api.errors.
    # commissioning_failed), everything else gets the framing here. A
    # comparison against the start of the message text would be exactly
    # the place where the translation would drift apart again - it only
    # ever knows one of the two languages.
    assert "error.status === 422" in body
    assert 't("web.devices.commission_failed", { message })' in body
    assert "startsWith" not in body
    assert "Einlernen fehlgeschlagen" not in body


async def test_commission_device_avoids_duplicate_tiles(api):
    """Finding 4 (re-review 2026-09-05): the commissioning route returns
    the same `device_id` for a device that is already registered (see the
    backend test
    `test_recommissioning_a_known_device_applies_the_chosen_room` in
    `tests/api/test_devices.py`). An unconditional `push` dropped this
    device into `this.devices` a second time: two tiles with the same
    `device.id`, which violates `x-for`'s `:key="device.id"` and made the
    room chip count it twice.

    Without a browser engine there is no way to check either a duplicate
    Alpine key warning in the console, or the race against a concurrently
    running `saveRoom`/`saveLabel` (which remembers an object reference
    before its own `await`) - what is proven instead is the delivered
    method body: it looks for an already-present device with the same ID
    via `findIndex`, and on a match fills the existing object via
    `Object.assign` instead of replacing it in the array (otherwise a
    `saveRoom`/`saveLabel` resolving later would write into an instance
    decoupled from the array), and otherwise appends a new one."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    commission_start = script.index("async commissionDevice() {")
    commission_end = script.index("\n    },", script.index("this.commissionBusy = false;"))
    body = script[commission_start:commission_end]

    assert "const existingIndex = this.devices.findIndex((d) => d.id === device.id);" in body
    assert "this.devices.push(device);" in body
    assert "Object.assign(this.devices[existingIndex], device);" in body


async def test_the_remove_confirm_dialog_text_comes_from_t(api):
    """Task 11, step 4: the native `window.confirm(...)` in `removeDevice`
    now carries a single `t(...)` call with `label` and `id` instead of
    the hand-built template string - the dialog itself cannot be checked
    without a browser engine, but that its text now comes from the
    translation table can be."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert (
        'window.confirm(t("web.devices.remove_confirm", { label: device.label, id: device.id }))'
        in script
    )
    assert "wirklich entfernen? Das kann nicht rückgängig gemacht werden" not in script
    assert "In Loxone bleiben danach verwaist" not in script


async def test_remove_device_reconciles_the_room_filter(api):
    """Finding 1 (re-review 2026-09-05): `removeDevice` mutated
    `this.devices` directly and called neither `reconcileRoomFilter()`
    nor a reload. Filter by a room and delete its last device, and
    `roomFilter` stays pointed at the vanished name: no tile visible
    anymore, no chip active anymore - and if it was the last room at all,
    even the entire chip bar disappears (`hasAnyRoom()` then false, see
    index.html), taking with it the "All" chip that would offer a way
    out. Simply reloading the page was the only way out.

    Without a browser engine there is no way to check either `roomFilter`
    or the rendered markup after a click (see the other tests in this
    file that admit the same limitation). Proven instead is that the
    delivered method body of `removeDevice` itself calls
    `reconcileRoomFilter()` after removing from `this.devices`."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    remove_start = script.index("async removeDevice(device) {")
    remove_end = script.index("\n    },", remove_start)
    remove_body = script[remove_start:remove_end]

    filter_index = remove_body.index("(d) => d.id !== device.id)")
    reconcile_index = remove_body.index("this.reconcileRoomFilter();")
    assert filter_index < reconcile_index, remove_body


async def test_the_signal_modal_static_text_is_translated(api):
    """Formerly `test_the_signal_view_static_text_is_translated`: the same
    assertions, now against the modal instead of the dissolved tab
    (2026-09-05).

    Two of them have been dropped without replacement: `show_expert` (the
    global toggle gives way to a `<details>` per group) and
    `expert_collapsed_hint` (whose text referred to exactly that toggle).
    The remaining hints, labels, and placeholders still carry `t(...)`
    unchanged - none of the former German literals remain in the
    markup."""
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
    assert ":aria-label=\"t('web.signals.export_checkbox')\"" in dialog
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
    """Task 12, step 4: the load error, the title-save error, the export-
    flag error, and the raw-value success message now carry `t(...)`. The
    error message of the raw-value write (`app.js`, `writeRaw` catch
    branch) deliberately remains untouched - it passes through the
    `detail` text already translated by the backend unchanged (Task 9's
    scope note); there is no `web.*` key for it."""
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
    # Deliberately untranslated: passes through the backend error text unchanged.
    assert (
        "this.rawWriteMessages[signal.key] = { text: error.message, isError: true };"
        in write_raw_body
    )


async def test_the_export_tab_static_text_is_translated(api):
    """Task 13, step 3: the heading, the IP/port labels, the managed-in-
    settings hint (the same prefix/link/suffix pattern as Task 11's
    bridge-IP hint, here with `web.export.settings_hint_*` and the shared
    `web.settings.miniserver_link`), the two checkbox labels, the filter
    explanation (via `x-html`, it contains an embedded `<strong>`), the
    two buttons, the preview heading, all eight column headers, the
    expert-withheld explanation, and the system-templates prefix now
    carry `t(...)` instead of fixed German literals - none of the former
    literals remain in the markup. The dynamic `x-text` that appends
    `exportPreview.system_files` remains unchanged next to the translated
    prefix."""
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
    # The link itself remains unchanged (the same address as the tab, now
    # translated with the shared key from Task 11).
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
    """Task 13, step 3 (follow-up fix): `web.export.filter_explanation`
    contains an embedded `<strong>and</strong>` - the binding must be
    `x-html`, otherwise the browser shows the angle brackets as text
    instead of rendering bold. `GET /api/i18n` delivers the raw templates
    (via `i18n.raw_template`, see `api/language.py`), which `app.js`'s
    `t()` inserts into this HTML block - proven here only is that the raw
    `<strong>` arrives in it; the actual bold rendering is part of the
    manual browser check."""
    client, _, _ = api
    body = (await client.get("/api/i18n")).json()
    template = body["strings"]["web.export.filter_explanation"]
    assert "<strong>" in template and "</strong>" in template


async def test_the_export_tab_dynamic_errors_are_translated(api):
    """Task 13, step 4: the status load error, the remaining two of three
    copies of the bridge IP check (`previewExport`, `downloadExport` - the
    third one in `exportDevice` belongs to Task 11 and is already
    translated, see
    `test_the_device_card_export_bridge_ip_validation_is_translated`),
    the preview error, and the download error now carry `t(...)`."""
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
    """Task 14, step 3: the two card headings "Systemcheck" and
    "Live-Diagnose" together with the "Aktualisieren" button, the three
    ternary literals (system check status OK/error, connection status
    live/disconnected - the latter uses the key `web.connection.live`
    already known from Task 10 for the true branch -, pause/resume), the
    noise-filter label, the log-level label along with all four
    `<option>`s (the "Fehler" entry shares `web.system.check_error` with
    the system check status), the "Leeren" button, the pause/clear
    explanation (as `x-html`, it contains an embedded
    `<span class="key">tail -f</span>`), the headings and hint texts of
    the logs, UDP, and command-log cards, as well as the backup card
    (heading, both explanation paragraphs, the download button) now carry
    `t(...)` instead of fixed German literals - none of the former
    literals remain in the markup."""
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
    """Task 14, step 3 (follow-up fix): `web.system.pause_clear_explanation`
    contains an embedded `<span class="key">tail -f</span>` - the binding
    must be `x-html`, otherwise the browser shows the angle brackets as
    text instead of applying the keyboard styling. Proven here only is
    that the raw markup arrives in the delivered language string; the
    actual rendering is part of the manual browser check."""
    client, _, _ = api
    body = (await client.get("/api/i18n")).json()
    template = body["strings"]["web.system.pause_clear_explanation"]
    assert '<span class="key">tail -f</span>' in template


async def test_the_system_tab_dynamic_errors_are_translated(api):
    """Task 14, step 4: the system-check load error (`loadSystem`) and the
    backup error (`downloadFabricBackup`) now carry `t(...)` instead of
    template strings."""
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
    """Task 15, step 3: the connection card (heading, explanation as
    `x-html` - it contains an embedded "<strong>Nicht</strong>" and a
    `<span class="key">` with the URL example -, the shared
    `web.bridge_ip_label`, the placeholder, the port labels, the save
    button, and the last-saved/not-yet-saved hint) now carry `t(...)`
    instead of fixed German literals. The "Verbindung zum Miniserver"
    heading is the clearest proof that this card was translated at all -
    see steps 1/2.

    Does NOT check the global absence of the text across the whole page
    anymore: the project file sync feature, developed independently on
    main (see its own card in the export tab), links to this settings
    card with the exact same raw German phrase
    "Einstellungen → Verbindung zum Miniserver" - a separate, untranslated
    outside this task, not proof that the heading itself is untranslated
    here. The precise `>...<` form below matches exclusively the heading
    as its own text node."""
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
    """Task 15, step 3 (follow-up fix, analogous to Task 13/14):
    `web.settings.connection_explanation` contains both an embedded
    "<strong>Nicht</strong>" and a `<span class="key">` that encloses the
    URL example - the binding must therefore be `x-html`, otherwise the
    browser shows the angle brackets as text. `GET /api/i18n` delivers the
    raw template; the actual rendering is part of the manual browser
    check."""
    client, _, _ = api
    body = (await client.get("/api/i18n")).json()
    template = body["strings"]["web.settings.connection_explanation"]
    assert "<strong>" in template and "</strong>" in template
    assert '<span class="key">' in template


async def test_the_settings_tab_dynamic_errors_are_translated(api):
    """Task 15, step 6: the load and save errors as well as the required-
    field message and the success toast of `loadSettings`/`saveSettings`
    now carry `t(...)` instead of fixed German literals or template
    strings."""
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
    """Task 15, step 4: the former placeholder card
    "Weitere Einstellungen" is replaced (not translated, see its comment in
    Task 9) by two buttons that mark the current language via the
    already-existing reactive `language` property (Task 8) and call
    `setLanguage(...)` on click (step 5). Reuses the already-existing
    `nav.tabs`/`button.active` class (tab bar up top) instead of a newly
    invented CSS class - see `style.css`, there is otherwise no example
    of a button row with an active state."""
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
    """Task 15, steps 1/2 and 5: `app.js` supplies `setLanguage`, which
    calls `PATCH /api/language` (Task 1) and then reloads the page - see
    the task description for the deliberately simple variant with no
    special case for already-shown toasts/WebSocket states."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    assert "async setLanguage(language) {" in script

    start = script.index("async setLanguage(language) {")
    end = script.index("\n    },", start)
    body = script[start:end]
    assert 'await this.request("PATCH", "/api/language", { language });' in body
    assert "window.location.reload();" in body


async def test_set_language_handles_request_failures_like_save_settings(api):
    """Review fix Important (whole-branch review, 2026-09-04): every other
    action in app.js (`saveSettings`, `exportDevice`, `commissionDevice`,
    `sendCommand`, ...) wraps `this.request(...)` in try/catch and shows
    an error via an existing error field - `setLanguage` was previously
    the only exception: a failure (e.g. 400/502, `this.request` re-throws
    except for 401) became an unhandled promise rejection with no
    feedback whatsoever. This test records that `setLanguage` now uses
    the same try/catch/finally pattern as `saveSettings` (including the
    settingsBusy guard)."""
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
    """Task 16 (project file sync card): the heading, the intro text, the
    bridge IP hint (prefix/link/suffix - the same structure as Task
    11/13, here with `web.export.projectsync_bridge_ip_hint_*` and the
    shared `web.settings.miniserver_link`), the file field label, the
    processing hint, the miniserver selection (label, placeholder option,
    multiple-miniservers hint - a user request after the review: has, since
    merging with `main`, completely replaced the former IP text field, see
    `web.export.projectsync_miniserver_select_*`/`_multiple_miniservers_
    hint`), the "all up to date" message, the five overall tally labels,
    the differing "all up to date" short form per device card, the
    simplified disclosure (a single `t(...)` call with `{count}` instead
    of two elements), the checkbox label, and the download button now
    carry `t(...)` instead of fixed German literals - none of the former
    literals remain in the markup."""
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
    """Task 16 (project file sync card): the bridge IP error in
    `uploadProjectFile` (shares `web.export.bridge_ip_missing` with Task
    11/13), the upload-failed error, the seven-entry
    `projectSyncStatusLabel` table, the orphaned-device fallback label in
    `projectSyncGroupedEntries`, the two input/output section labels, the
    five `projectSyncEntryNote` return values, and the six-entry
    `projectSyncAttrLabel` table now carry `t(...)` instead of fixed
    German literals."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    upload_start = script.index("async uploadProjectFile(event) {")
    upload_end = script.index("\n    },", script.index('input.value = "";', upload_start))
    upload_body = script[upload_start:upload_end]
    assert 'this.projectSync.error = t("web.export.bridge_ip_missing");' in upload_body
    assert "die Brücken-IP hinterlegen" not in upload_body

    # `_syncProjectFile` has, since merging with `main` (a miniserver
    # selection field instead of an IP text field), been the shared core of
    # both `uploadProjectFile` AND `confirmProjectSyncMiniserver` - the
    # upload-failed error has lived there since, no longer in
    # `uploadProjectFile` itself.
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
    """The periodic-resend card (settings tab, last card, right after the
    language toggle) and the corresponding checkbox label in the signal
    list now carry `t(...)` instead of fixed German literals - the save
    button deliberately shares `web.settings.save` with the connection
    card above it (the same meaning, no key of its own)."""
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
    assert ":aria-label=\"t('web.signals.resend_checkbox')\"" in resend_checkbox_label
    assert "periodisch erneut senden" not in resend_checkbox_label


async def test_the_resend_card_dynamic_strings_are_translated(api):
    """`toggleResend` (signal list), as well as `loadResendInterval` and
    `saveResendInterval` (settings tab), now carry `t(...)` instead of
    fixed German literals for their error messages, the validation, and
    the success toast."""
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
    """The UI is not checked by a JS test runner (there is none - Alpine
    runs vendored in the browser). This check therefore only records THAT
    the building blocks the markup in index.html relies on are delivered
    - a rename on one side without the other shows up here."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    for name in (
        "roomKeyOf(",
        "roomChips(",
        "hasAnyRoom(",
        "visibleDevices(",
        "deviceGroups(",
        "categoryLabel(",
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
    """The search runs over the device list already loaded anyway - there
    is no endpoint for it, and none should be created either."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "/api/devices/search" not in script
    assert "/api/rooms/rename" in script


async def test_the_page_offers_the_room_bar(api):
    client, _, _ = api
    page = (await client.get("/")).text
    assert "roomChips()" in page
    assert "deviceGroups()" in page
    assert "firstSignalsFor(" in page
    assert "deviceSearch" in page


async def test_every_category_has_an_icon_symbol(api):
    """Eight categories, eight symbols - "other" included. A missing
    symbol does NOT show up in the browser: a `<use>` pointing at an
    unknown ID silently draws nothing, no error message. That is why it
    shows up here instead."""
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
    """Finding 6 (review from 2026-09-05): the rename, export, and remove
    buttons showed the raw characters "✎", "↓", and "🗑" instead of one of
    the app's own `<symbol>` icons - Spec 6.5 and the rest of this view
    require inline SVG, no external icon library, and no raw glyphs. "🗑"
    also renders as a coloured emoji on macOS/Windows instead of a
    monochrome symbol, breaking the copper/muted symbol language of the
    other icons.

    Task 2 (kebab menu, 2026-09-05) moved export and remove from the
    footer into the menu, turning them into spelled-out text buttons in
    the process - there is room for a label there instead of an icon.
    Their symbols `#i-export`/`#i-remove` had since been referenced
    nowhere and were removed entirely from the symbol block with the
    final review from 2026-09-05. Only the rename button remains an icon
    button and is still checked here; the kebab symbol itself is covered
    by `test_the_tile_menu_has_its_own_icon_symbol`."""
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
    """The kebab symbol is delivered inline like all the others - no icon
    library, no CDN, because the UI runs offline. A `<use>` pointing at a
    missing ID silently draws nothing - the risky direction is therefore
    not the symbol definition but its usage: a `<use href="#i-kebab">`
    with no matching `<symbol id="i-kebab">` would have stayed just as
    silently empty here as the reverse. Both sides are therefore
    checked."""
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
    """Follow-up fix 2026-09-05, Finding 2: `align-items: start` left
    every tile standing at its own content height - measured in the
    browser, two tiles of the same row came out at 277px and 271px (see
    the task report). `align-items: stretch` alone is not enough: it only
    stretches the card, and the extra free space then remained as empty
    space BELOW the footer, while the shorter neighbouring tile draws its
    footer right below its last command (measured: two footers of the
    same row offset by 14px). The card must therefore itself become a
    flex column whose footer absorbs the free space ahead of it via
    `margin-top: auto`. Without a browser engine this suite cannot
    recompute the row heights itself - what is proven is only that
    `.device-grid` has `align-items: stretch` and that the card brings
    both: `display: flex; flex-direction: column` as well as a
    `.device-foot` with `margin-top: auto`."""
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
    """Finding 2 (review from 2026-09-05): `display: flex` on
    `.device-card` (see above) has a second, unintended side effect - flex
    items no longer collapse their margins with each other. `.value-rows`
    (`margin-bottom: 0.5rem`, 8px) and the two conditionally visible
    `<p class="hint">` paragraphs right after it (UA default `margin-block:
    1em`, which at their `font-size: 0.82rem` is 13.12px) previously
    collapsed, measured in the browser, to `max(8px, 13.12px)` = 13.12px,
    and have since added up to 21.12px (see the task report) - a tile in
    the loading or "no functional signals" state becomes visibly taller as
    a result, purely because it happened to become a flex column. Without
    a browser engine this suite cannot recompute the actual gap - what is
    proven is only that the delivered rule gives the two affected
    paragraphs (not the third, independent Miniserver hint after
    `.device-foot`) a compensating `margin-top` of `0.32rem`
    (13.12px - 8px), instead of leaving them unchanged or setting their
    margin to 0 entirely (which would shrink the gap to 8px, not restore
    it)."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split(".value-rows + p.hint,\n.value-rows + p.hint + p.hint {", 1)[1].split("}", 1)[
        0
    ]
    assert "margin-top: 0.32rem" in rule


async def test_the_device_card_does_not_clip_its_overflowing_menu(api):
    """Finding 1 (review 2026-09-05): the tile menu is positioned
    absolutely relative to `.device-card` and opens upward past its edge.
    As long as `.device-card` carries `overflow: hidden` (as it did
    before), the card clips exactly this part of the menu - measured in a
    real browser (see the task report): `document.elementFromPoint` at the
    top edge of the menu then returns the card instead of the menu. This
    suite has no browser engine and cannot reproduce the clipping itself -
    what is proven is only that the delivered rule carries
    `overflow: visible` and does not fall back to `hidden` again."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split(".device-card {", 1)[1].split("}", 1)[0]
    assert "overflow: visible" in rule
    assert "hidden" not in rule


async def test_the_device_card_stripe_is_rounded_on_its_own(api):
    """The colour stripe (`.device-card::before`) previously owed its
    rounded corners to the card's `overflow: hidden`, which clipped it
    instead of rounding it - the same rule that also clipped the tile
    menu (see `test_the_device_card_does_not_clip_its_overflowing_menu`).
    With `overflow: visible` (see above), the stripe must now carry its
    left-side rounding itself, otherwise its top-left/bottom-left corners
    sit square next to the rounded card."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split(".device-card::before {", 1)[1].split("}", 1)[0]
    assert "border-radius" in rule


async def test_the_tile_menu_outranks_the_sticky_header(api):
    """Finding 1 (follow-up fix 2026-09-05): `.tile-menu-items` opens
    UPWARD past the tile's edge (see above) and can in doing so reach
    beneath the sticky header (`header.app-header`, `z-index: 10`) as soon
    as a tile in the first row opens its menu. Measured in a real browser
    (see the task report): menu top edge at y=48, header bottom edge at
    y=56, `document.elementFromPoint` at the menu's top edge returned the
    header instead of the menu - without a browser engine this suite
    cannot reproduce the overpainting itself, what is proven is only that
    the delivered rule carries a higher `z-index` than the header and does
    not fall back below it again.

    Review finding 4 (2026-09-05, review of the follow-up work): this
    comparison of two numbers is a NECESSARY but not a SUFFICIENT
    condition - a `z-index` only applies within the stacking context of
    its creator, and `opacity`/`filter`/`transform`/`backdrop-filter`/
    `will-change`/`isolation`/`contain: paint` on `.device-card`,
    `.device-foot`, or `.tile-menu` open up such a context. Measured with
    `opacity: 0.75` on `.device-card` (the pre-follow-up-fix state):
    `document.elementFromPoint` at the menu's top edge still returned the
    header, EVEN with `z-index: 20` here - the card's opacity locked the
    menu into its own context, where the comparison never arrived. This
    test knows nothing of stacking contexts, it only compares two numbers
    in the stylesheet - if any of the mentioned side effects returns
    between `.device-card` and `.tile-menu-items`, the menu gets caught
    under the header again, WITHOUT this test (or any other in this file)
    reporting it."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    header_rule = css.split("header.app-header {", 1)[1].split("}", 1)[0]
    header_z_index = int(re.search(r"z-index:\s*(\d+)", header_rule).group(1))
    menu_rule = css.split(".tile-menu-items {", 1)[1].split("}", 1)[0]
    menu_z_index = int(re.search(r"z-index:\s*(\d+)", menu_rule).group(1))
    assert menu_z_index > header_z_index


async def test_the_tile_menu_caps_its_width_and_truncates_long_room_names(api):
    """Finding 2 (follow-up fix 2026-09-05): `.tile-menu-item` is
    `white-space: nowrap`, `.tile-menu-items` only had a `min-width`, no
    upper bound - a freely chosen, long room name inflates the
    shrink-to-fit box to its full word width and, because it is anchored
    at `right: 0`, lets it grow out of the tile to the LEFT. Measured in
    the browser at 375 px width with the room name
    "Werkstatt im Untergeschoss hinter der Heizung und dem Regal":
    menu 384 px wide, left edge at x=-46, the document scrolled
    horizontally. This suite has no browser engine and cannot recompute
    the layout itself - what is proven is only that the delivered rule
    carries a `max-width` and that `.tile-menu-item` truncates its text
    with an ellipsis instead of wrapping."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    items_rule = css.split(".tile-menu-items {", 1)[1].split("}", 1)[0]
    assert "max-width" in items_rule
    item_rule = css.split(".tile-menu-item {", 1)[1].split("}", 1)[0]
    assert "text-overflow: ellipsis" in item_rule
    assert "overflow: hidden" in item_rule
    assert "white-space: nowrap" in item_rule


async def test_the_current_room_checkmark_survives_the_width_cap(api):
    """Review finding 1 (2026-09-05, review of the follow-up work):
    `.tile-menu-item.is-current::after` appends its checkmark as ordinary
    text at the end of the line - exactly where `.tile-menu-item`'s
    `overflow: hidden; text-overflow: ellipsis` (finding 2 of the
    follow-up fix, see above) cuts it off. Measured in the browser with
    the room name
    "Werkstatt im Untergeschoss hinter der Heizung und dem Regal":
    `clientWidth` 246px, `scrollWidth`
    413px, the `::after` sat 167px behind the clip edge - invisible, the
    entry showed the ellipsis with no checkmark at all. Without a browser
    engine this suite cannot reproduce the clipping itself - what is
    proven is only that the delivered rule positions the checkmark outside
    the clipped text flow (`position: absolute` on a
    `position: relative` reference frame) instead of leaving it to the
    text content, and that the current entry reserves space for it."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    item_rule = css.split(".tile-menu-item {", 1)[1].split("}", 1)[0]
    assert "position: relative" in item_rule
    current_rule = css.split(".tile-menu-item.is-current {", 1)[1].split("}", 1)[0]
    assert "padding-right" in current_rule
    after_rule = css.split(".tile-menu-item.is-current::after {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in after_rule


async def test_room_chip_in_the_tile_menu_carries_the_full_name_as_a_title(api):
    """Addition to finding 2: truncated with an ellipsis, the full room
    name is not visible anywhere, unless you hover over the entry or
    focus it - which requires the button to have a native `title`.
    `chip.key` is a room name assigned by the user, so it is data, not a
    UI string to translate - unlike the rest of the menu text, it
    deliberately does NOT run through `t()`. This suite only checks that
    the attribute is delivered, not that the browser actually shows it on
    hover."""
    client, _, _ = api
    page = (await client.get("/")).text
    # `roomChips()` is traversed twice via `x-for`: once in the native
    # `<select>` for "Commission a new device", once in the tile menu -
    # so the search starts only from `.tile-menu-items`, otherwise the
    # first (wrong) match would have hit and the test would never have
    # proven anything.
    menu_start = page.index('class="tile-menu-items"')
    chip_start = page.index('x-for="chip in roomChips()', menu_start)
    chip_end = page.index("</template>", chip_start)
    chip_block = page[chip_start:chip_end]
    assert ':title="chip.key"' in chip_block


async def test_offline_dimming_stays_off_the_tile_menu(api):
    """Finding 3 (follow-up fix 2026-09-05): `.device-card.is-offline {
    opacity: 0.75 }` opened up an opacity context over the ENTIRE card,
    the tile menu included - it hangs as a descendant inside
    `.device-foot`. An offline device thereby dimmed its own menu, which
    floats absolutely over the neighbouring tile, to 75% along with it,
    letting the neighbouring tile visibly show through it. Without a
    browser engine this suite cannot reproduce the show-through itself -
    what is proven is only that `.device-card.is-offline` no longer
    carries a blanket `opacity` and that `.tile-menu` is explicitly
    excluded from the now-targeted dimming."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    # The blanket card rule `.device-card.is-offline { opacity: ... }` no
    # longer exists - only as a selector prefix before `::before` or `>`.
    # A bare `{` directly after it would be the old, discarded version.
    assert ".device-card.is-offline {" not in css
    assert ".device-card.is-offline > *:not(.device-foot) {" in css
    assert ".device-card.is-offline .device-foot > *:not(.tile-menu) {" in css


async def test_offline_stripe_keeps_its_dimmed_look_on_its_own(api):
    """Addition to finding 3: with the dimming moved away from
    `.device-card`, the colour stripe (`.device-card::before`) no longer
    automatically dims along with it as a descendant - without a rule of
    its own it would suddenly look more saturated than before in an
    offline tile. `.device-card.is-offline::before` must therefore now
    carry the `opacity: 0.75` itself."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    stripe_rule = css.split(".device-card.is-offline::before {", 1)[1].split("}", 1)[0]
    assert "opacity: 0.75" in stripe_rule


async def test_offline_dimming_reaches_the_kebab_trigger_too(api):
    """Review finding 2 (2026-09-05, review of the follow-up work): the
    exception `.device-card.is-offline .device-foot > *:not(.tile-menu)`
    excludes the ENTIRE `<details class="tile-menu">`, not only the
    transient, opening list - including its permanently visible
    `<summary>` kebab. Measured in the browser on an offline card:
    `.device-foot` opacity 1, `.tile-menu > summary` opacity 1, while the
    `.hint` next to it sat at 0.75 - the kebab trigger consequently looked
    brighter than everything around it. Without a browser engine this
    suite cannot reproduce the brightness difference itself - what is
    proven is only that the trigger has been given its own, targeted
    `opacity: 0.75` rule, instead of remaining unhandled."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    summary_rule = css.split(".device-card.is-offline .tile-menu > summary {", 1)[1].split("}", 1)[
        0
    ]
    assert "opacity: 0.75" in summary_rule


async def test_offline_footer_divider_matches_the_dimmed_commands_divider(api):
    """Review finding 3 (2026-09-05, review of the follow-up work):
    `.device-commands` and `.device-foot` both draw
    `border-top: 1px solid var(--border)`. On an offline device, the
    first divider dims along as part of `.device-commands` (a direct,
    fully dimmed card child, see above), the second one does not -
    `.device-foot` itself remains excluded from the dimming (only its
    children other than `.tile-menu` are dimmed), so its border keeps
    painting at full strength a few pixels below. Dimming `.device-foot`
    as a whole is out of the question, that would take `.tile-menu` as a
    descendant along with it. Without a browser engine this suite cannot
    reproduce the brightness difference itself - what is proven is only
    that the delivered rule dims ONLY the border colour (`color-mix` on
    `border-top-color`), not the opacity of the whole element."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    foot_offline_rule = css.split(".device-card.is-offline .device-foot {", 1)[1].split("}", 1)[0]
    assert "border-top-color" in foot_offline_rule
    assert "color-mix" in foot_offline_rule
    assert "75%" in foot_offline_rule
    # Must NOT drag along the opacity of the whole `.device-foot` (and
    # thus its descendant `.tile-menu`) - exactly the bug the
    # `:not(.tile-menu)` exception above is meant to prevent.
    assert "opacity" not in foot_offline_rule


async def test_the_footer_pill_wraps_onto_its_own_line_below_the_kebab(api):
    """The kebab shares the first footer line with the export hint, the
    pill wraps onto its own line below it (2026-09-06).

    `order: 1` alone was not enough: if everything fits on one line for a
    wide tile, the pill overtakes the kebab and it ends up sitting in the
    middle of the footer instead of on the right. `flex-basis: 100%`
    always forces the pill onto its own line, making the split the same
    at every tile width. Both properties belong together - that is why
    this test checks both, not just the more conspicuous one.

    Pure delivery proof: what is checked is the rule in the delivered
    stylesheet, not the result in the browser. How it actually plays out
    has been measured by hand at 300, 460, and 526 px tile width (see the
    commit message)."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split(".device-foot > .status-pill {", 1)[1].split("}", 1)[0]
    assert "order: 1" in rule
    assert "flex-basis: 100%" in rule


async def test_the_changed_pill_now_lives_in_the_tile_footer(api):
    """Pill-into-the-footer rebuild (2026-09-06): the changed-since-export
    pill previously sat in the header (`.device-ident`), where, per the
    now-removed design-6.2 comment, it displaced the primary-value label.
    It now sits in `.device-foot`, right next to `exportHintFor` - the
    same export state, the same line, instead of competing for the narrow
    line below the device name. The order hint-then-pill mirrors the
    running text ("Last exported on ... and changed since"), not the
    reverse.

    Pure delivery proof (no browser run, no Alpine rendering): what is
    checked is the position of the text markers in the delivered markup,
    not the actual layout - that only confirms what is delivered, nothing
    about wrapping or overflow in the browser (see the manual check in the
    task report for 261 px/1440 px)."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    foot_pos = markup.index('class="device-foot"')
    hint_pos = markup.index('x-text="exportHintFor(device.id)"')
    pill_pos = markup.index("x-text=\"t('web.devices.changed_since_export')\"")
    offline_pos = markup.index("x-text=\"t('web.devices.offline')\"")
    # The pill sits after the start of the footer and after the export
    # hint - no longer before `.device-foot` in the header.
    assert foot_pos < hint_pos < pill_pos
    # "Offline" stays in the header, i.e. BEFORE the footer: that is
    # device state, not export state, and stays where the eye lands
    # first.
    assert offline_pos < foot_pos

    # The pill is NO LONGER tied to `isOnline` (2026-09-06). This coupling
    # came from the header, where the pill and the offline marker shared a
    # line; nothing competes for space below, and whether a device has
    # changed since export is independent of whether it is currently
    # responding. Without this assertion, a later rebuild could silently
    # drag the condition back in - and a device sitting offline with a
    # pending export would look like one without, because the edge stripe
    # `is-changed` also ties to `isOnline`.
    # Anchor backward on the PILL itself, not on the next `<span`: that
    # would be the inner text span carrying the label key.
    pill_open = markup.rindex('<span class="status-pill warn"', 0, pill_pos)
    pill_tag = markup[pill_open : markup.index(">", pill_open)]
    assert "changedSinceExport(device.id)" in pill_tag
    assert "isOnline" not in pill_tag


async def test_command_row_wrappers_do_not_stack_their_sibling_margin(api):
    """Follow-up fix 2026-09-05, Finding 3: each command sits in its own
    `<span class="row">` wrapper (index.html), and `.row + .row {
    margin-top: 0.5rem }` (further up in this file) is meant for
    vertically STACKED rows. In `.device-commands`, though, the wrappers
    sit SIDE BY SIDE in the same centred flex row - a top margin on one of
    them there does not push it downward, but UPWARD within the centred
    row. Measured in the browser: all three command buttons are the same
    height (30.9px), but the first one (margin-free) sat at `top:
    976.1`, the two following ones at `top: 980.1` (see the task report).
    Without a browser engine this suite cannot recompute the offset itself
    - what is proven is only that `.device-commands` sets the stacking
    margin to 0 in a targeted way, instead of detaching the element (which
    still needs flex/`gap`/centring) from `.row` entirely."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    override_rule = css.split(".device-commands > .row + .row {", 1)[1].split("}", 1)[0]
    assert "margin-top: 0;" in override_rule
    # The general rule, meant for vertical stacks, must remain untouched -
    # the fix is a targeted override, not a removal. As with every other
    # rule check in this file, the rule body is extracted for that instead
    # of matching the complete block including braces and indentation
    # (Finding 3, review from 2026-09-05) - a comment in the rule or a
    # different order of the declaration should not break this test
    # without anything about the behaviour changing.
    base_rule = css.split(".row + .row {", 1)[1].split("}", 1)[0]
    assert "margin-top: 0.5rem;" in base_rule


async def test_device_name_truncates_with_an_ellipsis_instead_of_clipping(api):
    """Fund 1 (Review vom 2026-09-05), nachgezogen 2026-09-07: seit dem
    Wegfall von `.lead-value` hat der Name die Kopfzeile fuer sich und
    kuerzt nur noch bei aussergewoehnlich langen Namen. Dass er es dann
    SICHTBAR tut, bleibt die Zusicherung dieses Tests. Ein
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
    """Finding 2 (review from 2026-09-05), two rounds: move a filtered
    room's last device into another one via the tile menu, or rename/merge
    the filtered room away, and `roomChips()` changes - `roomFilter`
    itself does not, with nothing following it. The filtered room then no
    longer exists, no chip is active anymore, no tile visible anymore, and
    the rename pencil (tied only to `roomFilter`) keeps pointing at a room
    that would return a 404 on click.

    The FIRST version of this fix still deliberately excluded "No room"
    (`roomFilter === ""`), with the (for the rename pencil, correct)
    reasoning that it never shows up for "No room" anyway. The re-review
    (2026-09-05) revealed the blind spot: "No room" is the filter someone
    uses to assign one unsorted device after another to a room - exactly
    that makes the "No room" chip disappear from `roomChips()` as soon as
    the last device has been taken care of, with the same symptoms (no
    chip active, no tile, no way out) as with a real room name. The guard
    therefore now only checks for "All" (`null`) - there is nothing to do
    for that one, `roomChips()` has no chip for it anyway.

    Proven is that the method exists, that its guard as described now
    only excludes `null` (no longer `""`), and that both `saveRoom` and
    `commitRenameRoom` call it after their respective write - not that
    Alpine then actually switches to the "All" chip (that would need a
    browser engine, see `test_the_page_does_not_call_init_a_second_time`)."""
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
    """Finding 3 (review from 2026-09-05): the tile rebuild had lost the
    two hints that used to be gated on `controlsLoaded(device.id)`, and
    the subsequent i18n cleanup pass then consistently - but prematurely -
    deleted their associated keys `web.devices.controls_loading` and
    `web.devices.no_known_commands`. `controlsLoaded()` remained behind as
    dead code, its own docstring justifying why it was needed (Spec 8.1: a
    failure must not appear as a harmless state) - with nothing calling it
    anymore. Without the distinction, a device whose `/controls` fetch has
    failed or is not yet finished renders the same empty, bordered strip
    as a device with no commands at all: exactly the kind of silently
    wrong display Spec 8.1 aims to rule out.

    Analog fuer Signale: `web.devices.no_functional_signals` wurde
    ebenfalls geloescht, wodurch eine Kachel mit geladenen, aber leeren
    funktionalen Signalen (damals: `leadSignalFor` liefert `null`; die
    Bedingung dafuer heisst heute `functionalSignalsFor(id).length === 0`,
    `leadSignalFor` gibt es seit dem Wegfall des Leitwerts nicht mehr)
    zwischen Kopfzeile und Befehlsleiste stillschweigend eine Luecke
    zeigte - ununterscheidbar von einer noch ladenden Kachel.

    Proven is that both distinctions are delivered again and that
    `controlsLoaded` is actually used (again) for it - not that Alpine
    switches them correctly in the browser."""
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
    """`<details>` holds the open/closed state in the DOM - the reason the
    menu is built this way in the first place (see the design, section
    4). Three of the four ways to close it nonetheless have to come by
    hand: `<details>` closes neither on a click outside nor on Escape by
    itself, and Enter in the new-room field commits the draft instead of
    merely closing. The fourth, clicking an entry (room, export, remove),
    sits on the entries themselves.

    Checked exclusively within the tile-menu block, not page-wide:
    `@keydown.escape` (without `.window`) also sits on the room-rename
    field (`room-rename-input`) - a page-wide `in page` test would stay
    green even if the menu's own escape guard were deleted, as long as
    `@keydown.escape` occurs anywhere else on the page. The remaining
    paths run through `closeTileMenu($el)`, which EVERY entry calls on
    click AND the new-room field's Enter handler (see `closeTileMenu` in
    app.js, which actually sets `open = false` there)."""
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
    """Finding 1 (final review 2026-09-05): `newRoomFor` now only toggles
    the visibility of the new-room text field, but was left unchanged on
    three of the four ways to close - only Enter and Escape INSIDE the
    field reset it (see their `@keydown` handlers further down in this
    file). Clicking outside, Escape in the menu, or clicking a room entry
    left `newRoomFor` pointing at the device ID even though the menu is
    visibly closed: on the next open, the tile showed an empty new-room
    field instead of the "+ New room" button - measured in the browser:
    after an outside click, `newRoomFor` stayed pointed at the device's
    ID, and on reopening it was `newRoomButtonVisible: false,
    strayInputVisible: true`.

    The fix hangs off `@toggle` instead of each of the four ways to close
    individually: `<details>` fires `toggle` on EVERY state change,
    whatever the reason, and is thereby the only place that still needs to
    know about the new-room state.

    Finding 5 (re-review 2026-09-05): this check used to pin the entire
    inline expression character for character - whitespace and quotation
    marks included. The obvious next rebuild (pulling the expression into
    a method like `closeTileMenu`) would have broken it with no change in
    behaviour. What is checked now is therefore only the intent: the
    `<details>` carries a `@toggle`, and its expression resets
    `newRoomFor` and `newRoomDraft` on a match with the device ID - no
    longer in exactly what spelling that happens.

    Finding 6 (self-defence review 2026-09-05): the three substring
    assertions below only check that the three components appear
    SOMEWHERE in the expression. Rebuilding the original version discarded
    in Finding 3 (see the comment above the `<details>` in index.html),
    `!$el.open && newRoomFor === device.id`, the expression would still
    literally contain `newRoomFor === device.id`, `newRoomFor = null`, and
    `newRoomDraft = ''` - all three assertions would stay green even
    though exactly the gap Finding 3 closed would be back: if a tile with
    an open menu disappears (a re-render removes the node without
    replacement), a closing `toggle` never fires, `newRoomFor` stays
    pointed at the device ID, and `!$el.open` then discards the next
    `toggle` for the same ID (an open) as a reset trigger - the orphaned
    new-room field is back. Only a dedicated negative assertion on
    `!$el.open` makes this absence explicitly checkable."""
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
    """Finding 2 (review 2026-09-05): `@keydown.escape` without `.window`
    hangs off the `<details>` subtree and only fires when focus sits
    inside it. Safari on macOS does NOT put focus there on a mouse click
    on `<summary>` (default, "Full Keyboard Access" off) - Escape then
    lands on `document.body` and never reaches the `<details>`, the menu
    stays silently open. `.window` instead attaches the listener to
    `window` and works independently of focus. Without a browser engine
    this suite cannot reproduce the focus behaviour itself - what is
    proven is only that the delivered page carries the focus-independent
    form and does not fall back to the element-bound one again."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    assert '@keydown.escape.window="if ($el.open) closeTileMenu($el)"' in menu


async def test_close_tile_menu_restores_focus_conditionally_without_scrolling(api):
    """Self-defence review 2026-09-05, finding 3: `closeTileMenu` (app.js)
    only takes focus back if it actually sat INSIDE the menu when closing
    (`hadFocus`), and does so with `{ preventScroll: true }`. Up to this
    point there was not a single assertion for this in this file -
    `grep -c "preventScroll|hadFocus|tile-menu-new"` returns 0 before this
    test. A refactor could have tipped over either one unnoticed: leave
    out the `hadFocus` guard, and an outside click or Escape outside the
    menu (the `@click.outside`/`@keydown.escape.window` handlers call
    `closeTileMenu` unconditionally for EVERY tile, see the test above)
    would tear focus away from exactly the element the user just clicked -
    even if its menu has long been closed. If `preventScroll` were
    missing, the page would jump back to the `<summary>` on every
    programmatic focus call, even if the corresponding tile has long
    scrolled out of the visible area.

    This is an assertion against the DELIVERED file, not a behaviour
    check: it proves that both building blocks are present in the source
    of `closeTileMenu`, not that the browser actually behaves that way
    when running it (this suite lacks a real browser engine for that, see
    the test above)."""
    client, _store, _device_id = api
    script = (await client.get("/static/app.js")).text
    close_start = script.index("closeTileMenu(el) {")
    close_end = script.index("\n    },", close_start)
    close_body = script[close_start:close_end]
    assert "hadFocus" in close_body
    assert "preventScroll" in close_body


async def test_the_new_room_escape_handler_focuses_the_new_room_button(api):
    """Self-defence review 2026-09-05, finding 3: Escape in the new-room
    text field hides the field via `x-show`, WITHOUT taking focus away
    from it in the process (finding 2, comment block above the
    `<details>` in index.html) - the handler must therefore itself send
    focus to the reappearing "+ New room" button (`.tile-menu-new`).
    Without this assertion, the focus call could disappear from the
    Escape handler without replacement, and the suite would stay green -
    focus would then be left stranded on the invisible text field that
    has fallen out of the tab order, a dead end for keyboard and
    screen-reader use.

    This too is an assertion against the DELIVERED file: it proves that
    the focus call is present in the source, not that the browser also
    moves focus there on an actual Escape press."""
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
    """Both actions now sit in the menu and close it on click. The export
    button remains bound to `bridgeSettings.bridge_ip` - with no bridge IP
    entered, there is nothing to export."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    assert "exportDevice(device)" in menu
    assert "removeDevice(device)" in menu
    assert "!bridgeSettings.bridge_ip" in menu


async def test_the_rooms_are_menu_entries_and_the_select_is_gone(api):
    """Room assignment is now a list of entries in the menu. The
    `<select>` and the entire mechanism that was needed to keep its
    displayed value in sync with `device.room` is gone without
    replacement - that is exactly the point of this rebuild."""
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
    """The checkmark on the current room is purely visual. `aria-current`
    carries the same information for anything the page cannot see -
    without it, the current room in the menu would be just one of several
    identical-looking rows.

    Checked within the tile-menu block, not page-wide - a bare
    `"aria-current" in page` would already be satisfied by some future,
    entirely unrelated use of the attribute elsewhere on the page, without
    anything being marked here at all. Also proven is that the attribute
    is actually tied to `roomKeyOf(device)` - the same comparison that
    also drives `.is-current` (the visible checkmark) - and not to an
    independent expression that could potentially drift out of sync."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    assert ":aria-current=\"roomKeyOf(device) === '' ? 'true' : null\"" in menu
    assert ":aria-current=\"roomKeyOf(device) === chip.key ? 'true' : null\"" in menu


async def test_the_kebab_button_names_itself_via_aria_label_and_hides_its_icon(api):
    """A11y follow-up fix (2026-09-05), finding 1: the kebab button
    previously only carried `title`, and `title` is merely the last
    fallback value when computing the accessible name - screen readers do
    not reliably rely on it. The spec text for this menu already called
    `web.devices.menu` "the accessible name of the ⋮ button" (see the
    comment above the keys in strings.yaml), which simply was not true
    until this fix. `aria-label` now carries the same translated value,
    `title` additionally remains as a mouse tooltip - different purposes,
    both attributes are allowed to sit side by side.

    The `<svg>` at the same time gets `aria-hidden="true"`: a decorative
    icon inside an element that already has a name must not give the
    accessibility tree a second, competing name. This is an assertion
    against the DELIVERED file, not a behaviour check - this suite lacks
    a real browser engine for the actual name computation (see the other
    tile-menu tests in this file)."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    summary = menu.split("<summary", 1)[1].split("</summary>", 1)[0]
    assert ":title=\"t('web.devices.menu')\"" in summary
    assert ":aria-label=\"t('web.devices.menu')\"" in summary
    assert 'aria-hidden="true"' in summary


async def test_the_room_list_is_a_labelled_group_for_assistive_tech(api):
    """A11y follow-up fix (2026-09-05), finding 2: the
    `.tile-menu-heading` heading was purely a visual section label for the
    room entries - without a programmatic connection, a screen-reader user
    tabbing through only hears "Kitchen, current entry" with no indication
    at all that this is a room. `role="group"` plus `aria-labelledby` on a
    new `.tile-menu-rooms` wrapper establishes this connection.

    The heading's id is deliberately derived PER DEVICE
    (`'tile-menu-room-heading-' + device.id`) instead of a fixed constant:
    the whole page shares a single `x-data`, and this markup is rendered
    once PER TILE - a fixed id would be duplicated in the delivered
    document as many times as there are tiles, and `aria-labelledby` would
    then only hit the first occurrence. The search therefore explicitly
    checks for the `device.id` expression, not just that some id exists -
    reverting to a constant would otherwise look identical at first
    glance.

    Also proven is that the group actually encloses the room entries and
    the new-room field, but leaves export and remove outside - a group
    drawn too wide or too narrow would be just as wrong for screen-reader
    users as no group at all.

    Review finding 7 (2026-09-05, review of the follow-up work): ending
    the cut at the FIRST `</div>` after the group's opening tag is only
    correct as long as no nested `<div>` appears inside the group - if one
    were added, `menu.index("</div>", ...)` would find its closing tag
    instead of the group's, and `group_body` would then be cut off too
    short. The POSITIVE assertions below would loudly report that (the
    truncated text would then no longer contain "roomChips()" and
    friends), whereas the two NEGATIVE ones ("not in") would silently keep
    passing even though they no longer check anything - a nested `<div>`
    simply falls outside the (now too short) `group_body`, before
    export/remove could even come into play. The end is therefore anchored
    at `<hr class="tile-menu-sep"` - the actual, markup-fixed end of the
    room group (see index.html), not at some arbitrary first `</div>`."""
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
    """A11y follow-up fix (2026-09-05), finding 2: `.tile-menu-items` is a
    flex column whose children are directly the menu entries (reasoning at
    the rule itself, style.css). The `.tile-menu-rooms` wrapper for
    `role="group"` is purely semantic and must not change this layout.

    Review finding 5 (2026-09-05, review of the follow-up work):
    originally solved via `display: contents` (exactly like `.room-picker`
    once was in the same file) - but that has a known WebKit limitation:
    Safari partly drops `display: contents` subtrees from the
    accessibility tree, so the `role="group"` together with
    `aria-labelledby` would never arrive there, even though Safari is
    explicitly a target elsewhere in this file. The delivered rule
    therefore instead turns the wrapper into a real box with the same
    column flex, the same `gap`, and the same `align-items` as
    `.tile-menu-items` itself - layout-identical, but without the browser
    compatibility issue. Without this follow-up, the room entries would
    otherwise (with `display: contents` or with no special treatment at
    all) no longer sit on the same level as "+ New room", the separator,
    export, and remove."""
    client, _store, _device_id = api
    css = (await client.get("/static/style.css")).text
    rule = css.split(".tile-menu-rooms {", 1)[1].split("}", 1)[0]
    assert "display: contents" not in rule
    assert "display: flex" in rule
    assert "flex-direction: column" in rule
    assert "align-items: stretch" in rule
    assert "gap: 1px" in rule


async def test_exactly_one_dialog_of_each_kind_is_delivered(api):
    """Entwurf Abschnitt 4: EIN `<dialog>` je Zweck fuer die ganze Seite,
    nicht eines je Kachel.

    Markup innerhalb `x-for` wird einmal PRO GERAET ausgeliefert - bei
    dreissig Geraeten laegen dreissig vollstaendige Signaltabellen im
    Dokument, und jede `id` darin dreissigfach (derselbe Fallstrick, den
    `aria-labelledby` im Kachel-Menue schon einmal umschiffen musste). Die
    Zaehlung auf 2 (ein Signal-Modal, ein Bedien-Modal aus Aufgabe 7) ist
    die einzige Zusicherung, die diesen Rueckfall ueberhaupt bemerken
    wuerde: ein `<dialog>` in der Kachel saehe im ausgelieferten Text sonst
    genauso aus wie eines am Seitenende.

    Die Ortspruefung (nach `</main>`) belegt zusaetzlich, dass beide
    ausserhalb der Ansichts-Sections und damit ausserhalb jeder
    Geraeteschleife stehen."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert markup.count("<dialog") == 2
    assert 'x-ref="signalsModal"' in markup
    assert 'x-ref="controlModal"' in markup
    assert markup.index("<dialog") > markup.index("</main>")


async def test_the_signals_modal_has_exactly_one_place_that_resets_its_state(api):
    """Design section 4: `@close` is the ONE AND ONLY place that resets
    it.

    The event fires on every way of closing - Escape, the close button,
    the backdrop, `close()` from JavaScript. A second reset spot on a
    single way of closing would be exactly the spread across multiple
    handlers that cost six review rounds on the room selection field;
    that is why this test counts the occurrences instead of searching for
    just one.

    `@click.self` is required for this and not incidental: a `<dialog>`
    does NOT close by itself on a click on the backdrop.

    Finding 12 (final branch review, 2026-09-06): `@click.self` alone
    also closes the modal when a mousedown STARTS inside the content
    (e.g. while selecting a signal title) and the mouseup, from dragging
    over, lands on the backdrop - the click target is then `<dialog>`,
    even though the gesture started in the content. `@mousedown` has
    since recorded whether the mousedown ITSELF already started on the
    backdrop; the click handler now only closes if both are true.
    `signalsModalBackdropMousedown` is purely presentation bookkeeping in
    this, not part of the modal state being guarded here - `@close`
    nonetheless remains the only place that resets `signalsModalDevice`.

    Finding 1 (fix verification, 2026-09-06): the mousedown handler must
    NOT carry `.self`, and this test is the only place that records that.
    With `.self`, Alpine skips the expression entirely if the target is
    not the `<dialog>` - the field would then only ever be set, never
    cleared, and a mousedown on the backdrop with no following click on it
    (Escape with the mouse button held down, releasing outside the
    window) would leave it permanently at `true`. The next drag starting
    from the content would then close the modal in exactly the way
    Finding 12 was meant to prevent. Without `.self`, every mousedown in
    the subtree rewrites the field.

    `isBackdropEvent` (app.js) decides whether an event actually occurred
    on the backdrop: the modal's own scrollbar also belongs to the
    `<dialog>` and delivers the same target, so grabbing it would
    otherwise close the modal - of all things, for the long lists it
    exists for. Checked at BOTH ends of the gesture, otherwise a drag from
    the backdrop INTO the content would also close it: the click event
    fires at the nearest common ancestor of both targets, and that is
    again the `<dialog>`.

    Die frueher hier stehende `offsetX < clientWidth`-Bedingung ist
    ersetzt, nicht ergaenzt: sie trennte nur einen Scrollbalken ab, der
    PLATZ RESERVIERT, und lief bei einem ueberlagernden (macOS-
    Voreinstellung, misst 0 px) ins Leere - dazu deckte sie weder einen
    waagerechten Balken noch eine RTL-Anordnung ab. Der Rechteckvergleich
    in `isBackdropEvent` braucht keine dieser Fallunterscheidungen; taucht
    `offsetX` hier je wieder auf, ist das ein Rueckschritt.

    Fund 3 (Nachpruefung, 2026-09-07): derselbe `@close`-Handler setzt seit
    da zusaetzlich `expandedSignalKey` zurueck - dediziert geprueft in
    `test_closing_the_signals_modal_resets_the_open_detail` weiter unten;
    hier zaehlt nur weiterhin `signalsModalDevice = null`, um genau EINE
    Ruecksetzstelle fuer dieses Feld zu belegen."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    script = (await client.get("/static/app.js")).text
    assert '@close="signalsModalDevice = null; expandedSignalKey = null"' in markup
    assert '@mousedown="signalsModalBackdropMousedown = isBackdropEvent($event, $el)"' in markup
    assert "@mousedown.self=" not in markup
    assert (
        '@click.self="if (signalsModalBackdropMousedown && isBackdropEvent($event, $el)) '
        '$el.close(); signalsModalBackdropMousedown = false"' in markup
    )
    assert markup.count("signalsModalDevice = null") == 1

    # The helper compares the position against the dialog's rectangle -
    # not `offsetX` against `clientWidth`, see above.
    helper_start = script.index("isBackdropEvent(event, el) {")
    helper = script[helper_start : script.index("\n    },", helper_start)]
    assert "event.target !== el" in helper
    assert "getBoundingClientRect()" in helper
    for edge in ("rect.left", "rect.right", "rect.top", "rect.bottom"):
        assert edge in helper
    assert "offsetX" not in helper


async def test_the_two_entry_points_open_the_signals_modal(api):
    """Design section 5: the modal has exactly two entry points.

    The kebab entry calls `closeTileMenu($el)` FIRST, then
    `openSignalsModal(device)` - this order carries the focus:
    `closeTileMenu` sets it onto the `<summary>`, and the immediately
    following `showModal()` remembers exactly this focus as the return
    point. Reversed, focus would land nowhere after the modal closes.

    The `+ N more signals` link used to jump via `selectView('signals')`
    into a list of ALL devices, in which one then had to search for one's
    own again - it now points at the device whose signals it promises."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert '@click="closeTileMenu($el); openSignalsModal(device)"' in markup
    assert "x-text=\"t('web.devices.menu_signals')\"" in markup
    assert '@click.prevent="openSignalsModal(device)"' in markup

    # Compare the order ONLY within the menu: the `+ N more signals` link
    # sits further up in the same tile and calls the same method, so a
    # `markup.index(...)` over the whole page would hit it instead of the
    # menu entry and would always be true.
    menu_start = markup.index('<div class="tile-menu-items">')
    menu = markup[menu_start : markup.index("</details>", menu_start)]
    assert menu.index("openSignalsModal(device)") < menu.index("exportDevice(device)")


async def test_open_signals_modal_shows_the_dialog_only_after_alpine_rendered(api):
    """Design section 4: `showModal()` only within `$nextTick`.

    `showModal()` sets the initial focus onto the first focusable element
    INSIDE the dialog - and that only exists after Alpine has built the
    `x-if` content. Without `$nextTick`, the dialog opens empty and focus
    lands on the `<dialog>` itself; the first Tab press then starts at the
    beginning of the document instead of in the modal."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("openSignalsModal(device) {")
    end = script.index("\n    },", start)
    body = script[start:end]
    assert "this.signalsModalDevice = device.id;" in body
    assert "this.$nextTick(() => this.$refs.signalsModal.showModal());" in body


async def test_opening_the_signals_modal_clears_a_stale_error_from_another_device(api):
    """Finding 2 (final branch review, 2026-09-06): `signalsError` is
    page-wide, but the modal is per device. `startApp` loads the signals
    of all devices in parallel, and each `loadSignals` only clears
    `signalsError` BEFORE its own fetch - if device A fails and device B
    then loads successfully, device A's error remains. If the modal is
    then opened for a third, cleanly loaded device, the nameless error
    hangs over its list even though it has nothing to do with this device.
    `openSignalsModal` must therefore clear the error itself, as the very
    first statement.

    This is NOT a second reset spot for `signalsModalDevice` - that rule
    (see `test_the_signals_modal_has_exactly_one_place_that_resets_its_state`)
    concerns exclusively that one field; `signalsError` is independent
    state."""
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
    """Design section 4, "When the device disappears".

    Without this call, a dialog would stay open over a device that no
    longer exists - and the `x-if` guard would turn it into an empty box
    with no discernible reason."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("async removeDevice(device) {")
    end = script.index("\n    },", start)
    body = script[start:end]
    assert "if (this.signalsModalDevice === device.id) {" in body
    assert "this.closeSignalsModal();" in body


async def test_losing_authentication_closes_an_open_signals_modal(api):
    """Finding 3 (final branch review, 2026-09-06): the `<dialog>`
    deliberately sits outside
    `<template x-if="stringsReady && authenticated">` (see index.html), so
    that `$refs.signalsModal` is always resolvable - but nothing used to
    close it when `authenticated` flips to `false`. If the session drops
    while the modal is open (a bridge restart via
    `handleLiveDisconnect` -> `loadAuthInfo`, or a 401 from a modal action
    via `noteAuthError`), Alpine renders the login screen BEHIND an open
    `showModal()` dialog: everything outside it is inert, the password
    field and error banner unreachable.

    This test only proves that both places where `authenticated` is set
    to `false` call `closeSignalsModal()` - NOT that a browser actually
    turns this into a reachable login screen; that would need a real
    rendering engine (see `test_the_page_does_not_call_init_a_second_time`
    above)."""
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
    # The call hangs off the `if (!this.authenticated)` branch, not the
    # success case - a version that only passed the bare `in` test would
    # let `closeSignalsModal()` through even if it sat unconditionally
    # BEFORE the condition and closed the modal on every call.
    guard_index = load_auth_info_body.index("if (!this.authenticated) {")
    call_index = load_auth_info_body.index("this.closeSignalsModal();")
    assert guard_index < call_index


def _signals_dialog(markup: str) -> str:
    """The content of the signals modal, without the rest of the page.

    A bare `in markup` would count the old signals section too, as long as
    it still exists (Task 3 only removes it afterwards) - and would still
    hit the device tiles afterwards, which use the same helpers."""
    start = markup.index("<dialog")
    return markup[start : markup.index("</dialog>", start)]


async def test_the_signals_modal_carries_the_complete_signal_row(api):
    """Design section 2: the signal row moves over 1:1, with no loss of
    functionality - title, export, resend, and raw-value write included.
    Exactly these four write paths are what the old view was capable of
    on its own; if one falls off during the move, it is no longer
    reachable anywhere."""
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
    """Design section 4, point 2: `signalsError` sits INSIDE the modal.

    A `<dialog>` in the top layer covers everything below it including
    the backdrop - an error banner outside it would be invisible during
    the one action that can trigger it (saving a title, setting the
    checkbox, writing a raw value). Per Spec 8.1, an invisible error is
    worse than none."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    assert 'x-show="signalsError"' in dialog
    assert 'x-text="signalsError"' in dialog


async def test_both_signal_groups_share_one_details_template(api):
    """Design section 4, point 5: ONE template for both groups.

    Two forms (a block here, `<details>` there) would mean two branches
    and, in each, its own copy of the signal-row template - exactly the
    duplication `signalGroupsFor` abolished (51 duplicate lines, see its
    comment in app.js).

    The initial state runs via `x-init` and NOT via a bound `:open`:
    Alpine re-evaluates bindings on every change to their dependencies,
    and `signalGroupsFor` depends on `signalsByDevice` - a saved signal
    title would rewrite an `:open` and silently close the expert group
    that was just opened. This test is the only brake against a later,
    well-meaning simplification to `:open`."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    assert 'x-for="group in signalGroupsFor(signalsModalDevice)"' in dialog
    assert dialog.count("<details") == 1
    assert 'x-init="$el.open = !group.collapsible"' in dialog
    assert ":open=" not in dialog
    assert "x-text=\"t('web.signals.functional_vs_expert_explanation')\"" in dialog
    assert "x-text=\"t('web.signals.none_functional')\"" in dialog


async def test_the_signals_modal_head_ships_a_labelled_heading_and_a_close_button(api):
    """Finding 4 and finding 5 (final branch review, 2026-09-06): neither
    the heading nor the close button nor the `#i-close` symbol had been
    anchored anywhere so far - an edit that deletes the button (the only
    always-reachable way out of the dialog besides Escape) would run
    right past this suite unimpeded.

    Also the proof for finding 4: the `<dialog>` carries
    `aria-labelledby="signals-modal-heading"`, and exactly this `id` sits
    on the `<h2>` - without it, a screen-reader announcement would say
    "dialog" with no discernible subject. Unlike the `aria-labelledby` on
    the tile menu (which must derive its id from `device.id`, because it
    exists once PER TILE), a fixed id is correct here, because this
    `<dialog>` exists only a single time in the document.

    Finding 2 (fix verification, 2026-09-06): BOTH sides of the icon are
    checked - the `<use href="#i-close">` in the dialog AND the
    `<symbol id="i-close">` definition in the sprite block above.
    `#i-close` otherwise has no user; without the second assertion, the
    symbol could be deleted without this suite noticing anything, and the
    button would silently draw nothing (a `<use>` pointing at a missing id
    stays empty, with no console message). The test
    `test_the_tile_menu_has_its_own_icon_symbol` further above checks the
    same pair for the same reason."""
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
    """Finding 1 (review of the follow-up work, 2026-09-06): the comment
    on the `.signal-group` block in `style.css` claimed that a
    `<summary>`'s default marker deliberately remained visible there -
    when in fact none of the rules suppressed it: no `list-style: none`,
    no `::-webkit-details-marker` rule, no chevron. What was delivered was
    therefore the browser's own disclosure triangle (Firefox: outline,
    WebKit: filled) - the opposite of what the comment claimed.

    This test anchors the correct counterpart: the same double
    suppression as with `.tile-menu > summary` and
    `.projectsync-device summary`, plus the replacement chevron
    (`#i-chevron`), which rotates on opening. It proves only that the
    markup and stylesheet carry the nodes and rules needed for that - not
    that a rendering engine actually turns this into an invisible default
    marker and a visible, rotating arrow; that would need a real browser.
    A future edit that removes only one half of the duplication and
    leaves the comment standing fails here."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    dialog = _signals_dialog(markup)
    assert 'class="icon chevron" aria-hidden="true"' in dialog
    assert 'href="#i-chevron"' in dialog

    css = (await client.get("/static/style.css")).text
    block_start = css.index("/* The two signal groups in the modal.")
    block = css[block_start:]
    assert ".signal-group > summary {" in block
    assert "list-style: none;" in block
    assert ".signal-group > summary::-webkit-details-marker {" in block
    assert ".signal-group > summary .chevron {" in block
    assert ".signal-group[open] > summary .chevron {" in block
    assert "transform: rotate(90deg);" in block
    assert "ERWUENSCHT" not in block


async def test_the_signals_view_is_gone_from_navigation_and_markup(api):
    """Design section 3: the tab is dissolved without replacement.

    Checked is not only the nav button, but also that NOWHERE does
    anything still switch to the view value `'signals'` - a leftover
    `selectView('signals')` would be a click that sends the app into a
    view that no longer exists: all sections would stay hidden, the page
    would be empty, with no error message.

    `showExpertSignals` falls with it: the open/closed state now lives in
    the DOM (`<details>` in the modal), a global field for it would be a
    second source of truth with no reader."""
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
    """The three keys of the old tab have no reader left.

    `expert_collapsed_hint` falls away without replacement rather than
    moving: "12 expert signals hidden" says the same thing as "Expert
    (12)" in the `<summary>`, just not at the place where you click. A key
    left standing would not merely be dead - its own text would refer to a
    toggle that no longer exists."""
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
# Search field of the device view (design from 2026-09-06). The field fell
# through the CSS grid - the form rule lists text, number, password, and
# select, but not search - which is why the browser drew it itself.
# ---------------------------------------------------------------------------


async def test_the_search_field_ships_the_words_for_counter_and_clear_button(api):
    """The counter carries text, the clear cross carries none and
    therefore needs an accessible name - both must arrive at the browser
    translated.

    `{count}` remains UNRESOLVED here: it is resolved in app.js
    (`t(key, values)`) once the number is known. The server does not know
    it, and `GET /api/i18n` therefore delivers the raw template - exactly
    that is what the comparison against the string, braces included,
    proves."""
    client, _, _ = api
    strings = (await client.get("/api/i18n")).json()["strings"]
    assert strings["web.devices.search_count"] == "{count} found"
    assert strings["web.devices.search_clear"] == "Clear search"


async def test_the_search_field_has_a_magnifier_of_its_own(api):
    """The magnifier comes from the inline sprite like every other symbol
    - the same reasoning as for the checked-in vendor/alpine.min.js: the
    UI runs offline.

    The clear cross, by contrast, gets NO symbol of its own, it uses the
    existing `#i-close`. Two identical shapes would be two places to
    remember the next time the stroke weight changes - and one of them
    would be forgotten."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    assert 'id="i-search"' in page
    assert page.count('id="i-close"') == 1


async def test_the_search_field_carries_the_frame_and_the_input_does_not(api):
    """The border sits on the container, not on the field.

    If both carried one, a border would sit inside the other one - and
    the focus ring (next test) would have nothing to attach to that
    encloses the magnifier and the cross too."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    field = css.split(".search-field {", 1)[1].split("}", 1)[0]
    inner = css.split('.search-field input[type="search"] {', 1)[1].split("}", 1)[0]
    assert "border: 1px solid var(--border)" in field
    assert "border-radius" in field
    assert "border: none" in inner
    assert "background: none" in inner


async def test_the_search_focus_ring_wraps_the_whole_group(api):
    """`:focus-within` on the container instead of `:focus` on the field:
    the ring should enclose the magnifier, the counter, and the cross too,
    not only the input field in the middle of them. The browser's own
    outline on the field must give way for that, otherwise both would
    show at once."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    ring = css.split(".search-field:focus-within {", 1)[1].split("}", 1)[0]
    assert "border-color: var(--accent)" in ring
    inner_focus = css.split('.search-field input[type="search"]:focus {', 1)[1].split("}", 1)[0]
    assert "outline: none" in inner_focus


async def test_the_browser_does_not_add_a_second_clear_cross(api):
    """WebKit places its own clear cross inside an
    `input[type="search"]` - ours would sit right next to it a second
    time.

    It is turned off with `-webkit-appearance` AND `appearance`: the
    pseudo-element is vendor-specific, and the assertion on the second
    form needs the start of the line - `"appearance: none"` is a
    substring of `"-webkit-appearance: none"` and would otherwise already
    be satisfied by the first line."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split("::-webkit-search-cancel-button {", 1)[1].split("}", 1)[0]
    assert "-webkit-appearance: none" in rule
    assert re.search(r"^\s*appearance: none", rule, re.MULTILINE)


async def test_the_counter_and_the_cross_appear_only_with_a_query(api):
    """Both depend on `deviceSearch` and carry `x-cloak`: with an empty
    field they are gone, and on the first draw they do not flash up
    before Alpine has initialised.

    The counter reads `visibleDevices().length` - i.e. what actually sits
    below the bar, including an active room filter. The search logic
    itself remains untouched.

    Two `aria-label`s: one on the input field (it only carries a
    placeholder, and that disappears the moment someone types something),
    one on the cross (it carries no word at all).

    The cross must put focus back into the field: on clearing, `x-show`
    sets `display: none` on the element that currently holds focus, and
    the browser then throws it onto `<body>`. WebKit's native clear cross
    - `::-webkit-search-cancel-button`, deliberately turned off elsewhere
    in this file - did exactly that: kept focus in the field. Our own
    cross must achieve the same, otherwise keyboard operation loses its
    place through the clearing and would have to tab through the page
    again from the very top.

    `aria-live="polite"` on the counter: for screen-reader users it
    answers the same question it also answers visually - how many matches
    remain after the last keystroke, down to zero. Without the live
    region this would stay silent."""
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
    """The spacer that pushes the field to the right exists only together
    with the chips it would need to push past.

    With no rooms, the chip bar hides itself (`x-if="hasAnyRoom()"`). If
    the spacer then remained in the markup - as it used to - a single box
    would be left standing on the right in an otherwise empty row. With
    its own `x-if`, it disappears along with the chips, and the field
    moves to the left edge, onto a sightline with the tile grid below it.

    Two `x-if="hasAnyRoom()"` in the bar are therefore correct and not an
    oversight: one for the chips, one for the spacer."""
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
def test_the_battery_never_opens_the_value_grid_and_is_never_counted_twice():
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
          battery: state.batterySignalFor(1).key,
          preview: state.firstSignalsFor(1).map((s) => s.key),
          remaining: state.remainingSignalCount(1),
        }));
        """
    )

    assert values["preview"][0] == "d1_1_s0"
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
          first: state.firstSignalsFor(1)[0].key,
          remaining: state.remainingSignalCount(1),
        }));
        """
    )

    assert values["battery"] is None
    assert values["first"] == "d1_1_onoff"
    assert values["remaining"] == 0


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_a_device_whose_only_functional_signal_is_the_battery_shows_an_empty_grid():
    """Der Randfall, an dem der Hinweis "keine funktionalen Signale" falsch
    waere: es GIBT eines, es steht nur in der Fusszeile.

    Das Werteraster ist hier leer, obwohl `functionalSignalsFor` ein Signal
    liefert - `previewSignalsFor` nimmt den Batteriestand ja heraus. Genau
    deshalb haengt der Hinweis im Markup an `functionalSignalsFor` und nicht
    an den Rasterzeilen, siehe
    `test_the_no_functional_signals_hint_asks_the_list_not_the_grid`."""
    values = _app_state(
        """
        state.signalsByDevice = { 1: [
          { key: "d1_0_battery", title: "battery", endpoint: 0, cluster_id: 47, functional: true },
        ] };
        console.log(JSON.stringify({
          rows: state.firstSignalsFor(1).length,
          functional: state.functionalSignalsFor(1).length,
          battery: state.batterySignalFor(1).key,
          remaining: state.remainingSignalCount(1),
        }));
        """
    )

    assert values["rows"] == 0
    assert values["functional"] == 1
    assert values["battery"] == "d1_0_battery"
    assert values["remaining"] == 0


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_a_device_with_two_power_source_endpoints_keeps_both_out_of_the_grid():
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
          first: state.firstSignalsFor(1)[0].key,
          battery: state.batterySignalFor(1).key,
          preview: state.previewSignalsFor(1).map((s) => s.key),
          previewClusters: state.previewSignalsFor(1).map((s) => s.cluster_id),
        }));
        """
    )

    assert values["first"] == "d1_1_onoff"
    assert values["battery"] == "d1_0_battA"
    assert 47 not in values["previewClusters"]
    assert values["preview"] == ["d1_1_onoff"]


async def test_the_tile_has_a_battery_row_with_its_own_symbol(api):
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert 'id="i-battery"' in page
    assert "device-battery" in page
    assert 'x-show="batterySignalFor(device.id)"' in page


async def test_the_no_functional_signals_hint_asks_the_list_not_the_grid(api):
    """Der Hinweis muss an `functionalSignalsFor` haengen, nicht an den
    Rasterzeilen.

    Der Grund ist die Batteriezeile: `previewSignalsFor` nimmt den
    Batteriestand aus dem Werteraster heraus, ein Geraet mit NUR einem
    Batteriesignal hat dort also null Zeilen bei einem funktionalen Signal.
    Ueber die Rasterzeilen gefragt behauptete der Hinweis dann "keine
    funktionalen Signale", waehrend die Fusszeile darunter eines zeigt.

    Die Bedingung fragte frueher `!leadSignalFor(device.id)` und nach dem
    Batterie-Umbau `!leadSignalFor(...) && !batterySignalFor(...)`. Mit dem
    Wegfall des Leitwerts ist beides hinfaellig - `functionalSignalsFor`
    beantwortet dieselbe Frage direkt und deckt den Batterie-Randfall von
    sich aus ab."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    hint = page[page.index("no_functional_signals") - 400 : page.index("no_functional_signals")]
    assert "functionalSignalsFor(device.id).length === 0" in hint
    # Weder ueber die Rasterzeilen noch ueber den entfallenen Leitwert.
    assert "firstSignalsFor" not in hint
    assert "leadSignalFor" not in hint


async def test_the_signal_rows_and_the_header_share_one_grid(api):
    """Was heute fehlt und weshalb nichts fluchtet: die Zeile ist ein
    `flex-wrap`-Container ohne Spaltenmasse. Bei `multipress_ongoing`
    rutschte "periodisch erneut senden" allein in die naechste Zeile.

    200px statt vormals 150px fuer die Schluessel-Spalte (Aufgabe 13, Fund:
    der alte Kommentar behauptete eine Messung, die nie stattfand - 150px
    ueberliefen bei `d4_1_multipress_ongoing`, dem tatsaechlich laengsten
    Schluessel der vier Demo-Geraete, gemessen 183px scrollWidth gegen
    148px clientWidth). Diese Zusicherung MUSS mitgezogen werden, sonst
    haette der Umbau eine Kopf- und eine Datenzeile mit verschiedenen
    Vorlagen hinterlassen - genau die Abweichung, die dieser Test
    ueberhaupt sperrt.

    Fund (Abschlusspruefung): `page.count("signal-grid") >= 2` zaehlt nur
    einen Teilstring. Allein `class="signal-grid signal-grid-head"`
    liefert zwei Treffer, weil "signal-grid-head" mit "signal-grid"
    beginnt - die Datenzeile koennte ihre Rasterklasse also ganz verlieren
    (Ausgangszustand des Umbaus) und der zaehlende Assert bliebe gruen.
    Ebenso pruefte die zweite Zusicherung nur, dass die Spaltenmasse
    IRGENDWO im CSS stehen, nicht dass Kopf UND Zeile sie ueber dieselbe
    Regel beziehen - ein spaeterer `.signal-row-cells { grid-template-
    columns: ... }`-Override waere unsichtbar geblieben. Diese Fassung
    verlangt beide Klassen einzeln am jeweiligen Element und bindet die
    Spaltenmasse an den Regelkoerper von `.signal-grid` allein, mit einer
    Gegenprobe, dass keine der beiden Modifikator-Klassen sie ausserhalb
    der Medienabfrage nochmal selbst definiert.

    Fund (Nachpruefung vor dem Merge): `rule_body` benutzte `css.index`, das
    nur die ERSTE Regel mit diesem Selektor findet. Ein spaeterer Override
    derselben Klasse - genau der Fall, gegen den diese Gegenprobe antreten
    soll - steht aber am Ende der Datei und gewinnt dort die Kaskade,
    unabhaengig davon, ob die erste Fassung sauber ist. Beleg: haengt man
    `.signal-row-cells { grid-template-columns: 40px 1fr; }` ans Ende von
    `style.css`, bleibt die alte Fassung dieses Tests gruen, obwohl Kopf-
    und Datenzeile danach nachweislich verschiedene Spaltenmasse haben.
    `rule_bodies_outside_media` sammelt deshalb ALLE Regelkoerper fuer
    einen Selektor (nach Entfernen der `@media`-Bloecke, in denen `.signal-
    grid` bewusst eine andere Vorlage fuer den schmalen Fall traegt), und
    die Gegenprobe prueft jeden davon."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    css = (await client.get("/static/style.css")).text

    assert 'class="signal-grid signal-grid-head"' in page
    assert 'class="signal-grid signal-row-cells"' in page

    def _without_media_queries(css: str) -> str:
        """`css` ohne jeden `@media`-Block, per Klammerzaehlung entfernt -
        ein einfaches `css.index("}")` braeche bei der ERSTEN Regel im
        Block ab, nicht am Blockende, weil Regeln selbst auch `{ }` tragen."""
        pieces = []
        pos = 0
        while True:
            at = css.find("@media", pos)
            if at == -1:
                pieces.append(css[pos:])
                break
            pieces.append(css[pos:at])
            open_brace = css.index("{", at)
            depth = 1
            i = open_brace + 1
            while depth:
                if css[i] == "{":
                    depth += 1
                elif css[i] == "}":
                    depth -= 1
                i += 1
            pos = i
        return "".join(pieces)

    def rule_bodies_outside_media(selector: str, css: str) -> list[str]:
        """ALLE Regelkoerper fuer `selector` ausserhalb jeder Medienabfrage -
        nicht nur der erste (siehe Docstring oben: ein Override am
        Dateiende blieb sonst unsichtbar)."""
        bodies = []
        pos = 0
        while True:
            start = css.find(selector, pos)
            if start == -1:
                return bodies
            open_brace = css.index("{", start)
            close_brace = css.index("}", open_brace)
            bodies.append(css[open_brace:close_brace])
            pos = close_brace + 1

    css_outside_media = _without_media_queries(css)

    grid_bodies = rule_bodies_outside_media(".signal-grid {", css_outside_media)
    assert len(grid_bodies) == 1
    assert "grid-template-columns: 58px minmax(0, 1fr) 200px 70px 76px 28px" in grid_bodies[0]
    # Bei JEDER Fassung von `.signal-grid-head`/`.signal-row-cells` ausserhalb
    # der Medienabfrage duerfen die Spaltenmasse nicht eigenstaendig
    # auftauchen, sonst koennten Kopf und Zeile ueber je eine eigene Regel
    # auseinanderlaufen, ohne dass es hier auffiele.
    for body in rule_bodies_outside_media(".signal-grid-head {", css_outside_media):
        assert "grid-template-columns" not in body
    for body in rule_bodies_outside_media(".signal-row-cells {", css_outside_media):
        assert "grid-template-columns" not in body


async def test_the_key_pill_wraps_instead_of_touching_the_value_column(api):
    """Fund (Aufgabe 13): 200px reichen fuer die heutigen Schluessel, aber
    `.key` selbst hatte weder `white-space` noch `overflow` - ein noch
    laengerer Schluessel (z. B. eine dreistellige Geraete-ID) wuerde die
    Spalte erneut sprengen, unsichtbar fuer eine Messung, die nur auf
    abweichende Spaltenkanten prueft. Die Absicherung sitzt bewusst an
    `.signal-grid .key`, nicht an `.key` global: dieselbe Klasse traegt
    auch die Firmware-Dateinamen im Diagnose-Tab und den
    Kommissionierungscode im Kopf (`class="key"` an anderer Stelle in
    `index.html`) - eine globale Regel haette dort mitgewirkt, ohne dass
    dieser Test das je gesehen haette.

    `overflow-wrap: break-word` statt `text-overflow: ellipsis`: `.key`
    traegt `user-select: all`, die Pille ist zum Kopieren gedacht, und es
    gibt im Signal-Modal keinen zweiten Ort, der denselben Schluessel
    ungekuerzt zeigt. Eine Ellipse waere beim Kopieren vollstaendig, aber
    beim Ablesen eine stille Verstuemmelung - deshalb Umbruch, nicht
    Kappung."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text

    # Bindung an den Regelkoerper des eigenen Selektors (`.signal-grid
    # .key`), nicht an ein Stichwort-Fenster - sonst waere der Test auch
    # dann gruen, wenn `overflow-wrap` zufaellig in einer benachbarten,
    # unbeteiligten Regel stuende.
    start = css.index(".signal-grid .key {")
    open_brace = css.index("{", start)
    close_brace = css.index("}", open_brace)
    rule = css[open_brace:close_brace]

    assert "overflow-wrap: break-word" in rule
    assert "text-overflow" not in rule


async def test_both_boolean_columns_are_checkboxes(api):
    """Das Bedienelement folgt dem BEHAELTER, nicht der Bedeutung: in einer
    Tabelle Haekchen, weil sie in einer Spalte fluchten und leise bleiben -
    eine Spalte aus 17 Schiebeschaltern waere eine deutlich lautere Textur,
    und Lautstaerke ist genau das Problem dieses Modals."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    modal = page[page.index('class="signals-modal"') :]
    for handler in ("toggleExported(signal)", "toggleResend(signal)"):
        # Das Bedienelement, das den Handler traegt: vom Handler
        # rueckwaerts bis zum oeffnenden Tag. So prueft der Test das
        # tatsaechliche Element und nicht irgendein `type="checkbox"`
        # anderswo im Modal.
        end = modal.index(handler)
        element = modal[modal.rindex("<", 0, end) : end]
        assert 'type="checkbox"' in element, handler


async def test_the_boolean_columns_keep_a_label_for_assistive_technology(api):
    """Die Beschriftung steht als Spaltenkopf einmal statt siebzehnmal neben
    einem Kaestchen - ein Screenreader liest aber die Zeile, nicht die
    Tabelle. Beide Kaestchen brauchen deshalb weiterhin ihren eigenen Namen.

    Fund (Abschlusspruefung): die alte Fassung war asymmetrisch scharf -
    fuer die Export-Spalte prueft sie `:aria-label` direkt AM Element, fuer
    Resend genuegte eine lose Textsuche auf der ganzen Seite. Der
    Resend-Schluessel steht dort ohnehin dreimal (Kaestchen, versteckte
    Beschriftung fuer den schmalen Fall, Tooltip), ein geloeschtes
    `:aria-label` am Resend-Kaestchen selbst haette der Test also nie
    bemerkt. Diese Fassung bindet beide Spalten gleich scharf: vom Handler
    rueckwaerts zum Element, das ihn traegt - dasselbe Muster wie in
    `test_both_boolean_columns_are_checkboxes`."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    modal = page[page.index('class="signals-modal"') :]

    for handler, key in (
        ("toggleExported(signal)", "export_checkbox"),
        ("toggleResend(signal)", "resend_checkbox"),
    ):
        end = modal.index(handler)
        element = modal[modal.rindex("<", 0, end) : end]
        assert f":aria-label=\"t('web.signals.{key}')\"" in element, handler


async def test_the_resend_column_is_explained_once_above_the_table(api):
    """Fund (Abschlusspruefung): die alte Fassung pruefte nur, DASS der
    Schluessel irgendwo auf der Seite steht - weder "einmal" noch "ueber
    der Tabelle", obwohl der Name genau das verspricht. Der Erklaersatz
    koennte als Beschriftung neben jedem der siebzehn Kaestchen stehen -
    genau der Zustand, den der Umbau abgeschafft hat - und der alte Assert
    bliebe gruen, weil er nur Existenz sieht. Diese Fassung zaehlt die
    Treffer (genau einer) und bindet die Position an die Tabelle: der Satz
    muss vor dem Spaltenkopf stehen."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert page.count("web.signals.resend_explanation") == 1
    assert page.index("web.signals.resend_explanation") < page.index(
        'class="signal-grid signal-grid-head"'
    )


async def test_the_raw_write_field_lives_in_the_row_detail(api):
    """Nachfolger von `test_the_raw_write_strip_still_works_alongside_the_grid`
    (Aufgabe 7): der Streifen dort war ausdruecklich uebergangsweise, sein
    endgueltiger Platz ist der Aufklapper aus Aufgabe 8. Bedienlogik und
    Handler bleiben unveraendert, nur der Ort und die Sichtbarkeitsbedingung
    aendern sich - letztere jetzt als `isAttributeSignal(signal)`-Aufruf
    statt einem zweiten Mal ausgeschriebenem `signal.kind === 'attribute'`.
    `test_the_raw_write_field_is_no_longer_a_row_of_its_own` ergaenzt dazu
    die strukturelle Gegenprobe: dasselbe Feld liegt NICHT als eigenes
    Geschwister der Rasterzeile daneben."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    detail = dialog[dialog.index('class="signal-detail"') :]

    assert 'x-show="isAttributeSignal(signal)"' in detail
    assert ":placeholder=\"t('web.signals.raw_write_placeholder')\"" in detail
    assert '@input="rawWriteDrafts[signal.key] = $event.target.value"' in detail
    assert '@click="writeRaw(signal)"' in detail
    assert ':disabled="rawWriteBusyKey === signal.key"' in detail
    assert "x-text=\"t('web.signals.raw_write_submit')\"" in detail


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_only_one_signal_detail_is_open_at_a_time():
    """Anders als beim Kachel-Menue und den Signalgruppen lebt dieser
    Zustand in Alpine, nicht im DOM: es gibt genau EINEN Wert fuer das ganze
    Modal, kein Auf/Zu je Element.

    Fund (Abschlusspruefung): die alte Fassung prueft nur zwei
    Zeichenketten - dass `expandedSignalKey` mit `null` startet und dass im
    Rumpf von `toggleSignalDetails` IRGENDWO `this.expandedSignalKey = `
    vorkommt. Ein `toggleSignalDetails`, das zu `this.expandedSignalKey =
    signal.key` verkuerzt wuerde (ohne den Vergleich, der es zum echten
    Umschalter macht), oeffnete den Kebab nur noch und schloesse nie - der
    Kebab bliebe dann fuer immer offen, sobald man ihn einmal geklickt hat.
    Beide alten Asserts haetten das nicht bemerkt, weil sie nur Text sehen,
    kein Verhalten. Diese Fassung fuehrt `toggleSignalDetails` echt aus
    (ueber `_app_state`, wie beim Lade-Guard oben in dieser Datei) und
    prueft die drei Faelle, die den Umschalter ausmachen: zweimal auf
    dasselbe Signal (auf/zu), danach auf ein anderes Signal (Wechsel)."""
    values = _app_state(
        """
        const signalA = { key: "a" };
        const signalB = { key: "b" };
        const seen = [state.expandedSignalKey];
        state.toggleSignalDetails(signalA);
        seen.push(state.expandedSignalKey);
        state.toggleSignalDetails(signalA);
        seen.push(state.expandedSignalKey);
        state.toggleSignalDetails(signalA);
        seen.push(state.expandedSignalKey);
        state.toggleSignalDetails(signalB);
        seen.push(state.expandedSignalKey);
        console.log(JSON.stringify({ seen }));
        """
    )
    initial, after_first_a, after_second_a, after_third_a, after_b = values["seen"]

    assert initial is None
    assert after_first_a == "a"
    # Der zweite Klick auf DASSELBE Signal muss schliessen - genau die
    # Umschalt-Haelfte, an der eine blosse Zuweisung vorbeigeschummelt
    # haette.
    assert after_second_a is None, "ein zweiter Klick auf dasselbe Signal muss schliessen"
    assert after_third_a == "a"
    # Ein Klick auf ein ANDERES Signal wechselt, statt ein zweites zu
    # oeffnen - der Zustand ist ein einzelner Wert, kein Set, also hoechstens
    # eines gleichzeitig offen.
    assert after_b == "b"


async def test_closing_the_signals_modal_resets_the_open_detail(api):
    """Fund 3 (Nachpruefung, 2026-09-07): `@close="signalsModalDevice = null"`
    fasste `expandedSignalKey` bisher nicht an - schloss jemand das Modal
    mit offenem Aufklapper und oeffnete danach dasselbe Geraet erneut, stand
    der Aufklapper sofort wieder offen, ohne dass der Kebab dafuer geklickt
    wurde. `@close` ist laut dem Kommentar am `<dialog>` (index.html) der
    einzige Schliessweg, den ALLE Wege durchlaufen (Escape, Backdrop,
    Schliessen-Knopf, `close()` aus JavaScript ueber `closeSignalsModal()`)
    - der Reset gehoert deshalb genau dorthin, nicht an eine einzelne
    Schliessstelle."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    assert '@close="signalsModalDevice = null; expandedSignalKey = null"' in markup


async def test_the_raw_write_field_is_no_longer_a_row_of_its_own(api):
    """Fund 2 (Nachpruefung, 2026-09-07): dieser Test pruefte zuvor nur die
    Ab-/Anwesenheit einer Zeichenkette
    (`'x-show="signal.kind === \\'attribute\\'"' not in page`) - keine
    Struktur, obwohl der Name "keine eigene Zeile mehr" verspricht. Diese
    Fassung sichert das strukturell zu: das Rohwert-Eingabefeld liegt
    INNERHALB des `.signal-detail`-Bereichs, nicht als eigenes Geschwister
    der Rasterzeile (`.signal-grid.signal-row-cells`) daneben.

    Fund (Abschlusspruefung): das zweite Pruefenster war `dialog[detail_
    start:]` - nach HINTEN offen bis zum Ende des ganzen Dialogs, nicht auf
    das Ende von `.signal-detail` begrenzt. Ein ZUSAETZLICH zwischen
    Rasterzeile und Aufklapper eingefuegtes Rohwertfeld - die alte
    Fehlerposition aus Aufgabe 7 - verletzte damit keine der beiden
    Zusicherungen: das erste Fenster reichte nur bis zum eigenen `</div>`
    der Rasterzeile (das neue Feld liegt DAHINTER), das zweite begann erst
    bei `class="signal-detail"` (das neue Feld liegt DAVOR) - die Luecke
    dazwischen sah keines der beiden Fenster.

    Diese Fassung schliesst die Luecke, indem das erste Fenster bis
    `detail_start` reicht statt nur bis zum eigenen `</div>` der
    Rasterzeile - alles zwischen Rasterzeile und Aufklapper zaehlt jetzt
    als "darf das Feld nicht enthalten". Das zweite Fenster wird zusaetzlich
    ans eigene `</div>` von `.signal-detail` gekappt (Klammertiefe zaehlen,
    Muster aus `test_the_signal_table_stacks_on_a_narrow_screen`, noetig
    wegen des verschachtelten `<div class="row">` darin) statt offen bis
    zum Dialogende zu lesen - sonst haette JEDE spaetere Fundstelle im Rest
    des Dialogs die Zusicherung erfuellt, selbst wenn das Feld aus
    `.signal-detail` HERAUSgewandert waere.

    Kein reiner Wiederholung von `test_the_raw_write_field_lives_in_the_
    row_detail`: jener Test prueft nur Anwesenheit im (offenen)
    Aufklapper-Fenster, dieser hier ergaenzt dazu die Abwesenheit
    zwischen Rasterzeile und Aufklapper UND die scharfe obere Grenze -
    beide Haelften zusammen sichern "kein eigenes Geschwister mehr" zu."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))

    row_start = dialog.index('class="signal-grid signal-row-cells"')
    detail_start = dialog.index('class="signal-detail"', row_start)
    # Nicht nur die Rasterzeile selbst, sondern ALLES bis zum Aufklapper -
    # die alte Fehlerposition war ein Rohwertfeld als eigenes Geschwister
    # GENAU dazwischen, und ein bei der Rasterzeile endendes Fenster haette
    # das nicht gesehen.
    assert "raw_write_placeholder" not in dialog[row_start:detail_start]

    # Auf das eigene </div> von .signal-detail kappen statt offen bis zum
    # Dialogende zu lesen. Innerhalb liegt ein verschachteltes
    # <div class="row"> (das Rohwertfeld selbst) - deshalb Klammertiefe
    # zaehlen, nicht einfach den naechsten </div> nehmen.
    open_tag_start = dialog.rindex("<div", 0, detail_start)
    body_start = dialog.index(">", open_tag_start) + 1
    depth = 1
    pos = body_start
    for match in re.finditer(r"<div\b|</div>", dialog[body_start:]):
        pos = body_start + match.end()
        if match.group() == "</div>":
            depth -= 1
            if depth == 0:
                break
        else:
            depth += 1
    assert depth == 0, "kein schliessendes </div> fuer .signal-detail gefunden"
    detail = dialog[detail_start:pos]

    assert "raw_write_placeholder" in detail


async def test_the_detail_spells_out_the_path(api):
    """Der Pfad `1/59/1` bekommt endlich einen Ort, an dem genug Platz ist,
    ihn auszuschreiben, statt ihn als Raetsel neben den Namen zu stellen.

    Fund 1 (Nachpruefung, 2026-09-07): die Zerlegung des Pfads
    (`signal.path.split('/')[2]` fuer das Element) stand zuvor als Ausdruck
    mitten im Markup - Wissen ueber das Pfadformat, das bei einer Aenderung
    des Formats im Markup gesucht werden muesste statt an einer Stelle in
    `app.js`. Ein Test, der nur `GET /` prueft, haette einen Helfer in
    `app.js` nicht gesehen - dieser Test prueft deshalb wie
    `test_only_one_signal_detail_is_open_at_a_time` direkt `app.js` und
    zusaetzlich die Bindung im Markup."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    page = _without_comments((await client.get("/")).text)

    start = script.index("signalOriginText(signal) {")
    body = script[start:][:300]
    assert 't("web.signals.origin"' in body
    assert "signal.endpoint" in body
    assert "signal.cluster_id" in body
    assert 'signal.path.split("/")[2]' in body

    assert 'x-text="signalOriginText(signal)"' in page


async def test_the_row_kebab_is_the_only_thing_left_in_the_28px_column(api):
    """Pflichtergebnis aus der Pruefung von Aufgabe 7: die Warnpille mit
    `signal.reason` sass in der 28-px-Rasterspalte und brach dort bei jedem
    Geraet mit einem TEXT- oder listwertigen Signal um (`.badge` hatte kein
    `overflow-wrap`, `.signal-grid > * { min-width: 0 }` liess die Zelle
    schrumpfen) - gemessen 744px `scrollWidth` gegen 719px `clientWidth`.
    Die Pille wohnt jetzt im Aufklapper; die Rasterzeile traegt in ihrer
    letzten Zelle nur noch den Kebab-Knopf.

    Fund (Abschlusspruefung): die alte Fassung prueft nur die Abwesenheit
    ZWEIER konkreter Zeichenketten (die Warnpille, `signal.reason`) - der
    Name verspricht aber "nur der Kebab", nicht "diese zwei Dinge nicht".
    Ein beliebiges DRITTES Element in der letzten Zelle (ein neues Badge,
    ein Icon, ein zweiter Knopf) waere unbemerkt geblieben. Diese Fassung
    schneidet die letzte Zelle exakt: alles nach der Resend-Zelle (dem
    letzten bekannten Geschwister davor) bis zum Ende der Rasterzeile muss
    GENAU ein Element sein, und das muss der Kebab-Knopf sein."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))

    # Innerhalb der Rasterzeile steht kein verschachteltes `<div>` (Zellen
    # sind `<label>`, `<input>`, `<span>`, `<button>`) - das naechste
    # `</div>` schliesst also genau diese Zeile, nicht irgendein Kindelement.
    row_start = dialog.index('class="signal-grid signal-row-cells"')
    row_end = dialog.index("</div>", row_start)
    row = dialog[row_start:row_end]

    assert "signal-more" in row

    # Von der Resend-Zelle (letztes bekanntes Geschwister vor der letzten
    # Spalte) bis zum Ende der Zeile darf NUR noch der Kebab-Knopf stehen -
    # keine Warnpille, kein sonstiges Element, egal wie es heisst.
    resend_close = row.index("</label>", row.index("toggleResend(signal)"))
    tail = row[resend_close + len("</label>") :]
    assert tail.lstrip().startswith("<button"), tail
    button_close = tail.index("</button>") + len("</button>")
    assert tail[:button_close].count('class="signal-more"') == 1
    assert tail[button_close:].strip() == "", tail[button_close:]


async def test_the_pill_fix_sits_on_the_pill_not_on_the_container(api):
    """Fund (Nachpruefung vor dem Merge): ein frueherer Anlauf loeste die
    gestreckte Warnpille (`.badge.warn`, `signal.reason`) mit `align-items:
    flex-start` an `.signal-detail` selbst - das trifft aber ALLE Kinder,
    nicht nur die Pille. Eines davon ist `.row`, das Rohwert-Feld
    (`input[type="text"]`) darunter: unter `flex-start` verliert auch DAS
    seine gestreckte Breite und faellt auf sein `min-width: 12rem` zurueck.
    Gemessen im Wegwerf-Harness (echtes `index.html`/`style.css`, Signal
    mit `exportable: false` und Text aus `_UNEXPORTABLE_REASONS`): `.row`
    schrumpft von voller Aufklapperbreite (695.6px) auf 406.6px, das Feld
    darin von rund 630px auf den 192px-Boden.

    Ein Test kann diese Breiten nicht nachmessen (kein Browser-Layout in
    der Testsuite) - er kann aber festhalten, WO die Loesung sitzen muss:
    an der Pille selbst (`align-self: flex-start`), nicht am Container.
    `.signal-detail` bleibt bei der Vorgabe `stretch` (kein eigenes
    `align-items`), sonst waeren wir wieder beim Container-Fix und `.row`
    schrumpfte erneut - genau das soll dieser Test sperren."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text

    container_start = css.index(".signal-detail {")
    open_brace = css.index("{", container_start)
    close_brace = css.index("}", open_brace)
    container_body = css[open_brace:close_brace]
    # Der Regelkoerper traegt selbst einen Kommentar, der zur Begruendung
    # `align-items: flex-start` als Zitat nennt (genau der verworfene
    # Ansatz) - erst die Kommentare raus, sonst faende die blosse
    # Zeichenkettensuche ihr eigenes Zitat und der Test waere nie rot.
    container_declarations = re.sub(r"/\*.*?\*/", "", container_body, flags=re.DOTALL)
    assert "align-items" not in container_declarations, (
        "align-items am Container trifft auch .row (Rohwert-Feld) mit, nicht nur die Pille"
    )

    # Der Selektor, der `align-self: flex-start` tatsaechlich traegt - nicht
    # per `css.index(".signal-detail .badge {")` gesucht, das faende die
    # FALSCHE der beiden gleichlautenden Selektorzeilen (die andere gehoert
    # zur `margin: 0`-Regel fuer `.hint` UND `.badge` zusammen). Stattdessen
    # von der Deklaration selbst rueckwaerts zu ihrem eigenen Selektor.
    align_self_pos = css.index("align-self: flex-start;")
    badge_open_brace = css.rindex("{", 0, align_self_pos)
    prev_close_brace = css.rindex("}", 0, badge_open_brace)
    # Der Begruendungskommentar vor der Regel steht in derselben Luecke -
    # raus damit, sonst bleibt vom Selektor nicht die letzte, sondern die
    # erste (Kommentar-)Zeile uebrig.
    preceding = re.sub(
        r"/\*.*?\*/", "", css[prev_close_brace + 1 : badge_open_brace], flags=re.DOTALL
    )
    selector = preceding.strip()
    assert selector == ".signal-detail .badge"


async def test_the_kebab_button_is_named_and_reports_its_state(api):
    """Fund (Abschlusspruefung): die alte Fassung prueft nur, DASS
    `:aria-expanded=` und `:aria-label=` als Attributnamen vorkommen - nicht
    WORAN sie haengen. Ein `:aria-expanded="true"` (an eine Konstante
    gebunden, meldet also immer denselben Zustand) waere gruen geblieben.
    Diese Fassung bindet beide an den konkreten Ausdruck, den der Knopf
    tatsaechlich tragen muss."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))

    button_start = dialog.index('class="signal-more"')
    button_tag_start = dialog.rindex("<button", 0, button_start)
    button_tag_end = dialog.index(">", button_start)
    button = dialog[button_tag_start:button_tag_end]

    assert ':aria-expanded="expandedSignalKey === signal.key"' in button
    assert ":aria-label=\"t('web.signals.row_details')\"" in button


async def test_the_modal_leads_with_the_number_the_user_came_for(api):
    """Man oeffnet dieses Modal, um zu sehen und zu aendern, was nach Loxone
    geht. Diese Zahl stand bisher nirgends.

    Fund (Abschlusspruefung): die alte Fassung prueft weder POSITION
    ("leads with") noch den NENNER (`total:`) - die Zusammenfassung haette
    ans Ende des Modals rutschen oder den falschen Nenner zeigen koennen,
    ohne dass der Test das bemerkt haette. Diese Fassung bindet die Zahl an
    ihre Position vor dem Spaltenkopf und sichert beide Haelften des
    Bruchs einzeln zu.

    Entscheidung (bewusst, nicht aendern): der Nenner zaehlt ALLE Signale
    ueber `signalCount`, nicht nur die funktionalen - am Taster steht
    "17 von 173", nicht "12 von 17" wie im Entwurf skizziert. Beides ist
    wahr; der Code zaehlt bewusst alle, weil das Modal auch die
    Expertengruppe fuehrt und ein freigeschaltetes Expertensignal wirklich
    mitgehen soll."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert "web.signals.export_summary" in page
    assert "exported: exportedSignalCount(signalsModalDevice)" in page
    assert "total: signalCount(signalsModalDevice)" in page
    assert page.index("web.signals.export_summary") < page.index(
        'class="signal-grid signal-grid-head"'
    )


async def test_the_deselect_all_button_calls_the_correct_function_with_the_device_id(api):
    """Die Kopfzeile zeigt die Exportzahl und daneben einen "Alle abwaehlen"-
    Knopf (Task 9, Entwurf 2026-09-07, Abschnitt 4.2). Ein fehlerhafter
    Funktionsaufruf (vertauschte Variable, falsche Methode, vergessenes
    Argument) bliebe im Text-Test gruen - der Knopf selbst laeuft nie, ohne
    Browser-Engine naemlich nur sein Markup. Belegt wird deshalb, dass die
    ausgelieferte Datei die korrekte Bindung traegt: der Aufruf heisst
    `deselectAllSignals(signalsModalDevice)` (das Geraet-Argument nicht
    vergessen, nicht vertauscht) und die Beschriftung kommt aus dem
    Uebersetzungsschluessel `web.signals.deselect_all`.

    Ein eigener Test, weil er eine andere Frage als
    `test_the_modal_leads_with_the_number_the_user_came_for` beantwortet:
    "haengt der Knopf am richtigen Aufruf" vs. "steht die Zahl im Kopf"."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))

    # Die `signals-summary` Div findet, in der der Knopf sitzt.
    summary_start = dialog.index('class="signals-summary"')
    summary_section_end = dialog.index("</div>", summary_start)
    summary_section = dialog[summary_start:summary_section_end]

    # Den "Alle abwaehlen"-Knopf selbst schneiden, nicht die ganze Div.
    button_text_idx = summary_section.index("deselectAllSignals")
    button_start = summary_section.rindex("<button", 0, button_text_idx)
    button_end = summary_section.index("</button>", button_text_idx)
    button = summary_section[button_start:button_end]

    assert '@click="deselectAllSignals(signalsModalDevice)"' in button
    assert "x-text=\"t('web.signals.deselect_all')\"" in button


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_deselect_all_empties_the_selection_instead_of_inverting_it():
    """Ein `toggleExported` ueber ALLE Signale haette die Auswahl invertiert -
    der Knopf heisst aber "alle abwaehlen", nicht "umkehren". Ein zweiter
    Klick muss deshalb nichts mehr tun.

    `toggleExported` wird hier ersetzt, weil es die Route ruft: geprueft
    wird die Auswahl-Regel dieser Schleife, nicht der Schreibweg."""
    values = _app_state(
        """
        state.signalsByDevice = { 1: [
          { key: "a", exported: true, exportable: true },
          { key: "b", exported: false, exportable: true },
          { key: "c", exported: true, exportable: false },
        ] };
        const touched = [];
        state.toggleExported = (signal) => {
          touched.push(signal.key);
          signal.exported = !signal.exported;
        };
        const before = state.exportedSignalCount(1);
        // Async-IIFE, weil `node -e` als CommonJS laeuft und dort kein
        // `await` auf oberster Ebene erlaubt ist - `deselectAllSignals`
        // ist async.
        (async () => {
          await state.deselectAllSignals(1);
          const firstRun = touched.slice();
          await state.deselectAllSignals(1);
          console.log(JSON.stringify({
            before,
            after: state.exportedSignalCount(1),
            total: state.signalCount(1),
            firstRun,
            secondRunTouched: touched.length - firstRun.length,
          }));
        })();
        """
    )

    # "c" ist zwar `exported`, passt aber auf keinen Loxone-Eingang - es
    # zaehlt nicht mit, genauso wie `to_inputs` es server-seitig auslaesst.
    assert values["before"] == 1
    assert values["total"] == 3
    assert values["after"] == 0
    # "b" war bereits aus und darf nicht angefasst worden sein.
    assert "b" not in values["firstRun"]
    assert values["secondRunTouched"] == 0


async def test_the_signal_table_stacks_on_a_narrow_screen(api):
    """Sechs Spalten passen unter etwa 640 px nicht. Ohne diesen Umbruch
    franst die Tabelle dort wieder aus - also genau der Zustand, den der
    ganze Umbau beseitigt hat, nur auf einem Telefon.

    Prueft nicht nur, DASS "display: none" irgendwo im Umkreis der
    Medienabfrage steht, sondern dass es im REGELKOERPER von
    `.signal-grid-head` selbst steht: ein Stichwort-Fenster von 900
    Zeichen waere auch dann gruen, wenn ein CSS den Spaltenkopf gar
    nicht ausblendet, weil "display: none" zufaellig in einer
    benachbarten, unbeteiligten Regel auftaucht (diese Fassung tut das
    tatsaechlich - die Kaestchen-Spalten selbst tragen `display: flex`,
    nicht `none`, das koennte man aber nicht am blossen Fenster
    ablesen). Diese Fassung bindet jede erwartete Deklaration an ihren
    eigenen Selektor."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text

    assert "@media (max-width: 640px)" in css
    media_start = css.index("@media (max-width: 640px)")
    body_start = css.index("{", media_start) + 1

    # Klammertiefe zaehlen statt eine feste Zeichenzahl zu raten - liefert
    # exakt den Koerper der Medienabfrage, unabhaengig davon, wie lang die
    # Regeln darin sind oder in welcher Reihenfolge sie stehen.
    depth = 1
    i = body_start
    while depth:
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
        i += 1
    media_body = css[body_start : i - 1]

    def rule_body(selector: str) -> str:
        start = media_body.index(selector)
        open_brace = media_body.index("{", start)
        close_brace = media_body.index("}", open_brace)
        return media_body[open_brace:close_brace]

    # Der Spaltenkopf verschwindet: ueber einer gestapelten Karte
    # beschriftet er nichts mehr.
    assert "display: none" in rule_body(".signal-grid-head")
    # Die Zeile wird zur gestapelten Karte statt der sechs festen Spalten.
    assert "grid-template-columns: auto minmax(0, 1fr)" in rule_body(".signal-grid {")
    # Die beiden Haekchenspalten bleiben sichtbar und bedienbar - sie
    # verschwinden nicht mit dem Kopf, sie ruecken nur linksbuendig.
    assert "justify-content: flex-start" in rule_body(".signal-grid .col-center")


async def test_the_checkbox_labels_become_visible_only_below_640px(api):
    """Pruefungsfund: der Kommentar ueber der Medienabfrage behauptete,
    `title` erscheine dort als sichtbarer Text neben dem Kaestchen - es
    gab aber nirgends ein `content: attr(title)` o.ae., `title` blieb ein
    reiner Hover-Tooltip, den ein Touch-Nutzer nie zu sehen bekommt. Der
    Fix zeigt echten Text: ein `<span class="col-center-label">` mit
    demselben Uebersetzungsschluessel wie das `aria-label` des Kaestchens,
    per Grundregel verborgen und nur innerhalb der 640px-Medienabfrage
    gezeigt - umgekehrt zum Spaltenkopf, der genau dort verschwindet."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    css = (await client.get("/static/style.css")).text

    # Beide Labels tragen den Span mit demselben Schluessel wie ihr
    # `aria-label`, innerhalb desselben `<label>` wie das Kaestchen - kein
    # neuer Uebersetzungsschluessel, dieselbe Auskunft an einem zweiten Ort.
    for key in ("export_checkbox", "resend_checkbox"):
        label_start = dialog.index(f"aria-label=\"t('web.signals.{key}')\"")
        label_start = dialog.rindex("<label", 0, label_start)
        label_end = dialog.index("</label>", label_start)
        label = dialog[label_start:label_end]
        assert 'class="col-center-label"' in label
        assert 'aria-hidden="true"' in label
        assert f"x-text=\"t('web.signals.{key}')\"" in label

    # Grundregel: verborgen, solange der Spaltenkopf beschriftet.
    base_start = css.index(".signal-grid .col-center-label")
    media_start = css.index("@media (max-width: 640px)")
    assert base_start < media_start
    base_open = css.index("{", base_start)
    base_close = css.index("}", base_open)
    assert "display: none" in css[base_open:base_close]

    # Medienabfrage: hier gezeigt, an derselben Klammertiefe wie die
    # Nachbarregeln - Muster aus
    # `test_the_signal_table_stacks_on_a_narrow_screen`.
    body_start = css.index("{", media_start) + 1
    depth = 1
    i = body_start
    while depth:
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
        i += 1
    media_body = css[body_start : i - 1]

    label_start = media_body.index(".signal-grid .col-center-label")
    label_open = media_body.index("{", label_start)
    label_close = media_body.index("}", label_open)
    assert "display: inline" in media_body[label_open:label_close]


async def test_the_value_grid_now_carries_every_functional_signal(api):
    """Entwurf 2026-09-07, Abschnitt 2: der herausgehobene Leitwert
    entfaellt, alle funktionalen Signale stehen gleichrangig im
    Werteraster. `restSignalsFor` lieferte die Kurzliste OHNE ihren ersten
    Eintrag - genau der stand oben in der Kopfzeile. Mit dem Wegfall der
    Kopfzeilen-Anzeige muss das Raster wieder ueber die volle Liste
    laufen, sonst verschwaende das erste Signal ersatzlos."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert 'x-for="signal in firstSignalsFor(device.id)"' in markup
    assert "restSignalsFor(" not in markup


async def test_the_tile_header_no_longer_carries_a_lead_value(api):
    """Weder die Klassen noch der Aufruf duerfen ausgeliefert werden. Der
    Test laeuft ueber `_without_comments`, weil die Begruendung im Markup
    den Leitwert weiterhin beim Namen nennt - und zwar gerade, um zu
    erklaeren, warum er dort nicht mehr steht."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "lead-value" not in markup
    assert "lead-label" not in markup
    assert "leadSignalFor(" not in markup


async def test_the_offline_pill_sits_in_the_header_not_under_the_name(api):
    """Die Pille rueckt auf den Platz des Leitwerts: drittes Kind von
    `.device-head`, nicht mehr Kind von `.device-ident` unter dem Namen
    (Entwurf, Abschnitt 5). Keine eigene Positionsregel noetig: `.device-
    ident` traegt `flex: 1 1 auto` und verbraucht den freien Platz in der
    Flex-Kopfzeile, damit steht die Pille rechts - ohne dass ihr eigenes
    `margin-left: auto` (style.css) dabei etwas beitraegt (Nachtrag
    2026-09-07, siehe Spec Abschnitt 5).

    Belegt wird die Verschachtelung ueber die Reihenfolge im
    ausgelieferten Markup: zwischen dem Namensfeld und der Pille MUSS ein
    schliessendes `</span>` liegen - das von `.device-ident`. Steht die
    Pille noch drin, fehlt es."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    name_end = markup.index('@change="saveLabel(device)"')
    pill = markup.index('<span class="status-pill off"', name_end)
    assert "</span>" in markup[name_end:pill], (
        "die Offline-Pille steht noch innerhalb von `.device-ident`"
    )


async def test_the_missing_signals_hint_no_longer_asks_for_a_lead(api):
    """Der Hinweis unterscheidet "geladen, aber leer" von "laedt noch"
    (Spec 8.1). Sein Aufhaenger war `!leadSignalFor(device.id)`; ohne
    Leitwert fragt er die Liste direkt."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert (
        'x-show="signalsByDevice[device.id] && functionalSignalsFor(device.id).length === 0"'
    ) in markup


async def test_the_lead_helpers_are_gone_from_the_script(api):
    """Entwurf 2026-09-07, Abschnitt 10: beide Methoden entfallen
    ersatzlos, nachdem das Markup sie nicht mehr aufruft. Eine ungenutzte
    Methode in `app.js` ist kein harmloser Rest - sie laedt den naechsten
    Umbau dazu ein, den Leitwert wieder einzufuehren, ohne den Entwurf
    gelesen zu haben."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    # Auf die DEFINITION ankern, nicht auf den blossen Namen: der Kommentar
    # an `signalIsFresh` nennt `leadSignalFor` weiterhin - und zwar gerade,
    # um zu erklaeren, warum dessen Null-Duldsamkeit stehen bleibt, obwohl
    # der Aufrufer weg ist. Anders als beim Markup gibt es fuer `app.js`
    # keinen `_without_comments`-Helfer.
    assert "leadSignalFor(deviceId) {" not in script
    assert "restSignalsFor(deviceId) {" not in script
    assert "this.firstSignalsFor(deviceId).slice(1)" not in script


async def test_the_lead_rules_are_gone_from_the_stylesheet(api):
    """Entwurf 2026-09-07, Abschnitt 10. Beide Klassen stehen in keinem
    Markup mehr; ihre Regeln - samt der langen Begruendung zu
    `flex: 0 0 auto` gegen `flex: 0 1 auto` und zum `padding-block` fuer
    Unterlaengen - beschreiben ein Element, das es nicht mehr gibt."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    # Auf den Selektor mit oeffnender Klammer ankern, nicht auf den blossen
    # Klassennamen: der Kommentar an `.device-head .device-name` nennt
    # `.lead-value` weiterhin - er erklaert, warum der Name dort frueher nur
    # 65 px bekam. Ein Stylesheet hat keinen `_without_comments`-Helfer.
    assert ".lead-value {" not in css
    assert ".lead-value small {" not in css
    assert ".lead-label {" not in css


async def test_the_control_modal_is_delivered(api):
    """Belegt NUR die Auslieferung. Ob die Alpine-Ausdruecke darin
    tatsaechlich binden, kann dieser Test nicht sagen - das prueft der
    Browser-Durchgang in einer spaeteren Aufgabe."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "openControlModal" in script
    assert "readStartValues" in script
    assert "controlsByKind" in script
    assert "sendControl" in script

    page = (await client.get("/")).text
    assert 'x-ref="controlModal"' in page
    assert '@close="controlModalDevice = null"' in page
    assert "openControlModal(device)" in page
    # Deviation vom Aufgaben-Brief (Projektentscheidung): der Prozent-Regler
    # traegt seine Beschriftung ueber `command.slug`, nicht ueber einen
    # festen Uebersetzungsschluessel - die eingecheckte Testleuchte
    # (`ikea_kajplats_cws_lamp.json`) hat zwei Prozent-Kommandos, die sich
    # sonst nicht unterscheiden liessen.
    assert "web.devices.control_brightness" not in page
    assert "web.devices.control_brightness" not in script


async def test_the_checkbox_hit_targets_reach_24px(api):
    """WCAG 2.2 AA, Erfolgskriterium 2.5.8 (Target Size Minimum): 24x24
    CSS-Pixel Trefferflaeche. Eine Pruefung fand ein Kaestchen mit
    gemessen 13 px Breite auf einem schmalen Fenster - der nackte
    `<input type="checkbox">` steht in `style.css` ohne eigene Regel auf
    reinem Browser-Standard.

    Dieser Test kann NICHT selbst messen (keine Layout-Engine hier) -
    das Ausmessen lief in einem Wegwerf-Harness ausserhalb des Repos
    (echtes Markup aus `index.html` herausgeschnitten, echtes `style.css`,
    ueber http mit Alpine geladen, `label.getBoundingClientRect()`
    gemessen, nicht die des `<input>`). Ergebnis vorher/nachher (Desktop
    px, unter 640 px identisch fuer die vier Text-Label-Zeilen, da sie an
    keiner Medienabfrage haengen):
      - projectSync.includeNewDevices: 204x21.5 -> 200x24
      - exportIncludeSystem:           208x21.5 -> 204x24
      - exportOnlyPending:             131x21.5 -> 128x24
      - hideNoise:                     166x21.5 -> 162x24
      - Signal-Modal Export-Spalte:     58x19   ->  58x24 (Desktop),
                                         99x21   ->  99x24 (< 640 px)
      - Signal-Modal Periodisch-Spalte: 76x19   ->  76x24 (Desktop),
                                        193x21   -> 193x24 (< 640 px)
    Alle sechs waren waagerecht schon VORHER ueber 24 px - vier durch den
    Text neben dem Kaestchen, die beiden Signal-Modal-Spalten durch die
    feste Spaltenbreite aus `grid-template-columns` (Desktop) bzw. den
    sichtbaren `.col-center-label`-Text (< 640 px). Nur die Hoehe fehlte,
    darum setzen beide Regeln unten NUR `min-height`, kein `min-width` -
    eine wirkungslose Regel waere Ballast. Das Signal-Modal-Raster blieb
    dabei fluchtend (linke Kantenabweichung weiterhin 0 gegen den Kopf,
    siehe `test_the_signal_rows_and_the_header_share_one_grid`) und ohne
    waagerechten Ueberlauf (`scrollWidth - clientWidth` weiterhin 0), in
    beiden Breiten.

    Die Loesung sitzt am `<label>`, nicht am `<input>`: ein Label, das
    sein Kaestchen umschliesst, leitet die Aktivierung von jeder Stelle
    seiner Flaeche weiter - es IST also schon die Trefferflaeche, ihr
    fehlte nur das Mindestmass. Ein groesseres Kaestchen saehe neben dem
    14-px-Text klobig aus und haette das Aussehen der ganzen Oberflaeche
    veraendert."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    css = (await client.get("/static/style.css")).text

    # Die vier Text-Label-Kaestchen tragen jetzt `checkbox-label` - nicht
    # `.row label` allgemein, denn dieselbe Zeilenklasse traegt anderswo
    # auch Textfeld-, Datei- und Auswahllabels (z. B. die Bruecken-IP),
    # deren Layout hier nicht mitgezogen werden soll. `:has()` bewusst
    # vermieden (Projekt-Vorgabe).
    for model in (
        'x-model="projectSync.includeNewDevices"',
        'x-model="exportIncludeSystem"',
        'x-model="exportOnlyPending"',
        'x-model="hideNoise"',
    ):
        at = page.index(model)
        label_start = page.rindex("<label", 0, at)
        label_end = page.index(">", label_start)
        opening_tag = page[label_start:label_end]
        assert 'class="checkbox-label"' in opening_tag, model

    # Bindung an den Regelkoerper des eigenen Selektors, nicht an ein
    # Stichwort-Fenster - siehe `test_the_key_pill_wraps_instead_of_
    # touching_the_value_column` fuer dasselbe Muster.
    start = css.index(".checkbox-label {")
    open_brace = css.index("{", start)
    close_brace = css.index("}", open_brace)
    checkbox_label_rule = css[open_brace:close_brace]
    assert "min-height: 24px" in checkbox_label_rule
    assert "align-items: center" in checkbox_label_rule
    # Kein `min-width`: der Text neben dem Kaestchen traegt die Breite
    # schon laengst ueber 24 px, siehe Docstring-Messung oben.
    assert "min-width" not in checkbox_label_rule

    # `.signal-grid .col-center` traegt zusaetzlich eine Regel INNERHALB
    # der 640-px-Medienabfrage (justify-content, gap) - `css.index` findet
    # die ERSTE, die ausserhalb liegende Basisregel, in der `min-height`
    # jetzt stehen muss.
    media_start = css.index("@media (max-width: 640px)")
    start = css.index(".signal-grid .col-center {")
    assert start < media_start
    open_brace = css.index("{", start)
    close_brace = css.index("}", open_brace)
    col_center_rule = css[open_brace:close_brace]
    assert "min-height: 24px" in col_center_rule
    assert "align-items: center" in col_center_rule
    assert "min-width" not in col_center_rule
