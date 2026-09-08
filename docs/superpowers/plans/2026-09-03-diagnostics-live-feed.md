# Live feed for diagnostics — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The "System" view shows log lines, UDP capture, and command log continuously instead of once on open.

**Architecture:** Three sources get an observer chain following the pattern of `Runtime._notify_observers` — the UDP sender (it alone knows what was on the wire), the existing command log, and a new `logging.Handler`. A second WebSocket `GET /api/diagnostics/live` pushes all three into the browser, opened only on switching to "System". For this, the queue and pump mechanics move out of `api/live.py` into a shared module.

**Tech Stack:** Python 3.12, FastAPI/Starlette WebSockets, `logging`, Alpine.js 3.17.1 (vendored, no build step), pytest, ruff, mypy strict.

**Design document:** [`docs/superpowers/specs/2026-09-03-diagnostics-live-feed-design.md`](../specs/2026-09-03-diagnostics-live-feed-design.md). If plan and design conflict, the design wins; report the conflict.

## Global Constraints

- **German** in prose, comments, docstrings, labels, and error messages; **English** in all identifiers — including test names, JS variables, and JSON field names.
- Tests run **without hardware and without network access**.
- `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` (strict over `src` and `scripts`) must be clean. Starting point: **598 tests green**.
- **The log handler must never log itself** — not even in the error case. A handler that produces a line while processing a line calls itself endlessly. This is the one place in the project where a swallowed error is not offset by a log entry.
- **An observer must never halt the path it observes.** Neither the UDP send nor the logging may fail because of a raising observer.
- Every new source file carries the GPL header, as the other files do (copy the header of an existing file, before the module docstring).
- No `sudo`. **No connection to a host on the user's home network** — a real installation runs at `10.0.1.56`.
- **Do not read** `tests/fixtures/VirtualIn/` and `tests/fixtures/VirtualOut/`.

## Files

| File | Responsibility |
|---|---|
| `src/loxmatter/api/streaming.py` | **new** — queue and pump, used by both WebSocket routes |
| `src/loxmatter/api/live.py` | shortened — uses `streaming` instead of its own copy |
| `src/loxmatter/diagnostics/logbuffer.py` | **new** — `logging.Handler` with a ring and observers |
| `src/loxmatter/loxone/sender.py` | extended — observer chain on the capture |
| `src/loxmatter/api/diagnostics_live.py` | **new** — the `/api/diagnostics/live` route |
| `src/loxmatter/loxone/server.py` | extended — hook up router, command-log observer |
| `src/loxmatter/cli.py` | extended — attach handler at start |
| `src/loxmatter/web/index.html`, `app.js`, `style.css` | extended — continuous display, filters, pause |

---

### Task 1: Extract the shared WebSocket mechanics

Without this step the queue would exist twice — and the first fix to it was already necessary (the unbounded queue, review fix phase 5).

**Files:**
- Create: `src/loxmatter/api/streaming.py`
- Modify: `src/loxmatter/api/live.py`
- Test: `tests/api/test_streaming.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `QUEUE_MAXSIZE: int` (= 512, take the value over from `api/live.py`)
  - `BoundedQueue` — like `api.live._BoundedQueue`, but with **one** payload object instead of `(key, value)`: `put(payload: dict[str, object]) -> None`, `async get() -> dict[str, object]`
  - `async watch_for_disconnect(websocket: WebSocket) -> None`
  - `async send_loop(websocket: WebSocket, queue: BoundedQueue) -> None`
  - `accepted_subprotocol(websocket: WebSocket) -> str | None`
  - `async pump(websocket: WebSocket) -> …` is **not** part of this task.

- [ ] **Step 1: Read the existing code**

`src/loxmatter/api/live.py` contains `_BoundedQueue`, `_watch_for_disconnect`, `_send_loop`, and the subprotocol logic in `build_live_router`. Read all four along with their docstrings — the reasoning there (drop-oldest instead of drop-newest, log only on transitions, treat `RuntimeError` as a disconnect, echo the subprotocol only if offered and **never** the token) is the result of review rounds and moves over **verbatim**.

- [ ] **Step 2: Write the failing test**

```python
"""The WebSocket mechanics shared by both live routes."""

from __future__ import annotations

import asyncio

import pytest

from loxmatter.api.streaming import QUEUE_MAXSIZE, BoundedQueue


