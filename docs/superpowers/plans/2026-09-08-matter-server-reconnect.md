# Reconnecting to matter-server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** loxmatter notices the loss of the websocket connection to matter-server, rebuilds it with backoff, and tells the truth about it for as long as it is missing.

**Architecture:** The listener task that `connect()` starts is already the signal for the breakdown — today it is simply thrown away. Task 1 picks it up, task 2 builds on it a supervisor that runs the existing startup sequence again. Task 3 and 4 make the state visible from the outside: to Loxone through the falling-silent heartbeat, to the UI through a "last heard" timestamp.

**Tech stack:** Python 3.12, `asyncio`, `pytest` (`asyncio_mode=auto`), `ruff`, `mypy --strict`.

**Design:** [docs/superpowers/specs/2026-09-08-matter-server-reconnect-design.md](../specs/2026-09-08-matter-server-reconnect-design.md)

## Global constraints

- All commands run from the worktree root directory, never with `cd` into the main checkout. Never `git stash` — the stash stack is shared with other sessions.
- Project language in comments and docstrings is English.
- Line length 100 (`[tool.ruff] line-length = 100`).
- The four checks: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`. The full suite takes around **four minutes** — that looks like a hang, it isn't, don't abort.
- Starting state of the suite on this branch: **1419 passed, 2 skipped.** Each task states by how much that number must rise.
- Timestamps come exclusively from `loxmatter.timestamps.now_iso()` — the codebase has exactly one helper for this, and it stays the only one.
- `mypy` runs with `strict = true` over `src` and `scripts`. In task 3 that is a load-bearing part of the safeguard, not a formality.

## Deviation from the design, deliberate

The design demands, in section 5 and in Test 3, a test that checks the **hand-off** of `link_ok` out of `cli.serve()` — so that it does not accidentally read `client.connected` (a bool evaluated once) instead of `lambda: client.connected`.

This plan solves it differently, and better: `link_ok` gets the annotation `Callable[[], bool]`. A `bool` at this point is thereby a **type error** that `mypy --strict` rejects in CI — the mistake cannot be committed in the first place. A test would have checked the same thing more weakly, since it would have to start `cli.serve()` or rebuild its setup.

What the design wants in substance, task 3 still checks: a `link_ok` that first returns `True` and then `False` must actually stop the pulse. That closes the "evaluated once" gap on the behavioural side.

## Files

| File | Role | Task |
| --- | --- | --- |
| `src/loxmatter/matter/client.py` | `connected` honest, `wait_for_link_loss()` new | 1 |
| `src/loxmatter/matter/supervisor.py` | **new** — `attach()` and `supervise()` | 2 |
| `src/loxmatter/cli.py` | uses `attach()`, starts `supervise()`, passes `link_ok` | 2, 3 |
| `src/loxmatter/loxone/runtime.py` | `link_ok`, `_last_heard`, `last_heard_for()` | 3, 4 |
| `src/loxmatter/api/models.py` | `DeviceOut.last_heard` | 4 |
| `src/loxmatter/api/devices.py` | fills `last_heard` | 4 |
| `tests/matter/test_client.py` | the breakdown is noticed | 1 |
| `tests/matter/test_supervisor.py` | **new** — rebuild with backoff | 2 |
| `tests/loxone/test_runtime.py` | heartbeat falls silent, "last heard" | 3, 4 |

---

### Task 1: The client notices the breakdown

**Files:**
- Modify: `src/loxmatter/matter/client.py` (property `connected`, line 382–392)
- Modify: `tests/matter/test_client.py` (append)

**Interfaces:**
- Uses: `self._listener_task` (already present, set in `connect()` line 332, cleared in `disconnect()` line 349).
- Provides: `BridgeMatterClient.connected -> bool` (meaning changes) and new `async BridgeMatterClient.wait_for_link_loss() -> None`. Task 2 and 3 build on this.

- [ ] **Step 1: Write the failing tests**

Append to `tests/matter/test_client.py`. `FakeUpstream` (line 51) already takes `fail_connect`; needed is a listener that dies **after** readiness — there isn't one yet, so a dedicated double:

```python
class DyingUpstream(FakeUpstream):
    """A listener that signals readiness and then dies.

    Exactly the case from the operational outage of 8 September 2026: the
    connection is up, `connect()` has long since returned, and then the
    websocket drops. `FakeUpstream(fail_connect=True)` does NOT model this -
    that one fails before readiness and is cleaned up by `_start_listener`
    before a client even comes into existence.
    """

    async def start_listening(self, init_ready=None) -> None:
        self.start_listening_calls += 1
        self._nodes = self._configured_nodes
        if init_ready is not None:
            init_ready.set()
        # Yield several times so that `connect()` can take the task over
        # before it ends - a single `asyncio.sleep(0)` empirically does NOT
        # suffice: `_start_listener`'s `asyncio.wait(..., FIRST_COMPLETED)`
        # only notices the readiness signal two event-loop rounds later, and
        # only then does it check whether this task is already done. With
        # fewer rounds the task is already finished by the time
        # `_start_listener` returns it, and the test would be checking a
        # different case.
        # The coupling to `_start_listener` is fail-loud: should the three no
        # longer suffice after a rework, `assert bridge.connected is True` in
        # the first test below fails immediately - it cannot go hollow on us.
        for _ in range(3):
            await asyncio.sleep(0)
        raise ConnectionResetError("websocket gone")


