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

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.matter.client import CommissioningError, MatterUnavailableError
from loxmatter.matter.otbr import ThreadDatasetUnavailableError
from loxmatter.model.store import Store


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime, fake_client, fake_otbr):
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
    fake_client.store = store

    app = build_app(
        store,
        no_invoke,
        fake_runtime(store),
        client=fake_client,
        thread_dataset_source=fake_otbr,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await authenticate(store, c)
        yield c, store, device_id, fake_client
    store.close()


async def test_device_list_carries_name_and_signal_count(api):
    client, _, device_id, _ = api
    response = await client.get("/api/devices")
    assert response.status_code == 200
    devices = response.json()
    assert len(devices) == 1
    assert devices[0]["id"] == device_id
    assert "GRILLPLATS" in devices[0]["label"]
    assert devices[0]["signal_count"] == 159


async def test_device_list_reports_how_many_inputs_the_next_export_would_produce(api):
    """Follow-up fix 7 (closing review): the device tile used to show only
    `signal_count` (159) and `exportable_count` (110) - both correct, but
    neither answers how many inputs the next export would actually
    produce. `next_export_count` is the same number as
    `ExportDeviceOut.inputs` in the export preview: 5 functional signals
    plus the online signal, see
    `test_export_api.py::test_preview_reports_what_would_be_written`."""
    client, _, device_id, _ = api
    response = await client.get("/api/devices")
    devices = response.json()
    device = next(d for d in devices if d["id"] == device_id)
    assert device["next_export_count"] == 6


async def test_signal_tree_marks_what_cannot_be_exported(api):
    """Spec 6.6: values that can't be mapped are shown but not exportable."""
    client, _, device_id, _ = api
    signals = (await client.get(f"/api/devices/{device_id}/signals")).json()
    assert len(signals) == 159
    assert sum(1 for s in signals if s["exportable"]) == 110
    unexportable = next(s for s in signals if not s["exportable"])
    assert unexportable["reason"]


async def test_the_signal_payload_says_whether_a_signal_is_functional(api):
    """The UI must be able to separate the two blocks without rebuilding the
    rule a second time in JavaScript (Task 8)."""
    client, _, device_id, _ = api
    rows = (await client.get(f"/api/devices/{device_id}/signals")).json()
    onoff = next(r for r in rows if r["key"].endswith("_onoff"))
    counter = next(r for r in rows if "_c53_" in r["key"])
    assert onoff["functional"] is True
    assert counter["functional"] is False


async def test_signal_carries_its_immutable_key_and_editable_title(api):
    client, _, device_id, _ = api
    signals = (await client.get(f"/api/devices/{device_id}/signals")).json()
    signal = signals[0]
    assert signal["key"].startswith(f"d{device_id}_")
    assert "title" in signal


async def test_renaming_a_signal_leaves_its_key_alone(api):
    """Spec 6.2: the key is the wiring in Loxone."""
    client, store, device_id, _ = api
    before = {s.ref: s.key for s in store.signals(device_id)}
    key = next(iter(before.values()))
    response = await client.patch(f"/api/signals/{key}", json={"title": "Kaffeemaschine"})
    assert response.status_code == 200
    assert {s.ref: s.key for s in store.signals(device_id)} == before
    assert any(s.title == "Kaffeemaschine" for s in store.signals(device_id))


async def test_the_key_cannot_be_changed_through_the_api(api):
    client, store, device_id, _ = api
    key = store.signals(device_id)[0].key
    response = await client.patch(f"/api/signals/{key}", json={"key": "d99_9_boese"})
    assert response.status_code in (200, 422)
    assert any(s.key == key for s in store.signals(device_id))


async def test_unknown_signal_yields_404(api):
    client, _, _, _ = api
    response = await client.patch("/api/signals/d1_1_gibtsnicht", json={"title": "x"})
    assert response.status_code == 404
    assert response.json()["detail"] == "unknown signal key 'd1_1_gibtsnicht'"


async def test_unknown_signal_yields_404_in_german(api):
    """German companion test to test_unknown_signal_yields_404."""
    client, store, _, _ = api
    store.locale.set_language("de")
    response = await client.patch("/api/signals/d1_1_gibtsnicht", json={"title": "x"})
    assert response.status_code == 404
    assert response.json()["detail"] == "unbekannter Signal-Schluessel 'd1_1_gibtsnicht'"


async def test_unknown_device_yields_404(api):
    client, _, _, _ = api
    assert (await client.get("/api/devices/999/signals")).status_code == 404


async def test_exporting_a_signal_can_be_turned_off(api):
    """`SignalPatch.exported` is the counterpart to `title` - independent
    fields, settable independently (Spec 5, data model)."""
    client, store, device_id, _ = api
    key = next(s.key for s in store.signals(device_id) if s.exported)
    response = await client.patch(f"/api/signals/{key}", json={"exported": False})
    assert response.status_code == 200
    assert response.json()["exported"] is False
    assert next(s for s in store.signals(device_id) if s.key == key).exported is False


async def test_the_signal_payload_says_whether_resend_is_flagged(api):
    """Periodic resend as opt-in (design 2026-09-04) - default value off."""
    client, _, device_id, _ = api
    signals = (await client.get(f"/api/devices/{device_id}/signals")).json()
    assert signals
    assert all(s["resend"] is False for s in signals)


async def test_resend_can_be_turned_on_through_the_api(api):
    client, store, device_id, _ = api
    key = store.signals(device_id)[0].key
    response = await client.patch(f"/api/signals/{key}", json={"resend": True})
    assert response.status_code == 200
    assert response.json()["resend"] is True
    assert next(s for s in store.signals(device_id) if s.key == key).resend is True


async def test_resend_and_exported_are_independent_fields(api):
    client, store, device_id, _ = api
    key = store.signals(device_id)[0].key
    await client.patch(f"/api/signals/{key}", json={"exported": False})

    response = await client.patch(f"/api/signals/{key}", json={"resend": True})

    body = response.json()
    assert body["resend"] is True
    assert body["exported"] is False


async def test_signal_route_404s_once_its_device_has_been_removed(api):
    """Review-fix Important #4, 2026-09-02: `rename_signal` used to resolve
    exclusively via `signal_by_key`, without checking, as every
    device-bound route does, whether the associated device is still
    active. After removal, `GET /api/devices/{id}` correctly reported 404,
    but `PATCH /api/signals/{key}` kept mutating the orphaned row without
    complaint. Calls `store.forget_device` here directly instead of via
    `DELETE /api/devices/{id}` - this test holds independent of the Matter
    client."""
    client, store, device_id, _ = api
    key = store.signals(device_id)[0].key
    store.forget_device(device_id)

    response = await client.patch(f"/api/signals/{key}", json={"title": "x"})
    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"signal {key!r} belongs to device {device_id}, which was removed"
    )