def test_the_queue_drops_the_oldest_entry_when_it_is_full():
    """Drop-oldest, not drop-newest: a live view wants the most current
    state, the stale entry is the dispensable one."""
    queue = BoundedQueue(maxsize=2, connection_label="test")
    queue.put({"n": 1})
    queue.put({"n": 2})
    queue.put({"n": 3})

    assert asyncio.run(_drain(queue, 2)) == [{"n": 2}, {"n": 3}]


async def _drain(queue: BoundedQueue, count: int) -> list[dict[str, object]]:
    return [await queue.get() for _ in range(count)]


def test_putting_never_blocks_and_never_raises():
    """`put` runs in the observer's call path - for the log handler even
    on a foreign thread. If it blocked or raised, it would take the
    observed path down with it."""
    queue = BoundedQueue(maxsize=1, connection_label="test")
    for n in range(1000):
        queue.put({"n": n})


def test_the_default_size_matches_what_the_value_stream_used():
    """Taken over from api/live.py, not newly chosen: the value is
    justified there, and two different sizes would be a question nobody
    could answer."""
    assert QUEUE_MAXSIZE == 512
```

- [ ] **Step 3: Run test, confirm failure**

Run: `uv run pytest tests/api/test_streaming.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.api.streaming'`

- [ ] **Step 4: Create the module**

Move `_BoundedQueue`, `_watch_for_disconnect`, `_send_loop`, and the subprotocol decision to `src/loxmatter/api/streaming.py`. While doing so:

- `BoundedQueue` now carries **one** payload object (`dict[str, object]`) instead of `(key, value)`. Reason: the diagnostics channel sends different kinds of messages, a fixed pair no longer fits.
- `send_loop` sends the payload unchanged (`await websocket.send_json(payload)`).
- `accepted_subprotocol(websocket)` encapsulates the lines from `build_live_router` that evaluate `scope["subprotocols"]`. The comment on it moves along — it explains why only the marker is returned and **never** the token.
- The names lose their leading underscore, because they are now used across modules.

- [ ] **Step 5: Switch `api/live.py` to the new module**

`build_live_router` uses `BoundedQueue`, `watch_for_disconnect`, `send_loop`, `accepted_subprotocol`. The observer builds the payload itself:

```python
        def observer(key: str, value: object) -> None:
            queue.put({"key": key, "value": value})
```

The message format on the wire therefore stays **unchanged** — `{"key": …, "value": …}`, exactly as `app.js` reads it today. A test in `tests/api/test_live_smoke.py` already checks this; it must stay green without changes. **If it goes red, you broke the format, not the test.**

- [ ] **Step 6: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "refactor(api): WebSocket-Mechanik fuer beide Live-Routen herausloesen"
```

---

### Task 2: Observer chain on the UDP capture

**Files:**
- Modify: `src/loxmatter/loxone/sender.py`
- Test: `tests/loxone/test_sender.py`

**Interfaces:**
- Consumes: `RingBuffer`, `DatagramLogEntry` from `api.diagnostics` (already imported).
- Produces: on `UdpSender`
  - `add_datagram_observer(callback: Callable[[DatagramLogEntry], None]) -> None`
  - `remove_datagram_observer(callback: Callable[[DatagramLogEntry], None]) -> None`

**Why here and not on the runtime:** `Runtime._notify_observers` deliberately does **not** notify on a full resend and not on the falling edge of a pulse (justified there). A capture built on it would show something other than the traffic — in exactly the case "did anything even go out?". `UdpSender._record_sent`, on the other hand, runs **after** every `sendto`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_datagram_observer_sees_every_send():
    """Including what the runtime observers omit: the full resend and the
    falling edge of a pulse. This is the reason the capture hangs off the
    sender and not off the runtime."""
    sender = UdpSender("127.0.0.1", 7000)
    seen: list[str] = []
    sender.add_datagram_observer(lambda entry: seen.append(f"{entry.key}={entry.value}"))

    asyncio.run(sender.send("d1_1_onoff", True))
    asyncio.run(sender.send("d1_1_onoff", False, force=True))

    assert seen == ["d1_1_onoff=1", "d1_1_onoff=0"]


def test_a_throwing_observer_does_not_break_the_send_path():
    """A diagnostic tool that halts the path it observes would be worse
    than none at all - the same reasoning as for the capture itself (see
    `_record_sent`)."""
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

Check first how the existing tests in this file construct a `UdpSender` and whether they open a real socket — follow that instead of introducing a second way. If `send` is called there differently than above, the existing way wins.

- [ ] **Step 2: Run test, confirm failure**

Run: `uv run pytest tests/loxone/test_sender.py -k observer -v`
Expected: FAIL — `AttributeError: 'UdpSender' object has no attribute 'add_datagram_observer'`

- [ ] **Step 3: Build the chain**

In `UdpSender.__init__`, create an observer list. `_record_sent` notifies **after** appending to the ring, in its own `try/except` per observer — following the same pattern as `Runtime._notify_observers` (read it there, including the copy of the list made while iterating: an observer that unregisters itself during its own call must not disturb the rest).

An observer's error is logged and skipped. Unlike the log handler in task 3, that is **allowed and correct** here: the UDP path already logs anyway, and silently swallowing it here would be the worse choice.

- [ ] **Step 4: Run test, confirm success**

Run: `uv run pytest tests/loxone/test_sender.py -v`
Expected: PASS

- [ ] **Step 5: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(loxone): Beobachterkette am UDP-Mitschnitt"
```

