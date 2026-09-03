# Live-Feed für Diagnose — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Ansicht „System" zeigt Logzeilen, UDP-Mitschnitt und Kommando-Log laufend statt einmalig beim Öffnen.

**Architecture:** Drei Quellen bekommen eine Beobachterkette nach dem Muster von `Runtime._notify_observers` — der UDP-Sender (er allein weiß, was auf der Leitung war), das vorhandene Kommando-Log, und ein neuer `logging.Handler`. Ein zweiter WebSocket `GET /api/diagnostics/live` schiebt alle drei in den Browser, aufgemacht erst beim Wechsel auf „System". Die Warteschlangen- und Pump-Mechanik wandert dafür aus `api/live.py` in ein gemeinsames Modul.

**Tech Stack:** Python 3.12, FastAPI/Starlette WebSockets, `logging`, Alpine.js 3.17.1 (vendort, kein Build-Schritt), pytest, ruff, mypy strict.

**Entwurfsdokument:** [`docs/superpowers/specs/2026-09-03-diagnose-livefeed-design.md`](../specs/2026-09-03-diagnose-livefeed-design.md). Bei Widerspruch zwischen Plan und Entwurf gilt der Entwurf; melde den Widerspruch.

## Global Constraints

- **Deutsch** in Prosa, Kommentaren, Docstrings, Beschriftungen und Fehlermeldungen; **Englisch** in allen Bezeichnern — auch in Testnamen, JS-Variablen und JSON-Feldnamen.
- Tests laufen **ohne Hardware und ohne Netzzugriff**.
- `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` (strict über `src` und `scripts`) müssen sauber sein. Ausgangslage: **598 Tests grün**.
- **Der Log-Handler darf niemals selbst protokollieren** — auch nicht im Fehlerfall. Ein Handler, der beim Verarbeiten einer Zeile eine Zeile erzeugt, ruft sich endlos auf. Das ist die einzige Stelle im Projekt, an der ein verschluckter Fehler nicht durch einen Logeintrag ausgeglichen wird.
- **Ein Beobachter darf nie den Pfad anhalten, den er beobachtet.** Weder der UDP-Versand noch das Logging dürfen an einem werfenden Beobachter scheitern.
- Jede neue Quelldatei trägt den GPL-Kopf, wie ihn die übrigen Dateien tragen (Kopf einer bestehenden Datei kopieren, vor dem Modul-Docstring).
- Kein `sudo`. **Keine Verbindung zu einem Host im Heimnetz des Anwenders** — unter `10.0.1.56` läuft eine echte Installation.
- `tests/fixtures/VirtualIn/` und `tests/fixtures/VirtualOut/` **nicht lesen**.

## Dateien

| Datei | Zuständigkeit |
|---|---|
| `src/loxmatter/api/streaming.py` | **neu** — Warteschlange und Pumpe, von beiden WebSocket-Routen benutzt |
| `src/loxmatter/api/live.py` | gekürzt — benutzt `streaming` statt eigener Kopie |
| `src/loxmatter/diagnostics/logbuffer.py` | **neu** — `logging.Handler` mit Ring und Beobachtern |
| `src/loxmatter/loxone/sender.py` | ergänzt — Beobachterkette am Mitschnitt |
| `src/loxmatter/api/diagnostics_live.py` | **neu** — die Route `/api/diagnostics/live` |
| `src/loxmatter/loxone/server.py` | ergänzt — Router einhängen, Kommando-Log-Beobachter |
| `src/loxmatter/cli.py` | ergänzt — Handler beim Start anhängen |
| `src/loxmatter/web/index.html`, `app.js`, `style.css` | ergänzt — laufende Anzeige, Filter, Anhalten |

---

### Task 1: Gemeinsame WebSocket-Mechanik herauslösen

Ohne diesen Schritt gäbe es die Warteschlange zweimal — und die erste Korrektur daran war schon nötig (die unbegrenzte Warteschlange, Review-Fix Phase 5).

