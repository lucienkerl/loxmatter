# Updates through the web UI, stage 2: the sidecar and the button

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisite:** [Stage 1](2026-09-08-webui-updates-stufe1.md) is fully implemented and version `0.2.0` is published as `ghcr.io/lucienkerl/loxmatter:stable`. Without an image to update to, there is nothing to test here — the rollback least of all.

**Goal:** A click in the System tab installs a new version, visibly step by step, even while the bridge itself is momentarily not responding — and rolls itself back automatically if the new version does not come up healthy.

**Architecture:** An additional container `loxmatter-updater` holds the Docker socket, has no ports and no host network, and communicates with the bridge exclusively through three files in a shared volume. Because the state lives there and not in the bridge's memory, progress stays visible through the restart. The security boundary sits in the sidecar, not at the login: it assembles the image name itself, checks the target against a fixed pattern, and only ever allows moving forward.

**Tech Stack:** POSIX sh (busybox/Alpine), `jq`, Docker CLI + Compose plugin, git, curl, Python 3.12/FastAPI/Pydantic, Alpine.js, pytest.

**Basis:** [`docs/superpowers/specs/2026-09-08-webui-updates-design.md`](../specs/2026-09-08-webui-updates-design.md), sections 6–14.

## Global Constraints

- **Every new source file starts with the GPL header** in the official English FSF wording, word-for-word identical to existing files (template: `src/loxmatter/api/settings.py:1-15`). For shell files, in the `#`-comment form as in `scripts/update.sh:1-16`.
- **Developer prose in English**, dense and reasoned.
- **Every user-visible text goes through `i18n.t()`** with both an `en` **and** a `de` entry in `src/loxmatter/i18n/strings.yaml`.
- **POSIX sh, not bash.** The updater image is Alpine; `/bin/sh` is busybox, and a `[[` dies there. Every new shell file must pass `shellcheck -s sh`.
- **Fixed names**, the same everywhere: volume path `/data/update/`, files `request.json`, `state.json`, `log.txt`, `LETZTER-FEHLSCHLAG.txt` (German for "last failure" — the name is a fixed identifier pinned across this plan and a test, and stays as is); service name `loxmatter-updater`; registry `ghcr.io/lucienkerl/loxmatter` and `ghcr.io/lucienkerl/loxmatter-updater`.
- **Phases** (the value of `state.json.phase`), exhaustively: `idle`, `queued`, `backup`, `pull`, `recreate`, `health`, `rollback`, `done`, `failed`, `rejected`.
- **The updater never interprets text from the request as a command.** No `eval`, no variable from `request.json` in a command position, no image name taken from the request.
- **The full test suite takes about three minutes.** Do not interrupt it.
- **Before every commit:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`, and for shell changes `shellcheck -s sh <file>`.
- **Every test must be made to fail once, on a trial basis.**

## File Structure

| File | Responsibility |
|---|---|
| `deploy/updater/Dockerfile` (new) | The sidecar image: Alpine + docker-cli + compose + git + curl + jq + coreutils. |
| `deploy/updater/entrypoint.sh` (new) | The loop. Three lines: do one round of work, sleep two seconds. |
| `deploy/updater/update-once.sh` (new) | One round: refresh the heartbeat, check for a request, execute it, write state. All of the sidecar's logic, and all of it testable without the loop. |
| `deploy/testhost/docker-compose.yml` (modify) | The new service. |
| `.github/workflows/ci.yml` (modify) | A second image job for the sidecar. |
| `src/loxmatter/update.py` (new) | Write the request (atomically), read the state, judge the sidecar's presence. Knows no HTTP concepts. |
| `src/loxmatter/update_check.py` (new) | Asks GitHub about the release or `main`. Knows no files. |
| `src/loxmatter/api/update.py` (new) | The three routes. Glues the two modules to HTTP, nothing else. |
| `src/loxmatter/model/store.py` (modify) | Channel setting and "checking allowed" toggle, like the other settings. |
| `src/loxmatter/web/index.html`, `app.js`, `i18n/strings.yaml` (modify) | The card with four states. |
| `tests/test_updater_script.py`, `tests/test_update_module.py`, `tests/api/test_update_api.py`, `tests/test_update_check.py` (new) | see the respective task. |

**Why three Python modules and not one:** `update.py` touches files, `update_check.py` touches the network, `api/update.py` touches neither. Kept separate, each can be tested on its own — the network part without a filesystem, the file part without a network, and the routes against both as a fake.

---

### Task 1: The sidecar image

**Files:**
- Create: `deploy/updater/Dockerfile`, `deploy/updater/entrypoint.sh`
- Modify: `.github/workflows/ci.yml` (second job `updater-image`)
- Test: `tests/test_updater_image.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ghcr.io/lucienkerl/loxmatter-updater:<version>` and `:stable`. The entrypoint calls `/opt/loxmatter/update-once.sh` (Task 2) in a loop.

- [ ] **Step 1: Write the failing test**

`tests/test_updater_image.py` (GPL header, then):

```python
"""The sidecar image brings along exactly the tools the script uses.

The failure this guards against is unpleasantly quiet: if `jq` is missing
from the image, the sidecar starts up, never writes a usable state, and
the web UI shows a button that does nothing. A comparison between the
`apk add` lines and the commands invoked in the script catches this here,
before someone discovers it on a Pi."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "deploy" / "updater" / "Dockerfile"

# What the script from task 2/3/4 invokes and what Alpine does NOT bring
# along by itself. `sh`, `mv`, `printf` are deliberately absent here - those
# are busybox built-ins and cannot be missing.
REQUIRED_PACKAGES = ("docker-cli", "docker-cli-compose", "git", "curl", "jq", "coreutils", "tar")


def test_das_image_bringt_jedes_benutzte_werkzeug_mit() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    for package in REQUIRED_PACKAGES:
        assert re.search(rf"\b{re.escape(package)}\b", source), package


def test_die_basis_ist_gepinnt() -> None:
    # A `FROM alpine:latest` would turn every rebuild of the sidecar into a
    # surprise - of all containers, the one that is root-equivalent on the
    # host.
    source = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^FROM alpine:3\.\d+", source, re.MULTILINE)
    assert "alpine:latest" not in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_updater_image.py -v`
Expected: FAIL — `FileNotFoundError` for `deploy/updater/Dockerfile`

- [ ] **Step 3: Write the Dockerfile**

`deploy/updater/Dockerfile`:

```dockerfile
# The updater sidecar (design "Applying updates through the web UI",
# 2026-09-08, section 6).
#
# This container holds the Docker socket and is thereby root-equivalent
# on the host. That is secured through narrowness, not through privileges:
# no ports, no host network (see the Compose file), a single fixed
# work program - and this base pinned instead of `latest`, so a rebuild
# is never a surprise.
#
# Deliberately NOT the loxmatter image with a different entrypoint: the
# sidecar must not be the same image as the one it replaces, or an update
# would replace the process that is running it.
FROM alpine:3.20

# docker-cli-compose brings the `docker compose` subcommand along - the
# sidecar only ever invokes Compose, never a bare `docker run` or
# `docker build`. The reason has been in scripts/update.sh since 2026-09-03:
# the service builds its own image via its `build:` block, and one built
# alongside it would go unused.
#
# coreutils for `sort -V`: the "forward only" check (spec section
# 10, rule 3) compares semantic versions, and busybox's sort cannot
# do that reliably.
RUN apk add --no-cache \
      docker-cli \
      docker-cli-compose \
      git \
      curl \
      jq \
      coreutils \
      tar

COPY entrypoint.sh update-once.sh /opt/loxmatter/
RUN chmod +x /opt/loxmatter/entrypoint.sh /opt/loxmatter/update-once.sh

ENTRYPOINT ["/opt/loxmatter/entrypoint.sh"]
```

- [ ] **Step 4: Write the entrypoint**

`deploy/updater/entrypoint.sh` (GPL header in `#` form, then):

```sh
#!/bin/sh
# The sidecar's loop - design "Applying updates through the web UI"
# (2026-09-08), section 6.
#
# Deliberately thin: all the logic lives in update-once.sh, entirely. Only
# that way can a single run be invoked in a test, without spinning up an
# endless loop and having to kill it again.
#
# `|| true`: a single failed run must not terminate the sidecar. It is the
# only one still able to report a broken state at all - a container that
# exits on error takes exactly that report down with it.
set -u

while true; do
  /opt/loxmatter/update-once.sh || true
  sleep 2
done
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_updater_image.py -v && shellcheck -s sh deploy/updater/entrypoint.sh`
Expected: 2 passed, shellcheck still clean

- [ ] **Step 6: Add the CI job**

In `.github/workflows/ci.yml`, after the job `image`:

```yaml

  # The sidecar. Its own job and its own image, because it must NOT be the
  # same image as the one it replaces: otherwise an update would replace
  # the process that is running it. It changes rarely - hence no :dev tag,
  # only on releases.
  updater-image:
    needs: test
    if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: deploy/updater
          platforms: linux/amd64,linux/arm64
          push: true
          tags: |
            ghcr.io/lucienkerl/loxmatter-updater:${{ github.ref_name }}
            ghcr.io/lucienkerl/loxmatter-updater:stable
```

- [ ] **Step 7: Commit**

```bash
git add deploy/updater/ .github/workflows/ci.yml tests/test_updater_image.py
git commit -m "feat(updater): sidecar image and loop

A dedicated, tiny image - deliberately NOT the loxmatter image with a
different entrypoint: the sidecar must not be the same image as the one
it replaces, or an update would replace the process that is running it.

The base pinned instead of latest. For a container that holds the Docker
socket and is thereby root-equivalent on the host, a surprising rebuild
is the wrong kind of convenience.

The loop stays thin, all the logic lives in update-once.sh: only that way
can a single run be tested, without spinning up an endless loop. A failed
run does not terminate the sidecar - it is the only one still able to
report a broken state.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Heartbeat and request validation — the security boundary

**Files:**
- Create: `deploy/updater/update-once.sh`
- Test: `tests/test_updater_script.py`

**Interfaces:**
- Consumes: the environment variables from the Compose file (Task 5), already populated with defaults here.
- Produces: `update-once.sh` — one run. Writes `state.json` with at least `{phase, updater_seen_at}`, processes an `id` exactly once. Task 3 and 4 extend the same file.

- [ ] **Step 1: Write the failing tests**

`tests/test_updater_script.py` (GPL-Kopf, dann):

```python
"""Behavioral tests for the sidecar.

Same approach as `test_install_script.py` and `test_update_script.py`: a
sealed PATH made of fake binaries, and what is checked is WHICH commands
the script chooses.

The tests around `test_ein_ziel_mit_semikolon_*` are the core of this
file. They substantiate the claim from spec section 10 - "even someone
who fully takes over the bridge can at most install a published, newer
version". Without them that would just be an assertion. What matters here
is not only THAT the request is rejected, but that the call log shows NOT
A SINGLE docker call: a rejection that has already done something before
rejecting is not one."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "updater" / "update-once.sh"

SYSTEM_TOOLS = ("sh", "cat", "grep", "sed", "awk", "tr", "printf", "mkdir", "rm", "mv", "date", "sort", "head", "tail", "jq", "sleep", "seq", "ls", "cut")


@pytest.fixture
def updater(tmp_path):
    """Returns `run(**env)` -> (result, calls, state). `calls` is the log
    of every faked tool, `state` the written state as a dict (or None)."""
    bindir, sysdir = tmp_path / "bin", tmp_path / "sys"
    bindir.mkdir()
    sysdir.mkdir()
    log = tmp_path / "stub.log"
    update_dir = tmp_path / "data" / "update"
    update_dir.mkdir(parents=True)
    stack = tmp_path / "repo" / "deploy" / "testhost"
    stack.mkdir(parents=True)
    (stack / ".env").write_text("LOXMATTER_IMAGE_TAG=0.2.0\n", encoding="utf-8")

    def stub(name: str, body: str = "") -> None:
        path = bindir / name
        path.write_text(f'#!/bin/sh\nprintf "%s %s\\n" "{name}" "$*" >> "$STUB_LOG"\n{body}\n', encoding="utf-8")
        path.chmod(0o755)

    stub("docker")
    stub("git")
    stub("curl", 'echo \'{"status":"ok"}\'')
    stub("tar")

    for tool in SYSTEM_TOOLS:
        real = subprocess.run(["which", tool], capture_output=True, text=True).stdout.strip()
        if real:
            (sysdir / tool).symlink_to(real)

    def run(**extra_env):
        env = {
            "PATH": f"{bindir}:{sysdir}",
            "STUB_LOG": str(log),
            "LOXMATTER_UPDATE_DIR": str(update_dir),
            "LOXMATTER_BACKUP_DIR": str(tmp_path / "data" / "backups"),
            "LOXMATTER_STACK": str(stack),
            "LOXMATTER_REPO": str(tmp_path / "repo"),
            "LOXMATTER_HEALTH_TIMEOUT": "3",
            **extra_env,
        }
        result = subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env)
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
        state_file = update_dir / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else None
        return result, calls, state

    run.update_dir = update_dir
    run.stack = stack
    return run


