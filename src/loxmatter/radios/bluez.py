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
"""Matter commissioning advertisements and adapter state, read from BlueZ.

Design 2026-09-22, sections 3 and 6. matter-server scans over BlueZ while it
commissions; BlueZ keeps every advertisement it received as an
`org.bluez.Device1` object. This module reads those objects - it never starts
a scan itself: on 21 September 2026 the Raspberry Pi 3's on-board adapter
wedged after a single commissioning attempt, and a second scanning client on
the same chip is the last thing it needs.

A Matter device in commissioning mode advertises service data under
`MATTER_SERVICE_UUID`: byte 0 is the opcode (0x00 = commissionable), bytes 1-2
little-endian hold the 12-bit discriminator in their low bits, bytes 3-4 the
vendor id, bytes 5-6 the product id. Measured: `00 23 04 7c 11 01 90 00` is
discriminator 1059, vendor 4476 (0x117C, IKEA), product 36865 (0x9001).
Nothing in it is secret.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

MATTER_SERVICE_UUID: Final = "0000fff6-0000-1000-8000-00805f9b34fb"
_DEVICE: Final = "org.bluez.Device1"
_ADAPTER: Final = "org.bluez.Adapter1"
_COMMISSIONABLE_OPCODE: Final = 0x00

ObjectsFetcher = Callable[[], Awaitable[Mapping[str, Mapping[str, Mapping[str, Any]]]]]


@dataclass(frozen=True)
class MatterAdvert:
    address: str
    name: str | None
    rssi: int | None
    discriminator: int
    vendor_id: int
    product_id: int
    connected: bool
    adapter: str | None


@dataclass(frozen=True)
class AdapterState:
    name: str
    powered: bool
    discovering: bool


@dataclass(frozen=True)
class BluezSnapshot:
    adverts: list[MatterAdvert]
    adapter: AdapterState | None


def parse_matter_service_data(data: bytes) -> tuple[int, int, int] | None:
    """`(discriminator, vendor_id, product_id)`, or `None` for anything that is
    not a commissionable Matter advertisement."""
    if len(data) < 7 or data[0] != _COMMISSIONABLE_OPCODE:
        return None
    discriminator = int.from_bytes(data[1:3], "little") & 0x0FFF
    vendor_id = int.from_bytes(data[3:5], "little")
    product_id = int.from_bytes(data[5:7], "little")
    return discriminator, vendor_id, product_id


def _advert(properties: Mapping[str, Any]) -> MatterAdvert | None:
    service_data = properties.get("ServiceData") or {}
    raw = service_data.get(MATTER_SERVICE_UUID)
    rssi = properties.get("RSSI")
    if raw is None or not isinstance(rssi, int):
        # No RSSI: BlueZ remembers the device but has not heard it lately.
        return None
    parsed = parse_matter_service_data(bytes(raw))
    if parsed is None:
        return None
    discriminator, vendor_id, product_id = parsed
    name = properties.get("Name")
    adapter = properties.get("Adapter")
    return MatterAdvert(
        address=str(properties.get("Address", "")),
        name=name if isinstance(name, str) else None,
        rssi=rssi,
        discriminator=discriminator,
        vendor_id=vendor_id,
        product_id=product_id,
        connected=bool(properties.get("Connected", False)),
        adapter=adapter if isinstance(adapter, str) else None,
    )


def snapshot_from_objects(objects: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> BluezSnapshot:
    adverts: list[MatterAdvert] = []
    adapters: list[AdapterState] = []
    for path, interfaces in objects.items():
        if _DEVICE in interfaces:
            advert = _advert(interfaces[_DEVICE])
            if advert is not None:
                adverts.append(advert)
        if _ADAPTER in interfaces:
            properties = interfaces[_ADAPTER]
            adapters.append(
                AdapterState(
                    name=path.rsplit("/", 1)[-1],
                    powered=bool(properties.get("Powered", False)),
                    discovering=bool(properties.get("Discovering", False)),
                )
            )
    adverts.sort(key=lambda advert: -(advert.rssi or -1000))
    # The adapter that scans is the one matter-server uses; with none
    # scanning, the first one.
    adapters.sort(key=lambda adapter: adapter.name)
    scanning = [adapter for adapter in adapters if adapter.discovering]
    candidates = scanning or adapters
    adapter: AdapterState | None = candidates[0] if candidates else None
    return BluezSnapshot(adverts=adverts, adapter=adapter)


def _unwrap(value: Any) -> Any:
    """dbus-fast `Variant`s into plain Python values, recursively."""
    from dbus_fast import Variant

    if isinstance(value, Variant):
        return _unwrap(value.value)
    if isinstance(value, dict):
        return {key: _unwrap(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unwrap(item) for item in value]
    return value


async def _fetch_managed_objects() -> Mapping[str, Mapping[str, Mapping[str, Any]]]:
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(
            Message(
                destination="org.bluez",
                path="/",
                interface="org.freedesktop.DBus.ObjectManager",
                member="GetManagedObjects",
            )
        )
    finally:
        bus.disconnect()
    if reply is None or reply.message_type == MessageType.ERROR:
        raise RuntimeError("BlueZ refused GetManagedObjects")
    result: Mapping[str, Mapping[str, Mapping[str, Any]]] = _unwrap(reply.body[0])
    return result


class BluezReader:
    """One `GetManagedObjects` per `snapshot()`. `None` whenever BlueZ cannot be
    read - no socket, no BlueZ, a refused call - because a missing source must
    read like "not available", never like an error (design section 8)."""

    def __init__(self, fetch: ObjectsFetcher | None = None) -> None:
        self._fetch = fetch or _fetch_managed_objects

    async def snapshot(self) -> BluezSnapshot | None:
        try:
            objects = await self._fetch()
        except Exception as exc:  # noqa: BLE001
            # No socket, no BlueZ, a refused call: all the same "not
            # available" (design section 8), never an error to the caller.
            logger.debug("BlueZ not readable: %s", exc)
            return None
        return snapshot_from_objects(objects)
