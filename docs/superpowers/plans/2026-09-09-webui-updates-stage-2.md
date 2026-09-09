# WebUI updates, stage 2 — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A bridge that updates itself from its own interface — choose a channel, see what is available, press once, and get either the new version or the old one back.

**Architecture:** A sidecar container is the only thing holding the Docker socket; the bridge never touches it. The two talk through three files in the shared volume, so the interface can keep reading state while the bridge itself is restarting. Everything that decides — validation, the state machine, the rollback — is Python in this repository and unit-tested; only the thin layer that actually calls `docker`, `git` and `curl` needs a daemon.

**Tech Stack:** Python 3.12, `pytest`, `mypy --strict`, Docker CLI + Compose plugin inside an Alpine image, Alpine's `python3`, plain Alpine.js in the interface.

**Design:** [docs/superpowers/specs/2026-09-08-webui-updates-design.md](../specs/2026-09-08-webui-updates-design.md), including its addendum section 16 of 9 September.

## Global Constraints

- All commands run from the worktree root, never `cd` into the main checkout. Never `git stash` — the stash stack is shared with other sessions.
- **English everywhere** — code, comments, docstrings, test names, commit messages. See `CLAUDE.md`. The one exception: `de:` values in `src/loxmatter/i18n/strings.yaml` are shipped product and stay German, **with** umlauts for `web.*` keys (`Brücke`, `Gerät`).
- Line length 100 (`[tool.ruff] line-length = 100`).
- The checks: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run python scripts/check_language.py`, `uv run pytest`.
- The full suite takes about **three minutes**. It looks like a hang; it is not. **Implementers must not run it** — the controller does. Run only the tests covering your change.
- Baseline on this branch: **1481 passed, 2 skipped**, measured after `main` was merged in.

  > **Correction (9 September 2026).** This said 1475, and it was wrong twice over. The branch was
  > cut from `origin/main` while `main` was already ten commits ahead - the last-heard work was
  > merged locally and not pushed - so its real starting point was 1467, and the per-task totals
  > below were all eight too high. Task 1 reported 1473 and was correct; the plan was not. `main`
  > has since been merged into the branch, which is what this project's own notes say to do when
  > main moves during a branch. Every per-task total below should be read as **six higher** than
  > printed.
- `mypy` runs with `strict = true` over `src` and `scripts` (`pyproject.toml`). Everything this plan adds under those paths is covered by it; that is deliberate and is half the reason the updater is Python.
- Timestamps come only from `loxmatter.timestamps.now_iso()`.

## Two decisions this plan makes, and why

**The updater is Python, not shell.** The design (§6) lists "Alpine, Docker CLI, Compose plugin, git, curl", which reads like shell. It does not require it. The three security rules of §10 — the channel enum, the target pattern, forward-only with `git merge-base --is-ancestor` — are logic that decides whether a request is executed at all, and a shell script carrying them would sit outside every quality gate this project has: no `mypy`, no `pytest`, `shellcheck` only covers `install.sh`. Alpine ships `python3`; the extra image size is a sidecar's to pay.

**`scripts/update.sh` is not rewritten to share the logic.** Addendum §16.4 demands a deliberate answer, and this is it: the console script is proven, in use, and is the fallback for exactly the case where the sidecar is broken or absent. Rewriting it onto new, unproven code would put both paths on the same untested footing at the same moment. The duplication is five steps and is accepted knowingly; if the sidecar earns trust on the Pi, folding the script onto it is a separate, later change.

## What can and cannot be verified here

The Docker daemon is available on this machine (29.7.2, Docker Desktop, aarch64), and a container can drive it through a mounted socket — both checked. So tasks 4 to 6 are genuinely exercisable, not merely written.

What still cannot be reproduced here is the real stack: `matter-server` and `otbr` need `network_mode: host` and a Thread radio. The updater only ever touches the `loxmatter` service with `--no-deps`, so a reduced two-service stack is enough to exercise it — but the first run against a real installation is still the first run against a real installation.

## Files

| File | Role | Task |
| --- | --- | --- |
| `src/loxmatter/model/update_settings_store.py` | **new** — the channel, in the database | 1 |
| `src/loxmatter/api/settings.py` | the channel over HTTP | 1 |
| `src/loxmatter/update/protocol.py` | **new** — the three files, their shapes, atomic write | 2 |
| `src/loxmatter/update/rules.py` | **new** — the three security rules of §10 | 3 |
| `src/loxmatter/update/runner.py` | **new** — the state machine, on an injected command layer | 3 |
| `src/loxmatter/update/commands.py` | **new** — the thin layer that calls docker/git/curl | 4 |
| `src/loxmatter/update/__main__.py` | **new** — the loop the sidecar runs | 4 |
| `deploy/updater/Dockerfile` | **new** — the sidecar image | 5 |
| `.github/workflows/ci.yml` | a second image job | 5 |
| `deploy/testhost/docker-compose.yml` | the sidecar service | 6 |
| `src/loxmatter/api/update.py` | **new** — `/api/update/{status,check,apply}` | 7 |
| `src/loxmatter/loxone/server.py` | wire the router | 7 |
| `src/loxmatter/web/{index.html,app.js,style.css}` | the tile, four states | 8 |
| `src/loxmatter/i18n/strings.yaml` | its text | 1, 8 |

---

### Task 1: The channel, in the database

The smallest piece and the one the operator asked for first: which stream this installation follows, settable from the interface rather than by hand-editing `.env`.

**Files:**
- Create: `src/loxmatter/model/update_settings_store.py`
- Modify: `src/loxmatter/api/settings.py`, `src/loxmatter/api/models.py`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/model/test_update_settings_store.py` (new), `tests/api/test_settings.py`

**Interfaces:**
- Consumes: the `setting` key/value table and the shape of `ResendSettingsStore` (`src/loxmatter/model/resend_settings_store.py`) — follow it; it is the smaller, closer model of the two settings stores.
- Produces: `UpdateChannel` (a `str` `Enum` with `STABLE = "stable"` and `DEV = "dev"`), `UpdateSettingsStore.get_channel() -> UpdateChannel` and `.set_channel(channel: UpdateChannel) -> None`. Tasks 3, 4, 7 and 8 all read it.

- [ ] **Step 1: Write the failing store test**

Create `tests/model/test_update_settings_store.py`. Take the GPL header from any neighbouring test file verbatim.

```python
"""The update channel: which stream this installation follows."""

from __future__ import annotations

import pytest

from loxmatter.model.store import Store
from loxmatter.model.update_settings_store import UpdateChannel, UpdateSettingsStore


def make_store(tmp_path) -> UpdateSettingsStore:
    store = Store(tmp_path / "s.sqlite")
    return UpdateSettingsStore(store.db)


def test_the_default_channel_is_stable(tmp_path):
    """An installation that has never chosen follows released versions.

    The other way round would be the dangerous default: an operator who
    never opened the setting would be riding every commit on main.
    """
    assert make_store(tmp_path).get_channel() is UpdateChannel.STABLE


def test_a_saved_channel_comes_back(tmp_path):
    settings = make_store(tmp_path)
    settings.set_channel(UpdateChannel.DEV)
    assert settings.get_channel() is UpdateChannel.DEV


def test_an_unknown_stored_value_falls_back_to_stable(tmp_path):
    """A row written by a newer version, or by hand, must not crash the
    bridge on start - and must not silently mean `dev`.

    Falling back to `stable` is the conservative reading: whatever this
    value was meant to be, following released versions is the choice that
    cannot surprise anyone.
    """
    settings = make_store(tmp_path)
    settings._db.execute(  # noqa: SLF001 - deliberately writing a bad row
        "INSERT OR REPLACE INTO setting (key, value) VALUES (?, ?)",
        ("update_channel", "nightly"),
    )
    assert settings.get_channel() is UpdateChannel.STABLE
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest tests/model/test_update_settings_store.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'loxmatter.model.update_settings_store'`.

