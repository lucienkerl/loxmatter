#!/bin/sh
# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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
# shellcheck disable=SC2034  # consumed by Task 3's clone/checkout step
REPO="${LOXMATTER_REPO:-/repo}"
SERVICE="${LOXMATTER_SERVICE:-loxmatter}"
# shellcheck disable=SC2034  # consumed by Task 3, assembling the image ref
IMAGE="${LOXMATTER_IMAGE:-ghcr.io/lucienkerl/loxmatter}"
# shellcheck disable=SC2034  # consumed by Task 3's health check
HEALTH_URL="${LOXMATTER_HEALTH_URL:-http://host.docker.internal:8080/health}"
# shellcheck disable=SC2034  # consumed by Task 3's health check
HEALTH_TIMEOUT="${LOXMATTER_HEALTH_TIMEOUT:-120}"

REQUEST="$UPDATE_DIR/request.json"
STATE="$UPDATE_DIR/state.json"
LOG="$UPDATE_DIR/log.txt"
# shellcheck disable=SC2034  # consumed by Task 4's rollback bookkeeping
FAILURE="$UPDATE_DIR/LETZTER-FEHLSCHLAG.txt"
ENV_FILE="$STACK/.env"

mkdir -p "$UPDATE_DIR" "$BACKUP_DIR"

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
write_state() {
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
# of that work.
if [ -f "$STATE" ]; then
  # `jq` on an existing state.json can fail (corrupt file, truncated by an
  # unrelated crash, hand edited). In `write_state "$(jq ...)"` above, the
  # command substitution's exit status was being discarded - it is an
  # argument to write_state, not the command `set -e` sees - so a failing
  # jq used to write write_state an EMPTY string, which write_state then
  # dutifully persisted as the new state.json. That is unrecoverable by
  # itself: updater_seen_at is gone, so the bridge concludes the sidecar is
  # absent and hides the update button for good, and every later pass
  # reads the same broken file and does it again. Check the substitution's
  # own exit status explicitly (assigning it directly, not nesting it) and
  # fall back to a fresh idle state instead of persisting jq's failure.
  if REFRESHED="$(jq --arg seen "$(now)" '.updater_seen_at = $seen' "$STATE" 2>/dev/null)" \
    && [ -n "$REFRESHED" ]; then
    write_state "$REFRESHED"
  else
    JOB_ID="" FROM="" TO="" set_state idle ""
  fi
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
LAST="$(jq -r '.id // empty' "$STATE" 2>/dev/null || true)"
[ "$JOB_ID" != "$LAST" ] || exit 0

if [ "$UNREADABLE" = 1 ]; then
  FROM="" TO="" reject "request is not readable (invalid JSON, a top-level array, or a missing/empty id)"
fi

FROM="$(current_tag)"
TO="$TARGET"
ROLLED=false
HEALTHY=true

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

# Rule 3: forward only. In the stable channel by semantic version;
# `sort -V` from coreutils, busybox's sort cannot do that reliably. The
# dev channel has no ordering over SHAs - there Task 3 instead checks
# ancestry, once the refs have been fetched.
#
# RUNNING is fetched here, deliberately AFTER the channel/pattern checks
# above and not before: a malformed or malicious request never earns a
# docker call at all, and only a well-formed candidate for the stable
# channel triggers the one read-only `docker inspect` needed to answer
# "what is running right now".
if [ "$CHANNEL" = "stable" ]; then
  RUNNING="$(running_version)"
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
