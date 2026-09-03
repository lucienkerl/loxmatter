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

"""Bremse gegen das Durchprobieren von Passwoertern (Spec 8).

Der Grund, warum es dieses Modul ueberhaupt gibt: ein Passwort ist ratbar,
ein Token aus `openssl rand -hex 32` nicht. Ohne Bremse waere der Login also
der schwaechere Weg in denselben Dienst - und dieser Entwurf haette die
Absicherung verschlechtert, waehrend er sie bequemer macht.

**Im Speicher und nicht in der Datenbank:** das ist fluechtiger Zustand, der
keinen Schreibzugriff je Fehlversuch rechtfertigt. Ein Neustart loescht ihn -
nur kann ein Angreifer keinen ausloesen, und ein Betreiber, der neu startet,
um sich schneller wieder anmelden zu koennen, betrachtet sein eigenes
Passwort ohnehin nicht als Angriff.

**`time.monotonic` und nicht `time.time`:** eine Zeitumstellung oder ein
NTP-Sprung darf eine Sperre weder verlaengern noch aufheben.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

FAILURES_BEFORE_THROTTLING = 5
THROTTLE_SECONDS = 30


@dataclass
class LoginThrottle:
    """Zaehlt Fehlversuche je Aufrufer. Eine Instanz je Router, siehe
    `api.auth.build_auth_router`."""

    _failures: dict[str, int] = field(default_factory=dict)
    _blocked_until: dict[str, float] = field(default_factory=dict)

    def retry_after(self, client: str, *, now: float | None = None) -> int:
        """Wie viele Sekunden dieser Aufrufer noch warten muss - `0`, wenn er
        es sofort versuchen darf.

        Aufgerundet, damit die Meldung in der Oberflaeche ("in X Sekunden
        wieder moeglich") nie zu frueh zum Wiederholen einlaedt."""
        moment = time.monotonic() if now is None else now
        blocked_until = self._blocked_until.get(client)
        if blocked_until is None or blocked_until <= moment:
            return 0
        return int(blocked_until - moment) + 1

    def record_failure(self, client: str, *, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        count = self._failures.get(client, 0) + 1
        self._failures[client] = count
        if count >= FAILURES_BEFORE_THROTTLING:
            self._blocked_until[client] = moment + THROTTLE_SECONDS

    def record_success(self, client: str) -> None:
        """Setzt Zaehler und Sperre zurueck - wer das Passwort kennt, ist
        kein Angreifer, auch wenn er sich vorher fuenfmal vertippt hat."""
        self._failures.pop(client, None)
        self._blocked_until.pop(client, None)
