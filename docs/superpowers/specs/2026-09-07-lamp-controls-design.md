# Lamp Controls: Colour Picker, Sliders and the Loxone Colour Path

Design, 7 September 2026. Gives the device tile controls that match the value type of a command, instead of presenting every value with the same bare number field — and thereby opens the colour path (`MoveToHueAndSaturation`) available since Phase 4 for WebUI **and** Loxone.

Rationale: two IKEA lamps in the test setup, one CCT and one RGBW lamp. There is currently no way to control the colour of the second one.

## 1. The Problem

The control bar of the tile (`index.html`, block `device-commands`) renders
every named command according to exactly one pattern: without a value, a button; with
a value, an `<input type=”number”>` plus “Send”. For `on`/`off`/`toggle` this
is correct. For everything else it is the absence of a decision:

- **Brightness** requires a percentage number that one types instead of
  dragging.
- **Colour temperature** requires a Kelvin number without any indication of
  what range the lamp actually supports. If someone enters 6500 K even though
  the lamp ends at 4000 K, they get no error message — the device
  silently clamps.
- **Colour** does not exist at all. `MoveToHueAndSaturation` (Cluster 768,
  Command 6) is not in `profiles/clusters.yaml`, so it is not named by
  `command_slug`, filtered out from the Controls route, and
  appears only as anonymous “+1 further commands”.

The last gap is the real one. An RGBW lamp hangs on the bridge, duly reports
its colour state as a signal — and cannot be set to a colour from either the
UI or Loxone.

### The Contradiction in the Justification

`commands/translate.py` justifies in the module docstring why the pair (768, 6)
is missing: “because the Loxone-side RGB number is not reliably documented
(see `color.py`)”.

`commands/color.py` says the opposite at that location. There it states
under **”RGB — documented”** the formula

```
AQa = red% + green% * 1000 + blue% * 1_000_000
```

with official source (Loxone Knowledge Base, “RGB Lighting Controller”,
Outputs section) and a second, confirming community source. Not
documented is exclusively **Lumitech** — the combined brightness and
Kelvin output, which is not what Hue/Saturation is about at all.

The block is therefore based on confusion between two Loxone output formats.
This design removes it and corrects the docstring.

## 2. Purpose and Scope

The purpose remains **diagnosis** (Main Spec 8.1): a click separates “device
does not respond” from “Loxone wiring or export is wrong”. This
design does not make a comfort control panel out of it. Specifically that means:

- **No live state mirroring.** The UI does not move the sliders
  when the device changes from outside.
- **But an initial value.** On opening, existing values are read once
  (Section 6). Without that, every slider would sit at a fictional
  position, and the first push would move the lamp somewhere — the click
  would then prove nothing about the state it changed.
- **No scenes, no favourites, no schedule.**

## 3. What Remains Unchanged

- **`POST /api/commands/{key}`** — route, status codes (404/400/502) and
  semantics remain unchanged. No second control path is created.
- **`to_matter_call`** remains the only translator for WebUI *and*
  Loxone endpoint (Main Spec 4.2).
- **`takes_value`** keeps its meaning for export. The new
  `control:` is exclusively a hint for the UI.
- **The allowlist stance** from `api/control.py`: only released
  is what is documented against real hardware or explicitly marked against the
  specification.
- **The "+N further commands" note** and `hidden_raw_commands`.
- **The buttons for valueless commands** remain directly on the tile.
- **`POST /api/signals/{key}/write`** stays with its honest 501.

## 4. Documentation Instead of Assumptions

The following IDs are checked against the installed `chip` SDK
(`chip.clusters.Objects.ColorControl`), not noted from memory:

| Element | ID | Note |
| --- | --- | --- |
| `MoveToHueAndSaturation` | Command 6 | to be released |
| `MoveToColorTemperature` | Command 10 | already present |
| `MoveToHue` / `MoveToSaturation` | 0 / 3 | **not** released |
| `MoveToColor` (xy) | 7 | **not** released |
| `EnhancedMoveToHueAndSaturation` | 67 | **not** released |
| `ColorTempPhysicalMinMireds` | Attribute 16395 | new boundary |
| `ColorTempPhysicalMaxMireds` | Attribute 16396 | new boundary |
| `ColorMode` | Attribute 8 | 0 = Hue/Sat, 1 = xy, 2 = Mired |

`ColorModeEnum` also checked against the SDK.

### 4.1 Device Fixtures as a Prerequisite

`tests/fixtures/nodes/synthetic_color_light.json` is explicitly marked as
**synthetic, not a real device**. It was the placeholder for
exactly the hardware that now exists.

