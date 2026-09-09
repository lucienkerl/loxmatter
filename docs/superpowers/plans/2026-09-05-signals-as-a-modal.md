# Signals as a modal in the device screen — Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The "Signals" view disappears as its own tab; editing individual signals happens in a modal, opened from the device tile's kebab menu and from the `+ N weitere Signale` link.

**Architecture:** A single `<dialog>` at the end of the page, outside every `x-for` loop. Alpine holds only the device **ID** (`signalsModalDevice`); `@close` is the only place that resets it. The signal row moves over unchanged from the old view. The global "Show expert" toggle gives way to a `<details>` per group — the open/closed state lives in the DOM instead of in Alpine.

**Tech Stack:** Alpine.js 3 (vendored under `web/vendor/alpine.min.js`), native `<dialog>` and `<details>`, FastAPI serves `index.html`/`app.js`/`style.css` statically, tests with pytest against the delivered text, behavior verification in the browser against the demo server.

**Design:** [docs/superpowers/specs/2026-09-05-signals-as-a-modal-design.md](../specs/2026-09-05-signals-as-a-modal-design.md)

## Global Constraints

- **The API stays untouched.** `GET /api/devices/{id}/signals`, `PATCH /api/signals/{key}`, `POST /api/signals/{key}/write` — no new route, no new field, no change to `src/loxmatter/api/`.
- **Comments in `app.js`, `index.html`, `style.css`, and in Python write umlauts as `ae`/`oe`/`ue`.** Only `strings.yaml` (user-facing text) and the documents under `docs/` carry real umlauts.
- **No value in `strings.yaml` may be wrapped as a whole in typographic quotation marks** (`„…“`, `“…”`). YAML only splits scalars on straight ASCII quotation marks; typographic ones land literally in the string. `tests/test_i18n.py::test_no_value_is_wrapped_in_typographic_quotes` catches it — it has already happened twice.
- **Every `web.*` key needs at least `en`.** Otherwise `de` falls back to `en`, never the other way around.
- **The WebUI tests only prove THAT something is delivered**, never that it works. Behavior is checked in task 4 in the browser against the vendored Alpine, not by reading.
- **Four gates at the end of every task:** `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`.
- **No `overflow: hidden` on `.device-card`, `.device-foot`, or `.tile-menu`**, and no new `opacity`/`filter`/`transform`/`contain` on these three — that silently clips or sinks the tile menu (see the comments in `style.css`).

## File Structure

| File | Role in this rework |
| --- | --- |
| `src/loxmatter/web/index.html` | Symbol `i-close`, kebab entry, new target of the `+ N` link, the `<dialog>` with its content; later removed: nav button and the signal `<section>` |
| `src/loxmatter/web/app.js` | `signalsModalDevice`, `signalsModalDeviceObject()`, `openSignalsModal()`, `closeSignalsModal()`, closing in `removeDevice`; later removed: `showExpertSignals` and the `selectView` branch |
| `src/loxmatter/web/style.css` | `.signals-modal*`, `.signal-group`; later removed: `.signal-group-toggle` |
| `src/loxmatter/i18n/strings.yaml` | three new keys; later removed: three old ones |
| `tests/api/test_web.py` | new assertions; later: three existing tests to update |
| `scripts/capture_screenshots.py` | `signals.png` will going forward come from the modal instead of the tab |
| `README.md` | caption of the Signals tile |

**Order and why:** Tasks 1 and 2 build the modal **next to** the still-existing tab — the application is never broken at any point, and a reviewer can judge the modal before anything is deleted. Only task 3 dissolves the tab.

---

### Task 1: Modal shell, state, and the two entry points

**Files:**
- Modify: `src/loxmatter/web/index.html` (symbol block at `i-rename`; kebab menu at `exportDevice(device)`; `+ N` link; new `<dialog>` before `<div class="toasts"`)
- Modify: `src/loxmatter/web/app.js` (state block "Signals"; new methods before `async loadSignals(deviceId)`; `removeDevice`)
- Modify: `src/loxmatter/web/style.css` (at the end of the file)
- Modify: `src/loxmatter/i18n/strings.yaml` (sections `web.devices` and `web.signals`)
- Test: `tests/api/test_web.py` (at the end of the file)

**Interfaces:**
- Consumes: `closeTileMenu(el)`, `t(key, values)`, `this.devices` (list of `{id, label, room, online, category, …}`) — all present.
- Produces:
  - `signalsModalDevice: string | null` — device ID of the open modal.
  - `signalsModalDeviceObject(): object | null` — resolves it against `this.devices`.
  - `openSignalsModal(device): void` — sets the ID, opens in `$nextTick`.
  - `closeSignalsModal(): void` — calls `close()` on the `<dialog>`.
  - i18n: `web.devices.menu_signals`, `web.signals.modal_heading` (placeholder `{device}`), `web.signals.modal_close`.
  - CSS classes: `.signals-modal`, `.signals-modal-body`, `.signals-modal-head`, `.signals-modal-close`.
  - SVG symbol `#i-close`.

- [ ] **Step 1: Write the three failing tests**

Append to the end of `tests/api/test_web.py`:

