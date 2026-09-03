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


# Ein wohlgeformtes Projekt, in dem noch nie ein virtueller EINGANG angelegt
# wurde - es fehlt also der `VirtualInCaption`-Abschnitt, in den ein komplett
# neuer Eingangs-Container muesste. Realistischer Fall fuer jemanden, der
# bislang nur Vorlagen fuer Ausgaenge importiert hat.
NO_VIRTUAL_IN_CAPTION_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">'
    "</C>\r\n"
    "</ControlList>\r\n"
)


def test_run_sync_returns_plan_and_both_file_variants(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    result = run_sync(
        sample_project.encode("utf-8"), store, bridge_ip="10.0.0.5", port=7000, listen=8080
    )
    assert result.plan.entries  # nicht leer - die Steckdose hat Signale
    assert result.patched_with_new_devices is not None
    assert result.patched_conservative != result.patched_with_new_devices
    assert result.new_devices_unavailable_reason is None
    store.close()


def test_missing_caption_disables_only_the_experimental_variant(tmp_path):
    """Fehlt der `VirtualInCaption`-Abschnitt, ist das laut Entwurf Abschnitt
    8 eine Grenze des EXPERIMENTELLEN Pfades - kein Grund, den ganzen Upload
    scheitern zu lassen. Plan und konservative Variante muessen weiterhin
    entstehen, nur `patched_with_new_devices` faellt mit einer Begruendung
    weg."""
    store = _plug_store(tmp_path)
    result = run_sync(
        NO_VIRTUAL_IN_CAPTION_PROJECT.encode("utf-8"),
        store,
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
    )
    assert result.patched_with_new_devices is None
    assert result.new_devices_unavailable_reason
    assert "VirtualInCaption" in result.new_devices_unavailable_reason

    # Plan und konservative Variante sind davon voellig unberuehrt: die
    # konservative Variante legt nie einen Container an, kann also gar nicht
    # an einer fehlenden Caption scheitern.
    assert result.plan.entries
    assert result.patched_conservative.decode("utf-8-sig") == NO_VIRTUAL_IN_CAPTION_PROJECT
    store.close()


def test_run_sync_raises_project_format_error_for_garbage(tmp_path):
    import pytest

    from loxmatter.projectsync.index import ProjectFormatError

    store = _plug_store(tmp_path)
    with pytest.raises(ProjectFormatError):
        run_sync(b"nicht xml", store, bridge_ip="10.0.0.5", port=7000, listen=8080)
    store.close()


def test_run_sync_raises_project_format_error_for_non_utf8_upload(tmp_path):
    """Wer eine falsche Datei hochlaedt (Bild, ZIP, UTF-16-Export), bekommt
    beim Dekodieren einen `UnicodeDecodeError` - der ist keine
    `ProjectFormatError` und schlug bis in den Endpunkt als HTTP 500 durch.
    Erwartet ist die uebliche klare Meldung (Entwurf Abschnitt 8)."""
    import pytest

    from loxmatter.projectsync.index import ProjectFormatError

    store = _plug_store(tmp_path)
    # UTF-16-kodiertes "<ControlList/>" - gueltiger Text, nur eben nicht UTF-8.
    utf16 = "<ControlList/>".encode("utf-16")
    with pytest.raises(ProjectFormatError, match="UTF-8"):
        run_sync(utf16, store, bridge_ip="10.0.0.5", port=7000, listen=8080)
    store.close()
