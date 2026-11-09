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

"""A Zigbee device's configuration values, read from its quirk.

A v2 quirk (`QuirkBuilder`) describes every value Home Assistant offers as a
number, select or switch entity in `entity_metadata`: the attribute, its
cluster and endpoint, its range or its enum, a name. That is exactly what a
`ConfigOption` needs, and it is maintained upstream for hundreds of Tuya,
Aqara and other devices - so this module reads it instead of keeping a
second catalogue (design 2026-11-08, section 3).

Where the definition lives in zha-quirks 2.2.2, measured, not guessed:
`ZHA_DEVICE_REGISTRY.resolve` stamps the transformed zigpy device with
`_quirk_registry_entry`; its `zha_device_factory` is a `QuirkV2Factory`
whose `quirk_definition.entity_metadata` is the tuple. A v1 quirk has no such
factory and no device has metadata - both read as "no options", never as an
error.

Read with `getattr` throughout and never by importing the metadata classes:
this module stays importable without zhaquirks, and its tests use plain
fakes. The class is told apart by NAME (`NumberMetadata`, `ZCLEnumMetadata`,
`SwitchMetadata`), checked against the installed package by
`tests/zigbee/test_options.py`.

Only what can be WRITTEN becomes an option. An enum on the sensor platform
(the ZG-204ZM's `human_motion_state`) is a reading, not a setting; a button
(`WriteAttributeButtonMetadata`) writes a constant and is an action. Both
stay out.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from loxmatter.device_config import ConfigOption, choice

logger = logging.getLogger(__name__)

_NUMBER = "NumberMetadata"
_ENUM = "ZCLEnumMetadata"
_SWITCH = "SwitchMetadata"

# `EntityPlatform.SELECT`'s value. Compared as text: the enum lives in
# `zha.application`, which this module must not import.
_SELECT_PLATFORM = "select"
# `ClusterType.Server`, whose value is 0. A client cluster's attribute is
# not the device's own setting.
_SERVER = 0


def quirk_entity_metadata(device: Any) -> tuple[Any, ...]:
    """The quirk's entity descriptions, or none."""
    entry = getattr(device, "_quirk_registry_entry", None)
    factory = getattr(entry, "zha_device_factory", None)
    definition = getattr(factory, "quirk_definition", None)
    metadata = getattr(definition, "entity_metadata", None)
    return tuple(metadata) if metadata else ()


def _enum_value(value: object) -> object:
    return value.value if isinstance(value, Enum) else value


def _option_from(device: Any, metadata: Any) -> ConfigOption | None:
    kind_name = type(metadata).__name__
    if kind_name not in (_NUMBER, _ENUM, _SWITCH):
        return None
    if kind_name == _ENUM and str(_enum_value(metadata.entity_platform)) != _SELECT_PLATFORM:
        return None
    if int(getattr(metadata, "cluster_type", _SERVER)) != _SERVER:
        return None
    endpoint_id = int(getattr(metadata, "endpoint_id", 1))
    cluster_id = int(metadata.cluster_id)
    endpoint = getattr(device, "endpoints", {}).get(endpoint_id)
    cluster = None if endpoint is None else endpoint.in_clusters.get(cluster_id)
    if cluster is None:
        return None
    name = str(metadata.attribute_name)
    definition = getattr(cluster, "attributes_by_name", {}).get(name)
    if definition is None:
        return None
    title = str(getattr(metadata, "fallback_name", None) or name)
    translation_key = getattr(metadata, "translation_key", None)
    common: dict[str, Any] = {
        "endpoint": endpoint_id,
        "cluster_id": cluster_id,
        "attribute_id": int(definition.id),
        "name": name,
        "fallback_title": title,
        "translation_key": str(translation_key) if translation_key else None,
    }
    if kind_name == _ENUM:
        members = tuple(choice(int(member.value), str(member.name)) for member in metadata.enum)
        if not members:
            return None
        return ConfigOption(kind="enum", choices=members, **common)
    if kind_name == _SWITCH:
        return ConfigOption(
            kind="switch",
            on_raw=int(getattr(metadata, "on_value", 1)),
            off_raw=int(getattr(metadata, "off_value", 0)),
            **common,
        )
    multiplier = getattr(metadata, "multiplier", None)
    return ConfigOption(
        kind="number",
        unit=str(getattr(metadata, "unit", None) or ""),
        minimum=_float_or_none(getattr(metadata, "min", None)),
        maximum=_float_or_none(getattr(metadata, "max", None)),
        step=_float_or_none(getattr(metadata, "step", None)),
        multiplier=float(multiplier) if multiplier else 1.0,
        **common,
    )


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def config_options(device: Any) -> tuple[ConfigOption, ...]:
    """Every writable value the device's quirk describes, in its order.

    A description this module cannot turn into an option - an attribute the
    quirk names but the cluster does not declare, say - is skipped and
    logged, never raised: a device must not lose its signals over a setting.
    The first description of an attribute wins; two entities over one
    attribute would otherwise become two outputs writing the same value."""
    options: list[ConfigOption] = []
    seen: set[tuple[int, int, int]] = set()
    for metadata in quirk_entity_metadata(device):
        try:
            option = _option_from(device, metadata)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.debug("skipping a quirk entity of %s: %s", getattr(device, "ieee", "?"), exc)
            continue
        if option is None:
            continue
        identity = (option.endpoint, option.cluster_id, option.attribute_id)
        if identity in seen:
            continue
        seen.add(identity)
        options.append(option)
    return tuple(options)


