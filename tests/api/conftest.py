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

"""Gemeinsame Fixtures fuer die WebUI-API-Tests (Phase 5).

Jede Task dieser Phase, die einen `httpx2`-Client gegen `build_app` aufbaut,
braucht dieselben drei Dinge: einen Invoker, der nie wirklich ein
Matter-Kommando verschickt, eine `Runtime`, die keinen echten UDP-Sender
braucht, und einen Matter-Client, der ohne Netzwerk auskommt. `no_invoke`,
`fake_runtime` und `fake_client` sind dafuer als eigenstaendige
`@pytest.fixture`-Funktionen gebaut, nicht als Modul-Funktionen zum manuellen
Importieren: Pytest liefert Wiederverwendbarkeit ueber die eingebaute
Fixture-Vererbung kostenlos - jede Testdatei unter `tests/api/` bekommt sie
automatisch als Parameter, ganz ohne Import.

`load_snapshot` ist die eine Ausnahme: eine Fixture kann keinen Dateinamen
entgegennehmen, deshalb bleibt sie eine gewoehnliche Funktion, importiert per
`from conftest import load_snapshot` - das funktioniert, weil Pytest das
Verzeichnis dieser Datei (`tests/api/`, ohne `__init__.py`) beim Einlesen von
Testdateien bereits vorn in `sys.path` einreiht (siehe restliche Testsuite,
die ebenfalls ohne `__init__.py`-Pakete auskommt).

Erweiterung fuer spaetere Tasks dieser Phase: `fake_runtime` nimmt bereits
`store` entgegen wie die echte `Runtime`, und `FakeMatterClient` sammelt
seine Aufrufe in Listen wie `FakeUpstream` in
`tests/matter/test_client_commissioning.py` - fuer einen Test, der eine
Fehlschlag-Simulation braucht, reicht `fake_client.fail_commission_with =
CommissioningError(...)` vor dem Aufruf zu setzen, ganz ohne diese Datei
anzufassen. Ein Taster-Geraet laedt sich ueber `load_snapshot
("ikea_bilresa_button.json")`.

`plug_store` und `api_with_runtime` (Task 3, Live-Werte): manche Tests
brauchen eine ECHTE `Runtime` statt `FakeRuntime` - z. B. jeder Test der
Beobachter-Verdrahtung (`Runtime.add_observer`), denn nur die echte
`Runtime` kennt Beobachter ueberhaupt. `plug_store` ist die Grundlage dafuer:
derselbe Aufbau wie die `api`-Fixture in `test_devices.py`, aber ohne
bereits eine App/einen Client zu bauen, damit auch `tests/loxone/
test_runtime.py`-artige Tests, die nur den Store brauchen, sie nutzen
koennen. `api_with_runtime` baut darauf die App inklusive WebSocket-Route
(`/api/live`) und liefert einen Client, der zusaetzlich zu den ueblichen
HTTP-Methoden `websocket_connect` anbietet.

**Warum `websocket_connect` nicht `httpx2` selbst benutzt:** `httpx2`
bietet mit `.websocket()`/`ASGIWebSocketTransport` grundsaetzlich denselben
inprozess-WebSocket-Test-Mechanismus - er haelt aber seine `anyio`-
Task-Gruppe ueber die komplette Verbindung offen, vom Verbindungsaufbau bis
zum Trennen. Unter `pytest-asyncio` laufen Setup und Teardown einer
Async-Generator-Fixture (das `yield` unten) nachweislich in ZWEI
verschiedenen `asyncio.Task`-Objekten, selbst innerhalb derselben
Event-Loop - `anyio`s `CancelScope` verlangt aber zwingend denselben Task
fuer Eintritt und Austritt und wirft sonst `RuntimeError: Attempted to exit
cancel scope in a different task than it was entered in` (reproduziert:
schon ein einzeiliger `async with client.websocket(...): pass` in einem
Test reicht, unabhaengig vom Rest dieser Datei). `_InProcessWebSocket`
unten umgeht das, indem es ganz ohne `anyio`-Task-Gruppen auskommt - nur
eine ASGI-App als `asyncio.Task` plus zwei `asyncio.Queue`s, komplett
lebend innerhalb des EINEN Tasks, der den jeweiligen Test ausfuehrt (`__aenter__`
und `__aexit__` werden beide direkt aus dem Testkoerper aufgerufen, nie ueber
eine Fixture-Grenze hinweg)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Self

import httpx2 as httpx
import pytest

from loxmatter.auth.passwords import hash_password
from loxmatter.commands.translate import MatterCall
from loxmatter.diagnostics.logbuffer import install_log_buffer
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.sender import UdpSender
from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load_snapshot(name: str) -> NodeSnapshot:
    """Laedt ein aufgezeichnetes Geraet aus `tests/fixtures/nodes/`."""
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


# Das Passwort, mit dem sich jede Testfixture anmeldet. Ein fester Wert und
# kein zufaelliger: er taucht in Fehlermeldungen fehlschlagender Tests auf,
# und dort ist "test-passwort" hilfreicher als eine Zufallsfolge.
TEST_PASSWORD = "test-passwort"


async def authenticate(store: Store, client: httpx.AsyncClient) -> None:
    """Setzt ein Passwort und meldet `client` an.

    Gebraucht seit der Waechter ohne Nachweis nichts mehr durchlaesst (Spec 4):
    eine Testfixture, die `/api` aufruft, muss angemeldet sein wie ein
    Browser. `httpx.AsyncClient` fuehrt einen eigenen Cookie-Speicher, ein
    einziger Aufruf hier genuegt also fuer alle folgenden Anfragen desselben
    Clients."""
    store.auth.set_password_hash(hash_password(TEST_PASSWORD))
    response = await client.post("/auth/login", json={"password": TEST_PASSWORD})
    assert response.status_code == 200, "Anmeldung in der Testfixture fehlgeschlagen"


@pytest.fixture
def no_invoke():
    """Ein Invoker, der `build_app` erfuellt, aber nie wirklich gebraucht
    wird - die Geraete-API loest keine `/cmd`-Aufrufe aus. Ruft ein Test ihn
    doch auf, tut er nichts, statt gegen ein echtes Geraet zu senden."""

    async def _invoke(call: MatterCall) -> None:
        return None

    return _invoke


class FakeRuntime:
    """Erfuellt `api.devices.RuntimeValues`, ohne einen UdpSender oder eine
    Matter-Subscription aufzubauen - fuer Tests, die nur die Geraete-API
    pruefen wollen. Die volle `Runtime` (Sender, Impulse, Heartbeat) hat
    ihre eigene Testsuite unter `tests/loxone/test_runtime.py`.

    `store` wird entgegengenommen, aber (noch) nicht benutzt - allein damit
    die Fabrik dieselbe Form wie `Runtime(store, sender)` hat, falls eine
    spaetere Task hier doch einmal nachschlagen muss."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._values: dict[str, float | bool] = {}

    def seed(self, key: str, value: float | bool) -> None:
        """Traegt einen Wert ein, als haette eine Subscription ihn gerade gemeldet."""
        self._values[key] = value

    def last_values_for(self, device_id: int) -> dict[str, float | bool]:
        prefix = f"d{device_id}_"
        return {k: v for k, v in self._values.items() if k.startswith(prefix)}

    async def set_online(self, device_id: int, online: bool) -> None:
        """Wie `Runtime.set_online`, ohne den UDP-Versand: haelt den Wert
        unter demselben Schluessel, den `_device_out` liest. Gebraucht,
        seit das Einlernen die Erreichbarkeit eines neuen Geraets selbst
        saeet (siehe `api/devices.py`)."""
        self._values[f"d{device_id}_online"] = online


