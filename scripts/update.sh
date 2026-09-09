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


# Brings the running bridge to the state of the published version.
#
#   ./scripts/update.sh              # fetch, pull image, restart
#   ./scripts/update.sh --no-pull    # pull and restart only
#   ./scripts/update.sh --build      # build from source instead of pulling
#   ./scripts/update.sh --build --no-cache   # build without layer cache
#
# Run this on the machine where the bridge runs. The stack lives inside the
# repository itself (deploy/testhost/), the script finds it via its own
# path - no configuration step needed.
#
# Since 0.2.0 we pull instead of building: building took five to ten
# minutes on the test Pi and could fail on a PyPI outage or
# out of memory. --build restores the old way for
# development and for hosts without access to the registry.
#
# The service is started with `--no-deps`: matter-server and OTBR remain
# untouched. Without this, Compose recreates them whenever
# the project configuration changes - and OTBR's Thread state is tied to
# a volume that a rebuild survives, but a restart of the
# Thread network for no reason is not part of an update.
set -euo pipefail

PULL=1
BUILD=0
NO_CACHE=""
for arg in "$@"; do
  case "$arg" in
    --no-pull)  PULL=0 ;;
    --build)    BUILD=1 ;;
    --no-cache) NO_CACHE=1 ;;
    -h|--help)  sed -n '18,39p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)          printf 'Unknown argument: %s (allowed: --no-pull, --build, --no-cache, --help)\n' "$arg" >&2; exit 2 ;;
  esac
