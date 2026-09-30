# Firmware updates for Matter devices — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator see which commissioned Matter devices have a firmware update in the CSA DCL, install one on a click, follow its progress, and see every device's Matter version — as designed in `docs/superpowers/specs/2026-09-30-device-firmware-updates-design.md`.

**Architecture:** A new `FirmwareSource` protocol (`sources/firmware.py`) that `BridgeMatterClient` implements on top of matter-server's `check_node_update`/`update_node` and its node cache. A new package `loxmatter/firmware/` holds the pure state rules, the checker (all/one), the single install job, the daily scheduler, and a `FirmwareService` that bundles them. State lives in a new `firmware_status` table plus `device.matter_spec_version` (schema 15). A new router `api/firmware.py` serves the WebUI, which polls it.

**Tech Stack:** Python 3.12, FastAPI, SQLite, `matter-python-client` 1.4.0, pytest + pytest-asyncio + httpx2, Alpine.js WebUI.

## Global Constraints

- Everything in the repository is English; user-visible text goes through `i18n.t(...)` with an `en` and a `de` value in `src/loxmatter/i18n/strings.yaml`. German values use the formal "Sie", like every existing `de:` value.
- Commit messages: Conventional Commits, English, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Store migrations are additive only: no column or table is ever dropped (the updater's rollback runs without a database restore).
- The bridge never installs firmware on its own. Only `POST /api/devices/{id}/firmware/update` starts an install.
- Minimum server schema for the feature: `10` (`FIRMWARE_MIN_SCHEMA`).
- Daily check at `06:00` local time, setting key `firmware.daily_check_enabled`, missing means enabled.
- Install job bounds: poll `2 s`, re-read from the device after `30 s` without change, `failed` after `120 s` back in Idle, `stalled` after `15 min` without change, give up after `3 h`.
- Check bound: `60 s` per device.
- New test files must have names no other test module has (`test_firmware_*.py`). Tests run in the foreground. The full suite takes ~11 minutes: run it in two halves (`uv run pytest tests/api -q` and `uv run pytest --ignore=tests/api -q`), after `uv run pytest --collect-only -q` over everything.
- Checks CI runs: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`, `uv run python scripts/check_language.py`.
- A firmware version is compared as the integer `SoftwareVersion` (`0/40/9`), never as the string.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/loxmatter/sources/firmware.py` (new) | `FirmwareFacts`, `UpdateOffer`, `FirmwareSource` protocol, `FIRMWARE_MIN_SCHEMA` |
| `src/loxmatter/firmware/__init__.py` (new) | Package docstring only |
| `src/loxmatter/firmware/states.py` (new) | Pure rules: `job_state_for`, `derive_state`, `format_spec_version` |
| `src/loxmatter/model/firmware_status_store.py` (new) | `FirmwareStatus` row + `FirmwareStatusStore` on the `firmware_status` table |
| `src/loxmatter/model/firmware_settings_store.py` (new) | `FirmwareSettingsStore`: the daily-check switch |
| `src/loxmatter/model/store.py` | Schema 15: table, `device.matter_spec_version`, `StoredDevice` field, register/backfill/set |
| `src/loxmatter/sources/supervisor.py` | Call the new backfill in `attach` |
| `src/loxmatter/matter/client.py` | `BridgeMatterClient` implements `FirmwareSource` |
| `src/loxmatter/firmware/check.py` (new) | `FirmwareChecker`: check all / one, progress |
| `src/loxmatter/firmware/job.py` (new) | `FirmwareJobs`: the one install at a time, follow, resume |
| `src/loxmatter/firmware/schedule.py` (new) | `run_daily`, `seconds_until` |
| `src/loxmatter/firmware/service.py` (new) | `FirmwareService` bundling checker, jobs, overview |
| `src/loxmatter/api/firmware.py` (new) | Router: overview, check all/one, install, settings |
| `src/loxmatter/api/models.py` | Pydantic models for the router; `DeviceExpertOut.matter_version` |
| `src/loxmatter/api/devices.py` | Expert route returns `matter_version` |
| `src/loxmatter/loxone/server.py` | `build_app(..., firmware=...)`, router wiring |
| `src/loxmatter/cli.py` | Build `FirmwareService`, resume, schedule, shutdown |
| `src/loxmatter/i18n/strings.yaml` | `api.firmware.*`, `web.firmware.*`, `web.devices.expert_matter_version`, `web.devices.menu_firmware` |
| `src/loxmatter/web/app.js`, `index.html`, `style.css` | Tile pill, kebab item, dialog, System card, expert row |
| `CHANGELOG.md` | Unreleased entry |

---

### Task 1: Firmware types and pure state rules

**Files:**
- Create: `src/loxmatter/sources/firmware.py`
- Create: `src/loxmatter/firmware/__init__.py`
- Create: `src/loxmatter/firmware/states.py`
- Test: `tests/firmware/test_firmware_states.py`

`states.py` needs a `FirmwareStatus` type that Task 2 defines in the store. To keep Task 1 independent, `derive_state` takes plain values, not the row.

**Interfaces:**
- Produces:
  - `FIRMWARE_MIN_SCHEMA: Final = 10`
  - `@dataclass(frozen=True) FirmwareFacts(available: bool, has_requestor: bool, software_version: int | None, software_version_string: str | None, spec_version: int | None, update_state: int | None, update_progress: int | None)`
  - `@dataclass(frozen=True) UpdateOffer(software_version: int, software_version_string: str, min_applicable: int, max_applicable: int, release_notes_url: str | None, source: str)`
  - `FirmwareSource` protocol (runtime-checkable): `technology`, `connected`, `firmware_supported() -> bool`, `firmware_facts(address: str) -> FirmwareFacts | None`, `async refresh_firmware_facts(address: str) -> None`, `async check_update(address: str) -> UpdateOffer | None`, `async start_update(address: str, software_version: int) -> None`, `async follow(address: str, *, seed_even_without_new_paths: bool = False) -> None`
  - `states.job_state_for(update_state: int | None) -> str | None`
  - `states.derive_state(*, has_requestor: bool, installed: int | None, checked_at: str | None, check_error: str | None, offer_version: int | None, job_state: str | None) -> str`
  - `states.format_spec_version(raw: int | None, technology: str) -> str | None`
  - `states.SPEC_VERSION_BEFORE_1_3: Final = "<1.3"`
  - State constants: `AVAILABLE`, `NONE_FOUND`, `NO_SOURCE`, `UNCHECKED`, `CHECK_FAILED`, `TRANSFERRING`, `APPLYING`, `STALLED`, `FAILED`, `INTERRUPTED` (string values equal to the lower-case names), `ACTIVE_JOB_STATES = frozenset({TRANSFERRING, APPLYING, STALLED})`

- [ ] **Step 1: Write the failing tests**

`tests/firmware/test_firmware_states.py`:

```python
"""The pure rules of firmware updates (design 2026-09-30, sections 5 and 9.3).

The spec-version values are the ones measured on the test Pi on
September 30, 2026 - see the design, section 9.3."""

import pytest

from loxmatter.firmware import states
from loxmatter.firmware.states import derive_state, format_spec_version, job_state_for


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0x01030000, "1.3"),  # IKEA BILRESA, ALPSTUGA, MYGGSPRAY, TIMMERFLOTTE, KLIPPBOK
        (0x01040000, "1.4"),  # IKEA GRILLPLATS, MYGGBETT, KAJPLATS
        (0x01040100, "1.4.1"),  # Tasmota 15.6.0
        (None, states.SPEC_VERSION_BEFORE_1_3),  # Tasmota 13.3.0: no 0/40/21
    ],
)
def test_format_spec_version_matches_the_measured_devices(raw, expected):
    assert format_spec_version(raw, "matter") == expected


def test_format_spec_version_ignores_the_reserved_low_byte():
    assert format_spec_version(0x010400FF, "matter") == "1.4"


def test_format_spec_version_is_none_for_a_zigbee_device():
    assert format_spec_version(None, "zigbee") is None
    assert format_spec_version(0x01040000, "zigbee") is None


@pytest.mark.parametrize(
    ("update_state", "expected"),
    [
        (None, None),
        (0, None),  # Unknown
        (1, None),  # Idle
        (2, states.TRANSFERRING),  # Querying
        (3, states.TRANSFERRING),  # DelayedOnQuery
        (4, states.TRANSFERRING),  # Downloading
        (5, states.APPLYING),  # Applying
        (6, states.APPLYING),  # DelayedOnApply
        (7, states.APPLYING),  # RollingBack
        (8, states.TRANSFERRING),  # DelayedOnUserConsent
    ],
)
def test_job_state_for_maps_every_update_state(update_state, expected):
    assert job_state_for(update_state) == expected


def _derive(**overrides):
    values = {
        "has_requestor": True,
        "installed": 16842752,  # KAJPLATS 1.1.0
        "checked_at": "2026-09-30T06:00:00+00:00",
        "check_error": None,
        "offer_version": None,
        "job_state": None,
    }
    values.update(overrides)
    return derive_state(**values)


def test_a_device_without_the_requestor_cluster_has_no_source():
    assert _derive(has_requestor=False, checked_at=None) == states.NO_SOURCE


def test_a_device_never_checked_is_unchecked():
    assert _derive(checked_at=None) == states.UNCHECKED


def test_a_check_without_offer_found_nothing():
    assert _derive() == states.NONE_FOUND


def test_a_newer_offer_is_available():
    assert _derive(offer_version=16908288) == states.AVAILABLE  # 1.2.0


def test_an_offer_the_device_already_reached_is_not_available():
    assert _derive(offer_version=16842752) == states.NONE_FOUND


def test_an_offer_for_a_device_with_unknown_version_is_available():
    assert _derive(installed=None, offer_version=16908288) == states.AVAILABLE


def test_a_failed_check_wins_over_the_kept_offer():
    assert _derive(check_error="no internet", offer_version=16908288) == states.CHECK_FAILED


@pytest.mark.parametrize("job", [states.TRANSFERRING, states.APPLYING, states.STALLED])
def test_a_running_job_wins_over_everything(job):
    assert _derive(has_requestor=False, check_error="x", job_state=job) == job


@pytest.mark.parametrize("job", [states.FAILED, states.INTERRUPTED])
def test_an_ended_job_is_shown_until_the_next_check(job):
    assert _derive(offer_version=16908288, job_state=job) == job
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/firmware/test_firmware_states.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.firmware'`

- [ ] **Step 3: Write the implementation**

`src/loxmatter/sources/firmware.py` (copy the GPL header block from `src/loxmatter/sources/__init__.py` lines 1-15 to the top of every new source file in this plan):

```python
"""What a device source offers for firmware updates (design 2026-09-30).

Apart from `DeviceSource` on purpose: only some sources can update firmware -
Matter now, Zigbee in stage 2 - and a method every source must carry would
force a stub onto the others. `FirmwareService` asks `isinstance(source,
FirmwareSource)` instead."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from loxmatter.matter.models import Technology

# The server schema from which `check_node_update` and `update_node` exist
# (`matter_server.client.MatterClient`, `require_schema=10`).
FIRMWARE_MIN_SCHEMA: Final = 10


@dataclass(frozen=True)
class FirmwareFacts:
    """What the source's cache knows about one device's firmware right now.

    `software_version` is the integer `SoftwareVersion` (0/40/9), the only
    value versions are compared by; `software_version_string` (0/40/10) is
    what a person reads. `spec_version` is the raw `SpecificationVersion`
    (0/40/21), `None` on a device from before Matter 1.3.
    `update_state`/`update_progress` are the OTA Software Update Requestor's
    `UpdateState` (0/42/2) and `UpdateStateProgress` (0/42/3)."""

    available: bool
    has_requestor: bool
    software_version: int | None
    software_version_string: str | None
    spec_version: int | None
    update_state: int | None
    update_progress: int | None


@dataclass(frozen=True)
class UpdateOffer:
    """An image the source found for a device - on Matter, one row of the
    CSA DCL as `check_node_update` returns it."""

    software_version: int
    software_version_string: str
    min_applicable: int
    max_applicable: int
    release_notes_url: str | None
    source: str


@runtime_checkable
class FirmwareSource(Protocol):
    """A device source that can check and install firmware updates."""

    @property
    def technology(self) -> Technology: ...

    @property
    def connected(self) -> bool: ...

    def firmware_supported(self) -> bool: ...

    def firmware_facts(self, address: str) -> FirmwareFacts | None: ...

    async def refresh_firmware_facts(self, address: str) -> None: ...

    async def check_update(self, address: str) -> UpdateOffer | None: ...

    async def start_update(self, address: str, software_version: int) -> None: ...

    async def follow(self, address: str, *, seed_even_without_new_paths: bool = False) -> None: ...
```

`src/loxmatter/firmware/__init__.py`:

```python
"""Firmware updates for commissioned devices (design 2026-09-30).

`states` holds the rules, `check` asks for offers, `job` installs one,
`schedule` runs the daily check, `service` bundles them for the API and
`cli`."""
```

`src/loxmatter/firmware/states.py`:

```python
"""The pure rules of firmware updates (design 2026-09-30, sections 5 and 9.3).

No I/O here: everything the overview shows is decided by these functions,
so the table in section 5 is tested row by row without a server."""

from __future__ import annotations

from typing import Final

AVAILABLE: Final = "available"
NONE_FOUND: Final = "none_found"
NO_SOURCE: Final = "no_source"
UNCHECKED: Final = "unchecked"
CHECK_FAILED: Final = "check_failed"
TRANSFERRING: Final = "transferring"
APPLYING: Final = "applying"
STALLED: Final = "stalled"
FAILED: Final = "failed"
INTERRUPTED: Final = "interrupted"

# A job in one of these is still running; the others have ended.
ACTIVE_JOB_STATES: Final = frozenset({TRANSFERRING, APPLYING, STALLED})
ENDED_JOB_STATES: Final = frozenset({FAILED, INTERRUPTED})

# Matter's UpdateStateEnum (OTA Software Update Requestor, 0/42/2):
# 0 Unknown, 1 Idle, 2 Querying, 3 DelayedOnQuery, 4 Downloading,
# 5 Applying, 6 DelayedOnApply, 7 RollingBack, 8 DelayedOnUserConsent.
_TRANSFERRING_STATES: Final = frozenset({2, 3, 4, 8})
_APPLYING_STATES: Final = frozenset({5, 6, 7})

# Shown for a Matter device that carries no SpecificationVersion: the
# attribute exists since Matter 1.3, so the device implements 1.2 or older.
# The WebUI translates this token; the exact older version is not guessed.
SPEC_VERSION_BEFORE_1_3: Final = "<1.3"


def job_state_for(update_state: int | None) -> str | None:
    """The job state an `UpdateState` stands for, `None` for Idle/Unknown."""
    if update_state in _TRANSFERRING_STATES:
        return TRANSFERRING
    if update_state in _APPLYING_STATES:
        return APPLYING
    return None


def derive_state(
    *,
    has_requestor: bool,
    installed: int | None,
    checked_at: str | None,
    check_error: str | None,
    offer_version: int | None,
    job_state: str | None,
) -> str:
    """One device's state in the update overview (design section 5).

    A running job wins over everything, an ended one is shown until the
    next check clears it. `none_found` never claims "up to date": the DCL
    cannot tell a current device from one whose manufacturer publishes
    nothing (design section 4)."""
    if job_state in ACTIVE_JOB_STATES or job_state in ENDED_JOB_STATES:
        return job_state
    if not has_requestor:
        return NO_SOURCE
    if checked_at is None:
        return UNCHECKED
    if check_error is not None:
        return CHECK_FAILED
    if offer_version is not None and (installed is None or offer_version > installed):
        return AVAILABLE
    return NONE_FOUND


def format_spec_version(raw: int | None, technology: str) -> str | None:
    """`SpecificationVersion` as a person reads it (design section 9.3).

    Encoded as 0xMMmmPP00 - major, minor, patch, and a reserved low byte.
    `1.4` when the patch is 0, else `1.4.1`. `None` for a non-Matter
    device, `SPEC_VERSION_BEFORE_1_3` for a Matter device without it."""
    if technology != "matter":
        return None
    if raw is None:
        return SPEC_VERSION_BEFORE_1_3
    major = (raw >> 24) & 0xFF
    minor = (raw >> 16) & 0xFF
    patch = (raw >> 8) & 0xFF
    return f"{major}.{minor}" if patch == 0 else f"{major}.{minor}.{patch}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/firmware/test_firmware_states.py -q`
Expected: all pass.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check src/loxmatter/firmware src/loxmatter/sources/firmware.py tests/firmware
uv run ruff format src/loxmatter/firmware src/loxmatter/sources/firmware.py tests/firmware
uv run mypy
git add src/loxmatter/sources/firmware.py src/loxmatter/firmware tests/firmware
git commit -m "feat(firmware): add the firmware source protocol and the state rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Store schema 15 — `firmware_status`, `matter_spec_version`, settings

**Files:**
- Create: `src/loxmatter/model/firmware_status_store.py`
- Create: `src/loxmatter/model/firmware_settings_store.py`
- Modify: `src/loxmatter/model/store.py` (schema comment block before `_SCHEMA_VERSION` ~line 175, `_SCHEMA` ~line 187, `_migrate_to_v14` → add `_migrate_to_v15` after it ~line 975, `_MIGRATIONS` ~line 980, `StoredDevice` ~line 1335, `Store.__init__` ~line 1433, `register_device` ~line 1523, `_as_device` ~line 1652, new methods after `backfill_basic_information` ~line 2375)
- Modify: `src/loxmatter/sources/supervisor.py:83`
- Modify: `tests/model/test_store_migration.py` (every `user_version(path) == 14` → `15`)
- Test: `tests/model/test_firmware_status_store.py`, `tests/model/test_firmware_settings_store.py`, `tests/model/test_firmware_store_schema.py`

**Interfaces:**
- Consumes: `UpdateOffer` (Task 1).
- Produces:
  - `FirmwareStatus(device_id: int, checked_at: str | None, check_error: str | None, offer: UpdateOffer | None, job_state: str | None, job_progress: int | None, job_started_at: str | None, job_changed_at: str | None, job_error: str | None)`
  - `FirmwareStatusStore`: `get(device_id) -> FirmwareStatus | None`, `all() -> dict[int, FirmwareStatus]`, `record_check(device_id, offer: UpdateOffer | None, checked_at: str) -> None`, `record_check_error(device_id, error: str, checked_at: str) -> None`, `drop_offer(device_id) -> None`, `start_job(device_id, started_at: str) -> None`, `update_job(device_id, state: str, progress: int | None, changed_at: str) -> None`, `end_job(device_id, state: str | None, error: str | None, changed_at: str) -> None`, `last_checked_at() -> str | None`
  - `FirmwareSettingsStore`: `get_daily_check_enabled() -> bool`, `set_daily_check_enabled(enabled: bool) -> None`
  - `Store.firmware_status: FirmwareStatusStore`, `Store.firmware_settings: FirmwareSettingsStore`
  - `StoredDevice.matter_spec_version: int | None = None` (last field, with default)
  - `Store.backfill_matter_spec_version(snapshots: Sequence[NodeSnapshot]) -> int`
  - `Store.set_firmware_details(device_id: int, firmware: str | None, spec_version: int | None) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/model/test_firmware_status_store.py`:

```python
"""`firmware_status` rows (design 2026-09-30, section 8)."""

import json
from pathlib import Path

from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.sources.firmware import UpdateOffer

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"

# node 22 on the test Pi, measured 2026-09-30: KAJPLATS 1.1.0 -> 1.2.0
KAJPLATS_OFFER = UpdateOffer(
    software_version=16908288,
    software_version_string="1.2.0",
    min_applicable=0,
    max_applicable=16908287,
    release_notes_url=None,
    source="main-net-dcl",
)


def _store_with_lamp(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    raw = json.loads((FIXTURES / "ikea_kajplats_cws_lamp.json").read_text(encoding="utf-8"))
    device_id = store.register_device(NodeSnapshot.from_raw(raw["node_id"], raw))
    return store, device_id


def test_a_device_without_a_row_has_no_status(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    assert store.firmware_status.get(device_id) is None
    assert store.firmware_status.all() == {}


def test_record_check_stores_the_offer(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    store.firmware_status.record_check(device_id, KAJPLATS_OFFER, "2026-09-30T06:00:00+00:00")
    status = store.firmware_status.get(device_id)
    assert status is not None
    assert status.offer == KAJPLATS_OFFER
    assert status.checked_at == "2026-09-30T06:00:00+00:00"
    assert status.check_error is None


def test_record_check_without_offer_clears_the_old_one(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    store.firmware_status.record_check(device_id, KAJPLATS_OFFER, "t1")
    store.firmware_status.record_check(device_id, None, "t2")
    status = store.firmware_status.get(device_id)
    assert status is not None and status.offer is None


def test_a_check_error_keeps_the_previous_offer(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    store.firmware_status.record_check(device_id, KAJPLATS_OFFER, "t1")
    store.firmware_status.record_check_error(device_id, "no internet", "t2")
    status = store.firmware_status.get(device_id)
    assert status is not None
    assert status.offer == KAJPLATS_OFFER
    assert status.check_error == "no internet"
    assert status.checked_at == "t2"


def test_a_new_check_clears_a_failed_job_but_not_a_running_one(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    store.firmware_status.end_job(device_id, "failed", "device refused", "t1")
    store.firmware_status.record_check(device_id, KAJPLATS_OFFER, "t2")
    status = store.firmware_status.get(device_id)
    assert status is not None and status.job_state is None and status.job_error is None

    store.firmware_status.start_job(device_id, "t3")
    store.firmware_status.record_check(device_id, KAJPLATS_OFFER, "t4")
    status = store.firmware_status.get(device_id)
    assert status is not None and status.job_state == "transferring"


def test_a_job_moves_through_its_states(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    store.firmware_status.start_job(device_id, "t1")
    store.firmware_status.update_job(device_id, "transferring", 43, "t2")
    status = store.firmware_status.get(device_id)
    assert status is not None
    assert (status.job_state, status.job_progress, status.job_started_at) == ("transferring", 43, "t1")
    store.firmware_status.end_job(device_id, None, None, "t3")
    status = store.firmware_status.get(device_id)
    assert status is not None and status.job_state is None and status.job_progress is None


def test_drop_offer_keeps_the_check_time(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    store.firmware_status.record_check(device_id, KAJPLATS_OFFER, "t1")
    store.firmware_status.drop_offer(device_id)
    status = store.firmware_status.get(device_id)
    assert status is not None and status.offer is None and status.checked_at == "t1"


def test_last_checked_at_is_the_newest_check(tmp_path):
    store, device_id = _store_with_lamp(tmp_path)
    assert store.firmware_status.last_checked_at() is None
    store.firmware_status.record_check(device_id, None, "2026-09-30T06:00:00+00:00")
    assert store.firmware_status.last_checked_at() == "2026-09-30T06:00:00+00:00"
```

`tests/model/test_firmware_settings_store.py`:

```python
"""The daily-check switch (design 2026-09-30, section 6.2)."""

from loxmatter.model.store import Store


def test_the_daily_check_is_on_when_nothing_is_stored(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    assert store.firmware_settings.get_daily_check_enabled() is True


def test_the_daily_check_can_be_switched_off_and_on(tmp_path):
    path = tmp_path / "t.sqlite"
    store = Store(path)
    store.firmware_settings.set_daily_check_enabled(False)
    store.close()
    store = Store(path)
    assert store.firmware_settings.get_daily_check_enabled() is False
    store.firmware_settings.set_daily_check_enabled(True)
    assert store.firmware_settings.get_daily_check_enabled() is True
```

`tests/model/test_firmware_store_schema.py`:

```python
"""Schema 15 (design 2026-09-30, sections 8 and 9.3): additive only."""

import json
import sqlite3
from pathlib import Path

from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store, schema_version

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"


def _snapshot(name: str) -> NodeSnapshot:
    raw = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return NodeSnapshot.from_raw(raw["node_id"], raw)


def _columns(path: Path, table: str) -> set[str]:
    db = sqlite3.connect(str(path))
    try:
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    finally:
        db.close()


def test_schema_version_is_15():
    assert schema_version() == 15


def test_register_device_stores_the_spec_version(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(_snapshot("ikea_kajplats_cws_lamp.json"))
    assert store.device(device_id).matter_spec_version == 0x01040000


def test_a_v14_database_gains_the_column_and_the_table(tmp_path):
    path = tmp_path / "v14.sqlite"
    store = Store(path)
    device_id = store.register_device(_snapshot("ikea_bilresa_button.json"))
    store.close()
    db = sqlite3.connect(str(path))
    db.executescript(
        "DROP TABLE firmware_status;"
        " CREATE TABLE device_old AS SELECT id, unique_id, node_id, technology, address,"
        " label, udp_port, active, exported_at, updated_at, room, device_types,"
        " network_features, vendor_name, product_name, firmware, serial_number FROM device;"
        " DROP TABLE device;"
        " ALTER TABLE device_old RENAME TO device;"
        " PRAGMA user_version = 14;"
    )
    db.commit()
    db.close()
    assert "matter_spec_version" not in _columns(path, "device")

    store = Store(path)
    assert "matter_spec_version" in _columns(path, "device")
    assert "job_state" in _columns(path, "firmware_status")
    assert store.device(device_id).matter_spec_version is None
    assert store.backfill_matter_spec_version([_snapshot("ikea_bilresa_button.json")]) == 1
    assert store.device(device_id).matter_spec_version == 0x01030000


def test_backfill_never_overwrites_a_known_value(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    snapshot = _snapshot("ikea_kajplats_cws_lamp.json")
    device_id = store.register_device(snapshot)
    assert store.backfill_matter_spec_version([snapshot]) == 0
    assert store.device(device_id).matter_spec_version == 0x01040000


def test_set_firmware_details_overwrites_both_values(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    device_id = store.register_device(_snapshot("ikea_kajplats_cws_lamp.json"))
    store.set_firmware_details(device_id, "1.3.0", 0x01050000)
    device = store.device(device_id)
    assert device.firmware == "1.3.0"
    assert device.matter_spec_version == 0x01050000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/model/test_firmware_status_store.py tests/model/test_firmware_settings_store.py tests/model/test_firmware_store_schema.py -q`
Expected: FAIL (`AttributeError: 'Store' object has no attribute 'firmware_status'`, `schema_version() == 14`).

- [ ] **Step 3: Write `firmware_status_store.py`**

```python
"""One firmware row per device (design 2026-09-30, section 8).

Another view onto the store's connection, like `zigbee_pending_store.py`.
A device without a row has never been checked and never updated."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from loxmatter.sources.firmware import UpdateOffer

# Job states a new check clears (design section 5: shown until the next
# check). A running job is never cleared by a check.
_ENDED_JOB_STATES = ("failed", "interrupted")


@dataclass(frozen=True)
class FirmwareStatus:
    device_id: int
    checked_at: str | None
    check_error: str | None
    offer: UpdateOffer | None
    job_state: str | None
    job_progress: int | None
    job_started_at: str | None
    job_changed_at: str | None
    job_error: str | None


class FirmwareStatusStore:
    """Access to `firmware_status` through the store's connection."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def _ensure_row(self, device_id: int) -> None:
        self._db.execute(
            "INSERT INTO firmware_status (device_id) VALUES (?)"
            " ON CONFLICT(device_id) DO NOTHING",
            (device_id,),
        )

    @staticmethod
    def _as_status(row: sqlite3.Row) -> FirmwareStatus:
        offer = None
        if row["offer_version"] is not None:
            offer = UpdateOffer(
                software_version=int(row["offer_version"]),
                software_version_string=str(row["offer_version_string"] or ""),
                min_applicable=int(row["offer_min_applicable"] or 0),
                max_applicable=int(row["offer_max_applicable"] or 0),
                release_notes_url=row["offer_notes_url"],
                source=str(row["offer_source"] or ""),
            )
        return FirmwareStatus(
            device_id=int(row["device_id"]),
            checked_at=row["checked_at"],
            check_error=row["check_error"],
            offer=offer,
            job_state=row["job_state"],
            job_progress=None if row["job_progress"] is None else int(row["job_progress"]),
            job_started_at=row["job_started_at"],
            job_changed_at=row["job_changed_at"],
            job_error=row["job_error"],
        )

    def get(self, device_id: int) -> FirmwareStatus | None:
        row = self._db.execute(
            "SELECT * FROM firmware_status WHERE device_id = ?", (device_id,)
        ).fetchone()
        return None if row is None else self._as_status(row)

    def all(self) -> dict[int, FirmwareStatus]:
        rows = self._db.execute("SELECT * FROM firmware_status").fetchall()
        return {int(row["device_id"]): self._as_status(row) for row in rows}

    def last_checked_at(self) -> str | None:
        row = self._db.execute("SELECT MAX(checked_at) AS latest FROM firmware_status").fetchone()
        return None if row is None else row["latest"]

    def record_check(self, device_id: int, offer: UpdateOffer | None, checked_at: str) -> None:
        """A successful check: replaces the offer (or clears it), clears the
        check error and an ended job."""
        self._ensure_row(device_id)
        self._db.execute(
            "UPDATE firmware_status SET checked_at = ?, check_error = NULL,"
            " offer_version = ?, offer_version_string = ?, offer_min_applicable = ?,"
            " offer_max_applicable = ?, offer_notes_url = ?, offer_source = ?"
            " WHERE device_id = ?",
            (
                checked_at,
                None if offer is None else offer.software_version,
                None if offer is None else offer.software_version_string,
                None if offer is None else offer.min_applicable,
                None if offer is None else offer.max_applicable,
                None if offer is None else offer.release_notes_url,
                None if offer is None else offer.source,
                device_id,
            ),
        )
        self._db.execute(
            "UPDATE firmware_status SET job_state = NULL, job_progress = NULL, job_error = NULL"
            f" WHERE device_id = ? AND job_state IN {_ENDED_JOB_STATES}",
            (device_id,),
        )
        self._db.commit()

    def record_check_error(self, device_id: int, error: str, checked_at: str) -> None:
        """A failed check: keeps the previous offer (design section 5)."""
        self._ensure_row(device_id)
        self._db.execute(
            "UPDATE firmware_status SET checked_at = ?, check_error = ? WHERE device_id = ?",
            (checked_at, error, device_id),
        )
        self._db.commit()

    def drop_offer(self, device_id: int) -> None:
        self._db.execute(
            "UPDATE firmware_status SET offer_version = NULL, offer_version_string = NULL,"
            " offer_min_applicable = NULL, offer_max_applicable = NULL,"
            " offer_notes_url = NULL, offer_source = NULL WHERE device_id = ?",
            (device_id,),
        )
        self._db.commit()

    def start_job(self, device_id: int, started_at: str) -> None:
        self._ensure_row(device_id)
        self._db.execute(
            "UPDATE firmware_status SET job_state = 'transferring', job_progress = NULL,"
            " job_started_at = ?, job_changed_at = ?, job_error = NULL WHERE device_id = ?",
            (started_at, started_at, device_id),
        )
        self._db.commit()

    def update_job(self, device_id: int, state: str, progress: int | None, changed_at: str) -> None:
        self._ensure_row(device_id)
        self._db.execute(
            "UPDATE firmware_status SET job_state = ?, job_progress = ?, job_changed_at = ?"
            " WHERE device_id = ?",
            (state, progress, changed_at, device_id),
        )
        self._db.commit()

    def end_job(
        self, device_id: int, state: str | None, error: str | None, changed_at: str
    ) -> None:
        """Ends a job: `state` `None` on success, `failed`/`interrupted` otherwise."""
        self._ensure_row(device_id)
        self._db.execute(
            "UPDATE firmware_status SET job_state = ?, job_progress = NULL, job_error = ?,"
            " job_changed_at = ? WHERE device_id = ?",
            (state, error, changed_at, device_id),
        )
        self._db.commit()
```

- [ ] **Step 4: Write `firmware_settings_store.py`**

```python
"""The daily firmware check's switch (design 2026-09-30, section 6.2).

One key in the `setting` table, following `update_settings_store.py`. A
missing key means enabled: the check is on by default."""

from __future__ import annotations

import sqlite3

_DAILY_CHECK_KEY = "firmware.daily_check_enabled"


class FirmwareSettingsStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_daily_check_enabled(self) -> bool:
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_DAILY_CHECK_KEY,)
        ).fetchone()
        return row is None or str(row["value"]) != "0"

    def set_daily_check_enabled(self, enabled: bool) -> None:
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_DAILY_CHECK_KEY, "1" if enabled else "0"),
        )
        self._db.commit()
```

- [ ] **Step 5: Extend `store.py`**

1. Imports, next to the other store views (~line 60):

```python
from loxmatter.model.firmware_settings_store import FirmwareSettingsStore
from loxmatter.model.firmware_status_store import FirmwareStatusStore
```

2. Append to the comment block above `_SCHEMA_VERSION` and bump it:

```python
# Version 15 (firmware updates, design 2026-09-30) adds the table
# `firmware_status` and the nullable column `device.matter_spec_version`,
# see `_migrate_to_v15`. Both additive: a rolled-back image names neither.
_SCHEMA_VERSION = 15
```

3. In `_SCHEMA`, add `matter_spec_version INTEGER` as the last column of `device` (after `serial_number    TEXT`, add a comma there), and append the table before the closing `"""`:

```sql
CREATE TABLE IF NOT EXISTS firmware_status (
    device_id            INTEGER PRIMARY KEY REFERENCES device(id),
    checked_at           TEXT,
    check_error          TEXT,
    offer_version        INTEGER,
    offer_version_string TEXT,
    offer_min_applicable INTEGER,
    offer_max_applicable INTEGER,
    offer_notes_url      TEXT,
    offer_source         TEXT,
    job_state            TEXT,
    job_progress         INTEGER,
    job_started_at       TEXT,
    job_changed_at       TEXT,
    job_error            TEXT
);
```

(The design sketched `ON DELETE CASCADE`; devices are never deleted here — `forget_device` sets `active = 0` — so it is left out, like every other `REFERENCES device(id)` in `_SCHEMA`.)

4. After `_migrate_to_v14`:

```python
def _migrate_to_v15(db: sqlite3.Connection) -> None:
    """Adds `device.matter_spec_version` and `firmware_status` (firmware
    updates, design 2026-09-30, sections 8 and 9.3).

    The table comes from `_SCHEMA`'s `CREATE TABLE IF NOT EXISTS`, which
    `Store.__init__` runs before `_migrate`; the column needs the ALTER.
    No backfill here: `Store.backfill_matter_spec_version` fills it from the
    next snapshot, the way `backfill_basic_information` fills `firmware`."""
    _add_column_if_missing(db, "device", "matter_spec_version", "INTEGER")
```

Check that `Store.__init__` runs `executescript(_SCHEMA)` before `_migrate(...)` (search for `_SCHEMA` in `__init__`). If it runs after, add the `CREATE TABLE IF NOT EXISTS firmware_status (...)` statement to `_migrate_to_v15` as well.

Add `15: _migrate_to_v15,` to `_MIGRATIONS`.

5. `StoredDevice`: add as the LAST field, with a default so the three `StoredDevice(...)` calls in `tests/projectsync/` keep working:

```python
    # The raw SpecificationVersion (0/40/21, design 2026-09-30, 9.3) - `None`
    # for a Zigbee device and for a Matter device from before 1.3. Unlike
    # the four fields above it is refreshed after a firmware update.
    matter_spec_version: int | None = None
```

6. `_as_device`: add `matter_spec_version=None if row["matter_spec_version"] is None else int(row["matter_spec_version"]),`.

7. Next to `_text_attribute`:

```python
def _int_attribute(snapshot: NodeSnapshot, path: str) -> int | None:
    """An integer attribute from the snapshot, `None` if absent or not an
    int (a `bool` is not one here)."""
    value = snapshot.attributes.get(path)
    return value if isinstance(value, int) and not isinstance(value, bool) else None
```

8. `register_device`: add `matter_spec_version` to the column list and one more `?`, with value `_int_attribute(snapshot, "0/40/21")` last.

9. `Store.__init__`, after `self.zigbee_settings = ...`:

```python
        # Firmware updates (design 2026-09-30) - same connection twice more.
        self.firmware_status = FirmwareStatusStore(self._db)
        self.firmware_settings = FirmwareSettingsStore(self._db)
```

10. After `backfill_basic_information`:

```python
    def backfill_matter_spec_version(self, snapshots: Sequence[NodeSnapshot]) -> int:
        """Fills `device.matter_spec_version` where it is still NULL and the
        snapshot carries 0/40/21; returns how many rows that touched. Same
        rules as `backfill_basic_information`: never overwrites, skips a
        device missing from `snapshots`, leaves `updated_at` alone."""
        by_identity = {self._identity_of(snapshot): snapshot for snapshot in snapshots}
        rows = self._db.execute(
            "SELECT id, technology, address FROM device"
            " WHERE matter_spec_version IS NULL AND active = 1"
        ).fetchall()
        filled = 0
        for row in rows:
            snapshot = by_identity.get((str(row["technology"]), str(row["address"])))
            if snapshot is None:
                continue
            spec_version = _int_attribute(snapshot, "0/40/21")
            if spec_version is None:
                continue
            self._db.execute(
                "UPDATE device SET matter_spec_version = ? WHERE id = ?",
                (spec_version, int(row["id"])),
            )
            filled += 1
        self._db.commit()
        return filled

    def set_firmware_details(
        self, device_id: int, firmware: str | None, spec_version: int | None
    ) -> None:
        """After a successful firmware update (design 7.3): the new version
        replaces the one captured at commissioning. A `None` keeps the
        stored value - a device that stops reporting a field has not lost
        it."""
        self._db.execute(
            "UPDATE device SET firmware = COALESCE(?, firmware),"
            " matter_spec_version = COALESCE(?, matter_spec_version) WHERE id = ?",
            (firmware, spec_version, device_id),
        )
        self._db.commit()
```

- [ ] **Step 6: Call the backfill in `attach`**

`src/loxmatter/sources/supervisor.py`, after `store.backfill_basic_information(snapshots)`:

```python
    store.backfill_matter_spec_version(snapshots)
```

- [ ] **Step 7: Move the old migration tests to version 15**

Run: `grep -rn "== 14" tests/model/test_store_migration.py tests/test_version.py`
Replace each `user_version(path) == 14` with `user_version(path) == 15`. Any other hit that means the schema version: change it too; leave hits that mean something else.

- [ ] **Step 8: Run the model tests**

Run: `uv run pytest tests/model tests/sources -q`
Expected: all pass.

- [ ] **Step 9: Lint, type-check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/loxmatter/model src/loxmatter/sources/supervisor.py tests/model
git commit -m "feat(store): keep firmware offers and jobs, and the Matter version, in schema 15

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `BridgeMatterClient` implements `FirmwareSource`

**Files:**
- Modify: `src/loxmatter/matter/client.py` (new methods after `set_thread_dataset`, ~line 607; imports)
- Test: `tests/matter/test_client_firmware.py`

**Interfaces:**
- Consumes: `FirmwareFacts`, `UpdateOffer`, `FIRMWARE_MIN_SCHEMA` (Task 1).
- Produces: `BridgeMatterClient.firmware_supported()`, `.firmware_facts(address)`, `.refresh_firmware_facts(address)`, `.check_update(address)`, `.start_update(address, software_version)` — the `FirmwareSource` protocol.

- [ ] **Step 1: Write the failing tests**

`tests/matter/test_client_firmware.py`:

```python
"""`BridgeMatterClient` as a `FirmwareSource` (design 2026-09-30, section 8).

The answers are the ones matter-server gave on the test Pi on 2026-09-30.
The fake upstream is local to this file on purpose: it needs only the
node cache and three commands."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from matter_server.common.models import MatterSoftwareVersion, UpdateSource

from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError
from loxmatter.sources.firmware import FirmwareSource, UpdateOffer

KAJPLATS_22 = {
    "0/40/9": 16842752,
    "0/40/10": "1.1.0",
    "0/40/21": 0x01040000,
    "0/42/0": [],
    "0/42/1": True,
    "0/42/2": 1,
    "0/42/3": None,
}
TASMOTA_23 = {"0/40/9": 1, "0/40/10": "13.3.0"}


class FirmwareNode:
    def __init__(self, node_id: int, attributes: dict[str, Any], available: bool = True):
        self.node_id = node_id
        self.available = available
        self.node_data = SimpleNamespace(attributes=attributes, available=available)


class FirmwareUpstream:
    def __init__(self, nodes: list[FirmwareNode], schema: int = 11):
        self._nodes = nodes
        self.server_info = SimpleNamespace(schema_version=schema)
        self.checked: list[int] = []
        self.updated: list[tuple[int, int | str]] = []
        self.read: list[tuple[int, Any]] = []
        self.offer: MatterSoftwareVersion | None = None

    async def start_listening(self, init_ready: asyncio.Event | None = None) -> None:
        if init_ready is not None:
            init_ready.set()
        await asyncio.Event().wait()

    async def disconnect(self) -> None:
        return None

    def get_nodes(self) -> list[FirmwareNode]:
        return self._nodes

    def subscribe_events(self, *args: Any, **kwargs: Any) -> Any:
        return lambda: None

    async def check_node_update(self, node_id: int) -> MatterSoftwareVersion | None:
        self.checked.append(node_id)
        return self.offer

    async def update_node(self, node_id: int, software_version: int | str) -> None:
        self.updated.append((node_id, software_version))

    async def read_attribute(self, node_id: int, attribute_path: Any) -> dict[str, Any]:
        self.read.append((node_id, attribute_path))
        return {"0/42/2": 4, "0/42/3": 43}


class _Session:
    async def close(self) -> None:
        return None


async def _connected(upstream: FirmwareUpstream) -> BridgeMatterClient:
    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=_Session,
    )
    await bridge.connect()
    return bridge


def _dcl_offer() -> MatterSoftwareVersion:
    return MatterSoftwareVersion(
        vid=4476,
        pid=36870,
        software_version=16908288,
        software_version_string="1.2.0",
        firmware_information="",
        min_applicable_software_version=0,
        max_applicable_software_version=16908287,
        release_notes_url="",
        update_source=UpdateSource.MAIN_NET_DCL,
    )


async def test_the_client_is_a_firmware_source():
    bridge = await _connected(FirmwareUpstream([]))
    try:
        assert isinstance(bridge, FirmwareSource)
    finally:
        await bridge.disconnect()


@pytest.mark.parametrize(("schema", "expected"), [(9, False), (10, True), (11, True)])
async def test_firmware_is_supported_from_schema_10(schema, expected):
    bridge = await _connected(FirmwareUpstream([], schema=schema))
    try:
        assert bridge.firmware_supported() is expected
    finally:
        await bridge.disconnect()


async def test_firmware_is_not_supported_while_disconnected():
    bridge = BridgeMatterClient(url="ws://test/ws")
    assert bridge.firmware_supported() is False


async def test_facts_come_from_the_node_cache():
    bridge = await _connected(FirmwareUpstream([FirmwareNode(22, KAJPLATS_22)]))
    try:
        facts = bridge.firmware_facts("22")
    finally:
        await bridge.disconnect()
    assert facts is not None
    assert facts.available is True
    assert facts.has_requestor is True
    assert facts.software_version == 16842752
    assert facts.software_version_string == "1.1.0"
    assert facts.spec_version == 0x01040000
    assert facts.update_state == 1
    assert facts.update_progress is None


async def test_a_tasmota_plug_has_no_requestor_and_no_spec_version():
    bridge = await _connected(FirmwareUpstream([FirmwareNode(23, TASMOTA_23)]))
    try:
        facts = bridge.firmware_facts("23")
    finally:
        await bridge.disconnect()
    assert facts is not None
    assert facts.has_requestor is False
    assert facts.spec_version is None


async def test_facts_for_an_unknown_node_are_none():
    bridge = await _connected(FirmwareUpstream([]))
    try:
        assert bridge.firmware_facts("99") is None
    finally:
        await bridge.disconnect()


async def test_check_update_maps_the_dcl_answer():
    upstream = FirmwareUpstream([FirmwareNode(22, KAJPLATS_22)])
    upstream.offer = _dcl_offer()
    bridge = await _connected(upstream)
    try:
        offer = await bridge.check_update("22")
    finally:
        await bridge.disconnect()
    assert upstream.checked == [22]
    assert offer == UpdateOffer(
        software_version=16908288,
        software_version_string="1.2.0",
        min_applicable=0,
        max_applicable=16908287,
        release_notes_url=None,  # the DCL's "" is no link
        source="main-net-dcl",
    )


async def test_check_update_passes_none_through():
    upstream = FirmwareUpstream([FirmwareNode(22, KAJPLATS_22)])
    bridge = await _connected(upstream)
    try:
        assert await bridge.check_update("22") is None
    finally:
        await bridge.disconnect()


async def test_start_update_sends_the_integer_version():
    upstream = FirmwareUpstream([FirmwareNode(22, KAJPLATS_22)])
    bridge = await _connected(upstream)
    try:
        await bridge.start_update("22", 16908288)
    finally:
        await bridge.disconnect()
    assert upstream.updated == [(22, 16908288)]


async def test_refresh_writes_the_read_values_into_the_cache():
    attributes = dict(KAJPLATS_22)
    upstream = FirmwareUpstream([FirmwareNode(22, attributes)])
    bridge = await _connected(upstream)
    try:
        await bridge.refresh_firmware_facts("22")
        facts = bridge.firmware_facts("22")
    finally:
        await bridge.disconnect()
    assert upstream.read == [(22, ["0/42/2", "0/42/3", "0/40/9", "0/40/10"])]
    assert facts is not None and (facts.update_state, facts.update_progress) == (4, 43)


async def test_check_update_without_connection_raises():
    bridge = BridgeMatterClient(url="ws://test/ws")
    with pytest.raises(MatterUnavailableError):
        await bridge.check_update("22")
```

Before running: confirm `BridgeMatterClient.__init__` accepts `session_factory` and `http_session_factory` exactly as `make_client` in `tests/matter/test_client.py` uses them, and that `connect()` needs nothing beyond `start_listening`/`subscribe_events` from the upstream. If `connect()` calls more upstream methods, add them to `FirmwareUpstream` as no-ops.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/matter/test_client_firmware.py -q`
Expected: FAIL with `AttributeError: 'BridgeMatterClient' object has no attribute 'firmware_supported'`.

- [ ] **Step 3: Implement**

Imports in `client.py`:

```python
from loxmatter.sources.firmware import FIRMWARE_MIN_SCHEMA, FirmwareFacts, UpdateOffer
```

Methods, after `set_thread_dataset`:

```python
    # --- Firmware updates (design 2026-09-30, section 8). Verified against
    # matter-python-client 1.4.0: `check_node_update(node_id) ->
    # MatterSoftwareVersion | None` and `update_node(node_id,
    # software_version)`, both `require_schema=10`. Measured on the test Pi
    # on 2026-09-30: the old python-matter-server (schema 11) answers
    # `check_node_update` as well.

    _FIRMWARE_REFRESH_PATHS: Final = ["0/42/2", "0/42/3", "0/40/9", "0/40/10"]

    def firmware_supported(self) -> bool:
        info = getattr(self._upstream, "server_info", None)
        schema = getattr(info, "schema_version", None)
        return self.connected and isinstance(schema, int) and schema >= FIRMWARE_MIN_SCHEMA

    def _node(self, address: str) -> Any | None:
        node_id = int(address)
        for node in self._require_upstream().get_nodes():
            if node.node_id == node_id:
                return node
        return None

    def firmware_facts(self, address: str) -> FirmwareFacts | None:
        node = self._node(address)
        if node is None:
            return None
        attributes = node.node_data.attributes

        def as_int(path: str) -> int | None:
            value = attributes.get(path)
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        text = attributes.get("0/40/10")
        return FirmwareFacts(
            available=bool(node.available),
            has_requestor=any(path.startswith("0/42/") for path in attributes),
            software_version=as_int("0/40/9"),
            software_version_string=text.strip() or None if isinstance(text, str) else None,
            spec_version=as_int("0/40/21"),
            update_state=as_int("0/42/2"),
            update_progress=as_int("0/42/3"),
        )

    async def refresh_firmware_facts(self, address: str) -> None:
        """Reads the update attributes from the device itself and writes them
        into the node cache - for a device that does not report progress on
        its own (design 7.2)."""
        upstream = self._require_upstream()
        values = await upstream.read_attribute(int(address), list(self._FIRMWARE_REFRESH_PATHS))
        node = self._node(address)
        if node is not None:
            node.node_data.attributes.update(values)

    async def check_update(self, address: str) -> UpdateOffer | None:
        result = await self._require_upstream().check_node_update(int(address))
        if result is None:
            return None
        return UpdateOffer(
            software_version=int(result.software_version),
            software_version_string=str(result.software_version_string),
            min_applicable=int(result.min_applicable_software_version),
            max_applicable=int(result.max_applicable_software_version),
            release_notes_url=result.release_notes_url or None,
            source=str(getattr(result.update_source, "value", result.update_source)),
        )

    async def start_update(self, address: str, software_version: int) -> None:
        await self._require_upstream().update_node(int(address), software_version)
```

`Final` on a class attribute: if mypy objects, drop the annotation and make it a module-level `_FIRMWARE_REFRESH_PATHS: Final = (...)` tuple instead, passing `list(...)`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/matter/test_client_firmware.py tests/matter/test_client.py -q`
Expected: all pass.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/loxmatter/matter/client.py tests/matter/test_client_firmware.py
git commit -m "feat(matter): check and start firmware updates through matter-server

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `FirmwareChecker`

**Files:**
- Create: `src/loxmatter/firmware/check.py`
- Create: `tests/firmware/firmware_fakes.py` (shared fake, imported by Tasks 4–7; module name is unique across the suite)
- Test: `tests/firmware/test_firmware_check.py`
- Modify: `src/loxmatter/i18n/strings.yaml` (key `api.firmware.check_timeout`)

**Interfaces:**
- Consumes: `FirmwareSource`, `FirmwareFacts`, `UpdateOffer` (Task 1); `Store.devices()`, `Store.device()`, `Store.firmware_status` (Task 2).
- Produces:
  - `@dataclass(frozen=True) CheckProgress(running: bool, checked: int, total: int)`
  - `FirmwareChecker(store: Store, source_for: Callable[[], FirmwareSource | None], *, now: Callable[[], str] = now_iso, per_device_timeout: float = 60.0)`
  - `.progress -> CheckProgress`, `.start_all() -> CheckProgress`, `async .check_all() -> None`, `async .check_one(device_id: int) -> None`

- [ ] **Step 1: Write the shared fake**

`tests/firmware/firmware_fakes.py`:

```python
"""A `FirmwareSource` without a server, for the firmware tests.

`facts` and `offers` are keyed by address and can be changed by a test
while a job runs; `fail_check`/`fail_start` make the next call raise."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.sources.firmware import FirmwareFacts, UpdateOffer

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"

# Measured on the test Pi, 2026-09-30 (design section 3).
KAJPLATS_OFFER = UpdateOffer(16908288, "1.2.0", 0, 16908287, None, "main-net-dcl")
BILRESA_OFFER = UpdateOffer(17367055, "1.9.15", 17301509, 17367054, None, "main-net-dcl")


def idle_facts(software_version: int, text: str, *, available: bool = True) -> FirmwareFacts:
    return FirmwareFacts(
        available=available,
        has_requestor=True,
        software_version=software_version,
        software_version_string=text,
        spec_version=0x01040000,
        update_state=1,
        update_progress=None,
    )


def register(store: Store, fixture: str) -> tuple[int, str]:
    raw = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    snapshot = NodeSnapshot.from_raw(raw["node_id"], raw)
    return store.register_device(snapshot), snapshot.address


class FakeFirmwareSource:
    technology = "matter"

    def __init__(self) -> None:
        self.connected = True
        self.supported = True
        self.facts: dict[str, FirmwareFacts] = {}
        self.offers: dict[str, UpdateOffer | None] = {}
        self.fail_check: Exception | None = None
        self.fail_start: Exception | None = None
        self.hang_check = False
        self.checked: list[str] = []
        self.started: list[tuple[str, int]] = []
        self.refreshed: list[str] = []
        self.followed: list[str] = []

    def firmware_supported(self) -> bool:
        return self.supported

    def firmware_facts(self, address: str) -> FirmwareFacts | None:
        return self.facts.get(address)

    async def refresh_firmware_facts(self, address: str) -> None:
        self.refreshed.append(address)

    async def check_update(self, address: str) -> UpdateOffer | None:
        self.checked.append(address)
        if self.hang_check:
            await asyncio.Event().wait()
        if self.fail_check is not None:
            raise self.fail_check
        return self.offers.get(address)

    async def start_update(self, address: str, software_version: int) -> None:
        self.started.append((address, software_version))
        if self.fail_start is not None:
            raise self.fail_start

    async def follow(self, address: str, *, seed_even_without_new_paths: bool = False) -> None:
        self.followed.append(address)

    def set_state(self, address: str, update_state: int, progress: int | None = None) -> None:
        self.facts[address] = replace(
            self.facts[address], update_state=update_state, update_progress=progress
        )

    def finish(self, address: str, software_version: int, text: str) -> None:
        self.facts[address] = replace(
            self.facts[address],
            update_state=1,
            update_progress=None,
            software_version=software_version,
            software_version_string=text,
        )
```

- [ ] **Step 2: Write the failing tests**

`tests/firmware/test_firmware_check.py`:

```python
"""Checking for firmware updates (design 2026-09-30, section 6.1)."""

import asyncio
from dataclasses import replace

from firmware_fakes import (
    BILRESA_OFFER,
    KAJPLATS_OFFER,
    FakeFirmwareSource,
    idle_facts,
    register,
)

from loxmatter import i18n
from loxmatter.firmware.check import FirmwareChecker
from loxmatter.model.store import Store


def _setup(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    lamp_id, lamp = register(store, "ikea_kajplats_cws_lamp.json")
    button_id, button = register(store, "ikea_bilresa_button.json")
    source = FakeFirmwareSource()
    source.facts[lamp] = idle_facts(16842752, "1.1.0")
    source.facts[button] = idle_facts(17301509, "1.8.5")
    checker = FirmwareChecker(store, lambda: source, now=lambda: "2026-09-30T06:00:00+00:00")
    return store, source, checker, (lamp_id, lamp), (button_id, button)


async def test_check_all_stores_every_answer(tmp_path):
    store, source, checker, (lamp_id, lamp), (button_id, button) = _setup(tmp_path)
    source.offers[lamp] = KAJPLATS_OFFER
    source.offers[button] = None

    await checker.check_all()

    assert store.firmware_status.get(lamp_id).offer == KAJPLATS_OFFER
    assert store.firmware_status.get(button_id).offer is None
    assert store.firmware_status.get(button_id).checked_at == "2026-09-30T06:00:00+00:00"
    assert checker.progress.running is False
    assert (checker.progress.checked, checker.progress.total) == (2, 2)


async def test_a_device_without_requestor_is_not_asked(tmp_path):
    store, source, checker, (lamp_id, lamp), _ = _setup(tmp_path)
    source.facts[lamp] = replace(idle_facts(16842752, "1.1.0"), has_requestor=False)
    await checker.check_all()
    assert lamp not in source.checked
    assert store.firmware_status.get(lamp_id) is None


async def test_an_offline_device_is_skipped_and_keeps_its_state(tmp_path):
    store, source, checker, (lamp_id, lamp), _ = _setup(tmp_path)
    store.firmware_status.record_check(lamp_id, KAJPLATS_OFFER, "yesterday")
    source.facts[lamp] = idle_facts(16842752, "1.1.0", available=False)
    await checker.check_all()
    assert lamp not in source.checked
    assert store.firmware_status.get(lamp_id).checked_at == "yesterday"


async def test_an_offer_the_device_already_has_is_not_stored(tmp_path):
    store, source, checker, (lamp_id, lamp), _ = _setup(tmp_path)
    source.facts[lamp] = idle_facts(16908288, "1.2.0")
    source.offers[lamp] = KAJPLATS_OFFER
    await checker.check_all()
    assert store.firmware_status.get(lamp_id).offer is None


async def test_a_failed_check_keeps_the_previous_offer(tmp_path):
    store, source, checker, (lamp_id, _), _ = _setup(tmp_path)
    store.firmware_status.record_check(lamp_id, KAJPLATS_OFFER, "yesterday")
    source.fail_check = RuntimeError("DCL unreachable")
    await checker.check_all()
    status = store.firmware_status.get(lamp_id)
    assert status.offer == KAJPLATS_OFFER
    assert status.check_error == "DCL unreachable"


async def test_a_hanging_device_times_out(tmp_path):
    store, source, _, (lamp_id, _), _ = _setup(tmp_path)
    source.hang_check = True
    checker = FirmwareChecker(store, lambda: source, now=lambda: "t", per_device_timeout=0.01)
    await checker.check_all()
    assert store.firmware_status.get(lamp_id).check_error == i18n.t("api.firmware.check_timeout")


async def test_a_second_start_joins_the_running_check(tmp_path):
    _, source, checker, _, _ = _setup(tmp_path)
    source.hang_check = True
    first = checker.start_all()
    second = checker.start_all()
    assert first.running and second.running
    await asyncio.sleep(0)
    assert len(source.checked) == 1  # only one run is asking
    checker.cancel()


async def test_check_one_asks_only_that_device(tmp_path):
    store, source, checker, (lamp_id, lamp), (_, button) = _setup(tmp_path)
    source.offers[lamp] = KAJPLATS_OFFER
    await checker.check_one(lamp_id)
    assert source.checked == [lamp]
    assert store.firmware_status.get(lamp_id).offer == KAJPLATS_OFFER


async def test_nothing_happens_without_a_supported_source(tmp_path):
    store, source, checker, _, _ = _setup(tmp_path)
    source.supported = False
    await checker.check_all()
    assert source.checked == []
    assert store.firmware_status.all() == {}


async def test_the_bilresa_offer_is_stored_with_its_window(tmp_path):
    store, source, checker, _, (button_id, button) = _setup(tmp_path)
    source.offers[button] = BILRESA_OFFER
    await checker.check_all()
    assert store.firmware_status.get(button_id).offer.min_applicable == 17301509
```

`firmware_fakes` is importable because pytest puts `tests/firmware/` on `sys.path` (rootdir-relative, no `__init__.py`), the same way `tests/api/conftest.py`'s `load_snapshot` is imported.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/firmware/test_firmware_check.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.firmware.check'`.

- [ ] **Step 4: Add the i18n key**

`src/loxmatter/i18n/strings.yaml`, in the `api.` block (alphabetical position near `api.errors.*`):

```yaml
api.firmware.check_timeout:
  en: "The device or the update directory did not answer within 60 seconds."
  de: "Das Gerät oder das Update-Verzeichnis hat nicht innerhalb von 60 Sekunden geantwortet."
```

- [ ] **Step 5: Implement `check.py`**

```python
"""Asking for firmware offers (design 2026-09-30, section 6.1).

One code path for the daily run, the overview button and the dialog button.
Only one `check_all` runs at a time: a second start joins the first, which
covers a double click and a second browser."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

from loxmatter import i18n
from loxmatter.model.store import StoredDevice, Store
from loxmatter.sources.firmware import FirmwareSource
from loxmatter.timestamps import now_iso

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CheckProgress:
    running: bool
    checked: int
    total: int


def describe_failure(exc: BaseException) -> str:
    """A failure as the overview shows it - the exception's own text, or its
    type when it has none."""
    if isinstance(exc, TimeoutError):
        return i18n.t("api.firmware.check_timeout")
    return str(exc) or type(exc).__name__


class FirmwareChecker:
    def __init__(
        self,
        store: Store,
        source_for: Callable[[], FirmwareSource | None],
        *,
        now: Callable[[], str] = now_iso,
        per_device_timeout: float = 60.0,
    ) -> None:
        self._store = store
        self._source_for = source_for
        self._now = now
        self._timeout = per_device_timeout
        self._task: asyncio.Task[None] | None = None
        self._checked = 0
        self._total = 0

    @property
    def progress(self) -> CheckProgress:
        running = self._task is not None and not self._task.done()
        return CheckProgress(running=running, checked=self._checked, total=self._total)

    def start_all(self) -> CheckProgress:
        """Starts a check of every device in the background, or joins the
        running one. Returns at once."""
        if self._task is None or self._task.done():
            self._task = asyncio.ensure_future(self._run_all())
        return self.progress

    async def check_all(self) -> None:
        """Like `start_all`, but waits until the run has ended."""
        self.start_all()
        assert self._task is not None
        await asyncio.shield(self._task)

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def check_one(self, device_id: int) -> None:
        source = self._supported_source()
        if source is None:
            return
        await self._check_device(source, self._store.device(device_id))

    def _supported_source(self) -> FirmwareSource | None:
        source = self._source_for()
        if source is None or not source.firmware_supported():
            return None
        return source

    async def _run_all(self) -> None:
        source = self._supported_source()
        if source is None:
            self._checked = self._total = 0
            return
        devices = [d for d in self._store.devices() if d.technology == source.technology]
        self._checked, self._total = 0, len(devices)
        for device in devices:
            await self._check_device(source, device)
            self._checked += 1

    async def _check_device(self, source: FirmwareSource, device: StoredDevice) -> None:
        facts = source.firmware_facts(device.address)
        if facts is None or not facts.has_requestor or not facts.available:
            return
        try:
            offer = await asyncio.wait_for(source.check_update(device.address), self._timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.info("firmware check for device %s failed: %s", device.id, exc)
            with contextlib.suppress(Exception):
                self._store.firmware_status.record_check_error(
                    device.id, describe_failure(exc), self._now()
                )
            return
        if (
            offer is not None
            and facts.software_version is not None
            and offer.software_version <= facts.software_version
        ):
            offer = None
        self._store.firmware_status.record_check(device.id, offer, self._now())
```

`StoredDevice` must be importable from `loxmatter.model.store` (it is defined there). `asyncio.wait_for` raises `TimeoutError` in Python 3.11+.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/firmware -q && uv run pytest tests/test_i18n.py -q`
Expected: all pass.

- [ ] **Step 7: Lint, type-check, language check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/firmware/check.py src/loxmatter/i18n/strings.yaml tests/firmware
git commit -m "feat(firmware): check every device for updates, one run at a time

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `FirmwareJobs` — install, follow, resume

**Files:**
- Create: `src/loxmatter/firmware/job.py`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/firmware/test_firmware_job.py`

**Interfaces:**
- Consumes: Tasks 1, 2; `describe_failure` from `check.py` (Task 4).
- Produces:
  - Exceptions: `FirmwareUnsupportedError`, `FirmwareBusyError(device_id: int)`, `OfferChangedError`, `DeviceOfflineError`
  - `@dataclass(frozen=True) JobTiming(poll: float = 2.0, reread_after: float = 30.0, idle_fail_after: float = 120.0, stall_after: float = 900.0, give_up_after: float = 10800.0)`
  - `FirmwareJobs(store, source_for, *, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, now: Callable[[], str] = now_iso, timing: JobTiming = JobTiming())`
  - `.running_device_id -> int | None`, `.start(device_id: int, software_version: int) -> None`, `.resume_all() -> int`, `async .wait() -> None`, `async .stop() -> None`

- [ ] **Step 1: Write the failing tests**

The job loop is driven by a fake clock and a fake `sleep` that advances it, so a three-hour job runs in milliseconds. The `UpdateState` sequence is the Matter spec's (Idle → Querying → Downloading → Applying → Idle); Task 10 replaces it with the recorded one.

`tests/firmware/test_firmware_job.py`:

```python
"""Installing one firmware update (design 2026-09-30, section 7)."""

import asyncio

import pytest
from firmware_fakes import KAJPLATS_OFFER, FakeFirmwareSource, idle_facts, register

from loxmatter.firmware import states
from loxmatter.firmware.job import (
    DeviceOfflineError,
    FirmwareBusyError,
    FirmwareJobs,
    FirmwareUnsupportedError,
    JobTiming,
    OfferChangedError,
)
from loxmatter.model.store import Store


class Clock:
    """A clock the fake sleep advances; `script` runs source changes at
    given times, like a device reporting its state."""

    def __init__(self) -> None:
        self.t = 0.0
        self.script: list[tuple[float, object]] = []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds
        while self.script and self.script[0][0] <= self.t:
            _, action = self.script.pop(0)
            action()  # type: ignore[operator]
        await asyncio.sleep(0)


def _setup(tmp_path):
    store = Store(tmp_path / "t.sqlite")
    lamp_id, lamp = register(store, "ikea_kajplats_cws_lamp.json")
    store.firmware_status.record_check(lamp_id, KAJPLATS_OFFER, "t0")
    source = FakeFirmwareSource()
    source.facts[lamp] = idle_facts(16842752, "1.1.0")
    clock = Clock()
    jobs = FirmwareJobs(
        store, lambda: source, clock=clock, sleep=clock.sleep, now=lambda: "now", timing=JobTiming()
    )
    return store, source, clock, jobs, lamp_id, lamp


async def test_a_successful_update_ends_on_the_new_version(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    clock.script = [
        (2, lambda: source.set_state(lamp, 2)),
        (4, lambda: source.set_state(lamp, 4, 10)),
        (60, lambda: source.set_state(lamp, 4, 60)),
        (600, lambda: source.set_state(lamp, 5)),
        (660, lambda: source.finish(lamp, 16908288, "1.2.0")),
    ]
    jobs.start(lamp_id, 16908288)
    await jobs.wait()

    assert source.started == [(lamp, 16908288)]
    status = store.firmware_status.get(lamp_id)
    assert status.job_state is None and status.offer is None
    assert store.device(lamp_id).firmware == "1.2.0"
    assert source.followed == [lamp]
    assert jobs.running_device_id is None


async def test_progress_is_written_while_transferring(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    seen = []
    clock.script = [
        (2, lambda: source.set_state(lamp, 4, 43)),
        (4, lambda: seen.append(store.firmware_status.get(lamp_id))),
        (6, lambda: source.set_state(lamp, 5)),
        (8, lambda: seen.append(store.firmware_status.get(lamp_id))),
        (10, lambda: source.finish(lamp, 16908288, "1.2.0")),
    ]
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    assert (seen[0].job_state, seen[0].job_progress) == (states.TRANSFERRING, 43)
    assert (seen[1].job_state, seen[1].job_progress) == (states.APPLYING, None)


async def test_back_to_idle_without_new_version_fails_after_two_minutes(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    clock.script = [(2, lambda: source.set_state(lamp, 4, 10)), (10, lambda: source.set_state(lamp, 1))]
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    status = store.firmware_status.get(lamp_id)
    assert status.job_state == states.FAILED
    assert 130 <= clock.t <= 140
    assert status.offer == KAJPLATS_OFFER  # still offered, can be retried


async def test_no_change_for_fifteen_minutes_is_stalled_and_recovers(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    seen = []
    clock.script = [
        (2, lambda: source.set_state(lamp, 4, 10)),
        (910, lambda: seen.append(store.firmware_status.get(lamp_id).job_state)),
        (912, lambda: source.set_state(lamp, 4, 11)),
        (916, lambda: seen.append(store.firmware_status.get(lamp_id).job_state)),
        (920, lambda: source.finish(lamp, 16908288, "1.2.0")),
    ]
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    assert seen == [states.STALLED, states.TRANSFERRING]


async def test_a_quiet_device_is_read_after_thirty_seconds(tmp_path):
    _, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    clock.script = [(2, lambda: source.set_state(lamp, 4, 10)), (70, lambda: source.finish(lamp, 16908288, "1.2.0"))]
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    assert source.refreshed and source.refreshed[0] == lamp


async def test_the_job_gives_up_after_three_hours(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    ticker = [(t, lambda t=t: source.set_state(lamp, 4, t // 600)) for t in range(2, 11000, 600)]
    clock.script = ticker
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    assert store.firmware_status.get(lamp_id).job_state == states.FAILED
    assert 10800 <= clock.t <= 10810


async def test_losing_matter_server_interrupts_the_job(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    clock.script = [(2, lambda: source.set_state(lamp, 4, 10)), (6, lambda: setattr(source, "connected", False))]
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    assert store.firmware_status.get(lamp_id).job_state == states.INTERRUPTED


async def test_update_node_raising_fails_the_job(tmp_path):
    store, source, _, jobs, lamp_id, _ = _setup(tmp_path)
    source.fail_start = RuntimeError("node refused")
    jobs.start(lamp_id, 16908288)
    await jobs.wait()
    status = store.firmware_status.get(lamp_id)
    assert status.job_state == states.FAILED and status.job_error == "node refused"


async def test_only_one_install_runs_at_a_time(tmp_path):
    store, source, _, jobs, lamp_id, lamp = _setup(tmp_path)
    button_id, button = register(store, "ikea_bilresa_button.json")
    jobs.start(lamp_id, 16908288)
    with pytest.raises(FirmwareBusyError) as raised:
        jobs.start(button_id, 1)
    assert raised.value.device_id == lamp_id
    await jobs.stop()


async def test_a_version_other_than_the_offer_is_refused(tmp_path):
    _, _, _, jobs, lamp_id, _ = _setup(tmp_path)
    with pytest.raises(OfferChangedError):
        jobs.start(lamp_id, 16908289)


async def test_an_offline_device_is_refused(tmp_path):
    _, source, _, jobs, lamp_id, lamp = _setup(tmp_path)
    source.facts[lamp] = idle_facts(16842752, "1.1.0", available=False)
    with pytest.raises(DeviceOfflineError):
        jobs.start(lamp_id, 16908288)


async def test_an_unsupported_server_is_refused(tmp_path):
    _, source, _, jobs, lamp_id, _ = _setup(tmp_path)
    source.supported = False
    with pytest.raises(FirmwareUnsupportedError):
        jobs.start(lamp_id, 16908288)


async def test_resume_picks_up_a_transfer_after_a_loxmatter_restart(tmp_path):
    store, source, clock, jobs, lamp_id, lamp = _setup(tmp_path)
    store.firmware_status.start_job(lamp_id, "before restart")
    source.set_state(lamp, 4, 50)
    clock.script = [(10, lambda: source.finish(lamp, 16908288, "1.2.0"))]
    assert jobs.resume_all() == 1
    assert jobs.running_device_id == lamp_id
    await jobs.wait()
    assert source.started == []  # no second update_node
    assert store.device(lamp_id).firmware == "1.2.0"


async def test_resume_marks_a_job_whose_device_went_idle_as_interrupted(tmp_path):
    store, source, _, jobs, lamp_id, _ = _setup(tmp_path)
    store.firmware_status.start_job(lamp_id, "before restart")
    assert jobs.resume_all() == 0
    assert store.firmware_status.get(lamp_id).job_state == states.INTERRUPTED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/firmware/test_firmware_job.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.firmware.job'`.

- [ ] **Step 3: Add the i18n keys**

```yaml
api.firmware.job_no_new_version:
  en: "The device went back to idle without reporting the new version. Try again later."
  de: "Das Gerät ist in den Ruhezustand zurückgekehrt, ohne die neue Version zu melden. Versuchen Sie es später erneut."
api.firmware.job_gave_up:
  en: "The update did not finish within three hours and was abandoned."
  de: "Das Update wurde nicht innerhalb von drei Stunden abgeschlossen und abgebrochen."
```

- [ ] **Step 4: Implement `job.py`**

```python
"""Installing one firmware update and following it (design 2026-09-30, 7).

One install at a time on the Matter source: `_task` is the lock. The
transfer runs between matter-server and the device, so this job only
watches the node cache; a restart of loxmatter loses nothing that
`resume_all` cannot pick up again."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loxmatter import i18n
from loxmatter.firmware import states
from loxmatter.firmware.check import describe_failure
from loxmatter.model.store import Store
from loxmatter.sources.firmware import FirmwareFacts, FirmwareSource
from loxmatter.timestamps import now_iso

logger = logging.getLogger(__name__)


class FirmwareUnsupportedError(RuntimeError):
    """No source, or a server below schema 10."""


class FirmwareBusyError(RuntimeError):
    def __init__(self, device_id: int) -> None:
        super().__init__(f"firmware update running on device {device_id}")
        self.device_id = device_id


class OfferChangedError(RuntimeError):
    """The requested version is not the stored offer (a stale browser tab)."""


class DeviceOfflineError(RuntimeError):
    """The device is not reachable right now."""


@dataclass(frozen=True)
class JobTiming:
    poll: float = 2.0
    reread_after: float = 30.0
    idle_fail_after: float = 120.0
    stall_after: float = 900.0
    give_up_after: float = 10800.0


class FirmwareJobs:
    def __init__(
        self,
        store: Store,
        source_for: Callable[[], FirmwareSource | None],
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], str] = now_iso,
        timing: JobTiming = JobTiming(),
    ) -> None:
        self._store = store
        self._source_for = source_for
        self._clock = clock
        self._sleep = sleep
        self._now = now
        self._timing = timing
        self._task: asyncio.Task[None] | None = None
        self._device_id: int | None = None

    @property
    def running_device_id(self) -> int | None:
        if self._task is None or self._task.done():
            return None
        return self._device_id

    def _supported_source(self) -> FirmwareSource:
        source = self._source_for()
        if source is None or not source.firmware_supported():
            raise FirmwareUnsupportedError()
        return source

    def start(self, device_id: int, software_version: int) -> None:
        """Starts an install in the background. Raises before anything is
        sent: busy, unsupported, `UnknownDeviceError`, offer changed, offline."""
        running = self.running_device_id
        if running is not None:
            raise FirmwareBusyError(running)
        source = self._supported_source()
        device = self._store.device(device_id)
        status = self._store.firmware_status.get(device_id)
        if status is None or status.offer is None or status.offer.software_version != software_version:
            raise OfferChangedError()
        facts = source.firmware_facts(device.address)
        if facts is None or not facts.available:
            raise DeviceOfflineError()
        self._store.firmware_status.start_job(device_id, self._now())
        self._device_id = device_id
        self._task = asyncio.ensure_future(
            self._install(source, device_id, device.address, software_version)
        )

    def resume_all(self) -> int:
        """After a start of loxmatter: follows a device whose transfer is
        still running, and marks every other remembered job interrupted.
        Returns how many jobs it resumed (0 or 1)."""
        source = self._source_for()
        if source is None or not source.firmware_supported():
            return 0
        resumed = 0
        by_id = {device.id: device for device in self._store.devices()}
        for device_id, status in self._store.firmware_status.all().items():
            if status.job_state not in states.ACTIVE_JOB_STATES:
                continue
            device = by_id.get(device_id)
            facts = None if device is None else source.firmware_facts(device.address)
            busy = facts is not None and states.job_state_for(facts.update_state) is not None
            if device is None or not busy or status.offer is None or resumed:
                self._store.firmware_status.end_job(
                    device_id, states.INTERRUPTED, None, self._now()
                )
                continue
            self._device_id = device_id
            self._task = asyncio.ensure_future(
                self._follow(source, device_id, device.address, status.offer.software_version)
            )
            resumed = 1
        return resumed

    async def wait(self) -> None:
        if self._task is not None:
            await asyncio.shield(self._task)

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _install(
        self, source: FirmwareSource, device_id: int, address: str, target: int
    ) -> None:
        # `update_node` may return at once or only after the transfer
        # (design 3, open until the first real install), so it runs next to
        # the follow loop rather than before it.
        starter = asyncio.ensure_future(source.start_update(address, target))
        try:
            await self._follow(source, device_id, address, target, starter=starter)
        finally:
            if not starter.done():
                starter.cancel()

    async def _follow(
        self,
        source: FirmwareSource,
        device_id: int,
        address: str,
        target: int,
        *,
        starter: asyncio.Future[None] | None = None,
    ) -> None:
        timing = self._timing
        started = self._clock()
        last_signature: tuple[int | None, int | None] | None = None
        last_change = started
        last_reread = started
        idle_since: float | None = None
        seen_busy = False
        while True:
            now = self._clock()
            if starter is not None and starter.done() and not starter.cancelled():
                failure = starter.exception()
                if failure is not None:
                    self._end(device_id, states.FAILED, describe_failure(failure))
                    return
            if not source.connected:
                self._end(device_id, states.INTERRUPTED, None)
                return
            facts = source.firmware_facts(address)
            if facts is not None and facts.software_version is not None and facts.software_version >= target:
                await self._succeed(source, device_id, address, facts)
                return
            update_state = None if facts is None else facts.update_state
            progress = None if facts is None else facts.update_progress
            signature = (update_state, progress)
            if signature != last_signature:
                last_signature, last_change = signature, now
            job_state = states.job_state_for(update_state)
            if job_state is not None:
                seen_busy, idle_since = True, None
            elif seen_busy:
                idle_since = now if idle_since is None else idle_since
                if now - idle_since >= timing.idle_fail_after:
                    self._end(device_id, states.FAILED, i18n.t("api.firmware.job_no_new_version"))
                    return
            if now - started >= timing.give_up_after:
                self._end(device_id, states.FAILED, i18n.t("api.firmware.job_gave_up"))
                return
            shown = states.STALLED if now - last_change >= timing.stall_after else (
                job_state or states.TRANSFERRING
            )
            self._store.firmware_status.update_job(
                device_id, shown, progress if shown == states.TRANSFERRING else None, self._now()
            )
            if now - last_change >= timing.reread_after and now - last_reread >= timing.reread_after:
                last_reread = now
                try:
                    await source.refresh_firmware_facts(address)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.info("reading firmware state of device %s failed: %s", device_id, exc)
            await self._sleep(timing.poll)

    def _end(self, device_id: int, state: str, error: str | None) -> None:
        self._store.firmware_status.end_job(device_id, state, error, self._now())

    async def _succeed(
        self, source: FirmwareSource, device_id: int, address: str, facts: FirmwareFacts
    ) -> None:
        self._store.set_firmware_details(device_id, facts.software_version_string, facts.spec_version)
        self._store.firmware_status.drop_offer(device_id)
        self._store.firmware_status.end_job(device_id, None, None, self._now())
        # An update can renumber endpoints (the Tasmota plug did); re-reading
        # the structure is what lets "Changed since export" appear.
        try:
            await source.follow(address, seed_even_without_new_paths=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("re-reading device %s after its update failed: %s", device_id, exc)
```

Note on `test_a_quiet_device_is_read_after_thirty_seconds`: with `set_state(lamp, 4, 10)` at 2 s and nothing until 70 s, the loop re-reads at ≥32 s. Note on `test_the_job_gives_up_after_three_hours`: the ticker changes the progress every 600 s, so the job never stalls into `failed` early and ends on the 3-hour bound.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/firmware -q`
Expected: all pass. If `test_a_successful_update_ends_on_the_new_version` hangs, the fake `sleep` is not advancing the clock — check `Clock.sleep`.

- [ ] **Step 6: Lint, type-check, language check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/firmware/job.py src/loxmatter/i18n/strings.yaml tests/firmware/test_firmware_job.py
git commit -m "feat(firmware): install one update at a time and follow it to the new version

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Daily schedule

**Files:**
- Create: `src/loxmatter/firmware/schedule.py`
- Test: `tests/firmware/test_firmware_schedule.py`

**Interfaces:**
- Consumes: `FirmwareChecker.check_all` (Task 4), `FirmwareSettingsStore` (Task 2).
- Produces: `DAILY_CHECK_AT: Final = time(6, 0)`, `seconds_until(now: datetime, at: time) -> float`, `async run_daily(checker, settings, *, now=..., sleep=asyncio.sleep, at=DAILY_CHECK_AT) -> None`

- [ ] **Step 1: Write the failing tests**

```python
"""The daily firmware check (design 2026-09-30, section 6.2)."""

import asyncio
from datetime import datetime, time, timedelta, timezone

import pytest

from loxmatter.firmware.schedule import seconds_until, run_daily

TZ = timezone(timedelta(hours=2))


def test_seconds_until_later_today():
    assert seconds_until(datetime(2026, 9, 30, 5, 0, tzinfo=TZ), time(6, 0)) == 3600


def test_seconds_until_tomorrow_when_the_time_has_passed():
    assert seconds_until(datetime(2026, 9, 30, 7, 0, tzinfo=TZ), time(6, 0)) == 23 * 3600


def test_seconds_until_exactly_now_is_tomorrow():
    assert seconds_until(datetime(2026, 9, 30, 6, 0, tzinfo=TZ), time(6, 0)) == 24 * 3600


class _Stop(Exception):
    pass


class _Settings:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def get_daily_check_enabled(self) -> bool:
        return self.enabled


class _Checker:
    def __init__(self) -> None:
        self.runs = 0

    async def check_all(self) -> None:
        self.runs += 1


def _driver(days: int):
    """A clock that jumps to the requested wake-up time, for `days` days."""
    state = {"now": datetime(2026, 9, 30, 5, 0, tzinfo=TZ), "sleeps": 0}

    async def sleep(seconds: float) -> None:
        state["sleeps"] += 1
        if state["sleeps"] > days:
            raise _Stop
        state["now"] += timedelta(seconds=seconds)
        await asyncio.sleep(0)

    return state, (lambda: state["now"]), sleep


async def test_runs_once_a_day_at_six():
    state, now, sleep = _driver(days=3)
    checker = _Checker()
    with pytest.raises(_Stop):
        await run_daily(checker, _Settings(True), now=now, sleep=sleep)
    assert checker.runs == 3
    assert state["now"].time() == time(6, 0)


async def test_does_not_run_when_switched_off():
    _, now, sleep = _driver(days=2)
    checker = _Checker()
    with pytest.raises(_Stop):
        await run_daily(checker, _Settings(False), now=now, sleep=sleep)
    assert checker.runs == 0


async def test_a_failing_check_does_not_end_the_schedule():
    _, now, sleep = _driver(days=2)

    class Failing(_Checker):
        async def check_all(self) -> None:
            self.runs += 1
            raise RuntimeError("boom")

    checker = Failing()
    with pytest.raises(_Stop):
        await run_daily(checker, _Settings(True), now=now, sleep=sleep)
    assert checker.runs == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/firmware/test_firmware_schedule.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""The daily firmware check at 06:00 local time (design 2026-09-30, 6.2).

It only ever checks; installing always takes a click. A run missed while
the bridge was down is not caught up."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta
from typing import Final, Protocol

logger = logging.getLogger(__name__)

DAILY_CHECK_AT: Final = time(6, 0)


class _Checker(Protocol):
    async def check_all(self) -> None: ...


class _Settings(Protocol):
    def get_daily_check_enabled(self) -> bool: ...


def seconds_until(now: datetime, at: time) -> float:
    """Seconds from `now` to the next `at`; exactly `at` counts as tomorrow,
    so a wake-up at 06:00:00 never schedules a second run for 06:00:00."""
    target = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _local_now() -> datetime:
    return datetime.now().astimezone()


async def run_daily(
    checker: _Checker,
    settings: _Settings,
    *,
    now: Callable[[], datetime] = _local_now,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    at: time = DAILY_CHECK_AT,
) -> None:
    while True:
        await sleep(seconds_until(now(), at))
        if not settings.get_daily_check_enabled():
            continue
        try:
            await checker.check_all()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("the daily firmware check failed")
```

- [ ] **Step 4: Run, lint, commit**

```bash
uv run pytest tests/firmware -q
uv run ruff check . && uv run ruff format --check . && uv run mypy
git add src/loxmatter/firmware/schedule.py tests/firmware/test_firmware_schedule.py
git commit -m "feat(firmware): check for updates every day at 06:00

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `FirmwareService`, API routes, expert route, app wiring

**Files:**
- Create: `src/loxmatter/firmware/service.py`
- Create: `src/loxmatter/api/firmware.py`
- Modify: `src/loxmatter/api/models.py` (new models; `DeviceExpertOut.matter_version`)
- Modify: `src/loxmatter/api/devices.py:380-392` (expert route)
- Modify: `src/loxmatter/loxone/server.py` (`build_app` parameter + router)
- Modify: `src/loxmatter/cli.py` (~lines 845-960)
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_firmware_api.py`; extend `tests/api/test_devices.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces:
  - `FirmwareService(store: Store, sources: Sources, *, checker: FirmwareChecker | None = None, jobs: FirmwareJobs | None = None)` with `.checker`, `.jobs`, `.source_for() -> FirmwareSource | None`, `.supported() -> bool`, `.device_out(device: StoredDevice) -> FirmwareDeviceOut`, `.overview() -> FirmwareOverviewOut`
  - `build_firmware_router(store: Store, firmware: FirmwareService) -> APIRouter`
  - `build_app(..., firmware: FirmwareService | None = None)`
  - JSON of `GET /api/firmware` (the WebUI in Task 8 reads exactly these names):

```json
{
  "supported": true,
  "daily_check_enabled": true,
  "last_checked_at": "2026-09-30T06:00:00+00:00",
  "check": {"running": false, "checked": 9, "total": 9},
  "updating_device_id": null,
  "devices": [
    {
      "device_id": 1, "label": "KAJPLATS", "room": "Kitchen", "technology": "matter",
      "matter_version": "1.4", "installed": "1.1.0", "online": true,
      "state": "available", "progress": null,
      "offer": {"version": 16908288, "version_string": "1.2.0", "release_notes_url": null, "source": "main-net-dcl"},
      "checked_at": "2026-09-30T06:00:00+00:00", "check_error": null, "job_error": null
    }
  ]
}
```

- [ ] **Step 1: Write the failing API tests**

`tests/api/test_firmware_api.py`:

```python
"""The firmware routes (design 2026-09-30, section 9.1)."""

import sys
from pathlib import Path

import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter import i18n
from loxmatter.firmware.service import FirmwareService
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store
from loxmatter.sources import Sources

sys.path.insert(0, str(Path(__file__).parents[1] / "firmware"))
from firmware_fakes import (  # noqa: E402
    KAJPLATS_OFFER,
    FakeFirmwareSource,
    idle_facts,
    register,
)


@pytest.fixture
async def firmware_api(tmp_path, no_invoke, fake_runtime, fake_otbr):
    store = Store(tmp_path / "t.sqlite")
    lamp_id, lamp = register(store, "ikea_kajplats_cws_lamp.json")
    source = FakeFirmwareSource()
    source.facts[lamp] = idle_facts(16842752, "1.1.0")
    source.offers[lamp] = KAJPLATS_OFFER
    firmware = FirmwareService(store, Sources([source]))
    app = build_app(
        store,
        no_invoke,
        fake_runtime(store),
        sources=Sources([source]),
        thread_dataset_source=fake_otbr,
        firmware=firmware,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, store, source, firmware, lamp_id, lamp
    await firmware.jobs.stop()
    firmware.checker.cancel()
    store.close()


async def test_overview_lists_the_device_unchecked(firmware_api):
    client, _, _, _, lamp_id, _ = firmware_api
    data = (await client.get("/api/firmware")).json()
    assert data["supported"] is True
    assert data["daily_check_enabled"] is True
    row = next(d for d in data["devices"] if d["device_id"] == lamp_id)
    assert row["state"] == "unchecked"
    assert row["installed"] == "1.1.0"
    assert row["matter_version"] == "1.4"
    assert row["online"] is True


async def test_check_all_returns_at_once_and_fills_the_overview(firmware_api):
    client, _, _, firmware, lamp_id, _ = firmware_api
    response = await client.post("/api/firmware/check")
    assert response.status_code == 202
    await firmware.checker.check_all()
    row = next(d for d in (await client.get("/api/firmware")).json()["devices"] if d["device_id"] == lamp_id)
    assert row["state"] == "available"
    assert row["offer"]["version_string"] == "1.2.0"


async def test_check_one(firmware_api):
    client, _, source, _, lamp_id, lamp = firmware_api
    response = await client.post(f"/api/devices/{lamp_id}/firmware/check")
    assert response.status_code == 200
    assert response.json()["state"] == "available"
    assert source.checked == [lamp]


async def test_install_starts_the_job(firmware_api):
    client, store, source, firmware, lamp_id, lamp = firmware_api
    await client.post(f"/api/devices/{lamp_id}/firmware/check")
    response = await client.post(
        f"/api/devices/{lamp_id}/firmware/update", json={"software_version": 16908288}
    )
    assert response.status_code == 202
    assert response.json()["state"] == "transferring"
    assert firmware.jobs.running_device_id == lamp_id


async def test_install_with_a_stale_version_is_409(firmware_api):
    client, _, _, _, lamp_id, _ = firmware_api
    await client.post(f"/api/devices/{lamp_id}/firmware/check")
    response = await client.post(
        f"/api/devices/{lamp_id}/firmware/update", json={"software_version": 1}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == i18n.t("api.firmware.fail_offer_changed")


async def test_a_second_install_is_409_naming_the_first_device(firmware_api):
    client, store, source, _, lamp_id, _ = firmware_api
    button_id, button = register(store, "ikea_bilresa_button.json")
    source.facts[button] = idle_facts(17301509, "1.8.5")
    await client.post(f"/api/devices/{lamp_id}/firmware/check")
    await client.post(f"/api/devices/{lamp_id}/firmware/update", json={"software_version": 16908288})
    response = await client.post(f"/api/devices/{button_id}/firmware/update", json={"software_version": 1})
    assert response.status_code == 409
    label = store.device(lamp_id).label
    assert response.json()["detail"] == i18n.t("api.firmware.fail_busy", device=label)


async def test_install_on_an_offline_device_is_409(firmware_api):
    client, _, source, _, lamp_id, lamp = firmware_api
    await client.post(f"/api/devices/{lamp_id}/firmware/check")
    source.facts[lamp] = idle_facts(16842752, "1.1.0", available=False)
    response = await client.post(
        f"/api/devices/{lamp_id}/firmware/update", json={"software_version": 16908288}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == i18n.t("api.firmware.fail_offline")


async def test_unknown_device_is_404(firmware_api):
    client, *_ = firmware_api
    response = await client.post("/api/devices/999/firmware/check")
    assert response.status_code == 404


async def test_an_unsupported_server_is_409(firmware_api):
    client, _, source, _, lamp_id, _ = firmware_api
    source.supported = False
    assert (await client.get("/api/firmware")).json()["supported"] is False
    response = await client.post("/api/firmware/check")
    assert response.status_code == 409
    assert response.json()["detail"] == i18n.t("api.firmware.fail_unsupported")


async def test_the_daily_check_can_be_switched_off(firmware_api):
    client, store, *_ = firmware_api
    response = await client.put("/api/firmware/settings", json={"daily_check_enabled": False})
    assert response.status_code == 200
    assert response.json()["daily_check_enabled"] is False
    assert store.firmware_settings.get_daily_check_enabled() is False


async def test_the_routes_need_a_login(tmp_path, no_invoke, fake_runtime, fake_otbr):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), sources=Sources([]), thread_dataset_source=fake_otbr)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/firmware")).status_code == 401
    store.close()


async def test_without_a_firmware_source_the_overview_says_unsupported(tmp_path, no_invoke, fake_runtime, fake_client, fake_otbr):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), client=fake_client, thread_dataset_source=fake_otbr)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        assert (await client.get("/api/firmware")).json()["supported"] is False
    store.close()
