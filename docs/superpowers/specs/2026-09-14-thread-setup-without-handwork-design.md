# Thread setup without handwork

Date: 14 September 2026. Follows "Radios in the Web UI" (2026-09-11) and the
0.4.0 release.

## 1. The problem

The rule since 14 September: updates of the bridge and of the updater service
work through the web UI alone. For the updater, which cannot replace itself,
the web UI shows the one command to run. Nobody edits `.env` or the crontab.

0.4.0 breaks that rule for Thread in three places, and a fresh installation
inherits two of them:

1. **The watchdog.** `install.sh` ends by printing a crontab line to type in,
   and the 0.4.0 notes asked existing users to change theirs.
2. **The border router image.** `openthread/otbr:latest` is built without
   `-DOT_RCP_RESTORATION_MAX_COUNT`, so a single lost Spinel frame ends
   `otbr-agent`. The image that fixes this (`local-otbr:2ba12d24-rcprestore2`)
   exists only on the test Pi, selected through `OTBR_IMAGE` in its `.env`.
3. **Existing installations never get a new otbr container.**
   `update-once.sh` recreates the `loxmatter` service only. A new image or the
   fixed container path `/dev/ttyThread` (1f787b5) reaches otbr only when
   something else recreates it.

## 2. Goal

- A fresh installation gets the RCP-restoration image, the fixed device path and
  a running watchdog without a single extra step.
- An existing 0.4.x installation needs exactly what the web UI already asks
  for: the update button, then the updater refresh command that System →
  Version shows because the updater image changed. From then on, both of the
  following happen automatically:
  - the watchdog runs;
  - otbr follows every change of its image or container configuration.
- A later OTBR version reaches every installation the same way: change the
  pinned commit in the repository, release, and the update carries it.
- `deploy/updater/update-once.sh` does not change.

## 3. The border router image

### 3.1 Source

`deploy/otbr/source.env`, read by the workflow and by tests:

```sh
OT_BR_POSIX_COMMIT=2ba12d24f4d1efad78c1e1b0e5960d65aa5a9fd7
OTBR_OPTIONS=-DOT_RCP_RESTORATION_MAX_COUNT=2
OTBR_IMAGE_TAG=2ba12d24-rcp2
```

This is the same commit and the same options as the image that has been running
on the test Pi since 14 September. The build is the one measured there:
ot-br-posix's `etc/docker/test/Dockerfile` with
`--build-arg BASE_IMAGE=ubuntu:bionic` and `--build-arg OTBR_OPTIONS=...`, with
the ot-br-posix checkout (submodules included) as build context.

### 3.2 Workflow `.github/workflows/otbr-image.yml`

- Triggers: `workflow_dispatch`, and `push` to `main` touching
  `deploy/otbr/**` or the workflow itself.
- Image `ghcr.io/lucienkerl/loxmatter-otbr:${OTBR_IMAGE_TAG}`. Tags are
  immutable: the first job checks with `docker buildx imagetools inspect`
  whether the tag exists, and if it does, the workflow ends successfully
  without building.
- One build job per architecture, native: `ubuntu-24.04` for `linux/amd64`,
  `ubuntu-24.04-arm` for `linux/arm64`. Each job:
  - clones ot-br-posix at `OT_BR_POSIX_COMMIT` with submodules;
  - builds as in 3.1;
  - checks the binary with `docker run --rm --entrypoint grep <image> -c "Trying to recover" /usr/sbin/otbr-agent`
    (must print at least 1 — this string exists only when RCP restoration is
    compiled in);
  - pushes by digest.
- A merge job creates the multi-arch tag from the two digests
  (`docker buildx imagetools create`).
- Labels: `org.opencontainers.image.source` (ot-br-posix URL),
  `org.opencontainers.image.revision` (`OT_BR_POSIX_COMMIT`), and
  `org.opencontainers.image.description` naming the RCP restoration option.
- Permissions: `contents: read`, `packages: write`.

### 3.3 Compose and release gate

- `deploy/testhost/docker-compose.yml`: the image line becomes
  `image: ${OTBR_IMAGE:-ghcr.io/lucienkerl/loxmatter-otbr:2ba12d24-rcp2}`.
  `OTBR_IMAGE` stays as an expert override.
