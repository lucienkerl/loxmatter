# One-Liner Install Script: `install.sh`

Design, September 5, 2026. Describes a script that reduces a loxmatter
installation to a single command:

```
curl -fsSL https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh | sh
```

Connects to [`deploy/testhost/README.md`](../../../deploy/testhost/README.md) —
that's where the manual path this script summarizes lives — and to
[`scripts/update.sh`](../../../scripts/update.sh), whose style and division of
labor it adopts. The quickstart section from
[the README product-page design](2026-09-05-readme-product-page-design.md)
is supplied by this design, see section 10.

## 1. The Problem

An installation today is eight steps spread across two documents:
clone, create `.env` from the template, set four values in it by hand,
`mkdir -p data`, `docker compose up -d --build` — and on a Raspberry Pi
after that the rfkill unblock and the `start-stop-daemon` workaround from
[`deploy/testhost/README.md`](../../../deploy/testhost/README.md), without
which neither BLE commissioning nor the Thread network work.

Whoever skips the `MINISERVER_IP` step gets a running stack
that sends nothing to the Miniserver. Whoever doesn't know the Thread
workaround gets a stack that looks healthy and finds no devices.
Both errors only show up hours later.

On top of that comes a hurdle that has nothing to do with the flow: the
stack requires a Thread radio module, even if someone only wants to
connect WiFi Matter devices. `devices: - ${RADIO_DEVICE}:${RADIO_DEVICE}`
on the `otbr` service makes `docker compose up` fail as soon as the path
does not exist. This design lifts that too.

## 2. Agreed Decisions

| Question | Decision | Reason |
|---|---|---|
| Install path | **Only the Docker stack** (`deploy/testhost/`) | The target outcome "set a password, open the WebUI" is only reachable with the service running, and the WebUI needs a reachable `matter-server`. The pure CLI path via `uv` does not reach this state and is three lines anyway. |
| Host intervention | **Install, check, report** | The rfkill fix effectively needs root, the otbr workaround is kernel-specific and would kill running processes on healthy hosts. Silent success with a dead Thread network would be the worst outcome — so it is checked and named, but not secretly fixed. |
| Configuration | **Interactive via `/dev/tty`**, env variables override | `stdin` is the pipe in the `curl \| sh` case. Detectable values are suggested, `MINISERVER_IP` is not detectable and is asked. |
| Missing base tools | **`git`, `curl`, `openssl` are installed afterward** | Otherwise the one-liner fails on a fresh host over a triviality. |
| Missing Docker | **Installed without asking**, but announced | Deliberate decision by the client. "Don't ask" doesn't mean "don't tell": the script names the step and source, but does not stop. |
| Package management | **Only `apt-get`** | Debian, Ubuntu, Raspberry Pi OS are the documented target hosts. Untested package-manager branches are exactly the mid-way abort this design rules out. |
| Operating mode | **Thread or WiFi/Ethernet-only.** `otbr` becomes a compose profile | Without a radio module, `docker compose up` used to fail on `devices: ${RADIO_DEVICE}`, even though WiFi Matter devices don't need the border router at all. The operating mode sits as `COMPOSE_PROFILES` in the `.env`, so every later compose call knows it without carrying a `--profile` along. |
| Second run | **Check and straighten out**, update only after consent | `scripts/update.sh` backs up the signal database first; an install script that updates along the way would bypass that backup. So it asks and delegates to `update.sh`. |
| Language | **English throughout**, including the comments | Deviation from the project convention (German code comments), deliberate: the one-liner is the first contact with the project and sits in an English README. |
| Safeguarding | `shellcheck` in CI, `--dry-run`, tests with fake binaries | Idempotence and "clean abort instead of mid-way" should be verified, not just claimed. |

## 3. Form

**Location:** `install.sh` at the repository root, matching the target URL
`…/main/install.sh`. GPL header like the other scripts.

