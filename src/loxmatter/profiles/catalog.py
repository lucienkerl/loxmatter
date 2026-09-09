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

The Matter client library installs `chip.clusters.Objects` as a dependency
anyway (Task 4, main document 6.2: the signal key remains generic and
unchanging - this module feeds only the display, never the key). For each
cluster there is an `Attributes` and an `Events` class with names assigned by
the Matter specification, indexed by `attribute_id` respectively `event_id`.
Maintaining these names by hand in `clusters.yaml` would be work for something
the dependency already provides.

The catalog is a pure display enhancement, not an operational resource: if the
import of `chip.clusters.Objects` fails, or a future SDK version has a
different form than expected here (different attribute names, missing
`attribute_id`/`event_id`), `_catalog()` catches that and returns an empty
mapping. `element_name` then returns `None` for every signal, the caller
(`profiles.table.lookup`) falls back to the generic slug, and the tool runs
unchanged - there is deliberately no path here where an SDK problem propagates
an exception to the caller.

**Update (8 September 2026): the library that `chip` provides has changed.**
Until then it was `python-matter-server`, since then it is `matter-python-client`
from the successor project `matterjs-server`. The module path `chip.clusters.Objects`
and the form of the `Attributes`/`Events` classes have remained the same, which is
why no line changes here. The "future SDK version" in the paragraph above is thus
no longer a hypothetical concern, but has actually occurred - and the fallback path
that this module provides for it held up without needing to be used.
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
