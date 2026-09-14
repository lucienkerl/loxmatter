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

The Thread network's state comes from `docker exec otbr ot-ctl state`, not
from a host file: `DOCKER_OTCTL_STATE` (default "leader") is what the fake
`ot-ctl state` answers, before its `\\r`-and-`Done`-terminated real-world
shape is applied. `thread_up` in the `watchdog` fixture is sugar for
picking a sane default ("leader" or "detached") - pass `DOCKER_OTCTL_STATE`
explicitly to test a particular state. `DOCKER_RESTARTED_MARKER` (a file
the fixture always points at, but that starts out missing) lets a test
give the fake a DIFFERENT answer after the watchdog has restarted the
container, via `DOCKER_OTCTL_STATE_AFTER_RESTART` - every test that
doesn't set it keeps answering with `DOCKER_OTCTL_STATE` throughout, exactly
as before there was a restart to distinguish.

Container age comes from `docker top otbr -o pid,etimes`, not from `ps`:
a real daemon refuses a bare `-o etimes` ("Couldn't find PID field in ps
output", exit 1 - the daemon needs the PID column itself to map host
processes back to the container), which the fake reproduces. With
`pid,etimes` it prints a `PID                 ELAPSED` header the way
`docker top` really does, then one `PID ELAPSED` row per process from
`DOCKER_TOP_ETIMES` (default `"4242 86400"`) - several rows, separated by
a real newline, test "the largest ELAPSED decides" and "a PID is never
mistaken for an age".

`DOCKER_HANG`, when set, makes the fake `docker` replace itself with a
long real sleep (by absolute path - `sleep` on PATH is the instant stub)
for any call whose full argument list CONTAINS `DOCKER_HANG` as a
substring - `"top"`, `"rm -f"`, `"restart -t"`, `"ps -a"` pick out one call
site each without also matching the others. `timeout` is what then has to
end the call.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCHDOG = REPO_ROOT / "scripts" / "otbr-watchdog.sh"

# `docker exec otbr ot-ctl state`, `docker top otbr -o pid,etimes`,
# `docker restart -t 10 otbr`: the fake mirrors the real CLI's shape for
# each, including the `\r`-terminated, `Done`-suffixed answer a Thread CLI
# gives, the `PID ELAPSED` header `docker top` prints before its data
# lines, and a bare `-o etimes` (no `pid` column) failing outright the way
# a real daemon does. A call recorded before it is checked against
# `DOCKER_HANG`, so a hung call still shows up in `calls` - callers assert
# restart was ATTEMPTED, even where it then timed out.
_DOCKER_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$DOCKER_CALLS"
if [ -n "${DOCKER_HANG:-}" ]; then
  case "$*" in
    *"$DOCKER_HANG"*) exec "$REAL_SLEEP" 60 ;;
  esac
