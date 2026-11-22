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
# The matter-server job of the updater sidecar - design "matter-server
# follows its pinned image" (2026-11-22). One pass: when the running
# matter-server container was created from a different version of the image
# the Compose file pins, pull that version, back up ./data, recreate
# matter-server, and check that it registers the same nodes as before - or
# go back to the previous image and the backup.
#
# entrypoint.sh runs this after update-once.sh and radios-once.sh in the
# same loop, so it never runs beside an update or a radios job.
#
# Only the image is compared, never anything else (design, section 4).
# matter-server holds every commissioned device's fabric, and on
# 23 September 2026 an unprepared image switch brought it up with 0 nodes.

set -eu

UPDATE_DIR="${LOXMATTER_UPDATE_DIR:-/data/update}"
STACK="${LOXMATTER_STACK:-/repo/deploy/testhost}"
STACK_HOST_PATH="${LOXMATTER_STACK_HOST_PATH:-}"
# The volume update-once.sh keeps its database backups in.
BACKUP_DIR="${LOXMATTER_BACKUP_DIR:-/data/backups}"
# How long the recreated matter-server has to report its nodes. The test Pi
# took about a minute for 27 nodes from 1.4.0 to 1.6.0. A pass runs the
# verification at most twice (forward and in the rollback) and must end
# inside entrypoint.sh's 900 s for this worker.
VERIFY_TIMEOUT="${LOXMATTER_MATTER_SERVER_VERIFY_TIMEOUT:-240}"
POLL_SECONDS="${LOXMATTER_MATTER_SERVER_POLL_SECONDS:-5}"
# The limit of one docker call that only reads: `inspect`, `logs`. The log
# of a matter-server that ran for weeks is tens of megabytes.
PROBE_TIMEOUT="${LOXMATTER_MATTER_SERVER_PROBE_TIMEOUT:-30}"
# `docker stop`: Compose's own grace period is 10 s.
STOP_TIMEOUT="${LOXMATTER_MATTER_SERVER_STOP_TIMEOUT:-60}"
# The pull: its limit and how long a failed one waits, as for otbr.
PULL_TIMEOUT="${LOXMATTER_MATTER_SERVER_PULL_TIMEOUT:-600}"
PULL_RETRY="${LOXMATTER_MATTER_SERVER_PULL_RETRY:-1800}"
KEEP_BACKUPS=3

STATE="$UPDATE_DIR/matter-server-state.json"
LOG="$UPDATE_DIR/matter-server-log.txt"
UPDATE_STATE="$UPDATE_DIR/state.json"
RADIOS_STATE="$UPDATE_DIR/radios-state.json"
ENV_FILE="$STACK/.env"
COMPOSE_FILE="$STACK/docker-compose.yml"
CHECKED="$UPDATE_DIR/matter-server-upkeep-checked"
TRIED="$UPDATE_DIR/matter-server-upkeep-tried"
HELD="$UPDATE_DIR/matter-server-upkeep-held"
PULL_FAILED="$UPDATE_DIR/matter-server-upkeep-pull-failed-at"
OVERRIDE="$UPDATE_DIR/matter-server-rollback.yml"

# A trailing slash would make every prefix test below miss.
case "$STACK_HOST_PATH" in */) STACK_HOST_PATH="${STACK_HOST_PATH%/}" ;; esac

mkdir -p "$UPDATE_DIR"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

log() {
  { printf '%s %s\n' "$(now)" "$*" >> "$LOG"
    tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  } 2>/dev/null || true
}

checksum() {
  cksum | awk '{ print $1 "-" $2 }'
}

# ------------------------------------------------------------- heartbeat --

# The same as radios-once.sh's touch_seen_at and refresh_heartbeat - read
# the comment there. While this job waits, neither update-once.sh nor
# radios-once.sh can run, and the web UI reads a state file whose timestamp
# stops for longer than `_MAX_SILENT_SECONDS` (src/loxmatter/update.py) as a
# sidecar that is gone. Writing into the other workers' files is safe
# because entrypoint.sh runs the workers one after the other.
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

