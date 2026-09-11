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

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from matter_server.common.models import EventType, MatterNodeEvent

from loxmatter import i18n
from loxmatter.matter import client as client_module
from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError
from loxmatter.matter.models import NodeSnapshot
from loxmatter.sources import DeviceCall


class FakeNode:
    """Stands in for matter_server.client.models.node.MatterNode.

    The real MatterNode does not carry its raw attributes directly, but
    under node_data.attributes — node_id, however, stays an attribute
    directly on the node (there a property over node_data.node_id). This
    stand-in reproduces exactly this shape, instead of flattening it for
    simplicity. `available` in reality also sits on node_data (property
    `MatterNode.available`).
    """

    def __init__(self, node_id: int, attributes: dict[str, object], *, available: bool = True):
        self.node_id = node_id
        self.available = available
        self.node_data = SimpleNamespace(attributes=attributes, available=available)


class FakeUpstream:
    """Stands in for matter_server.client.MatterClient.

    start_listening() reproduces the real contract: it fills the node
    cache, sets init_ready (if requested) and then blocks until it is
    cancelled — exactly like MatterClient.start_listening(). get_nodes()
    deliberately returns something only after start_listening() has run: a
    test that never starts the listener must be able to reproduce the
    original failure (empty node cache).
    """

    def __init__(
        self,
        nodes: list[FakeNode] | None = None,
        fail_connect: bool = False,
        fail_disconnect: bool = False,
        signal_ready: bool = True,
    ):
        self._configured_nodes = nodes or []
        self._nodes: list[FakeNode] = []
        self.disconnect_calls = 0
        self.start_listening_calls = 0
        self.cancelled = False
        self._fail_connect = fail_connect
        self._fail_disconnect = fail_disconnect
        self._signal_ready = signal_ready
        self._subscribers: dict[str, list[Any]] = {}
        self.sent_commands: list[tuple[int, int, Any]] = []

    async def start_listening(self, init_ready: asyncio.Event | None = None) -> None:
        self.start_listening_calls += 1
        if self._fail_connect:
            raise RuntimeError("Verbindung fehlgeschlagen")
        self._nodes = self._configured_nodes
        if self._signal_ready and init_ready is not None:
            init_ready.set()
        try:
            await asyncio.Event().wait()  # blocks until cancelled
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self._fail_disconnect:
            raise RuntimeError("Trennung fehlgeschlagen")

    def get_nodes(self) -> list[FakeNode]:
        return self._nodes

    def add_node(self, node: FakeNode) -> None:
        """A device that only joins after start_listening() - on the real
        MatterClient the NODE_ADDED event fills the node cache accordingly.
        Exactly the case that `follow` covers."""
        self._nodes.append(node)

    # --- from here on: reproduction of MatterClient.subscribe_events()/
    # send_device_command() for task 8. subscribe_events() reproduces the
    # real key matching from MatterClient._signal_event() — including the
    # property that is precisely what makes BridgeMatterClient.subscribe()
    # necessary: the callback only gets (event, data), NEVER
    # node_id/attribute_path.

    def subscribe_events(
        self,
        callback: Any,
        event_filter: EventType | None = None,
        node_filter: int | None = None,
        attr_path_filter: str | None = None,
    ) -> Any:
        key = (
            f"{event_filter.value if event_filter is not None else '*'}/"
            f"{node_filter if node_filter is not None else '*'}/"
            f"{attr_path_filter if attr_path_filter is not None else '*'}"
        )
        self._subscribers.setdefault(key, []).append(callback)

        def unsubscribe() -> None:
            self._subscribers[key].remove(callback)

        return unsubscribe

    def emit(
        self,
        event: EventType,
        data: Any,
        node_id: int | None = None,
        attribute_path: str | None = None,
    ) -> None:
        """Simulates an incoming server message like
        MatterClient._signal_event() — including wildcard matching."""
        for evt_key in (event.value, "*"):
            for node_key in [node_id, "*"] if node_id is not None else ["*"]:
                for attr_key in [attribute_path, "*"] if attribute_path is not None else ["*"]:
                    key = f"{evt_key}/{node_key}/{attr_key}"
                    for cb in self._subscribers.get(key, []):
                        cb(event, data)

    async def send_device_command(
        self,
        node_id: int,
        endpoint_id: int,
        command: Any,
        response_type: Any = None,
        timed_request_timeout_ms: int | None = None,
        interaction_timeout_ms: int | None = None,
    ) -> Any:
        self.sent_commands.append((node_id, endpoint_id, command))
        return None


