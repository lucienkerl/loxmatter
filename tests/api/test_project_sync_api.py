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
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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
# `patch.apply_plan`). `apply_plan` creates this section itself (design
# section 8: the special case of first-time creation) - no manual prep in
# Loxone Config needed any more.
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


async def test_project_sync_returns_plan_and_the_patched_file(api):
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
    patched = base64.b64decode(body["patched_base64"])
    # A new device container, without any option to ask for one - the
    # upload's only file has it (design section 3.4, since 2026-09-11).
    assert b"VirtualUdpIn" not in SAMPLE_PROJECT.encode("utf-8")
    assert b"VirtualUdpIn" in patched


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
    assert body["patched_base64"] is None


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
    assert body["patched_base64"] is not None


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
    patched file creates the missing section itself, following the user
    request from the review."""
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
    patched = base64.b64decode(body["patched_base64"])
    assert b'Type="VirtualInCaption"' in patched


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


async def test_project_sync_stamps_the_file_in_the_users_offset(api):
    """`utc_offset` reaches the patched file's `Date`: two uploads a few
    hours apart in offset differ in `Date` by those hours, and not at all
    in `DateS`, which is UTC (see `projectsync/savedate.py`)."""
    client, _store = api
    project = SAMPLE_PROJECT.replace(
        'Title="Testprojekt">',
        'Title="Testprojekt" Date="2026-09-23 23:13:53" DateS="559430033">',
        1,
    ).encode("utf-8")

    async def stamp(offset: int) -> tuple[datetime, int]:
        response = await client.post(
            "/api/export/project-sync",
            params={"bridge_ip": "10.0.0.5", "utc_offset": offset},
            files={"file": ("projekt.Loxone", project, "application/xml")},
        )
        assert response.status_code == 200
        patched = base64.b64decode(response.json()["patched_base64"]).decode("utf-8")
        date = re.search(r'Date="([^"]*)"', patched)
        date_s = re.search(r'DateS="([^"]*)"', patched)
        assert date is not None and date_s is not None
        # `Date` carries no zone; UTC only makes the two comparable.
        wall_clock = datetime.strptime(date.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        return wall_clock, int(date_s.group(1))

    east, east_s = await stamp(120)
    west, west_s = await stamp(-300)
    assert east_s > 559430033
    # Seconds apart at most, since the two uploads ran one after the other.
    assert abs(east_s - west_s) < 60
    assert abs((east - west) - timedelta(hours=7)) < timedelta(seconds=60)


async def test_project_sync_rejects_an_impossible_offset(api):
    client, _store = api
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5", "utc_offset": 15 * 60},
        files={"file": ("projekt.Loxone", SAMPLE_PROJECT.encode("utf-8"), "application/xml")},
    )
    assert response.status_code == 422


def _store_miniserver(store, ip):
    store.settings.save(bridge_ip="10.0.0.5", udp_port=7000, listen_port=8080, miniserver_ip=ip)


async def test_the_stored_miniserver_resolves_a_file_with_several(api):
    """Spec section 9: no selection field when the address in Settings
    already says which Miniserver this bridge talks to."""
    client, store = api
    _store_miniserver(store, "10.0.0.20")
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={
            "file": ("mehrere_ms.Loxone", TWO_LOXLIVE_PROJECT.encode("utf-8"), "application/xml")
        },
    )
    body = response.json()
    assert body["needs_miniserver_selection"] is False
    assert body["patched_base64"] is not None


async def test_a_stored_miniserver_not_in_the_file_still_asks(api):
    client, store = api
    _store_miniserver(store, "10.0.0.99")
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={
            "file": ("mehrere_ms.Loxone", TWO_LOXLIVE_PROJECT.encode("utf-8"), "application/xml")
        },
    )
    assert response.json()["needs_miniserver_selection"] is True


async def test_a_single_miniserver_file_ignores_a_stored_address_that_differs(api):
    """`build_index` rejects a `miniserver_ip` that does not match a single
    block, so the stored address may only be offered where the choice is
    open."""
    client, store = api
    _store_miniserver(store, "10.0.0.99")
    response = await client.post(
        "/api/export/project-sync",
        params={"bridge_ip": "10.0.0.5"},
        files={"file": ("p.Loxone", SAMPLE_PROJECT.encode("utf-8"), "application/xml")},
    )
    assert response.status_code == 200
    assert response.json()["patched_base64"] is not None
