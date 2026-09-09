# Device tile without a primary signal: all signals equal

Design, September 7, 2026. Changes the header of the device tile from
[the device dashboard design](2026-09-03-device-dashboard-and-export-design.md),
section 6.2, and thus removes part of
[the device tab design](2026-09-05-devices-tab-rooms-and-tile-grid-design.md)
again.

## 1. The problem

The tile shows one signal differently from all others. `leadSignalFor()`
picks out the first functional signal and places it at `1.35rem`
on the right in the header; its title appears as `.lead-label` in `0.65rem`
capitals left below the device name. The remaining up to five signals
sit below in the value grid, at `0.75rem`, title left and value right.

Same kind of thing, two displays. For a radiator,
"Temperature" appears top left small in capitals and "Humidity" two lines
lower in normal type in the title column — even though both are signal titles
and both values come from the same source.

This costs two things:

**The width of the name.** `.lead-value` carries `max-width: 50%` and
`flex: 0 0 auto` — it never shrinks, that was a deliberate decision
(the reason is explained in detail at the rule). The name thus gets
65 px at the grid minimum of 261 px. Almost every
Loxone device name is truncated there, and at the wrong place:
names of this kind differ at the end ("… North" vs "… South"),
the end gets cut off.

**Readability as a set.** When someone wants to check if a tile looks plausible,
they read six signals. Five of them line up in one column, one
is elsewhere. The value grid has a reason in the stylesheet for exactly this purpose — "you scan one column instead of twelve units" —, and the
primary signal is the exception that breaks it.

## 2. The decision

The primary signal is removed without replacement. All functional signals sit
equally in the value grid, in one font size, in one column.

This is explicitly **not** a condensing design. The tile does not
become smaller (see section 7), and the length of the list at eighty
devices stays as it is. It is about equal footing and about the width
of the name.

Three designs were rejected that wanted to keep the primary signal and
instead condense the row (line list, anomaly triage, density toggle): they all
show only the primary signal and push the remaining signals behind a dropdown. Often several of them matter at the same time.
Also rejected: chip bars instead of the value grid — they save height,
but destroy exactly the column alignment that the grid exists for.

## 3. What stays unchanged

- **The signal data.** `functionalSignalsFor()`, `firstSignalsFor()`,
  `remainingSignalCount()` and `FUNCTIONAL_PREVIEW_LIMIT: 6` get no
  changes. Six signals per tile stay six signals per tile.
- **The order of signals.** It was already the API order; the
  primary signal was just the first entry from it, not a separate selection.
- **`true`/`false`.** Boolean values stay technically labeled.
  This design does not touch `formatValue()`.
- **The color bar** on the left of the card, along with its three states, and
  the Modified badge in the footer.
- **`value-fresh`** on freshly arrived values — in the future only in the
  value grid, because it is the only place where values appear.
- **The tile menu**, the command bar, the footer, the signals modal
  and the "+ N more signals" notice.

## 4. The header

Two children instead of three:

```
┌────────────────────────────────────────────┐
│ [◧]  Radiator Living West                  │
├────────────────────────────────────────────┤
│ Temperature                        21.40 °C │
│ Humidity                            44.00 %│
│ Set temperature                    21.00 °C │
│ Valve position                      34.00 %│
│ Battery                            88.00 %│
│ Window open                        false │
└────────────────────────────────────────────┘
```

`.device-ident` keeps its role as the shrinking middle, but has only
one child. `min-width: 0` stays on it **and** on the name: the reason
was never the primary signal, but the intrinsic minimum width of an
`<input>` or the automatic minimum of a flex child.

`.device-head` keeps `align-items: flex-start`. With a single-line
middle, the difference to `center` is invisible as long as no badge
stands beside it; with badge both should sit at the top, not centered
with the icon.

## 5. The offline badge

It moves out of `.device-ident` and into the header itself, in the place of the
primary signal. `.status-pill` already carries `margin-left: auto` —
in the flex header it thus pushes itself to the right without further ado.

Added September 7, 2026: This reasoning is factually wrong,
even though the result is correct. `.device-ident` carries `flex: 1 1 auto` and
when resolving flexible lengths (CSS Flexbox § 9.7) already
consumes all free space in the header **before** auto margins
are even distributed (§ 9.5). No free space remains that
`margin-left: auto` on `.status-pill` could collect — the auto margin
is ineffective at this spot. The badge stands right **because
`.device-ident` grows**, not because of its own auto margin. Thus
`flex: 1 1 auto` on `.device-ident` is key here and not optional:
if it went away, free space would remain, and only then would
`margin-left: auto` have an effect.

Thus the rule "only the offline badge displaces the
primary signal label" goes away: there is no label left to displace. The
condition shrinks from
`x-show="isOnline(device) && leadSignalFor(device.id)"` to a simple
`x-show="!isOnline(device)"` on the badge — and the coupling to
`isOnline` disappears from the label without replacement, because the label
no longer exists.

## 6. Not in this design: the name as text

It would be natural to turn the name from the permanently visible `<input>` into
plain text and show the field only when renaming.
This was originally planned as a second part, with the reasoning that
the text field costs around 192 px minimum width via its `size=20`.

