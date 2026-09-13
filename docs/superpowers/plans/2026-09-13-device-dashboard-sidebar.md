# Device Dashboard Room Sidebar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the horizontal room-chip bar above the device grid with a
persistent left sidebar (search + room navigation), and narrow the device
grid so a wide monitor fills with more columns instead of empty gap.

**Architecture:** Pure frontend change to the existing Alpine.js single-page
app (`src/loxmatter/web/index.html`, `style.css`, `app.js`). No new Alpine
state or methods are needed — `roomChips()`, `hasAnyRoom()`, `deviceGroups()`,
`beginRenameRoom()` etc. already do everything the sidebar needs
(`app.js:2009-2192`); this plan only relocates and restyles existing markup
into a two-column flex layout and widens the grid's column count.

**Tech Stack:** Alpine.js 3 (vendored, no build step), plain CSS custom
properties, FastAPI serving static files, httpx-based string-assertion tests
in `tests/api/test_web.py`.

## Global Constraints

- Everything in this repository — code, comments, test names, commit
  messages — is written in English (`CLAUDE.md`). The only German that
  belongs anywhere touched by this plan is an existing `de:` value in
  `src/loxmatter/i18n/strings.yaml` — this plan adds no new strings, so
  none of its edits touch that file.
- Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`,
  `uv run pytest -v`, and `uv run python scripts/check_language.py` before
  considering any task done (`docs/DEVELOPMENT.md`).
- The tile's own content (value rows, `value-fresh` highlighting, battery
  row, kebab menu, offline pill, color stripe) is explicitly unchanged by
  this plan (design doc section 2) — no task here touches
  `.device-card`'s internal markup.
- `scripts/capture_screenshots.py:302` selects `.room-bar` and the second
  `.device-grid` by CSS class name and position
  (`shoot(page, "dashboard", ".room-bar", "nth:.device-grid:1")`). Both
  class names and the relative document order of the two `.device-grid`
  elements must survive this plan unchanged — verified per-task below.
- Design doc: `docs/superpowers/specs/2026-09-13-device-dashboard-sidebar-and-expert-settings-design.md`,
  sections 3 and 4.

---

## Task 1: Narrow the Device Grid's Minimum Column Width

**Files:**
- Modify: `src/loxmatter/web/style.css:1870-1875`
- Modify: `tests/api/test_web.py:4509-4513`

**Interfaces:** None — pure CSS constant change, no new state or function.

- [ ] **Step 1: Change the existing test to assert the new, narrower minimum width**

Open `tests/api/test_web.py` and find:

```python
async def test_the_device_grid_is_multi_column(api):
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    assert "auto-fill" in css
    assert "minmax(260px" in css
```

Replace the last line so the whole test reads:

```python
async def test_the_device_grid_is_multi_column(api):
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    assert "auto-fill" in css
    assert "minmax(200px" in css
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/api/test_web.py::test_the_device_grid_is_multi_column -v`
Expected: FAIL — `assert "minmax(200px" in css` is false, the file still says `260px`.

- [ ] **Step 3: Narrow the grid's minimum column width**

In `src/loxmatter/web/style.css`, find:

```css
.device-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 0.6rem;
  align-items: stretch;
}
```

Change `260px` to `200px`:

```css
.device-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0.6rem;
  align-items: stretch;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/api/test_web.py::test_the_device_grid_is_multi_column -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): narrow the device grid so a wide window fits more columns

