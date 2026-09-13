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
#
# The radios job of the updater sidecar - design "Radios in the Web UI"
# (2026-09-11), section 6. One pass: report the effective radio
# configuration from .env, and when radios-request.json holds a request not
# handled yet, validate it. update-once.sh runs before this in the same
# entrypoint loop, so the two never run at the same time.

set -eu

UPDATE_DIR="${LOXMATTER_UPDATE_DIR:-/data/update}"
STACK="${LOXMATTER_STACK:-/repo/deploy/testhost}"
STACK_HOST_PATH="${LOXMATTER_STACK_HOST_PATH:-}"
HOST_DEV="${LOXMATTER_HOST_DEV:-/host/dev}"
SYS_BLUETOOTH="${LOXMATTER_SYS_BLUETOOTH:-/sys/class/bluetooth}"
MATTER_SERVER_URL="${LOXMATTER_MATTER_SERVER_URL:-http://host.docker.internal:5580/}"
BLUETOOTH_TIMEOUT="${LOXMATTER_RADIOS_BLUETOOTH_TIMEOUT:-60}"
# How long `verify_thread` waits for a Thread state, and after how long it
# applies the watchdog's fix once. A normal attach took 22-35 s on the Pi.
# With the fix at 30 s, on 13 September 2026 three Thread enable requests
# had otbr restarted in the middle of an attach that was about to succeed,
# and the restarted agent did not come back within the 60 s the window
# had left. 60 s lets a slow attach finish; 150 s gives a restarted
# agent the same 90 s a fresh one had before. Neither changes how long the
# job stays silent: the loop refreshes its heartbeat on every poll. They do
# change how long a pass can run, and a pass must end inside entrypoint.sh's
# WORKER_TIMEOUT_SECONDS (600 s): a request changing both radios whose
# verifications fail forward and again in the rollback now counts up to
# 2 x (60 + 150) = 420 s of polling, before the time each poll's own
# `docker exec` and the recreates take.
THREAD_TIMEOUT="${LOXMATTER_RADIOS_THREAD_TIMEOUT:-150}"
THREAD_FIX_AFTER="${LOXMATTER_RADIOS_THREAD_FIX_AFTER:-60}"
POLL_SECONDS="${LOXMATTER_RADIOS_POLL_SECONDS:-5}"

REQUEST="$UPDATE_DIR/radios-request.json"
STATE="$UPDATE_DIR/radios-state.json"
LOG="$UPDATE_DIR/radios-log.txt"
UPDATE_STATE="$UPDATE_DIR/state.json"
HANDLED_DIR="$UPDATE_DIR/radios-handled"
ENV_FILE="$STACK/.env"

NEWLINE='
'

mkdir -p "$UPDATE_DIR" "$HANDLED_DIR"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

log() {
  { printf '%s %s\n' "$(now)" "$*" >> "$LOG"
    tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  } 2>/dev/null || true
}

# -------------------------------------------------------------------- .env --

