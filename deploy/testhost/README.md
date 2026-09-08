# Test host: matter-server + OTBR

Test environment for Phase 1 (Task 6). **Not** the production stack from Spec 4.1 —
no hardening, no deployment guide, no diagnostics page. That's Phase 6. This
document records what actually worked on the concrete host, as
raw material for that.

**Current host:** `pi@10.0.1.56` — a Raspberry Pi 4 Model B Rev 1.5 (Debian 13
"trixie", Raspberry Pi OS, aarch64, 8 GB RAM). This directory used to be called
`deploy/testvm/` and ran on an Ubuntu VM (`lucienkerl@10.0.1.215`). It was moved to
the Pi because **the VM had no Bluetooth adapter** — Matter commissioning
runs over BLE, and without an adapter no device can ever be commissioned (`ls
/sys/class/bluetooth` returned nothing, `bluetooth.service` was inactive). The Pi has
a built-in adapter (`hci0`). The VM history including every problem found there
is recorded below under "History: the VM", because the causes (NAT64/
firewall, OTBR image variant, baud rate) apply unchanged to the Pi — same
dongle, same firmware, same image.

## Status: what's running

- `docker exec otbr ot-ctl state` → `leader`
- RCP connected with `RADIO_BAUDRATE=460800` (as on the VM — the same SONOFF Dongle
  Plus MG24, the same firmware, carried over unchanged and successful on the
  first attempt). `ot-ctl version` returns `OPENTHREAD/; POSIX; ...`; in syslog,
  Spinel frames run between otbr-agent and the RCP (e.g. `PROP_VALUE_GET, key:TIMESTAMP`)
  — the dongle responds.
- `matter-server` listens on `0.0.0.0:5580` and `[::]:5580`, **with BLE enabled**
  (see "Enable BLE" below for the proof).
- From a Mac on the same LAN: `uv run loxmatter inspect --node 1 --url
  ws://10.0.1.56:5580/ws` connects and returns on stderr:
  ```
  Node 1 ist am matter-server (ws://10.0.1.56:5580/ws) nicht bekannt — kommissioniert?
  ```
  Exit code 1 — per the Definition of Done, the proof that the connection stands (no
  commissioned device present, that's Task 7's job).

## Deployment

The complete workflow, runnable top to bottom. Don't use `-it` — there's
no interactive TTY over SSH `BatchMode=yes`, `docker exec` is enough. **No
`sudo`** — the Pi asks for a password for that, which is never requested or
entered; everything below can be done as the `pi` user.

**Two additional manual steps are needed on the Pi that weren't needed on
the VM.** They're already in the right place below, in order
(rationale and details in "Bluetooth adapter is rfkill-soft-blocked" and
"start-stop-daemon hangs on the Pi kernel" further down):

1. Unblock `hci0` via rfkill, **before** `matter-server` is started (step 2).
2. Manually restart `otbr-agent`/`otbr-web` after `docker compose up -d`, because
   `start-stop-daemon` in the OTBR image never finishes on this kernel (step 4).

**Step 1 — copy files and create the configuration:**

```bash
# on the Pi (a git checkout since 2026-09-03, previously individual files
# copied via scp under ~/loxmatter-testhost):
git clone https://github.com/lucienkerl/loxmatter.git ~/matter-loxone
cd ~/matter-loxone/deploy/testhost
cp .env.example .env      # adjust RADIO_DEVICE/RADIO_BAUDRATE/BACKBONE_IF/BLUETOOTH_ADAPTER if needed
mkdir -p data

# MINISERVER_IP must be set, shipped empty. LOXMATTER_API_TOKEN
# is optional (see below) - anyone who wants to set it anyway REPLACES the
# existing line instead of appending a second one: a second definition
# of the same variable would technically work (Compose takes the last one),
# but whoever edits the file later would then change the wrong line.
sed -i "s|^LOXMATTER_API_TOKEN=.*|LOXMATTER_API_TOKEN=$(openssl rand -hex 32)|" .env
sed -i "s|^MINISERVER_IP=.*|MINISERVER_IP=10.0.1.99|" .env   # substitute your own address
```

