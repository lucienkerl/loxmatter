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

"""Tests fuer POST /api/export/project-sync - siehe api/project_sync.py."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"

SAMPLE_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="VirtualInCaption" IName="C1" U="1000-0000-0000-aaaaaaaaaaaaaaaa">'
    "</C>\r\n"
    '\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">'
    "</C>\r\n"
    "</ControlList>\r\n"
)

# Absichtlich OHNE `VirtualInCaption`-Abschnitt - anders als SAMPLE_PROJECT
# oben. Ein reales Projekt, in dem noch nie ein virtueller Eingang angelegt
# wurde, sieht so aus (siehe `tests/projectsync/test_patch.py`,
# NO_VIRTUAL_IN_CAPTION_PROJECT, fuer dasselbe Muster auf Ebene von
# `patch.apply_plan`). `run_sync` (Task 10) berechnet IMMER beide Varianten
# in einem Aufruf, auch die `include_new_devices=True`-Variante - dieser
# Endpunkt muss also `MissingCaptionError` behandeln, sobald ein hochgeladenes
# Projekt fuer eine Geraete-Art (hier: Eingaenge) gar keinen Caption-Abschnitt
# hat UND mindestens ein neues Geraet dieser Art einbringt, unabhaengig davon,
# ob die Anfrage selbst `include_new_devices` ueberhaupt anfordert.
NO_VIRTUAL_IN_CAPTION_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">'
    "</C>\r\n"
    "</ControlList>\r\n"
)


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[tuple[httpx.AsyncClient, Store]]:
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store
    store.close()


async def test_project_sync_returns_plan_and_both_variants(api):
    client, _store = api
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={"file": ("projekt.Loxone", SAMPLE_PROJECT.encode("utf-8"), "application/xml")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["entries"]
    assert body["has_changes"] is True
    conservative = base64.b64decode(body["patched_conservative_base64"])
    with_new_devices = base64.b64decode(body["patched_with_new_devices_base64"])
    assert b"VirtualUdpIn" not in conservative  # Neuanlage nur mit dem Haken
    assert b"VirtualUdpIn" in with_new_devices


async def test_project_sync_rejects_invalid_file(api):
    client, _store = api
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={"file": ("kaputt.Loxone", b"kein xml", "application/xml")},
    )
    assert response.status_code == 400


async def test_project_sync_requires_authentication(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/export/project-sync",
            params={"bridge_ip": "10.0.0.5"},
            files={"file": ("p.Loxone", SAMPLE_PROJECT.encode("utf-8"), "application/xml")},
        )
    assert response.status_code == 401
    store.close()


async def test_project_sync_reports_missing_caption_as_400(api):
    """Ein wohlgeformtes Projekt ohne `VirtualInCaption`-Abschnitt, hochgeladen
    fuer ein Geraet, das komplett neu ist (die Steckdose aus der `api`-Fixture
    hat keinen passenden Container in `NO_VIRTUAL_IN_CAPTION_PROJECT`): `
    run_sync` wirft dabei `MissingCaptionError` beim Aufbau der
    `include_new_devices=True`-Variante (siehe Modul-Docstring oben) - das
    darf nicht als 500 durchschlagen, sondern muss als 400 mit einer
    Fehlermeldung ankommen, die den fehlenden Abschnitt nennt."""
    client, _store = api
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={
            "file": (
                "ohne_caption.Loxone",
                NO_VIRTUAL_IN_CAPTION_PROJECT.encode("utf-8"),
                "application/xml",
            )
        },
    )
    assert response.status_code == 400
    assert "VirtualInCaption" in response.json()["detail"]
