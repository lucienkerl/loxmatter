# Firmware updates for commissioned devices, stage 1: Matter

Design, September 30, 2026. Builds on
[the device source boundary](2026-09-11-device-source-boundary-design.md)
(the `DeviceSource` protocol this extends) and borrows the step display of
[applying updates through the web UI](2026-09-08-webui-updates-design.md).

## 1. The problem

loxmatter shows the firmware a device reported when it was commissioned
(`device.firmware`, in the expert area) and does nothing else with it. An
operator cannot see which devices are out of date, and cannot update one
without the manufacturer's app — which, for a device living only in the
loxmatter fabric, often cannot reach it at all.

## 2. Decisions

Taken with Lucien on September 30, 2026:

- **Matter first, Zigbee in a later stage.** Zigbee gets the same UI on top
  of zigpy's OTA later (section 11). This document covers Matter only.
- **The bridge checks for updates once a day on its own, and never installs
  on its own.** Installing always takes a click and a confirmation, per
  device. The daily check can be switched off in Settings; it is on by
  default.
- **"Check for updates now" exists** — once in the overview card for all
  devices, once in the device dialog for one.
- **No file upload in this stage.** Installing a local `.ota` image
  (`upload_ota_file`, schema 13) is stage 3, if at all.
- **The test Pi is `pi@10.0.1.56`.**
- **The Matter version a device implements is shown** in the update
  overview and in the device's expert area (section 9.3).
- **Built and first tested against the old server, verified against
  matterjs-server before release.** The test Pi keeps the old
  `python-matter-server` for now; moving it is its own piece of work.
  Section 10.3 is a release gate.

## 3. What already exists, measured

`matter-python-client` 1.4.0 (installed) offers:

- `check_node_update(node_id) -> MatterSoftwareVersion | None` — asks the
  CSA Distributed Compliance Ledger (DCL) for a newer image. The result
  carries `software_version` (int), `software_version_string`,
  `min_applicable_software_version`, `max_applicable_software_version`,
  `release_notes_url`, and `update_source` (`main-net-dcl`, `test-net-dcl`,
  `local`). Requires server schema 10.
- `update_node(node_id, software_version)` — starts the transfer; the
  matter-server acts as OTA provider. Requires schema 10.

The device side, in the attributes loxmatter already caches:

| Path | Meaning |
| --- | --- |
| `0/40/9`, `0/40/10` | Basic Information: `SoftwareVersion` (int), `SoftwareVersionString` |
| `0/40/21` | Basic Information: `SpecificationVersion`, the Matter version the device implements. Added in Matter 1.3 |
| `0/42/*` | OTA Software Update Requestor. Its presence means the device can be updated over Matter at all |
| `0/42/2` | `UpdateState`: 0 Unknown, 1 Idle, 2 Querying, 3 DelayedOnQuery, 4 Downloading, 5 Applying, 6 DelayedOnApply, 7 RollingBack, 8 DelayedOnUserConsent |
| `0/42/3` | `UpdateStateProgress`, percent or null |

`discovery.py` filters endpoint 0's management clusters out of the signal
list; that stays so. This feature reads them directly.

**Measured on `pi@10.0.1.56` on September 30, 2026**, read-only, nothing
installed. That Pi still runs the **old** `python-matter-server` image
(sdk 2025.7.0, schema 11), not matterjs-server. It answers
`check_node_update` anyway:

| Node | Device | Installed | DCL offers |
| --- | --- | --- | --- |
| 4 | IKEA BILRESA dual button | 1.8.5 | 1.9.15 |
| 11 | IKEA MYGGBETT door/window sensor | 1.1.4 | `None` |
| 12 | IKEA ALPSTUGA air quality monitor | 1.0.15 | 1.0.26 |
| 13 | IKEA MYGGSPRAY motion sensor | 1.0.7 | 1.1.4 |
| 15 | IKEA TIMMERFLOTTE temp/humidity | 1.0.21 | `None` |
| 16 | IKEA KLIPPBOK water leak sensor | 1.0.11 | 1.0.13 |
| 21 | IKEA KAJPLATS E14 globe | 1.2.0 | `None` |
| 22 | IKEA KAJPLATS E14 globe | 1.1.0 | 1.2.0 |
| 26 | IKEA KAJPLATS E27 G60 | 1.1.0 | `None` |
| 23, 24 | Tasmota plugs | 13.3.0 / 15.6.0 | no `0/42` cluster, not asked |

