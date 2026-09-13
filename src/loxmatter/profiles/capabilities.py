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

"""What a cluster says it can do, and which commands that permits.

`AcceptedCommandList` says which commands a device will not reject. It does
NOT say the device can carry them out in any way a person would recognise -
and for ColorControl the difference is visible on hardware standing in this
flat. The IKEA KAJPLATS white-spectrum lamp
(`tests/fixtures/nodes/ikea_kajplats_ws_lamp.json`, device type 268, Color
Temperature Light) lists command 7 (MoveToColor) in its AcceptedCommandList
and declares the XY feature - but it has two white channels and no colour
ones. Everything it is sent lands on its white locus.

Until command 7 got an entry in `clusters.yaml` (2026-09-12) that did not
matter: `export.commands.extract_commands` filters on
`profiles.table.command_slug`, and an unnamed command falls out there. With
the entry it stopped falling out, and the lamp grew a colour picker. This
module is the check that was missing - the same shape as
`matter.discovery.FEATURE_MAP_EVENTS`: cluster knowledge as a table of bit
rules, not as a branch inside the algorithm.

**The bit each command needs.** ColorControl carries its abilities in two
attributes with the same bit layout: `FeatureMap` (0xFFFC), which every
Matter device has, and `ColorCapabilities` (0x400A), which ZCL had first and
which is therefore the one a Zigbee lamp will bring (a Zigbee cluster has no
FeatureMap at all). Both are read, in that order, so the same rule serves
both worlds.

- (768, 6) MoveToHueAndSaturation sends `hue`/`saturation` and needs HS.
- (768, 7) MoveToColor sends `colorX`/`colorY` and needs XY - **plus one
  of two things that tell a colour lamp from a white one**: the HS bit, or
  the absence of the CT bit.

  XY on its own does not mean "can show a colour". A tunable-white lamp
  declares it because xy is also how a controller hands it a *white point*;
  the KAJPLATS WS above declares exactly XY|CT and is not a colour lamp.
  The control this project builds on top of the command is a full-gamut
  colour picker (`control: hue_sat` in `clusters.yaml`), so offering it to
  that lamp draws colours its two white channels cannot show.

  A lamp that declares XY and NOT CT, however, is not a white-spectrum
  lamp - a lamp that tunes white declares CT - so XY is how it takes
  colour, and the only way. zha-quirks ships exactly such a device:
  `zhaquirks.candeo.CandeoRGBColorCluster` pins ColorCapabilities to
  `XY_attributes` alone for the Candeo C-ZB-LC20 RGB controllers. The first
  version of this table asked for XY|HS and gave that controller no colour
  control at all, while the design and the change notes promised colour on
  lamps that only accept XY (corrected 13 September 2026).

  What such a lamp does not have is CurrentHue/CurrentSaturation, which is
  where the picker reads its start position from; the modal then shows its
  "start value unknown" note, the same one every control shows for a value
  it cannot read. That costs the marker, not the colour.

**The device type settles what the bits cannot.** A colour lamp declaring
XY|CT without HS is bit-for-bit the white-spectrum lamp -
`zhaquirks.candeo.CandeoRGBCCTColorCluster` (XY_attributes +
Color_temperature) is a real one - and the bits alone denied it with the
KAJPLATS. The endpoint's device type separates them, and it sits in the
same snapshot (`<ep>/29/0`, written natively by Matter and by the Zigbee
edge from `zigbee/translate.py`'s table, with the same numbers):

- **Extended Color Light (269)** is a colour lamp, so for (768, 7) the XY
  bit is enough, CT or not. It gains command 7 and nothing else - command
  6 still needs HS - so such a lamp gets exactly one colour control.
- **Color Temperature Light (268)** is a white lamp, and gets neither
  colour command whatever its bits claim. The project's standing asymmetry
  decides that direction (see `api/control.py`, `_WRITABLE_ATTRIBUTES`): a
  wrongly locked control costs one missing option, a wrongly released one
  misbehaves on hardware. An endpoint that lists 269 as well is judged as
  269.
- **No device type** leaves the bits to decide, exactly as before.

**A device that declares nothing is denied.** Neither attribute present means
the snapshot makes no claim, and a gate that reads silence as consent is not
a gate. The price is the same one missing control option; the alternative is
the regression above, reintroduced for every device whose snapshot happens to
be short one attribute.

Only ColorControl has entries here. A command with no entry is not gated at
all - On/Off and LevelControl carry no feature that could contradict them.
"""

from __future__ import annotations

from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.paths import FEATURE_MAP_ID
from loxmatter.profiles.relevance import device_types_by_endpoint

COLOR_CONTROL_CLUSTER = 768

# ColorControl's feature bits. Layout per the Matter Application Cluster
# Specification, and identical in `ColorCapabilities` - checked against the
# installed SDK (chip.clusters.Objects.ColorControl.Bitmaps.Feature and
# .ColorCapabilitiesBitmap) and against both checked-in lamps: the CWS
# reports 31 = HS|EHUE|CL|XY|CT in 1/768/65532 and 1/768/16394, the WS
# reports 24 = XY|CT in both.
COLOUR_FEATURE_HUE_SATURATION = 0x01
COLOUR_FEATURE_ENHANCED_HUE = 0x02
COLOUR_FEATURE_COLOR_LOOP = 0x04
COLOUR_FEATURE_XY = 0x08
COLOUR_FEATURE_COLOR_TEMPERATURE = 0x10

# ColorCapabilities (0x400A). ZCL's own name for the same bits; a Zigbee lamp
# has this and no FeatureMap.
COLOR_CAPABILITIES_ID = 0x400A