fi
case "$1" in
  ps)
    printf 'otbr\\n'
    ;;
  top)
    case "$4" in
      etimes)
        printf "Error response from daemon: Couldn't find PID field in ps output\\n" >&2
        exit 1
        ;;
      pid,etimes)
        if [ "${DOCKER_TOP_STATUS:-0}" = "0" ]; then
          printf 'PID                 ELAPSED\\n'
          printf '%s\\n' "${DOCKER_TOP_ETIMES-4242 86400}"
        fi
        exit "${DOCKER_TOP_STATUS:-0}"
        ;;
    esac
    ;;
  exec)
    if [ "${DOCKER_EXEC_STATUS:-0}" = "0" ]; then
      case "$3" in
        ot-ctl)
          if [ -e "${DOCKER_RESTARTED_MARKER:-/nonexistent}" ]; then
            printf '%s\\r\\nDone\\r\\n' "${DOCKER_OTCTL_STATE_AFTER_RESTART:-${DOCKER_OTCTL_STATE-leader}}"
          else
            printf '%s\\r\\nDone\\r\\n' "${DOCKER_OTCTL_STATE-leader}"
          fi
          ;;
      esac
    fi
    exit "${DOCKER_EXEC_STATUS:-0}"
    ;;
  restart)
    : > "${DOCKER_RESTARTED_MARKER:-/dev/null}" 2>/dev/null || true
    exit "${DOCKER_RESTART_STATUS:-0}"
    ;;
  logs)
    printf 'a line from the otbr log\\n'
    ;;
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
    # Never created by the fixture - only `docker restart` (the fake)
    # creates it, so its mere existence is "a restart already happened".
    restarted_marker = tmp_path / "restarted-marker"

    def run(
        *, thread_up: bool, flock_stub: bool = False, **env: str
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        if flock_stub:
            path = bindir / "flock"
            path.write_text(_FLOCK_STUB, encoding="utf-8")
            path.chmod(0o755)
        env.setdefault("DOCKER_OTCTL_STATE", "leader" if thread_up else "detached")
        proc = subprocess.run(
            ["bash", str(WATCHDOG)],
            capture_output=True,
            text=True,
            timeout=45,
            # The failure paths are what several of these tests assert on,
            # so a non-zero status is a result here, not an error.
            check=False,
            env={
                **os.environ,
                "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                "DOCKER_CALLS": str(calls),
                "DOCKER_RESTARTED_MARKER": str(restarted_marker),
                "OTBR_WATCHDOG_LOCK": str(lock),
                "REAL_SLEEP": shutil.which("sleep") or "/bin/sleep",
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
    assert not any(call.startswith("restart") for call in calls)
    assert proc.stdout == ""


@pytest.mark.parametrize("state", ["leader", "router", "child"])
def test_up_states_are_left_alone(watchdog, state):
    proc, calls = watchdog(thread_up=True, DOCKER_OTCTL_STATE=state)
    assert proc.returncode == 0
    assert not any(call.startswith("restart") for call in calls)


@pytest.mark.parametrize("state", ["detached", "disabled", ""])
def test_down_states_trigger_a_restart(watchdog, state):
    _proc, calls = watchdog(thread_up=False, DOCKER_OTCTL_STATE=state)
    assert any(call.startswith("restart") for call in calls), calls


def test_a_failing_exec_is_treated_as_down(watchdog):
    """`docker exec ... ot-ctl state` itself failing (the agent or the
    container is gone) must read as "down", not abort the script under
    `set -euo pipefail`."""
    _proc, calls = watchdog(thread_up=False, DOCKER_EXEC_STATUS="1")
    assert any(call.startswith("restart") for call in calls), calls


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
    restarts = [i for i, call in enumerate(calls) if call.startswith("restart")]
    assert removals, f"the stale pid file is never removed; docker calls were {calls}"
    assert restarts, f"no restart was attempted; docker calls were {calls}"
    assert removals[0] < restarts[0], f"removal must precede the restart; got {calls}"


def test_the_restart_happens_even_if_the_pid_file_cannot_be_removed(watchdog):
    """A stopped container makes `docker exec` fail, and that is exactly
    the case the watchdog exists for. Giving up there would turn a
    recoverable outage into a permanent one."""
    _proc, calls = watchdog(thread_up=False, DOCKER_EXEC_STATUS="1")
    assert any(call.startswith("restart") for call in calls), calls


def test_a_failed_restart_is_reported(watchdog):
    proc, _calls = watchdog(thread_up=False, DOCKER_RESTART_STATUS="1")
    assert proc.returncode == 1
    assert "failed" in proc.stdout.lower()


def test_the_recovery_wait_polls_until_the_network_is_back(watchdog):
    proc, calls = watchdog(thread_up=False, DOCKER_OTCTL_STATE_AFTER_RESTART="leader")
    assert proc.returncode == 0
    assert "Thread network is back" in proc.stdout
    assert any(call.startswith("restart") for call in calls), calls


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
    assert any(call.startswith("restart") for call in calls), calls


def test_a_container_started_less_than_90_seconds_ago_is_left_alone(watchdog):
    """Boot, an update, or this script's own restart a minute ago: the agent
    is still attaching, which takes 22-35 s on the Pi, and a restart now
    would make it start over.

    Fault to prove it: remove the grace check - the run restarts `otbr`."""
    proc, calls = watchdog(thread_up=False, DOCKER_TOP_ETIMES="4242 10")
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert "top otbr -o pid,etimes" in calls
    assert not any(call.startswith("restart") for call in calls), calls
    assert not any("rm" in call and "otbr-agent.pid" in call for call in calls), calls


def test_a_container_started_longer_ago_is_restarted(watchdog):
    proc, calls = watchdog(thread_up=False, DOCKER_TOP_ETIMES="4242 120")
    assert any(call.startswith("restart") for call in calls), calls
    assert "restarting otbr" in proc.stdout
    assert "no grace period" not in proc.stdout


def test_the_largest_elapsed_value_decides(watchdog):
    """A container can have more than one process (an entrypoint plus the
    agent); the OLDEST one - the largest ELAPSED - is the container's age.

    Fault to prove it: take the smallest line instead - a container with
    one young process and one old one is then treated as young, and left
    alone although it has really been up for two minutes."""
    proc, calls = watchdog(thread_up=False, DOCKER_TOP_ETIMES="1 10\n2 120")
    assert any(call.startswith("restart") for call in calls), calls
    assert "restarting otbr" in proc.stdout


def test_a_large_pid_is_not_mistaken_for_an_old_container(watchdog):
    """`docker top -o pid,etimes` prints the PID first and ELAPSED second;
    a PID can easily exceed the grace period in seconds while its process
    just started. Real measurement on the Pi: PID 80195, ELAPSED 2.

    Fault to prove it: read the first column (PID) instead of the second
    (ELAPSED) - a PID of 4242 looks like a container that has been up for
    over an hour, and the run restarts an agent that only just started."""
    proc, calls = watchdog(thread_up=False, DOCKER_TOP_ETIMES="4242 30")
    assert proc.returncode == 0
    assert not any(call.startswith("restart") for call in calls), calls


def test_the_grace_period_does_not_read_the_wall_clock(watchdog, tmp_path):
    """A Pi has no real-time clock, and NTP may step it by days after boot.
    The container's age comes from the kernel via `docker top`, so a `date`
    that is years off changes nothing: a 10 s old container is still left
    alone.

    Fault to prove it: compare docker's StartedAt with `date +%s` again - a
    clock years ahead makes the young container look old, and it is
    restarted."""
    date = tmp_path / "bin" / "date"
    date.write_text(
        '#!/bin/sh\ncase "$*" in *%s*) echo 4102444800 ;; *) echo 2100-01-01 ;; esac\n',
        encoding="utf-8",
    )
    date.chmod(0o755)
    proc, calls = watchdog(thread_up=False, DOCKER_TOP_ETIMES="4242 10")
    assert proc.returncode == 0, proc.stdout
    assert not any(call.startswith("restart") for call in calls), calls


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"DOCKER_TOP_ETIMES": "4242 garbage"}, "an age that is not a number"),
        ({"DOCKER_TOP_ETIMES": ""}, "docker top printing nothing"),
        ({"DOCKER_TOP_STATUS": "1"}, "docker top itself failing"),
        ({"DOCKER_TOP_ETIMES": "4242 -5"}, "a negative age"),
        ({"DOCKER_TOP_ETIMES": "4242 18446744073709551615"}, "an age that wraps to -1 in bash"),
        ({"DOCKER_TOP_ETIMES": "4242 99999999999"}, "an age too long to be young"),
        ({"DOCKER_TOP_ETIMES": "4242 010"}, "an age with a leading zero"),
    ],
)
def test_an_age_that_cannot_be_read_skips_the_grace_period_not_the_restart(
    watchdog, overrides, why
):
    """Whatever goes wrong reading the container's age, the restart still
    happens: a watchdog that stays quiet because of a format would be the
    outage of 3 September again.

    Faults to prove it, one at a time: remove the `case` guard on a line -
    bash arithmetic reads a name as 0, and a negative or wrapped value as
    small, so the run exits as if the container had just started; remove
    the `|| true`-style status guard after `docker top` - `set -e` ends the
    run on the failing call before it restarts anything."""
    proc, calls = watchdog(thread_up=False, **overrides)
    assert any(call.startswith("restart") for call in calls), (why, proc.stdout, proc.stderr)
    assert "no grace period" in proc.stdout, (why, proc.stdout)
    assert "restarting otbr" in proc.stdout, (why, proc.stdout)