async def test_connected_becomes_false_when_the_listener_dies():
    """`connected` so far only said whether `connect()` had run.

    The old condition was `self._upstream is not None` - set in `connect()`,
    cleared solely by `disconnect()`. When the websocket died, it stayed
    put, and the `matter-server` diagnostics item reported "Connected" while
    no value arrived any more and every /cmd failed with 502.
    """
    upstream = DyingUpstream()
    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=lambda: FakeSession(),
    )
    await bridge.connect()
    assert bridge.connected is True

    # Give the listener task a chance to actually die.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert bridge.connected is False


async def test_wait_for_link_loss_returns_when_the_listener_dies():
    upstream = DyingUpstream()
    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=lambda: FakeSession(),
    )
    await bridge.connect()

    # Returns instead of hanging - and does NOT re-raise the listener's
    # exception: the caller wants to know THAT the connection is gone.
    await asyncio.wait_for(bridge.wait_for_link_loss(), timeout=1.0)


async def test_wait_for_link_loss_returns_immediately_without_a_listener():
    """A client that never connected must not hang here."""
    bridge, _upstream = make_connected_pair()
    await asyncio.wait_for(bridge.wait_for_link_loss(), timeout=1.0)
```

- [ ] **Step 2: Check that the tests fail**

```bash
uv run pytest tests/matter/test_client.py -k "listener_dies or without_a_listener" -v
```

Expected: **3 failed.** The first two with `AttributeError: 'BridgeMatterClient' object has no attribute 'wait_for_link_loss'` resp. `assert True is False`; the third likewise with `AttributeError`.

- [ ] **Step 3: Make `connected` honest**

In `src/loxmatter/matter/client.py`, replace the body of the property `connected` (line 382–392). The existing docstring describes the old condition and is replaced along with it:

```python
    @property
    def connected(self) -> bool:
        """Whether the connection to matter-server currently HOLDS.

        This used to be `self._upstream is not None` - that is, the answer
        to "has anyone called connect()?", not to "is the connection up?".
        The field is set once in `connect()` and cleared exclusively by
        `disconnect()`; when the websocket died, it stayed put. On
        8 September 2026 exactly that made an outage invisible:
        `GET /api/diagnostics/system` reported "Connected" while no device
        value arrived any more and every Loxone command failed with 502
        (see the design of 2026-09-08, section 1.3).

        That is why the listener task now counts as well: as long as it
        runs, this client receives push updates; once it has ended, the
        connection is gone, no matter what `_upstream` still holds.

        Unlike before, this is therefore NOT the same condition as in
        `_require_upstream` any more. That is deliberate: a call against a
        dead upstream should still fail at the point where it happens, and
        not already here.
        """
        return (
            self._upstream is not None
            and self._listener_task is not None
            and not self._listener_task.done()
        )
```

- [ ] **Step 4: Add `wait_for_link_loss()`**

Insert directly after the property `connected`:

```python
    async def wait_for_link_loss(self) -> None:
        """Returns as soon as the listener ends - for whatever reason.

        The signal for a lost connection already exists: the task from
        `upstream.start_listening()`. Until 8 September 2026 it was simply
        never collected anywhere - no `add_done_callback`, no supervision -
        so its exception seeped away silently and nobody noticed that the
        bridge had gone deaf.

        `asyncio.wait` instead of `await task`: an `await` on a task
        PROPAGATES the waiter's cancellation to the task. If the supervisor
        (see `matter/supervisor.py`) is cancelled during shutdown, it would
        tear the listener down with it - and `disconnect()` would find it
        already cancelled. `asyncio.wait` does not touch the tasks handed
        to it.

        The listener's exception is collected and logged, not re-raised:
        the caller wants to know THAT the connection is gone, and should
        not have to distinguish between reasons for the breakdown itself.
        Without this `exception()`, Python would additionally write "Task
        exception was never retrieved" into the log when the task is
        cleaned up.
        """
        task = self._listener_task
        if task is None:
            return
        await asyncio.wait({task})
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("connection to matter-server lost: %s", exc)
        else:
            logger.warning("listener from matter-server ended without an error")
```

- [ ] **Step 5: Check that the tests pass**

```bash
uv run pytest tests/matter/test_client.py -v
```

Expected: all tests in the file green, including the three new ones.

- [ ] **Step 6: Prove that the first test really bites**

Reset `connected` on a trial basis to the old condition — only the return, leave the docstring standing:

```python
        return self._upstream is not None