260px per card left a wide monitor's auto-fill grid with either a
half-empty last column or wide inter-card gaps. 200px lets more cards
fit per row without changing the card's own content or padding.
EOF
)"
```

---

## Task 2: Turn the Room Bar Into a Persistent Sidebar

**Files:**
- Modify: `src/loxmatter/web/index.html:816-934` (room bar) and `:1726-1727` (end of the room-grouped device grid, closing `</section>`)
- Modify: `src/loxmatter/web/style.css:1920-1970` (`.room-bar`, `.room-chips`, `.room-spacer`, `.room-chip`, `.room-chip.active`)
- Modify: `tests/api/test_web.py:6058-6077` (retire the obsolete spacer test) and add two new tests

**Interfaces:**
- Consumes: `roomChips()`, `hasAnyRoom()`, `deviceGroups()`, `beginRenameRoom()`,
  `commitRenameRoom()`, `cancelRenameRoom()`, `renamingRoom`, `renameDraft`,
  `deviceSearch`, `visibleDevices()`, `visibleGroups()`, `hitsOutsideRoom()`,
  `clearRoomFilter()`, `openGroupCreate()` — all pre-existing in `app.js`,
  none of their signatures change.
- Produces: two new CSS classes other tasks/tests may rely on going
  forward — `.dashboard-shell` (the two-column flex container) and
  `.dashboard-main` (the right-hand column holding the group grid and the
  room-grouped device grid).

### 2.1 Why this is one task, not several

Removing the room-spacer element only makes sense together with the flex
restructuring that makes it obsolete — done separately, the search field
would visibly stretch to fill the whole row in the interval between the two
changes (the spacer, not the search field, currently carries the
`flex: 1 1 auto` that reserves the gap; see the CSS at `style.css:1935-1942`).
Both land in one task so there is no reviewable-but-visually-broken
intermediate state.

- [ ] **Step 1: Write the failing tests**

In `tests/api/test_web.py`, replace the existing test:

```python
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
```

with two new tests covering the sidebar it becomes:

```python
async def test_the_room_bar_has_no_horizontal_spacer_trick_any_more(api):
    """The room bar is now the sidebar column of `.dashboard-shell`
    (device dashboard sidebar design, 2026-09-13): the search field
    always sits at a fixed position at the top of a vertical column,
    whether or not any device carries a room, so the horizontal spacer
    that used to push it to the right of a chip row - and the CSS rule
    that gave the spacer its `flex: 1 1 auto` - are both gone."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert '<span class="room-spacer">' not in page
    assert 'class="search-field"' in page
    css = (await client.get("/static/style.css")).text
    assert ".room-spacer {" not in css


async def test_the_dashboard_shell_puts_the_room_bar_beside_the_grid(api):
    """`.dashboard-shell` is the new two-column flex container: the room
    bar (now a sidebar) and the room-grouped device grid are its two
    children, in that order, so the sidebar renders to the left of the
    grid it filters."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert 'class="dashboard-shell"' in page
    shell_start = page.index('class="dashboard-shell"')
    room_bar_pos = page.index('class="room-bar"', shell_start)
    main_pos = page.index('class="dashboard-main"', shell_start)
    grid_pos = page.index("deviceGroups()", shell_start)
    assert room_bar_pos < main_pos < grid_pos
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/api/test_web.py -k "dashboard_shell or no_horizontal_spacer" -v`
Expected: FAIL — neither `.dashboard-shell` nor `.dashboard-main` exist yet, and `.room-spacer` is still present.

- [ ] **Step 3: Wrap the room bar and the grid in `.dashboard-shell` / `.dashboard-main`, and remove the spacer**

In `src/loxmatter/web/index.html`, find (the comment plus the room bar's opening tag):

```html
        <!-- Room bar (design 6.3): does not show at all as long as not a
             single device carries a room - with three devices and no
             room it would be a line of noise above a list that fits in
             one glance anyway. The search field stays reachable in that case
             regardless, because it also searches by name and category
             without any rooms. -->
        <div class="room-bar" x-show="devices.length > 0 || groups.length > 0" x-cloak>
```

Replace with:

```html
        <!-- Room bar (design 6.3), now the sidebar column of
             `.dashboard-shell` (device dashboard sidebar design,
             2026-09-13): does not show at all as long as not a single
             device carries a room - with three devices and no room it
             would be a line of noise beside a list that fits in one
             glance anyway. The search field stays reachable in that case
             regardless, because it also searches by name and category
             without any rooms. The visibility check that used to sit on
             this bar alone now covers the whole shell, sidebar and grid
             together - see `.dashboard-shell` below. -->
        <div class="dashboard-shell" x-show="devices.length > 0 || groups.length > 0" x-cloak>
        <div class="room-bar">
