from loxmatter.export.documents import LoxoneCommand
from loxmatter.export.signals import LoxoneInput
from loxmatter.projectsync.schema import (
    desired_input_cmd_attrs,
    desired_output_cmd_attrs,
    find_any_iodata_attrs,
    new_cmd_children_xml,
    new_input_cmd_open_tag,
    new_input_container_open_tag,
    new_output_cmd_open_tag,
    new_output_container_open_tag,
    sibling_iodata_attrs,
)


def test_desired_input_cmd_attrs_covers_only_managed_fields():
    entry = LoxoneInput("d1_1_temp", "Temperatur", "Kommentar", True, "<v.1> °C")
    desired = desired_input_cmd_attrs(entry)
    assert desired == {
        "Title": "Temperatur",
        "Check": "d1_1_temp:\\v",
        "Analog": "true",
        "Unit": "<v.1> °C",
    }


def test_desired_output_cmd_attrs_omits_cmdoff_when_there_is_none():
    command = LoxoneCommand("d1_1_level", "level", "/cmd/d1_1_level/<v>", True)
    desired = desired_output_cmd_attrs(command)
    assert desired == {"Title": "level", "CmdOn": "/cmd/d1_1_level/<v>", "Analog": "true"}


def test_desired_output_cmd_attrs_includes_cmdoff_for_paired_commands():
    command = LoxoneCommand(
        "d1_1_on + d1_1_off", "onoff", "/cmd/d1_1_on/1", False, off_path="/cmd/d1_1_off/1"
    )
    desired = desired_output_cmd_attrs(command)
    assert desired["CmdOff"] == "/cmd/d1_1_off/1"
    assert desired["Analog"] == "false"


def test_new_input_cmd_open_tag_is_a_valid_non_self_closing_start_tag():
    entry = LoxoneInput("d2_1_temp", "Temperatur", "Kommentar", True, "<v.1> °C")
    tag = new_input_cmd_open_tag(entry, "VCI9", "u-new")
    assert tag.startswith('<C Type="VirtualUdpInCmd"')
    assert tag.endswith(">")
    assert not tag.endswith("/>")
    assert 'Check="d2_1_temp:\\v"' in tag
    assert 'IName="VCI9"' in tag
    assert 'U="u-new"' in tag


def test_new_output_cmd_open_tag_contains_command_path():
    command = LoxoneCommand("d2_1_on", "on", "/cmd/d2_1_on/1", False)
    tag = new_output_cmd_open_tag(command, "VQC9", "u-new")
    assert 'CmdOn="/cmd/d2_1_on/1"' in tag
    assert 'IName="VQC9"' in tag


def test_sibling_iodata_attrs_reads_from_an_existing_cmd(sample_project):
    from loxmatter.projectsync.index import build_index

    index = build_index(sample_project)
    cmd = index.input_cmds["d1_1_onoff"]
    attrs = sibling_iodata_attrs(sample_project, cmd)
    assert attrs is not None
    assert attrs["Cr"] == "1000-0005-0000-aaaaaaaaaaaaaaaa"


def test_find_any_iodata_attrs_falls_back_to_any_cmd_under_the_caption(sample_project):
    from loxmatter.projectsync.index import build_index

    index = build_index(sample_project)
    attrs = find_any_iodata_attrs(sample_project, index.virtual_in_caption)
    assert attrs is not None
    assert "Cr" in attrs


def test_new_cmd_children_xml_contains_two_connectors_for_input():
    existing_u: set[str] = {"1000-0001-0000-aaaaaaaaaaaaaaaa"}
    xml = new_cmd_children_xml(kind="input", existing_u=existing_u, iodata_attrs=None)
    assert xml.count('Co K="AQ"') == 1
    assert xml.count('Co K="Q"') == 1
    assert "<IoData" not in xml
    assert "<Display" in xml


def test_new_cmd_children_xml_contains_one_connector_for_output_with_iodata():
    existing_u: set[str] = {"1000-0001-0000-aaaaaaaaaaaaaaaa"}
    xml = new_cmd_children_xml(
        kind="output", existing_u=existing_u, iodata_attrs={"Cr": "x", "Pr": "y"}
    )
    assert xml.count('Co K="I"') == 1
    assert '<IoData Cr="x" Pr="y"/>' in xml


def test_new_input_container_open_tag_carries_bridge_address():
    tag = new_input_container_open_tag("Neues Geraet", "10.0.0.5", 7000, "VUI9", "u-new")
    assert 'Type="VirtualUdpIn"' in tag
    assert 'Title="Matter — Neues Geraet"' in tag
    assert 'Address="10.0.0.5"' in tag
    assert 'Port="7000"' in tag
    assert not tag.endswith("/>")


def test_new_output_container_open_tag_carries_base_url():
    tag = new_output_container_open_tag("Neues Geraet", "http://10.0.0.5:8080", "VQ9", "u-new")
    assert 'Type="VirtualOut"' in tag
    assert 'Address="http://10.0.0.5:8080"' in tag
    assert not tag.endswith("/>")
