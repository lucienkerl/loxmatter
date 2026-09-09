# Order signals by importance — and make the signal modal legible

Design, September 7, 2026. Affects the device tile from
[the device tab design](2026-09-05-devices-tab-rooms-and-tile-grid-design.md)
and the signal modal from [Signals as a modal](2026-09-05-signals-as-a-modal-design.md).
Also touches `export/signals.py`, because the ordering there has the same
source (section 5).

## 1. The finding

A "primary signal" is never *chosen*. The primary value of the tile is the
first element of a list:

```js
leadSignalFor(deviceId) {
  return this.firstSignalsFor(deviceId)[0] || null;
}
```

`firstSignalsFor` limits `functionalSignalsFor` to six, and this
comes unchanged from `GET /api/devices/<id>/signals`, which in turn
reads `Store.signals` — sorted with

```sql
ORDER BY endpoint, cluster_id, element_id, kind
```

This is a purely technical ordering. It answers "where in the Matter tree does that stand", not "what kind of device is this here". And because Matter places the
**PowerSource cluster (47) on endpoint 0**, while the actual
utility cluster sits on endpoint 1 or 2, the battery level automatically wins on **every**
battery-powered device.

Verified against the four commissioned devices (the tiles come from
`scripts/dev_web_server.py` via the snapshots in `tests/fixtures/nodes/`;
`~/.loxmatter/loxmatter.sqlite` is empty and still on the schema before
room/category):

| Device | Primary value today | Source | Should be |
| --- | --- | --- | --- |
| Hallway button (IKEA BILRESA) | `battery` 12.4 % | `0/47/12` | `press` |
| Living room lamp | `VendorName` | `0/40/1` | `onoff` |
| Kitchen spots | `onoff` | `1/6/0` | — |
| Coffee machine (GRILLPLATS) | `onoff` | `1/6/0` | — |

Two out of four wrong, and both for the same reason: endpoint 0 sorts before
endpoint 1, and only administration sits there.

**The `VendorName` case is half a snapshot artifact and must not
be counted as a second error.** `example_light.json` carries no
descriptor on any endpoint (`<ep>/29/0` is completely missing, verified against the snapshots) — this means the utility endpoint layer in
`relevance.is_functional` does not apply, and BasicInformation attributes are treated
as functional. A certified device declares `RootNode` there and
falls out. The *weakness* is the same regardless and is fixed by this
design: the primary value today is "what sorts first", not
"what matters".

## 2. The second finding: the modal

The signal modal (`index.html`, `<dialog class="signals-modal">`) shows per
signal a `.row` with seven controls — key pill, title field,
path, value, "export" checkbox, "resend periodically" checkbox, and for each attribute an additional second `.row` spanning
the full width with raw value field and button. With 17 functional
signals on the button, that is over one hundred elements with no hierarchy.

Five concrete causes, readable in the screenshot `docs/screenshots/signals.png`:

1. **No column headers.** `1/59/2` stands there without comment.
2. **Nothing aligns.** The `.row` is a `flex-wrap` container without
   column dimensions; at `multipress_ongoing`, "resend periodically"
   wraps alone to the next line. The eye finds no column.
3. **Name duplication without resolution.** `press` appears twice in the list —
   `1/59/1` and `2/59/1`, two different buttons on the same
   remote, identically labeled. Nothing says which is which.
4. **The raw value field has the same weight as everything else.** A
   tool for experimentation claims a full row at each attribute.
5. **No grouping.** 17 functional signals as a flat list, below that
   156 under "Expert".

## 3. The decisions

From the design meeting, all four confirmed:

1. **Cluster ranking**, not one lead cluster per category and not
   mere demotion of endpoint 0. A ranking works also for
   device types this tool has never seen — the same reasoning
   that `relevance.py` uses, relying on Matter's own structure rather than
   a list of cluster numbers someone finds boring.
2. **The ranking sorts the entire short list**, not just the primary value.
   One rule instead of two; the rows under the heading then read
   just as much by importance as the heading itself.
3. **Sorting happens at the source.** WebUI *and* Loxone template follow
   the same ordering (section 5).
4. **The battery gets its own row on the tile** instead of falling out of
   the preview (section 6).
5. **The modal becomes a table with endpoint groups** (variant A from the
   design canvas, section 7).

## 4. The ranking

It lives as `rank:` per cluster in **`profiles/clusters.yaml`** — the same
file that already carries title, unit, and scaling for that cluster.
No second place where cluster knowledge lives, and no Python dictionary
alongside a YAML table that already halfway answers the same question.

