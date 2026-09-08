# loxmatter - connects Matter devices to a Loxone Miniserver.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Tests for the export via the API (Task 5, Phase 5) - see api/export.py."""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot
from typer.testing import CliRunner

from loxmatter.cli import app as cli_app
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


@pytest.fixture
async def api(
    tmp_path, no_invoke, fake_runtime
) -> AsyncIterator[tuple[httpx.AsyncClient, Store, int]]:
    """Like the `api` fixture in `test_devices.py`, but as a 3-tuple with no
    `fake_client` - the export router needs no Matter client."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, device_id
    store.close()


async def test_preview_reports_what_would_be_written(api):
    """`inputs` since Task 6: 5 relevant signals of the plug (see
    `tests/model/test_store.py::test_a_freshly_registered_plug_exports_only_its_meaningful_values`)
    plus the online signal, makes 6. `skipped` stays at 49 - it still only
    counts what's technically not mappable (`is_exportable`), unaffected by
    the new relevance selection."""
    client, _, device_id = api
    preview = (await client.get("/api/export/preview?bridge_ip=192.168.1.50")).json()
    device = next(d for d in preview["devices"] if d["device_id"] == device_id)
    assert device["inputs"] == 6
    assert device["commands"] == 3
    assert device["skipped"] == 49


async def test_the_preview_reports_how_many_signals_are_hidden(api):
    """`hidden_count` (Task 8): how many signals the UI hides in the
    collapsed "expert" block of the signals view - for the plug in the test
    template, 154 of 159 (see the `_signal_out` test in `test_devices.py`,
    only 5 are functional)."""
    client, _, device_id = api
    body = (await client.get("/api/export/preview?bridge_ip=10.0.0.1")).json()
    plug = next(d for d in body["devices"] if d["device_id"] == device_id)
    assert plug["hidden_count"] > 100


async def test_preview_does_not_write_anything(api, tmp_path):
    """Preview means preview."""
    client, _, _ = api
    before = set(tmp_path.iterdir())
    await client.get("/api/export/preview?bridge_ip=192.168.1.50")
    assert set(tmp_path.iterdir()) == before


async def test_preview_never_marks_a_device_as_exported(api):
    """Complements the test above: not just the filesystem, the database
    itself stays untouched too (Decision 1, api/export.py)."""
    client, store, device_id = api
    await client.get("/api/export/preview?bridge_ip=192.168.1.50")
    assert store.device(device_id).exported_at is None


async def test_download_returns_a_zip_with_both_templates(api):
    client, _, device_id = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert any(n.startswith(f"VIU_d{device_id}_") for n in names)
    assert any(n.startswith(f"VO_d{device_id}_") for n in names)


async def test_download_skips_the_vo_file_for_a_device_without_commands(
    tmp_path, no_invoke, fake_runtime
):
    """A button has no output commands - without this exception, the VO_
    file would be nothing but its empty skeleton, and importing it into
    Loxone Config would produce nothing but an empty template in the tree
    (the same rule as in
    `tests/test_export_cli.py::test_button_gets_no_output_commands`)."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_bilresa_button.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    store.close()

    assert response.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert any(n.startswith(f"VIU_d{device_id}_") for n in names)
    assert not any(n.startswith(f"VO_d{device_id}_") for n in names)


async def test_zip_contains_the_system_templates_and_a_readme(api):
    client, _, _ = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50&system=true")
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert "VIU_Matter_System.xml" in names
    assert "VO_Matter_System.xml" in names
    assert any(n.lower().endswith(".md") or n.lower().endswith(".txt") for n in names)


async def test_the_readme_explains_how_to_import_the_templates(api):
    """Task 6: `_readme_text()` resolves `api.export.readme_text` fresh on
    every call - see `test_the_readme_is_german_when_the_language_is_de`
    for the German companion test."""
    client, _, _ = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    name = next(n for n in archive.namelist() if n.endswith(".txt"))
    raw = archive.read(name)
    text = raw.decode("utf-8")
    assert "IMPORT INSTRUCTIONS" in text
    assert "Templates\\VirtualIn\\" in text
    # `_readme_text()` still appends the CRLF conversion to its return
    # value - the same Notepad-friendliness as before Task 6.
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


