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
"""DCL answers cached in the store (design 2026-10-02, section 7)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from loxmatter.matter.dcl_types import DclModel, DclVendor


@dataclass(frozen=True)
class CachedVendor:
    entry: DclVendor | None
    fetched_at: str


@dataclass(frozen=True)
class CachedModel:
    entry: DclModel | None
    fetched_at: str


class DclStore:
    """`entry None` means "the DCL does not know this id"; the row is kept so
    the question is not asked again on every lookup."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def vendor(self, vendor_id: int) -> CachedVendor | None:
        row = self._db.execute(
            "SELECT * FROM dcl_vendor WHERE vendor_id = ?", (vendor_id,)
        ).fetchone()
        if row is None:
            return None
        entry = None if row["missing"] else DclVendor(vendor_id, str(row["vendor_name"]))
        return CachedVendor(entry, str(row["fetched_at"]))

    def model(self, vendor_id: int, product_id: int) -> CachedModel | None:
        row = self._db.execute(
            "SELECT * FROM dcl_model WHERE vendor_id = ? AND product_id = ?",
            (vendor_id, product_id),
        ).fetchone()
        if row is None:
            return None
        entry = None
        if not row["missing"]:
            entry = DclModel(
                vendor_id=vendor_id,
                product_id=product_id,
                name=str(row["product_name"]),
                part_number=row["part_number"],
                device_type=row["device_type"],
                initial_steps_hint=int(row["initial_steps_hint"] or 0),
                initial_steps_instruction=row["initial_steps_instruction"],
            )
        return CachedModel(entry, str(row["fetched_at"]))

    def put_vendor(self, vendor_id: int, entry: DclVendor | None, fetched_at: str) -> None:
        self._db.execute(
            "INSERT INTO dcl_vendor (vendor_id, vendor_name, missing, fetched_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(vendor_id) DO UPDATE SET vendor_name = excluded.vendor_name,"
            " missing = excluded.missing, fetched_at = excluded.fetched_at",
            (
                vendor_id,
                None if entry is None else entry.name,
                1 if entry is None else 0,
                fetched_at,
            ),
        )
        self._db.commit()

    def put_model(
        self, vendor_id: int, product_id: int, entry: DclModel | None, fetched_at: str
    ) -> None:
        self._db.execute(
            "INSERT INTO dcl_model (vendor_id, product_id, product_name, part_number,"
            " device_type, initial_steps_hint, initial_steps_instruction, missing, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(vendor_id, product_id) DO UPDATE SET"
            " product_name = excluded.product_name,"
            " part_number = excluded.part_number, device_type = excluded.device_type,"
            " initial_steps_hint = excluded.initial_steps_hint,"
            " initial_steps_instruction = excluded.initial_steps_instruction,"
            " missing = excluded.missing, fetched_at = excluded.fetched_at",
            (
                vendor_id,
                product_id,
                None if entry is None else entry.name,
                None if entry is None else entry.part_number,
                None if entry is None else entry.device_type,
                None if entry is None else entry.initial_steps_hint,
                None if entry is None else entry.initial_steps_instruction,
                1 if entry is None else 0,
                fetched_at,
            ),
        )
        self._db.commit()
