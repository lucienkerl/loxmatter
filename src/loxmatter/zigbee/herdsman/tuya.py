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

"""Tuya data points: the wire format of cluster 0xEF00 and the library's
value converters, as data (design 2026-11-09, section 6).

A Tuya device that speaks only through 0xEF00 puts every reading and
setting into one command: a sequence number, then any number of data
points, each `dp (1) type (1) length (2, big-endian) data`. The library
describes a device by a table of `[dp, property, converter]`; the build
step writes the converters it recognises as `{"kind": ...}` descriptors,
and this module runs them in both directions, exactly as `lib/tuya.js`
does. A converter it does not recognise makes the definition an engine
definition at build time, so none reaches this module."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

TUYA_CLUSTER = 0xEF00

# Commands the device sends to the coordinator: the data points it reports.
DATA_RESPONSE = 0x01
DATA_REPORT = 0x02
ACTIVE_STATUS_REPORT_ALT = 0x05
ACTIVE_STATUS_REPORT = 0x06
DATA_REPORT_ALT = 0x0A
REPORT_COMMANDS = frozenset(
    {DATA_RESPONSE, DATA_REPORT, ACTIVE_STATUS_REPORT_ALT, ACTIVE_STATUS_REPORT, DATA_REPORT_ALT}
)

# Commands the coordinator sends.
DATA_REQUEST = 0x00
DATA_QUERY = 0x03
SEND_DATA = 0x04
SEND_COMMANDS = {"dataRequest": DATA_REQUEST, "sendData": SEND_DATA}

# Data point types.
TYPE_RAW = 0
TYPE_BOOL = 1
TYPE_VALUE = 2
TYPE_STRING = 3
TYPE_ENUM = 4
TYPE_BITMAP = 5


class ConverterError(ValueError):
    """A value a converter cannot take - the library throws in the same place."""


@dataclass(frozen=True)
class Enum:
    """`lib/tuya.js`'s `Enum`: a number sent as an enum data point."""

    value: int


@dataclass(frozen=True)
class Bitmap:
    """`lib/tuya.js`'s `Bitmap`: a number sent as a bitmap data point."""

    value: int


@dataclass(frozen=True)
class DpValue:
    dp: int
    type: int
    data: bytes


def parse_dp_values(payload: bytes) -> tuple[int, list[DpValue]]:
    """A report's payload (after the ZCL header): its sequence number and
    its data points. Stops at a truncated data point, as herdsman's
    `readListTuyaDataPointValues` does."""
    if len(payload) < 2:
        raise ValueError("Tuya payload shorter than its sequence number")
    seq = int.from_bytes(payload[0:2], "little")
    values: list[DpValue] = []
    offset = 2
    while offset + 4 <= len(payload):
        dp, kind = payload[offset], payload[offset + 1]
        length = int.from_bytes(payload[offset + 2 : offset + 4], "big")
        data = payload[offset + 4 : offset + 4 + length]
        if len(data) < length:
            break
        values.append(DpValue(dp, kind, bytes(data)))
        offset += 4 + length
    return seq, values


def encode_dp_values(seq: int, values: Sequence[DpValue]) -> bytes:
    out = bytearray((seq & 0xFFFF).to_bytes(2, "little"))
    for value in values:
        out += bytes((value.dp, value.type)) + len(value.data).to_bytes(2, "big") + value.data
    return bytes(out)


def data_value(value: DpValue) -> Any:
    """`getDataValue`: a data point's value as the converters receive it."""
    if value.type == TYPE_RAW:
        return value.data
    if value.type == TYPE_BOOL:
        return bool(value.data) and value.data[0] == 1
    if value.type in (TYPE_VALUE, TYPE_BITMAP):
        # `convertBufferToNumber`: an unsigned big-endian number.
        return int.from_bytes(value.data, "big") if value.data else 0
    if value.type == TYPE_STRING:
        return "".join(chr(b) for b in value.data)
    if value.type == TYPE_ENUM:
        return value.data[0] if value.data else 0
    return None


def dp_value(dp: int, value: Any) -> DpValue:
    """A converted value as the data point `tz.datapoints` sends for it."""
    if isinstance(value, bool):
        return DpValue(dp, TYPE_BOOL, bytes((1 if value else 0,)))
    if isinstance(value, Enum):
        return DpValue(dp, TYPE_ENUM, bytes((value.value & 0xFF,)))
    if isinstance(value, Bitmap):
        return DpValue(dp, TYPE_BITMAP, bytes((value.value & 0xFF,)))
    if isinstance(value, (int, float)):
        number = _js_round(float(value))
        if number < 0:
            number += 0x1_0000_0000
        return DpValue(dp, TYPE_VALUE, (number & 0xFFFF_FFFF).to_bytes(4, "big"))
    if isinstance(value, str):
        return DpValue(dp, TYPE_STRING, bytes(ord(c) & 0xFF for c in value))
    if isinstance(value, (bytes, bytearray, list)):
        return DpValue(dp, TYPE_RAW, bytes(value))
    raise ConverterError(f"cannot send a value of type {type(value).__name__}")


