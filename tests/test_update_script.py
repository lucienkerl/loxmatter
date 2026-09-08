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

"""Behavior tests for scripts/update.sh.

Same procedure as in `test_install_script.py`: the script runs
against a sealed PATH of fake binaries, and what's tested is
WHICH commands it chooses - not what they do. A real
`docker compose pull` would be unreasonable in CI or on a
development machine.

The most important test below is `test_without_build_never_builds`: the
script always built before 0.2.0, and the whole point of this change is
that an update on a Pi no longer takes five to ten minutes.
A regressed `compose build` otherwise goes unnoticed - it
works, just slowly."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "update.sh"

SYSTEM_TOOLS = (
    "bash",
    "sh",
    "cat",
    "grep",
    "sed",
    "awk",
    "tr",
    "printf",
    "mkdir",
    "rm",
    "sleep",
    "date",
    "ls",
    "xargs",
    "tail",
    "seq",
    "hostname",
    "dirname",
)


@pytest.fixture
def sealed(tmp_path):
    """A PATH from two directories: fake tools and the
    real ones the script legitimately needs. Each stub logs its
    call to $STUB_LOG and exits successfully - `curl` additionally
    outputs a health response so the wait loop
    continues immediately instead of waiting 120 seconds."""
    bindir = tmp_path / "bin"
    sysdir = tmp_path / "sys"
    bindir.mkdir()
    sysdir.mkdir()
    log = tmp_path / "stub.log"

    def stub(name: str, body: str = "") -> None:
        path = bindir / name
        path.write_text(
            f'#!/bin/sh\nprintf "%s %s\\n" "{name}" "$*" >> "$STUB_LOG"\n{body}\n', encoding="utf-8"
        )
        path.chmod(0o755)

    # "docker run ... tar czf X ..." (the database backup) must actually
    # create the target file: the following `ls store-*.tgz` in the
    # script runs under `pipefail`, and without a match `ls` fails with
    # exit status 1 - the script would break before it ever got to pulling or
    # building. A stub that only logs would be too thin here.
    # The target path is hidden in the container behind `/backup/...` - the stub
    # looks for the `-v HOSTDIR:/backup` and writes back there.
    stub(
        "docker",
        'if [ "$1" = "run" ]; then\n'
        '  hostdir=""\n'
        '  for a in "$@"; do\n'
        '    case "$a" in *:/backup) hostdir="${a%:/backup}" ;; esac\n'
        "  done\n"
        '  prev=""\n'
        '  for a in "$@"; do\n'
        '    if [ "$prev" = "czf" ]; then\n'
        '      case "$a" in /backup/*) a="$hostdir/${a#/backup/}" ;; esac\n'
        '      : > "$a"\n'
        "    fi\n"
        '    prev="$a"\n'
        "  done\n"
        "fi\n",
    )
    stub("git")
    stub("curl", 'echo \'{"status":"ok"}\'')

    for tool in SYSTEM_TOOLS:
        real = subprocess.run(
            ["which", tool], capture_output=True, text=True, check=False
        ).stdout.strip()
        if real:
            (sysdir / tool).symlink_to(real)

    def run(*args: str):
        result = subprocess.run(
            [str(SCRIPT), *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{bindir}:{sysdir}",
                "STUB_LOG": str(log),
                "HOME": str(tmp_path),
            },
            check=False,
        )
        return result, log.read_text(encoding="utf-8") if log.exists() else ""

    return run


def test_it_pulls_the_image_instead_of_building_it(sealed):
    _, calls = sealed("--no-pull")
    assert "compose pull loxmatter" in calls


def test_without_build_never_builds(sealed):
    _, calls = sealed("--no-pull")
    assert "compose build" not in calls


def test_with_build_it_builds_and_does_not_pull(sealed):
    _, calls = sealed("--no-pull", "--build")
    assert "compose build loxmatter" in calls
    assert "compose pull loxmatter" not in calls


def test_pulling_comes_before_restarting(sealed):
    _, calls = sealed("--no-pull")
    assert calls.index("compose pull") < calls.index("compose up")


def test_restart_leaves_neighbor_services_alone(sealed):
    # --no-deps: OTBR's Thread state is tied to a volume, and restarting
    # the Thread network is not part of an update.
    _, calls = sealed("--no-pull")
    up_line = next(line for line in calls.splitlines() if "compose up" in line)
    assert "--no-deps" in up_line


def test_database_is_backed_up_before_everything_else(sealed):
    _, calls = sealed("--no-pull")
    assert calls.index("volume inspect") < calls.index("compose pull")


def test_no_cache_without_build_is_rejected(sealed):
    result, _ = sealed("--no-pull", "--no-cache")
    assert result.returncode != 0
    assert "--build" in result.stderr
