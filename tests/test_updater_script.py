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
import subprocess
import time
from pathlib import Path

import pytest

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

    def run(_timeout=None, **extra_env):
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

    run.update_dir = update_dir
    run.stack = stack
    run.bindir = bindir
    run.backup_dir = tmp_path / "data" / "backups"
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
    _auftrag(updater, target="0.3.0\nrm -rf /")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_a_foreign_registry_target_with_an_embedded_newline_is_rejected(updater):
    _auftrag(updater, target="evil.example.com/loxmatter:latest\n0.3.0")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


def test_a_dev_target_with_an_embedded_newline_is_rejected(updater):
    _auftrag(updater, channel="dev", target="abcdef1\n; wget evil")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert "docker" not in calls


# The `dev` pattern check (`^[0-9a-f]{7,40}$`) is the only validation a
# dev-channel request meets in this half of the script - nothing else
# would notice if it were wrong, or missing, without these three.
def test_a_valid_dev_target_is_accepted(updater):
    # Validating a dev target still never needs a `docker inspect` call:
    # that read only answers "what is running", needed for the stable
    # channel's semver comparison (Rule 3) - the dev channel's own
    # forward-only equivalent (Task 3's ancestry check) reads via `git`
    # instead. Since Task 3, though, an accepted request no longer stops
    # at "queued": the flow runs it straight through, so this well-formed
    # commit target does end up making `docker compose pull`/`up` calls -
    # what this test still pins down is that no INSPECT call happens.
    _auftrag(updater, channel="dev", target="abcdef1")
    _, calls, state = updater()
    assert state["phase"] == "done"
    assert "docker inspect" not in calls


def test_a_malformed_dev_target_is_rejected(updater):
    _auftrag(updater, channel="dev", target="not-a-commit-sha")
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
    _auftrag(updater, id="")
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
    #
    # This target is well-formed - it has to be, to test rule 3 at all -
    # so answering "is 0.1.0 newer than what's running" costs one
    # `docker inspect` (see running_version()). That call is legitimate;
    # what must not happen is anything that changes host state.
    _auftrag(updater, target="0.1.0")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert _mutating_docker_calls(calls) == []


def test_the_same_version_is_rejected(updater):
    # Same reasoning as test_an_older_version_is_rejected above: a
    # well-formed target that ties the running version also needs the one
    # read-only `docker inspect` to know that it ties.
    _auftrag(updater, target="0.2.0")
    _, calls, state = updater()
    assert state["phase"] == "rejected"
    assert _mutating_docker_calls(calls) == []


def test_a_valid_target_is_accepted(updater):
    _auftrag(updater, target="0.3.0")
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
    # just a wrong `to`.
    _auftrag(updater, target="0.3.0")
    _, erste_calls, erste = updater()
    assert erste["phase"] == "done"
    assert erste["to"] == "0.3.0"
    assert erste_calls.count("compose pull") == 1

    _auftrag(updater, target="0.4.0")  # same id "auftrag-1", new target
    _, zweite_calls, zweite = updater()
    assert zweite["id"] == "auftrag-1"
    assert zweite["to"] == "0.3.0"
    assert zweite_calls.count("compose pull") == 1


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
    _auftrag(updater, target=huge_target)
    result, calls, state = updater()
    assert result.returncode == 0
    assert state is not None
    assert state["phase"] == "rejected"
    assert state["error"] == "target is too long"
    assert "docker" not in calls


def test_an_oversized_id_is_rejected(updater):
    # Critical, the id half: unbounded, an id reaches the exact same
    # set_state E2BIG failure the target-length test above exercises.
    _auftrag(updater, id="x" * 200)
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
    _auftrag(updater, id=evil_id)
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
    _auftrag(updater, channel="invalid-channel")
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
    _auftrag(updater, channel="invalid-channel")
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
    _auftrag(updater, target="v0.3.0")
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
    _auftrag(updater, target="0.3.0")
    _, calls, state = updater()
    assert calls.index("tar") < calls.index("compose pull")
    assert calls.index("compose pull") < calls.index("compose up")
    assert state["phase"] == "done"


