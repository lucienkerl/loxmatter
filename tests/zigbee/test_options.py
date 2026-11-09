"""Configuration values read from a device's quirk, and written to it
(design 2026-11-08, sections 3 and 5).

The quirk half runs against the REAL zha-quirks registry and real zigpy
devices, not fakes: what is under test is exactly how this bridge reads
another package's metadata, and a fake would only repeat this module's own
assumptions back to it. That costs one `zhaquirks.setup()` (2-3 s) per run
of this file."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakes import DeliveryError

from loxmatter.device_config import ConfigOption
from loxmatter.model.store import Store
from loxmatter.model.zigbee_pending_store import PendingWrite
from loxmatter.zigbee import options as options_module
from loxmatter.zigbee.options import config_options, missing_values, read_missing, write_pending

IAS_ZONE = 0x0500
TUYA = 0xEF00


@pytest.fixture(scope="module")
def registry() -> Any:
    import zhaquirks

    zhaquirks.setup()
    import loxmatter.zigbee.local_quirks  # noqa: F401 - registers on import

    return zhaquirks.ZHA_DEVICE_REGISTRY


def _device(registry: Any, manufacturer: str, model: str, clusters: list[int]) -> Any:
    import zigpy.device
    import zigpy.types as t

    app = MagicMock()
    device = zigpy.device.Device(app, t.EUI64.convert("a4:c1:38:a6:0e:d0:31:74"), 0x1234)
    device.manufacturer = manufacturer
    device.model = model
    endpoint = device.add_endpoint(1)
    endpoint.profile_id = 260
    endpoint.device_type = 0x0402
    for cluster_id in clusters:
        endpoint.add_input_cluster(cluster_id)
    return registry.resolve(device)


def _by_name(options: tuple[ConfigOption, ...]) -> dict[str, ConfigOption]:
    return {option.name: option for option in options}


def test_the_zg_204z_gets_keep_time_and_sensitivity_from_loxmatters_own_quirk(registry):
    """Measured on 8 November 2026: the sensor reports `HOBEIAN` /
    `ZG-204Z`, which no zha-quirks quirk matches."""
    device = _device(registry, "HOBEIAN", "ZG-204Z", [0, 1, 3, IAS_ZONE, TUYA])

    found = _by_name(config_options(device))

    assert set(found) == {"keep_time", "current_zone_sensitivity_level"}
    keep_time = found["keep_time"]
    assert (keep_time.endpoint, keep_time.cluster_id, keep_time.attribute_id) == (
        1,
        IAS_ZONE,
        0xF001,
    )
    assert keep_time.kind == "enum"
    assert keep_time.translation_key == "keep_time"
    assert [keep_time.choice_value(c) for c in keep_time.choices] == [30, 60, 120]
    sensitivity = found["current_zone_sensitivity_level"]
    assert sensitivity.attribute_id == 0x0013
    assert [c.name for c in sensitivity.choices] == ["Low", "Medium", "High"]
    assert not sensitivity.by_number


def test_the_keep_time_goes_out_as_zigbee2mqtt_writes_it(registry):
    """uint8 on the wire and no manufacturer code - zigbee2mqtt's
    `zg204_attr` converter, which the device is known to accept."""
    from zigpy.zcl.foundation import DataTypeId

    device = _device(registry, "HOBEIAN", "ZG-204Z", [0, 1, 3, IAS_ZONE, TUYA])
    cluster = device.endpoints[1].in_clusters[IAS_ZONE]
    cluster.write_attributes_raw = AsyncMock(return_value=[[]])

    asyncio.run(cluster.write_attributes({0xF001: 2}))

    (records,), kwargs = cluster.write_attributes_raw.call_args
    assert kwargs["manufacturer_code"] is None
    assert records[0].attrid == 0xF001
    assert records[0].value.type == DataTypeId.uint8
    assert int(records[0].value.value) == 2


def test_an_upstream_tuya_quirk_brings_its_settings_without_any_loxmatter_code(registry):
    """The ZG-204ZL's upstream quirk: two selects and a number on the Tuya
    cluster - none of it named anywhere in loxmatter."""
    device = _device(registry, "_TZE200_3towulqd", "TS0601", [0, 1, 3, TUYA])

    found = _by_name(config_options(device))

    assert {"presence_sensitivity", "fading_time", "illuminance_interval"} <= set(found)
    fading = found["fading_time"]
    assert [fading.choice_value(c) for c in fading.choices] == [10, 30, 60, 120]
    interval = found["illuminance_interval"]
    assert interval.kind == "number"
    assert (interval.minimum, interval.maximum, interval.step) == (1, 720, 1)
    assert interval.unit == "min"


def test_a_reading_is_no_setting_and_a_switch_is_one(registry):
    """The ZG-204ZM's `human_motion_state` is an enum on the sensor
    platform: shown by Home Assistant, written by nobody."""
    device = _device(registry, "HOBEIAN", "ZG-204ZM", [0, 1, 3, TUYA])

    found = _by_name(config_options(device))

    assert "human_motion_state" not in found
    assert found["find_switch"].kind == "switch"
    assert found["static_detection_distance"].multiplier == pytest.approx(0.01)


def test_a_device_without_a_quirk_has_no_settings(registry):
    device = _device(registry, "IKEA of Sweden", "nothing-like-this", [0, 6])
    assert config_options(device) == ()


def test_the_metadata_classes_are_still_called_what_this_bridge_looks_for():
    """`zigbee/options.py` tells the descriptions apart by class NAME, so
    it can stay importable without zhaquirks. A rename upstream would
    silently empty every device's configuration - this catches it."""
    from zhaquirks.builder import metadata

    for name in ("NumberMetadata", "ZCLEnumMetadata", "SwitchMetadata"):
        assert hasattr(metadata, name)


