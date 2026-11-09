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

"""Stage 1's codecs without a device: Tuya data points, ZCL attributes and
the mapping from a library property to a snapshot path and an option
(design 2026-11-09, sections 8.1 to 8.4). Every expected value here is the
one zigbee-herdsman-converters 26.128.0 computes for the same input."""

from __future__ import annotations

import math

import pytest

from loxmatter.device_config import from_raw, to_raw
from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles.table import (
    VENDOR_PROPERTIES_CLUSTER,
    lookup,
    marked_non_functional,
    vendor_property_ids,
)
from loxmatter.zigbee.herdsman import properties, tuya, zcl
from loxmatter.zigbee.herdsman.catalogue import Attribute, Expose

# ----------------------------------------------------------------- Tuya --


def test_a_report_parses_into_its_data_points() -> None:
    payload = bytes.fromhex("03006f020004000000d701010001016604000102")
    seq, values = tuya.parse_dp_values(payload)
    assert seq == 3
    assert values == [
        tuya.DpValue(0x6F, tuya.TYPE_VALUE, bytes.fromhex("000000d7")),
        tuya.DpValue(0x01, tuya.TYPE_BOOL, b"\x01"),
        tuya.DpValue(0x66, tuya.TYPE_ENUM, b"\x02"),
    ]
    assert [tuya.data_value(v) for v in values] == [215, True, 2]


def test_a_truncated_data_point_ends_the_list_as_herdsman_does() -> None:
    _seq, values = tuya.parse_dp_values(bytes.fromhex("010001010001010202000400"))
    assert values == [tuya.DpValue(1, tuya.TYPE_BOOL, b"\x01")]


def test_what_is_sent_encodes_as_tz_datapoints_does() -> None:
    assert tuya.encode_dp_values(1, [tuya.dp_value(102, 30)]).hex() == "0100660200040000001e"
    assert tuya.dp_value(108, True) == tuya.DpValue(108, tuya.TYPE_BOOL, b"\x01")
    assert tuya.dp_value(4, tuya.Enum(2)) == tuya.DpValue(4, tuya.TYPE_ENUM, b"\x02")
    # A negative number is sent as its two's complement, a scaled one rounded.
    assert tuya.dp_value(9, -15).data == bytes.fromhex("fffffff1")
    assert tuya.dp_value(9, 1.12 * 100).data == bytes.fromhex("00000070")


@pytest.mark.parametrize(
    ("converter", "wire", "value"),
    [
        ({"kind": "raw"}, 48, 48),
        ({"kind": "divideBy", "factor": 10}, 215, 21.5),
        ({"kind": "divideBy", "factor": 10}, 210, 21),
        ({"kind": "multiplyBy", "factor": 10}, 23, 230),
        ({"kind": "scale", "range": [0, 254, 0, 1000]}, 500, 127),
        ({"kind": "twosComplement", "bits": 8, "scale": 10}, 0xF1, -1.5),
        ({"kind": "localTemperatureCalibration"}, 4086, -10),
        ({"kind": "localTemperatureCalibration_256"}, 250, -6),
        ({"kind": "divideByWithLimits", "factor": 10, "min": 5, "max": 35}, 400, 35),
        ({"kind": "trueFalseInvert"}, True, False),
        ({"kind": "lookup", "map": [["ON", True], ["OFF", False]], "fallback": None}, True, "ON"),
        (
            {"kind": "lookup", "map": [["low", {"enum": 0}], ["high", {"enum": 1}]]},
            1,
            "high",
        ),
    ],
)
def test_converters_run_both_ways(
    converter: dict[str, object], wire: object, value: object
) -> None:
    assert tuya.from_device(converter, wire) == value
    if converter["kind"] in ("divideByWithLimits",):
        return  # clamps, so not its own inverse
    back = tuya.to_device(converter, value)
    plain = back.value if isinstance(back, tuya.Enum) else back
    if converter["kind"] == "scale":
        return  # rounds both ways
    assert plain == wire or (isinstance(plain, float) and math.isclose(plain, float(wire)))  # type: ignore[arg-type]


