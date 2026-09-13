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

"""Zigbee facts in, a Matter-shaped `NodeSnapshot` out.

**Pure.** No zigpy import, no I/O, no clock. `source.py` reads zigpy and
fills `DeviceFacts`; everything decided here is decided from those
dataclasses alone. That is not tidiness: there is no Zigbee stick on the
test Pi (design section 10.3), so the fake-based tests against this module
are the only evidence this translation works, and they are only worth
anything if the thing they test has no hidden inputs.

The boundary design bet that Matter's data model is close enough to the
Zigbee Cluster Library that a Zigbee source can deliver the same
`NodeSnapshot` and everything downstream stays unchanged. For on/off, level,
hue, saturation, colour temperature, temperature and humidity that bet pays
in full: the attribute IDs, the scaling and the units are LITERALLY
IDENTICAL and nothing is converted. What it does not cover is what Zigbee
has and Matter does not, and that is what this module is:

- IAS Zone, which is one bitmap standing in for four different sensors,
  with a polarity that is inverted for one of them;
- ZLL device types, where 0x0100 means something else than it does in
  Matter;
- an `AcceptedCommandList`, which Zigbee has no reliable equivalent for;
- invalid-value sentinels, which are not measurements;
- a battery level that lives on the wrong endpoint.

**The one rule that outranks everything else here: never write a path whose
value is `None`.** See `build_snapshot`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.paths import ACCEPTED_COMMAND_LIST_ID
from loxmatter.profiles.relevance import (
    DESCRIPTOR_CLUSTER_ID,
    DEVICE_TYPE_LIST_ID,
    POWER_SOURCE_DEVICE_TYPE,
    ROOT_NODE_DEVICE_TYPE,
)

# --------------------------------------------------------------- facts in --


@dataclass(frozen=True)
class EndpointFacts:
    """What zigpy reports about one endpoint of a joined device."""

    endpoint: int
    profile_id: int
    device_type: int
    in_cluster_ids: frozenset[int]
    attributes: Mapping[tuple[int, int], object]


@dataclass(frozen=True)
class DeviceFacts:
    """What zigpy reports about a whole joined device."""

    ieee: str
    manufacturer: str
    model: str
    is_mains_powered: bool
    available: bool
    quirk_applied: bool
    endpoints: tuple[EndpointFacts, ...]


# --------------------------------------------------------- device type maps --

# (profile ID, device type) -> Matter device type. Keyed by the PAIR, never
# by device type alone: ZLL 0x0100 is a dimmable light while ZHA and Matter
# 0x0100 is an on/off light (design 5.3, R1 section 11) - keying by device
# type alone would export every ZLL dimmable lamp without a brightness.
#
# Numbers verified against the Zigbee Home Automation and Zigbee Light Link
# device-ID tables and cross-checked against
# `matter_server.client.models.device_types` by
# `test_every_mapped_type_exists_in_the_matter_table` (via
# `_all_mapped_matter_types` below).
_MATTER_DEVICE_TYPE_BY_PROFILE: dict[tuple[int, int], int] = {
    # ZHA (Zigbee Home Automation), profile 0x0104.
    (0x0104, 0x0000): 0x0103,  # On/Off Switch -> OnOffLightSwitch
    (0x0104, 0x0002): 0x010A,  # On/Off Output -> OnOffPlugInUnit
    (0x0104, 0x0009): 0x010A,  # Mains Power Outlet -> OnOffPlugInUnit
    (0x0104, 0x0100): 0x0100,  # On/Off Light -> OnOffLight
    (0x0104, 0x0101): 0x0101,  # Dimmable Light -> DimmableLight
    (0x0104, 0x0102): 0x010D,  # Color Dimmable Light -> ExtendedColorLight
    (0x0104, 0x0103): 0x0103,  # On/Off Light Switch -> OnOffLightSwitch
    (0x0104, 0x0104): 0x0104,  # Dimmer Switch -> DimmerSwitch
    (0x0104, 0x0105): 0x0105,  # Color Dimmer Switch -> ColorDimmerSwitch
    (0x0104, 0x0106): 0x0106,  # Light Sensor -> LightSensor
    (0x0104, 0x0107): 0x0107,  # Occupancy Sensor -> OccupancySensor
    (0x0104, 0x0302): 0x0302,  # Temperature Sensor -> TemperatureSensor
    # ZLL (Zigbee Light Link), profile 0xC05E. Its own device-ID numbering,
    # unrelated to ZHA's - the row below is the whole reason this map is
    # keyed by (profile, type) rather than type alone: ZLL 0x0100 is a
    # DIMMABLE light, not the on/off light ZHA and Matter mean by 0x0100.
    (0xC05E, 0x0000): 0x0100,  # On/Off Light -> OnOffLight
    (0xC05E, 0x0010): 0x010A,  # Smart Plug -> OnOffPlugInUnit
    (0xC05E, 0x0100): 0x0101,  # Dimmable Light -> DimmableLight (see above)
    (0xC05E, 0x0110): 0x010B,  # Dimmable Plug-in Unit -> DimmablePlugInUnit
    (0xC05E, 0x0200): 0x010D,  # Color Light -> ExtendedColorLight
    (0xC05E, 0x0210): 0x010D,  # Extended Color Light -> ExtendedColorLight
    (0xC05E, 0x0220): 0x010C,  # Color Temperature Light -> ColorTemperatureLight
}

# IAS Zone's own ZCL device type says only "IAS Zone"; which KIND of sensor
# it is lives in `zone_type` (design 5.3). Zone type -> Matter device type.
_MATTER_DEVICE_TYPE_BY_ZONE_TYPE: dict[int, int] = {
    0x0015: 0x0015,  # Contact switch -> ContactSensor
    0x002A: 0x0043,  # Water sensor -> WaterLeakDetector
    0x000D: 0x0107,  # Motion sensor (PIR) -> OccupancySensor
    0x0028: 0x0076,  # Fire sensor -> SmokeCoAlarm
    0x002B: 0x0076,  # Carbon monoxide sensor -> SmokeCoAlarm
}

# Which (cluster, attribute) IAS Zone's alarm bits are translated to, and
# whether the reading is inverted relative to IAS. Only the zone types with
# a Matter cluster this module understands appear here - fire, CO and
# vibration do not (see `_apply_ias_zone`). `_CLUSTER_BOOLEAN_STATE` and
# `_CLUSTER_OCCUPANCY_SENSING` are defined further below, next to the other
# cluster-number constants.
#
# DELIBERATE ASYMMETRY with `_MATTER_DEVICE_TYPE_BY_ZONE_TYPE` above: fire
# (0x0028) and CO (0x002B) are typed as SmokeCoAlarm there but have no row
# here, so such an endpoint declares SmokeCoAlarm while exporting only the
# raw `<ep>/1280/2` bitmap. That is the intended outcome, not an oversight.
# Matter's SmokeCoAlarm is a multi-attribute cluster (SmokeState,
# COState, ExpressedState, ...) whose enum values this module cannot honestly
# synthesise from two IAS alarm bits, and inventing them would be worse than
# the bitmap: the device type still gets the endpoint the right label, the
# right category and the right icon, and the bitmap stays visible as an
# expert signal an owner can wire up by hand. A faithful translation belongs
# with the SmokeCoAlarm cluster itself, not here.
_IAS_STATE_TARGETS: dict[int, tuple[int, int, bool]] = {
    # Matter's BooleanState.StateValue is TRUE when CLOSED; IAS alarm1 is
    # TRUE when OPEN - the two are opposite (design 5.2, pinned from the
    # Matter device library and BooleanState.xml, not measured - see
    # section 10.2 and the test docstring for the empirical follow-up).
    0x0015: (69, 0, True),  # Contact switch -> BooleanState, inverted
    0x002A: (69, 0, False),  # Water sensor -> BooleanState, not inverted
    0x000D: (1030, 0, False),  # Motion sensor -> OccupancySensing, not inverted
}


def _all_mapped_matter_types() -> frozenset[int]:
    """Every Matter device type number this module can produce.

    Its own inventory, read by `test_every_mapped_type_exists_in_the_matter_table`
    to guard against a typo turning into a silent "other" downstream."""
    return frozenset(_MATTER_DEVICE_TYPE_BY_PROFILE.values()) | frozenset(
        _MATTER_DEVICE_TYPE_BY_ZONE_TYPE.values()
    )


# `ADMINISTRATIVE_CLUSTERS` in `profiles/table.py` is Matter-numbered and
# does not protect a Zigbee device (design 5.4) - a Zigbee block list has to
# exist at this edge instead. Basic (0x0000) carries
# `reset_to_factory_defaults`; Identify (0x0003) is harmless but noise;
# OTA (0x0019) is firmware management, not a home-automation output; ZLL
# commissioning (0x1000) can move the device to a different network.
BLOCKED_CLUSTER_IDS: frozenset[int] = frozenset({0x0000, 0x0003, 0x0019, 0x1000})

# Invalid-value sentinels the ZCL uses to say "no reading", keyed by the
# Zigbee (cluster, attribute) that carries them. Passing these through would
# send -327.68 degrees, 655.35 %, 65535 lux, or a fully-charged 127.5 %
# battery into Loxone as if they were real measurements.
#
# Every constant here is the value the INSTALLED zigpy delivers for that
# attribute's declared type, not the bit pattern the ZCL specification
# prints - see the temperature row.
#
# WHICH rows belong here is decided by reach, not by which sensors came to
# mind: `_apply_endpoint` passes through every attribute of every
# non-blocked cluster, so a sentinel is needed wherever a nullable ZCL
# measurement can become a Loxone signal. The four rows added after the
# first audit were found that way, each type read off the installed zigpy
# before it was written down.
#
# A FLOAT-typed measurement never belongs in this table, however reachable
# it is: the ZCL's invalid value for those is NaN, and this table compares
# with `==`. `_is_not_a_number` above is that rule's home instead - read its
# docstring before adding a row for a concentration cluster.
#
# `_NO_SENTINEL` is a private marker object, not `None`: `dict.get()` on a
# pair with no entry here already answers `None`, and a naive
# `_SENTINELS.get(key) == value` would then coincidentally agree with the
# separate "value is None" rule below for exactly those pairs - hiding a
# fault that drops the None check on its own (found by fault injection
# while proving `test_a_path_whose_value_is_none_is_left_out_entirely`: a
# missing "value is None" check for an attribute with no sentinel entry
# still passed, for this reason, until this marker was added).
_NO_SENTINEL: object = object()


def _is_not_a_number(value: object) -> bool:
    """The invalid-value sentinel of every FLOAT-typed ZCL measurement.

    `_SENTINELS` cannot express this one, and no row added to it ever
    could: that table compares with `==`, and `float("nan") == float("nan")`
    is `False` by definition, so a NaN entry could never match anything.
    It needs its own arm beside the `value is None` rule.

    **This reaches far more clusters than it looks.** 32 clusters in the
    installed zigpy 2.2.0 declare `measured_value` / `min_measured_value` /
    `max_measured_value` as `Single` - every concentration-measurement
    cluster the ZCL defines, among them `PM25` (0x042A),
    `CarbonDioxideConcentration` (0x040D), `CarbonMonoxideConcentration`
    (0x040C) and `FormaldehydeConcentration` (0x042B). None of them is in
    `BLOCKED_CLUSTER_IDS`, so `_apply_endpoint` passes every one of their
    attributes straight out by number, and an air-quality sensor with no
    reading yet would write `nan` into a Loxone virtual input - a value
    nothing downstream recognises as "no reading" and nothing can compare
    against either.

    Reachable with real hardware, not only in theory: zha-quirks 2.2.2
    declares these clusters in `zhaquirks/ikea/starkvind.py`,
    `zhaquirks/xiaomi/aqara/airm_fhac01.py`, `zhaquirks/tuya/tuya_co.py`
    and `zhaquirks/tuya/builder/__init__.py`.

    `isinstance(value, float)` first, because `math.isnan` raises
    `TypeError` on the bytes, strings and lists that also pass through
    here. zigpy's `Single` is a `float` subclass (verified against the
    installed 2.2.0), so the values this module actually receives are
    covered.
    """
    return isinstance(value, float) and math.isnan(value)


_SENTINELS: dict[tuple[int, int], int] = {
    # NEGATIVE, not 0x8000. The ZCL writes this sentinel as the bit pattern
    # 0x8000, but `TemperatureMeasurement.measured_value` is zigpy's
    # `int16s` (verified against zigpy 2.2.0: `int16s.min_value == -32768`,
    # `int16s.deserialize(b"\x00\x80") == -32768`), so what zigpy hands this
    # module is -32768. +32768 is not a value `int16s` can hold at all -
    # `int16s(0x8000)` raises - so a `0x8000` row here can never match, and
    # a sensor with no reading would publish -327.68 degrees into Loxone as
    # a genuine measurement. Every other row below is an UNSIGNED attribute,
    # where the bit pattern and the value coincide.
    (0x0402, 0x0000): -0x8000,  # TemperatureMeasurement.MeasuredValue, invalid (int16s)
    (0x0405, 0x0000): 0xFFFF,  # RelativeHumidity.MeasuredValue, invalid (uint16_t)
    (0x0400, 0x0000): 0xFFFF,  # IlluminanceMeasurement.MeasuredValue, invalid (uint16_t)
    (0x0001, 0x0021): 0xFF,  # PowerConfiguration.BatteryPercentageRemaining, unknown (uint8_t)
    (0x0001, 0x0020): 0xFF,  # PowerConfiguration.BatteryVoltage, unknown (uint8_t)
    # The SAME trap as the temperature row, one cluster over, and on the
    # device class this project explicitly targets. `Thermostat.local_temperature`
    # is `int16s` in the installed zigpy (verified, not assumed), and Matter's
    # Thermostat is cluster 0x0201 attribute 0x0000 as well - `LocalTemperature`,
    # nullable, verified against the installed `chip.clusters` - so the value
    # passes straight through this module and out to Loxone. A TRV that has
    # not measured yet reports -32768, which is -327.68 degrees in somebody's
    # living room.
    (0x0201, 0x0000): -0x8000,  # Thermostat.LocalTemperature, invalid (int16s)
    # `Thermostat.outdoor_temperature`, same type, same sentinel, same Matter
    # number (0x0201/0x0001, `OutdoorTemperature`, nullable). Included with
    # the row above rather than after the next TRV arrives: the argument for
    # one is the argument for the other, word for word.
    (0x0201, 0x0001): -0x8000,  # Thermostat.OutdoorTemperature, invalid (int16s)
    # `PressureMeasurement.measured_value` is `int16s` too - the third signed
    # row, and the reason this table cannot be skimmed for `0xFFFF`.
    (0x0403, 0x0000): -0x8000,  # PressureMeasurement.MeasuredValue, invalid (int16s)
    # These two are `uint16_t` in the installed zigpy, so here the bit
    # pattern and the value do coincide. Flow lines up with Matter exactly
    # (0x0404/0x0000, `MeasuredValue`, nullable); soil moisture does NOT -
    # Matter numbers it 0x0430, so 0x0408 reaches no Matter device type at
    # all. It reaches a Loxone signal either way, because `_apply_endpoint`
    # passes every non-blocked attribute through by number, and that is
    # precisely why the row is needed: nothing downstream would recognise
    # 65535 as "no reading".
    (0x0404, 0x0000): 0xFFFF,  # FlowMeasurement.MeasuredValue, invalid (uint16_t)
    (0x0408, 0x0000): 0xFFFF,  # SoilMoisture.MeasuredValue, invalid (uint16_t)
    #
    # ElectricalMeasurement (0x0B04). Not in `BLOCKED_CLUSTER_IDS`, so every
    # attribute of it reaches a Loxone signal by number - and an energy
    # monitoring plug is one of the device classes this bridge exists for,
    # which makes these the highest-stakes rows in the table after the
    # temperature ones. Each type read off the INSTALLED zigpy 2.2.0
    # (`zigpy.zcl.clusters.homeautomation.ElectricalMeasurement`), not off
    # the specification: `rms_voltage.type.max_value == 65535`,
    # `active_power.type.min_value == -32768`.
    (0x0B04, 0x0505): 0xFFFF,  # ElectricalMeasurement.RMSVoltage, unavailable (uint16_t)
    (0x0B04, 0x0508): 0xFFFF,  # ElectricalMeasurement.RMSCurrent, unavailable (uint16_t)
    # SIGNED, like the temperature rows: `active_power` is `int16s`, so what
    # zigpy hands over is -32768 and never +32768. Without this row a plug
    # that has not measured yet publishes -327.68 watts.
    (0x0B04, 0x050B): -0x8000,  # ElectricalMeasurement.ActivePower, unavailable (int16s)
    #
    # `min_measured_value` / `max_measured_value` (0x0001 / 0x0002) on the
    # measurement clusters. The same "not defined" sentinel as each
    # cluster's own `measured_value` above and, being static capability
    # values rather than readings, exported once and then never corrected -
    # so a sensor that leaves them undefined would show an implausible
    # bound in Loxone forever. Signed where the cluster's own measurement is
    # signed, unsigned where it is unsigned; each checked against the
    # installed zigpy rather than assumed to follow the measured value's
    # type. `IlluminanceMeasurement` is deliberately absent - see below.
    (0x0402, 0x0001): -0x8000,  # TemperatureMeasurement.MinMeasuredValue (int16s)
    (0x0402, 0x0002): -0x8000,  # TemperatureMeasurement.MaxMeasuredValue (int16s)
    (0x0403, 0x0001): -0x8000,  # PressureMeasurement.MinMeasuredValue (int16s)
    (0x0403, 0x0002): -0x8000,  # PressureMeasurement.MaxMeasuredValue (int16s)
    (0x0405, 0x0001): 0xFFFF,  # RelativeHumidity.MinMeasuredValue (uint16_t)
    (0x0405, 0x0002): 0xFFFF,  # RelativeHumidity.MaxMeasuredValue (uint16_t)
    (0x0404, 0x0001): 0xFFFF,  # FlowMeasurement.MinMeasuredValue (uint16_t)
    (0x0404, 0x0002): 0xFFFF,  # FlowMeasurement.MaxMeasuredValue (uint16_t)
    (0x0408, 0x0001): 0xFFFF,  # SoilMoisture.MinMeasuredValue (uint16_t)
    (0x0408, 0x0002): 0xFFFF,  # SoilMoisture.MaxMeasuredValue (uint16_t)
    #
    # Examined and deliberately NOT added, so the next audit does not spend
    # the time again:
    #
    # - `Thermostat` setpoints (0x0011, 0x0012, 0x0013, 0x0014) and the
    #   heat/cool limits. `int16s`, but NOT nullable in the ZCL: -32768 is a
    #   legal, if absurd, configured value there and has no "invalid"
    #   meaning. Suppressing it would hide a real misconfiguration.
    # - `Thermostat.local_temperature_calibration` (0x0010) is `int8s`, whose
    #   whole range is legal - there is no sentinel to write.
    # - `PressureMeasurement.scaled_value` (0x0010) is nullable `int16s` as
    #   well, but loxmatter never reads the companion `scale` (0x0014) that
    #   makes it mean anything, so a row for it would suppress a value this
    #   bridge does not publish in the first place.
    # - `IlluminanceMeasurement.min_measured_value` / `max_measured_value`
    #   (0x0400/0x0001, 0x0400/0x0002). `uint16_t` in the installed zigpy, so
    #   an 0xFFFF row would LOOK like its five siblings above - and would be
    #   dead code. Illuminance is the one measurement cluster whose bounds
    #   are not marked undefined with the all-ones pattern: the ZCL reserves
    #   0x0000 for that here, because the scale is logarithmic and the top of
    #   the range is a legal reading. Writing 0x0000 instead would be a
    #   specification value nothing in the installed tree can confirm - zigpy
    #   carries the type and no range at all - and this table's own rule is
    #   that every constant in it is read off the library. Left out until a
    #   real lux sensor shows which it sends. The cost is one implausible
    #   bound on a signal zigpy's interview does not read in the first place.
    # - The rest of `ElectricalMeasurement` - roughly 120 more attributes.
    #   Examined as a block rather than one at a time, because they fall into
    #   three kinds and none of them is a nullable reading this bridge
    #   publishes today. The multipliers and divisors (0x02xx, 0x04xx, 0x06xx)
    #   are scaling configuration with no "unavailable" value. The alarm
    #   thresholds and overload limits (0x07xx, 0x08xx) are configured values,
    #   excluded for the same reason as the thermostat setpoints above:
    #   suppressing one would hide a real misconfiguration. The recorded
    #   extremes (`rms_voltage_min`/`_max`, `active_power_min`/`_max`, ...)
    #   and the phase B and C mirrors (0x09xx, 0x0Axx) do carry the same
    #   sentinels as the three rows added above, but only a three-phase meter
    #   reports the mirrors at all, and nothing in this project has yet seen
    #   a device send any of them. Adding forty rows by pattern would make
    #   this table unreadable without making one device correct that is not
    #   correct already; the three attributes a single-phase energy plug
    #   actually reports are the three that are written down.
}

# A table, not a camelCase -> snake_case helper (design 5.7): two of these
# do not follow the mechanical rule, and getting `colorTemperatureMireds`
# wrong breaks colour temperature on every lamp.
_ARGUMENT_NAMES: dict[str, str] = {
    # zigpy's ColorControl.move_to_color_temp names this field
    # `color_temp_mireds` - NOT the mechanical `color_temperature_mireds`.
    # This is the one entry a computed rule gets wrong, and the reason this
    # is a table rather than a rule.
    "colorTemperatureMireds": "color_temp_mireds",
    "transitionTime": "transition_time",
    "optionsMask": "options_mask",
    "optionsOverride": "options_override",
    "level": "level",
    "hue": "hue",
    "saturation": "saturation",
    # MoveToColor (XY): zigpy's own field names already match the
    # mechanical transform for these two - kept in the table anyway, so
    # there is exactly one source of truth for every argument name rather
    # than a rule for "most" fields and a table for the exceptions.
    "colorX": "color_x",
    "colorY": "color_y",
}

# --------------------------------------------- Zigbee cluster/attribute IDs --
# used specially below. Everything else passes through unchanged: the
# cluster and attribute numbers are, per the module docstring, literally
# identical between Zigbee and Matter for on/off, level, colour, temperature,
# humidity, illuminance and (real) occupancy.

_CLUSTER_ONOFF = 6
_CLUSTER_LEVEL = 8
_CLUSTER_COLOR_CONTROL = 768
_CLUSTER_POWER_CONFIGURATION = 1  # Zigbee "Power Configuration" - battery
_CLUSTER_IAS_ZONE = 0x0500  # 1280 decimal - Zigbee-only, no Matter equivalent
_CLUSTER_BOOLEAN_STATE = 69  # Matter BooleanState - IAS edge target
_CLUSTER_OCCUPANCY_SENSING = 1030  # Matter/Zigbee OccupancySensing - IAS edge target

_ATTRIBUTE_BATTERY_PERCENTAGE = 0x0021  # BatteryPercentageRemaining, half percent
_ATTRIBUTE_BATTERY_VOLTAGE = 0x0020  # BatteryVoltage, 100 mV units
_ATTRIBUTE_ZONE_TYPE = 0x0001
_ATTRIBUTE_ZONE_STATUS = 0x0002
_ATTRIBUTE_COLOR_TEMPERATURE_MIREDS = 0x0007
_ATTRIBUTE_COLOR_CAPABILITIES = 0x400A  # Same numeric ID as Matter's ColorCapabilities

# The battery percentage lives on its own endpoint in Zigbee
# (PowerConfiguration, cluster 1); Matter expects it on the synthesised root
# endpoint's PowerSource cluster (design 5.1/5.2) - "edge" in location, "="
# in unit (both count in half a percent).
_BATTERY_PERCENTAGE_PATH = "0/47/12"
_BATTERY_VOLTAGE_PATH = "0/47/11"

# The three BasicInformation paths `NodeSnapshot.from_raw` reads for Matter
# (see `matter/models.py`); endpoint 0 is free to use for these because in
# Zigbee it is the ZDO endpoint and carries no ZCL clusters.
_VENDOR_NAME_PATH = "0/40/1"
_PRODUCT_NAME_PATH = "0/40/3"
_UNIQUE_ID_PATH = "0/40/18"

# ZHA reads the alarm as `value & 0b11`, not bit 0 alone: Alarm_1 is bit 0
# and Alarm_2 is bit 1, and some sensors (e.g. some motion sensors) only
# ever set Alarm_2. Tamper (bit 2) and Battery (bit 3) are not the alarm.
_IAS_ALARM_MASK = 0b11

# ColorCapabilities/FeatureMap bits this module reads directly (design 5.4).
# These serve `accepted_commands`' OWN synthesis rule, which is simpler than
# (and separate from) `profiles.capabilities.COMMAND_FEATURE_RULES`: the
# downstream gate in `export.commands` still applies on top of whatever is
# written here, exactly as it does for a Matter device.
_COLOUR_CAPABILITY_HUE_SATURATION = 0x01
_COLOUR_CAPABILITY_XY = 0x08
_COLOUR_CAPABILITY_COLOR_TEMPERATURE = 0x10


def _as_plain_int(value: object) -> int | None:
    """`value` as a plain `int`, or `None` if it is not one.

    `bool` is a subclass of `int` in Python and is excluded explicitly - the
    same guard `profiles.capabilities.declared_features` uses - so a stray
    `True` is never mistaken for the integer 1 in a bitmap or capability
    field."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


