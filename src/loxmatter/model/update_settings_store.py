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

"""Which stream of published code this installation follows.

A setting in the database like every other one (design
`2026-09-08-webui-updates-design.md`, section 9), deliberately not an
environment variable: the operator's standing requirement is that
configuration is made in the interface, and a second place that only
`.env` knows about would defeat that on the very first setting stage 2
adds.

`LOXMATTER_IMAGE_TAG` in `.env` stays what it is - the deployed version
pointer, written by the updater and never by hand (see addendum 16.3).
The channel here says which stream to *compare against*; the tag says
what is *running*. Two questions, two homes.
"""

from __future__ import annotations

import enum
import sqlite3

_CHANNEL_KEY = "update_channel"


class UpdateChannel(str, enum.Enum):
    """`str` mixin so a value serialises as itself in API models and in
    the request file the updater reads - the sidecar must not have to
    know Python's enum repr to parse it."""

    STABLE = "stable"
    DEV = "dev"


class UpdateSettingsStore:
    """Access to `setting` through the store's connection - like
    `ResendSettingsStore`, for a different key."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_channel(self) -> UpdateChannel:
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_CHANNEL_KEY,)
        ).fetchone()
        if row is None:
            return UpdateChannel.STABLE
        try:
            return UpdateChannel(row["value"])
        except ValueError:
            # A row from a newer version, or written by hand. Falling back
            # to STABLE rather than raising: the bridge must start, and of
            # the two channels this is the one that cannot surprise
            # anybody.
            return UpdateChannel.STABLE

    def set_channel(self, channel: UpdateChannel) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO setting (key, value) VALUES (?, ?)",
            (_CHANNEL_KEY, channel.value),
        )
        self._db.commit()
