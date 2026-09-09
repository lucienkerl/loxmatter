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

from loxmatter.update.protocol import (
    UpdateRequest,
    read_request,
    read_state,
    request_path,
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
    """
    write_json_atomically(request_path(tmp_path), {"id": "x"})
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != request_path(tmp_path).name]
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
