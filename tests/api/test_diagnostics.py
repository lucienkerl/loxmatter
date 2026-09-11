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

"""Tests for the diagnostics API (Task 6, Phase 5) - see api/diagnostics.py.

Three local fixtures, following the pattern of the other files under
tests/api/ (every test file builds its own `api` fixture to match its
needs, see the conftest.py module docstring):

`api` - basic setup with a real `fake_client` (connected) AND a real
`matter_data_dir` (for the backup), but WITHOUT a real `UdpSender` (most
tests here need no recording).

`api_with_sender` - additionally a real `UdpSender` that sends to a local
UDP socket (`receiver`, as in tests/loxone/test_sender.py) - for the two
recording tests. The recording lives in `UdpSender` itself (see the module
docstring of sender.py), so a fake sender wouldn't trigger it at all.

`api_without_matter` - `client=None`, which as documented in server.py
means "the bridge is running without a Matter connection" - for the test
that a red line in the system check carries a useful hint.

`api_with_token` - like `api`, but with a configured API token instead of
a signed-in session. `GET /api/diagnostics/fabric-backup` requires - like
every `/api` route - one of the two proofs (Task 8, Phase 5, Spec 9); the
tests that look at the backup itself identify via the token header rather
than a login here, to cover both paths. The remaining fixtures
deliberately stay without a token: every other diagnostics route is
unaffected by it, and that should keep being checked here the way an
operation without a token actually sees them.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import httpx2 as httpx
import pytest
from conftest import authenticate, load_snapshot

from loxmatter import i18n
from loxmatter.api import diagnostics
from loxmatter.api.diagnostics import RingBuffer, _check_thread_credentials
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.sender import UdpSender
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


class _ClientWithThreadDataset:
    """Stands in for `BridgeMatterClient` - only the two attributes
    `_check_thread_credentials` reads."""

    def __init__(self, thread_dataset_set: bool, connected: bool = True) -> None:
        self.thread_dataset_set = thread_dataset_set
        self.connected = connected


def _matter_data_dir(tmp_path: Path) -> Path:
    """A directory with a harmless test file - standing in for the
    matter-server data directory without touching any real key material
    (see the task brief: tests/fixtures/VirtualIn|VirtualOut are off
    limits, but those have nothing to do with this file)."""
    directory = tmp_path / "matter-data"
    directory.mkdir()
    (directory / "credentials.json").write_text('{"fixture": "no real keys"}')
    return directory


_BACKUP_TOKEN = "test-token"
_BACKUP_HEADERS = {"Authorization": f"Bearer {_BACKUP_TOKEN}"}


@pytest.fixture
def receiver() -> Iterator[socket.socket]:
    """Like in tests/loxone/test_sender.py - a UDP socket on 127.0.0.1 that
    never leaves the machine."""
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
    store.register_commands(device_id, extract_commands(snapshot))

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
    store.register_commands(device_id, extract_commands(snapshot))

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
    """Like `api`, but with `api_token` configured - see the module docstring."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot))

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
    store.register_commands(device_id, extract_commands(snapshot))

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
    """A bridge runs for months at a time - the recording must not keep growing."""
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
    """Spec 4.1: the one piece of data in the system that can't be replaced."""
    client, _, _ = api_with_token
    response = await client.get("/api/diagnostics/fabric-backup", headers=_BACKUP_HEADERS)
    assert response.status_code == 200
    assert response.headers["content-type"] in ("application/zip", "application/gzip")
    assert len(response.content) > 0


async def test_fabric_backup_is_503_without_a_configured_directory(
    no_invoke, fake_runtime, fake_client, tmp_path
):
    """The first of the two 503 branches (Task 6 review, point 3):
    `matter_data_dir is None` - the service runs without
    `--matter-data-dir`, e.g. because the deployment doesn't set this
    option (yet) (see deploy/testhost/docker-compose.yml, deliberately
    commented out there until Task 8 delivers token protection)."""
    store = Store(tmp_path / "t.sqlite")
    app = build_app(
        store, no_invoke, fake_runtime(store), client=fake_client, api_token=_BACKUP_TOKEN
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/diagnostics/fabric-backup", headers=_BACKUP_HEADERS)
    store.close()

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "The matter-server data directory is not mounted for this service — a backup "
        "therefore cannot be created. See the deployment (docker-compose.yml, "
        "--matter-data-dir)."
    )