refresh_heartbeat() {
  touch_seen_at "$STATE" .seen_at
  touch_seen_at "$RADIOS_STATE" .seen_at
  touch_seen_at "$UPDATE_STATE" .updater_seen_at
  return 0
}

# ----------------------------------------------------------------- state --

JOB_ID=""
JOB_PHASE=idle
JOB_ERROR=""
ROLLED=false
HEALTHY=null
FROM=""
TO=""
NODES_BEFORE=""
NODES_AFTER=""
BACKUP_NAME=""
INTERRUPTED_PHASE=""

load_previous_state() {
  [ -f "$STATE" ] || return 0
  jq -e 'type == "object"' "$STATE" >/dev/null 2>&1 || return 0
  JOB_ID="$(jq -r '.id // empty' "$STATE")"
  JOB_PHASE="$(jq -r '.phase // "idle"' "$STATE")"
  JOB_ERROR="$(jq -r '.error // empty' "$STATE")"
  ROLLED="$(jq -c 'if .rolled_back == true then true else false end' "$STATE")"
  HEALTHY="$(jq -c 'if (.healthy | type) == "boolean" then .healthy else null end' "$STATE")"
  FROM="$(jq -r '.from // empty' "$STATE")"
  TO="$(jq -r '.to // empty' "$STATE")"
  NODES_BEFORE="$(jq -r '.nodes_before // empty' "$STATE")"
  NODES_AFTER="$(jq -r '.nodes_after // empty' "$STATE")"
  BACKUP_NAME="$(jq -r '.backup // empty' "$STATE")"
  # One pass is one job and nothing outlives it, so a phase that is not
  # terminal at the start of a pass means the pass that wrote it was killed
  # (the same inference as radios-once.sh's load_previous_state).
  case "$JOB_PHASE" in
    idle|done|failed) ;;
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
  jq -n \
    --arg id "$JOB_ID" --arg phase "$JOB_PHASE" --arg error "$JOB_ERROR" \
    --argjson rolled "$ROLLED" --argjson healthy "$HEALTHY" \
    --arg from "$FROM" --arg to "$TO" \
    --arg before "$NODES_BEFORE" --arg after "$NODES_AFTER" \
    --arg backup "$BACKUP_NAME" --arg seen "$(now)" \
    'def opt: if . == "" then null else . end;
     {id: ($id | opt), phase: $phase, error: ($error | opt),
      rolled_back: $rolled, healthy: $healthy,
      from: ($from | opt), to: ($to | opt),
      nodes_before: ($before | opt), nodes_after: ($after | opt),
      backup: ($backup | opt), seen_at: $seen}' > "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

# ---------------------------------------------------------------- docker --

# Every compose call that changes something goes through here: logged, its
# output in the log, bracketed by the heartbeat. With ROLLBACK_IMAGE set,
# the override file that sets only matter-server's image is added - the
# Compose file pins the image literally, so no environment variable could
# replace it.
ROLLBACK_IMAGE=""
compose() {
  log "\$ docker compose -f $COMPOSE_FILE${ROLLBACK_IMAGE:+ -f $OVERRIDE} --project-directory $STACK_HOST_PATH --env-file $ENV_FILE $*"
  refresh_heartbeat
  if [ -n "$ROLLBACK_IMAGE" ]; then
    docker compose -f "$COMPOSE_FILE" -f "$OVERRIDE" --project-directory "$STACK_HOST_PATH" \
      --env-file "$ENV_FILE" "$@" >> "$LOG" 2>&1
  else
    docker compose -f "$COMPOSE_FILE" --project-directory "$STACK_HOST_PATH" \
      --env-file "$ENV_FILE" "$@" >> "$LOG" 2>&1
  fi
  compose_status=$?
  refresh_heartbeat
  return $compose_status
}

stop_matter_server() {
  log "\$ docker stop matter-server"
  refresh_heartbeat
  timeout "$STOP_TIMEOUT" docker stop matter-server >> "$LOG" 2>&1
  stop_status=$?
  refresh_heartbeat
  return $stop_status
}

start_matter_server() {
  log "\$ docker start matter-server"
  timeout "$STOP_TIMEOUT" docker start matter-server >> "$LOG" 2>&1
}

