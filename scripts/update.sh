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


# Brings the running bridge up to the state of the repository.
#
#   ./scripts/update.sh              # pull, build, restart
#   ./scripts/update.sh --no-pull    # only build and restart
#   ./scripts/update.sh --no-cache   # build without the layer cache
#
# Run this on the machine where the bridge runs. The stack lives inside the
# repository itself (deploy/testhost/), the script finds it via its own
# path - no configuration step needed.
#
# The service is started with `--no-deps`: matter-server and OTBR are left
# untouched. Without that, Compose would recreate them too as soon as the
# project configuration changes - and OTBR's Thread state hangs off a
# volume that survives a rebuild, but restarting the Thread network for no
# reason isn't part of an update.
set -euo pipefail

PULL=1
NO_CACHE=""
for arg in "$@"; do
  case "$arg" in
    --no-pull)  PULL=0 ;;
    --no-cache) NO_CACHE=1 ;;
    -h|--help)  sed -n '18,31p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)          printf 'Unknown argument: %s (allowed: --no-pull, --no-cache, --help)\n' "$arg" >&2; exit 2 ;;
  esac
done

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
  ls -1t "$BACKUPS"/store-*.tgz 2>/dev/null | tail -n +11 | xargs -r rm --
else
  printf '\nNo database volume found (%s) - first run?\n' "$VOLUME"
fi

# Via `docker compose build`, NOT via a separate `docker build`
# (2026-09-03): in the compose file the service carries a `build:` block
# and so builds its own image. Nobody uses a `loxmatter:local` built
# alongside it - for months the script built an image that never went
# anywhere, while Compose on `up` simply kept reusing an existing image
# instead of rebuilding. The service then kept running unchanged and
# still reported "Done".
say "Building the image"
(cd "$STACK" && docker compose build ${NO_CACHE:+--no-cache} "$SERVICE") \
  || die "Build failed - the running service is unchanged."

say "Restarting the service"
(cd "$STACK" && docker compose up -d --no-deps --force-recreate "$SERVICE") \
  || die "Restart failed. The backup above is the way back."

say "Checking whether it's alive"
PORT="$(grep -A1 -- '--listen' "$STACK/docker-compose.yml" | tail -1 | tr -dc '0-9')"
PORT="${PORT:-8080}"
URL="http://127.0.0.1:$PORT/health"
# Der Dienst braucht vor seiner ersten Antwort spuerbar Zeit: er verbindet
# sich mit matter-server, holt ein Abbild jedes Knotens, saet daraus die
# Startwerte, traegt Bestandsgeraete nach und schickt einmal alles an den
# Miniserver - erst danach geht uvicorn ans Netz. Auf dem Test-Pi mit neun
# Geraeten sind das gemessen 18 Sekunden, allein 5 davon die Verbindung.
#
# Hier standen 20 Sekunden. Das ging genau so lange gut, bis es das nicht
# mehr tat (8. September 2026): ein Lauf kippte knapp darueber, das Skript
# brach ab und meldete einen Dienst als krank, der zehn Sekunden spaeter
# tadellos lief. Ein zu kurzes Zeitfenster ist hier die teurere Sorte
# Fehlalarm - es sieht aus wie ein kaputtes Update und verleitet zum
# Zurueckrollen eines Standes, der in Ordnung ist. Wer mehr Geraete hat,
# wartet laenger; 120 Sekunden lassen dafuer Luft, ohne dass ein
# tatsaechlich toter Dienst unzumutbar lange haengt.
WAIT_SECONDS=120
OK=0
for i in $(seq 1 "$WAIT_SECONDS"); do
  if curl -fsS -m 3 "$URL" >/dev/null 2>&1; then OK=1; break; fi
  # Ein Lebenszeichen alle zehn Sekunden: ohne das sieht ein normaler,
  # nur langsamer Start genauso aus wie ein haengender.
  if [ $((i % 10)) -eq 0 ]; then printf '  ... %s s gewartet\n' "$i"; fi
  sleep 1
done
if [ "$OK" -ne 1 ]; then
  printf '\n\033[31m%s antwortet nach %s s nicht. Letzte Zeilen aus dem Log:\033[0m\n' \
    "$URL" "$WAIT_SECONDS"
  docker logs --tail 30 "$SERVICE" 2>&1 || true
  die "The service is up but not reporting healthy."
fi
printf '  %s\n' "$(curl -fsS -m 3 "$URL")"

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
