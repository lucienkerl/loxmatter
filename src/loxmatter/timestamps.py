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

"""Ein einziger Zeitstempel-Helfer fuer die ganze Codebasis.

`model.store`, `loxone.sender` und `loxone.server` trugen bislang je eine
eigene, wortgleiche Kopie dieser Funktion - jede mit einem eigenen Kommentar,
der begruendete, warum eine gemeinsame Abhaengigkeit angeblich mehr Kopplung
koste, als sie einspare. Fuer eine Zeile Code, die sonst nirgends gebraucht
wird, war das schon bei zwei Kopien eine duenne Begruendung; bei DREI
(Review-Fix Minor, 2026-09-02) haette jede kuenftige Aenderung an der
Zeitstempelform (z. B. an der Genauigkeit) an drei Stellen synchron bleiben
muessen. `model.store.Store._now` bleibt als duenne, einzeilige Bruecke auf
`now_iso` hier bestehen - nicht aus Kopplungsangst, sondern weil `self._now()`
bereits an vielen Stellen dieser Klasse verdrahtet ist und eine reine
Umbenennung aller Aufrufer keinen Mehrwert gegenueber der Bruecke haette."""

from __future__ import annotations

from datetime import UTC, datetime


def now_iso() -> str:
    """ISO-8601-Zeitstempel in UTC, mit Mikrosekunden.

    Fest mit `timespec="microseconds"`, damit zwei kurz aufeinander
    folgende Zeitstempel (z. B. Export, dann sofort eine Umbenennung) als
    Text zuverlaessig in derselben Reihenfolge vergleichbar bleiben wie
    chronologisch - ohne das liesse `datetime.isoformat()` die
    Sekundenbruchteile bei einem zufaellig exakten Sekundenwert weg, was
    zwei Zeitstempel unterschiedlicher Laenge ergeben koennte."""
    return datetime.now(UTC).isoformat(timespec="microseconds")