class FakeSession:
    """Stands in for aiohttp.ClientSession — counts how often close() ran."""

    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def make_client(
    nodes: list[FakeNode] | None = None,
    *,
    fail_connect: bool = False,
    fail_disconnect: bool = False,
    signal_ready: bool = True,
) -> tuple[BridgeMatterClient, FakeSession]:
    """Builds a BridgeMatterClient with stand-ins for the HTTP session and upstream."""
    session = FakeSession()
    upstream = FakeUpstream(
        nodes or [],
        fail_connect=fail_connect,
        fail_disconnect=fail_disconnect,
        signal_ready=signal_ready,
    )
    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=lambda: session,
    )
    return bridge, session


@pytest.fixture
def client() -> BridgeMatterClient:
    bridge, _ = make_client(
        [
            FakeNode(12, {"0/40/1": "IKEA of Sweden", "1/6/0": True}),
            FakeNode(13, {"0/40/1": "IKEA of Sweden", "1/1026/0": 2150}),
        ]
    )
    return bridge


async def test_snapshots_requires_a_connection(client):
    with pytest.raises(MatterUnavailableError, match="not connected"):
        await client.snapshots()


async def test_snapshots_requires_a_connection_in_german(client):
    """German counterpart to `test_snapshots_requires_a_connection` above."""
    i18n.set_language("de")
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await client.snapshots()


async def test_snapshots_maps_every_node(client):
    await client.connect()
    snapshots = await client.snapshots()
    assert [s.address for s in snapshots] == ["12", "13"]
    assert snapshots[0].vendor_name == "IKEA of Sweden"
    assert snapshots[1].attributes["1/1026/0"] == 2150
    assert snapshots[0].available is True


async def test_snapshots_reflect_node_availability():
    """Review fix C1, 2026-09-02: `Runtime.seed_from_snapshot` needs
    `node.available` in every snapshot to correctly seed a device as
    on-/offline at bridge startup - see the module docstring for why
    NODE_ADDED/NODE_UPDATED do not reliably fire for this."""
    bridge, _ = make_client([FakeNode(1, {"0/40/1": "Aqara"}, available=False)])
    await bridge.connect()
    snapshots = await bridge.snapshots()
    assert snapshots[0].available is False


async def test_snapshot_selects_by_node_id(client):
    await client.connect()
    assert (await client.snapshot(13)).attributes["1/1026/0"] == 2150


async def test_snapshot_raises_for_unknown_node(client):
    await client.connect()
    with pytest.raises(MatterUnavailableError, match="unknown node 99"):
        await client.snapshot(99)


async def test_snapshot_raises_for_unknown_node_in_german(client):
    """German counterpart to `test_snapshot_raises_for_unknown_node` above."""
    i18n.set_language("de")
    await client.connect()
    with pytest.raises(MatterUnavailableError, match="unbekannter Node 99"):
        await client.snapshot(99)


async def test_disconnect_is_idempotent(client):
    await client.connect()
    await client.disconnect()
    await client.disconnect()
    with pytest.raises(MatterUnavailableError):
        await client.snapshots()


async def test_connect_disconnect_closes_session_exactly_once():
    """BridgeMatterClient creates the session itself and must close it again."""
    bridge, session = make_client([FakeNode(1, {})])
    await bridge.connect()
    assert session.close_calls == 0
    await bridge.disconnect()
    assert session.close_calls == 1


async def test_disconnect_twice_closes_session_once_and_does_not_raise():
    bridge, session = make_client([FakeNode(1, {})])
    await bridge.connect()
    await bridge.disconnect()
    await bridge.disconnect()
    assert session.close_calls == 1


async def test_failed_connect_closes_session_and_allows_retry():
    """A failing connect() must not leak the session and must allow a later,
    successful connect()."""
    sessions: list[FakeSession] = []

    def http_session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    attempts = {"n": 0}

    def session_factory(_session: FakeSession) -> FakeUpstream:
        attempts["n"] += 1
        return FakeUpstream([FakeNode(1, {})], fail_connect=attempts["n"] == 1)

    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=session_factory,
        http_session_factory=http_session_factory,
    )

    with pytest.raises(RuntimeError, match="Verbindung fehlgeschlagen"):
        await bridge.connect()

    assert len(sessions) == 1
    assert sessions[0].close_calls == 1
    with pytest.raises(MatterUnavailableError, match="not connected"):
        await bridge.snapshots()

    await bridge.connect()
    snapshots = await bridge.snapshots()
    assert [s.address for s in snapshots] == ["1"]
    assert sessions[1].close_calls == 0


