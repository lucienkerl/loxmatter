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


# Takes over Home Assistant's Matter server and Thread border router on the
# same host, and gives them back.
#
#   ./scripts/home-assistant-handover.sh check       # read only: what would happen
#   ./scripts/home-assistant-handover.sh take        # loxmatter runs both
#   ./scripts/home-assistant-handover.sh give-back   # Home Assistant runs both again
#
# Why: one host cannot run two Matter servers (both want port 5580) or two
# border routers on one radio stick. Home Assistant Container runs both as
# containers of its own. `take` stops them and starts loxmatter's instead:
#
#   - loxmatter's Matter server starts on a COPY of Home Assistant's Matter
#     data - the same fabric, so no device is paired again. Home Assistant's
#     Matter integration talks to ws://localhost:5580/ws and reconnects to
#     loxmatter's server by itself; Home Assistant keeps its Matter devices.
#     matterjs-server reads python-matter-server's files on its first start
#     (deploy/testhost/README.md, "Migration to matterjs-server").
#   - loxmatter's border router gets Home Assistant's radio stick and the
#     same Thread network: the active dataset is read from Home Assistant's
#     border router before it stops and written into loxmatter's before
#     loxmatter itself starts - otherwise the bridge would form a new
#     network and the Thread devices would sit in the old one alone.
#
# Nothing of Home Assistant's is deleted or written to: its containers are
# stopped, renamed out of the way (loxmatter's Matter server is called
# `matter-server` too) and kept from starting at boot. Their data stays where
# it is. `give-back` undoes exactly that, from the record `take` leaves in
# deploy/testhost/.homeassistant-handover.
#
# The Thread dataset is the network key. It passes through a shell variable
# only and is printed nowhere.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STACK="${LOXMATTER_STACK_DIR:-$SCRIPT_DIR/../deploy/testhost}"
# Printed in every message: /home/pi/loxmatter/deploy/testhost reads better
# than /home/pi/loxmatter/scripts/../deploy/testhost.
STACK="$(cd "$STACK" 2>/dev/null && pwd -P || printf '%s' "$STACK")"
RECORD="$STACK/.homeassistant-handover"
DATA="$STACK/data"
# How long loxmatter's border router may take to answer its REST API.
READY_SECONDS="${HANDOVER_READY_SECONDS:-60}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
die() {
  printf '\033[31mError: %s\033[0m\n' "$*" >&2
  exit 1
}

# Docker as this user when it may, through sudo when it may not - the
# installer's rule (`pi` outside the docker group).
dk() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    sudo docker "$@"
  fi
}

# Root for the Matter files: python-matter-server ran as root and wrote its
# node file with mode 600, and matterjs-server runs as UID 1000. Without the
# chown it starts with zero nodes and says nothing (23 September 2026).
as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

env_value() {
  sed -n "s/^$1=//p" "$STACK/.env" 2>/dev/null | tail -n 1
}

# ------------------------------------------------------------ discovery --

HA_MATTER=""
HA_OTBR=""
HA_MATTER_DATA=""
HA_RADIO=""
HA_BAUD=""
HA_REST_PORT=""

# Home Assistant's containers by image, not by name: a compose project
# names its containers after itself (`homeassistant-otbr-1`), and only
# loxmatter's own border router is called `otbr`.
discover() {
  local name image
  while IFS=$'\t' read -r name image; do
    [ -n "$name" ] || continue
    case "$image" in
      *python-matter-server*) HA_MATTER="$name" ;;
      *border-router*|*/otbr:*|*/otbr)
        if [ "$name" != "otbr" ]; then HA_OTBR="$name"; fi
        ;;
    esac
  done < <(dk ps -a --format '{{.Names}}\t{{.Image}}')

  if [ -n "$HA_MATTER" ]; then
    HA_MATTER_DATA="$(dk inspect -f '{{range .Mounts}}{{if or (eq .Destination "/data") (eq .Destination "/data/")}}{{.Source}}{{end}}{{end}}' "$HA_MATTER")"
  fi
  if [ -n "$HA_OTBR" ]; then
    # The stick as the host names it (a by-id path, ideally), the baud rate
    # and the REST port from the container's own settings.
    HA_RADIO="$(dk inspect -f '{{range .HostConfig.Devices}}{{.PathOnHost}}{{"\n"}}{{end}}' "$HA_OTBR" | grep -v '/dev/net/tun' | head -n 1)"
    local env_lines
    env_lines="$(dk inspect -f '{{range .Config.Env}}{{.}}{{"\n"}}{{end}}' "$HA_OTBR")"
    # Privileged, as in Home Assistant's own compose example: no device is
    # listed, the stick is only named in OT_RCP_DEVICE.
    if [ -z "$HA_RADIO" ]; then
      HA_RADIO="$(printf '%s\n' "$env_lines" | sed -n 's#^OT_RCP_DEVICE=[^:]*://\(/[^?]*\).*#\1#p' | head -n 1)"
    fi
    HA_BAUD="$(printf '%s\n' "$env_lines" | sed -n 's/.*uart-baudrate=\([0-9]*\).*/\1/p' | head -n 1)"
    HA_REST_PORT="$(printf '%s\n' "$env_lines" | sed -n 's/^OT_REST_LISTEN_PORT=\([0-9]*\)$/\1/p' | head -n 1)"
    HA_REST_PORT="${HA_REST_PORT:-8081}"
  fi
}

