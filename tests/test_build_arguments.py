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


UPDATER_DOCKERFILE = ROOT / "deploy" / "updater" / "Dockerfile"


def _build_push_step(job: str) -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for step in workflow["jobs"][job]["steps"]:
        if str(step.get("uses", "")).startswith("docker/build-push-action"):
            return step
    raise AssertionError(f"No docker/build-push-action step in job '{job}'")


def test_the_ci_passes_exactly_the_arguments_the_dockerfile_knows() -> None:
    declared = set(
        re.findall(
            r"^ARG\s+([A-Z_][A-Z0-9_]*)", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE
        )
    )
    passed = {
        line.split("=", 1)[0].strip()
        for line in _build_push_step("image")["with"]["build-args"].strip().splitlines()
        if line.strip()
    }
    assert passed == declared


def test_both_architectures_are_built() -> None:
    # The Pi is the normal case for this project, not the exception. If
    # arm64 is missing, nobody notices until a user reads "no matching manifest".
    platforms = _build_push_step("image")["with"]["platforms"]
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


# The updater-image job's own build-args (deploy/updater/Dockerfile, added
# alongside the sidecar's own LOXMATTER_UPDATER_VERSION field, mirroring
# the bridge's LOXMATTER_VERSION). Until this job passed no build-args at
# all, its own `ARG LOXMATTER_UPDATER_VERSION=dev` default silently applied
# to every published image regardless of which release it actually was -
# exactly the same class of drift `test_the_ci_passes_exactly_the_
# arguments_the_dockerfile_knows` above already guards for the bridge's
# own Dockerfile, just never checked for this one.
def test_the_updater_ci_passes_exactly_the_arguments_the_updater_dockerfile_knows() -> None:
    declared = set(
        re.findall(
            r"^ARG\s+([A-Z_][A-Z0-9_]*)",
            UPDATER_DOCKERFILE.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    passed = {
        line.split("=", 1)[0].strip()
        for line in _build_push_step("updater-image")["with"]["build-args"].strip().splitlines()
        if line.strip()
    }
    assert passed == declared


def test_the_updater_image_is_also_built_for_both_architectures() -> None:
    platforms = _build_push_step("updater-image")["with"]["platforms"]
    assert "linux/arm64" in platforms
    assert "linux/amd64" in platforms


def test_the_updater_version_strips_the_leading_v_the_same_way_the_bridge_does() -> None:
    """The two jobs must derive `version` the identical way, or a running
    bridge and its own sidecar could never be compared without first
    normalising one side - exactly the "make the version you bake in
    consistent with the bridge's" requirement this field exists to meet.

    Checked as a literal source match, not by executing either shell
    script: `${GITHUB_REF#refs/tags/v}` is the exact expression the
    `image` job's own `meta` step uses for its stable-channel `version`
    output (see its own `case "$GITHUB_REF" in refs/tags/v*) ...` arm) -
    the `updater-image` job's own `if:` already guarantees `github.ref` is
    always `refs/tags/v...` when this step runs, so it needs no equivalent
    `case` of its own, only the same strip.
    """
    workflow_source = WORKFLOW.read_text(encoding="utf-8")
    assert 'version="${GITHUB_REF#refs/tags/v}"' in workflow_source
    assert 'echo "version=${GITHUB_REF#refs/tags/v}"' in workflow_source
