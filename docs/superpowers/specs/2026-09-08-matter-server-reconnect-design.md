# The bridge survives a restart of matter-server

Design, 8 September 2026. Gives loxmatter back what it does not have today:
it notices the loss of the connection to matter-server, rebuilds it, and
tells the truth about it for as long as it is missing.

Trigger: an operational outage the same evening, reproduced twice within an
hour. The whole course of events is in section 1.

## 1. What happened

Reported was: "Dual Button and window contact are no longer sending any
data." What was found were **two independent bugs**, the second of which
concealed the first.

### 1.1 The harmless part

The window contact was never broken. It reports `BooleanState` (cluster 69)
only on **change**, and no signal carries the resend flag. Since the last
restart of the bridge nobody had moved the window - so nothing came. A
device that is currently quiet looked in the UI exactly like one from
which nothing has come for days. That is exactly what section 5 fixes.

### 1.2 The dead subscription

For the BILRESA button (node 4), matter-server had kept a subscription
alive with a report interval of [0, 900] since 3 September at 19:32 - and
logged **not a single line** since then. No timeout, no renewal. Not even
when a restart of the border router tore the subscriptions of five other
devices: for node 4 there was nothing left that could have torn.

The device itself was reachable the whole time. Ping over IPv6: 6.8 s,
successful. Live read of `0/47/12`: 7.5 s, response `200` half-percent,
so full battery. It answered every question and raised none of its own.

