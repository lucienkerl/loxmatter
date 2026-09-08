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

"""Der Verwalter, der die Verbindung zu matter-server wieder aufbaut."""

from __future__ import annotations

import asyncio

import pytest

from loxmatter.matter.supervisor import attach, supervise


class FakeClient:
    """Steht fuer `BridgeMatterClient`, so weit der Verwalter ihn benutzt."""

    def __init__(self, connect_failures: int = 0) -> None:
        self._connect_failures = connect_failures
        self.connect_calls = 0
        self.subscribe_calls = 0
        self._link_lost = asyncio.Event()

    async def wait_for_link_loss(self) -> None:
        await self._link_lost.wait()
        self._link_lost.clear()

    def drop_link(self) -> None:
        self._link_lost.set()

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_calls <= self._connect_failures:
            raise ConnectionError("matter-server nicht erreichbar")

    async def subscribe(self, resolve_device_id, handler) -> None:
        self.subscribe_calls += 1

    async def snapshots(self) -> list[object]:
        return []


class FakeStore:
    def __init__(self) -> None:
        self.backfill_types_calls = 0
        self.backfill_commands_calls = 0

    def device_id_for_node(self, node_id: int) -> int | None:
        return None

    def backfill_device_types(self, snapshots) -> int:
        self.backfill_types_calls += 1
        return 0

    def backfill_commands(self, snapshots) -> int:
        self.backfill_commands_calls += 1
        return 3


class FakeRuntime:
    def __init__(self) -> None:
        self.seed_calls = 0
        self.resend_calls = 0

    async def seed_from_snapshot(self, snapshots) -> int:
        self.seed_calls += 1
        return 0

    async def resend_all(self) -> int:
        self.resend_calls += 1
        return 0


async def test_attach_runs_the_whole_startup_sequence():
    """`attach` ist die Sequenz, die bis dahin nur in `cli.serve()` stand.

    Sie hier zu buendeln ist der Kern dieser Aufgabe: Start und
    Wiederaufbau muessen dasselbe tun, sonst driften sie auseinander - und
    das faellt erst auf, wenn nach einem Reconnect etwas fehlt, das beim
    Start selbstverstaendlich war.
    """
    client, store, runtime = FakeClient(), FakeStore(), FakeRuntime()

    gained = await attach(client, store, runtime)

    assert client.subscribe_calls == 1
    assert runtime.seed_calls == 1
    assert store.backfill_types_calls == 1
    assert store.backfill_commands_calls == 1
    # Der eigentliche Gewinn: Loxone bekommt den VOLLSTAENDIGEN Zustand
    # zurueck, statt auf den Werten sitzenzubleiben, die beim Abriss
    # zufaellig die letzten waren.
    assert runtime.resend_calls == 1
    assert gained == 3


async def test_supervise_rebuilds_after_a_link_loss():
    client, store, runtime = FakeClient(), FakeStore(), FakeRuntime()
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    task = asyncio.ensure_future(supervise(client, store, runtime, sleep=fake_sleep))
    await asyncio.sleep(0)
    client.drop_link()
    for _ in range(10):
        await asyncio.sleep(0)

    assert client.connect_calls == 1
    assert runtime.resend_calls == 1
    assert slept == []

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_supervise_backs_off_and_keeps_trying():
    """Zwei Fehlschlaege, dann traegt es - die Wartezeiten verdoppeln sich.

    Gemessen wird ueber eine eingesetzte `sleep`-Funktion, nicht real
    abgewartet: ein Test, der acht Sekunden schlaeft, wird beim naechsten
    Umbau als "langsam" uebersprungen und prueft dann gar nichts mehr.
    """
    client = FakeClient(connect_failures=2)
    store, runtime = FakeStore(), FakeRuntime()
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    task = asyncio.ensure_future(supervise(client, store, runtime, sleep=fake_sleep))
    await asyncio.sleep(0)
    client.drop_link()
    for _ in range(20):
        await asyncio.sleep(0)

    assert client.connect_calls == 3
    assert slept == [1.0, 2.0]
    assert runtime.resend_calls == 1

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_supervise_caps_the_backoff():
    client = FakeClient(connect_failures=12)
    store, runtime = FakeStore(), FakeRuntime()
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    task = asyncio.ensure_future(
        supervise(client, store, runtime, sleep=fake_sleep, backoff_max=8.0)
    )
    await asyncio.sleep(0)
    client.drop_link()
    for _ in range(60):
        await asyncio.sleep(0)

    # 1, 2, 4, 8, dann gedeckelt - und es gibt KEINE Aufgabegrenze:
    # matter-server kann beliebig lange weg sein, und niemand steht daneben.
    assert slept[:5] == [1.0, 2.0, 4.0, 8.0, 8.0]
    assert all(s <= 8.0 for s in slept)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
