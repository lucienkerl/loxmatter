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

"""Only one device blinks at a time (design 2026-10-02, section 9.2)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Final

from loxmatter.sources import IdentifySource, IdentifyUnsupportedError

logger = logging.getLogger(__name__)

IDENTIFY_SECONDS: Final = 30
RENEW_EVERY: Final = 25


class IdentifyCoordinator:
    def __init__(
        self,
        source_for: Callable[[str], IdentifySource | None],
        device_address: Callable[[int], tuple[str, str]],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._source_for = source_for
        self._device_address = device_address
        self._sleep = sleep
        self._blinking: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def blinking(self) -> int | None:
        return self._blinking

    async def start(self, device_id: int, *, renew: bool, seconds: int = IDENTIFY_SECONDS) -> None:
        async with self._lock:
            await self._stop_locked()
            source, address = self._resolve(device_id)
            await source.identify(address, seconds)
            self._blinking = device_id
            self._task = asyncio.ensure_future(
                self._keep(device_id, source, address, renew, seconds)
            )

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_locked()

    async def aclose(self) -> None:
        await self.stop()

    def _resolve(self, device_id: int) -> tuple[IdentifySource, str]:
        technology, address = self._device_address(device_id)
        source = self._source_for(technology)
        if source is None:
            raise IdentifyUnsupportedError(technology)
        return source, address

    async def _stop_locked(self) -> None:
        task, device_id = self._task, self._blinking
        self._task, self._blinking = None, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if device_id is not None:
            try:
                source, address = self._resolve(device_id)
                await source.identify(address, 0)
            except Exception as exc:  # noqa: BLE001 - the device stops by itself within 30 s
                logger.info("Stopping identify on device %s failed: %s", device_id, exc)

    async def _keep(
        self, device_id: int, source: IdentifySource, address: str, renew: bool, seconds: int
    ) -> None:
        if not renew:
            await self._sleep(seconds)
            if self._blinking == device_id:
                self._blinking = None
                self._task = None
            return
        while True:
            await self._sleep(RENEW_EVERY)
            try:
                await source.identify(address, seconds)
            except Exception as exc:  # noqa: BLE001 - keep trying; the blink lapses by itself
                logger.info("Renewing identify on device %s failed: %s", device_id, exc)