# ------------------------------------------------------------- device type --


def matter_device_type(profile_id: int, device_type: int) -> int | None:
    """The Matter device type for a (profile, device type) pair, or `None`
    if this module does not know it."""
    return _MATTER_DEVICE_TYPE_BY_PROFILE.get((profile_id, device_type))


def ias_matter_device_type(zone_type: int) -> int | None:
    """The Matter device type an IAS Zone's `zone_type` stands for, or
    `None` if this module has no Matter cluster for it (fire, CO,
    vibration, ...)."""
    return _MATTER_DEVICE_TYPE_BY_ZONE_TYPE.get(zone_type)


# --------------------------------------------------------- accepted commands --


def accepted_commands(endpoint: EndpointFacts) -> dict[int, list[int]]:
    """The `AcceptedCommandList` this endpoint would carry on Matter.

    Zigbee has no reliable equivalent (ZCL "Discover Commands Received" is
    optional and widely unimplemented), so this is synthesised from the
    endpoint's in-clusters and, for colour, its declared capabilities - and
    only with what `commands/translate.py` can actually build: On/Off,
    LevelControl and the three ColorControl commands that module knows.
    `export.commands.extract_commands` drops everything else anyway outside
    raw mode, so synthesising more here would be synthesising noise.

    Dangerous clusters (`BLOCKED_CLUSTER_IDS`) never contribute a command,
    even though the Matter-numbered `ADMINISTRATIVE_CLUSTERS` guard in
    `profiles/table.py` would not have caught them here."""
    clusters = endpoint.in_cluster_ids - BLOCKED_CLUSTER_IDS
    commands: dict[int, list[int]] = {}

    if _CLUSTER_ONOFF in clusters:
        commands[_CLUSTER_ONOFF] = [0, 1, 2]  # Off, On, Toggle

    if _CLUSTER_LEVEL in clusters:
        commands[_CLUSTER_LEVEL] = [0, 4]  # MoveToLevel, MoveToLevelWithOnOff

    if _CLUSTER_COLOR_CONTROL in clusters:
        capabilities = (
            _as_plain_int(
                endpoint.attributes.get((_CLUSTER_COLOR_CONTROL, _ATTRIBUTE_COLOR_CAPABILITIES))
            )
            or 0
        )
        color_commands: list[int] = []
        if capabilities & _COLOUR_CAPABILITY_HUE_SATURATION:
            color_commands.append(6)  # MoveToHueAndSaturation
        if capabilities & _COLOUR_CAPABILITY_XY:
            color_commands.append(7)  # MoveToColor
        readable_color_temperature = (
            _CLUSTER_COLOR_CONTROL,
            _ATTRIBUTE_COLOR_TEMPERATURE_MIREDS,
        ) in endpoint.attributes
        if capabilities & _COLOUR_CAPABILITY_COLOR_TEMPERATURE or readable_color_temperature:
            color_commands.append(10)  # MoveToColorTemperature
        commands[_CLUSTER_COLOR_CONTROL] = color_commands

    return commands


