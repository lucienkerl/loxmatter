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

`main()` itself is `while True: run_once(...); time.sleep(...)` and is
deliberately not exercised here: an infinite loop has no return value to
assert on, and the interesting behaviour - what one pass does - is all in
`run_once`, which takes no daemon and no wall-clock sleep to test.
"""

from __future__ import annotations

from pathlib import Path

from loxmatter.timestamps import now_iso
from loxmatter.update.__main__ import run_once
from loxmatter.update.protocol import UpdateRequest, read_state, state_path, write_request


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

    def read_running_identity(self) -> tuple[str, str]:
        self.calls.append("read_running_identity")
        return self.running_version, self.running_commit


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


def test_touch_seen_at_leaves_a_corrupt_state_file_alone(tmp_path):
    """A `state.json` that cannot be parsed - a hand edit, a torn write,
    binary garbage from a failing SD card - must not be replaced with a
    fresh, id-less `idle` state. Overwriting it would erase whatever `id`
    it held; the next pass would then see no reason to skip a request it
    had already executed and would run it again from scratch, every two
    seconds, forever."""
    base = tmp_path / "update"
    path = state_path(base)
    path.parent.mkdir(parents=True)
    corrupt = b"\xff\xfe not valid JSON, and not valid UTF-8 either"
    path.write_bytes(corrupt)
    commands = FakeCommands()

    pass_once(base, commands)

    assert path.read_bytes() == corrupt
    # And nothing was invoked - this was a quiet pass with no request.
    assert commands.calls == []


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
