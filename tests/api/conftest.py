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
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Self

import httpx2 as httpx
import pytest

from loxmatter.commands.translate import MatterCall
from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_app
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def load_snapshot(name: str) -> NodeSnapshot:
    """Laedt ein aufgezeichnetes Geraet aus `tests/fixtures/nodes/`."""
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


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

    async def commission_with_code(self, code: str) -> NodeSnapshot:
        if self.fail_commission_with is not None:
            raise self.fail_commission_with
        self.commissioned.append(code)
        node_id = self._next_node_id
        self._next_node_id += 1
        return NodeSnapshot(
            node_id=node_id,
            vendor_name="Fake",
            product_name="Geraet",
            unique_id=f"fake-{node_id}",
            attributes={},
        )

    async def remove_node(self, node_id: int) -> None:
        if self.fail_remove_with is not None:
            raise self.fail_remove_with
        self.removed.append(node_id)

    async def set_thread_dataset(self, dataset: str) -> None:
        self.datasets.append(dataset)


@pytest.fixture
def fake_client():
    return FakeMatterClient()


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


class _NullSender:
    """Ein Sender, der nichts wirklich verschickt - fuer Tests, die eine
    echte `Runtime` (und damit ihre Beobachter-Verdrahtung) brauchen, aber
    keinen UDP-Sender. Anders als `RecordingSender`/`FakeSender` in den
    jeweiligen Testdateien selbst zeichnet dieser hier nichts auf - er ist
    nur Fuellmaterial fuer `Runtime.__init__`."""

    async def send(self, key: str, value: float | bool, *, force: bool = False) -> bool:
        return True

    async def close(self) -> None:
        return None


class _InProcessWebSocket:
    """Minimaler inprozess-ASGI-WebSocket-Testclient - siehe Modul-Docstring
    fuer den Grund, warum das nicht einfach `httpx2.AsyncClient.websocket`
    ist. Treibt die ASGI-App als eigenen `asyncio.Task` an, verbunden ueber
    zwei `asyncio.Queue`s (eingehend/ausgehend) - ganz ohne `anyio`-
    Task-Gruppen, die eine Fixture-Grenze ueberleben muessten.

    Implementiert nur, was diese Testsuite braucht: verbinden, `receive_json`,
    sauber trennen. Kein Text-/Bytes-Versand, kein Ping/Pong - die WebUI
    schickt auf dieser Route nichts, sie hoert nur zu (siehe `api/live.py`)."""

    def __init__(self, app: Any, path: str) -> None:
        self._app = app
        self._path = path
        self._to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "scheme": "ws",
            "path": self._path,
            "raw_path": self._path.encode(),
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 123),
            "server": ("testserver", 80),
            "subprotocols": [],
            "state": {},
            "extensions": {"websocket.http.response": {}},
        }

        async def receive() -> dict[str, Any]:
            return await self._to_app.get()

        async def send(message: dict[str, Any]) -> None:
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


class WebSocketClient:
    """Duenner Wrapper um `httpx2.AsyncClient`, der zusaetzlich
    `websocket_connect` anbietet (siehe `_InProcessWebSocket`). Jeder andere
    Aufruf (`get`, `post`, `patch`, ...) wird unveraendert an den
    zugrunde liegenden Client durchgereicht, damit `api_with_runtime` sich
    fuer REST- und WebSocket-Tests gleichermassen eignet."""

    def __init__(self, client: httpx.AsyncClient, app: Any) -> None:
        self._client = client
        self._app = app

    def websocket_connect(self, url: str) -> _InProcessWebSocket:
        return _InProcessWebSocket(self._app, url)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


@pytest.fixture
async def api_with_runtime(
    plug_store, no_invoke, fake_client
) -> AsyncIterator[tuple[WebSocketClient, Runtime, int]]:
    """Wie die `api`-Fixture in `test_devices.py`, aber mit einer ECHTEN
    `Runtime` statt `FakeRuntime` - fuer Tests der Beobachter-Verdrahtung
    und der WebSocket-Route `/api/live` (Task 3, Spec 8.3)."""
    store, device_id = plug_store
    runtime = Runtime(store, _NullSender())
    app = build_app(store, no_invoke, runtime, client=fake_client)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield WebSocketClient(client, app), runtime, device_id
