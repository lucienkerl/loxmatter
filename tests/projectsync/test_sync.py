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
# wurde - es fehlt also der `VirtualInCaption`-Abschnitt. Realistischer Fall
# fuer jemanden, der bislang nur Vorlagen fuer Ausgaenge importiert hat;
# `apply_plan` legt diesen Abschnitt im experimentellen Pfad selbst mit an
# (Entwurf Abschnitt 8).
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


def test_missing_caption_is_auto_created_for_the_experimental_variant(tmp_path):
    """Fehlt der `VirtualInCaption`-Abschnitt, legt `apply_plan` ihn im
    experimentellen Pfad (`include_new_devices=True`) inzwischen selbst mit
    an (Entwurf Abschnitt 8, Nutzerwunsch nach dem Review) - kein manuelles
    Vorbereiten in Loxone Config mehr noetig, nur um den Pfad ueberhaupt zu
    erreichen. Die konservative Variante bleibt davon unberuehrt: sie legt
    nie einen Container an, kann also gar nicht an einer fehlenden Caption
    haengen."""
    store = _plug_store(tmp_path)
    result = run_sync(
        NO_VIRTUAL_IN_CAPTION_PROJECT.encode("utf-8"),
        store,
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
    )
    assert result.new_devices_unavailable_reason is None
    assert result.patched_with_new_devices is not None
    assert b'Type="VirtualInCaption"' in result.patched_with_new_devices

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


# Enthaelt schon einen `VirtualUdpIn`-Container fuer Geraet 1 (Praefix `d1_`,
# mit `d1_1_onoff`), aber KEIN einziges `U`-Attribut irgendwo im Dokument.
# `export.signals.to_inputs` erzeugt fuer JEDES Geraet zusaetzlich ein
# Online-Signal (`d1_online`) - das fehlt hier im Container, erzwingt also
# einen `NEW_SIGNAL`-Eintrag in einem bereits BESTEHENDEN Container. Anders
# als `NO_VIRTUAL_IN_CAPTION_PROJECT` oben (das einen `NEW_DEVICE`/
# `MissingCaptionError`-Pfad braucht) reicht das schon mit
# `include_new_devices=False` - `NEW_SIGNAL` ist von diesem Flag unabhaengig.
NO_U_ATTR_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="VirtualInCaption" IName="C1">\r\n'
    '\t\t<C Type="VirtualUdpIn" IName="VUI1" Title="Matter — Steckdose" WF="16384"'
    ' Address="10.0.0.5" Port="7000">\r\n'
    '\t\t\t<C Type="VirtualUdpInCmd" IName="VCI1" Title="Ein/Aus" Nio="2" WF="16384"'
    ' Check="d1_1_onoff:\\v" Analog="true">\r\n'
    '\t\t\t\t<IoData Cr="x" Pr="y"/>\r\n'
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


def test_run_sync_propagates_project_format_error_from_id_generation(tmp_path):
    """Finding N1, Punkt 3 aus dem Re-Review: ein `ProjectFormatError` aus
    `_installation_suffix` (kein `U`-Wert im erwarteten Format in der Datei)
    soll bewusst NICHT wie `MissingCaptionError` degradiert werden, sondern
    bis zum Aufrufer durchschlagen - `api.project_sync` faengt es zur
    verstaendlichen 400 ab. Anders als eine fehlende Caption (nur eine Grenze
    des experimentellen Pfades) heisst dieser Fehler "das ID-Format der Datei
    ist grundsaetzlich nicht erkennbar" (Entwurf Abschnitt 10) und soll darum
    den ganzen Upload scheitern lassen.

    Wichtig: dieser Fehler tritt schon bei der KONSERVATIVEN Variante auf
    (`include_new_devices=False`), die in `run_sync` VOR der experimentellen
    Variante berechnet wird und durch kein `try` geschuetzt ist - er kann
    also gar nicht erst bis zum `except MissingCaptionError`-Block der
    experimentellen Variante gelangen."""
    import pytest

    from loxmatter.projectsync.index import ProjectFormatError

    store = _plug_store(tmp_path)
    with pytest.raises(ProjectFormatError, match="Installations-Suffix"):
        run_sync(
            NO_U_ATTR_PROJECT.encode("utf-8"),
            store,
            bridge_ip="10.0.0.5",
            port=7000,
            listen=8080,
        )
    store.close()
