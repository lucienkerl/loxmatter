# Zigbee as the Second Device Source, and Pairing in the Web UI

Design, 12 September 2026. Spec 2a-2 of the Zigbee programme: it fills the
boundary drawn on 11 September
(`2026-09-11-device-source-boundary-design.md`) with a second
implementation, and gives the user a way to pair a Zigbee device without a
console. Everything it rests on was measured or read from source on
11 September 2026 and is recorded in `.superpowers/sdd/zigbee/research-2.md`
(cited below as its section letters) and in the earlier source-level study
of zigpy 2.2.0 / bellows 1.0.1 / zha 2.2.2 / zha-quirks 2.2.2 (cited as
**R1 §n**). Where a claim here has no measurement behind it, it says so.

Rationale: the boundary spec bet that Matter's data model is close enough to
the Zigbee Cluster Library that a Zigbee source can deliver the same
`NodeSnapshot` and everything downstream — discovery, profiles, the store,
the runtime, the export, groups — stays unchanged. Section C of the research
settles that bet: for on/off, level, hue, saturation, colour temperature,
temperature and humidity the attribute IDs, the scaling and the units are
literally identical, and nothing has to be converted at all. What the bet
does not cover is everything Zigbee has and Matter does not — IAS Zone,
Metering with per-device multipliers, ZLL device types — and everything
zigpy does not do for us: it does not configure devices to report, it does
not enroll IAS sensors, it does not reconnect, and it has no concept of a
device being available. That work is this spec.

The maintainer delegated every decision in this document on the night of
11/12 September with one instruction: **user experience first, even at more
development cost.** The decisions below were taken under that instruction
and are settled; this document records them and the reason for each, not
the alternatives to them.

## 1. Where This Sits

| Spec | Content | Status |
|---|---|---|
| 2 (first half) | The `DeviceSource` boundary, migration 9, transport badge | merged 11 September 2026 |
| 2a-1 | Radios in the web UI: detection, settings card, sidecar job, Compose mounts | designed; **partly built** (see below) |
| **2a-2 — this document** | The Zigbee source, configure-on-join, the pairing tab, the Zigbee row and badge, profile-table additions, the shared colour fix, a command timeout | designed |
| 2b | Operations: coordinator replacement, network backup, blocklist, Metering scaling, remotes and buttons, "Reconfigure device", installer detection, diagnostics | later |

