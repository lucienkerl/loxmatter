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
import logging

import pytest

from loxmatter.sources.supervisor import attach, supervise
from loxmatter.zigbee.source import ZigbeeUnavailableError


class FakeClient:
    """Stands in for a `DeviceSource`, as far as the supervisor uses it."""

    def __init__(
        self,
        connect_failures: int = 0,
        technology: str = "matter",
        *,
        connected: bool = True,
        errors: list[Exception] | None = None,
    ) -> None:
        self._connect_failures = connect_failures
        self.technology = technology
        self.connect_calls = 0
        self.subscribe_calls = 0
        # `True` is a Matter client `cli._run` connected before starting the
        # supervisor; `False` is a Zigbee source, whose first connect the
        # supervisor performs itself.
        self.connected = connected
        # What each failed attempt raises, in order; the rest raise the
        # default below.
        self._errors = list(errors or [])
        self._link_lost = asyncio.Event()

    async def wait_for_link_loss(self) -> None:
        if not self.connected:
            return
        await self._link_lost.wait()
        self._link_lost.clear()
        self.connected = False

    def drop_link(self) -> None:
        self._link_lost.set()

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_calls <= self._connect_failures:
            if self._errors:
                raise self._errors.pop(0)
            raise ConnectionError("matter-server not reachable")
        self.connected = True

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


async def _run_until(client: FakeClient, rounds: int, *, drop: bool) -> asyncio.Task[None]:
    async def fake_sleep(seconds: float) -> None:
        return None

    task = asyncio.ensure_future(supervise(client, FakeStore(), FakeRuntime(), sleep=fake_sleep))
    await asyncio.sleep(0)
    if drop:
        client.drop_link()
    for _ in range(rounds):
        await asyncio.sleep(0)
    return task


async def _stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_first_connect_is_not_logged_as_a_lost_connection(caplog):
    """A Zigbee source is never connected when its supervisor starts - the
    supervisor performs the first connect itself - so `wait_for_link_loss()`
    returns at once. That used to log "connection of source zigbee lost -
    rebuilding it" at WARNING on every start of every installation with a
    stick, before anything had failed, and now that the bridge's log
    reaches `docker logs` it would be the first thing a user reads there.

    A source that WAS connected and lost its link still warns.

    Fault to prove it: log the WARNING regardless of `source.connected`."""
    caplog.set_level(logging.INFO, logger="loxmatter.sources.supervisor")
    fresh = FakeClient(technology="zigbee", connected=False)
    task = await _run_until(fresh, 10, drop=False)
    await _stop(task)

    assert fresh.connect_calls == 1
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("zigbee is not connected yet" in r.getMessage() for r in caplog.records)

    caplog.clear()
    matter = FakeClient()
    task = await _run_until(matter, 10, drop=True)
    await _stop(task)

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == ["connection of source matter lost - rebuilding it"]


async def test_a_failure_that_repeats_carries_its_traceback_once(caplog):
    """A stick that stays unplugged fails the same way every 60 s. With a
    traceback on every attempt, the System tab's 500-line ring was full of
    the same one within hours, and the lines the hardware checklist looks
    for - the warm-up time, the firmware - were gone.

    Each attempt still gets its line, with the attempt count; the traceback
    comes with the first occurrence of each DIFFERENT failure, so a stick
    that goes from missing to busy explains itself again.

    Fault to prove it: log `exc_info` on every attempt (five tracebacks), or
    only on the very first attempt of the outage (the busy stick below gets
    none)."""
    caplog.set_level(logging.INFO, logger="loxmatter.sources.supervisor")
    missing = [FileNotFoundError(2, "No such file or directory") for _ in range(3)]
    busy = [OSError(16, "Device or resource busy") for _ in range(2)]
    client = FakeClient(
        connect_failures=5, technology="zigbee", connected=False, errors=missing + busy
    )

    task = await _run_until(client, 40, drop=False)
    await _stop(task)

    assert client.connect_calls == 6
    failures = [r for r in caplog.records if "rebuild of source zigbee failed" in r.getMessage()]
    assert len(failures) == 5
    with_traceback = [r for r in failures if r.exc_info]
    assert [type(r.exc_info[1]).__name__ for r in with_traceback] == [
        "FileNotFoundError",
        "OSError",
    ]
    assert "attempt 3" in failures[2].getMessage()


async def test_two_different_causes_of_the_same_wrapper_type_both_explain_themselves(caplog):
    """N-3 (verification 2026-09-13): the test above only varies the
    exception TYPE (`FileNotFoundError`, then `OSError`) - which is not what
    production ever raises here. Every real failure of this loop is a
    `ZigbeeUnavailableError` (or `MatterUnavailableError`), the SAME
    wrapper type every time, `raise`d `from` whatever actually went wrong
    (`zigbee/source.py`). `_failure_identity` was written to tell such
    failures apart by MESSAGE, not only by type - "missing" and "busy" both
    surface as a `ZigbeeUnavailableError`, and folding the second into "the
    same failure, attempt 2" because the type matches would have been
    exactly the silent-repeat bug this whole module exists to avoid, just
    one level further down than the type check catches.

    Fault to prove it: key `explained` by `type(exc).__name__` alone (drop
    the message half of `_failure_identity`) - the busy stick's failure
    then logs as a bare repeat of the missing stick's, with no traceback of
    its own."""
    caplog.set_level(logging.INFO, logger="loxmatter.sources.supervisor")
    missing = ZigbeeUnavailableError("could not open the stick: missing")
    missing.__cause__ = FileNotFoundError(2, "No such file or directory")
    busy = ZigbeeUnavailableError("could not open the stick: busy")
    busy.__cause__ = OSError(16, "Device or resource busy")
    client = FakeClient(
        connect_failures=2, technology="zigbee", connected=False, errors=[missing, busy]
    )

    task = await _run_until(client, 20, drop=False)
    await _stop(task)

    assert client.connect_calls == 3
    failures = [r for r in caplog.records if "rebuild of source zigbee failed" in r.getMessage()]
    assert len(failures) == 2
    with_traceback = [r for r in failures if r.exc_info]
    # Both, not one: same wrapper type, different message, so each is its
    # own failure with its own traceback - never "attempt 2" of the first.
    assert [type(r.exc_info[1]).__name__ for r in with_traceback] == [
        "ZigbeeUnavailableError",
        "ZigbeeUnavailableError",
    ]
    # "failed again, attempt N" is the repeat branch's own phrasing
    # (`explained` already had this identity) - neither may use it, since
    # each cause is new to `explained` when it first occurs.
    assert "failed again" not in failures[0].getMessage()
    assert "failed again" not in failures[1].getMessage()