async def test_the_readme_is_german_when_the_language_is_de(api):
    """German companion test to
    `test_the_readme_explains_how_to_import_the_templates` -
    `store.locale.set_language`, not `i18n.set_language` directly: the
    `sync_language` middleware reads from the store fresh on every request
    (see Task 1)."""
    client, store, _ = api
    store.locale.set_language("de")
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    name = next(n for n in archive.namelist() if n.endswith(".txt"))
    text = archive.read(name).decode("utf-8")
    assert "IMPORT-ANLEITUNG" in text
    assert "Templates\\VirtualIn\\" in text


async def test_the_readme_filename_stays_language_neutral(api):
    """Review-fix Important (whole-branch review, 2026-09-04): Task 6
    translated only the CONTENT of the instructions file
    (`api.export.readme_text`) - the filename itself stayed hardcoded as
    `Import-Anleitung.txt`, even in an English-language export. A filename
    that changes with the UI language would complicate every script that
    expects the ZIP structure - it is therefore now ONE fixed,
    language-neutral value (`README.txt`), regardless of whether the store
    is set to English or German."""
    client, store, _ = api

    response_en = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    names_en = zipfile.ZipFile(io.BytesIO(response_en.content)).namelist()
    assert "README.txt" in names_en
    assert "Import-Anleitung.txt" not in names_en

    store.locale.set_language("de")
    response_de = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    names_de = zipfile.ZipFile(io.BytesIO(response_de.content)).namelist()
    assert "README.txt" in names_de
    assert "Import-Anleitung.txt" not in names_de


async def test_files_in_the_zip_keep_bom_and_crlf(api):
    """Spec 6.1: the format is measured, not negotiable - in the archive too."""
    client, _, _ = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    name = next(n for n in archive.namelist() if n.startswith("VIU_"))
    raw = archive.read(name)
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in raw.replace(b"\r\n", b"")


async def test_status_marks_a_device_as_never_exported(api):
    client, _, device_id = api
    status = (await client.get("/api/export/status")).json()
    entry = next(s for s in status if s["device_id"] == device_id)
    assert entry["exported_at"] is None
    assert entry["changed_since_export"] is True


async def test_missing_bridge_ip_yields_422(api):
    client, _, _ = api
    assert (await client.get("/api/export/preview")).status_code == 422


async def test_download_missing_bridge_ip_yields_422(api):
    client, _, _ = api
    assert (await client.get("/api/export/download")).status_code == 422


async def test_download_marks_a_device_as_exported(api):
    client, store, device_id = api
    await client.get("/api/export/download?bridge_ip=192.168.1.50")

    assert store.device(device_id).exported_at is not None
    status = (await client.get("/api/export/status")).json()
    entry = next(s for s in status if s["device_id"] == device_id)
    assert entry["exported_at"] is not None
    assert entry["changed_since_export"] is False


async def test_a_failure_partway_through_the_archive_marks_no_device(api, monkeypatch):
    """Review-fix Important #1, 2026-09-02: `download` used to mark every
    device IMMEDIATELY, while the ZIP was still being built - if building a
    later device failed (500, no ZIP for the client), the devices already
    processed still stayed marked as exported permanently. Two devices in
    the store, the second one deliberately makes `to_inputs` (called from
    `api.export.download`) fail - the first device has, by this point,
    already been fully written into the archive. After the fix, NEITHER of
    the two may be marked, because the archive never finished."""
    client, store, first_device_id = api
    second_snapshot = load_snapshot("example_light.json")
    second_device_id = store.register_device(second_snapshot)
    store.register_signals(second_device_id, second_snapshot)
    store.register_commands(
        second_device_id, extract_commands(second_snapshot), second_snapshot.node_id
    )
    assert store.devices()[0].id == first_device_id  # first device is processed first

    import loxmatter.api.export as export_module

    original_to_inputs = export_module.to_inputs

    def boom(signals, device_id, label):  # type: ignore[no-untyped-def]
        if device_id == second_device_id:
            raise RuntimeError("simulated crash while rendering the second device")
        return original_to_inputs(signals, device_id, label)

    monkeypatch.setattr(export_module, "to_inputs", boom)

    with pytest.raises(RuntimeError, match="simulated crash"):
        await client.get("/api/export/download?bridge_ip=192.168.1.50")

    assert store.device(first_device_id).exported_at is None
    assert store.device(second_device_id).exported_at is None