```

```bash
uv run pytest tests/matter/test_client.py -k "connected_becomes_false" -v
```

Expected: **1 failed** with `assert True is False`. Then revert and repeat step 5.

- [ ] **Step 7: Verification runs and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Expected: all four green, **1422 passed, 2 skipped** (three new tests).

```bash
git add src/loxmatter/matter/client.py tests/matter/test_client.py
git commit -m "$(cat <<'EOF'
feat(matter): notice when the link to matter-server drops

`connected` used to answer "has connect() run?", not "is the connection
up?" - when the websocket died it stayed true, and on 8 September 2026
that made an outage invisible: diagnostics reported "Connected" while no
device value arrived and every command failed with 502. It now also
requires the listener task to still be running.

`wait_for_link_loss()` collects the listener task that was previously
never awaited anywhere, so its exception no longer seeps away silently.
It uses `asyncio.wait` rather than `await task`, which would propagate
the waiter's cancellation into the listener during shutdown.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The reconnection

**Files:**
- Create: `src/loxmatter/matter/supervisor.py`
- Modify: `src/loxmatter/cli.py` (startup sequence in `serve()`, line 600–623)
- Create: `tests/matter/test_supervisor.py`

**Interfaces:**
- Uses from task 1: `await client.wait_for_link_loss() -> None`.
- Provides: `async attach(client, store, runtime) -> int` (number of backfilled commands) and `async supervise(client, store, runtime, *, sleep=asyncio.sleep, backoff_start=1.0, backoff_max=60.0) -> None`.

- [ ] **Step 1: Write the failing test**

New file `tests/matter/test_supervisor.py`:

```python
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

"""The supervisor that rebuilds the connection to matter-server."""

from __future__ import annotations

import asyncio

import pytest

from loxmatter.matter.supervisor import attach, supervise


class FakeClient:
    """Stands in for `BridgeMatterClient`, as far as the supervisor uses it."""

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
            raise ConnectionError("matter-server not reachable")

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
    """`attach` is the sequence that until then lived only in `cli.serve()`.

    Bundling it here is the core of this task: startup and rebuild must do
    the same thing, otherwise they drift apart - and that only shows up
    once something is missing after a reconnect that was taken for granted
    at startup.
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
```

- [ ] **Step 2: Check that the tests fail**

```bash
uv run pytest tests/matter/test_supervisor.py -v
```

Expected: collection error `ModuleNotFoundError: No module named 'loxmatter.matter.supervisor'`.

- [ ] **Step 3: Write the module**

New file `src/loxmatter/matter/supervisor.py`:

```python
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

"""Keeps the connection to matter-server alive.

On 8 September 2026 the websocket to matter-server died, and loxmatter did
not reconnect - it did not even notice. From the outside everything looked
healthy: the web UI answered, the heartbeat kept pulsing, diagnostics
reported "Connected". In fact no device value arrived any more, and every
Loxone command failed with 502. The only way out was a restart of the
container, and the same thing happened a second time that same evening.

This module deliberately does NOT live in the client: rebuilding the
connection needs `Store` and `Runtime`, and a client that knows half the
application would be the worse boundary. The client only reports that the
connection is gone (`wait_for_link_loss`); what has to happen afterwards is
this layer's knowledge.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from loxmatter.loxone.runtime import Runtime
from loxmatter.matter.client import BridgeMatterClient
from loxmatter.model.store import Store

logger = logging.getLogger(__name__)

BACKOFF_START_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 60.0


async def attach(client: BridgeMatterClient, store: Store, runtime: Runtime) -> int:
    """Binds an existing connection to the store and the runtime.

    Exactly the sequence that, until 8 September 2026, lived only in
    `cli.serve()`. Bundling it here is not tidying-up work: startup and
    reconnect MUST do the same thing, otherwise they drift apart - and that
    only shows up once something is missing after a reconnect that was
    taken for granted at startup.

    `resend_all()` at the end is the actual gain. The existing guarantee
    "a restart of the bridge behaves like /resync" (spec 6.4) thereby
    applies to a reconnect as well: Loxone gets the complete state back,
    instead of being stuck on whichever values happened to be the last ones
    before the link dropped.

    Returns the number of backfilled commands - `cli.serve()` reports them
    on the console at startup, the supervisor logs them. A `typer.echo`
    does not belong here: this module also runs when nobody is looking at a
    terminal.

    `runtime.start()` DELIBERATELY does not belong here. It starts the
    heartbeat and resend loops, and those are meant to outlast an outage,
    not to begin anew with it.
    """
    await client.subscribe(store.device_id_for_node, runtime)
    snapshots = await client.snapshots()
    await runtime.seed_from_snapshot(snapshots)
    store.backfill_device_types(snapshots)
    gained: int = store.backfill_commands(snapshots)
    await runtime.resend_all()
    return gained


async def supervise(
    client: BridgeMatterClient,
    store: Store,
    runtime: Runtime,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    backoff_start: float = BACKOFF_START_SECONDS,
    backoff_max: float = BACKOFF_MAX_SECONDS,
) -> None:
    """Rebuilds the connection after every breakdown - endlessly.

    **Without a give-up limit**, and that is a decision: a bridge that
    gives up after ten attempts is worse than one that keeps knocking every
    sixty seconds. matter-server can be gone for arbitrarily long - an
    update, a restart of the host, a container that only comes up after the
    network - and nobody is standing by to help along by hand.

    `sleep` is injectable so that the tests can MEASURE the waiting times
    instead of sitting them out. A test that sleeps for eight seconds gets
    skipped as "slow" at the next rework and then checks nothing at all.
    """
    while True:
        try:
            await client.wait_for_link_loss()
            logger.warning("connection to matter-server lost - rebuilding it")
        except asyncio.CancelledError:
            raise
        except Exception:
            # A module built against silent failure must not fail silently
            # itself: without this `except`, `supervise()` would end here
            # without comment, nobody would restart it, and the bridge would
            # be deaf again without reporting it - exactly the state of
            # 8 September 2026.
            #
            # It waits `backoff_max`, NOT `backoff_start`: an error at this
            # point is not a connection problem (the inner loop below catches
            # those), but most likely a programming error. Repeating it in a
            # tight loop does not fix it, it only fills the log and buries
            # everything next to it.
            logger.exception("supervisor: unexpected error while waiting for the breakdown")
            await sleep(backoff_max)
            continue
        delay = backoff_start
        while True:
            try:
                await client.connect()
                gained = await attach(client, store, runtime)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Every cause should trigger the same backoff: an unreachable
                # matter-server is just as much a reason to try again as a state
                # error during the rebuild. A narrower exception would let one of
                # these cases slip through unnoticed (ruff no longer flags the broad
                # `except Exception` as blind thanks to the `exc_info=exc` below - a
                # `noqa: BLE001` would be unused here and would make `ruff check`
                # fail).
                # The traceback MUST go into the log: otherwise a state error in
                # `attach()` repeats every few seconds as the same line without a
                # cause - and on 8 September 2026 that was exactly the reason why
                # nobody found the actual cause of the outage.
                logger.warning(
                    "rebuild failed (%s) - next attempt in %.0f s",
                    exc,
                    delay,
                    exc_info=exc,
                )
                await sleep(delay)
                delay = min(delay * 2, backoff_max)
            else:
                logger.info(
                    "connection to matter-server restored (%d commands backfilled)",
                    gained,
                )
                break
```

- [ ] **Step 4: Check that the tests pass**

```bash
uv run pytest tests/matter/test_supervisor.py -v
```

Expected: **5 passed.**

- [ ] **Step 5: Switch `cli.serve()` over to `attach`**

In `src/loxmatter/cli.py`, replace the block from `await client.subscribe(...)` through `await runtime.resend_all()` (line 600–623).

**In the process, `await runtime.start()` moves forward**, and that is a deliberate behavioural change, not a reordering for convenience. Today it stands **between** `client.subscribe(...)` and `client.snapshots()`. Since `attach()` encloses exactly that span, it cannot stay there — it would have to sit in the middle of `attach()` and would be called again on every rebuild, which would restart the heartbeat and resend loops each time.

The new position **before** `attach()` is unproblematic: `runtime.start()` starts two background loops and waits for nothing. The heartbeat loop is allowed to run before the first value — the connection already stands at this point (`client.connect()` ran before it), and from task 3 onward that is exactly the condition that lets it pulse. The resend loop only sends signals that are marked, and simply finds nothing until the first `seed_from_snapshot`.

Were it to move **behind** `attach()` instead, `resend_all()` would have run before the loops stand — that is harmless, but the earlier position keeps the "bridge is alive" state up from the earliest possible moment, which is exactly the point of this task.

Import addition at the top of `cli.py`:

```python
from loxmatter.matter.supervisor import attach, supervise
```

The startup sequence itself then reads, immediately after `await client.connect()` has succeeded:

```python
        await runtime.start()
        # Since 8 September 2026 the startup sequence and the rebuild share
        # one place (`matter.supervisor.attach`) - see there for why.
        gained = await attach(client, store, runtime)
        if gained:
            typer.echo(i18n.t("cli.run.echo_commands_backfilled", count=gained))
        # The supervisor runs for as long as the service runs: if the
        # websocket to matter-server dies, it rebuilds the connection and
        # lets `attach` run again. Without it the bridge stays mute after a
        # restart of matter-server, without reporting it - exactly the
        # outage of 8 September 2026.
        supervisor_task = asyncio.ensure_future(supervise(client, store, runtime))
```

