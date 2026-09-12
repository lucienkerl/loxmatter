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
- (768, 7) MoveToColor sends `colorX`/`colorY` and needs XY - **and HS as
  well.** That second half goes beyond what the payload strictly requires
  and is the deliberate part of this table, so here is the reasoning in
  full:

  XY on its own does not mean "can show a colour". A tunable-white lamp
  declares it because xy is also how a controller hands it a *white point*;
  the KAJPLATS WS above declares exactly XY|CT and is not a colour lamp. The
  control this project builds on top of the command is not an xy field, it
  is a full-gamut colour picker (`control: hue_sat` in `clusters.yaml`,
  the colour area in `web/index.html`), and that picker reads the lamp's
  position back from CurrentHue (768/0) and CurrentSaturation (768/1) -
  attributes a lamp without the HS feature does not have. On the KAJPLATS WS
  the picker therefore cannot even show where the light is, and
  `readStartValues`' `colormode` never becomes 0, so the modal never opens on
  the colour tab. A control whose read-back is structurally absent is not a
  control.

  The cost is a colour lamp that declares XY but no HS: it loses the picker.
  No such device exists in this repository, on the shelf, or in the fixture
  set, while the lamp harmed by the other choice does - and the project's
  standing asymmetry (see `api/control.py`, `_WRITABLE_ATTRIBUTES`) is that a
  wrongly locked control costs one missing option while a wrongly released
  one misbehaves on real hardware. When such a lamp turns up, this table is
  the one line to change.

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

# (cluster ID, command ID) -> the feature bits that must ALL be declared.
# See the module docstring for why (768, 7) asks for HS on top of XY.
COMMAND_REQUIRED_FEATURES: dict[tuple[int, int], int] = {
    (COLOR_CONTROL_CLUSTER, 6): COLOUR_FEATURE_HUE_SATURATION,
    (COLOR_CONTROL_CLUSTER, 7): COLOUR_FEATURE_XY | COLOUR_FEATURE_HUE_SATURATION,
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
    """
    for attribute_id in _FEATURE_SOURCES.get(cluster_id, ()):
        value = snapshot.attributes.get(f"{endpoint}/{cluster_id}/{attribute_id}")
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        return value
    return None


def command_needs_missing_feature(
    snapshot: NodeSnapshot, endpoint: int, cluster_id: int, command_id: int
) -> bool:
    """Whether this command asks for a feature the endpoint does not declare.

    False for every command with no entry in `COMMAND_REQUIRED_FEATURES` -
    the gate is an exception list, not a second permission list on top of
    `clusters.yaml`.

    True when the required bits are missing AND when no source attribute is
    present at all. Silence is not consent: a snapshot that says nothing about
    ColorControl's abilities cannot be the reason a colour picker appears.
    """
    required = COMMAND_REQUIRED_FEATURES.get((cluster_id, command_id))
    if required is None:
        return False
    features = declared_features(snapshot, endpoint, cluster_id)
    if features is None:
        return True
    return (features & required) != required