async def test_fabric_backup_is_503_without_a_configured_directory_in_german(
    no_invoke, fake_runtime, fake_client, tmp_path
):
    """German companion test to
    test_fabric_backup_is_503_without_a_configured_directory."""
    store = Store(tmp_path / "t.sqlite")
    app = build_app(
        store, no_invoke, fake_runtime(store), client=fake_client, api_token=_BACKUP_TOKEN
    )
    store.locale.set_language("de")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/diagnostics/fabric-backup", headers=_BACKUP_HEADERS)
    store.close()

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Das matter-server-Datenverzeichnis ist fuer diesen Dienst nicht "
        "eingehaengt - eine Sicherung kann deshalb nicht erstellt werden. "
        "Siehe die Bereitstellung (docker-compose.yml, --matter-data-dir)."
    )


async def test_fabric_backup_is_503_when_the_configured_directory_is_missing(
    no_invoke, fake_runtime, fake_client, tmp_path
):
    """The second 503 branch: `matter_data_dir` is set, but the path
    doesn't exist (any more) - e.g. a mount that was detached in the
    meantime."""
    store = Store(tmp_path / "t.sqlite")
    missing = tmp_path / "does-not-exist"
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
    assert response.json()["detail"] == (
        "The configured matter-server data directory does not exist or is not a "
        "directory. Check the mount."
    )


async def test_fabric_backup_is_503_when_the_configured_directory_is_missing_in_german(
    no_invoke, fake_runtime, fake_client, tmp_path
):
    """German companion test to
    test_fabric_backup_is_503_when_the_configured_directory_is_missing."""
    store = Store(tmp_path / "t.sqlite")
    missing = tmp_path / "does-not-exist"
    app = build_app(
        store,
        no_invoke,
        fake_runtime(store),
        client=fake_client,
        matter_data_dir=missing,
        api_token=_BACKUP_TOKEN,
    )
    store.locale.set_language("de")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/diagnostics/fabric-backup", headers=_BACKUP_HEADERS)
    store.close()

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Das konfigurierte matter-server-Datenverzeichnis existiert nicht "
        "oder ist kein Verzeichnis. Pruefen Sie die Einhaengung."
    )


async def test_fabric_backup_is_503_when_the_configured_path_is_a_file(
    no_invoke, fake_runtime, fake_client, tmp_path
):
    """The same branch as above (`not matter_data_dir.is_dir()`), but via
    the third case not covered so far: the path does exist, but isn't a
    directory - just an ordinary file."""
    store = Store(tmp_path / "t.sqlite")
    not_a_directory = tmp_path / "matter-data-is-a-file"
    not_a_directory.write_text("no fabric backup, just an ordinary file")
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
    """A red dot with no hint helps nobody."""
    client, _, _ = api_without_matter
    checks = (await client.get("/api/diagnostics/system")).json()
    failing = next(c for c in checks if not c["ok"])
    assert len(failing["detail"]) > 20
    matter_check = next(c for c in checks if c["name"] == "matter-server")
    assert matter_check["ok"] is False
    assert matter_check["detail"] == (
        "No matter-server client configured — the bridge is running without a Matter "
        "connection. This is always set for `loxmatter run`; if it's missing here, this "
        "service was started with an incomplete setup."
    )


async def test_a_failing_check_says_what_to_do_in_german(api_without_matter):
    """German companion test to test_a_failing_check_says_what_to_do."""
    client, store, _ = api_without_matter
    store.locale.set_language("de")
    checks = (await client.get("/api/diagnostics/system")).json()
    matter_check = next(c for c in checks if c["name"] == "matter-server")
    assert matter_check["ok"] is False
    assert matter_check["detail"] == (
        "Kein matter-server-Client konfiguriert - die Bruecke laeuft ohne Matter-"
        "Anbindung. Das ist bei `loxmatter run` immer gesetzt; fehlt es hier, "
        "wurde dieser Dienst mit einem unvollstaendigen Aufbau gestartet."
    )


