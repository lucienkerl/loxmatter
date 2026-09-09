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
from loxmatter.update.protocol import UpdatePhase, UpdateRequest, UpdateState, write_state
from loxmatter.update.rules import RequestRejected, check_request, image_reference, image_tag

#: How long to wait for `/health` before calling the update failed. Not a
#: new number: it comes from `scripts/update.sh`, whose comment records
#: why 20 s was too tight - one run went over and the script reported a
#: healthy service as sick. Too short a window is the more expensive kind
#: of false alarm.
DEADLINE_SECONDS = 120.0
POLL_SECONDS = 2.0

#: How often `_wait_for_health` republishes `state.json` while it polls,
#: expressed as a multiple of `POLL_SECONDS`. The wait can run for the
#: whole two-minute deadline without a single publish otherwise, and
#: `api/update.py`'s `_updater_present` treats a stamp older than
#: `_STALE_AFTER_SECONDS` as "no updater installed" - exactly the wrong
#: answer while an update is running normally. Refreshing on every poll
#: would mean up to sixty extra atomic writes into the shared directory
#: on an SD card over one wait, for a value nothing reads that often.
#:
#: The worst-case time between two refreshes is not simply
#: `HEALTH_REFRESH_POLLS * POLL_SECONDS`: each poll also pays for
#: `commands.DockerCommands.healthy()`'s own subprocess call, which
#: `commands.py`'s `_HEALTH_TIMEOUT_SECONDS` bounds at `POLL_SECONDS`
#: itself as a backstop (curl's own `--max-time` is tighter in the normal
#: case - see there). So one poll can cost up to `2 * POLL_SECONDS`
#: before `_wait_for_health`'s `sleep(POLL_SECONDS)` even runs, and four
#: of those is up to sixteen seconds - `api/update.py`'s
#: `_STALE_AFTER_SECONDS` has to clear that with real headroom, not the
#: two seconds the previous, unbounded-poll version of this comment
#: assumed; `tests/api/test_update.py` pins the relationship so the two
#: constants cannot drift apart with nothing checking again.
HEALTH_REFRESH_POLLS = 4

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


