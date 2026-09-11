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

"""Shared fixtures for the WebUI API tests (Phase 5).

Every task in this phase that builds an `httpx2` client against `build_app`
needs the same three things: an invoker that never actually sends a Matter
command, a `Runtime` that needs no real UDP sender, and a Matter client that
gets by without a network. `no_invoke`, `fake_runtime` and `fake_client` are
built for that as standalone `@pytest.fixture` functions rather than module
functions to import by hand: pytest gives reusability via its built-in
fixture inheritance for free - every test file under `tests/api/` picks them
up automatically as a parameter, with no import at all.

`load_snapshot` is the one exception: a fixture cannot take a filename, so
it stays a plain function, imported via `from conftest import
load_snapshot` - that works because pytest already puts this file's
directory (`tests/api/`, with no `__init__.py`) at the front of `sys.path`
when it collects test files (see the rest of the test suite, which also
gets by without `__init__.py` packages).

Extension for later tasks in this phase: `fake_runtime` already takes
`store` the way the real `Runtime` does, and `FakeMatterClient` collects its
calls in lists the way `FakeUpstream` does in
`tests/matter/test_client_commissioning.py` - for a test that needs a
failure simulation, it's enough to set `fake_client.fail_commission_with =
CommissioningError(...)` before the call, with no need to touch this file.
A button device loads via `load_snapshot("ikea_bilresa_button.json")`.

`plug_store` and `api_with_runtime` (Task 3, live values): some tests need a
REAL `Runtime` instead of `FakeRuntime` - e.g. any test of the observer
wiring (`Runtime.add_observer`), since only the real `Runtime` knows about
observers at all. `plug_store` is the basis for that: the same setup as the
`api` fixture in `test_devices.py`, but without already building an app/a
client, so that tests in the style of `tests/loxone/test_runtime.py`, which
only need the store, can use it too. `api_with_runtime` builds the app on
top of that, including the WebSocket route (`/api/live`), and returns a
client that offers `websocket_connect` in addition to the usual HTTP
methods.

**Why `websocket_connect` doesn't use `httpx2` itself:** `httpx2` offers,
with `.websocket()`/`ASGIWebSocketTransport`, fundamentally the same
in-process WebSocket test mechanism - but it holds its `anyio` task group
open across the whole connection, from connect to disconnect. Under
`pytest-asyncio`, setup and teardown of an async-generator fixture (the
`yield` below) demonstrably run in TWO different `asyncio.Task` objects,
even within the same event loop - but `anyio`'s `CancelScope` strictly
requires the same task for entry and exit and otherwise raises
`RuntimeError: Attempted to exit cancel scope in a different task than it
was entered in` (reproduced: even a one-line `async with
client.websocket(...): pass` in a single test is enough, independent of the
rest of this file). `_InProcessWebSocket` below works around that by
getting by without any `anyio` task groups at all - just an ASGI app as an
`asyncio.Task` plus two `asyncio.Queue`s, living entirely within the ONE
task that runs the given test (`__aenter__` and `__aexit__` are both called
directly from the test body, never across a fixture boundary)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Self

import httpx2 as httpx
import pytest

from loxmatter.auth.passwords import hash_password
from loxmatter.commands.translate import MatterCall
from loxmatter.diagnostics.logbuffer import install_log_buffer
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.sender import UdpSender
from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load_snapshot(name: str) -> NodeSnapshot:
    """Loads a recorded device from `tests/fixtures/nodes/`."""
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


# The password every test fixture logs in with. A fixed value rather than a
# random one: it shows up in failure messages of failing tests, and there
# "test-passwort" is more helpful than a random string.
TEST_PASSWORD = "test-passwort"


async def authenticate(store: Store, client: httpx.AsyncClient) -> None:
    """Sets a password and logs `client` in.

    Needed ever since the guard stopped letting anything through without
    proof (Spec 4): a test fixture that calls `/api` must be logged in like
    a browser. `httpx.AsyncClient` carries its own cookie store, so a single
    call here is enough for every following request from the same client."""
    store.auth.set_password_hash(hash_password(TEST_PASSWORD))
    response = await client.post("/auth/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 200, "Login in the test fixture failed"


@pytest.fixture
def no_invoke():
    """An invoker that satisfies `build_app` but is never actually needed -
    the device API doesn't trigger any `/cmd` calls. If a test does call it
    anyway, it does nothing instead of sending to a real device."""

    async def _invoke(call: MatterCall) -> None:
        return None

    return _invoke


class FakeRuntime:
    """Satisfies `api.devices.RuntimeValues` without setting up a UdpSender
    or a Matter subscription - for tests that only exercise the device API.
    The full `Runtime` (sender, pulses, heartbeat) has its own test suite
    under `tests/loxone/test_runtime.py`.

    `store` is accepted but (still) unused - purely so the factory has the
    same shape as `Runtime(store, sender)`, in case a later task ever needs
    to look it up here after all."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._values: dict[str, float | bool] = {}
        # In the style of `FakeMatterClient.fail_commission_with`: the real
        # `Runtime.set_online` sends a UDP datagram, and `socket.sendto`
        # raises `OSError` when the Miniserver network is briefly down. A
        # test that needs that case sets an exception here.
        self.fail_set_online_with: Exception | None = None
        # Same pattern for the full resend (`POST
        # /api/diagnostics/resync`): `Runtime.resend_all` sends over the
        # same sender and fails at the same places.
        self.fail_resend_with: Exception | None = None
        # "Last heard" per device id, as `Runtime._last_heard` keeps it.
        # Empty means "nothing heard since startup" - a test that wants to
        # trace the timestamp all the way into the JSON response sets it here.
        self.last_heard: dict[int, str] = {}
        # What `resend_all` reports as its count, and how often it was
        # called - a test that traces the number all the way into the
        # response sets the first, a test of the wiring reads the second.
        self.resend_result = 0
        self.resend_calls = 0

    def seed(self, key: str, value: float | bool) -> None:
        """Records a value as if a subscription had just reported it."""
        self._values[key] = value

    def last_values_for(self, device_id: int) -> dict[str, float | bool]:
        prefix = f"d{device_id}_"
        return {k: v for k, v in self._values.items() if k.startswith(prefix)}

    def last_heard_for(self, device_id: int) -> str | None:
        """Like `Runtime.last_heard_for`: the timestamp of the last receipt,
        or `None` for as long as nothing has come from this device.

        The timestamps live in `last_heard` and are set by the test. Until
        the final review this method returned a hard `None` - so not a single
        test under `tests/api/` checked whether `api/devices.py` reads the
        value at all and passes it on. Anyone who had replaced the call there
        with a fixed `last_heard=None` would have got through; whether
        `Runtime` keeps the value correctly (its own suite under
        `tests/loxone/test_runtime.py`) does not answer that question."""
        return self.last_heard.get(device_id)

    async def set_online(self, device_id: int, online: bool) -> None:
        """Like `Runtime.set_online`, without the UDP send: holds the value
        under the same key that `_device_out` reads. Needed ever since
        commissioning started seeding a freshly commissioned device's
        reachability itself (see `api/devices.py`)."""
        if self.fail_set_online_with is not None:
            raise self.fail_set_online_with
        self._values[f"d{device_id}_online"] = online

    async def resend_all(self) -> int:
        """Like `Runtime.resend_all`, without the UDP send: only reports how
        many values would have gone out. Needed ever since the system tab
        got a resync button (`POST /api/diagnostics/resync`) - `build_app`
        already required `resend_all` for `/resync`, but no test fixture
        here had ever called it."""
        if self.fail_resend_with is not None:
            raise self.fail_resend_with
        self.resend_calls += 1
        return self.resend_result


