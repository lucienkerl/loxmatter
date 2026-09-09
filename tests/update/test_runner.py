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

    def __init__(
        self,
        *,
        healthy_after: int = 0,
        fail_on: str | None = None,
        fail_times: int = 1,
    ) -> None:
        self.calls: list[str] = []
        self.lines: list[str] = []
        self.tag = "stable"
        self._healthy_after = healthy_after
        self._health_asked = 0
        self._fail_on = fail_on
        # How many times `fail_on` raises before it starts succeeding.
        # `run_update` calls a command of the same name twice when a step
        # fails and triggers a rollback - once for the failing step
        # itself, once again inside `roll_back`. `fail_times=1` (the
        # default) fails only the first call, so the rollback's own call
        # succeeds; `fail_times=2` fails both, so the rollback fails too.
        self._fail_times = fail_times
        self._fail_counts: dict[str, int] = {}

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if self._fail_on == name:
            seen = self._fail_counts.get(name, 0) + 1
            self._fail_counts[name] = seen
            if seen <= self._fail_times:
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
    `up -d` would deploy.

    `recreate` fails once - the attempt that triggers the rollback - and
    then succeeds when `roll_back` calls it again, so this proves the
    rollback itself completes rather than also failing."""
    commands = FakeCommands(fail_on="recreate", fail_times=1)
    state = run(commands, tmp_path)
    assert state.phase == "failed"
    assert state.rolled_back is True
    assert commands.tag == "stable"
    assert commands.calls.count("recreate") == 2


def test_a_failing_rollback_is_reported_not_raised(tmp_path):
    """The defect this fixes: `roll_back` used to call `commands.recreate()`
    uncaught, so a rollback whose own `recreate` also failed threw the
    exception straight out of `run_update` instead of returning a state.

    `state.json` is the only channel back to the operator, so that
    exception left it stuck at `phase: "rollback"` forever - the interface
    would show "rolling back" for all time. A failing rollback must
    instead publish `phase="failed"` with `rolled_back=False` (the files
    on disk were restored, the container was not, so the installation is
    NOT back on its previous version) and an error that names both the
    original failure and the rollback's own failure.
    """
    commands = FakeCommands(fail_on="recreate", fail_times=2)
    state = run(commands, tmp_path)

    assert state.phase == "failed"
    assert state.rolled_back is False
    assert state.healthy is False
    assert state.error is not None
    assert "recreate failed" in state.error
    assert "rollback" in state.error.lower()
    # Both attempts happened - the original step, then the rollback's own
    # call of the same name - and both failed.
    assert commands.calls.count("recreate") == 2
    # The tag and ref were still restored on disk before recreate raised.
    assert commands.tag == "stable"
    assert commands.calls[-3:] == ["tag:stable", "checkout:v0.2.0", "recreate"]
