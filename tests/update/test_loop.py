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

"""One pass of the sidecar's own loop - see `__main__.run_once`.

`main()` itself is `while True: run_once(...); time.sleep(...)` and stays
untested for what makes every iteration alike - that behaviour is all in
`run_once`, which takes no daemon and no wall-clock sleep, and is covered
above. What is NOT alike between iterations - `main`'s own pre-loop setup
(`_repair_state`, `_seed_last_id`) and the in-memory carry across
iterations that only `main` performs - has no return value of its own to
assert on, but is testable anyway: inject a fake `DockerCommands` and a
`time.sleep` that raises after a fixed number of calls to end the
otherwise-infinite loop on command. See
`test_main_seeds_and_carries_last_id_across_a_torn_read` below.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loxmatter.timestamps import now_iso
from loxmatter.update import __main__ as main_module
from loxmatter.update import protocol
from loxmatter.update.__main__ import _seed_last_id, run_once
from loxmatter.update.protocol import (
    UpdateRequest,
    UpdateState,
    read_state,
    state_path,
    write_request,
    write_state,
)


class FakeCommands:
    """A `Commands` (plus `is_ancestor` and `read_running_identity`) that
    always succeeds and is healthy on the first poll - the loop-skipping
    behaviour is what these tests are about, not the state machine
    `run_update` already owns (see `tests/update/test_runner.py`).

    `running_version`/`running_commit` are plain, mutable attributes
    (default: what the checkout used to report before the Critical fix,
    so the existing tests below did not have to change their target
    versions) rather than constructor arguments, so a test that cares can
    set them without touching every other call site.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.tag = "stable"
        self.running_version = "0.2.0"
        self.running_commit = "abc1234"

    def backup(self) -> str:
        self.calls.append("backup")
        return "/data/backups/store-x.tgz"

    def fetch(self) -> None:
        self.calls.append("fetch")

    def checkout(self, ref: str) -> None:
        self.calls.append(f"checkout:{ref}")

    def pull(self, image: str) -> None:
        self.calls.append(f"pull:{image}")

    def recreate(self) -> None:
        self.calls.append("recreate")

    def healthy(self) -> bool:
        return True

    def write_image_tag(self, tag: str) -> None:
        self.calls.append(f"tag:{tag}")
        self.tag = tag

    def read_image_tag(self) -> str:
        return self.tag

    def read_git_ref(self) -> str:
        return "v0.2.0"

    def log(self, line: str) -> None:
        self.calls.append(f"log:{line}")

    def is_ancestor(self, older: str, newer: str) -> bool:
        return True

    def read_running_identity(self) -> tuple[str, str, int]:
        self.calls.append("read_running_identity")
        return self.running_version, self.running_commit, 7


def pass_once(base: Path, commands: FakeCommands, *, last_id: str | None = None) -> str | None:
    return run_once(
        base,
        commands,
        last_id=last_id,
        is_ancestor=commands.is_ancestor,
        sleep=lambda _seconds: None,
    )


def test_the_same_request_id_is_executed_only_once(tmp_path):
    """The bridge writes the file once, but the loop reads it every two
    seconds until someone replaces it. Without the id check, a single
    press would update forever.

    The second pass also has to leave the published `done` state alone,
    not reset it to `idle` - see `_touch_seen_at`'s own docstring. Losing
    that would not break this test's first half at all, which is exactly
    why the assertion is here rather than only in a test named after
    `_touch_seen_at`."""
    base = tmp_path / "update"
    write_request(
        base,
        UpdateRequest(id="req-1", channel="stable", target="v0.3.0", requested_at=now_iso()),
    )
    commands = FakeCommands()

    last_id = pass_once(base, commands)
    assert last_id == "req-1"
    first_pass_calls = list(commands.calls)
    assert "recreate" in first_pass_calls
    state = read_state(base)
    assert state is not None
    assert state.id == "req-1"
    assert state.phase == "done"

    # request.json still names the same id, and the caller (as `main`
    # would) remembers it - a second pass must not run the update again.
    last_id = pass_once(base, commands, last_id=last_id)
    assert last_id == "req-1"
    assert commands.calls == first_pass_calls
    state_after_quiet_pass = read_state(base)
    assert state_after_quiet_pass is not None
    assert state_after_quiet_pass.id == "req-1"
    assert state_after_quiet_pass.phase == "done"


