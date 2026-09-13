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

"""The OTBR watchdog - see `scripts/otbr-watchdog.sh`.

Runs the REAL script against a fake `docker` that records every call, so
the assertions are about what the watchdog does, not about what its
source text contains. A grep over the script would pass just as happily
if the removal ran AFTER the restart, which is precisely the bug this
guards against.

`sleep` is faked too: the script waits up to 60 s for the network to come
back, and a test has no reason to.

`docker inspect` answers with a start time long past unless a test says
otherwise, so the grace period for a freshly started container stays out
of the way of every test that is not about it. `OTBR_WATCHDOG_LOCK` gives
each run a lock file of its own.

`IF_INET6` points the interface check at a fixture instead of
`/proc/net/if_inet6`, the same seam `install.sh` uses for `RFKILL_DIR`.
Without it the outcome would depend on whether the machine running the
tests happens to have a Thread interface - on the Raspberry Pi this very
script runs on, every "no Thread interface" test would take the
"everything is fine" branch and assert nothing.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCHDOG = REPO_ROOT / "scripts" / "otbr-watchdog.sh"

# Columns of /proc/net/if_inet6: address, index, prefix length, scope,
# flags, device. The script looks for scope 00 (global) on a wpan*
# device.
_LOOPBACK = "00000000000000000000000000000001 01 80 10 80       lo\n"
_THREAD = "fd7df0629267d2e000000000000ffc11 05 40 00 80    wpan0\n"

_DOCKER_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$DOCKER_CALLS"
case "$1" in
  ps)      printf 'otbr\\n' ;;
  inspect) printf '%s\\n' "${DOCKER_STARTED_AT:-2020-01-01T00:00:00.000000000Z}" ;;
  exec)    exit "${DOCKER_EXEC_STATUS:-0}" ;;
  compose) exit "${DOCKER_RESTART_STATUS:-0}" ;;
  logs)    printf 'a line from the otbr log\\n' ;;
esac
exit 0
"""

_SLEEP_STUB = "#!/bin/sh\nexit 0\n"

# Stands in for util-linux `flock` when a test asks for it: answers with
# `FLOCK_STATUS`, 1 being "another run holds the lock".
_FLOCK_STUB = """#!/bin/sh
printf 'flock %s\\n' "$*" >> "$DOCKER_CALLS"
exit "${FLOCK_STATUS:-0}"
"""


def _docker_time(seconds_ago: int) -> str:
    """A start time the way `docker inspect` prints it, with nanoseconds."""
    started = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    return started.strftime("%Y-%m-%dT%H:%M:%S.123456789Z")


