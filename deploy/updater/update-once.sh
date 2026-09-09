#!/bin/sh
# loxmatter - connects Matter devices to a Loxone Miniserver.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
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
# Baked in at build time by deploy/updater/Dockerfile's own ARG/ENV pair
# (mirroring the bridge's own LOXMATTER_VERSION, see that Dockerfile),
# never set by docker-compose.yml - this is the one thing the sidecar
# knows about ITSELF that is worth reporting: the web UI compares this
# against the running bridge's version and says so when they differ (see
# `set_state` below, and `src/loxmatter/update.py`/`api/update.py` on the
# bridge side). Empty, not defaulted to "dev" here, is deliberate: every
# sidecar built before this field existed has no such ENV at all, and an
# empty string is what `set_state`'s own "empty means null" convention
# (already used for FROM/TO/etc.) turns into `updater_version: null` in
# state.json - the honest "this sidecar predates the field" answer, not a
# claim of "dev" it never made. `deploy/updater/Dockerfile`'s own ARG
# default of "dev" only applies to a MANUAL build of a CURRENT checkout
# without CI's build-arg - a sidecar from before this change was never
# built with the ARG at all, so no default there can reach it.
UPDATER_VERSION="${LOXMATTER_UPDATER_VERSION:-}"

# This container's own image, by digest - resolved exactly ONCE, by
# entrypoint.sh, before it ever starts this script's poll loop (see
# entrypoint.sh's own comment). NOT resolved here, on every pass, even
# though a `docker inspect` against the local socket would be cheap
# enough on its own: `tests/test_updater_script.py`'s very first tests
# pin down that a pure heartbeat pass - and, more importantly, a
# REJECTED, malformed request - makes NO docker call whatsoever (spec
# section 10's own claim: "even someone who fully takes over the bridge
# can at most install a published, newer version" is proven exactly by
# that absence). Resolving it from inside this script would touch docker
# unconditionally on every single pass, malformed requests included, and
# quietly weaken that proof. Describes THIS CONTAINER, never the job,
# exactly like $UPDATER_VERSION above - so entrypoint.sh resolving it
# once, the same way the Dockerfile bakes in $UPDATER_VERSION, is the
# right layer for a fact that never changes for this container's whole
# life. Empty (never defaulted) means null in state.json, the same
# convention $UPDATER_VERSION already uses - an entrypoint.sh that could
# not resolve it (no `RepoDigests` entry) reports nothing, honestly,
# rather than a value nothing backs.
UPDATER_DIGEST="${LOXMATTER_UPDATER_DIGEST:-}"

REQUEST="$UPDATE_DIR/request.json"
STATE="$UPDATE_DIR/state.json"
LOG="$UPDATE_DIR/log.txt"
FAILURE="$UPDATE_DIR/LETZTER-FEHLSCHLAG.txt"
ENV_FILE="$STACK/.env"

mkdir -p "$UPDATE_DIR" "$BACKUP_DIR" "$UPDATE_DIR/handled"

# A literal newline, for `case ... in *"$NEWLINE"*)` further down - the
# only reliable way in POSIX sh to test whether a value contains one. A
# glob/case pattern matches the whole word (newlines included); `grep`
# cannot be made to do that here, see the validation section below.
NEWLINE='
'

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# Atomic, always. The bridge reads this file once a second and must never
# see a half-written one - a truncated JSON would be indistinguishable to
# it from "no sidecar present".
#
# Refuses to write when $STATE exists but is not a regular file (a
# directory, reachable via the heartbeat block below: `[ -f "$STATE" ]`
# is false for a directory too, so that block's `else` calls `set_state
# idle` - i.e. this function - on it same as it would for a missing file).
# Without this check, `mv "$STATE.tmp" "$STATE"` onto a directory does not
# error: mv moves the tmp file INTO the directory, under the same
# basename, and returns 0 - the write "succeeds" while state.json stays a
# directory forever, permanently unreadable to the bridge, and every later
# pass repeats the same silent no-op with the dedup guard permanently
# unable to read a LAST id. Failing loudly here (non-zero return, which
# `set -eu` turns into the script stopping) is deliberate: a sidecar that
# visibly stops is something an operator notices; one that keeps running
# and silently no-ops forever is not.
write_state() {
  if [ -e "$STATE" ] && [ ! -f "$STATE" ]; then
    printf 'write_state: %s exists and is not a regular file - refusing to write\n' "$STATE" >&2
    return 1
  fi
  printf '%s\n' "$1" > "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

# Best-effort. The log is an audit trail, not part of the state machine -
# a full disk or an unwritable $LOG must not be able to kill the script
# under `set -eu` and thereby stop `set_state` from ever running. (It did:
# an unwritable $LOG previously took down reject() before it recorded the
# rejection - see reject() below.) So log() swallows its own failure
# instead of letting a diagnostic side effect become fatal to the one
# thing the bridge actually depends on: an honest state.json.
log() {
  { printf '%s %s\n' "$(now)" "$*" >> "$LOG"
    tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  } 2>/dev/null || true
}

# ---------------------------------------------------- $REPO ownership --
# This sidecar runs as root - deploy/updater/Dockerfile sets no USER, and
# the Docker socket it holds already makes it root-equivalent on the host
# regardless (see docker-compose.yml's own block comment on that
# service). install.sh clones $REPO as the INVOKING user. Since git
# 2.35.2, a repository whose owner (per stat) differs from the caller's
# effective uid is refused outright - "fatal: detected dubious ownership
# in repository at ..." - and nothing anywhere in this tree ever set
# safe.directory, so every git call this script makes against $REPO
# failed from the very first run against any checkout install.sh ever
# produces. git_repo()/run_git() below fix that with `-c
# safe.directory=$REPO` on every single invocation - not a one-time
# `git config --global --add safe.directory`, which would write into
# root's own $HOME/.gitconfig inside this CONTAINER's own ephemeral
# filesystem and be gone the moment the container is recreated - not a
# rare event: `restart: unless-stopped` after a host reboot, or an
# operator refreshing this sidecar's own image by hand (see the removed
# self-replacement's own comment, near the end of the success branch
# below, for why that refresh is manual now) both do exactly that.
#
# Fixing only that trades one failure for a worse one: once git actually
# runs, `fetch` and `checkout` write .git/index, FETCH_HEAD and the
# working tree - AS ROOT. The checkout's ORIGINAL owner (the operator who
# ran install.sh) then finds their OWN `git`/`./scripts/update.sh`
# refused by the identical dubious-ownership check, now pointed back at
# THEM, on files this sidecar left behind. $REPO_UID/$REPO_GID are
# captured here, once, before any git call below could have changed
# them, and chown_repo_back restores them after every single call - not
# just once at the end of a pass - so the window in which any file could
# be caught root-owned is exactly one git invocation, regardless of
# whether that invocation succeeds, fails partway, or this script is
# killed by the SIGTERM trap immediately afterward.
#
# Resolved (and cached) lazily, on the first call that actually needs it
# - not unconditionally at the top of the script. This whole file runs
# every 2 seconds, forever (entrypoint.sh's poll loop), and the
# overwhelming majority of passes are a plain heartbeat with no request
# to act on at all; a `stat` on $REPO those passes have no other reason
# to touch would be a stat call this sidecar does not need multiplied
# across the rest of its running life for zero benefit.
#
# Empty when $REPO does not exist yet or `stat` cannot read it (a fresh
# install.sh run that has not cloned yet, say) - chown_repo_back treats
# that as "do nothing", never as "chown to root" (an empty first
# argument makes `chown` itself error, not silently no-op, which would
# otherwise turn a merely-not-yet-cloned repository into a spurious log
# line on every single pass).
chown_repo_back() {
  if [ -z "${REPO_OWNER_RESOLVED:-}" ]; then
    REPO_UID="$(stat -c %u "$REPO" 2>/dev/null || true)"
    REPO_GID="$(stat -c %g "$REPO" 2>/dev/null || true)"
    REPO_OWNER_RESOLVED=1
  fi
  if [ -n "$REPO_UID" ] && [ -n "$REPO_GID" ]; then
    chown -R "$REPO_UID:$REPO_GID" "$REPO" 2>/dev/null || true
  fi
}

# Every READ against $REPO whose OUTPUT a caller needs - rev-parse,
# merge-base --is-ancestor - goes through here rather than a bare `git -C
# "$REPO" ...`, so the safe.directory fix above applies to it too. Not
# logged to $LOG (run_git() below is the logged, write-side counterpart)
# - these are routine checks, several per accepted request, and this
# file's own log() already caps itself at 2000 lines; a caller that DOES
# want one logged calls log() itself, the same as before this function
# existed. chown_repo_back still runs here too, defensively: none of
# this function's three callers further down are documented to write
# anything, but a stray lockfile is cheaper to guard against here than to
# have relied on that never happening.
git_repo() {
  git -c "safe.directory=$REPO" -C "$REPO" "$@"
  git_repo_rc=$?
  chown_repo_back
  return $git_repo_rc
}

# Every WRITE against $REPO - fetch, checkout - goes through here.
# Mirrors run() below (same log line shape, same "$LOG" redirect) but
# additionally carries the safe.directory/chown-back pair documented
# above.
run_git() {
  log "\$ git -C $REPO $*"
  git -c "safe.directory=$REPO" -C "$REPO" "$@" >> "$LOG" 2>&1
  run_git_rc=$?
  chown_repo_back
  return $run_git_rc
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
  # POSIX sh has no `local` - this is a global. Named after the function,
  # not "version", so Tasks 3/4 adding their own bookkeeping to this file
  # cannot silently collide with it.
  running_version_raw="$(docker inspect "$SERVICE" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
    | sed -n -E 's/^LOXMATTER_VERSION=(.+)$/\1/p' | head -1)"
  printf '%s' "${running_version_raw:-unbekannt}"
}

# The tag from the .env - needed only for the rollback now, i.e. to know
# what to write back if the update fails.
current_tag() {
  # Same reasoning as running_version_raw above: a global, deliberately
  # named after its function rather than "tag".
  current_tag_raw="$(sed -n -E 's/^LOXMATTER_IMAGE_TAG=(.*)$/\1/p' "$ENV_FILE" 2>/dev/null | tail -1)"
  printf '%s' "${current_tag_raw:-stable}"
}