async def test_command_log_does_not_record_diagnostics_polling(api):
    """Signal/noise decision (see the module docstring of diagnostics.py):
    a client that keeps the diagnostics page open and polls every few
    seconds should not flood the small ring buffer with itself."""
    client, _, _ = api
    for _ in range(5):
        await client.get("/api/diagnostics/commands")
        await client.get("/api/diagnostics/system")
    entries = (await client.get("/api/diagnostics/commands")).json()
    assert all(not e["path"].startswith("/api/diagnostics") for e in entries)


async def test_command_log_never_carries_a_query_string(api):
    """Looking ahead to Task 8 (token protection for the backup): if the
    token is ever carried as a query parameter somewhere, it must not end
    up in the command log that's visible to every diagnostics viewer.
    Query strings are therefore never recorded at all, on principle,
    regardless of which route carries them."""
    client, _, device_id = api
    await client.get(f"/cmd/d{device_id}_1_on/1?secret=should-not-be-logged")
    entries = (await client.get("/api/diagnostics/commands")).json()
    assert all("?" not in e["path"] and "secret" not in e["path"] for e in entries)


async def test_a_check_that_raises_unexpectedly_fails_gracefully(api, monkeypatch):
    """Point 3 of the brief: a check that raises itself (not just one of the
    expected error kinds, but an actual bug) must not shoot down the
    entire endpoint with 500 - it turns into exactly one red line."""
    client, store, _ = api

    def _broken_check_writable() -> None:
        raise RuntimeError("Simulated bug in the check itself")

    monkeypatch.setattr(store, "check_writable", _broken_check_writable)

    response = await client.get("/api/diagnostics/system")
    assert response.status_code == 200
    checks = response.json()
    store_check = next(c for c in checks if c["name"] == "store")
    assert store_check["ok"] is False
    assert len(store_check["detail"]) > 20
    assert store_check["detail"] == (
        "This check itself failed (Simulated bug in the check itself) "
        "— that is a bug in the check, not necessarily in the checked system. The full "
        "traceback is in the server log."
    )


async def test_a_check_that_raises_unexpectedly_fails_gracefully_in_german(api, monkeypatch):
    """German companion test to
    test_a_check_that_raises_unexpectedly_fails_gracefully."""
    client, store, _ = api

    def _broken_check_writable() -> None:
        raise RuntimeError("Simulated bug in the check itself")

    monkeypatch.setattr(store, "check_writable", _broken_check_writable)
    store.locale.set_language("de")

    response = await client.get("/api/diagnostics/system")
    assert response.status_code == 200
    checks = response.json()
    store_check = next(c for c in checks if c["name"] == "store")
    assert store_check["ok"] is False
    assert store_check["detail"] == (
        "Diese Pruefung selbst ist fehlgeschlagen (Simulated bug in the check itself) "
        "- das ist ein Fehler in der Pruefung, nicht zwangslaeufig im "
        "gepruerften System. Der volle Traceback steht im Server-Log."
    )


# ---------------------------------------------------------------------------
# IPv6 and Thread checks (2026-09-03)
# ---------------------------------------------------------------------------

# Excerpt from a real `/proc/net/if_inet6` of the test host. Columns:
# address (hex, no colons), interface index, prefix length, scope, flags,
# name.
_IF_INET6_WITH_THREAD = """\
fe80000000000000da3addfffe99419e 03 40 20 80 wlan0
00000000000000000000000000000001 01 80 10 80 lo
fd2745d78c7800010e26ce8e4edd7c50 07 40 00 00 wpan0
fd7df0629267d2e0000000fffe00fc10 07 40 00 00 wpan0
"""

# The same host, after the OTBR agent died on an RCP timeout: `wpan0` is
# gone, leaving only link-local and loopback.
_IF_INET6_WITHOUT_THREAD = """\
fe80000000000000da3addfffe99419e 03 40 20 80 wlan0
00000000000000000000000000000001 01 80 10 80 lo
"""