```yaml
clusters:
  6:            # OnOff
    rank: 10
  59:           # Switch
    rank: 10
  1026:         # TemperatureMeasurement
    rank: 10
  1029:         # RelativeHumidityMeasurement
    rank: 10
  8:            # LevelControl
    rank: 20
  768:          # ColorControl
    rank: 30
  144:          # ElectricalPowerMeasurement
    rank: 40
  145:          # ElectricalEnergyMeasurement
    rank: 40
  47:           # PowerSource
    rank: 90
  40:           # BasicInformation
    rank: 95
```

Three properties that carry the design:

- **Smaller rank first.** What a device *does* in the house ranks at 10–40;
  what it says about itself ranks at 90+.
- **A cluster without `rank:` gets 50.** This way unknowns land in the
  middle — behind what demonstrably matters, but before battery and
  device details. This is the conservative answer: a new device type
  never accidentally gets battery as its primary value, and its
  actual main feature is not banished behind known ones just
  because no one has entered a rank yet.
- **Within a rank, today's ordering stays.** Endpoint, cluster,
  element, kind — it is stable and correct there; it orders two signals
  of the same cluster, and it does that well. The ranking only orders
  the clusters relative to each other.

The ordering is therefore `(rank, endpoint, cluster_id, element_id, kind)`.
It is total and deterministic: `rank` is a number per cluster, the rest
is the previous, already unique key (UNIQUE constraint on
`signal`).

**Why no rank per element.** It would be possible to put `press` within
cluster 59 before `positions`. This is deliberately *not* part of this
design: the element ordering within a cluster today follows the
element ID, which is already roughly assigned by importance in the Matter
specification itself. A second rank level would be effort without proven
gain — it can be added later if a specific device requires it.

## 5. Sorting happens at the source

`Store.signals` gets the new ordering. This affects **both**:

- **The WebUI**, via `GET /api/devices/<id>/signals` — tile and modal
  without their own sorting in the frontend.
- **The Loxone template**, because `to_inputs(signals, …)` in
  `api/export.py` (lines 147 and 326) writes exactly this order into the
  VIU file. In the Loxone tree, the button press then stands at top and
  battery at bottom instead of the other way around.

The price is a newly loaded template that lists its inputs differently
than the previously downloaded one. **This is verified and consequence-free:**

- **The project file sync matches by key, not by position.** `_plan_inputs` in `projectsync/diff.py` looks up each entry
  with `index.input_cmds.get(entry.key)`; `_orphaned_entries` also
  uses keys. Reordering produces neither false changes nor duplicates there.
- **"Changed since export" depends on `updated_at`, not on file content**
  (`_changed_since_export` in `api/export.py`). No device jumps to "changed"
  because of this change.
- **The keys themselves remain untouched.** They are
  key material (main document 6.2) and are not touched by the sorting — the
  wiring in Loxone survives.

What follows from this and belongs in a test: a template that a
user imported **before** this change remains fully operable via the
key matching. Ordering is presentation,
not identity.

## 6. The tile

**The primary value** is the first-ranking functional signal. On the button it is
`press`, not `battery`; on the light `onoff`, not `VendorName`.
`leadSignalFor`/`firstSignalsFor`/`restSignalsFor` remain unchanged — they
read the same list, which now comes sorted differently. **Not one line
of frontend code changes for the primary value itself.**

**The battery gets its own footer.** This is the consequence the ranking
enforces and that was separately decided: at rank 90, the
battery stands behind all 16 other functional signals on the button and
would fall out of the six preview rows (`FUNCTIONAL_PREVIEW_LIMIT`)
— it would not be visible on the tile at all.

It therefore appears **below** the preview rows and **below** the
"+ N more" link, separated by a dashed line, with
battery icon, the word "Battery" and the percentage value in `--warn`:

```
┌─────────────────────────────────┐
│ ▣  Hallway button       true    │
│    PRESS                        │
│                                 │
│ longpress                 true  │
│ shortrelease              true  │
│ longrelease               true  │
│ multipress                true  │
│ position                     1  │
│ + 10 weitere                    │
│ ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈ │
│ 🔋 Batterie             12,4 %  │
└─────────────────────────────────┘
```

Three rules for it:

- **It does not count against `FUNCTIONAL_PREVIEW_LIMIT`.** The six
  preview slots remain for utility signals. The "+ N more" counter must
  therefore book the battery as shown — otherwise it counts it twice.
  (This exact error appeared in the first design canvas: "+ 11 more" on
  a tile showing seven of 17 signals.)
