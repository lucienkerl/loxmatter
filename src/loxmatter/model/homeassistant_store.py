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

"""What the Home Assistant migration has to remember across a restart
(design 2026-11-07, section 4).

Three values in the generic `setting` table, like `setup_store.py`, so no
schema bump:

- `homeassistant.connection` - the address of Home Assistant and the
  tokens this bridge signed in with. The refresh token is a credential for
  an administrator account; it lives here, in the bridge's own database,
  for as long as the migration needs it and is dropped by `forget()` when
  the user is done.
- `homeassistant.zigbee_backup` - ZHA's network backup, taken when ZHA is
  paused. It carries the Zigbee network key, so it never leaves the bridge
  through any route; it is kept because the user may unplug, replug and
  restart between pausing ZHA and writing the network onto a stick.
- `homeassistant.results` - which Home Assistant device became which
  loxmatter device. The clean-up step reads it: only a device that really
  arrived here may be removed from Home Assistant.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any, Final, Literal

_CONNECTION_KEY: Final = "homeassistant.connection"
_BACKUP_KEY: Final = "homeassistant.zigbee_backup"
_RESULTS_KEY: Final = "homeassistant.results"

ResultState = Literal["done", "failed"]


@dataclass(frozen=True)
class HomeAssistantConnection:
    """`refresh_token` is `None` for a long-lived access token, which never
    expires and cannot be refreshed; `expires_at` is a UNIX time."""

    url: str
    client_id: str | None
    access_token: str
    refresh_token: str | None
    expires_at: float | None


@dataclass(frozen=True)
class MigrationResult:
    """One Home Assistant device and what became of it here."""

    ha_device_id: str
    kind: str
    state: ResultState
    device_id: int | None = None
    detail: str | None = None
    cleaned_up: bool = False

    def as_json(self) -> dict[str, object]:
        return asdict(self)


class HomeAssistantStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    # ------------------------------------------------------------ helpers --

    def _get(self, key: str) -> Any:
        row = self._db.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(str(row["value"]))
        except ValueError:
            return None

    def _put(self, key: str, value: object) -> None:
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )
        self._db.commit()

    def _delete(self, key: str) -> None:
        self._db.execute("DELETE FROM setting WHERE key = ?", (key,))
        self._db.commit()

    # --------------------------------------------------------- connection --

    def connection(self) -> HomeAssistantConnection | None:
        raw = self._get(_CONNECTION_KEY)
        if not isinstance(raw, dict):
            return None
        url = raw.get("url")
        access_token = raw.get("access_token")
        if not isinstance(url, str) or not isinstance(access_token, str):
            return None
        client_id = raw.get("client_id")
        refresh_token = raw.get("refresh_token")
        expires_at = raw.get("expires_at")
        return HomeAssistantConnection(
            url=url,
            client_id=client_id if isinstance(client_id, str) else None,
            access_token=access_token,
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            expires_at=float(expires_at) if isinstance(expires_at, int | float) else None,
        )

    def save_connection(self, connection: HomeAssistantConnection) -> None:
        self._put(_CONNECTION_KEY, asdict(connection))

    # ------------------------------------------------------ zigbee backup --

    def zigbee_backup(self) -> dict[str, Any] | None:
        raw = self._get(_BACKUP_KEY)
        return raw if isinstance(raw, dict) else None

    def save_zigbee_backup(self, backup: dict[str, Any]) -> None:
        self._put(_BACKUP_KEY, backup)

    # ------------------------------------------------------------ results --

    def results(self) -> dict[str, MigrationResult]:
        raw = self._get(_RESULTS_KEY)
        if not isinstance(raw, dict):
            return {}
        found: dict[str, MigrationResult] = {}
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            state = value.get("state")
            if state not in ("done", "failed"):
                continue
            device_id = value.get("device_id")
            detail = value.get("detail")
            found[key] = MigrationResult(
                ha_device_id=key,
                kind=str(value.get("kind", "")),
                state=state,
                device_id=device_id if isinstance(device_id, int) else None,
                detail=detail if isinstance(detail, str) else None,
                cleaned_up=bool(value.get("cleaned_up", False)),
            )
        return found

    def record(self, result: MigrationResult) -> None:
        results = self.results()
        results[result.ha_device_id] = result
        self._put(_RESULTS_KEY, {key: value.as_json() for key, value in results.items()})

    # -------------------------------------------------------------- reset --

    def forget(self) -> None:
        """Drops the sign-in and the Zigbee backup - the end of a
        migration. The results stay: they are what tells a later run of the
        assistant which devices already came over."""
        self._delete(_CONNECTION_KEY)
        self._delete(_BACKUP_KEY)
