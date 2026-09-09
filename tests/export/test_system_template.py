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

from loxmatter.export.documents import render_system_templates


def text(raw: bytes) -> str:
    return raw.decode("utf-8-sig")


def test_input_template_carries_the_heartbeat_as_an_analog_value():
    """Analog, not digital (2026-09-03, clarified on the Miniserver): the
    watchdog relies on the value ALTERNATING between 1 and 0. A digital UDP
    input does not evaluate the value - it would only see that a pattern
    matches, and could not notice the change at all. But that is exactly
    what it must: if the value stops changing, the bridge is dead."""
    viu, _ = render_system_templates("192.168.1.50", 7000, 8080)
    assert 'Check="bridge_alive:\\v"' in text(viu)
    assert 'Analog="true"' in text(viu)


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
    """They belong to no device - a d<id>_ prefix would be wrong."""
    viu, vo = render_system_templates("192.168.1.50", 7000, 8080)
    assert "d1_" not in text(viu)
    assert "d1_" not in text(vo)


def test_output_address_uses_the_given_listen_port():
    """Review-Fix I3, 2026-09-02: previously the HTTP port here was hardwired
    to 8080, regardless of which `--listen` `loxmatter run` actually starts
    with - a differing port let `/resync` run into the void without any
    error message."""
    _, vo = render_system_templates("192.168.1.50", 7000, 9090)
    assert 'Address="http://192.168.1.50:9090"' in text(vo)
    assert "8080" not in text(vo)
