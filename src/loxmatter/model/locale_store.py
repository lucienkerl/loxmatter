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

"""Die gemeinsame Spracheinstellung dieser Installation - EINE Einstellung
fuer CLI und (ab Phase B) WebUI, kein Feld pro Nutzer oder Browser. Siehe
docs/superpowers/specs/2026-09-03-i18n-phase-a-sprachwahl-cli-design.md,
Abschnitt 4.

Eigenes Modul und eigene Klasse, analog zu `auth_store.py` und
`settings_store.py`: die `setting`-Tabelle ist generisch angelegt, genau
damit weitere Konfiguration wie diese hier denselben Weg gehen kann. Diese
Klasse ist eine weitere Sicht auf dieselbe Tabelle und dieselbe Verbindung,
kein zweiter Verbindungsaufbau."""

from __future__ import annotations

import sqlite3

from loxmatter.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

_LANGUAGE_KEY = "language"


class LocaleStore:
    """Zugriff auf `setting` ueber die Verbindung des Stores - wie
    `AuthStore`, nur fuer den Schluessel `"language"`."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_language(self) -> str:
        """Der gespeicherte Wert - `DEFAULT_LANGUAGE`, solange nichts
        gespeichert ist oder der gespeicherte Wert (z. B. nach einer
        kuenftigen Ruecknahme einer Sprache aus `SUPPORTED_LANGUAGES`)
        nicht mehr unterstuetzt wird. Wirft nie."""
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
                f"nicht unterstuetzte Sprache {language!r}, erwartet eine von "
                f"{sorted(SUPPORTED_LANGUAGES)}"
            )
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_LANGUAGE_KEY, language),
        )
        self._db.commit()
