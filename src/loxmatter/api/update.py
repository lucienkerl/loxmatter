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
(2026-09-08), sections 7, 9, 10, 11, 12 and 13.

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

**`GET /update/check` is cached, not re-run on every call.** Design
section 9: queried "once daily and when opening the System tab, result
cached". The interface calls this route far more often than once a day in
practice - every channel toggle, every System-tab open - and GitHub's
unauthenticated budget is 60 requests per hour per IP, shared with
whatever else is on the same network. `build_update_router` holds the
cache in a closure local to the router it builds, not at module scope: a
module-level cache would leak between the app instances each test in
`tests/api/test_update.py` builds, and there is exactly one router built
in the real process anyway.

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
died, say) ages past it and reads as absent, same as before.

**`GET /update/status`'s `error` is localised, not a raw copy of
`state.json`'s.** On the `dev` channel this route's own pre-check cannot
enforce the ancestry rule (see `_no_git_access` below), so a request that
fails it is refused only by the sidecar - `runner.run_update`'s own
`check_request` call, which is where `RequestRejected`'s `i18n_key`/
`i18n_values` come from in the first place (design section 10's ancestry
rule, enforced there and only there). `state.json` is the one channel
back to this process, so those two travel through it alongside the
English `error` `log.txt` also gets - see `UpdateState`'s own docstring.
`_localized_error` resolves them into the operator's selected language
before this route ever returns them; a failure with no key (almost every
other failure `run_update` can report - a step that raised, a health
check that timed out) falls back to the same English text the interface
has always shown for those, which CLAUDE.md does not require a
translation for since none of it is prose this codebase itself wrote."""

from __future__ import annotations

import math
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
    _BACKUP_TIMEOUT_SECONDS,
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
    read_request,
    read_state,
    write_request,
)
from loxmatter.update.rules import IMAGE_REPOSITORY, RequestRejected, check_request
from loxmatter.update.runner import DEADLINE_SECONDS, HEALTH_REFRESH_POLLS, POLL_SECONDS
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
#: - `"backup"`: published BEFORE `commands.backup()` runs (see
#:   `runner.run_update`), so the window this bound has to cover starts
#:   there, not after it - `_BACKUP_TIMEOUT_SECONDS` itself, plus
#:   `fetch()`, `checkout()` and `pull()`, all of which also run before
#:   the next publish (`"pull"`) - `_BACKUP_TIMEOUT_SECONDS` +
#:   `_GIT_FETCH_TIMEOUT_SECONDS` + `_LOCAL_TIMEOUT_SECONDS` +
#:   `_IMAGE_PULL_TIMEOUT_SECONDS`. By far the largest bound here,
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
        _BACKUP_TIMEOUT_SECONDS
        + _GIT_FETCH_TIMEOUT_SECONDS
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

#: The prelude that used to leave a gap here (see git history of this
#: comment for the earlier account of it) does not need a state.json
#: stamp at all to close: `apply_update` itself writes `request.json`
#: synchronously, before it ever returns to the caller, so a second
#: `POST /apply` arriving a moment later can compare against that write
#: directly instead of waiting for the sidecar to publish anything about
#: it. `_update_in_progress` below is keyed off the PAIR of files for
#: exactly that reason - see its own docstring.
#:
#: Worst-case wall-clock time `runner._wait_for_health` can spend before
#: giving up, derived the same way its own docstring derives it:
#: `waited` only advances by `POLL_SECONDS` per iteration, but every
#: iteration also pays for `commands.DockerCommands.healthy()`'s own
#: subprocess call, bounded at `_HEALTH_TIMEOUT_SECONDS` - so
#: `ceil(DEADLINE_SECONDS / POLL_SECONDS)` iterations can each cost up to
#: `POLL_SECONDS + _HEALTH_TIMEOUT_SECONDS` of real time before the loop
#: gives up on a 120s deadline that never resolves. `math.ceil` rather
#: than plain division: `_wait_for_health`'s own `while waited <
#: deadline_seconds` keeps polling for a partial interval too, and a
#: bound that rounds down would be exactly as wrong as one that ignores
#: the health check's own subprocess cost.
_WORST_CASE_HEALTH_WAIT_SECONDS = math.ceil(DEADLINE_SECONDS / POLL_SECONDS) * (
    POLL_SECONDS + _HEALTH_TIMEOUT_SECONDS
)

#: The longest `request.json` can legitimately still be waiting on a
#: matching, terminal entry in `state.json` - the second leg
#: `_update_in_progress` needs so that an unreadable `state.json` (see
#: its docstring, and `read_state`'s) cannot make a request look in
#: flight forever. Every term is one of `commands.py`'s own step
#: timeouts or `runner.DEADLINE_SECONDS`, summed along the single
#: longest path `run_update` can take end to end, so a change to any of
#: them changes this bound automatically rather than by someone
#: remembering to also edit a hand-typed number beside it - the exact
#: failure mode this branch has already produced six times over between
#: two constants that had to agree with nothing checking:
#:
#: - The prelude `update.__main__.run_once` and `run_update` pay before
#:   the first `publish()` - `read_running_identity()` (two subprocess
#:   calls), `is_ancestor` (the `dev` channel only, taken unconditionally
#:   since this bound must hold for whichever channel an abandoned
#:   request named) and `read_git_ref()` - four `_LOCAL_TIMEOUT_SECONDS`
#:   calls in the worst case.
#: - `backup()`, `fetch()`, `checkout()` and `pull()`, all of which run
#:   before the `"pull"` phase is published.
#: - `recreate()`, before the `"recreate"` phase is published.
#: - The main health wait, worst case never turning healthy.
#: - One rollback attempt: `write_image_tag` is a plain file edit with
#:   nothing to time out on, so only `checkout()` and `recreate()` are
#:   bounded, followed by the rollback's own confirmation health wait -
#:   worst case never turning healthy either, the single longest thing
#:   `run_update` can still be doing before it is guaranteed to have
#:   published `"done"` or `"failed"`.
#:
#: `_BUSY_MARGIN_SECONDS` on top for the same reason it already sits on
#: every entry in `_BUSY_BOUND_SECONDS` above: real headroom, not a bound
#: computed to the second and then immediately eroded by the next
#: unrelated change to one of these timeouts. `tests/api/test_update.py`
#: recomputes this same formula from the raw constants independently, so
#: a change to any of them that erodes the real margin fails there, not
#: silently.
_MAX_REQUEST_AGE_SECONDS = (
    4 * _LOCAL_TIMEOUT_SECONDS
    + _BACKUP_TIMEOUT_SECONDS
    + _GIT_FETCH_TIMEOUT_SECONDS
    + _LOCAL_TIMEOUT_SECONDS
    + _IMAGE_PULL_TIMEOUT_SECONDS
    + _RECREATE_TIMEOUT_SECONDS
    + _WORST_CASE_HEALTH_WAIT_SECONDS
    + _LOCAL_TIMEOUT_SECONDS
    + _RECREATE_TIMEOUT_SECONDS
    + _WORST_CASE_HEALTH_WAIT_SECONDS
    + _BUSY_MARGIN_SECONDS
)

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

#: How long `GET /update/check`'s result is served from cache before the
#: route asks GitHub again - design section 9: "queried once daily and
#: when opening the System tab, result cached". The interface calls this
#: route far more often than once a day (every channel toggle, every
#: System-tab open); GitHub's unauthenticated budget is 60 requests per
#: hour per IP, shared with whatever else is on the same network, so a
#: few dozen toggles in one sitting could exhaust it and leave the tile
#: stuck showing "could not check" for the rest of the hour. Fifteen
#: minutes keeps at most four real requests per hour even under
#: continuous re-opening of the System tab - comfortable headroom under
#: the budget - while "last checked" still reads as current to someone
#: actually looking at the tile, unlike the literal "once daily" the
#: design text names, which would feel stale for an operator watching
#: for a just-published release.
_CHECK_CACHE_SECONDS = 15 * 60.0

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
    section 10 opens by saying plainly that all three rules - this
    ancestry check on the `dev` channel included - are "enforced in the
    sidecar", not here; a `dev` request this route lets through still
    meets that check, unchanged, a loop interval later, and this function
    defers to it rather than guessing at an answer it cannot compute."""
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
    if age_seconds < 0:
        # A stamp from the future satisfies `<= _STALE_AFTER_SECONDS` for
        # any threshold, however far ahead it is - a clock that jumped
        # backwards (an unsynced RTC on first boot, an NTP correction)
        # would otherwise keep a dead sidecar reading as present forever,
        # with nothing that could ever age it past a threshold it is
        # already "younger" than. Nothing legitimate ever writes a stamp
        # ahead of now, so this is unconditionally untrusted.
        return False
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


