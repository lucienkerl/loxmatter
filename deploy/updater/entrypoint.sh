#!/bin/sh
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
# Die Schleife des Beiwagens - Entwurf "Updates ueber die Oberflaeche
# einspielen" (2026-09-08), Abschnitt 6.
#
# Bewusst duenn: aller Verstand steckt in update-once.sh, und zwar
# vollstaendig. Nur so laesst sich ein Durchlauf im Test aufrufen, ohne
# eine Endlosschleife anzuwerfen und wieder abzuwuergen.
#
# `|| true`: ein einzelner misslungener Durchlauf darf den Beiwagen nicht
# beenden. Er ist der Einzige, der einen kaputten Zustand ueberhaupt noch
# melden kann - ein Container, der sich bei einem Fehler beendet, nimmt
# genau diese Meldung mit.
set -u

while true; do
  /opt/loxmatter/update-once.sh || true
  sleep 2
done
