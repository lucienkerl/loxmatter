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
