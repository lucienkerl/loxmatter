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

`run_once` takes `running_version`/`running_commit` as plain arguments,
the same way `run_update` does, rather than deriving them itself - `main`
resolves them once per iteration (see `_running_identity` below) and hands
them over, so a test never needs a real checkout or a real git binary to
drive a pass.
"""

from __future__ import annotations

import os
import time
import tomllib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from loxmatter.timestamps import now_iso
from loxmatter.update.commands import DockerCommands
from loxmatter.update.protocol import UpdateState, read_request, read_state, write_state
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

#: The mounted checkout `fetch`/`checkout` run against, and - see
#: `_running_version` below - where `pyproject.toml` is read from to learn
#: what is currently deployed.
DEFAULT_COMPOSE_DIR = Path("/app")

DEFAULT_HEALTH_URL = "http://host.docker.internal:8080/health"


class LoopCommands(Commands, Protocol):
    """`Commands`, plus `is_ancestor` - see `DockerCommands`'s docstring
    for why that member sits outside the Protocol `run_update` itself
    takes."""

    def is_ancestor(self, older: str, newer: str) -> bool: ...


def _running_version(compose_dir: Path) -> str:
    """`rules.check_request`'s own comment says where this number comes
    from: "out of `pyproject.toml`'s bare `version = '0.2.0'`" - not from
    `.env`'s `LOXMATTER_IMAGE_TAG`, which is often the literal string
    `stable` and would not parse as a version at all."""
    data = tomllib.loads((compose_dir / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def run_once(
    base: Path,
    commands: LoopCommands,
    *,
    running_version: str,
    running_commit: str,
    is_ancestor: Callable[[str, str], bool],
    sleep: Callable[[float], None],
) -> None:
    previous = read_state(base)
    last_id = previous.id if previous is not None else None
    request = read_request(base)
    if request is not None and request.id != last_id:
        run_update(
            request,
            commands,
            base=base,
            running_version=running_version,
            running_commit=running_commit,
            is_ancestor=is_ancestor,
            sleep=sleep,
        )
        return
    # Nothing to do this pass - `run_update` already stamped
    # `updater_seen_at` on its way out above, so only the quiet path needs
    # to do it here.
    _touch_seen_at(base, previous)


def _touch_seen_at(base: Path, previous: UpdateState | None) -> None:
    """Refresh the one field the bridge polls for aliveness, leaving
    everything else exactly as the last `run_update` (or lack of one) left
    it - a quiet pass must not overwrite a `done` or `failed` result with
    something that looks like `idle`."""
    if previous is None:
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
    commands = DockerCommands(compose_dir=compose_dir, env_path=env_path, health_url=health_url)

    while True:
        try:
            version = _running_version(compose_dir)
            commit = commands.read_git_ref()
        except Exception as failure:  # noqa: BLE001 - see docstring
            # Nothing is executed against an installation whose current
            # identity cannot even be established - the same reasoning
            # `run_update` applies to a failure before its first command
            # (see its docstring). The loop keeps running so the next
            # pass, once whatever is wrong is fixed, can pick the request
            # back up rather than needing the container restarted.
            _log_best_effort(commands, f"cannot determine what is currently running: {failure!r}")
            _touch_seen_at(base, read_state(base))
        else:
            run_once(
                base,
                commands,
                running_version=version,
                running_commit=commit,
                is_ancestor=commands.is_ancestor,
                sleep=time.sleep,
            )
        time.sleep(LOOP_INTERVAL_SECONDS)


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value) if value else default


if __name__ == "__main__":
    main()
