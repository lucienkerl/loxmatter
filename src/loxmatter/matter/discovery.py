"""Zerlegt ein Node-Abbild in einzelne Signale.

Rein funktional und ohne I/O — arbeitet auf einem NodeSnapshot und ist damit
gegen eingecheckte Fixtures echter Geräte testbar.

Grundsatz aus Spec 3.5: bei Attributen wird nichts verworfen. Unbekannte
Cluster werden genauso zu Signalen wie bekannte; die Anreicherung um Namen
und Skalierung passiert später in profiles/.

Für Events gilt dieser Grundsatz **nicht mehr uneingeschränkt** — das ist die
Korrektur aus der Validierung an echten Geräten (Phase 1, 2026-09-01, siehe
Spec 3.5 und 6.3). Die EventList (0xFFFA) ist im Matter-Standard optional und
in der Praxis bei den geprüften IKEA-Geräten nicht implementiert: ein Taster,
der nachweislich Tastendrücke sendet, lieferte über die EventList null
Events. Als zweite, cluster-spezifische Quelle wird deshalb aus der FeatureMap
(0xFFFC) abgeleitet, welche Events ein Cluster laut Matter-Spezifikation
generieren *kann* — das Gerät muss die Events dafür nicht selbst auflisten.
Dieses Wissen steht in `FEATURE_MAP_EVENTS`, einer Tabelle, nicht in
verzweigendem Code, damit weitere Cluster ergänzbar sind, ohne den Algorithmus
hier anzufassen. Beide Quellen werden vereinigt und dedupliziert (SignalRef
ist hashable, das Ergebnis-Set übernimmt das automatisch).
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

# Switch-Cluster (0x003B / 59) — Feature-Bits der FeatureMap nach Matter
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
    """Ein Event, das ein Cluster generiert, wenn bestimmte FeatureMap-Bits
    gesetzt und andere nicht gesetzt sind."""

    event_id: int
    requires: int
    excludes: int = 0

    def applies(self, feature_map: int) -> bool:
        return (feature_map & self.requires) == self.requires and (feature_map & self.excludes) == 0


# Welche Events ein Cluster laut Spezifikation abhängig von seiner FeatureMap
# generieren kann. Quelle geprüft gegen
# data_model/1.4/clusters/Switch.xml aus project-chip/connectedhomeip
# (maschinenlesbare Transkription der Matter Application Cluster
# Specification) — je Event ein mandatoryConform über Feature-Bits:
#
#   SwitchLatched (0)        ← LS
#   InitialPress (1)         ← MS
#   LongPress (2)            ← MSL
#   ShortRelease (3)         ← MSR
#   LongRelease (4)          ← MSL
#   MultiPressOngoing (5)    ← MSM UND NICHT AS
#   MultiPressComplete (6)   ← MSM
#
# Weitere Cluster mit Events ohne EventList-Unterstützung kommen hier als
# weitere Einträge dazu — der Algorithmus in extract_signals ändert sich
# dafür nicht.
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
    """Event-IDs, die laut FEATURE_MAP_EVENTS aus der FeatureMap eines Clusters folgen.

    Leer für Cluster ohne Tabelleneintrag oder eine FeatureMap, die keine der
    dort hinterlegten Bit-Bedingungen erfüllt.
    """
    rules = FEATURE_MAP_EVENTS.get(cluster_id)
    if not rules or not isinstance(value, (int, float)):
        return []
    feature_map = int(value)
    return [rule.event_id for rule in rules if rule.applies(feature_map)]


def extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]:
    """Jedes nicht-globale Attribut wird ein Signal. Events kommen aus zwei
    vereinigten Quellen: der EventList (falls das Gerät sie führt) und, für
    Cluster mit Eintrag in FEATURE_MAP_EVENTS, aus der FeatureMap."""
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
    """Attribute, die das Gerät in seiner AttributeList nennt, aber nicht geliefert hat.

    Das ist der Prüfstein für Spec 3.5: eine nicht-leere Liste bedeutet, dass die
    generische Zerlegung Werte übersieht, die das Gerät eigentlich anbietet.
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
    """Pfade, die nicht dem erwarteten Format entsprachen. Sollte leer sein."""
    broken: list[str] = []
    for path in snapshot.attributes:
        try:
            parse_attribute_path(path)
        except ValueError:
            broken.append(path)
    return sorted(broken)
