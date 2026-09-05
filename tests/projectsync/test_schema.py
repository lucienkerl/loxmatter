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
    """Ohne `Unit`: eine Projektdatei fuehrt die Einheit nicht am `<C>`-Tag,
    sondern im `<Display>`-Kind (siehe `MANAGED_INPUT_CMD_ATTRS`). Ein hier
    gepflegtes `Unit` liesse jeden analogen Eingang bei jedem Lauf erneut als
    "aktualisiert" erscheinen und schriebe den Wert an eine Stelle, an der
    Loxone Config ihn nie liest."""
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
    """Anwenderbericht: "die Einheit ist bei den virtuellen Eingaengen nicht
    mehr dabei". In einer echten Projektdatei traegt KEIN einziges
    `<C>`-Objekt ein `Unit`-Attribut (an allen 3710 geprueft) - die Einheit
    steht dort ausschliesslich im `<Display>`-Kind, siehe
    `new_cmd_children_xml`. Das `Unit`-Attribut gehoert allein in die
    Vorlagendatei (`export.documents.virtual_in_udp_cmd_attributes`, ein
    anderes Dateiformat); hier war es aus dieser Liste mituebernommen worden
    und landete an einer Stelle, an der Loxone Config es nie liest."""
    entry = LoxoneInput("d2_1_temp", "Temperatur", "Kommentar", True, "<v.1> °C")
    tag = new_input_cmd_open_tag(entry, "VCI9", "u-new")
    assert "Unit=" not in tag
    # An der echten Referenzdatei geprueft: ALLE <C>-Objekte tragen ein
    # `V`-Attribut (Entwurf Abschnitt 6, Korrektur nach echtem Praxistest) -
    # ohne ihn blieb ein neu angelegtes Kommando in Loxone Config unsichtbar.
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
    """Die Einheit eines analogen Eingangs gehoert ins `<Display>`-Kind, in
    genau der Form, die eine echte Projektdatei zeigt: `Type="2"` fuer einen
    analogen Wert und der komplette Formatstring inklusive Einheit
    (86 Beispiele in der Referenzdatei, z. B. `Type="2" Unit="<v.3> kW"`)."""
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
    """Analoges Signal ohne bekannte Einheit (`unit_format` ist dann leer,
    siehe `profiles.table.unit_format`): der Formatstring bleibt, nur ohne
    Einheitentext - ebenfalls so in der Referenzdatei zu sehen. Ein leeres
    `Unit=""` gaebe es dort nirgends."""
    existing_u: set[str] = {"1000-0001-0000-aaaaaaaaaaaaaaaa"}
    xml = new_cmd_children_xml(
        kind="input", existing_u=existing_u, iodata_attrs=None, analog=True, unit_format=""
    )
    assert '<Display Type="2" Unit="&lt;v.1&gt;" StateOnly="true"/>' in xml


def test_new_cmd_children_xml_display_type_follows_the_analog_flag():
    """`Type="2"` haengt an genau demselben Schalter wie das `Analog`-Attribut
    des Tags (`export.documents.virtual_in_udp_cmd_attributes`): in der
    Referenzdatei steht es ausnahmslos bei `Analog="true"`. Heute markiert
    `export.signals.to_inputs` zwar jeden Eingang als analog - die beiden
    duerfen aber nicht auseinanderlaufen, falls sich das aendert."""
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