@pytest.fixture
def watchdog(tmp_path):
    """Runs the watchdog with a recording `docker` and an instant `sleep`."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("docker", _DOCKER_STUB), ("sleep", _SLEEP_STUB)):
        path = bindir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    calls = tmp_path / "docker-calls.txt"
    calls.touch()
    lock = tmp_path / "watchdog.lock"
    lock.touch()

    def run(
        *, thread_up: bool, flock_stub: bool = False, **env: str
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        if flock_stub:
            path = bindir / "flock"
            path.write_text(_FLOCK_STUB, encoding="utf-8")
            path.chmod(0o755)
        if_inet6 = tmp_path / "if_inet6"
        if_inet6.write_text(_LOOPBACK + (_THREAD if thread_up else ""), encoding="utf-8")
        proc = subprocess.run(
            ["bash", str(WATCHDOG)],
            capture_output=True,
            text=True,
            # The failure paths are what several of these tests assert on,
            # so a non-zero status is a result here, not an error.
            check=False,
            env={
                **os.environ,
                "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                "DOCKER_CALLS": str(calls),
                "IF_INET6": str(if_inet6),
                "OTBR_WATCHDOG_LOCK": str(lock),
                **env,
            },
        )
        recorded = [line for line in calls.read_text(encoding="utf-8").splitlines() if line]
        return proc, recorded

    return run


def test_a_working_thread_interface_is_left_alone(watchdog):
    """The watchdog must not restart anything while the network is up -
    it runs every minute."""
    proc, calls = watchdog(thread_up=True)
    assert proc.returncode == 0
    assert not any("restart" in call for call in calls)
    assert proc.stdout == ""


def test_the_stale_pid_file_is_removed_before_the_restart(watchdog):
    """The bug this fixes, measured on the Pi on 11 September 2026.

    `/run/otbr-agent.pid` lives in the container's writable layer and
    therefore survives a restart, while the PID namespace starts over at
    1 - so the file names a PID that the new container hands to some
    other process. `/etc/init.d/otbr-agent`'s start guard then reports
    "already started; not starting", the container comes up with no Thread
    daemon at all, and the next watchdog run is the first chance to
    recover - five minutes later, when it ran every five minutes.

    Removing the file BEFORE the restart is the whole fix, and the order
    is the whole point: afterwards would delete the pidfile of the agent
    that just started.
    """
    _proc, calls = watchdog(thread_up=False)
    removals = [i for i, call in enumerate(calls) if "rm" in call and "otbr-agent.pid" in call]
    restarts = [i for i, call in enumerate(calls) if "compose restart" in call]
    assert removals, f"the stale pid file is never removed; docker calls were {calls}"
    assert restarts, f"no restart was attempted; docker calls were {calls}"
    assert removals[0] < restarts[0], f"removal must precede the restart; got {calls}"


def test_the_restart_happens_even_if_the_pid_file_cannot_be_removed(watchdog):
    """A stopped container makes `docker exec` fail, and that is exactly
    the case the watchdog exists for. Giving up there would turn a
    recoverable outage into a permanent one."""
    _proc, calls = watchdog(thread_up=False, DOCKER_EXEC_STATUS="1")
    assert any("compose restart" in call for call in calls), calls


def test_a_failed_restart_is_reported(watchdog):
    proc, _calls = watchdog(thread_up=False, DOCKER_RESTART_STATUS="1")
    assert proc.returncode == 1
    assert "failed" in proc.stdout.lower()


def test_a_run_that_finds_the_lock_held_does_nothing(watchdog):
    """Cron starts a run every minute, and a run that restarted the agent
    waits up to 60 s for the network. A second run must not restart the
    agent under the first one, and must not write to the log either.

    Fault to prove it: remove the `flock -n` block - the second run
    restarts `otbr`."""
    proc, calls = watchdog(thread_up=False, flock_stub=True, FLOCK_STATUS="1")
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert calls == ["flock -n 9"]


@pytest.mark.skipif(shutil.which("flock") is None, reason="util-linux flock is not installed")
def test_a_real_lock_held_by_another_run_stops_this_one(watchdog, tmp_path):
    """The same with the real `flock`, as on the Pi: the lock is held here
    with flock(2), which is what util-linux `flock` takes as well."""
    # The fixture's lock file, in the same `tmp_path`.
    with open(tmp_path / "watchdog.lock", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        proc, calls = watchdog(thread_up=False)
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert calls == []


def test_a_free_lock_lets_the_run_go_ahead(watchdog):
    _proc, calls = watchdog(thread_up=False, flock_stub=True, FLOCK_STATUS="0")
    assert calls[0] == "flock -n 9"
    assert any("compose restart" in call for call in calls), calls


def test_a_container_started_less_than_90_seconds_ago_is_left_alone(watchdog):
    """Boot, an update, or this script's own restart a minute ago: the agent
    is still attaching, which takes 22-35 s on the Pi, and a restart now
    would make it start over.

    Fault to prove it: remove the grace check - the run restarts `otbr`."""
    proc, calls = watchdog(thread_up=False, DOCKER_STARTED_AT=_docker_time(10))
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert not any("compose restart" in call for call in calls), calls
    assert not any(call.startswith("exec") for call in calls), calls


def test_a_container_started_longer_ago_is_restarted(watchdog):
    proc, calls = watchdog(thread_up=False, DOCKER_STARTED_AT=_docker_time(120))
    assert any("compose restart" in call for call in calls), calls
    assert "restarting otbr" in proc.stdout