- [ ] **Step 3: Write the store**

Create `src/loxmatter/model/update_settings_store.py`. Copy the GPL header from `src/loxmatter/model/resend_settings_store.py` and follow that file's shape.

```python
"""Which stream of published code this installation follows.

A setting in the database like every other one (design
`2026-09-08-webui-updates-design.md`, section 9), deliberately not an
environment variable: the operator's standing requirement is that
configuration is made in the interface, and a second place that only
`.env` knows about would defeat that on the very first setting stage 2
adds.

`LOXMATTER_IMAGE_TAG` in `.env` stays what it is - the deployed version
pointer, written by the updater and never by hand (see addendum 16.3).
The channel here says which stream to *compare against*; the tag says
what is *running*. Two questions, two homes.
"""

from __future__ import annotations

import enum
import sqlite3

_CHANNEL_KEY = "update_channel"


class UpdateChannel(str, enum.Enum):
    """`str` mixin so a value serialises as itself in API models and in
    the request file the updater reads - the sidecar must not have to
    know Python's enum repr to parse it."""

    STABLE = "stable"
    DEV = "dev"


class UpdateSettingsStore:
    """Access to `setting` through the store's connection - like
    `ResendSettingsStore`, for a different key."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_channel(self) -> UpdateChannel:
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_CHANNEL_KEY,)
        ).fetchone()
        if row is None:
            return UpdateChannel.STABLE
        try:
            return UpdateChannel(row["value"])
        except ValueError:
            # A row from a newer version, or written by hand. Falling back
            # to STABLE rather than raising: the bridge must start, and of
            # the two channels this is the one that cannot surprise
            # anybody.
            return UpdateChannel.STABLE

    def set_channel(self, channel: UpdateChannel) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO setting (key, value) VALUES (?, ?)",
            (_CHANNEL_KEY, channel.value),
        )
        self._db.commit()
```

- [ ] **Step 4: Run it to verify it passes**

```bash
uv run pytest tests/model/test_update_settings_store.py -v
```

Expected: **3 passed**.

- [ ] **Step 5: Expose it over HTTP**

Read `src/loxmatter/api/settings.py` first and follow its existing router shape. Add a model to `src/loxmatter/api/models.py`:

```python
class UpdateChannelOut(BaseModel):
    """Which stream this installation follows. `stable` compares against
    published releases, `dev` against the tip of `main`."""

    model_config = ConfigDict(frozen=True)

    channel: str


class UpdateChannelIn(BaseModel):
    channel: UpdateChannel
```

and to the settings router a `GET /api/settings/update-channel` returning `UpdateChannelOut`, and a `PUT` taking `UpdateChannelIn`. Pydantic validates the enum, so an unknown value is a 422 before any code sees it.

- [ ] **Step 6: Write the route tests**

Append to `tests/api/test_settings.py`, following that file's existing fixtures:

```python
async def test_the_update_channel_defaults_to_stable(api):
    client, _, _ = api
    response = await client.get("/api/settings/update-channel")
    assert response.status_code == 200
    assert response.json()["channel"] == "stable"


async def test_the_update_channel_can_be_switched_and_comes_back(api):
    client, _, _ = api
    put = await client.put("/api/settings/update-channel", json={"channel": "dev"})
    assert put.status_code == 200
    assert (await client.get("/api/settings/update-channel")).json()["channel"] == "dev"


async def test_an_unknown_update_channel_is_refused(api):
    """Pydantic rejects it before any code sees it - the enum is the guard,
    not a hand-written check that could drift from the store's."""
    client, _, _ = api
    response = await client.put("/api/settings/update-channel", json={"channel": "nightly"})
    assert response.status_code == 422
```

- [ ] **Step 7: Run the route tests**

```bash
uv run pytest tests/api/test_settings.py -v
```

Expected: all green, including the three new ones.

- [ ] **Step 8: Prove the fallback bites**

Change `get_channel`'s `except ValueError` branch to `raise`. Run:

```bash
uv run pytest tests/model/test_update_settings_store.py -k unknown_stored_value -v
```

Expected: **1 failed** with `ValueError: 'nightly' is not a valid UpdateChannel`. Revert and re-run: **3 passed**.

Without this, the test would pass just as well against a store that happened not to be asked — it is the fallback, not the absence of a crash, that is being pinned.

- [ ] **Step 9: Fast checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
```

```bash
git add src/loxmatter/model/update_settings_store.py src/loxmatter/api/settings.py \
        src/loxmatter/api/models.py tests/model/test_update_settings_store.py \
        tests/api/test_settings.py
git commit -m "$(cat <<'EOF'
feat(settings): the update channel lives in the database

Which stream an installation follows is a setting like every other one, not
an environment variable. The operator's standing requirement is that
configuration happens in the interface; a channel that only `.env` knew about
would defeat that on the first setting stage 2 adds.

`LOXMATTER_IMAGE_TAG` keeps its job and its home: it is the deployed version
pointer, written by the updater and never by hand. The channel says what to
compare against, the tag says what is running.

Default is `stable`, and an unreadable stored value falls back to it rather
than raising - the bridge has to start, and of the two channels that is the
one that cannot surprise anybody.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

Expected suite total after this task: **1481 passed, 2 skipped** (six new tests).

---

### Task 2: The three files both sides agree on

The bridge and the sidecar never talk over the network (design §7). They share a directory. This task is that contract, and nothing else — no Docker, no git, pure data.

**Files:**
- Create: `src/loxmatter/update/__init__.py`, `src/loxmatter/update/protocol.py`
- Test: `tests/update/__init__.py` (empty), `tests/update/test_protocol.py`

**Interfaces:**
- Consumes: `loxmatter.timestamps.now_iso()`.
- Produces:
  - `UPDATE_DIR = Path("/data/update")`, and `request_path(base)`, `state_path(base)`, `log_path(base)` taking a base directory so tests can point them at `tmp_path`.
  - `@dataclass(frozen=True) UpdateRequest(id: str, channel: str, target: str, requested_at: str)`
  - `@dataclass(frozen=True) UpdateState(id, phase, steps, from_version, to_version, error, rolled_back, healthy, updater_seen_at)` with `phase` one of `"idle" | "backup" | "pull" | "recreate" | "health" | "done" | "rollback" | "failed"`.
  - `write_json_atomically(path: Path, payload: Mapping[str, object]) -> None`
  - `read_request(base) -> UpdateRequest | None`, `read_state(base) -> UpdateState | None`, `write_request(base, req)`, `write_state(base, state)`
  Tasks 3, 4 and 7 all use these.

- [ ] **Step 1: Write the failing tests**

Create `tests/update/__init__.py` (empty) and `tests/update/test_protocol.py` with the GPL header, then:

