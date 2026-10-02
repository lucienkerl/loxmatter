"""`BridgeMatterClient` as an `IdentifySource` (design 2026-10-02, section 9.1).

The fake upstream is local to this file on purpose: it needs only the node
cache and `send_device_command`."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from loxmatter.matter.client import BridgeMatterClient
from loxmatter.sources import IdentifySource, IdentifyUnsupportedError


class IdentifyNode:
    def __init__(self, node_id: int, attributes: dict[str, Any], available: bool = True):
        self.node_id = node_id
        self.available = available
        self.node_data = SimpleNamespace(attributes=attributes, available=available)


class IdentifyUpstream:
    def __init__(self, nodes: list[IdentifyNode], schema: int = 11):
        self._nodes = nodes
        self.server_info = SimpleNamespace(schema_version=schema)
        self.sent: list[tuple[int, int, Any]] = []

    async def start_listening(self, init_ready: asyncio.Event | None = None) -> None:
        if init_ready is not None:
            init_ready.set()
        await asyncio.Event().wait()

    async def disconnect(self) -> None:
        return None

    def get_nodes(self) -> list[IdentifyNode]:
        return self._nodes

    def subscribe_events(self, *args: Any, **kwargs: Any) -> Any:
        return lambda: None

    async def send_device_command(self, node_id: int, endpoint_id: int, command: Any) -> None:
        self.sent.append((node_id, endpoint_id, command))


class _Session:
    async def close(self) -> None:
        return None


async def _connected(upstream: IdentifyUpstream) -> BridgeMatterClient:
    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=_Session,
    )
    await bridge.connect()
    return bridge


async def test_the_client_is_an_identify_source():
    bridge = await _connected(IdentifyUpstream([]))
    try:
        assert isinstance(bridge, IdentifySource)
    finally:
        await bridge.disconnect()


async def test_identify_goes_to_every_endpoint_with_the_cluster():
    upstream = IdentifyUpstream(
        [IdentifyNode(21, {"0/40/1": "IKEA", "1/3/0": 0, "1/6/0": True, "2/3/0": 0})]
    )
    bridge = await _connected(upstream)
    try:
        assert bridge.supports_identify("21") is True
        await bridge.identify("21", 30)
    finally:
        await bridge.disconnect()
    sent = [
        (node, endpoint, type(command).__name__, command.identifyTime)
        for node, endpoint, command in upstream.sent
    ]
    assert sent == [(21, 1, "Identify", 30), (21, 2, "Identify", 30)]


async def test_a_device_without_identify_is_unsupported():
    upstream = IdentifyUpstream([IdentifyNode(23, {"0/40/1": "Tasmota", "1/6/0": True})])
    bridge = await _connected(upstream)
    try:
        assert bridge.supports_identify("23") is False
        with pytest.raises(IdentifyUnsupportedError):
            await bridge.identify("23", 30)
    finally:
        await bridge.disconnect()