def test_the_restart_leaves_the_neighboring_services_alone(updater):
    _auftrag(updater, target="0.3.0")
    _, calls, _ = updater()
    up = next(line for line in calls.splitlines() if "compose up" in line)
    assert "--no-deps" in up
    assert "loxmatter-updater" not in up


def test_the_image_name_does_not_come_from_the_job(updater):
    # `calls` is the FAKE BINARIES' call log (docker/git/curl/tar) - the
    # image name is deliberately never an argument to any of them (see
    # the comment above `log "target image: ..."` in update-once.sh), so
    # it cannot show up there. It shows up in the script's OWN log
    # (log.txt) instead, precisely because that is the one place meant to
    # record it for an operator without ever handing it to a command.
    _auftrag(updater, target="0.3.0")
    updater()
    log_text = (updater.update_dir / "log.txt").read_text(encoding="utf-8")
    assert "ghcr.io/lucienkerl/loxmatter" in log_text


def test_the_tag_lands_in_the_env_file(updater):
    _auftrag(updater, target="0.3.0")
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
    _auftrag(updater, target="0.3.0")
    updater()
    text = env.read_text(encoding="utf-8")
    assert "MINISERVER_IP=10.0.1.9" in text
    assert "RADIO_DEVICE=/dev/ttyUSB0" in text
    assert "LOXMATTER_IMAGE_TAG=0.3.0" in text
    assert "0.2.0" not in text


def test_a_backup_is_made_before_the_pull(updater):
    _auftrag(updater, target="0.3.0")
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
    _auftrag(updater, channel="dev", target="abcdef1")
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
    _auftrag(updater, target="0.3.0")
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
    curl_path = updater.bindir / "curl"
    curl_path.write_text("#!/bin/sh\nsleep 2\nexit 1\n", encoding="utf-8")
    curl_path.chmod(0o755)
    _auftrag(updater, target="0.3.0")
    start = time.monotonic()
    _, _, state = updater(LOXMATTER_HEALTH_TIMEOUT="2", _timeout=30)
    elapsed = time.monotonic() - start
    assert state["phase"] == "rollback"
    assert elapsed < 6, f"took {elapsed:.1f}s - the old iteration-counting loop took ~6-9s here"


def test_a_failed_restart_hands_off_to_rollback_instead_of_stranding_the_tag(updater):
    # Important 3. Proven against the unpatched script with a `compose
    # up` stub that exits 1: the run ended `phase: failed`, `error:
    # restart failed`, and .env was left reading the NEW tag forever -
    # neither restored (as the pull-failure path does) nor handed to the
    # rollback Task 4 attaches after `set_state rollback ""`. Since
    # `--force-recreate` removes the old container before creating its
    # replacement, the service can genuinely be down at this point, and
    # only a further `compose up` - what the rollback performs, against
    # the OLD image - can recover it; restoring just the tag would not
    # restart anything by itself.
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "docker" "$*" >> "$STUB_LOG"\n'
        'case "$1" in\n'
        '  inspect) printf "LOXMATTER_VERSION=0.2.0\\n" ;;\n'
        '  compose) [ "$2" = "up" ] && exit 1; exit 0 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _auftrag(updater, target="0.3.0")
    _, _calls, state = updater()
    assert state["phase"] == "rollback"
    assert (updater.stack / ".env").read_text(encoding="utf-8") == "LOXMATTER_IMAGE_TAG=0.3.0\n"


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
        _auftrag(updater, target="0.3.0")
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
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "docker" "$*" >> "$STUB_LOG"\n'
        'case "$1" in\n'
        '  inspect) printf "LOXMATTER_VERSION=0.2.0\\n" ;;\n'
        "  compose)\n"
        '    if [ "$2" = "pull" ]; then chmod 0555 "$LOXMATTER_STACK"; exit 1; fi\n'
        "    exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _auftrag(updater, target="0.3.0")
    try:
        result, _calls, state = updater()
    finally:
        updater.stack.chmod(0o755)
    assert result.returncode == 0
    assert state is not None
    assert state["phase"] == "failed"
    assert "restored" in state["error"]