**Before implementation** both lamps are checked in as a fixture
(`tests/fixtures/nodes/`, format `{"node_id", "available", "attributes"}`)
and replace the synthetic image in the colour tests. Only then does
every whitelist entry stand on the same documentation that `api/control.py` and
`commands/color.py` demand of themselves.

The fixtures are checked before check-in for content that does not belong in the
repository — the same care with which `.gitignore` keeps the
Loxone original templates away.

**These fixtures can still change the design.** If the
RGBW lamp advertises command 6 not, but only 7 (xy), an
xy colour space conversion will be necessary, which does not exist anywhere today; this design
does not cover it and would need to be updated then.

## 5. Server

### 5.1 `profiles/clusters.yaml`

New under Cluster 768, `commands`:

```yaml
6: {slug: color, takes_value: true, control: hue_sat}
```

All existing command entries get `control:`:

| Cluster/Command | Slug | `control` |
| --- | --- | --- |
| 6/0, 6/1, 6/2 | `off`, `on`, `toggle` | `none` |
| 8/0 | `level` | `percent` |
| 8/4 | `level_onoff` | `percent` |
| 768/10 | `colortemp` | `kelvin` |
| 768/6 | `color` | `hue_sat` |

New under Cluster 768, `attributes`:

```yaml
16395: {slug: colortemp_phys_min_mireds, unit: mired, functional: false}
16396: {slug: colortemp_phys_max_mireds, unit: mired, functional: false}
```

### 5.2 New Table Field `functional: false`

`profiles/relevance.py`, `is_functional` layer 3, explains today: if a
cluster has an `attributes:` section, exactly what is named there is intended.
Simply naming the two CT boundaries would mean turning two
immutable device constants into two by-default-exported virtual
Loxone inputs.

The new optional field `functional: false` separates both: **known enough
to read, not interesting enough to pre-select.** `is_functional`
returns `false` for such attributes; in the expert block of the signal list
they remain visible and manually selectable, `exportable` does not change.

Deliberately a general field and not a special case for Cluster 768: any
further cluster with capacity constants (min/max ranges, resolutions)
hits the same problem, and the alternative — fetching the
values directly from the snapshot past the table — would create a second place where
attribute knowledge lives. That is exactly what the table is meant to prevent.

### 5.3 `commands/color.py`

New: the counterpart to `rgb_to_hue_saturation` — an unpacker for the
packed Loxone number.

```
loxone_rgb_to_rgb(value) -> (r, g, b)   # each 0–255
```

Decomposes `r% + g%*1000 + b%*1000000` into three percentage values and scales them to
0–255. The formula is already documented in the module docstring with official source;
it was just never implemented. Values outside the valid range (any
channel > 100 %, negative numbers, non-numbers) result in `ValueError` — the
caller makes 400 of that, not a fictional colour.

The module docstring also receives the note that the
RGB→Hue/Sat chain is from now on actually used and against which hardware
it was verified — the current warning “NOT validated against hardware”
thus becomes moot and must not remain.

### 5.4 `commands/translate.py`

New: `_payload_hue_saturation`, entered under `(768, 6)`:

```
Unpack number → RGB → rgb_to_hue_saturation → {"hue", "saturation", "transitionTime": 0}
```

The existing consistency test via `known_command_pairs()` forces
table entry and payload builder to move together — exactly the pitfall from
Review-Fix C2 (2026-09-02), where a builder without a table entry created a
digital command with an analogue payload.

**The module docstring is corrected.** The current justification for the
absence of command 6 is factually wrong (Section 1). In its place
comes the reference to the documented RGB formula and the clear note that
**Lumitech** remains open.

> **Addendum, 8 September 2026:** This paragraph describes the state at
> the time of writing this design. Lumitech has since been documented and
> implemented — see Section 10, point 1.

### 5.5 API

`CommandOut` (`api/models.py`) gets two fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `control` | `str` | `none` \| `percent` \| `kelvin` \| `hue_sat` |
| `range` | `{min, max} \| None` | only for `kelvin`, from 16395/16396 of the device |

`range` is filled from the last known attribute values of the device and
delivered in **Kelvin**, not Mired — the UI should not
have to calculate, and the conversion is a reciprocal where min and max
swap. If one of the two attributes is missing, `range` is `None`.

If a `control` is missing from the table, the API returns the explicit value
`unknown` — **not** a guess from `takes_value`. A
slider whose scale no one has documented would be worse than a number field:
it claims a value range. `unknown` falls back to the current number field in the UI (Section 6.2).

Today this case is unreachable — Section 5.1 provides every
existing entry with a `control`, and the Controls route filters
everything out that is not in the table at all (`command_slug is None`).
`unknown` exists for the next table entry that someone adds without
`control`, so it does not silently appear as a slider.

## 6. UI