async def test_failed_connect_closes_session_and_allows_retry_in_german():
    """German counterpart to `test_failed_connect_closes_session_and_allows_retry`
    above. `FakeUpstream.start_listening` always raises "Verbindung fehlgeschlagen"
    as a plain RuntimeError - that is a fixture text, not a translated one,
    and therefore stays the same in both languages."""
    i18n.set_language("de")
    sessions: list[FakeSession] = []

    def http_session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    attempts = {"n": 0}

    def session_factory(_session: FakeSession) -> FakeUpstream:
        attempts["n"] += 1
        return FakeUpstream([FakeNode(1, {})], fail_connect=attempts["n"] == 1)

    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=session_factory,
        http_session_factory=http_session_factory,
    )

    with pytest.raises(RuntimeError, match="Verbindung fehlgeschlagen"):
        await bridge.connect()

    assert len(sessions) == 1
    assert sessions[0].close_calls == 1
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await bridge.snapshots()

    await bridge.connect()
    snapshots = await bridge.snapshots()
    assert [s.address for s in snapshots] == ["1"]
    assert sessions[1].close_calls == 0


async def test_connect_twice_closes_previous_session_and_does_not_leak():
    """A second connect() without an intervening disconnect() must not leave
    the first session unreachable — it must be closed before the second
    session is created."""
    sessions: list[FakeSession] = []

    def http_session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    def session_factory(_session: FakeSession) -> FakeUpstream:
        return FakeUpstream([FakeNode(1, {})])

    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=session_factory,
        http_session_factory=http_session_factory,
    )

    await bridge.connect()
    await bridge.connect()

    assert len(sessions) == 2
    assert sessions[0].close_calls == 1
    assert sessions[1].close_calls == 0
    snapshots = await bridge.snapshots()
    assert [s.address for s in snapshots] == ["1"]


async def test_disconnect_closes_session_even_if_upstream_disconnect_raises():
    """If the upstream raises in disconnect(), the session must still be
    closed and the client afterward recognizable as not connected."""
    bridge, session = make_client([FakeNode(1, {})], fail_disconnect=True)
    await bridge.connect()

    with pytest.raises(RuntimeError, match="Trennung fehlgeschlagen"):
        await bridge.disconnect()

    assert session.close_calls == 1
    with pytest.raises(MatterUnavailableError, match="not connected"):
        await bridge.snapshots()


async def test_disconnect_closes_session_even_if_upstream_disconnect_raises_in_german():
    """German counterpart to
    `test_disconnect_closes_session_even_if_upstream_disconnect_raises` above.
    "Trennung fehlgeschlagen" is a fixture text (always German, see there)."""
    i18n.set_language("de")
    bridge, session = make_client([FakeNode(1, {})], fail_disconnect=True)
    await bridge.connect()

    with pytest.raises(RuntimeError, match="Trennung fehlgeschlagen"):
        await bridge.disconnect()

    assert session.close_calls == 1
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await bridge.snapshots()


async def test_connect_cancelled_closes_session_and_propagates_cancellation():
    """asyncio.CancelledError inherits from BaseException, not Exception — a
    connect() cancelled while the connection is being established must
    still not leak the session, and must propagate the cancellation."""
    session = FakeSession()

    class CancellingUpstream:
        async def start_listening(self, init_ready: asyncio.Event | None = None) -> None:
            raise asyncio.CancelledError()

    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: CancellingUpstream(),
        http_session_factory=lambda: session,
    )

    with pytest.raises(asyncio.CancelledError):
        await bridge.connect()

    assert session.close_calls == 1


async def test_connect_times_out_when_listener_never_signals_readiness(monkeypatch):
    """The defect this test prevents: without a time limit, connect() would
    either wait forever for an event that never comes, or — worse —
    incorrectly report itself as connected without the node cache ever
    having been filled. A listener that never sets init_ready must make
    connect() fail within the time limit."""
    monkeypatch.setattr(client_module, "LISTENER_READY_TIMEOUT_SECONDS", 0.05)
    bridge, _session = make_client([FakeNode(1, {})], signal_ready=False)

    with pytest.raises(MatterUnavailableError, match="did not report readiness"):
        await bridge.connect()


async def test_connect_times_out_when_listener_never_signals_readiness_in_german(monkeypatch):
    """German counterpart to
    `test_connect_times_out_when_listener_never_signals_readiness` above."""
    i18n.set_language("de")
    monkeypatch.setattr(client_module, "LISTENER_READY_TIMEOUT_SECONDS", 0.05)
    bridge, _session = make_client([FakeNode(1, {})], signal_ready=False)

    with pytest.raises(MatterUnavailableError, match="keine Bereitschaft"):
        await bridge.connect()


