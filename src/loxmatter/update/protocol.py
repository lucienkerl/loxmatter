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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

#: Where the bridge and the sidecar meet inside the container, named here
#: because the plan's Task 2 interface list puts it here
#: (`docs/superpowers/plans/2026-09-09-webui-updates-stage-2.md:325`:
#: "Tasks 3, 4 and 7 all use these"). `__main__.DEFAULT_BASE` aliases
#: this rather than redefining it - `base` is a plain parameter
#: everywhere else in this package (`run_once`, `run_update`, every
#: function below), so this is the one place the actual path is written
#: down.
UPDATE_DIR = Path("/data/update")

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


def failure_path(base: Path) -> Path:
    """Where `runner.run_update` writes a plain-text account of a run that
    ended in a state a person has to act on - see the docstring at its
    one call site for exactly which states those are.

    `LAST-FAILURE.txt`: the plan that first named this file
    (`docs/superpowers/plans/2026-09-08-webui-updates-stage-2.md`) called
    it `LETZTER-FEHLSCHLAG.txt` - correct at the time, wrong now that the
    whole repository is English-only (see CLAUDE.md; the translation
    predates that rule by a day). Uppercase, unlike its two siblings
    above: `request.json`/`state.json`/`log.txt` are machine-read, this
    one is written for a person reading `ls /data/update/` over SSH with
    the web interface unreachable, and the name should say so on sight."""
    return base / "LAST-FAILURE.txt"


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

    `error` stays English and unabbreviated, same reasoning as
    `rules.RequestRejected.reason`: it is what `runner._log` writes into
    `log.txt`, which CLAUDE.md says is never localised. `error_i18n_key`/
    `error_i18n_values` are the same failure for the operator - set only
    when the failure IS one of the six known `rules.check_request`
    rejections (`runner.run_update`'s `except RequestRejected` branch is
    the only place that has a key to give), `None`/empty for every other
    failure this file can describe (a step that raised, a health check
    that timed out, a rollback that itself failed) - free-form exception
    text with no translation to offer, which stays English same as
    `error` itself. `api/update.py`'s `_status_out` resolves the pair
    through `i18n.t()` in the operator's selected language before the
    interface ever sees it; `state.json` is the only channel between the
    sidecar (which has no database, and therefore no idea which language
    is selected) and the bridge (which does), so both representations
    have to travel through this file the same way `RequestRejected`
    itself carries both.

    Defaulted, not required: every other constructor of this dataclass
    (`_repair_state`'s fresh `idle` state, every test written before this
    pair existed) has nothing translatable to report and should not have
    to say so at every call site.

    `previous_tag`/`previous_ref`/`backup_path`/`running_schema_version`
    are the four values design section 8 requires recorded before the
    first step runs: what `LOXMATTER_IMAGE_TAG` and the git ref were
    before this run touched either, where the fresh backup landed, and
    the schema version of the code that was running when the update
    started. They exist for the case the rest of this file's rollback
    machinery is built to survive - the bridge does not come back - where
    they are what a person reads over SSH (in `runner.failure_path`'s
    file) or a script reads from this one to finish the job by hand.
    Defaulted to `None` for the same reason the two fields above are:
    every state this dataclass ever described before they existed had no
    such values to give, and every one of them (a fresh `idle` state, a
    request rejected before `run_update`'s prelude even starts reading
    `.env`) still does not."""

    id: str | None
    phase: UpdatePhase
    steps: Sequence[str]
    from_version: str | None
    to_version: str | None
    error: str | None
    rolled_back: bool
    healthy: bool
    updater_seen_at: str
    error_i18n_key: str | None = None
    error_i18n_values: Mapping[str, str] = field(default_factory=dict)
    previous_tag: str | None = None
    previous_ref: str | None = None
    backup_path: str | None = None
    running_schema_version: int | None = None


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
        # Absent on any `state.json` written before this pair existed -
        # an older sidecar image, or any of the fixtures/tests that predate
        # it. Same tolerance as `steps` above: the wrong shape reads as
        # "nothing translatable to report", not as a reason to treat the
        # whole file as unreadable.
        error_i18n_key = payload.get("error_i18n_key")
        if error_i18n_key is not None and not isinstance(error_i18n_key, str):
            error_i18n_key = None
        error_i18n_values = payload.get("error_i18n_values") or {}
        if not isinstance(error_i18n_values, dict):
            error_i18n_values = {}
        # Same tolerance again, for the four fields design section 8 added:
        # absent on any `state.json` a sidecar written before they existed
        # left behind, and the wrong shape is exactly as uninformative as
        # absent - neither is a reason to treat the whole file as
        # unreadable.
        previous_tag = payload.get("previous_tag")
        if previous_tag is not None and not isinstance(previous_tag, str):
            previous_tag = None
        previous_ref = payload.get("previous_ref")
        if previous_ref is not None and not isinstance(previous_ref, str):
            previous_ref = None
        backup_path = payload.get("backup_path")
        if backup_path is not None and not isinstance(backup_path, str):
            backup_path = None
        running_schema_version = payload.get("running_schema_version")
        # `bool` is an `int` subclass - excluded explicitly so a stray
        # `true`/`false` in this field does not silently pass as 0/1.
        if not isinstance(running_schema_version, int) or isinstance(running_schema_version, bool):
            running_schema_version = None
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
            error_i18n_key=error_i18n_key,
            error_i18n_values={str(k): str(v) for k, v in error_i18n_values.items()},
            previous_tag=previous_tag,
            previous_ref=previous_ref,
            backup_path=backup_path,
            running_schema_version=running_schema_version,
        )
    except KeyError:
        return None


def write_state(base: Path, state: UpdateState) -> None:
    write_json_atomically(state_path(base), asdict(state))