_TERMINAL_PHASES: frozenset[UpdatePhase] = frozenset({"done", "failed"})


def _request_expired(request: UpdateRequest, *, now: datetime) -> bool:
    """Whether `request` is older than `_MAX_REQUEST_AGE_SECONDS` - the
    escape hatch `_update_in_progress` falls back on when `state.json`
    cannot prove the request has finished (see there). `requested_at`
    comes from `now_iso()` (`apply_update` sets it), but the file it
    lives in is hand-editable on the Pi like every other file this
    package reads, so it gets the same tolerance `_updater_present` gives
    `state.updater_seen_at` a few lines up: a stamp that fails to parse.

    A stamp that fails to parse, or one from the future, both resolve to
    "expired" here - the opposite of `_updater_present`'s own "untrusted
    stamp reads as absent" rule, but the same underlying reasoning
    applied to the case that must not get stuck: an update genuinely
    running writes a well-formed, past `requested_at` once and never
    touches it again, so a stamp that cannot be trusted to be young must
    not be allowed to hold the apply button shut indefinitely either."""
    try:
        requested_at = datetime.fromisoformat(request.requested_at)
    except ValueError:
        return True
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=UTC)
    age_seconds = (now - requested_at).total_seconds()
    if age_seconds < 0:
        return True
    return age_seconds > _MAX_REQUEST_AGE_SECONDS