The whole check of nine nodes took seconds. Every IKEA device carries
`0/42`; neither Tasmota plug does.

Not measured, and open until the first real install (section 10):

- whether the old server image actually contains a working OTA provider,
  or only answers the DCL query;
- whether `update_node` returns at once or only after the transfer;
- how often a device reports `0/42/3` while downloading.

## 4. What `None` means

`check_node_update` returning `None` says "the DCL has no newer image for
this vendor/product that applies to this version". It cannot tell a device
that is current from one whose manufacturer publishes nothing. The UI
therefore says **"No update found"**, never "Up to date".

## 5. States

One state per device, derived as follows:

| State | When |
| --- | --- |
| `no_source` | The device has no `0/42/*` attribute. Not checked, never installable |
| `unchecked` | Has `0/42`, never checked |
| `none_found` | Last check returned `None` |
| `available` | Last check returned an offer newer than the installed `0/40/9` |
| `check_failed` | Last check raised (no internet, DCL error, timeout); the previous offer, if any, is kept and shown |
| `transferring` | An install runs, `UpdateState` is 2–4 or 8; carries the percent when known |
| `applying` | `UpdateState` is 5 or 6 |
| `failed` | The install ended without the new version (see 7.3) |
| `stalled` | No state or progress change for 15 minutes |
| `interrupted` | matter-server disconnected during an install |

A device that is offline is skipped by the check and keeps its last state;
the UI adds "unreachable" beside it and blocks installing.

An offer is dropped once `0/40/9` reaches or passes its version — that covers
an update applied from another fabric (Apple Home, Google Home) as well.

## 6. Checking

### 6.1 One code path

`firmware/check.py` holds `check_all()` and `check_one(device_id)`. The
daily run, the overview button, and the dialog button all call these.

- Devices are checked one after another, each bounded by 60 s.
- Only one `check_all()` runs at a time. A second request while one runs
  returns the running one's progress (`checked`, `total`) instead of
  starting another. This covers a double click and a second browser.
- Results go to the store as they arrive, so the overview fills in while
  the run is still going.

### 6.2 The daily run

- At 06:00 in the bridge's local time, when enabled.
- The switch lives in the existing `setting` table under
  `firmware.daily_check_enabled`, read and written through a small
  `FirmwareSettingsStore` following `update_settings_store.py`. Missing
  means enabled.
- The scheduler takes its clock as a parameter so tests can drive it.
- A run missed because the bridge was down is not caught up; the next one
  is tomorrow at 06:00. The overview shows when the last check ran.

## 7. Installing

### 7.1 Starting

`POST /api/devices/{id}/firmware/update` with body
`{"software_version": <int>}`.

- The version must equal the stored offer for that device, otherwise 409.
  This stops a stale browser tab from installing something no longer
  offered.
- A process-wide lock allows one install at a time on the Matter source.
  A second request gets 409 naming the device that is updating.
- The device must be online, otherwise 409.
- The route calls `update_node` in a background task and answers 202 at
  once, whether or not `update_node` blocks.

### 7.2 Following

The upstream client keeps every node's attributes current in its cache
(matter-server subscribes to all of them). The install job therefore reads
`0/42/2`, `0/42/3`, and `0/40/9` from that cache every 2 s. No extra
subscription is needed. If nothing has changed for 30 s, it reads them from
the device once with `read_attribute`, in case a device does not report
progress on its own.

The job writes state and percent to the store. The UI polls
`GET /api/firmware` every 5 s while any install or check runs, and every
60 s otherwise. The live WebSocket stays for signal values only.

