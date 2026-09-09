# Last heard on the device card — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the `last_heard` timestamp, already carried by `GET /api/devices`, on the device tile — so "is this device quiet or gone?" can be answered without `curl`.

**Architecture:** Three small pieces. A coarse time formatter that never shows seconds, because the tile already moved a per-second label out of the text flow once. A per-device mark of the newest live arrival, so the server's one-shot timestamp cannot go stale while values stream into the same tile. And one quiet line of markup between the tile's header and its value grid.

**Tech Stack:** Alpine.js (no build step, no bundler), plain JS in `src/loxmatter/web/app.js`, YAML strings in `src/loxmatter/i18n/strings.yaml`, `pytest` for the served-asset assertions.

**Design:** [docs/superpowers/specs/2026-09-09-last-heard-on-the-device-card-design.md](../specs/2026-09-09-last-heard-on-the-device-card-design.md)

## Global Constraints

- All commands run from the worktree root, never `cd` into the main checkout. Never `git stash` — the stash stack is shared with other sessions.
- **English everywhere** — code, comments, docstrings, test names, commit messages. The one exception this plan touches: the `de:` values in `src/loxmatter/i18n/strings.yaml` are shipped product and stay German. See `CLAUDE.md`.
- German `de:` values in this repository are written **without umlauts** (`gehoert`, `Bruecke`), matching every neighbouring entry.
- Line length 100 (`[tool.ruff] line-length = 100`).
- The checks: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run python scripts/check_language.py`, `uv run pytest`.
- The full suite takes about **three minutes** — it looks like a hang, it is not. Wait for it; do not return early.
- Baseline on this branch: **1467 passed, 2 skipped**. Each task says how far the number must rise.
- **No colour, no threshold, no warning pill.** A silent window contact is normal. The line states a fact; the reader judges.

## How this repository tests JavaScript

There is no JS test runner and no node step in CI. The established pattern — see `test_the_relative_time_and_header_helpers_are_translated` (`tests/api/test_web.py:1335`) — is to fetch the served `/static/app.js`, slice out a named function's body, and assert on what it contains. That catches a hardcoded translation or a wrong i18n key.

It does **not** prove the code runs. That gap is closed once, by hand, in Task 3: a throwaway node harness that actually evaluates the helpers. Its output is recorded in the report, and the harness is not committed — adding a JS test runner and a CI step for it is a separate decision, not this feature's to make.

> **Correction (final review, 9 September 2026).** Two things above are
> wrong, and the second one made the first one's conclusion unnecessary.
>
> 1. This repository *does* already run `app.js` in a committed test —
>    `test_the_signal_helpers_tolerate_null_without_throwing`
>    (`tests/api/test_web.py`), through the `_app_state` helper, which loads
>    the shipped file in `node` and calls `app()`. No runner, no bundler, no
>    CI step: `subprocess.run([node, "-e", ...])` plus a `skipif` when node
>    is missing. The claim that only text assertions existed was never true;
>    the paragraph above reads the convention off one half of it.
> 2. Because that harness exists, "the harness is not committed" is no
>    longer a decision worth defending. The final review's finding A3 closed
>    the gap for good instead of restating it: the two tests in the
>    "last heard … the shipped file, executed" section at the end of
>    `tests/api/test_web.py` exercise `lastHeardText` and the live message
>    handler on the real object `app()` returns.
>
> Both paragraphs stay as written — they are the reasoning the plan was
> executed under, and the throwaway harness *was* thrown away.

## Files

| File | Role | Task |
| --- | --- | --- |
| `src/loxmatter/i18n/strings.yaml` | three new keys | 1, 2 |
| `src/loxmatter/web/app.js` | `sinceTextCoarse`, `deviceHeardAt`, `lastHeardAt`, `lastHeardText` | 1, 2 |
| `src/loxmatter/web/index.html` | the line on the tile | 3 |
| `src/loxmatter/web/style.css` | its margins | 3 |
| `tests/api/test_web.py` | served-asset assertions | 1, 2, 3 |

---

### Task 1: A time label that never shows seconds

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml` (after `web.header.time_ago_hours`)
- Modify: `src/loxmatter/web/app.js` (after `sinceText`, currently ending near line 1971)
- Modify: `tests/api/test_web.py` (append)

**Interfaces:**
- Consumes: `this.nowTick` (a millisecond clock ticking once a second, `app.js:532`), and `t(key, values)`.
- Produces: `sinceTextCoarse(timestamp) -> string | null`. Task 2 calls it.

- [ ] **Step 1: Add the string**

