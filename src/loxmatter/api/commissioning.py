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

"""Routes of the commissioning dialog (design 2026-10-02, section 10).

A thin layer over `CommissioningSession`: every route calls one session
method and turns a `CodeRejected` into the HTTP status the session chose
for it (422, 409 or 404, the detail already translated). Card routes answer
with the card as `GET /api/commissioning` shows it - which, like the whole
view, never contains a pairing code.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response, status

from loxmatter import i18n
from loxmatter.api.models import (
    CommissioningCardIn,
    CommissioningCodeIn,
    CommissioningScanIn,
    IdentifyRequest,
)
from loxmatter.commissioning.session import UNSET, CodeRejected, CommissioningSession, Unset
from loxmatter.matter.client import MatterUnavailableError
from loxmatter.sources import DeviceUnreachableError, IdentifyUnsupportedError


def _rejected(exc: CodeRejected) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def build_commissioning_router(session: CommissioningSession) -> APIRouter:
    router = APIRouter(prefix="/api/commissioning")

    @router.get("")
    async def view() -> dict[str, Any]:
        return session.view()

    @router.post("/scan", status_code=status.HTTP_202_ACCEPTED)
    async def scan(request: CommissioningScanIn | None = None) -> dict[str, Any]:
        automatic = request.automatic if request is not None else False
        # Refused only while a device is being commissioned; a skipped
        # automatic scan, a scan already running or a failed one (that
        # sets `bluetooth_warning`) all answer with the view.
        if session.busy:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=i18n.t("api.commissioning.fail_busy"),
            )
        await session.scan(automatic=automatic)
        return session.view()

    @router.post("/codes", status_code=status.HTTP_201_CREATED)
    async def add_code(request: CommissioningCodeIn) -> dict[str, Any]:
        try:
            card = await session.add_code(request.code, request.room)
        except CodeRejected as exc:
            raise _rejected(exc) from exc
        return session.card_view(card.id)

    @router.patch("/cards/{card_id}")
    async def update_card(card_id: int, request: CommissioningCardIn) -> dict[str, Any]:
        room: str | None | Unset = request.room if "room" in request.model_fields_set else UNSET
        try:
            await session.update_card(card_id, name=request.name, room=room)
        except CodeRejected as exc:
            raise _rejected(exc) from exc
        return session.card_view(card_id)

    @router.post("/start", status_code=status.HTTP_202_ACCEPTED)
    async def start() -> dict[str, Any]:
        await session.start()
        return session.view()

    @router.post("/cards/{card_id}/confirm-name")
    async def confirm_name(card_id: int) -> dict[str, Any]:
        try:
            await session.confirm_name(card_id)
        except CodeRejected as exc:
            raise _rejected(exc) from exc
        return session.card_view(card_id)

    @router.post("/cards/{card_id}/skip-name")
    async def skip_name(card_id: int) -> dict[str, Any]:
        try:
            await session.skip_name(card_id)
        except CodeRejected as exc:
            raise _rejected(exc) from exc
        return session.card_view(card_id)

    @router.post("/cards/{card_id}/force", status_code=status.HTTP_202_ACCEPTED)
    async def force(card_id: int) -> dict[str, Any]:
        try:
            await session.force(card_id)
        except CodeRejected as exc:
            raise _rejected(exc) from exc
        return session.card_view(card_id)

    @router.post("/cards/{card_id}/identify", status_code=status.HTTP_204_NO_CONTENT)
    async def identify(card_id: int, request: IdentifyRequest) -> Response:
        try:
            await session.identify_card(card_id, request.on)
        except CodeRejected as exc:
            raise _rejected(exc) from exc
        except IdentifyUnsupportedError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=i18n.t("api.commissioning.fail_no_identify"),
            ) from exc
        except (MatterUnavailableError, DeviceUnreachableError) as exc:
            # The same answer as `POST /api/devices/{id}/identify`.
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove(card_id: int) -> Response:
        try:
            session.remove(card_id)
        except CodeRejected as exc:
            raise _rejected(exc) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/clear")
    async def clear() -> dict[str, Any]:
        session.clear()
        return session.view()

    return router
