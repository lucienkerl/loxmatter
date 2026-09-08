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

"""Photographs the interface for the README.

Usage:  uv run --with playwright python scripts/capture_screenshots.py

Starts `dev_web_server.py --demo` itself, logs in, walks through the
views and drops the images under docs/screenshots/. Each image shows
only the region its caption in the README talks about - why, is
explained at `shoot()`.

Six of the seven images are REPRODUCIBLE: the demo database is freshly
created on every start, and every seeded timestamp is pinned to
`DEMO_TIMESTAMP` (see dev_web_server.py) instead of the wall clock.
Called twice, that produces byte-identical files there; a diff therefore
means a real change and must not be waved away as noise.

`system.png` is NOT reproducible. Its command log shows the HTTP requests
of this very run, down to the microsecond - the protocol of the capture
while it happens. Pinning that down would only be possible by having demo
mode fabricate a protocol, and a faked log in the documentation would be
worse than a noisy image. Anyone running this script without anything
having changed in the interface should therefore discard `system.png` and
find the remaining six unchanged.

The selectors below are read off the actual markup
(`src/loxmatter/web/index.html`) and the English translation strings
(`src/loxmatter/i18n/strings.yaml`), not guessed - the interface has
already changed three times in a week, a guessed selector would be wrong
again by the next run. Playwright is an ad-hoc dependency (see the usage
line above) and deliberately NOT in pyproject.toml - it's only needed to
regenerate these images.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"
PORT = 8420
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "loxmatter-demo"

# `main` is capped at 960 px by CSS, so a wider window just produces grey
# margin. 820 px lets the content column determine its own width and
# settles the tile grid onto two columns - the tiles grow from 300 to
# 345 px in the process, which translates directly into readability in
# the scaled-down README image.
VIEWPORT_NARROW = 820
# Only for the export preview: its eight columns need 895 px and would get
# horizontal scrolling in the narrow window.
VIEWPORT_WIDE = 1440
VIEWPORT_HEIGHT = 1000
# Only for the signal modal (see there): `.signals-modal` caps itself in
# style.css at `max-height: 85vh`, and anything beyond that scrolls inside
# the dialog rather than growing the window. Measured against the actual
# content (three endpoint groups of the first tile, Hallway button: 8 + 8 +
# 1 rows including header and group rows) that's about 1075 px - 85% of
# 1350 px is 1147 px, enough headroom that the dialog shrinks to its
# actual content height instead of scrolling on its own.
VIEWPORT_HEIGHT_SIGNALS = 1350

# For the `from tests...` import of the example project file below.
sys.path.insert(0, str(ROOT))


# Resolver for the region specs below. A spec is either a CSS selector,
# `card:<heading>` for the card around an h2 heading (cards have no
# classes of their own, their heading is the only stable feature), or
# `nth:<selector>:<index>` for the nth match.
_RESOLVE_JS = """
  (spec) => {
    if (spec.startsWith('card:')) {
      const want = spec.slice(5);
      const h = [...document.querySelectorAll('.card h2')]
        .find((h) => h.textContent.trim() === want);
      return h ? h.closest('.card') : null;
    }
    if (spec.startsWith('nth:')) {
      const cut = spec.lastIndexOf(':');
      const all = document.querySelectorAll(spec.slice(4, cut));
      return all[Number(spec.slice(cut + 1))] ?? null;
    }
    return document.querySelector(spec);
  }
