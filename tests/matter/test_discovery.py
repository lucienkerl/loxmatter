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

from loxmatter.matter.discovery import (
    extract_signals,
    find_clusters_with_undiscoverable_events,
    find_unparsable_paths,
    find_unreported_attributes,
)
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef


def snapshot(attributes: dict[str, object]) -> NodeSnapshot:
    return NodeSnapshot.from_raw(node_id=1, raw={"attributes": attributes})


def test_every_non_global_attribute_becomes_a_signal():
    signals = extract_signals(snapshot({"1/6/0": True, "1/8/0": 254}))
    assert signals == [
        SignalRef(1, 6, 0, SignalKind.ATTRIBUTE),
        SignalRef(1, 8, 0, SignalKind.ATTRIBUTE),
    ]


def test_global_attributes_are_not_signals():
    signals = extract_signals(
        snapshot({"1/6/0": True, "1/6/65533": 6, "1/6/65532": 0, "1/6/65531": [0]})
    )
    assert signals == [SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)]


def test_event_list_produces_event_signals():
    signals = extract_signals(snapshot({"1/59/65530": [0, 1, 2]}))
    assert signals == [
        SignalRef(1, 59, 0, SignalKind.EVENT),
        SignalRef(1, 59, 1, SignalKind.EVENT),
        SignalRef(1, 59, 2, SignalKind.EVENT),
    ]


def test_empty_or_absent_event_list_produces_nothing():
    assert extract_signals(snapshot({"1/59/65530": []})) == []
    assert extract_signals(snapshot({"1/59/65530": None})) == []


def test_unknown_cluster_is_still_extracted():
    """Spec 3.5: profiles/ is enrichment, not a gatekeeper."""
    signals = extract_signals(snapshot({"1/64999/7": 42}))
    assert signals == [SignalRef(1, 64999, 7, SignalKind.ATTRIBUTE)]


def test_signals_are_sorted_deterministically():
    signals = extract_signals(snapshot({"2/6/0": True, "1/1030/0": 1, "1/6/0": False}))
    assert [s.path for s in signals] == ["1/6/0", "1/1030/0", "2/6/0"]


def test_finds_attributes_the_device_claims_but_did_not_report():
    # AttributeList (65531) names 0 and 16, but only 0 was delivered.
    missing = find_unreported_attributes(snapshot({"1/6/65531": [0, 16], "1/6/0": True}))
    assert missing == [SignalRef(1, 6, 16, SignalKind.ATTRIBUTE)]


def test_reports_nothing_missing_when_device_is_complete():
    assert find_unreported_attributes(snapshot({"1/6/65531": [0], "1/6/0": True})) == []


def test_global_attributes_are_not_counted_as_missing():
    missing = find_unreported_attributes(snapshot({"1/6/65531": [0, 65533], "1/6/0": True}))
    assert missing == []


def test_unparsable_paths_are_collected_not_raised():
    snap = snapshot({"kaputt": 1, "1/6/0": True})
    assert find_unparsable_paths(snap) == ["kaputt"]
    assert extract_signals(snap) == [SignalRef(1, 6, 0, SignalKind.ATTRIBUTE)]


# Second event source: FeatureMap of the switch cluster (0x003B / 59).
#
# EventList (65530) is optional and, per validation against real IKEA
# devices (see tests/matter/test_real_devices.py), not implemented in
# practice — a switch without this derivation delivers zero event signals.
# The conditions below are taken from
# data_model/1.4/clusters/Switch.xml (project-chip/connectedhomeip, a
# machine-readable transcription of the Matter Application Cluster
# Specification): one mandatoryConform per event, over feature bits.


def test_feature_map_ms_only_yields_initial_press_only():
    # MS (Bit 1) = 0b10 = 2
    signals = extract_signals(snapshot({"1/59/65532": 2}))
    assert signals == [SignalRef(1, 59, 1, SignalKind.EVENT)]  # InitialPress


def test_feature_map_ms_msr_yields_initial_press_and_short_release():
    # MS + MSR = 0b110 = 6
    signals = extract_signals(snapshot({"1/59/65532": 6}))
    assert signals == [
        SignalRef(1, 59, 1, SignalKind.EVENT),  # InitialPress
        SignalRef(1, 59, 3, SignalKind.EVENT),  # ShortRelease
    ]


