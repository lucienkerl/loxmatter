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

"""Zugriff auf die Verbindungsdaten dieser Bruecke - IP und Ports, wie sie
heute schon im Export-Tab eingegeben werden (`api/export.py`).

Eigenes Modul und eigene Klasse, analog zu `auth_store.py`: die `setting`-
Tabelle ist generisch (Schluessel/Wert) angelegt, genau damit weitere
Konfiguration wie diese hier denselben Weg gehen kann (siehe dortiger
Moduldocstring, Spec 14.2 des Login-Entwurfs). Diese Klasse ist eine weitere
Sicht auf dieselbe Tabelle und dieselbe Verbindung, kein zweiter
Verbindungsaufbau.

Siehe docs/superpowers/specs/2026-09-03-geraete-dashboard-und-export-design.md,
Abschnitt 4: serverseitig statt `localStorage`, weil die Bridge-Adresse eine
Eigenschaft der Installation ist, nicht des Browsers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from loxmatter.timestamps import now_iso

_BRIDGE_IP_KEY = "bridge_ip"
_BRIDGE_UDP_PORT_KEY = "bridge_udp_port"
_BRIDGE_LISTEN_PORT_KEY = "bridge_listen_port"
_BRIDGE_SETTINGS_SAVED_AT_KEY = "bridge_settings_saved_at"

_ALL_KEYS = (
    _BRIDGE_IP_KEY,
    _BRIDGE_UDP_PORT_KEY,
    _BRIDGE_LISTEN_PORT_KEY,
    _BRIDGE_SETTINGS_SAVED_AT_KEY,
)


@dataclass(frozen=True)
class BridgeSettings:
    """`bridge_ip`/`saved_at` sind `None`, solange niemand gespeichert hat -
    die Ports fallen in dem Fall auf die Vorgabewerte zurueck, die beim
    Erzeugen des Stores gesetzt wurden."""

    bridge_ip: str | None
    udp_port: int
    listen_port: int
    saved_at: str | None


class BridgeSettingsStore:
    """Zugriff auf `setting` ueber die Verbindung des Stores - wie
    `AuthStore`, nur fuer andere Schluessel."""

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
            saved_at=values.get(_BRIDGE_SETTINGS_SAVED_AT_KEY),
        )

    def save(self, *, bridge_ip: str, udp_port: int, listen_port: int) -> BridgeSettings:
        """Schreibt alle drei Werte und den Zeitstempel in einer Transaktion
        - kein Teil-Update: die drei Felder gehoeren fachlich zusammen."""
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
        self._db.commit()
        return self.get()