async def test_connect_timeout_closes_session_and_allows_a_later_successful_connect(
    monkeypatch,
):
    """After a readiness timeout, the own session must be closed, the client
    must count as not connected, and a later connect() with a working
    upstream must still succeed."""
    monkeypatch.setattr(client_module, "LISTENER_READY_TIMEOUT_SECONDS", 0.05)
    sessions: list[FakeSession] = []

    def http_session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    attempts = {"n": 0}

    def session_factory(_session: FakeSession) -> FakeUpstream:
        attempts["n"] += 1
        return FakeUpstream([FakeNode(1, {})], signal_ready=attempts["n"] != 1)

    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=session_factory,
        http_session_factory=http_session_factory,
    )

    with pytest.raises(MatterUnavailableError, match="did not report readiness"):
        await bridge.connect()

    assert len(sessions) == 1
    assert sessions[0].close_calls == 1
    with pytest.raises(MatterUnavailableError, match="not connected"):
        await bridge.snapshots()

    await bridge.connect()
    snapshots = await bridge.snapshots()
    assert [s.address for s in snapshots] == ["1"]
    assert sessions[1].close_calls == 0


async def test_connect_timeout_closes_session_and_allows_a_later_successful_connect_in_german(
    monkeypatch,
):
    """German counterpart to
    `test_connect_timeout_closes_session_and_allows_a_later_successful_connect`
    above."""
    i18n.set_language("de")
    monkeypatch.setattr(client_module, "LISTENER_READY_TIMEOUT_SECONDS", 0.05)
    sessions: list[FakeSession] = []

    def http_session_factory() -> FakeSession:
        session = FakeSession()
        sessions.append(session)
        return session

    attempts = {"n": 0}

    def session_factory(_session: FakeSession) -> FakeUpstream:
        attempts["n"] += 1
        return FakeUpstream([FakeNode(1, {})], signal_ready=attempts["n"] != 1)

    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=session_factory,
        http_session_factory=http_session_factory,
    )

    with pytest.raises(MatterUnavailableError, match="keine Bereitschaft"):
        await bridge.connect()

    assert len(sessions) == 1
    assert sessions[0].close_calls == 1
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await bridge.snapshots()

    await bridge.connect()
    snapshots = await bridge.snapshots()
    assert [s.address for s in snapshots] == ["1"]
    assert sessions[1].close_calls == 0


async def test_disconnect_cancels_the_listener_task():
    """disconnect() must cancel the listener task instead of simply letting
    it keep running — otherwise a coroutine stays active, waiting on a
    connection that has meanwhile been closed."""
    session = FakeSession()
    upstream = FakeUpstream([FakeNode(1, {})])
    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=lambda: session,
    )
    await bridge.connect()
    assert upstream.cancelled is False

    await bridge.disconnect()

    assert upstream.cancelled is True


async def test_snapshots_reflect_nodes_populated_by_the_listener():
    """Regression test for the actual defect: the old connect() never called
    upstream.start_listening(), leaving the upstream's node cache forever
    empty — every real device appeared as unknown, no matter how many had
    been commissioned. Here, get_nodes() — like the real MatterClient —
    deliberately returns something only after start_listening() has run;
    against the old code (no call to start_listening()) this test fails."""
    bridge, _session = make_client([FakeNode(3, {"0/40/1": "Aqara", "1/6/0": True})])

    await bridge.connect()
    snapshots = await bridge.snapshots()

    assert [s.address for s in snapshots] == ["3"]
    assert snapshots[0].vendor_name == "Aqara"


class FakeHandler:
    """Stands in for Runtime (on_attribute/on_event/set_online) — Runtime
    fulfills the same protocol unchanged, see RuntimeEventHandler."""

    def __init__(self) -> None:
        self.attribute_calls: list[tuple[int, str, object]] = []
        self.event_calls: list[tuple[int, str]] = []
        self.availability_calls: list[tuple[int, bool]] = []
        self.snapshot_calls: list[tuple[int, NodeSnapshot]] = []

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None:
        self.attribute_calls.append((device_id, path, raw))

    async def on_event(self, device_id: int, path: str) -> None:
        self.event_calls.append((device_id, path))

    async def set_online(self, device_id: int, online: bool) -> None:
        self.availability_calls.append((device_id, online))

    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None:
        self.snapshot_calls.append((device_id, snapshot))


def make_connected_pair(
    nodes: list[FakeNode] | None = None,
) -> tuple[BridgeMatterClient, FakeUpstream]:
    """Like make_client(), but additionally returns the upstream stand-in —
    send()/subscribe() evaluate its sent_commands/subscribe_events(),
    which is not reachable through make_client()'s return value."""
    upstream = FakeUpstream(nodes or [])
    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=lambda: FakeSession(),
    )
    return bridge, upstream


async def _settle() -> None:
    """Lets subscribe()'s dispatch task catch up with the queue —
    put_nowait() from a synchronous callback and its processing in the
    background task otherwise land in different event-loop iterations.

    Six iterations instead of three, since a NODE_ADDED/NODE_UPDATED
    produces two entries (availability and catch-up) and the catch-up
    itself waits on the handler once more."""
    for _ in range(6):
        await asyncio.sleep(0)


