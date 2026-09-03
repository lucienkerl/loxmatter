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

"""Der Live-Kanal fuer Logs, Mitschnitt und Kommando-Log (Task 4, Phase 5,
Spec 10.5) - siehe api/diagnostics_live.py.

Die Token-Absicherung dieser Route steht bewusst NICHT hier, sondern bei den
uebrigen WebSocket-Sicherheitstests in tests/api/test_security.py (siehe
dort, Abschnitt zu `/api/live` - `/api/diagnostics/live` folgt demselben
Muster daneben)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx2 as httpx
import pytest
from conftest import WebSocketClient, authenticate

from loxmatter.api.diagnostics_live import SNAPSHOT_LIMIT
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_app


async def _drain_snapshot(socket: Any, *, timeout: float = 0.5) -> None:
    """Liest die Momentaufnahme weg, bis eine kurze Weile lang nichts Neues
    mehr ankommt - wartet dabei NIE unbegrenzt (siehe Brief: "ein Test, der
    haengt, statt fehlzuschlagen, ist schlimmer als keiner")."""
    while True:
        try:
            await asyncio.wait_for(socket.receive_json(), timeout=timeout)
        except TimeoutError:
            return


async def test_a_fresh_datagram_arrives_as_a_message(api_with_runtime):
    """Der Strom haengt am SENDER, nicht an der Laufzeit: nur dort ist
    sichtbar, was tatsaechlich auf der Leitung war - einschliesslich
    Full-Resend und Impulsende, die die Laufzeit-Beobachter auslassen."""
    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        await _drain_snapshot(socket)
        await runtime.on_attribute(device_id, "2/144/4", 230000)
        message = await asyncio.wait_for(socket.receive_json(), timeout=2)

    assert message["kind"] == "datagram"
    assert message["key"] == f"d{device_id}_2_voltage"


async def test_a_fresh_log_line_arrives_as_a_message(api_with_runtime):
    client, _, _ = api_with_runtime
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        await _drain_snapshot(socket)
        logging.getLogger("loxmatter.test").warning("Miniserver nicht erreichbar")
        message = await asyncio.wait_for(socket.receive_json(), timeout=2)

    assert message["kind"] == "log"
    assert message["level"] == "WARNING"
    assert message["message"] == "Miniserver nicht erreichbar"


async def test_the_connection_starts_with_a_snapshot(api_with_runtime):
    """Ohne die Momentaufnahme klaffte eine Luecke zwischen 'einmal
    abrufen' und 'ab jetzt zuhoeren' - und die Ansicht waere beim Oeffnen
    leer, bis zufaellig etwas passiert."""
    client, runtime, device_id = api_with_runtime
    await runtime.on_attribute(device_id, "2/144/4", 230000)

    async with client.websocket_connect("/api/diagnostics/live") as socket:
        first = await asyncio.wait_for(socket.receive_json(), timeout=2)

    assert first["kind"] == "datagram"
    assert first["key"] == f"d{device_id}_2_voltage"


async def test_a_fresh_command_arrives_as_a_message(api_with_runtime):
    """Der dritte Strom: der Kommando-Log-Ring aus loxone/server.py, ueber
    die neue Beobachterkette auf `RingBuffer` (siehe api/diagnostics.py)."""
    client, _runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        await _drain_snapshot(socket)
        response = await client.get(f"/api/devices/{device_id}/controls")
        assert response.status_code == 200
        message = await asyncio.wait_for(socket.receive_json(), timeout=2)

    assert message["kind"] == "command"
    assert message["method"] == "GET"
    assert message["path"] == f"/api/devices/{device_id}/controls"
    assert message["status"] == 200


async def test_the_snapshot_is_capped_per_stream(api_with_runtime):
    """`SNAPSHOT_LIMIT` begrenzt die Momentaufnahme je Strom - siehe
    Docstring dort: 500 Eintraege x 3 Stroeme auf einen Schlag waeren beim
    Oeffnen der Ansicht eine spuerbare Nachricht."""
    client, _runtime, device_id = api_with_runtime
    extra_commands = SNAPSHOT_LIMIT + 5
    for _ in range(extra_commands):
        response = await client.get(f"/api/devices/{device_id}/controls")
        assert response.status_code == 200

    async with client.websocket_connect("/api/diagnostics/live") as socket:
        commands = []
        while True:
            try:
                message = await asyncio.wait_for(socket.receive_json(), timeout=0.5)
            except TimeoutError:
                break
            if message["kind"] == "command":
                commands.append(message)

    assert len(commands) == SNAPSHOT_LIMIT


async def test_observers_are_unsubscribed_after_disconnect(api_with_runtime, caplog):
    """Im `finally` werden alle drei Beobachter wieder abgemeldet -
    Aktivitaet NACH dem Trennen darf weder einen Fehler werfen noch die
    naechste Verbindung beeintraechtigen."""
    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        await _drain_snapshot(socket)

    # Nach dem Trennen: neue Eintraege in allen drei Stroemen duerfen nicht
    # gegen eine tote Verbindung anlaufen.
    await runtime.on_attribute(device_id, "2/144/4", 231000)
    logging.getLogger("loxmatter.test").warning("nach dem Trennen")
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)

    async with client.websocket_connect("/api/diagnostics/live") as socket:
        first = await asyncio.wait_for(socket.receive_json(), timeout=2)
    assert first["kind"] == "datagram"
    assert first["key"] == f"d{device_id}_2_voltage"


class _RecordingSender:
    """Wie `RecordingSender` in test_live.py - erfuellt `Runtime`, ohne
    einen echten UDP-Mitschnitt zu fuehren. Fuer den Beleg unten, dass die
    Route auch OHNE `sender`/`log_handler` an `build_app` antwortet."""

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        return True

    async def close(self) -> None:
        return None


@pytest.fixture
async def api_without_diagnostics_streams(plug_store, no_invoke, fake_client):
    """Wie `api_with_runtime`, aber OHNE `sender`/`log_handler` an
    `build_app` - fuer den Beleg, dass `GET /api/diagnostics/live` trotzdem
    antwortet und nur die beiden fehlenden Zweige entfallen (siehe
    api/diagnostics_live.py, Moduldocstring)."""
    store, device_id = plug_store
    runtime = Runtime(store, _RecordingSender())
    app = build_app(store, no_invoke, runtime, client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield WebSocketClient(client, app), runtime, device_id


async def test_without_a_sender_or_log_handler_the_route_still_answers(
    api_without_diagnostics_streams,
):
    """`sender` und `log_handler` sind optional (siehe `build_app`) - fehlt
    einer, entfaellt sein Zweig, nicht die Route: die Anmeldung selbst
    (`POST /auth/login`) landet trotzdem im Kommando-Log-Zweig."""
    client, _runtime, _device_id = api_without_diagnostics_streams
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        message = await asyncio.wait_for(socket.receive_json(), timeout=2)

    assert message["kind"] == "command"
    assert message["path"] == "/auth/login"
