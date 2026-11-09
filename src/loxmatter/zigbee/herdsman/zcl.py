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

"""Stage 1 on plain ZCL attributes: the library's `numeric`, `enumLookup`
and `binary` extends, as data (design 2026-11-09, section 5).

Each describes one attribute - cluster, attribute ID, ZCL type, perhaps a
manufacturer code - and how its value becomes the property and back. The
frames themselves are parsed here rather than taken from zigpy's cluster
events, because the attribute is often one zigpy's cluster does not
define: a manufacturer attribute zigpy decodes as raw bytes, or not at
all."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from zigpy.zcl import foundation

from loxmatter.zigbee.herdsman.catalogue import Attribute


@dataclass(frozen=True)
class AttributeValue:
    """One attribute value a device reported or answered with."""

    endpoint: int
    cluster: int
    attribute: int
    manufacturer_code: int | None
    value: Any


def parse_attribute_frame(endpoint: int, cluster: int, data: bytes) -> list[AttributeValue]:
    """The attribute values in a ZCL frame: an attribute report or a read
    response, from the device. Anything else - a cluster command, a frame
    to the device, one that does not parse - holds none."""
    try:
        header, rest = foundation.ZCLHeader.deserialize(data)
    except (ValueError, KeyError):
        return []
    if not header.frame_control.is_general:
        return []
    if header.frame_control.direction != foundation.Direction.Server_to_Client:
        return []
    manufacturer = header.manufacturer if header.frame_control.is_manufacturer_specific else None
    try:
        if header.command_id == foundation.GeneralCommand.Report_Attributes:
            schema = foundation.GENERAL_COMMANDS[foundation.GeneralCommand.Report_Attributes].schema
            report: Any = schema.deserialize(rest)[0]
            records = [(r.attrid, r.value.value) for r in report.attribute_reports]
        elif header.command_id == foundation.GeneralCommand.Read_Attributes_rsp:
            schema = foundation.GENERAL_COMMANDS[
                foundation.GeneralCommand.Read_Attributes_rsp
            ].schema
            response: Any = schema.deserialize(rest)[0]
            records = [
                (r.attrid, r.value.value)
                for r in response.status_records
                if r.status == foundation.Status.SUCCESS and r.value is not None
            ]
        else:
            return []
    except (ValueError, KeyError):
        return []
    return [
        AttributeValue(endpoint, cluster, int(attrid), manufacturer, _plain(value))
        for attrid, value in records
    ]


def _plain(value: Any) -> Any:
    """A zigpy value as the library's `msg.data` holds it: numbers, booleans,
    strings, bytes."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return value


def matches(attribute: Attribute, value: AttributeValue) -> bool:
    """Whether a reported value is this attribute's. An attribute bound to an
    endpoint takes only that endpoint's; one without takes any - the
    library's converter does not ask either."""
    if value.cluster != attribute.cluster or value.attribute != attribute.attribute:
        return False
    return attribute.endpoint is None or value.endpoint == attribute.endpoint


def _precision_round(value: float, precision: int) -> float:
    factor = float(10**precision)
    return math.floor(value * factor + 0.5) / factor


def _tidy(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def from_device(attribute: Attribute, raw: Any) -> Any:
    """The property value for an attribute's raw value; `None` when the
    library's converter would publish nothing."""
    if attribute.kind == "numeric":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        value = float(raw)
        if attribute.scale is not None:
            value = value / float(attribute.scale)
        if attribute.precision is not None:
            value = _precision_round(value, int(attribute.precision))
        return _tidy(value)
    if attribute.kind == "enumLookup":
        for name, number in attribute.lookup:
            if not isinstance(raw, bool) and raw == number:
                return name
        return None
    if attribute.kind == "binary":
        if attribute.value_on is not None and raw == attribute.value_on[1]:
            return attribute.value_on[0]
        if attribute.value_off is not None and raw == attribute.value_off[1]:
            return attribute.value_off[0]
        return None
    return None


def to_device(attribute: Attribute, value: Any) -> int | float:
    """The raw attribute value to write for a property value. Raises
    `ValueError` for a value the attribute does not take."""
    if attribute.kind == "numeric":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{attribute.property} takes a number")
        number = float(value)
        if attribute.scale is not None:
            number = number * float(attribute.scale)
        if attribute.precision is not None:
            number = _precision_round(number, int(attribute.precision))
        return _tidy(number)
    if attribute.kind == "enumLookup":
        for name, number in attribute.lookup:
            if name == value:
                return int(number)
        raise ValueError(f"{value!r} is not a value of {attribute.property}")
    if attribute.kind == "binary":
        for pair in (attribute.value_on, attribute.value_off):
            if pair is not None and pair[0] == value:
                return pair[1]  # type: ignore[no-any-return]
        raise ValueError(f"{value!r} is not a value of {attribute.property}")
    raise ValueError(f"unknown attribute kind {attribute.kind!r}")


def typed_value(type_id: int, raw: float) -> foundation.TypeValue:
    """A raw value as the ZCL type the library writes it with."""
    data_type = foundation.DataType.from_type_id(type_id)  # type: ignore[arg-type]
    if data_type.name in ("semi", "single", "double"):
        value = data_type.python_type(float(raw))
    else:
        value = data_type.python_type(round(raw))
    return foundation.TypeValue(type=data_type.type_id, value=value)