**2a-1 is a real dependency and it is not finished.** Checked in the
worktree on 12 September 2026: `src/loxmatter/radios/inventory.py`,
`src/loxmatter/radios/sidecar.py` and `deploy/updater/radios-once.sh` exist,
but there is no `src/loxmatter/api/radios.py`, no radios router wired into
`loxone/server.py` or `cli.py`, and no service in
`deploy/testhost/docker-compose.yml` carries `/dev:/host/dev:ro` or any
`device_cgroup_rules` (`grep -rn "host/dev\|device_cgroup_rules" deploy/`
finds only the sidecar script's own default). So the card this spec adds a
row to does not exist yet, and the bridge cannot see `/dev/serial/by-id`
from inside its container. The implementation plan must either land 2a-1's
API and Compose changes first or carry them, and it cannot treat the radios
card as given.

**What 2b gets, and why it is not here.** Coordinator replacement including
the write-once-EUI64 confirmation (R1 §10, G6), network backup as a
downloadable secret (G5), a rejoin blocklist of our own (B.1 item 7, G11),
Metering and ElectricalMeasurement scaling with per-device
multiplier/divisor (C.2, R1 §12), remotes and button events (R1 §6c), a
per-device "Reconfigure device" button (D.3), and the zigpy-DB rollback note
for the updater (G4). Each is either a second screen's worth of UI or a
problem that only appears on hardware this project does not have yet;
pairing one sensor and having its value reach Loxone is the thing that has
to work first.

## 2. Principles

1. **Translate at the edge, keep the middle Matter-shaped.** The Zigbee
   source produces `NodeSnapshot` with Matter attribute paths, Matter device
   type IDs and a synthesised `AcceptedCommandList`. Nothing past
   `loxmatter/sources/` learns that Zigbee exists (boundary design §2).
2. **A table entry beats an edge branch when it also helps Matter.** Five of
   the additions in section 5 go into `profiles/clusters.yaml` rather than
   into Zigbee code, because occupancy, BooleanState, illuminance and colour
   XY are Matter clusters too and are simply missing from the table today
   (research C.5).
3. **Never write a path whose value is `None` into a snapshot.** This is the
   single most likely "loxmatter paired my sensor and Loxone gets nothing"
   report (research D.1 step 5, G1); section 5.5 states the rule and section
   6 the reading that makes it satisfiable.
4. **Zigbee is optional; Matter is not.** A missing or broken Zigbee stick
   degrades the bridge, it never stops it (research E.2). The Loxone
   watchdog heartbeat keeps meaning what it means today.
5. **Say what is happening, including when it is slow or stuck.** ZHA's
   pairing hangs forever on "Starting interview" with no failure state at
   all (research B; HA core issues 124114, 99497, 123136, 162426). Every
   state this spec defines either progresses, fails, or names the real cause
   of its own delay.
6. **Every device call is bounded.** Nothing on the command path has a
   timeout today (research E.6); a Loxone output aimed at a sleeping button
   would hold an HTTP request for over a minute.

## 3. What the User Does

### 3.1 The Pairing Tab

The commissioning card in the Devices view gains two tabs, **Matter** and
**Zigbee** (boundary design §1). The Matter tab is today's card unchanged,
including the code field and the sticker illustration. The Zigbee tab is new
and follows research B.1 exactly; the numbered items below are its items.

The tab is only offered when a Zigbee source is configured. Without one it
is absent rather than disabled: a tab that explains why it does nothing is
worse than no tab.

**Before searching.** The tab opens on reset guidance and a single button,
not on an open network. ZHA starts the permit window when the page opens and
burns it while the user is still reading the instructions (research B). The
guidance names the two cases that cover almost every device: a lamp is reset
by switching it on and off five to ten times, a battery device by holding
its button until the LED blinks.

**Searching.** `POST /api/zigbee/permit {duration: 254}` answers
`{permit_until: <iso>}`; Alpine counts down from that timestamp, so a
reloaded page still shows the truth. 254 s is the protocol maximum — zigpy
asserts `0 <= t <= 254` (R1 §3) — and no unlimited mode is offered;
Zigbee2MQTT removed its permanent option in 2.0 as a security concern
(research B). Two buttons while the window is open: **Stop searching**
(`permit(0)`) and **Keep open longer** (re-send 254), with the countdown
under them as `m:ss` ("Open for new devices: 4:12 left"). Leaving the tab
for good — switching to the Matter tab or to another view — closes the
window; ZHA's failure to do that is a standing complaint. Interviews already
running are unaffected — the permit gates only new joins.

**Only the page that opened a window closes it on leaving.** A window a
phone opened, which a laptop merely sees through its poll, is not the
laptop's to close when its user clicks Export. The page remembers that it
opened the window (Start or Keep open succeeded there) and forgets it when a
Stop succeeds or a list shows no window; a Start still under way when the
tab is left counts as opened there, and is closed the moment its answer
lands off screen. The claim is kept in the browser tab's `sessionStorage`,
so it survives a reload and is shared with no other tab. A Stop refused on
leaving (`api.zigbee.close_failed`) is shown in a banner above the main
navigation, where the user now is, with its own Stop button — not inside the
pane they left.

**A reload shows an open window.** A reload is not leaving for good, and it
sends no Stop: that would also close a window another tab or a phone is
watching. Entering the Devices view reads the pairing list once whichever
tab is selected, and an open window selects the Zigbee tab so its countdown
is on screen.

**One row per device, keyed by IEEE.** The states, and the event that
produces each (R1 §3):

| State | Trigger | What the row says |
|---|---|---|
| joined | `device_joined` | Found a device — reading its details |
| interviewing | interview in progress | Reading its details … (the row's title already names `<manufacturer> <model>`; while neither is known, it says what joined says) |
| configuring | `device_initialized`, our configure-on-join running | Setting it up |
| ready | configure-on-join finished | Ready to use, green — or, on a row not added to the device list yet, "Ready - add it to your devices" (a green "Ready to use" there read as finished, and the device never reached Loxone) |
| interview failed | `device_init_failure` | Could not read this device, with **Retry** and **Remove** |
| stuck | no progress for 60 s (mains) / 90 s (battery) | Names the real cause: battery devices fall asleep, press the device's button every few seconds |
| waiting to wake | configuration deferred (section 6.4) | Waiting for the device to wake up — press its button |

The last two are the states ZHA does not have, and they are the reason this
tab is worth building rather than copying. A stuck row is not an error row:
it keeps waiting, and it keeps offering Retry and Remove.

**On the ready row**, inline: a **name**, prefilled with
`<Manufacturer> <Model>` rather than the IEEE (Z2M prefills the IEEE, which
nobody keeps), and a **room**, reusing the commissioning card's existing room
`<select>` with its "No room" and "New room ..." options — the same component
and the same room-key encoding (`""` for no room), not a second one. The same
fields are on a row displayed as configuring or waiting to wake: both are
stored as ready, and a sleeping sensor can wait days for its wake-up.

**An explicit Add button, then save on blur.** A row not yet adopted carries
an **Add** button, and the first `PATCH` is sent by it, not by a blur. That
`PATCH` adopts the device — it puts it into the store, the export and Loxone
— which is a step worth a deliberate press; and the prefilled name is often
exactly right, so a user who keeps it has no field to leave and no blur
would ever fire. Once the device is added, name and room save on their own:
the name on blur (and Enter), the room when the select changes. The name
field grows to show a long prefilled name in full rather than cutting it
off, and stays a single line.

**Rows already added fold away.** The list holds every device the radio has
seen since it came up. Rows not added yet stay in the open; rows already in
the device list fold into a closed disclosure titled with their count ("2
devices already added"). A row added in the page stays open until the next
load, so the line saying where it went is read.

**A "quirk applied" / "no quirk" hint** sits on the ready row. zigpy tells
us which it was: the resolved device carries `_quirk_registry_entry`
(R1 §5). This is loxmatter's equivalent of Z2M's "Unsupported" badge, and it
explains missing values before the user asks — users routinely confuse a
failed interview with an unsupported device (zigbee2mqtt issues 5127,
19012), so the two are shown as different things in different places.

**Removal copy is honest.** `remove()` deletes the device from zigpy's
database whether or not the leave request is ever delivered, and nothing
stops the device rejoining (R1 §4, G11). The confirmation says so: the
bridge asks the device to leave and forgets it either way, and a device that
was asleep must be factory-reset before it can be paired elsewhere.

**Not in this version:** "Join via a specific router" (2b), install codes.

### 3.2 The Zigbee Row in the Radios Card

The settings card from 2a-1 gains a third row, below Thread and Bluetooth. It
lists the detected USB sticks with their fingerprint result (section 7), the
current one marked "in use", plus "No Zigbee stick".

**The Thread stick can never be chosen**, and the row says why rather than
silently omitting it. Enforcement is by resolved device identity, not by
path string: the Pi's `.env` still holds `/dev/ttyUSB0` while the UI offers
by-id paths, and after the `/dev` bind of section 8 the bridge sees the node
under a different prefix again (research A.3, F.8). The row compares the
resolved `major:minor` of the candidate against the Thread device reported
by the sidecar (`radios/sidecar.py`, `RadioConfig.thread_device`), and the
bridge refuses such a path on the API too — the UI filter is a courtesy, the
server check is the guarantee.

An unknown stick — no fingerprint match — is still selectable, behind an
**Advanced** disclosure that exposes radio type and baud rate. No probing in
this version (section 7).

### 3.3 The Badge

`i-transport-zigbee` joins `i-transport-thread` and `i-transport-ip` in the
sprite (both already exist in `web/index.html`), drawn in the same style: 24
viewBox, stroke 1.8, `currentColor`, no official logo — "Zigbee" is a
trademark of the Connectivity Standards Alliance (boundary design §5.3).
`transport_for` already returns `"zigbee"` for the technology, so only the
glyph and its i18n key are missing.

### 3.4 New User-Facing Strings

Every key needs an `en` and a `de` value in `src/loxmatter/i18n/strings.yaml`
and is resolved with `i18n.t(...)` at call time; writing the English
sentence directly would move the problem, not solve it. The flat dotted
namespace follows the file's existing convention.

| Key | Purpose |
|---|---|
| `web.devices.transport_zigbee` | Badge title and `aria-label` |
| `web.zigbee.tab` / `web.devices.commission_tab_matter` | The two tab labels |
| `web.zigbee.reset_hint_lamp`, `web.zigbee.reset_hint_battery` | Reset guidance before searching |
| `web.zigbee.reset_intro` | The lead-in to the reset guidance |
| `web.zigbee.start`, `web.zigbee.stop`, `web.zigbee.extend` | The three buttons ("Stop searching", not a bare "Stop") |
| `web.zigbee.countdown` | "Open for new devices: {time} left", `{time}` as `m:ss` |
| `web.zigbee.searching_empty` | The open window before any device joined |
| `web.zigbee.state_joined`, `_interviewing`, `_configuring`, `_ready`, `_failed`, `_stuck`, `_waiting_wake` | The seven row states of 3.1 |
| `web.zigbee.state_ready_to_add` | A ready row not added to the device list yet |
| `web.zigbee.name_label`, `web.zigbee.adopt` | The name field and the Add button |
| `web.zigbee.adopted_hint`, `web.zigbee.saved` | Where an added device went; a save on blur |
| `web.zigbee.added_group_one`, `web.zigbee.added_group_many` | The title of the folded rows already added (two keys: `i18n.t` knows no plural forms) |
| `web.zigbee.retry`, `web.zigbee.remove` | Row actions |
| `web.zigbee.quirk_applied`, `web.zigbee.quirk_none` | The hint |
| `web.zigbee.remove_confirm`, `web.zigbee.remove_confirm_adopted` | The honest removal copy of 3.1, and what it adds for a device already in the device list |
| `web.radios.zigbee_label`, `web.radios.zigbee_none` | The radios row |
| `web.radios.zigbee_is_thread_stick` | Why the Thread stick is not offered |
| `web.radios.fingerprint_unknown`, `web.radios.advanced` | Unknown stick and its disclosure |
| `api.errors.zigbee_not_connected` | The radio is configured but the link is down |
| `api.zigbee.permit_failed`, `api.zigbee.close_failed`, `api.zigbee.unknown_device` | Pairing route errors; `close_failed` is a refused Stop, which says the network may still be open |
| `api.zigbee.radio_changing`, `api.zigbee.not_ready_yet` | The pairing routes' 503 during a radio change, and the 409 of a row not stored as ready. The 503 does not tell the user to reload, and nor does `unknown_device`: the tab keeps asking and its list updates by itself |

## 4. The Zigbee Source

A new package `loxmatter/zigbee/`, whose `ZigbeeSource` satisfies
`DeviceSource` (`src/loxmatter/sources/__init__.py`) without an adapter, the
way `BridgeMatterClient` does.

### 4.1 Lifecycle

```python
zigbee = ZigbeeSource(path=..., radio=..., db=Path("/data/zigbee.sqlite"))
try:
    await zigbee.connect()  # NOT fatal, unlike matter-server
except ZigbeeUnavailableError as exc:
    logger.warning("Zigbee radio not available at startup: %s", exc)
sources = Sources([client, zigbee])
```

`connect()` always builds a **fresh** `ControllerApplication`: `await
old.shutdown(db=True)`, then the `zhaquirks` resolver, then
`new(start_radio=False, device_resolver=zhaquirks.ZHA_DEVICE_REGISTRY.resolve,
uninitialized_packet_handler=...)`, then `startup(auto_form=True)` (R1 §1,
§9). An object whose `startup()` failed is never reused — that is what Home
Assistant does, and zigpy's own teardown cancels tasks and calls
`device.on_remove()` on every device (research E.3).

> **Three names in this document were wrong, and are corrected in place.
> Do not "restore" them.** Each was checked against the packages that are
> actually installed — zigpy 2.2.0, zha-quirks 2.2.2 — not against the
> research notes, and `tests/zigbee/test_zigpy_names.py` now fails if any of
> them changes back.
>
> 1. **`device_resolver`** is `zhaquirks.ZHA_DEVICE_REGISTRY.resolve`, not
>    `DEVICE_REGISTRY.resolve`. In the installed zha-quirks,
>    `zhaquirks.DEVICE_REGISTRY` is the *legacy v1* `LegacyDeviceRegistry`
>    and has no `resolve` attribute at all. Following this document as first
>    written would have loaded **every device without its quirk** and raised
>    `AttributeError` on the first connect.
> 2. **`ControllerError` does not exist** (§4.7's table). zigpy spells it
>    `zigpy.exceptions.ControllerException`, a subclass of `ZigbeeException`.
>    Caught by the wrong name, it would leave the boundary untranslated and
>    reach `api/devices.py`'s removal route as an unhandled 500.
> 3. **`NetworkSettingsInconsistent` takes three arguments**
>    (§4.6, §4.8): `NetworkSettingsInconsistent(message, new_state,
>    old_state)` — it carries both network backups. Harmless for the source,
>    which only ever catches it, but a test or a fixture that constructs one
>    with a message alone does not compile against the real class.

`validate_network_settings = True`, as ZHA sets it. Its consequence is
section 4.6.

### 4.2 `DeviceSource` Conformance

| Method | Zigbee implementation |
|---|---|
| `technology` | `"zigbee"` |
| `connected` | An explicit `self._connected` flag, set in `connect()`, cleared by the `connection_lost` listener and by `disconnect()`. **Not** `bellows.is_controller_running`, which is never cleared on loss (R1 §2, research E.3) |
| `connect()` | Section 4.1 |
| `disconnect()` | `app.shutdown(db=True)`; must always run, or bellows' non-daemon serial thread leaks and the stick is left mid-frame (research E.1, G14) |
| `wait_for_link_loss()` | `await self._link_lost.wait()` on an `asyncio.Event` set by the `connection_lost` listener **and by `disconnect()`** — a supervisor parked here holds nothing else, so a caller that takes the radio away must wake it; returns immediately when already disconnected — the same contract as `BridgeMatterClient.wait_for_link_loss` |
| `snapshots()` | The devices zigpy loaded from its own database, `available` from section 4.5. Works **even when the stick is gone**: `new(start_radio=False)` loads the DB without touching the radio (research E.2) |
| `subscribe()` | Registers per-cluster listeners after every (re)connect and starts one dispatch task (section 4.4) |
| `follow()` | Re-reads a device and hands a fresh snapshot to `on_node_snapshot`; this is the path that creates signal rows for values that only arrived later (section 5.5) |
| `send()` | Section 4.7 |
| `remove()` | `app.remove(ieee)`; treat `device_removed` as the truth (R1 §4) |

`snapshots()` and `subscribe()` must tolerate being called while
disconnected, because `sources.supervisor.attach()` calls both
unconditionally and `cli._run` runs `attach` for every source at startup.
That is the cheaper half of the choice: the alternative is teaching startup
to skip `attach` for an unconnected source, which would put startup and
reconnect on different paths — the exact drift `attach()` exists to prevent.
The supervisor then does the right thing on its own: `wait_for_link_loss()`
returning immediately puts it into its 1 s → 60 s backoff loop, forever
(research E.2, `sources/supervisor.py`).

### 4.3 Reconnect

zigpy and bellows never reconnect by themselves. A lost link surfaces once,
as the listener event `connection_lost(exc)`; bellows' watchdog (every 10 s,
four consecutive failures) turns a wedged NCP into the same event (R1 §2,
research E.3). So the supervisor's existing loop is exactly right, and the
only new work is making the event reach it.

**bellows keeps its own event loop in its own thread** (`use_thread=True`,
the default) and proxies calls both ways. Keep it: ASH acknowledges frames on
a deadline, and loxmatter's main loop does synchronous SQLite work, so a
blocked main loop would otherwise cause NCP resets (research E.1). This is
how Home Assistant runs it too.

### 4.4 Events Into the Runtime

zigpy's cluster events are **synchronous callbacks that do not catch
exceptions** (R1 §6). Reuse the Matter client's shape: the callback does
nothing but `queue.put_nowait(...)`, and one dispatch task does the awaiting
work (`matter/client.py`, `_subscribe_attribute_paths` and
`_dispatch_loop`). A raising handler then cannot tear down zigpy's event
emission.

Subscribe to the four attribute events — `attribute_report`,
`attribute_read`, `attribute_updated`, `attribute_written` — on every cluster
of every device, both freshly initialized and database-loaded, and
re-subscribe after every reconnect, reinterview and removal (R1 §6).

The **reinterview** half of that sentence needs two library facts, or it
cannot be implemented (both verified against zigpy 2.2.0):

- The event is **`device_reinterviewed`**, not `device_initialized`.
  `ControllerApplication._device_reinterviewed` emits it deliberately —
  its own comment says callers "should listen for `device_reinterviewed`
  instead" — and `raw_device_initialized`, the only other event that fires,
  means "interviewing" and is too early to bind to.
- It hands over a **new device object with new clusters** under the same
  IEEE (`old_device.on_remove()`, then `_finalize_device(shadow)`). A
  re-subscription guarded by address therefore returns early and binds
  nothing; the guard has to compare the device object.

### 4.5 Availability

zigpy has no availability concept; ZHA's is worth copying literally
(research E.4).

- Every packet updates `device.last_seen`, and zigpy persists it — so after
  a restart the online state is seeded from the database rather than
  unknown.
- A checker every 30–45 s: online while `now - last_seen` is under the
  threshold. The threshold is **2 h for mains-powered, 6 h for battery**,
  read from the node descriptor's `is_mains_powered`.
- For mains devices, **ping before declaring them offline**: read
  `Basic.manufacturer` with `allow_cache=False`, twice, with two grace
  periods. LUMI devices are exempt — they do not answer.
- The result goes into `Runtime.set_online(device_id, online)` and is seeded
  through `NodeSnapshot.available` at `attach()` time.
- **On link loss, push `set_online(False)` for every Zigbee device.**
  Otherwise Loxone keeps the last value and believes the sensors are still
  alive.

### 4.6 Startup Failures, in the User's Words

Measured (research E.2): a missing device raises `FileNotFoundError`
immediately — **not** zigpy's `TransientConnectionError`, which only covers
`ENETUNREACH` — and a silent port raises `TimeoutError` after 7.5 s. Each
gets its own message rather than one generic failure:

| Cause | Exception | What the UI says |
|---|---|---|
| Stick unplugged or renamed | `FileNotFoundError` | The stick is not there any more; check the radios card |
| Container may not open USB serial devices | `PermissionError` | The container lacks device permission; refresh the Compose stack (section 8) |
| Another process holds it | `OSError(EBUSY, "... already locked ...")` | Another program is using this stick — a second loxmatter instance is the common case (G13) |
| Stick answers nothing | `TimeoutError` after 7.5 s | This stick does not answer as a Zigbee coordinator; it may be running Thread or bootloader firmware (research A.4 item 6) |
| Stick carries a different network | `NetworkSettingsInconsistent(message, new_state, old_state)` | Section 4.8 |

### 4.7 Command Timeout and the Error Vocabulary

This closes boundary design open point 11 and research E.6/G12.

zigpy retries a request twice and waits 5 s per attempt for a mains device,
**28 s for an end device or one without a node descriptor** (R1 §8). Every
source call originating from Loxone or the web UI is therefore wrapped in
`asyncio.wait_for(..., 10)` — an explicit bound, at the one place that knows
a human or a Miniserver is waiting.

The mapping, which `api/control.py` and `api/devices.py` already have the
seams for (`SourceNotConfiguredError` → 503 at line 338, a bare `except
Exception` → 502 `api.errors.device_unreachable` at line 341):

| Condition | Status |
|---|---|
| `TimeoutError`, `DeliveryError`, `ControllerException`, `ZigbeeException`, a non-SUCCESS ZCL status, our own "radio not connected" | **502**, `api.errors.device_unreachable` |
| No Zigbee source configured (`SourceNotConfiguredError`) | **503**, unchanged |

The 502 set matters beyond tidiness: `devices.py`'s removal route today
catches only `MatterUnavailableError`, so a zigpy `DeliveryError` on the same
failure would surface as an unhandled **500**. The Zigbee source therefore
raises one exception type of its own for "asked and got nothing back", and
shared code catches that alongside `MatterUnavailableError`.

**A battery device that does not answer an outgoing command is physics, not
a bug** (G16). Its device tile says so next to the command, rather than
letting the user debug 502s.

### 4.8 A Stick That Already Carries a Network

zigpy adopts whatever is on the stick — it only forms when the network is
not formed (R1 §1). With `validate_network_settings = True` a mismatch
between the stored backup and the stick raises `NetworkSettingsInconsistent`
(G7). **The bridge stops there with a clear message and does not
overwrite.** The message offers the two real options in words — keep the
network that is on this stick, or restore the one this bridge knows — and
the second is 2b, because it is destructive and needs the write-once-EUI64
confirmation (R1 §10, G6).

One consequence the UI must not misreport: devices of an adopted network
start talking, and zigpy interviews them on its own
(`_discover_unknown_device`), so **devices can appear without anyone opening
a join window** (G7). Such a device is shown as discovered, not as "joined
just now".

### 4.9 The Heartbeat Keeps Its Meaning

This closes boundary design open point 9.1. `Runtime(link_ok=
sources.all_connected)` would silence the Loxone watchdog heartbeat when
only the Zigbee stick is gone, and Loxone would declare the whole bridge
dead while every Matter device still works. So the heartbeat keeps meaning
**"the bridge and the mandatory source are alive"** — the predicate covers
the Matter source only (research E.5).

Zigbee health is reported where it belongs: per-device `d<id>_online`
(section 4.5), plus a new `zigbee_connected` signal — one more virtual input
to wire *if* the user cares — and a badge in the web UI. This keeps the
watchdog's meaning stable for every existing installation, which is the
point.

## 5. Translating Into the Matter Model

### 5.1 What a Zigbee `NodeSnapshot` Contains

`attributes` is the entire interface: `extract_signals`
(`matter/discovery.py`), `device_types_by_endpoint`
(`profiles/relevance.py`), `extract_commands` (`export/commands.py`) and
`Store.register_signals` read nothing else (research C.1). So the edge
synthesises, per endpoint:

- `"<ep>/29/0"` → `[{"0": <matter device type>, "1": 1}]`. The string field
  tag is what `_device_type_ids` expects (it accepts `"0"` and `0`).
- `"<ep>/<cluster>/65529"` → the accepted command list (5.4).
- `"0/40/1"`, `"0/40/3"`, `"0/40/18"` → manufacturer, model, IEEE, the three
  paths `NodeSnapshot.from_raw` reads for Matter. Endpoint 0 is free to use:
  in Zigbee it is the ZDO endpoint and carries no ZCL clusters.
- `"0/29/0"` → `[RootNode 0x0016]`, plus `PowerSource 0x0011` for battery
  devices, so that `is_functional`'s layer 2 treats it exactly like a Matter
  root endpoint and `endpoint_labels` calls it "Device".
- `unique_id` = the IEEE; the store key is `zigbee:<ieee>` (boundary design
  §4.2).

### 5.2 Class by Class

`=` means the numbers are literally identical and nothing is converted;
**edge** means the Zigbee source computes it (research C.2).

| Class | Zigbee | Matter path | Relation |
|---|---|---|---|
| Lamp on/off, plug | `0x0006/0x0000` | `<ep>/6/0` | = |
| Dimmer | `0x0008/0x0000` | `<ep>/8/0` | = (ZCL 0–254, Matter 1–254) |
| Colour hue / saturation | `0x0300/0x0000`, `/0x0001` | `<ep>/768/0`, `/1` | = |
| Colour XY | `0x0300/0x0003`, `/0x0004` | `768/3`, `768/4` | = (table entry added, 5.3) |
| Colour temperature | `0x0300/0x0007` mireds | `768/7` mired | = |
| Colour mode / capabilities | `0x0008`, `0x400A`, `0x400B/0x400C` | `768/8`, `768/16394`, `16395`, `16396` | = |
| Temperature | `0x0402/0x0000` int16s, 0.01 °C | `<ep>/1026/0`, scale 0.01 | = ; **edge**: 0x8000 → `None` |
| Humidity | `0x0405/0x0000` uint16, 0.01 % | `<ep>/1029/0`, scale 0.01 | = ; **edge**: 0xFFFF → `None` |
| Occupancy (real 0x0406) | `0x0406/0x0000` bitmap8, bit 0 | `<ep>/1030/0` | = ; table entry added |
| Illuminance | `0x0400/0x0000` | `<ep>/1024/0` | = (both 10000·log10(lux)+1; left raw) |
| IAS contact | `0x0500/0x0002` bit 0, `zone_type = 0x0015` | `<ep>/69/0` BooleanState = **not** alarm1 | **edge** |
| IAS water leak | same, `zone_type = 0x002A` | `<ep>/69/0` = alarm1 | **edge** |
| IAS motion | same, `zone_type = 0x000D` | `<ep>/1030/0` bit 0 = alarm1 \| alarm2 | **edge** |
| IAS other (fire, CO, vibration) | `zone_status` raw | `<ep>/1280/2` | **edge**, pass-through |
| Battery percent | `0x0001/0x0021` uint8, half percent | `0/47/12`, scale 0.5 | = in unit, **edge** in location; 0xFF → `None` |
| Battery voltage | `0x0001/0x0020` uint8, 100 mV | `0/47/11` mV | **edge**, optional |
| Plug power / energy | `0x0B04` / `0x0702` with multiplier and divisor | `<ep>/144/4,5,8`; `<ep>/145/1` | **edge** — **2b** |

**Read the alarm as `value & 0b11`, not bit 0 alone** — that is what ZHA
does, and `zone_status`'s bit assignments are Alarm_1=1, Alarm_2=2, Tamper=4,
Battery=8, Supervision=16, Restore=32, Trouble=64, AC=128 (research C.2).

**Matter's BooleanState polarity for contact sensors is inverted relative to
IAS**: Matter's `StateValue` is true when *closed*, while IAS alarm1 is true
when *open*. This is taken from the Matter device library and the
`BooleanState.xml` data model, not measured here — section 10 records how to
settle it empirically on the test Pi with devices that already exist.

### 5.3 Device Types

Key the map by **(profile ID, device type)**, never by device type alone:
ZLL `0x0100` is a dimmable light while ZHA and Matter `0x0100` is an on/off
light (R1 §11). For IAS devices key by `zone_type` instead: contact →
Contact Sensor `0x0015`, water → Water Leak Detector `0x0043`, motion →
Occupancy Sensor `0x0107`, fire/CO → Smoke/CO Alarm `0x0076`.

Every number in R1 §11's draft map already exists in
`matter_server.client.models.device_types`, which is what
`test_every_mapped_type_exists_in_the_matter_table` enforces — so
`category_for` and `endpoint_labels` work unchanged.

### 5.4 `AcceptedCommandList`

Zigbee has no reliable equivalent: ZCL "Discover Commands Received" is
optional and widely unimplemented. Synthesise from clusters and
capabilities, and synthesise **only what `commands/translate.py` can
actually build**, because `extract_commands` drops the rest anyway outside
raw mode (research C.4):

| Endpoint has | `<ep>/<cluster>/65529` |
|---|---|
| in-cluster `0x0006` | `[0, 1, 2]` |
| in-cluster `0x0008` | `[0, 4]` |
| `0x0300` with `color_capabilities & 0x10`, or a readable `color_temperature` | add `10` |
| `0x0300` with `color_capabilities & 0x01` | add `6` |
| `0x0300` with `color_capabilities & 0x08` | add `7` (see 5.6) |

`ADMINISTRATIVE_CLUSTERS` in `profiles/table.py` is Matter-numbered and does
not protect a Zigbee device. Add a **Zigbee block list at the edge** for at
least `0x0019` (OTA), `0x0003` (Identify — harmless but noise), `0x0000`
(Basic, which carries `reset_to_factory_defaults`) and `0x1000` (ZLL
commissioning).

### 5.5 Profile Table Additions

These go into `src/loxmatter/profiles/clusters.yaml` because they improve
Matter devices too — occupancy, BooleanState, illuminance and colour XY are
Matter clusters that are simply missing from the table (research C.5). New
entries are safe for existing installations: keys are assigned only when a
signal row is created (`store.py`, `register_signals`), so an existing
`c69_a0` keeps its key.

| Cluster | Entry |
|---|---|
| 1030 OccupancySensing | `0: {slug: occupancy, unit: ""}`, rank 10 |
| 69 BooleanState | `0: {slug: state, unit: ""}`, rank 10 |
| 1024 IlluminanceMeasurement | `0: {slug: illuminance, unit: ""}` — left raw, both sides use 10000·log10(lux)+1 |
| 768 ColorControl | `3: {slug: color_x}`, `4: {slug: color_y}` |
| 1280 IasZone | `2: {slug: ias_zone_status, functional: false}` |

The IAS entry is deliberately `functional: false`: the raw bitmap stays
visible as an expert signal without being exported twice next to the
BooleanState or occupancy path the edge derives from it.

**The `None` rule.** `Store.register_signals` computes `exported` **only
when the row is created**, and a path whose value is `None` classifies as
`Exportability.NONE` — so a signal first seen without a value stays
*unexported forever* until the user toggles it by hand (research D.1 step 5,
G1). Therefore: **never put a path with value `None` into a snapshot.**
Leave the path out; when the first real value arrives, go through
`follow`/`on_node_snapshot`, which calls `register_signals`, invalidates the
signal index and seeds the value — creating the row properly.

### 5.6 Colour, and a Fix That Helps Both Technologies

ZHA 2.2.2 sends colour **only** as `move_to_color` (XY) and
`move_to_color_temp`, never hue/saturation, and it assumes XY when
`color_capabilities` is missing. loxmatter supports the opposite: `(768, 6)`
hue/saturation, and no `(768, 7)` (research C.4, `_PAYLOAD_BUILDERS` in
`commands/translate.py`). Matter's own Extended Color Light makes XY
mandatory and hue/saturation optional.

So: **add `(768, 7)` MoveToColor with an RGB→CIE xy builder in
`commands/color.py`**, used by both technologies. It is the difference
between "colour works on this Zigbee bulb" and "the colour output does
nothing", and it improves Matter lamps at the same time. The new builder
sits next to `rgb_to_hue_saturation` and follows that module's standing
rule: whoever touches it measures again.

`clusters.yaml` gains `768` command `7: {slug: color_xy, takes_value: true,
control: hue_sat}` so the command is exported rather than appearing as a raw
`c768_cmd7`.

**Before the hardware step: a database written between 12 September 2026's
two commits carries a stale row.** Naming `(768, 7)` let `extract_commands`
register a `color_xy` command for every lamp that accepts MoveToColor,
including the white-spectrum lamps the capability gate added hours later
withholds it from. The gate applies at extraction; `api/control.py`'s
`controls` route reads the STORED command rows, and `store.register_commands`
only inserts and updates — nothing in `model/store.py` ever deletes a command
row, because a deleted row would take its key with it and a key is Loxone
wiring. So a device commissioned in that window keeps the row, and its colour
picker survives every restart and every code update. There is deliberately no
migration and no prune for it: the maintainer's Pi never ran that state (the
branch was neither merged nor pushed), and a delete path in the command table
is a far larger decision than this. The one way to clear such a row is to
forget the device and commission it again. A test installation showing a
colour picker on a white-only lamp should be suspected of this before the
gate is.

On a colour-capable lamp the stale row is harmless in practice: such a lamp
also carries `(768, 6)` hue/saturation, so `duplicate_control_command`'s
dedup preference already hides the extra `color_xy` widget; a white-spectrum
lamp never gets a `(768, 6)` row to prefer — its AcceptedCommandList runs
`[7, 8, 9, 10, 71, 75, 76]`, with `7` and no `6` — so there the stale row has
nothing to hide behind it, which is the case this caveat is actually about.

### 5.7 Command Argument Names

A table, not a `camelCase` → `snake_case` helper — two of these do not follow
the mechanical rule (research C.5):

| Matter (loxmatter builds) | zigpy |
|---|---|
| `colorTemperatureMireds` | `color_temp_mireds` (**not** `color_temperature_mireds`) |
| `transitionTime` | `transition_time` |
| `optionsMask` | `options_mask` |
| `optionsOverride` | `options_override` |
| `level` | `level` |
| `hue`, `saturation` | unchanged |

`move_to_level_with_on_off` (8/4) has **no** options fields in zigpy, while
`move_to_level` (8/0) has them as optional (R1 §8). Validate the payload
against `command.schema.fields` and drop or raise deliberately — silently
passing an unknown field is how a command turns into a parsing error on the
wire.

**One risk to measure on hardware.** `translate.py` relies on ColorControl's
`ExecuteIfOff` option bit to set colour on a lamp that is off. Older ZLL
firmware ignores the Options fields, in which case the lamp comes up in the
old colour; ZHA works around it by turning on first. If the test lamp shows
this, the workaround belongs at the Zigbee edge, not in `translate.py`
(research C.5).

## 6. Configure-on-Join

zigpy does not configure devices to report — that was ZHA's entity layer's
job, and without it a paired sensor is silent (boundary design §1.1). This
runs immediately on `device_initialized`, while the device is still awake
(R1 §3), inside `async with app.request_priority(PacketPriority.HIGH)` as
ZHA does (research D.1).

### 6.1 The Routine

1. **Quirk hook first.** `if hasattr(dev, "apply_custom_configuration"):
   await dev.apply_custom_configuration()`. This casts the Tuya "spell" — a
   specific `Basic` read of `[4, 0, 1, 5, 7, 0xFFFE]` — without which many
   Tuya devices never send anything. Skip bind and reporting entirely when
   `dev.skip_configuration` is set (79 quirks set it).
2. **Read the static facts** that decide type and commands: `zone_type`,
   `color_capabilities`, `color_temp_physical_min/max`, the `Basic` power
   source.
3. **Bind and configure reporting** per cluster, always through the
   *quirk's* cluster object so overrides such as
   `TuyaNoBindPowerConfigurationCluster` apply. Use
   `configure_reporting_multiple`, which batches by manufacturer code
   (R1 §7).
4. **IAS enrollment** (6.2).
5. **Read the current values** of every mapped attribute with
   `read_attributes(allow_cache=False)` — this is what makes section 5.5's
   `None` rule satisfiable rather than merely stated.
6. **Identify blink** at the end. A light that flashes is the cheapest "it
   worked" the user can get, and ZHA does the same.

### 6.2 Reporting Values

ZHA's values, which are the only field-proven set (research D.1, R1 §7):

| Cluster / attribute | min | max | change |
|---|---|---|---|
| OnOff `on_off` | 0 | 900 | 1 |
| LevelControl `current_level` | 1 | 900 | 1 |
| Color `current_x`, `current_y`, `color_temperature` | 30 | 900 | 1 |
| TemperatureMeasurement `measured_value` | 30 | 900 | 50 (0.5 °C) |
| RelativeHumidity `measured_value` | 30 | 900 | 100 (1 %) |
| OccupancySensing `occupancy` | 0 | 900 | 1 |
| IlluminanceMeasurement `measured_value` | 30 | 900 | 1 |
| PowerConfiguration `battery_voltage`, `battery_percentage_remaining` | 3600 | 10800 | 1 (battery devices only) |
| IasZone `zone_status` | **bind only, no reporting** | | |
| Metering / ElectricalMeasurement | 5 | 900 | 1 (multipliers 0/900/1) — **2b** |

Where reporting cannot be configured — status other than SUCCESS, common on
cheap lamps — fall back to polling as ZHA does for lights: a refresh every
2700–4500 s (research D.3). That poll doubles as a liveness check.

### 6.3 IAS Enrollment

Without this, a contact, motion or leak sensor **never reports anything**.
zigpy defines the commands but does not enroll (R1 §7). The steps, copied
from ZHA (research D.1 step 4):

1. Bind the IasZone cluster.
2. Read `zone_type` — it decides the target cluster and the polarity (5.2).
3. `write_attributes({cie_addr: app.state.node_info.ieee})`.
4. Send an **unsolicited** `enroll_response(Success, zone_id=0)`.
5. Permanently handle two client commands: `status_change_notification`
   (id 0) → `cluster.update_attribute(zone_status, args[0])`, and `enroll`
   (id 1) → answer with `enroll_response(Success, 0)`.

**Alarms arrive as that command, never as an attribute report.** A design
that only subscribes to reports will see a sensor that pairs, configures,
goes green — and never fires.

### 6.4 Sleepy End Devices

A `configure_reporting` to a sleeping device fails with `TimeoutError` or
`DeliveryError` after up to ~28 s per attempt (R1 §8). So (research D.2):

- **Use `device.fast_poll_mode()` around the whole routine** when the device
  has a PollControl cluster: it binds PollControl and writes
  `fast_poll_timeout`, keeping the device polling its parent during
  configuration.
- **Persist "configuration pending" per (device, cluster)** in loxmatter's
  own store, and retry opportunistically the next time anything is heard
  from the device — every incoming packet fires
  `device_last_seen_updated`, and right after a device transmits it polls
  its parent, so a queued request has its best chance then. Also retry on
  `device_joined` and on `checkin`.
- **Show it.** The pairing row says "waiting for the device to wake up"
  (section 3.1), and the device tile keeps a small "setup incomplete" marker
  until the last cluster is done. Guessing silently is what makes ZHA's
  "Configuring" hang feel like a bug.

### 6.5 Rejoin

A device that rejoins with a **new NWK** fires `device_joined` and
`device_initialized` again — run the routine a second time, because a
factory-reset device has lost its bindings. A rejoin with the **same NWK**
fires nothing, and bindings survive it anyway (research D.3).

Do **not** reconfigure everything at startup; ZHA does not either. Read
values instead. A manual per-device "Reconfigure device" button is the
documented escape hatch in both ZHA and Z2M and is **2b**.

## 7. Radio Choice and the Fingerprint Table

**Fingerprint, never probe on our own initiative** (research A.4). The
reasons are measured: Home Assistant's full auto-probe chain costs ~34 s on
a stick that answers nothing, and probing has side effects — ZNP toggles
DTR/RTS and then blasts 256 bootloader-skip bytes, which **resets the chip**
on every CP2102N/CH9102 dongle, and merely `open()`ing a tty on Linux
asserts DTR/RTS (research A.2). A stick that resets itself because the
settings card was opened is not acceptable.

A new data module beside `radios/inventory.py` maps the inventory
loxmatter already collects — `manufacturer`, `product`, `serial`,
`vid_pid`, the by-id path (`SerialRadio`) — to radio type, baud rate and
flow control, without opening the port. Columns: **VID:PID**,
**manufacturer**, **by-id path regex**, **radio type**, **baud rate**,
**flow control**. Ported from Zigbee2MQTT's table (research A.1):

| Stick | VID:PID | manufacturer / by-id | Type | Baud | Flow |
|---|---|---|---|---|---|
| HA Connect ZBT-1 / SkyConnect | 10c4:ea60 | Nabu Casa, `.*Nabu_Casa.*_ZBT-1.*` | EZSP | 115200 | hardware |
| HA Connect ZBT-2 | 303a:4001, 303a:831a | Nabu Casa, `.*Nabu_Casa_ZBT-2.*` | EZSP | 460800 | hardware |
| SONOFF ZBDongle-E V2 (CH9102) | 1a86:55d4 | ITEAD, `.*sonoff.*plus.*` | EZSP | 115200 | none |
| SONOFF ZBDongle-E V2 (CP2102N) | 10c4:ea60 | ITEAD, `.*sonoff.*plus_v2_.*` | EZSP | 115200 | none |
| SONOFF Dongle Plus MG24 | 10c4:ea60 | SONOFF, `.*sonoff.*plus.*mg24.*` | EZSP | 115200 | none |
| SONOFF Dongle Max MG24 / Lite MG21 | 10c4:ea60 | SONOFF, `.*sonoff.*max.*` / `.*lite.*mg21.*` | EZSP | 115200 | none |
| SONOFF ZBDongle-P (CC2652P) | 10c4:ea60 | ITEAD, `.*sonoff.*plus(?!_v2_).*` | ZNP | 115200 | none |
| SLZB-06M (USB mode) | 10c4:ea60 | SMLIGHT, `.*slzb-06m.*` | EZSP | 115200 | none |
| SLZB-06p7 / 06p10 / 07p7 | 10c4:ea60 | SMLIGHT, `.*SLZB-06p7_.*` etc. | ZNP | 115200 | none |
| SLZB-07 / 07mg24 | 10c4:ea60 | SMLIGHT | EZSP | 115200 | hardware |
| ConBee II | 1cf1:0030 | dresden elektronik, `.*conbee.*` | deCONZ | 115200 | none |
| ConBee III | 0403:6015 | dresden elektronik, `.*conbee.*` | deCONZ | 115200 | none |

**`10c4:ea60` must never match on VID:PID alone.** It is shared by at least
six sticks above — including the maintainer's SONOFF MG24 — and Z2M
explicitly refuses a VID:PID-only match for it. Only the by-id string tells
them apart; a `10c4:ea60` with no telling by-id string is "unknown", not a
guess.

Flow control comes from this table too, not from a probe: bellows maps
`None` → XON/XOFF and anything else → RTS/CTS, and ASH escapes 0x11/0x13, so
software flow control is safe (research A.4 item 5).

**An unknown stick** gets an "Advanced" disclosure exposing radio type and
baud rate, defaulting to EZSP at 115200, and a plain sentence that loxmatter
does not recognise this stick. It is selectable — refusing to work with an
unlisted stick would be worse than letting the user say what it is.

**"Test this stick"** — an explicit, user-initiated probe in EZSP → ZNP →
deCONZ order, honestly labelled with the measured worst case ("this can take
up to about 40 seconds") — is recorded as a **2b** option. When it lands it
must never run against the Thread stick, never at startup, and it needs
deCONZ's baud loop written by hand (115200, then 38400), because
zigpy-deconz's `_probe_config_variants` is dead code the base class never
reads (research A.1).

## 8. Deployment and Dependencies

### 8.1 Compose

Serial access **without naming a device**. A `devices:` entry fails the
whole stack at `docker compose up` when the node is absent — that is why
`otbr` sits behind a profile — and a `devices:` node is copied into the
container at create time, so hotplug is invisible (research F.8). Also,
`tests/test_compose_profiles.py` asserts that no service outside the
`thread` profile has a `devices:` key, and that is the right rule.

```yaml
    volumes:
      - type: bind
        source: /dev
        target: /host-dev
        read_only: true
    device_cgroup_rules:
      - 'c 188:* rmw'   # ttyUSB* (CP210x, CH34x, FTDI)
      - 'c 166:* rmw'   # ttyACM* (CDC-ACM)
      # - 'c 204:64 rmw'  # only for a GPIO-UART hat
```

`/host-dev`, not `/dev`, and that was measured, not assumed: a read-write
`-v /dev:/dev` let container root **delete a host device node** and made the
container's `/dev/shm` the host's, while a recursive `:ro` bind at `/dev` in
Docker 25+ makes `/dev/shm` read-only and breaks `multiprocessing.Lock()`. A
read-only bind does not block opening character devices, and relative
`serial/by-id/../../ttyUSB0` symlinks resolve inside the mount (research
F.8). Binding `/dev/serial/by-id` alone is insufficient — those symlinks are
relative and would dangle.

Majors verified from the kernel's `Documentation/admin-guide/devices.txt`:
**188 = USB serial converters**, **166 = ACM USB modems**; `ttyAMA` (GPIO
UART) is 204:64 per the PL011 driver, not the 204:16 in that file.

`serialx` enumerates ports from `/sys/class/tty` and reports `/dev/...`
names, so **loxmatter maps the `/host-dev` prefix itself**.

`tests/test_compose_profiles.py` gains an assertion for the two cgroup rules
(G18).

### 8.2 The Sharp Edge: otbr Shares Major 188

`RADIO_DEVICE` defaults to `/dev/ttyUSB0`, so the Thread RCP is itself a
major-188 node and `c 188:* rmw` hands the bridge access to it (research
F.8). Exclusion is enforced in loxmatter by resolved `major:minor` (section
3.2). Additionally, `&uart-exclusive` should be appended to otbr's
`RADIO_URL`: OpenThread only takes `flock` + `TIOCEXCL` when the radio URL
carries it, and the compose file passes no lock at all today (research A.3).
Verify on the Pi that the installed otbr image honours or ignores the
parameter rather than refusing to start — `TIOCEXCL` is in any case bypassed
by a holder of `CAP_SYS_ADMIN`, which privileged otbr has, so this is a
second layer and not the guarantee.

### 8.3 Dependencies

`uv lock` on a copy resolved cleanly: **32 added, zero updated, zero
removed** — no existing loxmatter pin moves, because zigpy declares its
requirements unpinned (research F.1). All 63 packages install from wheels on
arm64; no source builds (F.2). Licences are all compatible with
GPL-3.0-or-later (F.5), and Apache-2.0 §4 asks for NOTICE propagation, so a
third-party licence list joins the existing `license-files` entry.

**`zha` and the five radio libraries cannot be avoided** (F.6). `zha-quirks`
imports `zha.quirks`; `zhaquirks.setup()` reaches
`zha/application/const.py`, which imports **all six** radio applications at
module level. Either take the whole tree, or ship zigpy+bellows without
quirks and accept that Tuya and Aqara devices misbehave. There is no middle
road, and `override-dependencies` would resolve and then crash at runtime.

Two packaging warts to know: `zha` and `zigpy` both ship a top-level
`tools/` package, so they overwrite each other and **loxmatter can never add
a module of that name** (F.1, G17). And every `zha-quirks` upgrade moves
four packages in lockstep, because `zha` pins each radio library exactly
(R1 §9).

### 8.4 Image and Startup

Image size grows **+33 MB** gzipped-layer 14.6 → 27.3 MB; cryptography alone
is 14.2 MB and is unavoidable, being zigpy core (F.3).

**`zhaquirks.setup()` costs 2.1–2.7 s warm on an M1 Pro and +73–75 MB RSS,
extrapolating to 9–15 s on a Pi 4 and 4–6 s on a Pi 5** (F.4). So: never at
import time. Run it **once per process in an executor, in a background
task**, only when a Zigbee radio is configured, and keep `/health` and the
web UI answering while it runs — `cli._run` starts uvicorn only after
`attach`, so the Zigbee source must not hold that up. Log the measured
duration.

Set **`UV_COMPILE_BYTECODE=1`** in the Dockerfile (it is absent today). It
costs ~16 MB and makes startup deterministic: without it the 462 quirk
modules are byte-compiled at first import inside the container and again
after every `--force-recreate` — which turns "2 s" into double-digit seconds
on a Pi 4 exactly when the user is watching the update card (F.3, G19).

### 8.5 zigpy Configuration

| Setting | Value | Why |
|---|---|---|
| `database_path` | `/data/zigbee.sqlite` | Next to the store in the same volume; zigpy needs SQLite ≥ 3.24 and the image has 3.46.1 (G3) |
| OTA | **off** | On by default with three internet providers and a broadcast every 3.9 h. A bridge that silently updates the user's lamps from the internet is not what this project promises (G9) |
| Topology scan | **kept** (4 h default) | It is what makes neighbour tables and, later, "join via this router" possible; it does generate traffic (G10) |
| `validate_network_settings` | `True` | Section 4.8 |
| Channel | energy scan, **excluding the Thread channel** | Read the active dataset from OTBR (`matter/otbr.py`) and exclude its channel from the candidate list `[11, 15, 20, 25]` when forming. After forming the channel is effectively fixed — `move_network_to_channel` exists but sleepy devices may not follow (G8) |

Container stop grace is 10 s by default, so shutdown must cancel the
watchdog, run `shutdown()` and flush the DB inside it, or the stick is left
mid-frame and the next start pays for it with a reset sequence (G14).

Expect Zigbee traffic at startup even with no user action: some quirks
schedule reads when the device object is built, so simply loading the
database produces traffic. That is not an unexpected wake-up (G15).

## 9. Security and Privacy

**What the cgroup rule grants.** The rule is the grant, not the bind:
container root already holds `CAP_MKNOD` and Docker's default `c *:* m`, so
it can `mknod` a 188:N node itself — which is exactly how the measurements
in F.8 were run. The bind mount only supplies stable names and hotplug
visibility; removing the rule denies access with `EPERM` (measured, F.9).

`c 188:* rmw` is coarse: **every** USB-serial adapter on the Pi, the Thread
stick included. It does not reach block devices, `/dev/mem` or i2c — those
stay `EPERM` even though the `/dev` bind shows them. Against `privileged:
true` that is a much smaller blast radius, which matters for a service that
already runs with `network_mode: host` and, today, as root.

**Residual exposure, to be written into the README and the updater security
section** (the radios design §6.6 has the pattern): the listing leaks
hardware inventory, including serial numbers under `by-id`, and code
execution in the bridge could talk to any USB-serial device — including
garbaging the Thread RCP. Narrowing the rule to observed minors (`c 188:0
rmw`) is possible at the cost of "works after replugging into another port".

**The network key is a secret in clear text.** zigpy's backups live inside
its database and can be emitted as Open Coordinator Backup JSON containing
the network key unencrypted (R1 §10, G5). This spec does not build the
backup download — that is 2b — but it does bind the rule now: never log the
key, and when the download lands it is marked the way the Matter fabric
backup is marked.

`dialout` group membership is irrelevant today because the container runs as
uid 0 (no `USER` in the Dockerfile) and `CAP_DAC_OVERRIDE` opens the
`root:dialout 0660` node. When the planned non-root hardening lands the
service needs `group_add: ["20"]`, numeric, and the zigpy database needs
write permission (F.7, G3).

## 10. Testing

### 10.1 What Fake-Based Tests Must Prove

Every test that names a protection is shown to catch it: the fault is
introduced, the test is seen failing, the fault is reverted. That is this
repository's standing rule, and these tests are where it earns its keep,
because the hardware is absent (10.3).

| Test | Protects | Fault introduced to prove it |
|---|---|---|
| Snapshot synthesis | `0/29/0` root type, per-endpoint `29/0`, `0/40/1,3,18`, `65529` lists | Drop the root-node entry and watch `is_functional` reclassify |
| **No `None` in a snapshot** | A path whose value is `None` is absent, not present-and-null | Emit the path with `None`; the signal must become permanently unexported |
| Late value creates the row | A path absent at join and present later reaches `on_node_snapshot` and gets a row with `exported` computed | Skip `invalidate_index` and watch the value be silently discarded |
| IAS mapping | contact → `69/0` inverted, water → `69/0` direct, motion → `1030/0`, other → `1280/2` raw; alarm read as `value & 0b11` | Read bit 0 alone; an Alarm_2-only sensor stops reporting |
| Device types | (profile, type) keyed; ZLL `0x0100` → Dimmable, ZHA `0x0100` → On/Off | Key by device type alone |
| `AcceptedCommandList` | The five rows of 5.4, including `7` only with the XY capability bit | Always add `6` |
| Argument renaming | The table of 5.7, and that 8/4 carries no options fields | Use a mechanical camel→snake helper; `color_temp_mireds` breaks |
| Sentinels | 0x8000, 0xFFFF, 0xFF → path omitted | Pass them through as numbers |
| Command timeout | A source call that never returns is abandoned at 10 s | Remove the `wait_for` |
| Error mapping | zigpy's exception set → 502; `SourceNotConfiguredError` → 503 | Map `DeliveryError` to 500 and watch removal break |
| Heartbeat | Heartbeat keeps pulsing when only Zigbee is down | Use `sources.all_connected` |
| Availability | Offline at 2 h mains / 6 h battery; ping before offline; **all devices offline on link loss** | Drop the link-loss sweep; Loxone keeps stale sensor values |
| Reconnect | `connection_lost` sets the event; `connect()` builds a fresh application | Reuse the old object after a failed `startup()` |
| Fingerprints | Every row of section 7; `10c4:ea60` never matches on VID:PID alone | Allow the VID:PID-only match and watch a ZNP stick be called EZSP |
| Thread exclusion | Refused by resolved `major:minor`, not by path string | Compare path strings; the by-id/`ttyUSB0` pair slips through |
| Compose | `/host-dev` read-only, both cgroup rules, still no `devices:` key | Remove a rule |
| Colour | RGB → CIE xy against published reference values | Swap x and y |

The pairing tab's behaviour is checked in a throwaway harness the way the
transport badge was (boundary design §7.3): the tab's markup cut out of
`index.html` by script, with `style.css` and the vendored Alpine beside it,
served over http — countdown from `permit_until`, the seven row states, and
the window closing when the tab is left.

### 10.2 What Only Hardware Can Settle

- Whether reporting actually arrives at the configured intervals, and
  whether a given cheap lamp refuses `configure_reporting` and falls through
  to polling.
- IAS enrollment end to end: that a contact sensor fires at all.
- Whether `ExecuteIfOff` works on the specific lamp, or the turn-on-first
  workaround is needed (5.7).
- Whether the adopted-network and `NetworkSettingsInconsistent` paths behave
  as read.
- The real cost of `zhaquirks.setup()` on the Pi, against the 9–15 s
  extrapolation.
- What a cancelled `BridgeMatterClient.send` leaves behind upstream. Every
  call into a source now runs under `bounded_source_call`'s
  `asyncio.wait_for` (`sources/__init__.py`), so a Matter command that
  outlives `SOURCE_CALL_TIMEOUT_SECONDS` is **cancelled** while it awaits a
  websocket RPC future inside `python-matter-server`. Whether that client
  then drops its entry in its own result-future map, or keeps a pending
  future for a reply that arrives later, is upstream behaviour nothing here
  can exercise without a device that stalls for ten seconds. Check it on the
  Pi: make one command time out (a device powered off mid-command), then
  keep the instance running and watch whether later commands still get their
  replies and whether the process's memory grows across repeats. A leak here
  would be slow and would look like "the bridge gets worse the longer it
  runs", which is exactly the kind of thing to find on purpose rather than
  by surprise.
- Matter's BooleanState polarity (5.2). This one is settleable **today with
  Matter devices that already exist**: MYGGBETT (contact) and KLIPPBOK
  (water leak) are commissioned, and reading their `x/69/0` in a known
  physical state decides it empirically — read through the running
  instance's `signals` route, never through a fresh `snapshots()` call,
  which returns matter-server's cache.

### 10.3 There Is No Zigbee Stick on the Test Pi

The Pi has exactly one USB stick, the SONOFF Dongle Plus MG24, and it is the
Thread RCP (radios design §3, measured 11 September 2026). **Nothing in this
spec has been exercised against Zigbee hardware, and nothing in it should be
reported as verified.** Every timing, every reporting interval and every
device behaviour here is read from ZHA's and zigpy's source or measured
against a silent pty, not against a radio that answered.

Two practical consequences. First, the fake-based suite of 10.1 is not a
convenience, it is the only evidence available before a second stick is
bought, and it should be written as if it were the last line of defence.
Second, the first hardware session needs its own checklist entry extending
`2026-09-09-first-run-checklist.md`, and the Thread-exclusion rule of
section 3.2 must be proven on the Pi **before** any Zigbee code opens a
port — a wrong pick garbles the maintainer's live Thread network.

## 11. Explicitly Not Built Here

All of these are 2b:

- Coordinator replacement, including the write-once-EUI64 confirmation
  dialog (R1 §10, G6).
- Network backup and its download as a marked secret (G5).
- A rejoin blocklist of loxmatter's own — zigpy has none (B.1 item 7, G11).
- Metering and ElectricalMeasurement scaling with per-device
  multiplier/divisor (C.2, R1 §12).
- Remotes and buttons: Zigbee remotes send commands to the coordinator
  rather than reporting attributes, so `SignalKind.EVENT` paths for them
  need their own design (R1 §6c).
- A per-device "Reconfigure device" button (D.3).
- The zigpy-DB rollback note for the updater: zigpy's schema is
  `DB_VERSION = 15` and a rolled-back bridge sees a **stale** Zigbee world,
  degraded rather than dead — the same bargain as boundary design §4.1, but
  it must be written down and `zigbee.sqlite` copied next to the updater's
  other pre-update backups (G4).
- "Test this stick" probing, and `universal-silabs-flasher` for "this stick
  runs Thread firmware" (A.4).
- "Join via a specific router" (B.1 item 6).
- Install codes (`permit_with_link_key`).
- Native Matter groups for Zigbee devices.

## 12. Open Points

1. **2a-1 is not finished** (section 1). The implementation plan has to
   decide whether to land its API, card and Compose changes first or to
   carry them, and the estimate depends on the answer.
2. **`parse_technology` still fails the whole device list for one bad row**
   (boundary design open point 10). This spec does not need it — `zigbee` is
   already in `Technology` — but the lenient read should land with the first
   code that writes non-Matter rows, because that is when a rollback starts
   producing rows an older version cannot parse.
3. **Group fan-out still conflates "not configured" with "no answer"**
   (boundary open point 12). Section 4.7 fixes the single-device path; the
   group path should draw the same distinction, and now there is a second
   technology to get it wrong with.
4. **`api.errors.source_not_configured` interpolates the raw lowercase
   technology** (boundary open point 13). Technologies should get a display
   name the way categories do.
5. **`&uart-exclusive` is unverified** against the otbr image actually
   installed on the Pi (section 8.2).
6. **The IAS `cie_addr` and every binding point at the current coordinator's
   IEEE.** If the coordinator IEEE ever changes, everything must be
   reconfigured (D.3, G6) — 2b builds the replacement flow, but this spec's
   configure-on-join is what it will have to re-run.
7. **Two loxmatter instances on one Pi** is a routine of this project's
   maintenance, and `serialx`'s flock makes the second one fail cleanly on
   the same stick (G13). Section 4.6 surfaces that text; whether the second
   instance should instead run read-only against the same zigpy database is
   not decided.

## 13. Corrections After Implementation

Added 13 September 2026, when the implementation plan
(`docs/superpowers/plans/2026-09-12-zigbee-source.md`) had been carried out
on branch `claude/radios-in-web-ui`. Sections 1 to 12 above stay the record
of what was designed; this section lists where the build departed from
them. **Nothing in the build has been exercised against Zigbee hardware**
(section 10.3 still holds; see the first-run checklist's section 15).

### 13.1 The plan's five corrections, found before Task 1

1. **Section 1 is stale.** `api/radios.py`, the radios router, the radios
   card and the `/dev:/host/dev:ro` mount all shipped with 2a-1, so open
   point 1 of section 12 answered itself: neither landed first nor carried.
2. **Section 8.1's bind target is `/host/dev`, not `/host-dev`.** The
   repository already used `/host/dev` in Compose, the sidecar and a passing
   test; only the two `device_cgroup_rules` were missing.
3. **Section 3.2's "third half" of the sidecar request was wrong.** The
   sidecar applies a radio change by recreating a container, and zigpy runs
   inside the bridge, so a Zigbee half would have recreated `loxmatter`
   itself. The Zigbee stick is a bridge-owned setting in loxmatter's own
   `setting` table, applied in process by reconnecting the source, with its
   own Apply on the card (`3ab60ef`, `d5c44ff`).
4. **Section 4.7's line numbers had moved.** The seams are cited by name;
   the predicted 500 on removing a Zigbee device was real and was fixed
   (`92f332d`, `8f425e7`).
5. **Section 5.5's `1280` entry is correct but sits on the cluster-40 trap**
   in `clusters.yaml`: an `attributes:` section filters every attribute of
   the cluster through `names_element`. That is wanted here, and is recorded
   so nobody tidies it into a bare `rank`.

### 13.2 Edits already made to the body during implementation

Each was made in place, with a note in the text where it matters, and is
listed here so the body is not mistaken for the untouched design.

- **Section 4.1: three library names corrected** (`e1153cf`).
  `device_resolver` is `zhaquirks.ZHA_DEVICE_REGISTRY.resolve` (the name
  written first is the legacy registry and would have loaded every device
  without its quirk), `ControllerError` is `ControllerException`, and
  `NetworkSettingsInconsistent` takes three arguments. The same commit adds
  the `device_reinterviewed` facts and that `disconnect()` also wakes
  `wait_for_link_loss()`.
- **Section 5.6: the stale-row caveat** (`c08d6bd`, `1b3874a`). A database
  written between two commits of 12 September 2026 keeps a `color_xy` row on
  a white-spectrum lamp until the device is commissioned again.
- **Section 10.2: what a cancelled `BridgeMatterClient.send` leaves behind
  upstream** (`8f425e7`), a hardware question added once every source call
  became bounded.
- **Sections 3.1 and 3.4: the pairing tab as built** (`b3462d9`). The
  button labels, the `m:ss` countdown, the explicit Add button, who closes a
  join window, a reload that shows an open window, the folded list of rows
  already added, and the keys those needed.

### 13.3 Other departures recorded in the progress ledger

- **The Thread lock-out is gated on Thread actually running**
  (`thread_enabled` or `otbr_running`), not on the stored Thread device
  (`3ab60ef`). Turning Thread off leaves `RADIO_DEVICE` naming the stick, so
  the stored-device rule would have stranded a user freeing a dual-capable
  stick for Zigbee.
- **The reverse lock-out exists too** (`40eff78`). `POST /api/radios`
  refuses the stick the Zigbee setting names as the Thread stick, compared by
  resolved `major:minor`.
- **"Add" is a button, not adopt-on-blur, and only the page that opened a
  join window closes it on leaving** (`3b6cd2c`, `ce24a8a`; recorded in 3.1
  by `b3462d9`).
- **No connect on the startup path** (`3675716`, `9b90c83`). `supervise()`
  performs the first connect in the background; a second, startup-path
  connect would have raced it on one serial port.
- **`--zigbee-device` seeds the stored setting and never overrides it**
  (`4caa260`). The flag can therefore no longer rescue a wrong stored
  setting; see the follow-ups below.
- **The colour command `(768, 7)` is gated on declared features**
  (`e8040f4`, `c08d6bd`). It needs both XY and hue/saturation, because the
  picker reads its position back from hue and saturation; a lamp declaring
  XY alone gets no colour control. Where a lamp has both commands, the web
  UI's picker sends `(768, 6)`; the Loxone export offers both.
- **Two sentinel rules differ from section 10.1's table.** The temperature
  sentinel is `-0x8000`, because the attribute is `int16s` (`b927723`), and
  a `Single` measurement's invalid value is NaN, caught by its own check
  rather than a table row (`9b90c83`).
- **`device_init_failure` arrives on the application's listeners**, not
  the device's (`b5876dc`).
- **zigpy's database is not where section 8.5 puts it.** `cli._run` builds
  it at `<--matter-data-dir>/zigbee.sqlite` (`31f1312`), and
  `deploy/testhost/docker-compose.yml` mounts that directory into the bridge
  read-only (`./data:/matter-data:ro`). Read from the code and the compose
  file, not run: as deployed, zigpy cannot create its database. This has to
  be fixed before the first hardware session.
- **Section 10.3 is out of date on one point.** A second stick, the ITEAD
  SONOFF Zigbee 3.0 USB Dongle Plus V2, has been on the test Pi since
  12 September 2026, beside the MG24 that runs Thread. The loxmatter Zigbee
  code has never opened it.

### 13.4 Still not built

Section 11's list stands unchanged, and open points 6 (every binding and
the IAS `cie_addr` point at the current coordinator's IEEE) and 7 (two
loxmatter instances on one Pi) of section 12 are carried into 2b. Found
during implementation and left for later:

- A `loxmatter zigbee clear` command. Now that `--zigbee-device` only seeds,
  a wrong stored setting behind an unreachable web UI has no console way out.
- The pytest collision between `tests/api` and `tests/projectsync`: both
  have a `conftest.py`, and the test tree has no `__init__.py`, so the two
  directories cannot be collected in one run.
- Comments in `zigbee/source.py` and `zigbee/runtime.py` that still name
  plan task numbers ("Task 8", "Task 10", "Task 11"), which mean nothing
  outside the plan.
