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

"""Taking over a Zigbee network from Home Assistant's ZHA (design
2026-11-07, section 6).

ZHA and this bridge stand on the same library, zigpy, and that is what
makes a takeover without re-pairing possible at all:

- **The network** travels as zigpy's own `NetworkBackup`, which ZHA hands
  out through `zha/network/backups/create`. On a stick that moved over from
  Home Assistant the network is already on the stick (EmberZNet keeps it in
  the NCP's tokens) and nothing has to be written; on a different stick the
  backup is restored onto it.
- **The devices** travel as the signature ZHA reports for each of them in
  `zha/devices`: node descriptor, endpoints with profile and device type,
  input and output clusters, manufacturer and model. That is exactly what
  zigpy's interview would have read from the device, so a device built from
  it is a device zigpy considers initialised - nobody has to wake a
  battery sensor or reset a lamp.

Like `source.py`, this module imports zigpy only inside functions: an
installation without a Zigbee stick never pays for loading it.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

# bellows writes the coordinator's EUI64 only when the backup carries this
# key in its stack-specific EZSP section. The EUI64 lives in a token that
# most EmberZNet sticks let you write ONCE; ZHA's "migrate radio" sets the
# same key (`EZSP_OVERWRITE_EUI64` in Home Assistant's ZHA integration).
EZSP_OVERWRITE_EUI64: Final = "i_understand_i_can_update_eui64_only_once_and_i_still_want_to_do_it"

# The clusters every Zigbee device carries for housekeeping. A device that
# serves nothing BUT these - a remote control, a scene button - sends
# commands instead of reporting values, which the bridge cannot pass on to
# Loxone yet (design 2026-09-12, section 1: remotes and buttons are 2b).
_HOUSEKEEPING_CLUSTERS: Final = frozenset(
    {
        0x0000,  # Basic
        0x0001,  # Power Configuration
        0x0003,  # Identify
        0x0004,  # Groups
        0x0005,  # Scenes
        0x0019,  # OTA Upgrade
        0x0020,  # Poll Control
        0x0B05,  # Diagnostics
        0x1000,  # ZLL Commissioning
    }
)


@dataclass(frozen=True)
class NetworkIdentity:
    """What tells two Zigbee networks, and two coordinators, apart. Never
    the network key."""

    extended_pan_id: str | None
    coordinator_ieee: str | None
    channel: int | None

    def as_json(self) -> dict[str, object]:
        return {
            "extended_pan_id": self.extended_pan_id,
            "coordinator_ieee": self.coordinator_ieee,
            "channel": self.channel,
        }


@dataclass(frozen=True)
class ImportedEndpoint:
    endpoint_id: int
    profile_id: int | None
    device_type: int | None
    input_clusters: tuple[int, ...]
    output_clusters: tuple[int, ...]


@dataclass(frozen=True)
class ImportedDevice:
    """One device as ZHA describes it, ready to be rebuilt in zigpy."""

    ieee: str
    nwk: int
    manufacturer: str | None
    model: str | None
    node_descriptor: dict[str, Any] | None
    endpoints: tuple[ImportedEndpoint, ...]

    @property
    def reports_values(self) -> bool:
        """Whether any endpoint serves a cluster beyond housekeeping - the
        difference between a sensor or a lamp and a remote control."""
        return any(
            set(endpoint.input_clusters) - _HOUSEKEEPING_CLUSTERS for endpoint in self.endpoints
        )


def _normalised_ieee(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    digits = text.replace(":", "")
    if len(digits) != 16 or any(c not in "0123456789abcdef" for c in digits):
        return None
    return ":".join(digits[i : i + 2] for i in range(0, 16, 2))


def _number(value: object) -> int | None:
    """ZHA writes ids as `"0x0104"`, and NWK addresses sometimes as plain
    numbers - both are read, anything else is `None`."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip(), 0)
        except ValueError:
            return None
    return None


