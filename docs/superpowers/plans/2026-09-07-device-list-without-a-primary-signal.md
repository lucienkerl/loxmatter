# Device Tile Without a Primary Signal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The highlighted primary signal in the device tile's header goes away with no replacement; all functional signals sit at equal rank in the value grid.

**Architecture:** Pure removal. The value grid's `x-for` switches from `restSignalsFor` to `firstSignalsFor`, thereby showing the full short list instead of that same list without its first entry; the `.lead-value` block and the `.lead-label` span disappear from the header, and the offline pill moves into the space that frees up. Two Alpine methods and three CSS rules then fall away with no replacement. No new concept, no new colour, no new translation key is introduced.

**Tech Stack:** Static HTML with Alpine.js (vendored under `web/vendor/`), hand-written CSS with custom properties, inline SVG sprite. Tests: pytest + httpx against the ASGI app, checking the **delivered** markup and CSS; the three Node tests run `app.js` in a `node` process.

**Design:** [2026-09-07-device-list-without-a-primary-signal-design.md](../specs/2026-09-07-device-list-without-a-primary-signal-design.md)

## Global Constraints

- **Comments in source code without umlauts** — `ae`, `oe`, `ue`, `ss`. Only the documentation under `docs/` and the German text in `strings.yaml` carry real umlauts. (Consistent throughout the whole repo.)
- **`functionalSignalsFor()`, `firstSignalsFor()`, `remainingSignalCount()`, and `FUNCTIONAL_PREVIEW_LIMIT: 6` stay untouched.** Six signals per tile stay six signals per tile (design, section 3).
- **`formatValue()` stays untouched.** `wahr`/`falsch` stay labeled technically.
- **No new colour, no new translation key, no new file.** The design is a removal.
- **`.value-rows` stays at `font-size: 0.75rem`.** Raising it to `0.8rem` is the *correction for the case that the browser check in Task 4 makes it necessary* — not part of the planned change.
- **The null-tolerance of `signalIsFresh`, `signalAgeTitle`, and `liveValueOf` stays in place** (design, section 8). Only what their test hangs off of changes.
- **Tests check what's delivered, not what's rendered.** No engine that applies CSS or runs Alpine runs in this suite. Assertions run against `(await client.get("/")).text` and `(await client.get("/static/style.css")).text`.
- **Markup assertions go through `_without_comments()`** (helper near the top of `tests/api/test_web.py`). The comments in `index.html` name attributes by name, partly to explain why they *don't* appear there.
- **CI checks:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest -v`.
- **New tests get appended to `tests/api/test_web.py`.**

---

### Task 1: The value grid shows every signal, the primary signal leaves the markup

**Files:**
- Modify: `src/loxmatter/web/index.html:601-648` (header), `:652` (value grid's `x-for`), `:678-688` (hint about missing signals)
- Modify: `tests/api/test_web.py:2624-2630` (`test_the_page_offers_the_room_bar`), `:3040-3057` (delete)
- Test: `tests/api/test_web.py` (append)

**Interfaces:**
- Consumes: `firstSignalsFor(deviceId)` and `functionalSignalsFor(deviceId)` from `app.js` — both already exist unchanged.
- Produces: markup without the strings `lead-value`, `lead-label`, `leadSignalFor(`, and `restSignalsFor(`. Task 2 and Task 3 build on this.

- [ ] **Step 1: Write the four new tests**

Append to `tests/api/test_web.py`:

```python
async def test_the_value_grid_now_carries_every_functional_signal(api):
    """Design 2026-09-07, section 2: the highlighted primary signal goes
    away, all functional signals sit at equal rank in the value grid.
    `restSignalsFor` delivered the short list WITHOUT its first entry -
    that exact entry used to sit up in the header. With the header
    display gone, the grid has to run over the full list again, or the
    first signal would vanish with no replacement."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert 'x-for="signal in firstSignalsFor(device.id)"' in markup
    assert "restSignalsFor(" not in markup


async def test_the_tile_header_no_longer_carries_a_lead_value(api):
    """Neither the classes nor the call may be delivered. The test runs
    through `_without_comments`, because the justification in the markup
    still names the primary signal - precisely to explain why it no
    longer appears there."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "lead-value" not in markup
    assert "lead-label" not in markup
    assert "leadSignalFor(" not in markup


async def test_the_offline_pill_sits_in_the_header_not_under_the_name(api):
    """The pill moves into the primary signal's former place: third child
    of `.device-head`, no longer a child of `.device-ident` under the
    name (design, section 5). `margin-left: auto` on `.status-pill`
    pushes it to the right there with no rule of its own needed.

    The nesting is proven via the order in the delivered markup: between
    the name field and the pill there MUST be a closing `</span>` - the
    one belonging to `.device-ident`. If the pill is still inside, it's
    missing."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    name_end = markup.index('@change="saveLabel(device)"')
    pill = markup.index('<span class="status-pill off"', name_end)
    assert "</span>" in markup[name_end:pill], (
        "die Offline-Pille steht noch innerhalb von `.device-ident`"
    )


async def test_the_missing_signals_hint_no_longer_asks_for_a_lead(api):
    """The hint distinguishes "loaded, but empty" from "still loading"
    (Spec 8.1). What it hung off of used to be
    `!leadSignalFor(device.id)`; without a primary signal it queries the
    list directly."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert (
        'x-show="signalsByDevice[device.id] && functionalSignalsFor(device.id).length === 0"'
    ) in markup
```

- [ ] **Step 2: Run the four tests, they must fail**

Run: `uv run pytest tests/api/test_web.py -k "value_grid_now_carries or no_longer_carries_a_lead or offline_pill_sits_in_the_header or no_longer_asks_for_a_lead" -v`

Expected: 4 FAILED. `test_the_value_grid_now_carries_every_functional_signal` fails at `assert 'x-for="signal in firstSignalsFor(device.id)"' in markup`, the rest at their respective first assertion.

- [ ] **Step 3: Replace the header in `index.html`**

`src/loxmatter/web/index.html`, lines 601–648. Replace the entire block from `<div class="device-head">` to its matching `</div>` with:

```html
                  <div class="device-head">
                    <span class="type-badge">
                      <svg class="icon"><use :href="'#i-cat-' + device.category"></use></svg>
                    </span>
                    <span class="device-ident">
                      <input
                        type="text"
                        class="device-name"
                        :value="device.label"
                        @input="labelDrafts[device.id] = $event.target.value"
                        @change="saveLabel(device)"
                      />
                    </span>
                    <!-- The offline pill sits HERE, in the primary
                         signal's former place, since it went away (design
                         2026-09-07), and is no longer inside
                         `.device-ident` under the name. It therefore no
                         longer displaces anything: the `.lead-label` it
                         used to share the line with doesn't exist anymore
                         - and with it the rule "only the offline pill
                         displaces the primary-signal label", which used
                         to need two paragraphs of justification here.

                         No positioning rule of its own is needed:
                         `.status-pill` carries `margin-left: auto`
                         (style.css), and that pushes it to the right edge
                         in this flex header. As long as `.device-ident`
                         next to it has `flex: 1 1 auto`, the name stays
                         the part that shrinks - the pill is short and
                         isn't meant to shrink.

                         The changed-since-export pill still sits in the
                         FOOTER (pill-into-the-footer rework, 2026-09-06)
                         and deliberately not here: it answers the same
                         question as `exportHintFor` right next to it. The
                         header stays reserved for device state, not
                         export state. -->
                    <span class="status-pill off" x-show="!isOnline(device)">
                      <svg class="icon"><use href="#i-offline"></use></svg>
                      <span x-text="t('web.devices.offline')"></span>
                    </span>
                  </div>
