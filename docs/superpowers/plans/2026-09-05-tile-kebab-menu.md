# Tile kebab menu — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The device tile's footer becomes one line — export note on the left, a ⋮ on the right —, behind which room assignment, export, and remove live.

**Architecture:** The menu is a native `<details>` with the ⋮ as `<summary>`; the open/closed state thus lives in the DOM instead of in Alpine. This is precisely what removes the coupling "JavaScript mode state controls a native control" that cost the room select field six review rounds — `roomSelectDrafts`, `syncRoomSelectDraft`, `onRoomSelectChange`, and the `focusout` guard disappear without replacement.

**Tech Stack:** Alpine.js 3.17.1 (vendored under `src/loxmatter/web/vendor/`, no build step), native `<details>`/`<summary>`, pytest against the shipped files.

**Spec:** `docs/superpowers/specs/2026-09-05-tile-kebab-menu-design.md` — whenever in doubt, the spec governs, not this plan.

## Global Constraints

- **Developer prose in German.** Comments, docstrings, and commit messages in dense, reasoning German that states the *why*. Exception: the GPL header of every source file stays in the English FSF wording.
- **Every user-visible text goes through `t()`** with an `en` **and** `de` entry in `src/loxmatter/i18n/strings.yaml`. Keys flat and dotted. A button that carries only an icon needs a `:title` from `t()`.
- **No external frontend dependency.** Icons are inline SVG `<symbol>`s in the existing block in `index.html`. No icon library, no CDN, no Alpine plugin.
- **The literal `localStorage` must not appear in any shipped file** — an existing security test forbids it.
- **Commands run with `uv`**: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`. All four must be green.
- **The WebUI tests only prove THAT something is shipped**, never that it does what it should. Behavior is checked in the browser against the **vendored** Alpine (task 4), not by reading.
- Commit messages end with the trailer `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

## File Structure

**Changed:**
- `src/loxmatter/web/index.html` — a new `<symbol id="i-kebab">`; the device tile's footer (`.device-foot`, currently lines 527-618) is replaced.
- `src/loxmatter/web/style.css` — new rules for `.tile-menu`; the rules `.room-select`, `.room-new`, `.room-picker` go away.
- `src/loxmatter/web/app.js` — `roomSelectDrafts`, `syncRoomSelectDraft`, `onRoomSelectChange` and their callers go away; `newRoomFor` stays, in its original role.
- `src/loxmatter/i18n/strings.yaml` — two new keys.
- `tests/api/test_web.py` — four tests go away, one is adjusted, three are added.
- `scripts/capture_screenshots.py` — only if a selector breaks (task 4).

**Not touched:** API, store, `profiles/categories.py`. This plan changes only the UI.

---

### Task 1: Kebab icon and translation keys