def _update_in_progress(
    request: UpdateRequest | None, state: UpdateState | None, *, now: datetime
) -> bool:
    """Whether the request the bridge itself last wrote to `request.json`
    is still waiting on a matching, terminal entry in `state.json`.
    `apply_update` uses this to refuse a second request while one is
    already running (design section 13): the bug this closes let a
    second `POST /apply` overwrite `request.json` mid-run, after which
    `run_once` returns the OLD id once the first `run_update` finishes,
    the next pass sees a DIFFERENT id in `request.json`, and the sidecar
    runs a second full update immediately after the first - the bridge
    recreated twice back to back from one accidental double-click.

    The previous version of this check read `state.json` alone, on the
    theory that a busy-looking, fresh phase was as good a signal as any.
    It was not: `state.json` only starts describing THIS request once
    the sidecar's own `run_once`/`run_update` prelude has finished
    (`read_running_identity()`, `read_git_ref()`, and on the `dev`
    channel `is_ancestor` - see this module's docstring) and calls
    `publish()` for the first time. For up to `_MAX_REQUEST_AGE_SECONDS`'
    own prelude terms of real time, `state.json` still holds whatever the
    PREVIOUS run left it at, and a second `POST /apply` arriving in that
    window read as "nothing running" and was wrongly accepted - the
    reproduction this whole fix closes. `request.json`, by contrast, is
    written synchronously by `apply_update` itself before it ever
    returns 200 to the caller: there is no window during which a request
    this route just accepted is not yet visible to the next one.

    So "in progress" is now: a request exists, AND `state.json` has not
    yet recorded that exact request's id in a terminal phase. Requiring
    the id to match, not just a terminal phase to exist, is what makes
    `test_apply_accepts_a_new_request_once_the_previous_one_finished`
    correct rather than accidentally so - a `state.json` still holding a
    PREVIOUS request's `"done"` must not be mistaken for confirmation
    that THIS request (the one just overwritten into `request.json`,
    with a new id the sidecar has not looked at yet) has finished too.

    The trap: `read_state` returns `None` for a missing `state.json` and
    for a corrupt one alike (see its own docstring), and neither can ever
    prove "finished" - so without a second leg, an unreadable state file
    would make every request look permanently in flight, the same class
    of bug this feature has already been fixed for twice (`updater_seen_at`
    stuck stale, then a phase stuck busy - see `_updater_present`'s own
    docstring for both). `_request_expired` is that second leg: a request
    can only be in flight for as long as an update can possibly take,
    finite by construction, so a corrupt `state.json` - or one whose
    sidecar was deleted along with the container that would have written
    it - ages out and stops blocking, same as a genuinely wedged busy
    phase already had to."""
    if request is None:
        return False
    finished = state is not None and state.id == request.id and state.phase in _TERMINAL_PHASES
    if finished:
        return False
    return not _request_expired(request, now=now)