### 6.1 Tile

Unchanged except for one addition: the valueless commands remain as buttons
directly on the tile, plus a **”Control”** button as soon as the device
has at least one command with `control` not equal to `none`. The current
number fields disappear from the tile.

The three existing state notes (`controls_loading`,
`no_known_commands`, `+N further commands`) remain word for word unchanged.

### 6.2 The Modal

Following the pattern of the Signal modal (design from 5 September 2026):
same open and close path, same header with device name.

The content is created **exclusively** from `control`. No slug comparison in
JavaScript:

| `control` | Widget |
| --- | --- |
| `none` | Button |
| `percent` | Slider 0–100 % |
| `kelvin` | Slider, limited by `range` |
| `hue_sat` | 2-D field: horizontal hue, vertical saturation |
| `unknown` | current number field with “Send” |

The last row is the fallback: a command without a `control` entry
loses nothing, it looks like today. No regression for devices that
this design does not have in view.

### 6.3 The Mode Tabs Emerge Automatically

Matter knows no state “colour and colour temperature at once”: `ColorMode`
is either Hue/Sat or Mired. The modal represents this rather than
obscuring it.

- Device has `kelvin` **and** `hue_sat` → Tab bar **White | Colour**.
- Device has only one of the two → no tab bar, just this widget.

The CCT lamp thereby gets a Kelvin slider without tabs, the RGBW lamp
both tabs — **without a single query for device type, manufacturer or
model.** This keeps the README promise “no curated list
of supported models” for controls as well.

### 6.4 Initial Value

Once on opening, no tracking. Source is the already-loaded
`signalsByDevice` entry — `GET /api/devices/{id}/signals` returns **all**
signals of the device with last value, not just the exported ones. So
no new route is created.

**The API already delivers scaled.** `SignalOut.value` comes from
`Runtime.last_values_for`, and the values arrive there via `to_loxone_value`,
which applies the `scale` factor from the table. The UI therefore
only calculates where the table cannot:

| Widget | Slug | Conversion |
| --- | --- | --- |
| Brightness | `level` | none — table already scales to % |
| Hue | `hue` | none — table already scales to degrees |
| Saturation | `saturation` | none — table already scales to % |
| Colour temperature | `colortemp_mireds` | Mired → Kelvin, reciprocal (`scale` can only multiply) |
| active tab | `colormode` | 2 → White, 0 → Colour |

That both `functional: false` boundaries carry a value is
verified and no accident: `Runtime._cache_attribute` caches every signal that
the store knows, without filtering on `exported`. Without this property,
`range` would always be empty and the Kelvin slider never limited.

If a value is missing, the widget starts in the centre **and says so**: a visible
note “Initial value unknown” instead of a position that claims knowledge
that does not exist.

### 6.5 Send

On **release** (`change`), not during dragging. One drag = one
radio packet. Thread is slow, and if a click is to prove something, the
mapping between input and reaction must remain clear.

Lock per command via the existing `commandBusyKey`; if the device
is offline, everything stays disabled — both as today.

The 2-D field converts the picked point to RGB, packs it as a
Loxone number and sends **that**. This means the click in the
UI follows exactly the path Loxone later takes — for a diagnostic tool
the real gain: if it works here, the Loxone path is proven.

## 7. Localization

All new strings come in German **and** English versions to
`i18n/strings.yaml`, as has been customary since Phase B/C: modal titles, tab labels
(White/Colour), slider labels, units, the note “Initial value
unknown” and the error message for an invalid colour number.

## 8. Tests

| Location | What |
| --- | --- |
| `tests/commands/test_color.py` | Unpacking, round-trip RGB→packed→RGB, edge cases (black, white, pure channels), rejection of >100 %, negative, NaN/inf |
| `tests/commands/test_translate.py` | Pair (768, 6) builds expected payload; invalid number → `UnsupportedValueError`; existing consistency test covers table ↔ builder |
| `tests/profiles/` | `functional: false` takes effect; CT boundaries remain `exportable` and selectable in expert block; `control` is read correctly |
| `tests/api/` | `CommandOut.control` and `range`; `range` is `None` when 16395/16396 are missing; `unknown` for commands without table entry |
| Fixtures | both real lamps replace `synthetic_color_light.json` in colour tests |
| WebUI | Delivery test only documents delivery — the Alpine expressions of the modal additionally run in a throwaway harness in the browser (tabs, initial value, fallback to number field) |

## 9. Deliberately Accepted Trade-offs

1. **Accuracy loss.** The packed Loxone number carries only
   0–100 % per channel. The picked colour is thus quantized before it becomes Hue/Sat.
   That is the price for the UI and Loxone using the same
   translator — and for a diagnostic tool the right price:
   a lossless but separate conversion would destroy exactly the claim
   that the click is meant to make.