env_value() {
  [ -f "$ENV_FILE" ] || return 0
  raw="$(sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1)"
  # A hand-edited .env may quote its value; strip one matching pair so
  # RADIO_DEVICE="/dev/..." compares equal to an unquoted request value.
  case "$raw" in
    \"*\") raw="${raw#\"}"; raw="${raw%\"}" ;;
    \'*\') raw="${raw#\'}"; raw="${raw%\'}" ;;
  esac
  printf '%s' "$raw"
}

env_target() {
  target="$ENV_FILE"
  if [ -L "$target" ]; then
    resolved="$(readlink -f "$target" 2>/dev/null || true)"
    if [ -n "$resolved" ]; then target="$resolved"; fi
  fi
  printf '%s' "$target"
}

# Rewrites through `cat >`, not `mv`, so a symlinked .env and the file's
# inode stay what the operator set up - the same care as set_tag() in
# update-once.sh. Values reaching here passed validation: a by-id path
# ([A-Za-z0-9._:+-]), a number, or a profile list built below.
#
# Returns non-zero (never dies under `set -eu`) on any failed step, and
# cleans up its own `.radios-tmp` either way - env_set_or_fail below is
# what turns a failure here into a terminal state. This runs as a plain
# statement at every call site, not as an `if` condition the way the
# backup's own `cp` already is - nothing here was ever exempt from `set -e`
# before this guard, so an unwritable .env or an ENOSPC card used to take
# the whole pass down mid-write instead of failing cleanly.
env_set() {
  target="$(env_target)"
  if grep -q "^$1=" "$target" 2>/dev/null; then
    escaped="$(printf '%s' "$2" | sed 's/[\\&|]/\\&/g')"
    if ! sed "s|^$1=.*|$1=$escaped|" "$target" > "$target.radios-tmp"; then
      rm -f "$target.radios-tmp"
      return 1
    fi
    if ! cat "$target.radios-tmp" > "$target"; then
      rm -f "$target.radios-tmp"
      return 1
    fi
    rm -f "$target.radios-tmp"
  else
    printf '%s=%s\n' "$1" "$2" >> "$target" || return 1
  fi
}

# Every env_set call site below goes through here, so a write failure ends
# in a terminal, retryable-next-time state instead of dying mid-write with
# no write_state call and no log line - the exact gap a reviewer
# reproduced: .env possibly already truncated by env_set's own
# `cat > "$target"`, the phase left at "write" (not terminal), rolled_back
# still false (it is only ever set once a restore is attempted), and a
# handled marker that would block any retry of the same request forever.
# A failed write also RESTORES the backup before giving up. Without that,
# this path left the very thing it was guarding against on disk: a .env
# possibly truncated mid-write by env_set's own `cat > "$target"`, with a
# fresh, complete, unused copy of the good file sitting right beside it
# under the name this same pass had just stamped. The recovery is the one
# the rollback path further down already performs verbatim; there was no
# reason for a write failure to be the one terminal exit that did not
# attempt it. Nothing has been applied at this point - the `write` step
# runs before any compose call - so a successful restore fully undoes the
# job, which is what rolled_back: true then reports.
#
# The error key stays env_write_failed whether or not the restore works,
# and a failed restore is NOT escalated to env_restore_failed. The two
# ways to get here are not alike. On an ENOSPC mid-write .env really is
# damaged and the restore is the repair. On an unwritable .env (a
# read-only file, a read-only mount) the write never began - the
# redirection failed to open the file, so nothing was truncated - and the
# restore is then guaranteed to fail for the very same reason, against a
# file that was never harmed. Reporting "the .env file could not be
# restored from its backup", with healthy: false, would be a more
# alarming message for the case where LESS went wrong. env_write_failed
# is the true cause in both, so it is what gets reported; ROLLED is set
# only when the restore actually succeeded, because only then is the
# previous setting genuinely back.
env_set_or_fail() {
  if ! env_set "$1" "$2"; then
    if [ -n "${BACKUP:-}" ] && [ -f "$BACKUP" ] && cat "$BACKUP" > "$(env_target)" 2>/dev/null; then
      ROLLED=true
      log "radios request $JOB_ID: restored .env from $BACKUP after a failed write"
    else
      log "radios request $JOB_ID: could not restore .env from ${BACKUP:-(no backup)}"
    fi
    write_state failed env_write_failed
    log "radios request $JOB_ID: could not write $1 to .env"
    exit 0
  fi
}

profiles_with_thread() {
  others="$(env_value COMPOSE_PROFILES | tr ',' '\n' | awk 'NF && $0 != "thread" { printf "%s%s", sep, $0; sep = "," }')"
  if [ "$1" = on ]; then
    if [ -n "$others" ]; then printf '%s,thread' "$others"; else printf 'thread'; fi
  else
    printf '%s' "$others"
  fi
}

# Brackets every compose call with a heartbeat (see refresh_heartbeat):
# recreating a container is the one thing here that can take longer than
# `_MAX_SILENT_SECONDS` without any loop of this script's own running to
# keep the timestamps moving. `compose_status` is captured rather than
# returned directly because the refresh afterwards would otherwise become
# this function's exit status, and every call site reads that status to
# decide whether the step failed.
compose() {
  log "\$ docker compose -f $STACK/docker-compose.yml --project-directory $STACK_HOST_PATH --env-file $ENV_FILE $*"
  refresh_heartbeat
  docker compose -f "$STACK/docker-compose.yml" --project-directory "$STACK_HOST_PATH" \
    --env-file "$ENV_FILE" "$@" >> "$LOG" 2>&1
  compose_status=$?
  refresh_heartbeat
  return $compose_status
}

# ----------------------------------------------------------------- report --

# thread_enabled is "the profile says so, or an otbr container exists" -
# the test Pi runs otbr without a COMPOSE_PROFILES line (design 12.1).
# BLUETOOTH_ADAPTER defaults to 0 because docker-compose.yml does.
read_current() {
  CUR_DEVICE="$(env_value RADIO_DEVICE)"
  CUR_BLUETOOTH="$(env_value BLUETOOTH_ADAPTER)"
  case "$CUR_BLUETOOTH" in ''|*[!0-9]*) CUR_BLUETOOTH=0 ;; esac
  # Strip leading zeros so a padded .env value ("01") compares equal, both
  # as a string against a request's unpadded number and as JSON via
  # --argjson, to an unpadded value.
  CUR_BLUETOOTH="$(printf '%s' "$CUR_BLUETOOTH" | sed 's/^0*\([0-9]\)/\1/')"
  otbr_state="$(docker ps -a --filter 'name=^otbr$' --format '{{.State}}' 2>/dev/null | head -n 1 || true)"
  if [ "$otbr_state" = running ]; then CUR_OTBR_RUNNING=true; else CUR_OTBR_RUNNING=false; fi
  if env_value COMPOSE_PROFILES | tr ',' '\n' | grep -qx thread || [ -n "$otbr_state" ]; then
    CUR_ENABLED=true
  else
    CUR_ENABLED=false
  fi
}

CAPABLE=true
CAPABLE_REASON=""
if [ ! -d "$HOST_DEV" ]; then
  CAPABLE=false
  CAPABLE_REASON=host_dev_not_mounted