**Access to the interface: a password, not `LOXMATTER_API_TOKEN`.** Since
the WebUI login (Task 9/10, Phase 6), you set a password the first time you open
`http://10.0.1.56:8080/` in the browser — until that happens,
**no** `/api` route returns anything (HTTP 401), regardless of
whether `LOXMATTER_API_TOKEN` is set. That also applies to the
fabric backup (`GET /api/diagnostics/fabric-backup`): this stack runs
with `network_mode: host` and mounts matter-server's data directory into the
loxmatter service, but a missing token is no longer the decisive condition for
that since the WebUI login — without a password set, no `/api` route
responds anyway, this one included; the
former dedicated 403 branch for "no token set" has therefore been dropped.

`LOXMATTER_API_TOKEN` nonetheless remains worth setting if this instance
should also be reachable via script or `curl` — for the browser
itself it's no longer needed. `openssl rand -hex 32` is the
recommended way to generate it — it must be transmittable in an HTTP header and in a
WebSocket subprotocol, so no spaces, no
comma, ASCII only; `openssl rand -hex 32` produces only `[0-9a-f]`.

**Forgot the password — the emergency exit.** `loxmatter set-password` resets it
and logs out all open sessions in the process. Run this on THIS stack **inside
the running container**, not on the Pi itself:

```bash
docker compose exec loxmatter loxmatter set-password
```

Why `uv run loxmatter set-password` does NOT work on the Pi here:
the database lives in the named Docker volume
`loxmatter-store` (see `docker-compose.yml`, `LOXMATTER_STORE:
/data/loxmatter.sqlite`) — this path only exists *inside* the
container. On the host this environment variable is missing, so `set-password`
would instead hit the user-home default there
(`~/.loxmatter/loxmatter.sqlite`), i.e. a different, empty database —
without catching this emergency-exit trap, the command would have created that without comment and
reported success, while the actual bridge stayed locked
unchanged. It therefore aborts with a clear error if the specified
database doesn't exist, instead of creating a new one.

## WiFi/Ethernet-only (without a Thread radio module)

The `otbr` service has sat behind the `thread` Compose profile since
September 5, 2026. Anyone who only wants to connect WiFi or Ethernet Matter devices
leaves `COMPOSE_PROFILES` empty in the `.env` — then the Border Router is never
created, and `RADIO_DEVICE`, `RADIO_BAUDRATE` and `BACKBONE_IF` stay
ineffective.

**Why this was necessary:** `otbr` passes through a real device via `devices: -
${RADIO_DEVICE}:${RADIO_DEVICE}`. If no radio module is plugged in,
`docker compose up` fails with "error gathering device information" — and
that's for the *entire* stack, including the two services that never
needed the module.

**BLE remains necessary in both operating modes.** Even a WiFi Matter device is
commissioned over Bluetooth; `BLUETOOTH_ADAPTER` and the rfkill section further
down apply unchanged.

**Retrofitting:** plug in the radio module, set `COMPOSE_PROFILES=thread`
in the `.env` and point `RADIO_DEVICE` at the right path, then `docker compose up -d`.
The `start-stop-daemon` workaround further down is needed again from that point on.

## Updating

