"""Ein einziger echter WebSocket-Handshake gegen einen echten `uvicorn`
(Review-Fix Important #1, 2026-09-02).

**Warum dieser Test noetig ist, obwohl `tests/api/test_live.py` schon acht
gruene Tests fuer `/api/live` hat:** jene Tests laufen alle ueber
`_InProcessWebSocket` (siehe `tests/api/conftest.py`) - eine ASGI-App, direkt
als `asyncio.Task` angetrieben, ganz ohne einen echten Server dazwischen.
Das war beim Aufspueren dieses Fehlers bereits der Fall: `uvicorn` allein
(ohne das "standard"-Extra) bringt gar keine WebSocket-Implementierung mit,
`GET /api/live` antwortete gegen einen echten `uvicorn.run` dieses Dienstes
mit 404 "Unsupported upgrade request" - und trotzdem blieb die komplette
Testsuite gruen, weil der In-Prozess-Pfad uvicorns eigene HTTP/WebSocket-
Weiche schlicht nie durchlaeuft. `websockets>=12` in `pyproject.toml` (siehe
Kommentar dort) ist seither die einzige Absicherung dagegen - eine
Abhaengigkeits-Aktualisierung, ein Aufraeumen ("importiert ja niemand
`websockets` direkt") oder ein Wechsel von `uvicorn[standard]` auf blosses
`uvicorn` wuerde die Live-Aktualisierung der WebUI erneut lautlos
zerstoeren, und `uv run pytest` wuerde es nicht bemerken - denn genau das
ist ja passiert.

Dieser Test schliesst exakt diese Luecke: er startet einen ECHTEN
`uvicorn.Server` auf einem Loopback-Port und fuehrt darueber einen echten
WebSocket-Handshake (RFC 6455) gegen `/api/live` aus - **ohne selbst eine
WebSocket-Client-Bibliothek zu benutzen** (siehe `_perform_raw_handshake`
unten). Das ist bewusst so: benutzte der Client hier stattdessen das
`websockets`-Paket, wuerde ein aus dem Environment entferntes `websockets`
schon den TEST-CLIENT an einem `ImportError` scheitern lassen, lange bevor
der eigentliche Server (uvicorn) ueberhaupt gefragt wird - der Test wuerde
zwar rot, aber aus dem falschen Grund, und ein Wechsel des Testclients auf
eine andere Bibliothek koennte die Luecke wieder oeffnen. Ein rohes
TCP-Socket mit von Hand gebauten Upgrade-Headern haengt an nichts, was die
Abwesenheit von `websockets` selbst verdecken koennte - faellt `uvicorn`
mangels `websockets` (und ohne `wsproto`, das dieses Projekt ebenfalls
nicht installiert) auf keine WebSocket-Implementierung zurueck, antwortet
der Server mit `404`, und genau das faengt die Assertion unten ab.

**Bindet an `127.0.0.1`, Port `0`.** `127.0.0.1` verlaesst nie diese
Maschine - kein Netzwerkzugriff im Sinne des Projekt-Constraints (siehe
Aufgabenstellung). Port `0` laesst das Betriebssystem einen freien Port
zuteilen (`socket.getsockname()` liefert ihn danach), damit dieser Test nie
mit einem bereits belegten Port kollidiert, egal wie oft oder parallel er
laeuft.

**Als eigener Marker (`slow`), aber ohne Default-Ausschluss.** Das Starten
und Stoppen eines echten `uvicorn`-Prozesses (im Thread) kostet spuerbar
mehr als die Millisekunden eines In-Prozess-Tests - ein Test, der dafuer
extra ausgewaehlt werden muesste (`-m slow`, oder umgekehrt bewusst
uebersprungen `-m "not slow"`), waere aber ein Test, der vergessen wird.
Der Marker existiert deshalb nur, damit CI ihn bei Bedarf gezielt
herausfiltern oder gezielt isoliert erneut laufen lassen kann - `uv run
pytest` ohne Filter fuehrt ihn immer mit aus."""

from __future__ import annotations

import base64
import os
import socket
import threading
import time

import pytest
import uvicorn

from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_app

STARTUP_TIMEOUT_S = 5.0
SHUTDOWN_TIMEOUT_S = 5.0


class _NullSender:
    """Wie `_NullSender` in `conftest.py` - reines Fuellmaterial fuer
    `Runtime.__init__`, dieser Test prueft nur den Handshake, keinen
    Datenfluss ueber die UDP-Bruecke."""

    async def send(self, key: str, value: float | bool, *, force: bool = False) -> bool:
        return True

    async def close(self) -> None:
        return None


def _perform_raw_handshake(host: str, port: int, path: str, *, timeout: float) -> str:
    """Fuehrt den WebSocket-Handshake (RFC 6455) selbst aus, ueber ein rohes
    TCP-Socket - siehe Modul-Docstring fuer den Grund, warum kein
    WebSocket-Client hier zum Einsatz kommt. Liefert die Statuszeile der
    Antwort zurueck (z. B. "HTTP/1.1 101 Switching Protocols" im Erfolgsfall,
    "HTTP/1.1 404 Not Found" beim genau hier untersuchten Regressionsfall)."""
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).encode("ascii")
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(request)
        response = sock.recv(4096)
    return response.decode("iso-8859-1").split("\r\n", 1)[0]


@pytest.mark.slow
def test_a_real_uvicorn_upgrades_api_live_to_a_websocket(plug_store, no_invoke):
    """Regression fuer den 404-Fehlschlag aus dem Modul-Docstring: ein echter
    `uvicorn`-Server muss `GET /api/live` per Upgrade auf `101 Switching
    Protocols` beantworten, nicht mit `404 Unsupported upgrade request`.

    Bewusst ein GEWOEHNLICHER (nicht-async) Testkoerper: `uvicorn.Server.run`
    baut sich seine eigene `asyncio`-Ereignisschleife in einem eigenen
    Thread auf (siehe `capture_signals` in `uvicorn/server.py` - Signale
    werden dort ausdruecklich nur im Hauptthread behandelt, ein Serverlauf
    im Nebenthread ist also unterstuetzt), unabhaengig von der Schleife, die
    `pytest-asyncio` fuer async-Tests dieser Suite aufspannt."""
    store, _device_id = plug_store
    runtime = Runtime(store, _NullSender())
    app = build_app(store, no_invoke, runtime)

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    # `bind_socket()` bindet bereits (mit vom Betriebssystem zugeteiltem
    # Port, da `port=0`) - der tatsaechliche Port steht danach in
    # `sock.getsockname()`, lange bevor `server.run()` ueberhaupt startet.
    sock = config.bind_socket()
    port = sock.getsockname()[1]

    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while not server.started:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"uvicorn ist nicht innerhalb von {STARTUP_TIMEOUT_S}s gestartet"
                )
            time.sleep(0.01)

        status_line = _perform_raw_handshake(
            "127.0.0.1", port, "/api/live", timeout=STARTUP_TIMEOUT_S
        )
        assert "101" in status_line, f"WebSocket-Upgrade fehlgeschlagen: {status_line!r}"
    finally:
        server.should_exit = True
        thread.join(timeout=SHUTDOWN_TIMEOUT_S)
