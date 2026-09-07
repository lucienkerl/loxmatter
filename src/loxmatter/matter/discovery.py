# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

"""Break a node snapshot down into individual signals.

Purely functional and without I/O — operates on a NodeSnapshot and is
therefore testable against checked-in fixtures of real devices.

Principle from spec 3.5: nothing is discarded for attributes. Unknown
clusters become signals just like known ones; enrichment with names and
scaling happens later in profiles/.

For events this principle **no longer holds without restriction** — that
is the correction from validation against real devices (phase 1,
2026-09-01, see spec 3.5 and 6.3). The EventList (0xFFFA) is optional in
the Matter standard and in practice not implemented on the IKEA devices
tested: a push-button that demonstrably sends button presses delivered
zero events via the EventList. As a second, cluster-specific source, we
therefore derive from the FeatureMap (0xFFFC) which events a cluster *can*
generate according to the Matter specification — the device does not need
to list the events itself for that. This knowledge lives in
`FEATURE_MAP_EVENTS`, a table, not in branching code, so that further
clusters can be added without touching the algorithm here. Both sources
are unioned and deduplicated (SignalRef is hashable, the result set
handles that automatically).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.matter.paths import (
    ATTRIBUTE_LIST_ID,
    EVENT_LIST_ID,
    FEATURE_MAP_ID,
    GLOBAL_ATTRIBUTE_IDS,
    parse_attribute_path,
)

# Switch cluster (0x003B / 59) — FeatureMap feature bits per the Matter
# Application Cluster Specification.
_SWITCH_CLUSTER_ID = 59
_LATCHING_SWITCH = 0x01
_MOMENTARY_SWITCH = 0x02
_MOMENTARY_SWITCH_RELEASE = 0x04
_MOMENTARY_SWITCH_LONG_PRESS = 0x08
_MOMENTARY_SWITCH_MULTI_PRESS = 0x10
_ACTION_SWITCH = 0x20


@dataclass(frozen=True)
class _FeatureEventRule:
    """An event a cluster generates when certain FeatureMap bits are set
    and others are not."""

    event_id: int
    requires: int
    excludes: int = 0

    def applies(self, feature_map: int) -> bool:
        return (feature_map & self.requires) == self.requires and (feature_map & self.excludes) == 0


# Which events a cluster can generate depending on its FeatureMap, per the
# specification. Source checked against
# data_model/1.4/clusters/Switch.xml from project-chip/connectedhomeip
# (machine-readable transcription of the Matter Application Cluster
# Specification) — one mandatoryConform per event, via feature bits:
#
#   SwitchLatched (0)        ← LS
#   InitialPress (1)         ← MS
#   LongPress (2)            ← MSL
#   ShortRelease (3)         ← MSR
#   LongRelease (4)          ← MSL
#   MultiPressOngoing (5)    ← MSM AND NOT AS
#   MultiPressComplete (6)   ← MSM
#
# Further clusters with events lacking EventList support are added here as
# additional entries — the algorithm in extract_signals does not change
# for that.
FEATURE_MAP_EVENTS: dict[int, tuple[_FeatureEventRule, ...]] = {
    _SWITCH_CLUSTER_ID: (
        _FeatureEventRule(event_id=0, requires=_LATCHING_SWITCH),
        _FeatureEventRule(event_id=1, requires=_MOMENTARY_SWITCH),
        _FeatureEventRule(event_id=2, requires=_MOMENTARY_SWITCH_LONG_PRESS),
        _FeatureEventRule(event_id=3, requires=_MOMENTARY_SWITCH_RELEASE),
        _FeatureEventRule(event_id=4, requires=_MOMENTARY_SWITCH_LONG_PRESS),
        _FeatureEventRule(
            event_id=5,
            requires=_MOMENTARY_SWITCH_MULTI_PRESS,
            excludes=_ACTION_SWITCH,
        ),
        _FeatureEventRule(event_id=6, requires=_MOMENTARY_SWITCH_MULTI_PRESS),
    ),
}


def _parsed_paths(snapshot: NodeSnapshot) -> Iterable[tuple[int, int, int, object]]:
    for path, value in snapshot.attributes.items():
        try:
            endpoint, cluster_id, attribute_id = parse_attribute_path(path)
        except ValueError:
            continue
        yield endpoint, cluster_id, attribute_id, value


def _as_id_list(value: object) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    return [int(item) for item in value if isinstance(item, (int, float))]


def _feature_map_event_ids(cluster_id: int, value: object) -> list[int]:
    """Event IDs that follow from a cluster's FeatureMap per FEATURE_MAP_EVENTS.

    Empty for clusters without a table entry, or a FeatureMap that satisfies
    none of the bit conditions stored there.
    """
    rules = FEATURE_MAP_EVENTS.get(cluster_id)
    if not rules or not isinstance(value, (int, float)):
        return []
    feature_map = int(value)
    return [rule.event_id for rule in rules if rule.applies(feature_map)]


def extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]:
    """Every non-global attribute becomes a signal. Events come from two
    unioned sources: the EventList (if the device carries it) and, for
    clusters with an entry in FEATURE_MAP_EVENTS, the FeatureMap."""
    signals: set[SignalRef] = set()

    for endpoint, cluster_id, attribute_id, value in _parsed_paths(snapshot):
        if attribute_id == EVENT_LIST_ID:
            for event_id in _as_id_list(value):
                signals.add(SignalRef(endpoint, cluster_id, event_id, SignalKind.EVENT))
            continue
        if attribute_id == FEATURE_MAP_ID:
            for event_id in _feature_map_event_ids(cluster_id, value):
                signals.add(SignalRef(endpoint, cluster_id, event_id, SignalKind.EVENT))
            continue
        if attribute_id in GLOBAL_ATTRIBUTE_IDS:
            continue
        signals.add(SignalRef(endpoint, cluster_id, attribute_id, SignalKind.ATTRIBUTE))

    return sorted(signals)


def find_unreported_attributes(snapshot: NodeSnapshot) -> list[SignalRef]:
    """Attributes the device names in its AttributeList but did not deliver.

    This is the touchstone for spec 3.5: a non-empty list means the generic
    decomposition is overlooking values the device actually offers.
    """
    present: set[tuple[int, int, int]] = set()
    claimed: set[tuple[int, int, int]] = set()

    for endpoint, cluster_id, attribute_id, value in _parsed_paths(snapshot):
        present.add((endpoint, cluster_id, attribute_id))
        if attribute_id == ATTRIBUTE_LIST_ID:
            for claimed_id in _as_id_list(value):
                if claimed_id not in GLOBAL_ATTRIBUTE_IDS:
                    claimed.add((endpoint, cluster_id, claimed_id))

    return sorted(
        SignalRef(endpoint, cluster_id, attribute_id, SignalKind.ATTRIBUTE)
        for endpoint, cluster_id, attribute_id in claimed - present
    )


def find_unparsable_paths(snapshot: NodeSnapshot) -> list[str]:
    """Paths that did not match the expected format. Should be empty.

    Deliberately walks all paths independently once more instead of
    reusing `_parsed_paths` — that is precisely what measures what gets
    discarded there.
    """
    broken: list[str] = []
    for path in snapshot.attributes:
        try:
            parse_attribute_path(path)
        except ValueError:
            broken.append(path)
    return sorted(broken)


def find_clusters_with_undiscoverable_events(snapshot: NodeSnapshot) -> list[tuple[int, int]]:
    """Clusters for which neither an EventList is present nor an entry in
    FEATURE_MAP_EVENTS exists.

    These are the clusters where this tool simply cannot say whether events
    exist: the EventList was not delivered (the device either does not
    carry one, or it is empty — both look the same here) and the FeatureMap
    table does not know the cluster, so it cannot derive anything either.
    "Events (0)" in the report is, for such a cluster, not a statement
    about the device but about this tool's knowledge gap.

    Deliberately without a special case for endpoint 0: cluster 0/42 (OTA
    Software Update Requestor) is exactly the example that motivated this
    function — its events (StateTransition, VersionApplied, DownloadError)
    are mandatory but cannot be proven without an EventList. A blanket
    exception for administrative endpoint-0 clusters would therefore hide
    the very case this instrument is meant to uncover.
    """
    clusters: set[tuple[int, int]] = set()
    with_event_list: set[tuple[int, int]] = set()

    for endpoint, cluster_id, attribute_id, _value in _parsed_paths(snapshot):
        clusters.add((endpoint, cluster_id))
        if attribute_id == EVENT_LIST_ID:
            with_event_list.add((endpoint, cluster_id))

    return sorted(
        (endpoint, cluster_id)
        for endpoint, cluster_id in clusters
        if (endpoint, cluster_id) not in with_event_list and cluster_id not in FEATURE_MAP_EVENTS
    )