```python
async def test_exactly_one_signals_dialog_is_delivered(api):
    """Design section 4: ONE `<dialog>` for the whole page, not one per
    tile.

    Markup inside `x-for` is delivered once PER DEVICE - with thirty
    devices, thirty complete signal tables would sit in the document, and
    every `id` inside them thirty times over (the same pitfall that
    `aria-labelledby` in the tile menu already had to navigate around).
    Counting to 1 is the only assertion that would notice this regression
    at all: a `<dialog>` inside the tile would otherwise look exactly the
    same in the delivered text as one at the end of the page.

    The location check (after `</main>`) additionally proves that it sits
    outside the view sections and thus outside every device loop."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert markup.count("<dialog") == 1
    assert 'x-ref="signalsModal"' in markup
    assert markup.index("<dialog") > markup.index("</main>")


async def test_the_signals_modal_has_exactly_one_place_that_resets_its_state(api):
    """Design section 4: `@close` is the ONLY reset point.

    The event fires on every closing path - Escape, close button,
    backdrop, `close()` from JavaScript. A second reset on a single
    closing path would be exactly the spread across multiple handlers
    that cost six review rounds on the room selection field; that's why
    this test counts the occurrences instead of just searching for one.

    `@click.self` is mandatory for this, not decoration: a `<dialog>`
    does NOT close on a click on the backdrop by itself."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert '@close="signalsModalDevice = null"' in markup
    assert '@click.self="$el.close()"' in markup
    assert markup.count("signalsModalDevice = null") == 1


async def test_the_two_entry_points_open_the_signals_modal(api):
    """Design section 5: the modal has exactly two entry points.

    The kebab entry calls `closeTileMenu($el)` FIRST, then
    `openSignalsModal(device)` - this order carries the focus:
    `closeTileMenu` sets it on the `<summary>`, and the `showModal()` that
    immediately follows remembers exactly this focus as the return point.
    Reversed, the focus would land nowhere after the modal closed.

    The `+ N weitere Signale` link used to jump via `selectView('signals')`
    into a list of ALL devices, in which one had to search for one's own
    again - it now points to the device whose signals it promises."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert '@click="closeTileMenu($el); openSignalsModal(device)"' in markup
    assert "x-text=\"t('web.devices.menu_signals')\"" in markup
    assert '@click.prevent="openSignalsModal(device)"' in markup

    # Compare the order ONLY within the menu: the `+ N weitere Signale`
    # link sits further up in the same tile and calls the same method, so
    # a `markup.index(...)` over the whole page would hit it instead of
    # the menu entry and would always be true.
    menu_start = markup.index('<div class="tile-menu-items">')
    menu = markup[menu_start : markup.index("</details>", menu_start)]
    assert menu.index("openSignalsModal(device)") < menu.index("exportDevice(device)")


async def test_open_signals_modal_shows_the_dialog_only_after_alpine_rendered(api):
    """Design section 4: `showModal()` only in `$nextTick`.

    `showModal()` sets the initial focus on the first focusable element
    IN the dialog - and that only exists after Alpine has built the
    `x-if` content. Without `$nextTick` the dialog opens empty and the
    focus lands on the `<dialog>` itself; the first Tab key then starts
    at the beginning of the document instead of in the modal."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("openSignalsModal(device) {")
    end = script.index("\n    },", start)
    body = script[start:end]
    assert "this.signalsModalDevice = device.id;" in body
    assert "this.$nextTick(() => this.$refs.signalsModal.showModal());" in body


async def test_removing_a_device_closes_a_signals_modal_that_shows_it(api):
    """Design section 4, "When the device disappears".

    Without this call, a dialog would remain open over a device that no
    longer exists - and the `x-if` guard would turn it into an empty box
    with no discernible reason."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("async removeDevice(device) {")
    end = script.index("\n    },", start)
    body = script[start:end]
    assert "if (this.signalsModalDevice === device.id) {" in body
    assert "this.closeSignalsModal();" in body
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
uv run pytest tests/api/test_web.py -q -k "signals_dialog or resets_its_state or two_entry_points or only_after_alpine or closes_a_signals_modal"
```

Expected: 5 failed. The first fails on `assert 0 == 1` (no `<dialog>` in the document), the JavaScript tests on `ValueError: substring not found` from `script.index(...)`.

- [ ] **Step 3: Enter the three translation keys**

In `src/loxmatter/i18n/strings.yaml`, directly after `web.devices.menu_room_heading` (the last entry of the `web.devices` block):

```yaml
web.devices.menu_signals:
  en: "Edit signals…"
  de: "Signale bearbeiten…"
```

And in the `web.signals` block, directly after `web.signals.write_success`:

```yaml
web.signals.modal_heading:
  en: "Signals — {device}"
  de: "Signale — {device}"
web.signals.modal_close:
  en: "Close"
  de: "Schließen"
```

The placeholder `{device}` is unproblematic: `_web_strings()` (`src/loxmatter/api/language.py:56`) delivers unresolved templates via `raw_template()`, precisely so that `web.*` keys may carry placeholders. It is filled in the browser in `t()`.

- [ ] **Step 4: Add the close symbol**

In `src/loxmatter/web/index.html`, directly after the `</symbol>` of `i-rename`:

```html
      <symbol id="i-close" viewBox="0 0 24 24">
        <path d="M6 6l12 12" />
        <path d="M18 6L6 18" />
      </symbol>
```

