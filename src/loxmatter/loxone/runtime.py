# loxmatter - connects Matter devices to a Loxone Miniserver.
# Copyright (C) 2026 Lucien Kerl
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Connects Matter subscriptions to the UDP sender.

This is where the three things live that a virtual UDP input cannot do on
its own:

Events (spec 6.3) - an input carries values, not "something happened".
Every event becomes a pulse that produces an edge, plus a monotonic
counter that survives a lost UDP packet.

Reachability (spec 6.5) - one digital signal per device, plus a global
heartbeat that serves Loxone as a watchdog and covers "container dead" and
"network gone" equally. A heartbeat that dies on the first send failure
would be useless for exactly this purpose - see `_heartbeat_loop`.

State restoration (spec 6.4) - UDP is stateless. After a Miniserver
restart, all inputs sit at their default value until the next update
arrives; for a temperature sensor that can be hours.

Observers (spec 8.3, phase 5 task 3) - the WebUI shows live values over
the same subscription that also feeds the UDP sender. No second path, no
polling: `add_observer` attaches a UI to the same stream of attribute,
event and online changes that already goes to Loxone - see
`_notify_observers`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import Protocol

from loxmatter.loxone.values import to_loxone_value
from loxmatter.matter.models import NodeSnapshot, SignalKind
from loxmatter.model.store import Store, StoredSignal
from loxmatter.timestamps import now_iso

PULSE_MILLISECONDS = 200
HEARTBEAT_KEY = "bridge_alive"

logger = logging.getLogger(__name__)


class Sender(Protocol):
    """What the runtime needs from the sender - so tests can substitute it."""

    async def send(self, key: str, value: float | bool, *, force: bool = False) -> bool: ...

    async def close(self) -> None: ...


