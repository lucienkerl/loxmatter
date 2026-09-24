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

"""Turning what zigpy knows into what loxmatter's Matter-shaped middle
expects (design 2026-09-12, section 5).

Pure in, pure out: these tests build `DeviceFacts` by hand and never touch
zigpy, a radio or a database. Section 10.3 of the design is the reason they
are written this thoroughly - there is no Zigbee stick on the test Pi, so
nothing here has been exercised against real hardware, and this suite is the
only evidence that exists."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from loxmatter.matter.discovery import extract_signals
from loxmatter.profiles.relevance import (
    ROOT_NODE_DEVICE_TYPE,
    device_types_by_endpoint,
    is_functional,
)
from loxmatter.zigbee.translate import (
    _SENTINELS,
    BLOCKED_CLUSTER_IDS,
    TRADFRI_MOTION_SENSOR_MODEL,
    DeviceFacts,
    EndpointFacts,
    accepted_commands,
    build_snapshot,
    ias_matter_device_type,
    matter_device_type,
    rename_payload,
)

ZHA_PROFILE = 0x0104
ZLL_PROFILE = 0xC05E


def _lamp(overrides: Mapping[tuple[int, int], object] | None = None) -> DeviceFacts:
    """A colour lamp on endpoint 1, the shape of the test hardware.

    NOTE ON A BRIEF MISMATCH: the plan's version of this helper took
    `**attributes` and was called as `_lamp(**{(0x0300, 0x0007): None})` -
    but a dict with a tuple key cannot be splatted as keyword arguments
    (`TypeError: keywords must be strings`, verified interactively before
    writing this). This version takes a plain mapping instead; the call
    sites below pass their overrides positionally.
    """
    attributes: dict[tuple[int, int], object] = {(0x0006, 0x0000): True, (0x0008, 0x0000): 254}
    if overrides:
        attributes.update(overrides)
    return DeviceFacts(
        ieee="00:12:4b:00:1c:a1:b2:c3",
        manufacturer="IKEA of Sweden",
        model="TRADFRI bulb",
        is_mains_powered=True,
        available=True,
        quirk_applied=False,
        endpoints=(
            EndpointFacts(
                endpoint=1,
                profile_id=ZLL_PROFILE,
                device_type=0x0210,
                in_cluster_ids=frozenset({0x0006, 0x0008, 0x0300}),
                attributes=attributes,
            ),
        ),
    )


def _ias(*, zone_type: int, zone_status: int) -> DeviceFacts:
    """A one-endpoint device carrying only an IAS Zone cluster."""
    return DeviceFacts(
        ieee="00:12:4b:00:1c:a1:b2:c6",
        manufacturer="m",
        model="d",
        is_mains_powered=False,
        available=True,
        quirk_applied=False,
        endpoints=(
            EndpointFacts(
                endpoint=1,
                profile_id=ZHA_PROFILE,
                device_type=0x0402,
                in_cluster_ids=frozenset({0x0500}),
                attributes={(0x0500, 0x0001): zone_type, (0x0500, 0x0002): zone_status},
            ),
        ),
    )


def _state_of(*, zone_type: int, zone_status: int) -> object:
    """The BooleanState value the IAS edge produces for a given reading."""
    return build_snapshot(_ias(zone_type=zone_type, zone_status=zone_status)).attributes["1/69/0"]


def _onoff_sensor(*, value: bool | None) -> DeviceFacts:
    """The classic IKEA TRADFRI motion sensor (E1525, E1745): no IAS Zone
    cluster and no OccupancySensing cluster at all - `OnOff` is its OUTPUT
    cluster, never its input one, so it carries no `in_cluster_ids` here.
    `value=None` is a sensor that has never sent a command yet."""
    attributes: dict[tuple[int, int], object] = {} if value is None else {(0x0006, 0x0000): value}
    return DeviceFacts(
        ieee="d0:cf:5e:ff:fe:71:a3:19",
        manufacturer="IKEA of Sweden",
        model=TRADFRI_MOTION_SENSOR_MODEL,
        is_mains_powered=False,
        available=True,
        quirk_applied=True,
        endpoints=(
            EndpointFacts(
                endpoint=1,
                profile_id=ZHA_PROFILE,
                device_type=0x0850,
                in_cluster_ids=frozenset(),
                attributes=attributes,
            ),
        ),
    )


# --------------------------------------------------------------- snapshot --


def test_the_snapshot_carries_the_three_identity_paths_and_a_root_endpoint():
    """`NodeSnapshot.from_raw` reads 0/40/1, 0/40/3 and 0/40/18 for Matter,
    and `is_functional`'s layer 2 plus `endpoint_labels` both key off a root
    node device type on endpoint 0. Endpoint 0 is free to use: in Zigbee it
    is the ZDO endpoint and carries no ZCL clusters.

    Fault to prove it: drop the `0/29/0` root-node entry. `is_functional`
    then stops treating endpoint 0 as management, and every unnamed
    attribute there becomes a wanted signal - the device reclassifies."""
    snapshot = build_snapshot(_lamp())

    assert snapshot.technology == "zigbee"
    assert snapshot.address == "00:12:4b:00:1c:a1:b2:c3"
    assert snapshot.unique_id == "00:12:4b:00:1c:a1:b2:c3"
    assert snapshot.attributes["0/40/1"] == "IKEA of Sweden"
    assert snapshot.attributes["0/40/3"] == "TRADFRI bulb"
    assert snapshot.attributes["0/40/18"] == "00:12:4b:00:1c:a1:b2:c3"
    assert ROOT_NODE_DEVICE_TYPE in device_types_by_endpoint(snapshot)[0]


def test_a_battery_device_declares_power_source_on_endpoint_zero():
    """So the battery level at 0/47/12 survives `is_functional`'s layer 2,
    which keeps a cluster on a management endpoint only when the endpoint
    also declares the matching functional device type.

    Fault to prove it: declare only the root node. The battery level then
    drops out as not functional and Loxone never sees it."""
    facts = DeviceFacts(
        ieee="00:12:4b:00:1c:a1:b2:c4",
        manufacturer="LUMI",
        model="lumi.sensor_magnet",
        is_mains_powered=False,
        available=True,
        quirk_applied=True,
        endpoints=(
            EndpointFacts(
                endpoint=1,
                profile_id=ZHA_PROFILE,
                device_type=0x0402,
                in_cluster_ids=frozenset({0x0500, 0x0001}),
                attributes={(0x0500, 0x0001): 0x0015, (0x0500, 0x0002): 0, (0x0001, 0x0021): 200},
            ),
        ),
    )

    snapshot = build_snapshot(facts)
    types = device_types_by_endpoint(snapshot)

    assert 0x0011 in types[0]  # PowerSource
    assert snapshot.attributes["0/47/12"] == 200
    ref = next(s for s in extract_signals(snapshot) if s.path == "0/47/12")
    assert is_functional(ref, types) is True


def test_a_path_whose_value_is_none_is_left_out_entirely():
    """THE most important rule in this module (design 5.5, research D.1
    step 5). `Store.register_signals` computes `exported` only when the row
    is CREATED, and a `None` value classifies as `Exportability.NONE` - so a
    signal first seen without a value stays unexported FOREVER, until the
    user toggles it by hand. That is the single most likely "loxmatter
    paired my sensor and Loxone gets nothing" report.

    Fault to prove it: emit the path with `None` instead of omitting it.

    This module owns only the leaving-out half. The other half - a value
    that arrives later really does create the row - belongs to
    `ZigbeeSource.follow` (design section 5.5, Task 7) and is tested with
    that, because it needs a source, a `Runtime` and a `Store`, none of
    which this pure module has or should acquire."""
    facts = _lamp({(0x0300, 0x0007): None})

    snapshot = build_snapshot(facts)

    assert "1/768/7" not in snapshot.attributes
    assert not any(value is None for value in snapshot.attributes.values())


def _sensor(attributes: Mapping[tuple[int, int], object]) -> DeviceFacts:
    """A one-endpoint sensor carrying exactly the given attributes."""
    return DeviceFacts(
        ieee="00:12:4b:00:1c:a1:b2:c5",
        manufacturer="m",
        model="d",
        is_mains_powered=True,
        available=True,
        quirk_applied=False,
        endpoints=(
            EndpointFacts(
                endpoint=1,
                profile_id=ZHA_PROFILE,
                device_type=0x0302,
                in_cluster_ids=frozenset(cluster for cluster, _ in attributes),
                attributes=dict(attributes),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("cluster", "attribute", "sentinel", "path"),
    [
        # NEGATIVE. `TemperatureMeasurement.measured_value` is zigpy's
        # `int16s`, so the ZCL bit pattern 0x8000 reaches this module as
        # -32768; +32768 is a value that type cannot hold, and a fixture
        # feeding it tested a row the library can never trigger. See
        # `test_the_temperature_sentinel_is_the_value_zigpy_actually_delivers`.
        (0x0402, 0x0000, -32768, "1/1026/0"),  # temperature, invalid (int16s)
        (0x0405, 0x0000, 0xFFFF, "1/1029/0"),  # humidity, invalid (uint16_t)
        (0x0400, 0x0000, 0xFFFF, "1/1024/0"),  # illuminance, invalid (uint16_t)
        (0x0001, 0x0021, 0xFF, "0/47/12"),  # battery percentage, unknown (uint8_t)
        (0x0001, 0x0020, 0xFF, "0/47/11"),  # battery voltage, unknown (uint8_t)
    ],
)
def test_invalid_value_sentinels_become_an_absent_path(cluster, attribute, sentinel, path):
    """These are not measurements, they are the ZCL's way of saying "no
    reading". Passing them through would send -327.68 degrees, 65535 lux or
    127.5 % into Loxone as if they were real.

    Every constant above is the value the INSTALLED zigpy delivers for that
    attribute's declared type, not the bit pattern the specification prints.

    Fault to prove it: pass them through as numbers."""
    assert path not in build_snapshot(_sensor({(cluster, attribute): sentinel})).attributes


def test_the_temperature_sentinel_is_the_value_zigpy_actually_delivers():
    """The one sentinel on a SIGNED attribute, and the reason the table is
    checked against the library rather than against the specification's
    printed bit pattern.

    `TemperatureMeasurement.measured_value` is `zigpy.types.int16s`, so what
    a sensor with no reading hands this module is -32768, not +32768 - a
    value `int16s` cannot hold at all. A table entry of `0x8000` is
    therefore a row that can never match, and the sensor publishes
    -327.68 degrees into Loxone as a genuine measurement. This test pins the
    real value AND asserts that the bit pattern is not what arrives, so a
    revert to `0x8000` cannot be green again.

    zigpy is imported here and nowhere else in this file: the module under
    test is pure by design (see the module docstring), but the CONSTANT it
    compares against is a fact about the library, and a test that reasons
    about it from convention is exactly how this bug survived review.

    Fault to prove it: put `0x8000` back in `_SENTINELS`."""
    from zigpy.types import int16s
    from zigpy.zcl.clusters.measurement import TemperatureMeasurement

    attribute = TemperatureMeasurement.attributes[0x0000]
    assert attribute.type is int16s
    assert int16s.min_value == -32768
    # What the ZCL's "invalid" encoding deserialises to on this attribute.
    invalid, _rest = int16s.deserialize(b"\x00\x80")
    assert int(invalid) == -32768
    with pytest.raises(ValueError):
        int16s(0x8000)  # +32768 is not a value this attribute can ever carry

    assert "1/1026/0" not in build_snapshot(_sensor({(0x0402, 0x0000): int(invalid)})).attributes
    # and a real reading still gets through, so the row is not simply eating
    # the whole attribute
    assert build_snapshot(_sensor({(0x0402, 0x0000): -500})).attributes["1/1026/0"] == -500


@pytest.mark.parametrize(
    ("cluster", "attribute", "sentinel", "path"),
    [
        # ElectricalMeasurement (0x0B04 = 2820). The cluster an energy
        # monitoring plug reports through, and one `BLOCKED_CLUSTER_IDS` does
        # not cover - so every attribute of it reaches Loxone by number.
        (0x0B04, 0x0505, 0xFFFF, "1/2820/1285"),  # rms_voltage (uint16_t)
        (0x0B04, 0x0508, 0xFFFF, "1/2820/1288"),  # rms_current (uint16_t)
        (0x0B04, 0x050B, -32768, "1/2820/1291"),  # active_power (int16s), SIGNED
        # The measurement clusters' static bounds, signed where the cluster's
        # own reading is signed.
        (0x0402, 0x0001, -32768, "1/1026/1"),  # temperature min (int16s)
        (0x0402, 0x0002, -32768, "1/1026/2"),  # temperature max (int16s)
        (0x0403, 0x0001, -32768, "1/1027/1"),  # pressure min (int16s)
        (0x0403, 0x0002, -32768, "1/1027/2"),  # pressure max (int16s)
        (0x0405, 0x0001, 0xFFFF, "1/1029/1"),  # humidity min (uint16_t)
        (0x0405, 0x0002, 0xFFFF, "1/1029/2"),  # humidity max (uint16_t)
        (0x0404, 0x0001, 0xFFFF, "1/1028/1"),  # flow min (uint16_t)
        (0x0404, 0x0002, 0xFFFF, "1/1028/2"),  # flow max (uint16_t)
        (0x0408, 0x0001, 0xFFFF, "1/1032/1"),  # soil moisture min (uint16_t)
        (0x0408, 0x0002, 0xFFFF, "1/1032/2"),  # soil moisture max (uint16_t)
    ],
)
def test_the_electrical_and_bound_sentinels_become_an_absent_path(
    cluster, attribute, sentinel, path
):
    """The second audit's rows, and the highest-stakes ones after
    temperature: a plug that has not measured yet would otherwise publish
    65535 volts, 65535 amps or -327.68 watts into Loxone as genuine
    readings, and a sensor that leaves its bounds undefined would show an
    implausible limit that - being static - is never corrected.

    Fault to prove it: drop the rows from `_SENTINELS`."""
    assert path not in build_snapshot(_sensor({(cluster, attribute): sentinel})).attributes
    # and a plausible reading on the same path still gets through, so the row
    # is not simply eating the whole attribute
    assert build_snapshot(_sensor({(cluster, attribute): 230})).attributes[path] == 230


def test_the_electrical_measurement_sentinels_match_the_installed_zigpy_types():
    """Whether a row reads `0xFFFF` or `-32768` is a fact about the
    INSTALLED library, not about the specification's printed bit pattern -
    the trap `test_the_temperature_sentinel_is_the_value_zigpy_actually_delivers`
    documents, one cluster over. `active_power` is the signed one of the
    three, so a row copied from its two unsigned neighbours would never
    match, and a plug with no reading would publish -327.68 watts.

    Fault to prove it: give `active_power` the `0xFFFF` of its neighbours.

    zigpy is imported here for the same reason it is imported there: the
    module under test is pure, but the constants it compares against are
    facts about the library."""
    from zigpy.types import int16s, uint16_t
    from zigpy.zcl.clusters.homeautomation import ElectricalMeasurement

    assert ElectricalMeasurement.cluster_id == 0x0B04
    assert ElectricalMeasurement.attributes[0x0505].type is uint16_t
    assert ElectricalMeasurement.attributes[0x0508].type is uint16_t
    assert ElectricalMeasurement.attributes[0x050B].type is int16s
    assert uint16_t.max_value == 0xFFFF
    assert int16s.min_value == -32768
    with pytest.raises(ValueError):
        int16s(0x8000)  # +32768 is not a value `active_power` can ever carry

    assert _SENTINELS[(0x0B04, 0x0505)] == uint16_t.max_value
    assert _SENTINELS[(0x0B04, 0x0508)] == uint16_t.max_value
    assert _SENTINELS[(0x0B04, 0x050B)] == int16s.min_value


def test_a_float_measurement_of_nan_becomes_an_absent_path():
    """The ZCL's invalid value for a FLOAT-typed measurement is NaN, and no
    `_SENTINELS` row can ever suppress it: that table compares with `==`,
    and `float("nan") == float("nan")` is `False`. Without an `isnan` arm of
    its own, `nan` travels straight out to a Loxone virtual input - a value
    nothing downstream recognises as "no reading" and nothing can compare
    against either.

    The fixture is read out of the INSTALLED zigpy rather than invented:
    every cluster that declares `measured_value`, `min_measured_value` or
    `max_measured_value` as `Single` is collected from the cluster registry
    and driven through `build_snapshot`. That is 32 clusters in zigpy 2.2.0,
    every concentration measurement the ZCL defines, and none of them is
    blocked - so all 96 attributes reach Loxone by number today.

    Reachable with real devices: zha-quirks 2.2.2 declares these clusters in
    `zhaquirks/ikea/starkvind.py`, `zhaquirks/xiaomi/aqara/airm_fhac01.py`,
    `zhaquirks/tuya/tuya_co.py` and `zhaquirks/tuya/builder/__init__.py`.

    A fixture whose value is not NaN would prove nothing here, so each pair
    is driven twice: once with `nan`, which must be left out, and once with
    a plausible reading, which must arrive unchanged.

    Fault to prove it: drop `_is_not_a_number(value)` from
    `_apply_endpoint`'s guard - or try to express the same rule as
    `_SENTINELS[(0x042A, 0x0000)] = float("nan")`, which changes nothing at
    all."""
    from zigpy.types import Single
    from zigpy.zcl import Cluster

    float_measurements: list[tuple[int, int]] = []
    for cluster_id, cluster in Cluster._registry.items():
        for name in ("measured_value", "min_measured_value", "max_measured_value"):
            attribute = getattr(cluster.AttributeDefs, name, None)
            if attribute is not None and attribute.type is Single:
                float_measurements.append((cluster_id, attribute.id))

    assert issubclass(Single, float), "the isnan arm only sees these if they are floats"
    # The comparison a `_SENTINELS` row would make, spelled exactly the way
    # `_apply_endpoint` spells it. Through variables, because a literal
    # `float("nan") == float("nan")` is what ruff's PLW0177 exists to
    # forbid - and the reason it forbids it is the reason this rule cannot
    # live in that table.
    row: dict[tuple[int, int], float] = {(0x042A, 0x0000): float("nan")}
    reading = float("nan")
    assert row[(0x042A, 0x0000)] != reading, "a NaN row could never match a NaN reading"
    assert len(float_measurements) >= 32, (
        f"zigpy 2.2.0 declares 32 such clusters; found {len(float_measurements) // 3}"
    )
    # The four this project named as reachable through zha-quirks.
    for cluster_id in (0x042A, 0x040D, 0x040C, 0x042B):
        assert (cluster_id, 0x0000) in float_measurements
        assert cluster_id not in BLOCKED_CLUSTER_IDS, "nothing else keeps these off the wire"

    for cluster_id, attribute_id in float_measurements:
        path = f"1/{cluster_id}/{attribute_id}"
        absent = build_snapshot(_sensor({(cluster_id, attribute_id): float("nan")})).attributes
        assert path not in absent, f"{path} published a NaN as if it were a measurement"
        present = build_snapshot(_sensor({(cluster_id, attribute_id): 12.5})).attributes
        assert present[path] == 12.5, f"{path} must still carry a real reading"


def test_the_battery_voltage_is_converted_from_hundred_millivolt_units():
    """Zigbee's `BatteryVoltage` counts in 100 mV units; Matter's
    PowerSource `BatVoltage` (0/47/11) counts in mV. Without the factor a
    healthy 3.0 V cell reports as 30 mV and every battery display in the
    web UI and in Loxone reads as flat.

    Fault to prove it: drop the `* 100`."""
    snapshot = build_snapshot(_sensor({(0x0001, 0x0020): 30}))
    assert snapshot.attributes["0/47/11"] == 3000


# ------------------------------------------------------------ device types --


def test_device_types_are_keyed_by_profile_and_type_together():
    """ZLL 0x0100 is a DIMMABLE light while ZHA and Matter 0x0100 is an
    ON/OFF light (R1 section 11). Keyed by device type alone, every IKEA and
    Hue lamp on the ZLL profile would be exported without a brightness.

    Fault to prove it: key the map by device type alone."""
    assert matter_device_type(ZLL_PROFILE, 0x0100) == 0x0101  # DimmableLight
    assert matter_device_type(ZHA_PROFILE, 0x0100) == 0x0100  # OnOffLight
    assert matter_device_type(ZLL_PROFILE, 0x0210) == 0x010D  # ExtendedColorLight
    assert matter_device_type(ZHA_PROFILE, 0x0107) == 0x0107  # OccupancySensor
    assert matter_device_type(0xDEAD, 0x0100) is None


@pytest.mark.parametrize(
    ("zone_type", "expected"),
    [(0x0015, 0x0015), (0x002A, 0x0043), (0x000D, 0x0107), (0x0028, 0x0076), (0x002B, 0x0076)],
)
def test_ias_devices_are_typed_by_their_zone_type(zone_type, expected):
    """An IAS device's ZCL device type says only "IAS Zone"; what KIND of
    sensor it is lives in `zone_type`.

    Fault to prove it: type them all as ContactSensor."""
    assert ias_matter_device_type(zone_type) == expected


def test_every_mapped_type_exists_in_the_matter_table():
    """The same guard `profiles/categories.py` has: every number this module
    produces must exist in `matter_server.client.models.device_types`, or
    `category_for` and `endpoint_labels` silently fall through to "other".

    NOTE ON A BRIEF MISMATCH: `tests/profiles/test_categories.py`'s own
    version of this guard does not scan `vars(matter_types)` for classes
    carrying a `device_type` attribute - it reads the module's own
    `ALL_TYPES` collection directly. That is what this test mirrors, per
    the brief's own instruction to copy the real access rather than its
    sketch.

    Fault to prove it: map a zone type to 0x9999."""
    from matter_server.client.models.device_types import ALL_TYPES

    from loxmatter.zigbee.translate import _all_mapped_matter_types

    unknown = sorted(hex(t) for t in _all_mapped_matter_types() if t not in ALL_TYPES)
    assert unknown == []


# -------------------------------------------------------------- IAS values --


def test_a_contact_sensor_inverts_the_alarm_because_matter_counts_closed():
    """Matter's BooleanState StateValue is TRUE when the contact is CLOSED;
    IAS alarm1 is TRUE when it is OPEN. The two are opposite, and a bridge
    that forwards the bit unchanged reports every door as exactly wrong.

    This polarity is taken from the Matter device library and the
    BooleanState data model, NOT measured here - design section 10.2 records
    how to settle it empirically on the test Pi with MYGGBETT, which is
    already commissioned. Until that is done this test pins the documented
    reading, and the report says so.

    Fault to prove it: drop the inversion."""
    assert _state_of(zone_type=0x0015, zone_status=0b00) is True  # closed
    assert _state_of(zone_type=0x0015, zone_status=0b01) is False  # open


def test_a_water_leak_sensor_does_not_invert():
    """TRUE means leak, on both sides.

    Fault to prove it: invert it like the contact sensor."""
    assert _state_of(zone_type=0x002A, zone_status=0b00) is False
    assert _state_of(zone_type=0x002A, zone_status=0b01) is True


def test_the_alarm_is_read_as_both_bits_not_just_the_first():
    """ZHA reads `value & 0b11`: Alarm_1 is bit 0 and Alarm_2 is bit 1, and
    some sensors only ever set Alarm_2.

    Fault to prove it: read bit 0 alone. An Alarm_2-only sensor then never
    reports anything at all, having paired and configured perfectly."""
    facts = _ias(zone_type=0x000D, zone_status=0b10)  # motion, Alarm_2 only
    assert build_snapshot(facts).attributes["1/1030/0"] == 1


def test_tamper_and_battery_bits_are_not_read_as_an_alarm():
    """Tamper is bit 2 and battery bit 3; neither is the sensor firing.

    Fault to prove it: read `value` truthily instead of `value & 0b11`."""
    facts = _ias(zone_type=0x000D, zone_status=0b1100)
    assert build_snapshot(facts).attributes["1/1030/0"] == 0


def test_an_unmapped_ias_zone_type_passes_the_raw_bitmap_through():
    """Fire, CO and vibration have no Matter cluster loxmatter understands,
    so the raw bitmap stays visible at 1280/2 as an expert signal rather
    than being dropped.

    Fault to prove it: drop unmapped zone types."""
    snapshot = build_snapshot(_ias(zone_type=0x002D, zone_status=0b01))
    assert snapshot.attributes["1/1280/2"] == 0b01
    assert "1/69/0" not in snapshot.attributes


# --------------------------------- the classic TRADFRI motion sensor (OnOff) --


def test_the_classic_tradfri_motion_sensor_reports_through_its_onoff_cluster():
    """This device has no IAS Zone cluster and no OccupancySensing cluster -
    it is the OnOff cluster's CLIENT, and sends `on`/`off` the way it would
    to a bound lamp (`zhaquirks/ikea/motion.py`, `motionzha.py`).
    `configure.py` writes what it sends into that cluster's own attribute
    cache at `(6, 0)`; this is the only place the value exists to be read
    from.

    Fault to prove it: read `(6, 0)` as an ordinary OnOff light instead."""
    assert build_snapshot(_onoff_sensor(value=True)).attributes["1/1030/0"] == 1
    assert build_snapshot(_onoff_sensor(value=False)).attributes["1/1030/0"] == 0


def test_the_classic_tradfri_motion_sensor_is_typed_as_an_occupancy_sensor():
    """Its ZCL device type says only `ON_OFF_SENSOR` (0x0850), which is also
    what an IKEA remote or switch declares - the model string, not the
    device type, is what tells this one apart (design 11's remotes-and-
    buttons non-goal is why device type alone must never make this call).

    Fault to prove it: type it from `(profile_id, device_type)` instead."""
    snapshot = build_snapshot(_onoff_sensor(value=True))
    assert snapshot.attributes["1/29/0"] == [{"0": 0x0107, "1": 1}]  # OccupancySensor


def test_the_classic_tradfri_motion_sensors_raw_onoff_attribute_is_not_also_exported():
    """The same physical fact must not land in Loxone twice under two
    names - one a plausible-looking "turn the motion sensor on" light
    control, which this device cannot honour at all.

    Fault to prove it: leave the generic passthrough's `(6, 0)` row in."""
    snapshot = build_snapshot(_onoff_sensor(value=True))
    assert "1/6/0" not in snapshot.attributes


