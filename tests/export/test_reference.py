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
    reference = _first_matching(_lines(FIXTURES / "VO_Referenz.xml"), "<VirtualOut ")
    rendered = _first_matching(_rendered_vo_lines(), "<VirtualOut ")
    assert _attr_names(rendered) == _attr_names(reference)


def test_virtual_out_cmd_attribute_set_and_order_matches_the_reference():
    reference = _first_matching(_lines(FIXTURES / "VO_Referenz.xml"), "<VirtualOutCmd ")
    rendered = _first_matching(_rendered_vo_lines(), "<VirtualOutCmd ")
    assert _attr_names(rendered) == _attr_names(reference)


def test_info_element_is_the_first_child_in_the_virtual_in_udp_reference_and_output():
    reference_children = [line.strip() for line in _lines(FIXTURES / "VIU_Referenz.xml")[2:-1]]
    rendered_children = [line.strip() for line in _rendered_viu_lines()[2:-1]]
    assert reference_children[0].startswith("<Info ")
    assert rendered_children[0].startswith("<Info ")


def test_info_element_is_the_first_child_in_the_virtual_out_reference_and_output():
    reference_children = [line.strip() for line in _lines(FIXTURES / "VO_Referenz.xml")[2:-1]]
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
    reference_lines = _lines(FIXTURES / "VO_Referenz.xml")
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


def test_a_valued_command_carries_the_same_analog_flag_as_the_reference():
    """Der Referenzbefehl aus einer echten Installation ist ANALOG - sein
    `CmdOn` enthaelt `<v>` - und traegt trotzdem `Analog="false"`.

    Dem Namen zum Trotz entspricht dieses Attribut in Loxone Config also
    nicht der Frage "ist das analog", sondern dem Haken "Als Digitalausgang
    verwenden". Wir schrieben es bis zum 2026-09-03 andersherum.

    Der Referenztest verglich bisher nur die NAMEN der Attribute und ihre
    Reihenfolge. Genau deshalb blieb der falsche Wert jahrelang unbemerkt -
    und dieser Test schliesst die Luecke.
    """
    reference = _first_matching(_lines(FIXTURES / "VO_Referenz.xml"), "<VirtualOutCmd ")
    assert "&lt;v&gt;" in _attr(reference, "CmdOn"), "Referenzbefehl ist nicht analog"

    valued = [LoxoneCommand("d1_1_level", "level", "/cmd/d1_1_level/<v>", True)]
    rendered = _first_matching(
        render_virtual_out("Lampe", "http://192.168.1.50:8080", valued)
        .decode("utf-8-sig")
        .splitlines(),
        "<VirtualOutCmd ",
    )
    assert _attr(rendered, "Analog") == _attr(reference, "Analog") == "false"


def test_a_switching_command_asks_loxone_for_a_digital_output():
    """Ohne `Analog="true"` bleibt in Loxone Config der Haken "Als
    Digitalausgang verwenden" leer - und damit bietet Config das Feld fuer
    den Aus-Befehl gar nicht erst an, sodass unser `CmdOff` wirkungslos
    bleibt. Am Miniserver des Anwenders beobachtet (2026-09-03).

    Diese Richtung ist NICHT aus der Referenzvorlage belegt: die enthaelt
    nur einen analogen Befehl. Sie stammt aus der Beobachtung an der echten
    Config.
    """
    switching = [
        LoxoneCommand("d1_1_on", "onoff", "/cmd/d1_1_on/1", False, off_path="/cmd/d1_1_off/1")
    ]
    rendered = _first_matching(
        render_virtual_out("Steckdose", "http://192.168.1.50:8080", switching)
        .decode("utf-8-sig")
        .splitlines(),
        "<VirtualOutCmd ",
    )
    assert _attr(rendered, "Analog") == "true"
    assert _attr(rendered, "CmdOff") == "/cmd/d1_1_off/1"
