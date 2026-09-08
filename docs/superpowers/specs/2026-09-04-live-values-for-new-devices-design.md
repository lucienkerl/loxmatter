# Live values without a restart: catching subscriptions up

Design, September 4, 2026. Lifts the known limitation that the module
docstring of
[`matter/client.py`](../../../src/loxmatter/matter/client.py) has carried
as an open point since phase 4, and extends
[the main document](2026-09-01-matter-loxone-bridge-design.md).

## 1. The problem

`BridgeMatterClient.subscribe()`
([client.py:486](../../../src/loxmatter/matter/client.py)) runs exactly
once, at bridge start, and registers one attribute subscription per (node,
path) pair **known at call time**. The reason for this is not an
oversight but a property of `python-matter-server`: on
`EventType.ATTRIBUTE_UPDATED`, `data` is only ever the new value — no
`node_id`, no path. A single wildcard subscription could therefore not
attribute an attribute update to any device; only `node_filter` and
`attr_path_filter` determine what a callback stands for, and the callback
closes over both.

Two gaps follow from that, both already named in the module docstring:

1. **A device commissioned after this call** has not a single attribute
   subscription. It appears in the UI, but every signal shows "-" until
   the bridge restarts.
2. **A known device that later reports new attribute paths** — say after a
   firmware update that unlocks a cluster — stays just as silent for
   those new paths.

The first case was reported on 2026-09-04, right after commissioning
itself started working again. Until then, the UI had not fixed it, only
described it: after every commissioning it showed the sentence "Live
values only appear after a restart of the bridge."

## 2. Why the event alone isn't enough

The obvious approach would be to react to `NODE_ADDED`. That doesn't work,
and the reason is the same one that already swallowed the online status:
matter-server reports `NODE_ADDED` **while** `commission_with_code` is
still running (`device_controller._setup_node`, `signal_event` before the
call returns). At that point, `store.register_device` has not yet given
the node a `device_id`, `_dispatch_loop`
([client.py:562](../../../src/loxmatter/matter/client.py)) rightly
discards the notification, and a second one never arrives for a device
that then sits quietly on the network.

Recorded on the running stack, node 8:

```
11:15:29  Matter commissioning of Node ID 8 successful.
11:15:31  <Node:8> Setting up attributes and events subscription.
11:15:33  Subscription succeeded with report interval [1, 60]
11:15:33  Commissioning of Node ID 8 completed.
```

All of that happens before the call returns to the commissioning route.
The route must therefore trigger the catch-up **itself**.

