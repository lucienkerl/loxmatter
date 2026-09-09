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

import asyncio
import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.runtime import HEARTBEAT_KEY, Runtime
from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot, SignalKind, SignalRef
from loxmatter.model.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


class FakeSender:
    """Remembers what was sent, instead of actually sending it."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, object, bool]] = []

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        self.sent.append((key, value, force))
        return True

    async def close(self) -> None:
        return None

    def keys(self) -> list[str]:
        return [k for k, _, _ in self.sent]

    def __iter__(self) -> Iterator[str]:
        # Ruff's SIM118 ("key in dict instead of key in dict.keys()")
        # assumes __contains__/__iter__ exist, otherwise `key in sender`
        # raises a TypeError instead of the desired shortcut - both added
        # here, so `in`/`for` behave on `FakeSender` exactly as on `.keys()`.
        return iter(self.keys())

    def __contains__(self, key: str) -> bool:
        return key in self.keys()


class MutatingSender(FakeSender):
    """Like FakeSender, but calls `mutate` once on the first call WITH
    `force=True` - stands in for a concurrent update that arrives while a
    `resend_all()` is running (review fix I4). Deliberately reacts only to
    `force=True`: only `resend_all()` sets that, and a regular
    `on_attribute()` call during test setup (which also calls `send()`)
    should not trigger the mutation prematurely - and thus at the wrong
    point."""

    def __init__(self, mutate: Callable[[], None]) -> None:
        super().__init__()
        self._mutate = mutate
        self._mutated = False

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        if force and not self._mutated:
            self._mutated = True
            self._mutate()
        return await super().send(key, value, force=force)


class FlakySender(FakeSender):
    """Like FakeSender, but raises a RuntimeError on the nth call - for
    tests that want to reproduce a failed send attempt."""

    def __init__(self, fail_on_call: int) -> None:
        super().__init__()
        self._fail_on_call = fail_on_call
        self._calls = 0

    async def send(self, key: str, value: object, *, force: bool = False) -> bool:
        self._calls += 1
        if self._calls == self._fail_on_call:
            raise RuntimeError("Sender kaputt")
        return await super().send(key, value, force=force)


@pytest.fixture
def environment(tmp_path):
    """Two devices in a store: the plug delivers the attribute for the
    scaling tests (2/144/4), the switch delivers the event for the pulse
    tests (1/59/1) — the plug has no switch cluster and cannot deliver an
    event."""
    store = Store(tmp_path / "t.sqlite")

    plug_raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    plug_snap = NodeSnapshot.from_raw(plug_raw["node_id"], plug_raw)
    device_id = store.register_device(plug_snap)
    store.register_signals(device_id, plug_snap)
    store.register_commands(device_id, extract_commands(plug_snap), plug_snap.node_id)

    button_raw = json.loads((FIXTURES / "ikea_bilresa_button.json").read_text(encoding="utf-8"))
    button_snap = NodeSnapshot.from_raw(button_raw["node_id"], button_raw)
    button_device_id = store.register_device(button_snap)
    store.register_signals(button_device_id, button_snap)

    sender = FakeSender()
    runtime = Runtime(store, sender)
    yield runtime, sender, store, device_id, button_device_id
    store.close()


async def test_attribute_change_becomes_a_scaled_datagram(environment):
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert sender.sent == [(f"d{device_id}_2_voltage", pytest.approx(230.0), False)]


async def test_unmappable_attribute_is_not_sent(environment):
    """Spec 6.6: lists never become a datagram."""
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "0/29/1", [29, 31, 40])
    assert sender.sent == []


async def test_unknown_path_is_ignored_not_raised(environment):
    """A device can report attributes that were not included at export time."""
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "9/9999/9", 1)
    assert sender.sent == []


async def test_event_sends_a_pulse_and_a_counter(environment):
    """Spec 6.3: the pulse produces the edge, the counter survives a lost packet."""
    runtime, sender, _, _, button_device_id = environment
    await runtime.on_event(button_device_id, "1/59/1")
    keys = sender.keys()
    assert f"d{button_device_id}_1_press" in keys
    assert f"d{button_device_id}_1_press_n" in keys


async def test_pulse_falls_back_to_zero(environment):
    runtime, sender, _, _, button_device_id = environment
    await runtime.on_event(button_device_id, "1/59/1")
    await asyncio.sleep(Runtime.PULSE_MILLISECONDS / 1000 + 0.1)
    pulses = [(k, v) for k, v, _ in sender.sent if k == f"d{button_device_id}_1_press"]
    assert pulses == [
        (f"d{button_device_id}_1_press", True),
        (f"d{button_device_id}_1_press", False),
    ]


async def test_counter_increases_monotonically(environment):
    runtime, sender, _, _, button_device_id = environment
    for _ in range(3):
        await runtime.on_event(button_device_id, "1/59/1")
    counters = [v for k, v, _ in sender.sent if k == f"d{button_device_id}_1_press_n"]
    assert counters == [1, 2, 3]


async def test_online_signal_is_sent(environment):
    runtime, sender, _, device_id, _ = environment
    await runtime.set_online(device_id, False)
    assert (f"d{device_id}_online", False, False) in sender.sent


async def test_resend_forces_every_known_value(environment):
    """Spec 6.4: debouncing must be bypassed after a Miniserver restart."""
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    sender.sent.clear()
    count = await runtime.resend_all()
    assert count == 1
    assert sender.sent[0][2] is True


async def test_resend_sends_the_freshest_value_not_a_stale_snapshot(environment):
    """Review fix I4, 2026-09-02: `resend_all()` captured key AND value
    together as a snapshot and then waited - slowed down by debouncing in
    the real `UdpSender` - per key. An update that arrived for an OTHER,
    not-yet-processed key during that wait was then overwritten by the
    delayed resend with its long-stale value. This test simulates that:
    `MutatingSender` writes a new value for `current_key` on the first
    `send()` (for `voltage_key`) - a key that `resend_all()` still has
    ahead of it."""
    _, _, store, device_id, _ = environment
    current_key = f"d{device_id}_2_current"

    def mutate() -> None:
        runtime._last_values[current_key] = 555.0

    sender = MutatingSender(mutate)
    runtime = Runtime(store, sender)
    await runtime.on_attribute(device_id, "2/144/4", 230000)  # fills voltage_key
    await runtime.on_attribute(device_id, "2/144/5", 100)  # fills current_key, in the dict after
    sender.sent.clear()

    await runtime.resend_all()

    sent_currents = [v for k, v, _ in sender.sent if k == current_key]
    assert sent_currents[-1] == 555.0


async def test_resend_of_an_empty_runtime_sends_nothing(environment):
    runtime, _, _, _, _ = environment
    assert await runtime.resend_all() == 0


async def test_resend_marked_only_sends_flagged_signals(environment):
    runtime, sender, store, device_id, _ = environment
    voltage_key = f"d{device_id}_2_voltage"
    current_key = f"d{device_id}_2_current"
    await runtime.on_attribute(device_id, "2/144/4", 230000)  # voltage
    await runtime.on_attribute(device_id, "2/144/5", 100)  # current
    store.set_resend(voltage_key, True)
    sender.sent.clear()

    count = await runtime.resend_marked()

    assert count == 1
    assert sender.keys() == [voltage_key]
    assert sender.sent[0][2] is True
    assert current_key not in sender


async def test_resend_marked_of_no_flagged_signals_sends_nothing(environment):
    runtime, sender, _, device_id, _ = environment
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    sender.sent.clear()

    assert await runtime.resend_marked() == 0
    assert sender.sent == []


async def test_resend_all_ignores_the_resend_flag_and_sends_everything(environment):
    """/resync and bridge startup rely on `resend_all()` as complete state
    restoration (spec 6.4) - the `resend` flag (periodic resend design,
    section 6) must NOT restrict that, or most virtual inputs would stay
    at their default value after a Miniserver restart."""
    runtime, sender, store, device_id, _ = environment
    voltage_key = f"d{device_id}_2_voltage"
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    assert store.signal_by_key(voltage_key).resend is False  # default value
    sender.sent.clear()

    count = await runtime.resend_all()

    assert count == 1
    assert sender.keys() == [voltage_key]


async def test_resend_loop_never_sends_an_unmarked_signal(environment, monkeypatch):
    _, sender, store, device_id, _ = environment
    marked_key = f"d{device_id}_2_voltage"
    unmarked_key = f"d{device_id}_2_current"
    store.set_resend(marked_key, True)
    monkeypatch.setattr(store.resend_settings, "get_interval_seconds", lambda: 0.01)

    runtime = Runtime(store, sender, resend_poll_seconds=0.02)
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    await runtime.on_attribute(device_id, "2/144/5", 100)
    sender.sent.clear()

    await runtime.start()
    await asyncio.sleep(0.09)
    await runtime.stop()

    forced = {k for k, _, forced in sender.sent if forced}
    assert marked_key in forced
    assert unmarked_key not in forced


async def test_resend_loop_reacts_to_a_lowered_interval_without_a_restart(environment, monkeypatch):
    """A change via the WebUI (`PATCH /api/settings/resend-interval`) takes
    effect within a few seconds, without a process restart (design, section
    6). `interval` is a mutable dict instead of a plain variable, because
    the monkeypatch lambda below must read it via closure after the test
    has already changed its value."""
    _, sender, store, device_id, _ = environment
    key = f"d{device_id}_2_voltage"
    store.set_resend(key, True)
    interval = {"seconds": 10.0}
    monkeypatch.setattr(store.resend_settings, "get_interval_seconds", lambda: interval["seconds"])

    runtime = Runtime(store, sender, resend_poll_seconds=0.02)
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    sender.sent.clear()

    await runtime.start()
    try:
        await asyncio.sleep(0.09)
        # `runtime.start()` also starts the heartbeat loop on the side
        # (here with the default `heartbeat_seconds=30.0`), which sends
        # once even before its own first sleep (see `_heartbeat_loop`) -
        # independent of the resend interval tested here. Without the
        # filter, "bridge_alive" would incorrectly make this check fail,
        # even though the resend itself (what this test is about) has not
        # run at all yet.
        assert [
            k for k in sender if k != "bridge_alive"
        ] == []  # 10s interval (simulated) is nowhere near up yet

        interval["seconds"] = 0.01
        await asyncio.sleep(0.09)
    finally:
        await runtime.stop()

    assert key in sender


async def test_resend_loop_survives_a_failing_interval_read(environment, monkeypatch):
    """An error while reading the interval (e.g. a briefly locked database)
    must not let the loop die unnoticed (final review, important #1)."""
    _, sender, store, device_id, _ = environment
    key = f"d{device_id}_2_voltage"
    store.set_resend(key, True)

    calls = {"n": 0}

    def flaky_get_interval() -> float:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Datenbank kurzzeitig gesperrt")
        return 0.01

    monkeypatch.setattr(store.resend_settings, "get_interval_seconds", flaky_get_interval)

    runtime = Runtime(store, sender, resend_poll_seconds=0.02)
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    sender.sent.clear()

    await runtime.start()
    await asyncio.sleep(0.09)
    await runtime.stop()

    assert calls["n"] >= 2  # the loop survived the first error and kept polling
    assert key in sender  # and actually resent afterward, once reading works again


async def test_heartbeat_toggles(environment):
    """Spec 6.5: bridge_alive covers "container dead" and "network gone" alike."""
    _, sender, store, _, _ = environment
    runtime = Runtime(store, sender, heartbeat_seconds=0.05)
    await runtime.start()
    await asyncio.sleep(0.16)
    await runtime.stop()
    values = [v for k, v, _ in sender.sent if k == "bridge_alive"]
    assert len(values) >= 2
    assert values[0] != values[1]


async def test_heartbeat_survives_a_failed_send(environment):
    """Review fix important #1: per the module docstring, the heartbeat
    covers "container dead" and "network gone" alike - a single failed
    send attempt must therefore not end the watchdog loop, or the Loxone
    watchdog freezes on the last value while the bridge has long since
    gone silent."""
    _, _, store, _, _ = environment
    sender = FlakySender(fail_on_call=2)
    runtime = Runtime(store, sender, heartbeat_seconds=0.05)
    await runtime.start()
    await asyncio.sleep(0.22)
    await runtime.stop()
    values = [v for k, v, _ in sender.sent if k == "bridge_alive"]
    # The second call fails (see FlakySender) - without the fix, the loop
    # would die there and no further values would ever arrive.
    assert len(values) >= 3


async def test_stop_completes_even_if_a_task_already_died(environment):
    """Review fix important #1, companion bug: contextlib.suppress(CancelledError)
    only suppresses a cancellation, not any other exception that a task
    already died from before `stop()`. The old implementation let `stop()`
    abort with exactly that exception, skipping the clearing of the task
    list in the process."""
    runtime, _, _, _, _ = environment

    async def boom() -> None:
        raise RuntimeError("task already died before stop()")

    dead_task = asyncio.create_task(boom())
    await asyncio.sleep(0)  # let the task actually die
    assert dead_task.done()
    runtime._tasks.append(dead_task)

    await runtime.start()
    await runtime.stop()  # must not fail on the already-dead task

    assert runtime._tasks == []
    assert runtime._pulse_tasks == set()


async def test_stop_lowers_an_in_flight_pulse(environment):
    """Review fix important #2: a cancellation during the pulse sleep
    otherwise skips the `send(key, False)` - the digital signal would stay
    stuck at 1 until the next event on this key."""
    runtime, sender, _, _, button_device_id = environment
    await runtime.on_event(button_device_id, "1/59/1")
    await runtime.stop()
    key = f"d{button_device_id}_1_press"
    values = [v for k, v, _ in sender.sent if k == key]
    assert values[-1] is False


async def test_stop_completes_even_if_lowering_a_pulse_fails(environment):
    """Review fix M11, 2026-09-02: before the fix, the loop that lowers
    every currently-high pulse ran unguarded, ahead of the task
    cancellation. If a send attempt failed there (e.g. an already-closed
    `UdpSender`), the whole method aborted right there - EVERY
    `task.cancel()` and both `.clear()` calls were skipped. `stop()` is
    itself the cleanup path; a failed send attempt must not take it down
    too."""
    runtime, _, _, _, button_device_id = environment
    await runtime.on_event(button_device_id, "1/59/1")
    assert runtime._pulses_high  # the pulse is still True

    runtime._sender = FlakySender(fail_on_call=1)  # the next send() fails
    await runtime.start()

    await runtime.stop()  # must not fail on the failing sender

    assert runtime._pulses_high == set()
    assert runtime._tasks == []
    assert runtime._pulse_tasks == set()


async def test_invalidate_index_lets_a_newly_registered_signal_through(environment, monkeypatch):
    """Review fix important #3: `Store.register_signals` can add a new
    signal to an already-indexed device at any time (e.g. after a firmware
    update). Without `invalidate_index`, this signal stays invisible to
    the runtime, because `_signal_for` reads from the database only once
    per device."""
    runtime, sender, store, device_id, _ = environment
    plug_raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    plug_snap = NodeSnapshot.from_raw(plug_raw["node_id"], plug_raw)

    new_ref = SignalRef(9, 1234, 5, SignalKind.ATTRIBUTE)
    key = f"d{device_id}_9_c1234_a5"

    def extended_extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]:
        return [*extract_signals(snapshot), new_ref]

    # First-time indexing by the runtime - the path does not exist yet.
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.sent == []

    monkeypatch.setattr("loxmatter.model.store.extract_signals", extended_extract_signals)
    store.register_signals(device_id, plug_snap)

    # The runtime's cache does not yet know about the new signal.
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.sent == []

    runtime.invalidate_index(device_id)
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.keys() == [key]


