#!/usr/bin/env bash
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


# Brings the OTBR agent back if it has died.
#
# Meant for a cron entry, see deploy/testhost/README.md:
#
#   * * * * * /home/pi/matter-loxone/scripts/otbr-watchdog.sh >> /home/pi/otbr-watchdog.log 2>&1
#
# No `flock` in the cron line: the script takes its own lock, below.
#
# WHY this is needed: the OTBR agent aborts if the radio module stops
# responding (RCP timeout - a USB dropout, power supply, the module
# itself). The CONTAINER keeps running regardless, because its entrypoint
# script is not the agent. `restart: unless-stopped` therefore doesn't
# kick in, and the image ships no watchdog of its own. On 2026-09-03 such
# an outage went unnoticed for six and a half hours; no device was
# reachable during that time.
#
# The check is the same one the "System" view shows: does a Thread
# interface (wpan*) with a mesh address exist? It disappears along with
# the agent.
#
# Deliberately NO restart loop inside a run: one restart, up to 60 s of
# waiting, done. If the radio module itself is stuck, retrying would not
# help. Cron starts a run every minute, so a recovery takes about a minute
# instead of five; two things keep that from turning into restarts on top
# of each other:
#
# - a lock: a run that is still waiting for the network makes the next run
#   exit quietly instead of restarting the agent under it;
# - a grace period: an otbr container started less than 90 s ago is left
#   alone. That covers boot, an update, and this script's own restart - a
#   normal attach takes 22-35 s on the Pi, and an agent restarted in the
#   middle of one starts over.
#
# Every docker call has a time limit. A docker daemon that hangs would
# otherwise keep this run - and its lock - alive for good, and every later
# run would exit quietly on the lock: the watchdog would stop without a
# single line in its log.
#
# A stuck module therefore gets one restart attempt, and its log lines,
# about every two minutes until someone looks - and finds what happened in
# the log.
set -euo pipefail

SERVICE="otbr"
# Overridable for the same reason `install.sh` makes RFKILL_DIR
# overridable: otherwise the check can only be exercised on a host that
# happens to have - or happens to lack - a Thread interface, and the
# tests for it would assert nothing on the very Pi this runs on.
IF_INET6="${IF_INET6:-/proc/net/if_inet6}"
STACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/testhost" && pwd)"
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"
# The lock is this script file itself, opened for reading: it always exists
# and is readable by whoever runs the script, so no lock file is left in
# /tmp that a run as root could create and a run as pi then fail to open.
# Overridable so the tests can give each run a lock of its own.
LOCK_FILE="${OTBR_WATCHDOG_LOCK:-${BASH_SOURCE[0]}}"
GRACE_SECONDS=90
# Limits for one docker call, overridable so the tests need not wait them
# out. A restart stops and starts the container, which takes longer than a
# query.
DOCKER_TIMEOUT="${OTBR_WATCHDOG_DOCKER_TIMEOUT:-30}"
RESTART_TIMEOUT="${OTBR_WATCHDOG_RESTART_TIMEOUT:-120}"

# `bounded SECONDS COMMAND...` runs COMMAND with a time limit: SIGTERM when
# it runs out, SIGKILL ten seconds later if that did not end it. `timeout`
# is GNU coreutils, which Raspberry Pi OS always has. Where it is missing (a
# developer's Mac without coreutils) the call runs unbounded, as it did
# before there was a limit.
if command -v timeout >/dev/null 2>&1; then
  bounded() { timeout -k 10 "$@"; }
else
  bounded() {
    shift
    "$@"
  }
fi

# " (no answer within N s)" when a bounded call's status says `timeout`
# stopped it - 124 after SIGTERM, 137 after SIGKILL - and nothing otherwise.
timed_out() {
  if [ "$1" -eq 124 ] || [ "$1" -eq 137 ]; then
    printf ' (no answer within %s s)' "$2"
  fi
}

# One run at a time. `flock` is util-linux, present on Raspberry Pi OS; where
# it is missing (a developer's Mac running the tests) the run goes ahead
# without a lock, as it did before there was one.
if command -v flock >/dev/null 2>&1; then
  exec 9<"$LOCK_FILE"
  if ! flock -n 9; then
    exit 0
  fi
fi

# In WiFi/Ethernet-only operation (COMPOSE_PROFILES without "thread", see
# deploy/testhost/.env) this service doesn't exist at all. Without this
# brake the watchdog would never find a Thread interface, would try a
# restart every minute and write a failure to the log every time - a
# watchdog would turn into an avalanche.
#
# Important: this is ONLY the check for whether otbr is configured at
# all - not whether docker works. Under `set -euo pipefail`, a missing or
# not-running docker would cause `docker ps` to end with empty output and
# an error status, `grep` would find nothing (status 1), pipefail would
# raise that status to the pipeline, and `!` would turn it into a silent
# 0 - a broken docker would then look exactly like "no Thread mode" and
# the restart attempt further below (including its log entry on failure)
# would never be reached. So check separately: if the docker query itself
# fails, that's a real error and must be logged; only a successful query
# that doesn't list otbr may exit quietly.
STATUS=0
CONTAINERS=$(bounded "$DOCKER_TIMEOUT" docker ps -a --format '{{.Names}}' 2>&1) || STATUS=$?
if [ "$STATUS" -ne 0 ]; then
  printf '%s  docker ps failed%s - cannot check the otbr container:\n' \
    "$STAMP" "$(timed_out "$STATUS" "$DOCKER_TIMEOUT")"
  printf '%s\n' "$CONTAINERS" | sed 's/^/    /'
  exit 1
