# Thread Setup Without Handwork — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fresh installations get the RCP-restoration border router image, the fixed device path and a running watchdog with no extra step; existing installations get the same after the one updater refresh the web UI already shows.

**Architecture:** A GitHub workflow builds a pinned `loxmatter-otbr` image that Compose uses by default. The updater container runs the (now container-capable) watchdog script from the checkout every minute, and `radios-once.sh` recreates otbr when its image, device mapping or radio URL drifts from Compose, with pull-first, verify and image rollback. The bridge labels such jobs so the Radios card explains them.

**Tech Stack:** POSIX sh / bash scripts tested from pytest with sealed fake binaries, GitHub Actions, Docker Compose, FastAPI, Alpine.js.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-14-thread-setup-without-handwork-design.md`. Exact names, file paths, defaults (60 s, 300 s, 600 s, 1800 s, 2000 lines), keys and texts are there; copy them verbatim.
- Never modify `deploy/updater/update-once.sh`.
- Everything in English except `de:` values in `src/loxmatter/i18n/strings.yaml`; German uses formal "Sie". User-visible strings go through i18n (en + de).
- No plan task numbers, "Task N" or TRANSITIONAL markers in `src/`, `deploy/`, `scripts/` or `install.sh`. New files carry the project's 15-line GPL header in the file's comment syntax (copy from an existing file of the same kind, e.g. `deploy/updater/radios-once.sh` for shell).
- Comments explain why, in the density of the surrounding file.
- Conventional Commits in English, ending with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Never push.
- Tests run in the FOREGROUND only (never `run_in_background`, never Monitor). Run only the test files named in the task. `tests/test_updater_radios_script.py` alone takes several minutes: use `-k` while iterating, and run the whole file once at the end with a 600000 ms timeout.
- Every new protective test is fault-injected once: break the code it protects, see the test fail, restore by editing back (never `git checkout` over uncommitted work; purge `__pycache__` after Python injections).
- Checks before each commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run python scripts/check_language.py`.

---

### Task 1: Pinned border router image and release gate

**Files:**
- Create: `deploy/otbr/source.env`, `.github/workflows/otbr-image.yml`, `tests/test_otbr_image.py`
- Modify: `deploy/testhost/docker-compose.yml` (otbr `image:` line and its comment), `.github/workflows/ci.yml` (tag gate), `deploy/testhost/README.md` (where it mentions the otbr image / `OTBR_IMAGE`), `tests/test_compose_profiles.py` only if it asserts the old default

**Interfaces:**
- Produces: `deploy/otbr/source.env` with `OT_BR_POSIX_COMMIT`, `OTBR_OPTIONS`, `OTBR_IMAGE_TAG` (spec §3.1); compose default `ghcr.io/lucienkerl/loxmatter-otbr:2ba12d24-rcp2`.

- [ ] **Step 1: Tests first** in `tests/test_otbr_image.py` (parse files with `yaml.safe_load` / plain text; see how `tests/test_compose_profiles.py` reads the compose file):
  - `source.env` has the three keys; `OTBR_IMAGE_TAG` starts with `OT_BR_POSIX_COMMIT[:8]`; commit is 40 hex chars.
  - The compose otbr image is exactly `${OTBR_IMAGE:-ghcr.io/lucienkerl/loxmatter-otbr:<OTBR_IMAGE_TAG>}`.
  - `otbr-image.yml`: triggers `workflow_dispatch` and `push` on `main` with paths `deploy/otbr/**` and its own path; reads `deploy/otbr/source.env`; a build matrix covering `ubuntu-24.04` + `linux/amd64` and `ubuntu-24.04-arm` + `linux/arm64`; uses `etc/docker/test/Dockerfile`, `BASE_IMAGE=ubuntu:bionic` and `OTBR_OPTIONS`; runs the `Trying to recover` grep check against `/usr/sbin/otbr-agent`; pushes by digest; a merge job using `imagetools create`; an existence check that skips building when the tag already exists; `packages: write`.
  - `ci.yml`: on tags, a step inspects the compose default image (`imagetools inspect`) before any image push step, in a job the image jobs depend on or at the top of each tag-gated image job.