elif [ -z "$STACK_HOST_PATH" ]; then
  CAPABLE=false
  CAPABLE_REASON=stack_host_path_unknown
fi

JOB_ID=""
JOB_PHASE=idle
JOB_STEPS='[]'
JOB_ERROR=""
ROLLED=false
HEALTHY=null
# Set by load_previous_state's self-heal below to the phase the dead pass
# was found in, and read once afterwards for the log line. Declared here
# because `set -u` is on and the self-heal only sometimes assigns it.
INTERRUPTED_PHASE=""

load_previous_state() {
  [ -f "$STATE" ] || return 0
  jq -e 'type == "object"' "$STATE" >/dev/null 2>&1 || return 0
  JOB_ID="$(jq -r '.id // empty' "$STATE")"
  JOB_PHASE="$(jq -r '.phase // "idle"' "$STATE")"
  JOB_STEPS="$(jq -c 'if (.steps | type) == "array" then .steps else [] end' "$STATE")"
  JOB_ERROR="$(jq -r '.error // empty' "$STATE")"
  ROLLED="$(jq -c 'if .rolled_back == true then true else false end' "$STATE")"
  HEALTHY="$(jq -c 'if (.healthy | type) == "boolean" then .healthy else null end' "$STATE")"

  # Self-heal, the counterpart of update-once.sh's own top-of-pass
  # recovery (the `if [ -f "$STATE" ] && refresh_heartbeat` block there):
  # any NON-TERMINAL phase found here proves the previous pass died.
  #
  # That inference is sound because of how this script is invoked. One
  # invocation is exactly one pass: no job outlives the process that
  # started it, there is no background work, and entrypoint.sh starts the
  # next pass only after this one has exited. So a phase like
  # "apply_thread" sitting in the state file at STARTUP cannot mean "a job
  # is working on it" - nothing is running that could be. It can only mean
  # the pass that wrote it was killed before it reached a terminal phase:
  # `docker stop` (entrypoint.sh forwards the SIGTERM), the host powering
  # off, entrypoint.sh's own 600-second worker timeout, or an OOM kill.
  #
  # Without this, such a state was PERMANENT and it stranded the user, for
  # exactly the reason the phase is still there to begin with: this pass
  # would re-assert the dead phase with a FRESH seen_at (write_state, just
  # below, rebuilds the whole file), the handled marker would send the
  # request check straight to `exit 0`, and the next pass would do the
  # same thing two seconds later, forever. The card then saw a sidecar
  # whose heartbeat was perfectly healthy and a job that never advanced -
  # so not even the stall detection fired - leaving the step list frozen,
  # both selects disabled, Apply hidden and every POST /api/radios a 409
  # (`request_radios` in src/loxmatter/radios/sidecar.py refuses while the
  # phase is non-terminal). Surviving a reload, a logout and a new
  # browser, because it was server-side state, its only exit was deleting
  # radios-state.json over SSH - the console trip this whole feature
  # exists to abolish.
  #
  # No signal trap is needed to make this work, and deliberately so: a
  # trap could only ever cover the signals it is installed for, while this
  # covers every way a pass can fail to finish, SIGKILL and a power cut
  # included. The cost is that the recovery lands on the NEXT pass rather
  # than at the moment of death - about two seconds later in a running
  # container, or at the next container start after a `docker stop`, which
  # is the first moment anything could be reported to anyone anyway.
  #
  # `failed` rather than a new phase of its own: the terminal set is
  # closed (see the comment on the case arm below), and `interrupted` as
  # the ERROR key is what tells this apart from a job that really failed
  # its verification. HEALTHY is left exactly as it was found - usually
  # null - because nothing here knows whether the half-applied change
  # works; claiming either way would be inventing a measurement. What the
  # user is told follows from the error key alone
  # (`web.radios.result_interrupted`, via `radiosResultKey()` in app.js):
  # neither finished nor undone, look and decide - NOT the "previous
  # setting restored" the ordinary failure path reports, which for an
  # interrupted pass would be a plain lie. .env may well already carry the
  # new values, and `otbr` may be half recreated, because the rollback is
  # the very thing that never got to run.
  #
  # THE PHASE LIST BELOW IS ONE OF THREE COPIES. The terminal phases of
  # the radios job - idle, done, failed, rejected, unchanged - are written
  # out in full in:
  #   1. this case arm;
  #   2. `TERMINAL_PHASES` in src/loxmatter/radios/sidecar.py, which
  #      decides whether POST /api/radios is a 409;
  #   3. the inline array in `radiosPhaseActive()` in
  #      src/loxmatter/web/app.js, which decides whether the card treats a
  #      job as still running.
  # Nothing at runtime ties the three together - they live in three
  # processes, one of them POSIX sh - so
  # `tests/test_updater_radios_script.py` compares all three textually
  # instead. A phase added to one and not the others means, concretely: a
  # job the bridge thinks is finished and the card thinks is still
  # running, or a pass that self-heals a phase the bridge still refuses
  # requests for.
  case "$JOB_PHASE" in
    idle|done|failed|rejected|unchanged) ;;
    *)
      INTERRUPTED_PHASE="$JOB_PHASE"
      JOB_PHASE=failed
      JOB_ERROR=interrupted
      ;;
  esac
}

