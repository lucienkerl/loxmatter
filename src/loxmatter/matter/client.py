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

subscribe() — eine Abweichung vom Auftrag (Task 8), belegt gegen die
installierte python-matter-server==8.1.2:

`MatterClient.subscribe_events(callback, event_filter, node_filter,
attr_path_filter)` ruft `callback` bei jedem Treffer als `callback(event,
data)` auf — synchron, nur zwei Argumente. `node_filter`/`attr_path_filter`
steuern ausschließlich, *ob* ein registriertes `callback` überhaupt
aufgerufen wird (Schlüssel-Matching in `MatterClient._signal_event`), sie
werden ihm NICHT mitgegeben. Für `EventType.NODE_EVENT` und
`EventType.NODE_ADDED`/`NODE_UPDATED` reicht das trotzdem: `data` ist dort
ein `MatterNodeEvent` bzw. der volle `MatterNode`, beide tragen `node_id`
selbst. Für `EventType.ATTRIBUTE_UPDATED` dagegen ist `data` einzig der neue
Wert — kein `node_id`, kein Attributpfad. Eine einzelne Wildcard-Subscription
kann ein Attribut-Update deshalb nicht einem Gerät zuordnen; das ist keine
Falllücke, sondern in `_handle_event_message`/`_signal_event` so angelegt
(siehe `.venv/.../matter_server/client/client.py`).

Deshalb registriert `subscribe()` für Attribute genau eine Subscription pro
bei Aufruf bekanntem (Node, Pfad)-Paar — `node_filter` und `attr_path_filter`
legen dabei exakt fest, wofür ein Callback steht, und der Callback selbst
schließt `node_id`/`path` als Closure ein. Node-Events und
Erreichbarkeit laufen dagegen über je eine einzige Wildcard-Subscription,
weil ihre `data` bereits alles Nötige trägt.

Bekannte Grenze: Attribute eines Geräts, das ERST NACH diesem Aufruf neu
kommissioniert wird oder nachträglich neue Attributpfade meldet, werden
nicht automatisch mit abonniert — es gibt (noch) keinen erneuten
Subscription-Abgleich bei `NODE_ADDED`. Für die anvisierte Nutzung (`connect()`
liest den vollen Node-Cache, danach einmalig `subscribe()`) ist das
hinnehmbar, gehört aber in die Spec als offener Punkt, nicht in ein
stilles Vergessen — siehe Task-8-Report.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Protocol

from loxmatter.commands.translate import MatterCall
from loxmatter.matter.models import NodeSnapshot

logger = logging.getLogger(__name__)

# Wie lange connect() auf das Bereitschafts-Event des Listeners wartet, bevor
# es aufgibt. matter-server schickt den initialen Node-Dump normalerweise
# binnen weniger Sekunden; das Vielfache dient als Sicherheitsmarge gegen
# einen langsamen oder hängenden Server.
LISTENER_READY_TIMEOUT_SECONDS: Final = 10.0


class MatterUnavailableError(RuntimeError):
    """matter-server ist nicht verbunden, kennt den gefragten Node nicht,
    oder kennt das angeforderte Kommando nicht."""


class RuntimeEventHandler(Protocol):
    """Was `subscribe()` von seinem Aufrufer braucht — `Runtime`
    (loxone/runtime.py) erfüllt das bereits unverändert, `_run()` kann sie
    also direkt als `handler` übergeben, ohne einen Adapter zu schreiben."""

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None: ...
    async def on_event(self, device_id: int, path: str) -> None: ...
    async def set_online(self, device_id: int, online: bool) -> None: ...


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