Two lines, no `fill` — `.icon` sets `fill: none; stroke: currentColor`, a filled path icon would remain invisible here (see the comment on the `i-kebab` symbol).

- [ ] **Step 5: State and methods in `app.js`**

In `src/loxmatter/web/app.js`, in the state block directly after `rawWriteMessages: {},`:

```js
    // The signal modal holds the device ID, NOT the device object:
    // `loadDevices` replaces `devices` wholesale, so a held-onto object
    // would afterward be a corpse with a stale name and room.
    // `signalsModalDeviceObject()` resolves the ID against the current
    // list each time. This field is reset in EXACTLY ONE place, the
    // `@close` of the `<dialog>` in index.html - see the comment there.
    signalsModalDevice: null,
```

Directly before `async loadSignals(deviceId) {`, the three methods:

```js
    signalsModalDeviceObject() {
      return this.devices.find((device) => device.id === this.signalsModalDevice) || null;
    },

    /**
     * Opens the signal modal for a device.
     *
     * The `$nextTick` is mandatory, not style: `showModal()` sets the
     * initial focus on the first focusable element IN the dialog, and
     * that only exists after Alpine has built the `x-if` content.
     * Without waiting for it, the dialog opens empty, the focus stays on
     * the `<dialog>` itself, and the first Tab key starts at the
     * beginning of the document again.
     *
     * `$refs` is unproblematic here, even though the comment on the tile
     * menu (index.html, finding 3) explicitly advises against it: the
     * objection there hits a registration that runs PER TILE and
     * overwrites itself. This `<dialog>` sits exactly once in the
     * document - the same situation as `pinLogListToTop`, which already
     * uses `this.$refs` today for the same reason.
     */
    openSignalsModal(device) {
      this.signalsModalDevice = device.id;
      this.$nextTick(() => this.$refs.signalsModal.showModal());
    },

    /**
     * Closes the modal via the native `close()` method instead of
     * clearing the state directly: `close()` triggers the `close` event,
     * and its handler in index.html is the one place that resets
     * `signalsModalDevice`. Writing `this.signalsModalDevice = null`
     * here as well would again create two truths about the same state.
     */
    closeSignalsModal() {
      this.$refs.signalsModal.close();
    },
```

In `removeDevice`, directly after `delete this.signalsByDevice[device.id];`:

```js
        // Without this, a dialog would remain open over a device that no
        // longer exists - and the `x-if` guard in the modal would turn it
        // into an empty box with no discernible reason. `close()` is a
        // no-op if the dialog isn't open at all; the check still guards
        // it so that a modal over a DIFFERENT device doesn't close along
        // with it.
        if (this.signalsModalDevice === device.id) {
          this.closeSignalsModal();
        }
```

- [ ] **Step 6: Wire up the two entry points in `index.html`**

In the tile menu, **before** the "Export" button and directly after `<hr class="tile-menu-sep" />`:

```html
                        <button
                          class="tile-menu-item"
                          @click="closeTileMenu($el); openSignalsModal(device)"
                          x-text="t('web.devices.menu_signals')"
                        ></button>
```

The order in `@click` carries the focus: `closeTileMenu` closes the `<details>` and sets focus on its `<summary>`; the `showModal()` that immediately follows remembers exactly this focus as the return point. The same order as for "Export" and "Remove" next to it.

And on the `+ N weitere Signale` link in the value grid: `@click.prevent="selectView('signals')"` becomes

```html
                        @click.prevent="openSignalsModal(device)"
```

- [ ] **Step 7: Insert the `<dialog>`**

In `src/loxmatter/web/index.html`, directly **before** `<div class="toasts" aria-live="polite">` — i.e. outside the `</template>` that wraps the logged-in view, exactly like the toast notifications next to it:

```html
    <!--
      Signal modal (design "Signals as a modal", 2026-09-05). Exactly ONE
      `<dialog>` for the whole page, deliberately outside the `x-for`
      loop of the device tiles: markup inside `x-for` is delivered once
      PER DEVICE - with thirty devices, thirty complete signal tables
      would sit in the document, and every `id` inside them thirty times
      over (the same pitfall that `aria-labelledby` in the tile menu
      already had to navigate around).

      It also sits outside the login `<template>`, like the toast
      notifications below it: this way `$refs.signalsModal` is always
      resolvable and does not depend on whether Alpine has just rendered
      the logged-in subtree.

      `x-ref` is permitted here, even though the comment on the tile menu
      (finding 3) explicitly advises against it. The objection there hits
      a registration that runs PER TILE: the page shares a single
      `x-data` on the `<body>`, and the entry of the most recently
      rendered tile overwrites every one before it. This `<dialog>` sits
      exactly once in the document - no one can overwrite it. The same
      situation as `x-ref="diagnosticsLogsList"` and
      `x-ref="datagramsList"` further up, which are unproblematic for the
      same reason.

      `@close` is the ONLY place that resets `signalsModalDevice`. The
      event fires on every closing path - Escape, close button, backdrop,
      `close()` from JavaScript - so there is no path on which the
      Alpine state and the visible state can drift apart. The same role
      that `@toggle` plays for the tile menu's `<details>`. Do NOT add
      further resets on individual closing paths - exactly this spread
      across multiple handlers was the cause of six review rounds on the
      room selection field.

      A `<dialog>` does NOT close on a click on the backdrop by itself
      (unlike what Escape does). `@click.self` adds that: the content
      sits entirely in `.signals-modal-body`, so a click event with the
      `<dialog>` ITSELF as the target can only be the backdrop.
    -->
    <dialog
      x-ref="signalsModal"
      class="signals-modal"
      @close="signalsModalDevice = null"
      @click.self="$el.close()"
    >
      <template x-if="signalsModalDeviceObject()">
        <div class="signals-modal-body">
          <div class="signals-modal-head">
            <h2 x-text="t('web.signals.modal_heading', { device: signalsModalDeviceObject().label })"></h2>
            <span style="flex: 1 1 auto"></span>
            <button
              class="signals-modal-close"
              :title="t('web.signals.modal_close')"
              :aria-label="t('web.signals.modal_close')"
              @click="closeSignalsModal()"
            ><svg class="icon" aria-hidden="true"><use href="#i-close"></use></svg></button>
          </div>
        </div>
      </template>
    </dialog>
```

