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

from loxmatter.api.models import (
    FirmwareCheckOut,
    FirmwareDeviceOut,
    FirmwareOfferOut,
    FirmwareOverviewOut,
)
from loxmatter.firmware import states
from loxmatter.firmware.check import FirmwareChecker
from loxmatter.firmware.job import FirmwareJobs
from loxmatter.model.store import Store, StoredDevice
from loxmatter.sources import SourceNotConfiguredError, Sources
from loxmatter.sources.firmware import FirmwareSource

_STATES_WITH_OFFER = frozenset(
    {states.AVAILABLE, states.CHECK_FAILED, *states.ACTIVE_JOB_STATES, *states.ENDED_JOB_STATES}
)


class FirmwareService:
    def __init__(
        self,
        store: Store,
        sources: Sources,
        *,
        checker: FirmwareChecker | None = None,
        jobs: FirmwareJobs | None = None,
    ) -> None:
        self._store = store
        self._sources = sources
        self.checker = checker or FirmwareChecker(store, self.source_for)
        self.jobs = jobs or FirmwareJobs(store, self.source_for)

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

    def device_out(self, device: StoredDevice) -> FirmwareDeviceOut:
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
                release_notes_url=offer.release_notes_url,
                source=offer.source,
            ),
            checked_at=None if status is None else status.checked_at,
            check_error=None if status is None else status.check_error,
            job_error=None if status is None else status.job_error,
        )

    def overview(self) -> FirmwareOverviewOut:
        progress = self.checker.progress
        return FirmwareOverviewOut(
            supported=self.supported(),
            daily_check_enabled=self._store.firmware_settings.get_daily_check_enabled(),
            last_checked_at=self._store.firmware_status.last_checked_at(),
            check=FirmwareCheckOut(
                running=progress.running, checked=progress.checked, total=progress.total
            ),
            updating_device_id=self.jobs.running_device_id,
            devices=[self.device_out(device) for device in self._store.devices()],
        )
