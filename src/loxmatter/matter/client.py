"""Verbindung zu python-matter-server.

Bewusst dünn gehalten: holt Rohdaten und macht NodeSnapshots daraus. Die
Zerlegung in Signale passiert in discovery.py und ist dort ohne Netz getestet.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loxmatter.matter.models import NodeSnapshot


class MatterUnavailableError(RuntimeError):
    """matter-server ist nicht verbunden oder kennt den gefragten Node nicht."""


def _default_session_factory(url: str) -> Callable[[], Any]:
    def factory() -> Any:
        import aiohttp
        from matter_server.client.client import MatterClient

        return MatterClient(url, aiohttp.ClientSession())

    return factory


class BridgeMatterClient:
    def __init__(self, url: str, session_factory: Callable[[], Any] | None = None) -> None:
        self._url = url
        self._session_factory = session_factory or _default_session_factory(url)
        self._upstream: Any | None = None

    async def connect(self) -> None:
        upstream = self._session_factory()
        await upstream.connect()
        self._upstream = upstream

    async def disconnect(self) -> None:
        if self._upstream is None:
            return
        await self._upstream.disconnect()
        self._upstream = None

    def _require_upstream(self) -> Any:
        if self._upstream is None:
            raise MatterUnavailableError("nicht verbunden mit matter-server")
        return self._upstream

    async def snapshots(self) -> list[NodeSnapshot]:
        upstream = self._require_upstream()
        return [
            NodeSnapshot.from_raw(node.node_id, {"attributes": node.attributes})
            for node in upstream.get_nodes()
        ]

    async def snapshot(self, node_id: int) -> NodeSnapshot:
        for candidate in await self.snapshots():
            if candidate.node_id == node_id:
                return candidate
        raise MatterUnavailableError(f"unbekannter Node {node_id}")