@pytest.fixture
def fake_runtime():
    """A factory rather than a finished object: the store is only known
    inside the given test (see the `api` fixture in `test_devices.py`)."""
    return FakeRuntime


class FakeMatterClient:
    """Satisfies exactly the three `BridgeMatterClient` methods that the
    device API calls: commission, remove, thread dataset. The same
    recording pattern as `FakeUpstream` in
    `tests/matter/test_client_commissioning.py`, just at the level of
    `BridgeMatterClient` instead of its `session_factory` seam - the device
    API calls `BridgeMatterClient` directly, not its upstream.
    """

    def __init__(self) -> None:
        self.commissioned: list[str] = []
        self.removed: list[int] = []
        self.datasets: list[str] = []
        self.fail_commission_with: Exception | None = None
        self.fail_remove_with: Exception | None = None
        # The case this branch is built around: matter-server restarts
        # right after commissioning, and `follow_node` runs into
        # `MatterUnavailableError` - after the device is already in the
        # fabric AND in the store.
        self.fail_follow_with: Exception | None = None
        self._next_node_id = 100
        # For the diagnostics system check (Task 6, Phase 5) - mirrors
        # `BridgeMatterClient.connected`. A test that wants to simulate a
        # dropped connection simply sets it to `False`, with no need to
        # touch this file (see the conftest module docstring, "Extension
        # for later tasks").
        self.connected = True
        # What `commission_with_code` reports as the reachability of the
        # freshly commissioned node - on the real client it comes from
        # `MatterNodeData.available`. A test that wants to simulate a
        # device matter-server can't reach sets it to `False`.
        self.available = True
        # Mirrors `BridgeMatterClient.thread_dataset_set`: whether
        # matter-server currently holds the Thread credentials. `False` by
        # default, because that is the state after every restart of the
        # service - exactly the one in which commissioning a Thread device
        # used to fail.
        self.thread_dataset_set = False
        # The order of the calls: the dataset must be set BEFORE
        # commissioning, or it arrives too late for this device.
        self.order: list[str] = []
        # The node IDs for which the route has triggered catching up on
        # subscriptions (`BridgeMatterClient.follow_node`).
        self.followed: list[int] = []
        # The store `follow_node` checks against to see whether the device
        # was already registered at the time of the call. The `api` fixture
        # sets it; without it, `follow_node` only records the call.
        self.store: Store | None = None
        self.followed_resolved: list[int | None] = []
        # Whether the given call forced the seeding
        # (`seed_even_without_new_paths`). For the route this isn't
        # incidental: by the time it catches up, the dispatch loop has long
        # since subscribed to the new node's paths - without the switch it
        # would find an empty diff and never seed (see
        # `BridgeMatterClient.follow_node`).
        self.followed_forced: list[bool] = []
        # The snapshot `commission_with_code` returns when set (category on
        # the freshly commissioned device, Task 5 device tab): the stub
        # snapshot below carries `attributes={}`, so `category_for` always
        # returns `OTHER` - a test that wants to see a different category
        # after commissioning needs a real, recorded snapshot
        # (`load_snapshot`). `None` (the default) leaves the existing
        # behavior unchanged.
        self.snapshot_to_return: NodeSnapshot | None = None

    async def commission_with_code(self, code: str) -> NodeSnapshot:
        if self.fail_commission_with is not None:
            raise self.fail_commission_with
        self.commissioned.append(code)
        self.order.append("commission")
        if self.snapshot_to_return is not None:
            return self.snapshot_to_return
        node_id = self._next_node_id
        self._next_node_id += 1
        return NodeSnapshot(
            technology="matter",
            address=str(node_id),
            vendor_name="Fake",
            product_name="Device",
            unique_id=f"fake-{node_id}",
            attributes={},
            available=self.available,
        )

    async def remove_node(self, node_id: int) -> None:
        if self.fail_remove_with is not None:
            raise self.fail_remove_with
        self.removed.append(node_id)

    async def set_thread_dataset(self, dataset: str) -> None:
        self.datasets.append(dataset)
        self.order.append("dataset")
        self.thread_dataset_set = True

    async def follow_node(self, node_id: int, *, seed_even_without_new_paths: bool = False) -> None:
        if self.fail_follow_with is not None:
            raise self.fail_follow_with
        self.followed.append(node_id)
        self.followed_forced.append(seed_even_without_new_paths)
        self.order.append("follow")
        # The actual point: the real `BridgeMatterClient.follow_node`
        # resolves the node ID via the store and, without a match, does
        # nothing but subscribe. If it isn't resolved here, the route
        # catches up too early - the same race the NODE_ADDED event already
        # lost.
        self.followed_resolved.append(
            None
            if self.store is None
            else self.store.device_id_for("matter", str(node_id))  # TRANSITIONAL (Task 5)
        )