def _plug_snapshot() -> NodeSnapshot:
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


async def test_seed_from_snapshot_populates_cache_without_sending(environment):
    """Live run from 2026-09-02 (spec 6.4): `resend_all()` sends nothing at
    startup, because `_last_values` is empty - otherwise a value only lands
    there via a subscription, which reports *changing* values. A plug with
    no load, for instance, never reports a changing voltage. Seeding fills
    the cache directly from the current device state, but itself sends
    nothing - see the docstring of `seed_from_snapshot`. 110 attribute
    signals plus 1 online signal (review fix C1, 2026-09-02)."""
    runtime, sender, _, device_id, _ = environment

    seeded = await runtime.seed_from_snapshot([_plug_snapshot()])

    assert seeded == 111
    assert len(runtime._last_values) == 111
    assert runtime._last_values[f"d{device_id}_online"] is True
    assert sender.sent == []


async def test_seed_from_snapshot_seeds_an_unavailable_node_as_offline(environment):
    """Review fix C1, 2026-09-02: the core of the bug. `start_listening()`
    fills the initial node cache WITHOUT firing NODE_ADDED, and
    NODE_UPDATED only comes with a node data message - the only writer of
    `d<id>_online` (`set_online`) would therefore never run after a bridge
    startup, and the key would stay stuck at its `DefVal="0"` (reads in
    Loxone as "unreachable"). Seeding must therefore take availability
    from the snapshot itself."""
    runtime, sender, _, device_id, _ = environment
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    raw = dict(raw)
    raw["available"] = False
    offline_snap = NodeSnapshot.from_raw(raw["node_id"], raw)

    await runtime.seed_from_snapshot([offline_snap])

    assert runtime._last_values[f"d{device_id}_online"] is False
    assert sender.sent == []