- [ ] **Step 2:** Run `uv run pytest tests/test_otbr_image.py -q` — fails.
- [ ] **Step 3: Implement** the files per spec §3. In the workflow, check out ot-br-posix with `git clone https://github.com/openthread/ot-br-posix.git`, `git checkout "$OT_BR_POSIX_COMMIT"`, `git submodule update --init --recursive`, then `docker buildx build` with `-f etc/docker/test/Dockerfile`, the two build args, OCI labels (spec §3.2), `--platform`, and `--output type=image,name=ghcr.io/lucienkerl/loxmatter-otbr,push-by-digest=true,name-canonical=true,push=true` after a `--load` build + grep check (build once with `--load` to test, then push the same cache). Upload the digest as an artifact per arch; merge job downloads and runs `docker buildx imagetools create -t ghcr.io/lucienkerl/loxmatter-otbr:$OTBR_IMAGE_TAG <repo>@sha256:...`. Use `docker/setup-buildx-action` and `docker/login-action` at the versions `ci.yml` already uses. In `ci.yml`, read the compose default from the file with a small shell step (grep the `image: ${OTBR_IMAGE:-...}` line) — keep it independent of Python.
- [ ] **Step 4:** Tests pass; run `uv run pytest tests/test_compose_profiles.py tests/test_otbr_image.py -q`. Fault-inject: (a) change the tag in compose, (b) drop the grep check step, (c) remove the tag gate from ci.yml.
- [ ] **Step 5:** Checks; commit `feat(otbr): build a pinned border router image with RCP restoration`.

---

### Task 2: A watchdog that also runs inside a container

**Files:**
- Modify: `scripts/otbr-watchdog.sh`, `tests/test_otbr_watchdog.py`

**Interfaces:**
- Produces: marker line `# loxmatter-watchdog: container-ready` in `scripts/otbr-watchdog.sh`; the script no longer reads `/proc/net/if_inet6`, no longer calls `ps` or `docker compose`, no longer `cd`s.

