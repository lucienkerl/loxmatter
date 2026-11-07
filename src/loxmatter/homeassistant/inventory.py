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

"""What Home Assistant has, and what of it this bridge can take (design
2026-11-07, section 4).

Read-only: every call here is a query. The list the assistant shows is
built from four of Home Assistant's own answers - the device registry for
names and rooms, the area registry for the rooms' names, Matter's
`node_diagnostics` for how a Matter device is connected, and ZHA's
`zha/devices` for each Zigbee device's signature.

**One entry per Matter node, not per registry device.** A Matter bridge (a
Hue bridge, an IKEA DIRIGERA) is one node that Home Assistant shows as one
registry device per bridged lamp. It joins this bridge's fabric once, as a
whole, so the list shows it once, with the bridged devices' names under it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from loxmatter.homeassistant.client import HomeAssistantApi, HomeAssistantError
from loxmatter.zigbee.migration import ImportedDevice, imported_device_from_zha

logger = logging.getLogger(__name__)

DeviceKind = Literal["matter_wifi", "matter_thread", "matter_bridge", "zigbee", "other"]
# `ready`: can come over now. `needs_thread`: a Thread device, and this
# bridge has no Thread stick. `needs_zigbee`: a Zigbee device, and no
# Zigbee stick or Zigbee was left out. `later`: a kind this bridge cannot
# pass on to Loxone yet (remote controls). `unsupported`: anything else.
# `done`: already moved by an earlier run.
Availability = Literal["ready", "needs_thread", "needs_zigbee", "later", "unsupported", "done"]


@dataclass(frozen=True)
class InventoryDevice:
    """One line of the assistant's list. `ha_device_id` is the registry
    id the migration and the clean-up refer to; for a Matter bridge it is
    the bridge's own registry device, and `members` names the rest."""

    ha_device_id: str
    name: str
    room: str | None
    kind: DeviceKind
    availability: Availability
    manufacturer: str | None = None
    model: str | None = None
    integration: str | None = None
    members: tuple[str, ...] = ()
    matter_node_id: int | None = None
    # True when the node is already in this bridge's own fabric: Home
    # Assistant talks to loxmatter's Matter server after the take-over on
    # the same host, so the device needs no pairing, only a store entry.
    in_fabric: bool = False
    zigbee: ImportedDevice | None = field(default=None, compare=False)

    def as_json(self) -> dict[str, object]:
        return {
            "ha_device_id": self.ha_device_id,
            "name": self.name,
            "room": self.room,
            "kind": self.kind,
            "availability": self.availability,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "integration": self.integration,
            "members": list(self.members),
            "in_fabric": self.in_fabric,
            "ieee": self.zigbee.ieee if self.zigbee is not None else None,
        }


@dataclass(frozen=True)
class ThreadDataset:
    dataset_id: str
    network_name: str | None
    channel: int | None
    extended_pan_id: str | None
    preferred: bool

    def as_json(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "network_name": self.network_name,
            "channel": self.channel,
            "extended_pan_id": self.extended_pan_id,
            "preferred": self.preferred,
        }


@dataclass(frozen=True)
class Inventory:
    devices: tuple[InventoryDevice, ...]
    thread_datasets: tuple[ThreadDataset, ...]
    zha_entry_id: str | None
    zha_disabled: bool
    matter_entry_id: str | None

    @property
    def preferred_dataset(self) -> ThreadDataset | None:
        for dataset in self.thread_datasets:
            if dataset.preferred:
                return dataset
        return self.thread_datasets[0] if self.thread_datasets else None

    def device(self, ha_device_id: str) -> InventoryDevice | None:
        for device in self.devices:
            if device.ha_device_id == ha_device_id:
                return device
        return None

    def as_json(self) -> dict[str, object]:
        preferred = self.preferred_dataset
        return {
            "devices": [device.as_json() for device in self.devices],
            "thread_network": preferred.as_json() if preferred is not None else None,
            "zha": self.zha_entry_id is not None,
            "zha_paused": self.zha_disabled,
            "matter": self.matter_entry_id is not None,
        }


