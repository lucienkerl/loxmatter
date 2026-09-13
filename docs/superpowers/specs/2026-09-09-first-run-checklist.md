# First run on the Pi: what to check before pressing the button

Written 9 September 2026, after the whole-branch review of the browser-update
feature (Stufe 2). It exists because of one fact: **nothing in that feature
has ever run as a real container.** The sidecar's worker is a thousand lines
of POSIX shell exercised only against fake binaries on a sealed `PATH`, and
the machine it was written on has no Docker daemon.

Four defects found in that review were exactly this class — things that are
invisible until a container boundary is crossed. They are fixed, but fixed by
reasoning, not by observation. Steps 1–9 below change nothing and replace no
container; run them first.

## 0. Before touching the Pi — the registry

`ghcr.io/lucienkerl/loxmatter-updater` is created for the first time by the
0.3.0 tag. **New GHCR packages default to private.** From a machine logged
out of ghcr:

```bash
docker logout ghcr.io
docker pull ghcr.io/lucienkerl/loxmatter-updater:stable
docker buildx imagetools inspect ghcr.io/lucienkerl/loxmatter-updater:stable
```

`denied` or `unauthorized` means the package is private and every installation
will fail to start the sidecar. A missing `linux/arm64` line means the Pi will
report "no matching manifest".

## 1. Create the sidecar

```bash
cd ~/loxmatter && git pull --ff-only
cd deploy/testhost && docker compose up -d
docker ps --filter name=loxmatter-updater
```

No container here means the compose entry did not land.

## 2. The path trap — check before anything is recreated

```bash
docker exec -w /repo/deploy/testhost loxmatter-updater docker compose config | grep -B2 -A2 'source:'
docker inspect loxmatter --format '{{json .Mounts}}'
```

Compare them. If the sidecar prints `source: /repo/...` where the host shows
`/home/pi/loxmatter/...`, **stop**. Compose run inside a container resolves the
compose file's relative bind mounts against its own project directory, so the
first update would recreate the bridge with an empty `/matter-data` and the
sidecar with an empty `/repo`. This is what `--project-directory` was added to
prevent; this step proves it works.

## 3. git inside the sidecar

```bash
docker exec loxmatter-updater git -C /repo status --porcelain; echo rc=$?
```

`fatal: detected dubious ownership` means every update dies at `git fetch`.
After the first update also run `find ~/loxmatter -user root | head` — root-owned
files in your checkout are the other half of that problem.

## 4. busybox versus GNU

```bash
docker exec loxmatter-updater sh -c 'sort --version | head -1; printf "0.3.0\n0.10.0\n" | sort -V; timeout --version | head -1'
```

`sort -V` must put `0.3.0` first. busybox `sort` would invert it and let a
downgrade past the forward-only check. `timeout` must be coreutils, or the
entrypoint's signal forwarding never reaches the worker.

## 5. The health URL

```bash
docker exec loxmatter-updater curl -fsS -m 3 http://host.docker.internal:8080/health
```

Anything but a JSON body means every update reaches the health phase, waits
120 seconds and rolls back a release that is working fine.

## 6. Socket and compose

```bash
docker exec loxmatter-updater docker compose version
docker exec -w /repo/deploy/testhost loxmatter-updater docker compose ps
```

Socket permission errors and unset `.env` variables surface here rather than
mid-update.

## 7. Running-version detection

```bash
docker exec loxmatter-updater docker inspect loxmatter \
  --format '{{range .Config.Env}}{{println .}}{{end}}' | grep LOXMATTER_VERSION
```

Empty or `dev` means every stable-channel request is rejected as "does not
state a version".

## 8. Backup source and heartbeat

```bash
docker exec loxmatter-updater ls -l /data/loxmatter.sqlite
docker exec loxmatter cat /data/update/state.json; sleep 3
docker exec loxmatter cat /data/update/state.json
```

`updater_seen_at` must advance, `phase` must read `idle`. Then open System in
a browser: the version and either "up to date" or an offer must appear.
"This installation has no updater" here means the bridge's update directory
and the sidecar's are not the same directory.

## 9. Prove the security boundary, without changing anything

```bash
docker exec loxmatter sh -c 'printf "{\"id\":\"probe-1\",\"channel\":\"stable\",\"target\":\"0.0.1\"}" > /data/update/request.json.tmp && mv /data/update/request.json.tmp /data/update/request.json'
docker exec loxmatter-updater cat /data/update/log.txt
```