# ------------------------------------------------------------- argument names --


def rename_payload(
    cluster_id: int, command_id: int, payload: Mapping[str, object], allowed: frozenset[str]
) -> dict[str, object]:
    """Matter-named command arguments translated to zigpy's own field names.

    `cluster_id` and `command_id` are part of the interface for symmetry
    with the rest of this module, but the renaming itself never branches on
    them: `allowed` already carries the per-command knowledge (in
    `ZigbeeSource`, the real zigpy command's own schema field names), so a
    field the table cannot rename, or one this specific command does not
    declare, is dropped rather than invented - never passed through
    unfiltered, and never renamed by guesswork."""
    del cluster_id, command_id
    renamed: dict[str, object] = {}
    for key, value in payload.items():
        target = _ARGUMENT_NAMES.get(key)
        if target is not None and target in allowed:
            renamed[target] = value
    return renamed


# ------------------------------------------------------------------ IAS zone --


def _endpoint_matter_device_type(endpoint: EndpointFacts) -> int | None:
    """The device type this endpoint's `<ep>/29/0` should carry.

    An IAS Zone endpoint is typed by its `zone_type`, never by the
    endpoint's own (profile, device type) pair, which says only "IAS Zone"
    (design 5.3)."""
    if _CLUSTER_IAS_ZONE in endpoint.in_cluster_ids:
        zone_type = _as_plain_int(
            endpoint.attributes.get((_CLUSTER_IAS_ZONE, _ATTRIBUTE_ZONE_TYPE))
        )
        return None if zone_type is None else ias_matter_device_type(zone_type)
    return matter_device_type(endpoint.profile_id, endpoint.device_type)


