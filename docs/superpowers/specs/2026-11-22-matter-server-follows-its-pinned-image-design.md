# matter-server follows its pinned image

Date: 22 November 2026. Follows "Thread setup without handwork" (2026-09-14),
section 5, and the 0.8.0 release.

## 1. The problem

0.8.0 pins `matter-server` to `ghcr.io/matter-js/matterjs-server:1.6.0` in
`deploy/testhost/docker-compose.yml`. Until then the file said `:stable`,
and nothing ever pulled that tag again: on the test Pi matter-server still ran
1.4.0 from August. Its matter.js (0.17.9) left a Bluetooth peripheral in
noble's `error` state after one failed connection and refused every later
attempt until matter-server restarted (fixed in matter.js 0.18.0,
matter-js/matter.js#4233). Bluetooth commissioning failed on 22 November
2026 for exactly that reason.

The pin reaches new installations only. `update-once.sh` recreates the
`loxmatter` service alone, `scripts/update.sh` uses `--no-deps` on purpose,
and the radios job recreates matter-server only when the Bluetooth adapter
changes. Every existing installation keeps whatever image it first pulled
until someone follows "Moving matter-server to the pinned version" in
`deploy/testhost/README.md` by hand. The test Pi was moved that way on
22 November (backup first; all 27 nodes kept).

matter-server is not otbr. It holds every commissioned device's fabric data
in `./data`. On 23 September 2026 an unprepared image switch brought it up
with 0 nodes: every device silently gone from the bridge. Whatever moves it
automatically has to assume the new image can do that again.

## 2. Goal

- An existing installation whose matter-server runs a different version of
  the image the Compose file pins is moved to the pinned version by the
  updater sidecar, with no step on the host.
- Before the move, `./data` is backed up. After it, matter-server must report
  the same nodes it reported before. If it does not, the previous image and
  the backup come back.
- A failed move is not repeated until the Compose file asks for a different
  image.
- Nothing here acts on any other difference between the running container
  and its Compose configuration.

## 3. Where it runs

A fourth worker, `deploy/updater/matter-server-once.sh`, run by
`entrypoint.sh` after `radios-once.sh` and before the watchdog, in the same
pass and with its own limit (`MATTER_SERVER_WORKER_TIMEOUT_SECONDS`, 900 s).
The loop already runs its workers one after the other, so this one never
overlaps an update, a radios request or the otbr upkeep.

Not a function inside `radios-once.sh` next to `otbr_upkeep`, although the
shape is the same. The radios job's state file is the Radios card's: its
steps, phases and error keys are shown there and copied into
`src/loxmatter/radios/sidecar.py` and `app.js` (the three-copy rule in
`load_previous_state`). A matter-server job has steps the card does not
know, and its worst case - two verifications of up to 240 s plus a restore -
would share the radios worker's 900 s with a Thread request's own 420 s.

The new script reaches an installation with the updater image that carries
it. System → Version shows the refresh command when the updater image
changes, as it did for 0.4.1.

## 4. Drift: the image, nothing else

- **Desired:** `.services["matter-server"].image` from `docker compose config
  --format json`, with the same `-f`, `--project-directory` and `--env-file`
  as every other compose call of the sidecar.
- **Actual:** `.Config.Image` from `docker inspect --type container
  matter-server` - the reference the container was created from, as
  written.

Equal: nothing to do. The comparison is of the reference, not the digest: a
pinned tag that is moved upstream is not drift, and the Compose file pins
versions for exactly that reason.

The other fields the otbr upkeep compares (devices, command, log cap) are
deliberately not compared. A recreate of matter-server costs a minute of
every Matter device and a restart of a process holding the fabric; a changed
log cap is not worth that. When the image does differ, the recreate builds
the container from the whole current Compose configuration, so the other
fields are corrected on the way - but they never cause a recreate.

The result of `compose config` is cached under the checksums of
`docker-compose.yml` and `.env`, as the otbr upkeep does: the worker runs
every two seconds and a Compose run costs the Pi a noticeable share of a
core.

## 5. When it does not act

Each of these leaves matter-server exactly as it is and logs one line:

1. **An update is running** (state.json in `queued`, `backup`, `pull`,
   `build`, `recreate`, `health` or `rollback`), or the radios job's phase is
   not terminal.
2. **The stack path on the host is unknown** (`LOXMATTER_STACK_HOST_PATH`
   empty): compose would resolve `./data` against the wrong directory.
3. **No matter-server container exists.** Creating one is the installer's
   job, not an upkeep's.
4. **A different image, not a different version.** The repository part of
   the two references (everything before the tag or digest) must be the
   same. `python-matter-server` to `matterjs-server` is a migration that
   needs a `chown` and a verified backup first (README, "Migration to
   matterjs-server"); doing it unprepared is what lost every node on
   23 September.
5. **`./data` is not under the stack directory.** The volume mounted at
   `/data` in the Compose configuration must have its source under
   `$LOXMATTER_STACK_HOST_PATH`, which the sidecar sees under
   `$LOXMATTER_STACK`. A data directory anywhere else cannot be backed up
   from here.
6. **The node count before the move is unknown.** See section 7. The
   matching line is no longer in the running container's log.
7. **This target was attempted already** (section 8).

Cases 4 to 6 are recorded in `matter-server-upkeep-held` with the target and
the container's ID and start time. While those stay the same the worker
returns without reading the log again: `docker logs` of a matter-server that
has run for weeks is not something to repeat every two seconds. A
`docker restart matter-server` changes the start time and so re-evaluates
case 6, which is what the log line tells the operator to do.

## 6. Pull first

As in the otbr upkeep (2026-09-14, 5.4): an image not yet on the host is
pulled with `compose pull --quiet matter-server` while the old container
keeps running, bounded by `PULL_TIMEOUT` (600 s), with the heartbeat moving.
A successful pull ends the pass; the job runs in the next one with the whole
time budget. A failed pull is no attempt and is tried again after
`PULL_RETRY` (1800 s), for the same target only.

## 7. Counting nodes

matterjs-server logs one line when it has loaded its nodes, measured on the
test Pi on 22 November 2026 at the move from 1.4.0 to 1.6.0:

    Registered 27 of 27 nodes

The worker reads the last such line of `docker logs matter-server` as
`R of T`. Before the move that comes from the running container. Afterwards
the recreated container is a new container with a log of its own, so any line
there was written by the new image.

A move is healthy when, within `MATTER_SERVER_VERIFY_TIMEOUT` (240 s), the
new container is running and its line reports the same `T` and at least the
same `R`. A line with a smaller count fails at once; there is no point
waiting for a server that has already said it knows fewer nodes. No line in
240 s fails too.

Why the log and not the WebSocket API: the sidecar is a shell image with
`curl` and `jq`, and has no WebSocket client that can send `get_nodes` and
read the answer in a script; adding one (a Python or Node runtime) to a
root-equivalent image for one check is not worth it. The log line is also
what the manual path in the README checks, so both paths check the same
thing.

Why a missing "before" line stops the job (section 5, case 6) instead of
being replaced by something weaker: the count is the whole safety argument.
A rotated-away line most likely means a long-running, healthy server, and
the operator can get the line back with one restart - safer than a move
whose result nothing can judge.

## 8. One attempt per target

The target is the checksum of the desired image reference. It is written to
`matter-server-upkeep-tried` before the job changes anything, so a pass
killed in the middle is not started again by the next one. A rolled-back
image is not tried again until the Compose file pins a different one - a
new release.

## 9. The job

`JOB_ID` is `matter-server-upkeep-<stamp>`, the stamp a UTC timestamp. Steps, each written to
`matter-server-state.json` before it starts:

1. **`stop`** - `docker stop matter-server`. The backup must not race the
   server's own writes. A failed stop ends as `failed` / `stop_failed`;
   nothing was changed.
2. **`backup`** - `tar czf --numeric-owner` of the data directory into
   `$LOXMATTER_BACKUP_DIR` (`/data/backups`, the volume `update-once.sh`
   keeps its database backups in) as `matter-server-data-<stamp>.tgz`. The
   archive is listed again with `tar tzf`; both must succeed and it must
   contain at least one file. The newest three are kept.

   The README's manual path runs `tar` in a helper container with
   `--user 0:0` because the user `pi` cannot read the root-owned node file.
   The sidecar already runs as root (its image sets no `USER`) and sees the
   checkout at `/repo`, so it reads the directory directly.

   A failed backup starts the old container again (`docker start
   matter-server`) and ends as `failed` / `backup_failed`. Nothing changed.
3. **`recreate`** - `compose up -d --no-deps --force-recreate --quiet-pull
   matter-server`.
4. **`verify`** - section 7.

On a failed `recreate` or `verify`, **`rollback`**:

1. The failed container's last 400 log lines are saved as
   `<JOB_ID>.log` in the update directory (newest five kept), then
   `docker stop matter-server`.
2. The archive is unpacked into a new directory beside the data directory,
   `data.restore-<stamp>`. Only when that succeeds are the two swapped:
   `data` becomes `data.failed-<stamp>`, kept for troubleshooting, and the
   restored directory becomes `data`. Unpacking into place instead would
   leave every file the new image created, because `tar` deletes nothing
   (README, "If it fails").
3. matter-server is recreated from the image it ran before, through a
   Compose override file in the update directory that sets only `image:`.
   Not an environment variable as with `OTBR_IMAGE`: the Compose file pins
   matter-server literally, and an override works whatever the file says.
   The override names the reference the old container was created from
   while that reference still resolves to the same image ID on the host,
   and the image ID otherwise. A container created from a bare ID would
   carry no repository in its reference; section 5, case 4 then reads the
   repository from the image's first tag, so the next release's move is
   not held as "another image".
4. The same verification, against the count from before the move.
   `healthy` reports its result.

If the restore itself fails (step 2), matter-server stays **stopped**: a
server started on a half-restored or new-format directory can do more
damage than one that is not running. The state ends `failed` /
`restore_failed` with `healthy: false`, and the log names the archive and
the manual restore.

`data.restore-*` and `data.failed-*` are added to `deploy/testhost/.gitignore`
so a rolled-back move does not show up as an untracked change in the
checkout.

## 10. State and heartbeat

`matter-server-state.json` in the update directory:

```json
{"id": "matter-server-upkeep-20261122120000", "phase": "done",
 "error": null, "rolled_back": false, "healthy": true,
 "from": "ghcr.io/matter-js/matterjs-server:stable",
 "to": "ghcr.io/matter-js/matterjs-server:1.6.0",
 "nodes_before": "27 of 27", "nodes_after": "27 of 27",
 "backup": "matter-server-data-20261122120000.tgz",
 "seen_at": "2026-11-22T12:01:10Z"}
```

Terminal phases: `idle`, `done`, `failed`. A non-terminal phase found at the
start of a pass means the previous pass died, and is recorded as `failed` /
`interrupted`, as in `radios-once.sh`. If it died in `stop` or `backup`, the
old container is stopped and its data untouched, so the next pass starts it
again (`docker start matter-server`). In any later phase it does not touch
matter-server: what is running there is unknown.

The web UI does not read this file yet (section 12). The log is
`matter-server-log.txt` beside it, trimmed to 2000 lines.

The heartbeat works as in `radios-once.sh` (`refresh_heartbeat`): while the
job waits, `updater_seen_at` in `state.json` and `seen_at` in
`radios-state.json` are moved forward, so neither the update card nor the
Radios card reads a working sidecar as a missing one. Safe for the same
reason it is safe there: the workers run one after the other.

## 11. Time budget

Inside the worker's 900 s: stop (Compose's 10 s grace), backup (seconds; the
test Pi's directory is a few MB), recreate, verify up to 240 s; then the
rollback's stop, restore, recreate and verify up to another 240 s. About
520 s in the worst case. The pull never shares a pass with the job.

## 12. Out of scope

- **The web UI.** The job is reported in its own state file and log. A line
  under System → Version ("matter-server moved to 1.6.0, 27 of 27 nodes")
  is a follow-up.
- **The radios job's Bluetooth recreate** is not out of scope; it is
  decided here, so that this worker stays the only thing that changes
  matter-server's image. A Bluetooth change on the Radios card has to
  recreate matter-server, because the adapter is one of its environment
  variables. Until now that recreate ran on `docker-compose.yml` alone and
  so brought the pinned image along, without a backup or a node count -
  and after a rolled-back move, the image that had just failed.

  `radios-once.sh` now recreates matter-server through an override file in
  the update directory, `radios-matter-server.yml`, that sets only
  `image:`, chosen as this worker's rollback chooses it (section 9): the
  container's `.Config.Image` while that still resolves to the same ID as
  its `.Image`, the image ID otherwise. The image is read once, at the
  first recreate of a pass, and kept for the rollback's, which therefore
  goes back to the image from before the request even if the forward
  recreate removed the container. With no matter-server container to read
  it from, the step fails (`apply_bluetooth_failed`) and nothing is
  created: falling back to the pinned image is exactly the move this job
  must leave alone. `apply_and_verify` and `rollback_and_verify` call the
  same helper, `recreate_matter_server`.

  A host whose move was rolled back therefore keeps the old image through
  any number of Bluetooth changes, and section 8 keeps this worker from
  trying the same target again; the next release's pin is the next move.
- **`python-matter-server` installations** (section 5, case 4) stay on the
  manual migration.
- **`scripts/update.sh`** keeps leaving matter-server alone. The README's
  manual path stays for hosts without the sidecar.

## 13. Tests

`tests/test_updater_matter_server_script.py`, in the style of
`tests/test_updater_radios_script.py`: a sealed `PATH` with a `docker` stub
driven by files in a fake directory, a clock in a file, and real `tar` on a
real data directory under `tmp_path`. Every test names the fault that would
make it fail. Covered: no drift; each case of section 5; pull in one pass and
the job in the next; a failed pull waits; backup before recreate and its
contents; a failed backup restarts the old container; the healthy move; a
lower count and a missing count each roll back to the previous image ID
with the backup restored and the failed directory kept; a failed restore
leaves matter-server stopped; one attempt per target; an interrupted pass;
the heartbeat moves through the verification.

`tests/test_updater_radios_script.py` covers section 12's Bluetooth
recreate: the forward recreate and the rollback's each keep the running
image; a reference that names another image now, or none, is replaced by
the image ID; without a container nothing is created.

`tests/test_updater_entrypoint.py` and `tests/test_updater_image.py` pin the
new worker's place in the loop and in the image.
