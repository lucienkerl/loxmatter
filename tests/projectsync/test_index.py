from loxmatter.projectsync.index import AmbiguousMiniserverError, ProjectFormatError, build_index

# Two `LoxLIVE` blocks (two Miniservers configured in one project), each
# with its own, disjoint input signal - proves that `build_index` really
# only searches the CHOSEN block after resolution, not both.
TWO_LOXLIVE_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="Document" U="2000-0000-0000-aaaaaaaaaaaaaaaa" Title="Testprojekt">\r\n'
    '\t\t<C Type="LoxLIVE" U="2000-0001-0000-aaaaaaaaaaaaaaaa" Title="Erster Miniserver"'
    ' IntAddr="10.0.0.10" Serial="504F00000001">\r\n'
    '\t\t\t<C Type="VirtualInCaption" IName="C1" U="1000-0000-0000-aaaaaaaaaaaaaaaa">\r\n'
    '\t\t\t\t<C Type="VirtualUdpIn" IName="VUI1" U="1000-0001-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Geraet A" WF="16384" Address="10.0.0.5" Port="7000">\r\n'
    '\t\t\t\t\t<C Type="VirtualUdpInCmd" IName="VCI1" U="1000-0002-0000-aaaaaaaaaaaaaaaa"'
    ' Title="A" Nio="2" WF="16384" Check="d1_1_onoff:\\v" Analog="true">\r\n'
    '\t\t\t\t\t\t<IoData Cr="x" Pr="y"/>\r\n'
    "\t\t\t\t\t</C>\r\n"
    "\t\t\t\t</C>\r\n"
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    '\t\t<C Type="LoxLIVE" U="2000-0002-0000-aaaaaaaaaaaaaaaa" Title="Zweiter Miniserver"'
    ' IntAddr="10.0.0.20" Serial="504F00000002">\r\n'
    '\t\t\t<C Type="VirtualInCaption" IName="C2" U="1000-0003-0000-aaaaaaaaaaaaaaaa">\r\n'
    '\t\t\t\t<C Type="VirtualUdpIn" IName="VUI2" U="1000-0004-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Geraet B" WF="16384" Address="10.0.0.5" Port="7000">\r\n'
    '\t\t\t\t\t<C Type="VirtualUdpInCmd" IName="VCI2" U="1000-0005-0000-aaaaaaaaaaaaaaaa"'
    ' Title="B" Nio="2" WF="16384" Check="d2_1_onoff:\\v" Analog="true">\r\n'
    '\t\t\t\t\t\t<IoData Cr="x" Pr="y"/>\r\n'
    "\t\t\t\t\t</C>\r\n"
    "\t\t\t\t</C>\r\n"
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)

# A `Document` that contains no `LoxLIVE` block at all - a technically
# valid, but empty/freshly created project.
NO_LOXLIVE_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="Document" U="2000-0000-0000-aaaaaaaaaaaaaaaa" Title="Testprojekt"/>\r\n'
    "</ControlList>\r\n"
)


def test_finds_both_captions(sample_project):
    index = build_index(sample_project)
    assert index.virtual_in_caption is not None
    assert index.virtual_in_caption.type == "VirtualInCaption"
    assert index.virtual_out_caption is not None
    assert index.virtual_out_caption.type == "VirtualOutCaption"


def test_indexes_existing_input_cmd_by_key(sample_project):
    index = build_index(sample_project)
    assert "d1_1_onoff" in index.input_cmds
    cmd = index.input_cmds["d1_1_onoff"]
    assert cmd.attrs["Title"] == "Alter Titel"
    container = index.input_containers["d1_1_onoff"]
    assert container.type == "VirtualUdpIn"


def test_indexes_existing_output_cmd_by_key(sample_project):
    index = build_index(sample_project)
    assert "d1_1_on" in index.output_cmds
    assert index.output_cmds["d1_1_on"].attrs["CmdOn"] == "/cmd/d1_1_on/1"


def test_unknown_device_has_no_entry(sample_project):
    index = build_index(sample_project)
    assert "d2_1_onoff" not in index.input_cmds


def test_collects_all_u_values_including_connectors(sample_project):
    index = build_index(sample_project)
    # "1000-0003-0000-bbbbbbbbbbbbbbbb" belongs to a <Co>, not a <C> -
    # it must still be captured, otherwise a newly generated id would not
    # be reliably unique.
    assert "1000-0003-0000-bbbbbbbbbbbbbbbb" in index.all_u_values
    assert "1000-0001-0000-aaaaaaaaaaaaaaaa" in index.all_u_values


def test_collects_all_inames(sample_project):
    index = build_index(sample_project)
    assert {"VUI1", "VCI1", "VCI2", "VQ1", "VQC1"} <= index.all_inames


def test_rejects_file_without_control_list():
    import pytest

    with pytest.raises(ProjectFormatError):
        build_index("<NotAProject/>")


def test_single_loxlive_is_auto_selected_without_ip(sample_project):
    """Exactly one `LoxLIVE` block in the file: it gets auto-selected,
    `miniserver_ip` stays optional (draft, section on Miniserver
    assignment)."""
    index = build_index(sample_project)
    assert index.target_loxlive.type == "LoxLIVE"
    assert index.target_loxlive.attrs["IntAddr"] == "10.0.0.10"


def test_single_loxlive_matching_ip_is_selected(sample_project):
    index = build_index(sample_project, "10.0.0.10")
    assert index.target_loxlive.attrs["IntAddr"] == "10.0.0.10"