def _attribute_subscriptions(upstream: FakeUpstream) -> list[str]:
    """The keys of the active attribute subscriptions, one per (node, path),
    in the form `attribute_updated/<node>/<path>`.

    Deliberately reads the stand-in's `_subscribers` directly: it thereby
    reproduces exactly the key matching of `MatterClient._signal_event()`,
    and it is exactly this registration scheme that is meant to be checked
    here."""
    prefix = f"{EventType.ATTRIBUTE_UPDATED.value}/"
    return sorted(
        key
        for key, callbacks in upstream._subscribers.items()
        if key.startswith(prefix) and callbacks
    )


# --- send() -------------------------------------------------------------


async def test_send_requires_a_connection():
    bridge, _upstream = make_connected_pair()
    call = DeviceCall(
        technology="matter", address="12", endpoint=1, cluster_id=6, command_id=1, payload={}
    )
    with pytest.raises(MatterUnavailableError, match="not connected"):
        await bridge.send(call)


async def test_send_requires_a_connection_in_german():
    """German counterpart to `test_send_requires_a_connection` above."""
    i18n.set_language("de")
    bridge, _upstream = make_connected_pair()
    call = DeviceCall(
        technology="matter", address="12", endpoint=1, cluster_id=6, command_id=1, payload={}
    )
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await bridge.send(call)


async def test_send_builds_the_real_cluster_command_from_cluster_and_command_id():
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()

    call = DeviceCall(
        technology="matter", address="12", endpoint=1, cluster_id=6, command_id=1, payload={}
    )
    await bridge.send(call)

    assert len(upstream.sent_commands) == 1
    node_id, endpoint_id, command = upstream.sent_commands[0]
    assert (node_id, endpoint_id) == (12, 1)
    # chip.clusters.Objects.OnOff.Commands.On — command_id 1 in the OnOff cluster (6).
    assert command.__class__.__name__ == "On"
    assert command.cluster_id == 6


async def test_send_passes_the_payload_as_command_fields():
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()

    # LevelControl (8) MoveToLevelWithOnOff (4) — the same field names that
    # commands/translate.py._payload_level builds.
    call = DeviceCall(
        technology="matter",
        address="12",
        endpoint=1,
        cluster_id=8,
        command_id=4,
        payload={"level": 128, "transitionTime": 0},
    )
    await bridge.send(call)

    _node_id, _endpoint_id, command = upstream.sent_commands[0]
    assert command.__class__.__name__ == "MoveToLevelWithOnOff"
    assert command.level == 128
    assert command.transitionTime == 0


async def test_send_builds_the_colour_temperature_command_from_the_sdk():
    """ColorControl (768) MoveToColorTemperature (10) through `chip`.

    `tests/commands/test_translate.py` only checks the payload dict that
    `translate.py` builds - never whether `chip.clusters.ClusterObjects.
    ALL_ACCEPTED_COMMANDS` makes a class from it with exactly these fields.
    Without this test, a renamed SDK field would silently break color
    temperature: the call would go out, the light would stay as it was.
    """
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()

    call = DeviceCall(
        technology="matter",
        address="12",
        endpoint=1,
        cluster_id=768,
        command_id=10,
        payload={
            "colorTemperatureMireds": 370,
            "optionsMask": 1,
            "optionsOverride": 1,
        },
    )
    await bridge.send(call)

    _node_id, _endpoint_id, command = upstream.sent_commands[0]
    assert command.__class__.__name__ == "MoveToColorTemperature"
    assert command.colorTemperatureMireds == 370
    # The bit without which a color command on a switched-off light has no effect
    # (see `_EXECUTE_IF_OFF` in commands/translate.py).
    assert command.optionsMask == 1
    assert command.optionsOverride == 1


async def test_send_builds_the_hue_saturation_command_from_the_sdk():
    """ColorControl (768) MoveToHueAndSaturation (6), same reason."""
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()

    call = DeviceCall(
        technology="matter",
        address="12",
        endpoint=1,
        cluster_id=768,
        command_id=6,
        payload={
            "hue": 85,
            "saturation": 254,
            "transitionTime": 0,
            "optionsMask": 1,
            "optionsOverride": 1,
        },
    )
    await bridge.send(call)

    _node_id, _endpoint_id, command = upstream.sent_commands[0]
    assert command.__class__.__name__ == "MoveToHueAndSaturation"
    assert command.hue == 85
    assert command.saturation == 254


async def test_send_raises_for_a_cluster_command_the_sdk_does_not_know():
    bridge, _upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()

    call = DeviceCall(
        technology="matter", address="12", endpoint=1, cluster_id=9999, command_id=1, payload={}
    )
    with pytest.raises(MatterUnavailableError, match="9999"):
        await bridge.send(call)


# --- subscribe() --------------------------------------------------------


