# Commissioning timing on the test Pi (pi@10.0.1.56)

Read-only analysis of `docker logs --timestamps --since 24h` for `matter-server`
(matterjs-server `stable`, matter.js 0.17.9) and `loxmatter`
(`local-6b334a88…`, which is `origin/main` 6b334a8). Window: 2026-10-14 22:37 to
2026-10-15 22:39 UTC. Nothing on the Pi was changed or restarted.

All times are UTC, from matter-server's own log clock (it agrees with the
docker timestamps to within 1 ms).

## Where the data comes from, and what is missing

- **matter-server** logs every commissioning step, every Interaction Model
  exchange over BLE, CASE, interview, and subscription. All phase times below
  come from it.
- **loxmatter** logs **nothing** about commissioning internally: no BlueZ scan
  start or result, no DCL lookup, no queue state, no "commission_with_code
  returned". Only the uvicorn access log (`POST /api/commissioning/scan`,
  `/codes`, `/start`, `/cards/N/force`, `PATCH /cards/N`) is there. The queue
  times below are taken from those requests and from matter-server's
  `Set config key nextNodeId` line, which comes 10–20 ms after each
  `commission_with_code` arrives.
- The `loxmatter` container restarted at 15:14:37, so its log has nothing for
  the 14:19 commissioning.
- Kernel log (`journalctl -k`) has no Bluetooth/HCI messages since 14:00.

## Summary per attempt

| # | Card / disc. | BLE MAC | Node | Path | Start (`nextNodeId`) | End | Total | Result |
|---|---|---|---|---|---|---|---|---|
| A | – / 1 | C7:CF:E1:F5:D8:9E | @1:28 (40) KAJPLATS WS, ColorTemperatureLight | BLE + Thread | 14:19:48.275 | 14:21:32.190 subscribed | **103.9 s** | ok |
| B | card 1 / 8 | C5:8E:E3:1A:0B:F1 | @1:29 (41) ExtendedColorLight | BLE + Thread | 15:19:18.555 | 15:20:59.530 | **101.0 s** | ok |
| C1 | card 2 / 11 | E8:B9:90:AC:AE:6F | – | BLE | 15:20:59.951 | 15:23:59.991 | **180.0 s** | discovery timeout |
| C2 | card 2 / 11 (force) | E8:B9:90:AC:AE:6F | – | BLE | 15:27:06.724 | 15:30:06.750 | **180.0 s** | discovery timeout |
| C3 | card 2 / 11 (force) | F8:25:41:62:97:AB (new MAC) | @1:2c (44) | BLE + Thread | 16:31:24.969 | 16:33:08.089 | **103.1 s** | ok |
| D | card 3 / 11 | EC:A2:BD:73:D2:99 | @1:2d (45) | BLE + Thread | 22:13:14.245 | 22:14:57.999 | **103.7 s** | ok |
| E | card 4 / 8 | – (mDNS, already on Thread) | @1:2e (46) | IP only | 22:14:58.443 | 22:15:03.518 | **5.1 s** | ok |
| F1 | card 5 / 1 | D1:50:2B:0D:6A:5F | – | BLE | 22:15:03.846 | 22:18:03.882 | **180.0 s** | discovery timeout |
| F2 | card 5 / 1 (force) | D1:50:2B:0D:6A:5F | – | BLE | 22:18:35.824 | 22:21:35.866 | **180.0 s** | discovery timeout |
| F3 | card 5 / 1 (force) | F3:BB:39:C4:A1:4B (new MAC) | (@1:31) | BLE + Thread | 22:36:51.892 | 22:38:52.852 | **121.0 s** | BLE link dropped in the Thread step |

Of the 21.0 minutes spent in `commission_with_code` across all ten attempts,
**12 minutes (4 × 180 s) went into four discovery timeouts** that achieved
nothing, and 2 more minutes into F3, which failed near the end.

## Phases of the successful BLE commissionings