```

- [ ] **Step 4: Switch the value grid's `x-for`**

Same file, in `<div class="value-rows" ...>`. Replace:

```html
                    <template x-for="signal in restSignalsFor(device.id)" :key="signal.key">
```

with:

```html
                    <!-- `firstSignalsFor`, not `restSignalsFor` (design
                         2026-09-07): the grid shows the full short list.
                         `restSignalsFor` delivered it without its first
                         entry, because that exact entry used to sit up in
                         the header - with the header display gone it
                         would otherwise no longer be visible anywhere. -->
                    <template x-for="signal in firstSignalsFor(device.id)" :key="signal.key">
```

- [ ] **Step 5: Re-hang the hint about missing signals**

Same file, lines 678–688. Replace the comment and the paragraph with:

```html
                  <!-- Finding 3: without this hint, a silent gap remained
                       between header and command bar whenever a device
                       had loaded but empty functional signals (the
                       `value-rows` then stay without content) -
                       indistinguishable from a device that's still
                       loading. The condition used to query
                       `!leadSignalFor(device.id)`; without a primary
                       signal it queries the list directly, which is the
                       more honest question anyway. -->
                  <p
                    class="hint"
                    x-show="signalsByDevice[device.id] && functionalSignalsFor(device.id).length === 0"
                    x-text="t('web.devices.no_functional_signals')"
                  ></p>
