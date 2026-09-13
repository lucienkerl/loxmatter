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
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.api.diagnostics import FABRIC_BACKUP_NAME
from loxmatter.api.export import ARCHIVE_NAME

# The private helper on purpose, not a second dict comprehension over the
# same keys: the tests at the end of this file run the shipped `app.js`
# with the table the browser really gets from `GET /api/i18n`, and a local
# copy would keep agreeing with itself if that endpoint ever changed.
from loxmatter.api.language import _web_strings
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store
from loxmatter.zigbee.source import PERMIT_MAX_SECONDS

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
    store.register_commands(device_id, extract_commands(snapshot))

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
    """An unclosed CSS rule swallows EVERYTHING that follows it -
    with no error message, with no test noticing.

    That is exactly what happened when merging main (2026-09-08): a
    conflict marker cut right through `.signals-summary`, and resolving it
    took the closing brace along with it, so the entire stylesheet was
    dead from that line on. The signal modal fell back to the state
    the whole rework had eliminated: no grid, ragged
    rows, the column header as flowing text.

    The test suite stayed fully green throughout. It cannot even see
    this: every CSS assertion in this file searches for strings in
    the shipped file, and the strings were indeed all still
    in there - just in dead text. It only surfaced on a newly
    generated screenshot.

    This test is the cheapest safeguard against that: it does not
    understand CSS, it only counts. Comments are stripped first, because
    `{` and `}` are allowed to appear inside them (and appear plenty in
    this stylesheet, which is densely commented)."""
    css = (WEB_DIR / "style.css").read_text(encoding="utf-8")
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

    depth = 0
    for number, line in enumerate(without_comments.split("\n"), start=1):
        depth += line.count("{") - line.count("}")
        assert depth >= 0, f"one brace too many closed, line {number}"

    assert depth == 0, f"{depth} rule(s) not closed - everything after that is dead"


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

    The block now carries sixteen `<symbol>` definitions, eight of them
    from the device-tab rework (design 2026-09-05, section 6.5) - none
    of them had run through a parser before. A single wrong
    dash or an unclosed tag in a new symbol would therefore only have
    surfaced in the browser, and even there only as an empty area,
    never as a message."""
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

    Task 12: the two group titles have carried `t(...)` instead of
    fixed German literals ever since - see
    `test_the_signal_group_titles_are_translated` for the binding itself;
    what remains here is only the proof that the grouping
    (`signal.functional`) is unchanged.

    Task 6: the single "Functional" group gave way to endpoint groups (see
    `test_the_groups_follow_the_ranking_not_the_endpoint_number` for their
    order and content). The `group_functional` key no longer
    exists since then; the original assertion of this test -
    group titles via `t(...)` instead of hardcoded - remains valid,
    but now targets the endpoint subtitle."""
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

    Task 7 replaces the earlier `x-show="signal.exportable"` on the
    enclosing `<label>` with `:disabled="!signal.exportable"` on the
    `<input>` itself: the grid row (`.signal-grid`) needs the same
    number of cells in EVERY row, otherwise the columns of the
    non-exportable signals shift against the header row - exactly the
    alignment this task guarantees. A disabled rather than hidden
    checkbox covers the same case: `resend_marked()` can never have any
    effect for a non-exportable signal anyway (`_last_values`
    stays empty there, see `Runtime._cache_attribute`), it just can no
    longer be clicked."""
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


async def test_the_system_view_shows_the_running_version(api):
    """The version card (task "pull the image") fetches
    `GET /api/version` and binds the result to four text modules -
    the same proof as the other markup tests in this file: only that
    markup and script are delivered, not that Alpine fills them correctly at
    runtime. A typo in `versionInfo` in one of the
    two files otherwise delivers a blank card permanently, with
    no test noticing it."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert 'versionInfo = await this.request("GET", "/api/version")' in script
    assert "t('web.system.version_running', { version: versionInfo.version })" in page
    assert "t('web.system.version_commit', { commit: versionInfo.commit })" in page
    assert "t('web.system.version_built_at', { built_at: versionInfo.built_at })" in page


async def test_the_system_view_shows_when_the_updater_sidecar_is_behind(api):
    """The updater sidecar no longer replaces its own container after a
    successful update (removed - see the incident recorded in
    update-once.sh's own comment, near the end of the success branch:
    measured on the maintainer's Pi to corrupt its own container instead
    of updating it). Without that, a sidecar can silently drift behind the
    bridge it serves - this banner (`updaterVersionBehind()` in app.js) is
    the replacement signal, and this is the same kind of proof as
    `test_the_system_view_shows_the_running_version` right above: the
    markup and the binding are actually delivered, not that Alpine renders
    them correctly at runtime."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert "updaterVersionBehind()" in script
    assert "updaterRefreshMessage()" in script
    assert 'x-show="updaterVersionBehind()"' in page
    assert 'x-text="updaterRefreshMessage()"' in page


async def test_the_update_card_offers_its_four_states_and_the_confirmation(api):
    """Task 9 (design "Applying updates through the web UI", 2026-09-08,
    section 9): the four states plus the confirmation step all live in the
    same card as the version block above, keyed off `updateStatus` and
    `updateAvailable` - same kind of proof as
    `test_the_system_view_shows_the_running_version`: that the markup and
    the bindings are actually delivered, not that Alpine renders them
    correctly at runtime (that is the manual browser check).

    Also covers the one thing the brief's own Step 5 sample never renders:
    a failed `applyUpdate()`/`loadUpdateStatus()` sets `updateError`, and
    without a binding for it the card would silently swallow a 409/503
    from `/api/update/apply` - see `api/update.py`'s module docstring for
    why that message is worth showing verbatim."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text

    # State 0 (no sidecar) and state 1 (ready).
    assert 'x-show="updateStatus && !updateStatus.updater_present && !updateRunning()"' in page
    assert "t('web.system.update_no_updater')" in page
    assert (
        'x-if="updateStatus?.updater_present && !updateRunning() && updateAvailable?.target"'
        in page
    )
    assert "t('web.system.update_available', { version: updateAvailable.target })" in page
    assert "t('web.system.update_behind', { behind: updateAvailable.behind })" in page
    assert "t('web.system.update_up_to_date', { checked_at: updateAvailable.checked_at })" in page

    # State 2 (confirmation).
    assert 'x-if="updateConfirming"' in page
    assert "t('web.system.update_confirm_title', { version: updateAvailable.target })" in page
    assert "t('web.system.update_confirm_downtime')" in page
    assert '@click="applyUpdate()"' in page
    assert '@click="updateConfirming = false"' in page

    # State 3 (running, including the health phase where the bridge itself
    # is gone).
    assert 'x-if="updateRunning()"' in page
    assert "t('web.system.update_restarting')" in page
    assert "t('web.system.update_step_backup')" in page
    assert "t('web.system.update_step_pull')" in page
    assert "t('web.system.update_step_recreate')" in page
    assert "t('web.system.update_step_health')" in page
    assert "t('web.system.update_restarting_hint')" in page

    # State 4 (result).
    assert "updateStatus?.state?.phase === 'done'" in page
    assert "t('web.system.update_done', { version: updateStatus.state.to })" in page
    assert "updateStatus?.state?.phase === 'failed'" in page
    assert "t('web.system.update_failed')" in page
    # `from_version` reads `state.rolled_back_to` - the concrete version
    # update-once.sh's rollback section actually restored ($BACK) - not
    # `state.from`, which is `current_tag()`'s own reading of .env and, on
    # any standard installation, the moving "stable" alias rather than a
    # version (see current_tag()'s comment there). Naming the alias
    # instead of the version is exactly the bug this binding exists to
    # fix - the one sentence the user most needs to be accurate in was
    # naming a channel. `|| updateStatus.state.from` is the deliberate
    # fallback for a sidecar older than this field.
    assert (
        "t('web.system.update_rolled_back', "
        "{ version: updateStatus.state.to, "
        "from_version: updateStatus.state.rolled_back_to || updateStatus.state.from })" in page
    )
    # `healthy` (update.py's `UpdateState.healthy`, update-once.sh's own
    # $HEALTHY) reaches this route's response but used to be read nowhere
    # in this file at all - a rollback that itself never became healthy
    # (the case update-once.sh's own rollback health wait can produce)
    # rendered the identical "is running again" sentence above, the one
    # case where that claim is false. See the dedicated behavioral test
    # below (`test_a_rollback_that_did_not_come_back_healthy_...`) for
    # proof that the two lines are mutually exclusive on `healthy`, not
    # just that this string was delivered somewhere on the page.
    assert (
        "t('web.system.update_rollback_unhealthy', "
        "{ version: updateStatus.state.to, "
        "from_version: updateStatus.state.rolled_back_to || updateStatus.state.from })" in page
    )

    # State 5 (rejected) - the "Also" fix: `phase: rejected` used to be
    # rendered NOWHERE and `state.error` was never shown at all, so a
    # rejected request (already running, no version stated, older than
    # what is running) produced no feedback whatsoever. Bound to
    # `state.error` verbatim, the same "show the backend's own wording"
    # rule the generic `updateError` hint below already follows.
    assert "updateStatus?.state?.phase === 'rejected'" in page
    assert "t('web.system.update_rejected', { message: updateStatus.state.error })" in page

    # The one gap in the brief's own sample: a visible spot for `updateError`.
    assert 'x-show="updateError"' in page
    assert 'x-text="updateError"' in page

    # `loadUpdateStatus` takes an `{ allowStop }` options object since
    # Critical 3 (see its own comment in app.js) - the bare, no-parens
    # form would never match again after that fix, silently stop proving
    # this route is wired up at all.
    assert "async loadUpdateStatus({ allowStop = true } = {})" in script
    assert "async loadUpdateCheck()" in script
    assert "async applyUpdate()" in script
    assert "async setUpdateChannel(channel)" in script


async def test_the_channel_switch_is_live_now_that_its_blockers_are_fixed(api):
    """The channel switch used to be commented out of the page: the dev
    channel could not work end to end, since `update_check.py` answered
    with `target="main"` (rejected outright by the sidecar's own
    dev-channel pattern, update-once.sh Rule 1: `^[0-9a-f]{7,40}$`), and
    even a real commit SHA would have failed at `pull` next, since no
    `loxmatter:<sha>` tag was ever published (CI only publishes
    `:dev`/`:sha-<short>`). All three blockers named at the control's own
    comment are fixed now: `update_check.py` returns the actual tip
    commit of `main`, update-once.sh derives the `sha-<short>` tag CI
    actually publishes from it, and its ancestry check compares against
    the running image's own commit rather than this checkout's HEAD - so
    the control now ships as live markup, not inside an HTML comment, and
    the stale "HIDDEN FOR 0.3.0" marker is gone."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text

    assert "HIDDEN FOR 0.3.0" not in page, "the stale hidden-control marker must not linger"

    for needle in (
        ":class=\"{ active: updateStatus?.channel === 'stable' }\"",
        "@click=\"setUpdateChannel('stable')\"",
        ":class=\"{ active: updateStatus?.channel === 'dev' }\"",
        "@click=\"setUpdateChannel('dev')\"",
        "x-show=\"updateStatus?.channel === 'dev'\"",
    ):
        assert needle in page, f"expected live in the delivered page: {needle!r}"

    assert "t('web.system.update_channel_label')" in page
    assert "t('web.system.update_channel_stable')" in page
    assert "t('web.system.update_channel_dev')" in page
    assert "t('web.system.update_channel_dev_warning')" in page
    assert "async setUpdateChannel(channel)" in script


async def test_the_changelog_does_not_promise_the_hidden_channel_switch():
    """CHANGELOG.md's `[Unreleased]` section used to advertise "An update
    channel setting (Stable, the default, or Development)" as part of the
    0.3.0 feature set - the web UI shows exactly this section as release
    notes before anyone installs an update (see the changelog's own header
    comment), so a promise here reaches people who cannot see that the
    control is commented out of the page they are about to receive. With
    the switch hidden (see the test above), the changelog must not claim
    it either."""
    changelog = (WEB_DIR.parents[2] / "CHANGELOG.md").read_text(encoding="utf-8")
    unreleased = changelog.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    assert "update channel" not in unreleased.lower()
    assert "development" not in unreleased.lower()


async def test_the_up_to_date_hint_is_suppressed_without_an_updater(api):
    """Also-look-at fix: with no sidecar reachable, the card used to show
    both 'This installation has no updater...' AND 'Up to date. Last
    checked ...' at once - the second line true (checking still works
    without a sidecar, it just cannot install anything) but useless noise
    next to the first, since there is no button either message could ever
    lead to. The up-to-date hint now also requires
    `updateStatus?.updater_present`; the two hints can therefore never
    both show at the same time (`!updateStatus.updater_present` in the
    first is the logical negation of the second's added clause). The
    error hint (no internet, checking switched off) is deliberately left
    ungated: that is real, independent information about the check
    itself, not a claim about whether anything could be installed."""
    client, _, _ = api
    page = (await client.get("/")).text

    up_to_date_idx = page.index("t('web.system.update_up_to_date'")
    tag_start = page.rindex("<p", 0, up_to_date_idx)
    tag_end = page.index(">", up_to_date_idx)
    up_to_date_tag = page[tag_start:tag_end]
    assert "updateStatus?.updater_present" in up_to_date_tag
    assert "!updateAvailable.target" in up_to_date_tag

    error_idx = page.index('x-show="!updateRunning() && updateAvailable?.error"')
    error_tag_end = page.index(">", error_idx)
    error_tag = page[error_idx:error_tag_end]
    assert "updateStatus?.updater_present" not in error_tag


async def test_the_disconnect_banner_gets_a_different_text_during_an_update(api):
    """Design section 9, state 3: a planned restart must not look like an
    outage. The existing danger banner keeps its text for a genuine
    outage but is silenced during an update (`&& !updateRunning()`), and a
    second, calmer banner takes over for exactly that window."""
    client, _, _ = api
    page = (await client.get("/")).text

    assert 'x-show="!socketConnected && socketEverConnected && !updateRunning()"' in page, (
        "the genuine-outage banner must not also fire during a planned restart"
    )
    assert 'x-show="!socketConnected && updateRunning()"' in page
    # It must use the update-specific text, not the generic outage one.
    restart_banner_start = page.index('x-show="!socketConnected && updateRunning()"')
    restart_banner_end = page.index("</div>", restart_banner_start)
    assert "t('web.system.update_restarting')" in page[restart_banner_start:restart_banner_end]


async def test_the_update_polling_only_runs_while_a_job_is_in_progress(api):
    """The status route is polled every two seconds ONLY while a job is
    running (design section 9: a request per second for a value that
    changes maybe ten times a year would be waste on a Pi) - and the timer
    must stop on every path out: the job finishing, a poll failing outside
    a running job, and the System tab itself being left. A test that only
    checked `setInterval` exists would miss a timer nothing ever clears."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    running_start = script.index("updateRunning() {")
    running_end = script.index("\n    },", running_start)
    running_body = script[running_start:running_end]
    for phase in ("queued", "backup", "pull", "recreate", "health", "rollback"):
        assert f'"{phase}"' in running_body
    # End states must NOT count as running - a stray "idle"/"done"/"failed"/
    # "rejected" here would leave the timer running forever after a job ends.
    assert "idle" not in running_body
    assert "rejected" not in running_body

    # `startUpdateTimer()` (Critical 3) is the ONE place that ever creates
    # the interval - both `loadUpdateStatus()` and `applyUpdate()` (see
    # its own test above) go through it rather than assigning
    # `this.updateTimer` themselves, which is what keeps arming
    # idempotent everywhere it happens.
    start_timer_start = script.index("startUpdateTimer() {")
    start_timer_end = script.index("\n    },", start_timer_start)
    start_timer_body = script[start_timer_start:start_timer_end]
    assert "this.updateTimer = setInterval(() => this.loadUpdateStatus(), 2000)" in start_timer_body
    assert "!this.updateTimer" in start_timer_body  # never a second interval

    load_status_start = script.index("async loadUpdateStatus({ allowStop = true } = {}) {")
    load_status_end = script.index("\n    },", load_status_start)
    load_status_body = script[load_status_start:load_status_end]
    assert "this.startUpdateTimer()" in load_status_body
    assert "this.stopUpdateTimer()" in load_status_body

    # Leaving the System tab must stop the timer too - the leak this
    # task's self-review calls out by name.
    select_view_start = script.index("async selectView(view) {")
    select_view_end = script.index("\n    },", select_view_start)
    select_view_body = script[select_view_start:select_view_end]
    assert "this.stopUpdateTimer()" in select_view_body


async def test_a_stalled_sidecar_gets_its_own_message_in_the_running_state(api):
    """Review fix, Important 2 (markup half): before this fix, the "no
    updater" hint stayed suppressed for a dead-mid-job sidecar - it is
    gated on `!updateRunning()`, and `updateRunning()` reads `true` for
    as long as `phase` sits in a running value, which for a crashed
    sidecar is forever. The running block (state 3) rendered exactly the
    same regardless of whether anything was still alive back there. This
    checks that `updateStalled()` (see the node-harness test above for
    its own logic) is actually wired into that block, with its own
    translated text, and that the existing "reconnects by itself" hint -
    which promises exactly the recovery a stalled job will not get - is
    suppressed at the same time so the two are never shown together."""
    client, _, _ = api
    page = (await client.get("/")).text

    running_start = page.index('x-if="updateRunning()"')
    running_end = page.index("</template>", running_start)
    running_block = page[running_start:running_end]

    assert 'x-show="updateStalled()"' in running_block
    assert "t('web.system.update_stalled')" in running_block
    stalled_idx = running_block.index('x-show="updateStalled()"')
    stalled_tag_start = running_block.rindex("<p", 0, stalled_idx)
    stalled_tag_end = running_block.index(">", stalled_idx)
    assert "banner danger" in running_block[stalled_tag_start:stalled_tag_end]

    # The two must be mutually exclusive - a stalled job is never also
    # told it is reconnecting on its own.
    hint_idx = running_block.index("t('web.system.update_restarting_hint')")
    hint_tag_start = running_block.rindex("<p", 0, hint_idx)
    hint_tag_end = running_block.index(">", hint_idx)
    assert "!updateStalled()" in running_block[hint_tag_start:hint_tag_end]


async def test_a_never_collected_request_gets_its_own_message_outside_the_running_block(api):
    """Stufe 2 of Critical 3 (markup half): `updateNeverCollected()` reads
    `true` while `updateRunning()` reads `false` - the sidecar never
    advanced `state.json` past whatever it held before the request, so
    state 3 (`x-if="updateRunning()"`) never renders and has no step list
    to attach a message to. This banner therefore has to live OUTSIDE that
    template, as its own sibling, with its own text - `update_stalled`
    would be dishonest here (its wording points at "the step above",
    which does not exist in this state)."""
    client, _, _ = api
    page = (await client.get("/")).text

    running_start = page.index('x-if="updateRunning()"')
    running_end = page.index("</template>", running_start)
    running_block = page[running_start:running_end]

    # Not inside the running block - it has no step list to point to.
    assert "updateNeverCollected()" not in running_block

    after_running_block = page[running_end:]
    assert 'x-show="updateNeverCollected()"' in after_running_block
    assert "t('web.system.update_not_collected')" in after_running_block
    never_collected_idx = after_running_block.index('x-show="updateNeverCollected()"')
    tag_start = after_running_block.rindex("<p", 0, never_collected_idx)
    tag_end = after_running_block.index(">", never_collected_idx)
    assert "banner danger" in after_running_block[tag_start:tag_end]


async def test_the_update_card_css_classes_carry_the_rules_the_markup_relies_on(api):
    """Minor 6: `.confirm`, `.steps` (`done`/`now`) and `.notes` style the
    update card's confirmation box, its step list and its notes/log
    boxes, but had no test of their own - a gap next to the precedent
    this file already sets for CSS (`test_the_highlight_cannot_change_
    the_width_of_a_cell`, `test_the_tab_styling_covers_links_and_
    buttons`, `test_the_device_grid_is_multi_column`). Full visual
    rendering stays genuinely untestable here - no engine applies CSS in
    this suite, Playwright is deliberately not a dependency - so this
    only proves the structural precondition: the exact selectors the
    markup binds to (`index.html`'s `class="confirm"`,
    `:class="{ done: ..., now: ... }"` on `.steps li`, `class="notes"`)
    carry rules at all, and that the two visual states `done`/`now` are
    told apart without colour alone (task requirement, also WCAG 1.4.1) -
    a filled/checked marker for `done`, a spinning ring for `now`."""
    client, _, _ = api
    page = (await client.get("/")).text
    css = (await client.get("/static/style.css")).text

    # The markup actually uses these class names - a CSS-only test could
    # otherwise stay green after a rename left the rule below orphaned.
    assert 'class="confirm"' in page
    assert 'class="steps"' in page
    assert "done: ['pull','build','recreate','health'].includes(updateStatus.state.phase)" in page
    assert "now: updateStatus.state.phase === 'backup'" in page
    assert 'class="notes"' in page

    confirm_rule = css[css.index(".confirm {") : css.index("}", css.index(".confirm {"))]
    assert "border" in confirm_rule
    assert "border-radius" in confirm_rule

    steps_rule = css[css.index(".steps {") : css.index("}", css.index(".steps {"))]
    assert "list-style: none;" in steps_rule

    done_rule = css[css.index(".steps li.done {") : css.index("}", css.index(".steps li.done {"))]
    done_before_rule = css[
        css.index(".steps li.done::before {") : css.index(
            "}", css.index(".steps li.done::before {")
        )
    ]
    assert "color: var(--text);" in done_rule
    assert "background: var(--accent);" in done_before_rule

    now_rule = css[css.index(".steps li.now {") : css.index("}", css.index(".steps li.now {"))]
    now_before_rule = css[
        css.index(".steps li.now::before {") : css.index("}", css.index(".steps li.now::before {"))
    ]
    assert "font-weight: 600;" in now_rule
    assert "animation: update-step-spin" in now_before_rule

    notes_rule = css[css.index(".notes {") : css.index("}", css.index(".notes {"))]
    assert "white-space: pre-wrap;" in notes_rule
    assert "overflow-y: auto;" in notes_rule


async def test_the_device_tile_no_longer_promises_a_ranking_it_does_not_have(api):
    """Review fix Fix 9 (2026-09-03) had deliberately renamed the heading
    "Most important values" to "Signals (start of the list)", because the
    signals shown at the time were only filtered by `exportable` - for the
    test template that meant NetworkCommissioning and BasicInformation
    instead of on/off and power. Now that `signal.functional` supplies the
    real selection criterion, the old, more honest wording is accurate
    again.

    Task 8 (grid rework, 2026-09-05) then removed the dedicated values
    heading entirely: the tile today (since the removal of the
    primary signal, design 2026-09-07) shows all functional signals at
    equal rank as an aligned grid with no section title - a heading over
    the single value list of an already
    compact tile would have been pure waste of space. The key
    `web.devices.values_heading` was therefore (task 9) removed from
    `strings.yaml` and no longer appears in the shipped markup. The
    original concern of this
    test - a heading that promises more than the tile delivers -
    still remains valid to check: neither of the two outdated
    phrasings may show up anywhere anymore."""
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
#
# Correction (final review of "last heard on the device card", 9
# September 2026): no longer the only place. The last-heard section at
# the end of this file runs `app.js` too, through the same `_app_state`
# below - see the section comment there for why that one had to run
# rather than read. The sentence above stands as the record of when this
# harness was the exception rather than a tool.
# ---------------------------------------------------------------------------

NODE = shutil.which("node")


def _app_state(setup: str = "", translations: dict[str, str] | None = None) -> dict:
    """Loads `app.js` in node, calls `app()`, and runs `setup` on it.

    `app.js` is a simple script with no module system (deliberately, see
    the header of the file) - hence `new Function` instead of an import.

    `translations` fills the module-global `translationStrings` the same
    way `loadI18n()` does at runtime, with a single assignment. It has to
    happen INSIDE the function body: `translationStrings` is a `let` in
    `app.js`'s own scope, so `setup` - which runs outside that body -
    cannot reach it. Without a table, `t()` falls back to returning the
    key, which is fine for a helper whose result is a boolean or a number
    and useless for one whose result is a sentence: two different
    timestamps would both render as "web.devices.last_heard".
    """
    fill_strings = f"translationStrings = {json.dumps(translations)};\n" if translations else ""
    # `t` is a global in the browser, and markup expressions call it by
    # name; inside this `new Function` it is a local, so the binding tests
    # at the end of this file would not find it without the export.
    tail = json.dumps("\n" + fill_strings + "globalThis.t = t;\nreturn app();")
    script = f"""
      const fs = require("node:fs");
      const src = fs.readFileSync({str(WEB_DIR / "app.js")!r}, "utf8");
      const state = new Function(src + {tail})();
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


def _js_constant(name: str) -> int:
    """The value of a `const NAME = <number>;` in app.js, read out of the
    file rather than retyped here - the same rule `_x_show_expr` below
    follows for markup. A test that hard-coded 20000 would keep passing
    with its own stale copy of a window someone had since changed."""
    match = re.search(
        rf"^const {re.escape(name)} = (\d+);",
        (WEB_DIR / "app.js").read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert match, f"no `const {name} = <number>;` in app.js"
    return int(match.group(1))


def _x_show_expr(markup: str, t_key: str) -> str:
    """The literal `x-show="..."` expression on the `<p>` whose own
    `x-text` calls `t(t_key, ...)` - pulled straight out of the SERVED
    markup, not retyped by hand, so a future edit to index.html that
    changes the condition (or drops it) breaks this extraction rather
    than silently testing a stale copy. `re.DOTALL`: the two attributes
    sit on separate lines in index.html (`x-show="..." x-cloak` then
    `x-text="..."` on the next), same as every other multi-attribute tag
    in that file."""
    match = re.search(
        r'x-show="([^"]*)"[^>]*x-text="t\(\'' + re.escape(t_key) + r"'",
        markup,
        flags=re.DOTALL,
    )
    assert match, f"no x-show immediately precedes t('{t_key}', ...) in the markup"
    return match.group(1)


def _running_step_lis(markup: str) -> list[tuple[str, str]]:
    """The `(:class expression, x-text expression)` pair for each of the
    four step `<li>` elements inside the `x-if="updateRunning()"` block,
    pulled straight out of the SERVED markup - the same "extract the real
    expression, don't retype it" technique `_x_show_expr` above already
    uses, for the same reason: a retyped copy would only ever prove it
    agrees with itself."""
    running_start = markup.index('x-if="updateRunning()"')
    running_end = markup.index("</template>", running_start)
    block = markup[running_start:running_end]
    lis = re.findall(r'<li\s+:class="(\{[^}]*\})"\s*\n\s*x-text="([^"]*)"', block)
    assert len(lis) == 4, f"expected exactly four step <li> elements, found {len(lis)}: {lis}"
    return lis


def _eval_js(expr: str, *, phase: str, channel: str) -> object:
    """Evaluates one of the expressions `_running_step_lis` extracted,
    against a real `updateStatus` shaped the way `/api/update/status`
    actually returns one, in node - not a Python re-implementation of
    Alpine's expression evaluation, which would only prove that
    re-implementation self-consistent. `t` is stubbed to return its own
    key (rather than a real translation) so this stays a check of WHICH
    key each phase/channel combination selects, not of strings.yaml's
    wording."""
    script_src = (
        "const t = (key) => key;\n"
        f"const updateStatus = {{ state: {{ phase: {json.dumps(phase)} }}, "
        f"channel: {json.dumps(channel)} }};\n"
        f"console.log(JSON.stringify({expr}));\n"
    )
    result = subprocess.run(
        [NODE, "-e", script_src], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_build_phase_is_tied_across_every_place_it_lives(api):
    """The phase set (`idle`/`queued`/`backup`/`pull`/`build`/`recreate`/
    `health`/`rollback`/`done`/`failed`/`rejected`) lives in five places
    with nothing tying them together: `update-once.sh`'s own `set_state`
    calls, `update.py`'s `_RUNNING_PHASES`, `app.js`'s `updateRunning()`,
    index.html's four step `<li>`s, and `strings.yaml`'s `update_step_*`
    keys. Before this test, nothing would have noticed `build` (design
    addendum "The development channel builds on the machine" plus this
    feature's own follow-up) landing in some of those five and not
    others - a card silently stuck on a step that never highlights,
    the kind of drift this feature has produced six times, always found
    by a person comparing two files by hand.

    Every check below runs the REAL code - the real `_RUNNING_PHASES`
    import, the real `app.js` executed in node, the real `:class`/
    `x-text` expressions pulled out of the served index.html, the real
    `strings.yaml` table - rather than a second, hand-typed phase list
    that could only ever prove agreement with itself.

    What this test does NOT cover: `update-once.sh` cannot be executed
    here (node runs no POSIX sh), so this test does not touch place one
    of the five. That place is bite-checked separately and more strongly
    - behaviourally, not just textually - by
    `tests/test_updater_script.py::test_the_image_build_reports_phase_
    build_not_pull`, which snapshots state.json at the instant `docker
    build` actually runs."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text

    from loxmatter import i18n
    from loxmatter.update import _RUNNING_PHASES

    # Place 2: update.py's own list - the value every other place below
    # is measured against.
    assert "build" in _RUNNING_PHASES, "update.py's _RUNNING_PHASES must include 'build'"

    # Place 3: app.js's updateRunning(), executed for real.
    values = _app_state(
        """
        state.updateStatus = { state: { phase: "build" }, channel: "dev" };
        console.log(JSON.stringify({ running: state.updateRunning() }));
        """
    )
    assert values["running"] is True, "app.js's updateRunning() must treat 'build' as running"

    # Place 4: index.html's step list, both halves - the highlight (keyed
    # on phase: 'build' and 'pull' both have to light up the second step,
    # and both have to mark the first step 'done' once reached) and the
    # label (keyed on channel - see index.html's own comment for why).
    step_lis = _running_step_lis(page)
    backup_class_expr, _ = step_lis[0]
    step2_class_expr, step2_text_expr = step_lis[1]

    assert _eval_js(step2_class_expr, phase="build", channel="dev")["now"] is True
    assert _eval_js(step2_class_expr, phase="pull", channel="stable")["now"] is True
    assert _eval_js(backup_class_expr, phase="build", channel="dev")["done"] is True
    assert _eval_js(backup_class_expr, phase="pull", channel="stable")["done"] is True

    # The label must follow the CHANNEL for the whole run, including
    # `backup` - before `phase` has ever read 'build' or 'pull' - not
    # just at the instant the step is actually highlighted.
    assert (
        _eval_js(step2_text_expr, phase="backup", channel="dev") == "web.system.update_step_build"
    )
    assert _eval_js(step2_text_expr, phase="build", channel="dev") == "web.system.update_step_build"
    assert (
        _eval_js(step2_text_expr, phase="backup", channel="stable") == "web.system.update_step_pull"
    )
    assert (
        _eval_js(step2_text_expr, phase="pull", channel="stable") == "web.system.update_step_pull"
    )

    # Place 5: strings.yaml must actually carry the key the channel-keyed
    # label above names, in both languages - a label pointing at a key
    # nobody translated would be its own kind of drift.
    assert "web.system.update_step_build" in i18n._STRINGS
    assert set(i18n._STRINGS["web.system.update_step_build"]) >= {"en", "de"}

    # `script` is fetched only so a future edit cannot silently swap
    # `_app_state`'s own file read for the SERVED script without this
    # test noticing the delivered file differs - `_app_state` reads
    # `app.js` straight off disk, `script` is what `/static/app.js`
    # actually serves.
    assert (WEB_DIR / "app.js").read_text(encoding="utf-8") == script


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_rollback_that_did_not_come_back_healthy_gets_its_own_message(api):
    """Found comparing against an independent implementation of the same
    design: `update-once.sh` sets `ROLLED=true` (the image write-back to
    the old tag succeeded) and THEN, separately, `HEALTHY=false` when the
    RESTORED version itself never answers `/health` within
    `HEALTH_TIMEOUT` - the one case where a human, not the sidecar, has
    to intervene next. `healthy` reaches `GET /api/update/status`
    (`api/update.py`'s `_status()`) but was never read anywhere in
    index.html at all (`grep healthy src/loxmatter/web/index.html`
    returned nothing) - so the tile rendered the same "{from_version} is
    running again" sentence regardless, in the one case where that
    claim is false.

    `test_the_update_card_offers_its_four_states_and_the_confirmation`
    above only proves the new string was DELIVERED somewhere on the page
    - true even if both lines' `x-show` conditions were identical, or
    both hardcoded `true`, which would reproduce the exact bug this test
    exists to catch while that assertion still passed. This test instead
    pulls the REAL `x-show` expression text for both lines out of the
    served page and evaluates it in node against a constructed
    `updateStatus.state`, for every combination of `rolled_back`/
    `healthy` - proving the two lines are actually mutually exclusive at
    runtime, not merely both present in the HTML somewhere.

    No Alpine runtime involved (consistent with every other node-harness
    test in this file: `updateStalled()` and friends are plain JS
    predicates run directly) - `x-show`'s value is itself a plain JS
    boolean expression Alpine evaluates against the component's data, so
    running it directly against a hand-built `updateStatus` is a faithful
    stand-in, not a simulation of Alpine's own machinery."""
    client, _, _ = api
    page = (await client.get("/")).text
    rolled_back_expr = _x_show_expr(page, "web.system.update_rolled_back")
    unhealthy_expr = _x_show_expr(page, "web.system.update_rollback_unhealthy")

    def shown(expr: str, *, rolled_back: bool, healthy: bool) -> bool:
        script = (
            "const updateStatus = { state: { rolled_back: "
            + ("true" if rolled_back else "false")
            + ", healthy: "
            + ("true" if healthy else "false")
            + " } };\n"
            f"console.log(!!({expr}));\n"
        )
        result = subprocess.run(
            [NODE, "-e", script], capture_output=True, text=True, timeout=10, check=False
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip() == "true"

    # Rolled back AND healthy again - the plain, reassuring sentence, and
    # only that one.
    assert shown(rolled_back_expr, rolled_back=True, healthy=True) is True
    assert shown(unhealthy_expr, rolled_back=True, healthy=True) is False

    # Rolled back but STILL not healthy - the case this fix adds. The
    # reassuring sentence must no longer show (it would claim the old
    # version "is running again", which here is not true); the new one
    # must.
    assert shown(rolled_back_expr, rolled_back=True, healthy=False) is False
    assert shown(unhealthy_expr, rolled_back=True, healthy=False) is True

    # No rollback at all - neither line, regardless of `healthy`.
    assert shown(rolled_back_expr, rolled_back=False, healthy=True) is False
    assert shown(unhealthy_expr, rolled_back=False, healthy=True) is False
    assert shown(rolled_back_expr, rolled_back=False, healthy=False) is False
    assert shown(unhealthy_expr, rolled_back=False, healthy=False) is False


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_cold_load_during_the_restart_window_still_starts_the_update_poll():
    """Review fix, Important 1: `loadSystem()` used to await `GET
    /api/version` first, inside the same `try` as `loadUpdateStatus()`
    and `loadUpdateCheck()`. `/api/version` is exactly the call that
    fails throughout the bridge's OWN restart window (`recreate`/`health`
    in `update.py`'s `_RUNNING_PHASES`) - the one window this whole card
    exists to report progress through. A failure there jumped straight
    to the outer `catch`, past both update calls: `updateStatus` stayed
    `null`, `updateRunning()` read `false`, and the poll timer - started
    only inside `loadUpdateStatus()` - never got the chance to start.
    Opening or reloading the System tab (or pressing "Refresh") during
    exactly this window showed nothing but the generic load-error
    banner: no restarting message, no steps, no polling.

    Runs `app.js`'s own `loadSystem()` directly, the same way the other
    node-harness tests in this file exercise the state object without an
    Alpine runtime: `state.request` is stubbed to fail only for
    `/api/version` and to answer a mid-job `/api/update/status` for
    everything else. Before the fix this test failed with `updateStatus`
    still `null` and no timer ever started - `/api/version` failing
    aborted the whole function before `loadUpdateStatus()` ran."""
    values = _app_state(
        """
        state.request = async (method, path) => {
          if (path === "/api/version") {
            throw new Error("bridge unreachable");
          }
          if (path === "/api/update/status") {
            return {
              state: { phase: "pull", id: "j1", from: "1.0.0", to: "1.1.0",
                       error: null, rolled_back: false, healthy: true },
              updater_present: true,
              log: [],
              channel: "stable",
              check_enabled: true,
            };
          }
          if (path === "/api/update/check") {
            return {
              channel: "stable", target: "1.1.0", title: null, notes: null,
              behind: null, checked_at: null, error: null,
            };
          }
          if (path === "/api/diagnostics/system") {
            return [];
          }
          throw new Error("unexpected request " + method + " " + path);
        };
        (async () => {
          await state.loadSystem();
          // The interval keeps node's event loop alive - cleared before
          // the process exits, or the 30s subprocess timeout would fire.
          const timerWasSet = state.updateTimer !== null;
          state.stopUpdateTimer();
          console.log(JSON.stringify({
            phase: state.updateStatus ? state.updateStatus.state.phase : null,
            timerWasSet,
            systemError: state.systemError,
            versionInfo: state.versionInfo,
          }));
        })();
        """
    )

    assert values["phase"] == "pull"
    assert values["timerWasSet"] is True
    assert values["systemError"] is None
    # `/api/version` failed and must not have blanked anything it does
    # not own: `versionInfo` stays at its initial `null` (see `app()`'s
    # own state) rather than being forced to some other value by a
    # failure in a completely different fetch.
    assert values["versionInfo"] is None


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_apply_update_arms_the_poll_timer_even_though_the_immediate_read_is_stale():
    """Critical 3. `applyUpdate()` used to call `loadUpdateStatus()` once,
    immediately after the POST, and rely on THAT call's own internal
    "start the timer if a phase is running" branch to arm the poll. At
    the instant of that call, `state.json` can still hold the PREVIOUS
    end state - the sidecar's own loop only wakes once every two seconds
    (update-once.sh, driven by entrypoint.sh) - so `updateRunning()` read
    `false`, the timer was never armed, and nothing else ever called
    `loadUpdateStatus()` again on its own: `loadSystem()` only runs on a
    tab switch or a manual "Refresh", and the reconnect handlers never
    touch the System tab at all.

    Proven end to end in the unpatched code with the exact stub below
    (POST succeeds, the immediate status GET answers with a stale "idle"
    state - no request/sidecar change involved, purely a timing window):
    `state.updateTimer` stayed `null` after `applyUpdate()` returned, and
    the card would have kept showing "Install update" while the job
    silently ran. Fixed by arming the timer in `applyUpdate()` itself,
    unconditionally, right after the POST succeeds - independent of
    whatever phase this immediate read happens to return."""
    values = _app_state(
        """
        state.updateAvailable = { target: "1.1.0", error: null };
        state.updateConfirming = true;
        state.updateError = null;
        let statusCalls = 0;
        state.request = async (method, path) => {
          if (method === "POST" && path === "/api/update/apply") {
            return { id: "job-1" };
          }
          if (method === "GET" && path === "/api/update/status") {
            statusCalls += 1;
            // The sidecar has not woken up yet - state.json still reads
            // the END state from BEFORE this request, exactly the
            // staleness window Critical 3 is about.
            return {
              state: { phase: "idle", id: null, from: null, to: null,
                       error: null, rolled_back: false, healthy: true },
              updater_present: true, log: [], channel: "stable", check_enabled: true,
            };
          }
          throw new Error("unexpected request " + method + " " + path);
        };
        (async () => {
          await state.applyUpdate();
          const timerWasArmed = state.updateTimer !== null;
          state.stopUpdateTimer();
          console.log(JSON.stringify({
            timerWasArmed,
            statusCalls,
            updateError: state.updateError,
            updateConfirming: state.updateConfirming,
          }));
        })();
        """
    )

    assert values["timerWasArmed"] is True
    assert values["statusCalls"] == 1
    assert values["updateError"] is None
    assert values["updateConfirming"] is False


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_apply_update_keeps_the_timer_armed_when_the_immediate_read_fails_outright():
    """Critical 3, the other half named in this task's own brief: what the
    poll timer must do if the FIRST status read after a successful POST
    fails outright (a transient network hiccup, not a 401 - that path is
    already covered by `test_an_expired_session_stops_the_update_poll_
    instead_of_retrying_forever` below). The POST already succeeded, so
    the sidecar already has a job queued regardless of whether this one
    read can currently reach the bridge - the timer armed in
    `applyUpdate()` must survive that failed read exactly as it survives
    a stale-but-successful one, and its own next tick two seconds later
    is what gets a real answer.

    `state.authenticated` is set `true` here (unlike this file's default
    `app()` state) precisely so the failure below is NOT mistaken for an
    expired session - `loadUpdateStatus()`'s own `!this.authenticated`
    branch would otherwise stop the timer for an unrelated reason and
    this test would pass for the wrong cause."""
    values = _app_state(
        """
        state.authenticated = true;
        state.updateAvailable = { target: "1.1.0", error: null };
        state.updateConfirming = true;
        state.updateError = null;
        state.request = async (method, path) => {
          if (method === "POST" && path === "/api/update/apply") {
            return { id: "job-1" };
          }
          if (method === "GET" && path === "/api/update/status") {
            throw new Error("network hiccup");
          }
          throw new Error("unexpected request " + method + " " + path);
        };
        (async () => {
          await state.applyUpdate();
          const timerWasArmed = state.updateTimer !== null;
          state.stopUpdateTimer();
          console.log(JSON.stringify({ timerWasArmed, authenticated: state.authenticated }));
        })();
        """
    )

    assert values["timerWasArmed"] is True
    assert values["authenticated"] is True


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_apply_update_does_not_arm_a_second_timer_when_one_is_already_running():
    """Critical 3's own explicit caveat: arming must stay idempotent. A
    session that opened the System tab while a PREVIOUS job was still
    running already has a timer armed by the time anyone could click
    "Install update" again (the button is disabled while `updateRunning()`
    - but this proves the underlying arming logic itself is safe, not
    just the button). `startUpdateTimer()`'s own guard
    (`if (!this.updateTimer)`) must leave an EXISTING timer's identity
    untouched rather than replace it with a second `setInterval` - two
    timers polling the same endpoint would double the request rate and
    leave the first one uncleared forever."""
    values = _app_state(
        """
        state.updateAvailable = { target: "1.1.0", error: null };
        state.updateConfirming = true;
        state.updateError = null;
        const existingTimer = setInterval(() => {}, 999999);
        state.updateTimer = existingTimer;
        state.request = async (method, path) => {
          if (method === "POST" && path === "/api/update/apply") {
            return { id: "job-1" };
          }
          if (method === "GET" && path === "/api/update/status") {
            return {
              state: { phase: "backup", id: "job-1", from: "1.0.0", to: "1.1.0",
                       error: null, rolled_back: false, healthy: true },
              updater_present: true, log: [], channel: "stable", check_enabled: true,
            };
          }
          throw new Error("unexpected request " + method + " " + path);
        };
        (async () => {
          await state.applyUpdate();
          const sameTimer = state.updateTimer === existingTimer;
          state.stopUpdateTimer();
          console.log(JSON.stringify({ sameTimer }));
        })();
        """
    )

    assert values["sameTimer"] is True


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_a_stale_poll_shortly_after_apply_does_not_stop_the_timer():
    """Stufe 2 of Critical 3: the fix above only protects `applyUpdate()`'s
    OWN immediate read (`allowStop: false`). Every poll after that one -
    the timer's own two-second tick - calls `loadUpdateStatus()` with the
    default `allowStop: true`, and the sidecar's loop (entrypoint.sh) can
    easily still not have caught up by then: it wakes at most every two
    seconds, and may be finishing the pass that was already running when
    the request landed. Proven by manual browser verification before this
    fix: `updateTimer !== null` was `true` right after `applyUpdate()`,
    `false` 2.5 seconds later with `updateStatus.state.phase` still
    `"idle"` - the SAME bug Critical 3 fixed, reopened by the second poll
    instead of the first.

    Simulates that 2.5s gap deterministically by overriding `Date.now`
    rather than actually sleeping - `applyUpdate()`'s own
    `updateApplyDeadline` is computed from it (see `UPDATE_APPLY_GRACE_MS`
    in app.js), so advancing the mocked clock advances the fix's own
    notion of elapsed time exactly as a real 2.5s wait would, without
    slowing this test down or making it flaky under load.

    Against the code before this fix, this test fails: the second
    `loadUpdateStatus()` call sees `phase: "idle"`, `updateRunning()` reads
    `false`, and its `allowStop`-gated branch tears the timer down."""
    values = _app_state(
        """
        let now = 1_700_000_000_000;
        Date.now = () => now;

        state.updateAvailable = { target: "1.1.0", error: null };
        state.updateConfirming = true;
        state.updateError = null;
        state.request = async (method, path) => {
          if (method === "POST" && path === "/api/update/apply") {
            return { id: "job-1" };
          }
          if (method === "GET" && path === "/api/update/status") {
            // The sidecar has still not woken up - state.json reads
            // exactly the end state from BEFORE this request, on every
            // poll, not just the first one.
            return {
              state: { phase: "idle", id: null, from: null, to: null,
                       error: null, rolled_back: false, healthy: true },
              updater_present: true, log: [], channel: "stable", check_enabled: true,
            };
          }
          throw new Error("unexpected request " + method + " " + path);
        };
        (async () => {
          await state.applyUpdate();
          const timerArmedRightAfterApply = state.updateTimer !== null;

          // The timer's own next tick, 2.5s later - well within
          // UPDATE_APPLY_GRACE_MS, but past the point a naive fix (or the
          // pre-fix code) would already have given up.
          now += 2500;
          await state.loadUpdateStatus();
          const timerStillArmed = state.updateTimer !== null;
          const phaseStillIdle = state.updateStatus.state.phase === "idle";

          state.stopUpdateTimer();
          console.log(JSON.stringify({
            timerArmedRightAfterApply,
            timerStillArmed,
            phaseStillIdle,
            updateError: state.updateError,
          }));
        })();
        """
    )

    assert values["timerArmedRightAfterApply"] is True
    assert values["phaseStillIdle"] is True
    assert values["timerStillArmed"] is True
    # A poll that is still within the grace window must not surface the
    # generic connection error either - the bridge is answering fine, the
    # sidecar just has not caught up yet.
    assert values["updateError"] is None


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_apply_gives_up_and_reports_the_request_was_never_collected():
    """The other half of the same fix: `updateAwaitingPickup()` must not
    become "poll forever" - the whole point of the previous test is that
    the timer survives a SHORT stale window, not that it survives
    indefinitely. Once `UPDATE_APPLY_GRACE_MS` passes with `state.json`
    never once reporting this apply's own job id, `updateNeverCollected()`
    should read `true` (the card's honest "this was accepted but nobody
    picked it up" message, index.html's state 3b) and the timer should
    stop - continuing to poll every two seconds for an outcome that will
    never arrive is exactly the waste `updateTimer`'s own comment warns
    against.

    `state.json` here answers with a DIFFERENT id throughout (`"stale-id"`,
    never `"job-1"`) - simulating either a sidecar that crashed between
    `api/update.py`'s own 503 presence check and actually reading
    request.json (this fix's own named scenario), or one that is simply
    dead and never runs another pass at all."""
    values = _app_state(
        """
        let now = 1_700_000_000_000;
        Date.now = () => now;

        state.updateAvailable = { target: "1.1.0", error: null };
        state.updateConfirming = true;
        state.updateError = null;
        state.request = async (method, path) => {
          if (method === "POST" && path === "/api/update/apply") {
            return { id: "job-1" };
          }
          if (method === "GET" && path === "/api/update/status") {
            return {
              state: { phase: "idle", id: "stale-id", from: null, to: null,
                       error: null, rolled_back: false, healthy: true },
              updater_present: true, log: [], channel: "stable", check_enabled: true,
            };
          }
          if (method === "GET" && path === "/api/version") {
            // `loadUpdateStatus()` re-fetches this once it decides to
            // stop the timer - see its own comment.
            return { version: "1.0.0" };
          }
          throw new Error("unexpected request " + method + " " + path);
        };
        (async () => {
          await state.applyUpdate();

          // Still well within the grace window: neither predicate should
          // fire yet.
          now += 2500;
          await state.loadUpdateStatus();
          const stillWaitingMidway = state.updateAwaitingPickup();
          const notYetGivenUp = !state.updateNeverCollected();

          // Past UPDATE_APPLY_GRACE_MS now, still the same stale id.
          now += 30000;
          await state.loadUpdateStatus();

          console.log(JSON.stringify({
            stillWaitingMidway,
            notYetGivenUp,
            timerStoppedAfterGraceExpired: state.updateTimer === null,
            neverCollectedAfterGraceExpired: state.updateNeverCollected(),
            awaitingPickupAfterGraceExpired: state.updateAwaitingPickup(),
            stalledAfterGraceExpired: state.updateStalled(),
          }));
        })();
        """
    )

    assert values["stillWaitingMidway"] is True
    assert values["notYetGivenUp"] is True
    assert values["timerStoppedAfterGraceExpired"] is True
    assert values["neverCollectedAfterGraceExpired"] is True
    assert values["awaitingPickupAfterGraceExpired"] is False
    # Distinct from updateStalled(): that state means "was running, then
    # went quiet" - phase never left "idle" here, so it must stay false.
    assert values["stalledAfterGraceExpired"] is False


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_apply_grace_clears_the_instant_the_sidecars_own_job_id_is_seen():
    """The id comparison (`loadUpdateStatus()`, matching `updateApplyJobId`
    against `updateStatus.state.id`) is what lets a REJECTED request end
    the grace window immediately, without waiting out the full
    `UPDATE_APPLY_GRACE_MS`: `reject()` (update-once.sh) never passes
    through a running phase at all, so `updateRunning()` alone could never
    detect the pickup. Without this, `updateAwaitingPickup()` would stay
    `true` for the rest of the grace window even though the sidecar
    answered almost immediately, and a poll landing after the deadline
    (simulated here by jumping the clock forward) would wrongly report
    `updateNeverCollected()` for a request that was, in fact, collected
    and answered."""
    values = _app_state(
        """
        let now = 1_700_000_000_000;
        Date.now = () => now;

        state.updateAvailable = { target: "1.1.0", error: null };
        state.updateConfirming = true;
        state.updateError = null;
        let statusPhase = "idle";
        let statusId = null;
        state.request = async (method, path) => {
          if (method === "POST" && path === "/api/update/apply") {
            return { id: "job-1" };
          }
          if (method === "GET" && path === "/api/update/status") {
            return {
              state: { phase: statusPhase, id: statusId, from: "1.0.0", to: null,
                       error: "already running", rolled_back: false, healthy: true },
              updater_present: true, log: [], channel: "stable", check_enabled: true,
            };
          }
          if (method === "GET" && path === "/api/version") {
            // `loadUpdateStatus()` re-fetches this once the rejected
            // request makes it decide to stop the timer - see its own
            // comment.
            return { version: "1.0.0" };
          }
          throw new Error("unexpected request " + method + " " + path);
        };
        (async () => {
          await state.applyUpdate();

          // The sidecar answers fast - well within the grace window -
          // but with `rejected`, a phase `updateRunning()` never counts.
          now += 500;
          statusPhase = "rejected";
          statusId = "job-1";
          await state.loadUpdateStatus();
          const clearedRightAfterRejection = state.updateApplyDeadline === null;

          // Long after the ORIGINAL grace window would have expired -
          // must stay unremarkable now that the id has been seen.
          now += 30000;
          await state.loadUpdateStatus();

          console.log(JSON.stringify({
            clearedRightAfterRejection,
            awaitingPickupMuchLater: state.updateAwaitingPickup(),
            neverCollectedMuchLater: state.updateNeverCollected(),
            timerStoppedMuchLater: state.updateTimer === null,
          }));
        })();
        """
    )

    assert values["clearedRightAfterRejection"] is True
    assert values["awaitingPickupMuchLater"] is False
    assert values["neverCollectedMuchLater"] is False
    assert values["timerStoppedMuchLater"] is True


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_update_apply_missed_is_written_reactively_not_left_to_a_clock_read():
    """Alpine bug found live in the browser: `x-show="updateNeverCollected()"`
    (index.html, state 3b) only re-evaluates when a property IT read on a
    PREVIOUS run later changes - and a `Date.now()` comparison is never such
    a property. Before this fix, `updateNeverCollected()` computed
    `updateApplyDeadline !== null && Date.now() >= updateApplyDeadline`
    fresh on every call. The one poll where that expression would first
    flip to `true` is the SAME poll where `loadUpdateStatus()` lets the
    timer stop (`updateRunning()` and `updateAwaitingPickup()` both `false`
    - nothing left to await) - so no later tick ever calls the method again
    to notice the flip, and Alpine kept rendering the value from one poll
    earlier: `false`. Proven in a live browser: `updateNeverCollected()`
    called by hand from the console read `true`, while the `<p>` it drives
    stayed `display: none` and the card had fallen back to "Install
    update", as if the accepted request had never happened.

    A test that merely calls `updateNeverCollected()` again right after the
    deadline passes - see `test_apply_grace_clears...` above - would NOT
    have caught this: the predicate itself was already correct, it simply
    never gets asked again once the timer stops. What must be asserted
    instead is `updateApplyMissed`, the plain boolean field the fixed
    `updateNeverCollected()` now reads (and the one a real `x-show` would
    track): `loadUpdateStatus()` must WRITE it, once, the instant it
    notices the deadline has passed - the write itself is what makes the
    fact durable and reactive, not any later re-read of the clock."""
    values = _app_state(
        """
        let now = 1_700_000_000_000;
        Date.now = () => now;

        state.updateAvailable = { target: "1.1.0", error: null };
        state.updateConfirming = true;
        state.updateError = null;
        state.request = async (method, path) => {
          if (method === "POST" && path === "/api/update/apply") {
            return { id: "job-1" };
          }
          if (method === "GET" && path === "/api/update/status") {
            // A different id throughout: the sidecar never picks this up.
            return {
              state: { phase: "idle", id: "stale-id", from: null, to: null,
                       error: null, rolled_back: false, healthy: true },
              updater_present: true, log: [], channel: "stable", check_enabled: true,
            };
          }
          if (method === "GET" && path === "/api/version") {
            return { version: "1.0.0" };
          }
          throw new Error("unexpected request " + method + " " + path);
        };
        (async () => {
          await state.applyUpdate();

          // Still within the grace window: the flag must not be set yet.
          now += 2500;
          await state.loadUpdateStatus();
          const notYetMissedMidway = state.updateApplyMissed;

          // The one poll that crosses UPDATE_APPLY_GRACE_MS - this is the
          // write under test.
          now += 30000;
          await state.loadUpdateStatus();
          const missedRightAfterTheCrossingPoll = state.updateApplyMissed;

          // A real page left sitting on this exact moment gets no further
          // loadUpdateStatus() calls at all - the timer already stopped.
          // Null out updateApplyDeadline itself (what the OLD, time-derived
          // updateNeverCollected() compared against) and move the clock on
          // regardless, with no further poll: if updateApplyMissed were
          // still secretly reading the clock rather than holding a written
          // value, this would either throw (deadline gone) or read false
          // (comparison against null). A plain boolean survives both.
          state.updateApplyDeadline = null;
          now += 1_000_000;

          console.log(JSON.stringify({
            notYetMissedMidway,
            missedRightAfterTheCrossingPoll,
            stillMissedWithNoFurtherPollOrDeadlineField: state.updateApplyMissed,
            neverCollectedStillReadsTheFlag: state.updateNeverCollected(),
            timerStoppedAfterTheCrossingPoll: state.updateTimer === null,
          }));
        })();
        """
    )

    assert values["notYetMissedMidway"] is False
    assert values["missedRightAfterTheCrossingPoll"] is True
    assert values["stillMissedWithNoFurtherPollOrDeadlineField"] is True
    assert values["neverCollectedStillReadsTheFlag"] is True
    assert values["timerStoppedAfterTheCrossingPoll"] is True


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_update_apply_missed_does_not_haunt_the_card_past_the_next_attempt():
    """The other risk in the same fix: `updateApplyMissed` (see the
    previous test) must not stay `true` forever once it stops being true.
    `applyUpdate()` clears it itself, at the very start of a NEW attempt,
    before that attempt's own POST even lands - otherwise a retry that IS
    picked up promptly would still render the stale "never collected"
    banner (index.html, state 3b) for up to another `UPDATE_APPLY_GRACE_MS`,
    a true fact about the PREVIOUS attempt presented as still true about
    this one."""
    values = _app_state(
        """
        let now = 1_700_000_000_000;
        Date.now = () => now;
        let matchIncomingId = false;

        state.updateAvailable = { target: "1.1.0", error: null };
        state.updateConfirming = true;
        state.updateError = null;
        state.request = async (method, path) => {
          if (method === "POST" && path === "/api/update/apply") {
            return { id: "job-1" };
          }
          if (method === "GET" && path === "/api/update/status") {
            return {
              state: { phase: "idle", id: matchIncomingId ? "job-1" : "stale-id",
                       from: null, to: null, error: null, rolled_back: false,
                       healthy: true },
              updater_present: true, log: [], channel: "stable", check_enabled: true,
            };
          }
          if (method === "GET" && path === "/api/version") {
            return { version: "1.0.0" };
          }
          throw new Error("unexpected request " + method + " " + path);
        };
        (async () => {
          // First attempt: never collected.
          await state.applyUpdate();
          now += 30000;
          await state.loadUpdateStatus();
          const missedAfterFirstAttempt = state.updateApplyMissed;

          // Second attempt: picked up right away this time.
          matchIncomingId = true;
          await state.applyUpdate();
          const missedRightAfterSecondAttemptStarts = state.updateApplyMissed;
          await state.loadUpdateStatus();

          console.log(JSON.stringify({
            missedAfterFirstAttempt,
            missedRightAfterSecondAttemptStarts,
            missedAfterSecondAttemptResolves: state.updateApplyMissed,
          }));
        })();
        """
    )

    assert values["missedAfterFirstAttempt"] is True
    assert values["missedRightAfterSecondAttemptStarts"] is False
    assert values["missedAfterSecondAttemptResolves"] is False


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_updatestalled_is_true_only_while_running_with_no_recent_heartbeat():
    """Review fix, Important 2: a crashed sidecar (OOM, a full disk) still
    leaves `state.json`'s `phase` on whatever it was mid-job - nothing is
    left to advance it. `updater_present` already carries "no heartbeat
    in the last 30 seconds" on every `/api/update/status` response (see
    `update.py`'s `updater_present()`, mirrored by `api/update.py`'s
    `_status()`); `updateStalled()` combines it with `updateRunning()` so
    the card can tell a genuinely stuck job apart from one still making
    progress. Before this method existed, calling it threw `TypeError:
    state.updateStalled is not a function` - this test would fail outright
    rather than merely assert the wrong value."""
    values = _app_state(
        """
        function stalledFor(status) {
          state.updateStatus = status;
          return state.updateStalled();
        }
        console.log(JSON.stringify([
          stalledFor({ state: { phase: "pull" }, updater_present: false }),
          stalledFor({ state: { phase: "pull" }, updater_present: true }),
          stalledFor({ state: { phase: "done" }, updater_present: false }),
          stalledFor(null),
        ]));
        """
    )

    assert values == [
        True,  # running, no heartbeat - the stuck case this fix names
        False,  # running, heartbeat present - a healthy job in progress
        False,  # not running at all - an end state, not a stall
        False,  # no state ever loaded
    ]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_updater_version_behind_says_nothing_for_an_unknown_digest():
    """`updaterVersionBehind()` (app.js) must read `false` - not "unknown
    but assume the worst" - whenever either side of the DIGEST comparison
    is missing. The comparison moved from version STRINGS to digests
    (review fix: CI baked the release version into every updater image
    unconditionally, so a version-string comparison flagged "newer" on
    every single release, including one that never touched
    deploy/updater/ at all). The case this test exists for: a sidecar
    built before `updater_digest` existed, one built locally (no
    `RepoDigests` entry), checking switched off, or GHCR unreachable right
    now, all report `null` for one side or the other - if this read `true`
    for any of those, the card would nag with a command that fixes
    nothing."""
    values = _app_state(
        """
        function behindFor(updaterDigest, publishedDigest) {
          state.updateStatus = {
            state: { updater_digest: updaterDigest },
            published_updater_digest: publishedDigest,
          };
          return state.updaterVersionBehind();
        }
        console.log(JSON.stringify([
          behindFor("sha256:aaa", "sha256:bbb"),
          behindFor("sha256:aaa", "sha256:aaa"),
          behindFor(null, "sha256:bbb"),
          behindFor("sha256:aaa", null),
          behindFor(null, null),
        ]));
        """
    )

    assert values == [
        True,  # the running sidecar's image is not what GHCR serves any more
        False,  # both agree - nothing to say
        False,  # sidecar predates the field, or was built locally - unknown is not stale
        False,  # GHCR unreachable, or checking switched off
        False,  # neither side known
    ]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_updater_refresh_message_carries_the_reported_host_path():
    """`updaterRefreshMessage()` must print the "refresh the updater"
    command against the sidecar's OWN reported host path
    (`updater_stack_host_path`), not a value this file guesses - the
    defect this whole feature replaces: the card used to hardcode
    `~/loxmatter/deploy/testhost`, which is wrong on any checkout not
    literally named `loxmatter` (the maintainer's own is
    `~/matter-loxone`)."""
    values = _app_state(
        """
        state.updateStatus = {
          state: {
            updater_version: "0.3.3",
            updater_stack_host_path: "/home/pi/matter-loxone/deploy/testhost",
          },
        };
        state.versionInfo = { version: "0.3.4" };
        console.log(JSON.stringify(state.updaterRefreshMessage()));
        """,
        translations={
            "web.system.updater_behind": (
                "still on {updater_version}, this bridge on {version} - cd {path}"
            ),
            "web.system.updater_behind_unknown_path": "must not be used here",
        },
    )

    assert (
        values == "still on 0.3.3, this bridge on 0.3.4 - cd /home/pi/matter-loxone/deploy/testhost"
    )


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_updater_refresh_message_degrades_when_the_host_path_is_unresolved():
    """The sibling case: `update-once.sh`'s `host_path_for()` (via
    entrypoint.sh's one-time resolution) could not resolve
    `$LOXMATTER_STACK` at all - the message must not fabricate a path by
    substituting an empty/`null` one into the normal template, it must
    switch to the dedicated fallback string that says so in words."""
    values = _app_state(
        """
        state.updateStatus = {
          state: { updater_version: "0.3.3", updater_stack_host_path: null },
        };
        state.versionInfo = { version: "0.3.4" };
        console.log(JSON.stringify(state.updaterRefreshMessage()));
        """,
        translations={
            "web.system.updater_behind": "must not be used here - {path}",
            "web.system.updater_behind_unknown_path": (
                "still on {updater_version}, this bridge on {version} - path unknown"
            ),
        },
    )

    assert values == "still on 0.3.3, this bridge on 0.3.4 - path unknown"


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_a_failed_channel_switch_surfaces_an_error_instead_of_vanishing_silently():
    """Review fix, Important 3: `setUpdateChannel` used to await its PATCH
    with no `try` at all, unlike every sibling method in this card
    (`applyUpdate`, `resyncAll`, `downloadFabricBackup`, ...). A rejected
    request became an unhandled promise rejection straight out of the
    `@click` handler: nothing shown, `updateStatus.channel` silently
    left as it was, no way for whoever clicked to tell the click did
    anything.

    Before the fix, this test's own node process never printed its JSON:
    the rejection propagated out of the async IIFE below uncaught, and
    node exits non-zero for that - `_app_state`'s own
    `assert result.returncode == 0, result.stderr` is what turns that
    into a failing assertion here."""
    values = _app_state(
        """
        state.updateStatus = { channel: "stable" };
        state.updateError = null;
        state.request = async (method, path) => {
          if (method === "PATCH" && path === "/api/update/settings") {
            throw new Error("boom");
          }
          throw new Error("unexpected request " + method + " " + path);
        };
        state.loadUpdateCheck = async () => {
          throw new Error("loadUpdateCheck must not run after a failed PATCH");
        };
        (async () => {
          await state.setUpdateChannel("dev");
          console.log(JSON.stringify({
            updateError: state.updateError,
            channel: state.updateStatus.channel,
          }));
        })();
        """
    )

    assert values["updateError"] == "boom"
    # The failed PATCH must not have silently taken effect either.
    assert values["channel"] == "stable"


@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_an_expired_session_stops_the_update_poll_instead_of_retrying_forever():
    """Review fix, Important 4: `loadUpdateStatus()` never checked
    `this.authenticated`. A 401 mid-job was already caught, but since
    `updateRunning()` still read `true` off the last good state fetched
    before the session died, neither branch below the `catch` noticed
    anything wrong: no error was set, and the timer - already
    running - was left exactly as it was, firing this same request every
    two seconds against a dead session until the page was reloaded by
    hand. `handleDiagnosticsDisconnect` states the identical rule one
    screen away for the diagnostics socket: an out-of-band 401 means
    back to the login screen, not a retry loop against a session that
    will never answer again.

    `this.request()` itself already flips `this.authenticated` to
    `false` synchronously before rethrowing (see `noteAuthError`) - the
    stub below reproduces exactly that side effect rather than going
    through the real fetch/`UnauthorizedError` stack, the same shortcut
    `test_deselect_all_empties_the_selection_instead_of_inverting_it`
    above takes for `toggleExported`. Before the fix, `timerStopped`
    below read `False`: the interval created ahead of the call was still
    running afterwards."""
    values = _app_state(
        """
        state.authenticated = true;
        state.updateStatus = {
          state: { phase: "pull", id: "j1", from: "1.0.0", to: "1.1.0",
                   error: null, rolled_back: false, healthy: true },
          updater_present: true, log: [], channel: "stable", check_enabled: true,
        };
        // A real timer, not a placeholder value: `stopUpdateTimer()`
        // calls `clearInterval` on it, and this proves that call is
        // actually reached rather than merely that `updateTimer` gets
        // reassigned to `null` by some other path.
        state.updateTimer = setInterval(() => {}, 999999);
        state.request = async () => {
          state.authenticated = false;
          state.authError = "session expired";
          throw new Error("session expired");
        };
        (async () => {
          await state.loadUpdateStatus();
          console.log(JSON.stringify({
            timerStopped: state.updateTimer === null,
            updateError: state.updateError,
            authenticated: state.authenticated,
          }));
        })();
        """
    )

    assert values["timerStopped"] is True
    assert values["authenticated"] is False
    # No update-specific error either: the login screen (`authError`,
    # already set above the way `noteAuthError` sets it for real) is the
    # whole answer, not a second, contradictory message under a card the
    # login screen has just covered.
    assert values["updateError"] is None


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_signal_helpers_tolerate_null_without_throwing():
    """Formerly `test_a_device_without_a_lead_signal_does_not_throw_in_any_binding`.

    The trigger was the primary signal: between `GET /api/devices` and
    `GET /api/devices/<id>/signals` there is a rendering pass in which
    `signalsByDevice` for the device is still EMPTY - `leadSignalFor`
    then returned `null`. The `x-show` on the wrapper did not help: it
    only sets `display`, it does NOT stop Alpine from evaluating the
    children's expressions. So the three helpers read `signal.key` on
    `null` and threw - three times per device, on every pass.

    This caller no longer exists (design 2026-09-07): the `x-for`
    of the value grid runs over an empty list and evaluates nothing
    at all. The helpers' tolerance nonetheless stays in place and is
    still checked here - the test now asks them directly instead of via
    a caller that no longer exists.
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


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_groups_follow_the_ranking_not_the_endpoint_number():
    """The reason for the groups: `press` appears twice in the list -
    1/59/1 and 2/59/1, i.e. two different buttons of the same
    remote. Without a group, that is the same word twice with no clue
    which one is meant.

    And the order: "Geraet" (endpoint 0, only the battery) comes
    LAST, even though it carries the lowest endpoint number - the groups
    take over the order of first occurrence in the already
    ranked list, they do not sort by themselves. This is exactly what a
    string search in `app.js` cannot prove.

    `t()` returns the key itself when no translation table is loaded
    (see `t` in app.js) - the title of the expert group here is
    therefore the key, and that is enough for the assertion."""
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
    # Structuring 156 signals across all endpoints would only produce more
    # headings - the expert block stays ONE collapsed group.
    assert values[3]["signals"] == ["d1_0_vendor"]
    assert values[3]["collapsible"] is True
    assert [g["collapsible"] for g in values[:3]] == [False, False, False]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_device_without_functional_signals_yields_only_the_expert_group():
    """The state for which the `none_functional` notice now sits OUTSIDE
    the group loop: an endpoint group is never empty, in that case
    there simply is none."""
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
    """Follow-up 2026-09-07, finding 3: the rework to the value grid (design
    2026-09-05) removed the line `assert values["lead"] == "d1_1_onoff"`
    from `test_a_signal_that_exists_is_unaffected_by_the_guard` - formally a
    statement about the now-deleted `leadSignalFor`. In fact, though,
    that was the only assertion in the repo that ran `firstSignalsFor` in a
    real `node` process. `test_the_value_grid_now_carries_every_
    functional_signal` (tests/api/test_web.py) has since only proven that
    `x-for="signal in firstSignalsFor(device.id)"` is shipped as a string
    - not what the helper itself does. This test closes the
    gap on its own, instead of bolting it onto a test with a
    different purpose.

    All three responsibilities of `firstSignalsFor` /
    `remainingSignalCount` are checked together (app.js): non-functional
    signals fall through the `signal.functional` filter, the order of
    the input list is preserved (no sorting, no reshuffling), and
    once there are more than `FUNCTIONAL_PREVIEW_LIMIT` (6) functional
    signals, `firstSignalsFor` returns exactly six, while
    `remainingSignalCount` counts the rest."""
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

    # Seven of the nine signals are functional (s1, s3, s4, s6, s7, s8, s9);
    # s2 and s5 must stay out, and the order of the remaining
    # seven stays that of the input list - no sorting by key
    # or anything else.
    assert values["first"] == ["s1", "s3", "s4", "s6", "s7", "s8"]
    # Seven functional signals, capped at six: exactly one is left over.
    assert values["remaining"] == 1


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_live_values_are_shown_with_at_most_two_decimal_places():
    """The live values come from Matter attributes as integer
    hundredths; converting them inherits the floating-point fuzz, and
    `String(value)` used to write them into the tile unabridged
    (22.529999999999998 for 22.53). Two decimal places are the
    precision the device delivers in the first place - everything
    beyond that is noise that blows up the column.

    What is checked here is the BEHAVIOR, not the shipped text: whether
    it rounds or truncates and what happens with a clean value is not
    in any string that one could search for in `app.js`.
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

    assert values[0] == "22.53", "the floating-point fuzz disappears"
    assert values[1] == "21", "a clean value gets no zeros appended"
    assert values[2] == "21.5", "a single decimal place stays one"
    assert values[3] == "21.01", "it rounds, it does not truncate"
    assert values[4] == "-3.14"
    assert values[5] == "1234.57"
    assert values[6] == "0"
    # What is not a number is also not treated as one: parsing a
    # string from the live connection would mean guessing which
    # part of it is supposed to be a number.
    assert values[7] == "22.5299"
    # And the two special paths of `formatValue` stay as they were
    # (the translation table is not loaded in node, so the key
    # appears here instead of "wahr"/"falsch" - see
    # `test_formatting_helpers_translate_and_the_locale_follows_the_language`
    # for the translation itself).
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
    """Design "pairing code: write it the way it stands on the device"
    (2026-09-07): the field writes the dashes along as you type,
    names on the right of the field what it has recognized, and the
    card sends off the NORMALIZED rather than the merely trimmed value.

    For the entire rework there was previously exactly ONE assertion in
    this file (on `commissionRunCode`, see
    `test_commission_device_drives_the_flow_and_stops_where_it_failed`).
    Nothing verified that `@input` is still attached to the field, that
    the chip exists, or that `commissionDevice` really sends the
    normalized rather than the trimmed value - anyone who lost that while
    reshuffling code would otherwise still see nothing but green tests."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    code_start = markup.index('<div class="code-field">')
    code_field = markup[code_start : markup.index("</div>", code_start)]
    assert '@input="formatCommissionCode($event.target)"' in code_field
    assert '@keydown="commissionCodeKeydown($event)"' in code_field
    assert 'class="code-detect"' in code_field
    assert ":class=\"'tone-' + commissionCodeBadge().tone\"" in code_field
    assert 'x-text="commissionCodeBadge().text"' in code_field

    # The example line with the two code forms that replaces the
    # parenthetical addition in the earlier placeholder.
    example_start = markup.index('<p class="code-examples"')
    example_block = markup[example_start : markup.index("</p>", example_start)]
    assert "x-text=\"t('web.devices.code_example_manual')\"" in example_block
    assert "x-text=\"t('web.devices.code_example_qr')\"" in example_block

    script = (await client.get("/static/app.js")).text
    # `commissionDevice` sends off the NORMALIZED value, no longer
    # `this.commissionCode.trim()` - the separators the field itself
    # inserted while typing do not belong in the Matter stack.
    assert "const code = normalizePairingCode(this.commissionCode);" in script
    assert "const body = { code };" in script

    # The four pure functions, at module level, checkable without a
    # loaded translation table.
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
    # Step 1 comes BEFORE the reload, step 2 after it.
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


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
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

    # `saveLabel` shares its write path with `saveGroupLabel` since the fix
    # of 2026-09-11 (`saveEntityLabel`) - the error field it writes into is
    # now a runtime parameter (`this[errorField] = ...`), so the OLD literal
    # text `this.deviceActionError = t("web.devices.label_save_error", ...)`
    # no longer appears anywhere in the script, on purpose. Running the real
    # `saveLabel` proves the same thing a source-text search used to: a
    # failed save shows the translated `label_save_error` text through
    # `deviceActionError`, not the German original.
    values = _app_state(
        """
        state.request = async () => { throw new Error("boom"); };
        state.labelDrafts[1] = "New name";
        (async () => {
          await state.saveLabel({ id: 1, label: "Lamp" });
          console.log(JSON.stringify({ error: state.deviceActionError }));
        })();
        """,
        translations={"web.devices.label_save_error": "Could not save name: {message}"},
    )
    assert values["error"] == "Could not save name: boom"
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
    carries a `t(...)` call with `label` and `id` instead of a hand-built
    template string - the dialog itself cannot be checked without a
    browser engine, but that its text now comes from the translation
    table can be.

    Final fix pass, item 2: the call used to be `window.confirm(t(...))`
    inline; it now builds `confirmText` first so the groups note (see
    `test_removing_a_device_names_its_groups_in_the_confirmation` below)
    can be appended before the dialog opens. The base text must still
    come from `t("web.devices.remove_confirm", ...)` with exactly `label`
    and `id`, unconditionally - not only when `confirmText` happens to get
    extended afterward."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert (
        'let confirmText = t("web.devices.remove_confirm", { label: device.label, id: device.id });'
        in script
    )
    assert "window.confirm(confirmText)" in script
    assert "wirklich entfernen? Das kann nicht rückgängig gemacht werden" not in script
    assert "In Loxone bleiben danach verwaist" not in script
    assert "Es gehört außerdem zu" not in script


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_removing_a_device_in_no_group_shows_the_confirmation_unextended():
    """Final fix pass, item 2: a device in no group at all is the common
    case, and it must see EXACTLY the careful wording
    `web.devices.remove_confirm` already had - no trailing "it also
    belongs to: " clause naming nothing. `state.groups` here holds a group
    whose `member_ids` does NOT include this device, so the fix must
    filter by membership, not merely by "are there any groups at all"."""
    values = _app_state(
        """
        let confirmText = null;
        global.window = { confirm: (message) => { confirmText = message; return false; } };
        state.groups = [
          { id: 1, label: "Ceiling", room: null, category: "light",
            member_ids: [5], member_labels: ["Other lamp"], command_count: 1 },
        ];
        state.request = async () => { throw new Error("must not run: confirm was declined"); };
        (async () => {
          await state.removeDevice({ id: 9, label: "Lamp" });
          console.log(JSON.stringify({ confirmText }));
        })();
        """,
        translations={
            "web.devices.remove_confirm": "remove {label} ({id})?",
            "web.devices.remove_confirm_groups_note": "it also belongs to: {groups}",
        },
    )
    assert values["confirmText"] == "remove Lamp (9)?"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_removing_a_device_names_its_groups_in_the_confirmation():
    """Final fix pass, item 2: removing a device is a membership change in
    every group it belongs to (design 4.3, `register_group_commands`) - if
    it was the only member carrying a command, that command drops out of
    the group's intersection and the matching key answers 404 in Loxone
    from then on, with nothing in the old confirmation ever saying so.
    This device (id 9) is a member of both groups in `state.groups`
    (`member_ids` includes 9 in each) - the note must name BOTH, and the
    base wording from `web.devices.remove_confirm` must still be there,
    unextended in itself, with the note appended after it rather than
    mixed into it."""
    values = _app_state(
        """
        let confirmText = null;
        global.window = { confirm: (message) => { confirmText = message; return false; } };
        state.groups = [
          { id: 1, label: "Ceiling", room: null, category: "light",
            member_ids: [9], member_labels: ["Lamp"], command_count: 1 },
          { id: 2, label: "Reading corner", room: "Living room", category: "light",
            member_ids: [3, 9], member_labels: ["Other", "Lamp"], command_count: 2 },
        ];
        state.request = async () => { throw new Error("must not run: confirm was declined"); };
        (async () => {
          await state.removeDevice({ id: 9, label: "Lamp" });
          console.log(JSON.stringify({ confirmText }));
        })();
        """,
        translations={
            "web.devices.remove_confirm": "remove {label} ({id})?",
            "web.devices.remove_confirm_groups_note": "it also belongs to: {groups}",
        },
    )
    assert values["confirmText"] == (
        "remove Lamp (9)?\n\nit also belongs to: Ceiling, Reading corner"
    )


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
    """Task 12, step 4: `signalGroupsFor`'s group titles (object
    literals) go through `t(...)` instead of the fixed literals "Funktional"
    / "Experte".

    Task 6 changes WHICH object literals those are: the endpoint groups
    carry `signal.endpoint_label` as their title (already comes translated
    from the API, see task 4) and `t("web.signals.group_endpoint_subtitle")`
    as a subtitle; only the expert group still has a title set fixed via
    `t(...)`. The `group_functional` key no longer exists since
    then. The original assertion - no hardcoded German literal in the
    group construction - is preserved: the ban on
    `"Experte"` as a literal remains, the one on `"Funktional"` disappears
    along with the title that no longer exists."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    signal_groups_start = script.index("signalGroupsFor(deviceId) {")
    signal_groups_end = script.index("\n    },", signal_groups_start)
    body = script[signal_groups_start:signal_groups_end]
    assert 'subtitle: t("web.signals.group_endpoint_subtitle"' in body
    assert 'title: t("web.signals.group_expert")' in body
    assert '"Experte"' not in body


async def test_the_group_header_shows_the_endpoint_as_a_subtitle(api):
    """Task 6, step 5: the group's `<summary>` now also shows
    `group.subtitle` ("Endpunkt N") next to the title and count."""
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
    assert 't("web.export.projectsync_unassigned_device_label")' in grouped_body
    assert 't("web.export.projectsync_group_label", { label: rawLabel })' in grouped_body
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


async def test_the_group_changed_pill_is_wired_into_the_export_tabs_group_table(api):
    """Item 3 of the final polish pass (2026-09-11): `groupChangedSinceExport`
    used to be defined and called by nothing. The group tile in the
    devices grid deliberately carries no export footer of its own (see
    the group-tile markup comment further up in `index.html`), so the
    export tab's group table is a group's only home for "changed since
    export" at all - this proves the DELIVERED markup actually wires that
    row to it, with the same pill classes (`status-pill warn`), the same
    icon (`#i-warn`), and the same wording key
    (`web.devices.changed_since_export`, reused rather than a second
    string invented for groups) as the device tile's own pill - not a
    copy-pasted `changedSinceExport(device.id)` left over from that
    tile."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    groups_heading_pos = markup.index("t('web.export.groups_heading')")
    pill_pos = markup.index("x-text=\"t('web.devices.changed_since_export')\"", groups_heading_pos)
    pill_open = markup.rindex('<span class="status-pill warn"', groups_heading_pos, pill_pos)
    pill_tag = markup[pill_open : markup.index(">", pill_open)]
    assert "groupChangedSinceExport(group.group_id)" in pill_tag

    # Same subject-predicate order as the device tile's footer: the
    # timestamp is named first, the pill immediately after.
    timestamp_pos = markup.rindex(
        "groupExportedAtFor(group.group_id)", groups_heading_pos, pill_open
    )
    assert timestamp_pos < pill_open


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_changed_group_shows_the_marker_a_re_exported_one_does_not(api):
    """The behavioural counterpart of the markup-wiring test above: runs
    the REAL `updated_at`/`exported_at` comparison end to end -
    `Store.changed_since_export` (already exhaustively exercised in
    `test_export_api.py`) through `GET /api/export/status`, shaped
    exactly as `loadExportStatus()` shapes `exportStatusByGroup` in
    `app.js` - into the real `groupChangedSinceExport` running in node.
    Not a hand-typed status object, which could only ever prove agreement
    with itself, and not a second implementation of the comparison either
    - it is the same one `_group_changed_since_export` (api/export.py)
    calls."""
    client, store, device_id = api
    await authenticate(store, client)
    group = store.create_group("Solo", [device_id])

    # Exported once, nothing has touched `updated_at` since: unchanged.
    download = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    assert download.status_code == 200
    status = (await client.get("/api/export/status")).json()
    unchanged_entry = next(e for e in status if e.get("group_id") == group.id)
    assert unchanged_entry["changed_since_export"] is False

    # `set_group_members` stamps `updated_at` (design 4.3: the
    # intersection depends on who the members are) - later than the
    # `exported_at` set above, so the group now reads as changed.
    store.set_group_members(group.id, [device_id])
    status = (await client.get("/api/export/status")).json()
    changed_entry = next(e for e in status if e.get("group_id") == group.id)
    assert changed_entry["changed_since_export"] is True

    values = _app_state(
        f"""
        state.exportStatusByGroup = {{ {unchanged_entry["group_id"]}: {json.dumps(unchanged_entry)} }};
        const unchanged = state.groupChangedSinceExport({unchanged_entry["group_id"]});
        state.exportStatusByGroup = {{ {changed_entry["group_id"]}: {json.dumps(changed_entry)} }};
        const changed = state.groupChangedSinceExport({changed_entry["group_id"]});
        console.log(JSON.stringify({{ unchanged, changed }}));
        """
    )
    assert values == {"unchanged": False, "changed": True}


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
    """Finding 1 (review from 2026-09-05), followed up on 2026-09-07: since
    the removal of `.lead-value`, the name has the header row to itself
    and only truncates for exceptionally long names. That it then does
    so VISIBLY remains the assertion of this test. An
    `<input>` clips its text internally as soon as it does not fit, and
    does so WITHOUT any character indicating that text is missing, as
    long as no `text-overflow` is set - measured in the browser at a
    261px tile width: name 65px, cut off mid-letter, where `overflow:
    hidden` plus `text-overflow: ellipsis` instead truncate visibly
    (see the task report). Without a browser engine, this suite cannot
    verify the truncation itself - it only proves that the
    shipped rule carries both properties."""
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
    only excludes `null` (no longer `""`), and that `saveRoom`,
    `saveGroupRoom` and `commitRenameRoom` call it after their respective
    write - not that Alpine then actually switches to the "All" chip (that
    would need a browser engine, see
    `test_the_page_does_not_call_init_a_second_time`).

    `saveRoom` and `saveGroupRoom` share one write path since the fix of
    2026-09-11 (`saveEntityRoom`, see the fix report): the `finally {
    reconcileRoomFilter() }` that used to sit, duplicated, at the end of
    each of them now sits once, in the shared function. A source-text
    search anchored on `async saveRoom(device, value) {` would no longer
    find that call inside the substring it cuts out (`saveRoom`'s own body
    is now a one-line delegation) and would prove nothing either way -
    running the real code instead proves the call still fires for both
    kinds of tile, regardless of where in the file it is written."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text

    reconcile_start = script.index("reconcileRoomFilter() {")
    reconcile_end = script.index("\n    },", reconcile_start)
    reconcile_body = script[reconcile_start:reconcile_end]
    assert "this.roomFilter === null" in reconcile_body
    assert 'this.roomFilter === ""' not in reconcile_body
    assert 'typeof this.roomFilter !== "string"' not in reconcile_body
    assert "this.roomFilter = null;" in reconcile_body

    rename_start = script.index("async commitRenameRoom() {")
    rename_end = script.index("\n    },", rename_start)
    rename_body = script[rename_start:rename_end]
    assert "this.reconcileRoomFilter();" in rename_body

    # The room-save half of this guard runs the real code in node, and it
    # must NOT become a silent no-op when node is missing. It replaced a
    # source-text assertion that ran everywhere, so `if NODE is not None`
    # would leave the 2026-09-05 finding's only remaining guard inert while
    # the test still reported green - which is precisely how that finding
    # would come back unnoticed. A skip says so out loud; the assertions
    # above it have already run by this point.
    if NODE is None:
        pytest.skip("node is required for the saveRoom/saveGroupRoom half of this guard")

    values = _app_state(
        """
        const calls = [];
        state.reconcileRoomFilter = () => calls.push("reconcile");
        state.request = async () => ({ id: 1, room: "Kitchen" });
        (async () => {
          await state.saveRoom({ id: 1, label: "Lamp" }, "Kitchen");
          await state.saveGroupRoom({ id: 1, label: "Ceiling" }, "Kitchen");
          console.log(JSON.stringify({ calls }));
        })();
        """
    )
    assert values["calls"] == ["reconcile", "reconcile"], (
        "saveRoom and saveGroupRoom must both call reconcileRoomFilter() "
        "after their write, device and group alike"
    )


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_commit_rename_room_offers_the_merge_confirmation_for_a_group_only_room():
    """Final fix pass, item 1: `commitRenameRoom`'s merge check used to read
    `this.devices.some((device) => device.room === name)` - a room carried
    by nothing but a GROUP (design 6: a group's room is its own field, not
    derived from members) was therefore invisible to it. Renaming into
    such a name merged two rooms with no confirmation at all - the one
    dialog this flow exists to show never appeared.

    `state.devices` is deliberately empty here: the target name "Neu" is
    carried only by the group in `state.groups`, so a fix that still only
    checks `devices` would find no match, skip `window.confirm` entirely,
    and let `state.request` (which throws) run straight through - the
    thrown error would surface as `deviceActionError` and this assertion
    would fail with a wrong message rather than a wrong flow. Declining
    the confirmation (`confirm: () => false`) then proves the rest of the
    guard still holds: an unwanted merge must not fire the rename at all,
    so `state.request` must stay uncalled."""
    values = _app_state(
        """
        state.devices = [];
        state.groups = [
          { id: 1, label: "Ceiling", room: "Neu", category: "light",
            member_ids: [], member_labels: [], command_count: 0 },
        ];
        state.renamingRoom = "Kueche";
        state.renameDraft = "Neu";
        let confirmCalled = false;
        global.window = { confirm: () => { confirmCalled = true; return false; } };
        state.request = async () => { throw new Error("must not run: merge was declined"); };
        (async () => {
          await state.commitRenameRoom();
          console.log(JSON.stringify({
            confirmCalled,
            renamingRoom: state.renamingRoom,
            error: state.deviceActionError,
          }));
        })();
        """,
        translations={"web.devices.room_rename_merge_confirm": "merge?"},
    )
    assert values["confirmCalled"] is True
    # The field stays open on decline (see the comment in `commitRenameRoom`
    # right above the `window.confirm` call) - `renamingRoom` therefore
    # keeps pointing at the room being renamed, not `null`.
    assert values["renamingRoom"] == "Kueche"
    assert values["error"] is None


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_commit_rename_room_reloads_groups_after_a_successful_rename():
    """Final fix pass, item 1: `Store.rename_room` now writes
    `device_group.room` alongside `device.room` (see the store-side tests
    next to `Store.rename_room`), but the WebUI still needs telling - the
    group list it already holds in `state.groups` was fetched before the
    rename and does not update itself. Before this fix `commitRenameRoom`
    called `loadDevices()` only, so a group's tile and the chip bar kept
    showing the OLD room name for that group until some unrelated action
    happened to trigger a full reload."""
    values = _app_state(
        """
        const calls = [];
        state.devices = [];
        state.groups = [];
        state.renamingRoom = "Kueche";
        state.renameDraft = "Essbereich";
        state.roomFilter = null;
        state.request = async () => { calls.push("rename"); return { renamed: 1 }; };
        state.loadDevices = async () => { calls.push("loadDevices"); };
        state.loadGroups = async () => { calls.push("loadGroups"); };
        state.reconcileRoomFilter = () => { calls.push("reconcile"); };
        (async () => {
          await state.commitRenameRoom();
          console.log(JSON.stringify({ calls }));
        })();
        """
    )
    assert values["calls"] == ["rename", "loadDevices", "loadGroups", "reconcile"], (
        "commitRenameRoom must reload groups (not just devices) after a "
        "successful rename, so a group's room reflects the write it just made"
    )


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

    Analogous for signals: `web.devices.no_functional_signals` was
    also deleted, causing a tile with loaded but empty
    functional signals (back then: `leadSignalFor` returns `null`; the
    condition for it is today called `functionalSignalsFor(id).length === 0`,
    `leadSignalFor` no longer exists since the primary signal was removed)
    to silently show a gap between the header row and the command bar -
    indistinguishable from a tile still loading.

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

    # `controlsLoaded(subject)`, not `(deviceId)`, since the group tiles
    # (design 2026-09-10): the parameter is a control SUBJECT - a device id
    # or a group's "g3" - and the function body is unchanged. Only the
    # anchor moved; what this line proves is still that the method exists
    # with a body, nothing about the parameter's name.
    controls_loaded_start = script.index("controlsLoaded(subject) {")
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
    """Design section 4: ONE `<dialog>` per purpose for the whole page,
    not one per tile.

    Markup inside `x-for` is shipped once PER DEVICE - with thirty
    devices there would be thirty complete signal tables in the
    document, and every `id` in it thirtyfold (the same pitfall that
    `aria-labelledby` in the tile menu already had to dodge once). The
    count (one signal modal, one control modal from task 7, one group
    dialog since the device groups of 2026-09-10) is the only assertion
    that would even notice this regression: a `<dialog>` inside the tile
    would otherwise look exactly the same in the shipped text as one at
    the end of the page. The group dialog is the case in point - it holds
    a checkbox per device, so inside the group `x-for` it would be
    shipped once per group.

    The location check (after `</main>`) additionally proves that all
    three sit outside the view sections and thus outside any
    device loop."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert markup.count("<dialog") == 3
    assert 'x-ref="signalsModal"' in markup
    assert 'x-ref="controlModal"' in markup
    assert 'x-ref="groupDialog"' in markup
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

    The `offsetX < clientWidth` condition that used to sit here is
    replaced, not supplemented: it only separated out a scrollbar that
    RESERVES SPACE, and came up empty for an overlay one (macOS
    default, measures 0px) - on top of that, it covered neither a
    horizontal bar nor an RTL layout. The rectangle comparison
    in `isBackdropEvent` needs none of these case distinctions; if
    `offsetX` ever shows up here again, that is a regression.

    Finding 3 (re-check, 2026-09-07): the same `@close` handler has
    since additionally reset `expandedSignalKey` - dedicatedly checked in
    `test_closing_the_signals_modal_resets_the_open_detail` further below;
    here only `signalsModalDevice = null` continues to be counted, to
    prove exactly ONE reset spot for this field."""
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
# Task 5: The battery row of the tile. Counterweight to the cluster
# ranking (task 4): at rank 90, the battery level would come after all
# sixteen other functional signals of the button and would thus fall out
# of the six preview rows - it would no longer be visible on the tile at all.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_battery_never_opens_the_value_grid_and_is_never_counted_twice():
    """The three assertions of the battery row in ONE setup, because they
    belong together: the battery level does not lead, it is not in the
    preview, and it does not count as "more".

    The setup is the button: 17 functional signals in the order the
    cluster ranking delivers them - sixteen switch signals,
    the battery last. Six preview rows plus one footer row leave
    ten remaining. If the tile says eleven, the battery is counted twice -
    exactly the bug the canvas design had.

    As a node run instead of a string search in `app.js`: a search
    only proves THAT a row is shipped. On 2026-09-05, three
    such tests let a critical bug through because they checked exactly
    the strings that produced the bug."""
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


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_mains_powered_device_has_no_battery_row():
    """Without a PowerSource signal, the tile must not show a footer row -
    and the counter must behave exactly as it did before this change."""
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


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_device_whose_only_functional_signal_is_the_battery_shows_an_empty_grid():
    """The edge case where the "no functional signals" hint would be
    wrong: there IS one, it just sits in the footer row.

    The value grid is empty here even though `functionalSignalsFor`
    returns a signal - `previewSignalsFor` takes the battery level out
    after all. That is exactly why the hint in the markup is tied to
    `functionalSignalsFor` and not to the grid rows, see
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


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_device_with_two_power_source_endpoints_keeps_both_out_of_the_grid():
    """The edge case of a composite device or a bridge with two batteries
    under one record: two signals on cluster 47, on different endpoints.

    `batterySignalFor` picks only the first-ranked one of these via
    `.find()` - the second one would, if the preview set only excludes
    THIS one (key comparison instead of cluster filter), remain
    unnoticed in the preview and could end up as the primary signal.
    Assertion here: the primary signal is the useful signal, no signal
    of the preview set carries `cluster_id === 47`, and
    `batterySignalFor` returns the first-ranked of the two
    PowerSource signals."""
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
    """The hint must be tied to `functionalSignalsFor`, not to the
    grid rows.

    The reason is the battery row: `previewSignalsFor` takes the
    battery level out of the value grid, so a device with ONLY a
    battery signal has zero rows there for one functional signal.
    Asked via the grid rows, the hint would then claim "no
    functional signals", while the footer row below shows one.

    The condition used to ask `!leadSignalFor(device.id)` and, after the
    battery rework, `!leadSignalFor(...) && !batterySignalFor(...)`. With
    the removal of the primary signal, both are moot -
    `functionalSignalsFor` answers the same question directly and covers
    the battery edge case on its own."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    hint = page[page.index("no_functional_signals") - 400 : page.index("no_functional_signals")]
    assert "functionalSignalsFor(device.id).length === 0" in hint
    # Neither via the grid rows nor via the removed primary signal.
    assert "firstSignalsFor" not in hint
    assert "leadSignalFor" not in hint


async def test_the_signal_rows_and_the_header_share_one_grid(api):
    """What is missing today and why nothing lines up: the row is a
    `flex-wrap` container with no column dimensions. With `multipress_ongoing`,
    "periodisch erneut senden" slid into the next line by itself.

    200px instead of the previous 150px for the key column (task 13,
    finding: the old comment claimed a measurement that never took place -
    150px overflowed for `d4_1_multipress_ongoing`, the actually longest
    key of the four demo devices, measured at 183px scrollWidth against
    148px clientWidth). This assertion MUST be carried along, otherwise
    the rework would have left a header row and a data row with different
    templates - exactly the divergence this test exists to
    block.

    Finding (final review): `page.count("signal-grid") >= 2` only counts
    a substring. `class="signal-grid signal-grid-head"` alone
    produces two hits, because "signal-grid-head" starts with "signal-grid"
    - the data row could thus lose its grid class entirely
    (the starting state of the rework) and the counting assert would stay
    green. Likewise, the second assertion only checked that the column
    dimensions appear SOMEWHERE in the CSS, not that the header AND the
    row get them from the same rule - a later
    `.signal-row-cells { grid-template-columns: ... }` override would have
    stayed invisible. This version requires both classes individually on
    their respective element and ties the column dimensions to the rule
    body of `.signal-grid` alone, with a counter-check that neither of the
    two modifier classes defines them again itself outside the
    media query.

    Finding (re-check before the merge): `rule_body` used `css.index`,
    which only finds the FIRST rule with this selector. A later override
    of the same class - exactly the case this counter-check is meant to
    guard against - sits at the end of the file though, and wins the
    cascade there, regardless of whether the first version is clean.
    Proof: appending `.signal-row-cells { grid-template-columns: 40px 1fr; }`
    to the end of `style.css` leaves the old version of this test green,
    even though the header and data row demonstrably have different
    column dimensions after that. `rule_bodies_outside_media` therefore
    collects ALL rule bodies for a selector (after removing the `@media`
    blocks in which `.signal-grid` deliberately carries a different
    template for the narrow case), and the counter-check verifies each
    one."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    css = (await client.get("/static/style.css")).text

    assert 'class="signal-grid signal-grid-head"' in page
    assert 'class="signal-grid signal-row-cells"' in page

    def _without_media_queries(css: str) -> str:
        """`css` with every `@media` block removed, stripped by brace
        counting - a plain `css.index("}")` would stop at the FIRST rule
        inside the block, not at the block's end, because rules
        themselves also carry `{ }`."""
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
        """ALL rule bodies for `selector` outside every media query -
        not only the first one (see the docstring above: an override at
        the end of the file would otherwise stay invisible)."""
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
    # In EVERY version of `.signal-grid-head`/`.signal-row-cells` outside
    # the media query, the column dimensions must not appear
    # independently, otherwise the header and row could drift apart via
    # their own separate rule without this catching it.
    for body in rule_bodies_outside_media(".signal-grid-head {", css_outside_media):
        assert "grid-template-columns" not in body
    for body in rule_bodies_outside_media(".signal-row-cells {", css_outside_media):
        assert "grid-template-columns" not in body


async def test_the_key_pill_wraps_instead_of_touching_the_value_column(api):
    """Finding (task 13): 200px is enough for today's keys, but
    `.key` itself had neither `white-space` nor `overflow` - an even
    longer key (e.g. a three-digit device ID) would blow up the
    column again, invisible to a measurement that only checks for
    diverging column edges. The safeguard deliberately sits on
    `.signal-grid .key`, not on `.key` globally: the same class also
    carries the firmware filenames in the diagnostics tab and the
    commissioning code in the header (`class="key"` elsewhere in
    `index.html`) - a global rule would have affected those too, without
    this test ever having seen it.

    `overflow-wrap: break-word` instead of `text-overflow: ellipsis`:
    `.key` carries `user-select: all`, the pill is meant for copying, and
    there is no second place in the signal modal that shows the same key
    unabridged. An ellipsis would be complete when copied, but a silent
    mutilation when read - hence wrapping, not truncation."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text

    # Bound to the rule body of its own selector (`.signal-grid
    # .key`), not to a keyword window - otherwise the test would stay
    # green even if `overflow-wrap` happened to sit in a neighboring,
    # unrelated rule.
    start = css.index(".signal-grid .key {")
    open_brace = css.index("{", start)
    close_brace = css.index("}", open_brace)
    rule = css[open_brace:close_brace]

    assert "overflow-wrap: break-word" in rule
    assert "text-overflow" not in rule


async def test_both_boolean_columns_are_checkboxes(api):
    """The control follows the CONTAINER, not the meaning: in a
    table, checkboxes, because they line up in a column and stay quiet -
    a column of 17 toggle switches would be a distinctly louder texture,
    and loudness is exactly this modal's problem."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    modal = page[page.index('class="signals-modal"') :]
    for handler in ("toggleExported(signal)", "toggleResend(signal)"):
        # The control that carries the handler: from the handler
        # backwards to the opening tag. This way, the test checks the
        # actual element and not just any `type="checkbox"`
        # elsewhere in the modal.
        end = modal.index(handler)
        element = modal[modal.rindex("<", 0, end) : end]
        assert 'type="checkbox"' in element, handler


async def test_the_boolean_columns_keep_a_label_for_assistive_technology(api):
    """The label sits as a column header once instead of seventeen times
    next to a checkbox - but a screen reader reads the row, not the
    table. Both checkboxes therefore still need their own name.

    Finding (final review): the old version was asymmetrically strict -
    for the export column it checks `:aria-label` directly ON the
    element, for resend a loose text search across the whole page was
    enough. The resend key appears there three times anyway (checkbox,
    hidden label for the narrow case, tooltip), so a deleted
    `:aria-label` on the resend checkbox itself would never have been
    noticed by the test. This version ties both columns down equally
    strictly: from the handler backwards to the element that carries it
    - the same pattern as in
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
    """Finding (final review): the old version only checked THAT the
    key appears somewhere on the page - neither "once" nor "above
    the table", even though the name promises exactly that. The
    explanation sentence could sit as a label next to each of the
    seventeen checkboxes - exactly the state the rework abolished -
    and the old assert would stay green, because it only sees existence.
    This version counts the hits (exactly one) and ties the position to
    the table: the sentence must come before the column header."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert page.count("web.signals.resend_explanation") == 1
    assert page.index("web.signals.resend_explanation") < page.index(
        'class="signal-grid signal-grid-head"'
    )


async def test_the_raw_write_field_lives_in_the_row_detail(api):
    """Successor to `test_the_raw_write_strip_still_works_alongside_the_grid`
    (task 7): the strip there was explicitly transitional, its
    final place is the disclosure from task 8. Control logic and
    handler stay unchanged, only the location and the visibility
    condition change - the latter now as an `isAttributeSignal(signal)`
    call instead of `signal.kind === 'attribute'` spelled out a second
    time. `test_the_raw_write_field_is_no_longer_a_row_of_its_own` adds
    the structural counter-check to this: the same field does NOT sit
    next to it as its own sibling of the grid row."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    detail = dialog[dialog.index('class="signal-detail"') :]

    assert 'x-show="isAttributeSignal(signal)"' in detail
    assert ":placeholder=\"t('web.signals.raw_write_placeholder')\"" in detail
    assert '@input="rawWriteDrafts[signal.key] = $event.target.value"' in detail
    assert '@click="writeRaw(signal)"' in detail
    assert ':disabled="rawWriteBusyKey === signal.key"' in detail
    assert "x-text=\"t('web.signals.raw_write_submit')\"" in detail


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_only_one_signal_detail_is_open_at_a_time():
    """Unlike the tile menu and the signal groups, this state lives in
    Alpine, not in the DOM: there is exactly ONE value for the whole
    modal, no open/closed per element.

    Finding (final review): the old version only checked two
    strings - that `expandedSignalKey` starts at `null` and that
    `this.expandedSignalKey = ` occurs SOMEWHERE in the body of
    `toggleSignalDetails`. A `toggleSignalDetails` shortened to
    `this.expandedSignalKey = signal.key` (without the comparison that
    makes it a real toggle) would only ever open the kebab and never
    close it - the kebab would then stay open forever once clicked
    once. Neither of the old asserts would have noticed that, because
    they only see text, not behavior. This version actually runs
    `toggleSignalDetails` (via `_app_state`, as with the loading guard
    above in this file) and checks the three cases that make up the
    toggle: twice on the same signal (open/close), then on a different
    signal (switch)."""
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
    # The second click on the SAME signal must close - exactly the
    # toggle half that a plain assignment would have snuck past.
    assert after_second_a is None, "a second click on the same signal must close it"
    assert after_third_a == "a"
    # A click on a DIFFERENT signal switches instead of opening a
    # second one - the state is a single value, not a set, so at most
    # one is open at a time.
    assert after_b == "b"


async def test_closing_the_signals_modal_resets_the_open_detail(api):
    """Finding 3 (re-check, 2026-09-07): `@close="signalsModalDevice = null"`
    used to leave `expandedSignalKey` untouched - if someone closed the
    modal with a disclosure open and then reopened the same device, the
    disclosure would immediately be open again, without the kebab having
    been clicked for it. Per the comment on the `<dialog>` (index.html),
    `@close` is the ONE closing path that ALL routes go through (Escape,
    backdrop, close button, `close()` from JavaScript via
    `closeSignalsModal()`) - the reset therefore belongs exactly there,
    not at a single closing spot."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    assert '@close="signalsModalDevice = null; expandedSignalKey = null"' in markup


async def test_the_raw_write_field_is_no_longer_a_row_of_its_own(api):
    """Finding 2 (re-check, 2026-09-07): this test previously only checked
    the presence/absence of a string
    (`'x-show="signal.kind === \\'attribute\\'"' not in page`) - no
    structure, even though the name promises "no longer its own row".
    This version verifies that structurally: the raw-write input field
    sits INSIDE the `.signal-detail` area, not as its own sibling next
    to the grid row (`.signal-grid.signal-row-cells`).

    Finding (final review): the second check window was `dialog[detail_
    start:]` - open at the BACK end all the way to the end of the whole
    dialog, not bounded to the end of `.signal-detail`. A raw-write field
    inserted ADDITIONALLY between the grid row and the disclosure - the
    old bug position from task 7 - therefore violated neither of the two
    assertions: the first window only reached to the grid row's own
    `</div>` (the new field sits AFTER that), the second only started at
    `class="signal-detail"` (the new field sits BEFORE that) - neither
    window saw the gap in between.

    This version closes the gap by having the first window reach up to
    `detail_start` instead of only to the grid row's own `</div>` -
    everything between the grid row and the disclosure now counts as
    "must not contain the field". The second window is additionally
    capped at `.signal-detail`'s own `</div>` (counting brace depth, the
    pattern from `test_the_signal_table_stacks_on_a_narrow_screen`,
    necessary because of the nested `<div class="row">` inside it)
    instead of reading open all the way to the end of the dialog -
    otherwise ANY later match in the rest of the dialog would have
    satisfied the assertion, even if the field had wandered OUT of
    `.signal-detail`.

    Not a pure repeat of `test_the_raw_write_field_lives_in_the_
    row_detail`: that test only checks presence within the (open)
    disclosure window, this one adds to that the absence between the
    grid row and the disclosure AND the strict upper bound -
    both halves together guarantee "no longer its own sibling"."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))

    row_start = dialog.index('class="signal-grid signal-row-cells"')
    detail_start = dialog.index('class="signal-detail"', row_start)
    # Not just the grid row itself, but EVERYTHING up to the disclosure -
    # the old bug position was a raw-write field as its own sibling
    # EXACTLY in between, and a window ending at the grid row would not
    # have seen that.
    assert "raw_write_placeholder" not in dialog[row_start:detail_start]

    # Cap at .signal-detail's own </div> instead of reading open to the
    # end of the dialog. Inside sits a nested
    # <div class="row"> (the raw-write field itself) - hence counting
    # brace depth instead of simply taking the next </div>.
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
    assert depth == 0, "no closing </div> found for .signal-detail"
    detail = dialog[detail_start:pos]

    assert "raw_write_placeholder" in detail


async def test_the_detail_spells_out_the_path(api):
    """The path `1/59/1` finally gets a place where there is enough
    room to spell it out, instead of putting it as a riddle next to the
    name.

    Finding 1 (re-check, 2026-09-07): the parsing of the path
    (`signal.path.split('/')[2]` for the element) used to sit as an
    expression right in the markup - knowledge about the path format that
    would have to be searched for in the markup on a format change,
    instead of in one place in `app.js`. A test that only checks
    `GET /` would not have seen a helper in `app.js` - this test
    therefore, like `test_only_one_signal_detail_is_open_at_a_time`,
    checks `app.js` directly and additionally the binding in the
    markup."""
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
    """Mandatory result from checking task 7: the warning pill with
    `signal.reason` used to sit in the 28px grid column and wrap there
    for every device with a TEXT- or list-valued signal (`.badge` had no
    `overflow-wrap`, `.signal-grid > * { min-width: 0 }` let the cell
    shrink) - measured at 744px `scrollWidth` against 719px `clientWidth`.
    The pill now lives in the disclosure; the grid row carries only the
    kebab button in its last cell.

    Finding (final review): the old version only checked the absence
    of TWO concrete strings (the warning pill, `signal.reason`) - the
    name promises "only the kebab", though, not "not these two things".
    Any arbitrary THIRD element in the last cell (a new badge,
    an icon, a second button) would have gone unnoticed. This version
    cuts the last cell exactly: everything after the resend cell (the
    last known sibling before it) up to the end of the grid row must
    be EXACTLY one element, and that must be the kebab button."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))

    # Inside the grid row there is no nested `<div>` (cells
    # are `<label>`, `<input>`, `<span>`, `<button>`) - the next
    # `</div>` therefore closes exactly this row, not some child element.
    row_start = dialog.index('class="signal-grid signal-row-cells"')
    row_end = dialog.index("</div>", row_start)
    row = dialog[row_start:row_end]

    assert "signal-more" in row

    # From the resend cell (the last known sibling before the last
    # column) to the end of the row, ONLY the kebab button may remain -
    # no warning pill, no other element, whatever it may be called.
    resend_close = row.index("</label>", row.index("toggleResend(signal)"))
    tail = row[resend_close + len("</label>") :]
    assert tail.lstrip().startswith("<button"), tail
    button_close = tail.index("</button>") + len("</button>")
    assert tail[:button_close].count('class="signal-more"') == 1
    assert tail[button_close:].strip() == "", tail[button_close:]


async def test_the_pill_fix_sits_on_the_pill_not_on_the_container(api):
    """Finding (re-check before the merge): an earlier attempt fixed
    the stretched warning pill (`.badge.warn`, `signal.reason`) with
    `align-items: flex-start` on `.signal-detail` itself - but that
    affects ALL children, not only the pill. One of them is `.row`, the
    raw-write field (`input[type="text"]`) below it: under `flex-start`,
    THAT also loses its stretched width and falls back to its
    `min-width: 12rem`. Measured in the throwaway harness (real
    `index.html`/`style.css`, a signal with `exportable: false` and text
    from `_UNEXPORTABLE_REASONS`): `.row` shrinks from full disclosure
    width (695.6px) to 406.6px, the field inside it from around 630px to
    the 192px floor.

    A test cannot measure these widths (no browser layout in the
    test suite) - but it can pin down WHERE the fix has to sit: on the
    pill itself (`align-self: flex-start`), not on the container.
    `.signal-detail` stays at the default `stretch` (no own
    `align-items`), otherwise we would be back at the container fix and
    `.row` would shrink again - exactly what this test is meant to
    block."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text

    container_start = css.index(".signal-detail {")
    open_brace = css.index("{", container_start)
    close_brace = css.index("}", open_brace)
    container_body = css[open_brace:close_brace]
    # The rule body itself carries a comment that quotes `align-items:
    # flex-start` as justification (exactly the discarded approach) -
    # strip the comments first, otherwise a plain string search would
    # find its own quote and the test would never go red.
    container_declarations = re.sub(r"/\*.*?\*/", "", container_body, flags=re.DOTALL)
    assert "align-items" not in container_declarations, (
        "align-items on the container also hits .row (raw-write field), not just the pill"
    )

    # The selector that actually carries `align-self: flex-start` - not
    # found via `css.index(".signal-detail .badge {")`, which would find
    # the WRONG one of the two identically worded selector lines (the
    # other belongs to the `margin: 0` rule shared by `.hint` AND
    # `.badge`). Instead, going backwards from the declaration itself to
    # its own selector.
    align_self_pos = css.index("align-self: flex-start;")
    badge_open_brace = css.rindex("{", 0, align_self_pos)
    prev_close_brace = css.rindex("}", 0, badge_open_brace)
    # The justification comment before the rule sits in the same gap -
    # strip it, otherwise what remains of the selector is not the
    # last line but the first (comment) line.
    preceding = re.sub(
        r"/\*.*?\*/", "", css[prev_close_brace + 1 : badge_open_brace], flags=re.DOTALL
    )
    selector = preceding.strip()
    assert selector == ".signal-detail .badge"


async def test_the_kebab_button_is_named_and_reports_its_state(api):
    """Finding (final review): the old version only checked THAT
    `:aria-expanded=` and `:aria-label=` occur as attribute names - not
    WHAT they are bound to. An `:aria-expanded="true"` (bound to a
    constant, i.e. always reporting the same state) would have stayed
    green. This version ties both to the concrete expression the button
    actually has to carry."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))

    button_start = dialog.index('class="signal-more"')
    button_tag_start = dialog.rindex("<button", 0, button_start)
    button_tag_end = dialog.index(">", button_start)
    button = dialog[button_tag_start:button_tag_end]

    assert ':aria-expanded="expandedSignalKey === signal.key"' in button
    assert ":aria-label=\"t('web.signals.row_details')\"" in button


async def test_the_modal_leads_with_the_number_the_user_came_for(api):
    """You open this modal to see and change what goes to Loxone.
    This number used to appear nowhere.

    Finding (final review): the old version checked neither POSITION
    ("leads with") nor the DENOMINATOR (`total:`) - the summary could
    have slid to the end of the modal or shown the wrong denominator
    without the test noticing. This version ties the number to its
    position before the column header and asserts both halves of the
    fraction individually.

    Decision (deliberate, do not change): the denominator counts ALL
    signals via `signalCount`, not only the functional ones - on the
    button it reads "17 of 173", not "12 of 17" as sketched in the
    design. Both are true; the code deliberately counts all of them,
    because the modal also carries the expert group, and an enabled
    expert signal really should be included."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)

    assert "web.signals.export_summary" in page
    assert "exported: exportedSignalCount(signalsModalDevice)" in page
    assert "total: signalCount(signalsModalDevice)" in page
    assert page.index("web.signals.export_summary") < page.index(
        'class="signal-grid signal-grid-head"'
    )


async def test_the_deselect_all_button_calls_the_correct_function_with_the_device_id(api):
    """The header row shows the export count and, next to it, a
    "deselect all" button (task 9, design 2026-09-07, section 4.2). A
    faulty function call (swapped variable, wrong method, forgotten
    argument) would stay green in a text test - the button itself never
    runs, since without a browser engine there is only its markup. What
    is proven, therefore, is that the shipped file carries the correct
    binding: the call is named `deselectAllSignals(signalsModalDevice)`
    (the device argument not forgotten, not swapped) and the label comes
    from the translation key `web.signals.deselect_all`.

    A separate test, because it answers a different question than
    `test_the_modal_leads_with_the_number_the_user_came_for`:
    "is the button tied to the right call" vs. "does the number appear
    in the header"."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))

    # Find the `signals-summary` div the button sits in.
    summary_start = dialog.index('class="signals-summary"')
    summary_section_end = dialog.index("</div>", summary_start)
    summary_section = dialog[summary_start:summary_section_end]

    # Cut out the "deselect all" button itself, not the whole div.
    button_text_idx = summary_section.index("deselectAllSignals")
    button_start = summary_section.rindex("<button", 0, button_text_idx)
    button_end = summary_section.index("</button>", button_text_idx)
    button = summary_section[button_start:button_end]

    assert '@click="deselectAllSignals(signalsModalDevice)"' in button
    assert "x-text=\"t('web.signals.deselect_all')\"" in button


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_deselect_all_empties_the_selection_instead_of_inverting_it():
    """A `toggleExported` over ALL signals would have inverted the
    selection - but the button is named "deselect all", not "invert". A
    second click must therefore do nothing more.

    `toggleExported` is replaced here because it calls the route: what
    is checked is the selection rule of this loop, not the write
    path."""
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
        // Async IIFE, because `node -e` runs as CommonJS and top-level
        // `await` is not allowed there - `deselectAllSignals`
        // is async.
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

    # "c" is `exported`, but does not map to any Loxone input - it
    # does not count, just as `to_inputs` leaves it out server-side.
    assert values["before"] == 1
    assert values["total"] == 3
    assert values["after"] == 0
    # "b" was already off and must not have been touched.
    assert "b" not in values["firstRun"]
    assert values["secondRunTouched"] == 0


async def test_the_signal_table_stacks_on_a_narrow_screen(api):
    """Six columns do not fit under about 640px. Without this wrap
    the table would fray out there again - exactly the state the
    whole rework eliminated, just on a phone.

    Checks not only THAT "display: none" appears somewhere in the
    vicinity of the media query, but that it sits in the RULE BODY of
    `.signal-grid-head` itself: a keyword window of 900 characters would
    stay green even if a CSS did not hide the column header at all,
    because "display: none" happens to appear in a neighboring,
    unrelated rule (this version actually does that - the checkbox
    columns themselves carry `display: flex`, not `none`, but that could
    not be read off from a mere window). This version ties every
    expected declaration to its own selector."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text

    assert "@media (max-width: 640px)" in css
    media_start = css.index("@media (max-width: 640px)")
    body_start = css.index("{", media_start) + 1

    # Count brace depth instead of guessing a fixed character count -
    # yields exactly the body of the media query, regardless of how
    # long the rules inside it are or in what order they appear.
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

    # The column header disappears: above a stacked card it no
    # longer labels anything.
    assert "display: none" in rule_body(".signal-grid-head")
    # The row becomes a stacked card instead of the six fixed columns.
    assert "grid-template-columns: auto minmax(0, 1fr)" in rule_body(".signal-grid {")
    # The two checkbox columns stay visible and usable - they
    # do not disappear with the header, they just shift to the left.
    assert "justify-content: flex-start" in rule_body(".signal-grid .col-center")


async def test_the_checkbox_labels_become_visible_only_below_640px(api):
    """Review finding: the comment above the media query claimed that
    `title` would appear there as visible text next to the checkbox - but
    there was no `content: attr(title)` or similar anywhere, `title`
    stayed a plain hover tooltip that a touch user never gets to see.
    The fix shows real text: a `<span class="col-center-label">` with the
    same translation key as the checkbox's `aria-label`, hidden by
    default and only shown within the 640px media query - the reverse of
    the column header, which disappears exactly there."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    css = (await client.get("/static/style.css")).text

    # Both labels carry the span with the same key as their
    # `aria-label`, inside the same `<label>` as the checkbox - no
    # new translation key, the same information in a second place.
    for key in ("export_checkbox", "resend_checkbox"):
        label_start = dialog.index(f"aria-label=\"t('web.signals.{key}')\"")
        label_start = dialog.rindex("<label", 0, label_start)
        label_end = dialog.index("</label>", label_start)
        label = dialog[label_start:label_end]
        assert 'class="col-center-label"' in label
        assert 'aria-hidden="true"' in label
        assert f"x-text=\"t('web.signals.{key}')\"" in label

    # Default rule: hidden as long as the column header is labeling.
    base_start = css.index(".signal-grid .col-center-label")
    media_start = css.index("@media (max-width: 640px)")
    assert base_start < media_start
    base_open = css.index("{", base_start)
    base_close = css.index("}", base_open)
    assert "display: none" in css[base_open:base_close]

    # Media query: shown here, at the same brace depth as the
    # neighboring rules - pattern from
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
    """Design 2026-09-07, section 2: the highlighted primary signal
    is removed, all functional signals sit at equal rank in the
    value grid. `restSignalsFor` used to deliver the short list WITHOUT
    its first entry - that one sat above in the header row. With the
    header row display removed, the grid must run over the full list
    again, otherwise the first signal would vanish without replacement."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert 'x-for="signal in firstSignalsFor(device.id)"' in markup
    assert "restSignalsFor(" not in markup


async def test_the_tile_header_no_longer_carries_a_lead_value(api):
    """Neither the classes nor the call may be shipped. The
    test runs through `_without_comments`, because the justification in
    the markup still names the primary signal explicitly - precisely to
    explain why it no longer appears there."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "lead-value" not in markup
    assert "lead-label" not in markup
    assert "leadSignalFor(" not in markup


async def test_the_offline_pill_sits_in_the_header_not_under_the_name(api):
    """The pill moves to the primary signal's former place: third child
    of `.device-head`, no longer a child of `.device-ident` under the
    name (design, section 5). No separate positioning rule needed:
    `.device-ident` carries `flex: 1 1 auto` and consumes the free space
    in the flex header row, which puts the pill on the right - without
    its own `margin-left: auto` (style.css) contributing anything to
    that (addendum 2026-09-07, see spec section 5).

    The nesting is proven via the order in the shipped markup: between
    the name field and the pill there MUST be a closing `</span>` - the
    one belonging to `.device-ident`. If the pill still sits inside it,
    that closing tag is missing."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    name_end = markup.index('@change="saveLabel(device)"')
    pill = markup.index('<span class="status-pill off"', name_end)
    assert "</span>" in markup[name_end:pill], "the offline pill is still inside `.device-ident`"


async def test_the_missing_signals_hint_no_longer_asks_for_a_lead(api):
    """The hint distinguishes "loaded but empty" from "still loading"
    (spec 8.1). Its trigger used to be `!leadSignalFor(device.id)`;
    without a primary signal it asks the list directly."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert (
        'x-show="signalsByDevice[device.id] && functionalSignalsFor(device.id).length === 0"'
    ) in markup


async def test_the_lead_helpers_are_gone_from_the_script(api):
    """Design 2026-09-07, section 10: both methods are removed without
    replacement, now that the markup no longer calls them. An unused
    method in `app.js` is not a harmless leftover - it invites the next
    rework to reintroduce the primary signal without having read the
    design."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    # Anchored on the DEFINITION, not on the mere name: the comment
    # on `signalIsFresh` still names `leadSignalFor` - precisely to
    # explain why its null tolerance remains even though the caller is
    # gone. Unlike the markup, there is no `_without_comments` helper
    # for `app.js`.
    assert "leadSignalFor(deviceId) {" not in script
    assert "restSignalsFor(deviceId) {" not in script
    assert "this.firstSignalsFor(deviceId).slice(1)" not in script


async def test_the_lead_rules_are_gone_from_the_stylesheet(api):
    """Design 2026-09-07, section 10. Neither class appears in any
    markup anymore; their rules - including the long justification for
    `flex: 0 0 auto` versus `flex: 0 1 auto` and for the `padding-block`
    for descenders - describe an element that no longer exists."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    # Anchored on the selector with its opening brace, not on the mere
    # class name: the comment on `.device-head .device-name` still
    # names `.lead-value` - it explains why the name there used to get
    # only 65px. A stylesheet has no `_without_comments` helper.
    assert ".lead-value {" not in css
    assert ".lead-value small {" not in css
    assert ".lead-label {" not in css


async def test_the_control_modal_is_delivered(api):
    """Proves ONLY delivery. Whether the Alpine expressions in it
    actually bind cannot be said by this test - that is checked by the
    browser pass in a later task."""
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
    # Deviation from the task brief (project decision): the percent
    # slider carries its label via `command.slug`, not via a
    # fixed translation key - the checked-in test lamp
    # (`ikea_kajplats_cws_lamp.json`) has two percent commands that
    # could not otherwise be distinguished.
    assert "web.devices.control_brightness" not in page
    assert "web.devices.control_brightness" not in script


async def test_the_checkbox_hit_targets_reach_24px(api):
    """WCAG 2.2 AA, success criterion 2.5.8 (Target Size Minimum): 24x24
    CSS-pixel hit target. A review found a checkbox measured at
    13px width on a narrow window - the bare
    `<input type="checkbox">` sits in `style.css` with no rule of its
    own, on plain browser default.

    This test cannot measure it itself (no layout engine here) -
    the measuring ran in a throwaway harness outside the repo
    (real markup cut out of `index.html`, real `style.css`,
    loaded with Alpine over http, `label.getBoundingClientRect()`
    measured, not the `<input>`'s). Result before/after (desktop
    px, identical under 640px for the four text-label rows, since they
    are not tied to any media query):
      - projectSync.includeNewDevices: 204x21.5 -> 200x24 (the checkbox
        itself was removed on 2026-09-11)
      - exportIncludeSystem:           208x21.5 -> 204x24
      - exportOnlyPending:             131x21.5 -> 128x24
      - hideNoise:                     166x21.5 -> 162x24
      - Signal modal export column:     58x19   ->  58x24 (desktop),
                                         99x21   ->  99x24 (< 640px)
      - Signal modal resend column:    76x19   ->  76x24 (desktop),
                                        193x21   -> 193x24 (< 640px)
    All six were already horizontally over 24px BEFORE - four via the
    text next to the checkbox, the two signal-modal columns via the
    fixed column width from `grid-template-columns` (desktop) or the
    visible `.col-center-label` text (< 640px). Only the height was
    missing, which is why both rules below set ONLY `min-height`, no
    `min-width` - an ineffective rule would be dead weight. The
    signal-modal grid stayed aligned throughout (left edge deviation
    still 0 against the header, see
    `test_the_signal_rows_and_the_header_share_one_grid`) and without
    horizontal overflow (`scrollWidth - clientWidth` still 0), at both
    widths.

    The fix sits on the `<label>`, not on the `<input>`: a label that
    wraps its checkbox forwards activation from anywhere in
    its area - it therefore already IS the hit target, it was
    just missing the minimum size. A larger checkbox would have looked
    clunky next to the 14px text and would have changed the look of the
    entire UI."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    css = (await client.get("/static/style.css")).text

    # The text-label checkboxes now carry `checkbox-label` - not
    # `.row label` in general, because the same row class elsewhere also
    # carries text-field, file, and select labels (e.g. the bridge IP),
    # whose layout is not meant to be dragged along here. `:has()`
    # deliberately avoided (project guideline).
    for model in (
        'x-model="exportIncludeSystem"',
        'x-model="exportOnlyPending"',
        'x-model="hideNoise"',
    ):
        at = page.index(model)
        label_start = page.rindex("<label", 0, at)
        label_end = page.index(">", label_start)
        opening_tag = page[label_start:label_end]
        assert 'class="checkbox-label"' in opening_tag, model

    # Bound to the rule body of its own selector, not to a
    # keyword window - see `test_the_key_pill_wraps_instead_of_
    # touching_the_value_column` for the same pattern.
    start = css.index(".checkbox-label {")
    open_brace = css.index("{", start)
    close_brace = css.index("}", open_brace)
    checkbox_label_rule = css[open_brace:close_brace]
    assert "min-height: 24px" in checkbox_label_rule
    assert "align-items: center" in checkbox_label_rule
    # No `min-width`: the text next to the checkbox has long carried
    # the width past 24px already, see the docstring measurement above.
    assert "min-width" not in checkbox_label_rule

    # `.signal-grid .col-center` additionally carries a rule INSIDE
    # the 640px media query (justify-content, gap) - `css.index` finds
    # the FIRST, the base rule outside it, where `min-height`
    # must now sit.
    media_start = css.index("@media (max-width: 640px)")
    start = css.index(".signal-grid .col-center {")
    assert start < media_start
    open_brace = css.index("{", start)
    close_brace = css.index("}", open_brace)
    col_center_rule = css[open_brace:close_brace]
    assert "min-height: 24px" in col_center_rule
    assert "align-items: center" in col_center_rule
    assert "min-width" not in col_center_rule


async def test_the_coarse_age_helper_never_speaks_in_seconds(api):
    """`sinceTextCoarse` is the label that sits IN the tile's text flow.

    `sinceText` next to it stays as it is: it feeds a tooltip, where a
    width that changes every second costs nothing. In the flow it does -
    the tile carried such a label once and moved it into the tooltip on
    purpose, because a value counting up from "7s ago" shoves the row
    sideways and draws the eye to the motion instead of the change (see
    `signalSeenText` in app.js).

    So this helper must NOT reach for `web.header.time_ago_seconds`.

    It DOES reach for `web.header.time_ago_days`, which `sinceText` does
    not (final review, A7): this is the one label in the interface built
    to show a long silence, and "120h ago" is a number to convert before
    it is an answer.
    """
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("sinceTextCoarse(timestamp) {")
    end = script.index("\n    },", start)
    body = script[start:end]

    assert 'return t("web.header.time_ago_just_now");' in body
    assert 'return t("web.header.time_ago_minutes", { minutes });' in body
    assert 'return t("web.header.time_ago_hours", { hours });' in body
    assert 'return t("web.header.time_ago_days", { days: Math.round(hours / 24) });' in body
    # The whole point of the helper: no per-second branch.
    assert "time_ago_seconds" not in body
    # And no hardcoded translation, in either language.
    assert "just now" not in body
    assert "gerade eben" not in body


async def test_the_live_handler_credits_the_right_device(api):
    """A live message names its device in its key: `d<id>_<rest>`.

    The heartbeat (`bridge_alive`) belongs to no device (Spec 6.5) and
    must not count. It arrives every 30 seconds no matter what, so
    crediting it to anyone would make EVERY tile claim it had just been
    heard from - and the one statement this feature exists to make would
    become a lie on every card at once.

    `d<id>_online` is the second key that must not count, and unlike the
    heartbeat the key pattern does NOT exclude it (final review, A1). The
    server draws that line deliberately: `Runtime._mark_heard` is called
    from `on_attribute`, `on_node_snapshot` and `on_event`, and NOT from
    `set_online`, because reachability is matter-server's bookkeeping
    about a node, not the node saying anything. `set_online` nonetheless
    ends in `_notify_observers("d<id>_online", ...)`, so the key reaches
    this handler verbatim - and the tile would render the Offline pill
    and "Last heard just now" on the same card, at the precise moment
    this feature exists to serve.
    """
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    # `app.js` has two `socket.addEventListener("message", ...)` blocks -
    # this one, and `connectDiagnosticsLive`'s, which comes first in the
    # file. Anchor on `connectLive()` itself so `.index` cannot land on
    # the wrong one.
    connect_live = script.index("connectLive() {")
    start = script.index('socket.addEventListener("message"', connect_live)
    end = script.index("\n      });", start)
    body = script[start:end]

    assert "const owner = /^d(\\d+)_/.exec(message.key);" in body
    assert "this.deviceHeardAt[Number(owner[1])] = now;" in body
    # The exclusion itself, pinned character for character: a matching
    # `owner` alone must NOT be enough to credit the device.
    assert "if (owner && message.key !== `d${owner[1]}_online`) {" in body


async def test_a_reconnection_refetches_the_served_last_heard(api):
    """`deviceHeardAt` is the tab's own bookkeeping and cannot know what
    it missed.

    Nothing backfills it: `loadDevices()` runs from `startApp()` and
    after commissioning or removal, never on reconnect. A window contact
    that reports once during a two-hour socket outage would therefore
    leave its tile reading "Last heard 3h ago" indefinitely, with the
    staleness banner already cleared - the same wasted investigation this
    feature was built to prevent, pointing the other way (final review,
    A2).

    The guard reads `socketEverConnected` BEFORE the assignment below it,
    so it is the state of the PREVIOUS connection: on the first one
    `startApp()` has just loaded the list.
    """
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    # Anchor on `connectLive()` - `connectDiagnosticsLive` has an `open`
    # listener of its own, and it comes first in the file.
    connect_live = script.index("connectLive() {")
    start = script.index('socket.addEventListener("open"', connect_live)
    end = script.index("\n      });", start)
    body = script[start:end]

    assert "if (this.socketEverConnected) {" in body
    assert "this.loadDevices();" in body
    # Order matters: read as the previous state, set afterwards.
    assert body.index("if (this.socketEverConnected) {") < body.index(
        "this.socketEverConnected = true;"
    )


async def test_the_tile_takes_the_later_of_the_served_and_the_live_timestamp(api):
    """`device.last_heard` arrives once, with GET /api/devices.

    Shown on its own it would say "12m ago" while values stream into the
    very same tile - confidently wrong, which is worse than silent. The
    served value is only the starting point for the window between page
    load and the first live message from that device.
    """
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("lastHeardAt(device) {")
    end = script.index("\n    },", start)
    body = script[start:end]

    assert "const live = this.deviceHeardAt[device.id];" in body
    assert "Date.parse(device.last_heard)" in body
    assert "Math.max(...candidates)" in body


async def test_the_last_heard_line_is_translated_and_states_the_never_case(api):
    """Both branches carry i18n keys, and the `null` case has its own
    sentence rather than an empty line: "nothing since the bridge
    started" is the statement that would have shortened 8 September, and
    it must not be silently indistinguishable from a device heard from a
    second ago."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("lastHeardText(device) {")
    end = script.index("\n    },", start)
    body = script[start:end]

    assert 'return t("web.devices.never_heard");' in body
    assert 'return t("web.devices.last_heard", { text: this.sinceTextCoarse(at) });' in body
    assert "Last heard" not in body
    # The German that actually SHIPS (strings.yaml, `web.devices.last_heard`).
    # Until the final review this line read "Zuletzt gehoert" - the
    # transliteration that commit 8870425 had already replaced in the
    # shipped string, so the assertion could no longer fail and someone
    # hardcoding the real German would have sailed straight past it. The
    # old spelling stays below as a second guard: nobody should reach for
    # it here either.
    assert "Zuletzt gehört" not in body
    assert "Zuletzt gehoert" not in body


async def test_the_tile_shows_the_last_heard_line_between_head_and_values(api):
    """Device state belongs in the header half of the tile, export state
    in the foot - the tile's own comment already draws that line ("The
    header stays reserved for the device's state, not the export
    state"). Two timestamps about different subjects on adjacent lines
    read as one muddled sentence, so this must not land in
    `.device-foot` next to `exportHintFor`.
    """
    client, _, _ = api
    page = (await client.get("/")).text

    assert 'class="hint device-heard" x-text="lastHeardText(device)"' in page

    # There is exactly one tile template in the page, so plain positions
    # are enough to pin the order.
    head = page.index('<div class="device-head">')
    line = page.index('x-text="lastHeardText(device)"')
    values = page.index('<div class="value-rows"')
    foot = page.index('<div class="device-foot">')

    assert head < line < values < foot


# ---------------------------------------------------------------------------
# "Last heard ..." - the shipped file, executed (final review, A3).
#
# Every other check on this line in this file reads DELIVERED TEXT: that
# `lastHeardText` names its i18n keys, that the tile carries the binding,
# that the live handler excludes `d<id>_online`. Those prove the right
# characters left the server. They cannot prove the code does the right
# thing - an assertion on a condition's source text stays green however
# the condition behaves, and the two tests below are about behaviour that
# a copy of the source could not have shown: which of two timestamps
# wins, and which keys count as the device speaking.
#
# The design (§8.2) asked for the expressions to be evaluated against a
# real DOM; the plan settled for a throwaway harness over three copied
# function bodies, which proved something about the copy. These tests run
# the real, shipped `src/loxmatter/web/app.js` instead, through the same
# `_app_state` as the tile-header section further up. Still no Alpine and
# still no DOM - the unit under test is the object `app()` returns, and
# the bindings in `index.html` stay pinned by the delivery tests above.
# The one thing this buys over the surrounding convention is the one
# thing that mattered here: a wrong condition FAILS instead of reading
# correctly.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_shipped_last_heard_line_takes_the_newer_of_the_two_sources():
    """Runs `lastHeardText` on the object `app()` actually returns.

    Three cases, and the label is a different sentence in each - which is
    why this test feeds the real translation table (`_web_strings`, what
    `GET /api/i18n` sends the browser). Without it `t()` falls back to
    returning the key and both timestamp cases would render as
    "web.devices.last_heard", i.e. the test would pass no matter which
    source won.

    The `null` case is the one this feature exists for: a device from
    which nothing has been heard must say so, not fall back to an empty
    line that looks like a device heard from a second ago.
    """
    values = _app_state(
        setup="""
        // A fixed instant instead of the clock: the label must not depend
        // on how long node took to start.
        const now = 1757400000000;
        state.nowTick = now;
        const iso = (ms) => new Date(ms).toISOString();
        const out = {};

        // Neither source has anything.
        out.never = state.lastHeardText({ id: 1 });

        // The SERVED value is the newer one - the live bookkeeping of
        // this tab is three hours behind it.
        state.deviceHeardAt[2] = now - 3 * 3600 * 1000;
        out.served_newer = state.lastHeardText({ id: 2, last_heard: iso(now - 120000) });

        // And the other way round: the page was loaded three hours ago
        // and this device has just reported.
        state.deviceHeardAt[3] = now;
        out.live_newer = state.lastHeardText({ id: 3, last_heard: iso(now - 3 * 3600 * 1000) });

        // The five-day silence this whole line was built for. Before the
        // day branch (A7) this read "Last heard 120h ago".
        state.deviceHeardAt[4] = now - 5 * 24 * 3600 * 1000;
        out.five_days = state.lastHeardText({ id: 4 });

        console.log(JSON.stringify(out));
        """,
        translations=_web_strings(),
    )

    assert values["never"] == "Not heard since the bridge started"
    assert values["served_newer"] == "Last heard 2m ago"
    assert values["live_newer"] == "Last heard just now"
    assert values["five_days"] == "Last heard 5d ago"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_shipped_live_handler_does_not_credit_the_online_key():
    """The scenario of 8 September, played through the shipped handler.

    matter-server marks the dead button's node unavailable, `set_online`
    puts `d5_online=false` on the socket, and before the exclusion the
    tile answered that with "Last heard just now" - next to the Offline
    pill, on the same card. `Runtime._mark_heard` never ran for that
    message; only the browser counted it.

    This is the assertion the source-text check cannot make: it runs the
    handler, hands it the three kinds of message that reach it, and asks
    the tile's own sentence afterwards.
    """
    values = _app_state(
        setup="""
        // `connectLive()` is browser code: it reads `window.location` to
        // build the URL and calls `new WebSocket(...)`. node ships a REAL
        // global WebSocket that would try to open a real connection, so
        // both are stubbed - the stub only records its listeners, and
        // `state.socketEverConnected` stays false, so the `open` handler
        // does not reach for `fetch` either.
        const sockets = [];
        globalThis.window = {
          location: { protocol: "http:", host: "example.invalid" },
          setTimeout: () => 0,
          clearTimeout: () => {},
        };
        globalThis.WebSocket = class {
          constructor() {
            this.listeners = {};
            sockets.push(this);
          }
          addEventListener(type, handler) {
            (this.listeners[type] = this.listeners[type] || []).push(handler);
          }
          close() {}
        };

        state.connectLive();
        const socket = sockets[0];
        const deliver = (key, value) => {
          for (const handler of socket.listeners.message || []) {
            handler({ data: JSON.stringify({ key, value }) });
          }
        };
        const out = {};

        // Reachability: matter-server's bookkeeping ABOUT the node, not
        // the node saying anything.
        deliver("d5_online", false);
        out.after_online = state.deviceHeardAt[5] ?? null;
        state.nowTick = Date.now();
        out.text_after_online = state.lastHeardText({ id: 5 });

        // The heartbeat belongs to no device at all (Spec 6.5).
        deliver("bridge_alive", true);
        out.after_heartbeat = state.deviceHeardAt[5] ?? null;
        out.heartbeat_seen = state.lastHeartbeatAt !== null;

        // An actual attribute report from that same device - this one
        // counts, and it is the only one that does.
        deliver("d5_state", true);
        out.after_state = typeof state.deviceHeardAt[5];
        state.nowTick = Date.now();
        out.text_after_state = state.lastHeardText({ id: 5 });

        console.log(JSON.stringify(out));
        """,
        translations=_web_strings(),
    )

    assert values["after_online"] is None
    assert values["after_heartbeat"] is None
    # The sentence the card shows in exactly the state that cost hours on
    # 8 September: the device is offline AND nothing has been heard from
    # it. Before the exclusion this read "Last heard just now".
    assert values["text_after_online"] == "Not heard since the bridge started"
    # The heartbeat still arrives and is still recorded - just not against
    # any device.
    assert values["heartbeat_seen"] is True
    assert values["after_state"] == "number"
    assert values["text_after_state"] == "Last heard just now"


# ---------------------------------------------------------------------------
# Stale banners after a real bridge restart (session of 9 September 2026,
# System tab after a browser update to 0.3.2). Both bugs below were visible
# at once on the same screenshot: a red "bridge unreachable" banner next to
# a header already reading "Live connection active", and a yellow "Version
# X available"/"Install update" card next to the green "Now running: X" -
# offering to install the version already running.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_successful_reconnection_clears_a_stale_bridge_unreachable_banner():
    """Bug 1. During the bridge's own restart (an update, or any ordinary
    outage), `handleLiveDisconnect()`'s own `loadAuthInfo()` call fails
    outright - the HTTP server is gone along with the WebSocket - and its
    generic catch lands `t("web.errors.bridge_unreachable")` in `authError`
    (`requestJson`'s network-error catch, `loadAuthInfo`'s own catch).
    `authenticated` stays untouched by that failure (it is not an
    `UnauthorizedError`), so `handleLiveDisconnect` falls through to
    `scheduleReconnect()` instead of giving up.

    Nothing ever cleared the banner again once the bridge came back:
    `loadAuthInfo()` only clears `authError` on ITS OWN success, and once
    the socket alone reconnects successfully, nothing calls `loadAuthInfo()`
    a further time - `connectLive()`'s `open` handler used to only flip
    `socketConnected` and backfill devices. The header therefore read "Live
    connection active" right next to a red banner about an outage that had
    already ended.

    A successful `open` is proof enough on its own that the message can only
    be this stale CONNECTION text, never a genuine auth failure:
    `build_api_guard` (loxone/server.py) rejects an unauthorized WebSocket
    handshake before it ever reaches `open`, and a genuine auth failure
    always flips `this.authenticated` to `false` first (`noteAuthError`,
    `handleLiveDisconnect`'s own `if (!this.authenticated)` branch) and
    sends the page to the login screen well before any reconnection could
    succeed. The fix clears `authError` in the `open` handler only while
    `this.authenticated` is still `true` - the same distinction
    `handleLiveDisconnect` already draws a few lines away, not a blanket
    clear.

    Two scenarios against the real, shipped `connectLive()`, in one node
    process:
      1. The outage case above - `authenticated` stays `true` throughout,
         and the stale message must be gone once `open` fires.
      2. The genuine-auth case, played directly against the same `open`
         handler to pin the guard itself: with `authenticated` already
         `false` and a real session-expired message in `authError` (exactly
         what `handleLiveDisconnect`'s own branch sets), `open` firing must
         leave it completely alone.

    Before the fix, `connection_error_cleared` below reads the stale
    "bridge unreachable" sentence instead of `None`.
    """
    values = _app_state(
        """
        globalThis.window = {
          location: { protocol: "http:", host: "example.invalid" },
          setTimeout: () => 0,
          clearTimeout: () => {},
        };
        const sockets = [];
        globalThis.WebSocket = class {
          constructor() {
            this.listeners = {};
            sockets.push(this);
          }
          addEventListener(type, handler) {
            (this.listeners[type] = this.listeners[type] || []).push(handler);
          }
          close() {}
        };
        const openSocket = (socket) => {
          for (const handler of socket.listeners.open || []) handler();
        };

        const out = {};

        // Scenario 1: an outage that has just ended. `socketEverConnected`
        // stays false so the `open` handler does not also reach for
        // `loadDevices()`/`this.request`, which is not this test's concern.
        state.authenticated = true;
        state.authError = "The bridge is unreachable \\u2013 it may not be running.";
        state.connectLive();
        openSocket(sockets[0]);
        out.connection_error_cleared = state.authError;
        out.socket_connected = state.socketConnected;

        // Scenario 2: a genuine auth failure must survive the exact same
        // handler untouched.
        state.authenticated = false;
        state.authError = "Your session has expired. Please log in again.";
        state.connectLive();
        openSocket(sockets[1]);
        out.auth_error_survives = state.authError;

        console.log(JSON.stringify(out));
        """
    )

    assert values["connection_error_cleared"] is None
    assert values["socket_connected"] is True
    assert values["auth_error_survives"] == "Your session has expired. Please log in again."


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_update_check_is_refreshed_once_the_update_reaches_a_terminal_state():
    """Bug 2. `loadUpdateStatus()` used to never re-run `loadUpdateCheck()`,
    so once an accepted update finished, `updateAvailable` still held the
    offer the user had just accepted: the card rendered "Version X
    available" and an "Install update" button right beside the green "Now
    running: X" banner (the whole-branch review's own Minor finding, left
    unfixed until this session's screenshot showed exactly that).

    `loadUpdateStatus()` already has the one place a "was running, now
    isn't" transition is detected: the `allowStop` branch at its very end,
    which also re-fetches `versionInfo` for the same reason (the card
    should show the NEW number, not the one the page loaded with). This
    test plays two consecutive polls through the real, shipped
    `loadUpdateStatus()` - one mid-job, one landing on `done` - and checks
    that `loadUpdateCheck()` (stubbed to prove it is INVOKED, not merely
    that state ends up looking plausible) fires exactly once, at the
    transition, not on every poll.

    A failed update gets the same refresh (not only `done`): the offer may
    still be valid and the user may want to retry, per this task's own
    brief - both `done` and `failed` are covered by the same "was running,
    now the timer would otherwise stop" branch, deliberately not narrowed
    to `phase === 'done'` alone.
    """
    values = _app_state(
        """
        let phase = "pull";
        let updateCheckCalls = 0;
        state.updateAvailable = { target: "1.1.0", error: null };
        state.updateStatus = null;
        state.request = async (method, path) => {
          if (path === "/api/update/status") {
            return {
              state: { phase, id: "job-1", from: "1.0.0", to: "1.1.0",
                       error: null, rolled_back: false, healthy: true },
              updater_present: true, log: [], channel: "stable", check_enabled: true,
            };
          }
          if (path === "/api/version") {
            return { version: "1.1.0" };
          }
          throw new Error("unexpected request " + method + " " + path);
        };
        state.loadUpdateCheck = async () => {
          updateCheckCalls += 1;
          state.updateAvailable = { target: null, error: null };
        };
        (async () => {
          // Mid-job: no transition yet, must not refresh the offer.
          await state.loadUpdateStatus();
          const callsWhileRunning = updateCheckCalls;

          // The next poll lands on the terminal state.
          phase = "done";
          await state.loadUpdateStatus();

          console.log(JSON.stringify({
            callsWhileRunning,
            callsAfterDone: updateCheckCalls,
            updateAvailableAfterDone: state.updateAvailable,
          }));
        })();
        """
    )

    assert values["callsWhileRunning"] == 0
    assert values["callsAfterDone"] == 1
    assert values["updateAvailableAfterDone"] == {"target": None, "error": None}


def test_a_finished_update_is_announced_only_to_the_page_that_watched_it():
    """`phase` never returns to `idle` after `done` - the sidecar has no
    reason to rewrite its own record of what last happened - so a banner
    keyed on the phase alone stands on the System tab forever, across
    reloads and reboots, until the next update runs. Someone who never
    pressed anything reads "Now running: 0.3.6" weeks later.

    The banner is therefore keyed on `updateWatchedJobId`, which this
    page sets when it starts an update or first catches one already
    running, and which no reload survives. A socket reconnect is not a
    reload - the bridge restarting mid-update is precisely when the
    banner has to live through the gap - and the state is untouched
    there because the page itself never went away.

    A failure is deliberately not gated this way; see the markup.
    """
    values = _app_state(
        """
        state.request = async (method, path) => {
          if (path === "/api/update/status") {
            return {
              state: { phase: "done", id: "j-old", from: "0.3.5", to: "0.3.6",
                       error: null, rolled_back: false, healthy: true },
              updater_present: true, log: [], channel: "stable",
              check_enabled: true,
            };
          }
          throw new Error("unexpected request " + method + " " + path);
        };
        (async () => {
          // A freshly loaded page meeting a `done` left over from an
          // update somebody else ran, or the same person ran yesterday.
          await state.loadUpdateStatus();
          const afterReload = state.updateWatchedJobId;
          // The same page, having watched that very job.
          state.updateWatchedJobId = "j-old";
          const afterWatching = state.updateWatchedJobId;
          state.stopUpdateTimer();
          console.log(JSON.stringify({
            phase: state.updateStatus.state.phase,
            jobId: state.updateStatus.state.id,
            afterReload,
            afterWatching,
          }));
        })();
        """
    )

    # The status itself is unchanged - the banner's condition is what moved.
    assert values["phase"] == "done"
    assert values["jobId"] == "j-old"
    # Nothing on a fresh page claims the result, so the banner stays down.
    assert values["afterReload"] is None
    assert values["afterWatching"] == "j-old"


def test_the_page_adopts_an_update_somebody_else_started():
    """A second tab, or a phone, should still announce the result of an
    update it did not itself start - otherwise the person watching from
    the sofa never learns it finished. Catching it while a running phase
    is on the wire is what makes that work, and it is the same field the
    reload clears.
    """
    values = _app_state(
        """
        state.request = async (method, path) => {
          if (path === "/api/update/status") {
            return {
              state: { phase: "pull", id: "j-live", from: "0.3.5", to: "0.3.6",
                       error: null, rolled_back: false, healthy: true },
              updater_present: true, log: [], channel: "stable",
              check_enabled: true,
            };
          }
          throw new Error("unexpected request " + method + " " + path);
        };
        (async () => {
          await state.loadUpdateStatus();
          const adopted = state.updateWatchedJobId;
          state.stopUpdateTimer();
          console.log(JSON.stringify({ adopted }));
        })();
        """
    )

    assert values["adopted"] == "j-live"


async def test_leaving_the_development_channel_is_explained_before_the_button(api):
    """A delivery test: it proves the sentence and its two conditions were
    served, not that Alpine evaluated them. That is the weaker of the two
    kinds of test in this file, and it is the right one here - the
    condition reads `versionInfo`, which the node harness does not build.

    What it guards is a pairing that would otherwise drift silently. The
    updater now lets a development build move back to a published release
    (`tests/test_updater_script.py::test_a_development_build_may_return_to_a_published_release`),
    on the understanding that the interface says which way the move goes
    first - the release may be OLDER than the build running. Delete the
    sentence and the updater still accepts the switch, in silence. That
    is the failure this catches.
    """
    client, _, _ = api
    page = (await client.get("/")).text

    assert "web.system.update_leaving_dev" in page
    # Both halves of the gate: only on the stable channel, and only while
    # a development build is what is actually running.
    assert "updateStatus.channel === 'stable'" in page
    assert "versionInfo?.version === 'dev'" in page


def test_the_leaving_development_sentence_exists_in_both_languages():
    """`check_language.py` cannot catch a missing `de` - it looks for
    German where English belongs, not for absence. A `web.*` key with no
    German value falls back to English inside a German page, which is the
    thing CLAUDE.md's i18n rule exists to prevent.
    """
    from loxmatter import i18n

    for language in ("en", "de"):
        i18n.set_language(language)
        rendered = i18n.t("web.system.update_leaving_dev", version="0.3.7")
        assert "0.3.7" in rendered
        assert rendered != "web.system.update_leaving_dev"
    i18n.set_language("en")


# ---------------------------------------------------------------------------
# Device groups (design 2026-09-10, section 6)
#
# Two kinds of test, and the difference matters. The first one below is a
# DELIVERY test: it proves the markup and the script reached the browser,
# never that Alpine evaluated them - the known limit of string searches in
# this file. Everything after it runs the shipped `app.js` in node through
# `_app_state`, with a stubbed `request`, and therefore checks behaviour:
# each one fails if the rule it is named for is taken out.
# ---------------------------------------------------------------------------


async def test_the_group_tile_is_delivered_and_shows_nothing_a_group_has_not(api):
    """Delivery only. The negative half is the point: a group has no node,
    so the tile must carry none of `isOnline`, `lastHeardText` or the
    signal preview - showing one would mean inventing an aggregate over
    six lamps with six brightnesses (design 2). The block is cut on
    `group-card`, the tile's own second class, so this cannot accidentally
    end up reading the device tile and passing for the wrong reason."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    script = (await client.get("/static/app.js")).text

    assert "/api/groups" in script
    assert "visibleGroups()" in script

    # Cut from the group tile's own opening class to the ROOM-SECTION loop
    # that follows the group grid in the document. Not to the first
    # `</template>`: the tile contains several `x-for` blocks of its own
    # (the command bar, the room entries), and the cut would end inside the
    # first of them - the negative assertions below would then pass for the
    # wrong reason, having simply been handed a fragment too short to hold
    # anything. `deviceGroups()` is the older helper that buckets DEVICE
    # tiles by room and has nothing to do with device groups; it is used
    # here only as a stable marker for where the group grid ends.
    start = page.index('class="card device-card group-card"')
    tile = page[start : page.index('x-for="group in deviceGroups()"', start)]

    assert "t('web.groups.badge')" in tile
    assert "t('web.groups.member_count', { count: deviceGroup.member_ids.length })" in tile
    assert "t('web.groups.edit_members')" in tile
    assert "t('web.groups.delete')" in tile
    # The room comes from the group's own field, never from its members
    # (design 6): a derived room would move the group the moment one lamp
    # is re-roomed, and nobody would learn why.
    assert "saveGroupRoom(deviceGroup, '')" in tile
    assert "member_labels" in tile  # only as the `title` naming the members

    # `controlsBySubject` is keyed by a device id (a number) or a group
    # SUBJECT string ("g3") - safe only while every group call site wraps
    # its id in `groupSubject(...)`. A slip to, say,
    # `controlsByKind(deviceGroup.id, 'none')` would silently read DEVICE
    # 1's commands onto GROUP 1's tile, and nothing else here would notice:
    # this suite has no browser engine to actually render the markup and
    # see the wrong buttons appear, and the node tests
    # (`test_a_group_button_sends_one_post_to_the_shared_command_route` and
    # friends) call `groupSubject` themselves rather than exercise the
    # markup's own expressions. Checked at minimum for the commands list,
    # `controlsLoaded` and `commandsFor` - the three bindings the markup's
    # own comment above the command bar names as reading the group's
    # subject (finding, 2026-09-11).
    assert "controlsByKind(groupSubject(deviceGroup), 'none')" in tile
    assert "controlsLoaded(groupSubject(deviceGroup))" in tile
    assert "commandsFor(groupSubject(deviceGroup))" in tile

    assert "isOnline" not in tile
    assert "lastHeardText" not in tile
    assert "firstSignalsFor" not in tile
    assert "signalsByDevice" not in tile
    assert "batterySignalFor" not in tile
    assert "exportHintFor" not in tile


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_group_tile_and_a_device_tile_are_filtered_by_the_same_rule():
    """Room chip and search field act TOGETHER (AND) on both halves of the
    grid. `visibleGroups()` must not be a second implementation of that
    rule - if it were, the two halves would eventually disagree, which is
    exactly what this checks by running the real predicate over a group
    whose room, label and category each have to answer on their own.

    `web.devices.category.light` is filled in, because the search compares
    against the TRANSLATED category name (see `matchesSearch`): without a
    table, `t()` returns the key and searching for "light" would pass for
    the wrong reason."""
    values = _app_state(
        """
        state.devices = [
          { id: 1, label: "Lamp Kitchen", room: "Kitchen", category: "light", online: true },
          { id: 2, label: "Plug Hall", room: "Hall", category: "socket", online: true },
        ];
        state.groups = [
          { id: 1, label: "Ceiling", room: "Kitchen", category: "light",
            member_ids: [1], member_labels: ["Lamp Kitchen"], command_count: 2 },
          { id: 2, label: "Outside", room: null, category: "light",
            member_ids: [], member_labels: [], command_count: 0 },
        ];
        const labels = () => state.visibleGroups().map((group) => group.label);
        const out = { all: labels() };
        state.roomFilter = "Kitchen";
        out.inKitchen = labels();
        state.roomFilter = "";
        out.inNoRoom = labels();
        state.roomFilter = null;
        state.deviceSearch = "ceil";
        out.byLabel = labels();
        state.deviceSearch = "outsid";
        out.byOtherLabel = labels();
        state.deviceSearch = "light";
        out.byCategory = labels();
        state.deviceSearch = "kitchen";
        out.byRoom = labels();
        state.roomFilter = "Hall";
        out.searchAndFilterTogether = labels();
        state.deviceSearch = "";
        out.hallHasNoGroup = labels();
        console.log(JSON.stringify(out));
        """,
        translations={
            "web.devices.category.light": "Light",
            "web.devices.category.socket": "Socket",
        },
    )

    assert values["all"] == ["Ceiling", "Outside"]
    assert values["inKitchen"] == ["Ceiling"]
    # "No room" is a real selection, not "All" - the group with room null
    # belongs to it and the one in the kitchen does not.
    assert values["inNoRoom"] == ["Outside"]
    assert values["byLabel"] == ["Ceiling"]
    assert values["byOtherLabel"] == ["Outside"]
    assert values["byCategory"] == ["Ceiling", "Outside"]
    assert values["byRoom"] == ["Ceiling"]
    # AND, not OR: "kitchen" matches the Ceiling group, but the Hall chip
    # is selected, so nothing is left.
    assert values["searchAndFilterTogether"] == []
    assert values["hallHasNoGroup"] == []


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_room_chip_counts_the_groups_that_sit_under_it():
    """A chip's number must match what the grid below it shows, and the
    grid shows device tiles and group tiles. Counting devices only was the
    state before this feature: the Kitchen chip would have said 1 with two
    tiles under it.

    `hasAnyRoom()` is checked in the same run, for the case a chip bar has
    to exist for: a room carried by a GROUP and by no device at all.
    Without groups in that check, the whole bar (and with it the only way
    back to "All") would stay hidden."""
    values = _app_state(
        """
        state.devices = [
          { id: 1, label: "Lamp", room: "Kitchen", category: "light", online: true },
        ];
        state.groups = [
          { id: 1, label: "Ceiling", room: "Kitchen", category: "light",
            member_ids: [1], member_labels: ["Lamp"], command_count: 2 },
          { id: 2, label: "Garden", room: "Garden", category: "light",
            member_ids: [], member_labels: [], command_count: 0 },
          { id: 3, label: "Spare", room: null, category: "light",
            member_ids: [], member_labels: [], command_count: 0 },
        ];
        const chips = state.roomChips().map((chip) => [chip.key, chip.count]);
        const groupsOnly = { devices: [], groups: state.groups };
        console.log(JSON.stringify({
          chips,
          hasAnyRoom: state.hasAnyRoom(),
          hasAnyRoomWithoutDevices: (() => {
            const kept = state.devices;
            state.devices = [];
            const answer = state.hasAnyRoom();
            state.devices = kept;
            return answer;
          })(),
        }));
        """,
        translations={"web.devices.room_none": "No room"},
    )

    # Kitchen holds one device AND one group; Garden only a group; the
    # group with no room lands on the "No room" chip at the end.
    assert values["chips"] == [["Garden", 1], ["Kitchen", 2], ["", 1]]
    assert values["hasAnyRoom"] is True
    assert values["hasAnyRoomWithoutDevices"] is True


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_dialog_disables_another_category_with_the_reason_on_the_entry():
    """The first pick fixes the category, and everything of another kind is
    then disabled WITH its reason (design 6) - a tick that does nothing and
    says nothing is the silent failure Spec 8.1 exists to surface. The
    reason has to name the category the entry actually is, which is why
    the translated text is asserted and not just `disabled`.

    Second half: while EDITING, the lock comes from the group's stored
    category, not from the first draft member. Unticking every member must
    not unlock the other categories - the server refuses a foreign member
    for an emptied group too (design 2), and the refusal belongs on the
    entry, not in a 400 afterwards."""
    values = _app_state(
        """
        state.devices = [
          { id: 1, label: "Lamp", room: null, category: "light", online: true },
          { id: 2, label: "Plug", room: null, category: "socket", online: true },
        ];
        state.groups = [
          { id: 7, label: "Lights", room: null, category: "light",
            member_ids: [1], member_labels: ["Lamp"], command_count: 2 },
        ];
        const lamp = state.devices[0];
        const plug = state.devices[1];
        const both = () => ({
          lamp: state.groupCandidateState(lamp),
          plug: state.groupCandidateState(plug),
        });
        const out = { empty: both() };
        state.toggleGroupMember(lamp);
        out.afterLamp = both();
        out.pickedAfterLamp = [...state.groupDraft.memberIds];
        // A disabled entry must not be tickable through the handler either -
        // the `:disabled` attribute is the display, not the guard.
        state.toggleGroupMember(plug);
        out.pickedAfterPlugAttempt = [...state.groupDraft.memberIds];
        // Editing group 7, draft emptied: still locked to "light".
        state.groupDraft = { id: 7, label: "Lights", room: "", memberIds: [],
                             roomTouched: true };
        out.editingEmptied = both();
        console.log(JSON.stringify(out));
        """,
        translations={
            "web.devices.category.light": "Light",
            "web.devices.category.socket": "Socket",
            "web.groups.other_category": "is a {category} - a group takes one kind only",
        },
    )

    assert values["empty"]["lamp"]["disabled"] is False
    assert values["empty"]["plug"]["disabled"] is False
    assert values["afterLamp"]["lamp"]["disabled"] is False
    assert values["afterLamp"]["plug"]["disabled"] is True
    assert values["afterLamp"]["plug"]["reason"] == "is a Socket - a group takes one kind only"
    assert values["pickedAfterLamp"] == [1]
    assert values["pickedAfterPlugAttempt"] == [1]
    assert values["editingEmptied"]["plug"]["disabled"] is True
    assert values["editingEmptied"]["lamp"]["disabled"] is False


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_room_prefill_stops_at_the_first_keystroke():
    """Prefilled while the members agree on a room, the user's from then on
    (design 6). Both halves fail loudly if `roomTouched` is dropped:
    without it, ticking one more lamp silently overwrites a room just
    typed; without the prefill, a group of five kitchen lamps starts with
    no room at all."""
    values = _app_state(
        """
        state.devices = [
          { id: 1, label: "A", room: "Kitchen", category: "light", online: true },
          { id: 2, label: "B", room: "Kitchen", category: "light", online: true },
          { id: 3, label: "C", room: "Hall", category: "light", online: true },
        ];
        const out = {};
        // `openGroupCreate` focuses the dialog after Alpine's next tick -
        // neither exists in node.
        state.$nextTick = () => {};
        state.$refs = { groupDialog: { showModal: () => {} } };
        state.openGroupCreate();
        state.toggleGroupMember(state.devices[0]);
        out.afterFirst = state.groupDraft.room;
        state.toggleGroupMember(state.devices[1]);
        out.afterSecondSameRoom = state.groupDraft.room;
        state.toggleGroupMember(state.devices[2]);
        out.afterThirdOtherRoom = state.groupDraft.room;
        // The user types a room, then picks one more member.
        state.groupDraft.room = "Ground floor";
        state.groupDraft.roomTouched = true;
        state.toggleGroupMember(state.devices[2]);
        out.afterTypingThenPicking = state.groupDraft.room;
        console.log(JSON.stringify(out));
        """
    )

    assert values["afterFirst"] == "Kitchen"
    assert values["afterSecondSameRoom"] == "Kitchen"
    # Two rooms among the members: no guess at a majority - the group would
    # land somewhere none of them is.
    assert values["afterThirdOtherRoom"] == ""
    assert values["afterTypingThenPicking"] == "Ground floor"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_group_button_sends_one_post_to_the_shared_command_route():
    """The whole point of the shared key namespace: a group tile makes
    exactly the same `POST /api/commands/{key}` call a device tile makes,
    and there is deliberately no group control route (design 5). The
    recorded calls would show it immediately if `executeCommand` ever grew
    a group-specific path - and the count of one shows the tile does not
    fan out in the browser; the bridge does that.

    `loadGroupControls` is exercised in the same run, because the key the
    button sends comes from `GET /api/groups/{id}/controls` and from
    nowhere else."""
    values = _app_state(
        """
        const calls = [];
        state.request = async (method, path, body) => {
          calls.push([method, path, body]);
          if (path === "/api/groups/1/controls") {
            return { commands: [
                       { key: "g1_on", slug: "on", takes_value: false,
                         control: "none", range: null },
                       { key: "g1_level", slug: "level", takes_value: true,
                         control: "percent", range: null },
                     ],
                     hidden_raw_commands: 0, seed_device_id: 4,
                     seed_device_label: "Lamp Kitchen" };
          }
          return null;
        };
        // `showToast` schedules its own dismissal - node has no `window`.
        global.window = { setTimeout: () => 0 };
        const group = { id: 1, label: "Ceiling", room: "Kitchen", category: "light",
                        member_ids: [4, 5], member_labels: ["Lamp Kitchen", "Lamp Table"],
                        command_count: 2 };
        state.groups = [group];
        (async () => {
          await state.loadGroupControls(group);
          const bar = state.controlsByKind(state.groupSubject(group), "none");
          await state.executeCommand(group, bar[0]);
          console.log(JSON.stringify({
            subject: state.groupSubject(group),
            barKeys: bar.map((command) => command.key),
            adjustable: state.hasAdjustableControls(state.groupSubject(group)),
            calls,
          }));
        })();
        """
    )

    assert values["subject"] == "g1"
    assert values["barKeys"] == ["g1_on"]
    assert values["adjustable"] is True
    assert values["calls"] == [
        ["GET", "/api/groups/1/controls", None],
        ["POST", "/api/commands/g1_on", {"value": "1"}],
    ]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_group_is_never_blocked_for_being_offline_and_names_its_seed_device():
    """Two rules of the control modal once a group can open it.

    `subjectOffline` must answer false for a group: a group has no online
    state, and an aggregate over its members would be the invention design
    section 2 rules out. If it answered from the members, a group with one
    dead lamp would refuse to switch the five live ones - while the
    fan-out itself reports 502 WITH the names (design 3.2).

    `controlSeedLabel` must name the member the slider position came from,
    and must stay empty for a device - a device's own value needs no
    attribution, and the same binding serves both in the markup."""
    values = _app_state(
        """
        state.devices = [
          { id: 4, label: "Lamp Kitchen", room: "Kitchen", category: "light", online: false },
          { id: 5, label: "Lamp Table", room: "Kitchen", category: "light", online: true },
        ];
        state.groups = [
          { id: 1, label: "Ceiling", room: "Kitchen", category: "light",
            member_ids: [4, 5], member_labels: ["Lamp Kitchen", "Lamp Table"],
            command_count: 2 },
        ];
        state.controlsBySubject = {
          4: { commands: [], hidden_raw_commands: 0 },
          g1: { commands: [], hidden_raw_commands: 0,
                seed_device_id: 4, seed_device_label: "Lamp Kitchen" },
        };
        console.log(JSON.stringify({
          offlineDevice: state.subjectOffline(4),
          onlineDevice: state.subjectOffline(5),
          group: state.subjectOffline("g1"),
          groupSeed: state.controlSeedLabel("g1"),
          deviceSeed: state.controlSeedLabel(4),
          unloadedSeed: state.controlSeedLabel("g9"),
        }));
        """
    )

    assert values["offlineDevice"] is True
    assert values["onlineDevice"] is False
    assert values["group"] is False
    assert values["groupSeed"] == "Lamp Kitchen"
    assert values["deviceSeed"] == ""
    assert values["unloadedSeed"] == ""


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_refused_member_list_shows_the_servers_own_sentence():
    """The store's 400 detail already names the offending device and both
    categories. The dialog shows it verbatim: a generic "could not save"
    in its place would throw away the only part that says what to do next
    (Spec 8.1). The dialog also has to STAY open - the draft is still the
    user's to fix.

    The membership write goes out as the whole list (`PUT`), not add/remove
    per device: the intersection is recomputed after every change anyway,
    and two single removals would pass through an intermediate state
    nobody asked for, keys included (design 5)."""
    values = _app_state(
        """
        const calls = [];
        state.request = async (method, path, body) => {
          calls.push([method, path, body]);
          if (method === "PUT") {
            const error = new Error("device 2 is a socket, the group takes light");
            error.status = 400;
            throw error;
          }
          throw new Error("unexpected " + method + " " + path);
        };
        state.groups = [
          { id: 7, label: "Lights", room: null, category: "light",
            member_ids: [1], member_labels: ["Lamp"], command_count: 2 },
        ];
        state.groupDraft = { id: 7, label: "Lights", room: "", memberIds: [1, 2],
                             roomTouched: true };
        (async () => {
          await state.saveGroupDialog();
          console.log(JSON.stringify({
            error: state.groupDialogError,
            busy: state.groupDialogBusy,
            draftKept: state.groupDraft.memberIds,
            calls,
          }));
        })();
        """
    )

    assert values["error"] == "device 2 is a socket, the group takes light"
    # Released in `finally`, so a second attempt is possible at all.
    assert values["busy"] is False
    assert values["draftKept"] == [1, 2]
    assert values["calls"] == [["PUT", "/api/groups/7/members", {"member_ids": [1, 2]}]]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_creating_a_group_reloads_the_list_and_every_groups_commands():
    """A membership change recomputes the command intersection server-side
    (design 4.3), so the tile has to re-read it - a tile keeping the old
    list would offer a command whose key now answers 404. Reloaded for
    every group, not only the one just touched: a device that joined here
    may have left another group's list in the same breath.

    The room travels as the empty string for "no room" - the same encoding
    the device path uses and the value the API expects (design 4.1). The
    POST body is recorded and asserted for that reason, the same as the
    sibling test `test_a_refused_member_list_shows_the_servers_own_sentence`
    already does for its PUT - a claim about what goes out on the wire is
    only proven by looking at the wire."""
    values = _app_state(
        """
        const calls = [];
        state.request = async (method, path, body) => {
          calls.push([method, path, body]);
          if (method === "GET" && path === "/api/groups") {
            return [
              { id: 1, label: "Ceiling", room: null, category: "light",
                member_ids: [1], member_labels: ["Lamp"], command_count: 2 },
              { id: 2, label: "Outside", room: null, category: "light",
                member_ids: [2], member_labels: ["Lamp B"], command_count: 2 },
            ];
          }
          if (method === "GET" && path.endsWith("/controls")) {
            return { commands: [], hidden_raw_commands: 0,
                     seed_device_id: 1, seed_device_label: "Lamp" };
          }
          return { id: 1 };
        };
        state.$nextTick = () => {};
        state.$refs = { groupDialog: { close: () => {}, showModal: () => {} } };
        state.groupDraft = { id: null, label: "Ceiling", room: "", memberIds: [1],
                             roomTouched: false };
        (async () => {
          await state.saveGroupDialog();
          console.log(JSON.stringify({
            calls,
            error: state.groupDialogError,
            labels: state.groups.map((group) => group.label),
            loadedSubjects: Object.keys(state.controlsBySubject).sort(),
          }));
        })();
        """
    )

    assert values["error"] is None
    assert values["calls"][0] == [
        "POST",
        "/api/groups",
        {"label": "Ceiling", "room": "", "member_ids": [1]},
    ]
    assert values["calls"][1] == ["GET", "/api/groups", None]
    assert sorted(values["calls"][2:]) == [
        ["GET", "/api/groups/1/controls", None],
        ["GET", "/api/groups/2/controls", None],
    ]
    assert values["labels"] == ["Ceiling", "Outside"]
    assert values["loadedSubjects"] == ["g1", "g2"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_deleting_a_group_drops_its_controls_and_closes_a_modal_over_it():
    """A group that is gone must not leave its commands behind in
    `controlsBySubject` - a stale entry there is what `controlsLoaded`
    reads, and the next group to be given the same id would inherit it.
    A control modal standing open over it would become an empty box behind
    its own `x-if` guard, which is the reason `removeDevice` does the same
    two things."""
    values = _app_state(
        """
        let closed = false;
        state.request = async () => null;
        state.groups = [
          { id: 1, label: "Ceiling", room: null, category: "light",
            member_ids: [], member_labels: [], command_count: 0 },
          { id: 2, label: "Outside", room: null, category: "light",
            member_ids: [], member_labels: [], command_count: 0 },
        ];
        state.controlsBySubject = { g1: { commands: [], hidden_raw_commands: 0 },
                                    g2: { commands: [], hidden_raw_commands: 0 } };
        state.controlModalDevice = "g1";
        state.$refs = { controlModal: { close: () => { closed = true; } } };
        global.window = { confirm: () => true };
        (async () => {
          await state.removeGroup(state.groups[0]);
          console.log(JSON.stringify({
            left: state.groups.map((group) => group.id),
            subjects: Object.keys(state.controlsBySubject),
            closed,
            error: state.groupActionError,
          }));
        })();
        """,
        translations={"web.groups.delete_confirm": "delete {label} ({id})?"},
    )

    assert values["left"] == [2]
    assert values["subjects"] == ["g2"]
    assert values["closed"] is True
    assert values["error"] is None


async def test_the_group_dialog_waits_for_the_translation_table(api):
    """MEASURED in a browser harness, not reasoned about, and the reason
    this delivery test exists at all.

    The two modals above build their content inside an `x-for`/`x-if` that
    is false at startup, so every `t(...)` in them runs after
    `GET /api/i18n` has answered. The group dialog's body has no such
    condition of its own: without `x-if="stringsReady"` Alpine builds it on
    its FIRST pass, while `translationStrings` is still empty, and `t()`
    returns the bare key then (by design - see its comment in app.js).
    Nothing in that markup carries a reactive dependency that would ever
    re-evaluate it, so "web.groups.label_field" and
    "web.groups.members_heading" stood in the open dialog for the rest of
    the page's life. Both were visible in the harness before this guard.

    The `<dialog>` element itself must stay outside the guard, or
    `$refs.groupDialog` would not exist when `showModal()` is called."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    start = markup.index('x-ref="groupDialog"')
    dialog = markup[start : markup.index("</dialog>", start)]
    body = dialog.index('class="signals-modal-body"')
    guard = dialog.index('x-if="stringsReady"')
    assert guard < body, "the dialog body must sit INSIDE the stringsReady guard"


async def test_the_seed_attribution_only_appears_where_there_is_a_value(api):
    """Also measured in the harness: a group's Kelvin row showed "Start
    value unknown" and "Initial value from Lamp Kitchen" one under the
    other, because the intersected range gave the slider a position while
    the seed device had never reported a colour temperature. With no start
    value there is nothing to attribute, and the sentence above it is the
    whole truth.

    Every one of the three sliders carries the same pair of conditions -
    the seed label AND its own draft value. The device path is unaffected:
    `controlSeedLabel` answers "" for a device, so the line never shows
    there at all."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    shows = re.findall(
        r'x-show="([^"]*)"[^>]*x-text="t\(\'web\.groups\.seed_from\'',
        markup,
        flags=re.DOTALL,
    )
    assert len(shows) == 3, f"expected one attribution per slider kind, found {shows}"
    for condition, draft in zip(shows, ("percent", "kelvin", "hue")):
        assert "controlSeedLabel(controlModalDevice)" in condition
        assert f"controlDrafts.{draft} !== undefined" in condition


# --- Group dialog: the member picker grouped by room (design canvas, B) -----


_PICKER_DEVICES = """
  state.devices = [
    { id: 1, label: "Pendant", room: "Dining", category: "light", online: true },
    { id: 2, label: "Ceiling", room: "Kitchen", category: "light", online: true },
    { id: 3, label: "Coffee plug", room: "Kitchen", category: "socket", online: true },
    { id: 4, label: "Terrace", room: null, category: "light", online: true },
    { id: 5, label: "Armchair", room: "Living", category: "light", online: true },
  ];
  state.groups = [
    { id: 7, label: "Lights", room: null, category: "light",
      member_ids: [2], member_labels: ["Ceiling"], command_count: 2 },
  ];
"""


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_picker_groups_candidates_by_room_with_no_room_last():
    """The picker is sorted the way the room chips are: named rooms
    alphabetically, "No room" at the very end - the same order a user
    already reads above the tile grid, so a lamp is found where it hangs.
    Every candidate appears in exactly one section, in label order."""
    values = _app_state(
        _PICKER_DEVICES
        + """
        console.log(JSON.stringify({ sections: state.groupCandidateSections().map((s) => ({
          key: s.key, label: s.label, ids: s.devices.map((d) => d.id),
        })) }));
        """,
        translations={"web.devices.room_none": "No room"},
    )

    assert values["sections"] == [
        {"key": "Dining", "label": "Dining", "ids": [1]},
        {"key": "Kitchen", "label": "Kitchen", "ids": [2, 3]},
        {"key": "Living", "label": "Living", "ids": [5]},
        {"key": "", "label": "No room", "ids": [4]},
    ]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_pick_greys_out_other_kinds_but_keeps_them_in_place():
    """The user's explicit call on the design canvas: once the first
    device is picked, devices of another kind must NOT disappear from the
    list - they stay in their room, greyed out and unpickable, so it is
    visible what just happened. A picker that filtered them out would
    pass every "is it disabled" test and still break exactly that."""
    values = _app_state(
        _PICKER_DEVICES
        + """
        state.toggleGroupMember(state.devices[1]);
        const sections = state.groupCandidateSections();
        const kitchen = sections.find((s) => s.key === "Kitchen");
        console.log(JSON.stringify({
          everyId: sections.flatMap((s) => s.devices.map((d) => d.id)).sort(),
          kitchenIds: kitchen.devices.map((d) => d.id),
          plugDisabled: state.groupCandidateState(state.devices[2]).disabled,
          lampDisabled: state.groupCandidateState(state.devices[0]).disabled,
        }));
        """,
        translations={"web.devices.room_none": "No room"},
    )

    assert values["everyId"] == [1, 2, 3, 4, 5]
    assert values["kitchenIds"] == [2, 3]
    assert values["plugDisabled"] is True
    assert values["lampDisabled"] is False


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_clearing_the_members_unlocks_a_new_group_but_not_an_existing_one():
    """Clearing empties the selection. For a NEW group that also lifts the
    kind lock, because the lock only ever came from the first pick. For an
    EXISTING group it must not: its kind is stored (design 2), and the
    server refuses a foreign member for an emptied group too - unlocking
    here would turn a greyed-out entry into a 400 after saving.

    The room follows the same rule as picking: an untouched, prefilled
    room goes back to empty with the members; a typed room stays."""
    values = _app_state(
        _PICKER_DEVICES
        + """
        const out = {};
        state.toggleGroupMember(state.devices[1]);
        out.roomBefore = state.groupDraft.room;
        state.clearGroupMembers();
        out.createIds = [...state.groupDraft.memberIds];
        out.createCategory = state.groupDraftCategory();
        out.createRoom = state.groupDraft.room;

        state.toggleGroupMember(state.devices[1]);
        state.groupDraft.room = "Ground floor";
        state.groupDraft.roomTouched = true;
        state.clearGroupMembers();
        out.typedRoom = state.groupDraft.room;

        state.groupDraft = { id: 7, label: "Lights", room: "", memberIds: [2],
                             roomTouched: true };
        state.clearGroupMembers();
        out.editIds = [...state.groupDraft.memberIds];
        out.editCategory = state.groupDraftCategory();
        console.log(JSON.stringify(out));
        """
    )

    assert values["roomBefore"] == "Kitchen"
    assert values["createIds"] == []
    assert values["createCategory"] is None
    assert values["createRoom"] == ""
    assert values["typedRoom"] == "Ground floor"
    assert values["editIds"] == []
    assert values["editCategory"] == "light"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_kind_summary_names_the_locked_kind_and_the_count():
    """The pill above the list says which kind the group is locked to and
    how many devices are picked. Nothing to say while a new group is still
    open; an emptied EXISTING group still names its stored kind."""
    values = _app_state(
        _PICKER_DEVICES
        + """
        const out = { none: state.groupKindSummary() };
        state.toggleGroupMember(state.devices[1]);
        state.toggleGroupMember(state.devices[0]);
        out.two = state.groupKindSummary();
        state.groupDraft = { id: 7, label: "Lights", room: "", memberIds: [],
                             roomTouched: true };
        out.editEmptied = state.groupKindSummary();
        console.log(JSON.stringify(out));
        """,
        translations={
            "web.devices.category.light": "Light",
            "web.groups.kind_selected": "{category} · {count} selected",
        },
    )

    assert values["none"] == ""
    assert values["two"] == "Light · 2 selected"
    assert values["editEmptied"] == "Light · 0 selected"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_create_button_says_why_it_is_disabled():
    """A disabled button with no reason is a silent failure (Spec 8.1).
    The reason covers all four states of a new group. Editing an existing
    group may save any member list, including an empty one, so there is
    never a reason there - and the button's `disabled` reads this same
    helper, so the text and the state cannot disagree."""
    values = _app_state(
        _PICKER_DEVICES
        + """
        const out = {};
        out.both = state.groupDialogBlockedReason();
        state.groupDraft.label = "Kitchen";
        out.member = state.groupDialogBlockedReason();
        state.toggleGroupMember(state.devices[1]);
        out.ready = state.groupDialogBlockedReason();
        state.groupDraft.label = "   ";
        out.name = state.groupDialogBlockedReason();
        state.groupDraft = { id: 7, label: "", room: "", memberIds: [], roomTouched: true };
        out.editing = state.groupDialogBlockedReason();
        console.log(JSON.stringify(out));
        """,
        translations={
            "web.groups.blocked_name_and_member": "Name and at least one device missing",
            "web.groups.blocked_name": "Name missing",
            "web.groups.blocked_member": "Pick at least one device",
        },
    )

    assert values["both"] == "Name and at least one device missing"
    assert values["member"] == "Pick at least one device"
    assert values["ready"] == ""
    assert values["name"] == "Name missing"
    assert values["editing"] == ""


def _css_media_blocks(css: str, query: str) -> list[str]:
    """The bodies of every `@media <query> { ... }` block, brace-matched.
    All of them, not the first: the stylesheet has several blocks with the
    same query, and a lookup that stopped at the first would miss a rule
    appended to a later one."""
    blocks: list[str] = []
    start = 0
    head = f"@media {query}"
    while (at := css.find(head, start)) != -1:
        open_at = css.index("{", at)
        depth, i = 0, open_at
        while True:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        blocks.append(css[open_at + 1 : i])
        start = i
    return blocks


async def test_the_group_picker_markup_is_delivered_and_stacks_on_a_phone(api):
    """Delivery only - the browser harness is what proves the bindings run.
    The rows come from the room sections, carry the lock reason as their
    tooltip (it moved out of the row text, it did not go away), use the
    check mark from the sprite, and the button's `disabled` reads the same
    helper as the sentence beside it.

    The phone half: at the dialog breakpoint the name and room fields
    stack into ONE column. Two columns of ~150px each on a 375px screen
    leave no room to type a room name."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    start = markup.index('x-ref="groupDialog"')
    dialog = markup[start : markup.index("</dialog>", start)]

    assert "groupCandidateSections()" in dialog
    assert ':title="groupCandidateState(device).reason' in dialog
    assert "#i-check" in dialog
    assert '<symbol id="i-check"' in markup
    assert 'class="group-dialog-fields"' in dialog
    assert "groupDialogBlockedReason()" in dialog
    assert ':disabled="groupDialogBusy || groupDialogBlockedReason()' in dialog

    css = (await client.get("/static/style.css")).text
    phone = [b for b in _css_media_blocks(css, "(max-width: 640px)") if ".group-dialog-fields" in b]
    assert phone, "no 640px rule stacks the group dialog fields"
    rule = phone[0][phone[0].index(".group-dialog-fields") :]
    rule = rule[: rule.index("}")]
    assert "grid-template-columns: minmax(0, 1fr)" in rule


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_transport_badge_helper_maps_transports_to_symbols_and_labels():
    """Runs the real `transportBadge` in node. Fault to prove it: swap the
    two symbol names in its map."""
    values = _app_state(
        """
        console.log(JSON.stringify({
          thread: state.transportBadge({ transport: "thread" }),
          ip: state.transportBadge({ transport: "ip" }),
          none: state.transportBadge({ transport: null }),
          zigbee: state.transportBadge({ transport: "zigbee" }),
        }));
        """,
        translations={
            "web.devices.transport_thread": "Matter over Thread",
            "web.devices.transport_ip": "Matter over IP",
        },
    )
    assert values["thread"] == {"symbol": "i-transport-thread", "label": "Matter over Thread"}
    assert values["ip"] == {"symbol": "i-transport-ip", "label": "Matter over IP"}
    assert values["none"] is None
    # Zigbee has had its glyph since the Zigbee source design (2026-09-12,
    # section 3.3); `test_the_zigbee_badge_has_a_glyph_now_that_the_spec_adds_one`
    # checks what it maps to.
    assert values["zigbee"]["symbol"] == "i-transport-zigbee"


async def test_every_transport_badge_symbol_exists(api):
    """A `<use>` pointing at a missing symbol silently draws nothing - the
    same reason `test_every_category_has_an_icon_symbol` exists."""
    client, _, _ = api
    page = (await client.get("/")).text
    for symbol in ("i-transport-thread", "i-transport-ip", "i-transport-zigbee"):
        assert f'<symbol id="{symbol}"' in page, symbol


def _element_at(markup: str, open_index: int, tag: str) -> str:
    """The complete element whose opening tag starts at `open_index`,
    found by counting nested opening and closing tags of the same name -
    so "inside" means inside, not merely "somewhere after"."""
    depth = 0
    for match in re.finditer(rf"<{tag}\b|</{tag}>", markup[open_index:]):
        depth += -1 if match.group().startswith("</") else 1
        if depth == 0:
            return markup[open_index : open_index + match.end()]
    raise AssertionError(f"unbalanced <{tag}> starting at {open_index}")


async def test_the_badge_sits_inside_the_device_tiles_category_icon(api):
    """Only the device tile - a group has no single transport. Checks the
    badge is nested in the `.type-badge` that shows `device.category`, not
    merely present somewhere after it."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    icon = page.index("'#i-cat-' + device.category")
    type_badge = _element_at(page, page.rindex('<span class="type-badge">', 0, icon), "span")
    assert "transportBadge(device)" in type_badge
    assert "transportBadge(deviceGroup)" not in page


def test_the_transport_labels_exist_in_both_languages():
    from loxmatter import i18n

    for key in (
        "web.devices.transport_thread",
        "web.devices.transport_ip",
        "web.devices.transport_zigbee",
    ):
        english = i18n.t(key)
        i18n.set_language("de")
        try:
            german = i18n.t(key)
        finally:
            i18n.set_language("en")
        assert english and german and english != key


# ---------------------------------------------------------------------------
# The radios card (Task 7, design "Radios in the Web UI", 2026-09-11,
# section 8): choosing the Thread stick and Bluetooth adapter from the web
# UI, backed by Task 6's GET/POST /api/radios.
# ---------------------------------------------------------------------------

RADIOS_READY = {
    "sidecar": "ready",
    "updater_stack_host_path": "/home/pi/stack",
    "serial": [
        {
            "path": "/dev/serial/by-id/usb-A",
            "tty": "ttyUSB0",
            "manufacturer": "SONOFF",
            "product": "SONOFF Dongle Plus MG24",
            "serial": "e26a50c9",
            "vid_pid": "10c4:ea60",
        },
        {
            "path": "/dev/serial/by-id/usb-B",
            "tty": "ttyACM0",
            "manufacturer": None,
            "product": None,
            "serial": None,
            "vid_pid": None,
        },
    ],
    "bluetooth": [
        {"index": 0, "name": "hci0", "bus": "uart", "product": None, "rfkill_blocked": False}
    ],
    "current": {
        "thread_enabled": True,
        "thread_device": "/dev/serial/by-id/usb-A",
        "thread_device_present": True,
        "bluetooth_adapter": 0,
        "otbr_running": True,
    },
    "job": None,
}


def _radios_values(setup: str, *, translations: dict[str, str] | None = None) -> dict:
    return _app_state(
        f"state.radios = {json.dumps(RADIOS_READY)};\n"
        "state.radiosDraft = { threadDevice: '/dev/serial/by-id/usb-A', bluetoothAdapter: 0 };\n"
        + setup,
        translations=translations,
    )


def _attr_before_t_key(markup: str, attr: str, t_key: str) -> str:
    """Like `_x_show_expr` above, generalized to any attribute - the
    literal `attr="..."` on the tag whose own `x-text` calls
    `t(t_key, ...)`, pulled from the SERVED markup rather than retyped."""
    match = re.search(
        re.escape(attr) + r'="([^"]*)"[^>]*x-text="t\(\'' + re.escape(t_key) + r"'",
        markup,
        flags=re.DOTALL,
    )
    assert match, f"no {attr} immediately precedes t('{t_key}', ...) in the markup"
    return match.group(1)


def _class_before_text(markup: str, text_needle: str) -> str:
    """Like `_x_show_expr`, generalized to `class` instead of `x-show`
    and to a plain substring instead of a `t(...)` call - for markup
    whose `x-text` concatenates a translated string with other JS rather
    than calling `t()` directly (the rfkill warning: `option.label + ':
    ' + t('web.radios.rfkill_blocked')`)."""
    match = re.search(
        r'class="([^"]*)"[^>]*x-text="[^"]*' + re.escape(text_needle),
        markup,
        flags=re.DOTALL,
    )
    assert match, f"no class attr found before an x-text containing {text_needle!r}"
    return match.group(1)


def _bluetooth_option_exprs(markup: str) -> tuple[str, str]:
    """The `(:disabled, x-text)` pair on the Bluetooth `<option>` inside
    the radios card, pulled from the SERVED markup - the same "extract
    the real expression, don't retype it" technique `_running_step_lis`
    above already uses."""
    start = markup.index('x-model.number="radiosDraft.bluetoothAdapter"')
    end = markup.index("</select>", start)
    block = markup[start:end]
    match = re.search(r':disabled="([^"]*)"\s*\n\s*x-text="([^"]*)"', block, flags=re.DOTALL)
    assert match, "no :disabled/x-text pair found on the Bluetooth <option>"
    return match.group(1), match.group(2)


def _eval_js_expr(expr: str, **bindings: str) -> object:
    """Evaluates `expr` in node with each keyword argument predefined as a
    name bound to already-SERIALIZED JS source (not a Python value) - the
    same convention `_eval_js` above uses for `t`, extended to arbitrary
    names so a markup expression that reads e.g. `option` and calls `t`
    can be evaluated exactly as extracted."""
    preamble = "\n".join(f"const {name} = {value};" for name, value in bindings.items())
    script_src = f"{preamble}\nconsole.log(JSON.stringify({expr}));\n"
    result = subprocess.run(
        [NODE, "-e", script_src], capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_radios_card_detects_a_change_and_picks_the_confirmation_text():
    """Runs the real helpers in node. Fault to prove it: return
    `confirm_thread_on` for a stick switch."""
    values = _radios_values(
        """
        const out = { unchanged: state.radiosChanged() };
        state.radiosDraft.threadDevice = '/dev/serial/by-id/usb-B';
        out.switch = [state.radiosChanged(), state.radiosConfirmKeys()];
        state.radiosDraft.threadDevice = '';
        out.off = state.radiosConfirmKeys();
        state.radiosDraft = { threadDevice: '/dev/serial/by-id/usb-A', bluetoothAdapter: 1 };
        out.bluetooth = state.radiosConfirmKeys();
        state.radios.current.thread_enabled = false;
        state.radios.current.thread_device = null;
        out.on = state.radiosConfirmKeys();
        console.log(JSON.stringify(out));
        """
    )
    assert values["unchanged"] is False
    assert values["switch"] == [True, ["web.radios.confirm_thread_switch"]]
    assert values["off"] == ["web.radios.confirm_thread_off"]
    # Task 7d: a Bluetooth-only change says outright that Thread is not
    # touched, and names no Thread restart - because none happens.
    assert values["bluetooth"] == [
        "web.radios.confirm_bluetooth",
        "web.radios.confirm_thread_untouched",
    ]
    # A change that really does move Thread keeps the restart warning and
    # drops the reassurance.
    assert values["on"] == ["web.radios.confirm_thread_on", "web.radios.confirm_bluetooth"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_only_the_halves_the_user_changed_are_sent():
    """Task 7d: the request body carries `null` for a radio the user did
    not touch, which every layer below reads as "leave this one alone".

    Runs the real `confirmApplyRadios()` and captures what it actually
    POSTs, rather than inspecting the helper it calls - the body is the
    thing the sidecar acts on. `setInterval` is stubbed because a
    successful POST arms the 2 s poll, which would otherwise keep node
    alive past the end of the script.

    Fault to prove it: send both halves unconditionally again (the
    pre-7d body), whereupon the first case below carries a full Thread
    half and the sidecar reads a legacy `.env` as a stick switch."""
    values = _radios_values(
        """
        globalThis.setInterval = () => 1;
        globalThis.clearInterval = () => {};
        const bodies = [];
        state.request = async (method, url, body) => {
          if (method === 'POST') { bodies.push(body); return { id: 'j' }; }
          return state.radios;
        };
        (async () => {
          state.radiosDraft = { threadDevice: '/dev/serial/by-id/usb-A', bluetoothAdapter: 1 };
          await state.confirmApplyRadios();
          state.radiosDraft = { threadDevice: '/dev/serial/by-id/usb-B', bluetoothAdapter: 0 };
          await state.confirmApplyRadios();
          state.radiosDraft = { threadDevice: '', bluetoothAdapter: 1 };
          await state.confirmApplyRadios();
          console.log(JSON.stringify(bodies));
        })();
        """
    )
    assert values == [
        {"thread": None, "bluetooth": {"adapter": 1}},
        {"thread": {"enabled": True, "device": "/dev/serial/by-id/usb-B"}, "bluetooth": None},
        {"thread": {"enabled": False, "device": None}, "bluetooth": {"adapter": 1}},
    ]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_an_unplugged_stick_does_not_turn_a_bluetooth_change_into_a_thread_change():
    """The reported defect, at the card. The sidecar reports the
    installer's legacy `/dev/ttyUSB0` and the stick is unplugged, so
    `api/radios.py` maps nothing and reports that raw value with
    `thread_device_present: false`. The card shows it as "(missing)" and
    keeps it selected - it must NOT seed the draft to "no stick", which
    would turn a fallen-out stick into a request to stop the border
    router.

    What this pins: in that state a Bluetooth change is a Bluetooth
    change. The Thread half reads as unchanged, is sent as `null`, and the
    confirmation neither promises a Thread restart nor stays silent about
    it. Fault to prove it: compare the draft against
    `radios.serial`/presence rather than against
    `radiosCurrentThread()`, or drop the `radiosThreadChanged()` guard
    around the Thread confirmation keys."""
    values = _radios_values(
        """
        state.radios.current.thread_device = '/dev/ttyUSB0';
        state.radios.current.thread_device_present = false;
        state.radiosDraft = { threadDevice: '/dev/ttyUSB0', bluetoothAdapter: 1 };
        console.log(JSON.stringify({
          threadChanged: state.radiosThreadChanged(),
          bluetoothChanged: state.radiosBluetoothChanged(),
          changed: state.radiosChanged(),
          keys: state.radiosConfirmKeys(),
          body: state.radiosRequestBody(),
          missingOptionStillSelected:
            state.radiosThreadOptions().some((o) => o.value === '/dev/ttyUSB0' && o.missing),
        }));
        """
    )
    assert values["threadChanged"] is False
    assert values["bluetoothChanged"] is True
    assert values["changed"] is True
    assert values["missingOptionStillSelected"] is True
    assert values["keys"] == [
        "web.radios.confirm_bluetooth",
        "web.radios.confirm_thread_untouched",
    ]
    assert "web.radios.confirm_thread_switch" not in values["keys"]
    assert "web.radios.confirm_thread_off" not in values["keys"]
    assert values["body"] == {"thread": None, "bluetooth": {"adapter": 1}}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_thread_options_mark_the_current_stick_and_a_missing_one():
    values = _radios_values(
        """
        const out = { normal: state.radiosThreadOptions() };
        state.radios.current.thread_device = '/dev/ttyUSB7';
        state.radios.current.thread_device_present = false;
        out.missing = state.radiosThreadOptions();
        console.log(JSON.stringify(out));
        """
    )
    normal = values["normal"]
    assert [o["value"] for o in normal] == [
        "",
        "/dev/serial/by-id/usb-A",
        "/dev/serial/by-id/usb-B",
    ]
    assert [o["inUse"] for o in normal] == [False, True, False]
    missing = values["missing"]
    assert missing[-1] == {
        "value": "/dev/ttyUSB7",
        "label": "web.radios.missing",
        "inUse": True,
        "missing": True,
        "zigbee": False,
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_radios_job_states_map_to_step_classes_and_results():
    values = _radios_values(
        """
        state.radios.job = { id: 'j', phase: 'verify_thread', steps: ['validate','backup','write','apply_thread','verify_thread'],
                             error: null, rolled_back: false, healthy: null };
        const out = { running: state.radiosJobRunning(),
                      classes: state.radios.job.steps.map((s) => state.radiosStepClass(s)) };
        state.radios.job.phase = 'failed'; state.radios.job.error = 'verify_thread_failed';
        state.radios.job.rolled_back = true; state.radios.job.healthy = true;
        out.failed = [state.radiosJobRunning(), state.radiosResultKey()];
        state.radios.job.healthy = false;
        out.unhealthy = state.radiosResultKey();
        console.log(JSON.stringify(out));
        """
    )
    assert values["running"] is True
    assert values["classes"] == [
        {"done": True, "now": False},
        {"done": True, "now": False},
        {"done": True, "now": False},
        {"done": True, "now": False},
        {"done": False, "now": True},
    ]
    assert values["failed"] == [False, "web.radios.result_failed_restored"]
    assert values["unhealthy"] == "web.radios.result_failed_unhealthy"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_sidecar_message_depends_on_the_sidecar_state():
    values = _radios_values(
        """
        const out = {};
        for (const s of ['ready', 'missing', 'outdated', 'unmounted']) {
          state.radios.sidecar = s; out[s] = state.radiosSidecarMessage();
        }
        state.radios.sidecar = 'outdated'; state.radios.updater_stack_host_path = null;
        out.nopath = state.radiosSidecarMessage();
        console.log(JSON.stringify(out));
        """
    )
    assert values["ready"] is None
    assert values["missing"] == "web.radios.sidecar_missing"
    assert values["outdated"] == values["unmounted"] == "web.radios.sidecar_refresh"
    assert values["nopath"] == "web.radios.sidecar_refresh_unknown_path"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_polling_continues_until_the_posted_job_appears():
    """Fault to prove it: drop the `radiosPendingJobId` condition from
    `keepPolling` - the interval then stops on the old job's result."""
    values = _app_state(
        f"""
        let intervals = 0;
        globalThis.setInterval = () => {{ intervals += 1; return 1; }};
        globalThis.clearInterval = () => {{ intervals -= 1; }};
        const old = {json.dumps({**RADIOS_READY, "job": {"id": "old", "phase": "done", "steps": [], "error": None, "rolled_back": False, "healthy": True}})};
        const fresh = JSON.parse(JSON.stringify(old)); fresh.job.id = "new";
        let answer = old;
        state.request = async () => answer;
        (async () => {{
          state.radiosPendingJobId = "new";
          await state.loadRadios();
          const afterOld = intervals;
          answer = fresh;
          await state.loadRadios();
          console.log(JSON.stringify({{ afterOld, afterNew: intervals, pending: state.radiosPendingJobId }}));
        }})();
        """
    )
    assert values == {"afterOld": 1, "afterNew": 0, "pending": None}


async def test_the_radios_card_sits_in_the_settings_view(api):
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    settings = page[page.index("view === 'settings'") :]
    card = settings[: settings.index("t('web.settings.language_heading')")]
    for marker in (
        "t('web.radios.heading')",
        "radiosThreadOptions()",
        "radiosBluetoothOptions()",
        "askApplyRadios()",
        "confirmApplyRadios()",
        "radiosStepClass(",
    ):
        assert marker in card, marker


def test_the_radios_texts_exist_in_both_languages():
    """Reads the table directly: `i18n.raw_template` falls back to English
    when `de` is missing, so it could never see a missing translation.
    Fault to prove it: delete one `de:` line under `web.radios.*`."""
    from loxmatter import i18n

    keys = i18n.strings_with_prefix("web.radios.")
    assert "web.radios.confirm_thread_switch" in keys
    for key in keys:
        entry = i18n._STRINGS[key]
        assert entry.get("en") and entry.get("de"), key


# ---------------------------------------------------------------------------
# Radios card review fixes (2026-09-12): the reviewer's two Critical findings
# (a failed Apply that shows nothing, and a job that stops being advanced but
# never stops looking like it is running) plus five smaller ones, and the
# untested surface the reviewer named directly: `confirmApplyRadios`,
# `askApplyRadios`, `cancelApplyRadios`, `radiosReason`, `radiosBluetoothOptions`
# (never called by any test above), and the `:disabled`/`x-show` bindings on
# the card.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_failed_apply_leaves_its_error_on_screen_after_the_refresh():
    """Review-Fix Critical #1: `loadRadios()`'s own first statement sets
    `this.radiosError = null` synchronously, before its first `await` -
    so a `catch` that writes straight to `this.radiosError` and then
    unconditionally awaits `loadRadios()` clears the error in the same
    turn, before Alpine ever gets to render it (the reviewer measured
    this in node: `radiosError === null` right after a rejected POST).
    The stubbed `request` here throws only for the POST and succeeds for
    the GET `loadRadios()` makes afterwards, reproducing exactly that
    sequence. The error now lives in `radiosApplyError`, a field no poll
    touches (see the 409 test below for why re-applying it to
    `radiosError` afterwards was not enough); `radiosError` staying `null`
    is what proves the two are not the same field again. Fault to prove
    it: write `this.radiosError = error.message` in
    `confirmApplyRadios()`'s `catch` instead."""
    values = _radios_values(
        """
        state.request = async (method) => {
          if (method === 'POST') throw new Error('unknown adapter');
          return state.radios;
        };
        (async () => {
          await state.confirmApplyRadios();
          console.log(JSON.stringify({
            error: state.radiosApplyError,
            loadError: state.radiosError,
            busy: state.radiosBusy,
          }));
        })();
        """
    )
    assert values == {"error": "unknown adapter", "loadError": None, "busy": False}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_rejected_apply_survives_the_poll_that_the_rejection_itself_arms():
    """The 409 case, which the fix above did not reach: "a radio change is
    already running" comes back when a job was started somewhere else
    (another tab, a phone). The `loadRadios()` that `confirmApplyRadios()`
    always runs afterwards therefore SEES that running job and arms the
    two-second poll - and that poll's first tick runs
    `this.radiosError = null` again, erasing the explanation a second
    after the user read it, leaving a card that silently did nothing they
    asked for. Every other rejection (503, 400) leaves nothing running, so
    no timer is armed and re-applying the message after `loadRadios()`
    genuinely did fix those.

    Runs the real timer callback rather than waiting two seconds for it.
    Fault to prove it: put the apply error back on `this.radiosError`
    (re-applied after `loadRadios()`, exactly as the previous fix had it) -
    `afterTick` then reads `null`."""
    running = {
        **RADIOS_READY,
        "job": {
            "id": "elsewhere",
            "phase": "apply_thread",
            "steps": ["validate", "backup", "write", "apply_thread"],
            "error": None,
            "rolled_back": False,
            "healthy": None,
        },
    }
    values = _app_state(
        f"""
        let tick = null;
        globalThis.setInterval = (fn) => {{ tick = fn; return 7; }};
        globalThis.clearInterval = () => {{ tick = null; }};
        state.radios = {json.dumps(running)};
        state.request = async (method) => {{
          if (method === 'POST') throw new Error('a radio change is already running');
          return {json.dumps(running)};
        }};
        (async () => {{
          await state.confirmApplyRadios();
          const afterApply = state.radiosApplyError;
          const armed = tick !== null;
          await tick();
          console.log(JSON.stringify({{ afterApply, armed, afterTick: state.radiosApplyError }}));
        }})();
        """
    )
    assert values == {
        "afterApply": "a radio change is already running",
        "armed": True,
        "afterTick": "a radio change is already running",
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_ask_apply_radios_is_a_noop_without_a_change_and_cancel_clears_dirty():
    """`askApplyRadios`/`cancelApplyRadios` were untested per the
    reviewer. Also covers Review-Fix Minor #8's cancel half: leaving
    `radiosDirty` set after Cancel used to make `loadRadios()`'s own
    `if (current && !this.radiosDirty)` guard skip its draft resync for
    the rest of the session. Fault to prove it (two, one per behaviour):
    (a) drop the `if (!this.radiosChanged()) return;` guard from
    `askApplyRadios()`; (b) drop `this.radiosDirty = false;` from
    `cancelApplyRadios()`."""
    values = _radios_values(
        """
        const out = { noopWithoutChange: state.radiosConfirming };
        state.askApplyRadios();
        out.stillNoChange = state.radiosConfirming;

        state.radiosDraft.threadDevice = '/dev/serial/by-id/usb-B';
        state.askApplyRadios();
        out.confirmingAfterAsk = state.radiosConfirming;

        state.radiosDirty = true;
        state.cancelApplyRadios();
        out.confirmingAfterCancel = state.radiosConfirming;
        out.dirtyAfterCancel = state.radiosDirty;
        console.log(JSON.stringify(out));
        """
    )
    assert values == {
        "noopWithoutChange": False,
        "stillNoChange": False,
        "confirmingAfterAsk": True,
        "confirmingAfterCancel": False,
        "dirtyAfterCancel": False,
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_radios_reason_falls_back_to_the_unknown_text_for_an_unrecognized_error():
    """`radiosReason` was untested per the reviewer. Loads the REAL
    translation table (`_web_strings()`, the same reasoning
    `test_the_build_phase_is_tied_across_every_place_it_lives` gives for
    doing the same) rather than none at all: without real translations
    `t()` always returns its own key for EVERY key (see `t()`'s own
    comment), which would make `text === key` trivially true for any
    error string, real or fabricated, and prove nothing about the
    fallback branch actually firing. Fault to prove it: drop the
    `text === key ? t(...) : text` ternary in `radiosReason()` so it
    always returns the raw (here: missing) key instead."""
    values = _radios_values(
        """
        state.radios.job = { id: 'j', phase: 'failed', steps: [],
                              error: 'not-a-real-reason', rolled_back: false, healthy: false };
        console.log(JSON.stringify({ reason: state.radiosReason() }));
        """,
        translations=_web_strings(),
    )
    assert values == {"reason": "an unknown reason"}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_dead_sidecar_mid_job_is_reported_as_abandoned_and_stops_polling(api):
    """Review-Fix Critical #2a: `api/radios.py` returns the frozen `job`
    regardless of sidecar health - `sidecar_status()` (radios/sidecar.py)
    is what actually notices a crashed sidecar, flipping to
    `missing`/`outdated` on its own heartbeat. Before this fix
    `radiosJobRunning()` read a job in this state as running forever: the
    step list stayed frozen, the Apply button stayed hidden, both selects
    stayed disabled, and the 2s poll never stopped. Also confirms the
    stall banner in index.html is wired to the exact flag this sets
    (`_x_show_expr`, the technique this file already owns for this - see
    its own docstring), not merely present somewhere on the page.

    The flag is now raised only once the silence OUTLASTS
    `RADIOS_STALL_GRACE_MS` - see the regression test below for the half
    that matters more to a user - so this polls twice, with the window
    moved out from under the second poll.

    Fault to prove it: drop the `if (this.radiosStalled()) { ... }` block
    from `loadRadios()` entirely - `abandoned` then stays `false` and the
    timer is never stopped."""
    running_and_dead = json.dumps(
        {
            **RADIOS_READY,
            "sidecar": "missing",
            "job": {
                "id": "j",
                "phase": "apply_thread",
                "steps": ["validate", "backup", "write", "apply_thread"],
                "error": None,
                "rolled_back": False,
                "healthy": None,
            },
        }
    )
    values = _app_state(
        f"""
        let intervals = 0;
        globalThis.setInterval = () => {{ intervals += 1; return 999; }};
        globalThis.clearInterval = () => {{ intervals -= 1; }};
        state.radiosTimer = 999;
        state.request = async () => ({running_and_dead});
        (async () => {{
          await state.loadRadios();
          const firstPoll = {{
            running: state.radiosJobRunning(),
            stalled: state.radiosStalled(),
            abandoned: state.radiosJobAbandoned,
            timerCleared: state.radiosTimer === null,
          }};
          // The grace window elapsing, without waiting out 20 real
          // seconds: the only clock here is `Date.now()` measured against
          // `radiosStallSince`, so moving the start back is, to this code,
          // exactly the same event as time passing.
          state.radiosStallSince -= {_js_constant("RADIOS_STALL_GRACE_MS") + 1};
          await state.loadRadios();
          console.log(JSON.stringify({{
            firstPoll,
            running: state.radiosJobRunning(),
            abandoned: state.radiosJobAbandoned,
            timerCleared: state.radiosTimer === null,
            intervals,
          }}));
        }})();
        """
    )
    assert values == {
        "firstPoll": {
            "running": True,
            "stalled": True,
            "abandoned": False,
            "timerCleared": False,
        },
        "running": False,
        "abandoned": True,
        "timerCleared": True,
        "intervals": -1,
    }

    client, _, _ = api
    page = (await client.get("/")).text
    assert _x_show_expr(page, "web.radios.job_abandoned") == "radiosJobAbandoned"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_working_job_is_not_called_abandoned_over_a_momentary_silence():
    """The regression commit dd09241 introduced, and the reason the grace
    window above exists. The stall detector was faithful to its model but
    had nothing truthful to detect against: `sidecar_status()` checks
    `updater_present` first, off `state.json`, which ONLY `update-once.sh`
    writes - and entrypoint.sh runs the two workers one after the other,
    so for as long as a radios job applied that timestamp could not move.
    About 30 seconds (`_MAX_SILENT_SECONDS`) into the flagship
    one-to-two-minute stick switch the sidecar therefore read `missing`,
    the card called a perfectly healthy job abandoned, stopped polling,
    dropped the step list and put up a red banner - and the job then
    finished successfully with the card never saying so.

    The sidecar now keeps its heartbeat alive (see
    `test_the_heartbeat_advances_through_a_long_verify` in
    tests/test_updater_radios_script.py, the root-cause half). This is the
    independent second protection: one silent poll, then an answering one,
    must leave nothing flagged and the poll still running.

    Fault to prove it: set `radiosJobAbandoned` on the first stalled poll
    again, dropping the `RADIOS_STALL_GRACE_MS` comparison."""
    job = {
        "id": "j",
        "phase": "verify_thread",
        "steps": ["validate", "backup", "write", "apply_thread", "verify_thread"],
        "error": None,
        "rolled_back": False,
        "healthy": None,
    }
    silent = json.dumps({**RADIOS_READY, "sidecar": "missing", "job": job})
    answering = json.dumps({**RADIOS_READY, "sidecar": "ready", "job": job})
    values = _app_state(
        f"""
        globalThis.setInterval = () => 999;
        globalThis.clearInterval = () => {{}};
        let answer = {silent};
        state.request = async () => answer;
        (async () => {{
          await state.loadRadios();
          const afterSilent = state.radiosJobAbandoned;
          const stallStarted = state.radiosStallSince !== null;
          answer = {answering};
          await state.loadRadios();
          console.log(JSON.stringify({{
            afterSilent,
            stallStarted,
            afterRecovery: state.radiosJobAbandoned,
            stallSince: state.radiosStallSince,
            stillRunning: state.radiosJobRunning(),
            polling: state.radiosTimer !== null,
          }}));
        }})();
        """
    )
    assert values == {
        "afterSilent": False,
        "stallStarted": True,
        "afterRecovery": False,
        "stallSince": None,
        "stillRunning": True,
        "polling": True,
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_request_never_collected_is_reported_and_stops_polling(api):
    """Review-Fix Critical #2b: the radios counterpart of
    `updateNeverCollected()` - `confirmApplyRadios()` sets
    `radiosPendingJobId`/`radiosPendingDeadline` right after a successful
    POST, and `loadRadios()` is supposed to keep polling only until
    either `radios.job.id` matches or `RADIOS_APPLY_GRACE_MS` runs out.
    Simulates the deadline already having passed (`Date.now() - 1`)
    instead of waiting out the real 20s. `RADIOS_READY.job` is `null`, so
    the id-match branch never fires - only the deadline branch can
    explain the result below. Fault to prove it: drop the
    `this.radiosPendingDeadline !== null && Date.now() >= this.
    radiosPendingDeadline` check from `loadRadios()`."""
    values = _app_state(
        f"""
        let intervals = 0;
        globalThis.setInterval = () => {{ intervals += 1; return 1; }};
        globalThis.clearInterval = () => {{ intervals -= 1; }};
        state.radios = {json.dumps(RADIOS_READY)};
        state.radiosPendingJobId = 'new-job';
        state.radiosPendingDeadline = Date.now() - 1;
        state.radiosTimer = 999;
        state.request = async () => state.radios;
        (async () => {{
          await state.loadRadios();
          console.log(JSON.stringify({{
            missed: state.radiosNeverCollected(),
            pending: state.radiosPendingJobId,
            deadline: state.radiosPendingDeadline,
            timerCleared: state.radiosTimer === null,
            intervals,
          }}));
        }})();
        """
    )
    assert values == {
        "missed": True,
        "pending": None,
        "deadline": None,
        "timerCleared": True,
        "intervals": -1,
    }

    client, _, _ = api
    page = (await client.get("/")).text
    assert _x_show_expr(page, "web.radios.job_not_collected") == "radiosNeverCollected()"


async def test_leaving_settings_stops_the_radios_timer(api):
    """Review-Fix Important #3, first half - the same leak the sibling
    test in `test_the_build_phase_is_tied_across_every_place_it_lives`'s
    neighbourhood already found and fixed for the update timer
    (`select_view_body`/`this.stopUpdateTimer()` there) had no
    counterpart for the radios timer at all: navigating away from
    "Settings" left `loadRadios()` firing every 2s from a card nobody can
    see. `selectView` reaches into `window`/`history` (`writeHash`) that
    this suite cannot run headless in node - the same reason the sibling
    test above extracts and inspects the function's source instead of
    executing it; same technique here, narrowed to the specific `if`
    this fix adds rather than a bare "is the call present anywhere"
    check. Fault to prove it: drop the
    `if (view !== "settings") { this.stopRadiosTimer(); }` block from
    `selectView()`."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    select_view_start = script.index("async selectView(view) {")
    select_view_end = script.index("\n    },", select_view_start)
    select_view_body = script[select_view_start:select_view_end]
    guard_start = select_view_body.index('if (view !== "settings")')
    guard_end = select_view_body.index("}", guard_start)
    guard_block = select_view_body[guard_start : guard_end + 1]
    assert "this.stopRadiosTimer();" in guard_block


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_lost_session_stops_the_radios_timer():
    """Review-Fix Important #3, second half - the same rule
    `loadUpdateStatus()`'s own 401 branch already follows (see its own
    comment): a session that has ended elsewhere will never answer this
    poll either, so leaving the timer armed would fire it every 2s
    against a session that will never come back until the page is
    reloaded by hand. `radiosError` staying `null` (not the load-error
    text) is what proves the EARLY-RETURN branch ran, not just any error
    path. Fault to prove it: drop the `if (!this.authenticated) { this.
    stopRadiosTimer(); return; }` branch from `loadRadios()`'s `catch`."""
    values = _app_state(
        """
        let intervals = 0;
        globalThis.clearInterval = () => { intervals -= 1; };
        state.radiosTimer = 999;
        state.authenticated = false;
        state.request = async () => { throw new Error('session expired'); };
        (async () => {
          await state.loadRadios();
          console.log(JSON.stringify({
            timer: state.radiosTimer, intervals, error: state.radiosError,
          }));
        })();
        """
    )
    assert values == {"timer": None, "intervals": -1, "error": None}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_radios_options_report_unknown_instead_of_a_fabricated_default_with_no_current():
    """Review-Fix Critical/Minor #4: `current: null` (`api/radios.py`,
    sidecar missing or outdated) used to fall straight through to the
    constructor defaults ("" / adapter 0) because `loadRadios()` only
    ever sets the draft `if (current ...)` - rendering "No Thread stick
    (Thread off)" and adapter 0 marked "in use" as if they were a
    confirmed report, a fabrication where "unknown" is the truth. Fault
    to prove it: drop the `this.radios.current === null` guard from
    `radiosThreadOptions()` (equivalently `radiosBluetoothOptions()`)."""
    values = _radios_values(
        """
        state.radios.current = null;
        console.log(JSON.stringify({
          thread: state.radiosThreadOptions(),
          bluetooth: state.radiosBluetoothOptions(),
        }));
        """
    )
    assert values == {
        "thread": [
            {
                "value": "",
                "label": "web.radios.thread_unknown",
                "inUse": False,
                "missing": False,
                "zigbee": False,
            }
        ],
        "bluetooth": [
            {"value": "", "label": "web.radios.bluetooth_unknown", "inUse": False, "blocked": False}
        ],
    }


async def test_the_rfkill_warning_uses_the_banner_warn_class(api):
    """Review-Fix Minor #5: `.hint.warn` does not exist in style.css
    (only `.badge.warn`, `.banner.warn`, `.status-pill.warn` - checked
    directly against style.css, see its own grep in the review) - before
    this fix the one warning this card can show rendered as ordinary grey
    fine print. Extracts the real `class` attribute from the served
    markup (`_class_before_text`, the same technique `_x_show_expr` uses
    for `x-show`) instead of retyping it, so a future revert back to
    "hint warn" breaks this test rather than a hand-typed copy of it.
    Fault to prove it: revert the class in index.html to "hint warn"."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert _class_before_text(page, "rfkill_blocked") == "banner warn"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_blocked_bluetooth_adapters_are_marked_and_cannot_be_selected(api):
    """Review-Fix Minor #6: `radiosBluetoothOptions()` already computed
    `blocked` (`adapter.rfkill_blocked`) but nothing in index.html used
    it - a user could select an rfkill-blocked adapter, confirm, and wait
    out a 60s `verify_bluetooth` failure and a rollback for a state the
    card already knew was wrong; the server only checks existence, not
    rfkill. Covers both halves the reviewer named: the JS data itself
    (`radiosBluetoothOptions` - never called by any test before this
    file's Task 7 review) and the real `:disabled`/`x-text` expressions
    extracted from the served markup, evaluated in node against both a
    blocked and an open option - not merely that the word appears
    somewhere on the page. Fault to prove it: drop
    `:disabled="option.blocked"` and the blocked-label term from the
    Bluetooth `<option>` in index.html."""
    data = _radios_values(
        """
        state.radios.bluetooth.push({ index: 1, name: 'hci1', bus: 'usb', product: 'X', rfkill_blocked: true });
        console.log(JSON.stringify(state.radiosBluetoothOptions()));
        """
    )
    assert data[1] == {"value": 1, "label": "web.radios.bus_usb", "inUse": False, "blocked": True}

    client, _, _ = api
    page = (await client.get("/")).text
    disabled_expr, text_expr = _bluetooth_option_exprs(page)
    t_stub = "(key) => key"
    blocked_option = json.dumps({"value": 1, "label": "hci1", "inUse": False, "blocked": True})
    open_option = json.dumps({"value": 0, "label": "hci0", "inUse": False, "blocked": False})
    assert _eval_js_expr(disabled_expr, option=blocked_option, t=t_stub) is True
    assert _eval_js_expr(disabled_expr, option=open_option, t=t_stub) is False
    # The label is a helper on the state (`radiosBluetoothOptionLabel`), so
    # the `x-text` runs with the state as its scope, the way Alpine runs it.
    labels = _radios_values(
        _BINDINGS_JS + "console.log(JSON.stringify(["
        f"  run({json.dumps(text_expr)}, {{ option: {blocked_option} }}),"
        f"  run({json.dumps(text_expr)}, {{ option: {open_option} }}),"
        "]));"
    )
    assert "web.radios.option_blocked" in labels[0]
    assert "web.radios.option_blocked" not in labels[1]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_radios_step_label_falls_back_for_an_unknown_step():
    """Review-Fix Minor #7: `x-text="t('web.radios.step_' + step)"` built
    a key from SERVER data (`radios.job.steps`), and `t()` returns the
    key itself when it is missing (see its own comment) - a sidecar
    newer than this page would show the user the literal string
    `web.radios.step_reticulate_splines`. Loads the real translation
    table so a KNOWN step still resolves to its real sentence and only
    the unknown one falls back - proving the fallback is conditional, not
    that `radiosStepLabel` always returns the same thing. Fault to prove
    it: drop the `text === key ? t(...) : text` ternary so it always
    returns the raw key."""
    values = _radios_values(
        """
        console.log(JSON.stringify({
          known: state.radiosStepLabel('validate'),
          unknown: state.radiosStepLabel('reticulate_splines'),
        }));
        """,
        translations=_web_strings(),
    )
    assert values["known"] == "Check the setting"
    assert values["unknown"] == "Unknown step (reticulate_splines)"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_rescan_clears_dirty_and_the_standing_banners(api):
    """Review-Fix Minor #8's rescan half (clearing `radiosDirty`, without
    which `loadRadios()`'s own `if (current && !this.radiosDirty)` guard
    skips its draft resync for the rest of the session) plus the exit the
    sticky banners never had: `radiosJobAbandoned`/`radiosPendingMissed`
    used to be cleared ONLY in `confirmApplyRadios()`, so a user who had
    seen one kept seeing it through Rescan, through leaving Settings and
    coming back, until they reloaded the page by hand. `loadRadios()`
    clears them once the sidecar answers with nothing running; a job that
    really was abandoned never reaches a terminal phase, so for that one
    this button is the only way out.

    Checks the served markup calls the real method, then runs that method
    on the real component - the handler is no longer an inline statement
    list, so Alpine's `with(this)` evaluation has nothing left to prove.

    The GET deliberately answers with the sidecar STILL silent on a job
    still mid-phase: that is precisely the case `loadRadios()`'s own
    clearing branch cannot help with (a job that was really abandoned
    never reaches a terminal phase), so the button's own resets are the
    only thing that can clear anything here. A fresh look does start a
    fresh stall clock, which is why `freshStallClock` is expected - that
    is the new poll's judgment, not the old one's leftovers.

    Rescan also refreshes the Zigbee row's stick list - both rows read the
    same USB bus - so the request stub answers each endpoint on its own and
    counts them apart: `gets` is still exactly one radios load.

    Fault to prove it: drop the flag resets from `rescanRadios()`, leaving
    only `radiosDirty` (or point the button's `@click` back at
    `loadRadios()` alone). For the Zigbee half: drop
    `this.loadZigbeeRadio();` from `rescanRadios()`."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert _attr_before_t_key(page, "@click", "web.radios.rescan") == "rescanRadios()"

    stuck = json.dumps(
        {
            **RADIOS_READY,
            "sidecar": "missing",
            "job": {
                "id": "j",
                "phase": "verify_thread",
                "steps": ["validate", "backup", "write", "apply_thread", "verify_thread"],
                "error": None,
                "rolled_back": False,
                "healthy": None,
            },
        }
    )
    values = _app_state(
        f"""
        globalThis.setInterval = () => 999;
        globalThis.clearInterval = () => {{}};
        let gets = 0;
        let zigbeeGets = 0;
        state.radios = {stuck};
        state.radiosDirty = true;
        state.radiosJobAbandoned = true;
        state.radiosPendingMissed = true;
        state.radiosApplyError = 'a radio change is already running';
        state.request = async (method, path) => {{
          if (path === '/api/zigbee/radio') {{
            zigbeeGets += 1;
            return {{ serial: [], configured_path: null, configured_device_present: false,
              progress: {{ state: 'idle', attempts: 0, error: null }} }};
          }}
          gets += 1;
          return {stuck};
        }};
        state.rescanRadios();
        setTimeout(() => console.log(JSON.stringify({{
          dirty: state.radiosDirty,
          abandoned: state.radiosJobAbandoned,
          missed: state.radiosNeverCollected(),
          applyError: state.radiosApplyError,
          freshStallClock: state.radiosStallSince !== null,
          gets,
          zigbeeGets,
        }})), 0);
        """
    )
    assert values == {
        "dirty": False,
        "abandoned": False,
        "missed": False,
        "applyError": None,
        "freshStallClock": True,
        "gets": 1,
        "zigbeeGets": 1,
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_abandoned_banner_clears_once_the_sidecar_answers_again():
    """The other exit for `radiosJobAbandoned`, for the case that resolves
    itself: the sidecar comes back and the job has reached a terminal
    phase. Before this, `confirmApplyRadios()` was the only place that
    ever cleared it - a red "the updater service stopped answering" banner
    therefore stood over a job whose result was printed right above it,
    for the rest of the session.

    `radiosPendingMissed` is deliberately NOT part of this - see
    `test_the_never_collected_banner_survives_leaving_and_returning_to_settings`
    for why that flag has to survive this same condition instead.

    Fault to prove it: drop the `if (this.radios.sidecar === "ready" &&
    !this.radiosPhaseActive())` block from `loadRadios()`."""
    finished = json.dumps(
        {
            **RADIOS_READY,
            "job": {
                "id": "j",
                "phase": "done",
                "steps": ["validate", "backup", "write"],
                "error": None,
                "rolled_back": False,
                "healthy": True,
            },
        }
    )
    values = _app_state(
        f"""
        globalThis.setInterval = () => 999;
        globalThis.clearInterval = () => {{}};
        state.radiosJobAbandoned = true;
        state.radiosPendingMissed = true;
        state.radiosStallSince = 1;
        state.request = async () => ({finished});
        (async () => {{
          await state.loadRadios();
          console.log(JSON.stringify({{
            abandoned: state.radiosJobAbandoned,
            missed: state.radiosNeverCollected(),
            stallSince: state.radiosStallSince,
            result: state.radiosResultKey(),
          }}));
        }})();
        """
    )
    assert values == {
        "abandoned": False,
        "missed": True,
        "stallSince": None,
        "result": "web.radios.result_done",
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_never_collected_banner_survives_leaving_and_returning_to_settings():
    """Review-Fix Minor: `switchView("settings")` calls `loadRadios()`
    (see the `view === "settings"` branch in `selectView()`), so before
    this fix, simply leaving Settings and coming back satisfied
    `loadRadios()`'s own "sidecar ready, nothing running" clearing
    condition and wiped the "never collected" banner - even though the
    request the banner was warning about was still never picked up, and
    the resynced draft had silently gone back to showing the change as
    reverted. `rescanRadios()` (the explicit "look again" the user asked
    for) is meant to be the only way out.

    Raises `radiosPendingMissed` the real way - via an expired
    `radiosPendingDeadline`, the same mechanism
    `test_a_request_never_collected_is_reported_and_stops_polling` uses -
    rather than setting the flag directly, then calls `loadRadios()` a
    second time exactly the way `selectView("settings")` does when the
    user flips back to the tab. Only `rescanRadios()` should clear it.

    Fault to prove it: put `radiosPendingMissed = false;` back into the
    `if (this.radios.sidecar === "ready" && !this.radiosPhaseActive())`
    block in `loadRadios()`."""
    values = _app_state(
        f"""
        globalThis.setInterval = () => 999;
        globalThis.clearInterval = () => {{}};
        state.radios = {json.dumps(RADIOS_READY)};
        state.radiosPendingJobId = 'new-job';
        state.radiosPendingDeadline = Date.now() - 1;
        state.request = async () => state.radios;
        (async () => {{
          await state.loadRadios();
          const afterFirstPoll = state.radiosNeverCollected();
          // The view-switch call: sidecar answers "ready", no job running -
          // exactly the condition that used to wipe the banner.
          await state.loadRadios();
          const afterReturningToSettings = state.radiosNeverCollected();
          state.rescanRadios();
          console.log(JSON.stringify({{
            afterFirstPoll,
            afterReturningToSettings,
            afterRescan: state.radiosNeverCollected(),
          }}));
        }})();
        """
    )
    assert values == {
        "afterFirstPoll": True,
        "afterReturningToSettings": True,
        "afterRescan": False,
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_bluetooth_change_promises_nothing_about_thread_when_thread_is_off():
    """`web.radios.confirm_thread_untouched` reads "Thread is not touched.
    The border router keeps running with the stick it uses now, and Thread
    devices stay reachable." For an installation with Thread switched off
    - no stick, no border router, no Thread devices - all three clauses
    are false, and it was pushed for every Bluetooth-only change
    regardless. A confirmation dialog is the last place to tell someone
    something untrue about their own installation.

    The Thread-on case is covered by
    `test_the_radios_card_detects_a_change_and_picks_the_confirmation_text`
    above (`RADIOS_READY` has `thread_enabled: true`), which is why
    nothing caught this. Fault to prove it: drop the
    `current.thread_enabled` condition from `radiosConfirmKeys()`."""
    values = _radios_values(
        """
        state.radios.current.thread_enabled = false;
        state.radios.current.thread_device = null;
        state.radiosDraft = { threadDevice: '', bluetoothAdapter: 1 };
        console.log(JSON.stringify({
          changed: state.radiosChanged(),
          threadChanged: state.radiosThreadChanged(),
          keys: state.radiosConfirmKeys(),
        }));
        """
    )
    assert values == {
        "changed": True,
        "threadChanged": False,
        "keys": ["web.radios.confirm_bluetooth"],
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_apply_error_has_its_own_banner(api):
    """The field the poll cannot erase needs somewhere to render, or the
    fix above is invisible. Fault to prove it: point the second radios
    banner in index.html back at `radiosError`."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    assert 'x-show="radiosApplyError"' in page
    assert 'x-text="radiosApplyError"' in page


# ---------------------------------------------------------------------------
# Review round 2 (2026-09-12): the card must not print a console command that
# would kill a running update, the rollback must not render as silence, and an
# interrupted job must not claim the previous setting came back.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_no_refresh_command_is_offered_while_a_software_update_runs():
    """`sidecar_status()` derives `outdated` from "radios-state.json's
    seen_at is older than 30 s" - but entrypoint.sh runs the two workers
    sequentially in one loop, so while `update-once.sh` is inside `pull`,
    `build`, `recreate` or the up-to-120 s `health` wait, `radios-once.sh`
    cannot run at all and that timestamp necessarily goes stale (measured:
    `health` reads `outdated`, `pull` reads `missing`). For the whole
    duration of any update the card therefore told the user to run
    `docker compose up -d --no-deps loxmatter-updater` on the host - which
    would have sent SIGTERM into the container performing that very
    update.

    The genuine case is checked in the same test, so this cannot be
    "passed" by suppressing the command everywhere.

    Fault to prove it: drop the `if (this.radios?.update_running)` branch
    from `radiosSidecarMessage()`."""
    values = _radios_values(
        """
        const out = {};
        state.radios.update_running = true;
        for (const s of ['missing', 'outdated', 'unmounted']) {
          state.radios.sidecar = s;
          out[s] = state.radiosSidecarMessage();
        }
        state.radios.update_running = false;
        state.radios.sidecar = 'outdated';
        out.genuinelyOutdated = state.radiosSidecarMessage();
        console.log(JSON.stringify(out));
        """,
        translations=_web_strings(),
    )
    for status in ("missing", "outdated", "unmounted"):
        assert "software update" in values[status], status
        assert "docker compose" not in values[status], status
    assert "docker compose" in values["genuinelyOutdated"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_rollback_phase_says_what_is_happening_instead_of_going_blank():
    """`rollback` is not a member of `radios.job.steps` (it undoes the
    steps, it is not one of them), so `radiosStepClass()`'s
    `steps.indexOf(job.phase)` is -1 and NO step comes out done or now;
    and `rollback` is not terminal, so `radiosResultKey()` is null. The
    card therefore showed an entirely unmarked step list and no text at
    all for up to about two and a half minutes - silence over the most
    alarming moment in the flow.

    Fault to prove it: drop `radiosRollingBack()` from app.js."""
    values = _radios_values(
        """
        state.radios.job = { id: 'j', phase: 'rollback',
                             steps: ['validate','backup','write','apply_thread','verify_thread'],
                             error: 'verify_thread_failed', rolled_back: true, healthy: null };
        console.log(JSON.stringify({
          rolling: state.radiosRollingBack(),
          running: state.radiosJobRunning(),
          result: state.radiosResultKey(),
          marked: state.radios.job.steps.filter((s) => {
            const c = state.radiosStepClass(s);
            return c.done || c.now;
          }).length,
        }));
        """
    )
    # `marked: 0` is the blankness this message exists to cover, kept in
    # the assertion so the reason for the message stays visible.
    assert values == {"rolling": True, "running": True, "result": None, "marked": 0}


async def test_the_rollback_message_is_wired_to_the_rollback_phase(api):
    """The banner has to be bound to the flag, not merely present on the
    page - the technique this file already owns (`_x_show_expr`), for the
    same reason.

    Fault to prove it: bind the `<p>` to `radiosJobRunning()` instead."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    assert _x_show_expr(page, "web.radios.rolling_back") == "radiosRollingBack()"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_an_interrupted_job_is_not_reported_as_restored():
    """A pass killed mid-job is healed into `failed` with the error key
    `interrupted` by `load_previous_state()` in radios-once.sh. Both
    ordinary failure texts would be false for it: nothing was restored
    (the rollback is precisely what never ran) and nothing "did not come
    back up either" (no rollback was attempted).

    The ordinary failure is asserted in the same test, so this cannot be
    "passed" by giving every failure the interrupted text.

    Fault to prove it: drop the `job.error === "interrupted"` branch from
    `radiosResultKey()`."""
    values = _radios_values(
        """
        const job = { id: 'j', phase: 'failed', steps: ['validate','backup','write'],
                      error: 'interrupted', rolled_back: false, healthy: null };
        state.radios.job = job;
        const out = { interrupted: state.radiosResultKey(), reason: state.radiosReason() };
        job.error = 'verify_thread_failed';
        out.ordinary = state.radiosResultKey();
        job.healthy = false;
        out.unhealthy = state.radiosResultKey();
        console.log(JSON.stringify(out));
        """,
        translations=_web_strings(),
    )
    assert values == {
        "interrupted": "web.radios.result_interrupted",
        "reason": "the updater service was interrupted",
        "ordinary": "web.radios.result_failed_restored",
        "unhealthy": "web.radios.result_failed_unhealthy",
    }


# ---------------------------------------------------------------------------
# The colour picker: exactly one per subject.
#
# Runs the real `app.js` against the real `GET /api/devices/{id}/controls`
# payload, through the `x-for` expression pulled out of the SERVED markup -
# the same "extract the expression, don't retype it" rule `_x_show_expr`
# follows. All three halves have to hold for the assertion to pass: the
# route must stop offering the duplicate, `controlsByKind` must still return
# what the modal iterates, and the markup must still iterate that. A test
# that only fetched the page, or only searched it for a substring, could not
# have failed for the bug this covers - the markup was never wrong; it was
# handed two commands where one was meant (finding, review of e8040f4).
# ---------------------------------------------------------------------------


@pytest.fixture
async def colour_lamp(tmp_path, no_invoke, fake_runtime, fake_client):
    """The checked-in RGBW lamp, which accepts BOTH colour commands.

    Yields a factory so a single test can also build the lamp as a device
    that accepts only MoveToColor - the Zigbee shape, and the one that
    proves the rule keeps a picker rather than merely removing one."""
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)

        def register(*, drop: set[tuple[int, int]] = frozenset()) -> int:
            snapshot = load_snapshot("ikea_kajplats_cws_lamp.json")
            device_id = store.register_device(snapshot)
            store.register_signals(device_id, snapshot)
            store.register_commands(
                device_id,
                [c for c in extract_commands(snapshot) if (c.cluster_id, c.command_id) not in drop],
            )
            return device_id

        yield client, register
    store.close()


def _colour_picker_x_for(markup: str) -> str:
    """The literal `x-for` expression of the block that draws the colour
    area, read out of the served page. Identified by the colour field's own
    class rather than by a line number, and taken as the nearest `x-for`
    above it - if that block is ever rebound to something other than the
    subject's `hue_sat` commands, this extraction follows the change and the
    assertion below is made against what the browser would really loop
    over."""
    field = markup.index('class="colour-field"')
    opened = list(re.finditer(r'x-for="([^"]*)"', markup[:field]))
    assert opened, "no x-for precedes the colour field in the served markup"
    return opened[-1].group(1)


def _pickers_for(x_for: str, controls: dict, device_id: int) -> list[str]:
    """The slugs the colour block would draw, evaluated in node.

    `with (state)` is how Alpine resolves an expression against its data
    object, including the `this` binding the helper methods need - so this
    runs the markup's own expression rather than a Python reading of it."""
    iterable = x_for.split(" in ", 1)[1]
    values = _app_state(
        f"""
        state.controlsBySubject = {{ {device_id}: {json.dumps(controls)} }};
        state.controlModalDevice = {device_id};
        const draw = new Function("state", "with (state) {{ return (" + {json.dumps(iterable)} + "); }}");
        console.log(JSON.stringify(draw(state).map((command) => command.slug)));
        """
    )
    assert isinstance(values, list)
    return values


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_colour_lamp_gets_exactly_one_colour_picker(colour_lamp):
    """The lamp accepts MoveToHueAndSaturation (768/6) AND MoveToColor
    (768/7), and both carry `control: hue_sat`. Before 12 September 2026
    the modal therefore drew two colour areas: no slug label distinguishes
    them, and both read and wrote the same `controlDrafts.hue` /
    `.saturation`, so dragging one moved the other's marker.

    The surviving one is command 6, and the slug says which: it writes
    CurrentHue and CurrentSaturation, the two attributes the picker reads
    its own position back from, and sets ColorMode to 0, which is what
    decides whether the modal opens on the colour tab at all."""
    client, register = colour_lamp
    device_id = register()
    page = _without_comments((await client.get("/")).text)
    controls = (await client.get(f"/api/devices/{device_id}/controls")).json()

    assert [c["slug"] for c in controls["commands"] if c["control"] == "hue_sat"] == ["color"]
    assert _pickers_for(_colour_picker_x_for(page), controls, device_id) == ["color"]
    # The suppressed twin is not "hidden": that number means present but
    # UNNAMED, and this lamp has no unnamed command at all.
    assert controls["hidden_raw_commands"] == 0
    # Nothing else the modal builds is touched - the white tab still has
    # its Kelvin slider, and the tab bar still appears.
    assert [c["slug"] for c in controls["commands"] if c["control"] == "kelvin"] == ["colortemp"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_lamp_that_accepts_only_move_to_color_keeps_its_picker(colour_lamp):
    """The Zigbee shape (design 2026-09-12, section 5.6): ZHA 2.2.2 sends
    colour only as XY, and a lamp that never accepts MoveToHueAndSaturation
    has no preferred twin to stand aside for.

    This is the half that makes the rule a preference and not a ban. A fix
    that simply dropped `color_xy` would pass the test above and leave
    exactly the lamp that command 7 was named for with no colour control at
    all."""
    client, register = colour_lamp
    device_id = register(drop={(768, 6)})
    page = _without_comments((await client.get("/")).text)
    controls = (await client.get(f"/api/devices/{device_id}/controls")).json()

    assert [c["slug"] for c in controls["commands"] if c["control"] == "hue_sat"] == ["color_xy"]
    assert _pickers_for(_colour_picker_x_for(page), controls, device_id) == ["color_xy"]


# ---------------------------------------------------------------------------
# The Zigbee row on the radios card, and the Zigbee transport badge (design
# 2026-09-12, sections 3.2 and 3.3). The row reads `GET /api/zigbee/radio`
# and applies through `PUT /api/zigbee/radio`, never through the sidecar.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_zigbee_badge_has_a_glyph_now_that_the_spec_adds_one(api):
    """`transport_for` has returned "zigbee" since the boundary design, but
    `transportBadge` deliberately had no symbol for it - the comment in
    app.js says "Zigbee has no glyph before the Zigbee spec adds one". This
    is that spec.

    Runs the REAL `transportBadge` in node against a device object shaped
    the way `GET /api/devices` returns one; a markup-substring assertion
    could not fail for a binding that is merely wrong.

    Fault to prove it: remove the `zigbee` entry from the symbols map. The
    badge then returns null and a Zigbee device tile shows no transport at
    all, while Thread and IP ones do."""
    values = _app_state(
        setup="console.log(JSON.stringify({"
        "  zigbee: state.transportBadge({ transport: 'zigbee' }),"
        "  thread: state.transportBadge({ transport: 'thread' }),"
        "  unknown: state.transportBadge({ transport: null }),"
        "}));",
        translations={"web.devices.transport_zigbee": "Zigbee"},
    )
    assert values["zigbee"]["symbol"] == "i-transport-zigbee"
    assert values["zigbee"]["label"] == "Zigbee"
    assert values["thread"]["symbol"] == "i-transport-thread"
    assert values["unknown"] is None


async def test_the_sprite_carries_the_zigbee_symbol(api):
    """The glyph the badge names must exist, or the tile renders an empty
    box. Drawn in the sprite's own style - 24 viewBox, stroke 1.8,
    currentColor - and deliberately NOT the official logo: "Zigbee" is a
    trademark of the Connectivity Standards Alliance.

    Fault to prove it: rename the symbol id."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert 'id="i-transport-zigbee"' in page


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_thread_stick_is_offered_with_a_reason_not_silently_dropped(api):
    """A stick that is simply missing from a list is a bug report waiting to
    happen: the user sees their stick in the Thread row and not in the
    Zigbee one and concludes the detection is broken. It is listed,
    disabled, with the reason.

    Fault to prove it: filter the Thread device out of the list."""
    values = _app_state(
        setup="state.zigbee = { serial: ["
        "  { path: '/dev/serial/by-id/a', product: 'SONOFF', fingerprint: null,"
        "      is_thread: true, selectable: false },"
        "  { path: '/dev/serial/by-id/b', product: 'ZBT-1', fingerprint:"
        "      { name: 'ZBT-1', radio_type: 'ezsp' }, is_thread: false, selectable: true },"
        "], current: null };"
        "console.log(JSON.stringify(state.zigbeeRadioOptions()));",
        translations={"web.radios.zigbee_is_thread_stick": "used by Thread"},
    )
    thread_option = next(o for o in values if o["value"] == "/dev/serial/by-id/a")
    assert thread_option["disabled"] is True
    assert "Thread" in thread_option["label"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_card_refuses_the_maintainers_own_thread_stick(api):
    """The measured installation, reaching the screen (12 September 2026).

    His two sticks are indistinguishable by USB ids and by major number, so
    this is the shape the card must get right on the one machine that will
    actually run it: the ITEAD stick selectable, the MG24 - which his Thread
    border router is running on - listed, disabled, and labelled with the
    reason.

    `disabled` reads the server's `selectable`, NOT a rule the page invents,
    so the two can never disagree about which sticks are safe. That is the
    same reasoning behind `option.blocked` on the Bluetooth row.

    Fault to prove it: have `zigbeeRadioOptions()` compute `disabled` from
    the product name (say, anything containing "MG24") instead of reading
    `selectable`. The MG24 stays disabled for the wrong reason and the test
    still passes - so ALSO flip `selectable` to true on the MG24 entry and
    confirm the option becomes enabled. If it does not, the page is
    deciding for itself and the server check is decorative."""
    values = _app_state(
        setup="state.zigbee = { serial: ["
        "  { path: '/dev/serial/by-id/usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a-if00-port0',"
        "      product: 'SONOFF Dongle Plus MG24',"
        "      fingerprint: { name: 'SONOFF Zigbee Dongle Plus MG24', radio_type: 'ezsp' },"
        "      is_thread: true, selectable: false },"
        "  { path: '/dev/serial/by-id/usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf-if00-port0',"
        "      product: 'SONOFF ZBDongle-E V2',"
        "      fingerprint: { name: 'SONOFF ZBDongle-E V2', radio_type: 'ezsp' },"
        "      is_thread: false, selectable: true },"
        "], current: null };"
        "console.log(JSON.stringify(state.zigbeeRadioOptions()));",
        translations={"web.radios.zigbee_is_thread_stick": "in use for Thread"},
    )
    mg24 = next(o for o in values if "MG24" in o["value"])
    itead = next(o for o in values if "Itead" in o["value"])
    assert mg24["disabled"] is True
    assert "Thread" in mg24["label"]
    assert itead["disabled"] is False


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_an_unrecognised_stick_is_selectable_and_says_so(api):
    """Refusing to work with an unlisted stick would be worse than letting
    the user say what it is. It is offered, marked as unrecognised, and the
    Advanced disclosure carries radio type and baud rate.

    A stick this far along has already cleared `_is_thread_stick` (the two
    tests above prove that check), so `is_thread: false, selectable: true,
    fingerprint: null` is a real, reachable shape from
    `GET /api/zigbee/radio` - not a stand-in for one - the moment a stick
    reaches loxmatter that `fingerprints.py`'s table has no row for at all.

    Fault to prove it: disable options with no fingerprint. That fault
    cannot be caught by the two tests above: their fingerprint-null stick
    is ALSO the Thread stick, already disabled for its own reason, so
    "disabled because no fingerprint" and "disabled because it is Thread"
    agree by coincidence there. Only a fingerprint-null stick that is
    NOT the Thread stick tells the two reasons apart."""
    values = _app_state(
        setup="state.zigbee = { serial: ["
        "  { path: '/dev/serial/by-id/usb-Some_Other_CP210x_Bridge-if00',"
        "      product: 'Some Other CP210x Bridge', fingerprint: null,"
        "      is_thread: false, selectable: true },"
        "], current: null };"
        "console.log(JSON.stringify(state.zigbeeRadioOptions()));",
    )
    mystery = next(
        o for o in values if o["value"] == "/dev/serial/by-id/usb-Some_Other_CP210x_Bridge-if00"
    )
    # Offered: `disabled` reads `selectable`, exactly as the two tests above
    # establish for a recognised stick - a missing fingerprint is not a
    # second reason to refuse it.
    assert mystery["disabled"] is False
    # Marked as unrecognised: a plain flag the row template turns into text,
    # the same way `radiosThreadOptions()`'s `missing` flag above is not
    # itself a sentence.
    assert mystery["unrecognised"] is True
    # The Advanced disclosure carries radio type and baud rate: it has
    # something to prefill even for a stick the table has never heard of -
    # `fingerprints.DEFAULT_UNKNOWN`'s own values (`radio_type="ezsp"`,
    # `baudrate=115200`), not a blank form the user has to fill in from
    # nothing.
    assert mystery["fingerprint"]["radio_type"] == "ezsp"
    assert mystery["fingerprint"]["baudrate"] == 115200


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_row_says_what_a_running_connection_attempt_is_doing(api):
    """Task 11's `PUT` answers 202 and connects in the background, so the
    card is the only thing that can tell the user a 9-15 s quirks warm-up is
    a healthy operation rather than a dead one - which is this plan's own
    Global Constraint about long-running work, and the 2a-1 lesson behind
    it.

    `ConnectionState` has six members, not five: `"applying"` (commit
    `4caa260`, after this task was originally written) covers the window a
    radio swap spends with no `ZigbeeSource` at all - the old one released,
    the new one not yet built. Without it the card read `idle`, the "nothing
    is configured" state, for that whole window and never started polling -
    the same failure this test already exists to catch, just for a
    different gap. The polling rule is therefore three states, not two.

    Runs the REAL `zigbeeProgressText` in node, because a markup assertion
    cannot fail for a binding that is merely wrong.

    Fault to prove it: render a bare connected/not-connected boolean. A
    warm-up in progress then reads exactly like a broken stick. Also fails
    if `zigbeeRadioPolling` polls only for `loading_quirks`/`opening_radio`
    and drops `applying` as an apparent duplicate - the assertion below on
    `values["applying_polling"]` exists specifically to catch that."""
    values = _app_state(
        setup="console.log(JSON.stringify({"
        "  applying: state.zigbeeProgressText({ state: 'applying', attempts: 0 }),"
        "  quirks: state.zigbeeProgressText({ state: 'loading_quirks', attempts: 0 }),"
        "  opening: state.zigbeeProgressText({ state: 'opening_radio', attempts: 0 }),"
        "  failed: state.zigbeeProgressText({ state: 'failed', attempts: 4, error: 'nope' }),"
        "  applying_polling: state.zigbeeRadioPolling({ state: 'applying' }),"
        "  polling: state.zigbeeRadioPolling({ state: 'loading_quirks' }),"
        "  settled: state.zigbeeRadioPolling({ state: 'connected' }),"
        "}));",
        translations={
            "web.radios.zigbee_applying": "Applying the change - releasing the previous radio",
            "web.radios.zigbee_loading_quirks": "Preparing device support...",
            "web.radios.zigbee_opening_radio": "Opening the stick...",
            "web.radios.zigbee_failed_retrying": "Failed ({attempts}): {error}",
        },
    )
    assert values["applying"] == "Applying the change - releasing the previous radio"
    assert values["quirks"] == "Preparing device support..."
    assert values["opening"] == "Opening the stick..."
    assert "4" in values["failed"] and "nope" in values["failed"]
    # The card polls while an attempt is running - three states
    # (`applying`, `loading_quirks`, `opening_radio`), not two - and stops
    # once it settles (`connected` or `failed`), exactly as `loadRadios()`
    # does for a sidecar job. `applying` is not redundant with the other
    # two: it is the only one of the three a `ZigbeeSource` never reports
    # itself, because during it there is no `ZigbeeSource` to ask.
    assert values["applying_polling"] is True
    assert values["polling"] is True
    assert values["settled"] is False


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_configured_stick_that_is_gone_says_so(api):
    """The in-process design's worst failure mode reaching the screen: the
    supervisor retries a stick that will never answer, forever, and without
    this the card says only "not connected". `GET /api/zigbee/radio` returns
    `configured_device_present` for exactly this, the way the Thread row
    already uses `thread_device_present`.

    Fault to prove it: ignore the flag and render the stored path alone. An
    unplugged stick is then indistinguishable from one that is present and
    refusing to open - and the two need opposite actions from the user."""
    values = _app_state(
        setup="state.zigbee = { serial: [], current: null,"
        "  configured_path: '/dev/serial/by-id/gone', configured_device_present: false,"
        "  progress: { state: 'failed', attempts: 9, error: 'no such device' } };"
        "console.log(JSON.stringify({ text: state.zigbeeProgressText(state.zigbee.progress,"
        "  state.zigbee.configured_device_present) }));",
        translations={"web.radios.zigbee_device_missing": "The chosen stick is not plugged in."},
    )
    assert values["text"] == "The chosen stick is not plugged in."


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_applying_the_zigbee_row_never_sends_a_radios_request(api):
    """Spec Correction 3, enforced in the page itself: the Zigbee row has
    its own Apply and its own endpoint. If it ever shared the sidecar's
    request body, changing the Zigbee stick would recreate the bridge
    container - the one the page is talking to.

    Fault to prove it: build the Zigbee half into `radiosRequestBody()`.

    Sliced between the two METHOD DEFINITIONS, not between the first
    mentions of the two names: `radiosConfirmKeys()` is named in a comment
    inside `loadRadios()`, above `radiosRequestBody()`, so slicing from the
    first occurrence of each gave an empty string - in which "zigbee" can
    never be found, whatever the method contains. The two assertions on
    the slice itself keep it from going empty again."""
    source = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    start = source.index("\n    radiosRequestBody() {")
    body = source[start : source.index("\n    radiosConfirmKeys() {", start)]
    assert "thread:" in body and "bluetooth:" in body
    assert "zigbee" not in body.lower()


def _zigbee_state(overrides: str = "") -> str:
    """A `GET /api/zigbee/radio` body shaped the way the route returns one
    on the maintainer's Pi while Thread runs: the MG24 refused, the ITEAD
    stick selectable, and one stick the fingerprint table does not know.
    `overrides` is JS run right after, to reshape it per test."""
    return (
        "state.zigbee = {"
        "  serial: ["
        "    { path: '/dev/serial/by-id/usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a-if00-port0',"
        "      product: 'SONOFF Dongle Plus MG24',"
        "      fingerprint: { name: 'SONOFF Zigbee Dongle Plus MG24', radio_type: 'ezsp',"
        "        baudrate: 115200, flow_control: 'software' },"
        "      is_thread: true, selectable: false },"
        "    { path: '/dev/serial/by-id/usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf-if00-port0',"
        "      product: 'SONOFF ZBDongle-E V2',"
        "      fingerprint: { name: 'SONOFF ZBDongle-E V2', radio_type: 'ezsp',"
        "        baudrate: 115200, flow_control: 'software' },"
        "      is_thread: false, selectable: true },"
        "    { path: '/dev/serial/by-id/usb-Some_Other_CP210x_Bridge-if00',"
        "      product: 'Some Other CP210x Bridge', fingerprint: null,"
        "      is_thread: false, selectable: true },"
        "  ],"
        "  configured_path: null, configured_device_present: false,"
        "  progress: { state: 'idle', attempts: 0, error: null, changed_at: 'x' },"
        "};"
        "state.zigbeeDraft.path = '';" + overrides
    )


MG24 = "/dev/serial/by-id/usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a-if00-port0"
ITEAD = "/dev/serial/by-id/usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf-if00-port0"
UNKNOWN_STICK = "/dev/serial/by-id/usb-Some_Other_CP210x_Bridge-if00"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_mg24_becomes_selectable_once_thread_is_off():
    """The escape hatch the Thread lock-out deliberately leaves open: a
    dual-capable stick moves from Thread to Zigbee by turning Thread off
    first. `GET /api/zigbee/radio` then reports the MG24 with
    `is_thread: false, selectable: true`, and the card must offer it -
    without the "in use for Thread" suffix, which would now be untrue.

    Fault to prove it: keep the suffix whenever the product name contains
    "MG24" (a page that remembers which stick "is" the Thread stick instead
    of reading the answer). The label then still claims Thread."""
    values = _app_state(
        _zigbee_state(
            f"state.zigbee.serial[0].is_thread = false;"
            f"state.zigbee.serial[0].selectable = true;"
            f"console.log(JSON.stringify(state.zigbeeRadioOptions()"
            f"  .find((o) => o.value === {json.dumps(MG24)})));"
        ),
        translations={"web.radios.zigbee_is_thread_stick": "in use for Thread"},
    )
    assert values["disabled"] is False
    assert "Thread" not in values["label"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_unknown_stick_defaults_are_the_servers_own():
    """`ZIGBEE_UNKNOWN_FINGERPRINT` and `ZIGBEE_RADIO_TYPES` are copies of
    `fingerprints.DEFAULT_UNKNOWN` and `fingerprints.RadioType`, because
    the route reports an unknown stick's fingerprint as `null`. A copy is
    only safe with something holding it to the original: a page that
    prefilled 57600 while the server defaults to 115200 would store a baud
    rate the user never chose, and a radio type the schema does not know
    is a 422.

    Fault to prove it: change the baud rate in `ZIGBEE_UNKNOWN_FINGERPRINT`,
    or drop `deconz` from `ZIGBEE_RADIO_TYPES`."""
    from dataclasses import asdict
    from typing import get_args

    from loxmatter.radios.fingerprints import DEFAULT_UNKNOWN, RadioType

    values = _app_state(
        _zigbee_state(
            "console.log(JSON.stringify({"
            f"  unknown: state.zigbeeRadioOptions().find((o) => o.value === {json.dumps(UNKNOWN_STICK)}),"
            "  types: state.zigbeeRadioTypeOptions().map((o) => o.value),"
            "}));"
        )
    )
    assert values["unknown"]["fingerprint"] == asdict(DEFAULT_UNKNOWN)
    assert values["types"] == list(get_args(RadioType))


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_advanced_shows_what_the_configured_unknown_stick_is_opened_with():
    """Measured in the browser harness: after applying an unrecognised stick
    as ZNP at 38400 and reloading, Advanced read EZSP at 115200 - the
    defaults - beside a progress line reporting that very stick failing.
    The user opening Advanced to check the parameters would have read the
    wrong ones and "corrected" nothing.

    For the CONFIGURED stick the draft comes from `configured_radio_type` /
    `configured_baudrate`; for any other unrecognised stick from the
    defaults, because nothing is stored for it.

    Fault to prove it: always prefill from `option.fingerprint` in
    `resetZigbeeAdvanced()`."""
    values = _app_state(
        _zigbee_state(
            "globalThis.setTimeout = () => 1; globalThis.clearTimeout = () => {};"
            "const body = JSON.parse(JSON.stringify(state.zigbee));"
            f"body.configured_path = {json.dumps(UNKNOWN_STICK)};"
            "body.configured_device_present = true;"
            "body.configured_radio_type = 'znp'; body.configured_baudrate = 38400;"
            "body.serial.push({ path: '/dev/serial/by-id/usb-Another_Unknown-if00',"
            "  product: 'Another', fingerprint: null, is_thread: false, selectable: true });"
            "state.request = async () => body;"
            "(async () => {"
            "  await state.loadZigbeeRadio();"
            "  const configured = { ...state.zigbeeDraft };"
            "  state.zigbeeDraft.path = '/dev/serial/by-id/usb-Another_Unknown-if00';"
            "  state.zigbeeSelectionChanged();"
            "  console.log(JSON.stringify({ configured, other: { ...state.zigbeeDraft } }));"
            "})();"
        )
    )
    assert values["configured"] == {"path": UNKNOWN_STICK, "radioType": "znp", "baudrate": 38400}
    assert values["other"]["radioType"] == "ezsp"
    assert values["other"]["baudrate"] == 115200


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_zigbee_request_body_carries_advanced_values_only_for_an_unrecognised_stick():
    """`PUT /api/zigbee/radio` reads the three Advanced fields only for a
    stick the table does not know. Sending them for a recognised stick is
    harmless to the server but tells a lie about what the page is setting;
    NOT sending them for an unknown one silently drops what the user typed.

    Fault to prove it: send `radio_type`/`baudrate` for every stick, or for
    none."""
    values = _app_state(
        _zigbee_state(
            "const out = {};"
            f"state.zigbeeDraft.path = {json.dumps(ITEAD)};"
            "out.recognised = state.zigbeeRequestBody();"
            f"state.zigbeeDraft.path = {json.dumps(UNKNOWN_STICK)};"
            "state.zigbeeDraft.radioType = 'znp'; state.zigbeeDraft.baudrate = '57600';"
            "out.unknown = state.zigbeeRequestBody();"
            "state.zigbeeDraft.path = '';"
            "out.none = state.zigbeeRequestBody();"
            "console.log(JSON.stringify(out));"
        )
    )
    assert values["recognised"] == {"path": ITEAD}
    assert values["unknown"] == {"path": UNKNOWN_STICK, "radio_type": "znp", "baudrate": 57600}
    assert values["none"] == {"path": None}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_thread_stick_cannot_be_applied_even_as_a_changed_draft():
    """`:disabled` on the `<option>` stops a click; it does not stop a draft
    that already holds the path - a stale value, or a stick that became the
    Thread stick after it was picked. Apply must refuse it too, rather than
    send a `PUT` the server is certain to answer with a 400.

    Fault to prove it: drop the `selected?.disabled` check from
    `zigbeeCanApply()`."""
    values = _app_state(
        _zigbee_state(
            "const out = {};"
            f"state.zigbeeDraft.path = {json.dumps(MG24)};"
            "out.mg24 = [state.zigbeeRadioChanged(), state.zigbeeCanApply()];"
            f"state.zigbeeDraft.path = {json.dumps(ITEAD)};"
            "out.itead = [state.zigbeeRadioChanged(), state.zigbeeCanApply()];"
            "state.zigbee.progress = { state: 'loading_quirks', attempts: 0 };"
            "out.itead_while_first_attempt = state.zigbeeCanApply();"
            "state.zigbee.progress = { state: 'failed', attempts: 2, error: 'x' };"
            "out.itead_while_failed = state.zigbeeCanApply();"
            "console.log(JSON.stringify(out));"
        )
    )
    assert values["mg24"] == [True, False]
    assert values["itead"] == [True, True]
    # A first attempt blocks a second change; a failed one does not -
    # picking another stick is exactly what a failing one calls for.
    # (`test_a_failing_stick_can_be_corrected_while_the_bridge_retries_it`
    # covers the retries in between.)
    assert values["itead_while_first_attempt"] is False
    assert values["itead_while_failed"] is True


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_zigbee_row_polls_by_state_at_three_cadences():
    """All six `ConnectionState` members, through the REAL
    `loadZigbeeRadio()` and the timer it arms - not just the predicate.

    A failed attempt is settled for `zigbeeRadioPolling()` but NOT for the
    supervisor, which retries forever; a card that stopped asking there
    would never show a replugged stick coming back. So `failed` keeps a
    slow timer and the three working states a fast one.

    `connected` keeps a slower one still. It used to keep none, so a stick
    pulled out at rest - or a link zigpy reported lost - went on reading
    "Connected" until Rescan or a reload. Only `idle`, nothing configured,
    has nothing that could change by itself.

    Fault to prove it: return `null` for `failed` or for `connected` in
    `zigbeeRadioPollInterval()` (that row goes to `null`), or drop
    `applying` from `ZIGBEE_WORKING_STATES` (the `applying` row does)."""
    values = _app_state(
        _zigbee_state(
            "const delays = {};"
            "let armed = null;"
            "globalThis.setTimeout = (fn, delay) => { armed = delay; return 1; };"
            "globalThis.clearTimeout = () => { armed = null; };"
            "state.view = 'settings';"
            "const body = JSON.parse(JSON.stringify(state.zigbee));"
            "(async () => {"
            "  for (const s of ['idle', 'applying', 'loading_quirks', 'opening_radio',"
            "                   'connected', 'failed']) {"
            "    armed = null; state.zigbeeTimer = null;"
            "    state.request = async () => ({ ...body, progress: { state: s, attempts: 1,"
            "      error: 'e', changed_at: 'x' } });"
            "    await state.loadZigbeeRadio();"
            "    delays[s] = armed;"
            "  }"
            "  console.log(JSON.stringify(delays));"
            "})();"
        )
    )
    working = _js_constant("ZIGBEE_WORKING_POLL_MS")
    failed = _js_constant("ZIGBEE_FAILED_POLL_MS")
    connected = _js_constant("ZIGBEE_CONNECTED_POLL_MS")
    assert working < failed < connected
    assert values == {
        "idle": None,
        "applying": working,
        "loading_quirks": working,
        "opening_radio": working,
        "connected": connected,
        "failed": failed,
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_failed_zigbee_apply_survives_the_poll_it_arms():
    """The radios card's first Critical, in the shape this row could repeat
    it: the Apply fails, the reload after it sees an attempt still running
    (started from another tab) and arms the fast poll - and a poll that
    cleared the error field would erase the explanation a second after the
    user saw it. The error lives in `zigbeeApplyError`, which no load
    touches; this runs the reload AND the poll it armed.

    Fault to prove it: write the error into `zigbeeError` in
    `applyZigbeeRadio()`'s `catch` (the load clears it), or have
    `loadZigbeeRadio()` reset `zigbeeApplyError`."""
    values = _app_state(
        _zigbee_state(
            "let pending = null;"
            "globalThis.setTimeout = (fn) => { pending = fn; return 1; };"
            "globalThis.clearTimeout = () => { pending = null; };"
            "state.view = 'settings';"
            f"state.zigbeeDraft.path = {json.dumps(ITEAD)}; state.zigbeeDirty = true;"
            "const body = JSON.parse(JSON.stringify(state.zigbee));"
            "body.progress = { state: 'opening_radio', attempts: 0, error: null, changed_at: 'x' };"
            "state.request = async (method) => {"
            "  if (method === 'PUT') throw new Error('This stick is in use for Thread.');"
            "  return body;"
            "};"
            "(async () => {"
            "  await state.applyZigbeeRadio();"
            "  const afterApply = state.zigbeeApplyError;"
            "  const polled = pending !== null;"
            "  const tick = pending; pending = null; await tick();"
            "  console.log(JSON.stringify({ afterApply, polled, afterPoll: state.zigbeeApplyError,"
            "    loadError: state.zigbeeError, busy: state.zigbeeBusy }));"
            "})();"
        )
    )
    assert values == {
        "afterApply": "This stick is in use for Thread.",
        "polled": True,
        "afterPoll": "This stick is in use for Thread.",
        "loadError": None,
        "busy": False,
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_poll_does_not_overwrite_an_unapplied_zigbee_choice():
    """The row polls every second during an attempt. A poll that resynced
    the draft from `configured_path` every time would snap the select back
    under the user's hand while they are choosing.

    Fault to prove it: drop the `if (!this.zigbeeDirty)` guard in
    `loadZigbeeRadio()`."""
    values = _app_state(
        _zigbee_state(
            "globalThis.setTimeout = () => 1; globalThis.clearTimeout = () => {};"
            "const body = JSON.parse(JSON.stringify(state.zigbee));"
            f"body.configured_path = {json.dumps(ITEAD)};"
            "state.request = async () => body;"
            "(async () => {"
            "  await state.loadZigbeeRadio();"
            "  const clean = state.zigbeeDraft.path;"
            "  state.zigbeeDraft.path = '';"
            "  state.zigbeeSelectionChanged();"
            "  await state.loadZigbeeRadio();"
            "  console.log(JSON.stringify({ clean, dirty: state.zigbeeDraft.path }));"
            "})();"
        )
    )
    assert values == {"clean": ITEAD, "dirty": ""}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_an_older_zigbee_answer_cannot_overwrite_a_newer_one():
    """A poll in flight when Apply is pressed can answer AFTER the reload
    that follows the `PUT`. Without a guard its older `failed` (or `idle`)
    lands last and the card shows a state from before the change - the one
    reading that tells the user nothing happened.

    Fault to prove it: remove the `sequence !== this.zigbeeLoadSequence`
    check after the `await` in `loadZigbeeRadio()`."""
    values = _app_state(
        _zigbee_state(
            "globalThis.setTimeout = () => 1; globalThis.clearTimeout = () => {};"
            "const base = JSON.parse(JSON.stringify(state.zigbee));"
            "let releaseOld;"
            "const old = new Promise((resolve) => { releaseOld = resolve; });"
            "let calls = 0;"
            "state.request = async () => {"
            "  calls += 1;"
            "  if (calls === 1) { await old;"
            "    return { ...base, progress: { state: 'failed', attempts: 3, error: 'old' } }; }"
            "  return { ...base, progress: { state: 'applying', attempts: 0, error: null } };"
            "};"
            "(async () => {"
            "  const first = state.loadZigbeeRadio();"
            "  await state.loadZigbeeRadio();"
            "  releaseOld(); await first;"
            "  console.log(JSON.stringify({ state: state.zigbee.progress.state }));"
            "})();"
        )
    )
    assert values == {"state": "applying"}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_retry_does_not_claim_to_be_the_first_connection():
    """The supervisor walks `loading_quirks` -> `opening_radio` again on
    every retry, with `attempts` still counting the failures before it. The
    first-attempt wording ("Preparing device support - this can take a few
    seconds") says nothing about the failures before it.

    Fault to prove it: drop the `progress.attempts > 0` branch in
    `zigbeeProgressText()`."""
    values = _app_state(
        "console.log(JSON.stringify({"
        "  first: state.zigbeeProgressText({ state: 'loading_quirks', attempts: 0 }),"
        "  retry: state.zigbeeProgressText({ state: 'loading_quirks', attempts: 3 }),"
        "  retry_open: state.zigbeeProgressText({ state: 'opening_radio', attempts: 3 }),"
        "  idle: state.zigbeeProgressText({ state: 'idle', attempts: 0 }),"
        "}));",
        translations=_web_strings(),
    )
    assert values["first"].startswith("Preparing device support")
    assert values["retry"] == "Trying again (attempt 4)"
    assert values["retry_open"] == "Trying again (attempt 4)"
    assert values["idle"] is None


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_retry_shows_the_reason_the_bridge_carries_through_it():
    """Measured in the browser harness (12 September 2026): each retry
    started with `error: null`, so the failure sentence disappeared for the
    length of every retry and came back when it failed again - a three-line
    message blinking out and in every few seconds. The bridge now carries
    the previous attempt's reason through the retry (`ConnectionProgress`),
    translated per answer, and the row shows it as "Previous attempt".

    It used to be a copy the PAGE kept from the last `failed` poll, which a
    reload lost and which could not follow a language switch. So this runs
    the REAL `loadZigbeeRadio()` with retry answers that carry the error,
    and one that does not.

    Fault to prove it: read a page-side copy instead of `progress.error` in
    `zigbeeProgressText()` (the retry rows lose their reason), or drop the
    `progress.error` branch (they read "Trying again" alone)."""
    values = _app_state(
        _zigbee_state(
            "globalThis.setTimeout = () => 1; globalThis.clearTimeout = () => {};"
            f"const base = JSON.parse(JSON.stringify(state.zigbee));"
            f"base.configured_path = {json.dumps(ITEAD)}; base.configured_device_present = true;"
            "const answers = ["
            "  { state: 'failed', attempts: 1, error: 'The stick did not answer.' },"
            "  { state: 'opening_radio', attempts: 1, error: 'The stick did not answer.' },"
            "  { state: 'connected', attempts: 0, error: null },"
            "  { state: 'opening_radio', attempts: 1, error: null },"
            "];"
            "const out = [];"
            "(async () => {"
            "  for (const progress of answers) {"
            "    state.request = async () => ({ ...base, progress });"
            "    await state.loadZigbeeRadio();"
            "    out.push(state.zigbeeProgressText(state.zigbee.progress,"
            "      state.zigbee.configured_device_present));"
            "  }"
            "  console.log(JSON.stringify(out));"
            "})();"
        ),
        translations=_web_strings(),
    )
    assert values == [
        "The stick did not answer. (attempt 1 - the bridge keeps trying)",
        "Trying again (attempt 2). Previous attempt: The stick did not answer.",
        "Connected",
        "Trying again (attempt 2)",
    ]


def _zigbee_row(markup: str) -> str:
    start = markup.index('<div class="radios-zigbee">')
    return _element_at(markup, start, "div")


def _eval_in_state(expr: str, setup: str, translations: dict[str, str] | None = None) -> object:
    """Evaluates a markup expression the way Alpine does: with the `app()`
    object as scope, so a method called in the expression gets `this`
    bound to the state. The expression comes out of the SERVED markup."""
    return _app_state(
        setup
        + f"const scope = new Function('s', 'with (s) {{ return (' + {json.dumps(expr)} + '); }}');"
        "console.log(JSON.stringify(scope(state)));",
        translations=translations,
    )


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_progress_line_binding_passes_the_presence_flag(api):
    """`test_a_configured_stick_that_is_gone_says_so` proves the helper can
    say "not plugged in" when it is HANDED the flag. This proves the row
    hands it: the `x-text` on the progress line, extracted from the served
    page and evaluated against the state.

    Fault to prove it: drop `zigbee.configured_device_present` from the
    `x-text` in index.html. The line then reads the failure text instead."""
    client, _, _ = api
    row = _zigbee_row(_without_comments((await client.get("/")).text))
    match = re.search(r'class="hint radios-zigbee-progress".*?x-text="([^"]*)"', row, re.DOTALL)
    assert match, "no progress line in the Zigbee row"
    text = _eval_in_state(
        match.group(1),
        _zigbee_state(
            "state.zigbee.configured_path = '/dev/serial/by-id/gone';"
            "state.zigbee.progress = { state: 'failed', attempts: 9, error: 'no such device' };"
        ),
        translations=_web_strings(),
    )
    assert text.startswith("The chosen stick is not plugged in")


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_zigbee_option_binding_disables_what_the_helper_disables(api):
    """`zigbeeRadioOptions()` computing `disabled` is worth nothing if the
    `<option>` does not bind it - and worth just as little if this test only
    ever evaluated the raw JS truthiness of `option.disabled`. "No Zigbee
    stick" and the option for a configured stick the scan no longer finds
    both spell `disabled: false` out in their object literal rather than
    deriving it; deleting that key left `option.disabled` `undefined` for
    exactly those two options and every assertion here still green, while
    Alpine turns that `undefined` from a dotted `:disabled` expression into
    `""`, which a boolean attribute treats as present - the user could no
    longer remove a configured Zigbee stick that way. `boundTrue` (see
    `_BINDINGS_JS`) models that coercion; extracted from the served markup
    and evaluated for EVERY option `zigbeeRadioOptions()` returns, "No
    Zigbee stick" and the missing-stick option included.

    Fault to prove it: remove `:disabled="option.disabled"` from the Zigbee
    `<option>` (the extraction fails), bind it to `option.unrecognised` (the
    MG24 comes out enabled), or delete `disabled: false` from the "No
    Zigbee stick" or the missing-stick option in `zigbeeRadioOptions()`
    (app.js) - this test starts failing where the old one did not."""
    client, _, _ = api
    row = _zigbee_row(_without_comments((await client.get("/")).text))
    options = row[row.index('x-for="option in zigbeeRadioOptions()"') :]
    match = re.search(r'<option[^>]*:disabled="([^"]*)"', options)
    assert match, "the Zigbee <option> binds no :disabled"
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state("state.zigbee.configured_path = '/dev/serial/by-id/gone';")
        + "console.log(JSON.stringify(state.zigbeeRadioOptions().map((option) => ["
        "  option.value,"
        f"  boundTrue({json.dumps(match.group(1))}, {{ option }}),"
        "])));"
    )
    disabled = dict(values)
    assert disabled[""] is False
    assert disabled[MG24] is True
    assert disabled[ITEAD] is False
    assert disabled[UNKNOWN_STICK] is False
    assert disabled["/dev/serial/by-id/gone"] is False


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_configured_option_drops_in_use_while_it_is_the_open_draft():
    """A native `<select>` shows its CLOSED value using that option's own
    label - and the "in use" marker sits at the very end of it, exactly
    where a narrow select cuts a label off. Measured in German at 375 px:
    "SONOFF ZBDongle-E V2 · in Verwendung" came out clipped to "...in
    Verwendun". The suffix costs nothing while the draft still points at
    the configured stick (that is what "configured" already means), and it
    is not lost - it comes back on that same option as soon as the draft
    points elsewhere, which is what the open list then shows.

    Fault to prove it: drop `&& this.zigbeeDraft.path !== configured` from
    the `in_use` branch of `zigbeeRadioOptions()` (app.js) - `closed` below
    starts carrying the suffix again."""
    values = _app_state(
        _zigbee_state(
            f"state.zigbee.configured_path = {json.dumps(ITEAD)};"
            "state.zigbee.configured_device_present = true;"
            f"state.zigbeeDraft.path = {json.dumps(ITEAD)};"
            f"const closed = state.zigbeeRadioOptions().find((o) => o.value === {json.dumps(ITEAD)});"
            "state.zigbeeDraft.path = '';"
            f"const open = state.zigbeeRadioOptions().find((o) => o.value === {json.dumps(ITEAD)});"
            "console.log(JSON.stringify({ closed: closed.label, open: open.label }));"
        )
    )
    assert "web.radios.in_use" not in values["closed"]
    assert "web.radios.in_use" in values["open"]


async def test_the_zigbee_row_says_it_restarts_nothing_and_has_its_own_apply(api):
    """The two rows above it warn about a restart and run through the
    sidecar; this one does neither, and says so in the row itself. Its
    Apply calls its own method - a shared Apply would route a Zigbee change
    through the confirmation and the sidecar job.

    Fault to prove it: move the hint out of the Zigbee row, or point its
    button at `askApplyRadios()`."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    row = _zigbee_row(page)
    assert "t('web.radios.zigbee_hint')" in row
    assert '@click="applyZigbeeRadio()"' in row
    assert "askApplyRadios()" not in row
    assert 'class="commission-disclosure"' in row
    assert 'x-text="zigbeeApplyError"' in row
    # Below Thread and Bluetooth, inside the radios card.
    settings = page[page.index("view === 'settings'") :]
    card = settings[: settings.index("t('web.settings.language_heading')")]
    assert card.index("radiosBluetoothOptions()") < card.index('<div class="radios-zigbee">')


async def test_leaving_settings_stops_the_zigbee_timer(api):
    """The same leak `test_leaving_settings_stops_the_radios_timer` closes
    for the radios timer: a row polling once a second from a tab nobody is
    looking at.

    Fault to prove it: drop `this.stopZigbeeTimer();` from `selectView()`'s
    `view !== "settings"` block."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("async selectView(view) {")
    body = script[start : script.index("\n    },", start)]
    guard_start = body.index('if (view !== "settings")')
    guard = body[guard_start : body.index("}", guard_start) + 1]
    assert "this.stopZigbeeTimer();" in guard


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_load_that_finishes_after_leaving_settings_arms_no_timer():
    """`selectView()` stops the timer, but a load already in flight at that
    moment finishes afterwards - and would arm a new one behind its back.

    Fault to prove it: drop the `this.view !== "settings"` return in
    `scheduleZigbeeLoad()`."""
    values = _app_state(
        _zigbee_state(
            "let armed = 0;"
            "globalThis.setTimeout = () => { armed += 1; return 1; };"
            "globalThis.clearTimeout = () => {};"
            "state.view = 'devices';"
            "const body = JSON.parse(JSON.stringify(state.zigbee));"
            "body.progress = { state: 'loading_quirks', attempts: 0, error: null };"
            "state.request = async () => body;"
            "(async () => {"
            "  await state.loadZigbeeRadio();"
            "  console.log(JSON.stringify({ armed, timer: state.zigbeeTimer }));"
            "})();"
        )
    )
    assert values == {"armed": 0, "timer": None}


# ---------------------------------------------------------------------------
# The Zigbee row's bindings, run rather than read (review of Task 13).
#
# Seventeen mutations of the row survived the tests above: each one broke a
# binding in index.html or the call that wires it, and every test above
# either called the helper directly or checked that a substring was
# present. The tests below take the REAL attribute out of the served page -
# its `x-if`, `x-show`, `:disabled`, `x-model`, `@change`, `@click` - and
# run it against the real `app()` object in node the way Alpine does (the
# state as scope, a handler as a statement, `x-model` as an assignment).
# No DOM library: the repository has none, and a browser runtime is not a
# test dependency (pyproject.toml says why for playwright). What this
# cannot see is layout; the browser harness in the task report covers that.
# ---------------------------------------------------------------------------

_VOID_ELEMENTS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)


class _ServedElements(HTMLParser):
    """Every element of the served page as `(tag, attributes, ancestors)`,
    where `ancestors` is the `(tag, attributes)` chain from `<html>` down.

    Enough of an HTML tree for one question the regex helpers above cannot
    answer: which conditions an element renders under."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str], list[tuple[str, dict[str, str]]]]] = []
        self._open: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        self.elements.append((tag, attributes, list(self._open)))
        if tag not in _VOID_ELEMENTS:
            self._open.append((tag, attributes))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, {name: value or "" for name, value in attrs}, list(self._open)))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._open) - 1, -1, -1):
            if self._open[index][0] == tag:
                del self._open[index:]
                return


def _served_elements(
    markup: str,
) -> list[tuple[str, dict[str, str], list[tuple[str, dict[str, str]]]]]:
    parser = _ServedElements()
    parser.feed(_without_comments(markup))
    return parser.elements


def _zigbee_row_element(
    markup: str, tag: str, **wanted: str
) -> tuple[dict[str, str], list[tuple[str, dict[str, str]]]]:
    """The one `tag` inside the Zigbee row whose attributes include every
    `wanted` pair. Keys are spelled for Python: `_` for `-`, a leading
    `at_` for `@`, a leading `colon_` for `:`, and a trailing `_` dropped
    (`class_`). Exactly one, or the test fails on the extraction."""

    def spelled(name: str) -> str:
        if name.startswith("at_"):
            return "@" + name[3:].replace("_", "-")
        if name.startswith("colon_"):
            return ":" + name[6:].replace("_", "-")
        return name.rstrip("_").replace("_", "-")

    matches = [
        (attributes, ancestors)
        for element_tag, attributes, ancestors in _served_elements(markup)
        if element_tag == tag
        and any(
            candidate.get("class") == "radios-zigbee"
            for candidate in [attributes, *(ancestor for _, ancestor in ancestors)]
        )
        and all(attributes.get(spelled(name)) == value for name, value in wanted.items())
    ]
    assert len(matches) == 1, (
        f"expected one <{tag}> {wanted} in the Zigbee row, found {len(matches)}"
    )
    return matches[0]


# `run(expr)` evaluates an attribute expression, `exec(statement)` runs a
# handler - both with the state as scope, the way Alpine evaluates them.
_BINDINGS_JS = """
const run = (expr, extra = {}) =>
  new Function('s', 'extra', 'with (extra) { with (s) { return (' + expr + '); } }')(state, extra);
const exec = (statement, extra = {}) =>
  new Function('s', 'extra', 'with (extra) { with (s) { ' + statement + ' } }')(state, extra);
// Whether Alpine SETS a boolean attribute (`:disabled`) for this expression.
// Not `Boolean(run(expr))`: Alpine binds an `undefined` from a dotted
// expression as `""`, and a boolean attribute given `""` is present - which
// is how "No Thread stick" once rendered disabled with every test green.
const boundTrue = (expr, extra = {}) => {
  let value = run(expr, extra);
  if (value === undefined && expr.includes('.')) value = '';
  return ![null, undefined, false].includes(value);
};
globalThis.setTimeout = () => 1; globalThis.clearTimeout = () => {};
globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
"""


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_opening_settings_loads_the_zigbee_row():
    """The row is `x-if="zigbee"`, and only a load fills `zigbee` - so a
    Settings view that forgot to load it rendered no Zigbee row at all,
    and every helper test above stayed green.

    Through the REAL `selectView('settings')`, with `request` recording
    what it was asked.

    Fault to prove it: drop `await this.loadZigbeeRadio();` from
    `selectView()`."""
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            "globalThis.window = { location: { hash: '' }, history: { replaceState() {} } };"
            "const body = JSON.parse(JSON.stringify(state.zigbee));"
            "state.zigbee = null;"
            "const asked = [];"
            "state.request = async (method, url) => {"
            "  asked.push(method + ' ' + url);"
            "  if (url === '/api/zigbee/radio') return body;"
            f"  if (url === '/api/radios') return {json.dumps(RADIOS_READY)};"
            "  return { bridge_ip: '', udp_port: 7000, listen_port: 8080 };"
            "};"
            "(async () => {"
            "  await state.selectView('settings');"
            "  console.log(JSON.stringify({ asked, rendered: Boolean(run('zigbee')) }));"
            "})();"
        )
    )
    assert "GET /api/zigbee/radio" in values["asked"]
    assert values["rendered"] is True


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_without_a_thread_report_the_card_says_why_and_offers_no_stick(api):
    """When the bridge cannot tell which stick Thread is on, `GET
    /api/zigbee/radio` marks EVERY stick unselectable and carries the reason
    in `thread_refusal` (`radios/thread_lockout.py`). The card must show
    that sentence and must not offer Apply for any stick - while still
    letting the user turn Zigbee off.

    Runs the SERVED `x-show`/`x-text` of the reason line (plain truthiness:
    `x-show` is not a boolean attribute) and the SERVED `:disabled` of the
    Apply button (through `boundTrue`, which is), against that body.

    Fault to prove it: remove the reason line from the Zigbee row (the
    extraction fails), or drop the `selected?.disabled` check from
    `zigbeeCanApply()` (Apply comes out enabled for the ITEAD stick)."""
    client, _, _ = api
    page = (await client.get("/")).text
    reason, _ancestors = _zigbee_row_element(page, "p", x_text="zigbee.thread_refusal")
    apply_button, _ancestors = _zigbee_row_element(page, "button", at_click="applyZigbeeRadio()")
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            "for (const stick of state.zigbee.serial) stick.selectable = false;"
            "state.zigbee.thread_status = 'unknown';"
            "state.zigbee.thread_refusal = 'Thread unknown';"
            f"state.zigbee.configured_path = {json.dumps(UNKNOWN_STICK)};"
            "const out = {};"
            f"out.shown = Boolean(run({json.dumps(reason['x-show'])}));"
            f"out.text = run({json.dumps(reason['x-text'])});"
            f"state.zigbeeDraft.path = {json.dumps(ITEAD)};"
            f"out.itead = boundTrue({json.dumps(apply_button[':disabled'])});"
            "state.zigbeeDraft.path = '';"
            f"out.none = boundTrue({json.dumps(apply_button[':disabled'])});"
            "state.zigbee.thread_status = 'known'; state.zigbee.thread_refusal = null;"
            f"out.shown_when_known = Boolean(run({json.dumps(reason['x-show'])}));"
            "console.log(JSON.stringify(out));"
        )
    )
    assert values == {
        "shown": True,
        "text": "Thread unknown",
        "itead": True,
        "none": False,
        "shown_when_known": False,
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_zigbee_row_shows_the_firmware_the_stick_reported(api):
    """The firmware line under the Zigbee row, through its SERVED bindings:
    shown with the version `GET /api/zigbee/radio` reported in
    `coordinator.firmware`, and hidden - without an error - while there is
    no coordinator yet, which is what the route answers before the first
    successful connect and what every older body shape lacks entirely.
    `x-show` is evaluated for plain truthiness, as Alpine does - it is not a
    boolean attribute, so `boundTrue`'s coercion does not apply.

    Fault to prove it: bind `x-text` to `coordinator.radio_type`, or drop
    the `?.` so a `null` coordinator throws."""
    client, _, _ = api
    page = (await client.get("/")).text
    line = next(
        attributes
        for tag, attributes, _ancestors in _served_elements(page)
        if tag == "p" and "web.radios.zigbee_firmware" in attributes.get("x-text", "")
    )
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            "const out = {};"
            f"out.hidden = Boolean(run({json.dumps(line['x-show'])}));"
            "state.zigbee.coordinator = null;"
            f"out.hidden_null = Boolean(run({json.dumps(line['x-show'])}));"
            "state.zigbee.coordinator = { radio_type: 'ezsp', manufacturer: 'ITEAD',"
            "  model: 'Dongle-E', firmware: '7.4.4.0 build 0' };"
            f"out.shown = Boolean(run({json.dumps(line['x-show'])}));"
            f"out.text = run({json.dumps(line['x-text'])});"
            "console.log(JSON.stringify(out));"
        ),
        translations={"web.radios.zigbee_firmware": "Firmware: {firmware}"},
    )
    assert values == {
        "hidden": False,
        "hidden_null": False,
        "shown": True,
        "text": "Firmware: 7.4.4.0 build 0",
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_select_bindings_keep_a_choice_through_the_next_poll(api):
    """What the user picks in the select survives the poll that follows -
    the row polls every second during an attempt, and a poll that resynced
    the draft snapped the choice back under their hand.

    `zigbeeDirty` is what stops the resync, and the select's `@change` is
    the only thing that sets it. So this runs the SERVED `x-model` (as the
    assignment Alpine makes) and the SERVED `@change`, then a real poll.

    Fault to prove it: drop `@change="zigbeeSelectionChanged()"` from the
    Zigbee select (the extraction finds no handler), or bind it to
    something that does not mark the draft dirty (the poll puts the
    configured stick back)."""
    client, _, _ = api
    page = (await client.get("/")).text
    select, _ancestors = _zigbee_row_element(page, "select", x_model="zigbeeDraft.path")
    assert select.get("@change"), "the Zigbee select has no @change"
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            "const body = JSON.parse(JSON.stringify(state.zigbee));"
            f"body.configured_path = {json.dumps(ITEAD)}; body.configured_device_present = true;"
            "state.request = async () => body;"
            "(async () => {"
            "  await state.loadZigbeeRadio();"
            f"  exec({json.dumps(select['x-model'])} + ' = __value', {{ __value: '' }});"
            f"  exec({json.dumps(select['@change'])});"
            "  await state.loadZigbeeRadio();"
            "  console.log(JSON.stringify({ path: state.zigbeeDraft.path,"
            "    applicable: !run(" + json.dumps("zigbeeBusy") + ") && state.zigbeeCanApply() }));"
            "})();"
        )
    )
    assert values == {"path": "", "applicable": True}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_advanced_bindings_make_an_edit_a_change_apply_can_send(api):
    """The case the Advanced disclosure exists for: the configured stick is
    unrecognised, it is failing, and the user corrects its radio type or
    baud rate. The path does not change, so ONLY the Advanced handlers can
    turn that edit into something Apply will send.

    Runs the SERVED `x-model` and `@change` of the radio-type select and the
    SERVED `x-model.number` and `@input` of the baud-rate field, then the
    SERVED `:disabled` of the Apply button.

    Fault to prove it: drop `@change="zigbeeAdvancedChanged()"` or
    `@input="zigbeeAdvancedChanged()"` (Apply stays disabled after the
    edit), or have `zigbeeRadioChanged()` ignore `zigbeeAdvancedEdited`."""
    client, _, _ = api
    page = (await client.get("/")).text
    radio_type, _ = _zigbee_row_element(page, "select", x_model="zigbeeDraft.radioType")
    baudrate, _ = _zigbee_row_element(page, "input", type="number")
    apply_button, _ = _zigbee_row_element(page, "button", class_="primary")
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            "const body = JSON.parse(JSON.stringify(state.zigbee));"
            f"body.configured_path = {json.dumps(UNKNOWN_STICK)}; body.configured_device_present = true;"
            "body.configured_radio_type = 'ezsp'; body.configured_baudrate = 115200;"
            "body.progress = { state: 'failed', attempts: 3, error: 'no answer', changed_at: 'x' };"
            "state.request = async () => body;"
            "(async () => {"
            "  await state.loadZigbeeRadio();"
            f"  const disabled = () => run({json.dumps(apply_button[':disabled'])});"
            "  const before = disabled();"
            f"  exec({json.dumps(radio_type['x-model'])} + ' = __value', {{ __value: 'znp' }});"
            f"  exec({json.dumps(radio_type.get('@change', ''))});"
            "  const afterType = disabled();"
            "  await state.loadZigbeeRadio();"
            "  const afterPoll = [disabled(), state.zigbeeDraft.radioType];"
            "  state.zigbeeAdvancedEdited = false; state.zigbeeDirty = false;"
            "  state.zigbeeDraft.radioType = 'ezsp';"
            f"  exec({json.dumps(baudrate['x-model.number'])} + ' = __value', {{ __value: 38400 }});"
            f"  exec({json.dumps(baudrate.get('@input', ''))});"
            "  const afterBaud = disabled();"
            "  console.log(JSON.stringify({ before, afterType, afterPoll, afterBaud,"
            "    body: state.zigbeeRequestBody() }));"
            "})();"
        )
    )
    assert values["before"] is True
    assert values["afterType"] is False
    assert values["afterPoll"] == [False, "znp"]
    assert values["afterBaud"] is False
    assert values["body"] == {"path": UNKNOWN_STICK, "radio_type": "ezsp", "baudrate": 38400}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_apply_error_banner_binding_shows_the_refusal_and_keeps_it(api):
    """The existing checks read that the banner's `x-text` names
    `zigbeeApplyError`; a banner whose `x-show` was `false` passed them
    while never appearing. This runs the SERVED `x-show` after a real
    refused Apply and after the poll that follows it.

    Fault to prove it: set the banner's `x-show` to `false`, or to
    `zigbeeError`."""
    client, _, _ = api
    page = (await client.get("/")).text
    banner, _ = _zigbee_row_element(page, "p", x_text="zigbeeApplyError")
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            "state.view = 'settings';"
            f"state.zigbeeDraft.path = {json.dumps(ITEAD)}; state.zigbeeDirty = true;"
            "const body = JSON.parse(JSON.stringify(state.zigbee));"
            "state.request = async (method) => {"
            "  if (method === 'PUT') throw new Error('This stick is in use for Thread.');"
            "  return body;"
            "};"
            f"const shown = () => Boolean(run({json.dumps(banner['x-show'])}));"
            "(async () => {"
            "  const idle = shown();"
            "  await state.applyZigbeeRadio();"
            "  const refused = shown();"
            "  await state.loadZigbeeRadio();"
            "  console.log(JSON.stringify({ idle, refused, afterPoll: shown(),"
            f"    text: run({json.dumps(banner['x-text'])}) }}));"
            "})();"
        )
    )
    assert values == {
        "idle": False,
        "refused": True,
        "afterPoll": True,
        "text": "This stick is in use for Thread.",
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_fresh_install_shows_no_missing_stick(api):
    """A fresh install reports `configured_device_present: false` too -
    there is nothing configured to be present. The "not plugged in"
    sentence, in red, is for a stick that IS configured and gone; on a
    fresh install it would tell a new user to plug back in something they
    never had.

    Runs the SERVED `x-text` and `:class` of the progress line.

    Fault to prove it: drop the `configured_path` half of the check in
    `zigbeeStickMissing()`."""
    client, _, _ = api
    page = (await client.get("/")).text
    line, _ = _zigbee_row_element(page, "p", class_="hint radios-zigbee-progress")
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            "const out = {};"
            f"out.freshText = run({json.dumps(line['x-text'])});"
            f"out.freshClass = run({json.dumps(line[':class'])});"
            f"state.zigbee.configured_path = {json.dumps(ITEAD)};"
            "state.zigbee.progress = { state: 'failed', attempts: 2, error: 'gone' };"
            f"out.goneText = run({json.dumps(line['x-text'])});"
            f"out.goneClass = run({json.dumps(line[':class'])});"
            "console.log(JSON.stringify(out));"
        ),
        translations=_web_strings(),
    )
    assert not values["freshText"]
    assert values["freshClass"] == {"danger-text": False}
    assert values["goneText"].startswith("The chosen stick is not plugged in")
    assert values["goneClass"] == {"danger-text": True}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_zigbee_apply_button_puts_to_its_own_endpoint(api):
    """Spec Correction 3, through the button itself: the row's Apply goes to
    `PUT /api/zigbee/radio` and never to `/api/radios`, whose sidecar job
    recreates the container the page is talking to.

    Runs the SERVED `@click` against a recording `request`. The source
    check above (`test_applying_the_zigbee_row_never_sends_a_radios_request`)
    only reads `radiosRequestBody()`; a button pointed at
    `confirmApplyRadios()` - or an `applyZigbeeRadio()` that posted to
    `/api/radios` - passed it.

    Fault to prove it: send the Zigbee request to `/api/radios` in
    `applyZigbeeRadio()`, or point the button's `@click` at the radios
    Apply."""
    client, _, _ = api
    page = (await client.get("/")).text
    button, _ = _zigbee_row_element(page, "button", class_="primary")
    values = _app_state(
        _BINDINGS_JS
        + f"state.radios = {json.dumps(RADIOS_READY)};"
        + _zigbee_state(
            f"state.zigbeeDraft.path = {json.dumps(ITEAD)}; state.zigbeeDirty = true;"
            "const body = JSON.parse(JSON.stringify(state.zigbee));"
            "const sent = [];"
            "state.request = async (method, url, payload) => {"
            "  sent.push([method, url, payload ?? null]);"
            "  return method === 'GET' ? body : { progress: body.progress, id: 'job' };"
            "};"
            "(async () => {"
            f"  await run({json.dumps(button['@click'])});"
            "  console.log(JSON.stringify(sent.filter(([method]) => method !== 'GET')));"
            "})();"
        )
    )
    assert values == [["PUT", "/api/zigbee/radio", {"path": ITEAD}]]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_zigbee_row_renders_without_the_radios_sidecar(api):
    """The row reads an endpoint the bridge answers itself, so a missing or
    outdated updater sidecar - `radios` never loaded - must not hide it.
    A substring check could not tell whether the row sits inside
    `<template x-if="radios">`; this evaluates EVERY `x-if` and `x-show`
    the row renders under, with `radios` null.

    The same chain with `zigbee` null must NOT render, which is what keeps
    the check from passing on an extraction that found no conditions.

    Fault to prove it: move the Zigbee `<template>` inside the
    `x-if="radios"` block."""
    client, _, _ = api
    page = (await client.get("/")).text
    _row, ancestors = _zigbee_row_element(page, "div", class_="radios-zigbee")
    conditions = [
        attributes[name]
        for _tag, attributes in ancestors
        for name in ("x-if", "x-show")
        if name in attributes
    ]
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            "state.radios = null; state.view = 'settings';"
            "state.stringsReady = true; state.authenticated = true;"
            f"const conditions = {json.dumps(conditions)};"
            "const renders = () => conditions.every((condition) => Boolean(run(condition)));"
            "const withoutSidecar = renders();"
            "state.zigbee = null;"
            "console.log(JSON.stringify({ withoutSidecar, withoutZigbee: renders() }));"
        )
    )
    assert values == {"withoutSidecar": True, "withoutZigbee": False}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_configured_stick_the_scan_lost_stays_the_selected_option(api):
    """A native `<select>` whose value matches no `<option>` shows its first
    one - here "No Zigbee stick", a setting nobody made, while the bridge
    keeps retrying the real one. So the configured stick keeps an option of
    its own when the scan no longer finds it.

    Runs the SERVED `x-for` and `x-text` of the Zigbee `<option>` and the
    SERVED `x-model` of the select, and asks which option the select would
    show.

    Fault to prove it: drop the configured-but-missing option from
    `zigbeeRadioOptions()`."""
    client, _, _ = api
    page = (await client.get("/")).text
    select, _ = _zigbee_row_element(page, "select", x_model="zigbeeDraft.path")
    loop, _ = _zigbee_row_element(page, "template", x_for="option in zigbeeRadioOptions()")
    option, _ = _zigbee_row_element(
        page, "option", colon_value="option.value", colon_disabled="option.disabled"
    )
    source = loop["x-for"].split(" in ", 1)[1]
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            "const body = JSON.parse(JSON.stringify(state.zigbee));"
            "body.serial = body.serial.filter((stick) => !stick.path.includes('Itead'));"
            f"body.configured_path = {json.dumps(ITEAD)}; body.configured_device_present = false;"
            "body.progress = { state: 'failed', attempts: 4, error: 'gone', changed_at: 'x' };"
            "state.request = async () => body;"
            "(async () => {"
            "  await state.loadZigbeeRadio();"
            f"  const value = run({json.dumps(select['x-model'])});"
            f"  const shown = run({json.dumps(source)}).find((candidate) =>"
            "    run(" + json.dumps(option[":value"]) + ", { option: candidate }) === value);"
            "  console.log(JSON.stringify({ value, label: shown ? run("
            + json.dumps(option["x-text"])
            + ", { option: shown }) : null }));"
            "})();"
        ),
        translations=_web_strings(),
    )
    assert values == {"value": ITEAD, "label": f"{ITEAD} (missing)"}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_failing_stick_can_be_corrected_while_the_bridge_retries_it(api):
    """The reviewer's case: an unrecognised stick is configured and failing,
    and the user corrects its radio type under Advanced. Apply was enabled
    in `failed` and disabled in `loading_quirks`/`opening_radio` of every
    retry - so for most of the first minute the change the failure text
    asks for was refused, silently. Choosing "No Zigbee stick" mid-retry
    was refused the same way.

    Blocked now only while a change is being carried out for the first
    time: `applying`, or a first attempt (`attempts === 0`). The SERVED
    `:disabled` of the Apply button, in each state.

    Fault to prove it: block Apply in every working state again
    (`zigbeeRadioPolling()` in `zigbeeCanApply()`)."""
    client, _, _ = api
    page = (await client.get("/")).text
    button, _ = _zigbee_row_element(page, "button", class_="primary")
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            f"state.zigbee.configured_path = {json.dumps(UNKNOWN_STICK)};"
            "state.zigbee.configured_device_present = true;"
            f"state.zigbeeDraft.path = {json.dumps(UNKNOWN_STICK)};"
            "state.zigbeeDraft.radioType = 'znp'; state.zigbeeAdvancedEdited = true;"
            "state.zigbeeDirty = true;"
            f"const enabled = () => !run({json.dumps(button[':disabled'])});"
            "const out = {};"
            "for (const [name, progress] of Object.entries({"
            "  failed: { state: 'failed', attempts: 3, error: 'e' },"
            "  retry_quirks: { state: 'loading_quirks', attempts: 3, error: 'e' },"
            "  retry_opening: { state: 'opening_radio', attempts: 3, error: 'e' },"
            "  first_quirks: { state: 'loading_quirks', attempts: 0, error: null },"
            "  first_opening: { state: 'opening_radio', attempts: 0, error: null },"
            "  applying: { state: 'applying', attempts: 0, error: null },"
            "})) { state.zigbee.progress = progress; out[name] = enabled(); }"
            "state.zigbee.progress = { state: 'opening_radio', attempts: 3, error: 'e' };"
            "state.zigbeeDraft.path = ''; state.zigbeeAdvancedEdited = false;"
            "out.none_mid_retry = enabled();"
            "console.log(JSON.stringify(out));"
        )
    )
    assert values == {
        "failed": True,
        "retry_quirks": True,
        "retry_opening": True,
        "first_quirks": False,
        "first_opening": False,
        "applying": False,
        "none_mid_retry": True,
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_finished_radios_job_reloads_the_zigbee_row():
    """Turning Thread off in the row above frees the MG24 for Zigbee - and
    the Zigbee row kept it locked as "in use for Thread" until Rescan or
    re-entering Settings, because nothing reloaded it.

    The REAL `loadRadios()`: a job seen running and then `done` reloads the
    Zigbee row once, and so does a job that was already `done` on the first
    poll after its POST. An ordinary poll with nothing finishing does not
    reload it - that would double every radios poll.

    Fault to prove it: drop the `if (jobEnded) this.loadZigbeeRadio();`
    line from `loadRadios()`."""
    running = {**RADIOS_READY, "job": {"id": "j1", "phase": "otbr", "steps": ["otbr"]}}
    done = {
        **RADIOS_READY,
        "current": {**RADIOS_READY["current"], "thread_enabled": False, "otbr_running": False},
        "job": {"id": "j1", "phase": "done", "steps": ["otbr"], "error": None},
    }
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            "state.view = 'settings';"
            "const zigbeeBody = JSON.parse(JSON.stringify(state.zigbee));"
            "zigbeeBody.serial[0].is_thread = false; zigbeeBody.serial[0].selectable = true;"
            "let zigbeeGets = 0; let radios;"
            "state.request = async (method, url) => {"
            "  if (url === '/api/zigbee/radio') { zigbeeGets += 1; return zigbeeBody; }"
            "  return radios;"
            "};"
            "(async () => {"
            "  const out = {};"
            f"  radios = {json.dumps(RADIOS_READY)}; await state.loadRadios(); await state.loadRadios();"
            "  out.quiet = zigbeeGets;"
            f"  radios = {json.dumps(running)}; await state.loadRadios();"
            "  out.running = zigbeeGets;"
            f"  radios = {json.dumps(done)}; await state.loadRadios();"
            "  await new Promise((resolve) => setImmediate(resolve));"
            "  out.done = zigbeeGets;"
            f"  radios = {json.dumps(done)}; await state.loadRadios();"
            "  out.doneAgain = zigbeeGets;"
            f"  out.mg24 = state.zigbeeRadioOptions().find((o) => o.value === {json.dumps(MG24)}).disabled;"
            "  state.radios = null; zigbeeGets = 0; state.radiosPendingJobId = 'j1';"
            f"  radios = {json.dumps(done)}; await state.loadRadios();"
            "  out.fastJob = zigbeeGets;"
            "  console.log(JSON.stringify(out));"
            "})();"
        )
    )
    assert values == {
        "quiet": 0,
        "running": 0,
        "done": 1,
        "doneAgain": 1,
        "mg24": False,
        "fastJob": 1,
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_thread_stick_hint_names_the_stick_and_the_way_out(api):
    """The Thread stick is listed disabled with its reason at the END of
    the option label - where a narrow native select cuts it off (measured
    at 375 px in German: "… · in Verw"). And nothing on the card said how
    to get it back. The line under the select says both, whenever the list
    holds the Thread stick, and says nothing when it does not.

    The SERVED `x-show`/`x-text` of that line.

    Fault to prove it: remove the hint's `x-show` condition (it shows with
    no Thread stick), or return `null` from `zigbeeThreadHint()`."""
    client, _, _ = api
    page = (await client.get("/")).text
    hint, _ = _zigbee_row_element(page, "p", x_text="zigbeeThreadHint()")
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            "const out = {};"
            f"out.locked = [Boolean(run({json.dumps(hint['x-show'])})), run({json.dumps(hint['x-text'])})];"
            "state.zigbee.serial[0].is_thread = false; state.zigbee.serial[0].selectable = true;"
            f"out.free = Boolean(run({json.dumps(hint['x-show'])}));"
            "console.log(JSON.stringify(out));"
        ),
        translations=_web_strings(),
    )
    shown, text = values["locked"]
    assert shown is True
    assert text.startswith("SONOFF Dongle Plus MG24 is in use for Thread")
    assert "turn Thread off or move Thread to another stick" in text
    assert values["free"] is False


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_zigbee_stick_thread_took_over_reads_as_one_claim():
    """A stick set up for Zigbee that Thread was later moved onto read
    "… · in use · in use for Thread" - two claims that seem to contradict
    each other. It gets one suffix, and the hint names the conflict and
    both ways out of it.

    Fault to prove it: push `web.radios.in_use` for the configured stick
    whether or not it is the Thread stick."""
    values = _app_state(
        _zigbee_state(
            f"state.zigbee.configured_path = {json.dumps(MG24)};"
            "state.zigbee.configured_device_present = true;"
            "console.log(JSON.stringify({"
            f"  label: state.zigbeeRadioOptions().find((o) => o.value === {json.dumps(MG24)}).label,"
            "  hint: state.zigbeeThreadHint(),"
            "}));"
        ),
        translations=_web_strings(),
    )
    assert values["label"] == "SONOFF Dongle Plus MG24 · taken over by Thread"
    assert values["hint"].startswith("SONOFF Dongle Plus MG24 is set up for Zigbee")
    assert "choose another stick for Zigbee here" in values["hint"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_german_retry_line_does_not_say_the_bridge_gave_up():
    """ "Letzter Versuch" reads as "final attempt" - on a line whose whole
    point (`web.radios.zigbee_failed_retrying`) is that the bridge never
    gives up. Rendered with the real German table.

    Fault to prove it: put "Letzter Versuch" back into
    `web.radios.zigbee_retrying_after`."""
    from loxmatter import i18n

    i18n.set_language("de")
    german = _web_strings()
    i18n.set_language("en")
    values = _app_state(
        "console.log(JSON.stringify(state.zigbeeProgressText("
        "  { state: 'opening_radio', attempts: 2, error: 'Der Stick antwortet nicht.' })));",
        translations=german,
    )
    assert (
        values
        == "Neuer Verbindungsversuch (Versuch 3). Vorheriger Versuch: Der Stick antwortet nicht."
    )


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_warm_up_line_is_true_for_a_reconnect_too():
    """After a lost link the supervisor reconnects with `attempts` still 0,
    so `loading_quirks` shows the first-attempt line - which used to say
    "the first connection takes a few seconds". Both languages.

    Fault to prove it: put "first connection" / "erste Verbindung" back
    into `web.radios.zigbee_loading_quirks`."""
    from loxmatter import i18n

    lines = {}
    for language in ("en", "de"):
        i18n.set_language(language)
        lines[language] = _app_state(
            "console.log(JSON.stringify(state.zigbeeProgressText({ state: 'loading_quirks', attempts: 0 })));",
            translations=_web_strings(),
        )
    i18n.set_language("en")
    assert "first" not in lines["en"].lower()
    assert "erste" not in lines["de"].lower()


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_retry_line_keeps_the_colour_of_the_failure_it_carries(api):
    """The retry line carries the failure sentence of the `failed` line
    either side of it, and was grey while that one was red - the same
    words flipping colour every few seconds. The SERVED `:class`.

    Fault to prove it: colour only `failed` in `zigbeeProgressFailing()`."""
    client, _, _ = api
    page = (await client.get("/")).text
    line, _ = _zigbee_row_element(page, "p", class_="hint radios-zigbee-progress")
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            f"state.zigbee.configured_path = {json.dumps(ITEAD)};"
            "state.zigbee.configured_device_present = true;"
            "const out = {};"
            "for (const [name, progress] of Object.entries({"
            "  failed: { state: 'failed', attempts: 2, error: 'e' },"
            "  retry: { state: 'opening_radio', attempts: 2, error: 'e' },"
            "  first: { state: 'opening_radio', attempts: 0, error: null },"
            "  connected: { state: 'connected', attempts: 0, error: null },"
            "})) {"
            "  state.zigbee.progress = progress;"
            f"  out[name] = run({json.dumps(line[':class'])})['danger-text'];"
            "}"
            "console.log(JSON.stringify(out));"
        )
    )
    assert values == {"failed": True, "retry": True, "first": False, "connected": False}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_live_region_speaks_when_the_news_changes_not_the_counter(api):
    """The progress line was `role="status"` and carries an attempt counter,
    so a screen reader re-read a long failure sentence on every retry. The
    visible line is no live region now; a hidden one is, bound to
    `zigbeeAnnouncement`, which is written only when its text changes.

    Through the REAL `loadZigbeeRadio()`, counting every write to the field
    Alpine would re-render.

    Fault to prove it: bind the live region to `zigbeeProgressText(...)`
    (the markup check fails), put `role="status"` back on the visible line,
    or write `zigbeeAnnouncement` on every load."""
    client, _, _ = api
    page = (await client.get("/")).text
    live = [
        attributes
        for tag, attributes, ancestors in _served_elements(page)
        if attributes.get("role") == "status"
        and any(ancestor.get("class") == "radios-zigbee" for _, ancestor in ancestors)
    ]
    assert len(live) == 1, live
    assert live[0].get("x-text") == "zigbeeAnnouncement"
    assert "radios-zigbee-progress" not in live[0].get("class", "")
    values = _app_state(
        _BINDINGS_JS
        + _zigbee_state(
            "const writes = [];"
            "let stored = state.zigbeeAnnouncement;"
            "Object.defineProperty(state, 'zigbeeAnnouncement', {"
            "  get: () => stored, set: (value) => { writes.push(value); stored = value; } });"
            "const base = JSON.parse(JSON.stringify(state.zigbee));"
            f"base.configured_path = {json.dumps(ITEAD)}; base.configured_device_present = true;"
            "const answers = ["
            "  { state: 'failed', attempts: 1, error: 'No answer.' },"
            "  { state: 'opening_radio', attempts: 1, error: 'No answer.' },"
            "  { state: 'failed', attempts: 2, error: 'No answer.' },"
            "  { state: 'failed', attempts: 3, error: 'No answer.' },"
            "  { state: 'failed', attempts: 4, error: 'Busy.' },"
            "  { state: 'connected', attempts: 0, error: null },"
            "  { state: 'connected', attempts: 0, error: null },"
            "];"
            "(async () => {"
            "  for (const progress of answers) {"
            "    state.request = async () => ({ ...base, progress });"
            "    await state.loadZigbeeRadio();"
            "  }"
            "  console.log(JSON.stringify(writes));"
            "})();"
        ),
        translations=_web_strings(),
    )
    assert values == ["No answer.", "Busy.", "Connected"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_thread_row_offers_the_zigbee_stick_disabled_with_its_reason(api):
    """The reverse of the Zigbee row's Thread lock. `POST /api/radios`
    refuses the Zigbee stick for Thread, but the row offered it, the user
    confirmed a restart dialog, and only then read the refusal. The row
    lists it disabled, with the reason, from the server's `is_zigbee`.

    The SERVED `:disabled` and `x-text` of the Thread `<option>`, bound the
    way Alpine binds them (`boundTrue`) - measured in the browser, an option
    without a `zigbee` key came out DISABLED, because Alpine turns an
    `undefined` from `option.zigbee` into `""`. "No Thread stick (Thread
    off)" was that option.

    Fault to prove it: drop `:disabled="option.zigbee"` from the Thread
    option, read something other than `is_zigbee` for it, or leave `zigbee`
    off the "No Thread stick" option."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    thread_option = next(
        attributes
        for tag, attributes, ancestors in _served_elements(page)
        if tag == "option"
        and any(
            ancestor.get("x-for") == "option in radiosThreadOptions()" for _, ancestor in ancestors
        )
    )
    radios = json.loads(json.dumps(RADIOS_READY))
    radios["serial"][1]["is_zigbee"] = True
    values = _app_state(
        _BINDINGS_JS + f"state.radios = {json.dumps(radios)};"
        "console.log(JSON.stringify(state.radiosThreadOptions().map((option) => ["
        "  option.value,"
        f"  boundTrue({json.dumps(thread_option.get(':disabled', 'false'))}, {{ option }}),"
        f"  run({json.dumps(thread_option['x-text'])}, {{ option }}),"
        "])));",
        translations=_web_strings(),
    )
    rows = {value: (disabled, label) for value, disabled, label in values}
    assert rows["/dev/serial/by-id/usb-B"] == (True, "ttyACM0 · in use for Zigbee")
    assert rows["/dev/serial/by-id/usb-A"][0] is False
    assert rows[""][0] is False


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_radios_zigbee_hint_names_the_stick_and_the_way_out(api):
    """The Zigbee stick is listed in the Thread row disabled with its
    reason at the END of the option label - where a narrow native select
    cuts it off first (`test_the_thread_row_offers_the_zigbee_stick_disabled_with_its_reason`
    measured "ttyACM0 · in use for Zigbee"). And nothing on the card said
    how to get it back. `radiosZigbeeHint()` (mirroring `zigbeeThreadHint()`)
    is the line under the Thread select that says both, whenever the list
    holds a Zigbee stick, and says nothing when it does not.

    The SERVED `x-show`/`x-text` of that line.

    Fault to prove it: remove the hint's `x-show` condition (it shows with
    no Zigbee stick), or return `null` from `radiosZigbeeHint()`."""
    client, _, _ = api
    page = (await client.get("/")).text
    hint = next(
        attributes
        for tag, attributes, _ancestors in _served_elements(page)
        if tag == "p" and attributes.get("x-text") == "radiosZigbeeHint()"
    )
    radios = json.loads(json.dumps(RADIOS_READY))
    radios["serial"][1]["is_zigbee"] = True
    values = _app_state(
        _BINDINGS_JS + f"state.radios = {json.dumps(radios)};" + "const out = {};"
        f"out.locked = [Boolean(run({json.dumps(hint['x-show'])})), run({json.dumps(hint['x-text'])})];"
        "state.radios.serial[1].is_zigbee = false;"
        f"out.free = Boolean(run({json.dumps(hint['x-show'])}));"
        "console.log(JSON.stringify(out));",
        translations=_web_strings(),
    )
    shown, text = values["locked"]
    assert shown is True
    assert text.startswith("ttyACM0 is set up for Zigbee, so it cannot be chosen here for Thread")
    assert "choose another stick for Zigbee" in text
    assert values["free"] is False


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_radios_zigbee_hint_sits_under_thread_and_names_the_product(api):
    """Two things the task 13 screenshot in German showed wrong about the
    line `test_the_radios_zigbee_hint_names_the_stick_and_the_way_out`
    covers.

    WHERE: it stood under the Bluetooth select. It is about the Thread
    select, and a reader looks for an explanation under the control it
    explains - so in document order it comes after the Thread select and
    before the Bluetooth one.

    WHAT IT CALLS THE STICK: "ttyUSB1 ist für Zigbee eingerichtet", while
    the Zigbee row's own hint, one row further down, named the same stick
    "SONOFF ZBDongle-E V2". A stick with no USB product string is named by
    the fingerprint table's name when the Zigbee row has one for it, in the
    hint AND in the Thread option it points at; the tty is the last resort.

    Fault to prove it: move the hint back below the Bluetooth row, or drop
    the fingerprint step from `stickName()`."""
    client, _, _ = api
    page = (await client.get("/")).text
    elements = _served_elements(page)

    def position(predicate) -> int:
        found = [
            index
            for index, (tag, attributes, _) in enumerate(elements)
            if predicate(tag, attributes)
        ]
        assert len(found) == 1, found
        return found[0]

    thread = position(
        lambda tag, a: tag == "select" and a.get("x-model") == "radiosDraft.threadDevice"
    )
    hint = position(lambda tag, a: tag == "p" and a.get("x-text") == "radiosZigbeeHint()")
    bluetooth = position(
        lambda tag, a: tag == "select" and a.get("x-model.number") == "radiosDraft.bluetoothAdapter"
    )
    assert thread < hint < bluetooth

    radios = json.loads(json.dumps(RADIOS_READY))
    radios["serial"][1]["is_zigbee"] = True
    zigbee = {
        "serial": [
            {
                "path": "/dev/serial/by-id/usb-B",
                "product": None,
                "fingerprint": {"name": "SONOFF ZBDongle-E V2", "radio_type": "ezsp"},
                "is_thread": False,
                "selectable": True,
            }
        ],
        "configured_path": "/dev/serial/by-id/usb-B",
    }
    values = _app_state(
        f"state.radios = {json.dumps(radios)}; state.zigbee = {json.dumps(zigbee)};"
        "console.log(JSON.stringify({"
        "  hint: state.radiosZigbeeHint(),"
        "  option: state.radiosThreadOptions().find((o) => o.value === '/dev/serial/by-id/usb-B').label,"
        "  productWins: state.radiosThreadOptions().find((o) => o.value === '/dev/serial/by-id/usb-A').label,"
        "}));",
        translations=_web_strings(),
    )
    assert values["hint"].startswith("SONOFF ZBDongle-E V2 is set up for Zigbee")
    assert values["option"] == "SONOFF ZBDongle-E V2"
    # A stick WITH a product string keeps it: the fingerprint step only
    # fills a gap, it does not rename what the row already showed.
    assert values["productWins"] == "SONOFF Dongle Plus MG24 · …50c9"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_closed_thread_and_bluetooth_selects_drop_the_in_use_suffix(api):
    """The Zigbee row's rule (`test_the_configured_option_drops_in_use_while_it_is_the_open_draft`),
    applied to the two rows above it. A native `<select>` shows its CLOSED
    value with that option's own label, and cuts it off from the right -
    measured on the maintainer's real stick in German at 375 px: "SONOFF
    Dongle Plus MG24 · …50c9 · ir". The "in use" marker is left off while
    the draft still points at the option in use, and comes back on that
    option in the open list once the draft points elsewhere.

    The SERVED `x-text` of both `<option>`s, with the state as scope. The
    "taken over by Zigbee" suffix is NOT dropped: it is a conflict the user
    has to act on, not a restatement of the selection.

    Fault to prove it: drop the draft comparison from
    `radiosThreadOptionLabel()` or `radiosBluetoothOptionLabel()`."""
    client, _, _ = api
    page = (await client.get("/")).text

    def option_text(select_attribute: str, select_value: str) -> str:
        return next(
            attributes["x-text"]
            for tag, attributes, ancestors in _served_elements(page)
            if tag == "option"
            and any(ancestor.get(select_attribute) == select_value for _, ancestor in ancestors)
        )

    thread_text = option_text("x-model", "radiosDraft.threadDevice")
    bluetooth_text = option_text("x-model.number", "radiosDraft.bluetoothAdapter")
    values = _radios_values(
        _BINDINGS_JS + "const label = (expr, value, options) =>"
        "  run(expr, { option: options.find((o) => o.value === value) });"
        f"const thread = {json.dumps(thread_text)}; const bluetooth = {json.dumps(bluetooth_text)};"
        "const out = {};"
        "out.threadClosed = label(thread, '/dev/serial/by-id/usb-A', state.radiosThreadOptions());"
        "out.bluetoothClosed = label(bluetooth, 0, state.radiosBluetoothOptions());"
        "state.radiosDraft = { threadDevice: '/dev/serial/by-id/usb-B', bluetoothAdapter: 1 };"
        "out.threadOpen = label(thread, '/dev/serial/by-id/usb-A', state.radiosThreadOptions());"
        "out.bluetoothOpen = label(bluetooth, 0, state.radiosBluetoothOptions());"
        "state.radiosDraft.threadDevice = '/dev/serial/by-id/usb-A';"
        "state.radios.serial[0].is_zigbee = true;"
        "out.takenOver = label(thread, '/dev/serial/by-id/usb-A', state.radiosThreadOptions());"
        "console.log(JSON.stringify(out));",
        translations=_web_strings(),
    )
    assert values == {
        "threadClosed": "SONOFF Dongle Plus MG24 · …50c9",
        "bluetoothClosed": "hci0 · built in (UART)",
        "threadOpen": "SONOFF Dongle Plus MG24 · …50c9 · in use",
        "bluetoothOpen": "hci0 · built in (UART) · in use",
        "takenOver": "SONOFF Dongle Plus MG24 · …50c9 · taken over by Zigbee",
    }


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_thread_stick_zigbee_took_over_reads_as_one_claim(api):
    """The Thread-row mirror of `test_a_zigbee_stick_thread_took_over_reads_as_one_claim`:
    a stick in use for Thread that Zigbee was later set up on read "... ·
    in use · in use for Zigbee" - two claims that seem to contradict each
    other. It gets one suffix, and the hint under the select names the
    conflict and both ways out of it.

    The SERVED `x-text` of the Thread `<option>`, evaluated the way Alpine
    evaluates it, plus `radiosZigbeeHint()`.

    Fault to prove it: push `web.radios.in_use` and
    `web.radios.thread_option_zigbee` for the same option unconditionally
    instead of the single `web.radios.thread_zigbee_took_over`."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    thread_option = next(
        attributes
        for tag, attributes, ancestors in _served_elements(page)
        if tag == "option"
        and any(
            ancestor.get("x-for") == "option in radiosThreadOptions()" for _, ancestor in ancestors
        )
    )
    values = _app_state(
        _BINDINGS_JS
        + f"state.radios = {json.dumps(RADIOS_READY)};"
        + "state.radios.serial[0].is_zigbee = true;"
        "const option = state.radiosThreadOptions().find((o) => o.value === '/dev/serial/by-id/usb-A');"
        "console.log(JSON.stringify({"
        f"  label: run({json.dumps(thread_option['x-text'])}, {{ option }}),"
        "  hint: state.radiosZigbeeHint(),"
        "}));",
        translations=_web_strings(),
    )
    assert values["label"] == "SONOFF Dongle Plus MG24 · …50c9 · taken over by Zigbee"
    assert values["hint"].startswith("SONOFF Dongle Plus MG24 · …50c9 is in use for Thread")
    assert "choose another stick for Zigbee" in values["hint"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_zigbee_apply_refreshes_the_thread_rows_lock():
    """The Thread row learns which stick is the Zigbee one from
    `GET /api/radios`. Measured in the browser harness: after a Zigbee
    Apply it kept offering the newly chosen stick for Thread - and would
    have kept the previous one disabled - until Rescan.

    Fault to prove it: drop the `loadRadios()` call after a successful
    `PUT` in `applyZigbeeRadio()`."""
    values = _app_state(
        _BINDINGS_JS
        + f"state.radios = {json.dumps(RADIOS_READY)};"
        + _zigbee_state(
            f"state.zigbeeDraft.path = {json.dumps(ITEAD)}; state.zigbeeDirty = true;"
            "const body = JSON.parse(JSON.stringify(state.zigbee));"
            "const asked = [];"
            "state.request = async (method, url) => {"
            "  asked.push(method + ' ' + url);"
            f"  if (url === '/api/radios') return {json.dumps(RADIOS_READY)};"
            "  return method === 'GET' ? body : { progress: body.progress };"
            "};"
            "(async () => {"
            "  await state.applyZigbeeRadio();"
            "  await new Promise((resolve) => setImmediate(resolve));"
            "  console.log(JSON.stringify(asked));"
            "})();"
        )
    )
    assert values.index("PUT /api/zigbee/radio") < values.index("GET /api/radios")


# ---------------------------------------------------------------------------
# The Zigbee pairing tab on the commissioning card (design 2026-09-12,
# section 3.1): the tab strip, the join window and its countdown, and one
# row per device the radio has seen.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_zigbee_tab_is_absent_without_a_configured_source(api):
    """Absent, not disabled. A tab that explains why it does nothing is
    worse than no tab - and an installation with no Zigbee stick is the
    normal case, not an error state.

    Two halves, because the fault has two.

    The MARKUP half: the tab button sits inside an `x-if` template, which
    takes it out of the DOM, and carries no `disabled` binding of its own.
    Every other `nav.tabs` strip in index.html - the language card, the
    update channel - DOES carry one, so an implementer copying the nearest
    example writes the fault by hand; this is the assertion that stops it.

    The BEHAVIOUR half: the gate reads the STORED setting
    (`configured_path` from `GET /api/zigbee/radio`) and not whether `GET
    /api/zigbee/pairing` answered. That route returns 503 for an
    unconfigured source AND for the window of a radio swap - the same
    `_require_source` for both (Task 12) - so a tab gated on the list
    having loaded would disappear under the user mid-swap, which is the
    same bug as never showing it, arriving at a worse moment.

    Fault to prove it: render the tab disabled instead."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    # The tag the label lives in, located the way
    # `test_a_stalled_sidecar_gets_its_own_message_in_the_running_state`
    # locates its own - `rindex` back to the opening tag, `index` forward
    # to its `>` - rather than by retyping the markup here.
    label_at = markup.index("t('web.zigbee.tab')")
    tag = markup[markup.rindex("<button", 0, label_at) : markup.index(">", label_at)]
    assert "disabled" not in tag, tag

    # ABSENT: an `x-if` template encloses the button. `x-show` would leave
    # a hidden control on the strip and `:disabled` a visible dead one -
    # and the `</template>` check is what proves this template is the
    # button's OWN enclosure rather than an earlier one already closed.
    gate_at = markup.rindex("<template x-if=", 0, label_at)
    assert "</template>" not in markup[gate_at:label_at]
    gate_match = re.match(r'<template x-if="([^"]*)"', markup[gate_at:])
    assert gate_match, markup[gate_at : gate_at + 120]
    assert "zigbeeTabVisible()" in gate_match.group(1)

    # And the gate's own answer, from the REAL helper.
    values = _app_state(
        setup="const answer = (zigbee, pairing, error) => {"
        "  state.zigbee = zigbee;"
        "  state.zigbeePairing = pairing;"
        "  state.zigbeePairingError = error ?? null;"
        "  return state.zigbeeTabVisible();"
        "};"
        "console.log(JSON.stringify({"
        "  nothing_loaded: answer(null, null, null),"
        "  unconfigured: answer({ configured_path: null }, null, null),"
        "  configured: answer({ configured_path: '/dev/serial/by-id/a' },"
        "    { permit_until: null, rows: [] }, null),"
        "  mid_swap: answer({ configured_path: '/dev/serial/by-id/a' }, null,"
        "    'There is no Zigbee radio configured.'),"
        "}));"
    )
    # Nothing configured, and nothing known yet: no tab either way. The
    # second is the state the page is in for the instant between login and
    # the first answer, and a tab that flickered into existence there would
    # be worse than one that arrives a moment late.
    assert values["unconfigured"] is False
    assert values["nothing_loaded"] is False
    # Configured: offered.
    assert values["configured"] is True
    # Configured, and the pairing list is currently answering 503 because
    # the radio is being swapped. The tab STAYS. This is the assertion that
    # separates "reads the stored setting" from "reads whether the list
    # loaded"; without it both implementations pass.
    assert values["mid_swap"] is True


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_countdown_is_computed_from_the_server_timestamp(api):
    """ZHA starts its permit window when the page OPENS and runs a
    browser-side setTimeout(254000), so a reloaded page silently restarts
    the countdown while the real window is nearly over. The API returns
    `permit_until`, and this counts down to it - so a reload, a second tab
    and a phone all show the same truth.

    `permit_until` is `null` whenever the window is not open, and that is
    not an edge case: it is what `GET /api/zigbee/pairing` reports after a
    Stop, after the duration ran out, and after the radio went away while
    the rows stayed (Task 12). Null is therefore zero seconds left - never
    `NaN`, which renders as "Open for NaN s", and never a negative number
    ticking downwards past zero.

    Fault to prove it: count down from a duration stored when the button was
    pressed."""
    values = _app_state(
        setup="state.zigbeePermitUntil = new Date(Date.now() + 60000).toISOString();"
        "const open = state.zigbeeCountdown();"
        "state.zigbeePermitUntil = null;"
        "const closed = state.zigbeeCountdown();"
        "state.zigbeePermitUntil = new Date(Date.now() - 5000).toISOString();"
        "const past = state.zigbeeCountdown();"
        "console.log(JSON.stringify({ left: open, closed, past }));"
    )
    assert 55 <= values["left"] <= 60
    assert values["closed"] == 0
    assert values["past"] == 0


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_window_is_not_open_before_the_user_asks(api):
    """The tab opens on reset guidance and one button. ZHA opens the network
    as the page loads and burns the window while the user is still reading
    how to reset their device.

    Runs the REAL tab switch with `request` recording every call, and then
    presses Start - because a test that only asserted "no permit on open"
    would also pass for a tab whose button never opens the window at all,
    which is the same screen from the user's side and the opposite bug.

    The duration is the server's own `PERMIT_MAX_SECONDS`, imported rather
    than retyped: `ZigbeePermitIn` bounds the field at exactly that value
    and 422s anything above it, so a page and a schema that disagreed would
    fail as a validation error nobody would read as "the maximum moved".

    Fault to prove it: call the permit route from the tab's init."""
    values = _app_state(
        setup="""
        const calls = [];
        state.request = async (method, path, body) => {
          calls.push([method, path, body ?? null]);
          if (path === "/api/zigbee/pairing") {
            return { permit_until: null, rows: [] };
          }
          if (path === "/api/zigbee/permit") {
            return { permit_until: "2026-09-12T20:04:14+00:00" };
          }
          throw new Error("unexpected " + method + " " + path);
        };
        state.zigbee = { configured_path: "/dev/serial/by-id/a" };
        (async () => {
          await state.selectCommissionTab("zigbee");
          const onOpen = { calls: calls.slice(), permitUntil: state.zigbeePermitUntil };
          await state.startZigbeeSearch();
          console.log(JSON.stringify({
            onOpen,
            afterStart: calls,
            permitUntilAfterStart: state.zigbeePermitUntil,
          }));
        })();
        """
    )

    # Opening the tab reads the list and does nothing else. `permit_until`
    # comes back null from that read, which is also exactly what the route
    # reports for a window that has closed - so the tab never has to guess
    # which of the two it is looking at.
    assert [call[:2] for call in values["onOpen"]["calls"]] == [["GET", "/api/zigbee/pairing"]]
    assert values["onOpen"]["permitUntil"] is None
    # And the button does open it, at the protocol maximum the schema
    # allows - the other half of the fault, and the one a "no request on
    # open" assertion alone cannot see.
    assert ["POST", "/api/zigbee/permit", {"duration": PERMIT_MAX_SECONDS}] in values["afterStart"]
    assert values["permitUntilAfterStart"] == "2026-09-12T20:04:14+00:00"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
@pytest.mark.parametrize(
    "state_name",
    ["joined", "interviewing", "configuring", "ready", "failed", "stuck", "waiting_wake"],
)
async def test_every_row_state_renders_its_own_text(api, state_name):
    """The seven states of design 3.1. The last two - stuck and waiting to
    wake - are the ones ZHA does not have, and they are the reason this tab
    is worth building rather than copying.

    Three of the seven are not stored on `PairingRow` at all: `configuring`
    and `waiting_wake` are overlaid per request by `api/zigbee.py`'s
    `_row_status`, `stuck` is an age computed by `_stuck`. They arrive in
    the same `state` field as the other four and the page may not treat
    them as a lesser kind - which is exactly what collapsing one into a
    neighbour does.

    Run twice, on purpose. WITHOUT a translation table `t()` returns the
    key it was handed (`_app_state`'s own docstring), so the first run is
    the assertion on the KEY: this state reads its own string and no
    other's. WITH the real table - `_web_strings()`, what `GET /api/i18n`
    actually sends the browser - the second run proves the key resolves to
    a real sentence and that the seven sentences are seven, not six.
    Neither run contains an English string typed into this file.

    The seven are rendered on a row already in the device list. A ready row
    NOT added yet has an eighth sentence of its own
    (`web.zigbee.state_ready_to_add`): "Ready to use" in green read as
    finished there, and the device never reached Loxone because nobody
    pressed Add. It is checked on every run to be a different sentence from
    all seven, so it cannot quietly become one of them.

    Fault to prove it: collapse `stuck` into `failed`. A battery device that
    simply fell asleep is then presented as a broken one, and the user
    removes it. For the eighth: let an unadopted ready row read
    `state_ready`."""
    from loxmatter import i18n

    states = [
        "joined",
        "interviewing",
        "configuring",
        "ready",
        "failed",
        "stuck",
        "waiting_wake",
    ]
    key = f"web.zigbee.state_{state_name}"
    # Both languages, from the shipped table. A state whose English
    # sentence exists and whose German one does not is a German user
    # reading a dotted key inside a German frame.
    assert set(i18n._STRINGS[key]) >= {"en", "de"}
    assert set(i18n._STRINGS["web.zigbee.state_ready_to_add"]) >= {"en", "de"}

    row_js = (
        "{{ ieee: '00:12:4b:00:24:c2:1a:7e', state: {name!r}, manufacturer: 'IKEA of Sweden',"
        " model: 'TRADFRI bulb E27', quirk_applied: true, discovered: true,"
        " changed_at: '2026-09-12T20:00:00+00:00',"
        " suggested_name: 'IKEA of Sweden TRADFRI bulb E27',"
        " device_id: {device_id}, name: null, room: null }}"
    )
    calls = ", ".join(
        f"{name}: state.zigbeeRowState({row_js.format(name=name, device_id=7)})" for name in states
    )
    to_add = f"state.zigbeeRowState({row_js.format(name='ready', device_id='null')})"
    setup = "console.log(JSON.stringify({" + calls + ", ready_to_add: " + to_add + "}));"
    keys = _app_state(setup=setup)
    texts = _app_state(setup=setup, translations=_web_strings())

    assert keys[state_name] == key
    assert keys["ready_to_add"] == "web.zigbee.state_ready_to_add"
    seven = {name: text for name, text in texts.items() if name != "ready_to_add"}
    assert texts["ready_to_add"] not in seven.values(), texts
    assert "{" not in texts["ready_to_add"], texts["ready_to_add"]
    # A `{placeholder}` still standing means the row field it names is
    # spelled differently on the wire than in strings.yaml - the sentence
    # renders, and says "Found {model}".
    assert "{" not in texts[state_name], texts[state_name]
    others = {name: text for name, text in seven.items() if name != state_name}
    assert texts[state_name] not in others.values(), (state_name, texts)


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_stuck_row_still_offers_retry_and_remove_and_keeps_waiting(api):
    """A stuck row is NOT an error row.

    `stuck` is not a state the source stores. `api/zigbee.py`'s `_stuck`
    computes it from `changed_at` against `is_mains_powered` - 60 s for a
    mains device, 90 s for a battery one - and sends it down in `state`
    like any other, so the page sees an ordinary row and has to keep
    treating it as one. The usual cause is a battery device that fell
    asleep and the usual fix is pressing its button; a row presented as a
    failure is how a user comes to remove a device that was about to
    finish.

    The two `x-show` expressions are pulled out of the SERVED markup and
    evaluated through `with (state)`, which is the scope Alpine gives them
    itself: a retyped copy would only prove it agrees with itself, and a
    substring check on the markup cannot fail for a condition that is
    merely wrong. `row` is a parameter of that function rather than a
    property of `state`, so nothing on the component shadows it.

    Retry has to be hidden SOMEWHERE, or `x-show="true"` would satisfy
    every other assertion here. A ready row is that somewhere: it has
    nothing to re-interview and shows the name and room fields instead
    (design 3.1 offers Retry on the failed row only).

    "Keeps waiting" is measured on the text: a stuck row still says what
    design 3.1 says it says - press the device's button - rather than the
    failed row's "could not read this device". That overlaps
    `test_every_row_state_renders_its_own_text` deliberately. Hiding the
    actions and relabelling the row are the same misreading of what stuck
    means, and they are normally committed in the same edit.

    Fault to prove it: hide the actions while stuck."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)

    def action_show(call: str) -> str:
        at = markup.index(call)
        tag = markup[markup.rindex("<button", 0, at) : markup.index(">", at)]
        match = re.search(r'x-show="([^"]*)"', tag)
        assert match, f"the {call} button must be gated by an x-show: {tag}"
        return match.group(1)

    retry_expr = action_show("retryZigbeeDevice(")
    remove_expr = action_show("removeZigbeeDevice(")
    evaluator = f"with (state) {{ return [Boolean({retry_expr}), Boolean({remove_expr})]; }}"

    values = _app_state(
        setup="const shown = new Function('state', 'row', " + json.dumps(evaluator) + ");\n"
        "const out = {};\n"
        "for (const name of ['joined', 'interviewing', 'configuring', 'ready',\n"
        "                    'failed', 'stuck', 'waiting_wake']) {\n"
        "  out[name] = shown(state, { ieee: '00:12:4b:00:24:c2:1a:7e', state: name,\n"
        "    manufacturer: 'IKEA of Sweden', model: 'TRADFRI bulb E27',\n"
        "    quirk_applied: true, discovered: true,\n"
        "    changed_at: '2026-09-12T20:00:00+00:00',\n"
        "    suggested_name: 'IKEA of Sweden TRADFRI bulb E27',\n"
        "    device_id: null, name: null, room: null });\n"
        "}\n"
        "out.stuckText = state.zigbeeRowState({ state: 'stuck' });\n"
        "out.failedText = state.zigbeeRowState({ state: 'failed' });\n"
        "console.log(JSON.stringify(out));",
        translations=_web_strings(),
    )

    # Both actions, on a stuck row, exactly as on the failed row the
    # design names them for.
    assert values["stuck"] == [True, True]
    assert values["failed"] == [True, True]
    # And not everywhere - otherwise the two lines above prove nothing.
    assert values["ready"][0] is False
    # Keeps waiting rather than reporting a failure.
    assert values["stuckText"] and values["failedText"]
    assert values["stuckText"] != values["failedText"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_leaving_the_tab_closes_the_join_window(api):
    """ZHA never closes its window when the page is left, and that is a
    standing complaint - an open Zigbee network is one any passing device
    can join.

    Closing is `permit(0)` on the same route: `ZigbeePermitIn` allows a
    duration of 0 and documents it as Stop, so this needs no second
    endpoint and no second piece of server state.

    It is sent ONLY when a window is actually open. `permit_until` is null
    whenever it is not - after a Stop, after the duration elapsed, and
    after the radio went away, which closes the window on the source's side
    without being asked (`connection_lost` and `disconnect()` both do it,
    while the rows stay listed). A tab change that sent `permit(0)`
    unconditionally would aim a request at a bridge with no source - mid
    swap, or with the setting just cleared - and collect the 503 of
    `_require_source`: an error banner for doing nothing wrong, on the very
    click that was supposed to be tidy. (A Stop after a lost link is a 200
    with `permit_until: null` on the server's side; the 503 is what is
    left.)

    And only a window THIS page opened: the window here is opened with the
    tab's own Start, which is what makes it this page's to close
    (`test_only_a_window_opened_here_is_closed_on_leaving` covers a window
    seen only through the poll).

    Fault to prove it: leave the window open on tab change."""
    values = _app_state(
        setup="""
        let calls = [];
        state.request = async (method, path, body) => {
          calls.push([method, path, body ?? null]);
          if (path === "/api/zigbee/pairing") return { permit_until: null, rows: [] };
          if (path === "/api/zigbee/permit") {
            return { permit_until: body.duration > 0 ? new Date(Date.now() + 60000).toISOString() : null };
          }
          throw new Error("unexpected " + method + " " + path);
        };
        state.zigbee = { configured_path: "/dev/serial/by-id/a" };
        (async () => {
          // A window opened with the tab's own Start, and the user presses
          // Matter.
          state.commissionTab = "zigbee";
          await state.startZigbeeSearch();
          calls = [];
          await state.selectCommissionTab("matter");
          const closing = { calls, permitUntil: state.zigbeePermitUntil };

          // And again with nothing open - the radio went away and the
          // source closed the window without being asked. `calls` is
          // REBOUND rather than emptied, so `closing.calls` above keeps
          // the array it captured.
          calls = [];
          state.commissionTab = "zigbee";
          state.zigbeePermitUntil = null;
          await state.selectCommissionTab("matter");

          console.log(JSON.stringify({ closing, quiet: calls }));
        })();
        """
    )

    closing_permits = [call for call in values["closing"]["calls"] if call[1].endswith("/permit")]
    assert closing_permits == [["POST", "/api/zigbee/permit", {"duration": 0}]]
    assert values["closing"]["permitUntil"] is None
    # Nothing open, nothing sent.
    assert [call for call in values["quiet"] if call[1].endswith("/permit")] == []


async def test_the_matter_tab_keeps_the_card_it_had(api):
    """The Matter half must be the same card, not a rebuilt one: the code
    field, its detection chip, the sticker illustration and both
    disclosures.

    Fault to prove it: drop the sticker `<svg>` while restructuring."""
    client, _, _ = api
    page = (await client.get("/")).text
    for marker in ('id="commission-code"', "code-sticker", "commission-disclosure"):
        assert marker in page


def _pairing_element(markup: str, tag: str, **wanted: str) -> dict[str, str]:
    """The one `tag` inside the Zigbee tab whose attributes include every
    `wanted` pair, spelled the way `_zigbee_row_element` spells them."""

    def spelled(name: str) -> str:
        if name.startswith("at_"):
            return "@" + name[3:].replace("_", "-")
        if name.startswith("colon_"):
            return ":" + name[6:].replace("_", "-")
        return name.rstrip("_").replace("_", "-")

    matches = [
        attributes
        for element_tag, attributes, ancestors in _served_elements(markup)
        if element_tag == tag
        and any("zigbee-pairing" in ancestor.get("class", "").split() for _, ancestor in ancestors)
        and all(attributes.get(spelled(name)) == value for name, value in wanted.items())
    ]
    assert len(matches) == 1, (
        f"expected one <{tag}> {wanted} in the Zigbee tab, found {len(matches)}"
    )
    return matches[0]


# A `GET /api/zigbee/pairing` body: one device already adopted as "Desk lamp"
# and one ready device nobody has named yet.
_PAIRING_BODY = {
    "permit_until": None,
    "rows": [
        {
            "ieee": "00:12:4b:00:24:c2:1a:7e",
            "state": "ready",
            "manufacturer": "IKEA of Sweden",
            "model": "TRADFRI bulb E27",
            "quirk_applied": True,
            "discovered": False,
            "changed_at": "2026-09-12T20:00:00+00:00",
            "suggested_name": "IKEA of Sweden TRADFRI bulb E27",
            "device_id": 7,
            "name": "Desk lamp",
            "room": None,
        },
        {
            "ieee": "00:15:8d:00:07:77:88:07",
            "state": "waiting_wake",
            "manufacturer": "Aqara",
            "model": "Motion sensor P1",
            "quirk_applied": False,
            "discovered": False,
            "changed_at": "2026-09-12T20:01:00+00:00",
            "suggested_name": "Aqara Motion sensor P1",
            "device_id": None,
            "name": None,
            "room": None,
        },
    ],
}
_ADOPTED = "00:12:4b:00:24:c2:1a:7e"
_UNNAMED = "00:15:8d:00:07:77:88:07"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_poll_does_not_overwrite_a_name_being_typed(api):
    """The list is polled every two seconds while a device is being set up,
    and the name field is filled from it. A poll that wrote the stored name
    back into the field took the word being typed away mid-keystroke - the
    same bug as a poll snapping a select back under the user's hand.

    The SERVED `x-model` (as the assignment Alpine makes) and `@input` of
    the name field, then real polls, for an adopted row and for one not
    added yet - whose typed name has nowhere to be saved until "Add to
    devices" is pressed, so it must survive any number of polls.

    Fault to prove it: sync the draft from every GET regardless of
    `nameDirty` in `syncZigbeeDrafts()`."""
    client, _, _ = api
    page = (await client.get("/")).text
    name_input = _pairing_element(page, "textarea", x_model="zigbeeRowDrafts[row.ieee].name")
    values = _app_state(
        _BINDINGS_JS + f"const body = {json.dumps(_PAIRING_BODY)};"
        "state.request = async () => JSON.parse(JSON.stringify(body));"
        "(async () => {"
        "  await state.loadZigbeePairing();"
        "  const out = {};"
        f"  for (const ieee of [{json.dumps(_ADOPTED)}, {json.dumps(_UNNAMED)}]) {{"
        "    const row = state.zigbeeRow(ieee);"
        f"    exec({json.dumps(name_input['x-model'])} + ' = __value', {{ row, __value: 'Hall li' }});"
        f"    exec({json.dumps(name_input['@input'])}, {{ row }});"
        "  }"
        "  await state.loadZigbeePairing();"
        "  await state.loadZigbeePairing();"
        f"  out.adopted = state.zigbeeRowDrafts[{json.dumps(_ADOPTED)}].name;"
        f"  out.unnamed = state.zigbeeRowDrafts[{json.dumps(_UNNAMED)}].name;"
        "  console.log(JSON.stringify(out));"
        "})();"
    )
    assert values == {"adopted": "Hall li", "unnamed": "Hall li"}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_name_is_saved_on_blur_only_when_it_changed_and_a_refusal_stays_on_the_row(api):
    """Saved on blur, which means a blur happens every time the user tabs
    through the row - and a PATCH for each of those would re-register a
    device nobody changed. Only a changed name is sent. A refused save (the
    409 of a row that went back to interviewing while the name was being
    typed) is shown on that row, and the next poll does not wipe it or the
    name that was refused.

    The SERVED `@input` and `@blur` of the name field.

    Fault to prove it: drop the `name === row.name` early return from
    `saveZigbeeName()`, or clear `zigbeeRowErrors` in `loadZigbeePairing()`."""
    client, _, _ = api
    page = (await client.get("/")).text
    name_input = _pairing_element(page, "textarea", x_model="zigbeeRowDrafts[row.ieee].name")
    values = _app_state(
        _BINDINGS_JS + f"const body = {json.dumps(_PAIRING_BODY)};"
        "const sent = [];"
        "let refuse = false;"
        "state.request = async (method, path, payload) => {"
        "  if (method === 'PATCH') {"
        "    sent.push(payload);"
        "    if (refuse) { const error = new Error('This device is not ready yet.'); error.status = 409; throw error; }"
        "    return { ...body.rows[0], name: payload.name };"
        "  }"
        "  if (path === '/api/devices') return [];"
        "  if (path === '/api/zigbee/pairing') return JSON.parse(JSON.stringify(body));"
        "  return [];"
        "};"
        "(async () => {"
        "  await state.loadZigbeePairing();"
        f"  const row = () => state.zigbeeRow({json.dumps(_ADOPTED)});"
        f"  const blur = () => run({json.dumps(name_input['@blur'])}, {{ row: row() }});"
        "  const type = (value) => {"
        f"    exec({json.dumps(name_input['x-model'])} + ' = __value', {{ row: row(), __value: value }});"
        f"    exec({json.dumps(name_input['@input'])}, {{ row: row() }});"
        "  };"
        "  await blur();"
        "  const untouched = sent.length;"
        "  type('Desk lamp'); await blur();"
        "  const retypedSame = sent.length;"
        "  type('Reading lamp'); await blur();"
        "  const changed = sent.slice();"
        "  refuse = true;"
        "  type('Hall light'); await blur();"
        "  await state.loadZigbeePairing();"
        "  console.log(JSON.stringify({ untouched, retypedSame, changed,"
        "    error: state.zigbeeRowError(row()),"
        f"    draft: state.zigbeeRowDrafts[{json.dumps(_ADOPTED)}].name }}));"
        "})();"
    )
    assert values["untouched"] == 0
    assert values["retypedSame"] == 0
    assert values["changed"] == [{"name": "Reading lamp"}]
    assert values["error"] == "This device is not ready yet."
    assert values["draft"] == "Hall light"


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_an_added_zigbee_device_reaches_the_device_list():
    """ "Add to devices" registers the device on the server - and the device
    list in this page reloads only on commissioning, removal and a
    reconnect. Without a reload of its own the new tile appeared on the
    next page load, which reads as "adding did nothing".

    The REAL `adoptZigbeeDevice()`: the PATCH carries the prefilled name
    and the chosen room, and `GET /api/devices` follows it, with the new
    device's controls and signals.

    Fault to prove it: drop the `refreshAdoptedZigbeeDevice()` call from
    `adoptZigbeeDevice()`."""
    values = _app_state(
        _BINDINGS_JS + f"const body = {json.dumps(_PAIRING_BODY)};"
        "const asked = [];"
        "state.request = async (method, path, payload) => {"
        "  asked.push([method, path, payload ?? null]);"
        f"  if (method === 'PATCH') return {{ ...body.rows[1], device_id: 9, name: payload.name, room: payload.room ?? null }};"
        "  if (path === '/api/zigbee/pairing') return JSON.parse(JSON.stringify(body));"
        "  if (path === '/api/devices') return [{ id: 9, label: 'Aqara Motion sensor P1', room: 'Hall' }];"
        "  return [];"
        "};"
        "(async () => {"
        "  await state.loadZigbeePairing();"
        f"  state.zigbeeRowDrafts[{json.dumps(_UNNAMED)}].room = 'Hall';"
        f"  await state.adoptZigbeeDevice({json.dumps(_UNNAMED)});"
        "  console.log(JSON.stringify({ asked: asked.filter(([m, p]) => p !== '/api/zigbee/pairing'),"
        "    devices: state.devices.map((d) => d.id) }));"
        "})();"
    )
    requests = [call[:2] for call in values["asked"]]
    assert values["asked"][0] == [
        "PATCH",
        f"/api/zigbee/pairing/{_UNNAMED.replace(':', '%3A')}",
        {"name": "Aqara Motion sensor P1", "room": "Hall"},
    ]
    assert requests.index(["GET", "/api/devices"]) > 0
    assert ["GET", "/api/devices/9/controls"] in requests
    assert ["GET", "/api/devices/9/signals"] in requests
    assert values["devices"] == [9]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_leaving_the_devices_view_closes_the_join_window_and_stops_the_poll():
    """The Matter tab is one way to leave the Zigbee tab; leaving the whole
    Devices view is the other, and the more common one. Through the REAL
    `selectView('export')`: an open window is closed with `permit(0)`, the
    armed poll is cleared, and a list load that was already in flight when
    the view changed arms no new timer when it finishes.

    Fault to prove it: drop the `if (view !== "devices")` block from
    `selectView()`, or the on-screen guard in `scheduleZigbeePairingLoad()`."""
    values = _app_state(
        "globalThis.window = { location: { hash: '' }, history: { replaceState() {} } };"
        "let armed = 0; let cleared = 0;"
        "globalThis.setTimeout = () => { armed += 1; return armed; };"
        "globalThis.clearTimeout = () => { cleared += 1; };"
        "const calls = [];"
        f"const body = {json.dumps(_PAIRING_BODY)};"
        "let release;"
        "state.request = async (method, path, payload) => {"
        "  calls.push([method, path, payload ?? null]);"
        "  if (path === '/api/zigbee/pairing') {"
        "    if (release === undefined) return JSON.parse(JSON.stringify(body));"
        "    await new Promise((resolve) => { release = resolve; });"
        "    return JSON.parse(JSON.stringify(body));"
        "  }"
        "  if (path === '/api/zigbee/permit') return { permit_until: payload.duration > 0"
        "    ? new Date(Date.now() + 60000).toISOString() : null };"
        "  return [];"
        "};"
        "state.authenticated = true; state.view = 'devices';"
        "state.zigbee = { configured_path: '/dev/serial/by-id/a' };"
        "(async () => {"
        "  await state.selectCommissionTab('zigbee');"
        "  const armedOnScreen = state.zigbeePairingTimer !== null;"
        "  await state.startZigbeeSearch();"
        "  calls.length = 0;"
        "  release = null;"
        "  const inFlight = state.loadZigbeePairing();"
        "  await new Promise((resolve) => setImmediate(resolve));"
        "  const armedBefore = armed;"
        "  await state.selectView('export');"
        "  release();"
        "  await inFlight;"
        "  console.log(JSON.stringify({ armedOnScreen,"
        "    permits: calls.filter(([, p]) => p === '/api/zigbee/permit'),"
        "    timer: state.zigbeePairingTimer, armedAfterLeaving: armed - armedBefore }));"
        "})();"
    )
    assert values["armedOnScreen"] is True
    assert values["permits"] == [["POST", "/api/zigbee/permit", {"duration": 0}]]
    assert values["timer"] is None
    assert values["armedAfterLeaving"] == 0


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_pairing_buttons_are_enabled_the_way_alpine_binds_them(api):
    """Every `:disabled` in the Zigbee tab, evaluated through the served
    markup and Alpine's own coercion (`boundTrue`): an expression that
    reads `undefined` through a dot binds as `""`, which SETS the attribute.
    That is how "No Thread stick" once became unselectable with every test
    green, and a per-row lookup such as `zigbeeRowBusy[row.ieee]` is exactly
    that shape for every row nothing is running on.

    Idle: every button enabled. A 503 from the list (`radio_changing`):
    Start and every row button disabled, because each of them could only
    answer 503 too - and the tab and its detail stay.

    Fault to prove it: bind a row button's `:disabled` to
    `zigbeeRowBusy[row.ieee]`, or drop `zigbeePairingUnavailable` from
    `zigbeeRowLocked()`."""
    client, _, _ = api
    page = (await client.get("/")).text
    buttons = [
        attributes
        for tag, attributes, ancestors in _served_elements(page)
        if tag == "button"
        and any("zigbee-pairing" in ancestor.get("class", "").split() for _, ancestor in ancestors)
    ]
    handlers = sorted(button.get("@click", "") for button in buttons)
    assert handlers == sorted(
        [
            "startZigbeeSearch()",
            "stopZigbeeSearch()",
            "extendZigbeeSearch()",
            "adoptZigbeeDevice(row.ieee)",
            "retryZigbeeDevice(row.ieee)",
            "removeZigbeeDevice(row.ieee)",
        ]
    )
    expressions = {button["@click"]: button.get(":disabled", "false") for button in buttons}
    values = _app_state(
        _BINDINGS_JS + f"const body = {json.dumps(_PAIRING_BODY)};"
        f"const expressions = {json.dumps(expressions)};"
        "let fail = false;"
        "state.request = async () => {"
        "  if (fail) {"
        "    const error = new Error('The Zigbee radio is being changed.'); error.status = 503; throw error;"
        "  }"
        "  return JSON.parse(JSON.stringify(body));"
        "};"
        "state.zigbee = { configured_path: '/dev/serial/by-id/a' };"
        "state.authenticated = true;"
        "const disabled = () => Object.fromEntries(Object.entries(expressions).map(([click, expr]) =>"
        f"  [click, boundTrue(expr, {{ row: state.zigbeeRow({json.dumps(_ADOPTED)}) }})]));"
        "(async () => {"
        "  await state.loadZigbeePairing();"
        "  const idle = disabled();"
        "  fail = true;"
        "  state.loadZigbeeRadio = async () => {};"
        "  await state.loadZigbeePairing();"
        "  console.log(JSON.stringify({ idle, swapping: disabled(),"
        "    error: state.zigbeePairingError, tab: state.zigbeeTabVisible() }));"
        "})();"
    )
    assert set(values["idle"].values()) == {False}, values["idle"]
    swapping = values["swapping"]
    assert swapping["startZigbeeSearch()"] is True
    for click in (
        "adoptZigbeeDevice(row.ieee)",
        "retryZigbeeDevice(row.ieee)",
        "removeZigbeeDevice(row.ieee)",
    ):
        assert swapping[click] is True, click
    assert values["error"] == "The Zigbee radio is being changed."
    assert values["tab"] is True


# ---------------------------------------------------------------------------
# The Zigbee tab, after review: who closes a window, what a reload shows, and
# the brief's own rules measured through the served bindings.
# ---------------------------------------------------------------------------

# A page logged in on the Devices view with a stick configured and the Zigbee
# tab selected. `window` carries what `selectView()` writes the view into and
# what `removeZigbeeDevice()` confirms with.
_PAIRING_PAGE_JS = (
    "globalThis.window = { location: { hash: '' }, history: { replaceState() {} },"
    "  confirm: () => true };"
    "state.authenticated = true; state.view = 'devices';"
    "state.zigbee = { configured_path: '/dev/serial/by-id/a' };"
    "state.commissionTab = 'zigbee';"
    "const future = () => new Date(Date.now() + 254000).toISOString();"
    "const settle = () => new Promise((resolve) => setImmediate(resolve));"
    "const radioBody = { configured_path: '/dev/serial/by-id/a', serial: [],"
    "  configured_device_present: true,"
    "  progress: { state: 'connected', attempts: 0, error: null, changed_at: 'x' } };"
)


def _pairing_row_js(state: str, device_id: object) -> str:
    """One `GET /api/zigbee/pairing` row as JS, in the route's own shape."""
    return (
        "{ ieee: '00:12:4b:00:24:c2:1a:7e', state: "
        + json.dumps(state)
        + ", manufacturer: 'IKEA of Sweden', model: 'TRADFRI bulb E27',"
        " quirk_applied: true, discovered: true, changed_at: '2026-09-12T20:00:00+00:00',"
        " suggested_name: 'IKEA of Sweden TRADFRI bulb E27', device_id: "
        + json.dumps(device_id)
        + ", name: null, room: null }"
    )


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_leaving_while_start_is_under_way_closes_the_window_it_opens():
    """Start pressed, and the tab left before its answer arrived. Leaving
    found no window to close - `zigbeePermitUntil` is only written once the
    POST answers - and the answer then opened the network for 254 s behind
    a view showing nothing. Measured in node for both ways of leaving: only
    `{duration: 254}` was ever sent.

    Through the REAL `startZigbeeSearch()`, with its POST held open while
    the REAL `selectCommissionTab('matter')` or `selectView('export')` runs.
    The window it opens is closed again the moment the answer lands off
    screen. And the other side of it: a user who left and came BACK while
    the POST was under way is looking at the tab again, and keeps the window
    they asked for.

    Fault to prove it: drop the off-screen check after the await in
    `sendZigbeePermit()`."""
    values = _app_state(
        _BINDINGS_JS + _PAIRING_PAGE_JS + "const runCase = async (leave) => {"
        "  const permits = []; let answer;"
        "  state.commissionTab = 'zigbee'; state.view = 'devices';"
        "  state.zigbeePermitUntil = null; state.setZigbeeOpenedHere(false);"
        "  state.request = async (method, path, payload) => {"
        "    if (path === '/api/zigbee/permit') {"
        "      permits.push(payload);"
        "      if (payload.duration > 0) await new Promise((resolve) => { answer = resolve; });"
        "      return { permit_until: payload.duration > 0 ? future() : null };"
        "    }"
        "    if (path === '/api/zigbee/pairing') return { permit_until: null, rows: [] };"
        "    if (path === '/api/zigbee/radio') return radioBody;"
        "    return [];"
        "  };"
        "  const start = state.startZigbeeSearch();"
        "  await settle();"
        "  await leave();"
        "  answer();"
        "  await start;"
        "  return { permits, open: state.zigbeeWindowOpen(), openedHere: state.zigbeeOpenedHere };"
        "};"
        "(async () => {"
        "  const matter = await runCase(() => state.selectCommissionTab('matter'));"
        "  const exportView = await runCase(() => state.selectView('export'));"
        "  const cameBack = await runCase(async () => {"
        "    await state.selectCommissionTab('matter');"
        "    await state.selectCommissionTab('zigbee');"
        "  });"
        "  console.log(JSON.stringify({ matter, exportView, cameBack }));"
        "})();"
    )
    opened_then_closed = [{"duration": PERMIT_MAX_SECONDS}, {"duration": 0}]
    for case in ("matter", "exportView"):
        assert values[case]["permits"] == opened_then_closed, (case, values[case])
        assert values[case]["open"] is False, case
        assert values[case]["openedHere"] is False, case
    assert values["cameBack"]["permits"] == [{"duration": PERMIT_MAX_SECONDS}]
    assert values["cameBack"]["open"] is True


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_only_a_window_opened_here_is_closed_on_leaving():
    """A phone opens the window; the laptop's two-second poll sees it; the
    laptop's user clicks Export - and the laptop's Stop closed the window
    under the phone. A page closes, on leaving, only a window it opened.

    Three runs through the REAL tab switch and view change:

    - Opened here (Start), then Export: closed.
    - Opened by someone else, seen only through `GET /api/zigbee/pairing`:
      entering Devices lands on its countdown, and neither the Matter tab
      nor Export sends anything.
    - Opened here, then closed elsewhere (a GET shows `permit_until: null`),
      then opened again by the phone: the page's claim ended with its
      window, so leaving sends nothing either.

    Fault to prove it: drop the `zigbeeOpenedHere` check from
    `closeZigbeeWindow()` (the second run sends Stops), or do not clear the
    flag on a GET without a window (the third one does)."""
    values = _app_state(
        _BINDINGS_JS + _PAIRING_PAGE_JS + "const permits = [];"
        "let pairing = { permit_until: null, rows: [] };"
        "state.request = async (method, path, payload) => {"
        "  if (path === '/api/zigbee/permit') {"
        "    permits.push(payload);"
        "    return { permit_until: payload.duration > 0 ? future() : null };"
        "  }"
        "  if (path === '/api/zigbee/pairing') return JSON.parse(JSON.stringify(pairing));"
        "  if (path === '/api/zigbee/radio') return radioBody;"
        "  return [];"
        "};"
        "(async () => {"
        "  await state.startZigbeeSearch();"
        "  await state.selectView('export');"
        "  const openedHere = permits.splice(0);"
        "  state.commissionTab = 'matter';"
        "  pairing = { permit_until: future(), rows: [] };"
        "  await state.selectView('devices');"
        "  const landed = state.commissionTabShown();"
        "  await state.selectCommissionTab('matter');"
        "  await state.selectCommissionTab('zigbee');"
        "  await state.selectView('export');"
        "  const seenOnly = permits.splice(0);"
        "  pairing = { permit_until: null, rows: [] };"
        "  await state.selectView('devices');"
        "  await state.startZigbeeSearch();"
        "  await state.loadZigbeePairing();"
        "  pairing = { permit_until: future(), rows: [] };"
        "  await state.loadZigbeePairing();"
        "  await state.selectView('export');"
        "  const claimEnded = permits.splice(0);"
        "  console.log(JSON.stringify({ openedHere, landed, seenOnly, claimEnded }));"
        "})();"
    )
    assert values["openedHere"] == [{"duration": PERMIT_MAX_SECONDS}, {"duration": 0}]
    assert values["landed"] == "zigbee"
    assert values["seenOnly"] == []
    assert values["claimEnded"] == [{"duration": PERMIT_MAX_SECONDS}]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_reload_lands_on_an_open_windows_countdown_and_closes_nothing():
    """Design 3.1: the page counts down to the server's end time "so a
    reloaded page still shows the truth". A best-effort Stop on `pagehide`
    contradicted that - a reload closed the window - and closed a window
    another tab or a phone was watching as well. It is gone.

    What a reload now does instead, through the REAL `selectView('devices')`
    `startApp()` ends on: one pairing GET whichever tab is selected, and an
    open window selects the Zigbee tab so its countdown is on screen - with
    no permit request at all. A closed one leaves the Matter tab alone.

    The tab and "this page opened the window" survive the reload in
    `sessionStorage` (per browser tab, shared with no other tab or phone):
    a window opened here, then reloaded, is still this page's to close.

    Fault to prove it: drop the tab switch from `peekZigbeePairing()`, or
    the `rememberCommission()` call from `setZigbeeOpenedHere()`."""
    source = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "pagehide" not in source
    assert "keepalive" not in source
    values = _app_state(
        _BINDINGS_JS + _PAIRING_PAGE_JS + "const stored = {};"
        "window.sessionStorage = {"
        "  getItem: (key) => (key in stored ? stored[key] : null),"
        "  setItem: (key, value) => { stored[key] = String(value); },"
        "};"
        "const calls = [];"
        "let pairing = { permit_until: future(), rows: [] };"
        "state.request = async (method, path, payload) => {"
        "  calls.push([method, path]);"
        "  if (path === '/api/zigbee/permit') return { permit_until: payload.duration > 0 ? future() : null };"
        "  if (path === '/api/zigbee/pairing') return JSON.parse(JSON.stringify(pairing));"
        "  if (path === '/api/zigbee/radio') return radioBody;"
        "  return [];"
        "};"
        "(async () => {"
        "  state.restoreCommission();"
        "  const fresh = state.commissionTab;"
        "  await state.selectView('devices');"
        "  const open = { tab: state.commissionTabShown(), counting: state.zigbeeCountdown() > 0,"
        "    polling: state.zigbeePairingTimer !== null, calls: calls.splice(0) };"
        "  state.commissionTab = 'matter';"
        "  pairing = { permit_until: null, rows: [] };"
        "  await state.selectView('devices');"
        "  const closed = state.commissionTabShown();"
        "  await state.selectCommissionTab('zigbee');"
        "  await state.startZigbeeSearch();"
        "  state.commissionTab = 'matter'; state.zigbeeOpenedHere = false;"
        "  state.restoreCommission();"
        "  const reloaded = { tab: state.commissionTab, openedHere: state.zigbeeOpenedHere };"
        "  console.log(JSON.stringify({ fresh, open, closed, reloaded }));"
        "})();"
    )
    assert values["fresh"] == "matter"
    assert values["open"]["tab"] == "zigbee"
    assert values["open"]["counting"] is True
    assert values["open"]["polling"] is True
    assert not [call for call in values["open"]["calls"] if call[1] == "/api/zigbee/permit"]
    assert ["GET", "/api/zigbee/pairing"] in values["open"]["calls"]
    assert values["closed"] == "matter"
    assert values["reloaded"] == {"tab": "zigbee", "openedHere": True}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_every_row_stored_as_ready_offers_its_name_room_and_add(api):
    """The brief's rule, measured on the served bindings: a row DISPLAYED as
    `configuring` or `waiting_wake` is STORED as `ready`, the PATCH's 409
    does not apply to it, and it must be nameable. A sleeping sensor can
    show `waiting_wake` for days, and a field that appears only on `ready`
    refuses its name for all of that time.

    The `x-show` of the name/room fields and of the Add button, from the
    served markup, for all seven states, on a row not added yet and on one
    already added (which keeps its fields and loses Add).

    Fault to prove it: drop `waiting_wake` from `ZIGBEE_NAMEABLE_ROW_STATES`,
    or reduce the fields' `x-show` to `row.state === 'ready'`."""
    client, _, _ = api
    page = (await client.get("/")).text
    fields = _pairing_element(page, "div", class_="zigbee-row-fields")
    add = _pairing_element(page, "button", at_click="adoptZigbeeDevice(row.ieee)")
    states = ["joined", "interviewing", "configuring", "ready", "failed", "stuck", "waiting_wake"]
    rows = ", ".join(
        f"[{json.dumps(name)}, {json.dumps(adopted)}, {_pairing_row_js(name, 7 if adopted else None)}]"
        for name in states
        for adopted in (False, True)
    )
    values = _app_state(
        _BINDINGS_JS + f"const out = {{}};for (const [name, adopted, row] of [{rows}]) {{"
        "  out[name + (adopted ? ':added' : ':new')] = ["
        f"    Boolean(run({json.dumps(fields['x-show'])}, {{ row }})),"
        f"    Boolean(run({json.dumps(add['x-show'])}, {{ row }})),"
        "  ];"
        "}"
        "console.log(JSON.stringify(out));"
    )
    nameable = {"configuring", "ready", "waiting_wake"}
    for name in states:
        assert values[f"{name}:new"] == [name in nameable, name in nameable], name
        assert values[f"{name}:added"] == [name in nameable, False], name


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_entering_devices_loads_the_zigbee_gate():
    """The tab's gate is `configured_path` from `GET /api/zigbee/radio`,
    which the radios card loads only in Settings. Without a load of its own
    on entering Devices, the tab never appeared after a page load unless
    Settings had been visited first.

    Through the REAL `selectView('devices')` on a page that has loaded
    nothing: the radio GET is made, before the pairing GET it enables, and
    it arms no Settings poll.

    Fault to prove it: drop the `loadZigbeeRadio({ poll: false })` call from
    `selectView()`."""
    values = _app_state(
        _BINDINGS_JS + _PAIRING_PAGE_JS + "state.zigbee = null; state.commissionTab = 'matter';"
        "const calls = [];"
        "state.request = async (method, path) => {"
        "  calls.push([method, path]);"
        "  if (path === '/api/zigbee/radio') return radioBody;"
        "  if (path === '/api/zigbee/pairing') return { permit_until: null, rows: [] };"
        "  return [];"
        "};"
        "(async () => {"
        "  await state.selectView('devices');"
        "  console.log(JSON.stringify({ calls, tab: state.zigbeeTabVisible(),"
        "    settingsPoll: state.zigbeeTimer }));"
        "})();"
    )
    calls = values["calls"]
    assert ["GET", "/api/zigbee/radio"] in calls
    assert calls.index(["GET", "/api/zigbee/radio"]) < calls.index(["GET", "/api/zigbee/pairing"])
    assert values["tab"] is True
    assert values["settingsPoll"] is None


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_choosing_a_room_on_an_added_row_saves_it(api):
    """After Add, the room saves when the select changes - there is no
    other button for it. The served `x-model` (as the assignment Alpine
    makes) and `@change` of the row's room select, on the added row of
    `_PAIRING_BODY`.

    Fault to prove it: drop `saveZigbeeRoom(row.ieee)` from the select's
    `@change`."""
    client, _, _ = api
    page = (await client.get("/")).text
    select = _pairing_element(page, "select", x_model="zigbeeRowDrafts[row.ieee].room")
    values = _app_state(
        _BINDINGS_JS + _PAIRING_PAGE_JS + f"const body = {json.dumps(_PAIRING_BODY)};"
        "const patches = [];"
        "state.request = async (method, path, payload) => {"
        "  if (method === 'PATCH') { patches.push(payload); return { ...body.rows[0], room: payload.room }; }"
        "  if (path === '/api/zigbee/pairing') return JSON.parse(JSON.stringify(body));"
        "  return [];"
        "};"
        "(async () => {"
        "  await state.loadZigbeePairing();"
        f"  const row = state.zigbeeRow({json.dumps(_ADOPTED)});"
        f"  exec({json.dumps(select['x-model'])} + ' = __value', {{ row, __value: 'Kitchen' }});"
        f"  exec({json.dumps(select['@change'])}, {{ row }});"
        "  await settle();"
        "  console.log(JSON.stringify({ patches }));"
        "})();"
    )
    assert values["patches"] == [{"room": "Kitchen"}]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_countdown_line_reads_minutes_and_seconds(api):
    """ "Open for new devices: 252 s" had to be divided before it said
    whether there was time to walk to the lamp. The line reads `m:ss`.

    The formatter on its own - 0, 254 and 9 seconds, the three shapes that
    go wrong (no leading zero on the seconds, a missing minute) - and the
    SERVED `x-text` of the countdown line with the real table, on a window
    with 254 s left. `zigbeeCountdown()` itself stays in seconds; the brief's
    own test counts on that.

    Fault to prove it: bind the line to `zigbeeCountdown()` directly, or
    drop the `padStart` from `formatMinutesSeconds()`."""
    client, _, _ = api
    page = (await client.get("/")).text
    line = _pairing_element(page, "p", class_="zigbee-countdown")
    values = _app_state(
        _BINDINGS_JS + _PAIRING_PAGE_JS + "state.zigbeePermitUntil = future();"
        "console.log(JSON.stringify({"
        "  formatted: [0, 254, 9].map((seconds) => state.formatMinutesSeconds(seconds)),"
        f"  line: run({json.dumps(line['x-text'])}),"
        "  seconds: state.zigbeeCountdown(),"
        "}));",
        translations=_web_strings(),
    )
    assert values["formatted"] == ["0:00", "4:14", "0:09"]
    assert 253 <= values["seconds"] <= 254
    expected = _web_strings()["web.zigbee.countdown"]
    assert values["line"] in {
        expected.replace("{time}", "4:14"),
        expected.replace("{time}", "4:13"),
    }, values["line"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_window_buttons_send_stop_and_keep_open_and_take_turns_with_start(api):
    """The running window's two buttons, and the line they share with
    Start, through the served markup.

    - Stop sends `permit(0)`, Keep open sends the server's own maximum -
      each `@click` run as Alpine runs it, against a recording request.
    - Their `:disabled` binds false while nothing is being sent and true
      while a permit request is under way (`boundTrue`, Alpine's coercion).
    - The Start line and the Stop line take turns on `zigbeeWindowOpen()`:
      exactly one of the two shows, whichever way the window is.

    Fault to prove it: have Keep open call `stopZigbeeSearch()`, bind Stop's
    `:disabled` to `!zigbeePermitBusy`, or show the Stop line
    unconditionally."""
    client, _, _ = api
    page = (await client.get("/")).text
    stop = _pairing_element(page, "button", at_click="stopZigbeeSearch()")
    extend = _pairing_element(page, "button", at_click="extendZigbeeSearch()")
    lines = [
        attributes
        for tag, attributes, ancestors in _served_elements(page)
        if tag == "div" and "zigbee-window" in attributes.get("class", "").split()
    ]
    assert len(lines) == 2, lines
    values = _app_state(
        _BINDINGS_JS + _PAIRING_PAGE_JS + "const permits = [];"
        "state.request = async (method, path, payload) => {"
        "  if (path === '/api/zigbee/permit') { permits.push(payload);"
        "    return { permit_until: payload.duration > 0 ? future() : null }; }"
        "  return { permit_until: null, rows: [] };"
        "};"
        f"const lines = {json.dumps([line['x-show'] for line in lines])};"
        "const shown = () => lines.map((expr) => Boolean(run(expr)));"
        "(async () => {"
        "  const closedLines = shown();"
        "  state.zigbeePermitUntil = future();"
        "  const openLines = shown();"
        f"  const idle = [boundTrue({json.dumps(stop[':disabled'])}), boundTrue({json.dumps(extend[':disabled'])})];"
        "  state.zigbeePermitBusy = true;"
        f"  const busy = [boundTrue({json.dumps(stop[':disabled'])}), boundTrue({json.dumps(extend[':disabled'])})];"
        "  state.zigbeePermitBusy = false;"
        f"  await run({json.dumps(extend['@click'])});"
        "  const afterExtend = permits.splice(0);"
        f"  await run({json.dumps(stop['@click'])});"
        "  const afterStop = permits.splice(0);"
        "  console.log(JSON.stringify({ closedLines, openLines, idle, busy, afterExtend, afterStop }));"
        "})();"
    )
    assert sorted(values["closedLines"]) == [False, True]
    assert values["openLines"] == [not shown for shown in values["closedLines"]]
    assert values["idle"] == [False, False]
    assert values["busy"] == [True, True]
    assert values["afterExtend"] == [{"duration": PERMIT_MAX_SECONDS}]
    assert values["afterStop"] == [{"duration": 0}]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_tab_decides_which_pane_shows_and_rows_are_keyed_by_ieee(api):
    """Two structural rules of the brief, from the served markup.

    The Matter pane and the Zigbee pane are gated by the tab the card
    SHOWS (`commissionTabShown()`): exactly one of them is visible whichever
    tab is selected, and a Zigbee tab left selected after the stick was
    cleared shows the Matter pane rather than nothing.

    One row per device keyed by IEEE (design 3.1): the row template's `:key`
    is `row.ieee`. Keyed by anything that changes - the state, the position -
    Alpine rebuilds the row on every step of a join, and the name being
    typed in it is thrown away with the old element.

    Fault to prove it: key the rows by `row.state`, or gate the Matter pane
    on `commissionTab === 'matter'`."""
    client, _, _ = api
    page = (await client.get("/")).text
    elements = _served_elements(page)
    row_templates = [
        attributes
        for tag, attributes, _ in elements
        if tag == "template" and attributes.get("x-for", "").startswith("row in ")
    ]
    assert len(row_templates) == 1, row_templates
    assert row_templates[0][":key"] == "row.ieee"
    matter = [
        attributes
        for tag, attributes, ancestors in elements
        if tag == "div"
        and "commissionTab" in attributes.get("x-show", "")
        and "matter" in attributes.get("x-show", "")
    ]
    assert len(matter) == 1, matter
    zigbee = _pairing_element(page, "div", class_="zigbee-reset")
    pane = [
        attributes
        for tag, attributes, _ in elements
        if tag == "div" and attributes.get("class") == "zigbee-pairing"
    ]
    assert len(pane) == 1 and zigbee
    values = _app_state(
        _BINDINGS_JS + f"const panes = [{json.dumps(matter[0]['x-show'])}, "
        f"{json.dumps(pane[0]['x-show'])}];"
        "const shown = () => panes.map((expr) => Boolean(run(expr)));"
        "const out = {};"
        "state.zigbee = { configured_path: '/dev/serial/by-id/a' };"
        "state.commissionTab = 'matter'; out.matter = shown();"
        "state.commissionTab = 'zigbee'; out.zigbee = shown();"
        "state.zigbee = { configured_path: null }; out.stickCleared = shown();"
        "console.log(JSON.stringify(out));"
    )
    assert values["matter"] == [True, False]
    assert values["zigbee"] == [False, True]
    assert values["stickCleared"] == [True, False]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_add_sends_the_typed_name_once_however_often_enter_is_pressed(api):
    """Two rules of "Add", through the served name field and button.

    It sends the name TYPED, not the prefilled one - a user who replaced
    "Aqara Motion sensor P1" with "Hall motion" and pressed Add got the
    first.

    And it is refused while an action is already running on the row or the
    list answers 503. The button's `:disabled` said so, but Enter in the
    name field reached `adoptZigbeeDevice()` directly: two quick Enters sent
    two PATCHes while the button looked disabled, and during a radio swap
    Enter sent one the button would not have.

    Fault to prove it: drop the busy/unavailable guard from
    `adoptZigbeeDevice()`, or send `row.suggested_name` from it."""
    client, _, _ = api
    page = (await client.get("/")).text
    name = _pairing_element(page, "textarea", x_model="zigbeeRowDrafts[row.ieee].name")
    add = _pairing_element(page, "button", at_click="adoptZigbeeDevice(row.ieee)")
    values = _app_state(
        _BINDINGS_JS + _PAIRING_PAGE_JS + f"const body = {json.dumps(_PAIRING_BODY)};"
        "const patches = []; let answer;"
        "state.request = async (method, path, payload) => {"
        "  if (method === 'PATCH') {"
        "    patches.push(payload);"
        "    await new Promise((resolve) => { answer = resolve; });"
        "    return { ...body.rows[1], device_id: 9, name: payload.name };"
        "  }"
        "  if (path === '/api/zigbee/pairing') return JSON.parse(JSON.stringify(body));"
        "  return [];"
        "};"
        "const $el = { blur() {} };"
        "(async () => {"
        "  await state.loadZigbeePairing();"
        f"  const row = () => state.zigbeeRow({json.dumps(_UNNAMED)});"
        f"  exec({json.dumps(name['x-model'])} + ' = __value', {{ row: row(), __value: 'Hall motion' }});"
        f"  exec({json.dumps(name['@input'])}, {{ row: row() }});"
        f"  exec({json.dumps(name['@keydown.enter.prevent'])}, {{ row: row(), $el }});"
        f"  exec({json.dumps(name['@keydown.enter.prevent'])}, {{ row: row(), $el }});"
        f"  exec({json.dumps(add['@click'])}, {{ row: row() }});"
        "  const whileBusy = patches.slice();"
        "  answer(); await settle(); await settle();"
        "  state.zigbeePairing.rows[1].device_id = null;"
        "  state.zigbeePairingUnavailable = true;"
        f"  exec({json.dumps(name['@keydown.enter.prevent'])}, {{ row: row(), $el }});"
        "  const duringSwap = patches.length - whileBusy.length;"
        "  console.log(JSON.stringify({ whileBusy, duringSwap }));"
        "})();"
    )
    assert values["whileBusy"] == [{"name": "Hall motion"}]
    assert values["duringSwap"] == 0


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_late_poll_does_not_bring_a_removed_row_back():
    """Remove pressed while a poll was under way: the poll had been
    answered before the DELETE landed, still carried the row, and put it
    back on screen when it arrived - a device the user had just removed,
    with Remove on it again.

    Through the REAL `removeZigbeeDevice()` with a GET held open across the
    DELETE. The row stays gone, and the poll the dropped GET would have
    armed is armed anyway - a dropped GET schedules nothing.

    Fault to prove it: drop the `zigbeePairingSequence` bump from
    `removeZigbeeDevice()`."""
    values = _app_state(
        _BINDINGS_JS + _PAIRING_PAGE_JS + f"const body = {json.dumps(_PAIRING_BODY)};"
        "let hold = false; let release;"
        "state.request = async (method, path) => {"
        "  if (method === 'DELETE') return null;"
        "  if (path === '/api/zigbee/pairing') {"
        "    if (hold) await new Promise((resolve) => { release = resolve; });"
        "    return JSON.parse(JSON.stringify(body));"
        "  }"
        "  return [];"
        "};"
        "(async () => {"
        "  await state.loadZigbeePairing();"
        "  hold = true;"
        "  const late = state.loadZigbeePairing();"
        "  await settle();"
        f"  await state.removeZigbeeDevice({json.dumps(_UNNAMED)});"
        "  release(); await late;"
        "  console.log(JSON.stringify({"
        "    rows: state.zigbeePairing.rows.map((row) => row.ieee),"
        "    polling: state.zigbeePairingTimer !== null }));"
        "})();"
    )
    assert values["rows"] == [_ADOPTED]
    assert values["polling"] is True


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_refused_stop_on_leaving_is_shown_where_the_user_went(api):
    """Leaving the tab sends a Stop, and a radio can refuse it
    (`api.zigbee.close_failed`: "... so it may still be open"). That
    message landed inside the pane the user had just left - the one
    sentence saying the network may still be open, in the one place nobody
    was looking.

    It is shown in a banner above the main navigation, in every view, with
    a Stop button of its own; switching the user back to the tab instead
    would undo the navigation they just made, and cannot follow them to
    Export at all. Going back to the tab moves it beside that tab's Stop.

    The served banner's `x-show`, in the Export view after a refused Stop;
    the pane's own error stays empty; and the banner's button retries.

    Fault to prove it: put a leaving Stop's refusal into
    `zigbeePermitError`."""
    client, _, _ = api
    page = (await client.get("/")).text
    banners = [
        attributes
        for tag, attributes, _ in _served_elements(page)
        if tag == "div" and "zigbee-leave-banner" in attributes.get("class", "").split()
    ]
    assert len(banners) == 1, banners
    retry = [
        attributes
        for tag, attributes, ancestors in _served_elements(page)
        if tag == "button"
        and any("zigbee-leave-banner" in a.get("class", "").split() for _, a in ancestors)
    ]
    assert len(retry) == 1, retry
    values = _app_state(
        _BINDINGS_JS + _PAIRING_PAGE_JS + "const permits = []; let refuse = false;"
        "state.request = async (method, path, payload) => {"
        "  if (path === '/api/zigbee/permit') {"
        "    permits.push(payload);"
        "    if (payload.duration === 0 && refuse) {"
        "      const error = new Error('could not be closed'); error.status = 502; throw error;"
        "    }"
        "    return { permit_until: payload.duration > 0 ? future() : null };"
        "  }"
        "  if (path === '/api/zigbee/pairing') return { permit_until: future(), rows: [] };"
        "  if (path === '/api/zigbee/radio') return radioBody;"
        "  return [];"
        "};"
        "(async () => {"
        "  await state.startZigbeeSearch();"
        "  refuse = true;"
        "  await state.selectView('export');"
        f"  const inExport = {{ banner: Boolean(run({json.dumps(banners[0]['x-show'])})),"
        "    pane: state.zigbeePermitError, message: state.zigbeeLeaveCloseError };"
        "  refuse = false;"
        f"  await run({json.dumps(retry[0]['@click'])});"
        f"  const retried = {{ banner: Boolean(run({json.dumps(banners[0]['x-show'])})),"
        "    last: permits[permits.length - 1] };"
        "  console.log(JSON.stringify({ inExport, retried }));"
        "})();"
    )
    assert values["inExport"] == {"banner": True, "pane": None, "message": "could not be closed"}
    assert values["retried"] == {"banner": False, "last": {"duration": 0}}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_what_belongs_to_a_row_goes_with_it_and_a_listed_rows_refusal_stays():
    """A row that leaves the list takes its draft, its error and its
    "Saved." with it - removed from another tab, or gone with a radio swap.
    Kept, they came back on the same IEEE paired again later: a stale 409
    on a fresh row. A row STILL listed keeps its refusal: the poll is not
    what fixed it.

    Fault to prove it: drop the pruning loop from `syncZigbeeDrafts()`."""
    values = _app_state(
        _BINDINGS_JS + _PAIRING_PAGE_JS + f"const body = {json.dumps(_PAIRING_BODY)};"
        "let rows = body.rows;"
        "state.request = async () => ({ permit_until: null, rows: JSON.parse(JSON.stringify(rows)) });"
        "(async () => {"
        "  await state.loadZigbeePairing();"
        f"  state.zigbeeRowErrors[{json.dumps(_ADOPTED)}] = 'This device is not ready yet.';"
        f"  state.zigbeeRowErrors[{json.dumps(_UNNAMED)}] = 'This device is not ready yet.';"
        f"  state.zigbeeRowSaved[{json.dumps(_UNNAMED)}] = true;"
        f"  state.zigbeeRowDrafts[{json.dumps(_UNNAMED)}].name = 'Hall motion';"
        "  rows = [body.rows[0]];"
        "  await state.loadZigbeePairing();"
        "  console.log(JSON.stringify({ errors: state.zigbeeRowErrors,"
        "    drafts: Object.keys(state.zigbeeRowDrafts), saved: state.zigbeeRowSaved }));"
        "})();"
    )
    assert values["errors"] == {_ADOPTED: "This device is not ready yet."}
    assert values["drafts"] == [_ADOPTED]
    assert values["saved"] == {}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_row_s_fields_are_disabled_with_its_buttons_during_a_radio_swap(api):
    """During a 503 (`radio_changing`) every button of a row was disabled -
    and its name and room stayed editable, inviting typing that could not
    be saved and an Enter that went nowhere. The fields follow the buttons.

    Every `:disabled` on the name field, the room select and the new-room
    field, from the served markup through `boundTrue`: false while the
    list answers, true during the swap.

    Fault to prove it: drop the name field's `:disabled`."""
    client, _, _ = api
    page = (await client.get("/")).text
    controls = [
        _pairing_element(page, "textarea", x_model="zigbeeRowDrafts[row.ieee].name"),
        _pairing_element(page, "select", x_model="zigbeeRowDrafts[row.ieee].room"),
        _pairing_element(page, "input", x_model="zigbeeRowDrafts[row.ieee].newRoom"),
    ]
    expressions = [control.get(":disabled", "false") for control in controls]
    values = _app_state(
        _BINDINGS_JS + f"const body = {json.dumps(_PAIRING_BODY)};"
        f"const expressions = {json.dumps(expressions)};"
        "state.zigbee = { configured_path: '/dev/serial/by-id/a' };"
        "state.authenticated = true;"
        "let fail = false;"
        "state.loadZigbeeRadio = async () => {};"
        "state.request = async () => {"
        "  if (fail) { const error = new Error('changing'); error.status = 503; throw error; }"
        "  return JSON.parse(JSON.stringify(body));"
        "};"
        "const disabled = () => expressions.map((expr) =>"
        f"  boundTrue(expr, {{ row: state.zigbeeRow({json.dumps(_ADOPTED)}) }}));"
        "(async () => {"
        "  await state.loadZigbeePairing();"
        "  const idle = disabled();"
        "  fail = true;"
        "  await state.loadZigbeePairing();"
        "  console.log(JSON.stringify({ idle, swapping: disabled() }));"
        "})();"
    )
    assert values["idle"] == [False, False, False]
    assert values["swapping"] == [True, True, True]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_one_stick_has_one_name_in_the_thread_row_and_the_zigbee_row():
    """The MG24 read "SONOFF Dongle Plus MG24" in the Thread row and
    "SONOFF Zigbee Dongle Plus MG24" in the Zigbee row, one card further
    down - two helpers with two orders. A user cannot tell those are the
    same stick. One helper (`stickName()`), one order: the product string,
    then the fingerprint name, then the manufacturer, then the tty.

    Three sticks, each read in both rows: one with a product string and a
    fingerprint name that differ; one with only a fingerprint name; one the
    Zigbee answer knows only by path, whose manufacturer only the radios
    answer carries.

    Fault to prove it: put the fingerprint name before the product string
    in `stickName()` (the Thread row then names a Thread stick "... Zigbee
    Dongle ..."), or give the Zigbee row its own order again."""
    radios = {
        "serial": [
            {
                "path": "/dev/serial/by-id/usb-A",
                "tty": "ttyUSB0",
                "manufacturer": "SONOFF",
                "product": "SONOFF Dongle Plus MG24",
                "serial": None,
                "vid_pid": None,
            },
            {
                "path": "/dev/serial/by-id/usb-B",
                "tty": "ttyACM0",
                "manufacturer": None,
                "product": None,
                "serial": None,
                "vid_pid": None,
            },
            {
                "path": "/dev/serial/by-id/usb-C",
                "tty": "ttyUSB2",
                "manufacturer": "Silicon Labs",
                "product": None,
                "serial": None,
                "vid_pid": None,
            },
        ],
        "bluetooth": [],
        "current": {
            "thread_enabled": False,
            "thread_device": None,
            "thread_device_present": False,
            "bluetooth_adapter": None,
            "otbr_running": False,
        },
    }
    zigbee = {
        "serial": [
            {
                "path": "/dev/serial/by-id/usb-A",
                "product": "SONOFF Dongle Plus MG24",
                "fingerprint": {"name": "SONOFF Zigbee Dongle Plus MG24", "radio_type": "ezsp"},
                "is_thread": False,
                "selectable": True,
            },
            {
                "path": "/dev/serial/by-id/usb-B",
                "product": None,
                "fingerprint": {"name": "SONOFF ZBDongle-E V2", "radio_type": "ezsp"},
                "is_thread": False,
                "selectable": True,
            },
            {
                "path": "/dev/serial/by-id/usb-C",
                "product": None,
                "fingerprint": None,
                "is_thread": False,
                "selectable": True,
            },
        ],
        "configured_path": None,
        "configured_device_present": False,
        "progress": {"state": "idle", "attempts": 0, "error": None, "changed_at": "x"},
    }
    values = _app_state(
        f"state.radios = {json.dumps(radios)}; state.zigbee = {json.dumps(zigbee)};"
        "const thread = Object.fromEntries(state.radiosThreadOptions().map((o) => [o.value, o.label]));"
        "const zigbee = Object.fromEntries(state.zigbeeRadioOptions().map((o) => [o.value, o.label]));"
        "console.log(JSON.stringify({ thread, zigbee }));"
    )
    expected = {
        "/dev/serial/by-id/usb-A": "SONOFF Dongle Plus MG24",
        "/dev/serial/by-id/usb-B": "SONOFF ZBDongle-E V2",
        "/dev/serial/by-id/usb-C": "Silicon Labs",
    }
    for path, name in expected.items():
        assert values["thread"][path] == name, path
        assert values["zigbee"][path].split(" · ")[0] == name, path


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_the_interviewing_row_does_not_repeat_the_name_in_its_title():
    """The row's title is `<Manufacturer> <Model>` already. The state line
    under it said "Found Signify Netherlands B.V. LCA001 - reading its
    details", the same name twice in two lines. It names no device now,
    while the title does - and without manufacturer and model the title is
    the IEEE, and the line says a device was found.

    Fault to prove it: put `{device}` back into the interviewing sentence
    and pass the name to it."""
    values = _app_state(
        "console.log(JSON.stringify({"
        f"  named: state.zigbeeRowState({_pairing_row_js('interviewing', None)}),"
        "  bare: state.zigbeeRowState({ ieee: '00:12', state: 'interviewing', manufacturer: null,"
        "    model: null, device_id: null }),"
        "}));",
        translations=_web_strings(),
    )
    strings = _web_strings()
    assert values["named"] == strings["web.zigbee.state_interviewing"]
    assert "IKEA" not in values["named"] and "TRADFRI" not in values["named"]
    assert values["bare"] == strings["web.zigbee.state_joined"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_rows_already_added_fold_under_their_count(api):
    """The list holds every device the radio has seen since it came up,
    added ones included, and on a real network it pushed the device tiles
    far down the page. Rows not added yet stay open; added ones fold into a
    closed group titled with their count, by the file's two-key plural rule.
    A row added in THIS page stays open - its "in the device list" line is
    the confirmation, and folding it away as Add is pressed reads as the
    row vanishing.

    The served summary's `x-show`/`x-text` and the group's `:open`, through
    the real groups.

    Fault to prove it: put added rows in the open group, or render the
    summary with the `_many` key for one device."""
    client, _, _ = api
    page = (await client.get("/")).text
    elements = _served_elements(page)
    summary = [
        attributes
        for tag, attributes, _ in elements
        if tag == "summary" and "zigbeeAddedSummary" in attributes.get("x-text", "")
    ]
    details = [
        attributes
        for tag, attributes, _ in elements
        if tag == "details" and "zigbee-row-group" in attributes.get("class", "").split()
    ]
    assert len(summary) == 1 and len(details) == 1
    second_added = {**_PAIRING_BODY["rows"][0], "ieee": "00:12:4b:00:24:c2:1a:99", "device_id": 8}
    values = _app_state(
        _BINDINGS_JS + f"const body = {json.dumps(_PAIRING_BODY)};"
        f"const second = {json.dumps(second_added)};"
        "const groupsOf = () => state.zigbeeRowGroups().map((group) => ({ key: group.key,"
        "  rows: group.rows.map((row) => row.ieee),"
        f"  summary: Boolean(run({json.dumps(summary[0]['x-show'])}, {{ group }})),"
        f"  title: run({json.dumps(summary[0]['x-text'])}, {{ group }}),"
        f"  open: boundTrue({json.dumps(details[0][':open'])}, {{ group }}) }}));"
        "const out = {};"
        "state.zigbeePairing = JSON.parse(JSON.stringify(body)); out.one = groupsOf();"
        "state.zigbeePairing.rows.push(second); out.two = groupsOf();"
        f"state.zigbeeAddedHere[{json.dumps(_ADOPTED)}] = true; out.addedHere = groupsOf();"
        "console.log(JSON.stringify(out));",
        translations=_web_strings(),
    )
    strings = _web_strings()
    assert values["one"] == [
        {
            "key": "open",
            "rows": [_UNNAMED],
            "summary": False,
            "title": values["one"][0]["title"],
            "open": True,
        },
        {
            "key": "added",
            "rows": [_ADOPTED],
            "summary": True,
            "title": strings["web.zigbee.added_group_one"],
            "open": False,
        },
    ]
    assert values["two"][1]["title"] == strings["web.zigbee.added_group_many"].replace(
        "{count}", "2"
    )
    assert values["addedHere"][0]["rows"] == [_UNNAMED, _ADOPTED]
    assert values["addedHere"][1]["rows"] == ["00:12:4b:00:24:c2:1a:99"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_a_503_from_a_button_lands_in_the_banner_the_next_list_clears():
    """`api.zigbee.radio_changing` used to say "reload the page". The tab
    recovers by itself, so it says to wait a moment - and that has to be
    TRUE everywhere the sentence appears. It arrives from the list and from
    every button on the tab (Start, Add, Retry, Remove), and a failed
    button used to keep its message in a field no poll clears: the user
    would wait for a list that had long updated, under a sentence that no
    longer applied.

    A 503 from Start goes into the list's own banner, where the next list
    that answers clears it; the button's own error field stays empty.

    Fault to prove it: keep a 503 from a permit request in
    `zigbeePermitError`."""
    from loxmatter import i18n

    for key in ("api.zigbee.radio_changing", "api.zigbee.unknown_device"):
        assert "reload" not in i18n._STRINGS[key]["en"].lower(), key
        assert "neu laden" not in i18n._STRINGS[key]["de"].lower(), key
        assert "Seite" not in i18n._STRINGS[key]["de"], key
    values = _app_state(
        _BINDINGS_JS + _PAIRING_PAGE_JS + "let swapping = true;"
        "state.loadZigbeeRadio = async () => {};"
        "state.request = async (method, path) => {"
        "  if (swapping) { const error = new Error('The Zigbee radio is being changed.');"
        "    error.status = 503; throw error; }"
        "  return { permit_until: null, rows: [] };"
        "};"
        "(async () => {"
        "  await state.startZigbeeSearch();"
        "  const during = { list: state.zigbeePairingError, button: state.zigbeePermitError };"
        "  swapping = false;"
        "  await state.loadZigbeePairing();"
        "  const after = { list: state.zigbeePairingError, button: state.zigbeePermitError };"
        "  console.log(JSON.stringify({ during, after }));"
        "})();"
    )
    assert values["during"] == {"list": "The Zigbee radio is being changed.", "button": None}
    assert values["after"] == {"list": None, "button": None}


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_name_field_shows_a_long_name_and_stays_one_line(api):
    """A prefilled `<Manufacturer> <Model>` - "IKEA of Sweden TRADFRI bulb
    E27 WW 806lm" - was cut off inside a one-line input at 375 px, so nobody
    could check the name before keeping it. The field is a one-row textarea
    whose wrapper grows with the text (`data-value`), and it must still
    behave as a single-line field: Enter submits instead of breaking the
    line, and a pasted line break becomes a space.

    The served field: its tag and row count, the wrapper's `:data-value`
    reading the same draft as the field's `x-model`, the `.prevent` on
    Enter, and the `@input` run on a pasted two-line name.

    Fault to prove it: drop the line-break replacement from
    `zigbeeRowEdited()`, or the `.prevent` from the Enter handler."""
    client, _, _ = api
    page = (await client.get("/")).text
    field = _pairing_element(page, "textarea", x_model="zigbeeRowDrafts[row.ieee].name")
    assert field.get("rows") == "1"
    assert "@keydown.enter.prevent" in field
    wrappers = [
        (attributes, ancestors)
        for tag, attributes, ancestors in _served_elements(page)
        if tag == "span" and "zigbee-name-grow" in attributes.get("class", "").split()
    ]
    assert len(wrappers) == 1
    assert wrappers[0][0][":data-value"] == field["x-model"]
    values = _app_state(
        _BINDINGS_JS + _PAIRING_PAGE_JS + f"const body = {json.dumps(_PAIRING_BODY)};"
        "state.request = async () => JSON.parse(JSON.stringify(body));"
        "(async () => {"
        "  await state.loadZigbeePairing();"
        f"  const row = state.zigbeeRow({json.dumps(_UNNAMED)});"
        f"  exec({json.dumps(field['x-model'])} + ' = __value', {{ row, __value: 'Hall\\r\\n motion' }});"
        f"  exec({json.dumps(field['@input'])}, {{ row }});"
        f"  console.log(JSON.stringify({{ name: state.zigbeeRowDrafts[{json.dumps(_UNNAMED)}].name }}));"
        "})();"
    )
    assert values["name"] == "Hall motion"