Seconds per phase. The phase boundaries are matter-server's `Executing
commissioning step` lines.

| Phase | A @1:28 | B @1:29 | C3 @1:2c | D @1:2d | E @1:2e (IP) |
|---|---|---|---|---|---|
| Discovery → first `Establish PASE` | 0.12 | 0.07 | 0.07 | 0.07 | 0.03 |
| BLE connect → `Matter handshake response` | 3.6 | 4.3 | 4.3 | 4.0 | – |
| PASE (handshake → `Paired successfully`) | 7.9 | 7.7 | 7.7 | 7.9 | 0.25 (total PASE) |
| **Device found + PASE, total** | **11.6** | **12.1** | **12.0** | **11.9** | **0.28** |
| GetInitialData (2 reads) | 8.9 | 8.2 | 8.2 | 8.3 | 0.18 |
| ArmFailsafe | 4.5 | 4.1 | 4.1 | 4.1 | 0.11 |
| Regulatory config | 3.8 | 3.5 | 3.5 | 3.6 | 0.11 |
| **Attestation** (2 × certificateChain + attestationRequest) | **22.2** | **21.2** | **21.4** | **21.8** | **0.34** |
| **Certificates** (CSR, root, NOC) + re-arm failsafe | 20.4 + 2.0 | 19.7 + 2.0 | 19.8 + 2.0 | 19.6 + 2.0 | 0.46 |
| **Thread provisioning**, of which: | **26.4** | **25.8** | **25.5** | **25.9** | skipped |
|   – arm/read before scan | 6.4 | 5.9 | 5.9 | 5.9 | |
|   – `scanNetworks` (device scans the radio) | 7.3 | 7.2 | 7.2 | 7.2 | |
|   – arm + `addOrUpdateThreadNetwork` | 5.5 | 5.4 | 5.5 | 5.4 | |
|   – read + arm + `connectNetwork` | 7.2 | 7.3 | 6.9 | 7.4 | |
| Reconnect: SRP/mDNS resolve + CASE over Thread | 2.2 | 2.2 | 2.6 | 2.4 | 1.6 |
| CommissioningComplete + fabric label | 0.2 | 0.2 | 0.2 | 0.3 | 0.2 |
| **Interview** (bootstrap read) | 1.2 (213 attrs) | 1.6 (265) | 3.3 (265) | 3.3 (265) | 1.4 (265) |
| Subscription | 0.16 | 0.18 | 0.28 | 0.26 | 0.17 |
| **Total** (discovery → subscription) | **103.9** | **101.0** | **103.1** | **103.7** | **5.1** |
| Return to loxmatter → next card's `commission_with_code` | (no queue) | 0.42 | (last card) | 0.44 | 0.33 |

### What the numbers say

1. **The BLE link is the bottleneck, not attestation, DCL, or the network.**
   Every Interaction Model exchange over BLE takes about 2 s (`armFailSafe`
   1.9–2.3 s, short reads 1.7–2.7 s). Exchanges that carry a certificate
   take 6.4–8.8 s (`certificateChainRequest` 7.2–7.5 s each,
   `attestationRequest` 6.6 s, `csrRequest` 6.4 s, `addNoc` 8.7 s). About
   **about 90 of the ~103 s are BLE transport** (PASE start to `connectNetwork` reply is 96.5 s for B, 7.2 s of it the radio scan). The same exchanges over IP in
   attempt E take 0.05–0.15 s each.
2. **Attestation does not wait for the DCL.** The PAA comes from the local
   trust store (181 certificates). No DCL request happens during a
   commissioning; the only DCL traffic is the daily refresh at 15:59:22
   (vendors and certificates, 1.7 s total). The only attestation finding is
   informational and costs nothing:
   `Attestation finding (info): PaaTrustStoreTimeMismatch PAA was fetched at 2026-08-05T09:09:19.783Z which is after DAC notBefore 2025-01-13T08:54:20.000Z; cannot confirm PAA was in DCL when DAC was issued`
3. **Thread provisioning costs about 26 s**, of which 7.2 s is the device's own
   `scanNetworks`. The remaining 19 s is six BLE round trips, three of them
   `armFailSafe` re-arms (breadcrumb 2 with 60 s, 2 with 65 s, 3 and 4 with
   365 s).
4. **Operational discovery is fast:** 1.8–1.9 s from `Resolving (no address
   known)` to the CASE connect (`connect time: 1.86s … unknown:connect`),
   plus about 0.4 s for CASE.
5. **The interview is short:** 1.2–3.3 s for 213–265 attributes.
6. **loxmatter adds well under half a second.** The next queued card's
   `commission_with_code` arrives at matter-server 0.33–0.44 s after the
   previous device's subscription.
7. **A lamp that was removed but not reset re-commissions in 5 s.** @1:29 was
   removed at 22:08:47 (`DELETE /api/devices/…`, `removeFabric`). It stayed
   on the Thread network and advertised itself as commissionable over mDNS.
   Card 4 (discriminator 8) then reached it over IP at
   `fd27:45d7:8c78:1:88a0:eca1:7447:7fcc`, the same address @1:29 had:
   `Skipping NetworkCommissioning steps because the device is already on IP network (udp)`.

## The worst waits: the four 180-second discovery timeouts

All four have the same shape. The BLE connect to the peripheral fails. The
first try fails with a BlueZ D-Bus error, and the forced retry fails because
noble keeps that peripheral object in state `"error"`. matter-server
still waits for the whole 180-s discovery window, and the error only
reaches loxmatter at the end of it. Every time, the device was found
again under a **new BLE MAC address** (C3 and F3), and only after a
later rescan and force.

C1, card 2, 15:20:59.974 → 15:23:59.991:

```
2026-10-15 15:20:59.974 INFO   Discovery            Initiating discovery of node with discriminator 11
2026-10-15 15:21:00.011 INFO   Controller~missioner Establish PASE to device VP: 4476+36870
2026-10-15 15:23:59.991 ERROR  WebSocketC~erHandler [2] WebSocket error response (commission_with_code) 3ede12e7a9224fa1b35992715f9a8c6e Commission failed: discovery of node with discriminator 11 failed: No device could be commissioned (1 of 1 started attempt(s) failed, 1 discovered)
    Cause #0: [ble] Error while connecting to peripheral E8:B9:90:AC:AE:6F
    Caused by: interface not found in proxy object: org.freedesktop.DBus.Properties
