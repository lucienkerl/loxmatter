#!/usr/bin/env bash
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

#
# Bringt die laufende Bruecke auf dem Testhost auf den Stand des Arbeitsbaums.
#
# Zwei Betriebsarten, eine Datei:
#
#   ./deploy/testhost/update.sh            # auf dem Mac, im Repo: alles
#   bash ~/loxmatter-build/update.sh --local   # auf dem Pi: nur bauen und neu starten
#
# Die erste kopiert die Quellen (samt dieser Datei) auf den Zielrechner und
# ruft sich dort in der zweiten auf. Ein `git pull` auf dem Pi gibt es nicht,
# weil das Projekt kein Remote hat.
#
# Der Dienst wird mit `--no-deps` gestartet: matter-server und OTBR bleiben
# unangetastet. Ohne das erzeugt Compose sie mit neu, sobald sich die
# Projektkonfiguration geaendert hat - beim ersten Aufsetzen ist genau das
# passiert. Die Fabric ueberlebt es (sie liegt im Bind-Mount), aber ein
# Neustart des Thread-Netzes ohne Grund ist nichts, was ein Update tun soll.
set -euo pipefail

HOST="${LOXMATTER_HOST:-pi@10.0.1.56}"
BUILD_DIR="${LOXMATTER_BUILD_DIR:-loxmatter-build}"
STACK_DIR="${LOXMATTER_STACK_DIR:-loxmatter-testhost}"
IMAGE="loxmatter:local"
SERVICE="loxmatter"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\n\033[31mAbbruch: %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Teil auf dem Zielrechner: sichern, bauen, neu starten, nachsehen
# ---------------------------------------------------------------------------
run_local() {
  # Woher die Quellen kommen, haengt davon ab, wie dieses Skript auf den
  # Rechner kam:
  #
  #   - Als Teil eines Git-Checkouts (`git clone`, dann
  #     `bash deploy/testhost/update.sh --local`): dann liegt der
  #     Dockerfile zwei Ebenen ueber dieser Datei, und genau der Checkout
  #     ist gemeint - `git pull` davor, und man liefert aus, was man
  #     gerade geholt hat.
  #   - Per rsync vom Mac (siehe `run_remote`): dann liegt die Datei flach
  #     in ~/$BUILD_DIR neben dem Dockerfile.
  #
  # Der Checkout gewinnt, weil er der ausdruecklichere Fall ist: wer ihn
  # angelegt hat, will aus ihm ausliefern.
  local checkout src
  checkout="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || checkout=""
  if [ -n "$checkout" ] && [ -f "$checkout/Dockerfile" ]; then
    src="$checkout"
    if [ -d "$checkout/.git" ]; then
      printf '\nQuellen aus dem Checkout %s (%s)\n' "$src" "$(git -C "$src" rev-parse --short HEAD 2>/dev/null || echo 'kein Commit')"
    fi
  else
    src="$HOME/$BUILD_DIR"
  fi
  cd "$src" || die "Quellen fehlen unter $src - erst vom Mac aus aufrufen oder das Repo klonen."
  [ -f Dockerfile ] || die "Kein Dockerfile in $src."
  local stack="$HOME/$STACK_DIR"
  [ -f "$stack/docker-compose.yml" ] || die "Kein docker-compose.yml in ~/$STACK_DIR."
  grep -q "^  ${SERVICE}:" "$stack/docker-compose.yml" \
    || die "Die Compose-Datei kennt keinen Dienst '${SERVICE}'."

  # Die Signaldatenbank ist das einzige, was ein missgluecktes Update nicht
  # wiederherstellen koennte: darin stehen die Schluessel, und die sind die
  # Verdrahtung in der Loxone-Konfiguration. Vor jedem Update eine Kopie,
  # bevor irgendetwas anderes passiert.
  local volume stamp backup
  volume="$(basename "$stack")_loxmatter-store"
  stamp="$(date +%Y-%m-%d-%H%M%S)"
  backup="$HOME/loxmatter-backups"
  mkdir -p "$backup"
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    say "Sichere die Signaldatenbank"
    docker run --rm -v "$volume:/data:ro" -v "$backup:/backup" alpine:latest \
      tar czf "/backup/store-$stamp.tgz" -C /data . \
      || die "Sicherung fehlgeschlagen - es wird nichts geaendert."
    printf '  %s\n' "$backup/store-$stamp.tgz"
    # Alte Sicherungen aufraeumen, aber nie die letzten zehn.
    ls -1t "$backup"/store-*.tgz 2>/dev/null | tail -n +11 | xargs -r rm --
  else
    printf '\nKein Datenbank-Volume gefunden (%s) - erster Lauf?\n' "$volume"
  fi

  say "Baue das Image"
  docker build -t "$IMAGE" . || die "Build fehlgeschlagen - der laufende Dienst bleibt unveraendert."

  say "Starte den Dienst neu"
  # --no-deps: matter-server und OTBR nicht anfassen (siehe Kopf).
  (cd "$stack" && docker compose up -d --no-deps --force-recreate "$SERVICE") \
    || die "Neustart fehlgeschlagen. Zurueck geht es mit der Sicherung oben."

  say "Sehe nach, ob er lebt"
  local port url ok=0
  port="$(grep -A1 -- '--listen' "$stack/docker-compose.yml" | tail -1 | tr -dc '0-9')"
  port="${port:-8080}"
  url="http://127.0.0.1:$port/health"
  for _ in $(seq 1 20); do
    if curl -fsS -m 3 "$url" >/dev/null 2>&1; then ok=1; break; fi
    sleep 1
  done
  if [ "$ok" -ne 1 ]; then
    printf '\n\033[31m%s antwortet nicht. Letzte Zeilen aus dem Log:\033[0m\n' "$url"
    docker logs --tail 30 "$SERVICE" 2>&1 || true
    die "Der Dienst ist oben, meldet sich aber nicht gesund."
  fi
  printf '  %s\n' "$(curl -fsS -m 3 "$url")"

  # Was der naechste Export erzeugen wuerde - die Zahl, um die es beim
  # Update von Phase 6 geht. Ohne Token nicht abrufbar, deshalb aus dem
  # Container heraus statt ueber die API.
  say "Stand der Geraete"
  docker exec "$SERVICE" python3 -c "
import os, sqlite3
db = os.environ.get('LOXMATTER_STORE', '/data/loxmatter.sqlite')
c = sqlite3.connect(db)
print('  Schema-Version:', c.execute('PRAGMA user_version').fetchone()[0])
for did, label in c.execute('SELECT id, label FROM device WHERE active = 1'):
    n = c.execute('SELECT count(*) FROM signal WHERE device_id = ? AND exported = 1', (did,)).fetchone()[0]
    total = c.execute('SELECT count(*) FROM signal WHERE device_id = ?', (did,)).fetchone()[0]
    print(f'  {label}: {n} von {total} Signalen werden exportiert')
" 2>/dev/null || printf '  (nicht auslesbar - kein Fehler, nur keine Auskunft)\n'

  say "Fertig."
  printf 'Oberflaeche: http://%s:%s/\n' "$(hostname -I 2>/dev/null | awk '{print $1}')" "$port"
}

# ---------------------------------------------------------------------------
# Teil auf dem Mac: Quellen hinueber, dann drueben weitermachen
# ---------------------------------------------------------------------------
run_remote() {
  local repo
  repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  [ -f "$repo/Dockerfile" ] || die "Kein Dockerfile in $repo - laeuft dieses Skript aus dem Repo?"

  if [ -n "$(git -C "$repo" status --porcelain 2>/dev/null)" ]; then
    printf '\n\033[33mHinweis: der Arbeitsbaum ist nicht sauber. Es wird ausgeliefert, was da liegt.\033[0m\n'
  fi

  say "Kopiere die Quellen nach $HOST:~/$BUILD_DIR"
  ssh "$HOST" "mkdir -p ~/$BUILD_DIR" || die "Kein SSH-Zugang zu $HOST."
  rsync -az --delete \
    "$repo/Dockerfile" "$repo/pyproject.toml" "$repo/uv.lock" "$repo/README.md" \
    "$repo/src" "$repo/scripts" "$repo/deploy/testhost/update.sh" \
    "$HOST:~/$BUILD_DIR/" || die "Kopieren fehlgeschlagen."

  ssh "$HOST" "LOXMATTER_BUILD_DIR='$BUILD_DIR' LOXMATTER_STACK_DIR='$STACK_DIR' bash ~/$BUILD_DIR/update.sh --local"
}

case "${1:-}" in
  --local) run_local ;;
  "")      run_remote ;;
  -h|--help)
    sed -n '3,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    ;;
  *) die "Unbekanntes Argument: $1 (erlaubt: --local, --help)" ;;
esac