async def test_resend_after_seeding_sends_every_seeded_value(environment):
    runtime, sender, _, device_id, _ = environment
    await runtime.seed_from_snapshot([_plug_snapshot()])

    count = await runtime.resend_all()

    assert count == 111
    assert len(sender.sent) == 111
    assert all(force for _, _, force in sender.sent)
    assert (f"d{device_id}_online", True, True) in sender.sent


async def test_seed_from_snapshot_skips_attribute_without_a_stored_signal(environment):
    """A snapshot can contain attributes that were not included at export
    time (e.g. a new cluster after a firmware update that has not been
    exported yet) - this must not abort the seeding with an error, but is
    skipped exactly as with `on_attribute`."""
    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    raw = dict(raw)
    attributes = dict(raw["attributes"])
    attributes["9/9999/9"] = 42
    raw["attributes"] = attributes
    plug_snap = NodeSnapshot.from_raw(raw["node_id"], raw)
    runtime, _, _, device_id, _ = environment

    seeded = await runtime.seed_from_snapshot([plug_snap])

    assert seeded == 111
    assert f"d{device_id}_9_c9999_a9" not in runtime._last_values


async def test_seeding_twice_does_not_double_anything(environment):
    runtime, sender, _, _, _ = environment
    snap = _plug_snapshot()

    await runtime.seed_from_snapshot([snap])
    await runtime.seed_from_snapshot([snap])

    assert len(runtime._last_values) == 111
    count = await runtime.resend_all()
    assert count == 111
    assert len(sender.sent) == 111