async def test_signal_route_404s_once_its_device_has_been_removed_in_german(api):
    """German companion test to
    test_signal_route_404s_once_its_device_has_been_removed."""
    client, store, device_id, _ = api
    key = store.signals(device_id)[0].key
    store.forget_device(device_id)
    store.locale.set_language("de")

    response = await client.patch(f"/api/signals/{key}", json={"title": "x"})
    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"Signal {key!r} gehoert zu Geraet {device_id}, das entfernt wurde"
    )


async def test_signal_route_404s_after_the_device_is_removed_through_the_api(api):
    """Like above, but via the real `DELETE` path instead of a direct
    `store.forget_device` call - proves the fix also holds for the path a
    user actually triggers in the UI."""
    client, store, device_id, _ = api
    key = store.signals(device_id)[0].key

    remove_response = await client.delete(f"/api/devices/{device_id}")
    assert remove_response.status_code == 204

    response = await client.patch(f"/api/signals/{key}", json={"exported": False})
    assert response.status_code == 404


async def test_single_device_matches_the_list_entry(api):
    client, _, device_id, _ = api
    detail = (await client.get(f"/api/devices/{device_id}")).json()
    (listed,) = (await client.get("/api/devices")).json()
    assert detail == listed


async def test_unknown_device_detail_yields_404(api):
    client, _, _, _ = api
    assert (await client.get("/api/devices/999")).status_code == 404


