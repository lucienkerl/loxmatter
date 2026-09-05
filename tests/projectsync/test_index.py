from loxmatter.projectsync.index import AmbiguousMiniserverError, ProjectFormatError, build_index

# Zwei `LoxLIVE`-Bloecke (zwei konfigurierte Miniserver in einem Projekt) mit
# je einem eigenen, disjunkten Eingangssignal - beweist, dass `build_index`
# nach Aufloesung wirklich nur im GEWAEHLTEN Block sucht, nicht in beiden.
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

# Ein `Document`, das gar keinen `LoxLIVE`-Block enthaelt - ein technisch
# gueltiges, aber leeres/frisch angelegtes Projekt.
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


def test_single_loxlive_is_auto_selected_without_ip(sample_project):
    """Genau ein `LoxLIVE`-Block in der Datei: er wird automatisch gewaehlt,
    `miniserver_ip` bleibt optional (Entwurf, Abschnitt zur Miniserver-
    Zuordnung)."""
    index = build_index(sample_project)
    assert index.target_loxlive.type == "LoxLIVE"
    assert index.target_loxlive.attrs["IntAddr"] == "10.0.0.10"


def test_single_loxlive_matching_ip_is_selected(sample_project):
    index = build_index(sample_project, "10.0.0.10")
    assert index.target_loxlive.attrs["IntAddr"] == "10.0.0.10"


def test_single_loxlive_mismatched_ip_raises(sample_project):
    """Eine explizit mitgegebene, aber nicht passende IP deutet eher auf die
    falsche Datei hin als auf einen Grund, sie zu ignorieren - auch bei nur
    einem `LoxLIVE`-Block in der Datei muss sie darum passen."""
    import pytest

    with pytest.raises(AmbiguousMiniserverError, match="10.0.0.99"):
        build_index(sample_project, "10.0.0.99")


def test_multi_loxlive_without_ip_raises():
    import pytest

    with pytest.raises(AmbiguousMiniserverError, match="mehrere Miniserver"):
        build_index(TWO_LOXLIVE_PROJECT)


def test_multi_loxlive_without_ip_carries_candidates_for_a_selection_field():
    """`candidates` ist der Grund, warum die API bei mehreren Miniservern
    statt einer reinen Fehlermeldung ein Auswahlfeld anbieten kann
    (Nutzerwunsch nach dem Review: auswaehlen statt die IP abzutippen)."""
    import pytest

    from loxmatter.projectsync.index import MiniserverCandidate

    with pytest.raises(AmbiguousMiniserverError) as exc_info:
        build_index(TWO_LOXLIVE_PROJECT)
    assert exc_info.value.candidates == [
        MiniserverCandidate(title="Erster Miniserver", int_addr="10.0.0.10"),
        MiniserverCandidate(title="Zweiter Miniserver", int_addr="10.0.0.20"),
    ]


def test_no_loxlive_carries_no_candidates():
    """Ohne einen einzigen `LoxLIVE`-Block gibt es nichts zur Auswahl - die
    API muss diesen Fall weiterhin als echte 400 behandeln, kein leeres
    Auswahlfeld anbieten."""
    import pytest

    with pytest.raises(AmbiguousMiniserverError) as exc_info:
        build_index(NO_LOXLIVE_PROJECT)
    assert exc_info.value.candidates == []


def test_multi_loxlive_with_matching_ip_scopes_to_that_block_only():
    """Der Abgleich darf nur im gewaehlten `LoxLIVE`-Block suchen - sonst
    koennte er im falschen Miniserver-Bereich einer Mehr-Miniserver-Datei
    landen und dort faelschlich ein Signal finden, das eigentlich zum
    ANDEREN Miniserver gehoert."""
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


# Ein Ausgangs-Container, wie ihn dieses Projekt selbst schreibt: der
# kombinierte Ein/Aus-Befehl steht unmittelbar vor seinem `on` und traegt
# denselben `CmdOn` (`export.outputs.to_outputs`).
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
    """Anwenderbericht: nach Export und erneutem Import wollte der Sync ein
    zweites Feld "onoff" anlegen. Ursache war eine Schluesselkollision -
    kombinierter Ein/Aus-Befehl und einzelner `on`-Befehl tragen denselben
    `CmdOn`, sodass einer den anderen im Index ueberschrieb. Beide muessen
    unter ihrem EIGENEN Schluessel stehen; der kombinierte unter dem
    Doppelschluessel, den auch `export.outputs.to_outputs` vergibt."""
    index = build_index(PAIRED_OUTPUT_PROJECT)
    assert set(index.output_cmds) == {"d1_1_off", "d1_1_on", "d1_1_on + d1_1_off"}
    assert index.output_cmds["d1_1_on + d1_1_off"].attrs["Title"] == "onoff"
    assert index.output_cmds["d1_1_on"].attrs["Title"] == "on"
