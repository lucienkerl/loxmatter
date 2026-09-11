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

`IF_INET6` points the interface check at a fixture instead of
`/proc/net/if_inet6`, the same seam `install.sh` uses for `RFKILL_DIR`.
Without it the outcome would depend on whether the machine running the
tests happens to have a Thread interface - on the Raspberry Pi this very
script runs on, every "no Thread interface" test would take the
"everything is fine" branch and assert nothing.
"""

from __future__ import annotations

import os
import subprocess
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
  exec)    exit "${DOCKER_EXEC_STATUS:-0}" ;;
  compose) exit "${DOCKER_RESTART_STATUS:-0}" ;;
  logs)    printf 'a line from the otbr log\\n' ;;
esac
exit 0
"""

_SLEEP_STUB = "#!/bin/sh\nexit 0\n"


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

    def run(*, thread_up: bool, **env: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
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
                **env,
            },
        )
        recorded = [line for line in calls.read_text(encoding="utf-8").splitlines() if line]
        return proc, recorded

    return run


def test_a_working_thread_interface_is_left_alone(watchdog):
    """The watchdog must not restart anything while the network is up -
    it runs every five minutes."""
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
    daemon at all, and the next watchdog run five minutes later is the
    first chance to recover.

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