@pytest.fixture
def fake_runtime():
    """Fabrik statt fertigem Objekt: der Store steht erst innerhalb des
    jeweiligen Tests fest (siehe `api`-Fixture in `test_devices.py`)."""
    return FakeRuntime


class FakeMatterClient:
    """Erfuellt genau die drei `BridgeMatterClient`-Methoden, die die
    Geraete-API aufruft: Einlernen, Entfernen, Thread-Datensatz. Dasselbe
    Aufzeichnungs-Muster wie `FakeUpstream` in
    `tests/matter/test_client_commissioning.py`, nur auf der Ebene von
    `BridgeMatterClient` statt seines `session_factory`-Seams - die
    Geraete-API ruft `BridgeMatterClient` direkt auf, nicht dessen Upstream.
    """

    def __init__(self) -> None:
        self.commissioned: list[str] = []
        self.removed: list[int] = []
        self.datasets: list[str] = []
        self.fail_commission_with: Exception | None = None
        self.fail_remove_with: Exception | None = None
        self._next_node_id = 100
        # Fuer den Systemcheck der Diagnose (Task 6, Phase 5) - spiegelt
        # `BridgeMatterClient.connected`. Ein Test, der eine getrennte
        # Verbindung simulieren will, setzt es einfach auf `False`, ganz
        # ohne diese Datei anzufassen (siehe conftest-Moduldocstring,
        # "Erweiterung fuer spaetere Tasks").
        self.connected = True
        # Was `commission_with_code` als Erreichbarkeit des frisch
        # eingelernten Nodes meldet - beim echten Client kommt sie aus
        # `MatterNodeData.available`. Ein Test, der ein Geraet simulieren
        # will, das matter-server nicht erreicht, setzt es auf `False`.
        self.available = True
        # Spiegelt `BridgeMatterClient.thread_dataset_set`: ob matter-server
        # die Thread-Zugangsdaten gerade hat. `False` als Vorgabe, weil das
        # der Zustand nach jedem Neustart des Dienstes ist - genau der, in
        # dem das Einlernen eines Thread-Geraets bisher scheiterte.
        self.thread_dataset_set = False
        # Die Reihenfolge der Aufrufe: der Datensatz muss VOR dem Einlernen
        # gesetzt sein, sonst kommt er fuer dieses Geraet zu spaet.
        self.order: list[str] = []
        # Die Node-IDs, fuer die die Route das Nachziehen der Abonnements
        # angestossen hat (`BridgeMatterClient.follow_node`).
        self.followed: list[int] = []
        # Der Store, gegen den `follow_node` prueft, ob das Geraet zum
        # Zeitpunkt des Aufrufs bereits registriert war. Die `api`-Fixture
        # setzt ihn; ohne ihn zeichnet `follow_node` nur den Aufruf auf.
        self.store: Store | None = None
        self.followed_resolved: list[int | None] = []
        # Ob der jeweilige Aufruf das Saeen erzwungen hat
        # (`seed_even_without_new_paths`). Fuer die Route ist das kein
        # Beiwerk: zu dem Zeitpunkt, an dem sie nachzieht, hat die
        # Dispatch-Schleife die Pfade des neuen Node laengst abonniert - ohne
        # den Schalter faende sie einen leeren Diff und saete nie (siehe
        # `BridgeMatterClient.follow_node`).
        self.followed_forced: list[bool] = []

    async def commission_with_code(self, code: str) -> NodeSnapshot:
        if self.fail_commission_with is not None:
            raise self.fail_commission_with
        self.commissioned.append(code)
        self.order.append("commission")
        node_id = self._next_node_id
        self._next_node_id += 1
        return NodeSnapshot(
            node_id=node_id,
            vendor_name="Fake",
            product_name="Geraet",
            unique_id=f"fake-{node_id}",
            attributes={},
            available=self.available,
        )

    async def remove_node(self, node_id: int) -> None:
        if self.fail_remove_with is not None:
            raise self.fail_remove_with
        self.removed.append(node_id)

    async def set_thread_dataset(self, dataset: str) -> None:
        self.datasets.append(dataset)
        self.order.append("dataset")
        self.thread_dataset_set = True

    async def follow_node(self, node_id: int, *, seed_even_without_new_paths: bool = False) -> None:
        self.followed.append(node_id)
        self.followed_forced.append(seed_even_without_new_paths)
        self.order.append("follow")
        # Der eigentliche Nachweis: das echte
        # `BridgeMatterClient.follow_node` loest die Node-ID ueber den Store
        # auf und tut ohne Treffer nichts weiter als zu abonnieren. Wird sie
        # hier nicht aufgeloest, zieht die Route zu frueh nach - dasselbe
        # Wettrennen, das das NODE_ADDED-Ereignis bereits verloren hat.
        self.followed_resolved.append(
            None if self.store is None else self.store.device_id_for_node(node_id)
        )