**Pure POSIX `sh`, no Bash.** The one-liner ends in `| sh`, and on
Raspberry Pi OS `/bin/sh` is dash. A Bash script piped through `sh`
aborts at the first `[[` line. This costs arrays and `set -o pipefail`;
in exchange it runs everywhere the one-liner points. Checked with
`shellcheck -s sh`, not with the Bash dialect.

**Everything in functions, `main "$@"` on the last line.** If the
transfer is cut off mid-download, `sh` executes half a script. With this
pattern, a truncated script only defines functions and does nothing —
without it, an interrupted connection is a half-installed host.

**Invocation forms:**

```
curl -fsSL .../install.sh | sh                     # the one-liner
curl -fsSL .../install.sh | sh -s -- --dry-run     # shows everything, changes nothing
sh install.sh --dir /srv/loxmatter                 # downloaded and read
```

Flags: `--dry-run`, `--dir <path>`, `--help`. Environment variables that
skip a question: `LOXMATTER_DIR`, `MINISERVER_IP`, `RADIO_DEVICE`,
`RADIO_BAUDRATE`, `BACKBONE_IF`, `BLUETOOTH_ADAPTER`, `LOXMATTER_API_TOKEN`.

## 4. Flow

### Phase 1 — Check Before Anything Is Changed

Everything that can fail, fails here. This phase creates no file,
installs no package, and starts no container.

- **Linux?** On macOS/BSD, immediate abort with a pointer to the
  developer path (`uv sync`). Reasoning in the text: `network_mode: host`,
  `/dev/ttyUSB*`, `/run/dbus`, and rfkill don't exist there.
- **Architecture** in `aarch64|arm64|x86_64|amd64`? Otherwise abort, with the
  actually detected value in the message.
- **Collect missing tools, don't abort on the first hit:** `git`,
  `curl`, `openssl`, `docker`, `docker compose`. The result is a list.
- **Can the missing ones be fixed?** Only if there's something to install:
  root or `sudo` present, and `apt-get` present. Otherwise abort with the
  **complete** package list, not just the first missing tool.
- **Is the script running as root?** Warning, no abort: the clone and
  `~/loxmatter-backups` belong to root afterward, and `scripts/update.sh` only
  runs as root from then on.
- **Configuration obtainable?** No `/dev/tty` (non-interactive run) and
  `MINISERVER_IP` neither set nor in an existing `.env` — abort,
  with the line to fix it (`curl … | MINISERVER_IP=10.0.1.99 sh`).
- **Target directory** creatable, or existing and writable.

### Phase 2 — Install Afterward, If Needed

Two-stage, in this order, because `get.docker.com` itself needs `curl`:

1. Base packages: `apt-get update`, then
   `DEBIAN_FRONTEND=noninteractive apt-get install -y` with **exactly** the
   ones missing from `git curl openssl` — nothing beyond that.
