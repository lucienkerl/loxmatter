# Live feed for logs, UDP capture, and commands

Design, September 3, 2026. Extends
[the main document](2026-09-01-matter-loxone-bridge-design.md), sections
8.3 (WebSocket from the same subscription) and 10.5 (diagnostics).

## 1. The problem

The "System" view fetches the capture, command log, and system check
**once**, on open. Anyone hunting for a fault therefore keeps hitting
reload — and never sees what is happening right now, only what was
already over at the last click.

For log lines there is no capture at all. They go to `stderr` and
therefore to `docker logs`. Anyone who wants to see them needs a shell on
the host — and exactly at the moment you need them, you're sitting in
front of the browser.

## 2. Three streams, each from its honest source

### 2.1 UDP capture: from the sender, not from the runtime

`UdpSender._record_sent` records **after** the `sendto`. That is the only
place that knows what was actually on the wire.

The more convenient path would be the runtime's existing observer chain
(`Runtime._notify_observers`), which already feeds the values UI. For this
purpose, though, it is **wrong**: it deliberately does not notify on a
full resend and not on the falling edge of a pulse (both justified in
`runtime.py`). A feed built on it would be called "capture" and would show
something other than the traffic — in exactly the case it exists for:
"did anything even go out?"

`UdpSender` therefore gets an observer chain following the same pattern as
`Runtime`: called only after sending, an observer's error is swallowed, so
that a diagnostic tool never halts the path it is observing.

### 2.2 Command log: already present, just not pushed

The `/cmd` calls from Loxone already live as a `RingBuffer` in
`loxone/server.py`. They come along as a third message kind — the same
mechanism, and in the view they already sit next to each other.

### 2.3 Log lines: new