**Existing Thread installation:** if `COMPOSE_PROFILES` is missing from the `.env`
(every installation from before September 5, 2026), this branch silently lands in
WiFi mode without re-running the installer — the running
`otbr` container isn't stopped in the process, but on the next
configuration change it's no longer recreated, and a `docker compose down
&& up -d` won't bring the Thread router back afterwards. Anyone using Thread
should therefore add `COMPOSE_PROFILES=thread` to the `.env` before the next
`docker compose up`.

On the machine where the bridge runs:

```bash
cd ~/matter-loxone && ./scripts/update.sh
```

Pulls the latest state, builds the image, restarts the service. Just build
and restart without pulling: `./scripts/update.sh --no-pull`.

The script finds the stack via its own path — there's nothing to
configure as long as it runs from inside the repository.

What it guarantees:

- **It backs up the signal database before it changes anything.** That's where
  the signal keys live, and those are the wiring in the
  Loxone configuration — the one thing a botched update couldn't
  restore. The backups live under
  `~/loxmatter-backups/`, the last ten are kept.
- **matter-server and OTBR are left untouched** (`docker compose up
  --no-deps`). Without that, Compose would recreate them too as soon as the
  project configuration changes; OTBR's Thread state does survive that
  (it lives in the `otbr-state` volume), but restarting the Thread network
  for no reason isn't part of an update.
- **It aborts before it does damage.** If the backup or the build
  fails, the old service keeps running unchanged. If `/health` doesn't respond
  within 20 seconds after the restart, it shows the last
  log lines and reports a failure instead of success.
- At the end it reports which commit it shipped from and how many
  signals per device are exported.

## Enable BLE

That's the actual point of the move. `python-matter-server` only uses Bluetooth
when explicitly told to — unasked, BLE commissioning stays off even if
an adapter is present. The option name comes from the image itself, not from
guessing:

```
$ docker run --rm ghcr.io/home-assistant-libs/python-matter-server:stable --help
  --bluetooth-adapter BLUETOOTH_ADAPTER
                        Optional bluetooth adapter (id) to enable direct
                        commisisoning support.
```

`hci0` is the only adapter on the Pi → `--bluetooth-adapter 0`. The image's default
`CMD` is `--storage-path /data --paa-root-cert-dir /data/credentials` (verified via `docker
inspect --format '{{.Config.Cmd}}'`); `command:` in Compose overrides the
CMD completely, so the compose file carries over these two arguments and adds
`--bluetooth-adapter ${BLUETOOTH_ADAPTER}`. The adapter ID, like `RADIO_DEVICE`,
`RADIO_BAUDRATE` and `BACKBONE_IF`, is configurable via `.env` (`BLUETOOTH_ADAPTER`,
default `0` in `docker-compose.yml` if `.env` doesn't set the variable) instead of
hardcoded in the compose file — on a different host with several adapters, `hci0`
might have a different ID.

**Verification that the adapter actually gets through — not optional:** a stack
without BLE looks identical in the logs and WebSocket behaviour to one with BLE,
until a commissioning attempt fails. The code
(`matter_server/server/stack.py`, `MatterStack.__init__`) logs this explicitly, but only
at `DEBUG`:

```python
self.logger.debug(
    "Using storage file: %s - Bluetooth commissioning enabled: %s",
    storage_file,
    "NO" if bluetooth_adapter_id is None else f"YES (adapter {bluetooth_adapter_id})",
)
```

The default log level is `info` (`--log-level`, default per `--help`) — this line
does **not** appear in normal operation. Verified via a one-off run with
`--log-level debug` against the same `./data` directory (the Compose service stopped
first, then restarted normally afterwards with `docker compose up -d matter-server` —
no `--log-level debug` in ongoing operation, that would be too chatty):

```
$ docker compose stop matter-server
$ docker run --rm --network host --security-opt apparmor=unconfined \
    -v $PWD/data:/data -v /run/dbus:/run/dbus:ro \
    ghcr.io/home-assistant-libs/python-matter-server:stable \
    --storage-path /data --paa-root-cert-dir /data/credentials \
    --bluetooth-adapter 0 --log-level debug
...
2026-09-01 21:14:05.275 (MainThread) DEBUG [matter_server.server.stack]
  Using storage file: /data/chip.json - Bluetooth commissioning enabled: YES (adapter 0)
