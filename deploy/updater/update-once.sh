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
# Captured before ANY checkout happens - Task 3's own checkout (further
# down, of $REF) is the first thing that would move $REPO's working tree.
# Task 4's rollback checks the repository back out to exactly this commit
# again: the Compose file must match the running image (a new release can
# add a service or a variable the old one does not know), so rolling the
# image back without also rolling the checkout back can leave the old
# image started against a Compose file it was never meant to run under.
# Falls back to the literal string "HEAD" whenever $REPO does not (yet)
# answer `rev-parse` with something usable - `git checkout --detach HEAD`
# is then a well-defined no-op, the safest thing to attempt when there is
# no real answer to "checked out before". Two failure shapes, not one, and
# both need the same fallback: `git` can exit non-zero (no repository
# there at all), or it can exit 0 with EMPTY output - proven against this
# file's own test fixtures, whose default `git` stub does exactly that
# (unlike a real `git rev-parse HEAD`, which never succeeds without
# printing a SHA). `... || echo HEAD` alone only catches the first shape;
# a `git` that "succeeds" silently sailed straight through it and left
# GIT_BEFORE empty, so the rollback's own `git checkout --detach ""`
# further down would fail as well. Following the same
# capture-then-${:-default} idiom current_tag() and running_version()
# already use above closes both shapes in one place.
git_before_raw="$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)"
GIT_BEFORE="${git_before_raw:-HEAD}"
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
#
# The `cd` is checked explicitly, ahead of the subshell, rather than left
# to fail inside it: without this, a missing or unreadable $STACK makes
# the whole subshell exit non-zero with nothing on record beyond that -
# indistinguishable here from `docker compose` itself failing. Both call
# sites below then report a plainly wrong diagnosis ("image could not be
# pulled", "restart failed") for a problem that is neither a pull nor a
# restart, but the stack directory itself. This one log line at least
# tells the two apart for whoever reads log.txt afterward.
compose() {
  if [ ! -d "$STACK" ]; then
    log "compose: $STACK is not a directory - cannot run docker compose there"
    return 1
  fi
  log "\$ (cd $STACK && docker compose $*)"
  (cd "$STACK" && docker compose "$@") >> "$LOG" 2>&1
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
    sleep 1
  done
  return 1
}

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

# 1. Back up. Before the checkout/pull/recreate below - no longer the
# literal first thing this pass does (see the reordering above), but
# still before anything that could touch the running service or its
# database: the signal database is the one thing a failed update could
# not restore - it holds the signal keys, and those are the wiring into
# the Loxone configuration.
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
if ! git -C "$REPO" rev-parse -q --verify "${REF}^{commit}" >/dev/null 2>&1; then
  set_state failed "target $REF not found in the repository"
  exit 0
fi
if ! run git -C "$REPO" checkout --detach "$REF"; then
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

if ! compose pull "$SERVICE"; then
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

# 3. Replace it. --no-deps: matter-server and OTBR stay untouched, and the
# sidecar does not replace itself before `done` is written - see the
# self-replacement at the very end of the success branch below.
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

    # Last, and only after a successful update: the sidecar checks
    # whether its own pinned image is out of date and triggers its own
    # replacement, detached. AFTER writing `done`, never before - doing
    # this earlier would terminate the sidecar in the middle of writing
    # the state the web UI is currently reading, and a successful update
    # would look like a stuck one instead of a finished one.
    #
    # Detached via `-d`: the call that replaces this very container must
    # not wait inside it for its own end. `--no-deps`, the same as every
    # other compose call in this file: the bridge and its neighbours are
    # not this call's business.
    if [ "${LOXMATTER_UPDATER_SELF_REPLACE:-1}" = "1" ]; then
      compose up -d --no-deps loxmatter-updater || true
    fi

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
set_state rollback ""

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
case "$RUNNING" in
  ''|unbekannt|dev) BACK="$FROM" ;;
  *)                BACK="${RUNNING#v}" ;;