### 7.3 Ending

- **Success**: `0/40/9` equals the offered `software_version`. Then
  `device.firmware` is updated to `0/40/10` (today it is only filled on
  commissioning and backfilled when empty), the offer is dropped, and the
  device's structure is re-read through the existing `follow()` path. If
  the signals changed, the "Changed since export" band appears as it does
  today — an update can renumber endpoints (the Tasmota plug did, 13.3.0 →
  15.6.0).
- **Failed**: `UpdateState` returns to Idle and stays there for 2 minutes
  without `0/40/9` changing, after the job had seen it leave Idle. Also
  when `update_node` raises.
- **Stalled**: no change in state or percent for 15 minutes. The job keeps
  watching; a later change moves it back to `transferring`.
- **Given up**: 3 hours after the start the job stops and records `failed`.

The 15-minute and 3-hour bounds are guesses until section 10 measures a
real transfer.

### 7.4 Restarts

The transfer runs between matter-server and the device, not through
loxmatter. A restart of **loxmatter** therefore does not interrupt it. On
start, loxmatter looks at `0/42/2` of every device: any device not Idle gets
a job again and its state is shown. Its offer comes from the store.

A restart of **matter-server** does interrupt it. The job marks the device
`interrupted` when the source reports the link lost.

## 8. Components

- **`DeviceSource` stays as it is.** A separate protocol `FirmwareSource`
  in `sources/__init__.py` declares `check_update(address)` and
  `start_update(address, software_version)` plus `firmware_attributes(address)`
  for the cached `0/40/9`, `0/40/10`, `0/42/2`, `0/42/3`, and whether `0/42`
  exists. `BridgeMatterClient` implements it; the Zigbee source joins in
  stage 2.
- **Schema check.** `BridgeMatterClient` reports the capability as missing
  when the server's `schema_version` is below 10; the API then answers 409
  and the UI names the reason.
- **`loxmatter/firmware/`**: `check.py` (6.1), `schedule.py` (6.2), `job.py`
  (7), `states.py` (the derivation in 5, pure functions).
- **Store**: a new table, additive migration to schema 15:

  ```sql
  CREATE TABLE firmware_status (
      device_id            INTEGER PRIMARY KEY REFERENCES device(id) ON DELETE CASCADE,
      checked_at           TEXT,
      check_error          TEXT,
      offer_version        INTEGER,
      offer_version_string TEXT,
      offer_min_applicable INTEGER,
      offer_max_applicable INTEGER,
      offer_notes_url      TEXT,
      offer_source         TEXT,
      job_state            TEXT,
      job_progress         INTEGER,
      job_started_at       TEXT,
      job_changed_at       TEXT,
      job_error            TEXT
  );
  ```

  No column is ever dropped (the updater's rollback runs without a
  database restore).

## 9. API and UI

### 9.1 Routes

All under `/api`, behind the existing guard.

| Route | Answer |
| --- | --- |
| `GET /firmware` | Every device with state, installed version, offer, last check; plus the running check's `checked`/`total`, the daily switch, and whether the capability is available |
| `POST /firmware/check` | 202; starts `check_all()` or returns the running one |
| `POST /devices/{id}/firmware/check` | 200 with the device's new state |
| `POST /devices/{id}/firmware/update` | 202; 409 for offer mismatch, another install running, device offline, capability missing; 404 for an unknown device |
| `PUT /firmware/settings` | `{"daily_check_enabled": bool}` |

Every `detail` a user can see goes through `i18n.t(...)`.

### 9.2 Screens

The drafts were shown to Lucien as a private web page during the design
session; the text below is what counts. In words:

- **Device tile.** With an offer, a copper band under the footer reads
  "Update available: 4.1.3 → 4.2.0", in the place of "Changed since
  export" (copper, not the warning yellow: it is an offer, not a problem).
  During an install it reads "Updating · 43 %". Clicking it opens the
  dialog.