async def test_renaming_a_device_changes_its_label(api):
    client, store, device_id, _ = api
    response = await client.patch(f"/api/devices/{device_id}", json={"label": "Terrasse"})
    assert response.status_code == 200
    assert response.json()["label"] == "Terrasse"
    assert store.device(device_id).label == "Terrasse"


async def test_renaming_an_unknown_device_yields_404(api):
    client, _, _, _ = api
    assert (await client.patch("/api/devices/999", json={"label": "x"})).status_code == 404


async def test_commissioning_a_device_registers_it(api):
    client, store, _, fake_client = api
    response = await client.post("/api/devices/commission", json={"code": "MT:ABC123"})
    assert response.status_code == 201
    assert fake_client.commissioned == ["MT:ABC123"]
    new_device = response.json()
    assert store.device(new_device["id"]).id == new_device["id"]
    assert len(store.devices()) == 2


async def test_a_rejected_pairing_code_yields_422(api):
    client, _, _, fake_client = api
    fake_client.fail_commission_with = CommissioningError("Commissioning failed: rejected")
    response = await client.post("/api/devices/commission", json={"code": "MT:ABC123"})
    assert response.status_code == 422


async def test_matter_server_unreachable_during_commissioning_yields_502(api):
    client, _, _, fake_client = api
    fake_client.fail_commission_with = MatterUnavailableError("matter-server unreachable")
    response = await client.post("/api/devices/commission", json={"code": "MT:ABC123"})
    assert response.status_code == 502


async def test_removing_a_device_forgets_it_and_frees_the_fabric(api):
    client, store, device_id, fake_client = api
    node_id = store.device(device_id).node_id
    response = await client.delete(f"/api/devices/{device_id}")
    assert response.status_code == 204
    assert fake_client.removed == [node_id]
    assert store.devices() == []


async def test_removing_an_unknown_device_yields_404(api):
    client, _, _, _ = api
    assert (await client.delete("/api/devices/999")).status_code == 404


async def test_a_failed_fabric_removal_leaves_the_device_listed(api):
    """Proves the order justified in `api/devices.py`: if `remove_node`
    fails, the device stays visible and removable in `Store` instead of
    silently disappearing while it still hangs around in the fabric."""
    client, store, device_id, fake_client = api
    fake_client.fail_remove_with = MatterUnavailableError("matter-server gone")
    response = await client.delete(f"/api/devices/{device_id}")
    assert response.status_code == 502
    assert [d.id for d in store.devices()] == [device_id]


async def test_commissioning_without_a_matter_client_yields_503(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))  # client defaults to None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await authenticate(store, c)
        response = await c.post("/api/devices/commission", json={"code": "MT:ABC123"})
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "No matter-server client configured — the bridge is running without a Matter connection"
    )
    store.close()


async def test_commissioning_without_a_matter_client_yields_503_in_german(
    tmp_path, no_invoke, fake_runtime
):
    """German companion test to
    test_commissioning_without_a_matter_client_yields_503."""
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store))  # client defaults to None
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await authenticate(store, c)
        store.locale.set_language("de")
        response = await c.post("/api/devices/commission", json={"code": "MT:ABC123"})
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Matter-Client nicht verfuegbar - die Bruecke laeuft ohne Verbindung zu matter-server"
    )
    store.close()


