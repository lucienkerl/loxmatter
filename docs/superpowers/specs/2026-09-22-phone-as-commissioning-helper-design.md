# The phone brings a device into the Pi's fabric

Date: 22 September 2026. A design and a test plan, not an implementation.
Concerns the commissioning card, `POST /api/devices/commission`, and a phone
app that does not exist yet.

## 1. The problem

Today the Pi commissions every device itself, through its own Bluetooth
adapter (`matterjs-server`, BLE over D-Bus, see `deploy/testhost/docker-compose.yml`).
Two things follow from that:

- **Range.** BLE reaches a few metres, through one wall at best. A device
  that is built in — a flush-mounted switch, a ceiling light — cannot be
  carried to the Pi, and the Pi usually sits in a cabinet somewhere else.
- **The QR code.** The user types the numeric code from the sticker, or
  copies the `MT:` text out of some other scanner. The person standing next
  to the device holds a phone with a camera and a Bluetooth radio; the Pi has
  neither near the device.

## 2. Goal

- A user standing next to a device commissions it with their phone: scan the
  QR code, wait, done.
- Afterwards the device is in **the Pi's fabric only**, and — for a Thread
  device — in **the Pi's Thread network only**. Not in Apple Home, not in
  Google Home, not in any fabric of the phone.
- Nothing about the Pi's commissioning path changes for users who do not use
  the phone.

## 3. What does not work, and why

**Commissioning with Apple Home or Google Home and sharing the device
afterwards (multi-admin).** Works for Wi-Fi devices, but a Thread device then
lives in Apple's or Google's Thread network, not the Pi's. That contradicts
the goal.

**The phone as a Bluetooth relay for the Pi.** The phone would tunnel Matter's
BLE transport (BTP) over a WebSocket to loxmatter, and `matterjs-server`
would use that tunnel instead of its own adapter:

```
device ⇄ BLE ⇄ phone ⇄ WebSocket ⇄ loxmatter ⇄ matterjs-server
```

It keeps every secret on the Pi, which is attractive. It is rejected for now
for three reasons:

- `matterjs-server` has no way to take a BLE transport from outside. matter.js
  has an exchangeable BLE layer internally (it also runs on React Native), but
  using it means building and maintaining our own server image, or getting a
  feature merged upstream. Not verified in detail; this is the state of
  knowledge at the time of writing.
- In a browser, Web Bluetooth exists only in Chromium on Android, and only in
  a secure context. The web UI is served over plain HTTP in the LAN. iOS
  Safari has no Web Bluetooth at all, so iOS needs a native app regardless.
- BTP has acknowledgement timeouts. A tunnel through Wi-Fi, a Python process
  and a WebSocket adds latency and a second failure mode to the most fragile
  step of the whole flow.

If `matterjs-server` ever gains a pluggable BLE transport, this option should
be reconsidered.

## 4. The design: the phone commissions briefly, then hands over

Every step below is a standard Matter mechanism. `matterjs-server` is not
changed. Google Play Services does the same thing for third-party apps; the
difference here is whose network the device joins.

1. **Scan.** The app scans the QR code (or takes the numeric code) and
   connects to the device over BLE.
2. **Fetch the network.** The app asks loxmatter for the Pi's active Thread
   dataset (section 5.2). For a Wi-Fi device it asks the user for SSID and
   password instead, or reuses what it stored for this bridge.
3. **Commission into a throwaway fabric.** The app is a full commissioner
   with its own fabric, created fresh for this run: PASE over BLE, attestation,
   NOC, network configuration with the Pi's dataset, CASE over IP,
   `CommissioningComplete`. It also sets the fabric label to
   `loxmatter-handover` (`UpdateFabricLabel`). From here on the device is in
   the Pi's Thread network.
4. **Open a window.** Over the same CASE session, the app opens an Enhanced
   Commissioning Window (`OpenCommissioningWindow`, a few minutes long) and
   derives the pairing code for it.
5. **Hand over.** The app sends that code to `POST /api/devices/commission`,
   together with a room if the user chose one. The Pi commissions the device
   over IP, exactly as it does for a multi-admin code today
   (`commission_with_code` in `matter/client.py`). The Pi runs its own
   attestation check here, so the app's check in step 3 is not the one the
   bridge relies on.
6. **Leave.** The app removes its own fabric from the device (`RemoveFabric`
   with its own fabric index) and discards the fabric's keys.

Afterwards the device carries one fabric, the Pi's, plus whatever fabrics it
already had before step 1.

### 4.1 If the app does not get to step 6

The app crashes, the phone loses Wi-Fi, the user closes it. The device then
keeps the throwaway fabric forever, and devices only have room for a handful
of fabrics (at least five, by the specification).

Therefore loxmatter cleans up after itself: after every successful
commissioning it reads the device's `Fabrics` attribute (Operational
Credentials cluster) and removes every fabric **labelled exactly
`loxmatter-handover`** that is not its own. It never touches a fabric with any
other label — a device that is also in Apple Home is a legitimate
multi-admin setup, not garbage.

