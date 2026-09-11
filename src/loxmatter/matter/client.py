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

"""Connection to matter-server via its WebSocket API.

**Addendum (8 September 2026): the upstream now has a different name.**
This docstring verifies its statements consistently against the then-
installed `python-matter-server==8.1.2`. These verifications remain unchanged:
they were verified against source code and remain correct for that version.
Installed now is instead `matter-python-client` from
the successor project `matterjs-server` - the same `matter_server*` package and
the same `chip*` under the same module paths, which is why no import and
no instruction here had to change. Every signature verified below was checked
against the new source code; the only deviation is at
`set_thread_dataset()`. Where "python-matter-server" stands below, it means the
version THAT WAS MEASURED AGAINST, not the one installed today. The
complete comparison is in the design
docs/superpowers/specs/2026-09-08-matterjs-server-migration-design.md,
section 2 (also there, which calls the first version had overlooked).

Deliberately kept thin: fetches raw data and turns it into NodeSnapshots.
The decomposition into signals happens in discovery.py and is tested there
without a network.

BridgeMatterClient creates the aiohttp ClientSession itself and thereby
remains its sole owner: python-matter-server's
MatterClientConnection.disconnect() only closes the websocket, not the
session that was handed to it - by aiohttp convention, whoever created the
session must do that. This class therefore holds the session reference
itself and closes it in disconnect() or on a failed connect().

The upstream `MatterClient` fills its node cache exclusively in
`start_listening()` - a long-running coroutine that fetches the initial
node dump, sets an `init_ready` event and then keeps running to receive
push updates. `connect()` therefore starts it as a background task and
waits for the readiness event before the client reports itself as
connected; `disconnect()` cancels this task again before the connection is
closed.

subscribe() - a deviation from the assignment (task 8), verified against
the installed python-matter-server==8.1.2:

`MatterClient.subscribe_events(callback, event_filter, node_filter,
attr_path_filter)` calls `callback` on every match as `callback(event,
data)` - synchronously, only two arguments. `node_filter`/`attr_path_filter`
control exclusively *whether* a registered `callback` is called at all
(key matching in `MatterClient._signal_event`), they are NOT passed to it.
For `EventType.NODE_EVENT` and `EventType.NODE_ADDED`/`NODE_UPDATED` that
is nonetheless enough: `data` is there a `MatterNodeEvent` or the full
`MatterNode`, both of which carry `node_id` themselves. For
`EventType.ATTRIBUTE_UPDATED`, however, `data` is solely the new value -
no `node_id`, no attribute path. A single wildcard subscription therefore
cannot attribute an attribute update to a device; this is not a gap in
the design but built into `_handle_event_message`/`_signal_event` this
way (see `.venv/.../matter_server/client/client.py`).

This is why `subscribe()` registers, for attributes, exactly one
subscription per (node, path) pair known at call time - `node_filter` and
`attr_path_filter` exactly determine what a callback stands for, and the
callback itself closes over `node_id`/`path` as a closure. Node events and
reachability, by contrast, each run through a single wildcard
subscription, because their `data` already carries everything needed.

Whatever is added after `subscribe()` is caught up by `follow_node()` - a
device that is only commissioned afterwards, as well as a known device
that subsequently reports new attribute paths. It is triggered from the
dispatch loop on `NODE_ADDED`/`NODE_UPDATED` and additionally from the
commissioning route. This "additionally" is not belt-and-braces: the
`NODE_ADDED` of a device just commissioned demonstrably arrives BEFORE
`commission_with_code` returns and the store can give the node a
device_id - the values of this notification therefore go nowhere, and no
second one follows for a device that is quietly sitting on the network.
At this point, however, the dispatch task has already subscribed to every
path; the route's follow-up call therefore finds an empty diff and still
seeds only because it requests it with `seed_even_without_new_paths=True`
(the full rationale is at `follow_node`). See
docs/superpowers/specs/2026-09-04-live-values-for-new-devices-design.md.

commission_with_code()/remove_node()/set_thread_dataset() - verified
against the installed python-matter-server==8.1.2 (task 1, phase 5):

`MatterClient.commission_with_code(self, code: str, network_only: bool =
False) -> MatterNodeData` - `MatterClient.remove_node(self, node_id: int) ->
None` - `MatterClient.set_thread_operational_dataset(self, dataset: str) ->
None`. `remove_node` and `set_thread_operational_dataset` match the plan's
assumption exactly.

`commission_with_code` does NOT: the plan assumed the return value would
carry its raw attributes like a `MatterNode` from `get_nodes()` under
`node_data.attributes` (see above regarding `snapshots()`). In fact,
`commission_with_code` returns, per the source
(`dataclass_from_dict(MatterNodeData, data)`), the `MatterNodeData`
dataclass itself - `node_id` and `attributes` sit directly on the object,
no nesting. `MatterNode` (with `node_data` indirection) and `MatterNodeData`
(flat) are two different types in python-matter-server; `get_nodes()`
returns the former, `commission_with_code()` the latter. Uncritically
adopting the plan's assumption would have made `node.node_data.attributes`
fail with `AttributeError` - at least loudly, unlike the two silently
failing wrong assumptions from phase 4 (see above), but without a
verification pass (step 1), that too would have first surfaced during the
first real commissioning attempt, not while writing the code.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Final, Protocol

from loxmatter import i18n
from loxmatter.matter.models import NodeSnapshot
from loxmatter.sources import DeviceCall

logger = logging.getLogger(__name__)

# How long connect() waits for the listener's readiness event before
# giving up. matter-server normally sends the initial node dump within a
# few seconds; the multiple serves as a safety margin against a slow or
# hanging server.
LISTENER_READY_TIMEOUT_SECONDS: Final = 10.0


class MatterUnavailableError(RuntimeError):
    """matter-server is not connected, does not know the requested node,
    or does not know the requested command."""


class CommissioningError(RuntimeError):
    """Commissioning a device failed at the device itself (e.g. wrong
    code, device is already in another ecosystem, timeout during the
    interview).

    A loss of connection to matter-server WHILE commissioning is
    deliberately distinguished from this: commission_with_code() catches
    `NotConnected`/`ConnectionClosed`/`CannotConnect` separately and
    raises `MatterUnavailableError` for that, because only this way can
    one distinguish whether the device rejected the attempt or
    matter-server was unreachable (spec 8.1/9, review fix task 1). The
    original exception is preserved via `__cause__`."""


class RuntimeEventHandler(Protocol):
    """What `subscribe()` needs from its caller - `Runtime`
    (loxone/runtime.py) already satisfies this unchanged, so `_run()` can
    pass it directly as `handler`, without writing an adapter.

    `on_node_snapshot` was added with the follow-up of subscriptions
    (`follow_node`): the client sees a device with paths for which there
    is no signal row yet, and cannot do anything with that itself - it
    does not know the `Store` and is not supposed to know it. The handler,
    on the other hand, has it."""

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None: ...
    async def on_event(self, device_id: int, path: str) -> None: ...
    async def set_online(self, device_id: int, online: bool) -> None: ...
    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None: ...


@dataclass(frozen=True)
class _AttributeUpdate:
    node_id: int
    path: str
    raw: object


@dataclass(frozen=True)
class _EventUpdate:
    node_id: int
    path: str


@dataclass(frozen=True)
class _AvailabilityUpdate:
    node_id: int
    available: bool


@dataclass(frozen=True)
class _FollowNode:
    """Trigger to catch up a node's subscriptions.

    Runs over the same queue as the value updates, rather than directly
    out of the synchronous event callback: `follow_node` is a coroutine,
    and the callback cannot await one (see
    `on_node_or_availability_event`).
    """

    node_id: int


_QueueItem = _AttributeUpdate | _EventUpdate | _AvailabilityUpdate | _FollowNode


async def _cancel_and_await(task: asyncio.Task[Any]) -> None:
    """Cancels a task and awaits its end.

    Intended purely for cleanup purposes: exceptions from the cancelled
    task (typically CancelledError, but also others if the task had
    already ended with an error before) are swallowed here, so they do not
    obscure the actual, already-running error path - the caller has
    already seen the relevant exception at its actual source, or will
    still see it there.
    """
    task.cancel()
    with contextlib.suppress(BaseException):
        await task


class BridgeMatterClient:
    def __init__(
        self,
        url: str,
        session_factory: Callable[[Any], Any] | None = None,
        http_session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._url = url
        self._session_factory = session_factory or self._default_session_factory
        self._http_session_factory = http_session_factory or self._default_http_session_factory
        self._upstream: Any | None = None
        self._http_session: Any | None = None
        self._listener_task: asyncio.Task[Any] | None = None
        # subscribe() state: the dispatch task reads _event_queue and
        # calls the handler; _unsubscribers are the callback functions
        # upstream.subscribe_events() returns per registration.
        self._dispatch_task: asyncio.Task[None] | None = None
        self._unsubscribers: list[Callable[[], None]] = []
        # Whether THIS connection has already handed matter-server the
        # Thread dataset - see `thread_dataset_set` for why that cannot be
        # read from `server_info` alone. Reset on every connect(): a new
        # connection can hit a freshly restarted matter-server, and that
        # one has forgotten it.
        self._thread_dataset_set = False
        # subscribe()/follow_node() state. The set of already-created
        # attribute subscriptions is the only source of what counts as
        # "new" - a second subscription for the same (node, path) would
        # deliver every value twice. Queue, handler and the device_id
        # resolution remain reachable after subscribe(), because
        # follow_node needs them.
        self._subscribed_paths: set[tuple[int, str]] = set()
        # Nodes this bridge still owes a snapshot to - see `follow_node`,
        # which also explains why that answers a DIFFERENT question than
        # the `seed_even_without_new_paths` flag.
        self._seed_pending: set[int] = set()
        self._queue: asyncio.Queue[_QueueItem] | None = None
        self._handler: RuntimeEventHandler | None = None
        self._resolve_device_id: Callable[[int], int | None] | None = None

    def _default_session_factory(self, session: Any) -> Any:
        # Lazily imported, so tests never need to load matter_server.
        from matter_server.client.client import MatterClient

        return MatterClient(self._url, session)

    @staticmethod
    def _default_http_session_factory() -> Any:
        # Lazily imported, so tests never need to load aiohttp.
        import aiohttp

        return aiohttp.ClientSession()

    async def _start_listener(self, upstream: Any) -> asyncio.Task[Any]:
        """Starts upstream.start_listening() as a background task and
        waits until it has filled the node cache and signaled readiness.
        If the listener fails or does not report in time, this method
        fully cleans up the task and raises, instead of returning a
        half-connected task."""
        ready = asyncio.Event()
        listener_task: asyncio.Task[Any] = asyncio.ensure_future(upstream.start_listening(ready))
        ready_task = asyncio.ensure_future(ready.wait())
        try:
            done, _pending = await asyncio.wait(
                {listener_task, ready_task},
                timeout=LISTENER_READY_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if ready_task in done:
                # Readiness reported - the listener now keeps running in
                # the background to receive push updates.
                return listener_task

            await _cancel_and_await(ready_task)

            if listener_task in done:
                # The listener ended before it reported readiness.
                # .result() re-raises its original exception unchanged
                # (e.g. CannotConnect) - callers such as the CLI can thus
                # keep handling it specifically.
                listener_task.result()
                msg = i18n.t("api.errors.listener_stopped_early")
                raise MatterUnavailableError(msg)

            msg = i18n.t("api.errors.listener_timeout", timeout=LISTENER_READY_TIMEOUT_SECONDS)
            raise MatterUnavailableError(msg)
        except BaseException:
            await _cancel_and_await(listener_task)
            raise

    async def connect(self) -> None:
        # An already connected client is cleanly disconnected on a renewed
        # connect() before reconnecting - otherwise the old, still-open
        # session would become unreachable and never closed when
        # self._upstream/self._http_session are overwritten.
        if self._upstream is not None:
            await self.disconnect()
        http_session = self._http_session_factory()
        try:
            upstream = self._session_factory(http_session)
            listener_task = await self._start_listener(upstream)
        except BaseException:
            # BaseException rather than Exception: asyncio.CancelledError
            # inherits from BaseException, not from Exception. A connect()
            # cancelled during connection setup (e.g. by asyncio.wait_for)
            # must still close the session and re-raise the cancellation.
            await http_session.close()
            raise
        self._http_session = http_session
        self._upstream = upstream
        self._listener_task = listener_task
        self._thread_dataset_set = False

    async def disconnect(self) -> None:
        if self._upstream is None:
            return
        upstream = self._upstream
        http_session = self._http_session
        listener_task = self._listener_task
        dispatch_task = self._dispatch_task
        unsubscribers = self._unsubscribers
        # Set fields to None before the await: this way the client is
        # immediately recognizable as not connected, even if one of the
        # steps below raises an exception - disconnect() stays idempotent
        # and the object state clean, no matter how the disconnection
        # turns out.
        self._upstream = None
        self._http_session = None
        self._listener_task = None
        self._dispatch_task = None
        self._unsubscribers = []
        self._subscribed_paths = set()
        self._seed_pending = set()
        self._queue = None
        self._handler = None
        self._resolve_device_id = None
        if http_session is None:
            # Invariant: if _upstream is set, _http_session is set too
            # (both are only ever set together in connect()). An explicit
            # error rather than assert, so the check also applies under
            # `python -O`.
            msg = "internal error: _http_session is missing despite an active _upstream"
            raise RuntimeError(msg)
        # First unsubscribe from the upstream (no more updates into the
        # queue), then cancel the dispatch task (nothing more processed
        # from the queue) - both before the actual connection teardown,
        # otherwise the dispatch task would keep running against an
        # already-disconnected upstream.
        for unsubscribe in unsubscribers:
            unsubscribe()
        if dispatch_task is not None:
            await _cancel_and_await(dispatch_task)
        try:
            if listener_task is not None:
                await _cancel_and_await(listener_task)
        finally:
            try:
                await upstream.disconnect()
            finally:
                await http_session.close()

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
        not have to distinguish between reasons for the breakdown. Without
        the `exception()` call, Python would also write "Task exception was
        never retrieved" to the log when cleaning the task up.
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
            logger.warning("listener of matter-server ended without an error")

    def _require_upstream(self) -> Any:
        if self._upstream is None:
            raise MatterUnavailableError(i18n.t("api.errors.not_connected"))
        return self._upstream

    async def snapshots(self) -> list[NodeSnapshot]:
        upstream = self._require_upstream()
        return [
            # For matter_server.MatterNode, the raw attributes do not sit
            # directly on the node, but on node.node_data.attributes -
            # this used to be unobservable because the node cache was
            # always empty before the listener was wired up (see the
            # module docstring). `available` is deliberately included
            # (review fix C1, 2026-09-02): `Runtime.seed_from_snapshot`
            # needs it to correctly seed a device as on-/offline at
            # startup, instead of leaving it undetermined until the next
            # NODE_ADDED/NODE_UPDATED.
            NodeSnapshot.from_raw(
                node.node_id,
                {"attributes": node.node_data.attributes, "available": node.available},
            )
            for node in upstream.get_nodes()
        ]

    async def snapshot(self, node_id: int) -> NodeSnapshot:
        for candidate in await self.snapshots():
            if candidate.address == str(node_id):
                return candidate
        raise MatterUnavailableError(i18n.t("api.errors.unknown_node", node_id=node_id))

    async def commission_with_code(self, code: str) -> NodeSnapshot:
        """Commissions a device via its pairing code.

        The code is the 11-digit number or the 21-character MT: code from
        the device or its packaging. If the device is already in another
        ecosystem, the printed code no longer works - it then needs a
        multi-admin code from there (spec 7.1).

        The upstream returns here a `MatterNodeData`, whose
        `node_id`/`attributes`/`available` sit directly on the object -
        unlike with `get_nodes()` (see the module docstring). A Thread
        device fails here with "Required network information not
        provided" as long as `set_thread_dataset()` has not been called
        beforehand.
        """
        upstream = self._require_upstream()

        # Lazily imported like _default_session_factory: tests with a
        # fake upstream should never need to load matter_server.
        from matter_server.client.exceptions import CannotConnect, ConnectionClosed, NotConnected

        try:
            node = await upstream.commission_with_code(code)
        except (NotConnected, ConnectionClosed, CannotConnect) as exc:
            # A loss of connection to matter-server is not a rejection by
            # the device - both used to land indistinguishably in
            # CommissioningError (review fix, see the task 1 report).
            # Catches this branch BEFORE the generic except Exception
            # below, otherwise it would be caught there instead.
            msg = i18n.t("api.errors.matter_server_unreachable", exc=exc)
            raise MatterUnavailableError(msg) from exc
        except Exception as exc:
            raise CommissioningError(i18n.t("api.errors.commissioning_failed", exc=exc)) from exc
        return NodeSnapshot.from_raw(
            node.node_id, {"attributes": node.attributes, "available": node.available}
        )

    async def remove_node(self, node_id: int) -> None:
        """Removes a device from the fabric."""
        await self._require_upstream().remove_node(node_id)

    @property
    def thread_dataset_set(self) -> bool:
        """Whether matter-server currently has the Thread credentials.

        Two sources, because neither alone is enough:

        - `server_info.thread_credentials_set` says what matter-server
          reported AT CONNECTION SETUP. The service does send a
          `SERVER_INFO_UPDATED` event on every change
          (`device_controller.set_thread_operational_dataset` triggers
          it), but `MatterClient._handle_event_message` has no branch for
          it - the snapshot therefore stays as it was for the duration of
          the connection, even after this bridge has set the dataset
          itself. Verified against the installed version, not assumed.
        - `_thread_dataset_set` are this connection's own, successful
          calls to `set_thread_dataset()`.

        The result is deliberately conservative: `False` means "cannot be
        confirmed", not "definitely not set". The caller then fetches a
        dataset and sets it again - that is idempotent and costs one HTTP
        call, whereas the reverse mix-up would let a Thread device fail
        only after 40 seconds with "Commission with code failed".
        """
        if self._thread_dataset_set:
            return True
        info = getattr(self._upstream, "server_info", None)
        return bool(getattr(info, "thread_credentials_set", False))

    async def set_thread_dataset(self, dataset: str) -> None:
        """Hands matter-server the Thread credentials.

        Without this step, commissioning a Thread device fails with
        "Required network information not provided" - the controller
        finds the device via BLE, but cannot tell it about a network.

        matter-server holds them exclusively in memory (see
        `matter/otbr.py` for the entire process and the failure case):
        every restart of the service deletes them again, and this bridge
        must hand them over again afterward.

        **Update (8 September 2026): the payload changes here.**
        This is the only call in this module where that is true - and
        the first version of the migration design had missed exactly that
        (corrected there meanwhile, section 2.2). The signature is called
        `set_thread_operational_dataset(dataset, entry_id="default")` in
        `matter-python-client`, and the client sends `dataset` **and**
        `id=entry_id` over the wire; the old 8.1.2 sent only `dataset`.
        The call here does not specify `entry_id`, so gets `"default"` -
        and the version with `entry_id != "default"` is the only one that
        requires a higher schema version.

        That this also works against an old 8.1.2 server is verified, not
        hoped for: its argument resolution runs with `strict=False`
        (`matter_server/common/helpers/api.py:51,57`) and silently discards
        unknown keys instead of rejecting the call. This is the point where
        the two-part nature of this migration - first the library, then the
        server image - could have failed.
        """
        await self._require_upstream().set_thread_operational_dataset(dataset)
        self._thread_dataset_set = True

    async def send(self, call: DeviceCall) -> None:
        """Executes a translated `DeviceCall` over the upstream.

        `MatterClient.send_device_command()` does not expect a triple of
        cluster ID, command ID and a raw payload dict, but a command
        object from `chip.clusters.Objects` - the same SDK library that
        `matter_server.client.client` itself imports unchanged (see its
        `from chip.clusters import Objects as Clusters`).
        `chip.clusters.ClusterObjects.ALL_ACCEPTED_COMMANDS[cluster_id]
        [command_id]` is the complete table of these classes maintained by
        the SDK itself - precisely the source matter-server also uses
        internally for the same mapping. It is only populated by the
        import of `chip.clusters.Objects` (a side effect of the class
        definitions in it), hence the explicit import here rather than a
        plain `from chip.clusters import ClusterObjects`.

        The field names from `commands/translate.py` (e.g. `level`,
        `transitionTime`, `colorTemperatureMireds`) are deliberately named
        identically to the dataclass fields of the respective command
        class - see `test_send_passes_the_payload_as_command_fields`.
        """
        upstream = self._require_upstream()

        # Lazily imported like _default_session_factory: tests with a
        # fake upstream should never need to load chip.clusters.
        import chip.clusters.Objects  # noqa: F401 — needed only for the side effect
        from chip.clusters import ClusterObjects

        cluster_commands = ClusterObjects.ALL_ACCEPTED_COMMANDS.get(call.cluster_id)
        command_cls = cluster_commands.get(call.command_id) if cluster_commands else None
        if command_cls is None:
            raise MatterUnavailableError(
                i18n.t(
                    "api.errors.command_unknown_to_sdk",
                    cluster_id=call.cluster_id,
                    command_id=call.command_id,
                )
            )
        command = command_cls(**call.payload)
        await upstream.send_device_command(int(call.address), call.endpoint, command)

    def _subscribe_attribute_paths(
        self,
        upstream: Any,
        queue: asyncio.Queue[_QueueItem],
        node_id: int,
        paths: Iterable[str],
    ) -> int:
        """Creates one attribute subscription per not-yet-subscribed
        (node, path) pair and returns their count.

        One spot for both callers (`subscribe` and `follow_node`): two
        spots that rebuild the same registration scheme drift apart
        sooner or later - and that would not be noticed here, because a
        missing subscription is not an error, just silence.

        `queue` comes in as a parameter instead of from `self._queue`, so
        that `subscribe` can pass it before it is stored in the field -
        and so that no not-`None` check is needed here, whose narrowing
        would not carry through the closure below anyway.
        """
        # Lazily imported like everywhere in this file: tests with a fake
        # upstream should never need to load matter_server.
        from matter_server.common.models import EventType

        added = 0
        for path in paths:
            if (node_id, path) in self._subscribed_paths:
                continue

            # Default arguments bind node_id/path per loop iteration,
            # instead of evaluating the name from the enclosing scope too
            # late (the classic closure trap in a loop).
            def on_attribute_event(
                _event: Any, data: Any, node_id: int = node_id, path: str = path
            ) -> None:
                queue.put_nowait(_AttributeUpdate(node_id, path, data))

            self._unsubscribers.append(
                upstream.subscribe_events(
                    on_attribute_event,
                    event_filter=EventType.ATTRIBUTE_UPDATED,
                    node_filter=node_id,
                    attr_path_filter=path,
                )
            )
            self._subscribed_paths.add((node_id, path))
            added += 1
        return added

    async def subscribe(
        self,
        resolve_device_id: Callable[[int], int | None],
        handler: RuntimeEventHandler,
    ) -> None:
        """Reports attribute and event changes as well as reachability to `handler`.

        `resolve_device_id` maps a node ID to the store's stable
        `device_id` (e.g. `Store.device_id_for`) - exactly this
        mapping happens here, BEFORE `handler` sees anything, because the
        keys in Loxone hang off the `device_id`, not the node ID (see the
        module docstring, `Store` and the task 8 report). If
        `resolve_device_id` returns `None` (node not yet exported/
        registered, or removed in the meantime), the update is discarded -
        the same way `Runtime._signal_for` already does for an unknown
        signal path.

        `handler` satisfies `RuntimeEventHandler` - `Runtime` itself fits
        unchanged.

        See the module docstring for the rationale of the registration
        scheme (one wildcard subscription for node events/reachability,
        one subscription per attribute path known at call time) and its
        limit.
        """
        upstream = self._require_upstream()
        if self._dispatch_task is not None:
            raise MatterUnavailableError(i18n.t("api.errors.subscribe_already_called"))

        # Lazily imported like _default_session_factory: tests with a
        # fake upstream should never need to load matter_server.
        from matter_server.common.models import EventType

        queue: asyncio.Queue[_QueueItem] = asyncio.Queue()

        def on_node_or_availability_event(event: Any, data: Any) -> None:
            if event is EventType.NODE_EVENT:
                queue.put_nowait(
                    _EventUpdate(
                        data.node_id, f"{data.endpoint_id}/{data.cluster_id}/{data.event_id}"
                    )
                )
            elif event in (EventType.NODE_ADDED, EventType.NODE_UPDATED):
                queue.put_nowait(_AvailabilityUpdate(data.node_id, data.available))
                # In addition to the reachability update, not instead of
                # it: both notifications carry the same cause, but one
                # path sets `d<id>_online`, the other catches up
                # subscriptions.
                queue.put_nowait(_FollowNode(data.node_id))
            elif event is EventType.NODE_REMOVED:
                # data here is the bare node ID (not a node object) - see
                # MatterClient._handle_event_message.
                queue.put_nowait(_AvailabilityUpdate(data, False))

        self._unsubscribers = [upstream.subscribe_events(on_node_or_availability_event)]
        self._subscribed_paths = set()
        self._queue = queue
        self._handler = handler
        self._resolve_device_id = resolve_device_id

        # Attribute updates: see the module docstring for why this only
        # works per (node, path) pair known at this point. Whatever is
        # added after this call is caught up by `follow_node`.
        for node in upstream.get_nodes():
            self._subscribe_attribute_paths(
                upstream, queue, node.node_id, node.node_data.attributes
            )

        self._dispatch_task = asyncio.create_task(
            self._dispatch_loop(queue, resolve_device_id, handler)
        )

    async def follow_node(self, node_id: int, *, seed_even_without_new_paths: bool = False) -> None:
        """Catches up a node's attribute subscriptions.

        Two callers, one operation: the commissioning route
        (`api/devices.py`) after registering a new device, and
        `_dispatch_loop` on `NODE_ADDED`/`NODE_UPDATED` for a device that
        subsequently reports new paths.

        **Why the route cannot simply wait for the event:** matter-server
        reports `NODE_ADDED` while `commission_with_code` is still running
        (`device_controller._setup_node` signals it before the call
        returns). At this point the store does not yet know the node,
        `resolve_device_id` returns `None`, and for a device quietly
        sitting on the network no second notification follows. Recorded
        on 2026-09-04 on the running stack: node 8 finished commissioning
        at 11:15:33, complete with "Subscription succeeded" - all of it
        before the return to the route.

        The empty diff is the normal case and costs nothing: `NODE_UPDATED`
        also fires on every change of reachability and after every
        re-subscription, and for a device with no new paths, the process
        ends before the handler (and thus before the store).

        **`seed_even_without_new_paths` skips exactly this early exit** -
        and without the flag, seeding a freshly commissioned device would
        never take place. The sequence of events that forces this:

        1. The route is still waiting on `commission_with_code`.
        2. matter-server sends `NODE_ADDED` over the same websocket BEFORE
           the command result arrives; `MatterClient._handle_event_message`
           puts the node, complete with full `attributes`, into its cache
           and only then calls the callbacks.
        3. The dispatch task runs while the route is still waiting: its
           `follow_node` finds the node in the cache and subscribes to ALL
           of its paths. `resolve_device_id` returns `None` (the store
           does not yet know the node), so the handler is left out.
        4. The route returns, registers the device, and catches up -
           now the diff is empty, `added == 0`, and the early exit would
           come before `resolve_device_id`.

        For the call from the route, the purpose of the early exit (no
        store write load from the frequent `NODE_UPDATED`) does not apply:
        it happens once per commissioning, and seeding is the actual point
        of the call there. Without it, a freshly commissioned device would
        keep showing a dash for every static path - voltage with no load,
        battery level, a plug's off state - until the value changes for
        the first time; for some, never, because matter-server suppresses
        unchanged values.

        What is forced here is the seeding, not the invention of a
        device_id: if the store does not know the node, the handler is
        left out even with the flag.

        **`_seed_pending` answers a different question than the flag** -
        both are needed, neither replaces the other. The flag is the
        caller that knows it has just registered the device; the set is
        the bridge that remembers it still owes a node a snapshot. A node
        enters it when it could not (yet) be matched to a device_id, or
        when the handler threw while seeding, and only leaves it once the
        snapshot has arrived - which is why even an empty diff without the
        flag leads through to the handler, as long as the debt is
        outstanding.

        Without the set, the commissioning route's self-healing promise
        would only hold halfway: it applies to a `follow_node` that fails
        BEFORE it has subscribed. If it fails AFTER that -
        `resolve_device_id` reads from SQLite, the handler writes there,
        both can be hit by the write load of the resend loop - every
        later call from the dispatch loop would find an empty diff and
        turn back before the handler. The device would permanently stay
        without startup values, and no one would find out.

        `_subscribed_paths` remains untouched by this: the subscriptions
        with the upstream persist, and if their bookkeeping were
        discarded, the next call would create a SECOND subscription per
        path - every value would arrive twice, and for an event signal
        the counter would additionally count up twice.

        Called before `subscribe()`, the method does nothing rather than
        raising: the commissioning route calls it unconditionally, and a
        setup without a subscription should not fail because of that.
        """
        queue = self._queue
        handler = self._handler
        resolve_device_id = self._resolve_device_id
        if queue is None or handler is None or resolve_device_id is None:
            logger.debug(
                "follow_node(%s) without a prior subscribe() - nothing to catch up", node_id
            )
            return

        upstream = self._require_upstream()
        node = next((n for n in upstream.get_nodes() if n.node_id == node_id), None)
        if node is None:
            logger.info(
                "node %s is not known to matter-server - no subscriptions caught up", node_id
            )
            return

        attributes = node.node_data.attributes
        added = self._subscribe_attribute_paths(upstream, queue, node_id, attributes)
        if added == 0 and not seed_even_without_new_paths and node_id not in self._seed_pending:
            return

        device_id = resolve_device_id(node_id)
        if device_id is None:
            # The subscriptions remain in place, and the debt is noted:
            # once the store knows the node, the next `follow_node` will
            # catch up the snapshot - even without a new path and without
            # the flag.
            self._seed_pending.add(node_id)
            logger.debug("node %s is not matched to a device - only subscribed", node_id)
            return

        # Register first, then seed, and only clear the entry after
        # success: if the handler throws - it writes into the store via
        # `Runtime.on_node_snapshot` - or the call is cancelled, the debt
        # stays outstanding, and the next `follow_node` catches it up. The
        # exception propagates unchanged; what happens to it is the
        # caller's decision.
        self._seed_pending.add(node_id)
        await handler.on_node_snapshot(
            device_id,
            NodeSnapshot.from_raw(node_id, {"attributes": attributes, "available": node.available}),
        )
        self._seed_pending.discard(node_id)

    async def _dispatch_loop(
        self,
        queue: asyncio.Queue[_QueueItem],
        resolve_device_id: Callable[[int], int | None],
        handler: RuntimeEventHandler,
    ) -> None:
        while True:
            item = await queue.get()
            try:
                if isinstance(item, _FollowNode):
                    # BEFORE the device_id resolution: `follow_node` also
                    # creates subscriptions for a node the store does not
                    # (yet) know, and decides itself whether the handler
                    # gets to see anything.
                    await self.follow_node(item.node_id)
                    continue
                device_id = resolve_device_id(item.node_id)
                if device_id is None:
                    logger.debug("discarding update for unknown node %s", item.node_id)
                    continue
                if isinstance(item, _AttributeUpdate):
                    await handler.on_attribute(device_id, item.path, item.raw)
                elif isinstance(item, _EventUpdate):
                    await handler.on_event(device_id, item.path)
                else:
                    await handler.set_online(device_id, item.available)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failure on a single update must not end delivery as a
                # whole - analogous to Runtime._heartbeat_loop/_resend_loop.
                logger.exception("delivery of a Matter update failed")