```

C2, card 2 force, 15:27:06.733 → 15:30:06.750:

```
2026-10-15 15:30:06.750 ERROR  WebSocketC~erHandler [2] WebSocket error response (commission_with_code) 2eb2d55a48e94827b25d82c2aefebaf2 Commission failed: discovery of node with discriminator 11 failed: No device could be commissioned (1 of 1 started attempt(s) failed, 1 discovered)
    Cause #0: [ble] Can not connect to peripheral "E8:B9:90:AC:AE:6F" because unexpected state "error"
```

F1, card 5, 22:15:03.856 → 22:18:03.882, with the same D-Bus error for
D1:50:2B:0D:6A:5F:

```
2026-10-15 22:18:03.882 ERROR  WebSocketC~erHandler [2] WebSocket error response (commission_with_code) 52652050f6cb44db8b30147d43fc4e84 Commission failed: discovery of node with discriminator 1 failed: No device could be commissioned (1 of 1 started attempt(s) failed, 1 discovered)
    Cause #0: [ble] Error while connecting to peripheral D1:50:2B:0D:6A:5F
    Caused by: interface not found in proxy object: org.freedesktop.DBus.Properties
```

F2, card 5 force, 22:18:35.841 → 22:21:35.866:

```
    Cause #0: [ble] Can not connect to peripheral "D1:50:2B:0D:6A:5F" because unexpected state "error"
