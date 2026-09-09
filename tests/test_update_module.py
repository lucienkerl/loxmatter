# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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

"""Tests for the file side of the update - design "Applying updates
through the web UI" (2026-09-08), section 7.

This module touches only files, never the network and never HTTP. So
everything here runs against a tmp_path directory, without fakes."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

import pytest

from loxmatter.update import (
    UpdateBusyError,
    read_log,
    read_state,
    request_update,
    updater_present,
)

JETZT = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


def _state(tmp_path, **fields):
    body = {"id": "a", "phase": "idle", "updater_seen_at": JETZT.strftime("%Y-%m-%dT%H:%M:%SZ")}
    body.update(fields)
    (tmp_path / "state.json").write_text(json.dumps(body), encoding="utf-8")


def test_without_a_state_file_there_is_no_state(tmp_path):
    assert read_state(tmp_path) is None


def test_a_half_written_file_counts_as_no_state(tmp_path):
    # The sidecar writes atomically (temp + rename), but a truncated JSON
    # must still not trigger a 500 here: the web UI polls this route once
    # a second, and a single failure would look there like a broken
    # update.
    (tmp_path / "state.json").write_text('{"phase": "pu', encoding="utf-8")
    assert read_state(tmp_path) is None


def test_a_non_object_json_counts_as_no_state(tmp_path):
    # `set_state` in the sidecar always writes a JSON *object*. A bare
    # array, string or number is nonetheless valid JSON - `json.loads`
    # would happily hand one back - and `.get("phase")` on it is either a
    # TypeError (list, str) or nonsense (a number has no `.get` either).
    # Whatever produced this file (hand edit, a future bug), it must not
    # become an exception here.
    (tmp_path / "state.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert read_state(tmp_path) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits do not apply on Windows")
def test_an_unreadable_state_file_counts_as_no_state(tmp_path):
    # A file that exists, is complete, well-formed JSON, and simply cannot
    # be opened (permissions changed under a running process, an odd
    # mount) must be handled the same way as a missing or truncated one -
    # not surface as a raw PermissionError.
    _state(tmp_path)
    path = tmp_path / "state.json"
    path.chmod(0o000)
    try:
        assert read_state(tmp_path) is None
    finally:
        path.chmod(0o644)


def test_the_state_is_read_in_full(tmp_path):
    # The sidecar's own state.json (update-once.sh, set_state()) carries
    # the version fields under the literal keys "from" and "to" - "from"
    # is a Python reserved word, so it cannot be written as a plain
    # keyword argument here and has to go through a dict-unpack instead.
    # An earlier draft of this test passed `from_version=`/`to_version=`,
    # which _state() would have happily accepted as keys of THEIR OWN
    # name - silently testing nothing about the "from"/"to" keys
    # read_state() actually reads, and passing anyway because both sides
    # of the mismatched assertion were `None`.
    _state(
        tmp_path,
        phase="failed",
        error="nicht gesund",
        rolled_back=True,
        healthy=True,
        **{"from": "0.2.0", "to": "0.3.0"},
    )
    state = read_state(tmp_path)
    assert state.phase == "failed"
    assert state.from_version == "0.2.0"
    assert state.to_version == "0.3.0"
    assert state.error == "nicht gesund"
    assert state.rolled_back is True


def test_non_string_optional_fields_are_read_as_none(tmp_path):
    # `id`/`from`/`to`/`error`/`updater_seen_at` are always plain strings
    # in a well-formed state.json (the sidecar writes every one of them
    # through jq's `--arg`, which stringifies). A hand-edited or corrupted
    # file could carry some other JSON type under one of these keys; the
    # dataclass promises `str | None` (Task 8 builds its HTTP response
    # model directly off it), so a value of the wrong type must fall back
    # to `None` rather than leak an `int`/`list` past the type this module
    # advertises.
    _state(tmp_path, id=1, error=False, **{"from": None, "to": ["not", "a", "string"]})
    state = read_state(tmp_path)
    assert state.id is None
    assert state.from_version is None
    assert state.to_version is None
    assert state.error is None


def test_a_fresh_heartbeat_means_the_sidecar_is_present(tmp_path):
    _state(tmp_path)
    assert updater_present(read_state(tmp_path), now=JETZT + timedelta(seconds=5)) is True


def test_a_stale_heartbeat_means_no_sidecar(tmp_path):
    # It reports in every two seconds. Half a minute of silence means
    # nobody would pick up the request - the web UI must then not show a
    # button that does nothing.
    _state(tmp_path)
    assert updater_present(read_state(tmp_path), now=JETZT + timedelta(seconds=90)) is False


def test_without_state_there_is_no_sidecar(tmp_path):
    assert updater_present(None, now=JETZT) is False


def test_updater_present_with_an_unparseable_timestamp_is_false(tmp_path):
    _state(tmp_path, updater_seen_at="not-a-timestamp")
    assert updater_present(read_state(tmp_path), now=JETZT) is False


def test_updater_present_with_a_timestamp_from_the_future_is_false(tmp_path):
    # The sidecar and the bridge run on the same host and share the same
    # clock. A heartbeat that is noticeably AHEAD of "now" is not a
    # sidecar reporting from the future - it is a clock that jumped (an
    # NTP correction, a drifted RTC after a reboot) or a hand-edited
    # state.json. Treating that as "present, indefinitely" would be
    # exactly the false positive this function exists to prevent: a
    # button that writes into a volume nobody is reading anymore.
    _state(tmp_path, updater_seen_at=(JETZT + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert updater_present(read_state(tmp_path), now=JETZT) is False


def test_a_job_is_written_and_gets_an_id(tmp_path):
    _state(tmp_path)
    job = request_update(tmp_path, channel="stable", target="0.3.0")
    body = json.loads((tmp_path / "request.json").read_text(encoding="utf-8"))
    assert body["id"] == job
    assert body["channel"] == "stable"
    assert body["target"] == "0.3.0"
    assert body["requested_at"]


def test_two_jobs_get_different_ids(tmp_path):
    _state(tmp_path)
    erste = request_update(tmp_path, channel="stable", target="0.3.0")
    _state(tmp_path, id=erste, phase="done")
    zweite = request_update(tmp_path, channel="stable", target="0.4.0")
    assert erste != zweite


def test_while_an_update_is_running_a_second_one_is_not_accepted(tmp_path):
    _state(tmp_path, id="laeuft", phase="pull")
    with pytest.raises(UpdateBusyError):
        request_update(tmp_path, channel="stable", target="0.3.0")


def test_after_a_finished_update_a_new_one_goes_through(tmp_path):
    _state(tmp_path, id="alt", phase="done")
    assert request_update(tmp_path, channel="stable", target="0.4.0")


def test_a_second_call_before_the_sidecar_wakes_does_not_destroy_the_first(tmp_path):
    # The sidecar only updates state.json from its two-second poll loop.
    # In the window before that poll wakes, state.json still reports
    # whatever end state preceded the first request - "idle" here - so
    # the busy check on phase alone cannot see that a request is already
    # sitting in request.json, unread. Proven end to end first (see
    # test_two_calls_in_the_window_used_to_silently_lose_the_first_job
    # below, kept as a permanent regression guard): without this
    # refusal, the second call's os.replace overwrote the first job's
    # request.json, and that job's id - already handed back to its
    # caller - was never written anywhere else again.
    _state(tmp_path, id=None, phase="idle")
    erste = request_update(tmp_path, channel="stable", target="0.3.0")
    with pytest.raises(UpdateBusyError):
        request_update(tmp_path, channel="stable", target="0.4.0")
    body = json.loads((tmp_path / "request.json").read_text(encoding="utf-8"))
    assert body["id"] == erste


def test_a_request_the_sidecar_has_already_reflected_in_state_is_not_pending(tmp_path):
    # request.json's id equals state.json's id: the sidecar has already
    # caught up with this exact request (whatever its current phase), so
    # a new call must not be blocked by a stale request.json still lying
    # around.
    (tmp_path / "request.json").write_text(
        json.dumps({"id": "erste", "channel": "stable", "target": "0.3.0", "requested_at": "x"}),
        encoding="utf-8",
    )
    _state(tmp_path, id="erste", phase="done")
    assert request_update(tmp_path, channel="stable", target="0.4.0")


def test_a_request_already_marked_handled_is_not_pending(tmp_path):
    # The handled/<job-id> marker is written the moment the sidecar
    # ACCEPTS a request - before state.json necessarily reflects it, and
    # surviving even a later state.json reset. A stale request.json
    # naming an already-handled id must not block a new request either.
    (tmp_path / "request.json").write_text(
        json.dumps({"id": "erste", "channel": "stable", "target": "0.3.0", "requested_at": "x"}),
        encoding="utf-8",
    )
    (tmp_path / "handled").mkdir()
    (tmp_path / "handled" / "erste").touch()
    _state(tmp_path, id=None, phase="idle")  # simulates a state.json reset
    assert request_update(tmp_path, channel="stable", target="0.4.0")


def test_a_missing_handled_directory_is_not_a_crash(tmp_path):
    # A fresh installation - or a bridge started before the sidecar's own
    # first pass - has no handled/ directory at all yet. That must read
    # as "nothing has ever been marked handled", not raise.
    (tmp_path / "request.json").write_text(
        json.dumps({"id": "fremd", "channel": "stable", "target": "0.3.0", "requested_at": "x"}),
        encoding="utf-8",
    )
    assert not (tmp_path / "handled").exists()
    _state(tmp_path, id=None, phase="idle")
    with pytest.raises(UpdateBusyError):
        request_update(tmp_path, channel="stable", target="0.4.0")


def test_an_unparseable_request_file_does_not_block_a_new_request(tmp_path):
    # The sidecar's own "request is not readable" branch rejects such a
    # file outright and never marks it handled - it will never be acted
    # on. Blocking new requests on it forever would lock a caller out
    # permanently over a job that was never going anywhere.
    (tmp_path / "request.json").write_text('{"id": "unvollst', encoding="utf-8")
    _state(tmp_path, id=None, phase="idle")
    assert request_update(tmp_path, channel="stable", target="0.4.0")


def test_a_request_file_without_a_usable_id_does_not_block_a_new_request(tmp_path):
    # Valid JSON, but not an object with a string id - same "the sidecar
    # will only ever reject this" reasoning as the unparseable case.
    (tmp_path / "request.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    _state(tmp_path, id=None, phase="idle")
    assert request_update(tmp_path, channel="stable", target="0.4.0")


def test_the_job_is_written_atomically(tmp_path, monkeypatch):
    """The sidecar reads every two seconds. If it saw the file half
    written, it would reject a valid request as invalid - and because it
    touches each id only once, that request would never come again."""
    gesehen = []
    echtes_replace = __import__("os").replace

    def spion(src, dst):
        gesehen.append((str(src), str(dst)))
        echtes_replace(src, dst)

    monkeypatch.setattr("loxmatter.update.os.replace", spion)
    _state(tmp_path)
    request_update(tmp_path, channel="stable", target="0.3.0")
    assert gesehen, "request.json must land in place via os.replace"


def test_the_log_returns_the_last_lines(tmp_path):
    (tmp_path / "log.txt").write_text("\n".join(f"zeile {i}" for i in range(100)), encoding="utf-8")
    assert read_log(tmp_path, lines=5) == [f"zeile {i}" for i in range(95, 100)]


def test_without_a_log_the_list_is_empty(tmp_path):
    assert read_log(tmp_path) == []