`supervisor_task` needs to be pre-set to `supervisor_task: asyncio.Task[None] | None = None` before the surrounding `try`, and checked against `None` in `finally` — otherwise the teardown fails when `serve()` already fails before the supervisor is started:

```python
    supervisor_task: asyncio.Task[None] | None = None
    try:
        ...
    finally:
        if supervisor_task is not None:
            supervisor_task.cancel()
            try:
                await supervisor_task
            except asyncio.CancelledError:
                # Two different cancellations arrive here as the same exception,
                # and only one of them is the expected one.
                # `supervisor_task.cancelled()` tells them apart: if the
                # supervisor itself was cancelled, it was our `cancel()` one line
                # above - exactly what we expected, nothing to report. If it was
                # NOT, then the cancellation hit the surrounding `_run` task while
                # we were waiting for it (a second Ctrl-C in the middle of the
                # shutdown), and that one MUST keep travelling - the docstring
                # above says the same for every other cleanup step.
                if not supervisor_task.cancelled():
                    raise
            except Exception:
                # Its own `try` like every neighbouring block: if the supervisor
                # ended earlier on some other exception, `await` delivers it here -
                # and without this `except` the whole rest of the cleanup would be
                # skipped, `store.close()` included.
                logger.exception("Supervisor of the matter-server connection ended with an error")
        try:
            await runtime.stop()
        ...
```

This own `try`/`except` around `supervisor_task`, ahead of `runtime.stop()`, matters for the same reason as every other cleanup step in this `finally`: a failure in one step must not skip the ones after it — `sender.close()`, `client.disconnect()` and `store.close()` included.

- [ ] **Step 6: Verification runs and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Expected: all four green, **1426 passed, 2 skipped** (four new tests).

If an existing CLI test fails, that is a finding about the switch-over in step 5 and **not** a reason to adjust the test — report it.

```bash
git add src/loxmatter/matter/supervisor.py src/loxmatter/cli.py tests/matter/test_supervisor.py
git commit -m "$(cat <<'EOF'
feat(matter): rebuild the matter-server connection after a breakdown

New `matter/supervisor.py`. `attach()` bundles the startup sequence that
lived only in `cli.serve()` so that startup and reconnect cannot drift
apart, and `supervise()` runs it again after every link loss, with an
exponential backoff capped at sixty seconds and no give-up limit.

`cli._run()` now starts the supervisor as a task and tears it down in its
own `try`/`except` in the `finally`, so a failure there cannot skip
`runtime.stop()`, `sender.close()`, `client.disconnect()` and
`store.close()`. `runtime.start()` moved ahead of `attach()`: the
heartbeat and resend loops are meant to outlast an outage.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: The heartbeat falls silent

**Files:**
- Modify: `src/loxmatter/loxone/runtime.py` (`__init__` line 70–77, `_heartbeat_loop` line 511–534)
- Modify: `src/loxmatter/cli.py` (construction of `Runtime`, line 586)
- Modify: `tests/loxone/test_runtime.py` (append)

**Interfaces:**
- Uses from task 1: `BridgeMatterClient.connected -> bool`.
- Provides: `Runtime(..., link_ok: Callable[[], bool] = lambda: True)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/loxone/test_runtime.py`:

```python
async def test_the_heartbeat_stays_silent_without_a_matter_link(tmp_path):
    """Without a Matter connection `bridge_alive` must NOT pulse.

    The heartbeat is the watchdog input in Loxone. If it keeps pulsing
    while the bridge is deaf, Loxone reports "all well" - that is exactly
    what happened on 8 September 2026, and it is why the outage went
    unnoticed by everyone for hours.
    """
    store = Store(tmp_path / "s.sqlite")
    sender = FakeSender()
    runtime = Runtime(store, sender, heartbeat_seconds=0.01, link_ok=lambda: False)
    await runtime.start()
    await asyncio.sleep(0.05)
    await runtime.stop()

    assert HEARTBEAT_KEY not in sender


async def test_the_heartbeat_pulses_with_a_matter_link(tmp_path):
    """Counter-check - otherwise the test above only proves nothing happens."""
    store = Store(tmp_path / "s.sqlite")
    sender = FakeSender()
    runtime = Runtime(store, sender, heartbeat_seconds=0.01, link_ok=lambda: True)
    await runtime.start()
    await asyncio.sleep(0.05)
    await runtime.stop()

    assert HEARTBEAT_KEY in sender


