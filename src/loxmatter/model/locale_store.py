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

"""The shared language setting of this installation - ONE setting for the
CLI and (from Phase B on) the WebUI, not a field per user or browser. See
docs/superpowers/specs/2026-09-03-i18n-phase-a-sprachwahl-cli-design.md,
section 4.

Its own module and its own class, analogous to `auth_store.py` and
`settings_store.py`: the `setting` table is generic by design, precisely so
that further configuration like this one can go the same way. This class is
another view onto the same table and the same connection, not a second
connection."""

from __future__ import annotations

import sqlite3

from loxmatter.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

_LANGUAGE_KEY = "language"


class LocaleStore:
    """Access to `setting` via the store's connection - like `AuthStore`,
    just for the key `"language"`."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_language(self) -> str:
        """The stored value - `DEFAULT_LANGUAGE` as long as nothing is
        stored, or if the stored value (e.g. after a future removal of a
        language from `SUPPORTED_LANGUAGES`) is no longer supported. Never
        raises."""
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_LANGUAGE_KEY,)
        ).fetchone()
        if row is None:
            return DEFAULT_LANGUAGE
        value = str(row["value"])
        return value if value in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE

    def set_language(self, language: str) -> None:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(
                f"unsupported language {language!r}, expected one of {sorted(SUPPORTED_LANGUAGES)}"
            )
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_LANGUAGE_KEY, language),
        )
        self._db.commit()
