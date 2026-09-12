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
  uv run pytest -q tests/auth tests/commands tests/devtools tests/diagnostics tests/export tests/loxone tests/matter tests/model tests/profiles tests/projectsync tests/radios tests/sources
  # B
  uv run pytest -q tests/test_install_script.py tests/test_update_script.py tests/test_updater_script.py tests/test_updater_radios_script.py
  # C
  uv run pytest -q tests/test_build_arguments.py tests/test_cli.py tests/test_cli_language.py tests/test_compose_profiles.py tests/test_export_cli.py tests/test_i18n.py tests/test_otbr_watchdog.py tests/test_store_path.py tests/test_update_check.py tests/test_update_module.py tests/test_updater_entrypoint.py tests/test_updater_image.py tests/test_version.py
  ```

  The four parts cover `tests/` exactly, so **the pass counts must sum to the whole-suite total**. A sum that does not is evidence something was silently skipped — stop and report it. Baseline before this plan: **2063 passed, 2 skipped**.
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

from loxmatter.zigbee.quirks import ensure_quirks_loaded, quirks_loaded


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

Note for the implementer: these three tests share one process-wide flag, so each must reset it. Add this fixture at the top of the module, after the imports:

```python
import pytest

from loxmatter.zigbee import quirks as quirks_module


@pytest.fixture(autouse=True)
def _fresh_process_state():
    """The guard is deliberately process-wide (that is the whole point), so
    each test starts from a clean one rather than depending on file order."""
    quirks_module._reset_for_tests()
    yield
    quirks_module._reset_for_tests()
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


def test_the_radio_device_is_still_never_named_by_a_non_thread_service():
    """The rule this file has always enforced, restated against the change
    above: granting the bridge access must not have been done by giving it a
    `devices:` entry after all.

    Fault to prove it: add `devices: ["/dev/ttyUSB1:/dev/ttyUSB1"]` to
    `loxmatter`."""
    for name, service in _stack()["services"].items():
        if service.get("profiles") == ["thread"]:
            continue
        assert "devices" not in service, name


def test_otbr_asks_the_kernel_to_keep_its_stick_to_itself():
    """OpenThread takes flock + TIOCEXCL only when the radio URL carries
    `uart-exclusive` (research A.3); the compose file passed no lock at all.

    This is a SECOND layer, not the guarantee: TIOCEXCL is bypassed by a
    holder of CAP_SYS_ADMIN, which privileged otbr has. The real guarantee
    is that loxmatter never offers or accepts the Thread stick (section 3.2,
    Task 11).

    Fault to prove it: drop the parameter from RADIO_URL."""
    otbr = _stack()["services"]["otbr"]
    radio_url = " ".join(str(part) for part in otbr.get("command", []))
    assert "uart-exclusive" in radio_url
```

Note for the implementer: read how `RADIO_URL` is actually spelled in the `otbr` service before writing the last assertion — it may sit in `command:`, in `environment:` or in both. Assert against the place the file really uses; if it is an environment variable, read that instead of `command`. Do not change the shape of the service to suit the test.

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
Expected: PASS, all tests in the file including the four new ones.

- [ ] **Step 6: Prove each protection catches its fault.** Four faults, from the docstrings above: delete `c 166:* rmw`; add the rules to `matter-server`; add a `devices:` entry to `loxmatter`; drop `&uart-exclusive`. FAIL, revert, PASS, both outputs pasted.

- [ ] **Step 7: Run the checks** (all five, four-part pytest). Total: **2072 passed, 2 skipped**.

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
    command = _stored_command(cluster_id=768, command_id=7)
    calls = to_device_calls(command, "16711680")  # packed Loxone red

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
    command = _stored_command(cluster_id=768, command_id=7)
    calls = to_device_calls(command, "16711680")

    assert len(calls) == 2
    assert (calls[1].cluster_id, calls[1].command_id) == (8, 4)
```