def test_a_restarted_process_seeds_last_id_from_state_json_and_executes_nothing(tmp_path):
    """`run_once` only ever compares `request.id` against whatever
    `last_id` it is handed - it never reads `state.json`'s own `id`
    itself. Holding `last_id` in memory is exactly what makes that cheap,
    but it also means a process that starts fresh - a host reboot, a
    `docker compose up -d`, a crash under `restart: unless-stopped` - has
    no memory of anything, and used to see `last_id=None` on its very
    first pass regardless of what `state.json` already recorded as done.

    `main` closes that gap with `_seed_last_id`, called once before its
    `while True` begins (see the module docstring). This test drives
    `_seed_last_id` directly, in isolation from the rest of `main` - the
    two together, wired exactly as `main` wires them, are what
    `test_main_seeds_and_carries_last_id_across_a_torn_read` below
    covers. The first pass's own return value is thrown away on purpose,
    standing in for the fact that a real restart has genuinely lost it;
    `_seed_last_id` has to recover the id from `state.json` on its own,
    not be handed it as a shortcut.
    """
    base = tmp_path / "update"
    write_request(
        base,
        UpdateRequest(id="req-1", channel="dev", target="deadbeef123", requested_at=now_iso()),
    )
    commands = FakeCommands()

    first_process_last_id = pass_once(base, commands)
    assert first_process_last_id == "req-1"
    calls_after_first_process = list(commands.calls)
    assert "recreate" in calls_after_first_process
    del first_process_last_id  # discarded - a restart does not carry this forward

    # A fresh process: no memory of its own, seeded from `state.json`
    # exactly as `main` seeds it before entering the loop.
    restarted_last_id = _seed_last_id(base)
    assert restarted_last_id == "req-1"

    pass_once(base, commands, last_id=restarted_last_id)

    # Nothing new was invoked - the "restart" did not re-run the update.
    assert commands.calls == calls_after_first_process
    state = read_state(base)
    assert state is not None
    assert state.phase == "done"


def test_a_pass_with_no_request_still_stamps_updater_seen_at(tmp_path):
    """This stamp is how the bridge tells "nothing is running" from "no
    updater is installed" (design section 11). It has to be written on
    the quiet passes, which are almost all of them."""
    base = tmp_path / "update"
    commands = FakeCommands()

    pass_once(base, commands)

    state = read_state(base)
    assert state is not None
    assert state.updater_seen_at
    assert state.phase == "idle"
    # A quiet pass touches only the timestamp - it must not invoke a
    # single docker/git/curl call, and must not even ask what is running:
    # nothing needs checking against a request that does not exist.
    assert commands.calls == []


def test_touch_seen_at_moves_a_corrupt_state_file_aside_and_resumes_stamping(tmp_path):
    """A `state.json` that cannot be parsed - a hand edit, a torn write,
    binary garbage from a failing SD card - must not be silently
    overwritten (the evidence would be gone) and must not be left in
    place forever either (see the next test): that used to mean
    `updater_seen_at` stopped moving for good, which design section 11
    reads as "no sidecar installed" and hides the update button - over
    one unparsable byte, while the sidecar keeps polling perfectly.

    The fix is to move the file out of the way once, under a timestamped
    `.corrupt.<now_iso()>` name - not the single fixed name this used to
    be, which a second corruption would silently overwrite, destroying
    the first one's evidence - and resume stamping from a fresh `idle`
    state - preserving the evidence instead of discarding it, while
    letting the loop recover."""
    base = tmp_path / "update"
    path = state_path(base)
    path.parent.mkdir(parents=True)
    corrupt = b"\xff\xfe not valid JSON, and not valid UTF-8 either"
    path.write_bytes(corrupt)
    commands = FakeCommands()

    pass_once(base, commands)

    # The corrupt bytes survive, moved aside rather than deleted, under a
    # name that carries a timestamp rather than the fixed
    # `state.json.corrupt` this used to be.
    corrupt_candidates = list(path.parent.glob("state.json.corrupt.*"))
    assert len(corrupt_candidates) == 1
    assert corrupt_candidates[0].read_bytes() == corrupt

    # `state.json` itself is a fresh, readable, idle state - stamping has
    # resumed.
    state = read_state(base)
    assert state is not None
    assert state.phase == "idle"
    assert state.id is None
    assert state.updater_seen_at

    # This was still a quiet pass - no request was present, so no
    # docker/git/curl call happened. Only the notice was logged.
    non_log_calls = [call for call in commands.calls if not call.startswith("log:")]
    assert non_log_calls == []


