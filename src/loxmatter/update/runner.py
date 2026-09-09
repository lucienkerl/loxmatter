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

        The command sequence below is guarded. `state.json` is the only
        channel back to the operator - the sidecar writes it, the web
        interface reads it - so an exception that escaped this function
        would leave the file saying `phase: "rollback"` forever, with the
        interface stuck showing "rolling back" and the operator learning
        nothing. A rollback that does not complete has to publish that
        fact, not vanish.
        """
        commands.log(f"rolling back to {previous_tag} ({previous_ref}): {reason}")
        publish("rollback", error=reason)
        try:
            commands.write_image_tag(previous_tag)
            commands.checkout(previous_ref)
            commands.recreate()
        except Exception as rollback_failure:  # noqa: BLE001 - must publish, not propagate
            # By the time `recreate` throws, `write_image_tag` and
            # `checkout` have already run without raising: the files on
            # disk (image tag, git ref) are back on the old version, but
            # the running container is not - `rolled_back=True` would
            # claim the installation is back on its previous version when
            # it is not, and this field is what the interface renders.
            commands.log(
                "rollback did not complete: image tag and git ref were restored to "
                f"{previous_tag} ({previous_ref}), but the container was not recreated - "
                f"a person has to finish this by hand ({rollback_failure!r})"
            )
            return publish(
                "failed",
                error=f"{reason}; rollback also failed: {rollback_failure}",
                rolled_back=False,
                healthy=False,
            )
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