Note for the implementer: `_stored_command` is a helper this module may or may not already have; reuse the existing way this file builds a `StoredCommand` rather than adding a second one. Read a neighbouring test first. Work out the correct packed Loxone value for full red from `loxone_rgb_to_rgb`'s encoding and use that — `16711680` is written here as the shape of the argument, and the real value must come from the existing encoding, not from this plan.

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
Expected: PASS — including the pre-existing `test_known_cluster_with_unknown_command_raises`, which uses **cluster 768, command 7** as its example of an unknown command. **That test must now be changed**, and it is a finding: pick another genuinely unserved pair in a known cluster — `(768, 0)` MoveToHue — and update the test and its docstring. Record the change in the task report with this reason; do not delete the test.

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
kind. Every row below is a real product; the two that matter most for this
project are the SONOFF pair, because the maintainer's own MG24 shares its
VID:PID with at least five other sticks and only the by-id string tells them
apart."""

from __future__ import annotations

import pytest

from loxmatter.radios.fingerprints import ambiguous_vid_pids, match_fingerprint, table
from loxmatter.radios.inventory import SerialRadio


def _stick(by_id: str, vid_pid: str, manufacturer: str | None = None) -> SerialRadio:
    return SerialRadio(
        path=f"/dev/serial/by-id/{by_id}",
        tty="ttyUSB0",
        manufacturer=manufacturer,
        product=None,
        serial=None,
        vid_pid=vid_pid,
    )


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
        (
            "usb-ITEAD_SONOFF_Zigbee_3.0_USB_Dongle_Plus_V2_9f1-if00",
            "1a86:55d4",
            "ITEAD",
            "ezsp",
            115200,
            "software",
        ),
        (
            "usb-ITEAD_SONOFF_Zigbee_3.0_USB_Dongle_Plus_V2_9f1-if00",
            "10c4:ea60",
            "ITEAD",
            "ezsp",
            115200,
            "software",
        ),
        (
            "usb-SONOFF_Zigbee_3.0_USB_Dongle_Plus_MG24_e26a-if00",
            "10c4:ea60",
            "SONOFF",
            "ezsp",
            115200,
            "software",
        ),
        (
            "usb-SONOFF_Zigbee_3.0_USB_Dongle_Max_MG24_77aa-if00",
            "10c4:ea60",
            "SONOFF",
            "ezsp",
            115200,
            "software",
        ),
        (
            "usb-ITEAD_SONOFF_Zigbee_3.0_USB_Dongle_Plus_ab12-if00",
            "10c4:ea60",
            "ITEAD",
            "znp",
            115200,
            "software",
        ),
        ("usb-SMLIGHT_SLZB-06M_1122-if00", "10c4:ea60", "SMLIGHT", "ezsp", 115200, "software"),
        ("usb-SMLIGHT_SLZB-06p7_3344-if00", "10c4:ea60", "SMLIGHT", "znp", 115200, "software"),
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
    six sticks in this table, including the maintainer's SONOFF MG24. Z2M
    refuses a VID:PID-only match for it and so does this.

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
```

Note for the implementer: the twelve by-id strings above are **shaped** like real ones but were written for this plan. Before relying on them, check each against the regexes in research A.1's table and against the one real string the repository already has (`SONOFF` in `tests/api/test_radios_api.py`). If a regex does not match the string as written here, the **string** is what is wrong — fix it and say so in the report; do not loosen a regex to accept a string this plan invented.

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
Labs CP210x bridge, shared by at least six of the sticks below - including
the maintainer's SONOFF MG24 - and Z2M explicitly refuses a VID:PID-only
match for it. Only the by-id string tells them apart; a `10c4:ea60` with no
telling by-id string is UNKNOWN, not a guess. `_AMBIGUOUS_VID_PIDS` plus the
structural test in `tests/radios/test_fingerprints.py` keep that true for
rows nobody has written yet.

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
Expected: PASS, 16 passed (12 parametrised rows plus 4).

- [ ] **Step 5: Prove each protection catches its fault.** Four faults, from the docstrings: change the ZBDongle-P row to `ezsp`; allow the VID:PID-only match; return `DEFAULT_UNKNOWN` instead of `None`; add a `10c4:ea60` row with `path_pattern=None`. FAIL, revert, PASS, both pasted.

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
  - `loxmatter.commands.fanout.GroupOutcome(unreachable: list[str], unconfigured: list[str])`, returned by `dispatch_group` in place of the bare label list.

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

- [ ] **Step 3: Define the vocabulary** in `src/loxmatter/sources/__init__.py`:

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
    """
    key = f"api.technologies.{technology}"
    name = i18n.t(key)
    return technology if name == key else name
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

    unreachable: list[str]
    unconfigured: list[str]

    @property
    def failed(self) -> list[str]:
        return self.unreachable + self.unconfigured