_needs_timeout = pytest.mark.skipif(
    shutil.which("timeout") is None, reason="GNU coreutils `timeout` is not installed"
)


@_needs_timeout
@pytest.mark.parametrize(
    ("hang", "message", "restarted"),
    [
        ("ps -a", "docker ps failed (no answer within 1 s)", False),
        ("top", "no grace period", True),
        ("rm -f", "Could not clear the stale pid file (no answer within 1 s)", True),
        ("restart -t", "Restarting otbr failed (no answer within 1 s)", True),
    ],
)
def test_a_docker_call_that_hangs_is_ended_and_logged(watchdog, hang, message, restarted):
    """A docker daemon that stops answering must not keep the run alive for
    good: it holds the lock, and every later run would exit quietly on it.
    Each call ends at its limit, and the run says so in the log.

    Fault to prove it: drop `timeout` from `bounded` - the run waits for the
    hanging call and the test's own time limit ends it."""
    started = time.monotonic()
    proc, calls = watchdog(
        thread_up=False,
        DOCKER_HANG=hang,
        OTBR_WATCHDOG_DOCKER_TIMEOUT="1",
        OTBR_WATCHDOG_RESTART_TIMEOUT="1",
    )
    assert time.monotonic() - started < 15
    assert message in proc.stdout, proc.stdout
    # Every case ends as a failure: the hung call itself, or the Thread
    # interface that does not come back in this fixture.
    assert proc.returncode == 1
    assert any(call.startswith("restart") for call in calls) is restarted, calls


