# Pairing code: write it as it appears on the device

Design, September 7, 2026. Concerns the commissioning card of the device view —
last redesigned in the "Code first" design (see the comment over
`<div x-show="commissionStep === null">` in `web/index.html`) — and the
`POST /api/devices/commission` route.

## 1. The problem

Every Matter device has the numeric code displayed in groups: `1234-567-8901`. This
is how it appears on the sticker, how Apple, Google, and every manual show it.
When someone reads it and types it, they type the hyphens too — it looks just like
a phone number.

The input field accepts it that way and passes it through unfiltered:

```
web/app.js:1665     body = { code: this.commissionCode.trim() }
api/models.py       CommissionRequest.code: str
api/devices.py:419  active_client.commission_with_code(request.code)
matter/client.py    upstream.commission_with_code(code)
```

Further on, verified against the installed version rather than assumed:
`MatterClient.commission_with_code` (`matter_server/client/client.py:140`)
puts the string unchanged into the WebSocket command. **All the way from the
keyboard to the Matter stack, nobody strips the hyphens.**

Whether the CHIP SDK in the matter-server container swallows them in the end
cannot be proven from here — the parser runs there, not here. That's exactly the
point: the bridge currently relies on a commitment that nobody gave. If it does
not hold, commissioning fails with "Commission with code failed for node N", and
the reason — a hyphen — appears in no message.

There's a second, smaller problem: the field does not say what it expects.
Its placeholder reads "Pairing code (11-digit or MT:…)". That is the
form, not the appearance. Someone holding the sticker needs to first
translate the bracketed note into "the lower of the two numbers".

## 2. What this design wants

1. The field **writes the code as it appears on the device** — the
   hyphens appear automatically while typing.
2. The field **names what it recognized** — numeric code, QR code, or how many
   digits are still missing.
3. The bridge **strips the separators itself**, in one place, for
   every caller.

## 3. What stays unchanged

- **The commissioning logic.** `commissionDevice()`, the progress display, the
  error handling by status, the reloading of signals and commands —
  not a character.
- **The card as a whole.** A required field in the first row, room and
  dropdowns in the second. The "Code first" design remains valid; only
  what *in* the first row changes.
- **The two `<details>` dropdowns** along with their text.
- **The shared frame around field and button** (`.code-field`) and the
  monospace font inside it. Both are prerequisites for this design, not
  accessories: in proportional font, the digits shift when reformatted.

## 4. The formatting rule

One function, one place, two cases.

```js
// Anything other than digits, spaces, hyphens => QR content.
function isQr(raw) { return /[^0-9\s-]/.test(raw); }
```

The check triggers **on the first typed `M`** from `MT:`, not just
at the colon. That is intentional: a rule that waits for `MT:` would
treat the two characters before it as digit input and discard them.

- **QR content** remains character for character. It is Base38-encoded;
  any grouping would be made up.
- **Numeric code**: extract digits, group the first eleven as `4-3-4`,
  append the rest ungrouped.

**Nothing gets cut off.** Truncating to 21 digits would be tempting,
but wrong here: it would let characters disappear silently, and the
"too long" state of the chip (section 6) would be unreachable — a rule
that never triggers. If someone types it wrong, they should be able to read it instead of guessing.

**From the twelfth digit onward, no further grouping.** For the eleven-digit
code, `4-3-4` is the format shown on devices. For the 21-digit code, I found no documented grouping. A made-up one
would look different from the label — the field would then format the code *away*
from the original rather than toward it. The chip in section 6 still says that
a long code was recognized; it stays thus commented, just
not grouped.

Outbound always goes:

```js
// Trimmed; for numeric code without separators. QR content untouched.
function normalize(raw) {
  const v = raw.trim();
  return isQr(v) ? v : v.replace(/\D/g, "");
}
```

## 5. The cursor

Live formatting succeeds or fails by whether the cursor does not jump.

**When resetting**: count digits left of the cursor, reformat the value,
place the cursor after the same number of digits. Counting goes by digits,
not by character positions — otherwise every newly inserted
hyphen would shift the cursor by one.

**On backspace over a hyphen**, delete the digit before it instead. Without this
special handling, the key press deletes the separator,
which formatting immediately sets again: the value does not change, the cursor
stays put, and the key feels dead. This is the one
point where live-formatting input typically fails.

## 6. The chip in the field

On the right in the field, between input and button, with `aria-live="polite"`:

| Input | Chip | Color |
|---|---|---|
| empty | *(invisible, holds space)* | — |
| 1–10 digits | `4 more digits` | `--warn` |
| 11 digits | `Numeric code` | `--ok` |
| 12–20 digits | `9 more digits` | `--warn` |
| 21 digits | `Numeric code, long` | `--ok` |
| over 21 digits | `too long` | `--danger` |
| starts with `MT:` | `QR code` | `--ok` |
| other letters | `not a valid code` | `--danger` |

It counts toward the next valid length — first 11, then 21. When the
field is empty, it is `visibility: hidden` rather than removed, so the field does not
change width on the first key press.

**The chip describes; it does not forbid.** Even at `too long` and `not a valid
code`, the commissioning button stays enabled and the value goes
unchanged to the route. This is the same attitude as in section 8: the
bridge says what it sees and lets the Matter stack decide. A UI that blocks
input that the stack would have accepted cannot be avoided.

Thus the field carries the information itself, rather than spreading it across
placeholder and hint lines. And the name of the code is
learned at the place where you type it — that was the reason for the chip.

