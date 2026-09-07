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

"""Generates new, unique object IDs in the format observed in the reference
file (design section 6) - the unverified core of this feature: whether
Loxone Config accepts an ID generated this way without complaint when
opening the file, nobody knows before a real test import."""

from __future__ import annotations

import secrets
import time

from loxmatter.projectsync.scan import ProjectFormatError


def _is_hex(value: str) -> bool:
    if not value:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _installation_suffix(existing: set[str]) -> str:
    """The last hyphen-separated segment of an existing U-ID - carried over
    for new IDs so they belong to the same project family (design
    section 6), instead of inventing a suffix of its own.

    If no `U` value in the expected 4-hex-group scheme can be found (a
    file entirely without `U` attributes, or a Loxone Config version with
    a different ID format - design section 10 names exactly this format
    uncertainty as an open risk), that is not an internal malfunction but
    a format this module does not understand: `ProjectFormatError`, not a
    bare `ValueError` that would reach the upload endpoint as HTTP 500."""
    for value in existing:
        parts = value.split("-")
        if len(parts) == 4 and all(_is_hex(part) for part in parts):
            return parts[-1]
    raise ProjectFormatError(
        "Keine bestehende U-ID im erwarteten Format in der Datei gefunden, aus der "
        "sich ein Installations-Suffix ableiten liesse."
    )


def new_unique_id(existing: set[str]) -> str:
    """New U-ID, checked for uniqueness against `existing` and immediately
    recorded there (so subsequent calls within the same run also do not
    collide with each other)."""
    suffix = _installation_suffix(existing)
    while True:
        millis = int(time.time() * 1000) & 0xFFFFFFFF
        candidate = f"{millis:08x}-{secrets.token_hex(2)}-{secrets.token_hex(2)}-{suffix}"
        if candidate not in existing:
            existing.add(candidate)
            return candidate


def new_iname(prefix: str, existing: set[str]) -> str:
    """Next free number of the form ``<prefix><n>``, e.g. ``VCI2`` if
    ``VCI1``/``VCI3``/``VCI4`` are already taken - simply counts up until a
    free number is found, without preferring gaps (real projects have
    non-contiguous numbers as soon as something has once been deleted, see
    design section 6)."""
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