```

- [ ] **Step 6: Adjust the two legacy tests**

In `tests/api/test_web.py`, `test_the_page_offers_the_room_bar` (~line 2624): replace

```python
    assert "leadSignalFor(" in page
```

with

```python
    assert "firstSignalsFor(" in page
```

And delete `test_the_lead_label_only_yields_to_the_offline_pill_now` (~line 3040) entirely, docstring included. The test proved that the primary-signal label only ever yielded to the offline pill — there is no label left for anything to yield to.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest tests/api/test_web.py -v`

Expected: PASS. The four new tests from Step 1 are green, `test_the_page_offers_the_room_bar` stays green, `test_the_lead_label_only_yields_to_the_offline_pill_now` no longer exists. The Node tests (`test_a_device_without_a_lead_signal_…`, `test_a_signal_that_exists_…`) and the helper list are still green: `app.js` is unchanged, `leadSignalFor` still exists there, only the markup no longer calls it.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/index.html tests/api/test_web.py
git commit -m "feat(web): Werteraster zeigt alle Signale, Leitwert entfaellt

Das x-for laeuft ueber firstSignalsFor statt restSignalsFor, der
.lead-value-Block und das .lead-label-Span verlassen die Kopfzeile, die
Offline-Pille rueckt auf deren Platz.

Damit bekommt der Geraetename die ganze Kopfzeile: .lead-value trug
max-width: 50% und flex: 0 0 auto, der Name blieb an der
Grid-Untergrenze bei 65 px.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Delete `leadSignalFor` and `restSignalsFor`

**Files:**
- Modify: `src/loxmatter/web/app.js:1248-1265` (delete), `:1615-1620` (comment)
- Modify: `tests/api/test_web.py:704-747` (switch what it hangs off of), `:749-775` (one line), `:2595-2612` (helper list)

**Interfaces:**
- Consumes: the markup from Task 1, which no longer calls either method.
- Produces: an `app.js` without the strings `leadSignalFor` and `restSignalsFor`.

- [ ] **Step 1: Write the test that pins down the deletion**

Append to `tests/api/test_web.py`:

```python
async def test_the_lead_helpers_are_gone_from_the_script(api):
    """Design 2026-09-07, section 10: both methods go away with no
    replacement now that the markup no longer calls them. An unused
    method in `app.js` is not a harmless leftover - it invites the next
    rework to reintroduce the primary signal without having read the
    design."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    # Anchor on the DEFINITION, not on the bare name: the comment on
    # `signalIsFresh` still names `leadSignalFor` - precisely to explain
    # why its null-tolerance stays in place even though the caller is
    # gone. Unlike the markup, `app.js` has no `_without_comments` helper.
    assert "leadSignalFor(deviceId) {" not in script
    assert "restSignalsFor(deviceId) {" not in script
    assert "this.firstSignalsFor(deviceId).slice(1)" not in script
```

