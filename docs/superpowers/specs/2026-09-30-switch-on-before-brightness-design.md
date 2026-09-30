# Switch a Lamp On Before Its Brightness

Design, 30 September 2026. The bridge sends On (6, 1) before a
MoveToLevelWithOnOff (8, 4) above level 0 whenever the lamp may be off,
because at least one lamp comes on dim without it.

## 1. What Happened

The maintainer reported that the "IKEA Tunable White" lamp - an IKEA
KAJPLATS E27 WS G60, firmware 1.1.0, device 21 on the test Pi - came on dim
when the Loxone app went from "off" to "a lot of light", and bright when
the brightness was changed while it was on.

Measured the same day, with the maintainer watching the lamp. Each run
switched the lamp off with (8, 4) level 0, waited five seconds, and then
switched it on:

| Run | Sent | Lamp reported within 0.2 s | Lamp looked |
|---|---|---|---|
| 1 | (768, 10) 3498 K, then (8, 4) level 224 | on, 88 % | dim |
| 2 | (8, 4) level 224 | on, 88 % | dim |
| 3 | (6, 1), then (8, 4) level 224 | on, 88 % | bright |

The lamp's reports cannot tell the three apart: it reports the level it
was told, not the light it gives. Its Level Control attributes are
unremarkable - OnOffTransitionTime 0, OnLevel null, MinLevel 1, Options 0.
Earlier the same afternoon one switch-on reported "on, 0.4 %" for five
seconds before it reported 88 %.

The bridge sent what the Matter specification asks for; the lamp does not
act on it. This design works around the lamp, in the bridge, for every
dimmable light: an On before the brightness is harmless for a lamp that
does not need it.

## 2. The Rule

1. An (8, 4) above level 0 gets an On (6, 1) on the same endpoint first,
   unless the lamp is known to be on.
2. **Known to be on** means both: the last call this bridge sent the
   endpoint left it on - an On, or an (8, 4) above 0 - and the lamp has not
   reported its OnOff attribute as off since. The first catches an "off"
   the bridge sent whose report has not arrived yet (reports lagged by
   seconds on 30 September); the second catches an "off" from a wall switch
   or another app. After a toggle, a failed call, or a start of the bridge,
   nothing is known.
3. An endpoint that does not accept On (it is missing from its stored
   commands) gets the level call alone, as before.
4. Nothing else changes: (8, 0) does not switch a lamp on and gets no On; a
   level of 0 is an "off" and gets none either.

A slider dragged along a lamp that is on therefore costs no extra call; the
first brightness after an "off" costs one.

This was a choice between two options, made by the maintainer on
30 September 2026. The other was an On before every brightness: simpler, and
right even when an "off" from elsewhere is never reported, but one more
Thread call for every value of a dragged slider.

## 3. Where It Lives

- **`commands/switch_on.py`**: `SwitchOnFirst` wraps the invoker. It sits
  behind the command gate, which runs one request per device at a time, so
  the calls it sees for an endpoint are in the order the device receives
  them. The On is part of its request: if the On fails, the request fails.
- **`loxone/server.py`**: `build_app` wraps the invoker it hands the gate,
  reading the lamp's OnOff from the runtime (`d{id}_{endpoint}_onoff`) and
  whether the endpoint takes On from the store. Both the Loxone and the web
  UI routes go through it.

## 4. Testing

`SwitchOnFirst`: an unknown lamp gets the On; an "off" the bridge sent
counts even while the lamp still reports on, whether it was (6, 0) or
(8, 4) at 0; a lamp the bridge switched on is dimmed without an extra call;
a lamp reported off from elsewhere gets the On; a toggle or a failed call
makes the state unknown; a level of 0, (8, 0) and other calls pass
unchanged; an endpoint without On gets the level call alone; endpoints are
kept apart; a failing On stops the level call.

Routes: `build_app` reads the runtime's OnOff value under the right key and
asks the store whether the endpoint takes On. The translation tests that
list the calls a lamp receives now show the On before the first
brightness.

On hardware: switch the tunable-white lamp off and on to a high brightness
from the Loxone app several times; it should come on bright each time.