def test_a_tradfri_motion_sensor_that_has_never_reported_writes_no_path():
    """The one rule `build_snapshot` never breaks: a signal with no value
    yet is left out entirely, not written as `None` or a guessed default.

    Fault to prove it: default to `False` when the attribute is absent."""
    snapshot = build_snapshot(_onoff_sensor(value=None))
    assert "1/1030/0" not in snapshot.attributes


def test_a_regular_onoff_light_is_not_mistaken_for_the_tradfri_motion_sensor():
    """The model string is IKEA's TRADFRI motion sensor's alone - a lamp
    with an ordinary in-cluster OnOff must keep reading as a light.

    Fault to prove it: key the whole rule off the OnOff cluster's presence
    rather than off the model string."""
    snapshot = build_snapshot(_lamp())
    assert snapshot.attributes["1/6/0"] is True
    assert "1/1030/0" not in snapshot.attributes


# -------------------------------------------------- accepted command lists --


def test_the_accepted_command_list_is_synthesised_from_clusters_and_capabilities():
    """Zigbee has no reliable equivalent - ZCL "Discover Commands Received"
    is optional and widely unimplemented - so the list is synthesised, and
    only with what `commands/translate.py` can actually build.

    Fault to prove it: always add 6 (hue/saturation). A lamp without the
    capability then gets a colour output that does nothing."""
    endpoint = EndpointFacts(
        endpoint=1,
        profile_id=ZLL_PROFILE,
        device_type=0x0210,
        in_cluster_ids=frozenset({0x0006, 0x0008, 0x0300}),
        attributes={(0x0300, 0x400A): 0x08},  # XY only
    )
    commands = accepted_commands(endpoint)

    assert commands[6] == [0, 1, 2]
    assert commands[8] == [0, 4]
    assert commands[768] == [7]


