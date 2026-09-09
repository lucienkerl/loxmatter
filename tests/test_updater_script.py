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

"""Behavioral tests for the sidecar.

Same approach as `test_install_script.py` and `test_update_script.py`: a
sealed PATH made of fake binaries, and what is checked is WHICH commands
the script chooses.

The tests around `test_a_target_containing_a_semicolon_*` and
`test_a_target_with_an_embedded_newline_*` are the core of this file. They
substantiate the claim from spec section 10 - "even someone who fully
takes over the bridge can at most install a published, newer version".
Without them that would just be an assertion. What matters here is not
only THAT the request is rejected, but that the call log shows NOT A
SINGLE docker call: a rejection that has already done something before
rejecting is not one. The newline-embedding tests exist because `grep
-Eq '^...$'` checks a LINE, not a VALUE - see the comment above the
newline `case` guard in update-once.sh for the full story."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from loxmatter.update import _TERMINAL_PHASES

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "updater" / "update-once.sh"

# Two tiers of rejection, not one. Rule 3 ("forward only") can only be
# checked against the version that is actually running, and the sole
# authoritative source for that is `docker inspect` on the live container
# (see running_version() in update-once.sh) - a read that changes nothing.
# So the two kinds of rejected request carry different guarantees:
#
#   * MALFORMED (bad channel, a target that fails the pattern, shell
#     metacharacters, an image reference): shape is checked before the
#     script ever calls out to docker, so these tests keep the blanket
#     `"docker" not in calls`. Do NOT loosen these to match the tier below -
#     they are the ones substantiating spec section 10's claim that a fully
#     compromised bridge can be validated without running anything.
#   * WELL-FORMED BUT REJECTED (right shape, wrong direction - too old or
#     unchanged): legitimately costs one read-only `docker inspect` before
#     the reject. What must still hold is that no MUTATING docker
#     subcommand runs - see `_mutating_docker_calls` below.
#
# The subcommand this script ever reads with is `inspect`; everything else
# it invokes (`compose pull`, `compose up -d ...`) changes host state. The
# allowlist is spelled out explicitly rather than matched by forbidding a
# substring like "pull" - that would also trip over an unrelated word
# appearing anywhere in the log.
READ_ONLY_DOCKER_SUBCOMMANDS = {"inspect"}


def _mutating_docker_calls(calls: str) -> list[str]:
    """Lines from the stub call log that invoke docker in a way that
    changes host state - i.e. every logged `docker ...` call whose
    subcommand is not on the read-only allowlist above."""
    mutating = []
    for line in calls.splitlines():
        parts = line.split()
        if not parts or parts[0] != "docker":
            continue
        subcommand = parts[1] if len(parts) > 1 else ""
        if subcommand not in READ_ONLY_DOCKER_SUBCOMMANDS:
            mutating.append(line)
    return mutating


def _is_compose_call(line: str, subcommand: str) -> bool:
    """True if `line` (one entry from the stub call log) invokes `docker
    compose <subcommand> ...` - tolerant of the `-f <container
    path>/docker-compose.yml --project-directory <host path>` flags
    compose() in update-once.sh now inserts between `compose` and the
    subcommand it runs (see that function's own comment for why: `-f`
    keeps the container path this sidecar can read, `--project-directory`
    carries the host path Compose must resolve every relative volumes:
    entry against - the fix for the "compose inside a container resolves
    relative bind mounts to container paths" finding). A bare substring
    check on e.g. "compose pull" does NOT survive that flag insertion -
    it is exactly the mistake an earlier round on this branch made,
    removing the flag again to keep such a check passing instead of
    fixing the check. Matching on the SUBCOMMAND as a whole TOKEN,
    wherever it falls among the flags, is what keeps assertions honest
    about what actually ran without caring where Compose's own options
    happen to sit."""
    parts = line.split()
    return (
        len(parts) >= 3
        and parts[0] == "docker"
        and parts[1] == "compose"
        and subcommand in parts[2:]
    )


def _compose_calls(calls: str, subcommand: str) -> list[str]:
    """Lines from the stub call log invoking `docker compose <subcommand>
    ...` - see _is_compose_call() above."""
    return [line for line in calls.splitlines() if _is_compose_call(line, subcommand)]


def _docker_stub_source(*, version: str = "0.2.0", compose_case: str = "exit 0 ;;") -> str:
    """Full source for a fake `docker` binary that answers BOTH `docker
    inspect` calls update-once.sh actually makes:

      * `docker inspect $SERVICE --format ...` - running_version(),
        answered with LOXMATTER_VERSION=<version>.
      * `docker inspect loxmatter-updater --format ...` - host_path_for(),
        hardcoded to that container name regardless of $SERVICE.
        Answered with an IDENTITY mount mapping: $LOXMATTER_STACK and
        $LOXMATTER_REPO each resolve to themselves. That is what lets
        compose() in update-once.sh resolve --project-directory at all in
        this fixture, where $LOXMATTER_STACK is already a real host
        directory - there is no actual container/host boundary being
        modelled here (the tests in the "host_path_for" section below
        model a REAL split deliberately, with their own docker stubs, not
        this one).

    Distinguished by $2 (the container name), not by the `--format`
    argument that follows it - both invocations pass `inspect` as $1.

    `compose_case` is spliced into the `compose)` arm for callers that
    need a specific `docker compose ...` call to fail; the default just
    lets it succeed, since most callers only care THAT the right compose
    call happened, not what it returns."""
    return (
        "#!/bin/sh\n"
        'printf "%s %s\\n" "docker" "$*" >> "$STUB_LOG"\n'
        'case "$1" in\n'
        "  inspect)\n"
        '    if [ "$2" = "loxmatter-updater" ]; then\n'
        '      printf "%s %s\\n" "$LOXMATTER_STACK" "$LOXMATTER_STACK"\n'
        '      printf "%s %s\\n" "$LOXMATTER_REPO" "$LOXMATTER_REPO"\n'
        "    else\n"
        f'      printf "LOXMATTER_VERSION={version}\\n"\n'
        "    fi\n"
        "    ;;\n"
        f"  compose) {compose_case}\n"
        "esac\n"
    )


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
    "cp",
    "date",
    "sort",
    "head",
    "tail",
    "jq",
    "sleep",
    "seq",
    "ls",
    "cut",
    "wc",
    "readlink",
    "chmod",
    # `stat` (REPO_UID/REPO_GID) and `chown` (chown_repo_back) - both new
    # with the git-ownership fix (see git_repo()/run_git() in
    # update-once.sh). Real binaries, not stubs: $REPO in this fixture is
    # a real directory owned by whoever runs the test, so `chown` back
    # onto it is always a legitimate no-op, and faking either would only
    # test the fake.
    "stat",
    "chown",
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
    # "0.2.0" (same) and "0.3.0" (newer) apart. `_docker_stub_source()`
    # (module level, above) also answers the OTHER `docker inspect` call
    # this script makes - `inspect loxmatter-updater` for
    # host_path_for() - with an identity mount mapping, which is what
    # lets compose() resolve --project-directory at all by default; see
    # that helper's own docstring.
    docker_path = bindir / "docker"
    docker_path.write_text(_docker_stub_source(), encoding="utf-8")
    docker_path.chmod(0o755)
    stub("git")
    stub("curl", 'echo \'{"status":"ok"}\'')
    stub("tar")

    for tool in SYSTEM_TOOLS:
        real = subprocess.run(
            ["which", tool], capture_output=True, text=True, check=False
        ).stdout.strip()
        if real:
            (sysdir / tool).symlink_to(real)

    def build_env(**extra_env):
        return {
            "PATH": f"{bindir}:{sysdir}",
            "STUB_LOG": str(log),
            "LOXMATTER_UPDATE_DIR": str(update_dir),
            "LOXMATTER_BACKUP_DIR": str(tmp_path / "data" / "backups"),
            "LOXMATTER_STACK": str(stack),
            "LOXMATTER_REPO": str(tmp_path / "repo"),
            "LOXMATTER_HEALTH_TIMEOUT": "3",
            # Off by default: most tests below never intend to exercise
            # self-replacement, and it defaults ON in production
            # ("${LOXMATTER_UPDATER_SELF_REPLACE:-1}" in update-once.sh).
            # Left on, a successful run here would add an unrelated
            # `compose up ... loxmatter-updater` line to every such test's
            # call log. Tests that specifically cover self-replacement
            # override this back to "1" via **extra_env.
            "LOXMATTER_UPDATER_SELF_REPLACE": "0",
            **extra_env,
        }

    def run(_timeout=None, **extra_env):
        env = build_env(**extra_env)
        result = subprocess.run(
            [str(SCRIPT)], capture_output=True, text=True, env=env, check=False, timeout=_timeout
        )
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
        state_file = update_dir / "state.json"
        # `.is_file()`, not `.exists()`: state.json can legitimately be a
        # directory in one of the tests below (write_state's own guard
        # against exactly that) - reading it as text would raise
        # IsADirectoryError before the test ever gets to its assertions.
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else None
        return result, calls, state

    def popen(**extra_env):
        # For tests that need to send the process a real signal (the
        # SIGTERM trap) while it is still mid-run - `run()` above only
        # ever returns after the process has already exited, which is
        # exactly the state a signal test needs to interrupt.
        return subprocess.Popen([str(SCRIPT)], env=build_env(**extra_env))

    run.update_dir = update_dir
    run.log_path = log
    run.popen = popen
    run.stack = stack
    run.bindir = bindir
    run.backup_dir = tmp_path / "data" / "backups"
    return run


def _write_request(updater, **fields) -> None:
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
    # `>=` on two live-clock reads cannot fail: `now()` is second-grained,
    # so two passes taken close together routinely land in the same
    # second, and a heartbeat that silently stopped updating (the jq
    # refresh at the top of the script failing open, say) would satisfy
    # `>=` just as well as a working one. Seed a timestamp that is
    # unambiguously in the past instead, and require the pass to move
    # strictly beyond it - a frozen heartbeat then fails on equality.
    alt = "2000-01-01T00:00:00Z"
    state_file = updater.update_dir / "state.json"
    state_file.write_text(
        json.dumps(
            {
                "id": None,
                "phase": "idle",
                "from": None,
                "to": None,
                "error": None,
                "rolled_back": False,
                "healthy": True,
                "updater_seen_at": alt,
            }
        ),
        encoding="utf-8",
    )
    _, _, state = updater()
    assert state["updater_seen_at"] > alt


def test_a_target_containing_a_semicolon_is_rejected(updater):
    _write_request(updater, target="0.3.0; rm -rf /")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_a_target_with_a_foreign_registry_is_rejected(updater):
    # The image name is assembled inside the script, never taken over
    # verbatim. A target that looks like an image is therefore simply
    # not a valid target.
    _write_request(updater, target="evil.example.com/loxmatter:latest")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


# `grep -Eq '^...$'` matches per LINE, not per VALUE - it is satisfied the
# moment ANY line of a multi-line subject matches, and `jq -r` turns a
# JSON string's `\n` escapes into real newlines. So each of the three
# payloads below used to sail through the very check meant to stop it:
# the first line looks like a valid target, and everything after the
# newline rode along into `to` in state.json - from where Tasks 3/4 would
# write it into $STACK/.env as LOXMATTER_IMAGE_TAG, a value that
# deploy/testhost/docker-compose.yml interpolates straight into `image:`
# of a `privileged: true` service. These three are exactly the payloads
# verified end to end against the unfixed script (all three reached
# `phase: queued`, and the first one forged a line in log.txt); they must
# now be rejected before a single docker call, same as any other
# malformed target.
def test_a_target_with_an_embedded_newline_is_rejected(updater):
    _write_request(updater, target="0.3.0\nrm -rf /")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_a_foreign_registry_target_with_an_embedded_newline_is_rejected(updater):
    _write_request(updater, target="evil.example.com/loxmatter:latest\n0.3.0")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_a_dev_target_with_an_embedded_newline_is_rejected(updater):
    _write_request(updater, channel="dev", target="abcdef1\n; wget evil")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


# The `dev` pattern check (`^[0-9a-f]{7,40}$`) is the only validation a
# dev-channel request meets in this half of the script - nothing else
# would notice if it were wrong, or missing, without these three.
def test_a_valid_dev_target_is_accepted(updater):
    # The dev channel's own forward-only equivalent (Task 3's ancestry
    # check) reads via `git`, not `docker inspect` - Rule 3's semver
    # comparison, the only thing that ever needed that read, applies to
    # the stable channel alone. Task 4 changed what this test may claim,
    # though: `docker inspect` is no longer stable-channel-exclusive.
    # $RUNNING is now computed for every request whose shape has already
    # passed validation (see the comment above `RUNNING="$(running_version)"`
    # in update-once.sh) - it is the honest rollback target if THIS
    # update fails later, dev channel included, and a dev-channel rollback
    # referencing an unset $RUNNING under `set -eu` would otherwise crash
    # instead of rolling back. So one `inspect` call is now expected here
    # too - and a SECOND one besides: compose()'s own host_path_for() fix
    # (the "compose inside a container resolves relative bind mounts to
    # container paths" finding) makes exactly one more `docker inspect
    # loxmatter-updater --format ...` call, cached for the rest of the
    # pass, on compose()'s first invocation (the pull, here). What this
    # test still pins down is that BOTH are read-only: never a mutating
    # `docker inspect`, and never more of them than these two expected
    # ones.
    _write_request(updater, channel="dev", target="abcdef1")
    _, calls, state = updater()
    assert state["phase"] == "done"
    assert calls.count("docker inspect") == 2


def test_a_malformed_dev_target_is_rejected(updater):
    _write_request(updater, channel="dev", target="not-a-commit-sha")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_an_unparseable_request_is_rejected(updater):
    # Invalid JSON used to be indistinguishable here from "no request at
    # all" - `jq -r '.id // empty'` fails, the failure is swallowed by
    # `|| true`, and an empty JOB_ID takes the same silent `exit 0` as
    # nothing-to-do. The bridge, polling this file for a phase change,
    # would then wait forever for an answer that was never coming.
    (updater.update_dir / "request.json").write_text("not valid json{", encoding="utf-8")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_a_request_with_an_empty_id_is_rejected(updater):
    _write_request(updater, id="")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


@pytest.mark.parametrize("evil_id", [".", ".."])
def test_a_dot_or_dotdot_id_is_rejected_not_silently_swallowed(updater, evil_id):
    # Found while implementing the Stufe-2 Important 6 fix (the durable
    # "handled/<job-id>" marker), not by the review itself: "." and ".."
    # each pass the id character class untouched (both are made up
    # entirely of "." and "-", characters the class already allows), but
    # once id is used as a PATH SEGMENT - the marker this test's own
    # fixture never sees directly - "handled/." names the handled/
    # directory ITSELF and "handled/.." names $LOXMATTER_UPDATE_DIR, both
    # of which always exist. Without the fix, `[ -e "$HANDLED_MARKER" ]`
    # for either is therefore ALWAYS true, and a request carrying one of
    # these ids would be silently treated as already-handled on every
    # single pass, forever - never even reaching this rejection, in
    # violation of this file's own "a request is not readable" doctrine
    # that a rejection must be recorded, not silently skipped.
    _write_request(updater, id=evil_id)
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert state["error"] == 'id must not be "." or ".."'
    assert "docker" not in calls


def test_an_unknown_channel_is_rejected(updater):
    _write_request(updater, channel="beliebig")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_an_older_version_is_rejected(updater):
    # "Forward only", spec section 10, rule 3. .env is at 0.2.0.
    #
    # This target is well-formed - it has to be, to test rule 3 at all -
    # so answering "is 0.1.0 newer than what's running" costs one
    # `docker inspect` (see running_version()). That call is legitimate;
    # what must not happen is anything that changes host state.
    _write_request(updater, target="0.1.0")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert _mutating_docker_calls(calls) == []


def test_the_same_version_is_rejected(updater):
    # Same reasoning as test_an_older_version_is_rejected above: a
    # well-formed target that ties the running version also needs the one
    # read-only `docker inspect` to know that it ties.
    _write_request(updater, target="0.2.0")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert _mutating_docker_calls(calls) == []


def test_a_valid_target_is_accepted(updater):
    _write_request(updater, target="0.3.0")
    _, calls, state = updater()
    assert state["phase"] != "rejected"
    assert "docker" in calls


def test_the_same_job_is_not_run_twice(updater):
    # Originally written against state alone, because the `compose pull`
    # it looked for did not exist yet (Task 3). Now it does, so the
    # property strengthens to what "not run twice" actually means: not
    # just that `to` in state.json is left alone, but that the mutating
    # half of the flow does not fire a second time for a request whose id
    # was already decided.
    #
    # Re-decide the SAME job id with a DIFFERENT (still well-formed,
    # still forward) target between the two passes. If the guard fires,
    # id "auftrag-1" is already recorded as decided and the second
    # request must never be looked at - `to` stays whatever the first
    # pass wrote, AND no second `compose pull` runs. The stub log is
    # cumulative across both `updater()` calls in this fixture (same
    # stub.log for the whole test), so a guard that quietly stopped
    # working would show up as a second "compose pull" occurrence, not
    # just a wrong `to`. Matched via `_compose_calls()` (module level,
    # above), not a bare substring - see its own docstring for why a
    # literal "compose pull" no longer appears now that compose() carries
    # `-f`/`--project-directory` between "compose" and the subcommand.
    _write_request(updater, target="0.3.0")
    _, first_calls, first_state = updater()
    assert first_state["phase"] == "done"
    assert first_state["to"] == "0.3.0"
    assert len(_compose_calls(first_calls, "pull")) == 1

    _write_request(updater, target="0.4.0")  # same id "auftrag-1", new target
    _, second_calls, second_state = updater()
    assert second_state["id"] == "auftrag-1"
    assert second_state["to"] == "0.3.0"
    assert len(_compose_calls(second_calls, "pull")) == 1


# ---------------------------------------------------------- Stufe 2 round --
# The tests below close the second security-review pass on this file (see
# .superpowers/sdd/task-2-stufe2-fix-report.md for the full write-up). One
# Critical (an oversized target/id silently emptying state.json through the
# same "discarded command-substitution exit status" bug the first round
# fixed only at the heartbeat call site), one Important on the heartbeat
# recovery guard accepting a non-state, one Important on id going through
# unvalidated, and four fixes from the FIRST round that had no test at all
# (each is called out at its own test below).


def test_an_oversized_target_is_rejected_without_corrupting_state(updater):
    # Critical. A target long enough blows jq's own execve inside
    # set_state - E2BIG - because `write_state "$(jq -n ... --arg to
    # "$TO" ...)"` (pre-fix) discarded the substitution's exit status: it
    # is an argument to write_state, not the command `set -e` watches.
    # Reproduced against the unfixed script: rc=0, a 1-byte state.json, on
    # three consecutive passes, at a measured threshold of 522996
    # characters on macOS (the Alpine sidecar's Linux MAX_ARG_STRLEN,
    # ~131072 bytes, is smaller still). This target is a well-formed-
    # looking "semver" ("0.3." followed by a run of zeros - the pattern
    # check alone would accept it) and sized well past the macOS
    # threshold, so it is the length cap specifically - not the pattern
    # check - that has to stop it here.
    huge_target = "0.3." + "0" * 600000
    _write_request(updater, target=huge_target)
    result, calls, state = updater()
    assert result.returncode == 0
    assert state is not None
    assert state["phase"] == "rejected"
    assert state["error"] == "target is too long"
    assert "docker" not in calls


def test_an_oversized_id_is_rejected(updater):
    # Critical, the id half: unbounded, an id reaches the exact same
    # set_state E2BIG failure the target-length test above exercises.
    _write_request(updater, id="x" * 200)
    result, calls, state = updater()
    assert result.returncode == 0
    assert state["phase"] == "rejected"
    assert state["error"] == "id is too long"
    assert state["id"] is None
    assert "docker" not in calls


def test_an_id_with_an_embedded_newline_does_not_forge_a_log_line(updater):
    # Important 2. state.json stays safe regardless (jq escapes --arg),
    # but log() interpolates the id verbatim into an audit line - so an
    # id containing a newline can forge an arbitrary extra line. Verified
    # against the unfixed script: this exact id produced a syntactically
    # perfect forged "accepted" line, and the request still reached
    # phase: queued. The log must contain ONLY the real rejection line.
    evil_id = "a1\n2026-01-01T00:00:00Z Request evil accepted: 0.2.0 -> 9.9.9 (stable)"
    _write_request(updater, id=evil_id)
    result, calls, state = updater()
    assert result.returncode == 0
    assert state["phase"] == "rejected"
    assert state["error"] == "id contains an invalid character"
    log_text = (updater.update_dir / "log.txt").read_text(encoding="utf-8")
    assert len(log_text.strip("\n").split("\n")) == 1
    assert "evil accepted" not in log_text
    assert "docker" not in calls


def test_a_corrupt_state_file_recovers_to_a_fresh_idle_state(updater):
    # Kills the mutant that reverts set_state's heartbeat-refresh check
    # (this round's Important 1, and the FIRST round's own "Important 2"
    # fix at what is now :199-241) back to `write_state "$(jq ... ||
    # true)"` - i.e. feeding write_state whatever jq's stdout happened to
    # be, success or failure, without ever checking. There was no
    # permanent test for this at all; the first round's report shows only
    # a manual before/after. Also doubles as required coverage for "a
    # corrupt-state test" from this round's brief.
    (updater.update_dir / "state.json").write_text("garbage{", encoding="utf-8")
    result, calls, state = updater()
    assert result.returncode == 0
    assert state is not None
    assert state["phase"] == "idle"
    assert state["updater_seen_at"]
    assert "docker" not in calls


def test_a_state_file_containing_null_recovers_to_a_fresh_idle_state(updater):
    # Important 1, the `null` case: `null | .updater_seen_at = $seen` is
    # legal jq and yields a non-empty `{"updater_seen_at": "..."}` -
    # exactly the shape the old `[ -n "$REFRESHED" ]` check alone accepted
    # as a valid refresh, permanently losing "phase" and "id" from that
    # point on. The new check requires the parsed value to be an object
    # carrying "phase" before accepting it; `null` fails that and falls
    # through to the same "no usable state yet" recovery as a missing file.
    (updater.update_dir / "state.json").write_text("null", encoding="utf-8")
    result, calls, state = updater()
    assert result.returncode == 0
    assert state is not None
    assert state["phase"] == "idle"
    assert state["updater_seen_at"]
    assert "docker" not in calls


def test_a_state_directory_makes_write_state_fail_loudly(updater):
    # Important 1, the directory case: `[ -f "$STATE" ]` is false for a
    # directory too, so the heartbeat block's `else` calls `set_state idle`
    # on it exactly as it would for a missing file - and the OLD
    # write_state's `mv "$STATE.tmp" "$STATE"` onto a directory does not
    # error, it silently moves the tmp file INTO the directory and returns
    # 0. state.json then stays a directory forever, permanently unreadable,
    # with the script reporting success on every pass. write_state now
    # refuses to write onto anything but a regular file, so this has to
    # fail loudly (non-zero exit) instead.
    state_dir = updater.update_dir / "state.json"
    state_dir.mkdir()
    result, _, _ = updater()
    assert result.returncode != 0
    assert state_dir.is_dir()
    assert list(state_dir.iterdir()) == []  # nothing got moved into it


def test_reject_records_state_before_logging(updater):
    # Kills the mutant that reorders reject() back to log-then-state (the
    # FIRST round's "Important 3", part 1, at what is now :188-192) - no
    # permanent test existed for the ordering itself. $LOG is a FIFO with
    # no reader: opening it for the log() append blocks forever, at the OS
    # level, regardless of log()'s own `|| true` (that only matters once a
    # command RETURNS - it cannot un-block an open() call that hasn't
    # returned yet). So this distinguishes the two orders cleanly: with
    # state-before-log (fixed), the rejection is already on disk by the
    # time the script wedges on the log write; with log-before-state
    # (mutant), the wedge happens before set_state ever runs, and
    # state.json is left exactly as the heartbeat wrote it moments earlier
    # ("idle") - never "rejected".
    _write_request(updater, channel="invalid-channel")
    os.mkfifo(updater.update_dir / "log.txt")
    with pytest.raises(subprocess.TimeoutExpired):
        updater(_timeout=2)
    state = json.loads((updater.update_dir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "rejected"


def test_an_unwritable_log_does_not_prevent_a_rejection_from_being_recorded(updater):
    # Kills the mutant that removes log()'s `|| true` (the FIRST round's
    # "Important 3", part 2, at what is now :94-98) - no permanent test
    # existed for it either. Distinct failure mode from the FIFO test
    # above: here $LOG fails immediately (permission denied) rather than
    # blocking, so it is `|| true` specifically - not ordering - that has
    # to keep the failure from propagating through `set -eu` and killing
    # the script after set_state already ran.
    _write_request(updater, channel="invalid-channel")
    log_path = updater.update_dir / "log.txt"
    log_path.write_text("", encoding="utf-8")
    log_path.chmod(0o444)
    try:
        result, _, state = updater()
    finally:
        log_path.chmod(0o644)  # let tmp_path's own cleanup remove it
    assert result.returncode == 0
    assert state["phase"] == "rejected"


def test_a_leading_v_is_stripped_from_the_target(updater):
    # Kills the mutant that removes the `v`-prefix normalisation (the
    # FIRST round's Minor 7, at what is now :395-398) - no test exercised
    # the `v`-prefix acceptance path at all before this. Without the
    # normalisation, Task 3 would write LOXMATTER_IMAGE_TAG=v0.3.0 into
    # .env instead of the bare version the rest of the system expects.
    # Since Task 3's flow now runs an accepted request through to
    # completion in the same pass, this ends at "done", not "queued" -
    # what still pins down the normalisation is `to` and the .env write.
    _write_request(updater, target="v0.3.0")
    _, calls, state = updater()
    assert state["phase"] == "done"
    assert state["to"] == "0.3.0"
    assert "docker" in calls
    assert "LOXMATTER_IMAGE_TAG=0.3.0" in (updater.stack / ".env").read_text(encoding="utf-8")


# --------------------------------------------------------------- Task 3 --
# The flow itself: back up, fetch/check out the target, pull, recreate,
# wait for health. See task-3-brief.md / the Stufe 2 plan for the full
# design; the comments below only record where these tests had to depart
# from that brief's own literal text.


def test_the_flow_keeps_its_order(updater):
    _write_request(updater, target="0.3.0")
    _, calls, state = updater()
    lines = calls.splitlines()
    tar_idx = next(i for i, line in enumerate(lines) if line.startswith("tar "))
    pull_idx = next(i for i, line in enumerate(lines) if _is_compose_call(line, "pull"))
    up_idx = next(i for i, line in enumerate(lines) if _is_compose_call(line, "up"))
    assert tar_idx < pull_idx
    assert pull_idx < up_idx
    assert state["phase"] == "done"


def test_the_image_pull_reports_phase_pull_not_backup(updater):
    # Important 2. `set_state pull ""` used to run right before `git
    # fetch` - typically sub-second - while the ACTUAL image download
    # (`docker compose pull $SERVICE`, by far the longest step in this
    # whole file) ran silently inside the "backup" phase, which had
    # already been entered for the tar backup and never left again until
    # `set_state recreate` afterward. Proven against the unpatched
    # script: the docker stub below, snapshotting state.json at the
    # instant `compose pull` is actually invoked, read "backup" - the
    # card in the web UI (index.html's four-step list) was highlighting
    # "Backing up the database" while a multi-minute arm64 pull was
    # actually running, and had already finished highlighting "Loading
    # the image" for a fetch that took a fraction of a second.
    #
    # Snapshots state.json to a side file the instant the pull itself is
    # invoked - the same technique test_the_self_replacement_runs_
    # strictly_after_done_is_recorded (above) uses for the identical
    # reason: a direct, timestamped witness of what was actually on disk
    # at that moment, not just what the finished pass ends on.
    snapshot = updater.update_dir / "state-at-pull.json"
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        _docker_stub_source(
            compose_case=(
                'case " $* " in\n'
                f'      *" pull "*) cp "$LOXMATTER_UPDATE_DIR/state.json" "{snapshot}" 2>/dev/null ;;\n'
                "    esac\n"
                "    exit 0 ;;"
            )
        ),
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, _calls, state = updater()
    assert state["phase"] == "done"
    assert snapshot.is_file(), "the image pull never ran"
    snap_state = json.loads(snapshot.read_text(encoding="utf-8"))
    assert snap_state["phase"] == "pull", (
        "the phase at the moment of the actual image download should read "
        f"'pull', not {snap_state['phase']!r}"
    )


def test_the_heartbeat_keeps_advancing_through_a_long_image_pull(updater):
    # Important 1. `updater_seen_at` used to be written only by
    # `write_state` - at pass start, and at each `set_state` call - and
    # nothing refreshed it WITHIN a phase. `compose pull` is a single
    # blocking call with no loop of its own to hang a refresh off; proven
    # against the unpatched script with a `docker compose pull` stub that
    # slept for real seconds: the timestamp taken right before that call
    # and the one taken right after it were identical, for the entire
    # duration. The bridge treats the sidecar as absent after 30 seconds
    # of silence (`_MAX_SILENT_SECONDS`, update.py) - on a Pi, where an
    # arm64 pull takes minutes, this meant most of every SUCCESSFUL
    # update looked exactly like a crashed sidecar.
    #
    # The docker stub below snapshots state.json to two side files,
    # immediately before and after sleeping through the pull itself - a
    # direct witness of what the heartbeat read at each end of a call
    # long enough (7s, comfortably longer than
    # compose_pull_with_heartbeat's own 1s refresh poll) to prove the
    # timestamp moves DURING it, not only once it returns.
    before = updater.update_dir / "state-before-pull.json"
    after = updater.update_dir / "state-after-pull.json"
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        _docker_stub_source(
            compose_case=(
                'case " $* " in\n'
                f'      *" pull "*) cp "$LOXMATTER_UPDATE_DIR/state.json" "{before}" 2>/dev/null\n'
                "        sleep 7\n"
                f'        cp "$LOXMATTER_UPDATE_DIR/state.json" "{after}" 2>/dev/null ;;\n'
                "    esac\n"
                "    exit 0 ;;"
            )
        ),
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, _calls, state = updater(_timeout=30)
    assert state["phase"] == "done"
    assert before.is_file() and after.is_file(), "the image pull never ran"
    before_state = json.loads(before.read_text(encoding="utf-8"))
    after_state = json.loads(after.read_text(encoding="utf-8"))
    assert after_state["updater_seen_at"] > before_state["updater_seen_at"], (
        "the heartbeat must advance WHILE the pull is still running, not only once it has returned"
    )


def test_the_restart_leaves_the_neighboring_services_alone(updater):
    _write_request(updater, target="0.3.0")
    _, calls, _ = updater()
    up = _compose_calls(calls, "up")[0]
    assert "--no-deps" in up
    assert "loxmatter-updater" not in up


def test_the_image_name_does_not_come_from_the_job(updater):
    # `calls` is the FAKE BINARIES' call log (docker/git/curl/tar) - the
    # image name is deliberately never an argument to any of them (see
    # the comment above `log "target image: ..."` in update-once.sh), so
    # it cannot show up there. It shows up in the script's OWN log
    # (log.txt) instead, precisely because that is the one place meant to
    # record it for an operator without ever handing it to a command.
    _write_request(updater, target="0.3.0")
    updater()
    log_text = (updater.update_dir / "log.txt").read_text(encoding="utf-8")
    assert "ghcr.io/lucienkerl/loxmatter" in log_text


def test_the_tag_lands_in_the_env_file(updater):
    _write_request(updater, target="0.3.0")
    updater()
    assert "LOXMATTER_IMAGE_TAG=0.3.0" in (updater.stack / ".env").read_text(encoding="utf-8")


def test_the_env_file_keeps_its_other_lines(updater):
    # The .env carries MINISERVER_IP, RADIO_DEVICE, LOXMATTER_API_TOKEN. An
    # update that overwrites it takes half the installation down with it.
    env = updater.stack / ".env"
    env.write_text(
        "MINISERVER_IP=10.0.1.9\nLOXMATTER_IMAGE_TAG=0.2.0\nRADIO_DEVICE=/dev/ttyUSB0\n",
        encoding="utf-8",
    )
    _write_request(updater, target="0.3.0")
    updater()
    text = env.read_text(encoding="utf-8")
    assert "MINISERVER_IP=10.0.1.9" in text
    assert "RADIO_DEVICE=/dev/ttyUSB0" in text
    assert "LOXMATTER_IMAGE_TAG=0.3.0" in text
    assert "0.2.0" not in text


def test_a_backup_is_made_before_the_pull(updater):
    _write_request(updater, target="0.3.0")
    _, calls, _ = updater()
    tar_line = next(line for line in calls.splitlines() if line.startswith("tar"))
    assert "store-" in tar_line


def test_a_dev_target_that_is_not_a_descendant_is_rejected(updater):
    # Task 3's ancestry check is the dev channel's equivalent of "forward
    # only" - there is no ordering over SHAs, but there is ancestry (see
    # the comment above `git merge-base --is-ancestor` in
    # update-once.sh). The fixture's default `git` stub always exits 0
    # regardless of subcommand (see its own comment above, in the
    # `updater` fixture) - which is exactly why every dev-channel test
    # above never needed to distinguish "is an ancestor" from "is not".
    # Override it here so `merge-base --is-ancestor` specifically fails,
    # the same way it does both for a real commit unreachable from HEAD
    # and for a `git` that cannot answer the question at all - the check
    # has to fail CLOSED in both cases, not only the first, and nothing
    # short of actually failing this call exercises that.
    # Every call is `git -C "$REPO" <subcommand> ...` - the subcommand is
    # $3, not $1 - so matching on "$*" rather than a positional parameter
    # is what actually distinguishes merge-base from fetch/checkout here.
    git_path = updater.bindir / "git"
    git_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "git" "$*" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        "  *merge-base*) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    git_path.chmod(0o755)
    _write_request(updater, channel="dev", target="abcdef1")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert state["error"] == "not a descendant of the running state"
    assert "docker compose" not in calls


# ------------------------------------------------------- Stufe 2, round 2 --
# The tests below close the second review pass on Task 3's flow (see
# .superpowers/sdd/task-3-stufe2-report.md for the flow itself). One
# Critical (set_tag's append branch corrupting an unterminated, tag-less
# .env), four Importants (wait_healthy counting iterations instead of
# seconds; a failed `compose up` stranding the new tag with no rollback
# hand-off; the two unguarded set_tag calls under `set -eu`; five mutants
# from Important 5's table), and the cheap Minors (sed metacharacters in
# the restored tag, .env mode/ownership/symlink loss, backup pruning
# counting attempts instead of successes, and a checkout failure message
# that conflated "ref does not exist" with "checkout refused").


def test_an_env_file_with_no_trailing_newline_and_no_tag_line_keeps_its_other_values(updater):
    # Critical 1. Proven end to end against the unpatched set_tag(): with
    # .env = "MINISERVER_IP=10.0.1.9\nLOXMATTER_API_TOKEN=deadbeefcafe"
    # (no trailing newline, and no LOXMATTER_IMAGE_TAG= line - the APPEND
    # branch, not the sed-replace branch the existing .env fixture always
    # exercises, since that one seeds both a trailing newline and a tag
    # line), a run produced the single corrupted line
    # "LOXMATTER_API_TOKEN=deadbeefcafeLOXMATTER_IMAGE_TAG=0.3.0" - the
    # token gone, and no line beginning "LOXMATTER_IMAGE_TAG=" left for
    # docker-compose.yml to find (it silently falls back to its own
    # `stable` default), while state.json still reported "done". Checked
    # via splitlines(), not a substring test, specifically so a
    # concatenated line like the one above could not accidentally satisfy
    # the assertion the way "LOXMATTER_IMAGE_TAG=0.3.0" in text would.
    (updater.stack / ".env").write_text(
        "MINISERVER_IP=10.0.1.9\nLOXMATTER_API_TOKEN=deadbeefcafe", encoding="utf-8"
    )
    _write_request(updater, target="0.3.0")
    _, _calls, state = updater()
    assert state["phase"] == "done"
    lines = (updater.stack / ".env").read_text(encoding="utf-8").splitlines()
    assert "MINISERVER_IP=10.0.1.9" in lines
    assert "LOXMATTER_API_TOKEN=deadbeefcafe" in lines
    assert "LOXMATTER_IMAGE_TAG=0.3.0" in lines


def test_wait_healthy_is_bounded_by_wall_clock_not_curl_duration(updater):
    # Important 2. The old loop counted iterations (`i=$((i + 1))`, one
    # increment per pass) and only ever slept 1s per iteration ON TOP OF
    # whatever curl itself took - so a curl that consumes its own `-m 3`
    # budget inflates the real wait by roughly 4x. Measured against the
    # unpatched script: HEALTH_TIMEOUT=5 took 20.7 wall-clock seconds.
    # This curl stub plays that wedged container: it always fails, but
    # only after sleeping 2 REAL seconds - long enough that the old
    # iteration-counting loop, with HEALTH_TIMEOUT=2, would run two
    # iterations at ~3s each (2s curl + 1s sleep) for roughly 6s total,
    # while a wall-clock-bounded loop stops within about a second of the
    # 2s deadline regardless of how long any single curl call takes.
    #
    # With Task 4's rollback now appended, an unhealthy update no longer
    # ends the pass at "rollback" (a hand-off phase, not a terminal one) -
    # it runs the rollback's OWN wait_healthy immediately afterward, which
    # this same wedged curl also fails. Two wall-clock-bounded waits now
    # happen in this one pass instead of one; the bound below is widened
    # accordingly, but the claim is unchanged: bounded by the clock, not
    # by how long any single curl call takes, regardless of how many such
    # waits a pass contains.
    #
    # Widened once more for Important 1 (this branch's own fix, not a
    # regression): `compose_pull_with_heartbeat` polls its backgrounded
    # pull once a second rather than checking it synchronously, so even
    # an already-finished pull (this fixture's docker stub returns
    # instantly) can cost up to one full second of that poll's own
    # granularity before the loop notices - a real, disclosed, and
    # deliberately accepted cost of keeping the heartbeat alive during a
    # call this file has no other way to interrupt (see that function's
    # own comment). The bound below still sits far under what the OLD,
    # iteration-counting bug this test exists to catch would produce.
    curl_path = updater.bindir / "curl"
    curl_path.write_text("#!/bin/sh\nsleep 2\nexit 1\n", encoding="utf-8")
    curl_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    start = time.monotonic()
    _, _, state = updater(LOXMATTER_HEALTH_TIMEOUT="2", _timeout=30)
    elapsed = time.monotonic() - start
    assert state["phase"] == "failed"
    assert state["rolled_back"] is True
    assert elapsed < 12, (
        f"took {elapsed:.1f}s - two wall-clock-bounded 2s waits plus one "
        "1s-granularity pull poll should stay well under this"
    )


def test_the_heartbeat_keeps_advancing_through_a_long_health_wait(updater):
    # Important 1, the other half of the fix (the pull's own half has its
    # own test above): `wait_healthy`'s loop used to write
    # `updater_seen_at` zero times across its ENTIRE run - the top-of-pass
    # heartbeat block only runs once, before this loop is ever entered,
    # and nothing inside the loop touched state.json at all. On a Pi the
    # production HEALTH_TIMEOUT is 120s; the bridge treats the sidecar as
    # absent after 30s of silence (`_MAX_SILENT_SECONDS`, update.py) - so
    # a health check that took even half a minute made the bridge
    # conclude the sidecar had crashed, right as it was working exactly
    # as designed.
    #
    # `compose up` for $SERVICE is made to fail outright (same
    # `docker_path` stub `test_a_failed_restart_hands_off_to_rollback_
    # instead_of_stranding_the_tag` above uses), which sets `RECREATE_OK`
    # to false and skips the FIRST ("health" phase) wait entirely - the
    # run reaches exactly ONE `wait_healthy` call, inside the rollback,
    # with no `set_state` in between to interrupt it. Isolating to a
    # single, uninterrupted call matters: a first version of this test
    # let both the "health" AND "rollback" health waits contribute
    # samples, and `set_state rollback ""` sitting BETWEEN them already
    # advances `updater_seen_at` on its own - the assertion below passed
    # even with `refresh_heartbeat` deleted from `wait_healthy`, catching
    # nothing. This version does not have that gap: every sample comes
    # from curl calls inside the ONE wait this pass ever performs.
    #
    # The curl stub below (always fails, no `-f` needed to distinguish it
    # here) appends the CURRENT `updater_seen_at` to a side log on every
    # invocation - `wait_healthy` calls curl once per iteration of its own
    # one-second loop, so this is a direct, timestamped witness of what
    # the heartbeat read on each pass through a wait long enough
    # (`HEALTH_TIMEOUT=5`) to span several of `refresh_heartbeat`'s calls.
    # At least two DISTINCT timestamps across those samples is what proves
    # the heartbeat moved DURING the wait, not only once at its very
    # start.
    seen_log = updater.update_dir / "seen-during-health-wait.log"
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        _docker_stub_source(compose_case='case " $* " in *" up "*) exit 1 ;; esac; exit 0 ;;'),
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    curl_path = updater.bindir / "curl"
    curl_path.write_text(
        "#!/bin/sh\n"
        f'jq -r ".updater_seen_at" "$LOXMATTER_UPDATE_DIR/state.json" >> "{seen_log}" 2>/dev/null\n'
        "exit 1\n",
        encoding="utf-8",
    )
    curl_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, _calls, state = updater(LOXMATTER_HEALTH_TIMEOUT="5", _timeout=30)
    assert state["phase"] == "failed"
    assert state["rolled_back"] is True
    assert seen_log.is_file(), "the health check never ran"
    seen_timestamps = [line for line in seen_log.read_text(encoding="utf-8").splitlines() if line]
    assert len(seen_timestamps) >= 2, (
        "the health wait must have looped more than once to prove anything"
    )
    assert len(set(seen_timestamps)) > 1, (
        "the heartbeat must advance WHILE the health wait is still running, "
        f"not stay frozen at one value the whole time: {seen_timestamps!r}"
    )


def test_a_failed_restart_hands_off_to_rollback_instead_of_stranding_the_tag(updater):
    # Important 3, before Task 4 existed. Proven against the unpatched
    # script with a `compose up` stub that exits 1: the run ended `phase:
    # failed`, `error: restart failed`, and .env was left reading the NEW
    # tag forever - neither restored (as the pull-failure path does) nor
    # handed to any rollback, because none existed yet. Since
    # `--force-recreate` removes the old container before creating its
    # replacement, the service can genuinely be down at this point, and
    # only a further `compose up` - what the rollback now performs,
    # against the OLD image - can recover it; restoring just the tag
    # would not restart anything by itself.
    #
    # With Task 4's rollback appended, this fixture's run no longer stops
    # at the hand-off: it continues into the rollback within the SAME
    # pass and completes it, so .env ends up back on the concrete version
    # that was actually RUNNING (0.2.0, from the `inspect` stub below) -
    # not the un-recreated 0.3.0 target this update never reached, and
    # not restored on the assumption that the rollback might still be a
    # separate step.
    # compose_case matches on " up " as a whole token within the FULL
    # argument string, not on a positional "$2" - compose() in
    # update-once.sh now runs `docker compose -f ... --project-directory
    # ... <subcommand> ...`, so the subcommand is no longer the second
    # argument to `docker` (see _compose_calls()'s own docstring for the
    # same reasoning on the Python assertion side).
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        _docker_stub_source(compose_case='case " $* " in *" up "*) exit 1 ;; esac; exit 0 ;;'),
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, _calls, state = updater()
    assert state["phase"] == "failed"
    assert state["rolled_back"] is True
    assert (updater.stack / ".env").read_text(encoding="utf-8") == "LOXMATTER_IMAGE_TAG=0.2.0\n"


def test_a_tag_write_that_cannot_be_made_is_recorded_as_a_failure(updater):
    # Important 4, first of its two call sites (writing the new tag,
    # before the pull). Proven against the unpatched script with $STACK
    # made unwritable: set_tag's own temp-file create failed
    # ("Permission denied"), and since that call was unguarded under
    # `set -eu`, the WHOLE SCRIPT stopped right there - state.json stayed
    # frozen at "pull" with no `error` field and no "failed" phase ever
    # written, while the heartbeat kept refreshing (it is rewritten at
    # the top of every pass, before this point is ever reached again)
    # and $REPO's checkout had already moved to the new ref while the
    # container still ran the old image.
    updater.stack.chmod(0o555)
    try:
        _write_request(updater, target="0.3.0")
        result, calls, state = updater()
    finally:
        updater.stack.chmod(0o755)
    assert result.returncode == 0
    assert state is not None
    assert state["phase"] == "failed"
    assert state["error"]
    assert "docker compose" not in calls


def test_a_pull_failure_that_cannot_restore_the_tag_is_recorded_as_a_failure(updater):
    # Important 4, second call site: restoring $FROM after a failed
    # `compose pull`. The docker stub below makes $STACK unwritable AS
    # PART OF failing `compose pull` - i.e. exactly at the moment
    # `set_tag "$FROM"` would run - while the FIRST set_tag call (writing
    # the new tag, before the pull) still runs normally beforehand,
    # isolating this second call site specifically. Proven against the
    # unpatched script this way: rc=1, "Permission denied", and
    # state.json frozen at "pull" - further from the truth than even the
    # caught pull failure alone, since $FROM could not be restored either.
    # See the previous test's comment: matches " pull " as a whole token
    # in the FULL argument string, not a positional "$2".
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        _docker_stub_source(
            compose_case='case " $* " in *" pull "*) chmod 0555 "$LOXMATTER_STACK"; exit 1 ;; esac; exit 0 ;;'
        ),
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    try:
        result, _calls, state = updater()
    finally:
        updater.stack.chmod(0o755)
    assert result.returncode == 0
    assert state is not None
    assert state["phase"] == "failed"
    assert "restored" in state["error"]


def test_an_unhealthy_service_falls_through_to_rollback_not_done(updater):
    # Originally killed three of Important 5's five surviving mutants at
    # once: `wait_healthy() { return 0; ... }` (the health gate deleted
    # entirely), `curl -fsS` weakened to `curl -sS` (an HTTP 500 then
    # counts as healthy), and the final `set_state rollback ""` replaced
    # with `:` (the hand-off Task 4 continues below it). This curl stub
    # only SUCCEEDS when invoked WITHOUT `-f` - i.e. it plays a real curl
    # seeing an HTTP 500: with `-f` present (the correct code), that is a
    # failure; drop `-f` (one of the mutants) and the identical response
    # counts as success.
    #
    # Now that the rollback is appended and runs in the SAME pass, the
    # first two mutants still turn this test red exactly as before:
    # either one makes the INITIAL health check falsely report healthy,
    # and the run takes the success branch straight to "done" instead of
    # ever reaching the rollback. The third mutant (the intermediate
    # `set_state rollback ""` neutered to `:`) can no longer be told
    # apart from correct behaviour by the FINAL state alone: the rollback
    # code runs unconditionally regardless of that one write, and its own
    # terminal `set_state failed` overwrites whatever came before it -
    # an intrinsic consequence of the rollback now actually existing, not
    # a loss of coverage for the two mutants this assertion still catches.
    curl_path = updater.bindir / "curl"
    curl_path.write_text(
        '#!/bin/sh\ncase "$*" in\n  *-f*) exit 1 ;;\n  *) exit 0 ;;\nesac\n',
        encoding="utf-8",
    )
    curl_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, _calls, state = updater()
    assert state["phase"] == "failed"
    assert state["rolled_back"] is True


def test_the_checkout_uses_the_v_prefixed_ref(updater):
    # Kills two more of Important 5's mutants: the whole `git checkout
    # --detach` block replaced by `:` (a release is then never actually
    # checked out - the comment on that block explains the compose file
    # must match the version), and `REF="v${TARGET#v}"` weakened to
    # `REF="${TARGET#v}"` (releases are tagged `v0.2.0`, not bare
    # `0.2.0`, so a real host's checkout would fail on every single
    # stable update and nothing here would notice). Deleting the
    # checkout block leaves no such call-log line at all; dropping the
    # `v` produces "checkout --detach 0.3.0" instead - the exact
    # substring below, "v" included, distinguishes both from the correct
    # call.
    _write_request(updater, target="0.3.0")
    _, calls, _state = updater()
    assert "checkout --detach v0.3.0" in calls


def test_the_target_is_fetched_before_checkout(updater):
    # Kills the mutant that deletes the `git fetch` block entirely - a
    # checkout could then only ever succeed against whatever the
    # repository already happened to have locally, silently, with
    # nothing here to notice a target that was never actually fetched.
    _write_request(updater, target="0.3.0")
    _, calls, _state = updater()
    assert "fetch --tags --force origin" in calls
    assert calls.index("fetch --tags --force origin") < calls.index("checkout --detach v0.3.0")


def test_set_tag_escapes_sed_metacharacters_in_the_restored_tag(updater):
    # Minor 8. Proven directly: `set_tag 'a|b'` unescaped makes sed
    # itself fail ("bad flag in substitute command", since the bare `|`
    # closes the substitution early) and leaves a 0-byte $ENV_FILE.tmp
    # behind. $FROM comes back out of a hand-editable .env with no bound
    # on its character set - unlike TARGET, which Rule 2 already
    # restricts. Seed .env with a tag containing `|` and fail the pull so
    # `set_tag "$FROM"` actually runs with that value.
    (updater.stack / ".env").write_text("LOXMATTER_IMAGE_TAG=a|b\n", encoding="utf-8")
    # Matches " pull " as a whole token in the full argument string - see
    # test_a_failed_restart_hands_off_to_rollback_instead_of_stranding_the_tag's
    # comment above for why not a positional "$2".
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        _docker_stub_source(compose_case='case " $* " in *" pull "*) exit 1 ;; esac; exit 0 ;;'),
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, _calls, state = updater()
    assert state["phase"] == "failed"
    assert (updater.stack / ".env").read_text(encoding="utf-8") == "LOXMATTER_IMAGE_TAG=a|b\n"
    assert not (updater.stack / ".env.tmp").exists()


def test_set_tag_preserves_the_env_files_mode(updater):
    # Minor 7, the mode half. Measured on the unpatched function:
    # 0600 -> 0644 and 0444 -> 0644 after an update - `mv`ing a
    # brand-new temp file onto .env silently resets its permissions to
    # whatever the process umask allows, discarding whatever an operator
    # (or install.sh) had deliberately set.
    (updater.stack / ".env").chmod(0o600)
    _write_request(updater, target="0.3.0")
    updater()
    mode = (updater.stack / ".env").stat().st_mode & 0o777
    assert mode == 0o600


def test_set_tag_preserves_a_symlinked_env_file(updater):
    # Minor 7, the symlink half. Measured on the unpatched function: a
    # symlinked .env (a shared config kept outside the checkout, say)
    # was silently replaced by a plain file, because `mv` onto a path
    # replaces whatever sits there instead of writing through it.
    real_env = updater.update_dir.parent / "real-env"
    real_env.write_text("LOXMATTER_IMAGE_TAG=0.2.0\n", encoding="utf-8")
    env_path = updater.stack / ".env"
    env_path.unlink()
    env_path.symlink_to(real_env)
    _write_request(updater, target="0.3.0")
    updater()
    assert env_path.is_symlink()
    assert env_path.resolve() == real_env.resolve()
    assert "LOXMATTER_IMAGE_TAG=0.3.0" in real_env.read_text(encoding="utf-8")


def test_a_failed_update_does_not_prune_backups(updater):
    # Minor 9. The prune-to-last-ten loop used to run immediately after
    # EVERY backup, success or failure alike - so a run of failed
    # attempts (each still makes exactly one backup, per step 1's "before
    # anything risky" reasoning) counted toward the same ten-file budget
    # as genuine successes, and could evict the one copy that matters
    # after a schema-raising release. Seed ten pre-existing backups (aged
    # so a new one would rank as the newest) and fail the update at the
    # pull step - strictly AFTER the backup already ran. None of the ten
    # may be pruned: pruning now happens only once an update actually
    # reaches "done".
    backups_dir = updater.backup_dir
    backups_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    alte_dateien = []
    for i in range(10):
        p = backups_dir / f"store-2020-01-01-{i:06d}.tgz"
        p.write_bytes(b"x")
        os.utime(p, (now - 100000 + i, now - 100000 + i))
        alte_dateien.append(p)

    tar_path = updater.bindir / "tar"
    tar_path.write_text(
        '#!/bin/sh\nprintf "%s %s\\n" "tar" "$*" >> "$STUB_LOG"\n[ "$1" = "czf" ] && : > "$2"\n',
        encoding="utf-8",
    )
    tar_path.chmod(0o755)

    # Matches " pull " as a whole token in the full argument string - see
    # test_a_failed_restart_hands_off_to_rollback_instead_of_stranding_the_tag's
    # comment above for why not a positional "$2".
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        _docker_stub_source(compose_case='case " $* " in *" pull "*) exit 1 ;; esac; exit 0 ;;'),
        encoding="utf-8",
    )
    docker_path.chmod(0o755)

    _write_request(updater, target="0.3.0")
    _, _calls, state = updater()
    assert state["phase"] == "failed"
    for p in alte_dateien:
        assert p.exists(), f"{p.name} was pruned even though the update never succeeded"


def test_a_checkout_refused_by_local_modifications_is_reported_distinctly(updater):
    # Minor 10. Before this fix, "target $REF not found in the
    # repository" was reported for ANY checkout failure, including one
    # where the ref exists perfectly well but the checkout itself refuses
    # because $REPO's working tree has local modifications that would be
    # overwritten - a materially different, non-retryable-by-picking-
    # another-target problem the old message actively misled an operator
    # away from. Stub `rev-parse` (does the ref exist?) to succeed and
    # ONLY `checkout` itself to fail.
    git_path = updater.bindir / "git"
    git_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "git" "$*" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        "  *rev-parse*) exit 0 ;;\n"
        '  *"checkout --detach"*) exit 1 ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    git_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, _calls, state = updater()
    assert state["phase"] == "failed"
    assert "not found in the repository" not in state["error"]


def test_a_missing_ref_is_still_reported_as_not_found(updater):
    # Companion to the test above: confirms the "not found" wording is
    # still reachable, and specifically for the case it now means -
    # `rev-parse` itself says the ref does not exist - with `checkout`
    # never even attempted for a ref already known not to exist.
    #
    # Matches specifically on "rev-parse -q --verify" (Rule 2's own
    # existence check), NOT on a bare "*rev-parse*" - the flow now also
    # runs a plain `git rev-parse HEAD` earlier, past validation, to
    # determine GIT_BEFORE (see the block comment above that call in
    # update-once.sh). A blanket "*rev-parse*" match would fail THAT one
    # too and end the run at GIT_BEFORE's own "could not determine the
    # currently checked-out commit" failure instead of ever reaching the
    # ref-existence check this test exists to cover.
    git_path = updater.bindir / "git"
    git_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "git" "$*" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        '  *"rev-parse -q --verify"*) exit 1 ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    git_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, calls, state = updater()
    assert state["phase"] == "failed"
    assert "not found in the repository" in state["error"]
    assert "checkout --detach" not in calls


# ------------------------------------------------------------------ Task 4 --
# The rollback, the plain-text file, and the self-replacement. See
# update-once.sh's own "rollback" section for the reasoning; the tests
# below only cover what that reasoning implies is observable.


@pytest.fixture
def unhealthy_service(updater, tmp_path):
    """The same environment, but `curl` never responds healthy - the case
    the rollback exists for."""
    curl = tmp_path / "bin" / "curl"
    curl.write_text(
        '#!/bin/sh\nprintf "curl %s\\n" "$*" >> "$STUB_LOG"\nexit 7\n', encoding="utf-8"
    )
    curl.chmod(0o755)
    return updater


def test_an_unhealthy_service_is_rolled_back(unhealthy_service):
    # The rollback is a hand-off ("rollback" is not a terminal phase, see
    # the comment above the first `set_state rollback ""` in
    # update-once.sh) - once it runs, the pass ends `failed`, not
    # `rollback`, with `rolled_back` recording that the attempt was made.
    _write_request(unhealthy_service, target="0.3.0")
    _, _, state = unhealthy_service()
    assert state["phase"] == "failed"
    assert state["rolled_back"] is True


def test_the_rollback_checks_out_head_when_git_before_could_not_be_determined(unhealthy_service):
    # Found by manual verification, not by reading the code: this
    # fixture's default `git` stub always "succeeds" (exit 0) with NO
    # output at all - unlike a real `git rev-parse HEAD`, which never
    # succeeds without printing a SHA. `GIT_BEFORE="$(git ... || echo
    # HEAD)"` only substitutes the fallback on a NON-ZERO exit; a `git`
    # that exits 0 with empty stdout sails straight through it, and
    # GIT_BEFORE ends up "" - proven end to end against the unpatched
    # line: the rollback's own checkout call read literally `git ...
    # checkout --detach ` (trailing space, no argument at all). Fixed by
    # capturing the raw output and falling back through `${:-HEAD}`
    # instead, the same idiom current_tag() and running_version() already
    # use above for exactly this "succeeded but unusable" shape.
    _write_request(unhealthy_service, target="0.3.0")
    _, calls, _state = unhealthy_service()
    checkouts = [line for line in calls.splitlines() if "checkout --detach" in line]
    assert len(checkouts) == 2, checkouts
    assert checkouts[-1].endswith("checkout --detach HEAD"), checkouts[-1]


def test_the_rollback_restores_the_old_tag(unhealthy_service):
    # BACK comes from $RUNNING (this fixture's `docker inspect` stub
    # always answers "0.2.0"), not from $FROM - see the rollback
    # section's own comment on why a moving alias in .env would be the
    # wrong thing to write back. In this fixture the two happen to agree,
    # since the seeded .env already reads 0.2.0; the distinction matters
    # once .env starts out on the "stable" alias, which is the normal
    # case on every fresh installation (see the comment on current_tag()).
    _write_request(unhealthy_service, target="0.3.0")
    unhealthy_service()
    assert "LOXMATTER_IMAGE_TAG=0.2.0" in (unhealthy_service.stack / ".env").read_text(
        encoding="utf-8"
    )


def test_the_rollback_runs_exactly_once(unhealthy_service):
    # No flapping: two `up` calls (update and rollback), no more. If the
    # cause were not the image itself, a third attempt would only add
    # more downtime without changing the outcome.
    _write_request(unhealthy_service, target="0.3.0")
    _, calls, _state = unhealthy_service()
    assert len(_compose_calls(calls, "up")) == 2


def test_the_rollback_does_not_touch_the_database(unhealthy_service):
    # Spec section 8: the old version runs on the new schema
    # (`_migrate` returns immediately once version >= _SCHEMA_VERSION).
    # Restoring the backup is the more destructive step and stays an
    # explicit action in the web UI.
    _write_request(unhealthy_service, target="0.3.0")
    _, calls, _state = unhealthy_service()
    # Check specifically for the unpacking, not for an arbitrary "-x": that
    # would otherwise trip on any future call that happens to carry an
    # -x flag, and the test would go red for a reason that has nothing to
    # do with its claim.
    tar_aufrufe = [line for line in calls.splitlines() if line.startswith("tar ")]
    assert tar_aufrufe, "the backup itself must have taken place"
    for line in tar_aufrufe:
        assert " -x" not in line and "xzf" not in line, line


def test_a_failure_leaves_a_readable_file(unhealthy_service):
    _write_request(unhealthy_service, target="0.3.0")
    unhealthy_service()
    text = (unhealthy_service.update_dir / "LETZTER-FEHLSCHLAG.txt").read_text(encoding="utf-8")
    assert "0.2.0" in text
    assert "0.3.0" in text
    assert "scripts/update.sh" in text


def test_a_successful_update_leaves_no_failure_file(updater):
    # A stale file from an earlier failed attempt must not survive a
    # later success - otherwise an operator reads yesterday's rollback
    # story while today's update went through cleanly. Pre-seeding one
    # here is what makes this test able to fail at all: a fresh run that
    # never creates the file in the first place would satisfy the bare
    # "does not exist" assertion whether or not `rm -f "$FAILURE"` in the
    # success branch actually runs.
    (updater.update_dir / "LETZTER-FEHLSCHLAG.txt").write_text("stale", encoding="utf-8")
    _write_request(updater, target="0.3.0")
    updater()
    assert not (updater.update_dir / "LETZTER-FEHLSCHLAG.txt").exists()


def test_a_recreate_failure_also_rolls_back_without_a_pointless_wait(updater):
    # The `RECREATE_OK` branch in update-once.sh: when `compose up` fails
    # outright (the container was never even created), waiting the full
    # health timeout before rolling back would only cost time - the
    # rollback below is what update-once.sh's own comment calls "the only
    # thing left that can bring the house back up", and it should start
    # at once. This docker stub fails every `compose up` call (both the
    # initial recreate and the rollback's own retry) but leaves `inspect`
    # and every other subcommand alone; the default curl stub (always
    # healthy) is left in place, so a successful ROLLBACK recreate would
    # otherwise look "healthy" regardless of what docker itself reported -
    # exactly why HEALTHY here reflects curl, not docker's exit code.
    # Matches " up " as a whole token in the full argument string - see
    # test_a_failed_restart_hands_off_to_rollback_instead_of_stranding_the_tag's
    # comment above for why not a positional "$2".
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        _docker_stub_source(compose_case='case " $* " in *" up "*) exit 1 ;; esac; exit 0 ;;'),
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    start = time.monotonic()
    _, calls, state = updater(_timeout=30)
    elapsed = time.monotonic() - start
    assert state["phase"] == "failed"
    assert state["rolled_back"] is True
    # No `set_state health ""` for the doomed initial attempt - a
    # skipped, pointless wait, not merely a short one.
    assert len(_compose_calls(calls, "up")) == 2
    assert elapsed < 5, f"took {elapsed:.1f}s - the initial wait should have been skipped entirely"


def test_the_sidecar_replaces_itself_only_after_success(updater):
    _write_request(updater, target="0.3.0")
    _, calls, _ = updater(LOXMATTER_UPDATER_SELF_REPLACE="1")
    zeilen = calls.splitlines()
    # Restricted to lines starting "docker compose" specifically, not any
    # line merely MENTIONING "loxmatter-updater" - compose()'s own
    # host_path_for() fix now makes a `docker inspect loxmatter-updater
    # --format ...` call as the FIRST thing any compose() invocation does
    # (cached for the rest of the pass, see that function's comment), and
    # that line would otherwise be mistaken for "own" self-replacement
    # activity even though it runs ahead of the ordinary update's own
    # first `compose pull loxmatter`, not as part of replacing the
    # sidecar itself.
    eigen = next(
        i
        for i, line in enumerate(zeilen)
        if line.startswith("docker compose") and "loxmatter-updater" in line
    )
    fremd = next(
        i
        for i, line in enumerate(zeilen)
        if line.startswith("docker compose") and "loxmatter-updater" not in line
    )
    assert fremd < eigen


def test_after_a_failure_it_does_not_replace_itself(unhealthy_service):
    # "loxmatter-updater" alone is no longer a safe substring to forbid
    # outright: the Stufe-2 fix for the plain-text file's unusable
    # commands (host_path_for(), in update-once.sh) makes a READ-ONLY
    # `docker inspect loxmatter-updater --format ...` call on every
    # failure, rollback included, to resolve $STACK/$REPO back to a host
    # path - that call is expected here, and is not a self-replacement.
    # What this test actually claims is narrower and still holds: no
    # MUTATING `docker compose ... loxmatter-updater` call (a pull or an
    # up) ever runs on a failed pass.
    _write_request(unhealthy_service, target="0.3.0")
    _, calls, _ = unhealthy_service(LOXMATTER_UPDATER_SELF_REPLACE="1")
    self_replace_calls = [
        line
        for line in calls.splitlines()
        if line.startswith("docker compose") and "loxmatter-updater" in line
    ]
    assert self_replace_calls == []


def test_self_replacement_is_off_by_default_in_this_fixture(updater):
    # Documents the fixture default added for Task 4 (see the `updater`
    # fixture's own comment): without an explicit override, a successful
    # update never touches loxmatter-updater, so every other test in this
    # file can assert on the ONE `compose up` line it actually cares
    # about without also accounting for a self-replacement it never asked
    # about.
    #
    # "loxmatter-updater" alone is no longer a safe substring to forbid
    # outright - see test_after_a_failure_it_does_not_replace_itself's own
    # comment just above: compose()'s host_path_for() fix makes a
    # READ-ONLY `docker inspect loxmatter-updater --format ...` call on
    # EVERY compose() invocation now, including the ordinary `loxmatter`
    # pull/up this test's own successful update makes. What still holds,
    # and is what this test actually claims, is narrower: no MUTATING
    # `docker compose ... loxmatter-updater` call (a pull or an up) ever
    # runs when self-replacement is off.
    _write_request(updater, target="0.3.0")
    _, calls, state = updater()
    assert state["phase"] == "done"
    self_replace_calls = [
        line
        for line in calls.splitlines()
        if line.startswith("docker compose") and "loxmatter-updater" in line
    ]
    assert self_replace_calls == []


# ------------------------------------------------------ Task 4 Stufe 2 --
# The second review pass on the rollback, the plain-text file, and the
# self-replacement (see .superpowers/sdd/task-4-stufe2-report.md for the
# earlier round's own write-up). Seven Importants plus minors; each test
# below names which one it closes.


def test_the_rollback_does_not_claim_success_when_the_tag_write_fails(updater):
    # Important 1. Proven against the unpatched script: `ROLLED=true` was
    # set BEFORE `set_tag "$BACK"` was even attempted, so a failing tag
    # write left state.json reporting "rolled_back": true and
    # LETZTER-FEHLSCHLAG.txt printing "Rolled back to: 0.2.0" while .env
    # kept reading 0.3.0 and exactly one `docker compose up` had ever run
    # - an operator reading either would believe the house was back on
    # the old version when it plainly was not.
    #
    # The curl stub below chmods $STACK read-only the moment it is first
    # called - i.e. during the FIRST health wait, well after the update's
    # own earlier `set_tag` call (writing the NEW tag, before the pull)
    # has already succeeded normally. That isolates the failure to
    # exactly the ROLLBACK's own `set_tag "$BACK"` call, the same
    # technique `test_a_pull_failure_that_cannot_restore_the_tag_is_
    # recorded_as_a_failure` already uses for an earlier call site.
    curl_path = updater.bindir / "curl"
    curl_path.write_text(
        "#!/bin/sh\n"
        'printf "curl %s\\n" "$*" >> "$STUB_LOG"\n'
        'chmod 0555 "$LOXMATTER_STACK" 2>/dev/null || true\n'
        "exit 7\n",
        encoding="utf-8",
    )
    curl_path.chmod(0o755)
    try:
        _write_request(updater, target="0.3.0")
        _, calls, state = updater(_timeout=30)
    finally:
        updater.stack.chmod(0o755)
    assert state["phase"] == "failed"
    assert state["rolled_back"] is False
    assert "may still read" in state["error"]
    assert (updater.stack / ".env").read_text(encoding="utf-8") == "LOXMATTER_IMAGE_TAG=0.3.0\n"
    assert len(_compose_calls(calls, "up")) == 1
    text = (updater.update_dir / "LETZTER-FEHLSCHLAG.txt").read_text(encoding="utf-8")
    assert "NOT ROLLED BACK" in text
    assert "Rolled back to:" not in text


def test_the_failure_file_uses_a_real_host_path_when_docker_can_resolve_it(unhealthy_service):
    # Important 2. Proven against the unpatched script: four of the five
    # printed commands began `cd $STACK`/`cd $REPO` - CONTAINER paths
    # (/repo/deploy/testhost, /repo), bind-mounted from the host's
    # checkout - and on the host neither exists at all, so each failed at
    # the `cd` and the `&&` silently swallowed everything after it.
    #
    # host_path_for() in update-once.sh asks the docker daemon itself
    # (`docker inspect loxmatter-updater --format ...`) what is actually
    # mounted where. This stub answers that question the way a real
    # daemon would once the self-replacement service (a later task)
    # exists: one "container-path host-path" line per bind mount.
    host_checkout = "/home/pi/loxmatter-checkout"
    docker_path = unhealthy_service.bindir / "docker"
    docker_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "docker" "$*" >> "$STUB_LOG"\n'
        'case "$1" in\n'
        "  inspect)\n"
        '    if [ "$2" = "loxmatter-updater" ]; then\n'
        f'      printf "%s %s\\n" "$LOXMATTER_REPO" "{host_checkout}"\n'
        f'      printf "%s %s\\n" "$LOXMATTER_STACK" "{host_checkout}/deploy/testhost"\n'
        "    else\n"
        '      printf "LOXMATTER_VERSION=0.2.0\\n"\n'
        "    fi\n"
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _write_request(unhealthy_service, target="0.3.0")
    unhealthy_service()
    text = (unhealthy_service.update_dir / "LETZTER-FEHLSCHLAG.txt").read_text(encoding="utf-8")
    assert f"cd {host_checkout}/deploy/testhost && docker compose logs" in text
    assert f"cd {host_checkout} && ./scripts/update.sh --no-pull" in text
    # Bounded to the RUNNABLE command lines specifically, not the log
    # excerpt at the end of the file (which legitimately still shows the
    # container-side `cd` from this pass's own compose calls - that is a
    # log of what THIS container did, not a command for the operator to
    # run themselves).
    manual_section = text.split("Manual next steps")[1].split("Last lines of the log")[0]
    assert str(unhealthy_service.stack) not in manual_section
    assert str(unhealthy_service.stack.parent.parent) not in manual_section


def test_the_failure_file_resolves_a_host_path_for_a_path_under_a_mount(unhealthy_service):
    # The test directly above stubs `docker inspect` into reporting
    # $LOXMATTER_STACK as a mount destination IN ITS OWN RIGHT - which is
    # not what the real stack does. `deploy/testhost/docker-compose.yml`
    # bind-mounts only `../..:/repo`; $LOXMATTER_STACK
    # (/repo/deploy/testhost by default) is a SUBDIRECTORY of that one
    # mount, never a `Destination` of its own. Proven against the
    # exact-match `awk -v dest="$1" '$1 == dest {...}'` this replaces:
    # with a mount table shaped like the real one - one bind mount, at
    # /repo, nothing separately mounted at /repo/deploy/testhost - that
    # version never matched the stack path at all, so
    # LETZTER-FEHLSCHLAG.txt's "cd $host_stack" line fell back to "host
    # path unknown" for the ONE directory an operator most needs (it is
    # where `docker compose logs`/`./scripts/update.sh` actually have to
    # run from), even though the real host path was fully knowable - the
    # /repo mount's own Source plus "/deploy/testhost". host_path_for()
    # now finds the longest mount Destination that is a path-segment
    # prefix of the requested path and appends the remainder onto that
    # mount's Source, so a single /repo mount is enough to resolve both
    # /repo itself and everything under it.
    host_checkout = "/home/pi/loxmatter-checkout"
    docker_path = unhealthy_service.bindir / "docker"
    docker_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "docker" "$*" >> "$STUB_LOG"\n'
        'case "$1" in\n'
        "  inspect)\n"
        '    if [ "$2" = "loxmatter-updater" ]; then\n'
        f'      printf "%s %s\\n" "$LOXMATTER_REPO" "{host_checkout}"\n'
        "    else\n"
        '      printf "LOXMATTER_VERSION=0.2.0\\n"\n'
        "    fi\n"
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _write_request(unhealthy_service, target="0.3.0")
    unhealthy_service()
    text = (unhealthy_service.update_dir / "LETZTER-FEHLSCHLAG.txt").read_text(encoding="utf-8")
    assert f"cd {host_checkout}/deploy/testhost && docker compose logs" in text
    assert f"cd {host_checkout} && ./scripts/update.sh --no-pull" in text
    assert "host path unknown" not in text


def test_the_failure_file_says_so_plainly_when_the_host_path_cannot_be_resolved(unhealthy_service):
    # Important 2, the other half: the commands must not silently print
    # the unusable container path as if it were fine; they must say
    # plainly that the host path is unknown.
    #
    # This can no longer be reached with NO mount data at all, the way it
    # used to be: compose() itself now also depends on host_path_for()
    # resolving $STACK (the "compose inside a container resolves relative
    # bind mounts to container paths" fix, see compose()'s own comment in
    # update-once.sh), and refuses to run `docker compose` at all when it
    # cannot - so a $STACK that cannot be resolved would fail the update
    # at the very first `compose pull` and never even reach the rollback
    # section this file's own write_failure_file() runs from. The docker
    # stub below therefore resolves $STACK (compose() gets what it needs
    # and the pass proceeds into the rollback) but deliberately answers
    # NOTHING for $REPO - a real daemon that has that mount would answer
    # for both, since deploy/testhost/docker-compose.yml bind-mounts only
    # ONE thing (`../..:/repo`) and $STACK is a subdirectory of it (see
    # the test above), but this fixture's stub is free to model a daemon
    # that only PARTIALLY knows its own mounts, which is enough to prove
    # host_path_for()'s per-path fallback still says so plainly for the
    # one it cannot answer.
    docker_path = unhealthy_service.bindir / "docker"
    docker_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "docker" "$*" >> "$STUB_LOG"\n'
        'case "$1" in\n'
        "  inspect)\n"
        '    if [ "$2" = "loxmatter-updater" ]; then\n'
        '      printf "%s %s\\n" "$LOXMATTER_STACK" "$LOXMATTER_STACK"\n'
        "    else\n"
        '      printf "LOXMATTER_VERSION=0.2.0\\n"\n'
        "    fi\n"
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _write_request(unhealthy_service, target="0.3.0")
    unhealthy_service()
    text = (unhealthy_service.update_dir / "LETZTER-FEHLSCHLAG.txt").read_text(encoding="utf-8")
    assert "host path unknown" in text


def test_the_self_replacement_pulls_before_recreating(updater):
    # Important 3. Proven against the unpatched script: `compose up -d`
    # ALONE never asks the registry anything - Compose's default pull
    # policy is `missing`, and the sidecar's own pinned image is already
    # present locally the instant it is running at all, so the whole
    # block was a silent no-op on every run, self-replacement in name
    # only. `compose pull` first is what actually contacts the registry;
    # `up -d` afterward only recreates when that pull actually changed
    # the local image.
    _write_request(updater, target="0.3.0")
    _, calls, _ = updater(LOXMATTER_UPDATER_SELF_REPLACE="1")
    lines = calls.splitlines()
    pull_idx = next(
        i
        for i, line in enumerate(lines)
        if _is_compose_call(line, "pull") and "loxmatter-updater" in line
    )
    up_idx = next(
        i
        for i, line in enumerate(lines)
        if _is_compose_call(line, "up") and "loxmatter-updater" in line
    )
    assert pull_idx < up_idx


def test_the_rollback_uses_running_not_the_env_alias_when_they_disagree(unhealthy_service):
    # Important 4. Replacing the whole `case "$RUNNING" in ...` block that
    # computes $BACK with a bare `BACK="$FROM"` - the exact regression the
    # design's longest comment in update-once.sh exists to prevent - left
    # ALL other tests in this file passing, because every other fixture
    # seeds .env and the `docker inspect` stub with the SAME version
    # (0.2.0): the two can never disagree in any of them. Seeded to
    # genuinely differ here: .env holds the alias "stable" (the normal
    # case on every fresh installation since 0.2.0 - see current_tag()'s
    # own comment), while the container's baked-in LOXMATTER_VERSION -
    # the only reliable answer to "what is actually running" - says
    # 0.2.0. A correct rollback restores 0.2.0; a `BACK="$FROM"` mutant
    # would instead write back the literal string "stable", pointing the
    # next pull at whatever "stable" now resolves to in the registry -
    # possibly the very release that just failed.
    (unhealthy_service.stack / ".env").write_text("LOXMATTER_IMAGE_TAG=stable\n", encoding="utf-8")
    _write_request(unhealthy_service, target="0.3.0")
    unhealthy_service()
    assert (unhealthy_service.stack / ".env").read_text(encoding="utf-8") == (
        "LOXMATTER_IMAGE_TAG=0.2.0\n"
    )


def test_rollback_writes_the_concrete_version_into_state_json_not_the_alias(unhealthy_service):
    # The bug this test guards against: `$BACK` (computed exactly as in
    # `test_the_rollback_uses_running_not_the_env_alias_when_they_disagree`
    # right above, and correct there - and LETZTER-FEHLSCHLAG.txt already
    # reports it correctly, "Rolled back to: %s") never reached
    # state.json at all. `set_state`'s own `jq` invocation wrote only
    # `from` and `to` - and `from` here is `current_tag()`, i.e. the
    # LOXMATTER_IMAGE_TAG line in .env, "stable" on every standard
    # installation since 0.2.0 (current_tag()'s own comment). The web UI
    # read `state.from` for its rollback sentence and told the user
    # "stable is running again" - a channel name, not the version the
    # rollback actually put back. Seeded so `from` (the alias) and the
    # concrete restored version can never accidentally coincide, the same
    # setup as the sibling test above.
    (unhealthy_service.stack / ".env").write_text("LOXMATTER_IMAGE_TAG=stable\n", encoding="utf-8")
    _write_request(unhealthy_service, target="0.3.0")
    _, _, state = unhealthy_service()
    assert state["rolled_back"] is True
    assert state["from"] == "stable"
    assert state["rolled_back_to"] == "0.2.0"


def test_rolled_back_to_is_null_when_no_rollback_happens(updater):
    # The other half of the same field: a pass that never reaches the
    # rollback section at all (here, a plain `git fetch` failure - no
    # image was ever touched, nothing to name a rollback target for) must
    # not carry a stale or invented `rolled_back_to` into state.json.
    # `set_state`'s `${ROLLED_BACK_TO:-}` default, unset outside the
    # rollback section, is what makes this `null` rather than leftover
    # from some earlier call in the same pass.
    git_path = updater.bindir / "git"
    git_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "git" "$*" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        '  *"fetch --tags --force origin"*) exit 1 ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    git_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, _, state = updater()
    assert state["phase"] == "failed"
    assert state["rolled_back"] is False
    assert state["rolled_back_to"] is None


def test_the_self_replacement_runs_strictly_after_done_is_recorded(updater):
    # Important 5. Moving the self-replacement block to BEFORE
    # `set_state "done" ""` - exactly the ordering the code comment above
    # it says must never happen (it would terminate the sidecar mid-write
    # of the very state the web UI is currently reading) - left every
    # other test in this file passing: the only existing coverage
    # compares `compose up` INDICES within the call log, which that move
    # does not change at all (the self-replacement's own `compose up`
    # line still sorts after the update's `compose up` line regardless of
    # which `set_state` calls happened around either of them).
    #
    # The docker stub below, on the specific call this file makes to
    # replace ITSELF (a `compose` call whose arguments mention
    # "loxmatter-updater"), snapshots state.json to a SIDE file at the
    # moment that call actually runs - a direct, timestamped witness of
    # what the state machine had recorded AT THAT INSTANT, not just what
    # it is once the whole pass has finished.
    snapshot = updater.update_dir / "state-at-self-replace.json"
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        _docker_stub_source(
            compose_case=(
                'case "$*" in\n'
                f'      *loxmatter-updater*) cp "$LOXMATTER_UPDATE_DIR/state.json" "{snapshot}" 2>/dev/null ;;\n'
                "    esac\n"
                "    exit 0 ;;"
            )
        ),
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, _calls, state = updater(LOXMATTER_UPDATER_SELF_REPLACE="1")
    assert state["phase"] == "done"
    assert snapshot.is_file(), "self-replacement never ran"
    snap_state = json.loads(snapshot.read_text(encoding="utf-8"))
    assert snap_state["phase"] == "done"


def test_a_signal_after_done_is_recorded_does_not_overwrite_it(updater):
    # The reported bug, reproduced from the real state.json a successful
    # update on the maintainer's Pi left behind: version 0.3.1 was running
    # - the update genuinely succeeded - but the card read "Update failed".
    #
    # The mechanism: the success path writes `set_state "done" ""`, and
    # only THEN - deliberately, so a genuinely finished write is never torn
    # - does the self-replacement recreate this very container (see the
    # block comment above `compose pull loxmatter-updater` further down in
    # update-once.sh). Docker sends SIGTERM to PID 1 to do that recreate;
    # entrypoint.sh forwards it here; and the trap used to write
    # `set_state failed "interrupted by a signal ..."` unconditionally,
    # overwriting the "done" it had itself just recorded one moment
    # earlier. Two individually-correct pieces of code collide: the
    # self-replacement sits after `done` precisely so it cannot corrupt a
    # write in progress, and the trap exists so a genuine interruption is
    # recorded - neither anticipated that a *deliberate* self-termination
    # is indistinguishable, from inside the trap, from an unwanted one.
    #
    # Reproduced here without a real Docker daemon at all: the FAKE
    # `docker` binary, on exactly the self-replacement's own
    # `up -d --no-deps loxmatter-updater` call (the one that stands in for
    # Docker recreating this container), sends a REAL SIGTERM to its own
    # parent - this very update-once.sh process - while that process is
    # genuinely blocked waiting for the call to return. That is exactly
    # the timing entrypoint.sh's forwarded SIGTERM has in production: it
    # arrives while the self-replacement's own `compose up -d` is still
    # the foreground command.
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        _docker_stub_source(
            compose_case=(
                'case "$*" in\n'
                '      *"up -d --no-deps loxmatter-updater"*) '
                'kill -TERM "$PPID" 2>/dev/null || true ;;\n'
                "    esac\n"
                "    exit 0 ;;"
            )
        ),
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, _calls, state = updater(LOXMATTER_UPDATER_SELF_REPLACE="1", _timeout=15)
    assert state["phase"] == "done"
    assert state["error"] is None
    assert not (updater.update_dir / "LETZTER-FEHLSCHLAG.txt").exists(), (
        "a completed, successful update must not leave a failure report behind"
    )


def test_the_terminal_phase_guard_matches_update_py(updater):
    # Anti-drift check for the invariant above: update-once.sh cannot
    # import loxmatter.update._TERMINAL_PHASES directly (they run in
    # different processes, one of them POSIX sh), so the two lists are
    # kept honest by comparing them here instead - textually, against the
    # exact `case "$sig_phase" in ...)` arm on_signal() uses to decide
    # which phases a signal must leave untouched. A future edit that adds
    # or removes a terminal phase in one file without the other fails this
    # test rather than silently reopening the collision above.
    script_text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r'case "\$sig_phase" in\n\s*([a-z|]+)\)', script_text)
    assert match, "on_signal() must guard on sig_phase with a case arm"
    guarded_phases = set(match.group(1).split("|"))
    assert guarded_phases == set(_TERMINAL_PHASES)


def test_a_corrupted_state_file_does_not_replay_a_completed_rollback(unhealthy_service):
    # Important 6. Exactly-once used to rest SOLELY on state.json's own
    # "id" field, and request.json is never consumed or removed. Proven
    # against the unpatched script: complete a rollback (two recreates),
    # truncate state.json to unparseable garbage (this file's own
    # documented self-healing then resets the id to null - see the
    # heartbeat block's comment), run one more pass - the WHOLE failed
    # update replayed: another backup, another pull, two MORE recreates,
    # four for one request. An operator facing a stuck "rollback" phase
    # (see the SIGTERM trap and Important 7) would plausibly do exactly
    # this by hand, trying to fix what looks like a broken state file.
    #
    # The fix's guard (the "handled/<job-id>" marker, written the moment
    # a request is ACCEPTED) is independent of state.json entirely, so
    # this corruption must no longer be able to trigger a replay.
    _write_request(unhealthy_service, target="0.3.0")
    _, first_calls, state = unhealthy_service()
    assert state["phase"] == "failed"
    assert state["rolled_back"] is True

    (unhealthy_service.update_dir / "state.json").write_text("garbage{", encoding="utf-8")
    _, second_calls, second_state = unhealthy_service()
    assert second_state["phase"] == "idle"
    assert second_calls == first_calls, "the corrupted-state pass must do nothing at all"


def test_a_signal_during_the_rollback_health_wait_leaves_an_honest_failed_state(unhealthy_service):
    # Important 7. There was no `trap` anywhere in this file. Proven
    # against the unpatched script: sending SIGTERM - what entrypoint.sh
    # forwards, from `docker stop` and from its own 600s parent `timeout`
    # - while curl still answered unhealthy during the ROLLBACK's own
    # health wait left state.json exactly as the last `set_state
    # rollback ""` had written it: phase "rollback", "healthy": true
    # (set_state's own DEFAULT, never a measurement), and no
    # LETZTER-FEHLSCHLAG.txt at all - the one artefact meant for exactly
    # "the web UI is unreachable" was missing, and a web UI that WAS
    # reachable would have rendered a completed, healthy rollback that
    # never actually finished.
    #
    # Sends a REAL signal to a REAL running process, mid-wait - polling
    # state.json until it reports phase "rollback" (the second, long
    # health wait happens entirely within that phase; unhealthy_service's
    # curl always fails, so the window is the full HEALTH_TIMEOUT=3s),
    # then delivering SIGTERM exactly then.
    _write_request(unhealthy_service, target="0.3.0")
    proc = unhealthy_service.popen()
    try:
        deadline = time.monotonic() + 10
        reached_rollback = False
        state_file = unhealthy_service.update_dir / "state.json"
        while time.monotonic() < deadline:
            if state_file.is_file():
                try:
                    current = json.loads(state_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    current = None
                if current is not None and current.get("phase") == "rollback":
                    reached_rollback = True
                    break
            time.sleep(0.05)
        assert reached_rollback, "the run never reached the rollback phase in time"
        os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    final_state = json.loads(state_file.read_text(encoding="utf-8"))
    assert final_state["phase"] == "failed"
    assert final_state["healthy"] is not True
    assert "interrupt" in (final_state["error"] or "").lower()
    assert (unhealthy_service.update_dir / "LETZTER-FEHLSCHLAG.txt").exists()


def test_the_rollback_falls_back_to_from_for_an_unidentified_v_prefixed_running_version(updater):
    # Minor. The acceptance check normalises $RUNNING through
    # "${RUNNING#v}" before comparing it against the ''|unbekannt|dev
    # sentinel enum (see $CUR above); the rollback's own `case` tested
    # RAW $RUNNING instead. A build stamped "vdev" therefore matched
    # neither literal branch there and fell through to the "found a real
    # version" arm, producing BACK="dev" - silently accepted as a
    # rollback target - instead of correctly falling back to $FROM, the
    # way a bare "dev" already does everywhere else in this file.
    docker_path = updater.bindir / "docker"
    docker_path.write_text(_docker_stub_source(version="vdev"), encoding="utf-8")
    docker_path.chmod(0o755)
    curl_path = updater.bindir / "curl"
    curl_path.write_text(
        '#!/bin/sh\nprintf "curl %s\\n" "$*" >> "$STUB_LOG"\nexit 7\n', encoding="utf-8"
    )
    curl_path.chmod(0o755)
    _write_request(updater, channel="dev", target="abcdef1")
    updater()
    assert (updater.stack / ".env").read_text(encoding="utf-8") == "LOXMATTER_IMAGE_TAG=0.2.0\n"


def test_a_dev_channel_update_is_rolled_back_when_unhealthy(unhealthy_service):
    # Minor: no test drove a dev-channel update all the way into the
    # rollback before. Exercises $RUNNING being computed unconditionally
    # on channel (an earlier fix in this same section) together with the
    # rollback actually running for a dev-channel request.
    _write_request(unhealthy_service, channel="dev", target="abcdef1")
    _, _calls, state = unhealthy_service()
    assert state["phase"] == "failed"
    assert state["rolled_back"] is True


def test_no_further_recreates_on_the_pass_after_a_rollback(unhealthy_service):
    # Minor: no test asserted zero further recreates on the very next
    # pass after a rollback (distinct from
    # test_a_corrupted_state_file_does_not_replay_a_completed_rollback
    # above, which covers the CORRUPTED-state case specifically) - this
    # is the plain, uncorrupted redundant-safety case: request.json
    # unchanged, state.json's id intact, dedup alone must already refuse
    # to redo the mutating half of the flow.
    _write_request(unhealthy_service, target="0.3.0")
    _, first_calls, state = unhealthy_service()
    assert state["phase"] == "failed"
    assert state["rolled_back"] is True
    _, second_calls, second_state = unhealthy_service()
    assert second_state["id"] == state["id"]
    assert len(_compose_calls(second_calls, "up")) == len(_compose_calls(first_calls, "up"))
    assert len(_compose_calls(second_calls, "pull")) == len(_compose_calls(first_calls, "pull"))


def test_a_stale_failure_file_is_cleared_when_a_different_request_is_accepted(updater):
    # Minor. Proven against the unpatched script: complete a rollback
    # (writes LETZTER-FEHLSCHLAG.txt), then let a DIFFERENT, later
    # request fail at a stage that never reaches the rollback section at
    # all (`git fetch`) - the file titled "last failed update attempt"
    # still carried the OLDER attempt's timestamp and versions, wrongly
    # describing the CURRENT failure. The plain-text file exists to
    # narrate a rollback specifically (state.json's own "error" field
    # already covers every other failure mode), so a request that fails
    # before ever reaching that section should leave no such file at all.
    (updater.update_dir / "LETZTER-FEHLSCHLAG.txt").write_text(
        "stale rollback story", encoding="utf-8"
    )
    git_path = updater.bindir / "git"
    git_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "git" "$*" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        '  *"fetch --tags --force origin"*) exit 1 ;;\n'
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    git_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, _calls, state = updater()
    assert state["phase"] == "failed"
    assert state["error"] == "git fetch failed"
    assert not (updater.update_dir / "LETZTER-FEHLSCHLAG.txt").exists()


# ------------------------------------------------ Boundary-crossing fixes --
# Three Criticals from a whole-branch review, all one root cause: this
# feature had never crossed a container boundary. See
# .superpowers/sdd/final-fix-boundary-report.md for the full write-up of
# each; the tests below are what would have caught them.


def test_compose_resolves_the_host_path_not_the_container_path(updater):
    # Critical 1 - "compose inside a container resolves relative bind
    # mounts to container paths". compose() used to `cd "$STACK"` (a
    # CONTAINER path, e.g. /repo/deploy/testhost - bind-mounted from the
    # host's checkout) and run `docker compose` with no
    # --project-directory. Compose resolves every relative `volumes:`
    # entry against ITS OWN project directory (the -f file's directory,
    # absent an explicit override) and hands the DAEMON whatever that
    # resolves to - a HOST path of the same spelling, which on the real
    # host does not exist under that name at all.
    #
    # This fixture models a genuine container/host split: $LOXMATTER_STACK
    # is a real directory (the `[ ! -d ]` guard in compose() passes) but
    # the docker stub answers a DIFFERENT path as its actual host source -
    # the way `docker inspect loxmatter-updater --format
    # '{{range .Mounts}}...'` would against a real bind mount. What must
    # show up in the ACTUAL `docker compose` invocation is that different
    # host path, carried via --project-directory - never $LOXMATTER_STACK
    # itself.
    host_checkout = "/home/pi/loxmatter-checkout"
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "docker" "$*" >> "$STUB_LOG"\n'
        'case "$1" in\n'
        "  inspect)\n"
        '    if [ "$2" = "loxmatter-updater" ]; then\n'
        f'      printf "%s %s\\n" "$LOXMATTER_REPO" "{host_checkout}"\n'
        f'      printf "%s %s\\n" "$LOXMATTER_STACK" "{host_checkout}/deploy/testhost"\n'
        "    else\n"
        '      printf "LOXMATTER_VERSION=0.2.0\\n"\n'
        "    fi\n"
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, calls, state = updater()
    assert state["phase"] == "done"
    pull_line = _compose_calls(calls, "pull")[0]
    assert f"--project-directory {host_checkout}/deploy/testhost" in pull_line
    # -f still names the CONTAINER path - this sidecar can only read its
    # own filesystem, and it is the same file either way (bind-mounted).
    assert f"-f {updater.stack}/docker-compose.yml" in pull_line
    # The CONTAINER path must not appear as the resolved project
    # directory - that would be the bug this test exists to catch,
    # reached silently.
    assert f"--project-directory {updater.stack}" not in pull_line


@pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="drives the real docker compose binary to resolve a real docker-compose.yml",
)
def test_the_env_file_survives_the_project_directory_moving_to_the_host(updater):
    # The sibling of Critical 1 above, same root cause, found later: fixing
    # --project-directory to point at the HOST (so relative `volumes:`
    # entries resolve correctly) moves a SECOND lookup with it - Compose's
    # own search for `.env` - onto that same host path, which this
    # CONTAINER cannot read at all. Compose does not error on a missing
    # `.env`; it warns per undefined variable and substitutes an empty
    # string, so a recreate under that empty environment still reports
    # success. That is exactly what reached production: `--miniserver ""`
    # and an empty `LOXMATTER_API_TOKEN`, a bridge that came up and
    # answered `/health` anyway, and a green "Now running: 0.3.2" tile.
    #
    # Every OTHER test in this file stubs `docker compose ...` as a bare
    # `exit 0` (or a fixed failure), which proves nothing about `.env`:
    # nothing ever asks Compose to actually READ one. This test instead
    # translates each `docker compose ... pull|up ...` call update-once.sh
    # makes into `docker compose <same -f/--project-directory/--env-file
    # flags> config` against the REAL `docker` binary and the REAL
    # deploy/testhost/docker-compose.yml - i.e. it asks Compose itself
    # what it would resolve `--miniserver` to, under the exact flags
    # compose() actually built. `--project-directory` below is a literal,
    # never-created path (the same style the "host_checkout" tests above
    # already use) - proven above to need no host directory to actually
    # exist for `docker compose config` to run; what matters is only that
    # NO `.env` sits there, which is the whole point: a fixed function
    # finds `.env` via `--env-file` regardless, a broken one does not find
    # it at all and falls back to Compose's own empty-string default.
    real_docker = shutil.which("docker")
    host_checkout = "/home/pi/loxmatter-checkout"
    miniserver_ip = (
        "203.0.113.42"  # TEST-NET-3 (RFC 5737) - reserved for documentation, never a real host.
    )
    config_out = updater.bindir.parent / "resolved-config.yml"

    shutil.copy(
        ROOT / "deploy" / "testhost" / "docker-compose.yml", updater.stack / "docker-compose.yml"
    )
    with (updater.stack / ".env").open("a", encoding="utf-8") as env_file:
        env_file.write(f"MINISERVER_IP={miniserver_ip}\n")

    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "docker" "$*" >> "$STUB_LOG"\n'
        'case "$1" in\n'
        "  inspect)\n"
        '    if [ "$2" = "loxmatter-updater" ]; then\n'
        f'      printf "%s %s\\n" "$LOXMATTER_REPO" "{host_checkout}"\n'
        f'      printf "%s %s\\n" "$LOXMATTER_STACK" "{host_checkout}/deploy/testhost"\n'
        "    else\n"
        '      printf "LOXMATTER_VERSION=0.2.0\\n"\n'
        "    fi\n"
        "    ;;\n"
        "  compose)\n"
        "    shift\n"
        # compose() always emits some prefix of -f/--project-directory/
        # --env-file flag+value pairs before the real subcommand
        # (pull/up) - consumed generically here (rather than at fixed
        # positions) so this stub keeps working when the fix under test
        # is reverted for the bite-check below, where --env-file is
        # simply absent from that prefix.
        "    flags=\n"
        '    while [ "$#" -gt 0 ]; do\n'
        '      case "$1" in\n'
        "        -f|--project-directory|--env-file)\n"
        '          flags="$flags $1 $2"\n'
        "          shift 2\n"
        "          ;;\n"
        "        *) break ;;\n"
        "      esac\n"
        "    done\n"
        # The rest ($@: the real subcommand plus its own flags and the
        # service name) is deliberately dropped - `config` takes none of
        # that, and this call must never touch a real image or a real
        # container regardless of what update-once.sh asked for.
        f'    "{real_docker}" compose $flags config > "$CONFIG_OUT" 2>>"$STUB_LOG" || true\n'
        "    exit 0\n"
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker_path.chmod(0o755)

    _write_request(updater, target="0.3.0")
    _, calls, state = updater(CONFIG_OUT=str(config_out))
    assert state["phase"] == "done"
    assert _compose_calls(calls, "pull"), (
        "compose() was never called - nothing for this test to check"
    )

    assert config_out.exists(), "the docker stub's translated `config` call never ran"
    resolved = yaml.safe_load(config_out.read_text(encoding="utf-8"))
    command = resolved["services"]["loxmatter"]["command"]
    assert "--miniserver" in command, command
    assert command[command.index("--miniserver") + 1] == miniserver_ip, (
        "MINISERVER_IP did not survive --project-directory moving to the host - "
        f"resolved command was {command!r}"
    )


def test_compose_refuses_when_the_host_path_cannot_be_resolved(updater):
    # Critical 1, the other half: when host_path_for() cannot resolve
    # $STACK to a host path at all (no mount whose Destination is a
    # prefix of it - the daemon unreachable, or this sidecar's own mount
    # table not shaped as expected), compose() must not silently fall
    # back to the container path - that would reproduce the exact bug
    # above by a different route. It must refuse to run `docker compose`
    # at all: a call that never ran is retryable, one that mounted the
    # wrong host directory is not.
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "docker" "$*" >> "$STUB_LOG"\n'
        'case "$1" in\n'
        '  inspect) printf "LOXMATTER_VERSION=0.2.0\\n" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, calls, state = updater()
    assert state["phase"] == "failed"
    assert not _compose_calls(calls, "pull")
    assert "docker compose" not in calls


def test_git_calls_carry_safe_directory_and_chown_the_checkout_back(updater):
    # Critical 2 - the git ownership problem. This sidecar runs as root;
    # install.sh clones $REPO as the invoking user. Both wrapper functions
    # (git_repo/run_git in update-once.sh) must pass `-c
    # safe.directory=$REPO` on EVERY invocation (the fix for git's
    # "detected dubious ownership" refusal) AND chown the checkout back to
    # its original owner afterward (the fix for the second half: a write
    # left root-owned would then lock the operator's own future
    # `git`/./scripts/update.sh out of their own checkout).
    #
    # `chown` AND `stat` are both faked here, not the real binaries the
    # fixture's SYSTEM_TOOLS otherwise symlinks in - purely to OBSERVE the
    # call chain, and to sidestep a real cross-platform gap: `stat -c` is
    # a GNU-ism (the sidecar's Alpine base has it via the `coreutils`
    # package, see deploy/updater/Dockerfile), but this suite may run on
    # a host whose OWN `stat` is BSD's (no `-c` at all), which would make
    # REPO_UID/REPO_GID come back empty for a reason that has nothing to
    # do with the fix under test - and chown_repo_back()'s own guard
    # would then correctly, but unhelpfully, skip the chown entirely. The
    # fake `stat` answers a fixed uid/gid regardless of platform; a real
    # chown here would only ever restore the test's own tmp directory to
    # the test's own uid, a no-op that proves nothing extra, so the fake
    # `chown` just logs and exits.
    stat_path = updater.bindir / "stat"
    stat_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "stat" "$*" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        '  *"%u"*) echo 4242 ;;\n'
        '  *"%g"*) echo 4343 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    stat_path.chmod(0o755)
    chown_path = updater.bindir / "chown"
    chown_path.write_text(
        '#!/bin/sh\nprintf "chown %s\\n" "$*" >> "$STUB_LOG"\nexit 0\n', encoding="utf-8"
    )
    chown_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, calls, state = updater()
    assert state["phase"] == "done"
    git_calls = [line for line in calls.splitlines() if line.startswith("git ")]
    assert git_calls, "no git call was made at all"
    for line in git_calls:
        assert "safe.directory=" in line, line
    chown_calls = [line for line in calls.splitlines() if line.startswith("chown ")]
    assert chown_calls, "the checkout was never chowned back after a git write"
    for line in chown_calls:
        assert "4242:4343" in line, line


def test_a_broken_git_surfaces_as_a_failure_instead_of_a_silent_head_fallback(updater):
    # Critical 2, second half: `git_before_raw`'s old `|| true` collapsed
    # EVERY git failure - dubious ownership among them - into the same
    # GIT_BEFORE="HEAD" fallback a genuinely empty, freshly-cloned
    # repository legitimately needs. A `git rev-parse HEAD` that exits
    # NON-ZERO is a real, actionable failure and must abort the pass
    # instead of silently proceeding as if nothing were wrong: proceeding
    # would let a later rollback `git checkout --detach HEAD` run as a
    # no-op (HEAD already sits on the very ref this pass itself just
    # checked out), leaving the Compose file at the FAILED version while
    # the image rolls back to the old one.
    git_path = updater.bindir / "git"
    git_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "git" "$*" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        '  *"rev-parse HEAD"*) echo "fatal: detected dubious ownership" >&2; exit 128 ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    git_path.chmod(0o755)
    _write_request(updater, target="0.3.0")
    _, calls, state = updater()
    assert state["phase"] == "failed"
    assert "could not determine the currently checked-out commit" in state["error"]
    assert "checkout --detach" not in calls
    assert "fetch --tags --force origin" not in calls
