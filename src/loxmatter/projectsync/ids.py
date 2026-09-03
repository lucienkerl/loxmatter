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

"""Erzeugt neue, eindeutige Objekt-IDs im an der Referenzdatei beobachteten
Format (Entwurf Abschnitt 6) - der unverifizierte Kern dieses Features: ob
Loxone Config eine so erzeugte ID beim Oeffnen klaglos akzeptiert, weiss
niemand vor einem echten Test-Import."""

from __future__ import annotations

import secrets
import time


def _is_hex(value: str) -> bool:
    if not value:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _installation_suffix(existing: set[str]) -> str:
    """Der letzte Bindestrich-Abschnitt einer bestehenden U-ID - wird fuer
    neue IDs uebernommen, damit sie zur selben Projekt-Familie gehoeren
    (Entwurf Abschnitt 6), statt einen eigenen Suffix zu erfinden."""
    for value in existing:
        parts = value.split("-")
        if len(parts) == 4 and all(_is_hex(part) for part in parts):
            return parts[-1]
    raise ValueError(
        "Keine bestehende U-ID im erwarteten Format in der Datei gefunden, aus der "
        "sich ein Installations-Suffix ableiten liesse."
    )


def new_unique_id(existing: set[str]) -> str:
    """Neue U-ID, gegen `existing` eindeutig geprueft und dort sofort
    eingetragen (folgende Aufrufe im selben Lauf kollidieren damit auch
    untereinander nicht)."""
    suffix = _installation_suffix(existing)
    while True:
        millis = int(time.time() * 1000) & 0xFFFFFFFF
        candidate = f"{millis:08x}-{secrets.token_hex(2)}-{secrets.token_hex(2)}-{suffix}"
        if candidate not in existing:
            existing.add(candidate)
            return candidate


def new_iname(prefix: str, existing: set[str]) -> str:
    """Naechste freie Nummer der Form ``<prefix><n>``, z. B. ``VCI2``, wenn
    ``VCI1``/``VCI3``/``VCI4`` schon vergeben sind - zaehlt einfach hoch, bis
    eine freie Nummer gefunden ist, ohne Luecken zu bevorzugen (reale
    Projekte haben nicht-fortlaufende Nummern, sobald einmal etwas geloescht
    wurde, siehe Entwurf Abschnitt 6)."""
    used = {
        int(name[len(prefix) :])
        for name in existing
        if name.startswith(prefix) and name[len(prefix) :].isdigit()
    }
    n = 1
    while n in used:
        n += 1
    candidate = f"{prefix}{n}"
    existing.add(candidate)
    return candidate
