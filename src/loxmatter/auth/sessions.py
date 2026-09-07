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

"""Sessions: create, check, extend on a sliding basis (spec 7).

**Why in the database and not in memory:** the service runs with
`restart: unless-stopped` (see `deploy/testhost/docker-compose.yml`). A
restart - after an update, after a power outage, after a crash - would
otherwise log out every logged-in browser, and the operator would see a
login screen instead of their bridge, with no idea why.

**Why a server-side entry and not a signed cookie:** a signed cookie could
not be revoked. `POST /auth/logout` and `loxmatter set-password` are meant
to actually end a session, not just ask the browser to forget it.

`now` is an optional parameter (Unix seconds) in both functions.
Production code never passes it; the tests need it to let thirty days
pass without sleeping.
"""

from __future__ import annotations

import secrets
import time

from loxmatter.model.auth_store import AuthStore

# The cookie name. Lives here and not in `api/auth.py` because two places
# need it: the router sets it, the guard in `loxone/server.py` reads it.
# Two different spellings of the same name would be a bug that no test
# would notice, because both sides work fine on their own.
SESSION_COOKIE = "loxmatter_session"

SESSION_LIFETIME_SECONDS = 30 * 24 * 60 * 60

# Only extended once more than one day of the lifetime has been used up -
# see `session_is_valid`.
_EXTEND_AFTER_SECONDS = 24 * 60 * 60


def open_session(auth: AuthStore, *, now: int | None = None) -> str:
    """Creates a session and returns its identifier.

    32 bytes from `secrets.token_hex` - the same order of magnitude as the
    recommended API token (`openssl rand -hex 32`), because this
    identifier is worth exactly the same: whoever holds it is logged in."""
    moment = int(time.time()) if now is None else now
    auth.purge_expired_sessions(moment)
    session_id = secrets.token_hex(32)
    auth.create_session(session_id, created_at=moment, expires_at=moment + SESSION_LIFETIME_SECONDS)
    return session_id


def session_is_valid(auth: AuthStore, session_id: str, *, now: int | None = None) -> bool:
    """Is this session still valid? Extends it on a sliding basis while at it.

    The extension happens at most once per `_EXTEND_AFTER_SECONDS`, not on
    every call: this function runs on EVERY request to `/api`, and the
    interface issues several per second while in use. An `UPDATE` per
    request would be a SQLite write operation for nothing.

    An expired session is deleted right here - the cleanup path that would
    otherwise never run without a fresh login."""
    moment = int(time.time()) if now is None else now
    expires_at = auth.session_expires_at(session_id)
    if expires_at is None:
        return False
    if expires_at <= moment:
        auth.delete_session(session_id)
        return False
    if expires_at - moment <= SESSION_LIFETIME_SECONDS - _EXTEND_AFTER_SECONDS:
        auth.extend_session(session_id, expires_at=moment + SESSION_LIFETIME_SECONDS)
    return True
