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

"""Tests for POST /api/export/project-sync - see api/project_sync.py."""

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
    '\t<C Type="Document" U="2000-0000-0000-aaaaaaaaaaaaaaaa" Title="Testprojekt">\r\n'
    '\t\t<C Type="LoxLIVE" U="2000-0001-0000-aaaaaaaaaaaaaaaa" Title="Testserver"'
    ' IntAddr="10.0.0.10" Serial="504F00000000">\r\n'
    '\t\t\t<C Type="VirtualInCaption" IName="C1" U="1000-0000-0000-aaaaaaaaaaaaaaaa">'
    "</C>\r\n"
    '\t\t\t<C Type="VirtualOutCaption" IName="C2" U="1000-000a-0000-aaaaaaaaaaaaaaaa">'
    "</C>\r\n"
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)

# Deliberately WITHOUT a `VirtualInCaption` section - unlike SAMPLE_PROJECT
# above. A real project in which no virtual input has ever been created
# looks like this (see `tests/projectsync/test_patch.py`,
# NO_VIRTUAL_IN_CAPTION_PROJECT, for the same pattern at the level of
# `patch.apply_plan`). `apply_plan` creates this section itself on the
# experimental path (design section 8: the special case of first-time
# creation, also behind the experimental toggle) - no manual prep in Loxone
# Config needed any more.
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


# Two `LoxLIVE` blocks - triggers `AmbiguousMiniserverError` with
# `candidates` when `miniserver_ip` is missing (see
# `tests/projectsync/test_index.py`,
# `test_multi_loxlive_without_ip_carries_candidates_for_a_selection_field`,
# for the same fixture at the level of `index.build_index`).
TWO_LOXLIVE_PROJECT = (
    '<?xml version="1.0" encoding="utf-8"?>\r\n'
    '<ControlList Version="275" NextObj="100">\r\n'
    '\t<C Type="Document" U="2000-0000-0000-aaaaaaaaaaaaaaaa" Title="Testprojekt">\r\n'
    '\t\t<C Type="LoxLIVE" U="2000-0001-0000-aaaaaaaaaaaaaaaa" Title="Erster Miniserver"'
    ' IntAddr="10.0.0.10" Serial="504F00000001">\r\n'
    '\t\t\t<C Type="VirtualInCaption" IName="C1" U="1000-0000-0000-aaaaaaaaaaaaaaaa"></C>\r\n'
    "\t\t</C>\r\n"
    '\t\t<C Type="LoxLIVE" U="2000-0002-0000-aaaaaaaaaaaaaaaa" Title="Zweiter Miniserver"'
    ' IntAddr="10.0.0.20" Serial="504F00000002">\r\n'
    '\t\t\t<C Type="VirtualInCaption" IName="C2" U="1000-0003-0000-aaaaaaaaaaaaaaaa"></C>\r\n'
    "\t\t</C>\r\n"
    "\t</C>\r\n"
    "</ControlList>\r\n"
)


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[tuple[httpx.AsyncClient, Store]]:
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot))

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
    assert body["new_devices_unavailable_reason"] is None
    conservative = base64.b64decode(body["patched_conservative_base64"])
    with_new_devices = base64.b64decode(body["patched_with_new_devices_base64"])
    assert b"VirtualUdpIn" not in conservative  # new creation only with the toggle
    assert b"VirtualUdpIn" in with_new_devices


async def test_project_sync_rejects_invalid_file(api):
    client, _store = api
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={"file": ("kaputt.Loxone", b"kein xml", "application/xml")},
    )
    assert response.status_code == 400