async def test_subscribe_requires_a_connection():
    bridge, _upstream = make_connected_pair()
    with pytest.raises(MatterUnavailableError, match="not connected"):
        await bridge.subscribe(lambda _node_id: 1, FakeHandler())


async def test_subscribe_requires_a_connection_in_german():
    """German counterpart to `test_subscribe_requires_a_connection` above."""
    i18n.set_language("de")
    bridge, _upstream = make_connected_pair()
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await bridge.subscribe(lambda _node_id: 1, FakeHandler())


async def test_subscribe_twice_raises():
    bridge, _upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    await bridge.subscribe(lambda _node_id: 5, FakeHandler())
    with pytest.raises(MatterUnavailableError, match="already"):
        await bridge.subscribe(lambda _node_id: 5, FakeHandler())


async def test_subscribe_twice_raises_in_german():
    """German counterpart to `test_subscribe_twice_raises` above."""
    i18n.set_language("de")
    bridge, _upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    await bridge.subscribe(lambda _node_id: 5, FakeHandler())
    with pytest.raises(MatterUnavailableError, match="bereits"):
        await bridge.subscribe(lambda _node_id: 5, FakeHandler())


async def test_subscribe_maps_an_attribute_update_to_the_resolved_device_id():
    bridge, upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()
    handler = FakeHandler()

    await bridge.subscribe(lambda address: {"12": 5}.get(address), handler)
    upstream.emit(EventType.ATTRIBUTE_UPDATED, False, node_id=12, attribute_path="1/6/0")
    await _settle()

    assert handler.attribute_calls == [(5, "1/6/0", False)]


async def test_subscribe_drops_an_update_for_a_node_the_resolver_does_not_know():
    """A device that is not yet exported or has been removed returns `None`
    — the update is dropped, not delivered with a wrong device_id."""
    bridge, upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()
    handler = FakeHandler()

    await bridge.subscribe(lambda _node_id: None, handler)
    upstream.emit(EventType.ATTRIBUTE_UPDATED, True, node_id=12, attribute_path="1/6/0")
    await _settle()

    assert handler.attribute_calls == []


async def test_subscribe_only_delivers_updates_for_the_exact_path_subscribed():
    """An attribute update on a different path of the same device must not
    be delivered — subscribe() registers its own subscription per (node,
    path), see the module docstring of client.py."""
    bridge, upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()
    handler = FakeHandler()

    await bridge.subscribe(lambda _node_id: 5, handler)
    upstream.emit(EventType.ATTRIBUTE_UPDATED, 21.5, node_id=12, attribute_path="1/1026/0")
    await _settle()

    assert handler.attribute_calls == []


async def test_subscribe_maps_a_node_event_to_the_resolved_device_id():
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()

    await bridge.subscribe(lambda address: {"12": 5}.get(address), handler)
    node_event = MatterNodeEvent(
        node_id=12,
        endpoint_id=1,
        cluster_id=59,
        event_id=0,
        event_number=1,
        priority=0,
        timestamp=0,
        timestamp_type=0,
        data=None,
    )
    upstream.emit(EventType.NODE_EVENT, node_event)
    await _settle()

    assert handler.event_calls == [(5, "1/59/0")]


async def test_subscribe_maps_node_updated_availability_to_the_resolved_device_id():
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()

    await bridge.subscribe(lambda address: {"12": 5}.get(address), handler)
    upstream.emit(EventType.NODE_UPDATED, FakeNode(12, {}, available=False))
    await _settle()

    assert handler.availability_calls == [(5, False)]


async def test_subscribe_treats_node_removed_as_offline():
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()

    await bridge.subscribe(lambda address: {"12": 5}.get(address), handler)
    # MatterClient._handle_event_message delivers the bare node ID as data
    # on NODE_REMOVED, not a node object.
    upstream.emit(EventType.NODE_REMOVED, 12)
    await _settle()

    assert handler.availability_calls == [(5, False)]


async def test_disconnect_stops_delivering_updates():
    bridge, upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    await bridge.disconnect()
    upstream.emit(EventType.ATTRIBUTE_UPDATED, True, node_id=12, attribute_path="1/6/0")
    await _settle()

    assert handler.attribute_calls == []


# --- follow() ----------------------------------------------------


async def test_follow_subscribes_a_node_that_did_not_exist_at_subscribe_time():
    """The reported case: a device that was only commissioned after
    `subscribe()` had not a single attribute subscription - its signals
    stayed at "-" until the bridge's next restart."""
    bridge, upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda address: {"12": 5, "8": 9}.get(address), handler)

    upstream.add_node(FakeNode(8, {"1/6/0": True}))
    await bridge.follow("8")
    upstream.emit(EventType.ATTRIBUTE_UPDATED, False, node_id=8, attribute_path="1/6/0")
    await _settle()

    assert handler.attribute_calls == [(9, "1/6/0", False)]


