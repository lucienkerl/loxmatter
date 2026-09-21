# A Fresh Installation Forms Its Own Thread Network - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fresh loxmatter installation with a working Thread stick reaches a
formed Thread network without any command, and neither the otbr watchdog nor
the radios card's verification treats "no network configured yet" as a fault.

**Architecture:** Two shell scripts in the updater image learn one predicate,
"running but not configured" (`ot-ctl state` = `disabled` and
`ot-ctl dataset active` = `Error 23: NotFound`), and stop restarting or
rolling back in that state. The bridge gets a background loop,
`ThreadNetworkKeeper` in `src/loxmatter/matter/thread_network.py`, which forms a
network through OTBR's REST API (`PUT /node/dataset/active` with
`If-None-Match: *`) unless matter-server already has Thread devices, and hands
the dataset to matter-server. `GET /api/radios` reports the keeper's state and
the radios card shows one line for it.

**Tech Stack:** Python 3.12, FastAPI, aiohttp (through the existing
`session_factory` seam in `matter/otbr.py`), POSIX `sh` / `bash` scripts,
Alpine.js, pytest (with `node` for the web helpers).

**Spec:** `docs/superpowers/specs/2026-09-21-thread-network-on-a-fresh-installation-design.md`

**Worktree:** `/Users/lucienkerl/Development/matter-loxone/.claude/worktrees/thread-network-auto`
(branch `claude/thread-network-auto`). Every path below is relative to it.
Subagents: `cd` there first and use absolute paths; the main checkout is a
different branch that another session is working in.

## Global Constraints

- Everything in the repository is English; user-facing text goes through
  `src/loxmatter/i18n/strings.yaml` with an `en` and a `de` value, resolved with
  `i18n.t(...)` at call time.
- A Thread dataset is a credential. It never appears in a log line, an
  exception message, a test fixture that could be real, or the API response.
  Only the network name and channel may be shown.
- The bridge never forms a network without having asked matter-server first;
  any matter-server error in that decision ends the pass without writing.
- "Thread device" = a node whose attribute `0/29/1` (Descriptor ServerList on
  endpoint 0) contains `53`, or which has any attribute under `0/53/`.
- The keeper's pass interval is 60 s.
- `GET /api/radios` field `thread_network.state` is one of `unknown`,
  `formed`, `forming`, `missing`.
- Commit messages: Conventional Commits, English, ending with
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- The full test suite takes ~11 minutes and does not fit one Bash call. Run
  only the files a task names, in the foreground, and the other checks
  (`ruff check .`, `ruff format --check .`, `mypy`,
  `python scripts/check_language.py`) before every commit.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `scripts/otbr-watchdog.sh` (modify) | skip the restart when the agent is running but not configured |
| `tests/test_otbr_watchdog.py` (modify) | fake `ot-ctl dataset active`; the two new cases |
| `deploy/updater/radios-once.sh` (modify) | `verify_thread` accepts running but not configured |
| `tests/test_updater_radios_script.py` (modify) | fake mode `unconfigured`; the new cases |
| `src/loxmatter/matter/otbr.py` (modify) | REST calls: read dataset or none, role, create-if-absent, enable; network name from TLV |
| `tests/matter/test_otbr.py` (modify) | the new REST calls against `FakeSession` |
| `src/loxmatter/matter/thread_network.py` (create) | `ThreadNetworkKeeper`, `ThreadNetworkStatus`, `is_thread_node` |
| `tests/matter/test_thread_network.py` (create) | one pass per scenario of spec section 4.2 |
| `src/loxmatter/api/radios.py`, `src/loxmatter/loxone/server.py`, `src/loxmatter/cli.py` (modify) | pass the keeper through; report `thread_network`; start and stop the loop |
| `tests/api/test_radios_api.py` (modify) | `thread_network` in the response |
| `src/loxmatter/web/app.js`, `src/loxmatter/web/index.html`, `src/loxmatter/i18n/strings.yaml` (modify) | the card line and its texts |
| `tests/api/test_web.py` (modify) | `radiosThreadNetworkLine()` and the strings |
| `deploy/testhost/README.md`, `CHANGELOG.md` (modify) | restoring a lost dataset; the change notes |

---

### Task 1: The watchdog leaves an unconfigured border router alone

**Files:**
- Modify: `scripts/otbr-watchdog.sh` (after the `if thread_is_up; then exit 0; fi` block, before `AGE=""`)
- Test: `tests/test_otbr_watchdog.py`

**Interfaces:**
- Consumes: nothing.
- Produces: env var `DOCKER_OTCTL_DATASET` in the test fake (`missing` →
  `Error 23: NotFound`, anything else or unset → a present dataset).

- [ ] **Step 1: Teach the fake `docker` the dataset command**

In `tests/test_otbr_watchdog.py`, in `_DOCKER_STUB`, replace the `ot-ctl)`
branch of the `exec)` case:

```sh
        ot-ctl)
          case "$4" in
            dataset)
              if [ "${DOCKER_OTCTL_DATASET:-present}" = "missing" ]; then
                printf 'Error 23: NotFound\\r\\n'
              else
                printf 'Active Timestamp: 1\\r\\nDone\\r\\n'
              fi
              ;;
            *)
              if [ -e "${DOCKER_RESTARTED_MARKER:-/nonexistent}" ]; then
                printf '%s\\r\\nDone\\r\\n' "${DOCKER_OTCTL_STATE_AFTER_RESTART:-${DOCKER_OTCTL_STATE-leader}}"
              else
                printf '%s\\r\\nDone\\r\\n' "${DOCKER_OTCTL_STATE-leader}"
              fi
              ;;
          esac
          ;;
```

Add one sentence to the module docstring after the `DOCKER_OTCTL_STATE`
paragraph: "`DOCKER_OTCTL_DATASET=missing` makes `ot-ctl dataset active`
answer `Error 23: NotFound`, the measured answer of a border router that has
never formed a network; unset, it answers with a dataset."

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_otbr_watchdog.py`:

```python
def test_a_border_router_without_a_network_is_not_restarted(watchdog):
    """Spec 2026-09-21, section 3.1. Measured on pi3-andi on 21 September:
    `disabled` plus `Error 23: NotFound` is a healthy agent that nobody has
    given a network yet, and restarting it every 90 s only produced the
    symptoms of a broken stick. Silent, because the log holds incidents only.

    Fault to prove it: delete the `not_configured` check from the script."""
    proc, calls = watchdog(
        thread_up=False, DOCKER_OTCTL_STATE="disabled", DOCKER_OTCTL_DATASET="missing"
    )
    assert proc.returncode == 0
    assert not any(call.startswith("restart") for call in calls)
    assert "exec otbr ot-ctl dataset active" in calls
    assert proc.stdout == ""


def test_a_disabled_agent_with_a_dataset_is_still_restarted(watchdog):
    """A dataset that exists but did not attach is a fault; the auto-attach on
    restart is what fixes it. Fault to prove it: make `not_configured` look
    at the state alone."""
    proc, calls = watchdog(thread_up=False, DOCKER_OTCTL_STATE="disabled")
    assert any(call.startswith("restart") for call in calls)


def test_an_agent_that_does_not_answer_is_still_restarted(watchdog):
    """`ot-ctl` failing outright is not "not configured"."""
    proc, calls = watchdog(
        thread_up=False, DOCKER_EXEC_STATUS="1", DOCKER_OTCTL_DATASET="missing"
    )
    assert any(call.startswith("restart") for call in calls)
