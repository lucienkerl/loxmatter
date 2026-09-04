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

from loxmatter.export.xml import escape_attr_value, render_attrs, render_document


def test_document_starts_with_utf8_bom():
    out = render_document("VirtualInUdp", [("Title", "Test")], [])
    assert out.startswith(b"\xef\xbb\xbf")


def test_document_uses_crlf_line_endings():
    out = render_document("VirtualInUdp", [("Title", "Test")], [])
    assert b"\r\n" in out
    assert b"\n" not in out.replace(b"\r\n", b"")


def test_declaration_comes_first():
    out = render_document("VirtualInUdp", [("Title", "Test")], [])
    text = out.decode("utf-8-sig")
    assert text.splitlines()[0] == '<?xml version="1.0" encoding="utf-8"?>'


def test_loxone_value_placeholder_is_escaped():
    """Ein unescaptes <v> macht die Datei fuer Loxone Config unlesbar."""
    out = render_document(
        "VirtualOut",
        [("Title", "T")],
        [("VirtualOutCmd", [("CmdOn", "/cmd/d1_1_level/<v>")])],
    )
    text = out.decode("utf-8-sig")
    assert "&lt;v&gt;" in text
    assert "/<v>" not in text


def test_backslash_v_in_check_is_left_alone():
    """\\v ist Loxones Wertplatzhalter in der Befehlserkennung, kein XML."""
    out = render_document(
        "VirtualInUdp",
        [("Title", "T")],
        [("VirtualInUdpCmd", [("Check", "d1_1_temp:\\v")])],
    )
    assert "d1_1_temp:\\v" in out.decode("utf-8-sig")


def test_quotes_and_ampersands_are_escaped():
    out = render_document("VirtualInUdp", [("Title", 'Klaus & "Otto"')], [])
    text = out.decode("utf-8-sig")
    assert "&amp;" in text
    assert "&quot;" in text or "&#34;" in text


def test_children_are_rendered_as_self_closing_elements():
    out = render_document(
        "VirtualInUdp",
        [("Title", "T")],
        [("VirtualInUdpCmd", [("Title", "A")]), ("VirtualInUdpCmd", [("Title", "B")])],
    )
    text = out.decode("utf-8-sig")
    assert text.count("<VirtualInUdpCmd ") == 2
    assert text.count("/>") == 2
    assert text.rstrip().endswith("</VirtualInUdp>")


def test_escape_attr_value_is_importable_and_escapes_quotes():
    assert escape_attr_value('a"b&c<d>e') == "a&quot;b&amp;c&lt;d&gt;e"


def test_render_attrs_is_importable():
    assert render_attrs([("A", "1"), ("B", 'x"y')]) == 'A="1" B="x&quot;y"'