esac
log "$ROLLBACK_REASON - rolling back to $BACK"
ROLLED=true
set_state rollback ""

if ! set_tag "$BACK"; then
  # Same class of failure as the two set_tag guards further up (writing
  # the new tag; restoring $FROM after a failed pull), at the point where
  # it matters most: without a rewritten .env, the `compose up` below
  # would just recreate the SAME broken image all over again - not a
  # rollback at all, just the exact failure repeated once more, still
  # inside this one pass (see "exactly once" below). Nothing past this
  # point can still help, so nothing past this point is attempted.
  HEALTHY=false
  set_state failed "version $TO did not become healthy, and the rollback tag could not be written into $ENV_FILE either - it may still read $TO"
else
  # Best-effort: the Compose file at $GIT_BEFORE is what actually matched
  # $BACK, but even a checkout that fails here (a dirty working tree, a
  # ref this shallow clone never fetched) should not stop the one thing
  # that matters most - recreating the container against the now-restored
  # tag. Whatever mismatch that leaves in docker-compose.yml is a smaller
  # problem than not attempting the recreate at all.
  run git -C "$REPO" checkout --detach "$GIT_BEFORE" || true
  compose up -d --no-deps --force-recreate "$SERVICE" || true

  # Exactly once. No second attempt, no flapping: if the cause were not
  # the image itself (a dead matter-server, say), every further attempt
  # here would only add more downtime without changing the outcome.
  if wait_healthy; then
    HEALTHY=true
  else
    HEALTHY=false
  fi

  set_state failed "version $TO did not become healthy after ${HEALTH_TIMEOUT}s"
fi

# If even the rollback did not become healthy, the web UI is probably not
# reachable at all - then this file is the only answer someone finds who
# checks in over SSH after all. It is written on a successful rollback
# too: anyone who wants to know why their version is the old one again
# should be able to read up on it without needing the web UI at all.
#
# The restore commands below reach into $BACKUP_DIR through
# loxmatter-updater specifically, not loxmatter: by the time an operator
# runs them, loxmatter has just been asked to `stop`, and a stopped
# container cannot be `exec`ed into. loxmatter-updater keeps running
# throughout and shares the same volume at /data (see docker-compose.yml).
{
  printf 'loxmatter - last failed update attempt\n\n'
  printf 'Time:           %s\n' "$(now)"
  printf 'Attempted:      %s -> %s (channel %s)\n' "$FROM" "$TO" "$CHANNEL"
  printf 'Rolled back to: %s\n' "$BACK"
  printf 'Healthy again:  %s\n\n' "$([ "$HEALTHY" = true ] && echo yes || echo NO)"
  printf 'Signal database: NOT restored automatically (see model/store.py -\n'
  printf 'an older version starts up fine on a newer schema). A backup taken\n'
  printf 'just before this attempt is available if you need to go further\n'
  printf 'back than the image rollback above went:\n'
  printf '  %s/store-%s.tgz\n\n' "$BACKUP_DIR" "$STAMP"
  printf 'Manual next steps:\n'
  printf '  cd %s && docker compose logs --tail 100 %s\n' "$STACK" "$SERVICE"
  printf '  cd %s && ./scripts/update.sh --no-pull\n\n' "$REPO"
  printf 'To restore that backup instead (discards every signal learned\n'
  printf 'since it was taken - only if you are sure):\n'
  printf '  cd %s && docker compose stop %s\n' "$STACK" "$SERVICE"
  printf "  docker exec loxmatter-updater sh -c 'tar xzf %s/store-%s.tgz -C /data'\n" "$BACKUP_DIR" "$STAMP"
  printf '  cd %s && docker compose start %s\n\n' "$STACK" "$SERVICE"
  printf 'Last lines of the log:\n'
  tail -n 40 "$LOG" 2>/dev/null || true
} > "$FAILURE"
