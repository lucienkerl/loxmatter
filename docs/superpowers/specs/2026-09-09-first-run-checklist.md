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

### 15.0 Where zigpy's database lands

zigpy keeps its network database, `zigbee.sqlite`, in the same directory as
the bridge's own store: `/data/zigbee.sqlite` in the `loxmatter-store`
volume, beside `/data/loxmatter.sqlite`. The first build put it under
`--matter-data-dir`, which this compose file mounts read-only; that was fixed
before this section was run (`922a21f`), and
`tests/test_compose_profiles.py` now refuses a read-only mount for it in
every compose file. The bridge image has no `USER` line, so the bridge runs
as root and writes to that volume the way it already writes the store.

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
  `docker compose up -d`: otbr's `RADIO_URL` is now written with
  `${OTBR_RADIO_URL_EXTRA:-}` at its end, and although that resolves to the
  URL otbr already runs while the variable is unset, nobody has watched
  Compose decide that on the Pi - a bare `up` could recreate the border
  router now, before 15.3.
- **Expect:** the bridge healthy, otbr's `RunningFor` unchanged, no Zigbee
  stick configured, and no `/data/zigbee.sqlite` yet
  (`docker exec loxmatter ls /data`).
- **Record:** otbr's `RunningFor` before and after; the `ls /data` output;
  `docker inspect loxmatter --format '{{json .HostConfig.DeviceCgroupRules}}'`
  showing both rules.

### 15.3 The Thread lock-out, before anything opens a port

No Zigbee stick is stored at this point, so nothing in the bridge opens a
serial port: listing sticks reads `/sys` and the by-id names only.

1. **Do:** `curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/radios`.
   **Expect:** `current` is not `null`, with `"thread_enabled": true`,
   `"otbr_running": true` and `thread_device` naming the MG24 by-id path.
   **Stop if not**: the lock-out compares every stick with this report.
   Without a current one the bridge refuses to choose any stick at all
   (step 5 checks that), so nothing later in this section can be run, and
   a `current` naming the wrong stick would lock the wrong one.
   **Record:** `current` as printed.
2. **Do:** `curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8080/api/zigbee/radio`.
   **Expect:** `thread_status` is `known` and `thread_refusal` is `null`;
   `configured_path` is `null`, `progress.state` is `idle`, `coordinator`
   is `null`; the MG24 entry has `"is_thread": true, "selectable": false`;
   the ITEAD entry has `"is_thread": false, "selectable": true` and the
   fingerprint `SONOFF ZBDongle-E V2`, `ezsp`, 115200. **Record:** both
   entries and `thread_status` as printed.
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
5. **The fail-safe without a report.** Stopping the updater service does
   not touch Thread or the bridge; do it only while no update or radio
   change is running.
   **Do:** `docker stop loxmatter-updater`, wait 40 s (the report counts as
   current for 30 s), then repeat step 2, open the Radios card, and send
   the ITEAD stick past the card with step 4's command and the ITEAD path.
   Then `docker start loxmatter-updater` and wait until step 2 shows
   `thread_status: known` again.
   **Expect:** `thread_status` is `unknown` and BOTH entries have
   `"selectable": false`, the MG24 still `"is_thread": true` (the old report
   still names it); the card shows "The bridge cannot currently tell which
   stick Thread is using …" under the select, both sticks cannot be picked,
   "No Zigbee stick" can; the `PUT` answers `503` with that sentence and
   nothing is stored. **Record:** the `GET` body, the status and detail, a
   screenshot of the card, and how long after `docker start` the status
   read `known` again.

### 15.4 otbr with `&uart-exclusive`, opted into, and the recreate timing

`&uart-exclusive` asks OpenThread to take `flock` + `TIOCEXCL` on its stick.
It is **opt-in**: the compose file appends `OTBR_RADIO_URL_EXTRA` from `.env`
to the radio URL, and the variable is unset on every installation. Whether
the installed otbr image accepts the parameter is what this step measures
(design open point 5); only when it does is the opt-in worth recommending.
This is also the one step that measures how long an otbr recreate takes on
this SD card.

> **Warning: this step interrupts Thread.** It recreates the border router,
> and every Thread device goes quiet until the network is `leader` again. If
> the image refuses the parameter, Thread stays down until the rollback below
> has recreated otbr a second time. Agree a time first, keep this terminal
> open until Thread is back, and read the rollback lines before you start.