def _auftrag(updater, **fields) -> None:
    body = {"id": "auftrag-1", "channel": "stable", "target": "0.3.0", "requested_at": "2026-09-08T10:00:00Z"}
    body.update(fields)
    (updater.update_dir / "request.json").write_text(json.dumps(body), encoding="utf-8")


def test_ohne_auftrag_schreibt_es_nur_ein_lebenszeichen(updater):
    _, calls, state = updater()
    assert state["phase"] == "idle"
    assert state["updater_seen_at"]
    assert "docker" not in calls


def test_das_lebenszeichen_kommt_bei_jedem_durchlauf(updater):
    _, _, erst = updater()
    _, _, dann = updater()
    assert dann["updater_seen_at"] >= erst["updater_seen_at"]


def test_ein_ziel_mit_semikolon_wird_abgelehnt(updater):
    _auftrag(updater, target="0.3.0; rm -rf /")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_ein_ziel_mit_fremder_registry_wird_abgelehnt(updater):
    # The image name is assembled inside the script, never taken over
    # verbatim. A target that looks like an image is therefore simply
    # not a valid target.
    _auftrag(updater, target="evil.example.com/loxmatter:latest")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_ein_unbekannter_kanal_wird_abgelehnt(updater):
    _auftrag(updater, channel="beliebig")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_eine_aeltere_version_wird_abgelehnt(updater):
    # "Forward only", spec section 10, rule 3. .env is at 0.2.0.
    _auftrag(updater, target="0.1.0")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_dieselbe_version_wird_abgelehnt(updater):
    _auftrag(updater, target="0.2.0")
    _, _, state = updater()
    assert state["phase"] == "rejected"