def _apply_ias_zone(endpoint: EndpointFacts, attributes: dict[str, object]) -> None:
    """Writes what an IAS Zone endpoint's alarm bits translate to.

    Contact and water-leak sensors land on BooleanState, motion on
    OccupancySensing (`_IAS_STATE_TARGETS`); fire, CO and vibration have no
    Matter cluster this module understands, so the raw bitmap stays visible
    at `<ep>/1280/2` as an expert signal rather than being silently
    dropped."""
    if _CLUSTER_IAS_ZONE not in endpoint.in_cluster_ids:
        return

    zone_type = _as_plain_int(endpoint.attributes.get((_CLUSTER_IAS_ZONE, _ATTRIBUTE_ZONE_TYPE)))
    zone_status = _as_plain_int(
        endpoint.attributes.get((_CLUSTER_IAS_ZONE, _ATTRIBUTE_ZONE_STATUS))
    )
    if zone_type is None or zone_status is None:
        return

    target = _IAS_STATE_TARGETS.get(zone_type)
    if target is None:
        attributes[f"{endpoint.endpoint}/{_CLUSTER_IAS_ZONE}/{_ATTRIBUTE_ZONE_STATUS}"] = (
            zone_status
        )
        return

    cluster_id, attribute_id, invert = target
    alarm = bool(zone_status & _IAS_ALARM_MASK)
    value: bool | int
    if cluster_id == _CLUSTER_BOOLEAN_STATE:
        value = (not alarm) if invert else alarm
    else:
        value = int(alarm)
    attributes[f"{endpoint.endpoint}/{cluster_id}/{attribute_id}"] = value