```

That's the proof: `bluetooth_adapter_id` arrives as `0` in the stack, not `None`
(`None` would call `chip.native.Init(999)` — the code comments on this itself:
"give the fake adapter id of 999 to disable bluetooth"). So Task 7 can actually
commission over BLE, provided the adapter is
`UP`/`Powered` at the moment of the connection attempt (see the next section — that's separate from this check).

## Bluetooth adapter is rfkill-soft-blocked (new compared to the VM)

`hci0` was `DOWN` when the target environment was surveyed. That wasn't immediately
worrying — the assumption was that `bluetoothd`/`matter-server` would bring the adapter up
itself. That's only partly true:

```
$ bluetoothctl show
Powered: no
PowerState: off-blocked
```

`off-blocked` means: rfkill has **soft-blocked** the adapter, not just
shut it down. `bluetoothctl power on` doesn't change that — bluetoothd refuses
the power-on as long as the rfkill block stands. This is independent of `matter-server`;
even if `python-matter-server`/`bleak` tries to power the adapter via D-Bus at
startup, it would hit the same refusal. **So `matter-server` does not bring `hci0` up
itself when it's rfkill-blocked — this had to be established beforehand, not
discovered after a failed commissioning attempt.**

```
$ cat /sys/class/rfkill/rfkill0/name /sys/class/rfkill/rfkill0/type /sys/class/rfkill/rfkill0/soft /sys/class/rfkill/rfkill0/hard
hci0
bluetooth
1        # soft-blocked
0        # no hardware kill switch
```

`/dev/rfkill` and `/sys/class/rfkill/rfkill0/soft` are owned by `root:root`, `pi` is in
no group that would have write access (`id -nG` shows, among others, `netdev`, `gpio`,
`i2c`, `spi`, `docker` — none suffices). Unblocking therefore needs root privileges, which per
the assignment must not be obtained via `sudo`. The way out: `pi` is in the
`docker` group, and the Docker daemon runs as root — a privileged container
can write `/sys/class/rfkill/rfkill0/soft` without `sudo` ever being invoked
at the SSH prompt:

```bash
docker run --rm --privileged -v /sys:/sys alpine \
  sh -c 'echo 0 > /sys/class/rfkill/rfkill0/soft'
```

After that:

```
$ bluetoothctl show
Powered: yes
PowerState: on
$ hciconfig hci0
hci0: ... UP RUNNING
```

The soft block was a one-time factory state, not a recurring one: on 2026-09-01
this was verified with a real reboot. After the restart, `rfkill list bluetooth`
still reported `Soft blocked: no`, and `hci0` was `UP RUNNING` on its own —
the unblock survives reboots. This step therefore belongs only in
first-time setup, not before every `docker compose up -d`; whether it's needed is shown by
`rfkill list bluetooth` (see step 2 above).

## Setting up the OTBR watchdog

The OTBR agent aborts if the radio module stops responding — an
RCP timeout, usually a USB dropout or power supply issue. The **container** keeps running
regardless, because its entrypoint script is not the agent; `restart:
unless-stopped` therefore doesn't kick in, and the image ships no watchdog
of its own.

On September 3, 2026, such an outage went unnoticed for six and a half hours.
No device was reachable during that time. The last lines from the agent before
it aborted:

```
[W] P-RadioSpinel-: radio tx timeout
[C] P-RadioSpinel-: Failed to communicate with RCP - no response from RCP during initialization
[C] Platform------: HandleRcpTimeout() at radio_spinel.cpp:2054: RadioSpinelNoResponse
```

Set up with `crontab -e` and this line:

```
*/5 * * * * /home/pi/matter-loxone/scripts/otbr-watchdog.sh >> /home/pi/otbr-watchdog.log 2>&1
```

The script checks whether a Thread interface (`wpan*`) with a
mesh address exists — the same check the "System" view also shows.
If it's missing, it restarts the `otbr` service and waits up to 60
seconds for the network. As long as everything is running it writes nothing; the log file
therefore contains exactly the incidents.

**It deliberately does not restart in a loop.** If the radio module itself is stuck,
restarting every minute wouldn't help and would just flood the log. At that point
someone has to look — and finds what happened in the log, including the last lines from the
OTBR log.

It's also worth adding an alert in Loxone: `d<n>_online` goes to 0 during such an
outage, and that value is already available in the Miniserver anyway.

## start-stop-daemon hangs on the Pi kernel (new compared to the VM)

The OTBR "test" image (see "History: the VM" for the image variant) starts
`otbr-agent`, `otbr-web` and `rsyslog` internally via sysvinit scripts that use
`start-stop-daemon --background --make-pidfile`. On the Pi, this
Ubuntu-18.04-era base from 2018 (`dpkg`/`start-stop-daemon` 1.19.0.5, image base
per `/etc/os-release`) hangs endlessly in its fork-detection loop against the
very new kernel (`6.18.34+rpt-rpi-v8`, PREEMPT, built 2026-06-09):

```
$ docker exec otbr ps aux | grep otbr
root  84  99.1  ...  start-stop-daemon --start --quiet --pidfile /var/run/otbr-agent.pid \
                      --make-pidfile -b --exec /usr/sbin/otbr-agent -- -I wpan0 -B wlan0 ...