```python
"""The files the bridge and the sidecar share, and nothing else."""

from __future__ import annotations

import json

from loxmatter.update.protocol import (
    UpdateRequest,
    read_request,
    read_state,
    request_path,
    write_json_atomically,
    write_request,
)


def test_no_request_reads_as_none(tmp_path):
    """The normal state of an installation is that nothing is pending.

    A missing file must not be an error: the sidecar polls this path
    every two seconds for the entire life of the machine, and almost
    every read finds nothing.
    """
    assert read_request(tmp_path) is None
    assert read_state(tmp_path) is None


def test_a_written_request_reads_back_field_for_field(tmp_path):
    written = UpdateRequest(
        id="01J000000000000000000000",
        channel="stable",
        target="v0.3.0",
        requested_at="2026-09-09T10:00:00.000000+00:00",
    )
    write_request(tmp_path, written)
    assert read_request(tmp_path) == written


def test_a_half_written_file_is_never_observed(tmp_path):
    """The sidecar polls while the bridge writes. A reader must never see
    a truncated file, which is why the write goes to a temporary name and
    is renamed - `os.replace` is atomic within a filesystem.

    This checks the mechanism rather than trying to race it: after the
    call, no temporary file is left behind, so no reader could have found
    one to parse.
    """
    base = tmp_path / "update"
    base.mkdir()
    write_json_atomically(request_path(base), {"id": "x"})
    leftovers = [p.name for p in base.iterdir() if p.name != request_path(base).name]
    assert leftovers == []


def test_a_corrupt_request_reads_as_none_rather_than_raising(tmp_path):
    """A truncated or hand-edited file must not stop the sidecar's loop.

    Returning `None` means "nothing to do", and the next well-formed
    request is picked up normally. Raising here would end the loop and
    leave the installation without an updater until someone restarts the
    container - the failure mode this whole design exists to avoid.
    """
    request_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert read_request(tmp_path) is None


def test_the_written_file_is_plain_readable_json(tmp_path):
    """The sidecar is Python today. The file format must not assume that:
    anyone debugging on the Pi reads it with `cat`.
    """
    write_request(
        tmp_path,
        UpdateRequest(id="a", channel="dev", target="abc1234", requested_at="t"),
    )
    payload = json.loads(request_path(tmp_path).read_text(encoding="utf-8"))
    assert payload == {"id": "a", "channel": "dev", "target": "abc1234", "requested_at": "t"}
```

> **Correction (9 September 2026).** This test originally used `tmp_path`
> itself as the base and counted everything in it. The autouse fixture in
> `tests/conftest.py` already creates `fake-home` there, so the count could
> never be empty, and the implementer's way out was a second autouse fixture
> that deleted the parent's directory while `Path.home` still pointed at it -
> a dangling pointer for every later test in this package. The base is now a
> subdirectory the test owns. Corrected in `8deb220`.

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run pytest tests/update/test_protocol.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'loxmatter.update'`.

- [ ] **Step 3: Write the protocol module**

Create `src/loxmatter/update/__init__.py` with only the GPL header and a one-line docstring, and `src/loxmatter/update/protocol.py` with the header and:

```python
"""The three files the bridge and the sidecar share.

There is no network between them (design section 7). The bridge writes
`request.json`, the updater writes `state.json` and `log.txt`, and both
live in the volume the bridge already has mounted at `/data`.

That the state is a file and not the bridge's memory is the carrying
decision of the design: the bridge restarts in the middle of an update,
and the interface has to keep reading through it. Memory cannot survive
that; a file in the volume can.

Every write goes through `write_json_atomically`. The sidecar polls every
two seconds and would otherwise eventually read a half-written file - not
often, but the one time it happens is during an update, which is the one
moment nobody wants a parse error.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

#: Where the two sides meet inside the container. Tests pass their own
#: base directory instead; nothing in this module reads this constant on
#: its own.
UPDATE_DIR = Path("/data/update")


def request_path(base: Path) -> Path:
    return base / "request.json"


def state_path(base: Path) -> Path:
    return base / "state.json"


def log_path(base: Path) -> Path:
    return base / "log.txt"


@dataclass(frozen=True)
class UpdateRequest:
    """What the operator asked for. Written by the bridge, read once by
    the updater - `id` is what makes "once" enforceable."""

    id: str
    channel: str
    target: str
    requested_at: str


@dataclass(frozen=True)
class UpdateState:
    """Where the updater is. Written by the updater, read by the bridge
    and, through it, by the interface - including while the bridge is
    restarting and this file is the only thing that knows anything.

    `updater_seen_at` is rewritten on every pass of the loop even when
    nothing is happening. It is how the bridge tells "no update running"
    from "no updater installed" (design section 11).
    """

    id: str | None
    phase: str
    steps: Sequence[str]
    from_version: str | None
    to_version: str | None
    error: str | None
    rolled_back: bool
    healthy: bool
    updater_seen_at: str


def write_json_atomically(path: Path, payload: Mapping[str, object]) -> None:
    """Write via a temporary name in the same directory, then rename.

    `os.replace` is atomic within one filesystem, so a reader sees either
    the whole old file or the whole new one. The temporary file must be a
    sibling: across filesystems the rename would degrade to a copy and
    lose the guarantee.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Truncated, or edited by hand on the Pi. "Nothing to do" is the
        # right answer: raising would end the updater's loop and leave the
        # installation without an updater until someone restarts the
        # container.
        return None
    return parsed if isinstance(parsed, dict) else None


def read_request(base: Path) -> UpdateRequest | None:
    payload = _read_json(request_path(base))
    if payload is None:
        return None
    try:
        return UpdateRequest(
            id=str(payload["id"]),
            channel=str(payload["channel"]),
            target=str(payload["target"]),
            requested_at=str(payload["requested_at"]),
        )
    except KeyError:
        return None


def write_request(base: Path, request: UpdateRequest) -> None:
    write_json_atomically(request_path(base), asdict(request))


def read_state(base: Path) -> UpdateState | None:
    payload = _read_json(state_path(base))
    if payload is None:
        return None
    try:
        return UpdateState(
            id=payload.get("id"),
            phase=str(payload["phase"]),
            steps=[str(step) for step in payload.get("steps", [])],
            from_version=payload.get("from_version"),
            to_version=payload.get("to_version"),
            error=payload.get("error"),
            rolled_back=bool(payload.get("rolled_back", False)),
            healthy=bool(payload.get("healthy", False)),
            updater_seen_at=str(payload["updater_seen_at"]),
        )
    except KeyError:
        return None


def write_state(base: Path, state: UpdateState) -> None:
    write_json_atomically(state_path(base), asdict(state))
```

- [ ] **Step 4: Run them to verify they pass**

```bash
uv run pytest tests/update/test_protocol.py -v
```

Expected: **5 passed**.

- [ ] **Step 5: Prove the corruption guard bites**

Change `_read_json`'s `except json.JSONDecodeError` branch to `raise`, then:

```bash
uv run pytest tests/update/test_protocol.py -k corrupt_request -v
```

Expected: **1 failed** with a `json.decoder.JSONDecodeError`. Revert and re-run: **5 passed**.

- [ ] **Step 6: Fast checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
```

```bash
git add src/loxmatter/update tests/update
git commit -m "$(cat <<'EOF'
feat(update): the file protocol the bridge and the sidecar share

There is no network between them. The bridge writes request.json, the updater
writes state.json and log.txt, and both live in the volume the bridge already
has at /data.

That the state is a file rather than the bridge's memory is what lets the
interface keep reading while the bridge itself is restarting - which is
precisely the minute an update spends, and precisely when someone is
watching.

Every write goes through a temporary sibling and a rename. The sidecar polls
every two seconds, so it would otherwise eventually read a half-written file:
rare, but the one time it happens is during an update.

A corrupt file reads as "nothing to do" rather than raising. An exception here
would end the updater's loop and leave the installation without an updater
until someone restarts the container - the failure this whole design exists
to avoid.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

Expected suite total after this task: **1486 passed, 2 skipped** (five new tests).

---

### Task 3: The three rules, and the state machine

The dangerous half of stage 2, written so it can be exercised without a daemon. Nothing here runs a command; the runner takes its command layer as an argument, and the tests hand it a fake.

**Files:**
- Create: `src/loxmatter/update/rules.py`, `src/loxmatter/update/runner.py`
- Test: `tests/update/test_rules.py`, `tests/update/test_runner.py`

**Interfaces:**
- Consumes: `UpdateChannel` (Task 1), `UpdateRequest`/`UpdateState`/`write_state` (Task 2).
- Produces:
  - `class RequestRejected(Exception)` with a `reason: str`.
  - `check_request(request, *, running_version, running_commit, is_ancestor) -> None` — raises `RequestRejected`, returns nothing on success. `is_ancestor` is `Callable[[str, str], bool]`, injected so the rule is testable without git.
  - `IMAGE_REPOSITORY = "ghcr.io/lucienkerl/loxmatter"`, `image_reference(target: str) -> str`.
  - `class Commands(Protocol)` with `backup()`, `fetch()`, `checkout(ref)`, `pull(image)`, `recreate()`, `healthy() -> bool`, `write_image_tag(tag)`, `read_image_tag() -> str`, `read_git_ref() -> str`, `log(line)`.
    **`read_git_ref` is not decoration.** The rollback has to restore two different things: the image tag in `.env` and the checked-out ref. They are not the same string — an installation on the `stable` channel runs image tag `stable` from git ref `v0.2.0`, and checking out `stable` would fail because no such ref exists. Design §8 records both before step 1 for exactly this reason.
  - `run_update(request, commands, *, base, running_version, running_commit, is_ancestor, sleep, deadline_seconds=120.0) -> UpdateState`.

- [ ] **Step 1: Write the failing rule tests**

Create `tests/update/test_rules.py` with the GPL header, then:

```python
"""The three rules that decide whether a request is executed at all.

Design section 10. They are enforced in the updater, not at login: the
session that reaches this route already downloads the Fabric backup, so
another password prompt would claim a boundary that does not exist. The
boundary that does exist is here.
"""