set_state() {
  # $1 phase, $2 error message (may be empty)
  #
  # `write_state "$(jq -n ...)"` used to discard the substitution's own
  # exit status - it is an ARGUMENT to write_state, not the command
  # `set -e` is watching. write_state has no way to tell "I got jq's real
  # output" from "I got jq's empty, stderr-swallowed failure" - so a
  # failing jq here used to make write_state persist an empty string as
  # the new state.json: updater_seen_at gone, the bridge concludes the
  # sidecar is absent and hides the update button for good.
  #
  # A target or id long enough to blow jq's own execve (E2BIG - roughly
  # 128 KiB on the Alpine sidecar's Linux MAX_ARG_STRLEN, ~523 KiB
  # measured on macOS) used to reach exactly this failure; the length and
  # character-set bounds on TARGET and JOB_ID (further down) close that
  # specific path. This check is the general remedy underneath those
  # bounds, not a replacement for them: it is what keeps a future
  # `--argjson rolled` or `--argjson healthy` (Task 3/4 will give both
  # attacker-reachable inputs) from reopening the same hole if either
  # ever produces a non-boolean value jq rejects.
  #
  # $ROLLED_BACK_TO carries $BACK - the concrete version the rollback
  # section (further down) restored, never the possibly-aliased $FROM -
  # out to state.json. Empty everywhere else in this script (the
  # `${ROLLED_BACK_TO:-}` default below), which `if $back == ""` turns
  # into `null` the same way an unset FROM/TO already does: nothing at
  # the file-protocol boundary distinguishes "never set" from "explicitly
  # empty," and null is the honest answer for both when no rollback has
  # happened. Read back out of the previous state.json by the SIGTERM
  # trap, same as FROM/TO/JOB_ID already are, so a signal arriving mid-
  # rollback-health-wait does not lose the value the earlier `set_state
  # rollback ""` call already recorded.
  #
  # $UPDATER_VERSION (top of file) is this sidecar's OWN version, baked
  # in at build time - unlike every other field above, it never changes
  # within a single container's lifetime, so every `set_state` call
  # writes the same value regardless of phase. Empty (never defaulted
  # here) means null, the same "empty means null" convention as
  # FROM/TO/ROLLED_BACK_TO - the honest answer for a sidecar built before
  # this field existed. Unlike FROM/TO/ROLLED_BACK_TO, `refresh_heartbeat`
  # below does NOT merely preserve whatever this call already wrote here -
  # it re-asserts $UPDATER_VERSION on every heartbeat too. See that
  # function's own comment for why: this field describes the CONTAINER
  # that is running right now, not the job, and a heartbeat always knows
  # that for certain.
  #
  # $UPDATER_DIGEST (top of file, read from entrypoint.sh's own one-time
  # resolution - see the comment there for why this script never resolves
  # it itself) is asserted the same way, for the same reason: it
  # describes THIS container, not the job, and `refresh_heartbeat`
  # re-asserts it on every heartbeat too. It is what the web UI now
  # compares against GHCR's own ":stable" manifest digest to decide
  # whether to show the "refresh the updater" warning at all - a
  # version-string comparison alone used to trigger that warning on every
  # release, whether or not deploy/updater/ had actually changed.
  if STATE_JSON="$(jq -n \
       --arg id "${JOB_ID:-}" --arg phase "$1" --arg error "${2:-}" \
       --arg from "${FROM:-}" --arg to "${TO:-}" --arg seen "$(now)" \
       --arg back "${ROLLED_BACK_TO:-}" --arg updater_version "${UPDATER_VERSION:-}" \
       --arg updater_digest "${UPDATER_DIGEST:-}" \
       --argjson rolled "${ROLLED:-false}" --argjson healthy "${HEALTHY:-true}" \
       '{id: (if $id == "" then null else $id end),
         phase: $phase,
         from: (if $from == "" then null else $from end),
         to:   (if $to == "" then null else $to end),
         error: (if $error == "" then null else $error end),
         rolled_back: $rolled,
         rolled_back_to: (if $back == "" then null else $back end),
         healthy: $healthy,
         updater_seen_at: $seen,
         updater_version: (if $updater_version == "" then null else $updater_version end),
         updater_digest: (if $updater_digest == "" then null else $updater_digest end)}')" \
    && [ -n "$STATE_JSON" ]; then
    write_state "$STATE_JSON"
  else
    printf 'set_state: jq failed to build state.json for phase "%s" - refusing to write\n' "$1" >&2
    return 1
  fi
}

# Refreshes `updater_seen_at` - and, since this fix, `updater_version` -
# in the existing $STATE, leaving every other field - phase, id, from,
# to, error, rolled_back_to, ... - exactly as it already reads. The
# `updater_seen_at` half is the fix for Important 1: the bridge treats the
# sidecar as absent after 30 seconds of silence (`_MAX_SILENT_SECONDS` in
# update.py), but `updater_seen_at` used to be written only by
# `write_state` - i.e. at pass start and at each `set_state` call - and
# nothing refreshed it WITHIN a phase. Proven against the unpatched script
# with a `docker compose pull` stub that took 8 seconds: the timestamp
# froze for the whole pull. On a Pi an arm64 image pull takes minutes and
# the health wait can hold one phase for up to 120 seconds - most of a
# SUCCESSFUL update used to spend most of its time looking, to the web UI,
# exactly like a crashed sidecar.
#
# `updater_version` is asserted here - overwritten from $UPDATER_VERSION,
# never merely left alone - for the opposite reason FROM/TO/ROLLED_BACK_TO
# are left alone: those describe THE JOB, which a heartbeat must not
# invent or lose, but $UPDATER_VERSION describes the CONTAINER that is
# running THIS PASS, which every heartbeat knows for certain regardless of
# whether a job is even in progress. Preserving it like the job fields
# used to be exactly wrong, in both directions, both proven on the
# maintainer's own Pi:
#   * a state.json last written by a pre-0.3.3 sidecar (no such field at
#     all) stayed without `updater_version` forever after a 0.3.3 sidecar
#     replaced it and ran for minutes doing nothing but heartbeats - the
#     System tab could not tell the updater had a version until some job
#     happened to run and call `set_state`.
#   * once a job HAS written a version, replacing the sidecar with a
#     newer one leaves the OLD value in state.json until the next job -
#     state.json would then assert a version that is not the one actually
#     running, either hiding a real lag or reporting one that does not
#     exist.
# Every heartbeat is a container that has just, by definition, proven it
# is alive and knows its own $UPDATER_VERSION - so every heartbeat is as
# good an authority on that one field as `set_state` itself, and asserting
# it here rather than trusting whatever an earlier (possibly different)
# container once wrote is what keeps it honest between jobs, not just at
# the moment one finishes. "Empty means null" applies here exactly as it
# does in `set_state`, for the same reason: a sidecar built before this
# field existed still has nothing truthful to assert.
#
# Deliberately NOT `set_state` itself: `set_state` rebuilds state.json
# from this script's own JOB_ID/FROM/TO/etc. variables, which is exactly
# right when the PHASE is genuinely changing, but wrong here - calling it
# mid-phase (from `wait_healthy`, below, which runs during BOTH the
# post-recreate health wait at phase "health" AND the rollback's own
# health wait at phase "rollback") would overwrite whatever phase is
# already correctly on disk with whatever this call site happens to
# think the phase is. The SIGTERM trap test relies on state.json reading
# "rollback" for that entire second wait; a heartbeat helper that can
# only ever touch the timestamp (and, now, the running container's own
# version) is what keeps that true.
#
# Best-effort, like `log()`: a momentary read/write hiccup here (the
# volume briefly unwritable) must not abort a pass that is otherwise
# making real progress - the NEXT full pass's own heartbeat block (see
# below) already recovers a state.json that stays broken across passes.
# Callers wrap this in `|| true` for exactly that reason.
refresh_heartbeat() {
  if REFRESHED="$(jq --arg seen "$(now)" --arg updater_version "${UPDATER_VERSION:-}" \
       --arg updater_digest "${UPDATER_DIGEST:-}" \
       'if (type == "object" and has("phase"))
        then .updater_seen_at = $seen
             | .updater_version = (if $updater_version == "" then null else $updater_version end)
             | .updater_digest = (if $updater_digest == "" then null else $updater_digest end)
        else empty end' \
       "$STATE" 2>/dev/null)" \
    && [ -n "$REFRESHED" ]; then
    write_state "$REFRESHED"
    return $?
  fi
  return 1
}

# entrypoint.sh forwards SIGTERM to exactly this process - both on
# `docker stop` and when its own 600s parent `timeout` fires - and until
# now nothing here ever trapped it. Proven end to end: sending SIGTERM
# while curl still answered unhealthy during the ROLLBACK's own health
# wait left state.json exactly as the last `set_state rollback ""` had
# written it - phase "rollback", "healthy": true (set_state's own
# DEFAULT, never a measurement - see the comment above the rollback
# section further down), and no LETZTER-FEHLSCHLAG.txt at all. A web UI
# reading that state would render a *completed, healthy* rollback that
# never actually finished, and the one artefact meant for "the web UI is
# unreachable" would not exist. This composes with the request-marker
# guard below, too: a "rollback" phase that never resolves is exactly
# what an operator facing it would try to fix by deleting or repairing
# state.json - which self-heals to a fresh idle id (see the heartbeat
# block above) and would otherwise let the very same failed job replay.
#
# Reads id/from/to back OUT of $STATE itself rather than off this
# script's own JOB_ID/FROM/TO variables: those are unset for a signal
# arriving before a request has even been read (nothing to report beyond
# "idle" then, which the trap does not bother improving on), and the
# values $STATE already holds from the last real `set_state` call are the
# most honest account of what this pass was doing at the moment it was
# cut off - not a reconstruction from variables that may lag behind it.
#
# Every step from here on is best-effort (the trap disables itself
# first): a handler that could itself be killed by `set -eu` would leave
# the process to whatever default disposition remains - silence again,
# exactly what this exists to prevent.
on_signal() {
  # A second SIGTERM (an impatient `docker stop`, or the runtime
  # escalating while this handler is still mid-write) must not re-enter
  # this function while state.json or the failure file are half-written.
  trap '' TERM INT HUP

  sig_phase="$(jq -r '.phase // "unknown"' "$STATE" 2>/dev/null || echo unknown)"

  # The missing invariant this guard restores: once a pass has reached a
  # terminal outcome, a signal arriving afterwards cannot change what
  # happened. First proven end to end from a real update on the
  # maintainer's Pi, from a cause that no longer exists in this file: the
  # success path writes `set_state "done" ""`, and this sidecar used to
  # let a self-replacement step right after it (`compose pull
  # loxmatter-updater` / `compose up -d --no-deps loxmatter-updater`)
  # recreate its own container - which is what SIGTERM'd it one moment
  # after `done` was written. This trap, unconditionally at the time,
  # wrote `set_state failed "interrupted by a signal ..."` straight over
  # the "done" it had itself recorded a moment earlier, and the block
  # below wrote LETZTER-FEHLSCHLAG.txt beside a successful update. The
  # self-replacement step itself was later removed outright - see its own
  # comment, near the end of the success branch further down, for why
  # (measured on this same Pi: it cannot correctly replace the process
  # running it) - but the guard below stays. A `docker stop` or
  # entrypoint.sh's own 600-second worker timeout can still land in the
  # narrow window between `done` being written and this pass's own `exit
  # 0` for entirely ordinary reasons (an operator stopping the stack, a
  # host shutdown), and the same corruption - a genuinely finished update
  # reported as failed - would follow just as surely without this case
  # arm.
  #
  # `done`, `failed` and `rejected` are the three terminal phases -
  # mirrored from `_TERMINAL_PHASES` in `src/loxmatter/update.py`, which
  # is this pair's authoritative list (that module's own docstring names
  # the same three as "an end state"); `tests/test_updater_script.py`
  # checks this exact case arm against that constant so the two files
  # cannot silently drift apart. `idle` is deliberately NOT included: it
  # is the resting default before any request exists, not the outcome of
  # a finished pass, so a signal arriving while idle is still recorded
  # exactly as it always was - there is nothing finished there to
  # protect. A signal in any RUNNING phase (`queued`, `backup`, `pull`,
  # `recreate`, `health`, `rollback`) also falls through unchanged below -
  # that is what this trap exists for, and a rollback interrupted midway
  # is precisely the case it must keep reporting (see the block comment
  # above this function for that exact scenario, proven end to end).
  case "$sig_phase" in
    done|failed|rejected)
      log "signal received while phase was already terminal ('$sig_phase') - the pass had already finished; leaving state.json and any failure report exactly as they are"
      exit 143
      ;;
  esac

  JOB_ID="$(jq -r '.id // empty' "$STATE" 2>/dev/null || true)"
  FROM="$(jq -r '.from // empty' "$STATE" 2>/dev/null || true)"
  TO="$(jq -r '.to // empty' "$STATE" 2>/dev/null || true)"
  # Preserved the same way FROM/TO are, above: a signal landing during the
  # ROLLBACK's own health wait must not lose the $BACK value the earlier
  # `set_state rollback ""` call in this same pass already wrote - reading
  # it back out of $STATE, not off this pass's own variable, is what
  # makes that true regardless of where in the pass the signal arrives.
  ROLLED_BACK_TO="$(jq -r '.rolled_back_to // empty' "$STATE" 2>/dev/null || true)"
  ROLLED="${ROLLED:-false}"
  # Never true here: whatever the health endpoint's real state is right
  # now was, by definition, never measured after this signal arrived.
  HEALTHY=false

  set_state failed "interrupted by a signal while in phase '$sig_phase' - entrypoint.sh forwards SIGTERM here from \`docker stop\` and from its own 600s worker timeout; the fields above are the last ones this pass actually wrote, not a measurement of what is running now" \
    || true

  {
    printf 'loxmatter - last failed update attempt\n\n'
    printf 'Time:            %s\n' "$(now)"
    printf 'Attempted:       %s -> %s\n' "$FROM" "$TO"
    printf 'Interrupted in:  phase "%s" (a signal arrived before this pass could finish)\n' "$sig_phase"
    printf 'Healthy again:   UNKNOWN - interrupted before this could be measured\n\n'
    printf 'This run was killed by a signal - SIGTERM forwarded by entrypoint.sh, from\n'
    printf 'either "docker stop" or its own 600-second worker timeout - before it reached\n'
    printf 'a definite outcome. If phase above was "rollback", the service may currently\n'
    printf 'be on neither the old nor the new version. Check by hand:\n'
    printf '  cd %s && docker compose logs --tail 100 %s\n' "$STACK" "$SERVICE"
    printf '  cd %s && ./scripts/update.sh --no-pull\n\n' "$REPO"
    printf 'Last lines of the log:\n'
    tail -n 40 "$LOG" 2>/dev/null || true
  } > "$FAILURE" 2>/dev/null || true

  exit 143
}
trap on_signal TERM INT HUP

