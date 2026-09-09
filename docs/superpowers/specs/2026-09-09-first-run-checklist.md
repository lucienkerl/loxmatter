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
