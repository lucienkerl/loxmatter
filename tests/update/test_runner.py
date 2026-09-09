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
from loxmatter.update.runner import STEPS, run_update


class FakeCommands:
    """Records what was asked of it and answers as told."""

    def __init__(
        self,
        *,
        healthy_after: int = 0,
        fail_on: str | None = None,
        fail_times: int = 1,
        healthy_raises_on: frozenset[int] = frozenset(),
        log_raises: bool = False,
        base: Path | None = None,
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
        # The 1-based call indices at which `healthy()` raises instead of
        # answering - see the health-poll escape tests, which each
        # need the raise to land at a different point in the poll.
        self._healthy_raises_on = healthy_raises_on
        self._log_raises = log_raises
        # `base` lets `recreate` read the state file mid-flight - see the
        # mid-run state test, which needs to observe a write that
        # happened *during* the run rather than trusting a post-hoc read.
        self._base = base
        self.phase_at_recreate: str | None = None

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
        if self._base is not None and self.phase_at_recreate is None:
            state = read_state(self._base)
            self.phase_at_recreate = state.phase if state is not None else None
        self._record("recreate")

    def healthy(self) -> bool:
        self._health_asked += 1
        if self._health_asked in self._healthy_raises_on:
            raise ConnectionError("bridge did not answer /health")
        return self._health_asked > self._healthy_after

    @property
    def health_asked(self) -> int:
        """Total `healthy()` calls across both the main wait and the
        rollback's own confirmation poll - see the deadline test,
        which pins how many of those the deadline comparison allows."""
        return self._health_asked

    def write_image_tag(self, tag: str) -> None:
        # Through `_record`, not a bare append: otherwise
        # `fail_on` can never target this call, and the "nothing was
        # restored" branch of `roll_back`'s failure message - the one
        # that fires when even the tag write fails - is unreachable from
        # any test.
        self._record(f"tag:{tag}")
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
        if self._log_raises:
            raise OSError(28, "No space left on device")
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
        # `checkout` gets the git-form target; `pull` and the tag write
        # get the Docker form CI actually publishes for a stable release
        # - the `v` stripped. See `rules.image_tag`.
        "pull:ghcr.io/lucienkerl/loxmatter:0.3.0",
        "tag:0.3.0",
        "recreate",
    ]


def test_the_state_file_is_written_during_pull_not_only_at_the_end(tmp_path):
    """The interface reads this file while the bridge is restarting. A
    state written only at the end would leave it blind for the minute
    that matters most - so this has to check a write happened *during*
    the run, not merely that one exists once `run_update` returns (which
    a state written only on the final `publish` call would also satisfy).
    `recreate` reads the file mid-flight to prove it.

    Finding (p): this only pins the write at the `pull` phase - the one
    `recreate`'s read observes. The `backup` and `rollback` publishes are
    not separately checked here; the name says so rather than the
    original "at every phase", which overclaimed."""
    commands = FakeCommands(base=tmp_path)
    run(commands, tmp_path)
    assert commands.phase_at_recreate == "pull"
    written = read_state(tmp_path)
    assert written is not None
    assert written.steps == ["backup", "pull", "recreate", "health"]


def test_a_successful_run_records_exactly_the_steps_constant_in_order(tmp_path):
    """`STEPS` is what the web interface tile will read; pin
    it against what `run_update` actually records so the constant and the
    literals `done.append(...)` uses cannot silently drift apart."""
    commands = FakeCommands()
    state = run(commands, tmp_path)
    assert tuple(state.steps) == STEPS


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
    # Finding (n): the `recreate` branch of the rollback-failure message
    # (runner.py:181-185) was asserted by no test - only the error and
    # the call sequence were. `checkout` succeeded here before `recreate`
    # raised, so the log has to say the tag AND the ref came back, only
    # the container did not.
    log_text = "\n".join(commands.lines)
    assert (
        "image tag and git ref were restored to stable (v0.2.0), but the "
        "container was not recreated" in log_text
    )


def test_a_raising_health_poll_triggers_a_rollback_instead_of_escaping(tmp_path):
    """Finding (a): a real `healthy()` polls `/health` over HTTP, and when
    the bridge is down - the exact condition this poll exists to detect -
    that call raises `ConnectionError` instead of returning `False`.
    Before the fix, that exception left `run_update` from the unwrapped
    call at the bottom of the function: no rollback ran at all, and the
    state file was left frozen at `phase: "recreate"`.

    Both polls of the main wait raise here; the confirmation poll inside
    the rollback that follows does not, so this also proves the rollback
    the raise triggers goes on to complete normally.
    """
    commands = FakeCommands(healthy_raises_on=frozenset({1, 2}), healthy_after=2)
    state = run(commands, tmp_path, deadline_seconds=3.0)

    assert state.phase == "failed"
    assert state.rolled_back is True
    assert state.healthy is True
    assert commands.calls.count("recreate") == 2
    assert commands.calls[-3:] == ["tag:stable", "checkout:v0.2.0", "recreate"]