- **Kebab menu.** A new item "Software update …" above "Export", for every
  device with `0/42`.
- **Dialog, before.** Installed version, offered version, source, last
  check, release-notes link when the DCL has one. A warning box with three
  sentences: the transfer takes a while and the device stays usable; the
  device restarts at the end and Loxone gets no values for about a minute;
  if its signals change, re-export the Loxone template. Buttons "Check
  again", "Cancel", "Install update".
- **Dialog, during.** The step list of the bridge update: image fetched,
  device accepted, transfer with a progress bar and percent, device
  restarts, new version confirmed. It can be closed; the install goes on.
- **System → "Device updates" card.** Last check time, a "Check for updates
  now" button (with "Checking 4 of 11 …" while running), filter chips
  (all / update available / no source), a table of device, installed,
  offered, state, and an "Install" button per row. Install buttons are
  disabled while another install runs. The daily-check switch sits at the
  bottom. "No update source" carries a hint: the manufacturer publishes
  nothing over Matter; use its app or the device's own web page.

### 9.3 The Matter version

Lucien asked for it during the design; it is small and read from the same
cluster, so it joins stage 1.

`SpecificationVersion` (`0/40/21`) is encoded as `0xMMmmPP00`: major, minor,
patch, and a reserved low byte. Shown as `1.3` when the patch is 0, else
`1.4.1`. The attribute only exists since Matter 1.3, so a device without it
implements 1.2 or older; shown as "1.2 or older", never as a guess at the
exact version. `DataModelRevision` (`0/40/0`) is not used to narrow that
down: its mapping to spec versions is not something this project has
verified.

Measured on `pi@10.0.1.56`, September 30, 2026:

| Node | Device | `0/40/21` | Shown |
| --- | --- | --- | --- |
| 4, 12, 13, 15, 16 | IKEA BILRESA, ALPSTUGA, MYGGSPRAY, TIMMERFLOTTE, KLIPPBOK | `0x01030000` | 1.3 |
| 8, 11, 21, 22, 26 | IKEA GRILLPLATS, MYGGBETT, KAJPLATS (all three) | `0x01040000` | 1.4 |
| 24 | Tasmota 15.6.0 | `0x01040100` | 1.4.1 |
| 23 | Tasmota 13.3.0 | absent | 1.2 or older |

Node 22 on firmware 1.1.0 already reports 1.4; the version does not
follow the firmware number. It can change with an update, though, so it is
refreshed together with `device.firmware` (7.3).

- **Store**: a new nullable column `device.matter_spec_version INTEGER`,
  added in the same migration to schema 15. Filled on commissioning,
  backfilled for existing devices from the cached attributes at startup
  (like `firmware` today), refreshed after a successful update. NULL for
  Zigbee devices and for Matter devices without the attribute; the
  difference comes from `technology`.
- **Formatting** lives in one pure function
  `format_spec_version(raw: int | None, technology) -> str`, used by both
  routes, so the overview and the expert area cannot disagree.
- **Update overview**: a column "Matter" between device and installed
  version. `–` for Zigbee devices.
- **Expert area**: a row "Matter version" directly below the firmware row
  (`web.devices.expert_matter_version`). Not shown for Zigbee devices.

## 10. Testing

### 10.1 Automated

- **Fixtures from the measurement in section 3**, verbatim: the five
  offers, the `None` answers, the Tasmota nodes without `0/42`. The
  `UpdateState` sequence starts as the spec's Idle → Querying →
  Downloading → Applying → Idle and is **replaced by the recorded sequence**
  after the first real install.
- `states.py`: every row of the table in 5.
- `format_spec_version`: the four values measured in 9.3, plus a patch
  version and a Zigbee device.
- Checking: all, one, per-device timeout, offline device skipped, DCL error
  keeps the previous offer, second `check_all()` joins the first.
- Installing: success only on the new `0/40/9`; failed; stalled and back;
  given up; interrupted on link loss; the lock; the offer-mismatch 409;
  resuming after a loxmatter restart from a non-Idle `0/42/2`.