- **Do:**

  ```bash
  cd ~/loxmatter/deploy/testhost
  cp .env .env.before-uart-exclusive
  grep -q '^OTBR_RADIO_URL_EXTRA=' .env || echo 'OTBR_RADIO_URL_EXTRA=&uart-exclusive' >> .env
  grep '^OTBR_RADIO_URL_EXTRA=' .env
  date +%s; time docker compose up -d --force-recreate --no-deps otbr; date +%s
  timeout 180 sh -c 'until docker exec otbr ot-ctl state 2>/dev/null | grep -q leader; do sleep 1; done'; date +%s
  docker exec otbr ot-ctl state
  docker inspect otbr --format '{{range .Config.Env}}{{println .}}{{end}}' | grep RADIO_URL
  docker exec otbr sh -c 'for p in /proc/[0-9]*/cmdline; do tr "\0" " " < "$p"; echo; done' | grep '[o]tbr-agent'
  ```

  If the state is not `leader` within 180 s, apply the pid-file fix the
  radios job uses for this image, once, and wait again:
  `docker exec otbr rm -f /run/otbr-agent.pid && docker restart otbr`.

- **Expect:** `OTBR_RADIO_URL_EXTRA=&uart-exclusive` printed once;
  `RADIO_URL` ending in `?uart-baudrate=460800&uart-exclusive`; an
  `otbr-agent` command line carrying the same URL; the state reaching
  `leader`; Thread devices delivering values again, read through the running
  bridge.
- **Rollback, if otbr does not start, the agent is not running, or the state
  does not reach `leader`:**

  ```bash
  cd ~/loxmatter/deploy/testhost
  cp .env.before-uart-exclusive .env
  docker compose up -d --force-recreate --no-deps otbr
  timeout 180 sh -c 'until docker exec otbr ot-ctl state 2>/dev/null | grep -q leader; do sleep 1; done'
  docker exec otbr ot-ctl state
  ```

  with the same pid-file fix if it hangs. Record that the installed image
  does not accept the parameter, and leave `OTBR_RADIO_URL_EXTRA` unset.
- **Record:** whether the image starts with the parameter or refuses it (the
  otbr log, `docker logs otbr --tail 50`, if it refuses); the agent's command
  line; whether the rollback was needed. Whether the lock is actually taken
  is not tested here: that would mean opening the Thread stick from a second
  process, which is what this section exists to avoid. Also record the
  wall-clock seconds of the `compose up` itself and until `leader`. The
  second figure is the heartbeat measurement the radios sidecar is waiting
  for: its job refreshes the heartbeat only before and after each compose
  call, and the card treats about 50 s of silence (30 s
  `_MAX_SILENT_SECONDS` plus 20 s `RADIOS_STALL_GRACE_MS`) as an abandoned
  job. A recreate on this SD card that comes close to 50 s means that window
  has to grow, or compose has to run with the heartbeat alongside it.

### 15.5 The container can open the Zigbee stick at all

This is the first step that opens a serial port, and it opens the ITEAD
stick only. Never point it at `ttyUSB0`.

The bridge's container has no `/dev/serial/by-id` of its own: its `/dev` is
Docker's private one, and the host's `/dev` is mounted read-only at
`/host/dev`. The card lists and stores the host's path, and the bridge opens
the same name under `/host/dev` (`ZigbeeSource._open_path`). Whether a
character device can be opened for reading and writing through that
read-only bind is **not established by anything in this repository** - it is
what this step shows, on this kernel and this Docker.

- **Do:**

  ```bash
  docker exec loxmatter ls /dev/serial/by-id
  docker exec loxmatter grep ' /host/dev ' /proc/mounts
  ITEAD=/host/dev/serial/by-id/usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf16ad5953ef11844a28e0174bec31-if00-port0
  docker exec loxmatter python -c "import os; fd = os.open('$ITEAD', os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK); os.close(fd); print('opened')"
  docker exec loxmatter python -c "import os; os.open('/host/dev/mmcblk0', os.O_RDONLY)"
  ```

  (`mmcblk0` is the SD card; use `sda` if the Pi boots from USB.)

- **Expect:** `ls: cannot access '/dev/serial/by-id': No such file or
  directory` - the host path does not exist inside the container, which is
  why the bridge must not open it; `opened` for the stick through
  `/host/dev`; `PermissionError` for the SD card, because the cgroup rule
  does not reach block devices (the README states this to users).
- **Record:** all four outputs, the mount options from `/proc/mounts`
  (`ro`, and whether `nodev` is among them) included. If the stick does not
  print `opened`, record the exact error: `PermissionError` means the cgroup
  rule did not reach the container (go back to 15.2); `OSError: [Errno 30]
  Read-only file system` or a `PermissionError` with `nodev` in the mount
  options means the read-only bind itself refuses the open, and 15.7 cannot
  succeed until the mount changes - stop there and record it.