The body deliberately stays at just header and close button in this task — task 2 fills it. This way opening, closing, and focus return can be judged before content is added.

- [ ] **Step 8: Appearance**

At the end of `src/loxmatter/web/style.css`:

```css
/* Signal modal (design "Signals as a modal", 2026-09-05).
 *
 * `max-height` plus `overflow: auto` instead of a fixed height: a device
 * with forty attributes should scroll inside the modal, not grow past
 * the bottom edge of the screen, where the close button would then be
 * unreachable.
 *
 * The `::backdrop` gets NO color from the theme variables: it sits in
 * the top layer, outside the document tree, and does not reliably
 * inherit `:root` variables from there. A fixed, semi-transparent black
 * tone works in both themes. */
.signals-modal {
  width: min(46rem, 92vw);
  max-height: 85vh;
  overflow: auto;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
}

.signals-modal::backdrop {
  background: rgba(0, 0, 0, 0.45);
}

.signals-modal-body {
  padding: 1rem 1.2rem 1.2rem;
}

.signals-modal-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.signals-modal-head h2 {
  margin: 0;
}

.signals-modal-close {
  flex: none;
  background: none;
  border: 1px solid transparent;
  color: var(--text-muted);
  cursor: pointer;
  padding: 0.25rem;
  line-height: 0;
}

.signals-modal-close:hover {
  color: var(--text);
  border-color: var(--border);
}
```

- [ ] **Step 9: Run the tests**

```bash
uv run pytest tests/api/test_web.py -q -k "signals_dialog or resets_its_state or two_entry_points or only_after_alpine or closes_a_signals_modal"
```

Expected: 5 passed.

- [ ] **Step 10: All gates**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

Expected: all four clean. The "Signals" tab still exists and its tests still pass unchanged — that is correct at this point, task 3 clears it away.

- [ ] **Step 11: Commit**

```bash
git add src/loxmatter/web src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): Signal-Modal oeffnen und schliessen

Ein einziges <dialog> am Seitenende, erreichbar ueber einen neuen
Kebab-Eintrag und den "+ N weitere Signale"-Link. `@close` ist die
einzige Stelle, die `signalsModalDevice` zuruecksetzt; der Rumpf traegt
vorerst nur Kopfzeile und Schliessen-Knopf.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The content of the modal

**Files:**
- Modify: `src/loxmatter/web/index.html` (`.signals-modal-body`, from task 1)
- Modify: `src/loxmatter/web/style.css` (at the end of the file, following the block from task 1)
- Test: `tests/api/test_web.py` (at the end of the file)

**Interfaces:**
- Consumes: `signalsModalDeviceObject()` and `signalsModalDevice` (task 1); `signalGroupsFor(deviceId)` → `[{key, title, collapsible, signals}]`; `signalsByDevice`, `signalsError`, `titleDrafts`, `rawWriteDrafts`, `rawWriteBusyKey`, `rawWriteMessages`; `loadSignals`, `saveTitle`, `toggleExported`, `toggleResend`, `writeRaw`, `rawWriteMessageClass`, `liveValueOf`, `formatValue`, `signalIsFresh`, `signalAgeTitle` — all present unchanged.
- Produces: CSS class `.signal-group`; nothing else that a later task consumes.

- [ ] **Step 1: Write the failing tests**

At the end of `tests/api/test_web.py`:

```python
def _signals_dialog(markup: str) -> str:
    """The content of the signal modal, without the rest of the page.

    A bare `in markup` would also count the old signal section for as
    long as it still exists (task 3 only deletes it afterward) - and
    would afterward still hit the device tiles, which use the same
    helpers."""
    start = markup.index("<dialog")
    return markup[start : markup.index("</dialog>", start)]


async def test_the_signals_modal_carries_the_complete_signal_row(api):
    """Design section 2: the signal row moves over 1:1, without loss of
    functionality - title, export, resend, and raw-value writing
    included. These exact four write paths are what only the old view
    could do; if one falls off during the move, it is nowhere reachable
    anymore."""
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

    A `<dialog>` in the top layer covers everything below it, backdrop
    included - an error banner outside would be invisible during the one
    action that can trigger it (save title, set checkbox, write raw
    value). An invisible error is worse than none per spec 8.1."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    assert 'x-show="signalsError"' in dialog
    assert 'x-text="signalsError"' in dialog


