# Illuminance in Lux, Colour Temperature in Kelvin

Design, 1 October 2026. The bridge converts two values that it used to pass
through raw - illuminance (1024/0) into lux and colour temperature (768/7)
into Kelvin - before they reach Loxone. The WebUI shows the converted value
where the value always stood and, small underneath, the raw device value
for every signal the bridge converts.

## 1. What Happened

The maintainer asked what the `illuminance` value of the IKEA motion sensor
means. It is `10000 * log10(lux) + 1`, the encoding the ZCL and Matter
share: 20001 is 100 lx, 30001 is 1000 lx. The value had been left raw on
purpose, because the only conversion `clusters.yaml` can express is a
factor (`scale`), and turning that encoding back into lux is a power. The
colour temperature had stayed in mired for the same reason - Kelvin is
`1e6 / mired`, a reciprocal - and `clusters.yaml` records that as an open
point (signal-selection design, section 10).

Both answers are wrong for a Loxone user. A threshold of "dark" is written
in lux, and the colour temperature OUTPUT (768, 10) already takes Kelvin
(`control: kelvin`), so the feedback input reported a number in a unit the
matching output does not accept.

## 2. Decisions

Taken with the maintainer on 30 September 2026:

1. **Existing keys keep their name, their value changes.** A signal's key
   is assigned once and never changed (`Store._assign_key`), because the
   Loxone virtual input recognises the value by it. A colour temperature
   input already created as `d5_1_colortemp_mireds` keeps that key and
   carries Kelvin after the update; `d7_1_illuminance` carries lux. No
   re-import in Loxone is needed, but a threshold or formula on those
   inputs has to be adjusted. The changelog says so.
2. **The raw value is shown for every converted signal**, not only for the
   two new conversions: whenever what the bridge sends differs from what
   the device reported - `scale` other than 1 or a `convert` - the WebUI
   shows the raw value as well. One rule, no exceptions.
3. **The converted value stays in its original place**, the raw value goes
   underneath, small, labelled "raw value".

## 3. The Conversion

### 3.1 `convert` in `clusters.yaml`

An attribute entry may carry `convert: <name>` instead of `scale`:

```yaml
1024:
  attributes:
    0: {slug: illuminance_lux, unit: lx, convert: lux_from_log}
768:
  attributes:
    7: {slug: colortemp_kelvin, unit: K, feedback: true, convert: kelvin_from_mired}
```

An entry with both `scale` and `convert` is rejected when the table is
loaded, as is a `convert` name that does not exist. A test walks the table
and checks every name, so a typo fails CI rather than a running bridge.

### 3.2 `profiles/conversions.py`

A new module holds a fixed mapping from name to function. Nothing from the
YAML file is evaluated; the YAML only chooses an entry.

| Name | Function | Edge cases |
|---|---|---|
| `lux_from_log` | `10 ** ((v - 1) / 10000)` | `v == 0` is "too low to be measured" in both specifications and yields exactly `0.0`, not the 0.99977 the formula gives. |
| `kelvin_from_mired` | `1_000_000 / v` | `v <= 0` is outside the specified range (1-65279) and yields `None`: the value is not sent, rather than dividing by zero. |

Each function takes the raw number and returns `float | None`. `None` means
"not sendable", the same meaning `to_loxone_value` already gives it.

The sentinel values never reach these functions: Matter's `null` is not
`ANALOG` (`classify`), and the Zigbee edge already suppresses 0xFFFF for
`IlluminanceMeasurement.MeasuredValue` (`zigbee/translate.py`).

### 3.3 `to_loxone_value`

`loxone/values.py` stays the single place where a raw value becomes a
Loxone value. After `struct_member` and `classify`, it applies the
conversion if the entry has one, and `scale_factor` otherwise. The value
itself is not rounded; the Loxone format string decides the display, as for
every other unit.

`profiles/table.py` gains `converter(ref)` beside `scale_factor(ref)`, and
`is_converted(ref)` - true when `scale` is not 1.0 or a `convert` is set.
The WebUI flag in section 5 reads this function, so the rule lives in one
place.

### 3.4 Units

`_UNIT_DECIMALS` gains `"K": 0` and `"lx": 0`. `"mired": 0` stays, because
the physical limits below remain in mired.