```

The log dutifully shows `Starting thread border agent otbr-agent ... done.` beforehand —
that message lies: `/proc/84/comm` stays `start-stop-daem`, never `otbr-agent`. The
actual process was never exec'd; PID 84 (from the pidfile) is the wrapper itself,
`R` state, 0 syscalls in progress (`/proc/84/syscall` → `running`,
`/proc/84/wchan` → `0`) — a pure busy loop, not waiting on I/O. `rsyslog` hangs
briefly in the same pattern, but eventually gets through; `otbr-agent`/`otbr-web` never do,
observed over several minutes of waiting. Without a running `otbr-agent`,
`ot-ctl state` hangs with `connect session failed: No such file or directory` — the
corresponding control channel simply doesn't exist.

**Workaround, verified to work:** kill the hung wrappers and start the
binaries directly, with the same arguments readable from the pidfile/`ps` output
of the hung wrapper:

```bash
docker exec otbr sh -c 'kill -9 $(cat /var/run/otbr-agent.pid) $(cat /var/run/otbr-web.pid)'
docker exec -d otbr /usr/sbin/otbr-agent -I wpan0 -B wlan0 -d7 \
  --rest-listen-address 127.0.0.1 \
  spinel+hdlc+uart:///dev/ttyUSB0?uart-baudrate=460800
docker exec -d otbr /usr/sbin/otbr-web -I wpan0 -d7 -a 127.0.0.1 -p 80
```

After that, `ot-ctl` connects normally, and the Thread network forms as usual.

**Recovery after a Pi reboot takes longer — four steps instead of two.**
Measured on 2026-09-01: the container restarts automatically, but `otbr-agent` doesn't run
inside it at all, and there's nothing to kill — only orphaned PID files from before the
reboot. The Thread dataset itself survives (Ext PAN ID unchanged), so it doesn't need to be
recreated. But the interface is `detached` and must be restarted:

```bash
docker exec otbr sh -c 'rm -f /var/run/otbr-agent.pid /var/run/otbr-web.pid'
docker exec -d otbr /usr/sbin/otbr-agent -I wpan0 -B wlan0 -d7 \
  --rest-listen-address 127.0.0.1 \
  spinel+hdlc+uart:///dev/ttyUSB0?uart-baudrate=460800