```

- [ ] **Step 3: Run them and watch the first one fail**

Run: `uv run pytest tests/test_otbr_watchdog.py -v -k "without_a_network or with_a_dataset or does_not_answer"`
Expected: `test_a_border_router_without_a_network_is_not_restarted` FAILS (a
restart is recorded); the other two pass.

- [ ] **Step 4: Implement the check**

In `scripts/otbr-watchdog.sh`, directly after the `if thread_is_up; then exit 0; fi`
block, insert:

```bash
# Running but not configured (design 2026-09-21, section 3): the agent
# answers, reports `disabled`, and holds no active dataset. That is a border
# router nobody has given a network yet - the bridge forms one - and a
# restart cannot change it. Measured on 21 September on a fresh Pi, where
# restarting every 90 s cut the stick off mid-boot (`spinel_driver.cpp:87:
# Failure`) and left stale pid files behind. Nothing is logged: the log holds
# incidents, and this is not one. The dataset itself is never printed; only
# the one error line is matched.
not_configured() {
  [ "$(bounded "$DOCKER_TIMEOUT" docker exec "$SERVICE" ot-ctl state 2>/dev/null | tr -d '\r' | head -n 1)" = "disabled" ] \
    || return 1
  bounded "$DOCKER_TIMEOUT" docker exec "$SERVICE" ot-ctl dataset active 2>/dev/null \
    | tr -d '\r' | grep -qx 'Error 23: NotFound'
}

if not_configured; then
  exit 0
fi
```

- [ ] **Step 5: Run the whole watchdog test file**

Run: `uv run pytest tests/test_otbr_watchdog.py tests/test_updater_watchdog_once.py -v`
Expected: all PASS.

- [ ] **Step 6: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run python scripts/check_language.py
git add scripts/otbr-watchdog.sh tests/test_otbr_watchdog.py
git commit -m "fix(watchdog): leave a border router alone that has no Thread network yet

A fresh installation's otbr answers 'disabled' and holds no dataset until
something forms a network. The watchdog restarted it every 90 seconds, which
cannot change that state and cut the stick off mid-boot.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Switching Thread on is not rolled back on a fresh installation

**Files:**
- Modify: `deploy/updater/radios-once.sh` (`verify_thread`, around line 477)
- Test: `tests/test_updater_radios_script.py`

**Interfaces:**
- Consumes: nothing.
- Produces: fake `thread_mode` value `unconfigured`.

- [ ] **Step 1: Teach the fake the mode and the dataset command**

In `DOCKER_STUB` in `tests/test_updater_radios_script.py`, inside the
`exec)` → `case "$*" in`, add a new branch **before** `*"ot-ctl state"*)`:

```sh
      *"ot-ctl dataset active"*)
        mode="$(cat "$FAKE/thread_mode" 2>/dev/null || echo leader)"
        if [ "$mode" = unconfigured ]; then echo 'Error 23: NotFound'; else printf 'Active Timestamp: 1\nDone\n'; fi ;;
```

and inside the `*"ot-ctl state"*)` branch's `case "$mode" in`, add before
`hang)`:

```sh
          unconfigured) echo disabled ;;
```

- [ ] **Step 2: Write the failing tests**

Append:

```python
def test_enabling_thread_on_a_border_router_without_a_network_succeeds(radios):
    """Spec 2026-09-21, section 3.2: on a fresh installation the agent comes up
    `disabled` with no dataset, and only the bridge forms the network. The
    verification used to wait 150 s for `leader` and roll Thread back off.

    Fault to prove it: remove the `disabled)` branch from `verify_thread`."""
    (radios.fake / "otbr_state").unlink()
    (radios.fake / "thread_mode").write_text("unconfigured")
    _request(radios)
    _, calls, state = radios()
    assert (state["phase"], state["error"]) == ("done", None)
    assert calls.count("rm -f /run/otbr-agent.pid") == 0
    assert "ot-ctl dataset active" in calls


def test_a_disabled_agent_with_a_dataset_still_fails_the_verification(radios):
    """`disabled` alone is not enough; a dataset that never attaches is the
    fault the verification exists for. The fake's `never` mode answers
    `detached`, so this uses a mode that answers `disabled` with a dataset:
    `unconfigured` for the state, but a present dataset."""
    (radios.fake / "thread_mode").write_text("disabled_with_dataset")
    _request(radios)
    _, _, state = radios()
    assert (state["phase"], state["error"]) == ("failed", "verify_thread_failed")
```

and add `disabled_with_dataset) echo disabled ;;` next to
`unconfigured) echo disabled ;;` in the stub's state branch (the dataset
branch already answers "present" for every mode but `unconfigured`).

- [ ] **Step 3: Run them and watch the first fail**

Run: `uv run pytest tests/test_updater_radios_script.py -v -k "without_a_network or still_fails_the_verification"`
Expected: the first FAILS with phase `failed`; the second passes.

- [ ] **Step 4: Implement**

In `deploy/updater/radios-once.sh`, replace the probe `case` inside
`verify_thread`'s loop:

```sh
    case "$(timeout "$PROBE_TIMEOUT" docker exec otbr ot-ctl state 2>/dev/null | tr -d '\r' | head -n 1)" in
      leader|router|child) return 0 ;;
      disabled)
        # Running but not configured (design 2026-09-21, section 3): a
        # healthy agent that holds no network yet. The bridge forms it; a
        # restart or a rollback cannot. Only the error line is matched -
        # the dataset is a credential and is never printed here.
        if timeout "$PROBE_TIMEOUT" docker exec otbr ot-ctl dataset active 2>/dev/null \
          | tr -d '\r' | grep -qx 'Error 23: NotFound'; then
          log "otbr is running with no Thread network yet - the bridge forms it"
          return 0
        fi
        ;;
    esac
```

- [ ] **Step 5: Run the radios script tests**

Run: `uv run pytest tests/test_updater_radios_script.py -v`
Expected: all PASS. (This file is slow; run it alone in the foreground.)

- [ ] **Step 6: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run python scripts/check_language.py
git add deploy/updater/radios-once.sh tests/test_updater_radios_script.py
git commit -m "fix(updater): switching Thread on succeeds before a network exists

verify_thread waited for leader/router/child, which a fresh border router
cannot reach until a network is formed, so the radios card rolled Thread
back off. A running agent with no dataset now counts as healthy.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: OTBR REST calls for reading, creating and enabling a network

**Files:**
- Modify: `src/loxmatter/matter/otbr.py`
- Test: `tests/matter/test_otbr.py`

**Interfaces:**
- Consumes: existing `validated_dataset`, `ThreadDatasetUnavailableError`,
  `_base_url`, `_default_session_factory`, i18n keys
  `api.errors.thread_dataset_unreachable` and
  `api.errors.thread_dataset_http_status`.
- Produces (all keyword `base_url: str | None = None, *, session_factory: Callable[[], Any] | None = None`):
  - `async def read_active_dataset(...) -> str | None` — 200 → validated hex, 204 → `None`, otherwise raises `ThreadDatasetUnavailableError`.
  - `async def border_router_role(...) -> str` — the JSON string from `GET /node/state`, e.g. `"disabled"`; raises `ThreadDatasetUnavailableError` otherwise.
  - `NetworkCreation = Literal["created", "exists", "busy"]`
  - `async def create_network_if_absent(...) -> NetworkCreation` — 201 created, 412 exists, 409 busy; anything else raises.
  - `async def enable_thread(...) -> None` — 200 ok; otherwise raises.
  - `def thread_network_name_from_dataset(dataset: str) -> str | None`

- [ ] **Step 1: Extend `FakeSession` with `put`**

In `tests/matter/test_otbr.py`, give `FakeSession` per-path answers and a
`put()`. Replace the class with:

```python
class FakeSession:
    """Stands in for `aiohttp.ClientSession` - `get()`, `put()` and `close()`.

    `status`/`body` answer every request unless `routes` names the method and
    path, in which case that `(status, body)` answers instead."""

    def __init__(self, status: int = 200, body: str = FAKE_DATASET) -> None:
        self.status = status
        self.body = body
        self.routes: dict[tuple[str, str], tuple[int, str]] = {}
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.puts: list[tuple[str, dict[str, str], str]] = []
        self.closed = False
        self.raise_on_get: Exception | None = None

    def _answer(self, method: str, url: str) -> FakeResponse:
        path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
        status, body = self.routes.get((method, path), (self.status, self.body))
        return FakeResponse(status, body)

    def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        self.requests.append((url, headers or {}))
        if self.raise_on_get is not None:
            raise self.raise_on_get
        return self._answer("GET", url)

    def put(self, url: str, data: str = "", headers: dict[str, str] | None = None) -> Any:
        self.puts.append((url, headers or {}, data))
        if self.raise_on_get is not None:
            raise self.raise_on_get
        return self._answer("PUT", url)

    async def close(self) -> None:
        self.closed = True