```

`interface not found in proxy object: org.freedesktop.DBus.Properties` comes
from noble's `_ensureDeviceProxy`. BlueZ no longer had (or did not yet have)
the device object at that D-Bus path when noble tried to connect. That fits
the BLE address changing: the old path was gone. The timestamps do not show
when inside the 180 s the connect failed, because no line is written at that
moment. In F3 the second `Establish PASE` came 27.7 s after the first, so a
BLE peripheral can take tens of seconds to show up even when everything
works.

## F3: BLE link dropped at the very end of the Thread step

Card 5's third try found the lamp under a new MAC and ran normally for
120 s. Discovery and PASE took 39.1 s, because the second PASE try only
started after 27.7 s:

```
2026-10-15 22:36:51.954 INFO   Controller~missioner Establish PASE to device VP: 4476+36874
2026-10-15 22:37:19.694 INFO   Controller~missioner Establish PASE to device VP: 4476+36874
2026-10-15 22:37:23.241 INFO   BleChannel           Peripheral F3:BB:39:C4:A1:4B: Received Matter handshake response: 656c04110005.
2026-10-15 22:37:31.046 INFO   PaseClient           Paired successfully » ble://F3:BB:39:C4:A1:4B 3↔3
```

Then attestation took 21.5 s and certificates 19.9 s. The Thread dataset had
already been accepted (`addOrUpdateThreadNetwork networkingStatus: 0`). After
that the lamp, or BlueZ, closed the link during the failsafe re-arm just
before `connectNetwork`:

```
2026-10-15 22:38:51.385 INFO   ClientInteraction    Invoke » @0:0•e10f(ble)⇵a8e5 0.generalCommissioning.armFailSafe with breadcrumb: 4 expiryLengthSeconds: 365
2026-10-15 22:38:52.849 INFO   Session              @0:0•e10f Session ended
2026-10-15 22:38:52.852 ERROR  WebSocketC~erHandler [2] WebSocket error response (commission_with_code) 46c9cce4e79c48eab87d5fe1cf7bb357 Commission failed: Operation aborted
  Caused by: [aborted] Operation aborted
  Caused by: [ble-channel-closed] BLE transport closed on @0:0•e10f(ble)
      at Noble._onDisconnect (/app/node_modules/@stoprocent/noble/lib/noble.js:512:18)
      at DbusBindings._onDeviceDisconnected (/app/node_modules/@stoprocent/noble/lib/dbus/bindings.js:571:10)