- [ ] **Step 2: Run the test, it must fail**

Run: `uv run pytest tests/api/test_web.py::test_the_lead_helpers_are_gone_from_the_script -v`

Expected: FAIL at `assert "leadSignalFor" not in script`.

- [ ] **Step 3: Delete the two methods from `app.js`**

`src/loxmatter/web/app.js`, lines 1248–1265. Delete the entire section — the separator comment line `// --- Leitwert (Kachel-Kopfzeile) ---…`, both explanatory comments, and both methods:

```javascript
    // --- Primary signal (tile header) --------------------------------------

    // The first functional signal in the order that `firstSignalsFor`
    // delivers anyway - i.e. the one from the profile table. Outlet ->
    // state, climate sensor -> temperature, blind -> position. No data
    // storage of its own, no configuration: a device with no functional
    // signals simply has no primary signal, and the header stays a
    // single line.
    leadSignalFor(deviceId) {
      return this.firstSignalsFor(deviceId)[0] || null;
    },

    // The rest of the short list. `FUNCTIONAL_PREVIEW_LIMIT` counts the
    // primary signal IN (design 6.2), so there's no second truncation
    // here - `firstSignalsFor` has already done it.
    restSignalsFor(deviceId) {
      return this.firstSignalsFor(deviceId).slice(1);
    },
```

No replacement. The `deviceGroups()` above and the next section below close up together.

- [ ] **Step 4: Update the comment on `signalIsFresh`**

Same file, ~line 1615. Replace within the comment block:

```javascript
      // `null` is a VALID argument here, not a programming error:
      // `leadSignalFor` delivers it for every device whose signals are
      // not yet loaded - and that is every device, between
      // `GET /api/devices` and `GET /api/devices/<id>/signals`, for at
      // least one rendering pass (2026-09-06).
```

with:

```javascript
      // `null` is a VALID argument here, not a programming error.
      // The caller that supplied it was `leadSignalFor` - for every
      // device whose signals were not yet loaded, i.e. between
      // `GET /api/devices` and `GET /api/devices/<id>/signals`, for
      // EVERY device, for at least one rendering pass (2026-09-06).
      //
      // This caller no longer exists since the primary signal was
      // dropped (design 2026-09-07): the value grid's `x-for` runs over
      // an empty list and evaluates nothing at all. The tolerance stays
      // in place nonetheless. Removing it because the one KNOWN caller is
      // gone would be the kind of cleanup that bites back at the next
      // caller - and the next one would rediscover the same bug, without
      // knowing the comment below.
```

The following two paragraphs of the comment (about `x-show` and about why the safeguard lives here rather than in the markup) stay unchanged.

- [ ] **Step 5: Shorten the helper list**

`tests/api/test_web.py`, ~line 2595. Delete the two lines from the `for name in (…)` tuple:

```text
        "leadSignalFor(",
        "restSignalsFor(",
```

The remaining thirteen entries stay unchanged.

- [ ] **Step 6: Re-hang the null-tolerance test onto the helpers themselves**

`tests/api/test_web.py`, ~line 704. Replace `test_a_device_without_a_lead_signal_does_not_throw_in_any_binding` entirely with:

```python
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
        assert call["ok"], f"{name} warf: {call.get('error')}"

    # What the tile shows in this state: no highlight, no tooltip - and the
    # dash that `formatValue` uses for "no value".
    assert values["calls"]["signalIsFresh"]["value"] is False
    assert values["calls"]["signalAgeTitle"]["value"] in (None, "")
    assert values["calls"]["liveValueOf"]["value"] is None
    assert values["formatted"] == "-"
```

- [ ] **Step 7: Detach the normal-case test from the primary signal**