@pytest.fixture
def fake_client():
    return FakeMatterClient()


class FakeThreadDatasetSource:
    """Steht fuer `loxmatter.matter.otbr.fetch_active_dataset` - die Quelle,
    aus der sich das Einlernen den Thread-Datensatz holt, wenn matter-server
    ihn nicht (mehr) hat. Zaehlt die Aufrufe, damit ein Test belegen kann,
    dass der Border Router NICHT gefragt wurde."""

    def __init__(self) -> None:
        # Gestalt wie ein echter (Hex-TLV), aber ohne jeden Bezug zu einem
        # existierenden Netz - ein echter Datensatz ist ein Credential und
        # gehoert weder ins Repository noch in ein Log.
        self.dataset = "0e08000000000001" + "00" * 24
        self.calls = 0
        self.fail_with: Exception | None = None

    async def __call__(self) -> str:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return self.dataset


@pytest.fixture
def fake_otbr():
    return FakeThreadDatasetSource()


@pytest.fixture
def plug_store(tmp_path):
    """Ein `Store` mit der IKEA-Steckdose, registriert wie bei einem
    echten Einlernen (Geraet, Signale, Ausgangsbefehle) - Grundlage fuer
    Tests, die eine ECHTE `Runtime` brauchen statt `FakeRuntime` (z. B.
    die Beobachter-Verdrahtung aus Task 3). Liefert `(store, device_id)`,
    wie `environment` es in `tests/loxone/test_runtime.py` tut."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
    yield store, device_id
    store.close()


class _InProcessWebSocket:
    """Minimaler inprozess-ASGI-WebSocket-Testclient - siehe Modul-Docstring
    fuer den Grund, warum das nicht einfach `httpx2.AsyncClient.websocket`
    ist. Treibt die ASGI-App als eigenen `asyncio.Task` an, verbunden ueber
    zwei `asyncio.Queue`s (eingehend/ausgehend) - ganz ohne `anyio`-
    Task-Gruppen, die eine Fixture-Grenze ueberleben muessten.

    Implementiert nur, was diese Testsuite braucht: verbinden, `receive_json`,
    sauber trennen. Kein Text-/Bytes-Versand, kein Ping/Pong - die WebUI
    schickt auf dieser Route nichts, sie hoert nur zu (siehe `api/live.py`).

    `break_send_after` (Review-Fix Important #2 in `api/live.py`,
    2026-09-02): rein additiv, `None` (Default) aendert am obigen Verhalten
    nichts. Gesetzt, laesst es den ASGI-`send`-Aufrufer selbst ein
    `RuntimeError` werfen, sobald mehr als `break_send_after`
    `websocket.send`-Nachrichten durchgelaufen sind - simuliert damit genau
    den Fall aus dem Modul-Docstring von `api/live.py`: eine ASGI-Schicht,
    die beim Versand auf eine bereits verlorene Verbindung kein
    `WebSocketDisconnect`, sondern ein `RuntimeError` wirft. Ohne dieses
    Werkzeug liesse sich dieser Pfad in diesem Inprozess-Harness gar nicht
    erreichen: `_from_app` unten ist unbegrenzt, ein Test, der einfach nicht
    liest, erzeugt hier - anders als ein echter, volles TCP-Sendepuffer
    blockierender Client - keinen echten Sendefehler.

    `cookies` (Task 8, Phase 5): das Sitzungs-Cookie, mit dem sich
    `WebSocketClient` bereits ueber `authenticate()` angemeldet hat, reist
    hier NICHT von selbst mit - dieser Scope wird von Hand gebaut, nicht aus
    einer echten Verbindung abgeleitet, die den Cookie-Header eines Browsers
    automatisch mitschickt. Ohne diesen Parameter wuerde jeder Test, der
    `websocket_connect` benutzt, am seit Task 8 geschlossenen Waechter
    scheitern, obwohl `client` laengst angemeldet ist."""

    def __init__(
        self,
        app: Any,
        path: str,
        *,
        break_send_after: int | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self._app = app
        self._path = path
        self._to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._break_send_after = break_send_after
        self._sends_before_break = 0
        self._cookies = cookies or {}

    async def __aenter__(self) -> Self:
        headers: list[tuple[bytes, bytes]] = []
        if self._cookies:
            cookie_header = "; ".join(f"{name}={value}" for name, value in self._cookies.items())
            headers.append((b"cookie", cookie_header.encode()))
        scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "scheme": "ws",
            "path": self._path,
            "raw_path": self._path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": headers,
            "client": ("testclient", 123),
            "server": ("testserver", 80),
            "subprotocols": [],
            "state": {},
            "extensions": {"websocket.http.response": {}},
        }

        async def receive() -> dict[str, Any]:
            return await self._to_app.get()

        async def send(message: dict[str, Any]) -> None:
            if self._break_send_after is not None and message["type"] == "websocket.send":
                if self._sends_before_break >= self._break_send_after:
                    raise RuntimeError('Cannot call "send" once a close message has been sent.')
                self._sends_before_break += 1
            await self._from_app.put(message)

        self._task = asyncio.create_task(self._app(scope, receive, send))
        await self._to_app.put({"type": "websocket.connect"})
        message = await self._from_app.get()
        if message["type"] != "websocket.accept":
            raise AssertionError(f"WebSocket wurde nicht akzeptiert: {message!r}")
        return self

    async def receive_json(self) -> Any:
        message = await self._from_app.get()
        if message["type"] == "websocket.close":
            raise AssertionError("WebSocket wurde vom Server getrennt, bevor eine Nachricht kam")
        return json.loads(message["text"])

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        assert self._task is not None
        try:
            await asyncio.wait_for(self._task, timeout=2)
        except TimeoutError:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def wait_closed(self, timeout: float = 2) -> None:
        """Wartet, bis die SERVER-Seite die Verbindung von sich aus beendet
        hat - ohne, anders als `__aexit__`, selbst ein
        `websocket.disconnect` zu schicken. Fuer Tests, die pruefen wollen,
        dass die Route sich selbst aufraeumt (z. B. nach einem simulierten
        Sendefehler ueber `break_send_after`), statt dass der Client die
        Trennung ausloest."""
        assert self._task is not None
        await asyncio.wait_for(self._task, timeout=timeout)


class WebSocketClient:
    """Duenner Wrapper um `httpx2.AsyncClient`, der zusaetzlich
    `websocket_connect` anbietet (siehe `_InProcessWebSocket`). Jeder andere
    Aufruf (`get`, `post`, `patch`, ...) wird unveraendert an den
    zugrunde liegenden Client durchgereicht, damit `api_with_runtime` sich
    fuer REST- und WebSocket-Tests gleichermassen eignet."""

    def __init__(self, client: httpx.AsyncClient, app: Any) -> None:
        self._client = client
        self._app = app

    def websocket_connect(
        self, url: str, *, break_send_after: int | None = None
    ) -> _InProcessWebSocket:
        # `dict(self._client.cookies)` statt des Cookie-Jars selbst: das
        # Sitzungs-Cookie aus `authenticate()` soll unveraendert mitreisen,
        # wie es bei einem echten Browser-WebSocket vom selben Ursprung aus
        # geschaehe (siehe `_InProcessWebSocket`-Docstring).
        return _InProcessWebSocket(
            self._app,
            url,
            break_send_after=break_send_after,
            cookies=dict(self._client.cookies),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


@pytest.fixture
async def api_with_runtime(
    plug_store, no_invoke, fake_client
) -> AsyncIterator[tuple[WebSocketClient, Runtime, int]]:
    """Wie die `api`-Fixture in `test_devices.py`, aber mit einer ECHTEN
    `Runtime` statt `FakeRuntime` - fuer Tests der Beobachter-Verdrahtung
    und der WebSocket-Routen `/api/live` (Task 3, Spec 8.3) und
    `/api/diagnostics/live` (Task 4, Spec 10.5).

    **Ein ECHTER `UdpSender` statt eines Fuellmaterial-Objekts** (anders als
    noch in Task 3) - `api.diagnostics_live.build_diagnostics_live_router`
    haengt an `sender.add_datagram_observer` (Task 2), und dieser Zweig
    haengt an `UdpSender.send`s Mitschnitt (`_record_sent`), nicht an
    `Runtime`s Beobachterkette (siehe dort). Ein Fake-Sender, der nur
    `send()`/`close()` erfuellt, wuerde diesen Mitschnitt nie ausloesen -
    `test_a_fresh_datagram_arrives_as_a_message` braucht also denselben
    Aufbau wie `api_with_sender` in `test_diagnostics.py` (ein UDP-Socket
    auf `127.0.0.1`, der die Maschine nicht verlaesst, Port `0` fuer einen
    vom Betriebssystem zugeteilten, freien Port).

    **`install_log_buffer()` fuer denselben Grund** - der Log-Zweig der
    neuen Route (`log_handler.add_observer`, Task 3) braucht einen echten
    `LogBufferHandler`, angehaengt an den Logger `loxmatter`. Wird nach dem
    Test wieder abgemeldet, UND die Stufe des Loggers zurueckgesetzt -
    sonst haeufte jeder Test, der diese Fixture benutzt, einen weiteren
    Handler am selben, PROZESSWEITEN Logger an, und dessen Stufe bliebe
    auf INFO stehen (siehe `tests/diagnostics/test_logbuffer.py` fuer
    dasselbe Muster)."""
    store, device_id = plug_store
    loxmatter_logger = logging.getLogger("loxmatter")
    previous_level = loxmatter_logger.level
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.setblocking(False)
    host, port = receiver.getsockname()
    sender = UdpSender(host, port)
    runtime = Runtime(store, sender)
    log_handler = install_log_buffer()
    app = build_app(
        store,
        no_invoke,
        runtime,
        client=fake_client,
        sender=sender,
        log_handler=log_handler,
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await authenticate(store, client)
            yield WebSocketClient(client, app), runtime, device_id
    finally:
        await sender.close()
        loxmatter_logger.removeHandler(log_handler)
        # Auch die STUFE zuruecksetzen (2026-09-03): `install_log_buffer`
        # setzt seit Task 3 nicht nur die Stufe des Handlers, sondern auch
        # die des Loggers - sonst blieb `loxmatter` nach dieser Fixture
        # dauerhaft auf INFO stehen, obwohl der Handler laengst abgemeldet
        # ist. Ein Test, der spaeter laeuft und eine andere Stufe erwartet,
        # saehe dann etwas, das kein Test gesetzt hat.
        loxmatter_logger.setLevel(previous_level)
        receiver.close()