Expect `phase: rejected`, an error about an older version, and **no**
`docker compose` line in the log at all. A rejection that already did
something is not a rejection.

## 10. The real update, watched from the console

Terminal A: `docker exec loxmatter tail -f /data/update/log.txt`
Terminal B: `while :; do docker exec loxmatter-updater cat /data/update/state.json; echo; sleep 1; done`

Then press the button. Watch both terminals as well as the browser — the
point of this run is to compare what the card claims against what the sidecar
is actually doing.

## 11. After it says done

```bash
docker inspect loxmatter --format '{{json .Mounts}}'
docker inspect loxmatter-updater --format '{{json .Mounts}}'
sudo ls -la /repo 2>/dev/null
git -C ~/loxmatter status
find ~/loxmatter -user root | head
grep LOXMATTER_IMAGE_TAG ~/loxmatter/deploy/testhost/.env
```

A stray `/repo` on the host means the path trap fired after all. The checkout
will be detached, and `.env` will now name a concrete version rather than
`stable` — both expected, both consequences worth knowing about.

## 12. The rollback drill, deliberately, before you need it

```bash
cd ~/loxmatter/deploy/testhost
sed -i 's/^LOXMATTER_IMAGE_TAG=.*/LOXMATTER_IMAGE_TAG=0.2.0/' .env
docker compose up -d --no-deps --force-recreate loxmatter
git -C ~/loxmatter checkout main
```

This is the deliberate way back — "0.3.0 works but I want 0.2.0" — as opposed
to the automatic rollback, which only fires when a new version fails to become
healthy. Verify it works while nothing is wrong.

## 13. The health-failure drill

Recreate the sidecar with `LOXMATTER_HEALTH_URL` pointed at a dead port and
`LOXMATTER_HEALTH_TIMEOUT=20`, then run an update. Confirm three things: the
old image comes back on its own; `LETZTER-FEHLSCHLAG.txt` exists; and the
`cd …` commands printed inside that file are actually runnable in your host
shell. The last one is the whole reason `host_path_for()` exists.

## Known limitations this release ships with

- **The development channel is hidden.** It cannot work end to end yet: the
  check returns `main`, the worker's pattern requires a commit hash, and CI
  publishes `:dev` and `:sha-<short>` rather than `:<sha>`. The setting, the
  store, the API and the check logic are all in place behind it.
- **The console fallbacks break after the first browser update.** The sidecar
  leaves the checkout detached, so `git pull --ff-only` — printed in the
  README, in OPERATIONS and in the no-updater hint — fails with "You are not
  currently on a branch". Step 12 above is the workaround.
- **The rollback message names the `.env` tag, not the version restored.**
  The rollback itself pins the concrete running version, and
  `LETZTER-FEHLSCHLAG.txt` reports it correctly; only the card's wording is
  wrong.
- Two failure paths leave the checkout on the new ref while the old image
  runs. The container is unchanged, the compose file is not.

## 14. Radios (design "Radios in the Web UI", 2026-09-11)

0. Copy the stack's `.env` aside by hand before the first radio job.
1. After installing a build with this feature, refresh the sidecar from the
   console once. Before the refresh the Radios card is read-only and says
   what to run; afterwards it shows the selects.
2. The card lists the Thread stick and the Bluetooth adapter, both "in use".
3. Send an invalid Bluetooth index through the API
   (`POST /api/radios` with `{"thread": {…current…}, "bluetooth": {"adapter": 9}}`):
   400 from the bridge. Then go around the bridge and write a request
   straight into the update directory as `radios-request.json`, to prove
   the sidecar rejects it on its own:

   ```json
   {"id": "pi-check-3", "thread": null, "bluetooth": {"adapter": 9},
    "requested_at": "2026-09-13T10:00:00Z"}
   ```

   The sidecar rejects it with `bluetooth_adapter_not_found`, and no
   container restarts (compare
   `docker ps --format '{{.Names}} {{.RunningFor}}'` before and after).

   Write that body, not the POST body from the first half of this step.
   The sidecar's schema check is a strict whitelist over the exact key set
   `["bluetooth", "id", "requested_at", "thread"]`; a POST body carries
   neither `id` nor `requested_at` (the bridge adds both when it writes
   the file), so pasting it here is rejected as `request_malformed`. That
   still demonstrates "rejected without effect", but not the
   host-validation check this step is for — and the difference costs a
   hardware session to notice. Use a fresh `id` on each repeat: an id
   already in `radios-handled/` is ignored rather than judged again.