**Files:**
- Create: `src/loxmatter/api/streaming.py`
- Modify: `src/loxmatter/api/live.py`
- Test: `tests/api/test_streaming.py`

**Interfaces:**
- Consumes: nichts Neues.
- Produces:
  - `QUEUE_MAXSIZE: int` (= 512, Wert aus `api/live.py` übernehmen)
  - `BoundedQueue` — wie `api.live._BoundedQueue`, aber mit **einem** Nutzlast-Objekt statt `(key, value)`: `put(payload: dict[str, object]) -> None`, `async get() -> dict[str, object]`
  - `async watch_for_disconnect(websocket: WebSocket) -> None`
  - `async send_loop(websocket: WebSocket, queue: BoundedQueue) -> None`
  - `accepted_subprotocol(websocket: WebSocket) -> str | None`
  - `async pump(websocket: WebSocket) -> …` ist **nicht** Teil dieser Aufgabe.

- [ ] **Step 1: Den vorhandenen Code lesen**

`src/loxmatter/api/live.py` enthält `_BoundedQueue`, `_watch_for_disconnect`, `_send_loop` und die Subprotokoll-Logik in `build_live_router`. Lies alle vier samt ihrer Docstrings — die Begründungen dort (Drop-Oldest statt Drop-Newest, Log nur beim Übergang, `RuntimeError` als Trennung behandeln, Subprotokoll nur echoen wenn angeboten und **nie** das Token) sind das Ergebnis von Review-Runden und wandern **wörtlich mit**.

- [ ] **Step 2: Den fehlschlagenden Test schreiben**

```python
"""Die WebSocket-Mechanik, die sich beide Live-Routen teilen."""

from __future__ import annotations

import asyncio

import pytest

from loxmatter.api.streaming import QUEUE_MAXSIZE, BoundedQueue


def test_the_queue_drops_the_oldest_entry_when_it_is_full():
    """Drop-Oldest, nicht Drop-Newest: eine Live-Ansicht will den aktuellsten
    Stand, der veraltete Eintrag ist der verzichtbare."""
    queue = BoundedQueue(maxsize=2, connection_label="test")
    queue.put({"n": 1})
    queue.put({"n": 2})
    queue.put({"n": 3})

    assert asyncio.run(_drain(queue, 2)) == [{"n": 2}, {"n": 3}]


async def _drain(queue: BoundedQueue, count: int) -> list[dict[str, object]]:
    return [await queue.get() for _ in range(count)]


def test_putting_never_blocks_and_never_raises():
    """`put` laeuft im Aufrufpfad des Beobachters - beim Log-Handler sogar in
    einem fremden Thread. Wuerde es blockieren oder werfen, riss es den
    beobachteten Pfad mit."""
    queue = BoundedQueue(maxsize=1, connection_label="test")
    for n in range(1000):
        queue.put({"n": n})


def test_the_default_size_matches_what_the_value_stream_used():
    """Uebernommen aus api/live.py, nicht neu gewaehlt: der Wert ist dort
    begruendet, und zwei verschiedene Groessen waeren eine Frage, die
    niemand beantworten kann."""
    assert QUEUE_MAXSIZE == 512
```

- [ ] **Step 3: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/api/test_streaming.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.api.streaming'`

- [ ] **Step 4: Das Modul anlegen**

Verschiebe `_BoundedQueue`, `_watch_for_disconnect`, `_send_loop` und die Subprotokoll-Entscheidung nach `src/loxmatter/api/streaming.py`. Dabei:

- `BoundedQueue` trägt jetzt **ein** Nutzlast-Objekt (`dict[str, object]`) statt `(key, value)`. Grund: der Diagnosekanal schickt verschiedene Nachrichtenarten, ein festes Zweiertupel passt nicht mehr.
- `send_loop` schickt die Nutzlast unverändert (`await websocket.send_json(payload)`).
- `accepted_subprotocol(websocket)` kapselt die Zeilen aus `build_live_router`, die `scope["subprotocols"]` auswerten. Der Kommentar dazu wandert mit — er erklärt, warum nur der Marker und **nie** das Token zurückgegeben wird.
- Die Namen verlieren ihren Unterstrich, weil sie jetzt modulübergreifend benutzt werden.

