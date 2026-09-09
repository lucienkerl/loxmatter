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
    """Like `tests/export/test_signals.py::load` - a real device fixture
    instead of the hand-built `_signal`/`_device` helpers above, because the
    test below really has to run through `Store.signals` (the new ranking
    from task 2 sits in `model.store._signal_order`, not in anything that
    could be reproduced with a single hand-built `StoredSignal`)."""
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


# Like `sample_project`, but the existing output command carries a
# corrupted `CmdOn` (missing "n": `/cmd/d1_1_o/1` instead of
# `/cmd/d1_1_onoff/1`) with the correct title "onoff" - exactly the case the
# user reported on their real file ("onoff shows up twice"):
# `key_from_cmd_on` reads the wrong key `d1_1_o` from it, so the
# actually intended object never turns up under the desired
# key `d1_1_on + d1_1_off`.
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
    # The title in the file is "Alter Titel", but `to_inputs` generates the
    # signal title "Ein/Aus" - so this MUST be `updated`, not `unchanged`.
    # This test documents the expected behavior for task step 3 below (see
    # the note there on the title divergence).
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
    """User report: an existing combined output command "onoff" with a
    corrupted `CmdOn` previously generated a second, genuine "onoff"
    command in the same container - a silent duplication. The desired
    combined command (title "onoff", key `d1_1_on + d1_1_off`) must instead
    be marked as `possible_duplicate`, NOT as `new_signal`."""
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

    # The corrupted old row itself stays visible as its own entry (under
    # its wrong key `d1_1_o`) - orphaned, left untouched.
    orphaned_keys = {e.key for e in plan.entries if e.status == PlanStatus.ORPHANED}
    assert "d1_1_o" in orphaned_keys

    # The individual "on"/"off" commands have no title conflict (the file
    # only knows the combined "onoff") - a perfectly normal new creation,
    # so it does make `has_changes` true (unlike the `possible_duplicate`
    # entry itself, which, like `orphaned`/`conflict`, is not a planned
    # change - see `SyncPlan.has_changes`).
    on_entry = next(e for e in plan.entries if e.key == "d1_1_on")
    assert on_entry.status == PlanStatus.NEW_SIGNAL
    assert plan.has_changes


def test_has_changes_is_false_when_everything_matches(sample_project):
    index = build_index(sample_project)
    device = _device(1, "Altes Geraet")
    # "Ein/Aus" instead of "Alter Titel", so this test really checks the
    # unchanged case.
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
    # "d9_9_verwaist" stays in the file but does not make has_changes true
    # - ORPHANED is a notice, not a planned change.
    assert plan.has_changes is False


def _project_with_inputs(inputs: Sequence[LoxoneInput], device_label: str = "Taster") -> str:
    """Builds - unlike `SAMPLE_PROJECT`/`CORRUPTED_ONOFF_PROJECT` above,
    which are fixed text and cannot be parametrized - a synthetic
    project file with EXACTLY one virtual input container that carries a
    `VirtualUdpInCmd` object for each `LoxoneInput` passed in, in
    the order of the given list. Same schema as
    `SAMPLE_PROJECT` (a `LoxLIVE` block with `VirtualInCaption` ->
    `VirtualUdpIn` -> `VirtualUdpInCmd`), just looped over the inputs
    instead of spelled out. Only use so far: the test below,
    for which the order is exactly what matters."""
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
    """A project file that a user imported BEFORE the ranking (task 2)
    carries its inputs in a different order.
    `_plan_inputs` looks up every entry via `index.input_cmds.get
    (entry.key)`, not by its position - no entry may therefore
    count as new (`NEW_SIGNAL`/`NEW_DEVICE`) and none as
    orphaned (`ORPHANED`).

    Without this test, the assertion in section 5 of the design would be
    just a claim. It is the only reason the order was allowed to
    change at the source (`model.store._signal_order`) instead of
    only in the display."""
    store = Store(tmp_path / "t.sqlite")
    try:
        snap = _load_snapshot("ikea_bilresa_button.json")
        device_id = store.register_device(snap)
        store.register_signals(device_id, snap)
        signals = store.signals(device_id)
    finally:
        store.close()

    inputs = to_inputs(signals, device_id, "Taster")
    # SOME order different from today's - which one is irrelevant:
    # the matching runs on the key, so NO reordering may
    # disturb it. Alphabetical by key is an arbitrary
    # permutation and thereby proves more than the one old order (which
    # applied before the ranking).
    shuffled = sorted(inputs, key=lambda i: i.key)
    project = _project_with_inputs(shuffled, device_label="Taster")
    index = build_index(project)

    device = _device(device_id, "Taster")
    plan = build_plan(index, [device], {device_id: signals}, {device_id: []})

    statuses = {e.key: e.status for e in plan.entries if e.kind == "input"}
    assert PlanStatus.NEW_SIGNAL not in statuses.values()
    assert PlanStatus.NEW_DEVICE not in statuses.values()
    assert PlanStatus.ORPHANED not in statuses.values()
    # Stronger than "not new/orphaned": `_project_with_inputs` writes, for
    # every key, exactly the attributes `desired_input_cmd_attrs` expects,
    # so every entry must be `UNCHANGED`. This matters because a
    # wrong (e.g. position-based instead of key-based) mapping
    # would NOT be visible as `NEW_SIGNAL`/`ORPHANED`, but as
    # `UPDATED` with a differing `Check` - verified firsthand: a
    # trial positional mapping in `_plan_inputs` left the three
    # asserts above green unchanged, while every entry actually
    # came out as `updated`. Only this extra line would have caught that.
    assert set(statuses.values()) == {PlanStatus.UNCHANGED}
