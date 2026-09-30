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

"""The daily firmware check's switch (design 2026-09-30, section 6.2).

One key in the `setting` table, following `update_settings_store.py`. A
missing key means enabled: the check is on by default."""

from __future__ import annotations

import sqlite3

_DAILY_CHECK_KEY = "firmware.daily_check_enabled"


class FirmwareSettingsStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_daily_check_enabled(self) -> bool:
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_DAILY_CHECK_KEY,)
        ).fetchone()
        return row is None or str(row["value"]) != "0"

    def set_daily_check_enabled(self, enabled: bool) -> None:
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_DAILY_CHECK_KEY, "1" if enabled else "0"),
        )
        self._db.commit()