- [ ] **Step 5: `api/live.py` auf das neue Modul umstellen**

`build_live_router` benutzt `BoundedQueue`, `watch_for_disconnect`, `send_loop`, `accepted_subprotocol`. Der Beobachter baut die Nutzlast selbst:

```python
        def observer(key: str, value: object) -> None:
            queue.put({"key": key, "value": value})
```

Das Nachrichtenformat auf der Leitung bleibt damit **unverändert** — `{"key": …, "value": …}`, genau wie `app.js` es heute liest. Ein Test in `tests/api/test_live_smoke.py` prüft das bereits; er muss ohne Änderung grün bleiben. **Wird er rot, hast du das Format gebrochen und nicht den Test.**

- [ ] **Step 6: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "refactor(api): WebSocket-Mechanik fuer beide Live-Routen herausloesen"
```

---

### Task 2: Beobachterkette am UDP-Mitschnitt

**Files:**
- Modify: `src/loxmatter/loxone/sender.py`
- Test: `tests/loxone/test_sender.py`

**Interfaces:**
- Consumes: `RingBuffer`, `DatagramLogEntry` aus `api.diagnostics` (bereits importiert).
- Produces: auf `UdpSender`
  - `add_datagram_observer(callback: Callable[[DatagramLogEntry], None]) -> None`
  - `remove_datagram_observer(callback: Callable[[DatagramLogEntry], None]) -> None`

**Warum hier und nicht an der Laufzeit:** `Runtime._notify_observers` benachrichtigt bewusst **nicht** beim Full-Resend und nicht beim Absenken eines Impulses (dort begründet). Ein Mitschnitt darauf zeigte etwas anderes als den Verkehr — ausgerechnet im Fall „ging überhaupt etwas raus?". `UdpSender._record_sent` läuft dagegen **nach** jedem `sendto`.

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

```python
def test_a_datagram_observer_sees_every_send():
    """Auch das, was die Laufzeit-Beobachter auslassen: den Full-Resend und
    das Absenken eines Impulses. Das ist der Grund, warum der Mitschnitt am
    Sender haengt und nicht an der Laufzeit."""
    sender = UdpSender("127.0.0.1", 7000)
    seen: list[str] = []
    sender.add_datagram_observer(lambda entry: seen.append(f"{entry.key}={entry.value}"))

    asyncio.run(sender.send("d1_1_onoff", True))
    asyncio.run(sender.send("d1_1_onoff", False, force=True))

    assert seen == ["d1_1_onoff=1", "d1_1_onoff=0"]


def test_a_throwing_observer_does_not_break_the_send_path():
    """Ein Diagnosewerkzeug, das den Pfad anhaelt, den es beobachtet, waere
    schlimmer als gar keins - dieselbe Begruendung wie beim Mitschreiben
    selbst (siehe `_record_sent`)."""
    sender = UdpSender("127.0.0.1", 7000)
    sender.add_datagram_observer(lambda entry: (_ for _ in ()).throw(RuntimeError("kaputt")))

    asyncio.run(sender.send("d1_1_onoff", True))

    assert [entry.key for entry in sender.datagram_log] == ["d1_1_onoff"]


def test_a_removed_observer_is_no_longer_called():
    sender = UdpSender("127.0.0.1", 7000)
    seen: list[str] = []
    observer = lambda entry: seen.append(entry.key)  # noqa: E731
    sender.add_datagram_observer(observer)
    sender.remove_datagram_observer(observer)

    asyncio.run(sender.send("d1_1_onoff", True))

    assert seen == []
