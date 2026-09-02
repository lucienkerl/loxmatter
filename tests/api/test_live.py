import asyncio

import pytest

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