async def test_project_sync_offers_a_selection_for_multiple_miniservers(api):
    """User request from the review: with multiple Miniservers in the file,
    the WebUI should be able to show a selection field instead of making
    the user type the IP by hand - that needs a normal 200 response with
    the Miniservers found, not an error."""
    client, _store = api
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={
            "file": ("mehrere_ms.Loxone", TWO_LOXLIVE_PROJECT.encode("utf-8"), "application/xml")
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["needs_miniserver_selection"] is True
    assert body["available_miniservers"] == [
        {"title": "Erster Miniserver", "int_addr": "10.0.0.10"},
        {"title": "Zweiter Miniserver", "int_addr": "10.0.0.20"},
    ]
    # All plan-specific fields stay empty - there is no plan (yet).
    assert body["entries"] == []
    assert body["patched_conservative_base64"] is None


async def test_project_sync_with_selected_miniserver_returns_the_plan(api):
    """The same upload, this time with the IP chosen from the selection
    field - returns the normal plan, no more selection field."""
    client, _store = api
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5", "miniserver_ip": "10.0.0.10"},
        files={
            "file": ("mehrere_ms.Loxone", TWO_LOXLIVE_PROJECT.encode("utf-8"), "application/xml")
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["needs_miniserver_selection"] is False
    assert body["available_miniservers"] == []
    assert body["patched_conservative_base64"] is not None


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


async def test_project_sync_missing_caption_is_auto_created(api):
    """A well-formed project with no `VirtualInCaption` section, uploaded
    for a device that is completely new (the plug from the `api` fixture
    has no matching container in `NO_VIRTUAL_IN_CAPTION_PROJECT`): the
    variant with new device containers now creates the missing section
    itself, following the user request from the review, instead of just
    locking the experimental path with a justification. The conservative
    file stays unaffected by this."""
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
    assert response.status_code == 200
    body = response.json()
    assert body["entries"]
    assert body["new_devices_unavailable_reason"] is None
    with_new_devices = base64.b64decode(body["patched_with_new_devices_base64"])
    assert b'Type="VirtualInCaption"' in with_new_devices
    conservative = base64.b64decode(body["patched_conservative_base64"])
    assert conservative.decode("utf-8-sig") == NO_VIRTUAL_IN_CAPTION_PROJECT


async def test_project_sync_distinguishes_a_group_from_a_same_numbered_device(api):
    """Regression for the missing `owner_kind` at the API boundary (final
    review, Item 1): a group id and a device id can be the same number -
    both counters start at 1 - so `PlanEntry.owner_kind` exists precisely
    to let a consumer of the plan tell a group's entries from a
    same-numbered device's. `_entries_out` used to drop it when building
    `ProjectSyncEntryOut`, and the WebUI's `projectSyncGroupedEntries`
    grouped by `device_id` alone - the normal case on a first
    installation, not an edge case, since the first device and the first
    group both get id 1.

    The `api` fixture's plug is device 1 (the only device registered).
    Grouping it into a one-member group makes that group id 1 too - the
    exact collision the fix exists for (`_category_of` accepts a group
    whose sole member fixes its own category, so this needs no second
    device). Without `owner_kind` in the response, this test cannot even
    ask the question it means to ask - there would be no field to prove
    a group entry apart from a device entry with the same `device_id`.
    """
    client, store = api
    (plug,) = store.devices()
    group = store.create_group("Kitchen group", [plug.id])
    assert group.id == plug.id == 1  # the collision this test exists to cover

    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={"file": ("projekt.Loxone", SAMPLE_PROJECT.encode("utf-8"), "application/xml")},
    )
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert entries

    same_numbered = [e for e in entries if e["device_id"] == 1]
    assert same_numbered  # sanity: the collision actually produced entries
    device_entries = [e for e in same_numbered if e["owner_kind"] == "device"]
    group_entries = [e for e in same_numbered if e["owner_kind"] == "group"]
    assert device_entries and group_entries

    # The group's entries carry the group's own label - never the
    # device's - which is exactly what a `device_id`-only grouping (the
    # WebUI's old behaviour) could not preserve once merged into one card.
    assert {e["device_label"] for e in group_entries} == {"Kitchen group"}
    assert {e["device_label"] for e in device_entries} == {plug.label}
