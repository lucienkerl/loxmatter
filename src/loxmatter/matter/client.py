"""Verbindung zu python-matter-server.

Bewusst dünn gehalten: holt Rohdaten und macht NodeSnapshots daraus. Die
Zerlegung in Signale passiert in discovery.py und ist dort ohne Netz getestet.

BridgeMatterClient erzeugt die aiohttp-ClientSession selbst und bleibt damit
ihr alleiniger Besitzer: MatterClientConnection.disconnect() aus
python-matter-server schließt nur das Websocket, nicht die Session, die ihr
übergeben wurde — laut aiohttp-Konvention muss das tun, wer die Session
erzeugt hat. Deshalb hält diese Klasse die Session-Referenz selbst und
schließt sie in disconnect() bzw. bei einem gescheiterten connect().

Der Upstream-`MatterClient` füllt seinen Node-Cache ausschließlich in
`start_listening()` — eine langlaufende Coroutine, die den initialen
Node-Dump holt, ein `init_ready`-Event setzt und danach weiterläuft, um
Push-Updates zu empfangen. `connect()` startet sie deshalb als Hintergrund-
Task und wartet auf das Bereitschafts-Event, bevor der Client sich als
verbunden meldet; `disconnect()` bricht diesen Task wieder ab, bevor die
Verbindung geschlossen wird.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any, Final

from loxmatter.matter.models import NodeSnapshot

# Wie lange connect() auf das Bereitschafts-Event des Listeners wartet, bevor
# es aufgibt. matter-server schickt den initialen Node-Dump normalerweise
# binnen weniger Sekunden; das Vielfache dient als Sicherheitsmarge gegen
# einen langsamen oder hängenden Server.
LISTENER_READY_TIMEOUT_SECONDS: Final = 10.0


class MatterUnavailableError(RuntimeError):
    """matter-server ist nicht verbunden oder kennt den gefragten Node nicht."""


async def _cancel_and_await(task: asyncio.Task[Any]) -> None:
    """Bricht einen Task ab und wartet sein Ende ab.

    Rein für Aufräumzwecke gedacht: Ausnahmen aus dem abgebrochenen Task
    (typischerweise CancelledError, aber auch andere, falls der Task schon
    vorher mit einem Fehler geendet hat) werden hier verschluckt, damit sie
    nicht den eigentlichen, bereits laufenden Fehlerpfad überdecken — der
    Aufrufer hat die relevante Ausnahme an der eigentlichen Fehlerquelle
    bereits gesehen oder sieht sie dort noch.
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

    def _default_session_factory(self, session: Any) -> Any:
        # Lazy importiert, damit Tests matter_server nie laden müssen.
        from matter_server.client.client import MatterClient

        return MatterClient(self._url, session)

    @staticmethod
    def _default_http_session_factory() -> Any:
        # Lazy importiert, damit Tests aiohttp nie laden müssen.
        import aiohttp

        return aiohttp.ClientSession()

    async def _start_listener(self, upstream: Any) -> asyncio.Task[Any]:
        """Startet upstream.start_listening() als Hintergrund-Task und
        wartet, bis er den Node-Cache gefüllt und Bereitschaft signalisiert
        hat. Scheitert der Listener oder meldet er sich nicht rechtzeitig,
        räumt diese Methode den Task vollständig ab und wirft, statt einen
        halb verbundenen Task zurückzugeben."""
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
                # Bereitschaft gemeldet — der Listener läuft jetzt im
                # Hintergrund weiter, um Push-Updates zu empfangen.
                return listener_task

            await _cancel_and_await(ready_task)

            if listener_task in done:
                # Der Listener ist beendet, bevor er Bereitschaft gemeldet
                # hat. .result() wirft seine ursprüngliche Ausnahme
                # unverändert weiter (z. B. CannotConnect) — Aufrufer wie
                # die CLI können sie damit weiterhin gezielt behandeln.
                listener_task.result()
                msg = "Listener wurde beendet, bevor er Bereitschaft meldete"
                raise MatterUnavailableError(msg)

            msg = (
                f"matter-server hat nach {LISTENER_READY_TIMEOUT_SECONDS:.0f}s "
                "keine Bereitschaft gemeldet"
            )
            raise MatterUnavailableError(msg)
        except BaseException:
            await _cancel_and_await(listener_task)
            raise

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
            listener_task = await self._start_listener(upstream)
        except BaseException:
            # BaseException statt Exception: asyncio.CancelledError erbt von
            # BaseException, nicht von Exception. Ein während des Verbindungs-
            # aufbaus abgebrochenes connect() (z. B. durch asyncio.wait_for)
            # muss die Session trotzdem schließen und den Abbruch weiterreichen.
            await http_session.close()
            raise
        self._http_session = http_session
        self._upstream = upstream
        self._listener_task = listener_task

    async def disconnect(self) -> None:
        if self._upstream is None:
            return
        upstream = self._upstream
        http_session = self._http_session
        listener_task = self._listener_task
        # Felder vor dem await auf None setzen: so ist der Client sofort als
        # nicht verbunden erkennbar, auch wenn einer der Schritte unten eine
        # Ausnahme wirft — disconnect() bleibt idempotent und der
        # Objektzustand sauber, ganz gleich, wie die Trennung ausgeht.
        self._upstream = None
        self._http_session = None
        self._listener_task = None
        if http_session is None:
            # Invariante: Ist _upstream gesetzt, ist auch _http_session gesetzt
            # (beide werden nur gemeinsam in connect() gesetzt). Als expliziter
            # Fehler statt assert, damit die Prüfung auch unter `python -O`
            # greift.
            msg = "interner Fehler: _http_session fehlt trotz aktivem _upstream"
            raise RuntimeError(msg)
        try:
            if listener_task is not None:
                await _cancel_and_await(listener_task)
        finally:
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
            # Die Rohattribute liegen bei matter_server.MatterNode nicht
            # direkt auf dem Node, sondern auf node.node_data.attributes —
            # war bislang unbeobachtbar, weil der Node-Cache vor der
            # Listener-Anbindung immer leer war (siehe Modul-Docstring).
            NodeSnapshot.from_raw(node.node_id, {"attributes": node.node_data.attributes})
            for node in upstream.get_nodes()
        ]

    async def snapshot(self, node_id: int) -> NodeSnapshot:
        for candidate in await self.snapshots():
            if candidate.node_id == node_id:
                return candidate
        raise MatterUnavailableError(f"unbekannter Node {node_id}")
