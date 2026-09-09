# Pairing Code Entry: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The commissioning field writes the pairing code the way it appears on the device (`1234-567-8901`), names what it has recognized, and the bridge strips the separators before the Matter stack itself sees them.

**Architecture:** Three pure module-level functions in `web/app.js` carry the whole frontend logic (formatting, normalizing, describing); the Alpine component calls them from two event handlers. In the backend, a `field_validator` on `CommissionRequest.code` mirrors the same normalization rule, so it applies to every caller of the route and not only to our UI.

**Tech Stack:** Python 3.12 / FastAPI / Pydantic v2 / pytest · Alpine.js (no build step, no JS test framework) · Playwright only for the screenshots

**Design:** [`docs/superpowers/specs/2026-09-07-pairing-code-entry-design.md`](../specs/2026-09-07-pairing-code-entry-design.md)

## Global Constraints

- **Comments and docstrings in English, no umlauts in source code** (`Geraet`, `zurueck`) — as throughout the repo. User-facing text in `strings.yaml`, by contrast, carries real umlauts.
- **All `web.*` keys in both `en` AND `de`.** `tests/test_i18n.py::test_web_namespace_has_no_missing_english_fallback_gaps` enforces at least `en`.
- **No typographic quotation marks as the outermost characters of a `strings.yaml` value** — `tests/test_i18n.py::test_no_value_is_wrapped_in_typographic_quotes` otherwise fails.
- **Colours only from the existing CSS variables** (`--ok`, `--warn`, `--danger`, `--off`, `--border`, `--bg`, `--text`, `--text-muted`, `--accent`). No new colour.
- **Green after every task:** `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`.
- **The rule exists twice** — once in JS, once in Python. Both versions must do exactly the same thing character for character; whoever changes one changes the other.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/loxmatter/api/models.py` | `field_validator` on `CommissionRequest.code` — the normalization for every caller | 1 |
| `tests/api/test_devices.py` | Proof that separators don't reach the stack | 1 |
| `src/loxmatter/web/app.js` (module level) | The three pure functions: format, normalize, describe | 2 |
| `src/loxmatter/web/app.js` (in `app()`) | Two event handlers and the chip state | 3 |
| `src/loxmatter/web/index.html` | Field, chip, example line, sketch | 3, 4 |
| `src/loxmatter/i18n/strings.yaml` | Nine new keys, one changed | 3, 4 |
| `src/loxmatter/web/style.css` | `.code-detect`, `.code-examples`, `.code-sticker` | 3, 4 |
| `scripts/capture_screenshots.py` | Selector that currently hangs off the placeholder | 5 |
| `docs/screenshots/commissioning.png` | The image of this card | 5 |

---

### Task 1: Normalization in the backend

Comes first because it already works on its own: after this, a manually issued call can carry the code as typed, independent of any UI.

**Files:**
- Modify: `src/loxmatter/api/models.py:26` (import), `src/loxmatter/api/models.py:198-217` (`CommissionRequest`)
- Test: `tests/api/test_devices.py` (at the end of the commissioning tests, after `test_commissioning_a_device_registers_it` at line 258)

**Interfaces:**
- Consumes: nothing
- Produces: `CommissionRequest.code` is trimmed after validation and digit-pure for the numeric code. `api/devices.py:419` passes it on unchanged — no character changes there.

- [ ] **Step 1: Write the four tests**

Append to `tests/api/test_devices.py`:

```python
async def test_a_pairing_code_with_dashes_reaches_the_stack_without_them(api):
    """The case this is about: this is how the code appears on the device,
    and this is how everyone types it in. Until now nobody stripped the
    separators - not even `MatterClient.commission_with_code`, which puts
    the string unchanged into the WebSocket command."""
    client, _, _, fake_client = api
    response = await client.post("/api/devices/commission", json={"code": "1234-567-8901"})
    assert response.status_code == 200
    assert fake_client.commissioned == ["12345678901"]


async def test_a_qr_code_reaches_the_stack_untouched(api):
    """The MT: text is Base38-encoded - a dash in it carries meaning.
    Normalization must therefore leave it alone."""
    client, _, _, fake_client = api
    response = await client.post(
        "/api/devices/commission", json={"code": " MT:Y.K90SO527JA0648G00 "}
    )
    assert response.status_code == 200
    assert fake_client.commissioned == ["MT:Y.K90SO527JA0648G00"]


async def test_spaces_inside_a_pairing_code_are_removed_as_well(api):
    """Anyone copying from a manual often brings spaces instead of
    dashes."""
    client, _, _, fake_client = api
    response = await client.post("/api/devices/commission", json={"code": "3497 011 2332"})
    assert response.status_code == 200
    assert fake_client.commissioned == ["34970112332"]


async def test_an_overlong_code_is_passed_on_rather_than_rejected(api):
    """The validator normalizes, it does NOT validate (design section 8):
    the setup code forms are decided by the Matter stack, not this bridge.
    An overlong code therefore passes through and fails where it belongs."""
    client, _, _, fake_client = api
    response = await client.post(
        "/api/devices/commission", json={"code": "1234-567-8901-2345-678-9012"}
    )
    assert response.status_code == 200
    assert fake_client.commissioned == ["1234567890123456789012"]
