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

"""The files the bridge and the sidecar share, and nothing else."""

from __future__ import annotations

import json
import os

import pytest

from loxmatter.update.protocol import (
    UpdateRequest,
    read_request,
    read_state,
    request_path,
    state_path,
    write_json_atomically,
    write_request,
)


def test_no_request_reads_as_none(tmp_path):
    """The normal state of an installation is that nothing is pending.

    A missing file must not be an error: the sidecar polls this path
    every two seconds for the entire life of the machine, and almost
    every read finds nothing.
    """
    assert read_request(tmp_path) is None
    assert read_state(tmp_path) is None


def test_a_written_request_reads_back_field_for_field(tmp_path):
    written = UpdateRequest(
        id="01J000000000000000000000",
        channel="stable",
        target="v0.3.0",
        requested_at="2026-09-09T10:00:00.000000+00:00",
    )
    write_request(tmp_path, written)
    assert read_request(tmp_path) == written


def test_a_half_written_file_is_never_observed(tmp_path):
    """The sidecar polls while the bridge writes. A reader must never see
    a truncated file, which is why the write goes to a temporary name and
    is renamed - `os.replace` is atomic within a filesystem.

    This checks the mechanism rather than trying to race it: after the
    call, no temporary file is left behind, so no reader could have found
    one to parse.

    The base is a subdirectory rather than `tmp_path` itself because the
    autouse fixture in the parent conftest already puts a fake home there,
    and counting leftovers only means something in a directory this test
    owns.
    """
    base = tmp_path / "update"
    base.mkdir()
    write_json_atomically(request_path(base), {"id": "x"})
    leftovers = [p.name for p in base.iterdir() if p.name != request_path(base).name]
    assert leftovers == []


def test_a_corrupt_request_reads_as_none_rather_than_raising(tmp_path):
    """A truncated or hand-edited file must not stop the sidecar's loop.

    Returning `None` means "nothing to do", and the next well-formed
    request is picked up normally. Raising here would end the loop and
    leave the installation without an updater until someone restarts the
    container - the failure mode this whole design exists to avoid.
    """
    request_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert read_request(tmp_path) is None


def test_a_wrong_shaped_steps_field_reads_as_none_rather_than_raising(tmp_path):
    """`{"phase": "idle", "steps": null, "updater_seen_at": "t"}` is well
    formed JSON with every required key, but `[str(step) for step in None]`
    raises `TypeError` - only `KeyError` was ever caught. A file this
    malformed must read the same way as a truncated one: as `None`, not
    as an exception that stops the sidecar's poll loop."""
    state_path(tmp_path).write_text(
        json.dumps({"phase": "idle", "steps": None, "updater_seen_at": "t"}),
        encoding="utf-8",
    )
    assert read_state(tmp_path) is None

    # A field of the wrong shape that does not happen to raise is just as
    # malformed and must be rejected the same way.
    state_path(tmp_path).write_text(
        json.dumps({"phase": "idle", "steps": 5, "updater_seen_at": "t"}),
        encoding="utf-8",
    )
    assert read_state(tmp_path) is None


def test_a_phase_outside_the_known_set_reads_as_none(tmp_path):
    """The plan's interface list names eight values for `phase`. A state
    file written by some future version of the updater - or edited by
    hand - with a ninth value is not one `UpdateState` can represent, and
    must read as `None` like every other malformed case rather than be
    cast through without a check."""
    state_path(tmp_path).write_text(
        json.dumps({"phase": "exploding", "steps": [], "updater_seen_at": "t"}),
        encoding="utf-8",
    )
    assert read_state(tmp_path) is None


def test_a_failed_replace_leaves_no_tmp_file_behind(tmp_path, monkeypatch):
    """`os.replace` can itself fail - a misconfigured mount making the
    rename cross a filesystem boundary, say. The `.tmp` sibling this
    function writes before renaming must not be left in the directory for
    the sidecar to trip over on its next poll, and the original error
    must still reach the caller rather than being swallowed alongside it.

    The base is a subdirectory rather than `tmp_path` itself for the same
    reason as `test_a_half_written_file_is_never_observed` above: the
    autouse fixture in the parent conftest already puts a fake home in
    `tmp_path`, and counting leftovers only means something in a
    directory this test owns.
    """
    base = tmp_path / "update"
    base.mkdir()

    def raiser(_src: object, _dst: object) -> None:
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "replace", raiser)

    with pytest.raises(OSError):
        write_json_atomically(request_path(base), {"id": "x"})

    assert list(base.iterdir()) == []


def test_the_written_file_is_plain_readable_json(tmp_path):
    """The sidecar is Python today. The file format must not assume that:
    anyone debugging on the Pi reads it with `cat`.
    """
    write_request(
        tmp_path,
        UpdateRequest(id="a", channel="dev", target="abc1234", requested_at="t"),
    )
    payload = json.loads(request_path(tmp_path).read_text(encoding="utf-8"))
    assert payload == {"id": "a", "channel": "dev", "target": "abc1234", "requested_at": "t"}
