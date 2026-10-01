# Firmware Update Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Update all" in the Device updates card queues every selected device; the bridge installs them one after another without a browser.

**Architecture:** A `queued_at` column on `firmware_status` (schema 16) holds the queue; a halt reason lives in the `setting` table. A new `FirmwareQueue` (`src/loxmatter/firmware/queue.py`) runs one background task that feeds the unchanged `FirmwareJobs` one device at a time. `FirmwareService` owns it, the API exposes three routes, the WebUI gets a button, a checklist dialog and two bands.

**Tech Stack:** Python 3.12, FastAPI, SQLite, pytest (asyncio auto mode), Alpine.js, node-based binding harness in `tests/api/test_web.py`.

**Spec:** `docs/superpowers/specs/2026-10-01-firmware-update-queue-design.md`

## Global Constraints

- Everything in English: code, comments, test names, commit messages (Conventional Commits, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`).
- Every user-visible string goes through `i18n.t(...)` with an `en` and a `de` value in `src/loxmatter/i18n/strings.yaml`.
- Migrations are additive only: never drop a column.
- One install at a time; `FirmwareJobs` (`src/loxmatter/firmware/job.py`) is not changed.
- New test files get names no other test module has.
- Every new source file starts with the GPL header the other files carry (copy it from `src/loxmatter/firmware/check.py`, lines 1-15).
- The full suite takes ~11 minutes; run only the files named in a task, foreground. Before finishing a task run: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run python scripts/check_language.py`.
- Paths are relative to the worktree `/Users/lucienkerl/Development/matter-loxone/.claude/worktrees/batch-device-updates-25581c`.

---

### Task 1: Store — `queued_at` and the halt reason

**Files:**
- Modify: `src/loxmatter/model/store.py` (`_SCHEMA_VERSION = 15` at line ~180 → 16; `_SCHEMA` `CREATE TABLE IF NOT EXISTS firmware_status` gains `queued_at TEXT`; new `_migrate_to_v16`; `_MIGRATIONS[16]`)
- Modify: `src/loxmatter/model/firmware_status_store.py`
- Modify: `src/loxmatter/model/firmware_settings_store.py`
- Modify: tests that assert schema 15: `tests/model/test_firmware_store_schema.py:27`, `tests/model/test_store_identity.py:149,177`, `tests/model/test_store_migration.py:257,272,281,288` → 16 (rename `test_schema_version_is_15` to `test_schema_version_is_16`)
- Create: `tests/model/test_firmware_queue_store.py`

**Interfaces — Produces:**
- `FirmwareStatus.queued_at: str | None` (new last field of the dataclass)
- `FirmwareStatusStore.enqueue(device_ids: Sequence[int], queued_at: str) -> int` — sets `queued_at` on each listed device whose row has none yet (creating the row if missing); returns how many it set.
- `FirmwareStatusStore.queued() -> list[int]` — device ids with `queued_at` set, ordered by `queued_at`, then `device_id`.
- `FirmwareStatusStore.dequeue(device_id: int) -> None`
- `FirmwareStatusStore.clear_queue() -> None`
- `FirmwareSettingsStore.get_queue_halted_reason() -> str | None` and `set_queue_halted_reason(reason: str | None) -> None` — key `firmware.queue_halted_reason`; `None` deletes the row.

- [ ] **Step 1: Write the failing tests** in `tests/model/test_firmware_queue_store.py`:

```python
import sqlite3
import sys
from pathlib import Path

from loxmatter.model.store import Store

sys.path.insert(0, str(Path(__file__).parents[1] / "firmware"))
from firmware_fakes import register


def _store(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    a, _ = register(store, "ikea_kajplats_cws_lamp.json")
    b, _ = register(store, "ikea_bilresa_button.json")
    return store, a, b


def test_enqueue_orders_by_time_then_id(tmp_path):
    store, a, b = _store(tmp_path)
    assert store.firmware_status.enqueue([b, a], "2026-10-01T10:00:00Z") == 2
    assert store.firmware_status.queued() == sorted([a, b])
    assert store.firmware_status.get(a).queued_at == "2026-10-01T10:00:00Z"


def test_enqueue_keeps_an_earlier_place(tmp_path):
    store, a, b = _store(tmp_path)
    store.firmware_status.enqueue([b], "2026-10-01T10:00:00Z")
    assert store.firmware_status.enqueue([a, b], "2026-10-01T11:00:00Z") == 1
    assert store.firmware_status.queued() == [b, a]
    assert store.firmware_status.get(b).queued_at == "2026-10-01T10:00:00Z"


def test_dequeue_and_clear(tmp_path):
    store, a, b = _store(tmp_path)
    store.firmware_status.enqueue([a, b], "t")
    store.firmware_status.dequeue(a)
    assert store.firmware_status.queued() == [b]
    store.firmware_status.clear_queue()
    assert store.firmware_status.queued() == []


def test_a_deleted_device_leaves_the_queue(tmp_path):
    store, a, b = _store(tmp_path)
    store.firmware_status.enqueue([a, b], "t")
    store.delete_device(a)
    assert store.firmware_status.queued() == [b]


def test_halt_reason_round_trip(tmp_path):
    store, _, _ = _store(tmp_path)
    assert store.firmware_settings.get_queue_halted_reason() is None
    store.firmware_settings.set_queue_halted_reason("api.firmware.queue_halted_disconnected")
    assert store.firmware_settings.get_queue_halted_reason() == "api.firmware.queue_halted_disconnected"
    store.firmware_settings.set_queue_halted_reason(None)
    assert store.firmware_settings.get_queue_halted_reason() is None


def test_a_v15_database_gains_queued_at(tmp_path):
    path = tmp_path / "v15.sqlite"
    store = Store(path)
    a, _ = register(store, "ikea_kajplats_cws_lamp.json")
    store.firmware_status.record_check(a, None, "t0")
    store.close()
    db = sqlite3.connect(str(path))
    db.executescript(
        "CREATE TABLE fs_old AS SELECT device_id, checked_at, check_error, offer_version,"
        " offer_version_string, offer_min_applicable, offer_max_applicable, offer_notes_url,"
        " offer_source, job_state, job_progress, job_started_at, job_changed_at, job_error"
        " FROM firmware_status;"
        " DROP TABLE firmware_status;"
        " ALTER TABLE fs_old RENAME TO firmware_status;"
        " PRAGMA user_version = 15;"
    )
    db.commit()
    db.close()
    store = Store(path)
    assert store.firmware_status.get(a).checked_at == "t0"
    assert store.firmware_status.get(a).queued_at is None
    assert store.firmware_status.enqueue([a], "t1") == 1
```

Check the store's method for deleting a device (`grep -n "def delete_device\|def remove_device" src/loxmatter/model/store.py`) and use its real name. If the recreated table in the last test loses the foreign key, that is fine for this test.

- [ ] **Step 2: Run** `uv run pytest tests/model/test_firmware_queue_store.py -v` — expect failures (`AttributeError: ... enqueue`).

- [ ] **Step 3: Implement.**

`store.py`: add `queued_at TEXT` as the last column of the `firmware_status` `CREATE TABLE` in `_SCHEMA`; bump `_SCHEMA_VERSION` to 16; add after `_migrate_to_v15`:

```python
def _migrate_to_v16(db: sqlite3.Connection) -> None:
    """Adds `firmware_status.queued_at`, the update queue (design
    2026-10-01, section 3). A set value means the device is queued."""
    _add_column_if_missing(db, "firmware_status", "queued_at", "TEXT")
```

and `16: _migrate_to_v16,` in `_MIGRATIONS`.

`firmware_status_store.py`: add `queued_at: str | None` to `FirmwareStatus`, fill it in `_as_status` with `row["queued_at"]`, and add:

```python
    def enqueue(self, device_ids: Sequence[int], queued_at: str) -> int:
        """Queues each device not queued yet; one already queued keeps its place."""
        added = 0
        for device_id in device_ids:
            self._ensure_row(device_id)
            cursor = self._db.execute(
                "UPDATE firmware_status SET queued_at = ?"
                " WHERE device_id = ? AND queued_at IS NULL",
                (queued_at, device_id),
            )
            added += cursor.rowcount
        self._db.commit()
        return added

    def queued(self) -> list[int]:
        rows = self._db.execute(
            "SELECT device_id FROM firmware_status WHERE queued_at IS NOT NULL"
            " ORDER BY queued_at, device_id"
        ).fetchall()
        return [int(row["device_id"]) for row in rows]

    def dequeue(self, device_id: int) -> None:
        self._db.execute(
            "UPDATE firmware_status SET queued_at = NULL WHERE device_id = ?", (device_id,)
        )
        self._db.commit()

    def clear_queue(self) -> None:
        self._db.execute("UPDATE firmware_status SET queued_at = NULL")
        self._db.commit()
```

(`from collections.abc import Sequence`.)

`firmware_settings_store.py`: update the module docstring to mention the second key, and add:

```python
_QUEUE_HALTED_KEY = "firmware.queue_halted_reason"

    def get_queue_halted_reason(self) -> str | None:
        """The i18n key of why the update queue halted; `None` while it runs."""
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_QUEUE_HALTED_KEY,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def set_queue_halted_reason(self, reason: str | None) -> None:
        if reason is None:
            self._db.execute("DELETE FROM setting WHERE key = ?", (_QUEUE_HALTED_KEY,))
        else:
            self._db.execute(
                "INSERT INTO setting (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_QUEUE_HALTED_KEY, reason),
            )
        self._db.commit()
```

Update the schema-15 assertions listed under Files to 16.

- [ ] **Step 4: Run** `uv run pytest tests/model -v` — all pass.

- [ ] **Step 5: Commit** — `feat(store): keep a firmware update queue in firmware_status`

---

### Task 2: `FirmwareQueue`

**Files:**
- Create: `src/loxmatter/firmware/queue.py`
- Modify: `src/loxmatter/i18n/strings.yaml` (four `api.firmware.queue_*` keys, next to the other `api.firmware.*` keys)
- Create: `tests/firmware/test_firmware_queue.py`

**Interfaces:**
- Consumes: Task 1's store methods; `FirmwareJobs.start(device_id, software_version)`, `FirmwareJobs.wait()`, `FirmwareJobs.running_device_id`, and the errors `FirmwareBusyError`, `FirmwareUnsupportedError`, `OfferChangedError`, `DeviceOfflineError` from `loxmatter.firmware.job`; `UnknownDeviceError` from `loxmatter.model.store`; `states.FAILED`, `states.INTERRUPTED`.
- Produces:

```python
class FirmwareQueue:
    def __init__(self, store: Store, jobs: FirmwareJobs,
                 source_for: Callable[[], FirmwareSource | None], *,
                 now: Callable[[], str] = now_iso) -> None
    def start(self) -> None            # starts the task (idempotent) and wakes it
    async def stop(self) -> None       # cancels the task
    def enqueue(self, device_ids: Sequence[int]) -> int   # only devices with an offer
    def resume(self) -> None
    def clear(self) -> None
    @property
    def active(self) -> bool           # queued devices and not halted, or a device the queue started is still installing
    async def wait_idle(self) -> None  # tests: returns when the loop has nothing left to do
```

Strings (`api.firmware.*`):

```yaml
api.firmware.queue_offline_at_turn:
  en: "Offline when its turn in the update queue came"
  de: "War offline, als es in der Update-Warteschlange an der Reihe war"
api.firmware.queue_halted_disconnected:
  en: "the connection to matter-server was lost during an update"
  de: "die Verbindung zum matter-server ist während eines Updates abgebrochen"
api.firmware.queue_halted_unsupported:
  en: "matter-server no longer offers firmware updates"
  de: "der matter-server bietet keine Firmware-Updates mehr an"
api.firmware.queue_halted_error:
  en: "an unexpected error stopped it; the bridge log has the details"
  de: "ein unerwarteter Fehler hat sie angehalten; Details stehen im Log der Bridge"
```

- [ ] **Step 1: Write the failing tests** in `tests/firmware/test_firmware_queue.py`. Build on the `Clock` pattern of `tests/firmware/test_firmware_job.py` (copy the class; it is not exported) and on `firmware_fakes` (`FakeFirmwareSource`, `idle_facts`, `register`, `KAJPLATS_OFFER`, `BILRESA_OFFER`). Use a real `FirmwareJobs` with the fake clock so jobs finish in fake time:

```python
def _setup(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    lamp_id, lamp = register(store, "ikea_kajplats_cws_lamp.json")
    button_id, button = register(store, "ikea_bilresa_button.json")
    store.firmware_status.record_check(lamp_id, KAJPLATS_OFFER, "t0")
    store.firmware_status.record_check(button_id, BILRESA_OFFER, "t0")
    source = FakeFirmwareSource()
    source.facts[lamp] = idle_facts(16842752, "1.1.0")
    source.facts[button] = idle_facts(17301509, "1.8.5")
    clock = Clock()
    jobs = FirmwareJobs(store, lambda: source, clock=clock, sleep=clock.sleep,
                        now=lambda: "now", timing=JobTiming())
    queue = FirmwareQueue(store, jobs, lambda: source, now=lambda: "q")
    return store, source, clock, jobs, queue, (lamp_id, lamp), (button_id, button)
```

A device "finishes" when the source reports the target version: make `FakeFirmwareSource.start_update` drive that by scripting it. Simplest: wrap `source.start_update` in the test so it schedules `source.finish(address, version, text)` a few fake seconds later via `clock.script.append((clock.t + 10, ...))`. Put that helper in the test file:

```python
def _auto_finish(source, clock, versions):
    """Every started install reports its new version 10 fake seconds later."""
    original = source.start_update

    async def start_update(address, software_version):
        await original(address, software_version)
        clock.script.append(
            (clock.t + 10, lambda: source.finish(address, software_version, versions[address]))
        )
        clock.script.sort(key=lambda entry: entry[0])

    source.start_update = start_update
```

Tests (each ends with `await queue.stop(); await jobs.stop()`):

1. `test_devices_are_installed_one_after_another` — enqueue `[button_id, lamp_id]`, `queue.start()`, `await queue.wait_idle()`; `source.started == [(lamp, KAJPLATS...), (button, BILRESA...)]` in queue order (lower device id first — assert against the order `store.firmware_status.queued()` had right after enqueue, captured before `start`); both offers dropped; `store.firmware_status.queued() == []`; `queue.active is False`.
2. `test_enqueue_ignores_devices_without_an_offer` — drop the button's offer (`store.firmware_status.drop_offer(button_id)`); `queue.enqueue([lamp_id, button_id]) == 1`.
3. `test_a_device_that_already_has_the_version_is_skipped` — lamp facts already at 16908288; nothing started for the lamp, no `job_error`, queue empty.
4. `test_an_offline_device_fails_and_the_next_one_runs` — lamp `available=False`; lamp ends `job_state == "failed"` with `job_error == i18n.t("api.firmware.queue_offline_at_turn")`; the button was installed.
5. `test_a_failed_install_does_not_stop_the_queue` — `source.fail_start = RuntimeError("boom")` only for the first call (wrap like `_auto_finish`); first device `failed`, second installed.
6. `test_an_interrupted_install_halts_the_queue` — script `source.connected = False` at fake t=4 for the first device; afterwards first device `interrupted`, the second still in `queued()`, `store.firmware_settings.get_queue_halted_reason() == "api.firmware.queue_halted_disconnected"`, `queue.active is False`, nothing started for the second.
7. `test_resume_continues_after_a_halt` — after test 6's state, `source.connected = True`, `queue.resume()`, `await queue.wait_idle()`: the second device installed, halt reason `None`.
8. `test_unsupported_halts_and_keeps_the_device` — `source.supported = False` before start: halt reason `api.firmware.queue_halted_unsupported`, both devices still queued, nothing started.
9. `test_clear_empties_the_queue_and_the_halt` — enqueue, set a halt reason, `queue.clear()`: `queued() == []`, halt reason `None`.
10. `test_the_queue_is_picked_up_from_the_store` — enqueue with one `FirmwareQueue`, never start it; build a second `FirmwareQueue` on the same store and jobs, `start()`, `wait_idle()`: both installed.
11. `test_the_queue_waits_for_an_install_already_running` — `jobs.start(button_id, BILRESA_OFFER.software_version)` directly, then enqueue `[lamp_id, button_id]` (button has an offer still, so both queued), `start()`, `wait_idle()`: `source.started` begins with the button, the lamp follows, and the button is not started twice (its offer is gone after it finished, so the queue skips it).
12. `test_enqueue_clears_a_halt` — set a halt reason, `enqueue([lamp_id])` → reason `None`.

- [ ] **Step 2: Run** `uv run pytest tests/firmware/test_firmware_queue.py -v` — fails (module missing).

- [ ] **Step 3: Implement** `src/loxmatter/firmware/queue.py`:

```python
"""Installing several firmware updates one after another (design 2026-10-01).

The queue lives in the store (`firmware_status.queued_at`), so it outlives a
restart of loxmatter. This class only drives it: one task takes the first
queued device, hands it to the unchanged `FirmwareJobs`, waits for the end,
and takes the next. A failed device does not stop it; a lost link to
matter-server does, because every further device would fail the same way."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence

from loxmatter import i18n
from loxmatter.firmware import states
from loxmatter.firmware.job import (
    DeviceOfflineError,
    FirmwareBusyError,
    FirmwareJobs,
    FirmwareUnsupportedError,
    OfferChangedError,
)
from loxmatter.model.store import Store, UnknownDeviceError
from loxmatter.sources.firmware import FirmwareSource
from loxmatter.timestamps import now_iso

logger = logging.getLogger(__name__)

HALTED_DISCONNECTED = "api.firmware.queue_halted_disconnected"
HALTED_UNSUPPORTED = "api.firmware.queue_halted_unsupported"
HALTED_ERROR = "api.firmware.queue_halted_error"


class FirmwareQueue:
    def __init__(
        self,
        store: Store,
        jobs: FirmwareJobs,
        source_for: Callable[[], FirmwareSource | None],
        *,
        now: Callable[[], str] = now_iso,
    ) -> None:
        self._store = store
        self._jobs = jobs
        self._source_for = source_for
        self._now = now
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        # The device this queue handed to `FirmwareJobs` last; while it still
        # installs, the queue counts as active although nothing is queued.
        self._current: int | None = None

    @property
    def active(self) -> bool:
        if self._store.firmware_settings.get_queue_halted_reason() is not None:
            return False
        if self._store.firmware_status.queued():
            return True
        return self._current is not None and self._jobs.running_device_id == self._current

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run())
        self._kick()

    async def stop(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                if not task.cancelled():
                    raise

    def enqueue(self, device_ids: Sequence[int]) -> int:
        with_offer = []
        for device_id in device_ids:
            status = self._store.firmware_status.get(device_id)
            if status is not None and status.offer is not None:
                with_offer.append(device_id)
        added = self._store.firmware_status.enqueue(with_offer, self._now())
        if with_offer:
            self._store.firmware_settings.set_queue_halted_reason(None)
            self._kick()
        return added

    def resume(self) -> None:
        self._store.firmware_settings.set_queue_halted_reason(None)
        self._kick()

    def clear(self) -> None:
        self._store.firmware_status.clear_queue()
        self._store.firmware_settings.set_queue_halted_reason(None)

    async def wait_idle(self) -> None:
        await self._idle.wait()

    def _kick(self) -> None:
        self._idle.clear()
        self._wake.set()

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            try:
                await self._drain()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("the firmware update queue failed")
                self._halt(HALTED_ERROR)
            if not self._wake.is_set():
                self._idle.set()

    def _halt(self, reason: str) -> None:
        self._store.firmware_settings.set_queue_halted_reason(reason)

    async def _drain(self) -> None:
        while True:
            if self._store.firmware_settings.get_queue_halted_reason() is not None:
                return
            queued = self._store.firmware_status.queued()
            if not queued:
                return
            if self._jobs.running_device_id is not None:
                await self._jobs.wait()
                continue
            source = self._source_for()
            if source is None or not source.firmware_supported():
                self._halt(HALTED_UNSUPPORTED)
                return
            await self._install(source, queued[0])

    async def _install(self, source: FirmwareSource, device_id: int) -> None:
        status = self._store.firmware_status.get(device_id)
        try:
            device = self._store.device(device_id)
        except UnknownDeviceError:
            self._store.firmware_status.dequeue(device_id)
            return
        offer = None if status is None else status.offer
        facts = source.firmware_facts(device.address)
        if offer is None or (
            facts is not None
            and facts.software_version is not None
            and facts.software_version >= offer.software_version
        ):
            # Nothing left to install: reached already, or the offer is gone.
            self._store.firmware_status.dequeue(device_id)
            return
        if facts is None or not facts.available:
            self._offline(device_id)
            return
        try:
            self._jobs.start(device_id, offer.software_version)
        except FirmwareBusyError:
            # A single install started in the same instant; the device stays
            # first and the loop waits for that install.
            return
        except FirmwareUnsupportedError:
            self._halt(HALTED_UNSUPPORTED)
            return
        except DeviceOfflineError:
            self._offline(device_id)
            return
        except (OfferChangedError, UnknownDeviceError):
            self._store.firmware_status.dequeue(device_id)
            return
        self._store.firmware_status.dequeue(device_id)
        self._current = device_id
        try:
            await self._jobs.wait()
        finally:
            self._current = None
        ended = self._store.firmware_status.get(device_id)
        if ended is not None and ended.job_state == states.INTERRUPTED:
            self._halt(HALTED_DISCONNECTED)

    def _offline(self, device_id: int) -> None:
        self._store.firmware_status.dequeue(device_id)
        self._store.firmware_status.end_job(
            device_id, states.FAILED, i18n.t("api.firmware.queue_offline_at_turn"), self._now()
        )
```

Notes for the implementer: `FirmwareJobs.start` raises `FirmwareBusyError` when a job runs; in `_drain` the running check comes first, so the busy branch only covers the race. Check that `_install`'s ordering matches the spec (4): skip → offline → start. Prefer keeping the code above; change it only when a test proves it wrong, and say so in the report.

- [ ] **Step 4: Run** `uv run pytest tests/firmware -v` — all pass. Then the checks from Global Constraints.

- [ ] **Step 5: Commit** — `feat(firmware): install queued updates one after another`

---

### Task 3: Service, API and startup

**Files:**
- Modify: `src/loxmatter/firmware/service.py` (own a `FirmwareQueue`; queue data in the overview and in `device_out`)
- Modify: `src/loxmatter/api/models.py` (`FirmwareQueueOut`, `FirmwareQueueIn`, new fields)
- Modify: `src/loxmatter/api/firmware.py` (three routes)
- Modify: `src/loxmatter/cli.py` (start after `resume_all`, stop before `jobs.stop()`)
- Modify: `src/loxmatter/i18n/strings.yaml` (`api.firmware.queue_nothing_to_install`)
- Modify: `docs/superpowers/specs/2026-09-30-device-firmware-updates-design.md` — one line under 9.1's table: "The update queue adds three routes; see [the queue design](2026-10-01-firmware-update-queue-design.md)."
- Modify: `CHANGELOG.md` — under `## [Unreleased]`: in "Before you update" change "rises from 14 to 15" to "rises from 14 to 16"; in "Added", after the firmware entry: "- **Update every device at once.** "Update all" in the Device updates card lists every device with an update, all ticked. The bridge installs them one after another on its own; the browser can be closed. A device that fails does not stop the others; a lost connection to matter-server pauses the queue until you continue it."
- Create: `tests/api/test_firmware_queue_api.py`

**Interfaces:**
- Consumes: `FirmwareQueue` (Task 2).
- Produces:

```python
class FirmwareQueueOut(BaseModel):
    model_config = ConfigDict(frozen=True)
    device_ids: list[int]
    active: bool
    # Already translated; None while the queue is not halted.
    halted_reason: str | None

class FirmwareQueueIn(BaseModel):
    device_ids: list[int]
```

`FirmwareOverviewOut.queue: FirmwareQueueOut`; `FirmwareDeviceOut.queue_position: int | None` (1-based). `FirmwareService.queue: FirmwareQueue` (constructor gains `queue: FirmwareQueue | None = None`, built as `FirmwareQueue(store, self.jobs, self.source_for)`). `FirmwareService.device_out(device, queued: list[int] | None = None)` — when `None`, reads `self._store.firmware_status.queued()`; `overview()` reads it once and passes it.

Routes (in `build_firmware_router`):

```python
    @router.post("/firmware/queue", status_code=status.HTTP_202_ACCEPTED)
    async def enqueue(body: FirmwareQueueIn) -> FirmwareOverviewOut:
        _require_supported()
        if firmware.queue.enqueue(body.device_ids) == 0 and not set(body.device_ids) & set(
            store.firmware_status.queued()
        ):
            raise HTTPException(
                status_code=409, detail=i18n.t("api.firmware.queue_nothing_to_install")
            )
        firmware.queue.start()
        return firmware.overview()

    @router.post("/firmware/queue/resume")
    async def resume_queue() -> FirmwareOverviewOut:
        _require_supported()
        firmware.queue.resume()
        firmware.queue.start()
        return firmware.overview()

    @router.delete("/firmware/queue")
    async def clear_queue() -> FirmwareOverviewOut:
        firmware.queue.clear()
        return firmware.overview()
```

String:

```yaml
api.firmware.queue_nothing_to_install:
  en: "None of the selected devices has an update to install."
  de: "Keines der ausgewählten Geräte hat ein Update zum Installieren."
```

The overview translates the stored key: `halted_reason=None if key is None else i18n.t(key)`.

- [ ] **Step 1: Write the failing tests** in `tests/api/test_firmware_queue_api.py`, with a fixture modelled on `firmware_api` in `tests/api/test_firmware_api.py` (copy its imports and `authenticate` usage; register the lamp and the BILRESA button, both with offers recorded via `store.firmware_status.record_check`). Teardown: `await firmware.queue.stop()` before `firmware.jobs.stop()`. Tests:

1. `POST /api/firmware/queue` with both ids → 202; body `queue.device_ids` has both or the first already moved on (assert `set(body["queue"]["device_ids"]) | {body["updating_device_id"]} >= {lamp_id, button_id} - {None}` is too loose — instead stop the source from progressing: set `source.connected = True` and leave facts idle so the first job stays in `transferring`; then assert `body["queue"]["active"] is True` and that `GET /api/firmware` lists the second device with `queue_position == 1` after the first has started (poll with `await asyncio.sleep(0)` a few times).
2. `POST /api/firmware/queue` with only a device without an offer → 409 with the `en` text of `api.firmware.queue_nothing_to_install`.
3. `POST` while unsupported (`source.supported = False`) → 409 `api.firmware.fail_unsupported` text.
4. `DELETE /api/firmware/queue` → 200, `queue.device_ids == []`.
5. Halted: `store.firmware_settings.set_queue_halted_reason("api.firmware.queue_halted_disconnected")` → `GET /api/firmware` has `queue.halted_reason` equal to the `en` text and `queue.active is False`; `POST /api/firmware/queue/resume` → `halted_reason is None`.
6. A German check for one detail: set the locale the way `tests/api/test_language.py` does and assert the `de` text of `queue_nothing_to_install`.

Also add a test in `tests/test_cli.py` only if that file already tests `resume_all` wiring (`grep -n resume_all tests/test_cli.py`); otherwise skip — the wiring is two lines and the WebUI run on the Pi covers it.

- [ ] **Step 2: Run** `uv run pytest tests/api/test_firmware_queue_api.py -v` — fails.

- [ ] **Step 3: Implement** the service, models, routes, strings, CLI wiring:

In `cli.py`, right after the `try: firmware.jobs.resume_all() ... except` block:

```python
        try:
            # Design 2026-10-01: a queue that outlived the restart goes on.
            firmware.queue.start()
        except Exception:
            logger.exception("Starting the firmware update queue failed")
```

and in the `finally`, before the `await firmware.jobs.stop()` block:

```python
        try:
            # Before the jobs: the queue must not start the next device while
            # they shut down. Its devices stay queued in the store.
            await firmware.queue.stop()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("The firmware update queue could not be stopped cleanly on shutdown")
```

Also make sure every other test fixture that builds `FirmwareService` and stops `jobs`/`checker` in teardown keeps working (`grep -rn "firmware.jobs.stop" tests`); add `await firmware.queue.stop()` there when the queue could have been started (only the new API tests start it, so likely none).

- [ ] **Step 4: Run** `uv run pytest tests/api/test_firmware_queue_api.py tests/api/test_firmware_api.py tests/firmware -v`, then the checks from Global Constraints.

- [ ] **Step 5: Commit** — `feat(api): queue firmware updates for several devices`

---

### Task 4: WebUI

**Files:**
- Modify: `src/loxmatter/web/index.html` (card at ~line 2588; tile pill at ~1514; a new `<dialog x-ref="firmwareQueueModal">` after the firmware dialog ending ~line 3800)
- Modify: `src/loxmatter/web/app.js` (firmware section ~5005-5200; state ~1185-1199; logout cleanup ~1581-1594)
- Modify: `src/loxmatter/web/style.css` (only if the new list needs it; reuse `.firmware-*`, `.banner`, `.expert-modal`)
- Modify: `src/loxmatter/i18n/strings.yaml` (`web.firmware.*` keys below)
- Modify: `tests/api/test_web.py` (append tests near `test_the_firmware_parts_are_delivered`, ~line 16638)

**Interfaces:**
- Consumes: `GET /api/firmware` → `queue: {device_ids, active, halted_reason}`, per device `queue_position`; `POST /api/firmware/queue {device_ids}`, `POST /api/firmware/queue/resume`, `DELETE /api/firmware/queue`.
- Produces (app.js state/methods): `firmwareQueueSelection: []`, `firmwareQueueModalBackdropMousedown: false`, `firmwareQueueBusy: false`; `firmwareQueueCandidates()`, `openFirmwareQueueModal()`, `closeFirmwareQueueModal()`, `startFirmwareQueue()`, `resumeFirmwareQueue()`, `clearFirmwareQueue()`, `firmwareQueueBusyText()`.

Strings (`en` / `de`):

| Key | en | de |
| --- | --- | --- |
| `web.firmware.update_all` | `Update all ({count})` | `Alle aktualisieren ({count})` |
| `web.firmware.queue_heading` | `Update all devices` | `Alle Geräte aktualisieren` |
| `web.firmware.queue_intro` | `These devices have an update. Untick any you want to leave as they are.` | `Diese Geräte haben ein Update. Entferne den Haken bei allen, die bleiben sollen, wie sie sind.` |
| `web.firmware.queue_offline` | `offline – skipped if it still is when its turn comes` | `offline – wird übersprungen, wenn es dann noch offline ist` |
| `web.firmware.queue_background` | `The updates run one after another on the bridge. You can close the browser.` | `Die Updates laufen nacheinander auf der Bridge. Du kannst den Browser schließen.` |
| `web.firmware.queue_install` | `Install {count} updates` | `{count} Updates installieren` |
| `web.firmware.queue_running` | `Updating {device} · {state} · {count} more queued` | `Aktualisiere {device} · {state} · {count} weitere in der Warteschlange` |
| `web.firmware.queue_waiting` | `{count} updates queued` | `{count} Updates in der Warteschlange` |
| `web.firmware.queue_cancel_rest` | `Cancel the rest` | `Rest abbrechen` |
| `web.firmware.queue_halted` | `The update queue has stopped: {reason}` | `Die Update-Warteschlange ist angehalten: {reason}` |
| `web.firmware.queue_resume` | `Continue` | `Fortsetzen` |
| `web.firmware.queue_clear` | `Clear the queue` | `Warteschlange leeren` |
| `web.firmware.state_queued` | `Queued (#{position})` | `In Warteschlange ({position}.)` |
| `web.firmware.pill_queued` | `Update queued` | `Update in Warteschlange` |

Behaviour:

- **Card header:** a second button before "Check for updates now":
  `<button @click="openFirmwareQueueModal()" :disabled="!firmware?.supported || firmwareQueueCandidates().length === 0 || firmware?.queue.active || firmware?.queue.device_ids.length > 0" x-text="t('web.firmware.update_all', { count: firmwareQueueCandidates().length })"></button>`
- **`firmwareQueueCandidates()`** returns `(this.firmware?.devices ?? []).filter((row) => row.state === "available" && row.offer && row.queue_position === null)`.
- **Running band** (above the filter chips): `x-show="firmware?.queue.active"`, class `banner`, text `firmwareQueueBusyText()` plus a button `t('web.firmware.queue_cancel_rest')` → `clearFirmwareQueue()`. `firmwareQueueBusyText()`: with `updating_device_id` set and its row found → `queue_running` with `device` = row label, `state` = `firmwareStateText(row)`, `count` = `queue.device_ids.length`; otherwise `queue_waiting` with `count`.
- **Halted band:** `x-show="firmware?.queue.halted_reason"`, class `banner warn`, text `t('web.firmware.queue_halted', { reason: firmware.queue.halted_reason })`, buttons Continue → `resumeFirmwareQueue()` and Clear → `clearFirmwareQueue()`.
- **State column:** `firmwareStateText(row)` returns `t('web.firmware.state_queued', { position: row.queue_position })` when `row.queue_position != null` and the row is not running (check `FIRMWARE_ACTIVE_STATES` first).
- **Row install button:** add `|| firmware.queue.active || firmware.queue.device_ids.length > 0` to its `:disabled`.
- **Tile pill:** `firmwarePillText` returns `t('web.firmware.pill_queued')` for a row with `queue_position != null` that is not running (before the `available` branch).
- **Polling:** in `loadFirmware`, `busy` also when `this.firmware.queue.active`.
- **Dialog** `<dialog x-ref="firmwareQueueModal" class="expert-modal firmware-modal" aria-labelledby="firmware-queue-heading">`, with the same backdrop handling as the firmware dialog (`@mousedown` / `@click.self` with `firmwareQueueModalBackdropMousedown` and `isBackdropEvent`). Content: heading `queue_heading`, close button, `firmwareError` banner, `queue_intro`, a list (`<ul class="firmware-queue-list">`) with one `<li>` per `firmwareQueueCandidates()` entry (`:key="entry.device_id"`): `<label><input type="checkbox" :value="entry.device_id" x-model.number="firmwareQueueSelection"> label, room hint, installed → offer.version_string, and the `queue_offline` hint when `entry.online === false`</label>`; the existing warning box (`before_heading`, `before_duration`, `before_restart`, `before_export`) plus `queue_background` as a fourth `<li>`; buttons `t('web.firmware.cancel')` → `closeFirmwareQueueModal()` and primary `t('web.firmware.queue_install', { count: firmwareQueueSelection.length })`, `:disabled="firmwareQueueBusy || firmwareQueueSelection.length === 0"` → `startFirmwareQueue()`.
- **`openFirmwareQueueModal()`**: `this.firmwareError = null; this.firmwareQueueSelection = this.firmwareQueueCandidates().map((row) => row.device_id); this.$nextTick(() => this.$refs.firmwareQueueModal.showModal());`
- **`startFirmwareQueue()`**: sets `firmwareQueueBusy`, `this.firmware = await this.request("POST", "/api/firmware/queue", { device_ids: this.firmwareQueueSelection })`, closes the dialog on success, on error sets `firmwareError` with `web.firmware.action_error` and keeps the dialog open; finally clears busy and calls `loadFirmware()`.
- **`resumeFirmwareQueue()` / `clearFirmwareQueue()`**: `POST /api/firmware/queue/resume` / `DELETE /api/firmware/queue`, assign `this.firmware`, errors into `firmwareError`, then `loadFirmware()`.
- **Logout** cleanup next to the firmware dialog's: close `firmwareQueueModal` if open.
- The card's error banner condition `firmwareError && firmwareModalDevice === null` must not show the dialog's error twice: show it only when the queue dialog is closed too (`&& !$refs.firmwareQueueModal?.open` is not reactive — instead add a flag `firmwareQueueModalOpen` set in open/`@close`, and use `&& !firmwareQueueModalOpen`).

- [ ] **Step 1: Write the failing tests** in `tests/api/test_web.py`, after `test_the_next_firmware_check_line_follows_next_check_at`. Use the same helpers (`_served_elements`, `_app_state`, `_BINDINGS_JS`, `NODE`). Study `test_the_next_firmware_check_line_follows_next_check_at` and one test that calls an app method through `state.<method>()` (grep for `state.firmwarePillText` or `state.firmware` usages, or another harness test calling methods) before writing these:

1. `test_the_firmware_queue_parts_are_delivered` (no node): the HTML has `x-ref="firmwareQueueModal"`, `openFirmwareQueueModal()`, `startFirmwareQueue()`, `t('web.firmware.update_all'`, `t('web.firmware.queue_halted'`, `clearFirmwareQueue()`, `resumeFirmwareQueue()`; app.js has `"/api/firmware/queue"`. `startFirmwareQueue(` appears exactly once in the HTML (only the dialog's button starts a queue).
2. `test_update_all_counts_and_preselects_the_offered_devices` (node): with `state.firmware` holding three rows (one `available` with offer, one `available` already queued with `queue_position: 1`, one `none_found`) and `queue: {device_ids: [], active: false, halted_reason: null}`, evaluate the served "Update all" button's `x-text` → `Update all (1)` with `{count}` translation, and its `:disabled` false; after `state.firmware.queue.active = true` → disabled true; run `state.firmwareQueueCandidates().map(r => r.device_id)` → only the first id.
3. `test_the_queue_bands_follow_the_overview` (node): served running band `x-show` false with `queue.active` false, true with it true; its text with `updating_device_id` pointing at a row in `transferring` with progress 43 → `Updating Lamp · 43 % · 2 more queued` (provide translations for `queue_running` and `state_transferring`); halted band `x-show` truthy with `halted_reason: 'x'` and its `x-text` → `Stopped: x`.
4. `test_a_queued_device_reads_queued_in_table_and_pill` (node): `state.firmwareStateText({state:'available', queue_position: 3, progress: null})` → `Queued (#3)`; `state.firmwarePillText(id)` for a queued row → `Update queued`; a row in `transferring` with `queue_position` null keeps the transferring text.

Each node test carries `@pytest.mark.skipif(NODE is None, reason="node is required for this test")` and a "Fault to prove it:" line in its docstring, like the neighbouring tests.

- [ ] **Step 2: Run** `uv run pytest tests/api/test_web.py -k "firmware" -v` — new tests fail.

- [ ] **Step 3: Implement** the markup, methods, strings and CSS described above.

- [ ] **Step 4: Run** `uv run pytest tests/api/test_web.py -k "firmware or i18n or strings" -v` and `uv run pytest tests/test_i18n.py -v` (it checks every `t('…')` key has `en` and `de`), then the checks from Global Constraints.

- [ ] **Step 5: Real browser check.** Start the app the way `docs/DEVELOPMENT.md` describes for local WebUI work (or `.claude/launch.json` if it exists) with a fake or no matter-server only if that is documented; if the app cannot run without a matter-server, skip this step and say so in the report — the node harness tests stand in for it.

- [ ] **Step 6: Commit** — `feat(web): update every device with an offer from the Device updates card`

---

### Task 5: Whole-branch verification

- [ ] `uv run pytest --collect-only -q | tail -3` — no collection errors (duplicate test module names break CI).
- [ ] Suite in two halves, each in the foreground with a 600 s timeout: `uv run pytest tests/api -q` and `uv run pytest tests --ignore=tests/api -q`.
- [ ] `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run python scripts/check_language.py`.
- [ ] Check the schema number against `origin/main`: `git fetch origin && git show origin/main:src/loxmatter/model/store.py | grep -n "_SCHEMA_VERSION ="` — if `main` is already at 16, renumber this branch's migration to the next free number and adjust tests and CHANGELOG.