**No `inputmode="numeric"`.** On the phone there would be the digit keyboard,
and you stand there reading. But it would lock typing a QR text
behind a shift key, and the card has exactly one
field for both forms.

## 7. The sticker sketch

An inline SVG, roughly 190×112, to the right of the field, always visible:

```
┌─────────────────────────────────────────────────────┐
│ PAIRING-CODE                                        │
│ ┌─────────────────────────────────────────────────┐ │
│ │ 1234-567-8901        [Numeric code] [Commissioning]│ │
│ └─────────────────────────────────────────────────┘ │
│ Numeric code 1234-567-8901   QR code MT:Y.K90SO527… │
│                                                     │
│  ▓▒░ MATTER                                         │
│  ░▓▒ ┌───────────────┐   ← the sketch stands        │
│  ▒░▓ │ 1234-567-8901 │      always beside it        │
│      └───────────────┘                              │
└─────────────────────────────────────────────────────┘
```

It shows where on the device the number that goes here is printed, and sets it apart
from the QR code next to it. Someone holding the sticker does not need
to read a sentence to find the right one of the two numbers.

**To the objection that the card was deliberately decluttered.** It was — the
"Code first" design moved 78 words of permanent help text into dropdowns.
A sketch does not fall under that: it costs no read time,
those who do not need it skip over it, and it replaces the bracketed note
in the placeholder. So the text becomes not more, but less.

**Colors from existing variables**: `--bg` as surface, `--border` as
border, `--text` for QR pattern and digits, `--text-muted` for
labels, `--warn`/`--warn-bg` for highlighting the number. Thus
it works in both themes without introducing a new color — and
the highlighting uses a color that already has meaning ("look
here"), not a new one.

The QR square is a suggestion from rectangles (three search patterns plus
noise), not a readable code. A real QR in the image would be an invitation
to scan it, and led nowhere.

**Wrap below 520px** over the field instead of beside it — the same threshold at
which `style.css:501` already wraps field and button today, for the same
reason: that is where you stand with the phone in front of the device.

An `aria-label` describes what is to see. The sketch is a
clarification, not an information source — the example line above carries
the same content as text.

## 8. Normalization in the backend

A `field_validator` on `CommissionRequest.code`. One place, valid for
every caller of the route, not just our UI — a manually issued
`curl` with the typed code should not fail at the same place
where the UI was just fixed.

It does the same as `normalize()` in section 4, and **it only normalizes,
it does not validate.**

This is the second key decision of this design. The bridge should
not try to be smarter than the Matter stack here: if it rejected a code
that the stack would have accepted, there would be no way around it — and
the forms of setup codes are not something this bridge manages.
Stripping separators is lossless; a separate length rule would be a
bet on a specification that evolves without us.

An empty code thus remains an empty code and fails where it fails today.

## 9. Text strings

New in `i18n/strings.yaml`, all in both languages:

| Key | de |
|---|---|
| `web.devices.code_detect_manual` | Numeric code |
| `web.devices.code_detect_manual_long` | Numeric code, long |
| `web.devices.code_detect_qr` | QR code |
| `web.devices.code_detect_remaining_one` | 1 more digit |
| `web.devices.code_detect_remaining_many` | {n} more digits |
| `web.devices.code_detect_too_long` | too long |
| `web.devices.code_detect_invalid` | not a valid code |
| `web.devices.code_where_hint` | Printed on the device, its packaging, or in the manual. |
| `web.devices.sticker_alt` | *(aria-label of the sketch)* |

Two keys for "N more digits" because `i18n.t` does not know plural forms
and "1 more digits" would be wrong in both German and English.

The labels of the example line ("Numeric code", "QR code") are
the same keys as those of the chip — it is the same term, and
having it be the same in both places is the point.

**Changed**: `web.devices.code_placeholder` changes from "Pairing code
(11-digit or MT:…)" to `1234-567-8901`. The bracketed note is replaced by
sketch and example line, and a placeholder that shows the form
says more than one that describes it.

## 10. What goes along

- **The progress display.** `commissionRunCode` shows the formatted code,
  not the normalized one. Someone waiting twenty to sixty seconds for
  commissioning should recognize the code they typed.
- **`docs/screenshots/commissioning.png`** retake. The images are
  byte-for-byte reproducible (see header comment of
  `scripts/capture_screenshots.py`); a diff there is not imprecision,
  but exactly this change.
- **The selectors in `capture_screenshots.py`** check if they
  depend on the placeholder text.

## 11. Verification

**Backend, with pytest**, in `tests/api/`:

- `1234-567-8901` arrives as `12345678901` at `commission_with_code`
- `MT:Y.K90SO527JA0648G00` arrives unchanged
- Spaces and surrounding whitespace are also removed
- the existing commissioning tests stay green (they send `MT:ABC123`,
  a QR code, which the validator does not touch)

**Frontend.** There is no JS test framework in the repo. The formatting logic is
therefore exercised in a throwaway harness rather than asserted:

- Typing `12345678901`, character by character — grouping and
  cursor position after each keystroke
- Inserting `1234-567-8901` into an empty and a filled field
- Backspace directly after a hyphen
- Inserting a digit in the middle
- Switch point to QR content: `M`, then `MT`, then `MT:`
- 22 digits (nothing disappears, chip reports `too long`, button
  stays enabled)

What the harness does **not** prove is that the binding
reaches the application — for that, a look at the running UI.
