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

"""Attribute names from the chip-SDK's cluster catalog."""

from __future__ import annotations

from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles.catalog import element_name


def test_a_standard_attribute_gets_its_specification_name():
    """c47_a12 is called BatPercentRemaining in the standard. The name lives
    in a dependency this project installs anyway - maintaining it by hand
    would be work for nothing."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    assert element_name(ref) == "BatPercentRemaining"


def test_an_unknown_cluster_has_no_name():
    assert element_name(SignalRef(1, 4711, 0, SignalKind.ATTRIBUTE)) is None


def test_an_unknown_attribute_of_a_known_cluster_has_no_name():
    assert element_name(SignalRef(1, 6, 9999, SignalKind.ATTRIBUTE)) is None


def test_an_event_gets_its_specification_name_too():
    """The catalog keeps events separate from attributes (`.Events` instead
    of `.Attributes`) - the same (cluster_id, element_id) number can mean
    something different in each section, so `kind` has to be part of the
    key."""
    ref = SignalRef(1, 47, 0, SignalKind.EVENT)
    assert element_name(ref) == "WiredFaultChange"


def test_the_catalog_is_read_once():
    """Searching 140 clusters with all their attributes on every signal
    would be noticeable at 159 signals per device. Building it belongs
    behind a cache."""
    first = element_name(SignalRef(0, 47, 12, SignalKind.ATTRIBUTE))
    second = element_name(SignalRef(0, 47, 12, SignalKind.ATTRIBUTE))
    assert first == second == "BatPercentRemaining"
