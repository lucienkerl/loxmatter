# Matter → Loxone Bridge — Design

**Date:** 2026-09-01
**Status:** Design agreed, ready for implementation planning

---

## 1. Goal

A self-hosted container service that connects Matter devices (over Thread and WiFi) to a
Loxone Miniserver. Devices are commissioned via a WebUI; every value a
device supplies — including events such as button presses — is passed on to
Loxone. The Loxone-side objects are created via a generated template file,
not by hand.

Target audience: open source, third-party installations. No assumption may be
made about Miniserver generation, Loxone Config version, or device inventory.

## 2. Non-goals in v1

- Patching the Loxone Config project file (see 3.2 — an optional module later)
- Matter OTA updates for devices
- Multiple Miniservers on one bridge
- Direction Loxone → Matter for scenes/groups (single-device commands only)
- Exposing Loxone as a Matter device
- Scenes, schedules, or automation in the WebUI (see 8.2)

---

## 3. Decisions

### 3.1 Loxone transport: virtual UDP inputs + virtual HTTP outputs

Chosen over two alternatives:

**MQTT (native Loxone client) — dropped.** Miniserver Gen 1 is not supported,
and the limits are too tight: max. 16 subscriptions and 16 publish inputs/outputs,
evaluation of value changes at most every 2 seconds. Unusable for a few dozen
devices with several attributes each.
Source: <https://www.loxone.com/enen/kb/mqtt/>

**Modbus TCP — dropped.** Loxone Config does have a native Modbus TCP driver,
but Loxone is the master and polls (latency), and Matter semantics (color, events, strings)
cannot be sensibly mapped onto a 16-bit register model. Right for meters and
inverters, a step backward for lights and buttons.

**Chosen: UDP input (push, low latency) + HTTP output (commands).**
Works on all Miniserver generations, is documented and stable.

### 3.2 Loxone import: template files, no direct writing

The originally desired path — the tool connects to the Miniserver and creates
the IOs itself — is **not possible**. The Miniserver API (`/dev/sps/io/<name>/<value>`,
`LoxAPP3.json`) can set values on *existing* IOs and read the structure, but
cannot create IO objects. The program is compiled by Loxone Config and uploaded
as a binary; creating IOs is a compiler function of Config.

Rebuilding the upload path would mean reverse-engineering the compiler and the
proprietary upload protocol — disqualifying for a tool that runs in other people's homes.

**Project file patching** (the Config project file is XML-based, "LoxPLAN") remains open
as a later, optional module. It is not XML-valid (duplicate attributes), needs
a tolerant parser, and is version-dependent. The gain over the template path
is also small: both require zero effort *per device*, and wiring the IOs
to function blocks stays manual work in both cases.
Source: <https://loxwiki.atlassian.net/wiki/spaces/LOX/pages/1852243969>

### 3.3 Stack: Python throughout

`python-matter-server` (based on the official CHIP SDK) as the Matter engine,
FastAPI backend, lean SPA. Rationale: the most mature controller implementation
outside Home Assistant, delivers exactly the needed model over WebSocket —
full attribute tree plus events via subscriptions. One language across the project.

`matter.js` (TypeScript) was considered: one process instead of two, shared types
between backend and frontend. Dropped because the controller side is less
proven and commissioning and Thread require considerably more low-level work.

### 3.4 Thread: dedicated OTBR in the stack

The container stack ships its own OpenThread Border Router (USB radio module,
e.g. ZBT-1 / nRF52840). Fully self-contained, no third-party border router needed,
no manual acquisition of Thread credentials. Price: USB passthrough, host
networking, and IPv6/radvd in the deployment.

### 3.5 Mapping: generic, not curated

No profile per device type. Instead, the device's endpoint/cluster/attribute
tree is read out and **every readable attribute and every event** becomes a
Loxone signal. A YAML table enriches known clusters (short name, scaling,
unit); unknown clusters are still exported raw.

Consequence: new devices work on day zero, just with uglier names. The
table is an enrichment layer, not a gatekeeper.

**Validation (phase 1, 2026-09-01).** Checked against 2 real IKEA devices on a
running matter-server (`ws://10.0.1.56:5580/ws`): node 3 "IKEA of Sweden
GRILLPLATS Plug" (metering plug, clusters 144 ElectricalPowerMeasurement,
145 ElectricalEnergyMeasurement) and node 4 "IKEA of Sweden BILRESA dual
button" (two-channel button, switch cluster 59 on endpoint 1 and 2).
Captured snapshots are under `tests/fixtures/nodes/`.

What holds up: for both devices, every attribute path was parseable
(`find_unparsable_paths` empty), and no attribute listed by the device in its
`AttributeList` was missing from the delivered snapshot (`find_unreported_attributes`
empty). Unknown clusters were extracted unchanged along with the rest. For attributes,
the generic decomposition thus holds without restriction.

What does not hold up, and why that is a real finding rather than a footnote:
**neither device populates the `EventList` (0xFFFA)**. For the button it is
simply missing from the switch cluster's `AttributeList` (`1/59/65531` =
`[0, 1, 2, 65528, 65529, 65531, 65532, 65533]` — 65530 is not in it); the
global attribute is optional in the Matter standard, and IKEA does not
implement it. A device that demonstrably sends button presses delivered
**zero** events via the pure EventList derivation. Attribute decomposition
remains generic — it needs no cluster knowledge and misses nothing.
**Event decomposition can no longer be that unrestricted**: which events
a cluster produces has to be derived from the FeatureMap, and that
derivation is necessarily cluster-specific knowledge (correction in 6.3,
implemented in `discovery.FEATURE_MAP_EVENTS`). That is a limit on the
claim "generic, not curated" above, not a side detail.

Second finding from the same capture: **45 of the 159 extracted attribute signals
of the plug are not scalar** — lists, structs, or strings, e.g.
`0/29/1 = [29, 31, 40, ...]`, `0/31/0 = [{...}]`, `0/40/1 = 'IKEA of Sweden'`.
The generic decomposition misses none of this — it delivers all 159 as a
signal —, but a good quarter of what was found (28.3%) cannot be mapped 1:1
onto a virtual UDP input, which only knows numbers and digital values
(for strings there is at least a virtual text input; for lists
and structs, nothing). Consequence for the exporter (6.6) and the WebUI (8).

**Addendum (phase 6, 2026-09-03).** Of the technically mappable signals
(6.6), a user wants to see only a small part exported by default
— for the plug, five of them (on/off, voltage, current, power,
consumption). [The signal selection design](2026-09-03-signal-selection-design.md)
introduces the term `Relevance` for this, separate from the `Exportability`
described here. The generic decomposition itself remains **unchanged**
in the process — it continues to deliver every readable attribute and every event as a
signal, exactly as validated above; `Relevance` only changes which **default value**
the `exported` column (section 5) gets on commissioning. An allowlist
("only what I recognize gets through") was explicitly rejected for this — it
would contradict the core bet made here, see section 2 there.

---

## 4. System architecture

### 4.1 Container stack

```
docker compose
├── otbr            OpenThread Border Router
│                   network_mode: host, USB dongle, IPv6 + radvd
├── matter-server   python-matter-server (CHIP SDK)
│                   network_mode: host (mDNS + IPv6 mandatory)
│                   Volume: fabric credentials
└── loxmatter       FastAPI + WebUI + SQLite
                    network_mode: host, port 8080/tcp

"dev" profile adds (see 10.2)
├── virtual-devices  CHIP example devices, actually commissionable
└── fake-miniserver  UDP capture + command sender instead of Loxone
```

`otbr` and `matter-server` require host networking without exception (mDNS, IPv6).
`loxmatter` speaks WebSocket inward as well as UDP and HTTP toward Loxone and would
technically get by with bridge networking too, but in the reference installation
(`deploy/testhost/docker-compose.yml`) it also runs with `network_mode: host` — for
two reasons argued at length there: `--url` (default
`ws://localhost:5580/ws`) reaches the also host-networked `matter-server` only
via `127.0.0.1` (in the compose bridge network it is not a resolvable service name), and the
HTTP port for the Loxone outgoing commands (`--listen`) is thereby reachable from the
Miniserver without a dedicated port mapping. The diagram and the running text said the
opposite of this until 2026-09-03 ("bridge networking" above, `network_mode: host`
below, with a "see above" pointing at the diagram); the compose file is and was
authoritative.

**Critical:** the volume holding `matter-server`'s fabric credentials is the only
irreplaceable state. Losing it means having to recommission every device. Must be
stated prominently in the deployment guide and in the WebUI; the WebUI offers a backup export.