def test_single_loxlive_mismatched_ip_raises(sample_project):
    """An explicitly supplied but non-matching IP points more to the wrong
    file than to a reason to ignore it - so it must match even when there
    is only one `LoxLIVE` block in the file."""
    import pytest

    with pytest.raises(AmbiguousMiniserverError, match="10.0.0.99"):
        build_index(sample_project, "10.0.0.99")


def test_multi_loxlive_without_ip_raises():
    import pytest

    with pytest.raises(AmbiguousMiniserverError, match="mehrere Miniserver"):
        build_index(TWO_LOXLIVE_PROJECT)


def test_multi_loxlive_without_ip_carries_candidates_for_a_selection_field():
    """`candidates` is why the API can offer a selection field instead of
    a plain error message when there are multiple Miniservers (user request
    after the review: choose from a list instead of typing the IP)."""
    import pytest

    from loxmatter.projectsync.index import MiniserverCandidate

    with pytest.raises(AmbiguousMiniserverError) as exc_info:
        build_index(TWO_LOXLIVE_PROJECT)
    assert exc_info.value.candidates == [
        MiniserverCandidate(title="Erster Miniserver", int_addr="10.0.0.10"),
        MiniserverCandidate(title="Zweiter Miniserver", int_addr="10.0.0.20"),
    ]


def test_no_loxlive_carries_no_candidates():
    """Without a single `LoxLIVE` block there is nothing to choose from -
    the API must still treat this case as a real 400, not offer an empty
    selection field."""
    import pytest

    with pytest.raises(AmbiguousMiniserverError) as exc_info:
        build_index(NO_LOXLIVE_PROJECT)
    assert exc_info.value.candidates == []


def test_multi_loxlive_with_matching_ip_scopes_to_that_block_only():
    """The matching may only search within the chosen `LoxLIVE` block -
    otherwise it could land in the wrong Miniserver area of a
    multi-Miniserver file and falsely find a signal there that actually
    belongs to the OTHER Miniserver."""
    index = build_index(TWO_LOXLIVE_PROJECT, "10.0.0.20")
    assert index.target_loxlive.attrs["Title"] == "Zweiter Miniserver"
    assert "d2_1_onoff" in index.input_cmds
    assert "d1_1_onoff" not in index.input_cmds


def test_multi_loxlive_with_non_matching_ip_raises():
    import pytest

    with pytest.raises(AmbiguousMiniserverError, match="10.0.0.99"):
        build_index(TWO_LOXLIVE_PROJECT, "10.0.0.99")


def test_no_loxlive_raises():
    import pytest

    with pytest.raises(AmbiguousMiniserverError, match="keinen einzigen konfigurierten Miniserver"):
        build_index(NO_LOXLIVE_PROJECT)


# An output container the way this project itself writes it: the combined
# on/off command sits immediately before its `on` and carries the same
# `CmdOn` (`export.outputs.to_outputs`).
PAIRED_OUTPUT_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="Document" U="2000-0000-0000-aaaaaaaaaaaaaaaa" Title="Testprojekt">\r\n'
    '\t\t<C Type="LoxLIVE" U="2000-0001-0000-aaaaaaaaaaaaaaaa" Title="Testserver"'
    ' IntAddr="10.0.0.10">\r\n'
    '\t\t\t<C Type="VirtualOutCaption" U="1000-000a-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Virtuelle Ausgänge" WF="16384">\r\n'
    '\t\t\t\t<C Type="VirtualOut" IName="VQ1" U="1000-000b-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Matter — Steckdose" WF="16384" Address="http://10.0.0.5:8080">\r\n'
    '\t\t\t\t\t<C Type="VirtualOutCmd" IName="VQC1" U="1000-000c-0000-aaaaaaaaaaaaaaaa"'
    ' Title="off" Nio="1" CmdOn="/cmd/d1_1_off/1"><IoData Cr="x" Pr="y"/></C>\r\n'
    '\t\t\t\t\t<C Type="VirtualOutCmd" IName="VQC2" U="1000-000d-0000-aaaaaaaaaaaaaaaa"'
    ' Title="onoff" Nio="1" CmdOn="/cmd/d1_1_on/1" CmdOff="/cmd/d1_1_off/1" Analog="false">'
    '<IoData Cr="x" Pr="y"/></C>\r\n'
    '\t\t\t\t\t<C Type="VirtualOutCmd" IName="VQC3" U="1000-000e-0000-aaaaaaaaaaaaaaaa"'
    ' Title="on" Nio="1" CmdOn="/cmd/d1_1_on/1" Analog="true"><IoData Cr="x" Pr="y"/></C>\r\n'
    "\t\t\t\t</C>\r\n"
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


def test_paired_and_single_output_commands_do_not_collide():
    """User report: after export and re-import, the sync wanted to create
    a second "onoff" field. The cause was a key collision - the combined
    on/off command and the single `on` command carry the same `CmdOn`, so
    one overwrote the other in the index. Both must stand under their OWN
    key; the combined one under the double key that
    `export.outputs.to_outputs` also assigns."""
    index = build_index(PAIRED_OUTPUT_PROJECT)
    assert set(index.output_cmds) == {"d1_1_off", "d1_1_on", "d1_1_on + d1_1_off"}
    assert index.output_cmds["d1_1_on + d1_1_off"].attrs["Title"] == "onoff"
    assert index.output_cmds["d1_1_on"].attrs["Title"] == "on"