4. **Leaves the other radio alone.** Write `radios-request.json` directly
   with `{"thread": null, "bluetooth": {"adapter": 0}}` (the current
   adapter, or the only one present): the job reports `unchanged` for the
   Bluetooth half and does not restart `matter-server`, and `otbr`'s
   `RunningFor` is identical before and after — a `null` half is never
   validated or applied. This is what the design added on 12 September
   2026; before it, any radio request force-recreated `otbr` as well.
5. **A `null` Thread half tolerates an unplugged stick.** Unplug the
   configured stick, then send the same `{"thread": null, "bluetooth":
   {"adapter": 0}}` request: it still ends `unchanged`/`done` rather than
   being rejected for a missing device, because a `null` half is never
   checked against the host.
6. **Interrupts Thread devices — agree a time first.** Apply the same stick
   by its by-id path (the installer wrote `/dev/ttyUSB0`). `otbr` is
   recreated, `docker exec otbr ot-ctl state` reports `leader`, and the
   Thread devices deliver values again within two minutes, read through the
   running bridge.
7. **Interrupts Thread devices.** Switch Thread off, then on again: `otbr`
   disappears and returns, the devices with it.
8. Apply without a change: "Nothing to change", no restart. A real
   Bluetooth adapter switch cannot be exercised on a host with one adapter;
   record that instead of counting it as passed.

## 15. Zigbee (design "Zigbee as the Second Device Source", 2026-09-12)

**Nothing in this feature has been exercised against Zigbee hardware.** Every
timing, every reporting interval and every device behaviour it relies on was
read from ZHA's and zigpy's source or measured against a silent pty, never
against a radio that answered. None of it counts as verified until the steps
below have been run, and a step that could not be run is recorded as not run,
not as passed.

The hardware, read on the Pi on 12 September 2026:

| Role | `/dev/serial/by-id/` name | Node |
|---|---|---|
| Thread (running, `RADIO_DEVICE=/dev/ttyUSB0`) | `usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a7d9118f9ef118f7767135c2a50c9-if00-port0` | `ttyUSB0` |
| Zigbee (new, never opened by loxmatter) | `usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf16ad5953ef11844a28e0174bec31-if00-port0` | `ttyUSB1` |

Both sticks report `10c4:ea60` (CP210x) and both are major 188, so neither the
USB ids nor the cgroup rule tells them apart; only the by-id name and the
resolved minor do. **The order below is not cosmetic: the Thread lock-out is
proven before anything opens a port**, because a wrong pick garbles the live
Thread network. Every step says what to do, what to expect and what to
record. A step that can interrupt Thread says so before it starts.

For the API calls, `TOKEN` is `LOXMATTER_API_TOKEN` from
`deploy/testhost/.env`, sent as `Authorization: Bearer $TOKEN`.

### 15.0 A known blocker

