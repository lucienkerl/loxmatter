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

"""Checks the output shape against real Loxone templates.

The reference files under ``tests/fixtures/loxone/`` are sanitized
derivatives of a real Loxone Config installation (see
``tests/fixtures/VirtualIn/`` and ``VirtualOut/``, which are therefore
.gitignored). The first block checks only the file shape, not the content:
BOM, line endings, and the first line must match what
``loxmatter.export.xml`` produces.

The second block (Review-Fix Important #1) goes further: it pins the
actually exported attribute set of each of the four element types - name
*and* order *and* count - against the same reference files. Without that, a
swapped, missing, or extra attribute column in ``documents.py`` would go
unnoticed by any of the 168 existing tests, even though that is exactly the
class of deviation this phase is meant to prevent (see correction
2026-09-02 in Spec 6.1).

The third block (Review-Fix Minor #2, 2026-09-02) pins the same shape for
``render_system_templates``. So far the guarantee above only covers that
function too because it is currently a pure pass-through to
``render_virtual_in_udp``/``render_virtual_out`` - that would no longer hold
once it gets a second input or output.
"""

import re
from pathlib import Path

import pytest

from loxmatter.export.documents import (
    LoxoneCommand,
    render_system_templates,
    render_virtual_in_udp,
    render_virtual_out,
)
from loxmatter.export.signals import LoxoneInput
from loxmatter.export.xml import DECLARATION

FIXTURES = Path(__file__).parents[1] / "fixtures" / "loxone"
FIXTURE_FILES = sorted(FIXTURES.glob("*.xml"))


def test_fixture_directory_is_not_empty():
    """Guard: an emptied-out template folder must not let the tests below
    silently pass."""
    assert FIXTURE_FILES, f"No *.xml templates found under {FIXTURES}"


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_reference_file_starts_with_utf8_bom(path: Path):
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_reference_file_uses_pure_crlf_line_endings(path: Path):
    raw = path.read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.name)
def test_reference_file_first_line_is_the_declaration(path: Path):
    text = path.read_bytes().decode("utf-8-sig")
    assert text.splitlines()[0] == DECLARATION


# -- Attribute set and order (Review-Fix Important #1) ---------------------


def _attr_names(line: str) -> tuple[str, ...]:
    """Extracts the attribute names of an XML line in document order."""
    return tuple(re.findall(r'(\w+)="', line))


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8-sig").splitlines()


def _first_matching(lines: list[str], prefix: str) -> str:
    return next(line.strip() for line in lines if line.strip().startswith(prefix))


def _rendered_viu_lines() -> list[str]:
    inputs = [LoxoneInput("d1_1_temp", "Temperatur", "Wohnzimmer · 1/1026/0", True, "<v.1> °C")]
    raw = render_virtual_in_udp("Wohnzimmerlampe", "192.168.1.50", 7000, inputs)
    return raw.decode("utf-8-sig").splitlines()


def _rendered_vo_lines() -> list[str]:
    commands = [LoxoneCommand("d1_1_onoff", "Schalten", "/cmd/d1_1_onoff/1", False)]
    raw = render_virtual_out("Wohnzimmerlampe", "http://192.168.1.50:8080", commands)
    return raw.decode("utf-8-sig").splitlines()


def test_virtual_in_udp_root_attribute_set_and_order_matches_the_reference():
    reference = _first_matching(_lines(FIXTURES / "VIU_reference.xml"), "<VirtualInUdp ")
    rendered = _first_matching(_rendered_viu_lines(), "<VirtualInUdp ")
    assert _attr_names(rendered) == _attr_names(reference)


def test_virtual_in_udp_cmd_attribute_set_and_order_matches_the_reference():
    reference = _first_matching(_lines(FIXTURES / "VIU_reference.xml"), "<VirtualInUdpCmd ")
    rendered = _first_matching(_rendered_viu_lines(), "<VirtualInUdpCmd ")
    assert _attr_names(rendered) == _attr_names(reference)


def test_virtual_out_root_attribute_set_and_order_matches_the_reference():
    reference = _first_matching(_lines(FIXTURES / "VO_working.xml"), "<VirtualOut ")
    rendered = _first_matching(_rendered_vo_lines(), "<VirtualOut ")
    assert _attr_names(rendered) == _attr_names(reference)


