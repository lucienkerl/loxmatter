"""Zerlegt ein Node-Abbild in einzelne Signale.

Rein funktional und ohne I/O — arbeitet auf einem NodeSnapshot und ist damit
gegen eingecheckte Fixtures echter Geräte testbar.

Grundsatz aus Spec 3.5: hier wird nichts verworfen. Unbekannte Cluster werden
genauso zu Signalen wie bekannte; die Anreicherung um Namen und Skalierung
passiert später in profiles/.
"""

from __future__ import annotations

from collections.abc import Iterable

from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.matter.paths import (
    ATTRIBUTE_LIST_ID,
    EVENT_LIST_ID,
    GLOBAL_ATTRIBUTE_IDS,
    parse_attribute_path,
)


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


def extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]:
    """Jedes nicht-globale Attribut und jedes gelistete Event wird ein Signal."""
    signals: set[SignalRef] = set()

    for endpoint, cluster_id, attribute_id, value in _parsed_paths(snapshot):
        if attribute_id == EVENT_LIST_ID:
            for event_id in _as_id_list(value):
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