async def test_follow_does_not_subscribe_the_same_path_twice():
    """A second subscription for the same path would deliver every value
    twice - `on_attribute` would run twice, and for an event signal the
    counter would count up twice as fast."""
    bridge, upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    await bridge.follow("12")
    upstream.emit(EventType.ATTRIBUTE_UPDATED, False, node_id=12, attribute_path="1/6/0")
    await _settle()

    assert handler.attribute_calls == [(5, "1/6/0", False)]


async def test_follow_only_subscribes_the_paths_that_are_new():
    node = FakeNode(12, {"1/6/0": True})
    bridge, upstream = make_connected_pair([node])
    await bridge.connect()
    await bridge.subscribe(lambda _node_id: 5, FakeHandler())

    node.node_data.attributes["1/8/0"] = 254
    await bridge.follow("12")

    assert _attribute_subscriptions(upstream) == [
        "attribute_updated/12/1/6/0",
        "attribute_updated/12/1/8/0",
    ]


async def test_follow_without_new_paths_leaves_the_handler_alone():
    """The normal case in operation: `NODE_UPDATED` also fires on a change
    of availability and after every re-subscription. For a device with no
    new paths, the diff is empty, and the process ends before reaching the
    handler - otherwise each of these messages would write over a hundred
    UPDATE statements to the database."""
    bridge, _upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    await bridge.follow("12")

    assert handler.snapshot_calls == []


async def test_follow_hands_the_snapshot_to_the_handler():
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda address: {"8": 42}.get(address), handler)

    upstream.add_node(FakeNode(8, {"0/40/1": "IKEA of Sweden", "1/6/0": True}))
    await bridge.follow("8")

    assert [(device_id, snap.address) for device_id, snap in handler.snapshot_calls] == [(42, "8")]
    assert handler.snapshot_calls[0][1].vendor_name == "IKEA of Sweden"


async def test_follow_subscribes_even_when_the_store_does_not_know_the_node():
    """The subscriptions still come into being - only the handler stays out
    of it, because there is no device that the values would belong to.

    That the seeding is caught up on afterward is NOT what this test
    proves, but `test_the_commissioning_route_still_seeds_after_the_dispatch_
    loop_was_first` further below: a plain second `follow` would find
    not a single new path anymore and would not even reach the handler -
    that is exactly why `seed_even_without_new_paths` exists."""
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: None, handler)

    upstream.add_node(FakeNode(8, {"1/6/0": True}))
    await bridge.follow("8")

    assert "attribute_updated/8/1/6/0" in _attribute_subscriptions(upstream)
    assert handler.snapshot_calls == []


async def test_the_commissioning_route_still_seeds_after_the_dispatch_loop_was_first():
    """The flow of an actual commissioning, in its real order (verified against
    python-matter-server 8.1.2 - since 8 September 2026 `matter-python-client`
    is installed, which delivers the same event sequence over the same WebSocket
    API; the order below is a property of the protocol, not the library, and this
    test verifies it against a mock anyway):

    1. The commissioning route is still waiting on `commission_with_code`.
    2. matter-server sends `NODE_ADDED` over the same websocket before the
       command result arrives - the node, with all its attributes, is
       therefore already in the upstream's cache.
    3. The dispatch loop catches up and subscribes EVERY path of the node.
       The store does not know it yet, `resolve_device_id` returns `None`,
       the handler stays out of it.
    4. The route returns, registers the device - and finds an empty diff
       when it catches up.

    Without `seed_even_without_new_paths`, step 4 ends before the handler,
    and the seeding never happens: matter-server suppresses unchanged
    values, so every static path (voltage with no load, battery level, the
    off state of a plug) would stay a dash until its first change - for
    some, forever."""
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()
    # Stands in for `Store.device_id_for`: only knows the new node
    # after `register_device` has run - so only from step 4 onward.
    known: dict[str, int] = {}
    await bridge.subscribe(known.get, handler)

    new_node = FakeNode(8, {"0/40/1": "IKEA of Sweden", "1/6/0": False})
    upstream.add_node(new_node)
    upstream.emit(EventType.NODE_ADDED, new_node, node_id=8)
    await _settle()

    assert "attribute_updated/8/1/6/0" in _attribute_subscriptions(upstream)
    assert handler.snapshot_calls == []

    known["8"] = 42
    await bridge.follow("8", seed_even_without_new_paths=True)

    assert [(device_id, snap.address) for device_id, snap in handler.snapshot_calls] == [(42, "8")]
    assert handler.snapshot_calls[0][1].attributes == {"0/40/1": "IKEA of Sweden", "1/6/0": False}


