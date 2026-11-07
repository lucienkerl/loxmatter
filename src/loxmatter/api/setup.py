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

"""`/api/setup` - where the first-run assistant stands (design 2026-11-07).

The assistant itself is a view of the web UI that composes routes which
already exist - `/auth/setup` for the password, `PATCH /api/settings` for
the Miniserver, `POST /api/radios` and `PUT /api/zigbee/radio` for the
sticks, `/api/homeassistant/*` for the migration. These routes only keep
its place, so that a reload, a second browser or a bridge restart lands on
the same screen, and say when it is done.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from loxmatter import i18n
from loxmatter.model.setup_store import SetupState
from loxmatter.model.store import Store


class SetupPatch(BaseModel):
    step: str | None = Field(default=None, max_length=40)
    path: Literal["new", "ha"] | None = None


def build_setup_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _current() -> SetupState:
        has_devices = bool(store.devices())
        state = store.setup.get(has_devices=has_devices)
        if not store.setup.stored():
            # Written down the first time anyone asks: an installation that
            # was "completed" because it had devices stays completed once
            # its last device is removed (see `setup_store.py`).
            store.setup.save(state)
        return state

    @router.get("/setup")
    async def get_setup() -> dict[str, object]:
        return _current().as_json()

    @router.patch("/setup")
    async def patch_setup(patch: SetupPatch) -> dict[str, object]:
        state = _current()
        if state.completed:
            raise HTTPException(status_code=409, detail=i18n.t("api.setup.fail_completed"))
        return store.setup.update(step=patch.step, path=patch.path).as_json()

    @router.post("/setup/complete")
    async def complete_setup() -> dict[str, object]:
        _current()
        return store.setup.update(completed=True, step="done").as_json()

    @router.post("/setup/restart")
    async def restart_setup() -> dict[str, object]:
        """Opens the assistant again from Settings - for a user who skipped
        the migration and wants to fetch the rest from Home Assistant once
        a stick is there. Devices already set up are not touched."""
        _current()
        return store.setup.save(SetupState(completed=False, step="path", path=None)).as_json()

    return router