In `src/loxmatter/i18n/strings.yaml`, directly after the `web.header.time_ago_hours` entry:

```yaml
web.header.time_ago_just_now:
  en: "just now"
  de: "gerade eben"
```

It sits with the other `time_ago_*` keys because that is where the coarse helper's remaining branches already live.

- [ ] **Step 2: Write the failing test**

Append to `tests/api/test_web.py`. This follows the file's established shape for JS helpers — fetch the served script, slice the function body, assert on it:

```python
async def test_the_coarse_age_helper_never_speaks_in_seconds(api):
    """`sinceTextCoarse` is the label that sits IN the tile's text flow.

    `sinceText` next to it stays as it is: it feeds a tooltip, where a
    width that changes every second costs nothing. In the flow it does -
    the tile carried such a label once and moved it into the tooltip on
    purpose, because a value counting up from "7s ago" shoves the row
    sideways and draws the eye to the motion instead of the change (see
    `signalSeenText` in app.js).

    So this helper must NOT reach for `web.header.time_ago_seconds`.
    """
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("sinceTextCoarse(timestamp) {")
    end = script.index("\n    },", start)
    body = script[start:end]

    assert 'return t("web.header.time_ago_just_now");' in body
    assert 'return t("web.header.time_ago_minutes", { minutes });' in body
    assert 'return t("web.header.time_ago_hours", { hours: Math.round(minutes / 60) });' in body
    # The whole point of the helper: no per-second branch.
    assert "time_ago_seconds" not in body
    # And no hardcoded translation, in either language.
    assert "just now" not in body
    assert "gerade eben" not in body
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
uv run pytest tests/api/test_web.py -k coarse_age_helper -v
```

Expected: **1 failed**, with a `ValueError: substring not found` from `script.index("sinceTextCoarse(timestamp) {")` — the helper does not exist yet.

- [ ] **Step 4: Write the helper**

In `src/loxmatter/web/app.js`, immediately after the closing `},` of `sinceText`:

```js
    /**
     * Like `sinceText`, but never in seconds: "just now", "3m ago",
     * "2h ago".
     *
     * The difference is not cosmetic. `sinceText` feeds a `title`, where a
     * label that changes width every second costs nothing. This one sits
     * in the tile's text flow, and the tile has been here before: a
     * per-signal age used to stand next to the value and was moved into
     * the tooltip precisely because counting up from "7s ago" changes the
     * label's width and shoves the row back and forth, drawing the eye to
     * the motion instead of to the change that matters (see
     * `signalSeenText`).
     *
     * Under a minute this is therefore a fixed string; from there it
     * changes at most once a minute. Reads `nowTick`, so Alpine redraws
     * it on its own.
     */
    sinceTextCoarse(timestamp) {
      if (!timestamp) {
        return null;
      }
      const seconds = Math.max(0, Math.round((this.nowTick - timestamp) / 1000));
      if (seconds < 60) {
        return t("web.header.time_ago_just_now");
      }
      const minutes = Math.round(seconds / 60);
      if (minutes < 60) {
        return t("web.header.time_ago_minutes", { minutes });
      }
      return t("web.header.time_ago_hours", { hours: Math.round(minutes / 60) });
    },
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
uv run pytest tests/api/test_web.py -k coarse_age_helper -v
```

Expected: **1 passed**.

- [ ] **Step 6: Prove the test bites**

Change the first branch in the helper to `return t("web.header.time_ago_seconds", { seconds });` and run the same command.

Expected: **1 failed**, on `assert "time_ago_seconds" not in body`. Revert and re-run: **1 passed**.

A test that only asserts the presence of three lines would pass just as well against a helper that also carries a seconds branch — the negative assertion is what makes it mean something, and this step is what proves the negative assertion is live.

- [ ] **Step 7: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py && uv run pytest
```

Expected: all green, **1468 passed, 2 skipped**.

```bash
git add src/loxmatter/i18n/strings.yaml src/loxmatter/web/app.js tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): add a coarse age label that never shows seconds

The tile is about to carry a "last heard" line in its text flow, and the
existing `sinceText` is the wrong tool for that: under a minute it counts up
in seconds, and a label that changes width every second shoves the row back
and forth. The tile learned this once already and moved the per-signal age
into a tooltip for exactly this reason.

