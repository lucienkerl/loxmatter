# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

Was nach `subscribe()` dazukommt, holt `follow_node()` nach — ein Gerät,
das erst danach eingelernt wird, ebenso wie ein bekanntes Gerät, das
nachträglich neue Attributpfade meldet. Angestossen wird es aus der
Dispatch-Schleife bei `NODE_ADDED`/`NODE_UPDATED` und zusätzlich von der
Einlern-Route. Das „zusätzlich" ist nicht Gürtel-und-Hosenträger: das
`NODE_ADDED` eines gerade eingelernten Geräts kommt nachweislich, BEVOR
`commission_with_code` zurückkehrt und der Store dem Node eine device_id
geben kann — die Werte dieser Meldung gehen deshalb ins Leere, und eine
zweite folgt für ein ruhig im Netz stehendes Gerät nicht. Der Dispatch-Task
hat zu diesem Zeitpunkt aber bereits jeden Pfad abonniert; der Nachzug der
Route findet also einen leeren Diff vor und säet nur deshalb trotzdem, weil
sie ihn mit `seed_even_without_new_paths=True` anfordert (die ganze
Begründung steht bei `follow_node`). Siehe
docs/superpowers/specs/2026-09-04-live-werte-neuer-geraete-design.md.

commission_with_code()/remove_node()/set_thread_dataset() — belegt gegen die
installierte python-matter-server==8.1.2 (Task 1, Phase 5):

`MatterClient.commission_with_code(self, code: str, network_only: bool =
False) -> MatterNodeData` — `MatterClient.remove_node(self, node_id: int) ->
None` — `MatterClient.set_thread_operational_dataset(self, dataset: str) ->
None`. `remove_node` und `set_thread_operational_dataset` entsprechen exakt
der Planannahme.

`commission_with_code` NICHT: Der Plan nahm an, der Rückgabewert trüge seine
Rohattribute wie ein `MatterNode` aus `get_nodes()` unter `node_data.attributes`
(siehe oben zu `snapshots()`). Tatsächlich liefert `commission_with_code`
laut Quelltext (`dataclass_from_dict(MatterNodeData, data)`) das
`MatterNodeData`-Dataclass selbst zurück — `node_id` und `attributes` liegen
dort unmittelbar auf dem Objekt, keine Verschachtelung. `MatterNode` (mit
`node_data`-Indirektion) und `MatterNodeData` (flach) sind in
python-matter-server zwei verschiedene Typen; `get_nodes()` liefert Ersteres,
`commission_with_code()` Letzteres. Bei ungeprüfter Übernahme der
Plan-Annahme hätte `node.node_data.attributes` mit `AttributeError`
fehlgeschlagen — hier immerhin laut, anders als die zwei still ausfallenden
Fehlannahmen aus Phase 4 (siehe oben), aber ohne Nachsehen (Step 1) wäre
auch das erst beim ersten echten Einlernversuch aufgefallen, nicht beim
Schreiben des Codes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Final, Protocol

from loxmatter import i18n
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


class CommissioningError(RuntimeError):
    """Das Einlernen eines Geraets ist am Geraet selbst gescheitert (z. B.
    falscher Code, Geraet haengt schon in einem anderen Oekosystem, Timeout
    beim Interview).

    Ein Verbindungsverlust zu matter-server WAEHREND des Einlernens ist
    davon ausdruecklich abgegrenzt: commission_with_code() faengt
    `NotConnected`/`ConnectionClosed`/`CannotConnect` gesondert ab und wirft
    dafuer `MatterUnavailableError`, denn nur so laesst sich unterscheiden,
    ob das Geraet abgelehnt hat oder matter-server nicht erreichbar war
    (Spec 8.1/9, Review-Fix Task 1). Die urspruengliche Ausnahme bleibt ueber
    `__cause__` erhalten."""


