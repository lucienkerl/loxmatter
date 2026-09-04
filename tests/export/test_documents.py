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

from loxmatter.export.documents import (
    LoxoneCommand,
    filename_for,
    render_virtual_in_udp,
    render_virtual_out,
    virtual_in_udp_cmd_attributes,
    virtual_out_cmd_attributes,
)
from loxmatter.export.signals import LoxoneInput


def inputs():
    return [
        LoxoneInput("d1_1_temp", "Temperatur", "Wohnzimmer · 1/1026/0", True, "<v.1> °C"),
        LoxoneInput("d1_online", "erreichbar", "Wohnzimmer", False, ""),
    ]


def text_of(raw: bytes) -> str:
    return raw.decode("utf-8-sig")


def test_root_carries_title_address_and_port():
    out = text_of(render_virtual_in_udp("Wohnzimmerlampe", "192.168.1.50", 7000, inputs()))
    assert 'Title="Matter — Wohnzimmerlampe"' in out
    assert 'Address="192.168.1.50"' in out
    assert 'Port="7000"' in out


def test_each_input_becomes_a_command_with_its_check_pattern():
    out = text_of(render_virtual_in_udp("L", "192.168.1.50", 7000, inputs()))
    assert 'Check="d1_1_temp:\\v"' in out
    assert 'Check="d1_online:\\v"' in out
    assert out.count("<VirtualInUdpCmd ") == 2


def test_analog_flag_follows_the_input():
    out = text_of(render_virtual_in_udp("L", "192.168.1.50", 7000, inputs()))
    temp = next(line for line in out.splitlines() if "d1_1_temp" in line)
    online = next(line for line in out.splitlines() if "d1_online" in line)
    assert 'Analog="true"' in temp
    assert 'Analog="false"' in online


def test_defaults_from_the_verified_schema_are_present():
    out = text_of(render_virtual_in_udp("L", "192.168.1.50", 7000, inputs()))
    for attr in (
        "Signed=",
        "SourceValLow=",
        "DestValHigh=",
        "DefVal=",
        "MinVal=",
        "MaxVal=",
        "Unit=",
        "HintText=",
    ):
        assert attr in out


def test_unit_format_is_escaped_into_the_unit_attribute():
    """Spec 6.1, Korrektur 2026-09-02: VirtualInUdpCmd hat 15 Attribute, u. a. Unit."""
    out = text_of(render_virtual_in_udp("L", "192.168.1.50", 7000, inputs()))
    assert 'Unit="&lt;v.1&gt; °C"' in out


def test_info_element_is_the_first_child_of_virtual_in_udp():
    """Spec 6.1, Korrektur 2026-09-02: jede Vorlage traegt ein Info-Element als erstes Kind."""
    out = text_of(render_virtual_in_udp("L", "192.168.1.50", 7000, inputs()))
    # ">" Nr. 1 schliesst die XML-Deklaration, ">" Nr. 2 das Wurzelelement —
    # erst danach beginnt der Elementinhalt, in dem <Info> stehen soll.
    body_after_root = out.split(">", 2)[2]
    assert body_after_root.lstrip().startswith('<Info templateType="1" minVersion="14040925"/>')


def test_virtual_out_escapes_the_value_placeholder():
    out = text_of(
        render_virtual_out(
            "Lampe",
            "http://192.168.1.50:8080",
            [LoxoneCommand("d1_1_level", "Helligkeit", "/cmd/d1_1_level/<v>", True)],
        )
    )
    assert "&lt;v&gt;" in out
    assert "/<v>" not in out


def test_virtual_out_carries_method_and_address():
    out = text_of(
        render_virtual_out(
            "Lampe",
            "http://192.168.1.50:8080",
            [LoxoneCommand("d1_1_onoff", "Schalten", "/cmd/d1_1_onoff/1", False)],
        )
    )
    assert 'Address="http://192.168.1.50:8080"' in out
    assert 'CmdOnMethod="GET"' in out
    assert 'CmdOffMethod="GET"' in out


