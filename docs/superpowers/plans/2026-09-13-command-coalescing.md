# Command Coalescing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send at most one request per device at a time, and let a newer brightness or colour value replace one that is still waiting.

**Architecture:** A per-application `CommandGate` (`commands/coalesce.py`) keeps one queue and one worker per `(technology, address)`. Both device routes call `gate.run(calls)`, and both group routes pass `gate.run` to `dispatch_group`. The rule and its reasons are in `docs/superpowers/specs/2026-09-13-command-coalescing-design.md`; read Section 2 before either task.

**Tech Stack:** Python 3.12 asyncio, FastAPI, pytest with pytest-asyncio in auto mode.

## Global Constraints

- **Language and copy:** everything is in English, except `de:` values in `src/loxmatter/i18n/strings.yaml`. New files carry the 15-line GPL header, copied from `src/loxmatter/commands/fanout.py`. No plan task numbers and no TRANSITIONAL markers in `src/`.
- **Commits:** Conventional Commits, ending with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **Slots:** `(endpoint, "level")` for (8,0) and (8,4); `(endpoint, "colour")` for (768,6), (768,7) and (768,10). Every other call has no slot.
- **Supersession:**
  - A waiting request is superseded only when all its calls have slots and its slot set is a subset of the new request's slot set.
  - A request containing any call without a slot is never superseded and supersedes nothing.
- **Execution:** a running request is never interrupted, its calls run in order, and waiting requests run in arrival order.
- **Superseded requests:** `gate.run` returns `False`. The device routes answer 200; in a group, the member counts as reached.
- **Errors:** an error is raised to its own caller only, and the next request still runs.
- **One gate per application,** shared by `/cmd/{key}/{value}` and `POST /api/commands/{key}`.
- **Existing tests:** every existing test must keep passing unmodified, except where a test calls a signature this plan changes. None does: `build_control_router` and `dispatch_group` only gain optional arguments.
- **Tests:** run each part as a separate foreground command:
  - A1 `uv run pytest -q tests/api` (alone)
  - A2 `uv run pytest -q tests/auth tests/commands tests/devtools tests/diagnostics tests/export tests/loxone tests/matter tests/model tests/profiles tests/projectsync tests/radios tests/sources tests/zigbee`
  - B `uv run pytest -q tests/test_install_script.py tests/test_update_script.py tests/test_updater_script.py tests/test_updater_radios_script.py`
  - C `uv run pytest -q tests/test_build_arguments.py tests/test_cli.py tests/test_cli_language.py tests/test_compose_profiles.py tests/test_export_cli.py tests/test_i18n.py tests/test_otbr_watchdog.py tests/test_store_path.py tests/test_update_check.py tests/test_update_module.py tests/test_updater_entrypoint.py tests/test_updater_image.py tests/test_version.py`
  - The two known `main` failures in `tests/api/test_web.py` (`test_the_system_view_shows_the_running_version`, `test_the_update_card_offers_its_four_states_and_the_confirmation`) are not this plan's.
- **Checks:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run python scripts/check_language.py`.
- **Fault injection:** every protective test gets FAIL → restore → PASS, with `__pycache__` purged before each run.

---

### Task 1: The gate

**Files:**
- Create: `src/loxmatter/commands/coalesce.py`
- Create: `tests/commands/test_coalesce.py`

**Interfaces:**
- Produces:
  - `class CommandGate(invoke: Callable[[DeviceCall], Awaitable[None]])`, whose method `async run(calls: Sequence[DeviceCall]) -> bool`: `True` when the calls ran, `False` when a newer request superseded them. It raises whatever a call raised.
  - `slot_of(call: DeviceCall) -> tuple[int, str] | None`

- [ ] **Step 1: Write the failing tests.** Create `tests/commands/test_coalesce.py` with the GPL header and these tests:

```python
"""One request per device at a time - design 2026-09-13 (command coalescing)."""

from __future__ import annotations

import asyncio

import pytest

from loxmatter.commands.coalesce import CommandGate, slot_of
from loxmatter.sources import DeviceCall


def call(address: str, cluster_id: int, command_id: int, level: int | None = None, endpoint: int = 1) -> DeviceCall:
    payload: dict[str, object] = {} if level is None else {"level": level, "transitionTime": 0}
    return DeviceCall(
        technology="matter",
        address=address,
        endpoint=endpoint,
        cluster_id=cluster_id,
        command_id=command_id,
        payload=payload,
    )


def level(address: str, value: int, endpoint: int = 1) -> list[DeviceCall]:
    return [call(address, 8, 4, value, endpoint)]


