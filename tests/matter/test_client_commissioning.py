import asyncio

import pytest
from matter_server.client.exceptions import NotConnected
from matter_server.common.errors import NodeCommissionFailed

from loxmatter.matter.client import BridgeMatterClient, CommissioningError, MatterUnavailableError


class FakeNodeData:
    """Steht fuer matter_server.common.models.MatterNodeData.

    commission_with_code() liefert dieses Dataclass direkt zurueck - anders
    als get_nodes() (siehe FakeNode in test_client.py), das MatterNode-
    Wrapper mit node_data.attributes liefert. node_id und attributes liegen
    hier beide unmittelbar auf dem Objekt, keine Verschachtelung.
    """

    def __init__(self, node_id: int, attributes: dict[str, object]):
        self.node_id = node_id
        self.attributes = attributes
        self.available = True


class FakeUpstream:
    def __init__(self) -> None:
        self.nodes: list[FakeNodeData] = []
        self.removed: list[int] = []
        self.datasets: list[str] = []
        self.fail_with: Exception | None = None

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    async def start_listening(self, ready=None) -> None:
        if ready is not None:
            ready.set()

    def get_nodes(self) -> list[FakeNodeData]:
        return self.nodes

    async def commission_with_code(self, code: str, network_only: bool = False) -> FakeNodeData:
        if self.fail_with is not None:
            raise self.fail_with
        node = FakeNodeData(7, {"0/40/1": "IKEA of Sweden", "1/6/0": True})
        self.nodes.append(node)
        return node

    async def remove_node(self, node_id: int) -> None:
        self.removed.append(node_id)

    async def set_thread_operational_dataset(self, dataset: str) -> None:
        self.datasets.append(dataset)


class FakeSession:
    """Steht fuer aiohttp.ClientSession — close() muss awaitbar sein, denn
    BridgeMatterClient.disconnect() ruft `await http_session.close()`
    (aiohttp.ClientSession.close() ist eine Coroutine)."""

    async def close(self) -> None: ...


@pytest.fixture
def client() -> tuple[BridgeMatterClient, FakeUpstream]:
    upstream = FakeUpstream()
    return (
        BridgeMatterClient(
            "ws://test/ws",
            session_factory=lambda _session: upstream,
            http_session_factory=FakeSession,
        ),
        upstream,
    )


async def test_commissioning_returns_a_snapshot(client):
    bridge, _ = client
    await bridge.connect()
    snapshot = await bridge.commission_with_code("MT:ABC123")
    assert snapshot.node_id == 7
    assert snapshot.vendor_name == "IKEA of Sweden"
    await bridge.disconnect()


async def test_commissioning_without_connection_raises(client):
    bridge, _ = client
    with pytest.raises(Exception, match="nicht verbunden"):
        await bridge.commission_with_code("MT:ABC123")


async def test_a_failed_commissioning_says_so_in_german(client):
    bridge, upstream = client
    upstream.fail_with = RuntimeError("device not found")
    await bridge.connect()
    with pytest.raises(CommissioningError, match="Einlernen fehlgeschlagen"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_a_connection_loss_during_commissioning_says_so_in_german(client):
    """NotConnected & Co. betreffen die Verbindung zu matter-server, nicht das
    Geraet - sie muessen als MatterUnavailableError ankommen, nicht als
    CommissioningError, sonst sucht der Bedienende den Fehler faelschlich am
    Geraet statt an matter-server (siehe Spec 8.1/9)."""
    bridge, upstream = client
    upstream.fail_with = NotConnected("nicht mehr verbunden")
    await bridge.connect()
    with pytest.raises(MatterUnavailableError, match="matter-server"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_a_device_side_commissioning_failure_stays_a_commissioning_error(client):
    """Eine Ablehnung durch das Geraet selbst (z. B. falscher Code) bleibt
    ein CommissioningError - nur der Verbindungsverlust zu matter-server
    wird umgeleitet."""
    bridge, upstream = client
    upstream.fail_with = NodeCommissionFailed("Timeout during commissioning")
    await bridge.connect()
    with pytest.raises(CommissioningError, match="Einlernen fehlgeschlagen"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_cancellation_during_commissioning_propagates_unwrapped(client):
    """asyncio.CancelledError ist eine BaseException, keine Exception - weder
    der Geraete- noch der Verbindungsverlust-Zweig duerfen sie abfangen."""
    bridge, upstream = client
    upstream.fail_with = asyncio.CancelledError()
    await bridge.connect()
    with pytest.raises(asyncio.CancelledError):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_remove_node_reaches_upstream(client):
    bridge, upstream = client
    await bridge.connect()
    await bridge.remove_node(7)
    assert upstream.removed == [7]
    await bridge.disconnect()


async def test_thread_dataset_reaches_upstream(client):
    """Ohne Datensatz kann matter-server einem Thread-Geraet kein Netz nennen."""
    bridge, upstream = client
    await bridge.connect()
    await bridge.set_thread_dataset("0e08...")
    assert upstream.datasets == ["0e08..."]
    await bridge.disconnect()