class Runtime:
    PULSE_MILLISECONDS = PULSE_MILLISECONDS

    def __init__(
        self,
        store: Store,
        sender: Sender,
        *,
        heartbeat_seconds: float = 30.0,
        resend_poll_seconds: float = 5.0,
        link_ok: Callable[[], bool] = lambda: True,
    ) -> None:
        self._store = store
        self._sender = sender
        self._heartbeat_seconds = heartbeat_seconds
        self._resend_poll_seconds = resend_poll_seconds
        # Whether the connection to matter-server is currently holding -
        # asked afresh by the heartbeat on EVERY beat (see
        # `_heartbeat_loop`). The annotation `Callable[[], bool]` is a
        # load-bearing safeguard here, not a formality: `cli.serve()`
        # passes `lambda: client.connected`, and `client.connected` on its
        # own - a property, hence a bool evaluated once - would thereby be
        # a type error that `mypy --strict` rejects in CI. Without this
        # annotation the heartbeat would silently hang on the state of the
        # moment of startup and would never fall silent.
        self._link_ok = link_ok
        # When something last arrived from a device at all - one ISO
        # timestamp per device id. IN MEMORY ONLY, not in the database:
        # the same reasoning that the docstring of `StoredDevice` already
        # gives for `online` - reachability is runtime state. A timestamp
        # that survives a restart claims something after startup that
        # nobody has checked; `None`, by contrast, honestly says "nothing
        # heard since this bridge started".
        #
        # The occasion (8 September 2026): a window contact that only
        # sends on change looked in the UI exactly like a button from
        # which nothing had come for five days - both `online: true`.
        # `online` stays what it is; this is the second number that makes
        # the question answerable in the first place.
        self._last_heard: dict[int, str] = {}
        self._last_values: dict[str, float | bool] = {}
        self._counters: dict[str, int] = {}
        self._heartbeat_on = False
        # Long-lived background tasks (heartbeat and resend loop).
        self._tasks: list[asyncio.Task[None]] = []
        # Short-lived pulse tasks, one per `on_event` call. A done_callback
        # throws out every finished task immediately, otherwise the set
        # would grow unbounded with every event (review fix minor #1) -
        # only `stop()` would ever have cleared it otherwise.
        self._pulse_tasks: set[asyncio.Task[None]] = set()
        # Keys whose pulse is currently high. `stop()` lowers them
        # explicitly, because a cancellation during the pulse sleep would
        # otherwise skip the `send(key, False)` in `_release_pulse` and the
        # digital signal would stay stuck on this key until the next event
        # (review fix important #2).
        self._pulses_high: set[str] = set()
        # Index (device_id, path, kind) -> StoredSignal, loaded from the
        # database once per device. `on_attribute` and `on_event` run for
        # every reported value of a device - without this cache that would
        # be a fresh query across ~160 lines per call, and the original
        # design even queried twice: once for the key, a second time for
        # the SignalRef. Here it is read exactly once per device; every
        # further path of the same device is a dict lookup. Whoever calls
        # `Store.register_signals` again for the same device after the
        # first indexing must call `invalidate_index` afterwards -
        # otherwise a newly added signal stays invisible for this runtime
        # (review fix important #3).
        self._signals: dict[tuple[int, str, str], StoredSignal] = {}
        self._indexed: set[int] = set()
        # WebUI observers (spec 8.3) - see `add_observer`.
        self._observers: list[Callable[[str, object], None]] = []

    def add_observer(self, callback: Callable[[str, object], None]) -> None:
        """Registers an observer that sees every value the UDP sender also
        sees (spec 8.3) - no second path, no polling.

        Two rules, both implemented in `_notify_observers`:

        - The observer is called ONLY AFTER sending. The bridge to Loxone
          is the purpose of this runtime; the UI just watches. If sending
          fails, the observer learns what actually happened - not what was
          intended.
        - An observer that throws is logged and skipped. It must not take
          the UDP path down with it - the same rule that hardened the
          heartbeat loop in phase 4 (see `_heartbeat_loop`): a closed
          browser tab must not stop the bridge."""
        self._observers.append(callback)

    def remove_observer(self, callback: Callable[[str, object], None]) -> None:
        """Unsubscribes an observer - e.g. when a WebSocket disconnects. An
        unknown observer (e.g. unsubscribed twice) is not an error, it is
        silently ignored."""
        try:
            self._observers.remove(callback)
        except ValueError:
            pass

    def observer_count(self) -> int:
        """Number of currently registered observers - for tests that want
        to verify that a disconnected client was actually unsubscribed and
        does not linger as a zombie."""
        return len(self._observers)

    def _notify_observers(self, key: str, value: object) -> None:
        """Calls every observer with the key/value pair just sent - ALWAYS
        only after `self._sender.send(...)` has returned (see the call
        sites in `on_attribute`, `on_event`, `_release_pulse` and
        `set_online`).

        Iterates a copy of the list rather than the original: an observer
        that unsubscribes itself during its own call (`remove_observer`)
        must not disrupt the notification of the remaining ones currently
        in progress."""
        for observer in list(self._observers):
            try:
                observer(key, value)
            except Exception:
                # Same reasoning as in `_heartbeat_loop`: an observer
                # failure (e.g. a bug in the WebUI) must not take the UDP
                # path down with it - logged, skipped, moving on to the
                # next observer.
                logger.exception("observer for key %r failed - skipping it", key)

    def _signal_for(self, device_id: int, path: str, kind: SignalKind) -> StoredSignal | None:
        """Finds the stored signal for a Matter path, without querying the
        database again on every call."""
        if device_id not in self._indexed:
            for stored in self._store.signals(device_id):
                self._signals[(device_id, stored.ref.path, stored.ref.kind.value)] = stored
            self._indexed.add(device_id)
        signal = self._signals.get((device_id, path, kind.value))
        if signal is None:
            logger.debug(
                "no signal for device %s, path %s, kind %s - discarding update",
                device_id,
                path,
                kind.value,
            )
        return signal

    def invalidate_index(self, device_id: int | None = None) -> None:
        """Discards the signal cache of one device, or - without an
        argument - of all devices.

        Whoever calls `Store.register_signals` again at runtime for a
        device that is already running (e.g. after a firmware update that
        unlocks a new cluster) MUST call this method afterwards for the
        affected device. Without that, `_signal_for` stays at the state it
        loaded once: the new signal exists in the database, but updates
        for it run into nothing for the rest of the process - no error, no
        log entry apart from the `debug` entry in `_signal_for`.
        """
        if device_id is None:
            self._signals.clear()
            self._indexed.clear()
            return
        self._indexed.discard(device_id)
        for cache_key in [k for k in self._signals if k[0] == device_id]:
            del self._signals[cache_key]

    def _cache_attribute(self, device_id: int, path: str, raw: object) -> str | None:
        """Converts a raw Matter value into the cache and returns the key
        used for it - or `None` if the store knows no signal for this path
        or the value is not exportable (list, struct, text; see
        `to_loxone_value`).

        This single spot decides what becomes of a raw Matter value - both
        for a genuine update (`on_attribute`) and for seeding from the
        current device state (`seed_from_snapshot`). A second spot that
        rebuilt the same conversion would sooner or later drift from this
        one."""
        signal = self._signal_for(device_id, path, SignalKind.ATTRIBUTE)
        if signal is None:
            return None
        value = to_loxone_value(signal.ref, raw)
        if value is None:
            return None
        self._last_values[signal.key] = value
        return signal.key

    async def on_attribute(self, device_id: int, path: str, raw: object) -> None:
        self._mark_heard(device_id)
        key = self._cache_attribute(device_id, path, raw)
        if key is None:
            return
        value = self._last_values[key]
        await self._sender.send(key, value)
        self._notify_observers(key, value)

    async def seed_from_snapshot(self, snapshots: Sequence[NodeSnapshot]) -> int:
        """Fills the cache from the current device state (spec 6.4).

        A live run on 2026-09-02 revealed the gap: `resend_all()` iterates
        `_last_values`, and that is empty at startup - a value ends up
        there only via a subscription that reports *changing* values. A
        plug with no load, for instance, never reports a changing voltage,
        so the cache stayed empty after startup and the first resend sent
        nothing, even though it is precisely the one meant to take over
        the role of `/resync` after a bridge restart. This method fetches
        the missing startup values from `BridgeMatterClient.snapshots()` -
        the same picture that `loxmatter export` also reads from.

        Deliberately sends nothing itself while doing so: it only fills
        `_last_values` via `_cache_attribute` (the same path `on_attribute`
        also takes), and the single `resend_all()` call right after
        seeding (see `_run`) then sends everything together with
        `force=True`. If seeding itself already sent, every startup would
        produce a double send for every signal - once here, once via the
        resend right after - regardless of whether the sender's
        debouncing happens to be empty or not.

        A node that `Store` does not know (never exported, or removed in
        the meantime) does not abort seeding - it is skipped, all other
        nodes are still seeded. An attribute for which the store knows no
        signal is silently discarded, just as with any update at runtime.

        Also seeds `d<id>_online` from `snapshot.available` (review fix
        C1, 2026-09-02): the only writer of `d<id>_online` is otherwise
        `set_online`, called from `BridgeMatterClient._dispatch_loop` on
        NODE_ADDED/NODE_UPDATED/NODE_REMOVED - but `start_listening()`
        fills the initial node cache WITHOUT firing NODE_ADDED, and
        NODE_UPDATED only arrives on a node data message, not on a plain
        attribute update. Without this seeding, `d<id>_online` would stay
        at its `DefVal="0"` after every bridge start - i.e. permanently
        "unreachable" for a device that is simply quiet, and `/resync`
        could not heal that because the key never lands in
        `_last_values`. Exactly the same class of bug that spec 6.4
        already prevents for attributes.

        Returns the number of signals seeded (for the log in `_run`)."""
        count = 0
        for snapshot in snapshots:
            device_id = self._store.device_id_for_node(snapshot.node_id)
            if device_id is None:
                logger.info(
                    "no known device for node %s - skipping snapshot during seeding",
                    snapshot.node_id,
                )
                continue
            self._cache_online(device_id, snapshot.available)
            count += 1
            for path, raw in snapshot.attributes.items():
                if self._cache_attribute(device_id, path, raw) is not None:
                    count += 1
        return count

    async def on_node_snapshot(self, device_id: int, snapshot: NodeSnapshot) -> None:
        """Catches up a device whose attribute paths have changed - called
        from `BridgeMatterClient.follow_node`.

        Three steps. Only one order is binding: `invalidate_index` MUST
        run before seeding (step 3). Whether `register_signals` comes
        before or after `invalidate_index` has no consequence - both just
        need to be finished before seeding makes its first `_signal_for`
        access.

        1. `register_signals` creates the rows for new paths. The method
           is expressly built for repeated calls (see that docstring): key
           and title stay, `exported` stays untouched for known signals,
           `unit`/`exportability`/`functional` are refreshed.
        2. `invalidate_index` discards this device's signal cache.
           **Without this step before seeding, step 3 would be ineffective
           for every new path**: `_signal_for` reads a device's signals
           exactly once and remembers that in `_indexed`; a signal just
           created would then exist in the database, but seeding would not
           find it via the stale cache and would silently discard its
           value - no error, just a `debug` entry. The docstring of
           `invalidate_index` has required this call since phase 4; this
           is its first caller.
        3. Seed values, via the same `_cache_attribute` path as
           `seed_from_snapshot` - and for the same reason: a plug with no
           load never reports a changing voltage, so its value would
           otherwise never arise.

        Sends nothing itself, exactly like `seed_from_snapshot` (see
        there). An additional reason here: a freshly created signal does
        not even have a virtual input in Loxone yet - that only comes into
        being once the template has been exported and imported.
        """
        self._mark_heard(device_id)
        self._store.register_signals(device_id, snapshot)
        self.invalidate_index(device_id)
        self._cache_online(device_id, snapshot.available)
        for path, raw in snapshot.attributes.items():
            self._cache_attribute(device_id, path, raw)

    async def on_event(self, device_id: int, path: str) -> None:
        self._mark_heard(device_id)
        signal = self._signal_for(device_id, path, SignalKind.EVENT)
        if signal is None:
            return
        key = signal.key
        # The counter serves to detect packet loss, not an exact protocol -
        # it therefore deliberately counts up before sending. A counter
        # that got stuck on a failed send() would be no gain for this
        # purpose (review fix minor #2).
        self._counters[key] = self._counters.get(key, 0) + 1
        await self._sender.send(key, True)
        self._notify_observers(key, True)
        self._pulses_high.add(key)
        await self._sender.send(f"{key}_n", self._counters[key])
        self._notify_observers(f"{key}_n", self._counters[key])
        self._last_values[f"{key}_n"] = self._counters[key]
        task = asyncio.create_task(self._release_pulse(key))
        task.add_done_callback(self._pulse_tasks.discard)
        self._pulse_tasks.add(task)

    async def _release_pulse(self, key: str) -> None:
        await asyncio.sleep(PULSE_MILLISECONDS / 1000)
        await self._sender.send(key, False)
        self._notify_observers(key, False)
        self._pulses_high.discard(key)

    @staticmethod
    def _online_key(device_id: int) -> str:
        return f"d{device_id}_online"

    def _cache_online(self, device_id: int, online: bool) -> None:
        """Enters a device's reachability into the cache, without sending.

        A separate step, extracted from `set_online` (review fix C1,
        2026-09-02): `seed_from_snapshot` needs the same key and the same
        cache entry that a later `set_online` would produce via a
        NODE_ADDED/NODE_UPDATED event - but, like any other seeded signal,
        WITHOUT sending itself. The subsequent `resend_all()` in `_run`
        sends everything seeded bundled with `force=True`; if seeding here
        already sent, `d<id>_online` would get a double send on every
        startup (see the docstring of `seed_from_snapshot`)."""
        self._last_values[self._online_key(device_id)] = online

    async def set_online(self, device_id: int, online: bool) -> None:
        self._cache_online(device_id, online)
        key = self._online_key(device_id)
        await self._sender.send(key, online)
        self._notify_observers(key, online)

    def _mark_heard(self, device_id: int) -> None:
        """Records that something has just arrived from this device.

        Sits RIGHT AT THE TOP of `on_attribute`/`on_event`/
        `on_node_snapshot`, before any early return: whether a path can be
        mapped to an exported signal is a question of configuration - the
        report arrived either way, and that is all this timestamp states.
        """
        self._last_heard[device_id] = now_iso()

    def last_heard_for(self, device_id: int) -> str | None:
        """When something last arrived from this device, or `None`.

        `None` means "nothing heard since this bridge started" - see
        `_last_heard` in the constructor for why this is not persisted.
        """
        return self._last_heard.get(device_id)

    def last_values_for(self, device_id: int) -> dict[str, float | bool]:
        """All most-recently-known values of a device, indexed by signal
        key - for the device and signal API (task 2, phase 5), which wants
        to show a live value per signal without running a second
        subscription itself.

        A pure read helper over `_last_values`: returns only what has
        already arrived here once, via a subscription or
        `seed_from_snapshot`. A signal the bridge has never been reported
        does not show up here - the caller treats that as "no value known
        yet" (`None`), not as an error. Text values never show up here at
        all: `_cache_attribute` only stores what `to_loxone_value`
        delivers, and that is always `None` for `Exportability.TEXT` (see
        there) - a virtual UDP input knows no text.

        The prefix comparison is safe against confusing a device with a
        numeric prefix of another (e.g. device 1 vs. device 12): the key
        always carries an underscore directly after the device_id
        (`d1_...` vs. `d12_...`), so `"d12_1_temp".startswith("d1_")` is
        `False`.
        """
        prefix = f"d{device_id}_"
        return {k: v for k, v in self._last_values.items() if k.startswith(prefix)}

    async def resend_all(self) -> int:
        """Sends EVERY known value again, bypassing debouncing - regardless
        of the `resend` flag (periodic resend design, 2026-09-04, section
        6). Deliberately remains unchanged as the full restore path for
        `/resync` (`loxone.server`) and bridge startup (`cli.py`, directly
        after `seed_from_snapshot`) - both must restore EVERY virtual
        input after a Miniserver restart (spec 6.4), regardless of whether
        anyone has flagged the signal for the periodic timer. The periodic
        timer itself calls `resend_marked()` instead, see there.

        Only iterates the keys as a snapshot, but reads the value from
        `_last_values` freshly PER KEY, immediately before sending (review
        fix I4, 2026-09-02). The old code captured `(key, value)` pairs
        together as one snapshot and then waited - due to the debouncing
        in `UdpSender` - up to a few seconds for around 110 signals. A
        concurrent update during that time already wrote its new value
        into `_last_values` and sent it itself immediately, but the
        long-running resend then arrived again with its long-stale
        snapshot and overwrote the fresh value in Loxone back to the old
        one. The bug only heals itself on the next genuine update - but
        the trigger here is `/resync`, wired to the system-start block,
        and so fires exactly when someone is watching.
        """
        return await self._force_resend(list(self._last_values))

    async def resend_marked(self) -> int:
        """Like `resend_all`, but only for signals with `resend = true`
        (periodic resend design, 2026-09-04, section 6) - the counterpart
        to `resend_all`'s deliberate disregard of this flag. Only
        `_resend_loop` calls this method."""
        keys = self._store.resend_keys()
        return await self._force_resend(keys)

    async def _force_resend(self, keys: Sequence[str]) -> int:
        """Shared core of `resend_all`/`resend_marked` - see `resend_all`
        for why the value is re-read from `_last_values` PER KEY,
        immediately before sending (review fix I4)."""
        count = 0
        for key in keys:
            value = self._last_values.get(key)
            if value is None:
                # A key could theoretically have vanished between the
                # snapshot of the keys above and this access - never in
                # practice, but `_last_values` knows no deletion, only
                # overwriting. Safer to skip than to put a `None` value on
                # the wire.
                continue
            # Deliberately no `_notify_observers(...)` here (review fix
            # minor #3, 2026-09-02): a resend only sends values an
            # observer (e.g. the WebUI) has long since seen as current -
            # not a new value, so no new notification is needed either.
            await self._sender.send(key, value, force=True)
            count += 1
        return count

    async def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        self._tasks.append(asyncio.create_task(self._resend_loop()))

    async def stop(self) -> None:
        # Lower every currently high pulse BEFORE cancelling the
        # corresponding tasks - otherwise the cancellation skips the
        # `send(key, False)` in `_release_pulse` and the signal stays stuck
        # on 1 until the next event (review fix important #2).
        #
        # In its own try/finally (review fix M11, 2026-09-02): an already
        # dead sender (e.g. a `UdpSender` whose socket is already closed -
        # see `test_a_failing_resend_yields_502...` in test_server.py for
        # the same case with `/resync`) used to make this loop abort
        # unconditionally without the fix - and thereby SKIP EVERY
        # `task.cancel()` below and both `.clear()` calls. `stop()` is the
        # cleanup path itself; a failed send attempt must not cause
        # background tasks to keep running and both sets to never be
        # cleared.
        try:
            for key in list(self._pulses_high):
                # Deliberately no `_notify_observers(...)` here (review fix
                # minor #3, 2026-09-02): an observer has already seen this
                # pulse's high value (see `on_event`) - lowering it on
                # shutdown is pure cleanup for Loxone, not new information
                # for the WebUI.
                await self._sender.send(key, False)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "a pulse could not be lowered during shutdown - cleanup continues anyway"
            )
        finally:
            self._pulses_high.clear()

        tasks: list[asyncio.Task[None]] = [*self._tasks, *self._pulse_tasks]
        for task in tasks:
            task.cancel()
        # gather(..., return_exceptions=True) instead of a
        # contextlib.suppress(CancelledError) per task: the latter only
        # suppresses a cancellation, not an exception a task already died
        # from before `stop()` - that would be re-raised, aborting the loop
        # over the tasks and skipping `clear()` (review fix important #1,
        # collateral bug).
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._pulse_tasks.clear()

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                # No pulse without a Matter connection (8 September 2026):
                # the heartbeat is the watchdog input in Loxone. If it
                # keeps pulsing while the bridge is deaf, Loxone reports
                # "all well" - and that is exactly why an outage lasting
                # hours went unnoticed by everyone.
                #
                # This is something DIFFERENT from the failure case below:
                # if SENDING fails, the loop carries on and keeps pulsing,
                # so that the error becomes visible. If the CONNECTION is
                # missing, it falls silent, so that the error becomes
                # visible. Two states, two right answers - the difference
                # is what the pulse makes a statement about.
                #
                # The toggle of `_heartbeat_on` sits INSIDE the condition
                # on purpose: otherwise the phase would keep advancing
                # during the outage and the first pulse afterwards would
                # come out on the same value as the last one before it by
                # chance - for an edge-triggered watchdog that would be a
                # swallowed beat.
                if self._link_ok():
                    self._heartbeat_on = not self._heartbeat_on
                    await self._sender.send(HEARTBEAT_KEY, self._heartbeat_on, force=True)
                    # Also to the UI (2026-09-03). Previously the heartbeat
                    # went only to Loxone, and a bridge on which nothing is
                    # currently changing - a plug with no load reports neither
                    # current nor power - was indistinguishable in the live
                    # view from a crashed one: no value was moving, and no one
                    # could tell whether nothing was happening or nothing was
                    # arriving. The heartbeat is precisely the signal that
                    # answers this question; withholding it from the UI was a
                    # gap, not a decision.
                    self._notify_observers(HEARTBEAT_KEY, self._heartbeat_on)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Precisely the failure case the heartbeat is meant to
                # report must not silence it - otherwise the Loxone
                # watchdog freezes on the last value while nothing is
                # running anymore (review fix important #1).
                logger.exception("heartbeat could not be sent - loop continues")
            await asyncio.sleep(self._heartbeat_seconds)

    async def _resend_loop(self) -> None:
        """Periodically resends only the flagged signals (`resend_marked`)
        - unlike the one-off full restore at `/resync` and at bridge
        startup (`resend_all`, see there). The interval itself is a
        setting changeable at runtime via the WebUI
        (`store.resend_settings`, periodic resend design, section 4/6)
        rather than a constant fixed at startup: this clock reads it
        freshly on EVERY poll, every `resend_poll_seconds` (default 5s) -
        a change via the WebUI therefore takes effect within a few
        seconds, without a process restart.

        Two separate error paths (follow-up fix, final review): a failure
        while reading the interval (e.g. a briefly locked database) must
        NOT advance `last_resend` - otherwise a resend that is actually
        due would falsely look like it had just been done on the next
        poll. A failure in `resend_marked()` itself, by contrast, does
        advance `last_resend`, as before: a permanently broken sender
        should not retry on EVERY poll, but wait out a full interval
        again. Without splitting this into two try/except blocks, a
        failure while reading the interval would let the entire loop die
        unnoticed (`Runtime.stop()`'s
        `asyncio.gather(..., return_exceptions=True)` additionally
        swallows that on the next shutdown, without ever having logged
        anything)."""
        loop = asyncio.get_running_loop()
        last_resend = loop.time()
        while True:
            await asyncio.sleep(self._resend_poll_seconds)
            try:
                interval = self._store.resend_settings.get_interval_seconds()
                due = loop.time() - last_resend >= interval
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("could not read the resend interval - loop continues")
                continue
            if not due:
                continue
            try:
                await self.resend_marked()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("flagged resend failed - loop continues")
            last_resend = loop.time()