Read from the code and the compose file, not run: `cli._run` puts zigpy's
database at `<--matter-data-dir>/zigbee.sqlite`, and the compose file passes
`--matter-data-dir /matter-data` while mounting `./data:/matter-data:ro`
into the bridge. As deployed, zigpy cannot create its database, and 15.7
would fail with "The Zigbee radio could not be started: …". The path has to
move to a writable place (the design put it at `/data/zigbee.sqlite`, in the
bridge's own volume) in the code or the compose file before this section is
run. 15.2 checks that the build under test has that fix.

### 15.1 Baseline, changing nothing

- **Do:**

  ```bash
  ls -l /dev/serial/by-id/ /dev/ttyUSB*
  grep -E '^(RADIO_DEVICE|COMPOSE_PROFILES)=' ~/loxmatter/deploy/testhost/.env
  docker exec otbr ot-ctl state
  docker ps --format '{{.Names}} {{.RunningFor}}'
  ```

- **Expect:** the two by-id names above, `crw-rw---- … 188, 0` for
  `ttyUSB0` and `188, 1` for `ttyUSB1`, `RADIO_DEVICE=/dev/ttyUSB0`, and
  `leader`.
- **Record:** the minors, the otbr state, and every container's
  `RunningFor`. If the minors are swapped after a reboot, every later step
  still names the sticks by by-id, never by `ttyUSB` number.

### 15.2 Install the build on the bridge only

- **Do:** with the commit under test checked out,
  `cd ~/loxmatter && ./scripts/update.sh --build`. The script recreates only
  the `loxmatter` service (`--no-deps`), so it picks up the new
  `device_cgroup_rules` without recreating otbr. Do **not** run a bare
  `docker compose up -d`: the otbr environment changed (`&uart-exclusive`)
  and that would recreate the border router now, before 15.3.
- **Expect:** the bridge healthy, otbr's `RunningFor` unchanged, no Zigbee
  stick configured. The directory the build now puts `zigbee.sqlite` in is
  writable from inside the bridge (15.0): for example
  `docker exec loxmatter sh -c 'touch <that directory>/zigbee-probe && rm <that directory>/zigbee-probe'`
  succeeds. `Read-only file system` means 15.0 is not fixed in this build;
  stop.
- **Record:** otbr's `RunningFor` before and after; the database directory
  and the probe's result; `docker inspect loxmatter --format
  '{{json .HostConfig.DeviceCgroupRules}}'` showing both rules.

### 15.3 The Thread lock-out, before anything opens a port

No Zigbee stick is stored at this point, so nothing in the bridge opens a
serial port: listing sticks reads `/sys` and the by-id names only.

1. **Do:** `curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/radios`.
   **Expect:** `current` is not `null`, with `"thread_enabled": true`,
   `"otbr_running": true` and `thread_device` naming the MG24 by-id path.
   **Stop if not**: the lock-out is gated on the sidecar's report that
   Thread is running, and without that report the MG24 would be offered for
   Zigbee. **Record:** `current` as printed.
2. **Do:** `curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/zigbee/radio`.
   **Expect:** `configured_path` is `null`, `progress.state` is `idle`; the
   MG24 entry has `"is_thread": true, "selectable": false`; the ITEAD entry
   has `"is_thread": false, "selectable": true` and the fingerprint
   `SONOFF ZBDongle-E V2`, `ezsp`, 115200. **Record:** both entries as
   printed.
3. **Do:** open Settings → Radios in the browser, in English and in German.
   **Expect:** in the Zigbee row the MG24 is listed as "in use for Thread"
   (`für Thread in Verwendung` in German) and cannot be picked; the line
   under the select says why and how to free it; the ITEAD stick is
   offered. Do not press Apply. **Record:** a screenshot of each language.
4. **Do:** send the refused request past the card:

   ```bash
   curl -s -X PUT -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"path": "/dev/serial/by-id/usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a7d9118f9ef118f7767135c2a50c9-if00-port0"}' \
     -w '\n%{http_code}\n' http://localhost:8080/api/zigbee/radio
   ```

   **Expect:** `400` with "This stick is in use for Thread …". Then
   `GET /api/zigbee/radio` still shows `configured_path: null`,
   `docker exec otbr ot-ctl state` still `leader`, otbr's `RunningFor`
   unchanged. **Record:** status, detail, otbr state and `RunningFor`.

### 15.4 otbr with `&uart-exclusive`, and the recreate timing

**Interrupts Thread devices — agree a time first.** This recreates the
border router.

- **Do:**

  ```bash
  cd ~/loxmatter/deploy/testhost
  date +%s; time docker compose up -d --force-recreate --no-deps otbr; date +%s
  while ! docker exec otbr ot-ctl state 2>/dev/null | grep -q leader; do sleep 1; done; date +%s
  docker inspect otbr --format '{{range .Config.Env}}{{println .}}{{end}}' | grep RADIO_URL
  ```

- **Expect:** otbr starts, `RADIO_URL` ends in `&uart-exclusive`, the state
  reaches `leader`, and Thread devices deliver values again, read through the
  running bridge. If otbr refuses to start with the parameter, remove it from
  the compose file, recreate again, and record that the installed image does
  not accept it (design open point 5).
- **Record:** whether the image starts with the parameter or refuses it.
  Whether it actually takes the lock is not tested here: that would mean
  opening the Thread stick from a second process, which is what this section
  exists to avoid. Also record the wall-clock seconds of the `compose up`
  itself and until `leader`. The second figure is the heartbeat measurement
  the radios sidecar is waiting for: its job refreshes the heartbeat only before and after each compose
  call, and the card treats about 50 s of silence (30 s
  `_MAX_SILENT_SECONDS` plus 20 s `RADIOS_STALL_GRACE_MS`) as an abandoned
  job. A recreate on this SD card that comes close to 50 s means that window
  has to grow, or compose has to run with the heartbeat alongside it.

### 15.5 The container can open the Zigbee stick at all

This is the first step that opens a serial port, and it opens the ITEAD
stick only. Never point it at `ttyUSB0`.

- **Do:**

  ```bash
  ITEAD=/host/dev/serial/by-id/usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf16ad5953ef11844a28e0174bec31-if00-port0
  docker exec loxmatter python -c "import os; fd = os.open('$ITEAD', os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK); os.close(fd); print('opened')"
  docker exec loxmatter python -c "import os; os.open('/host/dev/mmcblk0', os.O_RDONLY)"
  ```

  (`mmcblk0` is the SD card; use `sda` if the Pi boots from USB.)

- **Expect:** `opened` for the stick; `PermissionError` for the SD card,
  because the cgroup rule does not reach block devices (the README states
  this to users).
- **Record:** both outputs. `PermissionError` on the stick means the cgroup
  rule did not reach the container; go back to 15.2.

### 15.6 The stick's firmware, before the bridge opens it

The ITEAD stick must run EZSP coordinator (NCP) firmware. The bridge will
not tell you which version it runs: bellows reports the stack version only
at DEBUG level, on the `bellows.ezsp` logger ("EZSP Stack Type: …, Stack
Version: …, Protocol version: …"), and loxmatter logs at INFO.

- **Do:** record the firmware version from whatever you already know about
  this unit (the vendor's release it shipped with, or a flashing tool you
  have used on it). Do not flash anything as part of this session.
- **Expect:** an EZSP NCP build. If it is unknown, 15.7 is the test: a stick
  on the wrong firmware fails with "This stick does not answer as a Zigbee
  coordinator …".
- **Record:** the version, or "unknown".

### 15.7 Choose the stick in the web UI, with Thread running

Thread stays up throughout this step; if it does not, that is the finding.

- **Do:** note otbr's and matter-server's `RunningFor`. In Settings → Radios,
  pick the ITEAD stick in the Zigbee row and press its Apply. Watch the row
  while it works, then read `docker logs loxmatter`.
- **Expect:** the row steps through "Applying the change", "Preparing device
  support - this can take a few seconds", "Opening the stick", "Connected".
  No other container restarts; Thread and Matter devices keep delivering
  values the whole time. The log carries
  `zigbee quirks registry loaded in N s`. In the Thread row's select the
  ITEAD stick is now marked "in use for Zigbee".
- **Record:** the `N` from that log line, against the design's extrapolated
  9–15 s for a Pi 4 (`zhaquirks.setup()`, design section 8.4); the time from
  Apply to "Connected"; `RunningFor` of otbr and matter-server before and
  after; the failure text and attempt count if it does not connect.

Then the reverse lock-out, which would recreate otbr on the wrong stick if it
failed. **Can interrupt Thread devices if the guard is broken — agree a time
first.**

- **Do:**

  ```bash
  curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"thread": {"enabled": true, "device": "/dev/serial/by-id/usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf16ad5953ef11844a28e0174bec31-if00-port0"}, "bluetooth": null}' \
    -w '\n%{http_code}\n' http://localhost:8080/api/radios
  ```

- **Expect:** `400` from the bridge, refusing the Zigbee stick; no
  `radios-request.json` written; otbr's `RunningFor` unchanged. A `503`
  means the sidecar was not ready, so the guard was never reached and
  nothing is proven; repeat once it is.
- **Record:** status, detail and `RunningFor`.

### 15.8 Pair a lamp and a sensor

- **Do:** in the Devices view, open the Zigbee tab. Reset the lamp (switch it
  off and on five to ten times), press **Search for devices**, and watch the
  row. Press **Add**, keep or change the name, pick a room. Repeat with a
  battery sensor, pressing its button until the LED blinks. Then open the
  export and generate the lamp's template.
- **Expect:** a countdown "Open for new devices: 4:1x left"; a row that goes
  from found, to reading its details, to setting it up, to "Ready - add it to
  your devices"; after **Add** the device in the device list with its Zigbee
  badge and in the export with its signals. A battery sensor may show "No
  progress for a while …" or "Waiting for the device to wake up - press its
  button"; pressing the button moves it on. Leaving the tab with the window
  open closes it.
- **Record:** manufacturer and model of each device; whether the row said
  "Device-specific support (quirk) applied" or "No device-specific support
  (quirk) …"; the time from Search to Ready for each; any row that stayed
  stuck; whether the device and its signals appear in the export.

### 15.9 Reporting arrives at the configured intervals

- **Do:** with the System tab's UDP capture open, switch the lamp from Loxone
  or the web UI, and change it by hand (wall switch or remote) if possible.
  Leave it untouched for 20 minutes. Warm the sensor in your hand.
  `docker logs loxmatter 2>&1 | grep -E 'refused reporting|deferred until'`.
- **Expect:** on/off and level changes within a second or two; temperature
  and humidity changes within about 30 s of crossing 0.5 °C or 1 % (design
  section 6.2: min 30 s, max 900 s). No "refused reporting … polling it
  instead" line; if there is one, that cluster is polled every 2700–4500 s
  instead.
- **Record:** the delay per signal, every `refused reporting` or `deferred`
  line, and whether the lamp or sensor went offline in the UI during the
  quiet 20 minutes (it must not).

### 15.10 IAS enrolment, end to end

- **Do:** pair a contact sensor, add it, then open and close it several
  times with the UDP capture open.
- **Expect:** each opening and closing reaches Loxone as a change of its
  contact signal. A sensor that pairs, reports its battery and never reports
  opening is exactly the failure this step exists for.
- **Record:** the sensor's model, the signal's value when open and when
  closed, and the delay.

### 15.11 `ExecuteIfOff` on the test lamp

- **Do:** switch the lamp off. From the web UI, change its colour
  temperature (and colour, if it has colour) while it is off, then switch it
  on. Then, with the lamp off again, send it a colour from Loxone, which
  carries the brightness in the same value.
- **Expect:** both times it comes on in the new setting. If it comes on in
  the old one, the lamp ignores the Options fields and needs ZHA's
  turn-on-first workaround, which belongs at the Zigbee edge, not in
  `translate.py` (design section 5.7).
- **Record:** the lamp's manufacturer and model, and for each try which of
  the two happened.

### 15.12 An illuminance sensor's min/max rows

Only if a lux sensor is available. `IlluminanceMeasurement`
`min_measured_value` / `max_measured_value` (0x0400/0x0001, /0x0002) were
deliberately left out of the sentinel table in `zigbee/translate.py`: the ZCL
marks an undefined bound with 0x0000 there, and nothing in the installed
libraries confirms which value a real sensor sends.

- **Do:** pair it, add it, open its signals.
- **Expect:** a measured value that follows the light. The min and max rows
  may be absent, 0, or 65535.
- **Record:** the raw values of both bounds, so the table can be settled.

### 15.13 Matter's BooleanState polarity

This one needs no Zigbee device. MYGGBETT (contact) and KLIPPBOK (water leak)
are already commissioned.

- **Do:** find their ids with `GET /api/devices`. Put MYGGBETT in a known
  state (magnet on, closed) and read
  `curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/devices/<id>/signals`;
  open it and read again. Do the same for KLIPPBOK, dry and then wet.
  Read through the running instance's `signals` route only, never through a
  fresh `snapshots()` call, which returns matter-server's cache.
- **Expect:** the design assumes `x/69/0` is true when a contact is closed
  (design section 5.2), the inverse of IAS; that is read from the Matter
  data model, not measured.
- **Record:** the `x/69/0` value in each physical state, for both devices.

### 15.14 Rollback

- **Do:** in Settings → Radios, set the Zigbee row to "No Zigbee stick" and
  apply.
- **Expect:** the row reads "No Zigbee stick" and the Zigbee tab is no
  longer offered in the Devices view; Thread and Matter devices are
  unaffected and no container restarts.
- **Record:** `RunningFor` of every container before and after, and what the
  Zigbee devices' tiles show.

### What this section does not cover

Deferred to 2b (design section 11): coordinator replacement, network backup
and its download, a rejoin blocklist, Metering and ElectricalMeasurement
scaling, remotes and buttons, a per-device "Reconfigure device" button, the
zigpy database in the updater's pre-update backup, stick probing and
firmware flashing, joining through a specific router, install codes, and
native Matter groups. Also open: every binding and the IAS `cie_addr` point
at the current coordinator's IEEE, so a new coordinator means configuring
every device again (design section 12, point 6), and what a second loxmatter
instance on this Pi should do with the same stick (point 7).

Found while building it, not yet done: a `loxmatter zigbee clear` command
(`--zigbee-device` only seeds the stored setting now, so a wrong setting
behind an unreachable web UI has no console way out); the pytest collision
between `tests/api` and `tests/projectsync`, which both carry a
`conftest.py`; and comments in `zigbee/source.py` and `zigbee/runtime.py`
that still name plan task numbers.
