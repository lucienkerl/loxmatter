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

"""Behavioral tests for the sidecar.

Same approach as `test_install_script.py` and `test_update_script.py`: a
sealed PATH made of fake binaries, and what is checked is WHICH commands
the script chooses.

The tests around `test_a_target_containing_a_semicolon_*` are the core of this
file. They substantiate the claim from spec section 10 - "even someone
who fully takes over the bridge can at most install a published, newer
version". Without them that would just be an assertion. What matters here
is not only THAT the request is rejected, but that the call log shows NOT
A SINGLE docker call: a rejection that has already done something before
rejecting is not one."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "updater" / "update-once.sh"

SYSTEM_TOOLS = (
    "sh",
    "cat",
    "grep",
    "sed",
    "awk",
    "tr",
    "printf",
    "mkdir",
    "rm",
    "mv",
    "date",
    "sort",
    "head",
    "tail",
    "jq",
    "sleep",
    "seq",
    "ls",
    "cut",
)


@pytest.fixture
def updater(tmp_path):
    """Returns `run(**env)` -> (result, calls, state). `calls` is the log
    of every faked tool, `state` the written state as a dict (or None)."""
    bindir, sysdir = tmp_path / "bin", tmp_path / "sys"
    bindir.mkdir()
    sysdir.mkdir()
    log = tmp_path / "stub.log"
    update_dir = tmp_path / "data" / "update"
    update_dir.mkdir(parents=True)
    stack = tmp_path / "repo" / "deploy" / "testhost"
    stack.mkdir(parents=True)
    (stack / ".env").write_text("LOXMATTER_IMAGE_TAG=0.2.0\n", encoding="utf-8")

    def stub(name: str, body: str = "") -> None:
        path = bindir / name
        path.write_text(
            f'#!/bin/sh\nprintf "%s %s\\n" "{name}" "$*" >> "$STUB_LOG"\n{body}\n', encoding="utf-8"
        )
        path.chmod(0o755)

    # `running_version()` answers "what is running" via `docker inspect
    # <service> --format ...`. The fixture's .env pins
    # LOXMATTER_IMAGE_TAG=0.2.0 (see above) so this stub answers with the
    # same version: it plays the role of a freshly-installed 0.2.0
    # container that has not yet been updated - exactly the fixture the
    # forward-only tests below need to be able to tell "0.1.0" (older),
    # "0.2.0" (same) and "0.3.0" (newer) apart. The stub answers every
    # `docker inspect` call the same way regardless of arguments, which
    # is enough here since the script only ever inspects one service.
    stub("docker", 'case "$1" in\n  inspect) printf "LOXMATTER_VERSION=0.2.0\\n" ;;\nesac')
    stub("git")
    stub("curl", 'echo \'{"status":"ok"}\'')
    stub("tar")

    for tool in SYSTEM_TOOLS:
        real = subprocess.run(
            ["which", tool], capture_output=True, text=True, check=False
        ).stdout.strip()
        if real:
            (sysdir / tool).symlink_to(real)

    def run(**extra_env):
        env = {
            "PATH": f"{bindir}:{sysdir}",
            "STUB_LOG": str(log),
            "LOXMATTER_UPDATE_DIR": str(update_dir),
            "LOXMATTER_BACKUP_DIR": str(tmp_path / "data" / "backups"),
            "LOXMATTER_STACK": str(stack),
            "LOXMATTER_REPO": str(tmp_path / "repo"),
            "LOXMATTER_HEALTH_TIMEOUT": "3",
            **extra_env,
        }
        result = subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=env, check=False)
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
        state_file = update_dir / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else None
        return result, calls, state

    run.update_dir = update_dir
    run.stack = stack
    return run


def _auftrag(updater, **fields) -> None:
    body = {
        "id": "auftrag-1",
        "channel": "stable",
        "target": "0.3.0",
        "requested_at": "2026-09-08T10:00:00Z",
    }
    body.update(fields)
    (updater.update_dir / "request.json").write_text(json.dumps(body), encoding="utf-8")


def test_without_a_job_it_only_writes_a_heartbeat(updater):
    _, calls, state = updater()
    assert state["phase"] == "idle"
    assert state["updater_seen_at"]
    assert "docker" not in calls


def test_the_heartbeat_is_written_on_every_pass(updater):
    _, _, erst = updater()
    _, _, dann = updater()
    assert dann["updater_seen_at"] >= erst["updater_seen_at"]


def test_a_target_containing_a_semicolon_is_rejected(updater):
    _auftrag(updater, target="0.3.0; rm -rf /")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_a_target_with_a_foreign_registry_is_rejected(updater):
    # The image name is assembled inside the script, never taken over
    # verbatim. A target that looks like an image is therefore simply
    # not a valid target.
    _auftrag(updater, target="evil.example.com/loxmatter:latest")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_an_unknown_channel_is_rejected(updater):
    _auftrag(updater, channel="beliebig")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_an_older_version_is_rejected(updater):
    # "Forward only", spec section 10, rule 3. .env is at 0.2.0.
    _auftrag(updater, target="0.1.0")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_the_same_version_is_rejected(updater):
    _auftrag(updater, target="0.2.0")
    _, _, state = updater()
    assert state["phase"] == "rejected"


def test_a_valid_target_is_accepted(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, state = updater()
    assert state["phase"] != "rejected"
    assert "docker" in calls


def test_the_same_job_is_not_run_twice(updater):
    _auftrag(updater, target="0.3.0")
    updater()
    _, zweite_calls, _ = updater()
    assert "compose pull" not in zweite_calls
