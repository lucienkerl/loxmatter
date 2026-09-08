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

"""Device types per endpoint from the descriptor cluster."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.profiles.relevance import (
    OTA_REQUESTOR_DEVICE_TYPE,
    POWER_SOURCE_DEVICE_TYPE,
    ROOT_NODE_DEVICE_TYPE,
    device_types_by_endpoint,
    is_functional,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"

_KIND = SignalKind.ATTRIBUTE


def _snapshot(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text())
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def test_the_plug_declares_a_utility_endpoint_and_two_application_endpoints():
    types = device_types_by_endpoint(_snapshot("ikea_grillplats_plug.json"))
    assert ROOT_NODE_DEVICE_TYPE in types[0]
    assert OTA_REQUESTOR_DEVICE_TYPE in types[0]
    assert ROOT_NODE_DEVICE_TYPE not in types[1]
    assert ROOT_NODE_DEVICE_TYPE not in types[2]


def test_the_button_declares_a_power_source_on_its_utility_endpoint():
    """The battery level isn't on endpoint 0 by accident - the device
    declares the Power Source device type there. The exception in task 2
    relies on exactly that; without this assertion it would be a guess."""
    types = device_types_by_endpoint(_snapshot("ikea_bilresa_button.json"))
    assert POWER_SOURCE_DEVICE_TYPE in types[0]


def test_an_endpoint_without_a_descriptor_is_absent_rather_than_empty():
    """If the descriptor is missing, the caller should be able to tell that
    apart from 'descriptor present but empty' - both lead to the same
    decision later, but for different reasons."""
    snapshot = NodeSnapshot.from_raw(1, {"attributes": {"7/6/0": True}})
    assert device_types_by_endpoint(snapshot) == {}


@pytest.mark.parametrize(
    "raw",
    [
        "not a dictionary",
        [{"1": 3}],
        [{"0": "not a number"}],
        [None],
        42,
    ],
)
def test_an_unexpected_descriptor_shape_yields_no_device_types(raw):
    """A non-conformant device must not trigger a crash. The endpoint then
    counts as typeless - and thus later (task 2) as an application
    endpoint: when in doubt, one input too many, never a missing value."""
    snapshot = NodeSnapshot.from_raw(1, {"attributes": {"0/29/0": raw}})
    assert device_types_by_endpoint(snapshot) == {0: frozenset()}


_PLUG_TYPES = {0: frozenset({18, 22}), 1: frozenset({266}), 2: frozenset({1296})}
_BUTTON_TYPES = {0: frozenset({17, 18, 22}), 1: frozenset({15}), 2: frozenset({15})}


def test_a_thread_diagnostics_counter_on_the_root_endpoint_is_not_functional():
    ref = SignalRef(0, 53, 4, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _PLUG_TYPES) is False


def test_the_battery_level_on_a_root_endpoint_is_functional():
    """The exceptional case that the descriptor itself justifies: the
    button additionally declares Power Source on endpoint 0."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _BUTTON_TYPES) is True


def test_the_battery_cluster_is_not_functional_where_no_power_source_is_declared():
    """The same cluster number on an endpoint without a Power Source type
    stays management. The rule hinges on the declared device type, not on
    the cluster number - otherwise it would just be another list."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _PLUG_TYPES) is False


def test_onoff_on_an_application_endpoint_is_functional():
    assert is_functional(SignalRef(1, 6, 0, _KIND), _PLUG_TYPES) is True


def test_a_generic_attribute_of_a_known_cluster_is_not_functional():
    """StartUpOnOff (0x4003) legitimately sits under OnOff, but nobody
    wants it in Loxone. The table knows cluster 6 and names only
    attribute 0 there."""
    assert is_functional(SignalRef(1, 6, 0x4003, _KIND), _PLUG_TYPES) is False


def test_every_attribute_of_an_unknown_cluster_stays_functional():
    """The project's core bet (main document 3.5): a device type this tool
    has never seen still works. If this were wrong, an unfamiliar device
    would sit mute - without anyone noticing that something's missing."""
    assert is_functional(SignalRef(1, 4711, 99, _KIND), _PLUG_TYPES) is True


def test_identify_groups_and_descriptor_are_never_functional():
    for cluster_id in (3, 4, 29):
        assert is_functional(SignalRef(1, cluster_id, 0, _KIND), _PLUG_TYPES) is False