# Defined here, ahead of the heartbeat and request-reading sections below,
# so it is available to the "request is not readable at all" branch there
# too - rejecting is needed the moment a request is found to be bad, not
# only once validation proper starts.
#
# State first, THEN log - not the other way round. reject() used to log
# before writing the state; under `set -eu` a failing `log` (LOG unwritable,
# disk full) killed the script right there, before `set_state rejected` ran.
# The state stayed whatever it was before (e.g. "idle"), so the bridge saw
# no rejection, and request.json - never consumed - sat there to be picked
# up and rejected the exact same broken way on every following pass,
# forever. Recording the rejection is the part that must not be skippable;
# the log line is secondary (and, per log()'s own comment above, can no
# longer abort the script anyway - this reordering is belt and suspenders).
reject() {
  set_state rejected "$1"
  log "Request ${JOB_ID:-?} rejected: $1"
  exit 0
}

# ------------------------------------------------------------ heartbeat --
# First, before anything else: the bridge hides the update button when
# this timestamp goes stale (see update.py). A sidecar that only gives a
# heartbeat after finishing its work would look absent during every bit
# of that work - which is exactly why `refresh_heartbeat` (defined above,
# next to `set_state`) exists as its own function now: this top-of-pass
# call is only ONE of its callers since Important 1, the other two being
# `wait_healthy` and the image-pull wrapper further down, both of which
# need the identical "touch only the timestamp" write mid-phase.
#
# `refresh_heartbeat` can fail in two ways this block still has to
# recover from itself (it does not, on its own):
#   * `jq` on an existing state.json can fail outright (corrupt file,
#     truncated by an unrelated crash, hand edited) - `refresh_heartbeat`
#     returns non-zero rather than persist an empty string (see its own
#     comment for the history of that failure mode).
#   * state.json containing the literal `null`, or being a directory
#     (`[ -f "$STATE" ]` below is false for that case, see write_state's
#     own guard for what a write onto one used to do): also handled by
#     `refresh_heartbeat`'s own "must be an object with phase" check.
# Either way, ONLY here - not from any mid-phase caller, which must never
# silently reset a running job back to "idle" - falling through to a
# fresh idle state is the right recovery.
if [ -f "$STATE" ] && refresh_heartbeat; then
  :
else
  JOB_ID="" FROM="" TO="" set_state idle ""
fi

[ -f "$REQUEST" ] || exit 0

# A request.json that isn't valid JSON, is a top-level array (`.id` on an
# array is a jq type error), or carries no id is indistinguishable here
# from "no request" unless checked for explicitly - jq's failure was being
# swallowed by `|| true` and an empty JOB_ID then took the same silent
# `exit 0` as "nothing to do". The bridge, which is polling this file
# waiting for a phase to change, would wait forever for an answer that
# was never going to come. Reject instead: one branch, and the requester
# finds out.
#
# UNREADABLE is recorded rather than rejecting immediately, so the dedup
# guard below gets a chance to run first. Without this, an unreadable
# request.json that nothing ever replaces (a crashed writer, say) forced
# JOB_ID="" on every single pass - and "" can never equal a non-empty
# LAST, so the guard could never match its own previous rejection. The
# result was a full state.json rewrite and a full 2000-line log.txt
# rewrite every two seconds, forever (~43000 of each per day on the Pi
# this runs on). A synthetic id derived from the request file's own mtime
# and size stands in for the missing real one instead: the same
# unreadable file produces the same synthetic id on every pass, so the
# guard bites on the second pass exactly as it already does for a
# readable request.
UNREADABLE=0
if ! JOB_ID="$(jq -r '.id // empty' "$REQUEST" 2>/dev/null)" || [ -z "$JOB_ID" ]; then
  UNREADABLE=1
  SYNTH_MTIME="$(date -r "$REQUEST" -u +%Y%m%d%H%M%S 2>/dev/null || true)"
  SYNTH_SIZE="$(wc -c < "$REQUEST" 2>/dev/null | tr -d ' ' || true)"
  JOB_ID="unreadable-${SYNTH_MTIME}-${SYNTH_SIZE}"
fi
CHANNEL="$(jq -r '.channel // empty' "$REQUEST" 2>/dev/null || true)"
TARGET="$(jq -r '.target // empty' "$REQUEST" 2>/dev/null || true)"

# Exactly once. Without this the sidecar would work through the same
# request again every two seconds - and an update that restarts itself
# never comes to rest.
#
# $LAST alone used to be the WHOLE guard, and that rests entirely on
# state.json's own integrity - the heartbeat block above self-heals a
# corrupt or `null` state.json back to a fresh idle state with id null
# (by design: an unreadable state must not wedge the heartbeat forever).
# Proven end to end against the unpatched guard: complete a rollback (two
# recreates), truncate state.json to "garbage{", run one more pass - the
# WHOLE failed update replayed, another backup, another pull, two MORE
# recreates, four for one request. This is worse than a mere duplicate
# log line: it composes with a stuck "rollback" phase (see the SIGTERM
# trap above) exactly the way an operator would trigger it - by deleting
# or repairing a state.json that looks broken.
#
# $HANDLED_MARKER is the independent half: a plain, empty file created
# the moment a request is ACCEPTED (see `set_state queued ""` below), not
# once it finishes - so even a request whose processing is later
# interrupted (the SIGTERM trap above) or whose state.json is later reset
# stays marked done. Deliberately a file per job id, not a rewrite of
# state.json's own id field: state.json is the ONE thing this file's own
# self-healing doctrine says must recover from corruption; a guard that
# depends on the very field that doctrine resets could not be
# "independent of the state file's integrity" at all.
#
# Guarded by the same character-class $JOB_ID must already pass to be
# ACCEPTED (see Rule 0 below) before it is ever used as a filename here -
# at this point in the script JOB_ID has NOT been validated yet (an
# unreadable or malformed id reaches this line too), and building a path
# from an unvalidated value would reopen a path-traversal question this
# file has spent the sections below closing for every other purpose.
# Skipping the marker check for anything that fails the class simply
# falls through to $LAST - correct, since a request that was never
# actually accepted (Rule 0/1/2 reject it further down) can never have
# earned a marker in the first place.
#
# "." and ".." are excluded here too, defensively, even though Rule 0
# below now also rejects both outright as an id (see its own comment) -
# this check runs BEFORE Rule 0 has had a chance to, on the same raw,
# not-yet-validated $JOB_ID an unreadable or malformed request carries.
# "$UPDATE_DIR/handled/." names the handled/ directory ITSELF, and
# "$UPDATE_DIR/handled/.." names $UPDATE_DIR - both always exist, so
# `[ -e "$HANDLED_MARKER" ]` for either would ALWAYS be true, and a
# request with id "." or ".." would be silently treated as
# already-handled forever, never even reaching Rule 0's rejection below -
# exactly the "request is not readable" doctrine above exists to prevent
# for other malformed shapes (a rejection must be recorded, not silently
# skipped). Excluding both here, too, is what lets Rule 0 actually see
# and reject such a request instead of it being swallowed a step earlier.
HANDLED_MARKER=""
case "$JOB_ID" in
  .|..|*[!A-Za-z0-9._-]*) ;;
  *) HANDLED_MARKER="$UPDATE_DIR/handled/$JOB_ID" ;;
esac
LAST="$(jq -r '.id // empty' "$STATE" 2>/dev/null || true)"
if [ "$JOB_ID" = "$LAST" ] || { [ -n "$HANDLED_MARKER" ] && [ -e "$HANDLED_MARKER" ]; }; then
  exit 0
fi

if [ "$UNREADABLE" = 1 ]; then
  FROM="" TO="" reject "request is not readable (invalid JSON, a top-level array, or a missing/empty id)"
fi

# Rule 0: id is bounded in length here - before JOB_ID is ever used in a
# jq --arg position (set_state, above), and before FROM/TO are assigned
# from a possibly-oversized TARGET (Rule 2, just below, applies the
# identical reasoning there). By this point UNREADABLE is guaranteed 0 -
# the branch above already exited otherwise - so JOB_ID here is exactly
# the raw `.id` from the request.
#
# An id long enough blows jq's own execve inside set_state (E2BIG),
# exactly as an oversized TARGET does (see Rule 2's write-up) - set_state's
# substitution comes back empty, and without set_state's own guard (above)
# that used to get persisted as the new state.json, heartbeat gone for
# good. 128 chars is generous for any id this bridge actually generates
# (timestamp/uuid-shaped, comfortably under 40) and, like TARGET's bound
# below, several orders of magnitude under the ~131072-byte Linux
# MAX_ARG_STRLEN this defends against.
#
# JOB_ID is forced back to "" before rejecting, the same as the
# UNREADABLE branch above - which reopens that branch's own dedup gap for
# a request whose id specifically fails this check (a resubmission of the
# same oversized id is re-rejected every pass rather than deduped). Left
# as-is: unlike an unreadable file, this needs an attacker to keep
# resubmitting a deliberately oversized id on purpose, a narrower and
# self-inflicted version of the problem the synthetic id above actually
# fixes.
if [ "${#JOB_ID}" -gt 128 ]; then
  JOB_ID="" FROM="" TO="" reject "id is too long"
fi