write_state() {
  JOB_PHASE="$1"
  JOB_ERROR="${2:-}"
  if [ -e "$STATE" ] && [ ! -f "$STATE" ]; then
    printf 'write_state: %s exists and is not a regular file - refusing to write\n' "$STATE" >&2
    return 1
  fi
  read_current
  jq -n \
    --arg id "$JOB_ID" --arg phase "$JOB_PHASE" --argjson steps "$JOB_STEPS" \
    --arg error "$JOB_ERROR" --argjson rolled "$ROLLED" --argjson healthy "$HEALTHY" \
    --argjson enabled "$CUR_ENABLED" --arg device "$CUR_DEVICE" \
    --argjson bluetooth "$CUR_BLUETOOTH" --argjson running "$CUR_OTBR_RUNNING" \
    --argjson capable "$CAPABLE" --arg reason "$CAPABLE_REASON" --arg seen "$(now)" \
    '{id: (if $id == "" then null else $id end), phase: $phase, steps: $steps,
      error: (if $error == "" then null else $error end), rolled_back: $rolled,
      healthy: $healthy,
      current: {thread_enabled: $enabled,
                thread_device: (if $device == "" then null else $device end),
                bluetooth_adapter: $bluetooth, otbr_running: $running},
      capable: $capable, capable_reason: (if $reason == "" then null else $reason end),
      seen_at: $seen}' > "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

# ------------------------------------------------------------- heartbeat --

# Rewrites exactly one timestamp field in one state file, atomically and
# best-effort. Never touches anything else in it - see refresh_heartbeat
# below for why that restraint is the whole point.
touch_seen_at() {
  [ -f "$1" ] || return 0
  refreshed="$(jq --arg seen "$(now)" \
    "if type == \"object\" and has(\"phase\") then $2 = \$seen else empty end" \
    "$1" 2>/dev/null || true)"
  [ -n "$refreshed" ] || return 0
  if printf '%s\n' "$refreshed" > "$1.heartbeat-tmp" 2>/dev/null; then
    mv "$1.heartbeat-tmp" "$1" 2>/dev/null || rm -f "$1.heartbeat-tmp" 2>/dev/null
  else
    rm -f "$1.heartbeat-tmp" 2>/dev/null
  fi
  return 0
}

# The counterpart of update-once.sh's `refresh_heartbeat()` - read that
# function's comment first. It exists because most of a SUCCESSFUL update
# used to spend most of its time looking, to the web UI, exactly like a
# crashed sidecar. The radios job had the same hole, and worse, because
# `sidecar_status()` (src/loxmatter/radios/sidecar.py) asks two questions
# and an applying pass could answer neither:
#
#   * `updater_present(update_state)`, checked FIRST, reads
#     `updater_seen_at` in state.json - and ONLY update-once.sh ever
#     writes that file. entrypoint.sh runs the two workers one after the
#     other in the same loop, so for as long as this script is applying a
#     change update-once.sh cannot run at all and that timestamp simply
#     stops moving.
#   * `seen_at` in radios-state.json is written by `write_state`, which
#     runs once per STEP - and a single step here is `verify_bluetooth`
#     (up to 60 s) or `verify_thread` (up to 150 s). During a rollback
#     nothing was written at all, `step()` being a no-op while $ROLLING.
#
# `_MAX_SILENT_SECONDS` is 30 (src/loxmatter/update.py), so about half a
# minute into the flagship flow - switching the Thread stick, which the
# design itself calls one to two minutes - a perfectly healthy job began
# reporting itself to the card as an abandoned one.
#
# Deliberately NOT `write_state`, for the same reason update-once.sh's
# heartbeat is deliberately not its `set_state`: this may only ever touch
# the timestamps. `write_state` rebuilds the whole file from this
# script's own JOB_* variables, which is right when the phase is really
# changing and wrong here - the rollback path keeps the phase at
# "rollback" on purpose for the entire recovery (see `step()`), and a
# heartbeat that could overwrite a phase would undo that.
#
# Writing into state.json - ANOTHER worker's file - is safe here because
# of SEQUENTIALITY, and for no other reason. entrypoint.sh runs
# update-once.sh to completion and only then runs this script, in one
# loop, so at the moment this line executes there is provably no
# update-once.sh process alive to race with. That is the whole of the
# argument.
#
# In particular it is NOT the `has("phase")` test in `touch_seen_at`
# above that makes this safe. That test is a SHAPE check - "is this file
# a state file at all", so a corrupt or half-written one is left alone -
# and a shape check is not a lock: it does not exclude a concurrent
# writer, and two processes could both pass it and then both write. If
# the two workers are ever made to overlap (a second loop, a manual
# invocation while the loop runs, a future entrypoint that parallelises
# them), this function needs real mutual exclusion and the phase test
# will not supply it.
#
# Best-effort throughout, like `log()`: a momentarily unwritable volume
# must not take down a pass that is otherwise making real progress. The
# next write_state (or the next pass) rewrites both files anyway.
refresh_heartbeat() {
  touch_seen_at "$STATE" .seen_at
  touch_seen_at "$UPDATE_STATE" .updater_seen_at
  return 0
}

