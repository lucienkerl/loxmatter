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
"""Behavioral tests for deploy/updater/watchdog-once.sh - design "Thread
setup without handwork" (2026-09-14), section 4.2.

Same approach as test_updater_entrypoint.py: run the real script against a
throwaway $LOXMATTER_WATCHDOG_SCRIPT and $LOXMATTER_UPDATE_DIR, and check
what it actually did, not what its source text says. The script itself
only ever reads a marker line, runs `bash` and trims a log file - all
three are exercised end to end here rather than mocked.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "updater" / "watchdog-once.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="watchdog-once.sh runs the watchdog script with bash, as the image does",
)


def _watchdog_script(tmp_path: Path, name: str, body: str, *, marker: bool = True) -> Path:
    marker_line = "# loxmatter-watchdog: container-ready\n" if marker else ""
    path = tmp_path / name
    path.write_text(f"#!/usr/bin/env bash\n{marker_line}{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _run(
    tmp_path: Path, script: Path, update_dir: Path, **env_overrides: str
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "LOXMATTER_WATCHDOG_SCRIPT": str(script),
        "LOXMATTER_UPDATE_DIR": str(update_dir),
        **env_overrides,
    }
    return subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )


def test_a_script_without_the_marker_is_not_run(tmp_path: Path) -> None:
    """Fault to prove it: run the script regardless of the marker.

    Protects an old checkout after a bridge rollback (spec section 4.2):
    a pre-container-ready otbr-watchdog.sh reads /proc/net/if_inet6, which
    a container cannot see, and would restart otbr every minute."""
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    ran = tmp_path / "ran"
    script = _watchdog_script(tmp_path, "watchdog.sh", f'echo x >> "{ran}"', marker=False)

    result = _run(tmp_path, script, update_dir)

    assert result.returncode == 0, result.stderr
    assert not ran.exists()
    assert not (update_dir / "otbr-watchdog.log").exists()


def test_a_missing_script_exits_zero(tmp_path: Path) -> None:
    update_dir = tmp_path / "update"
    update_dir.mkdir()

    result = _run(tmp_path, tmp_path / "absent.sh", update_dir)

    assert result.returncode == 0, result.stderr
    assert not (update_dir / "otbr-watchdog.log").exists()


def test_a_script_with_the_marker_runs_with_bash_and_logs_stdout_and_stderr(
    tmp_path: Path,
) -> None:
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    script = _watchdog_script(
        tmp_path,
        "watchdog.sh",
        'echo "on stdout"\necho "on stderr" >&2\n[ -n "${BASH_VERSION:-}" ] || exit 9\n',
    )

    result = _run(tmp_path, script, update_dir)

    assert result.returncode == 0, result.stderr
    log = (update_dir / "otbr-watchdog.log").read_text(encoding="utf-8")
    assert "on stdout" in log
    assert "on stderr" in log


def test_the_log_is_trimmed_to_its_newest_2000_lines(tmp_path: Path) -> None:
    """A log of 2500 lines ends with 2000 lines, the newest kept."""
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    log = update_dir / "otbr-watchdog.log"
    log.write_text("".join(f"old-{i}\n" for i in range(2500)), encoding="utf-8")
    script = _watchdog_script(tmp_path, "watchdog.sh", "true")

    result = _run(tmp_path, script, update_dir)

    assert result.returncode == 0, result.stderr
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2000
    assert lines[0] == "old-500"
    assert lines[-1] == "old-2499"


def test_a_short_log_is_left_alone(tmp_path: Path) -> None:
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    script = _watchdog_script(tmp_path, "watchdog.sh", 'echo "hello"')

    result = _run(tmp_path, script, update_dir)

    assert result.returncode == 0, result.stderr
    lines = (update_dir / "otbr-watchdog.log").read_text(encoding="utf-8").splitlines()
    assert lines == ["hello"]


def test_a_term_to_the_worker_ends_the_watchdog_run(tmp_path: Path) -> None:
    """entrypoint.sh's `timeout` signals only its direct child, the worker.
    A watchdog run left behind would keep the lock and go on restarting
    otbr unseen, so the worker forwards the signal.

    Fault to prove it: drop the `trap` in watchdog-once.sh."""
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    alive = tmp_path / "alive"
    script = _watchdog_script(
        tmp_path, "watchdog.sh", f'for i in $(seq 1 100); do date +%s > "{alive}"; sleep 0.1; done'
    )
    env = {
        **os.environ,
        "LOXMATTER_WATCHDOG_SCRIPT": str(script),
        "LOXMATTER_UPDATE_DIR": str(update_dir),
        "LOXMATTER_WATCHDOG_POLL_SECONDS": "0.2",
    }
    worker = subprocess.Popen(["sh", str(SCRIPT)], env=env, cwd=str(tmp_path))
    try:
        for _ in range(50):
            if alive.exists():
                break
            time.sleep(0.1)
        assert alive.exists()
        worker.send_signal(signal.SIGTERM)
        worker.wait(timeout=10)
        time.sleep(0.5)
        stamp = alive.stat().st_mtime
        time.sleep(0.6)
        assert alive.stat().st_mtime == stamp, "the watchdog run outlived the worker"
    finally:
        worker.kill()


def test_the_heartbeats_stay_fresh_while_the_watchdog_runs(tmp_path: Path) -> None:
    """The bridge calls the updater outdated after 30 s without a heartbeat,
    and a restart run lasts longer than that. Both timestamps advance during
    the run, and nothing else in either file changes.

    Fault to prove it: drop the touch_seen_at calls from the wait loop."""
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    old = "2020-01-01T00:00:00Z"
    state = update_dir / "state.json"
    radios = update_dir / "radios-state.json"
    state.write_text(json.dumps({"phase": "idle", "updater_seen_at": old, "to": "x"}))
    radios.write_text(json.dumps({"phase": "done", "seen_at": old, "id": "job-1"}))
    script = _watchdog_script(tmp_path, "watchdog.sh", "sleep 1")

    result = _run(tmp_path, script, update_dir, LOXMATTER_WATCHDOG_POLL_SECONDS="0.2")

    assert result.returncode == 0, result.stderr
    after_state = json.loads(state.read_text())
    after_radios = json.loads(radios.read_text())
    assert after_state["updater_seen_at"] != old
    assert after_radios["seen_at"] != old
    assert after_state["to"] == "x" and after_state["phase"] == "idle"
    assert after_radios["id"] == "job-1" and after_radios["phase"] == "done"