### 3.5 What Stays in Mired

`ColorTempPhysicalMinMireds` (16395) and `ColorTempPhysicalMaxMireds`
(16396) stay in mired. They are expert signals (`functional: false`) that
never change, and `api/control.py` and `api/groups.py` already convert them
for the Kelvin slider. Converting them would swap minimum and maximum
(the smaller mired is the larger Kelvin) and rename both - not worth it for
two constants.

## 4. Existing Installations

- **Key:** unchanged, see decision 1.
- **Unit:** follows on the next registration. `register_signals` already
  updates `unit` on every call for an existing signal.
- **Title:** the stored title is the slug at creation time, and from then
  on it belongs to the user (`set_title`). An existing colour temperature
  signal would keep the title `colortemp_mireds` next to a Kelvin value.
  A store migration renames the title of 768/7 from `colortemp_mireds` to
  `colortemp_kelvin` and of 1024/0 from `illuminance` to `illuminance_lux`
  - ONLY where the title still equals the old default. A title the user
  chose stays. The migration only updates rows, it adds and removes no
  column (migrations stay additive, so a rollback of the updater still
  works without restoring the database; the old version would then show
  the new title over a mired value until the next update, which is
  harmless).
- **Changelog:** an entry under Unreleased that says, in the user's terms,
  that these two inputs now carry Kelvin and lux and that thresholds or
  formulas on them have to be adjusted.

## 5. The Raw Value in the WebUI

### 5.1 Runtime

`Runtime._cache_attribute` already holds the raw value when it converts.
It stores it in a second map, `_last_raw[key]` - the value after
`struct_member`, before `scale` or `convert`. `last_raw_values_for(device_id)`
returns it next to `last_values_for`.

The observer signature becomes `(key, value, raw)`. Its only consumer is
`api/live.py`. For online state, events and pulse releases, `raw` is
`None`.

### 5.2 API

`SignalOut` gains two fields:

- `converted: bool` - `profiles.table.is_converted(signal.ref)`.
- `raw_value: float | bool | str | None` - from `last_raw_values_for`,
  `None` when the signal is not converted.

The live WebSocket message becomes `{"key", "value", "raw"}`, with `raw`
following the same rule: `None` unless the signal is converted.

### 5.3 Display

- **Signals dialog** (the full list, `index.html` around line 3499): under
  the value, a second line in a muted, smaller style: "raw value: 23000"
  / "Rohwert: 23000". Shown only when `signal.converted` and a raw value
  is known. The label comes from `strings.yaml` (`en` and `de`).
- **Device tile** (`index.html` around line 1369, 260 px wide): no second
  row. The raw value is appended to the tooltip that already shows the
  age of the value (`signalAgeTitle`).
- **Live updates:** `liveRaw[key]` is kept next to `liveValues[key]` from
  the WebSocket message, and a `liveRawOf(signal)` helper mirrors
  `liveValueOf`.

### 5.4 Control Dialog

`readStartValues` (`app.js`) reads the colour temperature from 768/7 and
divided `1e6` by it. The value is Kelvin now, so it is used directly
(rounded to a whole Kelvin for the slider).

## 6. Tests

- `conversions.py`: both functions, including `0`, negative and ordinary
  values; `lux_from_log(20001)` ≈ 100, `kelvin_from_mired(370)` ≈ 2703.
- Table: `convert` and `scale` together is rejected; an unknown name is
  rejected; every `convert` in `clusters.yaml` names an existing function;
  `is_converted` for a `scale` entry, a `convert` entry and a plain one.
- `to_loxone_value` for 1024/0 and 768/7, including the edge cases.
- Store migration: the default title is renamed, a user title is not.
- `Runtime`: `last_raw_values_for` after `on_attribute` and after
  `seed_from_snapshot`; the observer receives `raw`.
- API: `SignalOut.converted` and `raw_value`; the live message carries
  `raw`.
- WebUI: the tests prove delivery only. The Alpine bindings - the raw line
  in the dialog, the tooltip, the start value of the Kelvin slider - are
  run in a throwaway browser harness against the real `app.js`.

## 7. Not Part of This

- Converting the physical colour temperature limits (section 3.5).
- A conversion for colour X/Y - the pair only means something together.
- Renaming existing keys.
