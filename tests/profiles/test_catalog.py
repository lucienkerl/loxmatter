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

"""Attributnamen aus dem Cluster-Katalog des chip-SDK."""

from __future__ import annotations

from loxmatter.matter.models import SignalKind, SignalRef
from loxmatter.profiles.catalog import element_name


def test_a_standard_attribute_gets_its_specification_name():
    """c47_a12 heisst im Standard BatPercentRemaining. Der Name liegt in
    einer Abhaengigkeit, die dieses Projekt ohnehin installiert - ihn von
    Hand zu pflegen waere Arbeit fuer nichts."""
    ref = SignalRef(0, 47, 12, SignalKind.ATTRIBUTE)
    assert element_name(ref) == "BatPercentRemaining"


def test_an_unknown_cluster_has_no_name():
    assert element_name(SignalRef(1, 4711, 0, SignalKind.ATTRIBUTE)) is None


def test_an_unknown_attribute_of_a_known_cluster_has_no_name():
    assert element_name(SignalRef(1, 6, 9999, SignalKind.ATTRIBUTE)) is None


def test_an_event_gets_its_specification_name_too():
    """Der Katalog fuehrt Ereignisse getrennt von Attributen (`.Events`
    statt `.Attributes`) - dieselbe (cluster_id, element_id)-Zahl kann in
    beiden Abschnitten etwas anderes bedeuten, `kind` muss also mit in den
    Schluessel."""
    ref = SignalRef(1, 47, 0, SignalKind.EVENT)
    assert element_name(ref) == "WiredFaultChange"


def test_the_catalog_is_read_once():
    """140 Cluster mit allen Attributen bei jedem Signal zu durchsuchen
    waere bei 159 Signalen je Geraet spuerbar. Der Aufbau gehoert hinter
    einen Cache."""
    first = element_name(SignalRef(0, 47, 12, SignalKind.ATTRIBUTE))
    second = element_name(SignalRef(0, 47, 12, SignalKind.ATTRIBUTE))
    assert first == second == "BatPercentRemaining"
