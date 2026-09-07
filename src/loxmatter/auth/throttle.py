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

"""Brake against brute-forcing passwords (spec 8).

The reason this module exists at all: a password is guessable, a token
from `openssl rand -hex 32` is not. Without a brake, login would therefore
be the weaker way into the same service - and this design would have made
the security worse while making it more convenient.

**In memory and not in the database:** this is transient state that does
not justify a write on every failed attempt. A restart clears it - but an
attacker cannot trigger one, and an operator who restarts to be able to
log back in faster does not consider their own password an attack anyway.

**`time.monotonic` and not `time.time`:** a clock change or an NTP jump
must neither extend nor lift a lockout.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

FAILURES_BEFORE_THROTTLING = 5
THROTTLE_SECONDS = 30


@dataclass
class LoginThrottle:
    """Counts failed attempts per caller. One instance per router, see
    `api.auth.build_auth_router`."""

    _failures: dict[str, int] = field(default_factory=dict)
    _blocked_until: dict[str, float] = field(default_factory=dict)

    def retry_after(self, client: str, *, now: float | None = None) -> int:
        """How many seconds this caller still has to wait - `0` if they may
        try again immediately.

        Rounded up so the message in the interface ("possible again in X
        seconds") never invites a retry too early."""
        moment = time.monotonic() if now is None else now
        blocked_until = self._blocked_until.get(client)
        if blocked_until is None or blocked_until <= moment:
            return 0
        return int(blocked_until - moment) + 1

    def record_failure(self, client: str, *, now: float | None = None) -> None:
        moment = time.monotonic() if now is None else now
        count = self._failures.get(client, 0) + 1
        self._failures[client] = count
        if count >= FAILURES_BEFORE_THROTTLING:
            self._blocked_until[client] = moment + THROTTLE_SECONDS

    def record_success(self, client: str) -> None:
        """Resets counter and lockout - whoever knows the password is not
        an attacker, even if they mistyped it five times before."""
        self._failures.pop(client, None)
        self._blocked_until.pop(client, None)