from __future__ import annotations

import pytest

from loxmatter.update.protocol import UpdateRequest
from loxmatter.update.rules import IMAGE_REPOSITORY, RequestRejected, check_request, image_reference


def request(**overrides: str) -> UpdateRequest:
    base = {
        "id": "01J000000000000000000000",
        "channel": "stable",
        "target": "v0.3.0",
        "requested_at": "2026-09-09T10:00:00+00:00",
    }
    base.update(overrides)
    return UpdateRequest(**base)  # type: ignore[arg-type]


def never_ancestor(_running: str, _target: str) -> bool:
    return False


def always_ancestor(_running: str, _target: str) -> bool:
    return True


def test_a_stable_request_for_a_newer_version_passes():
    check_request(
        request(),
        running_version="0.2.0",
        running_commit="abc1234",
        is_ancestor=never_ancestor,
    )


def test_an_unknown_channel_is_rejected():
    with pytest.raises(RequestRejected, match="channel"):
        check_request(
            request(channel="nightly"),
            running_version="0.2.0",
            running_commit="abc1234",
            is_ancestor=always_ancestor,
        )


@pytest.mark.parametrize(
    "target",
    [
        "v0.3.0; rm -rf /",
        "../../etc/passwd",
        "latest",
        "",
        "v0.3",
        "$(whoami)",
        "abc",  # too short for a SHA
    ],
)
def test_a_target_that_is_not_a_version_or_a_sha_is_rejected(target):
    """The target reaches a command line. Anything that is not plainly a
    version or a commit is refused before it can get there - this is the
    rule that stops the route being a shell."""
    with pytest.raises(RequestRejected, match="target"):
        check_request(
            request(target=target),
            running_version="0.2.0",
            running_commit="abc1234",
            is_ancestor=always_ancestor,
        )


def test_stable_refuses_to_go_backwards():
    """Forward only, by semantic version. Going back is the updater's own
    business, from its own bookkeeping - never through this route."""
    with pytest.raises(RequestRejected, match="older"):
        check_request(
            request(target="v0.1.0"),
            running_version="0.2.0",
            running_commit="abc1234",
            is_ancestor=never_ancestor,
        )


def test_stable_refuses_the_version_already_running():
    with pytest.raises(RequestRejected, match="older"):
        check_request(
            request(target="v0.2.0"),
            running_version="0.2.0",
            running_commit="abc1234",
            is_ancestor=never_ancestor,
        )


def test_dev_requires_the_target_to_descend_from_the_running_commit():
    """There is no ordering over SHAs, so "forward" means "descendant".
    A commit that is not one is a sideways or backwards move."""
    with pytest.raises(RequestRejected, match="descend"):
        check_request(
            request(channel="dev", target="deadbee"),
            running_version="0.2.0",
            running_commit="abc1234",
            is_ancestor=never_ancestor,
        )


def test_dev_accepts_a_descendant():
    check_request(
        request(channel="dev", target="deadbee"),
        running_version="0.2.0",
        running_commit="abc1234",
        is_ancestor=always_ancestor,
    )


def test_the_image_name_never_comes_from_the_request():
    """Design section 10, rule 2. The repository is hard-coded here; the
    request contributes only the tag, and only after passing the pattern
    above. A request naming a foreign registry has nowhere to put it."""
    assert image_reference("v0.3.0") == f"{IMAGE_REPOSITORY}:v0.3.0"
```

- [ ] **Step 2: Run them to verify they fail**

```bash
uv run pytest tests/update/test_rules.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'loxmatter.update.rules'`.

- [ ] **Step 3: Write the rules**

Create `src/loxmatter/update/rules.py` with the GPL header and:

```python
"""What a request must satisfy before anything is executed.

Design section 10. Three rules, and the reason they live here rather than
in the bridge's route: the bridge is reachable across the whole LAN with
`network_mode: host`, the sidecar is not. Whoever takes over the bridge
completely can still only make this container install a published, newer
loxmatter version.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from loxmatter.update.protocol import UpdateRequest

#: Hard-coded, never taken from the request (rule 2). The request may
#: contribute a tag, and only one that passed `_TARGET` below.
IMAGE_REPOSITORY = "ghcr.io/lucienkerl/loxmatter"

_CHANNELS = frozenset({"stable", "dev"})
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


class RequestRejected(Exception):
    """A request that will not be executed. The reason reaches the
    interface, so it is written for a person, not for a log grep."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def image_reference(target: str) -> str:
    return f"{IMAGE_REPOSITORY}:{target}"


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = _VERSION.match(value)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def check_request(
    request: UpdateRequest,
    *,
    running_version: str,
    running_commit: str,
    is_ancestor: Callable[[str, str], bool],
) -> None:
    """Raise `RequestRejected` unless all three rules hold.

    `is_ancestor(running, target)` is injected rather than shelling out to
    git here: the rule is logic and deserves a test, and the git call is
    the command layer's business (see `commands.py`).
    """
    if request.channel not in _CHANNELS:
        raise RequestRejected(f"unknown channel {request.channel!r}")

    target = request.target
    target_version = _version_tuple(target)
    if target_version is None and _SHA.match(target) is None:
        raise RequestRejected(f"target {target!r} is neither a version nor a commit")

    if request.channel == "stable":
        if target_version is None:
            raise RequestRejected(f"target {target!r} is not a version, but the channel is stable")
        current = _version_tuple(running_version)
        # An unparsable running version means we cannot prove the request
        # goes forward. Refusing is the only safe reading: the whole point
        # of the rule is that it never lets anything move backwards.
        if current is None or target_version <= current:
            raise RequestRejected(
                f"target {target!r} is not newer than the running {running_version!r} - "
                "this route never goes to an older version"
            )
        return

    # dev: no ordering over SHAs, so "forward" means "descendant".
    if target_version is not None:
        raise RequestRejected(f"target {target!r} is a version, but the channel is dev")
    if not is_ancestor(running_commit, target):
        raise RequestRejected(
            f"target {target!r} does not descend from the running commit {running_commit!r}"
        )
```

- [ ] **Step 4: Run them to verify they pass**

```bash
uv run pytest tests/update/test_rules.py -v
```

Expected: **all green**, 14 tests (the parametrised one counts seven times).

- [ ] **Step 5: Prove the forward-only rule bites**

Delete the `or target_version <= current` half of the stable condition, then:

```bash
uv run pytest tests/update/test_rules.py -k "backwards or already_running" -v
```

Expected: **2 failed**. Revert and re-run: all green.

This is the rule the design calls the one that must not fall for a convenience case. A test that only checked the happy path would not notice its removal.

- [ ] **Step 6: Write the failing runner tests**

Create `tests/update/test_runner.py` with the GPL header, then:

```python
"""The state machine, driven against a fake command layer.

None of this touches Docker. That is the point: the rollback path runs on
a real installation for the first time, so it had better have run here a
hundred times first.
"""

