# Zigbee as the Second Device Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pair a Zigbee device from the web UI, have loxmatter configure it to report, and have its value arrive in Loxone as an ordinary signal — with Matter untouched and still mandatory.

**Architecture:** A new package `loxmatter/zigbee/` whose `ZigbeeSource` satisfies the existing `DeviceSource` protocol without an adapter, exactly as `BridgeMatterClient` does. zigpy runs in-process; everything Zigbee-specific is translated into the Matter data model at that package's own edge, so `matter/discovery.py`, `profiles/`, `commands/translate.py`, the store, `loxone/runtime.py`, the export and groups stay unchanged for both technologies. The web UI gains a Zigbee commissioning tab and a Zigbee row on the radios card.

**Tech Stack:** Python 3.12, zigpy 2.2.0 + bellows 1.0.1 + zha-quirks 2.2.2 (in-process, no MQTT, no second container), FastAPI, SQLite, Alpine.js (vendored, no build step), Docker Compose, pytest, node for `app.js` tests.

**Spec:** `docs/superpowers/specs/2026-09-12-zigbee-source-design.md` — read it in full before starting any task, together with `.superpowers/sdd/zigbee/research-2.md` (cited in the spec as its section letters).

**Read also:** `docs/superpowers/specs/2026-09-11-device-source-boundary-design.md` sections 1, 3 and 9 — the boundary this plan fills, and the open points it closes.

## Spec Corrections — Read Before Task 1

The spec was written on 12 September 2026, before sub-project 2a-1 finished. Five of its statements no longer describe the code. **Plan and implement against the code, not against these sentences**; each correction below was verified in the worktree on 12 September 2026.

1. **Spec §1 says `src/loxmatter/api/radios.py` does not exist and the radios card "does not exist yet".** It does. `src/loxmatter/api/radios.py` serves `GET`/`POST /api/radios`, `build_radios_router` is wired into `loxone/server.py`, the card is in `index.html` (the `web.radios.heading` card) with its handlers in `app.js`, and `deploy/testhost/docker-compose.yml` mounts `/dev:/host/dev:ro` into `loxmatter` and `loxmatter-updater`. **No task in this plan carries 2a-1's API, card or mount.** Spec §12 open point 1 ("decide whether to land 2a-1 first or carry it") is therefore answered: neither, it is already there.

2. **Spec §8.1 names the bind target `/host-dev`. The repository uses `/host/dev`.** `deploy/testhost/docker-compose.yml` mounts `/dev:/host/dev:ro`, `radios-once.sh` defaults `HOST_DEV=/host/dev`, `build_app` passes `radios_host_dev`, and `tests/test_compose_profiles.py::test_only_the_bridge_and_the_updater_see_the_host_dev_tree_read_only` asserts the mount list is exactly `["/dev:/host/dev:ro"]`. **Keep `/host/dev`.** Renaming it would churn the sidecar, the API and a passing test for nothing. What is genuinely missing from Compose is only the two `device_cgroup_rules` (Task 2).

3. **Spec §3.2 says the Zigbee row joins the radios card's sidecar request as a third half. It must not.** The sidecar's job (`deploy/updater/radios-once.sh`) applies a change by rewriting `.env` and recreating a *container* — `otbr` for Thread, `matter-server` for Bluetooth. zigpy, by the programme's own decision, runs **in-process inside the bridge**. Routing a Zigbee stick change through the sidecar would mean recreating the `loxmatter` container, which (a) drops the Matter link and every device with it, for a change to a radio Matter does not use, (b) kills the HTTP request that asked for it along with the page the user is looking at, and (c) is the self-replacement the project already measured as unworkable. So:

   **The sidecar request keeps exactly two halves, `thread` and `bluetooth`, unchanged.** `RadiosIn`, `request_radios`, the `radios-request.json` schema in `radios-once.sh` and `tests/test_updater_radios_script.py` are **not touched by this plan**. The Zigbee stick is a bridge-owned setting, persisted in loxmatter's own `setting` table (the `BridgeSettingsStore`/`ResendSettingsStore` pattern) and applied **in process** by reconnecting the source — instantly, with no container restart and no downtime for any other radio. The null-half guarantee 2a-1 built is thereby honoured more completely than a third half could honour it: changing Zigbee cannot touch Thread or Bluetooth because it never reaches the file they travel in, and changing Thread or Bluetooth cannot touch Zigbee because the sidecar writes no key it reads. Task 11 builds this; Task 13 puts it on the card with its own Apply, next to but separate from the sidecar-owned rows.

4. **Spec §4.7 says `api/control.py` maps a bare `except Exception` to 502 "at line 341".** The seam is real but the line numbers have moved (the mapping now sits at `api/control.py:338-347`, `SourceNotConfiguredError` → 503 and the broad catch → 502). `api/devices.py`'s removal route catches `MatterUnavailableError` only, at `devices.py:637` — that is the 500 the spec predicts, and Task 5 fixes it. Cite the seams by name, not by line number.

5. **Spec §5.5 lists a `1280` profile-table entry under "cluster / entry" as `2: {slug: ias_zone_status, functional: false}`.** Correct, but note the trap `clusters.yaml` documents at cluster 40: adding an `attributes:` section to a cluster makes `relevance.is_functional` filter *every* attribute of that cluster through `names_element`. That is the wanted behaviour here; it is called out so nobody "tidies" the entry into a bare `rank`.

Additionally, two spec sentences are correct but easy to misread and are restated as constraints below: the profile-table additions of §5.5 are safe for existing installations (keys are assigned only when a row is created), and §4.2's "`snapshots()` and `subscribe()` must tolerate being called while disconnected" is load-bearing, not a nicety — `cli._run` calls `attach` for every source unconditionally.

## Global Constraints

Every task's requirements implicitly include this section.

- **Worktree (absolute path, use it in every command):** `/Users/lucienkerl/Development/matter-loxone/.claude/worktrees/german-to-english-translation-f84003`, branch `claude/radios-in-web-ui`. Never `cd` elsewhere.
- **Everything is written in English:** code, comments, docstrings, test names, filenames, commit messages. German appears **only** as `de:` values in `src/loxmatter/i18n/strings.yaml` — shipped product content, never translated — and as quoted data in `tests/` and `tests/fixtures/`.
- **Every string a user can see goes through i18n**, with **both** an `en` and a `de` value, resolved at call time: `i18n.t("...")` in Python, `t("...")` in `app.js`. This includes every `HTTPException` detail, because the UI displays it. `src/loxmatter/api/devices.py` and `src/loxmatter/projectsync/` show the pattern. Writing the English sentence directly does not solve the problem, it moves it.
- **`scripts/check_language.py` enforces the language rule and runs in CI.** Its green light is a vocabulary result, not a proof — it can only find words it knows. Read what you write. Do not exempt a whole file; if a word list needs fixing, fix the word list.
- **New source, script and test files start with the 15-line GPL header** copied verbatim from `src/loxmatter/profiles/categories.py` lines 1-15 (in shell scripts, after the `#!/bin/sh` line).
- **Dependency versions are exactly those research F.1 resolved:** `zigpy` 2.2.0, `bellows` 1.0.1, `zha` 2.2.2, `zha-quirks` 2.2.2, `zigpy-znp` 1.1.0, `zigpy-deconz` 1.0.0, `zigpy-xbee` 0.22.0, `zigpy-zigate` 0.14.0. No existing loxmatter pin may move (32 added, zero updated, zero removed).
- **`zha` and `zigpy` both ship a top-level `tools/` package.** loxmatter can never add a module named `tools`.
- **`zhaquirks.setup()` costs 2.1-2.7 s on an M1 and an estimated 9-15 s on a Pi 4.** Never call it at import time, never on a request path. Once per process, in an executor, in a background task, only when a Zigbee radio is configured. Log the measured duration.
- **Never write a path whose value is `None` into a `NodeSnapshot`.** `Store.register_signals` computes `exported` only when the row is created, and a `None` value classifies as `Exportability.NONE`, so such a signal stays unexported forever. Leave the path out; when the first real value arrives, go through `follow`/`on_node_snapshot`, which creates the row properly.
- **Zigbee is optional, Matter is not.** A missing or broken Zigbee stick degrades the bridge and never stops it. `client.connect()` failing still ends startup; `zigbee.connect()` failing never does.
- **Nothing past `loxmatter/sources/` may learn that Zigbee exists.** Shared code must never import zigpy, and no `except` clause outside `loxmatter/zigbee/` may name a zigpy exception type.
- **`deploy/updater/update-once.sh` is not modified by any task.** Neither is `deploy/updater/radios-once.sh`, the `radios-request.json` schema, `RadiosIn`, `request_radios` or `tests/test_updater_radios_script.py` (see Spec Correction 3).
- **The full test suite no longer fits in one command.** It takes about 10:45 and the Bash tool's ceiling is 10:00. Run it in **four parts, each in the foreground**, never in the background:

  ```bash
  # A1 - tests/api MUST be invoked alone: a pre-existing conftest.py name
  # collision aborts collection when it is passed alongside tests/projectsync.
  uv run pytest -q tests/api
  # A2
  uv run pytest -q tests/auth tests/commands tests/devtools tests/diagnostics tests/export tests/loxone tests/matter tests/model tests/profiles tests/projectsync tests/radios tests/sources tests/zigbee
  # B
  uv run pytest -q tests/test_install_script.py tests/test_update_script.py tests/test_updater_script.py tests/test_updater_radios_script.py
  # C
  uv run pytest -q tests/test_build_arguments.py tests/test_cli.py tests/test_cli_language.py tests/test_compose_profiles.py tests/test_export_cli.py tests/test_i18n.py tests/test_otbr_watchdog.py tests/test_store_path.py tests/test_update_check.py tests/test_update_module.py tests/test_updater_entrypoint.py tests/test_updater_image.py tests/test_version.py
  ```

  The four parts cover `tests/` exactly, so **the pass counts must sum to the whole-suite total**. A sum that does not is evidence something was silently skipped — stop and report it. Baseline before this plan: **2063 passed, 2 skipped**.

  **Measured baseline, 12 September 2026, after Task 1 landed (`f7b6b20`): 2069 passed, 2 skipped.** This number is authoritative and supersedes the arithmetic in Task 1's own steps, which predicted 2068 — Task 1 added six tests, not the five it estimated. Task 1 is complete and committed; its step text is left as the historical record rather than rewritten. Every forward-looking total in Tasks 2 onwards is counted from **2069**.

  **Measured baseline, 12 September 2026, after Task 8 landed (`464a078`): 2249 passed, 2 skipped.** Tasks 2 through 8 stopped restating a running total in their own "run the checks" steps (Task 4's Step 13 already switched to "confirm the new total equals the previous total plus the tests added here" rather than a hard number), so this is the number a Task 9, 10 or 11 baseline run should actually see before that task's own new tests are added — including the nine new tests this correction adds to Task 10 and the four it adds to Task 11's Step 1 (Task 9 adds its own, separately). Every count stated inside Tasks 10 and 11 below (their fault counts, in particular) is counted against this baseline; it does not itself change when a task merely rearranges *which* file a test lives in.

  `tests/zigbee` is listed in part A2 above because Task 1 creates it and Tasks 6-9 fill it with the largest suites in this plan — and those suites are, by this plan's own admission, the only evidence that will exist for the translation until a second stick is bought. A directory that no part names is a directory CI never runs. **The only run where `tests/zigbee` must be dropped from the A2 command is Task 1 Step 1**, the baseline, because the directory does not exist yet and pytest exits with `ERROR: file or directory not found`. From Task 1 Step 8 onwards it is always included.
- **Every test that names a protection must be shown to catch it.** Introduce the stated fault, run the test, see it **FAIL**, revert, see it **PASS**, and paste both outputs into the task report. A reviewer on this branch found tests that passed with their stated fault in place; several had to be rewritten. A test that stays green with the fault in place is wrong.
- **A Python test that fetches a page proves only that the file was served.** It proves nothing about Alpine bindings, and a markup-substring assertion cannot fail for a binding that is merely wrong. Any web task must use the techniques already in `tests/api/test_web.py`: `_app_state(...)` runs the real `app.js` in node, `_x_show_expr(markup, key)` and `_running_step_lis(markup)` extract the real expressions out of the served markup rather than retyping them, and `_js_constant(name)` reads a constant out of the file.
- **Anything that writes durable state must be recoverable from an interruption.** An interrupted radios pass used to leave a non-terminal phase that froze the card forever and could only be cleared over SSH. Plan the interrupted case explicitly wherever this plan persists state (Task 9's pending-configuration rows, Task 11's radio setting).
- **Long-running work must say what it is doing throughout.** If a step can take tens of seconds, the UI shows progress for the whole of it, or the user will read a healthy job as a dead one.
- **Run `uv run ruff format .` before the checks.** The five checks CI runs, all of which must pass at the end of every task: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, the four-part pytest above, `uv run python scripts/check_language.py`. Note that `ruff format` also formats fenced Python inside Markdown, so a reflowed code sample in a document can break the format check.
- **Commit messages:** Conventional Commits, English, ending with:

  ```
  Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
  ```

## File Map

| File | Responsibility | Task |
|---|---|---|
| `pyproject.toml`, `uv.lock`, `Dockerfile` | zigpy dependency tree, `UV_COMPILE_BYTECODE=1` | 1 |
| `src/loxmatter/zigbee/__init__.py` (new) | package docstring, public names | 1 |
| `src/loxmatter/zigbee/quirks.py` (new) | one-shot `zhaquirks.setup()` in an executor, measured | 1 |
| `deploy/testhost/docker-compose.yml` | `device_cgroup_rules`, otbr `&uart-exclusive` | 2 |
| `src/loxmatter/profiles/clusters.yaml` | occupancy, BooleanState, illuminance, colour XY, IAS raw, `768` cmd 7 | 3 |
| `src/loxmatter/commands/color.py`, `commands/translate.py` | RGB → CIE xy, `(768, 7)` MoveToColor | 3 |
| `src/loxmatter/radios/fingerprints.py` (new) | VID:PID + by-id → radio type, baud, flow control | 4 |
| `src/loxmatter/sources/__init__.py` | `DeviceUnreachableError`, bounded `Sources.send`, technology display names | 5 |
| `src/loxmatter/matter/models.py` | lenient `parse_technology` | 5 |
| `src/loxmatter/api/devices.py`, `api/control.py`, `commands/fanout.py` | one error vocabulary, 502 vs 503 | 5 |
| `src/loxmatter/zigbee/translate.py` (new) | snapshot synthesis, device types, IAS, sentinels, command lists, argument names | 6 |
| `src/loxmatter/zigbee/source.py` (new) | `ZigbeeSource`: lifecycle, events, send, remove | 7 |
| `src/loxmatter/zigbee/availability.py` (new) | last-seen checker, ping before offline, link-loss sweep | 8 |
| `src/loxmatter/zigbee/configure.py` (new) | configure-on-join, IAS enrolment, sleepy deferral | 9 |
| `src/loxmatter/model/zigbee_pending_store.py` (new) | per-(device, cluster) configuration-pending rows | 9 |
| `src/loxmatter/cli.py`, `src/loxmatter/loxone/runtime.py` | heartbeat meaning, `zigbee_connected`, startup wiring | 10 |
| `src/loxmatter/model/zigbee_settings_store.py` (new) | the persisted Zigbee radio setting | 11 |
| `src/loxmatter/radios/inventory.py` | resolved device identity for Thread exclusion | 11 |
| `src/loxmatter/api/zigbee.py` (new) | radio setting, permit, pairing rows, retry, remove | 11, 12 |
| `src/loxmatter/web/index.html`, `app.js`, `style.css` | Zigbee row, badge, pairing tab | 13, 14 |
| `src/loxmatter/i18n/strings.yaml` | every `api.zigbee.*`, `web.zigbee.*`, `web.radios.zigbee_*` key | 3, 5, 11-14 |
| `README.md`, `CHANGELOG.md`, `docs/superpowers/specs/2026-09-09-first-run-checklist.md` | residual exposure, change notes, hardware checklist | 15 |
| tests | `tests/zigbee/`, `tests/radios/`, `tests/api/`, `tests/profiles/`, `tests/commands/`, `tests/test_compose_profiles.py`, `tests/api/test_web.py` | 1-15 |

---

### Task 1: The Dependency Tree and the Quirks Warm-Up

zigpy, bellows and the whole `zha` + five-radio-library tree enter the project, and the one expensive thing in it — `zhaquirks.setup()` — is made to run exactly once per process, in an executor, off every request path.

**Files:**
- Modify: `pyproject.toml`, `uv.lock`, `Dockerfile`, `tests/test_build_arguments.py`
- Create: `src/loxmatter/zigbee/__init__.py`, `src/loxmatter/zigbee/quirks.py`, `tests/zigbee/test_quirks.py`, `tests/zigbee/test_dependencies.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `loxmatter.zigbee.quirks.ensure_quirks_loaded(*, setup: Callable[[], None] = _default_setup) -> float` — idempotent per process, runs `setup` in the default executor, returns the measured wall-clock seconds of the first (and only) run; later calls return the same recorded duration without running anything.
  - `loxmatter.zigbee.quirks.quirks_loaded() -> bool`

- [ ] **Step 1: Baseline.** Run all four suite parts in the foreground (see Global Constraints) and record the four pass counts and their sum. Expected: **2063 passed, 2 skipped** in total. If the sum differs, stop and report BLOCKED rather than continuing on an unknown baseline.

  **For this run only, drop `tests/zigbee` from the end of the A2 command** — Step 2 creates that directory, and pytest exits with `ERROR: file or directory not found` on a path that does not exist yet. Every later run in this plan, starting at Step 8, uses the A2 command exactly as Global Constraints spells it.

- [ ] **Step 2: Write the failing tests** `tests/zigbee/test_quirks.py` (GPL header first; no `__init__.py` in `tests/zigbee/`, as in `tests/matter/`):

```python
"""The one-shot quirks warm-up (design 2026-09-12 section 8.4, research F.4).

`zhaquirks.setup()` costs 2.1-2.7 s on an M1 Pro and an estimated 9-15 s on
a Pi 4, and it imports 857 modules. It therefore may not run at import time,
may not run twice, and may not run on the event loop - `cli._run` starts
uvicorn after `attach`, so anything that blocks the loop here is time the
web UI and `/health` spend not answering.

The fake `setup` below sleeps with `time.sleep`, not `asyncio.sleep`, on
purpose: only a genuinely blocking call can demonstrate that the executor
is doing its job."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from loxmatter.zigbee import quirks as quirks_module
from loxmatter.zigbee.quirks import ensure_quirks_loaded, quirks_loaded


@pytest.fixture(autouse=True)
def _fresh_process_state():
    """The three tests below share one process-wide flag - which is exactly
    what the module under test is for - so each starts from a clean one
    rather than depending on file order."""
    quirks_module._reset_for_tests()
    yield
    quirks_module._reset_for_tests()


async def test_the_warm_up_runs_exactly_once_however_often_it_is_awaited():
    """Fault to prove it: drop the `_done` guard in `ensure_quirks_loaded`
    and let every caller run `setup`. Two concurrent callers then pay the
    9-15 s cost twice on a Pi."""
    calls: list[int] = []

    def setup() -> None:
        calls.append(1)

    first, second, third = await asyncio.gather(
        ensure_quirks_loaded(setup=setup),
        ensure_quirks_loaded(setup=setup),
        ensure_quirks_loaded(setup=setup),
    )

    assert calls == [1]
    # Every caller learns the SAME measured duration, not 0.0 for the
    # losers of the race - the log line in `cli._run` reports whatever it
    # is handed, and "0.0 s" would be a measurement nobody took.
    assert first == second == third
    assert quirks_loaded() is True


async def test_the_warm_up_does_not_block_the_event_loop():
    """Fault to prove it: call `setup()` directly instead of handing it to
    `run_in_executor`. `ticks` then stays at 0 or 1, because nothing else
    on the loop gets to run for the whole of the warm-up."""
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    def setup() -> None:
        time.sleep(0.3)

    beat = asyncio.ensure_future(ticker())
    try:
        await ensure_quirks_loaded(setup=setup)
    finally:
        beat.cancel()

    # 0.3 s of blocking against a 0.01 s tick: a loop that kept running
    # managed many ticks, a blocked one managed almost none.
    assert ticks >= 5, f"the event loop was blocked during the warm-up ({ticks} ticks)"


async def test_the_warm_up_runs_off_the_event_loop_thread():
    """The direct statement of the same protection, independent of timing:
    `setup` must observe a different thread than the loop's own."""
    loop_thread = threading.get_ident()
    seen: list[int] = []

    await ensure_quirks_loaded(setup=lambda: seen.append(threading.get_ident()))

    assert seen and seen[0] != loop_thread
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest -q tests/zigbee/test_quirks.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'loxmatter.zigbee'`.

- [ ] **Step 4: Add the dependencies.** Append to the `dependencies` list in `pyproject.toml`, inside the existing array, with this comment:

```toml
    # Zigbee as the second device source (design 2026-09-12). zigpy runs
    # IN PROCESS - no MQTT, no broker, no second container (boundary design
    # 2026-09-11, section 1).
    #
    # `zha` and the five radio libraries cannot be avoided and are not
    # optional extras (research F.6, proved twice in a scratch venv):
    # `zha-quirks` imports `zha.quirks`, and `zhaquirks.setup()` reaches
    # `zha/application/const.py`, which imports ALL SIX radio applications at
    # module level. Either the whole tree is installed, or quirks are dropped
    # and Tuya and Aqara devices misbehave - `override-dependencies` would
    # resolve and then crash at runtime. gpiozero is equally unavoidable
    # (`zigpy_zigate/common.py` imports it at module level).
    #
    # Measured on 11 September 2026 (research F.1): 32 packages added, zero
    # updated, zero removed - no existing loxmatter pin moves, because zigpy
    # declares its requirements unpinned. All 63 install from wheels on
    # arm64, no source builds (F.2). Licences are all compatible with
    # GPL-3.0-or-later (F.5).
    "zigpy>=2.2.0",
    "bellows>=1.0.1",
    "zha-quirks>=2.2.2",
```

Then regenerate the lock:

```bash
cd /Users/lucienkerl/Development/matter-loxone/.claude/worktrees/german-to-english-translation-f84003
uv lock
```

Expected: `Resolved 83 packages`, with 32 added and **zero updated, zero removed**. Verify that claim before moving on:

```bash
git diff --stat uv.lock
git diff uv.lock | grep -E '^-name = |^-version = ' | head -20
```

Expected: the second command prints **nothing** — no existing package line is removed or changed. If any existing pin moved, stop and report it; that is a finding the spec's F.1 measurement did not predict. If `uv lock` cannot reach the network in this environment, stop and report BLOCKED — do not hand-edit `uv.lock`.

- [ ] **Step 5: Write the dependency guard** `tests/zigbee/test_dependencies.py` (GPL header first):

```python
"""What the lock file must keep true about the Zigbee tree (research F.1,
F.5, F.6).

Not a test that the packages are "installed somewhere" - that would be true
of any environment. It reads `uv.lock`, which is the file an image build and
a Pi install actually resolve from."""

from __future__ import annotations

import tomllib
from pathlib import Path

LOCK = Path(__file__).resolve().parent.parent.parent / "uv.lock"


def _locked() -> dict[str, str]:
    raw = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    return {package["name"]: package["version"] for package in raw["package"]}


def test_the_whole_zigbee_tree_is_locked_not_just_zigpy():
    """Fault to prove it: drop `zha-quirks` from pyproject.toml and relock.
    `zigpy` and `bellows` alone still resolve, and the bridge then pairs a
    Tuya device that never sends anything - the failure research F.6
    describes, which no import error announces."""
    locked = _locked()
    # The five radio libraries arrive through `zha`, which pins each of them
    # exactly; none of them is a direct dependency of loxmatter.
    for name in (
        "zigpy",
        "bellows",
        "zha",
        "zha-quirks",
        "zigpy-znp",
        "zigpy-deconz",
        "zigpy-xbee",
        "zigpy-zigate",
        "gpiozero",
    ):
        assert name in locked, f"{name} is missing from uv.lock"


def test_no_existing_pin_moved_for_zigbee():
    """The versions loxmatter already depended on before the Zigbee tree
    landed. zigpy declares its own requirements unpinned, which is why
    adding it moved nothing (research F.1) - if a later zigpy release does
    pin one of these, this test is where that surfaces, rather than in a
    behaviour change nobody connects to a lock file.

    Fault to prove it: change one of the expected versions below."""
    locked = _locked()
    assert locked["aiohttp"] == "3.14.3"
    assert locked["attrs"] == "26.1.0"
    assert locked["typing-extensions"] == "4.16.0"
    assert locked["click"] == "8.5.0"
```

Note for the implementer: the four versions in the second test are what research F.1 measured on 11 September 2026. Read the real values out of the regenerated `uv.lock` and use those; if any of them differs from the research figure, that is itself the finding — record it in the task report rather than quietly matching the file.

- [ ] **Step 6: Create the package.** `src/loxmatter/zigbee/__init__.py` (GPL header first):

```python
"""Zigbee as the second device source (design 2026-09-12).

Everything Zigbee-specific lives in this package and nowhere else. The
boundary is `loxmatter/sources/`: `ZigbeeSource` satisfies `DeviceSource`
without an adapter, exactly as `BridgeMatterClient` does, and produces the
same `NodeSnapshot` with Matter attribute paths, Matter device type IDs and
a synthesised `AcceptedCommandList`. Nothing past `loxmatter/sources/`
learns that Zigbee exists, and no `except` clause outside this package may
name a zigpy exception type (boundary design 2026-09-11, section 2; design
2026-09-12, section 2.1)."""
```

- [ ] **Step 7: Write the implementation** `src/loxmatter/zigbee/quirks.py` (GPL header first):

```python
"""The one-shot `zhaquirks.setup()` warm-up.

Measured inside `python:3.12-slim` arm64 on an M1 Pro (research F.4):
2.1-2.7 s warm, 3.1 s cold, RSS +73-75 MB, +857 modules. Extrapolated to a
Pi 4 that is 9-15 s, and to a Pi 5 4-6 s - on a cold SD card, worse.

Three rules follow from that number, and this module exists to hold all
three in one place:

1. **Never at import time.** `import loxmatter.zigbee.quirks` must stay
   cheap; the cost is paid by calling `ensure_quirks_loaded()`.
2. **Never on the event loop.** `cli._run` starts uvicorn only after
   `attach`, so a blocking warm-up there is time `/health` and the web UI
   spend not answering. It goes to the default executor.
3. **Never twice.** The flag is process-wide, which is exactly the scope of
   the registry `zhaquirks.setup()` fills.

Only called when a Zigbee radio is configured - an installation without one
never pays any of this."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_duration_seconds: float | None = None


def _default_setup() -> None:
    """The real warm-up. Imported inside the function, never at module
    level: the import itself is most of the cost this module exists to
    defer, and a test with a fake `setup` must not pay it."""
    import zhaquirks

    zhaquirks.setup()


def quirks_loaded() -> bool:
    return _duration_seconds is not None


def _reset_for_tests() -> None:
    """Only `tests/zigbee/test_quirks.py` calls this. The guard is
    process-wide by design, so a test that wants a fresh one has to say so
    explicitly rather than depend on module import order."""
    global _duration_seconds
    _duration_seconds = None


async def ensure_quirks_loaded(*, setup: Callable[[], None] = _default_setup) -> float:
    """Runs the quirks registry warm-up once per process; returns its
    measured duration in seconds.

    Every caller gets the same measurement, including the ones that arrived
    while the first was still running - the alternative, returning 0.0 to
    the losers of the race, would put a number nobody measured into the
    startup log.

    The `asyncio.Lock` rather than a bare flag check: two `attach` calls
    racing on startup would both see `None` and both start a 15-second
    import storm on a Pi.
    """
    global _duration_seconds
    if _duration_seconds is not None:
        return _duration_seconds
    async with _lock:
        if _duration_seconds is not None:
            return _duration_seconds
        started = time.monotonic()
        await asyncio.get_running_loop().run_in_executor(None, setup)
        _duration_seconds = time.monotonic() - started
        logger.info("zigbee quirks registry loaded in %.1f s", _duration_seconds)
        return _duration_seconds
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest -q tests/zigbee/`
Expected: PASS, 5 passed.

- [ ] **Step 9: Set `UV_COMPILE_BYTECODE` in the `Dockerfile`.** Insert immediately **before** the existing `RUN pip install --no-cache-dir uv==0.6.* ...` line:

```dockerfile
# Byte-compile at build time instead of at first import (research F.3, F.4;
# design 2026-09-12 section 8.4). Without this, the 462 quirk modules are
# compiled inside the container on first import - and again after every
# `--force-recreate`, i.e. after every update. On a Pi 4 that turns the
# measured 9-15 s warm-up into double-digit seconds more, exactly while the
# user is watching the update card. It costs about 16 MB in the image and
# makes startup deterministic.
ENV UV_COMPILE_BYTECODE=1
```

- [ ] **Step 10: Guard the Dockerfile change.** Append to `tests/test_build_arguments.py` (which already reads this file):

```python
def test_the_image_byte_compiles_at_build_time():
    """Without `UV_COMPILE_BYTECODE=1` the 462 zha-quirks modules are
    byte-compiled inside the container at first import, and again after
    every `--force-recreate` (research F.3/F.4) - double-digit seconds on a
    Pi 4, every update, with the user watching.

    Fault to prove it: delete the `ENV UV_COMPILE_BYTECODE=1` line."""
    dockerfile = (Path(__file__).resolve().parent.parent / "Dockerfile").read_text(encoding="utf-8")
    assert "UV_COMPILE_BYTECODE=1" in dockerfile
```

Note: `Path` and the file location are already imported and used in this module; reuse whatever constant it already defines for the Dockerfile path rather than building a second one.

- [ ] **Step 11: Prove each protection catches its fault.** For each of the four tests that names one, introduce the fault, run only that test, see it FAIL, revert, see it PASS. Paste both outputs into the report.

| Test | Fault to introduce |
|---|---|
| `test_the_warm_up_runs_exactly_once_however_often_it_is_awaited` | delete the two `if _duration_seconds is not None: return` guards |
| `test_the_warm_up_does_not_block_the_event_loop` | replace the `run_in_executor` call with a direct `setup()` |
| `test_the_whole_zigbee_tree_is_locked_not_just_zigpy` | remove `"zha-quirks>=2.2.2"` from `pyproject.toml` and relock |
| `test_the_image_byte_compiles_at_build_time` | delete the `ENV UV_COMPILE_BYTECODE=1` line |

- [ ] **Step 12: Run the checks.** `uv run ruff format .`, then `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, all four pytest parts in the foreground, `uv run python scripts/check_language.py`. The four parts must now sum to **2068 passed, 2 skipped** (baseline plus the five new tests).

- [ ] **Step 13: Commit**

```bash
cd /Users/lucienkerl/Development/matter-loxone/.claude/worktrees/german-to-english-translation-f84003
git add pyproject.toml uv.lock Dockerfile src/loxmatter/zigbee tests/zigbee tests/test_build_arguments.py
git commit -m "$(cat <<'EOF'
feat(zigbee): add the zigpy dependency tree and a one-shot quirks warm-up

zha and the five radio libraries cannot be separated from zha-quirks, so
the whole tree lands at once; measured, it moves no existing pin. The
warm-up costs an estimated 9-15 s on a Pi 4, so it runs once per process in
an executor and never on a request path, and the image now byte-compiles at
build time so it is not paid again after every --force-recreate.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Serial Access for the Container

The bridge can list sticks today but cannot open one: the `/dev:/host/dev:ro` mount supplies names, and the **cgroup rule is the grant**. Two rules are added, and otbr is given a lock on its own stick.

**Files:**
- Modify: `deploy/testhost/docker-compose.yml`, `tests/test_compose_profiles.py`
- Test: `tests/test_compose_profiles.py`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing importable. The `loxmatter` service gains `device_cgroup_rules`; the mount stays `/dev:/host/dev:ro` (see Spec Correction 2).

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_compose_profiles.py`:

```python
def test_the_bridge_may_open_serial_devices_without_naming_one():
    """Design 2026-09-12 section 8.1. A `devices:` entry cannot be used
    here: it fails the WHOLE stack at `docker compose up` when the node is
    absent (which is why otbr sits behind a profile), and it is copied into
    the container at create time, so hotplug is invisible. The cgroup rule
    grants the access instead, and the existing read-only /dev bind supplies
    the names.

    188 = USB serial converters (ttyUSB*), 166 = ACM USB modems (ttyACM*),
    both verified against the kernel's admin-guide/devices.txt (research
    F.8). A GPIO-UART hat would be 204:64 and is deliberately not granted.

    Fault to prove it: delete one of the two rules."""
    rules = _stack()["services"]["loxmatter"]["device_cgroup_rules"]
    assert "c 188:* rmw" in rules
    assert "c 166:* rmw" in rules


def test_only_the_bridge_may_open_serial_devices():
    """The rule is coarse - it reaches EVERY USB-serial adapter on the host,
    the Thread stick included (research F.9). That is acceptable for the one
    service that needs to open a Zigbee coordinator and for no other, and it
    is much narrower than `privileged: true`. Exclusion of the Thread stick
    itself is enforced in loxmatter, by resolved major:minor (section 3.2).

    Fault to prove it: add the same rules to `matter-server`."""
    for name, service in _stack()["services"].items():
        has_rules = "device_cgroup_rules" in service
        assert has_rules == (name == "loxmatter"), name


def test_otbr_asks_the_kernel_to_keep_its_stick_to_itself():
    """OpenThread takes flock + TIOCEXCL only when the radio URL carries
    `uart-exclusive` (research A.3); the compose file passed no lock at all.

    This is a SECOND layer, not the guarantee: TIOCEXCL is bypassed by a
    holder of CAP_SYS_ADMIN, which privileged otbr has. The real guarantee
    is that loxmatter never offers or accepts the Thread stick (section 3.2,
    Task 11).

    `RADIO_URL` is an ENVIRONMENT variable of the otbr service, not part of
    its `command:` - the image's "test" entrypoint reads it from the
    environment, and `command:` carries only `--backbone-interface
    ${BACKBONE_IF}`. Asserting against `command` would pass for the wrong
    reason today (the parameter is absent from it either way) and would keep
    passing after somebody deleted the lock.

    Fault to prove it: drop the parameter from RADIO_URL."""
    otbr = _stack()["services"]["otbr"]
    assert "uart-exclusive" in str(otbr["environment"]["RADIO_URL"])
```

The old rule this file has always enforced — that no non-Thread service names the radio device — is **already covered** by the existing `test_only_otbr_needs_the_radio_module`, which this task does not touch. A near-verbatim second copy of it was drafted here and deliberately dropped: it asserted the same thing over the same services and would have failed and passed in lockstep with the original, which makes it a maintenance cost with no independent protection. If the implementer believes the `device_cgroup_rules` change needs its own `devices:` guard, the right move is to extend the existing test's docstring with the new reason, not to add a second test.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_compose_profiles.py`
Expected: FAIL — `KeyError: 'device_cgroup_rules'` on the first new test, and an assertion failure on the `uart-exclusive` one.

- [ ] **Step 3: Add the cgroup rules.** In `deploy/testhost/docker-compose.yml`, in the `loxmatter` service, immediately after the existing `volumes:` block (the one ending with `- /dev:/host/dev:ro`):

```yaml
    # What actually grants the bridge permission to OPEN a serial device -
    # the /dev bind above only supplies stable names and hotplug visibility
    # (design 2026-09-12, section 8.1; research F.8/F.9). Container root
    # already holds CAP_MKNOD and Docker's default `c *:* m`, so it can
    # mknod a node itself; removing these rules denies the open with EPERM.
    #
    # NOT a `devices:` entry, for two measured reasons: `docker compose up`
    # fails the WHOLE stack when the named node is absent (the documented
    # reason otbr sits behind a profile), and a `devices:` node is copied in
    # at create time, so a stick plugged in later is invisible.
    #
    # Majors verified against the kernel's admin-guide/devices.txt:
    #   188 = USB serial converters (ttyUSB*: CP210x, CH34x, FTDI)
    #   166 = ACM USB modems (ttyACM*: CDC-ACM)
    # A GPIO-UART hat would be 204:64 and is deliberately NOT granted; add
    # `c 204:64 rmw` only for an installation that has one.
    #
    # The rule is coarse: it reaches every USB-serial adapter on the host,
    # including the Thread RCP, whose RADIO_DEVICE defaults to a major-188
    # node. Exclusion is enforced in loxmatter by resolved major:minor
    # (design section 3.2), not here - and this is still a far smaller blast
    # radius than `privileged: true`. It does not reach block devices,
    # /dev/mem or i2c, which stay EPERM even though the bind shows them.
    device_cgroup_rules:
      - 'c 188:* rmw'
      - 'c 166:* rmw'
```

- [ ] **Step 4: Give otbr a lock on its own stick.** Append `&uart-exclusive` to the `RADIO_URL` the `otbr` service passes, so that

```
spinel+hdlc+uart://${RADIO_DEVICE}?uart-baudrate=${RADIO_BAUDRATE}
```

becomes

```
spinel+hdlc+uart://${RADIO_DEVICE}?uart-baudrate=${RADIO_BAUDRATE}&uart-exclusive
```

with this comment above it:

```yaml
      # `&uart-exclusive` makes OpenThread take flock + TIOCEXCL on the
      # stick (research A.3; the option landed upstream on 2026-05-04). A
      # second layer only: TIOCEXCL is bypassed by a holder of
      # CAP_SYS_ADMIN, which this privileged container has, and the real
      # guarantee is that loxmatter never offers or accepts the Thread stick
      # as a Zigbee coordinator (design section 3.2).
      #
      # UNVERIFIED against the otbr image installed on the test Pi (design
      # open point 5): confirm on the Pi that the image honours or ignores
      # the parameter rather than refusing to start, BEFORE relying on it.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q tests/test_compose_profiles.py`
Expected: PASS, all tests in the file including the three new ones.

- [ ] **Step 6: Prove each protection catches its fault.** Three faults, from the docstrings above: delete `c 166:* rmw`; add the rules to `matter-server`; drop `&uart-exclusive`. FAIL, revert, PASS, both outputs pasted.

- [ ] **Step 7: Run the checks** (all five, four-part pytest). Total: **2072 passed, 2 skipped** (the measured 2069 after Task 1, plus the three tests above).

- [ ] **Step 8: Commit**

```bash
git add deploy/testhost/docker-compose.yml tests/test_compose_profiles.py
git commit -m "$(cat <<'EOF'
feat(deploy): let the bridge open USB serial devices without naming one

Two device cgroup rules grant what the existing read-only /dev bind cannot:
the bind supplies names, the rule is the grant. A devices: entry was not an
option - it fails the whole stack when the node is absent and hides hotplug.
otbr additionally asks the kernel to keep its own stick to itself, as a
second layer behind loxmatter's own exclusion.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Profile Table Additions and the Shared Colour Fix

Five clusters that are **Matter** clusters too are simply missing from the profile table, and loxmatter cannot send the one colour command Zigbee lamps actually accept. Both are fixed here, before any Zigbee code exists, because both improve Matter devices on their own.

**Files:**
- Modify: `src/loxmatter/profiles/clusters.yaml`, `src/loxmatter/commands/color.py`, `src/loxmatter/commands/translate.py`
- Test: `tests/profiles/test_table.py`, `tests/commands/test_color.py`, `tests/commands/test_translate.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `loxmatter.commands.color.rgb_to_cie_xy(r: int, g: int, b: int) -> tuple[int, int]` — sRGB 0-255 to ZCL/Matter `CurrentX`/`CurrentY`, each `0..0xFEFF`.
  - `(768, 7)` becomes a served pair in `commands/translate.py`'s `_PAYLOAD_BUILDERS`, producing payload keys `colorX`, `colorY`, `transitionTime`, `optionsMask`, `optionsOverride`.
  - New `clusters.yaml` entries: `1030/0`, `69/0`, `1024/0`, `768/3`, `768/4`, `1280/2`, and command `768/7`.

- [ ] **Step 1: Write the failing colour test** in `tests/commands/test_color.py`:

```python
def test_rgb_to_cie_xy_against_the_srgb_primaries():
    """The published chromaticities of the sRGB primaries and of the D65
    white point (IEC 61966-2-1). Checked as the ZCL encoding, which is what
    goes on the wire: x = CurrentX / 65536, range 0x0000-0xFEFF.

    Fault to prove it: swap the returned x and y. Red then reports the
    chromaticity of a colour it is not, and the lamp shows it.
    """
    assert rgb_to_cie_xy(255, 0, 0) == (41943, 21627)  # x 0.6400, y 0.3300
    assert rgb_to_cie_xy(0, 255, 0) == (19661, 39322)  # x 0.3000, y 0.6000
    assert rgb_to_cie_xy(0, 0, 255) == (9830, 3932)  # x 0.1500, y 0.0600


def test_rgb_to_cie_xy_puts_white_on_d65():
    """White must land on the illuminant the sRGB standard defines, not
    somewhere near it - a white that drifts is the most visible error this
    conversion can make.

    Fault to prove it: skip the gamma expansion (use the raw 0-1 channel
    values). White still lands on D65, but every mixed colour moves - which
    is why the primaries above are tested too, and why this test alone is
    not enough."""
    x, y = rgb_to_cie_xy(255, 255, 255)
    assert abs(x / 65536 - 0.3127) < 0.001
    assert abs(y / 65536 - 0.3290) < 0.001


def test_rgb_to_cie_xy_never_exceeds_the_zcl_maximum():
    """CurrentX/CurrentY are capped at 0xFEFF by the ZCL, not at 0xFFFF. A
    value above it is out of range on the wire.

    Fault to prove it: return `round(x * 65536)` without the cap."""
    for colour in ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (0, 0, 0)):
        x, y = rgb_to_cie_xy(*colour)
        assert 0 <= x <= 0xFEFF
        assert 0 <= y <= 0xFEFF
```

Note for the implementer: compute the three expected primary pairs yourself from the published chromaticities (`round(0.64 * 65536)` and so on) and check they match what the implementation produces. If a value is off by one from rounding, fix the **expected** value to the correctly rounded one and say so in the report — do not loosen the assertion to a tolerance, which would stop the swap fault from failing.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest -q tests/commands/test_color.py -k cie_xy`
Expected: FAIL, `ImportError: cannot import name 'rgb_to_cie_xy'`.

- [ ] **Step 3: Implement the conversion** in `src/loxmatter/commands/color.py`, next to `rgb_to_hue_saturation`:

```python
# sRGB (IEC 61966-2-1) to CIE 1931 xy, D65. The matrix is the standard's
# own linear-RGB-to-XYZ matrix; the gamma expansion above it is the
# standard's EOTF, not the 2.2 approximation - the difference is visible in
# mixed colours, which is exactly what a lamp shows.
#
# This module's standing rule applies: WHOEVER TOUCHES THIS MEASURES AGAIN.
# The test checks the three primaries and the white point against published
# chromaticities, not against this code's own output.
_SRGB_TO_XYZ = (
    (0.4124564, 0.3575761, 0.1804375),
    (0.2126729, 0.7151522, 0.0721750),
    (0.0193339, 0.1191920, 0.9503041),
)

# The ZCL caps CurrentX/CurrentY at 0xFEFF, not at 0xFFFF (Zigbee Cluster
# Library, Color Control). A value above it is out of range on the wire.
_CIE_MAX = 0xFEFF


def _expand_gamma(channel: int) -> float:
    value = channel / 255
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def rgb_to_cie_xy(r: int, g: int, b: int) -> tuple[int, int]:
    """RGB (0-255) to the ZCL/Matter CurrentX and CurrentY encoding.

    Both are `x * 65536` capped at 0xFEFF, which is how ColorControl carries
    a chromaticity: x = CurrentX / 65536.

    Black is the one input with no chromaticity at all (X+Y+Z = 0). It
    returns the D65 white point rather than raising or returning (0, 0):
    the caller only ever reaches this with a colour it is about to send, and
    "off" travels through LevelControl, never through here (see
    `to_device_calls`). A (0, 0) would be a corner of the gamut that no lamp
    can show and that no user asked for.
    """
    red, green, blue = _expand_gamma(r), _expand_gamma(g), _expand_gamma(b)
    x_value, y_value, z_value = (
        row[0] * red + row[1] * green + row[2] * blue for row in _SRGB_TO_XYZ
    )
    total = x_value + y_value + z_value
    if total <= 0:
        return (round(0.3127 * 65536), round(0.3290 * 65536))
    return (
        min(_CIE_MAX, round(x_value / total * 65536)),
        min(_CIE_MAX, round(y_value / total * 65536)),
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest -q tests/commands/test_color.py -k cie_xy`
Expected: PASS, 3 passed.

- [ ] **Step 5: Write the failing translate test** in `tests/commands/test_translate.py`:

```python
def test_move_to_color_is_served_and_carries_execute_if_off():
    """ZHA 2.2.2 sends colour ONLY as move_to_color (XY) and
    move_to_color_temp, never hue/saturation, and Matter's own Extended
    Color Light makes XY mandatory and hue/saturation optional (research
    C.4). Without (768, 7), colour on a Zigbee bulb does nothing.

    `ExecuteIfOff` for the reason `_OPTIONS_EXECUTE_IF_OFF` already gives:
    without it a colour command has no effect on a switched-off lamp.

    Fault to prove it: remove the (768, 7) entry from `_PAYLOAD_BUILDERS`.
    The call then raises `UnsupportedValueError` and the colour output is
    dead."""
    command = cmd(768, 7, takes_value=True)
    calls = to_device_calls(command, "100")  # packed Loxone full red

    colour = calls[0]
    assert (colour.cluster_id, colour.command_id) == (768, 7)
    assert colour.payload["colorX"] == 41943
    assert colour.payload["colorY"] == 21627
    assert colour.payload["optionsMask"] == 1
    assert colour.payload["optionsOverride"] == 1


def test_move_to_color_still_sends_brightness_as_a_second_call():
    """The same two-call rule `(768, 6)` already follows: a packed Loxone
    number carries colour AND brightness, Matter carries them in different
    clusters, and the colour call must come first so the lamp does not
    visibly power on in the old colour.

    Fault to prove it: return only the colour call."""
    command = cmd(768, 7, takes_value=True)
    calls = to_device_calls(command, "100")

    assert len(calls) == 2
    assert (calls[1].cluster_id, calls[1].command_id) == (8, 4)
```

Note for the implementer: the helper this module already has is `cmd(cluster, command, takes_value=False)`, defined at the top of `tests/commands/test_translate.py` — use it, do not add a second builder.

**On the packed value `100`.** An earlier draft of this plan wrote `16711680`, the 24-bit hex RGB for red. That is wrong for Loxone and was corrected against `commands/color.py` before this plan was executed. Loxone does not pack bytes, it concatenates **whole percentages**: `AQa = red% + green% * 1000 + blue% * 1_000_000`, and `loxone_rgb_to_rgb` raises `LoxoneColourError(kind="channel_out_of_range")` for any channel above 100. `16711680 % 1000 = 680`, i.e. "red at 680 %", so the old value would have raised rather than producing a colour, and the test would have failed for a reason that had nothing to do with the builder under test. Full red is **`100`**; green is `100000` and blue `100000000`, as `color.py`'s own hardware-measured table records. The `rgb_to_cie_xy` expectations in Step 1 were checked independently and are correct — leave them alone.

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest -q tests/commands/test_translate.py -k move_to_color`
Expected: FAIL, `UnsupportedValueError` naming cluster 768 command 7.

- [ ] **Step 7: Implement the builder** in `src/loxmatter/commands/translate.py`. Add the import of `rgb_to_cie_xy` to the existing `from loxmatter.commands.color import (...)` block, add the constant next to `_COMMAND_HUE_SATURATION`:

```python
_COMMAND_MOVE_TO_COLOR = 7
```

add the builder next to `_payload_hue_saturation`:

```python
def _payload_color_xy(value: str) -> _Built:
    """Packed Loxone colour number -> ColorControl MoveToColor (XY).

    Added for Zigbee (design 2026-09-12, section 5.6) and kept shared,
    because it improves Matter lamps at the same time: ZHA 2.2.2 sends
    colour only as XY, and Matter's Extended Color Light makes XY mandatory
    while hue/saturation is optional. A lamp that ignores (768, 6) accepts
    this one.

    Same shape as `_payload_hue_saturation` in every other respect,
    including the Lumitech branch: the SAME Loxone output carries colour,
    white and brightness, so which of the three a value means is decided by
    the value, not by the command it was exported as.
    """
    number = _as_number(value)

    if number == int(number) and is_lumitech(int(number)):
        try:
            kelvin = lumitech_to_kelvin(int(number))
        except ValueError as exc:
            raise UnsupportedValueError(
                i18n.t("api.errors.lumitech_malformed", value=value)
            ) from exc
        return _Built(
            {"colorTemperatureMireds": kelvin_to_mireds(kelvin), **_OPTIONS_EXECUTE_IF_OFF},
            command_id=_COMMAND_COLOR_TEMPERATURE,
            brightness_percent=lumitech_to_brightness(int(number)),
        )

    try:
        red, green, blue = loxone_rgb_to_rgb(number)
    except LoxoneColourError as exc:
        raise UnsupportedValueError(_translate_loxone_colour_error(exc)) from exc
    colour_x, colour_y = rgb_to_cie_xy(red, green, blue)
    return _Built(
        {
            "colorX": colour_x,
            "colorY": colour_y,
            "transitionTime": 0,
            **_OPTIONS_EXECUTE_IF_OFF,
        },
        brightness_percent=rgb_to_brightness(red, green, blue),
    )
```

and the table entry in `_PAYLOAD_BUILDERS`:

```python
    (_CLUSTER_COLOR, _COMMAND_MOVE_TO_COLOR): _payload_color_xy,
```

Also update this module's docstring: it currently states that MoveToColor (7, xy) is **not** supported. That sentence becomes false here. Replace it with a sentence saying (768, 7) is served, why it was added (Zigbee lamps accept XY and frequently not hue/saturation; Matter makes XY mandatory), and that MoveToHue (0), MoveToSaturation (3) and Enhanced (67) remain unsupported.

- [ ] **Step 8: Run to verify it passes**

Run: `uv run pytest -q tests/commands/test_translate.py`
Expected: FAIL at first, on the pre-existing tests that use `(768, 7)` as their example of an *unknown* command. Serving that pair is the whole point of this task, so those tests now assert the opposite of the truth. **There are three places to change, not one** — all three were verified to exist in the worktree on 12 September 2026, and missing any of them leaves either a failing suite or a docstring that lies:

| Where | What it says today | What it must become |
|---|---|---|
| `tests/commands/test_translate.py::test_known_cluster_with_unknown_command_raises` | expects `to_device_calls(cmd(768, 7, takes_value=True), "255,0,0")` to raise | use `(768, 0)` MoveToHue, which stays genuinely unserved |
| `tests/commands/test_translate.py::test_known_cluster_with_unknown_command_raises_in_german` | the German counterpart, same `(768, 7)` example | the same change to `(768, 0)` |
| `src/loxmatter/commands/translate.py` module docstring | cross-references the first test by name, "(cluster 768/ColorControl, command 7)" | name the new example, "(cluster 768/ColorControl, command 0/MoveToHue)" |

Neither test may be deleted: the rule they protect — that the dispatch keys on the **pair**, never on the cluster alone — is unchanged and still worth a test. Only their example moves, because this task made the old example valid. Record all three edits in the task report with this reason. Note that the same docstring's closing sentence lists "MoveToColor (7, xy)" among the unsupported commands; Step 7 already requires that sentence to be rewritten, and the two edits are to the same paragraph — make them together rather than in two passes.

After the three edits: PASS.

- [ ] **Step 9: Add the profile table entries** to `src/loxmatter/profiles/clusters.yaml`. These go here rather than into Zigbee code because all five are Matter clusters that are simply missing (design 5.5, research C.5), and they are safe for existing installations: keys are assigned only when a signal row is created, so an existing `c69_a0` keeps its key.

```yaml
  1030:
    name: occupancy
    rank: 10
    attributes:
      # Matter OccupancySensing and Zigbee 0x0406 are the same cluster ID,
      # the same attribute and the same bit 0 - nothing is converted. A
      # bitmap, not a quantity: no unit.
      0: {slug: occupancy, unit: ""}
  69:
    name: booleanstate
    rank: 10
    attributes:
      # Matter BooleanState StateValue. Contact and water-leak sensors both
      # land here; the POLARITY differs between them and is decided at the
      # Zigbee edge from the IAS zone type (design 5.2), never in this table.
      0: {slug: state, unit: ""}
  1024:
    name: illuminance
    rank: 10
    attributes:
      # Deliberately left raw. Both Matter and the ZCL use
      # 10000*log10(lux)+1, so the two agree exactly - and turning that back
      # into lux is a power, not a factor, which `scale` cannot express (the
      # same reason the colour temperature below stays in mired).
      0: {slug: illuminance, unit: ""}
  1280:
    name: iaszone
    rank: 40
    attributes:
      # The raw IAS zone-status bitmap, kept visible as an expert signal.
      #
      # `functional: false` ON PURPOSE: the Zigbee edge derives a
      # BooleanState (69/0) or an occupancy (1030/0) from this same bitmap,
      # and exporting both would put the same physical fact into Loxone
      # twice under two names. Whoever wants the raw bits - tamper, battery,
      # supervision - enables this one by hand in the expert block.
      #
      # NOTE the trap documented at cluster 40 below: an `attributes:`
      # section makes `relevance.is_functional` filter EVERY attribute of
      # this cluster through `names_element`. That is wanted here.
      2: {slug: ias_zone_status, unit: "", functional: false}
```

and, inside the **existing** `768:` block, two attributes and one command:

```yaml
      # CurrentX (3) and CurrentY (4): the chromaticity as the ZCL and
      # Matter both carry it, x = value / 65536. Left raw - a division by
      # 65536 would be expressible as `scale`, but the pair only means
      # anything together, and two independent 0-1 numbers in Loxone are
      # less useful than the two integers the standard names.
      3: {slug: color_x, unit: ""}
      4: {slug: color_y, unit: ""}
```

```yaml
      # MoveToColor (7, XY) - served since 2026-09-12, see
      # commands/translate.py `_payload_color_xy`. Without this entry the
      # command would appear as a generic, digital `c768_cmd7` in the raw
      # export and a button press would send the literal "1" into the XY
      # builder - the same trap that `10:` above documents.
      7: {slug: color_xy, takes_value: true, control: hue_sat}
```

- [ ] **Step 10: Write the table tests** in `tests/profiles/test_table.py`:

```python
def test_the_five_added_clusters_name_their_elements():
    """Design 2026-09-12 section 5.5. These are Matter clusters that were
    missing from the table, not Zigbee-only ones - a Matter occupancy sensor
    got `c1030_a0` before this.

    Fault to prove it: remove the `1030` entry. The slug falls back to the
    generic name and the signal loses its title."""
    assert lookup(SignalRef(1, 1030, 0, SignalKind.ATTRIBUTE), True).slug == "occupancy"
    assert lookup(SignalRef(1, 69, 0, SignalKind.ATTRIBUTE), True).slug == "state"
    assert lookup(SignalRef(1, 1024, 0, SignalKind.ATTRIBUTE), 5000).slug == "illuminance"
    assert lookup(SignalRef(1, 768, 3, SignalKind.ATTRIBUTE), 41943).slug == "color_x"
    assert lookup(SignalRef(1, 768, 4, SignalKind.ATTRIBUTE), 21627).slug == "color_y"


def test_the_raw_ias_bitmap_is_expert_only():
    """The edge derives 69/0 or 1030/0 from this same bitmap, so exporting
    it as well would send one physical fact to Loxone twice.

    Fault to prove it: drop `functional: false` from the 1280 entry."""
    assert marked_non_functional(SignalRef(1, 1280, 2, SignalKind.ATTRIBUTE)) is True


def test_move_to_color_is_an_analog_output_not_a_digital_one():
    """`takes_value: true` is what makes this an analog output. Without the
    table entry the raw export would offer a digital `c768_cmd7` and a
    button press would send the literal "1" into the XY builder.

    Fault to prove it: set `takes_value: false`."""
    assert command_slug(768, 7) == "color_xy"
    assert command_takes_value(768, 7) is True
```

Note for the implementer: match the real signatures of `lookup`, `marked_non_functional`, `command_slug` and `command_takes_value` as this test module already calls them — read a neighbouring test rather than trusting the argument shapes sketched above.

- [ ] **Step 11: Run to verify they pass**

Run: `uv run pytest -q tests/profiles/`
Expected: PASS. If an existing test asserted that a colour XY attribute falls back to a generic slug, it will now fail — that is the intended change; update it and record it in the report.

- [ ] **Step 12: Prove each protection catches its fault.** Seven faults, from the docstrings above. FAIL, revert, PASS, both outputs pasted.

- [ ] **Step 13: Run the checks** (all five, four-part pytest). Report the new total and confirm it equals the previous total plus the tests added here.

- [ ] **Step 14: Commit**

```bash
git add src/loxmatter/profiles/clusters.yaml src/loxmatter/commands/color.py src/loxmatter/commands/translate.py tests/profiles tests/commands
git commit -m "$(cat <<'EOF'
feat(commands): send colour as XY, and name five missing Matter clusters

ZHA sends colour only as MoveToColor and Matter makes XY mandatory while
hue/saturation is optional, so a lamp that ignores (768, 6) accepted nothing
at all from this bridge. The builder is shared, so Matter lamps gain it too.
Occupancy, BooleanState, illuminance and colour XY were Matter clusters
missing from the profile table; the raw IAS bitmap joins them as an expert
signal so the derived value is not exported twice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: The Fingerprint Table

Which stick is which, **without opening the port**. Probing is measured to cost ~34 s on a stick that answers nothing, and it has side effects: ZNP toggles DTR/RTS and blasts 256 bootloader-skip bytes, which resets the chip on every CP2102N/CH9102 dongle, and merely `open()`ing a tty on Linux asserts DTR/RTS. A stick that resets itself because the settings card was opened is not acceptable.

**Files:**
- Create: `src/loxmatter/radios/fingerprints.py`, `tests/radios/test_fingerprints.py`

**Interfaces:**
- Consumes: `loxmatter.radios.inventory.SerialRadio` (Task 0 of 2a-1, already in `main`).
- Produces:
  - `loxmatter.radios.fingerprints.RadioType = Literal["ezsp", "znp", "deconz"]`
  - `loxmatter.radios.fingerprints.FlowControl = Literal["hardware", "software"]`
  - `Fingerprint(name: str, radio_type: RadioType, baudrate: int, flow_control: FlowControl)` (frozen dataclass)
  - `match_fingerprint(radio: SerialRadio) -> Fingerprint | None` — `None` means "unknown stick", never a guess
  - `DEFAULT_UNKNOWN = Fingerprint(name="", radio_type="ezsp", baudrate=115200, flow_control="software")` — what the Advanced disclosure pre-fills for an unrecognised stick (Task 13)

- [ ] **Step 1: Write the failing tests** `tests/radios/test_fingerprints.py` (GPL header first):

```python
"""Which stick is which, from the USB inventory alone (design 2026-09-12
section 7, research A.1/A.4).