def test_virtual_out_cmd_has_no_id_attribute():
    """Spec 6.1, Korrektur 2026-09-02: VirtualOutCmd hat 15 Attribute und kein ID."""
    out = text_of(
        render_virtual_out(
            "Lampe",
            "http://192.168.1.50:8080",
            [LoxoneCommand("d1_1_onoff", "Schalten", "/cmd/d1_1_onoff/1", False)],
        )
    )
    assert 'ID="' not in out


def test_info_element_is_the_first_child_of_virtual_out():
    out = text_of(
        render_virtual_out(
            "Lampe",
            "http://192.168.1.50:8080",
            [LoxoneCommand("d1_1_onoff", "Schalten", "/cmd/d1_1_onoff/1", False)],
        )
    )
    # ">" Nr. 1 schliesst die XML-Deklaration, ">" Nr. 2 das Wurzelelement —
    # erst danach beginnt der Elementinhalt, in dem <Info> stehen soll.
    body_after_root = out.split(">", 2)[2]
    assert body_after_root.lstrip().startswith('<Info templateType="3" minVersion="14040925"/>')


def test_filenames_follow_the_spec_prefixes():
    assert filename_for("VIU", 12, "Wohnzimmer Lampe") == "VIU_d12_Wohnzimmer_Lampe.xml"
    assert filename_for("VO", 7, "Küche/Steckdose") == "VO_d7_Kueche_Steckdose.xml"


def test_filename_is_ascii_only():
    name = filename_for("VIU", 3, "Büro Ölheizung —Süd")
    assert name.isascii()
    assert name.startswith("VIU_") and name.endswith(".xml")


def test_filenames_of_labels_differing_only_by_separator_do_not_collide():
    """ "Lampe 1", "Lampe_1" und "Lampe-1" normalisieren alle auf dasselbe
    Label-Segment — auf verschiedenen Geraeten muss die ID sie trotzdem
    trennen."""
    space = filename_for("VIU", 1, "Lampe 1")
    underscore = filename_for("VIU", 2, "Lampe_1")
    hyphen = filename_for("VIU", 3, "Lampe-1")
    assert len({space, underscore, hyphen}) == 3


def test_filename_with_empty_label_has_no_trailing_separator_or_empty_segment():
    """Ein Label, das komplett wegnormalisiert (nicht-ASCII, leer, nur
    Sonderzeichen), darf weder mit "_" enden noch ein leeres "__"-Segment
    hinterlassen — die Datei bleibt trotzdem eindeutig ueber die ID."""
    for label in ("厨房", "", "!!!"):
        name = filename_for("VIU", 12, label)
        assert name == "VIU_d12.xml"
        assert not name.endswith("_.xml")
        assert "__" not in name


def test_same_label_on_different_device_ids_never_collides():
    first = filename_for("VIU", 1, "Wohnzimmerlampe")
    second = filename_for("VIU", 2, "Wohnzimmerlampe")
    assert first != second
    assert first == "VIU_d1_Wohnzimmerlampe.xml"
    assert second == "VIU_d2_Wohnzimmerlampe.xml"


def test_virtual_in_udp_cmd_attributes_matches_rendered_output():
    entry = LoxoneInput("d1_1_temp", "Temperatur", "Wohnzimmer · 1/1026/0", True, "<v.1> °C")
    attrs = dict(virtual_in_udp_cmd_attributes(entry))
    assert attrs["Title"] == "Temperatur"
    assert attrs["Check"] == "d1_1_temp:\\v"
    assert attrs["Analog"] == "true"
    assert attrs["Unit"] == "<v.1> °C"


def test_virtual_out_cmd_attributes_is_importable():
    command = LoxoneCommand("d1_1_on", "on", "/cmd/d1_1_on/1", False)
    attrs = dict(virtual_out_cmd_attributes(command))
    assert attrs["CmdOn"] == "/cmd/d1_1_on/1"
    assert attrs["Analog"] == "true"