A `logging.Handler` that writes into a ring and notifies observers. It
attaches to the `loxmatter` logger (not to the root logger: lines from
third-party libraries don't belong in a UI), level INFO.

Three properties that are not obvious and shape the design:

**It never logs itself.** A handler that produces a line while processing
a line calls itself endlessly. That also applies to its observers: an
error there is swallowed and **not logged**. This is the one place in the
project where a swallowed error must not be offset by a log entry.

The safeguard for this is a re-entrancy lock around the observer loop,
kept thread-local (`LogBufferHandler`, `diagnostics/logbuffer.py`,
implemented). **It deliberately does not cover `self.format(record)`
itself** — that is a gap, not a claim to the contrary: a log argument
whose `__str__`/`__repr__` itself logs through the same logger recurses
through `format()` unchecked until `RecursionError`, without the lock ever
kicking in. No caller in the project today does that (all `%` arguments
are simple values); whoever extends the lock to `format()` still has to
append the entry to the ring before it aborts — the line should not be
lost just because it triggered the error.

**It runs on the calling thread.** `logging.Handler.emit` is executed
wherever the line originates — in this project, that also means from
aiohttp and the chip SDK, i.e. from foreign threads. The handler itself
therefore only appends to the ring (`collections.deque.append` is atomic
under CPython) and calls its observers synchronously on the same thread;
it never waits and knows no asyncio primitives itself (see
`diagnostics/logbuffer.py`, `LogBufferHandler.add_observer`: an observer
is likewise not allowed to block, per the contract). **Waking the event
loop is therefore the responsibility of the respective observer, not of
the handler** — unlike what an earlier version of this design claimed
here. For the diagnostics feed, this observer is `on_log` in
`api/diagnostics_live.py`: it captures the running loop before it
registers, and enqueues via
`loop.call_soon_threadsafe(queue.put, ...)` instead of calling
`queue.put(...)` directly — the first implementation did not do that, and
a log line from a genuine foreign thread could under some circumstances
never have arrived, because an already-sleeping event loop is only
reliably woken from a foreign thread via `call_soon_threadsafe` (not via
ordinary queue access).

**It moves no secrets.** The feed shows what is in `docker logs` anyway,
and sits behind the same token protection as all `/api` routes. The
fabric backup deliberately logs nothing at all (see
`api/diagnostics.py`); that stays that way.

### 2.4 Sizes

All three rings hold 500 entries, like the existing ones. With two
devices that covers around a quarter hour: every 300s a full resend runs
with around 140 datagrams, plus the heartbeat every 30s.

## 3. Transport

A second WebSocket, `GET /api/diagnostics/live`. It sits under `/api` and
thereby inherits the token protection along with the subprotocol path
without a single line of extra code (section 9.1 of the main document).

**Separate from the values channel `/api/live`, not attached to it.** The
two have different lifetimes and different volumes: the values channel
runs as long as the UI is open, the diagnostics channel only as long as
someone is actually looking. If everything were one channel, every
forgotten browser tab on the "Devices" view would get every log line for
weeks. Also, `/api/live` would then carry three kinds instead of one.

**The shared machinery moves into its own module.** Bounded queue with
drop-oldest, disconnect detection via `receive_text`, clean teardown of
both sub-tasks — that currently lives in `api/live.py` and is needed by
both routes. Keeping it twice would mean making every future fix twice;
the first one was already necessary (the unbounded queue, review fix
phase 5).

**Message format.** Every message carries its kind and a timestamp (field
name corrected in follow-up task 7, fix 3e — the implementation calls it
`timestamp`, not `at`; the field `forced` was missing here entirely):

```json
{"kind": "datagram", "timestamp": "…", "key": "d1_2_power", "value": "0", "forced": false}
{"kind": "command",  "timestamp": "…", "path": "/cmd/d1_1_on/1", "status": 200}
{"kind": "log",      "timestamp": "…", "level": "WARNING", "logger": "…", "message": "…"}
```

The actual field names are taken by the implementation from the existing
ring entries (`DatagramLogEntry`, `CommandLogEntry`), so the same piece of
data isn't named differently twice.

**On connect, a snapshot first**, then live. This means the view is filled
immediately. **It does NOT prevent a gap between "fetch once" and "listen
from now on" — unlike what an earlier version of this design claimed here**
(corrected in follow-up task 7, fix 3a): this exact order drops an entry
that arises exactly in between (the snapshot has already been taken, the
observer not yet registered); the reverse order would instead duplicate
it. Losing rather than duplicating is the deliberate choice. For
datagrams and commands, the window has no consequence (no `await` in
between, both possible writers run on this route's event-loop thread);
for log lines from a foreign thread (see section 2.3), the gap is real,
however — see the `api/diagnostics_live.py` module docstring for the
full account. The existing GET routes stay unchanged for scripts and
`curl`.

## 4. UI

The "System" view keeps its sections; they fill continuously instead of
once. Four controls for that:

| | Default | why |
|---|---|---|
| **Pause** | running | without it, nothing that scrolls can be read |
| **Hide heartbeat and resend** | on | otherwise every 300s around 140 lines flush everything away |
| **Log level filter** | from INFO up | INFO is the level at which this project logs the events you look for during a fault |
| **Line cap in the browser** | fixed | a tab left open for days should not fill up memory |

The "hide" filter only affects the **display**, not the ring: whoever
turns it off sees the existing lines immediately, without waiting for new
ones.

**How "heartbeat and resend" are recognized was only decided during
implementation — this design left it open.** The first attempt measured
the arrival rate in the browser (a `DATAGRAM_BURST_GAP_MS` window) and
hid anything that followed in too quick succession — and thereby also
caught a genuine key press: `Runtime.on_event` sends a pulse and a counter
within microseconds of each other, exactly the pattern the heuristic took
for noise. The version actually implemented instead queries a fact rather
than a guess: `UdpSender.send` passes its `force` argument through
unchanged as a field `forced`, all the way into `DatagramLogEntry` and
from there into the live message (`api/diagnostics_live.py`). Exactly two
callers in the whole project set `force=True`, `Runtime.resend_all()`
(full resend) and the heartbeat — `False`, on the other hand, always means
a genuine value change, however fast it arrives. `hideNoise` in `app.js`
has since filtered on `!entry.forced`, no longer on the arrival rate.

The channel opens on switching to "System" and closes again on leaving.
**Exactly one connection** — the lesson from phase 5, where an additional
`x-init="init()"` per tab permanently produced two open channels, because
Alpine 3 already calls `init()` on its own.

## 5. Error handling

| Case | Behavior |
|---|---|
| An observer raises | swallowed; the log and send paths keep running |
| Error in the log observer | swallowed and **not logged** — that would be the infinite loop |
| Browser stops reading | queue capped, oldest entry drops out |
| Connection drops | reconnect with growing backoff, only while the view is open |
| Log line from a foreign thread | lands in the ring, wakes the loop, never blocks the caller |
| No running event loop | the handler still writes into the ring; only the notification is skipped |

## 6. Verification

All tests run without hardware and without network access.

- **Against the real ASGI application**, not against a stand-in — following
  the pattern of the WebSocket tests from phase 5. The reason is stated
  there: an in-process test was green while `/api/live` returned 404 in
  **every** real installation, because uvicorn had no WebSocket
  implementation installed at all. A smoke test with a raw RFC-6455
  handshake belongs here.
- A log line from a **foreign thread** arrives.
- A **raising observer** halts neither the logging nor the UDP sender.
- The handler produces **no** new log line, not even in the error case
  (checked for recursion-freedom).
- The capture contains what the sender actually sent — **including**
  falling pulse edges and full resends, which the runtime observers omit.
  This is the test that guards section 2.1.
- Without a token, the route answers with 401, like every `/api` route.
- The snapshot on connect contains existing entries, new ones arrive
  afterward.

## 7. Open points

1. **Decided: the same.** `DIAGNOSTICS_LINE_LIMIT` in `app.js` and all
   three ring sizes in the service (`DATAGRAM_LOG_SIZE` in
   `loxone/sender.py`, `COMMAND_LOG_SIZE` in `loxone/server.py`,
   `LOG_BUFFER_SIZE` in `diagnostics/logbuffer.py`) are set to 500 — as
   proposed, with no reason given for a deviation.
2. **Partly defused, not closed.** The level filter still acts
   client-side — nothing has changed about that. What has changed:
   `install_log_buffer()` sets, at start, not only the handler's level but
   also that of the `loxmatter` logger itself (see the section 2.3
   addendum above) — without this line, the "from INFO up" default would
   have captured nothing at all, because nobody else in this project sets
   the logger level otherwise. As long as nobody calls
   `install_log_buffer(level=logging.DEBUG)` — for which `loxmatter run`
   offers no switch today — DEBUG cannot reach the ring at all; the
   original point, however, remains valid unchanged once that becomes
   possible: the ring then stores unfiltered, the UI's level filter still
   only affects the display.
3. Downloading the capture as a file is still not part of this design.