```

- [ ] **Step 2: Run the tests, confirm failure**

```bash
uv run pytest tests/api/test_devices.py -k "pairing_code or qr_code_reaches or spaces_inside or overlong" -v
```

Expected: 4 FAILED. The first with `AssertionError: assert ['1234-567-8901'] == ['12345678901']` — the code arrives unfiltered today.

- [ ] **Step 3: Write the validator**

In `src/loxmatter/api/models.py`, line 26, extend the import:

```python
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator
```

Set the two patterns above `class CommissionRequest`:

```python
# Anything that is NOT a digit, whitespace, or dash turns the value into a
# QR payload (`MT:...`, Base38). A dash INSIDE that carries meaning and
# must not be dropped - which is why this pattern decides first, before
# anything gets stripped at all.
_QR_PAYLOAD = re.compile(r"[^0-9\s-]")
_MANUAL_CODE_SEPARATORS = re.compile(r"[\s-]")
```

And in `CommissionRequest`, after `code: str`:

```python
    @field_validator("code")
    @classmethod
    def _strip_separators(cls, value: str) -> str:
        """Accepts the code the way it appears on the device.

        There it is grouped - `1234-567-8901` - and that is exactly how
        everyone types it in. On the way to the Matter stack nobody else
        strips the separators: `api.devices` passes the value on unchanged
        to `BridgeMatterClient.commission_with_code`, and
        `MatterClient.commission_with_code` likewise puts it unchanged into
        the WebSocket command (checked against the installed version,
        `matter_server/client/client.py:140`).

        The validator ONLY NORMALIZES, it does not validate (design
        section 8): the valid forms are decided by the Matter stack. If
        the rule lived here, this bridge could reject a code the stack
        would have accepted - with no way around it. Stripping separators
        is lossless; a length rule would be a bet.
        """
        text = value.strip()
        if _QR_PAYLOAD.search(text):
            return text
        return _MANUAL_CODE_SEPARATORS.sub("", text)
