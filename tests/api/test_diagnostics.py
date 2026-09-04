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

`api_with_token` - wie `api`, aber mit gesetztem API-Token statt einer
angemeldeten Sitzung. `GET /api/diagnostics/fabric-backup` verlangt - wie
jede `/api`-Route - einen der beiden Nachweise (Task 8, Phase 5, Spec 9);
die Tests, die die Sicherung selbst betrachten, weisen sich hier ueber den
Token-Header statt ueber eine Anmeldung aus, um beide Wege abzudecken. Die
uebrigen Fixtures bleiben absichtlich ohne Token: alle anderen
Diagnose-Routen sind davon unberuehrt, und das soll hier weiterhin so
gepruft werden, wie ein Betrieb ohne Token sie tatsaechlich sieht.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter.api import diagnostics
from loxmatter.api.diagnostics import RingBuffer, _check_thread_credentials
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.sender import UdpSender
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


class _ClientWithThreadDataset:
    """Steht fuer `BridgeMatterClient` - nur die zwei Eigenschaften, die
    `_check_thread_credentials` liest."""

    def __init__(self, thread_dataset_set: bool, connected: bool = True) -> None:
        self.thread_dataset_set = thread_dataset_set
        self.connected = connected


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
        await authenticate(store, client)
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
        await authenticate(store, client)
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
        await authenticate(store, client)
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
    assert {"matter-server", "store", "ipv6", "thread"} <= names
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


# ---------------------------------------------------------------------------
# IPv6- und Thread-Pruefung (2026-09-03)
# ---------------------------------------------------------------------------

# Auszug aus einem echten `/proc/net/if_inet6` des Testhosts. Spalten:
# Adresse (hex, ohne Doppelpunkte), Interface-Index, Praefixlaenge, Scope,
# Flags, Name.
_IF_INET6_WITH_THREAD = """\
fe80000000000000da3addfffe99419e 03 40 20 80 wlan0
00000000000000000000000000000001 01 80 10 80 lo
fd2745d78c7800010e26ce8e4edd7c50 07 40 00 00 wpan0
fd7df0629267d2e0000000fffe00fc10 07 40 00 00 wpan0
"""

# Derselbe Host, nachdem der OTBR-Agent an einem RCP-Timeout gestorben ist:
# `wpan0` ist verschwunden, uebrig bleiben link-lokal und Loopback.
_IF_INET6_WITHOUT_THREAD = """\
fe80000000000000da3addfffe99419e 03 40 20 80 wlan0
00000000000000000000000000000001 01 80 10 80 lo
"""


def _with_if_inet6(monkeypatch, tmp_path, content: str | None) -> None:
    """Legt `_IF_INET6` auf eine Datei mit diesem Inhalt - oder auf einen
    Pfad, den es nicht gibt, wenn `content is None` (Nicht-Linux)."""
    path = tmp_path / "if_inet6"
    if content is not None:
        path.write_text(content, encoding="ascii")
    monkeypatch.setattr(diagnostics, "_IF_INET6", path)


def test_ipv6_accepts_a_unique_local_address(monkeypatch, tmp_path):
    """Der Fehler, den dieser Check frueher hatte: er verlangte eine Route zu
    einer GLOBALEN Adresse und meldete auf einem gesunden Thread-Aufbau rot.
    Thread laeuft ueber Unique-Local-Adressen, und die meisten Heimnetze
    haben ueberhaupt kein globales IPv6."""
    _with_if_inet6(monkeypatch, tmp_path, _IF_INET6_WITH_THREAD)
    ok, detail = diagnostics._check_ipv6()
    assert ok is True
    assert "fd27" in detail


def test_ipv6_fails_when_only_link_local_and_loopback_remain(monkeypatch, tmp_path):
    _with_if_inet6(monkeypatch, tmp_path, _IF_INET6_WITHOUT_THREAD)
    ok, detail = diagnostics._check_ipv6()
    assert ok is False
    assert "link-lokale" in detail


def test_thread_check_finds_the_mesh_interface(monkeypatch, tmp_path):
    _with_if_inet6(monkeypatch, tmp_path, _IF_INET6_WITH_THREAD)
    ok, detail = diagnostics._check_thread()
    assert ok is True
    assert "wpan0" in detail


def test_thread_check_fails_when_the_interface_is_gone(monkeypatch, tmp_path):
    """Der echte Ausfall vom 2026-09-03: das Funkmodul antwortete nicht mehr,
    der OTBR-Agent brach mit einem RCP-Timeout ab, `wpan0` verschwand - und
    der Container lief weiter, sodass `restart: unless-stopped` nicht griff.
    Sechseinhalb Stunden lang war kein Geraet erreichbar. Dieser Check haette
    es gezeigt."""
    _with_if_inet6(monkeypatch, tmp_path, _IF_INET6_WITHOUT_THREAD)
    ok, detail = diagnostics._check_thread()
    assert ok is False
    assert "OTBR" in detail
    assert "restart" in detail


def test_both_checks_stay_quiet_where_they_cannot_look(monkeypatch, tmp_path):
    """Auf einem Nicht-Linux-System gibt es /proc/net/if_inet6 nicht. Das ist
    kein Fehler des Aufbaus, sondern eine Grenze der Pruefung - ein roter
    Punkt dafuer waere eine Falschmeldung auf jedem Entwicklungsrechner."""
    _with_if_inet6(monkeypatch, tmp_path, None)
    for ok, detail in (diagnostics._check_ipv6(), diagnostics._check_thread()):
        assert ok is True
        assert "Nicht feststellbar" in detail


# ---------------------------------------------------------------------------
# Thread-Zugangsdaten im matter-server
#
# Der aufgezeichnete Ernstfall vom 2026-09-04: matter-server war am Vortag neu
# gestartet und hatte damit die Thread-Zugangsdaten verloren (er haelt sie nur
# im Arbeitsspeicher, siehe `loxmatter/matter/otbr.py`). Nichts hat es
# gemeldet - kein Check, keine Zeile in der Oberflaeche. Sichtbar wurde es
# erst, als drei Einlernversuche hintereinander mit "Commission with code
# failed for node N" scheiterten, und selbst dann nannte die Meldung die
# Ursache nicht. Dieser Check macht den Zustand sichtbar, BEVOR jemand vor
# einem Geraet im Pairing-Modus steht.
# ---------------------------------------------------------------------------


def test_thread_credentials_check_is_green_when_matter_server_has_them():
    ok, detail = _check_thread_credentials(_ClientWithThreadDataset(True))
    assert ok
    assert detail


def test_thread_credentials_check_is_red_when_matter_server_lost_them():
    ok, detail = _check_thread_credentials(_ClientWithThreadDataset(False))
    assert not ok
    # Ein roter Punkt ohne Hinweis hilft niemandem (siehe
    # `test_a_failing_check_says_what_to_do`) - und dieser hier muss sagen,
    # dass ein Neustart des Dienstes die Ursache ist.
    assert "Neustart" in detail


def test_thread_credentials_check_stays_quiet_without_a_matter_connection():
    """Ohne Verbindung ist der Zustand nicht feststellbar - das ist die
    Aussage des matter-server-Checks daneben, nicht die dieses hier. Zwei
    rote Punkte fuer dieselbe Ursache verteilen die Aufmerksamkeit."""
    ok, detail = _check_thread_credentials(None)
    assert ok
    assert "feststellbar" in detail


async def test_the_system_check_carries_the_thread_credentials_line(api):
    client, _, _ = api
    checks = (await client.get("/api/diagnostics/system")).json()
    assert "thread-zugangsdaten" in {c["name"] for c in checks}
