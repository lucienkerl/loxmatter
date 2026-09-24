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

"""Which outputs a device or group exports (design 2026-09-24, 4.5).

Its own router rather than a part of `api/control.py`: that one drives
devices, this one edits what the next export carries - the same split
`api/devices.py`'s `PATCH /api/signals/{key}` makes for inputs. `PATCH
/api/commands/{key}` shares its path with `POST /api/commands/{key}` on
purpose: the key namespace is one (`d` vs `g` prefix), and a device key is
tried first, the way the send route does it.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, HTTPException

from loxmatter import i18n
from loxmatter.api.models import OutputOut, OutputPatch
from loxmatter.export.outputs import output_title
from loxmatter.model.store import (
    Store,
    StoredCommand,
    StoredGroupCommand,
    UnknownCommandError,
    UnknownDeviceError,
    UnknownGroupError,
)

__all__ = ["build_outputs_router"]


def _out(command: StoredCommand | StoredGroupCommand) -> OutputOut:
    return OutputOut(
        key=command.key,
        slug=command.slug,
        title=output_title(command.slug),
        exported=command.exported,
        functional=command.functional,
    )


def _ordered(commands: Sequence[StoredCommand | StoredGroupCommand]) -> list[OutputOut]:
    """Functional first, in the store's order within each part."""
    return [_out(c) for c in commands if c.functional] + [
        _out(c) for c in commands if not c.functional
    ]


def build_outputs_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/devices/{device_id}/outputs")
    async def device_outputs(device_id: int) -> list[OutputOut]:
        try:
            store.device(device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _ordered(store.commands(device_id))

    @router.get("/groups/{group_id}/outputs")
    async def group_outputs(group_id: int) -> list[OutputOut]:
        try:
            store.group(group_id)
        except UnknownGroupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _ordered(store.group_commands(group_id))

    @router.patch("/commands/{key}")
    async def patch_output(key: str, patch: OutputPatch) -> OutputOut:
        try:
            stored = store.resolve_command(key)
        except UnknownCommandError:
            try:
                store.resolve_group_command(key)
            except UnknownCommandError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            store.set_group_command_exported(key, patch.exported)
            return _out(store.resolve_group_command(key))
        try:
            store.device(stored.device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(
                status_code=404,
                detail=i18n.t(
                    "api.errors.command_belongs_to_removed_device",
                    command_key=key,
                    device_id=stored.device_id,
                ),
            ) from exc
        store.set_command_exported(key, patch.exported)
        return _out(store.resolve_command(key))

    return router
