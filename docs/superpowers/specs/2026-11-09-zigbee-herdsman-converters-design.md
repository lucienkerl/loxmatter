# The zigbee2mqtt Device Library Next to zha-quirks

Design, 9 November 2026. loxmatter learns the devices that
`zigbee-herdsman-converters` (the device library of zigbee2mqtt) describes,
in addition to what zha-quirks already gives it. This document decides how,
and in which order. Every number in it was measured on
9 November 2026 against `zigbee-herdsman-converters` 26.128.0 and
`zigbee-herdsman` 11.2.1 from npm, unless it says otherwise.

**Built on 9 November 2026**, both stages in one branch at Lucien's
request (`beide Stufen anfangen und umsetzen`). Where the build departed
from this design, section 14 says how and why; the sections above it are
left as decided, so the reasoning stays readable.

It builds on PR #40 (`2026-11-08-device-configuration-values-design.md`,
not merged at the time of writing): its `ConfigOption`, the option command
rows, the pending-write table and the wake-up watcher are the place every
setting from the new library lands. PR #40 itself stays as it is.

## 1. What Was Asked

On 8 November 2026 the maintainer noticed that zigbee2mqtt shows the keep
time of his HOBEIAN ZG-204Z, while Home Assistant and loxmatter only could
after PR #40 added a quirk of its own. He asked to bring
`zigbee-herdsman-converters` into loxmatter, because it seems to hold more
device knowledge than Home Assistant's library, and ideally to combine both.
Asked what it would take, he then asked whether the JavaScript could be
converted at build time so loxmatter gets zigbee2mqtt's behaviour. Told that
a translation to Python would not be reliable, and offered a design for
running the original JavaScript instead, he agreed (9 November 2026).

## 2. What the Library Is

