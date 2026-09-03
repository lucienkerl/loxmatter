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

"""Attributpfade von matter-server parsen.

matter-server adressiert Attribute als "<endpoint>/<cluster>/<attribute>",
z.B. "1/6/0" für OnOff.OnOff auf Endpoint 1.
"""

from __future__ import annotations

# Globale Attribute nach Matter-Spezifikation. Sie beschreiben das Gerät,
# statt einen Messwert zu tragen, und werden nicht zu Loxone-Signalen.
GENERATED_COMMAND_LIST_ID = 0xFFF8
ACCEPTED_COMMAND_LIST_ID = 0xFFF9
EVENT_LIST_ID = 0xFFFA
ATTRIBUTE_LIST_ID = 0xFFFB
FEATURE_MAP_ID = 0xFFFC
CLUSTER_REVISION_ID = 0xFFFD

GLOBAL_ATTRIBUTE_IDS: frozenset[int] = frozenset(
    {
        GENERATED_COMMAND_LIST_ID,
        ACCEPTED_COMMAND_LIST_ID,
        EVENT_LIST_ID,
        ATTRIBUTE_LIST_ID,
        FEATURE_MAP_ID,
        CLUSTER_REVISION_ID,
    }
)


def parse_attribute_path(path: str) -> tuple[int, int, int]:
    """Zerlegt "1/6/0" in (endpoint, cluster_id, attribute_id)."""
    parts = path.split("/")
    if len(parts) != 3:
        raise ValueError(f"unerwarteter Attributpfad: {path!r}")
    try:
        endpoint, cluster_id, attribute_id = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError(f"unerwarteter Attributpfad: {path!r}") from exc
    return endpoint, cluster_id, attribute_id