# --------------------------------------------------------------- the snapshot --


def _device_type_list_path(endpoint: int) -> str:
    return f"{endpoint}/{DESCRIPTOR_CLUSTER_ID}/{DEVICE_TYPE_LIST_ID}"


def _root_endpoint_device_types(facts: DeviceFacts) -> list[dict[str, int]]:
    """`0/29/0`: the root node always, plus PowerSource for battery devices
    so that `is_functional`'s layer 2 treats endpoint 0 exactly like a
    Matter root endpoint and `endpoint_labels` calls it "Device"."""
    types = [{"0": ROOT_NODE_DEVICE_TYPE, "1": 1}]
    if not facts.is_mains_powered:
        types.append({"0": POWER_SOURCE_DEVICE_TYPE, "1": 1})
    return types


def _apply_endpoint(endpoint: EndpointFacts, attributes: dict[str, object]) -> None:
    device_type = _endpoint_matter_device_type(endpoint)
    if device_type is not None:
        attributes[_device_type_list_path(endpoint.endpoint)] = [{"0": device_type, "1": 1}]

    for (cluster_id, attribute_id), value in endpoint.attributes.items():
        if (
            value is None
            or _is_not_a_number(value)
            or _SENTINELS.get((cluster_id, attribute_id), _NO_SENTINEL) == value
        ):
            continue

        if cluster_id == _CLUSTER_IAS_ZONE:
            continue  # handled once per endpoint by `_apply_ias_zone` below

        if cluster_id == _CLUSTER_POWER_CONFIGURATION:
            if attribute_id == _ATTRIBUTE_BATTERY_PERCENTAGE:
                attributes[_BATTERY_PERCENTAGE_PATH] = value
            elif attribute_id == _ATTRIBUTE_BATTERY_VOLTAGE:
                voltage = _as_plain_int(value)
                if voltage is not None:
                    attributes[_BATTERY_VOLTAGE_PATH] = voltage * 100
            continue

        # Everything else: the Zigbee and Matter cluster/attribute numbers
        # are literally identical (module docstring) - nothing to convert.
        attributes[f"{endpoint.endpoint}/{cluster_id}/{attribute_id}"] = value

    _apply_ias_zone(endpoint, attributes)

    for cluster_id, command_ids in accepted_commands(endpoint).items():
        attributes[f"{endpoint.endpoint}/{cluster_id}/{ACCEPTED_COMMAND_LIST_ID}"] = command_ids