| Fact | Measured |
|---|---|
| Licence | MIT (compatible with this project's GPL-3.0, like Alpine.js in `web/vendor/`) |
| Device definitions | 4653, in 405 vendor modules under `dist/devices/` |
| Definitions with hand-written JavaScript (`fromZigbee`, `toZigbee`, `configure` or `onEvent` of their own) | 1604 (34 %) |
| Definitions built only from shared building blocks | 3049 (66 %) |
| ... of which only from building blocks this design can read as data (section 5) | 2782 (60 %) |
| Tuya definitions with a data-point table (`meta.tuyaDatapoints`) | 525, with 6287 data points |
| ... of which every data point uses a converter this design can read as data | 267 |
| Data points with such a converter, over all 525 definitions | 5206 of 6287 (83 %) |
| What the library needs from `zigbee-herdsman` | `Zcl`, `ZSpec`, `getTimeClusterAttributes` - the ZCL codec and its cluster tables, not the stack |

The building blocks are `modernExtend` functions. Counted once per
definition that uses them: `light` 1670, `identify` 959,
`deviceAddCustomCluster` 786, `tuyaBase` 720, `onOff` 535, `battery` 478,
`deviceEndpoints` 390, `numeric` 368, `enumLookup` 344, `binary` 236,
`electricityMeter` 224, `temperature` 218, `iasZoneAlarm` 169, and a long
tail. 450 definitions use a vendor's own `modernExtend` that the library
does not export.

The 4653 definitions are not 4653 devices loxmatter cannot handle today.
zigpy reads every standard cluster without help, and zha-quirks covers
roughly 1350 manufacturer and model pairs. The gain sits where zha-quirks
has nothing: Tuya devices that only speak data points on cluster 0xEF00,
and manufacturer attributes - settings like the ZG-204Z's keep time and
readings that live outside the standard clusters.

The HOBEIAN ZG-204Z is one of the 34 %: its definition carries a local
`fromZigbee` and `toZigbee` converter (`fzLocal.zg204_attr`,
`tzLocal.zg204_attr` in `devices/hobeian.js`).

## 3. Decisions

1. **The library is taken as published, not rewritten.** loxmatter ships
   build artefacts generated from a pinned npm version, never a hand-kept
   copy of device knowledge.
2. **Node is a build tool only.** Nothing at runtime needs Node or a second
   process. Running zigbee2mqtt or a Node sidecar next to the bridge was
   ruled out on 8 November 2026: a second stack on the same radio is not
   possible, and a second process for device knowledge alone is a second
   thing to supervise, update and explain.
3. **Translating the JavaScript to Python is ruled out.** The hand-written
   converters call half the library (helpers, a per-device state store,
   timers, the Tuya protocol) and operate on zigbee-herdsman's device
   objects. A translator would have to carry all of that, every npm update
   would produce new unreviewed Python, and errors would only show on a real
   device.
4. **Two stages, both shipped from one build.**
   - **Stage 1, the catalogue.** What is data in the library becomes a JSON
     catalogue at build time, and Python reads it: settings and readings on
     ZCL attributes, and a Tuya data-point layer. No new runtime dependency.
   - **Stage 2, the engine.** Definitions with hand-written JavaScript run
     unchanged in an embedded JavaScript engine inside the bridge process,
     bridged to zigpy at the level of raw ZCL frames.
5. **zha-quirks keeps the first word** (section 7). A device it describes
   behaves exactly as today; the new library only fills what is missing.

## 4. The Build

### 4.1 Where It Lives

- `herdsman/package.json` and `herdsman/package-lock.json` pin
  `zigbee-herdsman-converters`, `zigbee-herdsman` (through the lock file),
  `esbuild` and the two polyfill packages `buffer` and `string_decoder`.
  Updating the library is a change to these two files and nothing else.
- `scripts/herdsman/build.mjs` produces both artefacts into
  `src/loxmatter/zigbee/herdsman_data/`, which is in `.gitignore`. A
  developer runs it once (`npm ci --prefix herdsman && node
  scripts/herdsman/build.mjs`); it takes seconds.
- The `Dockerfile` gains a first stage `FROM node:22-slim` that runs the same
  two commands and copies `herdsman_data/` into the Python stage. The
  published image therefore contains no Node, only the output.
- CI's test job runs the build before `pytest`. GitHub's runners carry
  Node, so the tests always see the artefacts of the pinned version.
- `herdsman_data/` also receives the library's `LICENSE` and a `README.md`
  stating the version, the same arrangement as `web/vendor/`.

### 4.2 The Two Artefacts

**`catalogue.json`** - written by loading every vendor module in Node with
the exported `modernExtend` functions, the Tuya `modernExtend` functions and
the Tuya `valueConverterBasic` factories wrapped, so that each building block
records the arguments it was called with. That this works was measured: the
counts in section 2 come from exactly this wrapping. Section 5 gives its
content.

**`engine.js`** - the library bundled by esbuild into one script for a
plain JavaScript engine (`platform: neutral`, IIFE). Measured:

- `zigbee-herdsman`'s package entry pulls in `serialport` and its native
  bindings, so the bundle cannot simply import it. A resolver plugin maps the
  bare name `zigbee-herdsman` to a shim exporting only `Zcl`, `ZSpec` and
  `getTimeClusterAttributes` from their `dist/` files. With that, nothing
  native is left.
- The library selects vendor modules with a computed `require` at runtime
  (`index.js`, through `models-index.json`). esbuild cannot follow that, so
  the build generates an entry file with one static `require` per vendor
  module, each behind a lazy getter: a vendor's definitions are only
  evaluated when a paired device needs them.
- `node:assert`, `node:crypto` (used once, for install-code hashing that
  loxmatter does not need), `node:fs` (used by the library's own indexer,
  bypassed here), `Buffer`, `process.env` and the timer functions are
  provided by small polyfills; the timers are the bridge's own (section 9.4).
- Result: 7.6 MB minified, 1.5 MB gzipped.

## 5. The Catalogue

One entry per definition, keyed by vendor module and position:

```json
{
  "id": "hobeian.js#47",
  "vendor": "HOBEIAN",
  "model": "ZG-204Z",
  "description": "Motion sensor",
  "match": {"zigbee_model": ["ZG-204Z"], "fingerprint": []},
  "mode": "engine",
  "exposes": [
    {"property": "keep_time", "type": "enum", "access": 7, "values": [30, 60, 120]}
  ],
  "attributes": [],
  "datapoints": [],
  "configure": []
}
```

- **`match`** holds `zigbeeModel` and every `fingerprint` (model ID,
  manufacturer name, and the further fields a fingerprint may carry). White
  labels are kept for display only.
- **`exposes`** is the definition's own list after `prepareDefinition`: every
  property with its type, unit, range or values, endpoint and access bits
  (read, set, get). This is what the web UI and the export can show, in both
  modes.
- **`attributes`** comes from `numeric`, `enumLookup` and `binary`: property,
  endpoint, cluster, attribute ID, ZCL data type, manufacturer code, scale,
  range, lookup table, reporting configuration, and whether it can be
  written. A custom cluster declared with `deviceAddCustomCluster` supplies
  the data types its attributes need.
- **`datapoints`** comes from `meta.tuyaDatapoints` and the Tuya
  `modernExtend` data-point blocks: data-point number, property, and the
  converter as data (`raw`, `onOff`, `lookup` with its table, `divideBy`
  with its divisor, `trueFalse0`, ...).
- **`configure`** lists binds and reporting that the building blocks would
  set up, and the Tuya start-up steps that `tuyaBase` performs (reading the
  Basic attributes that make many Tuya devices start reporting, the time
  sync).
- **`mode`** is `catalogue` when every building block of the definition is
  one this design reads as data and it has no hand-written converter of its
  own, otherwise `engine`. A building block called with a function as an
  argument (a computed scale, say) counts as hand-written. The build decides
  the mode, never the runtime.

Standard building blocks such as `light`, `onOff` or `temperature` describe
clusters loxmatter already reads natively. The catalogue records them so the
mode can be decided, but the runtime does not act on them.

## 6. Finding a Device's Definition

`zigbee/herdsman.py` matches a zigpy device to a catalogue entry with the
rules of the library's `findDefinition`: a fingerprint match beats a match on
`zigbeeModel` alone, and among fingerprints the one with more matching
fields wins. Definitions the library would generate on the fly for unknown
devices (`generateDefinition`) are not used: for those, loxmatter's own
reading of the standard clusters is the better fallback.

Matching is ported to Python rather than asked of the engine, so that stage
1 works without the engine. The build writes a golden table - model ID and
manufacturer name of every fingerprint, with the definition the library
itself picks for it - and a test checks the Python matcher against all of
it.

## 7. Which Library Speaks for a Device

| A device that ... | Settings | Readings and configuration |
|---|---|---|
| matches a zha-quirks quirk with entity metadata (upstream, or one of loxmatter's own in `local_quirks.py`) | the quirk's; the catalogue adds only settings on attributes the quirk does not describe | as today |
| matches no such quirk and a `catalogue` definition | the catalogue's | as today, plus the catalogue's attributes and data points |
| matches no such quirk and an `engine` definition | the engine's (stage 2) | the engine's for the properties it exposes, native for the rest (section 9.6) |
| matches nothing | none | as today |

Upstream zha-quirks first because it is what every installation already
runs, and a device that works today must not change behaviour on an
update. loxmatter's own quirks second, before the new library: the ZG-204Z
keeps PR #40's keep-time option and its command row. Its quirk is not
removed when the engine arrives, because the engine's option would be a
different command row, and a Loxone output already wired to `set_keep_time`
would silently stop working.

Until stage 2 is built, an `engine` definition still contributes whatever
its recognised building blocks describe; only its hand-written part is
missing.

## 8. Stage 1: Settings, Readings, Data Points

### 8.1 Settings on ZCL Attributes

A writable catalogue attribute becomes a `ConfigOption` from PR #40, with
its real endpoint, cluster and attribute, so it gets a command row, a
virtual output, an input and the configuration dialog without new plumbing.
What PR #40's quirk reader does not need but this one does: the data type
and the manufacturer code travel with the option, and the write uses
zigpy's `write_attributes_raw` with an explicitly typed value instead of
`write_attributes`, which only knows attributes the zigpy cluster declares.
Sleeping devices and the pending-write table work exactly as in PR #40.

### 8.2 Readings on ZCL Attributes

A catalogue attribute that is not writable is a reading. zigpy parses
attribute reports generically - each value carries its own data type - and
keeps values of attributes its cluster does not declare in the attribute
cache (`Cluster._update_attribute`, zigpy 2.2.0). `translate.py` places
the value - scaled and looked up as the catalogue says - at its path in the
snapshot, and the profile table names it (section 8.4).

### 8.3 The Tuya Data-Point Layer

Tuya devices without a zha-quirks quirk speak only on cluster 0xEF00, which
zigpy does not know: their frames fail to parse and are dropped today. So
stage 1 adds a **raw tap**: the Zigbee source sees every frame of a device
before zigpy parses it (zigpy's `Device.packet_received` is the single entry
point; the tap is installed per device, the way `configure.py` installs its
listeners). The layer decodes Tuya's data-point frames (`dataResponse`,
`dataReport`, and the time-sync request), converts each data point with the
catalogue's converter, and answers time sync. Writing a data point
(`dataRequest`) goes through `device.request` with a frame the layer builds.

A data point's property becomes a snapshot path through section 8.4; a
writable one becomes a `ConfigOption` on cluster 0xEF00 with the data-point
number as its attribute ID, which keeps PR #40's command ID scheme unique
per endpoint.

The other 258 Tuya definitions, where at least one data point or building
block is JavaScript, get the data points that are data in stage 1, and the
rest from the engine in stage 2.

### 8.4 From a Property to a Snapshot Path

loxmatter's middle is Matter-shaped (design 2026-09-12, section 2.1). A
library property therefore goes to a Matter path wherever one means the same
thing: `temperature` to 1026/0, `humidity` to 1029/0, `occupancy` to
1030/0, `illuminance` to 1024/0, `battery` to 47/12, `contact` and
`water_leak` to BooleanState, `power` and `energy` to their Matter clusters,
with the unit conversion stated per entry. The table is
`profiles/herdsman_properties.yaml`, reviewed like `clusters.yaml`.

Any other property - `keep_time`, `fading_time`, `self_test_result` - goes
to one manufacturer cluster that loxmatter reserves for the library, under
an attribute ID from `herdsman/property_ids.json`. That file maps each
property name to a number, is committed, and is only ever appended to by the
build. A signal's path, and with it its Loxone key, therefore stays the same
across library updates.

## 9. Stage 2: The Engine

### 9.1 Which Engine

| | quickjs-ng (`quickjs-ng` on PyPI, 0.18.0.1) | V8 (`mini-racer`, 0.14.1) |
|---|---|---|
| Wheel for linux aarch64 (the Pi) | yes, abi3, manylinux 2.17 and musllinux | yes, manylinux 2.27 and musllinux |
| Wheel size | 2.5 MB | 20 MB |
| Loads the bundle, measured here | yes | did not answer even `1+1` in the cloud container used for this design; not investigated further |
| Licence | MIT | ISC |

**Decision: quickjs-ng.** It is small, needs no JIT, and is the one
actually measured. Its risk is maintenance: the Python binding is maintained
by one person and its current release is four days old. The engine sits
behind one module (`zigbee/engine.py`), so swapping it touches nothing else.

Measured on the x86-64 cloud container (4 cores, Xeon 2.1 GHz), in a fresh
QuickJS context:

| Loaded | Time | Memory |
|---|---|---|
| library core only, no vendor module | 0.12 s | +13 MB |
| whole bundle parsed, only `hobeian` and `tuya` evaluated (621 definitions) | 0.85 s | +40 MB |
| whole bundle, all 4653 definitions evaluated | 1.4 s | +108 MB |

A Pi 4 is slower; the factor is not measured. For the zha-quirks warm-up,
`zigbee/quirks.py` extrapolates a factor of 4-6 from an M1 to a Pi 4, which
would put the lazy case at 3-5 s. That is paid once, off the event loop, and only when
a paired device needs the engine - the same three rules `quirks.py` already
follows. Measuring it on the Pi comes before the stage-2 implementation
plan.

### 9.2 The Probe That Settles the Approach

On 9 November 2026 the ZG-204Z's real definition ran in quickjs-ng from
Python, with a stub endpoint of a few lines:

- An IAS Zone attribute report as raw bytes (`18 05 0a 01f0 20 02 1300 20
  01`: attribute 0xF001 = 2, attribute 0x0013 = 1) was decoded by
  zigbee-herdsman's own `Zcl.Frame.fromBuffer`, turned into the converter's
  input by its own `attributeKeyValue`, and the definition's
  `fzLocal.zg204_attr` returned `{"keep_time": 120, "sensitivity":
  "medium"}`.
- Setting `keep_time` to 60 through `tzLocal.zg204_attr` made the stub's
  `write` produce the frame `10 07 02 01f0 20 01`: Write Attributes, 0xF001,
  uint8, value 1, no manufacturer code - byte for byte what PR #40's local
  quirk sends.

### 9.3 The Bridge

The engine never sees zigpy objects, and zigpy never sees JavaScript. They
exchange raw ZCL frames, and **zigbee-herdsman's own ZCL codec, inside the
engine, encodes and decodes them**, so a converter receives exactly the
data structures it gets in zigbee2mqtt.

- **In:** the raw tap of section 8.3 hands every frame of an `engine`
  device to the engine as cluster ID, endpoint, bytes, link quality. The
  engine decodes it, builds the message a `fromZigbee` converter expects,
  runs the matching converters and returns the properties they produced.
- **Out:** the engine implements zigbee-herdsman's `Endpoint` and `Device`
  in JavaScript. Every call that talks to the device encodes a frame and
  hands it to Python as `(endpoint, cluster, sequence number, bytes)`.
  Python sends it with `device.request(..., expect_reply=False)` and
  routes the reply, recognised by the sequence number in the raw tap, back
  into the engine, where the call's promise resolves. The sequence numbers
  come from zigpy (`device.get_sequence()`), so they cannot collide with
  loxmatter's own requests.

What the bridge has to implement is bounded, counted by call site in the
library's compiled code:

| Call | Sites |
|---|---|
| `getEndpoint` | 1635 |
| `read` | 1294 |
| `write` | 724 |
| `command` | 401 |
| `configureReporting` | 256 |
| `save` | 156 |
| `saveClusterAttributeKeyValue`, `getClusterAttributeValue` | 72, 40 |
| `bind`, `unbind` | 52, 6 |
| `supportsInputCluster`, `supportsOutputCluster` | 47, 12 |
| `commandResponse`, `readResponse`, `customReadResponse` | 19, 5, 12 |
| everything else | under 10 sites in total |

Device fields read by converters: `meta` (145 sites), `ieeeAddr`,
`manufacturerName`, `endpoints`, `powerSource`, `modelID`, the Basic
cluster's version fields, `customClusters`. All of them exist in zigpy's
device and its stored Basic attributes.

### 9.4 Running It

- **One thread.** A QuickJS context is not thread-safe, so every call into
  it runs on one dedicated executor thread, never on the event loop.
  Promises are driven by `execute_pending_job` until none is left.
- **Timers.** `setTimeout` and `setInterval` are the bridge's: the engine
  asks Python, Python schedules a callback on the event loop, and the
  callback re-enters the engine on its thread. 56 `setTimeout` and 4
  `setInterval` sites exist, mostly occupancy timeouts.
- **Limits.** Each call into the engine has a time limit
  (`set_time_limit`) and the context a memory limit. A converter that
  throws, hangs or exceeds a limit costs that one message and is logged
  with the device and definition; three failures in a row for one device
  disable the engine for that device until restart, and its tile says so.
  The bridge never stops because of the engine, the same promise
  `quirks.py` gives for the Zigbee stick.
- **Sandbox.** The engine has no file, network or process access - QuickJS
  has none unless the host adds it, and the bridge adds only frame sending,
  timers and logging.
- **State.** What zigbee-herdsman persists per device - `device.meta` and
  the attribute values saved with `saveClusterAttributeKeyValue` - goes to
  a new table `zigbee_herdsman_state (address, state_json)`, written when
  the engine calls `save()`. The library's global store stays in memory, as
  it does in zigbee2mqtt.

### 9.5 Configuring an Engine Device

On join, loxmatter's own configuration keeps the steps that belong to the
stack in zigbee2mqtt too - IAS enrolment and reading the static facts - and
then runs the definition's `configure` through the engine instead of its own
bind-and-report plan. The definition knows its device: the ZG-204Z's binds
only the power configuration cluster, where loxmatter's generic plan binds
everything it recognises. A device that does not answer during `configure`
is retried on its next wake-up, as `retry_pending` does today.

### 9.6 Readings, Settings and Commands from the Engine

- **Readings.** The properties a converter returns go to the snapshot
  through section 8.4. Where an engine property and loxmatter's native
  translation land on the same path, the engine wins for that device, so
  there is one truth per signal. The ZG-204Z's `occupancy` lands on 1030/0
  either way: its existing signal and Loxone key stay.
- **Settings.** An exposed property with the set access bit that is not one
  of the standard command properties (`state`, `brightness`, `color`, ...)
  becomes a `ConfigOption` on the reserved cluster, with the property's ID
  from section 8.4. Setting it calls the definition's `toZigbee` converter.
  Pending writes for sleeping devices are kept as in PR #40.
- **Commands.** Switching, dimming and colour stay native in this design.
  Devices whose standard commands do not work natively are a later step;
  none is known in an installation today.

## 10. Updating the Library

The pinned version moves by a pull request that changes
`herdsman/package.json` and the lock file. The build prints, and the pull
request quotes, what changed in the catalogue: definitions added and
removed, definitions whose mode changed, new property IDs. Updates are made
when there is a reason (a device that needs it, a fix), not on the
library's weekly release rhythm.

## 11. Tests

- The matcher against the build's golden table (section 6).
- Catalogue readers, the Tuya layer and the property mapping against small
  captured catalogue excerpts and captured frames under `tests/fixtures/`.
- The engine with the real bundle: the ZG-204Z probe of section 9.2 as a
  test, plus one Tuya data-point device and one device with timers.
- A test that fails when a property in `property_ids.json` changes its
  number.

## 12. Order of Work

1. **Build and catalogue** (section 4, 5) with the golden matcher test. No
   behaviour change yet.
2. **Stage 1 settings and readings on ZCL attributes** (8.1, 8.2, 8.4).
3. **Raw tap and Tuya data-point layer** (8.3).
4. **Measure the engine on the Pi** - load time and memory of the lazy
   bundle, read-only, without touching the running installation.
5. **Stage 2 engine** (section 9), first for readings and settings.

Each step is its own pull request with its own implementation plan.

## 13. Open

- The engine's load time and memory on a Pi 4 (section 9.1). Measured only
  in the cloud container so far: the whole bundle loads in 0.6 s.
- Why `mini-racer` did not answer in the cloud container. It only matters
  if quickjs-ng has to be replaced.
- Whether devices exist whose standard commands need the engine
  (section 9.6).
- A real Tuya device and a real engine device against the built bridge.
  The tests run the library's own definitions against captured and
  constructed frames; no radio was involved.

## 14. As Built

The code is in `src/loxmatter/zigbee/herdsman/` (`catalogue`, `properties`,
`tuya`, `zcl`, `engine`, `library`), the build in `scripts/herdsman/`, the
tests in `tests/zigbee/test_herdsman_*.py`. What differs from the sections
above:

**One address space for every library property (8.1, 8.3, 8.4).** A
setting is not an option on its real ZCL attribute, nor on cluster 0xEF00
under its data-point number. Every library property - reading or setting,
attribute, data point or engine - lives on one manufacturer cluster,
`VENDOR_PROPERTIES_CLUSTER` (0xFFF1FC10: the Matter test vendor and a
manufacturer cluster number), under its number from
`src/loxmatter/profiles/vendor_properties.json`. The option's path, its
command row, its pending write and its reading are then one and the same
path, whichever stage serves the device, and a device that moves from the
catalogue to the engine on a library update keeps its Loxone keys. The
write itself still goes to the real attribute (`write_attributes_raw`,
typed, with the catalogue's manufacturer code) or data point; only the
address loxmatter files it under is the library's. `ConfigOption` needed no
new fields for that.

**The property numbers** are `profiles/vendor_properties.json`, not
`herdsman/property_ids.json`: the profile table reads it, so it sits next
to `clusters.yaml`. Each entry carries the property's most common unit and
whether the library files it as a setting or diagnostic on most devices;
such a property is named but `functional: false`, so it is readable without
filling the default export. The Matter mapping (8.4) is a table in
`properties.py` rather than a YAML file: each entry needs a conversion
function. `presence` - what a radar reports - maps to occupancy as well.

**Readings are taken from the raw tap (8.2),** not from zigpy's attribute
cache: the tap sees the frame once, before zigpy parses it, and parses the
report itself with zigpy's foundation types. The tap hooks
`Device._should_filter_packet` per device, which zigpy calls first for every
packet, so a duplicate zigpy filters is not taken twice.
`tests/zigbee/test_herdsman_library.py` checks the hook exists.

**Which library speaks for a device (7)** is decided once per device and
connection: a device zha-quirks transformed gets only settings on ZCL
attributes its quirk does not already offer (its data points are its
quirk's); otherwise an `engine` definition goes to the engine and a
`catalogue` definition to stage 1. An engine device whose engine fails to
start, or fails three times in a row, falls back to what the catalogue
holds of its definition rather than to nothing.

**The engine never calls Python (9.3, 9.4).** quickjs-ng refuses a call
into Python while a time limit is set, and the time limit is what stops a
converter that loops. So the bridge queues every request to the host -
send a frame, start a timer, keep a record, publish, answer a call - in an
outbox, and `engine.py` collects the outbox after each call and acts on it
on the event loop. The transaction sequence number is handed in before each
call from zigpy's own counter.

**The bridge reuses zigbee-herdsman's own `Device` and `Endpoint` (9.3)**
instead of implementing them in JavaScript: an injected adapter turns each
frame they encode into a send request and resolves the answer when the tap
hands it back; an injected database hands each record to Python.
`Endpoint.bind` goes to zigpy's ZDO.

**Engine state is a file, not a table (9.4):**
`<zigpy database>.herdsman.json`, written atomically and at most every ten
seconds. A table would have meant a schema version, and PR #40 takes schema
19; the file keeps this branch independent of that numbering. It also
holds each device's last property values, so a restart shows them at once.

**Configuring (9.5)** runs loxmatter's own configure-on-join first and the
definition's `configure` (or Tuya's magic packet) after it, rather than
instead of it. loxmatter's own readings of the same device keep working,
and the cost - a few binds the definition would not have made - is small.

**Order of work (12):** both stages in one branch, as asked. Measuring the
engine on the Pi stays open (section 13).
