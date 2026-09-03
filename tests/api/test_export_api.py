# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

"""Tests fuer den Export ueber die API (Task 5, Phase 5) - siehe api/export.py."""

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
        await authenticate(store, client)
        yield client, store, device_id
    store.close()


async def test_preview_reports_what_would_be_written(api):
    """`inputs` seit Aufgabe 6: 5 relevante Signale der Steckdose (siehe
    `tests/model/test_store.py::test_a_freshly_registered_plug_exports_only_its_meaningful_values`)
    plus das Online-Signal, macht 6. `skipped` bleibt bei 49 - das zaehlt
    weiterhin nur technisch nicht Abbildbares (`is_exportable`), unberuehrt
    von der neuen Relevanz-Auswahl."""
    client, _, device_id = api
    preview = (await client.get("/api/export/preview?bridge_ip=192.168.1.50")).json()
    device = next(d for d in preview["devices"] if d["device_id"] == device_id)
    assert device["inputs"] == 6
    assert device["commands"] == 3
    assert device["skipped"] == 49


async def test_the_preview_reports_how_many_signals_are_hidden(api):
    """`hidden_count` (Aufgabe 8): wie viele Signale die Oberflaeche im
    zugeklappten "Experte"-Block der Signale-Ansicht versteckt - fuer die
    Steckdose der Testvorlage 154 von 159 (siehe `_signal_out`-Test in
    `test_devices.py`, nur 5 sind funktional)."""
    client, _, device_id = api
    body = (await client.get("/api/export/preview?bridge_ip=10.0.0.1")).json()
    plug = next(d for d in body["devices"] if d["device_id"] == device_id)
    assert plug["hidden_count"] > 100


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


async def test_download_skips_the_vo_file_for_a_device_without_commands(
    tmp_path, no_invoke, fake_runtime
):
    """Ein Taster hat keine Ausgangsbefehle - ohne diese Ausnahme waere die
    VO_-Datei nur ihr leeres Grundgeruest, und ein Import in Loxone Config
    braechte nichts ausser einer leeren Vorlage im Baum (dieselbe Regel wie
    in `tests/test_export_cli.py::test_button_gets_no_output_commands`)."""
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


async def test_a_failure_partway_through_the_archive_marks_no_device(api, monkeypatch):
    """Review-Fix Important #1, 2026-09-02: `download` markierte bislang
    jedes Geraet SOFORT, waehrend das ZIP noch aufgebaut wurde - schlug der
    Aufbau eines spaeteren Geraets fehl (500, kein ZIP beim Client), blieben
    die zuvor verarbeiteten Geraete trotzdem dauerhaft als exportiert
    vermerkt. Zwei Geraete im Store, das zweite laesst `to_inputs`
    (aufgerufen aus `api.export.download`) absichtlich scheitern - das
    erste Geraet ist zu diesem Zeitpunkt schon vollstaendig ins Archiv
    geschrieben. Nach dem Fix darf trotzdem KEINS der beiden markiert
    sein, weil das Archiv nie fertig wurde."""
    client, store, first_device_id = api
    second_snapshot = load_snapshot("example_light.json")
    second_device_id = store.register_device(second_snapshot)
    store.register_signals(second_device_id, second_snapshot)
    store.register_commands(
        second_device_id, extract_commands(second_snapshot), second_snapshot.node_id
    )
    assert store.devices()[0].id == first_device_id  # erstes Geraet wird zuerst verarbeitet

    import loxmatter.api.export as export_module

    original_to_inputs = export_module.to_inputs

    def boom(signals, device_id, label):  # type: ignore[no-untyped-def]
        if device_id == second_device_id:
            raise RuntimeError("simulierter Absturz beim Rendern des zweiten Geraets")
        return original_to_inputs(signals, device_id, label)

    monkeypatch.setattr(export_module, "to_inputs", boom)

    with pytest.raises(RuntimeError, match="simulierter Absturz"):
        await client.get("/api/export/download?bridge_ip=192.168.1.50")

    assert store.device(first_device_id).exported_at is None
    assert store.device(second_device_id).exported_at is None


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
        await authenticate(store, client)
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
        await authenticate(store, client)
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


