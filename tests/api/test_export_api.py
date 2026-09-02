"""Tests fuer den Export ueber die API (Task 5, Phase 5) - siehe api/export.py."""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2 as httpx
import pytest
from conftest import load_snapshot
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
    """Wie die `api`-Fixture in `test_devices.py`, aber als 3-Tupel ohne
    `fake_client` - der Export-Router braucht keinen Matter-Client."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store, device_id
    store.close()


async def test_preview_reports_what_would_be_written(api):
    client, _, device_id = api
    preview = (await client.get("/api/export/preview?bridge_ip=192.168.1.50")).json()
    device = next(d for d in preview["devices"] if d["device_id"] == device_id)
    assert device["inputs"] == 110
    assert device["commands"] == 3
    assert device["skipped"] == 50


async def test_preview_does_not_write_anything(api, tmp_path):
    """Vorschau heisst Vorschau."""
    client, _, _ = api
    before = set(tmp_path.iterdir())
    await client.get("/api/export/preview?bridge_ip=192.168.1.50")
    assert set(tmp_path.iterdir()) == before


async def test_preview_never_marks_a_device_as_exported(api):
    """Ergaenzt den obigen Test: nicht nur das Dateisystem, auch die
    Datenbank selbst bleibt unberuehrt (Entscheidung 1, api/export.py)."""
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


async def test_zip_contains_the_system_templates_and_a_readme(api):
    client, _, _ = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50&system=true")
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert "VIU_Matter_System.xml" in names
    assert "VO_Matter_System.xml" in names
    assert any(n.lower().endswith(".md") or n.lower().endswith(".txt") for n in names)


async def test_files_in_the_zip_keep_bom_and_crlf(api):
    """Spec 6.1: das Format ist gemessen, nicht verhandelbar - auch im Archiv."""
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


async def test_a_rename_after_export_marks_the_device_changed_again(api):
    """Ein Export ist kein Einfrieren: aendert sich das Geraet danach - hier
    ueber eine Umbenennung -, muss `GET /api/export/status` das melden."""
    client, _, device_id = api
    await client.get("/api/export/download?bridge_ip=192.168.1.50")

    rename = await client.patch(f"/api/devices/{device_id}", json={"label": "Neue Bezeichnung"})
    assert rename.status_code == 200

    status = (await client.get("/api/export/status")).json()
    entry = next(s for s in status if s["device_id"] == device_id)
    assert entry["changed_since_export"] is True


async def test_removed_devices_do_not_appear_in_preview_download_or_status(api):
    """Ein entferntes Geraet behaelt seine id (Spec 6.2), aber eine Vorlage
    fuer ein Geraet, das nicht mehr existiert, ist schlimmer als keine."""
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
    """Kein Geraet eingelernt: weder eine leere ZIP-Datei noch ein 500 sind
    akzeptable Antworten - siehe api/export.py, `download`."""
    store = Store(tmp_path / "empty.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        preview = (await client.get("/api/export/preview?bridge_ip=192.168.1.50")).json()
        assert preview["devices"] == []

        response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
        assert response.status_code == 200
        names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
        assert names  # nicht leer - die Kurzanleitung liegt immer bei
        assert not any(n.startswith(("VIU_d", "VO_d")) for n in names)

        status = (await client.get("/api/export/status")).json()
        assert status == []
    store.close()


async def test_api_export_writes_the_same_database_as_the_cli(tmp_path, no_invoke, fake_runtime):
    """Kernanforderung des Tasks: API und CLI muessen dieselbe Datenbank
    schreiben - sonst bekommt ein Geraet, das einmal per CLI und einmal per
    WebUI exportiert wird, zwei Saetze Signalschluessel (siehe Modul-
    Docstring von api/export.py)."""
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

    # Dieselbe Datei, wie sie `loxmatter run`/die WebUI beim Start oeffnen
    # wuerde - kein zweiter, unabhaengiger Speicher fuer die API.
    store = Store(db_path)
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        devices = (await client.get("/api/devices")).json()
        assert len(devices) == 1  # nicht zwei - CLI und API sehen dasselbe Geraet
        device_id = devices[0]["id"]

        response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    store.close()

    archive = zipfile.ZipFile(io.BytesIO(response.content))
    api_viu = archive.read(
        next(n for n in archive.namelist() if n.startswith(f"VIU_d{device_id}_"))
    )
    api_vo = archive.read(next(n for n in archive.namelist() if n.startswith(f"VO_d{device_id}_")))

    # Byte-identisch: dieselben Schluessel, derselbe Titel, dieselbe
    # device_id - der einzig moegliche Ausgang, wenn beide Werkzeuge
    # dieselbe Datenbank lesen.
    assert api_viu == cli_viu
    assert api_vo == cli_vo
