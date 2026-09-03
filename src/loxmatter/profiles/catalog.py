"""Attribut- und Ereignisnamen aus dem Cluster-Katalog des chip-SDK.

`python-matter-server` installiert `chip.clusters.Objects` ohnehin als
Abhaengigkeit (Task 4, Hauptdokument 6.2: der Signalschluessel bleibt
generisch und unveraenderlich - dieses Modul speist ausschliesslich die
Anzeige, nie den Schluessel). Dort steht fuer jeden Cluster eine
`Attributes`- und eine `Events`-Klasse mit den von der Matter-Spezifikation
vergebenen Namen, indiziert ueber `attribute_id` bzw. `event_id`. Diese
Namen von Hand in `clusters.yaml` nachzupflegen waere Arbeit fuer etwas,
das die Abhaengigkeit bereits mitbringt.

Der Katalog ist eine reine Verbesserung der Anzeige, kein Betriebsmittel:
schlaegt der Import von `chip.clusters.Objects` fehl, oder hat eine
kuenftige SDK-Fassung eine andere Form als hier erwartet (andere
Attributnamen, fehlende `attribute_id`/`event_id`), faengt `_catalog()` das
ab und liefert eine leere Abbildung. `element_name` gibt dann fuer jedes
Signal `None` zurueck, der Aufrufer (`profiles.table.lookup`) faellt auf
den generischen Slug zurueck, und das Werkzeug laeuft unveraendert weiter -
es gibt hier bewusst keinen Pfad, auf dem ein SDK-Problem eine Ausnahme bis
zum Aufrufer durchreicht.
"""

from __future__ import annotations

import functools
import inspect

from loxmatter.matter.models import SignalKind, SignalRef

_CatalogKey = tuple[int, int, SignalKind]


@functools.cache
def _catalog() -> dict[_CatalogKey, str]:
    """Baut einmalig die Abbildung (cluster_id, element_id, kind) -> Name.

    Ein Geraet traegt bis zu 173 Signale (Entwurf 2026-09-03); 140 Cluster
    mit allen Attributen und Ereignissen fuer jedes einzelne neu zu
    durchsuchen waere spuerbar. Der Aufbau laeuft deshalb genau einmal pro
    Prozess hinter `functools.cache`, nicht bei jedem Aufruf von
    `element_name`.
    """
    try:
        import chip.clusters.Objects as chip_objects
    except ImportError:
        return {}

    mapping: dict[_CatalogKey, str] = {}
    try:
        clusters = [
            cls
            for _, cls in inspect.getmembers(chip_objects, inspect.isclass)
            if hasattr(cls, "id") and hasattr(cls, "Attributes")
        ]
        for cluster in clusters:
            cluster_id = cluster.id
            if not isinstance(cluster_id, int):
                continue
            for name, attribute in inspect.getmembers(cluster.Attributes, inspect.isclass):
                attribute_id = getattr(attribute, "attribute_id", None)
                if isinstance(attribute_id, int):
                    mapping[(cluster_id, attribute_id, SignalKind.ATTRIBUTE)] = name
            # Ereignisse liegen in einer eigenen, zu `Attributes` parallelen
            # Klasse `Events` mit `event_id` statt `attribute_id` (belegt in
            # Step 1 gegen chip.clusters.Objects.PowerSource: `WiredFaultChange`
            # traegt `event_id`, keine `attribute_id`). Nicht jeder Cluster
            # hat eine - `getattr` mit Default statt direktem Zugriff.
            events = getattr(cluster, "Events", None)
            if events is None:
                continue
            for name, event in inspect.getmembers(events, inspect.isclass):
                event_id = getattr(event, "event_id", None)
                if isinstance(event_id, int):
                    mapping[(cluster_id, event_id, SignalKind.EVENT)] = name
    except Exception:  # noqa: BLE001 — Katalog ist kein Betriebsmittel (siehe Moduldocstring):
        # jede unerwartete Form einer kuenftigen SDK-Fassung bleibt folgenlos statt das
        # Werkzeug zu stoppen; genau das rechtfertigt hier den absichtlich weiten Fang.
        return {}
    return mapping


def element_name(ref: SignalRef) -> str | None:
    """Name eines Attributs oder Ereignisses laut chip-SDK-Katalog.

    `None`, wenn der Katalog nicht verfuegbar ist (Import fehlgeschlagen
    oder unerwartete Form) oder das Element dort nicht auftaucht - beides
    behandelt der Aufrufer gleich: generischer Name bleibt bestehen.
    """
    return _catalog().get((ref.cluster_id, ref.element_id, ref.kind))