Same file, `test_a_signal_that_exists_is_unaffected_by_the_guard` directly below. Delete the line in the JavaScript block

```javascript
          lead: state.leadSignalFor(1).key,
```

and, further down, the matching assertion

```python
    assert values["lead"] == "d1_1_onoff"
```

The remaining three assertions (`live`, `fresh`, `title`) stay unchanged — they are the actual point of the test.

- [ ] **Step 8: Run the suite**

Run: `uv run pytest tests/api/test_web.py -v`

Expected: PASS. In particular green: `test_the_lead_helpers_are_gone_from_the_script`, `test_the_signal_helpers_tolerate_null_without_throwing`, `test_a_signal_that_exists_is_unaffected_by_the_guard`, and the shortened helper list.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web/app.js tests/api/test_web.py
git commit -m "refactor(web): leadSignalFor und restSignalsFor loeschen

Beide hatten nach dem Umbau der Kopfzeile keinen Aufrufer mehr.

Der Test gegen die Null-Duldsamkeit der drei Signalhelfer haengt jetzt
an den Helfern selbst statt am Leitwert: die Duldsamkeit bleibt, nur ihr
bekannter Aufrufer ist weg.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Delete `.lead-value` and `.lead-label` from the stylesheet

**Files:**
- Modify: `src/loxmatter/web/style.css:1659-1667` (`.lead-label`), `:1669-1745` (comment block, `.lead-value`, `.lead-value small`), `:1615-1637` (comment on `.device-head .device-name`)
- Modify: `tests/api/test_web.py:3088-3140` (delete two tests), `:3143-3161` (docstring)

**Interfaces:**
- Consumes: the markup from Task 1, which no longer carries either class.
- Produces: a `style.css` without the strings `.lead-value` and `.lead-label`.

- [ ] **Step 1: Write the test**

Append to `tests/api/test_web.py`:

```python
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
```

- [ ] **Step 2: Run the test, it must fail**

Run: `uv run pytest tests/api/test_web.py::test_the_lead_rules_are_gone_from_the_stylesheet -v`

Expected: FAIL at `assert ".lead-value" not in css`.

- [ ] **Step 3: Delete the three rules**

`src/loxmatter/web/style.css`. Delete entirely, with no replacement:

- the rule `.lead-label { … }` (lines 1659–1667),
- the comment block right before it justifying `flex: 0 0 auto` versus `flex: 0 1 auto` (starts at `/* \`flex: 0 0 auto\` mit \`max-width\` statt \`flex: 0 1 auto\``, line ~1669),
- the rule `.lead-value { … }` (lines 1726–1737),
- the rule `.lead-value small { … }` (lines 1739–1744).

The next comment block after that (`/* Werteraster statt Chips: … */`) and the `.value-rows` rule stay unchanged — they are the reason the primary signal goes away, not its appendage.

- [ ] **Step 4: Update the comment on `.device-head .device-name`**

Same file, ~line 1622. Replace within the comment block above `.device-head .device-name` the sentence fragment

```
 * indicates that text is missing. At the documented grid lower bound
 * (261px, see `.lead-value` below) this field only gets 65px and
 * MUST truncate; the two properties at the end of this rule ensure
```

with

```
 * indicates that text is missing. At the documented grid lower bound
 * (261px), this field used to get only 65px, because `.lead-value`
 * next to it claimed half the header and did not shrink; since that was
 * dropped (design 2026-09-07) the name has the header to itself minus
 * the icon tile and spacing - except for a device that is
 * offline, where the status pill occupies the same space
 * (index.html, `.device-head`). It therefore now only has to truncate
 * for exceptionally long names, a bit sooner when offline - the two
 * properties at the end of this rule ensure
```

The rest of the block (about `min-width: 0` and the intrinsic `size=20` minimum width) stays **unchanged**: that justification still applies, the field remains an `<input>`.

- [ ] **Step 5: Delete the two CSS tests**