def _js_round(value: float) -> int:
    """`Math.round`: halves round up, also below zero."""
    return math.floor(value + 0.5)


def _lookup_value(raw: Any) -> Any:
    """A value of a lookup map, as the build step wrote it."""
    if isinstance(raw, Mapping):
        if "enum" in raw:
            return Enum(int(raw["enum"]))
        if "bitmap" in raw:
            return Bitmap(int(raw["bitmap"]))
    return raw


def _plain(value: Any) -> Any:
    """`.valueOf()`."""
    if isinstance(value, (Enum, Bitmap)):
        return value.value
    return value


def _same(a: Any, b: Any) -> bool:
    """`===` between a data point value and a map value: no bool/number mixing."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    return bool(a == b)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConverterError(f"{value!r} is not a number")
    return float(value)


def _tidy(value: float) -> int | float:
    """A JavaScript number as Python spells it: 12.0 is 12."""
    return int(value) if float(value).is_integer() else value


def _map_range(value: float, from_min: float, from_max: float, to_min: float, to_max: float) -> int:
    """`utils.mapNumberRange`, which rounds."""
    mapped = to_min + ((value - from_min) / (from_max - from_min)) * (to_max - to_min)
    return _js_round(mapped)


def from_device(converter: Mapping[str, Any], value: Any) -> Any:
    """A data point value to the library's property value (`from`)."""
    kind = converter.get("kind")
    if kind == "raw":
        return value
    if kind == "lookup":
        for name, mapped in converter.get("map") or ():
            if _same(_plain(_lookup_value(mapped)), value):
                return name
        if converter.get("fallback") is not None:
            return converter["fallback"]
        raise ConverterError(f"value {value!r} is not in the lookup")
    if kind == "divideBy":
        return _tidy(_number(value) / float(converter["factor"]))
    if kind == "multiplyBy":
        return _tidy(_number(value) * float(converter["factor"]))
    if kind == "divideByFromOnly":
        return _tidy(_number(value) / float(converter["factor"]))
    if kind == "divideByWithLimits":
        factor, low, high = (
            float(converter["factor"]),
            float(converter["min"]),
            float(converter["max"]),
        )
        scaled = _number(value) / factor
        return _tidy(high if scaled > high else max(scaled, low))
    if kind == "scale":
        a, b, c, d = (float(x) for x in converter["range"])
        return _map_range(_number(value), c, d, a, b)
    if kind == "twosComplement":
        bits, scale = int(converter["bits"]), float(converter.get("scale") or 1)
        number = _number(value)
        signed = number - 2**bits if number >= 2 ** (bits - 1) else number
        return _tidy(signed / scale)
    if kind == "trueFalse":
        return _same(value, _plain(_lookup_value(converter.get("value"))))
    if kind == "trueFalseInvert":
        return not value
    if kind == "fault":
        return bool(value)
    if kind == "localTemperatureCalibration":
        number = _number(value)
        return _tidy(number - 4096 if number > 4000 else number)
    if kind == "localTemperatureCalibration_256":
        number = _number(value)
        return _tidy(number - 256 if number > 200 else number)
    raise ConverterError(f"unknown converter {kind!r}")


# Converters with no `to`: the library cannot write their property.
_READ_ONLY = frozenset({"divideByFromOnly", "trueFalse", "fault"})


def can_write(converter: Mapping[str, Any]) -> bool:
    return converter.get("kind") not in _READ_ONLY


def to_device(converter: Mapping[str, Any], value: Any) -> Any:
    """A property value to what goes on the wire (`to`)."""
    kind = converter.get("kind")
    if kind == "raw":
        return value
    if kind == "lookup":
        for name, mapped in converter.get("map") or ():
            if name == value:
                return _lookup_value(mapped)
        raise ConverterError(f"value {value!r} is not in the lookup")
    if kind == "divideBy":
        return _number(value) * float(converter["factor"])
    if kind == "multiplyBy":
        return _number(value) / float(converter["factor"])
    if kind == "divideByFromOnly":
        return value
    if kind == "divideByWithLimits":
        factor, low, high = (
            float(converter["factor"]),
            float(converter["min"]),
            float(converter["max"]),
        )
        number = _number(value)
        return high * factor if number > high else low * factor if number < low else number * factor
    if kind == "scale":
        a, b, c, d = (float(x) for x in converter["range"])
        return _map_range(_number(value), a, b, c, d)
    if kind == "twosComplement":
        bits, scale = int(converter["bits"]), float(converter.get("scale") or 1)
        scaled = _number(value) * scale
        return scaled + 2**bits if scaled < 0 else scaled
    if kind == "trueFalseInvert":
        return not value
    if kind == "localTemperatureCalibration":
        number = _number(value)
        return 4096 + number if number < 0 else number
    if kind == "localTemperatureCalibration_256":
        number = _number(value)
        return 256 + number if number < 0 else number
    raise ConverterError(f"converter {kind!r} cannot write")