def _with_if_inet6(monkeypatch, tmp_path, content: str | None) -> None:
    """Points `_IF_INET6` at a file with this content - or at a path that
    doesn't exist, when `content is None` (non-Linux)."""
    path = tmp_path / "if_inet6"
    if content is not None:
        path.write_text(content, encoding="ascii")
    monkeypatch.setattr(diagnostics, "_IF_INET6", path)


def test_ipv6_accepts_a_unique_local_address(monkeypatch, tmp_path):
    """The bug this check used to have: it required a route to a GLOBAL
    address and reported red on a healthy Thread setup. Thread runs over
    unique-local addresses, and most home networks have no global IPv6 at
    all."""
    _with_if_inet6(monkeypatch, tmp_path, _IF_INET6_WITH_THREAD)
    ok, detail = diagnostics._check_ipv6()
    assert ok is True
    assert "fd27" in detail
    assert "on wpan0" in detail


def test_ipv6_accepts_a_unique_local_address_in_german(monkeypatch, tmp_path):
    """German companion test to test_ipv6_accepts_a_unique_local_address.
    `_check_ipv6` is a pure function with no HTTP call, so `i18n.set_language`
    directly is enough here."""
    i18n.set_language("de")
    _with_if_inet6(monkeypatch, tmp_path, _IF_INET6_WITH_THREAD)
    ok, detail = diagnostics._check_ipv6()
    assert ok is True
    assert "fd27" in detail
    assert "auf wpan0" in detail


def test_ipv6_fails_when_only_link_local_and_loopback_remain(monkeypatch, tmp_path):
    _with_if_inet6(monkeypatch, tmp_path, _IF_INET6_WITHOUT_THREAD)
    ok, detail = diagnostics._check_ipv6()
    assert ok is False
    assert "link-local" in detail


def test_ipv6_fails_when_only_link_local_and_loopback_remain_in_german(monkeypatch, tmp_path):
    """German companion test to
    test_ipv6_fails_when_only_link_local_and_loopback_remain. `_check_ipv6`
    is a pure function with no HTTP call, so `i18n.set_language` directly is
    enough here - no middleware re-reads the language."""
    i18n.set_language("de")
    _with_if_inet6(monkeypatch, tmp_path, _IF_INET6_WITHOUT_THREAD)
    ok, detail = diagnostics._check_ipv6()
    assert ok is False
    assert "link-lokale" in detail


def test_thread_check_finds_the_mesh_interface(monkeypatch, tmp_path):
    _with_if_inet6(monkeypatch, tmp_path, _IF_INET6_WITH_THREAD)
    ok, detail = diagnostics._check_thread()
    assert ok is True
    assert "wpan0" in detail
    assert "mesh address" in detail


def test_thread_check_finds_the_mesh_interface_in_german(monkeypatch, tmp_path):
    """German companion test to test_thread_check_finds_the_mesh_interface.
    `_check_thread` is a pure function with no HTTP call, so
    `i18n.set_language` directly is enough here."""
    i18n.set_language("de")
    _with_if_inet6(monkeypatch, tmp_path, _IF_INET6_WITH_THREAD)
    ok, detail = diagnostics._check_thread()
    assert ok is True
    assert "wpan0" in detail
    assert "Mesh-Adresse" in detail


def test_thread_check_fails_when_the_interface_is_gone(monkeypatch, tmp_path):
    """The real outage from 2026-09-03: the radio module stopped
    responding, the OTBR agent aborted with an RCP timeout, `wpan0`
    disappeared - and the container kept running, so `restart:
    unless-stopped` never kicked in. No device was reachable for six and a
    half hours. This check would have shown it."""
    _with_if_inet6(monkeypatch, tmp_path, _IF_INET6_WITHOUT_THREAD)
    ok, detail = diagnostics._check_thread()
    assert ok is False
    assert "OTBR" in detail
    assert "restart" in detail
    assert "No Thread interface" in detail