def test_touch_seen_at_logs_the_corrupt_state_notice_once_not_every_pass(tmp_path):
    """The loop wakes every two seconds, and `log.txt` lives on an SD
    card - a notice repeated on every pass would drown the log and wear
    the card for a condition that, after the first pass, no longer
    exists (the file has already been moved aside and replaced)."""
    base = tmp_path / "update"
    path = state_path(base)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe not valid JSON, and not valid UTF-8 either")
    commands = FakeCommands()

    pass_once(base, commands)
    log_lines_after_first_pass = [call for call in commands.calls if call.startswith("log:")]
    assert len(log_lines_after_first_pass) == 1

    pass_once(base, commands)
    pass_once(base, commands)
    log_lines_after_three_passes = [call for call in commands.calls if call.startswith("log:")]
    # Same one line as after the first pass - the second and third passes
    # found an already-repaired, readable `state.json` and never took the
    # corrupt-file branch again.
    assert log_lines_after_three_passes == log_lines_after_first_pass


def test_a_downgrade_is_refused_against_the_running_container_not_the_checkout(tmp_path):
    """The Critical fix, exercised through the loop: identity comes from
    `commands.read_running_identity()` - standing in for `docker inspect`
    on the running `loxmatter` container - not from a checkout on disk
    that a moving `:stable` tag can leave behind.

    The target (0.2.5) is deliberately chosen to sit BETWEEN the
    checkout's stale number (0.2.0, still hard-coded above as
    `read_git_ref`'s answer - the wrong source this fix replaces) and
    what the container actually runs (0.3.0): checkout-derived identity
    would see 0.2.5 as forward progress and let it through, while it is
    really a downgrade from what is live. This is the exact silent-
    downgrade scenario the Critical finding describes, not merely a
    request that happens to be rejected either way - see the bite-check
    evidence in the task report, which shows this test passing even
    though it should not once the fix is reverted to the checkout's
    number, for a target that also equals that stale number."""
    base = tmp_path / "update"
    write_request(
        base,
        UpdateRequest(id="req-1", channel="stable", target="v0.2.5", requested_at=now_iso()),
    )
    commands = FakeCommands()
    # The running container has already advanced past the checkout's own
    # number (0.2.0) - exactly the "`:stable` moved, the checkout did
    # not" scenario the design calls out.
    commands.running_version = "0.3.0"
    commands.running_commit = "def5678"

    pass_once(base, commands)

    state = read_state(base)
    assert state is not None
    assert state.phase == "failed"
    assert state.error is not None
    assert "not newer" in state.error
    # Rejected before the first command (`run_update` logs the reason,
    # which is the one call besides the identity lookup itself) - see
    # `run_update`'s own docstring on `RequestRejected`.
    executed = [
        call
        for call in commands.calls
        if call not in ("read_running_identity",) and not call.startswith("log:")
    ]
    assert executed == []


class _StopLoop(Exception):
    """Raised by a faked `time.sleep` to end `main`'s otherwise-infinite
    `while True` on command, at a call count the test chooses - see the
    tests below. Not an assertion failure in itself; a test that lets it
    escape `pytest.raises` is one where `main` looped more or fewer times
    than expected."""


def _run_main_until_stopped(
    base: Path, commands: FakeCommands, monkeypatch: pytest.MonkeyPatch, *, passes: int
) -> None:
    """Drive the real `main()` - not `run_once` - for exactly `passes`
    iterations of its `while True`, then stop it with `_StopLoop` instead
    of letting it run forever. `DockerCommands` is replaced so `main`'s
    own construction of it (`repo_dir=/checkout`, `stack_dir=/checkout/
    deploy/testhost` by default, no `tmp_path` involved) never touches the
    filesystem; `time.sleep` is replaced so the test does not wait 2 real
    seconds per pass and so it has a way to end the loop at all.
    """
    monkeypatch.setattr(main_module, "DockerCommands", lambda **_kwargs: commands)
    monkeypatch.setenv("LOXMATTER_UPDATE_BASE", str(base))
    sleep_calls = 0

    def fake_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= passes:
            raise _StopLoop

    monkeypatch.setattr(main_module.time, "sleep", fake_sleep)
    with pytest.raises(_StopLoop):
        main_module.main()