For the second case, on the other hand, the event is the right trigger.
`NODE_UPDATED` does not fire per attribute value, but (checked against the
installed version) at five points: after a repeated interview ("existing
node, signal node updated event"), during the interview of a test node,
after a successful re-subscription, after a successful subscription, and
on a reachability change. New paths become visible exactly at the repeated
interview — and that is exactly when it fires.

## 3. The solution: one operation, two triggers

A new method `BridgeMatterClient.follow_node(node_id)` handles the
catch-up completely:

```
new_paths = node's paths − paths already subscribed for this node
if empty: done                          ← the common case, costs nothing
for each new path: create a subscription (same queue as subscribe())
device_id = resolve_device_id(node_id); if it is None: done
handler.on_node_snapshot(device_id, snapshot)
```

It is triggered from two places:

- **the commissioning route**, after `register_device`/`register_signals`
  — the case from section 2 that no event can cover;
- **the dispatch loop**, on `NODE_ADDED` and `NODE_UPDATED` — the case of
  new paths.

Both run through the same code. Two mechanisms that do almost the same
thing drift apart; one that is called from two places does not.

**The empty diff is the core of the cost accounting.** The frequent
`NODE_UPDATED` events — reachability, re-subscription — concern devices
whose paths have not changed. For them the set difference is empty and the
operation ends before any store access. Only a device with genuinely new
paths triggers work, and only for those paths.

## 4. Who knows what

The split follows the existing layering: `subscribe()` today already gets
only a `resolve_device_id` call and a handler, no store. That stays that
way.

| Component | Responsible for | Does not know about |
|---|---|---|
| `BridgeMatterClient` | Subscriptions, bookkeeping for them, passing the snapshot on | `Store`, `Runtime` |
| `Runtime` (as `RuntimeEventHandler`) | Signal rows, signal cache, seeding values | Subscriptions |
| Commissioning route | Triggering after registration | neither in detail |

**Client.** New: a `set[tuple[int, str]]` over the attribute subscriptions
created, populated in `subscribe()` and extended in `follow_node()`.
Without this bookkeeping, "new" could not be distinguished from "already
present", and a second subscription for the same path would deliver every
value twice. The node itself comes from the upstream cache (`get_nodes()`),
not from a second network call.

**Handler protocol.** `RuntimeEventHandler`
([client.py:134](../../../src/loxmatter/matter/client.py)) gets
`async def on_node_snapshot(device_id: int, snapshot: NodeSnapshot) -> None`.

**Runtime** fulfills this in three steps, in this order:

1. `store.register_signals(device_id, snapshot)`
   ([store.py:938](../../../src/loxmatter/model/store.py)) — explicitly
   built for repeated calls: key and title stay, `exported` stays
   untouched for known signals, `unit`/`exportability`/`functional` are
   caught up, new signals are created. Exactly the case for which the
   docstring there names the firmware update.
2. `invalidate_index(device_id)`
   ([runtime.py:183](../../../src/loxmatter/loxone/runtime.py)) — **without
   this step, the whole operation would be pointless.** `_signal_for`
   ([runtime.py:166](../../../src/loxmatter/loxone/runtime.py)) loads a
   device's signals once and remembers that in `_indexed`; a newly created
   signal would then exist in the database, but every update to it would
   run into a void for the rest of the process. The docstring of
   `invalidate_index` already demands the call, there simply was no caller
   for it so far.
3. Seed values, via the same path as `seed_from_snapshot`
   ([runtime.py:230](../../../src/loxmatter/loxone/runtime.py)) —
   `_cache_attribute` per attribute, plus `_cache_online`.

## 5. Why seeding sends nothing

`seed_from_snapshot` deliberately sends nothing at start; the single
`resend_all()` call afterward sends everything together. `on_node_snapshot`
handles it the same way, for a second reason: a freshly created signal has
no virtual input in Loxone yet at all. That only comes into existence once
the template is exported and imported into Loxone Config. Until then, any
send would go nowhere.

The benefit of seeding lies elsewhere and is nonetheless the actual point
of this design: `_last_values` is the source for `last_values_for` — the
UI thereby shows the values **immediately** instead of dashes. And the
values are ready for the next `/resync`, instead of only coming into
existence on the next change; a plug with no load never reports a
changing voltage (the gap that `seed_from_snapshot` created in the first
place on 2026-09-02).

`d<id>_online` stays unaffected by this: the commissioning route continues
to set it via `set_online`
([devices.py:373](../../../src/loxmatter/api/devices.py)), and that does
send — a reachability change is a message to Loxone, not a start value.

## 6. What changes in the UI

The sentence "Live values only appear after a restart of the bridge –
until then the device shows 'online', but every signal shows '-' (known
limitation, Spec 12.3)" is dropped from the commissioning success message.
It described a limitation that then no longer exists; leaving it in would
be worse than never having had it.

The known limitation in the module docstring of `matter/client.py` is not
deleted, but replaced with a description of what now happens — including
the reasoning from section 2 for why the commissioning route must trigger
this itself despite the existing events. That is the place the next
person will look for it.

## 7. Explicitly out of scope

- **Devices commissioned elsewhere** (matter-server dashboard, Home
  Assistant). They are not in the bridge's store; `resolve_device_id`
  returns `None`, `follow_node` ends there. Creating subscriptions for
  them would have no recipient.
- **A periodic reconciliation** as a safety net against missed events.
  Can be added later if it turns out events go missing — there is no
  evidence for that today.
- **Signals that disappear.** A firmware update that removes a cluster
  leaves behind an orphaned signal row. That is already the case today
  and does not change here.
- **Event subscriptions** (`NODE_EVENT`) and reachability continue to run
  through one wildcard subscription each and need no catch-up — their
  `data` carries the `node_id` itself.

## 8. Verification

New tests, each failing first:

**`tests/matter/`** — `follow_node` subscribes to the paths of a node that
did not yet exist at `subscribe()` time, and an attribute update arriving
afterward reaches the handler. A second call for the same node creates
**no** second subscription (otherwise every value would arrive twice). A
node with new paths gets subscriptions only for the new ones. A node with
no new paths triggers no `on_node_snapshot` call. `follow_node` before
`subscribe()` does nothing, instead of raising. A node the store does not
know is subscribed to, but without a handler call.

**`tests/loxone/`** — `on_node_snapshot` creates the signal row for a new
path, invalidates the signal cache, and seeds the value; an update to the
new path afterward actually lands in the cache (the test that catches the
forgotten `invalidate_index` call). Existing signals keep their key and
`exported`. Nothing is sent during seeding.

**`tests/api/`** — after commissioning, the route calls `follow_node` with
the new device's node ID, and `GET /api/devices/{id}/signals` returns
values instead of `null`.

## 9. Open points

None. The assumptions about `NODE_UPDATED` (section 2) and about the
re-call safety of `register_signals` (section 4) are checked against the
installed versions, not assumed.
