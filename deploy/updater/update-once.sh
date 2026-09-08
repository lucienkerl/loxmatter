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
REPO="${LOXMATTER_REPO:-/repo}"
SERVICE="${LOXMATTER_SERVICE:-loxmatter}"
IMAGE="${LOXMATTER_IMAGE:-ghcr.io/lucienkerl/loxmatter}"
HEALTH_URL="${LOXMATTER_HEALTH_URL:-http://host.docker.internal:8080/health}"
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
  if STATE_JSON="$(jq -n \
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
         updater_seen_at: $seen}')" \
    && [ -n "$STATE_JSON" ]; then
    write_state "$STATE_JSON"
  else
    printf 'set_state: jq failed to build state.json for phase "%s" - refusing to write\n' "$1" >&2
    return 1
  fi
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
  #
  # Non-empty is not sufficient by itself, though - two things jq can
  # produce that are perfectly valid, non-empty JSON and still not usable
  # as a state:
  #   * state.json containing the literal `null`: `null | .updater_seen_at
  #     = $seen` is legal jq and yields `{"updater_seen_at": "..."}` - a
  #     real, non-empty object, just missing "phase" and every other
  #     field. Recorded as-is, that state permanently loses them: a live
  #     heartbeat with no phase and no id, forever, and nothing about it
  #     ever looks broken enough to self-heal.
  #   * state.json as a directory: `[ -f "$STATE" ]` above is false, so
  #     this whole branch is skipped and the `else` runs `set_state idle`
  #     instead - see write_state's own guard for what that used to do
  #     (mv the tmp file silently into the directory).
  # Require the parsed value to be a JSON object carrying "phase" before
  # accepting it as a refresh target; anything else - `null`, a bare
  # string, a number, an array - falls through to the same "no usable
  # state yet" recovery the `else` branch already uses for a missing file.
  if REFRESHED="$(jq --arg seen "$(now)" \
       'if (type == "object" and has("phase"))
        then .updater_seen_at = $seen
        else empty end' \
       "$STATE" 2>/dev/null)" \
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
case "$JOB_ID" in
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

# Compose reads docker-compose.yml AND .env from the current directory (or
# an explicit --project-directory) - both live in $STACK. `cd` there in a
# subshell instead of passing --project-directory, the same way
# scripts/update.sh already does: the logged command then reads exactly as
# an operator typing it by hand from that directory would, which matters
# when comparing this log against scripts/update.sh's own output during an
# incident. The subshell keeps the `cd` from leaking into the rest of this
# script.
compose() {
  log "\$ (cd $STACK && docker compose $*)"
  (cd "$STACK" && docker compose "$@") >> "$LOG" 2>&1
}

# Replaces EXACTLY that one line and leaves the rest of the .env untouched.
# It also carries MINISERVER_IP, RADIO_DEVICE and the API token - an update
# that rewrites the whole file takes half the installation down with it.
#
# Goes through a temp file and `mv`, the same atomic-write idiom
# write_state() already uses above, rather than `sed -i`: GNU sed's `-i`
# takes an optional attached suffix, but BSD/macOS sed's `-i` requires one
# as a SEPARATE argument - `sed -i -E '...'` on BSD sed reads "-E" as that
# argument (a literal backup-file suffix) and runs the script that follows
# as a basic, not extended, regular expression. Verified end to end on this
# machine: it happened to still produce the right substitution (the
# pattern below uses no ERE-only syntax, so BRE and ERE agree on it) but
# silently left a stray ".env-E" backup file behind on every call - exactly
# the kind of accidental success this project has been bitten by more than
# once. Sidestepped entirely rather than patched per-platform: neither sed
# needs `-E` here, and neither needs `-i` once the substitution is piped
# through a temp file instead.
set_tag() {
  if grep -q '^LOXMATTER_IMAGE_TAG=' "$ENV_FILE" 2>/dev/null; then
    sed "s|^LOXMATTER_IMAGE_TAG=.*|LOXMATTER_IMAGE_TAG=$1|" "$ENV_FILE" \
      > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
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
#
# shellcheck disable=SC2012  # filenames are self-generated (store-<UTC
# timestamp>.tgz, written two lines above by this same script) rather than
# attacker- or user-supplied, so sorting them by mtime through `ls -t` is
# safe here in a way it would not be in general. scripts/update.sh already
# carries this exact pattern, unchecked; `find` has no equally simple,
# equally portable stand-in for "sorted by modification time" across the
# GNU/BSD/busybox sort/find/stat variance this project already has to mind.
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
  # rule 3): there is no ordering over SHAs, but there is ancestry. A
  # `git` that cannot answer at all - the binary missing, or the ref not
  # actually fetched - exits non-zero here exactly like a genuine "not an
  # ancestor" does. This fails CLOSED, the same direction every other
  # check in this file takes: an inconclusive answer is a "no", never
  # waved through as a "sure, why not".
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
# target - it never comes from the request (spec section 10, rule 2). That
# is also why $IMAGE below appears only in the log, not as an argument to
# any command: Compose forms the name from the .env line that set_tag
# writes next.
log "target image: $IMAGE:${TARGET#v}"
set_tag "${TARGET#v}"

if ! compose pull "$SERVICE"; then
  set_tag "$FROM"
  set_state failed "image could not be pulled - the running service is unchanged"
  exit 0
fi

# 3. Replace it. --no-deps: matter-server and OTBR stay untouched, and the
# sidecar does not replace itself - that would terminate it in the middle
# of its own request.
set_state recreate ""
if ! compose up -d --no-deps --force-recreate "$SERVICE"; then
  set_state failed "restart failed"
  exit 0
fi

# 4. Wait for the first healthy beat.
set_state health ""
if wait_healthy; then
  # Quoted "done": shellcheck (SC1010) reads a bare `done` here as the
  # loop-closing reserved word rather than a plain argument, even though
  # this position (a command's second argument) is not one where POSIX
  # actually gives it that meaning. Quoting settles the ambiguity for the
  # reader and the linter alike, same as any other phase name would need
  # if it happened to collide with a keyword.
  set_state "done" ""
  log "Update to $TO complete"
  exit 0
fi

set_state rollback ""
