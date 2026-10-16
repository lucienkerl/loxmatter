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
import time
from collections.abc import Awaitable, Callable
from typing import Final

from loxmatter.sources import IdentifySource, IdentifyUnsupportedError, bounded_source_call

logger = logging.getLogger(__name__)

IDENTIFY_SECONDS: Final = 30
# A third of `IDENTIFY_SECONDS`, so one renewal can go unanswered without the
# blink lapsing. At 25 s a renewal more than 5 s late was enough to end it -
# and while matter-server commissions the next device, a renewal can wait out
# its whole 10 s bound (`SOURCE_CALL_TIMEOUT_SECONDS`).
RENEW_EVERY: Final = 10


class IdentifyCoordinator:
    def __init__(
        self,
        source_for: Callable[[str], IdentifySource | None],
        device_address: Callable[[int], tuple[str, str]],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._source_for = source_for
        self._device_address = device_address
        self._sleep = sleep
        self._clock = clock
        self._blinking: int | None = None
        # When the last identify the device acknowledged runs out.
        self._lit_until = 0.0
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def blinking(self) -> int | None:
        """The device this coordinator keeps blinking - the one a stop
        goes to."""
        return self._blinking

    @property
    def lit(self) -> int | None:
        """`blinking`, but only while the last identify the device answered
        has not run out: what the dialog may show as blinking. A renewal
        that goes unanswered lets the blink lapse on the device while
        `blinking` still names it, and the renewal keeps trying."""
        if self._blinking is None or self._clock() >= self._lit_until:
            return None
        return self._blinking

    async def start(self, device_id: int, *, renew: bool, seconds: int = IDENTIFY_SECONDS) -> None:
        async with self._lock:
            await self._start_locked(device_id, renew, seconds)

    async def start_if_idle(self, device_id: int, seconds: int) -> bool:
        """A short, non-renewed blink that never interrupts another one.
        True if it started."""
        async with self._lock:
            if self._blinking is not None:
                return False
            await self._start_locked(device_id, False, seconds)
            return True

    async def _start_locked(self, device_id: int, renew: bool, seconds: int) -> None:
        # The same device restarts without a stop in between.
        await self._stop_locked(send_stop=self._blinking != device_id)
        source, address = self._resolve(device_id)
        await bounded_source_call(source.identify(address, seconds))
        self._blinking = device_id
        self._lit_until = self._clock() + seconds
        self._task = asyncio.ensure_future(self._keep(device_id, source, address, renew, seconds))

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

    async def _stop_locked(self, *, send_stop: bool = True) -> None:
        task, device_id = self._task, self._blinking
        self._task, self._blinking = None, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if device_id is not None and send_stop:
            try:
                source, address = self._resolve(device_id)
                await bounded_source_call(source.identify(address, 0))
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
        # A fixed cadence, counted from the start: a renewal that waited out
        # its bound must not push the next one back towards the lapse.
        due = self._clock()
        while True:
            due += RENEW_EVERY
            await self._sleep(max(0.0, due - self._clock()))
            try:
                await bounded_source_call(source.identify(address, seconds))
            except Exception as exc:  # noqa: BLE001 - keep trying; the blink lapses by itself
                logger.info("Renewing identify on device %s failed: %s", device_id, exc)
            else:
                self._lit_until = self._clock() + seconds
