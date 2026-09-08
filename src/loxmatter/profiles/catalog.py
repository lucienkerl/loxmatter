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

"""Attribute and event names from the chip SDK's cluster catalog.

Die Matter-Client-Bibliothek installiert `chip.clusters.Objects` ohnehin als
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

**Nachtrag (8. September 2026): die Bibliothek, die `chip` mitbringt, hat
gewechselt.** Bis dahin war es `python-matter-server`, seither ist es
`matter-python-client` aus dem Nachfolgeprojekt `matterjs-server`. Der
Modulpfad `chip.clusters.Objects` und die Form der `Attributes`-/
`Events`-Klassen sind dieselben geblieben, deshalb aendert sich hier keine
Zeile. Die "kuenftige SDK-Fassung" im Absatz darueber ist damit allerdings
keine hypothetische Sorge mehr, sondern einmal tatsaechlich eingetreten -
und der Rueckfallweg, den dieses Modul dafuer vorhaelt, hat gehalten, ohne
gebraucht zu werden.
"""

from __future__ import annotations

import functools
import inspect

from loxmatter.matter.models import SignalKind, SignalRef

_CatalogKey = tuple[int, int, SignalKind]


@functools.cache
def _catalog() -> dict[_CatalogKey, str]:
    """Builds the mapping (cluster_id, element_id, kind) -> name once.

    A device carries up to 173 signals (design 2026-09-03); re-searching
    140 clusters with all attributes and events for every single one would
    be noticeable. The build therefore runs exactly once per process behind
    `functools.cache`, not on every call to `element_name`.
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
            # Events live in their own class `Events`, parallel to
            # `Attributes`, with `event_id` instead of `attribute_id`
            # (confirmed in Step 1 against chip.clusters.Objects.PowerSource:
            # `WiredFaultChange` carries `event_id`, no `attribute_id`). Not
            # every cluster has one - `getattr` with a default instead of
            # direct access.
            events = getattr(cluster, "Events", None)
            if events is None:
                continue
            for name, event in inspect.getmembers(events, inspect.isclass):
                event_id = getattr(event, "event_id", None)
                if isinstance(event_id, int):
                    mapping[(cluster_id, event_id, SignalKind.EVENT)] = name
    except Exception:  # noqa: BLE001 — the catalog is not an operational resource (see module
        # docstring): any unexpected shape of a future SDK release stays without consequence
        # instead of stopping the tool; that is exactly what justifies the deliberately broad
        # catch here.
        return {}
    return mapping


def element_name(ref: SignalRef) -> str | None:
    """Name of an attribute or event per the chip SDK catalog.

    `None` if the catalog is unavailable (import failed or unexpected
    shape) or the element does not appear there - the caller treats both
    the same: the generic name is kept.
    """
    return _catalog().get((ref.cluster_id, ref.element_id, ref.kind))
