# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

"""Verbindungseinstellungen der Bruecke (IP, Ports) und das Intervall des
periodischen Resends ueber die API - Geraete-Dashboard-Entwurf
(2026-09-03), Abschnitt 4, und Entwurf periodischer Resend (2026-09-04),
Abschnitt 5.

`build_settings_router` baut einen `APIRouter` mit Praefix `/api`, genau wie
`api.devices.build_device_router` - eingebunden in `loxone.server.build_app`
neben den uebrigen Routern dieser Phase, hinter demselben `api_guard`."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from loxmatter.api.models import (
    BridgeSettingsIn,
    BridgeSettingsOut,
    ResendIntervalIn,
    ResendIntervalOut,
)
from loxmatter.model.store import Store


def _settings_out(store: Store) -> BridgeSettingsOut:
    settings = store.settings.get()
    return BridgeSettingsOut(
        bridge_ip=settings.bridge_ip,
        udp_port=settings.udp_port,
        listen_port=settings.listen_port,
        saved_at=settings.saved_at,
    )


def _resend_interval_out(store: Store) -> ResendIntervalOut:
    return ResendIntervalOut(interval_seconds=store.resend_settings.get_interval_seconds())


def build_settings_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/settings")
    async def get_settings() -> BridgeSettingsOut:
        return _settings_out(store)

    @router.patch("/settings")
    async def save_settings(patch: BridgeSettingsIn) -> BridgeSettingsOut:
        store.settings.save(
            bridge_ip=patch.bridge_ip,
            udp_port=patch.udp_port,
            listen_port=patch.listen_port,
        )
        return _settings_out(store)

    @router.get("/settings/resend-interval")
    async def get_resend_interval() -> ResendIntervalOut:
        return _resend_interval_out(store)

    @router.patch("/settings/resend-interval")
    async def save_resend_interval(patch: ResendIntervalIn) -> ResendIntervalOut:
        try:
            store.resend_settings.set_interval_seconds(patch.interval_seconds)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _resend_interval_out(store)

    return router