async def test_removal_without_a_matter_client_yields_503(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    app = build_app(store, no_invoke, fake_runtime(store))  # only 3 args, as in Phase 4
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await authenticate(store, c)
        response = await c.delete(f"/api/devices/{device_id}")
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "No matter-server client configured — the bridge is running without a Matter connection"
    )
    store.close()


async def test_removal_without_a_matter_client_yields_503_in_german(
    tmp_path, no_invoke, fake_runtime
):
    """German companion test to
    test_removal_without_a_matter_client_yields_503."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    app = build_app(store, no_invoke, fake_runtime(store))  # only 3 args, as in Phase 4
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await authenticate(store, c)
        store.locale.set_language("de")
        response = await c.delete(f"/api/devices/{device_id}")
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Matter-Client nicht verfuegbar - die Bruecke laeuft ohne Verbindung zu matter-server"
    )
    store.close()


# ---------------------------------------------------------------------------
# Reachability of a freshly commissioned device
#
# The recorded real-world incident from 2026-09-04: a device that had just
# been commissioned showed as "offline" in the UI and stayed that way, even
# though matter-server had cleanly interviewed it and built a subscription
# for it ("Subscription succeeded with report interval [1, 60]").
#
# The cause lies in the ordering: matter-server reports `NODE_ADDED` while
# `commission_with_code` is still running (see there
# `device_controller._setup_node`, `signal_event(EventType.NODE_ADDED, ...)`
# still before the call returns). At that point the store doesn't know the
# node yet - `store.register_device` only runs afterward - and
# `BridgeMatterClient._dispatch_loop` consequently discards the report
# ("update for unknown node ... discarded"). After that, a device sitting
# quietly on the network produces no further `NODE_ADDED`/`NODE_UPDATED`
# report, and `_device_out` reads `d<id>_online` as missing, i.e. as
# `False`. Only a restart of the bridge set the value - via
# `Runtime.seed_from_snapshot`.
#
# Commissioning must therefore seed the value itself, from exactly the
# snapshot it already has in hand anyway.
# ---------------------------------------------------------------------------


async def test_a_freshly_commissioned_device_is_online_right_away(api):
    client, _, _, _ = api
    response = await client.post("/api/devices/commission", json={"code": "MT:ABC123"})
    assert response.status_code == 201
    assert response.json()["online"] is True


async def test_the_online_state_of_a_new_device_outlives_its_own_response(api):
    """Not only in the response to commissioning itself: the value must be
    in the runtime, or the tile falls back to "offline" the next time the
    page loads - exactly the picture that was reported."""
    client, _, _, _ = api
    new_device = (await client.post("/api/devices/commission", json={"code": "MT:X"})).json()

    listed = {device["id"]: device for device in (await client.get("/api/devices")).json()}

    assert listed[new_device["id"]]["online"] is True


async def test_a_new_device_that_matter_server_cannot_reach_stays_offline(api):
    """The value is seeded, not assumed: if matter-server reports the node
    as unreachable, the tile says so too."""
    client, _, _, fake_client = api
    fake_client.available = False

    response = await client.post("/api/devices/commission", json={"code": "MT:ABC123"})

    assert response.json()["online"] is False


# ---------------------------------------------------------------------------
# Thread credentials during commissioning
#
# matter-server keeps them only in memory and forgets them on every restart
# (see `loxmatter/matter/otbr.py`). The UI's input field alone didn't catch
# this: it's optional and gets cleared after every commissioning, so it was
# empty the next time - and a Thread device failed with "Commission with
# code failed for node N", while the actual reason ("Required network
# information not provided") only showed up in matter-server's log.
# ---------------------------------------------------------------------------


async def test_a_missing_thread_dataset_is_fetched_from_the_border_router(api, fake_otbr):
    client, _, _, fake_client = api
    fake_client.thread_dataset_set = False

    response = await client.post("/api/devices/commission", json={"code": "MT:ABC123"})

    assert response.status_code == 201
    assert fake_client.datasets == [fake_otbr.dataset]
    # Order, not just occurrence: set after commissioning, the dataset
    # would be too late for this exact device. "follow" is added last
    # (Task 4): catching up on subscriptions requires the device_id that
    # has already been assigned.
    assert fake_client.order == ["dataset", "commission", "follow"]


async def test_a_dataset_from_the_request_wins_over_the_border_router(api, fake_otbr):
    """The manual path stays: whoever enters a dataset - say for a Thread
    network that doesn't come from this border router - gets theirs, not
    the host's."""
    client, _, _, fake_client = api
    fake_client.thread_dataset_set = False

    response = await client.post(
        "/api/devices/commission", json={"code": "MT:ABC123", "thread_dataset": "0e08AAAA"}
    )

    assert response.status_code == 201
    assert fake_client.datasets == ["0e08AAAA"]
    assert fake_otbr.calls == 0


