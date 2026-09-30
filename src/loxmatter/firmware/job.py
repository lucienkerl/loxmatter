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

"""Installing one firmware update and following it (design 2026-09-30, 7).

One install at a time on the Matter source: `_task` is the lock. The
transfer runs between matter-server and the device, so this job only
watches the node cache; a restart of loxmatter loses nothing that
`resume_all` cannot pick up again."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loxmatter import i18n
from loxmatter.firmware import states
from loxmatter.firmware.check import describe_failure
from loxmatter.model.store import Store
from loxmatter.sources.firmware import FirmwareFacts, FirmwareSource
from loxmatter.timestamps import now_iso

logger = logging.getLogger(__name__)


class FirmwareUnsupportedError(RuntimeError):
    """No source, or a server below schema 10."""


class FirmwareBusyError(RuntimeError):
    def __init__(self, device_id: int) -> None:
        super().__init__(f"firmware update running on device {device_id}")
        self.device_id = device_id


class OfferChangedError(RuntimeError):
    """The requested version is not the stored offer (a stale browser tab)."""


class DeviceOfflineError(RuntimeError):
    """The device is not reachable right now."""


@dataclass(frozen=True)
class JobTiming:
    poll: float = 2.0
    reread_after: float = 30.0
    idle_fail_after: float = 120.0
    stall_after: float = 900.0
    give_up_after: float = 10800.0


_DEFAULT_TIMING = JobTiming()


class FirmwareJobs:
    def __init__(
        self,
        store: Store,
        source_for: Callable[[], FirmwareSource | None],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], str] = now_iso,
        timing: JobTiming = _DEFAULT_TIMING,
    ) -> None:
        self._store = store
        self._source_for = source_for
        self._clock = clock
        self._sleep = sleep
        self._now = now
        self._timing = timing
        self._task: asyncio.Task[None] | None = None
        self._device_id: int | None = None
        # Re-reads after an update that finished while loxmatter was down.
        self._rereads: set[asyncio.Task[None]] = set()

    @property
    def running_device_id(self) -> int | None:
        if self._task is None or self._task.done():
            return None
        return self._device_id

    def _supported_source(self) -> FirmwareSource:
        source = self._source_for()
        if source is None or not source.firmware_supported():
            raise FirmwareUnsupportedError()
        return source

    def start(self, device_id: int, software_version: int) -> None:
        """Starts an install in the background. Raises before anything is
        sent: busy, unsupported, `UnknownDeviceError`, offer changed, offline."""
        running = self.running_device_id
        if running is not None:
            raise FirmwareBusyError(running)
        source = self._supported_source()
        device = self._store.device(device_id)
        status = self._store.firmware_status.get(device_id)
        if (
            status is None
            or status.offer is None
            or status.offer.software_version != software_version
        ):
            raise OfferChangedError()
        facts = source.firmware_facts(device.address)
        if facts is None or not facts.available:
            raise DeviceOfflineError()
        self._store.firmware_status.start_job(device_id, self._now())
        self._device_id = device_id
        self._task = asyncio.ensure_future(
            self._guarded(
                device_id,
                lambda: self._install(source, device_id, device.address, software_version),
            )
        )

    def resume_all(self) -> int:
        """After a start of loxmatter: follows a device whose transfer is
        still running, and marks every other remembered job interrupted.
        Returns how many jobs it resumed (0 or 1)."""
        if self.running_device_id is not None:
            return 0
        source = self._source_for()
        if source is None or not source.firmware_supported():
            return 0
        resumed = 0
        by_id = {device.id: device for device in self._store.devices()}
        for device_id, status in self._store.firmware_status.all().items():
            if status.job_state not in states.ACTIVE_JOB_STATES:
                continue
            device = by_id.get(device_id)
            facts = None if device is None else source.firmware_facts(device.address)
            if (
                device is not None
                and facts is not None
                and status.offer is not None
                and facts.software_version is not None
                and facts.software_version >= status.offer.software_version
            ):
                # The update finished while loxmatter was down. The store part
                # is synchronous; only the re-read of the structure is a task.
                self._record_success(device_id, facts)
                reread = asyncio.ensure_future(self._reread(source, device_id, device.address))
                self._rereads.add(reread)
                reread.add_done_callback(self._rereads.discard)
                continue
            busy = facts is not None and states.job_state_for(facts.update_state) is not None
            if device is None or not busy or status.offer is None or resumed:
                self._store.firmware_status.end_job(
                    device_id, states.INTERRUPTED, None, self._now()
                )
                continue
            self._device_id = device_id
            target = status.offer.software_version
            self._task = asyncio.ensure_future(
                self._guarded(
                    device_id,
                    functools.partial(self._follow, source, device_id, device.address, target),
                )
            )
            resumed = 1
        return resumed

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.shield(self._task)
        for reread in list(self._rereads):
            await asyncio.shield(reread)

    async def stop(self) -> None:
        for reread in list(self._rereads):
            reread.cancel()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _guarded(self, device_id: int, job: Callable[[], Awaitable[None]]) -> None:
        """A crash ends the job as failed; a cancellation (shutdown) leaves
        the row active so `resume_all` can pick the transfer up again."""
        try:
            await job()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("firmware job of device %s crashed", device_id)
            try:
                self._end(device_id, states.FAILED, describe_failure(exc))
            except Exception:
                logger.exception("ending the firmware job of device %s failed", device_id)

    async def _install(
        self, source: FirmwareSource, device_id: int, address: str, target: int
    ) -> None:
        # `update_node` may return at once or only after the transfer
        # (design 3, open until the first real install), so it runs next to
        # the follow loop rather than before it.
        starter = asyncio.ensure_future(source.start_update(address, target))
        # The job can end on another path (success, interrupted, gave up)
        # before the start does; its failure is then only logged, never
        # left unretrieved.
        starter.add_done_callback(lambda done: self._log_late_start(device_id, done))
        try:
            await self._follow(source, device_id, address, target, starter=starter)
        finally:
            if not starter.done():
                starter.cancel()
                # Waiting does not raise; bounded so a shielded client call
                # cannot hang shutdown.
                await asyncio.wait({starter}, timeout=5)

    @staticmethod
    def _log_late_start(device_id: int, starter: asyncio.Future[None]) -> None:
        if starter.cancelled():
            return
        failure = starter.exception()
        if failure is not None:
            logger.info("starting the update of device %s failed: %s", device_id, failure)

    async def _follow(
        self,
        source: FirmwareSource,
        device_id: int,
        address: str,
        target: int,
        *,
        starter: asyncio.Future[None] | None = None,
    ) -> None:
        timing = self._timing
        started = self._clock()
        last_signature: tuple[int | None, int | None] | None = None
        last_change = started
        last_reread = started
        idle_since: float | None = None
        seen_busy = False
        last_written: tuple[str, int | None] | None = None
        while True:
            now = self._clock()
            if starter is not None and starter.done() and not starter.cancelled():
                failure = starter.exception()
                if failure is not None:
                    self._end(device_id, states.FAILED, describe_failure(failure))
                    return
            if not source.connected:
                self._end(device_id, states.INTERRUPTED, None)
                return
            facts = source.firmware_facts(address)
            if (
                facts is not None
                and facts.software_version is not None
                and facts.software_version >= target
            ):
                await self._succeed(source, device_id, address, facts)
                return
            update_state = None if facts is None else facts.update_state
            progress = None if facts is None else facts.update_progress
            signature = (update_state, progress)
            if signature != last_signature:
                last_signature, last_change = signature, now
            job_state = states.job_state_for(update_state)
            if job_state is not None:
                seen_busy, idle_since = True, None
            elif seen_busy:
                idle_since = now if idle_since is None else idle_since
                if now - idle_since >= timing.idle_fail_after:
                    self._end(device_id, states.FAILED, i18n.t("api.firmware.job_no_new_version"))
                    return
            if now - started >= timing.give_up_after:
                self._end(device_id, states.FAILED, i18n.t("api.firmware.job_gave_up"))
                return
            shown = (
                states.STALLED
                if now - last_change >= timing.stall_after
                else (job_state or states.TRANSFERRING)
            )
            shown_progress = progress if shown == states.TRANSFERRING else None
            if (shown, shown_progress) != last_written:
                self._store.firmware_status.update_job(
                    device_id, shown, shown_progress, self._now()
                )
                last_written = (shown, shown_progress)
            if (
                now - last_change >= timing.reread_after
                and now - last_reread >= timing.reread_after
            ):
                last_reread = now
                try:
                    await source.refresh_firmware_facts(address)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - a missed read is retried next poll
                    logger.info("reading firmware state of device %s failed: %s", device_id, exc)
            await self._sleep(timing.poll)

    def _end(self, device_id: int, state: str, error: str | None) -> None:
        self._store.firmware_status.end_job(device_id, state, error, self._now())

    def _record_success(self, device_id: int, facts: FirmwareFacts) -> None:
        self._store.set_firmware_details(
            device_id, facts.software_version_string, facts.spec_version
        )
        self._store.firmware_status.drop_offer(device_id)
        self._store.firmware_status.end_job(device_id, None, None, self._now())

    async def _succeed(
        self, source: FirmwareSource, device_id: int, address: str, facts: FirmwareFacts
    ) -> None:
        self._record_success(device_id, facts)
        await self._reread(source, device_id, address)

    async def _reread(self, source: FirmwareSource, device_id: int, address: str) -> None:
        # An update can renumber endpoints (the Tasmota plug did); re-reading
        # the structure is what lets "Changed since export" appear.
        try:
            await source.follow(address, seed_even_without_new_paths=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the update itself has succeeded
            logger.warning("re-reading device %s after its update failed: %s", device_id, exc)