```

Run: `uv run pytest tests/matter/test_otbr.py -v` → all existing tests PASS.

- [ ] **Step 2: Write the failing tests**

Append to `tests/matter/test_otbr.py` (and add the new names to the
`from loxmatter.matter.otbr import (...)` list: `border_router_role`,
`create_network_if_absent`, `enable_thread`, `read_active_dataset`,
`thread_network_name_from_dataset`):

```python
# Channel 24, network name "OpenThread-07f0" - shaped like the dataset formed
# on pi3-andi on 21 September, with every key-bearing TLV left out.
NAMED_DATASET = "000300001803" + "030f" + "4f70656e5468726561642d30376630"


async def test_read_active_dataset_answers_none_while_no_network_exists() -> None:
    """ot-br-posix answers `GetDataset` with 204 No Content when no active
    dataset exists (`rest_web_server.cpp` at the pinned commit)."""
    session = FakeSession(status=204, body="")
    assert await read_active_dataset(session_factory=lambda: session) is None
    assert session.closed


async def test_read_active_dataset_returns_the_dataset() -> None:
    session = FakeSession()
    assert await read_active_dataset(session_factory=lambda: session) == FAKE_DATASET


@pytest.mark.parametrize("status", [409, 500])
async def test_read_active_dataset_raises_on_any_other_answer(status: int) -> None:
    session = FakeSession(status=status, body="")
    with pytest.raises(ThreadDatasetUnavailableError):
        await read_active_dataset(session_factory=lambda: session)


async def test_read_active_dataset_raises_when_unreachable() -> None:
    session = FakeSession()
    session.raise_on_get = OSError("connection refused")
    with pytest.raises(ThreadDatasetUnavailableError):
        await read_active_dataset(session_factory=lambda: session)


async def test_border_router_role_reads_the_json_string() -> None:
    session = FakeSession(body='"disabled"')
    assert await border_router_role(session_factory=lambda: session) == "disabled"
    url, headers = session.requests[0]
    assert url.endswith("/node/state")
    assert headers["Accept"] == "application/json"


@pytest.mark.parametrize(
    ("status", "outcome"), [(201, "created"), (412, "exists"), (409, "busy")]
)
async def test_create_network_if_absent_maps_the_three_answers(status: int, outcome: str) -> None:
    session = FakeSession(status=status, body="")
    assert await create_network_if_absent(session_factory=lambda: session) == outcome
    url, headers, data = session.puts[0]
    assert url.endswith("/node/dataset/active")
    # The atomic "only if none exists" - without it a second writer could
    # replace a network that devices already joined.
    assert headers["If-None-Match"] == "*"
    assert headers["Content-Type"] == "application/json"
    assert data == "{}"


async def test_create_network_if_absent_raises_on_anything_else() -> None:
    session = FakeSession(status=500, body="")
    with pytest.raises(ThreadDatasetUnavailableError):
        await create_network_if_absent(session_factory=lambda: session)


async def test_enable_thread_puts_enable() -> None:
    session = FakeSession(status=200, body="")
    await enable_thread(session_factory=lambda: session)
    url, headers, data = session.puts[0]
    assert url.endswith("/node/state")
    assert data == '"enable"'


async def test_enable_thread_raises_on_a_refusal() -> None:
    session = FakeSession(status=409, body="")
    with pytest.raises(ThreadDatasetUnavailableError):
        await enable_thread(session_factory=lambda: session)


def test_the_network_name_is_read_from_its_tlv() -> None:
    assert thread_network_name_from_dataset(NAMED_DATASET) == "OpenThread-07f0"
    assert thread_channel_from_dataset(NAMED_DATASET) == 24


@pytest.mark.parametrize("dataset", ["", "zz", "000300001803", "0f10" + "41" * 3])
def test_a_missing_or_broken_name_tlv_reads_as_none(dataset: str) -> None:
    assert thread_network_name_from_dataset(dataset) is None
```

- [ ] **Step 3: Run and watch them fail**

Run: `uv run pytest tests/matter/test_otbr.py -v`
Expected: ImportError / FAIL for the new names.

- [ ] **Step 4: Implement**

In `src/loxmatter/matter/otbr.py`:

1. Add `import json` and `from typing import Any, Final, Literal`.
2. Next to `_ACTIVE_DATASET_PATH` add:

```python
_NODE_STATE_PATH: Final = "/node/state"
_JSON: Final = {"Accept": "application/json", "Content-Type": "application/json"}
_NETWORK_NAME_TLV_TYPE: Final = 0x03

NetworkCreation = Literal["created", "exists", "busy"]
```

3. Add one request helper above `fetch_active_dataset` and use it there too
   (the body of `fetch_active_dataset` becomes: build `url`, then
   `status, body = await _request("GET", url, dict(_PLAIN_TEXT), None, session_factory)`,
   then the existing `status != 200` check and `validated_dataset`):

```python
async def _request(
    method: str,
    url: str,
    headers: dict[str, str],
    data: str | None,
    session_factory: Callable[[], Any] | None,
) -> tuple[int, str]:
    """One HTTP exchange with the border router, as `(status, body)`.

    An unreachable border router raises `ThreadDatasetUnavailableError` with
    the address and the exception, never with a body - the body of a dataset
    answer is a credential (see `fetch_active_dataset`)."""
    session = (session_factory or _default_session_factory)()
    try:
        try:
            if method == "GET":
                call = session.get(url, headers=headers)
            else:
                call = session.put(url, data=data or "", headers=headers)
            async with call as response:
                return response.status, await response.text()
        except Exception as exc:
            # `Exception` and not just `aiohttp.ClientError`: this module
            # deliberately does not import aiohttp itself (see
            # `_default_session_factory`).
            raise ThreadDatasetUnavailableError(
                i18n.t("api.errors.thread_dataset_unreachable", url=url, exc=exc)
            ) from exc
    finally:
        await session.close()


def _url(base_url: str | None, path: str) -> str:
    return (base_url or _base_url()).rstrip("/") + path


def _unexpected(url: str, status: int) -> ThreadDatasetUnavailableError:
    return ThreadDatasetUnavailableError(
        i18n.t("api.errors.thread_dataset_http_status", url=url, status=status)
    )
