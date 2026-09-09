from loxmatter.projectsync.keys import key_from_check, key_from_cmd_on, key_from_output_cmd


def test_key_from_check_splits_on_first_colon():
    assert key_from_check("d3_1_onoff:\\v") == "d3_1_onoff"


def test_key_from_check_returns_none_without_colon():
    assert key_from_check("keine ahnung") is None


def test_key_from_cmd_on_reads_our_own_command_path():
    assert key_from_cmd_on("/cmd/d3_1_onoff/1") == "d3_1_onoff"
    assert key_from_cmd_on("/cmd/d3_1_level/<v>") == "d3_1_level"


def test_key_from_cmd_on_ignores_foreign_paths():
    assert key_from_cmd_on("/toggle") is None
    assert key_from_cmd_on("/write?db=loxone") is None
    assert key_from_cmd_on("/cmd/") is None


def test_key_from_output_cmd_combines_both_paths_of_a_paired_command():
    """The combined on/off output (`export.outputs.to_outputs`) carries the
    same `CmdOn` as the single `on` command - it cannot be distinguished
    from that alone. Its key is ``"<on> + <off>"`` according to
    `to_outputs`, and reading it back must produce exactly that, or the
    diff would never find it and would recreate it on every run (user
    report: "a new onoff field after export and re-import")."""
    assert (
        key_from_output_cmd({"CmdOn": "/cmd/d1_1_on/1", "CmdOff": "/cmd/d1_1_off/1"})
        == "d1_1_on + d1_1_off"
    )


def test_key_from_output_cmd_reads_a_single_command_unchanged():
    assert key_from_output_cmd({"CmdOn": "/cmd/d1_1_on/1"}) == "d1_1_on"
    # An empty `CmdOff` is not an off command - Loxone Config writes the
    # attribute even for a purely on-switching output.
    assert key_from_output_cmd({"CmdOn": "/cmd/d1_1_on/1", "CmdOff": ""}) == "d1_1_on"


def test_key_from_output_cmd_ignores_foreign_commands():
    assert key_from_output_cmd({"CmdOn": "/toggle"}) is None
    assert key_from_output_cmd({}) is None
    # Foreign off command with our own on command: the on command counts,
    # the unrecognized off command must not distort the key.
    assert key_from_output_cmd({"CmdOn": "/cmd/d1_1_on/1", "CmdOff": "/aus"}) == "d1_1_on"
