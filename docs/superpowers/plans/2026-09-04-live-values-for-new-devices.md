# Live values without a restart: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A device commissioned after the bridge has started — or one that later reports new attribute paths — gets its attribute subscriptions and start values without a restart of the bridge.

**Architecture:** A new method `BridgeMatterClient.follow_node(node_id)` catches subscriptions up: it diffs a node's paths against the already-subscribed (node, path) pairs, creates a subscription for each missing one, and passes the snapshot to the handler. It is triggered from the commissioning route (after `register_device`) and from the dispatch loop on `NODE_ADDED`/`NODE_UPDATED`. `Runtime` fulfills the new handler call with `register_signals` → `invalidate_index` → seeding values.

**Tech Stack:** Python 3.12, asyncio, python-matter-server 8.1.2, FastAPI, pytest (`asyncio_mode = "auto"`), Alpine.js for the UI.

**Spec:** [2026-09-04-live-values-for-new-devices-design.md](../specs/2026-09-04-live-values-for-new-devices-design.md)

## Global Constraints

- Comments, docstrings, and UI text in German, in the style of the surrounding files: justify why something is the way it is, don't repeat what the code says.
- Source files contain no umlauts in identifiers or comments, wherever the surrounding code avoids them (`ue`/`ae`/`oe`); UI text in `index.html`/`app.js`, however, does.
- `uv run ruff format` on every touched file; `uv run ruff check` must produce no **new** findings (4 `SIM118` findings in `tests/loxone/test_runtime.py` already exist and stay untouched).
- `uv run mypy` (strict, `files = ["src", "scripts"]`) must stay clean.
- Line length 100 (`[tool.ruff] line-length = 100`).
- TDD: no production code without a previously failing test.
- Every task ends with its own commit.

---

### Task 1: `Runtime.on_node_snapshot` — signal rows, cache, values

The recipient of the catch-up. Testable independently of the subscriptions, hence first.

**Files:**
- Modify: `src/loxmatter/matter/client.py` (protocol `RuntimeEventHandler`, from line 134)
- Modify: `src/loxmatter/loxone/runtime.py` (new method after `seed_from_snapshot`)
- Test: `tests/loxone/test_runtime.py`

**Interfaces:**
- Consumes: `Store.register_signals(device_id, snapshot)`, `Runtime.invalidate_index(device_id)`, `Runtime._cache_attribute(device_id, path, raw)`, `Runtime._cache_online(device_id, online)` — all existing.
- Produces: `RuntimeEventHandler.on_node_snapshot(device_id: int, snapshot: NodeSnapshot) -> None` (async) and `Runtime.on_node_snapshot` with the same signature. Task 2 calls it.

- [ ] **Step 1: Write the failing test**

Append to `tests/loxone/test_runtime.py`:

```python
async def test_on_node_snapshot_registers_a_new_signal_and_lets_it_through(
    environment, monkeypatch
):
    """The core of the catch-up: a path the store did not yet know at
    commissioning time must afterward have a signal row AND make it
    through the runtime's signal cache. This is exactly where the test
    catches the forgotten `invalidate_index` call - without it,
    `register_signals` does create the row, but `_signal_for` stays at
    its once-loaded state and every update to the new path runs into a
    void for the rest of the process."""
    runtime, sender, _, device_id, _ = environment
    new_ref = SignalRef(9, 1234, 5, SignalKind.ATTRIBUTE)
    key = f"d{device_id}_9_c1234_a5"

    def extended_extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]:
        return [*extract_signals(snapshot), new_ref]

    # Index first, as in production: the runtime has already seen the
    # device once before the new path shows up.
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.sent == []

    monkeypatch.setattr("loxmatter.model.store.extract_signals", extended_extract_signals)
    await runtime.on_node_snapshot(device_id, _plug_snapshot())

    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.keys() == [key]


async def test_on_node_snapshot_seeds_the_values_without_sending(environment):
    """Same reasoning as with `seed_from_snapshot`: the cache fills up,
    nothing is sent. A freshly created signal has no virtual input in
    Loxone yet anyway - that only comes into existence once the template
    is exported."""
    runtime, sender, _, device_id, _ = environment

    await runtime.on_node_snapshot(device_id, _plug_snapshot())

    assert runtime._last_values[f"d{device_id}_online"] is True
    assert len(runtime._last_values) == 111
    assert sender.sent == []


async def test_on_node_snapshot_keeps_the_key_and_the_export_flag(environment):
    """`register_signals` is explicitly built for repeated calls (see its
    docstring). If the catch-up reassigned keys or reset `exported`,
    every repeated call would destroy the wiring in the Loxone
    configuration."""
    runtime, _, store, device_id, _ = environment
    before = store.signals(device_id)[0]
    store.set_exported(before.key, not before.exported)
    expected = not before.exported

    await runtime.on_node_snapshot(device_id, _plug_snapshot())

    after = store.signal_by_key(before.key)
    assert after is not None
    assert after.exported is expected


async def test_on_node_snapshot_seeds_an_unavailable_node_as_offline(environment):
    runtime, _, _, device_id, _ = environment
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    raw = dict(raw)
    raw["available"] = False

    await runtime.on_node_snapshot(device_id, NodeSnapshot.from_raw(raw["node_id"], raw))

    assert runtime._last_values[f"d{device_id}_online"] is False
```

