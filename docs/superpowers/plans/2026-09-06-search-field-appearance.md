# Search field of the device view — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The search field of the device view gets its own shape — border from the palette, magnifying glass, match counter, and its own clear cross — instead of the browser's own system box.

**Architecture:** The border moves from the `<input>` to an enclosing flex container; the field inside it becomes borderless and background-less, the magnifying glass, counter, and cross are its siblings in the same flow. The focus ring hangs off the container via `:focus-within` and thereby encloses the whole group. The search logic in `app.js` is not touched — the counter only reads `visibleDevices().length`.

**Tech Stack:** Static HTML with Alpine.js (vendored under `web/vendor/`), hand-written CSS with custom properties, inline SVG sprite. Tests: pytest + httpx against the ASGI app, checking the **delivered** markup and CSS.

**Design:** [2026-09-06-search-field-appearance-design.md](../specs/2026-09-06-search-field-appearance-design.md)

## Global Constraints

- **Comments in source code without umlauts** — `ae`, `oe`, `ue`, `ss`. Only the documentation under `docs/` and the German text in `strings.yaml` carry real umlauts. (Consistent throughout the whole repo.)
- **No icon library, no network reference.** New symbols go into the inline sprite in `index.html`. The UI runs offline.
- **No new color.** Only the existing variables `--bg`, `--surface`, `--border`, `--text`, `--text-muted`, `--accent`. The design, section 2: "Clarity over impact."
- **`matchesSearch()`, `visibleDevices()`, `hitsOutsideRoom()` stay untouched.** No changes in these three functions.
- **Tests check what is delivered, not what is rendered.** No engine that applies CSS or executes Alpine runs in this suite. Assertions go against `(await client.get("/")).text` and `(await client.get("/static/style.css")).text`.
- **Markup assertions run through `_without_comments()`** (helper at the top of `tests/api/test_web.py`). The comments in `index.html` name attributes explicitly, in part to explain why they are *not* there — a search over the raw file finds that too.
- **CI checks:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest -v`.
- **New tests are appended to `tests/api/test_web.py`** (the file has grown chronologically, last line 3447).

---

### Task 1: The two translation keys

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml:815-817` (directly after `web.devices.search_show_all_rooms`, before `web.devices.more_signals_short`)
- Test: `tests/api/test_web.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: the keys `web.devices.search_count` (with placeholder `{count}`) and `web.devices.search_clear`. Task 3 binds both via `t()` in the markup.

- [ ] **Step 1: Write the failing test**

Append to the end of `tests/api/test_web.py`:

```python
# ---------------------------------------------------------------------------
# Search field of the device view (design from 2026-09-06). The field fell
# through the CSS grid - the form rule lists text, number, password and
# select, but not search -, which is why the browser drew it itself.
# ---------------------------------------------------------------------------


async def test_the_search_field_ships_the_words_for_counter_and_clear_button(api):
    """The counter carries text, the clear cross carries none and
    therefore needs an accessible name - both must arrive at the browser
    already translated.

    `{count}` stays UNRESOLVED here: it is resolved in app.js
    (`t(key, values)`), once the number is known. The server does not know
    it, and `GET /api/i18n` therefore delivers the raw template - exactly
    what the comparison against the string including the curly braces
    proves."""
    client, _, _ = api
    strings = (await client.get("/api/i18n")).json()["strings"]
    assert strings["web.devices.search_count"] == "{count} found"
    assert strings["web.devices.search_clear"] == "Clear search"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/api/test_web.py::test_the_search_field_ships_the_words_for_counter_and_clear_button -v
```

Expected: FAIL with `KeyError: 'web.devices.search_count'`.

- [ ] **Step 3: Write minimal implementation**

In `src/loxmatter/i18n/strings.yaml`, insert directly after the `web.devices.search_show_all_rooms` block (lines 815–817):

```yaml
# Without a plural special case: "1 found" and "1 Treffer" both read
# correctly, and the table has no plural form anywhere.
web.devices.search_count:
  en: "{count} found"
  de: "{count} Treffer"
# The clear cross carries no text of its own, so it needs one - the key
# labels both `title` AND `aria-label`.
web.devices.search_clear:
  en: "Clear search"
  de: "Suche leeren"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/api/test_web.py::test_the_search_field_ships_the_words_for_counter_and_clear_button -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(i18n): Woerter fuer Treffer-Zaehler und Loeschkreuz der Suche"
```

---

### Task 2: The magnifying-glass symbol

**Files:**
- Modify: `src/loxmatter/web/index.html:149-153` (in the inline sprite, between `#i-rename` and `#i-close`)
- Test: `tests/api/test_web.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `#i-search` in the sprite. Task 3 references it with `<use href="#i-search">`.

- [ ] **Step 1: Write the failing test**

