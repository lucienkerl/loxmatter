import json
from collections.abc import Sequence
from pathlib import Path

from loxmatter.export.signals import LoxoneInput, to_inputs
from loxmatter.matter.models import NodeSnapshot, SignalKind
from loxmatter.model.store import SignalRef, Store, StoredCommand, StoredDevice, StoredSignal
from loxmatter.profiles.table import Exportability
from loxmatter.projectsync.diff import PlanStatus, build_plan
from loxmatter.projectsync.index import build_index

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def _signal(key: str, device_id: int, endpoint: int = 1) -> StoredSignal:
    return StoredSignal(
        key=key,
        ref=SignalRef(endpoint=endpoint, cluster_id=6, element_id=0, kind=SignalKind.ATTRIBUTE),
        title="Ein/Aus",
        unit="",
        exportability=Exportability.DIGITAL,
        device_id=device_id,
        exported=True,
        functional=True,
        resend=False,
    )


def _command(key: str, slug: str, device_id: int, command_id: int) -> StoredCommand:
    return StoredCommand(
        key=key,
        slug=slug,
        node_id=device_id,
        endpoint=1,
        cluster_id=6,
        command_id=command_id,
        takes_value=False,
        device_id=device_id,
    )


def _device(device_id: int, label: str) -> StoredDevice:
    return StoredDevice(
        id=device_id,
        node_id=device_id,
        unique_id=f"u{device_id}",
        label=label,
        exported_at=None,
        updated_at=None,
        room=None,
        device_types=None,
    )


def _load_snapshot(name: str) -> NodeSnapshot:
    """Wie `tests/export/test_signals.py::load` - eine echte Geraete-Fixture
    statt der handgebauten `_signal`/`_device`-Helfer oben, weil der Test
    unten `Store.signals` wirklich durchlaufen muss (die neue Rangordnung
    aus Aufgabe 2 steckt in `model.store._signal_order`, nicht in etwas, das
    sich mit einer einzelnen handgebauten `StoredSignal` nachbilden liesse)."""
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


# Wie `sample_project`, aber der bestehende Ausgangsbefehl traegt ein
# beschaedigtes `CmdOn` (fehlendes "n": `/cmd/d1_1_o/1` statt
# `/cmd/d1_1_onoff/1`) bei korrektem Titel "onoff" - genau der Fall, den der
# Anwender an seiner echten Datei gemeldet hat ("zwei mal onoff drin"):
# `key_from_cmd_on` liest daraus den falschen Schluessel `d1_1_o`, das
# eigentlich gemeinte Objekt taucht also nirgends unter dem gewuenschten
# Schluessel `d1_1_on + d1_1_off` auf.
CORRUPTED_ONOFF_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="Document" U="2000-0000-0000-aaaaaaaaaaaaaaaa" Title="Testprojekt">\r\n'
    '\t\t<C Type="LoxLIVE" U="2000-0001-0000-aaaaaaaaaaaaaaaa" Title="Testserver"'
    ' IntAddr="10.0.0.10" Serial="504F00000000">\r\n'
    '\t\t\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">\r\n'
    '\t\t\t\t<C Type="VirtualOut" IName="VQ1" U="1000-000b-0000-aaaaaaaaaaaaaaaa"'
    ' Title="Matter — Altes Geraet" WF="16384" Address="http://10.0.0.9:8080"'
    ' CloseAfterSend="true" CmdSep=";">\r\n'
    '\t\t\t\t\t<C Type="VirtualOutCmd" IName="VQC1" U="1000-000c-0000-aaaaaaaaaaaaaaaa"'
    ' Title="onoff" Nio="1" WF="16400" CmdOn="/cmd/d1_1_o/1" CmdOnMethod="1" Tx="false">\r\n'
    '\t\t\t\t\t\t<Co K="I" U="1000-000d-0000-bbbbbbbbbbbbbbbb"/>\r\n'
    '\t\t\t\t\t\t<IoData Cr="1000-0005-0000-aaaaaaaaaaaaaaaa" Pr="1000-0006-0000-aaaaaaaaaaaaaaaa"/>\r\n'
    '\t\t\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
    "\t\t\t\t\t</C>\r\n"
    "\t\t\t\t</C>\r\n"
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


def test_existing_matching_input_is_unchanged(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1)]
    plan = build_plan(index, [device], {1: signals}, {1: []})
    entry = next(e for e in plan.entries if e.key == "d1_1_onoff")
    # Titel in der Datei ist "Alter Titel", `to_inputs` erzeugt aber den
    # Signal-Titel "Ein/Aus" - das MUSS also `updated` sein, nicht
    # `unchanged`. Dieser Test dokumentiert das erwartete Verhalten fuer
    # Task-Step 3 unten (siehe dortige Anmerkung zur Titel-Divergenz).
    assert entry.status == PlanStatus.UPDATED
    assert entry.changes["Title"] == ("Alter Titel", "Ein/Aus")


