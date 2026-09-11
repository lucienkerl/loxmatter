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

"""Device sources: what produces devices, and the call that reaches them
(design 2026-09-11, section 3).

Matter's data model stays loxmatter's internal language. Every source
translates into it at its own edge, so everything past this package -
discovery, profiles, runtime, export, groups - does not know which source
a device came from.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from loxmatter import i18n
from loxmatter.matter.models import NodeSnapshot, Technology

__all__ = [
    "DeviceCall",
    "DeviceSource",
    "RuntimeEventHandler",
    "SourceNotConfiguredError",
    "Sources",
    "Technology",
]


@dataclass(frozen=True)
class DeviceCall:
    """One command to one endpoint of one device. Formerly `MatterCall`.

    `cluster_id`, `command_id` and the `payload` field names follow the
    Matter data model (`commands/translate.py` builds them); a non-Matter
    source renames the fields at its own edge. Zigbee's IDs for every
    command translated today are identical (design 2026-09-11, 1.1)."""

    technology: Technology
    address: str
    endpoint: int
    cluster_id: int
    command_id: int
    payload: dict[str, object] = field(default_factory=dict)


class RuntimeEventHandler(Protocol):
    """What `subscribe()` needs from its caller - `Runtime`
    (loxone/runtime.py) already satisfies this unchanged, so `_run()` can
    pass it directly as `handler`, without writing an adapter.

    `on_node_snapshot` was added with the follow-up of subscriptions
    (`follow`): the client sees a device with paths for which there
    is no signal row yet, and cannot do anything with that itself - it
    does not know the `Store` and is not supposed to know it. The handler,
    on the other hand, has it."""

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None: ...
    async def on_event(self, device_id: int, path: str) -> None: ...
    async def set_online(self, device_id: int, online: bool) -> None: ...
    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None: ...


class DeviceSource(Protocol):
    """What shared code needs from a source - measured against what it
    called on the Matter client, not against what a source could offer
    (design 2026-09-11, section 3.1).

    Commissioning is deliberately absent: Matter takes a code and returns
    one device, Zigbee opens the network and devices arrive later. Each
    commissioning route calls its own source by name (section 3.2)."""

    @property
    def technology(self) -> Technology: ...

    @property
    def connected(self) -> bool: ...

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def wait_for_link_loss(self) -> None: ...

    async def snapshots(self) -> list[NodeSnapshot]: ...

    async def subscribe(
        self,
        resolve_device_id: Callable[[str], int | None],
        handler: RuntimeEventHandler,
    ) -> None: ...

    async def follow(self, address: str, *, seed_even_without_new_paths: bool = False) -> None: ...

    async def send(self, call: DeviceCall) -> None: ...

    async def remove(self, address: str) -> None: ...


class SourceNotConfiguredError(LookupError):
    """A stored device belongs to a technology no running source serves.

    A `LookupError`, not a `KeyError`: `str()` of a `KeyError` wraps its
    message in quotes, and this message reaches the web UI."""

    def __init__(self, technology: str) -> None:
        super().__init__(i18n.t("api.errors.source_not_configured", technology=technology))
        self.technology = technology


class Sources:
    """The only place that dispatches by technology."""

    def __init__(self, sources: Iterable[DeviceSource]) -> None:
        self._by_technology: dict[str, DeviceSource] = {}
        for source in sources:
            if source.technology in self._by_technology:
                raise ValueError(f"two sources for technology {source.technology!r}")
            self._by_technology[source.technology] = source

    def get(self, technology: str) -> DeviceSource:
        try:
            return self._by_technology[technology]
        except KeyError:
            raise SourceNotConfiguredError(technology) from None

    def all(self) -> list[DeviceSource]:
        return list(self._by_technology.values())

    def all_connected(self) -> bool:
        return all(source.connected for source in self._by_technology.values())

    async def send(self, call: DeviceCall) -> None:
        await self.get(call.technology).send(call)