### 15.6 The stick's firmware

The ITEAD stick must run EZSP coordinator (NCP) firmware. bellows reports
the stack version only at DEBUG level, so the bridge reads it back itself:
after every successful connect it logs one INFO line, reports the same in
`coordinator` from `GET /api/zigbee/radio`, and shows "Firmware: <version>"
under the Zigbee row. The line reaches `docker logs loxmatter` (the bridge
writes its own log to stderr) and reads

```text
INFO:     loxmatter.zigbee.source: Zigbee coordinator connected on /dev/serial/by-id/usb-Itead_…-if00-port0: radio type ezsp, firmware <version>, manufacturer …, model …; network on channel <n>, PAN ID 0x…, extended PAN ID …, <whether the database knew it>
```

If `docker logs` does not show it, read the System tab's live log instead.
That log keeps the last 500 lines only, so read it soon after the connect.

- **Do:** nothing yet; do not flash anything as part of this session. If
  you know the version from the vendor's release or a flashing tool, note
  it now, so 15.7 can compare.
- **Expect:** 15.7 connects and the connect line names an EZSP NCP build. A
  stick on the wrong firmware fails 15.7 with "This stick does not answer
  as a Zigbee coordinator …" instead, and there is no such line.
- **Record:** the version you already knew, or "unknown"; 15.7 records what
  the bridge read.

### 15.7 Choose the stick in the web UI, with Thread running

Thread stays up throughout this step; if it does not, that is the finding.

- **Do:** note otbr's and matter-server's `RunningFor`, the Thread channel
  (`docker exec otbr ot-ctl channel`), whether the ITEAD stick has ever
  carried a Zigbee network before (a stick fresh from the box has not), and
  the time (`date -u +%FT%TZ`) right before pressing Apply - call it
  `$APPLY_TIME`. In Settings → Radios, pick the ITEAD stick in the Zigbee
  row and press its Apply. Watch the row while it works, then run

  ```bash
  docker logs --since "$APPLY_TIME" loxmatter 2>&1 | grep -E 'source zigbee|zigbee quirks registry loaded in|Zigbee coordinator connected on'
  docker logs --since "$APPLY_TIME" loxmatter 2>&1 | grep -E 'WARNING|Traceback'
  docker exec loxmatter ls -l /data
  ```

  The first grep matches only its three named patterns and would silently
  hide a WARNING or a traceback on any other line - it is what shows the
  four lines below, not what checks for their absence. The second grep,
  scoped to the same window, is what actually checks it.

- **Expect:** the row steps through "Applying the change", "Preparing device
  support - this can take a few seconds", "Opening the stick", "Connected",
  then shows "Firmware: …". No other container restarts; Thread and Matter
  devices keep delivering values the whole time. The first grep's log
  carries, in this order:

  ```text
  INFO:     loxmatter.sources.supervisor: source zigbee is not connected yet - connecting it
  INFO:     loxmatter.zigbee.quirks: zigbee quirks registry loaded in N s
  INFO:     loxmatter.zigbee.source: Zigbee coordinator connected on /dev/serial/by-id/usb-Itead_…: radio type ezsp, firmware …, manufacturer …, model …; network on channel C, PAN ID 0x…, extended PAN ID …, not in this bridge's database before (formed now, or adopted from the stick)
  INFO:     loxmatter.sources.supervisor: connection of source zigbee restored (0 commands backfilled)
  ```

  and the second grep, checking for exactly the WARNING or traceback the
  first one would have hidden, prints nothing.

  `C` is one of 11, 15, 20 and 25 and is not the Thread channel - but only
  when the network was FORMED just now (a stick fresh from the box, or one
  whose history has never carried a Zigbee network before). A stick that
  instead already had a network keeps ADOPTING it here, on whatever channel
  that network was already using: not necessarily from that list, and not
  necessarily different from the Thread channel - zigpy does not say which
  of the two happened, which is why the Record paragraph below reads a
  channel equal to Thread's as evidence of adoption rather than expecting
  one outcome or the other here. In the Thread row's select the ITEAD stick
  is now marked "in use for Zigbee".
  `/data/zigbee.sqlite` exists, but that alone proves nothing: zigpy creates
  the database before it opens the port, so it also appears when the connect
  fails. If `docker logs` shows none of these lines, read the System tab's
  live log; it keeps 500 lines, so do it right after the connect.
