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

"""`ZigbeeRuntime` - swapping a live radio out from under a running source.

This is the one object in the tree that removes a device source while the
bridge keeps serving, and the branch's own history says where the bugs in
that shape live: three components in a row got a device or a link
disappearing at the wrong moment wrong. So the tests here are mostly about
ORDER - what is cancelled before what is disconnected, what leaves the
registry before which `await` - rather than about the happy path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.model.zigbee_settings_store import ZigbeeRadioSettings
from loxmatter.sources import SourceNotConfiguredError, Sources
from loxmatter.zigbee import runtime as runtime_module
from loxmatter.zigbee.runtime import ZigbeeRuntime, build_zigbee_source
from loxmatter.zigbee.source import ZigbeeSource

ITEAD = "/dev/serial/by-id/usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2-if00-port0"
MG24 = "/dev/serial/by-id/usb-SONOFF_SONOFF_Dongle_Plus_MG24-if00-port0"


def _settings(path: str | None = ITEAD, **fields: Any) -> ZigbeeRadioSettings:
    base: dict[str, Any] = {
        "path": path,
        "radio_type": "ezsp",
        "baudrate": 115200,
        "flow_control": "software",
        "saved_at": "2026-09-12T10:00:00Z",
    }
    base.update(fields)
    return ZigbeeRadioSettings(**base)


class _FakeSource:
    """A source with `ZigbeeSource`'s lifecycle surface and no zigpy.

    `disconnect()` is deliberately slow (one event-loop turn at least), so
    a test can observe what the registry says WHILE the old stick is being
    released - the window in which a Loxone command must not be handed a
    source that is being shut down."""

    technology = "zigbee"

    def __init__(self, path: str, *, log: list[str] | None = None) -> None:
        self.path = path
        self.connected = False
        self.disconnects = 0
        self._log = log if log is not None else []
        self._progress = runtime_module.ConnectionProgress(
            state="idle", attempts=0, error=None, changed_at="2026-09-12T10:00:00Z"
        )

    def progress(self) -> runtime_module.ConnectionProgress:
        return self._progress

    def set_progress(self, state: str, **fields: Any) -> None:
        self._progress = runtime_module.ConnectionProgress(
            state=state,  # type: ignore[arg-type]
            attempts=fields.get("attempts", 0),
            error=fields.get("error"),
            changed_at="2026-09-12T10:00:01Z",
        )

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self._log.append(f"disconnect:{self.path}")
        await asyncio.sleep(0)
        self.disconnects += 1
        self.connected = False

    async def wait_for_link_loss(self) -> None:
        return None

    async def snapshots(self) -> list[NodeSnapshot]:
        return []

    async def subscribe(self, resolve_device_id: Any, handler: Any) -> None:
        return None

    async def follow(self, address: str, *, seed_even_without_new_paths: bool = False) -> None:
        return None

    async def send(self, call: Any) -> None:
        return None

    async def remove(self, address: str) -> None:
        return None


def _holder(
    store: Store,
    sources: Sources,
    *,
    built: list[ZigbeeRadioSettings] | None = None,
    log: list[str] | None = None,
    supervise: Any = None,
) -> tuple[ZigbeeRuntime, dict[str, Any]]:
    """A `ZigbeeRuntime` whose builder and supervisor are recorded."""
    state: dict[str, Any] = {"built": built if built is not None else [], "supervised": []}

    async def build(settings: ZigbeeRadioSettings) -> Any:
        state["built"].append(settings)
        if settings.path is None:
            return None
        return _FakeSource(settings.path, log=log)

    async def _spy_supervise(source: Any, store_: Any, runtime_: Any) -> None:
        state["supervised"].append(source)
        if log is not None:
            log.append(f"supervise:{source.path}")
        await asyncio.Event().wait()

    holder = ZigbeeRuntime(
        store,
        object(),  # type: ignore[arg-type]
        sources,
        build_source=build,
        supervise=supervise or _spy_supervise,
    )
    return holder, state


class _Matter:
    technology = "matter"
    connected = True


@pytest.fixture
def store(tmp_path: Path):
    store = Store(tmp_path / "t.sqlite")
    yield store
    store.close()


async def test_the_startup_source_comes_from_the_stored_setting(store):
    """The startup half of the holder's job. It reads the STORE, not a CLI
    flag - which is what makes the setting survive a container recreation.

    Fault to prove it: build from a path handed in at construction time."""
    store.zigbee_settings.save(_settings())
    sources = Sources([_Matter()])  # type: ignore[list-item]
    holder, state = _holder(store, sources)

    source = await holder.open()

    assert source is not None and source.path == ITEAD
    assert state["built"][0].path == ITEAD
    assert sources.get("zigbee") is source
    assert holder.current() is source


async def test_no_stored_stick_builds_nothing_and_registers_nothing(store):
    """An installation with no Zigbee radio pays nothing, and `Sources.get`
    keeps raising `SourceNotConfiguredError` - the 503 "not set up in this
    installation", which is true.

    Fault to prove it: register the `None`."""
    sources = Sources([_Matter()])  # type: ignore[list-item]
    holder, _state = _holder(store, sources)

    assert await holder.open() is None
    with pytest.raises(SourceNotConfiguredError):
        sources.get("zigbee")
    assert holder.progress().state == "idle"


async def test_applying_a_new_stick_returns_before_anything_is_built(store):
    """`apply()` is synchronous and schedules - the whole point of the 202.

    Fault to prove it: make `apply()` a coroutine that awaits the swap. The
    builder below has then already run by the time `apply()` returns, and
    the HTTP handler that called it would have waited for the same thing -
    a 9-15 s quirks warm-up on a Pi, plus the radio."""
    sources = Sources([_Matter()])  # type: ignore[list-item]
    holder, state = _holder(store, sources)

    holder.apply(_settings())

    assert state["built"] == [], "the build must not have happened yet"
    await holder.wait_for_apply()
    assert [s.path for s in state["built"]] == [ITEAD]
    assert sources.get("zigbee").path == ITEAD
    assert state["supervised"][0].path == ITEAD


async def test_the_old_supervisor_is_stopped_before_the_old_stick_is_released(store):
    """The order that keeps a radio change from reopening the stick it is
    giving up. `ZigbeeSource.disconnect()` SETS `_link_lost` on purpose, so
    a supervisor still parked in `wait_for_link_loss()` wakes the moment
    the port is released and reconnects it - the user's old stick, held
    open by a second application, and the next apply then finds it busy.

    Fault to prove it: disconnect first and cancel the supervisor
    afterwards. The log below then reads disconnect-before-cancel, and on
    real hardware the supervisor gets its turn inside that `await`."""
    store.zigbee_settings.save(_settings())
    log: list[str] = []
    sources = Sources([_Matter()])  # type: ignore[list-item]
    cancelled: list[str] = []

    async def supervise(source: Any, store_: Any, runtime_: Any) -> None:
        log.append(f"supervise:{source.path}")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            log.append(f"cancel:{source.path}")
            cancelled.append(source.path)
            raise

    holder, _state = _holder(store, sources, log=log, supervise=supervise)
    await holder.open()
    holder.supervise_current()
    await asyncio.sleep(0)

    holder.apply(_settings(path=MG24))
    await holder.wait_for_apply()

    assert cancelled == [ITEAD]
    assert log.index(f"cancel:{ITEAD}") < log.index(f"disconnect:{ITEAD}")
    assert log.index(f"disconnect:{ITEAD}") < log.index(f"supervise:{MG24}")


async def test_clearing_the_setting_disconnects_the_source(store):
    """ "No Zigbee stick" must actually release the port - otherwise the
    stick stays locked by this process and a second loxmatter instance, or
    a deliberate switch to another tool, fails with EBUSY for no visible
    reason.

    Fault to prove it: only clear the stored value."""
    store.zigbee_settings.save(_settings())
    sources = Sources([_Matter()])  # type: ignore[list-item]
    holder, _state = _holder(store, sources)
    source = await holder.open()
    holder.supervise_current()
    await asyncio.sleep(0)

    holder.apply(_settings(path=None))
    await holder.wait_for_apply()

    assert source.disconnects == 1
    assert holder.current() is None
    with pytest.raises(SourceNotConfiguredError):
        sources.get("zigbee")


async def test_the_source_leaves_the_registry_before_the_disconnect_is_awaited(store):
    """A Loxone command arriving mid-swap must get "there is no Zigbee
    radio" - which is true - rather than a source whose application is
    being shut down underneath it.

    The fake's `disconnect()` yields to the event loop, which is exactly
    the window a real `app.shutdown(db=True)` leaves open, for far longer.

    Fault to prove it: `await source.disconnect()` before
    `sources.replace("zigbee", None)`. The lookup inside the window then
    succeeds and hands out the dying source."""
    store.zigbee_settings.save(_settings())
    sources = Sources([_Matter()])  # type: ignore[list-item]
    seen: list[str] = []

    class _Watching(_FakeSource):
        async def disconnect(self) -> None:
            try:
                sources.get("zigbee")
                seen.append("still registered")
            except SourceNotConfiguredError:
                seen.append("already gone")
            await super().disconnect()

    async def build(settings: ZigbeeRadioSettings) -> Any:
        return None if settings.path is None else _Watching(settings.path)

    holder = ZigbeeRuntime(
        store,
        object(),  # type: ignore[arg-type]
        sources,
        build_source=build,
        supervise=_never_ending,
    )
    await holder.open()

    holder.apply(_settings(path=None))
    await holder.wait_for_apply()

    assert seen == ["already gone"]


async def _never_ending(source: Any, store_: Any, runtime_: Any) -> None:
    await asyncio.Event().wait()


async def test_two_applies_in_flight_do_not_overlap(store):
    """Two rapid changes - a double click on Apply, or two tabs - must not
    both tear down and both rebuild: the loser's source would be left
    running with the winner's stick in the registry, a second application
    holding a serial port nobody can reach.

    Fault to prove it: drop the lock in `_apply_in_background`. The build
    of the second apply then starts before the first one's disconnect has
    finished, and the two `disconnect`/`supervise` entries interleave."""
    store.zigbee_settings.save(_settings())
    log: list[str] = []
    sources = Sources([_Matter()])  # type: ignore[list-item]
    holder, _state = _holder(store, sources, log=log)
    await holder.open()
    holder.supervise_current()
    await asyncio.sleep(0)

    holder.apply(_settings(path=MG24))
    holder.apply(_settings(path=ITEAD))
    await holder.wait_for_apply()

    # Whichever ran second is the one in the registry, and each stick was
    # released exactly once: no overlap, no leak.
    assert sources.get("zigbee").path == ITEAD
    assert log == [
        f"supervise:{ITEAD}",
        f"disconnect:{ITEAD}",
        f"supervise:{MG24}",
        f"disconnect:{MG24}",
        f"supervise:{ITEAD}",
    ]


async def test_the_progress_of_a_running_attempt_is_readable(store):
    """What makes the asynchronous answer honest rather than merely fast: a
    PUT that returns immediately and reports nothing afterwards is a job the
    user cannot distinguish from a dead one.

    Fault to prove it: return a bare `connected` boolean. A 15 s warm-up
    then reads as "not connected", identical to a stick that is broken."""
    store.zigbee_settings.save(_settings())
    sources = Sources([_Matter()])  # type: ignore[list-item]
    holder, _state = _holder(store, sources)
    source = await holder.open()

    source.set_progress("loading_quirks")
    assert holder.progress().state == "loading_quirks"
    source.set_progress("failed", attempts=4, error="the stick is not there")
    assert (holder.progress().attempts, holder.progress().error) == (
        4,
        "the stick is not there",
    )


async def test_a_build_that_raises_does_not_kill_the_bridge(store):
    """`apply()` runs in a task nobody awaits. A builder that raises - a
    bug, or an OTBR read that fails in a way its own catch did not cover -
    must be logged, not swallowed into a dead task, and must leave the
    holder usable.

    Fault to prove it: let the exception escape `_apply_in_background`.
    the apply TASK then carries it, and in production that is a "Task
    exception was never retrieved" warning nobody reads.

    The task is awaited DIRECTLY here rather than through
    `wait_for_apply()`, and that detail was found by injecting the fault:
    `wait_for_apply` gathers with `return_exceptions=True` - the right
    behaviour for a shutdown that must not be derailed by one failing
    apply, and blind to exactly this, so a test written on top of it
    passed with the fault in place and measured nothing."""
    sources = Sources([_Matter()])  # type: ignore[list-item]

    async def build(settings: ZigbeeRadioSettings) -> Any:
        raise RuntimeError("boom")

    holder = ZigbeeRuntime(
        store,
        object(),  # type: ignore[arg-type]
        sources,
        build_source=build,
        supervise=_never_ending,
    )

    holder.apply(_settings())
    task = next(iter(holder._applies))
    await task  # must not raise

    assert holder.current() is None
    assert holder.progress().state == "idle"


async def test_stop_ends_the_supervisor_and_leaves_the_disconnect_to_the_caller(store):
    """`cli._run`'s own shutdown disconnects every source in `sources.all()`.
    A second `disconnect()` from here would race it; what this owns are the
    two tasks.

    Fault to prove it: return without cancelling. The supervisor task then
    outlives `_run` and reconnects the stick while the process is closing
    the store underneath it."""
    store.zigbee_settings.save(_settings())
    sources = Sources([_Matter()])  # type: ignore[list-item]
    cancelled: list[str] = []

    async def supervise(source: Any, store_: Any, runtime_: Any) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(source.path)
            raise

    holder, _state = _holder(store, sources, supervise=supervise)
    source = await holder.open()
    holder.supervise_current()
    await asyncio.sleep(0)

    await holder.stop()

    assert cancelled == [ITEAD]
    assert source.disconnects == 0, "disconnecting is the caller's job, and only once"


async def test_supervise_current_starts_exactly_one_supervisor(store):
    """`connect()` has no reentrancy guard: two overlapping calls both see
    `self._app is None`, both open the one serial port, and the loser's
    application leaks with its non-daemon bellows thread still running.

    Fault to prove it: drop the `self._supervisor is not None` guard."""
    store.zigbee_settings.save(_settings())
    sources = Sources([_Matter()])  # type: ignore[list-item]
    holder, state = _holder(store, sources)
    await holder.open()

    holder.supervise_current()
    holder.supervise_current()
    await asyncio.sleep(0)

    assert len(state["supervised"]) == 1


async def test_build_zigbee_source_passes_the_store_through_for_configure_on_join(
    store, monkeypatch
):
    """`store` is not a new capability this task adds - `ZigbeeSource` has
    taken it since Task 9, and Task 10's temporary `_build_zigbee_source`
    already passed it, CLI-flag path and all. This is the one thing the
    move to `build_zigbee_source` must not drop, because nothing about a
    missing store makes a radio change through the settings UI fail
    visibly: the source still connects, still joins devices, still shows
    them on the pairing tab - it just never binds a single cluster.

    Fault to prove it: drop `store=store` from the `ZigbeeSource(...)` call
    inside `build_zigbee_source`, or drop `store=store` from the
    `functools.partial(...)` binding in `cli._run`. Either fault leaves
    every device paired after the fix landed silently unconfigured -
    exactly the regression this task's own move was supposed to carry
    forward, not reintroduce."""

    async def no_border_router() -> int | None:
        return None

    monkeypatch.setattr(
        runtime_module, "current_thread_channel", lambda *a, **k: no_border_router()
    )

    source = await build_zigbee_source(
        _settings(),
        database=Path("/tmp/zigbee.sqlite"),
        on_connection_change=None,
        store=store,
    )

    assert isinstance(source, ZigbeeSource)
    assert source._store is store
    assert source._path == ITEAD
    assert source._database == Path("/tmp/zigbee.sqlite")
    assert source._fingerprint.radio_type == "ezsp"
    assert source._fingerprint.baudrate == 115200


async def test_build_zigbee_source_excludes_the_thread_channel_it_read(store, monkeypatch):
    """The second half of the Thread collision, and the reason
    `channels_excluding` shipped inert: `ZigbeeSource` has excluded OTBR's
    channel since Task 7, but nothing ever read that channel out of OTBR's
    dataset, so in production the exclusion did nothing at all.

    The fetch belongs HERE, once per build, and never inside `connect()` -
    that method runs on every one of the supervisor's 1 s -> 60 s retries.

    Fault to prove it: leave `thread_channel` at its default. The network
    then forms happily on the border router's own channel, with no error
    and no sign anywhere that anything collided."""
    calls: list[int] = []

    async def border_router_on_15() -> int | None:
        calls.append(1)
        return 15

    monkeypatch.setattr(
        runtime_module, "current_thread_channel", lambda *a, **k: border_router_on_15()
    )

    source = await build_zigbee_source(
        _settings(), database=Path("/tmp/z.sqlite"), on_connection_change=None, store=store
    )

    assert source is not None
    assert source._thread_channel == 15
    assert 15 not in source._config()["network"]["channels"]
    assert len(calls) == 1, "once per build, not once per connect attempt"


async def test_an_old_source_whose_disconnect_raises_still_ends_with_a_working_new_one(store):
    """THE fourth instance of this branch's recurring defect: something
    disappearing at the wrong moment, mishandled.

    `_release` tears the old source down BEFORE `_swap` builds the new one.
    An unguarded `disconnect()` that raises therefore stopped the swap
    exactly between those two, and what it left behind was not a partial
    change but a dead bridge: no source, no supervisor, nothing retrying,
    `progress()` reporting `idle` - which means "no radio is configured" -
    while the stored setting named the new stick. Only a restart recovered.

    It is reachable rather than theoretical. `ZigbeeSource.disconnect()`
    runs `await app.shutdown(db=True)` inside `try`/**`finally`**, not
    `try`/`except`, and `_stop_polling_loop()` re-raising is precisely the
    cascade the commit directly beneath this one fixed. The fake below
    models that shape: it finishes its own cleanup and THEN raises, so
    "the teardown happened" and "the teardown reported success" are
    different facts, as they are on real hardware.

    Fault to prove it: drop the `try`/`except` around `await
    source.disconnect()` in `_release`."""
    store.zigbee_settings.save(_settings())
    sources = Sources([_Matter()])  # type: ignore[list-item]

    class _RaisingOnRelease(_FakeSource):
        async def disconnect(self) -> None:
            await super().disconnect()  # the cleanup DID happen
            raise RuntimeError("the polling loop would not stop")

    built: list[Any] = []

    async def build(settings: ZigbeeRadioSettings) -> Any:
        if settings.path is None:
            return None
        source = _RaisingOnRelease(settings.path) if not built else _FakeSource(settings.path)
        built.append(source)
        return source

    supervised: list[Any] = []

    async def supervise(source: Any, store_: Any, runtime_: Any) -> None:
        supervised.append(source)
        source.set_progress("loading_quirks")
        await asyncio.Event().wait()

    holder = ZigbeeRuntime(store, object(), sources, build_source=build, supervise=supervise)  # type: ignore[arg-type]
    old = await holder.open()
    holder.supervise_current()
    await asyncio.sleep(0)

    holder.apply(_settings(path=MG24))
    await holder.wait_for_apply()
    await asyncio.sleep(0)

    # The old stick really was released - that is what makes the raise a
    # report of failure rather than a teardown that did not happen.
    assert old.disconnects == 1
    # And the swap went on regardless: a new source, in the registry, with
    # a supervisor of its own.
    new = holder.current()
    assert new is not None and new.path == MG24
    assert sources.get("zigbee") is new
    assert [s.path for s in supervised] == [ITEAD, MG24]
    assert holder._supervisor is not None and not holder._supervisor.done()
    # And the state the user reads is NOT the one that means "nothing is
    # configured here".
    assert holder.progress().state != "idle"


async def test_progress_says_applying_for_the_whole_window_the_old_source_is_gone(store):
    """The window `_swap` opens between tearing the old source down and
    having built the new one, measured from the outside.

    In production that window is `ZigbeeSource.disconnect()` - a bellows
    `app.shutdown(db=True)` on a Pi - plus `current_thread_channel()`'s
    up-to-5 s HTTP timeout, and for all of it there is no `ZigbeeSource` to
    ask. `progress()` used to fall through to `_idle_progress` there, so a
    running radio change reported the one state that means "no radio is
    configured". The card's rule is to poll while the state is a working
    one; it would have read `idle` in the 202 and again in its first `GET`
    and never started.

    Every other test in this file calls `wait_for_apply()` before it looks,
    so none of them could see this. This one looks INSIDE.

    Fault to prove it: drop the `self._applying` branch from `progress()`,
    or set the marker inside `_apply_in_background` instead of
    synchronously in `apply()` - the second fault leaves the FIRST reading
    below, the one an HTTP handler takes before it returns its 202, still
    saying `idle`."""
    store.zigbee_settings.save(_settings())
    sources = Sources([_Matter()])  # type: ignore[list-item]
    release_the_build = asyncio.Event()
    inside: list[str] = []

    async def build(settings: ZigbeeRadioSettings) -> Any:
        if settings.path == MG24:
            await release_the_build.wait()
        return None if settings.path is None else _FakeSource(settings.path)

    holder = ZigbeeRuntime(store, object(), sources, build_source=build, supervise=_never_ending)  # type: ignore[arg-type]
    await holder.open()
    holder.supervise_current()
    await asyncio.sleep(0)

    holder.apply(_settings(path=MG24))
    # Read exactly where the `PUT` handler reads it: after `apply()`
    # returned, before the background task has run a single line.
    inside.append(holder.progress().state)
    for _ in range(4):
        await asyncio.sleep(0)
        inside.append(holder.progress().state)

    assert inside == ["applying"] * 5, inside
    assert holder.current() is None, "the window really is the one with no source"

    release_the_build.set()
    await holder.wait_for_apply()
    assert holder.current() is not None
    assert holder.progress().state != "applying", "the marker must not outlive the swap"


async def test_a_cancelled_apply_does_not_leave_progress_stuck_on_applying(store):
    """`applying` is a marker a `finally` has to clear, and shutdown is when
    it would not be: `ZigbeeRuntime.stop()` runs while the loop is tearing
    tasks down, and a marker cleared only on the success path would leave
    the last thing the card ever read saying a change is still running.

    Fault to prove it: clear `self._applying` after the `async with` rather
    than in a `finally`."""
    sources = Sources([_Matter()])  # type: ignore[list-item]
    started = asyncio.Event()

    async def build(settings: ZigbeeRadioSettings) -> Any:
        started.set()
        await asyncio.Event().wait()  # never finishes
        return None

    holder = ZigbeeRuntime(store, object(), sources, build_source=build, supervise=_never_ending)  # type: ignore[arg-type]

    holder.apply(_settings())
    await started.wait()
    assert holder.progress().state == "applying"

    task = next(iter(holder._applies))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert holder.progress().state == "idle"


async def test_build_zigbee_source_builds_nothing_without_a_path(store, monkeypatch):
    """And asks OTBR nothing either: there is no network to keep off a
    channel.

    Fault to prove it: fetch the channel before the `path is None` check.
    Every bridge with no Zigbee stick then makes an HTTP request to a
    border router at startup for nothing."""
    calls: list[int] = []

    async def counted() -> int | None:
        calls.append(1)
        return None

    monkeypatch.setattr(runtime_module, "current_thread_channel", lambda *a, **k: counted())

    assert (
        await build_zigbee_source(
            _settings(path=None),
            database=Path("/tmp/z.sqlite"),
            on_connection_change=None,
            store=store,
        )
        is None
    )
    assert calls == []