# ------------------------------------------------------------ writing --
#
# A value is never written from the request that set it (design 2026-11-08,
# section 5). The request queues it in `zigbee_pending_write` and returns;
# the device's wake-up watcher (`configure.py`) writes it the moment the
# device is heard, and a mains-powered device is asked at once through the
# same path. A battery sensor sleeps between detections, and a write sent
# into that sleep costs up to ~28 s and fails - waiting for it would hold
# the web UI's request and Loxone's `/cmd` for nothing.

# How long after a failed read of a missing value the next one is tried.
# Reading costs a battery sensor a radio round trip each time it wakes; a
# device that does not answer the read at all must not pay that on every
# detection.
_READ_RETRY_SECONDS = 15 * 60

_read_attempted_at: dict[str, float] = {}

_TUYA_MCU_CLUSTER = 0xEF00


def _records(result: object) -> list[Any]:
    """`write_attributes`' answer as one flat list of status records."""
    records: list[Any] = []
    if isinstance(result, (list, tuple)):
        for item in result:
            if isinstance(item, (list, tuple)):
                records.extend(item)
            else:
                records.append(item)
    return records


def _refused(result: object) -> int | None:
    """The first non-success status in a write's answer, or None."""
    for record in _records(result):
        status = getattr(record, "status", 0)
        if int(status) != 0:
            return int(status)
    return None


async def write_pending(device: Any, store: Any) -> bool:
    """Writes what `zigbee_pending_write` holds for this device. Answers
    whether everything went - False when the device did not answer, so the
    caller knows the rest waits for the next wake-up.

    A value the device REFUSES is dropped with a warning: it will refuse it
    on every wake-up, and a row that can never clear would keep the web UI
    saying "waiting for the device" forever."""
    from loxmatter.profiles.table import VENDOR_PROPERTIES_CLUSTER
    from loxmatter.zigbee.configure import _describe, _is_unreachable
    from loxmatter.zigbee.herdsman.library import active

    address = str(device.ieee)
    pending = store.zigbee_pending
    library = active()
    for write in pending.writes_for(address):
        endpoint = device.endpoints.get(write.endpoint)
        cluster = None if endpoint is None else endpoint.in_clusters.get(write.cluster_id)
        by_library = write.cluster_id == VENDOR_PROPERTIES_CLUSTER
        if by_library:
            # A property of the zigbee2mqtt device library: written by the
            # library, through its catalogue or its engine (design
            # 2026-11-09, section 8.1).
            if library is None or library.property_of(address, write.attribute_id) is None:
                pending.clear_write(address, write)
                continue
        elif cluster is None:
            pending.clear_write(address, write)
            continue
        try:
            if by_library:
                assert library is not None
                await library.write(device, write.attribute_id, write.value)
                result: object = None
            else:
                assert cluster is not None
                result = await cluster.write_attributes({write.attribute_id: write.value})
        except Exception as exc:  # noqa: BLE001 - every failure is sorted below
            if _is_unreachable(exc):
                logger.info(
                    "%s did not take configuration value %#06x yet, retrying when it wakes: %s",
                    address,
                    write.attribute_id,
                    _describe(exc),
                )
                return False
            logger.warning(
                "%s: writing configuration value %#06x failed, dropping it: %s",
                address,
                write.attribute_id,
                _describe(exc),
            )
            pending.clear_write(address, write)
            continue
        status = _refused(result)
        if status is not None:
            logger.warning(
                "%s refused configuration value %#06x = %r (status %#04x)",
                address,
                write.attribute_id,
                write.value,
                status,
            )
        else:
            logger.info(
                "%s took configuration value %#06x = %r", address, write.attribute_id, write.value
            )
        pending.clear_write(address, write)
    return True


def missing_values(device: Any) -> dict[tuple[int, int], list[int]]:
    """`(endpoint, cluster) -> attribute ids` of the options whose current
    value the bridge has never seen. The ZG-204Z never reports its keep
    time on its own; without a read, the web UI could not show it."""
    missing: dict[tuple[int, int], list[int]] = {}
    for option in config_options(device):
        endpoint = device.endpoints.get(option.endpoint)
        cluster = None if endpoint is None else endpoint.in_clusters.get(option.cluster_id)
        if cluster is None or option.cluster_id == _TUYA_MCU_CLUSTER:
            # A Tuya data point cannot be read like an attribute: the
            # device reports its data points by itself when it wakes.
            continue
        try:
            value = cluster.get(option.name)
        except KeyError:
            value = None
        if value is None:
            missing.setdefault((option.endpoint, option.cluster_id), []).append(option.attribute_id)
    return missing


async def read_missing(device: Any, *, now: float) -> None:
    """Reads the options' values the bridge has not seen, at most once per
    `_READ_RETRY_SECONDS` per device. A read that is answered fills
    zigpy's attribute cache and emits `attribute_read`, which the source's
    listener turns into an update like any report."""
    from loxmatter.zigbee.configure import _describe

    address = str(device.ieee)
    missing = missing_values(device)
    if not missing:
        _read_attempted_at.pop(address, None)
        return
    last = _read_attempted_at.get(address)
    if last is not None and now - last < _READ_RETRY_SECONDS:
        return
    _read_attempted_at[address] = now
    for (endpoint_id, cluster_id), attribute_ids in missing.items():
        cluster = device.endpoints[endpoint_id].in_clusters[cluster_id]
        try:
            await cluster.read_attributes(attribute_ids)
        except Exception as exc:  # noqa: BLE001 - a failed read is retried later
            logger.info(
                "could not read the configuration values of %s: %s", address, _describe(exc)
            )
            return
