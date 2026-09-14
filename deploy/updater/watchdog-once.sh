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
# The watchdog job of the updater sidecar - design "Thread setup without
# handwork" (2026-09-14), section 4.2. One pass: run
# scripts/otbr-watchdog.sh from the checkout under /repo. The script runs
# from there, not from a copy baked into this image, so a later fix to
# the watchdog arrives with the ordinary bridge update, without an
# updater refresh - and nobody needs a crontab line on the host any more,
# entrypoint.sh calls this every minute on its own (see its own comment).

set -u

UPDATE_DIR="${LOXMATTER_UPDATE_DIR:-/data/update}"
SCRIPT="${LOXMATTER_WATCHDOG_SCRIPT:-/repo/scripts/otbr-watchdog.sh}"
LOG="$UPDATE_DIR/otbr-watchdog.log"

# The updater may meet an older checkout - a bridge rollback to 0.4.0, say.
# That version of the script still reads /proc/net/if_inet6 to see whether
# Thread is up, which a container outside the host's network namespace
# cannot see, and would restart otbr every minute believing Thread is
# always down. The marker line is how a container-ready script identifies
# itself; without it, doing nothing is the safe choice, not running it and
# hoping. OTBR_WATCHDOG_LOCK is left exactly as the environment gave it:
# unset, so the script below locks itself, the same file the /repo bind
# mount also gives a host cron job - the shared inode is the whole point
# (spec section 4.5).
[ -f "$SCRIPT" ] || exit 0
grep -q '^# loxmatter-watchdog: container-ready' "$SCRIPT" || exit 0

mkdir -p "$UPDATE_DIR" 2>/dev/null || true

# Keep the log from growing without bound, newest lines kept. A failure
# here - disk full, an unwritable directory - must never stop the watchdog
# from running: it having run is what matters, not whether its log got
# trimmed on this particular pass.
trimmed="$UPDATE_DIR/otbr-watchdog.log.trimmed"
if [ -f "$LOG" ] && tail -n 2000 "$LOG" >"$trimmed" 2>/dev/null; then
  mv "$trimmed" "$LOG" 2>/dev/null || true
fi

# Both heartbeats the web UI reads stay fresh while the watchdog runs. The
# three workers share one loop, so nothing else writes them meanwhile, and
# a restart run (docker restart, then up to a minute of waiting for the
# network) is longer than the 30 s the bridge allows before it calls the
# updater outdated or missing - the Radios card and System would then tell
# the user to run console commands, in the middle of the very outage they
# came to look at. Only the timestamp field is rewritten, the same way
# radios-once.sh's refresh_heartbeat does, so no job record is disturbed.
touch_seen_at() {
  [ -f "$1" ] || return 0
  refreshed="$(jq --arg seen "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "if type == \"object\" and has(\"phase\") then $2 = \$seen else empty end" \
    "$1" 2>/dev/null || true)"
  [ -n "$refreshed" ] || return 0
  if printf '%s\n' "$refreshed" > "$1.watchdog-tmp" 2>/dev/null; then
    mv "$1.watchdog-tmp" "$1" 2>/dev/null || rm -f "$1.watchdog-tmp" 2>/dev/null
  else
    rm -f "$1.watchdog-tmp" 2>/dev/null
  fi
  return 0
}

RUN_TIMEOUT="${LOXMATTER_WATCHDOG_RUN_TIMEOUT:-280}"
POLL_SECONDS="${LOXMATTER_WATCHDOG_POLL_SECONDS:-1}"

# Its own limit, under entrypoint.sh's 300 s for this worker, and a TERM
# forwarded to it: entrypoint.sh's `timeout` signals only this shell, and a
# worst-case watchdog run is longer than the worker's limit. Left running
# as an orphan, bash would keep the lock and go on restarting otbr unseen.
if command -v timeout >/dev/null 2>&1; then
  timeout -k 10 "$RUN_TIMEOUT" bash "$SCRIPT" >>"$LOG" 2>&1 &
else
  bash "$SCRIPT" >>"$LOG" 2>&1 &
fi
run_pid=$!
trap 'kill -TERM "$run_pid" 2>/dev/null' TERM INT
while kill -0 "$run_pid" 2>/dev/null; do
  touch_seen_at "$UPDATE_DIR/state.json" .updater_seen_at
  touch_seen_at "$UPDATE_DIR/radios-state.json" .seen_at
  sleep "$POLL_SECONDS"
done
wait "$run_pid" 2>/dev/null || true
exit 0
