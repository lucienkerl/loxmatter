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

"""The clusters of a Zigbee device that still have to be configured.

One row per `(address, endpoint, cluster)` that configure-on-join has not
finished with (Zigbee source design, section 6.4). Its own module and its
own class, the way `resend_settings_store.py` is another view onto the same
connection rather than a second connection.

**Why this is on disk and not a set on the source.** A
`configure_reporting` sent to a SLEEPING device fails with `TimeoutError`
or `DeliveryError` after up to ~28 s per attempt, and the device may not
wake for hours - a contact sensor on a door nobody opens can stay asleep
until the next morning. A set held in memory would be lost on every
restart of the bridge, and the sensor would then stay silent forever with
nothing anywhere recording why.

**A row IS the recovery record.** It is written BEFORE the attempt and
deleted only after that cluster really succeeded, so its meaning - "this
cluster still needs configuring" - is true whether the run finished,
failed, or was killed halfway through. That is deliberate: it means there
is no non-terminal phase a process can freeze in, and nothing an SSH
session has to clear by hand. Writing the rows only after a completed pass
would invert exactly that property - a run killed mid-way would leave no
trace at all, and the device would never be retried.
"""

from __future__ import annotations

import sqlite3


class ZigbeePendingStore:
    """Access to `zigbee_pending_config` through the store's connection."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def mark_pending(self, address: str, endpoint: int, cluster_id: int) -> None:
        """Records that this cluster still has to be configured.

        Idempotent: the primary key is the triple itself, so a cluster that
        has been deferred five times is still one row. `DO NOTHING` rather
        than a replace - there is nothing else on the row to update."""
        self._db.execute(
            "INSERT INTO zigbee_pending_config (address, endpoint, cluster_id) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(address, endpoint, cluster_id) DO NOTHING",
            (address, int(endpoint), int(cluster_id)),
        )
        self._db.commit()

    def clear(self, address: str, endpoint: int, cluster_id: int) -> None:
        """Forgets one cluster - it has been configured."""
        self._db.execute(
            "DELETE FROM zigbee_pending_config "
            "WHERE address = ? AND endpoint = ? AND cluster_id = ?",
            (address, int(endpoint), int(cluster_id)),
        )
        self._db.commit()

    def pending_for(self, address: str) -> list[tuple[int, int]]:
        """The `(endpoint, cluster)` pairs one device still owes, in a
        stable order so a retry always works through them the same way."""
        return [
            (int(row["endpoint"]), int(row["cluster_id"]))
            for row in self._db.execute(
                "SELECT endpoint, cluster_id FROM zigbee_pending_config "
                "WHERE address = ? ORDER BY endpoint, cluster_id",
                (address,),
            )
        ]

    def addresses_with_pending(self) -> list[str]:
        """Every device with unfinished business, for a bridge that has just
        started and wants to know which devices to watch for."""
        return [
            str(row["address"])
            for row in self._db.execute(
                "SELECT DISTINCT address FROM zigbee_pending_config ORDER BY address"
            )
        ]

    def forget(self, address: str) -> None:
        """Drops every row of one device.

        Called from `Store.forget_device`: removing and re-pairing a device
        must not make the new pairing inherit the old one's unfinished
        clusters, which belong to bindings the device no longer has."""
        self._db.execute("DELETE FROM zigbee_pending_config WHERE address = ?", (address,))
        self._db.commit()