- `.github/workflows/ci.yml`: on a version tag, before any image is pushed, a
  step resolves the compose default image with `docker buildx imagetools
  inspect` and fails the release if it does not exist. A version can therefore
  never point at an image nobody built.
- A test pins the compose default to `ghcr.io/lucienkerl/loxmatter-otbr:` plus
  `OTBR_IMAGE_TAG` from `source.env`, and checks that the tag starts with the
  first eight characters of `OT_BR_POSIX_COMMIT`.

One-time step for the maintainer, not for users: the first push creates the
GHCR package as private. It has to be set to public once, as was done for the
existing packages.

## 4. The watchdog runs inside the updater service

### 4.1 `scripts/otbr-watchdog.sh` works in a container

The script keeps its job, its lock and its limits, and changes how it measures,
so that it runs the same from the host's cron and from the updater container:

- **Thread up:** `docker exec otbr ot-ctl state` (bounded by the query timeout)
  answers `leader`, `router` or `child`. This replaces the scope-00 `wpan*` line
  in `/proc/net/if_inet6`, which a container outside the host network namespace
  cannot see, and which is the same test `radios-once.sh` already uses.
- **Container age:** `docker top otbr -o etimes`, taking the largest number.
  The Docker daemon runs `ps` on the host, so this works from inside a
  container, and it counts from boot rather than reading the wall clock (a Pi
  has no real-time clock). An unreadable age skips the grace period, as today.
- **Restart:** `docker restart otbr` instead of `docker compose restart otbr`,
  which would need the stack's `.env` and project directory. The stale pid file
  is removed first, as today.
- The `cd` into `deploy/testhost` and the `IF_INET6` seam go away.
- A marker line near the top: `# loxmatter-watchdog: container-ready`.

### 4.2 `deploy/updater/watchdog-once.sh` (new, in the updater image)

- `SCRIPT="${LOXMATTER_WATCHDOG_SCRIPT:-/repo/scripts/otbr-watchdog.sh}"`.
- Exits 0 without doing anything unless `SCRIPT` is a regular file containing
  the marker line. The updater may meet an older checkout (a bridge rollback to
  0.4.0): that script reads `/proc/net/if_inet6`, would never see a Thread
  interface from inside the container, and would restart otbr every minute.
- Runs `bash "$SCRIPT"`, appending stdout and stderr to
  `$LOXMATTER_UPDATE_DIR/otbr-watchdog.log` (default `/data/update`), then trims
  that file to its last 2000 lines.
- The script runs from the checkout, so a later fix to the watchdog arrives
  with the ordinary bridge update, without an updater refresh.

### 4.3 `deploy/updater/entrypoint.sh`

- `WATCHDOG_WORKER="${WATCHDOG_WORKER:-/opt/loxmatter/watchdog-once.sh}"`,
  `WATCHDOG_INTERVAL_SECONDS` (default 60) and
  `WATCHDOG_WORKER_TIMEOUT_SECONDS` (default 300).
- In each pass, after the radios worker: if the worker is executable and at
  least `WATCHDOG_INTERVAL_SECONDS` have passed since its last start, the
  entrypoint records the start time and runs it through `run_worker` with its
  own limit. The first pass runs it.
- Sequential, like the other two workers. A healthy check takes about a second.
  Only a broken Thread network makes a run long, and then an update request
  waiting behind it is the lesser problem.

### 4.4 `deploy/updater/Dockerfile`

It adds `bash`, and copies `watchdog-once.sh` next to the other workers. It
gets the same executable bit.

### 4.5 The lock

The updater reaches `scripts/otbr-watchdog.sh` through the `../..:/repo` bind
mount, so the updater and the host's cron lock the same inode. A crontab line
left over from 0.4.0 therefore does no harm: whichever run comes second finds
the lock taken and exits. `radios-once.sh` already takes the same lock.

## 5. otbr follows its configuration (`radios-once.sh`)

### 5.1 When it runs

The border router is checked on a pass with no request file, instead of the
current plain `exit 0`. All of these must hold:

- `CAPABLE` is true;
- `state.json` is not in a running update phase (the list the request path
  already uses);
- `.env` exists and `COMPOSE_PROFILES` contains `thread`;
- a container named `otbr` exists.

### 5.2 Drift