class RuntimeEventHandler(Protocol):
    """Was `subscribe()` von seinem Aufrufer braucht — `Runtime`
    (loxone/runtime.py) erfüllt das bereits unverändert, `_run()` kann sie
    also direkt als `handler` übergeben, ohne einen Adapter zu schreiben.

    `on_node_snapshot` kam mit dem Nachziehen der Abonnements dazu
    (`follow_node`): der Client sieht ein Gerät mit Pfaden, für die es noch
    keine Signalzeile gibt, und kann selbst nichts damit anfangen — er kennt
    den `Store` nicht und soll ihn nicht kennen. Der Handler dagegen hat
    ihn."""

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None: ...
    async def on_event(self, device_id: int, path: str) -> None: ...
    async def set_online(self, device_id: int, online: bool) -> None: ...
    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None: ...


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


@dataclass(frozen=True)
class _FollowNode:
    """Anstoss zum Nachziehen der Abonnements eines Node.

    Laeuft ueber dieselbe Queue wie die Wert-Aktualisierungen, statt direkt
    aus dem synchronen Ereignis-Rueckruf heraus: `follow_node` ist eine
    Coroutine, und der Rueckruf kann keine erwarten (siehe
    `on_node_or_availability_event`).
    """

    node_id: int


_QueueItem = _AttributeUpdate | _EventUpdate | _AvailabilityUpdate | _FollowNode


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
        # Ob DIESE Verbindung matter-server den Thread-Datensatz schon
        # uebergeben hat - siehe `thread_dataset_set` fuer die Begruendung,
        # warum das nicht aus `server_info` allein ablesbar ist. Wird bei
        # jedem connect() zurueckgesetzt: eine neue Verbindung kann einen
        # neu gestarteten matter-server treffen, und der hat sie vergessen.
        self._thread_dataset_set = False
        # subscribe()/follow_node()-Zustand. Die Menge der bereits angelegten
        # Attribut-Abonnements ist die einzige Quelle dafuer, was "neu" heisst
        # - ein zweites Abonnement fuer denselben (Node, Pfad) wuerde jeden
        # Wert doppelt zustellen. Queue, Handler und die device_id-Aufloesung
        # bleiben nach subscribe() erreichbar, weil follow_node sie braucht.
        self._subscribed_paths: set[tuple[int, str]] = set()
        # Nodes, denen diese Bruecke noch ein Abbild schuldet - siehe
        # `follow_node`, wo auch steht, warum das eine ANDERE Frage
        # beantwortet als der Schalter `seed_even_without_new_paths`.
        self._seed_pending: set[int] = set()
        self._queue: asyncio.Queue[_QueueItem] | None = None
        self._handler: RuntimeEventHandler | None = None
        self._resolve_device_id: Callable[[int], int | None] | None = None

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
                msg = i18n.t("api.errors.listener_stopped_early")
                raise MatterUnavailableError(msg)

            msg = i18n.t("api.errors.listener_timeout", timeout=LISTENER_READY_TIMEOUT_SECONDS)
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
        self._thread_dataset_set = False

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
        self._subscribed_paths = set()
        self._seed_pending = set()
        self._queue = None
        self._handler = None
        self._resolve_device_id = None
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

    @property
    def connected(self) -> bool:
        """Ob `connect()` erfolgreich lief und `disconnect()` seither nicht
        aufgerufen wurde - fuer den Systemcheck der Diagnose (Spec 10.5,
        Task 6, Phase 5; siehe `api.diagnostics._check_matter_server`), der
        einzige bisherige Aufrufer. Spiegelt exakt dieselbe Bedingung wie
        `_require_upstream` unten (`self._upstream is not None`), nur ohne
        zu werfen - eine Pruefung soll einen fehlenden Zustand melden
        koennen, nicht ihn signalisieren muessen."""
        return self._upstream is not None

    def _require_upstream(self) -> Any:
        if self._upstream is None:
            raise MatterUnavailableError(i18n.t("api.errors.not_connected"))
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
        raise MatterUnavailableError(i18n.t("api.errors.unknown_node", node_id=node_id))

    async def commission_with_code(self, code: str) -> NodeSnapshot:
        """Lernt ein Geraet ueber seinen Pairing-Code ein.

        Der Code ist die 11-stellige Zahl oder der 21-stellige MT:-Code vom
        Geraet oder seiner Verpackung. Haengt das Geraet schon in einem
        anderen Oekosystem, funktioniert der aufgedruckte Code nicht mehr -
        dann braucht es von dort einen Multi-Admin-Code (Spec 7.1).

        Der Upstream liefert hier ein `MatterNodeData` zurueck, dessen
        `node_id`/`attributes`/`available` unmittelbar auf dem Objekt liegen
        - anders als bei `get_nodes()` (siehe Modul-Docstring). Ein
        Thread-Geraet scheitert hier mit "Required network information not
        provided", solange `set_thread_dataset()` nicht vorher aufgerufen
        wurde.
        """
        upstream = self._require_upstream()

        # Lazy importiert wie _default_session_factory: Tests mit einem
        # Fake-Upstream sollen matter_server nie laden müssen.
        from matter_server.client.exceptions import CannotConnect, ConnectionClosed, NotConnected

        try:
            node = await upstream.commission_with_code(code)
        except (NotConnected, ConnectionClosed, CannotConnect) as exc:
            # Verbindungsverlust zu matter-server ist keine Ablehnung durch
            # das Geraet — beides landete zuvor ununterscheidbar in
            # CommissioningError (Review-Fix, siehe Task-1-Report). Fängt
            # diesen Zweig VOR dem generischen except Exception unten ab,
            # sonst würde er dort mitgefangen.
            msg = i18n.t("api.errors.matter_server_unreachable", exc=exc)
            raise MatterUnavailableError(msg) from exc
        except Exception as exc:
            raise CommissioningError(i18n.t("api.errors.commissioning_failed", exc=exc)) from exc
        return NodeSnapshot.from_raw(
            node.node_id, {"attributes": node.attributes, "available": node.available}
        )

    async def remove_node(self, node_id: int) -> None:
        """Entfernt ein Geraet aus der Fabric."""
        await self._require_upstream().remove_node(node_id)

    @property
    def thread_dataset_set(self) -> bool:
        """Ob matter-server die Thread-Zugangsdaten gerade hat.

        Zwei Quellen, weil keine allein reicht:

        - `server_info.thread_credentials_set` sagt, was matter-server BEIM
          VERBINDUNGSAUFBAU gemeldet hat. Der Dienst schickt zwar bei jeder
          Aenderung ein `SERVER_INFO_UPDATED`-Ereignis
          (`device_controller.set_thread_operational_dataset` loest es aus),
          aber `MatterClient._handle_event_message` kennt dafuer keinen
          Zweig - das Abbild bleibt also fuer die Dauer der Verbindung
          stehen, auch nachdem diese Bruecke den Datensatz selbst gesetzt
          hat. Geprueft gegen die installierte Fassung, nicht vermutet.
        - `_thread_dataset_set` sind die eigenen, erfolgreichen Aufrufe von
          `set_thread_dataset()` auf DIESER Verbindung.

        Die Angabe ist bewusst konservativ: `False` heisst "nicht belegbar",
        nicht "sicher nicht gesetzt". Der Aufrufer holt dann einen Datensatz
        und setzt ihn erneut - das ist idempotent und kostet einen
        HTTP-Aufruf, waehrend die umgekehrte Verwechslung ein Thread-Geraet
        erst nach 40 Sekunden mit "Commission with code failed" scheitern
        liesse.
        """
        if self._thread_dataset_set:
            return True
        info = getattr(self._upstream, "server_info", None)
        return bool(getattr(info, "thread_credentials_set", False))

    async def set_thread_dataset(self, dataset: str) -> None:
        """Uebergibt matter-server die Thread-Zugangsdaten.

        Ohne diesen Schritt scheitert das Einlernen eines Thread-Geraets mit
        "Required network information not provided" - der Controller findet
        das Geraet per BLE, kann ihm aber kein Netz nennen.

        matter-server haelt sie ausschliesslich im Arbeitsspeicher (siehe
        `matter/otbr.py` fuer den ganzen Vorgang und den Ernstfall dazu):
        jeder Neustart des Dienstes loescht sie wieder, und diese Bruecke
        muss sie danach erneut uebergeben.
        """
        await self._require_upstream().set_thread_operational_dataset(dataset)
        self._thread_dataset_set = True

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
                i18n.t(
                    "api.errors.command_unknown_to_sdk",
                    cluster_id=call.cluster_id,
                    command_id=call.command_id,
                )
            )
        command = command_cls(**call.payload)
        await upstream.send_device_command(call.node_id, call.endpoint, command)

    def _subscribe_attribute_paths(
        self,
        upstream: Any,
        queue: asyncio.Queue[_QueueItem],
        node_id: int,
        paths: Iterable[str],
    ) -> int:
        """Legt je ein Attribut-Abonnement pro noch nicht abonniertem
        (Node, Pfad)-Paar an und liefert deren Anzahl.

        Eine Stelle fuer beide Aufrufer (`subscribe` und `follow_node`): zwei
        Stellen, die dasselbe Registrierungsschema nachbilden, driften ueber
        kurz oder lang auseinander - und das faellt hier nicht auf, weil ein
        fehlendes Abonnement kein Fehler ist, sondern Stille.

        `queue` kommt als Parameter statt aus `self._queue`, damit `subscribe`
        sie uebergeben kann, bevor sie im Feld steht - und damit hier keine
        Nicht-`None`-Pruefung noetig ist, deren Einengung ueber die Closure
        unten ohnehin nicht traegt.
        """
        # Lazy importiert wie ueberall in dieser Datei: Tests mit einem
        # Fake-Upstream sollen matter_server nie laden muessen.
        from matter_server.common.models import EventType

        added = 0
        for path in paths:
            if (node_id, path) in self._subscribed_paths:
                continue

            # default-Argumente binden node_id/path pro Schleifendurchlauf,
            # statt den Namen aus dem umschliessenden Scope zu spaet
            # auszuwerten (klassische Closure-Falle in einer Schleife).
            def on_attribute_event(
                _event: Any, data: Any, node_id: int = node_id, path: str = path
            ) -> None:
                queue.put_nowait(_AttributeUpdate(node_id, path, data))

            self._unsubscribers.append(
                upstream.subscribe_events(
                    on_attribute_event,
                    event_filter=EventType.ATTRIBUTE_UPDATED,
                    node_filter=node_id,
                    attr_path_filter=path,
                )
            )
            self._subscribed_paths.add((node_id, path))
            added += 1
        return added

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
            raise MatterUnavailableError(i18n.t("api.errors.subscribe_already_called"))

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
                # Zusaetzlich zum Erreichbarkeits-Update, nicht statt seiner:
                # beide Meldungen tragen dieselbe Ursache, aber der eine Weg
                # setzt `d<id>_online`, der andere zieht Abonnements nach.
                queue.put_nowait(_FollowNode(data.node_id))
            elif event is EventType.NODE_REMOVED:
                # data ist hier die blanke Node-ID (kein Node-Objekt) — siehe
                # MatterClient._handle_event_message.
                queue.put_nowait(_AvailabilityUpdate(data, False))

        self._unsubscribers = [upstream.subscribe_events(on_node_or_availability_event)]
        self._subscribed_paths = set()
        self._queue = queue
        self._handler = handler
        self._resolve_device_id = resolve_device_id

        # Attribut-Updates: siehe Modul-Docstring, warum das nur pro bekanntem
        # (Node, Pfad)-Paar geht. Was nach diesem Aufruf dazukommt, holt
        # `follow_node` nach.
        for node in upstream.get_nodes():
            self._subscribe_attribute_paths(
                upstream, queue, node.node_id, node.node_data.attributes
            )

        self._dispatch_task = asyncio.create_task(
            self._dispatch_loop(queue, resolve_device_id, handler)
        )

    async def follow_node(self, node_id: int, *, seed_even_without_new_paths: bool = False) -> None:
        """Zieht die Attribut-Abonnements eines Node nach.

        Zwei Aufrufer, ein Vorgang: die Einlern-Route (`api/devices.py`) nach
        dem Registrieren eines neuen Geräts, und `_dispatch_loop` bei
        `NODE_ADDED`/`NODE_UPDATED` für ein Gerät, das nachträglich neue
        Pfade meldet.

        **Warum die Route nicht einfach auf das Ereignis warten kann:**
        matter-server meldet `NODE_ADDED` noch WÄHREND `commission_with_code`
        läuft (`device_controller._setup_node` signalisiert es vor der
        Rückkehr des Aufrufs). Zu diesem Zeitpunkt kennt der Store den Node
        noch nicht, `resolve_device_id` liefert `None`, und für ein ruhig im
        Netz stehendes Gerät folgt keine zweite Meldung. Am 2026-09-04 am
        laufenden Stack aufgezeichnet: Node 8 war um 11:15:33 fertig
        eingelernt, samt "Subscription succeeded" - alles davon vor der
        Rückkehr an die Route.

        Der leere Diff ist der Regelfall und kostet nichts: `NODE_UPDATED`
        feuert auch bei jedem Wechsel der Erreichbarkeit und nach jeder
        Re-Subscription, und für ein Gerät ohne neue Pfade endet der Vorgang
        vor dem Handler (und damit vor dem Store).

        **`seed_even_without_new_paths` überspringt genau diesen frühen
        Ausstieg** — und ohne den Schalter fände das Säen eines frisch
        eingelernten Geräts nie statt. Der Ablauf, der das erzwingt:

        1. Die Route wartet noch auf `commission_with_code`.
        2. matter-server schickt `NODE_ADDED` über denselben Websocket, BEVOR
           das Kommando-Ergebnis kommt; `MatterClient._handle_event_message`
           legt den Node samt vollständiger `attributes` in seinen Cache und
           ruft erst danach die Rückrufe auf.
        3. Der Dispatch-Task läuft, während die Route noch wartet: sein
           `follow_node` findet den Node im Cache und abonniert ALLE seine
           Pfade. `resolve_device_id` liefert `None` (der Store kennt den
           Node noch nicht), der Handler bleibt also außen vor.
        4. Die Route kehrt zurück, registriert das Gerät und zieht nach —
           jetzt ist der Diff leer, `added == 0`, und der frühe Ausstieg
           käme vor `resolve_device_id`.

        Für den Aufruf aus der Route gilt der Zweck des frühen Ausstiegs
        (keine Store-Schreiblast durch die häufigen `NODE_UPDATED`) nicht: er
        geschieht einmal pro Einlernen, und das Säen ist dort der eigentliche
        Sinn des Aufrufs. Ohne es zeigt ein frisch eingelerntes Gerät für
        jeden statischen Pfad — Spannung ohne Last, Batteriestand, der
        Aus-Zustand einer Steckdose — weiterhin einen Strich, bis der Wert
        sich zum ersten Mal ändert; bei manchen nie, weil matter-server
        unveränderte Werte unterdrückt.

        Geforct wird dabei das Säen, nicht das Erfinden einer device_id:
        kennt der Store den Node nicht, bleibt der Handler auch mit dem
        Schalter außen vor.

        **`_seed_pending` beantwortet eine andere Frage als der Schalter** —
        beide werden gebraucht, keines ersetzt das andere. Der Schalter ist
        der Aufrufer, der weiß, dass er das Gerät gerade registriert hat; die
        Menge ist die Brücke, die sich merkt, dass sie einem Node noch ein
        Abbild schuldet. Ein Node kommt hinein, wenn er (noch) keiner
        device_id zuzuordnen war oder wenn der Handler beim Säen geworfen
        hat, und verlässt sie erst, wenn das Abbild angekommen ist — deshalb
        führt auch ein leerer Diff ohne Schalter bis zum Handler, solange die
        Schuld offen ist.

        Ohne die Menge trüge die Selbstheilungs-Zusage der Einlern-Route nur
        halb: sie gilt für ein `follow_node`, das scheitert, BEVOR es
        abonniert hat. Scheitert es DANACH — `resolve_device_id` liest aus
        SQLite, der Handler schreibt dorthin, beides kann unter der
        Schreiblast der Resend-Schleife auffliegen —, fände jeder spätere
        Aufruf aus der Dispatch-Schleife einen leeren Diff vor und kehrte vor
        dem Handler um. Das Gerät bliebe dauerhaft ohne Startwerte, und
        niemand erführe davon.

        `_subscribed_paths` bleibt davon unangetastet: die Abonnements beim
        Upstream bestehen weiter, und würde man ihre Buchführung verwerfen,
        legte der nächste Aufruf ein ZWEITES Abonnement je Pfad an — jeder
        Wert käme doppelt an, bei einem Ereignissignal zählte zusätzlich der
        Zähler doppelt hoch.

        Vor `subscribe()` aufgerufen tut die Methode nichts, statt zu werfen:
        die Einlern-Route ruft sie bedingungslos, und ein Aufbau ohne
        Subscription soll daran nicht scheitern.
        """
        queue = self._queue
        handler = self._handler
        resolve_device_id = self._resolve_device_id
        if queue is None or handler is None or resolve_device_id is None:
            logger.debug(
                "follow_node(%s) ohne vorheriges subscribe() - nichts nachzuziehen", node_id
            )
            return

        upstream = self._require_upstream()
        node = next((n for n in upstream.get_nodes() if n.node_id == node_id), None)
        if node is None:
            logger.info(
                "Node %s ist matter-server nicht bekannt - keine Abonnements nachgezogen", node_id
            )
            return

        attributes = node.node_data.attributes
        added = self._subscribe_attribute_paths(upstream, queue, node_id, attributes)
        if added == 0 and not seed_even_without_new_paths and node_id not in self._seed_pending:
            return

        device_id = resolve_device_id(node_id)
        if device_id is None:
            # Die Abonnements bleiben bestehen, und die Schuld wird vermerkt:
            # sobald der Store den Node kennt, holt der nächste `follow_node`
            # das Abbild nach — auch ohne neuen Pfad und ohne den Schalter.
            self._seed_pending.add(node_id)
            logger.debug("Node %s ist keinem Gerät zugeordnet - nur abonniert", node_id)
            return

        # Erst eintragen, dann säen, und nur nach Gelingen wieder austragen:
        # wirft der Handler — er schreibt über `Runtime.on_node_snapshot` in
        # den Store — oder wird der Aufruf abgebrochen, bleibt die Schuld
        # stehen, und der nächste `follow_node` holt sie nach. Die Ausnahme
        # läuft unverändert weiter; was mit ihr geschieht, entscheidet der
        # Aufrufer.
        self._seed_pending.add(node_id)
        await handler.on_node_snapshot(
            device_id,
            NodeSnapshot.from_raw(node_id, {"attributes": attributes, "available": node.available}),
        )
        self._seed_pending.discard(node_id)

    async def _dispatch_loop(
        self,
        queue: asyncio.Queue[_QueueItem],
        resolve_device_id: Callable[[int], int | None],
        handler: RuntimeEventHandler,
    ) -> None:
        while True:
            item = await queue.get()
            try:
                if isinstance(item, _FollowNode):
                    # VOR der device_id-Aufloesung: `follow_node` legt
                    # Abonnements auch fuer einen Node an, den der Store
                    # (noch) nicht kennt, und entscheidet selbst, ob der
                    # Handler etwas zu sehen bekommt.
                    await self.follow_node(item.node_id)
                    continue
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
