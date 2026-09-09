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

import asyncio
import logging

import pytest

from loxmatter.api.streaming import BoundedQueue
from loxmatter.loxone.runtime import Runtime


class RecordingSender:
    async def send(self, key, value, *, force=False) -> bool:
        return True

    async def close(self) -> None: ...


async def test_observer_sees_every_value_the_sender_sees(tmp_path, plug_store):
    """Spec 8.3: one path, not two."""
    store, device_id = plug_store
    seen: list[tuple[str, object]] = []
    runtime = Runtime(store, RecordingSender())
    runtime.add_observer(lambda key, value: seen.append((key, value)))
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert seen == [(f"d{device_id}_2_voltage", pytest.approx(230.0))]


async def test_a_failing_observer_does_not_stop_the_udp_sender(tmp_path, plug_store):
    """The UI must not drag down the bridge."""
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
    """A closed browser tab must not write an error to the log."""
    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/live"):
        pass
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert runtime.observer_count() == 0


async def test_bounded_queue_drops_oldest_keeps_newest_and_logs_once(caplog):
    """Review-Fix Important #1: a full queue drops the OLDEST entry, not
    the newest - a live view wants the current state. The debug log reports
    only on the TRANSITION to dropping, not on every subsequent drop
    (otherwise a permanently stuck connection floods the log instead of
    just making it findable)."""
    queue = BoundedQueue(maxsize=3, connection_label="test-client")
    with caplog.at_level(logging.DEBUG, logger="loxmatter.api.streaming"):
        for i in range(5):
            queue.put({"key": f"k{i}", "value": i})
    received = [await queue.get() for _ in range(3)]
    assert received == [
        {"key": "k2", "value": 2},
        {"key": "k3", "value": 3},
        {"key": "k4", "value": 4},
    ]
    drop_logs = [r for r in caplog.records if "dropping oldest" in r.getMessage()]
    assert len(drop_logs) == 1


async def test_full_queue_does_not_affect_sender_or_observer_registration(plug_store):
    """An overflow of the WebUI queue affects only the display - never the
    UDP path (which has long since sent, see `on_attribute`) and never the
    observer registration itself (Review-Fix Important #1)."""
    store, device_id = plug_store
    sent: list[str] = []

    class Sender:
        async def send(self, key, value, *, force=False) -> bool:
            sent.append(key)
            return True

        async def close(self) -> None: ...

    runtime = Runtime(store, Sender())
    queue = BoundedQueue(maxsize=8, connection_label="test-client")
    runtime.add_observer(lambda key, value: queue.put({"key": key, "value": value}))

    total = 20  # much more than the queue size of 8
    for i in range(total):
        await runtime.on_attribute(device_id, "2/144/4", 230000 + i)

    assert sent == [f"d{device_id}_2_voltage"] * total
    assert runtime.observer_count() == 1


async def test_a_broken_send_is_treated_like_a_disconnect(api_with_runtime, caplog):
    """Review-Fix Important #2: some ASGI servers throw a `RuntimeError`
    instead of a `WebSocketDisconnect` when attempting to send on an already
    lost connection - see the module docstring of `api/live.py`. This must
    neither be logged as an error nor leave the observer registered."""
    client, runtime, device_id = api_with_runtime
    conn = client.websocket_connect("/api/live", break_send_after=0)
    await conn.__aenter__()
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    await conn.wait_closed()
    assert runtime.observer_count() == 0
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


async def test_a_stuck_reader_does_not_block_another_connection(api_with_runtime):
    """Each connection has its own queue (Review-Fix Minor #4) - one that
    does not read must not slow down another, and both are cleanly
    unregistered on disconnect."""
    client, runtime, device_id = api_with_runtime
    async with (
        client.websocket_connect("/api/live") as healthy,
        client.websocket_connect("/api/live") as stuck,
    ):
        assert stuck is not None  # second, independent connection - deliberately never reads
        assert runtime.observer_count() == 2
        await runtime.on_attribute(device_id, "2/144/4", 230000)
        message = await asyncio.wait_for(healthy.receive_json(), timeout=2)
        assert message["key"] == f"d{device_id}_2_voltage"
        assert message["value"] == pytest.approx(230.0)
    assert runtime.observer_count() == 0