`sinceTextCoarse` reads "just now" below a minute and changes at most once a
minute after that. `sinceText` stays as it is - in a tooltip a jittering
width costs nothing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The device's own last-heard timestamp

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml` (after `web.devices.offline`, currently near line 829)
- Modify: `src/loxmatter/web/app.js` (state next to `liveSeenAt: {}` near line 528; the websocket `message` handler near line 3555; the two new helpers after `sinceTextCoarse`)
- Modify: `tests/api/test_web.py` (append)

**Interfaces:**
- Consumes: `sinceTextCoarse(timestamp)` from Task 1.
- Produces: `lastHeardAt(device) -> number | null` (milliseconds) and `lastHeardText(device) -> string`. Task 3 binds the latter.

- [ ] **Step 1: Add the two strings**

In `src/loxmatter/i18n/strings.yaml`, directly after the `web.devices.offline` entry:

```yaml
web.devices.last_heard:
  en: "Last heard {text}"
  de: "Zuletzt gehoert {text}"
web.devices.never_heard:
  en: "Not heard since the bridge started"
  de: "Seit dem Start der Bruecke nichts gehoert"
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/api/test_web.py`:

```python
async def test_the_live_handler_credits_the_right_device(api):
    """A live message names its device in its key: `d<id>_<rest>`.

    The heartbeat (`bridge_alive`) belongs to no device (Spec 6.5) and
    must not count. It arrives every 30 seconds no matter what, so
    crediting it to anyone would make EVERY tile claim it had just been
    heard from - and the one statement this feature exists to make would
    become a lie on every card at once.
    """
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index('socket.addEventListener("message"')
    end = script.index("\n      });", start)
    body = script[start:end]

    assert 'const owner = /^d(\\d+)_/.exec(message.key);' in body
    assert "this.deviceHeardAt[Number(owner[1])] = now;" in body


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
    assert "Zuletzt gehoert" not in body
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run pytest tests/api/test_web.py -k "credits_the_right_device or later_of_the_served or last_heard_line_is_translated" -v
```

Expected: **3 failed**. The first on the two `assert ... in body` lines, the second and third with `ValueError: substring not found` — neither helper exists yet.

- [ ] **Step 4: Add the state**

In `src/loxmatter/web/app.js`, directly after `liveSeenAt: {},`:

```js
    // When something last arrived from a device, by device id. The
    // per-signal `liveSeenAt` above cannot answer this: asking "when did
    // I last hear from this DEVICE" would mean scanning every one of its
    // ~170 signal keys on every redraw, once a second, per tile.
    //
    // Never reset, exactly like `liveSeenAt` - a reconnect of the live
    // socket does not unmake the fact that something arrived earlier.
    deviceHeardAt: {},
```

- [ ] **Step 5: Credit the device in the message handler**

In the websocket `message` listener, directly after `this.liveSeenAt[message.key] = now;` and before the existing heartbeat branch:

```js
        // Which device a message belongs to is in its key: signal keys
        // start with `d<device id>_`. The heartbeat (`bridge_alive`)
        // matches no device on purpose - see the comment below for why it
        // is the honest sign of life, and exactly for that reason it must
        // not count here: it arrives every 30 seconds regardless, and
        // crediting it would make every tile claim it had just been heard
        // from.
        const owner = /^d(\d+)_/.exec(message.key);
        if (owner) {
          this.deviceHeardAt[Number(owner[1])] = now;
        }
```

- [ ] **Step 6: Write the two helpers**

In `src/loxmatter/web/app.js`, immediately after the closing `},` of `sinceTextCoarse`:

```js
    /**
     * When this device was last heard from, in milliseconds - the LATER
     * of two sources, or `null` when neither has anything.
     *
     * `device.last_heard` comes from the server, once, with
     * `GET /api/devices`. On its own it would go stale in the tile while
     * values stream into that very tile: confidently wrong, which is
     * worse than saying nothing. `deviceHeardAt` carries the live side.
     *
     * The served value is therefore only the starting point, for the
     * window between page load and the first live message from this
     * device - which is precisely the gap it exists to fill, because the
     * live bookkeeping starts empty on every page load and the server's
     * does not.
     */
    lastHeardAt(device) {
      const live = this.deviceHeardAt[device.id];
      const served = device.last_heard ? Date.parse(device.last_heard) : NaN;
      const candidates = [];
      if (live !== undefined) {
        candidates.push(live);
      }
      if (!Number.isNaN(served)) {
        candidates.push(served);
      }
      return candidates.length ? Math.max(...candidates) : null;
    },

    /**
     * The tile's line. A fact, not a judgement.
     *
     * No threshold and no colour anywhere near this: a silent window
     * contact is normal, and so is a silent button or leak detector. Any
     * staleness rule would fire first and most often on exactly the
     * devices that prompted this line, and a warning that cries wolf on
     * healthy hardware gets the next real one ignored too.
     *
     * The `null` branch is the valuable one. "Nothing since the bridge
     * started" is unambiguous - not "offline", not "no data" - and it is
     * the sentence that would have shortened 8 September, when a window
     * contact that only reports on change looked exactly like a button
     * whose subscription had been dead for five days.
     */
    lastHeardText(device) {
      const at = this.lastHeardAt(device);
      if (at === null) {
        return t("web.devices.never_heard");
      }
      return t("web.devices.last_heard", { text: this.sinceTextCoarse(at) });
    },
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
uv run pytest tests/api/test_web.py -k "credits_the_right_device or later_of_the_served or last_heard_line_is_translated" -v
```

Expected: **3 passed**.

- [ ] **Step 8: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py && uv run pytest
```