# "R T" from the last "Registered R of T nodes" line matterjs-server wrote
# (measured on the test Pi, 22 November 2026: "Registered 27 of 27 nodes"),
# or nothing. Both streams: which one the line goes to is the image's
# business.
registered_nodes() {
  timeout "$PROBE_TIMEOUT" docker logs matter-server 2>&1 \
    | grep -o 'Registered [0-9][0-9]* of [0-9][0-9]* nodes' \
    | tail -n 1 | awk '{ print $2 " " $4 }'
}

matter_server_running() {
  [ "$(timeout "$PROBE_TIMEOUT" docker inspect --type container matter-server 2>/dev/null \
    | jq -r '.[0].State.Running // false' 2>/dev/null)" = true ]
}

# The repository part of an image reference: without "@digest" and without
# the ":tag" of its last path component (a registry's ":port" stays).
repository_of() {
  ref="${1%%@*}"
  case "${ref##*/}" in *:*) ref="${ref%:*}" ;; esac
  printf '%s' "$ref"
}

# ------------------------------------------------------------ the guards --

# The phases in which update-once.sh is working on the stack - the same
# list as radios-once.sh's update_running.
update_running() {
  phase="$(jq -r '.phase // "idle"' "$UPDATE_STATE" 2>/dev/null || echo idle)"
  case "$phase" in
    queued|backup|pull|build|recreate|health|rollback) return 0 ;;
  esac
  return 1
}

# A radios job that is not in a terminal phase died in its own pass;
# radios-once.sh heals it on its next one. Until then nothing here knows
# what state matter-server was left in.
radios_running() {
  [ -f "$RADIOS_STATE" ] || return 1
  phase="$(jq -r '.phase // "idle"' "$RADIOS_STATE" 2>/dev/null || echo idle)"
  case "$phase" in
    idle|done|failed|rejected|unchanged) return 1 ;;
  esac
  return 0
}