async def test_both_signal_groups_share_one_details_template(api):
    """Design section 4, point 5: ONE template for both groups.

    Two forms (block here, `<details>` there) would mean two branches and
    in each its own copy of the signal-row template - exactly the
    duplication that `signalGroupsFor` abolished (51 duplicate lines, see
    its comment in app.js).

    The initial state runs via `x-init` and NOT via a bound `:open`:
    Alpine re-evaluates bindings whenever their dependencies change, and
    `signalGroupsFor` depends on `signalsByDevice` - a saved signal title
    would rewrite a `:open` and silently close the expert group right
    after it had been opened. This test is the only brake against a
    later, well-meant simplification to `:open`."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    assert 'x-for="group in signalGroupsFor(signalsModalDevice)"' in dialog
    assert dialog.count("<details") == 1
    assert 'x-init="$el.open = !group.collapsible"' in dialog
    assert ":open=" not in dialog
    assert "x-text=\"t('web.signals.functional_vs_expert_explanation')\"" in dialog
    assert "x-text=\"t('web.signals.none_functional')\"" in dialog
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
uv run pytest tests/api/test_web.py -q -k "complete_signal_row or error_banner_lives_inside or share_one_details"
```

Expected: 3 failed — `assert '@change="saveTitle(signal)"' in dialog` fails, the modal body so far only carries the header.

- [ ] **Step 3: Fill the body**

In `src/loxmatter/web/index.html`, add the content of `.signals-modal-body` directly after the `</div>` of `.signals-modal-head`:

```html
          <p x-show="signalsError" x-cloak class="banner danger" x-text="signalsError"></p>
          <p class="hint" x-text="t('web.signals.key_hint')"></p>

          <!-- Stays as a retry for the error case: in the normal case
               `startApp` has long since loaded the signals of every
               device, so this button only shows up if exactly this one
               fetch failed (the reason is in the banner above). -->
          <button
            x-show="!signalsByDevice[signalsModalDevice]"
            @click="loadSignals(signalsModalDevice)"
            x-text="t('web.signals.load_button')"
          ></button>

          <template x-if="signalsByDevice[signalsModalDevice]">
            <div>
              <!--
                ONE template for both groups. The obvious design -
                functional as a plain block, expert as `<details>` -
                would need two branches and in each its own copy of the
                signal row underneath. Exactly this duplication is what
                `signalGroupsFor` (app.js) abolished: 51 byte-identical
                lines that had to be kept in sync at both places on every
                change.

                The open/closed state lives in the DOM, not in Alpine -
                the same pattern as the tile menu, and the reason the old
                global toggle `showExpertSignals` is dropped with no
                replacement.

                `x-init` instead of `:open` is mandatory, not style:
                Alpine re-evaluates a BINDING whenever its dependencies
                change, and `signalGroupsFor` depends on
                `signalsByDevice` - a saved signal title would rewrite a
                `:open` and silently close the expert group right after
                it had been opened. `x-init` runs once per element; since
                `:key` with `group.key` is stable, Alpine does not
                rebuild the node on a re-render, and the user's click
                stays put. Do NOT simplify to `:open`.
              -->
              <template x-for="group in signalGroupsFor(signalsModalDevice)" :key="group.key">
                <details class="signal-group" x-init="$el.open = !group.collapsible">
                  <summary>
                    <span x-text="group.title"></span>
                    <span class="muted" x-text="'(' + group.signals.length + ')'"></span>
                  </summary>
                  <p
                    class="hint"
                    x-show="group.collapsible"
                    x-text="t('web.signals.functional_vs_expert_explanation')"
                  ></p>
                  <p
                    class="hint"
                    x-show="!group.collapsible && group.signals.length === 0"
                    x-text="t('web.signals.none_functional')"
                  ></p>
                  <template x-for="signal in group.signals" :key="signal.key">
                    <div class="device-controls">
                      <div class="row">
                        <span
                          class="key"
                          :title="t('web.signals.key_tooltip')"
                          x-text="signal.key"
                        ></span>
                        <input
                          type="text"
                          :value="signal.title"
                          @input="titleDrafts[signal.key] = $event.target.value"
                          @change="saveTitle(signal)"
                        />
                        <span class="hint" x-text="signal.path"></span>
                        <!--
                          When the value last arrived is in the `title` -
                          not next to it in the text flow (2026-09-03). A
                          statement like "7 s ago" changes its width every
                          second and thereby shifts the whole row; the eye
                          then follows the movement instead of the
                          change. `value-fresh` shows it instead: the
                          value lights up briefly and fades again,
                          without anything moving in the layout.
                        -->
                        <span
                          class="value"
                          :class="{ 'value-fresh': signalIsFresh(signal) }"
                          :title="signalAgeTitle(signal)"
                          x-text="formatValue(liveValueOf(signal)) + (signal.unit ? ' ' + signal.unit : '')"
                        ></span>
                        <label x-show="signal.exportable">
                          <input
                            type="checkbox"
                            :checked="signal.exported"
                            @change="toggleExported(signal)"
                          />
                          <span x-text="t('web.signals.export_checkbox')"></span>
                        </label>
                        <label x-show="signal.exportable">
                          <input
                            type="checkbox"
                            :checked="signal.resend"
                            @change="toggleResend(signal)"
                          />
                          <span x-text="t('web.signals.resend_checkbox')"></span>
                        </label>
                        <span
                          class="badge warn"
                          x-show="!signal.exportable"
                          x-text="signal.reason"
                        ></span>
                      </div>
                      <div class="row" x-show="signal.kind === 'attribute'">
                        <input
                          type="text"
                          :placeholder="t('web.signals.raw_write_placeholder')"
                          @input="rawWriteDrafts[signal.key] = $event.target.value"
                        />
                        <button
                          @click="writeRaw(signal)"
                          :disabled="rawWriteBusyKey === signal.key"
                          x-text="t('web.signals.raw_write_submit')"
                        ></button>
                        <span
                          x-show="rawWriteMessages[signal.key]"
                          :class="rawWriteMessageClass(signal)"
                          x-text="rawWriteMessages[signal.key] ? rawWriteMessages[signal.key].text : ''"
                        ></span>
                      </div>
                    </div>
                  </template>
                </details>
              </template>
            </div>
          </template>
```

- [ ] **Step 4: Appearance of the groups**

At the end of `src/loxmatter/web/style.css`, directly after the `.signals-modal-close:hover` block:

```css
/* The two signal groups in the modal. The default marker of a
 * `<summary>` has to be switched off twice: `list-style` applies in
 * Firefox and Chrome, the `::-webkit-details-marker` pseudo-element in
 * older WebKit versions - the same duplication as with
 * `.tile-menu > summary`. Here, though, the marker stays WANTED, because
 * both groups really are collapsible: only its own chevron replaces it,
 * so it looks the same in both browser families. */
.signal-group {
  margin-top: 1rem;
}

.signal-group > summary {
  cursor: pointer;
  font-weight: 600;
  padding: 0.3rem 0;
}

.signal-group > summary .muted {
  color: var(--text-muted);
  font-weight: 400;
  margin-left: 0.35rem;
}
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/api/test_web.py -q -k "complete_signal_row or error_banner_lives_inside or share_one_details"
```

Expected: 3 passed.

- [ ] **Step 6: All gates**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

Expected: all four clean.

If `test_the_signal_view_static_text_is_translated` (the old signal view) fails here, that is a sign of a typo in the new markup, **not** of an overdue rework — this test checks with `in markup` against the whole page and is not disturbed by additional, correct markup. Task 3 updates it.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/web tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): Signalzeilen und beide Gruppen ins Modal

Die Signalzeile zieht unveraendert um (Titel, Export, Resend,
Rohwert-Schreiben). Beide Gruppen teilen sich EIN <details>; der
Startzustand kommt aus x-init, nicht aus einem gebundenen :open - eine
Bindung wuerde die geoeffnete Expertengruppe bei jedem gespeicherten
Titel wieder zuklappen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Dissolve the tab

**Files:**
- Modify: `src/loxmatter/web/index.html` (nav button; the `<section x-show="view === 'signals'">`)
- Modify: `src/loxmatter/web/app.js` (`showExpertSignals`; the `signals` branch in `selectView`; the two helper comments that mention the toggle)
- Modify: `src/loxmatter/web/style.css` (`.signal-group-toggle`)
- Modify: `src/loxmatter/i18n/strings.yaml` (three keys)
- Modify: `tests/api/test_web.py` (three existing tests)

**Interfaces:**
- Consumes: everything from tasks 1 and 2.
- Produces: nothing new; removes `showExpertSignals` and the view value `'signals'`.

- [ ] **Step 1: Write the failing tests**

At the end of `tests/api/test_web.py`:

```python
async def test_the_signals_view_is_gone_from_navigation_and_markup(api):
    """Design section 3: the tab is dissolved with no replacement.

    Not only the nav button is checked, but also that NOWHERE ELSE is
    still switching to the view value `'signals'` - a leftover
    `selectView('signals')` would be a click that sends the application
    into a view that no longer exists: all sections would stay hidden,
    the page would be empty, with no error message.

    `showExpertSignals` falls along with it: the open/closed state now
    lives in the DOM (`<details>` in the modal), a global field for it
    would be a second truth with no reader."""
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

    `expert_collapsed_hint` is dropped with no replacement instead of
    moving over: "12 expert signals hidden" says the same thing as
    "Expert (12)" in the `<summary>`, just not at the place you click. A
    leftover key would not just be dead - its own text would point to a
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
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
uv run pytest tests/api/test_web.py -q -k "view_is_gone or dropped_signal_keys"
```

Expected: 2 failed — `assert "t('web.nav.signals')" not in page` and `assert "web.nav.signals" not in strings`.

- [ ] **Step 3: Remove the nav button and section**

In `src/loxmatter/web/index.html`, delete this line from `<nav class="tabs">`:

```html
      <button :class="{ active: view === 'signals' }" @click="selectView('signals')" x-text="t('web.nav.signals')"></button>
```

And delete the complete signal view: from the comment block

```html
      <!-- ================================================================
           View 2: Signals
           ================================================================ -->
```

up to and including the corresponding `</section>` — today lines 799–930. The following comment block "View 3: Export" becomes "View 2: Export"; adjust the numbering of the further view headings (System, Settings) down by one accordingly.

- [ ] **Step 4: Clean up `app.js`**

Delete `showExpertSignals: false,` along with the four-line comment above it ("Expert block (task 8): …") with no replacement.

In `selectView`, delete the entire `signals` branch — i.e. from

```js
      if (view === "signals") {
```

to the closing `} else if (view === "export") {`, which in the process becomes `if (view === "export") {`. The comment in the branch ("The complete tree, not only after a further click per device …") goes with it.

Finally, the two comments that name `showExpertSignals`. Without them, `test_the_signals_view_is_gone_from_navigation_and_markup` continues to fail (`assert "showExpertSignals" not in script` reads the whole file, comments included) — and worse: they would refer to a field that no longer exists.

Above `expertSignalsFor`, replace the third line. Previously:

```js
    // Signals view (task 8): "Functional" immediately shows what
    // `is_functional` classifies as wanted; "Expert" stays collapsed
    // until `showExpertSignals` switches that globally for all device
    // cards - the same data basis as above, just unfiltered by the
```

New:

```js
    // Signal modal: "Functional" immediately shows what `is_functional`
    // classifies as wanted; "Expert" stays collapsed until the user
    // expands the `<details>` in the modal (until 2026-09-05 a global
    // toggle did that for all devices at once)
    // - the same data basis as above, just unfiltered by the
```

And above `signalGroupsFor`, the second-to-last statement. Previously:

```js
    // template whether a block is hidden behind `showExpertSignals` and
    // shows its count in the heading - the rest (row markup, empty hint)
    // is identical for both groups.
```

New:

```js
    // template now only the initial state of the `<details>` (functional
    // open, expert closed, see `x-init` in index.html) - the rest
    // (row markup, empty hint) is identical for both groups. The first
    // sentence above has applied doubly since the modal rework: there,
    // both groups even share the same `<details>` markup, not just the
    // same row template.
```

The body of both functions stays unchanged.

- [ ] **Step 5: Clean up `style.css` and `strings.yaml`**

Delete `.signal-group-toggle` along with its comment block ("Signals view (task 8): the global toggle …").

Delete from `src/loxmatter/i18n/strings.yaml`: `web.nav.signals`, `web.signals.show_expert`, `web.signals.expert_collapsed_hint` — each with both language lines.

- [ ] **Step 6: Update the three existing tests**

In `tests/api/test_web.py`:

**a)** In `test_the_tab_bar_labels_are_translated` and `test_every_tab_button_binds_both_its_handler_and_its_label`, change the tuple `("devices", "signals", "export", "system", "settings")` to `("devices", "export", "system", "settings")` — both places.

**b)** In `test_the_bridge_ip_hint_splits_prefix_link_suffix_without_collapsing_to_x_html`, update the end anchor of the slice:

```python
    device_section_end = markup.index("x-show=\"view === 'export'\"")
```

And in this test's docstring, add a sentence about why the anchor moved:

```python
    The slice ends at the NEXT view, not at a closing tag:
    `"view === 'signals'"` was this anchor until the tab was dissolved
    (2026-09-05) - now it is `'export'`. An anchor on `</div>` or
    `</section>` would be unsuitable here, there are dozens of those in
    the device view.
```

**c)** `test_the_signal_view_static_text_is_translated` is redirected at the modal. Replace docstring and body with:

```python
async def test_the_signal_modal_static_text_is_translated(api):
    """Formerly `test_the_signal_view_static_text_is_translated`: the same
    assertions, now against the modal instead of against the dissolved
    tab (2026-09-05).

    Two of them have been dropped with no replacement: `show_expert` (the
    global toggle gives way to a `<details>` per group) and
    `expert_collapsed_hint` (whose text referred to exactly this toggle).
    The remaining hints, labels, and placeholders unchanged carry
    `t(...)` - none of the former German literals remain in the
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
    assert "x-text=\"t('web.signals.export_checkbox')\"" in dialog
    assert ">exportieren<" not in markup
    assert ":placeholder=\"t('web.signals.raw_write_placeholder')\"" in dialog
    assert "Rohwert schreiben" not in markup
    assert "x-text=\"t('web.signals.raw_write_submit')\"" in dialog
    assert ">Schreiben<" not in markup
