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
    """Der kombinierte Ein/Aus-Ausgang (`export.outputs.to_outputs`) traegt
    denselben `CmdOn` wie der einzelne `on`-Befehl - allein daraus laesst er
    sich nicht unterscheiden. Sein Schluessel ist laut `to_outputs`
    ``"<on> + <off>"``, und genau den muss auch das Wiedereinlesen liefern,
    sonst faende der Abgleich ihn nie und legte ihn bei jedem Durchlauf neu
    an (Anwenderbericht: "nach Export und erneutem Import ein neues Feld
    onoff")."""
    assert (
        key_from_output_cmd({"CmdOn": "/cmd/d1_1_on/1", "CmdOff": "/cmd/d1_1_off/1"})
        == "d1_1_on + d1_1_off"
    )


def test_key_from_output_cmd_reads_a_single_command_unchanged():
    assert key_from_output_cmd({"CmdOn": "/cmd/d1_1_on/1"}) == "d1_1_on"
    # Leeres `CmdOff` ist kein Aus-Befehl - Loxone Config schreibt das Attribut
    # auch bei einem rein einschaltenden Ausgang mit.
    assert key_from_output_cmd({"CmdOn": "/cmd/d1_1_on/1", "CmdOff": ""}) == "d1_1_on"


def test_key_from_output_cmd_ignores_foreign_commands():
    assert key_from_output_cmd({"CmdOn": "/toggle"}) is None
    assert key_from_output_cmd({}) is None
    # Fremder Aus-Befehl bei eigenem Ein-Befehl: der Ein-Befehl zaehlt, der
    # unverstandene Aus-Befehl darf den Schluessel nicht verfaelschen.
    assert key_from_output_cmd({"CmdOn": "/cmd/d1_1_on/1", "CmdOff": "/aus"}) == "d1_1_on"
