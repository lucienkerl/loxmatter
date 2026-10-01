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
    FirmwareQueueIn,
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
            try:
                busy_label = store.device(exc.device_id).label
            except UnknownDeviceError:
                # Removed while its update runs: the id is all that is left.
                busy_label = str(exc.device_id)
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

    @router.post("/firmware/queue", status_code=status.HTTP_202_ACCEPTED)
    async def enqueue(body: FirmwareQueueIn) -> FirmwareOverviewOut:
        _require_supported()
        if firmware.queue.enqueue(body.device_ids) == 0 and not set(body.device_ids) & set(
            store.firmware_status.queued()
        ):
            raise HTTPException(
                status_code=409, detail=i18n.t("api.firmware.queue_nothing_to_install")
            )
        firmware.queue.start()
        return firmware.overview()

    @router.post("/firmware/queue/resume")
    async def resume_queue() -> FirmwareOverviewOut:
        _require_supported()
        firmware.queue.resume()
        firmware.queue.start()
        return firmware.overview()

    @router.delete("/firmware/queue")
    async def clear_queue() -> FirmwareOverviewOut:
        firmware.queue.clear()
        return firmware.overview()

    @router.put("/firmware/settings")
    async def put_settings(body: FirmwareSettingsIn) -> FirmwareOverviewOut:
        store.firmware_settings.set_daily_check_enabled(body.daily_check_enabled)
        return firmware.overview()

    return router