# Which attributes may answer "what can this cluster do", most authoritative
# first. A cluster missing from this mapping has no feature source and
# therefore no gated commands either.
_FEATURE_SOURCES: dict[int, tuple[int, ...]] = {
    COLOR_CONTROL_CLUSTER: (FEATURE_MAP_ID, COLOR_CAPABILITIES_ID),
}

# (cluster ID, command ID) -> the rules that permit it, as
# `(required, excluded)` bit pairs. A command is permitted when ANY one rule
# holds: every `required` bit declared and no `excluded` bit declared. See
# the module docstring for the two rules of (768, 7).
COMMAND_FEATURE_RULES: dict[tuple[int, int], tuple[tuple[int, int], ...]] = {
    (COLOR_CONTROL_CLUSTER, 6): ((COLOUR_FEATURE_HUE_SATURATION, 0),),
    (COLOR_CONTROL_CLUSTER, 7): (
        # A colour lamp that also takes hue/saturation, white channels or not.
        (COLOUR_FEATURE_XY | COLOUR_FEATURE_HUE_SATURATION, 0),
        # A colour lamp whose only colour command is xy: no CT, so not a
        # white-spectrum lamp.
        (COLOUR_FEATURE_XY, COLOUR_FEATURE_COLOR_TEMPERATURE),
    ),
}


# The two Matter device types that say which kind of colour lamp an endpoint
# is, numbered as in `matter_server.client.models.device_types`
# (ColorTemperatureLight, ExtendedColorLight). See the module docstring.
COLOR_TEMPERATURE_LIGHT = 0x010C
EXTENDED_COLOR_LIGHT = 0x010D

# Commands a Color Temperature Light is denied whatever its bits say, and the
# bits an Extended Color Light needs for a command instead of its rules.
_WHITE_LAMP_DENIED: frozenset[tuple[int, int]] = frozenset(
    {(COLOR_CONTROL_CLUSTER, 6), (COLOR_CONTROL_CLUSTER, 7)}
)
_COLOUR_LAMP_RULES: dict[tuple[int, int], int] = {
    (COLOR_CONTROL_CLUSTER, 7): COLOUR_FEATURE_XY,
}


def declared_features(snapshot: NodeSnapshot, endpoint: int, cluster_id: int) -> int | None:
    """The feature bits this endpoint's cluster declares, or None.

    None means the snapshot carries neither source attribute - not that the
    device can do nothing. The two are told apart here so that the caller can
    decide; `command_needs_missing_feature` treats them the same on purpose
    (see there).

    `bool` is excluded explicitly for the same reason as in
    `profiles.transport.network_features_of`: it is a subclass of `int` in
    Python, and `True` would otherwise read as "hue/saturation".

    **A source that declares nothing does not end the search** (review
    finding Minor, 12 September 2026). The first version took the first
    source PRESENT, so a device reporting `FeatureMap = 0` alongside
    `ColorCapabilities = 31` was denied on the strength of the zero. Matter
    requires the two attributes to carry the same bits, so any disagreement
    is non-conformance; a zero is the one shape of it that can be read
    charitably, because "this cluster has no features at all" is not a claim
    a working ColorControl makes - it is what an unpopulated attribute looks
    like.

    Deliberately NOT an OR across the sources, which is the other way to
    reach the same fix. OR would let `ColorCapabilities` ADD a bit to a
    `FeatureMap` that declares its own, non-zero set - the WS lamp's real
    `FeatureMap = 24` plus a sloppy `ColorCapabilities = 31` would then
    produce exactly the colour picker this module exists to withhold. The
    ordering in `_FEATURE_SOURCES` says FeatureMap is the more authoritative
    of the two, and skipping past a zero keeps that ordering meaning
    something, while OR would make it dead weight. The project's standing
    asymmetry decides the tie: a wrongly locked control costs one missing
    option, a wrongly released one misbehaves on hardware.
    """
    fallback: int | None = None
    for attribute_id in _FEATURE_SOURCES.get(cluster_id, ()):
        value = snapshot.attributes.get(f"{endpoint}/{cluster_id}/{attribute_id}")
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if value:
            return value
        if fallback is None:
            # Remembered rather than discarded: every source saying zero is
            # still a declaration of no features, and must stay
            # distinguishable from no source at all.
            fallback = value
    return fallback


def command_needs_missing_feature(
    snapshot: NodeSnapshot, endpoint: int, cluster_id: int, command_id: int
) -> bool:
    """Whether this command asks for a feature the endpoint does not declare.

    False for every command with no entry in `COMMAND_FEATURE_RULES` - the
    gate is an exception list, not a second permission list on top of
    `clusters.yaml`.

    True when no rule holds AND when no source attribute is present at all.
    Silence is not consent: a snapshot that says nothing about ColorControl's
    abilities cannot be the reason a colour picker appears.
    """
    rules = COMMAND_FEATURE_RULES.get((cluster_id, command_id))
    if rules is None:
        return False
    features = declared_features(snapshot, endpoint, cluster_id)
    if features is None:
        return True
    device_types = device_types_by_endpoint(snapshot).get(endpoint, frozenset())
    if EXTENDED_COLOR_LIGHT in device_types:
        required = _COLOUR_LAMP_RULES.get((cluster_id, command_id))
        if required is not None and (features & required) == required:
            return False
    elif COLOR_TEMPERATURE_LIGHT in device_types and (cluster_id, command_id) in _WHITE_LAMP_DENIED:
        return True
    return not any(
        (features & required) == required and not features & excluded
        for required, excluded in rules
    )