- **Record:** the `N` from the warm-up line, against the design's
  extrapolated 9–15 s for a Pi 4 (`zhaquirks.setup()`, design section 8.4);
  the whole `Zigbee coordinator connected` line (the firmware for 15.6); the
  network's channel and the Thread channel; whether the network was formed
  or adopted. zigpy does not tell the bridge which: read it from the stick's
  history - a stick that never carried a network forms one - and treat a
  channel equal to the Thread channel as adopted, because a network formed
  here leaves that channel out. Also the time from Apply to "Connected";
  `RunningFor` of otbr and matter-server before and after; the failure text,
  attempt count and the first `rebuild of source zigbee failed` line with
  its traceback if it does not connect.

Then the stored stick after a restart with a stale report - the case of a
Pi rebooting and the bridge coming up before the updater service. **Restarts
the bridge; Matter values pause for its restart, Thread is not touched.**

- **Do:** `docker stop loxmatter-updater`, wait 40 s, `docker restart
  loxmatter`, and watch the Zigbee row. Then `docker start
  loxmatter-updater`.
- **Expect:** the ITEAD stick connects again without the updater service
  running: the report on disk is stale but still names the MG24 as Thread's
  and not the ITEAD stick. The log carries a fresh `Zigbee coordinator
  connected` line, on the same channel and PAN ID as before, ending in
  `already in this bridge's database`. System tab's live log if `docker logs`
  shows nothing.
- **Record:** whether and how fast it connected, and the row's text while it
  did.

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
  stuck; whether the device and its signals appear in the export. For a
  lamp, which colour outputs the export offers (`color`, `color_xy`, or
  none) and whether its Control dialog shows one colour area, none, or two:
  a tunable-white lamp must show none, a colour lamp exactly one, and a
  lamp that takes colour only as XY one whose marker is missing, with the
  "Start value unknown" note. A lamp that declares XY and a colour
  temperature but no hue and saturation (an RGBCCT controller) shows one
  colour area only if it declares itself an Extended Color Light. For every
  device, also record the category its tile shows (a lamp or plug must not
  read "Other") and whether it can be put into a group with a Matter lamp or
  plug of the same kind.

### 15.9 Reporting arrives at the configured intervals

- **Do:** with the System tab's UDP capture open, switch the lamp from Loxone
  or the web UI, and change it by hand (wall switch or remote) if possible.
  Leave it untouched for 20 minutes. Warm the sensor in your hand. Then

  ```bash
  docker logs loxmatter 2>&1 | grep -E 'configuration of .* finished|refused reporting|deferred until'
  ```

- **Expect:** on/off and level changes within a second or two; temperature
  and humidity changes within about 30 s of crossing 0.5 °C or 1 % (design
  section 6.2: min 30 s, max 900 s). For each device paired in 15.8, one
  `INFO:     loxmatter.zigbee.configure: configuration of <ieee> finished:
  clusters configured [...], deferred [...]` line whose configured list
  names its clusters. No "refused reporting … polling it instead" line; if
  there is one, that cluster is polled every 2700–4500 s instead.
  **An empty result is not a pass.** Without the `finished` line nothing
  shows that reporting was configured at all - the log may simply not be
  reaching `docker logs`. Read the System tab's live log for the same three
  phrases then (it keeps 500 lines; pairing may already have scrolled out),
  and record the step as not proven if the `finished` line is in neither.
- **Record:** the delay per signal, every `configuration of … finished`,
  `refused reporting` and `deferred` line, and whether the lamp or sensor went
  offline in the UI during the quiet 20 minutes (it must not).

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
  unaffected and no container restarts. Every Zigbee device's tile shows it
  offline, and the UDP capture shows `d<id>_online` 0 for each of them, at
  the moment of the apply rather than after the next restart.
- **Record:** `RunningFor` of every container before and after, what the
  Zigbee devices' tiles show, and their `d<id>_online` values.

Then the forget-only removal (finding I3) - the only way a Zigbee tile is
ever removable with no stick configured, and unexercised so far.

- **Do:** with "No Zigbee stick" still set, open a Zigbee device's tile menu
  and press Remove. On the box that appears, press "Keep it" first, then
  open the menu and press Remove again, and this time press "Remove from
  loxmatter only".
- **Expect:** "Keep it" hides the box and leaves the tile untouched, with
  keyboard focus back on the tile's own menu button. The second Remove
  offers the box again; "Remove from loxmatter only" removes the tile from
  the device list and the export at once, without contacting the device -
  the physical device is not told and stays joined to whatever network it
  was on.
- **Record:** whether the tile is gone from `GET /api/devices` afterwards,
  and whether the device itself needs a factory reset before it can be
  paired again (it does - nothing here told it to leave).

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
behind an unreachable web UI has no console way out); and the pytest
collision between `tests/api` and `tests/projectsync`, which both carry a
`conftest.py`.