def build_snapshot(facts: DeviceFacts) -> NodeSnapshot:
    """The device as loxmatter's Matter-shaped middle expects it.

    **Never writes a path whose value is `None`.** `Store.register_signals`
    computes `exported` only when the row is CREATED, and a path whose value
    is `None` classifies as `Exportability.NONE` - so a signal first seen
    without a value stays unexported forever, until somebody toggles it by
    hand. A sensor that paired, configured and went green, whose value never
    reaches Loxone and whose row cannot heal itself, is the single most
    likely bug report this feature can produce.

    So a value that is missing, or is one of the ZCL's invalid-value
    sentinels, means the path is LEFT OUT. When the first real value
    arrives, `ZigbeeSource.follow` hands a fresh snapshot to
    `Runtime.on_node_snapshot`, which calls `register_signals`, invalidates
    the signal index and seeds the value - creating the row properly, with
    `exported` computed from a value that exists.
    """
    attributes: dict[str, object] = {
        _device_type_list_path(0): _root_endpoint_device_types(facts),
        _VENDOR_NAME_PATH: facts.manufacturer,
        _PRODUCT_NAME_PATH: facts.model,
        _UNIQUE_ID_PATH: facts.ieee,
    }

    for endpoint in facts.endpoints:
        _apply_endpoint(endpoint, attributes)

    return NodeSnapshot(
        technology="zigbee",
        address=facts.ieee,
        vendor_name=facts.manufacturer,
        product_name=facts.model,
        unique_id=facts.ieee,
        attributes=attributes,
        available=facts.available,
    )
