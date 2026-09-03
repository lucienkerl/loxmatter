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

"""Synthetische Beispiel-Projektdatei fuer `projectsync`-Tests - handgebaut
nach dem in der Referenzdatei beobachteten Schema (Entwurf Abschnitt 3-6),
NICHT die echte vom Anwender gelieferte Datei (bleibt aus Datenschutzgruenden
ausserhalb des Repos, siehe Entwurf Abschnitt 9).

Enthaelt fuer Geraet 1 (``d1_...``) ein bereits bestehendes Eingangssignal
(``d1_1_onoff``, Titel weicht bewusst vom Soll ab - deckt den `updated`-Fall
ab) und ein bestehendes Ausgangssignal (``d1_1_on``). Geraet 1 hat KEIN
``d1_1_temp`` - deckt den `new_signal`-Fall ab (Container existiert, Signal
fehlt). Geraet 2 existiert in der Datei ueberhaupt nicht - deckt den
`new_device`-Fall ab. ``d9_9_verwaist`` gehoert zu keinem bekannten Geraet
mehr - deckt den `orphaned`-Fall ab."""

import pytest

SAMPLE_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="VirtualInCaption" IName="C1" U="1000-0000-0000-aaaaaaaaaaaaaaaa">\r\n'
    '\t\t<C Type="VirtualUdpIn" IName="VUI1" U="1000-0001-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Matter — Altes Geraet" WF="16384" Address="10.0.0.5" Port="7000">\r\n'
    '\t\t\t<C Type="VirtualUdpInCmd" IName="VCI1" U="1000-0002-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Alter Titel" Nio="2" WF="16384" Check="d1_1_onoff:\\v" Signed="true"'
    ' Analog="true" SourceValHigh="100" DestValHigh="100" MinVal="-10000" MaxVal="10000"'
    ' MinChange="0.25" MinTime="1000">\r\n'
    '\t\t\t\t<Co K="AQ" U="1000-0003-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t<Co K="Q" U="1000-0004-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
    "\t\t\t</C>\r\n"
    '\t\t\t<C Type="VirtualUdpInCmd" IName="VCI2" U="1000-0007-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Verwaist" Nio="2" WF="16384" Check="d9_9_verwaist:\\v" Signed="true"'
    ' Analog="true" SourceValHigh="100" DestValHigh="100" MinVal="-10000" MaxVal="10000"'
    ' MinChange="0.25" MinTime="1000">\r\n'
    '\t\t\t\t<Co K="AQ" U="1000-0008-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t<Co K="Q" U="1000-0009-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    '\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">\r\n'
    '\t\t<C Type="VirtualOut" IName="VQ1" U="1000-000b-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Matter — Altes Geraet" WF="16384" Address="http://10.0.0.9:8080"'
    ' CloseAfterSend="true" CmdSep=";">\r\n'
    '\t\t\t<C Type="VirtualOutCmd" IName="VQC1" U="1000-000c-0000-aaaaaaaaaaaaaaaa"'
    ' Title="on" Nio="1" WF="16384" CmdOn="/cmd/d1_1_on/1" CmdOnMethod="1"'
    ' SourceValHigh="10" DestValHigh="10" Tx="false">\r\n'
    '\t\t\t\t<Co K="I" U="1000-000d-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


@pytest.fixture
def sample_project() -> str:
    return SAMPLE_PROJECT
