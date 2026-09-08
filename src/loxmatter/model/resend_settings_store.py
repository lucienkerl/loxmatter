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

"""The interval of the periodic resend - ONE setting for the entire bridge,
changeable at runtime via the WebUI/API instead of a constant fixed at
startup. See
docs/superpowers/specs/2026-09-04-periodic-resend-design.md, section 4.

Its own module and its own class, analogous to `locale_store.py`: the
`setting` table is generic by design, precisely so that further
configuration like this one can go the same way. This class is another view
onto the same table and the same connection, not a second connection."""

from __future__ import annotations

import sqlite3

_INTERVAL_KEY = "resend_interval_seconds"

DEFAULT_RESEND_INTERVAL_SECONDS = 300.0
# Lower bound (design, section 5): guards against an accidentally too-short
# interval that, with many marked signals, would produce exactly the burst
# this design is actually meant to avoid.
MIN_RESEND_INTERVAL_SECONDS = 10.0


class ResendSettingsStore:
    """Access to `setting` via the store's connection - like `LocaleStore`,
    just for the key `"resend_interval_seconds"`."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_interval_seconds(self) -> float:
        """The stored value - `DEFAULT_RESEND_INTERVAL_SECONDS` as long as
        nothing is stored OR the stored value can no longer be read as a
        number (e.g. after a manual change to the database from outside).
        This one case deliberately falls back silently to the default
        instead of letting the caller (the periodic timer in
        `Runtime._resend_loop`) fail on it. A genuine database error (e.g. a
        locked file) is NOT caught by this and still raises -
        `_resend_loop` has its own error path for that (see there), which
        expects exactly that."""
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_INTERVAL_KEY,)
        ).fetchone()
        if row is None:
            return DEFAULT_RESEND_INTERVAL_SECONDS
        try:
            return float(row["value"])
        except (ValueError, TypeError):
            return DEFAULT_RESEND_INTERVAL_SECONDS

    def set_interval_seconds(self, seconds: float) -> None:
        if seconds < MIN_RESEND_INTERVAL_SECONDS:
            raise ValueError(
                f"resend interval must be at least {MIN_RESEND_INTERVAL_SECONDS}s, got: {seconds}"
            )
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_INTERVAL_KEY, str(seconds)),
        )
        self._db.commit()
