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
driven directly - `main`'s own `while True` has no return value to assert
on and nothing to unit-test that `run_once` does not already cover.

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
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from loxmatter.timestamps import now_iso
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

#: Where the two sides meet inside this container - see
#: `protocol.UPDATE_DIR`. `main` uses this default; `run_once` itself
#: takes `base` as a parameter, same as `run_update`.
DEFAULT_BASE = Path("/data/update")

#: The mounted checkout `fetch`/`checkout` run against.
DEFAULT_COMPOSE_DIR = Path("/app")

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
    _touch_seen_at(base, read_state(base))
    return last_id


def _touch_seen_at(base: Path, previous: UpdateState | None) -> None:
    """Refresh the one field the bridge polls for aliveness, leaving
    everything else exactly as the last `run_update` (or lack of one) left
    it - a quiet pass must not overwrite a `done` or `failed` result with
    something that looks like `idle`.

    `previous is None` is ambiguous on its own: `read_state` returns it
    both when `state.json` has never existed (the honest "idle, nothing
    has ever run here" case) and when it exists but could not be parsed -
    corrupt JSON, an unknown `phase`, a hand edit. Only the first case
    should produce a fresh `idle` state; the second must be left alone,
    because a fresh `id=None` state written over it would make the next
    pass believe no request has ever been seen, and re-run whatever
    `request.json` currently names from scratch.
    """
    if previous is None:
        if state_path(base).exists():
            return
        state = UpdateState(
            id=None,
            phase="idle",
            steps=[],
            from_version=None,
            to_version=None,
            error=None,
            rolled_back=False,
            healthy=False,
            updater_seen_at=now_iso(),
        )
    else:
        state = replace(previous, updater_seen_at=now_iso())
    try:
        write_state(base, state)
    except Exception:  # noqa: BLE001, S110 - best effort, matches
        # `runner.run_update`'s own `publish`: a full disk must not stop
        # the loop, only skip the one write it could not make.
        pass


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

    # Held in memory across iterations, in addition to `state.json` - see
    # the module docstring. `state.json` alone once meant a corrupt read
    # or a failed write could make an already-completed request look
    # brand new again.
    last_id: str | None = None
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
                _touch_seen_at(base, read_state(base))
            except Exception:  # noqa: BLE001, S110 - best effort, matches every guard here
                pass
        time.sleep(LOOP_INTERVAL_SECONDS)


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else default


if __name__ == "__main__":
    main()