async def test_a_hand_entered_dataset_that_is_no_dataset_yields_422(api):
    """The dataset fetched from the border router goes through the same
    check (`otbr.validated_dataset`); the one entered by hand used to run
    through unchecked. Pasting it in as a JSON structure or with line
    breaks triggered a `bytes.fromhex` failure at matter-server - that
    comes back as a `FailedCommand`, not as a `MatterUnavailableError`, and
    landed as a 500 "Internal Server Error" in the UI."""
    client, _, _, fake_client = api

    response = await client.post(
        "/api/devices/commission",
        json={"code": "MT:ABC123", "thread_dataset": '{"NetworkKey": "cafebabe"}'},
    )

    assert response.status_code == 422
    # And that BEFORE commissioning: a consumed pairing code would be an
    # expensive price for a typo in the input field.
    assert fake_client.datasets == []
    assert fake_client.commissioned == []


async def test_the_rejected_dataset_never_appears_in_the_message(api):
    """The dataset contains the Thread network's network key - it belongs
    in neither a log nor an error message (see `matter/otbr.py`)."""
    client, _, _, _ = api

    response = await client.post(
        "/api/devices/commission",
        json={"code": "MT:ABC123", "thread_dataset": '{"NetworkKey": "cafebabe"}'},
    )

    detail = response.json()["detail"]
    assert "cafebabe" not in detail
    assert "NetworkKey" not in detail


async def test_a_hand_entered_dataset_with_an_odd_length_yields_422(api):
    """Exactly the case the `strip()` was built for, one step further: when
    copying the output of `ot-ctl dataset active -x`, a character gets
    lost at the line end, leaving 221 instead of 222 hex characters. Each
    of those is hex, so the character-class check let it through - at
    matter-server, `bytes.fromhex` then failed, and that error comes back
    as `UnknownError`, not as `MatterUnavailableError`. The route's
    `except` didn't catch it, and the response was 500 "Internal Server
    Error" - exactly the outcome this check was meant to eliminate."""
    client, _, _, fake_client = api

    response = await client.post(
        "/api/devices/commission",
        json={"code": "MT:ABC123", "thread_dataset": "0e08AAA"},
    )

    assert response.status_code == 422
    # Also before commissioning here: the printed pairing code stays intact.
    assert fake_client.datasets == []
    assert fake_client.commissioned == []
    # The dataset is a credential and does not belong in the response.
    assert "0e08AAA" not in response.json()["detail"]


async def test_a_hand_entered_dataset_may_carry_a_trailing_newline(api):
    """A dataset copied from the terminal almost always brings a trailing
    newline along. `validated_dataset` trims it instead of rejecting it -
    matter-server gets the cleaned-up dataset."""
    client, _, _, fake_client = api

    response = await client.post(
        "/api/devices/commission", json={"code": "MT:ABC123", "thread_dataset": "0e08AAAA\n"}
    )

    assert response.status_code == 201
    assert fake_client.datasets == ["0e08AAAA"]


