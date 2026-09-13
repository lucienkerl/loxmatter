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

"""The live Zigbee source, and the radio change that swaps it.

**Why a holder object at all.** `build_app` builds its routers before
`cli._run` owns any source, so `api/zigbee.py` cannot be handed the source
itself - it would capture whichever one existed at startup, including the
`None` of an installation that had no stick yet. It is handed this instead,
constructed in `_run` and closed over by both.

**Why the apply is asynchronous.** The obvious shape - the `PUT` persists
the setting, awaits `source.connect()` and answers with the result - is
wrong, and it was written into an earlier draft of the plan before being
rejected. On a first-ever Zigbee configuration that handler would await an
estimated 9-15 s of `zhaquirks.setup()` on a Pi 4, then
`startup(auto_form=True)`, then, if the stick is silent or is not a
coordinator, a further 7.5 s before `TimeoutError`: up to half a minute of
a blocked HTTP request with nothing on screen moving. So the `PUT` stores
the setting, calls `apply()`, and answers 202 at once; the connection
happens in the background where the user can watch it through
`progress()`.

The mechanism is the one the repository already has rather than a second
one invented here: `sources/supervisor.py` owns connection attempts with a
1 s -> 60 s backoff, and starting it is all it takes to connect - it opens
with `await source.wait_for_link_loss()`, which returns AT ONCE for a
source that was never connected, so the supervisor falls straight into its
own connect-and-back-off loop and performs the first attempt itself. A
stick that is missing at apply time is therefore retried on the same
schedule as one that dies an hour later.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Final, cast

from loxmatter.loxone.runtime import Runtime
from loxmatter.matter.otbr import current_thread_channel
from loxmatter.model.store import Store
from loxmatter.model.zigbee_settings_store import ZigbeeRadioSettings
from loxmatter.radios.fingerprints import Fingerprint, FlowControl, RadioType
from loxmatter.sources import Sources
from loxmatter.sources.supervisor import supervise
from loxmatter.timestamps import now_iso
from loxmatter.zigbee.source import (
    ConnectionProgress,
    CoordinatorInfo,
    OpenGuard,
    ZigbeeSource,
)

logger = logging.getLogger(__name__)

# What `build_source` looks like once `cli._run` has bound everything that
# never changes between a startup build and an apply-time rebuild.
SourceBuilder = Callable[[ZigbeeRadioSettings], Awaitable["ZigbeeSource | None"]]

ZIGBEE_DATABASE_NAME: Final = "zigbee.sqlite"


def zigbee_database_beside(store_path: Path) -> Path:
    """Where zigpy keeps its network database: in the directory of
    loxmatter's own store, whichever way that path was given
    (`--store-path`, `LOXMATTER_STORE`, or the `~/.loxmatter/` default).

    Design section 8.5 put it "next to the store in the same volume", and
    the store's directory is the one place the bridge is already known to
    write to - `cli.run` creates it before the store is opened. The first
    build derived it from `--matter-data-dir` instead, which the shipped
    compose file mounts read-only (it is matter-server's directory, lent to
    the fabric-backup route), so zigpy could not have created the file on
    the very first Apply. `tests/test_compose_profiles.py` holds every
    shipped compose file to this rule.
    """
    return store_path.parent / ZIGBEE_DATABASE_NAME


async def build_zigbee_source(
    settings: ZigbeeRadioSettings,
    *,
    database: Path,
    on_connection_change: Callable[[bool], Awaitable[None]] | None,
    store: Store,
    open_guard: OpenGuard | None = None,
    host_dev: Path | None = None,
) -> ZigbeeSource | None:
    """The one place a `ZigbeeSource` is built - at startup and on every
    radio change alike, now that `ZigbeeRuntime` owns both. Two call sites
    that built it separately were exactly the drift
    `sources/supervisor.py`'s own docstring warns about for `attach()`, so
    there is only one left.

    Fetches the Thread channel to avoid HERE, once per build, rather than
    inside `ZigbeeSource.connect()`. `channels_excluding`'s own docstring
    names this call as its outstanding debt and says where it belongs: the
    caller that already knows about OTBR, which must not become an HTTP
    call inside `connect()` - that method runs on every one of the
    supervisor's 1 s -> 60 s retries, not once. Building a new
    `ZigbeeSource` happens far less often - once at startup, once per radio
    change - so paying for the fetch here is the same trade the quirks
    warm-up already makes.

    (The warm-up is named in prose only, and deliberately not by its
    function name. The test in `tests/zigbee/test_source.py` that keeps it
    off every request path greps the source tree for that name, and it
    cannot tell a mention from a call.)

    **`store` is carried across from the earlier startup-only builder, not
    a new parameter.** `_build_zigbee_source(store)`'s own name said as
    much, and this function is explicitly what that name is replaced by -
    a move that dropped the parameter rather than carrying it would
    silently undo the fix that gave `configure_device` its pending table,
    and leave it with none again from the very first radio change made
    through the settings UI, even though the CLI-flag startup path kept
    working. Without it, a device paired before this landed keeps working;
    one paired after the FIRST radio change made through this function's
    caller would not.
    """
    if settings.path is None:
        return None
    fingerprint = Fingerprint(
        name="",
        # The store returns plain strings: nothing about a SQLite text
        # column guarantees one of the three radio types, and a value
        # edited from outside the application must fail where the user can
        # read about it - `connect()`'s translated "could not be started"
        # message - rather than as a `ValueError` on a route that was only
        # listing sticks. `_settings_from` in `api/zigbee.py` is what keeps
        # written values inside the literal.
        radio_type=cast("RadioType", settings.radio_type),
        baudrate=settings.baudrate,
        flow_control=cast("FlowControl", settings.flow_control),
    )
    channel = await current_thread_channel()
    return ZigbeeSource(
        path=settings.path,
        fingerprint=fingerprint,
        database=database,
        on_connection_change=on_connection_change,
        thread_channel=channel,
        store=store,
        open_guard=open_guard,
        host_dev=host_dev,
    )


class ZigbeeRuntime:
    """Owns the live Zigbee source and the task supervising it.

    One object rather than a bare callable because applying a radio change
    means three things at once - swap the source in the registry, stop the
    old supervisor, start a new one - and doing two of the three is worse
    than doing none.
    """

    def __init__(
        self,
        store: Store,
        runtime: Runtime,
        sources: Sources,
        *,
        build_source: SourceBuilder,
        supervise: Callable[[ZigbeeSource, Store, Runtime], Awaitable[None]] = supervise,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._sources = sources
        self._build_source = build_source
        self._supervise = supervise
        self._source: ZigbeeSource | None = None
        self._supervisor: asyncio.Task[None] | None = None
        # Held on `self` so the garbage collector cannot take one
        # mid-flight, the `_pulse_tasks` pattern in `loxone/runtime.py`.
        self._applies: set[asyncio.Task[None]] = set()
        # One swap at a time. Two overlapping applies would both tear down
        # and both rebuild, and the loser's source would be left running
        # with the winner's stick in the registry - a second application
        # holding the serial port open for a stick nobody can reach.
        self._swapping = asyncio.Lock()
        self._idle_progress = ConnectionProgress(
            state="idle", attempts=0, error=None, changed_at=now_iso()
        )
        # What `progress()` reports while a change is in flight, and the
        # count of changes that are. A counter rather than a flag because
        # `apply()` can be called again while the first swap still holds
        # `_swapping`: the first one's `finally` must not clear a marker the
        # second one still needs.
        self._applying: ConnectionProgress | None = None
        self._applies_pending = 0

    async def open(self) -> ZigbeeSource | None:
        """Builds the source the STORED setting names and registers it.

        The startup half of this object's job, and it goes through the same
        `build_source` every later change does - which is the whole reason
        the separate startup builder was moved in here.

        Does not start the supervisor: `cli._run` attaches every source
        before supervising any, and a supervisor started here would connect
        and attach underneath that loop.
        """
        source = await self._build_source(self._store.zigbee_settings.get())
        self._source = source
        if source is not None:
            self._sources.replace("zigbee", source)
        return source

    def supervise_current(self) -> None:
        """Starts the supervisor for the source `open()` built.

        Separate from `open()` for the ordering reason given there, and a
        no-op when no radio is configured - there is nothing to supervise
        until an apply builds something.
        """
        if self._source is None or self._supervisor is not None:
            return
        self._supervisor = asyncio.ensure_future(
            self._supervise(self._source, self._store, self._runtime)
        )

    def current(self) -> ZigbeeSource | None:
        return self._source

    def progress(self) -> ConnectionProgress:
        """How far the current attempt got - `idle` while no radio is
        configured, which is not an error and must not read as one.

        **`applying` covers the window in which there is nothing to ask.**
        `_swap` tears the old source down before it builds the new one, so
        between those two there is no `ZigbeeSource` at all and this method
        used to fall through to `_idle_progress` - reporting a radio change
        that is very much running as the one state that means "nothing is
        configured here". In production that window is
        `ZigbeeSource.disconnect()` (a bellows `app.shutdown(db=True)` on a
        Pi) plus `current_thread_channel()`'s up-to-5 s HTTP timeout, and
        the card's polling rule keys off this very field: it would have read
        `idle` in the 202 and again in its first `GET`, decided nothing was
        happening, and never started polling. That is the "a running job the
        user cannot tell from a dead one" failure the plan names as a Global
        Constraint, so the window gets a state of its own.

        The marker is set SYNCHRONOUSLY in `apply()`, not inside the
        background task: `asyncio.ensure_future` does not run a single line
        of the coroutine before the handler that called it has returned, so
        a marker set in `_apply_in_background` would still leave `idle` in
        the 202 the user's browser reads first.
        """
        if self._applying is not None:
            return self._applying
        return self._idle_progress if self._source is None else self._source.progress()

    def coordinator(self) -> CoordinatorInfo | None:
        """What the current stick reported about itself on its last
        successful connect - its firmware above all - or `None`."""
        return None if self._source is None else self._source.coordinator()

    def apply(self, settings: ZigbeeRadioSettings) -> None:
        """Schedules the change. Returns IMMEDIATELY - see the module
        docstring.

        Not `async`: the caller is an HTTP handler that must not await any
        part of this, and a coroutine would invite exactly that mistake.
        """
        self._applies_pending += 1
        self._applying = ConnectionProgress(
            state="applying", attempts=0, error=None, changed_at=now_iso()
        )
        task = asyncio.ensure_future(self._apply_in_background(settings))
        self._applies.add(task)
        task.add_done_callback(self._applies.discard)

    async def wait_for_apply(self) -> None:
        """Awaits whatever `apply()` scheduled.

        For shutdown and for tests. A snapshot (`tuple(...)`) rather than
        the set itself: the done callback removes each task from
        `self._applies` as it finishes, and iterating the live set across
        an `await` is the mutation-during-await mistake that has already
        killed one loop on this branch.
        """
        while self._applies:
            await asyncio.gather(*tuple(self._applies), return_exceptions=True)

    async def stop(self) -> None:
        """Ends the supervisor and any scheduled apply.

        Does NOT disconnect the source: `cli._run`'s own shutdown walks
        `sources.all()` and disconnects every source there, and a second
        `disconnect()` from here would race it. What this owns, and what
        nothing else would otherwise stop, are the two tasks.
        """
        await self.wait_for_apply()
        await self._stop_supervisor()

    async def _stop_supervisor(self) -> None:
        supervisor, self._supervisor = self._supervisor, None
        if supervisor is None:
            return
        supervisor.cancel()
        try:
            await supervisor
        except asyncio.CancelledError:
            if not supervisor.cancelled():
                # The cancellation hit US, not the supervisor - it has to
                # keep travelling, the rule `cli._run`'s shutdown already
                # follows for its own supervisor tasks.
                raise
        except Exception:
            logger.exception("the Zigbee supervisor ended with an error")

    async def _apply_in_background(self, settings: ZigbeeRadioSettings) -> None:
        try:
            async with self._swapping:
                try:
                    await self._swap(settings)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A failure here is a programming error or a build that
                    # raised, not a radio that would not open - the
                    # supervisor owns that case and reports it through
                    # `progress()`. It must not vanish: this runs in a task
                    # nobody awaits.
                    logger.exception("applying the Zigbee radio setting failed")
        finally:
            # In a `finally` and not after the `async with`, so that a
            # cancelled apply - `stop()` during shutdown, or a task the loop
            # tears down - cannot leave `progress()` stuck on `applying`
            # forever, which would read as a change that never ends.
            self._applies_pending -= 1
            if self._applies_pending <= 0:
                self._applies_pending = 0
                self._applying = None

    async def _swap(self, settings: ZigbeeRadioSettings) -> None:
        await self._release()
        source = await self._build_source(settings)
        if source is None:
            return
        self._source = source
        self._sources.replace("zigbee", source)
        self._supervisor = asyncio.ensure_future(
            self._supervise(source, self._store, self._runtime)
        )

    async def _release(self) -> None:
        """Gives up the current source and its stick, in this order.

        **The supervisor is cancelled BEFORE the source is disconnected,
        and that order is load-bearing.** `disconnect()` sets `_link_lost`
        on purpose, so a supervisor parked in `wait_for_link_loss()` wakes
        the instant the port is released and reconnects it - reopening the
        very stick the user just asked to stop using, and leaving a second
        application on a port the next apply then finds busy.

        The source leaves the registry BEFORE the `await` on
        `disconnect()`, so a Loxone command arriving mid-swap gets
        `SourceNotConfiguredError` - "there is no Zigbee radio", which is
        true - rather than being handed a source whose application is being
        shut down underneath it.

        **The `disconnect()` is guarded, and that guard is the whole point
        of this paragraph.** Letting it raise out of here stopped the swap
        AFTER the teardown and BEFORE the rebuild: no source, no supervisor,
        nothing retrying, and `progress()` reporting the state that means
        "no radio is configured" while the stored setting named the new
        stick. Nothing recovered that but a restart of the bridge. And it is
        reachable rather than theoretical - `ZigbeeSource.disconnect()` runs
        `await app.shutdown(db=True)` inside `try`/**`finally`**, not
        `try`/`except`, and `_stop_polling_loop()` re-raising is precisely
        the cascade the commit directly beneath this one fixed.

        Releasing the OLD stick is best effort by nature: whatever went
        wrong, the user asked for a different radio, and the new one is
        still openable. So the failure is logged with its traceback - the
        one place anybody can learn that a serial port may still be held -
        and the swap goes on to build what was asked for.
        """
        await self._stop_supervisor()
        source, self._source = self._source, None
        self._sources.replace("zigbee", None)
        if source is None:
            return
        try:
            await source.disconnect()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("releasing the previous Zigbee radio failed; continuing with the new")