2. Docker: announced ("Docker is not installed. Installing it from
   https://get.docker.com — this requires sudo."), then
   `curl -fsSL https://get.docker.com | sh`, then `usermod -aG docker <user>`.

If the script installed Docker itself in this run, it uses
`sudo docker` for the rest of **this one run** — the new group membership
only takes effect after a re-login. The final report says so: log out
and back in once, after that `docker` works without `sudo`, and
`scripts/update.sh` needs that.

### Phase 3 — Clone

`git clone https://github.com/lucienkerl/loxmatter.git ~/loxmatter`, branch
`main` (there are no tags). Over HTTPS, not SSH — a fresh host has
no key.

If the directory already exists: **do not clone, do not pull.** It is only
checked that it is a loxmatter checkout (`Dockerfile` and
`deploy/testhost/docker-compose.yml` present, the way `update.sh` checks it),
otherwise abort. Updating is Phase 6's job.

### Phase 4 — Configuration

Create `.env` from `.env.example` if it is missing.

**First the operating mode.** If the script finds a `/dev/ttyUSB*` or
`/dev/ttyACM*`, it suggests Thread, otherwise WiFi/Ethernet-only. The answer
lands as `COMPOSE_PROFILES=thread` or `COMPOSE_PROFILES=` in the `.env`;
`LOXMATTER_MODE=thread|wifi` skips the question. In WiFi mode, the
questions about `RADIO_DEVICE` and `BACKBONE_IF` are dropped entirely — both
belong exclusively to the `otbr` service, which is then not created.

Then each value individually:

| Variable | Detection | Question |
|---|---|---|
| `BACKBONE_IF` | `ip route show default` → field after `dev` | with a suggestion; Thread mode only |
| `RADIO_DEVICE` | first `/dev/ttyUSB*`, else first `/dev/ttyACM*` | with a suggestion; Thread mode only, not empty there |
| `RADIO_BAUDRATE` | default `460800` from `.env.example` | none; Thread mode only |
| `BLUETOOTH_ADAPTER` | first `hci<N>` from `/sys/class/bluetooth` → `<N>` | with a suggestion; in **both** operating modes, BLE is also the commissioning path for WiFi devices |
| `MINISERVER_IP` | not detectable (the project has no Miniserver search) | mandatory question, IPv4 format is checked |
| `LOXMATTER_API_TOKEN` | `openssl rand -hex 32`, fallback `od -An -tx1 -N32 /dev/urandom \| tr -d ' \n'` | none |

Rules that apply to every value:

- **Existing `.env` values are never overwritten.** Only missing or empty
  keys are filled. Whoever has a customized `.env` on the second run
  keeps it.
- **Line replacement, no appending.** A second definition of the same
  variable would work (Compose takes the last one), but whoever edits the
  file later then changes the wrong line — the justification for this
  already lives in
  [`deploy/testhost/README.md`](../../../deploy/testhost/README.md).
- A set environment variable skips the corresponding question.
- Questions run via `/dev/tty`, because `stdin` is the pipe.

**In Thread mode, `RADIO_DEVICE` must not stay empty.** The compose file
passes the device through as `devices: - ${RADIO_DEVICE}:${RADIO_DEVICE}`; an
empty value gives `- :`, a non-existent path the message "error
gathering device information" — both make `docker compose up` fail.
If the script finds no `/dev/ttyUSB*` and no `/dev/ttyACM*`, it therefore
suggests WiFi/Ethernet-only instead of inventing a default. Whoever wants
Thread anyway, because the module is only about to be plugged in, gives the
path by hand.

The fallback to `/dev/urandom` for the token delivers the same format (64
characters from `[0-9a-f]`, no spaces, ASCII — exactly the requirement from
`.env.example`). It exists so that of all things, token generation cannot
tip over an otherwise healthy installation.

### Phase 5 — Start

`mkdir -p data` in the stack directory, then `docker compose up -d --build`.
Before that, the notice that the build takes several minutes on a Raspberry
Pi — without it, a silent build looks like it's stuck.

No `--profile` on the call: the operating mode sits as `COMPOSE_PROFILES` in
the `.env`, which Compose reads on its own. This way it also applies to
every later manual call and to `scripts/update.sh`, without anything
needing to be repeated there.

### Phase 6 — Check and Report

Four checks that **change nothing**:

1. **Service healthy:** `http://127.0.0.1:<port>/health`, up to 20 seconds
   of patience, port read from the compose file — the same approach as in
   `update.sh`. If it doesn't answer, the last 30 lines from
   `docker logs loxmatter` are printed.
2. **Containers:** are `otbr`, `matter-server`, and `loxmatter` running?
3. **Bluetooth:** is the adapter rfkill soft-blocked? Determined via
   `/sys/class/rfkill/*/type` = `bluetooth` and the corresponding `soft` file —
   the index is searched for, not assumed to be `rfkill0`. If it is blocked,
   the command from the deploy README is **printed, not executed**,
   with the actually found index.
4. **Thread**, Thread mode only: is there a `wpan*` interface with
   a mesh address in `/proc/net/if_inet6`? The same check
   `scripts/otbr-watchdog.sh` and the "System" view use. If it's missing,
   the `start-stop-daemon` workaround is printed as a command block — with
   the values from the just-written `.env` substituted in — along with the
   note that it is needed again after **every** `compose up` until the OTBR
   image is replaced. In WiFi mode, the check is skipped, and the report
   explicitly says that Thread is off and how to retrofit it.

Checks 3 and 4 are findings, not a reason to abort: a stack without a
Thread network is fully usable for pure WiFi Matter devices.

### Phase 7 — Final Report

- LAN address as in `update.sh` (`hostname -I | awk '{print $1}'`) and the
  WebUI URL.
- "Open it and set a password — until you do, no `/api` route answers."
- In Thread mode, the watchdog cron suggestion, with the **actual**
  installation path, not the `/home/pi/matter-loxone` from the deploy README.
  In WiFi mode it is dropped — there is no `otbr` service to watch.
- `scripts/update.sh` as the path for later.
- If Docker was installed in this run: the note about the
  re-login.
- All open findings from Phase 6 collected once more, so they don't
  disappear between the build lines.

## 5. Error Behavior

`set -eu`. No `pipefail` — dash doesn't know it; where a pipeline counts,
the result is explicitly checked.

A variable holds the description of the current step. An `EXIT` trap prints
it on every unexpected abort, together with what has already happened
and what hasn't:

```
Failed while: writing .env
The checkout at /home/pi/loxmatter exists; nothing was started.
```

**No rollback.** A script that cleans up on someone else's host does more
damage than the half-state it wants to remove. Instead, every abort
message names the point reached, and a repeated run picks it up from there.

## 6. Idempotence

A second run:

- does not clone again and does not pull,
- leaves existing `.env` values untouched and only fills in missing ones,
- calls `docker compose up -d --build` again, which is idempotent on its
  own,
- runs the checks from Phase 6 again,
- and additionally checks via `git fetch` whether `main` is ahead. If so:
  a question via `/dev/tty` ("N new commits available. Update now? [y/N]"). On
  consent, `scripts/update.sh` runs, which backs up the signal database first.
  Without a TTY the question is skipped, the hint stays.

This makes the repeat run both the repair of an aborted first
run and the convenient path to an update — without bypassing the backup
`update.sh` brings along.

## 7. What the Script Explicitly Does Not Do

- It does not unblock rfkill itself. The command needs a privileged
  container with a `/sys` mount; doing that without asking is exactly the
  silent root action this design rules out.
- It does not apply the otbr workaround itself. It is kernel-specific and
  would kill running processes on hosts that don't need it.
- It does not add the watchdog cron entry itself.
- It installs no TLS and changes nothing about the security properties of
  the stack. The warnings from the README design apply unchanged.
- It does not touch `README.md` (see section 10). `deploy/testhost/README.md`
  only gets the section on the new operating mode, because the compose file
  changes and its own documentation would otherwise become wrong.

## 8. Files

| File | Change |
|---|---|
| `install.sh` | new — repository root, POSIX `sh`, GPL header, English throughout |
| `tests/test_install_script.py` | new — tests with fake binaries, see section 9 |
| `.github/workflows/ci.yml` | one step `shellcheck -s sh install.sh` |
| `docs/superpowers/specs/2026-09-05-install-oneliner-design.md` | this document |
| `deploy/testhost/docker-compose.yml` | `otbr` gets `profiles: ["thread"]`; `matter-server` loses `depends_on: otbr` |
| `deploy/testhost/.env.example` | new variable `COMPOSE_PROFILES`, commented |
| `deploy/testhost/README.md` | a section "WiFi/Ethernet-only" |
| `scripts/otbr-watchdog.sh` | guard against it: no `otbr` container → exit quietly |

**Why `matter-server` loses its `depends_on: otbr`:** Compose aborts if a
service depends on one whose profile is not active. The loss has
no effect on content — `depends_on` only controls start order, not
readiness, and `matter-server` does not need the border router at
startup: Thread commissioning later runs over the host network, on which
`otbr` already sits via `network_mode: host` anyway.

**Why `scripts/otbr-watchdog.sh` needs a guard:** today it only checks
whether a `wpan*` interface exists, and restarts `otbr` otherwise. In
WiFi mode it never exists and the service doesn't exist — the watchdog would
write a failure to the log every five minutes. If the container is missing,
it will exit quietly from now on.

`shellcheck` initially only runs against `install.sh`. Checking the
existing bash scripts (`scripts/update.sh`, `scripts/otbr-watchdog.sh`) as
well would presumably surface pre-existing findings — that would be its own
task.

## 9. Test Strategy

`tests/test_install_script.py` sets up a temporary `HOME` and a `PATH`
with stubs for `docker`, `git`, `sudo`, `apt-get`, `ip`, `uname`, `curl`. Each
stub writes its call to a log file and exits successfully. Without
`/dev/tty` the non-interactive branch runs, the values come from the environment.

Cases checked:

| Case | Expectation |
|---|---|
| `uname` stub reports `Darwin` | abort, no directory created, exit ≠ 0 |
| `git`/`curl`/`openssl` missing | exactly these three in the `apt-get` call, no others |
| Docker missing | `get.docker.com` only **after** `apt-get`; then `sudo docker` in all following calls |
| no `sudo`, no root, Docker missing | abort in Phase 1; `git clone` does **not** appear in the log |
| `MINISERVER_IP` missing, no TTY | abort before the clone |
| second run with a complete `.env` | no `git clone`, `.env` byte-identical, `compose up -d` again |
| `.env` with `MINISERVER_IP`, without `LOXMATTER_API_TOKEN` | only the missing line is added, the existing one stays |
| no `/dev/ttyUSB*`, no `/dev/ttyACM*`, no `LOXMATTER_MODE` | WiFi mode: `COMPOSE_PROFILES=` in the `.env`, no `RADIO_DEVICE` line set, no abort |
| `LOXMATTER_MODE=thread` without a detected device and without `RADIO_DEVICE` | abort in Phase 1, before the clone |
| `LOXMATTER_MODE=thread` with a detected device | `COMPOSE_PROFILES=thread`, `RADIO_DEVICE` and `BACKBONE_IF` set |
| `--dry-run` | no mutating stub is called: no `apt-get`, no `git clone`, no `docker`, no `sudo`. Read-only detection (`uname`, `ip route`) still runs — it changes nothing |

**What these tests do not achieve:** they check the *choice* of commands, not
their effect. Whether `docker compose up -d --build` actually produces a
healthy stack on a Pi is only shown by a run on a Pi. This is stated here so
that nobody later mistakes it for more than it is — the cautionary
precedent is the bug in `scripts/update.sh`, which for months built an
image that never arrived anywhere, and
still reported "Done" anyway.

## 10. Handoff to the README Product Page

The README is being rebuilt into an English product page in its own
session (see
[2026-09-05-readme-product-page-design.md](2026-09-05-readme-product-page-design.md),
section 7: "The one-liner install script is built in its own session […]
whoever builds the script pulls step 1 along"). At the time of this design,
that spec sits on its own branch, the README itself is unchanged.

**This session therefore touches neither `README.md` nor the other branch.**
Instead, the finished wording sits here and is adopted by the README session
into section 6 ("🚀 Quickstart") of the product page.

### Wording for the Quickstart (English, ready to adopt)

> ## 🚀 Quickstart
>
> One command on the machine that will run the bridge — a Raspberry Pi or any
> Debian-based Linux host:
>
> ```bash
> curl -fsSL https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh | sh
> ```
>
> It asks for your Miniserver's IP address, detects the rest (network
> interface, Thread radio, Bluetooth adapter), and starts the containers.
> When it finishes it prints the address of the web interface.
>
> **No Thread radio? That is fine.** If the installer finds no USB radio it
> sets up WiFi/Ethernet-only mode: the Thread border router is left out and
> the bridge talks to WiFi and Ethernet Matter devices over your existing
> network. Plug a radio in later, set `COMPOSE_PROFILES=thread` and
> `RADIO_DEVICE` in `deploy/testhost/.env`, and restart the stack.
>
> **Prefer to read it first?** Same script, three lines:
>
> ```bash
> curl -fsSLO https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh
> less install.sh
> sh install.sh
> ```
>
> The script installs `git`, `curl`, `openssl` and Docker if they are missing,
> which needs `sudo`. It tells you before it does, but it does not ask. Add
> `--dry-run` (`… | sh -s -- --dry-run`) to see every step without changing
> anything.
>
> **Then open `http://<host>:8080/` and set a password.** Until you do, no
> `/api` route answers — there is no open state.
>
> Running it again is safe: it keeps your configuration, re-checks the stack,
> and offers to update if there are new commits. The full manual path, and the
> two Raspberry-Pi-specific steps the script reports but does not perform
> (unblocking Bluetooth, restarting the Thread agent), are in
> [docs/SETUP.md](docs/SETUP.md).

The two Pi steps the last paragraph refers to currently live in
[`deploy/testhost/README.md`](../../../deploy/testhost/README.md)
(`Bluetooth-Adapter ist rfkill-soft-blocked` and
`start-stop-daemon haengt auf dem Pi-Kernel`) and
move to `docs/SETUP.md` with the README rebuild.

## 11. Boundaries

- No published container image. The stack continues to build `loxmatter` from
  the clone (`context: ../..`); an image in a registry would be its own
  task and would make the clone redundant.
- No change to application code.
- No change to `scripts/update.sh`. It specifically builds and starts the
  `loxmatter` service and is not affected by the profiles.
- Only what the WiFi/Ethernet-only operating mode needs is changed in
  `docker-compose.yml`, `.env.example`, and `scripts/otbr-watchdog.sh`
  (section 8) — no hardening, no digest pins, no non-root user.
- No `systemd` service, no automatic watchdog setup.
- No support for package managers other than `apt-get`.
- No switching of the operating mode while running, done by the script.
  Whoever retrofits a radio module later sets `COMPOSE_PROFILES=thread` and
  `RADIO_DEVICE` in the `.env` and restarts the stack; the WiFi mode's
  final report names exactly these two lines.

## 12. Risks

| Risk | Handling |
|---|---|
| `get.docker.com` changes its behavior or is unreachable | error is reported as its own step ("Failed while: installing Docker"), the clone does not exist yet at that point |
| The detected defaults are wrong (multiple USB devices, multiple interfaces) | every detected value is a suggestion in a question, not a silent decision |
| A non-interactive run silently makes wrong assumptions | without a TTY, nothing is guessed: if `MINISERVER_IP` is missing, Phase 1 aborts |
| The user takes "Done" to mean "Thread is running" | Phase 6 explicitly checks `wpan*` and repeats the finding in the final report |
| The stub tests give false confidence | section 9 names the limit; acceptance on a Pi remains a prerequisite |
| `sudo docker` in the same run hides that the group does not yet take effect | the final report explicitly demands the re-login |
| An existing stack without `COMPOSE_PROFILES` in the `.env` loses its `otbr` service on the next `compose up` | the second run fills in missing keys: if `COMPOSE_PROFILES` is missing and an `otbr` container exists, `thread` is written in — existing installations stay what they are |
| A radio module is plugged in but not detected (different device name) | the operating mode is a question with a suggestion, not a silent decision; Thread can be selected with a manually given path |
