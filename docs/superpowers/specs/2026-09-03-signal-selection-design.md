# Signal selection: relevant values instead of complete datasheets

Design, September 3, 2026. Supplements
[the main document](2026-09-01-matter-loxone-bridge-design.md), specifically
its sections 3.5 (generic decomposition), 5 (data model), 6.2 (keys), 7.3
(scaling), and 8 (WebUI).

## 1. The problem

An IKEA plug delivers 159 signals, of which 109 are technically mappable to
a Loxone UDP input. Of those, **four** are what you buy a plug for: on/off,
voltage, current, power. 55 of the 109 are Thread radio counters, another
eight are serial numbers and firmware versions.

*(These 109 are the starting finding before this design — see section 5:
the fifth wanted value, the meter reading, is not yet technically among
them, because section 4.4 shows it as a struct, which the generic
decomposition discards up to this point. After section 5 is implemented, it
is 110 — the meter reading joins the technically mappable ones. Evidence:
`tests/loxone/test_values_real_device.py::test_exactly_110_signals_yield_a_value`.)*

A user thus gets a template with 110 virtual inputs for a device with one
switch. That was the complaint this project began with ("otherwise you end
up with 200 inputs afterward and can't assign them to a single device") —
it has been solved by the per-device export, but only within a device, not
for the individual device itself.

Second, the value is missing for which you buy a *metering* plug in the
first place: cumulative consumption in kWh. Matter delivers it as a struct
(energy value plus timestamp), and the generic decomposition discards
structs. The instantaneous power arrives, the meter reading never.

## 2. What this design does not touch

The founding bet from section 3.5 of the main document: the tool does not
know Matter in detail, it decomposes generically so that a device type
works that no one foresaw when it was built. Confirmed in phase 1 on two
real devices.

An allowlist ("only what I know gets through") was therefore rejected. It
would give the cleanest result today and leave an unfamiliar device silent
tomorrow — without anyone noticing that something is missing.

Also unchanged: `bridge_alive`, `d<n>_online`, the command allowlist, and
the events. Button presses explicitly remain standard equipment; they were
the project's very first requirement of all.

## 3. Two concepts that stay separate

| Concept | Question | today |
|---|---|---|
| `Exportability` | Can the value be mapped to a UDP input at all? | present |
| **Relevance** | Does a user want it by default? | **new** |

A Thread radio counter is exportable, but not relevant. A struct without a
named element is not exportable, no matter how relevant it would be.
Mixing the two would be the mistake that no one untangles again later.

**Correction (final review, minor fix 9):** unlike `Exportability` (a real
type, `profiles.table.Exportability`), "relevance" here is a prose term,
not an identifier — in code the function is called
`profiles.relevance.is_functional`, the column `StoredSignal.functional`.
An earlier version of this section wrote "Relevance" in code formatting, as
if it were a type name, which does not exist as such.

Relevance decides only about the **default value** of the already existing
column `exported`. The mechanism — toggleable per signal via
`PATCH /api/signals/{key}`, evaluated in `export.signals.to_inputs` —
stays as it is.

## 4. The selection rule

Three layers, in decreasing order of generality and increasing maintenance
effort.

### 4.1 Structure: the device type per endpoint

The descriptor cluster (29) carries the attribute `DeviceTypeList` on every
endpoint — a list of standardized device type numbers from the Matter
Device Library. No manufacturer field, no free text: a device without this
information is not certified.

On the two test devices:

```
Plug        Endpoint 0: OTA Requestor, Root Node
            Endpoint 1: On/Off Plug-in Unit
            Endpoint 2: Electrical Sensor

Button      Endpoint 0: OTA Requestor, Power Source, Root Node
            Endpoint 1: Generic Switch
            Endpoint 2: Generic Switch
```

From this the rule:

> An endpoint that declares **Root Node** or **OTA Requestor** is
> administrative — its signals are off by default.
> Every other endpoint is the device — on.
>
> Exception on an administrative endpoint: the clusters of an application
> device type also declared there. In practice that is **Power Source**,
> i.e. the battery level.
>
> Off on every endpoint: **Identify (3)**, **Groups (4)**, **Descriptor
> (29)** — Matter internals with no meaning for a home automation system.

This explains the exception instead of just declaring it: the button's
battery level is not coincidentally on endpoint 0, but because the standard
provides for the device type Power Source there, and the device says so
itself.

**To verify before implementation:** the three device type numbers (Root
Node, OTA Requestor, Power Source) are in the Matter Device Library,
**not** in the installed SDK — its catalog covers clusters, not device
types. They are to be established against the specification and not taken
from this document. The mapping here is inferred from the test devices:
the button declares exactly one type more than the plug, and the button is
the battery-powered device.

**Edge case:** if an endpoint declares no device type at all
(non-conformant device, empty list), it counts as an application endpoint.
When in doubt, one input too many, never a missing value.

### 4.2 Names: the SDK's cluster catalog

`chip.clusters.Objects` — already a dependency of this project, already in
use for sending commands — contains all 140 standard clusters with all
attribute names, generated from the CSA's data model. `c47_a12` is called
`BatPercentRemaining` there.

Going forward, signals get their title from there when the project's own
profile table doesn't know anything better. Cost: zero maintenance, effect:
every standard attribute of every manufacturer has a readable name instead
of `cXX_aYY`.

The catalog delivers **names, not relevance**. `StartUpOnOff` is a perfectly
proper standard name for an attribute nobody wants in Loxone.

### 4.3 Meaning: the project's own profile table

`profiles/clusters.yaml` remains for what the SDK fundamentally cannot
know:

- the Loxone-side unit and conversion (W → kW, section 7.3),
- which element of a struct is the value (section 5 below),
- the fine selection within a known cluster.

**Fine selection:** if the table knows a cluster, then by default only the
attributes **named** there are relevant. If it does not know it, the
entire cluster remains relevant (section 2).

The table already names exactly the right ones today:

| Cluster | named | yields |
|---|---|---|
| 6 onoff | 0 | `onoff`; the configuration values 0x4000–0x4002 drop away |
| 144 power | 4, 5, 8 | `voltage`, `current`, `power`; 21 measurement ranges drop away |
| 145 energy | 1, 2 | consumption in and out |
| 59 switch | 0, 1 + events | `press`, `longpress`, `multipress` … |

New to add is cluster 47 (PowerSource) with `BatPercentRemaining` as
`battery` in percent. Matter counts in half-percent there — the factor
belongs in the table, not in the user's head.

### 4.4 Result

| | today (technically mappable) | after (exported by default) |
|---|---:|---:|
| Plug | 110 | **5** — on/off, voltage, current, power, consumption |
| Button | 122 | **17** — both rockers in full, battery level |

Worked out on the real devices, not estimated (recounted on the implemented
state, `tests/model/test_store.py`, `tests/profiles/test_relevance.py`):
the structure rule alone brings 110 → 19 and 122 → 27; the fine selection
from 4.3 does the rest. The plug's 110 already counts the meter reading
from section 5 — before its implementation it would have been 109 → 18,
see the footnote in section 1.

The button's 17 are, per rocker, `positions`, `position`, and the six
events (`press`, `longpress`, `shortrelease`, `longrelease`,
`multipress_ongoing`, `multipress`), plus `battery`. The events are not
subject to the fine selection from 4.3 — they are named in the table
anyway, and a discarded event would be a button press that never arrives
in Loxone.

## 5. Numbers from structs

The profile table gets an optional field `field`:

```yaml
145:
  name: energy
  attributes:
    # field: 0 = EnergyMeasurementStruct.energy, established against the
    # installed SDK (chip.clusters.Objects.ElectricalEnergyMeasurement.Structs).
    1: {slug: energy_imported, field: 0, unit: "kWh", scale: 1.0e-6}
```

`field` is a **field number, not a name**: matter-server delivers structs
as a dictionary with the field tag as the key, and as a string at that —
the descriptor cluster arrives, for example, as `[{"0": 18, "1": 1}]`. An
implementation that accesses `value["energy"]` finds nothing.

If `field` is set and the value is a struct that contains this field as a
number, the signal is exportable as analog; the rest of the struct
(timestamp) is left out. If the element is missing or is not a number, the
signal remains not exportable — **there is no guessing**. A made-up number
on a real energy meter would be worse than a missing value.

Only a cluster the table knows is allowed to do this. An unknown struct
stays unknown.

This affects `to_loxone_value` (runtime) and the exportability
classification in the decomposition — both must reach the same decision,
or the UI reports a value the export doesn't know.

## 6. Existing devices: migration without key changes

The signal rows store title, unit, and exportability **along with the
rest**. A table extension therefore does not take effect retroactively on
its own.

**Two migrations, not one.** Implemented as schema **v3**
(`_migrate_to_v3`, task 7): re-derives title, unit, exportability, and the
default value of `exported` for every existing signal. Task 8 (phase 6,
outside the original scope of this design, but the same question) adds
schema **v4** (`_migrate_to_v4`): a dedicated column `signal.functional`,
retroactively populated from the same substitute rule (see below) —
separate from `exported`, because from the first time a signal is known,
`exported` belongs to the user (a manually flipped checkbox stays flipped
on every *subsequent* re-read: `register_signals`/`set_exported` no longer
touch an `exported` that is already known), whereas `is_functional` is a
pure property of the device type.

**Correction (final review, minor fix 2):** the paragraph above correctly
describes the rule for ongoing operation, but not for this one update
itself. `_migrate_to_v3` rewrites `exported` once for **every** existing
row, regardless of whether a user had previously flipped it by hand — the
schema has no column that distinguishes "automatic default" from
"deliberate user decision," and can therefore not uphold this distinction
for THIS update. Anyone who, before this update, manually enabled twelve
Thread counters or renamed a signal loses that on the first start of this
version, without warning. The all-clear, as far as it goes: the runtime
path (`loxone.runtime`) does not filter on `exported` when sending — an
existing UDP wiring does not die because of this. Only a NEWLY generated
Loxone template after this update is affected. Details: the
`_migrate_to_v3` docstring in `src/loxmatter/model/store.py`, section "What
this migration otherwise CANNOT do"; the note for the operator audience is
in the main README under "What an exported template contains by default".

**The key remains untouched.** A button commissioned before the update
keeps `d2_0_c47_a12` and is then called "battery" in percent. One
commissioned after the update gets `d2_0_battery`. Two keys for the same
value is ugly; a renamed key would be a silently dead function block in
someone else's config, and that is the one thing this tool may never do
(section 6.2 of the main document).

**The substitute rule deviates from the real descriptor evaluation — not
just theoretically.** A migration runs when the database is opened
(`sqlite3.Connection`), never with a `NodeSnapshot` — the descriptor
cluster (4.1) is fundamentally inaccessible to it. Both migrations
therefore make do with the same substitute rule
(`store._endpoint0_device_types`, shared between `_migrate_to_v3` and
`_migrate_to_v4`, see their detailed docstrings in
`src/loxmatter/model/store.py`): **endpoint 0 always counts as Root Node**
(Matter Core Specification 9.2.1 — the only statement that can safely be
made without a snapshot), **every other endpoint counts as an application
endpoint**, and Power Source counts as declared on endpoint 0 as soon as a
signal of cluster 47 is stored there at all. This is an approximation, not
a reproduction of the real rule from 4.1 — that section explicitly states
that this assumption has "never been confirmed by any capture." Cross-checked
against both checked-in snapshots, it delivers the same number of
exported/functional signals for the plug and the button as a fresh
registration with a real snapshot — 5 and 17 respectively — but that is
verified on these two devices, no guarantee for every conceivable device
(e.g. one with a second administrative device type that is not Root Node,
or an application device type with a cluster other than 47 on endpoint 0 —
both cases would lie outside the two test devices, and the substitute rule
does not know them).

In addition, `_migrate_to_v3` raises `exportability` in a single, narrowly
bounded case, instead of leaving the stored value untouched: if a table
entry today carries a field number (section 5, the meter reading), the
element extracted from it counts as ANALOG regardless of the stored value.
This has a known, deliberately accepted limitation: a counter that was
`null` at registration time (never measured) is nonetheless raised to
ANALOG and counts as exported — a Loxone input that never carries a value.
`to_loxone_value` still returns `None` for it at runtime, so no made-up
value flows; only the generated template gets one input too many. Details
and the trade-off for why the exception remains anyway: the
`_migrate_to_v3` docstring, section "Two open limits of this exception".

Deliberately retroactive instead of only for new devices: two rule sets
side by side — old devices one way, new ones another — would be impossible
to explain to anyone in the long run, and the difference would hinge on
the commissioning date, which nobody keeps in their head.

## 7. UI

The signal list gets two blocks: **Functional** (open) and **Expert**
(collapsed, with a count). A "Show expert signals" toggle expands the
second one. Every signal keeps its own export checkbox; the expert block is
exactly the place where you deliberately turn one on, for instance a
Thread counter for troubleshooting.

The device tile will going forward show the functional signals instead of
the six with the smallest cluster number (open item from the final review
of phase 5: today those are NetworkCommissioning and BasicInformation, i.e.
neither on/off nor power).

The export preview additionally names how many signals are hidden as
expert (`ExportDeviceOut.hidden_count`, column "Withheld as expert" in the
preview table).

## 8. Error handling

| Case | Behavior |
|---|---|
| `DeviceTypeList` missing or empty | endpoint counts as application endpoint (on) |
| `DeviceTypeList` not readable (unexpected struct) | as above — silent, no log entry (see below) |
| Device type unknown | not an administrative type ⇒ on |
| Cluster unknown | fully on (section 2) |
| `field` set, element missing | not exportable, no guessing |
| Migration fails on a signal | row stays unchanged — silent, no log entry (see below); no abort |

**Correction (final review, minor fix 5):** two rows of this table
originally promised a log entry. Neither `profiles/relevance.py` nor
`model/store.py` imports a logging module — both paths deliberately
swallow the error case silently (when in doubt, one input too many or an
unchanged row, never a crash), without recording it anywhere. The table
above has been brought in line with the actual behavior.

## 9. Verification

The two real devices sit as snapshots in the repo and are the touchstones.
All tests run without hardware and without network.

- The plug yields **by name** `onoff`, `voltage`, `current`, `power`,
  `energy_imported` — not just "fewer than before." A test on the count
  would pass even with the wrong selection.
- The button yields both rockers in full, including `multipress` and the
  battery level. The case that shows whether the rule is too greedy.
- An unknown cluster on an application endpoint remains fully preserved.
  The test that guards the founding bet.
- An endpoint without `DeviceTypeList` counts as an application endpoint.
- The migration runs against a database built under the old rule and
  proves: **not a single key changes**, but title and unit do.
- A struct without the named element remains not exportable.
- Runtime and decomposition reach the same decision for structs.

## 10. Open items

1. ~~The three device type numbers are to be established against the
   Matter Device Library (section 4.1), not taken from this document.~~
   **Done (task 1).** The source is `matter_server.client.models.device_types`
   (part of the installed `python-matter-server` package) — per its own
   module docstring, machine-generated from `zcl/data-model/chip/
   matter-devices.xml` of the CSA specification. `chip.clusters.Objects`
   (installed chip SDK), by contrast, contains **no** device type table,
   only the cluster structure of the descriptor attribute itself — checked
   there, not assumed. Cross-checked against both checked-in snapshots
   (`tests/fixtures/nodes/`): Root Node `0x0016`, OTA Requestor `0x0012`,
   Power Source `0x0011`, matches the constants used in `relevance.py`. No
   direct look into the Matter Device Library Specification itself (no
   network access in the implementation environment) — this secondary, but
   machine-generated and empirically cross-checked source is what the
   three numbers rest on (details: `.superpowers/sdd/task-1-report.md`).
2. ~~Whether the fine selection from 4.3 should also apply to clusters
   whose names come from the SDK catalog is open. Proposal: no — the
   catalog knows no relevance, and "named" would then mean "all."~~
   **Decided, in the proposed sense (no).** `relevance.is_functional`
   asks `profiles.table.knows_cluster`/`names_element` — both evaluate
   exclusively `clusters.yaml` (`profiles.table._table()`), never the SDK
   catalog from 4.2. A cluster that only gets a name via the catalog but
   is not in `clusters.yaml` remains "unknown" for the fine selection and
   thus fully relevant — matches 4.2 above ("The catalog delivers names,
   not relevance").
3. A device with multiple application device types on one endpoint
   (bridges, combo devices) is unverified; the rule correctly treats it
   as an application endpoint, but none was available.
4. Whether the relevance classification (`is_functional`) should later be
   overridable by the user (a custom blocklist) remains open. Until
   someone asks for it: no. Explicitly not part of this phase (task 9,
   completion criteria).
5. **Not built (final review, minor fix 4):** that the migration records,
   per device, how many inputs drop away, and that the UI warns that the
   corresponding objects in Loxone become orphaned. Earlier versions of
   this section claimed both as done — neither `_migrate_to_v3`/
   `_migrate_to_v4` nor the WebUI records this number per device.
   `hidden_count` (section 7, task 8) is a snapshot of the current state,
   not a comparison to an earlier export.
6. **Not built:** a warning in the export preview when an export produces
   fewer inputs than the previous one. The preview shows `inputs` and
   `hidden_count` per device (section 7), but does not compare that to
   the previous export.
7. **Mired → Kelvin missing (final review, fix 1a).** Cluster 768
   (ColorControl), attribute `ColorTemperatureMireds` (7): Matter delivers
   mired, Loxone expects Kelvin, and `Kelvin = 1e6 / Mired` is a
   reciprocal — the existing `scale` mechanism in `clusters.yaml` can only
   multiply, not form a reciprocal. The value therefore stays in mired for
   now, with the slug `colortemp_mireds`, which makes that clear. A
   solution would need either a second conversion kind in
   `profiles/table.py` (an `invert` or `formula` field alongside `scale`)
   or a runtime conversion in `loxone.values.to_loxone_value` — outside
   the scope of this fix.