async def test_the_heartbeat_falls_silent_when_the_link_drops(tmp_path):
    """`link_ok` is asked afresh on EVERY beat, not once at construction.

    Were `cli.serve()` to pass `client.connected` instead of
    `lambda: client.connected` by accident - a property, hence a bool
    evaluated once - the heartbeat would hang forever on the state of the
    moment of startup and would never fall silent. `mypy --strict` now
    rejects that as a type error; this test covers the same gap on the
    behavioural side.
    """
    store = Store(tmp_path / "s.sqlite")
    sender = FakeSender()
    alive = [True]
    runtime = Runtime(store, sender, heartbeat_seconds=0.01, link_ok=lambda: alive[0])
    await runtime.start()
    await asyncio.sleep(0.05)
    assert HEARTBEAT_KEY in sender

    alive[0] = False
    before = len([k for k in sender if k == HEARTBEAT_KEY])
    await asyncio.sleep(0.05)
    after = len([k for k in sender if k == HEARTBEAT_KEY])
    await runtime.stop()

    assert after == before
```

`HEARTBEAT_KEY` and `asyncio` need to be imported in the test file for this. Check and add if needed:

```python
import asyncio

from loxmatter.loxone.runtime import HEARTBEAT_KEY, Runtime
```

- [ ] **Step 2: Check that the tests fail**

```bash
uv run pytest tests/loxone/test_runtime.py -k "heartbeat_st or heartbeat_pulses or heartbeat_falls" -v
```

Expected: **3 failed** with `TypeError: Runtime.__init__() got an unexpected keyword argument 'link_ok'`.

- [ ] **Step 3: Add `link_ok` to `Runtime`**

In `src/loxmatter/loxone/runtime.py`, extend the signature (line 70–77) and set the field:

```python
    def __init__(
        self,
        store: Store,
        sender: Sender,
        *,
        heartbeat_seconds: float = 30.0,
        resend_poll_seconds: float = 5.0,
        link_ok: Callable[[], bool] = lambda: True,
    ) -> None:
        self._store = store
        self._sender = sender
        self._heartbeat_seconds = heartbeat_seconds
        self._resend_poll_seconds = resend_poll_seconds
        # Whether the connection to matter-server is currently holding -
        # asked afresh by the heartbeat on EVERY beat (see
        # `_heartbeat_loop`). The annotation `Callable[[], bool]` is a
        # load-bearing safeguard here, not a formality: `cli.serve()`
        # passes `lambda: client.connected`, and `client.connected` on its
        # own - a property, hence a bool evaluated once - would thereby be
        # a type error that `mypy --strict` rejects in CI. Without this
        # annotation the heartbeat would silently hang on the state of the
        # moment of startup and would never fall silent.
        self._link_ok = link_ok
```

- [ ] **Step 4: Adapt `_heartbeat_loop`**

In `_heartbeat_loop` (line 511 ff.), gather the three lines inside the `try` into one condition. The existing comment block above `_notify_observers` stays unchanged:

```python
    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                # No pulse without a Matter connection (8 September 2026):
                # the heartbeat is the watchdog input in Loxone. If it
                # keeps pulsing while the bridge is deaf, Loxone reports
                # "all well" - and that is exactly why an outage lasting
                # hours went unnoticed by everyone.
                #
                # This is something DIFFERENT from the failure case below:
                # if SENDING fails, the loop carries on and keeps pulsing,
                # so that the error becomes visible. If the CONNECTION is
                # missing, it falls silent, so that the error becomes
                # visible. Two states, two right answers - the difference
                # is what the pulse makes a statement about.
                #
                # The toggle of `_heartbeat_on` sits INSIDE the condition
                # on purpose: otherwise the phase would keep advancing
                # during the outage and the first pulse afterwards would
                # come out on the same value as the last one before it by
                # chance - for an edge-triggered watchdog that would be a
                # swallowed beat.
                if self._link_ok():
                    self._heartbeat_on = not self._heartbeat_on
                    await self._sender.send(HEARTBEAT_KEY, self._heartbeat_on, force=True)
                    # Also to the UI (2026-09-03). Previously the heartbeat
                    # went only to Loxone, and a bridge on which nothing is
                    # currently changing - a plug with no load reports neither
                    # current nor power - was indistinguishable in the live
                    # view from a crashed one: no value was moving, and no one
                    # could tell whether nothing was happening or nothing was
                    # arriving. The heartbeat is precisely the signal that
                    # answers this question; withholding it from the UI was a
                    # gap, not a decision.
                    self._notify_observers(HEARTBEAT_KEY, self._heartbeat_on)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Precisely the failure case the heartbeat is meant to
                # report must not silence it - otherwise the Loxone
                # watchdog freezes on the last value while nothing is
                # running any more (review fix, Important #1).
                logger.exception("Heartbeat could not be sent - loop keeps running")
            await asyncio.sleep(self._heartbeat_seconds)
```

- [ ] **Step 5: Wire up `cli.serve()`**

In `src/loxmatter/cli.py`, the construction stands today like this (line 585–587):

```python
    sender = UdpSender(miniserver, port)
    runtime = Runtime(store, sender)
    client = _build_client(url)