def test_the_colour_capability_bits_each_unlock_their_own_command():
    """0x01 hue/saturation -> 6, 0x08 XY -> 7, 0x10 colour temperature -> 10.

    Fault to prove it: treat any non-zero capability as all three."""

    def colour(capabilities):
        return accepted_commands(
            EndpointFacts(
                1, ZLL_PROFILE, 0x0210, frozenset({0x0300}), {(0x0300, 0x400A): capabilities}
            )
        )[768]

    assert colour(0x01) == [6]
    assert colour(0x08) == [7]
    assert colour(0x10) == [10]
    assert colour(0x19) == [6, 7, 10]


def test_a_readable_colour_temperature_unlocks_it_without_the_capability_bit():
    """Cheap lamps report no capabilities at all but do answer
    `color_temperature`.

    Fault to prove it: require the bit."""
    endpoint = EndpointFacts(1, ZLL_PROFILE, 0x0220, frozenset({0x0300}), {(0x0300, 0x0007): 370})
    assert accepted_commands(endpoint)[768] == [10]


def test_dangerous_clusters_never_become_outputs():
    """`ADMINISTRATIVE_CLUSTERS` in profiles/table.py is Matter-numbered and
    does not protect a Zigbee device. Basic (0x0000) carries
    `reset_to_factory_defaults`, and an output wired to it in Loxone would
    unpair the device on a button press.

    Fault to prove it: remove 0x0000 from the block list."""
    assert {0x0000, 0x0003, 0x0019, 0x1000} <= BLOCKED_CLUSTER_IDS
    endpoint = EndpointFacts(
        1, ZHA_PROFILE, 0x0100, frozenset({0x0006, 0x0000, 0x0019, 0x1000}), {}
    )
    assert set(accepted_commands(endpoint)) == {6}