# The device a path finally points at - a by-id link and /dev/ttyUSB0 are
# the same stick - or the path itself where it does not exist.
same_device() {
  local real
  real="$(readlink -f "$1" 2>/dev/null)" || real=""
  printf '%s' "${real:-$1}"
}

show_findings() {
  say "Home Assistant on this host"
  if [ -n "$HA_MATTER" ]; then
    note "Matter server: container $HA_MATTER, data in ${HA_MATTER_DATA:-?}"
  else
    note "Matter server: none found"
  fi
  if [ -n "$HA_OTBR" ]; then
    note "Thread border router: container $HA_OTBR, stick ${HA_RADIO:-?} at ${HA_BAUD:-?} baud, REST port $HA_REST_PORT"
  else
    note "Thread border router: none found"
  fi
}

# ------------------------------------------------------------------ take --

thread_dataset_from_home_assistant() {
  local dataset
  dataset="$(curl -sf --max-time 5 -H 'Accept: text/plain' "http://127.0.0.1:$HA_REST_PORT/node/dataset/active" || true)"
  dataset="$(printf '%s' "$dataset" | tr -d '[:space:]')"
  case "$dataset" in
    ""|*[!0-9a-fA-F]*) return 1 ;;
  esac
  printf '%s' "$dataset"
}

