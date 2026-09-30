"""Asking for firmware offers (design 2026-09-30, section 6.1).

One code path for the daily run, the overview button and the dialog button.
Only one `check_all` runs at a time: a second start joins the first, which
covers a double click and a second browser."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from loxmatter import i18n
from loxmatter.model.store import Store, StoredDevice
from loxmatter.sources.firmware import FirmwareSource
from loxmatter.timestamps import now_iso

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckProgress:
    running: bool
    checked: int
    total: int


def describe_failure(exc: BaseException) -> str:
    """A failure as the overview shows it - the exception's own text, or its
    type when it has none."""
    if isinstance(exc, TimeoutError):
        return i18n.t("api.firmware.check_timeout")
    return str(exc) or type(exc).__name__


class FirmwareChecker:
    def __init__(
        self,
        store: Store,
        source_for: Callable[[], FirmwareSource | None],
        *,
        now: Callable[[], str] = now_iso,
        per_device_timeout: float = 60.0,
    ) -> None:
        self._store = store
        self._source_for = source_for
        self._now = now
        self._timeout = per_device_timeout
        self._task: asyncio.Task[None] | None = None
        self._checked = 0
        self._total = 0

    @property
    def progress(self) -> CheckProgress:
        running = self._task is not None and not self._task.done()
        return CheckProgress(running=running, checked=self._checked, total=self._total)

    def start_all(self) -> CheckProgress:
        """Starts a check of every device in the background, or joins the
        running one. Returns at once."""
        if self._task is None or self._task.done():
            self._checked = self._total = 0
            self._task = asyncio.ensure_future(self._run_all())
        return self.progress

    async def check_all(self) -> None:
        """Like `start_all`, but waits until the run has ended."""
        self.start_all()
        assert self._task is not None
        await asyncio.shield(self._task)

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def check_one(self, device_id: int) -> None:
        source = self._supported_source()
        if source is None:
            return
        await self._check_device(source, self._store.device(device_id))

    def _supported_source(self) -> FirmwareSource | None:
        source = self._source_for()
        if source is None or not source.firmware_supported():
            return None
        return source

    async def _run_all(self) -> None:
        source = self._supported_source()
        if source is None:
            self._checked = self._total = 0
            return
        devices = [d for d in self._store.devices() if d.technology == source.technology]
        self._checked, self._total = 0, len(devices)
        for device in devices:
            try:
                await self._check_device(source, device)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("firmware check for device %s failed", device.id)
            self._checked += 1

    async def _check_device(self, source: FirmwareSource, device: StoredDevice) -> None:
        facts = source.firmware_facts(device.address)
        if facts is None or not facts.has_requestor or not facts.available:
            return
        try:
            offer = await asyncio.wait_for(source.check_update(device.address), self._timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one device's failure is that device's answer
            logger.info("firmware check for device %s failed: %s", device.id, exc)
            try:
                self._store.firmware_status.record_check_error(
                    device.id, describe_failure(exc), self._now()
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("could not record the check error for device %s", device.id)
            return
        if (
            offer is not None
            and facts.software_version is not None
            and offer.software_version <= facts.software_version
        ):
            offer = None
        self._store.firmware_status.record_check(device.id, offer, self._now())
