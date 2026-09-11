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

"""The supervisor that rebuilds the connection to a device source."""

from __future__ import annotations

import asyncio

import pytest

from loxmatter.sources.supervisor import attach, supervise


class FakeClient:
    """Stands in for a `DeviceSource`, as far as the supervisor uses it."""

    def __init__(self, connect_failures: int = 0, technology: str = "matter") -> None:
        self._connect_failures = connect_failures
        self.technology = technology
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
            raise ConnectionError("matter-server not reachable")

    async def subscribe(self, resolve_device_id, handler) -> None:
        self.subscribe_calls += 1
        self.resolver = resolve_device_id

    async def snapshots(self) -> list[object]:
        return []


class FakeStore:
    def __init__(self) -> None:
        self.backfill_types_calls = 0
        self.backfill_commands_calls = 0
        self.backfill_features_calls = 0
        self.lookups: list[tuple[str, str]] = []

    def device_id_for(self, technology: str, address: str) -> int | None:
        self.lookups.append((technology, address))
        return None

    def backfill_device_types(self, snapshots) -> int:
        self.backfill_types_calls += 1
        return 0

    def backfill_network_features(self, snapshots) -> int:
        self.backfill_features_calls += 1
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
    """`attach` is the sequence that until then lived only in `cli.serve()`.

    Bundling it here is not tidying-up work (see `attach`'s own docstring):
    startup and rebuild must do the same thing, otherwise they drift apart
    - and that only shows up once something is missing after a reconnect
    that was taken for granted at startup.
    """
    client, store, runtime = FakeClient(), FakeStore(), FakeRuntime()

    gained = await attach(client, store, runtime)

    assert client.subscribe_calls == 1
    assert runtime.seed_calls == 1
    assert store.backfill_types_calls == 1
    assert store.backfill_commands_calls == 1
    # The actual gain: Loxone gets the COMPLETE state back, instead of
    # being stuck on whichever values happened to be the last ones before
    # the link dropped.
    assert runtime.resend_calls == 1
    assert gained == 3


async def test_attach_resolves_addresses_in_the_sources_own_technology():
    """A non-Matter source, so a resolver bound to "matter" cannot pass.
    Fault to prove it: bind `partial(store.device_id_for, "matter")` in
    `attach` instead of `source.technology`."""
    source = FakeClient(technology="zigbee")
    store = FakeStore()

    await attach(source, store, FakeRuntime())
    source.resolver("00:12:4b:00:1c:a1:b2:c3")

    assert store.lookups == [("zigbee", "00:12:4b:00:1c:a1:b2:c3")]


async def test_attach_backfills_network_features():
    """Fault to prove it: remove the `store.backfill_network_features(...)`
    line from `attach`."""
    store = FakeStore()
    await attach(FakeClient(), store, FakeRuntime())
    assert store.backfill_features_calls == 1


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
    """Two failures, then it holds - the waiting times double.

    Measured through an injected `sleep` function rather than actually sat
    out: a test that sleeps for eight seconds gets skipped as "slow" at the
    next rework and then checks nothing at all.
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

    # 1, 2, 4, 8, then capped - and there is NO give-up limit:
    # matter-server can be gone for arbitrarily long, and nobody is standing by.
    assert slept[:5] == [1.0, 2.0, 4.0, 8.0, 8.0]
    assert all(s <= 8.0 for s in slept)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