wait_for_border_router() {
  local port="$1" waited=0
  while [ "$waited" -lt "$READY_SECONDS" ]; do
    if curl -sf --max-time 3 "http://127.0.0.1:$port/node/state" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

take() {
  [ ! -f "$RECORD" ] || die "Already taken over (see $RECORD). Run give-back first to start again."
  [ -f "$STACK/.env" ] || die "No $STACK/.env yet - run install.sh first."
  discover
  show_findings
  [ -n "$HA_MATTER" ] || die "No Home Assistant Matter server found - nothing to take over."
  [ -n "$HA_MATTER_DATA" ] || die "Could not find the data directory of $HA_MATTER. Nothing was changed."
  as_root test -f "$HA_MATTER_DATA/chip.json" ||
    die "$HA_MATTER_DATA has no chip.json - that is not a Matter server's data. Nothing was changed."
  if [ -d "$DATA" ] && [ -n "$(ls -A "$DATA" 2>/dev/null)" ]; then
    die "$DATA is not empty - loxmatter already has Matter data of its own. Nothing was changed."
  fi

  local dataset="" rest_port
  rest_port="$(env_value OTBR_REST_PORT)"
  rest_port="${rest_port:-8081}"
  if [ -n "$HA_OTBR" ]; then
    [ "$(same_device "$(env_value RADIO_DEVICE)")" = "$(same_device "$HA_RADIO")" ] ||
      die "RADIO_DEVICE in .env is not Home Assistant's stick ($HA_RADIO). Nothing was changed."
    # OTBR_REST_PORT may equal Home Assistant's port - the installer picks
    # it so that Home Assistant's OTBR integration keeps a border router to
    # talk to. Theirs is stopped before ours starts, and ours before theirs
    # on give-back, so the two never hold it at once.
    dataset="$(thread_dataset_from_home_assistant)" ||
      die "Could not read the Thread network from Home Assistant's border router on port $HA_REST_PORT. Nothing was changed."
    note "Thread network read from $HA_OTBR"
  fi

  say "Writing down what is about to change"
  {
    printf 'HA_MATTER=%s\n' "$HA_MATTER"
    printf 'HA_MATTER_RESTART=%s\n' "$(dk inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$HA_MATTER")"
    if [ -n "$HA_OTBR" ]; then
      printf 'HA_OTBR=%s\n' "$HA_OTBR"
      printf 'HA_OTBR_RESTART=%s\n' "$(dk inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$HA_OTBR")"
    fi
  } > "$RECORD"
  note "$RECORD"

  say "Stopping Home Assistant's Matter server and border router"
  # From here on Home Assistant has no Matter server until loxmatter's runs.
  dk update --restart=no "$HA_MATTER" >/dev/null
  dk stop "$HA_MATTER" >/dev/null
  dk rename "$HA_MATTER" "$HA_MATTER-homeassistant"
  note "$HA_MATTER stopped, now called $HA_MATTER-homeassistant"
  if [ -n "$HA_OTBR" ]; then
    dk update --restart=no "$HA_OTBR" >/dev/null
    dk stop "$HA_OTBR" >/dev/null
    note "$HA_OTBR stopped"
  fi

  say "Copying Home Assistant's Matter data"
  as_root mkdir -p "$DATA"
  as_root cp -a "$HA_MATTER_DATA/." "$DATA/"
  as_root chown -R 1000:1000 "$DATA"
  note "$HA_MATTER_DATA -> $DATA (the original stays as it is)"

  if [ -n "$HA_OTBR" ]; then
    say "Starting loxmatter's border router on the same Thread network"
    (cd "$STACK" && dk compose up -d otbr)
    wait_for_border_router "$rest_port" ||
      die "loxmatter's border router does not answer on port $rest_port. Run give-back to return to Home Assistant."
    curl -sf --max-time 10 -X PUT -H 'Content-Type: text/plain' --data "$dataset" \
      "http://127.0.0.1:$rest_port/node/dataset/active" >/dev/null ||
      die "loxmatter's border router refused the Thread network. Run give-back to return to Home Assistant."
    curl -sf --max-time 10 -X PUT -H 'Content-Type: application/json' --data '"enable"' \
      "http://127.0.0.1:$rest_port/node/state" >/dev/null ||
      die "loxmatter's border router did not start Thread. Run give-back to return to Home Assistant."
    note "Thread is up on Home Assistant's network"
  fi

  say "Starting loxmatter's Matter server and the bridge"
  (cd "$STACK" && dk compose up -d)
  note "Home Assistant's Matter integration reconnects to ws://localhost:5580/ws by itself."
  note "To undo: $0 give-back"
}

# ------------------------------------------------------------- give-back --

give_back() {
  [ -f "$RECORD" ] || die "Nothing to give back: $RECORD does not exist."
  local HA_MATTER="" HA_MATTER_RESTART="" HA_OTBR="" HA_OTBR_RESTART=""
  # shellcheck disable=SC1090
  . "$RECORD"
  [ -n "$HA_MATTER" ] || die "$RECORD names no Matter server."

  say "Stopping loxmatter's Matter server and border router"
  (cd "$STACK" && dk compose stop loxmatter matter-server otbr) || true
  # Out of the way of Home Assistant's container name.
  (cd "$STACK" && dk compose rm -f matter-server) >/dev/null 2>&1 || true

  say "Starting Home Assistant's Matter server and border router"
  dk rename "$HA_MATTER-homeassistant" "$HA_MATTER" 2>/dev/null || true
  dk update --restart="${HA_MATTER_RESTART:-unless-stopped}" "$HA_MATTER" >/dev/null
  dk start "$HA_MATTER" >/dev/null
  note "$HA_MATTER runs again on its own, untouched data"
  if [ -n "$HA_OTBR" ]; then
    dk update --restart="${HA_OTBR_RESTART:-unless-stopped}" "$HA_OTBR" >/dev/null
    dk start "$HA_OTBR" >/dev/null
    note "$HA_OTBR runs again"
  fi
  rm -f "$RECORD"
  note "Devices paired through loxmatter since the take-over are not known to Home Assistant's Matter server."
  note "loxmatter's copy of the Matter data stays in $DATA."
}

case "${1:-}" in
  check)
    discover
    show_findings
    ;;
  take) take ;;
  give-back) give_back ;;
  *)
    printf 'Usage: %s check | take | give-back\n' "$0" >&2
    exit 2
    ;;
esac