def _clusters(value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    found = (_number(item) for item in value)
    return tuple(item for item in found if item is not None)


def imported_device_from_zha(info: dict[str, Any]) -> ImportedDevice | None:
    """One entry of `zha/devices`, or `None` for an entry this bridge
    cannot rebuild: the coordinator itself, a Green Power device without
    endpoints, or an entry without a readable address."""
    if info.get("active_coordinator"):
        return None
    ieee = _normalised_ieee(info.get("ieee"))
    nwk = _number(info.get("nwk"))
    signature = info.get("signature")
    if ieee is None or nwk is None or not isinstance(signature, dict):
        return None
    raw_endpoints = signature.get("endpoints")
    if not isinstance(raw_endpoints, dict) or not raw_endpoints:
        return None
    endpoints: list[ImportedEndpoint] = []
    for key, raw in raw_endpoints.items():
        endpoint_id = _number(key)
        if endpoint_id is None or endpoint_id == 0 or not isinstance(raw, dict):
            continue
        endpoints.append(
            ImportedEndpoint(
                endpoint_id=endpoint_id,
                profile_id=_number(raw.get("profile_id")),
                device_type=_number(raw.get("device_type")),
                input_clusters=_clusters(raw.get("input_clusters")),
                output_clusters=_clusters(raw.get("output_clusters")),
            )
        )
    if not endpoints:
        return None
    node_descriptor = signature.get("node_descriptor")
    manufacturer = info.get("manufacturer") or signature.get("manufacturer")
    model = info.get("model") or signature.get("model")
    return ImportedDevice(
        ieee=ieee,
        nwk=nwk,
        manufacturer=manufacturer if isinstance(manufacturer, str) else None,
        model=model if isinstance(model, str) else None,
        node_descriptor=node_descriptor if isinstance(node_descriptor, dict) else None,
        endpoints=tuple(sorted(endpoints, key=lambda e: e.endpoint_id)),
    )


def backup_identity(backup: dict[str, Any]) -> NetworkIdentity:
    """The identity a zigpy backup (as `NetworkBackup.as_dict()` writes it)
    describes. Reads the dictionary rather than parsing it with zigpy, so
    the API can answer without loading the radio libraries."""
    network = backup.get("network_info") if isinstance(backup, dict) else None
    node = backup.get("node_info") if isinstance(backup, dict) else None
    network = network if isinstance(network, dict) else {}
    node = node if isinstance(node, dict) else {}
    epid = network.get("extended_pan_id")
    channel = network.get("channel")
    return NetworkIdentity(
        extended_pan_id=_normalised_ieee(epid),
        coordinator_ieee=_normalised_ieee(node.get("ieee")),
        channel=channel if isinstance(channel, int) and not isinstance(channel, bool) else None,
    )


def network_identity(app: Any) -> NetworkIdentity:
    """The identity of the network a running zigpy application holds."""
    state = getattr(app, "state", None)
    network = getattr(state, "network_info", None)
    node = getattr(state, "node_info", None)
    channel = getattr(network, "channel", None)
    return NetworkIdentity(
        extended_pan_id=_normalised_ieee(str(getattr(network, "extended_pan_id", "") or "")),
        coordinator_ieee=_normalised_ieee(str(getattr(node, "ieee", "") or "")),
        channel=int(channel) if isinstance(channel, int) else None,
    )


async def restore_backup(app: Any, backup: dict[str, Any], *, overwrite_ieee: bool) -> None:
    """Write `backup` onto the stick `app` holds.

    `overwrite_ieee` asks bellows to give the stick the old coordinator's
    EUI64 as well - what keeps every binding and every IAS enrolment of
    the old network pointing at the right address. zigpy raises when the
    stick cannot take it; the caller decides whether to go on without."""
    from zigpy.backups import NetworkBackup

    parsed = NetworkBackup.from_dict(backup)
    if overwrite_ieee:
        parsed.network_info.stack_specific.setdefault("ezsp", {})[EZSP_OVERWRITE_EUI64] = True
    await app.backups.restore_backup(parsed)


def _module(name: str) -> Any:
    """A zigpy module as `Any`: zigpy's types are generated at runtime and
    largely untyped, and this module only builds objects from them."""
    return importlib.import_module(name)


def _node_descriptor(raw: dict[str, Any] | None) -> Any | None:
    if raw is None:
        return None
    zdo_t = _module("zigpy.zdo.types")
    try:
        return zdo_t.NodeDescriptor(**raw)
    except Exception as exc:  # noqa: BLE001 - a shape this zigpy does not know
        logger.info("could not rebuild a node descriptor from ZHA: %s", type(exc).__name__)
        return None


def _device_type(profile_id: int | None, device_type: int | None) -> Any:
    if device_type is None:
        return None
    zha = _module("zigpy.profiles.zha")
    zll = _module("zigpy.profiles.zll")
    try:
        if profile_id == zha.PROFILE_ID:
            return zha.DeviceType(device_type)
        if profile_id == zll.PROFILE_ID:
            return zll.DeviceType(device_type)
    except ValueError:
        return device_type
    return device_type


def build_device(app: Any, imported: ImportedDevice) -> Any:
    """A zigpy device built from ZHA's description, registered with `app`
    but not yet announced - the caller announces it with
    `app.device_initialized(device)`, which saves it to zigpy's database,
    applies quirks and reaches this bridge's own listener.

    Manufacturer and model go into the Basic cluster's attribute cache as
    well as onto the device: zigpy reads them back from that cache when it
    loads its database, and without them a restart would bring the device
    back nameless and without its quirk."""
    t = _module("zigpy.types")
    device_status = _module("zigpy.device").Status
    endpoint_status = _module("zigpy.endpoint").Status

    device = app.add_device(t.EUI64.convert(imported.ieee), t.NWK(imported.nwk))
    device.node_desc = _node_descriptor(imported.node_descriptor)
    for spec in imported.endpoints:
        endpoint = device.add_endpoint(spec.endpoint_id)
        endpoint.profile_id = spec.profile_id
        endpoint.device_type = _device_type(spec.profile_id, spec.device_type)
        for cluster_id in spec.input_clusters:
            endpoint.add_input_cluster(cluster_id)
        for cluster_id in spec.output_clusters:
            endpoint.add_output_cluster(cluster_id)
        endpoint.status = endpoint_status.ZDO_INIT
    if imported.manufacturer is not None:
        device.manufacturer = imported.manufacturer
    if imported.model is not None:
        device.model = imported.model
    for endpoint in device.non_zdo_endpoints:
        basic = endpoint.in_clusters.get(0x0000)
        if basic is None:
            continue
        if imported.manufacturer is not None:
            basic.update_attribute(0x0004, imported.manufacturer)
        if imported.model is not None:
            basic.update_attribute(0x0005, imported.model)
        break
    device.status = device_status.ENDPOINTS_INIT
    return device