---

### Task 3: Log handler with a ring and observers

The trickiest task of the plan. Re-read the constraints above before starting.

**Files:**
- Create: `src/loxmatter/diagnostics/__init__.py`, `src/loxmatter/diagnostics/logbuffer.py`
- Test: `tests/diagnostics/test_logbuffer.py`

**Interfaces:**
- Consumes: `RingBuffer` from `loxmatter.api.diagnostics`.
- Produces:
  - `LOG_BUFFER_SIZE: int` (= 500)
  - `LogEntry` — dataclass with `timestamp: str`, `level: str`, `logger: str`, `message: str`
  - `LogBufferHandler(logging.Handler)` with `entries: RingBuffer[LogEntry]`, `add_observer(cb)`, `remove_observer(cb)`
  - `install_log_buffer(logger_name: str = "loxmatter", level: int = logging.INFO) -> LogBufferHandler`

- [ ] **Step 1: Write the failing test**

```python
"""The log ring the UI gets its lines from."""

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
    """Log lines in this project also arise on foreign threads - aiohttp
    and the chip SDK. `emit` runs wherever the line originates."""
    logger, handler = _logger_with_handler()
    thread = threading.Thread(target=lambda: logger.info("aus einem Thread"))
    thread.start()
    thread.join()

    assert [e.message for e in handler.entries] == ["aus einem Thread"]


def test_a_throwing_observer_neither_breaks_logging_nor_logs():
    """The one place in the project where a swallowed error must NOT be
    offset by a log entry: the offsetting entry would itself be a log
    line, which calls the same handler - an infinite loop."""
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
    """During a fault, the traceback is the most interesting part - it
    must not be lost just because it isn't in `message`."""
    logger, handler = _logger_with_handler()
    try:
        raise ValueError("etwas ging schief")
    except ValueError:
        logger.exception("beim Senden")

    assert "ValueError" in list(handler.entries)[0].message
    assert "etwas ging schief" in list(handler.entries)[0].message
```

- [ ] **Step 2: Run test, confirm failure**

Run: `uv run pytest tests/diagnostics/test_logbuffer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loxmatter.diagnostics'`

- [ ] **Step 3: Write the handler**

`emit` must:
1. shape the record into a `LogEntry` (`self.format(record)` delivers the message **including** the traceback, if one is attached),
2. append it to the ring (`collections.deque.append` is atomic under CPython, so no lock is needed — write that into the docstring),
3. call the observers, **each in its own `try/except` that logs nothing and re-raises nothing**.

The timestamp comes from `loxmatter.timestamps` — check which function the other rings use (`DatagramLogEntry.timestamp`), and use the same one. Two different timestamp formats in one view would be a puzzle for the reader.

`install_log_buffer` attaches a handler to the named logger, sets its level, and returns it. **Not** to the root logger: lines from third-party libraries don't belong in a UI.

- [ ] **Step 4: Run test, confirm success**

Run: `uv run pytest tests/diagnostics/ -v`
Expected: PASS

- [ ] **Step 5: Prove no recursion is possible**

Additionally write a test that attaches an observer which **itself logs through the same logger**. It must prove that this terminates and does not run into an infinite loop. Describe in the docstring which property of the handler ensures that.

If you find it **would** actually be recursive, that is a real finding: report it and fix it, instead of weakening the test.

