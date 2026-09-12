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

"""`GET` and `POST /api/radios` (design 2026-09-11 "Radios in the Web UI",
section 7).

The bridge only ever reads and writes files here. It validates what it can
see - the sidecar is ready, the device and adapter exist in its own
inventory - so the UI gets an immediate answer; the sidecar validates again
against the host and has the last word (section 6.3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from loxmatter import i18n
from loxmatter import update as update_files
from loxmatter.radios.inventory import match_current_device, scan_bluetooth, scan_serial
from loxmatter.radios.sidecar import (
    BluetoothRequest,
    RadiosBusyError,
    ThreadRequest,
    read_radios_state,
    request_radios,
    sidecar_status,
)


class ThreadIn(BaseModel):
    enabled: bool
    device: str | None


class BluetoothIn(BaseModel):
    adapter: int


class RadiosIn(BaseModel):
    """Both keys are required, and either may be `null`: `null` means "leave
    this radio alone" and is carried through to the sidecar unchanged
    (design section 6.3). Required-but-nullable rather than optional, so
    this schema stays as strict as the sidecar's own key whitelist - a card
    that forgets a half is a bug, not a request to skip it. The full
    both-halves shape every earlier version sent stays valid."""

    thread: ThreadIn | None
    bluetooth: BluetoothIn | None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def build_radios_router(
    update_dir: Path,
    *,
    host_dev: Path,
    sys_root: Path,
    clock: Callable[[], datetime] = _utc_now,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/radios")
    async def get_radios() -> dict[str, object]:
        update_state = update_files.read_state(update_dir)
        radios_state = read_radios_state(update_dir)
        status = sidecar_status(update_state, radios_state, now=clock())
        serial = scan_serial(host_dev, sys_root)
        bluetooth = scan_bluetooth(sys_root)
        current: dict[str, object] | None = None
        if status in ("ready", "unmounted") and radios_state and radios_state.current:
            reported = radios_state.current
            device, present = match_current_device(reported.thread_device, serial)
            current = {
                "thread_enabled": reported.thread_enabled,
                "thread_device": device,
                "thread_device_present": present,
                "bluetooth_adapter": reported.bluetooth_adapter,
                "otbr_running": reported.otbr_running,
            }
        job: dict[str, object] | None = None
        if radios_state is not None and radios_state.id is not None:
            job = {
                "id": radios_state.id,
                "phase": radios_state.phase,
                "steps": list(radios_state.steps),
                "error": radios_state.error,
                "rolled_back": radios_state.rolled_back,
                "healthy": radios_state.healthy,
            }
        return {
            "sidecar": status,
            # Why the card needs this, and why it cannot work it out from
            # `sidecar` alone: the two sidecar workers run SEQUENTIALLY in
            # one loop (deploy/updater/entrypoint.sh), so while
            # `update-once.sh` is inside a long pass - `pull`, `build`,
            # `recreate`, the up-to-120 s `health` wait - `radios-once.sh`
            # cannot run at all and `radios-state.json`'s `seen_at`
            # necessarily goes stale. `sidecar_status()` reads that
            # staleness as `outdated` (and, once `updater_seen_at` goes
            # stale too, as `missing`), which is how a perfectly current,
            # perfectly busy sidecar came to be reported as an old one.
            # The card's `outdated` text then told the user to run
            # `docker compose up -d --no-deps loxmatter-updater` on the
            # host - which would have sent SIGTERM into the container
            # performing their update, mid-update. Saying "an update is
            # running" instead costs one boolean and is simply true.
            "update_running": (
                update_state is not None and update_state.phase in update_files._RUNNING_PHASES
            ),
            "updater_stack_host_path": (
                update_state.updater_stack_host_path if update_state is not None else None
            ),
            "serial": [asdict(radio) for radio in serial],
            "bluetooth": [asdict(adapter) for adapter in bluetooth],
            "current": current,
            "job": job,
        }

    @router.post("/radios", status_code=202)
    async def post_radios(body: RadiosIn) -> dict[str, str]:
        status = sidecar_status(
            update_files.read_state(update_dir), read_radios_state(update_dir), now=clock()
        )
        if status != "ready":
            raise HTTPException(status_code=503, detail=i18n.t("api.radios.fail_sidecar_not_ready"))
        # Only the halves actually asked for are checked. A `null` half is
        # not a value that could be wrong - it is the absence of a request
        # for that radio, and the sidecar will not read, write, apply or
        # verify anything about it. Validating one anyway is what used to
        # strand the user whose configured Thread stick had fallen out: the
        # unknown-device check below fired on a value they were not asking
        # to change, and their Bluetooth change never happened.
        if body.thread is not None and body.thread.enabled:
            if body.thread.device is None:
                raise HTTPException(
                    status_code=400, detail=i18n.t("api.radios.fail_thread_device_required")
                )
            if body.thread.device not in {radio.path for radio in scan_serial(host_dev, sys_root)}:
                raise HTTPException(
                    status_code=400, detail=i18n.t("api.radios.fail_unknown_device")
                )
        if body.bluetooth is not None and body.bluetooth.adapter not in {
            adapter.index for adapter in scan_bluetooth(sys_root)
        }:
            raise HTTPException(status_code=400, detail=i18n.t("api.radios.fail_unknown_adapter"))
        try:
            job_id = request_radios(
                update_dir,
                thread=(
                    None
                    if body.thread is None
                    else ThreadRequest(
                        enabled=body.thread.enabled,
                        device=body.thread.device if body.thread.enabled else None,
                    )
                ),
                bluetooth=(
                    None if body.bluetooth is None else BluetoothRequest(body.bluetooth.adapter)
                ),
            )
        except RadiosBusyError as exc:
            raise HTTPException(status_code=409, detail=i18n.t("api.radios.fail_busy")) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=503, detail=i18n.t("api.radios.fail_unwritable", exc=str(exc))
            ) from exc
        return {"id": job_id}

    return router
