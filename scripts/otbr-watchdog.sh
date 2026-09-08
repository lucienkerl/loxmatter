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
#   */5 * * * * /home/pi/matter-loxone/scripts/otbr-watchdog.sh >> /home/pi/otbr-watchdog.log 2>&1
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
# Deliberately NO restart loop: if the restart fails because the radio
# module itself is stuck, retrying every minute wouldn't help and would
# just flood the log. At that point someone has to look - and finds what
# happened in the log.
set -euo pipefail

SERVICE="otbr"
STACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/testhost" && pwd)"
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# In WiFi/Ethernet-only operation (COMPOSE_PROFILES without "thread", see
# deploy/testhost/.env) this service doesn't exist at all. Without this
# brake the watchdog would never find a Thread interface, would try a
# restart every five minutes and write a failure to the log every time -
# a watchdog would turn into an avalanche.
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
if ! CONTAINERS=$(docker ps -a --format '{{.Names}}' 2>&1); then
  printf '%s  docker ps failed - cannot check the otbr container:\n' "$STAMP"
  printf '%s\n' "$CONTAINERS" | sed 's/^/    /'
  exit 1
fi
if ! printf '%s\n' "$CONTAINERS" | grep -qx "$SERVICE"; then
  exit 0
fi

thread_is_up() {
  # Scope 00 means routed (ULA included); wpan* is OTBR's Thread
  # interface.
  awk '$4 == "00" && $6 ~ /^wpan/ { found = 1 } END { exit !found }' /proc/net/if_inet6
}

if thread_is_up; then
  exit 0
fi

printf '%s  No Thread interface - restarting %s\n' "$STAMP" "$SERVICE"
if ! (cd "$STACK" && docker compose restart "$SERVICE" >/dev/null 2>&1); then
  printf '%s  Restarting %s failed\n' "$STAMP" "$SERVICE"
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
docker logs --tail 20 "$SERVICE" 2>&1 | sed 's/^/    /' || true
exit 1