- **It appears only if the device has a functional PowerSource signal.**
  A mains-powered device does not get the row and is not
  made one empty row taller.
- **It is never the primary value**, even on a device whose only
  functional signal is battery. There the primary value remains empty, as
  today already with a device without functional signals
  (`leadSignalFor` returns `null`, the wrapper stays out via `x-show`).

## 7. The modal

Variant A from the design canvas: the same row as today, but with
column headers, fixed column dimensions, and grouped by endpoint.

### 7.1 Structure

```
Signale — Hallway button
IKEA of Sweden · BILRESA dual button · Hallway
┌───────────────────────────────────────────────────────┐
│ ▤ 12 von 17 Signalen gehen als Eingang nach Loxone    │
│                                    [Alle abwählen]    │
└───────────────────────────────────────────────────────┘
Der Schlüssel ist die Verdrahtung in Loxone … Periodisch heißt …

EXPORT │ SIGNAL      │ LOXONE-EINGANG │ WERT │ PERIODISCH │
────────────────────────────────────────────────────────────
▎Taste 1   Endpunkt 1 · Switch (59)              8 Signale
  ☑    ⏱ press          d4_1_press      true      ☐     ⋯
  ☑    ⏱ longpress      d4_1_longpress  true      ☐     ⋯
  ☑    ≡ position       d4_1_position      1      ☑     ⋯
       Endpunkt 1 · Cluster 59 Switch · Element 1 CurrentPosition
       [Rohwert schreiben            ] [Schreiben]
  ☐    ≡ positions      d4_1_positions     2      ☐     ⋯
  ⌄ 4 weitere Signale von Taste 1
▎Taste 2   Endpunkt 2 · Switch (59)              8 Signale
  …
▎Gerät     Endpunkt 0 · Power Source (47)        1 Signal
  ☑    🔋 battery       d4_0_battery   12,4 %     ☑     ⋯
────────────────────────────────────────────────────────────
⌄ Experte                              156 weitere Signale
```

### 7.2 The six columns

`display: grid` with `grid-template-columns: 58px minmax(0, 1fr) 150px 70px
76px 28px`, the same template in the header and each data row — this is
what is missing today and why nothing aligns.

The column header sticks to the top when scrolling (`position: sticky`); without it
the checkbox columns lose their meaning at 173 signals, as soon as the
header leaves the view.

### 7.3 Both yes/no columns are checkboxes

The first design showed "export" as a checkbox and
"resend periodically" as a toggle switch. There was no reason
that holds up: both are the same case — a yes/no per signal, in
the same row.

**The rule that applies instead: the control follows the container,
not the meaning.**

- **In a table, checkboxes.** They align in a column, stay
  compact, and read as "this row belongs in this set". A
  column of 17 toggle switches is a distinctly louder texture than 17
  checkmarks — and loudness is exactly the problem this modal has.
- **In a detail area, toggles**, one per row with an
  explanatory sentence next to it. That is variant B, which is not being built;
  the rule stands here anyway so it is not reinvented at the next detail area.

The column headers are called **EXPORT** and **PERIODIC** — both single
words that fit in 76 px. What "periodic" means stands
**once** above the table instead of seventeen times as a label next to a
checkbox. The translation keys `web.signals.export_checkbox` and
`web.signals.resend_checkbox` remain as `title`/`aria-label` of the
checkboxes — the screen reader needs the label per checkbox,
the eye does not.

### 7.4 Groups resolve name duplication

Per endpoint, one group header with a descriptive name, technical
origin, and count:

```
▎Button 1      Endpoint 1 · Switch (59)      8 signals
```

The descriptive name comes from the device type of the endpoint, plus a
running counter if the same type appears multiple times: two
`GenericSwitch` endpoints produce "Button 1" and "Button 2". An endpoint with
utility type is called "Device".

**The data situation must be verified, not assumed.** The mapping
endpoint → device types is persisted in the column
`device.device_types` (`_migrate_to_v7`) and is already
read via `category_for(device.device_types)` in `api/devices.py` — it is available,
but with two limitations:

- **It can be `NULL`**, as long as `backfill_device_types` has not
  run for this device. Then there is no descriptive name, and the group
  is simply called "Endpoint 1". This is the fallback, not an error case:
  the same treatment `category_for(None)` already gets with `OTHER`.
