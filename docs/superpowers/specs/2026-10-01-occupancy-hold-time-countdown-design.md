# Show How Long a Motion Sensor Stays Occupied

Design, 1 October 2026. The classic IKEA TRADFRI motion sensor tells the
bridge how long to stay "occupied" after each detection. The bridge keeps
that duration as the sensor's hold time, and the device tile counts down
the time that is left next to `occupancy`.

## 1. What Happened

On 1 October 2026 the maintainer paired three TRADFRI motion sensors
(E1745) on the test Pi. Three findings led here:

- The occupancy signal stayed at 1 after the first motion, because the
  sensor never sends `off`. That was fixed the same day (`fe73082`): the
  bridge counts down the `on_time` of `onWithTimedOff` itself.
- A signal that appeared while the dashboard was open showed only after a
  page reload. Also fixed the same day (`69c8ba3`, `d<id>_signals`).
- The duration itself was visible nowhere. It is set on the back of the
  sensor, and without seeing it, a user cannot tell why occupancy went back
  to 0 when it did.

The maintainer chose variant B of three mockups: a small chip next to the
occupancy value, and asked that the remaining time also be right after a
page reload.

## 2. The Problem a Countdown Has

**Renewed motion produced no message.** While occupied, a further
detection arrives as another `onWithTimedOff`. The bridge restarts its
timer, but the value stays 1, and `ZigbeeSource._deliver` passes on only
values that changed. A countdown in the browser would run to 0:00 while
occupancy was still 1.

## 3. Design

### 3.1 Hold time as a signal

The listener in `configure.py` stores `on_time` in the out-cluster's own
`OnTime` attribute (OnOff 0x4001, tenths of a second) before it sets the
occupancy. zigpy declares that attribute on `OnOff` and persists it with
the cache, so the hold time survives a bridge restart.

`translate.py` maps it to Matter's `OccupancySensing.HoldTime` (`1/1030/3`,
seconds), the attribute Matter occupancy sensors carry for exactly this.
`clusters.yaml` names it `hold_time`, unit `s`, `functional: false`: a
static setting, available for export by hand, not ticked by default.

### 3.2 Renewed motion is delivered

`ZigbeeSource` notes, per device, an `attribute_updated` event for the
TRADFRI sensor's OnOff attribute with value `True`: that is a detection,
renewed or not. `_deliver` then passes on the occupancy path even when its
value has not changed.

Loxone sees nothing new: the UDP sender drops a value equal to the last one
it sent (`UdpSender.send`). The WebUI observers are told regardless,
which is what the browser needs.

### 3.3 When each signal was last reported

`Runtime` records, per signal key, when `on_attribute` last delivered it
(ISO time, like `_last_heard`, not persisted). `GET /api/devices/<id>/
signals` returns it as `reported_at`. A value only seeded at startup
(`seed_from_snapshot`) has no `reported_at`: the bridge did not watch it
arrive.

**Amended the same evening, after the first test on the Pi.** A detection
that brings a new path - the first one carrying a hold time - reaches the
runtime as a whole snapshot, and `on_node_snapshot` only cached its values.
The countdown then started at the second detection, and worse, Loxone never
heard of the first one. `on_node_snapshot` now treats a known signal whose
cached value changed like `on_attribute` (sent, reported, `reported_at`),
and gives a signal born in the snapshot a `reported_at` without sending it.

### 3.4 The chip

Next to the `occupancy` value on the device tile, when the device has a
hold-time signal on the same endpoint:

- occupancy 1, time left: `noch 2:41` / `2:41 left`
- otherwise: `Nachlauf 3 min` / `3 min hold` (seconds below one minute)

Time left = latest of (`reported_at`, the tab's own `liveSeenAt`) + hold
time − now, ticking with `nowTick`. A countdown that has run out while
occupancy is still 1 shows the hold time instead of `0:00`: a Matter sensor
extends its hold internally without reporting, and the chip must not
pretend to know more than it was told.

## 4. Not in Scope

- Changing the hold time from the bridge. The TRADFRI sensor takes it from
  the switch on its back; per-device settings are a separate design.
- A countdown in Loxone. `hold_time` can be exported; the countdown itself
  is the UI's.