def test_virtual_out_cmd_attribute_set_and_order_matches_the_reference():
    reference = _first_matching(_lines(FIXTURES / "VO_working.xml"), "<VirtualOutCmd ")
    rendered = _first_matching(_rendered_vo_lines(), "<VirtualOutCmd ")
    assert _attr_names(rendered) == _attr_names(reference)


def test_info_element_is_the_first_child_in_the_virtual_in_udp_reference_and_output():
    reference_children = [line.strip() for line in _lines(FIXTURES / "VIU_reference.xml")[2:-1]]
    rendered_children = [line.strip() for line in _rendered_viu_lines()[2:-1]]
    assert reference_children[0].startswith("<Info ")
    assert rendered_children[0].startswith("<Info ")


def test_info_element_is_the_first_child_in_the_virtual_out_reference_and_output():
    reference_children = [line.strip() for line in _lines(FIXTURES / "VO_working.xml")[2:-1]]
    rendered_children = [line.strip() for line in _rendered_vo_lines()[2:-1]]
    assert reference_children[0].startswith("<Info ")
    assert rendered_children[0].startswith("<Info ")


# -- Same pinning for render_system_templates (Review-Fix Minor #2) --------


def _rendered_system_viu_lines() -> list[str]:
    viu_sys, _vo_sys = render_system_templates("192.168.1.50", 7000, 8080)
    return viu_sys.decode("utf-8-sig").splitlines()


def _rendered_system_vo_lines() -> list[str]:
    _viu_sys, vo_sys = render_system_templates("192.168.1.50", 7000, 8080)
    return vo_sys.decode("utf-8-sig").splitlines()


def test_system_virtual_in_udp_matches_the_reference_shape():
    reference_lines = _lines(FIXTURES / "VIU_reference.xml")
    rendered_lines = _rendered_system_viu_lines()
    reference_root = _first_matching(reference_lines, "<VirtualInUdp ")
    rendered_root = _first_matching(rendered_lines, "<VirtualInUdp ")
    reference_cmd = _first_matching(reference_lines, "<VirtualInUdpCmd ")
    rendered_cmd = _first_matching(rendered_lines, "<VirtualInUdpCmd ")
    assert _attr_names(rendered_root) == _attr_names(reference_root)
    assert _attr_names(rendered_cmd) == _attr_names(reference_cmd)
    assert rendered_lines[2:-1][0].strip().startswith("<Info ")


def test_system_virtual_out_matches_the_reference_shape():
    reference_lines = _lines(FIXTURES / "VO_working.xml")
    rendered_lines = _rendered_system_vo_lines()
    reference_root = _first_matching(reference_lines, "<VirtualOut ")
    rendered_root = _first_matching(rendered_lines, "<VirtualOut ")
    reference_cmd = _first_matching(reference_lines, "<VirtualOutCmd ")
    rendered_cmd = _first_matching(rendered_lines, "<VirtualOutCmd ")
    assert _attr_names(rendered_root) == _attr_names(reference_root)
    assert _attr_names(rendered_cmd) == _attr_names(reference_cmd)
    assert rendered_lines[2:-1][0].strip().startswith("<Info ")


def _attr(line: str, name: str) -> str:
    match = re.search(rf'{name}="([^"]*)"', line)
    assert match, f"{name} missing in {line!r}"
    return match.group(1)