**This reasoning does not hold.** `.device-head .device-name` already carries
`min-width: 0`, with detailed comment at the rule — exactly
this intrinsic minimum width was already addressed there. The field
costs no width today that the text would not also cost.

Two cosmetic arguments remain: a name that always looks like a
form field invites editing even though you mostly just read it; and a
field with `border: 1px solid transparent` is an element
that reveals its state via hover rather than through its shape. Both are
true and both are small.

Against it stands real effort: the tile menu offers *Signals*,
*Export* and *Remove*, but **not** *Rename* — that runs
only through the visible field. The entry point would have to be created,
along with menu item, language keys in both languages, and a state field
for each open rename.

That is too much for a cosmetic gain. The name stays an
`<input>`. For anyone who wants the refactor later, `renamingRoom` /
`renameDraft` at the room header provides the template.

## 7. The height

Truth before marketing: the tile gets **taller**, not shorter.

Roughly estimated, at `font-size: 14px` and `line-height: 1.5`:

| | |
|---|---|
| Value grid gains one line (`0.75rem` × 1.5 + `0.05rem` line spacing) | **≈ +16 px** |
| Header loses the label line (`0.65rem` × 1.5 plus `0.1rem` spacing), to the extent the icon tile with its 33.6 px does not compensate | **≈ −6 px** |
| **Net per tile, estimated** | ≈ +10 px |
| **Net per tile, measured** | **+14 px** |

At eighty devices and three columns, that is roughly 380 px additional
scrolling distance.

Added September 7, 2026: measured in the browser (Chromium via
Playwright, demo data), all four tiles consistently showed **+14 px** —
284→298 and 265→279, twice each. The estimate was 40 % short because
the second item was uncertain: an `<input>` does not inherit `line-height`
reliably, so the header loses less than assumed. The sign does not change,
and the magnitude is right.

The calculation applies to devices with at least one functional signal. A
device without signals becomes shorter, because the header loses one line
and nothing is added.

## 8. What goes away on its own

Removing the primary signal also deletes a whole class of bugs.

`test_a_device_without_a_lead_signal_does_not_throw_in_any_binding`
describes it: between `GET /api/devices` and
`GET /api/devices/<id>/signals` lies a rendering pass in which
`signalsByDevice` for the device is still empty. `leadSignalFor()` then
returns `null`, and `x-show` on the wrapper does **not** prevent Alpine from
evaluating the child expressions — `signalIsFresh(null)`,
`signalAgeTitle(null)` and `liveValueOf(null)` ran to nothing three times per device. That did not hit corrupted data,
but every device once.

An `x-for` over an empty array, by contrast, evaluates nothing. The
error source vanishes with its cause, not with additional protection.

**The protection stays anyway.** The three helpers keep their
null tolerance and the test keeps its purpose; only its hook moves
from the primary signal to the helpers themselves. Removing leniency because
one known caller is gone is the kind of cleanup that backfires on the next
caller.

## 9. What to verify

- **In the browser**, not in markup: whether the tile reads at a glance without the large value. The assumption is yes — the value column is
  scanned as a column, not read line by line —, but it is an
  assumption. If no, the smallest correction is to raise
  `.value-rows` to `0.8rem`, not the return of the primary signal.
  `.value-rows` stays at `0.75rem` in this design.
- **Tile heights in a row.** `align-items: stretch` and
  `.device-foot { margin-top: auto }` should keep footers aligned;
  this needs to be measured again after a change to the card's child count, not assumed.
- **The actual height difference** per tile, against the estimate
  in section 7. The only interest is whether it is in the ballpark —
  two pixels do not matter.
- **The screenshots** in `docs/screenshots/`, insofar as they show
  device tiles.

Language files are not affected: the primary signal title came from
`signal.title`, from the data, not from `strings.yaml`.
`web.devices.offline` and `web.devices.no_functional_signals` remain in
use.

## 10. Affected locations

**Removed:**

| Location | |
|---|---|
| `app.js` | `leadSignalFor()`, `restSignalsFor()` |
| `style.css` | `.lead-label`, `.lead-value`, `.lead-value small` |
| `index.html` | `.lead-value` block and `.lead-label` span in `.device-head` |
| `test_web.py` | `test_the_lead_label_only_yields_to_the_offline_pill_now`, `test_lead_value_gets_padding_room_for_descenders` |

**Changed:**

| Location | |
|---|---|
| `index.html` | `.value-rows` runs over `firstSignalsFor()` instead of `restSignalsFor()`; offline badge moves to `.device-head`; notice of missing signals depends on `functionalSignalsFor(id).length === 0` instead of `!leadSignalFor(id)` |
| `style.css` | Comment at `.device-head .device-name` loses its reference to the 261-px calculation with `.lead-value` |
| `app.js` | Comment at line ~1616 refers to `leadSignalFor` |
| `test_web.py` | Helper list (~line 2600) loses two entries; `test_a_device_without_a_lead_signal_…` changes its hook; docstrings of two other tests refer to the primary signal |

The scope is thus small and contained: one header, one
`x-for` call, two deleted Alpine methods, three deleted CSS rules,
and the tests that go with them.