```

4. Append the four calls and the name reader:

```python
async def read_active_dataset(
    base_url: str | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> str | None:
    """The active dataset, or `None` when the border router holds none.

    Unlike `fetch_active_dataset`, "no network" is an answer here, not an
    error: ot-br-posix replies 204 No Content to `GET /node/dataset/active`
    while no active dataset exists (design 2026-09-21, section 3)."""
    url = _url(base_url, _ACTIVE_DATASET_PATH)
    status, body = await _request("GET", url, dict(_PLAIN_TEXT), None, session_factory)
    if status == 204:
        return None
    if status != 200:
        raise _unexpected(url, status)
    return validated_dataset(body, url)


async def border_router_role(
    base_url: str | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> str:
    """The Thread role, e.g. `"disabled"`, `"detached"`, `"leader"`."""
    url = _url(base_url, _NODE_STATE_PATH)
    status, body = await _request("GET", url, {"Accept": "application/json"}, None, session_factory)
    if status != 200:
        raise _unexpected(url, status)
    try:
        role = json.loads(body)
    except ValueError:
        raise _unexpected(url, status) from None
    if not isinstance(role, str):
        raise _unexpected(url, status)
    return role


async def create_network_if_absent(
    base_url: str | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> NetworkCreation:
    """Form a new Thread network with random values - only if none exists.

    `If-None-Match: *` makes ot-br-posix check "no active dataset" and write
    the new one in the same main-loop task, so a network that appeared a
    moment ago is never replaced. The empty JSON object leaves every field to
    `otDatasetCreateNewNetwork`. 412: a dataset exists now. 409: the agent is
    no longer `disabled`."""
    url = _url(base_url, _ACTIVE_DATASET_PATH)
    headers = {**_JSON, "If-None-Match": "*"}
    status, _ = await _request("PUT", url, headers, "{}", session_factory)
    if status == 201:
        return "created"
    if status == 412:
        return "exists"
    if status == 409:
        return "busy"
    raise _unexpected(url, status)


async def enable_thread(
    base_url: str | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> None:
    """`ifconfig up` plus `thread start`, through the REST API."""
    url = _url(base_url, _NODE_STATE_PATH)
    status, _ = await _request("PUT", url, dict(_JSON), '"enable"', session_factory)
    if status != 200:
        raise _unexpected(url, status)


def thread_network_name_from_dataset(dataset: str) -> str | None:
    """The Network Name TLV (type 3, UTF-8, at most 16 bytes), or `None`.

    Same tolerance as `thread_channel_from_dataset`: anything it cannot make
    sense of reads as "no name", never as an error. The name is not a secret;
    the rest of the dataset is, and is never returned or logged."""
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
        if tlv_type == _NETWORK_NAME_TLV_TYPE:
            if not 0 < length <= 16:
                return None
            try:
                return raw[value_start:value_end].decode("utf-8")
            except UnicodeDecodeError:
                return None
        index = value_end
    return None
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/matter/test_otbr.py tests/matter/test_client_commissioning.py -v`
Expected: all PASS (the second file covers `fetch_active_dataset`'s callers).

- [ ] **Step 6: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/matter/otbr.py tests/matter/test_otbr.py
git commit -m "feat(otbr): read, create and enable a Thread network through the REST API

The building blocks for forming a network on a fresh installation:
'no dataset' as an answer rather than an error, the agent's role, an atomic
create-only-if-absent, and the network name for display.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `ThreadNetworkKeeper`

**Files:**
- Create: `src/loxmatter/matter/thread_network.py`
- Test: `tests/matter/test_thread_network.py`

**Interfaces:**
- Consumes (Task 3): `read_active_dataset`, `border_router_role`,
  `create_network_if_absent`, `enable_thread`,
  `thread_network_name_from_dataset`, `thread_channel_from_dataset`,
  `ThreadDatasetUnavailableError`; from `matter/client.py`:
  `MatterUnavailableError`; from `matter/models.py`: `NodeSnapshot`.
- Produces:
  - `ThreadNetworkState = Literal["unknown", "formed", "forming", "missing"]`
  - `@dataclass(frozen=True) class ThreadNetworkStatus: state: ThreadNetworkState = "unknown"; name: str | None = None; channel: int | None = None; thread_devices: int = 0; def as_json(self) -> dict[str, object]`
  - `def is_thread_node(snapshot: NodeSnapshot) -> bool`
  - `class ThreadMatterClient(Protocol)`: `thread_dataset_set: bool` (property), `async set_thread_dataset(dataset: str) -> None`, `async snapshots() -> list[NodeSnapshot]`
  - `class ThreadNetworkKeeper(client, *, base_url=None, session_factory=None, interval=60.0, sleep=asyncio.sleep)` with `status: ThreadNetworkStatus` (property), `async run_pass() -> bool` (True = done, stop), `async run() -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/matter/test_thread_network.py`:

```python
# (license header: copy the 15-line GPL header from tests/matter/test_otbr.py)

"""Forming a Thread network on a fresh installation (design 2026-09-21,
section 4) - one pass of `ThreadNetworkKeeper` per scenario.

The border router is `FakeOtbr`, a `session_factory` that answers by method
and path; matter-server is `FakeMatter`. Every test states what was written
to either, because "nothing was written" is the claim of half of them."""

from __future__ import annotations

from typing import Any, Self

import pytest

from loxmatter.matter.client import MatterUnavailableError
from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.thread_network import (
    ThreadNetworkKeeper,
    ThreadNetworkStatus,
    is_thread_node,
)

# Channel 24, name "OpenThread-07f0", no key-bearing TLV (see test_otbr.py).
DATASET = "000300001803" + "030f" + "4f70656e5468726561642d30376630"


class _Response:
    def __init__(self, status: int, body: str) -> None:
        self.status, self._body = status, body

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def text(self) -> str:
        return self._body


class FakeOtbr:
    """A border router: `dataset` None = no network, `role` its Thread role.
    A successful create stores `created_dataset`; enable sets `leader`."""

    def __init__(self, *, dataset: str | None = None, role: str = "disabled") -> None:
        self.dataset = dataset
        self.role = role
        self.created_dataset = DATASET
        self.create_status: int | None = None  # override the PUT's answer
        self.unreachable = False
        self.puts: list[tuple[str, dict[str, str], str]] = []

    def __call__(self) -> FakeOtbr:
        return self

    def _path(self, url: str) -> str:
        return "/" + url.split("://", 1)[-1].split("/", 1)[-1]

    def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        if self.unreachable:
            raise OSError("connection refused")
        path = self._path(url)
        if path == "/node/dataset/active":
            return _Response(204, "") if self.dataset is None else _Response(200, self.dataset)
        if path == "/node/state":
            return _Response(200, f'"{self.role}"')
        return _Response(404, "")

    def put(self, url: str, data: str = "", headers: dict[str, str] | None = None) -> Any:
        if self.unreachable:
            raise OSError("connection refused")
        self.puts.append((self._path(url), headers or {}, data))
        path = self._path(url)
        if path == "/node/dataset/active":
            if self.create_status is not None:
                return _Response(self.create_status, "")
            if self.dataset is not None:
                return _Response(412, "")
            self.dataset = self.created_dataset
            return _Response(201, "")
        if path == "/node/state":
            self.role = "leader"
            return _Response(200, "")
        return _Response(404, "")

    async def close(self) -> None:
        return None


class FakeMatter:
    def __init__(
        self,
        *,
        nodes: list[NodeSnapshot] | None = None,
        credentials_set: bool = False,
        unavailable: bool = False,
    ) -> None:
        self.nodes = nodes or []
        self.credentials_set = credentials_set
        self.unavailable = unavailable
        self.datasets_set: list[str] = []

    @property
    def thread_dataset_set(self) -> bool:
        return self.credentials_set

    async def set_thread_dataset(self, dataset: str) -> None:
        if self.unavailable:
            raise MatterUnavailableError("down")
        self.datasets_set.append(dataset)
        self.credentials_set = True

    async def snapshots(self) -> list[NodeSnapshot]:
        if self.unavailable:
            raise MatterUnavailableError("down")
        return list(self.nodes)


def _node(address: str, attributes: dict[str, Any]) -> NodeSnapshot:
    return NodeSnapshot(
        technology="matter",
        address=address,
        vendor_name="",
        product_name="",
        unique_id=address,
        attributes=attributes,
    )


# The IKEA switch commissioned on pi3-andi: its root server list includes 53.
THREAD_NODE = _node("3", {"0/29/1": [29, 31, 40, 42, 47, 48, 49, 51, 53, 60]})
WIFI_NODE = _node("4", {"0/29/1": [29, 31, 40, 48, 49, 51, 54, 60]})


def _keeper(otbr: FakeOtbr, matter: FakeMatter) -> ThreadNetworkKeeper:
    return ThreadNetworkKeeper(matter, base_url="http://otbr.test:8081", session_factory=otbr)


def test_a_node_is_a_thread_node_by_its_server_list_or_its_attributes() -> None:
    assert is_thread_node(THREAD_NODE)
    assert is_thread_node(_node("5", {"0/53/0": 24}))
    assert not is_thread_node(WIFI_NODE)
    assert not is_thread_node(_node("6", {}))


async def test_a_fresh_border_router_gets_a_network_that_matter_server_learns() -> None:
    otbr, matter = FakeOtbr(), FakeMatter()
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is True

    create, enable = otbr.puts
    assert create[0] == "/node/dataset/active"
    assert create[1]["If-None-Match"] == "*"
    assert create[2] == "{}"
    assert enable == ("/node/state", enable[1], '"enable"')
    assert matter.datasets_set == [DATASET]
    assert keeper.status == ThreadNetworkStatus(state="formed", name="OpenThread-07f0", channel=24)


async def test_an_existing_network_is_handed_to_matter_server_without_writing() -> None:
    otbr, matter = FakeOtbr(dataset=DATASET, role="leader"), FakeMatter()
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is True

    assert otbr.puts == []
    assert matter.datasets_set == [DATASET]
    assert keeper.status.state == "formed"


async def test_nothing_is_written_when_both_sides_already_know_the_network() -> None:
    otbr = FakeOtbr(dataset=DATASET, role="leader")
    matter = FakeMatter(credentials_set=True)

    assert await _keeper(otbr, matter).run_pass() is True

    assert otbr.puts == []
    assert matter.datasets_set == []


async def test_thread_devices_without_a_network_block_forming() -> None:
    """The guard, spec 4.2 step 3 / 4.3. Fault to prove it: drop the
    `is_thread_node` count from `run_pass`."""
    otbr, matter = FakeOtbr(), FakeMatter(nodes=[THREAD_NODE, WIFI_NODE])
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is False

    assert otbr.puts == []
    assert matter.datasets_set == []
    assert keeper.status == ThreadNetworkStatus(state="missing", thread_devices=1)


async def test_only_wifi_devices_do_not_block_forming() -> None:
    otbr, matter = FakeOtbr(), FakeMatter(nodes=[WIFI_NODE], credentials_set=True)

    assert await _keeper(otbr, matter).run_pass() is True

    assert len(otbr.puts) == 2
    # matter-server's old credentials strand nothing and are replaced.
    assert matter.datasets_set == [DATASET]


async def test_a_blocked_keeper_turns_formed_once_the_dataset_is_restored() -> None:
    otbr, matter = FakeOtbr(), FakeMatter(nodes=[THREAD_NODE])
    keeper = _keeper(otbr, matter)
    assert await keeper.run_pass() is False

    otbr.dataset, otbr.role = DATASET, "leader"  # restored by hand

    assert await keeper.run_pass() is True
    assert keeper.status.state == "formed"
    assert otbr.puts == []


@pytest.mark.parametrize(("status", "role"), [(412, "disabled"), (409, "detached")])
async def test_losing_the_race_is_not_an_error(status: int, role: str) -> None:
    otbr, matter = FakeOtbr(), FakeMatter()
    otbr.create_status = status
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is False

    assert [put[0] for put in otbr.puts] == ["/node/dataset/active"]
    assert matter.datasets_set == []
    assert keeper.status.state == "forming"


async def test_a_busy_agent_is_left_alone() -> None:
    otbr, matter = FakeOtbr(role="detached"), FakeMatter()

    assert await _keeper(otbr, matter).run_pass() is False

    assert otbr.puts == []


async def test_an_unreachable_border_router_changes_nothing() -> None:
    otbr, matter = FakeOtbr(), FakeMatter()
    otbr.unreachable = True
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is False

    assert matter.datasets_set == []
    assert keeper.status == ThreadNetworkStatus()


async def test_an_unreachable_matter_server_means_no_network_is_formed() -> None:
    """"The bridge never forms a network without having asked matter-server
    first" (spec 4.2). Fault to prove it: treat the exception as "no nodes"."""
    otbr, matter = FakeOtbr(), FakeMatter(unavailable=True)

    assert await _keeper(otbr, matter).run_pass() is False

    assert otbr.puts == []


async def test_run_repeats_until_a_pass_is_done() -> None:
    otbr, matter = FakeOtbr(), FakeMatter(nodes=[THREAD_NODE])
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            otbr.dataset, otbr.role = DATASET, "leader"

    keeper = ThreadNetworkKeeper(matter, session_factory=otbr, interval=60.0, sleep=sleep)
    await keeper.run()

    assert sleeps == [60.0, 60.0]
    assert keeper.status.state == "formed"


def test_the_json_shape_carries_no_dataset() -> None:
    status = ThreadNetworkStatus(state="formed", name="OpenThread-07f0", channel=24)
    assert status.as_json() == {
        "state": "formed",
        "name": "OpenThread-07f0",
        "channel": 24,
        "thread_devices": 0,
    }
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/matter/test_thread_network.py -v`
Expected: `ModuleNotFoundError: loxmatter.matter.thread_network`.

- [ ] **Step 3: Implement**

Create `src/loxmatter/matter/thread_network.py` (GPL header copied from
`src/loxmatter/matter/otbr.py`):

```python
"""Forming the Thread network on a fresh installation.

Design 2026-09-21 ("A fresh installation forms its own Thread network"). On
21 September a freshly installed Pi ran for hours with a healthy border
router and no Thread network: nothing in the product had ever created a
dataset, and matter-server cannot commission a Thread device without one.
This module forms that network - through OTBR's REST API, the same channel
`matter/otbr.py` already reads the dataset through - and hands it to
matter-server.

**The one thing it must never do** is replace a network devices already
live in. A border router that lost its dataset (a removed `otbr-state`
volume) looks exactly like a fresh one. The difference is on matter-server's
side: Thread devices it knows. So a pass forms a network only after
matter-server has answered, and only if none of its nodes is a Thread device.
Credentials matter-server holds without any Thread node strand nothing -
`pi3-andi` kept a dataset from an earlier installation in its data directory
and had no nodes at all - and are replaced by the new network's.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol

from loxmatter.matter.client import MatterUnavailableError
from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.otbr import (
    ThreadDatasetUnavailableError,
    border_router_role,
    create_network_if_absent,
    enable_thread,
    read_active_dataset,
    thread_channel_from_dataset,
    thread_network_name_from_dataset,
)

logger = logging.getLogger(__name__)

PASS_INTERVAL_SECONDS: Final = 60.0
THREAD_DIAGNOSTICS_CLUSTER: Final = 0x0035
_ROOT_SERVER_LIST: Final = "0/29/1"
_THREAD_DIAGNOSTICS_PREFIX: Final = f"0/{THREAD_DIAGNOSTICS_CLUSTER}/"

ThreadNetworkState = Literal["unknown", "formed", "forming", "missing"]


@dataclass(frozen=True)
class ThreadNetworkStatus:
    """What the radios card shows. Never the dataset - only its name and
    channel, which every Thread device near the house advertises anyway."""

    state: ThreadNetworkState = "unknown"
    name: str | None = None
    channel: int | None = None
    thread_devices: int = 0

    def as_json(self) -> dict[str, object]:
        return {
            "state": self.state,
            "name": self.name,
            "channel": self.channel,
            "thread_devices": self.thread_devices,
        }


class ThreadMatterClient(Protocol):
    @property
    def thread_dataset_set(self) -> bool: ...

    async def set_thread_dataset(self, dataset: str) -> None: ...

    async def snapshots(self) -> list[NodeSnapshot]: ...


def is_thread_node(snapshot: NodeSnapshot) -> bool:
    """A node that reaches this bridge over Thread: its endpoint 0 serves
    Thread Network Diagnostics. Read from the root Descriptor's server list,
    or - for a snapshot without it - from any attribute of that cluster."""
    server_list = snapshot.attributes.get(_ROOT_SERVER_LIST)
    if isinstance(server_list, list) and THREAD_DIAGNOSTICS_CLUSTER in server_list:
        return True
    return any(key.startswith(_THREAD_DIAGNOSTICS_PREFIX) for key in snapshot.attributes)


def _formed(dataset: str) -> ThreadNetworkStatus:
    return ThreadNetworkStatus(
        state="formed",
        name=thread_network_name_from_dataset(dataset),
        channel=thread_channel_from_dataset(dataset),
    )


class ThreadNetworkKeeper:
    """Runs until a network exists and matter-server has it (design section
    4.1), one pass per `interval`. The guarded case keeps it running, so a
    dataset restored by hand turns up in a later pass."""

    def __init__(
        self,
        client: ThreadMatterClient,
        *,
        base_url: str | None = None,
        session_factory: Callable[[], Any] | None = None,
        interval: float = PASS_INTERVAL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._otbr: dict[str, Any] = {"base_url": base_url, "session_factory": session_factory}
        self._interval = interval
        self._sleep = sleep
        self._status = ThreadNetworkStatus()

    @property
    def status(self) -> ThreadNetworkStatus:
        return self._status

    async def run(self) -> None:
        while not await self.run_pass():
            await self._sleep(self._interval)

    async def run_pass(self) -> bool:
        """One pass of design section 4.2. `True` when there is nothing left
        to do. Never raises for a border router or matter-server that is
        away; the next pass tries again."""
        try:
            dataset = await read_active_dataset(**self._otbr)
        except ThreadDatasetUnavailableError:
            # Thread off, otbr starting, or no border router at all - the
            # same quiet "not now" every installation without Thread sees
            # once a minute. A `missing` finding stays until it is resolved.
            if self._status.state != "missing":
                self._status = ThreadNetworkStatus()
            return False

        if dataset is not None:
            self._status = _formed(dataset)
            return await self._hand_over(dataset)

        try:
            role = await border_router_role(**self._otbr)
        except ThreadDatasetUnavailableError:
            return False
        if role != "disabled":
            return False

        try:
            nodes = await self._client.snapshots()
        except MatterUnavailableError:
            return False
        thread_devices = sum(1 for node in nodes if is_thread_node(node))
        if thread_devices:
            self._status = ThreadNetworkStatus(state="missing", thread_devices=thread_devices)
            return False

        self._status = ThreadNetworkStatus(state="forming")
        try:
            outcome = await create_network_if_absent(**self._otbr)
            if outcome != "created":
                # Someone else was faster (412) or the agent left `disabled`
                # (409). The next pass reads whatever network exists now.
                return False
            await enable_thread(**self._otbr)
            dataset = await read_active_dataset(**self._otbr)
        except ThreadDatasetUnavailableError as exc:
            logger.warning("Could not form a Thread network: %s", exc)
            return False
        if dataset is None:
            return False

        self._status = _formed(dataset)
        logger.info(
            "Formed Thread network %s on channel %s", self._status.name, self._status.channel
        )
        return await self._hand_over(dataset, force=True)

    async def _hand_over(self, dataset: str, *, force: bool = False) -> bool:
        """Give matter-server the dataset unless it confirms it has one.
        `force` after forming: whatever it held before is not this network."""
        if not force and self._client.thread_dataset_set:
            return True
        try:
            await self._client.set_thread_dataset(dataset)
        except MatterUnavailableError:
            return False
        return True
```

Note on the log messages: they are operator log lines, like the other
`logger.warning` calls in `matter/`, not text shown in the web UI; they carry
the network name and channel only.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/matter/test_thread_network.py -v`
Expected: all PASS.

`test_losing_the_race_is_not_an_error` with `(409, "detached")`: the fake's
role stays `disabled` during the pass, the `PUT` answers 409; the pass returns
`False` with state `forming`. That is the intended behaviour (the next pass
resolves it).

- [ ] **Step 5: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/matter/thread_network.py tests/matter/test_thread_network.py
git commit -m "feat(matter): form the Thread network on a fresh installation

A background keeper creates a network through OTBR when none exists and hands
it to matter-server. It forms nothing while matter-server knows Thread
devices, because a lost network looks exactly like a fresh border router.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Run the keeper and report it in `GET /api/radios`

**Files:**
- Modify: `src/loxmatter/api/radios.py` (`build_radios_router` signature and `get_radios` body)
- Modify: `src/loxmatter/loxone/server.py` (`build_app` parameter, the `build_radios_router(...)` call around line 541)
- Modify: `src/loxmatter/cli.py` (`_run`, around lines 809-915)
- Test: `tests/api/test_radios_api.py`

**Interfaces:**
- Consumes (Task 4): `ThreadNetworkKeeper`, `ThreadNetworkStatus`.
- Produces: `build_app(..., thread_network: ThreadNetworkKeeper | None = None)`;
  `build_radios_router(..., thread_network: ThreadNetworkKeeper | None = None)`;
  JSON field `thread_network` = `ThreadNetworkStatus.as_json()` on every
  `GET /api/radios` answer.

- [ ] **Step 1: Write the failing tests**

In `tests/api/test_radios_api.py`, extend the `api` fixture to accept an
optional keeper through a module-level holder, or add a second fixture.
Add a second fixture right after `api`:

```python
class _StatusOnly:
    """Stands in for `ThreadNetworkKeeper` - the router reads `status` only."""

    def __init__(self, status: ThreadNetworkStatus) -> None:
        self.status = status


@pytest.fixture
async def api_with_network(tmp_path, no_invoke, fake_runtime):
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    host_dev, sys_root = _host(tmp_path)
    store = Store(tmp_path / "t.sqlite")
    keeper = _StatusOnly(ThreadNetworkStatus())
    app = build_app(
        store,
        no_invoke,
        fake_runtime(store),
        update_dir=update_dir,
        radios_host_dev=host_dev,
        radios_sys_root=sys_root,
        thread_network=keeper,  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await authenticate(store, client)
        yield client, keeper
    store.close()
```

(import `from loxmatter.matter.thread_network import ThreadNetworkStatus`)

and the tests:

```python
async def test_without_a_keeper_the_thread_network_reads_unknown(api):
    client, _ = api
    body = (await client.get("/api/radios")).json()
    assert body["thread_network"] == {
        "state": "unknown", "name": None, "channel": None, "thread_devices": 0
    }


@pytest.mark.parametrize(
    "status",
    [
        ThreadNetworkStatus(state="formed", name="OpenThread-07f0", channel=24),
        ThreadNetworkStatus(state="forming"),
        ThreadNetworkStatus(state="missing", thread_devices=2),
    ],
)
async def test_the_keepers_status_is_reported_as_it_is_now(api_with_network, status):
    client, keeper = api_with_network
    keeper.status = status
    body = (await client.get("/api/radios")).json()
    assert body["thread_network"] == status.as_json()
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/api/test_radios_api.py -v -k "thread_network or keepers_status"`
Expected: FAIL (`KeyError: 'thread_network'` / unexpected keyword argument).

- [ ] **Step 3: Implement the router and `build_app`**

`src/loxmatter/api/radios.py`: add the parameter
`thread_network: ThreadNetworkKeeper | None = None` after `clock` (import the
class under `TYPE_CHECKING` if the module already uses that pattern, else
directly), and in `get_radios()` add to the returned dict:

```python
            # Design 2026-09-21, section 5: the bridge's own view of the Thread
            # network, next to the sidecar's view of the container. Read
            # fresh on every call - the keeper updates it once a minute.
            "thread_network": (
                thread_network.status if thread_network is not None else ThreadNetworkStatus()
            ).as_json(),
```

`src/loxmatter/loxone/server.py`: add `thread_network: ThreadNetworkKeeper | None = None`
to `build_app`'s keyword parameters (after `zigbee_runtime`), and pass
`thread_network=thread_network` into the `build_radios_router(...)` call.

- [ ] **Step 4: Run the radios API tests**

Run: `uv run pytest tests/api/test_radios_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Start and stop the loop in `cli._run`**

In `src/loxmatter/cli.py`, import `ThreadNetworkKeeper`. After
`zigbee_runtime.supervise_current()`:

```python
        # Design 2026-09-21: forms the Thread network on a fresh installation.
        # Every installation runs it; without a border router each pass is one
        # refused connection to 127.0.0.1:8081 a minute, and nothing else.
        thread_network = ThreadNetworkKeeper(client)
        thread_network_task: asyncio.Task[None] | None = asyncio.ensure_future(
            thread_network.run()
        )
```

Declare `thread_network_task: asyncio.Task[None] | None = None` next to
`supervisor_tasks: list[asyncio.Task[None]] = []` (and drop the annotation on
the assignment above), pass `thread_network=thread_network` to `build_app(...)`,
and in the `finally:` block, directly before the `for supervisor_task in supervisor_tasks:`
loop:

```python
        if thread_network_task is not None:
            thread_network_task.cancel()
            try:
                await thread_network_task
            except asyncio.CancelledError:
                # Same distinction as the supervisor loop below: our own
                # cancel() is expected; a cancellation of `_run` itself must
                # keep travelling.
                if not thread_network_task.cancelled():
                    raise
            except Exception:
                logger.exception("The Thread network keeper ended with an error")
```

- [ ] **Step 6: Run the CLI tests that exercise `_run`**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all PASS. If a test's fake client lacks `snapshots` or
`thread_dataset_set`, the keeper's first pass fails only if the border router
answers - it does not in tests (nothing listens on 127.0.0.1:8081), so the pass
ends at step 1. If a test does fail because of the new task, give that
test's fake the two members rather than special-casing the keeper.

- [ ] **Step 7: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
git add src/loxmatter/api/radios.py src/loxmatter/loxone/server.py src/loxmatter/cli.py tests/api/test_radios_api.py
git commit -m "feat(radios): run the Thread network keeper and report its state

loxmatter run starts the keeper beside the source supervisors, and
GET /api/radios carries its state, network name and channel so the radios
card can show them.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The radios card shows the Thread network

**Files:**
- Modify: `src/loxmatter/web/app.js` (next to `radiosThreadStatus()`, around line 5059)
- Modify: `src/loxmatter/web/index.html` (after the `radiosThreadStatus()` template, around line 2703)
- Modify: `src/loxmatter/i18n/strings.yaml` (after `web.radios.thread_status_not_running`)
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes (Task 5): `radios.thread_network` in the `GET /api/radios` body.
- Produces: `radiosThreadNetworkLine()` → `null` or
  `{ key: string, params: object, warn: boolean }`.

- [ ] **Step 1: Write the failing tests**

In `tests/api/test_web.py`, after
`test_radios_thread_status_follows_the_current_report_and_hides_during_a_job`:

```python
@pytest.mark.skipif(NODE is None, reason="node is required for this test")
def test_radios_thread_network_line_follows_the_keeper_state():
    """Design 2026-09-21, section 5. Hidden while Thread is off, during a job,
    and for `unknown`; `missing` is the one warning.

    Fault to prove it: return the `formed` line for `missing`."""
    values = _radios_values(
        """
        const out = {};
        state.radios.current = { thread_enabled: true, otbr_running: true };
        state.radios.thread_network = { state: 'unknown', name: null, channel: null, thread_devices: 0 };
        out.unknown = state.radiosThreadNetworkLine();

        state.radios.thread_network = { state: 'formed', name: 'OpenThread-07f0', channel: 24, thread_devices: 0 };
        out.formed = state.radiosThreadNetworkLine();

        state.radios.thread_network = { state: 'forming', name: null, channel: null, thread_devices: 0 };
        out.forming = state.radiosThreadNetworkLine();

        state.radios.thread_network = { state: 'missing', name: null, channel: null, thread_devices: 2 };
        out.missing = state.radiosThreadNetworkLine();

        state.radios.current = { thread_enabled: false, otbr_running: false };
        out.threadOff = state.radiosThreadNetworkLine();

        state.radios.current = { thread_enabled: true, otbr_running: true };
        delete state.radios.thread_network;
        out.olderBridge = state.radiosThreadNetworkLine();

        state.radios.thread_network = { state: 'formed', name: 'x', channel: 11, thread_devices: 0 };
        state.radios.job = { id: 'j', phase: 'apply_thread',
                              steps: ['validate','backup','write','apply_thread','verify_thread'],
                              error: null, rolled_back: false, healthy: null };
        out.duringJob = state.radiosThreadNetworkLine();

        console.log(JSON.stringify(out));
        """
    )
    assert values["unknown"] is None
    assert values["formed"] == {
        "key": "web.radios.thread_network_formed",
        "params": {"name": "OpenThread-07f0", "channel": 24},
        "warn": False,
    }
    assert values["forming"] == {"key": "web.radios.thread_network_forming", "params": {}, "warn": False}
    assert values["missing"] == {
        "key": "web.radios.thread_network_missing",
        "params": {"count": 2},
        "warn": True,
    }
    assert values["threadOff"] is None
    assert values["olderBridge"] is None
    assert values["duringJob"] is None


def test_the_three_thread_network_strings_exist_in_both_languages():
    """Named explicitly, like the thread-off-after-rollback keys above, so a
    typo'd key fails here. Fault to prove it: delete one `de:` line."""
    from loxmatter import i18n

    keys = i18n.strings_with_prefix("web.radios.")
    for key in (
        "web.radios.thread_network_formed",
        "web.radios.thread_network_forming",
        "web.radios.thread_network_missing",
    ):
        assert key in keys, key
        entry = i18n._STRINGS[key]
        assert entry.get("en") and entry.get("de"), key
        assert entry["en"] != entry["de"]


async def test_the_card_renders_the_thread_network_line(api):
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert 'x-if="radiosThreadNetworkLine()"' in markup
    assert "t(radiosThreadNetworkLine().key, radiosThreadNetworkLine().params)" in markup
```


- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/api/test_web.py -v -k "thread_network"`
Expected: FAIL (`radiosThreadNetworkLine is not a function`, missing keys,
missing markup).

- [ ] **Step 3: Add the helper**

In `src/loxmatter/web/app.js`, directly after `radiosThreadStatus() { ... },`:

```js
    /** The bridge's own view of the Thread network (design 2026-09-21,
     * section 5) - below the Thread state line, only while Thread is on and
     * no job is running. `unknown` shows nothing: it is also what a bridge
     * without a border router reports, once a minute, forever. A bridge from
     * before this field reports no `thread_network` at all. */
    radiosThreadNetworkLine() {
      const current = this.radios?.current;
      const network = this.radios?.thread_network;
      if (!current || !current.thread_enabled || !network || this.radiosJobRunning()) return null;
      if (network.state === "formed") {
        return {
          key: "web.radios.thread_network_formed",
          params: { name: network.name ?? "-", channel: network.channel ?? "-" },
          warn: false,
        };
      }
      if (network.state === "forming") {
        return { key: "web.radios.thread_network_forming", params: {}, warn: false };
      }
      if (network.state === "missing") {
        return { key: "web.radios.thread_network_missing", params: { count: network.thread_devices }, warn: true };
      }
      return null;
    },
```

- [ ] **Step 4: Add the markup**

In `src/loxmatter/web/index.html`, directly after the closing `</template>`
of the `radiosThreadStatus()` block:

```html
              <!-- The Thread network the bridge formed or found (design
                   2026-09-21, section 5). `x-if` for the same reason as the
                   state line above: the helper returns `null`. -->
              <template x-if="radiosThreadNetworkLine()">
                <p
                  :class="radiosThreadNetworkLine().warn ? 'banner warn' : 'hint radios-row-hint'"
                  x-text="t(radiosThreadNetworkLine().key, radiosThreadNetworkLine().params)"
                ></p>
              </template>
```

- [ ] **Step 5: Add the strings**

In `src/loxmatter/i18n/strings.yaml`, after `web.radios.thread_status_not_running`:

```yaml
web.radios.thread_network_formed:
  en: "Thread network {name}, channel {channel}."
  de: "Thread-Netz {name}, Kanal {channel}."
web.radios.thread_network_forming:
  en: "Creating the Thread network…"
  de: "Das Thread-Netz wird angelegt…"
web.radios.thread_network_missing:
  en: "The border router has no Thread network, but {count} Thread devices were commissioned into one. The bridge does not create a new network, because it would cut those devices off. Restore the network from a fabric backup (see “Restoring a lost Thread network” in deploy/testhost/README.md)."
  de: "Der Border Router hat kein Thread-Netz, aber {count} Thread-Geräte wurden in eines eingelernt. Die Bridge legt kein neues Netz an, weil es diese Geräte abhängen würde. Stellen Sie das Netz aus einem Fabric-Backup wieder her (siehe „Restoring a lost Thread network“ in deploy/testhost/README.md)."
```

- [ ] **Step 6: Run the web tests for the radios card**

Run: `uv run pytest tests/api/test_web.py -v -k "radios or thread_network"`
Expected: all PASS.

- [ ] **Step 7: Run the helper in a throwaway Alpine harness**

The pytest tests only prove the helper and the served markup. Per the
project's own rule, check the binding renders: serve `src/loxmatter/web/`
with a stub `GET /api/radios` answering the `formed` and then the `missing`
shape (a 30-line Python `http.server` script in the scratchpad), open it in
the browser pane, and confirm the line and its warning style appear under the
Thread select. Delete the harness afterwards; it is not committed.

- [ ] **Step 8: Checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run python scripts/check_language.py
git add src/loxmatter/web/app.js src/loxmatter/web/index.html src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(web): show the Thread network on the radios card

Name and channel once the bridge has formed or found a network, a note while
it forms one, and a warning when Thread devices exist but their network is
gone, which the bridge deliberately does not replace.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Documentation

**Files:**
- Modify: `deploy/testhost/README.md`
- Modify: `CHANGELOG.md` (the `Unreleased` section; create it at the top in the file's existing style if absent)

- [ ] **Step 1: README**

In `deploy/testhost/README.md`, next to the existing section that shows
`docker exec otbr ot-ctl dataset active -x` (around line 519), add:

````markdown
### Restoring a lost Thread network

Since 0.4.3 the bridge forms a Thread network by itself when the border router
has none - but not when matter-server still knows Thread devices, because a
new network would cut them off. The radios card then says so. To bring the old
network back, put its dataset into the border router while Thread is stopped:

```
docker exec otbr ot-ctl thread stop
docker exec otbr ot-ctl ifconfig down
docker exec otbr ot-ctl dataset set active <hex>
docker exec otbr ot-ctl ifconfig up
docker exec otbr ot-ctl thread start
```

`<hex>` is the value `ot-ctl dataset active -x` printed while the network was
still there, or the `threadDataset` entry in matter-server's data directory
inside a fabric backup. It is a credential: do not paste it into an issue or a
chat. Within a minute of the network coming back the radios card shows its
name and channel again.
````

Replace "0.4.3" with the version this ships in if the release is numbered
differently.

- [ ] **Step 2: CHANGELOG**

Under `Unreleased` (Fixed / Added, matching the file's headings):

```markdown
- A fresh installation forms its own Thread network. Before, nothing created
  one, and the border router watchdog restarted a healthy but unconfigured
  border router every 90 seconds.
- Switching Thread on through the radios card no longer rolls back on a
  border router that has no network yet.
- The radios card shows the Thread network's name and channel, and warns
  when Thread devices exist but their network is gone.
```

- [ ] **Step 3: Checks and commit**

```bash
uv run ruff format --check . && uv run python scripts/check_language.py
git add deploy/testhost/README.md CHANGELOG.md
git commit -m "docs: restoring a lost Thread network, and the change notes

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Verify on hardware (pi3-andi)

**Requires the owner's explicit go-ahead before step 2** - it stops the
running Thread network briefly. `ssh pi@100.76.58.4`, checkout
`~/loxmatter/deploy/testhost`. Never print a dataset.

- [ ] **Step 1: Build the bridge and updater images from this branch**

Follow `docs/DEVELOPMENT.md` for building a `dev` image of the branch, or
build on the Pi from the checkout (`docker compose build loxmatter`) after
`git fetch && git checkout claude/thread-network-auto` there. Record which
route was taken.

- [ ] **Step 2: Measure the `{}` body (spec section 4.2 step 4)**

With the owner's OK: stop `otbr`, start it on a new empty volume
(`docker compose run`-free route: temporarily point the `otbr-state` volume
in a Compose override at `otbr-state-test`), wait for `ot-ctl state` →
`disabled`, then from the Pi:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X PUT -H 'Content-Type: application/json' -H 'If-None-Match: *' --data '{}' http://127.0.0.1:8081/node/dataset/active
```

Expected: `201`. A `400` means the empty object is refused; then stop, and
change `create_network_if_absent` to send `{"NetworkName": "loxmatter"}`
(spec amendment first) before continuing. Delete the dataset again for step 3
(`ot-ctl dataset clear` is not enough; recreate the empty volume).

- [ ] **Step 3: Fresh case**

Empty volume, matter-server without Thread nodes: temporarily move
matter-server's `./data` aside (`data.keep`) so it has no nodes. Start the
branch's bridge. Expected within ~60 s: `ot-ctl state` → `leader`, bridge log
line "Formed Thread network …", `GET /api/radios` → `thread_network.state ==
"formed"`, watchdog log with no new line.

- [ ] **Step 4: Guarded case**

Put matter-server's original `./data` back (the IKEA switch, node 3), empty
otbr volume again. Expected: no `PUT` in the OTBR log, `thread_network.state
== "missing"` with `thread_devices == 1`, the card's warning.

- [ ] **Step 5: Radios card on the empty volume**

Switch Thread off and on through the card. Expected: the job ends `done`,
not rolled back.

- [ ] **Step 6: Restore**

Remove the Compose override, delete `otbr-state-test`, `docker compose up -d
otbr`. Expected: `ot-ctl state` → `leader` on the original network, the IKEA
switch reports a button press. Record the results of steps 2-6 in the spec's
section 7 as a dated note.

---

## Self-review

- Spec 3.1 → Task 1; 3.2 → Task 2; 4.1-4.3 → Tasks 3-5; 5 → Tasks 5-6;
  section 6 tests → each task's tests; 7 → Task 8; README section named in
  4.3 → Task 7.
- Names used across tasks: `read_active_dataset`, `border_router_role`,
  `create_network_if_absent`, `enable_thread`,
  `thread_network_name_from_dataset`, `ThreadNetworkKeeper.run_pass/run/status`,
  `ThreadNetworkStatus.as_json`, `is_thread_node`, `radiosThreadNetworkLine` -
  consistent between the tasks that define and consume them.