def test_a_raising_rollback_confirmation_poll_yields_healthy_false(tmp_path):
    """Finding (b): the rollback's own health poll - its confirmation that
    the restore worked - sat outside `roll_back`'s `try`, which closes
    before it runs. A raise there used to escape `roll_back` entirely,
    leaving the state frozen at `phase: "rollback"` forever - precisely
    the "interface shows rolling back for all time" failure the previous
    fix was written to eliminate, reached through a different door.

    The main poll fails the ordinary way (never raises, just never turns
    healthy) so a normal rollback starts; the rollback's own confirmation
    poll then raises throughout. The restore commands (`write_image_tag`,
    `checkout`, `recreate`) all complete - only the confirmation fails -
    so the right result is `rolled_back=True` alongside `healthy=False`,
    not an escaped exception.
    """
    commands = FakeCommands(healthy_after=10_000, healthy_raises_on=frozenset({3, 4}))
    state = run(commands, tmp_path, deadline_seconds=3.0)

    assert state.phase == "failed"
    assert state.rolled_back is True
    assert state.healthy is False
    assert commands.calls.count("recreate") == 2
    assert commands.calls[-3:] == ["tag:stable", "checkout:v0.2.0", "recreate"]


def test_a_log_failure_on_a_rejected_request_does_not_escape(tmp_path):
    """Finding (c): `commands.log` appends to a file in the same shared
    directory that a `docker pull` can fill up, and the reject path calls
    it before `publish`. A raise there - verified here for the log case,
    where the state file used to never get written at all - must not stop
    `run_update` from returning a correct state: logging is best-effort.
    """
    commands = FakeCommands(log_raises=True)
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
    # Every line raised and was swallowed - nothing reached the fake log.
    assert commands.lines == []