```

The `sys.path` insert makes `tests/firmware/firmware_fakes.py` importable from `tests/api/`. If ruff flags the import order, keep the `# noqa: E402`.

Add to `tests/api/test_devices.py`, after `test_expert_reads_vendor_model_and_firmware_from_basic_information`:

```python
async def test_expert_shows_the_matter_version(api):
    """The GRILLPLATS fixture carries 0/40/21 = 0x01040000 (design 2026-09-30, 9.3)."""
    client, _, device_id, _ = api
    response = await client.get(f"/api/devices/{device_id}/expert")
    assert response.json()["matter_version"] == "1.4"
```

And in the Zigbee expert test (`test_expert_labels_a_zigbee_devices_address_as_its_ieee_address`), add `assert data["matter_version"] is None` (use the variable name that test already uses for the JSON).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_firmware_api.py tests/api/test_devices.py -q -k "firmware or matter_version"`
Expected: FAIL with `ModuleNotFoundError: No module named 'loxmatter.firmware.service'`.

- [ ] **Step 3: i18n keys**

```yaml
api.firmware.fail_unsupported:
  en: "Firmware updates need a matter-server with API schema 10 or newer, connected. The current one does not offer them."
  de: "Firmware-Updates brauchen einen verbundenen matter-server mit API-Schema 10 oder neuer. Der aktuelle bietet sie nicht an."
