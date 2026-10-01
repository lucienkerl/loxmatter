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

"""One firmware row per device (design 2026-09-30, section 8).

Another view onto the store's connection, like `zigbee_pending_store.py`.
A device without a row has never been checked and never updated."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from loxmatter.sources.firmware import UpdateOffer

# Job states a new check clears (design section 5: shown until the next
# check). A running job is never cleared by a check.
_ENDED_JOB_STATES = ("failed", "interrupted")


@dataclass(frozen=True)
class FirmwareStatus:
    device_id: int
    checked_at: str | None
    check_error: str | None
    offer: UpdateOffer | None
    job_state: str | None
    job_progress: int | None
    job_started_at: str | None
    job_changed_at: str | None
    job_error: str | None
    queued_at: str | None


class FirmwareStatusStore:
    """Access to `firmware_status` through the store's connection."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def _ensure_row(self, device_id: int) -> None:
        self._db.execute(
            "INSERT INTO firmware_status (device_id) VALUES (?) ON CONFLICT(device_id) DO NOTHING",
            (device_id,),
        )

    @staticmethod
    def _as_status(row: sqlite3.Row) -> FirmwareStatus:
        offer = None
        if row["offer_version"] is not None:
            offer = UpdateOffer(
                software_version=int(row["offer_version"]),
                software_version_string=str(row["offer_version_string"] or ""),
                min_applicable=int(row["offer_min_applicable"] or 0),
                max_applicable=int(row["offer_max_applicable"] or 0),
                release_notes_url=row["offer_notes_url"],
                source=str(row["offer_source"] or ""),
            )
        return FirmwareStatus(
            device_id=int(row["device_id"]),
            checked_at=row["checked_at"],
            check_error=row["check_error"],
            offer=offer,
            job_state=row["job_state"],
            job_progress=None if row["job_progress"] is None else int(row["job_progress"]),
            job_started_at=row["job_started_at"],
            job_changed_at=row["job_changed_at"],
            job_error=row["job_error"],
            queued_at=row["queued_at"],
        )

    def get(self, device_id: int) -> FirmwareStatus | None:
        row = self._db.execute(
            "SELECT * FROM firmware_status WHERE device_id = ?", (device_id,)
        ).fetchone()
        return None if row is None else self._as_status(row)

    def all(self) -> dict[int, FirmwareStatus]:
        rows = self._db.execute("SELECT * FROM firmware_status").fetchall()
        return {int(row["device_id"]): self._as_status(row) for row in rows}

    def last_checked_at(self) -> str | None:
        row = self._db.execute("SELECT MAX(checked_at) AS latest FROM firmware_status").fetchone()
        return None if row is None else row["latest"]

    def record_check(self, device_id: int, offer: UpdateOffer | None, checked_at: str) -> None:
        """A successful check: replaces the offer (or clears it), clears the
        check error and an ended job."""
        self._ensure_row(device_id)
        self._db.execute(
            "UPDATE firmware_status SET checked_at = ?, check_error = NULL,"
            " offer_version = ?, offer_version_string = ?, offer_min_applicable = ?,"
            " offer_max_applicable = ?, offer_notes_url = ?, offer_source = ?"
            " WHERE device_id = ?",
            (
                checked_at,
                None if offer is None else offer.software_version,
                None if offer is None else offer.software_version_string,
                None if offer is None else offer.min_applicable,
                None if offer is None else offer.max_applicable,
                None if offer is None else offer.release_notes_url,
                None if offer is None else offer.source,
                device_id,
            ),
        )
        self._db.execute(
            "UPDATE firmware_status SET job_state = NULL, job_progress = NULL, job_error = NULL"
            " WHERE device_id = ? AND job_state IN"
            f" ({', '.join('?' for _ in _ENDED_JOB_STATES)})",
            (device_id, *_ENDED_JOB_STATES),
        )
        self._db.commit()

    def record_check_error(self, device_id: int, error: str, checked_at: str) -> None:
        """A failed check: keeps the previous offer (design section 5)."""
        self._ensure_row(device_id)
        self._db.execute(
            "UPDATE firmware_status SET checked_at = ?, check_error = ? WHERE device_id = ?",
            (checked_at, error, device_id),
        )
        self._db.commit()

    def drop_offer(self, device_id: int) -> None:
        self._db.execute(
            "UPDATE firmware_status SET offer_version = NULL, offer_version_string = NULL,"
            " offer_min_applicable = NULL, offer_max_applicable = NULL,"
            " offer_notes_url = NULL, offer_source = NULL WHERE device_id = ?",
            (device_id,),
        )
        self._db.commit()

    def start_job(self, device_id: int, started_at: str) -> None:
        self._ensure_row(device_id)
        self._db.execute(
            "UPDATE firmware_status SET job_state = 'transferring', job_progress = NULL,"
            " job_started_at = ?, job_changed_at = ?, job_error = NULL WHERE device_id = ?",
            (started_at, started_at, device_id),
        )
        self._db.commit()

    def update_job(self, device_id: int, state: str, progress: int | None, changed_at: str) -> None:
        self._ensure_row(device_id)
        self._db.execute(
            "UPDATE firmware_status SET job_state = ?, job_progress = ?, job_changed_at = ?"
            " WHERE device_id = ?",
            (state, progress, changed_at, device_id),
        )
        self._db.commit()

    def end_job(
        self, device_id: int, state: str | None, error: str | None, changed_at: str
    ) -> None:
        """Ends a job: `state` `None` on success, `failed`/`interrupted` otherwise."""
        self._ensure_row(device_id)
        self._db.execute(
            "UPDATE firmware_status SET job_state = ?, job_progress = NULL, job_error = ?,"
            " job_changed_at = ? WHERE device_id = ?",
            (state, error, changed_at, device_id),
        )
        self._db.commit()

    def enqueue(self, device_ids: Sequence[int], queued_at: str) -> int:
        """Queues each device not queued yet; one already queued keeps its place."""
        added = 0
        for device_id in device_ids:
            self._ensure_row(device_id)
            cursor = self._db.execute(
                "UPDATE firmware_status SET queued_at = ?"
                " WHERE device_id = ? AND queued_at IS NULL",
                (queued_at, device_id),
            )
            added += cursor.rowcount
        self._db.commit()
        return added

    def queued(self) -> list[int]:
        rows = self._db.execute(
            "SELECT device_id FROM firmware_status WHERE queued_at IS NOT NULL"
            " ORDER BY queued_at, device_id"
        ).fetchall()
        return [int(row["device_id"]) for row in rows]

    def dequeue(self, device_id: int) -> None:
        self._db.execute(
            "UPDATE firmware_status SET queued_at = NULL WHERE device_id = ?", (device_id,)
        )
        self._db.commit()

    def clear_queue(self) -> None:
        self._db.execute("UPDATE firmware_status SET queued_at = NULL")
        self._db.commit()
