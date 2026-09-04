from loxmatter.projectsync.index import ProjectFormatError, build_index


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
    # "1000-0003-0000-bbbbbbbbbbbbbbbb" gehoert zu einem <Co>, keinem <C> -
    # muss trotzdem erfasst sein, sonst waere eine neu erzeugte ID nicht
    # sicher eindeutig.
    assert "1000-0003-0000-bbbbbbbbbbbbbbbb" in index.all_u_values
    assert "1000-0001-0000-aaaaaaaaaaaaaaaa" in index.all_u_values


def test_collects_all_inames(sample_project):
    index = build_index(sample_project)
    assert {"VUI1", "VCI1", "VCI2", "VQ1", "VQC1"} <= index.all_inames


def test_rejects_file_without_control_list():
    import pytest

    with pytest.raises(ProjectFormatError):
        build_index("<NotAProject/>")