`_plug_snapshot`, `SignalRef`, `SignalKind`, `extract_signals`, `FIXTURES`, `json`, and `NodeSnapshot` are already imported/defined in this file (see `test_invalidate_index_lets_a_newly_registered_signal_through` and `test_seed_from_snapshot_populates_cache_without_sending`). Import nothing new.

- [ ] **Step 2: Run the test and see it fail**

Run: `uv run pytest tests/loxone/test_runtime.py -q -k on_node_snapshot`
Expected: 4 FAILED with `AttributeError: 'Runtime' object has no attribute 'on_node_snapshot'`

- [ ] **Step 3: Extend the protocol**

In `src/loxmatter/matter/client.py`, in `class RuntimeEventHandler(Protocol)`, insert after `async def set_online(...)`:

```python
    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None: ...
```

And extend the class docstring with a paragraph:

```python
class RuntimeEventHandler(Protocol):
    """What `subscribe()` needs from its caller — `Runtime`
    (loxone/runtime.py) already fulfills this unchanged, so `_run()` can
    pass it directly as `handler`, without writing an adapter.

    `on_node_snapshot` was added with the subscription catch-up
    (`follow_node`): the client sees a device with paths for which there
    is no signal row yet, and cannot do anything with that itself — it
    doesn't know the `Store` and shouldn't. The handler, on the other
    hand, has it."""
```

- [ ] **Step 4: Write `Runtime.on_node_snapshot`**

In `src/loxmatter/loxone/runtime.py`, insert directly after `seed_from_snapshot`:

```python
    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None:
        """Catches up a device whose attribute paths have changed -
        called from `BridgeMatterClient.follow_node`.

        Three steps, whose order is not arbitrary:

        1. `register_signals` creates the rows for new paths. The
           method is explicitly built for repeated calls (see its
           docstring): key and title stay, `exported` stays untouched
           for known signals, `unit`/`exportability`/`functional` are
           caught up.
        2. `invalidate_index` discards this device's signal cache.
           **Without this step, the whole operation would be
           pointless**: `_signal_for` reads a device's signals exactly
           once and remembers that in `_indexed`; a signal just created
           would then exist in the database, but every update to it
           would run into a void for the rest of the process - no
           error, just a `debug` entry. The docstring of
           `invalidate_index` has demanded this call since phase 4;
           this is its first caller.
        3. Seed values, via the same `_cache_attribute` path as
           `seed_from_snapshot` - and for the same reason: a plug with
           no load never reports a changing voltage, so its value would
           otherwise never come into existence.

        Sends nothing itself, exactly like `seed_from_snapshot` (see
        there). Additional reason here: a freshly created signal has no
        virtual input in Loxone at all yet - that only comes into
        existence once the template is exported and imported.
        """
        self._store.register_signals(device_id, snapshot)
        self.invalidate_index(device_id)
        self._cache_online(device_id, snapshot.available)
        for path, raw in snapshot.attributes.items():
            self._cache_attribute(device_id, path, raw)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/loxone/test_runtime.py -q`
Expected: all PASS

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 6: Format, check, commit**