```

The disconnect came from BlueZ (`_onDeviceDisconnected`). The kernel log has
no HCI error that would explain it. The lamp now holds the Thread dataset with
the failsafe armed for 365 s, which then expires. Whether it went back to BLE
advertising afterwards is not in these logs.

## loxmatter side (from the access log)

| Time | Request | Meaning |
|---|---|---|
| 15:16:10.575, 15:18:10.421 | `POST /api/commissioning/scan` 202 | bridge BLE scan (`SCAN_SECONDS = 10.0` in `commissioning/session.py`); duration and result not logged |
| 15:18:35.722, 15:19:00.792 | `POST /api/commissioning/codes` 201 ×2 | two codes entered (human time) |
| 15:19:18.554 | `POST /api/commissioning/start` 202 | queue started; matter-server begins 18 ms later |
| 15:27:06.724, 16:31:24.970 | `POST /cards/2/force` 202 | manual retries of card 2 (C2, C3) |
| 15:36:35.597 | `POST /scan` 202 | rescan between C2 and C3 |
| 22:08:39–22:08:52 | `DELETE /api/devices/25, 27, 26` | three devices removed (removeFabric on @1:28, @1:2c, @1:29) |
| 22:12:11.555 | `POST /scan` 202 | |
| 22:12:54 – 22:13:09 | `POST /codes` 201 ×3 | three codes |
| 22:13:14.243 | `POST /start` 202 | queue: D → E → F1 back to back |
| 22:18:35.826 | `POST /cards/5/force` 202 | F2 |
| 22:19:56.799 | `POST /scan` **409 Conflict** | scan refused while a commissioning runs |
| 22:36:48.254 | `POST /scan` 202 | |
| 22:36:51.893 | `POST /cards/5/force` 202 | F3 |
| 22:36:58.870, 22:37:03.130 | `POST /api/commissioning/clear` 200 ×2 | clear pressed while F3 was running |
| 22:37:07.774 | `POST /scan` **409 Conflict** | |

## Side observations, not commissioning

- @1:28 lost its subscription 5 minutes after commissioning
  (`Subscription 0b554e27 to peer @1:28 timed out after 1m 38s`, 14:26:31).
  It was marked unavailable at 14:29:31 and came back only at 22:08:24, when
  the lamp itself resumed the CASE session.
- @1:17 (a Wi-Fi device at `fe80::feb4:67ff:fe35:48a4%wlan0`) reconnects
  every hour and drops after about 38 s every time:
  `Failed to establish subscription to @1:17, retry in 1h: [peer-unresponsive] Peer is no longer responding to active session (timed out after 38.1s)`.

## What would shorten commissioning, ranked by time saved

1. **Do not wait 180 s for a BLE peripheral that has already failed.** It
   cost 12 of the 21.0 minutes. When matter-server reports `1 of 1 started
   attempt(s) failed`, the failure is already final. loxmatter could put a
   tighter deadline on the BLE phase, or rescan right away so the device's
   new address is found.
2. **The ~90 s of BLE transport per device is a fixed cost** of the
   adapter, the BLE stack and the lamp. It is about 2 s per exchange. Only
   fewer exchanges could reduce it, and those are inside matter.js (for
   example, the three extra failsafe re-arms in the Thread step).
3. **Re-adding a lamp that is still on Thread** goes over IP in 5 s instead of
   100 s, as attempt E shows.

## Addendum: why each BLE exchange takes about 2 s

Two facts from the Pi, both read-only:

- **The Bluetooth controller is the Pi 4's onboard CYW43455 on the UART**
  (`hci0 -> …/fe201000.serial/…/serial0-0/bluetooth/hci0`, no USB Bluetooth
  adapter in `lsusb`). The same chip runs Wi-Fi, and **the Pi is on Wi-Fi
  only** (`eth0 DOWN`, `wlan0 UP 10.0.1.56/23`). On this chip Bluetooth and
  Wi-Fi share one antenna and take turns on the air, and the Wi-Fi link is
  busy the whole time (matter-server, the WebUI polling `/api/commissioning`
  once a second, the Miniserver).
- **The BTP link runs with tiny segments.** Every handshake response reads
  `Received Matter handshake response: 656c04110005`. By the BTP layout that is
  version `04`, selected segment size `0x0011` = **17 bytes**, window size
  `05`. A 17-byte segment matches an ATT MTU of 20: the MTU was apparently not
  negotiated up (noble with `NOBLE_BINDINGS=dbus`). A certificate of
  about 500 bytes then takes about 30 segments with an acknowledgement
  after every 5, which fits the 6.4–8.8 s for certificate exchanges.
  Phones usually negotiate 185–247 bytes.

Both are hypotheses until measured. They are measurable, though: commission
one lamp with the Pi on Ethernet (Wi-Fi off), and once with a USB Bluetooth
adapter, then compare the handshake response bytes and the per-exchange times.

## Addendum 2: how other setups compare (web research, 2026-10-16)

- **Typical times others report for Thread over BLE:** 45 s (Home Assistant
  with its own Bluetooth, smarthomescene.com); 65 s as the mean of 100 runs on
  Nordic hardware (Nordic DevZone 122005); guides say 30–90 s. Our ~103 s is at
  or above the top of that range. Our 180-s failures are not normal.
- **Segment size pinned to the minimum is a known matter.js bug.** matter.js
  PR #4269 (merged into `main` on 2026-10-06): noble reports the negotiated
  ATT MTU only through a later event, matter.js read the initial value (null)
  and fixed the BTP segment size at the 20-byte minimum for the whole session.
  That matches our handshake response.
- **Stale BLE devices and slow discovery are also fixed upstream, but not
  released.** The "Work in Progress" section of matter.js's CHANGELOG
  (unreleased after 0.17.9 of 2026-08-06, the version the Pi runs) includes:
  "BLE discovery shares one scan, handles timeouts, forgets stale or
  just-commissioned devices", "Commissioning tries addresses in ranked order and
  once each, moves on faster", "Failed BLE connection attempts are retried".
  matterjs-server only has nightly pre-releases with these
  (`v1.5.0-alpha.0-2026101x`); `stable` is still 1.4.0 / matter.js 0.17.9.
- **The Pi's onboard Bluetooth is a known weak point.** In connectedhomeip
  #40675 (Raspberry Pi 4, BlueZ), a maintainer advises a Bluetooth dongle
  rather than the Pi's built-in Bluetooth. Turning Wi-Fi off
  (`nmcli radio wifi off`, from #16178) made the BLE connect reliable there.
  matterjs-server #929 (IKEA GRILLPLATS): the maintainer recommends adapters
  with Bluetooth 4.2 or newer (Data Length Extension), and a TP-Link UB500
  fixed it for several users.