- Scheduler with an injected clock; the switch.
- Migration 14 → 15 is additive.
- API answers and their `en`/`de` strings.
- WebUI: the dialog, the progress, and the card run in the browser harness,
  not only asserted as delivered HTML.
- New test files get names no other test module has. Before merging:
  `--collect-only` over everything, then the suite in two halves.

### 10.2 On `pi@10.0.1.56`

1. Pull the fabric backup (`GET /api/diagnostics/fabric-backup`). Note node
   22's version, its signals, and that Loxone receives its values.
2. Build the branch image on the Pi and restart only loxmatter
   (`docker compose up -d --no-deps loxmatter`), so matter-server stays
   the old image and keeps its nodes.
3. "Check for updates now" in the WebUI. The result must match section 3.
4. Install on **node 22** (KAJPLATS E14, 1.1.0 → 1.2.0), a mains-powered
   Thread router. Node 21 is the same model already on 1.2.0, for
   comparison. Record every `0/42/*` and `0/40/9` change with a timestamp.
   This answers the open questions in 3: whether the old image has a
   provider, whether `update_node` blocks, the real state sequence, the
   duration, and how often progress is reported.
5. Afterwards: node 22 reports 1.2.0, its signals are unchanged, Loxone
   still receives. The overview and the expert area show the Matter
   versions of 9.3.
6. Restart loxmatter during a second install (BILRESA or ALPSTUGA). The UI
   must show the running install again after the restart.
7. Write the measurements into this document, adjust the bounds in 7.3,
   and replace the fixture sequence.

IKEA firmware cannot be rolled back. A battery device (MYGGSPRAY) is the
slow edge case and is tried only after the lamp works.

### 10.3 Release gate: matterjs-server

Section 10.2 runs against the old `python-matter-server`, because that is
what `pi@10.0.1.56` runs. The Compose file has named
`ghcr.io/matter-js/matterjs-server:stable` since September 8, 2026, so
**every new installation gets matterjs-server**. A pass on the old server
proves the old path only.

Why the test Pi is not on matterjs-server: on September 23, 2026 a
`docker compose up -d loxmatter` pulled matterjs-server in, which started
with zero nodes because it does not read the old server's storage. The old
image was brought back by hand with `docker run` (created 2026-09-23
05:22 UTC, no Compose label). Moving that Pi means re-commissioning every
device or migrating the fabric; that is a separate design, not part of this
one.

**The feature is not released until the following has passed against a
matterjs-server with at least one commissioned device that has a DCL
update**, and its results are written into this section:

1. The server's `schema_version` is at least 10, so the capability is
   reported as available.
2. `check_node_update` returns the same kind of answer as in section 3:
   an offer for an outdated device, `None` for a current one. Field names
   and the `update_source` values match `MatterSoftwareVersion`.
3. One install runs through: `update_node` is accepted, `0/42/2` and
   `0/42/3` move as recorded in 10.2 (or the differences are recorded and
   handled), and `0/40/9` reaches the offered version.
4. The Matter version from `0/40/21` shows the same as on the old server.

Where to run it is open: a second test installation, or the test Pi after
its own move to matterjs-server. Until this section holds results, the
changelog entry stays under "Unreleased" and no release is cut with it.

## 11. Later stages

- **Stage 2, Zigbee.** zigpy's OTA stays configured without broadcasts
  (`broadcast_enabled: False`), so the "no silent updates" promise of the
  Zigbee design (8.5, G9) holds: zigpy answers a device's query, never
  starts one. Offers from `application.ota.get_ota_images()`, install via
  `device.update_firmware(image, progress_callback)`. Needs the OTA client
  cluster 0x0019 and a prior Query Next Image from the device. One install
  at a time per Zigbee network, separate from the Matter lock.
- **Stage 3, own files.** Only after a decision whether it is wanted at
  all: a wrong image can brick a device.
