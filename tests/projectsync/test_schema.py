from loxmatter.export.documents import LoxoneCommand
from loxmatter.export.signals import LoxoneInput
from loxmatter.projectsync.schema import (
    desired_input_cmd_attrs,
    desired_output_cmd_attrs,
    find_any_iodata_attrs,
    new_caption_open_tag,
    new_cmd_children_xml,
    new_input_cmd_open_tag,
    new_input_container_open_tag,
    new_output_cmd_open_tag,
    new_output_container_open_tag,
    sibling_iodata_attrs,
)


def test_desired_input_cmd_attrs_covers_only_managed_fields():
    """Without `Unit`: a project file does not carry the unit on the `<C>`
    tag, but in the `<Display>` child (see `MANAGED_INPUT_CMD_ATTRS`). A
    `Unit` maintained here would make every analog input appear as
    "updated" again on every run and would write the value to a place
    Loxone Config never reads."""
    entry = LoxoneInput("d1_1_temp", "Temperatur", "Kommentar", True, "<v.1> °C")
    desired = desired_input_cmd_attrs(entry)
    assert desired == {
        "Title": "Temperatur",
        "Check": "d1_1_temp:\\v",
        "Analog": "true",
    }


def test_desired_output_cmd_attrs_omits_cmdoff_when_there_is_none():
    command = LoxoneCommand("d1_1_level", "level", "/cmd/d1_1_level/<v>", True)
    desired = desired_output_cmd_attrs(command)
    assert desired == {"Title": "level", "CmdOn": "/cmd/d1_1_level/<v>", "Analog": "true"}


def test_new_caption_open_tag_builds_input_caption():
    tag = new_caption_open_tag("input", "u-new")
    assert (
        tag == '<C Type="VirtualInCaption" V="178" U="u-new" Title="Virtuelle Eingänge" WF="16384">'
    )
    assert not tag.endswith("/>")


def test_new_caption_open_tag_builds_output_caption():
    tag = new_caption_open_tag("output", "u-new")
    assert (
        tag
        == '<C Type="VirtualOutCaption" V="178" U="u-new" Title="Virtuelle Ausgänge" WF="16384">'
    )


def test_new_caption_open_tag_rejects_unknown_kind():
    import pytest

    with pytest.raises(ValueError, match="input.*output"):
        new_caption_open_tag("bogus", "u-new")


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


def test_new_input_cmd_open_tag_carries_no_unit_attribute():
    """User report: "the unit is no longer there for the virtual inputs".
    In a real project file, NOT A SINGLE `<C>` object carries a `Unit`
    attribute (checked across all 3710) - the unit lives there exclusively
    in the `<Display>` child, see `new_cmd_children_xml`. The `Unit`
    attribute belongs only in the template file
    (`export.documents.virtual_in_udp_cmd_attributes`, a different file
    format); here it had been carried over from that list and ended up in a
    place Loxone Config never reads."""
    entry = LoxoneInput("d2_1_temp", "Temperatur", "Kommentar", True, "<v.1> °C")
    tag = new_input_cmd_open_tag(entry, "VCI9", "u-new")
    assert "Unit=" not in tag
    # Checked against the real reference file: ALL <C> objects carry a `V`
    # attribute (draft section 6, correction after a real practical test) -
    # without it a newly created command stayed invisible in Loxone Config.
    assert 'V="178"' in tag


def test_new_output_cmd_open_tag_contains_command_path():
    command = LoxoneCommand("d2_1_on", "on", "/cmd/d2_1_on/1", False)
    tag = new_output_cmd_open_tag(command, "VQC9", "u-new")
    assert 'CmdOn="/cmd/d2_1_on/1"' in tag
    assert 'IName="VQC9"' in tag
    assert 'V="178"' in tag


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


def test_new_cmd_children_xml_puts_the_unit_into_the_display_element():
    """The unit of an analog input belongs in the `<Display>` child, in
    exactly the form a real project file shows: `Type="2"` for an analog
    value and the complete format string including the unit (86 examples in
    the reference file, e.g. `Type="2" Unit="<v.3> kW"`)."""
    existing_u: set[str] = {"1000-0001-0000-aaaaaaaaaaaaaaaa"}
    xml = new_cmd_children_xml(
        kind="input",
        existing_u=existing_u,
        iodata_attrs=None,
        analog=True,
        unit_format="<v.1> °C",
    )
    assert '<Display Type="2" Unit="&lt;v.1&gt; °C" StateOnly="true"/>' in xml


def test_new_cmd_children_xml_display_falls_back_to_a_plain_format_string():
    """Analog signal without a known unit (`unit_format` is then empty, see
    `profiles.table.unit_format`): the format string stays, just without
    unit text - also seen this way in the reference file. An empty
    `Unit=""` would never occur there."""
    existing_u: set[str] = {"1000-0001-0000-aaaaaaaaaaaaaaaa"}
    xml = new_cmd_children_xml(
        kind="input", existing_u=existing_u, iodata_attrs=None, analog=True, unit_format=""
    )
    assert '<Display Type="2" Unit="&lt;v.1&gt;" StateOnly="true"/>' in xml


def test_new_cmd_children_xml_display_type_follows_the_analog_flag():
    """`Type="2"` hangs on exactly the same switch as the tag's `Analog`
    attribute (`export.documents.virtual_in_udp_cmd_attributes`): in the
    reference file it appears without exception alongside `Analog="true"`.
    Today `export.signals.to_inputs` does mark every input as analog - but
    the two must not drift apart if that changes."""
    existing_u: set[str] = {"1000-0001-0000-aaaaaaaaaaaaaaaa"}
    xml = new_cmd_children_xml(
        kind="input", existing_u=existing_u, iodata_attrs=None, analog=False, unit_format=""
    )
    assert '<Display Unit="&lt;v.1&gt;" StateOnly="true"/>' in xml
    assert 'Type="2"' not in xml


def test_new_input_container_open_tag_carries_bridge_address():
    tag = new_input_container_open_tag("Neues Geraet", "10.0.0.5", 7000, "VUI9", "u-new")
    assert 'Type="VirtualUdpIn"' in tag
    assert 'Title="Matter — Neues Geraet"' in tag
    assert 'Address="10.0.0.5"' in tag
    assert 'Port="7000"' in tag
    assert 'V="178"' in tag
    assert not tag.endswith("/>")


def test_new_output_container_open_tag_carries_base_url():
    tag = new_output_container_open_tag("Neues Geraet", "http://10.0.0.5:8080", "VQ9", "u-new")
    assert 'Type="VirtualOut"' in tag
    assert 'Address="http://10.0.0.5:8080"' in tag
    assert 'V="178"' in tag
    assert not tag.endswith("/>")