The desired configuration comes from
`compose --profile thread config --format json`, reduced with `jq` to:

- `image`;
- the sorted list of `source:target` device mappings (a device entry may be a
  string or an object, depending on the Compose version; permissions dropped);
- the `RADIO_URL` environment value.

The actual configuration comes from `docker inspect otbr`:

- `.Config.Image`;
- the sorted `PathOnHost:PathInContainer` pairs of `.HostConfig.Devices`;
- the `RADIO_URL=` entry of `.Config.Env`.

These are compared field by field. The Compose `config-hash` label is not used,
because its algorithm differs between Compose versions, and the host's Compose
is not the updater's.

Computing the desired configuration costs a Compose call. To avoid one every
two seconds, `$UPDATE_DIR/otbr-upkeep-checked` stores a key: the checksums of
`docker-compose.yml` and `.env` plus the otbr container ID. When the key is
unchanged, nothing is computed.

### 5.3 One attempt per target

- `$UPDATE_DIR/otbr-upkeep-tried` holds the checksum of the desired
  configuration last attempted. An equal checksum is never attempted again
  automatically, so a failing target cannot loop.
- The next desired configuration (a new release, a Thread change on the card)
  has a new checksum and is attempted.
- A radios job that recreates otbr itself also ends the drift.

### 5.4 Pull first

If the desired image differs from the running one:

- `compose --profile thread pull otbr` runs first, while the old container keeps
  running. The pull is bounded by `timeout 600`
  (`LOXMATTER_RADIOS_PULL_TIMEOUT`), well under the radios worker's 900 s
  limit. A pull killed by the entrypoint would write no failure time and would
  start again on the next pass.
- If the pull fails (offline, private package), the script logs it and writes
  the time to `$UPDATE_DIR/otbr-upkeep-pull-failed-at`. It tries again no
  sooner than 1800 s later.
- A pull failure writes no job state and uses up no attempt.

### 5.5 The job

- `JOB_ID="otbr-upkeep-<UTC yyyymmddHHMMSS>"`, `JOB_STEPS='["apply_thread","verify_thread"]'`,
  `ROLLED=false`, `HEALTHY=null`.
- The tried-checksum file is written before the apply, so a pass killed
  mid-job is not repeated.
- `PREVIOUS_IMAGE` is the running container's `.Image` (the image ID).
- The job takes the watchdog lock and runs `apply_thread up` and
  `verify_thread up`: the same functions, the same 60/150 s limits and the same
  fix as a card request.
- Success: `HEALTHY=true` and `write_state done`.
- Failure:
  1. `write_state rollback "<step>_failed"`.
  2. Save the otbr log exactly as the request rollback does.
  3. If the image changed, export `OTBR_IMAGE="$PREVIOUS_IMAGE"`, recreate
     (`apply_thread up`) and verify, then unset it. Otherwise nothing different
     exists to go back to, and the rollback is skipped.
  4. `ROLLED=true`, set `HEALTHY` from that verification (`false` when skipped),
     then `write_state failed "<step>_failed"`.
- Every step logs to `radios-log.txt` with the job ID.

The watchdog keeps looking after a border router that ends unhealthy.

## 6. The bridge and the card

- `GET /api/radios`: `job.kind` is `"otbr_upkeep"` when the job ID starts with
  `otbr-upkeep-`, otherwise `"request"`.
- While such a job runs (not rolling back), the card shows a `banner warn`:

  ```yaml
  web.radios.upkeep_running:
    en: "The Thread border router is being brought up to date after the update. Thread devices are unreachable for about a minute."
    de: "Der Thread-Border-Router wird nach dem Update auf den neuen Stand gebracht. Thread-Geräte sind etwa eine Minute lang nicht erreichbar."
  web.radios.result_upkeep_done:
    en: "The Thread border router was brought up to date."
    de: "Der Thread-Border-Router wurde auf den neuen Stand gebracht."
  web.radios.result_upkeep_failed_restored:
    en: "Bringing the Thread border router up to date failed ({reason}). It runs with its previous version again."
    de: "Der Thread-Border-Router ließ sich nicht auf den neuen Stand bringen ({reason}). Er läuft wieder mit seiner vorherigen Version."
  ```

