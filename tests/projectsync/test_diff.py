from loxmatter.matter.models import SignalKind
from loxmatter.model.store import SignalRef, StoredDevice, StoredSignal
from loxmatter.profiles.table import Exportability
from loxmatter.projectsync.diff import PlanStatus, build_plan
from loxmatter.projectsync.index import build_index


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


def _device(device_id: int, label: str) -> StoredDevice:
    return StoredDevice(
        id=device_id,
        node_id=device_id,
        unique_id=f"u{device_id}",
        label=label,
        exported_at=None,
        updated_at=None,
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
