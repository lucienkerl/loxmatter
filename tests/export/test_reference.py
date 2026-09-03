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

"""Prueft die Ausgabeform gegen echte Loxone-Vorlagen.

Die Referenzdateien unter ``tests/fixtures/loxone/`` sind bereinigte
Ableitungen aus einer echten Loxone-Config-Installation (siehe
``tests/fixtures/VirtualIn/`` und ``VirtualOut/``, die deshalb .gitignored
bleiben). Der erste Block prueft nur die Dateiform, nicht den Inhalt: BOM,
Zeilenenden und die erste Zeile muessen zu dem passen, was
``loxmatter.export.xml`` erzeugt.

Der zweite Block (Review-Fix Important #1) geht weiter: er pinnt das
tatsaechlich exportierte Attributset jedes der vier Elementtypen —
Name *und* Reihenfolge *und* Anzahl — gegen dieselben Referenzdateien. Ohne
das wuerde eine vertauschte, fehlende oder zusaetzliche Attribut-Spalte in
``documents.py`` von keinem der bisherigen 168 Tests bemerkt, obwohl genau
das die Abweichungsklasse ist, die diese Phase verhindern soll (siehe
Korrektur 2026-09-02 in Spec 6.1).

Der dritte Block (Review-Fix Minor #2, 2026-09-02) pinnt dieselbe Form fuer
``render_system_templates``. Die Garantie oben gilt bislang nur fuer diese
Funktion mit, weil sie heute ein reiner Durchreicher zu
``render_virtual_in_udp``/``render_virtual_out`` ist — das wuerde nicht mehr
gelten, sobald sie einen zweiten Eingang oder Ausgang bekommt.
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
    """Waechter: eine leer geraeumte Vorlagenmappe darf die Tests unten nicht
    stillschweigend bestehen lassen."""
    assert FIXTURE_FILES, f"Keine *.xml-Vorlagen gefunden unter {FIXTURES}"


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


# -- Attributset und Reihenfolge (Review-Fix Important #1) -----------------


def _attr_names(line: str) -> tuple[str, ...]:
    """Extrahiert die Attributnamen einer XML-Zeile in Dokumentreihenfolge."""
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
    reference = _first_matching(_lines(FIXTURES / "VIU_Referenz.xml"), "<VirtualInUdp ")
    rendered = _first_matching(_rendered_viu_lines(), "<VirtualInUdp ")
    assert _attr_names(rendered) == _attr_names(reference)


def test_virtual_in_udp_cmd_attribute_set_and_order_matches_the_reference():
    reference = _first_matching(_lines(FIXTURES / "VIU_Referenz.xml"), "<VirtualInUdpCmd ")
    rendered = _first_matching(_rendered_viu_lines(), "<VirtualInUdpCmd ")
    assert _attr_names(rendered) == _attr_names(reference)


def test_virtual_out_root_attribute_set_and_order_matches_the_reference():
    reference = _first_matching(_lines(FIXTURES / "VO_Funktionierend.xml"), "<VirtualOut ")
    rendered = _first_matching(_rendered_vo_lines(), "<VirtualOut ")
    assert _attr_names(rendered) == _attr_names(reference)


def test_virtual_out_cmd_attribute_set_and_order_matches_the_reference():
    reference = _first_matching(_lines(FIXTURES / "VO_Funktionierend.xml"), "<VirtualOutCmd ")
    rendered = _first_matching(_rendered_vo_lines(), "<VirtualOutCmd ")
    assert _attr_names(rendered) == _attr_names(reference)


def test_info_element_is_the_first_child_in_the_virtual_in_udp_reference_and_output():
    reference_children = [line.strip() for line in _lines(FIXTURES / "VIU_Referenz.xml")[2:-1]]
    rendered_children = [line.strip() for line in _rendered_viu_lines()[2:-1]]
    assert reference_children[0].startswith("<Info ")
    assert rendered_children[0].startswith("<Info ")


def test_info_element_is_the_first_child_in_the_virtual_out_reference_and_output():
    reference_children = [line.strip() for line in _lines(FIXTURES / "VO_Funktionierend.xml")[2:-1]]
    rendered_children = [line.strip() for line in _rendered_vo_lines()[2:-1]]
    assert reference_children[0].startswith("<Info ")
    assert rendered_children[0].startswith("<Info ")


# -- Dieselbe Pinnung fuer render_system_templates (Review-Fix Minor #2) ---


def _rendered_system_viu_lines() -> list[str]:
    viu_sys, _vo_sys = render_system_templates("192.168.1.50", 7000, 8080)
    return viu_sys.decode("utf-8-sig").splitlines()


def _rendered_system_vo_lines() -> list[str]:
    _viu_sys, vo_sys = render_system_templates("192.168.1.50", 7000, 8080)
    return vo_sys.decode("utf-8-sig").splitlines()


def test_system_virtual_in_udp_matches_the_reference_shape():
    reference_lines = _lines(FIXTURES / "VIU_Referenz.xml")
    rendered_lines = _rendered_system_viu_lines()
    reference_root = _first_matching(reference_lines, "<VirtualInUdp ")
    rendered_root = _first_matching(rendered_lines, "<VirtualInUdp ")
    reference_cmd = _first_matching(reference_lines, "<VirtualInUdpCmd ")
    rendered_cmd = _first_matching(rendered_lines, "<VirtualInUdpCmd ")
    assert _attr_names(rendered_root) == _attr_names(reference_root)
    assert _attr_names(rendered_cmd) == _attr_names(reference_cmd)
    assert rendered_lines[2:-1][0].strip().startswith("<Info ")


def test_system_virtual_out_matches_the_reference_shape():
    reference_lines = _lines(FIXTURES / "VO_Funktionierend.xml")
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
    assert match, f"{name} fehlt in {line!r}"
    return match.group(1)


def test_the_analog_flag_hangs_on_the_off_command_not_on_the_value():
    """Die Regel, die uns zweimal falsch war - jetzt aus einer Vorlage
    belegt, die Loxone Config nach einem funktionierenden Import selbst
    geschrieben hat (`VO_Funktionierend.xml`, vom Anwender geliefert,
    2026-09-03).

    `Analog="false"` steht genau dort, wo ein Aus-Befehl gesetzt ist: das
    ist der digitale Ausgang, bei dem Config den Haken "Als Digitalausgang
    verwenden" setzt und das Feld fuer den Aus-Befehl ueberhaupt erst
    anbietet. Ein Ausgang mit nur einem Befehl traegt `Analog="true"` - auch
    dann, wenn er keinen Wert nimmt.

    Es haengt also am AUS-BEFEHL, nicht daran, ob das Kommando einen Wert
    erwartet. Beide frueheren Fassungen banden es an den Wert, einmal
    direkt und einmal invertiert, und beide Male blieb `CmdOff` wirkungslos.
    """
    gold = {
        _attr(line, "Title"): line
        for line in _lines(FIXTURES / "VO_Funktionierend.xml")
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
    """Config schreibt SourceValLow/DestValLow/SourceValHigh/DestValHigh bei
    jedem Ausgang OHNE Aus-Befehl und laesst sie beim digitalen ganz weg."""
    scaling = ("SourceValLow", "DestValLow", "SourceValHigh", "DestValHigh")
    gold = {
        _attr(line, "Title"): line
        for line in _lines(FIXTURES / "VO_Funktionierend.xml")
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
    """Der Test, der die beiden Fehler zusammen gefangen haette. Der aeltere
    Referenztest verglich nur die NAMEN der Attribute und ihre Reihenfolge -
    eine Golden-File-Pruefung, die nur die Form prueft, laesst genau die
    Bedeutung durch, fuer die man sie hat."""
    gold = {
        _attr(line, "Title"): _attr_pairs(line)
        for line in _lines(FIXTURES / "VO_Funktionierend.xml")
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
        # `Comment` traegt bei uns den Schluessel, Config hat ihn beim
        # Zurueckschreiben uebernommen - ausser beim kombinierten Ausgang,
        # wo unser Kommentar beide Schluessel nennt.
        assert [k for k, _ in rendered[title]] == [k for k, _ in gold_attributes], title
        for name, value in gold_attributes:
            if name == "Comment":
                continue
            assert ours[name] == value, f"{title}.{name}"


def _attr_pairs(line: str) -> list[tuple[str, str]]:
    return re.findall(r'(\w+)="([^"]*)"', line)