**Security status of this backup export (protected since task 8, 2026-09-02 — see
9.1 for the full decision):** `GET /api/diagnostics/fabric-backup` (10.5)
has required `Authorization: Bearer <token>` since task 8, as soon as `--api-token`/
`LOXMATTER_API_TOKEN` is set (`loxone.server.build_api_guard`). `loxmatter` runs
with `network_mode: host` (see above) so the Miniserver can reach it — the same
reachability applies to the whole LAN, so a set token here is not an
option but the precondition for secure operation: **without a configured
token this route serves nothing at all** (HTTP 403 with a reason in `detail`) — it
is the sole exception to the rule that `/api` stays open without a token (9.1;
2026-09-03 revision, this paragraph said the opposite until then). The
read-only mount of the matter-server data directory (`./data:/matter-data:ro`)
and the corresponding
`--matter-data-dir` option in `deploy/testhost/docker-compose.yml` have been
active again since task 8, together with a `LOXMATTER_API_TOKEN` set in `.env` — without
this mount the route would only have returned a 503 anyway, instead of serving
real keys.

**Follow-up fix from 2026-09-03 (WebUI login, see
[2026-09-03-webui-login-design.md](2026-09-03-webui-login-design.md)):** the
entire preceding paragraph describes an intermediate state, not the current one. There
is no longer a rule "`/api` stays open without a token" of which this
route is the one exception — every `/api` route, this one included, requires
a valid session (password login) or a valid bearer token, otherwise 401.
The 403 branch built specifically for this route has thus gone away without
replacement, because the state it was directed against — a reachable bridge with
no proof of identity whatsoever — can no longer occur (details:
`api/diagnostics.py`'s module docstring). The mount itself remains justified,
for the same reason as any other `/api` route: not because a token sits next to
it, but because nobody can get there anymore without proof of identity.

### 4.2 Modules in `loxmatter`

| Module | Task | Dependencies |
|---|---|---|
| `matter/` | WS client to matter-server: commissioning, subscriptions, commands. Normalizes to `node → endpoint → cluster → attribute/event` | matter-server |
| `model/` | SQLite: devices, signals, mappings, export state | — |
| `profiles/` | YAML tables: cluster/attribute → short name, scaling, unit, Loxone type | — |
| `loxone/out` | UDP sender: debouncing, pulses, full resend, rate limiting | model, profiles |
| `commands/` | Translates "desired state → Matter command": level scaling, color space, cover position, setpoints. **Shared by `loxone/in` and `web/`** | matter, model, profiles |
| `loxone/in` | HTTP endpoints for virtual outputs; delegates to `commands/` | commands |
| `export/` | Generates `VIU_*.xml` and `VO_*.xml` | model, profiles |
| `web/` | SPA. REST against the backend for everything it does; plus **one** WebSocket connection `/api/live` that only receives live values and never sends (8.3 requires it) | — |

`commands/` exists as its own module for exactly this reason: the Loxone HTTP output and WebUI
operation are two callers of the same logic. If it lived in `loxone/in`, the color space and
level conversion would exist twice — with guaranteed divergent behavior.

Every module is testable without the others. `matter/` and `loxone/*` are the only
modules with I/O to the outside.

### 4.3 Data flow

```
Sensor:   Matter device ──subscription──► matter-server ──WS──► loxmatter
                                          mapping + scaling   │
                                                                ▼
                                UDP "d12_1_temp:21.5" ──► Miniserver (virt. UDP input)

Actuator: Loxone block ──► virt. output ──HTTP GET──► loxmatter
                                /cmd/d12_1_level/85              │
                                                        Matter command
                                                                 ▼
                                              matter-server ──► Matter device
```

---

## 5. Data model

```
Device
  id            int, sequential, stable
  node_id       Matter node ID
  unique_id     Matter unique ID (survives node ID reassignment)
  vendor, product, label
  online        bool

Signal
  id            int
  device_id     → Device
  endpoint      int
  cluster_id    int
  kind          'attribute' | 'event'
  element_id    int (attribute ID or event ID)
  key           str, UNIQUE, immutable  ← the Loxone wiring
  title         str, freely changeable
  unit          str
  exportability 'analog' | 'digital' | 'text' | 'none' (section 6.6)
  exported      bool, default value since phase 6 = `exportability` mappable
                AND `functional` (below) - not `functional` alone
  functional    bool, since phase 6 (schema v4) — whether `profiles.relevance.
                is_functional` wants this signal by default for the
                recognized device type (signal selection design, section 3/4).
                Not user-toggleable, unlike `exported`.
```

`analog`/`scale` from an earlier version of this section do not exist as
columns — scaling is the profile table's business
(`profiles/clusters.yaml`) at runtime, not a number stored per signal;
`exportability` is the actually stored, finer replacement for
the `analog: bool` named here previously.

The device additionally carries the Loxone export metadata:

```
Device (continued)
  udp_port      int, default from global setting (7000)
  exported_at   nullable — when a template was last generated
```

`key` is the only value that is never changed. `title` may be changed at any
time and only affects the next export.

---

## 6. Loxone integration

### 6.1 Verified template schema

Measured against 26 templates from a real Loxone Config installation — cleaned excerpts
are under `tests/fixtures/loxone/VIU_reference.xml` and `VO_reference.xml`,
`loxmatter.export.documents` rebuilds this schema:

```xml
<?xml version="1.0" encoding="utf-8"?>
<VirtualInUdp Title="Matter — Wohnzimmerlampe" Comment="erzeugt von loxmatter" Address="192.168.1.50" Port="7000">
	<Info templateType="1" minVersion="14040925"/>
	<VirtualInUdpCmd Title="Wohnzimmer Temperatur" Comment="Wohnzimmerlampe · 1/1026/0" Address="" Check="d12_1_temp:\v" Signed="true" Analog="true" SourceValLow="0" DestValLow="0" SourceValHigh="100" DestValHigh="100" DefVal="0" MinVal="-2147483647" MaxVal="2147483647" Unit="&lt;v.1&gt; °C" HintText=""/>
</VirtualInUdp>
```

```xml
<?xml version="1.0" encoding="utf-8"?>
<VirtualOut Title="Matter — Wohnzimmerlampe" Comment="erzeugt von loxmatter" Address="http://192.168.1.50:8080" CmdInit="" HintText="" CloseAfterSend="true" CmdSep="">
	<Info templateType="3" minVersion="14040925"/>
	<VirtualOutCmd Title="Wohnzimmer Licht Helligkeit" Comment="d12_1_level" CmdOnMethod="GET" CmdOffMethod="GET" CmdOn="/cmd/d12_1_level/&lt;v&gt;" CmdOnHTTP="" CmdOnPost="" CmdOff="" CmdOffHTTP="" CmdOffPost="" CmdAnswer="" HintText="" Analog="true" Repeat="0" RepeatRate="0"/>
</VirtualOut>
```

**Escaping warning:** the Loxone value placeholder `<v>` in `CmdOn` sits inside an
XML attribute and must be written as `&lt;v&gt;`. An unescaped `<v>` makes the
file unreadable for Loxone Config. The placeholder `\v` in `Check` is not affected by this.

**Meaning of `Address`:** in `VirtualInUdp`, `Address` is the *sender filter* —
the bridge IP from which datagrams are accepted. `Port` is the port the
Miniserver listens on. In `VirtualOut`, by contrast, `Address` is the target base URL of
the bridge.

**Origin and correction history.** The first draft of this section adopted the
schema from the reference implementation of the LoxBerry template builder
(<https://github.com/mschlenstedt/Loxberry/blob/master/libs/phplib/loxberry_loxonetemplatebuilder.php>).
On 2026-09-02, checked against the 26 templates from a real installation
(91 `VirtualInUdpCmd`, 19 `VirtualOutCmd`), it turned out that this schema differed in
four points from what Loxone Config actually writes. The blocks above already
show the corrected, measured form; the four corrections for
traceability:

1. **Every template carries an `<Info>` element as its first child** — in all 26 files,
   without exception:
   `<Info templateType="1" minVersion="14040925"/>`
   `templateType` is **1** for `VirtualInUdp`, **2** for `VirtualInHttp`, **3** for
   `VirtualOut`. `minVersion` is a Loxone Config version in the format `YYMMDDHH`.
2. **`VirtualInUdpCmd` has 15 attributes**, not 13 — `Unit` and `HintText` were missing:
   `Title, Comment, Address, Check, Signed, Analog, SourceValLow, DestValLow,
   SourceValHigh, DestValHigh, DefVal, MinVal, MaxVal, Unit, HintText`
3. **`VirtualOut` has `HintText`** between `CmdInit` and `CloseAfterSend`.
4. **`VirtualOutCmd` has 15 attributes and no `ID`**, and the two method fields
   sit together instead of apart:
   `Title, Comment, CmdOnMethod, CmdOffMethod, CmdOn, CmdOnHTTP, CmdOnPost, CmdOff,
   CmdOffHTTP, CmdOffPost, CmdAnswer, HintText, Analog, Repeat, RepeatRate`

Confirmed from the start, on the other hand: UTF-8 with BOM (26 of 26), plain CRLF
(26 of 26), and the XML declaration verbatim (26 of 26).

File format: **UTF-8 with BOM, CRLF line endings.**
File names: `VIU_d<device_id>_<label>.xml` and `VO_d<device_id>_<label>.xml`, with the
device label normalized to ASCII. The `device_id` is not decoration: the normalization is
lossy and maps different labels onto the same or an empty string —
`"Lampe 1"`, `"Lampe-1"`, and `"Lampe_1"`, for instance, all onto `Lampe_1`, a label with
no ASCII characters onto `""`. If the file name carried only the label, two devices with a
colliding label would overwrite each other on export, and a user would import
one template believing there were two. `Store` assigns `device_id` immutably and never
twice (6.2), which is what actually makes the name unique.
Location: `Documents\Loxone\Loxone Config\Templates\VirtualIn\` and
`...\VirtualOut\` respectively.
Import into Config: Peripherals → Virtual Inputs → Virtual UDP Input → Import
Template.

A `VirtualInUdp` carries any number of `VirtualInUdpCmd`s — one import thus brings
all the signals *of one device* into the project at once.

### 6.2 Key assignment and export granularity

**One template file per device.** Every Matter device becomes exactly one
`VirtualInUdp` object (all sensor values and events) and one `VirtualOut` object
(all commands). In Loxone Config this makes a named node with
its commands underneath appear per device — navigable and clearly assignable to a
device. A collective export of 200 inputs into one object would no longer be
manageable in Config.

This resolves the re-import problem by itself: a newly commissioned device means
exactly one additional import; existing objects are never touched, the
wiring stays intact.

**All devices share one UDP port.** The Miniserver limit is
**max. 50 distinct input UDP ports** — it counts ports, not objects. Since all
keys are globally unique, each `VirtualInUdp` object picks up only its
own `Check` patterns; a shared port produces no crosstalk. Default 7000,
consumption: exactly one port, regardless of device count.
Source: <https://www.loxone.com/enen/kb/communication-with-udp/>

That multiple `VirtualInUdp` objects can share the same port is **confirmed on a
real Miniserver** (2026-09-01). The 50-port limit also applies equally on all
Miniserver generations. The port nevertheless remains
configurable per device — for separate network segments or multiple bridges on one
Miniserver.

**Keys are opaque and immutable.** Format `d<device_id>_<endpoint>_<slug>`, e.g.
`d12_1_temp`. Assigned during commissioning, frozen afterward. Readable names live
exclusively in `Title` and `Comment`. Renaming in the WebUI only changes the
label in the next export, never the wiring. The key must stay unique
even when a device is removed and recommissioned — `device_id` is therefore never
reused.

**System signals** (`bridge_alive`, `/resync`, see 6.4 and 6.5) live in their own
pair `VIU_Matter_System.xml` / `VO_Matter_System.xml`, imported once — a
fixed name, because there is no device and thus no `device_id` that would need to
make it unique.

File names of the device templates: see 6.1.

### 6.3 Events

**Event detection (corrected, phase 1, 2026-09-01).** This section originally
assumed that events, like attributes, could be read generically from the `EventList`
(0xFFFA) of each cluster. The validation in 3.5 disproved that: none of the
checked devices populates this globally optional attribute.
Event detection is therefore **FeatureMap-based** and cluster-specific:
for the switch cluster (59), `discovery.FEATURE_MAP_EVENTS` records which
event requires which FeatureMap bits — `SwitchLatched` ← LS, `InitialPress`
← MS, `LongPress`/`LongRelease` ← MSL, `ShortRelease` ← MSR,
`MultiPressOngoing` ← MSM ∧ ¬AS, `MultiPressComplete` ← MSM. Checked against
`data_model/1.4/clusters/Switch.xml` from `project-chip/connectedhomeip`, the
machine-readable transcription of the Matter Application Cluster Specification
(`mandatoryConform` condition per event). The `EventList` remains an
**additional** source — it costs nothing, and some devices
do implement it —, its hits are unioned with those from the
FeatureMap and deduplicated. Further clusters with events are added as
further table entries, without touching the algorithm in `extract_signals`.

The Matter `Switch` cluster delivers `InitialPress`, `ShortRelease`, `LongPress`,
`MultiPressComplete`. A virtual UDP input knows only values, no event concept.

**Two** signals are exported per event type:

- `<key>` — digital pulse: `1`, then `0` after 200 ms. Produces a clean edge.
- `<key>_n` — monotonic counter. More robust, because a lost UDP packet only makes the
  counter skip instead of swallowing the press.

For `MultiPressComplete`, additionally `_press2`, `_press3` as their own pulses, plus
`_presscount`.

### 6.4 Zustands-Wiederherstellung

UDP is stateless. After a Miniserver restart, all inputs sit at `DefVal`,
until the next update arrives — for a temperature sensor, potentially hours.

- **Periodic full resend** of all current values, default every 5 min, staggered to
  about 50 datagrams/s. **Since the periodic resend design
  (2026-09-04) this applies only to `/resync` and bridge startup** — the
  periodic timer itself now only resends individually marked signals, with
  its own interval configurable via the WebUI. Details:
  [periodic resend design](2026-09-04-periodic-resend-design.md).
- **`/resync` endpoint**, shipped as a ready-made `VirtualOutCmd` in the export. Hooked
  in the Config project to the system-start block, all values are present immediately after every restart.

**Finding (phase 4, live run 2026-09-02).** A resend can only send values that
the bridge already holds itself — it iterates the last-sent value per signal, not
the device state. This cache is populated exclusively via subscriptions, which report
*changing* values, and is empty at startup. A live run with a
Matter plug under no load confirmed this: over 40 s exactly three datagrams arrived
(heartbeat, one HTTP-triggered switch command), but none of the 109
exportable attribute signals at the time (110 today, see 6.6 — the counter reading from the
energy struct was added in phase 6) — the full resend at startup ran empty, because nothing was in the cache yet,
and without a changing load this would not have changed for an indefinite time.
Exactly at this moment — right after a bridge restart — the mechanism is
thus empty, even though it would be most needed here. The bridge must therefore
seed itself from the current device state at startup (`Runtime.seed_from_snapshot`, fed from
`BridgeMatterClient.snapshots()` — the same picture that `loxmatter export`
also reads from), before the first full resend runs.

### 6.5 Additional signals

- `d<id>_online` — digital, per device: reachable yes/no.
- `bridge_alive` — global, toggles every 30 s. As a watchdog in Loxone; covers "container
  dead" and "network gone" alike.

### 6.6 Non-exportable values

**Finding (phase 1, 2026-09-01).** A good quarter of the generically extracted
attribute signals is not exportable, because a virtual UDP input only
accepts numbers and digital values (see 3.5): for the checked plug, 45
of 159 (28.3%). Strings can still be output via a virtual text input;
for lists and structs (`0/29/1 = [29, 31, 40, ...]`,
`0/31/0 = [{...}]`) there is **no** equivalent in Loxone.

The exporter (phase 3) needs an explicit rule for this instead of
implicit behavior: signals with list or struct values are omitted on
export, strings go to a virtual text input instead of the
numeric `VirtualInUdpCmd`. The generic decomposition itself does not
change as a result — it continues to deliver everything the device offers; the
choice of "exportable or not" only arises at export time, not at
extraction time. See 8 for the consequence in the WebUI.

A fourth, quiet category is added: for the plug, 5 of the 159
attribute signals carry the value `null` (e.g. `0/49/7`) — neither unreported (the
path is there), nor unparseable, nor non-scalar in the sense above, but
just as little a number or digital value; the exporter must also make an
explicit decision for `null`.

**The number the exporter works with is therefore not 45, but 50.**
The 45 are the non-scalar signals; also not mappable onto a `VirtualInUdpCmd`
are the null values on top of that. Breakdown of the 159
attribute signals of the plug, measured on 2026-09-01:

| Category | Count | exportable |
|---|---|---|
| Numbers (analog) | 102 | yes |
| Booleans (digital) | 7 | yes |
| Text | 13 | only via a virtual text input |
| Lists and structs | 32 | no |
| `null` | 5 | no |
| **mappable onto `VirtualInUdpCmd`** | **109** | |

Anyone reading the 45 as "not exportable" is off by the five
null values.

**Addendum (phase 6, 2026-09-03).** The table above is a snapshot
from 2026-09-01 and stays as such. Two things have changed since then,
both via the [signal selection design](2026-09-03-signal-selection-design.md):

First, **the 109 itself is no longer current — it is now 110.**
Cluster 145 (cumulative consumption) delivers its value as a structure
(energy value plus two timestamps) and therefore fell entirely into the row
"Lists and structs" above on 2026-09-01. Since then, the signal selection design (section
5) pulls the named number field out of exactly this structure
(`profiles.table.struct_member`, `field: 0` in `clusters.yaml`) — the
"mappable"; the timestamps continue to be dropped. Only
`energy_imported` is affected (the reference template never reports a value for
`energy_exported`, the attribute is entirely absent from the snapshot). Backed by
`tests/loxone/test_values_real_device.py::test_exactly_110_signals_yield_a_value`,
`tests/profiles/test_real_device_fixtures.py`, and
`tests/api/test_devices.py` — all three now expect **110**, not 109.
The [signal selection design](2026-09-03-signal-selection-design.md) itself also
still named the old number 109 in several places (corrected there, with
the same reasoning); one test docstring
(`tests/model/test_store.py::test_a_freshly_registered_plug_exports_only_its_meaningful_values`)
still names it in its prose — plain comment text with no assertion
on it, left as is here because this task explicitly does not touch code.

Second, the **default value of `exported`** changes: of these 110, for
the plug only **5** are what a user commissioned the device for
(on/off, voltage, current, power, consumption — the latter being exactly the
counter reading newly added above). The remaining 105 stay technically
exportable, but from now on are **not** checked by default — the
user can re-enable them individually in the "expert" block of the WebUI
(section 7 of the signal selection design), exactly as before. The
signal selection design justifies the selection rule
(`profiles.relevance.is_functional`).

### 6.7 Output commands: allowlist

**The source is `AcceptedCommandList` (0xFFF9), not the attribute list.** Matter attributes
are overwhelmingly read-only; an output command per readable attribute would be
ineffective more than ninety percent of the time. Measured against the devices from phase 1 (2026-09-01):
the plug is controllable via `1/6` OnOff, the button via nothing at all — it is an
input device.

**For commands an allowlist applies, not the generous pass-through from 3.5.**
Among the accepted commands are management clusters: `0/62` OperationalCredentials
contains `RemoveFabric`, `0/48` GeneralCommissioning and `0/49` NetworkCommissioning
commissioning, `0/51` GeneralDiagnostics the `TestEventTrigger`. An exporter that
emits everything puts commands on a Loxone user's function block that can
throw the device out of the fabric or render it unusable.

Only clusters with a `commands` entry in the profile table produce output commands.
An explicitly opt-in **raw mode** extends this to unknown clusters —
for devices whose clusters the table does not yet know.

**Management clusters stay blocked even in raw mode.** This is not a precaution
that can be switched off:

```
31, 41, 42, 48, 49, 50, 51, 52, 53, 54, 55, 56, 60, 62, 63, 70
```

The asymmetry is deliberate. Exporting one unknown attribute too many costs
one unused input. Exporting one unknown command too many can throw a
device off the network.

---

## 7. Matter integration

### 7.1 Commissioning

The WebUI accepts a pairing code (11/21 digits) or QR content and passes it to
`matter-server`. For Thread devices, the bridge's own OTBR supplies the
operational dataset.

Devices already in another ecosystem (Apple/Google/Amazon) have to be removed there
via **multi-admin** generate an additional pairing code there. The WebUI explains this
inline — it is the most common stumbling block.

### 7.2 Bridges

An IKEA DIRIGERA or comparable Matter bridge appears as *one* node with many
endpoints. The WebUI must therefore present endpoints as standalone, nameable units,
not as sub-items of a device. The data model already supports this
(`Signal.endpoint`).

**Missing UniqueID (phase 1, 2026-09-01).** The IKEA BILRESA button (node 4)
does not supply a `UniqueID` (BasicInformation, `0/40/18`) — the attribute is
missing entirely, not just its value empty. `NodeSnapshot.from_raw` already reads it
tolerantly (empty string instead of an error), `loxmatter inspect` shows
`Unique ID: —` accordingly. The data model relies on `unique_id` in section 5,
because it survives a node ID reassignment — for devices without a UniqueID that
does not apply, there `device_id` remains the only stable identifier. Not an
edge case: manufacturers genuinely leave out this optional attribute.

### 7.3 Values and scaling

Examples from the `profiles/` table:

| Cluster | Attribute | Raw | Loxone |
|---|---|---|---|
| TemperatureMeasurement | MeasuredValue | 0.01 °C | ÷100, `°C` |
| RelativeHumidityMeasurement | MeasuredValue | 0.01 % | ÷100, `%` |
| ElectricalPowerMeasurement | ActivePower | mW | ÷1,000,000, `kW` |
| ElectricalPowerMeasurement | RMSVoltage | mV | ÷1000, `V` |
| ElectricalPowerMeasurement | RMSCurrent | mA | ÷1000, `A` |
| ElectricalEnergyMeasurement | CumulativeEnergyImported | mWh | ÷1,000,000, `kWh` |
| LevelControl | CurrentLevel | 0–254 | ×100/254, `%` |
| OnOff | OnOff | bool | digital |

**Target units follow Loxone, not SI.** Loxone consistently computes power
in **kW** — the energy manager, the meter and consumption blocks expect
kW at the input. We therefore deliver kW, not W. The same rule applies to every future
entry in the profile table: what governs is the unit the Loxone block
expects, not the obvious SI unit.

**`Unit` is a format string, not unit text.** Loxone writes patterns there like
`<v.3> kW`, `<v.1> °C`, or `<v>%`: the digit after the dot is the number of
decimal places shown. Measured against 26 real templates, `<v.3> kW` is by
far the most common form for power.

**This undermines the rule below at the display level.** A value of 0.0003 kW arrives
in the UI as `0.000` with `<v.3> kW` — the value in the Miniserver is correct, but
nobody sees it. The exporter must therefore write **`<v.6> kW`** for power, not
the usual `<v.3>`. The same applies to any quantity whose range of interest spans
multiple orders of magnitude.

**Consequence for number formatting.** From mW to kW is six orders of magnitude. A
standby load of 300 mW becomes `0.0003` kW. The UDP sender must therefore
**not round values to two decimal places** — otherwise everything under 10 W disappears
into zero, and those small continuous loads are exactly what you want to see in Loxone. Decision:
output with up to **6 decimal places**, trailing zeros trimmed. That is
a dedicated test case in the scaling test suite.

Color: Loxone delivers in Lumitech or RGB notation, Matter expects hue/saturation
or CIE xy. The conversion lives in `commands/` and must be tested both ways.

**Research result (task 5, 2026-09-02).** The RGB encoding is officially documented: the
Loxone "RGB Lighting Controller" block outputs color on a single analog output
as one decimal number that concatenates three percentage values (each 0-100) decimally -
`AQa = red% + green% * 1000 + blue% * 1_000_000` (e.g. 20040060 = 60% red, 40% green,
20% blue). Source: Loxone Knowledge Base, "RGB Lighting Controller", section
"Outputs" (https://www.loxone.com/enen/kb/rgb-scene-controller/, retrieved 2026-09-02).

For the Lumitech encoding (brightness plus color temperature in one number),
**no reliable source** could be found - neither on the official
lighting block page nor in the structure file PDF. The only hit is a
forum post with self-logged DMX values (presumed format "AABBBCCCC"), which
the author himself flags as a guess
(https://www.loxforum.com/forum/hardware-zubehoer-sensorik/143867-lumitech-ausgang-dmx-dimmer,
post #2). Task 5 therefore implements only the (uncontested) Matter-side
conversion Kelvin→mired and RGB→hue/saturation in `commands/color.py`;
`to_matter_call` accepts an already-unpacked Kelvin value for color temperature
and does not decode a raw Loxone number. Unpacking the raw Loxone number
(RGB as well as Lumitech) remains the job of task 6 (HTTP endpoint) or the WebUI, once
a reliable source exists for Lumitech - see open points.

**Not tested against hardware.** No Matter light was available for
this task. `kelvin_to_mireds` and `rgb_to_hue_saturation` are tested exclusively
against reference values (the Zigbee/Matter mired convention and the HSV definition
respectively), not against a real device.

---

## 8. WebUI

Four views, deliberately kept lean.

**1. Devices** — list with online status, commissioning by code/QR, renaming, removal.
Per device, the most important live values and **direct controls** for the
obvious actions:

| Device type | Control in the WebUI |
|---|---|
| Light | toggle, brightness slider, color temperature/color |
| Plug | toggle, current power alongside |
| Cover | up / down / stop, position slider |
| Thermostat | setpoint, operating mode |
| Sensor, button | display only — nothing to control |

**2. Signals** — for each device, the complete attribute and event tree with live value.
Checkbox "export to Loxone", editable title, key visible but not
editable. Writable attributes can be **set raw** here — for anything view 1 has no
control for, and for unknown clusters. Non-exportable values
(lists and structs, see 6.6) are shown anyway, with a note instead of
the export checkbox — they are useful precisely for diagnostics, even though they never
become a UDP datagram.

**3. Export** — enter the IP **of this bridge** (as seen from the Miniserver) and the UDP port.
Not the Miniserver's address: the value becomes the `Address` of the virtual
UDP input — the sender address from which the Miniserver accepts datagrams at all
— and the base of the command URLs `http://<ip>:<listen>` in the virtual output (6.1).
This line said "Miniserver IP" until 2026-09-03, and the UI labeled
the field accordingly; a template generated with that looks correct and stays silent.
Download templates per device, individually or as a ZIP; a filter "only devices not yet
exported", which applies to the preview **and** to the ZIP (it also decides
which devices are marked as exported afterward). For each device it is visible when it was last
exported and whether signals have changed since. Contains the quick-start guide
and the one-time system templates.

**4. System** — system check, live feed (log lines, UDP capture, and command log,
continuous instead of one-shot — since 2026-09-03, see 10.5 and the
[live feed design](2026-09-03-diagnostics-live-feed-design.md)), backup of the
fabric credentials. The system check verifies four things: matter-server, the
signal key database, the local IPv6 path, and the routing path to the Miniserver
(10.5). **It does not check OTBR and the Thread network** — recorded as open
point 9 in section 12, not as a silent omission.

### 8.1 Why the controls are more than convenience

View 1 is the project's **diagnostic tool**. If a lamp doesn't switch via Loxone,
one click in the WebUI cleanly separates the two possible causes: if the
device responds here, the fault is in the Loxone wiring or the template export;
if it doesn't respond, the fault is in Matter, Thread, or the device. Without this, any troubleshooting is
guesswork — and for a tool used in other people's installations, that's the difference between
an answerable and an unanswerable bug report.

That's why the controls belong in v1 and not in a later expansion stage.

### 8.2 Scope boundary

The WebUI is a **commissioning and diagnostic tool, not a smart-home interface.**
Not included and not planned: scenes, schedules, automations,
favorites pages, rooms, user management, app. All of that is Loxone's job — the
bridge does not duplicate it.

### 8.3 Live updates

A WebSocket from the backend to the SPA pushes through attribute and event changes as well as
online status. The same subscription that feeds the UDP sender — no second
path, no polling.

**Since 2026-09-03 there are two of these, not one** (live feed for logs,
UDP capture, and command log, see 10.5 and the
[design](2026-09-03-diagnostics-live-feed-design.md)): `/api/live` remains the
value channel above, `/api/diagnostics/live` is a second, separate WebSocket
for the "System" view. Separate, not attached, because the two have
different lifetimes (the value channel runs as long as any view is
open; the diagnostics channel only as long as the "System" view is open) and
different volumes — a forgotten browser tab on "Devices" would otherwise
permanently receive every log line as well. Both share the same
WebSocket mechanism (`api/streaming.py`: bounded queue,
disconnect detection, subprotocol negotiation for the token) and the same
access protection (9.1).

### 8.4 Raw attribute writing: allowlist (task 4, 2026-09-02; finding corrected,
review fix Important #2, 2026-09-02)

**Finding: an attribute's writability sits in a table that this
installation cannot load and that python-matter-server does not use anywhere — not,
as first claimed here, in no table at all.** Checked against the installed
packages (python-matter-server==8.1.2), not assumed:

- `chip.clusters.ClusterObjects.ClusterAttributeDescriptor` — the base class of every
  generated attribute class (e.g. `BasicInformation.Attributes.NodeLabel`) — carries
  `cluster_id`, `attribute_id`, `attribute_type`, `must_use_timed_write`. None
  of these properties distinguishes read from write access;
  `must_use_timed_write` only governs whether an *allowed* write access needs
  a timed-write envelope.
- `matter_server.client.client.MatterClient.write_attribute(node_id, attribute_path,
  value)` checks nothing beforehand — the call goes to the controller unchecked; a
  rejection would come back, if at all, as an error from the device itself.
- **A full-text search for "writable" did in fact turn up hits — this section previously
  wrongly said the opposite.** `chip/clusters/CHIPClusters.py`, part of the installed
  `chip` package, carries its own table, independent of `ClusterObjects`, with exactly
  this information: `grep -c '"writable": True'
  .venv/lib/python3.12/site-packages/chip/clusters/CHIPClusters.py` returns **250**
  hits, and for `BasicInformation` (cluster 0x28 = 40) exactly the three
  attribute IDs 5 (`NodeLabel`), 6 (`Location`), and 16 (`LocalConfigDisabled`) are
  marked `"writable": True` in it — exactly the three the allowlist below
  independently already arrived at against a real device.
- **This module is nevertheless not importable, and python-matter-server does not use
  it anywhere.** `from chip.clusters.CHIPClusters import ChipClusters` fails in
  this distribution with `ImportError: cannot import name 'exceptions' from 'chip'`
  — the `home_assistant_chip_clusters` package, which provides `chip.clusters.CHIPClusters`
  here, ships the file without the accompanying `chip/exceptions.py` that it
  requires on load. A search for `CHIPClusters` in the installed
  `matter_server` package also yields not a single hit.

The practical consequence is the same as before — the allowlist stays
correct for today —, only its justification is now different: not "the information
doesn't exist", but "the information exists in a table that this
installation cannot load and that python-matter-server itself does not read." That
is a difference with a consequence: the second situation has an obvious
path forward that the first would not have — see open points, point 7.

**Consequence: the same asymmetry as with commands (6.7), this time for attributes.**
`POST /api/signals/{key}/write` (`api/control.py`) rejects every write attempt on an
attribute that is not on an explicit allowlist — a generous
pass-through as with *export* (3.5) would be wrong here: a wrongly exported
attribute costs one unused input, a wrongly permitted write access
can misconfigure a device. The list is deliberately small and contains only
`BasicInformation.NodeLabel` (0/40/5), `.Location` (0/40/6), and
`.LocalConfigDisabled` (0/40/16) — all three confirmed against the checked-in
IKEA GRILLPLATS template (`tests/fixtures/nodes/ikea_grillplats_plug.json`), not merely
assumed from the specification.

**Open point: even an allowed attribute cannot actually be written yet today.**
`BridgeMatterClient` (`matter/client.py`) has no `write_attribute`, and
`build_control_router(store, invoke)` also does not accept a second caller for
this — `invoke` is typed exclusively for commands
(`Callable[[MatterCall], Awaitable[None]]`), an attribute write access is not one.
`POST /api/signals/{key}/write` therefore responds to an allowed attribute with 501
instead of a success that does nothing — see open points, point 6.

---

## 9. Error handling

| Case | Behavior |
|---|---|
| matter-server unreachable | reconnect with exponential backoff; `bridge_alive` stops → Loxone watchdog triggers |
| Device offline | `d<id>_online` = 0, last values stay as they are (no reset) |
| HTTP command to an offline device | HTTP 503, visible in the log. Loxone doesn't evaluate a virtual output's response anyway |
| UDP send failure | log, no retry (fire and forget) |
| Unknown cluster | raw export without scaling, warning in the WebUI |
| Device removed and recommissioned | new `device_id`, new keys. The old Loxone objects become orphaned — the WebUI points this out and names the objects to delete |

### 9.1 Securing the `/api` routes (task 8, 2026-09-02)

**Note up front:** this section describes the protection as it existed between
task 8 and 2026-09-03 — an optional token, without which `/api` stayed
open (with one exception for the fabric backup). Since the WebUI login,
that no longer applies; what changed is gathered at the end of this
section, not retrofitted into every single sentence below.

Up to phase 4, this service offered two endpoints for the Miniserver: `/cmd` and
`/resync`. Whoever reached the port could at most switch one device with that. Since
phase 5 (task 1: commissioning, task 2: removal, task 6: fabric backup as a download)
the weight is different: whoever reaches the port can throw devices out of the fabric
or download the complete, irreplaceable fabric credentials (see 4.1). This
is a change in the kind of risk, not just its degree.

**The decision:** an optional bearer token (`--api-token`/
`LOXMATTER_API_TOKEN`, see `loxone.server.build_api_guard`) protects, from here on,
without exception every route under `/api` — read and write, all five routers
(devices, control, export, live values including the WebSocket route `/api/live`,
diagnostics including the fabric backup). If no token is configured, these
routes stay open unchanged — with a clear warning in the log at startup
(`cli._warn_if_missing_api_token`). A service that refuses to start at all without a token
would be unusable for a test environment or an initial setup without a prepared secret;
the warning is the deliberately chosen trade-off for that.

**Why `/cmd` and `/resync` are deliberately excluded:** the Miniserver calls
virtual outputs as a plain HTTP GET, with no way to
attach a header — that is a property of the Loxone template format (6.1), not a
choice of this project. A token on this path would simply disable the Loxone
integration, not secure it. This is a real, permanent limitation and is recorded here as
such, not as a shortcoming: **even with a token set, anyone
on the same network can still switch devices via `/cmd`.** What the token prevents is
exclusively changes to the inventory — commissioning, removal, and the download
of the fabric backup.

**How far "whoever reaches the port" really goes for `/cmd`** (added 2026-09-03).
`/cmd/{key}/{value}` is an unauthenticated **GET** with no check of
origin whatsoever. A GET needs no script and no foothold in the LAN: any website
someone opens in a browser from this network can switch a device with a single
`<img src="http://<bridge>:8080/cmd/d12_1_onoff/1">` — the browser
sends the request from inside the LAN, without the attacker ever having been on the network
themselves, and does not need to see the response. Neither an `Origin` check nor a
CSRF token nor a restriction to POST would help here without breaking exactly
what the route exists for: the Miniserver sends a plain GET with no header and
no state. The risk is thus not different in kind from what's described above, but
considerably bigger than "whoever reaches the port" suggests, and is recorded here in its
actual reach, instead of disappearing into a phrasing that
presupposes an attacker on the same network. What limits it is exclusively the
damage: `/cmd` allows switching, nothing more — no commissioning, no removal, and no
downloading.

`--host` (default `0.0.0.0`) continues to bind to all interfaces, because the
Miniserver has to be able to reach the service — the token does not change that, it only protects
what is reachable behind `/api`, not reachability itself.

**Two transport paths for the token, and why there have to be two** (follow-up fix
after review, 2026-09-03). `Authorization: Bearer <token>` is the main path and applies to
every REST route. For the WebSocket route `/api/live` it is structurally impossible: the
browser `WebSocket` API (`new WebSocket(url, protocols)`) has no
parameter at all for custom headers. The only channel the browser can influence in the
handshake is the subprotocol argument, which goes onto the wire as
`Sec-WebSocket-Protocol`. The UI therefore connects with `new WebSocket(url, ["bearer",
token])`, and `build_api_guard` additionally accepts the token from this one header,
exclusively in the form `bearer, <token>`. A query parameter would be the obvious
alternative and is deliberately NOT chosen: it ends up in server logs, proxy logs, and the
browser history, a header does not — the same reasoning by which `api/diagnostics.py`
keeps the command log free of query strings (10.5). `api/live.py` returns the
marker `bearer` in the accept (never the token), because otherwise the browser aborts
the handshake per RFC 6455.

**A requirement for the token itself follows from this:** it must be transportable
as an HTTP token — no spaces, no comma, no non-ASCII. `openssl rand -hex
32`, the path recommended in `.env.example` and the README, yields only `[0-9a-f]` and
satisfies that on its own; the requirement is stated there explicitly, instead of remaining a
silent assumption. A token consisting only of whitespace (a
truncated line break from a copied `.env`) counts as "not set" —
`normalize_api_token` decides this for the guard AND the startup warning together, otherwise
the service would be locked without the warning pointing that out. The comparison itself
runs via `secrets.compare_digest` on UTF-8 bytes, not via `!=` and not via
`str` (with `str`, `compare_digest` raises `TypeError` as soon as non-ASCII is involved —
a non-ASCII token in the header must not trigger a 500).

**The one exception to "without a token, `/api` stays open": the fabric backup.** `GET
/api/diagnostics/fabric-backup` is not served at all without a configured token
(HTTP 403, with an explanation in `detail`). All other routes remain
open unchanged. The reason is the difference in damage, not in principle: an
unprotected device list is embarrassing, an unprotected fabric backup is the
irreversible takeover of the fabric (4.1). The reference installation
(`deploy/testhost/`) mounts the matter-server data directory and runs with
`network_mode: host` — a default that depended on discipline reading the README
would not be defensible for this one route specifically. 403 and not 401, because without
a configured token there is nothing at all with which someone COULD authenticate: a
retry with credentials cannot help, and that is exactly what distinguishes 403 from
401 (RFC 9110). 503 remains reserved for the already-existing case of "no data directory
mounted" — three causes, three distinguishable codes.

**This entire section describes the state as of task 8 (2026-09-02) and
its follow-up fixes up to 2026-09-03 — no longer today's state.** The
addendum [2026-09-03-webui-login-design.md](2026-09-03-webui-login-design.md)
replaces the token as the sole proof of identity for the browser, and tasks 9/10 have
implemented that: initial setup assigns a password on first access,
after which the browser logs in with a session cookie (`loxone.server.
build_api_guard`, `auth.sessions`). Two sentences above are thereby superseded,
not merely supplemented:

- "If no token is configured, these routes stay open unchanged" no longer
  applies. Without a valid session AND without a valid token, every
  `/api` request ends with 401 — even when no password and no token
  are set up at all. The startup warning (`cli._warn_if_missing_api_token`)
  no longer exists accordingly; its successor `cli._warn_if_no_password`
  warns about the missing password, not the missing token.
- The **exception for the fabric backup** just described **goes away without
  replacement**, because there is no longer a rule "`/api` stays open" for it
  to be an exception to — the 403 branch built specifically for it has been removed
  from `api/diagnostics.py` (see its module docstring).

What remains unchanged from this section: `/cmd` and `/resync` remain without
any protection, for the same reason; the bearer token remains a path for
scripts and `curl`, with the same two transport paths and
the same character-set requirement; and `openssl rand -hex 32` remains the
recommended way to a token. Details of initial setup, the
password requirements, and session management are in the addendum, not
duplicated here.

---

## 10. Testing

### 10.1 Automated tests

- **Exporter — golden-file tests.** Round trip: generate XML → import into Loxone Config
  → save it there again as a template → diff. The only method that verifies the
  real format. The reference files are checked into the repo.
- **Matter adapter** — against recorded WebSocket fixtures from `matter-server`.
- **Integration without hardware** — `chip-all-clusters-app` as a virtual Matter device in the
  CI container. Covers commissioning, subscription, and commands.
- **UDP** — fake Miniserver (socket listener) that records datagrams; checks
  debouncing, pulse length, rate limiting, and full resend.
- **Scaling** — table tests per cluster entry, including color space conversion
  in both directions. A dedicated case for small power values: 300 mW must arrive
  as `0.0003`, not as `0`.
- **`commands/`** — the same test suite covers both callers. Plus a test
  that checks that the WebUI route and the Loxone HTTP route produce the same
  Matter command for the same input. That is the regression this module exists to
  guard against in the first place.

The entire suite runs **without hardware and without network access**. That is a
requirement, not an observation: as soon as a test needs a real device, it gets
skipped and rots.

### 10.2 Manual testing without hardware

`docker compose --profile dev up` starts two helper containers in addition to the
normal stack. This makes it possible to run through the complete path without a Miniserver, without a Thread dongle,
and without a single real Matter device:

**`virtual-devices`** — several instances of the CHIP example applications
(`chip-all-clusters-app`, `chip-lighting-app`) as real Matter devices over WiFi. They
are commissioned completely normally via the WebUI with the standard pairing codes — it is
the same code path as for real hardware, not a mock on the side. `all-clusters-app`
is especially valuable here, because it deliberately brings exotic clusters along and thereby
puts the generic export under load.

**`fake-miniserver`** — replaces the Loxone Miniserver in both directions:
- listens on UDP 7000 and shows every datagram with a timestamp in a small
  web interface. This immediately shows what the Miniserver *would* receive.
- can fire HTTP GETs at the bridge like a virtual output, including
  `<v>` substitution. This makes the command direction testable without Loxone.
- can read in a generated `VIU_*.xml` and derive the expected keys from it, in order
  to report which exported signals have **never** seen a datagram. This finds
  mapping errors that would otherwise only show up in Loxone.

Thread is the only part that needs real hardware. The OTBR therefore lives in
its own compose profile — without a dongle the stack still starts, just
without Thread.

### 10.3 The end-to-end path from zero

The path that runs in a few minutes after every change and is documented:

1. `docker compose --profile dev up`
2. open the WebUI, commission the virtual device with the displayed pairing code
3. see signals, switch the device in the WebUI — confirms the Matter direction
4. generate templates, `fake-miniserver` shows the datagrams — confirms the Loxone direction
5. fire a command in `fake-miniserver` — confirms the command direction

Only once this runs through is testing on real hardware worthwhile.

### 10.4 Development environment

A Miniserver, a Thread dongle, and real Matter devices (IKEA) are available. Two
consequences for planning:

- The **golden-file references for the exporter can come from real Loxone
  Config from the start** instead of from guesswork. This takes the risk out of the riskiest module.
- The **generic export gets validated early against real cluster trees**. Real devices
  deviate from the CHIP example apps in practice — exactly where the
  gaps arise that a purely virtual development process would miss.

The dev profile from 10.2 nevertheless stays mandatory: it is the foundation for CI and for
outside contributions, where this hardware is not available.

### 10.5 Built-in diagnostics

These four things were built for development, but are just as useful in operation — they
are the reason a bug report from a third-party installation becomes answerable:

- **UDP capture** in the WebUI: the last N sent datagrams with timestamps,
  filterable per device. Answers "is the bridge sending anything at all?" without Wireshark.
- **Command log**: incoming HTTP calls from the Miniserver with their result. Answers
  the opposite direction.
- **Log lines** (since 2026-09-03): a `logging.Handler`
  (`LogBufferHandler`, `diagnostics/logbuffer.py`) holds the last 500
  lines of the `loxmatter` logger — not the root logger, lines
  from third-party libraries do not belong in a control interface — from
  level INFO up, in a ring. The same lines that `docker logs` also shows, just
  without shell access to the host. `install_log_buffer()` sets, at
  startup, both the handler's level and the logger's own level —
  without the latter, `loxmatter` would stay at Python's default
  effective level WARNING, and no `logger.info(...)` line in the whole
  project would reach the ring, whatever level the handler carries (see
  the docstring there for the reasoning).
- **Live channel** (`GET /api/diagnostics/live`, WebSocket, since 2026-09-03):
  pushes UDP capture, command log, and log lines continuously instead of
  once to the "System" view — with a snapshot on connect,
  then live. Replaces manual reloading; details, message format,
  and the distinction from `/api/live` (8.3):
  [live feed design](2026-09-03-diagnostics-live-feed-design.md).
- **Template preview**: before the download, the WebUI shows which objects and commands
  will be created and how many.
- **System check** (`GET /api/diagnostics/system`): four lines, each green or red with
  a concrete note — `matter-server` (is there a connection?), `store` (is the
  signal key database writable? without that, no commissioning and no
  export marking), `ipv6` (is there a locally routed IPv6 address? Matter and Thread
  need it), and `miniserver` (does a routing path to the configured target exist?
  more cannot be determined with fire-and-forget UDP without ICMP evaluation). Until 2026-09-03
  this list was called "IPv6 present, mDNS reachable, dongle present, matter-server
  connected, Miniserver reachable" — mDNS and dongle are not implemented, `store`
  was in no spec. See open point 9 in section 12.
- **Fabric backup** (`GET /api/diagnostics/fabric-backup`, see 4.1): download of the
  matter-server data directory as an archive. Protected like every other
  `/api` route: without a valid session (WebUI login) or a valid token,
  the route responds with 401. Until 2026-09-03 this one route had its
  own 403 branch for the case "no token configured" (see 4.1, 9.1) —
  that has gone away without replacement with the WebUI login, because the state it
  was directed against (the route reachable, but no proof of identity at all in the
  system) can no longer occur since then.

---

## 11. Risks

| Risk | Assessment | Countermeasure |
|---|---|---|
| Deployment complexity from OTBR (USB, IPv6, host networking) | high, will definitely occur | Detailed guide, compose profiles, diagnostics page in the WebUI that checks IPv6/mDNS/dongle |
| Loss of the fabric credentials | medium, catastrophic | backup export in the WebUI, warning in the guide |
| Template schema changes with the Config version | low | golden-file tests, versioned schema |
| Multi-admin commissioning confuses users | high | inline instructions per ecosystem in the WebUI |
| UDP load from full resend of many signals | medium | rate limit, configurable interval |

---

## 12. Open points

1. Concrete radio module for the reference compose file.
2. **Loxone Lumitech encoding unresolved** (task 5, 2026-09-02). How Loxone encodes
   brightness and color temperature as one number in Lumitech output mode is not
   findable in the official Loxone documentation — only a forum post flagged
   as a guess exists (see 7.3). `commands/translate.py` therefore already
   expects an unpacked Kelvin value for color temperature instead of the
   raw Loxone number. Clarify before task 6 (HTTP endpoint), otherwise it
   cannot reliably unpack the raw number. The RGB encoding, by contrast, is documented (7.3).
3. **`subscribe()` only subscribes attributes statically — and this compounds with
   `Runtime.invalidate_index()` into a silent dead end** (task 8, 2026-09-02).
   `BridgeMatterClient.subscribe()` (`matter/client.py`) registers, on call, exactly
   one upstream subscription per (node, attribute path) pair known at that
   point in time — justified in the module docstring there: `attr_path_filter` only controls WHETHER
   a registered callback fires, not WHAT gets passed to it, and for
   `EventType.ATTRIBUTE_UPDATED` the delivered `data` is solely the new value, without
   node ID or path — a single wildcard subscription therefore could not attribute
   such an update to any device. An attribute path that a device reports for the FIRST
   TIME AFTER this call — after a firmware update that unlocks a cluster, or
   because a device is commissioned afterward — never gets a
   subscription for it and consequently never delivers an update.

   This interlocks with a second limitation that looks independent on its own:
   `Runtime.invalidate_index()` (`loxone/runtime.py`) exists exactly for the case where
   someone calls `Store.register_signals()` again for an already-running device, to
   make a newly added signal known. But it only discards
   `Runtime`'s own cache of already SUBSCRIBED paths (`_signal_for`'s
   `_signals`/`_indexed`) — it does not register a new upstream subscription and
   cannot, either, it does not know `BridgeMatterClient` at all. If `subscribe()` never
   learned about the path, matter-server produces no event for it at all; there is consequently
   nothing for `invalidate_index()` to miss. Even `_signal_for`'s own
   debug log (which fires on an unknown signal) never sees this case, because
   `on_attribute`/`on_event` are simply never called for a path that was never
   subscribed — not even at debug level does anything about it appear in the log.

   So anyone who wants to build "attach a signal to a running device at runtime"
   using only `Store.register_signals()` followed by
   `Runtime.invalidate_index(device_id)` lands in exactly this silent dead end: the
   call runs through without error, the cache is rebuilt cleanly — but no value ever
   arrives, because the actual gap sits one layer deeper, at `subscribe()`, not at
   `invalidate_index()`. A correct fix therefore needs both: after
   `Store.register_signals()`, ALONGSIDE `Runtime.invalidate_index(device_id)`,
   `BridgeMatterClient.subscribe()` must also run again for the affected node (or be
   extended specifically for the new path) — relying on the cache invalidation alone
   is not enough. For this phase's intended use (`connect()` reads
   the full node cache, then `subscribe()` once, no runtime
   recommissioning) the gap is acceptable; but it is an open point, not a
   completed task.

   **What a user sees of this** (added 2026-09-03, after phase 5 brought commissioning
   into the WebUI). Until then, this point only described the cause; the
   impact is more unpleasant than it sounds. A device freshly commissioned via view 1
   immediately gets `d<id>_online = 1` from the NODE_ADDED event and appears in the
   list **online and green** — but `subscribe()` ran at bridge startup and does not know
   this node, so `Runtime.last_values_for()` stays empty for it and every
   signal permanently shows "-" in view 1 and 2. Green with not a single value
   is indistinguishable from the outside from a broken device, and it is exactly the
   order in which a first use goes: commission, then look at values.
   The workaround until a real fix is a bridge restart — after that,
   `subscribe()` knows the node. The success message after commissioning says this in one sentence
   (`web/app.js`, `commissionDevice`), so nobody mistakes this known edge case for
   an error in their own installation. Template export is not
   affected by this: it reads the `Store`, not the live values.
