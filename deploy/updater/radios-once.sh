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
THREAD_TIMEOUT="${LOXMATTER_RADIOS_THREAD_TIMEOUT:-90}"
THREAD_FIX_AFTER="${LOXMATTER_RADIOS_THREAD_FIX_AFTER:-30}"
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
env_set() {
  target="$(env_target)"
  if grep -q "^$1=" "$target" 2>/dev/null; then
    escaped="$(printf '%s' "$2" | sed 's/[\\&|]/\\&/g')"
    sed "s|^$1=.*|$1=$escaped|" "$target" > "$target.radios-tmp"
    cat "$target.radios-tmp" > "$target"
    rm -f "$target.radios-tmp"
  else
    printf '%s=%s\n' "$1" "$2" >> "$target"
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

compose() {
  log "\$ docker compose -f $STACK/docker-compose.yml --project-directory $STACK_HOST_PATH --env-file $ENV_FILE $*"
  docker compose -f "$STACK/docker-compose.yml" --project-directory "$STACK_HOST_PATH" \
    --env-file "$ENV_FILE" "$@" >> "$LOG" 2>&1
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

load_previous_state() {
  [ -f "$STATE" ] || return 0
  jq -e 'type == "object"' "$STATE" >/dev/null 2>&1 || return 0
  JOB_ID="$(jq -r '.id // empty' "$STATE")"
  JOB_PHASE="$(jq -r '.phase // "idle"' "$STATE")"
  JOB_STEPS="$(jq -c 'if (.steps | type) == "array" then .steps else [] end' "$STATE")"
  JOB_ERROR="$(jq -r '.error // empty' "$STATE")"
  ROLLED="$(jq -c 'if .rolled_back == true then true else false end' "$STATE")"
  HEALTHY="$(jq -c 'if (.healthy | type) == "boolean" then .healthy else null end' "$STATE")"
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

reject() {
  write_state rejected "$1"
  log "radios request ${JOB_ID:-?} rejected: $1"
  exit 0
}

load_previous_state
write_state "$JOB_PHASE" "$JOB_ERROR"

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

printf '%s' "$REQUEST_BODY" | jq -e '
  type == "object"
  and (keys | sort) == ["bluetooth", "id", "requested_at", "thread"]
  and (.thread | type) == "object" and (.thread | keys | sort) == ["device", "enabled"]
  and (.bluetooth | type) == "object" and (.bluetooth | keys | sort) == ["adapter"]
  and (.thread.enabled | type) == "boolean"
  and ((.thread.device == null)
       or ((.thread.device | type) == "string"
           and (.thread.device | test("\\A/dev/serial/by-id/[A-Za-z0-9._:+-]+\\z"))))
  and (.bluetooth.adapter | type) == "number"
  and .bluetooth.adapter == (.bluetooth.adapter | floor)
  and (.bluetooth.adapter | tostring | test("^[0-9]+$"))
  and .bluetooth.adapter >= 0 and .bluetooth.adapter <= 15
' >/dev/null 2>&1 || reject request_malformed

# Guarded (not a bare assignment): a read failing here must reject, not let
# `set -eu` kill the pass with the marker already written (see above).
if ! WANT_ENABLED="$(printf '%s' "$REQUEST_BODY" | jq -r '.thread.enabled' 2>/dev/null)"; then
  reject request_malformed
fi
if ! WANT_DEVICE="$(printf '%s' "$REQUEST_BODY" | jq -r '.thread.device // empty' 2>/dev/null)"; then
  reject request_malformed
fi
if ! WANT_BLUETOOTH="$(printf '%s' "$REQUEST_BODY" | jq -r '.bluetooth.adapter' 2>/dev/null)"; then
  reject request_malformed
fi

if [ "$WANT_ENABLED" = true ]; then
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

[ -e "$SYS_BLUETOOTH/hci$WANT_BLUETOOTH" ] || reject bluetooth_adapter_not_found

# ---------------------------------------------------------------- changes --

read_current
ORIG_ENABLED="$CUR_ENABLED"
BLUETOOTH_CHANGE=false
if [ "$WANT_BLUETOOTH" != "$CUR_BLUETOOTH" ]; then BLUETOOTH_CHANGE=true; fi
THREAD_ACTION=none
if [ "$WANT_ENABLED" = true ]; then
  if [ "$CUR_ENABLED" = false ] || [ "$WANT_DEVICE" != "$CUR_DEVICE" ]; then THREAD_ACTION=up; fi
elif [ "$CUR_ENABLED" = true ]; then
  THREAD_ACTION=down
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

write_state write ""
if [ "$THREAD_ACTION" = up ]; then
  env_set RADIO_DEVICE "$WANT_DEVICE"
  env_set COMPOSE_PROFILES "$(profiles_with_thread on)"
  if [ -z "$(env_value RADIO_BAUDRATE)" ]; then env_set RADIO_BAUDRATE 460800; fi
elif [ "$THREAD_ACTION" = down ]; then
  env_set COMPOSE_PROFILES "$(profiles_with_thread off)"
fi
if [ "$BLUETOOTH_CHANGE" = true ]; then env_set BLUETOOTH_ADAPTER "$WANT_BLUETOOTH"; fi

verify_bluetooth() {
  waited=0
  while [ "$waited" -lt "$BLUETOOTH_TIMEOUT" ]; do
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
  if [ "$ROLLING" = false ]; then write_state "$1" ""; fi
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

if apply_and_verify; then
  HEALTHY=true
  write_state done ""
  log "radios request $JOB_ID applied"
  exit 0
fi

ERROR_KEY="${FAILED_STEP}_failed"
log "radios request $JOB_ID: $FAILED_STEP failed - restoring $BACKUP"
write_state rollback "$ERROR_KEY"
cat "$BACKUP" > "$(env_target)"
ROLLED=true
ROLLING=true
THREAD_ACTION="$ROLLBACK_THREAD"
if apply_and_verify; then HEALTHY=true; else HEALTHY=false; fi
write_state failed "$ERROR_KEY"
log "radios request $JOB_ID rolled back, healthy after rollback: $HEALTHY"