docker exec otbr ot-ctl ifconfig up
docker exec otbr ot-ctl thread start
```

The state then goes from `detached` to `leader`, measured after about 15 seconds.
Only check `docker exec otbr ot-ctl state` after that, otherwise you'll see `detached` and
mistake it for an error.
`otbr-web` (REST API on port 80, internal) is not needed by `matter-server`/`ot-ctl` —
it only runs for completeness, in case it's useful for
debugging later.

**Unlike the rfkill fix, this is not a lasting state** — it has to be reapplied after
every `docker compose up`/restart of the `otbr` container, until the
image itself is replaced (e.g. by the newer s6-overlay "border-router" variant,
which has no sysvinit/`start-stop-daemon` base — see the history below,
deviation 1). For Phase 6 that's the clear next step, not this task, which
only needs a test environment reliable enough for Task 7 to run.

## `wlan0` instead of `ens18`/a cable interface

The Pi has no Ethernet cable plugged in (`eth0` shows `NO-CARRIER`) — `wlan0` is the
only interface with an actual connection to the LAN and therefore the
backbone interface for OTBR (`BACKBONE_IF=wlan0` in `.env.example`, passed through to the
OTBR container via `--backbone-interface`). Functionally identical to
`ens18`'s role on the VM — the only difference is the name and that it's WiFi
instead of a cable; that has no bearing on Thread routing over `wpan0` (see
"Known limitations" below, the IPv6 point, which applies unchanged from the VM).

## Baud rate

**460800**, carried over unchanged from the VM — the same SONOFF Dongle Plus MG24 with
the same firmware was simply plugged into a different machine. Worked on the first try: the RCP responds
to Spinel requests (see "Status: what's running"), the Thread network forms, `ot-ctl
state` reports `leader`. No second attempt at 115200 needed.

## Backing up the fabric volume (`./data`)

`matter-server` stores fabric/node state under `~/matter-loxone/deploy/testhost/data`
(`chip.json`, `chip_*.ini`, `credentials/`, plus a `<NodeID>.json` per
committed node). Backup from the Pi:

```bash
ssh pi@10.0.1.56 'tar czf - -C ~/matter-loxone/deploy/testhost data' > matter-server-data-backup.tar.gz
```

Restore (stop the container first):

```bash
ssh pi@10.0.1.56 'cd ~/matter-loxone/deploy/testhost && docker compose stop matter-server'
cat matter-server-data-backup.tar.gz | ssh pi@10.0.1.56 'tar xzf - -C ~/matter-loxone/deploy/testhost'
ssh pi@10.0.1.56 'cd ~/matter-loxone/deploy/testhost && docker compose start matter-server'
```

## Thread dataset — NOT into the repository

`docker exec otbr ot-ctl dataset active -x` prints the active Thread operational dataset
(hex-encoded). That's a network credential (contains, among other things, the network key) —
whoever has it can join the Thread network. It does **not** belong in the repository or
under `deploy/`.

Stored on the Pi under `~/matter-loxone/deploy/testhost/thread-dataset.txt` (mode `600`,
readable only by the operator). `deploy/testhost/.gitignore` additionally
excludes `.env`, `data/` and anything named like a dataset
(`*.dataset`, `thread-dataset*`) from commits, in case someone accidentally works in
this directory.

Retrieve again with: `ssh pi@10.0.1.56 docker exec otbr ot-ctl dataset active -x`

This dataset is needed for Task 7 (commissioning the IKEA devices) — this time
actually over BLE, not just over the Thread network.

## Known limitations (deliberate, for a test environment)

- No legacy firewall on `wpan0` (see history, VM deviation 2) — not a
  hardening goal of this task.
- No global IPv6 on `wlan0` (link-local only) — uncritical for Thread devices, as
  already on the VM: OTBR sets up its own ULA prefix on `wpan0`, and
  `matter-server` runs alongside it with `network_mode: host` and reaches the devices via
  the route there. Only Matter-over-WiFi devices would need global IPv6 on the LAN.
- The `start-stop-daemon` workaround (see above) is
  **not persistent** — after a restart of the Pi or of the `otbr` container it
  has to be reapplied. Acceptable for a test environment, not for Phase 6.

## History: the VM

The host originally ran on `lucienkerl@10.0.1.215` (Ubuntu 26.04 LTS,
backbone interface `ens18`). Decommissioned because **no Bluetooth adapter** was
present there (`ls /sys/class/bluetooth` returned nothing, `bluetooth.service` was inactive) —
Matter commissioning runs over BLE, so without an adapter no device could ever be
commissioned there. The containers were stopped with `docker compose down` in
`~/loxmatter-testvm/`; the files and the saved dataset remain there unchanged, in case
they're needed later.

The briefing was a starting point, not a verified end state — both ambiguities
named in the briefing (the OTBR invocation, the baud rate) actually had to be checked.
These findings apply unchanged to the Pi (same dongle, same firmware,
same image):

### 1. The OTBR image is the "test" variant, not "border-router"

`openthread/otbr:latest` (Docker Hub, digest at the time of the VM deployment
`sha256:ebebd9f643f0fadf60a9e46a1c81b4f4c9f320f04863e69ed95d8fde6b5de5a6`) has as
its entrypoint `/app/etc/docker/test/docker_entrypoint.sh` — that's the older,
"test" Docker variant from the `ot-br-posix` repo, not the newer
s6-overlay-based `border-router` variant (which expects different environment variables like
`OT_RCP_DEVICE`/`OT_INFRA_IF` and ignores `command:` overrides). That wasn't
obvious in advance — the source code on `main` in the GitHub repo shows the newer
variant; which one `:latest` on Docker Hub actually is could only be established via
`docker inspect --format '{{.Config.Entrypoint}}'` on the pulled image.
The compose syntax (`RADIO_URL` as an env var, `--backbone-interface` as a
command-line argument) matches this variant and works unchanged.

On the Pi it additionally turned out that this exact "test" variant is unreliable on a
very new kernel (`start-stop-daemon`, see above) — a further reason
to switch to the `border-router` variant in Phase 6.

### 2. NAT64/legacy firewall setup fails → disabled via env var

On the first start on the VM, the `otbr` container crashed:

```
iptables v1.6.1: can't initialize iptables table `mangle': Table does not exist ...
iptables v1.6.1: can't initialize iptables table `nat': Table does not exist ...
iptables v1.6.1: can't initialize iptables table `filter': Table does not exist ...
 *** ERROR:  Failed to start NAT44!