- **A table device type → descriptive endpoint name does not yet
  exist.** `CATEGORY_BY_DEVICE_TYPE` in `categories.py` maps to
  device categories ("switch", "light") — those are names for a
  whole device, not for an endpoint in it. A remote is
  *one* switch with *two* buttons; "switch 1"/"switch 2" would be wrong.
  So it needs a small, separate mapping alongside the existing one, with
  the same sourcing requirement as there (number from
  `matter_server.client.models.device_types`, not from memory) —
  and with "Endpoint N" as fallback for any type not in it.
  The scope of this table belongs in the plan, not in this design.

So `press` appears once under *Button 1* and once under *Button 2* — the
duplication is no longer a puzzle, but the information that the remote
has two buttons.

**Groups follow the ranking**, not the endpoint number: the group
containing the first-ranking signal stands at top. On the button, "Device"
(battery only) therefore comes last, even though it is endpoint 0.

### 7.5 The raw value field moves into the row

Instead of claiming a second row spanning the full width at each attribute, the
`⋯` button at the row end opens an area
**directly below that row**. In it: the origin in plain text
("Endpoint 1 · Cluster 59 Switch · Element 1 CurrentPosition") and the
raw value field with its button.

This accomplishes two things at once — the tool gets the weight it
deserves, and the path `1/59/1` finally gets a place with
enough space to write it out instead of leaving it as a puzzle next to it.

At most one area is open at a time. The state lives in Alpine
(`expandedSignalKey`), not in the DOM: unlike the tile menu and
signal groups, there is exactly **one** value for the whole modal, no
open/closed per element.

### 7.6 The header

A summary replaces today's note paragraph as the first
element: "**12 of 17** signals go to Loxone as inputs", next to it
"Clear all". That is the number why the modal opens.

The key hint (`web.signals.key_hint`) stays, but moves below
the summary and takes the sentence about "periodic" with it.

## 8. What stays unchanged

- **`profiles/relevance.py`.** Which signals are functional is a
  different question from what order they stand in. `is_functional`
  does not change.
- **`profiles/categories.py`.** Device category orders devices
  relative to each other, ranking orders signals within a device. Two
  questions, two tables.
- **The keys.** `d4_1_press` stays `d4_1_press`.
- **`exported` and `exportability`.** The ranking says nothing about
  whether a signal is exported — only where it stands.
- **`FUNCTIONAL_PREVIEW_LIMIT` stays at 6.**
- **The Expert block** stays a collapsed `<details>` with the same
  `signalGroupsFor` template; it does *not* get the endpoint grouping,
  because there 156 signals span all endpoints and grouping would only
  create more headers.

## 9. Tests

- **`rank` per cluster is valid.** Every `rank:` in `clusters.yaml` is
  a number; no cluster carries two.
- **A cluster without `rank:` gets 50.** Checked directly against the loader,
  not via a device.
- **The button leads with a switch signal, not battery.**
  Against `ikea_bilresa_button.json`, the first functional signal.
- **The plug continues to lead with `onoff`.** Against
  `ikea_grillplats_plug.json` — the change must not shift the two
  currently correct devices.
- **Battery stands behind all utility signals**, but ahead of nothing
  unknown: a synthetic snapshot with a cluster without `rank:`
  confirms that it comes before 47.
- **The ordering is total.** Two signals of the same cluster keep
  their previous relative order (endpoint, then element).
- **Export follows the same ordering.** `to_inputs` against the button:
  the input for `press` comes before the one for `battery`.
- **A project file imported before the change remains matchable.**
  `build_plan` against a project file with inputs in the OLD
  order: no entry counts as new, none as orphaned.
- **The tile counts correctly.** On a device with a battery row,
  "+ N more" states the number of *not shown* signals — battery
  counts as shown.
- **A mains-powered device has no battery row.**
- **The modal aligns.** Header and data row carry the same
  `grid-template-columns`.
- **Both checkbox columns are `input[type="checkbox"]`** — no
  toggle switches in the modal.
- **Each checkbox carries a label** from `strings.yaml`, even
  if it is only visible to assistive technology.

## 10. Open points

- **Wrapping below about 640 px.** Six columns do not fit there. The
  table must break into stacked cards per signal; what they look like is
  not set in this design and belongs in the plan.
- **The ranks are an initial assignment.** They rely on the nine
  clusters that `clusters.yaml` carries today. A cluster added later
  needs a reasoned placement — by the same standard
  as `UTILITY_ENDPOINT_KEEP_CLUSTERS` in `relevance.py`: a concrete
  assignment on the device or in the specification, not the assumption that the
  table is complete by itself.
