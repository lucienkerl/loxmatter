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
Abschnitt 3.3) - `Check` bei Eingaengen, `CmdOn`/`CmdOff` bei Ausgaengen. Das
ist derselbe Schluessel, den `model.store._assign_key` vergibt und den
`export.documents`/`export.outputs` in genau diese Felder schreiben."""

from __future__ import annotations

from collections.abc import Mapping

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


def key_from_output_cmd(attrs: Mapping[str, str]) -> str | None:
    """Der Schluessel eines bestehenden `VirtualOutCmd` - aus `CmdOn` UND
    `CmdOff` zusammen, nicht aus `CmdOn` allein.

    Der Grund ist der kombinierte Ein/Aus-Ausgang aus `export.outputs.
    to_outputs`: er schickt bei steigender Flanke denselben Pfad wie der
    einzelne `on`-Befehl (`/cmd/d1_1_on/1`) und unterscheidet sich von ihm
    allein durch sein `CmdOff`. Aus `CmdOn` allein gelesen bekaemen beide
    denselben Schluessel - im Index ueberschriebe dann einer den anderen,
    und der kombinierte Befehl waere unter seinem echten Schluessel
    (``"d1_1_on + d1_1_off"``, so vergibt ihn `to_outputs`) nirgends zu
    finden. Genau das war der Anwenderbericht "nach Export und erneutem
    Import ein neues Feld onoff": bei jedem Durchlauf eine weitere Dublette,
    weil der Abgleich seine eigene Ausgabe nicht wiedererkannte.

    Ein `CmdOff`, das nicht von `loxmatter` stammt (oder fehlt/leer ist),
    aendert den Schluessel nicht - dann zaehlt der Ein-Befehl allein."""
    on = key_from_cmd_on(attrs.get("CmdOn", ""))
    if on is None:
        return None
    off = key_from_cmd_on(attrs.get("CmdOff", ""))
    return f"{on} + {off}" if off is not None else on