def colour_and_level(address: str, value: int, endpoint: int = 1) -> list[DeviceCall]:
    return [call(address, 768, 6, endpoint=endpoint), call(address, 8, 4, value, endpoint)]


class SlowDevices:
    """An invoker whose calls block until the test releases them, and which
    records what ran and how many calls overlapped per address."""

    def __init__(self) -> None:
        self.ran: list[DeviceCall] = []
        self.active: dict[str, int] = {}
        self.max_active: dict[str, int] = {}
        self.release = asyncio.Event()
        self.failing: set[tuple[str, int, int]] = set()

    async def __call__(self, device_call: DeviceCall) -> None:
        address = device_call.address
        self.active[address] = self.active.get(address, 0) + 1
        self.max_active[address] = max(self.max_active.get(address, 0), self.active[address])
        try:
            await self.release.wait()
            if (address, device_call.cluster_id, device_call.command_id) in self.failing:
                raise RuntimeError(f"no answer from {address}")
            self.ran.append(device_call)
        finally:
            self.active[address] -= 1


async def _let_run() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def test_slots():
    assert slot_of(call("a", 8, 0)) == (1, "level")
    assert slot_of(call("a", 8, 4)) == (1, "level")
    for command_id in (6, 7, 10):
        assert slot_of(call("a", 768, command_id)) == (1, "colour")
    for pair in ((6, 0), (6, 1), (6, 2), (3, 0), (8, 1)):
        assert slot_of(call("a", *pair)) is None


async def test_one_device_never_runs_two_requests_at_once_but_two_devices_do():
    """Fault to prove it: call `invoke` directly in `run` without the queue -
    `max_active["lamp"]` becomes 2."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    tasks = [
        asyncio.ensure_future(gate.run(level("lamp", 10))),
        asyncio.ensure_future(gate.run([call("lamp", 6, 2)])),
        asyncio.ensure_future(gate.run(level("other", 20))),
    ]
    await _let_run()
    assert devices.active == {"lamp": 1, "other": 1}
    devices.release.set()
    assert await asyncio.gather(*tasks) == [True, True, True]
    assert devices.max_active == {"lamp": 1, "other": 1}


async def test_only_the_newest_waiting_value_is_sent():
    """Three brightness values while the lamp is busy with the first: the
    second is superseded, the third runs.

    Fault to prove it: never supersede - the lamp receives 10, 20 and 30."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    first = asyncio.ensure_future(gate.run(level("lamp", 10)))
    await _let_run()
    second = asyncio.ensure_future(gate.run(level("lamp", 20)))
    await _let_run()
    third = asyncio.ensure_future(gate.run(level("lamp", 30)))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(first, second, third) == [True, False, True]
    assert [c.payload["level"] for c in devices.ran] == [10, 30]


async def test_a_colour_value_supersedes_a_waiting_brightness_on_its_endpoint_only():
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run(level("lamp", 1)))
    await _let_run()
    same_endpoint = asyncio.ensure_future(gate.run(level("lamp", 20, endpoint=1)))
    other_endpoint = asyncio.ensure_future(gate.run(level("lamp", 30, endpoint=2)))
    await _let_run()
    newer = asyncio.ensure_future(gate.run(colour_and_level("lamp", 40, endpoint=1)))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(busy, same_endpoint, other_endpoint, newer) == [True, False, True, True]


async def test_toggle_is_never_superseded_and_keeps_its_order():
    """Fault to prove it: give (6, 2) a slot - the second toggle disappears."""
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run(level("lamp", 1)))
    await _let_run()
    first = asyncio.ensure_future(gate.run([call("lamp", 6, 2)]))
    second = asyncio.ensure_future(gate.run([call("lamp", 6, 2)]))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(busy, first, second) == [True, True, True]
    assert [(c.cluster_id, c.command_id) for c in devices.ran] == [(8, 4), (6, 2), (6, 2)]


async def test_a_waiting_request_with_an_unslotted_call_is_never_superseded():
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run(level("lamp", 1)))
    await _let_run()
    mixed = asyncio.ensure_future(gate.run([call("lamp", 6, 1), call("lamp", 8, 4, 50)]))
    await _let_run()
    newer = asyncio.ensure_future(gate.run(colour_and_level("lamp", 60)))
    await _let_run()
    devices.release.set()
    assert await asyncio.gather(busy, mixed, newer) == [True, True, True]


