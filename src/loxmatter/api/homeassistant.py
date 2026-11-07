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

"""`/api/homeassistant/*` - the migration from Home Assistant (design
2026-11-07). Each route is one step of `homeassistant/migration.py`.

**A failed Home Assistant sign-in answers 403, never 401.** The web UI
reads a 401 from any `/api` route as "your session with THIS bridge has
ended" and sends the user back to the login screen - for an expired Home
Assistant token that would be the wrong screen entirely.
"""

from __future__ import annotations

from collections.abc import Awaitable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from loxmatter import i18n
from loxmatter.homeassistant.client import HomeAssistantAuthError, HomeAssistantError
from loxmatter.homeassistant.migration import HomeAssistantMigration, MigrationError
from loxmatter.matter.client import MatterUnavailableError
from loxmatter.matter.otbr import ThreadDatasetUnavailableError
from loxmatter.matter.thread_network import ThreadNetworkInUseError


class UrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=300)


class SignInStartIn(UrlIn):
    origin: str = Field(min_length=1, max_length=300)


class SignInFinishIn(BaseModel):
    code: str = Field(min_length=1, max_length=500)
    state: str = Field(min_length=1, max_length=100)


class TokenIn(UrlIn):
    token: str = Field(min_length=20, max_length=1000)


class DevicesIn(BaseModel):
    devices: list[str] = Field(max_length=500)


class RestoreIn(BaseModel):
    overwrite_ieee: bool = True


class CleanUpIn(DevicesIn):
    remove_zha: bool = False


async def _answer[T](call: Awaitable[T]) -> T:
    """One mapping from the migration's failures to HTTP, for every route."""
    try:
        return await call
    except MigrationError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except HomeAssistantAuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except HomeAssistantError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ThreadNetworkInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ThreadDatasetUnavailableError, MatterUnavailableError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def build_homeassistant_router(migration: HomeAssistantMigration) -> APIRouter:
    router = APIRouter(prefix="/api/homeassistant")

    @router.get("")
    async def get_status() -> dict[str, object]:
        return migration.status()

    @router.post("/probe")
    async def probe(body: UrlIn) -> dict[str, object]:
        return {"url": await _answer(migration.probe(body.url))}

    @router.post("/auth/start")
    async def start_sign_in(body: SignInStartIn) -> dict[str, object]:
        try:
            url = migration.start_sign_in(body.url, body.origin)
        except HomeAssistantError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"authorize_url": url}

    @router.post("/auth/finish")
    async def finish_sign_in(body: SignInFinishIn) -> dict[str, object]:
        await _answer(migration.finish_sign_in(body.code, body.state))
        return migration.status()

    @router.post("/auth/token")
    async def sign_in_with_token(body: TokenIn) -> dict[str, object]:
        await _answer(migration.sign_in_with_token(body.url, body.token))
        return migration.status()

    @router.post("/sign-out")
    async def sign_out() -> dict[str, object]:
        migration.sign_out()
        return migration.status()

    @router.get("/inventory")
    async def inventory() -> dict[str, object]:
        return (await _answer(migration.inventory())).as_json()

    @router.post("/thread")
    async def adopt_thread() -> dict[str, object]:
        return await _answer(migration.adopt_thread_network())

    @router.get("/job")
    async def job() -> dict[str, object]:
        return {"job": migration.job()}

    @router.post("/matter", status_code=202)
    async def start_matter(body: DevicesIn) -> dict[str, object]:
        return await _answer(migration.start_matter(body.devices))

    @router.get("/zigbee")
    async def zigbee_status() -> dict[str, object]:
        return migration.zigbee_status()

    @router.post("/zigbee/pause")
    async def pause_zha() -> dict[str, object]:
        return await _answer(migration.pause_zha())

    @router.post("/zigbee/restore")
    async def restore(body: RestoreIn) -> dict[str, object]:
        return await _answer(migration.restore_zigbee_network(overwrite_ieee=body.overwrite_ieee))

    @router.post("/zigbee/import", status_code=202)
    async def start_zigbee(body: DevicesIn) -> dict[str, object]:
        return await _answer(migration.start_zigbee(body.devices))

    @router.post("/cleanup")
    async def clean_up(body: CleanUpIn) -> dict[str, object]:
        if not body.devices and not body.remove_zha:
            raise HTTPException(status_code=422, detail=i18n.t("api.homeassistant.fail_nothing"))
        return await _answer(migration.clean_up(body.devices, remove_zha=body.remove_zha))

    return router
