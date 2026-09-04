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

"""Baut aus dem Byte-Span-Baum (`projectsync.scan`) einen nach `loxmatter`-
Schluesseln durchsuchbaren Index: welche virtuellen Eingaenge/Ausgaenge gibt
es schon, und in welchem Geraete-Container stecken sie (Entwurf Abschnitt
3.3/5)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from loxmatter.projectsync.keys import key_from_check, key_from_cmd_on
from loxmatter.projectsync.scan import Element, ProjectFormatError, parse_root, scan_children

__all__ = ["ProjectFormatError", "ProjectIndex", "build_index"]

_U_ATTR = re.compile(r'\bU="([^"]*)"')
_INAME_ATTR = re.compile(r'\bIName="([^"]*)"')


@dataclass
class ProjectIndex:
    text: str
    root_attrs: dict[str, str]
    root_open_end: int
    root_close_start: int
    virtual_in_caption: Element | None
    virtual_out_caption: Element | None
    input_cmds: dict[str, Element]
    output_cmds: dict[str, Element]
    input_containers: dict[str, Element]
    output_containers: dict[str, Element]
    all_u_values: set[str]
    all_inames: set[str]


def build_index(text: str) -> ProjectIndex:
    root_attrs, _root_open_start, root_open_end, root_close_start = parse_root(text)
    top_level = scan_children(text, root_open_end, root_close_start)

    virtual_in_caption = next((e for e in top_level if e.type == "VirtualInCaption"), None)
    virtual_out_caption = next((e for e in top_level if e.type == "VirtualOutCaption"), None)

    input_cmds: dict[str, Element] = {}
    input_containers: dict[str, Element] = {}
    if virtual_in_caption is not None:
        for container in virtual_in_caption.children:
            if container.type != "VirtualUdpIn":
                continue
            for cmd in container.children:
                if cmd.type != "VirtualUdpInCmd":
                    continue
                key = key_from_check(cmd.attrs.get("Check", ""))
                if key is not None:
                    input_cmds[key] = cmd
                    input_containers[key] = container

    output_cmds: dict[str, Element] = {}
    output_containers: dict[str, Element] = {}
    if virtual_out_caption is not None:
        for container in virtual_out_caption.children:
            if container.type != "VirtualOut":
                continue
            for cmd in container.children:
                if cmd.type != "VirtualOutCmd":
                    continue
                key = key_from_cmd_on(cmd.attrs.get("CmdOn", ""))
                if key is not None:
                    output_cmds[key] = cmd
                    output_containers[key] = container

    return ProjectIndex(
        text=text,
        root_attrs=root_attrs,
        root_open_end=root_open_end,
        root_close_start=root_close_start,
        virtual_in_caption=virtual_in_caption,
        virtual_out_caption=virtual_out_caption,
        input_cmds=input_cmds,
        output_cmds=output_cmds,
        input_containers=input_containers,
        output_containers=output_containers,
        # Ueber den gesamten Rohtext, nicht nur ueber <C>-Elemente: <Co>-
        # Verdrahtungsstummel tragen ebenfalls U-IDs, die eine neu erzeugte
        # ID nicht kollidieren duerfen (Entwurf Abschnitt 6).
        all_u_values=set(_U_ATTR.findall(text)),
        all_inames=set(_INAME_ATTR.findall(text)),
    )
