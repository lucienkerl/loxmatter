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

"""Room list snippets for `projectsync` tests (design 2026-09-29)."""

from __future__ import annotations

# Rooms as Loxone Config writes them (design 2026-09-29, section 3): a
# `PlaceCaption` with the default room (marked `First="true"`) and one
# ordinary room, and a rights group caption with one `RightGroup` per room
# (`Place.RGR` -> `RightGroup.U`). Inserted directly under `Document`, next
# to the `LoxLIVE` block, where real files carry them.
ROOMS_XML = (
    '\t\t<C Type="PlaceCaption" V="175" U="3000-0000-0000-aaaaaaaaaaaaaaaa" Title="Räume"'
    ' WF="16384">\r\n'
    '\t\t\t<C Type="Place" V="175" U="3000-0001-0000-aaaaaaaaaaaaaaaa" Title="Nicht zugeordnet"'
    ' WF="6307840" Icon="0000015c-00ff-0000-0000000000000000" First="true" PType="2"'
    ' RGR="3000-0011-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t<C Type="Place" V="175" U="3000-0002-0000-aaaaaaaaaaaaaaaa" Title="Küche"'
    ' WF="16384" Icon="0000005a-00ff-0000-0000000000000000" Rating="1" PGroup="4" PType="3"'
    ' RGR="3000-0012-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    "\t\t</C>\r\n"
    '\t\t<C Type="LoxCaption" V="175" U="3000-0010-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Berechtigungsgruppen" Cl="0,0,0" WF="20480" CaptionType="13" SubType="13">\r\n'
    '\t\t\t<C Type="RightGroup" V="175" U="3000-0011-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Nicht zugeordnet" Cl="0,0,0" WF="16384" GT="1" MG=""/>\r\n'
    '\t\t\t<C Type="RightGroup" V="175" U="3000-0012-0000-aaaaaaaaaaaaaaaa" Title="Küche"'
    ' Cl="0,0,0" WF="16384" GT="1" MG=""/>\r\n'
    "\t\t</C>\r\n"
)

# The room list of a project from before the rights system (the 2014/2015
# files in design 2026-09-29, section 3): rooms without `RGR`, no rights
# group caption at all.
PLACES_ONLY_XML = (
    '\t\t<C Type="PlaceCaption" V="70" U="3000-0000-0000-aaaaaaaaaaaaaaaa" Title="Räume">\r\n'
    '\t\t\t<C Type="Place" V="70" U="3000-0002-0000-aaaaaaaaaaaaaaaa" Title="Küche"'
    ' Icon="00000000-0000-0002-2100000000000000"/>\r\n'
    "\t\t</C>\r\n"
)

_DOCUMENT_OPEN = 'Title="Testprojekt">\r\n'


def with_rooms(project: str, rooms_xml: str = ROOMS_XML) -> str:
    """`project` with `rooms_xml` inserted as the first children of its
    `Document`."""
    assert _DOCUMENT_OPEN in project
    return project.replace(_DOCUMENT_OPEN, _DOCUMENT_OPEN + rooms_xml, 1)