```

Prüfe zuerst, wie die vorhandenen Tests in dieser Datei einen `UdpSender` bauen und ob sie einen echten Socket öffnen — richte dich danach, statt einen zweiten Weg einzuführen. Falls `send` dort anders aufgerufen wird als oben, gilt der vorhandene Weg.

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/loxone/test_sender.py -k observer -v`
Expected: FAIL — `AttributeError: 'UdpSender' object has no attribute 'add_datagram_observer'`

- [ ] **Step 3: Die Kette einbauen**

In `UdpSender.__init__` eine Beobachterliste anlegen. `_record_sent` benachrichtigt **nach** dem Anhängen an den Ring, in einem eigenen `try/except` je Beobachter — nach demselben Muster wie `Runtime._notify_observers` (dort nachlesen, inklusive der Kopie der Liste beim Iterieren: ein Beobachter, der sich während seines Aufrufs abmeldet, darf die übrigen nicht stören).

Ein Fehler eines Beobachters wird geloggt und übersprungen. Anders als beim Log-Handler in Task 3 ist das hier **erlaubt und richtig**: der UDP-Pfad protokolliert ohnehin, und eine stille Verschluckung wäre hier die schlechtere Wahl.

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

Run: `uv run pytest tests/loxone/test_sender.py -v`
Expected: PASS

- [ ] **Step 5: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(loxone): Beobachterkette am UDP-Mitschnitt"
```

---

### Task 3: Log-Handler mit Ring und Beobachtern

Die heikelste Aufgabe des Plans. Lies die Randbedingungen oben noch einmal, bevor du anfängst.

**Files:**
- Create: `src/loxmatter/diagnostics/__init__.py`, `src/loxmatter/diagnostics/logbuffer.py`
- Test: `tests/diagnostics/test_logbuffer.py`

**Interfaces:**
- Consumes: `RingBuffer` aus `loxmatter.api.diagnostics`.
- Produces:
  - `LOG_BUFFER_SIZE: int` (= 500)
  - `LogEntry` — Dataclass mit `timestamp: str`, `level: str`, `logger: str`, `message: str`
  - `LogBufferHandler(logging.Handler)` mit `entries: RingBuffer[LogEntry]`, `add_observer(cb)`, `remove_observer(cb)`
  - `install_log_buffer(logger_name: str = "loxmatter", level: int = logging.INFO) -> LogBufferHandler`

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

```python
"""Der Log-Ring, aus dem die Oberflaeche ihre Zeilen bekommt."""

from __future__ import annotations

import logging
import threading

from loxmatter.diagnostics.logbuffer import LogBufferHandler, LogEntry


def _logger_with_handler() -> tuple[logging.Logger, LogBufferHandler]:
    logger = logging.getLogger(f"test.{id(object())}")
    logger.setLevel(logging.INFO)
    handler = LogBufferHandler()
    logger.addHandler(handler)
    return logger, handler


def test_a_log_line_lands_in_the_ring():
    logger, handler = _logger_with_handler()
    logger.warning("Miniserver nicht erreichbar")

    entries = list(handler.entries)
    assert [e.message for e in entries] == ["Miniserver nicht erreichbar"]
    assert entries[0].level == "WARNING"


def test_a_line_from_another_thread_arrives():
    """Logzeilen entstehen in diesem Projekt auch in fremden Threads - aiohttp
    und das chip-SDK. `emit` laeuft dort, wo die Zeile entsteht."""
    logger, handler = _logger_with_handler()
    thread = threading.Thread(target=lambda: logger.info("aus einem Thread"))
    thread.start()
    thread.join()

    assert [e.message for e in handler.entries] == ["aus einem Thread"]


def test_a_throwing_observer_neither_breaks_logging_nor_logs():
    """Die eine Stelle im Projekt, an der ein verschluckter Fehler NICHT
    durch einen Logeintrag ausgeglichen werden darf: der Ausgleich waere
    selbst eine Logzeile, die denselben Handler aufruft - eine
    Endlosschleife."""
    logger, handler = _logger_with_handler()
    handler.add_observer(lambda entry: (_ for _ in ()).throw(RuntimeError("kaputt")))

    logger.info("erste")
    logger.info("zweite")

    assert [e.message for e in handler.entries] == ["erste", "zweite"]