Ported from Zigbee2MQTT's table, which is the only maintained one of its
kind. Every row below is a real product.

The two rows that matter most are the ones MEASURED on the maintainer's Pi
on 12 September 2026, because they are the whole argument for fingerprinting
by name. Both of his sticks report `10c4:ea60` - a bare Silicon Labs CP210x
UART bridge - and both sit at major 188. `lsusb` cannot tell them apart,
`/dev/ttyUSB*` cannot tell them apart, and only the by-id string can:

    usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf...-if00-port0  (ttyUSB1)
    usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a...-if00-port0              (ttyUSB0)

The first is his ZIGBEE coordinator. The second is his THREAD stick, and
`RADIO_DEVICE=/dev/ttyUSB0` in the live stack's `.env` confirms it. Note
what that means for this module: the MG24 row below is CORRECT and stays -
an MG24 genuinely is an EZSP coordinator, and is one for other people - so
this table will happily fingerprint the maintainer's Thread stick as a
usable Zigbee radio. That is not this module's bug to fix. Nothing here
knows what an installation is currently USING a stick for; keeping the
Thread stick out of the picker is Task 11's job, and it is done by resolved
major:minor against the configured Thread device, not by model name."""

from __future__ import annotations

import pytest

from loxmatter.radios.fingerprints import ambiguous_vid_pids, match_fingerprint, table
from loxmatter.radios.inventory import SerialRadio


def _stick(by_id: str, vid_pid: str | None, manufacturer: str | None = None) -> SerialRadio:
    return SerialRadio(
        path=f"/dev/serial/by-id/{by_id}",
        tty="ttyUSB0",
        manufacturer=manufacturer,
        product=None,
        serial=None,
        vid_pid=vid_pid,
    )


# MEASURED on the maintainer's Raspberry Pi, 12 September 2026, verbatim
# from `ls -l /dev/serial/by-id/`, which returns exactly these two entries.
# They are the ONLY by-id strings in this suite known to exist; every other
# row is shaped like a real one but was written for the plan.
REAL_ITEAD = (
    "usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf16ad5953ef11844a28e0174bec31-if00-port0"
)
REAL_MG24 = "usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a7d9118f9ef118f7767135c2a50c9-if00-port0"


@pytest.mark.parametrize(
    ("by_id", "vid_pid", "manufacturer", "radio_type", "baudrate", "flow_control"),
    [
        (
            "usb-Nabu_Casa_Home_Assistant_Connect_ZBT-1_1234-if00-port0",
            "10c4:ea60",
            "Nabu Casa",
            "ezsp",
            115200,
            "hardware",
        ),
        ("usb-Nabu_Casa_ZBT-2_abcd-if00", "303a:4001", "Nabu Casa", "ezsp", 460800, "hardware"),
        ("usb-Nabu_Casa_ZBT-2_abcd-if00", "303a:831a", "Nabu Casa", "ezsp", 460800, "hardware"),
        # MEASURED, 12 September 2026: the maintainer's Zigbee coordinator,
        # verbatim from `ls -l /dev/serial/by-id/`. `manufacturer` is passed
        # as None on both measured rows because the sysfs `manufacturer`
        # attribute was NOT captured - `lsusb` reported "Silicon Labs",
        # which is the bridge chip's descriptor, not the product's. The
        # ZBDongle-E V2 row carries no manufacturer constraint, so the value
        # cannot affect the result; do not add such a constraint on the
        # strength of an lsusb string that names the wrong vendor.
        (REAL_ITEAD, "10c4:ea60", None, "ezsp", 115200, "software"),
        # The CH9102 revision of the same product. Still invented.
        (
            "usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_9f1c2d-if00-port0",
            "1a86:55d4",
            None,
            "ezsp",
            115200,
            "software",
        ),
        # MEASURED, 12 September 2026: the maintainer's THREAD stick. It is
        # fingerprinted as a perfectly good EZSP coordinator, and that is
        # the RIGHT answer for this module - an MG24 is one. What must never
        # happen is offering it, and that is enforced in Task 11 against the
        # configured Thread device, not here. Do not "fix" this by deleting
        # the row: it would break the MG24 for every user who really does
        # run one as their Zigbee coordinator.
        (REAL_MG24, "10c4:ea60", None, "ezsp", 115200, "software"),
        (
            "usb-SONOFF_Zigbee_3.0_USB_Dongle_Max_MG24_77aabb-if00-port0",
            "10c4:ea60",
            "SONOFF",
            "ezsp",
            115200,
            "software",
        ),
        (
            "usb-ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_ab12cd34-if00-port0",
            "10c4:ea60",
            "ITEAD",
            "znp",
            115200,
            "software",
        ),
        (
            "usb-SMLIGHT_SLZB-06M_1122-if00-port0",
            "10c4:ea60",
            "SMLIGHT",
            "ezsp",
            115200,
            "software",
        ),
        (
            "usb-SMLIGHT_SLZB-06p7_3344-if00-port0",
            "10c4:ea60",
            "SMLIGHT",
            "znp",
            115200,
            "software",
        ),
        # The one genuine ordering hazard among the SMLIGHT rows: `SLZB-07`
        # (EZSP) is tried BEFORE `SLZB-...07p7` (ZNP), and only the trailing
        # `_` in `.*slzb-07(mg24)?_.*` keeps the EZSP row off this stick.
        # Getting it wrong opens a ZNP stick as EZSP.
        (
            "usb-SMLIGHT_SLZB-07p7_5566-if00-port0",
            "10c4:ea60",
            "SMLIGHT",
            "znp",
            115200,
            "software",
        ),
        (
            "usb-SMLIGHT_SLZB-07_4455-if00-port0",
            "10c4:ea60",
            "SMLIGHT",
            "ezsp",
            115200,
            "hardware",
        ),
        (
            "usb-dresden_elektronik_ConBee_II_DE123-if00",
            "1cf1:0030",
            "dresden elektronik",
            "deconz",
            115200,
            "software",
        ),
        (
            "usb-dresden_elektronik_ConBee_III_DE456-if00",
            "0403:6015",
            "dresden elektronik",
            "deconz",
            115200,
            "software",
        ),
    ],
)
def test_every_row_of_the_table_matches_its_own_stick(
    by_id, vid_pid, manufacturer, radio_type, baudrate, flow_control
):
    """Fault to prove it: change the SONOFF ZBDongle-P row's radio type to
    `ezsp`. A ZNP stick is then opened as an EmberZNet NCP and answers
    nothing, and the user is told their coordinator is broken."""
    found = match_fingerprint(_stick(by_id, vid_pid, manufacturer))
    assert found is not None, by_id
    assert (found.radio_type, found.baudrate, found.flow_control) == (
        radio_type,
        baudrate,
        flow_control,
    )


def test_the_shared_vid_pid_never_matches_on_its_own():
    """`10c4:ea60` is a plain Silicon Labs CP210x and is shared by at least
    six sticks in this table. Z2M refuses a VID:PID-only match for it and so
    does this.

    This is no longer an argument from the Z2M source: it is MEASURED. On
    the maintainer's Pi, `lsusb` reports BOTH of his sticks as
    `ID 10c4:ea60 Silicon Labs CP210x UART Bridge` - his Zigbee coordinator
    and his live Thread border router - and both sit at major 188. On the
    only hardware this project has, VID:PID is provably incapable of telling
    a Zigbee coordinator from a Thread stick, so nothing may ever be decided
    from it alone.

    Fault to prove it: allow the VID:PID-only match. The bare CP210x below
    is then reported as whichever `10c4:ea60` row happens to come first -
    and if that row says ZNP, opening it toggles DTR/RTS and RESETS the
    chip (research A.2)."""
    assert match_fingerprint(_stick("usb-Some_Other_CP210x_Bridge-if00", "10c4:ea60")) is None


def test_an_unknown_stick_is_unknown_and_not_a_guess():
    """Fault to prove it: return `DEFAULT_UNKNOWN` from `match_fingerprint`
    instead of `None`. The card then presents a guess as a detection, and
    the Advanced disclosure that exists to let the user say what the stick
    really is never appears."""
    assert match_fingerprint(_stick("usb-Totally_Unknown_Thing-if00", "dead:beef")) is None
    assert match_fingerprint(_stick("usb-No_Vid_Pid_At_All-if00", None)) is None


def test_no_ambiguous_row_may_rely_on_the_vid_pid_alone():
    """A structural guard on the TABLE, not on one lookup: any row whose
    VID:PID is in the conflict-prone set must carry a path pattern, or the
    rule above is only as good as whoever adds the next row remembers.

    Fault to prove it: add a row for `10c4:ea60` with `path_pattern=None`."""
    for entry in table():
        if entry.vid_pids & ambiguous_vid_pids():
            assert entry.path_pattern is not None, entry.name


def test_the_matcher_never_opens_the_port():
    """The whole point of fingerprinting (research A.2/A.4). `SerialRadio`
    carries no handle and this module imports nothing that could open one -
    asserted structurally, because a test cannot easily observe an open that
    does not happen.

    Fault to prove it: import `serial`/`bellows` in fingerprints.py."""
    import loxmatter.radios.fingerprints as module

    source = __import__("inspect").getsource(module)
    for forbidden in ("import serial", "import bellows", "import zigpy", "open("):
        assert forbidden not in source, forbidden


def test_the_table_matches_whatever_case_the_kernel_used():
    """MEASURED difference, 12 September 2026: the real stick spells itself
    `Itead_Sonoff`, while every string invented for this plan spelled it
    `ITEAD_SONOFF`. The matcher survives that only because
    `match_fingerprint` lowercases the path before applying the patterns and
    every pattern is written lowercase - which NO test pinned. A later
    "simplification" that dropped the `.lower()` would have kept every
    invented row green while silently losing the one coordinator the
    maintainer actually owns.

    Fault to prove it: remove `.lower()` from `radio.path.lower()` in
    `match_fingerprint`. The mixed-case spelling below stops matching."""
    for spelling in (
        REAL_ITEAD,
        REAL_ITEAD.upper(),
        REAL_ITEAD.lower(),
    ):
        found = match_fingerprint(_stick(spelling, "10c4:ea60"))
        assert found is not None, spelling
        assert found.radio_type == "ezsp", spelling


def test_the_two_sticks_on_the_maintainers_pi_are_told_apart_by_name_alone():
    """The measurement this whole module exists for (12 September 2026).

    Both sticks report `10c4:ea60` and both sit at major 188, so VID:PID,
    `lsusb` and the device number are each incapable of separating them. A
    matcher keyed on any of those would be a coin flip between his Zigbee
    coordinator and his live Thread border router. Only the by-id name
    works, which is why this table is keyed on it.

    Both come back as EZSP coordinators, and that is the CORRECT answer
    here: an MG24 genuinely is one, for anybody who runs it as one. Refusing
    to OFFER the MG24 is Task 11's job and is decided against the configured
    Thread device, never against this table.

    Fault to prove it: key the matcher on `vid_pid` alone. Both sticks then
    return the same row and the names compare equal."""
    itead = match_fingerprint(_stick(REAL_ITEAD, "10c4:ea60"))
    mg24 = match_fingerprint(_stick(REAL_MG24, "10c4:ea60"))
    assert itead is not None and mg24 is not None
    assert itead.name != mg24.name
    assert (itead.radio_type, mg24.radio_type) == ("ezsp", "ezsp")
```

**What is measured here and what is still invented.** `REAL_ITEAD` and `REAL_MG24` were read off the maintainer's Pi on 12 September 2026 and are verbatim; `ls -l /dev/serial/by-id/` returns exactly those two entries and nothing else. Every other by-id string in the table is **shaped** like a real one but was written for this plan. Before relying on an invented one, check it against the regexes in research A.1's table. If a regex does not match the string as written here, the **string** is what is wrong — fix it and say so in the report; do not loosen a regex to accept a string this plan invented.

**Three findings from the measurement, already applied above — verify each rather than trusting this paragraph.**

1. **Case is not a hazard, but nothing was pinning that.** The real stick spells itself `Itead_Sonoff`; every string this plan invented spelled it `ITEAD_SONOFF`. It matches anyway, because `match_fingerprint` lowercases `radio.path` and every pattern is written lowercase. The danger was never the data, it was that no test said so: with only upper-case rows, dropping the `.lower()` would have stayed green while losing the maintainer's coordinator. `test_the_table_matches_whatever_case_the_kernel_used` now pins it.

2. **The serial length and the `-if00-port0` suffix are harmless**, because every pattern is bracketed by `.*`. Two rows are the exception and are the reason the SMLIGHT rows now carry four test strings instead of two: `.*slzb-07(mg24)?_.*` and `.*slzb-0(6p7|6p10|7p7)_.*` both end in `_.*`, so what follows the model number is load-bearing. `SLZB-07` (EZSP) is tried **before** `SLZB-07p7` (ZNP), and only that trailing `_` keeps the EZSP row off the ZNP stick. Verified: `usb-SMLIGHT_SLZB-07p7_5566-if00-port0` matches only the ZNP row, `usb-SMLIGHT_SLZB-07_4455-if00-port0` only the EZSP one.

3. **The ZBDongle-P lookahead behaves as the earlier draft predicted.** That row's pattern is `.*sonoff.*plus(?!_v2_)(?!.*mg24).*`, and it is the only row whose correctness rests on a negative lookahead rather than on table order. Checked against the lowercased real MG24 path: the lookahead evaluates immediately after `plus`, where the remainder is `_mg24_e26a...`, so `(?!.*mg24)` fails; because the string contains only one `plus`, no backtracking rescues it. The MG24 string matches **only** the `SONOFF Zigbee Dongle Plus MG24` row, and the real ITEAD string matches **only** `SONOFF ZBDongle-E V2`. Getting this wrong is not cosmetic: ZNP is the one radio type whose `open()` toggles DTR/RTS and resets the chip. The lookahead is load-bearing for the genuine ZBDongle-P — if it ever stops behaving as described, fix the pattern and report it; do not delete it.

**What the measurement did NOT establish, and what must not be inferred from it.** The sysfs `manufacturer` attribute of either stick was not captured. `lsusb` reports both as `Silicon Labs`, which is the CP210x bridge chip's descriptor and not the product's, so it is not evidence about `SerialRadio.manufacturer`. Both measured rows therefore pass `manufacturer=None`, and neither row's `_Entry` carries a manufacturer constraint. **Do not add one** on the strength of an `lsusb` string that names the wrong vendor; if a manufacturer constraint is ever wanted for these rows, measure `/sys/.../manufacturer` on the Pi first.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/radios/test_fingerprints.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'loxmatter.radios.fingerprints'`.

- [ ] **Step 3: Write the implementation** `src/loxmatter/radios/fingerprints.py` (GPL header first):

```python
"""Which radio a USB stick carries, decided from the inventory alone.

