# Radios Card: Thread Off After Rollback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a failed request that wanted Thread on, the radios card says plainly that Thread is off, offers "Try again", and always shows the Thread state.

**Architecture:** The bridge keeps a copy of the request it hands to the sidecar (`radios-last-request.json`) and exposes it as `job.requested` in `GET /api/radios`. The Alpine card derives the new result sentence, the retry button and a Thread state line from `job`, `job.requested` and `current`.

**Tech Stack:** FastAPI, Python 3.12, Alpine.js (served `index.html` + `app.js`), pytest with the node-based `_app_state` harness in `tests/api/test_web.py`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-14-radios-thread-off-after-rollback-design.md` — exact texts, keys and conditions are there; copy them verbatim.
- Everything in English except `de:` values in `src/loxmatter/i18n/strings.yaml`; German uses formal "Sie".
- User-visible strings go through i18n with `en` and `de`.
- No change to `deploy/updater/*`, `.env` handling, or anything the updater image contains.
- No plan task numbers, "Task N" or TRANSITIONAL markers in `src/`.
- Conventional Commits in English, ending with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Tests run in the foreground only (never `run_in_background`, never Monitor). Run only the named test files, not the whole suite.
- Every new protective test is fault-injected once: break the code it protects, see the test fail, restore. Purge `__pycache__` when injecting into Python; never `git checkout` over uncommitted work.
- Checks before each commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run python scripts/check_language.py`.

---

### Task 1: The bridge remembers the request and reports it

**Files:**
- Modify: `src/loxmatter/radios/sidecar.py` (`request_radios`, new `RadiosRequestRecord`, new `read_last_request`)
- Modify: `src/loxmatter/api/radios.py` (`get_radios` job dict)
- Test: `tests/radios/test_sidecar.py`, `tests/api/test_radios_api.py`

**Interfaces:**
- Produces:
  - `LAST_REQUEST_FILE = "radios-last-request.json"` (module constant in `sidecar.py`)
  - `@dataclass(frozen=True) class RadiosRequestRecord: id: str; thread: ThreadRequest | None; bluetooth: BluetoothRequest | None`
  - `def read_last_request(update_dir: Path) -> RadiosRequestRecord | None`
  - `GET /api/radios` → `job["requested"]`: `{"thread": {"enabled": bool, "device": str | None} | None, "bluetooth": {"adapter": int} | None} | None`

- [ ] **Step 1: Write failing tests in `tests/radios/test_sidecar.py`**
  - `request_radios(...)` writes `radios-last-request.json` whose JSON equals the JSON of `radios-request.json` (same `id`, `thread`, `bluetooth`, `requested_at`).
  - `read_last_request` after `request_radios(thread=ThreadRequest(True, "/dev/serial/by-id/x"), bluetooth=None)` returns `RadiosRequestRecord(id=<returned id>, thread=ThreadRequest(True, "/dev/serial/by-id/x"), bluetooth=None)`.
  - `read_last_request` returns `None` for: no file; `"not json"`; `[]`; an object without a string `id`.
  - A half with the wrong shape (e.g. `"thread": {"enabled": "yes"}` or `"bluetooth": {"adapter": true}`) reads as `None` for that half while the other half and `id` survive.
  - When writing the copy raises `OSError` (monkeypatch so only the copy's write fails, e.g. make `radios-last-request.json.tmp` a directory), `request_radios` still returns the job id and `radios-request.json` exists.
- [ ] **Step 2: Run `uv run pytest tests/radios/test_sidecar.py -q`** — the new tests fail.
- [ ] **Step 3: Implement.** In `request_radios`, after `os.replace(temp, update_dir / "radios-request.json")`, write the same `body` to `LAST_REQUEST_FILE` through `LAST_REQUEST_FILE + ".tmp"` and `os.replace`, inside `try/except OSError: pass` with a one-line comment saying why (the request is already on its way; the copy only improves a later message, spec §3). `read_last_request` parses defensively like `read_radios_state`: `thread` valid only when a dict with bool `enabled` and `device` `None` or `str`; `bluetooth` valid only when a dict with a non-bool `int` `adapter`.
- [ ] **Step 4: Write failing tests in `tests/api/test_radios_api.py`** (follow the file's existing helpers `_radios_heartbeat`, `_update_heartbeat`, `_host`):
  - radios state with `id` `"job-1"`, phase `failed`, and a last-request file with id `"job-1"` and `thread: {"enabled": true, "device": "/dev/serial/by-id/x"}`, `bluetooth: null` → `job["requested"] == {"thread": {"enabled": True, "device": "/dev/serial/by-id/x"}, "bluetooth": None}`.
  - same with the record's id `"job-0"` → `job["requested"] is None`.
  - no last-request file → `job["requested"] is None`.
  - a `POST /api/radios` followed (after writing a radios state carrying the returned id) by `GET` reports the posted halves.
- [ ] **Step 5: Implement** in `get_radios`: read `read_last_request(update_dir)` and add `"requested"` to the job dict (use `dataclasses.asdict` on the halves, or explicit dicts).
- [ ] **Step 6: Run `uv run pytest tests/radios/test_sidecar.py tests/api/test_radios_api.py -q`** — all pass. Fault-inject: (a) skip writing the copy, (b) drop the id comparison, (c) accept a bool `adapter`; each must fail at least one new test.
- [ ] **Step 7: Checks and commit** `feat(radios): keep the request the bridge sent so the card can name it`.

---

### Task 2: The card names Thread off, offers Try again, shows the Thread state

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml` (next to the other `web.radios.result_*` keys)
- Modify: `src/loxmatter/web/app.js` (near `radiosResultKey()`, ~line 5236)
- Modify: `src/loxmatter/web/index.html` (radios card: under the Thread select ~line 2688, result block ~line 2761)
- Test: `tests/api/test_web.py` (radios section, ~line 8800; reuse `RADIOS_READY`, `_radios_values`, `_app_state`)

**Interfaces:**
- Consumes: `radios.job.requested` from Task 1 (may be absent or `null`).
- Produces (app.js methods): `radiosThreadLeftOff() -> boolean`, `retryRadios() -> void`, `radiosThreadStatus() -> {key: string, warn: boolean} | null`.

- [ ] **Step 1: Strings.** Add `web.radios.result_failed_thread_off`, `web.radios.retry`, `web.radios.thread_status_off`, `web.radios.thread_status_running`, `web.radios.thread_status_not_running` with the exact en/de texts of spec §4–§6.
- [ ] **Step 2: Failing tests in `tests/api/test_web.py`** (append after the existing radios tests; each via `_radios_values`):
  - Base failing job: `state.radios.job = {id: 'j', phase: 'failed', steps: [...], error: 'verify_thread_failed', rolled_back: true, healthy: true, requested: {thread: {enabled: true, device: '/dev/serial/by-id/usb-A'}, bluetooth: null}}` and `state.radios.current.thread_enabled = false; state.radios.current.thread_device = null; state.radios.current.otbr_running = false`. Expect `radiosThreadLeftOff() === true` and `radiosResultKey() === 'web.radios.result_failed_thread_off'`.
  - Each flipped once keeps `radiosThreadLeftOff() === false` and the old key: `healthy: false` → `result_failed_unhealthy`; `error: 'interrupted'` → `result_interrupted`; `requested: null` → `result_failed_restored`; `requested.thread.enabled: false` → `result_failed_restored`; `current.thread_enabled: true` → `result_failed_restored`; `phase: 'done'` → `result_done`. (Use whatever makes `radiosJobRunning()` false for terminal phases, as existing tests do.)
  - `retryRadios()` with `requested.bluetooth: {adapter: 1}` sets `radiosDraft` to `{threadDevice: '/dev/serial/by-id/usb-A', bluetoothAdapter: 1}`, `radiosDirty === true`, `radiosConfirming === true`; with `bluetooth: null` the adapter stays at the current value.
  - `radiosThreadStatus()`: `current` null → `null`; off → `{key: 'web.radios.thread_status_off', warn: false}`; on+running → `thread_status_running`, `warn: false`; on+not running → `thread_status_not_running`, `warn: true`; a running job (phase e.g. `'apply'` in its steps, as existing running-job tests set it) → `null`.
  - Markup (served `index.html`): the retry button's `@click` is `retryRadios()` and its `x-show` contains `radiosThreadLeftOff()`, `radios.sidecar === 'ready'`, `!radiosJobRunning()` and `!radiosBusy`; the state line calls `radiosThreadStatus()` and binds `banner warn` vs `hint` from `.warn`. Follow the existing extraction helpers (`_attr_before_t_key`, `_x_show_expr`).
  - Both locales define the five new keys (follow the existing strings-presence tests in the file).
- [ ] **Step 3: Run `uv run pytest tests/api/test_web.py -q -k radios`** — new tests fail.
- [ ] **Step 4: Implement app.js.**
  - `radiosThreadLeftOff()`: `const job = this.radios?.job; return Boolean(job) && !this.radiosJobRunning() && job.phase === "failed" && job.error !== "interrupted" && job.healthy !== false && job.requested?.thread?.enabled === true && this.radios?.current?.thread_enabled === false;`
  - In `radiosResultKey()`'s `failed` branch, after the `interrupted` return and before the healthy/unhealthy return: `if (this.radiosThreadLeftOff()) return "web.radios.result_failed_thread_off";`
  - `retryRadios()`: returns early unless `radiosThreadLeftOff()`; sets `threadDevice` to `requested.thread.device ?? ""`, `bluetoothAdapter` to `requested.bluetooth.adapter` when that half is not null (else keeps the draft's), `radiosDirty = true`, then `this.askApplyRadios()`.
  - `radiosThreadStatus()`: `null` when `!this.radios?.current` or `this.radiosJobRunning()`; else per spec §6 table.
  - Doc comments explain *why* (the 13 September incident: the old sentence read as harmless and the select resynced to "Thread off"), in the file's existing comment style, without task numbers.
- [ ] **Step 5: Implement index.html.**
  - Under the Thread select row (before the `radiosZigbeeHint()` paragraph): `<template x-if="radiosThreadStatus()"><p :class="radiosThreadStatus().warn ? 'banner warn' : 'hint radios-row-hint'" x-text="t(radiosThreadStatus().key)"></p></template>`.
  - In the result block, after the result paragraph: `<button class="primary" x-show="radiosThreadLeftOff() && radios.sidecar === 'ready' && !radiosJobRunning() && !radiosBusy" x-cloak @click="retryRadios()" x-text="t('web.radios.retry')"></button>`.
- [ ] **Step 6: Run `uv run pytest tests/api/test_web.py -q -k radios`** — pass. Then the whole `uv run pytest tests/api/test_web.py -q` in the foreground. Fault-inject: (a) drop the `healthy !== false` clause, (b) drop the `requested.thread.enabled` clause, (c) remove the `retryRadios` bluetooth branch, (d) swap `warn` for not-running, (e) remove `!radiosJobRunning()` from the button's `x-show`; each must fail a test.
- [ ] **Step 7: Checks and commit** `feat(web): say when a failed radios change left Thread off and offer a retry`.

---

### After the tasks (controller)

- Run the Alpine bindings in a throwaway browser harness against the served `index.html`/`app.js` with a stubbed `/api/radios` returning the §4 failing job: banner text, retry button and the "Thread is off." line appear; the button opens the confirmation dialog.
- `CHANGELOG.md` `[Unreleased]`: an entry under "Changed"/"Fixed" describing the clearer card.
- Test parts A1 (`tests/api`), A2, C in the foreground; B1/B2 are untouched by this change but run if time allows before merge.