def _motion_sensor_with_management_attributes() -> DeviceFacts:
    """A TRADFRI motion sensor's endpoint 1 the way the maintainer's Pi held
    it on 25 September 2026: the Basic attributes `configure.py` reads at
    join time (manufacturer 4, model 5, power source 7 = battery) and Poll
    Control's FastPollTimeout (3) next to the sensor reading itself."""
    return DeviceFacts(
        ieee="d0:cf:5e:ff:fe:71:a3:19",
        manufacturer="IKEA of Sweden",
        model=TRADFRI_MOTION_SENSOR_MODEL,
        is_mains_powered=False,
        available=True,
        quirk_applied=True,
        endpoints=(
            EndpointFacts(
                endpoint=1,
                profile_id=ZHA_PROFILE,
                device_type=0x0850,
                in_cluster_ids=frozenset({0x0000, 0x0001, 0x0003, 0x0020, 0x1000}),
                attributes={
                    (0x0000, 0x0004): "IKEA of Sweden",
                    (0x0000, 0x0005): TRADFRI_MOTION_SENSOR_MODEL,
                    (0x0000, 0x0007): 3,
                    (0x0020, 0x0003): 40,
                    (0x0006, 0x0000): True,
                },
            ),
        ),
    )


def test_management_clusters_never_become_signals():
    """Basic (0x0000) and Poll Control (0x0020) have no Matter counterpart -
    Matter numbers nothing 0 or 32 - so nothing downstream knows them, and
    `is_functional` treats an unknown cluster as wanted: the Basic power
    source reached the Loxone export as `c0_a7`, Poll Control's timeout as
    `c32_a3`. The block list that keeps these clusters out of the outputs
    has to keep them out of the inputs too.

    Fault to prove it: drop the block-list check from `_apply_endpoint`."""
    attributes = build_snapshot(_motion_sensor_with_management_attributes()).attributes
    assert not [path for path in attributes if path.split("/")[1] in {"0", "32"}]
    # and the sensor reading beside them still arrives
    assert attributes["1/1030/0"] == 1