async def test_an_error_reaches_its_own_caller_and_the_next_request_still_runs():
    devices = SlowDevices()
    gate = CommandGate(devices)
    devices.failing.add(("lamp", 6, 1))
    failing = asyncio.ensure_future(gate.run([call("lamp", 6, 1)]))
    await _let_run()
    after = asyncio.ensure_future(gate.run([call("lamp", 6, 0)]))
    await _let_run()
    devices.release.set()
    with pytest.raises(RuntimeError):
        await failing
    assert await after is True


async def test_the_device_entry_is_dropped_once_its_queue_drains():
    """Fault to prove it: never delete the lane - `_lanes` keeps the key."""
    devices = SlowDevices()
    devices.release.set()
    gate = CommandGate(devices)
    assert await gate.run(level("lamp", 10)) is True
    await _let_run()
    assert gate._lanes == {}


async def test_a_cancelled_caller_does_not_stop_the_requests_behind_it():
    devices = SlowDevices()
    gate = CommandGate(devices)
    busy = asyncio.ensure_future(gate.run([call("lamp", 6, 1)]))
    await _let_run()
    impatient = asyncio.ensure_future(gate.run([call("lamp", 6, 2)]))
    behind = asyncio.ensure_future(gate.run([call("lamp", 6, 0)]))
    await _let_run()
    impatient.cancel()
    devices.release.set()
    assert await busy is True
    assert await behind is True
    assert [(c.cluster_id, c.command_id) for c in devices.ran] == [(6, 1), (6, 2), (6, 0)]


async def test_an_empty_request_runs_nothing_and_succeeds():
    gate = CommandGate(SlowDevices())
    assert await gate.run([]) is True


async def test_calls_for_two_devices_in_one_request_are_refused():
    gate = CommandGate(SlowDevices())
    with pytest.raises(ValueError):
        await gate.run([call("a", 6, 1), call("b", 6, 1)])