# id's character set, same reasoning: unrestricted, an id containing a
# newline forges an arbitrary extra line in log.txt. state.json itself
# stays safe regardless (jq escapes --arg), but the log is the only
# forensic record of what the sidecar was ever asked to do, and log()
# interpolates the id verbatim. Verified end to end: id =
# "a1\n<forged 'accepted' line>" produced a syntactically perfect forged
# log entry, and the request still reached phase: queued. Restricted to
# [A-Za-z0-9._-] via a `case` glob - not `grep`, which matches per LINE
# not per VALUE (see the TARGET newline comment further down for the full
# mechanism) - blocks the newline and every other injection vector in the
# same motion, since `case` matches the WHOLE word including newlines.
#
# "." and ".." are rejected explicitly, ON TOP OF the class: both are
# made up entirely of "." and "-" - characters the class above already
# allows - so the class alone would accept them. id is now also used as a
# PATH SEGMENT (the "handled/<job-id>" marker further up, this task's own
# Important 6 fix), and "$UPDATE_DIR/handled/." names the handled/
# directory ITSELF while "$UPDATE_DIR/handled/.." names $UPDATE_DIR - a
# marker write onto either is `: > /some/directory`, which fails EISDIR
# and would take the whole script down under `set -eu` the moment an id
# of exactly "." or ".." was ever accepted.
case "$JOB_ID" in
  .|..)
    JOB_ID="" FROM="" TO="" reject "id must not be \".\" or \"..\"" ;;
  *[!A-Za-z0-9._-]*)
    JOB_ID="" FROM="" TO="" reject "id contains an invalid character" ;;
esac

# Rule 2: target is bounded in length before it can reach ANYTHING further
# down - including FROM/TO, assigned right after this check, and every
# reject() from here on that reports on a bad TARGET (unknown channel,
# embedded newline, failed pattern). All of those call set_state, which
# puts TO into a jq --arg position; TO is set from TARGET unconditionally,
# whether the request is ultimately accepted OR rejected. A target long
# enough blows jq's own execve inside set_state exactly like an oversized
# id does (see Rule 0 above) - reproduced end to end: a 523196-character
# numeric-looking target ("0.3." followed by a run of zeros, which still
# matches the pattern check further down) left three consecutive passes
# each at rc=0 with a 1-byte state.json, at a measured threshold of 522996
# characters on this machine. The Linux MAX_ARG_STRLEN bounding the Alpine
# sidecar is smaller still (32 pages, ~131072 bytes), so roughly 128 KiB
# suffices there. 128 characters - a real Docker tag can never legitimately
# be longer - rejects it three orders of magnitude before either threshold,
# regardless of what channel or pattern check would otherwise apply to it.
if [ "${#TARGET}" -gt 128 ]; then
  reject "target is too long"
fi

FROM="$(current_tag)"
# GIT_BEFORE (the commit Task 4's rollback checks back out to) is
# computed further down, past validation - see the comment there. It is
# not needed by anything between here and there, and reject()'s own
# doctrine (state before log, precisely so recording a rejection is not
# itself an action - see reject()'s comment above) is why a REJECTED
# request must never reach a `git` call at all, not even a read-only one.
TO="$TARGET"
ROLLED=false
HEALTHY=true
# Only ever given a real value in the rollback section further down, once
# $BACK is known - explicitly empty here (rather than left unset) so a
# request that never reaches a rollback at all writes an explicit "no
# rollback target" into state.json, the same "empty means null" reading
# FROM/TO/ROLLED already get, not a leftover from some earlier pass (each
# invocation of this script is a fresh process - see the top-of-file
# comment - so nothing actually leaks between passes either way; this is
# about a reader of this section understanding that without having to
# know that fact).
ROLLED_BACK_TO=""

# --------------------------------------------------------------- validation --
# Rule 1: channel is an enum, target must satisfy a pattern.
case "$CHANNEL" in
  stable|dev) ;;
  *) reject "unknown channel" ;;
esac

# `grep -Eq '^...$'` further down matches per LINE, not per VALUE: grep
# considers a multi-line subject a match as soon as ANY one of its lines
# satisfies the anchored pattern, and `jq -r` turns a JSON string's `\n`
# escapes into real newlines. So a target like "0.3.0\nrm -rf /" passes
# the pattern check on its first line and smuggles the rest straight
# through - verified end to end: it reaches `set_state queued`, and from
# there Tasks 3/4 write it into $STACK/.env as LOXMATTER_IMAGE_TAG, which
# deploy/testhost/docker-compose.yml interpolates into `image:` of a
# `privileged: true` service. A multiline value is an injected extra
# KEY=VALUE line in that file, not just a bad tag. `sort -V` doesn't catch
# it either - it just sees the payload as extra lines and still reports
# the running version as the lowest.
#
# Reject any embedded newline before the pattern is even tried, so the
# anchors below only ever see a single line. Do NOT "simplify" this back
# to a bare `grep -Eq '^...$'` - that is exactly the check already shown
# to be bypassable.
case "$TARGET" in
  *"$NEWLINE"*) reject "target contains a newline" ;;
esac

case "$CHANNEL" in
  stable)
    printf '%s' "$TARGET" | grep -Eq '^v?[0-9]+\.[0-9]+\.[0-9]+$' \
      || reject "not a valid version target" ;;
  dev)
    printf '%s' "$TARGET" | grep -Eq '^[0-9a-f]{7,40}$' \
      || reject "not a valid commit target" ;;
esac

# The pattern above accepts an optional leading "v" for the stable channel
# but treats it as equivalent to the bare number. Collapse it once, right
# here, so every consumer from this point on - the CUR/NEW comparison
# just below, and Task 3, which will use $TO verbatim as an image tag
# written into .env - sees exactly one spelling instead of each having to
# strip "v" itself.
if [ "$CHANNEL" = "stable" ]; then
  TARGET="${TARGET#v}"
  TO="$TARGET"
fi

# What is running right now. Needed for the stable channel's forward-only
# check just below, AND - regardless of channel - as the honest rollback
# target if this update fails later: a rollback must write back the
# concrete version that was actually running, never the possibly-aliased
# $FROM (see running_version()'s own comment, and Task 4's rollback
# section at the end of this file). Computing it once, here, unconditional
# on channel, is what makes it available to a failed dev-channel update
# too - it used to be read only inside the stable branch below, which left
# a dev-channel rollback referencing an unset $RUNNING under `set -eu`.
#
# Fetched here, deliberately AFTER the channel/pattern/newline checks
# above and not before: a malformed or malicious request never earns a
# docker call at all, and only a request whose SHAPE has already been
# accepted triggers the one read-only `docker inspect` needed to answer
# "what is running right now" - the dev channel's own remaining check
# (ancestry, once the target has been fetched) still runs later and can
# still reject, but by then this call has cost nothing extra: it is a
# read, not a mutation.
RUNNING="$(running_version)"

# Rule 3: forward only. In the stable channel by semantic version;
# `sort -V` from coreutils, busybox's sort cannot do that reliably. The
# dev channel has no ordering over SHAs - there Task 3 instead checks
# ancestry, once the refs have been fetched.
if [ "$CHANNEL" = "stable" ]; then
  # Compared against the RUNNING version, not against the tag in the
  # .env - see running_version() above. A tag can be named "stable" and
  # thereby not be a version at all.
  CUR="${RUNNING#v}"
  # TARGET was already normalised (leading "v" stripped) right above.
  NEW="$TARGET"
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

# Written the moment acceptance is final, before any risky work starts -
# see the guard above for why this has to happen at acceptance and not at
# completion. $HANDLED_MARKER is guaranteed non-empty here: JOB_ID has
# just passed Rule 0's identical character-class check above (further
# down in the script text, but already executed by the time control
# reaches this line), so it always matched the "safe filename" case above.
: > "$HANDLED_MARKER"

# A stale LETZTER-FEHLSCHLAG.txt from an EARLIER, unrelated failed
# request must not go on describing itself as "the" last failed attempt
# once a new one has been accepted - proven end to end: complete a
# rollback, then let a different request fail at `git fetch` (a stage
# that never touches this file at all) and the OLD rollback's versions
# and timestamp are still what an operator reads. The success path
# further down already removes this file on a clean finish; clearing it
# here as well means a request that fails BEFORE ever reaching the
# rollback section leaves no failure file at all, which is the honest
# answer - the plain-text file exists to narrate a rollback, not every
# possible failure (those are already in state.json's own "error" field).
rm -f "$FAILURE"

# ------------------------------------------------------------------- flow --
# Runs the accepted request through to completion in this same pass - there
# is no separate hand-off. entrypoint.sh's 600s worker timeout already
# accounts for two runs through the waiting parts of this (backup, pull,
# recreate, up to two 120s health waits) in case Task 4's rollback has to
# redo the pull/recreate once, backward.

run() {
  log "\$ $*"
  "$@" >> "$LOG" 2>&1
}

# Compose resolves every RELATIVE `volumes:` entry in docker-compose.yml
# against its own project directory (the -f file's own directory, or an
# explicit --project-directory) and hands the DAEMON whatever that
# resolves to - the daemon then mounts that path from ITS OWN filesystem,
# i.e. the HOST's, regardless of which filesystem the process invoking
# `docker compose` happens to be running on. Two entries in
# deploy/testhost/docker-compose.yml are relative: `./data:/matter-data:ro`
# on `loxmatter`, and `../..:/repo` on `loxmatter-updater` itself.
#
# An earlier version of this function `cd`d into $STACK and ran `docker
# compose` with no project-directory flag at all - which resolves those
# two entries against $STACK, and $STACK ($LOXMATTER_STACK,
# /repo/deploy/testhost by default) is a CONTAINER path: a directory that
# exists inside THIS sidecar (bind-mounted from the host's checkout) but
# not, under that exact name, on the host the daemon actually runs on.
# On the first update this ever ran: the `loxmatter` recreate resolved
# `./data` to a host path that does not exist, and the daemon silently
# created it EMPTY rather than erroring - the fabric backup route (`GET
# /api/diagnostics/fabric-backup`, which reads through that exact mount)
# went dead from that point on, with nothing in this script or state.json
# ever indicating it. The self-replacement step this sidecar carried at
# the time (since removed outright - see its own comment near the end of
# the success branch further down, for why) resolved `../..` the same way
# - an empty host `/repo` - so every update AFTER the first one failed at
# `git fetch` ("$STACK is not a directory", from a checkout that was
# never actually there). That self-replacement's own "only recreate when
# the image changed" reasoning was ALSO silently false the whole time:
# the WRONG mount source it resolved differs from the correct one on
# every single run, so Compose's
# own config-hash comparison never agreed with itself between runs and it
# recreated unconditionally regardless of whether the image had actually
# moved.
#
# `--project-directory` is the flag Compose actually resolves relative
# paths against, independent of `-f` (which only says where to READ the
# file). `-f "$STACK/docker-compose.yml"` keeps the CONTAINER path - this
# process can only ever see its own filesystem, and that path correctly
# reaches the (bind-mounted, so identical) file either way.
# `--project-directory` gets the HOST path instead, resolved through
# host_path_for() - the same function write_failure_file() below already
# uses to turn a container path back into something an operator can `cd`
# into, because the docker daemon behind this socket is the one party
# that actually knows what is mounted where.
#
# Resolved (and refused, see below) only ONCE per pass, on this
# function's first call - not once per invocation. There can be several
# in one pass (pull, the initial recreate, a rollback's own recreate),
# the mounts underneath this sidecar do not
# change mid-pass, and re-querying the daemon for the identical answer
# every time would be a socket round trip this sidecar does not need -
# it would also turn a single unresolved-mount problem into a separate
# log line, and a separate `docker inspect loxmatter-updater` call, for
# every compose() call in the pass instead of exactly one.
#
# A $STACK that host_path_for() cannot resolve at ALL (no mount whose
# Destination is a path-segment prefix of it - the daemon unreachable, or
# this sidecar's own mount table not shaped the way it expects) must NOT
# fall back to the container path - that is the exact bug this function
# exists to fix, just reached by a different route, and this time
# silently. Refuse instead: a compose call that never ran is retryable on
# the very next request; one that silently recreated a service against
# the wrong host directory is not.
# Split out of compose() itself (below) so
# compose_pull_with_heartbeat() further down can resolve and memoize
# $COMPOSE_PROJECT_DIR HERE, synchronously in THIS process, before
# forking the actual pull into a background subshell. A subshell (what
# `&` always creates) that resolved this for the first time would only
# ever memoize it for ITSELF - every LATER compose() call in this same
# pass (the recreate right after the pull returns; a rollback's own
# recreate, should the first one fail) runs back in THIS process, not
# that subshell,
# and would silently pay for a second `docker inspect loxmatter-updater`
# round trip the memoization above exists specifically to avoid (see its
# own comment) - and, had $STACK been unresolvable, log the "could not
# resolve" line a second time too.
#
# No-op if $STACK is not a directory: compose() below still runs its own
# identical check before ever doing real work, on every call including a
# backgrounded one; this mirror only exists to skip the (otherwise
# pointless) `docker inspect` when called from
# compose_pull_with_heartbeat() ahead of that check ever running.
resolve_compose_project_dir() {
  [ -d "$STACK" ] || return 0
  if [ -z "${COMPOSE_PROJECT_DIR_RESOLVED:-}" ]; then
    COMPOSE_PROJECT_DIR="$(host_path_for "$STACK" "")"
    COMPOSE_PROJECT_DIR_RESOLVED=1
  fi
}