4. **Event counters (`<key>_n`) are process-local and do not survive a bridge restart**
   (`Runtime`, `loxone/runtime.py`; review fix I7, 2026-09-02). Spec 6.3 sells
   this counter as a monotonic value whose benefit is that a lost UDP datagram
   only makes it *skip*, instead of swallowing the button press — Loxone logic should be able to
   watch it without ever missing a press. `Runtime.__init__` sets
   `self._counters: dict[str, int] = {}` but with no seeding at all, and the counter exists
   nowhere outside this process memory — no store field, no `seed_from_snapshot`,
   no `/resync` path. A bridge restart (deployment, crash, container restart)
   therefore resets it to 0, and the next button press sends `1` again. This is
   not the same class of bug that 6.3 addresses: a LOST packet makes the counter
   *rise* (jumps from, say, 4 to 6, still recognizable as "there was a press"), a
   RESTART makes it *fall* (from 47 back to 1) — Loxone logic that waits for "counter has
   increased" misses this one press completely after every bridge restart,
   the exact opposite of what the counter was introduced for. A fix would need one
   of two things: either the counter gets persisted (e.g. in the `Store`,
   analogous to the signal keys themselves, with the same care for concurrent access)
   and resumed from the database at startup instead of at 0, or the Loxone-side
   logic monitors the counter for *change* instead of *increase* — the latter is the
   simpler change, but requires every Config project that uses this counter to
   actually wire it that way. Neither one nor the other is implemented in this phase;
   left untouched because the behavior should not be changed without being asked, but
   recorded here, because 6.3 otherwise promises more than the implementation
   delivers.
