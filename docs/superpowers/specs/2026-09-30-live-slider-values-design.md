# Answer the Miniserver at Once, and Space the Values per Lamp

Design, 30 September 2026. `/cmd` answers before the device does, so the
values of a dragged colour wheel reach the bridge instead of queuing in the
Miniserver. The command gate then sends each lamp only its newest value, and
no more often than once per interval.

It builds on the command coalescing design of 13 September 2026
(`2026-09-13-command-coalescing-design.md`), which it corrects in one point:
that design assumed the Miniserver does not wait for an HTTP answer.

## 1. What Happened

On 30 September 2026 between 15:38:45 and about 15:40 UTC, the maintainer
dragged the colour wheel of a light in the Loxone app. The Miniserver sent
`d11_1_lumitech` and `d10_1_lumitech` alternately, two Thread lamps, about
200 values in two minutes. The access log of the test Pi shows:

- **The Miniserver waits for each answer.** Each request is logged when it
  is answered, and the next one follows 150-250 ms later, without a single
  overlap. Where the bridge took 5.2 s to answer (15:38:54.7 to 15:38:59.9),
  the Miniserver sent nothing in between.
- **Each answer took one lamp round trip.** `/cmd` awaited the gate, and a
  colour value is two Thread calls, colour and then brightness.
- **The values were stale.** The requests ran back to back for the whole
  burst, with no pause: the Miniserver was working through a queue of wheel
  positions, one per round trip, two lamps one after the other. The lamps
  followed the wheel later and later. That is the lag the maintainer
  reported.
- **The gate never replaced a value.** It only replaces a value that waits
  behind another for the same lamp. With one request in flight at a time
  and the next sent only after the answer, nothing ever waited.

In the same burst, at 15:38:54, the Thread radio reported `Framing error 6`
for a frame from the host, and five seconds later otbr logged
`radio tx timeout` and recovered the radio (`Trying to recover (1/2)`,
`RCP recovery is done`). The same happened once that day at 13:12 without
any load. That is the serial link to the radio, which this design does not
fix (section 5); it only takes away the load the bridge adds.

## 2. The Rule

1. **`/cmd` answers before the device does.** It still answers 404 for an
   unknown key and 400 for an unsuitable value, because both are decided
   before anything is sent. Every other value is handed to the gate and
   answered 200 at once.
2. **What the device did is logged.** A device that did not answer is logged
   as an error with its traceback, as the route logged it before its 502. A
   technology with no running source is a warning of one line, as it was a
   503. For a group, one line names the failed members and counts the ones
   reached, once all members have finished, with the same counting rules
   as before.
3. **A request is queued before the answer, in the order values arrived.**
   `CommandGate.submit` puts it into its device's queue without awaiting
   anything, so two values for one lamp cannot swap places on the way.
4. **Values for one lamp are spaced.** A request with slots (brightness,
   colour, white) does not start sooner than `VALUE_INTERVAL_SECONDS`
   (0.4 s) after the start of the one before it for the same device. While
   it waits, it stays in the queue, so a newer value still replaces it. The
   newest value is never dropped by this, only delayed.
5. **On, off and toggle are never held back, and are never replaced.** They
   do not start the interval either. One sent right after a slider value
   still waits behind that value, because the order stays as sent (rule 5
   of the coalescing design).
6. **The web UI route is unchanged.** `POST /api/commands/{key}` still
   waits for the device and answers 502 or 503: the person who clicked is
   looking at the answer. It shares the gate, and so the interval.

What "nothing gets lost" means here: every on, off and toggle reaches the
lamp, in order, and the last value of every slider reaches it. The positions
in between were replaced before they were sent, because they would have been
overwritten a moment later anyway.

## 3. Choosing the Interval

One colour value took 160-250 ms on the test Pi on 30 September 2026. At
0.4 s from start to start, a lamp gets at most two and a half values a
second - five Thread calls - and follows the wheel at most one interval plus
one round trip behind, about 0.6 s. Before, the Miniserver sent the two
lamps about eleven calls a second together, and the lag grew for as long as
the wheel moved. Answered at once, the two lamps are now served side by
side, so without the interval they would together receive up to twice that.

The interval is counted per device, from the start of the last request with
slots, and remembered after the device's queue has drained: a wheel whose
values arrive a little slower than a lamp answers empties the queue after
every value.

`VALUE_INTERVAL_SECONDS` is a constant in `commands/coalesce.py`. It should
be adjusted by measurement on the Pi, not by feel.

## 4. Where It Lives

- **`commands/coalesce.py`**: `CommandGate.submit(calls)` returns a future
  with what `run` would have returned or raised; its outcome tasks are held
  by the gate. `CommandGate(..., min_interval=...)` defaults to 0, so a gate
  built without it behaves as before. `build_app` passes
  `VALUE_INTERVAL_SECONDS`.
- **`commands/fanout.py`**: `group_outcome(plans, results)` is the report
  `dispatch_group` builds, for a caller that ran the members itself.
- **`loxone/server.py`**: both halves of `/cmd` submit and answer;
  `_log_device_outcome` and `_log_group_outcome` log what happened.

## 5. Explicitly Not Built

- **A fix for the radio link.** The `Framing error` is a frame damaged
  between the Pi and the MG24 stick, at 460800 baud without flow control.
  It is researched in the notes behind the otbr image; firmware with flow
  control or a lower baud rate would be needed.
- **A radio budget across lamps.** The interval is per lamp; a group of six
  lamps dragged together still loads the radio six times as much. There is
  no measurement yet that it is needed.
- **Skipping calls that change nothing** - a repeated value, or a brightness
  call when only the colour changed. Considered and set aside for now
  (30 September 2026); the brightness call also switches the lamp on.
- **A status for Loxone.** The Miniserver never evaluated one.

## 6. Testing

Gate: `submit` returns before the device answers and hands on the outcome;
submitted requests queue in submission order; a submitted value waiting on
its device is replaced by a newer one; a failure nobody reads is not logged
as unretrieved; a value within the interval waits and can be replaced; the
newest value goes out once the interval is over; on, off and toggle are not
held back; the interval counts per device and survives a drained queue.

Routes: `/cmd` answers while the device is still busy; a device failure is
logged with its traceback, a missing source as one warning line; a group's
log line names the failed members, counts only members given something to
do, counts members and not calls, names the first member's technology, and
tells a partial success from a group nobody was asked for; a dragged slider
through `/cmd` is answered at once and each lamp still ends on the newest
value; `build_app` spaces a second value that follows too soon.

On hardware: drag the colour wheel of the two KAJPLATS lamps for 30 seconds
and let go. The lamps should follow within about half a second and stop on
the last colour within a second of letting go. In the access log the
`/cmd` requests should come in quick succession rather than one per round
trip, and the otbr log should show fewer frames per second than on
30 September.
