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

"""Installing several firmware updates one after another (design 2026-10-01).

The queue lives in the store (`firmware_status.queued_at`), so it outlives a
restart of loxmatter. This class only drives it: one task takes the first
queued device, hands it to the unchanged `FirmwareJobs`, waits for the end,
and takes the next. A failed device does not stop it; a lost link to
matter-server does, because every further device would fail the same way."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence

from loxmatter import i18n
from loxmatter.firmware import states
from loxmatter.firmware.job import (
    DeviceOfflineError,
    FirmwareBusyError,
    FirmwareJobs,
    FirmwareUnsupportedError,
    OfferChangedError,
)
from loxmatter.model.store import Store, UnknownDeviceError
from loxmatter.sources.firmware import FirmwareSource
from loxmatter.timestamps import now_iso

logger = logging.getLogger(__name__)

HALTED_DISCONNECTED = "api.firmware.queue_halted_disconnected"
HALTED_UNSUPPORTED = "api.firmware.queue_halted_unsupported"
HALTED_ERROR = "api.firmware.queue_halted_error"


class FirmwareQueue:
    def __init__(
        self,
        store: Store,
        jobs: FirmwareJobs,
        source_for: Callable[[], FirmwareSource | None],
        *,
        now: Callable[[], str] = now_iso,
    ) -> None:
        self._store = store
        self._jobs = jobs
        self._source_for = source_for
        self._now = now
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        # The device this queue handed to `FirmwareJobs` last; while it still
        # installs, the queue counts as active although nothing is queued.
        self._current: int | None = None

    @property
    def active(self) -> bool:
        if self._store.firmware_settings.get_queue_halted_reason() is not None:
            return False
        if self._store.firmware_status.queued():
            return True
        return self._current is not None and self._jobs.running_device_id == self._current

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run())
        self._kick()

    async def stop(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise

    def enqueue(self, device_ids: Sequence[int]) -> int:
        """Queues the active devices that have a stored offer; returns how
        many were newly queued."""
        eligible = []
        for device_id in device_ids:
            try:
                self._store.device(device_id)
            except UnknownDeviceError:
                continue
            status = self._store.firmware_status.get(device_id)
            if status is not None and status.offer is not None:
                eligible.append(device_id)
        added = self._store.firmware_status.enqueue(eligible, self._now())
        if eligible:
            self._store.firmware_settings.set_queue_halted_reason(None)
            self._kick()
        return added

    def resume(self) -> None:
        self._store.firmware_settings.set_queue_halted_reason(None)
        self._kick()

    def clear(self) -> None:
        self._store.firmware_status.clear_queue()
        self._store.firmware_settings.set_queue_halted_reason(None)

    async def wait_idle(self) -> None:
        await self._idle.wait()

    def _kick(self) -> None:
        self._idle.clear()
        self._wake.set()

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            try:
                await self._drain()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("the firmware update queue failed")
                try:
                    self._halt(HALTED_ERROR)
                except Exception:
                    logger.exception("recording the halt of the firmware update queue failed")
            if not self._wake.is_set():
                self._idle.set()

    def _halt(self, reason: str) -> None:
        self._store.firmware_settings.set_queue_halted_reason(reason)

    async def _drain(self) -> None:
        while True:
            if self._store.firmware_settings.get_queue_halted_reason() is not None:
                return
            queued = self._store.firmware_status.queued()
            if not queued:
                return
            if self._jobs.running_device_id is not None:
                await self._jobs.wait()
                continue
            source = self._source_for()
            if source is None or not source.firmware_supported():
                self._halt(HALTED_UNSUPPORTED)
                return
            await self._install(source, queued[0])

    async def _install(self, source: FirmwareSource, device_id: int) -> None:
        status = self._store.firmware_status.get(device_id)
        try:
            device = self._store.device(device_id)
        except UnknownDeviceError:
            self._store.firmware_status.dequeue(device_id)
            return
        offer = None if status is None else status.offer
        facts = source.firmware_facts(device.address)
        if offer is None or (
            facts is not None
            and facts.software_version is not None
            and facts.software_version >= offer.software_version
        ):
            # Nothing left to install: reached already, or the offer is gone.
            self._store.firmware_status.dequeue(device_id)
            return
        if facts is None or not facts.available:
            self._offline(device_id)
            return
        try:
            self._jobs.start(device_id, offer.software_version)
        except FirmwareBusyError:
            # A single install started in the same instant; the device stays
            # first and the loop waits for that install.
            return
        except FirmwareUnsupportedError:
            self._halt(HALTED_UNSUPPORTED)
            return
        except DeviceOfflineError:
            self._offline(device_id)
            return
        except (OfferChangedError, UnknownDeviceError):
            self._store.firmware_status.dequeue(device_id)
            return
        self._store.firmware_status.dequeue(device_id)
        self._current = device_id
        try:
            # `jobs.stop()` cancelling the job passes through and ends this
            # task; fine, as shutdown stops the queue first and the store
            # keeps the queue.
            await self._jobs.wait()
        finally:
            self._current = None
        ended = self._store.firmware_status.get(device_id)
        if ended is not None and ended.job_state == states.INTERRUPTED:
            self._halt(HALTED_DISCONNECTED)

    def _offline(self, device_id: int) -> None:
        self._store.firmware_status.dequeue(device_id)
        self._store.firmware_status.end_job(
            device_id, states.FAILED, i18n.t("api.firmware.queue_offline_at_turn"), self._now()
        )
