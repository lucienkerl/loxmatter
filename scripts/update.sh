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


# Bringt die laufende Bruecke auf den Stand der veroeffentlichten Version.
#
#   ./scripts/update.sh              # holen, Image ziehen, neu starten
#   ./scripts/update.sh --no-pull    # nur ziehen und neu starten
#   ./scripts/update.sh --build      # aus der Quelle bauen statt ziehen
#   ./scripts/update.sh --build --no-cache   # ohne Layer-Cache bauen
#
# Auf dem Rechner auszufuehren, auf dem die Bruecke laeuft. Der Stack liegt
# im Repository selbst (deploy/testhost/), das Skript findet ihn ueber
# seinen eigenen Pfad - kein Konfigurationsschritt.
#
# Seit 0.2.0 wird gezogen statt gebaut: der Bau brauchte auf dem Test-Pi
# fuenf bis zehn Minuten und konnte an einem PyPI-Ausfall oder am
# Speicher scheitern. --build stellt den alten Weg wieder her, fuer
# Entwicklung und fuer Hosts ohne Zugang zur Registry.
#
# Der Dienst wird mit `--no-deps` gestartet: matter-server und OTBR bleiben
# unangetastet. Ohne das erzeugt Compose sie mit neu, sobald sich die
# Projektkonfiguration geaendert hat - und OTBRs Thread-Zustand haengt an
# einem Volume, das ein Neubau zwar ueberlebt, aber ein Neustart des
# Thread-Netzes ohne Grund gehoert nicht zu einem Update.
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
    *)          printf 'Unbekanntes Argument: %s (erlaubt: --no-pull, --build, --no-cache, --help)\n' "$arg" >&2; exit 2 ;;
  esac
done
# --no-cache steuert einen Bau. Ohne --build steuert es gar nichts, und
# ein Schalter, der stillschweigend wirkungslos bleibt, ist schlimmer als
# einer, der fehlt: er laesst jemanden glauben, er habe frisch gebaut.
if [ -n "$NO_CACHE" ] && [ "$BUILD" -eq 0 ]; then
  printf 'Abbruch: --no-cache wirkt nur zusammen mit --build.\n' >&2
  exit 2
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK="$REPO/deploy/testhost"
SERVICE="loxmatter"
BACKUPS="$HOME/loxmatter-backups"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\n\033[31mAbbruch: %s\033[0m\n' "$*" >&2; exit 1; }

[ -f "$REPO/Dockerfile" ] || die "Kein Dockerfile in $REPO - laeuft das Skript aus dem Repository?"
[ -f "$STACK/docker-compose.yml" ] || die "Kein docker-compose.yml in $STACK."
grep -q "^  ${SERVICE}:" "$STACK/docker-compose.yml" || die "Die Compose-Datei kennt keinen Dienst '${SERVICE}'."
command -v docker >/dev/null || die "docker ist nicht installiert."

if [ "$PULL" -eq 1 ]; then
  say "Hole den neuesten Stand"
  git -C "$REPO" pull --ff-only || die "git pull fehlgeschlagen - lokale Aenderungen im Weg?"
fi
printf '  %s (%s)\n' "$REPO" "$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo 'kein Commit')"
if [ -n "$(git -C "$REPO" status --porcelain 2>/dev/null)" ]; then
  printf '\033[33m  Hinweis: der Arbeitsbaum ist nicht sauber. Es wird ausgeliefert, was da liegt.\033[0m\n'
fi

# Die Signaldatenbank ist das Einzige, was ein misslungenes Update nicht
# wiederherstellen koennte: darin stehen die Signalschluessel, und die sind
# die Verdrahtung in der Loxone-Konfiguration. Vor allem anderen eine Kopie.
#
# Der Volume-Name setzt sich aus dem Projektnamen zusammen, und der steht
# fest in der Compose-Datei (`name:`) - genau deshalb steht er dort und
# nicht in einer .env, die jemand neu erzeugen koennte.
PROJECT="$(awk '/^name:/ {print $2; exit}' "$STACK/docker-compose.yml")"
[ -n "$PROJECT" ] || die "Kein 'name:' in der Compose-Datei - ohne Projektnamen ist der Volume-Name nicht bestimmbar."
VOLUME="${PROJECT}_loxmatter-store"
if docker volume inspect "$VOLUME" >/dev/null 2>&1; then
  say "Sichere die Signaldatenbank"
  mkdir -p "$BACKUPS"
  STAMP="$(date +%Y-%m-%d-%H%M%S)"
  docker run --rm -v "$VOLUME:/data:ro" -v "$BACKUPS:/backup" alpine:latest \
    tar czf "/backup/store-$STAMP.tgz" -C /data . \
    || die "Sicherung fehlgeschlagen - es wird nichts geaendert."
  printf '  %s\n' "$BACKUPS/store-$STAMP.tgz"
  # Alte Sicherungen aufraeumen, aber nie die letzten zehn.
  ls -1t "$BACKUPS"/store-*.tgz 2>/dev/null | tail -n +11 | xargs -r rm --
else
  printf '\nKein Datenbank-Volume gefunden (%s) - erster Lauf?\n' "$VOLUME"
fi

# Ziehen statt bauen (0.2.0). Der lange Kommentar von 2026-09-03 darueber,
# dass `docker compose build` und nicht ein eigenes `docker build` zu
# benutzen ist, gilt unveraendert weiter - er betrifft jetzt nur noch den
# --build-Zweig unten. Die Ursache von damals bleibt dieselbe: der Dienst
# traegt in der Compose-Datei einen `build:`-Block und baut sein eigenes
# Image; ein daneben gebautes `loxmatter:local` benutzt niemand.
if [ "$BUILD" -eq 1 ]; then
  say "Baue das Image"
  (cd "$STACK" && docker compose build ${NO_CACHE:+--no-cache} "$SERVICE") \
    || die "Build fehlgeschlagen - der laufende Dienst bleibt unveraendert."
else
  say "Hole das Image"
  (cd "$STACK" && docker compose pull "$SERVICE") \
    || die "Kein Image geladen - der laufende Dienst bleibt unveraendert. Ohne Zugang zur Registry hilft --build."
fi

say "Starte den Dienst neu"
(cd "$STACK" && docker compose up -d --no-deps --force-recreate "$SERVICE") \
  || die "Neustart fehlgeschlagen. Zurueck geht es mit der Sicherung oben."

say "Sehe nach, ob er lebt"
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
  die "Der Dienst ist oben, meldet sich aber nicht gesund."
fi
printf '  %s\n' "$(curl -fsS -m 3 "$URL")"

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
printf 'Oberflaeche: http://%s:%s/\n' "$(hostname -I 2>/dev/null | awk '{print $1}')" "$PORT"
