# One Command per Device at a Time, and Only the Newest Value Waits

Design, 13 September 2026. The bridge serialises the commands it sends to
each device, and lets a newer value replace an older one that has not been
sent yet. A Loxone slider dragged across a group then produces at most one
command per lamp in flight, instead of a queue on the Thread radio.

## 1. What Happened

On 13 September 2026, between 19:27:10 and 19:27:40 UTC, the maintainer's
Loxone Miniserver sent `g1_color` about once a second, and eleven times
within one ten-second window. That is a dimmer slider being dragged. The
group has three Thread lamps and one Zigbee lamp. Every value became a
colour call and a brightness call per member, all members at once, so the
Thread lamps received six to seven unicasts per second, each with its reply.

The Miniserver does not wait for an HTTP answer before sending the next
value, so calls for the same lamp overlapped. At 19:27:51 the first member
stopped answering. At 19:28:15 matter-server could no longer send over
`wpan0`, because the OpenThread agent had already exited. The cron watchdog
restarted it at 19:30:01, and the network was back six seconds later.

The agent's own log for that exit was lost: rsyslog inside the `otbr`
container had not been running since 11 September. The three earlier exits
on 3 and 8 September ended in the same two lines:

```
[W] P-RadioSpinel-: radio tx timeout
[C] Platform------: HandleRcpTimeout() at radio_spinel.cpp:2054: RadioSpinelNoResponse
```

The host kernel log shows no USB disconnect and no under-voltage.

**What this design addresses, and what it does not.** The agent aborting
on an RCP timeout is a problem of the radio link: baud rate, flow control,
or the RCP firmware. It is researched separately. This design removes the
trigger this bridge adds on top: bursts of overlapping commands to the same
devices. It is worth doing on its own merits even if the link turns out to
be fine, because a queue of stale slider positions also makes a lamp lag
behind the slider.

## 2. The Rule

1. **One request per device at a time.** A *request* is the list of calls
   one command key produces for one device: `to_device_calls` for a device
   key, one member's plan for a group key. A device is identified by
   `(technology, address)`. Requests for different devices still run
   concurrently.
2. **A waiting request can be superseded.** When a new request for a device
   arrives while the device is busy, it replaces every request still waiting
   for that device whose *slots* are all covered by the new request's slots.
3. **Slots.** Each call has at most one slot:
   - `(endpoint, "level")` for LevelControl (8, 0) and (8, 4);
   - `(endpoint, "colour")` for ColorControl (768, 6), (768, 7) and (768, 10).
     Colour and white temperature share a slot, because they are the same
     output of the lamp;
   - no slot for anything else: on, off, toggle and every other command.

   A request with any call that has no slot can **never be superseded**, and
   supersedes nothing. A Loxone colour value (colour + brightness) covers
   both slots and therefore replaces a waiting colour-only, brightness-only
   or colour + brightness request on the same endpoint.

   **Slot strength.** (8, 4) MoveToLevelWithOnOff also switches the lamp:
   on above the minimum level, off at it. (8, 0) MoveToLevel only sets the
   level. A new request covers a waiting one only if, for each of the
   waiting request's slots, it has a call in that slot at least as strong.
   So (8, 4) supersedes a waiting (8, 0), but a waiting (8, 4) is not
   superseded by an (8, 0): a waiting "level 0 with on/off" is an "off",
   and a plain level in its place would leave the lamp on (review finding
   M-b).

   **Not past a switch.** Supersession only looks at the waiting requests
   after the last waiting request that has an unslotted call. A new value
   cannot remove one that waits in front of a toggle, on or off: what the
   toggle does depends on the state the value before it left (review
   finding M-c).
4. **What is running finishes.** A request that has started is never
   interrupted, and its calls keep their order: colour first, then brightness.
5. **Waiting requests run in arrival order.** Supersession removes requests
   from the queue; it never reorders the ones that stay.
6. **A superseded request is not a failure.** Its caller receives "superseded"
   and answers as if it had succeeded:
   - the device route answers 200;
   - in a group, the member counts as reached, because a newer value for it
     is on its way.
