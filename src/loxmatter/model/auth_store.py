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

"""The part of the store that manages access - password hash and
sessions - as opposed to devices, signals and commands.

Its own module and its own class, not further methods on `Store`: that class
now carries over nine hundred lines for the device model, and access has
nothing to do with that domain. The connection still belongs to `Store`,
though - this class is a view onto it, not a second connection to the same
file (that would be a second lock domain for the same data).

What does NOT happen here: cryptography and HTTP. This class stores a hash
and reads it back without knowing how it is produced (see
`loxmatter.auth.passwords`), and it knows neither cookies nor status codes
(see `loxmatter.auth.sessions` and `loxmatter.api.auth`). Mixing those in
here would end up with three places where a secret can turn up instead of
one.

The schema of the two tables lives in `store.py` under `_SCHEMA` and
`_migrate_to_v4` - schema definitions stay in one place, even though access
to them lives here.
"""

from __future__ import annotations

import sqlite3

# The only key `setting` carries so far. The table is nonetheless generic
# (key/value) by design, because the rest of the configuration is meant to
# go the same way (Spec 14.2) - a `password` table with one column would
# have to be rebuilt the moment that happens.
_PASSWORD_KEY = "password_hash"


class AuthStore:
    """Access to `setting` and `session` via the store's connection."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def password_hash(self) -> str | None:
        """The stored hash - `None` as long as no password has been set.

        `None` is the state the entire access layer hinges on: it means
        "initial setup still open" and, per `loxone.server.build_api_guard`,
        blocks every single `/api` route."""
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_PASSWORD_KEY,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def set_password_hash_if_unset(self, value: str) -> bool:
        """Stores the hash, but only if none is there yet - `True` if this
        call is the one that set it.

        `INSERT OR IGNORE` rather than "check first, then write": SQLite
        decides this in a single statement, so two concurrent setup attempts
        cannot overwrite each other. `POST /auth/setup` relies on exactly
        this to answer 409 permanently after the first success."""
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO setting (key, value) VALUES (?, ?)",
            (_PASSWORD_KEY, value),
        )
        self._db.commit()
        return cursor.rowcount == 1

    def set_password_hash(self, value: str) -> None:
        """Sets the hash, overwriting any existing one, committing on its
        own.

        NOT the path for `loxmatter set-password` (see `reset_password`
        below, which folds this statement together with signing out every
        session into ONE transaction) - this piece stays public because test
        code uses it to preset a password in a fixture without touching any
        session."""
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_PASSWORD_KEY, value),
        )
        self._db.commit()

    def reset_password(self, value: str) -> None:
        """Sets a new hash and signs out every session - in ONE transaction,
        not as two separately committing steps.

        The only caller is `loxmatter set-password` (Spec 9, emergency
        escape hatch). Separately committing statements would leave a window
        open in which the new password already applies while an old session
        - meant to be signed out - keeps running: if the second step fails
        (e.g. a full disk between the two commits), exactly the state this
        command was built against would remain. A shared commit makes that
        impossible - either both take effect afterward or neither does."""
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_PASSWORD_KEY, value),
        )
        self._db.execute("DELETE FROM session")
        self._db.commit()

    def create_session(self, session_id: str, *, created_at: int, expires_at: int) -> None:
        self._db.execute(
            "INSERT INTO session (id, created_at, expires_at) VALUES (?, ?, ?)",
            (session_id, created_at, expires_at),
        )
        self._db.commit()

    def session_expires_at(self, session_id: str) -> int | None:
        """Expiry time as Unix seconds - `None` if the session does not (or
        no longer) exist. Whether it is therefore still valid is decided by
        `loxmatter.auth.sessions`, not this class."""
        row = self._db.execute(
            "SELECT expires_at FROM session WHERE id = ?", (session_id,)
        ).fetchone()
        return None if row is None else int(row["expires_at"])

    def extend_session(self, session_id: str, *, expires_at: int) -> None:
        self._db.execute("UPDATE session SET expires_at = ? WHERE id = ?", (expires_at, session_id))
        self._db.commit()

    def delete_session(self, session_id: str) -> None:
        self._db.execute("DELETE FROM session WHERE id = ?", (session_id,))
        self._db.commit()

    def delete_all_sessions(self) -> None:
        """Signs everyone out. Called by `loxmatter set-password`: whoever
        resets the password does not want an old session to keep
        running."""
        self._db.execute("DELETE FROM session")
        self._db.commit()

    def purge_expired_sessions(self, now: int) -> None:
        """Clears out expired rows. Called when creating a new session - no
        background job for a table that normally holds a handful of
        rows."""
        self._db.execute("DELETE FROM session WHERE expires_at <= ?", (now,))
        self._db.commit()