def test_thread_check_fails_when_the_interface_is_gone_in_german(monkeypatch, tmp_path):
    """German companion test to
    test_thread_check_fails_when_the_interface_is_gone.
    `_check_thread` is a pure function with no HTTP call, so
    `i18n.set_language` directly is enough here."""
    i18n.set_language("de")
    _with_if_inet6(monkeypatch, tmp_path, _IF_INET6_WITHOUT_THREAD)
    ok, detail = diagnostics._check_thread()
    assert ok is False
    assert "OTBR" in detail
    assert "restart" in detail
    assert "Keine Thread-Schnittstelle" in detail


def test_both_checks_stay_quiet_where_they_cannot_look(monkeypatch, tmp_path):
    """On a non-Linux system, /proc/net/if_inet6 doesn't exist. That's not a
    fault in the setup, but a limit of the check - a red dot for it would be
    a false alarm on every development machine."""
    _with_if_inet6(monkeypatch, tmp_path, None)
    for ok, detail in (diagnostics._check_ipv6(), diagnostics._check_thread()):
        assert ok is True
        assert "Not determinable" in detail


def test_both_checks_stay_quiet_where_they_cannot_look_in_german(monkeypatch, tmp_path):
    """German companion test to
    test_both_checks_stay_quiet_where_they_cannot_look.
    Both checks are pure functions with no HTTP call, so `i18n.set_language`
    directly is enough here."""
    i18n.set_language("de")
    _with_if_inet6(monkeypatch, tmp_path, None)
    for ok, detail in (diagnostics._check_ipv6(), diagnostics._check_thread()):
        assert ok is True
        assert "Nicht feststellbar" in detail


# ---------------------------------------------------------------------------
# Thread credentials in matter-server
#
# The recorded real-world incident from 2026-09-04: matter-server had been
# restarted the day before and had thereby lost the Thread credentials (it
# only holds them in memory, see `loxmatter/matter/otbr.py`). Nothing
# reported it - no check, no line in the UI. It only became visible when
# three commissioning attempts in a row failed with "Commission with code
# failed for node N", and even then the message didn't name the cause.
#
# This point makes the state visible - but it is NO LONGER an alarm. Ever
# since commissioning fetches the dataset itself from the border router
# (see `api/devices.py`), "not set" is the perfectly healthy normal state
# after every restart of the Pi: matter-server starts without the data,
# nobody commissions anything, and the state resolves itself on the next
# commissioning. A point that stayed red the whole time for this, with text
# explaining that there's nothing to do, devalues the red dots next to it.
# The alarm that actually demands action sits in the `thread` point: it
# turns red when no border router is running at all.
# ---------------------------------------------------------------------------


def test_thread_credentials_check_is_green_when_matter_server_has_them():
    ok, detail = _check_thread_credentials(_ClientWithThreadDataset(True))
    assert ok
    assert detail


def test_thread_credentials_check_stays_green_when_matter_server_lacks_them():
    """The healthy normal state after every restart of the Pi - not an
    alarm, but a status line. It still has to say two things: that the
    next commissioning fetches the data from the border router
    automatically, and where the point sits that turns red when none is
    running at all there.

    Checked in the default language (English); the German version is in
    the test below - the same pairing pattern as the IPv6/Thread checks
    above."""
    ok, detail = _check_thread_credentials(_ClientWithThreadDataset(False))
    assert ok
    assert "commissioning" in detail
    assert "Border Router" in detail
    # Refers to the neighboring point `thread` - exactly the name it has
    # under `GET /api/diagnostics/system`. That's a fixed identifier and
    # therefore stays the same in both languages.
    assert "thread" in detail


def test_thread_credentials_check_says_the_same_in_german():
    """This line is UI text and changes with the language. No HTTP call
    needed - the check is a pure function, hence `i18n.set_language`
    directly here."""
    i18n.set_language("de")

    ok, detail = _check_thread_credentials(_ClientWithThreadDataset(False))

    assert ok
    assert "Einlernen" in detail
    assert "Border Router" in detail
    assert "thread" in detail