2. **No CT range → no slider.** If a lamp does not deliver 16395/16396,
   the Kelvin slider falls back to the number field rather than showing
   fictional boundaries. A slider that ends at 6500 K even though the lamp
   stops at 4000 K would be the silent failure that Main Spec 8.1
   forbids.
3. **Only one colour command.** MoveToHue (0), MoveToSaturation (3),
   MoveToColor (7) and Enhanced (67) remain blocked. The 2-D field sets
   hue and saturation in one command; anything else would be unused
   space.

## 10. Open Items

1. ~~**Lumitech remains unsolved.**~~ **Solved on 8 September 2026.** The combined brightness and
   Kelvin output had no documented formula — only a forum assumption which this
   design explicitly identified as unreliable. An installation with Lumitech DMX output
   has confirmed it: 24 measured values in the bridge's command log, read as `AA BBB
   CCCC` (identifier 20, brightness, Kelvin). Decisive was not the quantity but that two
   different brightness levels appeared (28 % and 100 %) — only that shows that the
   middle field moves independently of the rear field, rather than coincidentally matching.

   The finding came from an error pattern: the White slider of the Loxone app did
   nothing. The light control module sends colour **and** white over the same analogue
   output, and the bridge read every white value as a colour with a channel over 100 % —
   50 rejections with 400 in the log. `_payload_hue_saturation` now distinguishes the two.
   This is not a heuristic: the largest RGB number is 100 100 100, the smallest
   Lumitech number 200 000 000, so the value ranges cannot overlap. A white value could
   therefore never have passed as a wrong colour even before, only been rejected.

   The only thing that remains open at this point is what point 5 describes: brightness
   from `BBB` is discarded, as it is on the RGB path as well.
2. **xy colour space.** If the fixtures show that devices
   expect `MoveToColor` (7) instead of command 6, the
   xy conversion is completely missing.
3. **The allowlist in `api/control.py`** remains manually maintained; the
   path described in its docstring via the `writable` table of the
   chip package is still blocked.
4. **No state mirroring.** If someone changes the lamp from outside while
   the modal is open, the sliders silently become stale. Deliberately so — expanding to
   a real control panel would be a separate design with its own
   justification against Main Spec 8.1.
5. ~~**The Loxone colour path discards brightness.**~~ **Solved on 8 September
   2026.** Loxone encodes brightness in the magnitude of the RGB number (the
   Value component of HSV) or in the `BBB` field of Lumitech; the bridge sent
   only hue and saturation and thus discarded brightness. Documented by two values
   from the same installation: `18004020` and `85019094` have the same hue
   (307.5° / 307.2°) and the same saturation (80.0 % / 79.8 %), but 20 %
   vs 94 % brightness — the same colour, once dimmed.

   `to_matter_calls` (formerly `to_matter_call`) therefore returns a **list**:
   one Loxone value can mean more than one thing. First the colour, then
   `MoveToLevelWithOnOff`. This way the value 0 actually turns the lamp off,
   instead of leaving it glowing white.

   Three things that only hardware revealed:
   - **The value 0 must not trigger a colour command.** In RGB encoding,
     `0` equals (0,0,0) — hue 0, **saturation 0**, i.e. white. First colouring
     white and then turning off produced a bright white flash on shutdown, even brighter
     than the image before: white uses all LEDs, saturated red only the red ones. At
     brightness 0 there is no colour to set — only one command remains, turning off.
     Comparison is against the rounded level, not the percentage.
   - A colour command to an **off** lamp vanishes (Matter spec).
     The colour payload therefore carries `ExecuteIfOff`; otherwise the lamp would
     come up in the old colour. Measured: without the bit it came up white, with the bit
     at hue 120.5° and 60 % brightness.
   - A failure on the second call leaves a half-state
     (colour set, brightness not). That is the price for Loxone sending both in one
     value and Matter requiring them separately; the caller reports the failure as 502 instead
     of swallowing it.
6. **Endpoint asymmetry between server and UI.**
   `api/control.py::_kelvin_range` correctly filters initial values to
   `signal.ref.endpoint == command.endpoint`; the UI searches for its
   initial values in `app.js` however via
   `entry.path.endsWith('/<cluster>/<element>')` — without endpoint — and
   takes the first match. On a node with LevelControl or
   ColorControl on two functional endpoints (two-channel dimmer, bridged
   lamps) both sliders would get the initial value from endpoint 1, and
   `hasColourTabs` would mix commands from different endpoints into one
   tab bar. No checked-in device triggers this today; the change
   would be larger than the benefit and belongs in a separate task
   (closing review 2026-09-08, finding I-4).
