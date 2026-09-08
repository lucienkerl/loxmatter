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

"""Synthetic sample project file for `projectsync` tests - handcrafted after
the schema observed in the reference file (draft section 3-6), NOT the real
file supplied by the user (stays outside the repo for privacy reasons, see
draft section 9).

**Correction after a real practical test (2026-09-04):** in a real file,
`<ControlList>` has exactly ONE child (`<C Type="Document">`), and every
Miniserver configured within it gets its own `<C Type="LoxLIVE">` block -
`VirtualInCaption`/`VirtualOutCaption` hang off THAT, not directly off
`ControlList` (see the `projectsync.index` module docstring for the full
derivation). This fixture now mirrors that: ONE `LoxLIVE` block
(`IntAddr="10.0.0.10"`), so all existing tests keep running without
`miniserver_ip` - a second, ambiguous case has its own, smaller fixture in
`tests/projectsync/test_index.py`.

For device 1 (``d1_...``) it contains an already existing input signal
(``d1_1_onoff``, title deliberately deviates from the target - covers the
`updated` case), the associated online signal (``d1_online`` -
`export.signals.to_inputs` automatically generates this signal for EVERY
device, see the docstring there; without a matching entry here, any diff
plan for device 1 would never be `unchanged`, even if all other signals
match) and an existing output signal (``d1_1_on``). Device 1 has NO
``d1_1_temp`` - covers the `new_signal` case (container exists, signal
missing). Device 2 does not exist in the file at all - covers the
`new_device` case. ``d9_9_verwaist`` no longer belongs to any known device -
covers the `orphaned` case."""

import pytest

from loxmatter import i18n


@pytest.fixture(autouse=True)
def _sample_project_uses_german(reset_language: None) -> None:
    """`SAMPLE_PROJECT`'s ``d1_online`` title
    ("Altes Geraet erreichbar") is hard-coded German in this sample file
    (see the module
    docstring); `export.signals.to_inputs` has generated the same title
    language-dependently via `i18n.t()` since i18n phase B+C, and without
    this fixture-wide German context it falls back to the new English
    default ("... reachable") - every actually unchanged diff plan would
    then falsely look like an `updated` case. Explicitly depends on
    `reset_language` (instead of relying on fixture ordering) so that this
    test folder reliably runs AFTER the global reset."""
    i18n.set_language("de")


SAMPLE_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="Document" U="2000-0000-0000-aaaaaaaaaaaaaaaa" Title="Testprojekt">\r\n'
    '\t\t<C Type="LoxLIVE" U="2000-0001-0000-aaaaaaaaaaaaaaaa" Title="Testserver"'
    ' IntAddr="10.0.0.10" Serial="504F00000000">\r\n'
    '\t\t\t<C Type="VirtualInCaption" IName="C1" U="1000-0000-0000-aaaaaaaaaaaaaaaa">\r\n'
    '\t\t\t\t<C Type="VirtualUdpIn" IName="VUI1" U="1000-0001-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Matter — Altes Geraet" WF="16384" Address="10.0.0.5" Port="7000">\r\n'
    '\t\t\t\t\t<C Type="VirtualUdpInCmd" IName="VCI1" U="1000-0002-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Alter Titel" Nio="2" WF="16384" Check="d1_1_onoff:\\v" Signed="true"'
    ' Analog="true" SourceValHigh="100" DestValHigh="100" MinVal="-10000" MaxVal="10000"'
    ' MinChange="0.25" MinTime="1000">\r\n'
    '\t\t\t\t\t\t<Co K="AQ" U="1000-0003-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t\t\t<Co K="Q" U="1000-0004-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
    "\t\t\t\t\t</C>\r\n"
    '\t\t\t\t\t<C Type="VirtualUdpInCmd" IName="VCI3" U="1000-000e-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Altes Geraet erreichbar" Nio="2" WF="16384" Check="d1_online:\\v" Signed="true"'
    ' Analog="true" SourceValHigh="100" DestValHigh="100" MinVal="-10000" MaxVal="10000"'
    ' MinChange="0.25" MinTime="1000">\r\n'
    '\t\t\t\t\t\t<Co K="AQ" U="1000-000f-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t\t\t<Co K="Q" U="1000-0010-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
    "\t\t\t\t\t</C>\r\n"
    '\t\t\t\t\t<C Type="VirtualUdpInCmd" IName="VCI2" U="1000-0007-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Verwaist" Nio="2" WF="16384" Check="d9_9_verwaist:\\v" Signed="true"'
    ' Analog="true" SourceValHigh="100" DestValHigh="100" MinVal="-10000" MaxVal="10000"'
    ' MinChange="0.25" MinTime="1000">\r\n'
    '\t\t\t\t\t\t<Co K="AQ" U="1000-0008-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t\t\t<Co K="Q" U="1000-0009-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
    "\t\t\t\t\t</C>\r\n"
    "\t\t\t\t</C>\r\n"
    "\t\t\t</C>\r\n"
    '\t\t\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">\r\n'
    '\t\t\t\t<C Type="VirtualOut" IName="VQ1" U="1000-000b-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Matter — Altes Geraet" WF="16384" Address="http://10.0.0.9:8080"'
    ' CloseAfterSend="true" CmdSep=";">\r\n'
    '\t\t\t\t\t<C Type="VirtualOutCmd" IName="VQC1" U="1000-000c-0000-aaaaaaaaaaaaaaaa"'
    ' Title="on" Nio="1" WF="16384" CmdOn="/cmd/d1_1_on/1" CmdOnMethod="1"'
    ' SourceValHigh="10" DestValHigh="10" Tx="false">\r\n'
    '\t\t\t\t\t\t<Co K="I" U="1000-000d-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
    "\t\t\t\t\t</C>\r\n"
    "\t\t\t\t</C>\r\n"
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


@pytest.fixture
def sample_project() -> str:
    return SAMPLE_PROJECT
