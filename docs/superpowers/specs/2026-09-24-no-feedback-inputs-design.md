# A device Loxone controls does not report its state back by default

Date: 24 September 2026. Narrows "Signal selection" (2026-09-03), whose
section 4 decides which signals are preselected for export.

## 1. What is wrong

Exporting a light produces virtual inputs next to its outputs: `onoff`,
`level`, and for a colour light `hue`, `saturation`, `colortemp_mireds`,
`colormode`, `color_x` and `color_y`. They are the light's state reported
back — useful where something besides Loxone can switch the light (an app, a
voice assistant, a switch of its own), because Loxone would otherwise show a
state that is no longer true.

In the installation this bridge serves, Loxone is the only system that
controls these devices. Every such input is then a copy of what Loxone itself
just sent: it clutters the project file, and nobody wires it.

None of this is new. `onoff` has been an exported input since the export
existed (2026-09-02) and stayed preselected when 2026-09-03 narrowed the
preselection. Three later changes made it more visible:

- `4ca7d69` (2026-09-03) gave ColorControl an attribute section, so a colour
  light's colour values became preselected — four to six inputs at once.
- `1d89619` (2026-09-11) made the project-file sync create containers for new
  devices by default; before, that was behind a checkbox that was off.
- The Zigbee source (2026-09-12) brings lights of a second radio in, treated
  like Matter lights.

## 2. Goal

A signal that only reports back a state the device accepts as a command is
**not preselected** for export — for every kind of device, not only lights.
It stays stored, readable and selectable: whoever does need it ticks it in
the signal dialog, as with any other signal.

What stays preselected is everything Loxone does not set itself: sensor
readings (temperature, humidity, illuminance, occupancy, contact), button
events, a plug's energy readings, the battery level, and the device's
`online` input.

The change applies to devices commissioned from now on **and, once, to every
device already stored** (section 5).

## 3. What counts as feedback

A mark in the profile table, `feedback: true`, on the attribute entry in
`src/loxmatter/profiles/clusters.yaml`. It is set on exactly these entries:

| Cluster | Attribute | Slug |
|---|---|---|
| 6 OnOff | 0 | `onoff` |
| 8 Level | 0 | `level` |
| 768 ColorControl | 0 | `hue` |
| 768 ColorControl | 1 | `saturation` |
| 768 ColorControl | 3 | `color_x` |
| 768 ColorControl | 4 | `color_y` |
| 768 ColorControl | 7 | `colortemp_mireds` |
| 768 ColorControl | 8 | `colormode` |

Every one of them is the state behind a command in the same cluster
(`on`/`off`/`toggle`, `level`/`level_onoff`, `colortemp`/`color`/`color_xy`).
`colormode` has no command of its own, but it only changes as a consequence
of the colour commands, so it is feedback in the same sense.

`colortemp_phys_min_mireds` and `colortemp_phys_max_mireds` keep their
`functional: false` and get no `feedback` mark: they are device constants,
not feedback, and already unselected.

### 3.1 Why a mark in the table and not a derivation

The alternative was to derive feedback from the device: an attribute is
feedback when the device accepts a command in the same cluster on the same
endpoint. That is wrong in both directions for devices that exist:

- A thermostat accepts setpoint commands and, in the same cluster, reports
  the measured room temperature — a sensor reading, not feedback.