**Files:**
- Modify: `src/loxmatter/web/index.html` (symbol block, after `<symbol id="i-remove">` at line 151)
- Modify: `src/loxmatter/i18n/strings.yaml` (`web.devices.*` block)
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the symbol `#i-kebab` and the keys `web.devices.menu`, `web.devices.menu_room_heading`, which tasks 2 and 3 use.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_web.py`. This file's `api` fixture is a 3-tuple `(client, store, device_id)`; the page is fetched via `/`, `app.js`/`style.css` via `/static/…` — read off the neighboring tests in the same file and write it the same way.

```python
async def test_the_tile_menu_has_its_own_icon_symbol(api):
    """The kebab symbol ships inline like all the others - no
    icon library, no CDN, because the UI runs offline. A
    `<use>` on a missing ID silently draws nothing, so a forgotten
    symbol shows up here rather than only in the browser."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    assert 'id="i-kebab"' in page
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web.py -k tile_menu_has_its_own_icon -v`
Expected: FAIL — `assert 'id="i-kebab"' in page`.

- [ ] **Step 3: Add the symbol**

In `src/loxmatter/web/index.html`, in the existing inline SVG block right after `<symbol id="i-remove">`:

```html
      <!-- Three filled dots instead of strokes: `.icon` sets
           `fill: none; stroke: currentColor`, which is right for
           line icons, but would make a dot invisible. `#i-offline`
           already makes the same exception for its dot. -->
      <symbol id="i-kebab" viewBox="0 0 24 24">
        <circle cx="12" cy="5" r="1.7" fill="currentColor" stroke="none" />
        <circle cx="12" cy="12" r="1.7" fill="currentColor" stroke="none" />
        <circle cx="12" cy="19" r="1.7" fill="currentColor" stroke="none" />
      </symbol>
```

- [ ] **Step 4: Add translation keys**

In `src/loxmatter/i18n/strings.yaml`, at the end of the `web.devices.*` block:

```yaml
# --- Tile menu (kebab menu design, 2026-09-05) ---
# `menu` is the accessible name of the kebab button: it carries no
# text, so it needs one. `menu_room_heading` labels the room section
# in the menu, so the list of room names doesn't sit without
# context above "Export" and "Remove".
web.devices.menu:
  en: "Actions"
  de: "Aktionen"
web.devices.menu_room_heading:
  en: "Room"
  de: "Raum"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_web.py tests/test_i18n.py tests/api/test_language.py -q`
Expected: PASS. `test_language.py` checks that every `web.*` key resolves via `GET /api/i18n`; `test_i18n.py` checks `en`/`de` completeness.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): kebab symbol and keys for the tile menu

Three filled dots instead of strokes - `.icon` sets `fill: none`,
which is right for line icons but made a dot invisible;
`#i-offline` already makes the same exception for its dot.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The menu with export and remove

**Files:**
- Modify: `src/loxmatter/web/index.html` (`.device-foot`, lines 527-618)
- Modify: `src/loxmatter/web/style.css` (new rules after `.device-foot`, line 1106)
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `#i-kebab`, `web.devices.menu` (task 1); the existing methods `exportDevice(device)`, `removeDevice(device)`, `exportHintFor(deviceId)` and the state field `bridgeSettings`.
- Produces: the markup scaffold `<details class="tile-menu">` with `.tile-menu-items`, into which task 3 hooks the room section.

**At the end of this task the UI is fully usable:** the room select field still sits in its place, export and remove have moved into the menu. The select field only disappears in task 3.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_web.py`:

```python
async def test_the_tile_menu_is_a_native_details_that_closes_three_ways(api):
    """`<details>` keeps the open/closed state in the DOM - the reason the
    menu is built this way at all (see design, section 4). Two of the
    three closing paths still have to come by hand: `<details>` closes
    neither on a click elsewhere nor on Escape on its own. The third,
    the click on an entry, is the entries' job."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    assert 'class="tile-menu"' in page
    assert "@click.outside" in page
    assert "@keydown.escape" in page


