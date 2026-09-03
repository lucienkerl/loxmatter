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

from loxmatter.export.documents import render_system_templates


def text(raw: bytes) -> str:
    return raw.decode("utf-8-sig")


def test_input_template_carries_the_heartbeat():
    viu, _ = render_system_templates("192.168.1.50", 7000, 8080)
    assert 'Check="bridge_alive:\\v"' in text(viu)
    assert 'Analog="false"' in text(viu)


def test_output_template_carries_resync():
    _, vo = render_system_templates("192.168.1.50", 7000, 8080)
    assert 'CmdOn="/resync"' in text(vo)


def test_both_templates_have_the_info_element_first():
    for raw in render_system_templates("192.168.1.50", 7000, 8080):
        assert text(raw).split(">", 2)[2].lstrip().startswith("<Info ")


def test_both_are_utf8_with_bom_and_crlf():
    for raw in render_system_templates("192.168.1.50", 7000, 8080):
        assert raw.startswith(b"\xef\xbb\xbf")
        assert b"\n" not in raw.replace(b"\r\n", b"")


def test_system_templates_carry_no_device_prefix():
    """Sie gehoeren zu keinem Geraet - ein d<id>_ waere falsch."""
    viu, vo = render_system_templates("192.168.1.50", 7000, 8080)
    assert "d1_" not in text(viu)
    assert "d1_" not in text(vo)


def test_output_address_uses_the_given_listen_port():
    """Review-Fix I3, 2026-09-02: vorher war der HTTP-Port hier fest auf 8080
    verdrahtet, unabhaengig davon, mit welchem `--listen` `loxmatter run`
    tatsaechlich startet - ein abweichender Port liess `/resync` ohne
    jede Fehlermeldung ins Leere laufen."""
    _, vo = render_system_templates("192.168.1.50", 7000, 9090)
    assert 'Address="http://192.168.1.50:9090"' in text(vo)
    assert "8080" not in text(vo)