async def test_a_rename_after_export_marks_the_device_changed_again(api):
    """An export is not a freeze: if the device changes afterward - here via
    a rename - `GET /api/export/status` must report it."""
    client, _, device_id = api
    await client.get("/api/export/download?bridge_ip=192.168.1.50")

    rename = await client.patch(f"/api/devices/{device_id}", json={"label": "Neue Bezeichnung"})
    assert rename.status_code == 200

    status = (await client.get("/api/export/status")).json()
    entry = next(s for s in status if s["device_id"] == device_id)
    assert entry["changed_since_export"] is True


async def test_removed_devices_do_not_appear_in_preview_download_or_status(api):
    """A removed device keeps its id (Spec 6.2), but a template for a device
    that no longer exists is worse than none at all."""
    client, store, device_id = api
    store.forget_device(device_id)

    preview = (await client.get("/api/export/preview?bridge_ip=192.168.1.50")).json()
    assert preview["devices"] == []

    response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert not any(n.startswith((f"VIU_d{device_id}_", f"VO_d{device_id}_")) for n in names)

    status = (await client.get("/api/export/status")).json()
    assert status == []


async def test_an_empty_installation_yields_an_empty_preview_and_a_non_empty_zip(
    tmp_path, no_invoke, fake_runtime
):
    """No device commissioned: neither an empty ZIP file nor a 500 are
    acceptable responses - see api/export.py, `download`."""
    store = Store(tmp_path / "empty.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        preview = (await client.get("/api/export/preview?bridge_ip=192.168.1.50")).json()
        assert preview["devices"] == []

        response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
        assert response.status_code == 200
        names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
        assert names  # not empty - the quick-start guide is always included
        assert not any(n.startswith(("VIU_d", "VO_d")) for n in names)

        status = (await client.get("/api/export/status")).json()
        assert status == []
    store.close()


async def test_api_export_writes_the_same_database_as_the_cli(tmp_path, no_invoke, fake_runtime):
    """The core requirement of this task: the API and the CLI must write the
    same database - otherwise a device exported once via the CLI and once
    via the WebUI gets two sets of signal keys (see the module docstring of
    api/export.py)."""
    db_path = tmp_path / "shared.sqlite"
    out_dir = tmp_path / "cli_out"

    result = CliRunner().invoke(
        cli_app,
        [
            "export",
            "--fixture",
            str(FIXTURES / "ikea_grillplats_plug.json"),
            "--bridge-ip",
            "192.168.1.50",
            "--out",
            str(out_dir),
            "--store-path",
            str(db_path),
        ],
    )
    assert result.exit_code == 0, result.output
    cli_viu = next(out_dir.glob("VIU_*.xml")).read_bytes()
    cli_vo = next(out_dir.glob("VO_*.xml")).read_bytes()

    # The same file that `loxmatter run`/the WebUI would open on startup -
    # no second, independent store for the API.
    store = Store(db_path)
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        devices = (await client.get("/api/devices")).json()
        assert len(devices) == 1  # not two - CLI and API see the same device
        device_id = devices[0]["id"]

        response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    store.close()

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    api_viu = archive.read(
        next(n for n in archive.namelist() if n.startswith(f"VIU_d{device_id}_"))
    )
    api_vo = archive.read(next(n for n in archive.namelist() if n.startswith(f"VO_d{device_id}_")))

    # Byte-identical: the same keys, the same title, the same device_id -
    # the only possible outcome when both tools read the same database.
    assert api_viu == cli_viu
    assert api_vo == cli_vo


# ---------------------------------------------------------------------------
# The "only devices not yet exported" filter (review fix Fix 4,
# 2026-09-03). It previously applied only to the preview table in the UI;
# `/api/export/download` didn't know about it at all, always delivered
# every device and marked all of them as exported too. Anyone who filtered,
# saw a pending device and downloaded it got everything - and the filter
# was permanently empty afterward.
# ---------------------------------------------------------------------------


def _second_device(store: Store) -> int:
    """A second device in the same store - the fixture above only builds
    one, and a filter can't be demonstrated with a single device."""
    snapshot = load_snapshot("example_light.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
    return device_id


async def test_a_filtered_download_contains_exactly_the_devices_the_preview_shows(api):
    """Preview and download must make the same selection. The UI filters
    its table by `changed_since_export` from `GET /api/export/status`;
    exactly this condition also decides the content of the archive here."""
    client, store, first_id = api
    second_id = _second_device(store)

    # Export both, then change only the first device again.
    await client.get("/api/export/download?bridge_ip=192.168.1.50")
    assert (
        await client.patch(f"/api/devices/{first_id}", json={"label": "Neu"})
    ).status_code == 200

    status = (await client.get("/api/export/status")).json()
    pending = {s["device_id"] for s in status if s["changed_since_export"]}
    assert pending == {first_id}

    response = await client.get("/api/export/download?bridge_ip=192.168.1.50&only_pending=true")
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert any(n.startswith(f"VIU_d{first_id}_") for n in names)
    assert not any(n.startswith(f"VIU_d{second_id}_") for n in names)


async def test_a_filtered_download_marks_only_what_it_delivered(api):
    """The real damage of the old version wasn't the ZIP being too big, but
    the `mark_exported` for devices whose template never made it into the
    archive: afterward, everything counted as exported and the filter
    stayed empty forever."""
    client, store, first_id = api
    second_id = _second_device(store)

    await client.get("/api/export/download?bridge_ip=192.168.1.50")
    await client.patch(f"/api/devices/{first_id}", json={"label": "Neu"})
    second_exported_at = store.device(second_id).exported_at

    await client.get("/api/export/download?bridge_ip=192.168.1.50&only_pending=true")

    assert store.device(second_id).exported_at == second_exported_at
    status = (await client.get("/api/export/status")).json()
    assert {s["device_id"]: s["changed_since_export"] for s in status} == {
        first_id: False,
        second_id: False,
    }


async def test_an_unfiltered_download_still_contains_every_device(api):
    """The default stays "everything": `only_pending` is a filter someone
    explicitly sets, not a new default behavior."""
    client, store, first_id = api
    second_id = _second_device(store)

    await client.get("/api/export/download?bridge_ip=192.168.1.50")
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    for device_id in (first_id, second_id):
        assert any(n.startswith(f"VIU_d{device_id}_") for n in names)


async def test_the_interface_asks_for_the_filter_it_shows(api):
    """The two halves of the filter live in different files and different
    languages: the checkbox in `index.html`/`app.js`, the evaluation in
    `api/export.py`. Exactly this kind of drift was the bug - the UI
    filtered the table and never sent the filter along. All that's proven
    here is that the download URL carries the parameter; whether the
    checkbox is wired up correctly in the browser is something no test in
    this suite can say without a browser engine."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "only_pending" in script


# ---------------------------------------------------------------------------
# device_id: exporting a single device via the export button on the device
# tile (device dashboard design, 2026-09-03, section 6). No dedicated
# endpoint - the same `/api/export/download`, just restricted to one device.
# ---------------------------------------------------------------------------


async def test_download_with_device_id_contains_only_that_device(api):
    client, store, first_id = api
    second_id = _second_device(store)

    response = await client.get(f"/api/export/download?bridge_ip=192.168.1.50&device_id={first_id}")
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert any(n.startswith(f"VIU_d{first_id}_") for n in names)
    assert not any(n.startswith(f"VIU_d{second_id}_") for n in names)


async def test_download_with_device_id_marks_only_that_device_exported(api):
    client, store, first_id = api
    second_id = _second_device(store)

    await client.get(f"/api/export/download?bridge_ip=192.168.1.50&device_id={first_id}")

    assert store.device(first_id).exported_at is not None
    assert store.device(second_id).exported_at is None


async def test_download_with_device_id_ignores_only_pending(api):
    """`device_id` wins over `only_pending` (design section 6): the
    requested device is exported even if, per `changed_since_export`, it
    wasn't pending at all."""
    client, store, first_id = api
    await client.get(f"/api/export/download?bridge_ip=192.168.1.50&device_id={first_id}")
    assert store.device(first_id).exported_at is not None  # already exported, "not changing"

    response = await client.get(
        f"/api/export/download?bridge_ip=192.168.1.50&device_id={first_id}&only_pending=true"
    )
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert any(n.startswith(f"VIU_d{first_id}_") for n in names)


async def test_download_with_unknown_device_id_yields_404(api):
    client, _, _ = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50&device_id=999999")
    assert response.status_code == 404