compose() {
  if [ ! -d "$STACK" ]; then
    log "compose: $STACK is not a directory - cannot run docker compose there"
    return 1
  fi
  resolve_compose_project_dir
  if [ -z "$COMPOSE_PROJECT_DIR" ]; then
    log "compose: could not resolve $STACK to a host path (docker inspect loxmatter-updater found no mount whose Destination is a prefix of it) - refusing to run docker compose, since a relative volumes: entry would otherwise resolve against this container's own filesystem instead of the host's"
    return 1
  fi
  log "\$ docker compose -f $STACK/docker-compose.yml --project-directory $COMPOSE_PROJECT_DIR $*"
  docker compose -f "$STACK/docker-compose.yml" --project-directory "$COMPOSE_PROJECT_DIR" "$@" >> "$LOG" 2>&1
}

# Important 1's other half: `compose pull` (the actual image download,
# now `set_state pull` below) is a SINGLE blocking call with no loop of
# its own to hang a heartbeat refresh off of, unlike `wait_healthy`
# above. This script is invoked once per entrypoint.sh tick and has no
# background process outside itself already running - so the refresh has
# to come from a helper that backgrounds the pull and watches it from
# THIS SAME PASS, not from entrypoint.sh: a heartbeat kept alive by a
# process OTHER than the one actually doing the work would refresh it
# even after this whole script died (OOM-killed, the container gone),
# which is precisely the case `_MAX_SILENT_SECONDS` (update.py) exists to
# catch. Refreshing only for as long as the CHILD THIS FUNCTION SPAWNED
# is still alive keeps that guarantee: the heartbeat goes silent the
# instant nothing is left running it.
#
# Cost, stated plainly: a pull that is not crashed but genuinely wedged
# (a connection stuck half-open with docker itself never timing out on
# it) keeps its child process alive and therefore keeps refreshing the
# heartbeat too - alive-but-stuck and alive-and-working look the same to
# this mechanism, exactly the "would a heartbeat that advances while a
# step is wedged defeat the stalled detection" trade-off the brief for
# this fix calls out by name. Accepted here: the alternative - never
# refreshing during a pull - reports a false crash on most SUCCESSFUL
# updates long enough to cross _MAX_SILENT_SECONDS, which per the design
# brief is the normal case on a Pi pulling an arm64 image; a pull wedged
# with no timeout at all is both rarer and still bounded by
# entrypoint.sh's own 600-second worker timeout, which SIGTERMs this
# whole script (and with it, the backgrounded pull) regardless of what
# the heartbeat says.
#
# Polls once a SECOND, the same granularity `wait_healthy` already uses
# above, not once every several seconds - `sleep N` inside a `while kill
# -0 ...; do sleep N; ...; done` loop always sleeps the FULL N before its
# very first liveness check, even when the child is done before that
# check ever runs. Measured with a 5-second poll during this fix's own
# development: an already-instant pull (this file's own test fixtures,
# where the pulled "image" is a fake binary that returns immediately)
# still cost a flat 5 extra wall-clock seconds on every single run
# through this function, real work or none - multiplied across a test
# suite already documented (see `project_testsuite_dauer` in this
# maintainer's own notes) as long enough to look like a hang. A
# one-second poll bounds that same unavoidable "sleep before the first
# check" tax to at most one second instead.
compose_pull_with_heartbeat() {
  resolve_compose_project_dir
  compose pull "$1" &
  pull_pid=$!
  while kill -0 "$pull_pid" 2>/dev/null; do
    sleep 1
    if kill -0 "$pull_pid" 2>/dev/null; then
      refresh_heartbeat || true
    fi
  done
  pull_rc=0
  wait "$pull_pid" || pull_rc=$?
  return "$pull_rc"
}

# Escapes sed's own replacement metacharacters - backslash, ampersand, and
# the `|` this substitution uses as its delimiter - out of a value before
# it reaches sed. Verified end to end: `set_tag 'a|b'` unescaped makes sed
# itself fail ("bad flag in substitute command", because the bare `|`
# closes the substitution early) and leaves a 0-byte $ENV_FILE.tmp behind.
# $1 here is not always the bounded, pattern-checked TARGET (Rule 2 above
# already limits that to characters a Docker tag can contain) - set_tag is
# also called with $FROM, read back out of a hand-editable .env with no
# such bound.
sed_escape_replacement() {
  printf '%s' "$1" | sed 's/[\\&|]/\\&/g'
}

# Replaces EXACTLY the LOXMATTER_IMAGE_TAG= line and leaves the rest of
# the .env untouched. It also carries MINISERVER_IP, RADIO_DEVICE and the
# API token - an update that rewrites the whole file takes half the
# installation down with it.
#
# Goes through a temp file and `mv`, the same atomic-write idiom
# write_state() already uses above, rather than `sed -i`: GNU sed's `-i`
# takes an optional attached suffix, but BSD/macOS sed's `-i` requires one
# as a SEPARATE argument - `sed -i -E '...'` on BSD sed reads "-E" as that
# argument (a literal backup-file suffix) and runs the script that follows
# as a basic, not extended, regular expression. Verified end to end on
# this machine: it happened to still produce the right substitution (the
# pattern below uses no ERE-only syntax, so BRE and ERE agree on it) but
# silently left a stray ".env-E" backup file behind on every call - exactly
# the kind of accidental success this project has been bitten by more than
# once. Sidestepped entirely rather than patched per-platform: neither sed
# needs `-E` here, and neither needs `-i` once the substitution is piped
# through a temp file instead.
set_tag() {
  # A symlinked .env - a shared config kept outside the repository
  # checkout, say - has to stay a symlink. `mv` onto a path replaces
  # whatever sits there (a plain rename(2), which does not follow a
  # destination symlink) rather than writing through it, so finishing
  # this function with an unconditional `mv ... "$ENV_FILE"` would
  # silently turn a symlinked .env into a plain file the moment an
  # update first ran - measured on the unpatched function. Resolve to
  # the real target once, up front, and do the temp-file dance against
  # THAT path instead: the symlink itself is then never touched, only
  # the file it points at.
  set_tag_target="$ENV_FILE"
  if [ -L "$set_tag_target" ]; then
    set_tag_resolved="$(readlink -f "$set_tag_target" 2>/dev/null || true)"
    [ -n "$set_tag_resolved" ] && set_tag_target="$set_tag_resolved"
  fi

  if [ ! -e "$set_tag_target" ]; then
    printf 'LOXMATTER_IMAGE_TAG=%s\n' "$1" > "$set_tag_target"
    return 0
  fi

  # `cp -p` before editing, not just relying on the closing `mv`: the
  # temp file below starts life as a brand-new inode, and neither the
  # redirection that fills it nor `mv` retroactively gives it back the
  # original's mode or ownership. Measured on the unpatched function:
  # 0600 -> 0644, 0444 -> 0644. `cp -p` onto the temp file first carries
  # the original's mode, ownership (where permitted) and timestamps onto
  # it; the redirection below then only overwrites that same temp file's
  # CONTENT, and the closing `mv` (a rename, same filesystem) keeps the
  # mode it already has.
  cp -p "$set_tag_target" "$set_tag_target.tmp"

  if grep -q '^LOXMATTER_IMAGE_TAG=' "$set_tag_target" 2>/dev/null; then
    set_tag_replacement="$(sed_escape_replacement "$1")"
    sed "s|^LOXMATTER_IMAGE_TAG=.*|LOXMATTER_IMAGE_TAG=$set_tag_replacement|" "$set_tag_target" \
      > "$set_tag_target.tmp"
  else
    # A hand-edited or hand-migrated .env commonly has no trailing
    # newline - a plain `printf` without one, or `$(...)` command
    # substitution (which strips trailing newlines), both leave it that
    # way. Appending straight onto that merges the new
    # "LOXMATTER_IMAGE_TAG=..." line onto the END of the file's last
    # existing line instead of starting one of its own. Proven end to
    # end: with .env ending "...LOXMATTER_API_TOKEN=deadbeefcafe" and no
    # trailing newline, the unpatched function produced the single
    # corrupted line
    # "LOXMATTER_API_TOKEN=deadbeefcafeLOXMATTER_IMAGE_TAG=0.3.0" - the
    # token gone, and no line beginning "LOXMATTER_IMAGE_TAG=" left for
    # docker-compose.yml to find, so it silently fell back to its own
    # `stable` default and the update never took effect, while
    # state.json still reported "done". install.sh's own `env_set`
    # already solves exactly this (see its comment there); mirrored here
    # rather than re-derived. `cp -p` above already copied
    # $set_tag_target's existing content onto $set_tag_target.tmp, so
    # only the missing newline and the new line need appending here.
    if [ -s "$set_tag_target" ] && [ "$(tail -c 1 "$set_tag_target")" != "" ]; then
      printf '\n' >> "$set_tag_target.tmp"
    fi
    printf 'LOXMATTER_IMAGE_TAG=%s\n' "$1" >> "$set_tag_target.tmp"
  fi

  mv "$set_tag_target.tmp" "$set_tag_target"
}