reject() {
  write_state rejected "$1"
  log "radios request ${JOB_ID:-?} rejected: $1"
  exit 0
}

load_previous_state
write_state "$JOB_PHASE" "$JOB_ERROR"
# State first, then log - the order reject() above already observes, and
# for the same reason: recording the outcome is the part that must not be
# skippable.
if [ -n "$INTERRUPTED_PHASE" ]; then
  log "radios request ${JOB_ID:-?}: the previous pass died in phase $INTERRUPTED_PHASE - recorded as failed (interrupted)"
fi

# ---------------------------------------------------------------- request --

[ -e "$REQUEST" ] || exit 0

# A FIFO (or anything else non-regular) left at this path must never be
# opened: with no writer, `cat` (or any reader) on a FIFO blocks forever,
# and update-once.sh runs in this same entrypoint loop, so that would stall
# updates too. Reject it - without ever attempting a read - before the
# regular-file case gets anywhere near one.
[ -f "$REQUEST" ] || reject request_malformed

# Read the request exactly once, into a shell variable - never through a
# file. /data (this whole directory) is mounted read-write into both the
# bridge and the updater, so any file this script created at a predictable
# path there (a "snapshot") is just as writable by whoever writes
# radios-request.json, and a symlink re-planted at that path between an
# `rm -f` and the next write can redirect a truncating copy anywhere this
# process can write, .env included. Process memory has no such shared path
# to race. Every check and every value read below is fed from this one
# in-memory copy via `printf '%s' "$REQUEST_BODY" | jq ...`, never from
# $REQUEST again - so an attacker who controls the bridge cannot make the
# schema check and the value reads see different bytes (design 6.6), and a
# request that becomes unreadable fails once, here, instead of crashing an
# unguarded read under `set -eu` with the phase stuck at "validate" forever.
#
# Bounded with `timeout`: the `[ -f "$REQUEST" ]` check above only proves
# $REQUEST was a regular file at that instant. A FIFO swapped in at the
# same path strictly between that check and this read still opens as a
# blocking read with no writer on the other end, and update-once.sh runs
# in this same entrypoint loop - an unbounded `cat` here would stall
# updates too, exactly like the no-writer FIFO case the `-f` check already
# stops on its own. `timeout` turns that hang into an empty $REQUEST_BODY
# after 5 seconds instead, which the emptiness check below rejects the
# same as any other unreadable request - a terminal, retryable-next-time
# state rather than a wedged pass.
REQUEST_BODY="$(timeout 5 cat "$REQUEST" 2>/dev/null || true)"
[ -n "$REQUEST_BODY" ] || reject request_malformed

REQUEST_ID="$(printf '%s' "$REQUEST_BODY" | jq -r 'if type == "object" and (.id | type) == "string" then .id else empty end' 2>/dev/null || true)"
MARKER="$REQUEST_ID"
case "$REQUEST_ID" in
  ''|.|..|*"$NEWLINE"*|*[!A-Za-z0-9._-]*)
    MARKER="invalid-$(printf '%s' "$REQUEST_BODY" | cksum | cut -d ' ' -f 1)" ;;
esac
if [ "${#MARKER}" -gt 128 ]; then
  MARKER="invalid-$(printf '%s' "$REQUEST_BODY" | cksum | cut -d ' ' -f 1)"
fi
if [ "$MARKER" = "$JOB_ID" ] || [ -e "$HANDLED_DIR/$MARKER" ]; then
  exit 0
fi

UPDATE_PHASE="$(jq -r '.phase // "idle"' "$UPDATE_STATE" 2>/dev/null || echo idle)"
case "$UPDATE_PHASE" in
  queued|backup|pull|build|recreate|health|rollback)
    log "radios request $MARKER waits: an update is in phase $UPDATE_PHASE"
    exit 0 ;;
esac

JOB_ID="$MARKER"
JOB_STEPS='["validate"]'
ROLLED=false
HEALTHY=null
: > "$HANDLED_DIR/$MARKER"
write_state validate ""

[ "$MARKER" = "$REQUEST_ID" ] || reject request_malformed
[ "$CAPABLE" = true ] || reject "$CAPABLE_REASON"

# Still a strict whitelist: the key set is exactly what it always was, and
# every rule that applied to a half's contents still applies. Only the
# value type widened - a half may now be `null`, which means "leave this
# radio alone" (design 6.2/6.3). A request naming a radio still has to
# describe it completely and correctly.
printf '%s' "$REQUEST_BODY" | jq -e '
  type == "object"
  and (keys | sort) == ["bluetooth", "id", "requested_at", "thread"]
  and ((.thread == null)
       or ((.thread | type) == "object" and (.thread | keys | sort) == ["device", "enabled"]
           and (.thread.enabled | type) == "boolean"
           and ((.thread.device == null)
                or ((.thread.device | type) == "string"
                    and (.thread.device | test("\\A/dev/serial/by-id/[A-Za-z0-9._:+-]+\\z"))))))
  and ((.bluetooth == null)
       or ((.bluetooth | type) == "object" and (.bluetooth | keys | sort) == ["adapter"]
           and (.bluetooth.adapter | type) == "number"
           and .bluetooth.adapter == (.bluetooth.adapter | floor)
           and (.bluetooth.adapter | tostring | test("^[0-9]+$"))
           and .bluetooth.adapter >= 0 and .bluetooth.adapter <= 15))