- [ ] **Step 6: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(diagnostics): Log-Ring mit Beobachtern, ohne Rekursionsgefahr"
```

---

### Task 4: The `/api/diagnostics/live` route

**Files:**
- Create: `src/loxmatter/api/diagnostics_live.py`
- Modify: `src/loxmatter/loxone/server.py`
- Test: `tests/api/test_diagnostics_live.py`

**Interfaces:**
- Consumes: `BoundedQueue`, `watch_for_disconnect`, `send_loop`, `accepted_subprotocol`, `QUEUE_MAXSIZE` (task 1); `UdpSender.add_datagram_observer` (task 2); `LogBufferHandler.add_observer` (task 3); `RingBuffer[CommandLogEntry]` from `loxone/server.py`.
- Produces:

```python
def build_diagnostics_live_router(
    sender: UdpSender | None,
    command_log: RingBuffer[CommandLogEntry],
    log_handler: LogBufferHandler | None,
) -> APIRouter: ...
```

  `sender` and `log_handler` are optional, because `build_app` already
  treats both as optional today (see there: a call without a sender is a
  valid state). If one is missing, the corresponding stream is dropped —
  the route still answers and delivers what it has.

**Message format.** Every message carries `kind` and the fields of the respective entry:

```json
{"kind": "datagram", "key": "d1_2_power", "value": "0",   "timestamp": "…"}
{"kind": "command",  "method": "GET", "path": "/cmd/…", "status": 200, "timestamp": "…"}
{"kind": "log",      "level": "WARNING", "logger": "…", "message": "…", "timestamp": "…"}
```

The field names come from `DatagramLogEntry`, `CommandLogEntry`, and `LogEntry` — **don't invent new ones**, or the same piece of data ends up named differently twice.

- [ ] **Step 1: Write the failing test**

The `api_with_runtime` fixture from `tests/api/conftest.py` provides a client with `websocket_connect(url)` against the real ASGI application. Check its actual signature and what it returns first — the code below follows that:

```python
"""The live channel for logs, capture, and command log."""

from __future__ import annotations

import logging

import pytest


async def test_a_fresh_datagram_arrives_as_a_message(api_with_runtime):
    """The stream hangs off the SENDER, not off the runtime: only there
    is it visible what was actually on the wire - including the full
    resend and the falling edge of a pulse, which the runtime observers
    omit."""
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
    """Without the snapshot, a gap would open between 'fetch once' and
    'listen from now on' - and the view would be empty on open, until
    something happened to occur."""
    client, runtime, device_id = api_with_runtime
    await runtime.on_attribute(device_id, "2/144/4", 230000)

    async with client.websocket_connect("/api/diagnostics/live") as socket:
        first = await socket.receive_json()

    assert first["kind"] == "datagram"
    assert first["key"] == f"d{device_id}_2_voltage"
```

`_drain_snapshot` reads away the snapshot until the first live entry
arrives. Write it so it does **not** wait indefinitely if nothing comes —
a test that hangs instead of failing is worse than none.

The test for the token case belongs in `tests/api/test_security.py`, where
the others live. **If a list of all protected routes exists there, the new
one must show up in it** — that is exactly how a forgotten route gets
noticed.

- [ ] **Step 2: Run test, confirm failure**

Run: `uv run pytest tests/api/test_diagnostics_live.py -v`
Expected: FAIL — module missing

- [ ] **Step 3: Build the route**

Following the pattern of `build_live_router` (read it first): evaluate the subprotocol, `accept`, create a queue, register observers, run `watch_for_disconnect` and `send_loop` side by side, unregister **all three** observers again in `finally`.

Before registering the observers, send the snapshot: the latest entries per ring, each in the form above. Choose an upper bound per stream and justify it in the docstring — 500 × 3 all at once would be a noticeable burst of messages when opening the view.

- [ ] **Step 4: Hook up the router**

In `loxone/server.py`, next to `build_live_router`, with `dependencies=api_guard` — otherwise the route would be unprotected. The command-log ring already sits there as a local variable; it also needs an observer chain. Decide whether to hang it off `RingBuffer` itself or off the `_record_command` middleware, and justify it.

If you hang it off `RingBuffer`, the same rule applies as everywhere: a raising observer must not halt the calling path.

- [ ] **Step 5: Run test, confirm success**

Run: `uv run pytest tests/api/ -v`
Expected: PASS

- [ ] **Step 6: Smoke test against real uvicorn**

`tests/api/test_live_smoke.py` checks `/api/live` with a raw RFC-6455 handshake, **without** a WebSocket library. The reason is documented there and in the ledger: an in-process test was green while `/api/live` returned 404 in **every** real installation, because uvicorn had no WebSocket implementation installed at all. Add the new route there in the same form.

- [ ] **Step 7: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(api): WebSocket fuer Logs, Mitschnitt und Kommando-Log"
```

---

### Task 5: Attach the handler at start