# Waits for the first healthy beat, bounded by WALL-CLOCK seconds, not by
# a count of loop iterations. The old loop counted iterations
# (`i=$((i + 1))`, one per pass) and treated HEALTH_TIMEOUT as an
# iteration budget - but each iteration is `curl -m 3` PLUS `sleep 1`, so
# an iteration only costs one second when curl returns instantly. Measured
# against a curl that consumes its own timeout - a container that binds
# the port and then wedges, or a lost path to host.docker.internal, i.e.
# exactly the failure this window exists to survive: HEALTH_TIMEOUT=5 took
# 20.7 seconds, a 4.1x factor. entrypoint.sh sizes its 600s parent
# `timeout` on "240s of known waiting" for two such waits; at that factor
# the real ceiling is closer to 960s, and when the parent `timeout` fires
# mid-rollback this script has no trap of its own - the host is left on
# the broken image with a frozen phase and a dedup guard that will not
# retouch that job id. Bounding on the clock instead makes the slow case
# cost what it says it costs, exactly once, regardless of how long any
# single curl call takes.
#
# The 120-second production value is unchanged - it is not a new number
# but the one from scripts/update.sh, and the reasoning there still holds:
# 20 seconds went fine for exactly that long, until a run on September 8th
# tipped just past it and reported a service unhealthy that was working
# flawlessly ten seconds later. A window that is too short is the more
# expensive false alarm here - it looks like a broken update and tempts
# one into rolling back a state that is fine.
wait_healthy() {
  wait_healthy_deadline=$(($(date +%s) + HEALTH_TIMEOUT))
  while [ "$(date +%s)" -lt "$wait_healthy_deadline" ]; do
    if curl -fsS -m 3 "$HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    # Important 1: this loop already ticks once a second, up to
    # HEALTH_TIMEOUT (120s in production) - without this call, that
    # entire wait wrote `updater_seen_at` ZERO times, and a health check
    # that legitimately takes even half a minute made the bridge
    # conclude the sidecar had crashed, right as it was working exactly
    # as designed. `refresh_heartbeat` (not `set_state`) specifically:
    # this same function also runs the ROLLBACK's own health wait, at
    # phase "rollback" - a call that touched `phase` here would
    # overwrite that with "health", which the SIGTERM-during-rollback
    # test relies on NOT happening.
    refresh_heartbeat || true
    sleep 1
  done
  return 1
}

# Resolves a path INSIDE this container back to where it lives on the
# DOCKER HOST - the machine an operator's shell actually runs on, not this
# sidecar's own filesystem. Proven end to end why guessing "same path"
# does not work: $STACK/$REPO default to /repo/deploy/testhost and /repo,
# bind-mounted from the host's checkout into this container - /repo does
# not exist on the host at all, so every "cd /repo && ..." line in
# LETZTER-FEHLSCHLAG.txt below used to fail at the `cd`, and the `&&`
# silently swallowed everything after it.
#
# Asks the docker daemon itself, over the socket this sidecar already
# holds, rather than assume any particular layout - it is the one party
# that actually knows what is mounted where. `--format` prints one
# "destination source" line per mount.
#
# The lookup below matches by LONGEST-PREFIX, not by an exact match on
# $1 - a previous version of this function used
# `awk -v dest="$1" '$1 == dest {...}'`, which only ever resolves a path
# that is ITSELF a mount destination. $LOXMATTER_STACK
# (/repo/deploy/testhost by default) is not one - only /repo is actually
# mounted (see docker-compose.yml's `../..:/repo`), and /repo/deploy/testhost
# is a subdirectory of it. Proven end to end against the exact-match
# version: four of the five commands LETZTER-FEHLSCHLAG.txt prints start
# `cd $STACK`, and every one of them fell back to "host path unknown"
# even though the real host path was perfectly knowable - the /repo
# mount's Source plus "/deploy/testhost". Only the fifth command (`cd
# $REPO`, which resolves exactly since /repo IS a mount destination) came
# out runnable. Restoring one out of five is not restoring the artefact
# this file exists to be: something pasteable over SSH when the web UI
# itself is unreachable.
#
# The fix: for each mount, ask whether its Destination is a PATH-SEGMENT
# prefix of $1 (either an exact match, or $1 continues past it with a
# "/"), keep the longest such Destination across all mounts, and append
# whatever of $1 remains after stripping that prefix onto the matching
# mount's Source. Three edge cases this has to get right:
#
#   * "/repo" must not be treated as a prefix of "/repository" - a plain
#     `index($1, dest) == 1` substring test would accept that, silently
#     resolving one mount's path as if it were inside a different one
#     that merely happens to share a longer common spelling. The
#     boundary check requires the character right after the shared
#     prefix to be "/" (or nothing, for an exact match).
#   * When mounts nest (a mount at /repo AND a more specific one at
#     /repo/deploy, say), the LONGEST matching Destination has to win -
#     otherwise a subdirectory that has its own, more specific mount
#     would incorrectly resolve through the outer one instead.
#   * A Destination of "/" (a mount of the whole container root - not
#     something this project's own compose files do, but a general
#     function should not assume its caller's layout) needs no special
#     case at all if the prefix logic above is right: normalising it to
#     the empty string before the "/"-boundary check makes it a prefix of
#     every absolute path, at length 0 - the lowest possible priority, so
#     any more specific mount still wins the longest-match comparison.
#
# Written for POSIX/busybox awk, not GNU awk specifically (the sidecar's
# Alpine base has busybox awk, not gawk) - no gawk-only extensions
# (`gensub`, `length()` on an array, gawk's own multi-char `RS`). Just
# `index`, `substr`, `length`, `sub` and plain scalar bookkeeping, all
# POSIX awk.
#
# Piped through `awk` rather than left bare: a `docker inspect` that
# fails (daemon unreachable, no container named `loxmatter-updater` yet -
# a fresh install.sh run this early in its own bootstrap) must not make
# the ASSIGNMENT this runs inside fail under `set -eu`, the same reasoning
# current_tag()/running_version() already document above for ending a
# substitution on a command that itself always exits 0 - `awk` does, even
# reading nothing at all.
#
# Falls back to $2 when nothing matches - a container path backed by a
# named volume ($BACKUP_DIR, in the caller below) has no host directory
# to report at all, and the caller is expected to reach for `docker exec`
# instead (see the restore command below, which already does exactly
# that, for exactly that reason).
host_path_for() {
  host_path_for_raw="$(docker inspect loxmatter-updater \
      --format '{{range .Mounts}}{{.Destination}} {{.Source}}
{{end}}' 2>/dev/null \
    | awk -v dest="$1" '
        {
          d = $1
          src = $0
          sub(/^[^ ]*/, "", src)
          sub(/^ /, "", src)

          # Normalise a Destination of "/" to the empty string - see the
          # block comment above for why that gives it the lowest possible
          # priority (length 0) instead of a special case.
          dn = d
          if (dn == "/") { dn = "" } else { sub(/\/$/, "", dn) }

          matched = 0
          if (dest == dn) {
            matched = 1
            rest = ""
          } else if (index(dest, dn "/") == 1) {
            matched = 1
            rest = substr(dest, length(dn) + 1)
          }

          if (matched) {
            dlen = length(dn)
            if (!found || dlen > best_len) {
              found = 1
              best_len = dlen
              best_source = src
              best_rest = rest
            }
          }
        }
        END { if (found) print best_source best_rest }
      ')"
  printf '%s' "${host_path_for_raw:-$2}"
}

# If even the rollback did not become healthy, the web UI is probably not
# reachable at all - then this file is the only answer someone finds who
# checks in over SSH after all. It is written on a successful rollback
# too: anyone who wants to know why their version is the old one again
# should be able to read up on it without needing the web UI at all.
#
# Called from BOTH rollback outcomes below, and deliberately BEFORE the
# `set_state failed` call at each of them, not after: `{ ... } >
# "$FAILURE"` used to sit downstream of an unguarded `set_state failed`
# call, so a failing `set_state` (jq's own execve failing under `set -eu`,
# say) killed the whole script right there - the one artefact meant for
# exactly the "state.json is unwritable" world was the one thing that
# then never got written. Calling this first means even a hard stop
# immediately afterward still leaves a readable account behind.
#
# The restore command below reaches into $BACKUP_DIR through
# loxmatter-updater specifically, not loxmatter: by the time an operator
# runs it, loxmatter has just been asked to `stop`, and a stopped
# container cannot be `exec`ed into. loxmatter-updater keeps running
# throughout and shares the same volume at /data (see docker-compose.yml).
# $BACKUP_DIR itself is a NAMED docker volume, not a host bind mount (see
# docker-compose.yml's loxmatter-store) - there is no host path to print
# for it at all, which is why it is named only in the `docker exec`
# command below, never in a bare `cd`.
write_failure_file() {
  # $1: the message this attempt ends on (already what is about to be
  # passed to `set_state failed` right after this call returns).
  host_stack="$(host_path_for "$STACK" \
    "$STACK (host path unknown - run: docker inspect loxmatter-updater --format '{{json .Mounts}}')")"
  host_repo="$(host_path_for "$REPO" \
    "$REPO (host path unknown - run: docker inspect loxmatter-updater --format '{{json .Mounts}}')")"
  {
    printf 'loxmatter - last failed update attempt\n\n'
    printf 'Time:           %s\n' "$(now)"
    printf 'Attempted:      %s -> %s (channel %s)\n' "$FROM" "$TO" "$CHANNEL"
    if [ "$ROLLED" = true ]; then
      printf 'Rolled back to: %s\n' "$BACK"
    else
      printf 'NOT ROLLED BACK - %s\n' "$1"
    fi
    printf 'Healthy again:  %s\n\n' "$([ "$HEALTHY" = true ] && echo yes || echo NO)"
    printf 'Signal database: NOT restored automatically (see model/store.py -\n'
    printf 'an older version starts up fine on a newer schema). A backup taken\n'
    printf 'just before this attempt is available if you need to go further\n'
    printf 'back than the image rollback above went. It lives INSIDE the\n'
    printf 'loxmatter-updater container, in a named Docker volume - not on the\n'
    printf 'host filesystem, so it cannot be "cd"ed to; reach it with "docker\n'
    printf 'exec", exactly as the restore command below already does:\n'
    printf '  %s/store-%s.tgz\n\n' "$BACKUP_DIR" "$STAMP"
    printf 'Manual next steps, run from the HOST (not inside any container):\n'
    printf '  cd %s && docker compose logs --tail 100 %s\n' "$host_stack" "$SERVICE"
    printf '  cd %s && ./scripts/update.sh --no-pull\n\n' "$host_repo"
    printf 'To restore that backup instead (discards every signal learned\n'
    printf 'since it was taken - only if you are sure):\n'
    printf '  cd %s && docker compose stop %s\n' "$host_stack" "$SERVICE"
    printf "  docker exec loxmatter-updater sh -c 'tar xzf %s/store-%s.tgz -C /data'\n" "$BACKUP_DIR" "$STAMP"
    printf '  cd %s && docker compose start %s\n\n' "$host_stack" "$SERVICE"
    printf 'Last lines of the log:\n'
    tail -n 40 "$LOG" 2>/dev/null || true
  } > "$FAILURE"
}