def test_feature_map_ls_only_yields_switch_latched_only():
    # LS (Bit 0) = 1
    signals = extract_signals(snapshot({"1/59/65532": 1}))
    assert signals == [SignalRef(1, 59, 0, SignalKind.EVENT)]  # SwitchLatched


def test_feature_map_30_matches_ikea_bilresa_button():
    # MS + MSR + MSL + MSM = 2 + 4 + 8 + 16 = 30, the real FeatureMap of the
    # IKEA BILRESA switch (node 4, endpoints 1 and 2). AS is not set, so
    # MultiPressOngoing fires in addition to MultiPressComplete; LS is not
    # set, so SwitchLatched is correspondingly absent.
    signals = extract_signals(snapshot({"1/59/65532": 30}))
    assert signals == [
        SignalRef(1, 59, 1, SignalKind.EVENT),  # InitialPress
        SignalRef(1, 59, 2, SignalKind.EVENT),  # LongPress
        SignalRef(1, 59, 3, SignalKind.EVENT),  # ShortRelease
        SignalRef(1, 59, 4, SignalKind.EVENT),  # LongRelease
        SignalRef(1, 59, 5, SignalKind.EVENT),  # MultiPressOngoing
        SignalRef(1, 59, 6, SignalKind.EVENT),  # MultiPressComplete
    ]


def test_feature_map_msm_with_action_switch_excludes_multi_press_ongoing():
    # MSM + AS = 16 + 32 = 48. MultiPressOngoing requires MSM AND NOT AS.
    signals = extract_signals(snapshot({"1/59/65532": 48}))
    assert signals == [SignalRef(1, 59, 6, SignalKind.EVENT)]  # MultiPressComplete


def test_feature_map_zero_yields_no_events():
    assert extract_signals(snapshot({"1/59/65532": 0})) == []


def test_feature_map_is_ignored_for_clusters_without_a_table_entry():
    """The FeatureMap derivation is cluster-specific knowledge — for clusters
    without an entry in FEATURE_MAP_EVENTS it must not invent anything."""
    assert extract_signals(snapshot({"1/6/65532": 30})) == []


def test_event_list_and_feature_map_are_unioned_and_deduplicated():
    signals = extract_signals(snapshot({"1/59/65530": [1, 3], "1/59/65532": 6}))
    # EventList names {1, 3}, FeatureMap (MS+MSR) also {1, 3} — no duplicate.
    assert signals == [
        SignalRef(1, 59, 1, SignalKind.EVENT),
        SignalRef(1, 59, 3, SignalKind.EVENT),
    ]


def test_feature_map_attribute_itself_is_not_an_attribute_signal():
    signals = extract_signals(snapshot({"1/59/65532": 30}))
    assert all(s.kind is SignalKind.EVENT for s in signals)


# Third instrument: clusters for which neither an EventList exists nor an
# entry in FEATURE_MAP_EVENTS. Here the tool cannot say whether there are
# events — unlike "0 events", where it has actually checked (via EventList
# or the FeatureMap table).


def test_flags_clusters_without_event_list_and_without_feature_map_table_entry():
    # Clusters 42 (OTA Requestor) and 145 (ElectricalEnergyMeasurement) both
    # have mandatory events per spec, but no EventList and no entry in
    # FEATURE_MAP_EVENTS — exactly the blind spot.
    snap = snapshot({"0/42/0": 1, "2/145/65532": 5})
    assert find_clusters_with_undiscoverable_events(snap) == [(0, 42), (2, 145)]


def test_cluster_with_event_list_is_not_flagged():
    snap = snapshot({"1/42/65530": [0, 1]})
    assert find_clusters_with_undiscoverable_events(snap) == []


def test_cluster_with_feature_map_table_entry_is_not_flagged_even_without_event_list():
    # Switch (59) is in FEATURE_MAP_EVENTS — even without an EventList, the
    # tool knows what to look for here.
    snap = snapshot({"1/59/0": True})
    assert find_clusters_with_undiscoverable_events(snap) == []


def test_undiscoverable_events_result_is_deduplicated_and_sorted():
    snap = snapshot({"2/99/0": 1, "0/42/1": 2, "0/42/2": 3, "1/50/0": 1})
    assert find_clusters_with_undiscoverable_events(snap) == [(0, 42), (1, 50), (2, 99)]


def test_no_clusters_flagged_when_snapshot_is_empty():
    assert find_clusters_with_undiscoverable_events(snapshot({})) == []
