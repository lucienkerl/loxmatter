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

"""Routes of the firmware update overview (design 2026-09-30, section 9.1)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from loxmatter import i18n
from loxmatter.api.models import (
    FirmwareCheckOut,
    FirmwareDeviceOut,
    FirmwareInstallIn,
    FirmwareOverviewOut,
    FirmwareSettingsIn,
)
from loxmatter.firmware.job import (
    DeviceOfflineError,
    FirmwareBusyError,
    FirmwareUnsupportedError,
    OfferChangedError,
)
from loxmatter.firmware.service import FirmwareService
from loxmatter.model.store import Store, StoredDevice, UnknownDeviceError


def build_firmware_router(store: Store, firmware: FirmwareService) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _device(device_id: int) -> StoredDevice:
        try:
            return store.device(device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _require_supported() -> None:
        if not firmware.supported():
            raise HTTPException(status_code=409, detail=i18n.t("api.firmware.fail_unsupported"))

    @router.get("/firmware")
    async def get_overview() -> FirmwareOverviewOut:
        return firmware.overview()

    @router.post("/firmware/check", status_code=status.HTTP_202_ACCEPTED)
    async def check_all() -> FirmwareCheckOut:
        _require_supported()
        progress = firmware.checker.start_all()
        return FirmwareCheckOut(
            running=progress.running, checked=progress.checked, total=progress.total
        )

    @router.post("/devices/{device_id}/firmware/check")
    async def check_one(device_id: int) -> FirmwareDeviceOut:
        device = _device(device_id)
        _require_supported()
        await firmware.checker.check_one(device_id)
        return firmware.device_out(device)

    @router.post("/devices/{device_id}/firmware/update", status_code=status.HTTP_202_ACCEPTED)
    async def install(device_id: int, body: FirmwareInstallIn) -> FirmwareDeviceOut:
        device = _device(device_id)
        try:
            firmware.jobs.start(device_id, body.software_version)
        except FirmwareUnsupportedError as exc:
            raise HTTPException(
                status_code=409, detail=i18n.t("api.firmware.fail_unsupported")
            ) from exc
        except FirmwareBusyError as exc:
            busy_label = _device(exc.device_id).label
            raise HTTPException(
                status_code=409, detail=i18n.t("api.firmware.fail_busy", device=busy_label)
            ) from exc
        except OfferChangedError as exc:
            raise HTTPException(
                status_code=409, detail=i18n.t("api.firmware.fail_offer_changed")
            ) from exc
        except DeviceOfflineError as exc:
            raise HTTPException(
                status_code=409, detail=i18n.t("api.firmware.fail_offline")
            ) from exc
        return firmware.device_out(device)

    @router.put("/firmware/settings")
    async def put_settings(body: FirmwareSettingsIn) -> FirmwareOverviewOut:
        store.firmware_settings.set_daily_check_enabled(body.daily_check_enabled)
        return firmware.overview()

    return router