done
# --no-cache controls a build. Without --build it does nothing at all, and
# a switch that silently has no effect is worse than
# one that is missing: it makes someone believe they built fresh.
if [ -n "$NO_CACHE" ] && [ "$BUILD" -eq 0 ]; then
  printf 'Aborting: --no-cache only works together with --build.\n' >&2
  exit 2
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK="$REPO/deploy/testhost"
SERVICE="loxmatter"
BACKUPS="$HOME/loxmatter-backups"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\n\033[31mAborting: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$REPO/Dockerfile" ] || die "No Dockerfile in $REPO - is the script running from the repository?"
[ -f "$STACK/docker-compose.yml" ] || die "No docker-compose.yml in $STACK."
grep -q "^  ${SERVICE}:" "$STACK/docker-compose.yml" || die "The compose file has no service '${SERVICE}'."
command -v docker >/dev/null || die "docker is not installed."

if [ "$PULL" -eq 1 ]; then
  say "Fetching the latest state"
  git -C "$REPO" pull --ff-only || die "git pull failed - local changes in the way?"
fi
printf '  %s (%s)\n' "$REPO" "$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo 'no commit')"
if [ -n "$(git -C "$REPO" status --porcelain 2>/dev/null)" ]; then
  printf '\033[33m  Note: the working tree is not clean. Whatever is there will be shipped.\033[0m\n'
fi

# The signal database is the one thing a botched update couldn't restore:
# it holds the signal keys, and those are the wiring in the Loxone
# configuration. A copy comes before anything else.
#
# The volume name is made up of the project name, and that's fixed in the
# compose file (`name:`) - which is exactly why it lives there and not in
# an .env that someone could regenerate.
PROJECT="$(awk '/^name:/ {print $2; exit}' "$STACK/docker-compose.yml")"
[ -n "$PROJECT" ] || die "No 'name:' in the compose file - without a project name the volume name can't be determined."
VOLUME="${PROJECT}_loxmatter-store"
if docker volume inspect "$VOLUME" >/dev/null 2>&1; then
  say "Backing up the signal database"
  mkdir -p "$BACKUPS"
  STAMP="$(date +%Y-%m-%d-%H%M%S)"
  docker run --rm -v "$VOLUME:/data:ro" -v "$BACKUPS:/backup" alpine:latest \
    tar czf "/backup/store-$STAMP.tgz" -C /data . \
    || die "Backup failed - nothing will be changed."
  printf '  %s\n' "$BACKUPS/store-$STAMP.tgz"
  # Clean up old backups, but never the last ten.
  # shellcheck disable=SC2012  # filenames are self-generated (store-<UTC
  # timestamp>.tgz, written above by this same script), not attacker- or
  # user-supplied, so sorting them by mtime through `ls -t` is safe here
  # in a way it would not be in general - the same reasoning
  # deploy/updater/update-once.sh already carries at its own identical
  # pruning line.
  ls -1t "$BACKUPS"/store-*.tgz 2>/dev/null | tail -n +11 | xargs -r rm --
else
  printf '\nNo database volume found (%s) - first run?\n' "$VOLUME"
fi

# Pull instead of build (0.2.0). The long comment from 2026-09-03 about
# using `docker compose build` and not a separate `docker build`
# still holds - it now only affects the
# --build branch below. The reason from then remains the same: the service
# carries a `build:` block in the Compose file and builds its own
# image; no one uses a separate built `loxmatter:local`.
if [ "$BUILD" -eq 1 ]; then
  say "Building the image"
  (cd "$STACK" && docker compose build ${NO_CACHE:+--no-cache} "$SERVICE") \
    || die "Build failed - the running service remains unchanged."
else
  say "Pulling the image"
  (cd "$STACK" && docker compose pull "$SERVICE") \
    || die "No image loaded - the running service remains unchanged. Without access to the registry, --build helps."
fi

say "Restarting the service"
(cd "$STACK" && docker compose up -d --no-deps --force-recreate "$SERVICE") \
  || die "Restart failed. The backup above is the way back."

say "Checking whether it's alive"
PORT="$(grep -A1 -- '--listen' "$STACK/docker-compose.yml" | tail -1 | tr -dc '0-9')"
PORT="${PORT:-8080}"
URL="http://127.0.0.1:$PORT/health"
# The service needs noticeably long before its first response: it connects
# to matter-server, fetches a snapshot of every node, seeds the starting
# values from that, backfills existing devices, and sends everything to the
# Miniserver once - only after that does uvicorn go live on the network. On
# the test Pi with nine devices that measured 18 seconds, 5 of it alone
# for the connection.
#
# This used to say 20 seconds. That held up fine until it didn't (8
# September 2026): one run tipped just past it, the script aborted and
# reported a service as unhealthy that was running flawlessly ten seconds
# later. Too short a window is the more expensive kind of false alarm here
# - it looks like a broken update and tempts you to roll back a state that
# is actually fine. Anyone with more devices waits longer; 120 seconds
# leaves room for that without letting an actually dead service hang
# unreasonably long.
WAIT_SECONDS=120
OK=0
for i in $(seq 1 "$WAIT_SECONDS"); do
  if curl -fsS -m 3 "$URL" >/dev/null 2>&1; then OK=1; break; fi
  # A sign of life every ten seconds: without it, a normal, merely slow
  # start looks exactly like a hung one.
  if [ $((i % 10)) -eq 0 ]; then printf '  ... waited %s s\n' "$i"; fi
  sleep 1
done
if [ "$OK" -ne 1 ]; then
  printf '\n\033[31m%s is not responding after %s s. Last lines from the log:\033[0m\n' \
    "$URL" "$WAIT_SECONDS"
  docker logs --tail 30 "$SERVICE" 2>&1 || true
  die "The service is up but not reporting healthy."
fi
printf '  %s\n' "$(curl -fsS -m 3 "$URL")"

# The documented migration path for an EXISTING installation is exactly
# "git pull && ./scripts/update.sh" (README.md, CHANGELOG.md) - and until
# this step existed, that command never created loxmatter-updater. Every
# call above this point names $SERVICE (loxmatter) explicitly with
# --no-deps, by design (see the block comment at the top of this file:
# matter-server and OTBR must never restart on an update, since OTBR's
# Thread state lives in a volume and a Thread-network restart is not part
# of an update). Compose only ever creates or recreates what is actually
# NAMED in a command, plus dependencies (suppressed here by --no-deps) -
# a service nobody named is simply never brought up. So an installation
# that predates the updater sidecar (design "Applying updates through the
# web UI", 2026-09-08) never got it from following that instruction: the
# bridge updated, the git checkout gained the service definition, and
# nothing here ever ran `docker compose up` against it. The System tab
# kept reporting no updater present, forever, on every installation that
# did exactly what it was told to do.
#
# Named explicitly, with the same --no-deps as every other compose call
# in this file: loxmatter-updater carries no `depends_on` of its own in
# the compose file, so --no-deps changes nothing about ITS startup, but
# keeps this call honest about the same rule the rest of this script
# already follows, rather than relying on that being true only by
# accident of the current compose file's shape. No --force-recreate: an
# already-running sidecar with an unchanged image and config must not be
# interrupted mid-cycle (it could be mid-update itself) for a call whose
# only job is to create the ones that do not exist yet - Compose's
# default "missing" pull policy already covers the creation case, since
# a service that has never run has no local image to compare against and
# gets pulled unconditionally.
#
# Best-effort, deliberately not `die`d: by this point the bridge itself
# has already been updated and confirmed healthy (the health wait above
# would have aborted the script otherwise). A registry hiccup fetching
# the SIDECAR's own image must not turn an otherwise-successful bridge
# update into a reported failure - the System tab already tells an
# operator plainly when no updater is present, so a sidecar that could
# not be created here stays visible on its own, not lost inside a
# success message that then lies about it.
say "Ensuring the updater sidecar is present"
if ! (cd "$STACK" && docker compose up -d --no-deps loxmatter-updater); then
  printf '\033[33m  Could not start loxmatter-updater - the bridge above is unaffected. Retry by hand:\033[0m\n'
  printf '  cd %s && docker compose up -d --no-deps loxmatter-updater\n' "$STACK"
fi

say "Device status"
docker exec "$SERVICE" python3 -c "
import os, sqlite3
db = os.environ.get('LOXMATTER_STORE', '/data/loxmatter.sqlite')
c = sqlite3.connect(db)
print('  Schema version:', c.execute('PRAGMA user_version').fetchone()[0])
for did, label in c.execute('SELECT id, label FROM device WHERE active = 1'):
    n = c.execute('SELECT count(*) FROM signal WHERE device_id = ? AND exported = 1', (did,)).fetchone()[0]
    total = c.execute('SELECT count(*) FROM signal WHERE device_id = ?', (did,)).fetchone()[0]
    print(f'  {label}: {n} of {total} signals are exported')
" 2>/dev/null || printf '  (not readable - not an error, just no data)\n'

say "Done."
printf 'Interface: http://%s:%s/\n' "$(hostname -I 2>/dev/null | awk '{print $1}')" "$PORT"