async def test_export_and_remove_moved_into_the_tile_menu(api):
    """Both actions now live in the menu and close it on click.
    The export button stays bound to `bridgeSettings.bridge_ip` - with
    no bridge IP set there is nothing to export."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    assert "exportDevice(device)" in menu
    assert "removeDevice(device)" in menu
    assert "!bridgeSettings.bridge_ip" in menu
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web.py -k "tile_menu_is_a_native_details or export_and_remove_moved" -v`
Expected: FAIL — `assert 'class="tile-menu"' in page`.

- [ ] **Step 3: Rebuild the footer**

In `src/loxmatter/web/index.html` replace the content of `<div class="device-foot">`. The `.room-picker` block (select field and new-room text field) along with its large comment **stays unchanged for now** — it goes away in task 3. Only the two icon buttons at the end are replaced, and the spacer before them:

```html
                    <span class="hint" x-text="exportHintFor(device.id)"></span>
                    <span style="flex: 1 1 auto"></span>
                    <!-- The open/closed state lives in the DOM, not in
                         Alpine: a `<details>` needs no `open` field per
                         tile that would have to be kept in sync with
                         the visible state. This exact sync obligation
                         cost the room select field six review rounds.
                         `<details>` does NOT, however, close on its own
                         on Escape (unlike a `<dialog>`) or on a click
                         elsewhere - both therefore sit here. That only
                         ever one menu is open falls out of this for
                         free: a click on the kebab of another tile
                         falls outside this `<details>` and closes it
                         via the same guard. -->
                    <details
                      class="tile-menu"
                      @click.outside="$el.open = false"
                      @keydown.escape="$el.open = false"
                    >
                      <summary :title="t('web.devices.menu')">
                        <svg class="icon"><use href="#i-kebab"></use></svg>
                      </summary>
                      <div class="tile-menu-items">
                        <button
                          class="tile-menu-item"
                          @click="$el.closest('details').open = false; exportDevice(device)"
                          :disabled="!bridgeSettings.bridge_ip"
                          x-text="t('web.devices.export')"
                        ></button>
                        <button
                          class="tile-menu-item is-danger"
                          @click="$el.closest('details').open = false; removeDevice(device)"
                          x-text="t('web.devices.remove')"
                        ></button>
                      </div>
                    </details>
```

The note paragraph below the footer (`x-show="!bridgeSettings.bridge_ip"`, with the link to settings) stays unchanged — it applies to all tiles, not to this one, and therefore does not belong in the menu.

- [ ] **Step 4: Add CSS**

At the end of `src/loxmatter/web/style.css`:

```css
/* Tile menu (kebab menu design, 2026-09-05).
 *
 * `position: relative` on the `<details>`, `absolute` on the list: the
 * menu is allowed to overhang the tile without making the footer
 * taller. It opens UPWARD (`bottom: 100%`) because the footer sits at
 * the tile's bottom edge - opening downward would cover the next tile
 * row instead of using free space. */
.tile-menu {
  position: relative;
  flex: none;
}

/* A `<summary>`'s default marker (triangle or disclosure arrow) has to
 * be switched off twice: `list-style` covers Firefox and Chrome, the
 * `::-webkit-details-marker` pseudo-element covers older WebKit
 * builds. */
.tile-menu > summary {
  list-style: none;
  cursor: pointer;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: var(--surface);
  color: var(--text-muted);
  padding: 0.15rem 0.4rem;
  line-height: 1;
  display: inline-flex;
  align-items: center;
}

.tile-menu > summary::-webkit-details-marker {
  display: none;
}

.tile-menu[open] > summary {
  border-color: var(--accent);
  color: var(--accent);
}

.tile-menu-items {
  position: absolute;
  right: 0;
  bottom: 100%;
  margin-bottom: 0.25rem;
  z-index: 5;
  min-width: 11rem;
  max-height: 60vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 1px;
  padding: 0.25rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  box-shadow: 0 6px 20px rgb(0 0 0 / 18%);
}

.tile-menu-item {
  border: none;
  background: none;
  text-align: left;
  padding: 0.3rem 0.5rem;
  border-radius: 4px;
  font-size: 0.8rem;
  white-space: nowrap;
}

.tile-menu-item:hover:not(:disabled) {
  background: var(--bg);
}

.tile-menu-item.is-danger {
  color: var(--danger);
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_web.py -q && uv run ruff format --check .`
Expected: PASS. Existing tests that check the old icon buttons in the footer fail here — they need to be adjusted to the new markup, not the markup rolled back to fit them.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): move export and remove into a kebab menu

A native `<details>`: the open/closed state lives in the DOM, not in
Alpine, so there is no `open` field per tile that would have to be
kept in sync with the visible state.

Two closing paths still have to come by hand - `<details>` closes
neither on Escape nor on a click elsewhere on its own. That only
ever one menu is open falls out of this for free.

The room select field still sits in its place; it goes away in the
next step.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Rooms into the menu, remove the select field without replacement

**Files:**
- Modify: `src/loxmatter/web/index.html` (`.room-picker` block in `.device-foot` goes away; room section goes into `.tile-menu-items`)
- Modify: `src/loxmatter/web/style.css` (`.room-select`, `.room-new`, `.room-picker` go away; two rules are added)
- Modify: `src/loxmatter/web/app.js`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `.tile-menu-items` (task 2), `web.devices.menu_room_heading` (task 1); the remaining methods `roomKeyOf(device)`, `roomChips()`, `saveRoom(device, value)`, `beginNewRoom(device)`, `commitNewRoom(device)`, `reconcileRoomFilter()`.
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_web.py`:

```python
async def test_the_rooms_are_menu_entries_and_the_select_is_gone(api):
    """Room assignment is now a list of entries in the menu. The
    `<select>` and the whole mechanism that was needed to keep its
    displayed value in sync with `device.room` go away without
    replacement - that is exactly the point of this rework."""
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
    """The checkmark on the current room is purely graphical. `aria-current`
    carries the same information for anything that doesn't see the
    page - without it the current room in the menu would be just one
    of several identical-looking rows."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    assert "aria-current" in page
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web.py -k "rooms_are_menu_entries or current_room_is_marked" -v`
Expected: FAIL — `assert "menu_room_heading" in page` and `assert "room-select" not in page` respectively.

- [ ] **Step 3: Remove the `.room-picker` block from the footer**

In `src/loxmatter/web/index.html` delete the entire `<span class="room-picker">…</span>` along with the multi-line comment before it without replacement — the comment starts with "Finding 1 (review of 2026-09-05" and ends right before the `<span>`. **Do not search by line numbers:** task 2 has already rebuilt the footer and shifted everything below it. The footer afterward consists of the note, the spacer, and the `<details>` from task 2.

- [ ] **Step 4: Hook the room section into the menu**

In `src/loxmatter/web/index.html`, as the first children of `<div class="tile-menu-items">`, **before** the two buttons from task 2:

```html
                        <p class="tile-menu-heading" x-text="t('web.devices.menu_room_heading')"></p>
                        <!-- "No room" is not a special case here but
                             the normal state of a not-yet-assigned
                             device - it carries the checkmark like any
                             other entry. The empty string is the same
                             value the API expects for "remove room",
                             so the same encoding on both sides and no
                             conversion. -->
                        <button
                          class="tile-menu-item"
                          :class="{ 'is-current': roomKeyOf(device) === '' }"
                          :aria-current="roomKeyOf(device) === '' ? 'true' : null"
                          @click="$el.closest('details').open = false; saveRoom(device, '')"
                          x-text="t('web.devices.room_none')"
                        ></button>
                        <template x-for="chip in roomChips().filter((c) => c.key !== '')" :key="chip.key">
                          <button
                            class="tile-menu-item"
                            :class="{ 'is-current': roomKeyOf(device) === chip.key }"
                            :aria-current="roomKeyOf(device) === chip.key ? 'true' : null"
                            @click="$el.closest('details').open = false; saveRoom(device, chip.key)"
                            x-text="chip.key"
                          ></button>
                        </template>
                        <button
                          class="tile-menu-item"
                          x-show="newRoomFor !== device.id"
                          @click="beginNewRoom(device)"
                          x-text="t('web.devices.room_new')"
                        ></button>
                        <!-- The text field is a child of the `<details>`,
                             so the menu stays open while typing. Escape
                             must ONLY cancel new-room mode and must not
                             bubble up to the `<details>`'s
                             `@keydown.escape`, or a cancel would make
                             the whole menu vanish too - hence `.stop`. -->
                        <input
                          x-show="newRoomFor === device.id"
                          x-cloak
                          type="text"
                          class="tile-menu-input"
                          x-model="newRoomDraft"
                          :placeholder="t('web.devices.room_new_placeholder')"
                          @keydown.enter="$el.closest('details').open = false; commitNewRoom(device)"
                          @keydown.escape.stop="newRoomFor = null; newRoomDraft = ''"
                        />
                        <hr class="tile-menu-sep" />