Append to the end of `tests/api/test_web.py`:

```python
async def test_the_search_field_has_a_magnifier_of_its_own(api):
    """The magnifying glass comes from the inline sprite like every other
    symbol - the same reasoning as for the checked-in vendor/alpine.min.js:
    the UI runs offline.

    The clear cross, on the other hand, gets NO symbol of its own, it uses
    the existing `#i-close`. Two identical shapes would be two places to
    remember on the next stroke-weight change - and one of the two gets
    forgotten."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    assert 'id="i-search"' in page
    assert page.count('id="i-close"') == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/api/test_web.py::test_the_search_field_has_a_magnifier_of_its_own -v
```

Expected: FAIL at `assert 'id="i-search"' in page`.

- [ ] **Step 3: Write minimal implementation**

In `src/loxmatter/web/index.html`, insert directly before `<symbol id="i-close" ...>`:

```html
      <!-- Magnifying glass for the search field of the room bar (search
           field appearance design, 2026-09-06). Same stroke technique as
           the category icons: paths only, no fill, `currentColor` takes
           color and state from the outside. -->
      <symbol id="i-search" viewBox="0 0 24 24">
        <circle cx="11" cy="11" r="6.5" />
        <path d="M15.8 15.8 20.5 20.5" />
      </symbol>
```

- [ ] **Step 4: Run test to verify it passes**

The new test **and** the existing XML check, which automatically covers the symbol too:

```bash
uv run pytest tests/api/test_web.py::test_the_search_field_has_a_magnifier_of_its_own tests/api/test_web.py::test_the_inline_icon_symbols_are_well_formed_xml -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/web/index.html tests/api/test_web.py
git commit -m "feat(web): Lupensymbol fuer das Suchfeld in den Sprite"
```

---

### Task 3: The search field

Markup and CSS in one go: the markup alone would be a useless intermediate
state — four loose elements without a border.

**Files:**
- Modify: `src/loxmatter/web/index.html:358-363` (replace the bare `<input type="search">`)
- Modify: `src/loxmatter/web/style.css:1099-1102` (replace the `.device-search` rule)
- Test: `tests/api/test_web.py` (append)

**Interfaces:**
- Consumes: `#i-search` (task 2), `#i-close` (existing), `web.devices.search_count` and `web.devices.search_clear` (task 1), `web.devices.search_placeholder` (existing), `deviceSearch` and `visibleDevices()` from `app.js` (existing, unchanged).
- Produces: the container `.search-field` with the children `.search-icon`, `input[type="search"]`, `.search-count`, `.search-clear`. Task 4 hangs its width rule off `.search-field`.

- [ ] **Step 1: Write the failing tests**

Append all four to the end of `tests/api/test_web.py`:

```python
async def test_the_search_field_carries_the_frame_and_the_input_does_not(api):
    """The border sits on the container, not on the field.

    If both carried one, a border would sit inside a border - and the
    focus ring (next test) would have nothing to attach to that encloses
    the magnifying glass and cross too."""
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
    the ring should enclose the magnifying glass, counter, and cross too,
    not just the input field at their center. The browser's own outline on
    the field has to yield for that, otherwise both would be present."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    ring = css.split(".search-field:focus-within {", 1)[1].split("}", 1)[0]
    assert "var(--accent)" in ring
    inner_focus = css.split('.search-field input[type="search"]:focus {', 1)[1].split("}", 1)[0]
    assert "outline: none" in inner_focus


async def test_the_browser_does_not_add_a_second_clear_cross(api):
    """WebKit puts its own clear cross into an `input[type="search"]` -
    ours would sit next to it a second time.

    It is switched off with `-webkit-appearance` AND `appearance`: the
    pseudo-element itself is vendor-specific, and the assertion on the
    second form needs the start of the line - `"appearance: none"` is a
    substring of `"-webkit-appearance: none"` and would otherwise already
    be satisfied by the first line."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split("::-webkit-search-cancel-button {", 1)[1].split("}", 1)[0]
    assert "-webkit-appearance: none" in rule
    assert re.search(r"^\s*appearance: none", rule, re.MULTILINE)


async def test_the_counter_and_the_cross_appear_only_with_a_query(api):
    """Both hang off `deviceSearch` and carry `x-cloak`: with an empty
    field they are gone, and on the first paint they don't flash before
    Alpine has initialized.

    The counter reads `visibleDevices().length` - i.e. what is actually
    shown below the bar, including an active room filter. The search logic
    itself stays untouched.

    Two `aria-label`s: one on the input field (it only carries a
    placeholder, and that disappears exactly when someone has typed
    something), one on the cross (it carries no text at all)."""
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/api/test_web.py -k "search_field_carries or focus_ring_wraps or second_clear_cross or counter_and_the_cross" -v
```