# Captured before ANY checkout happens - Task 3's own checkout (further
# down, of $REF) is the first thing that would move $REPO's working tree.
# Task 4's rollback checks the repository back out to exactly this commit
# again: the Compose file must match the running image (a new release can
# add a service or a variable the old one does not know), so rolling the
# image back without also rolling the checkout back can leave the old
# image started against a Compose file it was never meant to run under.
#
# Placed HERE - after validation (Rule 0-3, above), not before it, unlike
# an earlier version of this file - so a REJECTED request never reaches a
# `git` call at all (see the note left where this used to sit), and so a
# genuine failure here (below) can safely abort the whole pass without
# ever touching reject()'s own "nothing happened yet" guarantee. Still
# strictly before Task 3's own checkout further down, which is the
# property that actually matters.
#
# Falls back to the literal string "HEAD" whenever $REPO does not (yet)
# answer `rev-parse` with something usable - `git checkout --detach HEAD`
# is then a well-defined no-op, the safest thing to attempt when there is
# no real answer to "checked out before". Exactly ONE failure shape gets
# that fallback, though, not two: `git` exiting 0 with EMPTY output - a
# fresh repository with no commits yet, proven against this file's own
# test fixtures, whose default `git` stub does exactly that (unlike a
# real `git rev-parse HEAD`, which never succeeds without printing a
# SHA).
#
# A NON-ZERO exit is a different thing entirely and must NOT share that
# fallback - `... || echo HEAD` used to do exactly that, treating "git
# refused to answer" identically to "git answered honestly that there is
# nothing yet", and swallowing a real, actionable failure (dubious
# ownership - see run_git()'s own comment on that failure mode, now fixed
# at its source, but not something this line should assume can never
# recur; a corrupted .git; the binary missing) into a GIT_BEFORE that
# LOOKS like a normal, if unremarkable, value. A rollback later in this
# SAME pass would then `git checkout --detach HEAD` - a no-op, since HEAD
# already sits on the very ref this pass itself just checked out further
# down - leaving the Compose file at the FAILED version while the image
# rolls back to the old one, exactly the mismatch the paragraph above
# warns against. Aborting here instead means a git that cannot even
# answer "what commit are we on" never reaches that silently wrong
# rollback in the first place.
if git_before_raw="$(git_repo rev-parse HEAD 2>&1)"; then
  GIT_BEFORE="${git_before_raw:-HEAD}"
else
  log "git rev-parse HEAD failed against $REPO: $git_before_raw"
  set_state failed "could not determine the currently checked-out commit ($REPO: git rev-parse HEAD failed) - see log.txt"
  exit 0
fi

# 0. Fetch the target and, for the dev channel, validate ancestry - BEFORE
# the backup below, not after it. The Compose file must match the version
# (a new release can need a new service or a new variable), and the dev
# channel's "forward only" equivalent (there is no ordering over SHAs,
# only ancestry) can only be answered once the candidate commit is
# actually known locally - so the fetch has to come first either way.
#
# This deliberately breaks with a literal top-to-bottom reading of "1.
# Back up. Before anything else": a `git fetch`/`merge-base` cannot touch
# /data/loxmatter.sqlite, so nothing here can endanger the one thing the
# backup exists to protect - but the dev-channel ancestry check calls
# reject(), and reject()'s own doctrine (see its comment above: state
# before log, precisely so recording a rejection is not itself an action)
# is that a rejection which has already done something is not one. A
# backup IS a real, disk-visible action - it prunes old ones and costs
# real I/O - so running it before a request turns out to be invalid
# contradicts that doctrine exactly as much as the fetch itself would.
# Neither the fetch nor the ancestry check can mutate the running
# service, so moving both ahead of the backup keeps the backup's actual
# purpose intact while giving a dev-channel rejection the same "nothing
# happened yet" guarantee every other rejection in this file already has.
# A plain "git fetch failed" just below is not a rejection, though - it
# is `set_state failed`, an operational failure of an otherwise-valid,
# accepted request - and it now skips the backup for that one failed
# pass. That is not a regression: nothing past this point ever ran, so
# there was nothing new for that pass to endanger, and the previous
# backup remains on disk regardless.
#
# Deliberately NO `set_state` transition here (Important 2). This used
# to write `set_state pull ""` right before the fetch below - the one
# phase name that, everywhere else in this file and in the web UI
# (index.html's four-step list, `web.system.update_step_pull` = "Loading
# the image"), means the image DOWNLOAD. A `git fetch` is typically
# sub-second against a local remote and has nothing to do with an image;
# confirmed against the unpatched script with a `docker compose pull`
# stub that ran for real seconds: state.json read "backup" the entire
# time, because the ACTUAL image pull further down never got its own
# transition at all - it ran silently inside the "backup" phase. Fetch
# and the dev-channel ancestry check below stay under "queued" instead
# (already the phase this pass entered with, at `set_state queued ""`,
# and already documented as "accepted, not yet started" - a fetch this
# fast is exactly that, not a step the web UI's four-item list claims a
# name for). `set_state pull ""` now sits where the pull actually is,
# right before `compose_pull_with_heartbeat` further down.
if ! run_git fetch --tags --force origin; then
  set_state failed "git fetch failed"
  exit 0
fi

if [ "$CHANNEL" = "dev" ]; then
  # The dev channel's equivalent of "forward only" (spec section 10,
  # rule 3): there is no ordering over SHAs, but there is ancestry. A
  # `git` that cannot answer at all - the binary missing, or the ref not
  # actually fetched - exits non-zero here exactly like a genuine "not an
  # ancestor" does. This fails CLOSED, the same direction every other
  # check in this file takes: an inconclusive answer is a "no", never
  # waved through as a "sure, why not".
  if ! git_repo merge-base --is-ancestor HEAD "$TARGET" 2>/dev/null; then
    reject "not a descendant of the running state"
  fi
  REF="$TARGET"
else
  REF="v${TARGET#v}"
fi

# 1. Back up. Before the checkout/pull/recreate below - no longer the
# literal first thing this pass does (see the reordering above), but
# still before anything that could touch the running service or its
# database: the signal database is the one thing a failed update could
# not restore - it holds the signal keys, and those are the wiring into
# the Loxone configuration.
#
# This phase now also covers the checkout and `set_tag` right after the
# backup itself (Important 2) - both fast, non-network filesystem
# operations, seconds at most, unlike the image pull that used to run
# silently inside this same phase (see the comment above the fetch,
# further up, for the full before/after). "Backing up the database" is
# not a literally exact label for "and now also checking out the target
# commit and writing the new tag into .env" - but every one of those
# three is quick prep work with nothing else in this file's four-step
# vocabulary to call it, and lumping them here is far closer to honest
# than leaving them inside "pull", the one name a step in the web UI's
# list already claims to mean "downloading the image".
set_state backup ""
STAMP="$(date -u +%Y-%m-%d-%H%M%S)"
if ! run tar czf "$BACKUP_DIR/store-$STAMP.tgz" -C /data loxmatter.sqlite; then
  set_state failed "backup failed - nothing was changed"
  exit 0
fi

# 2. Check out the target. `git rev-parse --verify` first, deliberately
# separate from the `checkout` call: without this, every checkout
# failure - a ref that genuinely does not exist, AND a checkout refused
# because $REPO's working tree has local modifications that would be
# overwritten - reported the identical "target $REF not found in the
# repository", even though only the first one is actually about the
# target. The second is a materially different, non-retryable-by-
# picking-another-target problem (nothing wrong with the request; the
# checkout on THIS host is dirty) that the shared message actively misled
# an operator away from. Answering "does this ref exist at all" on its
# own, first, is what makes the two distinguishable.
if ! git_repo rev-parse -q --verify "${REF}^{commit}" >/dev/null 2>&1; then
  set_state failed "target $REF not found in the repository"
  exit 0
fi
if ! run_git checkout --detach "$REF"; then
  set_state failed "checkout of $REF failed even though the ref exists - see the log (a repository with local modifications refuses a checkout the same way a missing ref does; this is that case)"
  exit 0
fi

# The image name is assembled HERE, from a fixed constant and a validated
# target - it never comes from the request (spec section 10, rule 2). That
# is also why $IMAGE below appears only in the log, not as an argument to
# any command: Compose forms the name from the .env line that set_tag
# writes next.
log "target image: $IMAGE:${TARGET#v}"
if ! set_tag "${TARGET#v}"; then
  # Proven with $STACK made unwritable: set_tag exited non-zero
  # ("Permission denied" creating its own temp file), and this call used
  # to be unguarded under `set -eu` - the shell simply stopped right
  # here. state.json stayed frozen at "pull" with no `error` field and
  # no "failed" phase ever written, while the heartbeat kept refreshing
  # (it is rewritten at the top of every pass, before this point is ever
  # reached again) - a live sidecar visibly stuck in a phase that had, at
  # that exact moment, already stopped being true: $REPO's checkout had
  # already moved to the new ref while the container still ran the old
  # image. Recording a real failure here is what makes that legible
  # instead of just frozen.
  set_state failed "could not write the new image tag into $ENV_FILE"
  exit 0
fi

# 2.5 Pull the image - the actual, potentially multi-minute download an
# arm64 Pi does over a home connection, and (Important 2) now the ONLY
# step this phase name covers, matching what the web UI's own step list
# already claims it means. `compose_pull_with_heartbeat`, not a bare
# `compose pull` (Important 1): a single blocking call this long needs
# its heartbeat kept moving from inside itself - see that function's own
# comment for the mechanism and its accepted trade-off.
set_state pull ""
if ! compose_pull_with_heartbeat "$SERVICE"; then
  # $FROM is what .env held before this run touched it - the ALIAS a
  # fresh installation ships with ("stable"), not necessarily a version
  # (see current_tag()'s own comment above). Restoring it is correct
  # HERE specifically: the pull failed, nothing was recreated, and the
  # only right thing is to put .env back exactly as it was. Do NOT copy
  # this call for Task 4's rollback, though - by the time that runs,
  # `--force-recreate` has already happened, and the concrete version
  # that was actually RUNNING may no longer be what "stable" resolves to
  # in the registry (it can already point AT the release that just
  # failed to become healthy). Task 4's rollback restores the concrete
  # running version instead, precisely for that reason - see the plan's
  # own rollback step, which computes what it writes back from
  # `$RUNNING`, never from `$FROM`.
  if ! set_tag "$FROM"; then
    # Same class of failure as the guard above, at the one point where it
    # is worse: the pull already failed, and now .env cannot even be put
    # back either. Say so explicitly - the plain "unchanged" message just
    # below would be a lie here: .env may still read the new, un-pulled
    # target.
    set_state failed "image could not be pulled, and the tag could not be restored in $ENV_FILE - it may still read $TARGET"
    exit 0
  fi
  set_state failed "image could not be pulled - the running service is unchanged"
  exit 0
fi

# 3. Replace it. --no-deps: matter-server and OTBR stay untouched, and
# this call never touches the `loxmatter-updater` service itself - see
# the comment near the end of the success branch below for why the
# sidecar no longer replaces itself at all, ever.
set_state recreate ""
RECREATE_OK=true
if ! compose up -d --no-deps --force-recreate "$SERVICE"; then
  # Proven with a `compose up` stub that exits 1: unlike the pull failure
  # above, "the running service is unchanged" is not true here -
  # `--force-recreate` removes the old container before creating its
  # replacement, so a failure partway through can leave the service
  # genuinely down, on neither the old image nor the new one. Restoring
  # just the .env tag (as the pull-failure branch does) would not
  # restart anything - the service would stay down until an operator or
  # a watchdog happens to run `docker compose up` again, and if that
  # ever happens it starts the NEW, unhealthy image, because .env would
  # still read it. What actually recovers this is a further `compose up`
  # attempt against the OLD, known-good image, which is exactly what the
  # rollback below performs - so hand off to it instead of just
  # recording "failed" and stopping. Deliberately NOT restoring $FROM
  # first: the rollback computes its own tag from $RUNNING (see the
  # comment on `set_tag "$FROM"` above for why $FROM - possibly the
  # alias "stable" - is the wrong value for that), so writing $FROM here
  # would only be overwritten a moment later by the rollback anyway.
  #
  # RECREATE_OK, not a further `exit 0`: by the time `compose up` can
  # fail here, `--force-recreate` has already removed the old container,
  # so waiting HEALTH_TIMEOUT seconds below for a container that was
  # never even created would only spend time without learning anything.
  # The rollback further down is the only thing that can still recover
  # this pass, and it should start at once, not after a wait already
  # known to be pointless.
  log "docker compose up failed for $TO - handing off to the same rollback the failed-health path reaches below, since --force-recreate can already have removed the old container"
  RECREATE_OK=false