```

- [ ] **Step 5: CSS for the room section, remove old rules**

Add to `src/loxmatter/web/style.css` (next to the `.tile-menu-*` rules from task 2):

```css
.tile-menu-heading {
  margin: 0.1rem 0.5rem 0.2rem;
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
}

/* The checkmark sits in `::after`, not in the text: the entry carries
 * its meaning in the room name, the checkmark only confirms it.
 * `aria-current` in the markup says the same for anything that
 * doesn't see the page. */
.tile-menu-item.is-current {
  font-weight: 600;
  color: var(--accent);
}

.tile-menu-item.is-current::after {
  content: " ✓";
}

.tile-menu-input {
  margin: 0.15rem 0;
  font-size: 0.8rem;
}

.tile-menu-sep {
  border: none;
  border-top: 1px solid var(--border);
  margin: 0.25rem 0;
}
```

And remove: the rule `.room-rename, .room-select, .room-new { font-size: 0.75rem; }` becomes `.room-rename { font-size: 0.75rem; }` (the rename pencil stays), plus the entire `.room-picker` block along with its comment (it starts with "Wraps `<select>` and the new-room text field only as a focus guard").

- [ ] **Step 6: Clean up `app.js`**

Five spots, all in `src/loxmatter/web/app.js`:

1. **State:** delete `roomSelectDrafts: {},` along with the multi-line comment before it without replacement — the comment starts with "Tile permanently showed \"No room\"" and ends with the line `roomSelectDrafts: {},`.

2. **Adjust `newRoomFor`'s comment** — today it describes the coupling to the select list. New:

```javascript
    // Which tile is currently showing the text field for a new room name
    // (device ID or null). A single global scalar, not a set per
    // tile: therefore only ONE text field can ever be open. Picking
    // "+ New room ..." in a second tile's menu closes the first one
    // along with it - intentional, two text fields open at once would
    // be confusing anyway.
    newRoomFor: null,
