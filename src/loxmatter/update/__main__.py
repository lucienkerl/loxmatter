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

"""The loop the sidecar actually runs.

Small on purpose (design section 6: "Operation: loop with `sleep 2`"):
read the request, skip it if its `id` matches the one already recorded in
`state.json`, otherwise run it, and either way write `updater_seen_at` and
sleep two seconds. `run_once` is that one pass, factored out so it can be
driven directly - most of what a single pass does is covered there,
without a daemon or a wall-clock sleep.

That is not the whole of `main`, though: its pre-loop setup
(`_repair_state`, `_seed_last_id`) and the in-memory carry across
iterations are both things only `main` itself does, and both are still
testable despite the `while True` having no return value of its own -
inject a fake `DockerCommands` and a `time.sleep` that raises after a
fixed number of calls to end the loop on command (see
`tests/update/test_loop.py`).

`run_once` resolves `running_version`/`running_commit` itself, and only in
the branch that is about to check a request against them (`commands.
read_running_identity`, backed by `docker inspect` - see its docstring for
why the checkout's own `pyproject.toml`/`git rev-parse HEAD` are the wrong
source). `main` used to resolve both unconditionally on every pass, which
meant a `git` fork roughly every two seconds even on the passes - nearly
all of them - that have nothing to do; moving the lookup here also means a
test drives it with a plain fake, no real checkout or `git` binary needed.

Both `run_once` and `main` hold the last executed request `id` in memory,
not only in `state.json`: the file is the channel to the operator, but it
is not the only memory the loop is allowed to have, and a torn read or a
failed write must not be able to make an already-completed request run
again from scratch (see `_touch_seen_at` below, and the design's own
warning about exactly that failure mode).

The reverse gap matters just as much: `main` also seeds `last_id` from
`state.json` once, at startup, before the in-memory carry takes over. A
sidecar process does not live forever - a host reboot, a `docker compose
up -d`, `restart: unless-stopped` after a crash all start it fresh, with
no memory of anything. Without the seed, the first pass of a new process
would see `last_id=None` and re-run whatever `request.json` still names,
even though `state.json` already recorded it `done`. Memory and file are
each the only record across one of these two gaps - a restart, or a torn
read/failed write within one process - so both are read, and neither
replaces the other.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from loxmatter.timestamps import now_iso
from loxmatter.update import protocol
from loxmatter.update.commands import DockerCommands
from loxmatter.update.protocol import (
    UpdateState,
    read_request,
    read_state,
    state_path,
    write_state,
)
from loxmatter.update.runner import Commands, run_update

#: How often the loop wakes up to look for a new `request.json`. This
#: happens to equal `runner.POLL_SECONDS`, which paces the health-check
#: wait inside a single `run_update` call - the two share a value, not a
#: meaning, and are kept as separate constants so that changing one is not
#: mistaken for changing the other.
LOOP_INTERVAL_SECONDS = 2.0

#: Where the two sides meet inside this container. `main` uses this
#: default; `run_once` itself takes `base` as a parameter, same as
#: `run_update`. The plan's Task 2 interface list names this path as
#: `protocol.UPDATE_DIR`
#: (`docs/superpowers/plans/2026-09-09-webui-updates-stage-2.md:325`:
#: "Tasks 3, 4 and 7 all use these") - it is defined once, there, and
#: aliased here rather than duplicated, so the two modules cannot drift
#: to two different paths for the one directory this whole file is
#: about.
DEFAULT_BASE = protocol.UPDATE_DIR

#: The mounted checkout `fetch`/`checkout` run against.
#:
#: Deliberately NOT `/app`, which is where this image keeps its own code
#: (see `deploy/updater/Dockerfile`). Mounting the host checkout over
#: `/app` shadows `loxmatter/` inside the container, and `python3 -m
#: loxmatter.update` then dies with `ModuleNotFoundError: No module named
#: 'loxmatter'` before it reaches a single line of this file - a restart
#: loop with no update ever running. Checked, not reasoned about: a
#: container built from that Dockerfile imports fine, and the same
#: container with a checkout bind-mounted at `/app` does not. The two
#: paths must stay apart, and `deploy/testhost/docker-compose.yml` mounts
#: the checkout here.
DEFAULT_COMPOSE_DIR = Path("/checkout")

#: Reaches the bridge on the *host's* network: `loxmatter` in
#: `deploy/testhost/docker-compose.yml` runs with `network_mode: host`,
#: so it is not a resolvable Compose service name from this container,
#: which sits on the ordinary Compose network (design section 6 - it must
#: not have LAN access). `host.docker.internal` reaches the host from
#: there, but on Linux - the Raspberry Pi this ships to, not the Docker
#: Desktop this was last checked against - only once the container
#: declares `extra_hosts: ["host.docker.internal:host-gateway"]`. Design
#: section 6 already commits the `loxmatter-updater` Compose service (a
#: later task - adding it is not part of this one) to exactly that entry.
#: Until that entry exists, `healthy()` returns `False` for the whole
#: `runner.DEADLINE_SECONDS` and every update rolls back.
DEFAULT_HEALTH_URL = "http://host.docker.internal:8080/health"


class LoopCommands(Commands, Protocol):
    """`Commands`, plus `is_ancestor` and `read_running_identity` - see
    `DockerCommands`'s docstrings for why these sit outside the Protocol
    `run_update` itself takes: both are things the loop resolves before
    calling `run_update`, not things `run_update` asks of `Commands`
    while it runs."""

    def is_ancestor(self, older: str, newer: str) -> bool: ...
    def read_running_identity(self) -> tuple[str, str]: ...


def run_once(
    base: Path,
    commands: LoopCommands,
    *,
    last_id: str | None,
    is_ancestor: Callable[[str, str], bool],
    sleep: Callable[[float], None],
) -> str | None:
    """One pass. Returns the request `id` that should be remembered as
    "already executed" for the next pass - `last_id` unchanged on a quiet
    pass, `request.id` once `run_update` has been handed the request.
    `main` holds this return value in memory in addition to `state.json`
    (see the module docstring)."""
    request = read_request(base)
    if request is not None and request.id != last_id:
        running_version, running_commit = commands.read_running_identity()
        run_update(
            request,
            commands,
            base=base,
            running_version=running_version,
            running_commit=running_commit,
            is_ancestor=is_ancestor,
            sleep=sleep,
        )
        return request.id
    # Nothing to do this pass - `run_update` already stamped
    # `updater_seen_at` on its way out above, so only the quiet path needs
    # to do it here.
    _touch_seen_at(base, read_state(base), commands)
    return last_id


def _touch_seen_at(base: Path, previous: UpdateState | None, commands: LoopCommands) -> None:
    """Refresh the one field the bridge polls for aliveness, leaving
    everything else exactly as the last `run_update` (or lack of one) left
    it - a quiet pass must not overwrite a `done` or `failed` result with
    something that looks like `idle`.

    `previous is None` is ambiguous on its own: `read_state` returns it
    both when `state.json` has never existed (the honest "idle, nothing
    has ever run here" case) and when it exists but could not be parsed -
    corrupt JSON, an unknown `phase`, a hand edit. Only the first case
    should produce a fresh `idle` state outright; the second is
    `_repair_state`'s job - once it has moved the unreadable file aside
    and written a fresh one, the *next* pass finds a readable file again
    and takes the first branch below like any other quiet pass.
    """
    if previous is None:
        _repair_state(base, commands)
        return
    state = replace(previous, updater_seen_at=now_iso())
    try:
        write_state(base, state)
    except Exception:  # noqa: BLE001, S110 - best effort, matches
        # `runner.run_update`'s own `publish`: a full disk must not stop
        # the loop, only skip the one write it could not make.
        pass


def _repair_state(base: Path, commands: LoopCommands) -> None:
    """Make `state.json` readable again, called from two places: from
    `_touch_seen_at` above on any quiet pass that finds it unreadable,
    and once from `main`, before the loop even starts and before
    `_seed_last_id` (see there) reads the file it repairs.

    That second call site is the point of this function existing on its
    own rather than staying inlined in `_touch_seen_at`: a *pending*
    request never reaches `_touch_seen_at` at all in the process where
    `state.json` is actually corrupt (`run_once` returns before it, once
    `run_update` is called - see the module docstring), and
    `run_update`'s own `publish` would then overwrite the corrupt bytes
    outright, with no repair and no line in `log.txt` about it ever
    having happened. Calling this once from `main`, unconditionally,
    closes that gap regardless of what `request.json` says.

    A missing file needs nothing done here - a plain `write_state` below
    with `id=None` is the honest "nothing has ever run" state, same as
    always. An unreadable-but-present file is moved aside under a
    timestamped name (never overwriting an earlier `.corrupt` file - a
    second corruption must not erase the first one's evidence) and its
    `id` salvaged from the raw JSON before that move: `read_state`
    returns `None` not only for binary garbage but for well-formed JSON
    with an unknown `phase` or a non-list `steps` - the hand-edit case -
    and in that case `id` is sitting in the file in plain text. Losing it
    here would mean `_seed_last_id` genuinely cannot recover it, where
    salvaging it here means it can.

    Only the move is allowed to raise out of this function - a
    persistent failure to move (a read-only mount, say) has to surface
    somewhere, and letting it propagate means it reaches the exact
    dedup `main` already has for exactly this purpose (`last_logged_failure`)
    instead of this function reinventing one. `write_state` below stays
    best-effort and silent on failure, matching `_touch_seen_at` and
    `runner.run_update`'s own `publish`: a full disk must not stop the
    loop, and was never logged for those two either.

    Called from `_touch_seen_at`, `read_state(base) is None` already
    holds by construction - `_touch_seen_at` only reaches this branch
    when its own `previous` was `None`. Called from `main`, nothing
    upstream has checked that yet, and a `state.json` that parses fine
    must be left completely alone: re-reading it here first, rather than
    trusting "the path exists" as a proxy for "the file is unreadable",
    is what stops a perfectly healthy `done`/`failed` state from being
    clobbered into a fresh `idle` one on every single process start.
    """
    if read_state(base) is not None:
        return
    path = state_path(base)
    salvaged_id: str | None = None
    if path.exists():
        salvaged_id = _salvage_id(path)
        corrupt_path = _unused_corrupt_name(path)
        os.replace(path, corrupt_path)  # OSError propagates - see docstring
        _log_best_effort(
            commands,
            f"state.json could not be read; moved aside to {corrupt_path.name} "
            "and resuming with a fresh state"
            + (f", salvaged id {salvaged_id!r}" if salvaged_id is not None else ""),
        )
    state = UpdateState(
        id=salvaged_id,
        phase="idle",
        steps=[],
        from_version=None,
        to_version=None,
        error=None,
        rolled_back=False,
        healthy=False,
        updater_seen_at=now_iso(),
    )
    try:
        write_state(base, state)
    except Exception:  # noqa: BLE001, S110 - best effort, see docstring above
        pass


def _salvage_id(path: Path) -> str | None:
    """Best-effort recovery of `id` from a `state.json` that `read_state`
    could not turn into an `UpdateState`. `id` is a plain top-level JSON
    field even when the rest of the document has the wrong shape - an
    unknown `phase`, a `steps` that is not a list - so a raw `json.loads`
    can still reach it even though `read_state` gave up. Binary garbage,
    truncated JSON, a missing or non-string `id` all mean the same thing
    here: nothing to salvage, not an error to raise."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("id")
    return value if isinstance(value, str) else None


def _unused_corrupt_name(path: Path) -> Path:
    """A `.corrupt` name that does not collide with one a previous
    corruption already left behind - overwriting it would destroy that
    evidence, exactly what moving the file aside (instead of the
    original single fixed name `state.json.corrupt`) exists to avoid. A
    failing SD card can corrupt the file more than once, and each time
    deserves its own copy. `now_iso()` carries microseconds, so a
    same-process collision is not a realistic concern; the loop below
    exists only so this can never raise on one."""
    stamp = now_iso().replace(":", "-")
    candidate = path.with_name(f"{path.name}.corrupt.{stamp}")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.corrupt.{stamp}.{suffix}")
        suffix += 1
    return candidate


def _seed_last_id(base: Path) -> str | None:
    """The `id` `main` starts its in-memory carry from, read out of
    `state.json` once, before the loop begins - factored out for the same
    reason `run_once` was (see the module docstring): `main`'s own `while
    True` has no return value to assert on, and this is the one line of
    it that matters.

    `None` covers both an installation that has never run an update
    (`read_state` returns `None` because the file has never existed) and
    - now that `main` calls `_repair_state` once, unconditionally, before
    this function runs (see there) - the case where `state.json` was
    unreadable and has just been replaced with a fresh `idle` state, `id`
    salvaged where the raw JSON still named one and `None` where it truly
    did not. Either way `read_state` here sees a normal, parseable file.

    The two remaining cases are `None` for different reasons and must not
    be read as interchangeable by whoever meets this function next: a
    freshly installed sidecar with `id=None` has literally nothing to
    re-run - there is no `request.json` yet either. A repaired one with
    `id=None` means the id truly could not be salvaged, and the very next
    request this process sees will be treated as new even if it is,
    unknowably, one `state.json` already recorded `done` before the
    corruption - a silent re-run of a request that already completed,
    not merely "no worse than a fresh install."
    """
    state = read_state(base)
    return state.id if state is not None else None


def _log_best_effort(commands: LoopCommands, line: str) -> None:
    try:
        commands.log(line)
    except Exception:  # noqa: BLE001, S110 - logging must never crash the loop
        pass


def main() -> None:
    base = _path_from_env("LOXMATTER_UPDATE_BASE", DEFAULT_BASE)
    compose_dir = _path_from_env("LOXMATTER_COMPOSE_DIR", DEFAULT_COMPOSE_DIR)
    env_path = _path_from_env("LOXMATTER_ENV_PATH", compose_dir / ".env")
    health_url = os.environ.get("LOXMATTER_HEALTH_URL", "").strip() or DEFAULT_HEALTH_URL
    commands = DockerCommands(
        compose_dir=compose_dir, env_path=env_path, health_url=health_url, base=base
    )

    # Repair a corrupt or unreadable `state.json` before anything else
    # touches it - in particular before `_seed_last_id` just below, which
    # needs a readable file to recover `last_id` from, and before the
    # loop's first pass, whose own `run_update` would otherwise overwrite
    # the corrupt bytes outright the moment a request happens to be
    # pending (see `_repair_state`'s docstring). Guarded the same way
    # every other pre-loop and in-loop step in this function is: nothing
    # before `while True` may be allowed to end the process either. A
    # persistent failure here (a read-only mount, say) is not silently
    # lost - the very next pass hits the identical unreadable file
    # through `_touch_seen_at`, where it is logged with `main`'s own
    # `last_logged_failure` dedup below, not reinvented here for a call
    # that only ever happens once.
    try:
        _repair_state(base, commands)
    except Exception:  # noqa: BLE001, S110 - see comment above
        pass

    # Seeded from `state.json` once, here, and held in memory across
    # iterations after that - see the module docstring. The seed is what
    # survives a restart of this process (a reboot, `docker compose up
    # -d`, a crash under `restart: unless-stopped`); the in-memory carry
    # is what survives a corrupt read or a failed write within this
    # process without either replacing the other. `state.json` alone,
    # with no in-memory carry, once meant a corrupt read or a failed
    # write could make an already-completed request look brand new
    # again; the in-memory carry alone, with no seed, once meant a fresh
    # process forgot every request `state.json` had ever recorded and
    # re-ran the last one on every restart.
    last_id: str | None = _seed_last_id(base)
    # Also held across iterations: a failure that persists pass after
    # pass (the Docker socket staying unreachable, say) is logged once,
    # not every two seconds forever. `log()`'s own cap means a line
    # repeated that often does not even buy history - it becomes a
    # sustained stream of SD-card writes as `_cap_log` re-caps the file
    # on every single call, which is worse for the card the cap exists
    # to protect than the very failure being logged.
    last_logged_failure: str | None = None

    while True:
        # The whole pass is guarded, not just the identity lookup that
        # used to be the only thing that could raise here. `read_request`/
        # `read_state` are more tolerant now (see `protocol._read_json`),
        # but an exception escaping `while True` would still end the
        # sidecar silently - on an unattended installation, with nobody
        # watching to restart the container.
        try:
            last_id = run_once(
                base,
                commands,
                last_id=last_id,
                is_ancestor=commands.is_ancestor,
                sleep=time.sleep,
            )
            last_logged_failure = None
        except Exception as failure:  # noqa: BLE001 - see comment above
            message = f"a pass failed: {failure!r}"
            if message != last_logged_failure:
                _log_best_effort(commands, message)
                last_logged_failure = message
            try:
                _touch_seen_at(base, read_state(base), commands)
            except Exception:  # noqa: BLE001, S110 - best effort, matches every guard here
                pass
        time.sleep(LOOP_INTERVAL_SECONDS)


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else default


if __name__ == "__main__":
    main()