5. **`MultiPressComplete` delivers only the two base signals, not the `_press2`/
   `_press3`/`_presscount` promised in 6.3** (`export/signals.py`, `discovery.py`; review fix
   I6/M13, 2026-09-02). Spec 6.3 promises, verbatim: "For `MultiPressComplete`, additionally
   `_press2`, `_press3` as their own pulses, plus `_presscount`." In fact, `export/signals.py`'s
   `to_inputs` exports, for EVERY event — `MultiPressComplete` included —
   exclusively the two generic signals that every other event also gets:
   `<key>` (digital pulse) and `<key>_n` (monotonic counter, see point 4 above for its
   own gap). There is neither special handling for switch cluster event no. 6
   (`MultiPressComplete`, see `discovery.FEATURE_MAP_EVENTS`) nor a way to read a
   press count out of the raw `MultiPressComplete` event (which, per the Matter
   specification, carries the number of detected presses as its payload) and
   translate it into separate pulses/a `_presscount` value — `matter/paths.py`'s event
   detection delivers only the path (`endpoint/cluster/event`) anyway, no payload, and
   `Runtime.on_event` accordingly has no parameter for it. A device with
   multi-press detection (e.g. an IKEA button with double/triple click) thus delivers
   `MultiPressComplete` as the same single pulse as `InitialPress` — a double click
   looks exactly like a single press in Loxone, only the `_n` counter keeps counting.
   A fix would need: (a) passing the raw `MultiPressComplete` event with its payload instead
   of just its path through to `Runtime.on_event`, (b) an interpretation of this
   payload as a press count, and (c) an extension of `export/signals.py` that produces three
   additional `LoxoneInput`s for this one event instead of the generic two. Not
   implemented in this phase — recorded here so 6.3 no longer promises more than
   `export/signals.py` actually delivers.
