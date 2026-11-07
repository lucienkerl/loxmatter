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

"""Where the first-run setup assistant stands (design 2026-11-07).

One JSON value under the key `"setup"` in the generic `setting` table, the
same way `zigbee_settings_store.py` keeps its setting: no table of its own
and no schema bump, because a schema rise is the one change an updater
rollback cannot undo for free.

**An installation without a record is not necessarily new.** Every bridge
that ran before this version has no record either, and showing those users
a welcome screen on their next update would be a regression. `get()`
therefore answers "completed" for an installation that already has
devices, and the API writes that answer down the first time it is asked,
so that removing the last device later does not bring the assistant back.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from typing import Final, Literal

_SETUP_KEY: Final = "setup"

SetupPath = Literal["new", "ha"]
_PATHS: Final = frozenset({"new", "ha"})


@dataclass(frozen=True)
class SetupState:
    """`step` is the assistant's own step name, opaque to the bridge: the
    web UI owns the order of its screens, the bridge only remembers where
    the user left off so a reload or a second browser lands there too."""

    completed: bool = False
    step: str | None = None
    path: SetupPath | None = None

    def as_json(self) -> dict[str, object]:
        return asdict(self)


class SetupStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def _read(self) -> SetupState | None:
        row = self._db.execute("SELECT value FROM setting WHERE key = ?", (_SETUP_KEY,)).fetchone()
        if row is None:
            return None
        try:
            raw = json.loads(str(row["value"]))
        except ValueError:
            return None
        if not isinstance(raw, dict):
            return None
        step = raw.get("step")
        path = raw.get("path")
        return SetupState(
            completed=bool(raw.get("completed", False)),
            step=step if isinstance(step, str) else None,
            path=path if path in _PATHS else None,
        )

    def stored(self) -> bool:
        """Whether a record exists at all - what tells a new installation
        from one that predates the assistant."""
        return self._read() is not None

    def get(self, *, has_devices: bool = False) -> SetupState:
        """The stored state, or for an installation without a record:
        completed when it already has devices (see the module docstring),
        otherwise a fresh start."""
        state = self._read()
        if state is not None:
            return state
        return SetupState(completed=has_devices)

    def save(self, state: SetupState) -> SetupState:
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_SETUP_KEY, json.dumps(state.as_json())),
        )
        self._db.commit()
        return state

    def update(
        self,
        *,
        has_devices: bool = False,
        step: str | None = None,
        path: SetupPath | None = None,
        completed: bool | None = None,
    ) -> SetupState:
        """Changes only what is passed; `None` leaves a field as it is."""
        state = self.get(has_devices=has_devices)
        if step is not None:
            state = replace(state, step=step)
        if path is not None:
            if path not in _PATHS:
                raise ValueError(f"unknown setup path {path!r}")
            state = replace(state, path=path)
        if completed is not None:
            state = replace(state, completed=completed)
        return self.save(state)
