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

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.matter.client import CommissioningError, MatterUnavailableError
from loxmatter.model.store import Store


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime, fake_client):
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(store, no_invoke, fake_runtime(store), client=fake_client)
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
    """Nachbesserung Fix 7 (Abschlussreview): die Gerätekachel zeigte bisher
    nur `signal_count` (159) und `exportable_count` (110) - beide korrekt,
    aber keine davon beantwortet, wie viele Eingänge der nächste Export
    tatsächlich erzeugt. `next_export_count` ist dieselbe Zahl wie
    `ExportDeviceOut.inputs` in der Exportvorschau: 5 funktionale Signale
    plus das Online-Signal, siehe
    `test_export_api.py::test_preview_reports_what_would_be_written`."""
    client, _, device_id, _ = api
    response = await client.get("/api/devices")
    devices = response.json()
    device = next(d for d in devices if d["id"] == device_id)
    assert device["next_export_count"] == 6


async def test_signal_tree_marks_what_cannot_be_exported(api):
    """Spec 6.6: nicht abbildbare Werte werden angezeigt, aber nicht exportierbar."""
    client, _, device_id, _ = api
    signals = (await client.get(f"/api/devices/{device_id}/signals")).json()
    assert len(signals) == 159
    assert sum(1 for s in signals if s["exportable"]) == 110
    unexportable = next(s for s in signals if not s["exportable"])
    assert unexportable["reason"]


async def test_the_signal_payload_says_whether_a_signal_is_functional(api):
    """Die Oberflaeche muss die beiden Bloecke trennen koennen, ohne die
    Regel ein zweites Mal in JavaScript nachzubauen (Aufgabe 8)."""
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
    """Spec 6.2: der Schluessel ist die Verdrahtung in Loxone."""
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
    """Deutscher Begleittest zu test_unknown_signal_yields_404."""
    client, store, _, _ = api
    store.locale.set_language("de")
    response = await client.patch("/api/signals/d1_1_gibtsnicht", json={"title": "x"})
    assert response.status_code == 404
    assert response.json()["detail"] == "unbekannter Signal-Schluessel 'd1_1_gibtsnicht'"


async def test_unknown_device_yields_404(api):
    client, _, _, _ = api
    assert (await client.get("/api/devices/999/signals")).status_code == 404


async def test_exporting_a_signal_can_be_turned_off(api):
    """`SignalPatch.exported` ist das Gegenstueck zu `title` - unabhaengige
    Felder, unabhaengig setzbar (Spec 5, Datenmodell)."""
    client, store, device_id, _ = api
    key = next(s.key for s in store.signals(device_id) if s.exported)
    response = await client.patch(f"/api/signals/{key}", json={"exported": False})
    assert response.status_code == 200
    assert response.json()["exported"] is False
    assert next(s for s in store.signals(device_id) if s.key == key).exported is False


async def test_the_signal_payload_says_whether_resend_is_flagged(api):
    """Periodischer Resend als Opt-in (Entwurf 2026-09-04) - Vorgabewert aus."""
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
    """Review-Fix Important #4, 2026-09-02: `rename_signal` loeste bisher
    ausschliesslich ueber `signal_by_key` auf, ohne wie jede geraete-gebundene
    Route zu pruefen, ob das zugehoerige Geraet noch aktiv ist. Nach dem
    Entfernen meldete `GET /api/devices/{id}` korrekt 404, aber `PATCH
    /api/signals/{key}` mutierte die verwaiste Zeile weiterhin klaglos. Ruft
    `store.forget_device` hier direkt statt ueber `DELETE
    /api/devices/{id}` - dieser Test gilt unabhaengig vom Matter-Client."""
    client, store, device_id, _ = api
    key = store.signals(device_id)[0].key
    store.forget_device(device_id)

    response = await client.patch(f"/api/signals/{key}", json={"title": "x"})
    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"signal {key!r} belongs to device {device_id}, which was removed"
    )


async def test_signal_route_404s_once_its_device_has_been_removed_in_german(api):
    """Deutscher Begleittest zu test_signal_route_404s_once_its_device_has_been_removed."""
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
    """Wie oben, aber ueber den echten `DELETE`-Pfad statt eines direkten
    `store.forget_device`-Aufrufs - belegt, dass der Fix auch fuer den Weg
    greift, den eine Nutzerin tatsaechlich in der Oberflaeche ausloest."""
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
    fake_client.fail_commission_with = CommissioningError("Einlernen fehlgeschlagen: abgelehnt")
    response = await client.post("/api/devices/commission", json={"code": "MT:ABC123"})
    assert response.status_code == 422


async def test_matter_server_unreachable_during_commissioning_yields_502(api):
    client, _, _, fake_client = api
    fake_client.fail_commission_with = MatterUnavailableError("matter-server nicht erreichbar")
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
    """Belegt die in `api/devices.py` begruendete Reihenfolge: scheitert
    `remove_node`, bleibt das Geraet in `Store` sichtbar und entfernbar,
    statt lautlos zu verschwinden, waehrend es in der Fabric noch haengt."""
    client, store, device_id, fake_client = api
    fake_client.fail_remove_with = MatterUnavailableError("matter-server weg")
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
    """Deutscher Begleittest zu test_commissioning_without_a_matter_client_yields_503."""
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

    app = build_app(store, no_invoke, fake_runtime(store))  # nur 3 Argumente, wie in Phase 4
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
    """Deutscher Begleittest zu test_removal_without_a_matter_client_yields_503."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)

    app = build_app(store, no_invoke, fake_runtime(store))  # nur 3 Argumente, wie in Phase 4
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
