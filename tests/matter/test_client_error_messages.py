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

"""Tests fuer die uebersetzten MatterUnavailableError/CommissioningError-
Texte in matter/client.py - nur die Texte, die die einfach zu erreichenden
Zweige betreffen (kein echtes matter-server noetig)."""

from __future__ import annotations

import pytest

from loxmatter import i18n
from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError


async def test_require_upstream_error_is_english_by_default():
    client = BridgeMatterClient("ws://example.invalid/ws")
    with pytest.raises(MatterUnavailableError, match="not connected to matter-server"):
        await client.snapshots()


async def test_require_upstream_error_is_german_when_set():
    i18n.set_language("de")
    client = BridgeMatterClient("ws://example.invalid/ws")
    with pytest.raises(MatterUnavailableError, match="nicht verbunden mit matter-server"):
        await client.snapshots()


async def test_snapshot_of_unknown_node_is_english_by_default():
    client = BridgeMatterClient("ws://example.invalid/ws")

    class _FakeUpstream:
        def get_nodes(self):
            return []

    client._upstream = _FakeUpstream()  # bypasses connect() for this narrow test
    with pytest.raises(MatterUnavailableError, match="unknown node 42"):
        await client.snapshot(42)