```bash
uv run ruff format src/loxmatter/loxone/runtime.py src/loxmatter/matter/client.py tests/loxone/test_runtime.py
uv run mypy
git add src/loxmatter/loxone/runtime.py src/loxmatter/matter/client.py tests/loxone/test_runtime.py
git commit -m "feat(runtime): on_node_snapshot zieht Signalzeilen, Cache und Werte nach"
```

---

### Task 2: `BridgeMatterClient.follow_node` — catching up subscriptions

**Files:**
- Modify: `src/loxmatter/matter/client.py` (`__init__`, `disconnect`, `subscribe`, new methods)
- Test: `tests/matter/test_client.py` (the existing `subscribe()` tests live there, along with `FakeNode`, `FakeUpstream`, `FakeHandler`, `make_connected_pair`, and `_settle`)

**Interfaces:**
- Consumes: `RuntimeEventHandler.on_node_snapshot` from task 1.
- Produces: `BridgeMatterClient.follow_node(node_id: int) -> None` (async). Task 3 and task 4 call it.

- [ ] **Step 1: Extend the fakes and write the failing tests**

Everything in `tests/matter/test_client.py`.

First, extend `FakeUpstream` with a method, directly after `get_nodes`:

```python
    def add_node(self, node: FakeNode) -> None:
        """A device that is added only after start_listening() - with the
        real MatterClient, the NODE_ADDED event fills the node cache
        accordingly. Exactly the case `follow_node` covers."""
        self._nodes.append(node)
```

Extend `FakeHandler` with the new handler call — in `__init__`:

```python
        self.snapshot_calls: list[tuple[int, NodeSnapshot]] = []
```

and as a method:

```python
    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None:
        self.snapshot_calls.append((device_id, snapshot))
```

For this, add `from loxmatter.matter.models import NodeSnapshot`, if the file doesn't already import it.

Raise `_settle()` from three to six passes, with an extended docstring:

```python
async def _settle() -> None:
    """Lets subscribe()'s dispatch task catch up with the queue — put_nowait()
    from a synchronous callback and its processing in the background task
    otherwise land in different event-loop turns.

    Six passes instead of three, since a NODE_ADDED/NODE_UPDATED produces
    two entries (reachability and catch-up) and the catch-up itself waits
    on the handler once more."""
    for _ in range(6):
        await asyncio.sleep(0)
```

And a reading aid alongside the other module functions:

```python
def _attribute_subscriptions(upstream: FakeUpstream) -> list[str]:
    """The keys of the active attribute subscriptions, one per (node,
    path), in the form `attribute_updated/<node>/<path>`.

    Deliberately reads the fake's `_subscribers` directly: that way it
    exactly mirrors the key matching of `MatterClient._signal_event()`,
    and that registration scheme is exactly what is meant to be checked
    here."""
    prefix = f"{EventType.ATTRIBUTE_UPDATED.value}/"
    return sorted(
        key
        for key, callbacks in upstream._subscribers.items()
        if key.startswith(prefix) and callbacks
    )
```

Then append the tests:

```python
# --- follow_node() ----------------------------------------------------


async def test_follow_node_subscribes_a_node_that_did_not_exist_at_subscribe_time():
    """The reported case: a device commissioned only after `subscribe()`
    had not a single attribute subscription - its signals showed "-"
    until the next restart of the bridge."""
    bridge, upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda node_id: {12: 5, 8: 9}.get(node_id), handler)

    upstream.add_node(FakeNode(8, {"1/6/0": True}))
    await bridge.follow_node(8)
    upstream.emit(EventType.ATTRIBUTE_UPDATED, False, node_id=8, attribute_path="1/6/0")
    await _settle()

    assert handler.attribute_calls == [(9, "1/6/0", False)]


async def test_follow_node_does_not_subscribe_the_same_path_twice():
    """A second subscription for the same path would deliver every value
    twice - `on_attribute` would run twice, and for an event signal the
    counter would count up twice."""
    bridge, upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    await bridge.follow_node(12)
    upstream.emit(EventType.ATTRIBUTE_UPDATED, False, node_id=12, attribute_path="1/6/0")
    await _settle()

    assert handler.attribute_calls == [(5, "1/6/0", False)]


async def test_follow_node_only_subscribes_the_paths_that_are_new():
    node = FakeNode(12, {"1/6/0": True})
    bridge, upstream = make_connected_pair([node])
    await bridge.connect()
    await bridge.subscribe(lambda _node_id: 5, FakeHandler())

    node.node_data.attributes["1/8/0"] = 254
    await bridge.follow_node(12)

    assert _attribute_subscriptions(upstream) == [
        "attribute_updated/12/1/6/0",
        "attribute_updated/12/1/8/0",
    ]


async def test_follow_node_without_new_paths_leaves_the_handler_alone():
    """The common case in production: `NODE_UPDATED` also fires on a
    reachability change and after every re-subscription. For a device
    with no new paths, the diff is empty, and the operation ends before
    the handler - otherwise every one of these notifications would write
    over a hundred UPDATE statements into the database."""
    bridge, _upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    await bridge.follow_node(12)

    assert handler.snapshot_calls == []


async def test_follow_node_hands_the_snapshot_to_the_handler():
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda node_id: {8: 42}.get(node_id), handler)

    upstream.add_node(FakeNode(8, {"0/40/1": "IKEA of Sweden", "1/6/0": True}))
    await bridge.follow_node(8)

    assert [(device_id, snap.node_id) for device_id, snap in handler.snapshot_calls] == [(42, 8)]
    assert handler.snapshot_calls[0][1].vendor_name == "IKEA of Sweden"


async def test_follow_node_subscribes_even_when_the_store_does_not_know_the_node():
    """The subscriptions come into existence anyway - only the handler is
    left out, because there is no device the values would belong to. As
    soon as the commissioning route registers the device and catches up
    again, the handler branch takes effect."""
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: None, handler)

    upstream.add_node(FakeNode(8, {"1/6/0": True}))
    await bridge.follow_node(8)

    assert "attribute_updated/8/1/6/0" in _attribute_subscriptions(upstream)
    assert handler.snapshot_calls == []


async def test_follow_node_before_subscribe_does_nothing():
    """No raising: the commissioning route calls `follow_node`
    unconditionally, and a setup without a subscription should not fail
    because of it."""
    bridge, upstream = make_connected_pair([FakeNode(12, {"1/6/0": True})])
    await bridge.connect()

    await bridge.follow_node(12)

    assert _attribute_subscriptions(upstream) == []


async def test_follow_node_for_an_unknown_node_does_nothing():
    bridge, _upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    await bridge.follow_node(999)

    assert handler.snapshot_calls == []
```

- [ ] **Step 2: Run the test and see it fail**

Run: `uv run pytest tests/matter/ -q -k follow_node`
Expected: FAILED with `AttributeError: 'BridgeMatterClient' object has no attribute 'follow_node'`

- [ ] **Step 3: Add the bookkeeping**

In `BridgeMatterClient.__init__`, after `self._thread_dataset_set = False`:

```python
        # subscribe()/follow_node() state. The set of already-created
        # attribute subscriptions is the only source for what counts as
        # "new" - a second subscription for the same (node, path) would
        # deliver every value twice. Queue, handler, and the device_id
        # resolution stay reachable after subscribe(), because follow_node
        # needs them.
        self._subscribed_paths: set[tuple[int, str]] = set()
        self._queue: asyncio.Queue[_QueueItem] | None = None
        self._handler: RuntimeEventHandler | None = None
        self._resolve_device_id: Callable[[int], int | None] | None = None
```

In `disconnect()`, with the other resets (directly after `self._unsubscribers = []`):

```python
        self._subscribed_paths = set()
        self._queue = None
        self._handler = None
        self._resolve_device_id = None
```

- [ ] **Step 4: Extract the subscription creation**

In `client.py`, add a new private method, above `subscribe`:

```python
    def _subscribe_attribute_paths(
        self,
        upstream: Any,
        queue: asyncio.Queue[_QueueItem],
        node_id: int,
        paths: Iterable[str],
    ) -> int:
        """Creates one attribute subscription for each not-yet-subscribed
        (node, path) pair, and returns their count.

        One place for both callers (`subscribe` and `follow_node`): two
        places that rebuild the same registration scheme drift apart
        sooner or later - and that would go unnoticed here, because a
        missing subscription is not an error, it's silence.

        `queue` comes in as a parameter instead of from `self._queue`, so
        that `subscribe` can pass it before it is stored in the field -
        and so that no not-`None` check is needed here, whose narrowing
        wouldn't carry through the closure below anyway.
        """
        # Lazily imported like everywhere else in this file: tests with a
        # fake upstream should never have to load matter_server.
        from matter_server.common.models import EventType

        added = 0
        for path in paths:
            if (node_id, path) in self._subscribed_paths:
                continue

            # Default arguments bind node_id/path per loop iteration,
            # instead of evaluating the name from the enclosing scope too
            # late (the classic closure trap in a loop).
            def on_attribute_event(
                _event: Any, data: Any, node_id: int = node_id, path: str = path
            ) -> None:
                queue.put_nowait(_AttributeUpdate(node_id, path, data))

            self._unsubscribers.append(
                upstream.subscribe_events(
                    on_attribute_event,
                    event_filter=EventType.ATTRIBUTE_UPDATED,
                    node_filter=node_id,
                    attr_path_filter=path,
                )
            )
            self._subscribed_paths.add((node_id, path))
            added += 1
        return added
```

Import `Iterable` for this too: `from collections.abc import Callable, Iterable`.

Then, in `subscribe()`, replace the attribute loop. From:

```python
unsubscribers = [upstream.subscribe_events(on_node_or_availability_event)]

# Attribute updates: see the module docstring for why that only works
# per known (node, path) pair. Default arguments bind node_id/path per
# loop iteration, instead of evaluating the name from the enclosing
# scope too late (the classic closure trap in a loop).
for node in upstream.get_nodes():
    for path in node.node_data.attributes:

        def on_attribute_event(
            _event: Any, data: Any, node_id: int = node.node_id, path: str = path
        ) -> None:
            queue.put_nowait(_AttributeUpdate(node_id, path, data))

        unsubscribers.append(
            upstream.subscribe_events(
                on_attribute_event,
                event_filter=EventType.ATTRIBUTE_UPDATED,
                node_filter=node.node_id,
                attr_path_filter=path,
            )
        )

self._unsubscribers = unsubscribers
self._dispatch_task = asyncio.create_task(self._dispatch_loop(queue, resolve_device_id, handler))
```

becomes:

```python
self._unsubscribers = [upstream.subscribe_events(on_node_or_availability_event)]
self._subscribed_paths = set()
self._queue = queue
self._handler = handler
self._resolve_device_id = resolve_device_id

# Attribute updates: see the module docstring for why that only works
# per known (node, path) pair. Whatever arrives after this call is
# caught up by `follow_node`.
for node in upstream.get_nodes():
    self._subscribe_attribute_paths(upstream, queue, node.node_id, node.node_data.attributes)

self._dispatch_task = asyncio.create_task(self._dispatch_loop(queue, resolve_device_id, handler))
```

- [ ] **Step 5: Write `follow_node`**

Insert directly after `subscribe()`:

```python
async def follow_node(self, node_id: int) -> None:
    """Catches up a node's attribute subscriptions.

    Two callers, one operation: the commissioning route
    (`api/devices.py`) after registering a new device, and
    `_dispatch_loop` on `NODE_ADDED`/`NODE_UPDATED` for a device that
    later reports new paths.

    **Why the route cannot simply wait for the event:** matter-server
    reports `NODE_ADDED` still WHILE `commission_with_code` is running
    (`device_controller._setup_node` signals it before the call
    returns). At that point the store does not yet know the node,
    `resolve_device_id` returns `None`, and no second notification
    follows for a device that then sits quietly on the network.
    Recorded on the running stack on 2026-09-04: node 8 finished
    commissioning at 11:15:33, including "Subscription succeeded" - all
    of that before the return to the route.

    An empty diff is the common case and costs nothing: `NODE_UPDATED`
    also fires on every reachability change and after every
    re-subscription, and for a device with no new paths the operation
    ends here, before the handler (and therefore the store) is touched
    at all.

    Called before `subscribe()`, the method does nothing, instead of
    raising: the commissioning route calls it unconditionally, and a
    setup without a subscription should not fail because of it.
    """
    queue = self._queue
    handler = self._handler
    resolve_device_id = self._resolve_device_id
    if queue is None or handler is None or resolve_device_id is None:
        logger.debug("follow_node(%s) ohne vorheriges subscribe() - nichts nachzuziehen", node_id)
        return

    upstream = self._require_upstream()
    node = next((n for n in upstream.get_nodes() if n.node_id == node_id), None)
    if node is None:
        logger.info(
            "Node %s ist matter-server nicht bekannt - keine Abonnements nachgezogen", node_id
        )
        return

    attributes = node.node_data.attributes
    if self._subscribe_attribute_paths(upstream, queue, node_id, attributes) == 0:
        return

    device_id = resolve_device_id(node_id)
    if device_id is None:
        # The subscriptions remain: as soon as the commissioning route
        # registers the device and catches up again, the branch below
        # takes effect.
        logger.debug("Node %s ist keinem Geraet zugeordnet - nur abonniert", node_id)
        return

    await handler.on_node_snapshot(
        device_id,
        NodeSnapshot.from_raw(node_id, {"attributes": attributes, "available": node.available}),
    )
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/matter/ -q`
Expected: all PASS

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 7: Format, check, commit**