fi
if ! printf '%s\n' "$CONTAINERS" | grep -qx "$SERVICE"; then
  exit 0
fi

thread_is_up() {
  # Scope 00 means routed (ULA included); wpan* is OTBR's Thread
  # interface.
  awk '$4 == "00" && $6 ~ /^wpan/ { found = 1 } END { exit !found }' "$IF_INET6"
}

if thread_is_up; then
  exit 0
fi

# A container that has only just started is still attaching: leave it be.
#
# Its age is the age of the container's main process, as the kernel counts
# it - not the difference between docker's StartedAt and this machine's
# clock. A Pi has no real-time clock: at boot its clock is wherever it was
# at shutdown until NTP steps it, possibly by days, and a clock stepped
# backwards made a container started long ago look as if it had not started
# yet. `ps -o etimes` counts from a monotonic clock and is immune to both.
#
# Anything that cannot be read - no pid, a stopped container's pid 0, no
# such process, an answer that is not a plain number of seconds - skips the
# grace period, never the restart: a watchdog that stays quiet because of a
# format would be the outage of 3 September again. That is also why the
# number is checked before the arithmetic below: bash reads an empty or
# non-numeric value there as 0, and a negative or overlong one as a small
# number, all of which would mean "just started".
AGE=""
PID="$(bounded "$DOCKER_TIMEOUT" docker inspect -f '{{.State.Pid}}' "$SERVICE" 2>/dev/null || true)"
case "$PID" in
  '' | *[!0-9]* | 0*) ;;
  *) AGE="$(bounded "$DOCKER_TIMEOUT" ps -o etimes= -p "$PID" 2>/dev/null || true)" ;;
esac
AGE="${AGE//[[:space:]]/}"
case "$AGE" in
  # Empty, not digits only, a leading zero, or ten digits and more.
  '' | *[!0-9]* | 0?* | ??????????*)
    printf '%s  Could not read how long %s has been running - no grace period\n' \
      "$STAMP" "$SERVICE"
    ;;
  *)
    if ((AGE < GRACE_SECONDS)); then
      exit 0
    fi
    ;;
esac

printf '%s  No Thread interface - restarting %s\n' "$STAMP" "$SERVICE"

# The agent's pid file lives in the container's WRITABLE LAYER and so
# survives a restart, while the container's PID namespace starts over at
# 1. The file then names a pid the new container has already handed to
# some other process, and `/etc/init.d/otbr-agent`'s start guard - which
# asks whether that pid is alive, not whether it is the agent - answers
# "thread border agent already started; not starting". The container
# comes up with no Thread daemon at all, `docker ps` still reports "Up",
# and the next run of this watchdog is the first chance to recover.
#
# Measured on the Pi on 11 September 2026: /run/otbr-agent.pid still held
# 97 from a start three days earlier, and 97 is also the pid the agent
# gets on a fresh start of this image - so the collision is systematic,
# not bad luck. That incident, when this watchdog still ran every five
# minutes, cost five minutes of Thread outage on top of the one the radio
# module had already caused.
#
# Before the restart and not after: afterwards would delete the pid file
# of the agent that has just started. `/var/run` is a symlink to `/run`
# in this image, so one path covers both.
#
# A failure here is deliberately not fatal. The container may be stopped
# outright - exactly a case this watchdog exists to recover from - and
# the restart below is what recovers it.
STATUS=0
bounded "$DOCKER_TIMEOUT" docker exec "$SERVICE" rm -f /run/otbr-agent.pid >/dev/null 2>&1 || STATUS=$?
if [ "$STATUS" -ne 0 ]; then
  printf '%s  Could not clear the stale pid file%s - restarting anyway\n' \
    "$STAMP" "$(timed_out "$STATUS" "$DOCKER_TIMEOUT")"
fi

STATUS=0
(cd "$STACK" && bounded "$RESTART_TIMEOUT" docker compose restart "$SERVICE" >/dev/null 2>&1) || STATUS=$?
if [ "$STATUS" -ne 0 ]; then
  printf '%s  Restarting %s failed%s\n' "$STAMP" "$SERVICE" "$(timed_out "$STATUS" "$RESTART_TIMEOUT")"
  exit 1
fi

# Give the agent time to rejoin the network. About 10 s was observed;
# 60 s of patience leaves headroom without waiting forever on a real
# fault.
for _ in $(seq 1 12); do
  sleep 5
  if thread_is_up; then
    printf '%s  Thread network is back\n' "$(date '+%Y-%m-%d %H:%M:%S')"
    exit 0
  fi
done

printf '%s  Still no Thread interface after 60 s. Is the radio module stuck?\n' \
  "$(date '+%Y-%m-%d %H:%M:%S')"
printf '%s  Last lines from the OTBR log:\n' "$(date '+%Y-%m-%d %H:%M:%S')"
bounded "$DOCKER_TIMEOUT" docker logs --tail 20 "$SERVICE" 2>&1 | sed 's/^/    /' || true
exit 1
