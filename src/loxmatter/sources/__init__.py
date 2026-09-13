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

import asyncio
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from loxmatter import i18n
from loxmatter.matter.models import NodeSnapshot, Technology

__all__ = [
    "SOURCE_CALL_TIMEOUT_SECONDS",
    "DeviceCall",
    "DeviceSource",
    "DeviceUnreachableError",
    "RuntimeEventHandler",
    "SourceNotConfiguredError",
    "Sources",
    "Technology",
    "bounded_source_call",
    "technology_display_name",
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


SOURCE_CALL_TIMEOUT_SECONDS = 10.0


class DeviceUnreachableError(RuntimeError):
    """A source asked a device and got nothing back.

    The one exception type shared code may catch for that outcome, across
    every technology (boundary design open point 11). Before it existed,
    `api/devices.py`'s removal route caught `MatterUnavailableError` alone,
    so a second source raising its own type on the very same failure would
    have surfaced as an unhandled 500.

    Each source raises this at ITS OWN EDGE. That is what keeps zigpy's
    exception names - `DeliveryError`, `ControllerError`, `ZigbeeException`,
    a non-SUCCESS ZCL status - inside `loxmatter/zigbee/`, where they
    belong: no `except` clause in shared code may name one.

    Distinct from `SourceNotConfiguredError` on purpose, and the difference
    is the difference between 502 and 503: this means the device was ASKED
    and stayed silent, that one means nothing was asked at all.
    """


def technology_display_name(technology: str) -> str:
    """A technology's name as a person should read it.

    Boundary design open point 13: `api.errors.source_not_configured`
    interpolated the raw, lowercase stored value ("zigbee is not set up in
    this installation"), while every other user-facing identifier in this
    codebase goes through a lookup first (see `api.categories.*`). An
    unknown value falls back to itself rather than raising - this runs
    inside an error path, and an error about an error helps nobody.

    The fallback is a `KeyError` catch and NOT a comparison of the result
    against the key. `i18n.t` does `entry = _STRINGS[key]` and RAISES
    `KeyError` on a missing key - it never hands the key back - so a
    `name == key` test would be dead code guarding nothing, and the very
    miss it was written for would propagate a `KeyError` out of an error
    path. That is exactly the "error about an error" the paragraph above
    rules out. The miss is reachable: `technology` is read from the
    `device` table, so a row written by a NEWER loxmatter and left behind
    by an updater rollback arrives here with a name that has no string -
    the same rollback case `technology_or_none` exists for in Step 4.
    """
    try:
        return i18n.t(f"api.technologies.{technology}")
    except KeyError:
        return technology


async def bounded_source_call(call: Coroutine[Any, Any, None]) -> None:
    """Awaits one call into a source, and gives up on it after
    `SOURCE_CALL_TIMEOUT_SECONDS`.

    The one place the bound lives, because there is more than one way into
    a source and a human or a Miniserver waits on all of them: `Sources.send`
    (a command) and `api/devices.py`'s removal route (`source.remove`). The
    removal route was unbounded until this function existed, which mattered
    most exactly where the bound matters most - zigpy retries a request twice
    and waits 5 s per attempt for a mains device and 28 s for an end device
    or one without a node descriptor (research E.6), so removing a sleeping
    button held the DELETE open for over a minute.

    The bound is read from the module here, at call time, rather than bound
    as a default argument, so a test can shorten it with
    `monkeypatch.setattr("loxmatter.sources.SOURCE_CALL_TIMEOUT_SECONDS", ...)`
    and reach every caller at once.

    A timeout is reported as `DeviceUnreachableError`, not as the bare
    `TimeoutError`: from the caller's point of view "asked, no answer" is
    exactly what happened, it maps to the same 502 as every other way of not
    answering, and `str(TimeoutError())` is the empty string - a 502 whose
    detail is blank tells the person reading it nothing at all.
    """
    try:
        await asyncio.wait_for(call, SOURCE_CALL_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise DeviceUnreachableError(
            i18n.t("api.errors.device_timed_out", seconds=SOURCE_CALL_TIMEOUT_SECONDS)
        ) from exc


class SourceNotConfiguredError(LookupError):
    """A stored device belongs to a technology no running source serves.

    A `LookupError`, not a `KeyError`: `str()` of a `KeyError` wraps its
    message in quotes, and this message reaches the web UI."""

    def __init__(self, technology: str) -> None:
        super().__init__(
            i18n.t(
                "api.errors.source_not_configured",
                technology=technology_display_name(technology),
            )
        )
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

    def replace(self, technology: str, source: DeviceSource | None) -> None:
        """Swaps or removes the source serving one technology.

        For the in-process radio change, which is the one thing in
        this project that gains or loses a source WITHOUT a restart: zigpy
        runs in-process, so configuring a stick has to add a source to a
        registry that `build_app` captured at startup, and clearing one has
        to remove it.

        `None` removes, and removing is the point rather than a tidy-up:
        after it, `get()` raises `SourceNotConfiguredError` again, which is
        how a command aimed at a Zigbee device that no longer has a radio
        becomes a 503 "not set up in this installation" instead of a 502
        "asked, no answer". The device was not asked; there is nothing to
        ask.

        Deliberately NOT a general-purpose registry mutator: `__init__`
        keeps rejecting two sources for one technology, and this method is
        the single, named exception to "the registry is built once".
        """
        if source is None:
            self._by_technology.pop(technology, None)
            return
        if source.technology != technology:
            raise ValueError(
                f"source for {source.technology!r} cannot serve technology {technology!r}"
            )
        self._by_technology[technology] = source

    async def send(self, call: DeviceCall) -> None:
        """The invoker. Bounded through `bounded_source_call`, because this
        is one of the two places that knows a human or a Miniserver is
        waiting - see that function for the bound and why a timeout comes
        back as `DeviceUnreachableError`.
        """
        source = self.get(call.technology)
        await bounded_source_call(source.send(call))