Only a restart of matter-server healed it. **`interview_node` is not
enough** - the function re-reads the device data but does not rebuild the
subscription (proven: after the call the line "Setting up attributes and
events subscription" is missing from the log).

This bug is in matter-server, not in loxmatter. It is recorded here because
it shaped the debugging, and because it supplies the rationale for section
5. **It is not the subject of this design.**

### 1.3 The actual bug: the bridge does not survive the restart

When matter-server restarted to heal 1.2, loxmatter fell completely
silent - and reported it to nobody:

| Visible from outside | Actually |
| --- | --- |
| WebUI responds | — |
| `bridge_alive` keeps pulsing at its 30-second rate | no device value arrives any more |
| `GET /api/diagnostics/system` reports `matter-server -> True \| Connected.` | every `/cmd/...` fails with **502 Bad Gateway** |

There is **not a single line** about this in the loxmatter log. No
reconnect attempt, no warning. The only way out was a restart of the
container.

The same thing had already happened once, hours earlier, and had caused
the outage originally reported; the restart of the bridge with which the
operator tried to fix it fixed it temporarily.

## 2. Why it happened

Two places, both small, both clear-cut.

**`connected` asks the wrong question.** The property is
`self._upstream is not None` (`matter/client.py`). The field is set once in
`connect()` and cleared exclusively by `disconnect()`. If the websocket
dies, it stays put. The check answers "has anyone called `connect()`?"
and not "is the connection up?". Its own docstring already says so - it
names the condition as deliberately identical to `_require_upstream`. As
the basis of a health check it is therefore worthless.

**Nobody looks at the listener.** `_start_listener` returns the task from
`upstream.start_listening()`, `connect()` puts it into
`self._listener_task` - and after that nobody ever looks at it again. No
`add_done_callback`, no supervision. If it ends with an exception, it is
never collected. The signal that reports the breakdown already exists; it
is simply thrown away.

## 3. The client notices the breakdown

Two changes to `BridgeMatterClient`.

**`connected` will in future check both:**

```python
return (
    self._upstream is not None
    and self._listener_task is not None
    and not self._listener_task.done()
)
```

That makes `_check_matter_server` (`api/diagnostics.py`) honest without a
change of its own. The diagnostics item from 1.3 falls out here as a
side effect - a sign that the cause was correctly identified.

**New: `await client.wait_for_link_loss()`** - waits for the listener task
and returns as soon as it ends, whether with an exception or normally. The
exception is collected and logged in the process, not re-raised: the
caller wants to know *that* the connection is gone, and should not have to
distinguish between reasons for the breakdown itself.

No polling, no timer. The task is the signal.

## 4. The reconnection

New module `src/loxmatter/matter/supervisor.py` with two functions. It
deliberately does not live in the client: rebuilding the connection needs
`Store` and `Runtime`, and a client that knows half the application would
be the worse boundary.

**`attach(client, store, runtime) -> None`** - the sequence that today
stands word for word in `cli.serve()`: `subscribe` → `snapshots` →
`seed_from_snapshot` → `backfill_device_types` → `backfill_commands` →
`resend_all`. One place, two callers: startup and every rebuild.

That `resend_all()` runs along with it is the actual gain. After a
rebuild Loxone gets the **complete** state back, instead of being stuck on
whichever values happened to be the last ones when the link dropped. That
matches the existing guarantee "a restart of the bridge behaves like
`/resync`" (spec 6.4) - in future it applies to a reconnect as well.

**`supervise(client, store, runtime) -> None`** - the loop:

1. `await client.wait_for_link_loss()`
2. Log a warning (the line that was missing this evening)
3. `await client.connect()`, on failure back off and retry
4. `await attach(client, store, runtime)`
5. start over

Backoff: 1 s, doubling, capped at 60 s, reset after every success.
**Without a give-up limit.** A bridge that gives up after ten attempts is
worse than one that keeps knocking every sixty seconds - matter-server can
be gone for arbitrarily long, and nobody is standing by.

`cli.serve()` calls `attach()` once at startup and starts `supervise()` as
a background task. **`runtime.start()` stays outside** and continues to
run exactly once: it starts the heartbeat and resend loops, and those are
meant to outlast the outage, not begin anew with it.

## 5. The heartbeat falls silent

`Runtime.__init__` gets another keyword-only parameter:

```python
link_ok: Callable[[], bool] = lambda: True
```

`_heartbeat_loop` skips sending `HEARTBEAT_KEY` (`"bridge_alive"`) for as
long as `link_ok()` returns `False`. `cli.serve()` passes
**`lambda: client.connected`**, not `client.connected`: `connected` is a
property, so the second expression would be a bool evaluated once. The
heartbeat would then hang forever on the state of the moment of startup -
and because that is `True` at startup, it would never fall silent and this
whole section would be without effect, without any test noticing, because
the test itself passes `link_ok`. Test 3 in section 7 therefore also
checks the hand-off from `serve()`, not just the behaviour of `Runtime`.

> **Correction (final review, 9 September 2026).** The last sentence above
> describes a test that does not exist and will not exist - and that is
> deliberate, not an oversight. The implementation plan explains, in its
> section
> ["Deviation from the design, deliberate"](../plans/2026-09-08-matter-server-reconnect.md#deviation-from-the-design-deliberate),
> why the annotation `link_ok: Callable[[], bool]` is the **stronger**
> safeguard: a `bool` at this point is thereby a type error that
> `mypy --strict` rejects in CI - the mistake cannot be committed in the
> first place, while a test would only catch it after the commit. Tried
> out on purpose (`link_ok=client.connected` instead of `link_ok=lambda:
> client.connected`), mypy reports, verbatim: `src/loxmatter/cli.py:591:
> error: Argument "link_ok" to "Runtime" has incompatible type "bool";
> expected "Callable[[], bool]"  [arg-type]`. What this section wants in
> substance - that a value evaluated once cannot stop the pulse - is
> covered by Test 3 on the behavioural side; see the correction there. The
> wrong sentence stays as it is: this project does not rewrite its
> evidence retroactively.

This way **Loxone itself** notices that something is wrong: the watchdog
on the `bridge_alive` input trips without anyone opening a UI. That is
exactly what was missing this evening.

**The existing guarantee stays untouched.** The comment in
`_heartbeat_loop` records that a **send failure** must not silence the
loop, or the Loxone watchdog freezes on the last value. That still holds
and is not touched. "Sending fails" and "there is no Matter connection"
are two different states with two different right answers: keep pulsing
on the first, so the error becomes visible; fall silent on the second, so
it becomes visible. The difference is what the pulse makes a statement
about.

The default value `lambda: True` keeps every existing test running
unchanged.

## 6. "Last heard"

`Runtime` keeps `_last_heard: dict[int, str]` (device id to ISO-8601
timestamp), set in `on_attribute`, `on_event` and `on_node_snapshot` -
that is, everywhere something actually arrives from a device. Read via a
method `last_heard_for(device_id) -> str | None` next to the existing
`last_values_for`.

`DeviceOut` (`api/models.py`) gets a field `last_heard: str | None`,
filled in `api/devices.py` where `online` is already read today from
`runtime.last_values_for(device.id)`.

**In memory only, not in the database.** The same reasoning that already
stands in the docstring of `StoredDevice` for `online`: reachability is
runtime state. A timestamp that survives a restart claims something after
startup that nobody has checked - worse than none at all. `None` then
honestly says "nothing heard since this bridge started".

That is the difference 1.1 made visible: a device that is currently quiet,
versus one from which nothing has come for five days. Both show
`online: true` today, and `online` stays in future what it is - the display
gets the second number next to it, which is what makes the question
answerable in the first place.

## 7. Tests

Four tests, all without a network, all against the existing doubles in
`tests/matter/test_client.py` and `tests/loxone/` respectively:

1. **The breakdown is noticed** - a `FakeUpstream` whose `start_listening`
   raises after `init_ready` is set. Afterwards `connected` is `False`,
   and `wait_for_link_loss()` returns instead of hanging.
2. **Rebuild with backoff** - a client double whose `connect()` raises
   twice and holds on the third try. `supervise` then calls `attach`; the
   waiting times are measured through an injected `sleep` function, not
   actually sat out.
3. **Heartbeat falls silent** - `Runtime` with `link_ok=lambda: False`: the
   sender gets no call with `HEARTBEAT_KEY`. Counter-check with
   `link_ok=lambda: True`: it gets one. Plus a second test that checks the
   **hand-off**: a `link_ok` that returns `True` on the first call and
   `False` afterwards must actually stop the pulse - that fails if
   `serve()` passes a bool evaluated once from a property instead of a
   call (see section 5).
4. **"Last heard"** - after `on_attribute`, `last_heard_for` returns a
   timestamp, `None` before.

> **Correction to Test 3 (final review, 9 September 2026).** The "second
> test that checks the **hand-off**" was not written - deliberately, see
> the correction in section 5 and the section
> ["Deviation from the design, deliberate"](../plans/2026-09-08-matter-server-reconnect.md#deviation-from-the-design-deliberate)
> in the implementation plan. The hand-off from `cli.serve()` is instead
> secured by the annotation `Callable[[], bool]` under `mypy --strict`,
> and more sharply than a test could manage. What the design demands in
> substance is covered by
> `test_the_heartbeat_falls_silent_when_the_link_drops` in
> `tests/loxone/test_runtime.py` on the behavioural side: a `link_ok` that
> first returns `True` and then `False` must actually stop the pulse - a
> value evaluated once would fail there. The bite-test demanded for Test 3
> ("removing the `link_ok` check must turn the test red") still holds
> unchanged and was carried out. The original wording above stays as it is
> and is not deleted.

For Test 1 and Test 3, the check that the test really bites belongs
alongside: resetting `connected` by hand to the old condition, or removing
the `link_ok` check, must turn the test red. A test that only names a
structure instead of checking it does not notice at the next rework.

## 8. Not part of this design

- **The dead subscription from 1.2.** The bug is in matter-server. Whether
  `matterjs-server` does not have it is unchecked and would be a finding of
  its own.
- **The move to matterjs-server.** It forces exactly the restart that this
  design makes survivable, and therefore belongs after it - see
  `2026-09-08-matterjs-server-migration-design.md`.
- **A display in the WebUI for the outage.** The diagnostics item becomes
  honest with section 3; whether the device list additionally needs a
  banner is a design question and not a precondition for noticing the
  outage at all.
- **Resend flags for the event signals from 1.1.** Whether a window
  contact should periodically repeat its state is an operational decision
  about the existing setting, not a code change.