# What Compose would create, reduced to the two things this job needs: the
# image, and the host path of the volume mounted at /data. `compose config`
# prints volumes as objects with an absolute source; the string form is
# read too, in case a Compose version prints that.
# shellcheck disable=SC2016  # a jq program, expanded by jq
DESIRED='
  .services["matter-server"] as $s
  | if ($s | type) != "object" then error("no matter-server service") else . end
  | if (($s.image // "") == "") then error("no image") else . end
  | {image: $s.image,
     data: ([($s.volumes // [])[]
             | if type == "string"
               then (split(":") | {source: .[0], target: .[1]})
               else {source: .source, target: .target} end
             | select(.target == "/data") | .source]
            | last // "")}'

# ------------------------------------------------------------ the backup --

# Set by upkeep() once the data directory is known.
DATA_DIR=""
ARCHIVE=""

# The sidecar runs as root (its image sets no USER) and sees the checkout
# at /repo, so it reads the root-owned node file python-matter-server left
# behind directly - the README's helper container exists only because the
# user `pi` cannot. `--numeric-owner` both ways: the sidecar's Alpine has no
# user 1000, and the restore must give the files back to the same IDs.
backup_data() {
  mkdir -p "$BACKUP_DIR" || return 1
  BACKUP_NAME="matter-server-data-$STAMP.tgz"
  ARCHIVE="$BACKUP_DIR/$BACKUP_NAME"
  log "matter-server upkeep $JOB_ID: backing up $DATA_DIR to $ARCHIVE"
  refresh_heartbeat
  if ! tar --numeric-owner -czf "$ARCHIVE.tmp" -C "${DATA_DIR%/*}" "${DATA_DIR##*/}" >> "$LOG" 2>&1; then
    rm -f "$ARCHIVE.tmp"
    return 1
  fi
  refresh_heartbeat
  # A backup nobody listed is not a backup. A file, not just the directory
  # entry: an archive of an empty or unreadable directory is no backup of a
  # fabric.
  if ! listing="$(tar -tzf "$ARCHIVE.tmp" 2>> "$LOG")"; then
    rm -f "$ARCHIVE.tmp"
    return 1
  fi
  if ! printf '%s\n' "$listing" | grep -q '[^/]$'; then
    log "matter-server upkeep $JOB_ID: the backup contains no file"
    rm -f "$ARCHIVE.tmp"
    return 1
  fi
  mv "$ARCHIVE.tmp" "$ARCHIVE" || return 1
  log "matter-server upkeep $JOB_ID: backed up $(printf '%s\n' "$listing" | grep -c '[^/]$') files"
  # shellcheck disable=SC2012  # names this script wrote itself
  ls -1t "$BACKUP_DIR"/matter-server-data-*.tgz 2>/dev/null | tail -n +$((KEEP_BACKUPS + 1)) \
    | while read -r old; do rm -f "$old"; done
  return 0
}

# Unpacks the archive beside the data directory, and only then swaps the
# two: the data directory the failed image left is kept as
# data.failed-<stamp>. Unpacking over it instead would keep every file the
# new image created - `tar` deletes nothing (README, "If it fails").
restore_data() {
  parent="${DATA_DIR%/*}"
  base="${DATA_DIR##*/}"
  restore="$parent/$base.restore-$STAMP"
  failed="$parent/$base.failed-$STAMP"
  rm -rf "$restore"
  mkdir "$restore" || return 1
  refresh_heartbeat
  if ! tar --numeric-owner -xzpf "$ARCHIVE" -C "$restore" >> "$LOG" 2>&1 || [ ! -d "$restore/$base" ]; then
    rm -rf "$restore"
    return 1
  fi
  refresh_heartbeat
  if ! mv "$DATA_DIR" "$failed"; then
    rm -rf "$restore"
    return 1
  fi
  if ! mv "$restore/$base" "$DATA_DIR"; then
    # Put back what was there: matter-server stays stopped either way, and
    # whoever looks next finds the directory where they expect it.
    mv "$failed" "$DATA_DIR" || true
    rm -rf "$restore"
    return 1
  fi
  rmdir "$restore" 2>/dev/null || true
  log "matter-server upkeep $JOB_ID: restored $DATA_DIR from $ARCHIVE; the directory the new image left is $failed"
  return 0
}

# The failed container's own log goes with it at the rollback's recreate.
save_failed_log() {
  if timeout "$PROBE_TIMEOUT" docker logs --timestamps --tail 400 matter-server \
    > "$UPDATE_DIR/$JOB_ID.log" 2>&1; then
    log "matter-server upkeep $JOB_ID: saved the last 400 lines of its log to $JOB_ID.log"
  else
    log "matter-server upkeep $JOB_ID: could not save its log (see $JOB_ID.log)"
  fi
  # shellcheck disable=SC2012  # names this script wrote itself
  ls -1t "$UPDATE_DIR"/matter-server-upkeep-*.log 2>/dev/null | tail -n +6 \
    | while read -r old; do rm -f "$old"; done
}

# ------------------------------------------------------------ the checks --

# Waits for the recreated container's "Registered R of T nodes". Healthy is
# the same T and at least the same R as before the move, and the container
# still running. A line with fewer nodes fails at once: a server that has
# said it knows fewer nodes will not find them by waiting. Sets NODES_AFTER.
verify_nodes() {
  started="$(date +%s)"
  while [ $(($(date +%s) - started)) -lt "$VERIFY_TIMEOUT" ]; do
    refresh_heartbeat
    after="$(registered_nodes)"
    if [ -n "$after" ]; then
      after_registered="${after% *}"
      after_total="${after#* }"
      NODES_AFTER="$after_registered of $after_total"
      if [ "$after_total" -eq "$BEFORE_TOTAL" ] && [ "$after_registered" -ge "$BEFORE_REGISTERED" ]; then
        if matter_server_running; then
          log "matter-server upkeep $JOB_ID: matter-server registered $NODES_AFTER nodes, $NODES_BEFORE before"
          return 0
        fi
        log "matter-server upkeep $JOB_ID: matter-server registered $NODES_AFTER nodes but is no longer running"
        return 1
      fi
      log "matter-server upkeep $JOB_ID: matter-server registered $NODES_AFTER nodes, $NODES_BEFORE before"
      return 1
    fi
    sleep "$POLL_SECONDS"
  done
  NODES_AFTER=""
  log "matter-server upkeep $JOB_ID: matter-server did not report its nodes within ${VERIFY_TIMEOUT}s"
  return 1
}

# ------------------------------------------------------------- the upkeep --

# Records a reason not to act, for this target and this run of this
# container, and logs it once. A restart of matter-server changes its start
# time and so has the reasons looked at again.
hold() {
  printf '%s\n' "$HELD_KEY" > "$HELD"
  log "matter-server upkeep: $1"
}

upkeep() {
  [ -n "$STACK_HOST_PATH" ] || return 0
  if update_running || radios_running; then return 0; fi
  [ -f "$ENV_FILE" ] && [ -f "$COMPOSE_FILE" ] || return 0
  # `--type container`: an image called matter-server must not answer for
  # a missing container.
  inspected="$(timeout "$PROBE_TIMEOUT" docker inspect --type container matter-server 2>/dev/null)" || return 0
  container_id="$(printf '%s' "$inspected" | jq -r '.[0].Id // empty' 2>/dev/null)" || return 0
  [ -n "$container_id" ] || return 0
  started_at="$(printf '%s' "$inspected" | jq -r '.[0].State.StartedAt // empty' 2>/dev/null)" || return 0
  actual_image="$(printf '%s' "$inspected" | jq -r '.[0].Config.Image // empty' 2>/dev/null)" || return 0
  previous_image="$(printf '%s' "$inspected" | jq -r '.[0].Image // empty' 2>/dev/null)" || return 0
  [ -n "$actual_image" ] && [ -n "$previous_image" ] || return 0

  # `compose config` costs a Compose run every two seconds otherwise. Its
  # result depends on the Compose file and .env only, and is kept under
  # their checksums - a failure too, like the otbr upkeep's cache.
  key="$(checksum < "$COMPOSE_FILE") $(checksum < "$ENV_FILE")"
  fresh=false
  desired=""
  if [ -f "$CHECKED" ] && [ "$(sed -n 1p "$CHECKED")" = "$key" ]; then
    desired="$(sed -n 2p "$CHECKED")"
    [ "$desired" != failed ] || return 0
  fi
  if [ -z "$desired" ]; then
    if ! config="$(docker compose -f "$COMPOSE_FILE" --project-directory "$STACK_HOST_PATH" \
        --env-file "$ENV_FILE" config --format json 2>/dev/null)" \
      || ! desired="$(printf '%s' "$config" | jq -c "$DESIRED" 2>/dev/null)" \
      || [ -z "$desired" ]; then
      desired=failed
    fi
    fresh=true
    printf '%s\n%s\n' "$key" "$desired" > "$CHECKED.tmp"
    mv "$CHECKED.tmp" "$CHECKED"
    if [ "$desired" = failed ]; then
      log "matter-server upkeep: could not read matter-server's Compose configuration - it is left as it is"
      return 0
    fi
  fi
  desired_image="$(printf '%s' "$desired" | jq -r '.image')"
  data_source="$(printf '%s' "$desired" | jq -r '.data')"

  if [ "$desired_image" = "$actual_image" ]; then
    [ "$fresh" = false ] || log "matter-server upkeep: matter-server runs $actual_image, as its Compose configuration asks"
    return 0
  fi

  # One attempt per target: a version that loses nodes would otherwise be
  # moved to, verified and rolled back every pass, forever.
  target="$(printf '%s' "$desired_image" | checksum)"
  if [ -f "$TRIED" ] && [ "$(sed -n 1p "$TRIED")" = "$target" ]; then
    [ "$fresh" = false ] || log "matter-server upkeep: moving to $desired_image was already attempted - not again"
    return 0
  fi

  HELD_KEY="$target $container_id $started_at"
  if [ -f "$HELD" ] && [ "$(sed -n 1p "$HELD")" = "$HELD_KEY" ]; then
    return 0
  fi

  # A version, not another image: python-matter-server to matterjs-server
  # needs a chown and a verified backup first (README, "Migration to
  # matterjs-server"), and lost every node on 23 September 2026 without.
  # A container a rollback recreated from a bare image ID has no repository
  # in its reference; the image's first tag stands in for it.
  actual_ref="$actual_image"
  case "$actual_ref" in
    sha256:*)
      actual_ref="$(timeout "$PROBE_TIMEOUT" docker image inspect --format '{{index .RepoTags 0}}' \
        "$actual_image" 2>/dev/null || true)" ;;
  esac
  if [ -z "$actual_ref" ] || [ "$(repository_of "$desired_image")" != "$(repository_of "$actual_ref")" ]; then
    hold "matter-server runs $actual_image, Compose asks for $desired_image - another image, not another version of it; that move is made by hand (README, \"Migration to matterjs-server\")"
    return 0
  fi

  # The data directory has to be one this container can read and replace.
  case "$data_source" in
    "$STACK_HOST_PATH"/*) DATA_DIR="$STACK${data_source#"$STACK_HOST_PATH"}" ;;
    *)
      hold "matter-server's data directory ${data_source:-(none)} is not under $STACK_HOST_PATH - it cannot be backed up from here, so matter-server stays on $actual_image"
      return 0 ;;
  esac
  case "$DATA_DIR" in */) DATA_DIR="${DATA_DIR%/}" ;; esac
  if [ ! -d "$DATA_DIR" ]; then
    hold "matter-server's data directory $DATA_DIR does not exist here - matter-server stays on $actual_image"
    return 0
  fi

  # Pull first, while the old container keeps running, and never in the
  # same pass as the job (the otbr upkeep's reasoning, design 2026-09-14,
  # 5.4). A failed pull is no attempt; it waits PULL_RETRY for this target.
  if ! timeout "$PROBE_TIMEOUT" docker image inspect "$desired_image" >/dev/null 2>&1; then
    if [ -f "$PULL_FAILED" ]; then
      failed_at="$(sed -n '1s/ .*//p' "$PULL_FAILED")"
      failed_target="$(sed -n '1s/^[^ ]* //p' "$PULL_FAILED")"
      case "$failed_at" in ''|*[!0-9]*) failed_at=0 ;; esac
      if [ "$failed_target" = "$target" ] && [ $(($(date +%s) - failed_at)) -lt "$PULL_RETRY" ]; then
        return 0
      fi
    fi
    log "matter-server upkeep: matter-server runs $actual_image, Compose asks for $desired_image - pulling it first"
    log "\$ docker compose -f $COMPOSE_FILE --project-directory $STACK_HOST_PATH --env-file $ENV_FILE pull --quiet matter-server"
    refresh_heartbeat
    timeout "$PULL_TIMEOUT" docker compose -f "$COMPOSE_FILE" --project-directory "$STACK_HOST_PATH" \
      --env-file "$ENV_FILE" pull --quiet matter-server >> "$LOG" 2>&1 &
    pull_pid=$!
    while kill -0 "$pull_pid" 2>/dev/null; do
      refresh_heartbeat
      sleep "$POLL_SECONDS"
    done
    if wait "$pull_pid"; then
      rm -f "$PULL_FAILED"
      log "matter-server upkeep: pulled $desired_image - matter-server is moved to it on the next pass"
    else
      printf '%s %s\n' "$(date +%s)" "$target" > "$PULL_FAILED"
      log "matter-server upkeep: could not pull $desired_image - matter-server keeps running $actual_image, trying again in ${PULL_RETRY}s"
    fi
    return 0
  fi

  # The count the move is judged by (design, section 7). Without it nothing
  # could tell a good move from the one of 23 September.
  before="$(registered_nodes)"
  if [ -z "$before" ]; then
    hold "matter-server's log no longer says how many nodes it registered, so a move to $desired_image could not be checked - it stays on $actual_image. \`docker restart matter-server\` writes the line again"
    return 0
  fi
  BEFORE_REGISTERED="${before% *}"
  BEFORE_TOTAL="${before#* }"

  STAMP="$(date -u +%Y%m%d%H%M%S)"
  JOB_ID="matter-server-upkeep-$STAMP"
  FROM="$actual_image"
  TO="$desired_image"
  NODES_BEFORE="$BEFORE_REGISTERED of $BEFORE_TOTAL"
  NODES_AFTER=""
  BACKUP_NAME=""
  ROLLED=false
  HEALTHY=null
  # Before anything changes: a pass killed in the middle of this job is
  # healed by the next pass and must not be started again by it.
  printf '%s\n' "$target" > "$TRIED"
  rm -f "$HELD"
  log "matter-server upkeep $JOB_ID: matter-server runs $actual_image with $NODES_BEFORE nodes, Compose asks for $desired_image - moving it"

  # Stopped before the backup, so the archive does not race the server's
  # own writes.
  write_state stop ""
  if ! stop_matter_server; then
    start_matter_server || true
    write_state failed stop_failed
    log "matter-server upkeep $JOB_ID: could not stop matter-server - nothing was changed"
    return 0
  fi

  write_state backup ""
  if ! backup_data; then
    if start_matter_server; then HEALTHY=true; else HEALTHY=false; fi
    write_state failed backup_failed
    log "matter-server upkeep $JOB_ID: the backup of $DATA_DIR failed - matter-server was started again on $actual_image"
    return 0
  fi

  write_state recreate ""
  FAILED_STEP=""
  if compose up -d --no-deps --force-recreate --quiet-pull matter-server; then
    write_state verify ""
    if verify_nodes; then
      HEALTHY=true
      write_state "done" ""
      log "matter-server upkeep $JOB_ID: matter-server runs $desired_image"
      return 0
    fi
    FAILED_STEP=verify
  else
    FAILED_STEP=recreate
  fi

  ERROR_KEY="${FAILED_STEP}_failed"
  ROLLED=true
  write_state rollback "$ERROR_KEY"
  log "matter-server upkeep $JOB_ID: $FAILED_STEP failed - going back to $actual_image ($previous_image) and the backup"
  save_failed_log
  stop_matter_server || true
  if ! restore_data; then
    HEALTHY=false
    write_state failed restore_failed
    log "matter-server upkeep $JOB_ID: could not restore $DATA_DIR from $ARCHIVE - matter-server is left STOPPED, so it cannot start on a half-restored directory. Restore it by hand (README, \"Backing up the fabric volume\")"
    return 0
  fi
  # By the reference it was created from while that still names the same
  # image on this host, so the next release's move still sees a version of
  # the same repository (repository_of below); by the image ID otherwise.
  # Either way the previous image is on the host, and nothing is pulled.
  ROLLBACK_IMAGE="$previous_image"
  if [ "$(timeout "$PROBE_TIMEOUT" docker image inspect --format '{{.Id}}' "$actual_image" 2>/dev/null)" = "$previous_image" ]; then
    ROLLBACK_IMAGE="$actual_image"
  fi
  printf 'services:\n  matter-server:\n    image: "%s"\n' "$ROLLBACK_IMAGE" > "$OVERRIDE"
  if compose up -d --no-deps --force-recreate matter-server && verify_nodes; then
    HEALTHY=true
  else
    HEALTHY=false
  fi
  ROLLBACK_IMAGE=""
  write_state failed "$ERROR_KEY"
  log "matter-server upkeep $JOB_ID rolled back, healthy after rollback: $HEALTHY"
}

# ------------------------------------------------------------------ pass --

load_previous_state
if [ -n "$INTERRUPTED_PHASE" ]; then
  write_state failed interrupted
  log "matter-server upkeep ${JOB_ID:-?}: the previous pass died in phase $INTERRUPTED_PHASE - recorded as failed (interrupted)"
  # Killed after the stop and before the recreate: the old container is
  # stopped with its data untouched, and `restart: unless-stopped` does not
  # bring a stopped container back. In any later phase what runs there is
  # not known, and nothing here touches it.
  case "$INTERRUPTED_PHASE" in
    stop|backup)
      if start_matter_server; then
        log "matter-server upkeep $JOB_ID: started the old matter-server again"
      else
        log "matter-server upkeep $JOB_ID: could not start the old matter-server again"
      fi ;;
  esac
  exit 0
fi

upkeep
exit 0