def test_default_base_is_the_shared_update_dir_protocol_names(monkeypatch: pytest.MonkeyPatch):
    """The plan's Task 2 interface list gives `UPDATE_DIR` to `protocol`,
    not to `__main__` (`docs/superpowers/plans/2026-09-09-webui-updates-
    stage-2.md:325`: "Tasks 3, 4 and 7 all use these"). A previous round
    kept `__main__.DEFAULT_BASE` as its own, separate `Path("/data/
    update")` instead - the plan's resolution is the other way around:
    `protocol` keeps the one definition, and `__main__` aliases it."""
    assert main_module.DEFAULT_BASE is protocol.UPDATE_DIR


def test_main_points_env_path_at_the_stack_dir_not_the_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The defect this pins: `DockerCommands` used to take one
    `compose_dir` for both `git` (which needs the repository root) and
    `docker compose`/`.env` (which need `deploy/testhost`, two
    directories below it) - and `env_path` defaulted to `compose_dir /
    ".env"`. With the real default (`/checkout`) that pointed
    `write_image_tag`/`read_image_tag` at `/checkout/.env`, a file that
    has never existed; the real one is `deploy/testhost/.env` -
    `.env.example` ships there, not at the repository root. `main` now
    builds `repo_dir` and `stack_dir` separately and must default
    `env_path` under the stack directory, the same directory `docker
    compose` itself runs in.

    No `tmp_path` is passed into `DockerCommands` here on purpose - this
    test is about what `main` computes with NO override in the
    environment at all, which is exactly the gap the real deployment
    would have hit silently.
    """
    captured: dict[str, object] = {}

    def fake_docker_commands(**kwargs: object) -> FakeCommands:
        captured.update(kwargs)
        return FakeCommands()

    monkeypatch.setattr(main_module, "DockerCommands", fake_docker_commands)
    monkeypatch.setenv("LOXMATTER_UPDATE_BASE", str(tmp_path))
    monkeypatch.delenv("LOXMATTER_REPO_DIR", raising=False)
    monkeypatch.delenv("LOXMATTER_STACK_DIR", raising=False)
    monkeypatch.delenv("LOXMATTER_ENV_PATH", raising=False)
    sleep_calls = 0

    def fake_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        raise _StopLoop

    monkeypatch.setattr(main_module.time, "sleep", fake_sleep)
    with pytest.raises(_StopLoop):
        main_module.main()

    assert captured["repo_dir"] == main_module.DEFAULT_REPO_DIR
    assert captured["stack_dir"] == main_module.DEFAULT_STACK_DIR
    # The point of the fix: two different directories, and `.env` under
    # the second one, not the first.
    assert captured["stack_dir"] != captured["repo_dir"]
    assert captured["env_path"] == main_module.DEFAULT_STACK_DIR / ".env"
    assert captured["env_path"] != main_module.DEFAULT_REPO_DIR / ".env"


def test_main_honours_separate_repo_and_stack_dir_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LOXMATTER_REPO_DIR` and `LOXMATTER_STACK_DIR` are two independent
    overrides (the same `_path_from_env` pattern the old, single
    `LOXMATTER_COMPOSE_DIR` used) - setting one must not change what the
    other resolves to, and `env_path`'s default must follow whichever
    `stack_dir` was actually given, not the module's own default
    constant."""
    captured: dict[str, object] = {}

    def fake_docker_commands(**kwargs: object) -> FakeCommands:
        captured.update(kwargs)
        return FakeCommands()

    repo_dir = tmp_path / "checkout"
    stack_dir = tmp_path / "elsewhere" / "stack"
    monkeypatch.setattr(main_module, "DockerCommands", fake_docker_commands)
    monkeypatch.setenv("LOXMATTER_UPDATE_BASE", str(tmp_path))
    monkeypatch.setenv("LOXMATTER_REPO_DIR", str(repo_dir))
    monkeypatch.setenv("LOXMATTER_STACK_DIR", str(stack_dir))
    monkeypatch.delenv("LOXMATTER_ENV_PATH", raising=False)

    def fake_sleep(_seconds: float) -> None:
        raise _StopLoop

    monkeypatch.setattr(main_module.time, "sleep", fake_sleep)
    with pytest.raises(_StopLoop):
        main_module.main()

    assert captured["repo_dir"] == repo_dir
    assert captured["stack_dir"] == stack_dir
    assert captured["env_path"] == stack_dir / ".env"


