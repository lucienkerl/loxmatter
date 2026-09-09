# Last heard, on the device card

Design, 9 September 2026. Puts the `last_heard` timestamp — already carried
by `GET /api/devices` — on the device tile, so the question it answers can be
asked without `curl`.

## 1. Why

On 8 September a Dual Button and a window contact were reported as "no longer
sending data". Hours went into the window contact. It was never broken: it
reports `BooleanState` only **on change**, no signal carries the resend flag,
and nobody had moved the window since the bridge last restarted.

The button really was broken — a subscription in matter-server that had been
dead for five days.

In the interface the two looked **identical**. Both said `online: true`,
because that field answers reachability, not "has anything ever arrived".
Nothing on the tile distinguished a device that is quiet from one that is
gone.

`last_heard` was added for exactly this (design
`2026-09-08-matter-server-reconnect-design.md`, section 6) and reaches the
API — but no part of the interface shows it. The value exists and nobody
sees it.

That matters more than it sounds, because of what the reconnect work
deliberately does **not** catch: a websocket that stays open while
matter-server sends nothing. The listener does not end, `connected` stays
true, the heartbeat keeps pulsing. That is precisely the dead subscription of
8 September, and `last_heard` is the only trace of it.

## 2. What the interface already has, and why it is not enough

The tile is not starting from nothing. `signalAgeTitle(signal)` puts a
per-signal age in the value cell's `title`, fed by `liveSeenAt[signal.key]`,
and it even has a string for the boundary case:
`web.header.unchanged_since_load` — "Unchanged since the page loaded".

Two gaps:

- **It counts from page load.** `liveSeenAt` is filled by the websocket in
  this browser tab. Reload the page and every age is gone. The string above
  admits this.
- **It is per signal and lives in a tooltip.** Answering "when did I last
  hear from this *device*" means hovering over cells one at a time.

`last_heard` is the complement: server-side, per device, and it survives a
page reload. It does not survive a bridge restart — deliberately, see
section 6 of the reconnect design: a timestamp that outlives the process
would assert something nobody checked.

## 3. Show it, do not judge it

**No threshold, no colour, no warning pill.**

A silent window contact is normal. So is a silent button, a silent leak
detector, a silent door sensor. Any staleness threshold would fire first and
most often on exactly the devices that prompted this — and a warning that
cries wolf on healthy hardware is worse than no warning, because the next
real one gets ignored too.

The value of `last_heard` is not an alarm. It is an answer, for the moment
you are already suspicious. So the tile states a fact and leaves the judgement
to the reader.

Three cases, all in the same quiet style as the existing `.hint` text:

| State | Display |
| --- | --- |
| heard within the last minute | `Last heard just now` |
| heard earlier | `Last heard 3m ago` |
| `last_heard` is `null` | `Not heard since the bridge started` |

The third is the valuable one. It is the sentence that would have shortened
8 September, and it is unambiguous: not "offline", not "no data" — *this
bridge has never heard from this device since it started*.

## 4. Where it goes

**Its own element between `.device-head` and `.value-rows`.**

Not in the footer: `.device-foot` carries the export statement
(`exportHintFor`), and the tile's own comment already draws that line — "The
header stays reserved for the device's state, not the export state."
`last_heard` is device state. Two timestamps about different subjects on
adjacent lines read as one muddled sentence.

Not inside `.device-ident` either, although the row under the device name
came free when the primary-signal display was dropped (design 2026-09-07).
`.device-ident` is a `display: flex` in row direction, and its `flex: 1 1
auto` is load-bearing: the offline pill sits right because `.device-ident`
grows, not because its own `margin-left: auto` fires (see the comment in
`index.html`). Putting a line underneath means `flex-direction: column`,
which changes how the name `<input>` sizes itself. That is a larger blast
radius than this feature earns.

A sibling after `.device-head` touches none of that reasoning.

## 5. It has to stay live

`last_heard` arrives once, with `GET /api/devices`. Left alone it would show
"12m ago" while values stream into the very same tile — worse than showing
nothing, because it would be confidently wrong.

The tile therefore keeps its own per-device mark of the last live arrival and
displays **the later of the two**:

- The websocket handler already stamps `liveSeenAt[message.key] = now` for
  every message. Signal keys are `d<device id>_<rest>`, so the device is
  derivable from the key.
- A new `deviceHeardAt[deviceId]`, written in that same handler, holds the
  newest arrival per device.
- The displayed timestamp is `max(deviceHeardAt[id], Date.parse(last_heard))`,
  with either side possibly absent.

The heartbeat key (`bridge_alive`) belongs to no device and must not count —
it arrives every 30 seconds regardless and would make every tile claim it had
just been heard from. The key pattern excludes it on its own, and a test pins
that.

The server value is thus only the starting point, for the window between page
load and the first live message from that device. Which is exactly the gap it
exists to fill.

## 6. No seconds in the label

The tile had a per-second age label in the text flow once and moved it into
the tooltip on purpose. The reason is recorded in `signalSeenText`: a value
like "7s ago" changes its width as it counts up and shoves the row back and
forth, drawing the eye to the motion instead of to the change that matters.

The same trap applies here, so this label never shows seconds. Under a
minute it reads `just now`; from there it uses the existing
`web.header.time_ago_minutes` / `time_ago_hours`. It changes at most once a
minute, and `just now` is a fixed string.

This needs one new helper next to `sinceText` rather than a change to it —
`sinceText` is still right for the tooltip, where a jittering width costs
nothing.

## 7. Strings

Three new keys, English and German (the `de:` values are shipped product and
stay German — see `CLAUDE.md`):

| Key | en | de |
| --- | --- | --- |
| `web.header.time_ago_just_now` | `just now` | `gerade eben` |
| `web.devices.last_heard` | `Last heard {text}` | `Zuletzt gehoert {text}` |
| `web.devices.never_heard` | `Not heard since the bridge started` | `Seit dem Bruecken-Start nichts gehoert` |

`web.header.time_ago_just_now` sits with the other `time_ago_*` keys because
that is where the coarse helper's other branches already live.

## 8. Testing

**The server side is already covered and is not part of this work.**
`test_the_device_list_carries_last_heard_from_the_runtime`
(`tests/api/test_devices.py:845`) asserts the timestamp in the
`GET /api/devices` response for a device the runtime has heard from and
`null` for one it has not, and `FakeRuntime` holds per-device timestamps the
test sets. Both landed with the final review of the reconnect branch. This
design adds nothing there.

What remains is the interface, in two layers:

1. **The helpers behave.** The coarse formatter and the "later of the two"
   resolution, exercised directly: no timestamp at all, server value only,
   live value only, both with either one newer, and the heartbeat key which
   must not count for any device.
2. **The bindings actually run.** A delivery test proves only that the markup
   was served. The Alpine expressions must be evaluated in a throwaway
   harness against a real DOM — this interface has produced bindings that
   were shipped and never executed.

## 9. Not part of this design

- **A staleness threshold or warning colour.** Section 3.
- **Sorting or filtering by last-heard.** The device list has a search field
  and room grouping; adding a third axis is a separate question.
- **Showing it in the signals modal.** That view is per signal, and per
  signal the existing tooltip already answers it for the current page visit.
- **Persisting `last_heard` across bridge restarts.** Deliberately excluded
  by the reconnect design, section 6, and nothing here changes that.