- `radiosResultKey()` for an upkeep job:
  - `done` → `web.radios.result_upkeep_done`;
  - `failed` with `healthy !== false` → `web.radios.result_upkeep_failed_restored`;
  - `failed` with `healthy === false` → the existing
    `web.radios.result_failed_unhealthy`;
  - `interrupted` → the existing `web.radios.result_interrupted`.
- `radiosThreadLeftOff()` is already false for these jobs, because they carry
  no `requested`.

## 7. The installer

- `report()` no longer prints a crontab line. It says the updater service
  watches the Thread border router.
- In WiFi/Ethernet-only mode, instead of telling the user to set
  `COMPOSE_PROFILES` and `RADIO_DEVICE` in `.env`, it points to Settings →
  Radios.
- `check_thread()` no longer prints `docker exec` commands to bring
  `otbr-agent` up by hand. When Thread is not up yet, it adds a note (not a
  finding) that the border router needs up to a few minutes and that the
  updater's watchdog restarts it if it hangs.

## 8. Release 0.4.1 and the test Pi

- `CHANGELOG.md`, "Before you update": after this update, System → Version asks
  once for the updater refresh command; run it. From then on, the Thread border
  router's watchdog and upkeep run on their own. A 0.4.0 crontab line may stay.
  Right after the refresh, the border router is brought up to date once, and
  Thread devices are unreachable for about a minute.
- The release itself is done by hand, with the maintainer's OK for each step:
  1. merge;
  2. the OTBR workflow runs and the package is set to public;
  3. tag `v0.4.1` and GitHub release;
  4. update the test Pi through the web UI and run the refresh command.
- On the test Pi only, with the maintainer's OK: remove the
  `OTBR_IMAGE=local-otbr:…` line, which would otherwise keep the self-built
  image forever, then watch the upkeep move otbr to the GHCR image.
  - Before the release, one read-only check on the Pi compares the drift inputs
    computed inside the updater container with the running container.

## 9. Tests

- `tests/test_compose_profiles.py` (or a new `tests/test_otbr_image.py`):
  - the compose default matches `source.env`;
  - the tag carries the commit;
  - the workflow reads `source.env`, builds both architectures natively, greps
    for `Trying to recover` and never overwrites an existing tag;
  - `ci.yml` checks the pinned image on tags.
- `tests/test_otbr_watchdog.py`, adapted to the fake `docker`:
  - `ot-ctl state` decides up or down;
  - `docker top` gives the age;
  - `docker restart` comes after the pid removal;
  - the marker line exists;
  - the existing lock, grace and timeout tests are kept.
- `tests/test_updater_entrypoint.py`:
  - the watchdog worker runs after the radios worker in the first pass, with
    its own 300 s limit;
  - it does not run again before its interval;
  - a missing worker is skipped quietly.
- A new `tests/test_updater_watchdog_once.py`:
  - a script without the marker is not run;
  - a script with the marker runs, and its output lands in the log;
  - the log is trimmed.
- `tests/test_updater_image.py`: `bash` is installed, and `watchdog-once.sh` is
  copied and executable.
- `tests/test_updater_radios_script.py`, with the Docker stub extended by
  `inspect`, `compose config` and `compose pull`:
  - no drift → no job;
  - image drift → pull, then job `otbr-upkeep-*` with `apply_thread`, then
    `verify_thread` → `done`;
  - device-path drift alone → job without pull;
  - pull failure → no job, no attempt used up, no retry within 1800 s;
  - the same target is not attempted twice;
  - a failing verify with an image change → rollback with `OTBR_IMAGE` set to
    the previous image ID in the Compose call → `failed`, `rolled_back`,
    `healthy` from the second verify;
  - skipped when an update runs, when Thread is off, when there is no
    container, or when a request is pending;
  - the check cache skips `compose config` when nothing changed.
- `tests/api/test_radios_api.py`: `job.kind`.
- `tests/api/test_web.py`: result keys and running banner for upkeep jobs;
  strings exist in en and de.
- `tests/test_install_script.py`: no crontab line; Radios hint in
  WiFi/Ethernet-only mode; no manual `otbr-agent` commands.
- Every protective test is fault-injected once.

## 10. Out of scope

- Showing `otbr-watchdog.log` in the web UI.
- Moving matter-server or other services to automatic recreation.
- Changing `update-once.sh`.
