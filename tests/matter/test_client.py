import pytest

from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError


class FakeNode:
    def __init__(self, node_id: int, attributes: dict[str, object]):
        self.node_id = node_id
        self.attributes = attributes


class FakeUpstream:
    """Steht für matter_server.client.MatterClient."""

    def __init__(self, nodes: list[FakeNode]):
        self._nodes = nodes
        self.connected = False
        self.disconnect_calls = 0

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False
        self.disconnect_calls += 1

    def get_nodes(self) -> list[FakeNode]:
        return self._nodes


@pytest.fixture
def client() -> BridgeMatterClient:
    upstream = FakeUpstream(
        [
            FakeNode(12, {"0/40/1": "IKEA of Sweden", "1/6/0": True}),
            FakeNode(13, {"0/40/1": "IKEA of Sweden", "1/1026/0": 2150}),
        ]
    )
    return BridgeMatterClient(url="ws://test/ws", session_factory=lambda: upstream)


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