**Files:**
- Modify: `src/loxmatter/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `install_log_buffer` (task 3), `build_diagnostics_live_router` (task 4).
- Produces: nothing new.

- [ ] **Step 1: Write the failing test**

A test proving: after the application is built, a `LogBufferHandler` hangs off the `loxmatter` logger, and a line produced via `logging.getLogger("loxmatter.test").info(...)` lands in its ring. Look at how `tests/test_cli.py` builds the application without a real run, and use the same path.

- [ ] **Step 2: Run test, confirm failure**

Run: `uv run pytest tests/test_cli.py -k log_buffer -v`
Expected: FAIL

- [ ] **Step 3: Hook it up**

In `cli.py`'s `_run` (or wherever `build_app` is called — check): call `install_log_buffer()` and pass the handler through to `build_app`, so the route gets it.

**Attach exactly once.** A second call would attach a second handler to the same logger, and every line would end up twice in the ring. Write in the docstring how you ensure that.

- [ ] **Step 4: Run test, confirm success**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "feat(cli): Log-Ring beim Start anhaengen"
```

---

### Task 6: The "System" view

**Files:**
- Modify: `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `src/loxmatter/web/style.css`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: the route from task 4.
- Produces: nothing that a later task needs.

- [ ] **Step 1: Read the existing path**

`app.js` already has `connectLive()` for the values channel, with reconnection and growing backoff. The diagnostics channel follows the same pattern, **but only opens on switching to "System" and closes on leaving it**.

Also read the comment in `index.html` at `x-data="app()"`: it explains why **no** `x-init="init()"` sits next to it. Alpine 3 calls `init()` itself; an additional `x-init` would permanently create two open channels per tab. Do not add it.

- [ ] **Step 2: State and connection**

In `app()`: lists for the three streams, a `diagnosticsSocket`, `diagnosticsPaused`, `hideNoise` (default `true`), `logLevel` (default `"INFO"`), and an upper bound on the lines held per stream.

`selectView` opens the channel on `"system"` and closes it on every other value.

**The filter only affects the display, not the lines held** (design section 4): whoever turns it off sees the existing ones immediately, instead of waiting for new ones.

What counts as "noise": `bridge_alive` and everything that arrives in the same burst as a full resend. Decide how you recognize that, and write the rule down as a comment — a filter whose criterion nobody can look up is worthless the next time there's doubt.

- [ ] **Step 3: Markup and styling**

Three sections, with the four controls from the design above them. In the existing style, no new color scheme.

- [ ] **Step 4: Test**

In `tests/api/test_web.py`, in the style of the tests there: the delivered markup contains the controls, and `app.js` connects to `/api/diagnostics/live`. **The test docstring must honestly say what it does not prove** — no browser engine runs, a markup test shows that something is delivered, not that it works.

- [ ] **Step 5: Checks and commit**

```bash
uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
node --check src/loxmatter/web/app.js
git add -A
git commit -m "feat(web): Logs, Mitschnitt und Kommandos laufend statt einmalig"
```

---

### Task 7: Documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md`, `docs/superpowers/specs/2026-09-03-diagnostics-live-feed-design.md`, `README.md`

- [ ] **Step 1: Main document**

Section 10.5 (diagnostics) currently only names the routes you can poll. Add the live channel and the log ring, with a reference to the new design. Section 8.3 gets a sentence that there are now **two** WebSockets and why they are separate.

- [ ] **Step 2: Open points in the new design**

Section 7 has three open points. Strike what the implementation decided, and note how. What stays open, stays.

- [ ] **Step 3: README**

A paragraph in the description of the UI: what the "System" view now shows, and that the log lines are the same as in `docker logs`.

- [ ] **Step 4: Check and commit**

```bash
uv run ruff format --check src tests && uv run ruff check src tests && uv run mypy && uv run pytest -q
git add -A
git commit -m "docs: Live-Feed in Hauptdokument und README nachziehen"
```

---

## Completion criteria

The work is done when:

1. `uv run pytest` passes without hardware and without network,
2. a log line **from a foreign thread** lands in the ring,
3. a raising observer halts neither the logging nor the UDP send, and the log handler thereby **produces no new log line**,
4. the capture contains what the sender actually sent — **including** falling pulse edges and full resends, which the runtime observers omit,
5. the route answers with 401 without a token and appears in the list of protected routes,
6. a smoke test with a raw RFC-6455 handshake proves the route against real uvicorn,
7. the message format of `/api/live` is **unchanged**.

**Not part of this work:** downloading the capture as a file, a server-side level filter, and the missing system-check checks (mDNS, dongle, OTBR, Thread network) from the main document.
