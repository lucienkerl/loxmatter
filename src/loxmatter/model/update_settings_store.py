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

"""The update channel and the check toggle - design "Applying updates
through the web UI" (2026-09-08), section 9.

Own module and own class, following `locale_store.py` and
`resend_settings_store.py`: the `setting` table is deliberately generic,
exactly so that further configuration like this one can go through it
instead of earning its own table. This class is another view onto that
same table and the store's existing connection, not a second connection.

No new table, and thereby no schema bump: a migration here would come at
a real price. A schema rise is the one case where rolling back to the
previous release is not consequence-free (the old code cannot read a
newer schema), and of all the features in this bridge, the one that
updates it should not be what forces that choice on someone."""

from __future__ import annotations

import sqlite3

_CHANNEL_KEY = "update.channel"
_CHECK_ENABLED_KEY = "update.check_enabled"

_CHANNELS = ("stable", "dev")


class UpdateSettingsStore:
    """Access to `setting` over the store's connection - like
    `LocaleStore`, just for the two keys above."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_channel(self) -> str:
        """The stored channel, or `"stable"` while nothing is stored
        yet. Never throws - like `LocaleStore.get_language`, an unwritten
        setting is the normal case on a fresh install, not an error."""
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_CHANNEL_KEY,)
        ).fetchone()
        return str(row["value"]) if row is not None else "stable"

    def set_channel(self, channel: str) -> None:
        if channel not in _CHANNELS:
            raise ValueError(f"unknown update channel {channel!r}, expected one of {_CHANNELS}")
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_CHANNEL_KEY, channel),
        )
        self._db.commit()

    def get_check_enabled(self) -> bool:
        """Whether checking GitHub for updates is allowed - `True` while
        nothing is stored yet.

        Default ON: someone running this bridge in their home should
        learn that a fix exists, the same way any other piece of home
        software would tell them. It stays switchable OFF regardless,
        without qualification - this setting gates the one connection
        this bridge makes to the outside world, and that choice belongs
        to whoever runs it, not to this module."""
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_CHECK_ENABLED_KEY,)
        ).fetchone()
        return row["value"] != "0" if row is not None else True

    def set_check_enabled(self, enabled: bool) -> None:
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_CHECK_ENABLED_KEY, "1" if enabled else "0"),
        )
        self._db.commit()