**Fingerprint, never probe on our own initiative** (design 2026-09-12
section 7, research A.2/A.4). The reasons are measured, not cautious:

- Home Assistant's full auto-probe chain costs about 34 s on a stick that
  answers nothing, which is also what a wrong-type stick looks like.
- zigpy-znp toggles DTR/RTS and then blasts 256 bootloader-skip bytes. On
  every CP2102N/CH9102 dongle those pins are wired to RESET/BOOT, so
  probing RESETS THE CHIP.
- Merely `open()`ing a tty on Linux asserts DTR/RTS, so even a probe that
  writes nothing can reset such a stick.

A stick that resets itself because somebody opened the settings card is not
acceptable, so nothing here opens a port. The table is ported from
Zigbee2MQTT's `adapterDiscovery.ts`, which is the only maintained table of
its kind; the columns loxmatter already collects per stick
(`radios.inventory.SerialRadio`) are exactly the ones it needs.

**`10c4:ea60` must never match on VID:PID alone.** It is a plain Silicon
Labs CP210x bridge, shared by at least six of the sticks below, and Z2M
explicitly refuses a VID:PID-only match for it. Only the by-id string tells
them apart; a `10c4:ea60` with no telling by-id string is UNKNOWN, not a
guess. `_AMBIGUOUS_VID_PIDS` plus the structural test in
`tests/radios/test_fingerprints.py` keep that true for rows nobody has
written yet.

**This is measured, not inherited from Z2M.** On the maintainer's Pi
(12 September 2026) `lsusb` reports BOTH attached sticks as
`ID 10c4:ea60 Silicon Labs CP210x UART Bridge`, and both sit at major 188 -
yet one is his Zigbee coordinator and the other is the radio his live Thread
border router is running on. On the only hardware this project has, the
USB vendor/product id cannot distinguish a Zigbee coordinator from a Thread
stick, a serial console or a 3D printer. So: **never key anything on
`vid_pid` alone, and never "improve" this matcher by doing so.** The
vid_pid column narrows a candidate set; the by-id name decides. The
`usb-Some_Other_CP210x_Bridge-if00` negative case in the test file is that
rule's guard, and `test_the_two_sticks_on_the_maintainers_pi_are_told_apart_by_name_alone`
is its measured witness.

Flow control comes from this table too, not from a probe: bellows maps
`None` to XON/XOFF and anything else to RTS/CTS, and ASH escapes 0x11/0x13,
so software flow control is safe (research A.4 item 5). Z2M's own column
spells the software case "none"; it is spelled `"software"` here because
that is what bellows actually does with it, and a third state bellows has no
concept of would be an invention.

An explicit, user-initiated "Test this stick" probe is deliberately NOT
here - it is 2b, and when it lands it must never run against the Thread
stick and never at startup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from loxmatter.radios.inventory import SerialRadio

RadioType = Literal["ezsp", "znp", "deconz"]
FlowControl = Literal["hardware", "software"]


@dataclass(frozen=True)
class Fingerprint:
    """What a recognised stick is, and how to open it."""

    name: str
    radio_type: RadioType
    baudrate: int
    flow_control: FlowControl


@dataclass(frozen=True)
class _Entry:
    name: str
    vid_pids: frozenset[str]
    radio_type: RadioType
    baudrate: int
    flow_control: FlowControl
    # Matched case-insensitively against the by-id path. Mandatory for any
    # entry whose VID:PID is in `_AMBIGUOUS_VID_PIDS`.
    path_pattern: str | None = None
    manufacturer: str | None = None


# VID:PIDs that identify a generic USB-serial bridge rather than a product.
# An entry using one of these MUST carry a path pattern.
_AMBIGUOUS_VID_PIDS: Final[frozenset[str]] = frozenset({"10c4:ea60"})

