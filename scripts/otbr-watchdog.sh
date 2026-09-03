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


# Holt den OTBR-Agenten zurueck, wenn er gestorben ist.
#
# Gedacht fuer einen Cron-Eintrag, siehe deploy/testhost/README.md:
#
#   */5 * * * * /home/pi/matter-loxone/scripts/otbr-watchdog.sh >> /home/pi/otbr-watchdog.log 2>&1
#
# WARUM das noetig ist: der OTBR-Agent bricht ab, wenn das Funkmodul nicht
# mehr antwortet (RCP-Timeout - USB-Aussetzer, Stromversorgung, das Modul
# selbst). Der CONTAINER laeuft dabei weiter, weil sein Einstiegsskript nicht
# der Agent ist. `restart: unless-stopped` greift deshalb nicht, und das
# Image bringt keinen Aufpasser mit. Am 2026-09-03 blieb ein solcher Ausfall
# sechseinhalb Stunden unbemerkt; kein Geraet war in dieser Zeit erreichbar.
#
# Die Pruefung ist dieselbe, die auch die Ansicht "System" anzeigt: existiert
# eine Thread-Schnittstelle (wpan*) mit einer Mesh-Adresse? Sie verschwindet
# mit dem Agenten.
#
# Bewusst KEIN Neustart in Schleife: schlaegt der Neustart fehl, weil das
# Funkmodul selbst haengt, wuerde ein Wiederholen im Minutentakt nichts
# bessern und nur das Log fluten. Dann muss jemand hinsehen - und findet im
# Log, was war.
set -euo pipefail

SERVICE="otbr"
STACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/testhost" && pwd)"
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"

thread_is_up() {
  # Scope 00 heisst geroutet (ULA eingeschlossen); wpan* ist die
  # Thread-Schnittstelle von OTBR.
  awk '$4 == "00" && $6 ~ /^wpan/ { found = 1 } END { exit !found }' /proc/net/if_inet6
}

if thread_is_up; then
  exit 0
fi

printf '%s  Keine Thread-Schnittstelle - starte %s neu\n' "$STAMP" "$SERVICE"
if ! (cd "$STACK" && docker compose restart "$SERVICE" >/dev/null 2>&1); then
  printf '%s  Neustart von %s fehlgeschlagen\n' "$STAMP" "$SERVICE"
  exit 1
fi

# Dem Agenten Zeit geben, dem Netz wieder beizutreten. Beobachtet wurden
# rund 10 s; 60 s Geduld lassen Raum, ohne bei einem echten Defekt ewig zu
# warten.
for _ in $(seq 1 12); do
  sleep 5
  if thread_is_up; then
    printf '%s  Thread-Netz ist zurueck\n' "$(date '+%Y-%m-%d %H:%M:%S')"
    exit 0
  fi
done

printf '%s  Nach 60 s immer noch keine Thread-Schnittstelle. Haengt das Funkmodul?\n' \
  "$(date '+%Y-%m-%d %H:%M:%S')"
printf '%s  Letzte Zeilen aus dem OTBR-Log:\n' "$(date '+%Y-%m-%d %H:%M:%S')"
docker logs --tail 20 "$SERVICE" 2>&1 | sed 's/^/    /' || true
exit 1
