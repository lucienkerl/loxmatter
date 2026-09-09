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
"""Behavioral tests for deploy/updater/entrypoint.sh (review fix, Important
#2, updater Stufe 2).

Same idea as test_install_script.py / test_update_script.py: run the real
script and check WHICH commands it chooses, not what they do. Unlike
those two, this script has no side-effectful system commands to gate
behind a sealed PATH - its only external dependencies are `timeout` (a
harmless, read-only wrapper) and the worker it invokes, and the worker
path is already overridable via the WORKER environment variable
specifically so it can be pointed at a throwaway stub instead of the real
/opt/loxmatter/update-once.sh. So these tests run against the real
system PATH and point WORKER at small fixture scripts.

LOOP_ONCE=1 makes the otherwise-infinite `while` loop return after one
pass. It is not a test-only branch that changes what a pass does: it only
changes how many times the loop condition is re-checked, so every pass a
test observes goes through the exact same timeout-wrapped worker
invocation and exit-status handling that an unbounded run in the
container would use. See the comment beside it in entrypoint.sh.

What this file does NOT claim to test, because it cannot be tested
honestly without a container runtime:
- That `docker stop` actually delivers SIGTERM to PID 1 the way a
  manually-sent `kill -TERM` does in these tests - PID 1 signal
  disposition and container runtime behavior are not reproducible
  outside a running container.
- That Alpine's coreutils `timeout` (musl libc) forwards a received
  signal to its child exactly like the GNU coreutils build used here
  (verified by hand against the local `timeout` binary, glibc/macOS
  build) - the mechanism is documented GNU coreutils behavior and the
  Alpine package is the same upstream project, but that is inference,
  not a test result.
- Anything about update-once.sh itself (its health-check waits, its
  rollback path, its actual runtime under real load) - it does not exist
  yet; this task only exercises entrypoint.sh's loop around a stand-in.
- The actual `timeout ... & ; child_pid=$!` race (two statements, a
  signal landing in the gap between them finds child_pid still empty).
  It cannot be reproduced by racing real process scheduling from outside
  the shell: whatever we spawn to send the signal back has to be forked
  and exec'd first, while the parent's next statement is a plain
  variable assignment with no syscall in it - the assignment has
  effectively always already happened by the time an external signal
  could arrive. Reliably hitting that gap requires slowing the
  production script down, which is a debugging trick for a scratch
  copy, not something to ship. See the test below for what is
  deterministically testable instead: the fix's actual mechanism, given
  the precondition the race produces.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "updater" / "entrypoint.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("timeout") is None,
    reason="entrypoint.sh requires GNU coreutils `timeout`, as the image does",
)


def _script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _run(
    tmp_path: Path, worker: Path, path_prefix: Path | None = None, **env_overrides: str
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "WORKER": str(worker), "LOOP_ONCE": "1", **env_overrides}
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}:{env['PATH']}"
    return subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )


def _fake_timeout_logging_to(log: Path, tmp_path: Path) -> None:
    """A stand-in `timeout` that records what it was called with and then
    behaves like the real one for a command that exits immediately: run
    the given command and exit with its status. Used only to observe the
    invocation - actual enforcement of the limit is tested separately
    against the real `timeout` binary, since a stub cannot honestly prove
    that."""
    _script(
        tmp_path,
        "timeout",
        f'printf "%s\\n" "$*" >> "{log}"\nduration="$1"; shift\nexec "$@"\n',
    )


def test_worker_runs_under_timeout_with_the_default_limit(tmp_path: Path) -> None:
    """No WORKER_TIMEOUT_SECONDS override -> the 600s default from the
    comment in entrypoint.sh is the one actually passed to `timeout`."""
    log = tmp_path / "timeout-calls.log"
    _fake_timeout_logging_to(log, tmp_path)
    worker = _script(tmp_path, "worker.sh", "exit 0")

    result = _run(tmp_path, worker, path_prefix=tmp_path)

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert calls.startswith(f"600 {worker}"), calls


def test_worker_runs_under_timeout_with_a_configured_limit(tmp_path: Path) -> None:
    """WORKER_TIMEOUT_SECONDS is not just read - it is the value `timeout`
    is actually invoked with."""
    log = tmp_path / "timeout-calls.log"
    _fake_timeout_logging_to(log, tmp_path)
    worker = _script(tmp_path, "worker.sh", "exit 0")

    result = _run(tmp_path, worker, path_prefix=tmp_path, WORKER_TIMEOUT_SECONDS="45")

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8")
    assert calls.startswith(f"45 {worker}"), calls


def test_a_timed_out_worker_is_logged_and_does_not_end_the_loop(tmp_path: Path) -> None:
    """A worker that hangs past WORKER_TIMEOUT_SECONDS is killed by the
    real `timeout` (no stub - this exercises the actual binary the image
    ships), the fact that it timed out is reported on stderr, and the
    entrypoint process itself still exits cleanly rather than hanging or
    crashing - a hang must be visible, not silent, but it still must not
    take the sidecar down with it."""
    worker = _script(tmp_path, "worker.sh", "sleep 30")

    started = time.monotonic()
    result = _run(tmp_path, worker, WORKER_TIMEOUT_SECONDS="1")
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < 10, f"took {elapsed}s - timeout does not seem to have fired"
    assert "exceeded 1s" in result.stderr, result.stderr


def test_a_failing_worker_does_not_end_the_loop(tmp_path: Path) -> None:
    """update-once.sh exiting non-zero (a real failure, not a timeout) must
    not be treated as fatal - this container is the only thing left to
    report a broken state, and a sidecar that dies on the first failed
    pass cannot do that."""
    worker = _script(tmp_path, "worker.sh", "exit 7")

    result = _run(tmp_path, worker, WORKER_TIMEOUT_SECONDS="5")

    assert result.returncode == 0, result.stderr
    assert "exceeded" not in result.stderr, result.stderr


def test_the_loop_keeps_running_across_repeated_failures(tmp_path: Path) -> None:
    """Beyond a single pass: without LOOP_ONCE, the loop actually comes
    back around and invokes the worker again after a failed pass, rather
    than the single-pass tests above coincidentally passing because
    nothing ever tried a second time."""
    counter = tmp_path / "count"
    counter.write_text("", encoding="utf-8")
    worker = _script(tmp_path, "worker.sh", f'printf "x" >> "{counter}"\nexit 1\n')

    env = {**os.environ, "WORKER": str(worker), "WORKER_TIMEOUT_SECONDS": "5"}
    env.pop("LOOP_ONCE", None)
    proc = subprocess.Popen(
        ["sh", str(SCRIPT)],
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 15
        while len(counter.read_text(encoding="utf-8")) < 2 and time.monotonic() < deadline:
            time.sleep(0.1)
        passes_seen = len(counter.read_text(encoding="utf-8"))
        assert passes_seen >= 2, "loop did not survive a second pass after the first one failed"
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)


def test_sigterm_is_forwarded_to_the_running_worker(tmp_path: Path) -> None:
    """The whole point of Finding 2's signal handling: entrypoint.sh runs
    as PID 1 in the container, which gets no default signal disposition -
    without the trap this test exercises, `docker stop` would do nothing
    to a mid-run worker until the runtime's grace period expired and
    SIGKILL landed on both processes uncoordinated. Here the worker
    installs its own TERM trap and records that it was reached; if the
    entrypoint did not forward the signal, this file would stay empty and
    the process would only die once pytest's own subprocess timeout, far
    longer than a fast local test, forced it."""
    received = tmp_path / "received-term"
    worker = _script(
        tmp_path,
        "worker.sh",
        f"trap 'printf x >> \"{received}\"; exit 143' TERM\n"
        f'printf x >> "{tmp_path / "started"}"\n'
        "sleep 30 &\n"
        "wait $!\n",
    )

    env = {**os.environ, "WORKER": str(worker), "WORKER_TIMEOUT_SECONDS": "60"}
    env.pop("LOOP_ONCE", None)
    proc = subprocess.Popen(
        ["sh", str(SCRIPT)],
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        started = tmp_path / "started"
        deadline = time.monotonic() + 10
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert started.exists(), "worker never started"

        before = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        elapsed = time.monotonic() - before
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    assert elapsed < 10, (
        f"entrypoint took {elapsed}s to exit after SIGTERM - signal was not forwarded promptly"
    )
    assert received.exists(), (
        "worker's own TERM trap never fired - the signal was not forwarded to it"
    )


def _read_late_forward_line() -> str:
    """Pull the late-forward-signal line straight out of entrypoint.sh
    instead of retyping it, so this test fails loudly - marker not found
    - rather than quietly testing a stale copy if that line ever moves
    or changes."""
    text = SCRIPT.read_text(encoding="utf-8")
    marker = '[ "$terminated" -eq 1 ] && kill -TERM "$child_pid" 2>/dev/null'
    assert marker in text, "late-forward-signal line not found in entrypoint.sh"
    return marker


def test_late_forward_line_signals_an_already_known_pid(tmp_path: Path) -> None:
    """Covers what the real `timeout ... & ; child_pid=$!` race is NOT
    reproducible for in a test (see the module docstring): the fix's
    actual mechanism. The race leaves the entrypoint with terminated=1
    already set at the moment child_pid finally becomes known. This
    drives that exact precondition directly - terminated=1, child_pid
    pointing at a real running process - and runs the literal
    late-forward line extracted from entrypoint.sh against it, to
    confirm it does what the fix claims: deliver TERM to that PID.

    This does not prove the two-statement gap is ever hit in practice
    (it cannot honestly be timed into existence from outside the
    shell) - it proves that if it is hit, the recovery line actually
    recovers, rather than being dead code beside a trap that looks like
    it already does the same job."""
    line = _read_late_forward_line()
    received = tmp_path / "received-term"
    ready = tmp_path / "trap-ready"
    target_script = (
        f"trap 'printf x >> \"{received}\"; exit 0' TERM\n"
        f'printf x >> "{ready}"\n'
        "sleep 30 &\n"
        "wait $!\n"
    )
    target = subprocess.Popen(
        ["sh", "-c", target_script],
        start_new_session=True,
    )
    try:
        # Wait for confirmation that the trap is actually installed
        # before signaling - a TERM arriving before the `trap` builtin
        # has run would just kill the target outright (default
        # disposition) and prove nothing about the line under test.
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "target process never installed its TERM trap"

        subprocess.run(
            ["sh", "-c", f"terminated=1; child_pid={target.pid}; {line}"],
            check=True,
            timeout=5,
        )
        target.wait(timeout=5)
    finally:
        if target.poll() is None:
            target.kill()
            target.wait(timeout=5)

    assert received.exists(), (
        "late-forward line did not deliver TERM to a PID already known "
        "when terminated was already set"
    )


# ---------------------------------------------------------------------------
# The one-time digest resolution (review fix, "the comparison"):
# entrypoint.sh now resolves its own container's image digest exactly
# ONCE, before the poll loop ever starts $WORKER, and exports it so
# update-once.sh reads it as a plain environment variable - see that
# script's own comment for why it never resolves this itself (a docker
# call on every pass, including one that only rejects a malformed
# request, would weaken tests/test_updater_script.py's own "no docker
# call at all" proof).
# ---------------------------------------------------------------------------


def _fake_docker(
    tmp_path: Path,
    *,
    image_id: str = "sha256:fakeimage0000",
    repo_digest_line: str | None = "ghcr.io/lucienkerl/loxmatter-updater@sha256:" + "a" * 64,
    mount_destination: str | None = "/repo",
    mount_source: str | None = "/home/pi/matter-loxone",
) -> None:
    """A fake `docker` answering the two `docker inspect loxmatter-updater`
    shapes entrypoint.sh makes (distinguished, like update-once.sh's own
    stub, by whether the `--format` argument mentions "Mounts") plus the
    THIRD one against the resolved image id - mirroring
    `tests/test_updater_script.py::_docker_stub_source`'s own approach for
    the sidecar's per-pass calls, just for entrypoint.sh's one-time ones.

    `repo_digest_line`/`mount_destination` as `None` model "nothing to
    report" - an image with no `RepoDigests` entry (built locally), or a
    mount table that does not cover $LOXMATTER_STACK at all."""
    digest_case = f'printf "%s\\n" "{repo_digest_line}"' if repo_digest_line else ":"
    mount_case = (
        f'printf "%s %s\\n" "{mount_destination}" "{mount_source}"' if mount_destination else ":"
    )
    _script(
        tmp_path,
        "docker",
        f"""