Expected: all green, **1471 passed, 2 skipped**.

```bash
git add src/loxmatter/i18n/strings.yaml src/loxmatter/web/app.js tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): resolve a device's last-heard timestamp

`device.last_heard` arrives once, with GET /api/devices. Shown on its own it
would claim "12m ago" while values stream into the very same tile -
confidently wrong, which is worse than saying nothing.

The interface therefore keeps its own per-device mark of the newest live
arrival and takes the later of the two. The device is derivable from the
message key (`d<id>_<rest>`); the heartbeat matches no device on purpose and
must not count, or every tile would claim it had just been heard from.

The `null` branch says "nothing since the bridge started" rather than
rendering an empty line - that is the statement the whole feature exists to
make.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: The line on the tile

**Files:**
- Modify: `src/loxmatter/web/index.html` (between the closing `</div>` of `.device-head` and `<div class="value-rows"`, currently near line 734)
- Modify: `src/loxmatter/web/style.css` (near the other `.device-card` rules, after the `.device-card.is-offline` block near line 1257)
- Modify: `tests/api/test_web.py` (append)

**Interfaces:**
- Consumes: `lastHeardText(device)` from Task 2.
- Produces: nothing further.

- [ ] **Step 1: Write the failing test**

Append to `tests/api/test_web.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/api/test_web.py -k last_heard_line_between -v
```

Expected: **1 failed**, on the first assertion — the binding is not in the page.

- [ ] **Step 3: Add the markup**

In `src/loxmatter/web/index.html`, after the `</div>` that closes `.device-head` and before `<div class="value-rows" ...>`:

```html
                  <!-- Device state, not export state. `.device-foot`
                       carries the export statement (`exportHintFor`), and
                       the header's own comment above already draws that
                       line - two timestamps about different subjects on
                       adjacent lines read as one muddled sentence.

                       Deliberately NOT inside `.device-ident` either,
                       although the row under the device name came free
                       when the primary signal was dropped (design
                       2026-09-07): that span is a flex row, and its
                       `flex: 1 1 auto` is what puts the offline pill on
                       the right (see the comment up in the header). A
                       column direction there would change how the name
                       `<input>` sizes itself - a larger blast radius than
                       one line of text is worth. As a sibling it touches
                       none of that reasoning.

                       No colour, no threshold, no pill. A silent window
                       contact is normal, and so is a silent button or
                       leak detector; any staleness rule would fire first
                       on exactly the devices that prompted this. The line
                       states a fact and leaves the judgement to whoever
                       reads it. -->
                  <p class="hint device-heard" x-text="lastHeardText(device)"></p>
```

- [ ] **Step 4: Add the style**

In `src/loxmatter/web/style.css`, after the `.device-card.is-offline .device-foot > *:not(.tile-menu)` block:

```css
/* `.hint` already carries the size and the muted colour; this rule only
 * takes away the paragraph's default margins, which would otherwise open
 * a gap the 260px tile cannot spare. */
.device-heard {
  margin: 0 0 0.35rem;
}
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
uv run pytest tests/api/test_web.py -k last_heard_line_between -v
```

Expected: **1 passed**.

- [ ] **Step 6: Run the bindings, do not just serve them**

Everything above proves the text was delivered. It does not prove a single expression evaluates — this interface has shipped bindings that were never executed. Close that gap once, by hand.

Write a throwaway harness (it is **not** committed; put it under the scratchpad, not in the repository) that runs the real helper bodies with a stub `t()` and a fixed `nowTick`.

"Paste the body from `app.js`" below means exactly that: copy the three function bodies as they now stand in `src/loxmatter/web/app.js` — they are reproduced verbatim in Task 1 step 4 and Task 2 step 6. Do not retype or paraphrase them; a harness that exercises a re-typed copy proves nothing about the shipped code.

```javascript
// node harness.mjs
const t = (key, values) =>
  values ? `${key}:${JSON.stringify(values)}` : key;