' >/dev/null 2>&1 || reject request_malformed

# Which radios this request is about at all. A half that is `null` is left
# strictly alone from here on: not validated against the host, not written
# to .env, not applied and not verified. The defaults below are what the
# rest of the script sees for an absent half, and they are deliberately
# inert - but nothing downstream may rely on that alone, so every branch
# that could touch a radio tests its HAS_* flag as well.
#
# Guarded (not bare assignments): a read failing here must reject, not let
# `set -eu` kill the pass with the marker already written (see above).
if ! HAS_THREAD="$(printf '%s' "$REQUEST_BODY" | jq -r 'if .thread == null then false else true end' 2>/dev/null)"; then
  reject request_malformed
fi
if ! HAS_BLUETOOTH="$(printf '%s' "$REQUEST_BODY" | jq -r 'if .bluetooth == null then false else true end' 2>/dev/null)"; then
  reject request_malformed
fi

WANT_ENABLED=false
WANT_DEVICE=""
if [ "$HAS_THREAD" = true ]; then
  if ! WANT_ENABLED="$(printf '%s' "$REQUEST_BODY" | jq -r '.thread.enabled' 2>/dev/null)"; then
    reject request_malformed
  fi
  if ! WANT_DEVICE="$(printf '%s' "$REQUEST_BODY" | jq -r '.thread.device // empty' 2>/dev/null)"; then
    reject request_malformed
  fi
fi

WANT_BLUETOOTH=""
if [ "$HAS_BLUETOOTH" = true ]; then
  if ! WANT_BLUETOOTH="$(printf '%s' "$REQUEST_BODY" | jq -r '.bluetooth.adapter' 2>/dev/null)"; then
    reject request_malformed
  fi
fi

# Only a request that actually asks for Thread is checked against the
# host. The HAS_THREAD test is redundant against the default above and
# kept anyway: this is the exact check that used to fail a user whose
# configured stick had been unplugged - it rejected the whole job, the
# Bluetooth change included, over a device nobody had asked to change.
# Saying so at the check itself is worth one extra condition.
if [ "$HAS_THREAD" = true ] && [ "$WANT_ENABLED" = true ]; then
  [ -n "$WANT_DEVICE" ] || reject thread_device_required
  device_link="$HOST_DEV${WANT_DEVICE#/dev}"
  [ -L "$device_link" ] || reject thread_device_not_found
  host_dev_real="$(readlink -f "$HOST_DEV" 2>/dev/null || printf '%s' "$HOST_DEV")"
  device_target="$(readlink -f "$device_link" 2>/dev/null || true)"
  device_name="${device_target#"$host_dev_real"/}"
  [ "$device_name" != "$device_target" ] || reject thread_device_not_a_serial_port
  case "$device_name" in
    ttyUSB*|ttyACM*) ;;
    *) reject thread_device_not_a_serial_port ;;
  esac
  device_number="${device_name#tty???}"
  case "$device_number" in ''|*[!0-9]*) reject thread_device_not_a_serial_port ;; esac
  [ -n "$(env_value BACKBONE_IF)" ] || reject backbone_interface_missing
fi

if [ "$HAS_BLUETOOTH" = true ]; then
  [ -e "$SYS_BLUETOOTH/hci$WANT_BLUETOOTH" ] || reject bluetooth_adapter_not_found
fi

# ---------------------------------------------------------------- changes --

read_current
ORIG_ENABLED="$CUR_ENABLED"
BLUETOOTH_CHANGE=false
if [ "$HAS_BLUETOOTH" = true ] && [ "$WANT_BLUETOOTH" != "$CUR_BLUETOOTH" ]; then
  BLUETOOTH_CHANGE=true
fi
# An absent Thread half leaves THREAD_ACTION at none, which is what keeps
# `write`, `apply_thread`, `verify_thread` and the rollback's own Thread
# pass away from a radio nobody asked about. Note that the HAS_THREAD test
# here is NOT redundant: without it a `null` half would fall into the
# `elif` below and read as "Thread requested off", tearing down a running
# border router for a user who only changed Bluetooth.
THREAD_ACTION=none
if [ "$HAS_THREAD" = true ]; then
  if [ "$WANT_ENABLED" = true ]; then
    if [ "$CUR_ENABLED" = false ] || [ "$WANT_DEVICE" != "$CUR_DEVICE" ]; then THREAD_ACTION=up; fi
  elif [ "$CUR_ENABLED" = true ]; then
    THREAD_ACTION=down
  fi
fi

if [ "$BLUETOOTH_CHANGE" = false ] && [ "$THREAD_ACTION" = none ]; then
  write_state unchanged ""
  log "radios request $JOB_ID changes nothing"
  exit 0