```

The order has to reverse, because `Runtime` needs the client:

```python
    sender = UdpSender(miniserver, port)
    client = _build_client(url)
    # `lambda: client.connected`, NOT `client.connected`: the second form
    # would be a bool evaluated once, and the heartbeat would thereby hang
    # forever on the state of the moment of startup. `mypy --strict`
    # rejects it.
    runtime = Runtime(store, sender, link_ok=lambda: client.connected)
```

- [ ] **Step 6: Check that the tests pass**

```bash
uv run pytest tests/loxone/test_runtime.py -v
```

Expected: all tests in the file green, including the three new ones.

- [ ] **Step 7: Prove that mypy really sets the trap**

In `cli.py`, replace `link_ok=lambda: client.connected` on a trial basis with `link_ok=client.connected`:

```bash
uv run mypy
```

Expected: **failure** with a message of the kind `Argument "link_ok" to "Runtime" has incompatible type "bool"; expected "Callable[[], bool]"`. Then revert and run `uv run mypy` again: no findings.

If mypy stays silent here, the safeguard from the deviation note above is ineffective and the finding belongs reported — then it does need a test on the hand-off after all.

- [ ] **Step 8: Verification runs and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Expected: all four green, **1429 passed, 2 skipped** (three new tests).

```bash
git add src/loxmatter/loxone/runtime.py src/loxmatter/cli.py tests/loxone/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(loxone): let the heartbeat fall silent without a Matter link

`bridge_alive` is the watchdog input in Loxone. While it kept pulsing on
8 September 2026 the bridge was deaf, so Loxone reported "all well" and
the outage went unnoticed for hours. `Runtime` now takes a `link_ok`
callback, asked afresh on every beat, and `cli.serve()` passes
`lambda: client.connected`.

The toggle of `_heartbeat_on` sits inside the condition on purpose: an
advancing phase during the outage would make the first pulse afterwards
land on the same value as the last one before it, which an edge-triggered
watchdog would swallow. A failing send still pulses on, as before - a
missing connection and a failing send are two states with two different
right answers.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: "Last heard"

**Files:**
- Modify: `src/loxmatter/loxone/runtime.py` (`__init__`, `on_attribute` line 222, `on_node_snapshot` line 288, `on_event` line 328, new method next to `last_values_for` line 378)
- Modify: `src/loxmatter/api/models.py` (`DeviceOut`, line 74 ff.)
- Modify: `src/loxmatter/api/devices.py` (assembly of `DeviceOut`, line 222 ff.)
- Modify: `tests/loxone/test_runtime.py` (append)

**Interfaces:**
- Uses: `loxmatter.timestamps.now_iso() -> str`.
- Provides: `Runtime.last_heard_for(device_id: int) -> str | None` and `DeviceOut.last_heard: str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/loxone/test_runtime.py`:

```python
async def test_last_heard_is_none_until_something_arrives(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    runtime = Runtime(store, FakeSender())
    assert runtime.last_heard_for(1) is None


async def test_last_heard_is_set_even_for_an_unmapped_path(tmp_path):
    """A path without a signal counts as "heard" too.

    `on_attribute` returns early on an unknown path - but the report
    ARRIVED all the same, and that is exactly what the timestamp is meant
    to say. Were it set only after the mapping, a device whose signals
    nobody has exported would report "never heard" forever.
    """
    store = Store(tmp_path / "s.sqlite")
    runtime = Runtime(store, FakeSender())

    await runtime.on_attribute(1, "1/6/0", True)

    stamp = runtime.last_heard_for(1)
    assert stamp is not None
    assert stamp.startswith("20")


async def test_last_heard_is_set_by_an_event(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    runtime = Runtime(store, FakeSender())

    await runtime.on_event(2, "1/59/1")

    assert runtime.last_heard_for(2) is not None
    # Another device stays untouched - the timestamp is per device.
    assert runtime.last_heard_for(3) is None
```

- [ ] **Step 2: Check that the tests fail**

```bash
uv run pytest tests/loxone/test_runtime.py -k "last_heard" -v
```

Expected: **3 failed** with `AttributeError: 'Runtime' object has no attribute 'last_heard_for'`.

- [ ] **Step 3: Extend `Runtime`**

Import addition at the top of `runtime.py`:

```python
from loxmatter.timestamps import now_iso
```

In `__init__`, after `self._link_ok = link_ok`:

```python
        # When something last arrived from a device at all - one ISO
        # timestamp per device id. IN MEMORY ONLY, not in the database:
        # the same reasoning that the docstring of `StoredDevice` already
        # gives for `online` - reachability is runtime state. A timestamp
        # that survives a restart claims something after startup that
        # nobody has checked; `None`, by contrast, honestly says "nothing
        # heard since this bridge started".
        #
        # The occasion (8 September 2026): a window contact that only
        # sends on change looked in the UI exactly like a button from
        # which nothing had come for five days - both `online: true`.
        # `online` stays what it is; this is the second number that makes
        # the question answerable in the first place.
        self._last_heard: dict[int, str] = {}
```

