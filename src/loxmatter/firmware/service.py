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

"""Bundles checking and installing for the API and `cli` (design 2026-09-30).

`build_app` builds one when `cli` passes none, so every test app has the
routes; only `cli` starts the schedule and resumes jobs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from loxmatter import i18n
from loxmatter.api.models import (
    FirmwareCheckOut,
    FirmwareDeviceOut,
    FirmwareOfferOut,
    FirmwareOverviewOut,
    FirmwareQueueOut,
)
from loxmatter.firmware import states
from loxmatter.firmware.check import FirmwareChecker
from loxmatter.firmware.job import FirmwareJobs
from loxmatter.firmware.queue import FirmwareQueue
from loxmatter.firmware.schedule import local_now, next_check_at
from loxmatter.model.store import Store, StoredDevice
from loxmatter.sources import SourceNotConfiguredError, Sources
from loxmatter.sources.firmware import FirmwareSource

_STATES_WITH_OFFER = frozenset(
    {states.AVAILABLE, states.CHECK_FAILED, *states.ACTIVE_JOB_STATES, *states.ENDED_JOB_STATES}
)


def _web_link(url: str | None) -> str | None:
    """The release notes link only when it is a web address: it comes from
    the DCL, third-party data, and the dialog renders it as an `href`."""
    if url is not None and url.lower().startswith(("http://", "https://")):
        return url
    return None


class FirmwareService:
    def __init__(
        self,
        store: Store,
        sources: Sources,
        *,
        checker: FirmwareChecker | None = None,
        jobs: FirmwareJobs | None = None,
        queue: FirmwareQueue | None = None,
        local_clock: Callable[[], datetime] = local_now,
    ) -> None:
        self._store = store
        self._local_clock = local_clock
        self._sources = sources
        self.checker = checker or FirmwareChecker(store, self.source_for)
        self.jobs = jobs or FirmwareJobs(store, self.source_for)
        self.queue = queue or FirmwareQueue(store, self.jobs, self.source_for)

    def source_for(self) -> FirmwareSource | None:
        """The Matter source, if it can update firmware. Looked up on every
        call: `Sources` can change while the bridge runs."""
        try:
            source = self._sources.get("matter")
        except SourceNotConfiguredError:
            return None
        return source if isinstance(source, FirmwareSource) else None

    def supported(self) -> bool:
        source = self.source_for()
        return source is not None and source.firmware_supported()

    def device_out(
        self, device: StoredDevice, queued: list[int] | None = None
    ) -> FirmwareDeviceOut:
        if queued is None:
            queued = self._store.firmware_status.queued()
        source = self.source_for()
        own_device = source is not None and device.technology == source.technology
        facts = None
        if source is not None and own_device and source.connected:
            facts = source.firmware_facts(device.address)
        status = self._store.firmware_status.get(device.id)
        offer = None if status is None else status.offer
        installed_number = None if facts is None else facts.software_version
        if facts is not None:
            has_requestor = facts.has_requestor
        else:
            # Unreachable (design 5): keep the last state. Only a device with
            # the requestor cluster ever gets a status row; one without a row
            # cannot be told apart from one without the cluster, so it stays
            # `no_source` rather than claiming `unchecked`.
            has_requestor = own_device and status is not None
        state = states.derive_state(
            has_requestor=has_requestor,
            installed=installed_number,
            checked_at=None if status is None else status.checked_at,
            check_error=None if status is None else status.check_error,
            offer_version=None if offer is None else offer.software_version,
            job_state=None if status is None else status.job_state,
        )
        return FirmwareDeviceOut(
            device_id=device.id,
            label=device.label,
            room=device.room,
            technology=device.technology,
            # The live value first: the stored one is NULL until the backfill ran.
            matter_version=states.format_spec_version(
                facts.spec_version if facts is not None else device.matter_spec_version,
                device.technology,
            ),
            installed=(facts.software_version_string if facts is not None else None)
            or device.firmware,
            online=facts.available if facts is not None else (False if own_device else None),
            state=state,
            progress=None if status is None else status.job_progress,
            # Only states that use the offer show it; that also hides one
            # the device has already reached (`none_found`).
            offer=None
            if offer is None or state not in _STATES_WITH_OFFER
            else FirmwareOfferOut(
                version=offer.software_version,
                version_string=offer.software_version_string,
                release_notes_url=_web_link(offer.release_notes_url),
                source=offer.source,
            ),
            checked_at=None if status is None else status.checked_at,
            check_error=None if status is None else status.check_error,
            job_error=None if status is None else status.job_error,
            queue_position=queued.index(device.id) + 1 if device.id in queued else None,
        )

    def overview(self) -> FirmwareOverviewOut:
        progress = self.checker.progress
        queued = self._store.firmware_status.queued()
        halted = self._store.firmware_settings.get_queue_halted_reason()
        daily = self._store.firmware_settings.get_daily_check_enabled()
        return FirmwareOverviewOut(
            supported=self.supported(),
            daily_check_enabled=daily,
            # The scheduler's own clock and target: the container may run in
            # UTC, so naming a fixed hour in the UI would be wrong.
            next_check_at=next_check_at(self._local_clock()) if daily else None,
            last_checked_at=self._store.firmware_status.last_checked_at(),
            check=FirmwareCheckOut(
                running=progress.running, checked=progress.checked, total=progress.total
            ),
            updating_device_id=self.jobs.running_device_id,
            queue=FirmwareQueueOut(
                device_ids=queued,
                active=self.queue.active,
                halted_reason=None if halted is None else i18n.t(halted),
            ),
            devices=[self.device_out(device, queued) for device in self._store.devices()],
        )