- [ ] **Step 1: Adapt tests** (read the whole file first; keep its fake-docker approach, extend `_DOCKER_STUB`):
  - `exec ... ot-ctl state` answers from an env var (e.g. `DOCKER_OTCTL_STATE`, default `leader`); `top` prints a header `ETIMES` and lines from an env var (default a day, e.g. `86400`); `restart` exits with `DOCKER_RESTART_STATUS`.
  - Up states `leader`/`router`/`child` are left alone (parametrize); `detached`/`disabled`/empty/failing exec restart.
  - The pid removal (`exec otbr rm -f /run/otbr-agent.pid`) comes before `restart otbr`.
  - Age from `docker top otbr -o etimes`: the largest value decides; under 90 s → left alone; unreadable → restart without grace (keep the existing test's intent).
  - The recovery wait polls `ot-ctl state` and reports "Thread network is back".
  - Hang/timeout tests keep working for `exec`, `top` and `restart` (the restart keeps its longer limit).
  - The marker line exists exactly once.
  - No call to `docker compose` anywhere in the recorded calls; the script source contains no `/proc/net/if_inet6`.
  - Remove the `IF_INET6` seam and its fixtures.
- [ ] **Step 2:** `uv run pytest tests/test_otbr_watchdog.py -q` — fails.
- [ ] **Step 3: Implement** spec §4.1. Keep `set -euo pipefail`, the `flock` block, `GRACE_SECONDS=90`, `DOCKER_TIMEOUT`, `RESTART_TIMEOUT`, `bounded`, `timed_out` and the log lines. Update the header comment (how it runs: from the updater service every minute; a host cron line also works and shares the lock). Thread check: `thread_is_up() { case "$(bounded "$DOCKER_TIMEOUT" docker exec "$SERVICE" ot-ctl state 2>/dev/null | tr -d '\r' | head -n 1)" in leader|router|child) return 0 ;; esac; return 1; }` (keep `pipefail` from turning a failing exec into an abort — wrap so any failure means "down").
- [ ] **Step 4:** Tests pass. Fault-inject: (a) treat `detached` as up, (b) restart before removing the pid, (c) take the smallest `etimes` instead of the largest, (d) drop the marker.
- [ ] **Step 5:** Checks; commit `fix(watchdog): measure Thread through docker so the watchdog runs in a container`.

---

### Task 3: The updater service runs the watchdog

**Files:**
- Create: `deploy/updater/watchdog-once.sh`, `tests/test_updater_watchdog_once.py`
- Modify: `deploy/updater/entrypoint.sh`, `deploy/updater/Dockerfile`, `tests/test_updater_entrypoint.py`, `tests/test_updater_image.py`

**Interfaces:**
- Consumes: the marker line from Task 2.
- Produces: `/opt/loxmatter/watchdog-once.sh`; env `WATCHDOG_WORKER`, `WATCHDOG_INTERVAL_SECONDS` (60), `WATCHDOG_WORKER_TIMEOUT_SECONDS` (300), `LOXMATTER_WATCHDOG_SCRIPT`.

- [ ] **Step 1: Tests** (follow `tests/test_updater_entrypoint.py`'s `_script`/`_run`/`_fake_timeout_logging_to` helpers and `LOOP_ONCE`):
  - Entrypoint, one pass: the watchdog worker runs after the radios worker, under `timeout 300`; configurable via `WATCHDOG_WORKER_TIMEOUT_SECONDS`; a missing/non-executable worker is skipped quietly; a failing watchdog does not end the loop.
  - Interval: two passes with a fake `date +%s` (or `WATCHDOG_INTERVAL_SECONDS` large) → runs once; with interval 0 → runs twice. Use a bounded pass count mechanism the entrypoint already has, or add `LOOP_PASSES` only if `LOOP_ONCE` cannot express it — prefer a fake `sleep` that exits the loop after N calls via the existing `terminated` path (SIGTERM) if that is simpler; decide from reading the file.
  - `watchdog-once.sh`: a script without the marker is not run (no output file, exit 0); a missing script exits 0; a script with the marker is run with `bash` and its stdout+stderr land in `$LOXMATTER_UPDATE_DIR/otbr-watchdog.log`; a log of 2500 lines ends with 2000 lines, the newest kept.
  - Image: `tests/test_updater_image.py` asserts `bash` in the `apk add` list and `watchdog-once.sh` in the `COPY` and `chmod` lines.
- [ ] **Step 2:** Run the three test files — new tests fail.
- [ ] **Step 3: Implement** spec §4.2–4.4. `watchdog-once.sh` is POSIX sh (`set -u`), GPL header, comment explaining the marker (spec §4.2 reason). Entrypoint: keep `run_worker` unchanged; add the block after the radios worker with the same `[ "$terminated" -eq 1 ] && break` pattern and a comment on why sequential (spec §4.3).
- [ ] **Step 4:** Tests pass (`uv run pytest tests/test_updater_entrypoint.py tests/test_updater_watchdog_once.py tests/test_updater_image.py -q`). Fault-inject: (a) run a script without marker, (b) skip trimming, (c) drop the interval check, (d) remove `bash` from the Dockerfile.
- [ ] **Step 5:** Checks; commit `feat(updater): run the Thread watchdog every minute`.

---

### Task 4: otbr follows its Compose configuration

**Files:**
- Modify: `deploy/updater/radios-once.sh`, `tests/test_updater_radios_script.py`

**Interfaces:**
- Produces: jobs with id `otbr-upkeep-<yyyymmddHHMMSS>`, steps `["apply_thread","verify_thread"]`, error keys `apply_thread_failed`/`verify_thread_failed` (already translated); files `otbr-upkeep-checked`, `otbr-upkeep-tried`, `otbr-upkeep-pull-failed-at` in the update dir; env `LOXMATTER_RADIOS_PULL_TIMEOUT` (600), `LOXMATTER_RADIOS_PULL_RETRY` (1800).

- [ ] **Step 1: Tests** (read the harness at the top of the file — `DOCKER_STUB`, the `radios` fixture, `ADVANCE` and the fake epoch — and extend the stub):
  - `docker inspect otbr` prints a JSON array built from fake files (`$FAKE/actual_image`, `$FAKE/actual_image_id`, `$FAKE/actual_devices`, `$FAKE/actual_radio_url`, container `Id`), or honour `-f` formats if the implementation uses them — pick one and keep the stub minimal.
  - `docker compose ... config --format json` prints a JSON document with `services.otbr.image`, `devices` (test both string and object forms) and `environment.RADIO_URL` from fake files; `compose ... pull otbr` records the call and exits `$FAKE/pull_status`; the `compose up` branch records `OTBR_IMAGE` from the environment into the stub log (e.g. `printf 'env OTBR_IMAGE=%s\n' "${OTBR_IMAGE:-}"`).
  - Cases from spec §9 (radios script bullet): no drift → no job and state `id` unchanged; image drift → pull then `otbr-upkeep-*` job → `done`, `healthy: true`; device drift alone → job without pull; string-form and object-form devices compare equal to the same inspect pairs; pull failure → no job, `otbr-upkeep-tried` absent, no second pull within 1800 s (advance the fake epoch) and a retry after; same target not attempted twice; verify failure with image change → second `compose up` carries `OTBR_IMAGE=<previous image id>`, state `failed`, `rolled_back: true`, `healthy` from the second verify (both outcomes); verify failure with device drift only → no second `up`, `healthy: false`; skipped when `state.json` phase is `pull`, when `COMPOSE_PROFILES` lacks `thread`, when no otbr container exists, when `CAPABLE` is false, and when a request file exists (the request path runs instead); cache: a second pass with unchanged compose/.env/container id makes no `compose config` call, a changed `.env` makes one; the upkeep takes the watchdog lock; the pull runs under `timeout 600`.
- [ ] **Step 2:** `uv run pytest tests/test_updater_radios_script.py -q -k upkeep` — fails.
- [ ] **Step 3: Implement** spec §5 as functions defined before the request handling (the request path's `apply_thread`, `verify_thread`, `take_watchdog_lock`, `compose` and the otbr-log saving must be reachable: move the function definitions above the `[ -e "$REQUEST" ] || exit 0` line without changing their bodies, and factor the otbr-log saving into a function used by both paths). Replace `[ -e "$REQUEST" ] || exit 0` with `[ -e "$REQUEST" ] || { otbr_upkeep; exit 0; }`. `take_watchdog_lock` logs with `$JOB_ID`, which is set to the upkeep id by then. Use `cksum` for checksums. Keep comments explaining each guard (why field comparison not config-hash, why tried-file before apply, why pull first and bounded).
- [ ] **Step 4:** Run the upkeep tests, then the whole file once (600000 ms timeout). Fault-inject: (a) compare nothing (always drift), (b) write the tried file after the apply, (c) drop the pull-retry wait, (d) roll back without `OTBR_IMAGE`, (e) skip the running-update guard.
- [ ] **Step 5:** Checks; commit `feat(radios): bring otbr up to date when Compose asks for a different container`.

---

### Task 5: The bridge and card explain an upkeep job

**Files:**
- Modify: `src/loxmatter/api/radios.py`, `src/loxmatter/i18n/strings.yaml`, `src/loxmatter/web/app.js`, `src/loxmatter/web/index.html`, `tests/api/test_radios_api.py`, `tests/api/test_web.py`

**Interfaces:**
- Consumes: job ids `otbr-upkeep-*` from Task 4.
- Produces: `job.kind` ∈ `"otbr_upkeep" | "request"`; app.js `radiosUpkeepRunning() -> boolean`.

- [ ] **Step 1: Tests:** API: `kind` for both id forms (extend `test_the_latest_job_is_reported`'s expected dict with `"kind": "request"`). Web (append at the END of `tests/api/test_web.py`, reuse `RADIOS_READY`/`_radios_values`/`_x_show_expr`): `radiosResultKey()` for an upkeep job in `done`, `failed` healthy, `failed` unhealthy, `failed` interrupted; a request job keeps its old keys; `radiosUpkeepRunning()` true for a running upkeep job, false while rolling back, false for a running request job, false for a terminal upkeep job; the banner's `x-show` is `radiosUpkeepRunning()` with `t('web.radios.upkeep_running')`; the three strings exist in en and de.
- [ ] **Step 2:** Run `uv run pytest tests/api/test_radios_api.py -q` and `uv run pytest tests/api/test_web.py -q -k "upkeep"` — fail.
- [ ] **Step 3: Implement** spec §6: in `radiosResultKey()` branch on `job.kind === "otbr_upkeep"` before the request logic (interrupted and unhealthy keep existing keys); banner paragraph next to the `rolling_back` banner, `class="banner warn"`, `x-cloak`.
- [ ] **Step 4:** Tests pass; whole `tests/api/test_web.py` once and `tests/api/test_radios_api.py`. Fault-inject: (a) `kind` always `request`, (b) upkeep `failed` healthy returns the request key, (c) banner shown while rolling back.
- [ ] **Step 5:** Checks; commit `feat(web): say when the border router is brought up to date`.

---

### Task 6: Installer, docs and release notes

**Files:**
- Modify: `install.sh`, `tests/test_install_script.py`, `CHANGELOG.md`, `deploy/testhost/README.md` (watchdog section, if any, and `OTBR_IMAGE`), `README.md` (only where it mentions the crontab/watchdog or `COMPOSE_PROFILES` editing for Thread)

- [ ] **Step 1: Tests** in `tests/test_install_script.py` (read how existing tests run `report`/`check_thread` with fakes): the Thread-mode summary contains no `crontab` and no `otbr-watchdog.sh` path; it mentions that the updater service watches the border router; WiFi/Ethernet-only mode names Settings → Radios and no longer tells the user to set `COMPOSE_PROFILES`; `check_thread` with no Thread interface prints no `docker exec otbr` commands and adds no finding (a note instead).
- [ ] **Step 2:** Run `uv run pytest tests/test_install_script.py -q` — new tests fail.
- [ ] **Step 3: Implement** spec §7 in `install.sh`. `CHANGELOG.md`: a `[Unreleased]` entry per spec §8 (Before you update: the one refresh command System → Version shows; Thread briefly unreachable once afterwards; an old crontab line may stay) plus Added/Changed/Fixed items written for non-technical readers (border router image with automatic recovery from lost radio frames; watchdog built in; otbr brought up to date automatically; installer no longer asks for crontab). Docs: replace crontab instructions with the new behaviour; describe `deploy/otbr/source.env` as the way to move to a newer OTBR in `docs/DEVELOPMENT.md`'s release section (one short paragraph: change the commit and tag, let the workflow build, make sure the package is public, then release — the tag gate refuses otherwise).
- [ ] **Step 4:** Tests pass (the whole `tests/test_install_script.py`, 600000 ms timeout). Fault-inject: (a) print the crontab line again, (b) keep the `docker exec` finding.
- [ ] **Step 5:** Checks; commit `docs: Thread needs no crontab or hand-started border router any more`.
