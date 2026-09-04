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

"""Liest den von `loxmatter` selbst vergebenen Signal-/Kommando-Schluessel
aus den Feldern, in denen er in der Projektdatei bereits steht (Entwurf
Abschnitt 3.3) - `Check` bei Eingaengen, `CmdOn` bei Ausgaengen. Das ist
derselbe Schluessel, den `model.store._assign_key` vergibt und den
`export.documents`/`export.outputs` in genau diese Felder schreiben."""

from __future__ import annotations

_CMD_PREFIX = "/cmd/"


def key_from_check(check: str) -> str | None:
    """Der Teil vor dem ersten Doppelpunkt in einem Check-Muster, z. B.
    ``"d3_1_onoff:\\v"`` -> ``"d3_1_onoff"``. `None` ohne Doppelpunkt - dann
    stammt das Muster nicht von `loxmatter` (siehe `render_virtual_in_udp`,
    das `Check` immer als ``f"{key}:{suffix}"`` schreibt)."""
    if ":" not in check:
        return None
    return check.split(":", 1)[0]


def key_from_cmd_on(cmd_on: str) -> str | None:
    """Der Schluessel aus einem von `loxmatter` erzeugten Kommandopfad, z. B.
    ``"/cmd/d3_1_onoff/1"`` -> ``"d3_1_onoff"``. `None` fuer jeden Pfad, der
    nicht mit ``/cmd/`` beginnt - das ist der Marker, an dem sich eigene
    Ausgangsbefehle von allen anderen (``/toggle``, ``/write?db=...``)
    unterscheiden (siehe `export.outputs._command_path`)."""
    if not cmd_on.startswith(_CMD_PREFIX):
        return None
    rest = cmd_on[len(_CMD_PREFIX) :]
    key = rest.split("/", 1)[0]
    return key or None