api.firmware.fail_busy:
  en: "An update is already running on {device}. Wait until it has finished."
  de: "Auf {device} läuft bereits ein Update. Warten Sie, bis es abgeschlossen ist."
api.firmware.fail_offer_changed:
  en: "This update is no longer offered. Check for updates again."
  de: "Dieses Update wird nicht mehr angeboten. Prüfen Sie erneut auf Updates."
api.firmware.fail_offline:
  en: "The device is not reachable right now, so the update cannot start."
  de: "Das Gerät ist gerade nicht erreichbar, deshalb kann das Update nicht starten."
```

- [ ] **Step 4: API models**

`src/loxmatter/api/models.py`: add `matter_version: str | None = None` to `DeviceExpertOut` after `firmware`. Append:

```python
class FirmwareOfferOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    version_string: str
    release_notes_url: str | None
    source: str


class FirmwareDeviceOut(BaseModel):
    """One row of the update overview (design 2026-09-30, 9.1)."""

    model_config = ConfigDict(frozen=True)

    device_id: int
    label: str
    room: str | None
    technology: str
    matter_version: str | None
    installed: str | None
    # `None` when the source cannot tell (a Zigbee device in stage 1).
    online: bool | None
    state: str
    progress: int | None
    offer: FirmwareOfferOut | None
    checked_at: str | None
    check_error: str | None
    job_error: str | None


class FirmwareCheckOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    running: bool
    checked: int
    total: int


class FirmwareOverviewOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    supported: bool
    daily_check_enabled: bool
    last_checked_at: str | None
    check: FirmwareCheckOut
    updating_device_id: int | None
    devices: list[FirmwareDeviceOut]


class FirmwareInstallIn(BaseModel):
    software_version: int


class FirmwareSettingsIn(BaseModel):
    daily_check_enabled: bool
```

- [ ] **Step 5: `service.py`**

```python
"""Bundles checking and installing for the API and `cli` (design 2026-09-30).

`build_app` builds one when `cli` passes none, so every test app has the
routes; only `cli` starts the schedule and resumes jobs."""

from __future__ import annotations

from loxmatter.api.models import FirmwareCheckOut, FirmwareDeviceOut, FirmwareOfferOut, FirmwareOverviewOut
from loxmatter.firmware import states
from loxmatter.firmware.check import FirmwareChecker
from loxmatter.firmware.job import FirmwareJobs
from loxmatter.model.store import Store, StoredDevice
from loxmatter.sources import Sources, SourceNotConfiguredError
from loxmatter.sources.firmware import FirmwareSource


class FirmwareService:
    def __init__(
        self,
        store: Store,
        sources: Sources,
        *,
        checker: FirmwareChecker | None = None,
        jobs: FirmwareJobs | None = None,
    ) -> None:
        self._store = store
        self._sources = sources
        self.checker = checker or FirmwareChecker(store, self.source_for)
        self.jobs = jobs or FirmwareJobs(store, self.source_for)

    def source_for(self) -> FirmwareSource | None:
        """The Matter source, if it can update firmware. Looked up on every
        call: `Sources` can change while the bridge runs."""
        try:
            source = self._sources.get("matter")
        except SourceNotConfiguredError:
            return None
        return source if isinstance(source, FirmwareSource) else None

    def supported(self) -> bool:
        source = self.source_for()
        return source is not None and source.firmware_supported()

    def device_out(self, device: StoredDevice) -> FirmwareDeviceOut:
        source = self.source_for()
        facts = None
        if source is not None and source.connected and device.technology == source.technology:
            facts = source.firmware_facts(device.address)
        status = self._store.firmware_status.get(device.id)
        offer = None if status is None else status.offer
        installed_number = None if facts is None else facts.software_version
        state = states.derive_state(
            has_requestor=facts is not None and facts.has_requestor,
            installed=installed_number,
            checked_at=None if status is None else status.checked_at,
            check_error=None if status is None else status.check_error,
            offer_version=None if offer is None else offer.software_version,
            job_state=None if status is None else status.job_state,
        )
        return FirmwareDeviceOut(
            device_id=device.id,
            label=device.label,
            room=device.room,
            technology=device.technology,
            # The live value first: the stored one is NULL until the backfill ran.
            matter_version=states.format_spec_version(
                facts.spec_version if facts is not None else device.matter_spec_version,
                device.technology,
            ),
            installed=(facts.software_version_string if facts is not None else None) or device.firmware,
            online=None if facts is None else facts.available,
            state=state,
            progress=None if status is None else status.job_progress,
            # An offer the device has already reached is not shown.
            offer=None
            if offer is None or state == states.NONE_FOUND
            else FirmwareOfferOut(
                version=offer.software_version,
                version_string=offer.software_version_string,
                release_notes_url=offer.release_notes_url,
                source=offer.source,
            ),
            checked_at=None if status is None else status.checked_at,
            check_error=None if status is None else status.check_error,
            job_error=None if status is None else status.job_error,
        )

    def overview(self) -> FirmwareOverviewOut:
        progress = self.checker.progress
        return FirmwareOverviewOut(
            supported=self.supported(),
            daily_check_enabled=self._store.firmware_settings.get_daily_check_enabled(),
            last_checked_at=self._store.firmware_status.last_checked_at(),
            check=FirmwareCheckOut(running=progress.running, checked=progress.checked, total=progress.total),
            updating_device_id=self.jobs.running_device_id,
            devices=[self.device_out(device) for device in self._store.devices()],
        )