async def test_seed_from_snapshot_skips_an_unknown_node_without_aborting(environment):
    """A snapshot can report a node that `Store` does not (yet) know - such
    as a device that has never been exported. This must not abort startup;
    only this node is skipped, all others are still seeded."""
    runtime, sender, _, _, _ = environment
    unknown = NodeSnapshot(
        node_id=999_999,
        vendor_name="",
        product_name="",
        unique_id="",
        attributes={"2/144/4": 230000},
    )

    seeded = await runtime.seed_from_snapshot([unknown, _plug_snapshot()])

    assert seeded == 111
    assert sender.sent == []


async def test_last_values_for_returns_only_that_devices_keys(environment):
    """For the devices API (task 2, phase 5): `last_values_for` must not
    also collect values of another device - not even when that device's
    device_id, as a digit sequence, contains this one's device_id as a
    prefix."""
    runtime, _, _, device_id, button_device_id = environment
    await runtime.on_attribute(device_id, "2/144/4", 230000)
    await runtime.set_online(button_device_id, True)

    values = runtime.last_values_for(device_id)

    assert values == {f"d{device_id}_2_voltage": pytest.approx(230.0)}
    assert f"d{button_device_id}_online" not in values


async def test_last_values_for_is_empty_before_anything_is_known(environment):
    runtime, _, _, device_id, _ = environment
    assert runtime.last_values_for(device_id) == {}