fi

[ -f "$ENV_FILE" ] || reject env_file_missing

# ------------------------------------------------------------- apply --

JOB_STEPS='["validate","backup","write"]'
if [ "$BLUETOOTH_CHANGE" = true ]; then
  JOB_STEPS="$(printf '%s' "$JOB_STEPS" | jq -c '. + ["apply_bluetooth","verify_bluetooth"]')"
fi
if [ "$THREAD_ACTION" != none ]; then
  JOB_STEPS="$(printf '%s' "$JOB_STEPS" | jq -c '. + ["apply_thread","verify_thread"]')"
fi

if [ "$THREAD_ACTION" = up ] && [ "$ORIG_ENABLED" = true ]; then
  ROLLBACK_THREAD=up
elif [ "$THREAD_ACTION" = up ]; then
  ROLLBACK_THREAD=down
elif [ "$THREAD_ACTION" = down ]; then
  ROLLBACK_THREAD=up
else
  ROLLBACK_THREAD=none
fi

BACKUP="$(env_target).radios-$(date -u +%Y%m%d%H%M%S)"
write_state backup ""
if ! cp "$(env_target)" "$BACKUP"; then
  write_state failed env_backup_failed
  log "radios request $JOB_ID: could not back up .env"
  exit 0
fi

# Keep only the newest 5: .env carries LOXMATTER_API_TOKEN (see
# deploy/testhost/.env.example), and nothing here ever pruned its own
# backups before - every applying job leaves one behind, an SD card that
# already fills up one update at a time (update-once.sh's own backup
# prune) would fill up one radios change at a time too, each copy carrying
# a live secret nothing since ever removed. Same shape as that prune, one
# filename per line piped through `rm`.
# shellcheck disable=SC2012  # $(env_target).radios-* is stamped by this
# same line above, never attacker- or user-supplied, so sorting by mtime
# via `ls -t` is safe here the way it is not in general - see
# update-once.sh's identical backup prune for the full reasoning.
ls -1t "$(env_target)".radios-* 2>/dev/null | tail -n +6 | while read -r old; do rm -f "$old"; done

write_state write ""
if [ "$THREAD_ACTION" = up ]; then
  env_set_or_fail RADIO_DEVICE "$WANT_DEVICE"
  env_set_or_fail COMPOSE_PROFILES "$(profiles_with_thread on)"
  if [ -z "$(env_value RADIO_BAUDRATE)" ]; then env_set_or_fail RADIO_BAUDRATE 460800; fi
elif [ "$THREAD_ACTION" = down ]; then
  env_set_or_fail COMPOSE_PROFILES "$(profiles_with_thread off)"
fi
if [ "$BLUETOOTH_CHANGE" = true ]; then env_set_or_fail BLUETOOTH_ADAPTER "$WANT_BLUETOOTH"; fi

# The heartbeat inside this loop (and `verify_thread`'s below) is what
# keeps a waiting job distinguishable from a dead sidecar - see
# refresh_heartbeat. One `write_state` ran when this step began and the
# next one cannot run until it ends, up to $BLUETOOTH_TIMEOUT seconds
# later.
verify_bluetooth() {
  waited=0
  while [ "$waited" -lt "$BLUETOOTH_TIMEOUT" ]; do
    refresh_heartbeat
    if curl -s -o /dev/null --max-time 3 "$MATTER_SERVER_URL"; then return 0; fi
    sleep "$POLL_SECONDS"
    waited=$((waited + POLL_SECONDS))
  done
  return 1
}

apply_thread() {
  if [ "$1" = up ]; then
    compose up -d --no-deps --force-recreate otbr
  else
    compose rm -s -f otbr
  fi
}

# The watchdog's known fix (scripts/otbr-watchdog.sh): on the Pi kernel
# start-stop-daemon can leave a stale pid file and otbr-agent never runs.
# Applied at most once per verification.
verify_thread() {
  if [ "$1" = down ]; then
    [ -z "$(docker ps -a --filter 'name=^otbr$' --format '{{.Names}}' 2>/dev/null)" ]
    return
  fi
  waited=0
  fixed=0
  while [ "$waited" -lt "$THREAD_TIMEOUT" ]; do
    refresh_heartbeat
    case "$(docker exec otbr ot-ctl state 2>/dev/null | tr -d '\r' | head -n 1)" in
      leader|router|child) return 0 ;;
    esac
    if [ "$fixed" -eq 0 ] && [ "$waited" -ge "$THREAD_FIX_AFTER" ]; then
      fixed=1
      log "no Thread state after ${waited}s - clearing the stale pid file and restarting otbr"
      docker exec otbr rm -f /run/otbr-agent.pid >/dev/null 2>&1 || true
      compose restart otbr || true
    fi
    sleep "$POLL_SECONDS"
    waited=$((waited + POLL_SECONDS))
  done
  return 1
}

ROLLING=false
FAILED_STEP=""