async def test_forced_seeding_still_invents_no_device_id():
    """What is forced is the seeding, not the invention of a device_id: if
    the store does not know the node, the handler stays out of it even with
    the switch set - a signal row under a made-up device_id would be a
    wrongly wired row in Loxone, not a missing value."""
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: None, handler)

    upstream.add_node(FakeNode(8, {"1/6/0": True}))
    await bridge.follow("8", seed_even_without_new_paths=True)

    assert handler.snapshot_calls == []


class FailingOnceHandler(FakeHandler):
    """Stands in for a `Runtime.on_node_snapshot` that fails the first time.

    In reality the handler writes through the store to SQLite — under
    concurrent write load from the resend loop, that can blow up with
    `sqlite3.OperationalError` ("database is locked"). For the client, only
    THAT it raises matters.
    """

    def __init__(self) -> None:
        super().__init__()
        self.remaining_failures = 1

    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None:
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise RuntimeError("database is locked")
        await super().on_node_snapshot(device_id, snapshot)


async def test_a_snapshot_the_handler_refused_is_owed_and_caught_up_later():
    """The sequence the commissioning route had claimed for itself: if the
    seeding failed AFTER the subscribing, no later `NODE_UPDATED` helped
    anymore - the diff was empty, `follow` returned before reaching the
    handler, and the device stayed without initial values until the
    bridge's next restart.

    The later call here has neither a new path nor the switch - exactly the
    call from the dispatch loop."""
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FailingOnceHandler()
    await bridge.subscribe(lambda address: {"8": 42}.get(address), handler)

    upstream.add_node(FakeNode(8, {"0/40/1": "IKEA of Sweden", "1/6/0": True}))
    with pytest.raises(RuntimeError):
        # The exception propagates unchanged - the caller (the route)
        # decides what happens with it.
        await bridge.follow("8", seed_even_without_new_paths=True)
    assert handler.snapshot_calls == []

    await bridge.follow("8")

    assert [(device_id, snap.address) for device_id, snap in handler.snapshot_calls] == [(42, "8")]


async def test_a_node_the_store_did_not_know_yet_is_owed_its_snapshot():
    """The node was subscribed, but not seeded, because the store did not
    know it yet. The bridge owes it the snapshot as soon as it is
    resolvable - even without a new path and without the switch."""
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()
    known: dict[str, int] = {}
    await bridge.subscribe(known.get, handler)

    upstream.add_node(FakeNode(8, {"1/6/0": True}))
    await bridge.follow("8")
    assert handler.snapshot_calls == []

    known["8"] = 42
    await bridge.follow("8")

    assert [(device_id, snap.address) for device_id, snap in handler.snapshot_calls] == [(42, "8")]


async def test_a_snapshot_that_arrived_is_not_owed_a_second_time():
    """The cost brake stays in place: `NODE_UPDATED` fires on every change
    of availability and after every re-subscription. Once the debt is
    settled, the empty diff must again end before reaching the handler -
    otherwise each of these messages would write over a hundred UPDATE
    statements to the database."""
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda address: {"8": 42}.get(address), handler)

    upstream.add_node(FakeNode(8, {"1/6/0": True}))
    await bridge.follow("8")
    assert len(handler.snapshot_calls) == 1

    await bridge.follow("8")

    assert len(handler.snapshot_calls) == 1


async def test_follow_before_subscribe_does_nothing():
    """No raising: the commissioning route calls `follow`
    unconditionally, and a startup without a subscription must not fail
    because of it."""
    bridge, upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()

    await bridge.follow("12")

    assert _attribute_subscriptions(upstream) == []


async def test_follow_for_an_unknown_node_does_nothing():
    bridge, _upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    await bridge.follow("999")

    assert handler.snapshot_calls == []


async def test_a_node_update_with_new_paths_is_followed_automatically():
    """The second case of the known boundary: a device commissioned long
    ago reports a path after a firmware update that did not exist at
    startup. `NODE_UPDATED` fires at matter-server exactly when a device
    has been re-interviewed - the correct trigger."""
    node = FakeNode(12, {"1/6/0": True})
    bridge, upstream = make_connected_pair([node])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    node.node_data.attributes["1/8/0"] = 254
    upstream.emit(EventType.NODE_UPDATED, node, node_id=12)
    await _settle()

    assert "attribute_updated/12/1/8/0" in _attribute_subscriptions(upstream)
    assert [device_id for device_id, _ in handler.snapshot_calls] == [5]


async def test_an_availability_update_without_new_paths_touches_no_handler():
    """The single most common cause of `NODE_UPDATED`. It must not trigger
    any store access - and must still deliver availability as before."""
    node = FakeNode(12, {"1/6/0": True})
    bridge, upstream = make_connected_pair([node])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    upstream.emit(EventType.NODE_UPDATED, node, node_id=12)
    await _settle()

    assert handler.snapshot_calls == []
    assert handler.availability_calls == [(5, True)]


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