def test_the_analog_flag_hangs_on_the_off_command_not_on_the_value():
    """The rule we got wrong twice - now backed by a template that Loxone
    Config itself wrote after a working import (`VO_working.xml`,
    supplied by the user, 2026-09-03).

    `Analog="false"` sits exactly where an off command is set: that is the
    digital output, where Config sets the "Use as digital output" checkbox
    and only then offers the field for the off command at all. An output
    with only one command carries `Analog="true"` - even when it takes no
    value.

    So it hinges on the OFF COMMAND, not on whether the command expects a
    value. Both earlier versions tied it to the value, once directly and
    once inverted, and both times `CmdOff` stayed without effect.
    """
    gold = {
        _attr(line, "Title"): line
        for line in _lines(FIXTURES / "VO_working.xml")
        if "<VirtualOutCmd " in line
    }
    assert _attr(gold["onoff"], "Analog") == "false"
    assert _attr(gold["onoff"], "CmdOff") == "/cmd/d1_1_off/1"
    for single in ("on", "off", "toggle"):
        assert _attr(gold[single], "Analog") == "true"
        assert _attr(gold[single], "CmdOff") == ""

    paired = LoxoneCommand("d1_1_on", "onoff", "/cmd/d1_1_on/1", False, off_path="/cmd/d1_1_off/1")
    single = LoxoneCommand("d1_1_toggle", "toggle", "/cmd/d1_1_toggle/1", False)
    valued = LoxoneCommand("d1_1_level", "level", "/cmd/d1_1_level/<v>", True)
    lines = {
        _attr(line, "Title"): line
        for line in render_virtual_out(
            "Steckdose", "http://192.168.1.50:8080", [paired, single, valued]
        )
        .decode("utf-8-sig")
        .splitlines()
        if "<VirtualOutCmd " in line
    }
    assert _attr(lines["onoff"], "Analog") == "false"
    assert _attr(lines["toggle"], "Analog") == "true"
    assert _attr(lines["level"], "Analog") == "true"


def test_the_scaling_attributes_appear_only_on_the_analog_outputs():
    """Config writes SourceValLow/DestValLow/SourceValHigh/DestValHigh for
    every output WITHOUT an off command and omits them entirely for the
    digital one."""
    scaling = ("SourceValLow", "DestValLow", "SourceValHigh", "DestValHigh")
    gold = {
        _attr(line, "Title"): line
        for line in _lines(FIXTURES / "VO_working.xml")
        if "<VirtualOutCmd " in line
    }
    for name in scaling:
        assert f'{name}="' not in gold["onoff"]
        assert f'{name}="0"' in gold["toggle"]

    paired = LoxoneCommand("d1_1_on", "onoff", "/cmd/d1_1_on/1", False, off_path="/cmd/d1_1_off/1")
    single = LoxoneCommand("d1_1_toggle", "toggle", "/cmd/d1_1_toggle/1", False)
    lines = {
        _attr(line, "Title"): line
        for line in render_virtual_out("Steckdose", "http://192.168.1.50:8080", [paired, single])
        .decode("utf-8-sig")
        .splitlines()
        if "<VirtualOutCmd " in line
    }
    for name in scaling:
        assert f'{name}="' not in lines["onoff"]
        assert f'{name}="0"' in lines["toggle"]


def test_every_command_matches_what_config_wrote_attribute_for_attribute():
    """The test that would have caught both bugs together. The older
    reference test only compared the NAMES of the attributes and their
    order - a golden-file check that only tests the shape lets through
    exactly the meaning it was meant to catch."""
    gold = {
        _attr(line, "Title"): _attr_pairs(line)
        for line in _lines(FIXTURES / "VO_working.xml")
        if "<VirtualOutCmd " in line
    }
    commands = [
        LoxoneCommand("d1_1_off", "off", "/cmd/d1_1_off/1", False),
        LoxoneCommand("d1_1_on", "onoff", "/cmd/d1_1_on/1", False, off_path="/cmd/d1_1_off/1"),
        LoxoneCommand("d1_1_on", "on", "/cmd/d1_1_on/1", False),
        LoxoneCommand("d1_1_toggle", "toggle", "/cmd/d1_1_toggle/1", False),
    ]
    rendered = {
        _attr(line, "Title"): _attr_pairs(line)
        for line in render_virtual_out(
            "IKEA of Sweden GRILLPLATS Plug", "http://10.0.1.56:8080", commands
        )
        .decode("utf-8-sig")
        .splitlines()
        if "<VirtualOutCmd " in line
    }
    for title, gold_attributes in gold.items():
        ours = dict(rendered[title])
        # `Comment` carries the key on our side; Config adopted it when
        # writing it back - except for the combined output, where our
        # comment names both keys.
        assert [k for k, _ in rendered[title]] == [k for k, _ in gold_attributes], title
        for name, value in gold_attributes:
            if name == "Comment":
                continue
            assert ours[name] == value, f"{title}.{name}"


def _attr_pairs(line: str) -> list[tuple[str, str]]:
    return re.findall(r'(\w+)="([^"]*)"', line)