7. **Errors stay with their request.** A request whose call raises reports
   that error to its own caller only. The next waiting request still runs.
   That includes a `CancelledError` that comes out of a call while the
   worker itself is not being cancelled: matter-server's client cancels
   every pending call when its websocket closes. That request fails with
   `DeviceUnreachableError` (502), and the ones behind it still run. Only a
   worker whose own task is cancelling cancels the waiting requests.

## 3. Where It Lives

- **`src/loxmatter/commands/coalesce.py`** (new): `CommandGate`, built with
  the invoker. `await gate.run(calls) -> bool` returns `True` when the calls
  ran and `False` when a newer request superseded them. It raises what a call
  raised. Per device it keeps a queue and at most one worker task, and the
  entry for a device disappears once its queue is empty. It knows nothing
  about HTTP, the store or groups.
- **One gate per application.** `build_app` creates it once and hands it to
  both `/cmd/{key}/{value}` and `POST /api/commands/{key}`, so a Loxone value
  and a web UI click for the same lamp share one queue.
  `build_control_router` keeps working without a gate argument, by building
  its own, so existing callers and tests are unchanged.
- **Both device routes** replace their `for call in calls: await invoke(call)`
  with `await gate.run(calls)`.
- **`dispatch_group(plans, invoke, *, run=None)`**: when `run` is given, a
  member's plan goes through it instead of the sequential `invoke` loop.
  Both group routes pass `gate.run`. A member whose plan was superseded is
  neither failed nor unconfigured.
- **The bound stays per call** (`bounded_source_call`, 10 s; removal 120 s),
  **and waiting has a bound of its own.** A request that is still waiting
  for its turn after `SOURCE_CALL_TIMEOUT_SECONDS` leaves the queue unsent
  and raises `DeviceUnreachableError` ("the device did not answer within
  10 s"), the 502 a silent device gets anywhere else. Without it, requests
  behind a dead device waited without limit, on, off and toggle piled up
  because nothing supersedes them, and "a Loxone command gives up after 10
  seconds" was no longer true (review finding I2). A request that has
  already started is waited for to the end, because each of its calls is
  bounded. Whether it has started is read from the queue, not from the
  timer, so the timeout and the worker cannot both act on one request.

## 4. Testing

The gate is tested with a fake invoker whose calls block until the test
releases them:

- two requests for one device never run concurrently, while requests for
  two devices do;
- three brightness values sent while the device is busy: the first runs,
  the second is superseded, the third runs, and the device receives exactly
  the first and the third;
- a colour + brightness request supersedes a waiting brightness-only request
  on the same endpoint, but not one on another endpoint;
- `toggle`, `toggle` while busy: both run, in order;
- a waiting request mixing a slot call with an on/off call is never
  superseded;
- a raising call reaches its own caller, and the next request still runs;
- a call whose own future is cancelled fails only its request, with a 502,
  and the request behind it still runs;
- a request waiting past the wait bound is refused and never sent, while a
  request that started before the bound passed is not failed by it;
- an (8, 0) does not supersede a waiting (8, 4), and a new value does not
  supersede one waiting in front of a toggle;
- a worker cancelled before its first step does not strand its device, and
  cancelling a worker cancels the requests waiting for it;
- the per-device entry is gone once the queue drains;
- a caller cancelled while waiting does not stop the worker from running
  the requests behind it.

Routes: a burst of `/cmd` values for one group while the members are slow
reaches each member with no two calls overlapping per device, ends with the
last value, and every request answers 200. `dispatch_group` with `run`
counts a superseded member as reached. Every protective test is
fault-injected, as on the branches before it.

On hardware: drag the Loxone slider for "Lampengruppe 1" for 30 seconds.
Watch the bridge log for no group failures, the lamps for following the
slider without lag, and `otbr` for no abort.

## 5. Explicitly Not Built

- **A global rate limit across devices.** Serialising per device already caps
  the Thread load at one request per lamp. A global cap would delay unrelated
  devices, and there is no measurement that it is needed.
- **Interrupting a running request** for a newer value. A Matter command
  that has been sent cannot be recalled.
- **Coalescing reads**, and the removal route. Neither is a burst source.