def test_a_missing_value_is_found_and_read_once_per_interval(registry):
    device = _device(registry, "HOBEIAN", "ZG-204Z", [0, 1, 3, IAS_ZONE, TUYA])
    cluster = device.endpoints[1].in_clusters[IAS_ZONE]
    cluster.read_attributes = AsyncMock(return_value=({}, {}))
    options_module._read_attempted_at.clear()

    assert missing_values(device) == {(1, IAS_ZONE): [0xF001, 0x0013]}
    asyncio.run(read_missing(device, now=1000.0))
    asyncio.run(read_missing(device, now=1060.0))
    assert cluster.read_attributes.await_count == 1
    asyncio.run(read_missing(device, now=1000.0 + 16 * 60))
    assert cluster.read_attributes.await_count == 2


# ------------------------------------------------------------- writing --


class _Record:
    def __init__(self, status: int) -> None:
        self.status = status


class _Cluster:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.writes: list[dict[int, object]] = []

    async def write_attributes(self, attributes: dict[int, object]) -> object:
        self.writes.append(attributes)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _Endpoint:
    def __init__(self, cluster: _Cluster) -> None:
        self.in_clusters = {IAS_ZONE: cluster}


class _Device:
    ieee = "a4:c1:38:a6:0e:d0:31:74"

    def __init__(self, cluster: _Cluster) -> None:
        self.endpoints = {1: _Endpoint(cluster)}


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "store.sqlite")
    yield store
    store.close()


def _queue(store: Store, value: object) -> None:
    store.zigbee_pending.queue_write(_Device.ieee, 1, IAS_ZONE, 0xF001, value)


def test_a_taken_value_leaves_the_queue(store):
    cluster = _Cluster([[_Record(0)]])
    _queue(store, 2)

    assert asyncio.run(write_pending(_Device(cluster), store)) is True

    assert cluster.writes == [{0xF001: 2}]
    assert store.zigbee_pending.writes_for(_Device.ieee) == []
    assert store.zigbee_pending.addresses_with_pending() == []


def test_a_sleeping_device_keeps_its_value_queued_for_the_next_wake_up(store):
    cluster = _Cluster(DeliveryError("no answer"))
    _queue(store, 2)

    assert asyncio.run(write_pending(_Device(cluster), store)) is False

    assert store.zigbee_pending.writes_for(_Device.ieee) == [PendingWrite(1, IAS_ZONE, 0xF001, 2)]
    assert store.zigbee_pending.addresses_with_pending() == [_Device.ieee]


def test_a_refused_value_is_dropped_rather_than_retried_forever(store):
    cluster = _Cluster([[_Record(0x86)]])
    _queue(store, 2)

    assert asyncio.run(write_pending(_Device(cluster), store)) is True

    assert store.zigbee_pending.writes_for(_Device.ieee) == []


def test_a_newer_value_queued_meanwhile_survives_the_older_ones_success(store):
    _queue(store, 1)
    sent = store.zigbee_pending.writes_for(_Device.ieee)[0]
    _queue(store, 2)

    assert store.zigbee_pending.clear_write(_Device.ieee, sent) is False
    assert store.zigbee_pending.writes_for(_Device.ieee)[0].value == 2


def test_forgetting_a_device_drops_its_queued_values(store):
    _queue(store, 2)
    store.zigbee_pending.forget(_Device.ieee)
    assert store.zigbee_pending.writes_for(_Device.ieee) == []
