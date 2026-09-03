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

"""Tests fuer die Diagnose-API (Task 6, Phase 5) - siehe api/diagnostics.py.

Drei lokale Fixtures, dem Muster der uebrigen Dateien unter tests/api/
folgend (jede Testdatei baut ihre eigene `api`-Fixture passend zu ihrem
Bedarf, siehe conftest.py-Moduldocstring):

`api` - Grundausstattung mit echtem `fake_client` (verbunden) UND einem
echten `matter_data_dir` (fuer die Sicherung), aber OHNE echten `UdpSender`
(die meisten Tests hier brauchen keinen Mitschnitt).

`api_with_sender` - zusaetzlich ein echter `UdpSender`, der an einen lokalen
UDP-Socket sendet (`receiver`, wie in tests/loxone/test_sender.py) - fuer
die beiden Mitschnitt-Tests. Der Mitschnitt haengt in `UdpSender` selbst
(siehe Moduldocstring von sender.py), ein Fake-Sender wuerde ihn deshalb gar
nicht ausloesen.

`api_without_matter` - `client=None`, wie in server.py dokumentiert bedeutet
das "die Bruecke laeuft ohne Matter-Verbindung" - fuer den Test, dass eine
rote Zeile im Systemcheck einen brauchbaren Hinweis traegt.

`api_with_token` - wie `api`, aber mit gesetztem API-Token. Seit Review-Fix
Fix 3 (2026-09-03) liefert `GET /api/diagnostics/fabric-backup` OHNE
gesetztes Token gar nichts mehr aus (403, siehe api/diagnostics.py) - jeder
Test, der die Sicherung selbst betrachtet, braucht deshalb einen Dienst mit
Token und schickt den passenden Header mit. Die uebrigen Fixtures bleiben
absichtlich ohne Token: alle anderen Diagnose-Routen sind davon unberuehrt,
und das soll hier weiterhin so gepruft werden, wie ein Betrieb ohne Token
sie tatsaechlich sieht.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import httpx2 as httpx
import pytest
from conftest import load_snapshot

from loxmatter.api.diagnostics import RingBuffer
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.sender import UdpSender
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


def _matter_data_dir(tmp_path: Path) -> Path:
    """Ein Verzeichnis mit einer harmlosen Testdatei - steht fuer das
    matter-server-Datenverzeichnis, ohne echtes Schluesselmaterial zu
    beruehren (siehe Task-Brief: tests/fixtures/VirtualIn|VirtualOut sind
    tabu, aber die haben mit dieser Datei nichts zu tun)."""
    directory = tmp_path / "matter-data"
    directory.mkdir()
    (directory / "credentials.json").write_text('{"fixture": "keine echten Schluessel"}')
    return directory


_BACKUP_TOKEN = "test-token"
_BACKUP_HEADERS = {"Authorization": f"Bearer {_BACKUP_TOKEN}"}


@pytest.fixture
def receiver() -> Iterator[socket.socket]:
    """Wie in tests/loxone/test_sender.py - ein UDP-Socket auf 127.0.0.1,
    der die Maschine nicht verlaesst."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    yield sock
    sock.close()


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime, fake_client):
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(
        store,
        no_invoke,
        fake_runtime(store),
        client=fake_client,
        matter_data_dir=_matter_data_dir(tmp_path),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store, device_id
    store.close()


@pytest.fixture
async def api_with_sender(tmp_path, no_invoke, fake_runtime, fake_client, receiver):
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    host, port = receiver.getsockname()
    sender = UdpSender(host, port)
    app = build_app(
        store,
        no_invoke,
        fake_runtime(store),
        client=fake_client,
        sender=sender,
        matter_data_dir=_matter_data_dir(tmp_path),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, sender, device_id
    await sender.close()
    store.close()


@pytest.fixture
async def api_with_token(tmp_path, no_invoke, fake_runtime, fake_client):
    """Wie `api`, aber mit gesetztem `api_token` - siehe Moduldocstring."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(
        store,
        no_invoke,
        fake_runtime(store),
        client=fake_client,
        matter_data_dir=_matter_data_dir(tmp_path),
        api_token=_BACKUP_TOKEN,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store, device_id
    store.close()


@pytest.fixture
async def api_without_matter(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)

    app = build_app(store, no_invoke, fake_runtime(store), client=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store, device_id
    store.close()


def test_ring_buffer_drops_the_oldest():
    buffer = RingBuffer(maxlen=3)
    for i in range(5):
        buffer.append(i)
    assert list(buffer) == [2, 3, 4]


def test_ring_buffer_of_a_long_running_bridge_stays_bounded():
    """Eine Bruecke laeuft monatelang - der Mitschnitt darf nicht mitwachsen."""
    buffer = RingBuffer(maxlen=100)
    for i in range(1_000_000):
        buffer.append(i)
    assert len(list(buffer)) == 100


async def test_datagram_log_shows_what_was_sent(api_with_sender):
    client, sender, device_id = api_with_sender
    await sender.send(f"d{device_id}_2_voltage", 230.0)
    entries = (await client.get("/api/diagnostics/datagrams")).json()
    assert entries[-1]["key"] == f"d{device_id}_2_voltage"
    assert entries[-1]["value"] == "230"
    assert entries[-1]["timestamp"]


async def test_datagram_log_filters_by_device(api_with_sender):
    client, sender, device_id = api_with_sender
    await sender.send(f"d{device_id}_2_voltage", 230.0)
    await sender.send("bridge_alive", True)
    entries = (await client.get(f"/api/diagnostics/datagrams?device_id={device_id}")).json()
    assert all(e["key"].startswith(f"d{device_id}_") for e in entries)


async def test_command_log_records_the_result(api):
    client, _, device_id = api
    await client.get(f"/cmd/d{device_id}_1_on/1")
    await client.get("/cmd/d1_1_gibtsnicht/1")
    entries = (await client.get("/api/diagnostics/commands")).json()
    assert entries[-2]["status"] == 200
    assert entries[-1]["status"] == 404


async def test_system_check_reports_each_line_with_a_verdict(api):
    client, _, _ = api
    checks = (await client.get("/api/diagnostics/system")).json()
    names = {c["name"] for c in checks}
    assert {"matter-server", "store", "ipv6"} <= names
    for check in checks:
        assert check["ok"] in (True, False)
        assert check["detail"]


async def test_fabric_backup_is_a_real_archive(api_with_token):
    """Spec 4.1: das einzige unersetzliche Datum des Systems."""
    client, _, _ = api_with_token
    response = await client.get("/api/diagnostics/fabric-backup", headers=_BACKUP_HEADERS)
    assert response.status_code == 200
    assert response.headers["content-type"] in ("application/zip", "application/gzip")
    assert len(response.content) > 0


async def test_fabric_backup_is_503_without_a_configured_directory(
    no_invoke, fake_runtime, fake_client, tmp_path
):
    """Der erste der beiden 503-Zweige (Task-6-Review, Punkt 3):
    `matter_data_dir is None` - der Dienst laeuft ohne `--matter-data-dir`,
    z. B. weil die Bereitstellung diese Option (noch) nicht setzt (siehe
    deploy/testhost/docker-compose.yml, dort bewusst auskommentiert, bis
    Task 8 den Token-Schutz liefert)."""
    store = Store(tmp_path / "t.sqlite")
    app = build_app(
        store, no_invoke, fake_runtime(store), client=fake_client, api_token=_BACKUP_TOKEN
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/diagnostics/fabric-backup", headers=_BACKUP_HEADERS)
    store.close()

    assert response.status_code == 503
    assert "matter-data-dir" in response.json()["detail"]


async def test_fabric_backup_is_503_when_the_configured_directory_is_missing(
    no_invoke, fake_runtime, fake_client, tmp_path
):
    """Der zweite 503-Zweig: `matter_data_dir` ist gesetzt, aber der Pfad
    existiert (mehr) nicht - z. B. eine Einhaengung, die zwischenzeitlich
    ausgehaengt wurde."""
    store = Store(tmp_path / "t.sqlite")
    missing = tmp_path / "existiert-nicht"
    app = build_app(
        store,
        no_invoke,
        fake_runtime(store),
        client=fake_client,
        matter_data_dir=missing,
        api_token=_BACKUP_TOKEN,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/diagnostics/fabric-backup", headers=_BACKUP_HEADERS)
    store.close()

    assert response.status_code == 503


async def test_fabric_backup_is_503_when_the_configured_path_is_a_file(
    no_invoke, fake_runtime, fake_client, tmp_path
):
    """Derselbe Zweig wie oben (`not matter_data_dir.is_dir()`), aber ueber
    den bislang unbetrachteten dritten Fall: der Pfad existiert durchaus,
    ist aber kein Verzeichnis, sondern eine gewoehnliche Datei."""
    store = Store(tmp_path / "t.sqlite")
    not_a_directory = tmp_path / "matter-data-ist-eine-datei"
    not_a_directory.write_text("keine Fabric-Sicherung, nur eine gewoehnliche Datei")
    app = build_app(
        store,
        no_invoke,
        fake_runtime(store),
        client=fake_client,
        matter_data_dir=not_a_directory,
        api_token=_BACKUP_TOKEN,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/diagnostics/fabric-backup", headers=_BACKUP_HEADERS)
    store.close()

    assert response.status_code == 503


async def test_a_failing_check_says_what_to_do(api_without_matter):
    """Ein roter Punkt ohne Hinweis hilft niemandem."""
    client, _, _ = api_without_matter
    checks = (await client.get("/api/diagnostics/system")).json()
    failing = next(c for c in checks if not c["ok"])
    assert len(failing["detail"]) > 20


async def test_command_log_does_not_record_diagnostics_polling(api):
    """Signal/Rausch-Entscheidung (siehe Moduldocstring von diagnostics.py):
    ein Client, der die Diagnoseseite offen laesst und alle paar Sekunden
    pollt, soll den knappen Ringpuffer nicht mit sich selbst fluten."""
    client, _, _ = api
    for _ in range(5):
        await client.get("/api/diagnostics/commands")
        await client.get("/api/diagnostics/system")
    entries = (await client.get("/api/diagnostics/commands")).json()
    assert all(not e["path"].startswith("/api/diagnostics") for e in entries)


async def test_command_log_never_carries_a_query_string(api):
    """Ausblick auf Task 8 (Token-Schutz fuer die Sicherung): wird das Token
    irgendwo als Query-Parameter gefuehrt, darf es nicht im fuer jeden
    Diagnose-Betrachter sichtbaren Kommando-Log landen. Query-Strings werden
    deshalb grundsaetzlich nie mitgeschnitten, unabhaengig davon, welche
    Route sie traegt."""
    client, _, device_id = api
    await client.get(f"/cmd/d{device_id}_1_on/1?secret=should-not-be-logged")
    entries = (await client.get("/api/diagnostics/commands")).json()
    assert all("?" not in e["path"] and "secret" not in e["path"] for e in entries)


async def test_a_check_that_raises_unexpectedly_fails_gracefully(api, monkeypatch):
    """Punkt 3 des Auftrags: ein Check, der selbst wirft (nicht nur eine der
    erwarteten Fehlerarten, sondern ein echter Bug), darf den ganzen
    Endpunkt nicht mit 500 abschiessen - er wird zu genau einer roten Zeile."""
    client, store, _ = api

    def _broken_check_writable() -> None:
        raise RuntimeError("Simulierter Programmfehler in der Pruefung selbst")

    monkeypatch.setattr(store, "check_writable", _broken_check_writable)

    response = await client.get("/api/diagnostics/system")
    assert response.status_code == 200
    checks = response.json()
    store_check = next(c for c in checks if c["name"] == "store")
    assert store_check["ok"] is False
    assert len(store_check["detail"]) > 20
