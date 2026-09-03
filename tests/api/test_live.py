import asyncio
import logging

import pytest

from loxmatter.api.live import _BoundedQueue
from loxmatter.loxone.runtime import Runtime


class RecordingSender:
    async def send(self, key, value, *, force=False) -> bool:
        return True

    async def close(self) -> None: ...


async def test_observer_sees_every_value_the_sender_sees(tmp_path, plug_store):
    """Spec 8.3: ein Pfad, nicht zwei."""
    store, device_id = plug_store
    seen: list[tuple[str, object]] = []
    runtime = Runtime(store, RecordingSender())
    runtime.add_observer(lambda key, value: seen.append((key, value)))
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert seen == [(f"d{device_id}_2_voltage", pytest.approx(230.0))]


async def test_a_failing_observer_does_not_stop_the_udp_sender(tmp_path, plug_store):
    """Die Oberflaeche darf die Bruecke nicht mitreissen."""
    store, device_id = plug_store
    sent: list[str] = []

    class Sender:
        async def send(self, key, value, *, force=False) -> bool:
            sent.append(key)
            return True

        async def close(self) -> None: ...

    runtime = Runtime(store, Sender())

    def boom(key: str, value: object) -> None:
        raise RuntimeError("Beobachter kaputt")

    runtime.add_observer(boom)
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert sent == [f"d{device_id}_2_voltage"]


async def test_removed_observer_stops_receiving(tmp_path, plug_store):
    store, device_id = plug_store
    seen: list[str] = []
    runtime = Runtime(store, RecordingSender())
    observer = lambda key, value: seen.append(key)
    runtime.add_observer(observer)
    runtime.remove_observer(observer)
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert seen == []


async def test_websocket_delivers_a_value(api_with_runtime):
    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/live") as ws:
        await runtime.on_attribute(device_id, "2/144/4", 230000)
        message = await asyncio.wait_for(ws.receive_json(), timeout=2)
    assert message["key"] == f"d{device_id}_2_voltage"
    assert message["value"] == pytest.approx(230.0)


async def test_a_disconnecting_client_is_dropped_without_noise(api_with_runtime):
    """Ein geschlossener Browser-Tab darf keinen Fehler ins Log schreiben."""
    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/live"):
        pass
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert runtime.observer_count() == 0


async def test_bounded_queue_drops_oldest_keeps_newest_and_logs_once(caplog):
    """Review-Fix Important #1: eine volle Warteschlange wirft den
    AELTESTEN Eintrag weg, nicht den neuesten - eine Live-Ansicht will den
    aktuellsten Stand. Das Debug-Log meldet sich nur beim UEBERGANG ins
    Verwerfen, nicht bei jedem weiteren Verwurf (sonst flutet eine dauerhaft
    haengende Verbindung das Log, statt sie nur auffindbar zu machen)."""
    queue = _BoundedQueue(maxsize=3, connection_label="test-client")
    with caplog.at_level(logging.DEBUG, logger="loxmatter.api.live"):
        for i in range(5):
            queue.put(f"k{i}", i)
    received = [await queue.get() for _ in range(3)]
    assert received == [("k2", 2), ("k3", 3), ("k4", 4)]
    drop_logs = [r for r in caplog.records if "verworfen" in r.getMessage()]
    assert len(drop_logs) == 1


async def test_full_queue_does_not_affect_sender_or_observer_registration(plug_store):
    """Ein Ueberlauf der WebUI-Warteschlange betrifft nur die Anzeige - nie
    den UDP-Pfad (der laengst gesendet hat, siehe `on_attribute`) und nie
    die Beobachter-Registrierung selbst (Review-Fix Important #1)."""
    store, device_id = plug_store
    sent: list[str] = []

    class Sender:
        async def send(self, key, value, *, force=False) -> bool:
            sent.append(key)
            return True

        async def close(self) -> None: ...

    runtime = Runtime(store, Sender())
    queue = _BoundedQueue(maxsize=8, connection_label="test-client")
    runtime.add_observer(lambda key, value: queue.put(key, value))

    total = 20  # deutlich mehr als die Warteschlangengroesse von 8
    for i in range(total):
        await runtime.on_attribute(device_id, "2/144/4", 230000 + i)

    assert sent == [f"d{device_id}_2_voltage"] * total
    assert runtime.observer_count() == 1


async def test_a_broken_send_is_treated_like_a_disconnect(api_with_runtime, caplog):
    """Review-Fix Important #2: manche ASGI-Server werfen bei einem
    Sendeversuch auf eine bereits verlorene Verbindung kein
    `WebSocketDisconnect`, sondern ein `RuntimeError` - siehe Modul-Docstring
    von `api/live.py`. Das darf weder als Fehler geloggt werden noch den
    Beobachter angemeldet lassen."""
    client, runtime, device_id = api_with_runtime
    conn = client.websocket_connect("/api/live", break_send_after=0)
    await conn.__aenter__()
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    await conn.wait_closed()
    assert runtime.observer_count() == 0
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


async def test_a_stuck_reader_does_not_block_another_connection(api_with_runtime):
    """Jede Verbindung hat ihre eigene Warteschlange (Review-Fix Minor #4) -
    eine, die nicht liest, darf eine andere nicht ausbremsen, und beide
    werden beim Trennen sauber abgemeldet."""
    client, runtime, device_id = api_with_runtime
    async with (
        client.websocket_connect("/api/live") as healthy,
        client.websocket_connect("/api/live") as stuck,
    ):
        assert stuck is not None  # zweite, unabhaengige Verbindung - liest absichtlich nie mit
        assert runtime.observer_count() == 2
        await runtime.on_attribute(device_id, "2/144/4", 230000)
        message = await asyncio.wait_for(healthy.receive_json(), timeout=2)
        assert message["key"] == f"d{device_id}_2_voltage"
        assert message["value"] == pytest.approx(230.0)
    assert runtime.observer_count() == 0
