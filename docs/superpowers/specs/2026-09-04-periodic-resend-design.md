# Periodic resend: opt-in instead of a blanket sweep

Design, September 4, 2026. Extends
[the main document](2026-09-01-matter-loxone-bridge-design.md) and picks up
the distinction from
[the signal selection](2026-09-03-signal-selection-design.md#3-two-concepts-that-stay-separate)
between exportability and relevance — a third, independent signal property
is added here.

## 1. The problem

`Runtime._resend_loop` ([runtime.py:473-481](../../../src/loxmatter/loxone/runtime.py))
calls `resend_all()` every `resend_seconds` (fixed at 300s), which resends
*every* known value with `force=True` — regardless of whether it has
changed. `UdpSender.send` ([sender.py:149-179](../../../src/loxmatter/loxone/sender.py))
guards every send, real or forced, with **one** shared `asyncio.Lock` and
**one** shared rate limiter (50/s).

With many commissioned devices, the full resend therefore turns into a
burst that takes several seconds (at 300 signals: 6s). A real control
command that arrives in this window waits on the same lock and can
therefore be delayed by up to the full burst duration. The problem grows
linearly with the device count. A longer interval would only make the
burst rarer, not smaller.

Most signals presumably don't need the periodic resend at all. It
presumably exists as protection against unnoticed packet loss / a
Miniserver restart between two change-driven sends — that is typically
only relevant for a few, specifically chosen signals (e.g. ones that a
Loxone signaling rule reacts to with a timeout), not for all of them.

## 2. Two mechanisms that stay separate

| Mechanism | Purpose | affected by this design? |
|---|---|---|
| Heartbeat (`bridge_alive`, 30s, [runtime.py:448-471](../../../src/loxmatter/loxone/runtime.py)) | Sign of life for the bridge itself, a global key | no, unchanged |
| Full resend (`resend_all`, 300s) | Re-sync of individual *values* against packet loss | yes, switched to opt-in |

The heartbeat is not a `StoredSignal` and stays out of scope.

## 3. The solution

A new, third, independent flag per signal — `resend` — alongside
`exported` and `functional`. Only signals with `resend = true` are still
resent periodically (forced); all others exclusively on change, as
everything already is today. The resend interval itself becomes a setting
changeable at runtime through the WebUI instead of a fixed constant.

Default for every signal, existing as well as new: `resend = false`. After
this update, therefore, **nothing** is automatically resent periodically
any more until the user deliberately marks some — deliberately analogous
to the migration question in the signal selection (section 6 there), just
without the backward-compatibility problem here, because `resend` is a
completely new field with no history.

## 4. Data model

`signal` table ([store.py:109-123](../../../src/loxmatter/model/store.py)):
new column `resend INTEGER NOT NULL DEFAULT 0`, via
`_add_column_if_missing` like the existing migrations. `StoredSignal` gets
a field `resend: bool`. New method `Store.set_resend(key, value)`, a
sibling of `set_exported`.

The resend interval is not a signal property but a single global setting.
The generic `setting` table already exists for that
([store.py:135-138](../../../src/loxmatter/model/store.py)), through which
e.g. `LocaleStore` stores the language
([locale_store.py](../../../src/loxmatter/model/locale_store.py)). An
analogous thin wrapper (working title `RuntimeSettingsStore`) gets
`get_resend_interval() -> float` (default 300.0, so it matches today's
behavior identically as long as nobody changes anything) and
`set_resend_interval(seconds: float)`.

## 5. API

`PATCH /api/signals/{key}` ([devices.py:239-240](../../../src/loxmatter/api/devices.py))
gets an optional field `resend: bool | None`, the same pattern as
`exported`.

New endpoint for the interval, e.g. `GET/PATCH /api/settings/resend-interval`
(or folded into an already-existing/future, more generic settings
endpoint, should one arise — an implementation detail decision).
Validation: a number greater than a sensible minimum (e.g. ≥ 10s), to
prevent accidentally crippling the system with too short an interval.

## 6. Runtime behavior

**Correction to the original version of this section:**
`resend_all()` ([runtime.py:362-396](../../../src/loxmatter/loxone/runtime.py))
is not only called by the periodic timer but also on bridge start
(`cli.py`, right after `seed_from_snapshot`) and from the `/resync`
endpoint (`server.py`) — both cases that must explicitly restore *every*
known value (spec 6.4, state restoration after a Miniserver restart).
Filtering `resend_all()` itself down to `resend = true` would therefore
not only restrict the periodic timer but also `/resync` and the bridge
start — after a real Miniserver restart, most virtual inputs would then
stay at their default value, exactly the problem spec 6.4 is meant to
prevent.

**`resend_all()` therefore stays unchanged** (still a full restore of every
known value, used by `/resync` and bridge start). A new method
`resend_marked()` filters down to `resend = true` and is called
exclusively from `_resend_loop`; both internally share the existing send
logic, already race-guarded (reading the value per key from `_last_values`
only immediately before sending, see the comment on `resend_all()`), just
with a different key set.

`_resend_loop` no longer reads the configured interval length once at
start, but repeatedly from the store while running (a short poll cadence,
e.g. checking every 5s whether the configured time has elapsed since the
last `resend_marked()` run). A change made through the WebUI therefore
takes effect within a few seconds, without a process restart — no
event/wakeup mechanism needed, a simple poll is enough given the order of
magnitude involved (seconds, not milliseconds).

## 7. Explicitly out of scope

Synthetic keys not tracked in `StoredSignal` — the reachability status
`d<id>_online` and pulse counters (`_n` suffix) — get no `resend` flag.
They continue to be sent exclusively on change, never periodically.
Decision made deliberately to keep the change small; can be followed up
later if needed.

No CLI flag for the interval — it lives exclusively as a WebUI/API
setting, analogous to language and password.

## 8. UI

New "Resend" checkbox per signal row next to the existing "Exported"
checkbox ([web/index.html:427](../../../src/loxmatter/web/index.html)),
the same PATCH interaction on toggling. New input field for the interval
in seconds in the WebUI's settings area, PATCH on change, with
display/validation of the minimum from section 5.

## 9. Verification

- A signal with `resend = false` (default) is not picked up by
  `resend_marked()`, even if its value has been unchanged for a long time.
- A signal with `resend = true` appears on every `resend_marked()` run,
  regardless of its change status.
- `resend_all()` stays unaffected by this: it still captures EVERY known
  value, regardless of the `resend` flag — `/resync` and bridge start may
  rely on that (see section 6).
- `d<id>_online` and pulse-counter keys never show up in
  `resend_marked()`, even if someone (accidentally) tries to mark them.
- A change to the interval via `PATCH /api/settings/resend-interval` takes
  effect on `_resend_loop`'s cadence within a few seconds, without a
  restart.
- Migration: an existing database without a `resend` column gets it added
  automatically on open, all rows with `resend = false`.
- `PATCH /api/signals/{key}` with `resend` set changes exclusively that
  field, `exported`/`functional`/key stay untouched.

## 10. Open points

1. Whether online status and pulse counters should later also get a
   `resend` flag remains open. Until someone asks for it: no (section 7).
2. The exact path/name of the new settings endpoint (section 5) is an
   implementation-detail decision, not a design decision of this design.
3. Whether a too-low chosen interval (e.g. 10s with many marked signals)
   should additionally be checked server-side against the current count of
   marked signals (protection against a renewed, just smaller, burst
   problem) is not decided. Proposal for implementation: for now, only the
   fixed minimum from section 5, no dynamic check — YAGNI, until it turns
   out to be needed.