def test_ein_gueltiges_ziel_wird_angenommen(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, state = updater()
    assert state["phase"] != "rejected"
    assert "docker" in calls


def test_derselbe_auftrag_wird_nicht_zweimal_ausgefuehrt(updater):
    _auftrag(updater, target="0.3.0")
    updater()
    _, zweite_calls, _ = updater()
    assert "compose pull" not in zweite_calls
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_updater_script.py -v`
Expected: FAIL — all of them, `No such file or directory` for `update-once.sh`

- [ ] **Step 3: Write the script's skeleton, heartbeat and validation**

`deploy/updater/update-once.sh` (GPL header in `#` form, then):

```sh
#!/bin/sh
# One run of the sidecar - design "Applying updates through the web UI"
# (2026-09-08), sections 6 through 8 and 10.
#
# Invoked every two seconds by entrypoint.sh. Does three things, in this
# order: refresh the heartbeat, check for a new request, execute it.
#
# WHAT THIS SCRIPT NEVER DOES: put text from request.json into a command
# position. No eval, no "$TARGET" as part of a command, no image name
# taken from the request. The image name is assembled fixed below; from
# the request comes only a string that was checked beforehand against a
# fixed pattern. That is this solution's security boundary - not the
# login in front of it.
set -eu

UPDATE_DIR="${LOXMATTER_UPDATE_DIR:-/data/update}"
BACKUP_DIR="${LOXMATTER_BACKUP_DIR:-/data/backups}"
STACK="${LOXMATTER_STACK:-/repo/deploy/testhost}"
REPO="${LOXMATTER_REPO:-/repo}"
SERVICE="${LOXMATTER_SERVICE:-loxmatter}"
IMAGE="${LOXMATTER_IMAGE:-ghcr.io/lucienkerl/loxmatter}"
HEALTH_URL="${LOXMATTER_HEALTH_URL:-http://host.docker.internal:8080/health}"
HEALTH_TIMEOUT="${LOXMATTER_HEALTH_TIMEOUT:-120}"

REQUEST="$UPDATE_DIR/request.json"
STATE="$UPDATE_DIR/state.json"
LOG="$UPDATE_DIR/log.txt"
FAILURE="$UPDATE_DIR/LETZTER-FEHLSCHLAG.txt"
ENV_FILE="$STACK/.env"

mkdir -p "$UPDATE_DIR" "$BACKUP_DIR"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Atomic, always. The bridge reads this file once a second and must never
# see a half-written one - a truncated JSON would be indistinguishable to
# it from "no sidecar present".
write_state() {
  printf '%s\n' "$1" > "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

log() {
  printf '%s %s\n' "$(now)" "$*" >> "$LOG"
  tail -n 2000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
}

# What is RUNNING, not what would be pulled on the next start. Those are
# two different questions, and stage 1 showed that confusing them is
# costly: since 0.2.0 every fresh installation carries
# LOXMATTER_IMAGE_TAG=stable in the .env (deploy/testhost/.env.example).
# The tag is a MOVING ALIAS - "stable" is not a version, and a comparison
# "is 0.3.0 newer than stable" has no answer. That is exactly where the
# forward-only check from spec section 10, rule 3, would have silently
# slipped past on every standard installation.
#
# The reliable answer comes from the running container itself: its
# LOXMATTER_VERSION was baked in at build time (stage 1, section 4) and
# names exactly the version that is currently at work - regardless of
# which alias it was once pulled under.
running_version() {
  version="$(docker inspect "$SERVICE" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
    | sed -n -E 's/^LOXMATTER_VERSION=(.+)$/\1/p' | head -1)"
  printf '%s' "${version:-unbekannt}"
}

# The tag from the .env - needed only for the rollback now, i.e. to know
# what to write back if the update fails.
current_tag() {
  tag="$(sed -n -E 's/^LOXMATTER_IMAGE_TAG=(.*)$/\1/p' "$ENV_FILE" 2>/dev/null | tail -1)"
  printf '%s' "${tag:-stable}"
}

set_state() {
  # $1 phase, $2 error message (may be empty)
  write_state "$(jq -n \
    --arg id "${JOB_ID:-}" --arg phase "$1" --arg error "${2:-}" \
    --arg from "${FROM:-}" --arg to "${TO:-}" --arg seen "$(now)" \
    --argjson rolled "${ROLLED:-false}" --argjson healthy "${HEALTHY:-true}" \
    '{id: (if $id == "" then null else $id end),
      phase: $phase,
      from: (if $from == "" then null else $from end),
      to:   (if $to == "" then null else $to end),
      error: (if $error == "" then null else $error end),
      rolled_back: $rolled,
      healthy: $healthy,
      updater_seen_at: $seen}')"
}

# ------------------------------------------------------------ heartbeat --
# First, before anything else: the bridge hides the update button when
# this timestamp goes stale (see update.py). A sidecar that only gives a
# heartbeat after finishing its work would look absent during every bit
# of that work.
if [ -f "$STATE" ]; then
  write_state "$(jq --arg seen "$(now)" '.updater_seen_at = $seen' "$STATE")"
else
  JOB_ID="" FROM="" TO="" set_state idle ""
fi

[ -f "$REQUEST" ] || exit 0

JOB_ID="$(jq -r '.id // empty' "$REQUEST" 2>/dev/null || true)"
CHANNEL="$(jq -r '.channel // empty' "$REQUEST" 2>/dev/null || true)"
TARGET="$(jq -r '.target // empty' "$REQUEST" 2>/dev/null || true)"

# Exactly once. Without this the sidecar would work through the same
# request again every two seconds - and an update that restarts itself
# never comes to rest.
LAST="$(jq -r '.id // empty' "$STATE" 2>/dev/null || true)"
[ -n "$JOB_ID" ] || exit 0
[ "$JOB_ID" != "$LAST" ] || exit 0

# Two different things, deliberately kept apart:
#   RUNNING - which version is currently at work (for the forward-only
#             check and for the display)
#   FROM    - what is in the .env and would be written back on rollback
#             (can be an alias like "stable")
RUNNING="$(running_version)"
FROM="$(current_tag)"
TO="$TARGET"
ROLLED=false
HEALTHY=true

reject() {
  log "Request $JOB_ID rejected: $1"
  set_state rejected "$1"
  exit 0
}

# --------------------------------------------------------------- validation --
# Rule 1: channel is an enum, target must satisfy a pattern.
case "$CHANNEL" in
  stable|dev) ;;
  *) reject "unknown channel" ;;
esac

case "$CHANNEL" in
  stable)
    printf '%s' "$TARGET" | grep -Eq '^v?[0-9]+\.[0-9]+\.[0-9]+$' \
      || reject "not a valid version target" ;;
  dev)
    printf '%s' "$TARGET" | grep -Eq '^[0-9a-f]{7,40}$' \
      || reject "not a valid commit target" ;;
esac

# Rule 3: forward only. In the stable channel by semantic version;
# `sort -V` from coreutils, busybox's sort cannot do that reliably. The
# dev channel has no ordering over SHAs - there Task 3 instead checks
# ancestry, once the refs have been fetched.
if [ "$CHANNEL" = "stable" ]; then
  # Compared against the RUNNING version, not against the tag in the
  # .env - see running_version() above. A tag can be named "stable" and
  # thereby not be a version at all.
  CUR="${RUNNING#v}"
  NEW="${TARGET#v}"
  # A version that does not identify itself (a hand-built image,
  # LOXMATTER_VERSION empty) cannot be the starting point of a comparison.
  # Reject instead of guessing: the user then sees that they are running
  # an image that does not state its provenance - a usable piece of
  # information.
  case "$CUR" in
    ''|unbekannt|dev) reject "the running version does not state a version - update only via the console" ;;
  esac
  if [ "$CUR" = "$NEW" ]; then
    reject "this version is already running"
  fi
  if [ "$(printf '%s\n%s\n' "$CUR" "$NEW" | sort -V | head -1)" != "$CUR" ]; then
    reject "older version - only the rollback goes backward"
  fi
fi

set_state queued ""
log "Request $JOB_ID accepted: $FROM -> $TO ($CHANNEL)"
```

- [ ] **Step 4: Run tests to verify the rejection tests pass**

Run: `uv run pytest tests/test_updater_script.py -v`
Expected: all pass except `test_ein_gueltiges_ziel_wird_angenommen` and `test_derselbe_auftrag_wird_nicht_zweimal_ausgefuehrt` (those need Task 3 — until then the script does not yet call `docker`). Leave these two **expected FAIL** and turn them green in Task 3.

- [ ] **Step 5: Prove the security tests can fail**

Comment out the `case "$CHANNEL"` pattern check for `stable` on a trial basis, run `uv run pytest tests/test_updater_script.py -v`.
Expected: `test_ein_ziel_mit_semikolon_wird_abgelehnt` and `test_ein_ziel_mit_fremder_registry_wird_abgelehnt` FAIL. Then revert.

- [ ] **Step 6: shellcheck**

Run: `shellcheck -s sh deploy/updater/update-once.sh`
Expected: no findings

- [ ] **Step 7: Commit**

```bash
git add deploy/updater/update-once.sh tests/test_updater_script.py
git commit -m "feat(updater): heartbeat and request validation

This solution's security boundary sits here, not at the login: the
channel is an enum, the target must satisfy a fixed pattern, and from
there it only ever moves forward. The image name is assembled later in
the script, never taken over from the request - a target that looks like
an image is therefore simply not a valid target.

The tests substantiate not only THAT a request is rejected, but that the
call log shows not a single docker call. A rejection that has already
done something before rejecting is not one.

The heartbeat deliberately comes BEFORE request validation: the bridge
hides the button once it goes stale, and a sidecar that only gives a
heartbeat after finishing its work would look absent during every bit of
that work.

Two tests stay red for now - they need the flow from the next commit.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The flow — back up, pull, restart, become healthy

**Files:**
- Modify: `deploy/updater/update-once.sh` (append)
- Test: `tests/test_updater_script.py` (append)

**Interfaces:**
- Consumes: `JOB_ID`, `FROM`, `TO`, `CHANNEL` from Task 2.
- Produces: the phase sequence `backup` → `pull` → `recreate` → `health` → `done`, and `set_tag <value>`, which sets `LOXMATTER_IMAGE_TAG` in the `.env`. **Task 4 uses `set_tag` for the rollback.**

- [ ] **Step 1: Write the failing tests**

Ans Ende von `tests/test_updater_script.py`:

```python
def test_der_ablauf_haelt_seine_reihenfolge_ein(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, state = updater()
    assert calls.index("tar") < calls.index("compose pull")
    assert calls.index("compose pull") < calls.index("compose up")
    assert state["phase"] == "done"


def test_der_neustart_laesst_die_nachbardienste_in_ruhe(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, _ = updater()
    up = next(line for line in calls.splitlines() if "compose up" in line)
    assert "--no-deps" in up
    assert "loxmatter-updater" not in up


def test_der_image_name_kommt_nicht_aus_dem_auftrag(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, _ = updater()
    assert "ghcr.io/lucienkerl/loxmatter" in calls


def test_der_tag_landet_in_der_env(updater):
    _auftrag(updater, target="0.3.0")
    updater()
    assert "LOXMATTER_IMAGE_TAG=0.3.0" in (updater.stack / ".env").read_text(encoding="utf-8")


def test_die_env_behaelt_ihre_uebrigen_zeilen(updater):
    # The .env carries MINISERVER_IP, RADIO_DEVICE, LOXMATTER_API_TOKEN. An
    # update that overwrites it takes half the installation down with it.
    env = updater.stack / ".env"
    env.write_text("MINISERVER_IP=10.0.1.9\nLOXMATTER_IMAGE_TAG=0.2.0\nRADIO_DEVICE=/dev/ttyUSB0\n", encoding="utf-8")
    _auftrag(updater, target="0.3.0")
    updater()
    text = env.read_text(encoding="utf-8")
    assert "MINISERVER_IP=10.0.1.9" in text
    assert "RADIO_DEVICE=/dev/ttyUSB0" in text
    assert "LOXMATTER_IMAGE_TAG=0.3.0" in text
    assert "0.2.0" not in text


def test_eine_sicherung_entsteht_vor_dem_ziehen(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, _ = updater()
    tar_line = next(line for line in calls.splitlines() if line.startswith("tar"))
    assert "store-" in tar_line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_updater_script.py -v`
Expected: the six new ones FAIL (`ValueError: substring not found` or `phase == 'queued'`)

- [ ] **Step 3: Append the flow to the script**

Append to `deploy/updater/update-once.sh`:

```sh
# ------------------------------------------------------------------- flow --

run() {
  log "\$ $*"
  "$@" >> "$LOG" 2>&1
}

# Replaces EXACTLY that one line and leaves the rest of the .env untouched.
# It carries MINISERVER_IP, RADIO_DEVICE and the API token - an update
# that rewrites the file takes half the installation down with it.
set_tag() {
  if grep -q '^LOXMATTER_IMAGE_TAG=' "$ENV_FILE" 2>/dev/null; then
    sed -i -E "s|^LOXMATTER_IMAGE_TAG=.*|LOXMATTER_IMAGE_TAG=$1|" "$ENV_FILE"
  else
    printf 'LOXMATTER_IMAGE_TAG=%s\n' "$1" >> "$ENV_FILE"
  fi
}

# Waits for the first healthy beat. The 120 seconds are not a new value but
# the one from scripts/update.sh - and the reasoning there still holds
# unchanged: 20 seconds went fine for exactly that long, until a run on
# September 8th tipped just past it and the script reported a service as
# unhealthy that was working flawlessly ten seconds later. A window that is
# too short is the more expensive kind of false alarm here - it looks like
# a broken update and tempts one into rolling back a state that is fine.
wait_healthy() {
  i=0
  while [ "$i" -lt "$HEALTH_TIMEOUT" ]; do
    if curl -fsS -m 3 "$HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  return 1
}

# 1. Back up. Before anything else: the signal database is the one thing a
# failed update could not restore - it holds the signal keys, and those
# are the wiring into the Loxone configuration.
set_state backup ""
STAMP="$(date -u +%Y-%m-%d-%H%M%S)"
if ! run tar czf "$BACKUP_DIR/store-$STAMP.tgz" -C /data loxmatter.sqlite; then
  set_state failed "backup failed - nothing was changed"
  exit 0
fi
# Never sweep away the last ten, as in scripts/update.sh.
ls -1t "$BACKUP_DIR"/store-*.tgz 2>/dev/null | tail -n +11 | while read -r old; do rm -f "$old"; done

# 2. Fetch the target. The Compose file must match the version: a new
# release can need a new service or a new variable.
set_state pull ""
if ! run git -C "$REPO" fetch --tags --force origin; then
  set_state failed "git fetch failed"
  exit 0
fi

if [ "$CHANNEL" = "dev" ]; then
  # The dev channel's equivalent of "forward only" (spec section 10,
  # rule 3): there is no ordering over SHAs, but there is ancestry.
  if ! git -C "$REPO" merge-base --is-ancestor HEAD "$TARGET" 2>/dev/null; then
    reject "not a descendant of the running state"
  fi
  REF="$TARGET"
else
  REF="v${TARGET#v}"
fi

if ! run git -C "$REPO" checkout --detach "$REF"; then
  set_state failed "target $REF not found in the repository"
  exit 0
fi

# The image name is assembled HERE, from a fixed constant and a validated
# target - it never comes from the request (spec section 10, rule 2).
# That is also why $IMAGE below appears only in the log, not as an
# argument: Compose forms the name from the .env line that set_tag wrote.
log "target image: $IMAGE:${TARGET#v}"
set_tag "${TARGET#v}"

if ! run docker compose --project-directory "$STACK" pull "$SERVICE"; then
  set_tag "$FROM"
  set_state failed "image could not be pulled - the running service is unchanged"
  exit 0
fi

# 3. Replace it. --no-deps: matter-server and OTBR stay untouched, and the
# sidecar does not replace itself - that would terminate it in the middle
# of its own request.
set_state recreate ""
if ! run docker compose --project-directory "$STACK" up -d --no-deps --force-recreate "$SERVICE"; then
  set_state failed "restart failed"
  exit 0
fi

# 4. Wait for the first healthy beat.
set_state health ""
if wait_healthy; then
  set_state done ""
  log "Update to $TO complete"
  exit 0
fi

set_state rollback ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_updater_script.py -v`
Expected: all pass (the `curl` fake responds healthy immediately, so the run ends at `done`)

- [ ] **Step 5: Prove the `.env` test can fail**

Replace `set_tag` on a trial basis with `printf 'LOXMATTER_IMAGE_TAG=%s\n' "$1" > "$ENV_FILE"` and run the tests.
Expected: `test_die_env_behaelt_ihre_uebrigen_zeilen` FAILS. Then revert.

- [ ] **Step 6: shellcheck, commit**

```bash
shellcheck -s sh deploy/updater/update-once.sh
git add deploy/updater/update-once.sh tests/test_updater_script.py
git commit -m "feat(updater): back up, pull, restart, become healthy

The order is the one from scripts/update.sh, including the 120 seconds
and their reasoning: 20 went fine for exactly that long, until a run on
September 8th tipped just past it and reported a service as unhealthy
that was working flawlessly ten seconds later.

set_tag replaces exactly one line of the .env instead of rewriting it -
it carries MINISERVER_IP, RADIO_DEVICE and the API token, and an update
that steamrolls the file takes half the installation down with it. A
test pins this down.

The dev channel gets its equivalent of 'forward only' here: there is no
ordering over SHAs, but there is ancestry - merge-base --is-ancestor
against the running state.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The rollback, the plain-text file, and the self-replacement

**Files:**
- Modify: `deploy/updater/update-once.sh` (append)
- Test: `tests/test_updater_script.py` (append)

**Interfaces:**
- Consumes: `set_tag`, `wait_healthy`, `run`, `FROM`, `TO` from Task 3.
- Produces: end phases `done` and `failed` with `rolled_back` and `healthy`; the file `LETZTER-FEHLSCHLAG.txt`.

- [ ] **Step 1: Write the failing tests**

Ans Ende von `tests/test_updater_script.py`:

```python
@pytest.fixture
def kranker_dienst(updater, tmp_path):
    """The same environment, but `curl` never responds healthy - the case
    the rollback exists for."""
    curl = tmp_path / "bin" / "curl"
    curl.write_text('#!/bin/sh\nprintf "curl %s\\n" "$*" >> "$STUB_LOG"\nexit 7\n', encoding="utf-8")
    curl.chmod(0o755)
    return updater


def test_ein_kranker_dienst_wird_zurueckgesetzt(kranker_dienst):
    _auftrag(kranker_dienst, target="0.3.0")
    _, _, state = kranker_dienst()
    assert state["phase"] == "failed"
    assert state["rolled_back"] is True


def test_der_rueckfall_setzt_den_alten_tag_zurueck(kranker_dienst):
    _auftrag(kranker_dienst, target="0.3.0")
    kranker_dienst()
    assert "LOXMATTER_IMAGE_TAG=0.2.0" in (kranker_dienst.stack / ".env").read_text(encoding="utf-8")


def test_der_rueckfall_laeuft_genau_einmal(kranker_dienst):
    # No flapping: two `up` calls (update and rollback), no more.
    _auftrag(kranker_dienst, target="0.3.0")
    _, calls, _ = kranker_dienst()
    assert len([line for line in calls.splitlines() if "compose up" in line]) == 2


def test_der_rueckfall_ruehrt_die_datenbank_nicht_an(kranker_dienst):
    # Spec section 8: the old version runs on the new schema
    # (`_migrate` returns immediately once version >= _SCHEMA_VERSION).
    # Restoring the backup is the more destructive step and stays an
    # explicit action in the web UI.
    _auftrag(kranker_dienst, target="0.3.0")
    _, calls, _ = kranker_dienst()
    # Check specifically for the unpacking, not for an arbitrary "-x": that
    # would otherwise trip on any future call that happens to carry an
    # -x flag, and the test would go red for a reason that has nothing to
    # do with its claim.
    tar_aufrufe = [line for line in calls.splitlines() if line.startswith("tar ")]
    assert tar_aufrufe, "the backup itself must have taken place"
    for line in tar_aufrufe:
        assert " -x" not in line and "xzf" not in line, line


def test_ein_fehlschlag_hinterlaesst_eine_lesbare_datei(kranker_dienst):
    _auftrag(kranker_dienst, target="0.3.0")
    kranker_dienst()
    text = (kranker_dienst.update_dir / "LETZTER-FEHLSCHLAG.txt").read_text(encoding="utf-8")
    assert "0.2.0" in text
    assert "0.3.0" in text
    assert "scripts/update.sh" in text


def test_ein_gelungenes_update_hinterlaesst_keine_fehlschlagdatei(updater):
    _auftrag(updater, target="0.3.0")
    updater()
    assert not (updater.update_dir / "LETZTER-FEHLSCHLAG.txt").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_updater_script.py -v`
Expected: the six new ones FAIL — the run currently ends at `rollback` and does nothing there

- [ ] **Step 3: Append the rollback**

Append to `deploy/updater/update-once.sh` (the line `set_state rollback ""` from Task 3 stays in place and is thereby continued):

```sh
# --------------------------------------------------------------- rollback --
# Triggered because /health did not respond within HEALTH_TIMEOUT.
#
# What deliberately does NOT happen here: the database is not restored.
# `_migrate` in model/store.py returns immediately once
# `version >= _SCHEMA_VERSION` - so the old version starts up on the new
# schema, and since every migration so far is an ALTER TABLE ADD COLUMN
# (in SQLite necessarily nullable or with a default), it goes on writing
# valid rows. The image rollback alone is enough to bring the house back
# up.
#
# Restoring the backup would be the more destructive step: it discards
# everything since the backup was taken. That is not something to do
# automatically at two in the morning when nobody is watching - it sits
# in the web UI as its own button that must be confirmed explicitly.
# WHERE the rollback lands is not the same as WHERE the update came from.
# If the .env held a moving alias ("stable", the normal case on every
# fresh installation since 0.2.0), that alias in the registry now points
# AT THE FAILED VERSION. Writing it back would mean fetching exactly the
# state that just failed to become healthy on the next `compose pull` -
# and nobody would have any record that a rollback ever happened.
#
# What gets written back is therefore the concrete version that was
# RUNNING before. Only when that could not be determined does the old
# entry remain the only information available.
case "$RUNNING" in
  ''|unbekannt|dev) BACK="$FROM" ;;
  *)                BACK="${RUNNING#v}" ;;
esac
log "Update to $TO not healthy after ${HEALTH_TIMEOUT}s - rolling back to $BACK"
ROLLED=true
set_state rollback ""
set_tag "$BACK"
run git -C "$REPO" checkout --detach "$GIT_BEFORE" || true
run docker compose --project-directory "$STACK" up -d --no-deps --force-recreate "$SERVICE" || true

# Exactly once. No second attempt, no flapping: if the cause were not the
# image (but, say, a dead matter-server), every further run would only add
# more downtime.
if wait_healthy; then
  HEALTHY=true
else
  HEALTHY=false
fi

set_state failed "version $TO did not become healthy after ${HEALTH_TIMEOUT}s"

# If even the rollback did not become healthy, the web UI is probably not
# reachable at all - then this file is the only answer someone finds who
# checks in over SSH after all. It is also written on a successful
# rollback: anyone who wants to know why their version is the old one
# again should be able to read up on it.
{
  printf 'loxmatter - last failed update attempt\n\n'
  printf 'Time:           %s\n' "$(now)"
  printf 'Attempted:      %s -> %s (channel %s)\n' "$FROM" "$TO" "$CHANNEL"
  printf 'Rolled back:    yes, to %s\n' "$FROM"
  printf 'Healthy again:  %s\n\n' "$([ "$HEALTHY" = true ] && echo yes || echo NO)"
  printf 'Signal database backup:\n  %s\n\n' "$BACKUP_DIR/store-$STAMP.tgz"
  printf 'Manual next steps:\n'
  printf '  cd %s && docker compose logs --tail 100 %s\n' "$STACK" "$SERVICE"
  printf '  cd %s && ./scripts/update.sh --no-pull\n' "$REPO"
  printf '  # Restore the backup (discards everything since):\n'
  printf '  #   docker compose stop %s\n' "$SERVICE"
  printf '  #   tar xzf %s -C /var/lib/docker/volumes/.../\n\n' "$BACKUP_DIR/store-$STAMP.tgz"
  printf 'Last lines of the log:\n'
  tail -n 40 "$LOG" 2>/dev/null || true
} > "$FAILURE"
```

And in Task 3, right after `FROM="$(current_tag)"` from Task 2, add (so that `GIT_BEFORE` exists):

```sh
GIT_BEFORE="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || echo HEAD)"
```

Also, in the success branch from Task 3, before `set_state done ""`:

```sh
  rm -f "$FAILURE"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_updater_script.py -v`
Expected: all pass

- [ ] **Step 5: Prove the "exactly once" test can fail**

Wrap the rollback block on a trial basis with `for _ in 1 2; do … done` and run the tests.
Expected: `test_der_rueckfall_laeuft_genau_einmal` FAILS (`3 != 2`). Then revert.

- [ ] **Step 6: Append the self-replacement**

Right at the end of the success branch, **after** `set_state done ""`:

```sh
# Last, and only after a successful update: the sidecar checks whether
# its own image is out of date, and kicks off its own replacement
# detached. AFTER writing `done`, never before - otherwise it would
# terminate itself in the middle of writing the state the web UI is
# currently reading, and a successful update would look like a stuck one.
#
# Detached via `-d`: the call that replaces this container must not wait
# inside this container for its own end.
if [ "${LOXMATTER_UPDATER_SELF_REPLACE:-1}" = "1" ]; then
  run docker compose --project-directory "$STACK" up -d --no-deps loxmatter-updater || true
fi
```

Add the line `"LOXMATTER_UPDATER_SELF_REPLACE": "0",` to the `env` dict in the test fixture from Task 2 — otherwise `test_der_neustart_laesst_die_nachbardienste_in_ruhe` would count an `up` call meant for the sidecar itself. Plus a dedicated test:

```python
def test_der_beiwagen_tauscht_sich_erst_nach_dem_erfolg_aus(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, _ = updater(LOXMATTER_UPDATER_SELF_REPLACE="1")
    zeilen = calls.splitlines()
    eigen = next(i for i, line in enumerate(zeilen) if "loxmatter-updater" in line)
    fremd = next(i for i, line in enumerate(zeilen) if "compose up" in line and "loxmatter-updater" not in line)
    assert fremd < eigen


def test_nach_einem_fehlschlag_tauscht_er_sich_nicht_aus(kranker_dienst):
    _auftrag(kranker_dienst, target="0.3.0")
    _, calls, _ = kranker_dienst(LOXMATTER_UPDATER_SELF_REPLACE="1")
    assert "loxmatter-updater" not in calls
```

- [ ] **Step 7: Run all updater tests, shellcheck, commit**

Run: `uv run pytest tests/test_updater_script.py -v && shellcheck -s sh deploy/updater/update-once.sh`
Expected: all green

```bash
git add deploy/updater/update-once.sh tests/test_updater_script.py
git commit -m "feat(updater): rollback, plain-text file, self-replacement

The rollback swaps the image back and leaves the database alone. That
rests on a verified finding, not an assumption: _migrate in
model/store.py returns immediately once version >= _SCHEMA_VERSION, so
the old version runs on the new schema. Restoring the backup is the more
destructive step - it discards everything since the backup was taken -
and stays an explicit action in the web UI.

Exactly once, no flapping: if the cause were not the image but, say, a
dead matter-server, every further run would only add more downtime.

LETZTER-FEHLSCHLAG.txt is written even on a successful rollback. If the
web UI does not come up, it is the only answer someone finds who checks
in over SSH after all - and anyone who just wants to know why their
version is the old one again should be able to read up on it.

The self-replacement sits AFTER writing done: before that it would
terminate itself in the middle of writing the state the web UI is
currently reading, and a successful update would look like a stuck one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The sidecar in the stack

**Files:**
- Modify: `deploy/testhost/docker-compose.yml`
- Test: `tests/test_compose_profiles.py` (append)

**Interfaces:**
- Consumes: the image from Task 1, the environment variable names from Task 2.
- Produces: the running service `loxmatter-updater` with access to `loxmatter-store`, the checkout, and the Docker socket.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_compose_profiles.py`:

```python
def test_der_updater_hat_kein_netz_nach_aussen_offen() -> None:
    # The load-bearing safeguard for this container: it holds the Docker
    # socket and is thereby root-equivalent on the host. Unlike the three
    # other services, it therefore does NOT sit on the host network and
    # publishes no port.
    updater = _stack()["services"]["loxmatter-updater"]
    assert "ports" not in updater
    assert updater.get("network_mode") != "host"


def test_nur_der_updater_hat_den_docker_socket() -> None:
    for name, service in _stack()["services"].items():
        socket = any("docker.sock" in str(v) for v in service.get("volumes", []))
        assert socket == (name == "loxmatter-updater"), name


def test_der_updater_sieht_dieselbe_datenbank_wie_die_bruecke() -> None:
    # Communication runs through files in exactly this volume.
    updater = _stack()["services"]["loxmatter-updater"]
    assert any(str(v).startswith("loxmatter-store:") for v in updater["volumes"])


def test_der_updater_erreicht_die_gesundheitsroute_des_hosts() -> None:
    # It sits on Compose's default network; 127.0.0.1 there would be itself.
    updater = _stack()["services"]["loxmatter-updater"]
    assert any("host-gateway" in str(h) for h in updater["extra_hosts"])


def test_der_updater_ist_gepinnt() -> None:
    image = _stack()["services"]["loxmatter-updater"]["image"]
    assert "@sha256:" in image or ":latest" not in image
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compose_profiles.py -v`
Expected: FAIL — `KeyError: 'loxmatter-updater'`

- [ ] **Step 3: Add the service**

In `deploy/testhost/docker-compose.yml`, after the service `loxmatter`:

```yaml
  # The updater sidecar (design "Applying updates through the web UI",
  # 2026-09-08, section 6). It exists because the bridge cannot replace
  # itself: the process that would invoke `up -d --force-recreate` would
  # be exactly the one that call terminates.
  #
  # THIS CONTAINER IS ROOT-EQUIVALENT ON THE HOST. The Docker socket
  # grants that, and nothing softens it. It is secured through narrowness,
  # not through privileges:
  #
  #   - No `network_mode: host` and no `ports:`. It sits on Compose's
  #     default network - reachable outward (git fetch), not from the
  #     LAN. That is exactly what distinguishes it from the three other
  #     services here and the reason it is allowed to hold the socket.
  #   - No network service: communication with the bridge runs
  #     exclusively through files in loxmatter-store (/data/update/).
  #   - A single fixed work program. It never executes text from the
  #     request (see update-once.sh).
  #
  # Anyone who does not want this can strike this service. The bridge
  # notices via the stale heartbeat, hides the button, and names the
  # console route - no dead button, no message that sounds like a defect.
  loxmatter-updater:
    image: ghcr.io/lucienkerl/loxmatter-updater:stable
    container_name: loxmatter-updater
    restart: unless-stopped
    # So that `curl http://host.docker.internal:8080/health` reaches the
    # service on the host: from inside this container, 127.0.0.1 would be
    # itself, not the bridge.
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      # Root-equivalent - see above.
      - /var/run/docker.sock:/var/run/docker.sock
      # The checkout: the Compose file must match the target version, a
      # new release can need a new service or a new variable. Writable,
      # because `git checkout` writes into it.
      - ../..:/repo
      # The same volume as the bridge: this is where the request, state,
      # log, and backups live.
      - loxmatter-store:/data
    environment:
      LOXMATTER_UPDATE_DIR: /data/update
      LOXMATTER_BACKUP_DIR: /data/backups
      LOXMATTER_STACK: /repo/deploy/testhost
      LOXMATTER_REPO: /repo
      LOXMATTER_SERVICE: loxmatter
      LOXMATTER_IMAGE: ghcr.io/lucienkerl/loxmatter
      LOXMATTER_HEALTH_URL: http://host.docker.internal:8080/health
      LOXMATTER_HEALTH_TIMEOUT: "120"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_compose_profiles.py -v`
Expected: 11 passed

- [ ] **Step 5: Prove the socket test can fail**

Mount `/var/run/docker.sock:/var/run/docker.sock` on a trial basis on the `loxmatter` service too, and run the tests.
Expected: `test_nur_der_updater_hat_den_docker_socket` FAILS. Then revert.

- [ ] **Step 6: Bring it up on the test host and watch it breathe**

```bash
cd deploy/testhost && docker compose up -d loxmatter-updater
sleep 5
docker exec loxmatter cat /data/update/state.json
```
Expected: `{"id":null,"phase":"idle",...,"updater_seen_at":"2026-..."}` — and the timestamp keeps moving forward on every further call.

- [ ] **Step 7: Commit**

```bash
git add deploy/testhost/docker-compose.yml tests/test_compose_profiles.py
git commit -m "feat(compose): add the updater sidecar to the stack

It holds the Docker socket and is thereby root-equivalent on the host -
that is stated in full in the comment, and nothing softens it. It is
secured through narrowness: no host network, no ports, no network
service, a fixed work program. That is exactly what distinguishes it
from the three other services here, all of which sit on the host
network.

Tests pin down the three properties this rests on - and the third checks
not only that the sidecar has the socket, but that NOBODY else does.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: `loxmatter.update` — write the request, read the state

**Files:**
- Create: `src/loxmatter/update.py`
- Test: `tests/test_update_module.py`

**Interfaces:**
- Consumes: the file formats from Task 2–4.
- Produces:
  - `UpdateState` (frozen dataclass: `phase: str`, `id: str | None`, `from_version: str | None`, `to_version: str | None`, `error: str | None`, `rolled_back: bool`, `healthy: bool`, `updater_seen_at: str | None`)
  - `read_state(update_dir: Path) -> UpdateState | None`
  - `read_log(update_dir: Path, lines: int = 40) -> list[str]`
  - `updater_present(state: UpdateState | None, *, now: datetime, max_age_seconds: int = 30) -> bool`
  - `request_update(update_dir: Path, *, channel: str, target: str) -> str` (returns the new `id`)
  - `UpdateBusyError`

- [ ] **Step 1: Write the failing tests**

`tests/test_update_module.py` (GPL header, then):

```python
"""Tests for the file side of the update - design "Applying updates
through the web UI" (2026-09-08), section 7.

This module touches only files, never the network and never HTTP. So
everything here runs against a tmp_path directory, without fakes."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from loxmatter.update import (
    UpdateBusyError,
    read_log,
    read_state,
    request_update,
    updater_present,
)

JETZT = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)


def _state(tmp_path, **fields):
    body = {"id": "a", "phase": "idle", "updater_seen_at": JETZT.strftime("%Y-%m-%dT%H:%M:%SZ")}
    body.update(fields)
    (tmp_path / "state.json").write_text(json.dumps(body), encoding="utf-8")


def test_ohne_zustandsdatei_gibt_es_keinen_zustand(tmp_path):
    assert read_state(tmp_path) is None


def test_eine_halb_geschriebene_datei_gilt_als_kein_zustand(tmp_path):
    # The sidecar writes atomically (temp + rename), but a truncated JSON
    # must still not trigger a 500 here: the web UI polls this route once
    # a second, and a single failure would look there like a broken
    # update.
    (tmp_path / "state.json").write_text('{"phase": "pu', encoding="utf-8")
    assert read_state(tmp_path) is None


def test_der_zustand_wird_vollstaendig_gelesen(tmp_path):
    _state(tmp_path, phase="failed", from_version="0.2.0", to_version="0.3.0",
           error="nicht gesund", rolled_back=True, healthy=True)
    state = read_state(tmp_path)
    assert state.phase == "failed"
    assert state.from_version == "0.2.0"
    assert state.to_version == "0.3.0"
    assert state.error == "nicht gesund"
    assert state.rolled_back is True


def test_ein_frisches_lebenszeichen_heisst_beiwagen_da(tmp_path):
    _state(tmp_path)
    assert updater_present(read_state(tmp_path), now=JETZT + timedelta(seconds=5)) is True


def test_ein_altes_lebenszeichen_heisst_kein_beiwagen(tmp_path):
    # It reports in every two seconds. Half a minute of silence means
    # nobody would pick up the request - the web UI must then not show a
    # button that does nothing.
    _state(tmp_path)
    assert updater_present(read_state(tmp_path), now=JETZT + timedelta(seconds=90)) is False


def test_ohne_zustand_gibt_es_keinen_beiwagen(tmp_path):
    assert updater_present(None, now=JETZT) is False


def test_ein_auftrag_wird_geschrieben_und_bekommt_eine_id(tmp_path):
    _state(tmp_path)
    job = request_update(tmp_path, channel="stable", target="0.3.0")
    body = json.loads((tmp_path / "request.json").read_text(encoding="utf-8"))
    assert body["id"] == job
    assert body["channel"] == "stable"
    assert body["target"] == "0.3.0"
    assert body["requested_at"]


def test_zwei_auftraege_bekommen_verschiedene_ids(tmp_path):
    _state(tmp_path)
    erste = request_update(tmp_path, channel="stable", target="0.3.0")
    _state(tmp_path, id=erste, phase="done")
    zweite = request_update(tmp_path, channel="stable", target="0.4.0")
    assert erste != zweite


def test_waehrend_eines_laufenden_updates_wird_kein_zweiter_angenommen(tmp_path):
    _state(tmp_path, id="laeuft", phase="pull")
    with pytest.raises(UpdateBusyError):
        request_update(tmp_path, channel="stable", target="0.3.0")


def test_nach_einem_abgeschlossenen_update_geht_ein_neuer(tmp_path):
    _state(tmp_path, id="alt", phase="done")
    assert request_update(tmp_path, channel="stable", target="0.4.0")


def test_der_auftrag_wird_atomar_geschrieben(tmp_path, monkeypatch):
    """The sidecar reads every two seconds. If it saw the file half
    written, it would reject a valid request as invalid - and because it
    touches each id only once, that request would never come again."""
    gesehen = []
    echtes_replace = __import__("os").replace

    def spion(src, dst):
        gesehen.append((str(src), str(dst)))
        echtes_replace(src, dst)

    monkeypatch.setattr("loxmatter.update.os.replace", spion)
    _state(tmp_path)
    request_update(tmp_path, channel="stable", target="0.3.0")
    assert gesehen, "request.json must land in place via os.replace"


def test_das_protokoll_liefert_die_letzten_zeilen(tmp_path):
    (tmp_path / "log.txt").write_text("\n".join(f"zeile {i}" for i in range(100)), encoding="utf-8")
    assert read_log(tmp_path, lines=5) == [f"zeile {i}" for i in range(95, 100)]


def test_ohne_protokoll_ist_die_liste_leer(tmp_path):
    assert read_log(tmp_path) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_update_module.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.update'`

- [ ] **Step 3: Write the module**

`src/loxmatter/update.py` (GPL header, then):

```python
"""The file side of the update - design "Applying updates through the
web UI" (2026-09-08), section 7.

The bridge and the sidecar communicate exclusively through files in a
shared volume - no network, no socket, no shared library. The reason is
not frugality: the sidecar survives the bridge's restart, and because the
state lives in a file instead of in memory, the web UI can simply keep
reading after the restart. That is exactly the gap one was blind to
before this design.

This module knows no HTTP concepts and makes no network calls -
`update_check.py` handles the network, `api/update.py` the HTTP. That way
each can be tested on its own."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Phases in which a request is still running. Everything else is an end
# state (or `idle`) and allows a new request.
_LAUFENDE_PHASEN = frozenset({"queued", "backup", "pull", "recreate", "health", "rollback"})

# The sidecar reports in every two seconds (entrypoint.sh). Thirty seconds
# of silence is generous enough for a loaded Pi and short enough that
# nobody sees a button for long that no one would pick up.
_MAX_STILLE_SEKUNDEN = 30


class UpdateBusyError(RuntimeError):
    """A request is already running."""


@dataclass(frozen=True)
class UpdateState:
    phase: str
    id: str | None
    from_version: str | None
    to_version: str | None
    error: str | None
    rolled_back: bool
    healthy: bool
    updater_seen_at: str | None


def read_state(update_dir: Path) -> UpdateState | None:
    """The most recently written state, or `None`.

    Unreadable content is deliberately treated as missing rather than
    raising an exception: the web UI polls this once a second, and a
    single failure would look there like a broken update. The sidecar
    writes atomically (temp + rename), so half-written content is the
    exception anyway - but one that does not deserve an alarm.
    """
    try:
        raw = json.loads((update_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return UpdateState(
        phase=str(raw.get("phase") or "idle"),
        id=raw.get("id"),
        from_version=raw.get("from"),
        to_version=raw.get("to"),
        error=raw.get("error"),
        rolled_back=bool(raw.get("rolled_back")),
        healthy=bool(raw.get("healthy", True)),
        updater_seen_at=raw.get("updater_seen_at"),
    )


def read_log(update_dir: Path, lines: int = 40) -> list[str]:
    try:
        text = (update_dir / "log.txt").read_text(encoding="utf-8")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def updater_present(
    state: UpdateState | None,
    *,
    now: datetime,
    max_age_seconds: int = _MAX_STILLE_SEKUNDEN,
) -> bool:
    """Whether there is anyone at all who would pick up a request.

    Without this judgment, the web UI would show an existing installation
    - and anyone who has struck the sidecar from the Compose file - a
    button that does nothing and reports nothing either.
    """
    if state is None or not state.updater_seen_at:
        return False
    try:
        seen = datetime.strptime(state.updater_seen_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (now - seen).total_seconds() <= max_age_seconds


def request_update(update_dir: Path, *, channel: str, target: str) -> str:
    """Deposits a request and returns its `id`.

    Atomic via `os.replace`: the sidecar reads every two seconds, and if
    it saw the file half written, it would reject a valid request as
    invalid - permanently, because it touches each `id` only once.

    What is NOT checked here is whether `target` is valid. The sidecar
    does that, and deliberately there: it is the security boundary (spec
    section 10), and a boundary that relies on the caller having already
    checked is not one. This function is only the mailbox.
    """
    state = read_state(update_dir)
    if state is not None and state.phase in _LAUFENDE_PHASEN:
        raise UpdateBusyError(state.phase)

    job_id = str(uuid.uuid4())
    body = {
        "id": job_id,
        "channel": channel,
        "target": target,
        "requested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    update_dir.mkdir(parents=True, exist_ok=True)
    temp = update_dir / "request.json.tmp"
    temp.write_text(json.dumps(body), encoding="utf-8")
    os.replace(temp, update_dir / "request.json")
    return job_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_update_module.py -v`
Expected: 13 passed

- [ ] **Step 5: Prove the atomicity test can fail**

Replace `os.replace(temp, ...)` on a trial basis with `(update_dir / "request.json").write_text(json.dumps(body), encoding="utf-8")` and run the tests.
Expected: `test_der_auftrag_wird_atomar_geschrieben` FAILS. Then revert.

- [ ] **Step 6: Lint, types, full suite, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/loxmatter/update.py tests/test_update_module.py
git commit -m "feat(update): write the request, read the state, detect the sidecar

Communication runs through files, not through a network. That is not
frugality: the sidecar survives the bridge's restart, and because the
state lives in a file instead of in memory, the web UI can keep reading
after the restart - exactly the gap one was blind to before.

An unreadable state is treated as a missing one instead of raising an
exception: the web UI polls once a second, and a single failure would
look there like a broken update.

request_update deliberately does NOT check the target. The sidecar does
that - it is the security boundary, and a boundary that relies on the
caller having already checked is not one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: `loxmatter.update_check` — what is new

**Files:**
- Create: `src/loxmatter/update_check.py`
- Modify: `src/loxmatter/model/store.py` (two settings, see Step 3)
- Test: `tests/test_update_check.py`

**Interfaces:**
- Consumes: nothing from Task 6.
- Produces:
  - `Available` (frozen dataclass: `channel: str`, `target: str | None`, `title: str | None`, `notes: str | None`, `behind: int | None`, `checked_at: str`, `error: str | None`)
  - `async check(channel: str, *, current_version: str, current_commit: str | None, fetch: Fetch) -> Available`
  - `Fetch = Callable[[str], Awaitable[dict | list]]` — the network layer is passed in, so the test gets by without a network.
  - Store: `store.update_settings.get_channel() -> str`, `.set_channel(str)`, `.get_check_enabled() -> bool`, `.set_check_enabled(bool)`

- [ ] **Step 1: Write the failing tests**

`tests/test_update_check.py` (GPL header, then):

```python
"""Tests for the GitHub query - design "Applying updates through the web
UI" (2026-09-08), section 9.

The network layer is passed in as `fetch`: that way every test runs
without a network, and the "no internet" case is a test case instead of
a random occurrence in CI. A device without internet access is explicitly
NOT an error here - the last test pins that down."""

from __future__ import annotations

import pytest

from loxmatter.update_check import check


async def test_der_stabile_kanal_meldet_ein_neueres_release():
    async def fetch(url):
        assert "releases/latest" in url
        return {"tag_name": "v0.3.0", "name": "0.3.0", "body": "Fixes the restart hang."}

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target == "0.3.0"
    assert result.notes == "Fixes the restart hang."
    assert result.error is None


async def test_auf_dem_neuesten_stand_gibt_es_kein_ziel():
    async def fetch(url):
        return {"tag_name": "v0.2.0", "name": "0.2.0", "body": ""}

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target is None


async def test_ein_aelteres_release_gilt_nicht_als_update():
    # Anyone running a development state newer than the latest release
    # should not be invited to downgrade - "forward only" does catch that
    # in the sidecar, but a button that gets reliably rejected is a
    # broken button.
    async def fetch(url):
        return {"tag_name": "v0.1.0", "name": "0.1.0", "body": ""}

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target is None


async def test_der_entwicklungskanal_zaehlt_die_commits():
    async def fetch(url):
        assert "compare" in url
        return {"ahead_by": 14, "commits": [{"commit": {"message": "fix: one\n\nmore"}}, {"commit": {"message": "feat: two"}}]}

    result = await check("dev", current_version="dev", current_commit="a3f91c2", fetch=fetch)
    assert result.behind == 14
    assert "fix: one" in result.notes
    assert "more" not in result.notes, "only the subject line, not the full body"


async def test_der_entwicklungskanal_ohne_bekannten_commit_meldet_nichts():
    async def fetch(url):
        raise AssertionError("must not be queried without a commit")

    result = await check("dev", current_version="dev", current_commit=None, fetch=fetch)
    assert result.target is None
    assert result.error


async def test_ohne_internet_ist_das_kein_fehlerzustand():
    async def fetch(url):
        raise OSError("Name or service not known")

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target is None
    assert result.error
    assert result.checked_at


async def test_ein_unbekannter_kanal_wird_abgewiesen():
    async def fetch(url):
        raise AssertionError("must not be queried")

    with pytest.raises(ValueError):
        await check("beliebig", current_version="0.2.0", current_commit=None, fetch=fetch)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_update_check.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Add the two settings to the store**

In `src/loxmatter/model/store.py`, alongside the other setting accessors (following the pattern of `store.resend_settings`, which already lives in the same `setting` table from schema 5):

```python
class UpdateSettings:
    """Channel and check toggle - design "Applying updates through the
    web UI" (2026-09-08), section 9.

    No new table and thereby no new schema version: both values live in
    `setting`, which has existed since schema 5. A migration here would
    come at a steep price - a schema bump is the one case where a
    rollback to the previous version is not without consequences, and of
    all things the update feature should not trigger one without need.
    """

    _CHANNEL = "update.channel"
    _CHECK = "update.check_enabled"

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_channel(self) -> str:
        row = self._db.execute("SELECT value FROM setting WHERE key = ?", (self._CHANNEL,)).fetchone()
        return row[0] if row else "stable"

    def set_channel(self, channel: str) -> None:
        if channel not in ("stable", "dev"):
            raise ValueError(channel)
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self._CHANNEL, channel),
        )
        self._db.commit()

    def get_check_enabled(self) -> bool:
        row = self._db.execute("SELECT value FROM setting WHERE key = ?", (self._CHECK,)).fetchone()
        # Default on: anyone running a bridge in their home should learn
        # that they are running a version with known bugs. It stays
        # switchable off regardless - it is a connection out to the
        # outside world.
        return row[0] != "0" if row else True

    def set_check_enabled(self, enabled: bool) -> None:
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self._CHECK, "1" if enabled else "0"),
        )
        self._db.commit()
```

And in `Store.__init__`, alongside the other accessors: `self.update_settings = UpdateSettings(self._db)`

- [ ] **Step 4: Write the check module**

`src/loxmatter/update_check.py` (GPL header, then):

```python
"""What is new - design "Applying updates through the web UI"
(2026-09-08), section 9.

The network layer comes in as `fetch`, instead of being hardwired here.
That is not an end in itself: it means every test runs without a
network, and the "no internet" case becomes a test case instead of a
random occurrence in CI.

A device without internet access is explicitly NOT an error here. This
bridge sits in a home, not a data center; anyone running it with no path
outward has chosen that. That is why `Available` carries an `error`
field instead of raising an exception - the web UI then shows a calm
notice, not a red banner."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

Fetch = Callable[[str], Awaitable[Any]]

_RELEASE_URL = "https://api.github.com/repos/lucienkerl/loxmatter/releases/latest"
_COMPARE_URL = "https://api.github.com/repos/lucienkerl/loxmatter/compare/{base}...main"


@dataclass(frozen=True)
class Available:
    channel: str
    target: str | None
    title: str | None
    notes: str | None
    behind: int | None
    checked_at: str
    error: str | None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_tuple(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in version.lstrip("v").split("."))
    except ValueError:
        return None


async def check(
    channel: str,
    *,
    current_version: str,
    current_commit: str | None,
    fetch: Fetch,
) -> Available:
    if channel not in ("stable", "dev"):
        raise ValueError(channel)

    def leer(error: str | None = None) -> Available:
        return Available(channel, None, None, None, None, _now(), error)

    if channel == "dev" and not current_commit:
        # An image without LOXMATTER_COMMIT is a hand-built one. The
        # comparison would have no starting point, and there is no
        # guessing here.
        return leer("This version does not state a commit - the development channel cannot compare.")

    try:
        if channel == "stable":
            body = await fetch(_RELEASE_URL)
            tag = str(body.get("tag_name", "")).lstrip("v")
            neu, alt = _as_tuple(tag), _as_tuple(current_version)
            if not tag or neu is None:
                return leer("GitHub's response does not state a version.")
            # Anyone running a development state newer than the latest
            # release deliberately gets no target here: "forward only"
            # does catch that in the sidecar, but a button that gets
            # reliably rejected is a broken button.
            if alt is not None and neu <= alt:
                return leer()
            return Available(
                channel, tag, str(body.get("name") or tag), str(body.get("body") or ""), None, _now(), None
            )

        body = await fetch(_COMPARE_URL.format(base=current_commit))
        ahead = int(body.get("ahead_by", 0))
        if ahead <= 0:
            return leer()
        # Subject lines only: the body of a commit message in this project
        # is often half an essay, and the card should give an overview,
        # not a read.
        betreffe = [str(c["commit"]["message"]).splitlines()[0] for c in body.get("commits", [])]
        return Available(channel, "main", None, "\n".join(betreffe), ahead, _now(), None)
    except (OSError, KeyError, ValueError, TypeError) as exc:
        return leer(str(exc))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_update_check.py -v`
Expected: 7 passed

- [ ] **Step 6: Prove the downgrade test can fail**

Entferne probeweise die Bedingung `if alt is not None and neu <= alt:` und führe die Tests aus.
Expected: `test_ein_aelteres_release_gilt_nicht_als_update` und `test_auf_dem_neuesten_stand_gibt_es_kein_ziel` FAILEN. Danach zurücknehmen.

- [ ] **Step 7: Lint, types, full suite, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/loxmatter/update_check.py src/loxmatter/model/store.py tests/test_update_check.py
git commit -m "feat(update): Abfrage bei GitHub, Kanal und Pruefschalter

Die Netzschicht kommt als fetch herein statt fest verdrahtet zu sein: so
laeuft jeder Test ohne Netz, und 'kein Internet' wird ein Testfall statt
eines Zufallsereignisses in der CI.

Ein Geraet ohne Internetzugang ist ausdruecklich KEIN Fehler - diese
Bruecke steht in einem Haus, nicht in einem Rechenzentrum. Available
traegt deshalb ein error-Feld, statt zu werfen; die Oberflaeche zeigt
daraufhin einen ruhigen Hinweis.

Ein aelteres Release gilt nicht als Update. 'Nur vorwaerts' faengt das im
Beiwagen zwar ab, aber ein Knopf, der zuverlaessig abgelehnt wird, ist ein
kaputter Knopf.

Kanal und Pruefschalter liegen in der setting-Tabelle aus Schema 5, ohne
neue Schema-Version: ein Schemasprung ist der einzige Fall, in dem ein
Rueckfall nicht folgenlos ist, und ausgerechnet die Update-Funktion sollte
ihn nicht ohne Not ausloesen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Die drei Routen

**Files:**
- Create: `src/loxmatter/api/update.py`
- Modify: `src/loxmatter/loxone/server.py`
- Test: `tests/api/test_update_api.py`

**Interfaces:**
- Consumes: `loxmatter.update` (Task 6), `loxmatter.update_check` (Task 7), `loxmatter.version.build_info` (Stufe 1).
- Produces:
  - `GET /api/update/status` → `{state: {...} | null, updater_present: bool, log: [str], channel: str, check_enabled: bool}`
  - `GET /api/update/check` → `{channel, target, title, notes, behind, checked_at, error}`
  - `POST /api/update/apply` `{target}` → `{id}`; 409 wenn schon einer läuft, 503 wenn kein Beiwagen da ist
  - `PATCH /api/update/settings` `{channel?, check_enabled?}` → wie `status`
  - `build_update_router(store, update_dir) -> APIRouter`

- [ ] **Step 1: Write the failing tests**

`tests/api/test_update_api.py` (GPL-Kopf, dann):

```python
"""Tests fuer /api/update/*.

Die Routen kleben `loxmatter.update` und `loxmatter.update_check` an HTTP
und tun sonst nichts - entsprechend pruefen diese Tests genau die Klebe-
stellen: welcher Statuscode aus welchem Modulzustand folgt."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


def _lebenszeichen(update_dir, phase="idle", **fields):
    body = {
        "id": None,
        "phase": phase,
        "updater_seen_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    body.update(fields)
    (update_dir / "state.json").write_text(json.dumps(body), encoding="utf-8")


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[tuple[httpx.AsyncClient, object]]:
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), update_dir=update_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, update_dir
    store.close()


async def test_ohne_beiwagen_meldet_der_status_das_ehrlich(api):
    client, _ = api
    body = (await client.get("/api/update/status")).json()
    assert body["updater_present"] is False
    assert body["state"] is None


async def test_mit_beiwagen_meldet_der_status_ihn(api):
    client, update_dir = api
    _lebenszeichen(update_dir)
    body = (await client.get("/api/update/status")).json()
    assert body["updater_present"] is True
    assert body["state"]["phase"] == "idle"


async def test_ohne_beiwagen_wird_kein_auftrag_angenommen(api):
    """503 und nicht 200: sonst schriebe die Bruecke einen Auftrag in ein
    Volume, das niemand liest, und die Oberflaeche zeigte einen Fortschritt,
    der nie beginnt."""
    client, _ = api
    response = await client.post("/api/update/apply", json={"target": "0.3.0"})
    assert response.status_code == 503


async def test_mit_beiwagen_wird_ein_auftrag_angenommen(api):
    client, update_dir = api
    _lebenszeichen(update_dir)
    response = await client.post("/api/update/apply", json={"target": "0.3.0"})
    assert response.status_code == 200
    assert response.json()["id"]
    assert json.loads((update_dir / "request.json").read_text(encoding="utf-8"))["target"] == "0.3.0"


async def test_der_kanal_aus_der_einstellung_landet_im_auftrag(api):
    client, update_dir = api
    _lebenszeichen(update_dir)
    await client.patch("/api/update/settings", json={"channel": "dev"})
    await client.post("/api/update/apply", json={"target": "a3f91c2"})
    assert json.loads((update_dir / "request.json").read_text(encoding="utf-8"))["channel"] == "dev"


async def test_waehrend_eines_laufenden_updates_gibt_es_409(api):
    client, update_dir = api
    _lebenszeichen(update_dir, phase="pull", id="laeuft")
    response = await client.post("/api/update/apply", json={"target": "0.3.0"})
    assert response.status_code == 409


async def test_ein_unbekannter_kanal_wird_abgewiesen(api):
    client, _ = api
    assert (await client.patch("/api/update/settings", json={"channel": "beliebig"})).status_code == 422


async def test_ohne_sitzung_kein_zugriff(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), update_dir=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/update/status")).status_code == 401
        assert (await client.post("/api/update/apply", json={"target": "0.3.0"})).status_code == 401
    store.close()


async def test_die_pruefung_laesst_sich_abschalten(api):
    client, _ = api
    await client.patch("/api/update/settings", json={"check_enabled": False})
    body = (await client.get("/api/update/check")).json()
    assert body["target"] is None
    assert body["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/test_update_api.py -v`
Expected: FAIL — `TypeError: build_app() got an unexpected keyword argument 'update_dir'`

- [ ] **Step 3: Write the router**

`src/loxmatter/api/update.py` (GPL-Kopf, dann):

```python
"""Die Update-Routen - Entwurf "Updates ueber die Oberflaeche einspielen"
(2026-09-08), Abschnitte 9 und 10.

Dieses Modul klebt `loxmatter.update` (Dateien) und
`loxmatter.update_check` (Netz) an HTTP und tut sonst nichts. Insbesondere
prueft es das Ziel NICHT - das ist Sache des Beiwagens (Spec-Abschnitt 10),
und eine Grenze, die sich auf eine Vorpruefung eine Schicht hoeher
verlaesst, ist keine.

Alle Routen liegen hinter demselben `api_guard` wie jede andere
`/api`-Route. Bewusst KEINE erneute Passwortabfrage: dieselbe Sitzung
laedt heute schon die Fabric-Sicherung herunter, also die unersetzlichen
Anmeldedaten des gesamten Matter-Netzes. Ein Update auf eine
veroeffentlichte Version ist demgegenueber der kleinere Preis; eine zweite
Abfrage wuerde Sicherheit behaupten, die woanders laengst nicht besteht."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from loxmatter import update as update_files
from loxmatter import update_check
from loxmatter.model.store import Store
from loxmatter.version import build_info


class ApplyIn(BaseModel):
    target: str


class ApplyOut(BaseModel):
    id: str


class UpdateSettingsIn(BaseModel):
    channel: str | None = None
    check_enabled: bool | None = None


async def _fetch(url: str) -> Any:
    """Die einzige Stelle, die tatsaechlich nach draussen greift.

    Kurzer Zeitrahmen: die Oberflaeche wartet auf diese Antwort, und ein
    haengender GitHub-Aufruf duerfte den System-Tab nicht blockieren.
    """
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers={"Accept": "application/vnd.github+json"}) as response:
            response.raise_for_status()
            return await response.json()


def build_update_router(store: Store, update_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/update")

    def _status() -> dict[str, Any]:
        state = update_files.read_state(update_dir)
        return {
            "state": None if state is None else {
                "phase": state.phase,
                "id": state.id,
                "from": state.from_version,
                "to": state.to_version,
                "error": state.error,
                "rolled_back": state.rolled_back,
                "healthy": state.healthy,
            },
            "updater_present": update_files.updater_present(state, now=datetime.now(timezone.utc)),
            "log": update_files.read_log(update_dir),
            "channel": store.update_settings.get_channel(),
            "check_enabled": store.update_settings.get_check_enabled(),
        }

    @router.get("/status")
    async def status() -> dict[str, Any]:
        return _status()

    @router.patch("/settings")
    async def settings(patch: UpdateSettingsIn) -> dict[str, Any]:
        if patch.channel is not None:
            try:
                store.update_settings.set_channel(patch.channel)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if patch.check_enabled is not None:
            store.update_settings.set_check_enabled(patch.check_enabled)
        return _status()

    @router.get("/check")
    async def check() -> dict[str, Any]:
        info = build_info()
        if not store.update_settings.get_check_enabled():
            # Kein Fehler, sondern eine Einstellung - und trotzdem im
            # `error`-Feld, damit die Oberflaeche einen Satz zu zeigen hat
            # statt eines leeren Kastens.
            return {
                "channel": store.update_settings.get_channel(),
                "target": None, "title": None, "notes": None, "behind": None,
                "checked_at": None,
                "error": "Die Suche nach Updates ist abgeschaltet.",
            }
        result = await update_check.check(
            store.update_settings.get_channel(),
            current_version=info.version,
            current_commit=info.commit,
            fetch=_fetch,
        )
        return {
            "channel": result.channel, "target": result.target, "title": result.title,
            "notes": result.notes, "behind": result.behind,
            "checked_at": result.checked_at, "error": result.error,
        }

    @router.post("/apply")
    async def apply(body: ApplyIn) -> ApplyOut:
        state = update_files.read_state(update_dir)
        if not update_files.updater_present(state, now=datetime.now(timezone.utc)):
            # 503 und nicht 200: sonst schriebe die Bruecke einen Auftrag in
            # ein Volume, das niemand liest, und die Oberflaeche zeigte
            # einen Fortschritt, der nie beginnt.
            raise HTTPException(status_code=503, detail="Kein Updater vorhanden.")
        try:
            job_id = update_files.request_update(
                update_dir,
                channel=store.update_settings.get_channel(),
                target=body.target,
            )
        except update_files.UpdateBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ApplyOut(id=job_id)

    return router
```

- [ ] **Step 4: Wire it into `build_app`**

In `src/loxmatter/loxone/server.py`: `build_app` bekommt einen neuen Parameter mit Vorgabe, damit alle bestehenden Aufrufer unverändert bleiben:

```python
def build_app(
    store: Store,
    invoke: Invoke,
    runtime: BuildAppRuntime,
    *,
    # Vorgabe passt zum eingehaengten Volume (siehe Compose-Datei). Als
    # Parameter, damit die Tests gegen ein tmp_path-Verzeichnis laufen -
    # ohne das brauchte jeder Test dieser Routen einen Patch auf einen
    # Modulkonstanten, und zwei Tests nebeneinander stoerten sich.
    update_dir: Path = Path("/data/update"),
    ...
```

und bei den übrigen Routern:

```python
    app.include_router(build_update_router(store, update_dir), dependencies=api_guard)
```

Dazu in `cli.py`, beim `run`-Kommando, `update_dir` aus `LOXMATTER_UPDATE_DIR` (Vorgabe `/data/update`) durchreichen.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_update_api.py -v`
Expected: 9 passed

- [ ] **Step 6: Prove the 503 test can fail**

Entferne probeweise die `updater_present`-Prüfung in `apply` und führe die Tests aus.
Expected: `test_ohne_beiwagen_wird_kein_auftrag_angenommen` FAILT (`200 != 503`). Danach zurücknehmen.

- [ ] **Step 7: Lint, types, full suite, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/loxmatter/api/update.py src/loxmatter/loxone/server.py src/loxmatter/cli.py tests/api/test_update_api.py
git commit -m "feat(api): /api/update/status, /check, /apply, /settings

Die Routen kleben die Datei- und die Netzseite an HTTP und tun sonst
nichts. Insbesondere pruefen sie das Ziel NICHT - das ist Sache des
Beiwagens, und eine Grenze, die sich auf eine Vorpruefung eine Schicht
hoeher verlaesst, ist keine.

Ohne Beiwagen antwortet /apply mit 503 statt 200: sonst schriebe die
Bruecke einen Auftrag in ein Volume, das niemand liest, und die
Oberflaeche zeigte einen Fortschritt, der nie beginnt.

Keine erneute Passwortabfrage. Dieselbe Sitzung laedt heute schon die
Fabric-Sicherung herunter, also die unersetzlichen Anmeldedaten des
gesamten Matter-Netzes; eine zweite Abfrage wuerde Sicherheit behaupten,
die woanders laengst nicht besteht.

update_dir kommt als Parameter in build_app, damit die Tests gegen ein
tmp_path-Verzeichnis laufen statt sich ueber eine Modulkonstante zu
stoeren.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Die Karte mit ihren vier Zuständen

**Files:**
- Modify: `src/loxmatter/web/index.html` (die Karte aus Stufe 1, Task 4, wird erweitert)
- Modify: `src/loxmatter/web/app.js`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_update_api.py` (ein Textschlüssel-Test)

**Interfaces:**
- Consumes: die vier Routen aus Task 8.
- Produces: die fertige Oberfläche.

- [ ] **Step 1: Write the failing test**

Ans Ende von `tests/api/test_update_api.py`:

```python
def test_die_oberflaeche_kennt_alle_texte_der_update_karte():
    from loxmatter import i18n

    for key in (
        "web.system.update_available",
        "web.system.update_up_to_date",
        "web.system.update_apply",
        "web.system.update_cancel",
        "web.system.update_confirm_title",
        "web.system.update_confirm_downtime",
        "web.system.update_confirm_schema",
        "web.system.update_step_backup",
        "web.system.update_step_pull",
        "web.system.update_step_recreate",
        "web.system.update_step_health",
        "web.system.update_restarting",
        "web.system.update_restarting_hint",
        "web.system.update_done",
        "web.system.update_failed",
        "web.system.update_rolled_back",
        "web.system.update_no_updater",
        "web.system.update_channel_stable",
        "web.system.update_channel_dev",
        "web.system.update_channel_dev_warning",
        "web.system.update_check_disabled",
        "web.system.update_behind",
    ):
        assert i18n.raw_template(key), key
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_update_api.py -v`
Expected: FAIL — `KeyError: 'web.system.update_available'`

- [ ] **Step 3: Add the strings**

In `src/loxmatter/i18n/strings.yaml`, hinter den `web.system.version_*`-Schlüsseln aus Stufe 1:

```yaml
web.system.update_available:
  en: "Version {version} available"
  de: "Version {version} verfügbar"
web.system.update_up_to_date:
  en: "Up to date. Last checked {checked_at}."
  de: "Auf dem neuesten Stand. Zuletzt geprüft {checked_at}."
web.system.update_apply:
  en: "Install update"
  de: "Update einspielen"
web.system.update_cancel:
  en: "Cancel"
  de: "Abbrechen"
web.system.update_confirm_title:
  en: "Install version {version}?"
  de: "Update auf {version} einspielen?"
web.system.update_confirm_downtime:
  en: "About a minute without the bridge. The Miniserver receives no values during that time; afterwards the bridge sends everything again. Signal keys, rooms and the Loxone wiring stay untouched, and the database is backed up first."
  de: "Rund eine Minute ohne Brücke. Der Miniserver erhält in dieser Zeit keine Werte; danach sendet die Brücke alles erneut. Signalschlüssel, Räume und die Loxone-Verdrahtung bleiben unverändert, und die Datenbank wird vorher gesichert."
web.system.update_confirm_schema:
  en: "Raises the database schema from {from_version} to {to_version}. Going back to the previous version stays possible, but the new fields will not be maintained by it."
  de: "Hebt das Datenbankschema von {from_version} auf {to_version}. Ein Rückfall auf die vorherige Version bleibt möglich, die neuen Felder werden von ihr aber nicht gepflegt."
web.system.update_step_backup:
  en: "Backing up the database"
  de: "Datenbank wird gesichert"
web.system.update_step_pull:
  en: "Loading the image"
  de: "Image wird geladen"
web.system.update_step_recreate:
  en: "Restarting the service"
  de: "Dienst wird neu gestartet"
web.system.update_step_health:
  en: "Waiting for the bridge to report healthy"
  de: "Warten, bis die Brücke sich gesund meldet"
web.system.update_restarting:
  en: "The bridge is restarting"
  de: "Die Brücke startet neu"
web.system.update_restarting_hint:
  en: "This is part of the update. The page reconnects by itself as soon as the service answers. With many devices the first start takes longer: the bridge fetches every node before it goes on the network."
  de: "Das gehört dazu. Diese Seite verbindet sich von selbst wieder, sobald der Dienst antwortet. Bei vielen Geräten dauert der erste Start länger: die Brücke holt jeden Knoten ab, bevor sie ans Netz geht."
web.system.update_done:
  en: "Now running: {version}"
  de: "Läuft jetzt: {version}"
web.system.update_failed:
  en: "Update failed"
  de: "Update fehlgeschlagen"
web.system.update_rolled_back:
  en: "Version {version} did not report healthy. It was rolled back on its own; {from_version} is running again. The database was not touched."
  de: "Version {version} meldete sich nicht gesund. Es wurde selbsttätig zurückgesetzt, {from_version} läuft wieder. Die Datenbank wurde nicht angefasst."
web.system.update_no_updater:
  en: "This installation has no updater. Update from the console: git pull && ./scripts/update.sh"
  de: "Diese Installation hat keinen Updater. Aktualisieren über die Konsole: git pull && ./scripts/update.sh"
web.system.update_channel_stable:
  en: "Stable"
  de: "Stabil"
web.system.update_channel_dev:
  en: "Development"
  de: "Entwicklung"
web.system.update_channel_dev_warning:
  en: "The development channel installs whatever is currently on main, including untested intermediate states."
  de: "Der Entwicklungskanal spielt ein, was gerade auf main liegt — auch ungetestete Zwischenstände."
web.system.update_check_disabled:
  en: "The search for updates is switched off."
  de: "Die Suche nach Updates ist abgeschaltet."
web.system.update_behind:
  en: "{behind} commits behind main"
  de: "{behind} Commits hinter main"
```

- [ ] **Step 4: Add the state and the polling in `app.js`**

Neben `versionInfo` aus Stufe 1:

```javascript
    // Der Update-Zustand, wie ihn der Beiwagen in state.json schreibt.
    updateStatus: null,
    updateAvailable: null,
    updateConfirming: false,
    updateError: null,
    // Der Zeitgeber laeuft NUR, solange ein Auftrag laeuft. Ein dauerhaftes
    // Pollen waere fuer eine Angabe, die sich zehnmal im Jahr aendert, eine
    // Anfrage pro Sekunde auf einem Pi - und der Rest der Oberflaeche haengt
    // ohnehin am Live-Kanal.
    updateTimer: null,
```

Methoden:

```javascript
    /** Die vier laufenden Phasen. Ausserhalb davon ruht der Zeitgeber. */
    updateRunning() {
      const phase = this.updateStatus?.state?.phase;
      return ["queued", "backup", "pull", "recreate", "health", "rollback"].includes(phase);
    },

    async loadUpdateStatus() {
      try {
        this.updateStatus = await this.request("GET", "/api/update/status");
        this.updateError = null;
      } catch (error) {
        // Waehrend des Neustarts ist die Bruecke weg - das ist der
        // Normalfall dieses Ablaufs und kein Fehler. Der zuletzt bekannte
        // Zustand bleibt stehen, und der Zeitgeber versucht es weiter.
        if (!this.updateRunning()) {
          this.updateError = error.message;
        }
      }
      if (this.updateRunning() && !this.updateTimer) {
        this.updateTimer = setInterval(() => this.loadUpdateStatus(), 2000);
      }
      if (!this.updateRunning() && this.updateTimer) {
        clearInterval(this.updateTimer);
        this.updateTimer = null;
        // Nach dem Ende einmal die Version neu holen: die Karte oben soll
        // die neue Nummer zeigen, nicht die, mit der die Seite geladen wurde.
        this.versionInfo = await this.request("GET", "/api/version");
      }
    },

    async loadUpdateCheck() {
      try {
        this.updateAvailable = await this.request("GET", "/api/update/check");
      } catch (error) {
        this.updateAvailable = { target: null, error: error.message };
      }
    },

    async applyUpdate() {
      this.updateConfirming = false;
      this.updateError = null;
      try {
        await this.request("POST", "/api/update/apply", { target: this.updateAvailable.target });
        await this.loadUpdateStatus();
      } catch (error) {
        this.updateError = error.message;
      }
    },

    async setUpdateChannel(channel) {
      this.updateStatus = await this.request("PATCH", "/api/update/settings", { channel });
      await this.loadUpdateCheck();
    },
```

In `loadSystem()`, nach dem `versionInfo`-Aufruf aus Stufe 1:

```javascript
        await this.loadUpdateStatus();
        await this.loadUpdateCheck();
```

- [ ] **Step 5: Extend the card in `index.html`**

Innerhalb der Karte aus Stufe 1, nach dem `<template x-if="versionInfo">`-Block:

```html
          <!-- Kein Beiwagen: kein toter Knopf, sondern der Konsolenweg. -->
          <p class="hint" x-show="updateStatus && !updateStatus.updater_present && !updateRunning()" x-cloak
             x-text="t('web.system.update_no_updater')"></p>

          <!-- 1: Ein Update steht bereit. -->
          <template x-if="updateStatus?.updater_present && !updateRunning() && updateAvailable?.target">
            <div>
              <p class="banner warn"
                 x-text="updateStatus.channel === 'dev'
                   ? t('web.system.update_behind', { behind: updateAvailable.behind })
                   : t('web.system.update_available', { version: updateAvailable.target })"></p>
              <pre class="notes" x-show="updateAvailable.notes" x-cloak x-text="updateAvailable.notes"></pre>
              <button @click="updateConfirming = true" x-text="t('web.system.update_apply')"></button>
            </div>
          </template>

          <p class="hint" x-show="!updateRunning() && updateAvailable && !updateAvailable.target && !updateAvailable.error"
             x-cloak x-text="t('web.system.update_up_to_date', { checked_at: updateAvailable.checked_at })"></p>
          <p class="hint" x-show="!updateRunning() && updateAvailable?.error" x-cloak
             x-text="updateAvailable.error"></p>

          <!-- 2: Bestätigung. -->
          <template x-if="updateConfirming">
            <div class="confirm">
              <h3 x-text="t('web.system.update_confirm_title', { version: updateAvailable.target })"></h3>
              <p class="banner warn" x-text="t('web.system.update_confirm_downtime')"></p>
              <div class="row">
                <button @click="updateConfirming = false" x-text="t('web.system.update_cancel')"></button>
                <button @click="applyUpdate()" x-text="t('web.system.update_apply')"></button>
              </div>
            </div>
          </template>

          <!-- 3: Läuft — inklusive des Moments, in dem die Brücke weg ist. -->
          <template x-if="updateRunning()">
            <div>
              <h3 x-show="updateStatus.state.phase !== 'health'" x-cloak
                  x-text="t('web.system.update_available', { version: updateStatus.state.to })"></h3>
              <h3 x-show="updateStatus.state.phase === 'health'" x-cloak
                  x-text="t('web.system.update_restarting')"></h3>
              <ul class="steps">
                <li :class="{ done: ['pull','recreate','health'].includes(updateStatus.state.phase), now: updateStatus.state.phase === 'backup' }"
                    x-text="t('web.system.update_step_backup')"></li>
                <li :class="{ done: ['recreate','health'].includes(updateStatus.state.phase), now: updateStatus.state.phase === 'pull' }"
                    x-text="t('web.system.update_step_pull')"></li>
                <li :class="{ done: updateStatus.state.phase === 'health', now: updateStatus.state.phase === 'recreate' }"
                    x-text="t('web.system.update_step_recreate')"></li>
                <li :class="{ now: updateStatus.state.phase === 'health' }"
                    x-text="t('web.system.update_step_health')"></li>
              </ul>
              <p class="hint" x-show="updateStatus.state.phase === 'health'" x-cloak
                 x-text="t('web.system.update_restarting_hint')"></p>
            </div>
          </template>

          <!-- 4: Ergebnis. -->
          <p class="banner ok" x-show="updateStatus?.state?.phase === 'done'" x-cloak
             x-text="t('web.system.update_done', { version: updateStatus.state.to })"></p>
          <template x-if="updateStatus?.state?.phase === 'failed'">
            <div>
              <p class="banner danger" x-text="t('web.system.update_failed')"></p>
              <p x-show="updateStatus.state.rolled_back" x-cloak
                 x-text="t('web.system.update_rolled_back', { version: updateStatus.state.to, from_version: updateStatus.state.from })"></p>
              <pre class="notes" x-text="updateStatus.log.slice(-8).join('\n')"></pre>
              <button @click="downloadFabricBackup()" x-text="t('web.system.backup_download')"></button>
            </div>
          </template>
```

- [ ] **Step 6: Change the disconnect banner's text during an update**

Am bestehenden Verbindungsbanner (`index.html:271`) die Bedingung um `&& !updateRunning()` erweitern und daneben eine zweite Zeile stellen:

```html
    <div class="banner warn" x-show="!socketConnected && updateRunning()" x-cloak
         x-text="t('web.system.update_restarting')"></div>
```

Ohne das sähe der geplante Neustart aus wie eine Störung — der Zustand, den dieser ganze Entwurf sichtbar machen soll.

- [ ] **Step 7: Check the Alpine bindings in a throwaway harness**

Ein Browsertest belegt nur die Auslieferung. Fahre die vier Zustände gegen erfundene Zustandsdateien:

```bash
mkdir -p /tmp/lox-update
export LOXMATTER_UPDATE_DIR=/tmp/lox-update
uv run python scripts/dev_web_server.py
```

Dann nacheinander, jeweils den System-Tab neu laden:

```bash
# kein Beiwagen
rm -f /tmp/lox-update/state.json
# bereit
printf '{"id":null,"phase":"idle","updater_seen_at":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > /tmp/lox-update/state.json
# läuft, Brücke weg
printf '{"id":"a","phase":"health","from":"0.2.0","to":"0.3.0","updater_seen_at":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > /tmp/lox-update/state.json
# fehlgeschlagen, zurückgesetzt
printf '{"id":"a","phase":"failed","from":"0.2.0","to":"0.3.0","rolled_back":true,"healthy":true,"error":"nicht gesund","updater_seen_at":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > /tmp/lox-update/state.json
```

Prüfe je Zustand: die richtige Darstellung erscheint, die Browserkonsole zeigt **keine** Alpine-Fehler, und im Zustand „läuft" sind die ersten drei Schritte abgehakt und der vierte hervorgehoben.

- [ ] **Step 8: Lint, types, full suite, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
git add src/loxmatter/web/ src/loxmatter/i18n/strings.yaml tests/api/test_update_api.py
git commit -m "feat(web): die Update-Karte mit ihren vier Zustaenden

Der dritte Zustand ist der Grund fuer den ganzen Beiwagen: waehrend die
Bruecke neu startet, ist sie selbst nicht befragbar - der Fortschritt
kommt aus state.json im gemeinsamen Volume. Das Verbindungsbanner
bekommt fuer diese Zeit einen anderen Text, sonst saehe der geplante
Neustart aus wie eine Stoerung.

Der Zeitgeber laeuft nur waehrend eines Auftrags. Eine Anfrage pro
Sekunde fuer eine Angabe, die sich zehnmal im Jahr aendert, waere auf
einem Pi verschwendet.

Waehrend des Updates wird nichts gesperrt: eine Minute Bevormundung waere
der hoehere Preis, und wer mitten im Neustart etwas versucht, bekommt die
Erklaerung im Banner.

Ohne Beiwagen steht dort der Konsolenweg statt eines toten Knopfes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Der erste echte Durchlauf, das README und 0.3.0

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `docs/OPERATIONS.md`, `scripts/capture_screenshots.py`, `pyproject.toml`
- Create: `docs/screenshots/update.png`

- [ ] **Step 1: Run a real update on the test host**

Voraussetzung: der Beiwagen läuft (Task 5), und es gibt eine neuere veröffentlichte Version als die laufende. Notfalls `v0.2.1` mit einer belanglosen Änderung taggen.

Im Browser, System-Tab: Knopf drücken, bestätigen, **hinsehen**. Erwartet:

- Die vier Schritte laufen sichtbar durch.
- Beim Neustart verschwindet die Verbindung, und es steht „Die Brücke startet neu" da — **kein rotes Störungsbanner**.
- Danach steht oben die neue Versionsnummer.
- `docker exec loxmatter ls /data/backups/` zeigt eine frische Sicherung.

- [ ] **Step 2: Provoke a real rollback**

Der wichtigste Prüfpunkt des ganzen Vorhabens, und er lässt sich nicht simulieren:

```bash
# Ein Image, das absichtlich nicht gesund wird
cd deploy/testhost
docker tag ghcr.io/lucienkerl/loxmatter:0.2.0 ghcr.io/lucienkerl/loxmatter:0.2.99
```

Dann in der Compose-Datei probeweise `LOXMATTER_HEALTH_TIMEOUT: "20"` setzen, den Beiwagen neu starten, in der Oberfläche auf `0.2.99` aktualisieren, und **den Netzstecker von matter-server ziehen** (`docker stop matter-server`), damit die Brücke nicht gesund wird.

Erwartet:

- Nach 20 s wechselt die Karte auf „Update fehlgeschlagen" mit „0.2.0 läuft wieder".
- `docker exec loxmatter cat /data/update/LETZTER-FEHLSCHLAG.txt` nennt beide Versionen, den Sicherungspfad und die drei Befehle.
- In der `.env` steht wieder `LOXMATTER_IMAGE_TAG=0.2.0`.
- **Genau zwei** `compose up`-Zeilen im Protokoll.

Danach `docker start matter-server`, den Timeout zurücksetzen, den Testtag löschen.

- [ ] **Step 3: Screenshot**

In `scripts/capture_screenshots.py`, im System-Ablauf: `shoot(page, "update", "card:Version")`

Run: `uv run python scripts/capture_screenshots.py`
Expected: `docs/screenshots/update.png` zeigt die Karte mit einem bereitstehenden Update. **`system.png` dabei verwerfen** — es ist nicht reproduzierbar.

- [ ] **Step 4: README and OPERATIONS**

Im `README.md`, den Abschnitt „Updating" aus Stufe 1 ergänzen:

```markdown
Since 0.3.0 you can do this from the browser instead: **System → Version**
shows what is running, tells you when a new version is available, and
installs it at the press of a button — with a backup taken first and an
automatic rollback if the new version does not come up healthy.

This needs the `loxmatter-updater` service from the compose file. If your
installation predates 0.3.0, bring it in once from the console:

```bash
cd ~/loxmatter && git pull && ./scripts/update.sh
```

That container holds the Docker socket and is therefore root-equivalent on
the host. It has no ports and no host network, it talks to the bridge only
through files in a shared volume, and it never runs text from a request as
a command — it can install a published loxmatter version and nothing else.
If you would rather not have it, delete the service; the bridge notices and
points you back to the console.
```

In `docs/OPERATIONS.md` einen Abschnitt „Wenn ein Update schiefgeht" mit dem Pfad von `LETZTER-FEHLSCHLAG.txt`, dem Sicherungsverzeichnis und dem Hinweis, dass die Datenbank bewusst nicht mit zurückgesetzt wird.

- [ ] **Step 5: Release 0.3.0**

`pyproject.toml` auf `0.3.0`, `CHANGELOG.md` ergänzen, dann nach der Regel aus `docs/DEVELOPMENT.md`:

```bash
git add -A && git commit -m "release: 0.3.0

Updates lassen sich ueber die Oberflaeche einspielen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
git tag -a v0.3.0 -m "0.3.0" && git push && git push --tags
gh release create v0.3.0 --title "0.3.0" --notes-file -
```

Der Release-Text ist der Changelog-Abschnitt — **er ist der erste, den die Oberfläche jemandem im Bestätigungsdialog zeigt.** Entsprechend schreiben.

- [ ] **Step 6: Verify the loop closes**

Nach dem Release auf dem Test-Pi (der noch auf `0.2.x` steht): der System-Tab meldet von selbst „Version 0.3.0 verfügbar", und ein Klick spielt sie ein.

Damit ist der Weg über SSH für ein gewöhnliches Update abgeschafft — das Ziel dieses Entwurfs.

---

## Selbstprüfung dieses Plans

**Spec-Abdeckung** — jeder Abschnitt der Spec hat eine Task: 4 → Stufe 1/1–2; 5 → Stufe 1/5; 6 → Tasks 1, 5; 7 → Tasks 2, 6; 8 → Tasks 3, 4; 9 → Tasks 7, 9; 10 → Tasks 2, 5, 8; 11 → Tasks 6, 8, 9 (Erkennung), 10 (README); 12 → Task 9; 13 → jede Task; 14 → nichts zu bauen; 15 → die Aufteilung in zwei Pläne.

**Namensabgleich:** `update_dir` heißt in allen Signaturen gleich; `UpdateState.from_version`/`to_version` (Python, weil `from` ein Schlüsselwort ist) entsprechen `from`/`to` im JSON — die Umsetzung steht in `read_state` und in `_status()`; die Phasennamen sind in Task 2 abschließend aufgezählt und werden in Task 6 (`_LAUFENDE_PHASEN`) und Task 9 (`updateRunning()`) identisch benutzt.

**Erledigt:** `test_der_rueckfall_ruehrt_die_datenbank_nicht_an` prüfte ursprünglich mit `"-x" not in calls` auch auf ein entpackendes `tar` — grob genug, um an einem beliebigen anderen `-x`-Flag anzuschlagen. Der Test sieht sich jetzt nur die `tar`-Aufrufe an.

## Nachträge aus Stufe 1 (8. September 2026, nach dem Release 0.2.0)

Drei Dinge wurden erst sichtbar, als Stufe 1 tatsächlich lief:

1. **Die laufende Version kommt aus dem laufenden Container, nicht aus der `.env`.** Seit 0.2.0 trägt jede frische Installation `LOXMATTER_IMAGE_TAG=stable`, und „stable" ist keine Version. Der Vergleich „ist 0.3.0 neuer als stable" hat keine Antwort, und die Vorwärts-Prüfung aus Spec-Abschnitt 10, Regel 3, wäre auf jeder Standardinstallation stillschweigend wirkungslos geblieben. `running_version()` liest stattdessen `LOXMATTER_VERSION` aus dem laufenden Container — die Angabe, die Stufe 1 genau dafür ins Image gelegt hat.
2. **Der Rückfall pinnt eine konkrete Version.** Stand in der `.env` ein Alias, zeigt dieser in der Registry inzwischen auf die gescheiterte Fassung; ihn zurückzuschreiben hieße, sie beim nächsten `compose pull` wiederzuholen — ohne dass irgendetwas sich erinnert, dass je ein Rückfall stattfand.
3. **Eine Fassung ohne Versionsangabe wird abgelehnt**, statt geraten. Wer ein von Hand gebautes Image fährt, bekommt eine ehrliche Auskunft und den Konsolenweg.
