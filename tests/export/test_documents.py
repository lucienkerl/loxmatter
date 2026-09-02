from loxmatter.export.documents import (
    LoxoneCommand,
    filename_for,
    render_virtual_in_udp,
    render_virtual_out,
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
    assert filename_for("VIU", "Wohnzimmer Lampe") == "VIU_Wohnzimmer_Lampe.xml"
    assert filename_for("VO", "Küche/Steckdose") == "VO_Kueche_Steckdose.xml"


def test_filename_is_ascii_only():
    name = filename_for("VIU", "Büro Ölheizung —Süd")
    assert name.isascii()
    assert name.startswith("VIU_") and name.endswith(".xml")