def test_the_observer_sees_each_entry_once():
    logger, handler = _logger_with_handler()
    seen: list[LogEntry] = []
    handler.add_observer(seen.append)

    logger.info("eine Zeile")

    assert [e.message for e in seen] == ["eine Zeile"]


def test_an_exception_is_kept_as_text():
    """Bei einer Stoerung ist der Traceback das Interessanteste - er darf
    nicht verlorengehen, nur weil er nicht in `message` steht."""
    logger, handler = _logger_with_handler()
    try:
        raise ValueError("etwas ging schief")
    except ValueError:
        logger.exception("beim Senden")

    assert "ValueError" in list(handler.entries)[0].message
    assert "etwas ging schief" in list(handler.entries)[0].message
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/diagnostics/test_logbuffer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.diagnostics'`

- [ ] **Step 3: Den Handler schreiben**

`emit` muss:
1. den Datensatz zu einem `LogEntry` formen (`self.format(record)` liefert die Nachricht **einschliesslich** Traceback, wenn einer anhängt),
2. an den Ring anhängen (`collections.deque.append` ist unter CPython atomar, daher kein Schloss nötig — schreib das in den Docstring),
3. die Beobachter aufrufen, **jeden in seinem eigenen `try/except`, das nichts protokolliert und nichts weiterreicht**.

Der Zeitstempel kommt aus `loxmatter.timestamps` — schau nach, welche Funktion die übrigen Ringe benutzen (`DatagramLogEntry.timestamp`), und benutze dieselbe. Zwei verschiedene Zeitformate in einer Ansicht wären für den Leser ein Rätsel.

`install_log_buffer` hängt einen Handler an den benannten Logger, setzt seine Stufe und gibt ihn zurück. **Nicht** an den Root-Logger: die Zeilen fremder Bibliotheken gehören nicht in eine Bedienoberfläche.

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

Run: `uv run pytest tests/diagnostics/ -v`
Expected: PASS

- [ ] **Step 5: Belegen, dass keine Rekursion möglich ist**

Schreib zusätzlich einen Test, der einen Beobachter anhängt, welcher **selbst über denselben Logger protokolliert**. Er muss beweisen, dass das terminiert und nicht in eine Endlosschleife läuft. Beschreib in der Docstring, welche Eigenschaft des Handlers das sicherstellt.

Findest du dabei, dass es **doch** rekursiv würde, ist das ein echter Fund: melde ihn und behebe ihn, statt den Test abzuschwächen.

- [ ] **Step 6: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(diagnostics): Log-Ring mit Beobachtern, ohne Rekursionsgefahr"
```

---

### Task 4: Die Route `/api/diagnostics/live`

**Files:**
- Create: `src/loxmatter/api/diagnostics_live.py`
- Modify: `src/loxmatter/loxone/server.py`
- Test: `tests/api/test_diagnostics_live.py`

**Interfaces:**
- Consumes: `BoundedQueue`, `watch_for_disconnect`, `send_loop`, `accepted_subprotocol`, `QUEUE_MAXSIZE` (Task 1); `UdpSender.add_datagram_observer` (Task 2); `LogBufferHandler.add_observer` (Task 3); `RingBuffer[CommandLogEntry]` aus `loxone/server.py`.
- Produces:

```python
def build_diagnostics_live_router(
    sender: UdpSender | None,
    command_log: RingBuffer[CommandLogEntry],
    log_handler: LogBufferHandler | None,
) -> APIRouter: ...
```

  `sender` und `log_handler` sind optional, weil `build_app` beide schon heute
  optional führt (siehe dort: ein Aufruf ohne Sender ist ein gültiger Zustand).
  Fehlt einer, entfällt der zugehörige Strom — die Route antwortet trotzdem und
  liefert, was sie hat.

**Nachrichtenformat.** Jede Nachricht trägt `kind` und die Felder des jeweiligen Eintrags:

```json
{"kind": "datagram", "key": "d1_2_power", "value": "0",   "timestamp": "…"}
{"kind": "command",  "method": "GET", "path": "/cmd/…", "status": 200, "timestamp": "…"}
{"kind": "log",      "level": "WARNING", "logger": "…", "message": "…", "timestamp": "…"}
```

Die Feldnamen kommen aus `DatagramLogEntry`, `CommandLogEntry` und `LogEntry` — **nicht neu erfinden**, sonst heißt dieselbe Angabe zweimal verschieden.

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

Die Fixture `api_with_runtime` aus `tests/api/conftest.py` liefert einen Client mit
`websocket_connect(url)` gegen die echte ASGI-Anwendung. Prüfe zuerst ihre
tatsächliche Signatur und was sie zurückgibt — der folgende Code richtet sich
danach:

```python
"""Der Live-Kanal fuer Logs, Mitschnitt und Kommando-Log."""

