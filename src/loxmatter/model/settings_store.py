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

"""Access to the connection data of the bridge and the Miniserver - IPs and ports.

Its own module and its own class, analogous to `auth_store.py`: the
`setting` table is generic (key/value) by design, precisely so that further
configuration like this one can go the same way (see that module's
docstring, Spec 14.2 of the login design). This class is another view onto
the same table and the same connection, not a second connection.

See docs/superpowers/specs/2026-09-03-device-dashboard-and-export-design.md,
section 4: server-side instead of `localStorage`, because the bridge address
is a property of the installation, not of the browser."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from loxmatter.timestamps import now_iso

_BRIDGE_IP_KEY = "bridge_ip"
_BRIDGE_UDP_PORT_KEY = "bridge_udp_port"
_BRIDGE_LISTEN_PORT_KEY = "bridge_listen_port"
_BRIDGE_SETTINGS_SAVED_AT_KEY = "bridge_settings_saved_at"
# Since 2026-09-25 (design "The Miniserver's address is set in the web
# interface"): before that, the address only reached the bridge as
# `--miniserver`.
_MINISERVER_IP_KEY = "miniserver_ip"

_ALL_KEYS = (
    _BRIDGE_IP_KEY,
    _BRIDGE_UDP_PORT_KEY,
    _BRIDGE_LISTEN_PORT_KEY,
    _BRIDGE_SETTINGS_SAVED_AT_KEY,
    _MINISERVER_IP_KEY,
)


@dataclass(frozen=True)
class BridgeSettings:
    """`bridge_ip`/`miniserver_ip`/`saved_at` are `None` as long as nobody has
    saved (or, for `miniserver_ip`, seeded) anything - the ports fall back in
    that case to the default values that were set when the store was
    created."""

    bridge_ip: str | None
    udp_port: int
    listen_port: int
    miniserver_ip: str | None
    saved_at: str | None


class BridgeSettingsStore:
    """Access to `setting` via the store's connection - like `AuthStore`,
    just for different keys."""

    def __init__(
        self, db: sqlite3.Connection, *, default_udp_port: int, default_listen_port: int
    ) -> None:
        self._db = db
        self._default_udp_port = default_udp_port
        self._default_listen_port = default_listen_port

    def get(self) -> BridgeSettings:
        rows = self._db.execute(
            f"SELECT key, value FROM setting WHERE key IN ({', '.join('?' for _ in _ALL_KEYS)})",
            _ALL_KEYS,
        ).fetchall()
        values = {row["key"]: row["value"] for row in rows}
        return BridgeSettings(
            bridge_ip=values.get(_BRIDGE_IP_KEY),
            udp_port=int(values[_BRIDGE_UDP_PORT_KEY])
            if _BRIDGE_UDP_PORT_KEY in values
            else self._default_udp_port,
            listen_port=int(values[_BRIDGE_LISTEN_PORT_KEY])
            if _BRIDGE_LISTEN_PORT_KEY in values
            else self._default_listen_port,
            miniserver_ip=values.get(_MINISERVER_IP_KEY),
            saved_at=values.get(_BRIDGE_SETTINGS_SAVED_AT_KEY),
        )

    def save(
        self, *, bridge_ip: str, udp_port: int, listen_port: int, miniserver_ip: str | None
    ) -> BridgeSettings:
        """Writes all four values and the timestamp in one transaction -
        no partial update: the fields belong together by domain. A
        `miniserver_ip` of `None` removes the key, so `get()` reports no
        address rather than an empty string."""
        saved_at = now_iso()
        for key, value in (
            (_BRIDGE_IP_KEY, bridge_ip),
            (_BRIDGE_UDP_PORT_KEY, str(udp_port)),
            (_BRIDGE_LISTEN_PORT_KEY, str(listen_port)),
            (_BRIDGE_SETTINGS_SAVED_AT_KEY, saved_at),
        ):
            self._db.execute(
                "INSERT INTO setting (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        if miniserver_ip is None:
            self._db.execute("DELETE FROM setting WHERE key = ?", (_MINISERVER_IP_KEY,))
        else:
            self._upsert(_MINISERVER_IP_KEY, miniserver_ip)
        self._db.commit()
        return self.get()

    def seed_miniserver(self, ip: str, udp_port: int) -> bool:
        """Takes `loxmatter run --miniserver`/`--port` over on an installation
        that has no Miniserver address yet. Each key is written only where it
        is missing - a UDP port saved in the interface is what the Loxone
        project's templates say, and stays. `saved_at` is not touched:
        nobody saved anything in the interface. Returns whether the IP was
        written."""
        if self.get().miniserver_ip is not None:
            return False
        for key, value in ((_MINISERVER_IP_KEY, ip), (_BRIDGE_UDP_PORT_KEY, str(udp_port))):
            self._db.execute(
                "INSERT INTO setting (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                (key, value),
            )
        self._db.commit()
        return True

    def _upsert(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