"""


def shoot(
    page: Page,
    name: str,
    top: str,
    bottom: str | None = None,
    *,
    fixed: bool = False,
    pad_top: int | None = None,
    pad_bottom: int | None = None,
) -> None:
    """Photographs the region from `top` to `bottom` instead of the whole window.

    In the README the images run single-column and have about 896 px of
    content width there - anything wider gets scaled down. What matters
    for readability is therefore purely the CSS width of the crop, not its
    height: a tall narrow image stays readable, a 1440 px wide window
    comes out at barely half that. The crops here fall between 768 and
    952 px and so display practically unscaled; making them wider throws
    away exactly that advantage.

    A full-page shot would waste it twice over - `main` is capped at
    960 px, so a third of every image would be empty grey margin, and on
    top of that every image would show cards its own caption isn't even
    about.

    `fixed=True` for the dialog: it's `position: fixed` and so sits in the
    viewport, not the document - document coordinates would miss it.
    """
    page.wait_for_timeout(600)  # Alpine re-renders after loading
    rect = page.evaluate(
        """([topSpec, bottomSpec, fixed]) => {
          const resolve = """
        + _RESOLVE_JS
        + """;
          const a = resolve(topSpec);
          const b = bottomSpec ? resolve(bottomSpec) : a;
          if (!a || !b) return null;
          const ra = a.getBoundingClientRect();
          const rb = b.getBoundingClientRect();
          const ox = fixed ? 0 : window.scrollX;
          const oy = fixed ? 0 : window.scrollY;
          return {
            x: Math.min(ra.left, rb.left) + ox,
            y: Math.min(ra.top, rb.top) + oy,
            right: Math.max(ra.right, rb.right) + ox,
            bottom: Math.max(ra.bottom, rb.bottom) + oy,
          };
        }""",
        [top, bottom, fixed],
    )
    if rect is None:
        raise RuntimeError(f"{name}: region '{top}' to '{bottom}' not found in the markup")
    # The margin is adjustable separately top and bottom, because a crop
    # otherwise bleeds into the neighbouring element: 16 px above the
    # counter row reaches exactly into the upload row above it, and a
    # control that's horizontally cut in half looks like a broken image,
    # not a crop. Where the neighbours sit close together, `0` cuts
    # cleanly on the edge.
    pad = 16
    top_pad = pad if pad_top is None else pad_top
    bottom_pad = pad if pad_bottom is None else pad_bottom
    clip = {
        "x": max(rect["x"] - pad, 0),
        "y": max(rect["y"] - top_pad, 0),
        "width": rect["right"] - rect["x"] + 2 * pad,
        "height": rect["bottom"] - rect["y"] + top_pad + bottom_pad,
    }
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), clip=clip, full_page=not fixed)
    print(f"  {name}.png  ({round(clip['width'])}x{round(clip['height'])} css px)")


def select_view(page: Page, label: str) -> None:
    """Clicks the tab via its (English) label text - works because the
    demo database starts with no language preference set and the
    interface then falls back to English (see dev_web_server.py --demo).

    The tabs are `<a href="#/...">` and no longer buttons (URL navigation,
    see `nav.tabs` in index.html)."""
    page.click(f'nav.tabs a:has-text("{label}")')
    page.wait_for_timeout(400)


def capture(page: Page) -> None:
    page.goto(BASE, wait_until="networkidle")

    # The demo bridge already has a password (see --demo), so the login
    # screen shows, not first-run setup - that one has only ONE password
    # field.
    page.fill('input[type="password"]', PASSWORD)
    page.click('button:has-text("Log in")')

    # startApp() loads devices, signals, export and bridge settings in
    # parallel (app.js) - wait for the first device card instead of
    # relying on a fixed delay.
    page.wait_for_selector(".device-card", timeout=15000)
    page.wait_for_timeout(800)  # value chips and the live connection catch up

    # Room bar plus the first two room groups. The commissioning card
    # above it deliberately stays out: it's the subject of
    # `commissioning.png`.
    #
    # The argument for that was originally its size - with its three
    # explanatory paragraphs it took up the top half of an image whose
    # caption talks about the device list. Since the card was rebuilt
    # ("code first" design, 2026-09-07) it's less than half as tall, so
    # the argument is weaker; it still stays out, though, because two
    # images side by side showing the same subject is one image too many.
    #
    # That also drops the tab bar from this image - the argument used to
    # be that the gallery's opening image in particular had to show it,
    # so the application reads as multi-page. That argument still holds,
    # it's just carried by `commissioning.png` right next to it now: the
    # bar sits fully in that image. Showing the same bar twice wasn't
    # worth the half image-crop it cost.
    shoot(page, "dashboard", ".room-bar", "nth:.device-grid:1")

    # Signals no longer have their own tab ("signals as a modal" design,
    # 2026-09-05) - the image now comes from the modal over the device
    # grid. The path there is the same as for a user: kebab menu of the
    # first tile, then the menu item.
    page.click(".device-card .tile-menu > summary")
    page.click('.tile-menu-item:has-text("Edit signals")')
    page.wait_for_selector("dialog.signals-modal:not(.control-modal)[open]", timeout=5000)
    # A first attempt additionally expanded the expert group here, so that
    # more rows would be in the image. The result was unusable: Playwright
    # scrolls to the target of a `.click()`, and that target lies behind 17
    # functional signals - the image began mid-way through a cut-off row,
    # with no heading, with no way to tell WHAT it was showing.
    #
    # The original reason why no taller window was supposed to be needed
    # ("the first tile has functionally enough rows to fill the image")
    # has not held since the endpoint grouping (design 2026-09-07, section
    # 7.4): at `VIEWPORT_HEIGHT` the dialog stayed scrolled within its own
    # `max-height: 85vh` (style.css) and the image ended mid-way through
    # the second group ("Button 2") - the third group ("Device", the
    # battery) was never visible, even though the grouping is exactly the
    # most visible change of this rework. `VIEWPORT_HEIGHT_SIGNALS` gives
    # the dialog enough room to shrink to its actual height instead of
    # scrolling on its own - after that a single scroll from the top of
    # the page shows all three groups completely.
    page.set_viewport_size({"width": VIEWPORT_NARROW, "height": VIEWPORT_HEIGHT_SIGNALS})
    page.eval_on_selector("dialog.signals-modal:not(.control-modal)", "el => el.scrollTo(0, 0)")
    shoot(page, "signals", "dialog.signals-modal:not(.control-modal)", fixed=True)
    page.set_viewport_size({"width": VIEWPORT_NARROW, "height": VIEWPORT_HEIGHT})
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # The control modal (design "Controls for lamps", 2026-09-07). The
    # subject is the colour light, not just any tile: it's the only one
    # with both mode tabs and the colour field, and exactly that gradation
    # is the point of the image. The card is looked up by its name rather
    # than a fixed position - the tile order depends on category rank and
    # room (see DEMO_DEVICES in dev_web_server.py) and is allowed to
    # change without this image silently shifting to a different device.
    # The name sits in an `<input>`, so it's looked up via `.value`: a
    # text comparison against the markup would miss it.
    lamp = page.evaluate(
        """() => [...document.querySelectorAll('.device-card')]
             .findIndex((c) => c.querySelector('input')?.value === 'Kitchen spots')"""
    )
    if lamp < 0:
        raise SystemExit(
            "Tile 'Kitchen spots' not found - is the demo device still called that? "
            "(DEMO_DEVICES in scripts/dev_web_server.py)"
        )
    card = page.locator(".device-card").nth(lamp)
    card.get_by_role("button", name="Control").click()
    page.wait_for_selector("dialog.control-modal[open]", timeout=5000)
    # The Colour tab, not the default White tab: the light is set to
    # ColorMode 2 (colour temperature) in the fixture, so the modal opens
    # on "White" - and an image of the colour picker without the colour
    # picker would be pointless. The tab bar stays in the image and shows
    # both states.
    page.click("dialog.control-modal .control-tabs button:has-text('Colour')")
    page.wait_for_selector("dialog.control-modal .colour-field", timeout=5000)
    page.wait_for_timeout(300)
    shoot(page, "controls", "dialog.control-modal", fixed=True)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # The one crop that needs the wide window: the preview table is
    # 895 px wide (eight columns) and would get horizontal scrolling in
    # the narrow window, i.e. an image with its last column cut off. Back
    # to the narrow width afterwards.
    page.set_viewport_size({"width": VIEWPORT_WIDE, "height": VIEWPORT_HEIGHT})
    select_view(page, "Export")
    # Review-fix: without the click this crop showed only the two empty
    # "Project file sync" and "Export templates" cards - two-thirds empty
    # space, no preview table. "View preview" fills the third card below
    # with real rows, before anything is downloaded at all.
    page.click('button:has-text("View preview")')
    page.wait_for_selector("table", timeout=5000)
    page.wait_for_timeout(300)
    shoot(page, "export", "card:Project file sync", "card:Preview")
    page.set_viewport_size({"width": VIEWPORT_NARROW, "height": VIEWPORT_HEIGHT})

    # The "System check" card right at the top shows two "Error" rows in
    # this demo (no real matter-server client, no real UDP send) -
    # rightly so, but bad for a screenshot showcase: looks like a broken
    # product, but is really just the honest diagnosis of a deliberately
    # incomplete demo setup. The crop therefore starts only at "Live
    # diagnostics" - which used to require a scroll with compensation for
    # the sticky header and is now simply the region spec. That leaves
    # the tab bar out of this image; that's the same trade-off as for
    # `dashboard.png` and has the same compensation.
    select_view(page, "System")
    shoot(page, "system", "card:Live diagnostics", "card:Command log")

    select_view(page, "Settings")
    shoot(page, "settings", "card:Miniserver connection", "card:Periodic resend")

    # Special case 1: commissioning card with example code, but do NOT
    # submit - without a real Matter server, submitting would just
    # produce an error message. This image carries the header and tab
    # bar for the whole gallery, so here deliberately starting from the
    # top of the page.
    select_view(page, "Devices")
    # The selector hangs off the `id`, no longer off the placeholder text:
    # that used to be "Pairing code (11 digits or MT:…)" and, since the
    # design of 2026-09-07, is the digit sequence itself -
    # `input[placeholder*="MT:"]` found nothing anymore after that. An
    # `id` changes less often than a text that gets translated.
    #
    # What's entered now is the NUMERIC CODE rather than an MT: code: it is
    # the form this image is meant to explain, and only with it are the
    # grouping and the chip visible at all. `fill()` triggers the `input`
    # event that `formatCommissionCode` hangs off of - the number in the
    # image is therefore grouped, the same as after typing.
    page.fill("#commission-code", "34970112332")
    shoot(
        page,
        "commissioning",
        "header.app-header",
        "card:Commission a new device",
        pad_bottom=0,
    )

    # Special case 2: upload the example project file from the
    # projectsync tests and wait for the diff plan. Two German labels from
    # the fixture (it models a "before" project that used to be set up in
    # German) are replaced with English ones first - the "from" side of
    # each `.replace()` below deliberately keeps the fixture's exact
    # German text (see tests/projectsync/conftest.py, itself out of scope
    # here and deliberately German as fixture data), because a translated
    # argument simply wouldn't match. Otherwise the diff would show an old
    # German label next to the new English one, and the README images are
    # meant to be English throughout.
    from tests.projectsync.conftest import SAMPLE_PROJECT

    sample_text = (
        SAMPLE_PROJECT.replace("Alter Titel", "Old label")
        .replace("Altes Geraet erreichbar", "Old device reachable")
        .replace("Matter — Altes Geraet", "Matter — Old Device")
        .replace("Verwaist", "Orphaned")
    )
    # In the temp directory instead of under `docs/screenshots/`
    # (review-fix): that's a versioned directory, an aborted run would
    # otherwise leave a stray file there that needlessly dirties `git
    # status`.
    with tempfile.TemporaryDirectory() as tmp_dir:
        sample = Path(tmp_dir) / "_sample.Loxone"
        sample.write_text(sample_text, encoding="utf-8")
        select_view(page, "Export")
        page.set_input_files('input[type="file"]', str(sample))
        page.wait_for_timeout(2500)  # upload plus diff computation
        # Counter row plus the first expanded device block - exactly what
        # the caption talks about. The explanatory text above the
        # counters belongs to `export.png`.
        shoot(
            page,
            "project-sync",
            ".projectsync-summary",
            "nth:.projectsync-device:0",
            pad_top=0,
        )


def main() -> int:
    server = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "scripts" / "dev_web_server.py"),
            "--demo",
            "--port",
            str(PORT),
        ]
    )
    try:
        time.sleep(4)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(
                viewport={"width": VIEWPORT_NARROW, "height": VIEWPORT_HEIGHT},
                device_scale_factor=2,
            )
            capture(page)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