Expected: 4 failed — the three CSS tests with `IndexError: list index out of range` (the split doesn't find the rule), the markup test likewise.

- [ ] **Step 3: Write the markup**

In `src/loxmatter/web/index.html`, replace the bare input field (lines 358–363, starting at `<input` and ending at `/>`) with:

```html
          <!-- The border sits on the container, not on the field: the
               magnifying glass, counter, and cross sit WITHIN the border,
               and the focus ring hangs off the whole group via
               :focus-within instead of off the input field at their
               center.

               A <div>, not a <label>: a <button> inside a label also
               triggers that label's forwarding to the labeled control -
               clearing should be one click, not two events. The price is
               a narrow strip of padding that does not focus into the
               field; the magnifying glass gets `pointer-events: none`
               (style.css) for that, so at least it doesn't punch a click
               hole into the left edge. -->
          <div class="search-field">
            <svg class="icon search-icon" aria-hidden="true"><use href="#i-search"></use></svg>
            <input
              type="search"
              x-model="deviceSearch"
              :placeholder="t('web.devices.search_placeholder')"
              :aria-label="t('web.devices.search_placeholder')"
            />
            <span
              class="search-count"
              x-show="deviceSearch.trim()"
              x-cloak
              x-text="t('web.devices.search_count', { count: visibleDevices().length })"
            ></span>
            <button
              class="search-clear"
              x-show="deviceSearch.trim()"
              x-cloak
              @click="deviceSearch = ''"
              :title="t('web.devices.search_clear')"
              :aria-label="t('web.devices.search_clear')"
            ><svg class="icon" aria-hidden="true"><use href="#i-close"></use></svg></button>
          </div>
```

- [ ] **Step 4: Write the CSS**

In `src/loxmatter/web/style.css`, **fully replace** the `.device-search` rule (lines 1099–1102) with:

```css
/* Search field of the room bar (search field appearance design,
 * 2026-09-06). It used to be a bare `input[type="search"]`: the form rule
 * further up in this file lists text, number, password and select -
 * `search` is not among them, and the browser therefore drew the field
 * itself. In dark mode none of the project's colors applied there.
 *
 * The border sits HERE, on the container - the input field inside it is
 * borderless and background-less (see below). That way the magnifying
 * glass, counter, and cross sit within the border, without any of them
 * needing to be absolutely positioned: any padding number that has to
 * match the icon size would be a coupling that breaks on the next
 * font-size change. */
.search-field {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 0.15rem 0.4rem;
  /* Grows into the free rest of the row, capped at 22rem. Lifting the cap
   * would give a wide window a search field spanning four tiles - worse
   * than the lonesome box it is meant to eliminate. */
  flex: 1 1 12rem;
  max-width: 22rem;
}

/* The ring belongs on the group, not on its center: on `input:focus` it
 * would enclose only the input field and leave the magnifying glass and
 * cross outside. */
.search-field:focus-within {
  border-color: var(--accent);
}

/* Without `pointer-events: none`, the magnifying glass would catch clicks
 * meant for the field - it looks like part of the field and should behave
 * like one too. */
.search-icon {
  color: var(--text-muted);
  pointer-events: none;
}

.search-field input[type="search"] {
  appearance: none;
  -webkit-appearance: none;
  flex: 1 1 auto;
  min-width: 0;
  border: none;
  background: none;
  color: var(--text);
  font-size: 0.85rem;
  padding: 0.2rem 0;
}

.search-field input[type="search"]:focus {
  outline: none;
}

/* WebKit puts its own clear cross into the field - ours would sit next to
 * it a second time. Both spellings, because the pseudo-element itself is
 * vendor-specific. */
.search-field input[type="search"]::-webkit-search-cancel-button {
  -webkit-appearance: none;
  appearance: none;
}

/* `tabular-nums`: without it the digit width changes going from 9 to 10
 * and pushes the cross next to it along with it - the same jitter that
 * already cost the age display on the signal row once before (see
 * `.value-fresh`). */
.search-count {
  font-size: 0.72rem;
  color: var(--text-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

/* Button without button styling - the group's border already exists.
 * `line-height: 0`, so the line height of the empty button text doesn't
 * push the icon off its axis downward. */
.search-clear {
  border: none;
  background: none;
  color: var(--text-muted);
  padding: 0.1rem;
  line-height: 0;
}

.search-clear:hover {
  color: var(--text);
}
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/api/test_web.py -k "search_field_carries or focus_ring_wraps or second_clear_cross or counter_and_the_cross" -v
```

Expected: 4 passed.

- [ ] **Step 6: Verify nothing referenced the old class**

```bash
grep -rn "device-search" src tests scripts
```

Expected: no output. (`.device-search` only appeared at the two replaced spots.)

- [ ] **Step 7: Run the whole suite**

```bash
uv run pytest -q
```

Expected: all green, no new failures.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "feat(web): Suchfeld mit Lupe, Treffer-Zaehler und eigenem Loeschkreuz"
```

---

### Task 4: Width and position without rooms

**Files:**
- Modify: `src/loxmatter/web/index.html:357` (the spacer fixed in the markup)
- Modify: `src/loxmatter/web/style.css` (add rule `.room-spacer` next to `.room-chips`, line ~1064)
- Test: `tests/api/test_web.py` (append)

**Interfaces:**
- Consumes: `.search-field` (task 3), `hasAnyRoom()` from `app.js` (existing, unchanged).
- Produces: nothing that a later task builds on.

- [ ] **Step 1: Write the failing test**

Append to the end of `tests/api/test_web.py`:

```python
async def test_the_search_field_moves_left_when_there_are_no_rooms(api):
    """The spacer that pushes the field to the right only exists together
    with the chips it would need to make room for.

    Without rooms, the chip bar hides itself (`x-if="hasAnyRoom()"`). If
    the spacer stayed in the markup regardless - as it did before - a
    single box would be left sitting on the right in an otherwise empty
    row. With its own `x-if`, it disappears along with the chips, and the
    field moves to the left edge, onto a sightline with the tile grid
    below it.

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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/api/test_web.py::test_the_search_field_moves_left_when_there_are_no_rooms -v
```

Expected: FAIL at `assert 'style="flex: 1 1 auto"' not in bar`.

- [ ] **Step 3: Write the markup**

In `src/loxmatter/web/index.html`, replace the line

```html
          <span style="flex: 1 1 auto"></span>
```

with:

```html
          <!-- The spacer only exists together with the chips: it pushes
               the search field to the right, past them. Without rooms
               there is nothing to push past - then the field should move
               to the left edge instead of sitting alone on the right in
               an empty row. Hence its own `x-if` and not a row that is
               always there. -->
          <template x-if="hasAnyRoom()"><span class="room-spacer"></span></template>
```

- [ ] **Step 4: Write the CSS**

In `src/loxmatter/web/style.css`, insert directly after the `.room-chips` rule (ends around line ~1069):

```css
/* Swallows the free rest of the row and thereby pushes the search field
 * to the right, past the chips. It is its own class instead of a `style`
 * attribute in the markup, because the element now hangs off an `x-if` -
 * the reasoning belongs in the markup there, the measurement in the
 * stylesheet. */
.room-spacer {
  flex: 1 1 auto;
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/api/test_web.py::test_the_search_field_moves_left_when_there_are_no_rooms -v
```

Expected: PASS.

- [ ] **Step 6: Run the whole suite and the CI checks**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "fix(web): Abstandhalter der Raumleiste haengt an den Chips, nicht an der Zeile"
```

---

### Task 5: Update the screenshot

`docs/screenshots/dashboard.png` is anchored to `.room-bar`
(`scripts/capture_screenshots.py:208`) and therefore shows exactly the
system box that tasks 1–4 eliminated.

**Files:**
- Modify: `docs/screenshots/dashboard.png` (recaptured)

**Interfaces:**
- Consumes: the finished UI from task 4.
- Produces: nothing.

- [ ] **Step 1: Provision Playwright**

```bash
uv run --with playwright python -m playwright install chromium
```

Expected: Chromium is installed, or already was ("is already installed").

- [ ] **Step 2: Capture**

```bash
uv run --with playwright python scripts/capture_screenshots.py
```

The script starts `dev_web_server.py --demo` itself, logs in, and drops
all seven images.

- [ ] **Step 3: Check which images actually changed**

```bash
git status --short docs/screenshots/
```

Expected: `dashboard.png` changed, `system.png` changed, the other five
unchanged.

`system.png` is **not** reproducible — its command log shows the HTTP
requests of the capture run itself, down to the microsecond (see the
script's header comment). It is discarded:

```bash
git checkout -- docs/screenshots/system.png
```

If `git status` reports any of the other five images as changed beyond
that, that is **not** noise but a real, unplanned change to the UI — then
stop and look into it, rather than committing it.

- [ ] **Step 4: Look at the new image**

Open `docs/screenshots/dashboard.png` and hold it against the design:
border in palette color instead of a system box, magnifying glass on the
left, cross on the right (the counter only appears with a query, so the
demo image doesn't show it), field noticeably wider than before and
right-aligned next to the room chips.

- [ ] **Step 5: Commit**

```bash
git add docs/screenshots/dashboard.png
git commit -m "docs(screenshots): Dashboard mit dem neuen Suchfeld"
```

---

## Conclusion

After task 5, the design is fully implemented. To merge the branch:
`superpowers:finishing-a-development-branch`.
