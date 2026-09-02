import asyncio
from types import SimpleNamespace

import pytest

from loxmatter.matter import client as client_module
from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError


class FakeNode:
    """Steht für matter_server.client.models.node.MatterNode.

    Die echte MatterNode trägt ihre Rohattribute nicht direkt, sondern unter
    node_data.attributes — node_id bleibt aber ein Attribut direkt am Node
    (dort eine Property auf node_data.node_id). Diese Attrappe bildet genau
    diese Form nach, statt sie der Einfachheit halber abzuflachen.
    """

    def __init__(self, node_id: int, attributes: dict[str, object]):
        self.node_id = node_id
        self.node_data = SimpleNamespace(attributes=attributes)


class FakeUpstream:
    """Steht für matter_server.client.MatterClient.

    start_listening() bildet den echten Vertrag nach: Sie füllt den
    Node-Cache, setzt (sofern gewünscht) init_ready und blockiert danach,
    bis sie abgebrochen wird — genau wie MatterClient.start_listening().
    get_nodes() liefert bewusst erst etwas zurück, nachdem start_listening()
    gelaufen ist: Ein Test, der den Listener nie startet, muss den
    ursprünglichen Fehler (leerer Node-Cache) reproduzieren können.
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

    async def start_listening(self, init_ready: asyncio.Event | None = None) -> None:
        self.start_listening_calls += 1
        if self._fail_connect:
            raise RuntimeError("Verbindung fehlgeschlagen")
        self._nodes = self._configured_nodes
        if self._signal_ready and init_ready is not None:
            init_ready.set()
        try:
            await asyncio.Event().wait()  # blockiert, bis abgebrochen
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self._fail_disconnect:
            raise RuntimeError("Trennung fehlgeschlagen")

    def get_nodes(self) -> list[FakeNode]:
        return self._nodes


class FakeSession:
    """Steht für aiohttp.ClientSession — zählt, wie oft close() lief."""

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
    """Baut einen BridgeMatterClient mit Attrappen für HTTP-Session und Upstream."""
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
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await client.snapshots()


async def test_snapshots_maps_every_node(client):
    await client.connect()
    snapshots = await client.snapshots()
    assert [s.node_id for s in snapshots] == [12, 13]
    assert snapshots[0].vendor_name == "IKEA of Sweden"
    assert snapshots[1].attributes["1/1026/0"] == 2150


async def test_snapshot_selects_by_node_id(client):
    await client.connect()
    assert (await client.snapshot(13)).attributes["1/1026/0"] == 2150


async def test_snapshot_raises_for_unknown_node(client):
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
    """BridgeMatterClient erzeugt die Session selbst und muss sie wieder schließen."""
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
    """Ein scheiternder connect() darf die Session nicht leaken und muss einen
    späteren, erfolgreichen connect() zulassen."""
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
    assert [s.node_id for s in snapshots] == [1]
    assert sessions[1].close_calls == 0


async def test_connect_twice_closes_previous_session_and_does_not_leak():
    """Ein zweiter connect() ohne dazwischenliegendes disconnect() darf die
    erste Session nicht unerreichbar hinterlassen — sie muss geschlossen
    werden, bevor die zweite Session entsteht."""
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
    assert [s.node_id for s in snapshots] == [1]


async def test_disconnect_closes_session_even_if_upstream_disconnect_raises():
    """Wirft der Upstream in disconnect(), muss die Session trotzdem
    geschlossen und der Client danach als nicht verbunden erkennbar sein."""
    bridge, session = make_client([FakeNode(1, {})], fail_disconnect=True)
    await bridge.connect()

    with pytest.raises(RuntimeError, match="Trennung fehlgeschlagen"):
        await bridge.disconnect()

    assert session.close_calls == 1
    with pytest.raises(MatterUnavailableError, match="nicht verbunden"):
        await bridge.snapshots()


async def test_connect_cancelled_closes_session_and_propagates_cancellation():
    """asyncio.CancelledError erbt von BaseException, nicht Exception — ein
    während des Verbindungsaufbaus abgebrochener connect() darf die Session
    trotzdem nicht leaken und muss den Abbruch weiterreichen."""
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
    """Der Defekt, den dieser Test verhindert: Ohne Zeitlimit würde connect()
    entweder ewig auf ein Event warten, das nie kommt, oder — schlimmer — sich
    fälschlich als verbunden melden, ohne dass der Node-Cache je gefüllt
    wurde. Ein Listener, der init_ready nie setzt, muss connect() innerhalb
    des Zeitlimits scheitern lassen."""
    monkeypatch.setattr(client_module, "LISTENER_READY_TIMEOUT_SECONDS", 0.05)
    bridge, _session = make_client([FakeNode(1, {})], signal_ready=False)

    with pytest.raises(MatterUnavailableError, match="keine Bereitschaft"):
        await bridge.connect()


async def test_connect_timeout_closes_session_and_allows_a_later_successful_connect(
    monkeypatch,
):
    """Nach einer Bereitschafts-Zeitüberschreitung muss die eigene Session
    geschlossen sein, der Client als nicht verbunden gelten, und ein
    späterer connect() mit einem funktionierenden Upstream muss trotzdem
    gelingen."""
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
    assert [s.node_id for s in snapshots] == [1]
    assert sessions[1].close_calls == 0


async def test_disconnect_cancels_the_listener_task():
    """disconnect() muss den Listener-Task abbrechen, statt ihn einfach
    herumlaufen zu lassen — sonst bleibt eine Coroutine aktiv, die auf eine
    inzwischen geschlossene Verbindung wartet."""
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
    """Regressionstest für den eigentlichen Defekt: Der alte connect() rief
    upstream.start_listening() nie auf, wodurch der Node-Cache des Upstreams
    für immer leer blieb — jedes reale Gerät erschien als unbekannt, egal wie
    viele kommissioniert waren. get_nodes() liefert hier — wie beim echten
    MatterClient — bewusst erst etwas zurück, nachdem start_listening()
    gelaufen ist; gegen den alten Code (kein Aufruf von start_listening())
    schlägt dieser Test fehl."""
    bridge, _session = make_client([FakeNode(3, {"0/40/1": "Aqara", "1/6/0": True})])

    await bridge.connect()
    snapshots = await bridge.snapshots()

    assert [s.node_id for s in snapshots] == [3]
    assert snapshots[0].vendor_name == "Aqara"