def test_poll_control_is_on_the_block_list():
    """Poll Control is radio management: how often a sleeping device wakes
    up. Neither an input nor an output for Loxone.

    Fault to prove it: remove 0x0020 from the block list."""
    assert 0x0020 in BLOCKED_CLUSTER_IDS


# ------------------------------------------------------- argument renaming --


def test_command_arguments_are_renamed_from_a_table_not_by_a_rule():
    """Two of these do not follow the mechanical camelCase -> snake_case
    rule, and `colorTemperatureMireds` is the one that matters:
    zigpy calls it `color_temp_mireds`, not `color_temperature_mireds`.

    Fault to prove it: use a mechanical camel-to-snake helper. The colour
    temperature command then fails to build on every lamp."""
    renamed = rename_payload(
        768,
        10,
        {
            "colorTemperatureMireds": 370,
            "transitionTime": 0,
            "optionsMask": 1,
            "optionsOverride": 1,
        },
        allowed=frozenset(
            {"color_temp_mireds", "transition_time", "options_mask", "options_override"}
        ),
    )
    assert renamed == {
        "color_temp_mireds": 370,
        "transition_time": 0,
        "options_mask": 1,
        "options_override": 1,
    }


def test_move_to_level_with_on_off_carries_no_options_fields():
    """8/4 has no options fields in zigpy at all, while 8/0 has them as
    optional. Passing an unknown field silently is how a command turns into
    a parsing error on the wire, so the payload is validated against what
    the command really accepts and the rest is dropped deliberately.

    Fault to prove it: pass the payload through unfiltered."""
    renamed = rename_payload(
        8,
        4,
        {"level": 128, "transitionTime": 0, "optionsMask": 1},
        allowed=frozenset({"level", "transition_time"}),
    )
    assert renamed == {"level": 128, "transition_time": 0}