```

- [ ] **Step 7: All gates**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

Expected: all four clean.

If `test_the_signal_group_titles_and_toggle_are_translated` breaks, it's because of its name, not its content: it checks `signalGroupsFor`'s group titles in `app.js`, and those stay. Then just bring its docstring up to date.

- [ ] **Step 8: Check for dead translation keys**

```bash
for key in $(grep -o '^web\.\(nav\|signals\|devices\)\.[a-z_.]*' src/loxmatter/i18n/strings.yaml | tr -d ':'); do
  grep -q "$key" src/loxmatter/web/index.html src/loxmatter/web/app.js || echo "UNBENUTZT: $key"
done
```

Expected: no output. Every hit is checked and removed if truly nothing uses it anymore.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'EOF'
refactor(web): Signale-Reiter aufloesen

Nav-Knopf, Section, der selectView-Zweig, showExpertSignals und
.signal-group-toggle entfallen; drei Uebersetzungsschluessel ohne Leser
gehen mit. Der Slice-Anker des Bruecken-IP-Tests wandert von 'signals'
auf 'export'.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Verify behavior in the browser, screenshots, docs

**Files:**
- Modify: `scripts/capture_screenshots.py`
- Modify: `docs/screenshots/signals.png`, `docs/screenshots/dashboard.png`
- Modify: `README.md:115–117`

**Interfaces:**
- Consumes: everything.
- Produces: nothing.

**Why this task exists:** The tests from tasks 1–3 read delivered text. They prove *that* something is delivered, never that it works. In the predecessor design, exactly this gap let a bug through where *every* tile permanently showed the wrong room, even though three review rounds had read the markup.

- [ ] **Step 1: Start the demo server and run through the modal**

Use the existing demo server, don't build one yourself:

```bash
uv run python scripts/dev_web_server.py --demo --store-path /tmp/signals-modal-demo.sqlite --port 8423
```

Password `loxmatter-demo`. Open it in the browser and read the DOM values, don't interpret the image.

Check these nine points and write the results into the report:

1. The kebab entry "Edit signals…" opens the modal; the menu is closed afterward.
2. Escape closes the modal, a click on the backdrop does too, so does the × button. After each of the three paths, `Alpine.$data(document.body).signalsModalDevice === null`.
3. After closing, the focus is back on the `<summary>` of the tile it was opened from (`document.activeElement.closest('.tile-menu')` is not `null`).
4. On opening, the focus sits **inside** the modal (`document.querySelector('.signals-modal').contains(document.activeElement)`) — the proof that the `$nextTick` works.
5. The functional group is open, the expert group is closed.
6. **The regression `x-init` guards against:** expand the expert group, then change a signal title in the modal and leave the field (`change` fires, `saveTitle` rewrites `signalsByDevice`). The expert group must **stay open**. If it collapses, a binding is at play somewhere after all.
7. An export checkbox can be set and the value survives closing and reopening.
8. Live values keep updating in the open modal (a `value-fresh` flash is visible).
9. The `+ N weitere Signale` link opens the same modal for the same device.

Only in case a harness of one's own turns out to be needed after all: `/auth-info` and `/i18n` are fetched **without** the `/api` prefix, and `/api/devices/{id}/controls` returns `{commands, hidden_raw_commands}`, not a list — both have already cost time twice.

- [ ] **Step 2: Switch the screenshot script over to the modal**

In `scripts/capture_screenshots.py`, replace the signals section. Previously:

```python
    select_view(page, "Signals")
    shoot(page, "signals")