```

Also update the class docstring starting at `src/loxmatter/api/models.py:199` to match the new state — its first sentence currently claims the 21-digit form is the `MT:` code; those are two different things:

```python
    """`POST /api/devices/commission` - the pairing code from the device
    or its packaging (Spec 7.1). Two forms: the numeric code (11 digits,
    printed on the device as `1234-567-8901`, more rarely 21 digits) or
    the text behind the QR code (`MT:...`).

    `code` is normalized on arrival, see `_strip_separators`.
```

- [ ] **Step 4: Run the tests, confirm success**

```bash
uv run pytest tests/api/test_devices.py -v
```

Expected: all PASSED. In particular the existing commissioning tests stay green — they send `MT:ABC123`, a QR payload the validator doesn't touch.

- [ ] **Step 5: The whole suite and the checkers**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/api/models.py tests/api/test_devices.py
git commit -m "fix(api): Trenner aus dem Pairing-Code schneiden

Bis hierher reichte die Route den Code ungefiltert bis in den
WebSocket-Befehl von matter-server durch - wer ihn so abtippte, wie er
auf dem Geraet steht, scheiterte ohne deutbare Meldung.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The pure functions in app.js

Three functions with no DOM reference, so they can be checked individually. `app.js` has **no side effects at module level** (the file ends with `function app() {...}`, every `addEventListener` sits inside a function) — it can therefore be loaded in Node and the functions actually run through, instead of only clicking through them in the browser.

**Files:**
- Modify: `src/loxmatter/web/app.js` — new block directly before `const VIEWS = [...]` (line 317)
- Test: throwaway check script in the scratchpad, does not go into the repo

**Interfaces:**
- Consumes: nothing
- Produces:
  - `isPairingQrCode(raw: string) -> boolean`
  - `formatPairingCode(raw: string) -> string` — for display
  - `normalizePairingCode(raw: string) -> string` — for transmission
  - `describePairingCode(raw: string) -> { key: string, values: object, tone: "ok"|"warn"|"bad"|"idle" }` — `key` is a `strings.yaml` key, `values` are its placeholders; translation only happens in Task 3, so these functions stay checkable without a loaded language table

- [ ] **Step 1: Write the check script**

To `/tmp/pairing-probe.mjs` (throwaway, does not go into the repo):

```js
import fs from "node:fs";
import vm from "node:vm";

const src = fs.readFileSync("src/loxmatter/web/app.js", "utf8");
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(
  src + "\n;globalThis.probe = { isPairingQrCode, formatPairingCode, normalizePairingCode, describePairingCode };",
  sandbox,
);
const { isPairingQrCode, formatPairingCode, normalizePairingCode, describePairingCode } = sandbox.probe;

let failures = 0;
function check(label, actual, expected) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  if (a !== e) {
    console.error(`FAIL ${label}\n  erwartet ${e}\n  bekommen ${a}`);
    failures++;
  } else {
    console.log(`ok   ${label}`);
  }
}

// --- formatPairingCode: the 4-3-4 grouping ---
check("leer", formatPairingCode(""), "");
check("vier Ziffern", formatPairingCode("1234"), "1234");
check("fuenfte Ziffer oeffnet die zweite Gruppe", formatPairingCode("12345"), "1234-5");
check("siebte Ziffer schliesst die zweite Gruppe", formatPairingCode("1234567"), "1234-567");
check("achte Ziffer oeffnet die dritte", formatPairingCode("12345678"), "1234-567-8");
check("elf Ziffern", formatPairingCode("12345678901"), "1234-567-8901");
check("schon gruppiert bleibt gleich", formatPairingCode("1234-567-8901"), "1234-567-8901");
check("Leerzeichen werden zu Bindestrichen", formatPairingCode("3497 011 2332"), "3497-011-2332");

// --- from the twelfth digit on, grouping STOPS ---
check("zwoelfte Ziffer haengt an", formatPairingCode("123456789012"), "1234-567-89012");
check(
  "einundzwanzig Ziffern",
  formatPairingCode("123456789012345678901"),
  "1234-567-89012345678901",
);
check(
  "zweiundzwanzig Ziffern werden NICHT abgeschnitten",
  formatPairingCode("1234567890123456789012"),
  "1234-567-890123456789012",
);

// --- QR payload is left untouched ---
check("MT-Code", formatPairingCode("MT:Y.K90SO527JA0648G00"), "MT:Y.K90SO527JA0648G00");
check("erstes M schaltet schon um", formatPairingCode("M"), "M");
check("MT ohne Doppelpunkt", formatPairingCode("MT"), "MT");
check("Bindestrich im QR-Inhalt bleibt", formatPairingCode("MT:A-B"), "MT:A-B");
check("isPairingQrCode bei Ziffern", isPairingQrCode("1234-567"), false);
check("isPairingQrCode beim ersten Buchstaben", isPairingQrCode("M"), true);

// --- normalizePairingCode: what gets transmitted ---
check("Trenner raus", normalizePairingCode("1234-567-8901"), "12345678901");
check("Leerraum aussen raus", normalizePairingCode("  1234-567-8901  "), "12345678901");
check("QR getrimmt, sonst gleich", normalizePairingCode(" MT:A-B "), "MT:A-B");
check("leer bleibt leer", normalizePairingCode("   "), "");

// --- describePairingCode: the chip ---
check("leer ist unsichtbar", describePairingCode("").tone, "idle");
check("sieben Ziffern", describePairingCode("1234567"), {
  key: "web.devices.code_detect_remaining_many",
  values: { n: 4 },
  tone: "warn",
});
check("zehn Ziffern, Einzahl", describePairingCode("1234567890"), {
  key: "web.devices.code_detect_remaining_one",
  values: {},
  tone: "warn",
});
check("elf Ziffern", describePairingCode("1234-567-8901"), {
  key: "web.devices.code_detect_manual",
  values: {},
  tone: "ok",
});
check("zwoelf Ziffern zaehlen auf 21", describePairingCode("123456789012"), {
  key: "web.devices.code_detect_remaining_many",
  values: { n: 9 },
  tone: "warn",
});
check("einundzwanzig Ziffern", describePairingCode("123456789012345678901"), {
  key: "web.devices.code_detect_manual_long",
  values: {},
  tone: "ok",
});
check("zweiundzwanzig Ziffern", describePairingCode("1234567890123456789012"), {
  key: "web.devices.code_detect_too_long",
  values: {},
  tone: "bad",
});
check("MT-Code", describePairingCode("MT:Y.K90SO527JA0648G00"), {
  key: "web.devices.code_detect_qr",
  values: {},
  tone: "ok",
});
check("Buchstabensalat", describePairingCode("hallo"), {
  key: "web.devices.code_detect_invalid",
  values: {},
  tone: "bad",
});

console.log(failures === 0 ? "\nAlles gruen." : `\n${failures} Fehlschlaege.`);
process.exit(failures === 0 ? 0 : 1);
```

- [ ] **Step 2: Run the check script, confirm failure**

```bash
node /tmp/pairing-probe.mjs
```

Expected: aborts with `ReferenceError: isPairingQrCode is not defined` — the functions don't exist yet.

- [ ] **Step 3: Write the functions**

In `src/loxmatter/web/app.js`, insert immediately **before** `const VIEWS = ["devices", ...]` (line 317):

```js
// --- Pairing code (design of 2026-09-07) -----------------------------------
//
// On the device the numeric code is grouped: `1234-567-8901`. That is
// exactly how everyone types it in - so the field accepts it the same way
// and writes the dashes itself while typing.
//
// The rule exists TWICE: here and as `_strip_separators` in
// `api/models.py`. That is deliberate - the UI formats, the backend
// normalizes for EVERY caller of the route. Whoever changes one of the
// two versions changes the other.

// Anything other than digits, whitespace, and dashes turns the value into
// a QR payload. This check therefore fires on the first typed `M` of
// `MT:`, not only at the colon: a rule that waited for `MT:` would treat
// the two characters before it as digit input and discard them.
const PAIRING_QR_PAYLOAD = /[^0-9\s-]/;

// The documented notation exists only for the eleven-digit form. There is
// none for the 21-digit code - an invented grouping would look different
// from what's printed, so the field would format the code AWAY from the
// original rather than toward it. From the twelfth digit on it therefore
// stays ungrouped.
const PAIRING_GROUPS = [4, 7, 11];

function isPairingQrCode(raw) {
  return PAIRING_QR_PAYLOAD.test(raw);
}

function formatPairingCode(raw) {
  if (isPairingQrCode(raw)) {
    return raw;
  }
  const digits = raw.replace(/\D/g, "");
  const parts = [];
  let start = 0;
  for (const end of PAIRING_GROUPS) {
    if (digits.length <= start) {
      break;
    }
    parts.push(digits.slice(start, end));
    start = end;
  }
  // NOTHING gets cut off: truncating would let characters silently
  // disappear, and `describePairingCode`'s "too long" would be
  // unreachable - a rule that never fires. Anyone who mistypes should be
  // able to read that.
  if (digits.length > start) {
    parts.push(digits.slice(start));
  }
  return parts.join("-");
}

function normalizePairingCode(raw) {
  const text = raw.trim();
  return isPairingQrCode(text) ? text : text.replace(/\D/g, "");
}

// What the chip in the field says. Returns a key instead of text so this
// function stays checkable without a loaded language table - translation
// only happens at display time.
//
// The chip DESCRIBES, it does not forbid: even at `bad` the commissioning
// button stays usable and the value goes to the route unchanged. Same
// stance as the validator in the backend - the bridge says what it sees
// and lets the Matter stack decide.
function describePairingCode(raw) {
  const text = raw.trim();
  if (!text) {
    return { key: "", values: {}, tone: "idle" };
  }
  if (isPairingQrCode(text)) {
    return /^MT:/i.test(text)
      ? { key: "web.devices.code_detect_qr", values: {}, tone: "ok" }
      : { key: "web.devices.code_detect_invalid", values: {}, tone: "bad" };
  }
  const count = text.replace(/\D/g, "").length;
  if (count === 11) {
    return { key: "web.devices.code_detect_manual", values: {}, tone: "ok" };
  }
  if (count === 21) {
    return { key: "web.devices.code_detect_manual_long", values: {}, tone: "ok" };
  }
  if (count > 21) {
    return { key: "web.devices.code_detect_too_long", values: {}, tone: "bad" };
  }
  // Counted against the next valid length - 11 first, then 21.
  const missing = count < 11 ? 11 - count : 21 - count;
  return missing === 1
    ? { key: "web.devices.code_detect_remaining_one", values: {}, tone: "warn" }
    : { key: "web.devices.code_detect_remaining_many", values: { n: missing }, tone: "warn" };
}
```

- [ ] **Step 4: Run the check script, confirm success**

```bash
node /tmp/pairing-probe.mjs
```

Expected: every line `ok`, `Alles gruen.` at the end, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/web/app.js
git commit -m "feat(web): Formatier- und Erkennungsregel fuer Pairing-Codes

Drei reine Funktionen auf Modulebene - verdrahtet wird im naechsten
Schritt.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Wire up the field

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml:618` (change `code_placeholder`, add eight keys)
- Modify: `src/loxmatter/web/app.js` — handler in `app()`, after `commissionDevice()` (line 1653); plus `commissionRunCode` at line 1663
- Modify: `src/loxmatter/web/index.html:325-341` (field) and `:412` (progress display)
- Modify: `src/loxmatter/web/style.css:470` (after `.code-field .code-input`)

**Interfaces:**
- Consumes: `formatPairingCode`, `normalizePairingCode`, `describePairingCode` from Task 2
- Produces: `app().formatCommissionCode(input)`, `app().commissionCodeKeydown(event)`, `app().commissionCodeBadge()` — the last one returns `{ text: string, tone: string }` with the text already translated

- [ ] **Step 1: Add the strings**

In `src/loxmatter/i18n/strings.yaml`, replace the existing entry at line 618:

```yaml
web.devices.code_placeholder:
  # Shows the FORM instead of describing it. The former parenthetical
  # ("11 digits or MT:…") named the form - anyone holding the sticker in
  # their hand first had to translate that into "the lower of the two
  # numbers". That information is now carried by the example line below
  # the field and the sticker sketch next to it. Both languages carry the
  # same value - a digit sequence doesn't translate.
  en: "1234-567-8901"
  de: "1234-567-8901"
```

And insert directly after `web.devices.code_label` (line 638):

```yaml
# The chip on the right of the input field (design of 2026-09-07, section
# 6). It names what the field has recognized - so you learn the name of
# the code at exactly the spot where you type it. It only DESCRIBES: even
# "too long" doesn't lock the commissioning button.
web.devices.code_detect_manual:
  en: "Numeric code"
  de: "Zahlencode"
web.devices.code_detect_manual_long:
  en: "Numeric code, long"
  de: "Zahlencode, lang"
web.devices.code_detect_qr:
  en: "QR code"
  de: "QR-Code"
# Two keys instead of one with a plural rule: i18n.t doesn't know plural
# forms, and "1 digits to go" would be wrong in both languages.
web.devices.code_detect_remaining_one:
  en: "1 digit to go"
  de: "noch 1 Ziffer"
web.devices.code_detect_remaining_many:
  en: "{n} digits to go"
  de: "noch {n} Ziffern"
web.devices.code_detect_too_long:
  en: "too long"
  de: "zu lang"
web.devices.code_detect_invalid:
  en: "not a valid code"
  de: "kein gültiger Code"
# The example line below the field. The LABELS in it are the same keys as
# the chip's (code_detect_manual/_qr) - it's the same term, and that it
# reads identically in both places is the whole point of the exercise.
# Only the two example values live here, and they don't translate.
web.devices.code_example_manual:
  en: "1234-567-8901"
  de: "1234-567-8901"
web.devices.code_example_qr:
  en: "MT:Y.K90SO527JA0648G00"
  de: "MT:Y.K90SO527JA0648G00"
```

- [ ] **Step 2: Write the handlers in `app()`**

In `src/loxmatter/web/app.js`, insert immediately **before** `async commissionDevice()` (line 1653):

```js
    // Writes the numeric code while typing the way it appears on the
    // device.
    //
    // `commissionCode` is EXPLICITLY updated here rather than relying on
    // x-model: both hang off the same `input` event, and which listener
    // runs first depends on the order of the attributes in the markup. A
    // state that depends on attribute order is a bug that only surfaces
    // when things get reordered.
    formatCommissionCode(input) {
      const before = input.value;
      const formatted = formatPairingCode(before);
      if (formatted !== before) {
        // Count digits to the LEFT of the cursor, not character positions:
        // otherwise every newly inserted dash would shift the cursor by
        // one.
        const caret = input.selectionStart ?? before.length;
        const digitsLeft = before.slice(0, caret).replace(/\D/g, "").length;
        input.value = formatted;
        let seen = 0;
        let position = 0;
        while (position < formatted.length && seen < digitsLeft) {
          if (/\d/.test(formatted[position])) {
            seen += 1;
          }
          position += 1;
        }
        input.setSelectionRange(position, position);
      }
      this.commissionCode = input.value;
    },

    // Backspace DIRECTLY after a dash deletes the digit before it.
    //
    // Without this branch, the keypress deletes the separator that
    // `formatCommissionCode` immediately re-adds afterwards: the value
    // doesn't change, the cursor stays put, and the key appears dead.
    // That is the one point where a self-formatting input usually fails.
    commissionCodeKeydown(event) {
      if (event.key !== "Backspace") {
        return;
      }
      const input = event.target;
      if (input.selectionStart !== input.selectionEnd) {
        return;
      }
      const caret = input.selectionStart;
      if (caret < 2 || input.value[caret - 1] !== "-") {
        return;
      }
      event.preventDefault();
      input.value = input.value.slice(0, caret - 2) + input.value.slice(caret);
      input.setSelectionRange(caret - 2, caret - 2);
      this.formatCommissionCode(input);
    },

    // Text and colour of the chip in the field.
    commissionCodeBadge() {
      const state = describePairingCode(this.commissionCode);
      return {
        text: state.key ? t(state.key, state.values) : "",
        tone: state.tone,
      };
    },
```

- [ ] **Step 3: Normalize the transmitted value**

In `commissionDevice()`, replace the three places that currently use `this.commissionCode.trim()` (lines 1655, 1663, 1665):

```js
    async commissionDevice() {
      this.commissionMessage = null;
      // Normalized, not just trimmed: the separators the field itself
      // inserted while typing don't belong in the Matter stack. The
      // backend strips them a second time anyway
      // (`CommissionRequest._strip_separators`) - they're kept out here so
      // the UI doesn't send something other than what it shows.
      const code = normalizePairingCode(this.commissionCode);
      if (!code) {
        this.commissionMessage = t("web.devices.commission_code_required");
        this.commissionMessageIsError = true;
        return;
      }
      this.commissionBusy = true;
      this.commissionStep = 0;
      this.commissionFailed = false;
      // The progress display shows the FORMATTED code, not the
      // transmitted one: anyone waiting twenty to sixty seconds should
      // recognize the code they typed in.
      this.commissionRunCode = formatPairingCode(this.commissionCode.trim());
      try {
        const body = { code };
```

The rest of `commissionDevice()` stays unchanged.

- [ ] **Step 4: Wire up the markup**

In `src/loxmatter/web/index.html`, replace the block starting at line 325:

```html
            <div class="code-field">
              <input
                type="text"
                id="commission-code"
                class="code-input"
                spellcheck="false"
                autocomplete="off"
                x-ref="commissionCode"
                x-model="commissionCode"
                :placeholder="t('web.devices.code_placeholder')"
                @input="formatCommissionCode($event.target)"
                @keydown="commissionCodeKeydown($event)"
                @keydown.enter="commissionDevice()"
              />
              <!--
                No `inputmode="numeric"`: on a phone that would bring up
                the numeric keyboard, and that's where you'd be reading it
                off. But it would lock typing a QR text behind a shift
                key, and this card has exactly one field for both forms.

                The chip stays visible-but-empty on an empty field
                (`visibility: hidden` in style.css) rather than removed,
                so the field doesn't change its width on the first
                keystroke.
              -->
              <span
                class="code-detect"
                :class="'tone-' + commissionCodeBadge().tone"
                x-text="commissionCodeBadge().text"
                aria-live="polite"
              ></span>
              <button
                class="primary"
                @click="commissionDevice()"
                :disabled="commissionBusy"
                x-text="t('web.devices.commission_submit')"
              ></button>
          </div>
          <p class="code-examples">
            <span><b x-text="t('web.devices.code_detect_manual')"></b>
              <code x-text="t('web.devices.code_example_manual')"></code></span>
            <span><b x-text="t('web.devices.code_detect_qr')"></b>
              <code x-text="t('web.devices.code_example_qr')"></code></span>
          </p>
```

- [ ] **Step 5: The look**

In `src/loxmatter/web/style.css`, insert after `.code-field .code-input:focus` (line 488):

```css
/* The chip in the input field (design of 2026-09-07). It names what the
 * field has recognized. On an empty field it stays in place and only
 * becomes invisible - removing it would change the field's width on the
 * first keystroke.
 *
 * The three colours carry the same meaning as everywhere else in this UI
 * (--ok success, --warn incomplete, --danger wrong); no new one is added. */
.code-field .code-detect {
  display: inline-flex;
  align-items: center;
  align-self: center;
  flex-shrink: 0;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.03em;
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  white-space: nowrap;
}

.code-field .code-detect.tone-idle {
  visibility: hidden;
}

.code-field .code-detect.tone-ok {
  background: var(--ok-bg);
  color: var(--ok);
}

.code-field .code-detect.tone-warn {
  background: var(--warn-bg);
  color: var(--warn);
}

.code-field .code-detect.tone-bad {
  background: var(--danger-bg);
  color: var(--danger);
}

/* The two forms as an example. Replaces the former parenthetical in the
 * placeholder: that named the form, this shows it. */
.code-examples {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem 1.1rem;
  margin: 0.5rem 0 0;
  font-size: 0.8rem;
  color: var(--text-muted);
}

.code-examples b {
  font-weight: 600;
}

.code-examples code {
  font-family: var(--mono);
  font-size: 0.95em;
  color: var(--text);
}
```

**Nothing to do about the `@media (max-width: 480px)` rule at line 501.** It gives the field `flex: 1 1 100%` and the button `width: 100%` there; the chip falls into its own line between the two on its own as a result. Introducing an `order` would not only be redundant, it would push it behind the button. Checked anyway - Step 8, item 8.

- [ ] **Step 6: Check the language table**

```bash
uv run pytest tests/test_i18n.py -v
```

Expected: all PASSED — especially `test_web_namespace_has_no_missing_english_fallback_gaps` and `test_no_value_is_wrapped_in_typographic_quotes`.

- [ ] **Step 7: Check the pure functions again**

```bash
node /tmp/pairing-probe.mjs
```

Expected: `Alles gruen.` — the wiring step must not have changed them.

- [ ] **Step 8: Look at the binding in the browser**

The check script proves the rule, not the binding. Start the application for that:

```bash
uv run python scripts/dev_web_server.py --demo
```

Play through it on the device view and **actually look each time**, rather than assuming:

1. Type `12345678901` digit by digit — the dashes appear at the 5th and 8th digit, the cursor stays right after the last digit typed.
2. Place the cursor between `1234-` and `567`, type a `9` — it lands at the cursor position, not at the end.
3. Place the cursor directly after a dash, backspace — the digit before it disappears, the separator shifts back.
4. Paste `1234-567-8901` — the field shows it unchanged, the chip says "Numeric code".
5. Paste `MT:Y.K90SO527JA0648G00` — unchanged, the chip says "QR code".
6. Clear the field — the chip disappears, the field doesn't change its width.
7. Switch to German — the chip switches along with it.
8. Narrow the window to 400px — field, chip, and button stack vertically.

- [ ] **Step 9: The whole suite and the checkers**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [ ] **Step 10: Commit**

```bash
git add src/loxmatter/web/app.js src/loxmatter/web/index.html src/loxmatter/web/style.css src/loxmatter/i18n/strings.yaml
git commit -m "feat(web): Pairing-Code mitformatieren und benennen

Das Feld schreibt den Zahlencode als 1234-567-8901, wie er auf dem Geraet
steht, und sagt rechts im Feld, was es erkannt hat. Der Klammerzusatz im
Platzhalter weicht zwei Beispielen darunter.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The sticker sketch

**Files:**
- Modify: `src/loxmatter/web/index.html` (sketch next to the field, `.code-with-sticker` around both)
- Modify: `src/loxmatter/web/style.css` (`.code-with-sticker`, `.code-sticker`)
- Modify: `src/loxmatter/i18n/strings.yaml` (`sticker_alt`, `code_where_hint`)

**Interfaces:**
- Consumes: the field structure from Task 3
- Produces: nothing that later tasks access

- [ ] **Step 1: Add the two strings**

After `web.devices.code_example_qr` in `src/loxmatter/i18n/strings.yaml`:

```yaml
# The sticker sketch next to the field (design section 7). It's an
# illustration, not a source of information - the example line above it
# carries the same content as text. The aria-label therefore describes
# what there is to SEE, instead of reading out the content a second time.
web.devices.sticker_alt:
  en: "Sketch of a Matter label: the QR code on the left, the numeric code highlighted below it on the right."
  de: "Skizze eines Matter-Aufklebers: links der QR-Code, rechts darunter der hervorgehobene Zahlencode."
web.devices.code_where_hint:
  en: "Printed on the device, its packaging, or in the manual. The text behind the QR code works too."
  de: "Steht auf dem Gerät, seiner Verpackung oder im Handbuch. Statt der Zahl geht auch der Text hinter dem QR-Code."
```

- [ ] **Step 2: Put the sketch into the markup**

In `src/loxmatter/web/index.html`, wrap the `<div class="code-field">…</div>` from Task 3, together with the example line, in a frame and put the sketch next to it. From

```html
            <label class="field-label" for="commission-code" …></label>
            <div class="code-field"> … </div>
            <p class="code-examples"> … </p>
```

becomes the following block. It shows the markup in full, so it can be applied without referring back to Task 3 — **the two HTML comments from Task 3 (about the missing `inputmode` and the chip staying in place) carry over unchanged**, they are just not repeated here for length:

```html
            <div class="code-with-sticker">
              <div class="code-col">
                <label class="field-label" for="commission-code"
                       x-text="t('web.devices.code_label')"></label>
                <div class="code-field">
                  <input
                    type="text"
                    id="commission-code"
                    class="code-input"
                    spellcheck="false"
                    autocomplete="off"
                    x-ref="commissionCode"
                    x-model="commissionCode"
                    :placeholder="t('web.devices.code_placeholder')"
                    @input="formatCommissionCode($event.target)"
                    @keydown="commissionCodeKeydown($event)"
                    @keydown.enter="commissionDevice()"
                  />
                  <span
                    class="code-detect"
                    :class="'tone-' + commissionCodeBadge().tone"
                    x-text="commissionCodeBadge().text"
                    aria-live="polite"
                  ></span>
                  <button
                    class="primary"
                    @click="commissionDevice()"
                    :disabled="commissionBusy"
                    x-text="t('web.devices.commission_submit')"
                  ></button>
                </div>
                <p class="code-examples">
                  <span><b x-text="t('web.devices.code_detect_manual')"></b>
                    <code x-text="t('web.devices.code_example_manual')"></code></span>
                  <span><b x-text="t('web.devices.code_detect_qr')"></b>
                    <code x-text="t('web.devices.code_example_qr')"></code></span>
                </p>
                <p class="hint" x-text="t('web.devices.code_where_hint')"></p>
              </div>
              <!--
                The QR square is a suggestion made of rectangles - three
                finder patterns plus noise -, not a readable code. A real
                QR code in the image would be an invitation to scan it,
                and it would lead nowhere.

                All colours come from the existing variables, so the
                sketch holds up in both themes; --warn highlights the
                number and thereby carries the same meaning as everywhere
                else ("look here"), instead of introducing a new one.
              -->
              <svg class="code-sticker" width="188" height="112"
                   viewBox="0 0 188 112" role="img"
                   :aria-label="t('web.devices.sticker_alt')">
                <rect x="1" y="1" width="186" height="110" rx="6"
                      fill="var(--bg)" stroke="var(--border)" stroke-width="1" />
                <g fill="var(--text)">
                  <rect x="16" y="16" width="22" height="22" rx="1" />
                  <rect x="20" y="20" width="14" height="14" rx="1" fill="var(--bg)" />
                  <rect x="24" y="24" width="6" height="6" />
                  <rect x="62" y="16" width="22" height="22" rx="1" />
                  <rect x="66" y="20" width="14" height="14" rx="1" fill="var(--bg)" />
                  <rect x="70" y="24" width="6" height="6" />
                  <rect x="16" y="62" width="22" height="22" rx="1" />
                  <rect x="20" y="66" width="14" height="14" rx="1" fill="var(--bg)" />
                  <rect x="24" y="70" width="6" height="6" />
                  <rect x="46" y="18" width="4" height="4" /><rect x="54" y="26" width="4" height="4" />
                  <rect x="46" y="34" width="4" height="4" /><rect x="46" y="46" width="4" height="4" />
                  <rect x="62" y="46" width="4" height="4" /><rect x="70" y="54" width="4" height="4" />
                  <rect x="78" y="46" width="4" height="4" /><rect x="54" y="54" width="4" height="4" />
                  <rect x="62" y="62" width="4" height="4" /><rect x="78" y="70" width="4" height="4" />
                  <rect x="70" y="78" width="4" height="4" /><rect x="46" y="70" width="4" height="4" />
                  <rect x="16" y="46" width="4" height="4" /><rect x="30" y="46" width="4" height="4" />
                  <rect x="62" y="78" width="4" height="4" /><rect x="54" y="70" width="4" height="4" />
                </g>
                <rect x="98" y="52" width="76" height="20" rx="4"
                      fill="var(--warn-bg)" stroke="var(--warn)" stroke-width="1" />
                <text x="104" y="66" font-family="var(--mono)" font-size="11"
                      fill="var(--text)">1234-567-8901</text>
                <text x="98" y="44" font-size="8.5" letter-spacing="0.6"
                      fill="var(--text-muted)">MATTER</text>
              </svg>
            </div>
```

- [ ] **Step 3: The look**

In `src/loxmatter/web/style.css`, insert before `.code-field` (line 450):

```css
/* Field and sticker sketch side by side (design of 2026-09-07, section
 * 7). The sketch is permanently there: it costs no reading effort,
 * anyone who doesn't need it skips over it, and it replaces the former
 * parenthetical in the placeholder - so there's less text, not more. */
.code-with-sticker {
  display: flex;
  gap: 1.25rem;
  align-items: flex-start;
}

.code-with-sticker .code-col {
  flex: 1 1 auto;
  min-width: 0;
}

.code-sticker {
  flex: 0 0 auto;
}

/* Below this width the sketch moves under the field - the same threshold
 * as for wrapping the field and button further down, and for the same
 * reason: that's where you stand with your phone in front of the
 * device. */
@media (max-width: 480px) {
  .code-with-sticker {
    flex-wrap: wrap;
    gap: 0.75rem;
  }
}
```

- [ ] **Step 4: Check the language table**

```bash
uv run pytest tests/test_i18n.py -v
```

Expected: all PASSED.

- [ ] **Step 5: Look at it in the browser**

```bash
uv run python scripts/dev_web_server.py --demo
```

Check, each time by actually looking:

1. The sketch sits to the right of the field, top-aligned with the label.
2. In dark mode (toggle the system setting), the border, QR pattern, and number are readable and the highlight is visible.
3. At 400px width, the sketch sits below the field, nothing runs off horizontally.
4. The number in the sketch is in monospace — `font-family="var(--mono)"` doesn't work in the SVG, write `font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"` instead (SVG attributes don't resolve CSS variables everywhere; **actually look at this point**, don't assume).

- [ ] **Step 6: The whole suite and the checkers**

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css src/loxmatter/i18n/strings.yaml
git commit -m "feat(web): Aufkleber-Skizze neben das Pairing-Code-Feld

Zeigt, wo auf dem Geraet die Zahl steht, die hier hingehoert - wer den
Aufkleber in der Hand haelt, muss keinen Satz lesen, um die richtige der
beiden Zahlen zu finden.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Screenshot script and image

The script currently selects the input field via its placeholder text — **this selector breaks because of Task 3**, because that's exactly the text replaced there.

**Files:**
- Modify: `scripts/capture_screenshots.py:275`
- Replace: `docs/screenshots/commissioning.png`

**Interfaces:**
- Consumes: `id="commission-code"` from `index.html` (has been there since the "code first" rework)
- Produces: nothing

- [ ] **Step 1: Fix the selector**

In `scripts/capture_screenshots.py`, replace line 275:

```python
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
```

- [ ] **Step 2: Recapture the images**

```bash
uv run --with playwright python scripts/capture_screenshots.py
```

- [ ] **Step 3: Check the diff**

```bash
git status --short docs/screenshots/
```

Expected: `commissioning.png` changed, `system.png` changed (per the script's header comment that one is **not** reproducible — it shows the log of the run itself). **The remaining five must be unchanged.** If they are not, this change touched something it shouldn't have — look into it then, don't wave it off.

Discard `system.png`:

```bash
git checkout docs/screenshots/system.png
```

- [ ] **Step 4: Look at the new image**

Open `docs/screenshots/commissioning.png` and check: the code appears as `3497-011-2332` in the field, the chip next to it says "Numeric code" (the images are generated in English), and the sticker sketch is in the image.

- [ ] **Step 5: Commit**

```bash
git add scripts/capture_screenshots.py docs/screenshots/commissioning.png
git commit -m "docs(screenshots): Einlern-Karte mit Codeformat neu aufnehmen

Der Selektor haengt nicht mehr am Platzhaltertext - der ist jetzt die
Ziffernfolge selbst, und input[placeholder*=\"MT:\"] fand nichts mehr.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Wrap-up

Once, in full, after Task 5:

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy && node /tmp/pairing-probe.mjs
```

Then `superpowers:finishing-a-development-branch` for the way back to `main`.

The check script under `/tmp` is throwaway and does **not** go into the repo — there's no JS test framework here, and a single Node script that runs in no pipeline would be dead weight. Whoever changes the rule later rewrites it from Task 2, Step 1.