```bash
uv run ruff format src/loxmatter/matter/client.py tests/matter/
uv run mypy
git add src/loxmatter/matter/client.py tests/matter/
git commit -m "feat(matter): follow_node zieht Attribut-Abonnements eines Node nach"
```

---

### Task 3: The dispatch loop catches up on `NODE_ADDED`/`NODE_UPDATED`

**Files:**
- Modify: `src/loxmatter/matter/client.py` (new queue type, `on_node_or_availability_event`, `_dispatch_loop`)
- Test: same test file as task 2

**Interfaces:**
- Consumes: `BridgeMatterClient.follow_node` from task 2.
- Produces: nothing for later tasks.

- [ ] **Step 1: Write the failing test**

Append to `tests/matter/test_client.py`:

```python
async def test_a_node_update_with_new_paths_is_followed_automatically():
    """The second case of the known limitation: a device commissioned
    long ago reports, after a firmware update, a path that did not exist
    at start. `NODE_UPDATED` fires in matter-server exactly when a
    device has been re-interviewed - the right trigger."""
    node = FakeNode(12, {"1/6/0": True})
    bridge, upstream = make_connected_pair([node])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    node.node_data.attributes["1/8/0"] = 254
    upstream.emit(EventType.NODE_UPDATED, node, node_id=12)
    await _settle()

    assert "attribute_updated/12/1/8/0" in _attribute_subscriptions(upstream)
    assert [device_id for device_id, _ in handler.snapshot_calls] == [5]


async def test_an_availability_update_without_new_paths_touches_no_handler():
    """The most common cause of `NODE_UPDATED` at all. It must not
    trigger a store access - and must still deliver the reachability
    change as before."""
    node = FakeNode(12, {"1/6/0": True})
    bridge, upstream = make_connected_pair([node])
    await bridge.connect()
    handler = FakeHandler()
    await bridge.subscribe(lambda _node_id: 5, handler)

    upstream.emit(EventType.NODE_UPDATED, node, node_id=12)
    await _settle()

    assert handler.snapshot_calls == []
    assert handler.availability_calls == [(5, True)]
```

- [ ] **Step 2: Run the test and see it fail**

Run: `uv run pytest tests/matter/ -q -k "node_update"`
Expected: FAILED — `(8, "1/8/0") not in upstream.attribute_filters`

- [ ] **Step 3: Add the queue type**

With the other `@dataclass(frozen=True)` definitions in `client.py`:

```python
@dataclass(frozen=True)
class _FollowNode:
    """Trigger to catch up a node's subscriptions.

    Runs through the same queue as the value updates, instead of
    directly out of the synchronous event callback: `follow_node` is a
    coroutine, and the callback cannot await one (see
    `on_node_or_availability_event`).
    """

    node_id: int
```

And extend the union:

```python
_QueueItem = _AttributeUpdate | _EventUpdate | _AvailabilityUpdate | _FollowNode
```

- [ ] **Step 4: Enqueue the event**

In `subscribe()`, in `on_node_or_availability_event`, extend the `NODE_ADDED`/`NODE_UPDATED` branch:

```python
            elif event in (EventType.NODE_ADDED, EventType.NODE_UPDATED):
                queue.put_nowait(_AvailabilityUpdate(data.node_id, data.available))
                # In addition to the reachability update, not instead of
                # it: both notifications carry the same cause, but one
                # path sets `d<id>_online`, the other catches up
                # subscriptions.
                queue.put_nowait(_FollowNode(data.node_id))
```

- [ ] **Step 5: Extend the dispatch loop**

In `_dispatch_loop`, have the body of `try` start like this:

```python
            try:
                if isinstance(item, _FollowNode):
                    # BEFORE the device_id resolution: `follow_node`
                    # creates subscriptions even for a node the store
                    # does not (yet) know, and decides itself whether the
                    # handler gets anything to see.
                    await self.follow_node(item.node_id)
                    continue
                device_id = resolve_device_id(item.node_id)
```

The rest of the loop stays unchanged.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/matter/ -q`
Expected: all PASS

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 7: Format, check, commit**

```bash
uv run ruff format src/loxmatter/matter/client.py tests/matter/
uv run mypy
git add src/loxmatter/matter/client.py tests/matter/
git commit -m "feat(matter): NODE_ADDED/NODE_UPDATED ziehen neue Attributpfade nach"
```

---

### Task 4: Commissioning route, UI, and the known limitation

**Files:**
- Modify: `src/loxmatter/api/devices.py` (`commission_device`, from line 295)
- Modify: `src/loxmatter/matter/client.py` (module docstring, "Known limitation" section)
- Modify: `src/loxmatter/web/app.js` (success message in `commissionDevice`)
- Test: `tests/api/test_devices.py`, `tests/api/conftest.py`

**Interfaces:**
- Consumes: `BridgeMatterClient.follow_node` from task 2.
- Produces: nothing.

- [ ] **Step 1: Write the failing test**

In `tests/api/conftest.py`, extend `FakeMatterClient.__init__` with a list:

```python
        # The node IDs for which the route triggered catching up the
        # subscriptions (`BridgeMatterClient.follow_node`).
        self.followed: list[int] = []
```

and the method for it, directly after `set_thread_dataset`:

```python
    async def follow_node(self, node_id: int) -> None:
        self.followed.append(node_id)
        self.order.append("follow")
```

Append to `tests/api/test_devices.py`:

```python
async def test_commissioning_follows_the_new_node(api):
    """Without this call, the freshly commissioned device would have not
    a single attribute subscription: `subscribe()` ran once at bridge
    start, and this device's `NODE_ADDED` demonstrably arrived before the
    store could give it a device_id."""
    client, store, _, fake_client = api

    new_device = (await client.post("/api/devices/commission", json={"code": "MT:X"})).json()

    assert fake_client.followed == [store.device(new_device["id"]).node_id]


async def test_the_new_node_is_followed_only_after_it_is_registered(api):
    """The order is the entire reason for this call: if the route caught
    up earlier, `resolve_device_id` would again run into a void - exactly
    the race `NODE_ADDED` has already lost."""
    client, _, _, fake_client = api

    await client.post("/api/devices/commission", json={"code": "MT:X"})

    assert fake_client.order == ["commission", "follow"]