@dataclass(frozen=True)
class Capabilities:
    """What this bridge can take right now - decides `availability`."""

    thread: bool
    zigbee: bool
    # This bridge's compressed fabric id, when its Matter server reports one.
    fabric: int | None = None
    done: frozenset[str] = frozenset()


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _domains(entry: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for identifier in entry.get("identifiers") or []:
        if isinstance(identifier, list | tuple) and identifier and isinstance(identifier[0], str):
            found.add(identifier[0])
    return found


# Home Assistant's Matter device identifier: `deviceid_` + the compressed
# fabric id and the node id, 16 hex digits each, then a suffix
# (homeassistant/components/matter/helpers.py, `get_device_id`).
_MATTER_DEVICE_ID = re.compile(r"^deviceid_([0-9A-Fa-f]+)-([0-9A-Fa-f]{16})-")


def _matter_fabric(entry: dict[str, Any]) -> int | None:
    """The compressed fabric id Home Assistant's Matter server put into the
    device's identifier, or `None`."""
    for identifier in entry.get("identifiers") or []:
        if (
            isinstance(identifier, list | tuple)
            and len(identifier) == 2
            and identifier[0] == "matter"
            and isinstance(identifier[1], str)
            and (match := _MATTER_DEVICE_ID.match(identifier[1]))
        ):
            return int(match.group(1), 16)
    return None


def _matter_node(entry: dict[str, Any]) -> int | None:
    """The node id from the same identifier, or `None`."""
    for identifier in entry.get("identifiers") or []:
        if (
            isinstance(identifier, list | tuple)
            and len(identifier) == 2
            and identifier[0] == "matter"
            and isinstance(identifier[1], str)
            and (match := _MATTER_DEVICE_ID.match(identifier[1]))
        ):
            return int(match.group(2), 16)
    return None


def _device_name(entry: dict[str, Any]) -> str:
    return (
        _str(entry.get("name_by_user"))
        or _str(entry.get("name"))
        or _str(entry.get("model"))
        or str(entry.get("id"))
    )


async def _entry(api: HomeAssistantApi, domain: str) -> dict[str, Any] | None:
    try:
        entries = await api.call("config_entries/get", domain=domain)
    except HomeAssistantError:
        return None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("domain", domain) == domain:
            return entry
    return None


async def _thread_datasets(api: HomeAssistantApi) -> tuple[ThreadDataset, ...]:
    try:
        answer = await api.call("thread/list_datasets")
    except HomeAssistantError:
        return ()
    raw = answer.get("datasets") if isinstance(answer, dict) else None
    found: list[ThreadDataset] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not _str(item.get("dataset_id")):
            continue
        channel = item.get("channel")
        found.append(
            ThreadDataset(
                dataset_id=str(item["dataset_id"]),
                network_name=_str(item.get("network_name")),
                channel=channel if isinstance(channel, int) else None,
                extended_pan_id=_str(item.get("extended_pan_id")),
                preferred=bool(item.get("preferred")),
            )
        )
    return tuple(found)


# Home Assistant asks the Matter server for each node; a node that is not
# reachable yet - a sleeping Thread device right after the move from Home
# Assistant - kept the device list waiting up to 30 seconds for each, which
# on the first real migration (7 November 2026) looked like a hang. Such a
# node is listed as "other" and is there on the next read.
_DIAGNOSTICS_SECONDS = 5.0


async def _matter_kind(
    api: HomeAssistantApi, ha_device_id: str
) -> tuple[int | None, str | None, str | None]:
    """`(node_id, network_type, node_type)` from Home Assistant's Matter
    diagnostics, or `None`s for a device it cannot describe (offline)."""
    try:
        diagnostics = await asyncio.wait_for(
            api.call("matter/node_diagnostics", device_id=ha_device_id),
            _DIAGNOSTICS_SECONDS,
        )
    except (HomeAssistantError, TimeoutError):
        return None, None, None
    if not isinstance(diagnostics, dict):
        return None, None, None
    node_id = diagnostics.get("node_id")
    return (
        node_id if isinstance(node_id, int) else None,
        _str(diagnostics.get("network_type")),
        _str(diagnostics.get("node_type")),
    )


def _availability(kind: DeviceKind, capabilities: Capabilities) -> Availability:
    if kind == "matter_thread":
        return "ready" if capabilities.thread else "needs_thread"
    if kind in ("matter_wifi", "matter_bridge"):
        return "ready"
    if kind == "zigbee":
        return "ready" if capabilities.zigbee else "needs_zigbee"
    return "unsupported"


async def read_inventory(api: HomeAssistantApi, capabilities: Capabilities) -> Inventory:
    registry = await api.call("config/device_registry/list")
    areas = await api.call("config/area_registry/list")
    area_names = {
        str(area.get("area_id")): str(area.get("name"))
        for area in (areas if isinstance(areas, list) else [])
        if isinstance(area, dict) and area.get("area_id")
    }
    entries = [
        entry
        for entry in (registry if isinstance(registry, list) else [])
        if isinstance(entry, dict)
        and entry.get("id")
        and entry.get("entry_type") != "service"
        and not entry.get("disabled_by")
    ]
    by_id = {str(entry["id"]): entry for entry in entries}
    zha_entry = await _entry(api, "zha")
    matter_entry = await _entry(api, "matter")

    def room_of(entry: dict[str, Any]) -> str | None:
        area = entry.get("area_id")
        return area_names.get(area) if isinstance(area, str) else None

    def done_or(ha_device_id: str, availability: Availability) -> Availability:
        return "done" if ha_device_id in capabilities.done else availability

    devices: list[InventoryDevice] = []

    # Matter: one entry per node (see the module docstring).
    nodes: dict[int, list[tuple[dict[str, Any], str | None, str | None]]] = {}
    for entry in entries:
        if "matter" not in _domains(entry):
            continue
        node_id, network_type, node_type = await _matter_kind(api, str(entry["id"]))
        if (
            node_id is None
            and capabilities.fabric is not None
            and _matter_fabric(entry) == capabilities.fabric
        ):
            # Not answering yet, but already in this bridge's fabric: the
            # identifier names the node, and entering it needs no answer.
            node_id = _matter_node(entry)
        if node_id is None:
            devices.append(
                InventoryDevice(
                    ha_device_id=str(entry["id"]),
                    name=_device_name(entry),
                    room=room_of(entry),
                    kind="other",
                    availability="unsupported",
                    manufacturer=_str(entry.get("manufacturer")),
                    model=_str(entry.get("model")),
                    integration="matter",
                )
            )
            continue
        nodes.setdefault(node_id, []).append((entry, network_type, node_type))
    for node_id, members in nodes.items():
        # The node's own registry device is the one not reached through
        # another device of the same node - the bridge, for a bridge.
        member_ids = {str(entry["id"]) for entry, _, _ in members}
        root = next(
            (entry for entry, _, _ in members if entry.get("via_device_id") not in member_ids),
            members[0][0],
        )
        network_type = members[0][1]
        node_type = members[0][2]
        kind: DeviceKind
        if len(members) > 1 or node_type == "bridge":
            kind = "matter_bridge"
        elif network_type == "thread":
            kind = "matter_thread"
        else:
            kind = "matter_wifi"
        ha_id = str(root["id"])
        in_fabric = capabilities.fabric is not None and _matter_fabric(root) == capabilities.fabric
        node_availability: Availability = (
            "ready" if in_fabric else _availability(kind, capabilities)
        )
        devices.append(
            InventoryDevice(
                ha_device_id=ha_id,
                name=_device_name(root),
                room=room_of(root),
                kind=kind,
                availability=done_or(ha_id, node_availability),
                manufacturer=_str(root.get("manufacturer")),
                model=_str(root.get("model")),
                integration="matter",
                members=tuple(_device_name(entry) for entry, _, _ in members if entry is not root),
                matter_node_id=node_id,
                in_fabric=in_fabric,
            )
        )

    # Zigbee: names and rooms from the registry, the signature from ZHA.
    if zha_entry is not None:
        try:
            zha_devices = await api.call("zha/devices")
        except HomeAssistantError:
            zha_devices = []
        for info in zha_devices if isinstance(zha_devices, list) else []:
            if not isinstance(info, dict):
                continue
            imported = imported_device_from_zha(info)
            if imported is None:
                continue
            reg_id = _str(info.get("device_reg_id"))
            registered = by_id.get(reg_id) if reg_id else None
            ha_id = reg_id or imported.ieee
            availability: Availability = (
                _availability("zigbee", capabilities) if imported.reports_values else "later"
            )
            devices.append(
                InventoryDevice(
                    ha_device_id=ha_id,
                    name=(
                        _str(info.get("user_given_name"))
                        or (_device_name(registered) if registered is not None else None)
                        or _str(info.get("name"))
                        or imported.ieee
                    ),
                    room=room_of(registered) if registered is not None else None,
                    kind="zigbee",
                    availability=done_or(ha_id, availability),
                    manufacturer=imported.manufacturer,
                    model=imported.model,
                    integration="zha",
                    zigbee=imported,
                )
            )

    # Everything else the user might wonder about: shown, not offered.
    listed = {device.ha_device_id for device in devices}
    for nodes_members in nodes.values():
        listed.update(str(entry["id"]) for entry, _, _ in nodes_members)
    for entry in entries:
        ha_id = str(entry["id"])
        if ha_id in listed or entry.get("via_device_id") in listed:
            continue
        if not entry.get("manufacturer") and not entry.get("model"):
            continue
        domains = _domains(entry)
        if "zha" in domains:
            # A ZHA device ZHA itself did not list - the coordinator.
            continue
        devices.append(
            InventoryDevice(
                ha_device_id=ha_id,
                name=_device_name(entry),
                room=room_of(entry),
                kind="other",
                availability="unsupported",
                manufacturer=_str(entry.get("manufacturer")),
                model=_str(entry.get("model")),
                integration=min(domains) if domains else None,
            )
        )

    devices.sort(key=lambda device: ((device.room or "￿").lower(), device.name.lower()))
    return Inventory(
        devices=tuple(devices),
        thread_datasets=await _thread_datasets(api),
        zha_entry_id=_str(zha_entry.get("entry_id")) if zha_entry else None,
        zha_disabled=bool(zha_entry.get("disabled_by")) if zha_entry else False,
        matter_entry_id=_str(matter_entry.get("entry_id")) if matter_entry else None,
    )
