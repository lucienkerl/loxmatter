# Commissioning says what it is doing, and why it failed

Date: 22 September 2026. Follows "Pairing code entry" (2026-09-07) and
"A fresh installation forms its own Thread network" (2026-09-21).

## 1. What happened

On 21 September six commissioning attempts ran on `pi3-andi`, a Raspberry Pi 3
with its on-board Bluetooth. Two succeeded. For every one of them the web UI
showed the same thing for up to three minutes — "Commissioning the device into
the fabric" — and then either a device or the text of a matter-server
exception. What had actually happened was only visible in three logs read over
SSH:

| Attempt | What the logs showed | What the UI said |
|---------|----------------------|------------------|
| 22:11, 22:13, 22:20 | `discovery of node with discriminator 9 failed: No commissionable device was discovered` after exactly 180 s. The only Matter device advertising nearby (`LED Light0x07C2`, BlueZ service data `00 23 04 7c 11 01 90 00`) had discriminator 1059, short discriminator 4. The code belonged to no device in range. | "Commissioning failed: …" (the English exception text, as a 422) |
| 22:16 | Found in 1 s, PASE took 51 s (two failed BLE connection attempts), 25 BLE exchanges at 1.5–6 s each, then Thread and CASE in 4 s. 135 s in total, 115 of them BLE. | A spinner, then the device |
| 22:26, 22:38 | BlueZ `Discovering: false` while matter-server waited; kernel log every 10 s `hci0: Unable to disable scanning: -16`. The adapter had wedged after the previous attempt. | Spinner, then "not found" |
| 22:42, 22:45 | Device found, BLE handshake answered, then 37 s of `Frame reassembly failed` / `Received unexpected HCI Event 0x00` in the kernel log, `ATT error: 0x0e`, `unreachable`, afterwards `command 0x200c tx timeout`. | Spinner, then "Commissioning failed: …" |
| (22:35) | The bridge was restarted while a request was open: `RuntimeError: No response returned.` | "500 Internal Server Error" |

The next morning the kernel also reported `Undervoltage detected!` on the same
Pi.

Everything the user needed was knowable on the host: which phase the attempt
was in, whether the device the code names was in range at all, which other
Matter devices were, and that the Bluetooth chip was failing. None of it
reached the page.

## 2. Goal

While a commissioning attempt runs, the dialog shows the phase it is in, how
long it has run and roughly how long that phase takes. When it fails, the
dialog says which of a small set of reasons applies, in the user's language,
and what to try. During the search it lists the Matter devices advertising
nearby, with the one the code names marked. When the Bluetooth adapter reports
faults during the attempt, the dialog says so and names the usual remedies.
The diagnostics page gains a Bluetooth line.

## 3. What can be observed, and from where

matter-server reports no commissioning steps over its WebSocket API: the only
intermediate signal is `NODE_ADDED`, which arrives before `commission_with_code`
returns (`matter/client.py`). Its `discover` command is no substitute — measured
in matterjs-server 1.4.0 (`ControllerCommandHandler.handleDiscovery`): it looks
only on the IP network, for three seconds, and returns at most the last device
it saw. It never sees a device advertising over BLE.

Three host sources carry the rest. All three were measured on `pi3-andi` on
22 September:

- **BlueZ over D-Bus** (`/run/dbus/system_bus_socket`). matter-server already
  uses it from its container (`NOBLE_BINDINGS: dbus`). While matter-server
  scans, BlueZ keeps every advertisement it received as an `org.bluez.Device1`
  object: `Address`, `Name`, `RSSI`, `Connected`, and `ServiceData` keyed by
  UUID. A Matter device in commissioning mode advertises service
  `0000fff6-0000-1000-8000-00805f9b34fb`. `org.bluez.Adapter1` carries
  `Powered` and `Discovering`.