def test_new_signal_in_existing_container(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1), _signal("d1_1_temp", 1)]
    plan = build_plan(index, [device], {1: signals}, {1: []})
    entry = next(e for e in plan.entries if e.key == "d1_1_temp")
    assert entry.status == PlanStatus.NEW_SIGNAL


def test_new_device_has_no_existing_container(sample_project):
    index = build_index(sample_project)
    device = _device(2, "Neues Geraet")
    signals = [_signal("d2_1_onoff", 2)]
    plan = build_plan(index, [device], {2: signals}, {2: []})
    entry = next(e for e in plan.entries if e.key == "d2_1_onoff")
    assert entry.status == PlanStatus.NEW_DEVICE


def test_orphaned_signal_is_reported(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    signals = [_signal("d1_1_onoff", 1)]
    plan = build_plan(index, [device], {1: signals}, {1: []})
    orphaned = [e for e in plan.entries if e.status == PlanStatus.ORPHANED]
    assert any(e.key == "d9_9_verwaist" for e in orphaned)


def test_title_collision_with_mismatched_key_is_possible_duplicate():
    """Anwenderbericht: ein bestehender kombinierter Ausgangsbefehl "onoff"
    mit beschaedigtem `CmdOn` erzeugte bislang einen zweiten, echten
    "onoff"-Befehl im selben Container - eine stille Dopplung. Der
    gewuenschte kombinierte Befehl (Titel "onoff", Schluessel
    `d1_1_on + d1_1_off`) muss stattdessen als `possible_duplicate` markiert
    werden, NICHT als `new_signal`."""
    index = build_index(CORRUPTED_ONOFF_PROJECT)
    device = _device(1, "Altes Geraet")
    commands = [
        _command("d1_1_on", "on", 1, 1),
        _command("d1_1_off", "off", 1, 0),
    ]
    plan = build_plan(index, [device], {1: []}, {1: commands})

    onoff = next(e for e in plan.entries if e.title == "onoff")
    assert onoff.status == PlanStatus.POSSIBLE_DUPLICATE
    assert onoff.key == "d1_1_on + d1_1_off"

    # Die beschaedigte alte Zeile selbst bleibt als eigener Eintrag sichtbar
    # (unter ihrem falschen Schluessel `d1_1_o`) - verwaist, nicht angetastet.
    orphaned_keys = {e.key for e in plan.entries if e.status == PlanStatus.ORPHANED}
    assert "d1_1_o" in orphaned_keys

    # Die einzelnen "on"/"off"-Befehle haben keinen Titel-Konflikt (die
    # Datei kennt nur den kombinierten "onoff") - ganz normale Neuanlage,
    # macht `has_changes` also wahr (anders als der `possible_duplicate`-
    # Eintrag selbst, der wie `orphaned`/`conflict` keine geplante Aenderung
    # ist - siehe `SyncPlan.has_changes`).
    on_entry = next(e for e in plan.entries if e.key == "d1_1_on")
    assert on_entry.status == PlanStatus.NEW_SIGNAL
    assert plan.has_changes


def test_has_changes_is_false_when_everything_matches(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    # "Ein/Aus" statt "Alter Titel", damit dieser Test wirklich den
    # unveraenderten Fall prueft.
    signal = _signal("d1_1_onoff", 1)
    signal_matching_title = StoredSignal(
        key=signal.key,
        ref=signal.ref,
        title="Alter Titel",
        unit=signal.unit,
        exportability=signal.exportability,
        device_id=signal.device_id,
        exported=signal.exported,
        functional=signal.functional,
        resend=signal.resend,
    )
    plan = build_plan(index, [device], {1: [signal_matching_title]}, {1: []})
    onoff = next(e for e in plan.entries if e.key == "d1_1_onoff")
    assert onoff.status == PlanStatus.UNCHANGED
    # "d9_9_verwaist" bleibt in der Datei, macht has_changes aber nicht wahr
    # - ORPHANED ist eine Meldung, keine geplante Aenderung.
    assert plan.has_changes is False


def _project_with_inputs(inputs: Sequence[LoxoneInput], device_label: str = "Taster") -> str:
    """Baut - anders als `SAMPLE_PROJECT`/`CORRUPTED_ONOFF_PROJECT` oben,
    die als fester Text nicht parametrisierbar sind - eine synthetische
    Projektdatei mit GENAU einem virtuellen Eingangs-Container, der fuer
    jeden uebergebenen `LoxoneInput` ein `VirtualUdpInCmd`-Objekt traegt, in
    der Reihenfolge der uebergebenen Liste. Gleiches Schema wie
    `SAMPLE_PROJECT` (ein `LoxLIVE`-Block mit `VirtualInCaption` ->
    `VirtualUdpIn` -> `VirtualUdpInCmd`), nur ueber die Eingaenge geschleift
    statt ausgeschrieben. Einziger Verwendungszweck bisher: der Test unten,
    dem es gerade auf die Reihenfolge ankommt."""
    cmds = "".join(
        f'\t\t\t\t\t<C Type="VirtualUdpInCmd" IName="VCI{i}" U="1000-{i:04x}-0000-aaaaaaaaaaaaaaaa"'
        f' Title="{entry.title}" Nio="2" WF="16384" Check="{entry.key}:{entry.check_suffix}"'
        f' Signed="true" Analog="{"true" if entry.analog else "false"}" SourceValHigh="100"'
        ' DestValHigh="100" MinVal="-10000" MaxVal="10000" MinChange="0.25" MinTime="1000">\r\n'
        f'\t\t\t\t\t\t<Co K="AQ" U="1000-{i:04x}-0001-bbbbbbbbbbbbbbbb"/>\r\n'
        f'\t\t\t\t\t\t<Co K="Q" U="1000-{i:04x}-0002-bbbbbbbbbbbbbbbb"/>\r\n'
        '\t\t\t\t\t\t<Display Unit="&lt;v.1&gt;" StateOnly="true"/>\r\n'
        "\t\t\t\t\t</C>\r\n"
        for i, entry in enumerate(inputs, start=1)
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\r\n'
        '<ControlList Version="275" NextObj="100">\r\n'
        '\t<C Type="Document" U="2000-0000-0000-aaaaaaaaaaaaaaaa" Title="Testprojekt">\r\n'
        '\t\t<C Type="LoxLIVE" U="2000-0001-0000-aaaaaaaaaaaaaaaa" Title="Testserver"'
        ' IntAddr="10.0.0.10" Serial="504F00000000">\r\n'
        '\t\t\t<C Type="VirtualInCaption" IName="C1" U="1000-0000-0000-aaaaaaaaaaaaaaaa">\r\n'
        '\t\t\t\t<C Type="VirtualUdpIn" IName="VUI1" U="1000-0001-0000-aaaaaaaaaaaaaaaa"'
        f' Title="Matter — {device_label}" WF="16384" Address="10.0.0.5" Port="7000">\r\n'
        f"{cmds}"
        "\t\t\t\t</C>\r\n"
        "\t\t\t</C>\r\n"
        "\t\t</C>\r\n"
        "\t</C>\r\n"
        "</ControlList>\r\n"
    )


def test_a_project_imported_before_the_reordering_still_matches(tmp_path):
    """Eine Projektdatei, die ein Anwender VOR der Rangliste (Aufgabe 2)
    importiert hat, fuehrt ihre Eingaenge in einer anderen Reihenfolge.
    `_plan_inputs` schlaegt jeden Eintrag ueber `index.input_cmds.get
    (entry.key)` nach, nicht ueber seine Position - kein Eintrag darf
    deshalb als neu gelten (`NEW_SIGNAL`/`NEW_DEVICE`) und keiner als
    verwaist (`ORPHANED`).

    Ohne diesen Test waere die Zusicherung aus Abschnitt 5 des Entwurfs
    eine Behauptung. Sie ist der einzige Grund, warum die Reihenfolge an
    der Quelle (`model.store._signal_order`) geaendert werden durfte statt
    nur in der Anzeige."""
    store = Store(tmp_path / "t.sqlite")
    try:
        snap = _load_snapshot("ikea_bilresa_button.json")
        device_id = store.register_device(snap)
        store.register_signals(device_id, snap)
        signals = store.signals(device_id)
    finally:
        store.close()

    inputs = to_inputs(signals, device_id, "Taster")
    # EINE andere Reihenfolge als die heutige - welche, ist gleichgueltig:
    # der Abgleich laeuft ueber den Schluessel, also darf ihn KEINE
    # Umsortierung stoeren. Alphabetisch nach Schluessel ist eine beliebige
    # Permutation und beweist damit mehr als die eine alte Ordnung (die vor
    # der Rangliste galt).
    shuffled = sorted(inputs, key=lambda i: i.key)
    project = _project_with_inputs(shuffled, device_label="Taster")
    index = build_index(project)

    device = _device(device_id, "Taster")
    plan = build_plan(index, [device], {device_id: signals}, {device_id: []})

    statuses = {e.key: e.status for e in plan.entries if e.kind == "input"}
    assert PlanStatus.NEW_SIGNAL not in statuses.values()
    assert PlanStatus.NEW_DEVICE not in statuses.values()
    assert PlanStatus.ORPHANED not in statuses.values()
    # Staerker als "nicht neu/verwaist": `_project_with_inputs` schreibt zu
    # jedem Schluessel exakt die von `desired_input_cmd_attrs` erwarteten
    # Attribute, jeder Eintrag muss also `UNCHANGED` sein. Wichtig, weil eine
    # falsche (z. B. positionsbasierte statt schluesselbasierte) Zuordnung
    # NICHT als `NEW_SIGNAL`/`ORPHANED` sichtbar wuerde, sondern als
    # `UPDATED` mit abweichendem `Check` - am eigenen Leib geprueft: eine
    # testweise positionale Zuordnung in `_plan_inputs` liess die drei
    # Asserts oben unveraendert gruen, waehrend jeder Eintrag tatsaechlich
    # auf `updated` stand. Nur diese zusaetzliche Zeile haette das gefangen.
    assert set(statuses.values()) == {PlanStatus.UNCHANGED}