def _log(commands: Commands, line: str) -> None:
    """`commands.log` appends to a file in the shared directory, and a
    full SD card makes that an ordinary way for an update to fail. The
    diagnostic line is a courtesy to whoever reads the log afterwards; it
    must never be able to turn into the exception that breaks the one
    invariant this module has - no exception leaves `run_update`. Best
    effort: on failure, the line is simply not recorded."""
    try:
        commands.log(line)
    except Exception:  # noqa: BLE001, S110 - see docstring; logging must never escape
        pass


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
        phase: UpdatePhase,
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
        try:
            write_state(base, state)
        except Exception:  # noqa: BLE001, S110 - best effort, never an escape
            # `write_state` can fail the same way `commands.log` can - a
            # full disk mid-update is an ordinary failure, not an
            # exceptional one. `run_update`'s contract with every caller
            # is "return a correct state object", not "return one only if
            # it could also be written to disk". The in-memory state is
            # still accurate even when the file that should mirror it
            # could not be updated.
            pass
        return state

    def wait_for_health(*, waiting_phase: UpdatePhase, error: str | None) -> bool:
        """Wraps `_wait_for_health`, republishing `state.json` every
        `HEALTH_REFRESH_POLLS` polls so `updater_seen_at` keeps moving
        during a wait that can run up to `deadline_seconds`. `waiting_phase`
        and `error` are held constant across every refresh - the wait
        itself does not change what is going on, only how long it has
        been going on for."""

        def refresh() -> None:
            publish(waiting_phase, error=error)

        return _wait_for_health(commands, sleep, deadline_seconds, refresh)

    try:
        check_request(
            request,
            running_version=running_version,
            running_commit=running_commit,
            is_ancestor=is_ancestor,
        )
        # Both of these are read BEFORE anything is written, and they are
        # two different strings - see `read_git_ref` in the Protocol
        # above. Reading them inside this same `try` matters:
        # like `check_request`, they run before the first command, so a
        # failure here must read like a rejection too.
        previous_tag = commands.read_image_tag()
        previous_ref = commands.read_git_ref()
    except RequestRejected as rejected:
        # Before the first command, so a rejected request has taken no
        # backup and pulled no image - it left the installation exactly as
        # it found it.
        _log(commands, f"refused: {rejected.reason}")
        return publish("failed", error=rejected.reason)
    except Exception as failure:  # noqa: BLE001 - nothing has run yet, see below
        # `is_ancestor` shells out to git on the `dev` channel (there is
        # no `commands.py` yet to catch its failures for us), and
        # `read_image_tag`/`read_git_ref` read files that may not be
        # there. All three run before the first command, so nothing has
        # been executed and no rollback applies - the installation is
        # exactly as it was found, same as a rejected request. The
        # exception must not reach the caller: it has to become the same
        # published `failed` state a rejection produces, or the operator
        # sees a phase that stopped being true (or nothing at all, if
        # this was the very first thing `run_update` did).
        _log(commands, f"could not prepare the update: {failure!r}")
        return publish("failed", error=str(failure))

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

        The command sequence below is guarded. `state.json` is the only
        channel back to the operator - the sidecar writes it, the web
        interface reads it - so an exception that escaped this function
        would leave the file saying `phase: "rollback"` forever, with the
        interface stuck showing "rolling back" and the operator learning
        nothing. A rollback that does not complete has to publish that
        fact, not vanish.
        """
        _log(commands, f"rolling back to {previous_tag} ({previous_ref}): {reason}")
        publish("rollback", error=reason)
        restored_tag = False
        restored_ref = False
        try:
            commands.write_image_tag(previous_tag)
            restored_tag = True
            commands.checkout(previous_ref)
            restored_ref = True
            commands.recreate()
        except Exception as rollback_failure:  # noqa: BLE001 - must publish, not propagate
            # Name only what actually completed. `recreate` throwing is
            # not the only way in here: `checkout` can throw too, and if
            # it does the git ref was never restored - saying otherwise
            # would tell whoever reads this log that only `up -d` is
            # needed to finish by hand, which for someone who believes the
            # ref is back deploys the checked-out (new) revision's
            # docker-compose.yml instead of the old one.
            if restored_ref:
                what = (
                    f"image tag and git ref were restored to {previous_tag} "
                    f"({previous_ref}), but the container was not recreated"
                )
            elif restored_tag:
                what = (
                    f"the image tag was restored to {previous_tag}, but the git ref "
                    "is still on the new revision and the container was not recreated"
                )
            else:
                what = "nothing was restored - not even the image tag"
            _log(
                commands,
                f"rollback did not complete: {what} - a person has to finish this by "
                f"hand ({rollback_failure!r})",
            )
            return publish(
                "failed",
                error=f"{reason}; rollback also failed: {rollback_failure}",
                rolled_back=False,
                healthy=False,
            )
        recovered = wait_for_health(waiting_phase="rollback", error=reason)
        return publish("failed", error=reason, rolled_back=True, healthy=recovered)

    try:
        _log(commands, f"backing up before {request.target}")
        commands.backup()
        done.append("backup")
        publish("backup")

        commands.fetch()
        commands.checkout(request.target)
        # The checked-out ref goes in the log on purpose (addendum 16.2):
        # a checkout rewrites docker-compose.yml on disk, and whoever
        # reads this log afterwards has to be able to see that the file
        # changed under them.
        _log(commands, f"checked out {request.target}; compose file on disk is now that revision")
        commands.pull(image_reference(request.target))
        done.append("pull")
        publish("pull")

        # `image_tag`, not `request.target` - see its docstring in
        # `rules.py`. Writing the git-form target here was a real bug: the
        # tag that lands in `.env` has to be the Docker form CI actually
        # published, the same tag `pull` two lines up just used.
        commands.write_image_tag(image_tag(request.target))
        commands.recreate()
        done.append("recreate")
        publish("recreate")
    except Exception as failure:  # noqa: BLE001 - any failed step rolls back
        # Broad on purpose. Whatever went wrong, the tag may already point
        # at a version that never arrived, and that is exactly the state a
        # later `up -d` would deploy. The traceback goes to the log so the
        # cause survives.
        _log(commands, f"step failed: {failure!r}")
        return roll_back(str(failure))

    if not wait_for_health(waiting_phase="health", error=None):
        # `_wait_for_health` already treats a raising `healthy()` as "not
        # healthy" (see its docstring), and that is what makes this branch
        # reachable at all: a real `healthy()` polling `/health` over HTTP
        # raises rather than returning `False` while the bridge is down,
        # which is precisely the condition this poll exists to detect.
        return roll_back(f"the bridge did not answer /health within {deadline_seconds:.0f}s")

    done.append("health")
    _log(commands, f"update to {request.target} complete")
    return publish("done", healthy=True)


def _wait_for_health(
    commands: Commands,
    sleep: Callable[[float], None],
    deadline_seconds: float,
    refresh: Callable[[], None],
) -> bool:
    """Poll until healthy or the deadline passes.

    Elapsed time is counted from the polls rather than read off the wall
    clock, so a test can drive the whole wait with a `sleep` that returns
    immediately - otherwise the rollback test would take two real minutes
    and be skipped as slow within a month.

    A raising `healthy()` is treated as a `False` for that one poll, not
    as a reason to stop polling or to let the exception through. A real
    `healthy()` calls `/health` over HTTP, and while the bridge is down -
    the exact condition this whole function exists to detect - that call
    raises `ConnectionError` instead of returning `False`. Both callers
    (the main wait and `roll_back`'s own confirmation) rely on this: an
    unreachable bridge must read as "not healthy", which for the main
    caller means a rollback runs at all, and for `roll_back` means
    `healthy=False` is published instead of the exception escaping
    `run_update` and leaving `state.json` frozen.

    `refresh` is called every `HEALTH_REFRESH_POLLS` polls, not on every
    one - see that constant's docstring for the cadence tradeoff. It is
    never called before the first poll: `run_update` has just published a
    fresh stamp for the phase this wait belongs to (`recreate` before the
    main wait, `rollback` before the confirmation wait), so the stamp is
    already current when the wait begins.
    """
    waited = 0.0
    polls = 0
    while waited < deadline_seconds:
        try:
            if commands.healthy():
                return True
        except Exception:  # noqa: BLE001, S110 - a raising poll means "not healthy"
            pass
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        polls += 1
        if polls % HEALTH_REFRESH_POLLS == 0:
            refresh()
    return False