def test_thread_credentials_check_stays_quiet_without_a_matter_connection():
    """Without a connection, the state can't be determined - that is the
    point of the matter-server check next to it, not of this one. Two red
    dots for the same cause split attention."""
    ok, detail = _check_thread_credentials(None)
    assert ok
    assert "Not determinable" in detail
    assert "matter-server" in detail


def test_thread_credentials_check_stays_quiet_without_a_matter_connection_in_german():
    i18n.set_language("de")

    ok, detail = _check_thread_credentials(None)

    assert ok
    assert "Nicht feststellbar" in detail
    assert "matter-server" in detail


async def test_the_system_check_carries_the_thread_credentials_line(api):
    """The point's name is a fixed identifier, not a translation - exactly
    like "matter-server", "store", "ipv6", "thread" and "miniserver" next
    to it. It therefore does NOT change with the language, and a log or an
    error report stays readable across language boundaries."""
    client, _, _ = api
    checks = (await client.get("/api/diagnostics/system")).json()
    assert "thread-credentials" in {c["name"] for c in checks}


# ---------------------------------------------------------------------------
# POST /api/diagnostics/resync - the resync button in the system tab
# ---------------------------------------------------------------------------
#
# The same effect as `GET /resync` (see tests/loxone/test_server.py), just
# from the other side: `/resync` belongs to the Miniserver and deliberately
# stays open, this route belongs to the UI and sits behind the guard like
# every `/api` route. Both call the same `Runtime.resend_all`.
#
# A dedicated fixture, because `api` above doesn't hand out the runtime: the
# tests here need to touch it (set a count, trigger a failure), while every
# other test in this file only needs it as incidental to `build_app`.


@pytest.fixture
async def api_with_runtime(tmp_path, no_invoke, fake_runtime, fake_client):
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot))

    runtime = fake_runtime(store)
    app = build_app(store, no_invoke, runtime, client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, runtime
    store.close()


async def test_resync_triggers_a_full_resend(api_with_runtime):
    """The button should trigger the same thing as the system-start block in
    the Config project - not something similar."""
    client, runtime = api_with_runtime
    runtime.resend_result = 7

    response = await client.post("/api/diagnostics/resync")

    assert response.status_code == 200
    assert runtime.resend_calls == 1


async def test_resync_reports_how_many_values_went_out(api_with_runtime):
    """The number is the only feedback the user gets (the UI shows it as a
    brief message) - it has to be the real one. English key in the wire
    format, as with `/resync`."""
    client, runtime = api_with_runtime
    runtime.resend_result = 42

    response = await client.post("/api/diagnostics/resync")

    assert response.json() == {"sent": 42}


async def test_resync_reports_a_broken_sender_as_502(api_with_runtime):
    """Like `/resync` (review fix Minor #3 there): a dead sender is not a
    bug in this route. 502 instead of 500, and the message says what went
    wrong."""
    client, runtime = api_with_runtime
    runtime.fail_resend_with = OSError("Socket is closed")

    response = await client.post("/api/diagnostics/resync")

    assert response.status_code == 502
    assert "Socket is closed" in response.json()["detail"]


async def test_resync_keeps_the_traceback_out_of_the_answer(api_with_runtime):
    """Same reason as with `/resync` and `/cmd`: the full traceback belongs
    in the log, not in an HTTP response."""
    client, runtime = api_with_runtime
    runtime.fail_resend_with = OSError("Socket is closed")

    response = await client.post("/api/diagnostics/resync")

    assert "Traceback" not in response.text


async def test_resync_fails_in_german(no_invoke, fake_runtime, fake_client, tmp_path):
    """German companion test to test_resync_reports_a_broken_sender_as_502 -
    the language hangs off the store, not the process (see the `_in_german`
    tests of the security suite above)."""
    store = Store(tmp_path / "t.sqlite")
    runtime = fake_runtime(store)
    runtime.fail_resend_with = OSError("Socket is closed")
    app = build_app(store, no_invoke, runtime, client=fake_client)
    store.locale.set_language("de")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        response = await client.post("/api/diagnostics/resync")
    store.close()

    assert response.status_code == 502
    assert "fehlgeschlagen" in response.json()["detail"]
    assert "Socket is closed" in response.json()["detail"]