from __future__ import annotations

from pathlib import Path

from loxmatter.update.protocol import UpdateRequest, read_state
from loxmatter.update.runner import run_update


class FakeCommands:
    """Records what was asked of it and answers as told."""

    def __init__(self, *, healthy_after: int = 0, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self.lines: list[str] = []
        self.tag = "stable"
        self._healthy_after = healthy_after
        self._health_asked = 0
        self._fail_on = fail_on

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if self._fail_on == name:
            raise RuntimeError(f"{name} failed")

    def backup(self) -> str:
        self._record("backup")
        return "/data/backups/store-x.tgz"

    def fetch(self) -> None:
        self._record("fetch")

    def checkout(self, ref: str) -> None:
        self._record(f"checkout:{ref}")

    def pull(self, image: str) -> None:
        self._record(f"pull:{image}")

    def recreate(self) -> None:
        self._record("recreate")

    def healthy(self) -> bool:
        self._health_asked += 1
        return self._health_asked > self._healthy_after

    def write_image_tag(self, tag: str) -> None:
        self.calls.append(f"tag:{tag}")
        self.tag = tag

    def read_image_tag(self) -> str:
        return self.tag

    def read_git_ref(self) -> str:
        # Deliberately different from the image tag: an installation on
        # `stable` runs that image tag from the ref `v0.2.0`. A rollback
        # that confused the two would try to check out `stable`, which is
        # not a ref and would fail.
        return "v0.2.0"

    def log(self, line: str) -> None:
        self.lines.append(line)


def a_request() -> UpdateRequest:
    return UpdateRequest(
        id="01J000000000000000000000",
        channel="stable",
        target="v0.3.0",
        requested_at="2026-09-09T10:00:00+00:00",
    )


def run(commands: FakeCommands, base: Path, **overrides: object):
    kwargs: dict[str, object] = {
        "base": base,
        "running_version": "0.2.0",
        "running_commit": "abc1234",
        "is_ancestor": lambda _a, _b: True,
        "sleep": lambda _seconds: None,
    }
    kwargs.update(overrides)
    return run_update(a_request(), commands, **kwargs)  # type: ignore[arg-type]


def test_a_healthy_update_walks_the_five_steps_in_order(tmp_path):
    commands = FakeCommands()
    state = run(commands, tmp_path)

    assert state.phase == "done"
    assert state.rolled_back is False
    assert state.healthy is True
    assert commands.calls == [
        "backup",
        "fetch",
        "checkout:v0.3.0",
        "pull:ghcr.io/lucienkerl/loxmatter:v0.3.0",
        "tag:v0.3.0",
        "recreate",
    ]


def test_the_state_file_is_written_at_every_phase(tmp_path):
    """The interface reads this file while the bridge is restarting. A
    state written only at the end would leave it blind for the minute
    that matters most."""
    commands = FakeCommands()
    run(commands, tmp_path)
    written = read_state(tmp_path)
    assert written is not None
    assert written.steps == ["backup", "pull", "recreate", "health"]


def test_an_unhealthy_bridge_rolls_back_exactly_once(tmp_path):
    """The trigger is `/health` not answering within the deadline. The
    rollback writes the previous tag back, checks out the old ref and
    recreates - and if THAT is unhealthy too, it stops. Trying again
    would be a loop nobody is watching at two in the morning."""
    commands = FakeCommands(healthy_after=10_000)
    state = run(commands, tmp_path, deadline_seconds=3.0)

    assert state.phase == "failed"
    assert state.rolled_back is True
    assert commands.calls.count("recreate") == 2
    assert commands.calls[-3:] == ["tag:stable", "checkout:v0.2.0", "recreate"]


def test_the_database_is_never_restored_automatically(tmp_path):
    """Design section 8: the image rollback alone gets the house running
    again, because an old binary on a newer database returns early from
    `_migrate` instead of refusing. Restoring the backup would discard
    everything since it was taken - not something to do unattended."""
    commands = FakeCommands(healthy_after=10_000)
    run(commands, tmp_path, deadline_seconds=3.0)
    assert not any(call.startswith("restore") for call in commands.calls)


def test_a_rejected_request_executes_nothing(tmp_path):
    """The rules run before the first command. A request that fails them
    must not have taken a backup, let alone pulled an image."""
    commands = FakeCommands()
    state = run_update(
        UpdateRequest(id="x", channel="stable", target="v0.1.0", requested_at="t"),
        commands,
        base=tmp_path,
        running_version="0.2.0",
        running_commit="abc1234",
        is_ancestor=lambda _a, _b: True,
        sleep=lambda _seconds: None,
    )
    assert state.phase == "failed"
    assert state.error is not None and "older" in state.error
    assert commands.calls == []


def test_a_failing_step_rolls_back_too(tmp_path):
    """A pull that fails leaves the old image in place, so strictly
    nothing needs undoing - but the tag was already written, and a tag
    pointing at a version that never arrived is exactly the state a later
    `up -d` would deploy."""
    commands = FakeCommands(fail_on="recreate")
    state = run(commands, tmp_path)
    assert state.phase == "failed"
    assert state.rolled_back is True
    assert commands.tag == "stable"
```

- [ ] **Step 7: Run them to verify they fail**

```bash
uv run pytest tests/update/test_runner.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'loxmatter.update.runner'`.

- [ ] **Step 8: Write the runner**

Create `src/loxmatter/update/runner.py` with the GPL header and:

```python
"""The five steps, and what happens when one of them does not work.

Nothing here calls a command. `Commands` is a Protocol and the caller
supplies it, so the whole state machine - including the rollback - runs in
the test suite against a fake. That is not tidiness: the rollback path
executes against a real installation exactly once, the first time it is
ever needed, at which point it is the only thing standing between a failed
update and a bridge that stays down.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from loxmatter.timestamps import now_iso
from loxmatter.update.protocol import UpdateRequest, UpdateState, write_state
from loxmatter.update.rules import RequestRejected, check_request, image_reference

#: How long to wait for `/health` before calling the update failed. Not a
#: new number: it comes from `scripts/update.sh`, whose comment records
#: why 20 s was too tight - one run went over and the script reported a
#: healthy service as sick. Too short a window is the more expensive kind
#: of false alarm.
DEADLINE_SECONDS = 120.0
POLL_SECONDS = 2.0

#: The four steps the interface names while it waits. `fetch` and
#: `checkout` fold into `pull` on purpose - they are one thing to a person
#: watching ("it is getting the new version"), and four named steps beat a
#: progress bar that sticks at 60 percent.
STEPS = ("backup", "pull", "recreate", "health")


class Commands(Protocol):
    def backup(self) -> str: ...
    def fetch(self) -> None: ...
    def checkout(self, ref: str) -> None: ...
    def pull(self, image: str) -> None: ...
    def recreate(self) -> None: ...
    def healthy(self) -> bool: ...
    def write_image_tag(self, tag: str) -> None: ...
    def read_image_tag(self) -> str: ...
    def read_git_ref(self) -> str: ...
    def log(self, line: str) -> None: ...


def run_update(
    request: UpdateRequest,
    commands: Commands,
    *,
    base: Path,
    running_version: str,
    running_commit: str,
    is_ancestor: Callable[[str, str], bool],
    sleep: Callable[[float], None],
    deadline_seconds: float = DEADLINE_SECONDS,
) -> UpdateState:
    done: list[str] = []

    def publish(
        phase: str,
        *,
        error: str | None = None,
        rolled_back: bool = False,
        healthy: bool = False,
    ) -> UpdateState:
        state = UpdateState(
            id=request.id,
            phase=phase,
            steps=list(done),
            from_version=running_version,
            to_version=request.target,
            error=error,
            rolled_back=rolled_back,
            healthy=healthy,
            updater_seen_at=now_iso(),
        )
        write_state(base, state)
        return state

    try:
        check_request(
            request,
            running_version=running_version,
            running_commit=running_commit,
            is_ancestor=is_ancestor,
        )
    except RequestRejected as rejected:
        # Before the first command, so a rejected request has taken no
        # backup and pulled no image - it left the installation exactly as
        # it found it.
        commands.log(f"refused: {rejected.reason}")
        return publish("failed", error=rejected.reason)

    # Both of these are read BEFORE anything is written, and they are two
    # different strings - see `read_git_ref` in the Protocol above.
    previous_tag = commands.read_image_tag()
    previous_ref = commands.read_git_ref()

    def roll_back(reason: str) -> UpdateState:
        """Exactly once, whatever the outcome.

        A loop here would be a bridge restarting itself over and over at
        two in the morning with nobody watching. If one rollback does not
        bring it back, a person has to look.

        The database is deliberately NOT restored. `_migrate` in
        `model/store.py` returns early when the stored `user_version` is
        already at or above the binary's, so an old binary on a newer
        database starts normally - the image rollback alone gets the house
        running again. Restoring the backup would discard everything since
        it was taken, which is a decision for someone awake.
        """
        commands.log(f"rolling back to {previous_tag} ({previous_ref}): {reason}")
        publish("rollback", error=reason)
        commands.write_image_tag(previous_tag)
        commands.checkout(previous_ref)
        commands.recreate()
        recovered = _wait_for_health(commands, sleep, deadline_seconds)
        return publish("failed", error=reason, rolled_back=True, healthy=recovered)

    try:
        commands.log(f"backing up before {request.target}")
        commands.backup()
        done.append("backup")
        publish("backup")

        commands.fetch()
        commands.checkout(request.target)
        # The checked-out ref goes in the log on purpose (addendum 16.2):
        # a checkout rewrites docker-compose.yml on disk, and whoever
        # reads this log afterwards has to be able to see that the file
        # changed under them.
        commands.log(f"checked out {request.target}; compose file on disk is now that revision")
        commands.pull(image_reference(request.target))
        done.append("pull")
        publish("pull")

        commands.write_image_tag(request.target)
        commands.recreate()
        done.append("recreate")
        publish("recreate")
    except Exception as failure:  # noqa: BLE001 - any failed step rolls back
        # Broad on purpose. Whatever went wrong, the tag may already point
        # at a version that never arrived, and that is exactly the state a
        # later `up -d` would deploy. The traceback goes to the log so the
        # cause survives.
        commands.log(f"step failed: {failure!r}")
        return roll_back(str(failure))

    if not _wait_for_health(commands, sleep, deadline_seconds):
        return roll_back(f"the bridge did not answer /health within {deadline_seconds:.0f}s")

    done.append("health")
    commands.log(f"update to {request.target} complete")
    return publish("done", healthy=True)


def _wait_for_health(
    commands: Commands, sleep: Callable[[float], None], deadline_seconds: float
) -> bool:
    """Poll until healthy or the deadline passes.

    Elapsed time is counted from the polls rather than read off the wall
    clock, so a test can drive the whole wait with a `sleep` that returns
    immediately - otherwise the rollback test would take two real minutes
    and be skipped as slow within a month.
    """
    waited = 0.0
    while waited <= deadline_seconds:
        if commands.healthy():
            return True
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS
    return False
```

- [ ] **Step 9: Run them to verify they pass**

```bash
uv run pytest tests/update/test_runner.py -v
```

Expected: **6 passed**.

- [ ] **Step 10: Prove the rollback is capped**

Change the rollback to retry while unhealthy (a `while` instead of the single attempt), then:

```bash
uv run pytest tests/update/test_runner.py -k rolls_back_exactly_once -v
```

Expected: **1 failed** on the `recreate` count. Revert and re-run: **6 passed**.

- [ ] **Step 11: Fast checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
```

```bash
git add src/loxmatter/update/rules.py src/loxmatter/update/runner.py tests/update
git commit -m "$(cat <<'EOF'
feat(update): the three rules and the state machine

The dangerous half of stage 2, written so it can be exercised without a
daemon: the runner takes its command layer as an argument and the tests hand
it a fake. The rollback path runs against a real installation exactly once -
the first time - so it had better have run here a hundred times first.

The rules are design section 10. The channel is an enum, the target must be a
version or a commit before it can reach a command line, and the move must be
forward: by semantic version on stable, by `is_ancestor` on dev. The image
repository is hard-coded here and never taken from the request, so a request
naming a foreign registry has nowhere to put it.

Going backwards is the updater's own business, from its own bookkeeping,
never through this route - which is why the rollback writes the previous tag
back rather than accepting one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

Expected suite total after this task: **1506 passed, 2 skipped** (fourteen rule tests plus six runner tests).

---

### Task 4: The command layer and the loop

Everything the runner was handed as an argument, now real — and the loop the sidecar actually runs. This is the first task that needs a daemon.

**Files:**
- Create: `src/loxmatter/update/commands.py`, `src/loxmatter/update/__main__.py`
- Test: `tests/update/test_commands.py`, `tests/update/test_loop.py`

**Interfaces:**
- Consumes: `Commands` (the Protocol from Task 3), `run_update`, the protocol module, `UpdateChannel`.
- Produces: `DockerCommands(compose_dir: Path, env_path: Path, health_url: str)` satisfying `Commands`, and a `main()` that loops every two seconds.

- [ ] **Step 1: Write the failing command tests**

The interesting part of `DockerCommands` is not that it can call `docker` — it is **what** it calls and how it edits `.env`, both of which are testable without a daemon by injecting the subprocess runner.

Create `tests/update/test_commands.py` with the GPL header:

```python
"""What the command layer actually asks of docker, git and the filesystem."""

from __future__ import annotations

from pathlib import Path

from loxmatter.update.commands import DockerCommands


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, argv: list[str], **_kwargs: object) -> str:
        self.commands.append(argv)
        return ""


def make(tmp_path: Path) -> tuple[DockerCommands, RecordingRunner]:
    env = tmp_path / ".env"
    env.write_text("LOXMATTER_IMAGE_TAG=stable\nMINISERVER_IP=10.0.0.1\n", encoding="utf-8")
    runner = RecordingRunner()
    commands = DockerCommands(
        compose_dir=tmp_path,
        env_path=env,
        health_url="http://host.docker.internal:8080/health",
        run=runner,
    )
    return commands, runner


def test_recreate_never_touches_the_other_services(tmp_path):
    """`--no-deps` for two reasons now: the updater must not recreate
    itself mid-request, and matter-server must not be recreated at all -
    since 8 September that service points at matterjs-server, whose first
    start migrates the Fabric directory one way (addendum 16.2)."""
    commands, runner = make(tmp_path)
    commands.recreate()
    assert runner.commands == [
        ["docker", "compose", "up", "-d", "--no-deps", "loxmatter"],
    ]


def test_writing_the_tag_replaces_the_line_in_place(tmp_path):
    """In place, not appended: a second definition of the same variable
    would work (Compose takes the last), but whoever edited the file later
    would change the wrong line. `.env.example` says so already."""
    commands, _ = make(tmp_path)
    commands.write_image_tag("v0.3.0")
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LOXMATTER_IMAGE_TAG=v0.3.0" in text
    assert text.count("LOXMATTER_IMAGE_TAG") == 1
    # Everything else survives untouched - this file holds the Miniserver
    # address and the API token.
    assert "MINISERVER_IP=10.0.0.1" in text


def test_reading_the_tag_comes_back(tmp_path):
    commands, _ = make(tmp_path)
    commands.write_image_tag("v0.3.0")
    assert commands.read_image_tag() == "v0.3.0"


def test_a_missing_tag_line_reads_as_stable(tmp_path):
    """Compose's own default is `:-stable`; the reader must agree with it,
    or a rollback would write back a tag that was never running."""
    env = tmp_path / ".env"
    env.write_text("MINISERVER_IP=10.0.0.1\n", encoding="utf-8")
    commands = DockerCommands(
        compose_dir=tmp_path, env_path=env, health_url="http://x/health", run=RecordingRunner()
    )
    assert commands.read_image_tag() == "stable"


def test_checkout_fetches_tags_first(tmp_path):
    commands, runner = make(tmp_path)
    commands.fetch()
    commands.checkout("v0.3.0")
    assert runner.commands == [
        ["git", "fetch", "--tags", "--prune", "origin"],
        ["git", "checkout", "--detach", "v0.3.0"],
    ]
```

- [ ] **Step 2: Run them to verify they fail, then write `commands.py`**

```bash
uv run pytest tests/update/test_commands.py -v
```

Expected: `ModuleNotFoundError: No module named 'loxmatter.update.commands'`.

`DockerCommands` takes `run` as a constructor argument defaulting to a real `subprocess.run` wrapper that raises on a non-zero exit and returns stdout. Each method is one or two calls:

| Method | Command |
| --- | --- |
| `backup()` | `tar czf /data/backups/store-<stamp>.tgz -C /data loxmatter.sqlite`, returns the path |
| `fetch()` | `git fetch --tags --prune origin` |
| `checkout(ref)` | `git checkout --detach <ref>` |
| `pull(image)` | `docker compose pull loxmatter` |
| `recreate()` | `docker compose up -d --no-deps loxmatter` |
| `healthy()` | `curl -fsS <health_url>` — `True` on exit 0, `False` on any failure |
| `is_ancestor(a, b)` | `git merge-base --is-ancestor <a> <b>` — `True` on exit 0 |

`git` and `docker compose` run with `cwd=compose_dir`. `write_image_tag` rewrites `.env` line by line, replacing the one that starts with `LOXMATTER_IMAGE_TAG=` and leaving every other line byte-for-byte; if no such line exists it appends one. `log(line)` appends to `log_path`, capped — read the last N bytes and rewrite when the file grows past a limit.

- [ ] **Step 3: Run them to verify they pass**

```bash
uv run pytest tests/update/test_commands.py -v
```

Expected: **6 passed**.

- [ ] **Step 4: Write the loop, and its test**

`src/loxmatter/update/__main__.py` is small on purpose: read the request, skip it if its `id` matches the one already in `state.json`, otherwise run it, and either way write `updater_seen_at` and sleep two seconds.

`tests/update/test_loop.py` drives one pass with a fake clock and asserts:

```python
def test_the_same_request_id_is_executed_only_once(tmp_path):
    """The bridge writes the file once, but the loop reads it every two
    seconds until someone replaces it. Without the id check, a single
    press would update forever."""
```

and

```python
def test_a_pass_with_no_request_still_stamps_updater_seen_at(tmp_path):
    """This stamp is how the bridge tells "nothing is running" from "no
    updater is installed" (design section 11). It has to be written on
    the quiet passes, which are almost all of them."""
```

- [ ] **Step 5: Exercise it against the real daemon**

This machine has Docker (29.7.2) and a container can drive it through a mounted socket — both checked before this plan was written. Do not simulate what can be run.

Build a throwaway two-service compose stack under the scratchpad: a `loxmatter` service that is any small image serving `/health`, and nothing else. Point a `DockerCommands` at it and drive `recreate()` and `healthy()` for real. Record the commands and their output in the report verbatim.

**This is not a committed test.** It needs a daemon, and the Pi that runs this project may not have one to spare for a test run. The committed tests are the ones above; this step is the evidence that the layer beneath them works.

- [ ] **Step 6: Fast checks and commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run python scripts/check_language.py
```

```bash
git add src/loxmatter/update/commands.py src/loxmatter/update/__main__.py tests/update
git commit -m "$(cat <<'EOF'
feat(update): the command layer and the sidecar's loop

Everything the runner took as an argument, now real. The interesting part is
not that it can call docker - it is what it calls: `up -d --no-deps
loxmatter`, never a bare `up`, because since 8 September matter-server points
at matterjs-server and a bare recreate would fire a one-way Fabric migration.

`.env` is edited line by line, in place. A second LOXMATTER_IMAGE_TAG would
technically work - Compose takes the last - but whoever edited the file later
would change the wrong line, and this file also holds the Miniserver address
and the API token.

The loop skips a request whose id already stands in state.json. Without that,
one press would update forever, because the file stays on disk until someone
replaces it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: The sidecar image and its CI job

**Files:**
- Create: `deploy/updater/Dockerfile`
- Modify: `.github/workflows/ci.yml`
- Test: `tests/test_build_arguments.py` (it already guards the first image's build arguments — follow it)

- [ ] **Step 1: Write the Dockerfile**

`deploy/updater/Dockerfile`, from `alpine:3.20`, installing `docker-cli`, `docker-cli-compose`, `git`, `curl`, `python3`, and copying `src/loxmatter/update/` plus the two modules it imports from (`loxmatter/timestamps.py`, `loxmatter/model/update_settings_store.py`). Entrypoint `python3 -m loxmatter.update`.

Its header comment must say, unabridged, what the design's §10 requires be said in both the README and the Compose comment: **this container is root-equivalent on the host through the Docker socket.** That sentence is not decoration; it is the price of the whole feature and the reason the service has no `network_mode: host`.

- [ ] **Step 2: Build it here**

```bash
docker build -f deploy/updater/Dockerfile -t loxmatter-updater:local .
docker run --rm loxmatter-updater:local python3 -c "import loxmatter.update.rules; print('imports fine')"
```

Expected: the build succeeds and the import prints. Record both verbatim — this is a real build on a real daemon, not a guess.

- [ ] **Step 3: Add the CI job**

A second `image`-shaped job in `.github/workflows/ci.yml`, `needs: test`, same trigger condition, same tag logic, publishing `ghcr.io/lucienkerl/loxmatter-updater`. Read the existing `image` job first and mirror it; in particular keep the `env:`-not-`${{ }}` handling of the version, whose reason is written out there (a tag may contain shell metacharacters, and the job has `packages: write`).

- [ ] **Step 4: Fast checks and commit**

---

### Task 6: The service in the Compose file

**Files:**
- Modify: `deploy/testhost/docker-compose.yml`, `deploy/testhost/README.md`
- Test: `tests/deploy/test_compose.py` if one exists, otherwise assert in `tests/api/test_web.py`'s style on the parsed YAML

- [ ] **Step 1: Write the failing test**

Whatever file it lands in, the test parses `deploy/testhost/docker-compose.yml` with `yaml.safe_load` and asserts the properties that carry the security argument:

```python
def test_the_updater_is_not_reachable_from_the_lan():
    """This is the whole reason it is allowed to hold the Docker socket.
    The other three services run with `network_mode: host` and are
    reachable across the LAN; this one sits on Compose's own network,
    reachable outward for `git fetch` and not inward.

    If this assertion ever has to be relaxed, the socket has to go with
    it."""
    service = compose()["services"]["loxmatter-updater"]
    assert "network_mode" not in service
    assert "ports" not in service


def test_the_updater_mounts_the_socket_the_store_and_the_checkout():
    service = compose()["services"]["loxmatter-updater"]
    mounts = set(service["volumes"])
    assert "/var/run/docker.sock:/var/run/docker.sock" in mounts
    assert "loxmatter-store:/data" in mounts
```

- [ ] **Step 2: Add the service**

Following the design §6: image pinned by digest, the three mounts, `extra_hosts: ["host.docker.internal:host-gateway"]`, `restart: unless-stopped`, and **no** `ports` and **no** `network_mode`.

The comment above it carries the socket sentence from Task 5 again — the design asks for it in both places, and the Compose file is where someone edits before they read a Dockerfile.

- [ ] **Step 3: Validate and commit**

```bash
docker compose -f deploy/testhost/docker-compose.yml config >/dev/null && echo "compose parses"
```

Then the fast checks and a commit. Extend `deploy/testhost/README.md` with what the sidecar is, what the socket means, and how to run without it — the design's §11 says the bridge must behave when no updater is installed, and an operator who declines one deserves to be told how.

---

### Task 7: The bridge's three routes

**Files:**
- Create: `src/loxmatter/api/update.py`
- Modify: `src/loxmatter/api/models.py`, `src/loxmatter/loxone/server.py`
- Test: `tests/api/test_update.py`

**Interfaces:**
- Consumes: `read_state`, `write_request`, `UpdateRequest` (Task 2); `UpdateSettingsStore` (Task 1); `build_info()` from `src/loxmatter/api/version.py` for the running version and commit.
- Produces: `build_update_router(store, *, base: Path)` returning an `APIRouter` with `GET /api/update/status`, `GET /api/update/check`, `POST /api/update/apply`.

- [ ] **Step 1: Write the failing tests**

`tests/api/test_update.py`, following the fixtures in `tests/api/conftest.py`:

```python
async def test_status_says_no_updater_when_the_stamp_is_missing_or_stale(api):
    """Design section 11. `state.json` absent, or `updater_seen_at` older
    than a few loop intervals, both mean the same thing to an operator:
    there is nothing here that could perform an update. The interface must
    say so plainly rather than offering a button that does nothing."""


async def test_status_reports_the_phase_from_the_file(api):
    """Not from the bridge's memory. This route answers correctly one
    second after the bridge itself was restarted by an update - which is
    the entire reason the state is a file."""


async def test_apply_writes_a_request_and_returns_its_id(api):


async def test_apply_refuses_a_target_the_rules_reject(api):
    """The rules live in the updater and are enforced there. Checking them
    here too is not duplication for its own sake: a request that the
    sidecar will refuse should fail while the operator is still looking at
    the screen, not silently two seconds later in a file they never open."""


async def test_every_update_route_is_behind_the_api_guard(api_without_session):
    """Session or token, like everything under /api. No second password
    prompt - the same session already downloads the Fabric backup, and a
    prompt here would claim a boundary that does not exist elsewhere."""
```

Fill each body following the shape of the neighbouring API tests.

- [ ] **Step 2: Write the router, wire it, run the tests**

`build_update_router` is thin: `status` reads the state file and adds a computed `updater_present` boolean; `check` compares the running version against the channel's stream; `apply` validates with `check_request` and writes the request file with a fresh `id`.

Register it in `src/loxmatter/loxone/server.py` next to the others, **with `dependencies=api_guard`** — that line is what the last test above pins.

`GET /api/update/check` reaches GitHub. Give it a short timeout and let a failure become a plain "could not check" in the response rather than a 500: an installation behind a firewall is not broken, and the tile has to say something sensible.

- [ ] **Step 3: Fast checks and commit**

---

### Task 8: The tile, four states

**Files:**
- Modify: `src/loxmatter/web/index.html`, `src/loxmatter/web/app.js`, `src/loxmatter/web/style.css`, `src/loxmatter/i18n/strings.yaml`
- Test: `tests/api/test_web.py`

The System tab's section begins at `index.html:1511` (`x-show="view === 'system'"`), and the version block already there (`web.system.version_*`) is where this joins.

- [ ] **Step 1: The strings**

New `web.system.update_*` keys for: the heading, the channel labels (`Stable` / `Development`) and their one-line explanations, the four states' text, the confirmation with its honest sentence about roughly a minute without the bridge, the schema-jump warning, and the "no updater installed" case. English and German, German **with** umlauts.

- [ ] **Step 2: The channel selector**

A `<select>` bound to the setting from Task 1, in the tile. Changing it saves immediately and re-runs the check — the operator picked a channel to see what it offers, and a separate Save button would make them ask twice.

- [ ] **Step 3: The four states**

One tile, four mutually exclusive blocks, per design §9:

1. **Confirmation** — what happens, what stays untouched, the schema jump if `check` reported one, and the minute of silence.
2. **Running** — the four named steps from `state.steps`, plus the tail of the log. Not a progress bar: the steps are named because a bar that sticks at 60% tells nobody anything.
3. **Bridge away** — fed from the last `state.json` the interface managed to read, with seconds counting up against 120. **The existing connection banner gets different text for the duration** (`index.html`, the `!socketConnected && socketEverConnected` condition) instead of looking like a fault.
4. **Result** — success with the new version and the duration, or failure with the finding that the old version is running again, the last log lines, and buttons for the full log and the backup.

Nothing is locked and no modal covers the interface. Someone who tries to commission a device mid-update meets an error, and the banner from state 3 explains it — a minute of paternalism would cost more.

- [ ] **Step 4: The tests**

Following `tests/api/test_web.py`'s conventions:

- The four state blocks are present and mutually exclusive.
- The channel selector is bound to the route from Task 1.
- The confirmation text names the minute of silence — the design calls this the honest message, and an interface that omits it is promising something it cannot keep.
- The connection banner has the update text as an alternative, not a second banner.
- And, using the `_app_state` node harness that already exists in that file: the state-selection helper actually returns the right block for a given `state.json` shape. That helper runs the shipped `app.js`; this is the one place in the tile where a wrong branch would be invisible in the markup.

- [ ] **Step 5: See it**

Start the interface with `scripts/dev_web_server.py` and look at all four states with hand-written `state.json` files. A tile that only exists in tests is not a tile. Record what you saw.

- [ ] **Step 6: Fast checks and commit**

---

## Coverage against the design

| Design | Task |
| --- | --- |
| §5 the updater rewrites `LOXMATTER_IMAGE_TAG` | 4 (`write_image_tag`) |
| §6 the sidecar, its mounts, no LAN reachability | 5, 6 |
| §7 the three files | 2 |
| §8 the five steps, the pre-check, the rollback | 3 (logic), 4 (commands) |
| §8 the database is never restored automatically | 3, pinned by its own test |
| §9 the tile, four states | 8 |
| §9 the channel in the database | 1 |
| §10 the three rules | 3 |
| §10 routes behind `api_guard`, no second prompt | 7 |
| §11 no sidecar installed | 7 (`updater_present`), 6 (README) |
| §12 text | 8 |
| §14 no automatic deployment, no downgrade, no history | no task — deliberate |
| §16.2 `--no-deps`, the checked-out ref in the log | 3, 4 |
| §16.3 the channel is not an environment variable | 1 |
| §16.4 `scripts/update.sh` stays separate | stated in "Two decisions", no task |

## Honest limits of this plan

**Two kinds of step appear here.** Tasks 1 to 4 carry their code, because that is where the decisions live and where a paraphrase would let a subtly different implementation through. Tasks 5 to 8 specify against files that already exist — the `image` job, the Compose services, the System tab — and say what must be true of the result rather than transcribing hundreds of lines that would drift from their originals between writing this and executing it. Where a task says "mirror the existing job", read that job first; it is the source of truth, not this document.

**Two steps are hand-run, deliberately.** Task 4 step 5 drives the command layer against this machine's real Docker daemon, and Task 8 step 5 looks at the four states in a browser. Neither is committed: CI has no daemon and no browser, and claiming coverage that does not exist is the mistake the previous branch had to correct in its own plan.

**The self-review of this plan found one real error, and it is worth naming.** Task 3's rollback test originally expected `checkout:stable` — confusing the image tag with the git ref. They are not the same string: an installation on the `stable` channel runs image tag `stable` from git ref `v0.2.0`, and checking out `stable` would simply fail. Design §8 records both before step 1 for exactly this reason, and the plan had quietly dropped one of them. `Commands` now carries `read_git_ref()`, and the rollback restores both.
