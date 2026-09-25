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

"""Connection settings of the bridge (IP, ports) and the interval of the
periodic resend via the API - device dashboard design (2026-09-03),
section 4, and periodic resend design (2026-09-04), section 5.

`build_settings_router` builds an `APIRouter` with prefix `/api`, just like
`api.devices.build_device_router` - wired into `loxone.server.build_app`
alongside the other routers of this phase, behind the same `api_guard`.

Since 2026-09-25 the route also holds the Miniserver's address: saving it
retargets the sender, resends every value in the background and probes the
address - design "The Miniserver's address is set in the web interface",
sections 7 and 8."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from typing import Protocol

from fastapi import APIRouter, HTTPException

from loxmatter import i18n
from loxmatter.api.models import (
    BridgeSettingsIn,
    BridgeSettingsOut,
    MiniserverCheckOut,
    ResendIntervalIn,
    ResendIntervalOut,
)
from loxmatter.loxone.probe import MiniserverProbe, ProbeOutcome, probe_miniserver
from loxmatter.loxone.sender import UdpSender
from loxmatter.model.store import Store

logger = logging.getLogger(__name__)


class Resender(Protocol):
    async def resend_all(self) -> int: ...


def _settings_out(store: Store, check: MiniserverCheckOut | None = None) -> BridgeSettingsOut:
    settings = store.settings.get()
    return BridgeSettingsOut(
        bridge_ip=settings.bridge_ip,
        udp_port=settings.udp_port,
        listen_port=settings.listen_port,
        miniserver_ip=settings.miniserver_ip,
        saved_at=settings.saved_at,
        miniserver_check=check,
    )


def _resend_interval_out(store: Store) -> ResendIntervalOut:
    return ResendIntervalOut(interval_seconds=store.resend_settings.get_interval_seconds())


def _miniserver_ip(patch: BridgeSettingsIn, stored: str | None) -> str | None:
    """Absent keeps `stored`; `null` or blank clears; anything else must be
    an IPv4 address."""
    if "miniserver_ip" not in patch.model_fields_set:
        return stored
    value = (patch.miniserver_ip or "").strip()
    if not value:
        return None
    try:
        ipaddress.IPv4Address(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=i18n.t(
                "api.settings.invalid_ipv4",
                field=i18n.t("web.settings.miniserver_ip_label"),
                value=value,
            ),
        ) from exc
    return value


def _check_out(ip: str, probe: MiniserverProbe) -> MiniserverCheckOut:
    unknown = "-"
    if probe.outcome is ProbeOutcome.FOUND:
        message = i18n.t(
            "api.settings.miniserver_found",
            ip=ip,
            serial=probe.serial or unknown,
            firmware=probe.firmware or unknown,
        )
    elif probe.outcome is ProbeOutcome.HTTP_ERROR:
        message = i18n.t("api.settings.miniserver_http_error", ip=ip, status=probe.http_status)
    else:
        message = i18n.t(f"api.settings.miniserver_{probe.outcome.value}", ip=ip)
    return MiniserverCheckOut(
        found=probe.found, serial=probe.serial, firmware=probe.firmware, message=message
    )


def build_settings_router(
    store: Store,
    *,
    sender: UdpSender | None = None,
    runtime: Resender | None = None,
) -> APIRouter:
    """`sender`/`runtime` are `None` in tests that build the app without
    them: the route then saves without retargeting anything, the same
    reduced mode the diagnostics check reports for a missing sender."""
    router = APIRouter(prefix="/api")
    # Held so a running resend is not garbage-collected mid-flight.
    resend_tasks: set[asyncio.Task[int]] = set()

    def _resend_done(task: asyncio.Task[int]) -> None:
        resend_tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            logger.error("full resend after a Miniserver address change failed", exc_info=exc)

    def _resend_in_background() -> None:
        # Not awaited: at the sender's rate limit a full resend of a large
        # installation takes seconds, and the answer should not wait for it.
        assert runtime is not None
        task = asyncio.create_task(runtime.resend_all())
        resend_tasks.add(task)
        task.add_done_callback(_resend_done)

    @router.get("/settings")
    async def get_settings() -> BridgeSettingsOut:
        return _settings_out(store)

    @router.patch("/settings")
    async def save_settings(patch: BridgeSettingsIn) -> BridgeSettingsOut:
        previous = store.settings.get()
        miniserver_ip = _miniserver_ip(patch, previous.miniserver_ip)
        store.settings.save(
            bridge_ip=patch.bridge_ip,
            udp_port=patch.udp_port,
            listen_port=patch.listen_port,
            miniserver_ip=miniserver_ip,
        )
        if sender is not None:
            before = sender.target
            sender.set_target(miniserver_ip, patch.udp_port)
            if sender.target is not None and sender.target != before and runtime is not None:
                _resend_in_background()
        check = None
        if miniserver_ip is not None and "miniserver_ip" in patch.model_fields_set:
            check = _check_out(miniserver_ip, await probe_miniserver(miniserver_ip))
        return _settings_out(store, check)

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