```

If the `api.models` import creates an import cycle (`api` → `firmware.service` → `api.models`), move the four `Firmware*Out` models into `src/loxmatter/firmware/models.py` instead and import them from there in both places.

- [ ] **Step 6: `api/firmware.py`**

```python
"""Routes of the firmware update overview (design 2026-09-30, section 9.1)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from loxmatter import i18n
from loxmatter.api.models import (
    FirmwareCheckOut,
    FirmwareDeviceOut,
    FirmwareInstallIn,
    FirmwareOverviewOut,
    FirmwareSettingsIn,
)
from loxmatter.firmware.job import (
    DeviceOfflineError,
    FirmwareBusyError,
    FirmwareUnsupportedError,
    OfferChangedError,
)
from loxmatter.firmware.service import FirmwareService
from loxmatter.model.store import Store, StoredDevice, UnknownDeviceError


def build_firmware_router(store: Store, firmware: FirmwareService) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _device(device_id: int) -> StoredDevice:
        try:
            return store.device(device_id)
        except UnknownDeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def _require_supported() -> None:
        if not firmware.supported():
            raise HTTPException(status_code=409, detail=i18n.t("api.firmware.fail_unsupported"))

    @router.get("/firmware")
    async def get_overview() -> FirmwareOverviewOut:
        return firmware.overview()

    @router.post("/firmware/check", status_code=status.HTTP_202_ACCEPTED)
    async def check_all() -> FirmwareCheckOut:
        _require_supported()
        progress = firmware.checker.start_all()
        return FirmwareCheckOut(running=progress.running, checked=progress.checked, total=progress.total)

    @router.post("/devices/{device_id}/firmware/check")
    async def check_one(device_id: int) -> FirmwareDeviceOut:
        device = _device(device_id)
        _require_supported()
        await firmware.checker.check_one(device_id)
        return firmware.device_out(device)

    @router.post("/devices/{device_id}/firmware/update", status_code=status.HTTP_202_ACCEPTED)
    async def install(device_id: int, body: FirmwareInstallIn) -> FirmwareDeviceOut:
        device = _device(device_id)
        try:
            firmware.jobs.start(device_id, body.software_version)
        except FirmwareUnsupportedError as exc:
            raise HTTPException(status_code=409, detail=i18n.t("api.firmware.fail_unsupported")) from exc
        except FirmwareBusyError as exc:
            busy_label = _device(exc.device_id).label
            raise HTTPException(status_code=409, detail=i18n.t("api.firmware.fail_busy", device=busy_label)) from exc
        except OfferChangedError as exc:
            raise HTTPException(status_code=409, detail=i18n.t("api.firmware.fail_offer_changed")) from exc
        except DeviceOfflineError as exc:
            raise HTTPException(status_code=409, detail=i18n.t("api.firmware.fail_offline")) from exc
        return firmware.device_out(device)

    @router.put("/firmware/settings")
    async def put_settings(body: FirmwareSettingsIn) -> FirmwareOverviewOut:
        store.firmware_settings.set_daily_check_enabled(body.daily_check_enabled)
        return firmware.overview()

    return router
```

Check how `api/devices.py`'s `_require_device` turns `UnknownDeviceError` into a 404 (its detail text, possibly already i18n) and do exactly the same here; reuse its helper if it is importable.

- [ ] **Step 7: Expert route**

`src/loxmatter/api/devices.py`, in `get_expert`, add after `firmware=device.firmware,`:

```python
            matter_version=format_spec_version(device.matter_spec_version, device.technology),
```

with `from loxmatter.firmware.states import format_spec_version` at the top.

- [ ] **Step 8: `build_app`**

`src/loxmatter/loxone/server.py`: new keyword parameter `firmware: FirmwareService | None = None` at the end of `build_app`'s signature. After the `sources` fallback (`if sources is None and client is not None: ...`):

```python
    if firmware is None:
        # Every app has the routes; without a firmware-capable source the
        # overview says `supported: false`. Only `cli` starts the schedule.
        firmware = FirmwareService(store, sources if sources is not None else Sources([]))
```

Next to the other guarded routers:

```python
    app.include_router(build_firmware_router(store, firmware), dependencies=api_guard)
```

Imports: `from loxmatter.api.firmware import build_firmware_router`, `from loxmatter.firmware.service import FirmwareService`, and `Sources` if not yet imported.

- [ ] **Step 9: `cli._run`**

In `src/loxmatter/cli.py`:

1. Before `supervisor_tasks: list[...] = []`: `firmware = FirmwareService(store, sources)` and `firmware_schedule_task: asyncio.Task[None] | None = None`.
2. After the `for source in sources.all(): gained += await attach(...)` loop:

```python
        # Design 2026-09-30, 7.4: a transfer outlives a restart of loxmatter
        # (matter-server runs it); pick it up again, and start the daily check.
        firmware.jobs.resume_all()
        firmware_schedule_task = asyncio.ensure_future(
            run_daily(firmware.checker, store.firmware_settings)
        )
```

3. Pass `firmware=firmware,` to `build_app(...)`.
4. In the `finally:` block, before the `thread_network_task` cleanup:

```python
        if firmware_schedule_task is not None:
            firmware_schedule_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await firmware_schedule_task
        firmware.checker.cancel()
        await firmware.jobs.stop()
```

(Follow the existing cancellation pattern for `thread_network_task` if `contextlib.suppress` would swallow a cancellation of `_run` itself — copy that block's `if not task.cancelled(): raise` shape.)

Imports: `from loxmatter.firmware.schedule import run_daily`, `from loxmatter.firmware.service import FirmwareService`.

- [ ] **Step 10: Run the tests**

Run: `uv run pytest tests/api/test_firmware_api.py tests/api/test_devices.py tests/test_cli.py -q`
Expected: all pass.

- [ ] **Step 11: Lint, type-check, language check, commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter tests/api
git commit -m "feat(api): serve the firmware overview, checks and installs, and the Matter version

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: WebUI — expert row, tile pill, kebab item, dialog, System card

**Files:**
- Modify: `src/loxmatter/web/app.js` (state next to `expertData: null` ~line 1177; `startApp` after `await this.loadGroups();` ~line 1663; methods after `closeExpertModal` ~line 4975; `loadSystem` ~line 6843)
- Modify: `src/loxmatter/web/index.html` (tile foot pill ~line 1507; kebab ~line 1752; System card after the resync card ~line 2571; expert row ~line 3583; new `<dialog>` after the expert modal)
- Modify: `src/loxmatter/web/style.css`
- Modify: `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py` (extend with a delivery test); browser harness run (Step 7)

**Interfaces:**
- Consumes: `GET /api/firmware` JSON (Task 7), `POST /api/firmware/check`, `POST /api/devices/{id}/firmware/check`, `POST /api/devices/{id}/firmware/update` `{software_version}`, `PUT /api/firmware/settings` `{daily_check_enabled}`, `GET /api/devices/{id}/expert` field `matter_version`.
- Produces: Alpine state `firmware`, methods `loadFirmware`, `firmwareFor(deviceId)`, `firmwarePillText(deviceId)`, `openFirmwareModal(device)`, `closeFirmwareModal()`, `checkFirmwareAll()`, `checkFirmwareOne()`, `installFirmware()`, `setFirmwareDailyCheck(enabled)`, `matterVersionText(value)`, `firmwareStateText(row)`.

- [ ] **Step 1: Write the failing delivery test**

Look at `tests/api/test_web.py` for how it fetches `/` and asserts on markup (e.g. a test that checks an `x-text="t('web....')"` key is present). Add, in the same style:

```python
async def test_the_firmware_parts_are_delivered(web_client):
    """Design 2026-09-30, 9.2/9.3: the card, the dialog, the pill, the kebab
    item and the expert row. Delivery only - the bindings run in the browser
    harness (plan Task 8, Step 7)."""
    html = (await web_client.get("/")).text
    for needle in (
        "t('web.firmware.card_heading')",
        'x-ref="firmwareModal"',
        "firmwarePillText(device.id)",
        "openFirmwareModal(device)",
        "t('web.devices.expert_matter_version')",
    ):
        assert needle in html
```

Use the fixture name `test_web.py` already uses for a client serving `/` in place of `web_client`.

Also add to `tests/test_i18n.py` nothing: it already checks that every `t('...')` key in `index.html`/`app.js` exists in `strings.yaml` with `en` and `de` (verify by reading it; if it does not, add such a test for the `web.firmware.*` keys).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_web.py -q -k firmware`
Expected: FAIL (needles missing).

- [ ] **Step 3: Strings**

Add to `strings.yaml` (German in "Sie" form like the rest):

```yaml
web.devices.menu_firmware:
  en: "Software update …"
  de: "Software-Update …"
web.devices.expert_matter_version:
  en: "Matter version"
  de: "Matter-Version"
web.firmware.matter_version_older:
  en: "1.2 or older"
  de: "1.2 oder älter"
web.firmware.card_heading:
  en: "Device updates"
  de: "Geräte-Updates"
web.firmware.last_checked:
  en: "Last checked {when}"
  de: "Zuletzt geprüft {when}"
web.firmware.never_checked:
  en: "Not checked yet"
  de: "Noch nicht geprüft"
web.firmware.check_now:
  en: "Check for updates now"
  de: "Jetzt auf Updates prüfen"
web.firmware.checking:
  en: "Checking {checked} of {total} …"
  de: "Prüfe {checked} von {total} …"
web.firmware.unsupported:
  en: "This matter-server does not offer firmware updates (API schema 10 or newer needed)."
  de: "Dieser matter-server bietet keine Firmware-Updates an (API-Schema 10 oder neuer nötig)."
web.firmware.filter_all:
  en: "All {count}"
  de: "Alle {count}"
web.firmware.filter_available:
  en: "Update available {count}"
  de: "Update verfügbar {count}"
web.firmware.filter_no_source:
  en: "No update source {count}"
  de: "Ohne Update-Quelle {count}"
web.firmware.col_device:
  en: "Device"
  de: "Gerät"
web.firmware.col_matter:
  en: "Matter"
  de: "Matter"
web.firmware.col_installed:
  en: "Installed"
  de: "Installiert"
web.firmware.col_available:
  en: "Available"
  de: "Verfügbar"
web.firmware.col_state:
  en: "Status"
  de: "Status"
web.firmware.install:
  en: "Install"
  de: "Installieren"
web.firmware.daily_check:
  en: "Check for updates every day at 06:00. Updates are never installed automatically."
  de: "Täglich um 06:00 nach Updates suchen. Installiert wird nie automatisch."
web.firmware.state_available:
  en: "Update available"
  de: "Update verfügbar"
web.firmware.state_none_found:
  en: "No update found"
  de: "Kein Update gefunden"
web.firmware.state_no_source:
  en: "No update source"
  de: "Keine Update-Quelle"
web.firmware.state_no_source_hint:
  en: "The manufacturer publishes no updates over Matter. Use its app or the device's own web page."
  de: "Der Hersteller veröffentlicht keine Updates über Matter. Nutzen Sie seine App oder die Web-Oberfläche des Geräts."
web.firmware.state_unchecked:
  en: "Not checked"
  de: "Nicht geprüft"
web.firmware.state_check_failed:
  en: "Last check failed"
  de: "Letzte Prüfung fehlgeschlagen"
web.firmware.state_transferring:
  en: "Updating · {progress} %"
  de: "Wird übertragen · {progress} %"
web.firmware.state_transferring_no_progress:
  en: "Updating …"
  de: "Wird übertragen …"
web.firmware.state_applying:
  en: "Applying"
  de: "Wird angewendet"
web.firmware.state_stalled:
  en: "No progress for 15 minutes"
  de: "Seit 15 Minuten kein Fortschritt"
web.firmware.state_failed:
  en: "Update failed"
  de: "Update fehlgeschlagen"
web.firmware.state_interrupted:
  en: "Update interrupted"
  de: "Update unterbrochen"
web.firmware.unreachable:
  en: "unreachable"
  de: "nicht erreichbar"
web.firmware.pill_available:
  en: "Update available: {from} → {to}"
  de: "Update verfügbar: {from} → {to}"
web.firmware.dialog_heading:
  en: "Software update · {device}"
  de: "Software-Update · {device}"
web.firmware.dialog_installed:
  en: "Installed"
  de: "Installiert"
web.firmware.dialog_available:
  en: "Available"
  de: "Verfügbar"
web.firmware.dialog_source:
  en: "Source"
  de: "Quelle"
web.firmware.dialog_checked:
  en: "Checked"
  de: "Geprüft"
web.firmware.source_main-net-dcl:
  en: "CSA directory (DCL)"
  de: "CSA-Verzeichnis (DCL)"
web.firmware.source_test-net-dcl:
  en: "CSA test directory"
  de: "CSA-Testverzeichnis"
web.firmware.source_local:
  en: "Local file"
  de: "Lokale Datei"
web.firmware.release_notes:
  en: "Manufacturer's release notes"
  de: "Versionshinweise des Herstellers"
web.firmware.before_heading:
  en: "Before you start"
  de: "Bevor Sie starten"
web.firmware.before_duration:
  en: "The transfer takes a while, over Thread often 15 to 45 minutes. The device stays usable meanwhile."
  de: "Die Übertragung dauert eine Weile, über Thread oft 15 bis 45 Minuten. Das Gerät bleibt in der Zeit bedienbar."
web.firmware.before_restart:
  en: "At the end the device restarts and is unreachable for about a minute. Loxone receives no values from it during that time."
  de: "Zum Schluss startet das Gerät neu und ist etwa eine Minute nicht erreichbar. Loxone erhält in der Zeit keine Werte von ihm."
web.firmware.before_export:
  en: "If its signals change, the tile shows \"Changed since export\". Then export the Loxone template again."
  de: "Ändern sich seine Signale, zeigt die Kachel „Geändert seit Export“. Exportieren Sie dann die Loxone-Vorlage neu."
web.firmware.check_again:
  en: "Check again"
  de: "Erneut prüfen"
web.firmware.cancel:
  en: "Cancel"
  de: "Abbrechen"
web.firmware.install_update:
  en: "Install update"
  de: "Update installieren"
web.firmware.close:
  en: "Close"
  de: "Schließen"
web.firmware.step_accepted:
  en: "Device accepted the update"
  de: "Gerät hat das Update angenommen"
web.firmware.step_transfer:
  en: "Transfer to the device"
  de: "Übertragung ans Gerät"
web.firmware.step_restart:
  en: "Device restarts with the new version"
  de: "Gerät startet mit der neuen Version"
web.firmware.step_confirmed:
  en: "New version confirmed"
  de: "Neue Version bestätigt"
web.firmware.running_hint:
  en: "You can close this dialog. The update continues in the bridge; the tile shows its progress."
  de: "Sie können diesen Dialog schließen. Das Update läuft in der Brücke weiter, die Kachel zeigt den Stand."
web.firmware.done:
  en: "Updated to {version}."
  de: "Auf {version} aktualisiert."
web.firmware.action_error:
  en: "The action failed: {message}"
  de: "Die Aktion ist fehlgeschlagen: {message}"
```

Check the file's existing key order convention (grouped by prefix) and insert the `web.firmware.*` block next to `web.devices.*`. Check whether `strings.yaml` keys may contain `-` (`source_main-net-dcl`); if the i18n loader or the key checker rejects it, map the source in `app.js` instead (`{"main-net-dcl": "source_main_net_dcl", ...}`) and name the keys with underscores.

- [ ] **Step 4: `app.js`**

State, next to `expertData: null,`:

```javascript
    // Firmware updates (design 2026-09-30). `firmware` is the last
    // `GET /api/firmware`; `firmwareModalDevice` the device whose dialog is
    // open. Polled every 5 s while a check or an install runs, else every 60 s.
    firmware: null,
    firmwareTimer: null,
    firmwareModalDevice: null,
    firmwareModalBackdropMousedown: false,
    firmwareBusy: false,
    firmwareError: null,
    firmwareFilter: "all",
```

In `startApp`, after `await this.loadGroups();`:

```javascript
      this.loadFirmware();
```

Methods, after `closeExpertModal()`:

```javascript
    /** Loads the update overview and schedules the next load: every 5 s
     * while something runs, every 60 s otherwise. A `setTimeout` chain,
     * so a slow answer never stacks two polls. */
    async loadFirmware() {
      if (this.firmwareTimer) {
        clearTimeout(this.firmwareTimer);
        this.firmwareTimer = null;
      }
      try {
        this.firmware = await this.request("GET", "/api/firmware");
      } catch {
        // Keep the last known overview; the next poll tries again.
      }
      if (!this.authenticated) return;
      const busy = this.firmware && (this.firmware.check.running || this.firmware.updating_device_id !== null);
      this.firmwareTimer = setTimeout(() => this.loadFirmware(), busy ? 5000 : 60000);
    },

    firmwareFor(deviceId) {
      return this.firmware?.devices.find((row) => row.device_id === deviceId) || null;
    },

    matterVersionText(value) {
      if (value === "<1.3") return t("web.firmware.matter_version_older");
      return value ?? "–";
    },

    firmwareStateText(row) {
      if (!row) return "";
      if (row.state === "transferring") {
        return row.progress === null
          ? t("web.firmware.state_transferring_no_progress")
          : t("web.firmware.state_transferring", { progress: row.progress });
      }
      return t("web.firmware.state_" + row.state);
    },

    /** The pill on a device tile: an offer, or a running install. Empty
     * when there is nothing to say - the tile then shows no pill. */
    firmwarePillText(deviceId) {
      const row = this.firmwareFor(deviceId);
      if (!row) return "";
      if (row.state === "available" && row.offer) {
        return t("web.firmware.pill_available", { from: row.installed ?? "?", to: row.offer.version_string });
      }
      if (["transferring", "applying", "stalled"].includes(row.state)) {
        return this.firmwareStateText(row);
      }
      return "";
    },

    firmwareModalRow() {
      return this.firmwareModalDevice === null ? null : this.firmwareFor(this.firmwareModalDevice);
    },

    firmwareModalDeviceObject() {
      return this.devices.find((device) => device.id === this.firmwareModalDevice) || null;
    },

    openFirmwareModal(device) {
      this.firmwareError = null;
      this.firmwareModalDevice = device.id;
      this.$nextTick(() => this.$refs.firmwareModal.showModal());
    },

    closeFirmwareModal() {
      this.$refs.firmwareModal.close();
    },

    firmwareRows() {
      const rows = this.firmware?.devices ?? [];
      if (this.firmwareFilter === "available") return rows.filter((row) => row.state === "available");
      if (this.firmwareFilter === "no_source") return rows.filter((row) => row.state === "no_source");
      return rows;
    },

    firmwareCount(state) {
      return (this.firmware?.devices ?? []).filter((row) => row.state === state).length;
    },

    async checkFirmwareAll() {
      this.firmwareError = null;
      try {
        await this.request("POST", "/api/firmware/check");
      } catch (error) {
        this.firmwareError = t("web.firmware.action_error", { message: error.message });
      }
      await this.loadFirmware();
    },

    async checkFirmwareOne() {
      const deviceId = this.firmwareModalDevice;
      this.firmwareError = null;
      this.firmwareBusy = true;
      try {
        await this.request("POST", `/api/devices/${deviceId}/firmware/check`);
      } catch (error) {
        this.firmwareError = t("web.firmware.action_error", { message: error.message });
      } finally {
        this.firmwareBusy = false;
      }
      await this.loadFirmware();
    },

    async installFirmware() {
      const deviceId = this.firmwareModalDevice;
      const row = this.firmwareFor(deviceId);
      if (!row?.offer) return;
      this.firmwareError = null;
      this.firmwareBusy = true;
      try {
        await this.request("POST", `/api/devices/${deviceId}/firmware/update`, {
          software_version: row.offer.version,
        });
      } catch (error) {
        this.firmwareError = t("web.firmware.action_error", { message: error.message });
      } finally {
        this.firmwareBusy = false;
      }
      await this.loadFirmware();
    },

    async setFirmwareDailyCheck(enabled) {
      try {
        this.firmware = await this.request("PUT", "/api/firmware/settings", { daily_check_enabled: enabled });
      } catch (error) {
        this.firmwareError = t("web.firmware.action_error", { message: error.message });
      }
    },

    /** Step list of a running install, in the style of the bridge update
     * card: "done", "run" or "todo" per step. */
    firmwareSteps(row) {
      const order = ["accepted", "transfer", "restart", "confirmed"];
      const current = { transferring: row?.progress === null ? 0 : 1, stalled: 1, applying: 2 }[row?.state];
      return order.map((key, index) => ({
        key,
        status: current === undefined ? "todo" : index < current ? "done" : index === current ? "run" : "todo",
      }));
    },
```

In `loadSystem`, after `await this.loadUpdateCheck();`: `await this.loadFirmware();`.

Where the app handles logout / `authenticated = false` (search `this.authenticated = false`), also clear the timer: add `clearTimeout(this.firmwareTimer); this.firmwareTimer = null;` in `noteAuthError`'s `UnauthorizedError` branch.

- [ ] **Step 5: `index.html`**

1. Tile footer, directly after the `changedSinceExport` pill (`<span class="status-pill warn" x-show="changedSinceExport(device.id)">…</span>`):

```html
                    <!-- Firmware (design 2026-09-30, 9.2): an offer or a
                         running install. Copper, not warning yellow: an
                         offer is not a problem. A click opens the dialog. -->
                    <button type="button" class="status-pill update" x-show="firmwarePillText(device.id)" x-cloak
                            @click="openFirmwareModal(device)">
                      <span x-text="firmwarePillText(device.id)"></span>
                    </button>
```

2. Kebab, before the `exportDevice(device)` button:

```html
                        <button
                          class="tile-menu-item"
                          x-show="firmwareFor(device.id) && firmwareFor(device.id).state !== 'no_source'"
                          @click="closeTileMenu($el); openFirmwareModal(device)"
                          x-text="t('web.devices.menu_firmware')"
                        ></button>
```

3. Expert modal, after the firmware row:

```html
                  <span class="k" x-show="expertData.technology === 'matter'" x-text="t('web.devices.expert_matter_version')"></span><span x-show="expertData.technology === 'matter'" x-text="matterVersionText(expertData.matter_version)"></span>
```

4. System view, after the resync card (`</div>` closing the card with `resyncAll()`):

```html
        <!-- Device updates (design 2026-09-30, 9.2). -->
        <div class="card firmware-card">
          <div class="row">
            <h2 style="margin: 0" x-text="t('web.firmware.card_heading')"></h2>
            <span style="flex: 1 1 auto"></span>
            <button
              @click="checkFirmwareAll()"
              :disabled="!firmware?.supported || firmware?.check.running"
              x-text="firmware?.check.running
                      ? t('web.firmware.checking', { checked: firmware.check.checked, total: firmware.check.total })
                      : t('web.firmware.check_now')"
            ></button>
          </div>
          <p class="hint" x-show="firmware && !firmware.supported" x-cloak x-text="t('web.firmware.unsupported')"></p>
          <p class="hint" x-show="firmware" x-cloak
             x-text="firmware?.last_checked_at
                     ? t('web.firmware.last_checked', { when: formatTimestamp(firmware.last_checked_at) })
                     : t('web.firmware.never_checked')"></p>
          <p class="banner danger" x-show="firmwareError" x-cloak x-text="firmwareError"></p>
          <div class="firmware-filter" x-show="firmware" x-cloak>
            <button type="button" :class="{ on: firmwareFilter === 'all' }" @click="firmwareFilter = 'all'"
                    x-text="t('web.firmware.filter_all', { count: firmware?.devices.length ?? 0 })"></button>
            <button type="button" :class="{ on: firmwareFilter === 'available' }" @click="firmwareFilter = 'available'"
                    x-text="t('web.firmware.filter_available', { count: firmwareCount('available') })"></button>
            <button type="button" :class="{ on: firmwareFilter === 'no_source' }" @click="firmwareFilter = 'no_source'"
                    x-text="t('web.firmware.filter_no_source', { count: firmwareCount('no_source') })"></button>
          </div>
          <div class="firmware-table-wrap" x-show="firmware" x-cloak>
            <table class="firmware-table">
              <thead>
                <tr>
                  <th x-text="t('web.firmware.col_device')"></th>
                  <th x-text="t('web.firmware.col_matter')"></th>
                  <th x-text="t('web.firmware.col_installed')"></th>
                  <th x-text="t('web.firmware.col_available')"></th>
                  <th x-text="t('web.firmware.col_state')"></th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <template x-for="row in firmwareRows()" :key="row.device_id">
                  <tr>
                    <td>
                      <span x-text="row.label"></span>
                      <span class="hint" x-show="row.room" x-text="row.room"></span>
                    </td>
                    <td class="mono" x-text="matterVersionText(row.matter_version)"></td>
                    <td class="mono" x-text="row.installed ?? '–'"></td>
                    <td class="mono" x-text="row.offer?.version_string ?? '–'"></td>
                    <td>
                      <span class="firmware-state" :class="'is-' + row.state"
                            :title="row.state === 'no_source' ? t('web.firmware.state_no_source_hint') : (row.check_error ?? row.job_error ?? '')"
                            x-text="firmwareStateText(row)"></span>
                      <span class="hint" x-show="row.online === false" x-text="t('web.firmware.unreachable')"></span>
                    </td>
                    <td>
                      <!-- Opens the dialog rather than installing: the
                           dialog's "Install update" is the confirmation the
                           design requires (2, 9.2). -->
                      <button
                        class="primary"
                        x-show="row.state === 'available'"
                        :disabled="firmware.updating_device_id !== null || row.online === false"
                        @click="openFirmwareModal(devices.find((d) => d.id === row.device_id))"
                        x-text="t('web.firmware.install')"
                      ></button>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
          <label class="firmware-daily" x-show="firmware" x-cloak>
            <input type="checkbox" id="firmware-daily-check"
                   :checked="firmware?.daily_check_enabled"
                   @change="setFirmwareDailyCheck($event.target.checked)" />
            <span x-text="t('web.firmware.daily_check')"></span>
          </label>
        </div>
```

5. New dialog, directly after the expert modal's closing `</dialog>`:

```html
    <!-- Firmware update dialog (design 2026-09-30, 9.2). Same open/close
         mechanics as the expert modal above: `@close` is the one place that
         resets `firmwareModalDevice`. -->
    <dialog
      x-ref="firmwareModal"
      class="expert-modal firmware-modal"
      aria-labelledby="firmware-modal-heading"
      @close="firmwareModalDevice = null; firmwareError = null"
      @mousedown="firmwareModalBackdropMousedown = isBackdropEvent($event, $el)"
      @click.self="if (firmwareModalBackdropMousedown && isBackdropEvent($event, $el)) $el.close(); firmwareModalBackdropMousedown = false"
    >
      <template x-if="firmwareModalDeviceObject()">
        <div class="expert-modal-body">
          <div class="expert-modal-head">
            <h2 id="firmware-modal-heading"
                x-text="t('web.firmware.dialog_heading', { device: firmwareModalDeviceObject().label })"></h2>
            <span style="flex: 1 1 auto"></span>
            <button class="expert-modal-close" :title="t('web.firmware.close')" :aria-label="t('web.firmware.close')"
                    @click="closeFirmwareModal()"><svg class="icon" aria-hidden="true"><use href="#i-close"></use></svg></button>
          </div>
          <p class="banner danger" x-show="firmwareError" x-cloak x-text="firmwareError"></p>
          <template x-if="firmwareModalRow()">
            <div>
              <div class="expert-kv">
                <span class="k" x-text="t('web.firmware.dialog_installed')"></span><span class="mono" x-text="firmwareModalRow().installed ?? '–'"></span>
                <span class="k" x-text="t('web.firmware.dialog_available')"></span><span class="mono" x-text="firmwareModalRow().offer?.version_string ?? '–'"></span>
                <span class="k" x-text="t('web.devices.expert_matter_version')"></span><span class="mono" x-text="matterVersionText(firmwareModalRow().matter_version)"></span>
                <span class="k" x-show="firmwareModalRow().offer" x-text="t('web.firmware.dialog_source')"></span><span x-show="firmwareModalRow().offer" x-text="firmwareModalRow().offer ? t('web.firmware.source_' + firmwareModalRow().offer.source) : ''"></span>
                <span class="k" x-text="t('web.firmware.dialog_checked')"></span><span x-text="firmwareModalRow().checked_at ? formatTimestamp(firmwareModalRow().checked_at) : t('web.firmware.never_checked')"></span>
              </div>
              <p x-show="firmwareModalRow().offer?.release_notes_url" x-cloak>
                <a :href="firmwareModalRow().offer?.release_notes_url" target="_blank" rel="noopener" x-text="t('web.firmware.release_notes')"></a>
              </p>
              <p class="firmware-state" :class="'is-' + firmwareModalRow().state" x-text="firmwareStateText(firmwareModalRow())"></p>
              <p class="hint" x-show="firmwareModalRow().check_error || firmwareModalRow().job_error" x-cloak
                 x-text="firmwareModalRow().check_error ?? firmwareModalRow().job_error"></p>

              <!-- Before: the three consequences that show in operation. -->
              <div class="banner warn" x-show="firmwareModalRow().state === 'available'">
                <strong x-text="t('web.firmware.before_heading')"></strong>
                <ul>
                  <li x-text="t('web.firmware.before_duration')"></li>
                  <li x-text="t('web.firmware.before_restart')"></li>
                  <li x-text="t('web.firmware.before_export')"></li>
                </ul>
              </div>

              <!-- During: the step list of the bridge update card. -->
              <template x-if="['transferring', 'applying', 'stalled'].includes(firmwareModalRow().state)">
                <div>
                  <ul class="firmware-steps">
                    <template x-for="step in firmwareSteps(firmwareModalRow())" :key="step.key">
                      <li :class="'is-' + step.status">
                        <span x-text="t('web.firmware.step_' + step.key)"></span>
                        <template x-if="step.key === 'transfer' && step.status === 'run' && firmwareModalRow().progress !== null">
                          <progress max="100" :value="firmwareModalRow().progress"></progress>
                        </template>
                      </li>
                    </template>
                  </ul>
                  <p class="hint" x-text="t('web.firmware.running_hint')"></p>
                </div>
              </template>

              <div class="firmware-actions">
                <button type="button" @click="checkFirmwareOne()"
                        :disabled="firmwareBusy || !firmware?.supported || ['transferring', 'applying', 'stalled'].includes(firmwareModalRow().state)"
                        x-text="t('web.firmware.check_again')"></button>
                <span style="flex: 1 1 auto"></span>
                <button type="button" @click="closeFirmwareModal()" x-text="t('web.firmware.cancel')"></button>
                <button type="button" class="primary" x-show="firmwareModalRow().state === 'available'"
                        :disabled="firmwareBusy || firmware.updating_device_id !== null || firmwareModalRow().online === false"
                        @click="installFirmware()" x-text="t('web.firmware.install_update')"></button>
              </div>
            </div>
          </template>
        </div>
      </template>
    </dialog>
```

Check that `.banner.warn`, `button.primary`, `.row` and `.mono` exist in `style.css`; if a class does not, use the class the rest of the page uses for the same purpose (search for the bridge update card's primary button and its steps list, and reuse those classes instead of the new `firmware-steps`).

- [ ] **Step 6: `style.css`**

```css
/* Firmware updates (design 2026-09-30). The tile pill is copper, the
   accent's tint - an offer, not a warning. */
.status-pill.update {
  background: var(--type-bg);
  color: var(--type-fg);
  border: 0;
  cursor: pointer;
  font: inherit;
}

.firmware-filter {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin: 0.75rem 0;
}

.firmware-filter button {
  border-radius: 999px;
  padding: 0.15rem 0.7rem;
}

.firmware-filter button.on {
  background: var(--accent);
  color: var(--accent-contrast);
  border-color: var(--accent);
}

.firmware-table-wrap {
  overflow-x: auto;
}

.firmware-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.9rem;
}

.firmware-table th {
  text-align: left;
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  padding: 0.4rem 0.6rem 0.4rem 0;
}

.firmware-table td {
  border-bottom: 1px solid var(--border);
  padding: 0.55rem 0.6rem 0.55rem 0;
  vertical-align: middle;
}

.firmware-table td.mono {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
}

.firmware-state.is-available {
  color: var(--type-fg);
  font-weight: 600;
}

.firmware-state.is-check_failed,
.firmware-state.is-failed,
.firmware-state.is-interrupted,
.firmware-state.is-stalled {
  color: var(--warn);
  font-weight: 600;
}

.firmware-state.is-no_source,
.firmware-state.is-unchecked,
.firmware-state.is-none_found {
  color: var(--text-muted);
}

.firmware-daily {
  display: flex;
  gap: 0.6rem;
  align-items: center;
  margin-top: 1rem;
  padding-top: 0.9rem;
  border-top: 1px solid var(--border);
}

.firmware-steps {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 0.5rem;
}

.firmware-steps .is-todo {
  color: var(--text-muted);
}

.firmware-steps .is-run {
  font-weight: 600;
}

.firmware-steps progress {
  display: block;
  width: 100%;
  accent-color: var(--accent);
}

.firmware-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin-top: 1rem;
}
```

- [ ] **Step 7: Run the bindings in a throwaway browser harness**

Delivery tests prove only that markup ships. Run the real Alpine bindings once (project rule, see memory "webui-browser-verification"):

1. Start the fake Miniserver dev setup the project uses for WebUI work (see `docs/DEVELOPMENT.md`, "Running the WebUI locally" or equivalent), or a scratch script that builds `build_app` with a `FakeFirmwareSource` holding the two IKEA fixtures and `KAJPLATS_OFFER`, served by uvicorn on `127.0.0.1:8099`. Keep the script in the session scratchpad, not in the repository.
2. In the in-app browser, log in, and verify:
   - the tile shows "Update available: 1.1.0 → 1.2.0"; clicking it opens the dialog;
   - the kebab item "Software update …" opens the same dialog;
   - "Install update" switches the dialog to the step list; advancing the fake's `update_state`/`progress` moves the steps and the bar within 5 s;
   - System → "Device updates" lists both devices with the Matter column (`1.4`, `1.3`), filters work, "Check for updates now" shows "Checking n of m …" while running;
   - the expert modal shows "Matter version";
   - the page switched to German (`de`) shows the German strings; phone width (375 px) has no horizontal page scroll (the table scrolls in its own box).
3. Fix what the harness shows, then delete the harness.

- [ ] **Step 8: Run tests, checks, commit**

```bash
uv run pytest tests/api/test_web.py tests/test_i18n.py -q
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/web src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(web): show device updates, install one from a dialog, and the Matter version

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Changelog and the full run

**Files:**
- Modify: `CHANGELOG.md` (`## [Unreleased]`)

- [ ] **Step 1: Changelog**

Under `## [Unreleased]`, `### Added`:

```markdown
- **Firmware updates for Matter devices.** System has a new "Device updates"
  card: which devices have an update in the CSA directory, and an install
  button that asks first. A device tile shows an available update and the
  progress of a running one. The bridge checks once a day at 06:00 and never
  installs on its own; the daily check can be switched off in the card.
- **The Matter version of every device** is shown in the update list and in
  the device's expert settings.
```

Add a `### Before you update` block if the section has none yet, or append to it:

```markdown
- **The database schema rises from 14 to 15.** Nothing needs doing by hand.
```

- [ ] **Step 2: The full run**

```bash
uv run pytest --collect-only -q | tail -3
uv run pytest tests/api -q
uv run pytest --ignore=tests/api -q
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
```

Expected: collection without errors; both halves green; all checks green. Any red test: fix it in the task it belongs to before continuing.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): announce firmware updates and the Matter version

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: On the test Pi `pi@10.0.1.56` (design 10.2) — with Lucien

This task changes firmware on real devices that cannot be rolled back. Every install below needs Lucien's go-ahead in the session before it starts.

- [ ] **Step 1:** Pull the fabric backup: `GET /api/diagnostics/fabric-backup` from the Pi, store it off the Pi, check the archive opens.
- [ ] **Step 2:** Note node 22's firmware (1.1.0), its signals, and that Loxone receives its values.
- [ ] **Step 3:** Build the branch image on the Pi and restart only loxmatter: `docker compose up -d --no-deps loxmatter` in `/home/pi/matter-loxone/deploy/testhost` (never without `--no-deps`: it would replace the hand-started old matter-server with matterjs-server, which starts with zero nodes).
- [ ] **Step 4:** WebUI → System → "Check for updates now". Compare with the design's section 3 table (five offers, `None` for 11, 15, 21, 26, no source for 23/24).
- [ ] **Step 5:** Start a recorder in the loxmatter container that prints every `0/42/*` and `0/40/9` change of node 22 with a timestamp (a scratch script subscribing via `MatterClient.subscribe_events` to `ATTRIBUTE_UPDATED` for node 22). Then, with Lucien's go-ahead, install on node 22 from the dialog.
- [ ] **Step 6:** Record: whether the old image transfers at all; whether `update_node` returned at once; the `UpdateState` sequence; how often `UpdateStateProgress` arrived; total duration. Check afterwards: node 22 reports 1.2.0, its signals are unchanged, Loxone receives, the overview and expert area show Matter 1.4.
- [ ] **Step 7:** With Lucien's go-ahead, a second install (BILRESA node 4 or ALPSTUGA node 12); restart loxmatter mid-transfer (`docker restart loxmatter`); the dialog must show the running install again after the page reconnects.
- [ ] **Step 8:** Write the measurements into the design, section 10.2; adjust `JobTiming` if the measured transfer needs it; replace the `UpdateState` sequences in `tests/firmware/test_firmware_job.py` with the recorded one; commit (`docs(specs): record the first firmware update on the test Pi`, `test(firmware): replay the recorded update sequence`).

The release gate (design 10.3, a run against matterjs-server) stays open after this task. The changelog entry stays under "Unreleased" until it has passed.
