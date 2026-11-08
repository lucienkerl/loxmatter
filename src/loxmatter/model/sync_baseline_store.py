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

"""What the last project-file sync left both sides agreeing on (design
2026-11-08, two-way project sync, section 4).

A difference between loxmatter and the Loxone file says nothing about
which side moved. The baseline does: if Loxone still shows the value both
sides had after the last sync, loxmatter changed; otherwise Loxone did.
It is a JSON object in the `setting` table rather than a table of its
own, because it is read and written whole, once per sync.

`kept` holds the orphaned Loxone objects the user chose to keep, by owner
prefix (`d8`, `g2`), so the next sync offers them unticked."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

_KEY = "projectsync.baseline"


@dataclass(frozen=True)
class OwnerBaseline:
    room: str | None
    name: str | None


@dataclass(frozen=True)
class SyncBaseline:
    # "device:5" / "group:2" -> what both sides showed after the last sync.
    owners: dict[str, OwnerBaseline] = field(default_factory=dict)
    kept: frozenset[str] = frozenset()


class SyncBaselineStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get(self) -> SyncBaseline:
        row = self._db.execute("SELECT value FROM setting WHERE key = ?", (_KEY,)).fetchone()
        if row is None:
            return SyncBaseline()
        try:
            data = json.loads(str(row["value"]))
            owners = {
                str(owner): OwnerBaseline(room=values.get("room"), name=values.get("name"))
                for owner, values in data.get("owners", {}).items()
            }
            kept = frozenset(str(prefix) for prefix in data.get("kept", []))
        except (ValueError, AttributeError, TypeError):
            # A damaged value only costs the preselection, never the sync.
            return SyncBaseline()
        return SyncBaseline(owners, kept)

    def set(self, baseline: SyncBaseline) -> None:
        value = json.dumps(
            {
                "owners": {
                    owner: {"room": b.room, "name": b.name}
                    for owner, b in sorted(baseline.owners.items())
                },
                "kept": sorted(baseline.kept),
            },
            ensure_ascii=False,
        )
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_KEY, value),
        )
        self._db.commit()