fi

if [ "$RECREATE_OK" = true ]; then
  # 4. Wait for the first healthy beat.
  set_state health ""
  if wait_healthy; then
    # Never sweep away the last ten, as in scripts/update.sh - but only
    # NOW, once this update has actually reached "done", not immediately
    # after every backup as before. Pruning used to run right after the
    # tar call above regardless of what happened afterward, so a run of
    # ten FAILED attempts (each still makes exactly one backup, per step
    # 1's "before anything risky" reasoning) counted toward the very same
    # ten-file budget as genuine successes, and could evict the one backup
    # that matters most: the one taken just before a schema-raising
    # release - precisely the copy an operator would reach for if that
    # release needs reverting further back than this file's own rollback
    # goes. Deferring the prune to a confirmed success means a streak of
    # failures never touches the backup directory at all; it only shrinks
    # once an update actually sticks.
    #
    # shellcheck disable=SC2012  # filenames are self-generated (store-<UTC
    # timestamp>.tgz, written above by this same script) rather than
    # attacker- or user-supplied, so sorting them by mtime through `ls -t`
    # is safe here in a way it would not be in general. scripts/update.sh
    # already carries this exact pattern, unchecked; `find` has no equally
    # simple, equally portable stand-in for "sorted by modification time"
    # across the GNU/BSD/busybox sort/find/stat variance this project
    # already has to mind.
    ls -1t "$BACKUP_DIR"/store-*.tgz 2>/dev/null | tail -n +11 | while read -r old; do rm -f "$old"; done

    # A previous pass may have left this behind; a clean success means
    # the story it told is over. Written even though nothing here reads
    # it back - the file exists for a human on the other end of an SSH
    # session, not for this script.
    rm -f "$FAILURE"

    # Quoted "done": shellcheck (SC1010) reads a bare `done` here as the
    # loop-closing reserved word rather than a plain argument, even though
    # this position (a command's second argument) is not one where POSIX
    # actually gives it that meaning. Quoting settles the ambiguity for the
    # reader and the linter alike, same as any other phase name would need
    # if it happened to collide with a keyword.
    set_state "done" ""
    log "Update to $TO complete"

    # REMOVED (measured on the maintainer's Raspberry Pi, reproduced
    # deterministically): this used to be the point where, after writing
    # `done`, the sidecar pulled its own pinned image and - only if that
    # pull actually changed something - let `compose up -d --no-deps
    # loxmatter-updater` recreate its own container, detached.
    #
    # A container cannot replace itself with `docker compose up` executed
    # INSIDE it: the process carrying out the remaining steps of that
    # command is one of the things being stopped. Forcing the recreate
    # (`--force-recreate`) to remove the doubt about whether Compose would
    # even consider the image "changed" reproduced the failure every
    # time:
    #
    #   * The `docker exec` running this script died with exit 137
    #     (SIGKILL) - the container runtime tore down the very process
    #     that was still inside `compose up`, mid-command.
    #   * update-once.sh's own log ends at "Container loxmatter-updater
    #     Recreate" - Compose's own progress line for the step it was
    #     doing when it died. No exit line was ever written after it;
    #     there was nothing left running to write one.
    #   * Afterwards TWO containers existed where one should: the
    #     original `loxmatter-updater`, left behind in `Exited (0)`
    #     (Compose had already stopped it, the first half of a recreate),
    #     and a second one, `60112d04be65_loxmatter-updater` - the
    #     replacement Compose had started creating - stuck in `Created`,
    #     never started, because the process that would have started it
    #     no longer existed to do so.
    #   * `restart: unless-stopped` (docker-compose.yml) correctly did
    #     NOT bring anything back: the container had been explicitly
    #     stopped, not crashed, and that policy deliberately does not
    #     override an intentional stop. There was nothing left in the
    #     stack that still held the Docker socket, or that entrypoint.sh's
    #     poll loop could still be running inside.
    #
    # The result is not merely "the self-update did not happen" - it is
    # an installation left with NO running updater at all and a stray
    # half-created container next to it, recoverable only from the
    # console (`docker rm` the stray, `docker compose up -d
    # loxmatter-updater` by hand). That is the worst possible outcome for
    # a feature whose entire purpose was to avoid needing the console.
    #
    # Decided instead: the sidecar never touches its own service. It
    # keeps running whatever image it was started with until an operator
    # (or a future, differently-architected mechanism - e.g. a THIRD
    # party recreating it from outside, never itself) refreshes it. What
    # replaces this is an honest signal: the sidecar writes its own
    # version into state.json (see `set_state`'s `updater_version` field
    # below), the web UI compares it with the running bridge, and the
    # System tab card says so - and gives the one command that refreshes
    # it - when the two disagree, instead of silently drifting apart with
    # no way to notice short of reading state.json by hand.
    exit 0
  fi
fi

# `healthy: true` here is only set_state's own default (HEALTHY is
# initialised ahead of the validation section, above, and never touched
# since) - it is NOT a claim that anything is healthy, only that nothing
# has said otherwise yet. The rollback below sets HEALTHY to what
# actually happened (healthy again after rolling back, or not) before its
# own, terminal `set_state failed`.
if [ "$RECREATE_OK" = true ]; then
  ROLLBACK_REASON="update to $TO not healthy after ${HEALTH_TIMEOUT}s"
else
  ROLLBACK_REASON="docker compose up failed for $TO"
fi

# --------------------------------------------------------------- rollback --
# Reached from two places above: `compose up` failing outright, or the
# freshly-recreated container never reporting healthy. Either way,
# `--force-recreate` has already run - the old container is gone - so a
# further `compose up`, against the OLD, known-good image, is the only
# thing left that can bring the house back up.
#
# What deliberately does NOT happen here: the database is not restored.
# `_migrate` in model/store.py returns immediately once
# `version >= _SCHEMA_VERSION` - so the old version starts up fine on the
# new schema, and since every migration so far is an ALTER TABLE ADD
# COLUMN (which SQLite requires to be nullable or carry a default), the
# old version goes on writing valid rows into it. The image rollback
# alone is enough to bring the house back up.
#
# Restoring the backup would be the more destructive step: it discards
# everything written since the backup was taken. That is not something to
# do automatically at two in the morning when nobody is watching it - it
# stays a separate, explicit action in the web UI that a human has to
# confirm.
#
# WHERE the rollback lands is not the same as WHERE the update came from.
# If the .env held a moving alias ("stable", the normal case on every
# fresh installation since 0.2.0), that alias in the registry may by now
# point AT THE FAILED VERSION - it was resolved once, at pull time, and
# the registry does not stand still. Writing it back verbatim would mean
# fetching exactly the build that just failed to become healthy on the
# very next `compose pull`, with nothing left on disk to say a rollback
# ever happened.
#
# What gets written back is therefore the concrete version that was
# RUNNING before this pass touched anything ($RUNNING, computed above,
# before `--force-recreate` ever ran). Only when that could not be
# determined does the old, possibly-aliased .env entry ($FROM) remain the
# only information left to fall back on.
#
# Normalised through the SAME "${RUNNING#v}" the acceptance check above
# uses for $CUR, not tested raw - the two used to disagree: this case
# used to test bare $RUNNING against the enum, while the acceptance check
# tests it "v"-stripped. A build stamped "vdev" (a dev-channel build
# whose own version string carries the leading "v" the stable channel
# normally strips) matched neither literal branch here before - it fell
# through to the "found a real version" arm and produced BACK="dev",
# silently accepted as a rollback target, instead of correctly falling
# back to $FROM as an unidentified "dev" running-version already does
# everywhere else in this file.
RUNNING_NORMALIZED="${RUNNING#v}"
case "$RUNNING_NORMALIZED" in
  ''|unbekannt|dev) BACK="$FROM" ;;
  *)                BACK="$RUNNING_NORMALIZED" ;;
esac
log "$ROLLBACK_REASON - rolling back to $BACK"

# $BACK is the answer to "what did the rollback actually restore" -
# LETZTER-FEHLSCHLAG.txt already prints it verbatim ("Rolled back to:
# %s", further down in write_failure_file). state.json used to have no
# equivalent: the web UI's own rollback sentence read `state.from`
# instead, which on every standard installation (LOXMATTER_IMAGE_TAG=stable
# in .env since 0.2.0, see current_tag()'s own comment) is the moving
# alias, not the version this rollback actually put back. Setting
# $ROLLED_BACK_TO here, BEFORE the `set_state rollback ""` call right
# below, is what gets $BACK into that write and both of the terminal
# `set_state failed` calls further down in this section (set_state's own
# `${ROLLED_BACK_TO:-}` default means every OTHER set_state call in this
# file, none of which touch this variable, keeps writing `null` exactly
# as before).
ROLLED_BACK_TO="$BACK"

# One write here, not two: an earlier revision of this section wrote
# `set_state rollback ""` a second time immediately above this point,
# before $BACK was even known - a phase update no consumer distinguished
# from the one below, since ROLLED and BACK are still whatever they were
# a moment before either write. This single call already lands well
# before the health wait further down (state.json reads "phase":
# "rollback" for that entire wait), which is what the SIGTERM trap at the
# top of this file, and the test that exercises it, both rely on.
set_state rollback ""

if ! set_tag "$BACK"; then
  # Same class of failure as the two set_tag guards further up (writing
  # the new tag; restoring $FROM after a failed pull), at the point where
  # it matters most: without a rewritten .env, the `compose up` below
  # would just recreate the SAME broken image all over again - not a
  # rollback at all, just the exact failure repeated once more, still
  # inside this one pass (see "exactly once" below). Nothing past this
  # point can still help, so nothing past this point is attempted.
  #
  # ROLLED stays false here - it used to be set true unconditionally
  # BEFORE this call was ever attempted, so a failing `set_tag` (proven
  # with $STACK made read-only: this exact call failed, "Permission
  # denied") still left state.json and LETZTER-FEHLSCHLAG.txt both
  # claiming "Rolled back to: $BACK" while .env kept reading $TO - an
  # operator reading either would believe the house was back on the old
  # version when it plainly was not. ROLLED now only ever becomes true
  # once the write it claims has actually happened.
  HEALTHY=false
  ROLLBACK_MSG="version $TO did not become healthy, and the rollback tag could not be written into $ENV_FILE either - it may still read $TO"
  write_failure_file "$ROLLBACK_MSG"
  set_state failed "$ROLLBACK_MSG"
else
  ROLLED=true

  # Best-effort: the Compose file at $GIT_BEFORE is what actually matched
  # $BACK, but even a checkout that fails here (a dirty working tree, a
  # ref this shallow clone never fetched) should not stop the one thing
  # that matters most - recreating the container against the now-restored
  # tag. Whatever mismatch that leaves in docker-compose.yml is a smaller
  # problem than not attempting the recreate at all.
  run_git checkout --detach "$GIT_BEFORE" || true
  compose up -d --no-deps --force-recreate "$SERVICE" || true

  # Exactly once. No second attempt, no flapping: if the cause were not
  # the image itself (a dead matter-server, say), every further attempt
  # here would only add more downtime without changing the outcome.
  if wait_healthy; then
    HEALTHY=true
  else
    HEALTHY=false
  fi

  ROLLBACK_MSG="version $TO did not become healthy after ${HEALTH_TIMEOUT}s"
  write_failure_file "$ROLLBACK_MSG"
  set_state failed "$ROLLBACK_MSG"
fi
