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
"""The bridge's half of the radios file protocol (design 2026-09-11
"Radios in the Web UI", sections 6.2, 6.5, 7)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from loxmatter.radios.sidecar import (
    BluetoothRequest,
    RadioConfig,
    RadiosBusyError,
    ThreadRequest,
    read_radios_state,
    request_radios,
    sidecar_status,
)
from loxmatter.update import read_state

NOW = datetime(2026, 9, 11, 20, 0, 0, tzinfo=UTC)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _update_state(update_dir, phase="idle", seen=NOW):
    body = {"id": None, "phase": phase, "updater_seen_at": _stamp(seen)}
    (update_dir / "state.json").write_text(json.dumps(body), encoding="utf-8")


def _radios_state(update_dir, **fields):
    body = {
        "id": None,
        "phase": "idle",
        "steps": [],
        "error": None,
        "rolled_back": False,
        "healthy": None,
        "current": {
            "thread_enabled": True,
            "thread_device": "/dev/ttyUSB0",
            "bluetooth_adapter": 0,
            "otbr_running": True,
        },
        "capable": True,
        "capable_reason": None,
        "seen_at": _stamp(NOW),
    }
    body.update(fields)
    (update_dir / "radios-state.json").write_text(json.dumps(body), encoding="utf-8")


def test_a_full_state_is_read(tmp_path):
    _radios_state(tmp_path, id="j1", phase="verify_thread", steps=["validate", "verify_thread"])
    state = read_radios_state(tmp_path)
    assert state is not None
    assert (state.id, state.phase, state.steps) == (
        "j1",
        "verify_thread",
        ("validate", "verify_thread"),
    )
    assert state.current == RadioConfig(
        thread_enabled=True, thread_device="/dev/ttyUSB0", bluetooth_adapter=0, otbr_running=True
    )


@pytest.mark.parametrize("content", ["", "{", "[]", "null"])
def test_an_unreadable_state_reads_as_none(tmp_path, content):
    (tmp_path / "radios-state.json").write_text(content, encoding="utf-8")
    assert read_radios_state(tmp_path) is None


def test_a_malformed_current_block_reads_as_no_current(tmp_path):
    _radios_state(tmp_path, current={"thread_enabled": "yes"})
    state = read_radios_state(tmp_path)
    assert state is not None and state.current is None


def test_no_updater_means_missing(tmp_path):
    assert sidecar_status(None, None, now=NOW) == "missing"


def test_an_updater_without_the_radios_job_is_outdated(tmp_path):
    """Fault to prove it: treat a missing radios-state.json as ready."""
    _update_state(tmp_path)
    assert sidecar_status(read_state(tmp_path), None, now=NOW) == "outdated"


def test_a_stale_radios_heartbeat_is_outdated(tmp_path):
    _update_state(tmp_path)
    _radios_state(tmp_path, seen_at=_stamp(NOW - timedelta(seconds=31)))
    assert sidecar_status(read_state(tmp_path), read_radios_state(tmp_path), now=NOW) == "outdated"


def test_a_sidecar_without_the_dev_mount_is_unmounted(tmp_path):
    _update_state(tmp_path)
    _radios_state(tmp_path, capable=False, capable_reason="host_dev_not_mounted")
    assert sidecar_status(read_state(tmp_path), read_radios_state(tmp_path), now=NOW) == "unmounted"


def test_a_fresh_capable_sidecar_is_ready(tmp_path):
    _update_state(tmp_path)
    _radios_state(tmp_path)
    assert sidecar_status(read_state(tmp_path), read_radios_state(tmp_path), now=NOW) == "ready"


def _request(update_dir):
    return request_radios(
        update_dir,
        thread=ThreadRequest(enabled=True, device="/dev/serial/by-id/x"),
        bluetooth=BluetoothRequest(adapter=0),
    )


def test_a_request_is_written_with_exactly_the_protocol_keys(tmp_path):
    _update_state(tmp_path)
    _radios_state(tmp_path)
    job_id = _request(tmp_path)
    body = json.loads((tmp_path / "radios-request.json").read_text(encoding="utf-8"))
    assert set(body) == {"id", "thread", "bluetooth", "requested_at"}
    assert body["id"] == job_id
    assert body["thread"] == {"enabled": True, "device": "/dev/serial/by-id/x"}
    assert body["bluetooth"] == {"adapter": 0}
    assert not (tmp_path / "radios-request.json.tmp").exists()


def test_a_half_that_is_not_changing_is_written_as_a_json_null(tmp_path):
    """Task 7d: `None` for a half means "leave this radio alone", and it
    has to reach the sidecar as a literal `null` under a key that is still
    there - the sidecar's schema check is a strict whitelist on the key
    set, so an OMITTED key would be rejected as `request_malformed`.

    Fault to prove it: build the body by dropping `None` halves
    (`{k: v for k, v in ... if v is not None}`) instead of writing `null`
    - the key set then no longer matches and the real sidecar refuses the
    request."""
    _update_state(tmp_path)
    _radios_state(tmp_path)
    request_radios(tmp_path, thread=None, bluetooth=BluetoothRequest(adapter=1))
    body = json.loads((tmp_path / "radios-request.json").read_text(encoding="utf-8"))
    assert set(body) == {"id", "thread", "bluetooth", "requested_at"}
    assert body["thread"] is None
    assert body["bluetooth"] == {"adapter": 1}


def test_no_request_while_an_update_runs(tmp_path):
    """Fault to prove it: drop the update-phase check."""
    _update_state(tmp_path, phase="recreate")
    _radios_state(tmp_path)
    with pytest.raises(RadiosBusyError):
        _request(tmp_path)
    assert not (tmp_path / "radios-request.json").exists()


def test_no_request_while_a_radio_job_runs(tmp_path):
    _update_state(tmp_path)
    _radios_state(tmp_path, id="j1", phase="verify_thread")
    with pytest.raises(RadiosBusyError):
        _request(tmp_path)


def test_no_request_while_the_previous_one_was_not_picked_up(tmp_path):
    """A request the sidecar has not handled yet must not be overwritten.
    Fault to prove it: drop the pending-request check."""
    _update_state(tmp_path)
    _radios_state(tmp_path, id="old", phase="done")
    first = _request(tmp_path)
    with pytest.raises(RadiosBusyError):
        _request(tmp_path)
    (tmp_path / "radios-handled").mkdir()
    (tmp_path / "radios-handled" / first).touch()
    _request(tmp_path)
