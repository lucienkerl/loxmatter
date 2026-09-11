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
from functools import partial

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
    await client.subscribe(
        partial(store.device_id_for, "matter"),  # TRANSITIONAL (Task 6)
        runtime,
    )
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
