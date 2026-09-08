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

"""The human-readable name of A SINGLE endpoint (design 2026-09-07, section 7.4).

Sits next to `categories.py`, not inside it, and that is the whole
rationale for this module: `category_for` answers "what kind of thing
is the DEVICE" - light, plug, switch -, this file "what is this one
endpoint IN IT called". A remote control is ONE switch with TWO
buttons; applying `CATEGORY_BY_DEVICE_TYPE` to its endpoints would yield
"switch 1" and "switch 2" - the same wrong term, twice.

The table below is deliberately SMALL. It lists the device types that
actually occur on the checked-in snapshots in tests/fixtures/nodes/,
and nothing else - the same standard as for
`UTILITY_ENDPOINT_KEEP_CLUSTERS` in `relevance.py`: a new entry
needs a concrete piece of evidence, not the assumption that the table is
complete on its own. Everything else falls back to "Endpoint N", and
that is a name that is always correct.
"""

from __future__ import annotations

from collections.abc import Mapping

from loxmatter import i18n
from loxmatter.profiles.relevance import POWER_SOURCE_DEVICE_TYPE, UTILITY_DEVICE_TYPES

# Device type -> translation key. The numbers come from
# `matter_server.client.models.device_types` as in `categories.py`; the
# comments name the class name found there.
ENDPOINT_NAME_KEY_BY_DEVICE_TYPE: dict[int, str] = {
    0x000F: "web.signals.endpoint_button",  # GenericSwitch (IKEA BILRESA, Ep 1+2)
    0x010A: "web.signals.endpoint_socket",  # OnOffPlugInUnit (IKEA GRILLPLATS, Ep 1)
    0x010D: "web.signals.endpoint_light",  # ExtendedColorLight (synthetic_color_light, Ep 1)
    0x0510: "web.signals.endpoint_metering",  # ElectricalSensor (GRILLPLATS, Ep 2)
}

# An endpoint that carries only management is simply called "device" -
# that is where the battery level sits, and "endpoint 0" would be a
# number without meaning to the user. PowerSource counts here too,
# because alone it still does not make a functional endpoint (the same
# reasoning as `_IGNORED_DEVICE_TYPES` in categories.py).
_DEVICE_ENDPOINT_TYPES: frozenset[int] = UTILITY_DEVICE_TYPES | {POWER_SOURCE_DEVICE_TYPE}


def _name_key(declared: frozenset[int]) -> str | None:
    """The key for this endpoint, or `None` to fall back further.

    A functional type beats the management type: endpoint 0 of the
    remote control declares root node AND power source AND OTA requestor -
    it is called "device". If an endpoint instead carries both, management
    and a named functional type, the functional type wins because it says
    more.
    """
    for device_type in sorted(declared):
        key = ENDPOINT_NAME_KEY_BY_DEVICE_TYPE.get(device_type)
        if key is not None:
            return key
    if declared & _DEVICE_ENDPOINT_TYPES:
        return "web.signals.endpoint_device"
    return None


def endpoint_labels(device_types: Mapping[int, frozenset[int]] | None) -> dict[int, str]:
    """Endpoint number -> ready, translated name.

    Numbering happens only where there is something to distinguish: two
    button endpoints yield "Button 1" and "Button 2", a single socket
    endpoint stays "Socket" - a "1" without a "2" is a number without a
    counterpart.

    `None` (device types not yet backfilled, see
    `Store.backfill_device_types`) yields an empty mapping; the
    caller then falls back to `endpoint_plain` for every endpoint -
    the same wordless treatment that `category_for(None)` gets with
    `OTHER`. If `device_types` is known, however, but a single
    endpoint carries none of the types from `ENDPOINT_NAME_KEY_BY_DEVICE_TYPE`
    (table deliberately small, see module docstring), this very
    function already enters "Endpoint N" - not the caller. Only this way
    does every endpoint known in `device_types` stay represented in the
    result.
    """
    if not device_types:
        return {}

    keys: dict[int, str] = {}
    for endpoint, declared in device_types.items():
        key = _name_key(declared)
        if key is not None:
            keys[endpoint] = key

    counts: dict[str, int] = {}
    for key in keys.values():
        counts[key] = counts.get(key, 0) + 1

    # Every endpoint from `device_types` gets an entry - even one without
    # a resolved key already falls back to "Endpoint N" HERE, not only at
    # the caller. Only this way does `labels[endpoint]` stay reliable for
    # every known endpoint; an empty result (no entry) stays reserved
    # solely for the case "device_types entirely None" (see above).
    labels: dict[int, str] = {}
    seen: dict[str, int] = {}
    for endpoint in sorted(device_types):
        key = keys.get(endpoint)
        if key is None:
            labels[endpoint] = i18n.t("web.signals.endpoint_plain", endpoint=endpoint)
            continue
        name = i18n.t(key)
        if counts[key] == 1:
            labels[endpoint] = name
            continue
        seen[key] = seen.get(key, 0) + 1
        labels[endpoint] = i18n.t("web.signals.endpoint_numbered", name=name, index=seen[key])
    return labels