case "$1" in
  inspect)
    case "$2" in
      loxmatter-updater)
        case "$*" in
          *Mounts*) {mount_case} ;;
          *) printf "%s\\n" "{image_id}" ;;
        esac
        ;;
      {image_id})
        {digest_case}
        ;;
    esac
    ;;
esac
""",
    )


def _dump_worker(tmp_path: Path, log: Path) -> Path:
    """A worker that writes out the env var(s) entrypoint.sh is supposed
    to have resolved and exported, so a test can read what $WORKER
    actually saw - the one thing that matters here, since update-once.sh
    itself just reads these as plain environment variables (see its own
    comment)."""
    return _script(
        tmp_path,
        "worker.sh",
        f'printf "DIGEST=%s\\n" "$LOXMATTER_UPDATER_DIGEST" > "{log}"\n'
        f'printf "STACK_HOST_PATH=%s\\n" "$LOXMATTER_STACK_HOST_PATH" >> "{log}"\n'
        "exit 0\n",
    )


def test_the_digest_is_resolved_once_and_exported(tmp_path: Path) -> None:
    """The normal case: an image that was actually pulled (a `RepoDigests`
    entry exists) - the digest reaches the worker as a plain environment
    variable."""
    _fake_docker(tmp_path)
    log = tmp_path / "worker-env.log"
    worker = _dump_worker(tmp_path, log)

    result = _run(tmp_path, worker, path_prefix=tmp_path)

    assert result.returncode == 0, result.stderr
    seen = log.read_text(encoding="utf-8")
    assert f"DIGEST=sha256:{'a' * 64}" in seen


def test_the_digest_is_unknown_for_a_locally_built_image(tmp_path: Path) -> None:
    """`docker inspect <image>` answering an empty `RepoDigests` (a
    `docker build`, not a pull) must leave `LOXMATTER_UPDATER_DIGEST`
    empty - "unknown", never a fabricated value."""
    _fake_docker(tmp_path, repo_digest_line=None)
    log = tmp_path / "worker-env.log"
    worker = _dump_worker(tmp_path, log)

    result = _run(tmp_path, worker, path_prefix=tmp_path)

    assert result.returncode == 0, result.stderr
    seen = log.read_text(encoding="utf-8")
    assert "DIGEST=\n" in seen


def test_the_digest_resolution_makes_no_call_when_docker_is_unreachable(tmp_path: Path) -> None:
    """`docker` itself failing outright (daemon down, no such container
    yet - a fresh install.sh run this early in its own bootstrap) must not
    take entrypoint.sh down with it under its own `set -u`: the digest
    simply stays empty and the worker still starts."""
    _script(tmp_path, "docker", "exit 1\n")
    log = tmp_path / "worker-env.log"
    worker = _dump_worker(tmp_path, log)

    result = _run(tmp_path, worker, path_prefix=tmp_path)

    assert result.returncode == 0, result.stderr
    seen = log.read_text(encoding="utf-8")
    assert "DIGEST=\n" in seen
    assert "STACK_HOST_PATH=\n" in seen
