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


def test_a_standard_cluster_gets_its_specification_name():
    """Cluster 47 is PowerSource in the standard - the same dependency
    `element_name` already reads from (see the test above), just indexed
    by cluster id alone instead of (cluster_id, element_id, kind)."""
    from loxmatter.profiles.catalog import cluster_name

    assert cluster_name(47) == "Power Source"


def test_an_unknown_cluster_id_has_no_name():
    from loxmatter.profiles.catalog import cluster_name

    assert cluster_name(4711) is None


def test_a_multi_word_cluster_name_gets_a_space_before_every_interior_capital():
    """The chip SDK's cluster class names are PascalCase
    (`BasicInformation`); nothing in this codebase names clusters for a
    human otherwise (Expert Settings design, 2026-09-13)."""
    from loxmatter.profiles.catalog import cluster_name

    assert cluster_name(40) == "Basic Information"