def test_true_false_compares_strictly_like_javascript() -> None:
    assert tuya.from_device({"kind": "trueFalse", "value": 1}, 1) is True
    assert tuya.from_device({"kind": "trueFalse", "value": {"enum": 0}}, 0) is True
    # `true === 1` is false in JavaScript.
    assert tuya.from_device({"kind": "trueFalse", "value": 1}, True) is False
    assert not tuya.can_write({"kind": "trueFalse", "value": 1})


def test_an_unknown_lookup_value_is_refused_unless_there_is_a_fallback() -> None:
    with pytest.raises(tuya.ConverterError):
        tuya.from_device({"kind": "lookup", "map": [["a", 0]], "fallback": None}, 5)
    assert tuya.from_device({"kind": "lookup", "map": [["a", 0]], "fallback": "a"}, 5) == "a"


# ------------------------------------------------------------------- ZCL --


def test_an_attribute_report_parses_with_zigpys_codec() -> None:
    # The ZG-204Z report of design section 9.2.
    values = zcl.parse_attribute_frame(1, 0x0500, bytes.fromhex("18050a01f0200213002001"))
    assert [(v.attribute, v.value) for v in values] == [(0xF001, 2), (0x0013, 1)]
    # A frame TO the device holds nothing the library reads.
    assert zcl.parse_attribute_frame(1, 0x0500, bytes.fromhex("10070201f02001")) == []


def _attribute(**changes: object) -> Attribute:
    fields: dict[str, object] = {
        "kind": "numeric",
        "property": "x",
        "endpoint": None,
        "cluster": 0x0402,
        "attribute": 0,
        "type": 0x29,
        "manufacturer_code": None,
        "access": 7,
    }
    fields.update(changes)
    return Attribute(**fields)  # type: ignore[arg-type]


def test_numeric_attributes_scale_and_round_as_the_library_does() -> None:
    power = _attribute(scale=1000, precision=2)
    assert zcl.from_device(power, 12345) == 12.35
    assert zcl.to_device(power, 1.5) == 1500
    assert zcl.from_device(power, "x") is None


def test_enum_and_binary_attributes_look_their_values_up() -> None:
    target = _attribute(kind="enumLookup", lookup=(("None", 0), ("Moving", 1)))
    assert zcl.from_device(target, 1) == "Moving"
    assert zcl.to_device(target, "None") == 0
    switch = _attribute(kind="binary", value_on=("enable", 1), value_off=("disable", 0))
    assert zcl.from_device(switch, 0) == "disable"
    assert zcl.to_device(switch, "enable") == 1
    with pytest.raises(ValueError):
        zcl.to_device(switch, "maybe")


def test_a_written_value_carries_the_librarys_zcl_type() -> None:
    assert zcl.typed_value(0x20, 3).serialize().hex() == "2003"
    assert zcl.typed_value(0x29, -5).serialize().hex() == "29fbff"
    assert zcl.typed_value(0x30, 2).serialize().hex() == "3002"


# ------------------------------------------------------------ properties --


def _expose(**changes: object) -> Expose:
    data: dict[str, object] = {"name": "x", "property": "x", "type": "numeric", "access": 7}
    data.update(changes)
    return Expose.from_json(data)


def test_a_property_matter_means_lands_on_its_matter_path() -> None:
    temperature = _expose(name="temperature", property="temperature", unit="°C", access=1)
    assert properties.place(temperature, 21.5, endpoint=1) == properties.Placement(
        "1/1026/0", 2150, 0x0302
    )
    presence = _expose(name="presence", property="presence", type="binary", value_on=True)
    assert properties.place(presence, True, endpoint=1) == properties.Placement(
        "1/1030/0", 1, 0x0107
    )
    battery = _expose(name="battery", property="battery", unit="%", access=1)
    assert properties.place(battery, 80, endpoint=1).path == "0/47/12"  # type: ignore[union-attr]


