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

"""The bridge's side of self-updating - WebUI updates design
(2026-09-08), sections 7, 10 and 11.

`build_update_router` builds an `APIRouter` with prefix `/api`, wired into
`loxone.server.build_app` behind the same `api_guard` as every other route
under `/api` (section 10: no second password prompt, the same session
already downloads the Fabric backup).

**The bridge never runs docker or git.** That is the whole point of the
file-based protocol in `update.protocol`: the bridge only writes
`request.json` and reads `state.json`, exactly like this task's brief
says, and the three routes here do nothing else. In particular, this
module does NOT mount the checkout the sidecar mounts at `/checkout`
(compare `deploy/testhost/docker-compose.yml`'s `loxmatter` service, which
carries no such mount, against its `updater` service, which does) - so it
has no `.git` to ask a descendant question of. See `_no_git_access` below
for what that means for `POST /update/apply`'s own pre-check.

**The running version comes from `build_info()`, not from `state.json`.**
`to_version`/`from_version` in the state file are the updater's own
bookkeeping about what it last aimed for or started from; `build_info()`
reads the environment CI baked into the running image, the one thing that
actually knows what is running right now. `GET /update/status` returns
both - see `UpdateStatusOut`'s docstring for why a disagreement between
them is meant to be visible, not resolved away.

**"No updater installed" is not "no update running".** `state.json`'s
`updater_seen_at` is rewritten on every pass of the sidecar's loop, even
a pass that does nothing (`update.__main__._touch_seen_at`) - so a stamp
that has stopped moving is exactly as informative as a missing file: both
mean there is nothing here that could act on a request. `_updater_present`
below treats a stamp older than `_STALE_AFTER_SECONDS` the same as a
missing `state.json`. The exact multiplier of `LOOP_INTERVAL_SECONDS` is a
judgment call, not a value the design or the plan pins down anywhere - see
this module's test file and the task report for the reasoning.

Fifteen intervals (thirty seconds against a two-second loop), not five: a
health-check poll inside `runner._wait_for_health` republishes `state.json`
only every `runner.HEALTH_REFRESH_POLLS` polls (`runner.py:56`), and each
of those polls can itself cost up to `2 * runner.POLL_SECONDS` once
`commands.DockerCommands.healthy()`'s subprocess call is bounded rather
than unbounded (see `commands.py`'s `_HEALTH_TIMEOUT_SECONDS`) - a
worst case of sixteen seconds between refreshes during an otherwise
perfectly healthy update. Five intervals (ten seconds) left that six
seconds of *negative* margin, not the two seconds the old comment here
claimed on the assumption that a poll costs nothing: an update running
completely normally could make this route say "no updater installed" mid-
update, the exact false message this whole mechanism exists to avoid.
Thirty seconds clears the sixteen-second worst case with real headroom
while staying short enough that an operator does not wait long for an
honest answer; `tests/api/test_update.py` pins the relationship against
`runner.HEALTH_REFRESH_POLLS`/`runner.POLL_SECONDS` directly, so the two
sides cannot drift apart again with nothing checking.

**`GET /update/check` never raises for network reasons.** An installation
behind a firewall, or one whose operator switched the outbound check off
in a later task, is not broken - it just cannot say more than "last
checked: could not reach GitHub". `_query_github` therefore turns every
network failure into `_CheckUnavailable` on the way in, and the route
turns that into a normal 200 response with `error` set, never a 500.

**`_updater_present` reads `state.json`'s `phase`, not only its age.** A
flat staleness threshold sized for the health-check wait (see
`_STALE_AFTER_SECONDS` below) is far too short for the other windows a
real update spends between two writes of the file - `git fetch` +
`docker pull` alone can legitimately run for twenty minutes on the slow
link this feature ships to (`commands._IMAGE_PULL_TIMEOUT_SECONDS`).
Against a flat threshold, the interface told the operator "no updater
installed" for most of an update that was proceeding completely
normally - the exact false message this mechanism exists to avoid,
recurring in a longer window than the one already fixed. A `state.json`
whose `phase` is one of the non-terminal phases is itself evidence the
updater wrote it recently enough to still be running that step, so
`_updater_present` accepts a stamp aged past `_STALE_AFTER_SECONDS` for
one of those phases, up to a bound computed from the same subprocess
timeouts that bound the step itself - see `_BUSY_BOUND_SECONDS` below for
the derivation, one phase at a time. The bound is still finite: a
genuinely wedged updater (a step that started and then the container
died, say) ages past it and reads as absent, same as before."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from loxmatter import i18n
from loxmatter.api.models import (
    UpdateApplyIn,
    UpdateApplyOut,
    UpdateCheckOut,
    UpdateStatusOut,
)
from loxmatter.model.store import Store
from loxmatter.model.update_settings_store import UpdateChannel
from loxmatter.timestamps import now_iso
from loxmatter.update.__main__ import LOOP_INTERVAL_SECONDS
from loxmatter.update.commands import (
    _GIT_FETCH_TIMEOUT_SECONDS,
    _HEALTH_TIMEOUT_SECONDS,
    _IMAGE_PULL_TIMEOUT_SECONDS,
    _LOCAL_TIMEOUT_SECONDS,
    _RECREATE_TIMEOUT_SECONDS,
)
from loxmatter.update.protocol import (
    UpdatePhase,
    UpdateRequest,
    UpdateState,
    read_state,
    write_request,
)
from loxmatter.update.rules import IMAGE_REPOSITORY, RequestRejected, check_request
from loxmatter.update.runner import HEALTH_REFRESH_POLLS, POLL_SECONDS
from loxmatter.version import BuildInfo, build_info

#: See this module's docstring: a judgment call the design and the plan
#: leave open, expressed as a multiple of the sidecar's own loop interval
#: rather than an independently invented number of seconds. Fifteen, not
#: five - the margin over the health-wait refresh cadence in `runner.py`
#: has to survive a bounded-but-nonzero poll cost, not just a sleep. This
#: is the FAST-PATH threshold only now: it is what decides presence for
#: `idle`/`done`/`failed` (nothing is running, a fresh touch every loop
#: pass is all there ever is) and it is also always tried first for a
#: busy phase, because it is tight and usually all a healthy update needs
#: - `_BUSY_BOUND_SECONDS` below is the fallback for the phases whose
#: legitimate windows run far longer than this.
_STALE_AFTER_SECONDS = 15 * LOOP_INTERVAL_SECONDS

#: Worst-case time between two publishes of `state.json` while
#: `_wait_for_health` is polling - one poll's subprocess call
#: (`commands._HEALTH_TIMEOUT_SECONDS`, a backstop equal to
#: `runner.POLL_SECONDS` itself) plus the `sleep(POLL_SECONDS)`
#: `_wait_for_health` always pays afterwards, `HEALTH_REFRESH_POLLS`
#: times over before the next refresh. This is the window
#: `_STALE_AFTER_SECONDS` was originally sized for (see this module's
#: docstring) and it recurs twice in a run: the transition from
#: `phase="recreate"` into the health wait, and the health wait itself,
#: both refreshed on this same cadence - see `runner.HEALTH_REFRESH_POLLS`
#: for the accounting this mirrors.
_HEALTH_REFRESH_WORST_CASE_SECONDS = HEALTH_REFRESH_POLLS * (POLL_SECONDS + _HEALTH_TIMEOUT_SECONDS)

#: How long `state.json` may legitimately sit at each non-terminal phase
#: without a fresh `updater_seen_at`, derived from the actual subprocess
#: timeouts for the steps that run while that phase is the last one
#: published - not a second hand-typed number that has to be kept in
#: agreement with those timeouts by hand, which is exactly how the defect
#: this dict fixes happened in the first place (`_STALE_AFTER_SECONDS`
#: applied everywhere, sized only for the shortest window). Read
#: `runner.run_update` alongside this: each entry is the sum of the
#: `commands.py` timeouts for the steps between the `publish()` call that
#: writes that phase and the next `publish()` call after it.
#:
#: - `"backup"`: published once the backup step itself has completed;
#:   `fetch()`, `checkout()` and `pull()` all run before the next publish
#:   (`"pull"`) - `_GIT_FETCH_TIMEOUT_SECONDS` + `_LOCAL_TIMEOUT_SECONDS`
#:   + `_IMAGE_PULL_TIMEOUT_SECONDS`. By far the largest bound here,
#:   because `_IMAGE_PULL_TIMEOUT_SECONDS` is: a real image pull on the
#:   slow rural link this ships to legitimately takes most of it.
#: - `"pull"`: `write_image_tag()` is a plain local file edit with no
#:   subprocess call to time out on, so the only bounded step before the
#:   next publish (`"recreate"`) is `recreate()` itself -
#:   `_RECREATE_TIMEOUT_SECONDS`.
#: - `"recreate"`: the phase published right before `wait_for_health`
#:   starts polling - `_HEALTH_REFRESH_WORST_CASE_SECONDS` until the
#:   first refresh republishes (as `"health"`).
#: - `"health"`: `wait_for_health` republishes on the same cadence for as
#:   long as it keeps polling - `_HEALTH_REFRESH_WORST_CASE_SECONDS`
#:   again. (Included explicitly even though it is no larger than
#:   `_STALE_AFTER_SECONDS` already covers, so this dict states the
#:   reasoning for every non-terminal phase in one place rather than
#:   leaving one to be covered by the fast path's coincidence.)
#: - `"rollback"`: `roll_back` writes the previous tag (again a plain
#:   file edit), then `checkout()` and `recreate()` before its own
#:   confirmation health wait starts - `_LOCAL_TIMEOUT_SECONDS` +
#:   `_RECREATE_TIMEOUT_SECONDS` + `_HEALTH_REFRESH_WORST_CASE_SECONDS`
#:   until that wait's first refresh.
#:
#: `"idle"`, `"done"` and `"failed"` are deliberately absent: they are
#: terminal - nothing is running that could justify a widened bound - so
#: they fall through to the fast path above like any other quiet moment.
#: A margin is added on top of every entry the same way
#: `_STALE_AFTER_SECONDS` itself carries one over the bare health-wait
#: figure, for the same reason: real headroom, not a bound computed to
#: the second and then immediately eroded by the next unrelated change to
#: one of these timeouts.
_BUSY_MARGIN_SECONDS = 15.0
_BUSY_BOUND_SECONDS: dict[UpdatePhase, float] = {
    "backup": (
        _GIT_FETCH_TIMEOUT_SECONDS
        + _LOCAL_TIMEOUT_SECONDS
        + _IMAGE_PULL_TIMEOUT_SECONDS
        + _BUSY_MARGIN_SECONDS
    ),
    "pull": _RECREATE_TIMEOUT_SECONDS + _BUSY_MARGIN_SECONDS,
    "recreate": _HEALTH_REFRESH_WORST_CASE_SECONDS + _BUSY_MARGIN_SECONDS,
    "health": _HEALTH_REFRESH_WORST_CASE_SECONDS + _BUSY_MARGIN_SECONDS,
    "rollback": (
        _LOCAL_TIMEOUT_SECONDS
        + _RECREATE_TIMEOUT_SECONDS
        + _HEALTH_REFRESH_WORST_CASE_SECONDS
        + _BUSY_MARGIN_SECONDS
    ),
}

#: Not covered by `_BUSY_BOUND_SECONDS` above, and worth naming rather
#: than leaving implicit: `commands.DockerCommands.backup()` itself
#: (bounded by `commands._BACKUP_TIMEOUT_SECONDS`, 120s) runs BEFORE
#: `run_update`'s first `publish()` call, so while it is in progress
#: `state.json` still carries whatever phase the PREVIOUS run left it in
#: - `"idle"`/`"done"`/`"failed"`, all terminal. A backup slower than
#: `_STALE_AFTER_SECONDS` can therefore still read as "no updater
#: installed" for the tail of that window. Smaller and rarer than the
#: window this fix targets (120s at most, against the pull window's
#: twenty minutes) and left as a known gap here rather than closed by
#: moving `publish()` earlier in `runner.py`, which would change what
#: "phase" means for every consumer of `state.json`, not just this route
#: - a larger change than this fix warrants on its own.

#: `IMAGE_REPOSITORY` is a Docker reference ("ghcr.io/lucienkerl/loxmatter"),
#: not a GitHub API path - the registry host differs from the source
#: host, even though the account/repository segment is shared between
#: them by convention in this project.
_GITHUB_REPO = IMAGE_REPOSITORY.removeprefix("ghcr.io/")
_GITHUB_API = f"https://api.github.com/repos/{_GITHUB_REPO}"

#: Short on purpose (design section 9: "the tile has to say something
#: sensible" without the operator waiting on it) - this is an outbound
#: call made from a request handler, not a background job.
_GITHUB_TIMEOUT_SECONDS = 5.0

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class _CheckUnavailable(Exception):
    """GitHub could not be asked, or answered with something unusable -
    caught in `check_for_update` and turned into `UpdateCheckOut.error`
    rather than an HTTP failure (see the module docstring)."""


def _no_git_access(_older: str, _newer: str) -> bool:
    """`check_request`'s `is_ancestor` callback, for the bridge's own
    pre-check of a `dev`-channel request.

    This process has no checkout to ask `git merge-base --is-ancestor`
    against (see the module docstring) - only the sidecar does. Returning
    `True` unconditionally means this route's pre-check never blocks a
    `dev` request on the one rule it cannot actually verify; the other two
    rules (valid channel, valid target shape) and the `stable` channel's
    semantic forward-only check still get their fast, on-screen rejection
    from the very same `check_request` call below, unchanged. Design
    section 10 is explicit that the ancestry rule is the sidecar's alone
    to enforce - a `dev` request this route lets through still meets that
    check, unchanged, a loop interval later; this function defers to it
    rather than guessing at an answer it cannot compute."""
    return True


def _updater_present(state: UpdateState | None) -> bool:
    if state is None:
        return False
    try:
        seen_at = datetime.fromisoformat(state.updater_seen_at)
    except ValueError:
        # A hand-edited or otherwise malformed timestamp is exactly as
        # uninformative as a missing one - see `read_state`'s own
        # docstring for the same reasoning applied to the rest of the
        # file's shape.
        return False
    if seen_at.tzinfo is None:
        seen_at = seen_at.replace(tzinfo=UTC)
    age_seconds = (datetime.now(UTC) - seen_at).total_seconds()
    if age_seconds <= _STALE_AFTER_SECONDS:
        return True
    # The fast path above missed - either genuinely stale, or a stamp
    # that has not been refreshed in a while because a real subprocess
    # call is legitimately still running. `phase` distinguishes the two:
    # only a non-terminal phase is evidence the sidecar itself wrote this
    # file for a run currently under way, and even then only up to the
    # bound `_BUSY_BOUND_SECONDS` derives from the steps that phase
    # covers - a bound that is always finite, so a phase string sitting
    # in the file cannot make presence permanent on its own. See the
    # module docstring and `_BUSY_BOUND_SECONDS`'s own comment for the
    # full derivation.
    busy_bound = _BUSY_BOUND_SECONDS.get(state.phase)
    return busy_bound is not None and age_seconds <= busy_bound


def _status_out(state: UpdateState | None, info: BuildInfo) -> UpdateStatusOut:
    if state is None:
        return UpdateStatusOut(
            updater_present=False,
            running_version=info.version,
            running_commit=info.commit,
            id=None,
            phase="idle",
            steps=[],
            from_version=None,
            to_version=None,
            error=None,
            rolled_back=False,
            healthy=False,
            updater_seen_at=None,
        )
    return UpdateStatusOut(
        updater_present=_updater_present(state),
        running_version=info.version,
        running_commit=info.commit,
        id=state.id,
        phase=state.phase,
        steps=list(state.steps),
        from_version=state.from_version,
        to_version=state.to_version,
        error=state.error,
        rolled_back=state.rolled_back,
        healthy=state.healthy,
        updater_seen_at=state.updater_seen_at,
    )


def _parse_semver(value: str) -> tuple[int, int, int] | None:
    match = _SEMVER.match(value)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _default_session_factory() -> Any:
    # Lazily imported, like `matter/otbr.py`'s own `_default_session_factory`
    # - a test that supplies its own session should never need aiohttp
    # installed, and this module has no reason to import it at collection
    # time either.
    import aiohttp

    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_GITHUB_TIMEOUT_SECONDS))


async def _fetch_github(url: str) -> dict[str, Any]:
    session = _default_session_factory()
    try:
        try:
            async with session.get(
                url, headers={"Accept": "application/vnd.github+json"}
            ) as response:
                status = response.status
                payload = await response.json(content_type=None)
        except Exception as exc:
            # Deliberately `Exception`, not just `aiohttp.ClientError` -
            # this module does not import aiohttp itself (see
            # `_default_session_factory`), and an unreachable GitHub is,
            # to this caller, the same case as one that answers with
            # something unparsable: a reason, not a crash.
            raise _CheckUnavailable(str(exc)) from exc
    finally:
        await session.close()
    if status != 200:
        raise _CheckUnavailable(f"GitHub answered with HTTP {status}")
    return payload if isinstance(payload, dict) else {}


async def _query_github(channel: UpdateChannel, info: BuildInfo) -> tuple[str | None, bool]:
    """Returns `(latest, available)` for the given channel - design
    section 9: `stable` compares against the latest GitHub release,
    `dev` against the tip of `main`."""
    if channel is UpdateChannel.DEV:
        payload = await _fetch_github(f"{_GITHUB_API}/commits/main")
        sha = str(payload.get("sha") or "")
        if not sha:
            raise _CheckUnavailable("GitHub response carried no commit sha")
        # Without a running commit (a development checkout, never built by
        # CI - see `version.build_info`'s docstring) there is nothing to
        # compare against; that is not a network failure, so it is not
        # `_CheckUnavailable`, just "nothing available to report".
        available = info.commit is not None and not sha.startswith(info.commit)
        return sha[:7], available

    payload = await _fetch_github(f"{_GITHUB_API}/releases/latest")
    tag = str(payload.get("tag_name") or "")
    if not tag:
        raise _CheckUnavailable("GitHub response carried no release tag")
    latest_version = _parse_semver(tag)
    current_version = _parse_semver(info.version)
    available = (
        latest_version is not None
        and current_version is not None
        and latest_version > current_version
    )
    return tag, available


def build_update_router(store: Store, *, base: Path) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/update/status")
    async def get_update_status() -> UpdateStatusOut:
        return _status_out(read_state(base), build_info())

    @router.get("/update/check")
    async def check_for_update() -> UpdateCheckOut:
        info = build_info()
        channel = store.update_settings.get_channel()
        try:
            latest, available = await _query_github(channel, info)
        except _CheckUnavailable as exc:
            return UpdateCheckOut(
                channel=channel.value,
                running_version=info.version,
                available=False,
                latest=None,
                error=i18n.t("api.update.check_unavailable", reason=str(exc)),
            )
        return UpdateCheckOut(
            channel=channel.value,
            running_version=info.version,
            available=available,
            latest=latest,
            error=None,
        )

    @router.post("/update/apply")
    async def apply_update(body: UpdateApplyIn) -> UpdateApplyOut:
        info = build_info()
        request = UpdateRequest(
            id=secrets.token_hex(16),
            channel=body.channel.value,
            target=body.target,
            requested_at=now_iso(),
        )
        try:
            check_request(
                request,
                running_version=info.version,
                running_commit=info.commit or "",
                is_ancestor=_no_git_access,
            )
        except RequestRejected as exc:
            # `exc.reason` is already written for a person, not a log grep
            # (see `rules.RequestRejected`'s own docstring) - passed
            # through unchanged, the same pattern `api/devices.py` uses
            # for exceptions raised by a lower layer it does not own.
            raise HTTPException(status_code=400, detail=exc.reason) from exc
        write_request(base, request)
        return UpdateApplyOut(id=request.id)

    return router
