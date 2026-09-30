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

"""What a device source offers for firmware updates (design 2026-09-30).

Apart from `DeviceSource` on purpose: only some sources can update firmware -
Matter now, Zigbee in stage 2 - and a method every source must carry would
force a stub onto the others. `FirmwareService` asks `isinstance(source,
FirmwareSource)` instead."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from loxmatter.matter.models import Technology

# The server schema from which `check_node_update` and `update_node` exist
# (`matter_server.client.MatterClient`, `require_schema=10`).
FIRMWARE_MIN_SCHEMA: Final = 10


@dataclass(frozen=True)
class FirmwareFacts:
    """What the source's cache knows about one device's firmware right now.

    `software_version` is the integer `SoftwareVersion` (0/40/9), the only
    value versions are compared by; `software_version_string` (0/40/10) is
    what a person reads. `spec_version` is the raw `SpecificationVersion`
    (0/40/21), `None` on a device from before Matter 1.3.
    `update_state`/`update_progress` are the OTA Software Update Requestor's
    `UpdateState` (0/42/2) and `UpdateStateProgress` (0/42/3)."""

    available: bool
    has_requestor: bool
    software_version: int | None
    software_version_string: str | None
    spec_version: int | None
    update_state: int | None
    update_progress: int | None


@dataclass(frozen=True)
class UpdateOffer:
    """An image the source found for a device - on Matter, one row of the
    CSA DCL as `check_node_update` returns it."""

    software_version: int
    software_version_string: str
    min_applicable: int
    max_applicable: int
    release_notes_url: str | None
    source: str


@runtime_checkable
class FirmwareSource(Protocol):
    """A device source that can check and install firmware updates."""

    @property
    def technology(self) -> Technology: ...

    @property
    def connected(self) -> bool: ...

    def firmware_supported(self) -> bool: ...

    def firmware_facts(self, address: str) -> FirmwareFacts | None: ...

    async def refresh_firmware_facts(self, address: str) -> None: ...

    async def check_update(self, address: str) -> UpdateOffer | None: ...

    async def start_update(self, address: str, software_version: int) -> None: ...

    async def follow(self, address: str, *, seed_even_without_new_paths: bool = False) -> None: ...
