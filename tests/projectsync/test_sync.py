import json
from pathlib import Path

from loxmatter.export.commands import extract_commands
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.projectsync.sync import run_sync

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load_snapshot(name: str) -> NodeSnapshot:
    # `tests/api/conftest.py` defines a helper function with the same name,
    # but `from conftest import load_snapshot` only works for test files
    # that themselves live in `tests/api/`: without an `__init__.py`, pytest
    # puts EVERY test file's directory at the front of sys.path, and this
    # module here lives in `tests/projectsync/`, which already has its own
    # `conftest.py` - so "conftest" resolves there, not to `tests/api/`.
    # Hence the same local loading function as in
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


# A well-formed project in which no virtual INPUT has ever been created -
# so the `VirtualInCaption` section is missing. A realistic case for
# someone who has so far only imported templates for outputs; `apply_plan`
# creates this section itself (draft section 8).
NO_VIRTUAL_IN_CAPTION_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="Document" U="2000-0000-0000-aaaaaaaaaaaaaaaa" Title="Testprojekt">\r\n'
    '\t\t<C Type="LoxLIVE" U="2000-0001-0000-aaaaaaaaaaaaaaaa" Title="Testserver"'
    ' IntAddr="10.0.0.10" Serial="504F00000000">\r\n'
    '\t\t\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">'
    "</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


def test_run_sync_returns_plan_and_patched_file(tmp_path, sample_project):
    store = _plug_store(tmp_path)
    result = run_sync(
        sample_project.encode("utf-8"), store, bridge_ip="10.0.0.5", port=7000, listen=8080
    )
    assert result.plan.entries  # not empty - the outlet has signals
    # The plug is device 1, whose container `sample_project` already has -
    # its power signal is missing there and arrives as a new signal.
    assert b'Check="d1_2_power:' not in sample_project.encode("utf-8")
    assert b'Check="d1_2_power:' in result.patched
    store.close()


def test_new_device_and_missing_caption_are_created(tmp_path):
    """The plug has no container in this file, and the file has no
    `VirtualInCaption` section to put one in: `apply_plan` creates both
    (draft section 8, user request after the review) - no manual
    preparation in Loxone Config first, and, since 2026-09-11, no option
    to opt in to either (design section 3.4)."""
    store = _plug_store(tmp_path)
    result = run_sync(
        NO_VIRTUAL_IN_CAPTION_PROJECT.encode("utf-8"),
        store,
        bridge_ip="10.0.0.5",
        port=7000,
        listen=8080,
    )
    assert result.plan.entries
    assert b'Type="VirtualInCaption"' not in NO_VIRTUAL_IN_CAPTION_PROJECT.encode("utf-8")
    assert b'Type="VirtualInCaption"' in result.patched
    assert b'Type="VirtualUdpIn"' in result.patched
    store.close()


def test_run_sync_raises_project_format_error_for_garbage(tmp_path):
    import pytest

    from loxmatter.projectsync.index import ProjectFormatError

    store = _plug_store(tmp_path)
    with pytest.raises(ProjectFormatError):
        run_sync(b"nicht xml", store, bridge_ip="10.0.0.5", port=7000, listen=8080)
    store.close()


def test_run_sync_raises_project_format_error_for_non_utf8_upload(tmp_path):
    """Whoever uploads the wrong file (image, ZIP, UTF-16 export) gets a
    `UnicodeDecodeError` while decoding - that is not a `ProjectFormatError`
    and propagated all the way to the endpoint as an HTTP 500. Expected is
    the usual clear message (draft section 8)."""
    import pytest

    from loxmatter.projectsync.index import ProjectFormatError

    store = _plug_store(tmp_path)
    # UTF-16-encoded "<ControlList/>" - valid text, just not UTF-8.
    utf16 = "<ControlList/>".encode("utf-16")
    with pytest.raises(ProjectFormatError, match="UTF-8"):
        run_sync(utf16, store, bridge_ip="10.0.0.5", port=7000, listen=8080)
    store.close()


# Already contains a `VirtualUdpIn` container for device 1 (prefix `d1_`,
# with `d1_1_onoff`), but NOT A SINGLE `U` attribute anywhere in the
# document. `export.signals.to_inputs` additionally generates an online
# signal (`d1_online`) for EVERY device - that is missing here in the
# container, so it forces a `NEW_SIGNAL` entry in an already EXISTING
# container - no completely new device needed to reach the id generation.
NO_U_ATTR_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="Document">\r\n'
    '\t\t<C Type="LoxLIVE" Title="Testserver" IntAddr="10.0.0.10">\r\n'
    '\t\t\t<C Type="VirtualInCaption" IName="C1">\r\n'
    '\t\t\t\t<C Type="VirtualUdpIn" IName="VUI1" Title="Matter — Steckdose" WF="16384"'
    ' Address="10.0.0.5" Port="7000">\r\n'
    '\t\t\t\t\t<C Type="VirtualUdpInCmd" IName="VCI1" Title="Ein/Aus" Nio="2" WF="16384"'
    ' Check="d1_1_onoff:\\v" Analog="true">\r\n'
    '\t\t\t\t\t\t<IoData Cr="x" Pr="y"/>\r\n'
    "\t\t\t\t\t</C>\r\n"
    "\t\t\t\t</C>\r\n"
    "\t\t\t</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


def test_run_sync_propagates_project_format_error_from_id_generation(tmp_path):
    """Finding N1, point 3 from the re-review: a `ProjectFormatError` from
    `_installation_suffix` (no `U` value in the expected format in the
    file) should deliberately propagate through to the caller -
    `api.project_sync` catches it into an understandable 400. This error
    means "the file's id format is fundamentally unrecognizable" (draft
    section 10) and should therefore fail the whole upload."""
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
