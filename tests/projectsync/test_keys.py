from loxmatter.projectsync.keys import key_from_check, key_from_cmd_on


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