This also covers a throwaway fabric left behind by an earlier, failed run.

## 5. What has to be built

### 5.1 The app

A native app, Android and iOS. It does steps 1–6 and nothing else: no device
list, no controls, no settings beyond the bridge's address and login.

Technology is open and will be decided after the test in section 7. The
candidates are matter.js under React Native (same Matter stack as the Pi,
one code base for both platforms) and the CHIP SDK's platform controllers.
Whichever it is has to support: BLE commissioning with a caller-supplied
Thread dataset, `UpdateFabricLabel`, `OpenCommissioningWindow`, and
`RemoveFabric`.

The app talks to loxmatter through the existing API and logs in with the
existing password (`api/auth.py`), so it holds the same session cookie the
browser does.

### 5.2 Handing out the Thread dataset

This is the one genuinely new thing on the bridge side, and the sensitive
one: the dataset contains the Thread network key. Until now it has never left
the Pi — `matter/otbr.py` keeps it out of logs and error messages on purpose.

The proposal:

- A new route, only for a logged-in session, that returns the dataset from
  `fetch_active_dataset()`, with `Cache-Control: no-store`.
- It is called by the app during step 2 only and never shown in the web UI.
- The app keeps it in memory for the duration of one run and never writes it
  to disk.
- Every call is logged — that it happened and from where, never the value.

Anyone who knows the web UI password can already remove every device and
change the radios; reading the network key is in the same class of power.
It is still a new capability, and the decision to add it belongs in the
review of this design, not in the pull request that implements it.

### 5.3 The cleanup in loxmatter

Section 4.1, after `commission_with_code` succeeds and before the route
returns. A failure to clean up is logged and does not fail the
commissioning — the device works, it only carries one fabric too many.

## 6. What stays unchanged

- The commissioning card and its code field. The app sends the same request
  the card sends.
- Commissioning through the Pi's own Bluetooth adapter.
- `matterjs-server` and its container.

## 7. Test plan on the test host

Before any app is written, the chain is proven with existing tools. A laptop
running `chip-tool` stands in for the phone. The commands below are from
`chip-tool`'s documentation and have to be confirmed on the test host; node
ids and codes are placeholders.

### T1 — Can a device on the Wi-Fi reach the Thread mesh?

Step 3 needs CASE over IP from the phone to a Thread device, through the Pi's
border router (`--backbone-interface` in the compose file).

- From the laptop, in the same LAN as the Pi: `avahi-browse -rt _matter._tcp`
  must list the Thread devices the Pi already has.
- `ping -6` to one of the addresses it prints must answer.

If this fails, the border router does not advertise its route into the LAN,
and nothing below can work. Stop and fix that first.

### T2 — Commission into a throwaway fabric with the Pi's dataset

On the Pi, read the dataset once (`ot-ctl dataset active -x`) and copy it to
the laptop by hand. Reset a Thread test device to factory state, then on the
laptop:

```
chip-tool pairing code-thread 1001 hex:<DATASET> <MT:QR-CODE>
```

Expected: success, and `ot-ctl child table` or `ot-ctl neighbor table` on the
Pi shows the device.

### T3 — Label, open a window, hand over

```
chip-tool operationalcredentials update-fabric-label loxmatter-handover 1001 0
chip-tool pairing open-commissioning-window 1001 1 300 1000 3840
```

The second command prints a manual pairing code. Enter it in the web UI's
commissioning card. Expected: the device appears in loxmatter with its
signals, and commissioning does **not** fall back to BLE (check the
`matter-server` log). This also answers whether `commission_with_code` finds
an already networked device without being told `network_only`.

### T4 — Leave

```
chip-tool operationalcredentials read current-fabric-index 1001 0
chip-tool operationalcredentials remove-fabric <INDEX> 1001 0
```

Then, from the Pi's side, read the device's fabrics (expert view or the
`matter-server` dashboard). Expected: exactly one fabric, the Pi's. The
device still reports values in loxmatter.

### T5 — The same from a phone

Repeat T1 from an Android phone and from an iPhone in the same Wi-Fi (any
network tool app that can browse mDNS and ping IPv6). Phones treat router
advertisements differently from laptops; this decides whether step 3 works on
real phones at all.

### T6 — A Pi restart

Restart the stack. Expected: the device is back online in loxmatter, still in
the Pi's Thread network. This is the same as for any device the Pi
commissioned itself, and it is here to confirm that nothing from the
throwaway fabric is still needed.

T1 to T4 passing means the concept holds and the app is only a user
interface around these steps. T5 decides whether it holds on phones.

## 8. Open questions

- The app's technology (section 5.1).
- Whether the dataset route (section 5.2) is acceptable, or whether the key
  should stay on the Pi at the cost of bringing option "Bluetooth relay" back.
- Wi-Fi devices: where the app gets SSID and password, and whether it may
  store them.
- Distribution: store releases, or side-loading only for Android at first.
