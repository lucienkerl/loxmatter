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
from loxmatter.update.protocol import UpdateRequest, read_state, write_request


class FakeCommands:
    """A `Commands` (plus `is_ancestor`) that always succeeds and is
    healthy on the first poll - the loop-skipping behaviour is what these
    tests are about, not the state machine `run_update` already owns
    (see `tests/update/test_runner.py`)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.tag = "stable"

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


def pass_once(base: Path, commands: FakeCommands) -> None:
    run_once(
        base,
        commands,
        running_version="0.2.0",
        running_commit="abc1234",
        is_ancestor=commands.is_ancestor,
        sleep=lambda _seconds: None,
    )


def test_the_same_request_id_is_executed_only_once(tmp_path):
    """The bridge writes the file once, but the loop reads it every two
    seconds until someone replaces it. Without the id check, a single
    press would update forever."""
    base = tmp_path / "update"
    write_request(
        base,
        UpdateRequest(id="req-1", channel="stable", target="v0.3.0", requested_at=now_iso()),
    )
    commands = FakeCommands()

    pass_once(base, commands)
    first_pass_calls = list(commands.calls)
    assert "recreate" in first_pass_calls
    state = read_state(base)
    assert state is not None
    assert state.id == "req-1"
    assert state.phase == "done"

    # request.json still names the same id - a second pass must not run
    # the update again.
    pass_once(base, commands)
    assert commands.calls == first_pass_calls


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
    # single docker/git/curl call.
    assert commands.calls == []