from __future__ import annotations

import logging

import pytest


async def test_a_fresh_datagram_arrives_as_a_message(api_with_runtime):
    """Der Strom haengt am SENDER, nicht an der Laufzeit: nur dort ist
    sichtbar, was tatsaechlich auf der Leitung war - einschliesslich
    Full-Resend und Impulsende, die die Laufzeit-Beobachter auslassen."""
    client, runtime, device_id = api_with_runtime
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        await _drain_snapshot(socket)
        await runtime.on_attribute(device_id, "2/144/4", 230000)
        message = await socket.receive_json()

    assert message["kind"] == "datagram"
    assert message["key"] == f"d{device_id}_2_voltage"


async def test_a_fresh_log_line_arrives_as_a_message(api_with_runtime):
    client, _, _ = api_with_runtime
    async with client.websocket_connect("/api/diagnostics/live") as socket:
        await _drain_snapshot(socket)
        logging.getLogger("loxmatter.test").warning("Miniserver nicht erreichbar")
        message = await socket.receive_json()

    assert message["kind"] == "log"
    assert message["level"] == "WARNING"
    assert message["message"] == "Miniserver nicht erreichbar"


async def test_the_connection_starts_with_a_snapshot(api_with_runtime):
    """Ohne die Momentaufnahme klaffte eine Luecke zwischen 'einmal
    abrufen' und 'ab jetzt zuhoeren' - und die Ansicht waere beim Oeffnen
    leer, bis zufaellig etwas passiert."""
    client, runtime, device_id = api_with_runtime
    await runtime.on_attribute(device_id, "2/144/4", 230000)

    async with client.websocket_connect("/api/diagnostics/live") as socket:
        first = await socket.receive_json()

    assert first["kind"] == "datagram"
    assert first["key"] == f"d{device_id}_2_voltage"
```

`_drain_snapshot` liest die Momentaufnahme weg, bis der erste Live-Eintrag
kommt. Schreib sie so, dass sie **nicht** unbegrenzt wartet, wenn nichts
kommt — ein Test, der haengt, statt fehlzuschlagen, ist schlimmer als keiner.

Der Test für den Token-Fall gehört nach `tests/api/test_security.py`, wo die
übrigen stehen. **Steht dort eine Liste aller geschützten Routen, muss die neue
darin auftauchen** — genau so fällt eine vergessene Route auf.

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/api/test_diagnostics_live.py -v`
Expected: FAIL — Modul fehlt

- [ ] **Step 3: Die Route bauen**

Nach dem Muster von `build_live_router` (lies sie zuerst): Subprotokoll auswerten, `accept`, Warteschlange anlegen, Beobachter anmelden, `watch_for_disconnect` und `send_loop` nebeneinander laufen lassen, im `finally` **alle drei** Beobachter wieder abmelden.