# ---------------------------------------------------------------------------
# Der Filter "nur noch nicht exportierte Geraete" (Review-Fix Fix 4,
# 2026-09-03). Er galt vorher nur fuer die Vorschautabelle in der
# Oberflaeche; `/api/export/download` kannte ihn gar nicht, lieferte immer
# alle Geraete und markierte auch alle als exportiert. Wer filterte, ein
# ausstehendes Geraet sah und herunterlud, bekam alles - und der Filter war
# danach dauerhaft leer.
# ---------------------------------------------------------------------------


def _second_device(store: Store) -> int:
    """Ein zweites Geraet im selben Store - die Fixture oben baut nur eines,
    und ein Filter laesst sich an einem einzigen Geraet nicht zeigen."""
    snapshot = load_snapshot("example_light.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
    return device_id


async def test_a_filtered_download_contains_exactly_the_devices_the_preview_shows(api):
    """Vorschau und Download muessen dieselbe Auswahl treffen. Die
    Oberflaeche filtert ihre Tabelle nach `changed_since_export` aus
    `GET /api/export/status`; genau diese Bedingung entscheidet hier auch
    ueber den Inhalt des Archivs."""
    client, store, first_id = api
    second_id = _second_device(store)

    # Beide exportieren, danach nur das erste Geraet wieder aendern.
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
    """Der eigentliche Schaden der alten Fassung war nicht das zu grosse
    ZIP, sondern das `mark_exported` fuer Geraete, deren Vorlage nie im
    Archiv lag: danach galt alles als exportiert und der Filter blieb fuer
    immer leer."""
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
    """Die Voreinstellung bleibt "alles": `only_pending` ist ein Filter, den
    jemand ausdruecklich setzt, kein neues Standardverhalten."""
    client, store, first_id = api
    second_id = _second_device(store)

    await client.get("/api/export/download?bridge_ip=192.168.1.50")
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50")
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    for device_id in (first_id, second_id):
        assert any(n.startswith(f"VIU_d{device_id}_") for n in names)


async def test_the_interface_asks_for_the_filter_it_shows(api):
    """Die beiden Haelften des Filters stehen in verschiedenen Dateien und
    verschiedenen Sprachen: das Kaestchen in `index.html`/`app.js`, die
    Auswertung in `api/export.py`. Genau dieses Auseinanderlaufen war der
    Fehler - die Oberflaeche filterte die Tabelle und schickte den Filter
    nie mit. Belegt wird hier nur, dass die Download-URL den Parameter
    traegt; ob das Kaestchen im Browser richtig verdrahtet ist, kann ohne
    Browser-Engine kein Test dieser Suite sagen."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "only_pending" in script


# ---------------------------------------------------------------------------
# device_id: Export eines einzelnen Geraets ueber den Export-Knopf an der
# Geraetekarte (Geraete-Dashboard-Entwurf, 2026-09-03, Abschnitt 6). Kein
# eigener Endpunkt - derselbe `/api/export/download`, nur auf ein Geraet
# eingeschraenkt.
# ---------------------------------------------------------------------------


async def test_download_with_device_id_contains_only_that_device(api):
    client, store, first_id = api
    second_id = _second_device(store)

    response = await client.get(
        f"/api/export/download?bridge_ip=192.168.1.50&device_id={first_id}"
    )
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
    """`device_id` gewinnt gegen `only_pending` (Entwurf Abschnitt 6): das
    angeforderte Geraet wird exportiert, auch wenn es laut
    `changed_since_export` gar nicht ausstuende."""
    client, store, first_id = api
    await client.get(f"/api/export/download?bridge_ip=192.168.1.50&device_id={first_id}")
    assert store.device(first_id).exported_at is not None  # bereits exportiert, "nicht aenderend"

    response = await client.get(
        f"/api/export/download?bridge_ip=192.168.1.50&device_id={first_id}&only_pending=true"
    )
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert any(n.startswith(f"VIU_d{first_id}_") for n in names)


async def test_download_with_unknown_device_id_yields_404(api):
    client, _, _ = api
    response = await client.get("/api/export/download?bridge_ip=192.168.1.50&device_id=999999")
    assert response.status_code == 404