def test_main_leaves_a_healthy_state_file_alone_at_startup(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """`_repair_state` is now called unconditionally from `main`, before
    the loop even starts (see the finding this is a companion to below).
    A version of that call which decided "needs repair" from `path.
    exists()` rather than from `read_state(base) is None` would move
    ANY existing `state.json` aside on every single process start - even
    one that parses perfectly - clobbering a `done`/`failed` result into
    a fresh `idle` one before the interface ever gets to read it. The
    id salvage that `_repair_state` performs happens to recover `id` from
    valid JSON just as well as from a hand-edited one, which is exactly
    what let this slip past every other test here: `_seed_last_id` still
    saw the right id, so nothing *looked* wrong unless something checked
    `phase` too. This test does."""
    base = tmp_path / "update"
    write_request(
        base,
        UpdateRequest(id="req-1", channel="stable", target="v0.3.0", requested_at=now_iso()),
    )
    write_state(
        base,
        UpdateState(
            id="req-1",
            phase="done",
            steps=["backup", "fetch", "checkout", "pull", "recreate", "health"],
            from_version="v0.2.0",
            to_version="v0.3.0",
            error=None,
            rolled_back=False,
            healthy=True,
            updater_seen_at=now_iso(),
        ),
    )
    commands = FakeCommands()

    _run_main_until_stopped(base, commands, monkeypatch, passes=1)

    assert list(state_path(base).parent.glob("state.json.corrupt.*")) == []
    state = read_state(base)
    assert state is not None
    assert state.phase == "done"
    assert state.steps == ["backup", "fetch", "checkout", "pull", "recreate", "health"]
    non_log_calls = [call for call in commands.calls if not call.startswith("log:")]
    assert non_log_calls == []


def test_main_repairs_a_corrupt_state_file_even_with_a_request_already_pending(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """`run_once` returns before `_touch_seen_at` is ever reached
    whenever a request is pending and not yet recorded as this
    process's `last_id` (see `run_once`'s own early return) - so in the
    one process where `state.json` is actually corrupt AND a request is
    waiting, the corrupt-file repair used to never run at all.
    `run_update`'s own `publish` then overwrote the corrupt bytes
    outright: no `state.json.corrupt*` file, and no line in `log.txt`
    saying corruption ever happened.

    The fix is `main` calling `_repair_state` once, unconditionally,
    before the loop starts - before `run_once`'s first call can reach
    `run_update` at all. This test reproduces the reviewer's probe:
    corrupt `state.json`, a full request waiting, one pass of the real
    `main()`. The corrupt evidence must survive and the notice must be
    in the log - regardless of whether the request itself goes on to
    execute (it does here, since the raw bytes are unreadable garbage
    with no salvageable `id` - re-executing once the id is genuinely
    gone is inherent, see `_seed_last_id`'s docstring; a *recoverable*
    id is `test_main_salvages_the_id_of_a_hand_edited_state_before_
    seeding` below, and does not re-execute)."""
    base = tmp_path / "update"
    path = state_path(base)
    path.parent.mkdir(parents=True)
    corrupt = b"\xff\xfe not valid JSON, and not valid UTF-8 either"
    path.write_bytes(corrupt)
    write_request(
        base,
        UpdateRequest(id="req-1", channel="stable", target="v0.3.0", requested_at=now_iso()),
    )
    commands = FakeCommands()

    _run_main_until_stopped(base, commands, monkeypatch, passes=1)

    corrupt_candidates = list(path.parent.glob("state.json.corrupt.*"))
    assert len(corrupt_candidates) == 1
    assert corrupt_candidates[0].read_bytes() == corrupt
    corruption_logged = [
        call for call in commands.calls if call.startswith("log:") and "could not be read" in call
    ]
    assert len(corruption_logged) == 1
    # The repair ran before the re-run, as `_repair_state`'s docstring
    # requires - not merely "eventually": the id truly could not be
    # salvaged from binary garbage, so the update runs once more.
    assert "recreate" in commands.calls


def test_main_salvages_the_id_of_a_hand_edited_state_before_seeding(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """The other half of the same finding: `read_state` returns `None`
    not only for binary garbage but for well-formed JSON with an unknown
    `phase` or a non-list `steps` - the hand-edit case the corrupt-file
    comment itself names. `id` sits in that file in plain text; losing it
    would mean a later `_seed_last_id` cannot recover it even though it
    was recoverable. Here it names the same request already pending in
    `request.json`, so the correctly-salvaged, correctly-seeded id must
    make `main` skip it - no docker/git/curl call at all, just the
    corruption notice."""
    base = tmp_path / "update"
    path = state_path(base)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "id": "req-1",
                "phase": "not-a-real-phase",
                "steps": [],
                "from_version": "v0.2.0",
                "to_version": "v0.3.0",
                "error": None,
                "rolled_back": False,
                "healthy": True,
                "updater_seen_at": now_iso(),
            }
        ),
        encoding="utf-8",
    )
    write_request(
        base,
        UpdateRequest(id="req-1", channel="stable", target="v0.3.0", requested_at=now_iso()),
    )
    commands = FakeCommands()

    _run_main_until_stopped(base, commands, monkeypatch, passes=1)

    state = read_state(base)
    assert state is not None
    assert state.id == "req-1"
    non_log_calls = [call for call in commands.calls if not call.startswith("log:")]
    assert non_log_calls == []