Vor dem Anmelden der Beobachter die Momentaufnahme schicken: die letzten Einträge je Ring, jeder in der Form oben. Wähle eine Obergrenze je Strom und begründe sie im Docstring — 500 × 3 auf einen Schlag wären beim Öffnen der Ansicht eine spürbare Nachricht.

- [ ] **Step 4: Router einhängen**

In `loxone/server.py`, neben `build_live_router`, mit `dependencies=api_guard` — sonst wäre die Route ungeschützt. Der Kommando-Log-Ring liegt dort bereits als lokale Variable; er braucht ebenfalls eine Beobachterkette. Entscheide, ob du sie an `RingBuffer` selbst hängst oder an die Middleware `_record_command`, und begründe es.

Hängst du sie an `RingBuffer`, gilt dieselbe Regel wie überall: ein werfender Beobachter darf den Aufrufpfad nicht anhalten.

- [ ] **Step 5: Test laufen lassen, Erfolg bestätigen**

Run: `uv run pytest tests/api/ -v`
Expected: PASS

- [ ] **Step 6: Rauchtest gegen echtes uvicorn**

`tests/api/test_live_smoke.py` prüft `/api/live` mit einem rohen RFC-6455-Handshake, **ohne** WebSocket-Bibliothek. Der Grund steht dort und im Ledger: ein In-Process-Test war grün, während `/api/live` in **jeder** echten Installation 404 lieferte, weil uvicorn gar keine WebSocket-Implementierung installiert hatte. Ergänze die neue Route dort in derselben Form.

- [ ] **Step 7: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(api): WebSocket fuer Logs, Mitschnitt und Kommando-Log"
```

---

### Task 5: Handler beim Start anhängen

**Files:**
- Modify: `src/loxmatter/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `install_log_buffer` (Task 3), `build_diagnostics_live_router` (Task 4).
- Produces: nichts Neues.

- [ ] **Step 1: Den fehlschlagenden Test schreiben**

Ein Test, der belegt: nach dem Aufbau der Anwendung hängt ein `LogBufferHandler` am Logger `loxmatter`, und eine über `logging.getLogger("loxmatter.test").info(...)` erzeugte Zeile landet in seinem Ring. Schau in `tests/test_cli.py`, wie dort die Anwendung ohne echten Lauf aufgebaut wird, und benutze denselben Weg.

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `uv run pytest tests/test_cli.py -k log_buffer -v`
Expected: FAIL

- [ ] **Step 3: Einhängen**

In `cli.py`s `_run` (oder dort, wo `build_app` aufgerufen wird — nachsehen): `install_log_buffer()` aufrufen und den Handler an `build_app` durchreichen, damit die Route ihn bekommt.

**Genau einmal anhängen.** Ein zweiter Aufruf hängte einen zweiten Handler an denselben Logger, und jede Zeile stünde doppelt im Ring. Schreib in den Docstring, wie du das sicherstellst.

- [ ] **Step 4: Test laufen lassen, Erfolg bestätigen**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(cli): Log-Ring beim Start anhaengen"
```

---

### Task 6: Die Ansicht „System"

**Files:**
- Modify: `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `src/loxmatter/web/style.css`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: die Route aus Task 4.
- Produces: nichts, was ein späterer Task braucht.

- [ ] **Step 1: Den bestehenden Weg lesen**

`app.js` hat bereits `connectLive()` für den Wertekanal, mit Wiederverbindung und wachsender Wartezeit. Der Diagnosekanal folgt demselben Muster, **öffnet aber nur beim Wechsel auf „System" und schliesst beim Verlassen**.

Lies auch den Kommentar in `index.html` bei `x-data="app()"`: dort steht, warum **kein** `x-init="init()"` danebensteht. Alpine 3 ruft `init()` selbst auf; ein zusätzliches `x-init` erzeugte pro Tab dauerhaft zwei offene Kanäle. Trag es nicht ein.

- [ ] **Step 2: Zustand und Verbindung**

