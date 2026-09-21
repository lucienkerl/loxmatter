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

"""Forming the Thread network on a fresh installation.

Design 2026-09-21 ("A fresh installation forms its own Thread network"). On
21 September a freshly installed Pi ran for hours with a healthy border
router and no Thread network: nothing in the product had ever created a
dataset, and matter-server cannot commission a Thread device without one.
This module forms that network - through OTBR's REST API, the same channel
`matter/otbr.py` already reads the dataset through - and hands it to
matter-server.

**The one thing it must never do** is replace a network devices already
live in. A border router that lost its dataset (a removed `otbr-state`
volume) looks exactly like a fresh one. The difference is on matter-server's
side: Thread devices it knows. So a pass forms a network only after
matter-server has answered, and only if none of its nodes is a Thread device.
Credentials matter-server holds without any Thread node strand nothing -
`pi3-andi` kept a dataset from an earlier installation in its data directory
and had no nodes at all - and are replaced by the new network's.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

from loxmatter.matter.client import MatterUnavailableError
from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.otbr import (
    ThreadDatasetUnavailableError,
    border_router_role,
    create_network_if_absent,
    enable_thread,
    read_active_dataset,
    thread_channel_from_dataset,
    thread_network_name_from_dataset,
)
from loxmatter.profiles.transport import network_features_of, transport_for

logger = logging.getLogger(__name__)

PASS_INTERVAL_SECONDS: Final = 60.0
THREAD_DIAGNOSTICS_CLUSTER: Final = 0x0035
_ROOT_SERVER_LIST: Final = "0/29/1"
_THREAD_DIAGNOSTICS_PREFIX: Final = f"0/{THREAD_DIAGNOSTICS_CLUSTER}/"

ThreadNetworkState = Literal["unknown", "formed", "forming", "missing"]


@dataclass(frozen=True)
class ThreadNetworkStatus:
    """What the radios card shows. Never the dataset - only its name and
    channel, which every Thread device near the house advertises anyway."""

    state: ThreadNetworkState = "unknown"
    name: str | None = None
    channel: int | None = None
    thread_devices: int = 0

    def as_json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "name": self.name,
            "channel": self.channel,
            "thread_devices": self.thread_devices,
        }


class ThreadMatterClient(Protocol):
    @property
    def thread_dataset_set(self) -> bool: ...

    async def set_thread_dataset(self, dataset: str) -> None: ...

    async def snapshots(self) -> list[NodeSnapshot]: ...


def is_thread_node(snapshot: NodeSnapshot) -> bool:
    """A node that reaches this bridge over Thread.

    Two independent classifiers, because the Thread Network Diagnostics
    cluster (0x0035) is optional and some Thread devices never expose it:
    endpoint 0 serving that cluster (read from the root Descriptor's server
    list, or - for a snapshot without it - from any attribute of that
    cluster), OR `profiles/transport.py`'s hardware-verified classifier,
    which reads the mandatory Network Commissioning FeatureMap. Either one
    saying "thread" is enough."""
    server_list = snapshot.attributes.get(_ROOT_SERVER_LIST)
    if isinstance(server_list, list) and THREAD_DIAGNOSTICS_CLUSTER in server_list:
        return True
    if any(key.startswith(_THREAD_DIAGNOSTICS_PREFIX) for key in snapshot.attributes):
        return True
    return transport_for("matter", network_features_of(snapshot)) == "thread"


def _formed(dataset: str) -> ThreadNetworkStatus:
    return ThreadNetworkStatus(
        state="formed",
        name=thread_network_name_from_dataset(dataset),
        channel=thread_channel_from_dataset(dataset),
    )


class ThreadNetworkKeeper:
    """Runs until a network exists and matter-server has it (design section
    4.1), one pass per `interval`. The guarded case keeps it running, so a
    dataset restored by hand turns up in a later pass."""

    def __init__(
        self,
        client: ThreadMatterClient,
        *,
        base_url: str | None = None,
        session_factory: Callable[[], Any] | None = None,
        interval: float = PASS_INTERVAL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._otbr: dict[str, Any] = {"base_url": base_url, "session_factory": session_factory}
        self._interval = interval
        self._sleep = sleep
        self._status = ThreadNetworkStatus()
        # True until a hand-over actually succeeds, so the first pass that
        # sees an active dataset forces it over regardless of what
        # matter-server reports: a bridge restart after a failed hand-over
        # leaves matter-server holding an older installation's credentials
        # under `thread_dataset_set: true`, which this keeper never sent
        # and must not trust. `run_pass` also sets this the moment
        # `create_network_if_absent` reports "created", so a later pass
        # keeps forcing the hand-over - even past that same stale flag -
        # until one actually succeeds. Cleared only by a successful
        # `set_thread_dataset`, never by a failed attempt.
        self._handover_owed = True

    @property
    def status(self) -> ThreadNetworkStatus:
        return self._status

    async def run(self) -> None:
        while not await self.run_pass():
            await self._sleep(self._interval)

    async def run_pass(self) -> bool:
        """One pass of design section 4.2. `True` when there is nothing left
        to do. Never raises for a border router or matter-server that is
        away; the next pass tries again."""
        try:
            dataset = await read_active_dataset(**self._otbr)
        except ThreadDatasetUnavailableError:
            # Thread off, otbr starting, or no border router at all - the
            # same quiet "not now" every installation without Thread sees
            # once a minute. A `missing` finding stays until it is resolved.
            if self._status.state != "missing":
                self._status = ThreadNetworkStatus()
            return False

        if dataset is not None:
            self._status = _formed(dataset)
            return await self._hand_over(dataset, force=self._handover_owed)

        try:
            role = await border_router_role(**self._otbr)
        except ThreadDatasetUnavailableError:
            return False
        if role != "disabled":
            return False

        try:
            nodes = await self._client.snapshots()
        except MatterUnavailableError:
            return False
        thread_devices = sum(1 for node in nodes if is_thread_node(node))
        if thread_devices:
            self._status = ThreadNetworkStatus(state="missing", thread_devices=thread_devices)
            return False

        self._status = ThreadNetworkStatus(state="forming")
        try:
            outcome = await create_network_if_absent(**self._otbr)
            if outcome != "created":
                # Someone else was faster (412) or the agent left `disabled`
                # (409). The next pass reads whatever network exists now.
                return False
            self._handover_owed = True
            await enable_thread(**self._otbr)
            dataset = await read_active_dataset(**self._otbr)
        except ThreadDatasetUnavailableError as exc:
            # Includes an `enable_thread` failure: an agent with a dataset
            # that stays `disabled` is a fault for scripts/otbr-watchdog.sh
            # to restart, not this loop to repair (design section 3) -
            # otbr-agent re-attaches to the saved dataset on its own restart,
            # and a later pass here hands that dataset over once it does.
            logger.warning("Could not form a Thread network: %s", exc)
            # "forming" must not linger past this pass - a 412/409 outcome
            # (handled above, no exception) may stay `forming` because the
            # next pass resolves it by reading the network in step 1, but a
            # genuine failure here has nothing in progress any more.
            self._status = ThreadNetworkStatus()
            return False
        if dataset is None:
            return False

        self._status = _formed(dataset)
        logger.info(
            "Formed Thread network %s on channel %s", self._status.name, self._status.channel
        )
        return await self._hand_over(dataset, force=True)

    async def _hand_over(self, dataset: str, *, force: bool = False) -> bool:
        """Give matter-server the dataset unless it confirms it has one.
        `force` after forming, and while a hand-over from an earlier forming
        pass is still owed: whatever matter-server held before is not this
        network."""
        if not force and self._client.thread_dataset_set:
            return True
        try:
            await self._client.set_thread_dataset(dataset)
        except MatterUnavailableError:
            return False
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            # `BridgeMatterClient.set_thread_dataset` calls upstream
            # directly and can raise anything (a closed connection, a
            # failed command) - none of it may kill `run()`, the next pass
            # tries again. `asyncio.CancelledError` is re-raised above.
            logger.warning(
                "Could not hand the Thread network to matter-server: %s", type(exc).__name__
            )
            return False
        self._handover_owed = False
        return True