```

Then find the spacer block:

```html
          <!-- The spacer only exists together with the chips: it
               pushes the search field past them to the right. Without
               rooms there is nothing to push past - the field should
               then move to the left edge instead of standing alone on the right
               in an empty row. Hence its own `x-if` rather than an element
               that is always there. -->
          <template x-if="hasAnyRoom()"><span class="room-spacer"></span></template>
```

Replace with:

```html
          <!-- The horizontal spacer that used to push the search field to
               the right of the chip row is gone (device dashboard sidebar
               design, 2026-09-13): the sidebar is a vertical column now,
               the search field always sits at a fixed position above the
               room list, and there is nothing left to push past. -->
```

Then find where the room bar closes, right after the "+ New group" button:

```html
          <button
            @click="openGroupCreate()"
            :disabled="devices.length === 0"
            x-text="t('web.groups.new')"
          ></button>
        </div>
```

Replace the closing `</div>` with one that also opens `.dashboard-main`:

```html
          <button
            @click="openGroupCreate()"
            :disabled="devices.length === 0"
            x-text="t('web.groups.new')"
          ></button>
        </div>
        <div class="dashboard-main">
```

Finally, find the end of the room-grouped device grid, right before the devices `<section>` closes:

```html
        </template>
      </section>
```

(this `</template>` closes the `x-for="group in deviceGroups()"` loop — it is the only occurrence of exactly `</template>\n      </section>` in the file). Replace with:

```html
        </template>
        </div>
        </div>
      </section>
```

- [ ] **Step 4: Restyle the room bar as a vertical sidebar column**

In `src/loxmatter/web/style.css`, find:

```css
.room-bar {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
  margin-bottom: 0.8rem;
}

.room-chips {
  display: flex;
  gap: 0.3rem;
  flex-wrap: wrap;
  align-items: center;
}

/* Soaks up the remaining free space of the row and thereby pushes the search
 * field past the chips to the right. Exists as its own class rather than as
 * a `style` attribute in the markup, because the element now sits under an
 * `x-if` - the reasoning belongs in the markup there, the measurement in the
 * stylesheet. */
.room-spacer {
  flex: 1 1 auto;
}

.room-chip {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text-muted);
  border-radius: 999px;
  padding: 0.15rem 0.7rem;
  font-size: 0.8rem;
}

.room-chip.active {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-contrast);
}
```

Replace with:

```css
/* Two-column shell: the room bar (now a sidebar) on the left, the group
 * grid and room-grouped device grid on the right. `align-items: flex-start`
 * so the sidebar does not stretch to the grid's full height artificially -
 * it only needs `position: sticky` (below) to stay in view while the grid
 * scrolls. */
.dashboard-shell {
  display: flex;
  align-items: flex-start;
  gap: 1.25rem;
}

.dashboard-main {
  flex: 1 1 auto;
  min-width: 0;
}

/* The room bar is a fixed-width vertical column now, not a horizontal
 * row (device dashboard sidebar design, 2026-09-13) - search field on
 * top, room list below it, both stacked. `position: sticky` keeps room
 * navigation reachable while scrolling a long device grid; `top` clears
 * the sticky app header (`header.app-header`, `z-index: 10`,
 * `style.css:106-117`). */
.room-bar {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0.6rem;
  flex: none;
  width: 13rem;
  position: sticky;
  top: 4.5rem;
}

.room-chips {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.room-chip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  width: 100%;
  border: none;
  border-left: 3px solid transparent;
  background: none;
  color: var(--text-muted);
  border-radius: 6px;
  padding: 0.4rem 0.6rem;
  font-size: 0.85rem;
  text-align: left;
}

