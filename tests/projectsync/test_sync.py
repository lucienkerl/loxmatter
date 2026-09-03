import json
from pathlib import Path

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.projectsync.sync import run_sync

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load_snapshot(name: str) -> NodeSnapshot:
    # `tests/api/conftest.py` definiert eine gleichnamige Hilfsfunktion, aber
    # `from conftest import load_snapshot` funktioniert nur fuer Testdateien,
    # die selbst in `tests/api/` liegen: Pytest reiht ohne `__init__.py` das
    # Verzeichnis JEDER Testdatei vorn in sys.path ein, und dieses Modul hier
    # liegt in `tests/projectsync/`, das bereits eine eigene `conftest.py`
    # hat - "conftest" loest also dorthin auf, nicht nach `tests/api/`.
    # Deshalb dieselbe lokale Ladefunktion wie in
    # `tests/model/test_store_commands.py`.
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def _plug_store(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
    return store


def test_run_sync_returns_plan_and_both_file_variants(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    result = run_sync(
        sample_project.encode("utf-8"), store, bridge_ip="10.0.0.5", port=7000, listen=8080
    )
    assert result.plan.entries  # nicht leer - die Steckdose hat Signale
    assert result.patched_conservative != result.patched_with_new_devices
    store.close()


def test_run_sync_raises_project_format_error_for_garbage(tmp_path):
    import pytest

    from loxmatter.projectsync.index import ProjectFormatError

    store = _plug_store(tmp_path)
    with pytest.raises(ProjectFormatError):
        run_sync(b"nicht xml", store, bridge_ip="10.0.0.5", port=7000, listen=8080)
    store.close()