def _localized_error(state: UpdateState) -> str | None:
    """`state.error` (English, unabbreviated - the same text `log.txt`
    holds, see `UpdateState`'s own docstring) resolved into the operator's
    selected language wherever the sidecar left a translation key to
    resolve it with. `state.json` is the only channel from that process
    to this one - it has no database of its own, so it cannot know which
    language is selected - which is why the key travels as data instead
    of the sidecar (or `runner.py`) trying to guess.

    Falls back to the raw English `state.error` for every failure that
    carries no key: most of them (a step that raised, a health check that
    timed out, a rollback that itself failed) are free-form exception
    text with nothing in `strings.yaml` to resolve against, by design -
    only the six known `rules.check_request` rejections ever carry one
    (see `runner.run_update`'s `except RequestRejected` branch). The same
    fallback also covers a key that fails to resolve - a `state.json`
    written by a sidecar image ahead of, or behind, this bridge's own
    `strings.yaml`, say - `i18n.t()` raising `KeyError` here must not turn
    a routine `GET /update/status` into a 500 over a rejection message
    that would otherwise have displayed just fine in English."""
    if state.error_i18n_key is None:
        return state.error
    try:
        return i18n.t(state.error_i18n_key, **state.error_i18n_values)
    except KeyError:
        return state.error


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
        error=_localized_error(state),
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
    try:
        # Outside the `try`/`finally` that closes the session below on
        # purpose: before this fix, `_default_session_factory()` sat
        # outside every `try` in this function, so an `ImportError` from
        # its own lazy `import aiohttp` (aiohttp missing or broken)
        # propagated straight out as an unhandled exception - a 500 from
        # `GET /update/check`, contradicting this module's own docstring
        # ("never raises for network reasons"). There is no session yet
        # for a `finally` to close if this line itself raises.
        session = _default_session_factory()
    except Exception as exc:
        raise _CheckUnavailable(str(exc)) from exc
    try:
        async with session.get(url, headers={"Accept": "application/vnd.github+json"}) as response:
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

    # `GET /update/check`'s cache (see `_CHECK_CACHE_SECONDS` and the
    # module docstring). A closure local to the router this function
    # builds, not a module-level dict: a module-level cache would leak
    # between the separate `build_update_router` calls every test in
    # `tests/api/test_update.py` makes (each with its own `Store` and
    # `update_base`), and the real process only ever builds one router
    # anyway - there is nothing a module-level dict would buy here that a
    # closure does not.
    check_cache: dict[UpdateChannel, tuple[datetime, UpdateCheckOut]] = {}

    @router.get("/update/status")
    async def get_update_status() -> UpdateStatusOut:
        return _status_out(read_state(base), build_info())

    @router.get("/update/check")
    async def check_for_update() -> UpdateCheckOut:
        info = build_info()
        channel = store.update_settings.get_channel()
        now = datetime.now(UTC)
        cached = check_cache.get(channel)
        if cached is not None and (now - cached[0]).total_seconds() < _CHECK_CACHE_SECONDS:
            return cached[1]
        try:
            latest, available = await _query_github(channel, info)
        except _CheckUnavailable as exc:
            result = UpdateCheckOut(
                channel=channel.value,
                running_version=info.version,
                available=False,
                latest=None,
                error=i18n.t("api.update.check_unavailable", reason=str(exc)),
            )
        else:
            result = UpdateCheckOut(
                channel=channel.value,
                running_version=info.version,
                available=available,
                latest=latest,
                error=None,
            )
        # A failed check is cached too, same lifetime as a successful one
        # - without that, a firewalled installation would retry GitHub on
        # every single call (the exact flood this cache exists to stop),
        # never once benefiting from it.
        check_cache[channel] = (now, result)
        return result

    @router.post("/update/apply")
    async def apply_update(body: UpdateApplyIn) -> UpdateApplyOut:
        if _update_in_progress(read_request(base), read_state(base), now=datetime.now(UTC)):
            # Design section 13: "rejection of a second request while one
            # is running." Checked before anything else, including
            # `check_request` below - a request that would also have
            # failed the rules is still, first and foremost, a second
            # request arriving while the first has not finished. Without
            # this, the reproduction is direct: `request.json` still
            # holding a previous request whose id `state.json` has not
            # yet reported in a terminal phase - `_update_in_progress`'s
            # own docstring has the full account, including why this now
            # reads `request.json` rather than only `state.json`. Without
            # it, `run_once` (`update/__main__.py`) returns the OLD id
            # once the in-flight `run_update` finishes, the very next
            # pass then saw a DIFFERENT id sitting in `request.json` and
            # ran a second full update immediately, back to back with the
            # first, from one accidental double-click.
            raise HTTPException(status_code=409, detail=i18n.t("api.update.already_running"))
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
            # `exc.reason` (English, unabbreviated) is what `runner._log`
            # writes into `log.txt` - never localised, see CLAUDE.md.
            # `exc.i18n_key`/`exc.i18n_values` are the same rejection
            # translated for the operator: design section 12 names this
            # route's error messages among the ones needing en/de pairs,
            # and `rules.py` authors this English prose itself (unlike
            # `api/devices.py`'s `str(exc)` sites, which forward a
            # third-party Matter layer's own message) - so, unlike those,
            # it belongs behind `i18n.t()` here.
            raise HTTPException(
                status_code=400, detail=i18n.t(exc.i18n_key, **exc.i18n_values)
            ) from exc
        write_request(base, request)
        return UpdateApplyOut(id=request.id)

    return router