def test_a_field_the_command_does_not_know_is_never_invented():
    """Fault to prove it: keep unknown keys under their Matter names."""
    renamed = rename_payload(6, 1, {"somethingElse": 1}, allowed=frozenset())
    assert renamed == {}


# -------------------------------------------------------------- round trip --


def test_a_translated_lamp_decomposes_like_a_matter_lamp():
    """The boundary design's bet, checked end to end through the REAL
    downstream code: a snapshot this module builds must decompose through
    `extract_signals` and `extract_commands` exactly as a Matter snapshot
    does, with no Zigbee-shaped path surviving.

    This is what makes the rest of the suite meaningful - each test above
    checks one translation, this one checks that the result is genuinely the
    shape the middle expects rather than merely a dict.

    Fault to prove it: emit an attribute path as `<cluster>/<attribute>`
    without the endpoint. `parse_attribute_path` then rejects it, and
    `find_unparsable_paths` reports it."""
    from loxmatter.export.commands import extract_commands
    from loxmatter.matter.discovery import find_unparsable_paths

    snapshot = build_snapshot(_lamp({(0x0300, 0x400A): 0x19, (0x0300, 0x0007): 370}))

    assert find_unparsable_paths(snapshot) == []
    paths = {signal.path for signal in extract_signals(snapshot)}
    assert {"1/6/0", "1/8/0", "1/768/7"} <= paths
    slugs = {command.slug for command in extract_commands(snapshot)}
    assert {"on", "off", "toggle", "level_onoff", "colortemp", "color", "color_xy"} <= slugs