def test_voltage_goes_where_its_unit_says() -> None:
    cell = _expose(name="voltage", property="voltage", unit="mV", access=1)
    socket = _expose(name="voltage", property="voltage", unit="V", access=1)
    assert properties.place(cell, 3000, endpoint=1) == properties.Placement("0/47/11", 3000)
    assert properties.place(socket, 230.5, endpoint=2) == properties.Placement("2/144/4", 230500)


def test_every_other_property_lands_on_the_vendor_cluster_under_its_number() -> None:
    keep_time = _expose(name="keep_time", property="keep_time", type="enum", values=[30, 60, 120])
    placed = properties.place(keep_time, 120, endpoint=1)
    number = vendor_property_ids()["keep_time"]
    assert placed == properties.Placement(f"1/{VENDOR_PROPERTIES_CLUSTER}/{number}", 120.0)
    sensitivity = _expose(
        name="sensitivity", property="sensitivity", type="enum", values=["low", "medium", "high"]
    )
    assert properties.place(sensitivity, "medium", endpoint=1).value == 1.0  # type: ignore[union-attr]
    assert properties.place(sensitivity, "unknown", endpoint=1) is None


def test_an_option_and_its_reading_share_one_scale() -> None:
    """The value the snapshot shows for a setting is exactly what
    `from_raw` makes of the raw value the option writes - so the web UI's
    input, Loxone's virtual input and the queued write all agree."""
    cases = [
        (_expose(name="keep_time", property="keep_time", type="enum", values=[30, 60, 120]), 60),
        (
            _expose(
                name="indicator",
                property="indicator",
                type="binary",
                value_on="ON",
                value_off="OFF",
            ),
            "OFF",
        ),
        (
            _expose(
                name="temperature_calibration",
                property="temperature_calibration",
                value_min=-2,
                value_max=2,
                value_step=0.1,
            ),
            -1.5,
        ),
        (_expose(name="fading_time", property="fading_time", value_min=0, value_max=28800), 30),
    ]
    for expose, value in cases:
        option = properties.option_for(expose, endpoint=1)
        assert option is not None, expose.name
        shown = properties.loxone_value(expose, value)
        assert shown is not None
        raw = to_raw(option, shown)
        assert from_raw(option, raw) == pytest.approx(shown)
        assert properties.library_value(expose, raw) == value, expose.name


def test_what_a_light_or_switch_is_operated_by_is_never_a_setting() -> None:
    state = _expose(name="state", property="state", type="binary", value_on="ON", value_off="OFF")
    assert properties.option_for(state, endpoint=1) is None
    read_only = _expose(name="fading_time", property="fading_time", access=1)
    assert properties.option_for(read_only, endpoint=1) is None


# ------------------------------------------------------- property numbers --

# A handful of numbers as the first build gave them. `vendor_properties.json`
# is only ever appended to: a property whose number moved would move its
# signal's path and with it its Loxone key, and every output wired to it
# would silently stop working.
_PINNED = {"Bar": 1, "abs_max_heat_setpoint_limit": 2, "keep_time": 1901}


def test_property_numbers_never_move() -> None:
    numbers = vendor_property_ids()
    for name, number in _PINNED.items():
        assert numbers[name] == number, name
    assert len(set(numbers.values())) == len(numbers)
    assert sorted(numbers.values()) == list(range(1, len(numbers) + 1))


def test_the_profile_table_names_the_library_properties() -> None:
    numbers = vendor_property_ids()
    keep_time = SignalRef(1, VENDOR_PROPERTIES_CLUSTER, numbers["keep_time"], SignalKind.ATTRIBUTE)
    assert lookup(keep_time, 120.0).slug == "keep_time"
    assert not marked_non_functional(keep_time)
    # One the library files as a setting on most devices: kept out of the
    # default export, readable on demand.
    interval = SignalRef(
        1, VENDOR_PROPERTIES_CLUSTER, numbers["absence_circling_interval"], SignalKind.ATTRIBUTE
    )
    assert marked_non_functional(interval)
