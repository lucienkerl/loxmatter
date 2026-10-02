# Commissioning many devices: nearby cards, a queue, and a device that blinks

Design, October 2, 2026. Builds on
[commissioning feedback](2026-09-22-commissioning-feedback-design.md)
(phases, the BlueZ reader, failure reasons) and
[pairing code entry](2026-09-07-pairing-code-entry-design.md) (the code
field and its decoder in the browser).

## 1. The problem

Commissioning one Matter device takes the operator's full attention for one
to two minutes: type or scan the code, start, watch a spinner, then find the
new tile and give it a name and a room. Ten lamps are twenty minutes of
waiting. Loxone's own pairing dialog, which Lucien uses daily, lists every
device in pairing mode and learns each with one click; the device then
identifies itself.

Measured on `pi3-andi` on September 22, 2026 (feedback design, section 1):
found in 1 s, 115 of 135 s spent on Bluetooth, Thread and CASE in 4 s. A
code for no device in range costs matter-server's full 180 s discovery
timeout, which loxmatter cannot cancel.

Matter will not do Loxone's one click: the first secure session (PASE) needs
the setup passcode from the device's code, and without it the bridge cannot
send a device anything, not even "identify". What can be done is to take the
waiting off the operator: know what is nearby, collect codes quickly, run the
commissioning in the background, and let each finished device blink while it
is being named.

## 2. Decisions

Taken with Lucien on October 2, 2026, after a clickable prototype
(artifact "loxmatter Einlern-Warteschlange", private to Lucien):

- **The dialog is built around devices nearby** (prototype variant B): every
  device advertising Matter commissioning becomes a card; a scanned code snaps
  onto its card.
- **The bridge scans by itself**: 10 s when the dialog opens and on "Scan
  again", never while commissioning. This revises the feedback design's rule
  "the bridge never starts a scan of its own" (its section 6); the reason for
  that rule, a wedged adapter on September 21, is answered by the lock in 6.2
  and checked on hardware (13.2).
- **Commissioning continues in the background while a device is named.**
  Finished devices without a name queue up for naming; only the first of them
  blinks.
- **Name and room can be set on every card at any time**: before, during and
  after commissioning.
- **Room per card, with a default**: "Room for new cards" at the top; a card
  takes it when its code is scanned and can be changed on the card.
- **Identify has start and stop**, and only one device blinks at a time.
- **The queue lives in the bridge**, not in the browser: closing the dialog or
  the tab does not stop it.
- **Human-readable product names** come from the CSA Distributed Compliance
  Ledger (DCL).

## 3. What already exists, measured

- `radios/bluez.py` reads every `org.bluez.Device1` with Matter service data
  (`fff6`): discriminator (12 bits), vendor id, product id, RSSI. Measured:
  `00 23 04 7c 11 01 90 00` → discriminator 1059, vendor 4476, product 36865.
  It only reads; it never starts a scan.
- `matter/commissioning_progress.py`: `CommissioningTracker` (phases
  `searching` → `found` → `connected` → `joined`, BlueZ sampled every 2 s),
  `classify_failure` (reasons such as `not_found`, `connection_lost`).
- `app.js` `decodePairingCode`: QR → 12-bit ("long") discriminator; 11/21
  digit manual code → 4-bit ("short") discriminator, Verhoeff check. Browser
  only; the bridge has no decoder.
- `api/devices.py` `POST /api/devices/commission`: Thread dataset, tracker,
  `commission_with_code`, `register_device`, room, `follow`.
- Zigbee already blinks a newly joined device for 2 s
  (`zigbee/configure.py`, `_identify_blink`).
- The DCL, queried on October 2, 2026:

  | Request | Answer |
  | --- | --- |
  | `GET /dcl/vendorinfo/vendors/4476` | `vendorName` "IKEA of Sweden" |
  | `GET /dcl/model/models/4476/36865` | `productName` "KAJPLATS E27 WS globe 1055lm", `partNumber` "LED2407G8", `deviceTypeId` 268, `commissioningModeInitialStepsHint` 1 |
  | `GET /dcl/model/models/4476/36870` | "KAJPLATS E14 CWS globe 806lm", "LED2409G6" |
  | `GET /dcl/model/models/4476/32769` | "BILRESA dual button", "E2489", hint 1 |
  | `GET /dcl/model/models/4476/12288` | "MYGGSPRAY wrlss mtn sensor", "E2494", hint 1 |

  Base URL `https://on.dcl.csa-iot.org`. Hint bit 0 means "power cycle".

## 4. The dialog

### 4.1 Entry

