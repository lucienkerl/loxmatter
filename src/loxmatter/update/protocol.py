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
from typing import Any, Literal, get_args

#: The eight values the plan's interface list names. Defined once here and
#: used both for `UpdateState.phase` and for `run_update`'s `publish`
#: helper (`runner.py`), so a typo in either place is a `mypy` error
#: instead of a string that flows silently into the shared file.
UpdatePhase = Literal["idle", "backup", "pull", "recreate", "health", "done", "rollback", "failed"]

#: Runtime counterpart of `UpdatePhase`, for membership checks against
#: values coming out of JSON - which arrive as `Any`, so a cast alone
#: would prove nothing.
_PHASES: tuple[str, ...] = get_args(UpdatePhase)


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
    phase: UpdatePhase
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
    try:
        os.replace(tmp, path)
    except OSError:
        # The atomicity guarantee is unaffected either way - readers only
        # ever open the final path - but a `.tmp` file left behind would
        # sit in the directory forever, so clean it up before re-raising.
        tmp.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Missing (`FileNotFoundError`, an `OSError` subclass) reads the
        # same as torn or binary content (`UnicodeDecodeError` - a write
        # caught mid-flight despite `write_json_atomically`, or a hand
        # edit) and the same as any other I/O failure a failing SD card
        # can produce here. "Nothing to do" is the right answer for all
        # of them: raising would end the updater's loop and leave the
        # installation without an updater until someone restarts the
        # container.
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Truncated, or edited by hand on the Pi. Same reasoning as above.
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
        # `phase` and `steps` reach us as `Any` out of `json.loads` - well
        # formed JSON does not imply the right shape. A file like
        # `{"phase": "idle", "steps": null, ...}` or one whose `phase` is
        # not one of the eight known values is exactly as malformed as
        # truncated JSON, and must read the same way: as `None`, not as a
        # crash that stops the sidecar's poll loop.
        phase = payload["phase"]
        if phase not in _PHASES:
            return None
        steps = payload.get("steps", [])
        if not isinstance(steps, list):
            return None
        return UpdateState(
            id=payload.get("id"),
            phase=phase,
            steps=[str(step) for step in steps],
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