async def test_a_server_that_already_has_the_credentials_is_left_alone(api, fake_otbr):
    client, _, _, fake_client = api
    fake_client.thread_dataset_set = True

    response = await client.post("/api/devices/commission", json={"code": "MT:ABC123"})

    assert response.status_code == 201
    assert fake_otbr.calls == 0
    assert fake_client.datasets == []


async def test_commissioning_goes_ahead_when_no_border_router_answers(api, fake_otbr):
    """A WiFi device doesn't need a Thread dataset at all. A missing border
    router must therefore not abort commissioning - it's a hint, not an
    error."""
    client, _, _, _ = api
    fake_otbr.fail_with = ThreadDatasetUnavailableError("no border router reachable")

    response = await client.post("/api/devices/commission", json={"code": "MT:ABC123"})

    assert response.status_code == 201


async def test_a_failure_without_thread_credentials_names_the_likely_cause(api, fake_otbr):
    """The core of the reported problem: the UI showed only "Commission with
    code failed for node 7" - without the one sentence saying why."""
    client, _, _, fake_client = api
    fake_client.thread_dataset_set = False
    fake_otbr.fail_with = ThreadDatasetUnavailableError("no border router reachable")
    fake_client.fail_commission_with = CommissioningError(
        "Commissioning failed: Commission with code failed for node 7."
    )

    response = await client.post("/api/devices/commission", json={"code": "MT:ABC123"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Commission with code failed for node 7." in detail
    assert "Thread" in detail
    assert "no border router reachable" in detail


async def test_commissioning_follows_the_new_node(api):
    """Without this call, the freshly commissioned device would have no
    attribute subscription at all: `subscribe()` ran once when the bridge
    started, and the `NODE_ADDED` for this device demonstrably arrived
    before the store could give it a device_id."""
    client, store, _, fake_client = api

    new_device = (await client.post("/api/devices/commission", json={"code": "MT:X"})).json()

    assert fake_client.followed == [store.device(new_device["id"]).node_id]


async def test_the_new_node_is_followed_only_after_it_is_registered(api):
    """The ordering is the whole reason for this call: if the route caught
    up earlier, `resolve_device_id` would run into nothing again - exactly
    the race that `NODE_ADDED` already lost. The mere ordering "commission
    before follow" doesn't prove that - only the assurance that the store
    could already resolve the node ID to the new `device_id` at the time
    of the `follow_node` call shows that the route actually catches up
    only AFTER registration."""
    client, _, _, fake_client = api
    # The Thread dataset isn't the subject of this test (see
    # test_a_missing_thread_dataset_is_fetched_from_the_border_router for
    # that) - without this line, "dataset" would additionally sit at the
    # front of the list.
    fake_client.thread_dataset_set = True

    response = await client.post("/api/devices/commission", json={"code": "MT:X"})

    assert fake_client.order == ["commission", "follow"]
    new_device_id = response.json()["id"]
    assert fake_client.followed_resolved == [new_device_id]


async def test_the_route_forces_the_seeding_of_the_new_node(api):
    """The route catches up AFTER the dispatch loop has already done the
    same thing: matter-server reports `NODE_ADDED` while
    `commission_with_code` is still running, and the dispatch task
    subscribes to every path of the new node in the process. The route's
    catch-up therefore finds an empty diff - without
    `seed_even_without_new_paths` it ends before the handler, and the
    device's starting values would never be seeded (see
    `BridgeMatterClient.follow_node` and
    `test_the_commissioning_route_still_seeds_after_the_dispatch_loop_was_first`
    in tests/matter/test_client.py)."""
    client, _, _, fake_client = api

    await client.post("/api/devices/commission", json={"code": "MT:X"})

    assert fake_client.followed_forced == [True]


# ---------------------------------------------------------------------------
# The follow-up of commissioning must not retroactively cancel the process
#
# `set_online` and `follow_node` run AFTER `register_device` - so from the
# point at which the device is in the fabric AND in the store. A failure
# there is not a failed commissioning, but an incomplete follow-up on a
# completed process.
#
# The scenario is exactly what this branch is built around: matter-server
# restarts right after commissioning, `follow_node` fails with
# `MatterUnavailableError`, and the route answered with 500. The UI showed
# "Commissioning failed", the device tile didn't appear - but the device
# WAS commissioned. Anyone who then presses "commission" again fails on a
# consumed pairing code (422) and looks for the mistake in themselves.
# Second path there: `Runtime.set_online` -> `UdpSender.send` ->
# `socket.sendto` raises `OSError` when the Miniserver network is briefly
# down.
# ---------------------------------------------------------------------------


async def test_a_failing_follow_up_still_reports_the_device_as_commissioned(api):
    client, store, _, fake_client = api
    fake_client.fail_follow_with = MatterUnavailableError("matter-server unreachable")

    response = await client.post("/api/devices/commission", json={"code": "MT:X"})

    assert response.status_code == 201
    assert store.device(response.json()["id"]).node_id == 100


async def test_a_failing_online_seed_still_reports_the_device_as_commissioned(
    tmp_path, no_invoke, fake_runtime, fake_client, fake_otbr
):
    """A dedicated setup instead of the `api` fixture: the failure must be
    set on the `FakeRuntime`, and the fixture doesn't hand that out."""
    store = Store(tmp_path / "t.sqlite")
    fake_client.store = store
    runtime = fake_runtime(store)
    runtime.fail_set_online_with = OSError("Network is unreachable")
    app = build_app(store, no_invoke, runtime, client=fake_client, thread_dataset_source=fake_otbr)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await authenticate(store, c)
        response = await c.post("/api/devices/commission", json={"code": "MT:X"})

    assert response.status_code == 201
    assert store.device(response.json()["id"]).node_id == 100
    # And the follow-up keeps going despite the failure: catching up on
    # subscriptions doesn't depend on the reachability seed succeeding.
    assert fake_client.followed == [100]
    store.close()


async def test_the_device_list_carries_room_and_category(api):
    client, _store, device_id, _fake = api
    devices = (await client.get("/api/devices")).json()
    device = next(d for d in devices if d["id"] == device_id)
    assert device["room"] is None
    assert device["category"] == "socket"
    assert device["category_rank"] == 1


async def test_patching_only_the_room_leaves_the_label_alone(api):
    client, store, device_id, _fake = api
    before = store.device(device_id).label
    response = await client.patch(f"/api/devices/{device_id}", json={"room": "  Küche  "})
    assert response.status_code == 200
    assert response.json()["room"] == "Küche"
    assert store.device(device_id).label == before


async def test_patching_only_the_label_leaves_the_room_alone(api):
    client, store, device_id, _fake = api
    store.set_room(device_id, "Bad")
    response = await client.patch(f"/api/devices/{device_id}", json={"label": "Steckdose"})
    assert response.status_code == 200
    assert response.json()["room"] == "Bad"
    assert response.json()["label"] == "Steckdose"


async def test_an_empty_room_string_clears_the_room(api):
    """`""` means "remove room", `null`/omitted means "unchanged" - the same
    principle as `SignalPatch`."""
    client, store, device_id, _fake = api
    store.set_room(device_id, "Bad")
    response = await client.patch(f"/api/devices/{device_id}", json={"room": ""})
    assert response.status_code == 200
    assert response.json()["room"] is None


async def test_patching_the_room_does_not_make_the_device_pending(api):
    """The room doesn't land in any export template - a freshly exported
    device must not become pending again through a room assignment
    (design 3.3).

    The export up front is needed so the starting state is unambiguous: a
    device that was never exported always counts as pending, and this
    test's point wouldn't be visible there.

    The counter-check - a rename MUST mark the device as pending - already
    exists in `tests/api/test_export_api.py` (the test around line 280,
    "rename … must be reported by `GET /api/export/status`") and is not
    written a second time here. It's the reason this test can't turn green
    just because `updated_at` accidentally stops being set at all.

    `GET /api/export/status` answers with a LIST, not an object
    (`-> list[ExportStatusOut]`, `api/export.py:362`)."""
    client, store, device_id, _fake = api
    store.mark_exported(device_id)

    status = (await client.get("/api/export/status")).json()
    entry = next(e for e in status if e["device_id"] == device_id)
    assert entry["changed_since_export"] is False

    await client.patch(f"/api/devices/{device_id}", json={"room": "Flur"})

    status = (await client.get("/api/export/status")).json()
    entry = next(e for e in status if e["device_id"] == device_id)
    assert entry["changed_since_export"] is False


async def test_commissioning_accepts_a_room(api):
    client, _store, _device_id, fake_client = api
    fake_client.snapshot_to_return = load_snapshot("ikea_bilresa_button.json")
    response = await client.post(
        "/api/devices/commission", json={"code": "1234-567-8901", "room": "Küche"}
    )
    assert response.status_code == 201
    assert response.json()["room"] == "Küche"
    assert response.json()["category"] == "switch"


async def test_recommissioning_a_known_device_applies_the_chosen_room(api):
    """Review finding, Finding 4: `register_device` returns early for a
    device that's already active, BEFORE the INSERT - the `room` argument
    gets dropped in the process (see its docstring). The UI's
    commissioning tile now offers a room field; without the fix here, a
    person who recommissions an already-known device with a chosen room
    would get a 201 and no hint at all that their choice was ignored.

    `fake_client.snapshot_to_return` deliberately returns the same
    `unique_id` as the device the `api` fixture already registered without
    a room (`ikea_grillplats_plug.json`) - that's the case
    "recommissioning a known device", not "new device"."""
    client, store, device_id, fake_client = api
    assert store.device(device_id).room is None
    fake_client.snapshot_to_return = load_snapshot("ikea_grillplats_plug.json")

    response = await client.post(
        "/api/devices/commission", json={"code": "MT:ABC123", "room": "Küche"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == device_id
    assert body["room"] == "Küche"
    assert store.device(device_id).room == "Küche"
    # No second device created - `register_device` took the early return
    # path, `set_room` added the room afterward.
    assert len(store.devices()) == 1


async def test_recommissioning_a_known_device_uses_set_room_not_rename_device(api, monkeypatch):
    """The counter-check to the fix from Finding 4, at the store level
    instead of via `GET /api/export/status`: `register_signals` marks a
    device as "changed since" on EVERY recommissioning anyway -
    deliberately, with its own justification in the docstring there ("a
    plain refresh of an already-known device"), independent of any room
    choice. An end-to-end test via `GET /api/export/status` therefore
    couldn't tell the two causes apart and would always show "changed",
    regardless of whether the added room touches `updated_at` or not.

    This test checks the actual guarantee directly: the route in
    `api/devices.py` may only call `Store.set_room` to add the room
    choice afterward, never `Store.rename_device` - `rename_device` would
    set `updated_at`, and the room doesn't land in any export template
    (design 3.3). `rename_device` is replaced here with a trap that would
    make the test fail if it were actually called."""
    client, store, device_id, fake_client = api
    fake_client.snapshot_to_return = load_snapshot("ikea_grillplats_plug.json")

    def _rename_device_is_the_wrong_call(*_args, **_kwargs):
        raise AssertionError("rename_device must not be called when adding a room choice afterward")

    monkeypatch.setattr(store, "rename_device", _rename_device_is_the_wrong_call)

    response = await client.post(
        "/api/devices/commission", json={"code": "MT:ABC123", "room": "Küche"}
    )

    assert response.status_code == 201
    assert store.device(device_id).room == "Küche"
