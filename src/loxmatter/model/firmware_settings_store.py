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

"""Firmware settings in the `setting` table (designs 2026-09-30 and 2026-10-01).

Two keys, following `update_settings_store.py`. The daily check's switch
(section 6.2): a missing key means enabled, the check is on by default. The
update queue's halt reason (queue design, section 3): a missing key means
the queue is not halted."""

from __future__ import annotations

import sqlite3

_DAILY_CHECK_KEY = "firmware.daily_check_enabled"
_QUEUE_HALTED_KEY = "firmware.queue_halted_reason"


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

    def get_queue_halted_reason(self) -> str | None:
        """The i18n key of why the update queue halted; `None` while it runs."""
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_QUEUE_HALTED_KEY,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def set_queue_halted_reason(self, reason: str | None) -> None:
        if reason is None:
            self._db.execute("DELETE FROM setting WHERE key = ?", (_QUEUE_HALTED_KEY,))
        else:
            self._db.execute(
                "INSERT INTO setting (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_QUEUE_HALTED_KEY, reason),
            )
        self._db.commit()
