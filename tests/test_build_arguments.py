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

"""The CI and the Dockerfile must agree on the same four arguments.

This is deliberately NOT a test that just claims four lines exist in the
Dockerfile - such a test would be true as soon as someone types the names,
and would stay true if the CI then passes different ones. What is checked
is agreement between both files, which is exactly the error that would
only appear at image time: a `--build-arg` that the Dockerfile does not
know will be SILENTLY discarded by Docker (only a warning), and
the image will then have an empty version.

The third test covers the third source: the CI reads the
schema version with a grep from store.py. If the line's format changes there,
the grep returns empty - and the test fails here,
not only on the next update on a foreign Pi."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from loxmatter.model.store import schema_version

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

# The same format that the grep call in the CI uses. Both
# stand deliberately side-by-side: the test is only worth something if it
# checks the same pattern that the CI actually applies.
SCHEMA_PATTERN = r"^_SCHEMA_VERSION = ([0-9]+)$"


def _build_push_step() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["image"]["steps"]:
        if str(step.get("uses", "")).startswith("docker/build-push-action"):
            return step
    raise AssertionError("No docker/build-push-action step in job 'image'")


def test_the_ci_passes_exactly_the_arguments_the_dockerfile_knows() -> None:
    declared = set(
        re.findall(
            r"^ARG\s+([A-Z_][A-Z0-9_]*)", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE
        )
    )
    passed = {
        line.split("=", 1)[0].strip()
        for line in _build_push_step()["with"]["build-args"].strip().splitlines()
        if line.strip()
    }
    assert passed == declared


def test_both_architectures_are_built() -> None:
    # The Pi is the normal case for this project, not the exception. If
    # arm64 is missing, nobody notices until a user reads "no matching manifest".
    platforms = _build_push_step()["with"]["platforms"]
    assert "linux/arm64" in platforms
    assert "linux/amd64" in platforms


def test_the_ci_grep_finds_the_schema_version() -> None:
    store_source = (ROOT / "src" / "loxmatter" / "model" / "store.py").read_text(encoding="utf-8")
    found = re.findall(SCHEMA_PATTERN, store_source, re.MULTILINE)
    assert len(found) == 1, "exactly one line must match, or the grep misses"
    assert int(found[0]) == schema_version()


def test_the_ci_uses_exactly_this_pattern() -> None:
    workflow_source = WORKFLOW.read_text(encoding="utf-8")
    assert SCHEMA_PATTERN.strip("^$") in workflow_source