def test_an_unhealthy_service_falls_through_to_rollback_not_done(updater):
    # Kills three of Important 5's five surviving mutants at once:
    # `wait_healthy() { return 0; ... }` (the health gate deleted
    # entirely), `curl -fsS` weakened to `curl -sS` (an HTTP 500 then
    # counts as healthy), and the final `set_state rollback ""` replaced
    # with `:` (the hand-off this task exists to produce for Task 4).
    # This curl stub only SUCCEEDS when invoked WITHOUT `-f` - i.e. it
    # plays a real curl seeing an HTTP 500: with `-f` present (the
    # correct code), that is a failure; drop `-f` (one of the mutants)
    # and the identical response counts as success. Under the "return 0"
    # mutant curl is never even consulted; under the "set_state rollback"
    # -> ":" mutant, phase is left at whatever `set_state health ""` (the
    # immediately preceding write) set it to. All three converge on the
    # same wrong answer this test rules out: phase != "rollback".
    curl_path = updater.bindir / "curl"
    curl_path.write_text(
        '#!/bin/sh\ncase "$*" in\n  *-f*) exit 1 ;;\n  *) exit 0 ;;\nesac\n',
        encoding="utf-8",
    )
    curl_path.chmod(0o755)
    _auftrag(updater, target="0.3.0")
    _, _calls, state = updater()
    assert state["phase"] == "rollback"


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
    _auftrag(updater, target="0.3.0")
    _, calls, _state = updater()
    assert "checkout --detach v0.3.0" in calls


def test_the_target_is_fetched_before_checkout(updater):
    # Kills the mutant that deletes the `git fetch` block entirely - a
    # checkout could then only ever succeed against whatever the
    # repository already happened to have locally, silently, with
    # nothing here to notice a target that was never actually fetched.
    _auftrag(updater, target="0.3.0")
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
    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "docker" "$*" >> "$STUB_LOG"\n'
        'case "$1" in\n'
        '  inspect) printf "LOXMATTER_VERSION=0.2.0\\n" ;;\n'
        '  compose) [ "$2" = "pull" ] && exit 1; exit 0 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    docker_path.chmod(0o755)
    _auftrag(updater, target="0.3.0")
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
    _auftrag(updater, target="0.3.0")
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
    _auftrag(updater, target="0.3.0")
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

    docker_path = updater.bindir / "docker"
    docker_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "docker" "$*" >> "$STUB_LOG"\n'
        'case "$1" in\n'
        '  inspect) printf "LOXMATTER_VERSION=0.2.0\\n" ;;\n'
        '  compose) [ "$2" = "pull" ] && exit 1; exit 0 ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    docker_path.chmod(0o755)

    _auftrag(updater, target="0.3.0")
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
    _auftrag(updater, target="0.3.0")
    _, _calls, state = updater()
    assert state["phase"] == "failed"
    assert "not found in the repository" not in state["error"]


def test_a_missing_ref_is_still_reported_as_not_found(updater):
    # Companion to the test above: confirms the "not found" wording is
    # still reachable, and specifically for the case it now means -
    # `rev-parse` itself says the ref does not exist - with `checkout`
    # never even attempted for a ref already known not to exist.
    git_path = updater.bindir / "git"
    git_path.write_text(
        "#!/bin/sh\n"
        'printf "%s %s\\n" "git" "$*" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        "  *rev-parse*) exit 1 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    git_path.chmod(0o755)
    _auftrag(updater, target="0.3.0")
    _, calls, state = updater()
    assert state["phase"] == "failed"
    assert "not found in the repository" in state["error"]
    assert "checkout --detach" not in calls