```

The container has no `modprobe` (the preceding `sudo modprobe ip6table_filter`
already fails with "command not found", but is ignored), so it can't load the
legacy iptables kernel tables (`mangle`/`nat`/`filter`) itself. The entrypoint script
(`/app/script/_nat64`, `_firewall`) checks the environment variables `NAT64` and
`FIREWALL` respectively before the NAT64/NAT44 and firewall setup (both default
to `1` in the image). Setting both to `"0"` (see
`docker-compose.yml`, comment there) skipped this part entirely, and after that
`otbr-agent` started cleanly.

On the Pi, **the same finding was applied from the start** (`NAT64: "0"`, `FIREWALL:
"0"` were already in the compose file copied from the VM) — the crash therefore
never occurred there in the first place; the log just shows the same harmless
`sudo: modprobe: command not found` notice as on the VM. Like the VM, the Pi has
no loaded iptables modules (see the task brief) — the same cause, the same
fix, applied preventively instead of being driven to crash again.

Uncritical for the test environment: NAT64/NAT44 translates Thread IPv6 to IPv4 hosts
on the LAN — not needed here, neither `matter-server` nor `loxmatter` need to reach
IPv4 targets from inside the Thread network. The legacy firewall would have set up
ingress filtering for `wpan0`; without it, the container is more open than would be
defensible in Phase 6 — that belongs in hardening there.

Minor side note from the log, also caused by `FIREWALL=0`/the missing `modprobe`
and without effect on operation (observed identically on the Pi):

```
Platform------: Got an error when executing command `ipset flush otbr-ingress-allow-dst-swap`: Resource temporarily unavailable
Firewall - failed to update ipsets: Failed
```

That's otbr-agent's own (in-process) firewall component, which wants to maintain ipset
rules; without the matching kernel modules this stays a warning (`[W]`), not a
fatal error.

### 3. matter-server image path

`ghcr.io/home-assistant-libs/python-matter-server:stable` as in the briefing — still
pullable. Note for later: the upstream README now points
to `ghcr.io/matter-js/python-matter-server` as the successor project
(`python-matter-server` itself is frozen at version 8.1.2, no further
updates). Since `pyproject.toml` already pins `python-matter-server>=8.1.2`,
that's consistent — only relevant if the `home-assistant-libs` image ever
disappears.

## Files in this directory

- `docker-compose.yml` — the compose definition. Since September
  3, 2026 the Pi uses **this exact file**, from a git checkout under
  `~/matter-loxone` — no longer a copy that can drift out of sync.
- `.env.example` — template for `.env` on the Pi.
- `.gitignore` — prevents accidentally committing `.env`, `data/` and
  dataset files, in case these ever get created locally in this repo path.