- **The kernel log** through `/dev/kmsg`. `kernel.dmesg_restrict` is 0 on
  Raspberry Pi OS and `/dev/kmsg` is `crw-r--r--`. The bridge container cannot
  call `dmesg` today ("read kernel buffer failed: Operation not permitted",
  Docker's seccomp profile blocks `syslog(2)`), but a container given
  `--device /dev/kmsg:/dev/kmsg:r` read 540 `Bluetooth: hci0` lines as uid 0
  without any capability.
- **matter-server's own answer**: the exception text of a failed
  `commission_with_code`, and `NODE_ADDED`.

The pairing code itself names the device: the 11-digit manual code carries the
4-bit short discriminator, the `MT:` QR payload the 12-bit long one.

## 4. The pairing code, decoded in the browser

`web/app.js` gains `decodePairingCode(text)`:

- **Manual code** (11 digits, after removing spaces and dashes). Digit 1 is
  chunk 1, digits 2–6 chunk 2, digits 7–10 chunk 3, digit 11 a Verhoeff check
  digit over the first ten. Short discriminator =
  `((chunk1 & 0x3) << 2) | (chunk2 >> 14)`. Bit 2 of chunk 1 set means a
  21-digit code with vendor and product id; this bridge accepts it but only
  decodes the discriminator.
- **QR payload** (`MT:` followed by base-38). The decoded payload is, from bit 0:
  version (3 bits), vendor id (16), product id (16), custom flow (2), discovery
  capabilities (8), discriminator (12), passcode (27). The long discriminator is
  therefore bits 45–56; its top four bits are the short discriminator.
- Test vector from the Matter specification: manual code `34970112332` and
  QR `MT:Y.K9042C00KA0648G00` both name discriminator 3840 (short 15) and
  passcode 20202021.

A manual code whose check digit is wrong is refused in the dialog before
anything is sent: "This code has a typo — check the digits." Every other code
is sent as today; the server's validation does not change.

The dialog shows the discriminator from the start of the attempt: "Looking for
the device with discriminator 9…" (short) or "…3840" (long).

## 5. The phases

### 5.1 `CommissioningTracker`

A new module `src/loxmatter/matter/commissioning_progress.py` holds one
`CommissioningTracker` per bridge. `POST /api/devices/commission` starts an
attempt in it and ends it; one attempt at a time, as the route already allows.

```
searching  ->  found  ->  connected  ->  joined  ->  done
                                                 \-> failed (reason)
```

- **searching** — from the start. Over BLE this lasts up to 180 s (matter-server's
  discovery timeout, measured).
- **found** — a BlueZ device advertises `fff6` service data whose discriminator
  matches the code (short against the top four bits, long exactly).
- **connected** — that same BlueZ device reports `Connected: true`. PASE and the
  commissioning steps run over this connection; on `pi3-andi` they took 51 s
  and 64 s.
- **joined** — `NODE_ADDED` for a new node arrived while the attempt runs. The
  route is now registering signals and commands.
- **done** / **failed** — when the route returns.

Phases only move forward. A device commissioned over the IP network never
produces the BLE phases; its attempt goes from `searching` to `joined`. Without
BlueZ access (section 8) every attempt does.

The tracker samples BlueZ every 2 s while an attempt runs, and not at all
otherwise.

### 5.2 `GET /api/devices/commission/status`

Behind the same guard as the rest of `/api`. It answers with:

```json
{
  "bridge_started_at": "2026-09-22T06:50:31Z",
  "attempt": {
    "started_at": "…",
    "discriminator": {"value": 9, "kind": "short"},
    "phase": "searching",
    "phase_since": "…",
    "reason": null,
    "nearby": [ … ],
    "bluetooth": { … }
  }
}
```

`attempt` is the running attempt, or the last finished one, or `null` after a
bridge start. `nearby` is section 6, `bluetooth` section 7.1. The discriminator
is sent along with the `POST` body as `discriminator` (value and kind, from
section 4); the server does not decode codes itself. A code the browser could
not decode sends none, and the `found`/`connected` phases then stay out.

### 5.3 The dialog

While the `POST` is open, the dialog polls the status route every 2 s and shows:

- the phase as a list (done ✓, current highlighted, later ones grey) — the
  existing two-step list becomes this five-step list;
- the time since the start, and for the current phase its usual duration:
  "Searching over Bluetooth — usually seconds, at most 3 minutes";
  "Setting the device up over Bluetooth — usually 1–2 minutes";
- during `searching`, the nearby list (section 6);
- the Bluetooth warning when there is one (section 7.1).

A page that is reloaded while an attempt runs finds it through the status route
and shows its progress; the result then arrives through the status route
rather than the `POST` the old page had open.

## 6. Matter devices nearby

`src/loxmatter/radios/bluez.py` reads `GetManagedObjects` from `org.bluez` and
returns, for every `Device1` with `fff6` service data:

| Field | From |
|-------|------|
| `address` | `Address` |
| `name` | `Name`, if any |
| `rssi` | `RSSI`, if any |
| `discriminator` | service data bytes 1–2, little-endian, low 12 bits |
| `vendor_id`, `product_id` | bytes 3–4 and 5–6, little-endian |
| `connected` | `Connected` |
| `matches` | whether `discriminator` matches the attempt's |

Measured example: `00 23 04 7c 11 01 90 00` → discriminator 1059, vendor 4476
(0x117C, IKEA), product 36865 (0x9001). Byte 0 (opcode) and byte 7 (flags) are
read but not shown.

The list is only as fresh as someone's scan. During commissioning matter-server
scans, which is exactly when the list is shown. The bridge never starts a scan
of its own: on 21 September the adapter wedged after one attempt, and a second
scanning client on the same chip is the last thing it needs. BlueZ keeps stale
entries for a while; entries without an `RSSI` are dropped, the rest sorted by
`RSSI`.

When the attempt fails with "not found" (section 7.2), the list stays in the
result: "No device with discriminator 9 is in range. Advertising nearby:
LED Light0x07C2 (discriminator 1059)."

Never shown or stored: nothing beyond the fields above. Service data of a Matter
commissioning advertisement carries no secret.

## 7. Bluetooth health, and the failure reasons

### 7.1 Bluetooth health

`src/loxmatter/radios/bluetooth_health.py` reads `/dev/kmsg` non-blocking from
the start of the ring buffer, keeps only lines matching one of these patterns,
and turns each into `(category, adapter, monotonic time)`:

| Category | Kernel line (measured on pi3-andi unless noted) |
|----------|--------------------------------------------------|
| `transport` | `Bluetooth: hciN: Frame reassembly failed (-84)` / `(-90)`, `Received unexpected HCI Event 0x00` |
| `stuck` | `Unable to disable scanning: -16`, `stop background scanning failed`, `command 0x200c tx timeout`, `Opcode 0x200c failed: -110` |
| `power` | `hwmon hwmonN: Undervoltage detected!` |

Kernel timestamps are microseconds since boot; `/proc/uptime` gives the
reference. Raw lines never leave this module: the API carries categories,
counts and the time of the last one.

Together with BlueZ `Adapter1` (`Powered`, `Discovering`) the status route's
`bluetooth` object reads:

```json
{"available": true, "adapter": "hci0", "powered": true,
 "discovering": false, "during_attempt": {"transport": 23, "stuck": 4, "power": 0},
 "last_hour": {"transport": 540, "stuck": 52, "power": 1}}
```

`discovering: false` while an attempt is in `searching` over BLE is itself a
`stuck` finding: matter-server is waiting for advertisements the adapter is not
receiving.

The dialog warns as soon as `during_attempt` has any count or the adapter is
stuck:

- `transport`: "The Bluetooth chip reports transmission errors. A USB Bluetooth
  adapter usually fixes this; select it on the radios card."
- `stuck`: "The Bluetooth adapter stopped scanning. Restarting the bridge host
  usually clears it; a USB Bluetooth adapter avoids it."
- `power`: "The Raspberry Pi reports undervoltage. Use the official power supply
  for this Raspberry Pi model." — shown with either of the others too.

The diagnostics page (`api/diagnostics.py`) gains a check `bluetooth` built from
`last_hour`: green with no findings, a warning naming the categories otherwise,
"not available" when neither source can be read.

### 7.2 The failure reasons

`commission_with_code` failures are classified by the bridge, from the phase the
attempt reached and matter-server's text:

| Reason | When | Text (en) |
|--------|------|-----------|
| `not_found` | text contains `No commissionable device was discovered` | "No device with discriminator {d} is in range, or it is not ready for pairing. Check the code, put the device in pairing mode, and keep it close to the bridge." + nearby list |
| `connection_lost` | text contains `No device could be commissioned` or `unreachable`, or the phase had reached `found`/`connected` | "The device was found, but the Bluetooth connection to it broke off." + Bluetooth warning if any |
| `no_thread_network` | the existing Thread-credentials cause (`api.devices.commissioning_thread_cause`) | unchanged |
| `matter_server_unreachable` | `MatterUnavailableError` | unchanged (`api.errors.matter_server_unreachable`) |
| `bridge_restarted` | decided in the browser: the `POST` failed, and the status route's `bridge_started_at` is newer than when the attempt began | "The bridge restarted during commissioning. Check whether the device appears in the list; otherwise try again." |
| `other` | anything else | "Commissioning failed: {matter-server's text}" as today |

The `POST` keeps its status codes and its string `detail`, now the text of the
reason through `i18n.t(...)`. The reason itself is read from the status route
(`attempt.reason`), which the dialog reads after a failure anyway; `readError`
in `web/app.js` stays as it is.

All texts live in `src/loxmatter/i18n/strings.yaml` with `en` and `de`.

## 8. Rollout and degradation

`deploy/testhost/docker-compose.yml`, service `loxmatter`:

```yaml
    volumes:
      - /run/dbus:/run/dbus:ro
    devices:
      - /dev/kmsg:/dev/kmsg:r
```

Both are read-only. The compose file travels with every release and the updater
applies it, so installations get this through the ordinary update. The README's
security notes for the `loxmatter` service name both, and why.

Each source degrades on its own:

- no `/run/dbus` socket, or BlueZ not running → no `found`/`connected` phases,
  no nearby list, no `powered`/`discovering`;
- no `/dev/kmsg`, or reading it fails (`dmesg_restrict=1` on another
  distribution) → no kernel categories;
- both missing → `bluetooth.available: false`; the dialog shows phases
  `searching` → `joined` and the failure reasons, which need neither.

Nothing here makes commissioning itself depend on either source.

The D-Bus client is `dbus-fast` (pure Python, asyncio, what Home Assistant's
Bluetooth stack uses), added to `pyproject.toml`.

## 9. Tests

- `tests/api/test_web.py` (node, like the existing Alpine helper tests): `decodePairingCode` with
  the specification's vectors, a wrong check digit, a 21-digit code, a QR
  payload, garbage.
- `tests/radios/test_bluez.py`: a fake `GetManagedObjects` result built from the
  measured `LED Light0x07C2` object → one entry with discriminator 1059, vendor
  4476, product 36865; devices without `fff6` or without `RSSI` dropped;
  D-Bus unavailable → empty list, no exception.
- `tests/radios/test_bluetooth_health.py`: a fixture of real `/dev/kmsg` lines
  from `pi3-andi` (Bluetooth and hwmon lines only) → the counts per category
  and window; an unreadable device → `available: false`.
- `tests/matter/test_commissioning_progress.py`: the phase sequence driven by a
  fake BlueZ reader and a fake `NODE_ADDED`; phases never move backwards; an IP
  attempt goes `searching` → `joined`; the reason classification for each
  measured matter-server text.
- `tests/api`: the status route before any attempt, during one, after one; the
  `POST` body carrying the reason; the guard.

## 10. On hardware

On `pi3-andi`, with the owner:

1. A code for no device in range → `not_found` after 180 s, the nearby list
   shows whatever advertises.
2. A real device → the phases run through; the times per phase are recorded
   here as a dated note.
3. The on-board Bluetooth misbehaving (it did on 21 September without help) →
   the dialog's warning; the diagnostics line.
4. The bridge restarted mid-attempt → `bridge_restarted`.

## 11. Not part of this

- A scan started by the bridge.
- Raw kernel lines in the web UI or the API.
- Switching the Bluetooth adapter automatically.
- Progress for Zigbee pairing, which has its own flow.
- The diagnostics check that trusts `thread_credentials_set` (a separate task,
  noted on 21 September).