```

and classify in `dispatch_group` by `isinstance(result, SourceNotConfiguredError)`. Both call sites (`api/control.py::_execute_group_command` and the group route in `loxone/server.py`) then map:

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

**Files:**
- Modify: `src/loxmatter/cli.py`, `src/loxmatter/loxone/runtime.py`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/test_cli.py`, `tests/loxone/test_runtime.py`

**Interfaces:**
- Consumes: `ZigbeeSource` (Task 7), `ensure_quirks_loaded` (Task 1), the radio setting (Task 11 — until it lands, read the path from a CLI option `--zigbee-device` defaulting to `None`).
- Produces: `Runtime` gains `zigbee_connected` as a sent key, alongside `d<id>_online`.

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
    still ends startup; this one is logged and the bridge runs on, with the
    supervisor retrying forever in the background.

    Fault to prove it: let `ZigbeeUnavailableError` propagate out of
    `_run`. A bridge whose Zigbee stick fell out then refuses to start, and
    every Matter device goes with it."""


async def test_the_quirks_warm_up_does_not_delay_the_web_ui():
    """`cli._run` starts uvicorn only AFTER `attach`, and the warm-up is an
    estimated 9-15 s on a Pi 4. Held on that path, `/health` and the web UI
    would be unreachable for the whole of it, every start - which the
    updater's own health wait would read as a failed update.

    Fault to prove it: await `ensure_quirks_loaded()` before `attach`."""


async def test_no_zigbee_work_happens_when_no_radio_is_configured():
    """An installation without a Zigbee stick pays nothing: no import, no
    warm-up, no source in the registry.

    Fault to prove it: always construct the source."""
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

and, after `client.connect()` and before `attach`:

```python
        if zigbee is not None:
            # NOT fatal, unlike matter-server: a missing or broken Zigbee
            # stick degrades the bridge, it never stops it. The supervisor
            # retries forever on its own 1 s -> 60 s backoff.
            try:
                await zigbee.connect()
            except ZigbeeUnavailableError as exc:
                logger.warning("Zigbee radio not available at startup: %s", exc)
```

The quirks warm-up is started as a background task, never awaited on the startup path — `ZigbeeSource.connect()` awaits it itself, so the ordering guarantee lives in one place and uvicorn is never held behind it.

- [ ] **Step 4: Run to verify they pass.**

- [ ] **Step 5: Prove each protection catches its fault.** Five faults. FAIL, revert, PASS, both pasted.

- [ ] **Step 6: Run the checks** (all five, four-part pytest).

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/cli.py src/loxmatter/loxone/runtime.py src/loxmatter/i18n/strings.yaml tests
git commit -m "$(cat <<'EOF'
fix(loxone): keep the watchdog meaning what it has always meant

With a second source, all_connected would have silenced the Loxone heartbeat
whenever the Zigbee stick was gone, and the Miniserver would have declared
the whole bridge dead while every Matter device still worked. The heartbeat
now covers the mandatory source; Zigbee reports its own health as a signal
and per device.

A Zigbee radio that will not come up is logged and retried, never fatal, and
the quirks warm-up runs in the background so the web UI answers throughout.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: The Zigbee Radio Setting, and Refusing the Thread Stick

**Read Spec Correction 3 before starting.** The Zigbee stick is a **bridge-owned** setting, not a sidecar request: zigpy runs in-process, so the change is applied by reconnecting the source — instantly, with no container recreated and no other radio disturbed. The sidecar's two-half request is not touched.

**Files:**
- Create: `src/loxmatter/model/zigbee_settings_store.py`, `src/loxmatter/api/zigbee.py`, `tests/model/test_zigbee_settings_store.py`, `tests/api/test_zigbee_api.py`
- Modify: `src/loxmatter/radios/inventory.py`, `src/loxmatter/model/store.py`, `src/loxmatter/loxone/server.py`, `src/loxmatter/cli.py`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/radios/test_inventory.py`

**Interfaces:**
- Consumes: `match_fingerprint`/`DEFAULT_UNKNOWN` (Task 4), `ZigbeeSource` (Task 7), `RadioConfig.thread_device` (2a-1).
- Produces:
  - `loxmatter.radios.inventory.device_identity(path: str, host_dev: Path, *, stat: Callable[[str], os.stat_result] = os.stat) -> tuple[int, int] | None` — the resolved `(major, minor)` of a character device, or `None`.
  - `loxmatter.radios.inventory.is_same_device(left: str | None, right: str | None, host_dev: Path, *, stat=os.stat) -> bool`
  - `ZigbeeRadioSettings(path: str | None, radio_type: str, baudrate: int, flow_control: str, saved_at: str | None)` and `store.zigbee_settings` with `get()` / `save(...)` / `clear()`.
  - `GET /api/zigbee/radio` and `PUT /api/zigbee/radio`.

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

- [ ] **Step 2: Write the failing API tests** `tests/api/test_zigbee_api.py`, using the `_host(tmp_path)` tree and heartbeat helpers `tests/api/test_radios_api.py` already builds (import them or copy their shape — read that file first):

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


async def test_applying_a_new_stick_reconnects_in_process():
    """The point of Spec Correction 3: no container is recreated, so the
    Matter link and every Matter device are untouched, and the request that
    asked for the change survives to answer.

    Fault to prove it: require a restart to pick the value up."""
```

- [ ] **Step 3: Run both to verify they fail.**

- [ ] **Step 4: Implement `device_identity` / `is_same_device`** in `src/loxmatter/radios/inventory.py`, mapping a host-visible `/dev/...` path onto the container's `host_dev` prefix the way `radios-once.sh` already does (`"$HOST_DEV${WANT_DEVICE#/dev}"`), resolving symlinks, and returning `None` for anything that is not a character device.

- [ ] **Step 5: Implement `ZigbeeSettingsStore`** on the `ResendSettingsStore` pattern — another view onto the same connection, its own module, keys `zigbee_path`, `zigbee_radio_type`, `zigbee_baudrate`, `zigbee_flow_control`, `zigbee_settings_saved_at`.

- [ ] **Step 6: Implement `src/loxmatter/api/zigbee.py`** with `build_zigbee_router(store, *, source_holder, host_dev, sys_root, update_dir)`. `GET` returns the detected sticks with their fingerprint result, which one is in use, which one is the Thread device (with a reason string, never silently omitted), and the source's connection state. `PUT` validates — known path, not the Thread device — persists, and reconnects the source in process, reporting the outcome. Every rejection detail goes through `i18n.t`.

Note for the implementer: the router needs to reach the live `ZigbeeSource` to reconnect it, and `build_app` builds routers before `_run` owns the source. Pass a small holder object (or a callable returning the current source) rather than the source itself, so a bridge started without Zigbee still serves `GET` and answers `PUT` with a clear 503. Decide the shape by reading how `build_app` already threads `client` and `sources`, and keep the same stance.

- [ ] **Step 7: Add the i18n keys** (`en` + `de`): `web.radios.zigbee_label`, `web.radios.zigbee_none`, `web.radios.zigbee_is_thread_stick`, `web.radios.fingerprint_unknown`, `web.radios.advanced`, `api.errors.zigbee_is_thread_stick`, `api.errors.zigbee_unknown_device`, `api.errors.zigbee_not_configured`.

- [ ] **Step 8: Run to verify they pass.**

- [ ] **Step 9: Prove each protection catches its fault.** Nine faults. FAIL, revert, PASS, both pasted. **The Thread-exclusion fault is the single most important one in this plan** — prove it first and paste it first.

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
- Produces: `zigbeeRadioOptions()`, `zigbeeRadioChanged()`, `applyZigbeeRadio()`, `zigbeeAdvancedOpen` in `app.js`; the sprite symbol `i-transport-zigbee`; `transportBadge` gains its `zigbee` entry.

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
    page = (await api.get("/static/index.html")).text
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
        "  { path: '/dev/serial/by-id/a', product: 'SONOFF', fingerprint: null, is_thread: true },"
        "  { path: '/dev/serial/by-id/b', product: 'ZBT-1', fingerprint:"
        "      { name: 'ZBT-1', radio_type: 'ezsp' }, is_thread: false },"
        "], current: null };"
        "console.log(JSON.stringify(state.zigbeeRadioOptions()));",
        translations={"web.radios.zigbee_is_thread_stick": "used by Thread"},
    )
    thread_option = next(o for o in values if o["value"] == "/dev/serial/by-id/a")
    assert thread_option["disabled"] is True
    assert "Thread" in thread_option["label"]


@pytest.mark.skipif(NODE is None, reason="node is required for this test")
async def test_an_unrecognised_stick_is_selectable_and_says_so(api):
    """Refusing to work with an unlisted stick would be worse than letting
    the user say what it is. It is offered, marked as unrecognised, and the
    Advanced disclosure carries radio type and baud rate.

    Fault to prove it: disable options with no fingerprint."""


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

- [ ] **Step 4: Prove each protection catches its fault.** Five faults. FAIL, revert, PASS, both pasted.

- [ ] **Step 5: Check it in a browser.** Start the app and look at the Settings tab at the narrowest real card width, in **both** themes: the three rows read as three rows, the Thread stick is visibly disabled with its reason, and the Advanced disclosure opens without shifting the rows below it. Screenshot both themes into the task report. A test that renders markup cannot see a layout that collapses.

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
    page = (await api.get("/static/index.html")).text
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

**2. Placeholder scan.** No task contains "TBD", "implement later", "add appropriate error handling", "similar to Task N", or a code step without code. Tasks 7-14 elide some test **bodies** with an explicit instruction to write them in full and a note that a `pass` body is a plan failure; every one of those tests carries its complete docstring and its named fault, which is the part a fresh implementer cannot reconstruct. Where this plan supplies invented data — the twelve by-id strings in Task 4, the packed Loxone colour number in Task 3, the four pinned versions in Task 1 — it says so and tells the implementer to verify against the real source and report a mismatch rather than bend the code to the plan.

**3. Type consistency.** `build_snapshot`, `rename_payload`, `DeviceFacts`, `EndpointFacts` (Task 6) are consumed under exactly those names in Task 7. `DeviceUnreachableError` and `SOURCE_CALL_TIMEOUT_SECONDS` (Task 5) are used under those names in Tasks 7 and 9. `Fingerprint`/`match_fingerprint`/`DEFAULT_UNKNOWN` (Task 4) are consumed in Tasks 7, 11 and 13. `ensure_quirks_loaded` (Task 1) is called in Tasks 7 and 10. `device_identity`/`is_same_device` (Task 11) are used only there. `ZigbeePendingStore` (Task 9) is reached as `store.zigbee_pending` in Task 9 alone. `transportBadge` (Task 13) keeps its existing signature.

**4. Ordering.** Every task depends only on earlier ones. Tasks 1-5 touch no Zigbee runtime code and are independently mergeable; Task 3 improves Matter on its own and could ship alone. Task 10 wires a source that Tasks 7-9 must already provide. Tasks 13 and 14 consume APIs from Tasks 11 and 12.

**5. What this plan cannot give the implementer.** There is no Zigbee stick on the test Pi. Every interval, every timeout and every device behaviour in it is read from ZHA's and zigpy's source or measured against a silent pty. The fake-based suites of Tasks 6-9 are therefore not a convenience but the only evidence that will exist until a second stick is bought, and the fault-injection rounds are what make them worth anything. Nothing here may be reported as verified against hardware.