```

3. **`loadDevices`:** remove the loop along with its comment, so that only this remains:

```javascript
    async loadDevices() {
      this.devicesError = null;
      try {
        this.devices = await this.request("GET", "/api/devices");
      } catch (error) {
        this.devicesError = t("web.devices.list_load_error", { message: error.message });
      }
    },
```

4. **`saveRoom`:** the `finally` block loses `syncRoomSelectDraft`, keeps `reconcileRoomFilter`:

```javascript
      } finally {
        // Even on failure: if the failed write path leaves a room
        // empty, the filter must not stay on a room that no longer
        // exists.
        this.reconcileRoomFilter();
      }
```

5. **Delete `syncRoomSelectDraft` and `onRoomSelectChange`** along with their comments without replacement. **`removeDevice`** loses its line `delete this.roomSelectDrafts[device.id];`, **`commissionDevice`** loses its call `this.syncRoomSelectDraft(device);` — in the comment above it, the sentence about the `roomSelectDrafts` entry must be struck; the rest of the reasoning (populate the object instead of replacing it, because of the reference held across `await` in `saveRoom`/`saveLabel`) remains correct and important.

- [ ] **Step 7: Delete obsolete tests**

Remove without replacement from `tests/api/test_web.py`:

- `test_the_room_select_uses_a_synced_draft_instead_of_reading_device_room_directly`
- `test_the_new_room_option_resets_the_draft_before_the_mode_starts`
- `test_the_room_select_leaves_new_room_mode_when_a_normal_room_is_picked`
- `test_the_room_picker_closes_new_room_mode_when_focus_leaves_it_entirely`

They check a mechanism that no longer exists. Do not rewrite: their shared claim — "the select list never shows a room the device doesn't have" — is no longer a checkable claim afterward, because there is no select list.

`test_the_page_offers_the_room_bar_and_the_room_picker` checks several things at once; remove only the assertions about the room select, keep the ones about the room bar, and rename it to `test_the_page_offers_the_room_bar`.

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web src/loxmatter/i18n tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): rooms are menu entries, select field removed without replacement

A click on a room assigns it and closes the menu. This removes
`roomSelectDrafts`, `syncRoomSelectDraft`, `onRoomSelectChange`, and
the focusout guard - all counterweights to a coupling that no longer
exists without a native `<select>`.

Four tests go away with them. They are deleted, not rewritten:
their shared claim ("the select list never shows a room the device
doesn't have") is not a checkable claim anymore once there is no
select list.

`newRoomFor` stays - now in the role it was originally meant for:
visibility of the text field, nothing else.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Check behavior in the browser, screenshots, wrap-up

**Files:**
- Modify: `scripts/capture_screenshots.py` (only if a selector breaks)
- Modify: `docs/screenshots/*.png`

**Interfaces:**
- Consumes: everything.
- Produces: nothing.

**Why this task exists:** The WebUI tests only prove that a construct is shipped. In the predecessor design, exactly this gap let through a bug where *every* tile permanently showed the wrong room, even though three review rounds had read the markup. The harness below is the countermeasure and costs minutes.

- [ ] **Step 1: Build the harness and run through the menu**

Use the existing demo server, don't build one yourself:

```bash
uv run python scripts/dev_web_server.py --demo --store-path /tmp/kebab-demo.sqlite --port 8422
```

Password `loxmatter-demo`, four devices with rooms (kitchen has two, so sorting within a group stays legible). Open it in the browser and read the DOM values, don't interpret the picture.

Only if a dedicated harness turns out to be needed after all: `/auth-info` and `/i18n` are fetched **without** the `/api` prefix, and `/api/devices/{id}/controls` returns `{commands, hidden_raw_commands}`, not a list — both cost time last time.

Check these six points and write the results into the report:

1. The ⋮ opens the menu; a click elsewhere closes it; Escape closes it.
2. A click on the ⋮ of a second tile closes the first one's menu.
3. The current room carries the checkmark — for a device with no room it sits at "No room".
4. A click on another room assigns it, the tile moves into the right group, the menu is closed.
5. "+ New room …": text field appears **in** the menu, the menu stays open while typing, Enter saves and closes, Escape only cancels new-room mode and leaves the menu open.
6. Give the last device of a filtered room a different room: `reconcileRoomFilter` still kicks in, the filter falls back to "All".

- [ ] **Step 2: Refresh screenshots**

```bash
uv run --with playwright python scripts/capture_screenshots.py
```

The script clicks via `nav.tabs button:has-text(…)`, the password field, and button texts — none of these selectors depend on the tile's interior, so it should run through unchanged. If one does break anyway, update the selector in the script, not the markup.

Afterward look at `docs/screenshots/dashboard.png` and check that the footer really only carries the note and the ⋮ now.

- [ ] **Step 3: All four gates**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

Expected: all four clean. Every failure gets fixed, not suppressed.

- [ ] **Step 4: Remove unused translation keys**

```bash
for key in $(grep -o '^web\.devices\.[a-z_.]*' src/loxmatter/i18n/strings.yaml | tr -d ':'); do
  grep -q "$key" src/loxmatter/web/index.html src/loxmatter/web/app.js || echo "UNUSED: $key"
done
```

Check every hit and remove it if truly nothing uses it anymore. Nothing is expected here — the rework keeps using the same keys — but the check costs one line.

- [ ] **Step 5: Commit**

```bash
git add -A docs/screenshots scripts
git commit -m "$(cat <<'EOF'
docs: bring screenshots up to date with the tile menu

The footer now carries only the export note and the kebab; the old
images showed the room select field.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```