```

- [ ] **Step 2: Run the tests to see them fail.** `uv run pytest -q tests/commands/test_coalesce.py` should fail with `ModuleNotFoundError: No module named 'loxmatter.commands.coalesce'`.

- [ ] **Step 3: Implement `src/loxmatter/commands/coalesce.py`.** Start with the GPL header.

```python
"""One request per device at a time, and only the newest value waits.

Design 2026-09-13 (command coalescing). A Loxone slider sends a value about
once a second without waiting for the answer; sent as they came, those
values overlapped on the same lamps and queued up on the Thread radio until
the OpenThread agent gave up (13 September 2026, 19:27-19:28 UTC). Here each
device gets one queue: a request runs only when the previous one for that
device has finished, and a brightness or colour value that is still waiting
is replaced by a newer one of the same kind.

A *request* is the list of calls one command produces for one device - the
unit whose order matters, because the colour call has to reach a lamp before
the brightness call that switches it on (`commands/translate.py`,
`to_device_calls`). The gate never splits one.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from loxmatter.sources import DeviceCall

__all__ = ["CommandGate", "slot_of"]

Invoker = Callable[[DeviceCall], Awaitable[None]]
Slot = tuple[int, str]

_CLUSTER_LEVEL = 8
_CLUSTER_COLOUR = 768
_LEVEL_COMMANDS = frozenset({0, 4})
# MoveToHueAndSaturation, MoveToColor and MoveToColorTemperature: colour and
# white are the same output of a lamp, so a newer one of either replaces an
# older one of either.
_COLOUR_COMMANDS = frozenset({6, 7, 10})


def slot_of(call: DeviceCall) -> Slot | None:
    """What a call sets, if it sets a value a newer call may replace.

    On, off and toggle have no slot: two toggles are not one toggle, and a
    lamp told "off" then "on" must end up on."""
    if call.cluster_id == _CLUSTER_LEVEL and call.command_id in _LEVEL_COMMANDS:
        return (call.endpoint, "level")
    if call.cluster_id == _CLUSTER_COLOUR and call.command_id in _COLOUR_COMMANDS:
        return (call.endpoint, "colour")
    return None


def _slots(calls: Sequence[DeviceCall]) -> frozenset[Slot] | None:
    slots: set[Slot] = set()
    for device_call in calls:
        slot = slot_of(device_call)
        if slot is None:
            return None
        slots.add(slot)
    return frozenset(slots)


def _consume(future: asyncio.Future[bool]) -> None:
    """Marks an outcome as seen, so a request whose caller went away does not
    log "exception was never retrieved" when its call fails."""
    if not future.cancelled():
        future.exception()


@dataclass(eq=False)
class _Request:
    calls: tuple[DeviceCall, ...]
    slots: frozenset[Slot] | None
    outcome: asyncio.Future[bool]


@dataclass(eq=False)
class _Lane:
    waiting: list[_Request] = field(default_factory=list)
    worker: asyncio.Future[None] | None = None


class CommandGate:
    """Serialises requests per `(technology, address)`.

    One per application: `build_app` creates it and hands it to both command
    routes, so a Loxone value and a web UI click for the same lamp share one
    queue."""

    def __init__(self, invoke: Invoker) -> None:
        self._invoke = invoke
        self._lanes: dict[tuple[str, str], _Lane] = {}

    async def run(self, calls: Sequence[DeviceCall]) -> bool:
        """Runs `calls` on their device after every request already running
        or waiting for it. Returns `False` when a newer request replaced
        these calls before they started, and raises what a call raised.

        A caller that is cancelled while waiting does not take its request
        out of the queue: the value was sent to the bridge, and a Miniserver
        dropping the connection does not mean it no longer wants it."""
        if not calls:
            return True
        key = (calls[0].technology, calls[0].address)
        if any((c.technology, c.address) != key for c in calls):
            raise ValueError("one request must address one device")
        request = _Request(
            calls=tuple(calls),
            slots=_slots(calls),
            outcome=asyncio.get_running_loop().create_future(),
        )
        request.outcome.add_done_callback(_consume)
        lane = self._lanes.setdefault(key, _Lane())
        if request.slots is not None:
            for waiting in list(lane.waiting):
                if waiting.slots is not None and waiting.slots <= request.slots:
                    lane.waiting.remove(waiting)
                    waiting.outcome.set_result(False)
        lane.waiting.append(request)
        if lane.worker is None:
            lane.worker = asyncio.ensure_future(self._drain(key, lane))
        return await asyncio.shield(request.outcome)

    async def _drain(self, key: tuple[str, str], lane: _Lane) -> None:
        """Runs one device's queue until it is empty.

        Nothing is awaited between the last emptiness check and the `finally`
        below, so a request appended while the last one runs is always
        picked up by this loop, never stranded between two workers."""
        try:
            while lane.waiting:
                request = lane.waiting.pop(0)
                try:
                    for device_call in request.calls:
                        await self._invoke(device_call)
                except asyncio.CancelledError:
                    request.outcome.cancel()
                    raise
                except BaseException as exc:
                    request.outcome.set_exception(exc)
                else:
                    request.outcome.set_result(True)
        except asyncio.CancelledError:
            for waiting in lane.waiting:
                waiting.outcome.cancel()
            lane.waiting.clear()
            raise
        finally:
            lane.worker = None
            if not lane.waiting and self._lanes.get(key) is lane:
                del self._lanes[key]
```

- [ ] **Step 4: Run the tests and the checks.** Run `uv run pytest -q tests/commands/test_coalesce.py`, then `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy`. Everything must pass. If a test's expected list disagrees with the rule in spec Section 2, the test is wrong: fix the test and say so. Never bend the rule to fit a test.

- [ ] **Step 5: Fault-inject.** Apply each fault below on its own, purge `__pycache__`, run the named test and watch it FAIL, then restore and watch it PASS.
  1. `run` calls `self._invoke` directly, without a queue. Named test: `test_one_device_never_runs_two_requests_at_once_but_two_devices_do`.
  2. Remove the supersession loop. Named test: `test_only_the_newest_waiting_value_is_sent`.
  3. `slot_of` gives (6, 2) the slot `(endpoint, "switch")`. Named test: `test_toggle_is_never_superseded_and_keeps_its_order`.
  4. Delete the `del self._lanes[key]` line. Named test: `test_the_device_entry_is_dropped_once_its_queue_drains`.
  5. Drop the `asyncio.shield`. Named test: `test_a_cancelled_caller_does_not_stop_the_requests_behind_it`. If it does not fail, report that plainly: a surviving mutation means that case is unmeasured, not that the code is wrong.

- [ ] **Step 6: Commit.** Stage `src/loxmatter/commands/coalesce.py` and `tests/commands/test_coalesce.py`, and commit with the message `feat(commands): send one request per device at a time, keeping only the newest value`, followed by a body that explains the 13 September burst in two sentences.

---

### Task 2: Wire the gate into every command route

**Files:**
- Modify: `src/loxmatter/loxone/server.py`: `build_app` creates the gate; the `/cmd` device path; `_group_command`.
- Modify: `src/loxmatter/api/control.py`: `build_control_router` takes an optional gate; the device path; `_execute_group_command`.
- Modify: `src/loxmatter/commands/fanout.py`: `dispatch_group(..., *, run=None)` and `_run_member`.
- Modify: `CHANGELOG.md` (`[Unreleased]`) and `deploy/testhost/README.md` (the watchdog cron line).
- Test: `tests/commands/test_fanout.py`, `tests/api/test_group_control.py`, and the test module for the single-device `/cmd` path (find it with `grep -rln '"/cmd/' tests/api tests/loxone`).

**Interfaces:**
- Consumes `CommandGate`, whose `run(calls) -> bool` comes from Task 1.
- Produces:
  - `dispatch_group(plans, invoke, *, run: Callable[[Sequence[DeviceCall]], Awaitable[bool]] | None = None) -> GroupOutcome`
  - `build_control_router(store, invoke, values, *, gate: CommandGate | None = None)`

- [ ] **Step 1: Write the fan-out test first** (append to `tests/commands/test_fanout.py`):

```python
async def test_a_superseded_member_counts_as_reached():
    """Fault to prove it: treat a `False` from `run` as a failure - the
    outcome then names the member."""
    target = on_target(1, 21, "Lamp")
    plans = plan_group_calls(group_command(6, 1, "on", False), [target], "1")

    async def run(calls):
        return False

    outcome = await dispatch_group(plans, invoke=None, run=run)
    assert outcome.failed == []
```

`group_command` and `on_target` already exist in that file from earlier branches. If a name differs, use the real one.

- [ ] **Step 2: Implement it in `fanout.py`.** `_run_member(plan, invoke, run)` does `await run(plan.calls)` when `run` is not `None`, and otherwise keeps its existing loop. `dispatch_group` passes `run` through. Say in the docstring that the group routes pass `CommandGate.run`, and that a superseded member returns normally, so it is neither failed nor unconfigured. Also widen the `invoke` type to `Callable[[DeviceCall], Awaitable[None]] | None`, because the test passes `None` when `run` is given. Assert `invoke is not None` in the branch that uses it.

- [ ] **Step 3: Wire the routes.**
  - In `build_app` (`loxone/server.py`), create `gate = CommandGate(invoke)` once, before `build_control_router` is included. Pass `gate=gate` to it.
  - In `/cmd/{key}/{value}`, replace `for call in calls: await invoke(call)` with `await gate.run(calls)`. The surrounding `except` clauses stay as they are.
  - In `_group_command`, call `dispatch_group(plans, invoke, run=gate.run)`.
  - In `api/control.py`, `build_control_router(..., *, gate=None)` does `gate = gate or CommandGate(invoke)` at the top. Make the same two replacements there.

- [ ] **Step 4: Write the route tests.**
  - **Group burst** (append to `tests/api/test_group_control.py`). Build an `api`-like fixture whose `invoke` sleeps briefly and records `(address, start, end)`, or use an `asyncio.Event` as the tests in `test_coalesce.py` do. Fire five `/cmd/<g1 level_onoff key>/{10,20,30,40,50}` concurrently with `asyncio.gather`. Assert:
    - every response is 200;
    - per address, no two recorded calls overlap in time;
    - each member's last recorded level call carries level 127 (50 %);
    - each member received fewer than five level calls.
    
    Fault to prove it: in `_group_command`, use `dispatch_group(plans, invoke)` without `run`. Overlap then appears, or all five values arrive.
  - **Shared gate.** A `/cmd` device request and a `POST /api/commands/<same key>` for the same device, fired together while `invoke` blocks, never overlap. Fault to prove it: `build_app` passes no gate to `build_control_router`, so each route builds its own and the two overlap.

- [ ] **Step 5: Docs.**
  - **CHANGELOG `[Unreleased]`**, in that section's voice. Add one bullet: a dragged slider no longer queues up commands. Each lamp gets one command at a time, and only the newest value waits. This takes load off the Thread radio, which could previously give up under a burst.
  - **`deploy/testhost/README.md`**, watchdog section. Change the cron line to `* * * * * /home/pi/matter-loxone/scripts/otbr-watchdog.sh >> /home/pi/otbr-watchdog.log 2>&1`. Replace the sentence about five minutes with: a check every minute keeps an outage to about a minute. Keep the existing explanation that the script does not restart in a loop, and make it consistent with the new interval. The script itself is not changed.

- [ ] **Step 6: Full verification.** Run the four suite parts and the four checks, each in the foreground. Fault-inject both route tests from Step 4 and the fan-out test from Step 1.

- [ ] **Step 7: Commit.** Two commits:
  - `feat(commands): route every device and group command through one gate per application`, covering the source and tests.
  - `docs: say that a dragged slider no longer queues commands, and check Thread every minute`.