.room-chip.active {
  background: var(--type-bg);
  border-left-color: var(--accent);
  color: var(--accent);
  font-weight: 600;
}
```

`.room-rename` and `.room-rename-input` (`style.css:1959-1970`, directly below what
you just replaced) need no change — both already work as an inline element
within a row, vertical or horizontal.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/api/test_web.py -k "dashboard_shell or no_horizontal_spacer or room_bar or search_field or device_grid" -v`
Expected: PASS on all of them, including the pre-existing
`test_the_page_offers_the_room_bar` (`test_web.py:4434`) and
`test_room_chip_in_the_tile_menu_carries_the_full_name_as_a_title`
(`test_web.py:4684`) — neither asserts on `.room-bar`'s layout, only on
Alpine expressions and the (unrelated) tile menu's own room list, so
both keep passing unmodified.

- [ ] **Step 6: Run the full web test suite**

Run: `uv run pytest tests/api/test_web.py -v`
Expected: PASS. If anything else fails, read the failure before changing
anything else — in particular, if a test asserts a specific parent/child
relationship for the room bar or the group grid that this task's markup
move disturbs, fix the markup, not the test (Global Constraints).

- [ ] **Step 7: Verify the screenshot script's selectors still resolve**

`.room-bar` keeps its class name and `.device-grid` keeps its relative
document order (Global Constraints), so `scripts/capture_screenshots.py:302`
needs no code change. Confirm this by inspecting the served page once by
hand rather than assuming it:

```bash
uv run python -c "
from loxmatter.api.app import build_app
from loxmatter.model.store import Store
import tempfile, pathlib
tmp = pathlib.Path(tempfile.mkdtemp()) / 't.sqlite'
store = Store(tmp)
app = build_app(store, True, None)
import httpx, asyncio
async def main():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as c:
        page = (await c.get('/')).text
        assert page.count('class=\"device-grid') >= 2, 'device-grid count changed'
        print('room-bar present:', 'class=\"room-bar\"' in page)
asyncio.run(main())
"
```

Expected output: `room-bar present: True`, no assertion error. This is a
manual sanity check, not a new automated test — `capture_screenshots.py`
itself produces documentation images and is exercised separately (see
`docs/DEVELOPMENT.md`), not part of `pytest`.

- [ ] **Step 8: Run the full project checks**

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -v
uv run python scripts/check_language.py
```

Expected: all green. (This task touches no Python source, but `uv run mypy`
and `uv run ruff check .` still run over the whole project per
`docs/DEVELOPMENT.md` — confirm nothing elsewhere regressed.)

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): turn the room bar into a persistent sidebar

The room-chip bar and search field sat in a single horizontal row above
the device grid, which a wide monitor could not use for anything else,
and which stopped conveying room boundaries clearly once the device
count grew. Docks the same controls - unchanged Alpine bindings,
unchanged behavior - as a persistent left sidebar instead, freeing the
row's width for the grid and giving every room a permanently visible
entry rather than a chip that can wrap.
EOF
)"
```

---

## Self-Review

**Spec coverage** (design doc sections 3-4): sidebar with search + room
list (Task 2), denser grid (Task 1), grouping by room with headings under
"All Devices" (already implemented by the pre-existing `deviceGroups()` —
explicitly not re-built by this plan, confirmed in Task 2's Interfaces).
Rename-room-on-hover, the room list's `hasAnyRoom()` gating, and the
"no rooms yet" search-field behavior all carry over via the reused markup
and bindings — no task drops them. Section 4.1's optional card/list toggle
is intentionally **not** in this plan — it is called out in the design
doc itself as a secondary, deferrable view, and is left for a follow-up
plan so this one stays focused on the sidebar and grid density that were
the actual complaints (design doc section 1).

**Placeholder scan:** no TBD/TODO; every step shows complete, copy-ready
code; no step says "write tests for the above" without the test code.

**Type consistency:** N/A — no new functions or shared types are
introduced by this plan; every task reuses existing Alpine.js bindings
and CSS classes by their existing names.