# ORDER MATTERS, most specific first. Three of the SONOFF rows share
# `10c4:ea60` and overlapping names: `..._Plus_V2_...` (EZSP) and
# `..._Plus_MG24...` (EZSP) must both be tried before the plain
# `..._Plus_...` row (ZNP), which is why that last one also carries a
# negative lookahead. Getting this order wrong opens a ZNP stick as EZSP, or
# worse, an EZSP stick as ZNP - and a ZNP open resets the chip.
_TABLE: Final[tuple[_Entry, ...]] = (
    _Entry(
        name="Home Assistant Connect ZBT-1",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="hardware",
        path_pattern=r".*nabu_casa.*_zbt-1.*",
        manufacturer="Nabu Casa",
    ),
    _Entry(
        name="Home Assistant Connect ZBT-2",
        vid_pids=frozenset({"303a:4001", "303a:831a"}),
        radio_type="ezsp",
        baudrate=460800,
        flow_control="hardware",
        path_pattern=r".*nabu_casa_zbt-2.*",
        manufacturer="Nabu Casa",
    ),
    _Entry(
        name="SONOFF ZBDongle-E V2",
        vid_pids=frozenset({"1a86:55d4", "10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*sonoff.*plus_v2_.*",
    ),
    # MEASURED CAUTION: this row matches the maintainer's own stick, which
    # on his Pi is running THREAD, not Zigbee (`RADIO_DEVICE` points at it).
    # The row is still correct - an MG24 is a real EZSP coordinator and is
    # one for other users - and must NOT be deleted to protect him. This
    # module answers "what radio does this chip speak", never "is this stick
    # free to use". The second question is answered in `api/zigbee.py`
    # against the configured Thread device, by resolved major:minor.
    _Entry(
        name="SONOFF Zigbee Dongle Plus MG24",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*sonoff.*plus.*mg24.*",
    ),
    _Entry(
        name="SONOFF Zigbee Dongle Max MG24",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*sonoff.*max.*",
    ),
    _Entry(
        name="SONOFF Zigbee Dongle Lite MG21",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*lite.*mg21.*",
    ),
    _Entry(
        name="SLZB-06M",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*slzb-06m.*",
    ),
    _Entry(
        name="SLZB-07",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="hardware",
        path_pattern=r".*slzb-07(mg24)?_.*",
    ),
    _Entry(
        name="SLZB-06p7 / 06p10 / 07p7",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="znp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*slzb-0(6p7|6p10|7p7)_.*",
    ),
    # LAST of the SONOFF rows: the lookahead keeps it off the V2 stick,
    # and the MG24/Max/Lite rows above have already claimed theirs.
    _Entry(
        name="SONOFF ZBDongle-P",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="znp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*sonoff.*plus(?!_v2_)(?!.*mg24).*",
    ),
    _Entry(
        name="ConBee II",
        vid_pids=frozenset({"1cf1:0030"}),
        radio_type="deconz",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*conbee.*",
    ),
    _Entry(
        name="ConBee III",
        vid_pids=frozenset({"0403:6015"}),
        radio_type="deconz",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*conbee.*",
    ),
)

# What the Advanced disclosure pre-fills for a stick this table does not
# know (design section 7). EZSP at 115200 because that is what the great
# majority of current coordinators are - offered as an editable starting
# point the user can correct, never as a detection.
DEFAULT_UNKNOWN: Final = Fingerprint(
    name="", radio_type="ezsp", baudrate=115200, flow_control="software"
)


def table() -> tuple[_Entry, ...]:
    """The raw table, for the structural test that guards it."""
    return _TABLE


def ambiguous_vid_pids() -> frozenset[str]:
    return _AMBIGUOUS_VID_PIDS


def match_fingerprint(radio: SerialRadio) -> Fingerprint | None:
    """The stick's radio type, baud rate and flow control - or `None`.

    `None` means "loxmatter does not recognise this stick", which the UI
    says in plain words and follows with an Advanced disclosure. It never
    means "probably EZSP": a wrong guess here opens a port with the wrong
    driver, and for ZNP that resets the chip.
    """
    if radio.vid_pid is None:
        return None
    vid_pid = radio.vid_pid.lower()
    path = radio.path.lower()
    manufacturer = (radio.manufacturer or "").lower()
    for entry in _TABLE:
        if vid_pid not in entry.vid_pids:
            continue
        if entry.path_pattern is not None:
            if not re.fullmatch(entry.path_pattern, path):
                continue
        elif vid_pid in _AMBIGUOUS_VID_PIDS:
            # Unreachable while the structural test holds; kept as a
            # runtime guarantee rather than a comment, because the cost of
            # being wrong here is a reset coordinator.
            continue
        if entry.manufacturer is not None and entry.manufacturer.lower() not in manufacturer:
            continue
        return Fingerprint(
            name=entry.name,
            radio_type=entry.radio_type,
            baudrate=entry.baudrate,
            flow_control=entry.flow_control,
        )
    return None
```

Note for the implementer: `re.fullmatch` against a pattern that starts and ends with `.*` matches the whole by-id path, which is what the Z2M patterns assume. Verify each pattern against its test string and adjust the **pattern** only if research A.1 spells it differently; the negative lookaheads on the ZBDongle-P row are load-bearing and must stay.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q tests/radios/test_fingerprints.py`
Expected: PASS, 20 passed (14 parametrised rows plus 6).

- [ ] **Step 5: Prove each protection catches its fault.** Six faults, from the docstrings: change the ZBDongle-P row to `ezsp`; allow the VID:PID-only match; return `DEFAULT_UNKNOWN` instead of `None`; add a `10c4:ea60` row with `path_pattern=None`; remove the `.lower()` from `radio.path.lower()`; key the matcher on `vid_pid` alone. FAIL, revert, PASS, both pasted.

  The last two are the ones the hardware measurement added, and the fifth is the subtle one: it must fail on `REAL_ITEAD` (spelled `Itead_Sonoff`) while the upper-case invented rows stay green. If removing `.lower()` turns **every** row red, the table is being matched case-sensitively somewhere else too — report that rather than reverting quietly.

- [ ] **Step 6: Run the checks** (all five, four-part pytest).

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/radios/fingerprints.py tests/radios/test_fingerprints.py
git commit -m "$(cat <<'EOF'
feat(radios): identify Zigbee coordinators without opening the port

Probing costs about 34 s on a silent stick and resets the chip on every
CP2102N/CH9102 dongle, because ZNP toggles DTR/RTS - and merely opening a
tty asserts those pins. The table decides from the USB inventory instead.
10c4:ea60 is shared by six sticks and never matches on its own; an
unrecognised stick is reported as unknown rather than guessed at.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: One Error Vocabulary, and a Bound on Every Device Call

This closes boundary design open points 10, 11, 12 and 13 and spec §4.7, **before** any Zigbee code can rely on them. Nothing here imports zigpy: the vocabulary is defined at the boundary, and Task 7 translates zigpy's exceptions into it at the Zigbee edge.

**Files:**
- Modify: `src/loxmatter/sources/__init__.py`, `src/loxmatter/matter/models.py`, `src/loxmatter/model/store.py`, `src/loxmatter/api/devices.py`, `src/loxmatter/api/control.py`, `src/loxmatter/loxone/server.py`, `src/loxmatter/commands/fanout.py`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/sources/test_sources.py`, `tests/commands/test_fanout.py`, `tests/api/test_control.py`, `tests/api/test_devices.py`, `tests/model/test_store.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `loxmatter.sources.DeviceUnreachableError(RuntimeError)` — "a source asked a device and got nothing back". Every source raises this (or `MatterUnavailableError`, which shared code catches alongside); no shared `except` ever names a zigpy type.
  - `loxmatter.sources.SOURCE_CALL_TIMEOUT_SECONDS: float = 10.0`
  - `Sources.send` is bounded: it raises `DeviceUnreachableError` after that many seconds.
  - `loxmatter.sources.technology_display_name(technology: str) -> str`
  - `loxmatter.matter.models.technology_or_none(value: str) -> Technology | None`
  - `loxmatter.commands.fanout.GroupOutcome(failed: list[str], unreachable: list[str], unconfigured: list[str])`, returned by `dispatch_group` in place of the bare label list. `failed` is the plan-ordered list `dispatch_group` returns today, unchanged; the other two are order-preserving subsets of it.
  - `Sources.replace(technology: str, source: DeviceSource | None) -> None` — swaps or removes the source serving one technology in an already-built registry, for Task 11's in-process radio change. Consumed only there.

- [ ] **Step 1: Write the failing tests.** In `tests/sources/test_sources.py`:

```python
async def test_a_source_call_that_never_returns_is_abandoned():
    """zigpy waits 5 s per attempt for a mains device and 28 s for an end
    device or one with no node descriptor, and retries twice (research E.6,
    R1 section 8). Nothing on the command path bounded that before, so a
    Loxone virtual output aimed at a sleeping button held an HTTP request
    for over a minute.

    Fault to prove it: remove the `asyncio.wait_for` in `Sources.send`. The
    test then hangs until pytest's own timeout rather than failing, which is
    itself the report - note it as such if it happens."""
    started = asyncio.Event()

    class NeverAnswers:
        technology = "zigbee"
        connected = True

        async def send(self, call):
            started.set()
            await asyncio.sleep(3600)

    sources = Sources([NeverAnswers()])
    with pytest.raises(DeviceUnreachableError):
        await asyncio.wait_for(sources.send(DeviceCall("zigbee", "00:12", 1, 6, 1)), timeout=5)
    assert started.is_set()


async def test_the_bound_is_the_one_the_module_publishes():
    """Read from the module, not retyped: a test with its own copy of 10.0
    would keep passing after somebody changed the real bound."""
    assert SOURCE_CALL_TIMEOUT_SECONDS == 10.0
```

Note for the implementer: patch `SOURCE_CALL_TIMEOUT_SECONDS` down (for example with `monkeypatch.setattr`) so the first test does not really sit out ten seconds — the supervisor tests in this repository already take that stance ("a test that sleeps for eight seconds gets skipped as slow at the next rework and then checks nothing at all"). Make `Sources.send` read the module attribute at call time, not bind it as a default argument, so patching works.

In `tests/model/test_store.py`:

```python
def test_one_unreadable_technology_row_does_not_fail_the_whole_device_list():
    """Boundary design open point 10. `parse_technology` raises inside
    `_as_device`, so a single row written by a NEWER loxmatter - after an
    updater rollback, which restores the image but never the database -
    made `Store.devices()` fail for every device, and the bridge could not
    start rather than hiding the one device it could not place.

    Fault to prove it: call `parse_technology` in `devices()` again instead
    of `technology_or_none`."""
    store = ...  # the module's usual fixture
    store._db.execute("UPDATE device SET technology = 'zwave' WHERE id = ?", (other_id,))

    devices = store.devices()

    assert [d.id for d in devices] == [known_id]
```

- [ ] **Step 2: Run to verify they fail.** `uv run pytest -q tests/sources tests/model/test_store.py -k "abandoned or unreadable_technology"`. Expected: `ImportError` for `DeviceUnreachableError`, and the store test failing with `ValueError: unknown device technology 'zwave'`.

- [ ] **Step 3: Define the vocabulary** in `src/loxmatter/sources/__init__.py`.

**Add `import asyncio` to this module's imports first.** It is not there today — the file imports only `collections.abc`, `dataclasses`, `typing`, `loxmatter.i18n` and `loxmatter.matter.models` — and the bounded `Sources.send` below calls `asyncio.wait_for`. Add it in the existing `from __future__ import annotations` block's stdlib group, above `from collections.abc import ...`, so ruff's import ordering is satisfied without a reformat. Also extend `__all__` with `"DeviceUnreachableError"`, `"SOURCE_CALL_TIMEOUT_SECONDS"` and `"technology_display_name"`, which the module already maintains by hand.

```python
SOURCE_CALL_TIMEOUT_SECONDS = 10.0


class DeviceUnreachableError(RuntimeError):
    """A source asked a device and got nothing back.

    The one exception type shared code may catch for that outcome, across
    every technology (boundary design open point 11). Before it existed,
    `api/devices.py`'s removal route caught `MatterUnavailableError` alone,
    so a second source raising its own type on the very same failure would
    have surfaced as an unhandled 500.

    Each source raises this at ITS OWN EDGE. That is what keeps zigpy's
    exception names - `DeliveryError`, `ControllerError`, `ZigbeeException`,
    a non-SUCCESS ZCL status - inside `loxmatter/zigbee/`, where they
    belong: no `except` clause in shared code may name one.

    Distinct from `SourceNotConfiguredError` on purpose, and the difference
    is the difference between 502 and 503: this means the device was ASKED
    and stayed silent, that one means nothing was asked at all.
    """


def technology_display_name(technology: str) -> str:
    """A technology's name as a person should read it.

    Boundary design open point 13: `api.errors.source_not_configured`
    interpolated the raw, lowercase stored value ("zigbee is not set up in
    this installation"), while every other user-facing identifier in this
    codebase goes through a lookup first (see `api.categories.*`). An
    unknown value falls back to itself rather than raising - this runs
    inside an error path, and an error about an error helps nobody.

    The fallback is a `KeyError` catch and NOT a comparison of the result
    against the key. `i18n.t` does `entry = _STRINGS[key]` and RAISES
    `KeyError` on a missing key - it never hands the key back - so a
    `name == key` test would be dead code guarding nothing, and the very
    miss it was written for would propagate a `KeyError` out of an error
    path. That is exactly the "error about an error" the paragraph above
    rules out. The miss is reachable: `technology` is read from the
    `device` table, so a row written by a NEWER loxmatter and left behind
    by an updater rollback arrives here with a name that has no string -
    the same rollback case `technology_or_none` exists for in Step 4.
    """
    try:
        return i18n.t(f"api.technologies.{technology}")
    except KeyError:
        return technology
```

and change `SourceNotConfiguredError.__init__` to interpolate `technology_display_name(technology)` while keeping `self.technology` the raw value (callers compare against it). Then bound `Sources.send`:

```python
    async def send(self, call: DeviceCall) -> None:
        """The invoker. Bounded, because this is the one place that knows a
        human or a Miniserver is waiting.

        zigpy retries a request twice and waits 5 s per attempt for a mains
        device and 28 s for an end device or one without a node descriptor
        (research E.6), so an unbounded call here holds a Loxone virtual
        output open for over a minute. The bound is read from the module at
        call time so tests can shorten it.

        A timeout is reported as `DeviceUnreachableError`, not as
        `TimeoutError`: from the caller's point of view "asked, no answer"
        is exactly what happened, and it maps to the same 502 as every other
        way of not answering.
        """
        source = self.get(call.technology)
        try:
            await asyncio.wait_for(source.send(call), SOURCE_CALL_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise DeviceUnreachableError(
                i18n.t("api.errors.device_timed_out", seconds=SOURCE_CALL_TIMEOUT_SECONDS)
            ) from exc
```

Finally, let the registry be changed after it is built — Task 11 turns a Zigbee radio on and off while the bridge runs, and today `Sources` is frozen at construction:

```python
class Sources:  # the existing class - one method added, nothing else changes
    def replace(self, technology: str, source: DeviceSource | None) -> None:
        """Swaps or removes the source serving one technology.

        For Task 11's in-process radio change, which is the one thing in
        this project that gains or loses a source WITHOUT a restart: zigpy
        runs in-process, so configuring a stick has to add a source to a
        registry that `build_app` captured at startup, and clearing one has
        to remove it.

        `None` removes, and removing is the point rather than a tidy-up:
        after it, `get()` raises `SourceNotConfiguredError` again, which is
        how a command aimed at a Zigbee device that no longer has a radio
        becomes a 503 "not set up in this installation" instead of a 502
        "asked, no answer". The device was not asked; there is nothing to
        ask.

        Deliberately NOT a general-purpose registry mutator: `__init__`
        keeps rejecting two sources for one technology, and this method is
        the single, named exception to "the registry is built once".
        """
        if source is None:
            self._by_technology.pop(technology, None)
            return
        if source.technology != technology:
            raise ValueError(
                f"source for {source.technology!r} cannot serve technology {technology!r}"
            )
        self._by_technology[technology] = source
```

- [ ] **Step 4: Make the device list lenient.** In `src/loxmatter/matter/models.py`, add next to `parse_technology` (which keeps raising — it is right wherever a hard failure is right):

```python
def technology_or_none(value: str) -> Technology | None:
    """Like `parse_technology`, but answers `None` instead of raising.

    For the one caller that must survive a row it cannot place:
    `Store.devices()`. A value this code has never heard of means the
    database was written by a NEWER loxmatter - the updater rolls a failed
    update back to the old image and deliberately does NOT restore the
    database - and one such row used to make the whole device list raise,
    so the bridge could not start while `/health` still answered. Hiding
    the one device it cannot place is strictly better (boundary design open
    point 10).

    Single-device lookups keep using `parse_technology`: `device(id)` asked
    for one specific device, and silently returning something else - or
    nothing - would be worse there than a loud failure.
    """
    return cast(Technology, value) if value in _TECHNOLOGIES else None
```

In `Store.devices()`, skip and log rather than raise:

```python
    def devices(self) -> list[StoredDevice]:
        rows = self._db.execute("SELECT * FROM device WHERE active = 1 ORDER BY id").fetchall()
        devices: list[StoredDevice] = []
        for row in rows:
            if technology_or_none(str(row["technology"])) is None:
                # See `technology_or_none`: a row from a newer schema after
                # a rollback. Hidden, not fatal - and logged once per call
                # rather than silently, because a device disappearing from
                # the UI needs an explanation somewhere.
                logger.warning(
                    "device %s has technology %r, which this version does not know - hiding it",
                    row["id"],
                    row["technology"],
                )
                continue
            devices.append(self._as_device(row))
        return devices
```

- [ ] **Step 5: Teach the removal route the vocabulary.** In `src/loxmatter/api/devices.py`, change the removal route's `except MatterUnavailableError` to:

```python
        except (MatterUnavailableError, DeviceUnreachableError, TimeoutError) as exc:
            # One vocabulary across sources (boundary design open point 11).
            # `MatterUnavailableError` stays in the tuple rather than being
            # replaced: it is what the Matter client has always raised here
            # and every existing test asserts on it.
            raise HTTPException(status_code=502, detail=str(exc)) from exc
```

Apply the same widening to the two command paths that already have the seam — `api/control.py`'s device half and `loxone/server.py`'s `/cmd` route both end in a broad `except Exception` mapped to 502, so they already behave; **add `DeviceUnreachableError` to the `SourceNotConfiguredError` branch's sibling position only if a narrower catch exists there**. Read both before editing and change nothing that already maps correctly.

- [ ] **Step 6: Make the group fan-out tell the two apart.** In `src/loxmatter/commands/fanout.py`, replace `dispatch_group`'s bare list return with:

```python
@dataclass(frozen=True)
class GroupOutcome:
    """Which members failed, and why - the distinction the single-device
    path has had since the boundary design and the group path had not
    (boundary design open point 12).

    A member whose technology has no running source was never ASKED; a
    member that did not answer was. Reporting both as "no answer from X"
    told a user whose Zigbee stick they had just removed from the
    configuration that six lamps were unreachable, which sent them looking
    at the lamps.
    """

    # PLAN ORDER, every failed member, exactly the list `dispatch_group`
    # returned before this change - same content, same order, same
    # disambiguation. Stored rather than derived from the two lists below,
    # and that is the whole point of the field: `unreachable + unconfigured`
    # would silently regroup the members by KIND, so a group whose second
    # and third members failed for different reasons would be reported in an
    # order that depends on the failure, not on the group. This module's
    # docstring promises the opposite - "the returned list is in plan order,
    # so the message a caller builds from it is reproducible" - and both
    # `api/control.py` and `loxone/server.py` build their 502 detail from
    # it.
    failed: list[str]
    # Subsets of `failed`, each itself in plan order, for the callers that
    # need to tell a 502 from a 503.
    unreachable: list[str]
    unconfigured: list[str]
```

`dispatch_group` keeps building its plan-ordered, disambiguated label list exactly as it does today — that code is not to be rewritten — and then classifies each failed member by `isinstance(result, SourceNotConfiguredError)` into the two subsets, preserving order in both. The disambiguation rule (`"Lamp (12)"` when a label is shared by more than one failed member) is computed once over the whole failed set, so a label reads the same in `failed` as it does in whichever subset it lands in.

Both call sites (`api/control.py::_execute_group_command` and the group route in `loxone/server.py`) then map:

- only `unconfigured`, nothing unreachable → **503**, new key `api.errors.group_source_not_configured`
- anything unreachable → **502**, the existing `api.errors.group_partially_unreachable`, and when both kinds are present the detail names both sets

- [ ] **Step 7: Add the i18n keys** to `src/loxmatter/i18n/strings.yaml`, each with `en` **and** `de`:

| Key | Purpose |
|---|---|
| `api.technologies.matter` | display name, "Matter" both languages |
| `api.technologies.zigbee` | display name, "Zigbee" both languages |
| `api.errors.device_timed_out` | "the device did not answer within {seconds} s" |
| `api.errors.group_source_not_configured` | "{technology} is not set up in this installation, so {total} members were not reached" |

- [ ] **Step 8: Run the tests to verify they pass**, then run the four-part suite. **Expect existing tests to fail here** — `dispatch_group`'s return type changed, and every test that asserts on its bare list must be updated. That is intended and mechanical; each one that needs an *expectation* change rather than a shape change is a finding and goes in the report with its reason.

- [ ] **Step 9: Prove each protection catches its fault.** Faults: remove the `wait_for`; call `parse_technology` in `devices()`; drop `DeviceUnreachableError` from the removal route's tuple (a Zigbee removal failure then 500s); classify every group failure as unreachable (the 503 case becomes a 502). FAIL, revert, PASS, both pasted.

- [ ] **Step 10: Run the checks** (all five, four-part pytest).

- [ ] **Step 11: Commit**

```bash
git add src/loxmatter tests
git commit -m "$(cat <<'EOF'
feat(sources): one error vocabulary, and a bound on every device call

Nothing on the command path had a timeout, so an output aimed at a sleeping
device could hold an HTTP request for over a minute. Sources.send is now
bounded and reports a timeout as the same "asked, no answer" every other
source does, through one shared exception type that keeps zigpy's names
inside the Zigbee package. The group path can finally tell "not set up" from
"did not answer", and one unreadable technology row no longer fails the
entire device list.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Translating a Zigbee Device Into a Matter-Shaped Snapshot

The heart of the boundary, and **entirely pure**: dataclasses in, `NodeSnapshot` out, no zigpy import, no I/O. That is deliberate — there is no Zigbee stick on the test Pi, so this fake-based suite is not a convenience, it is the only evidence available before a second stick is bought, and it should be written as if it were the last line of defence.

**Files:**
- Create: `src/loxmatter/zigbee/translate.py`, `tests/zigbee/test_translate.py`

**Interfaces:**
- Consumes: `loxmatter.matter.models.NodeSnapshot`.
- Produces:
  - `EndpointFacts(endpoint: int, profile_id: int, device_type: int, in_cluster_ids: frozenset[int], attributes: Mapping[tuple[int, int], object])`
  - `DeviceFacts(ieee: str, manufacturer: str, model: str, is_mains_powered: bool, available: bool, quirk_applied: bool, endpoints: tuple[EndpointFacts, ...])`
  - `build_snapshot(facts: DeviceFacts) -> NodeSnapshot`
  - `matter_device_type(profile_id: int, device_type: int) -> int | None`
  - `ias_matter_device_type(zone_type: int) -> int | None`
  - `accepted_commands(endpoint: EndpointFacts) -> dict[int, list[int]]`
  - `rename_payload(cluster_id: int, command_id: int, payload: Mapping[str, object], allowed: frozenset[str]) -> dict[str, object]`
  - `BLOCKED_CLUSTER_IDS: frozenset[int]`

Task 7 calls `build_snapshot` and `rename_payload`; nothing else imports this module.

- [ ] **Step 1: Write the failing tests** `tests/zigbee/test_translate.py` (GPL header first). This is the largest test module in the plan; write it in full before any implementation.

```python
"""Turning what zigpy knows into what loxmatter's Matter-shaped middle
expects (design 2026-09-12, section 5).

Pure in, pure out: these tests build `DeviceFacts` by hand and never touch
zigpy, a radio or a database. Section 10.3 of the design is the reason they
are written this thoroughly - there is no Zigbee stick on the test Pi, so
nothing here has been exercised against real hardware, and this suite is the
only evidence that exists."""

from __future__ import annotations

import pytest

from loxmatter.matter.discovery import extract_signals
from loxmatter.matter.models import NodeSnapshot
from loxmatter.profiles.relevance import (
    ROOT_NODE_DEVICE_TYPE,
    device_types_by_endpoint,
    is_functional,
)
from loxmatter.zigbee.translate import (
    BLOCKED_CLUSTER_IDS,
    DeviceFacts,
    EndpointFacts,
    accepted_commands,
    build_snapshot,
    ias_matter_device_type,
    matter_device_type,
    rename_payload,
)

ZHA_PROFILE = 0x0104
ZLL_PROFILE = 0xC05E


def _lamp(**attributes) -> DeviceFacts:
    """A colour lamp on endpoint 1, the shape of the test hardware."""
    return DeviceFacts(
        ieee="00:12:4b:00:1c:a1:b2:c3",
        manufacturer="IKEA of Sweden",
        model="TRADFRI bulb",
        is_mains_powered=True,
        available=True,
        quirk_applied=False,
        endpoints=(
            EndpointFacts(
                endpoint=1,
                profile_id=ZLL_PROFILE,
                device_type=0x0210,
                in_cluster_ids=frozenset({0x0006, 0x0008, 0x0300}),
                attributes={(0x0006, 0x0000): True, (0x0008, 0x0000): 254, **attributes},
            ),
        ),
    )


# --------------------------------------------------------------- snapshot --


def test_the_snapshot_carries_the_three_identity_paths_and_a_root_endpoint():
    """`NodeSnapshot.from_raw` reads 0/40/1, 0/40/3 and 0/40/18 for Matter,
    and `is_functional`'s layer 2 plus `endpoint_labels` both key off a root
    node device type on endpoint 0. Endpoint 0 is free to use: in Zigbee it
    is the ZDO endpoint and carries no ZCL clusters.

    Fault to prove it: drop the `0/29/0` root-node entry. `is_functional`
    then stops treating endpoint 0 as management, and every unnamed
    attribute there becomes a wanted signal - the device reclassifies."""
    snapshot = build_snapshot(_lamp())

    assert snapshot.technology == "zigbee"
    assert snapshot.address == "00:12:4b:00:1c:a1:b2:c3"
    assert snapshot.unique_id == "00:12:4b:00:1c:a1:b2:c3"
    assert snapshot.attributes["0/40/1"] == "IKEA of Sweden"
    assert snapshot.attributes["0/40/3"] == "TRADFRI bulb"
    assert snapshot.attributes["0/40/18"] == "00:12:4b:00:1c:a1:b2:c3"
    assert ROOT_NODE_DEVICE_TYPE in device_types_by_endpoint(snapshot)[0]


def test_a_battery_device_declares_power_source_on_endpoint_zero():
    """So the battery level at 0/47/12 survives `is_functional`'s layer 2,
    which keeps a cluster on a management endpoint only when the endpoint
    also declares the matching functional device type.

    Fault to prove it: declare only the root node. The battery level then
    drops out as not functional and Loxone never sees it."""
    facts = DeviceFacts(
        ieee="00:12:4b:00:1c:a1:b2:c4",
        manufacturer="LUMI",
        model="lumi.sensor_magnet",
        is_mains_powered=False,
        available=True,
        quirk_applied=True,
        endpoints=(
            EndpointFacts(
                endpoint=1,
                profile_id=ZHA_PROFILE,
                device_type=0x0402,
                in_cluster_ids=frozenset({0x0500, 0x0001}),
                attributes={(0x0500, 0x0001): 0x0015, (0x0500, 0x0002): 0, (0x0001, 0x0021): 200},
            ),
        ),
    )

    snapshot = build_snapshot(facts)
    types = device_types_by_endpoint(snapshot)

    assert 0x0011 in types[0]  # PowerSource
    assert snapshot.attributes["0/47/12"] == 200
    ref = next(s for s in extract_signals(snapshot) if s.path == "0/47/12")
    assert is_functional(ref, types) is True


def test_a_path_whose_value_is_none_is_left_out_entirely():
    """THE most important rule in this module (design 5.5, research D.1
    step 5). `Store.register_signals` computes `exported` only when the row
    is CREATED, and a `None` value classifies as `Exportability.NONE` - so a
    signal first seen without a value stays unexported FOREVER, until the
    user toggles it by hand. That is the single most likely "loxmatter
    paired my sensor and Loxone gets nothing" report.

    Fault to prove it: emit the path with `None` instead of omitting it.
    See `test_a_late_value_creates_the_row` for the other half."""
    facts = _lamp(**{(0x0300, 0x0007): None})

    snapshot = build_snapshot(facts)

    assert "1/768/7" not in snapshot.attributes
    assert not any(value is None for value in snapshot.attributes.values())


@pytest.mark.parametrize(
    ("cluster", "attribute", "sentinel", "path"),
    [
        (0x0402, 0x0000, 0x8000, "1/1026/0"),  # temperature, invalid
        (0x0405, 0x0000, 0xFFFF, "1/1029/0"),  # humidity, invalid
        (0x0001, 0x0021, 0xFF, "0/47/12"),  # battery percentage, unknown
    ],
)
def test_invalid_value_sentinels_become_an_absent_path(cluster, attribute, sentinel, path):
    """These are not measurements, they are the ZCL's way of saying "no
    reading". Passing them through would send -327.68 degrees or 127.5 %
    into Loxone as if they were real.

    Fault to prove it: pass them through as numbers."""
    facts = DeviceFacts(
        ieee="00:12:4b:00:1c:a1:b2:c5",
        manufacturer="m",
        model="d",
        is_mains_powered=True,
        available=True,
        quirk_applied=False,
        endpoints=(
            EndpointFacts(
                endpoint=1,
                profile_id=ZHA_PROFILE,
                device_type=0x0302,
                in_cluster_ids=frozenset({cluster}),
                attributes={(cluster, attribute): sentinel},
            ),
        ),
    )

    assert path not in build_snapshot(facts).attributes


# ------------------------------------------------------------ device types --


def test_device_types_are_keyed_by_profile_and_type_together():
    """ZLL 0x0100 is a DIMMABLE light while ZHA and Matter 0x0100 is an
    ON/OFF light (R1 section 11). Keyed by device type alone, every IKEA and
    Hue lamp on the ZLL profile would be exported without a brightness.

    Fault to prove it: key the map by device type alone."""
    assert matter_device_type(ZLL_PROFILE, 0x0100) == 0x0101  # DimmableLight
    assert matter_device_type(ZHA_PROFILE, 0x0100) == 0x0100  # OnOffLight
    assert matter_device_type(ZLL_PROFILE, 0x0210) == 0x010D  # ExtendedColorLight
    assert matter_device_type(ZHA_PROFILE, 0x0107) == 0x0107  # OccupancySensor
    assert matter_device_type(0xDEAD, 0x0100) is None


@pytest.mark.parametrize(
    ("zone_type", "expected"),
    [(0x0015, 0x0015), (0x002A, 0x0043), (0x000D, 0x0107), (0x0028, 0x0076), (0x002B, 0x0076)],
)
def test_ias_devices_are_typed_by_their_zone_type(zone_type, expected):
    """An IAS device's ZCL device type says only "IAS Zone"; what KIND of
    sensor it is lives in `zone_type`.

    Fault to prove it: type them all as ContactSensor."""
    assert ias_matter_device_type(zone_type) == expected


def test_every_mapped_type_exists_in_the_matter_table():
    """The same guard `profiles/categories.py` has: every number this module
    produces must exist in `matter_server.client.models.device_types`, or
    `category_for` and `endpoint_labels` silently fall through to "other".

    Fault to prove it: map a zone type to 0x9999."""
    from matter_server.client.models import device_types as matter_types

    known = {
        getattr(cls, "device_type")
        for cls in vars(matter_types).values()
        if isinstance(cls, type) and hasattr(cls, "device_type")
    }
    produced = set(_all_mapped_matter_types())  # helper defined in the module under test
    assert produced <= known, produced - known


# -------------------------------------------------------------- IAS values --


def test_a_contact_sensor_inverts_the_alarm_because_matter_counts_closed():
    """Matter's BooleanState StateValue is TRUE when the contact is CLOSED;
    IAS alarm1 is TRUE when it is OPEN. The two are opposite, and a bridge
    that forwards the bit unchanged reports every door as exactly wrong.

    This polarity is taken from the Matter device library and the
    BooleanState data model, NOT measured here - design section 10.2 records
    how to settle it empirically on the test Pi with MYGGBETT, which is
    already commissioned. Until that is done this test pins the documented
    reading, and the report says so.

    Fault to prove it: drop the inversion."""
    assert _state_of(zone_type=0x0015, zone_status=0b00) is True  # closed
    assert _state_of(zone_type=0x0015, zone_status=0b01) is False  # open


def test_a_water_leak_sensor_does_not_invert():
    """TRUE means leak, on both sides.

    Fault to prove it: invert it like the contact sensor."""
    assert _state_of(zone_type=0x002A, zone_status=0b00) is False
    assert _state_of(zone_type=0x002A, zone_status=0b01) is True


def test_the_alarm_is_read_as_both_bits_not_just_the_first():
    """ZHA reads `value & 0b11`: Alarm_1 is bit 0 and Alarm_2 is bit 1, and
    some sensors only ever set Alarm_2.

    Fault to prove it: read bit 0 alone. An Alarm_2-only sensor then never
    reports anything at all, having paired and configured perfectly."""
    facts = _ias(zone_type=0x000D, zone_status=0b10)  # motion, Alarm_2 only
    assert build_snapshot(facts).attributes["1/1030/0"] == 1


def test_tamper_and_battery_bits_are_not_read_as_an_alarm():
    """Tamper is bit 2 and battery bit 3; neither is the sensor firing.

    Fault to prove it: read `value` truthily instead of `value & 0b11`."""
    facts = _ias(zone_type=0x000D, zone_status=0b1100)
    assert build_snapshot(facts).attributes["1/1030/0"] == 0


def test_an_unmapped_ias_zone_type_passes_the_raw_bitmap_through():
    """Fire, CO and vibration have no Matter cluster loxmatter understands,
    so the raw bitmap stays visible at 1280/2 as an expert signal rather
    than being dropped.

    Fault to prove it: drop unmapped zone types."""
    snapshot = build_snapshot(_ias(zone_type=0x002D, zone_status=0b01))
    assert snapshot.attributes["1/1280/2"] == 0b01
    assert "1/69/0" not in snapshot.attributes


# -------------------------------------------------- accepted command lists --


def test_the_accepted_command_list_is_synthesised_from_clusters_and_capabilities():
    """Zigbee has no reliable equivalent - ZCL "Discover Commands Received"
    is optional and widely unimplemented - so the list is synthesised, and
    only with what `commands/translate.py` can actually build.

    Fault to prove it: always add 6 (hue/saturation). A lamp without the
    capability then gets a colour output that does nothing."""
    endpoint = EndpointFacts(
        endpoint=1,
        profile_id=ZLL_PROFILE,
        device_type=0x0210,
        in_cluster_ids=frozenset({0x0006, 0x0008, 0x0300}),
        attributes={(0x0300, 0x400A): 0x08},  # XY only
    )
    commands = accepted_commands(endpoint)

    assert commands[6] == [0, 1, 2]
    assert commands[8] == [0, 4]
    assert commands[768] == [7]


def test_the_colour_capability_bits_each_unlock_their_own_command():
    """0x01 hue/saturation -> 6, 0x08 XY -> 7, 0x10 colour temperature -> 10.

    Fault to prove it: treat any non-zero capability as all three."""

    def colour(capabilities):
        return accepted_commands(
            EndpointFacts(
                1, ZLL_PROFILE, 0x0210, frozenset({0x0300}), {(0x0300, 0x400A): capabilities}
            )
        )[768]

    assert colour(0x01) == [6]
    assert colour(0x08) == [7]
    assert colour(0x10) == [10]
    assert colour(0x19) == [6, 7, 10]


def test_a_readable_colour_temperature_unlocks_it_without_the_capability_bit():
    """Cheap lamps report no capabilities at all but do answer
    `color_temperature`.

    Fault to prove it: require the bit."""
    endpoint = EndpointFacts(1, ZLL_PROFILE, 0x0220, frozenset({0x0300}), {(0x0300, 0x0007): 370})
    assert accepted_commands(endpoint)[768] == [10]


def test_dangerous_clusters_never_become_outputs():
    """`ADMINISTRATIVE_CLUSTERS` in profiles/table.py is Matter-numbered and
    does not protect a Zigbee device. Basic (0x0000) carries
    `reset_to_factory_defaults`, and an output wired to it in Loxone would
    unpair the device on a button press.

    Fault to prove it: remove 0x0000 from the block list."""
    assert {0x0000, 0x0003, 0x0019, 0x1000} <= BLOCKED_CLUSTER_IDS
    endpoint = EndpointFacts(
        1, ZHA_PROFILE, 0x0100, frozenset({0x0006, 0x0000, 0x0019, 0x1000}), {}
    )
    assert set(accepted_commands(endpoint)) == {6}


# ------------------------------------------------------- argument renaming --


def test_command_arguments_are_renamed_from_a_table_not_by_a_rule():
    """Two of these do not follow the mechanical camelCase -> snake_case
    rule, and `colorTemperatureMireds` is the one that matters:
    zigpy calls it `color_temp_mireds`, not `color_temperature_mireds`.

    Fault to prove it: use a mechanical camel-to-snake helper. The colour
    temperature command then fails to build on every lamp."""
    renamed = rename_payload(
        768,
        10,
        {
            "colorTemperatureMireds": 370,
            "transitionTime": 0,
            "optionsMask": 1,
            "optionsOverride": 1,
        },
        allowed=frozenset(
            {"color_temp_mireds", "transition_time", "options_mask", "options_override"}
        ),
    )
    assert renamed == {
        "color_temp_mireds": 370,
        "transition_time": 0,
        "options_mask": 1,
        "options_override": 1,
    }


def test_move_to_level_with_on_off_carries_no_options_fields():
    """8/4 has no options fields in zigpy at all, while 8/0 has them as
    optional. Passing an unknown field silently is how a command turns into
    a parsing error on the wire, so the payload is validated against what
    the command really accepts and the rest is dropped deliberately.

    Fault to prove it: pass the payload through unfiltered."""
    renamed = rename_payload(
        8,
        4,
        {"level": 128, "transitionTime": 0, "optionsMask": 1},
        allowed=frozenset({"level", "transition_time"}),
    )
    assert renamed == {"level": 128, "transition_time": 0}


def test_a_field_the_command_does_not_know_is_never_invented():
    """Fault to prove it: keep unknown keys under their Matter names."""
    renamed = rename_payload(6, 1, {"somethingElse": 1}, allowed=frozenset())
    assert renamed == {}
```

Note for the implementer: `_ias(...)`, `_state_of(...)` and `_all_mapped_matter_types()` are helpers this module must define — `_ias` builds a one-endpoint `DeviceFacts` with an IAS Zone cluster carrying `zone_type` at `(0x0500, 0x0001)` and `zone_status` at `(0x0500, 0x0002)`; `_state_of` builds one and reads `"1/69/0"` out of the snapshot. `_all_mapped_matter_types()` belongs in `translate.py` itself (it is the module's own inventory of what it can produce), and the Matter-table guard must mirror however `tests/profiles/test_categories.py::test_every_mapped_type_exists_in_the_matter_table` really reads that table — read it first and copy its access, rather than the sketch above.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest -q tests/zigbee/test_translate.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'loxmatter.zigbee.translate'`.

- [ ] **Step 3: Write the implementation** `src/loxmatter/zigbee/translate.py` (GPL header first). Structure it in this order, with a module docstring stating the bet the boundary design made and where it does and does not hold:

```python
"""Zigbee facts in, a Matter-shaped `NodeSnapshot` out.

**Pure.** No zigpy import, no I/O, no clock. `source.py` reads zigpy and
fills `DeviceFacts`; everything decided here is decided from those
dataclasses alone. That is not tidiness: there is no Zigbee stick on the
test Pi (design section 10.3), so the fake-based tests against this module
are the only evidence this translation works, and they are only worth
anything if the thing they test has no hidden inputs.

The boundary design bet that Matter's data model is close enough to the
Zigbee Cluster Library that a Zigbee source can deliver the same
`NodeSnapshot` and everything downstream stays unchanged. For on/off, level,
hue, saturation, colour temperature, temperature and humidity that bet pays
in full: the attribute IDs, the scaling and the units are LITERALLY
IDENTICAL and nothing is converted. What it does not cover is what Zigbee
has and Matter does not, and that is what this module is:

- IAS Zone, which is one bitmap standing in for four different sensors,
  with a polarity that is inverted for one of them;
- ZLL device types, where 0x0100 means something else than it does in
  Matter;
- an `AcceptedCommandList`, which Zigbee has no reliable equivalent for;
- invalid-value sentinels, which are not measurements;
- a battery level that lives on the wrong endpoint.

**The one rule that outranks everything else here: never write a path whose
value is `None`.** See `build_snapshot`.
"""
```

Then, in order:

1. `EndpointFacts` and `DeviceFacts` frozen dataclasses, exactly as in the Interfaces block above.
2. `_MATTER_DEVICE_TYPE_BY_PROFILE: dict[tuple[int, int], int]` — the (profile, type) map, with every entry commented with the Zigbee name it translates. Cover at minimum, from the numbers verified in Task 6's tests: ZHA `0x0000→0x0103`, `0x0002→0x010A`, `0x0009→0x010A`, `0x0100→0x0100`, `0x0101→0x0101`, `0x0102→0x010D`, `0x0103→0x0103`, `0x0104→0x0104`, `0x0105→0x0105`, `0x0106→0x0106`, `0x0107→0x0107`, `0x0302→0x0302`; ZLL `0x0000→0x0100`, `0x0010→0x010A`, `0x0100→0x0101`, `0x0110→0x010B`, `0x0200→0x010D`, `0x0210→0x010D`, `0x0220→0x010C`. Carry a comment on the ZLL `0x0100` row naming the conflict, because that row is the whole reason the key is a pair.
3. `_MATTER_DEVICE_TYPE_BY_ZONE_TYPE: dict[int, int]` — `0x0015→0x0015`, `0x002A→0x0043`, `0x000D→0x0107`, `0x0028→0x0076`, `0x002B→0x0076`.
4. `_all_mapped_matter_types()` returning both maps' values, for the guard test.
5. `BLOCKED_CLUSTER_IDS = frozenset({0x0000, 0x0003, 0x0019, 0x1000})` with the Basic/`reset_to_factory_defaults` reason spelled out.
6. `_SENTINELS: dict[tuple[int, int], int]` — `(0x0402, 0x0000): 0x8000`, `(0x0405, 0x0000): 0xFFFF`, `(0x0001, 0x0021): 0xFF`, `(0x0001, 0x0020): 0xFF`.
7. `_ARGUMENT_NAMES: dict[str, str]` — the table of §5.7, with `colorTemperatureMireds → color_temp_mireds` carrying the comment that it is **not** `color_temperature_mireds` and that a mechanical helper breaks exactly here.
8. `accepted_commands`, `matter_device_type`, `ias_matter_device_type`, `rename_payload`.
9. `build_snapshot`.

`build_snapshot`'s docstring must carry the `None` rule in full:

```python
def build_snapshot(facts: DeviceFacts) -> NodeSnapshot:
    """The device as loxmatter's Matter-shaped middle expects it.

    **Never writes a path whose value is `None`.** `Store.register_signals`
    computes `exported` only when the row is CREATED, and a path whose value
    is `None` classifies as `Exportability.NONE` - so a signal first seen
    without a value stays unexported forever, until somebody toggles it by
    hand. A sensor that paired, configured and went green, whose value never
    reaches Loxone and whose row cannot heal itself, is the single most
    likely bug report this feature can produce.

    So a value that is missing, or is one of the ZCL's invalid-value
    sentinels, means the path is LEFT OUT. When the first real value
    arrives, `ZigbeeSource.follow` hands a fresh snapshot to
    `Runtime.on_node_snapshot`, which calls `register_signals`, invalidates
    the signal index and seeds the value - creating the row properly, with
    `exported` computed from a value that exists.
    """
```

Endpoint 0 is synthesised, never taken from the device: `0/29/0` gets `[{"0": 0x0016, "1": 1}]` plus `{"0": 0x0011, "1": 1}` when `not facts.is_mains_powered`; `0/40/1`, `0/40/3`, `0/40/18` carry manufacturer, model and IEEE; the battery percentage moves from `(0x0001, 0x0021)` on its real endpoint to `0/47/12`.

**`NodeSnapshot` takes three required string fields that this plan's test assertions never mention, and they have no defaults** — `vendor_name`, `product_name` and `unique_id` are declared before the first field with a default, so constructing the dataclass without them raises `TypeError` and every test in this module fails at once. `NodeSnapshot.from_raw` fills them for Matter from `0/40/1`, `0/40/3` and `0/40/18`; `build_snapshot` sets them from the same facts it writes into those three paths, so the snapshot is self-consistent whichever way a reader gets at the identity:

```python
    return NodeSnapshot(
        technology="zigbee",
        address=facts.ieee,
        vendor_name=facts.manufacturer,
        product_name=facts.model,
        unique_id=facts.ieee,
        attributes=attributes,
        available=facts.available,
    )
```

`available` is passed through rather than defaulted: `Runtime.seed_from_snapshot` and `attach()` read it to decide a device's initial `d<id>_online`, and defaulting it to `True` would report every device of a disconnected radio as reachable at startup — the exact failure Task 8 exists to prevent, reintroduced one layer earlier.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest -q tests/zigbee/test_translate.py`
Expected: PASS.

- [ ] **Step 5: Write the round-trip test** — the one that proves the bet, in the same module:

```python
def test_a_translated_lamp_decomposes_like_a_matter_lamp():
    """The boundary design's bet, checked end to end through the REAL
    downstream code: a snapshot this module builds must decompose through
    `extract_signals` and `extract_commands` exactly as a Matter snapshot
    does, with no Zigbee-shaped path surviving.

    This is what makes the rest of the suite meaningful - each test above
    checks one translation, this one checks that the result is genuinely the
    shape the middle expects rather than merely a dict.

    Fault to prove it: emit an attribute path as `<cluster>/<attribute>`
    without the endpoint. `parse_attribute_path` then rejects it, and
    `find_unparsable_paths` reports it."""
    from loxmatter.export.commands import extract_commands
    from loxmatter.matter.discovery import find_unparsable_paths

    snapshot = build_snapshot(_lamp(**{(0x0300, 0x400A): 0x19, (0x0300, 0x0007): 370}))

    assert find_unparsable_paths(snapshot) == []
    paths = {signal.path for signal in extract_signals(snapshot)}
    assert {"1/6/0", "1/8/0", "1/768/7"} <= paths
    slugs = {command.slug for command in extract_commands(snapshot)}
    assert {"on", "off", "toggle", "level_onoff", "colortemp", "color", "color_xy"} <= slugs
```

- [ ] **Step 6: Run to verify it passes.** If it fails because `extract_commands` drops a command, that is a real finding about the synthesis, not a test to loosen — fix the synthesis.

- [ ] **Step 7: Prove each protection catches its fault.** Fourteen faults, one per docstring above. FAIL, revert, PASS, both outputs pasted for each. This is the longest fault-injection round in the plan and it is the one that matters most; do not batch it or summarise it.

- [ ] **Step 8: Run the checks** (all five, four-part pytest).

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/zigbee/translate.py tests/zigbee/test_translate.py
git commit -m "$(cat <<'EOF'
feat(zigbee): translate a Zigbee device into a Matter-shaped snapshot

Pure translation, no zigpy and no I/O: device types keyed by profile and
type together because ZLL 0x0100 is not Matter's, IAS zone types decided per
sensor kind with the contact sensor's inverted polarity, alarms read as both
alarm bits, invalid-value sentinels turned into an absent path rather than a
fake measurement, and an AcceptedCommandList synthesised from what the
command translator can actually build.

No path is ever written with a None value: such a signal would be computed
as unexportable at creation and stay that way forever.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `ZigbeeSource` — Lifecycle, Events, Commands

The class that satisfies `DeviceSource` without an adapter. zigpy is imported **only here** and in Task 9; every zigpy exception is translated into the vocabulary Task 5 defined before it leaves this file.

**Files:**
- Create: `src/loxmatter/zigbee/source.py`, `tests/zigbee/test_source.py`, `tests/zigbee/fakes.py`
- Modify: `src/loxmatter/i18n/strings.yaml`

**Interfaces:**
- Consumes: `DeviceFacts`/`EndpointFacts`/`build_snapshot`/`rename_payload` (Task 6), `DeviceUnreachableError` (Task 5), `ensure_quirks_loaded` (Task 1), `Fingerprint` (Task 4).
- Produces:
  - `ZigbeeUnavailableError(RuntimeError)` — the radio could not be brought up. Carries an already-translated message.
  - `ZigbeeSource(*, path: str, fingerprint: Fingerprint, database: Path, application_factory: ApplicationFactory = _default_factory)` satisfying `DeviceSource`.
  - `ZigbeeSource.permit(seconds: int) -> datetime` and `ZigbeeSource.pairing_rows() -> list[PairingRow]` — used by Task 12, not part of `DeviceSource` (commissioning is deliberately outside the protocol).
  - `ConnectionProgress(state: ConnectionState, attempts: int, error: str | None, changed_at: str)` (frozen dataclass) and `ZigbeeSource.progress() -> ConnectionProgress`, where `ConnectionState = Literal["idle", "loading_quirks", "opening_radio", "connected", "failed"]`. Read by Task 11's `GET /api/zigbee/radio` so the card can show what a connection attempt is doing; `error` carries an already-translated message when `state == "failed"`.
  - `ZigbeeSource(..., on_connection_change: Callable[[bool], Awaitable[None]] | None = None)` — awaited with `True` after a successful `startup()` and with `False` on link loss and on `disconnect()`. Task 10 wires it to `Runtime.set_zigbee_connected`; Task 11's holder uses the same hook to keep the card honest.

- [ ] **Step 1: Write the fake** `tests/zigbee/fakes.py` (GPL header first). It stands in for `zigpy.application.ControllerApplication` so that **no test in this plan imports zigpy**:

```python
"""A stand-in for zigpy's ControllerApplication.

Every test of `ZigbeeSource` drives this instead of a radio. That is not
only because there is no Zigbee stick on the test Pi (design 10.3): zigpy's
own startup takes 7.5 s to time out against a silent port, and a suite that
paid that per test would be abandoned within a week.

It mimics exactly the behaviour the source depends on and nothing else:
listener registration, `startup` raising what research E.2 MEASURED, the
`connection_lost` callback, and a device catalogue that is readable while
the radio is gone."""
```

It must offer: `add_listener(listener)`, `startup(auto_form=...)` (configurable to raise), `shutdown(db=...)` recording that it was called, `devices` as a mapping of IEEE to a fake device with endpoints/clusters, `permit(time_s=..., node=...)` recording the duration, `remove(ieee)` firing `device_removed`, and a `fire_connection_lost(exc)` helper for the reconnect tests.

- [ ] **Step 2: Write the failing tests** `tests/zigbee/test_source.py` (GPL header first). The protections, each with the fault that proves it:

```python
async def test_connect_builds_a_fresh_application_every_time():
    """zigpy's teardown cancels tasks and calls `on_remove()` on every
    device, and an object whose `startup()` failed is in an unknown state.
    Home Assistant reloads the whole config entry on a lost connection for
    exactly this reason.

    Fault to prove it: reuse `self._app` when it already exists. The second
    connect then runs against an application that was already shut down."""


async def test_a_failed_startup_is_shut_down_and_not_kept():
    """Fault to prove it: leave `self._app` set after a failed startup. The
    next `connect()` then reuses a half-initialised object, and `snapshots()`
    in between reads from one whose radio thread is gone."""


@pytest.mark.parametrize(
    ("raised", "key"),
    [
        (FileNotFoundError(), "api.errors.zigbee_stick_missing"),
        (PermissionError(), "api.errors.zigbee_no_device_permission"),
        (
            OSError(16, "/dev/ttyUSB0 is already locked by another process"),
            "api.errors.zigbee_stick_busy",
        ),
        (TimeoutError(), "api.errors.zigbee_not_a_coordinator"),
    ],
)
async def test_each_startup_failure_gets_its_own_words(raised, key):
    """Measured in research E.2: a missing device raises `FileNotFoundError`
    IMMEDIATELY - not zigpy's `TransientConnectionError`, which only covers
    ENETUNREACH - and a silent port raises `TimeoutError` after 7.5 s. Each
    one sends the user somewhere different: to the radios card, to the
    compose stack, to their second loxmatter instance, or to the firmware on
    the stick. One generic "connection failed" sends them nowhere.

    Fault to prove it: map them all to one message."""


async def test_connected_is_an_explicit_flag_cleared_on_loss():
    """`bellows.is_controller_running` is NEVER cleared on a lost link
    (R1 section 2), so reading it would report a dead radio as healthy
    forever - which is precisely the 8 September outage the supervisor
    exists to prevent, one layer down.

    Fault to prove it: report the fake's own `is_running` instead."""


async def test_wait_for_link_loss_returns_at_once_when_already_disconnected():
    """The contract `BridgeMatterClient.wait_for_link_loss` has, and what
    puts the supervisor into its 1 s -> 60 s backoff loop for a source that
    never came up at all.

    Fault to prove it: await the event unconditionally. `supervise()` then
    blocks forever on a source that was never connected, and the radio is
    never retried."""


async def test_snapshots_and_subscribe_tolerate_being_disconnected():
    """`sources.supervisor.attach()` calls both unconditionally, and
    `cli._run` runs `attach` for every source at startup. A source that
    raised here would take the whole bridge down because a USB stick was
    unplugged - and Matter is the mandatory source, not this one.

    `snapshots()` still returns the device catalogue: `new(start_radio=False)`
    loads zigpy's database without touching the radio (research E.2), so the
    devices are known even when the stick is gone - with `available=False`,
    which is the truth.

    Fault to prove it: raise `ZigbeeUnavailableError` from `snapshots()`
    when not connected."""


async def test_cluster_events_are_queued_not_awaited_in_the_callback():
    """zigpy's cluster events are SYNCHRONOUS callbacks that do not catch
    exceptions (R1 section 6). A handler that raises inside one would tear
    down zigpy's event emission itself, so the callback does nothing but
    `put_nowait` and one dispatch task does the awaiting work - the same
    shape as `BridgeMatterClient._dispatch_loop`.

    Fault to prove it: call the handler directly from the callback and make
    the handler raise. Event delivery then stops for every device."""


async def test_one_failing_update_does_not_end_delivery():
    """Fault to prove it: remove the `except Exception` in the dispatch
    loop."""


async def test_send_renames_the_payload_and_drops_what_the_command_lacks():
    """Fault to prove it: pass the Matter payload through unchanged. zigpy
    then rejects every command with a schema error."""


@pytest.mark.parametrize("raised", [...])  # the zigpy exception set, faked
async def test_every_zigpy_failure_leaves_as_device_unreachable(raised):
    """`DeliveryError`, `ControllerError`, `ZigbeeException`, `TimeoutError`
    and a non-SUCCESS ZCL status all mean the same thing to a caller: asked,
    no answer. They are translated HERE, so no `except` clause in shared
    code ever names a zigpy type - and so `api/devices.py`'s removal route,
    which used to catch `MatterUnavailableError` alone, does not turn a
    Zigbee removal failure into an unhandled 500.

    Fault to prove it: let `DeliveryError` escape. The removal route then
    answers 500 instead of 502."""


async def test_removal_treats_device_removed_as_the_truth():
    """`remove()` deletes the device from zigpy's database whether or not
    the leave request is ever delivered (R1 section 4).

    Fault to prove it: wait for a leave confirmation that never comes."""


async def test_a_stick_carrying_another_network_stops_with_a_clear_message():
    """zigpy ADOPTS whatever is on a stick - it only forms when the network
    is not formed. With `validate_network_settings = True` a mismatch
    against the stored backup raises `NetworkSettingsInconsistent`, and the
    bridge stops there and does NOT overwrite: the other option is
    destructive and needs the write-once-EUI64 confirmation, which is 2b.

    Fault to prove it: pass `validate_network_settings=False`. The bridge
    then silently adopts or overwrites somebody's network."""


async def test_ota_is_off_and_the_database_is_next_to_the_store():
    """zigpy's OTA is ON by default with three internet providers and a
    broadcast every 3.9 h. A bridge that silently updates the user's lamps
    from the internet is not what this project promises.

    Fault to prove it: leave the OTA config at its default."""
```

Note for the implementer: write each of these out in full with a real body — the bodies are elided in this plan only to keep it readable, and a test whose body is a `pass` is a plan failure. Drive everything through `tests/zigbee/fakes.py`; nothing in this module may `import zigpy`. For the exception-set test, define the fake exception classes in `fakes.py` with the same **names** zigpy uses and have the source catch by name through a small tuple constant it builds at import time, so the mapping is testable without the real package.

- [ ] **Step 3: Run to verify they fail.** `uv run pytest -q tests/zigbee/test_source.py`. Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Write the implementation** `src/loxmatter/zigbee/source.py` (GPL header first). The module docstring must state that this is the only file besides `configure.py` that imports zigpy, and why the bellows thread is kept:

```python
"""`ZigbeeSource` - zigpy behind the `DeviceSource` boundary.

Satisfies the protocol without an adapter, exactly as `BridgeMatterClient`
does (boundary design section 3.3). One class per technology, nothing
wrapped around it.

**bellows keeps its own event loop in its own thread** (`use_thread=True`,
the default) and proxies calls both ways. That is kept deliberately: ASH
acknowledges frames on a deadline and loxmatter's main loop does synchronous
SQLite work, so a blocked main loop would otherwise cause NCP resets
(research E.1). Home Assistant runs it the same way. The consequence is that
`disconnect()` MUST always run - the thread is a non-daemon worker, and
without the shutdown it leaks and the stick is left mid-frame.

**zigpy and bellows never reconnect by themselves.** A lost link surfaces
exactly once, as the listener event `connection_lost(exc)`; bellows' watchdog
(every 10 s, four consecutive failures) turns a wedged NCP into the same
event. So `sources/supervisor.py`'s existing loop is exactly right, and the
only work here is making that event reach it.
"""
```

Implement in this order:

1. `ZigbeeUnavailableError`, and `_STARTUP_MESSAGES`, an ordered list of `(exception predicate, i18n key)` pairs implementing spec §4.6's table. The EBUSY case must be matched on `errno`, not on the message text.
2. `connect()`: shut the old application down (`await old.shutdown(db=True)`) **before** anything else, `await ensure_quirks_loaded()`, build the config, `new(start_radio=False, ...)`, `startup(auto_form=True)`. On any failure: shut the new object down, clear the field, raise `ZigbeeUnavailableError` with the translated message. Never reuse an object whose `startup()` failed.

   `ensure_quirks_loaded()` stays **inside** `connect()`, so the ordering guarantee — quirks before any device object is built — lives in exactly one place and cannot be forgotten by a second caller. That is safe only because **`connect()` is never called on a request path.** Its one caller is a background worker: `sources/supervisor.py`'s loop, which performs the first connect as well as every reconnection — `wait_for_link_loss()` returns at once for a source that was never connected — including the first one after a radio is configured from the web UI (Task 11). `cli._run` deliberately does not call it at startup; see Task 10's Step 3 for why a second caller there races this one on a single serial port. If a future change ever puts `connect()` behind an HTTP handler, it puts a 9-15 s warm-up plus `startup()` plus a possible 7.5 s silent-port timeout on that request, which is what the Global Constraint about request paths forbids.

   `connect()` therefore also maintains `ConnectionProgress`, because it is the only thing that knows how far an attempt got and nothing else can report it while it runs:

```python
    async def connect(self) -> None:
        self._set_progress("loading_quirks")
        try:
            if self._app is not None:
                app, self._app = self._app, None
                await app.shutdown(db=True)
            await ensure_quirks_loaded()
            self._set_progress("opening_radio")
            app = await self._new_application()
            try:
                await app.startup(auto_form=True)
            except BaseException:
                # Never keep an object whose startup() failed - see
                # `test_a_failed_startup_is_shut_down_and_not_kept`.
                await app.shutdown(db=True)
                raise
        except Exception as exc:
            # `attempts` counts FAILED attempts, so the card can say "still
            # trying, 4 attempts" rather than implying a first try that is
            # about to succeed. The message is already translated: it is the
            # one the user reads, and `api/zigbee.py` hands it straight out.
            message = self._startup_message(exc)
            self._set_progress("failed", error=message, count_attempt=True)
            raise ZigbeeUnavailableError(message) from exc
        self._app = app
        self._connected = True
        self._link_lost.clear()
        self._set_progress("connected", attempts=0)
        if self._on_connection_change is not None:
            await self._on_connection_change(True)
```

   `_set_progress` stamps `changed_at` with `now_iso()` (`loxmatter.timestamps`, as the rest of the project does) and replaces the frozen dataclass wholesale. `disconnect()` and the `connection_lost` listener both set `"idle"` and `"failed"` respectively, and both await `on_connection_change(False)`. A source that has never been asked to connect reports `"idle"` with `attempts = 0`.
3. The zigpy configuration, each value carrying its reason as a comment: `database_path` at the path given, **OTA off**, topology scan kept at its 4 h default, `validate_network_settings=True`, and a channel list that **excludes the Thread channel** read from OTBR's active dataset when one is available (fall back to the full `[11, 15, 20, 25]` when it is not — a missing border router must not stop Zigbee from forming).
4. `connected`, `wait_for_link_loss`, `disconnect` — the explicit flag, the `asyncio.Event`, and a `disconnect` that always runs the shutdown.
5. `snapshots()` — builds `DeviceFacts` from zigpy's device objects and calls `build_snapshot`; works while disconnected, with `available` from Task 8's checker (before Task 8 lands, `available=self._connected`).
6. `subscribe()` — registers the four attribute events (`attribute_report`, `attribute_read`, `attribute_updated`, `attribute_written`) on every cluster of every device, both freshly initialised and database-loaded, re-registering after every reconnect, reinterview and removal; callbacks only `put_nowait`; one dispatch task does the awaiting.
7. `follow()`, `send()`, `remove()`, `permit()`.

- [ ] **Step 5: Add the i18n keys** — every one with `en` **and** `de`: `api.errors.zigbee_stick_missing`, `api.errors.zigbee_no_device_permission`, `api.errors.zigbee_stick_busy`, `api.errors.zigbee_not_a_coordinator`, `api.errors.zigbee_network_mismatch`, `api.errors.zigbee_not_connected`. Write the texts from spec §4.6's "What the UI says" column — each names the next thing the user should do, and the busy one names a second loxmatter instance as the common case.

- [ ] **Step 6: Run to verify they pass.** `uv run pytest -q tests/zigbee/`.

- [ ] **Step 7: Prove each protection catches its fault.** Thirteen faults, from the docstrings. FAIL, revert, PASS, both pasted.

- [ ] **Step 8: Run the checks** (all five, four-part pytest).

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/zigbee/source.py tests/zigbee src/loxmatter/i18n/strings.yaml
git commit -m "$(cat <<'EOF'
feat(zigbee): add the Zigbee device source behind the existing boundary

zigpy never reconnects on its own and reports a lost link exactly once, so
the source turns that one event into the signal the supervisor already knows
how to act on, and builds a fresh application every time rather than reusing
one whose startup failed. Cluster callbacks only enqueue, because zigpy
emits them synchronously and swallows nothing. Every zigpy failure leaves
this file as the one shared "device unreachable", and each way a radio can
fail to come up gets the words that say what to do about it.

OTA is off: a bridge that silently updates the user's lamps from the
internet is not what this project promises.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Availability

zigpy has no concept of a device being available. ZHA's is worth copying literally, and the half that matters most is the one ZHA does not need: **on link loss, every Zigbee device goes offline**, or Loxone keeps the last value and believes the sensors are still alive.

**Files:**
- Create: `src/loxmatter/zigbee/availability.py`, `tests/zigbee/test_availability.py`
- Modify: `src/loxmatter/zigbee/source.py`

**Interfaces:**
- Consumes: `RuntimeEventHandler.set_online` (already on `Runtime`), the source's device catalogue.
- Produces:
  - `MAINS_THRESHOLD_SECONDS = 2 * 60 * 60`, `BATTERY_THRESHOLD_SECONDS = 6 * 60 * 60`, `CHECK_INTERVAL_SECONDS = 30.0`
  - `AvailabilityChecker(source, handler, resolve_device_id, *, sleep=asyncio.sleep, now=...)` with `start()`, `stop()`, `mark_all_offline()`

- [ ] **Step 1: Write the failing tests** `tests/zigbee/test_availability.py`. `sleep` and `now` are injected so the tests **measure** the thresholds instead of sitting them out — the stance `sources/supervisor.py` already documents ("a test that sleeps for eight seconds gets skipped as slow at the next rework and then checks nothing at all").

```python
async def test_a_mains_device_is_offline_after_two_hours_and_a_battery_one_after_six():
    """From the node descriptor's `is_mains_powered`. One threshold for both
    would either declare every battery sensor dead four times a day, or take
    six hours to notice a dead lamp.

    Fault to prove it: use one threshold for both."""


async def test_a_mains_device_is_pinged_twice_before_it_is_declared_offline():
    """ZHA reads `Basic.manufacturer` with `allow_cache=False`, twice, with
    two grace periods. A mains device that simply had nothing to report is
    not a dead one.

    Fault to prove it: declare it offline on the first expiry. A quiet plug
    then drops offline in Loxone every two hours."""


async def test_lumi_devices_are_never_pinged():
    """They do not answer, so a ping proves nothing about them and only
    wastes a wake-up.

    Fault to prove it: ping them too - the LUMI sensor then reports offline
    despite being alive."""


async def test_every_device_goes_offline_when_the_link_is_lost():
    """THE one this feature exists for. zigpy has no availability concept at
    all, so without this sweep a pulled coordinator leaves every sensor
    showing its last value in Loxone, forever, with nothing marking it
    stale - a motion sensor that reads "no motion" because the radio is gone
    is worse than one that reads nothing.

    Fault to prove it: drop the link-loss sweep."""


async def test_the_online_state_is_seeded_from_the_database_after_a_restart():
    """zigpy persists `last_seen`, so the state after a restart is known
    rather than unknown - and it reaches Loxone through
    `NodeSnapshot.available` at `attach()` time, not through a separate
    path.

    Fault to prove it: seed every device as online."""
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement** `src/loxmatter/zigbee/availability.py`, and call `mark_all_offline()` from the source's `connection_lost` listener. The checker runs every 30-45 s; the poll that Task 9 falls back to for lamps that refuse reporting doubles as a liveness check, as it does in ZHA.

- [ ] **Step 4: Run to verify they pass.**

- [ ] **Step 5: Prove each protection catches its fault.** Five faults. FAIL, revert, PASS, both pasted.

- [ ] **Step 6: Run the checks** (all five, four-part pytest).

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/zigbee/availability.py src/loxmatter/zigbee/source.py tests/zigbee/test_availability.py
git commit -m "$(cat <<'EOF'
feat(zigbee): report whether a Zigbee device is still there

zigpy has no availability concept, so the bridge keeps its own: two hours of
silence for a mains device, six for a battery one, and a mains device is
pinged twice before it is written off because a quiet plug is not a dead one.

When the coordinator goes away every Zigbee device is pushed offline at once.
Without that, Loxone keeps the last value and a motion sensor reads "no
motion" because the radio is gone.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

**Measured correction, 12 September 2026 — read before Task 10.** `AvailabilityChecker` is complete and committed as above, and its Step 3 correctly scoped out starting the sweep (Task 10's job, not this one). But a review that ran it against the real source measured something Task 8's own suite of 114 tests does not catch: `_sweep()`/`_check_one()` decide availability from `is_available(device)` alone, while `ZigbeeSource._facts()` (the snapshot path) decides it from `self._connected and is_available(device)`. The two disagree the instant a link is lost. Measured sequence: `mark_all_offline()` reports `[(1, False)]`, correctly — and the very next sweep tick reports `[(1, False), (1, True)]`, because the device's `last_seen` is recent (it was heard from seconds before the coordinator died) and nothing in the sweep ever asks whether the source is still connected. Started as Task 10 originally wired it, this ships a regression: every device the link-loss protection just marked offline is read as available again on the next tick, up to `CHECK_INTERVAL_SECONDS` later — the exact failure this module exists to prevent, self-inflicted. Seven targeted mutations (to `start()`, `stop()`, `_run()`, and all four grace-counter branches) left all 114 tests green, which is the same shape of finding as the faults this plan's Step 5 rounds exist to catch — nothing here asked the question a fault-injection pass over this specific interaction would have asked.

The fix, applied to this file as a follow-up correction rather than by rewriting Task 8's steps above (the same convention the Task 1 baseline note below uses): `_check_one()` now checks `self._source.connected` before anything else and reports `False` at once when it is not, ahead of the `is_available()` branch — checked per device, inside the method every device already passes through one at a time, rather than once at the top of `_sweep()`. A sweep that spends several seconds pinging quiet mains devices in a row can have the link die partway through it, and only a per-device check stops the tail of that same sweep from reporting devices online again right after `mark_all_offline()` already told Loxone otherwise. A new `_devices_to_check()` helper — used by both `_sweep()` and `mark_all_offline()`, so the two cannot drift apart on which devices they cover — filters out the coordinator's own entry via `node_desc.is_coordinator` (zigpy 2.2.0; ZHA's own `_check_available`/`DeviceAvailabilityChecker` exempt it the same way), because zigpy keeps the coordinator in `app.devices` like any other node and nothing else was filtering it before. `mark_all_offline()` also now guards each device with its own `try`/`except`, so one that raises (a closed UDP socket, mid-shutdown) does not leave every device *after* it in the loop stuck at its last reported value. A new `_report()` step de-duplicates by the last value actually told to the handler, needed once the connection-loss branch and the periodic sweep can both decide the same device's fate. The same review additionally found `_check_one()` reading the wrong bit of the node descriptor to decide who gets pinged — `is_mains_powered` rather than `is_receiver_on_when_idle`, two different bits of the same MAC capability byte — corrected alongside the rest as `_answers_unsolicited_reads()`. Task 10's own wiring must additionally have `subscribe()` await the previous checker's `stop()` before building a new one, and `disconnect()` stop it too — `subscribe()` runs again on every reconnect, and replacing the reference without stopping the old task first leaks one dangling sweep per reconnect, each reading an application object that is progressively more stale.

---

### Task 9: Configure-on-Join

zigpy does not configure devices to report — that was ZHA's entity layer's job, and without it **a paired sensor is silent**. This is the task that decides whether the feature works at all.

**Files:**
- Create: `src/loxmatter/zigbee/configure.py`, `src/loxmatter/model/zigbee_pending_store.py`, `tests/zigbee/test_configure.py`, `tests/model/test_zigbee_pending_store.py`
- Modify: `src/loxmatter/model/store.py` (schema 10), `src/loxmatter/zigbee/source.py`

**Interfaces:**
- Consumes: the fake application from Task 7's `tests/zigbee/fakes.py`.
- Produces:
  - `Store._SCHEMA_VERSION` becomes **10**, adding the table `zigbee_pending_config(address TEXT, endpoint INTEGER, cluster_id INTEGER, PRIMARY KEY (address, endpoint, cluster_id))`.
  - `store.zigbee_pending: ZigbeePendingStore` with `mark_pending(address, endpoint, cluster_id)`, `clear(address, endpoint, cluster_id)`, `pending_for(address) -> list[tuple[int, int]]`, `addresses_with_pending() -> list[str]`, `forget(address)`.
  - `configure_device(device, *, store, now) -> ConfigureOutcome` in `configure.py`, with `ConfigureOutcome(configured: tuple[int, ...], deferred: tuple[int, ...], quirk_applied: bool)`.
  - `REPORTING: dict[tuple[int, int], tuple[int, int, int]]` — the (cluster, attribute) → (min, max, change) table of spec §6.2.

- [ ] **Step 1: Write the migration test** in `tests/model/test_store.py`, following the shape the migration tests already in that module use:

```python
def test_migration_10_adds_the_pending_table_and_only_adds():
    """Schema 10 is ADDITIVE, like every migration since 9 and for the same
    reason: `deploy/updater/update-once.sh` rolls a failed update back to
    the OLD image WITHOUT restoring the database, on the stated invariant
    that an older version starts up fine on a newer schema. A NEW TABLE is
    the safest possible shape of that - version-9 code never names it, so it
    cannot trip over it, and the rows simply wait until a version that
    understands them runs again.

    Fault to prove it: drop a column from `device` in the same migration.
    The version-9 SQL test in this module then fails."""


def test_migration_10_is_idempotent_on_a_fresh_database():
    """A fresh database already has the table from `_SCHEMA` and still runs
    the whole chain from `user_version = 0`.

    Fault to prove it: use a bare `CREATE TABLE` without `IF NOT EXISTS`."""
```

- [ ] **Step 2: Write the pending-store tests** `tests/model/test_zigbee_pending_store.py`:

```python
def test_a_pending_cluster_survives_a_restart():
    """The whole point. A `configure_reporting` to a SLEEPING device fails
    with TimeoutError or DeliveryError after up to ~28 s per attempt, and
    the device may not wake for hours. Holding "still to do" in memory would
    lose it on every restart, and the sensor would stay silent forever with
    nothing recording why.

    Fault to prove it: keep the set in memory on the source instead."""


def test_an_interrupted_configuration_run_leaves_retryable_rows_not_a_stuck_state():
    """The lesson from the radios sidecar, applied here: any state a process
    can be interrupted in must be recoverable. A row in this table IS the
    recovery - it says "this cluster still needs configuring", which is true
    whether the run finished, failed, or was killed halfway. There is no
    non-terminal phase that can freeze, and nothing needs an SSH session to
    clear.

    Fault to prove it: write the rows only AFTER a successful pass. A run
    killed mid-way then leaves no trace, and the device is never retried."""


def test_forgetting_a_device_forgets_its_pending_rows():
    """Fault to prove it: leave them. Removing and re-pairing a device then
    inherits the old device's unfinished business."""
```

- [ ] **Step 3: Run both to verify they fail**, then implement schema 10 and `ZigbeePendingStore` (the `ResendSettingsStore` pattern: another view onto the same connection, its own module, its own class). Add the table to `_SCHEMA` **and** to `_migrate_to_v10`, both with `IF NOT EXISTS`, exactly as the existing migrations do. Wire `store.zigbee_pending` next to `store.resend_settings`, and call `zigbee_pending.forget(address)` from `forget_device`.

- [ ] **Step 4: Write the configure-on-join tests** `tests/zigbee/test_configure.py`:

```python
async def test_the_quirk_hook_runs_before_anything_else():
    """`apply_custom_configuration()` is what casts the Tuya "spell" - a
    specific Basic read of [4, 0, 1, 5, 7, 0xFFFE] - and without it many
    Tuya devices never send anything at all. It has to run FIRST, because
    the binds that follow depend on the quirk's own cluster objects.

    Fault to prove it: run it after the binds."""


async def test_a_device_that_asks_to_skip_configuration_is_left_alone():
    """79 quirks set `skip_configuration`, and they set it because binding
    or reporting actively breaks those devices.

    Fault to prove it: bind anyway."""


async def test_binding_goes_through_the_quirks_cluster_object():
    """So overrides such as `TuyaNoBindPowerConfigurationCluster` apply. A
    bind sent to the raw cluster bypasses the very fix the quirk exists to
    provide.

    Fault to prove it: bind the raw endpoint cluster."""


def test_the_reporting_table_is_zhas_field_proven_set():
    """These intervals are the only ones with field evidence behind them
    (research D.1, R1 section 7). Read from the module rather than retyped.

    Fault to prove it: change OnOff's max from 900 to 60 - the lamp then
    reports fifteen times as often, on a network shared with sleepy
    devices."""
    assert REPORTING[(0x0006, 0x0000)] == (0, 900, 1)
    assert REPORTING[(0x0008, 0x0000)] == (1, 900, 1)
    assert REPORTING[(0x0402, 0x0000)] == (30, 900, 50)
    assert REPORTING[(0x0405, 0x0000)] == (30, 900, 100)
    assert REPORTING[(0x0406, 0x0000)] == (0, 900, 1)
    assert REPORTING[(0x0400, 0x0000)] == (30, 900, 1)
    assert REPORTING[(0x0001, 0x0021)] == (3600, 10800, 1)


def test_the_ias_zone_cluster_is_bound_but_never_configured_for_reporting():
    """Alarms arrive as a CLIENT COMMAND, never as an attribute report, so a
    reporting configuration on `zone_status` is both useless and, on some
    devices, a failure that aborts the rest of the pass.

    Fault to prove it: add IasZone to the reporting table."""


async def test_ias_enrolment_writes_the_cie_address_and_answers_unsolicited():
    """WITHOUT THIS A CONTACT, MOTION OR LEAK SENSOR NEVER REPORTS ANYTHING.
    zigpy defines the commands but does not enroll. Four steps: bind, read
    `zone_type`, write `cie_addr` with the coordinator's own IEEE, then send
    an UNSOLICITED `enroll_response(Success, zone_id=0)`.

    Fault to prove it: skip the unsolicited enroll_response. The sensor
    pairs, configures, goes green - and never fires."""


async def test_a_status_change_notification_updates_the_zone_status_attribute():
    """This is HOW alarms arrive: as client command 0, not as a report. A
    design that only subscribes to reports sees a sensor that works
    perfectly and never triggers.

    Fault to prove it: handle only attribute reports."""


async def test_an_enroll_request_from_the_device_is_answered():
    """Client command 1, permanently handled - some sensors ask on every
    rejoin and stay unenrolled until they get an answer.

    Fault to prove it: ignore command 1."""


async def test_current_values_are_read_uncached_at_the_end():
    """This is what makes the `None` rule SATISFIABLE rather than merely
    stated: without a real read, every mapped path would be absent at join
    and the first row would only be created later, if at all.

    Fault to prove it: read with `allow_cache=True`. The values come back
    from zigpy's cache as `None` for a device nobody has read yet."""


async def test_a_sleeping_device_defers_instead_of_failing_the_whole_pass():
    """A `configure_reporting` to a sleeping device fails after up to ~28 s
    PER ATTEMPT. One such cluster must not take the other clusters - or the
    IAS enrolment - down with it.

    Fault to prove it: let the first DeliveryError abort the routine."""


async def test_a_deferred_cluster_is_retried_when_the_device_is_next_heard_from():
    """Right after a device transmits it polls its parent, so a queued
    request has its best chance THEN. Every incoming packet fires
    `device_last_seen_updated`; the routine also retries on `device_joined`
    and on `checkin`.

    Fault to prove it: retry on a fixed timer instead. The request then
    almost always arrives while the device is asleep again."""


async def test_fast_poll_mode_brackets_the_whole_routine_when_available():
    """`device.fast_poll_mode()` binds PollControl and writes
    `fast_poll_timeout`, keeping the device polling its parent for the whole
    configuration rather than only for the first command.

    Fault to prove it: wrap only the binds."""


async def test_a_lamp_that_refuses_reporting_falls_back_to_polling():
    """Common on cheap lamps: `configure_reporting` answers a status other
    than SUCCESS. ZHA polls those every 2700-4500 s, and that poll doubles
    as the liveness check Task 8 uses.

    Fault to prove it: treat a non-SUCCESS status as success. The lamp then
    shows a stale state in Loxone forever and nothing reports it."""


async def test_a_rejoin_with_a_new_nwk_configures_again():
    """A factory-reset device has lost its bindings, and it fires
    `device_joined` plus `device_initialized` again. A rejoin with the same
    NWK fires nothing and keeps its bindings anyway.

    Fault to prove it: configure only on first join. A reset sensor then
    pairs and stays silent."""


async def test_startup_reads_values_and_does_not_reconfigure_everything():
    """ZHA does not either, and a full reconfiguration at every start would
    wake every battery device on the network on every restart of the bridge.

    Fault to prove it: run the full routine for every device at startup."""
```

- [ ] **Step 5: Run to verify they fail**, then implement `configure.py` in the order spec §6.1 gives: quirk hook, static reads (`zone_type`, `color_capabilities`, `color_temp_physical_min/max`, `Basic` power source), bind and `configure_reporting_multiple` through the quirk's cluster object, IAS enrolment, uncached read of every mapped attribute, identify blink. Run the whole routine inside `async with app.request_priority(PacketPriority.HIGH)`, and bracket it with `fast_poll_mode()` when the device has a PollControl cluster.

- [ ] **Step 6: Run to verify they pass.**

- [ ] **Step 7: Prove each protection catches its fault.** Eighteen faults across both modules. FAIL, revert, PASS, both pasted. Do not batch this round.

- [ ] **Step 8: Run the checks** (all five, four-part pytest).

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/zigbee/configure.py src/loxmatter/model/zigbee_pending_store.py src/loxmatter/model/store.py src/loxmatter/zigbee/source.py tests
git commit -m "$(cat <<'EOF'
feat(zigbee): configure a device to report when it joins

zigpy binds nothing and configures no reporting - that was ZHA's entity
layer's job - so without this a paired sensor is simply silent. The quirk
hook runs first because it is what makes many Tuya devices talk at all, IAS
sensors are enrolled and their alarms are read from the client command they
actually arrive as, and every mapped value is read uncached at the end so no
signal row is ever created without one.

A sleeping device defers its clusters to a table that survives a restart and
is retried the moment anything is heard from it, rather than failing the pass
or hanging on it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Startup Wiring, and What the Heartbeat Means

This closes boundary design open point 9.1. `Runtime(link_ok=sources.all_connected)` would silence the Loxone watchdog when only the Zigbee stick is gone, and **Loxone would declare the whole bridge dead while every Matter device still works.**

**This task also starts the availability sweep Task 8 built and left unstarted, on purpose — and it must wire an already-corrected checker, not the one Task 8 committed as-is.** `AvailabilityChecker` (Task 8) is already constructed fresh inside `ZigbeeSource.subscribe()` — read that method's own comment, "not started here - only `mark_all_offline()` is wired up yet" — and Task 8's Step 3 named exactly one wire to build: `mark_all_offline()` from the `connection_lost` listener. That scope was correct as far as it went, but a review has since measured that simply calling `start()` on the committed checker ships a regression, not merely a missing feature (see the "Measured correction" note at the end of Task 8): its sweep decides availability from `is_available(device)` alone, never from `ZigbeeSource.connected`, so the very first sweep tick after a lost link reports every device `mark_all_offline()` just marked offline as available again — because each one was, correctly, heard from only seconds before the link died. Task 8's own 114 tests do not catch this, because they drive the checker directly and never run a sweep tick after a real link loss.

That correction is why starting and stopping this component needs its own task, and not only "call `start()` somewhere": **whoever starts a background loop takes on owning its stop, and that ownership has two parts, not one.** First, the checker's sweep must know the source's connection state, which is exactly the fact this task's whole first half (the heartbeat) already treats as the thing worth reporting correctly — starting a sweep that reports the opposite of what the heartbeat says in the same moment would make the two ends of one task disagree about what "connected" means. Second, `subscribe()` runs again on every reconnect (`attach()`'s documented contract), so whoever calls `start()` on a fresh checker there must `await` a `stop()` on whatever checker the previous `subscribe()` left running, or every reconnect leaks one more dangling sweep task reading a progressively staler application object. Task 8 proved the checker's own decisions (which threshold, who gets pinged, who never does) against a fake clock and a fake source, and none of that needed the periodic sweep actually *running* in a live process, or run more than once, to be tested. Running it for real, more than once, over the life of a process is what this task is the first to do, so the ownership questions above are its questions to close, not Task 8's.

**Files:**
- Modify: `src/loxmatter/cli.py`, `src/loxmatter/loxone/runtime.py`, `src/loxmatter/zigbee/source.py`, `src/loxmatter/zigbee/availability.py`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/test_cli.py`, `tests/loxone/test_runtime.py`, `tests/zigbee/test_source.py`, `tests/zigbee/test_availability.py`

`availability.py` is listed even though Task 8 created it: **check first whether the correction in Task 8's "Measured correction" note is already applied.** If it is (a follow-up fix may already have landed between Task 8 and this task running), this task only adds the tests below that pin the corrected behaviour down through `subscribe()`/`disconnect()` and leaves the file itself alone. If it is not, this task applies that correction itself before starting the sweep — shipping the uncorrected checker live would be worse than the gap this task was written to close.

**Interfaces:**
- Consumes: `ZigbeeSource` (Task 7), `ensure_quirks_loaded` (Task 1), `AvailabilityChecker.start()` / `AvailabilityChecker.stop()` and `ZigbeeSource.connected` (Task 7/8 — the checker's sweep now gates every report on this flag; see the correction note at the end of Task 8), the radio setting (Task 11 — until it lands, read the path from a CLI option `--zigbee-device` defaulting to `None`).
- Produces:
  - `loxmatter.loxone.runtime.ZIGBEE_CONNECTED_KEY = "zigbee_connected"`, declared beside `HEARTBEAT_KEY`.
  - `Runtime.cache_zigbee_connected(connected: bool) -> None` and `Runtime.set_zigbee_connected(connected: bool) -> None` — the cache/send pair, shaped exactly like the existing `_cache_online`/`set_online`. Step 3 builds both; there is no generic mechanism to reuse, because `HEARTBEAT_KEY` is the only non-device key `Runtime` sends today.
  - `cli._run` passes `on_connection_change=runtime.set_zigbee_connected` into `ZigbeeSource` (Task 7's Interfaces) and seeds `cache_zigbee_connected(False)` before `attach()`.
  - `ZigbeeSource.subscribe()` now calls `self._availability_checker.start()` after building a fresh checker. Stopping the previous one first (`_stop_availability_checker()`, called from both `subscribe()` and `disconnect()`) is confirmed or added as part of the availability.py correction above, not new to this bullet — `start()` is the one call nothing before this task ever made. No new public name: the checker's lifecycle stays entirely inside `ZigbeeSource`, the same way the dispatch task's does, so neither `cli._run` nor Task 11's `ZigbeeRuntime` has to remember to manage it — every `attach()`/reconnect/radio-swap gets it for free through `subscribe()`/`disconnect()`, which they already call.

- [ ] **Step 1: Write the failing tests.**

```python
async def test_the_heartbeat_keeps_pulsing_when_only_zigbee_is_down():
    """Boundary design open point 9.1. The Loxone watchdog means "the bridge
    and the MANDATORY source are alive" - if it went quiet because a USB
    stick was unplugged, the Miniserver would treat every Matter device as
    dead too, and a user with no Zigbee devices at all could lose their
    whole installation to a radio they never used.

    Fault to prove it: pass `sources.all_connected` as `link_ok`."""


async def test_zigbee_health_is_reported_as_its_own_signal():
    """Where it belongs: one more virtual input to wire IF the user cares,
    plus the per-device `d<id>_online` keys. Not in the watchdog.

    Fault to prove it: drop the signal - Zigbee's state then reaches Loxone
    nowhere at all."""


async def test_a_zigbee_radio_that_will_not_come_up_does_not_stop_the_bridge():
    """Matter is mandatory and Zigbee is not. `client.connect()` failing
    still ends startup; a Zigbee radio that never comes up does not, and
    `cli._run` does not connect it at all — the supervisor does, retrying
    forever on its own 1 s -> 60 s backoff, and `supervise()` is the thing
    that must turn a raised `ZigbeeUnavailableError` into a logged warning
    plus another attempt rather than into a dead task.

    Build a source whose `connect()` always raises `ZigbeeUnavailableError`,
    let `_run` start (uvicorn stubbed, as the neighbouring `_run` tests
    already do), and assert on the supervisor's own log line - "rebuild of
    source zigbee failed (...) - next attempt in 1 s" - and on `_run`
    having reached `uvicorn.Config` regardless.

    Fault to prove it: narrow `supervise()`'s inner `except Exception` to
    `except CannotConnect` (or delete the inner `try` entirely). The
    supervisor task then dies on the first `ZigbeeUnavailableError`, nothing
    ever retries the radio, and a stick plugged in five minutes later is
    never found - while `_run` itself still serves happily, which is exactly
    why asserting only that "the bridge runs on" would keep passing."""


async def test_the_quirks_warm_up_does_not_delay_the_web_ui():
    """`cli._run` starts uvicorn only AFTER `attach`, and the warm-up is an
    estimated 9-15 s on a Pi 4. Held anywhere on that path, `/health` and
    the web UI would be unreachable for the whole of it, every start -
    which the updater's own health wait would read as a failed update.

    What this proves, now that `cli._run` connects nothing itself: the
    startup path contains NO wait on `ZigbeeSource.connect()` - neither an
    inline `await`, nor an `await` on a supervisor task, nor any other
    shape. The only caller of `connect()` is `supervise()`, started with
    `ensure_future` and never awaited before `uvicorn.Config`, so a
    Raspberry Pi's warm-up runs entirely beside a web UI that is already
    answering.

    Fault to prove it: put `await zigbee.connect()` into `cli._run` just
    before `attach` - the shape an earlier draft of Step 3 had, and the one
    thing that could plausibly be reintroduced here. `connect()` begins by
    awaiting `ensure_quirks_loaded()` (Task 7), so awaiting it reproduces
    the exact delay this test exists to catch, even though the call would
    be named `connect()` rather than `ensure_quirks_loaded()`. A fake slow
    `setup` passed through to `ensure_quirks_loaded` and measured against
    wall-clock time is what turns this from a docstring into a real
    assertion: `_run` must reach `attach`/`uvicorn.Config` before that fake
    `setup` resolves, not after."""


async def test_no_zigbee_work_happens_when_no_radio_is_configured():
    """An installation without a Zigbee stick pays nothing: no import, no
    warm-up, no source in the registry.

    Fault to prove it: always construct the source."""


async def test_the_availability_sweep_starts_when_the_source_is_subscribed():
    """`AvailabilityChecker` has existed since Task 8, rebuilt fresh inside
    `ZigbeeSource.subscribe()` every time - but never started, by that
    task's own deliberate scope. Without this, `CHECK_INTERVAL_SECONDS`
    never elapses at all: a mains device that stopped reporting hours ago
    is never pinged and never written off, and the web UI reports it
    reachable forever, even though `mark_all_offline()` still fires
    correctly the moment the coordinator itself is lost (Task 8's own
    protection, unaffected by this gap).

    Fault to prove it: construct the checker in `subscribe()` without
    calling `start()` - Task 8's original shape. The periodic sweep then
    never runs, on any installation, ever."""


async def test_a_reconnect_stops_the_previous_sweep_before_starting_a_new_one():
    """`attach()` calls `subscribe()` again on every reconnect (its
    documented contract). `_stop_availability_checker()` already guards
    against replacing `self._availability_checker` without stopping its
    task first - but that guard was written and proven against a checker
    that was never started, so nothing before this task exercised it
    against a checker with a live sweep task actually running. Confirm it
    still holds now that `start()` is in the picture: ten reconnects over a
    flaky USB cable must not leave ten sweep tasks running, nine of them
    reading an application object `disconnect()` has already thrown away.

    Fault to prove it: build and start the new checker without first
    awaiting `stop()` on the old one (or without an old one being stopped
    at all, if `_stop_availability_checker()` is itself missing).
    `asyncio.all_tasks()` grows by one on every single `subscribe()` call
    instead of staying flat."""


async def test_a_device_marked_offline_by_link_loss_does_not_flip_back_on_the_next_sweep():
    """THE regression a review measured in the committed checker, not merely
    a missing feature: `_sweep()`/`_check_one()` compute availability from
    `is_available(device)` alone, which reads only `device.last_seen` - and
    a device heard from ten seconds before the coordinator died still
    passes that check for the next two (mains) or six (battery) hours.
    `ZigbeeSource._facts()` gets this right (`self._connected and
    is_available(device)`); the sweep, before this task's correction, does
    not.

    Measured directly against the real checker: `mark_all_offline()` after
    a link loss reports `[(1, False)]`, correctly - and the very next sweep
    tick reports `[(1, False), (1, True)]`, flipping the device straight
    back to "online" in Loxone. Seven separate mutations to `start()`,
    `stop()`, `_run()` and all four grace-counter branches left Task 8's
    114 tests green, because none of them ever runs a sweep tick after a
    real link loss.

    Fault to prove it: revert `_sweep()`/`_check_one()` to decide
    availability from `is_available(device)` alone, without checking
    `self._source.connected` - Task 8's committed shape. This test then
    fails exactly as the measurement above describes: online again, one
    sweep after offline."""
    harness = build(FakeApplication(devices=[colour_lamp()]))
    await harness.source.connect()
    # `colour_lamp()` defaults `last_seen` to "now" (`fakes.py`'s own
    # `_LAST_SEEN_UNSET` sentinel) - heard from moments ago, exactly the
    # shape that made the measured regression invisible to `is_available`
    # alone.
    await harness.source.subscribe(_lamp_resolver(device_id=1), harness.handler)
    harness.app.fire_connection_lost()
    await _settle(harness.source)
    assert harness.handler.online == [(1, False)]
    # The next scheduled tick, invoked directly rather than waited for -
    # `AvailabilityChecker._sweep()` is what a real 30 s timer would call.
    await harness.source._availability_checker._sweep()
    assert harness.handler.online == [(1, False)]


async def test_the_availability_sweep_stops_on_disconnect():
    """A checker whose sweep task keeps running after `disconnect()` reads
    `_devices()` against an application that no longer exists (harmless -
    it returns `[]`), but the task itself is never cancelled: it leaks for
    the rest of the process, one more each time the source reconnects or a
    radio is swapped out from under it (Task 11). `disconnect()` already
    calls `_stop_availability_checker()` for a checker that was never
    started; this confirms the same call actually cancels a LIVE sweep task
    now that `start()` is wired in.

    Fault to prove it: drop the `_stop_availability_checker()` call from
    `disconnect()` (or have `start()` not being called mask a missing
    `stop()` entirely, which is why this test must run after `start()` is
    wired, not before). `asyncio.all_tasks()` then grows by one dangling
    sweep task on every single reconnect."""
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement.** In `cli._run`:

```python
    sender = UdpSender(miniserver, port)
    client = _build_client(url)
    # The heartbeat keeps meaning "the bridge and the MANDATORY source are
    # alive" (design 2026-09-12, section 4.9; boundary design open point
    # 9.1). NOT `sources.all_connected`: that would silence the Loxone
    # watchdog when only the Zigbee stick is gone, and the Miniserver would
    # declare the whole bridge dead while every Matter device still worked.
    # Zigbee's own health reaches Loxone as `zigbee_connected` and as the
    # per-device `d<id>_online` keys instead.
    runtime = Runtime(store, sender, link_ok=lambda: client.connected)
    zigbee = _build_zigbee_source(store)  # None when no radio is configured
    sources = Sources([client] if zigbee is None else [client, zigbee])
    invoke = sources.send
```

**`cli._run` calls `zigbee.connect()` nowhere — not inline, and not as a background task.** Two earlier drafts of this step did, the second one backgrounding what the first one awaited; both are wrong, and the reasons were established by reading `sources/supervisor.py` and `zigbee/source.py` rather than reasoning from this document:

- **`supervise()` already performs the first connect, and doing it here as well races it.** `supervise()` opens with `await source.wait_for_link_loss()`, which returns *immediately* while `_connected` is `False` — its own docstring says so ("or at once when it never existed… a radio that is missing at startup is retried on exactly the same schedule as one that dies an hour later"). The supervisor therefore falls straight into `connect()` → `attach()` → the 1 s → 60 s backoff on failure, for a source that was never connected. With a second connect started in `cli._run`, the two overlap: the supervisor tasks are created a few lines after `attach`, and on a Pi the startup attempt is usually still inside the 9-15 s `ensure_quirks_loaded()` when that happens. `ZigbeeSource.connect()` has **no reentrancy guard** — both calls see `self._app is None`, both build an application on the one serial port, and the loser's application is leaked with its non-daemon bellows serial thread still running.
- **And if the startup attempt wins the race instead, `attach()` never runs again.** `wait_for_link_loss()` would then park on `_link_lost` for the life of the connection, so the supervisor's own `connect()`/`attach()` pair is never reached: devices appear only as they happen to report, a quiet lamp never appears at all, `backfill_commands` never runs, and nothing is controllable.
- **Backgrounding it does not even buy the catalogue it claimed to.** The earlier draft justified itself with "`snapshots()` still returns the device catalogue, with `available=False`, when the radio never came up". That is true of a connection that dropped *after* a first connect, and false before one: `_devices()` returns `[]` while `self._app is None`, and `snapshots()`'s own docstring says "Before the first application exists at all, the honest answer is an empty list — nothing has read the database yet." A backgrounded connect that is still loading quirks when `attach()` runs therefore hands `seed_from_snapshot`, `backfill_device_types`, `backfill_network_features` and `backfill_commands` an empty list, and `resend_all()` carries no Zigbee value at all.

So `cli._run` builds the source, puts it in `Sources`, seeds `cache_zigbee_connected(False)`, runs `attach` over it (which tolerates a disconnected source), and starts its supervisor — nothing else. The supervisor connects it, calls `attach()` again itself the moment it succeeds, and retries forever on failure without ever making uvicorn wait. That is the same "startup and reconnect MUST do the same thing" argument `attach()`'s own docstring gives, applied one level up: a Zigbee source that is connected by exactly one code path cannot have a startup path that drifts from the reconnect one.

`ZigbeeSource.connect()` is still the one and only place that awaits `ensure_quirks_loaded()`, so the ordering guarantee from Task 7 — quirks before any device object is built — still lives in exactly that one place. It simply now has a single caller: the supervisor's loop, a background worker that no request path and no other startup step waits for.

**First, confirm (or apply) the correction to `src/loxmatter/zigbee/availability.py`.** Check whether `_check_one()`, `mark_all_offline()` and a `_devices_to_check()` helper already look like the code below — a follow-up fix may already have landed between Task 8 and this task running. If they do, this task touches nothing in that file and only adds the tests above, which pin the corrected behaviour down through `subscribe()`/`disconnect()`. If they do not, apply exactly this (it replaces `_is_mains_powered`'s use inside `_check_one` with a new, correctly-chosen predicate as well — a second, independent finding from the same review: `_check_one` was gating pings on `is_mains_powered`, but the bit that actually says whether a device is listening between its own transmissions is `is_receiver_on_when_idle`, a different bit of the same MAC capability byte):

```python
def _answers_unsolicited_reads(device: Any) -> bool:
    """Whether anything is listening between this device's own transmissions.

    `node_desc.is_receiver_on_when_idle` and NOT `is_mains_powered` - the two
    are different bits of the same MAC capability byte (verified against the
    installed zigpy 2.2.0), and it is this one, not the powered one, that
    says whether a ping can be answered at all. An uninterviewed device
    answers `None` here, treated the same as a sleepy one: not pinged, the
    direction that costs nothing.
    """
    node_desc = device.node_desc
    return bool(node_desc is not None and node_desc.is_receiver_on_when_idle)


def _is_coordinator(device: Any) -> bool:
    """Whether this "device" is the radio this bridge is talking through.

    zigpy keeps the coordinator in `app.devices` alongside every real node -
    nothing else filters it out, so without this both the sweep and
    `mark_all_offline()` would ping and report on the bridge's own radio as
    if it were a quiet mains device (ZHA's own `_check_available` and
    `DeviceAvailabilityChecker` both exempt it the same way).
    `NodeDescriptor.is_coordinator` answers `None` for a descriptor that has
    not been read yet, which `bool()` turns into "an ordinary device" - the
    safe reading, since an ordinary device merely gets checked.
    """
    node_desc = device.node_desc
    return bool(node_desc is not None and node_desc.is_coordinator)


class AvailabilityChecker:
    # ... constructor unchanged, plus one more piece of state:
    def __init__(self, source, handler, resolve_device_id, *, sleep=asyncio.sleep, now=time.time):
        ...
        # Per address, the last `available` this checker actually told the
        # handler. Without it every device would be re-announced every
        # thirty seconds forever, once `_check_one` and `mark_all_offline`
        # can both decide the same device's fate.
        self._reported: dict[str, bool] = {}

    def _devices_to_check(self) -> list[Any]:
        """The catalogue minus the radio itself - used by BOTH `_sweep()`
        and `mark_all_offline()`, so the two cannot drift apart on which
        devices they cover: a coordinator the sweep refuses to bring back
        online must not be one `mark_all_offline` is willing to take down,
        or it would be stuck offline for good."""
        return [device for device in self._source._devices() if not _is_coordinator(device)]

    async def _report(self, address: str, device_id: int, online: bool) -> None:
        """Tells the handler, but only when the answer has changed."""
        if self._reported.get(address) == online:
            return
        await self._handler.set_online(device_id, online)
        # Only after the handler returned: if it raised, the change is
        # still outstanding and the next sweep says it again.
        self._reported[address] = online

    async def mark_all_offline(self) -> None:
        """THE feature this module exists for: pushes every known device
        offline at once, in response to the coordinator going away.

        **Every device is guarded on its own.** `_handle_connection_lost`
        runs this through `_spawn`, so an exception escaping here reaches
        nothing but asyncio's "never retrieved" logger, and the devices
        after the one that raised would keep their last value forever -
        precisely the outcome this method exists to prevent.
        `RuntimeEventHandler.set_online` really does raise
        (`UdpSender.send` raises once its socket is closed).
        """
        self._missed_checkins.clear()
        for device in self._devices_to_check():
            device_id = self._resolve_device_id(str(device.ieee))
            if device_id is None:
                continue
            try:
                await self._report(str(device.ieee), device_id, False)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("could not mark Zigbee device %s offline", device.ieee)

    async def _sweep(self) -> None:
        moment = self._now()
        for device in self._devices_to_check():
            await self._check_one(device, moment)

    async def _check_one(self, device: Any, moment: float) -> None:
        address = str(device.ieee)
        device_id = self._resolve_device_id(address)
        if device_id is None:
            return
        if not self._source.connected:
            # THE gate: losing the coordinator does not touch `last_seen`,
            # so a sweep that asked the device alone would answer "online"
            # within thirty seconds of the link going down and undo what
            # `mark_all_offline()` just told Loxone - the same answer
            # `ZigbeeSource._facts` already uses for a snapshot
            # (`self._connected and is_available(device)`). Checked here,
            # inside the per-device method every device already passes
            # through, rather than once at the top of `_sweep()` - a sweep
            # that spends several seconds pinging earlier devices can have
            # the link die partway through it, and only a per-device check
            # stops the tail of that same sweep from reporting devices
            # online again right after `mark_all_offline()` already
            # answered (`test_a_device_marked_offline_by_link_loss_does_not_flip_back_on_the_next_sweep`).
            self._missed_checkins.pop(address, None)
            await self._report(address, device_id, False)
            return
        if is_available(device, now=moment):
            self._missed_checkins.pop(address, None)
            await self._report(address, device_id, True)
            return
        if not _answers_unsolicited_reads(device):
            await self._report(address, device_id, False)
            return
        if device.manufacturer == _LUMI_MANUFACTURER:
            await self._report(address, device_id, False)
            return
        missed = self._missed_checkins.get(address, 0)
        if missed >= _CHECKIN_GRACE_PERIODS:
            await self._report(address, device_id, False)
            return
        self._missed_checkins[address] = missed + 1
        await self._ping(device, address)
```

(`_ping` itself, the LUMI/grace-counter comments and `is_available` are unchanged from Task 8's committed version — shown collapsed above only where the branching itself did not change, per this plan's own rule against retyping unchanged code; write the full method exactly as committed, with the two `await self._handler.set_online(...)` calls it does not have replaced by `await self._report(...)`.)

**And in `src/loxmatter/zigbee/source.py`, start this now-corrected availability sweep.** `AvailabilityChecker` has been constructed inside `subscribe()` since Task 8, with the comment `# not started here - only mark_all_offline() is wired up yet`. Check first whether `subscribe()` and `disconnect()` already call a `_stop_availability_checker()` helper — a follow-up fix may already have landed alongside the `availability.py` correction above, adding exactly that: `_stop_availability_checker()` (stopping and clearing whichever checker is currently held, awaited, idempotent) called from `disconnect()` and from `subscribe()` before it rebuilds a fresh checker, so a checker from an earlier `subscribe()` call — `attach()` runs it again on every reconnect — is never simply dropped and leaked. If that helper does not exist yet, add it exactly as described and call it from both places, matching the shape `disconnect()` already uses for its own cleanup steps (`dispatch_task, self._dispatch_task = self._dispatch_task, None` and the like).

**Either way, this task is the one that adds the single missing line: starting the checker.** Nothing before this task ever called `AvailabilityChecker.start()` on a real, running source — Task 8 proved the checker's own decisions against a fake clock, which needed the sweep built, never running. This is the first task that runs a `ZigbeeSource` through a real service lifecycle, so it is where the periodic sweep actually begins:

```python
        self._register_cluster_listeners()
        await self._stop_availability_checker()
        # Rebuilt fresh on every call, exactly as before this task - but now
        # also STARTED here: this is the first task that runs a source
        # through a real subscribe-to-disconnect service lifecycle, so it is
        # where the periodic sweep (`CHECK_INTERVAL_SECONDS`) actually
        # begins running. By the time it runs live it must already gate
        # every report on `self.connected` (see the correction note at the
        # end of Task 8) and exclude the coordinator, or the sweep will
        # contradict `mark_all_offline()` a tick later
        # (`test_a_device_marked_offline_by_link_loss_does_not_flip_back_on_the_next_sweep`).
        self._availability_checker = AvailabilityChecker(self, handler, resolve_device_id)
        self._availability_checker.start()
        await self._seed_baseline()
```

`disconnect()` needs no change here beyond the `_stop_availability_checker()` call confirmed or added above — there is nothing left in it for this task to start.

**And in `src/loxmatter/loxone/runtime.py`, build the `zigbee_connected` signal.** Spec §4.9 requires it and the second test above asserts it, and there is nothing to build on: `Runtime` sends exactly one non-device key today (`HEARTBEAT_KEY`, from `_heartbeat_loop`) and has no generic mechanism for a second one. Add the key beside it:

```python
PULSE_MILLISECONDS = 200
HEARTBEAT_KEY = "bridge_alive"
# Whether the OPTIONAL Zigbee radio is up (design 2026-09-12, section 4.9).
# Deliberately a key of its own rather than a term in the watchdog: the
# heartbeat means "the bridge and the MANDATORY source are alive", and a
# Zigbee stick that fell out must not make the Miniserver declare a bridge
# dead whose Matter devices are all working. One more virtual input to wire
# IF the user cares.
ZIGBEE_CONNECTED_KEY = "zigbee_connected"
```

and the cache/send pair, modelled on `_cache_online`/`set_online` — which are two methods rather than one for a reason that applies here unchanged:

```python
class Runtime:  # the existing class - two methods added, next to `set_online`
    def cache_zigbee_connected(self, connected: bool) -> None:
        """Enters the radio's state into the cache, WITHOUT sending.

        The `_cache_online` half of the pair, and needed for the same
        reason (review fix C1, 2026-09-02): `cli._run` seeds this before
        `attach()`, and `attach()` ends in `resend_all()`, which sends every
        cached value with `force=True`. A seed that sent for itself would
        put `zigbee_connected` on the wire twice on every single startup.
        """
        self._last_values[ZIGBEE_CONNECTED_KEY] = connected

    async def set_zigbee_connected(self, connected: bool) -> None:
        """Reports a change of the radio's state to Loxone and the UI.

        Caching first, then sending, then notifying observers - the exact
        order `set_online` uses, and for the reason `add_observer`
        documents: the observer must learn what actually happened, not what
        was intended, so it is told only after the send has returned.

        Caching is what makes the value survive a Miniserver restart: it
        joins the set `resend_all()` restores (spec 6.4). Without it, a
        Miniserver that rebooted while the radio was down would show the
        input at its default until the radio next CHANGED state, which for
        a healthy stick is never.
        """
        self.cache_zigbee_connected(connected)
        await self._sender.send(ZIGBEE_CONNECTED_KEY, connected)
        self._notify_observers(ZIGBEE_CONNECTED_KEY, connected)
```

Wire it in `cli._run`: pass `on_connection_change=runtime.set_zigbee_connected` when constructing the `ZigbeeSource` (Task 7's Interfaces), and seed the startup value with `runtime.cache_zigbee_connected(False)` **before** the `attach()` loop, so the first `resend_all()` carries it. Seed `False`, not `True`: at that point the radio has not come up yet, and the startup connect attempt a few lines later either sets it to `True` or leaves it correctly at `False`. An installation with no Zigbee radio configured seeds nothing at all and never sends the key — Loxone simply never sees an input it has no use for.

- [ ] **Step 4: Run to verify they pass.**

- [ ] **Step 5: Prove each protection catches its fault.** Nine faults (five of the heartbeat/startup, plus the four availability-sweep tests above). Prove `test_a_device_marked_offline_by_link_loss_does_not_flip_back_on_the_next_sweep` exactly against the mutation the measurement used — remove the `self._source.connected` check from `_sweep()`/`_check_one()` — and paste it first: it is the one this task's correction note exists for, and if it does not fail on that specific mutation the correction has not actually landed in the tree being tested. FAIL, revert, PASS, both pasted.

- [ ] **Step 6: Run the checks** (all five, four-part pytest).

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/cli.py src/loxmatter/loxone/runtime.py src/loxmatter/zigbee/source.py src/loxmatter/zigbee/availability.py src/loxmatter/i18n/strings.yaml tests
git commit -m "$(cat <<'EOF'
fix(loxone): keep the watchdog meaning what it has always meant

With a second source, all_connected would have silenced the Loxone heartbeat
whenever the Zigbee stick was gone, and the Miniserver would have declared
the whole bridge dead while every Matter device still worked. The heartbeat
now covers the mandatory source; Zigbee reports its own health as a signal
and per device.

A Zigbee radio that will not come up is logged and retried, never fatal.
cli._run connects it nowhere: supervise() already performs the first connect
as well as every later one, so the quirks warm-up runs in a supervisor task
the web UI never waits for, and one serial port has one opener.

Also starts the availability sweep Task 8 built: AvailabilityChecker has been
constructed inside ZigbeeSource.subscribe() since that task landed, but
nothing ever called start(), so the periodic sweep never ran and a device
gone quiet was never marked offline except by a lost coordinator link.

A review measured that simply starting the checker as Task 8 committed it
would have shipped a regression rather than only a missing feature: its
sweep judged availability from last_seen alone, so the tick right after a
lost link flipped every device mark_all_offline() had just reported offline
back to online. availability.py now gates every report on whether the
source is still connected, checked per device rather than once per sweep,
skips the coordinator's own entry, and guards mark_all_offline() per device
so one failure cannot freeze the rest at their last value.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: The Zigbee Radio Setting, and Refusing the Thread Stick

**Read Spec Correction 3 before starting.** The Zigbee stick is a **bridge-owned** setting, not a sidecar request: zigpy runs in-process, so the change is applied by reconnecting the source — instantly, with no container recreated and no other radio disturbed. The sidecar's two-half request is not touched.

**This task is now backed by a hardware measurement, and it is the reason the task exists.** On the maintainer's Pi (12 September 2026) there are exactly two USB serial sticks. Both report `10c4:ea60`, both are major 188. One is his Zigbee coordinator; the other is an **MG24 that his Thread border router is currently running on**, confirmed by `RADIO_DEVICE=/dev/ttyUSB0`. Task 4's fingerprint table — correctly — reports that MG24 as a perfectly good EZSP Zigbee coordinator, because it is one. Nothing in the fingerprint layer can or should prevent it being offered. **This task is the only thing standing between the picker and a user selecting the radio their entire Thread network depends on.** Selecting it would take down every Thread device in the house. Treat the exclusion code below as the load-bearing part of this task, not as validation boilerplate.

**This task also closes the second half of that same collision: which RF channel Zigbee forms its network on.** Task 7 built `ZigbeeSource(thread_channel=...)` and `channels_excluding(thread_channel)` to keep a new Zigbee network off whatever channel OTBR's active dataset already occupies — the two share the 2.4 GHz band, and the border router usually sits on the same host. `thread_channel` defaults to `None`, and until now nothing ever passed anything else: `channels_excluding`'s own docstring names this as "outstanding debt for Tasks 10 and 11" and says exactly where the fix belongs — "the caller that already knows about OTBR", which must not become an HTTP call inside `connect()` (that runs on every supervisor retry, not once). This task is that caller, and not Task 10, for two reasons. First, `matter/otbr.py` is the module that already owns every OTBR read in this codebase, and this task is the one that touches Thread exclusion at all — Task 10 wires the heartbeat and never otherwise mentions OTBR. Second, and more concretely: the fetch belongs beside the single place a `ZigbeeSource` is actually built, and that place is `ZigbeeRuntime`'s builder, which this task creates and into which it moves Task 10's temporary `_build_zigbee_source` (see the note below) — fetching once per *build* rather than once per *connect attempt* is exactly the trade Task 7 already made for `ensure_quirks_loaded()`. Task 10's own temporary builder is allowed to leave `thread_channel` at its default, the same way it is allowed to read the radio path from a throwaway CLI flag: both are superseded the moment this task's builder replaces them, and nothing ships in between.

**Files:**
- Create: `src/loxmatter/model/zigbee_settings_store.py`, `src/loxmatter/zigbee/runtime.py`, `src/loxmatter/api/zigbee.py`, `tests/model/test_zigbee_settings_store.py`, `tests/zigbee/test_zigbee_runtime.py`, `tests/api/test_zigbee_api.py`
- Modify: `src/loxmatter/radios/inventory.py`, `src/loxmatter/model/store.py`, `src/loxmatter/loxone/server.py`, `src/loxmatter/cli.py`, `src/loxmatter/matter/otbr.py`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/radios/test_inventory.py`, `tests/matter/test_otbr.py`

**Interfaces:**
- Consumes: `Fingerprint`/`match_fingerprint`/`DEFAULT_UNKNOWN` (Task 4), `ZigbeeSource`, `ConnectionProgress` and `channels_excluding`/`thread_channel` (Task 7), `Sources.replace` (Task 5), `supervise` (`sources/supervisor.py`, unchanged), `match_current_device`/`scan_serial` (`radios/inventory.py`, 2a-1), `RadioConfig.thread_device` (2a-1), `fetch_active_dataset`/`ThreadDatasetUnavailableError` (`matter/otbr.py`, pre-existing).
- Produces:
  - `loxmatter.zigbee.runtime.ZigbeeRuntime` — owns the live source and its supervisor task; `current()`, `progress()`, and a **synchronous** `apply(settings)` that schedules the change and returns at once. Consumed by `api/zigbee.py` and constructed in `cli._run`, which also passes it to `build_app`.
  - `loxmatter.radios.inventory.device_identity(path: str, host_dev: Path, *, stat: Callable[[str], os.stat_result] = os.stat) -> tuple[int, int] | None` — the resolved `(major, minor)` of a character device, or `None`.
  - `loxmatter.radios.inventory.is_same_device(left: str | None, right: str | None, host_dev: Path, *, stat=os.stat) -> bool`
  - `loxmatter.api.zigbee._thread_stick(update_dir, serial) -> tuple[str | None, bool]` — the by-id path of the stick Thread is configured on, and whether Thread is actually using it.
  - `loxmatter.api.zigbee._is_thread_stick(radio_path, thread_device, thread_in_use, host_dev) -> bool` — the exclusion decision, by resolved major:minor.
  - Each entry of `GET /api/zigbee/radio`'s `serial` list carries `is_thread` and `selectable`; Task 13 reads both under those names.
  - `ZigbeeRadioSettings(path: str | None, radio_type: str, baudrate: int, flow_control: str, saved_at: str | None)` and `store.zigbee_settings` with `get()` / `save(...)` / `clear()`.
  - `GET /api/zigbee/radio`, returning the detected sticks, `configured_path`, `configured_device_present` and `progress`; and `PUT /api/zigbee/radio`, answering **202**.
  - `loxmatter.matter.otbr.thread_channel_from_dataset(dataset: str) -> int | None` — the 2.4 GHz channel a hex TLV active dataset names, or `None` when there is no Channel TLV, or a malformed one, to be found.
  - `loxmatter.matter.otbr.current_thread_channel(base_url=None, *, session_factory=None) -> int | None` — fetches and parses in one call; `None` for every reason OTBR cannot answer (absent, unreachable, no usable dataset), never an exception. This is what `ZigbeeRuntime`'s builder calls, once per build, to fill `ZigbeeSource(thread_channel=...)`.

Note on Task 10's `_build_zigbee_source(store)`: `ZigbeeRuntime` is where that helper belongs once this task lands. Move it rather than leaving two places that construct a `ZigbeeSource` — `cli._run` then asks the holder for the startup source, and the startup path and the apply path build it identically. That is the same reasoning `sources/supervisor.py`'s docstring gives for `attach()` covering both startup and reconnect: two code paths that must do the same thing will drift, and the drift only shows up when something is missing after the one that runs less often. **The move carries the Thread-channel fetch with it** (see above and Step 6) — a helper moved without it would silently restore the gap this task exists to close.

- [ ] **Step 1: Write the failing exclusion tests** in `tests/radios/test_inventory.py`:

```python
def test_the_thread_stick_is_recognised_through_every_name_it_has():
    """THE most dangerous mistake this feature can make: opening the
    maintainer's live Thread coordinator as a Zigbee radio garbles their
    Thread network.

    Comparing PATH STRINGS does not work, and the reason is concrete: the
    Pi's `.env` still holds `/dev/ttyUSB0` while this card offers by-id
    paths, and the bridge sees the same node under yet another prefix
    because /dev is bind-mounted at /host/dev. Three different strings, one
    physical stick. The resolved major:minor is the same for all three.

    Fault to prove it: compare the path strings. The by-id/ttyUSB0 pair then
    slips through and the Thread stick is offered as a Zigbee coordinator."""
    stats = {
        "/host/dev/serial/by-id/usb-SONOFF_MG24-if00": _char_device(188, 0),
        "/host/dev/ttyUSB0": _char_device(188, 0),
        "/host/dev/ttyUSB1": _char_device(188, 1),
    }
    fake_stat = stats.__getitem__

    assert (
        is_same_device(
            "/dev/serial/by-id/usb-SONOFF_MG24-if00",
            "/dev/ttyUSB0",
            Path("/host/dev"),
            stat=fake_stat,
        )
        is True
    )
    assert (
        is_same_device(
            "/dev/serial/by-id/usb-SONOFF_MG24-if00",
            "/dev/ttyUSB1",
            Path("/host/dev"),
            stat=fake_stat,
        )
        is False
    )


def test_an_unresolvable_device_is_not_treated_as_a_match():
    """A stick that is not there cannot be proven to be a different one, but
    it also must not be proven to be the SAME one - `None` is not equal to
    `None` here.

    Fault to prove it: return `True` when both resolve to `None`. Every
    absent path then counts as the Thread stick and nothing is selectable."""
```

Note for the implementer: `_char_device(major, minor)` builds an object with an `st_mode` that `stat.S_ISCHR` accepts and an `st_rdev` of `os.makedev(major, minor)`. A test cannot create a real device node without root, which is exactly why `stat` is injectable — and the injection point is the honest way to test this, not a shortcut around it.

**Also write the failing Thread-channel tests**, in `tests/matter/test_otbr.py`, alongside the existing `fetch_active_dataset` suite — read that file first for `FakeSession`/`FakeResponse`, which these reuse unchanged:

```python
# A well-formed active dataset carrying two TLVs: an Active Timestamp
# (type 0x0e, ignored by this parser) ahead of a Channel TLV (type 0x00,
# length 3: one byte of channel page, then the channel itself as a 2-byte
# big-endian integer - MeshCoP TLV numbering, Thread 1.3 "Network
# Management TLVs"). Not first in the stream on purpose: a parser that
# assumed the Channel TLV came first would pass against a fixture shaped
# like this one and fail against a real border router that orders its TLVs
# differently.
DATASET_ON_CHANNEL_15 = "0e08" + "00" * 8 + "000300000f"


def test_the_channel_tlv_is_found_regardless_of_where_it_sits_in_the_dataset():
    """MeshCoP TLVs are a flat, ordered stream with no fixed layout beyond
    "type, length, value, repeat" - OTBR is free to write them in any
    order.

    Fault to prove it: read the first three value bytes of the dataset as
    the Channel TLV unconditionally, instead of scanning for type `0x00`.
    This test's fixture, with the Channel TLV second, then reads as channel
    0 - or raises, depending on how the first TLV's bytes are misread."""
    assert thread_channel_from_dataset(DATASET_ON_CHANNEL_15) == 15


def test_a_dataset_with_no_channel_tlv_answers_none_not_an_error():
    """Every reason this parser cannot name a channel must read exactly
    like "no border router at all" to `channels_excluding` - a courtesy
    lost is not a reason to stop Zigbee from forming.

    Fault to prove it: raise instead of returning `None` when the stream
    runs out without a type-`0x00` TLV. Building a `ZigbeeSource` then
    fails outright against a real, valid dataset that simply omits the
    Channel TLV (permitted by the TLV format itself)."""
    assert thread_channel_from_dataset("0e08" + "00" * 8) is None


def test_a_truncated_tlv_stream_answers_none_rather_than_indexing_past_the_end():
    """The shape of a response cut off mid-transfer, or simply corrupt: a
    length byte claiming more value bytes than remain in the string.

    Fault to prove it: slice the value out of the stream without first
    checking that the claimed length fits. This test then fails with an
    `IndexError`/`ValueError` instead of reading `None` - turning a
    malformed dataset into a crash on the path that builds every
    `ZigbeeSource`."""
    assert thread_channel_from_dataset("0e08" + "00" * 2) is None


async def test_current_thread_channel_answers_none_when_the_border_router_is_absent():
    """The common case on any bridge with no Thread border router at all:
    `fetch_active_dataset` raises `ThreadDatasetUnavailableError` the
    instant the connection is refused. That must read as "nothing to
    avoid", never propagate - a missing OPTIONAL border router must not
    stop `ZigbeeRuntime` from building a `ZigbeeSource` at all, the same
    rule Task 10 already applies to a missing Zigbee radio itself.

    Fault to prove it: let `ThreadDatasetUnavailableError` escape instead of
    catching it. Building a `ZigbeeSource` then fails on every installation
    without a Thread border router configured - most of them."""
    session = FakeSession()
    session.raise_on_get = OSError("Connection refused")
    assert await current_thread_channel(session_factory=lambda: session) is None


async def test_current_thread_channel_reads_the_real_fetch_and_parse_path():
    """The end-to-end call `ZigbeeRuntime`'s builder actually makes: fetch,
    then parse, through the same fake session `fetch_active_dataset`'s own
    tests already use.

    Fault to prove it: call `thread_channel_from_dataset` on something other
    than `fetch_active_dataset`'s return value (for example, the raw,
    unvalidated response body). A border router that pads its response
    with trailing whitespace - which `fetch_active_dataset` already strips
    via `validated_dataset` - would then read as `None` instead of the real
    channel, silently losing the exclusion on hardware that works fine."""
    session = FakeSession(status=200, body=DATASET_ON_CHANNEL_15)
    assert await current_thread_channel(session_factory=lambda: session) == 15
```

- [ ] **Step 2: Write the failing API tests** `tests/api/test_zigbee_api.py`, using the `_host(tmp_path)` tree and heartbeat helpers `tests/api/test_radios_api.py` already builds (import them or copy their shape — read that file first).

**The fixture this file needs, and why it is not the one next door.** `tests/api/test_radios_api.py::_host` builds a tree with **one** stick (its `SONOFF` constant — which, now that the hardware has been read, is the maintainer's **Thread** stick). This suite needs **both** sticks, because the entire question is which of two is offered. Build `_host_two_sticks(tmp_path)` in the new file on the same shape, with these values measured on the Pi on 12 September 2026:

```python
# Verbatim from `ls -l /dev/serial/by-id/` on the maintainer's Pi.
REAL_ITEAD = (
    "usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf16ad5953ef11844a28e0174bec31-if00-port0"
)
REAL_MG24 = "usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a7d9118f9ef118f7767135c2a50c9-if00-port0"


def _host_two_sticks(tmp_path: Path) -> tuple[Path, Path]:
    """The maintainer's real Pi, as a directory tree.

    Both sticks are `10c4:ea60` and both are major 188 - minors 0 and 1 -
    because that is what the hardware reports. A fixture that gave them
    different vendor ids, or different majors, would let a matcher keyed on
    either of those pass while failing on the real machine.
    """
    host_dev, sys_root = tmp_path / "dev", tmp_path / "sys"
    (host_dev / "serial" / "by-id").mkdir(parents=True)
    for tty, by_id, minor in (
        ("ttyUSB0", REAL_MG24, 0),  # the THREAD stick - RADIO_DEVICE names it
        ("ttyUSB1", REAL_ITEAD, 1),  # the Zigbee coordinator
    ):
        node = host_dev / tty
        node.write_text("", encoding="utf-8")
        (host_dev / "serial" / "by-id" / by_id).symlink_to(Path("../..") / tty)
        usb = sys_root / "devices" / "usb1" / f"1-1.{minor}"
        (usb / f"1-1.{minor}:1.0" / tty).mkdir(parents=True)
        (usb / "idVendor").write_text("10c4\n", encoding="utf-8")
        (usb / "idProduct").write_text("ea60\n", encoding="utf-8")
        (sys_root / "class" / "tty" / tty).mkdir(parents=True)
        (sys_root / "class" / "tty" / tty / "device").symlink_to(usb / f"1-1.{minor}:1.0" / tty)
    (sys_root / "class" / "bluetooth" / "hci0").mkdir(parents=True)
    return host_dev, sys_root


def _current(**fields: object) -> dict[str, object]:
    """The sidecar's reported state, as the live Pi reports it: Thread on,
    running on ttyUSB0, which is the MG24."""
    body = {
        "thread_enabled": True,
        "thread_device": "/dev/ttyUSB0",
        "thread_device_present": True,
        "bluetooth_adapter": 0,
        "otbr_running": True,
    }
    body.update(fields)
    return body
```

Note the `thread_device` value: **`/dev/ttyUSB0`, not a by-id path.** That is what the installer actually writes, and it is the whole reason the comparison must resolve to major:minor. A fixture that stored the by-id path here would let a string compare pass.

Because a test cannot create real device nodes without root, `is_same_device`'s injected `stat` is what makes the resolution testable — map both `/host/dev/ttyUSB0` and `/host/dev/serial/by-id/<REAL_MG24>` onto `_char_device(188, 0)` and the ITEAD pair onto `_char_device(188, 1)`, exactly as Step 1 does.

The tests:

```python
async def test_the_thread_stick_is_refused_by_the_api_not_only_hidden_by_the_card():
    """The UI filter is a courtesy; the server check is the guarantee. A
    `PUT` naming the Thread device must be refused even though the card
    never offers it - a stale page, a second tab or a curl call must not be
    able to point zigpy at the border router's radio.

    Fault to prove it: drop the server-side check and rely on the card."""
    response = await api.put("/api/zigbee/radio", json={"path": THREAD_BY_ID})
    assert response.status_code == 400
    assert "thread" in response.json()["detail"].lower()


async def test_the_two_sticks_on_the_maintainers_pi_end_up_on_opposite_sides(api):
    """THE REAL INSTALLATION, PINNED (measured 12 September 2026).

    `ls -l /dev/serial/by-id/` on the maintainer's Pi returns exactly two
    entries, and `RADIO_DEVICE=/dev/ttyUSB0` in the live stack names the
    MG24. Both sticks report `10c4:ea60` and both are major 188, so neither
    the USB ids nor the major can separate them - only the resolved minor
    and the by-id name can.

    The ITEAD stick must be OFFERED and the MG24 must be REFUSED. If this
    ever comes out the other way round on real hardware, the picker hands
    the user the radio their Thread border router is running on, and
    selecting it takes down every Thread device in the house.

    Fault to prove it: compare path strings instead of resolved major:minor.
    `/dev/ttyUSB0` and the MG24's by-id path are different strings, so the
    MG24 becomes selectable and the fault is invisible until someone picks
    it."""
    client, update_dir, _ = api
    body = (await client.get("/api/zigbee/radio")).json()
    by_path = {stick["path"]: stick for stick in body["serial"]}
    itead = by_path[f"/dev/serial/by-id/{REAL_ITEAD}"]
    mg24 = by_path[f"/dev/serial/by-id/{REAL_MG24}"]

    assert (itead["is_thread"], itead["selectable"]) == (False, True)
    assert (mg24["is_thread"], mg24["selectable"]) == (True, False)

    assert (await client.put("/api/zigbee/radio", json={"path": itead["path"]})).status_code == 202
    refused = await client.put("/api/zigbee/radio", json={"path": mg24["path"]})
    assert refused.status_code == 400
    assert "thread" in refused.json()["detail"].lower()


async def test_a_stick_freed_by_turning_thread_off_becomes_selectable_again(api):
    """The escape hatch, and the reason the refusal is gated on
    `thread_enabled` rather than on `RADIO_DEVICE` alone.

    MEASURED in `deploy/updater/radios-once.sh`: the `down` path rewrites
    `COMPOSE_PROFILES` and LEAVES `RADIO_DEVICE` naming the stick. A refusal
    keyed on the stored device alone would therefore be permanent - the user
    who disables Thread specifically in order to repurpose their MG24 would
    find it greyed out forever, under a message telling them to release it
    in a row where they already have. An MG24 is dual-capable hardware and
    this is the only legitimate way to move it across.

    Fault to prove it: ignore `thread_enabled` and refuse whenever the path
    resolves to `RADIO_DEVICE`. The second half of this test then fails
    while the first half still passes."""
    client, update_dir, _ = api
    mg24 = f"/dev/serial/by-id/{REAL_MG24}"
    assert (await client.put("/api/zigbee/radio", json={"path": mg24})).status_code == 400

    _radios_heartbeat(
        update_dir, current={**_current(), "thread_enabled": False, "otbr_running": False}
    )
    body = (await client.get("/api/zigbee/radio")).json()
    freed = next(s for s in body["serial"] if s["path"] == mg24)
    assert (freed["is_thread"], freed["selectable"]) == (False, True)
    assert (await client.put("/api/zigbee/radio", json={"path": mg24})).status_code == 202


async def test_choosing_a_stick_does_not_touch_thread_or_bluetooth():
    """The null-half guarantee 2a-1 built, honoured completely: this setting
    never reaches `radios-request.json` at all, so there is no half that
    could be misread as "Thread off". No request file is written and no
    container is recreated.

    Fault to prove it: route the change through `request_radios`."""
    await api.put("/api/zigbee/radio", json={"path": SONOFF_BY_ID})
    assert not (update_dir / "radios-request.json").exists()


async def test_the_setting_survives_a_restart():
    """It lives in loxmatter's own `setting` table, so a container
    recreation - an ordinary update - keeps it.

    Fault to prove it: hold it on the source object in memory."""


async def test_an_unknown_stick_is_accepted_with_an_explicit_radio_type():
    """Refusing to work with an unlisted stick would be worse than letting
    the user say what it is (design section 7). The Advanced disclosure
    supplies radio type and baud rate; the fingerprint supplies them when it
    can.

    Fault to prove it: reject a path with no fingerprint match."""


async def test_a_path_that_is_not_a_detected_stick_is_refused():
    """Same rule `POST /api/radios` already applies to the Thread device:
    the bridge validates what it can see.

    Fault to prove it: accept any string. A typo then becomes a zigpy
    startup failure with a confusing message instead of a 400."""


async def test_clearing_the_setting_disconnects_the_source():
    """ "No Zigbee stick" must actually release the port - otherwise the
    stick stays locked by this process and a second loxmatter instance, or
    a deliberate switch to another tool, fails with EBUSY for no visible
    reason.

    Fault to prove it: only clear the stored value."""


async def test_applying_a_new_stick_answers_at_once_and_reconnects_in_the_background():
    """The point of Spec Correction 3: no container is recreated, so the
    Matter link and every Matter device are untouched, and the request that
    asked for the change survives to answer.

    It answers 202 WITHOUT waiting for the radio. A first-ever Zigbee
    configuration is a 9-15 s quirks warm-up on a Pi, plus
    `startup(auto_form=True)`, plus a possible 7.5 s timeout on a silent
    port - all of which would otherwise sit on this request with nothing on
    screen moving. The reconnection runs in the background and its progress
    is readable from `GET /api/zigbee/radio`.

    Fault to prove it: `await` the reconnection inside the handler. The
    test's fake source blocks for longer than the request's own timeout, so
    the PUT never returns - which is precisely what a Pi user would see."""


async def test_the_apply_request_does_not_wait_for_the_quirks_warm_up():
    """The Global Constraint, stated three times in this plan: the warm-up
    never sits on a request path. This is the one route that could put it
    there, because it is the only one that can cause a first-ever connect.

    Fault to prove it: call `ensure_quirks_loaded()` from the handler, or
    await `source.connect()` there - either puts the whole cost on the
    request."""


async def test_the_progress_of_a_running_attempt_is_readable():
    """What makes the asynchronous answer honest rather than merely fast: a
    PUT that returns immediately and reports nothing afterwards is a job the
    user cannot distinguish from a dead one, which is the 2a-1 lesson this
    plan records as a Global Constraint.

    Fault to prove it: return a bare `connected` boolean. A 15 s warm-up
    then reads as "not connected", identical to a stick that is broken."""


async def test_a_configured_stick_that_is_gone_is_reported_as_missing():
    """The in-process design's worst failure mode, and the one nothing else
    surfaces: the stored by-id path names a node that will never come back -
    the stick was pulled, or it came back under a different name - so the
    supervisor retries forever on its 60 s ceiling and the card says only
    "not connected". The user has no way to learn that the device they
    chose is simply absent.

    Sub-project 2a-1 solved exactly this for Thread: `GET /api/radios`
    returns `thread_device_present`, computed by `match_current_device`
    against the live scan. This is the same answer for the same question.

    Fault to prove it: report only the stored path. A stick that was
    unplugged is then indistinguishable from one that is present and
    refusing to open, and the two need opposite actions from the user."""
```

- [ ] **Step 3: Run all three test files to verify they fail** (`tests/radios/test_inventory.py`, `tests/api/test_zigbee_api.py`, `tests/matter/test_otbr.py`).

- [ ] **Step 4: Implement `device_identity` / `is_same_device`** in `src/loxmatter/radios/inventory.py`, mapping a host-visible `/dev/...` path onto the container's `host_dev` prefix the way `radios-once.sh` already does (`"$HOST_DEV${WANT_DEVICE#/dev}"`), resolving symlinks, and returning `None` for anything that is not a character device.

- [ ] **Step 5: Implement `ZigbeeSettingsStore`** on the `ResendSettingsStore` pattern — another view onto the same connection, its own module, keys `zigbee_path`, `zigbee_radio_type`, `zigbee_baudrate`, `zigbee_flow_control`, `zigbee_settings_saved_at`.

- [ ] **Step 6: Implement the holder and the router.**

**Why the apply is asynchronous, and why a synchronous version was rejected.** The obvious shape — `PUT` persists the setting, awaits `source.connect()`, and answers with the result — was written into an earlier draft of this plan and is wrong. On a first-ever Zigbee configuration that handler awaits an estimated **9-15 s** of `zhaquirks.setup()` on a Pi 4, then `startup(auto_form=True)`, then, if the stick is silent or is not a coordinator, a further **7.5 s** before `TimeoutError`. That is up to half a minute of a blocked HTTP request with nothing on screen moving, and it contradicts two of this plan's own Global Constraints at once: quirks loading must never sit on a request path, and long-running work must show progress or the user reads a healthy operation as a dead one. The supervisor Task 10 starts does not save it either — that one exists only for a radio that is **already** configured, which is precisely not this case.

So the `PUT` stores the setting and returns **202** at once, and the connection happens in the background where the user can watch it. The mechanism is the one the repository already has, twice over: `sources/supervisor.py` owns connection attempts with 1 s → 60 s backoff, and the radios card polls a status endpoint while a job runs. Nothing new is invented.

This also makes the Zigbee flow resemble the Thread flow, which is what the maintainer asked for — it should barely matter to the user which kind of device they attach.

**`ZigbeeRuntime`, the holder.** `build_app` builds routers before `_run` owns any source, so the router cannot be handed the source itself. It is handed this instead, constructed in `_run` and closed over by both:

```python
class ZigbeeRuntime:
    """Owns the live Zigbee source and the task supervising it.

    One object rather than a bare callable because applying a radio change
    means three things at once - swap the source in the registry, stop the
    old supervisor, start a new one - and doing two of the three is worse
    than doing none.
    """

    def __init__(self, store, runtime, sources, *, build_source, supervise=supervise): ...

    def current(self) -> ZigbeeSource | None: ...

    def progress(self) -> ConnectionProgress: ...

    def apply(self, settings: ZigbeeRadioSettings) -> None:
        """Schedules the change. Returns IMMEDIATELY - see above.

        Not `async`: the caller is an HTTP handler that must not await any
        part of this, and a coroutine would invite exactly that mistake.
        The work goes to a task held on `self` so it cannot be garbage
        collected mid-flight (the `_pulse_tasks` pattern in
        `loxone/runtime.py`).
        """
```

`_apply_in_background` does, in order: cancel the old supervisor task and `await old.disconnect()` (so the port is genuinely released — `test_clearing_the_setting_disconnects_the_source`); `sources.replace("zigbee", None)`; and then, if the new settings name a path, build a fresh `ZigbeeSource`, `sources.replace("zigbee", new)`, and `ensure_future(supervise(new, store, runtime))`.

Starting the supervisor is all it takes to connect, and that is the point of reusing it: `supervise()` opens with `await source.wait_for_link_loss()`, which Task 7 requires to return **at once** for a source that was never connected, so the supervisor falls straight into its own connect-and-back-off loop and performs the first attempt itself. There is no second connection path, no bespoke retry, and a stick that is missing at apply time is retried on the same 1 s → 60 s schedule as one that dies an hour later.

**First, implement the Thread-channel reader in `src/loxmatter/matter/otbr.py`.** `matter/otbr.py` is the only module in the tree that reads OTBR at all, and `fetch_active_dataset` already returns the active dataset as a hex TLV blob — nothing parses a channel out of it yet. Add these two functions beside it (no new imports: `Final` and `Callable` are already imported there):

```python
# MeshCoP TLV type for the Channel TLV (Thread 1.3 specification, "Network
# Management TLVs" table): one byte of channel page followed by the
# channel itself as a 2-byte big-endian integer - three value bytes in
# total. Channel page 0 is the 2.4 GHz band, the one Zigbee also uses.
_CHANNEL_TLV_TYPE: Final = 0x00
_CHANNEL_TLV_VALUE_LENGTH: Final = 3


def thread_channel_from_dataset(dataset: str) -> int | None:
    """The 2.4 GHz channel a hex TLV active dataset names, or `None`.

    `None` covers every shape this must tolerate without raising: no
    Channel TLV present, one with the wrong length, or a stream truncated
    partway through a type/length pair. This function is a courtesy - one
    Zigbee/Thread channel collision avoided - never a gate, so a dataset it
    cannot make sense of must read exactly like no border router at all
    (`channels_excluding(None)`), not like an error that stops a
    `ZigbeeSource` from being built at all.

    `dataset` is a credential-bearing blob (see `fetch_active_dataset`);
    this function never logs it or any slice of it, only the channel
    number it found.

    Two fail-safe gaps, both measured, both deliberately left as notes
    rather than code - an Active Operational Dataset fits in 254 bytes, so
    neither can arise from a real border router:

    - **The channel PAGE byte is skipped, not checked.** Page 0 is the
      2.4 GHz band Zigbee shares; page 23 is the 915 MHz band, whose
      channel numbers run from 0 and therefore overlap Zigbee's
      candidates. A page-23 dataset naming channel 11 (`"000317000b"`)
      returns 11 here, and `channels_excluding` then drops 2.4 GHz
      channel 11 to avoid a network that is not on it. The cost is one
      candidate needlessly removed from a list of four, never a wrong
      network: this function only ever shortens that list, and
      `channels_excluding` refuses nothing.
    - **Thread's extended-TLV escape derails the scan.** A length byte of
      `0xFF` means "two more bytes of extended length follow"; this parser
      reads it as a 255-byte value, walks past the Channel TLV, and
      returns `None` - the exclusion is lost, not wrong, which is the same
      answer as no border router at all.
    """
    try:
        raw = bytes.fromhex(dataset)
    except ValueError:
        return None
    index = 0
    while index + 2 <= len(raw):
        tlv_type = raw[index]
        length = raw[index + 1]
        value_start = index + 2
        value_end = value_start + length
        if value_end > len(raw):
            return None
        if tlv_type == _CHANNEL_TLV_TYPE and length == _CHANNEL_TLV_VALUE_LENGTH:
            return int.from_bytes(raw[value_start + 1 : value_end], "big")
        index = value_end
    return None


async def current_thread_channel(
    base_url: str | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> int | None:
    """The channel Zigbee should avoid, or `None` when there is nothing to
    avoid.

    Every reason this can fail to answer - no border router, an
    unreachable one, a dataset with no Channel TLV - reads the same way to
    the caller: form the Zigbee network on the full channel list
    (`channels_excluding(None)`). A missing OPTIONAL border router must
    never stop Zigbee from forming, the same rule Task 10 already applies
    to a missing Zigbee radio itself.
    """
    try:
        dataset = await fetch_active_dataset(base_url, session_factory=session_factory)
    except ThreadDatasetUnavailableError:
        return None
    return thread_channel_from_dataset(dataset)
```

**Then, "build a fresh `ZigbeeSource`" above means calling `build_source(settings)` — `ZigbeeRuntime`'s injected constructor argument, and the one and only place in the whole tree that calls `current_thread_channel`.** Its production implementation, `build_zigbee_source` in `zigbee/runtime.py`, is what Task 10's temporary `_build_zigbee_source(store)` is replaced by:

```python
async def build_zigbee_source(
    settings: ZigbeeRadioSettings,
    *,
    database: Path,
    on_connection_change: Callable[[bool], Awaitable[None]] | None,
) -> ZigbeeSource | None:
    """The one place a `ZigbeeSource` is built - at startup and on every
    radio change alike, now that `ZigbeeRuntime` owns both. Two call sites
    that built it separately were exactly the drift
    `sources/supervisor.py`'s own docstring warns about for `attach()`, so
    there is only one left.

    Fetches the Thread channel to avoid HERE, once per build, rather than
    inside `ZigbeeSource.connect()`. `channels_excluding`'s own docstring
    names this call as its outstanding debt and says where it belongs: the
    caller that already knows about OTBR, which must not become an HTTP
    call inside `connect()` - that method runs on every one of the
    supervisor's 1 s -> 60 s retries, not once. Building a new
    `ZigbeeSource` happens far less often - once at startup, once per radio
    change - so paying for the fetch here is the same trade Task 7 already
    made for `ensure_quirks_loaded()`.
    """
    if settings.path is None:
        return None
    fingerprint = Fingerprint(
        name="",
        radio_type=settings.radio_type,
        baudrate=settings.baudrate,
        flow_control=settings.flow_control,
    )
    channel = await current_thread_channel()
    return ZigbeeSource(
        path=settings.path,
        fingerprint=fingerprint,
        database=database,
        on_connection_change=on_connection_change,
        thread_channel=channel,
    )
```

`cli._run` passes `functools.partial(build_zigbee_source, database=matter_data_dir / "zigbee.sqlite", on_connection_change=runtime.set_zigbee_connected)` as `ZigbeeRuntime`'s `build_source` — `database` and `on_connection_change` never change between a startup build and an apply-time rebuild, so binding them once here is what keeps `build_source(settings)` a one-argument callable both `ZigbeeRuntime.__init__` (for the very first source) and `_apply_in_background` (for every one after) can call identically. A test exercising `ZigbeeRuntime` injects its own `build_source` and never touches OTBR at all — the fetch is this function's concern alone, which is exactly why it is a function, not a method inlined into `_apply_in_background`.

**The router**, `build_zigbee_router(store, *, zigbee_runtime, host_dev, sys_root, update_dir)`:

**The two module-level helpers that decide the exclusion.** These are the load-bearing part of the task; write them exactly:

```python
def _thread_stick(update_dir: Path, serial: Sequence[SerialRadio]) -> tuple[str | None, bool]:
    """Which stick this installation is CURRENTLY using for Thread.

    Returns `(by-id path or None, in_use)`. The path comes from the same
    place `GET /api/radios` reads it: the sidecar's reported
    `RadioConfig.thread_device`, mapped onto a by-id path by
    `match_current_device` because the installer writes `/dev/ttyUSB0`
    while this card speaks by-id.

    `in_use` is gated on `thread_enabled` (OR `otbr_running`, which is the
    independently observed fact beside it), and that gate is deliberate.
    MEASURED in `deploy/updater/radios-once.sh`: the `down` path rewrites
    `COMPOSE_PROFILES` and LEAVES `RADIO_DEVICE` naming the stick. So "is
    this the stored Thread device" is NOT the same question as "is Thread
    using it", and answering only the first would permanently strand the
    user who disables Thread in order to repurpose a dual-capable stick -
    the only legitimate way to move an MG24 across.

    No state, or no reported current, means nothing is known to be using
    anything: `(None, False)`. That is not a licence to open a stick
    blindly - it is the same "the bridge validates what it can see"
    position `POST /api/radios` already takes.
    """
    state = read_radios_state(update_dir)
    if state is None or state.current is None:
        return None, False
    device, _present = match_current_device(state.current.thread_device, serial)
    return device, bool(state.current.thread_enabled or state.current.otbr_running)


def _is_thread_stick(
    radio_path: str, thread_device: str | None, thread_in_use: bool, host_dev: Path
) -> bool:
    """Whether this stick is the one Thread is running on.

    By RESOLVED major:minor, never by string compare. The same physical
    stick is `/dev/ttyUSB0` in `.env`, a by-id path on this card, and a
    third name under the container's `/host/dev` mount - three strings, one
    piece of hardware. MEASURED on the Pi: the two attached sticks are
    major 188 minors 0 and 1 and share the vendor id `10c4:ea60`, so the
    resolved minor is the only thing that separates them.
    """
    if thread_device is None or not thread_in_use:
        return False
    return is_same_device(radio_path, thread_device, host_dev)
```

**The two routes.** Shown with the enclosing `def` so the block parses standalone and the indentation is the real one:

```python
def build_zigbee_router(store, *, zigbee_runtime, host_dev, sys_root, update_dir) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/zigbee/radio")
    async def get_zigbee_radio() -> dict[str, object]:
        serial = scan_serial(host_dev, sys_root)
        thread_device, thread_in_use = _thread_stick(update_dir, serial)
        stored = store.zigbee_settings.get()
        sticks: list[dict[str, object]] = []
        for radio in serial:
            fingerprint = match_fingerprint(radio)
            is_thread = _is_thread_stick(radio.path, thread_device, thread_in_use, host_dev)
            sticks.append(
                {
                    "path": radio.path,
                    "product": radio.product,
                    "fingerprint": None if fingerprint is None else asdict(fingerprint),
                    # LISTED WITH A REASON, never filtered out. A stick that
                    # is simply absent from this list reads as a detection
                    # bug to the user, who can see it in the Thread row two
                    # rows above. `selectable` is a separate key rather than
                    # `not is_thread` computed in the page, so the card
                    # cannot drift from the server's own rule - and so that
                    # a future second reason to refuse a stick has somewhere
                    # to live.
                    "is_thread": is_thread,
                    "selectable": not is_thread,
                }
            )
        _resolved, present = match_current_device(stored.path, serial)
        return {
            "serial": sticks,
            "configured_path": stored.path,
            # Whether the stored stick is ACTUALLY THERE, resolved through
            # the live scan exactly as `GET /api/radios` does for Thread
            # (`match_current_device` -> `thread_device_present`). Without
            # it, a stored by-id path naming a node that will never return
            # is indistinguishable from a stick that is present and
            # refusing to open - and the supervisor retries the first case
            # forever while the card says only "not connected". A
            # `configured_path` of `None` reports `False` without being an
            # error, because nothing is configured.
            "configured_device_present": present,
            "progress": asdict(zigbee_runtime.progress()),
        }

    @router.put("/zigbee/radio", status_code=202)
    async def put_zigbee_radio(body: ZigbeeRadioIn) -> dict[str, object]:
        serial = scan_serial(host_dev, sys_root)
        if body.path is not None:
            radio = next((item for item in serial if item.path == body.path), None)
            if radio is None:
                raise HTTPException(
                    status_code=400, detail=i18n.t("api.errors.zigbee_unknown_device")
                )
            thread_device, thread_in_use = _thread_stick(update_dir, serial)
            # THE most dangerous request this API can be sent, and the
            # reason it is checked here and not only in the page: the card
            # disables the option, but a stale tab, a second browser or a
            # curl call must not be able to point zigpy at the radio a live
            # Thread border router is running on. The card is a courtesy;
            # this is the guarantee.
            if _is_thread_stick(radio.path, thread_device, thread_in_use, host_dev):
                raise HTTPException(
                    status_code=400, detail=i18n.t("api.errors.zigbee_is_thread_stick")
                )
        settings = _settings_from(body, serial)
        store.zigbee_settings.save(settings)
        zigbee_runtime.apply(settings)
        return {"progress": asdict(zigbee_runtime.progress())}

    return router
```

`_settings_from(body, serial)` fills radio type, baud rate and flow control from `match_fingerprint(radio)` when the table recognises the stick, and from the request's own Advanced fields when it does not — falling back to `DEFAULT_UNKNOWN`'s values, never to a guess presented as a detection. A `path` of `None` is "no Zigbee stick" and is always accepted: clearing the setting can never be refused, or a user whose stick has been reassigned to Thread could not get out of the conflict. It awaits nothing to do with the radio, and every rejection detail goes through `i18n.t`.

The card polls `GET /api/zigbee/radio` every 2 s while `progress.state` is `"loading_quirks"` or `"opening_radio"`, exactly as `loadRadios()` polls while a sidecar job runs, and stops when the state reaches `"connected"` or `"failed"`. `"failed"` is not terminal for the supervisor — it keeps retrying — so the card shows the error together with the attempt count and keeps polling at a slower cadence rather than claiming the change is over.

Interruption recovery needs nothing extra here, and that is worth stating because this plan requires it of anything that writes durable state: the stored setting **is** the recovery record. A bridge killed mid-apply starts up, reads the setting, and Task 10's startup path connects to it — the same place it would have ended up. There is no non-terminal phase that can freeze, which is the failure the radios sidecar had.

- [ ] **Step 7: Add the i18n keys** (`en` + `de`): `web.radios.zigbee_label`, `web.radios.zigbee_none`, `web.radios.fingerprint_unknown`, `web.radios.advanced`, `api.errors.zigbee_unknown_device`, `api.errors.zigbee_not_configured`.

The two that carry the refusal are written out here, because their wording is the whole user-facing behaviour of this task and a vaguer sentence would leave the user stuck. They must say **what** is wrong, **why**, and **what to do about it** — never just "invalid device":

```yaml
api.errors.zigbee_is_thread_stick:
  en: "This stick is in use for Thread. Turn Thread off, or move it to another stick, before using this one for Zigbee."
  de: "Dieser Stick wird für Thread verwendet. Schalte Thread ab oder wähle dort einen anderen Stick, bevor du diesen für Zigbee nutzt."
web.radios.zigbee_is_thread_stick:
  en: "in use for Thread"
  de: "für Thread in Verwendung"
```

`web.radios.zigbee_is_thread_stick` is the short suffix in the option label, alongside the existing `web.radios.in_use` and `web.radios.option_blocked`, which is the pattern the Bluetooth row already uses for an rfkill-blocked adapter. The long sentence is the API's refusal, shown when a stale page posts anyway.

Plus the strings the progress and presence reporting need, each `en` + `de`: `web.radios.zigbee_loading_quirks` (say that the first connection prepares device support and takes a few seconds — the user is looking at a 9-15 s pause and deserves to know it is expected), `web.radios.zigbee_opening_radio`, `web.radios.zigbee_connected`, `web.radios.zigbee_failed_retrying` (carrying the attempt count and the error, because the supervisor never gives up and the card must not imply it has), and `web.radios.zigbee_device_missing` (the stored stick is not present — name replugging it or choosing another, the way the Thread row already does).

- [ ] **Step 8: Run to verify they pass.**

- [ ] **Step 9: Prove each protection catches its fault.** Nineteen faults: fifteen from the exclusion and API tests, plus the four Thread-channel tests added to Step 1. FAIL, revert, PASS, both pasted. **The Thread-exclusion faults are the single most important ones in this plan** — prove them first and paste them first, and prove them against the measured two-stick fixture rather than against invented paths. Two of the fifteen are specifically the hardware measurement's: comparing path strings instead of resolved major:minor (`test_the_two_sticks_on_the_maintainers_pi_end_up_on_opposite_sides`), and ignoring `thread_enabled` (`test_a_stick_freed_by_turning_thread_off_becomes_selectable_again`). The first must turn the MG24 selectable; if it does not, the fixture is not reproducing the real machine and the test is worthless — stop and fix the fixture. The four added in Step 2 (synchronous apply, warm-up on the request path, no progress, no presence) are proved the same way as the rest; for the first two, a fake source whose `connect()` blocks longer than the test client's timeout turns "the handler waited" into a failing test rather than a slow one. The four Thread-channel faults come last but are not lower stakes than they look: the whole reason `channels_excluding` shipped inert in Task 7 was that nothing called its input, and a mistake in `thread_channel_from_dataset` or `current_thread_channel` reintroduces exactly that silently, since a Zigbee network still forms perfectly well on the Thread channel it was supposed to avoid — there is no error, only a collision nobody is told about.

- [ ] **Step 10: Run the checks** (all five, four-part pytest).

- [ ] **Step 11: Commit**

```bash
git add src/loxmatter tests
git commit -m "$(cat <<'EOF'
feat(zigbee): choose the Zigbee coordinator, and never the Thread stick

The stick is a bridge setting, not a sidecar job: zigpy runs in process, so
the change is applied by reconnecting the source rather than by recreating
the container the request is being served from. Thread and Bluetooth are
therefore untouched by construction - the change never reaches the file they
travel in.

The Thread coordinator is refused by resolved major:minor, not by path
string: the same stick is /dev/ttyUSB0 in .env, a by-id path in the UI and a
third name under the container's mount, and opening it as a Zigbee radio
would garble a live Thread network.

Also closes the matching RF-level collision: ZigbeeSource has excluded
OTBR's active channel from its own candidate list since Task 7, but nothing
ever read that channel out of OTBR's dataset, so the exclusion was inert.
The one place a ZigbeeSource is now built - at startup and on every radio
change alike - fetches it once per build, never inside connect() itself,
which runs on every one of the supervisor's retries.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: The Pairing API

Commissioning is deliberately **outside** `DeviceSource` (boundary design §3.2): Matter takes a code and returns one device, Zigbee opens the network and devices arrive later, possibly several. This route calls `ZigbeeSource` by name.

**Files:**
- Modify: `src/loxmatter/api/zigbee.py`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_zigbee_api.py`

**Interfaces:**
- Consumes: `ZigbeeSource.permit`, the pairing-row state Task 7 keeps, `Store.register_device`, `Runtime.on_node_snapshot`.
- Produces: `POST /api/zigbee/permit`, `GET /api/zigbee/pairing`, `POST /api/zigbee/pairing/{ieee}/retry`, `DELETE /api/zigbee/pairing/{ieee}`, `PATCH /api/zigbee/pairing/{ieee}` (name and room).

- [ ] **Step 1: Write the failing tests.**

```python
async def test_the_permit_window_is_the_protocol_maximum_and_has_a_server_side_end():
    """254 s is the maximum zigpy will accept - it asserts 0 <= t <= 254 -
    and the response carries the END TIMESTAMP, not the duration, so a page
    that is reloaded halfway through still counts down to the truth rather
    than restarting at 254.

    Fault to prove it: return the duration and let the page count from
    there."""


async def test_no_unlimited_join_mode_is_offered():
    """Z2M REMOVED its permanent permit_join option in 2.0 as a security
    concern and sometimes unstable. A network left open forever is one any
    passing device can join.

    Fault to prove it: accept `duration: 0xFF` as "forever"."""


async def test_stopping_closes_the_window_immediately():
    """`permit(0)`. Leaving the tab does the same - ZHA's failure to close
    its window is a standing complaint.

    Fault to prove it: ignore a duration of 0."""


async def test_a_row_is_keyed_by_ieee_and_survives_a_reinterview():
    """Fault to prove it: key rows by NWK. A rejoining device then appears
    twice, once under each address it has had."""


async def test_an_interview_that_fails_says_so_and_offers_retry_and_remove():
    """ZHA has NO failure state at all - a hung interview sits on "Starting
    interview" forever (HA core issues 124114, 99497, 123136, 162426). That
    is the single thing this tab exists to do better.

    Fault to prove it: drop the `device_init_failure` handler."""


async def test_a_row_with_no_progress_becomes_stuck_and_names_the_real_cause():
    """60 s for a mains device, 90 s for a battery one. A stuck row is NOT
    an error row: it keeps waiting and keeps offering Retry and Remove,
    because the usual cause is a battery device that fell asleep and the
    usual fix is pressing its button - which the copy says.

    Fault to prove it: mark it failed instead. The user then removes a
    device that was about to finish."""


async def test_a_device_discovered_without_a_join_window_is_not_reported_as_just_joined():
    """zigpy interviews devices of an ADOPTED network on its own
    (`_discover_unknown_device`), so devices can appear when nobody opened a
    window. Telling the user "a device joined just now" would be false.

    Fault to prove it: report every new device as a join."""


async def test_the_quirk_hint_reports_what_zigpy_actually_resolved():
    """The resolved device carries `_quirk_registry_entry`. This is
    loxmatter's equivalent of Z2M's "Unsupported" badge, and it explains
    missing values before the user asks - users routinely confuse a failed
    interview with an unsupported device, so the two are shown as different
    things in different places.

    Fault to prove it: report "quirk applied" unconditionally."""


async def test_the_name_is_prefilled_from_manufacturer_and_model_not_the_ieee():
    """Z2M prefills the IEEE, which nobody keeps.

    Fault to prove it: prefill the IEEE."""


async def test_removal_forgets_the_device_even_when_the_leave_is_never_delivered():
    """`remove()` deletes the device from zigpy's database whether or not
    the leave request arrives, and nothing stops the device rejoining. The
    confirmation copy says exactly that, and a sleeping device must be
    factory-reset before it can be paired elsewhere.

    Fault to prove it: keep the row when the leave is not acknowledged. The
    UI then shows a device the bridge has already forgotten."""
```

- [ ] **Step 2: Run to verify they fail**, implement, run to verify they pass.

- [ ] **Step 3: Add the i18n keys** for every route error (`api.zigbee.permit_failed`, `api.zigbee.unknown_device`), each `en` + `de`.

- [ ] **Step 4: Prove each protection catches its fault.** Ten faults. FAIL, revert, PASS, both pasted.

- [ ] **Step 5: Run the checks** (all five, four-part pytest).

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/api/zigbee.py src/loxmatter/i18n/strings.yaml tests/api/test_zigbee_api.py
git commit -m "$(cat <<'EOF'
feat(api): open the Zigbee network for pairing, and say what is happening

The window is the protocol maximum and its END is what the API returns, so a
reloaded page counts down to the truth; there is no unlimited mode, which
Z2M removed as a security concern. Every pairing row is keyed by IEEE and
can fail, which ZHA's cannot - a hung interview there sits on "Starting
interview" forever - and a row that stops progressing says the real reason
rather than turning into an error.

Removal copy is honest: the bridge asks the device to leave and forgets it
either way.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: The Zigbee Row on the Radios Card, and the Badge

**Files:**
- Modify: `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `src/loxmatter/web/style.css`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `GET`/`PUT /api/zigbee/radio` (Task 11).
- Produces: `zigbeeRadioOptions()`, `zigbeeRadioChanged()`, `applyZigbeeRadio()`, `zigbeeAdvancedOpen`, `zigbeeProgressText()`, `zigbeeRadioPolling()` in `app.js`; the sprite symbol `i-transport-zigbee`; `transportBadge` gains its `zigbee` entry.

- [ ] **Step 1: Write the failing tests** in `tests/api/test_web.py`, using the node harness — a Python test that fetches the page proves only that the file was served.

```python
@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_zigbee_badge_has_a_glyph_now_that_the_spec_adds_one(api):
    """`transport_for` has returned "zigbee" since the boundary design, but
    `transportBadge` deliberately had no symbol for it - the comment in
    app.js says "Zigbee has no glyph before the Zigbee spec adds one". This
    is that spec.

    Runs the REAL `transportBadge` in node against a device object shaped
    the way `GET /api/devices` returns one; a markup-substring assertion
    could not fail for a binding that is merely wrong.

    Fault to prove it: remove the `zigbee` entry from the symbols map. The
    badge then returns null and a Zigbee device tile shows no transport at
    all, while Thread and IP ones do."""
    values = _app_state(
        setup="console.log(JSON.stringify({"
        "  zigbee: state.transportBadge({ transport: 'zigbee' }),"
        "  thread: state.transportBadge({ transport: 'thread' }),"
        "  unknown: state.transportBadge({ transport: null }),"
        "}));",
        translations={"web.devices.transport_zigbee": "Zigbee"},
    )
    assert values["zigbee"]["symbol"] == "i-transport-zigbee"
    assert values["zigbee"]["label"] == "Zigbee"
    assert values["thread"]["symbol"] == "i-transport-thread"
    assert values["unknown"] is None


async def test_the_sprite_carries_the_zigbee_symbol(api):
    """The glyph the badge names must exist, or the tile renders an empty
    box. Drawn in the sprite's own style - 24 viewBox, stroke 1.8,
    currentColor - and deliberately NOT the official logo: "Zigbee" is a
    trademark of the Connectivity Standards Alliance.

    Fault to prove it: rename the symbol id."""
    client, _, _ = api
    page = (await client.get("/")).text
    assert 'id="i-transport-zigbee"' in page


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_thread_stick_is_offered_with_a_reason_not_silently_dropped(api):
    """A stick that is simply missing from a list is a bug report waiting to
    happen: the user sees their stick in the Thread row and not in the
    Zigbee one and concludes the detection is broken. It is listed,
    disabled, with the reason.

    Fault to prove it: filter the Thread device out of the list."""
    values = _app_state(
        setup="state.zigbee = { serial: ["
        "  { path: '/dev/serial/by-id/a', product: 'SONOFF', fingerprint: null,"
        "      is_thread: true, selectable: false },"
        "  { path: '/dev/serial/by-id/b', product: 'ZBT-1', fingerprint:"
        "      { name: 'ZBT-1', radio_type: 'ezsp' }, is_thread: false, selectable: true },"
        "], current: null };"
        "console.log(JSON.stringify(state.zigbeeRadioOptions()));",
        translations={"web.radios.zigbee_is_thread_stick": "used by Thread"},
    )
    thread_option = next(o for o in values if o["value"] == "/dev/serial/by-id/a")
    assert thread_option["disabled"] is True
    assert "Thread" in thread_option["label"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_card_refuses_the_maintainers_own_thread_stick(api):
    """The measured installation, reaching the screen (12 September 2026).

    His two sticks are indistinguishable by USB ids and by major number, so
    this is the shape the card must get right on the one machine that will
    actually run it: the ITEAD stick selectable, the MG24 - which his Thread
    border router is running on - listed, disabled, and labelled with the
    reason.

    `disabled` reads the server's `selectable`, NOT a rule the page invents,
    so the two can never disagree about which sticks are safe. That is the
    same reasoning behind `option.blocked` on the Bluetooth row.

    Fault to prove it: have `zigbeeRadioOptions()` compute `disabled` from
    the product name (say, anything containing "MG24") instead of reading
    `selectable`. The MG24 stays disabled for the wrong reason and the test
    still passes - so ALSO flip `selectable` to true on the MG24 entry and
    confirm the option becomes enabled. If it does not, the page is
    deciding for itself and the server check is decorative."""
    values = _app_state(
        setup="state.zigbee = { serial: ["
        "  { path: '/dev/serial/by-id/usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a-if00-port0',"
        "      product: 'SONOFF Dongle Plus MG24',"
        "      fingerprint: { name: 'SONOFF Zigbee Dongle Plus MG24', radio_type: 'ezsp' },"
        "      is_thread: true, selectable: false },"
        "  { path: '/dev/serial/by-id/usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf-if00-port0',"
        "      product: 'SONOFF ZBDongle-E V2',"
        "      fingerprint: { name: 'SONOFF ZBDongle-E V2', radio_type: 'ezsp' },"
        "      is_thread: false, selectable: true },"
        "], current: null };"
        "console.log(JSON.stringify(state.zigbeeRadioOptions()));",
        translations={"web.radios.zigbee_is_thread_stick": "in use for Thread"},
    )
    mg24 = next(o for o in values if "MG24" in o["value"])
    itead = next(o for o in values if "Itead" in o["value"])
    assert mg24["disabled"] is True
    assert "Thread" in mg24["label"]
    assert itead["disabled"] is False


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_an_unrecognised_stick_is_selectable_and_says_so(api):
    """Refusing to work with an unlisted stick would be worse than letting
    the user say what it is. It is offered, marked as unrecognised, and the
    Advanced disclosure carries radio type and baud rate.

    Fault to prove it: disable options with no fingerprint."""


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_row_says_what_a_running_connection_attempt_is_doing(api):
    """Task 11's `PUT` answers 202 and connects in the background, so the
    card is the only thing that can tell the user a 9-15 s quirks warm-up is
    a healthy operation rather than a dead one - which is this plan's own
    Global Constraint about long-running work, and the 2a-1 lesson behind
    it.

    Runs the REAL `zigbeeProgressText` in node, because a markup assertion
    cannot fail for a binding that is merely wrong.

    Fault to prove it: render a bare connected/not-connected boolean. A
    warm-up in progress then reads exactly like a broken stick."""
    values = _app_state(
        setup="console.log(JSON.stringify({"
        "  quirks: state.zigbeeProgressText({ state: 'loading_quirks', attempts: 0 }),"
        "  opening: state.zigbeeProgressText({ state: 'opening_radio', attempts: 0 }),"
        "  failed: state.zigbeeProgressText({ state: 'failed', attempts: 4, error: 'nope' }),"
        "  polling: state.zigbeeRadioPolling({ state: 'loading_quirks' }),"
        "  settled: state.zigbeeRadioPolling({ state: 'connected' }),"
        "}));",
        translations={
            "web.radios.zigbee_loading_quirks": "Preparing device support...",
            "web.radios.zigbee_opening_radio": "Opening the stick...",
            "web.radios.zigbee_failed_retrying": "Failed ({attempts}): {error}",
        },
    )
    assert values["quirks"] == "Preparing device support..."
    assert values["opening"] == "Opening the stick..."
    assert "4" in values["failed"] and "nope" in values["failed"]
    # The card polls while an attempt is running and stops once it settles,
    # exactly as `loadRadios()` does for a sidecar job.
    assert values["polling"] is True
    assert values["settled"] is False


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_configured_stick_that_is_gone_says_so(api):
    """The in-process design's worst failure mode reaching the screen: the
    supervisor retries a stick that will never answer, forever, and without
    this the card says only "not connected". `GET /api/zigbee/radio` returns
    `configured_device_present` for exactly this, the way the Thread row
    already uses `thread_device_present`.

    Fault to prove it: ignore the flag and render the stored path alone. An
    unplugged stick is then indistinguishable from one that is present and
    refusing to open - and the two need opposite actions from the user."""
    values = _app_state(
        setup="state.zigbee = { serial: [], current: null,"
        "  configured_path: '/dev/serial/by-id/gone', configured_device_present: false,"
        "  progress: { state: 'failed', attempts: 9, error: 'no such device' } };"
        "console.log(JSON.stringify({ text: state.zigbeeProgressText(state.zigbee.progress,"
        "  state.zigbee.configured_device_present) }));",
        translations={"web.radios.zigbee_device_missing": "The chosen stick is not plugged in."},
    )
    assert values["text"] == "The chosen stick is not plugged in."


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_applying_the_zigbee_row_never_sends_a_radios_request(api):
    """Spec Correction 3, enforced in the page itself: the Zigbee row has
    its own Apply and its own endpoint. If it ever shared the sidecar's
    request body, changing the Zigbee stick would recreate the bridge
    container - the one the page is talking to.

    Fault to prove it: build the Zigbee half into `radiosRequestBody()`."""
    source = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    body = source[source.index("radiosRequestBody()") : source.index("radiosConfirmKeys()")]
    assert "zigbee" not in body.lower()
```

- [ ] **Step 2: Run to verify they fail**, then implement: add the `i-transport-zigbee` symbol to the sprite next to `i-transport-thread` and `i-transport-ip`, with the same comment style and a neutral pictogram; add `zigbee: "i-transport-zigbee"` to `transportBadge`'s symbols map and update its comment, which currently says Zigbee has no glyph yet; add the Zigbee row to the radios card **below** Thread and Bluetooth, with its own Apply button, its own busy and error fields, and an `<details class="commission-disclosure">` Advanced block exposing radio type and baud rate.

The row's markup must make the difference from its neighbours visible rather than implicit — one `<p class="hint">` saying that changing the Zigbee stick takes effect immediately and does not restart anything, because the two rows above it warn about exactly the opposite.

- [ ] **Step 3: Run to verify they pass.**

- [ ] **Step 4: Prove each protection catches its fault.** Seven faults. FAIL, revert, PASS, both pasted.

- [ ] **Step 5: Check it in a browser.** Start the app and look at the Settings tab at the narrowest real card width, in **both** themes: the three rows read as three rows, the Thread stick is visibly disabled with its reason, and the Advanced disclosure opens without shifting the rows below it. Screenshot both themes into the task report. A test that renders markup cannot see a layout that collapses.

  Check the progress line too, because it is the part a static render cannot judge: with the row mid-attempt, the text must not reflow the card or push the Apply button around as it changes between the three states, and the failed state has to stay readable when the error is a long one. Point the row at a path that does not exist to see the missing-stick text for real.

- [ ] **Step 6: Run the checks** (all five, four-part pytest).

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/web src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): choose the Zigbee coordinator on the radios card

A third row beside Thread and Bluetooth, with its own Apply: it reaches the
bridge directly rather than the sidecar, so it takes effect at once and
leaves the other two radios alone, and the row says so because its
neighbours warn about the opposite.

The Thread stick is listed and disabled with the reason rather than quietly
missing, and an unrecognised stick stays selectable behind an Advanced
disclosure. Device tiles finally get the Zigbee transport badge the boundary
design left a gap for.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: The Pairing Tab

The commissioning card gains two tabs, **Matter** and **Zigbee**. The Matter tab is today's card unchanged, including the code field and the sticker illustration.

**Files:**
- Modify: `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `src/loxmatter/web/style.css`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`, plus a throwaway Alpine harness

**Interfaces:**
- Consumes: the routes of Task 12.
- Produces: `commissionTab`, `zigbeePermitUntil`, `zigbeeCountdown()`, `zigbeeRowState(row)`, `startZigbeeSearch()`, `stopZigbeeSearch()`, `extendZigbeeSearch()`, `retryZigbeeDevice(ieee)`, `removeZigbeeDevice(ieee)` in `app.js`.

- [ ] **Step 1: Write the failing tests** in `tests/api/test_web.py`:

```python
@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_zigbee_tab_is_absent_without_a_configured_source(api):
    """Absent, not disabled. A tab that explains why it does nothing is
    worse than no tab - and an installation with no Zigbee stick is the
    normal case, not an error state.

    Fault to prove it: render the tab disabled instead."""


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_countdown_is_computed_from_the_server_timestamp(api):
    """ZHA starts its permit window when the page OPENS and runs a
    browser-side setTimeout(254000), so a reloaded page silently restarts
    the countdown while the real window is nearly over. The API returns
    `permit_until`, and this counts down to it - so a reload, a second tab
    and a phone all show the same truth.

    Fault to prove it: count down from a duration stored when the button was
    pressed."""
    values = _app_state(
        setup="state.zigbeePermitUntil = new Date(Date.now() + 60000).toISOString();"
        "console.log(JSON.stringify({ left: state.zigbeeCountdown() }));"
    )
    assert 55 <= values["left"] <= 60


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_the_window_is_not_open_before_the_user_asks(api):
    """The tab opens on reset guidance and one button. ZHA opens the network
    as the page loads and burns the window while the user is still reading
    how to reset their device.

    Fault to prove it: call the permit route from the tab's init."""


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
@pytest.mark.parametrize(
    "state_name",
    ["joined", "interviewing", "configuring", "ready", "failed", "stuck", "waiting_wake"],
)
async def test_every_row_state_renders_its_own_text(api, state_name):
    """The seven states of design 3.1. The last two - stuck and waiting to
    wake - are the ones ZHA does not have, and they are the reason this tab
    is worth building rather than copying.

    Fault to prove it: collapse `stuck` into `failed`. A battery device that
    simply fell asleep is then presented as a broken one, and the user
    removes it."""


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_a_stuck_row_still_offers_retry_and_remove_and_keeps_waiting(api):
    """A stuck row is NOT an error row.

    Fault to prove it: hide the actions while stuck."""


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_leaving_the_tab_closes_the_join_window(api):
    """ZHA never closes its window when the page is left, and that is a
    standing complaint - an open Zigbee network is one any passing device
    can join.

    Fault to prove it: leave the window open on tab change."""


async def test_the_matter_tab_keeps_the_card_it_had(api):
    """The Matter half must be the same card, not a rebuilt one: the code
    field, its detection chip, the sticker illustration and both
    disclosures.

    Fault to prove it: drop the sticker `<svg>` while restructuring."""
    client, _, _ = api
    page = (await client.get("/")).text
    for marker in ('id="commission-code"', "code-sticker", "commission-disclosure"):
        assert marker in page
```

- [ ] **Step 2: Run to verify they fail**, then implement. The tab strip reuses the existing `nav.tabs` pattern (the Settings language card shows it) rather than inventing a second one. The Zigbee tab's order follows spec §3.1 exactly: reset guidance and one button first; then, while open, the countdown with **Stop** and **Keep open longer**; then one row per device keyed by IEEE.

The ready row carries an inline name prefilled with `<Manufacturer> <Model>`, saved on blur, and a room `<select>` that **reuses the commissioning card's existing room control** — the same component and the same room-key encoding (`""` for no room), not a second one. The quirk hint sits on the ready row. The removal confirmation uses the honest copy of §3.1.

- [ ] **Step 3: Add every i18n key** from spec §3.4's table, each with `en` **and** `de`.

- [ ] **Step 4: Run to verify they pass.**

- [ ] **Step 5: Prove each protection catches its fault.** Eight faults. FAIL, revert, PASS, both pasted.

- [ ] **Step 6: Check the real bindings in a throwaway harness**, the way the transport badge was checked (boundary design §7.3): cut the tab's markup out of `index.html` **by script, not by retyping**, put `style.css` and the vendored `vendor/alpine.min.js` beside it, serve it over http, and drive it. Check the countdown from `permit_until`, all seven row states, and that leaving the tab closes the window. A Python test proves the file was served; this proves the bindings work. Delete the harness afterwards and record what it showed in the task report.

- [ ] **Step 7: Run the checks** (all five, four-part pytest).

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): pair a Zigbee device from the browser

Two tabs on the commissioning card; the Matter one is the card it has always
been. The Zigbee tab opens on reset guidance and a button rather than on an
open network, because starting the window when the page loads burns it while
the user is still reading how to reset their device, and it counts down to
the server's own end timestamp so a reload shows the truth.

Every row can fail, get stuck, or be waiting for a sleepy device to wake -
the three states ZHA does not have, and the reason a hung interview there
looks like a broken device rather than a sleeping one. Leaving the tab
closes the window.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: Documentation, Change Notes, and the Hardware Checklist

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `docs/superpowers/specs/2026-09-09-first-run-checklist.md`, `docs/superpowers/specs/2026-09-12-zigbee-source-design.md`

- [ ] **Step 1: Record the spec corrections.** Append a short, dated "Corrections after implementation" section to `docs/superpowers/specs/2026-09-12-zigbee-source-design.md` carrying the five findings from this plan's own Spec Corrections section, each in one or two sentences. Do **not** rewrite the spec's body: it is the record of what was designed, and a design document quietly edited to match what was built stops being evidence of anything.

- [ ] **Step 2: Write the residual-exposure note** in `README.md`, in the security section that already carries the updater's equivalent (the radios design §6.6 pattern). It must say plainly: the `/dev` listing leaks hardware inventory including serial numbers under `by-id`; the cgroup rule reaches **every** USB-serial adapter on the host, the Thread stick included, so code execution in the bridge could talk to any of them; the rule does **not** reach block devices, `/dev/mem` or i2c; and narrowing it to observed minors (`c 188:0 rmw`) is possible at the cost of "works after replugging into another port". Also record that zigpy's network key lives in its database in clear text, is never logged, and that the backup download is deliberately not built yet.

- [ ] **Step 3: Write the change notes** in `CHANGELOG.md` under `## [Unreleased]`, in the voice that section already uses — read by people who do not know the code, saying what they can now do and what it costs. Cover: Zigbee devices can be paired and used alongside Matter; Matter is still required; a second USB stick is needed; the image is about 33 MB larger and the first start after an update is a few seconds slower on a Pi; the Thread stick can never be chosen; OTA updates of the user's lamps are off; and that colour now works on lamps that only accept XY, which improves some Matter lamps too.

- [ ] **Step 4: Extend the first-run checklist** `docs/superpowers/specs/2026-09-09-first-run-checklist.md` with a Zigbee section. The order is not cosmetic — **the Thread-exclusion rule must be proven on the Pi before any Zigbee code opens a port**, because a wrong pick garbles the maintainer's live Thread network. Then, in order: confirm the otbr image still starts with `&uart-exclusive`; confirm the container can open the new stick at all (the cgroup rule); measure `zhaquirks.setup()` against the 9-15 s extrapolation; pair the lamp and a sensor; confirm reporting actually arrives at the configured intervals; confirm IAS enrolment end to end by opening a contact sensor; check whether `ExecuteIfOff` works on the test lamp or the turn-on-first workaround is needed; and settle Matter's BooleanState polarity using MYGGBETT and KLIPPBOK, which are already commissioned — reading their `x/69/0` **through the running instance's `signals` route**, never through a fresh `snapshots()` call, which returns matter-server's cache.

- [ ] **Step 5: State plainly what is unverified.** In both the checklist section and the task report: **nothing in this plan has been exercised against Zigbee hardware.** Every timing, every reporting interval and every device behaviour is read from ZHA's and zigpy's source or measured against a silent pty, not against a radio that answered. Do not report any of it as verified.

- [ ] **Step 6: Run the checks.** `uv run ruff format --check .` matters especially here — `ruff format` also formats fenced Python inside Markdown, so a reflowed code sample in a document breaks it. Then the other four.

- [ ] **Step 7: Commit**

```bash
git add README.md CHANGELOG.md docs
git commit -m "$(cat <<'EOF'
docs: record what Zigbee support grants, costs and has not proven

The cgroup rule reaches every USB-serial adapter on the host, the Thread
stick included, and the network key sits in zigpy's database in clear text -
both belong in the security section rather than in a commit message nobody
re-reads.

The first-run checklist puts the Thread-exclusion check before anything
opens a port, because a wrong pick garbles a live Thread network, and states
that nothing here has been exercised against Zigbee hardware at all.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

Run after the plan is written, before execution starts. This is a checklist for the plan's author, not a subagent dispatch.

**1. Spec coverage.** Every numbered section of `2026-09-12-zigbee-source-design.md` maps to a task:

| Spec section | Task |
|---|---|
| 3.1 The pairing tab | 12, 14 |
| 3.2 The Zigbee row in the radios card | 11, 13 |
| 3.3 The badge | 13 |
| 3.4 New user-facing strings | 5, 7, 11, 12, 13, 14 |
| 4.1-4.3 Lifecycle, conformance, reconnect | 7 |
| 4.4 Events into the runtime | 7 |
| 4.5 Availability | 8 |
| 4.6 Startup failures in the user's words | 7 |
| 4.7 Command timeout and error vocabulary | 5 |
| 4.8 A stick that already carries a network | 7 |
| 4.9 The heartbeat keeps its meaning | 10 |
| 5.1-5.5 Snapshot contents, classes, device types, command lists, profile table | 3, 6 |
| 5.6 Colour, and a fix that helps both technologies | 3 |
| 5.7 Command argument names | 6 |
| 6.1-6.5 Configure-on-join, reporting, IAS, sleepy devices, rejoin | 9 |
| 7 Radio choice and the fingerprint table | 4, 11 |
| 8.1-8.2 Compose, the otbr collision | 2 |
| 8.3-8.5 Dependencies, image, zigpy configuration | 1, 7 |
| 9 Security and privacy | 2, 15 |
| 10 Testing | every task; 10.2/10.3 in 15 |
| 11 Explicitly not built here | not planned, by design |
| 12 Open points 1-7 | 1 answered in Spec Corrections; 2, 3, 4 in Task 5; 5 in Tasks 2 and 15; 6 and 7 recorded as 2b in Task 15 |

No spec requirement is unassigned. Section 11's eight deferred items are deliberately absent, and Task 15 records them.

**2. Placeholder scan.** No task contains "TBD", "implement later", "add appropriate error handling", "similar to Task N", or a code step without code. Tasks 7-14 elide some test **bodies** with an explicit instruction to write them in full and a note that a `pass` body is a plan failure; every one of those tests carries its complete docstring and its named fault, which is the part a fresh implementer cannot reconstruct. Where this plan supplies invented data — the twelve by-id strings in Task 4, the four pinned versions in Task 1 — it says so and tells the implementer to verify against the real source and report a mismatch rather than bend the code to the plan.

The packed Loxone colour number in Task 3 is no longer in that category: it was invented (`16711680`, hex RGB), was checked against `commands/color.py` during the review of this plan, was **wrong** — that encoding concatenates whole percentages, so the value meant "red at 680 %" and would have raised `LoxoneColourError` — and has been corrected to `100`.

**Task 4's by-id strings have now left that category too, and taking the measurement changed a decision.** Both sticks on the maintainer's Pi were read on 12 September 2026 and are quoted verbatim as `REAL_ITEAD` and `REAL_MG24`. Three things came out of it. The invented strings' upper-case `ITEAD_SONOFF` spelling was harmless (the matcher lowercases) but **nothing was pinning that**, so a case test was added. Both sticks report `10c4:ea60` and both are major 188, which turns "never match on VID:PID alone" from a borrowed Z2M rule into a measured fact about this project's own hardware, and is now stated as such in Task 4 and guarded by a test. And most seriously, the plan's justification for the SONOFF rows — "the maintainer's own MG24" — was **backwards**: his MG24 runs Thread, and the fingerprint table correctly reports it as a usable Zigbee coordinator, so the table alone would have offered him the radio his Thread network depends on. The fix is not in Task 4, whose answer is right; it is Task 11's exclusion, which is now written out as complete code, gated on `thread_enabled` so that disabling Thread genuinely frees the stick, and pinned by a test built from the real two-stick tree.

**This section predates a later correction and understated two things Tasks 1-8 had actually produced but nothing yet consumed — found by re-checking the section below against what those tasks committed, rather than trusting this table's word for it.** `AvailabilityChecker.start()`/`.stop()` (Task 8) were built and the checker was already constructed fresh inside `ZigbeeSource.subscribe()`, but nothing in Tasks 9-15 as originally written ever called `start()` — the sweep would never have run on any installation. Separately, `ZigbeeSource(thread_channel=...)` and `channels_excluding` (Task 7) existed with `thread_channel` defaulting to `None` and no caller ever passing anything else — `channels_excluding`'s own docstring flags this as "outstanding debt for Tasks 10 and 11" — so the Thread-channel exclusion the design requires had no implementation anywhere. Both are now closed: the first in Task 10 (which also had to correct a second, more serious problem the same review measured — see Task 8's "Measured correction" note — a naive `start()` would have shipped a checker whose sweep contradicted `mark_all_offline()` one tick after every link loss), the second in Task 11, via `matter/otbr.py`'s new `thread_channel_from_dataset`/`current_thread_channel` and the single builder function (`build_zigbee_source`) that now owns constructing every `ZigbeeSource` in the tree.

**3. Type consistency.** `GroupOutcome` (Task 5) carries `failed` as a stored, plan-ordered field with `unreachable`/`unconfigured` as order-preserving subsets, so `fanout.py`'s documented ordering guarantee survives the change; both call sites read it under those names. `Sources.replace` (Task 5) is consumed only by `ZigbeeRuntime` (Task 11). `ConnectionProgress`/`progress()` and `on_connection_change` (Task 7) are consumed by `ZigbeeRuntime` and `GET /api/zigbee/radio` (Task 11) and by `Runtime.set_zigbee_connected` (Task 10); `ZIGBEE_CONNECTED_KEY`, `cache_zigbee_connected` and `set_zigbee_connected` (Task 10) are used under those names in Tasks 7 and 11. `build_snapshot`, `rename_payload`, `DeviceFacts`, `EndpointFacts` (Task 6) are consumed under exactly those names in Task 7. `DeviceUnreachableError` and `SOURCE_CALL_TIMEOUT_SECONDS` (Task 5) are used under those names in Tasks 7 and 9. `Fingerprint`/`match_fingerprint`/`DEFAULT_UNKNOWN` (Task 4) are consumed in Tasks 7, 11 and 13. `ensure_quirks_loaded` (Task 1) is called in Tasks 7 and 10. `device_identity`/`is_same_device` (Task 11) are used only there. `ZigbeePendingStore` (Task 9) is reached as `store.zigbee_pending` in Task 9 alone. `transportBadge` (Task 13) keeps its existing signature. `AvailabilityChecker.start()`/`.stop()` (Task 8) are now called from `ZigbeeSource.subscribe()`/`disconnect()` (Task 10) and nowhere else — no new public name was needed. `thread_channel_from_dataset`/`current_thread_channel` (Task 11, in `matter/otbr.py`) are consumed only by `build_zigbee_source` in the same task.

**4. Ordering.** Every task depends only on earlier ones. Tasks 1-5 touch no Zigbee runtime code and are independently mergeable; Task 3 improves Matter on its own and could ship alone. Task 10 wires a source that Tasks 7-9 must already provide. Tasks 13 and 14 consume APIs from Tasks 11 and 12.

**5. What this plan cannot give the implementer.** There is no Zigbee stick on the test Pi. Every interval, every timeout and every device behaviour in it is read from ZHA's and zigpy's source or measured against a silent pty. The fake-based suites of Tasks 6-9 are therefore not a convenience but the only evidence that will exist until a second stick is bought, and the fault-injection rounds are what make them worth anything. Nothing here may be reported as verified against hardware.