def test_the_restart_has_a_longer_limit_than_a_query(watchdog, tmp_path):
    """A restart stops and starts the container; 120 s, not the 30 s of a
    query. The limits are what `timeout` is called with."""
    log = tmp_path / "timeout-calls.txt"
    fake = tmp_path / "bin" / "timeout"
    fake.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nshift 3\nexec "$@"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    watchdog(thread_up=False)
    limits = log.read_text(encoding="utf-8").splitlines()
    assert "-k 10 30 docker ps -a --format {{.Names}}" in limits, limits
    assert "-k 10 30 docker exec otbr ot-ctl state" in limits, limits
    assert "-k 10 30 docker top otbr -o pid,etimes" in limits, limits
    assert "-k 10 30 docker exec otbr rm -f /run/otbr-agent.pid" in limits, limits
    assert "-k 10 120 docker restart -t 10 otbr" in limits, limits
    assert "-k 10 30 docker logs --tail 20 otbr" in limits, limits


def test_no_docker_compose_call_is_ever_made(watchdog):
    """A container running this script has only the Docker socket, not the
    stack's `.env` and project directory that `docker compose` would need."""
    _proc, calls = watchdog(thread_up=False, DOCKER_OTCTL_STATE_AFTER_RESTART="leader")
    assert not any("compose" in call for call in calls), calls


def test_the_script_no_longer_reads_a_host_network_file():
    """A container is not in the host's network namespace and cannot see
    `/proc/net/if_inet6` at all."""
    assert "/proc/net/if_inet6" not in WATCHDOG.read_text(encoding="utf-8")


def test_the_container_ready_marker_exists_exactly_once():
    text = WATCHDOG.read_text(encoding="utf-8")
    assert text.count("# loxmatter-watchdog: container-ready") == 1
