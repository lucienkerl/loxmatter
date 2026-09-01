"""Verbindung zu python-matter-server.

Bewusst dünn gehalten: holt Rohdaten und macht NodeSnapshots daraus. Die
Zerlegung in Signale passiert in discovery.py und ist dort ohne Netz getestet.

BridgeMatterClient erzeugt die aiohttp-ClientSession selbst und bleibt damit
ihr alleiniger Besitzer: MatterClientConnection.disconnect() aus
python-matter-server schließt nur das Websocket, nicht die Session, die ihr
übergeben wurde — laut aiohttp-Konvention muss das tun, wer die Session
erzeugt hat. Deshalb hält diese Klasse die Session-Referenz selbst und
schließt sie in disconnect() bzw. bei einem gescheiterten connect().
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loxmatter.matter.models import NodeSnapshot


class MatterUnavailableError(RuntimeError):
    """matter-server ist nicht verbunden oder kennt den gefragten Node nicht."""


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

    def _default_session_factory(self, session: Any) -> Any:
        # Lazy importiert, damit Tests matter_server nie laden müssen.
        from matter_server.client.client import MatterClient

        return MatterClient(self._url, session)

    @staticmethod
    def _default_http_session_factory() -> Any:
        # Lazy importiert, damit Tests aiohttp nie laden müssen.
        import aiohttp

        return aiohttp.ClientSession()

    async def connect(self) -> None:
        # Ein bereits verbundener Client wird bei erneutem connect() sauber
        # getrennt, bevor neu verbunden wird — sonst würde die alte, noch
        # offene Session beim Überschreiben von self._upstream/self._http_session
        # unerreichbar und nie geschlossen.
        if self._upstream is not None:
            await self.disconnect()
        http_session = self._http_session_factory()
        try:
            upstream = self._session_factory(http_session)
            await upstream.connect()
        except BaseException:
            # BaseException statt Exception: asyncio.CancelledError erbt von
            # BaseException, nicht von Exception. Ein während des Verbindungs-
            # aufbaus abgebrochenes connect() (z. B. durch asyncio.wait_for)
            # muss die Session trotzdem schließen und den Abbruch weiterreichen.
            await http_session.close()
            raise
        self._http_session = http_session
        self._upstream = upstream

    async def disconnect(self) -> None:
        if self._upstream is None:
            return
        upstream = self._upstream
        http_session = self._http_session
        # Felder vor dem await auf None setzen: so ist der Client sofort als
        # nicht verbunden erkennbar, auch wenn upstream.disconnect() unten
        # eine Ausnahme wirft — disconnect() bleibt idempotent und der
        # Objektzustand sauber, ganz gleich, wie die Trennung ausgeht.
        self._upstream = None
        self._http_session = None
        if http_session is None:
            # Invariante: Ist _upstream gesetzt, ist auch _http_session gesetzt
            # (beide werden nur gemeinsam in connect() gesetzt). Als expliziter
            # Fehler statt assert, damit die Prüfung auch unter `python -O`
            # greift.
            msg = "interner Fehler: _http_session fehlt trotz aktivem _upstream"
            raise RuntimeError(msg)
        try:
            await upstream.disconnect()
        finally:
            await http_session.close()

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