6. **Raw attribute writing is secured up to the allowlist, but not connected to
   matter-server** (task 4, 2026-09-02; see 8.4). `POST
   /api/signals/{key}/write` (`api/control.py`) rejects every attribute that is not on
   the (deliberately small, confirmed against a real device) allowlist
   `_WRITABLE_ATTRIBUTES` — that is tested
   (`test_raw_write_of_a_non_writable_attribute_is_refused`). But for an *allowed*
   attribute there is still no path to the device: `BridgeMatterClient` has no
   `write_attribute` (unlike `send_command`, which runs via `matter_server.client.client.
   MatterClient.send_device_command`), and `build_control_router(store, invoke)`
   also does not accept a second caller for this — `invoke` is restricted by type
   (`Callable[[MatterCall], Awaitable[None]]`) to commands, an
   attribute write access is not one. For an allowed attribute the route therefore
   honestly responds with 501, instead of faking a success that does nothing. A
   fix would need: (a) `BridgeMatterClient.write_attribute(node_id, attribute_path,
   value)` as a thin wrapper around `MatterClient.write_attribute` — following the same pattern
   as `remove_node`/`set_thread_dataset` (task 1) —, and (b) a second,
   attribute-shaped caller interface for `build_control_router`, analogous to
   `invoke` for commands. Not implemented in this phase — recorded here so
   view 2 (8, "Writable attributes can be set raw here") no longer
   promises more than the WebUI can actually do today.