step() {
  if [ "$ROLLING" = false ]; then
    write_state "$1" ""
  else
    # A rollback must keep the phase at "rollback" for the whole recovery
    # (that is what this guard has always been for), but it must not keep
    # the sidecar SILENT - and silent is exactly what it was: the one
    # stretch of this script that wrote nothing whatsoever, while
    # recreating containers and verifying them for up to another three and
    # a half minutes. A timestamp-only heartbeat says "still here, still
    # working" without touching the phase. See refresh_heartbeat.
    refresh_heartbeat
  fi
}

apply_and_verify() {
  if [ "$BLUETOOTH_CHANGE" = true ]; then
    step apply_bluetooth
    compose up -d --no-deps --force-recreate matter-server || { FAILED_STEP=apply_bluetooth; return 1; }
    step verify_bluetooth
    verify_bluetooth || { FAILED_STEP=verify_bluetooth; return 1; }
  fi
  if [ "$THREAD_ACTION" != none ]; then
    step apply_thread
    apply_thread "$THREAD_ACTION" || { FAILED_STEP=apply_thread; return 1; }
    step verify_thread
    verify_thread "$THREAD_ACTION" || { FAILED_STEP=verify_thread; return 1; }
  fi
  return 0
}

# The rollback's own pass, unlike apply_and_verify just above, must not
# stop at the first failure: a request changing BOTH radios whose Thread
# verify failed still has a Bluetooth rollback to attempt (or the reverse)
# - returning early after one side's rollback fails would leave the OTHER
# radio recreated against the values that just failed verification, while
# .env (already restored, further down, before this ever runs) names the
# OLD value for both - .env and the running containers would then disagree
# about which radio is which. Attempts every touched service regardless of
# an earlier one's own outcome, and reports failure if ANY of them did, not
# just the last one tried.
#
# THIS FUNCTION AND apply_and_verify MUST BE EDITED TOGETHER. Everything
# above explains why they differ; this says what to do about it. They
# apply the same services in the same order through the same helpers, and
# every change to the forward pass - a service added, a reordering, a
# different helper, another step - belongs in both. Only the handling of a
# failure may diverge, for the reason given above. A change made to one
# alone does not announce itself: most of the tests in
# tests/test_updater_radios_script.py exercise the forward pass, while
# this one runs only after a verification has already failed.
rollback_and_verify() {
  ok=true
  if [ "$BLUETOOTH_CHANGE" = true ]; then
    step apply_bluetooth
    if compose up -d --no-deps --force-recreate matter-server; then
      step verify_bluetooth
      verify_bluetooth || ok=false
    else
      ok=false
    fi
  fi
  if [ "$THREAD_ACTION" != none ]; then
    step apply_thread
    if apply_thread "$THREAD_ACTION"; then
      step verify_thread
      verify_thread "$THREAD_ACTION" || ok=false
    else
      ok=false
    fi
  fi
  [ "$ok" = true ]
}

if apply_and_verify; then
  HEALTHY=true
  write_state done ""
  log "radios request $JOB_ID applied"
  exit 0
fi

ERROR_KEY="${FAILED_STEP}_failed"
log "radios request $JOB_ID: $FAILED_STEP failed - restoring $BACKUP"
write_state rollback "$ERROR_KEY"
# Set BEFORE the restore is attempted, not after: a restore that itself
# fails (below) is still a rollback that was ATTEMPTED, and rolled_back
# must say so rather than default back to whatever it was before this job.
ROLLED=true
if ! cat "$BACKUP" > "$(env_target)"; then
  HEALTHY=false
  write_state failed env_restore_failed
  log "radios request $JOB_ID: could not restore .env from $BACKUP"
  exit 0
fi
ROLLING=true
# The rollback recreates or removes otbr, and with the container goes the
# only record of why the agent did not form the network: rsyslog inside the
# image is not reliable (it had not run for two days on 13 September 2026),
# so `docker logs` is all there is. Saved beside the job's own log, one file
# per request id - which the request check above limits to
# [A-Za-z0-9._-] - and only the newest 5 kept.
if [ "$ROLLBACK_THREAD" != none ] \
  && [ -n "$(docker ps -a --filter 'name=^otbr$' --format '{{.Names}}' 2>/dev/null)" ]; then
  OTBR_LOG="radios-otbr-$JOB_ID.log"
  refresh_heartbeat
  if docker logs --timestamps --tail 400 otbr > "$UPDATE_DIR/$OTBR_LOG" 2>&1; then
    log "radios request $JOB_ID: saved the last 400 lines of the otbr log to $OTBR_LOG"
  else
    log "radios request $JOB_ID: could not save the otbr log (see $OTBR_LOG)"
  fi
  refresh_heartbeat
  # shellcheck disable=SC2012  # names this script wrote itself, from a
  # checked id - the same reasoning as the .env backup prune above.
  ls -1t "$UPDATE_DIR"/radios-otbr-*.log 2>/dev/null | tail -n +6 | while read -r old; do rm -f "$old"; done
fi
THREAD_ACTION="$ROLLBACK_THREAD"
if rollback_and_verify; then HEALTHY=true; else HEALTHY=false; fi
write_state failed "$ERROR_KEY"
log "radios request $JOB_ID rolled back, healthy after rollback: $HEALTHY"
