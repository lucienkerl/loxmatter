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

"""Which signals a user wants by default (design 2026-09-03, 4.1).

Separate from `Exportability` and deliberately in its own module: the
question "can the value be mapped to a UDP input" (table.py) and the
question "does anyone want it" are different questions with different
answers. A Thread radio counter is exportable but not relevant.

The selection does not rely on a list of cluster numbers someone considers
boring, but on Matter's own structure: the descriptor cluster carries a
standardized device-type list on every endpoint. A device without this
information is not certified - the rule therefore holds for every
manufacturer and every device type, even ones this tool has never seen.
"""

from __future__ import annotations

from typing import Any

from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.matter.paths import parse_attribute_path
from loxmatter.profiles.table import known_attribute_section, marked_non_functional, names_element

DESCRIPTOR_CLUSTER_ID = 29
DEVICE_TYPE_LIST_ID = 0

# Source: `matter_server.client.models.device_types` (installed under
# .venv/lib/python3.12/site-packages/matter_server/client/models/
# device_types.py), per its own module docstring machine-generated from
# the CSA specification's `zcl/data-model/chip/matter-devices.xml`. The
# installed chip SDK itself (chip.clusters.Objects), by contrast, only
# contains the cluster structure of the descriptor attribute
# `DeviceTypeList`, no table of device-type numbers (checked in Task 1).
# Cross-checked against the checked-in snapshots
# tests/fixtures/nodes/ikea_grillplats_plug.json and
# ikea_bilresa_button.json: their "<endpoint>/29/0" values contain exactly
# these three numbers as expected.
ROOT_NODE_DEVICE_TYPE = 0x0016
OTA_REQUESTOR_DEVICE_TYPE = 0x0012
POWER_SOURCE_DEVICE_TYPE = 0x0011

UTILITY_DEVICE_TYPES: frozenset[int] = frozenset({ROOT_NODE_DEVICE_TYPE, OTA_REQUESTOR_DEVICE_TYPE})


def _device_type_ids(raw: object) -> frozenset[int]:
    """The device-type numbers from a `DeviceTypeList` value.

    matter-server returns structs as a dictionary with the field tag as a
    STRING, not the field name: a DeviceTypeStruct arrives as
    ``{"0": <type>, "1": <revision>}``. Both - string and number - are
    accepted, because a different serialisation could just as plausibly
    return the same struct as ``{0: ...}``.

    Anything unexpected yields an empty set instead of an exception: a
    non-conformant device should not halt the decomposition.
    """
    if not isinstance(raw, (list, tuple)):
        return frozenset()
    ids: set[int] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        value: Any = entry.get("0", entry.get(0))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        ids.add(int(value))
    return frozenset(ids)


def device_types_by_endpoint(snapshot: NodeSnapshot) -> dict[int, frozenset[int]]:
    """The declared device types per endpoint.

    An endpoint without a descriptor does not appear at all - the caller
    thereby distinguishes "not reported" from "reported, but empty", even
    though both later lead to the same decision.
    """
    result: dict[int, frozenset[int]] = {}
    for path, value in snapshot.attributes.items():
        try:
            endpoint, cluster_id, attribute_id = parse_attribute_path(path)
        except ValueError:
            continue
        if cluster_id != DESCRIPTOR_CLUSTER_ID or attribute_id != DEVICE_TYPE_LIST_ID:
            continue
        result[endpoint] = _device_type_ids(value)
    return result


# Management on every endpoint, regardless of device type: Identify
# (blinking for identification), Groups (Matter group management), and the
# descriptor itself. None of these has any meaning for home automation.
BOILERPLATE_CLUSTERS: frozenset[int] = frozenset({3, 4, DESCRIPTOR_CLUSTER_ID})

# Clusters that are wanted on a management endpoint nonetheless - but only
# if the device also declares the associated functional device type there.
# The battery level is the case that makes this necessary.
#
# IMPORTANT: this is NOT a complete lookup table of all device types and
# their clusters - only the exceptions that actually occur on real
# devices. A new case requires a concrete piece of evidence (a link to the
# device or to the specification), not the assumption that the table is
# complete on its own.
UTILITY_ENDPOINT_KEEP_CLUSTERS: dict[int, int] = {
    47: POWER_SOURCE_DEVICE_TYPE,  # PowerSource
}


def is_functional(ref: SignalRef, device_types: dict[int, frozenset[int]]) -> bool:
    """Whether this signal is wanted by default (design 2026-09-03, 4).

    Three layers, in this order:

    1. Boilerplate clusters are never wanted, on any endpoint.
    2. On a management endpoint (root node or OTA requestor), only
       something that also belongs to a functional device type declared
       there is even in the running - everything else drops out
       immediately.
    3. Whatever passes layer 2 (and every functional endpoint anyway) is
       wanted - except for a cluster for which the profile table carries
       an `attributes:` section: there only the elements named there -
       minus those the table explicitly marks with `functional:
       false` (device constants such as min/max ranges, see
       `marked_non_functional`).
       An unknown cluster, and a known cluster without an
       `attributes:` section (say, one that is in the table only because
       of its commands), stay fully wanted (main document 3.5).
       This distinction is `known_attribute_section` in
       `profiles.table` - review-fix phase 6: `knows_cluster` alone was
       not enough, see its docstring.

    Important: layer 2 only grants the cluster a place on the management
    endpoint, not already every one of its elements - layer 3 filters
    within that cluster exactly as it would on a functional endpoint. The
    button declares Power Source on endpoint 0 because of the battery
    level (element 12), but carries the same cluster 47 with 36 other,
    unnamed attributes (charge state, battery chemistry, ANSI designations,
    fault lists) - without this second filter all 37 would be "wanted"
    just because one of them is (Task 6, see
    `test_an_unnamed_power_source_attribute_on_the_utility_endpoint_is_not_functional`).

    Events are not subject to the cluster-table filter from layer 3: they
    are already listed by name in the table anyway, and a discarded event
    would be a button press that never arrives in Loxone.
    """
    if ref.cluster_id in BOILERPLATE_CLUSTERS:
        return False

    declared = device_types.get(ref.endpoint, frozenset())
    if declared & UTILITY_DEVICE_TYPES:
        required = UTILITY_ENDPOINT_KEEP_CLUSTERS.get(ref.cluster_id)
        if required is None or required not in declared:
            return False

    if ref.kind is SignalKind.EVENT:
        return True
    if known_attribute_section(ref.cluster_id):
        return names_element(ref) and not marked_non_functional(ref)
    return True