def test_main_seeds_and_carries_last_id_across_a_torn_read(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """The carrying decision of design section 7's file contract is that
    `state.json` is the *only* memory across a process restart, but
    `main` also holds `last_id` in memory across passes *within* one
    process, precisely so a torn read or a failed write between two
    passes cannot make an already-completed request look brand new
    again (see the module docstring). Neither half is protected by
    `run_once`'s own tests: replacing `main`'s `_seed_last_id(base)` with
    a bare `None`, or rewriting `run_once` to re-derive `last_id` from
    `read_state(base)` on every pass instead of trusting the value it was
    handed, both leave every test above passing - none of them drives
    `main` itself.

    This one does, covering seed, carry, and "no exception escapes
    `while True`" together: `state.json` already records `req-1` as
    `done`, matching `request.json` - a correctly seeded first pass must
    not re-run it. Between the first and second pass, `state.json` is
    overwritten with binary garbage - standing in for the torn read/
    failed write the module docstring warns about - so a `last_id` that
    were re-derived from disk on the second pass would see `None` there
    and re-run the request; a `last_id` correctly carried in memory does
    not."""
    base = tmp_path / "update"
    write_request(
        base,
        UpdateRequest(id="req-1", channel="stable", target="v0.3.0", requested_at=now_iso()),
    )
    write_state(
        base,
        UpdateState(
            id="req-1",
            phase="done",
            steps=["recreate"],
            from_version="v0.2.0",
            to_version="v0.3.0",
            error=None,
            rolled_back=False,
            healthy=True,
            updater_seen_at=now_iso(),
        ),
    )
    commands = FakeCommands()
    corrupted_mid_loop = False

    def corrupt_state_after_first_pass(_seconds: float) -> None:
        nonlocal corrupted_mid_loop
        if not corrupted_mid_loop:
            state_path(base).write_bytes(b"\xff\xfe not valid JSON, torn mid-write")
            corrupted_mid_loop = True
        else:
            raise _StopLoop

    monkeypatch.setattr(main_module, "DockerCommands", lambda **_kwargs: commands)
    monkeypatch.setenv("LOXMATTER_UPDATE_BASE", str(base))
    monkeypatch.setattr(main_module.time, "sleep", corrupt_state_after_first_pass)

    with pytest.raises(_StopLoop):
        main_module.main()

    # Neither pass invoked a single docker/git/curl call: the first
    # skipped because `_seed_last_id` correctly recovered "req-1" as
    # already done, the second skipped despite the corrupted file only
    # because `last_id` was carried in memory rather than re-read from a
    # `state.json` that, at that moment, could not say anything at all.
    non_log_calls = [call for call in commands.calls if not call.startswith("log:")]
    assert non_log_calls == []
