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

# The private helper on purpose, not a second dict comprehension over the
# same keys: the tests at the end of this file run the shipped `app.js`
# with the table the browser really gets from `GET /api/i18n`, and a local
# copy would keep agreeing with itself if that endpoint ever changed.
from loxmatter.api.language import _web_strings
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


async def test_the_channel_switch_is_hidden_but_everything_behind_it_still_works(api):
    """The channel switch cannot work end to end for 0.3.0: `update_check.
    py` answers the dev channel with `target="main"`, `applyUpdate()`
    posts that verbatim, and the sidecar's own pattern for the dev
    channel (update-once.sh, Rule 1: `^[0-9a-f]{7,40}$`) rejects "main"
    outright - and even a real commit SHA would not help, since `set_tag`
    writes the bare SHA as the image tag while CI only ever publishes
    `:dev`/`:sha-<short>`. The maintainer chose to hide the control for
    this release rather than ship one that fails on every click.

    What must hold, and what this test checks in that order:

      1. The control markup is NOT delivered to the browser inside a
         live (uncommented) tag - a real HTML comment, not merely an
         `x-show="false"` that would still ship the buttons and their
         `@click` handlers to anyone reading the page source.
      2. EVERYTHING it would have driven still exists and still works:
         `app.js` still defines `setUpdateChannel()`, and the settings
         route/store underneath it still accept and report a channel -
         re-enabling this later must be a UI change (uncommenting the
         markup, once the two points above are actually fixed) plus the
         `set_tag`/CI-tag fix, not a rebuild of the feature.
      3. The hidden block still carries a comment explaining WHY, naming
         both concrete blockers, so nobody "fixes" this by silently
         deleting the block or uncommenting a control that is still
         broken."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text

    # The hidden block, isolated from the rest of the page: everything
    # from the `<!--` that opens it (found by rewinding from the marker
    # text this fix's own comment carries) to its closing `-->`.
    hidden_idx = page.index("HIDDEN FOR 0.3.0")
    comment_start = page.rindex("<!--", 0, hidden_idx)
    # `rindex` finds the NEAREST preceding "<!--" - which is only the
    # right one if it is genuinely adjacent to the marker text. Mutation-
    # tested: a mutation that deletes just the "<!--"/"-->" pair around
    # this block (re-enabling the control) without touching the marker
    # text left `rindex`/`index` silently latching onto a DIFFERENT,
    # unrelated comment nearby and reporting the control as still hidden
    # - this proximity check is what catches that instead of a false
    # green.
    assert hidden_idx - comment_start < 80, (
        "the nearest preceding '<!--' is too far from the 'HIDDEN FOR 0.3.0' "
        "marker to be its own opening tag - the block may no longer be a "
        "real HTML comment at all"
    )
    comment_end = page.index("-->", hidden_idx) + len("-->")
    hidden_comment = page[comment_start:comment_end]
    outside_hidden_comment = page[:comment_start] + page[comment_end:]

    # 1. No live channel-switch control reaches the browser. A plain
    # `needle not in page` is NOT the right check here (and is exactly
    # what this test's own first version got wrong): the needles are
    # LITERALLY present in `page` regardless of the fix, since HTML
    # comments are still delivered as plain text - the browser only
    # skips evaluating what is inside them, it does not strip it from
    # the response. The real assurance is that every needle sits ONLY
    # inside the hidden comment and nowhere else in the page.
    for needle in (
        ":class=\"{ active: updateStatus?.channel === 'stable' }\"",
        "@click=\"setUpdateChannel('stable')\"",
        ":class=\"{ active: updateStatus?.channel === 'dev' }\"",
        "@click=\"setUpdateChannel('dev')\"",
        "x-show=\"updateStatus?.channel === 'dev'\"",
    ):
        assert needle in hidden_comment, f"expected inside the hidden block: {needle!r}"
        assert needle not in outside_hidden_comment, (
            f"the hidden channel-switch control still reaches LIVE markup: {needle!r}"
        )

    # 2. Everything behind the control is still there. The strings
    # themselves stay in the delivered page too (inside the HTML comment,
    # not evaluated by Alpine, but still literally present) - per this
    # fix's own instruction to leave the i18n strings in place.
    assert "t('web.system.update_channel_label')" in page
    assert "t('web.system.update_channel_stable')" in page
    assert "t('web.system.update_channel_dev')" in page
    assert "t('web.system.update_channel_dev_warning')" in page
    assert "async setUpdateChannel(channel)" in script

    # 3. The reason is written down at the markup, and names BOTH
    # concrete blockers - not a vague "TODO" a future reader could shrug
    # off without understanding what would actually break.
    assert 'target="main"' in hidden_comment
    assert "set_tag" in hidden_comment
    assert "loxmatter:&lt;sha&gt;" in hidden_comment or "loxmatter:<sha>" in hidden_comment


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
    assert "done: ['pull','recreate','health'].includes(updateStatus.state.phase)" in page
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
    tail = json.dumps("\n" + fill_strings + "return app();")
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
    """Design section 4: ONE `<dialog>` per purpose for the whole page,
    not one per tile.

    Markup inside `x-for` is shipped once PER DEVICE - with thirty
    devices there would be thirty complete signal tables in the
    document, and every `id` in it thirtyfold (the same pitfall that
    `aria-labelledby` in the tile menu already had to dodge once). The
    count of 2 (one signal modal, one control modal from task 7) is
    the only assertion that would even notice this regression: a
    `<dialog>` inside the tile would otherwise look exactly the same in
    the shipped text as one at the end of the page.

    The location check (after `</main>`) additionally proves that both
    sit outside the view sections and thus outside any
    device loop."""
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
      - projectSync.includeNewDevices: 204x21.5 -> 200x24
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

    # The four text-label checkboxes now carry `checkbox-label` - not
    # `.row label` in general, because the same row class elsewhere also
    # carries text-field, file, and select labels (e.g. the bridge IP),
    # whose layout is not meant to be dragged along here. `:has()`
    # deliberately avoided (project guideline).
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
