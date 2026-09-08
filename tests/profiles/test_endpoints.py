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

from loxmatter import i18n
from loxmatter.profiles import endpoints


def test_two_button_endpoints_are_numbered():
    """The case this module exists for: the remote carries
    the same device type on two endpoints. Without numbering, the
    modal would have two groups both named "Taste", and `press` would still be
    the same word twice with no clue which button is meant."""
    i18n.set_language("de")
    labels = endpoints.endpoint_labels(
        {0: frozenset({0x0016, 0x0011}), 1: frozenset({0x000F}), 2: frozenset({0x000F})}
    )
    assert labels[1] == "Taste 1"
    assert labels[2] == "Taste 2"


def test_a_single_endpoint_of_a_type_is_not_numbered():
    """A plug has exactly one useful endpoint. "Steckdose 1" would be
    a number with no counterpart."""
    i18n.set_language("de")
    labels = endpoints.endpoint_labels({0: frozenset({0x0016}), 1: frozenset({0x010A})})
    assert labels[1] == "Steckdose"


def test_a_utility_endpoint_is_called_the_device():
    i18n.set_language("de")
    labels = endpoints.endpoint_labels({0: frozenset({0x0016, 0x0011})})
    assert labels[0] == "Gerät"


def test_an_unmapped_type_falls_back_to_the_endpoint_number():
    """The table is deliberately small and only covers device types in use.
    Everything else gets a name that is always correct."""
    i18n.set_language("de")
    labels = endpoints.endpoint_labels({3: frozenset({0x0302})})
    assert labels[3] == "Endpunkt 3"


def test_device_types_never_backfilled_yields_an_empty_mapping():
    """`device.device_types` is `NULL` as long as `backfill_device_types`
    has not run (see `_migrate_to_v7`). That is not an error case, but
    the same fallback as with `category_for(None)` - and this function
    here only owes an empty dict for it, no fallback text:
    that is built by the caller (`api/devices._signal_out`), which has to
    guard `labels.get(...)` against a missing endpoint anyway (see there and
    `tests/api/test_devices.py`). A test-only helper that rebuilds this
    fallback here would only check its own copy of the rule,
    not the shipped spot - that used to be the case here."""
    assert endpoints.endpoint_labels(None) == {}


def test_every_mapped_type_exists_in_the_matter_table():
    """The same safeguard `test_categories.py` has for
    CATEGORY_BY_DEVICE_TYPE: a number from memory instead of from
    the specification would otherwise never be noticed."""
    from matter_server.client.models.device_types import ALL_TYPES

    unknown = sorted(
        hex(t) for t in endpoints.ENDPOINT_NAME_KEY_BY_DEVICE_TYPE if t not in ALL_TYPES
    )
    assert unknown == []