@pytest.fixture
def fake_client():
    return FakeMatterClient()


class FakeThreadDatasetSource:
    """Stands in for `loxmatter.matter.otbr.fetch_active_dataset` - the
    source commissioning fetches the Thread dataset from when matter-server
    doesn't (or no longer) has it. Counts the calls so a test can prove that
    the border router was NOT asked."""

    def __init__(self) -> None:
        # Shaped like a real one (hex TLV), but with no relation to any
        # existing network - a real dataset is a credential and belongs in
        # neither the repository nor a log.
        self.dataset = "0e08000000000001" + "00" * 24
        self.calls = 0
        self.fail_with: Exception | None = None

    async def __call__(self) -> str:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return self.dataset


@pytest.fixture
def fake_otbr():
    return FakeThreadDatasetSource()


@pytest.fixture
def plug_store(tmp_path):
    """A `Store` with the IKEA plug, registered the way a real commissioning
    would (device, signals, output commands) - the basis for tests that
    need a REAL `Runtime` instead of `FakeRuntime` (e.g. the observer
    wiring from Task 3). Returns `(store, device_id)`, the way `environment`
    does in `tests/loxone/test_runtime.py`."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot))
    yield store, device_id
    store.close()


class _InProcessWebSocket:
    """Minimal in-process ASGI WebSocket test client - see the module
    docstring for why this isn't simply `httpx2.AsyncClient.websocket`.
    Drives the ASGI app as its own `asyncio.Task`, connected via two
    `asyncio.Queue`s (inbound/outbound) - with no `anyio` task groups that
    would have to survive a fixture boundary.

    Implements only what this test suite needs: connect, `receive_json`,
    disconnect cleanly. No text/bytes send, no ping/pong - the WebUI sends
    nothing on this route, it only listens (see `api/live.py`).

    `break_send_after` (review fix Important #2 in `api/live.py`,
    2026-09-02): purely additive, `None` (the default) changes nothing about
    the behavior above. When set, it makes the ASGI `send` caller itself
    raise a `RuntimeError` once more than `break_send_after`
    `websocket.send` messages have gone through - simulating exactly the
    case from the module docstring of `api/live.py`: an ASGI layer that, on
    send to an already-lost connection, raises a `RuntimeError` rather than
    a `WebSocketDisconnect`. Without this tool this path couldn't be
    reached at all in this in-process harness: `_from_app` below is
    unbounded, so a test that simply doesn't read produces no real send
    failure here - unlike a real client blocking on a full TCP send buffer.

    `cookies` (Task 8, Phase 5): the session cookie that `WebSocketClient`
    has already logged in with via `authenticate()` does NOT travel here on
    its own - this scope is built by hand, not derived from a real
    connection that automatically carries a browser's cookie header along.
    Without this parameter, every test that uses `websocket_connect` would
    fail at the guard closed since Task 8, even though `client` has long
    since logged in."""

    def __init__(
        self,
        app: Any,
        path: str,
        *,
        break_send_after: int | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self._app = app
        self._path = path
        self._to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._break_send_after = break_send_after
        self._sends_before_break = 0
        self._cookies = cookies or {}

    async def __aenter__(self) -> Self:
        headers: list[tuple[bytes, bytes]] = []
        if self._cookies:
            cookie_header = "; ".join(f"{name}={value}" for name, value in self._cookies.items())
            headers.append((b"cookie", cookie_header.encode()))
        scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "scheme": "ws",
            "path": self._path,
            "raw_path": self._path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": headers,
            "client": ("testclient", 123),
            "server": ("testserver", 80),
            "subprotocols": [],
            "state": {},
            "extensions": {"websocket.http.response": {}},
        }

        async def receive() -> dict[str, Any]:
            return await self._to_app.get()

        async def send(message: dict[str, Any]) -> None:
            if self._break_send_after is not None and message["type"] == "websocket.send":
                if self._sends_before_break >= self._break_send_after:
                    raise RuntimeError('Cannot call "send" once a close message has been sent.')
                self._sends_before_break += 1
            await self._from_app.put(message)

        self._task = asyncio.create_task(self._app(scope, receive, send))
        await self._to_app.put({"type": "websocket.connect"})
        message = await self._from_app.get()
        if message["type"] != "websocket.accept":
            raise AssertionError(f"WebSocket was not accepted: {message!r}")
        return self

    async def receive_json(self) -> Any:
        message = await self._from_app.get()
        if message["type"] == "websocket.close":
            raise AssertionError(
                "WebSocket was disconnected by the server before a message arrived"
            )
        return json.loads(message["text"])

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        assert self._task is not None
        try:
            await asyncio.wait_for(self._task, timeout=2)
        except TimeoutError:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def wait_closed(self, timeout: float = 2) -> None:
        """Waits until the SERVER side ends the connection on its own -
        without itself sending a `websocket.disconnect`, unlike
        `__aexit__`. For tests that want to check that the route cleans up
        after itself (e.g. after a simulated send failure via
        `break_send_after`), rather than the client triggering the
        disconnect."""
        assert self._task is not None
        await asyncio.wait_for(self._task, timeout=timeout)


class WebSocketClient:
    """Thin wrapper around `httpx2.AsyncClient` that additionally offers
    `websocket_connect` (see `_InProcessWebSocket`). Every other call
    (`get`, `post`, `patch`, ...) is passed through unchanged to the
    underlying client, so that `api_with_runtime` works equally well for
    REST and WebSocket tests."""

    def __init__(self, client: httpx.AsyncClient, app: Any) -> None:
        self._client = client
        self._app = app

    def websocket_connect(
        self, url: str, *, break_send_after: int | None = None
    ) -> _InProcessWebSocket:
        # `dict(self._client.cookies)` rather than the cookie jar itself:
        # the session cookie from `authenticate()` should travel along
        # unchanged, the way it would for a real browser WebSocket from the
        # same origin (see the `_InProcessWebSocket` docstring).
        return _InProcessWebSocket(
            self._app,
            url,
            break_send_after=break_send_after,
            cookies=dict(self._client.cookies),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


@pytest.fixture
async def api_with_runtime(
    plug_store, no_invoke, fake_client
) -> AsyncIterator[tuple[WebSocketClient, Runtime, int]]:
    """Like the `api` fixture in `test_devices.py`, but with a REAL
    `Runtime` instead of `FakeRuntime` - for tests of the observer wiring
    and the WebSocket routes `/api/live` (Task 3, Spec 8.3) and
    `/api/diagnostics/live` (Task 4, Spec 10.5).

    **A REAL `UdpSender` instead of a filler object** (unlike still in
    Task 3) - `api.diagnostics_live.build_diagnostics_live_router` hangs off
    `sender.add_datagram_observer` (Task 2), and this branch hangs off
    `UdpSender.send`'s recording (`_record_sent`), not `Runtime`'s observer
    chain (see there). A fake sender that only satisfies `send()`/`close()`
    would never trigger that recording -
    `test_a_fresh_datagram_arrives_as_a_message` therefore needs the same
    setup as `api_with_sender` in `test_diagnostics.py` (a UDP socket on
    `127.0.0.1` that never leaves the machine, port `0` for a free port
    assigned by the operating system).

    **`install_log_buffer()` for the same reason** - the log branch of the
    new route (`log_handler.add_observer`, Task 3) needs a real
    `LogBufferHandler`, attached to the `loxmatter` logger. It is
    unregistered again after the test, AND the logger's level is reset -
    otherwise every test that uses this fixture would pile up yet another
    handler on the same, PROCESS-WIDE logger, and its level would stay
    stuck at INFO (see `tests/diagnostics/test_logbuffer.py` for the same
    pattern)."""
    store, device_id = plug_store
    loxmatter_logger = logging.getLogger("loxmatter")
    previous_level = loxmatter_logger.level
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.setblocking(False)
    host, port = receiver.getsockname()
    sender = UdpSender(host, port)
    runtime = Runtime(store, sender)
    log_handler = install_log_buffer()
    app = build_app(
        store,
        no_invoke,
        runtime,
        client=fake_client,
        sender=sender,
        log_handler=log_handler,
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await authenticate(store, client)
            yield WebSocketClient(client, app), runtime, device_id
    finally:
        await sender.close()
        loxmatter_logger.removeHandler(log_handler)
        # Also reset the LEVEL (2026-09-03): `install_log_buffer` has, since
        # Task 3, set not just the handler's level but the logger's too -
        # otherwise `loxmatter` stayed stuck at INFO after this fixture,
        # even though the handler had long since been unregistered. A test
        # that runs later and expects a different level would then see
        # something no test had set.
        loxmatter_logger.setLevel(previous_level)
        receiver.close()