In `app()`: Listen für die drei Ströme, ein `diagnosticsSocket`, `diagnosticsPaused`, `hideNoise` (Vorgabe `true`), `logLevel` (Vorgabe `"INFO"`), und eine Obergrenze der gehaltenen Zeilen je Strom.

`selectView` öffnet den Kanal bei `"system"` und schliesst ihn bei jedem anderen Wert.

**Der Filter wirkt nur auf die Anzeige, nicht auf die gehaltenen Zeilen** (Entwurf 4): wer ihn ausschaltet, sieht die vorhandenen sofort, statt auf neue zu warten.

Was als „Rauschen" gilt: `bridge_alive` und alles, was im selben Schwall wie ein Full-Resend kommt. Entscheide, woran du das erkennst, und schreib die Regel als Kommentar hin — ein Filter, dessen Kriterium niemand nachlesen kann, ist beim nächsten Zweifel wertlos.

- [ ] **Step 3: Markup und Gestaltung**

Drei Bereiche, darüber die vier Bedienelemente aus dem Entwurf. Im vorhandenen Stil, keine neue Farbwelt.

- [ ] **Step 4: Test**

In `tests/api/test_web.py`, im Stil der dortigen Tests: das ausgelieferte Markup enthält die Bedienelemente, und `app.js` verbindet sich auf `/api/diagnostics/live`. **Die Testdocstring muss ehrlich sagen, was sie nicht belegt** — es läuft keine Browser-Engine, ein Markup-Test zeigt, dass etwas ausgeliefert wird, nicht dass es funktioniert.

- [ ] **Step 5: Prüfungen und Commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
node --check src/loxmatter/web/app.js
git add -A
git commit -m "feat(web): Logs, Mitschnitt und Kommandos laufend statt einmalig"
```

---

### Task 7: Dokumentation

**Files:**
- Modify: `docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md`, `docs/superpowers/specs/2026-09-03-diagnose-livefeed-design.md`, `README.md`

- [ ] **Step 1: Hauptdokument**

Abschnitt 10.5 (Diagnose) nennt heute nur die abrufbaren Routen. Ergänze den Live-Kanal und den Log-Ring, mit einem Verweis auf den neuen Entwurf. Abschnitt 8.3 bekommt einen Satz, dass es jetzt **zwei** WebSockets gibt und warum sie getrennt sind.

- [ ] **Step 2: Offene Punkte im neuen Entwurf**

Abschnitt 7 hat drei offene Punkte. Streiche, was durch die Umsetzung entschieden wurde, und trag ein, wie. Was offen bleibt, bleibt stehen.

- [ ] **Step 3: README**

Ein Absatz bei der Beschreibung der Oberfläche: was die Ansicht „System" jetzt zeigt, und dass die Logzeilen dieselben sind wie in `docker logs`.

- [ ] **Step 4: Prüfung und Commit**

```bash
uv run ruff format --check src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "docs: Live-Feed in Hauptdokument und README nachziehen"
```

---

## Abschlusskriterien

Die Arbeit ist fertig, wenn:

1. `uv run pytest` ohne Hardware und ohne Netz durchläuft,
2. eine Logzeile **aus einem fremden Thread** im Ring landet,
3. ein werfender Beobachter weder das Logging noch den UDP-Versand anhält, und der Log-Handler dabei **keine neue Logzeile erzeugt**,
4. der Mitschnitt das enthält, was der Sender geschickt hat — **einschliesslich** Impulsende und Full-Resend, die die Laufzeit-Beobachter auslassen,
5. die Route ohne Token mit 401 antwortet und in der Liste der geschützten Routen steht,
6. ein Rauchtest mit rohem RFC-6455-Handshake die Route gegen echtes uvicorn belegt,
7. das Nachrichtenformat von `/api/live` **unverändert** ist.

**Nicht Teil dieser Arbeit:** ein Herunterladen des Mitschnitts als Datei, ein serverseitiger Stufenfilter, und die fehlenden Systemcheck-Prüfungen (mDNS, Dongle, OTBR, Thread-Netz) aus dem Hauptdokument.