7. **The hand-maintained allowlist (8.4) does not scale beyond a handful of
   devices — and there is now a documented path to replace it with a table**
   (review fix Important #2, 2026-09-02). `_WRITABLE_ATTRIBUTES`
   (`api/control.py`) today needs, for every attribute anyone ever wants to
   write, its own entry confirmed against a real device or the
   Matter specification — for an installation with more than a
   handful of different device types this quickly turns into a maintenance burden that
   nobody keeps up, and an unmaintained allowlist is either too tight
   (missing control options) or, worse, gets replaced out of convenience by a
   blocklist (see 8.4 for why that would be the wrong asymmetry). As 8.4 now
   records, the writability information does actually exist, in
   `chip/clusters/CHIPClusters.py`, only the module is not
   importable in this distribution (`ImportError: cannot import name 'exceptions' from 'chip'`)
   and python-matter-server does not read it. Two paths could change this: (a) a
   later version of `home_assistant_chip_clusters`/`chip` ships the missing
   `chip/exceptions.py`, which would make `ChipClusters.py` importable and its
   `"writable"` flags queryable at runtime, or (b) the file is not
   imported but parsed as plain text data (it is a large but
   syntactically regular Python literal) — a path with its own risk, because it
   reproduces an internal format of a third-party library that is not meant as an
   interface and can break with a future version without an import
   failure indicating it. Neither of the two paths is implemented in this phase;
   recorded because today's allowlist is a deliberate but not the
   only possible answer to 8.4's finding.
8. **Since the WebUI login, the UI logs in with a password — no longer
   with a token entered in the browser** (task 8, 2026-09-02; superseded by
   [2026-09-03-webui-login-design.md](2026-09-03-webui-login-design.md), implemented in
   task 9/10). What stood here until 2026-09-03 — a password field for the token
   at the top right, the token held in the browser's `localStorage`, sent along on every
   call as an `Authorization` header — no longer exists: `app.js`
   sets no `Authorization` header for the browser and puts no secret into
   `localStorage`. Instead, initial setup (`/auth/setup`) assigns a password on the
   first call, `POST /auth/login` then sets the
   session cookie `loxmatter_session`, and the browser authenticates with it on every
   `/api` route and on the WebSocket handshake of `/api/live`, entirely without the
   subprotocol detour (`build_api_guard` checks the cookie first). The download
   of the fabric backup travels over the same cookie instead of a token in the header
   — this changes nothing about `requestDownload()` versus a plain `<a href>`:
   the missing header was only one of two reasons for it, and the second still applies
   unchanged — a 401 or 503 should appear as a readable message in the
   UI instead of as raw error text in the browser window (`app.js`,
   `requestDownload`). The bearer token remains fully in place as a server-side path for
   scripts and `curl`, but since then is no longer a path the
   UI itself uses.

   What actually remains open, now for the password instead of the token: **it only knows
   "all or nothing".** Whoever has it can view, switch, commission,
   remove, and download the fabric backup; whoever doesn't have it sees nothing of
   `/api`. There is no read-only role for someone who is only supposed to
   view state, and no second, restricted password or token. For the
   intended use (one household, one person operating the bridge) this is
   appropriate — for an installation where several people are meant to have different
   levels of access, it would be too coarse. Unlike the old token, the
   session itself is no longer unlimited: it expires after 30 days of inactivity
   (`auth.sessions.SESSION_LIFETIME_SECONDS`), can be ended
   individually via `POST /auth/logout`, and `loxmatter set-password` ends all
   sessions at once. The bearer token itself, by contrast, remains the old static secret
   with no expiry, no rotation, and no revocation of individual callers — unchanged and
   open for the script path.
9. **The system check does not check mDNS, the radio dongle, OTBR, or the Thread network**
   (review fix Fix 6, 2026-09-03). 10.5 and section 8, view 4, promised until
   then "mDNS reachable, dongle present" as well as "status of matter-server and OTBR,
   Thread network". Four checks are implemented: `matter-server`, `store`, `ipv6`,
   `miniserver` (`api/diagnostics.py`, `build_diagnostics_router`'s `/system`). Both
   spec spots have been brought in line with what actually runs; the gap is recorded here, instead of
   silently deleting it from the spec. What is missing and what it costs:

   - **mDNS reachable** — Matter devices on WiFi/Ethernet are found via mDNS.
     If that fails, commissioning fails without any diagnostics line pointing to it;
     today only `matter-server` reports "connected", which does not touch this question.
   - **Dongle present** — the border router's USB radio stick. If it's missing, no
     Thread device is reachable. It would only be checkable inside the OTBR container, not in this
     process: `loxmatter` cannot see the USB device.
   - **OTBR and Thread network** — status of the border router and whether a
     Thread network has formed at all. Both sit behind OTBR's REST interface, to
     which this service today has no connection.

   Deliberately NOT rebuilt in this follow-up fix: the three checks need
   access to things outside this process (mDNS resolution on the host network, USB, a
   second HTTP interface) and therefore each need their own decision about what a
   failure should mean — a red line that is red for an environmental reason in an
   otherwise correctly running installation makes the diagnostics page worthless.
   `store`, in turn, is a sensible addition foreseen in no spec revision
   and is therefore recorded in 10.5 from now on.