async def test_on_node_snapshot_registers_a_new_signal_and_lets_it_through(
    environment, monkeypatch
):
    """The core of the catch-up: a path the store did not yet know at
    commissioning time must afterward have a signal row AND pass through
    the runtime's signal cache. This is exactly where the test catches a
    forgotten `invalidate_index` call - without it, `register_signals`
    does create the row, but `_signal_for` stays at its once-loaded state,
    and every update to the new path goes nowhere for the rest of the
    process."""
    runtime, sender, _, device_id, _ = environment
    new_ref = SignalRef(9, 1234, 5, SignalKind.ATTRIBUTE)
    key = f"d{device_id}_9_c1234_a5"

    def extended_extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]:
        return [*extract_signals(snapshot), new_ref]

    # First let it be indexed, as in operation: the runtime has already
    # seen the device once before the new path shows up.
    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.sent == []

    monkeypatch.setattr("loxmatter.model.store.extract_signals", extended_extract_signals)
    await runtime.on_node_snapshot(device_id, _plug_snapshot())

    await runtime.on_attribute(device_id, "9/1234/5", 1)
    assert sender.keys() == [key]


async def test_on_node_snapshot_seeds_the_values_without_sending(environment):
    """Same reasoning as for `seed_from_snapshot`: the cache fills up,
    nothing is sent. A freshly created signal has no virtual input in
    Loxone yet anyway - that only comes into being with the export of the
    template."""
    runtime, sender, _, device_id, _ = environment

    await runtime.on_node_snapshot(device_id, _plug_snapshot())

    assert runtime._last_values[f"d{device_id}_online"] is True
    assert len(runtime._last_values) == 111
    assert sender.sent == []