def test_a_write_state_failure_still_returns_a_correct_state(tmp_path, monkeypatch):
    """Finding (c): `write_state` (called from `publish`) can fail on the
    same full disk that makes `commands.log` fail - `OSError(28)` is an
    ordinary update failure. `run_update` must still return the state
    object it built even when it could not be written to the file the
    interface actually reads.
    """
    import loxmatter.update.runner as runner_module

    def raising_write_state(base: Path, state: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(runner_module, "write_state", raising_write_state)
    commands = FakeCommands()
    state = run(commands, tmp_path)

    assert state.phase == "done"
    assert state.healthy is True
    # Nothing reached disk - every `publish` call's write failed the
    # same way, and none of them was allowed to stop the run.
    assert not (tmp_path / "state.json").exists()


def test_a_rollback_where_checkout_fails_does_not_claim_the_ref_was_restored(tmp_path):
    """Finding (f): the log message used to unconditionally claim the
    image tag AND git ref were restored, on the assumption that `recreate`
    is always the thrower. When `checkout` throws instead, the git ref
    was never restored - claiming otherwise would send whoever reads the
    log to finish by hand with `up -d`, which for someone who believes the
    ref is already back deploys the checked-out (new) revision's
    docker-compose.yml, not the old one.
    """
    commands = FakeCommands(healthy_after=10_000, fail_on="checkout:v0.2.0")
    state = run(commands, tmp_path, deadline_seconds=3.0)

    assert state.phase == "failed"
    assert state.rolled_back is False
    log_text = "\n".join(commands.lines)
    assert "the image tag was restored to stable" in log_text
    assert "image tag and git ref were restored" not in log_text


def test_a_dev_update_writes_a_sha_prefixed_docker_tag_not_the_bare_commit(tmp_path):
    """Finding 1, exercised through the whole run rather than just
    `rules.image_tag` in isolation: `checkout` gets the raw target (the
    git side is correct as it stands), but `pull` and the `.env` write
    must get the `sha-`-prefixed tag CI actually publishes for a dev
    build - never the bare commit."""
    commands = FakeCommands()
    state = run_update(
        UpdateRequest(
            id="01J000000000000000000001",
            channel="dev",
            target="deadbee",
            requested_at="2026-09-09T10:00:00+00:00",
        ),
        commands,
        base=tmp_path,
        running_version="0.2.0",
        running_commit="abc1234",
        is_ancestor=lambda _a, _b: True,
        sleep=lambda _seconds: None,
    )
    assert state.phase == "done"
    assert commands.calls == [
        "backup",
        "fetch",
        "checkout:deadbee",
        "pull:ghcr.io/lucienkerl/loxmatter:sha-deadbee",
        "tag:sha-deadbee",
        "recreate",
    ]


def test_a_raising_is_ancestor_check_does_not_escape_run_update(tmp_path):
    """Finding 2, first escape: `is_ancestor` shells out to git (there is
    no `commands.py` yet to catch its failures for us) and is called from
    inside `check_request` on the `dev` channel. Before the fix, the
    `try` around `check_request` caught only `RequestRejected`, so an
    `OSError` from a missing git binary went straight out of `run_update`
    - `state.json` was never written and nothing was executed, yet the
    operator's only channel showed nothing at all.
    """

    def raising_is_ancestor(_running: str, _target: str) -> bool:
        raise OSError("git executable not found")

    commands = FakeCommands()
    state = run_update(
        UpdateRequest(id="x", channel="dev", target="deadbee", requested_at="t"),
        commands,
        base=tmp_path,
        running_version="0.2.0",
        running_commit="abc1234",
        is_ancestor=raising_is_ancestor,
        sleep=lambda _seconds: None,
    )
    assert state.phase == "failed"
    assert state.error is not None
    assert commands.calls == []
    written = read_state(tmp_path)
    assert written is not None
    assert written.phase == "failed"


def test_a_raising_read_image_tag_does_not_escape_run_update(tmp_path):
    """Finding 2, second escape: `commands.read_image_tag()` reads
    `.env`, which will not exist before the sidecar's first successful
    run. Before the fix, a `FileNotFoundError` there escaped `run_update`
    outright - it runs before `commands.backup()`, so nothing had been
    executed and the right outcome is a published `failed` state, exactly
    like a rejected request."""

    class RaisingReadTag(FakeCommands):
        def read_image_tag(self) -> str:
            raise FileNotFoundError(".env has no LOXMATTER_IMAGE_TAG yet")

    commands = RaisingReadTag()
    state = run(commands, tmp_path)
    assert state.phase == "failed"
    assert state.error is not None
    assert commands.calls == []


def test_a_raising_read_git_ref_does_not_escape_run_update(tmp_path):
    """Finding 2, third escape: `commands.read_git_ref()` reads
    `docker-compose.yml`'s checked-out revision, which can equally be
    missing or unreadable. Same shape as the two escapes above: it runs
    before the first command, so a raise here must become a published
    `failed` state instead of leaving `run_update`."""

    class RaisingReadRef(FakeCommands):
        def read_git_ref(self) -> str:
            raise OSError("docker-compose.yml is missing")

    commands = RaisingReadRef()
    state = run(commands, tmp_path)
    assert state.phase == "failed"
    assert state.error is not None
    assert commands.calls == []


def test_a_failing_rollback_tag_write_reports_nothing_restored(tmp_path):
    """Finding (m): `FakeCommands.write_image_tag` used to append to
    `calls` without going through `_record`, so `fail_on` could never
    target it - the "nothing was restored - not even the image tag"
    branch of `roll_back`'s failure message (runner.py:191-192) was
    unreachable from any test. The main run's own `write_image_tag` call
    (tag "0.3.0") must still succeed; only the rollback's later call,
    writing back the previous tag ("stable"), fails - the two are
    distinguishable because the fake records the tag value in the call
    name.
    """
    commands = FakeCommands(healthy_after=10_000, fail_on="tag:stable")
    state = run(commands, tmp_path, deadline_seconds=3.0)

    assert state.phase == "failed"
    assert state.rolled_back is False
    log_text = "\n".join(commands.lines)
    assert "nothing was restored - not even the image tag" in log_text


def test_the_poll_stops_after_the_deadline_is_reached_not_before(tmp_path):
    """Finding (o): `runner.py:272` uses `waited < deadline_seconds`, not
    `<=`. Every other test drives the wait with `deadline_seconds=3.0` or
    the default, and with `POLL_SECONDS = 2.0` both operators poll twice
    either way (`0.0 < 3.0` and `2.0 < 3.0` are both true regardless of
    `=`) - none of them pins the boundary. `4.0` is where they diverge:
    `<` stops the main wait after 2 polls (`waited` reaches exactly
    `4.0`, and `4.0 < 4.0` is false), while `<=` would poll a third time.
    The rollback's own confirmation wait runs the same comparison again,
    so a raise-free run through both waits gives 2 + 2 = 4 total calls to
    `healthy()` under `<`, and 3 + 3 = 6 under `<=`.
    """
    commands = FakeCommands(healthy_after=10_000)
    run(commands, tmp_path, deadline_seconds=4.0)
    assert commands.health_asked == 4


def test_a_log_failure_during_a_successful_run_does_not_escape(tmp_path):
    """Finding (q): the existing bite check for `_log`
    (`test_a_log_failure_on_a_rejected_request_does_not_escape`) only
    exercises the reject-path call before the first command. The six
    mid-function call sites (`runner.py:163, 193, 208, 219, 233, 246`)
    would all still pass with `_log` reverted to a bare `commands.log`,
    since nothing drove a log failure once the run had actually started.
    This drives a whole healthy update with a logger that always raises
    and checks the run still completes - covering the `backup`,
    `checkout` and `done` call sites (208, 219, 246)."""
    commands = FakeCommands(log_raises=True)
    state = run(commands, tmp_path)
    assert state.phase == "done"
    assert state.healthy is True
    assert commands.lines == []


def test_a_log_failure_during_a_failing_step_and_rollback_does_not_escape(tmp_path):
    """Finding (q), continued: the rollback-only call sites (163, before
    the restore commands run; 193, if the restore itself also fails) are
    reachable only when a step fails and the rollback then fails too -
    exercised here with the same `fail_on="recreate", fail_times=2`
    shape as `test_a_failing_rollback_is_reported_not_raised`, but with
    every log call raising."""
    commands = FakeCommands(fail_on="recreate", fail_times=2, log_raises=True)
    state = run(commands, tmp_path)
    assert state.phase == "failed"
    assert state.rolled_back is False
    assert commands.lines == []