`tests/api/test_web.py`. Delete entirely, docstrings included:

- `test_lead_value_gets_padding_room_for_descenders` (~line 3088)
- `test_lead_value_does_not_yield_to_the_device_name` (~line 3111)

Both read `css.split(".lead-value {", 1)[1]` and would fail with an `IndexError` after Step 3 instead of an assertion — a test hanging off a deleted rule is no longer a test.

- [ ] **Step 6: Fix the docstring of the name test**

Same file, `test_device_name_truncates_with_an_ellipsis_instead_of_clipping` (~line 3143). The test **stays** — truncating with an ellipsis is still correct, just needed less often. Replace the first sentence of the docstring

```
    """Finding 1 (review from 2026-09-05): with the cap on `.lead-value`
    lowered to `50%` (see above), the name at the grid lower bound still
    carries truncation - just no longer the complete kind. An
```

with

```
    """Finding 1 (review from 2026-09-05), followed up on 2026-09-07: since
    the removal of `.lead-value`, the name has the header row to itself
    and only truncates for exceptionally long names. That it then does
    so VISIBLY remains the assertion of this test. An
```

The rest of the docstring and both assertions stay unchanged.

- [ ] **Step 7: Run the full suite and the linters**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -v`

Expected: all four PASS. `test_the_lead_rules_are_gone_from_the_stylesheet` is green, the two deleted tests no longer appear, `test_device_name_truncates_with_an_ellipsis_instead_of_clipping` stays green.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "style(web): .lead-value und .lead-label loeschen

Beide Klassen stehen in keinem Markup mehr. Mit ihnen entfallen die
Begruendung zu flex: 0 0 auto gegen flex: 0 1 auto und das
padding-block gegen abgeschnittene Unterlaengen - beides beschrieb ein
Element, das es nicht mehr gibt.

Der Kommentar an .device-head .device-name behaelt seinen Teil ueber
min-width: 0: das Feld bleibt ein <input>, die intrinsische
size=20-Mindestbreite bleibt abzuraeumen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Check in the browser and update the screenshots

The suite proves **that** the changed markup and CSS are delivered — not what it looks like. The three open questions from section 9 of the design can only be answered in an engine.

No home-grown harness: the repo already has the path for this. `scripts/dev_web_server.py --demo` starts a UI with seeded demo data and fixed timestamps, and `scripts/capture_screenshots.py` drives exactly that with Playwright. Playwright is an ad-hoc dependency and deliberately not in `pyproject.toml`.

**Files:**
- Modify: `docs/screenshots/dashboard.png` (shows `.room-bar` down to the second `.device-grid`, i.e. the device tiles)
- Modify (only if question 1 requires it): `src/loxmatter/web/style.css`, `tests/api/test_web.py`

- [ ] **Step 1: Start the demo UI**

```bash
uv run python scripts/dev_web_server.py --demo
```

Expected: a server at `http://127.0.0.1:8420`, password `loxmatter-demo`. Check in the browser that the demo data contains at least one device with several functional signals, one without signals, and one that's offline. If one of these cases is missing, cover it in Step 2 via `evaluate` by setting `state.signalsByDevice` directly in the browser — there's no need to change the demo database for that.

- [ ] **Step 2: Measure the three questions**

In the browser console on the device view:

```javascript
// Question 2: do the footers of a row align?
const feet = [...document.querySelectorAll('.device-grid .device-foot')]
  .map((el) => Math.round(el.getBoundingClientRect().top));
console.log('Fusszeilen-Oberkanten:', feet);

// Question 3: how tall is a tile now?
const cards = [...document.querySelectorAll('.device-grid .device-card')];
console.log('Kachelhoehen:', cards.map((el) => el.offsetHeight));

// Question 3, cross-check: the same measurement on the state BEFORE this
// change (`git stash push -u -m "leitwert-messung"`, measure, `git stash
// apply <sha>`) - the difference per tile should be checked against the
// roughly +10px from section 7 of the design.
```

Evaluation:

1. **Does the tile without the large value still read at a glance?** A judgment call, not a measurement. If not: raise `.value-rows` — **not** bring back the primary signal (design, section 9). Continue with Step 3.
2. **Do the footers align?** All values from `feet` within one grid row must be equal. `align-items: stretch` on the grid and `.device-foot { margin-top: auto }` are meant to keep delivering that; the card has one fewer child than before, so this needs to be measured again, not assumed.
3. **Height difference per tile** against the rough estimate of about +10px. Only the order of magnitude matters. If it deviates sharply, the number in section 7 of the design needs correcting — not the change itself.

The worktree rule applies when stashing: `git stash push -u -m "leitwert-messung"`, note the SHA from `git stash list --format='%H %gs'`, restore it with `git stash apply <sha>`, then discard that entry specifically. No plain `git stash pop`.

- [ ] **Step 3: Only if question 1 requires it — raise the font size, with a test**

Both grid cells together, otherwise the title would end up larger than its value. In `src/loxmatter/web/style.css`, `.value-key` and `.value-rows .value` from `font-size: 0.75rem` to `0.8rem`, and the test for it, appended to `tests/api/test_web.py`:

```python
async def test_the_value_grid_type_was_raised_after_the_browser_check(api):
    """Task 4, question 1: without the large primary signal the tile no
    longer read at a glance. The correction is a font-size step in the
    value grid, not the return of the primary signal (design, section 9).

    BOTH grid cells, not just one: title and value sit on the same
    line, a one-sided increase would put the title larger than its
    value. What's proven is the delivered rule, not readability - the
    measurement for that lives in the task report."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    key_rule = css.split(".value-key {", 1)[1].split("}", 1)[0]
    value_rule = css.split(".value-rows .value {", 1)[1].split("}", 1)[0]
    assert "font-size: 0.8rem" in key_rule
    assert "font-size: 0.8rem" in value_rule
```

If the answer to question 1 comes out positive, this step is dropped with no replacement — then there is nothing to correct and nothing to prove. After the change: `uv run pytest tests/api/test_web.py -v`, expected PASS.

- [ ] **Step 4: Recapture the screenshots**

```bash
uv run --with playwright python scripts/capture_screenshots.py
```

The script starts `dev_web_server.py --demo` itself — stop the server from Step 1 first, or port 8420 is taken.

Expected: `docs/screenshots/dashboard.png` changes (it shows `.room-bar` down to the second `.device-grid`, i.e. the device tiles). `system.png` changes on every run because its command log shows the HTTP requests of the capture itself — **discard** it, don't commit it. The remaining five images must stay byte-identical; a diff there means a real, unintended change to a different view.

Run: `git status --short docs/screenshots/`
Expected: only `dashboard.png` as changed (after discarding `system.png`).

- [ ] **Step 5: Commit**

```bash
git add docs/screenshots/dashboard.png
git commit -m "docs(screenshots): Geraetekacheln ohne Leitwert nachziehen

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

If Step 3 produced a correction, it belongs in its own commit before this one:

```bash
git add src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "style(web): Werteraster auf 0.8rem anheben

Ohne den grossen Leitwert las die Kachel bei 0.75rem nicht mehr auf
einen Blick (Messung im Aufgabenbericht). Titel und Wert steigen
gemeinsam, sonst stuende der Titel groesser als sein Wert.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## What this plan deliberately does not do

- **Turn the device name into plain text.** The design, section 6, rejects that: the width argument doesn't hold, because `min-width: 0` on `.device-head .device-name` has long since cleared the intrinsic `size=20` minimum width. What would remain is a cosmetic gain against a new menu entry, a language key in both languages, and a state field per open rename.
- **Compact the list.** This change makes the tile taller, not shorter (design, section 7). That is known and accepted.
- **Touch `FUNCTIONAL_PREVIEW_LIMIT`.** Six signals stay six signals.
