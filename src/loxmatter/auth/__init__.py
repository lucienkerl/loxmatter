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

"""Zugang zur Oberflaeche: Passwort, Sitzung, Drosselung.

Drei Module, absichtlich getrennt und absichtlich ohne FastAPI-Bezug:

- `passwords` rechnet Hashes und prueft sie. Kennt weder Datenbank noch HTTP.
- `sessions` legt Sitzungen an und prueft sie. Kennt den `AuthStore`, kein HTTP.
- `throttle` zaehlt Fehlversuche. Kennt gar nichts ausser der Uhr.

Der HTTP-Teil liegt in `loxmatter.api.auth`, der Waechter in
`loxmatter.loxone.server`. Diese Trennung ist der Grund, warum die Logik
hier ohne ASGI-Testclient pruefbar ist - und warum ein Geheimnis nur an den
Stellen auftauchen kann, die es wirklich brauchen.
"""