def test_an_endpoint_without_a_declared_type_counts_as_an_application_endpoint():
    """When in doubt, one input too many, never a missing value."""
    assert is_functional(SignalRef(9, 4711, 0, _KIND), _PLUG_TYPES) is True


def test_events_of_a_known_cluster_stay_functional():
    """A dropped event would be a button press that never arrives in
    Loxone - this project's very first requirement."""
    for event_id in (1, 2, 3, 4, 5, 6):
        ref = SignalRef(1, 59, event_id, SignalKind.EVENT)
        assert is_functional(ref, _BUTTON_TYPES) is True


def test_an_event_on_a_utility_endpoint_with_an_unknown_cluster_is_not_functional():
    """An event on the management endpoint is not functional, even though
    it is an event. The docstring says 'events are not subject to layer
    3', but a reader could misread that as 'events aren't filtered by
    layer 3' and thereby miss the filtering by layer 2 (management
    endpoints). If someone later pulls the special case for events
    forward to 'simplify' the code, that silently lets through diagnostic
    events of the management endpoint - without a test catching it. This
    test makes sure that doesn't happen."""
    ref = SignalRef(0, 51, 0, SignalKind.EVENT)  # Cluster 51: GeneralDiagnostics
    assert is_functional(ref, _PLUG_TYPES) is False


def test_a_cluster_known_only_for_its_commands_keeps_its_attributes_functional(
    monkeypatch,
):
    """Review-Fix 1b (follow-up fix, phase 6, final review): layer 3 used
    to ask only `knows_cluster` - true as soon as the cluster carries ANY
    section, even just `commands:`. `names_element` then ALWAYS fails to
    find anything in a missing `attributes:` section, and every attribute
    counted as not functional - the bug that cluster 768 (ColorControl)
    actually had until this fix (see
    `test_extended_color_light_attributes_are_functional_after_the_cluster_768_fix`
    below). Reproduced here with a synthetic table instead of on cluster
    768 itself, so this test captures the trap STRUCTURALLY - for every
    future commands-only cluster, not just the one case now fixed."""
    monkeypatch.setattr(
        "loxmatter.profiles.table._table",
        lambda: {
            999: {
                "name": "commands_only",
                "commands": {1: {"slug": "go", "takes_value": False}},
            }
        },
    )
    ref = SignalRef(1, 999, 42, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _PLUG_TYPES) is True


def test_extended_color_light_attributes_are_functional_after_the_cluster_768_fix():
    """Documents the finding from the final review directly, on the
    (synthetic) device: before Review-Fix 1, cluster 768 (ColorControl)
    was in the table only with `commands:` - each of its attributes
    (`CurrentHue`, `CurrentSaturation`, `ColorTemperatureMireds`,
    `ColorMode`) therefore counted as not functional, while the output
    command for color temperature had long been exported: Loxone could
    set the color but never get it reported back. `tests/fixtures/nodes/
    synthetic_color_light.json` is synthetic (see the comment there) -
    device type and attribute IDs are backed by the same sources as
    `profiles/clusters.yaml` cluster 768 itself."""
    snapshot = _snapshot("synthetic_color_light.json")
    types = device_types_by_endpoint(snapshot)
    # 0, 1, 7, 8 = CurrentHue, CurrentSaturation, ColorTemperatureMireds, ColorMode
    for element_id in (0, 1, 7, 8):
        ref = SignalRef(1, 768, element_id, SignalKind.ATTRIBUTE)
        assert is_functional(ref, types) is True


def test_an_unnamed_power_source_attribute_on_the_utility_endpoint_is_not_functional():
    """Task 6, found while wiring things up: layer 2 used to release the
    whole cluster on a matching `UTILITY_ENDPOINT_KEEP_CLUSTERS` entry,
    instead of just its named elements the way layer 3 does. `clusters.yaml`
    is unambiguous ("only this one of 37 attributes is named") - the
    button actually reports a value under 0/47/0 (BatChargeLevel), and it
    still must not slip through just because the same cluster also
    carries the battery level."""
    ref = SignalRef(0, 47, 0, SignalKind.ATTRIBUTE)
    assert is_functional(ref, _BUTTON_TYPES) is False