The three entry-point methods each get the note **as their first line**. `on_attribute` (line 222):

```python
    async def on_attribute(self, device_id: int, path: str, raw: object) -> None:
        self._mark_heard(device_id)
        key = self._cache_attribute(device_id, path, raw)
```

`on_event` (line 328):

```python
    async def on_event(self, device_id: int, path: str) -> None:
        self._mark_heard(device_id)
        signal = self._signal_for(device_id, path, SignalKind.EVENT)
```

`on_node_snapshot` (line 288) — right behind the existing docstring, before the first statement:

```python
        self._mark_heard(device_id)
```

And the two new methods, immediately before `last_values_for` (line 378):

```python
    def _mark_heard(self, device_id: int) -> None:
        """Records that something has just arrived from this device.

        Sits RIGHT AT THE TOP of `on_attribute`/`on_event`/
        `on_node_snapshot`, before any early return: whether a path can be
        mapped to an exported signal is a question of configuration - the
        report arrived either way, and that is all this timestamp states.
        """
        self._last_heard[device_id] = now_iso()

    def last_heard_for(self, device_id: int) -> str | None:
        """When something last arrived from this device, or `None`.

        `None` means "nothing heard since this bridge started" - see
        `_last_heard` in the constructor for why this is not persisted.
        """
        return self._last_heard.get(device_id)
```

- [ ] **Step 4: Check that the tests pass**

```bash
uv run pytest tests/loxone/test_runtime.py -k "last_heard" -v
```

Expected: **3 passed.**

- [ ] **Step 5: Pass the field through to the API**

In `src/loxmatter/api/models.py`, in `DeviceOut` after `online: bool`:

```python
    online: bool
    # When something last arrived from this device at all; `None` if
    # nothing has come since the bridge started. `online` alone does not
    # answer the question that stayed open on 8 September 2026: a device
    # that only sends on change and is currently quiet looks exactly there
    # like one from which nothing has come for days.
    last_heard: str | None
```

In `src/loxmatter/api/devices.py`, where `online` is read today (line 222 ff.), add the line:

```python
    values = runtime.last_values_for(device.id)
    online = bool(values.get(f"d{device.id}_online", False))
    last_heard = runtime.last_heard_for(device.id)
```

and in `return DeviceOut(...)`, after `online=online`:

```python
        last_heard=last_heard,
```

- [ ] **Step 6: Verification runs**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Expected: all four green, **1432 passed, 2 skipped** (three new tests).

If an existing API test fails because `DeviceOut` has one more required field, that is expected: the affected test setups get `last_heard=None` added. That is the only adjustment to existing tests allowed in this task — every other failure belongs reported.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/loxone/runtime.py src/loxmatter/api/models.py src/loxmatter/api/devices.py tests/loxone/test_runtime.py
git commit -m "$(cat <<'EOF'
feat(api): report when a device was last heard from

`online: true` alone left the question open on 8 September 2026: a window
contact that only sends on change and is currently quiet looks exactly
like a button from which nothing has come for five days. `Runtime` now
keeps an ISO timestamp per device and `DeviceOut` carries it as
`last_heard`.

Kept in memory only, for the same reason `online` is - reachability is
runtime state, and a timestamp surviving a restart would claim something
nobody checked. `None` honestly says "nothing heard since this bridge
started". `_mark_heard` sits at the very top of `on_attribute`,
`on_event` and `on_node_snapshot`, before any early return: whether a
path maps to an exported signal is a question of configuration, but the
report arrived either way.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Coverage against the design

| Design | Task |
| --- | --- |
| 3. `connected` checks both | Task 1, Step 3 |
| 3. `wait_for_link_loss()` | Task 1, Step 4 |
| 3. Diagnostics become honest without a change of their own | Task 1, side effect — `_check_matter_server` reads `client.connected` |
| 4. `attach()` | Task 2, Step 3 |
| 4. `supervise()` with backoff, without a give-up limit | Task 2, Step 3; tests in step 1 |
| 4. `runtime.start()` stays outside | Task 2, Step 5 |
| 5. Heartbeat falls silent | Task 3, Steps 3–5 |
| 5. A send failure keeps it pulsing | Task 3, Step 4 — the existing `except` branch stays untouched |
| 5. Hand-off from `serve()` secured | Task 3, Step 7 — via `mypy --strict` instead of a test, see the deviation note above |
| 6. `_last_heard`, `last_heard_for` | Task 4, Step 3 |
| 6. `DeviceOut.last_heard` | Task 4, Step 5 |
| 6. not persisted | Task 4, Step 3 — comment in the constructor |
| 7. Tests 1–4 including the bite-test | Task 1 Step 6, Task 2 Step 1, Task 3 Step 7, Task 4 Step 1 |
| 8. Not part of the design | no task — deliberate |