_QueueItem = _AttributeUpdate | _EventUpdate | _AvailabilityUpdate


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
        # subscribe()-Zustand: der Dispatch-Task liest _event_queue und ruft
        # den handler auf; _unsubscribers sind die Rueckruf-Funktionen, die
        # upstream.subscribe_events() je Registrierung zurueckgibt.
        self._dispatch_task: asyncio.Task[None] | None = None
        self._unsubscribers: list[Callable[[], None]] = []

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
        dispatch_task = self._dispatch_task
        unsubscribers = self._unsubscribers
        # Felder vor dem await auf None setzen: so ist der Client sofort als
        # nicht verbunden erkennbar, auch wenn einer der Schritte unten eine
        # Ausnahme wirft — disconnect() bleibt idempotent und der
        # Objektzustand sauber, ganz gleich, wie die Trennung ausgeht.
        self._upstream = None
        self._http_session = None
        self._listener_task = None
        self._dispatch_task = None
        self._unsubscribers = []
        if http_session is None:
            # Invariante: Ist _upstream gesetzt, ist auch _http_session gesetzt
            # (beide werden nur gemeinsam in connect() gesetzt). Als expliziter
            # Fehler statt assert, damit die Prüfung auch unter `python -O`
            # greift.
            msg = "interner Fehler: _http_session fehlt trotz aktivem _upstream"
            raise RuntimeError(msg)
        # Erst die Subscriptions beim Upstream abmelden (keine neuen
        # Aktualisierungen mehr in die Queue), danach den Dispatch-Task
        # abbrechen (nichts mehr aus der Queue verarbeiten) — beides vor dem
        # eigentlichen Verbindungsabbau, sonst liefe der Dispatch-Task auf
        # einem bereits getrennten upstream weiter.
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
            # `available` kommt bewusst mit hinein (Review-Fix C1,
            # 2026-09-02): `Runtime.seed_from_snapshot` braucht sie, um ein
            # Geraet beim Start korrekt als on-/offline zu saeen, statt es
            # bis zum naechsten NODE_ADDED/NODE_UPDATED unbestimmt zu lassen.
            NodeSnapshot.from_raw(
                node.node_id,
                {"attributes": node.node_data.attributes, "available": node.available},
            )
            for node in upstream.get_nodes()
        ]

    async def snapshot(self, node_id: int) -> NodeSnapshot:
        for candidate in await self.snapshots():
            if candidate.node_id == node_id:
                return candidate
        raise MatterUnavailableError(f"unbekannter Node {node_id}")

    async def send_command(self, call: MatterCall) -> None:
        """Führt einen übersetzten `MatterCall` über den Upstream aus.

        `MatterClient.send_device_command()` erwartet kein Tripel aus
        Cluster-ID, Kommando-ID und einem rohen Nutzlast-Dict, sondern ein
        Kommando-Objekt aus `chip.clusters.Objects` — dieselbe SDK-Bibliothek,
        die `matter_server.client.client` selbst unverändert importiert
        (siehe dortiges `from chip.clusters import Objects as Clusters`).
        `chip.clusters.ClusterObjects.ALL_ACCEPTED_COMMANDS[cluster_id]
        [command_id]` ist die von der SDK selbst geführte, vollständige
        Tabelle dieser Klassen — genau die Quelle, die auch matter-server
        intern für dieselbe Zuordnung nutzt. Sie wird erst durch den Import
        von `chip.clusters.Objects` gefüllt (Seiteneffekt der
        Klassendefinitionen darin), deshalb der explizite Import hier statt
        eines bloßen `from chip.clusters import ClusterObjects`.

        Die Feldnamen aus `commands/translate.py` (z. B. `level`,
        `transitionTime`, `colorTemperatureMireds`) sind bewusst identisch zu
        den Dataclass-Feldern der jeweiligen Kommando-Klasse benannt — siehe
        `test_send_command_passes_the_payload_as_command_fields`.
        """
        upstream = self._require_upstream()

        # Lazy importiert wie _default_session_factory: Tests mit einem
        # Fake-Upstream sollen chip.clusters nie laden müssen.
        import chip.clusters.Objects  # noqa: F401 — nur fuer den Seiteneffekt gebraucht
        from chip.clusters import ClusterObjects

        cluster_commands = ClusterObjects.ALL_ACCEPTED_COMMANDS.get(call.cluster_id)
        command_cls = cluster_commands.get(call.command_id) if cluster_commands else None
        if command_cls is None:
            raise MatterUnavailableError(
                f"Cluster {call.cluster_id} Kommando {call.command_id} ist der chip-SDK unbekannt"
            )
        command = command_cls(**call.payload)
        await upstream.send_device_command(call.node_id, call.endpoint, command)

    async def subscribe(
        self,
        resolve_device_id: Callable[[int], int | None],
        handler: RuntimeEventHandler,
    ) -> None:
        """Meldet Attribut- und Event-Änderungen sowie Erreichbarkeit an `handler`.

        `resolve_device_id` bildet eine Node-ID auf die stabile `device_id`
        des Stores ab (z. B. `Store.device_id_for_node`) — genau diese
        Abbildung passiert hier, BEVOR `handler` etwas sieht, denn die
        Schlüssel in Loxone hängen an der `device_id`, nicht an der Node-ID
        (siehe Modul-Docstring, `Store` und Task-8-Report). Liefert
        `resolve_device_id` `None` (Node noch nicht exportiert/registriert
        oder inzwischen entfernt), wird die Aktualisierung verworfen — wie
        `Runtime._signal_for` es für einen unbekannten Signal-Pfad bereits
        tut.

        `handler` erfüllt `RuntimeEventHandler` — `Runtime` selbst passt
        unverändert.

        Siehe Modul-Docstring für die Begründung des Registrierungsschemas
        (eine Wildcard-Subscription für Node-Events/Erreichbarkeit, eine
        Subscription je bei Aufruf bekanntem Attributpfad) und seine Grenze.
        """
        upstream = self._require_upstream()
        if self._dispatch_task is not None:
            raise MatterUnavailableError("subscribe() wurde bereits aufgerufen")

        # Lazy importiert wie _default_session_factory: Tests mit einem
        # Fake-Upstream sollen matter_server nie laden müssen.
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
            elif event is EventType.NODE_REMOVED:
                # data ist hier die blanke Node-ID (kein Node-Objekt) — siehe
                # MatterClient._handle_event_message.
                queue.put_nowait(_AvailabilityUpdate(data, False))

        unsubscribers = [upstream.subscribe_events(on_node_or_availability_event)]

        # Attribut-Updates: siehe Modul-Docstring, warum das nur pro bekanntem
        # (Node, Pfad)-Paar geht. default-Argumente binden node_id/path pro
        # Schleifendurchlauf, statt den Namen aus dem umschließenden Scope zu
        # spät auszuwerten (klassische Closure-Falle in einer Schleife).
        for node in upstream.get_nodes():
            for path in node.node_data.attributes:

                def on_attribute_event(
                    _event: Any, data: Any, node_id: int = node.node_id, path: str = path
                ) -> None:
                    queue.put_nowait(_AttributeUpdate(node_id, path, data))

                unsubscribers.append(
                    upstream.subscribe_events(
                        on_attribute_event,
                        event_filter=EventType.ATTRIBUTE_UPDATED,
                        node_filter=node.node_id,
                        attr_path_filter=path,
                    )
                )

        self._unsubscribers = unsubscribers
        self._dispatch_task = asyncio.create_task(
            self._dispatch_loop(queue, resolve_device_id, handler)
        )

    async def _dispatch_loop(
        self,
        queue: asyncio.Queue[_QueueItem],
        resolve_device_id: Callable[[int], int | None],
        handler: RuntimeEventHandler,
    ) -> None:
        while True:
            item = await queue.get()
            try:
                device_id = resolve_device_id(item.node_id)
                if device_id is None:
                    logger.debug("Aktualisierung fuer unbekannte Node %s verworfen", item.node_id)
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
                # Ein Fehler bei einer einzelnen Aktualisierung darf die
                # Zustellung nicht insgesamt beenden — analog zu
                # Runtime._heartbeat_loop/_resend_loop.
                logger.exception("Zustellung einer Matter-Aktualisierung fehlgeschlagen")