def test_a_zigbee_lamp_that_takes_colour_only_as_xy_gets_exactly_one_picker():
    """End to end from a Zigbee endpoint to the controls the UI draws, for
    the three shapes that matter:

    - 0x08, XY alone - the Candeo C-ZB-LC20 RGB controller's
      ColorCapabilities in zha-quirks (pinned against the installed library
      in `test_zigpy_names.py`): one colour control, `color_xy`, which sends
      MoveToColor, ZHA's own and only colour command. Design 5.6 and the
      change notes promised this; the first gate gave it nothing.
    - 0x18, XY|CT without HS - the white-spectrum shape, and also the Candeo
      RGBCCT controller's. The bits cannot tell the two apart, and the
      device type the Zigbee edge writes does: on the Extended Color Light
      this helper declares (ZLL 0x0210) it is one colour control,
      `color_xy`; on a Color Temperature Light (ZLL 0x0220) none; and with a
      device type the table does not know, the bits decide, which is none.
    - 0x19, HS|XY|CT on a Color Temperature Light: the bits declare colour,
      and the device type does not take it away - ONE colour control. For
      one build a 268 lamp lost its picker whatever its bits said.
    - 0x1F, every colour bit: ONE colour control, not two twins. Both 6 and
      7 are exported for Loxone, and `duplicate_control_command` keeps only
      `color` for the modal - the fix for an earlier bug on this branch.

    Fault to prove it: require `XY | HS` for (768, 7) alone again (the first
    list comes out empty), drop the CT exclusion (the untyped 0x18 lamp
    grows `color_xy`), drop the Extended Color Light rule (the typed 0x18
    lamp loses it), deny colour on a Color Temperature Light again (the 0x19
    lamp loses its picker), or empty `_INTERCHANGEABLE_CONTROL_COMMANDS` (the
    full-colour lamp has two)."""
    from dataclasses import replace

    from loxmatter.export.commands import extract_commands
    from loxmatter.profiles.table import command_control, duplicate_control_command

    def pickers(value: int, device_type: int = 0x0210) -> tuple[list[str], list[str]]:
        lamp = _lamp({(0x0300, 0x400A): value, (0x0300, 0x0003): 24939, (0x0300, 0x0004): 24701})
        endpoint = replace(lamp.endpoints[0], device_type=device_type)
        snapshot = build_snapshot(replace(lamp, endpoints=(endpoint,)))
        commands = [
            command
            for command in extract_commands(snapshot)
            if command_control(command.cluster_id, command.command_id) == "hue_sat"
        ]
        present = {(command.cluster_id, command.command_id) for command in commands}
        drawn = [
            command.slug
            for command in commands
            if not duplicate_control_command(command.cluster_id, command.command_id, present)
        ]
        return [command.slug for command in commands], drawn

    assert pickers(0x08) == (["color_xy"], ["color_xy"])
    assert pickers(0x18) == (["color_xy"], ["color_xy"])
    assert pickers(0x18, device_type=0x0220) == ([], [])
    assert pickers(0x18, device_type=0x7FFF) == ([], [])
    assert pickers(0x19, device_type=0x0220) == (["color", "color_xy"], ["color"])
    assert pickers(0x1F) == (["color", "color_xy"], ["color"])