```

The design (section 8) additionally names, for this level, "`GET
/api/devices/{id}/signals` returns values instead of `null`". That is
deliberately NOT rebuilt here: `FakeMatterClient` and `FakeRuntime` are
two mutually independent fakes, and the real path from the client to the
runtime runs through `follow_node` → `handler.on_node_snapshot`. A test
that rebuilt this path in the fakes would check the fakes, not the code.
The two halves are instead covered where they actually run: seeding the
values in task 1 (`test_on_node_snapshot_seeds_the_values_without_sending`),
calling the handler in task 2 (`test_follow_node_hands_the_snapshot_to_the_handler`).
The connection between the two is checked by the "Verification on the
running stack" section at the end of this plan.

- [ ] **Step 2: Run the test and see it fail**

Run: `uv run pytest tests/api/test_devices.py -q -k follow`
Expected: 2 FAILED — `assert [] == [<node_id>]`

- [ ] **Step 3: Make the route catch up**

In `src/loxmatter/api/devices.py`, in `commission_device`, directly after `await runtime.set_online(device_id, snapshot.available)`:

```python
        # Only now, after `register_device`: `follow_node` resolves the
        # node ID via the store, and before this point there would be
        # nothing there to resolve - the same race `NODE_ADDED` has
        # already lost (see the comment above and the docstring of
        # `follow_node`). Creates the attribute subscriptions for this
        # device and seeds its values, so the signals immediately show
        # numbers instead of dashes - previously this required a restart
        # of the bridge.
        await active_client.follow_node(snapshot.node_id)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/api/ -q`
Expected: all PASS

- [ ] **Step 5: Correct the success message in the UI**

In `src/loxmatter/web/app.js`, in `commissionDevice`, replace the comment block and the assignment. From:

```javascript
        // The sentence about the subscription is not decoration (review
        // fix 3, 2026-09-03, see spec 12.3): `BridgeMatterClient.subscribe()`
        // runs exactly once at bridge start and only registers the
        // (node, path) pairs known at that time. A device just
        // commissioned immediately goes "online" via the NODE_ADDED
        // event and shows up green - but gets not a single attribute
        // value until the next restart. Without this note, the user
        // sees a green device whose signals are all "-", and looks for
        // the fault in themselves.
        this.commissionMessage =
          `${device.label} wurde eingelernt. Live-Werte erscheinen erst nach einem ` +
          "Neustart der Brücke – bis dahin zeigt das Gerät zwar „online“, aber jedes " +
          "Signal „-“ (bekannte Grenze, Spec 12.3). Der Export der Vorlagen " +
          "funktioniert davon unabhängig schon jetzt.";
```

becomes:

```javascript
        // The earlier sentence "live values only after a restart of the
        // bridge" is gone because the limitation is gone: the
        // commissioning route calls `follow_node`, which creates this
        // device's attribute subscriptions and seeds its values (design
        // from 2026-09-04). A note is still needed here, just a
        // different one: that the values arrive in the Miniserver only
        // after the templates have been exported and imported into
        // Loxone Config, because until then there is no virtual input
        // there.
        this.commissionMessage =
          `${device.label} wurde eingelernt und liefert ab sofort Live-Werte – ohne ` +
          "Neustart der Brücke. Im Miniserver kommen sie an, sobald Sie die Vorlagen " +
          "exportiert und in Loxone Config importiert haben.";
```

- [ ] **Step 6: Replace the known limitation in the module docstring**

In `src/loxmatter/matter/client.py`, in the module docstring, replace the "Known limitation: …" paragraph with:

```
Whatever arrives after `subscribe()` is caught up by `follow_node()` — a
device commissioned only afterward, as well as a known device that later
reports new attribute paths. It is triggered from the dispatch loop on
`NODE_ADDED`/`NODE_UPDATED` and additionally from the commissioning
route. The "additionally" is not belt-and-suspenders: the `NODE_ADDED`
of a device just commissioned demonstrably arrives BEFORE
`commission_with_code` returns and the store can give the node a
device_id — the notification is therefore discarded, and a second one
does not follow for a device that then sits quietly on the network. See
docs/superpowers/specs/2026-09-04-live-values-for-new-devices-design.md.
```

- [ ] **Step 7: Run everything and commit**

```bash
uv run pytest -q
uv run ruff format src/loxmatter/api/devices.py src/loxmatter/matter/client.py tests/api/
uv run ruff check src tests scripts
uv run mypy
git add -A
git commit -m "feat(api): Einlern-Route zieht die Abonnements des neuen Geraets nach"
```

Expected: `uv run pytest -q` reports all PASS; `uv run ruff check` continues to report exactly the 4 pre-existing `SIM118` findings in `tests/loxone/test_runtime.py` and none new; `uv run mypy` reports `Success`.

---

## Conclusion: verification on the running stack

After task 4, before the work counts as done — the test suite cannot
recreate a real `NODE_ADDED` timing:

- [ ] Deploy to the test host and commission a device.
- [ ] Check in the UI: the device shows online **and** its signals show
      numbers instead of dashes, without a restart of the bridge.
- [ ] Look through `docker logs loxmatter` for `Aktualisierung fuer unbekannte Node`
      — one such entry for the new node is expected (the lost
      `NODE_ADDED`), a persistent stream of them is not.
