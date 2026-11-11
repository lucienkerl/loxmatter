# Show a Value's History on Hover

Design, 11 November 2026. Resting the pointer on a value in the web UI opens
a small window with every change that value went through while the tab was
open, each with its time to the second. The goal is a quick answer to "how
often did the motion sensor in the hallway trigger?" without writing a
statistic in Loxone first.

## 1. The Request

The maintainer asked for a small dialog on hover that lists the switching
history of a value with timestamps including seconds, kept in the browser
only and only for as long as the tab is open. Two mockups were shown:

- **A**, a compact list: one row per change, newest first, with the time,
  the new value and how long it held, and a chip counting the switch-ons.
- **B**, the same list under a strip covering the last 60 minutes: the
  spans an on/off signal was on, or a small step line for a number.

The maintainer chose **B**.

## 2. What Is Recorded

`recordValueHistory` runs for every message of the live WebSocket
(`/api/live`) and appends `{ at, value }` to `valueHistory[key]`, with three
rules:

- **Only real changes.** The bridge sends a value again without it having
  changed: a full resend, a reconnect, and the TRADFRI motion sensor's
  renewed detection (design 2026-10-01, section 3.2). The browser cannot
  tell these apart, and counting the first two would make a quiet sensor
  look busy, so a value equal to the last recorded one is dropped. The
  price: renewed motion while a sensor is already occupied does not count
  as a new trigger. The count answers "how often did it go on", not "how
  many detections did it see".
- **No heartbeat.** `bridge_alive` belongs to no signal.
- **A cap of 200 entries per signal** (`VALUE_HISTORY_LIMIT`), oldest out
  first. A power meter reporting every few seconds would otherwise grow
  the tab without end, the same reason `DIAGNOSTICS_LINE_LIMIT` exists.

The live connection sends no snapshot when it opens, only changes. So
`loadSignals` seeds each signal's history with the value the signal list
reported at load time, marked `initial` and shown as "Start". Without it the
first change would have nothing to be a change from, and a resend of the
current value would count. A signal that already has a history keeps it:
`loadSignals` runs again for a device that gained a signal at runtime.

Nothing reaches the server and nothing goes to browser storage. A reload
starts over.

## 3. The Window

One window for the whole page, a manual popover (`popover="manual"`), so it
sits in the top layer above the sticky header and the tile menus. It opens
from every value cell: a tile's rows, its battery row, and the signal list.

- **Hover** opens it after 250 ms, so a pointer crossing a tile opens
  nothing. Leaving closes it after 200 ms, long enough to move the pointer
  into the window and scroll its list.
- **Click or tap** opens it pinned: it stays until a click elsewhere,
  Escape, or a second click on the same value. Touch screens have no hover,
  so this is how a phone opens it.
- **Scrolling** anything but the window's own list closes it, since the
  value it points at has moved.

The head shows the signal's title, a chip with the count, and the line the
value cell used to carry as its `title` ("Last updated 4 s ago"). The native
tooltip is gone from the value cells: it would open a second box over the
window.

The count is the number of switch-ons for an on/off signal and the number
of changes for anything else. The starting value is not a change.

### 3.1 The strip

It covers the last 60 minutes, ending now, with four clock ticks and "now".

- On/off signals: grey for off, green for each span the signal was on.
- Numbers: a step line scaled to the values the hour shows, with min and max
  beneath.
- Text, lists and structs get no strip, having no axis to draw on.

The stretch before the tab started listening is hatched (on/off) or shaded
(numbers): the tab does not know what happened there, and drawing it as
"off" would claim it does.

### 3.2 Inside the signal list

The signal list is a modal `<dialog>`. A modal dialog makes everything
outside itself inert, popovers included, so the pointer could not reach the
window's list to scroll it. `openHistory` therefore moves the window into
the open `<dialog>` before showing it, and back to `<body>` for a tile.

## 4. Tests

`tests/api/test_web.py` runs the real `app.js` in node for the recording
rules (real changes only, heartbeat ignored, lists compared by content),
the cap and the reseed, the count and the row durations, and the strip's
spans, clipping and unknown stretch. The window itself was checked in
Chromium against a local server with a toggling plug: hover, pin, click
away, and the signal list's dialog.