const app = {
  nowTick: 1_000_000_000_000,
  deviceHeardAt: {},
  sinceTextCoarse(timestamp) { /* paste the body from app.js */ },
  lastHeardAt(device) { /* paste the body from app.js */ },
  lastHeardText(device) { /* paste the body from app.js */ },
};

const cases = [
  ["neither source", { id: 1, last_heard: null }, null],
  ["served only", { id: 2, last_heard: new Date(app.nowTick - 120_000).toISOString() }, null],
  ["live only", { id: 3, last_heard: null }, app.nowTick - 5_000],
  ["live newer", { id: 4, last_heard: new Date(app.nowTick - 600_000).toISOString() }, app.nowTick - 5_000],
  ["served newer", { id: 5, last_heard: new Date(app.nowTick - 5_000).toISOString() }, app.nowTick - 600_000],
];
for (const [name, device, live] of cases) {
  if (live !== null) app.deviceHeardAt[device.id] = live;
  console.log(name.padEnd(16), "->", app.lastHeardText(device));
}
```

```bash
node harness.mjs
```

Expected, in order:

```
neither source   -> web.devices.never_heard
served only      -> web.devices.last_heard:{"text":"web.header.time_ago_minutes:{\"minutes\":2}"}
live only        -> web.devices.last_heard:{"text":"web.header.time_ago_just_now"}
live newer       -> web.devices.last_heard:{"text":"web.header.time_ago_just_now"}
served newer     -> web.devices.last_heard:{"text":"web.header.time_ago_just_now"}
```

The last two are the ones that matter: whichever source is newer wins, and the older one does not drag the label backwards. Record the actual output in the report verbatim. **If it differs from the above, that is a finding — report it rather than adjusting the expectation.**

- [ ] **Step 7: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py && uv run pytest
```

Expected: all green, **1472 passed, 2 skipped**.

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): show last heard on the device card

The value has reached the API since the reconnect work and no part of the
interface showed it. That is the gap that cost hours on 8 September: a window
contact that only reports on change looked exactly like a button whose
subscription had been dead for five days - both `online: true`, nothing on
the tile telling them apart.

The line sits between the header and the value grid. Not in the foot, which
carries the export statement: the tile's own comment already reserves the
header half for device state. Not inside `.device-ident` either, whose
`flex: 1 1 auto` is what puts the offline pill on the right.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Coverage against the design

| Design | Task |
| --- | --- |
| 3. Three cases, no threshold, no colour | Task 2, step 6 (`lastHeardText`); Task 3, step 3 (comment) |
| 4. Sibling after `.device-head`, not the foot, not `.device-ident` | Task 3, steps 1 and 3 |
| 5. Later of served and live; heartbeat excluded | Task 2, steps 5–6; harness in Task 3, step 6 |
| 6. No seconds in the label | Task 1 |
| 7. Three new strings | Task 1, step 1; Task 2, step 1 |
| 8. Helpers behave | Task 3, step 6 (harness) |
| 8. Bindings actually run | Task 3, step 6 (harness) |
| 8. Server side already covered | no task — `test_the_device_list_carries_last_heard_from_the_runtime` exists |
| 9. Not part of this design | no task — deliberate |

> **Correction (final review, 9 September 2026).** Two rows above were
> untrue as written, and they stay as the record of what was claimed.
>
> - *"8. Bindings actually run"* → Task 3, step 6 (harness). The design
>   (§8.2) requires the Alpine expressions be evaluated **against a real
>   DOM**. Step 6 ran a plain node harness over three copied function
>   bodies — no DOM, no Alpine, no `x-text` — so it could not have covered
>   this row at all. That requirement is still **not** met and is not
>   claimed to be: the tests added by finding A3 run the shipped `app.js`
>   without Alpine and without a DOM. What runs the bindings themselves
>   remains the delivery assertions, which prove the markup carries them.
> - *"8. Helpers behave"* and the harness half of *"5. Later of served and
>   live; heartbeat excluded"* → now covered permanently, and against the
>   actual file, by `test_the_shipped_last_heard_line_takes_the_newer_of_
>   the_two_sources` and
>   `test_the_shipped_live_handler_does_not_credit_the_online_key`
>   (`tests/api/test_web.py`). Row 5 also understated the design: the
>   heartbeat is not the only key that must be excluded — `d<id>_online`
>   matches the device key pattern and had to be excluded too (finding A1).
