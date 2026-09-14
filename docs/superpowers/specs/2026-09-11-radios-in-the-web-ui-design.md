# Radios in the Web UI: Thread Stick and Bluetooth Adapter Without Editing Files

Design, 11 September 2026. Lets the user choose, in the web UI, which USB
stick the Thread border router uses (or none), and which Bluetooth adapter
matter-server commissions over — the way the Miniserver address is already
set there. Nobody has to edit `.env` or `docker-compose.yml` for it any more.

Rationale: today the installer detects both once and writes them into
`deploy/testhost/.env`. Everything after that — plugging in a Thread stick
later, replacing one, a second USB stick changing the boot order — means a
shell on the host and a hand-edited file. The README even says so for the
most common case ("set `COMPOSE_PROFILES=thread` and `RADIO_DEVICE` in
`.env`"). The Zigbee work that follows needs a stick chosen the same way,
and a user with two sticks needs them to stay apart.

## 1. Where This Sits

This is the first of three specs in the Zigbee programme's second half
(`2026-09-11-device-source-boundary-design.md` was the first half):

| Spec | Content | Status |
|---|---|---|
| **2a-1 — this document** | Radios in the web UI: detection, a settings card for Thread and Bluetooth, a new job for the updater sidecar, Compose changes | designed |
| 2a-2 | Zigbee as the second device source, and commissioning in a Zigbee tab; adds a Zigbee row to this card | next |
| 2b | Operations: installer detection, diagnostics, coordinator backup, heartbeat with two sources, Metering, button events | later |

Decided with the maintainer while designing this (binding):

| Decision | Choice |
|---|---|
| Which radios are configurable in the UI | All three: Thread stick, Bluetooth adapter, and (with 2a-2) the Zigbee stick |
| Order | This spec first, then Zigbee |
| How Thread and Bluetooth changes are applied | A new, strictly validated job in the existing updater sidecar (section 6) |
| The Zigbee row | Appears with 2a-2, not before: a choice that changes nothing would confuse anyone running a release that has this spec but not the next |

**Rejected, with reasons.** A host-side helper (systemd or cron) applying a
config file would leave the sidecar untouched but add a component outside
Docker that the installer must set up, the console for every existing
installation, and a second path that controls containers. Self-reloading
services — `otbr` and `matter-server` started through wrapper scripts that
exit when a config file changes, so Docker restarts them — need no socket,
but depend on the internals of third-party images (OTBR's init already
misbehaves on the Pi kernel), and have no verification, no rollback and
fail silently.

## 2. Principles

1. **The truth is `.env`, and only the sidecar reads it.** The sidecar
   reports the effective radio configuration on every loop iteration
   (section 6.5). The bridge keeps no copy that could drift from it.
2. **The bridge itself is never recreated by this job.** Only `otbr` and
   `matter-server` are. The UI stays reachable throughout — unlike an
   update — and progress comes through the normal API. The existing
   supervisor (`sources/supervisor.py`) reconnects to matter-server after it
   restarts.
3. **No file has to be edited by hand any more** for choosing, switching,
   enabling or disabling a radio.

## 3. Measured on the Test Pi, 11 September 2026

| Question | Result |
|---|---|
| Radios attached | One USB stick: SONOFF Dongle Plus MG24 (`10c4:ea60`), the Thread RCP, at `/dev/ttyUSB0`, by-id `usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a7d9118f9ef118f7767135c2a50c9-if00-port0`. One Bluetooth adapter `hci0`, on UART. |
| What the running containers see | Both `loxmatter` and `loxmatter-updater` see `/sys/class/bluetooth/hci0` and `/sys/class/tty/ttyUSB0`. Neither sees `/dev/serial/by-id` or `/dev/ttyUSB*`. |
| Stick details from sysfs, inside `loxmatter` | `manufacturer=SONOFF`, `product=SONOFF Dongle Plus MG24`, `serial=e26a7d9118f9ef118f7767135c2a50c9`, `idVendor=10c4`, `idProduct=ea60`, read from the USB interface's grandparent of `/sys/class/tty/ttyUSB0/device`. |
| Bluetooth details from sysfs | `uevent` has `DEVTYPE=host`; `device` links to `…/serial0/serial0-0` (UART); an `rfkill0` entry exists. No address is exposed in sysfs. |
| Current `.env` | The stack runs from `/home/pi/matter-loxone/deploy/testhost` (Compose labels of all four containers; `/home/pi/loxmatter-testhost` is a leftover from 3 September). Its `.env`: `RADIO_DEVICE=/dev/ttyUSB0` (the unstable name the installer detected), `RADIO_BAUDRATE=460800`, `BACKBONE_IF=wlan0`, `BLUETOOTH_ADAPTER=0`, **no `COMPOSE_PROFILES` line** although the Compose file puts `otbr` behind `profiles: ["thread"]` and `otbr` runs — it was once started with the profile given on the command line. |
| Docker with a by-id path | `docker run --device <by-id path>:<by-id path>` works: the container gets a character device `188,0` at that path. |

The last two rows matter beyond this Pi: every installer-made `.env` holds
a `/dev/ttyUSB*` or `/dev/ttyACM*` name, and the test Pi deviates from the
installer's layout in a way the sidecar must not guess about (section 12).

## 4. Detection

A new module `loxmatter/radios/inventory.py` builds the inventory each
time the card asks for it. No background process; "Rescan" just asks again.

**USB serial devices.** Every entry of `/host/dev/serial/by-id/` (section
5). For each: the symlink's target name (`ttyUSB0`), and from
`/sys/class/tty/<name>/device` the USB interface's parent attributes
`manufacturer`, `product`, `serial`, `idVendor`, `idProduct` — each
optional, since not every USB serial chip reports all of them.

**Bluetooth adapters.** Every `hci<N>` under `/sys/class/bluetooth/`: the
index `N`, the bus derived from where `device` links (a path containing
`/usb` is USB, a `serial` path is UART, anything else "other"), for USB the
`product` attribute when present, and whether any `rfkill*/soft` below it
reads `1`.

**Assignment** comes from the sidecar's report (section 6.5). A reported
Thread device that is not a by-id path (`/dev/ttyUSB0`) is matched to the
by-id entry whose symlink points to the same `tty` name. A reported device
that matches nothing is shown as a separate entry marked missing, so the
card never implies an assignment that is not there.

```python
@dataclass(frozen=True)
class SerialRadio:
    path: str  # /dev/serial/by-id/… (the host path, not /host/dev/…)
    tty: str  # ttyUSB0
    manufacturer: str | None
    product: str | None
    serial: str | None
    vid_pid: str | None  # "10c4:ea60"


@dataclass(frozen=True)
class BluetoothAdapter:
    index: int
    name: str  # hci0
    bus: Literal["uart", "usb", "other"]
    product: str | None
    rfkill_blocked: bool
```

The roots (`/host/dev`, `/sys`) are parameters, so tests run against
directory trees built in `tmp_path`.

## 5. Compose Changes

| Service | Change | Why |
|---|---|---|
| `loxmatter` | `/dev:/host/dev:ro` | Only to read the names under `serial/by-id`. Without a device cgroup rule the container cannot open any device node, so this grants no access to a radio. Access for Zigbee comes with 2a-2. |
| `loxmatter-updater` | `/dev:/host/dev:ro` | The sidecar validates every path itself instead of trusting the bridge. |
| `otbr`, `matter-server` | none | They keep reading `${RADIO_DEVICE}`, `${RADIO_BAUDRATE}` and `${BLUETOOTH_ADAPTER}` from `.env`. |

These arrive with a normal update: the sidecar runs Compose against the
repository's own `deploy/testhost/docker-compose.yml`. The sidecar's new
mount only takes effect once the sidecar container is recreated, which it
cannot do itself (see the updater design and the measured failure behind
it); until then the card is read-only (section 9).

## 6. The Sidecar Job

### 6.1 Shape

A new script `deploy/updater/radios-once.sh`, one responsibility, copied
into the image next to `update-once.sh`. `entrypoint.sh` calls it in the
same loop, directly after `update-once.sh`, under the same worker timeout.
Because the loop is sequential, an update and a radio job never run at the
same time without needing a lock. `update-once.sh` is not changed. The few
helpers both need (writing state atomically, the Compose wrapper, a
bounded log) are written again in small form inside `radios-once.sh`
rather than extracted from the 2433-line updater script, whose behaviour
was only proven on hardware as it stands.

### 6.2 Files in `/data/update/`

| File | Writer | Content |
|---|---|---|
| `radios-request.json` | bridge | `{id, thread, bluetooth, requested_at}`, atomic via temp file and rename. `thread` is `{enabled, device}` or `null`; `bluetooth` is `{adapter}` or `null` |
| `radios-state.json` | sidecar | `{id, phase, steps[], error, rolled_back, healthy, current, capable, seen_at}` |
| `radios-log.txt` | sidecar | raw output, capped like `log.txt` |

The sidecar processes an `id` exactly once, like the updater.

### 6.3 Validation

Everything below is enforced in the sidecar; a request that fails any rule
ends as `rejected` with an error key, and nothing is executed and nothing
written to `.env`:

- The request is a JSON object with exactly the keys `id`, `thread`,
  `bluetooth`, `requested_at`. `thread` is `null` or an object with exactly
  `enabled` and `device`; `bluetooth` is `null` or an object with exactly
  `adapter`. The key set is a strict whitelist and does not change; only
  the value type widened (amendment, 12 September 2026).
- **A `null` half means "leave this radio alone".** It is not validated
  against the host, none of its `.env` keys are written, and it is neither
  applied nor verified — it cannot appear in the step list, and the
  rollback pass does not touch it either. The bridge sends `null` for a
  radio the user did not change (section 7). This is what lets a
  Bluetooth-only change succeed while the configured Thread stick is
  unplugged, and what stops a Bluetooth-only change from recreating `otbr`
  on an installer-made `.env`, where the stored `/dev/ttyUSB0` never
  compares equal to the by-id path the card speaks in. A half that *is*
  present is validated in full, exactly as before: an attached stick a
  request actually names must still exist, resolve to a tty, and have
  `BACKBONE_IF` set.
- `thread.enabled` is a boolean. `thread.enabled = true` requires a device.
- `thread.device` is `null` or a string matching
  `^/dev/serial/by-id/[A-Za-z0-9._:+-]+$`; `/host` + that path exists, is a
  symlink, and resolves to `/host/dev/ttyUSB[0-9]+` or
  `/host/dev/ttyACM[0-9]+`.
- `bluetooth.adapter` is an integer from 0 to 15 and
  `/sys/class/bluetooth/hci<adapter>` exists.
- Enabling Thread requires `BACKBONE_IF` to be set in `.env`; the sidecar
  does not guess an interface.
- A request that changes nothing ends as `unchanged` — including one whose
  halves are both `null`.
- A request is not started while `state.json` shows an update in a running
  phase; it waits for the next iteration.

### 6.4 Flow

| Phase | What happens |
|---|---|
| `validate` | Section 6.3. |
| `backup` | `.env` copied to `.env.radios-<stamp>` beside it. |
| `write` | Only these keys change: `RADIO_DEVICE` (always written as the by-id path), the `thread` entry of `COMPOSE_PROFILES` (added or removed, other entries kept), `BLUETOOTH_ADAPTER`. `RADIO_BAUDRATE` is written as `460800`, the installer's default, only if it is missing and Thread is being enabled. A half sent as `null` (section 6.3) writes none of its keys: a request that leaves Thread alone touches neither `RADIO_DEVICE` nor `COMPOSE_PROFILES` nor `RADIO_BAUDRATE`, and one that leaves Bluetooth alone does not touch `BLUETOOTH_ADAPTER`. |
| `apply_bluetooth` | Only if the adapter changed: recreate `matter-server` (`up -d --no-deps --force-recreate matter-server`). |
| `verify_bluetooth` | A TCP connection to the host's port 5580 succeeds within 60 s. |
| `apply_thread` | Thread enabled or its device changed: recreate `otbr`. Thread disabled: stop and remove `otbr`. |
| `verify_thread` | Enabled: `docker exec otbr ot-ctl state` prints `leader`, `router` or `child` within 90 s. If it has not by 30 s, the sidecar applies the watchdog's known fix once — remove `/run/otbr-agent.pid` in the container, restart `otbr` — and keeps waiting until 90 s. Disabled: no `otbr` container exists. |
| `done` / `failed` | Terminal. |

**On any verification failure:** restore the backed-up `.env`, apply the
same services again with the old values, verify again, and report
`rolled_back: true` and `healthy` as that second verification actually
came out — `false` is reported as `false`, with the log tail, never
smoothed over.

**The Thread network survives a stick change.** Network key, PAN ID and
channel live in the `otbr-state` volume (see the Compose comment on it),
not on the stick; the new RCP is configured from them. Thread devices are
unreachable for one to two minutes and rejoin on their own. A stick with
the wrong firmware (a Zigbee NCP chosen as Thread RCP) fails `verify_thread`
and is rolled back.

### 6.5 Reporting the Current Configuration

On every loop iteration, with or without a job, `radios-once.sh` writes
`seen_at` and `current`:

```json
{
  "thread_enabled": true,
  "thread_device": "/dev/ttyUSB0",
  "bluetooth_adapter": 0,
  "otbr_running": true
}
```

`thread_enabled` means `COMPOSE_PROFILES` contains `thread` or an `otbr`
container exists (section 12, point 1); `otbr_running` is what Docker
reports, so the card can show when the two
disagree instead of hiding it. `capable` is `false`, with a reason key,
when `/host/dev` is not mounted in the sidecar (a sidecar container
created before section 5's change).

While a job is applying, both timestamps the card judges the sidecar by
are refreshed from inside the long waits and during a rollback as well:
`seen_at` here, and `updater_seen_at` in `state.json`, which nothing else
can refresh at that moment because `entrypoint.sh` runs the two workers
one after the other in the same loop. Without that, a healthy job that
sits in `verify_thread` for up to 90 s goes silent for longer than
`_MAX_SILENT_SECONDS` (30 s, `src/loxmatter/update.py`) and the card
reports it as abandoned — which is exactly what the flagship stick switch
did until this was added.

One gap remains open, deliberately: the refresh brackets each `docker
compose` call rather than running during it, so a container recreate that
by itself takes longer than the silence budget can still exceed it and be
reported as abandoned. Closing that needs a refresh that runs concurrently
with the call, and is a separate task — this section describes a hole made
much smaller, not a closed one.

### 6.6 The Security Boundary, Extended

The updater design (`2026-09-08-webui-updates-design.md`, section 10)
states that whoever takes over the bridge can, through the sidecar, only
install a published, newer loxmatter version. This spec deliberately
extends that with a fourth rule, to be added there, in the README's
updater section and in the Compose comment on the sidecar:

> Through the same files, the bridge can additionally change only which
> existing USB serial device `otbr` uses, whether Thread runs, and which
> existing Bluetooth adapter `matter-server` uses. The sidecar validates
> each value against the host's devices, touches no other key and no other
> service, and executes no value from a request as a command.

`otbr` already runs privileged; pointing it at a different existing USB
serial device grants it nothing it could not already reach.

## 7. Bridge API

Behind the existing `api_guard`, in a new `api/radios.py`:

**`GET /api/radios`** — the shape, with the dataclasses of section 4 as
lists:

```text
{
  "sidecar": "ready | missing | outdated | unmounted",
  "update_running": false,
  "updater_stack_host_path": "/home/pi/loxmatter/deploy/testhost",
  "serial": [SerialRadio],
  "bluetooth": [BluetoothAdapter],
  "current": {"thread_enabled": true, "thread_device": "/dev/serial/by-id/…",
              "thread_device_present": true, "bluetooth_adapter": 0,
              "otbr_running": true},
  "job": {"id": "…", "phase": "…", "steps": [], "error": null,
          "rolled_back": false, "healthy": null}
}
```

- `sidecar`: `missing` when `state.json` shows no live updater (the
  existing check, 30 s window); `outdated` when the updater is live but
  `radios-state.json` has no `seen_at` within the same window; `unmounted`
  when `radios-state.json` says `capable: false`; otherwise `ready`.
- `update_running`: the update job is in one of its running phases
  (`_RUNNING_PHASES`, `src/loxmatter/update.py`). The card needs this
  because `sidecar` cannot express it: the two workers run sequentially in
  one loop, so a long update necessarily starves the radios heartbeat and
  reads back as `outdated` — and, once `updater_seen_at` goes stale too,
  as `missing`. Both are false readings of a current, busy sidecar, and
  the refresh command the `outdated` text prints would terminate the
  running update. While this is `true` the card says an update is running
  and prints no command.
- `updater_stack_host_path`: the host path of the Compose stack, from
  `state.json`. The card needs it to print the refresh command for the
  genuine `outdated`/`unmounted` cases; `null` when no updater state has
  been read, which selects the wording that names no path.
- `current` is the sidecar's report with `thread_device` already mapped to
  its by-id entry (section 4); `null` unless `sidecar` is `ready` or
  `unmounted`.
- `job` is the latest `radios-state.json` job, or `null`.

**`POST /api/radios`** with `{thread, bluetooth}` writes
`radios-request.json` and answers `202 {id}`. Both keys are required and
either may be `null`: `{enabled, device}` or `null` for `thread`,
`{adapter}` or `null` for `bluetooth`. `null` means "leave this radio
alone" and is passed through to the sidecar unchanged (section 6.3); a
**missing** key is a **422**, because a caller that forgot a half is a bug
rather than a request to skip it. The full both-halves shape stays valid
forever — this was an addition, not a replacement.

Only the halves that are present are validated. The bridge never re-derives
which halves changed: the card knows what the user touched and says so, and
a layer that guessed instead could invert a real intent when its own report
had gone stale (section 6, case 5).

It answers **409** while an update is in a running phase or a radio job has
not reached a terminal phase, **400** for values that are obviously invalid
against the bridge's own inventory — only for a half that is actually
present — and **503** unless `sidecar` is `ready`. The sidecar still has the
last word (section 6.3).

All error details through i18n with `en` and `de`.

## 8. The Settings Card

Placement: the Settings tab, directly below the Miniserver connection.

| State | Content |
|---|---|
| Normal | A Thread row: a selection of the detected USB sticks plus "No Thread stick", the current one marked "in use". A Bluetooth row: the detected adapters (`hci0 · built in (UART)`, or USB with product name), the current one marked "in use", a warning when rfkill blocks one. "Rescan". "Apply" appears only after a change. A current stick that is missing appears as its own entry marked missing. |
| Confirmation | Text chosen by what changes, and it names what will actually happen. Thread stick switched: the border router restarts with the new stick, Thread devices are unreachable for one to two minutes and rejoin, the network stays the same. Thread disabled: Thread devices stay unreachable until it is enabled again. Bluetooth switched: matter-server restarts and a running commissioning is aborted. Bluetooth switched while Thread stays as it is: says so outright — Thread is not touched, the border router keeps running — because the request leaves that half out and nothing about Thread moves. Every variant: a failure restores the old setting automatically. |
| Running | The steps from `radios-state.json`, rendered like the update card's steps, then "Applied" or "Failed, previous setting restored" with the reason. |
| Sidecar not ready | The detection stays visible, read-only. `missing`: says changing radios needs the updater service. `outdated` and `unmounted`: the existing refresh command (`web`/`api` strings for refreshing the updater sidecar, reused, not rewritten). |

Step names and error reasons come from the sidecar as keys and are
translated in the UI, as the update card does. Not on the card: baud rate,
backbone interface, unblocking rfkill (needs root on the host; the card
only warns, as the installer does).

## 9. When the Sidecar Cannot Do It

Three situations, one read-only card (section 8), no dead button:

- no sidecar at all (removed from the Compose file),
- a sidecar image without `radios-once.sh`,
- a new sidecar image whose container predates the `/dev` mount.

The first radio change after installing this release therefore needs the
console once — the same refresh command the System tab already shows when
the sidecar lags. This belongs in the release notes of the version that
ships this spec.

## 10. Testing

### 10.1 Automatic

Every test that names a protection is shown to catch it: the fault is
introduced, the test fails, the fault is reverted.

| Area | Where | What |
|---|---|---|
| `radios-once.sh` | new `tests/test_updater_radios_script.py`, following the fake-Docker harness of `tests/test_updater_script.py`, with `/host/dev` and `/sys` trees built in `tmp_path` | **Rejection without effect**: wrong path pattern, `by-id/../../sda`, a symlink to a non-tty, a missing adapter index, unknown keys, Thread enabled without a device, Thread enabled without `BACKBONE_IF` — each asserting no Docker call and a byte-identical `.env`. **Normal flows**: Bluetooth switched, stick switched, Thread disabled, Thread enabled (profile entry added, other entries kept, baud rate default only when missing). **Hanging agent**: the pid fix applied exactly once. **Verification failure**: `.env` restored, services applied again, `rolled_back` and `healthy` reported as they came out. **Other**: `unchanged`; `current` with a legacy `/dev/ttyUSB0`; `capable: false` without `/host/dev`; a job not started during a running update. |
| Detection | `tests/radios/test_inventory.py` | `ttyUSB0` matched to its by-id entry; manufacturer/product/serial/VID:PID, each optional; UART vs USB vs other; rfkill blocked; a current device that is missing. |
| API | `tests/api/test_radios_api.py` | `GET` composition for all four `sidecar` values; `POST` 202 writes the request atomically; 409 during an update and during a running radio job; 400; 503 unless ready. |
| UI | node tests on `app.js` in `tests/api/test_web.py` | change detection, the confirmation text chosen per change, the four card states. |
| Compose | `tests/test_compose_profiles.py` | `/dev:/host/dev:ro` on exactly `loxmatter` and `loxmatter-updater`, read-only. |

### 10.2 On the Test Pi

The updater's lesson was that the defects that mattered sat at boundaries
no test saw.

**The procedure lives in `2026-09-09-first-run-checklist.md`, section 14**,
and only there. It used to be written out a second time here, and the two
copies had already drifted: this one claimed an invalid Bluetooth index is
"rejected by the real sidecar", when the bridge answers 400 before the
sidecar ever sees it — so a maintainer following this copy would have been
proving the wrong boundary. A hardware session is expensive enough that
the steps must exist once, in the document the maintainer actually works
through at the Pi.

What that section covers: the read-only card before the sidecar refresh
and the normal card after; the stick and `hci0` both listed as "in use";
rejection without effect, at both boundaries; a `null` half leaving the
other radio alone, including with the configured stick unplugged; the real
stick switch; Thread off and on again; and "nothing to change".

Two of those steps interrupt the maintainer's Thread devices for minutes
and are scheduled with the maintainer, not run unannounced. The rollback
after a real failure is covered only by the automatic tests.

## 11. Explicitly Not Built

- The Zigbee row and any read-write device access for the bridge (2a-2).
- Editing `RADIO_BAUDRATE` or `BACKBONE_IF`.
- Installer changes; the installer still writes the first `.env`.
- Unblocking rfkill; showing Bluetooth addresses (not in sysfs, would need
  D-Bus).
- Probing what firmware a stick runs; a wrong choice is caught by
  verification and rolled back.
- Replacing the cron watchdog for OTBR.

## 12. Open Points

1. **Resolved while planning: a running `otbr` without a profile line.**
   The Pi's real stack is the repository layout; its `.env` simply lacks
   `COMPOSE_PROFILES` while `otbr` exists. So `thread_enabled` in the
   sidecar's report means "`COMPOSE_PROFILES` contains `thread`, **or** an
   `otbr` container exists", and the first applied change writes the
   profile explicitly. Naming `otbr` on the Compose command line activates
   its profile, so recreating it works either way.
2. **Baud rate per stick.** 460800 fits the SONOFF MG24 and the installer's
   default; an RCP that needs another rate fails verification and is rolled
   back, with no way to fix it from the UI. Revisit if it happens.
3. **For 2a-2:** the bridge will need read-write access to the Zigbee stick
   (`/dev` mounted read-write plus `device_cgroup_rules` for the tty
   majors), which widens what a compromised bridge can reach; the Zigbee row
   joins this card and the Thread stick must be excluded from it.

## Correction, 14 September 2026: a by-id path in the privileged otbr container

The measurement in section 2 ("`docker run --device <by-id path>:<by-id path>`
works") was taken with an unprivileged container. `otbr` runs with
`privileged: true`, and there it does not hold. Measured on the test Pi on
14 September 2026:

- `--privileged --device <by-id>:<by-id>` leaves the path missing inside the
  container. otbr-agent then stops at
  `Init() at hdlc_interface.cpp:154: No such file or directory`.
- `--privileged --device <by-id>:/dev/ttyThread` gives `/dev/ttyThread` with the
  stick's own `188,0`.
- `--device <by-id>:<by-id>` without `--privileged` works, as section 2 recorded.

A Thread change on the Radios card writes a by-id `RADIO_DEVICE`, so every such
change left the border router unable to start. The compose file now always
passes the stick in as `${RADIO_DEVICE}:/dev/ttyThread`, and `RADIO_URL` names
`/dev/ttyThread`. `tests/test_compose_profiles.py` pins both. A bare
`/dev/ttyUSB0` in `.env` keeps working unchanged.