- The migration in section 5 runs when the database opens and has no device
  snapshot (see `_migrate_to_v3`'s docstring); it could not see which
  commands a stored device accepts.

The table already carries attribute knowledge of this kind
(`functional: false` for device constants, `profiles.table.marked_non_functional`),
and a mark there is readable without a snapshot.

**The cost:** a controllable cluster added to the table later (window
covering, thermostat, door lock) has to mark its feedback attributes when it
is added. The comment at the top of the table says so. A cluster the table
does not know stays as it is today — fully preselected — which errs towards
too many inputs, never towards a missing one.

### 3.2 Why not `functional: false`

`functional` means "wanted by default" and also decides where the signal
dialog shows a signal: functional signals first, the rest folded into the
expert block (`fe6f0bc`). Marking feedback as not functional would fold
`onoff` and `level` in among the physical colour-temperature limits. Feedback
is the device's main state and belongs at the top of the dialog — only
unticked.

## 4. The rule, in code

`profiles.table` gains `marks_feedback(ref: SignalRef) -> bool`, the
counterpart of `marked_non_functional`: it reads the entry for `ref`'s
cluster and element from the `attributes` section and answers whether it
carries `feedback: true`. Events never do.

The default for `exported` when a signal is **created**
(`Store.register_signals`) becomes:

```
exported = is_exportable(exportability) and is_functional(ref, device_types)
           and not marks_feedback(ref)
```

`functional` itself does not change, so the signal dialog keeps its order.
The update branch of `register_signals` still leaves `exported` alone for a
signal it already knows: from then on the value belongs to the user.

The Zigbee source needs nothing of its own: it builds a Matter-shaped
snapshot (`zigbee/translate.py`, `build_snapshot`) that reaches the store
through the same `register_signals`, so a Zigbee light and a Matter light end
up alike.

## 5. Devices already stored

A new store migration, `_migrate_to_v12` (`_SCHEMA_VERSION` 11 → 12), sets
`exported = 0` for every stored signal whose endpoint, cluster and element
the table marks as feedback (`kind` attribute). It reads the marked entries
from the table and writes one `UPDATE` per entry; it touches no other column
and no other row.

**It cannot tell a preselected feedback signal from one the user ticked by
hand**, because the store keeps no record of which is which. Both are
unticked. This was decided consciously (option A in the conversation of
2026-09-24): the installation this serves has no hand-ticked feedback, and
the alternative — leaving stored devices alone — would have left every
existing light with the inputs the change is meant to remove.

**Additive, as every migration here** (see the store module docstring on
updater rollbacks): no column is added or dropped. A rollback to the previous
image finds the database at version 12, which that image treats like any
newer database; the unticked signals simply stay unticked, which is a valid
state for it.

## 6. What does not change

- **The project-file sync** does not delete anything from a project file. An
  input for a now unticked signal that is already in the file is reported as
  orphaned (`projectsync.diff`, `PlanStatus.ORPHANED`); the user removes it
  in Loxone Config.
- **Outputs.** Every command is still exported as before.
- **The web UI's control dialog** reads current values from all signals,
  exported or not (`signalValueByPath` in `web/app.js`), so the sliders keep
  their start values.
- **Groups** export outputs only and are unaffected.
- **The `online` input** belongs to the device, not to a signal
  (`export/signals.py`), and stays.

## 7. Tests

- `profiles`: `marks_feedback` is true for each entry in the section 3 table,
  false for a sensor attribute (temperature), for `battery`, for an energy
  attribute, for an event and for an unknown cluster.
- `model`: registering the checked-in colour light
  (`tests/fixtures/nodes/ikea_kajplats_cws_lamp.json`) leaves `onoff`,
  `level` and the colour values unexported but functional; registering the
  plug (`ikea_grillplats_plug.json`) leaves `onoff` unexported and its energy
  values exported. The
  expected counts are derived in the test docstring, as the existing count
  tests do.
- `model`, migration: a version 11 database with a light and a sensor comes
  out of `_migrate_to_v12` with the light's feedback signals unexported and
  every other row unchanged — including a sensor signal the user had
  unticked and a device constant they had ticked.
- `export`: the light's template has no feedback inputs and still has its
  outputs and its `online` input.
- Existing tests that count exported signals of the light or the plug are
  adjusted, each with the new derivation in its docstring.

## 8. Documentation

- `CHANGELOG.md`, unreleased block: under **Changed**, that feedback inputs
  are no longer preselected and that existing devices lose them once on
  update, with the pointer to tick them again in the signal dialog and to
  remove orphaned inputs in Loxone Config.
- The comment at the top of `clusters.yaml` gains a paragraph on
  `feedback: true` beside the one on `functional: false`.