async def test_on_node_snapshot_keeps_the_key_and_the_export_flag(environment):
    """`register_signals` is explicitly built for repeated calls (see its
    docstring there). If the catch-up reassigned keys or reset `exported`,
    every repeat call would destroy the wiring in the Loxone
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


async def test_on_node_snapshot_invalidates_before_seeding_a_stale_cache(environment, monkeypatch):
    """The four `test_on_node_snapshot_*` tests above only prove that
    `invalidate_index` gets called at all - not that it runs BEFORE the
    seeding. If you swap `self.invalidate_index(device_id)` with the
    seeding passage below it, all four stay green: none of them indexes
    the device with the new path in the cache BEFORE the
    `on_node_snapshot` call.

    This test does exactly that: `on_attribute` indexes the device
    beforehand (the path does not exist yet at this point, the value is
    dropped, but the cache is populated) - which makes it already stale by
    the time of the following `on_node_snapshot` call. If invalidation
    runs after the seeding, `_cache_attribute` reads the old state via
    `_signal_for`, finds no signal for the new path there, and drops the
    supplied value - exactly the dash state that `on_node_snapshot` is
    supposed to eliminate."""
    runtime, _, _, device_id, _ = environment
    new_ref = SignalRef(9, 1234, 5, SignalKind.ATTRIBUTE)
    key = f"d{device_id}_9_c1234_a5"

    def extended_extract_signals(snapshot: NodeSnapshot) -> list[SignalRef]:
        return [*extract_signals(snapshot), new_ref]

    # First-time indexing by the runtime - the path does not exist yet, the
    # value is dropped, but the device is indexed afterward (and the cache
    # thereby stale for anything that gets added next).
    await runtime.on_attribute(device_id, "9/1234/5", 1)

    monkeypatch.setattr("loxmatter.model.store.extract_signals", extended_extract_signals)

    raw = json.loads((FIXTURES / "ikea_grillplats_plug.json").read_text(encoding="utf-8"))
    raw = dict(raw)
    attributes = dict(raw["attributes"])
    attributes["9/1234/5"] = 1
    raw["attributes"] = attributes
    snapshot = NodeSnapshot.from_raw(raw["node_id"], raw)

    await runtime.on_node_snapshot(device_id, snapshot)

    assert key in runtime._last_values


async def test_the_heartbeat_stays_silent_without_a_matter_link(tmp_path):
    """Without a Matter connection `bridge_alive` must NOT pulse.

    The heartbeat is the watchdog input in Loxone. If it keeps pulsing
    while the bridge is deaf, Loxone reports "all well" - that is exactly
    what happened on 8 September 2026, and it is why the outage went
    unnoticed by everyone for hours.
    """
    store = Store(tmp_path / "s.sqlite")
    sender = FakeSender()
    runtime = Runtime(store, sender, heartbeat_seconds=0.01, link_ok=lambda: False)
    await runtime.start()
    await asyncio.sleep(0.05)
    await runtime.stop()

    assert HEARTBEAT_KEY not in sender


async def test_the_heartbeat_pulses_with_a_matter_link(tmp_path):
    """Counter-check - otherwise the test above only proves nothing happens."""
    store = Store(tmp_path / "s.sqlite")
    sender = FakeSender()
    runtime = Runtime(store, sender, heartbeat_seconds=0.01, link_ok=lambda: True)
    await runtime.start()
    await asyncio.sleep(0.05)
    await runtime.stop()

    assert HEARTBEAT_KEY in sender


async def test_the_heartbeat_falls_silent_when_the_link_drops(tmp_path):
    """`link_ok` is asked afresh on EVERY beat, not once at construction.

    Were `cli.serve()` to pass `client.connected` instead of
    `lambda: client.connected` by accident - a property, hence a bool
    evaluated once - the heartbeat would hang forever on the state of the
    moment of startup and would never fall silent. `mypy --strict` now
    rejects that as a type error; this test covers the same gap on the
    behavioural side.
    """
    store = Store(tmp_path / "s.sqlite")
    sender = FakeSender()
    alive = [True]
    runtime = Runtime(store, sender, heartbeat_seconds=0.01, link_ok=lambda: alive[0])
    await runtime.start()
    await asyncio.sleep(0.05)
    assert HEARTBEAT_KEY in sender

    alive[0] = False
    before = len([k for k in sender if k == HEARTBEAT_KEY])
    await asyncio.sleep(0.05)
    after = len([k for k in sender if k == HEARTBEAT_KEY])
    await runtime.stop()

    assert after == before
