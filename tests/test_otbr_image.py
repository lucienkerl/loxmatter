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

"""The pinned OpenThread border router image (design "Thread setup that
needs no handwork after one updater refresh", 2026-09-14, section 3).

deploy/otbr/source.env is the one place the commit, the build option and
the resulting tag are written down; the compose file's default and the
build workflow both have to agree with it, or a release can point at an
image the workflow never actually builds. These tests parse the plain env
file and the two YAML files with the same approach
tests/test_compose_profiles.py and tests/test_build_arguments.py already
use - no part of this is executed, only read.

PyYAML's safe_load resolves a bare `on:` key to the boolean `True` (a YAML
1.1 legacy this project's other workflow tests already work around) - every
job dict below is therefore reached through `workflow["jobs"]`, and the
trigger block through `workflow[True]`.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCE_ENV = ROOT / "deploy" / "otbr" / "source.env"
COMPOSE = ROOT / "deploy" / "testhost" / "docker-compose.yml"
OTBR_WORKFLOW = ROOT / ".github" / "workflows" / "otbr-image.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _source_env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in SOURCE_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key] = value
    return values


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _otbr_workflow() -> dict:
    return yaml.safe_load(OTBR_WORKFLOW.read_text(encoding="utf-8"))


def _ci_workflow() -> dict:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def _steps(job: dict) -> list[dict]:
    return job["steps"]


def _step_run_text(job: dict) -> str:
    """Every step's `run:` block of a job, concatenated - the simplest way
    to assert a shell fragment appears somewhere in a job without pinning
    down exactly which step it is in."""
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


# --- deploy/otbr/source.env -------------------------------------------------


def test_source_env_has_the_three_keys() -> None:
    values = _source_env()
    assert set(values) == {"OT_BR_POSIX_COMMIT", "OTBR_OPTIONS", "OTBR_IMAGE_TAG"}


def test_the_commit_is_forty_hex_characters() -> None:
    commit = _source_env()["OT_BR_POSIX_COMMIT"]
    assert _COMMIT_PATTERN.match(commit), commit


def test_the_image_tag_starts_with_the_short_commit() -> None:
    values = _source_env()
    assert values["OTBR_IMAGE_TAG"].startswith(values["OT_BR_POSIX_COMMIT"][:8])


def test_the_rcp_restoration_option_is_set() -> None:
    # This is the entire point of building instead of using the official
    # image (design section 3) - a change here would silently ship an otbr
    # image without the property the rest of this design relies on.
    assert _source_env()["OTBR_OPTIONS"] == "-DOT_RCP_RESTORATION_MAX_COUNT=2"


# --- Compose default ---------------------------------------------------------


def test_the_compose_default_matches_source_env() -> None:
    """Fault to prove it: change the tag in the compose file's otbr `image:`
    line without touching deploy/otbr/source.env."""
    tag = _source_env()["OTBR_IMAGE_TAG"]
    image = _compose()["services"]["otbr"]["image"]
    assert image == f"${{OTBR_IMAGE:-ghcr.io/lucienkerl/loxmatter-otbr:{tag}}}"


# --- .github/workflows/otbr-image.yml ---------------------------------------


def test_triggers_are_dispatch_and_push_to_main_on_the_right_paths() -> None:
    workflow = _otbr_workflow()
    triggers = workflow[True]  # "on:" - see module docstring
    assert "workflow_dispatch" in triggers
    push = triggers["push"]
    assert push["branches"] == ["main"]
    assert set(push["paths"]) == {"deploy/otbr/**", ".github/workflows/otbr-image.yml"}


def test_has_write_access_to_packages() -> None:
    assert _otbr_workflow()["permissions"]["packages"] == "write"


def test_reads_source_env() -> None:
    workflow = _otbr_workflow()
    for job in workflow["jobs"].values():
        text = _step_run_text(job)
        if "deploy/otbr/source.env" in text:
            return
    raise AssertionError("no job reads deploy/otbr/source.env")


def test_an_existence_check_can_skip_the_build() -> None:
    """Tags are immutable (spec 3.2): a workflow rerun, or a push that only
    touches an unrelated file under deploy/otbr/**, must not try to rebuild
    a tag that already exists.

    Fault to prove it: change the `check` job's `exists` output to always
    `false`, or drop the build/merge jobs' `if: needs.check.outputs.exists
    != 'true'`."""
    workflow = _otbr_workflow()
    jobs = workflow["jobs"]
    assert "imagetools inspect" in _step_run_text(jobs["check"])
    for name in ("build", "merge"):
        condition = str(jobs[name]["if"])
        assert "needs.check.outputs.exists" in condition


def test_the_build_matrix_covers_both_native_architectures() -> None:
    build = _otbr_workflow()["jobs"]["build"]
    entries = build["strategy"]["matrix"]["include"]
    seen = {(entry["os"], entry["platform"]) for entry in entries}
    assert ("ubuntu-24.04", "linux/amd64") in seen
    assert ("ubuntu-24.04-arm", "linux/arm64") in seen


def test_the_build_uses_the_pinned_dockerfile_and_base_image() -> None:
    text = _step_run_text(_otbr_workflow()["jobs"]["build"])
    assert "-f etc/docker/test/Dockerfile" in text
    assert "BASE_IMAGE=ubuntu:bionic" in text
    assert "OTBR_OPTIONS=" in text


def test_the_build_checks_out_ot_br_posix_with_submodules() -> None:
    text = _step_run_text(_otbr_workflow()["jobs"]["build"])
    assert "git clone https://github.com/openthread/ot-br-posix.git" in text
    assert "git submodule update --init --recursive" in text


def test_the_build_verifies_rcp_restoration_is_compiled_in() -> None:
    """Fault to prove it: drop this step - a build with a broken
    OTBR_OPTIONS would then still be pushed and tagged as the RCP-restoring
    image the compose default and the design both promise."""
    text = _step_run_text(_otbr_workflow()["jobs"]["build"])
    assert "Trying to recover" in text
    assert "/usr/sbin/otbr-agent" in text


def test_the_build_pushes_by_digest() -> None:
    text = _step_run_text(_otbr_workflow()["jobs"]["build"])
    assert "push-by-digest=true" in text


def test_a_merge_job_creates_the_multi_arch_tag() -> None:
    text = _step_run_text(_otbr_workflow()["jobs"]["merge"])
    assert "imagetools create" in text


# --- .github/workflows/ci.yml release gate -----------------------------------


def _pushes_an_image(job: dict) -> bool:
    return any(
        str(step.get("uses", "")).startswith("docker/build-push-action") for step in _steps(job)
    )


def _tag_gated_push_jobs(ci: dict) -> dict[str, dict]:
    """Jobs that both run only on a version tag and actually push an image -
    the ones the release gate exists to protect. Excludes a gate job that
    happens to be tag-gated itself but pushes nothing."""
    return {
        name: job
        for name, job in ci["jobs"].items()
        if "startsWith(github.ref, 'refs/tags/v')" in str(job.get("if", ""))
        and _pushes_an_image(job)
    }


def _gate_job_names(ci: dict) -> set[str]:
    return {name for name, job in ci["jobs"].items() if "imagetools inspect" in _step_run_text(job)}


def test_a_tag_release_checks_the_compose_default_image_exists() -> None:
    """Design section 3.3: a version can never point at an otbr image
    nobody built. The check must run before any image push step, in a job
    the tag-gated image jobs depend on - or, equivalently, at the top of
    each of those jobs themselves.

    Fault to prove it: remove `otbr-image-check` (or its `needs`/`if` wiring
    into `image`/`updater-image`) from ci.yml."""
    ci = _ci_workflow()
    push_jobs = _tag_gated_push_jobs(ci)
    assert push_jobs, "no job in ci.yml is gated on a version tag and pushes an image"

    gate_job_names = _gate_job_names(ci)
    assert gate_job_names, "no job in ci.yml inspects the compose default image"

    for name, job in push_jobs.items():
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        has_own_check = "imagetools inspect" in _step_run_text(job)
        depends_on_gate = bool(gate_job_names.intersection(needs))
        assert has_own_check or depends_on_gate, name


def test_the_release_gate_runs_before_any_image_is_pushed() -> None:
    """A gate job whose result a tag-gated image job never actually waits
    on (no `needs`, or an `if:` that does not read its result) would not
    stop that image job's own push step from running anyway."""
    ci = _ci_workflow()
    push_jobs = _tag_gated_push_jobs(ci)
    gate_names = _gate_job_names(ci) - set(push_jobs)
    assert gate_names, "no dedicated gate job found (see the other test if it runs inline)"

    for name, job in push_jobs.items():
        if "imagetools inspect" in _step_run_text(job):
            continue  # runs its own check inline - nothing to wait on
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        assert gate_names.intersection(needs), name
        # The gate's own result must be read: `needs.test.result` alone
        # also contains "needs.", and with `!cancelled()` in the condition
        # nothing else would stop a failed gate from letting the push run.
        condition = str(job.get("if", ""))
        assert any(f"needs.{gate}.result" in condition for gate in gate_names), name


def test_the_main_image_job_still_runs_when_the_tag_gate_is_skipped() -> None:
    """On a push to main `otbr-image-check` is skipped. An `if:` without a
    status function gets an implicit `success()`, which is false once any
    `needs` job was skipped - the `image` job would be skipped on main as
    well, and the :dev image the test Pi updates from would stop being
    built without any red run to show it. `!cancelled()` replaces the
    implicit check, and `test` then has to be required by name.

    Fault to prove it: drop `!cancelled() &&` from the `image` job's `if:`."""
    image = _ci_workflow()["jobs"]["image"]
    condition = str(image.get("if", ""))
    assert "!cancelled()" in condition or "always()" in condition, condition
    assert "needs.test.result == 'success'" in condition, condition


def test_the_release_gate_checks_the_image_the_way_a_pi_pulls_it() -> None:
    """A Pi pulls the otbr image anonymously. A gate that logs in first
    would pass for a package still private, and every fresh Thread
    installation of that release would fail to start.

    Fault to prove it: add a docker/login-action step to otbr-image-check."""
    job = _ci_workflow()["jobs"]["otbr-image-check"]
    uses = [str(step.get("uses", "")) for step in job.get("steps", [])]
    assert not any(u.startswith("docker/login-action") for u in uses), uses
    assert "imagetools inspect" in _step_run_text(job)