```

New — indent by one level when inserting, the block sits in the body of `capture()`:

```python
# Signals no longer have their own tab (design "Signals as a modal",
# 2026-09-05) - the image now comes from the modal over the device
# grid. The path there is the same as for a user: kebab of the first
# tile, then the menu item.
page.click(".device-card .tile-menu > summary")
page.click('.tile-menu-item:has-text("Edit signals")')
page.wait_for_selector("dialog.signals-modal[open]", timeout=5000)
# Expand the expert group: collapsed, the image would show only two or
# three rows and a lot of empty space for the demo devices - but the
# whole point of this image is precisely the Loxone addresses and the
# export checkboxes next to each other.
page.click("dialog.signals-modal details:not([open]) > summary")
shoot(page, "signals")
page.keyboard.press("Escape")
page.wait_for_timeout(300)
```

And in the comment above `shoot(page, "dashboard")`, correct the list `Devices/Signals/Export/System/Settings` to `Devices/Export/System/Settings`.

- [ ] **Step 3: Refresh the screenshots**

```bash
uv run --with playwright python scripts/capture_screenshots.py
```

Expected: all seven images are rewritten, without timeout. Afterward, look at `docs/screenshots/signals.png` and `docs/screenshots/dashboard.png`: the first shows the modal with visible Loxone addresses and export checkboxes, the second a tab bar without "Signals".

- [ ] **Step 4: Update the caption in the README**

Replace `README.md:115–117`:

```markdown
<img src="docs/screenshots/signals.png" alt="Signal editor opened over the device grid, with Loxone addresses and export checkboxes" />

**Signals**<br>Open a device's signals from its tile menu: each signal with the Loxone address it will get and its own export checkbox; the administrative ones sit behind a collapsed expert section.
```

- [ ] **Step 5: All gates**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

Expected: all four clean. Every failure gets fixed, not suppressed.

- [ ] **Step 6: Commit**

```bash
git add -A docs/screenshots scripts README.md
git commit -m "$(cat <<'EOF'
docs: Screenshots und Produktseite auf das Signal-Modal nachziehen

Das Signals-Bild entsteht jetzt aus dem Modal ueber dem Geraeteraster
statt aus einem eigenen Reiter; das Skript klickt sich denselben Weg wie
ein Nutzer.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## What this plan deliberately does NOT do

- **No cross-device signal overview as a replacement.** The design (section 3, "The deliberate loss") explains why: comparing across all devices is the rare task. Should the need surface, it belongs in the export preview, not in a new tab.
- **No change to `signalGroupsFor`, `functionalSignalsFor`, `expertSignalsFor`.** They already deliver exactly what the modal needs.
- **No new route and no new API field.**
- **No remembering the expert group's open/closed state beyond closing.** That would again be a global field — exactly what `showExpertSignals` was.