The devices page's card "Commission a new device" becomes a button
**"Commission devices"** that opens a dialog with the tabs Matter | Zigbee.
The Zigbee tab is today's Zigbee commissioning, unchanged. One device is
commissioned in the same dialog with one card. While a session has work left
and the dialog is closed, the devices page shows a line "Commissioning in the
background: 2 in progress, 1 waiting for a name" with "Open dialog".

### 4.2 Matter tab

- **Header:** a code field that keeps focus (a handheld scanner types into
  it, Enter adds), "Room for new cards", "Scan again", and a line with the scan
  state ("Scanning over Bluetooth (10 s) …", "4 devices in pairing mode
  found", or "Scanning is paused while a device is commissioned").
- **Cards**, sorted: waiting for a name, running, queued, ready, found,
  not nearby/failed, done; within a group by RSSI. Each shows the product name
  and "vendor · part number" from the DCL, the signal strength, a state chip,
  and "Code ✓" once a code is attached.
- **Name and room fields** appear once a code is attached and stay editable
  in every later state. A change after commissioning is written to the device
  at once (the same writes as `PATCH /api/devices/{id}`).
- **Footer:** "Commission ready devices (n)" and a count line.

### 4.3 Card states

| State | Meaning | Actions |
| --- | --- | --- |
| `found` | Seen by the scan, no code yet | — |
| `ready` | Code attached, not started | Remove |
| `queued` | In the queue, shows its position | — |
| `running` | Being commissioned; phase and progress from the tracker | — |
| `naming` | Commissioned without a name; waiting in the naming line | Confirm name, Continue without a name, Identify |
| `done` | Commissioned and named (or skipped) | Identify |
| `not_nearby` | Code matches no advertising device | Scan again, Try anyway, Remove |
| `failed` | Commissioning failed; reason from `classify_failure` | Scan again, Try anyway |

### 4.4 After commissioning

- **No name yet:** the card goes to `naming` and joins the naming line. The
  first card of the line blinks (section 9) and its name field takes focus;
  Enter confirms, the blink stops, and the next card in the line starts
  blinking.
- **Name entered beforehand:** the card goes to `done` and blinks for 3 s as a
  check, but only if no other device is blinking; it never joins the line.
- **"Continue without a name"** keeps the default label (vendor and product,
  as today) and ends the card's turn.

## 5. Decoding the pairing code in the bridge

`src/loxmatter/matter/setup_payload.py`, pure functions.

- `MT:` QR payload: base38, then the bit fields version (3), vendor id (16),
  product id (16), custom flow (2), discovery capabilities (8), discriminator
  (12), passcode (27). Discovery capabilities: bit 1 BLE, bit 2 on-network.
- Manual code, 11 digits: short discriminator = the top 4 bits of the 12-bit
  discriminator; 21 digits additionally carry vendor and product id. Verhoeff
  check as in the browser.
- Result: `SetupPayload(kind: "long" | "short", discriminator: int,
  vendor_id: int | None, product_id: int | None, ble: bool | None,
  on_network: bool | None)`. The passcode is decoded only as far as
  validation needs and is not part of the result.
- The code string is never logged, never stored on disk, and never part of
  an API response.

## 6. The bridge's own scan

### 6.1 Scanning

`radios/bluez.py` gains `scan(seconds: float = 10.0)`: `SetDiscoveryFilter`
(transport `le`, UUID `fff6`), `StartDiscovery`, wait, `StopDiscovery`, then
one `GetManagedObjects` for the result. The dialog triggers it when it opens,
unless a scan ran in the last 60 s or something is commissioning, and on
"Scan again".

### 6.2 The lock

One `asyncio.Lock` shared by the scan and the queue worker: a scan never runs
while a device is commissioned, and a commissioning attempt waits for a
running scan to end. `POST /api/commissioning/scan` while the worker holds the
lock answers 409.

### 6.3 Adapter health

After every scan the bridge reads the kernel-log categories of the feedback
design (its section 7.1). A wedge line since the scan started sets
`bluetooth_warning` in the session, and the dialog shows the existing
Bluetooth warning text.

## 7. Names from the DCL

`src/loxmatter/matter/dcl.py`:

- `vendor(vendor_id)` and `model(vendor_id, product_id)`, each one HTTPS
  request, 5 s timeout. The fields kept: vendor name; product name, part
  number, device type id, `commissioningModeInitialStepsHint`,
  `commissioningModeInitialStepsInstruction`.
- **Cache in the store**, schema 17, additive: tables `dcl_vendor` and
  `dcl_model`. A found entry is kept for good; "not in the DCL" is kept for
  7 days and then asked again. A network failure is not cached.
- **Test vendor ids** 0xFFF1–0xFFF4 are never queried; they read "Test device
  (vendor 0xFFF1)". Tasmota uses 0xFFF1.
- **Without an answer** a card shows "Vendor 0x117C · Product 0x9001".
- **Where the ids come from:** the QR code at scan time, the Bluetooth
  advertisement, and after commissioning the device's Basic Information.
- **The "pairing mode" hint:** bit 0 of the initial-steps hint gives "Switch
  the power off and on again"; a non-empty instruction text from the DCL is
  shown as is. Other bits get "See the manufacturer's instructions".

## 8. The commissioning session

`src/loxmatter/commissioning/session.py`, in memory only.

### 8.1 Content

Cards (from scans and from codes), each with an id, the advert identity
(BlueZ address) if any, the decoded payload, the code (memory only), name,
room, state, phase, progress, note; the queue (card ids); the naming line
(card ids); the blinking device; the scan state; `bluetooth_warning`.

### 8.2 Attaching a code

| Code | Match rule against the latest scan |
| --- | --- |
| QR (`long`) | advert discriminator equals the code's |
| manual (`short`) | `advert.discriminator >> 8` equals the code's |

- Exactly one unattached card matches → the code attaches to it; the card
  takes the room default and becomes `ready` (or `queued` if the queue is
  running).
- Several match (only possible for a manual code) → a new card "Device with
  numeric code", note "Matches 2 devices nearby". After commissioning the
  bridge knows which device it was, adopts its product name and removes the
  matching `found` card.
- None matches → a new card in `not_nearby` with the pairing-mode hint (7) —
  unless the QR payload says the device is found on the network only (no BLE
  bit), which goes straight to `ready` with the note "Wi-Fi device: searched
  on the network".
- The same code twice → rejected with a message.

### 8.3 The worker

One task works the queue in order. For each card it takes the lock (6.2) and
calls the commissioning function that today lives inside
`POST /api/devices/commission`, moved out to
`src/loxmatter/commissioning/run.py` so the route and the worker share it:
Thread dataset, tracker, `commission_with_code`, `register_device` with the
card's room, `follow`. Then it applies the card's name, if any, and moves the
card on (4.4).

- **Before starting a card** the worker checks the latest scan: a card whose
  device is no longer advertising becomes `not_nearby` and is skipped. "Try
  anyway" queues it without this check.
- **Early warning:** in phase `searching`, if 20 s pass without a BlueZ sample
  matching the card's discriminator, the card gets the note "No matching
  device is advertising yet". The attempt continues.
- **Failure:** the card becomes `failed` with the classified reason; the
  worker goes on with the next card.
- **matter-server disconnected:** the worker does not start a new card and
  resumes when the source is connected again; a running card fails with
  `matter_server_unreachable`.
- **Codes scanned while the worker runs** are queued at the end.

### 8.4 Lifetime

The session ends with the bridge: a restart empties it, codes included, and a
device commissioned before the restart is an ordinary device afterwards. "Clear
list" in the dialog removes every card that is not `running`.

## 9. Identify

### 9.1 Sources

`identify(address: str, seconds: int) -> None` on both sources:

- Matter: `Identify` (cluster 0x0003, command 0, `IdentifyTime`) on every
  endpoint that has the cluster, through the existing command path.
- Zigbee: the Identify cluster's `identify(identify_time)`, as
  `_identify_blink` does today.

`seconds = 0` stops. A device without the cluster raises
`IdentifyUnsupportedError`; the UI hides the button for it.

### 9.2 One at a time

`src/loxmatter/commissioning/identify.py`: `IdentifyCoordinator` with
`start(device_id, renew: bool)` and `stop()`. A start first stops the device
that is blinking (`IdentifyTime` 0). A device in the naming line is started
with 30 s and renewed every 25 s until its name is confirmed, so it stops on
its own within 30 s if the bridge goes away. A start from a tile or a card
runs 30 s without renewal and shows "Stop identifying" while it runs.

### 9.3 Device page

Every tile's kebab menu gets "Identify" (Matter and Zigbee), or "Stop
identifying" while it blinks.

## 10. API

All under `/api`, behind the existing guard. User-visible `detail` texts go
through `i18n.t`.

| Route | Answer |
| --- | --- |
| `GET /commissioning` | The session: cards (never codes), scan state, queue, naming line, blinking device, `bluetooth_warning` |
| `POST /commissioning/scan` | 202; 409 while commissioning |
| `POST /commissioning/codes` `{code, room}` | The card the code attached to or created; 422 for an unreadable code, 409 for a duplicate |
| `PATCH /commissioning/cards/{id}` `{name?, room?}` | The card; written to the device too when it is commissioned |
| `POST /commissioning/start` | 202; queues every `ready` card and starts the worker |
| `POST /commissioning/cards/{id}/confirm-name` | Ends the card's naming turn; 422 without a name |
| `POST /commissioning/cards/{id}/skip-name` | Ends it with the default label |
| `POST /commissioning/cards/{id}/force` | Queues a `not_nearby`/`failed` card without the range check |
| `DELETE /commissioning/cards/{id}` | Removes a card that is not running |
| `POST /commissioning/clear` | Removes every card that is not running |
| `POST /devices/{id}/identify` `{on: bool}` | 204; 409 for a device without Identify |

The dialog polls `GET /commissioning` every second while it is open; the
devices page polls it every 5 s while the session has work and the dialog is
closed. `POST /api/devices/commission` stays, now calling the shared function
of 8.3.

## 11. Strings

New keys under `web.commissioning.*` and `api.commissioning.*`, `en` and
`de`, German in the formal "Sie": card states, scan states, the pairing-mode
hints, the background line, the naming prompt ("This device is blinking.
Which one is it? Enter a name and press Enter."), and the API errors.

## 12. Testing

- **Decoder:** QR and manual codes from the Matter specification and from the
  browser decoder's tests in `tests/api/test_web.py`; a test runs Python and
  browser decoders on the same codes (the node harness pattern the suite
  already uses, skipped without node). A test that a code appears in no log
  record and no API response.
- **Matching:** the measured advertisement (discriminator 1059, IKEA 4476/
  36865) — QR hit, manual code with several hits, no hit, on-network only.
- **Session and worker** with a fake client and an injected clock: queue
  order, scanning while running, the naming line and its blink handover, a
  preset name, a failure followed by the next card, the pause while
  matter-server is away, "Try anyway", the range check before a card.
- **Lock:** a scan never overlaps a commissioning attempt.
- **Identify:** one at a time, renewal every 25 s, stop with 0, unsupported
  devices; Matter and Zigbee.
- **DCL:** the answers of section 3 as fixtures; offline, not found, the
  7-day retry, test vendor ids; migration 16 → 17 additive.
- **API:** every route, the guard, German details, no code in any response.
- **WebUI:** the dialog's bindings run in the browser against a harness, as
  for the firmware updates, not only asserted as delivered HTML.
- New test files get names no other test module has.

## 13. On the test Pi `pi@10.0.1.56`

The Pi runs matterjs-server since September 30, 2026.

1. Whether BlueZ allows the loxmatter container `SetDiscoveryFilter` and
   `StartDiscovery` over the read-only `/run/dbus` mount; the kernel log after
   the scan. **Passed on October 2, 2026, 12:02:** the container runs as
   `uid=0`; `SetDiscoveryFilter` (transport `le`, UUID `fff6`),
   `StartDiscovery` and, 10.0 s later, `StopDiscovery` all returned
   `METHOD_RETURN`; `Discovering` was `False` before and after. No device was
   in pairing mode, so the scan found none. `journalctl -k` showed no
   Bluetooth or HCI line in the 10 minutes around it; `hciconfig` reports
   `hci0` UP RUNNING with 0 errors. The read-only mount of the socket
   directory does not block method calls.
   **Repeated with the branch's `BluezScanner` on October 2, 2026, evening:**
   one D-Bus connection for filter, start, read and stop (BlueZ ends a
   discovery when the connection that started it closes, and clears every
   RSSI when it stops — so the objects are read before `StopDiscovery`).
   `Discovering` read `False` before, `True` 3 s into a 6 s scan, `False`
   after; no Bluetooth line in `journalctl -k`, `hciconfig` 0 errors. No
   device was in pairing mode, so the snapshot was empty.
2. Scan and commissioning alternating three times; the adapter must not wedge.
3. The DCL is reachable from the container.
4. Identify on a KAJPLATS lamp and on a Zigbee device; stop with 0; only one
   at a time; whether BILRESA and the sensors show anything.
5. A real queue of three devices that Lucien factory-resets for it: time per
   device and the operator's waiting time overall. This changes real devices
   and needs Lucien's go-ahead.

Open until then: whether 10 s of scanning finds every device in pairing mode.

## 14. Not in this design

- Commissioning several devices at the same time: one Bluetooth adapter does
  one at a time, and a second client wedged it on September 21.
- Scanning the QR code with a phone camera in the WebUI: browsers allow the
  camera only over HTTPS.
- Changes to the Zigbee tab, apart from Identify on Zigbee tiles.
