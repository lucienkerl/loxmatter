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

"""Behavioural tests for deploy/updater/matter-server-once.sh (design
2026-11-22 "matter-server follows its pinned image").

The same approach as tests/test_updater_radios_script.py: a sealed PATH of
fake binaries, and what is checked is which commands the script chooses. The
data directory is real, and so is `tar`: the backup and the restore are
checked by what ends up on disk."""

from __future__ import annotations

import json
import re
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "updater" / "matter-server-once.sh"
OLD_IMAGE = "ghcr.io/matter-js/matterjs-server:stable"
OLD_IMAGE_ID = "sha256:1400aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NEW_IMAGE = "ghcr.io/matter-js/matterjs-server:1.6.0"
SYSTEM_TOOLS = (
    "sh",
    "cat",
    "grep",
    "sed",
    "awk",
    "tr",
    "printf",
    "mkdir",
    "rmdir",
    "rm",
    "mv",
    "date",
    "head",
    "tail",
    "cut",
    "wc",
    "cksum",
    "timeout",
    "ls",
    "tar",
    "gzip",
    "jq",
    "kill",
)

# The script's clock, `date +%s`, is the number in $FAKE/epoch; the `sleep`
# stub adds its argument to it.
ADVANCE = r"""
advance() {
  [ -f "$1" ] || return 0
  read -r by < "$1"; read -r at < "$FAKE/epoch"
  echo $((at + by)) > "$FAKE/epoch"
}
"""

# One container at a time, described by files in $FAKE:
#   ups            how many times it was recreated (0: the original)
#   running        present while it runs
#   config_image   the reference it was created from (`.Config.Image`)
#   image_id       the image it runs (`.Image`)
#   nodes_<ups>    what that container's log says: "R of T", or "none"
#                  (default "27 of 27")
# A recreate (`compose up`) makes a new container from the image Compose
# resolves - the override file's when one is given, the configured one
# otherwise - and, on its first time, lets the "new image" leave a file in
# ./data, the way a migration would.
DOCKER_STUB = (
    r"""#!/bin/sh
printf 'docker %s\n' "$*" >> "$STUB_LOG"
"""
    + f"OLD_IMAGE='{OLD_IMAGE}'\nOLD_IMAGE_ID='{OLD_IMAGE_ID}'\nNEW_IMAGE='{NEW_IMAGE}'\n"
    + ADVANCE
    + r"""
image_id_of() {
  case "$1" in
    sha256:*) printf '%s' "$1" ;;
    "$OLD_IMAGE") cat "$FAKE/old_tag_id" 2>/dev/null || printf '%s' "$OLD_IMAGE_ID" ;;
    *) printf 'sha256:%s' "$(printf '%s' "$1" | cksum | cut -d ' ' -f 1)" ;;
  esac
}
ups="$(cat "$FAKE/ups" 2>/dev/null || echo 0)"
case "$1" in
  inspect)
    [ -f "$FAKE/no_container" ] && { echo '[]'; exit 1; }
    if [ -f "$FAKE/running" ]; then running=true; else running=false; fi
    printf '[{"Id": "ms-%s", "Image": "%s", "Config": {"Image": "%s"}, "State": {"Running": %s, "StartedAt": "%s"}}]\n' \
      "$ups" \
      "$(cat "$FAKE/image_id" 2>/dev/null || echo "$OLD_IMAGE_ID")" \
      "$(cat "$FAKE/config_image" 2>/dev/null || echo "$OLD_IMAGE")" \
      "$running" \
      "$(cat "$FAKE/started_at" 2>/dev/null || echo 2026-08-01T00:00:00Z)"
    exit 0 ;;
  image)
    # `docker image inspect [--format F] <ref>`: on the host when listed in
    # $FAKE/local_images (the old image always is).
    for ref; do :; done
    case "$ref" in
      sha256:*|"$OLD_IMAGE") ;;
      *) grep -qxF "$ref" "$FAKE/local_images" 2>/dev/null || exit 1 ;;
    esac
    case "$*" in
      *RepoTags*) cat "$FAKE/repo_tag" 2>/dev/null || echo "$OLD_IMAGE" ;;
      *.Id*) image_id_of "$ref"; echo ;;
    esac
    exit 0 ;;
  logs)
    advance "$FAKE/logs_seconds"
    nodes="$(cat "$FAKE/nodes_$ups" 2>/dev/null || echo '27 of 27')"
    echo '2026-11-22 12:00:00.000 INFO MatterServer Starting matterjs-server'
    [ "$nodes" = none ] || echo "2026-11-22 12:00:30.000 INFO MatterController Registered $nodes nodes"
    exit 0 ;;
  stop)
    [ -f "$FAKE/stop_fail" ] && exit 1
    rm -f "$FAKE/running"; exit 0 ;;
  start)
    [ -f "$FAKE/start_fail" ] && exit 1
    : > "$FAKE/running"; exit 0 ;;
  compose)
    case "$*" in *"$(cat "$FAKE/compose_fail" 2>/dev/null || echo __never__)"*) exit 1 ;; esac
    case "$*" in
      *" config "*)
        [ -f "$FAKE/config_fail" ] && exit 1
        data="$(cat "$FAKE/compose_data" 2>/dev/null || echo "$LOXMATTER_STACK_HOST_PATH/data")"
        printf '{"name": "testhost", "services": {"matter-server": {"image": "%s", "volumes": [{"type": "bind", "source": "%s", "target": "/data"}, {"type": "bind", "source": "/run/dbus", "target": "/run/dbus", "read_only": true}]}}}\n' \
          "$(cat "$FAKE/compose_image" 2>/dev/null || echo "$NEW_IMAGE")" "$data" ;;
      *" pull "*matter-server*)
        status="$(cat "$FAKE/pull_status" 2>/dev/null || echo 0)"
        if [ "$status" -eq 0 ]; then
          printf '%s\n' "$(cat "$FAKE/compose_image" 2>/dev/null || echo "$NEW_IMAGE")" >> "$FAKE/local_images"
        fi
        exit "$status" ;;
      *" up "*matter-server*)
        if [ -f "$FAKE/kill_on_up" ]; then
          rm -f "$FAKE/kill_on_up"; kill -KILL "$PPID"; exit 1
        fi
        image="$(cat "$FAKE/compose_image" 2>/dev/null || echo "$NEW_IMAGE")"
        case "$*" in
          *matter-server-rollback.yml*)
            image="$(sed -n 's/^ *image: "\(.*\)"$/\1/p' "$UPDATE_DIR_FOR_STUB/matter-server-rollback.yml")"
            printf 'override image=%s\n' "$image" >> "$STUB_LOG" ;;
        esac
        printf '%s\n' "$image" > "$FAKE/config_image"
        image_id_of "$image" > "$FAKE/image_id"
        echo $((ups + 1)) > "$FAKE/ups"
        : > "$FAKE/running"
        if [ "$ups" -eq 0 ]; then
          echo migrated > "$LOXMATTER_STACK/data/written-by-new-image"
          echo new-format > "$LOXMATTER_STACK/data/chip.json"
        fi ;;
    esac
    exit 0 ;;
esac
exit 0
"""
)

START_EPOCH = 1_763_812_800

DATE_STUB_TEMPLATE = """#!/bin/sh
case "$*" in
  *%s*) read -r at < "$FAKE/epoch"; echo "$at" ;;
  *) exec {real_date} "$@" ;;
esac
"""

SLEEP_STUB = (
    r"""#!/bin/sh
printf '%s\n' "$1" > "$FAKE/slept"
"""
    + ADVANCE
    + r"""
advance "$FAKE/slept"
[ -n "${HEARTBEAT_LOG:-}" ] && printf '%s %s %s %s\n' \
  "$(jq -r '.phase' "$UPDATE_DIR_FOR_STUB/matter-server-state.json" 2>/dev/null)" \
  "$(jq -r '.seen_at' "$UPDATE_DIR_FOR_STUB/matter-server-state.json" 2>/dev/null)" \
  "$(jq -r '.seen_at' "$UPDATE_DIR_FOR_STUB/radios-state.json" 2>/dev/null)" \
  "$(jq -r '.updater_seen_at' "$UPDATE_DIR_FOR_STUB/state.json" 2>/dev/null)" \
  >> "$HEARTBEAT_LOG"
exit 0
"""
)

# Advances one second per call, so the timestamps a heartbeat writes differ.
CLOCK_DATE_STUB = r"""#!/bin/sh
case "$*" in
  *%s*) read -r at < "$FAKE/epoch"; echo "$at"; exit 0 ;;
esac
n=$(cat "$FAKE/clock" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$FAKE/clock"
case "$*" in
  *%Y%m%d%H%M%S*) printf '202611221200%02d\n' $((n % 100)) ;;
  *) printf '2026-11-22T12:%02d:%02dZ\n' $((n / 60)) $((n % 60)) ;;
esac
"""


@pytest.fixture
def ms(tmp_path):
    """Returns run() -> (result, calls, state). Attributes expose the paths
    tests arrange: stack, data, update_dir, backup_dir, fake."""
    bindir, sysdir, fake = tmp_path / "bin", tmp_path / "sys-tools", tmp_path / "fake"
    for d in (bindir, sysdir, fake):
        d.mkdir()
    log = tmp_path / "stub.log"
    update_dir = tmp_path / "store" / "update"
    update_dir.mkdir(parents=True)
    backup_dir = tmp_path / "store" / "backups"
    stack = tmp_path / "repo" / "deploy" / "testhost"
    stack.mkdir(parents=True)
    (stack / ".env").write_text("LOXMATTER_IMAGE_TAG=0.8.0\nBLUETOOTH_ADAPTER=0\n")
    (stack / "docker-compose.yml").write_text("services: {}\n")
    data = stack / "data"
    (data / "credentials").mkdir(parents=True)
    (data / "chip.json").write_text("fabric\n")
    (data / "4711.json").write_text('{"nodes": {}}\n')
    (data / "credentials" / "paa.der").write_text("cert\n")
    (fake / "epoch").write_text(f"{START_EPOCH}\n")
    (fake / "running").write_text("")

    real_date = subprocess.run(
        ["which", "date"], capture_output=True, text=True, check=False
    ).stdout.strip()
    for name, body in (
        ("docker", DOCKER_STUB),
        ("sleep", SLEEP_STUB),
        ("date", DATE_STUB_TEMPLATE.format(real_date=real_date)),
    ):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    for tool in SYSTEM_TOOLS:
        real = subprocess.run(
            ["which", tool], capture_output=True, text=True, check=False
        ).stdout.strip()
        if real:
            (sysdir / tool).symlink_to(real)

    def run(timeout=30, **extra_env):
        env = {
            "PATH": f"{bindir}:{sysdir}",
            "STUB_LOG": str(log),
            "FAKE": str(fake),
            "UPDATE_DIR_FOR_STUB": str(update_dir),
            "LOXMATTER_UPDATE_DIR": str(update_dir),
            "LOXMATTER_BACKUP_DIR": str(backup_dir),
            "LOXMATTER_STACK": str(stack),
            "LOXMATTER_STACK_HOST_PATH": str(stack),
            "LOXMATTER_MATTER_SERVER_VERIFY_TIMEOUT": "20",
            "LOXMATTER_MATTER_SERVER_POLL_SECONDS": "5",
            **extra_env,
        }
        result = subprocess.run(
            [str(SCRIPT)], capture_output=True, text=True, env=env, check=False, timeout=timeout
        )
        calls = log.read_text() if log.exists() else ""
        state_file = update_dir / "matter-server-state.json"
        state = json.loads(state_file.read_text()) if state_file.is_file() else None
        return result, calls, state

    run.stack, run.data, run.update_dir, run.backup_dir = stack, data, update_dir, backup_dir
    run.fake, run.log, run.bindir = fake, log, bindir
    return run


def _compose(calls: str, *words: str) -> list[str]:
    return [
        line
        for line in calls.splitlines()
        if line.startswith("docker compose") and all(word in line.split() for word in words)
    ]


def _log_text(ms) -> str:
    path = ms.update_dir / "matter-server-log.txt"
    return path.read_text() if path.exists() else ""


def _mutating(calls: str) -> list[str]:
    return [
        line
        for line in calls.splitlines()
        if line.startswith(("docker stop", "docker start"))
        or _compose(line, "up")
        or _compose(line, "pull")
    ]


def _pulled(ms) -> None:
    """Runs the pass that pulls the pinned image: it ends after the pull and
    changes nothing else. Clears the stub log afterwards."""
    result, calls, state = ms()
    assert result.returncode == 0, result.stderr
    assert len(_compose(calls, "pull", "--quiet", "matter-server")) == 1
    assert _compose(calls, "up") == [] and "docker stop" not in calls
    assert state is None
    assert not (ms.update_dir / "matter-server-upkeep-tried").exists()
    assert f"pulled {NEW_IMAGE} - matter-server is moved to it on the next pass" in _log_text(ms)
    ms.log.unlink(missing_ok=True)


def _archive_names(path: Path) -> set[str]:
    with tarfile.open(path) as archive:
        return {member.name for member in archive.getmembers()}


# ------------------------------------------------------------- no action --


def test_a_matter_server_on_the_pinned_image_is_left_alone(ms):
    """Fault to prove it: compare against anything but `.Config.Image` - the
    pass stops and recreates a matter-server that is already right."""
    (ms.fake / "config_image").write_text(NEW_IMAGE)
    result, calls, state = ms()
    assert result.returncode == 0, result.stderr
    assert _mutating(calls) == []
    assert "docker logs" not in calls
    assert state is None
    assert "runs ghcr.io/matter-js/matterjs-server:1.6.0, as its Compose configuration asks" in (
        _log_text(ms)
    )


def test_the_compose_configuration_is_read_again_only_when_its_inputs_change(ms):
    """Fault to prove it: drop the cache - every pass runs `compose config`."""
    (ms.fake / "config_image").write_text(NEW_IMAGE)
    _, calls, _ = ms()
    assert len(_compose(calls, "config")) == 1
    ms.log.unlink(missing_ok=True)
    _, calls, _ = ms()
    assert _compose(calls, "config") == []
    ms.log.unlink(missing_ok=True)
    (ms.stack / ".env").write_text((ms.stack / ".env").read_text() + "BLUETOOTH_ADAPTER=1\n")
    _, calls, _ = ms()
    assert len(_compose(calls, "config")) == 1


@pytest.mark.parametrize("guard", ["update", "radios", "host_path", "container", "config"])
def test_the_job_is_skipped_when_it_must_not_touch_matter_server(ms, guard):
    """Fault to prove it (update): drop the running-update guard - the pass
    pulls in the middle of an update."""
    extra = {}
    if guard == "update":
        (ms.update_dir / "state.json").write_text(json.dumps({"phase": "recreate"}))
    elif guard == "radios":
        (ms.update_dir / "radios-state.json").write_text(json.dumps({"phase": "apply_bluetooth"}))
    elif guard == "host_path":
        extra["LOXMATTER_STACK_HOST_PATH"] = ""
    elif guard == "container":
        (ms.fake / "no_container").write_text("")
    else:
        (ms.fake / "config_fail").write_text("")
    result, calls, state = ms(**extra)
    assert result.returncode == 0, result.stderr
    assert _mutating(calls) == []
    assert state is None
    assert not (ms.update_dir / "matter-server-upkeep-tried").exists()
    if guard in ("update", "radios", "host_path"):
        assert "docker inspect" not in calls


def test_another_image_is_not_a_version_change(ms):
    """python-matter-server to matterjs-server is the migration that lost
    every node on 23 September 2026 when it ran unprepared.

    Fault to prove it: drop the repository comparison - the pass pulls
    matterjs-server for a python-matter-server installation."""
    (ms.fake / "config_image").write_text("ghcr.io/home-assistant-libs/python-matter-server:stable")
    result, calls, state = ms()
    assert result.returncode == 0, result.stderr
    assert _mutating(calls) == [] and state is None
    assert "another image, not another version of it" in _log_text(ms)
    assert "Migration to matterjs-server" in _log_text(ms)


def test_a_data_directory_outside_the_stack_is_not_touched(ms):
    """Fault to prove it: back up `$STACK/data` regardless of the volume -
    the job moves matter-server with a backup of the wrong directory."""
    (ms.fake / "compose_data").write_text("/srv/matter")
    result, calls, state = ms()
    assert result.returncode == 0, result.stderr
    assert _mutating(calls) == [] and state is None
    assert "/srv/matter is not under" in _log_text(ms)


def test_without_a_node_count_before_the_move_matter_server_stays(ms):
    """The count is the whole safety argument (design, section 7).

    Fault to prove it: go ahead when the log has no "Registered" line - the
    job runs with nothing to compare its result to."""
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    (ms.fake / "nodes_0").write_text("none")
    result, calls, state = ms()
    assert result.returncode == 0, result.stderr
    assert _mutating(calls) == [] and state is None
    assert "docker restart matter-server" in _log_text(ms)


def test_a_held_decision_does_not_read_the_log_every_pass_until_a_restart(ms):
    """`docker logs` of a matter-server that ran for weeks is tens of
    megabytes.

    Fault to prove it: drop the held record - every pass reads the log."""
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    (ms.fake / "nodes_0").write_text("none")
    ms()
    ms.log.unlink(missing_ok=True)
    _, calls, _ = ms()
    assert "docker logs" not in calls
    assert _log_text(ms).count("docker restart matter-server") == 1

    # The restart the log line asks for: a new start time, the line back.
    (ms.fake / "started_at").write_text("2026-11-22T13:00:00Z")
    (ms.fake / "nodes_0").write_text("27 of 27")
    ms.log.unlink(missing_ok=True)
    _, calls, state = ms()
    assert (state["phase"], state["healthy"]) == ("done", True)


# ------------------------------------------------------------------ pull --


def test_a_missing_image_is_pulled_in_one_pass_and_moved_to_in_the_next(ms):
    """Fault to prove it: go on to the job in the pass whose pull succeeded -
    the first pass stops matter-server."""
    _pulled(ms)
    result, calls, state = ms()
    assert result.returncode == 0, result.stderr
    assert _compose(calls, "pull") == []
    assert re.fullmatch(r"matter-server-upkeep-\d{14}", state["id"])
    assert (state["phase"], state["error"]) == ("done", None)
    assert (state["healthy"], state["rolled_back"]) == (True, False)
    assert (state["from"], state["to"]) == (OLD_IMAGE, NEW_IMAGE)
    assert (state["nodes_before"], state["nodes_after"]) == ("27 of 27", "27 of 27")
    assert (ms.fake / "config_image").read_text().strip() == NEW_IMAGE

    # The recreated container is what Compose asks for: nothing more to do.
    ms.log.unlink(missing_ok=True)
    _, calls, second = ms()
    assert _mutating(calls) == []
    assert second["id"] == state["id"]


def test_a_failed_pull_is_no_attempt_and_waits_1800_s(ms):
    (ms.fake / "pull_status").write_text("1")
    _, calls, state = ms()
    assert len(_compose(calls, "pull")) == 1 and state is None
    assert "could not pull" in _log_text(ms)
    failed_at = int((ms.update_dir / "matter-server-upkeep-pull-failed-at").read_text().split()[0])
    ms.log.unlink(missing_ok=True)
    (ms.fake / "epoch").write_text(f"{failed_at + 1799}\n")
    _, calls, _ = ms()
    assert _compose(calls, "pull") == []
    (ms.fake / "epoch").write_text(f"{failed_at + 1800}\n")
    (ms.fake / "pull_status").write_text("0")
    _, calls, _ = ms()
    assert len(_compose(calls, "pull")) == 1


# ------------------------------------------------------------------- job --


def test_the_move_stops_backs_up_and_then_recreates(ms):
    """Fault to prove it: back up while matter-server runs, or after the
    recreate - the archive races the server, or holds the new image's data."""
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    result, calls, state = ms()
    assert result.returncode == 0, result.stderr
    lines = calls.splitlines()
    stop = lines.index("docker stop matter-server")
    up = next(i for i, line in enumerate(lines) if _compose(line, "up"))
    assert stop < up
    up_line = lines[up].split()
    for word in ("-d", "--no-deps", "--force-recreate", "matter-server"):
        assert word in up_line
    assert "matter-server-rollback.yml" not in lines[up]

    archive = ms.backup_dir / state["backup"]
    assert re.fullmatch(r"matter-server-data-\d{14}\.tgz", state["backup"])
    names = _archive_names(archive)
    assert {"data/chip.json", "data/4711.json", "data/credentials/paa.der"} <= names
    assert "data/written-by-new-image" not in names
    with tarfile.open(archive) as tar:
        member = tar.extractfile("data/chip.json")
        assert member is not None and member.read() == b"fabric\n"


def test_only_the_newest_three_backups_are_kept(ms):
    ms.backup_dir.mkdir(parents=True)
    for i in range(4):
        old = ms.backup_dir / f"matter-server-data-2026010100000{i}.tgz"
        old.write_text("old")
    store = ms.backup_dir / "store-20260101000000.tgz"
    store.write_text("the database backup")
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    _, _, state = ms()
    kept = sorted(p.name for p in ms.backup_dir.glob("matter-server-data-*.tgz"))
    assert len(kept) == 3 and state["backup"] in kept
    assert store.exists()


def test_a_failed_backup_starts_the_old_matter_server_again(ms):
    """Fault to prove it: go on to the recreate without a backup."""
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    for child in sorted(ms.data.rglob("*"), reverse=True):
        if child.is_dir():
            child.rmdir()
        else:
            child.unlink()
    _, calls, state = ms()
    assert (state["phase"], state["error"]) == ("failed", "backup_failed")
    assert (state["rolled_back"], state["healthy"]) == (False, True)
    assert _compose(calls, "up") == []
    assert "docker start matter-server" in calls.splitlines()
    assert (ms.fake / "running").exists()
    assert not (ms.fake / "config_image").exists()


def test_a_matter_server_that_cannot_be_stopped_is_left_as_it_is(ms):
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    (ms.fake / "stop_fail").write_text("")
    _, calls, state = ms()
    assert (state["phase"], state["error"]) == ("failed", "stop_failed")
    assert _compose(calls, "up") == []
    assert not ms.backup_dir.exists() or not list(ms.backup_dir.iterdir())


@pytest.mark.parametrize(
    ("nodes_after", "reported"),
    [("0 of 0", "0 of 0"), ("26 of 27", "26 of 27"), ("27 of 28", "27 of 28"), ("none", None)],
)
def test_a_move_that_loses_nodes_is_rolled_back_with_the_backup(ms, nodes_after, reported):
    """The 23 September 2026 failure: the new server came up with 0 nodes.

    Fault to prove it: roll back the image without restoring the backup -
    `data` keeps the new image's files."""
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    (ms.fake / "nodes_1").write_text(nodes_after)
    result, calls, state = ms()
    assert result.returncode == 0, result.stderr
    assert (state["phase"], state["error"]) == ("failed", "verify_failed")
    assert (state["rolled_back"], state["healthy"]) == (True, True)
    assert state["nodes_before"] == "27 of 27"
    # The rollback's own verification overwrites the failed count.
    assert state["nodes_after"] == "27 of 27"
    if reported:
        assert f"registered {reported} nodes, 27 of 27 before" in _log_text(ms)

    ups = _compose(calls, "up", "matter-server")
    assert len(ups) == 2
    assert "matter-server-rollback.yml" in ups[1]
    assert f"override image={OLD_IMAGE}" in calls.splitlines()
    assert (ms.fake / "config_image").read_text().strip() == OLD_IMAGE

    # The backup is back; what the new image left is kept beside it.
    assert (ms.data / "chip.json").read_text() == "fabric\n"
    assert not (ms.data / "written-by-new-image").exists()
    assert (ms.data / "credentials" / "paa.der").exists()
    stamp = state["id"].removeprefix("matter-server-upkeep-")
    failed = ms.stack / f"data.failed-{stamp}"
    assert (failed / "written-by-new-image").exists()
    assert not (ms.stack / f"data.restore-{stamp}").exists()
    assert (ms.update_dir / f"{state['id']}.log").is_file()


def test_a_lower_count_fails_at_once_without_waiting(ms):
    """Fault to prove it: keep polling after a line with fewer nodes - the
    verification runs its full timeout."""
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    (ms.fake / "nodes_1").write_text("0 of 0")
    ms(LOXMATTER_MATTER_SERVER_VERIFY_TIMEOUT="240")
    assert int((ms.fake / "epoch").read_text()) == START_EPOCH


def test_a_more_complete_registration_counts_as_healthy(ms):
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    (ms.fake / "nodes_0").write_text("26 of 27")
    (ms.fake / "nodes_1").write_text("27 of 27")
    _, _, state = ms()
    assert (state["phase"], state["healthy"]) == ("done", True)


def test_a_failed_recreate_is_rolled_back_too(ms):
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    (ms.fake / "compose_fail").write_text("--quiet-pull")
    _, calls, state = ms()
    assert (state["phase"], state["error"]) == ("failed", "recreate_failed")
    assert state["rolled_back"] is True
    assert len(_compose(calls, "up")) == 2


def test_a_rollback_whose_tag_moved_uses_the_image_id(ms):
    """The old tag may name another image by now; the image ID is exact.

    Fault to prove it: always roll back by the reference - the rollback
    starts whatever the tag names today."""
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    (ms.fake / "old_tag_id").write_text("sha256:moved")
    (ms.fake / "nodes_1").write_text("0 of 0")
    _, calls, state = ms()
    assert f"override image={OLD_IMAGE_ID}" in calls.splitlines()
    assert state["healthy"] is True


def test_a_container_rolled_back_by_id_still_moves_to_a_later_version(ms):
    """Fault to prove it: compare the repository of a bare image ID - the
    installation is held forever as "another image"."""
    (ms.fake / "config_image").write_text(OLD_IMAGE_ID)
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    _, _, state = ms()
    assert (state["phase"], state["healthy"]) == ("done", True)


def test_a_failed_restore_leaves_matter_server_stopped(ms):
    """A server started on a half-restored directory can do more damage than
    one that is not running.

    Fault to prove it: start the previous image after a failed restore."""
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    (ms.fake / "nodes_1").write_text("0 of 0")
    # A `tar` that can write archives but not unpack them.
    real_tar = subprocess.run(
        ["which", "tar"], capture_output=True, text=True, check=False
    ).stdout.strip()
    (ms.bindir / "tar").write_text(
        f'#!/bin/sh\ncase "$*" in *-xzpf*) exit 1 ;; esac\nexec {real_tar} "$@"\n'
    )
    (ms.bindir / "tar").chmod(0o755)
    _, calls, state = ms()
    assert (state["phase"], state["error"]) == ("failed", "restore_failed")
    assert (state["rolled_back"], state["healthy"]) == (True, False)
    assert len(_compose(calls, "up")) == 1
    assert not (ms.fake / "running").exists()
    assert "left STOPPED" in _log_text(ms)


def test_the_same_target_is_not_attempted_twice(ms):
    """Fault to prove it: drop the tried marker - every pass moves, fails and
    rolls back again."""
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    (ms.fake / "nodes_1").write_text("0 of 0")
    _, _, first = ms()
    assert first["error"] == "verify_failed"
    ms.log.unlink(missing_ok=True)
    _, calls, state = ms()
    assert _mutating(calls) == []
    assert state["id"] == first["id"]

    # A new release pins another version: that one is attempted.
    (ms.fake / "compose_image").write_text("ghcr.io/matter-js/matterjs-server:1.7.0")
    (ms.fake / "local_images").write_text("ghcr.io/matter-js/matterjs-server:1.7.0\n")
    (ms.stack / "docker-compose.yml").write_text("services: {} # 1.7.0\n")
    _, calls, third = ms()
    assert third["to"] == "ghcr.io/matter-js/matterjs-server:1.7.0"
    assert len(_compose(calls, "up")) >= 1


# ----------------------------------------------------- interrupted passes --


def test_a_pass_killed_in_its_recreate_is_healed_and_not_started_again(ms):
    """Fault to prove it: write the tried marker after the job instead of
    before - the next pass stops matter-server again."""
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    (ms.fake / "kill_on_up").write_text("")
    result, _, state = ms()
    assert result.returncode != 0
    assert state["phase"] == "recreate"
    ms.log.unlink(missing_ok=True)
    _, calls, state = ms()
    assert (state["phase"], state["error"]) == ("failed", "interrupted")
    assert _mutating(calls) == []
    ms.log.unlink(missing_ok=True)
    _, calls, _ = ms()
    assert _mutating(calls) == []


@pytest.mark.parametrize("phase", ["stop", "backup"])
def test_a_pass_killed_before_its_recreate_starts_the_old_matter_server(ms, phase):
    """`restart: unless-stopped` does not bring back a stopped container.

    Fault to prove it: heal the state without starting matter-server - it
    stays down until someone notices."""
    (ms.update_dir / "matter-server-state.json").write_text(
        json.dumps({"id": "matter-server-upkeep-1", "phase": phase})
    )
    (ms.fake / "running").unlink()
    _, calls, state = ms()
    assert (state["phase"], state["error"]) == ("failed", "interrupted")
    assert "docker start matter-server" in calls.splitlines()
    assert (ms.fake / "running").exists()


def test_a_pass_killed_in_its_verification_leaves_matter_server_alone(ms):
    (ms.update_dir / "matter-server-state.json").write_text(
        json.dumps({"id": "matter-server-upkeep-1", "phase": "verify"})
    )
    _, calls, state = ms()
    assert (state["phase"], state["error"]) == ("failed", "interrupted")
    assert _mutating(calls) == []


# ------------------------------------------------------------- heartbeat --


def test_the_heartbeat_moves_through_the_verification(ms):
    """While this job runs, neither update-once.sh nor radios-once.sh can
    write their state files, and the web UI reads a still timestamp as a
    sidecar that is gone.

    Fault to prove it: drop refresh_heartbeat from the verification loop."""
    (ms.bindir / "date").write_text(CLOCK_DATE_STUB)
    (ms.bindir / "date").chmod(0o755)
    (ms.update_dir / "state.json").write_text(
        json.dumps({"phase": "done", "id": "u-1", "updater_seen_at": "2026-11-22T11:00:00Z"})
    )
    (ms.update_dir / "radios-state.json").write_text(
        json.dumps({"phase": "done", "id": "job-0", "seen_at": "2026-11-22T11:00:00Z"})
    )
    (ms.fake / "local_images").write_text(f"{NEW_IMAGE}\n")
    (ms.fake / "nodes_1").write_text("none")
    heartbeat = ms.fake / "heartbeat.log"
    _, _, state = ms(HEARTBEAT_LOG=str(heartbeat))
    assert state["error"] == "verify_failed"
    samples = [line.split() for line in heartbeat.read_text().splitlines()]
    verifying = [s for s in samples if s[0] == "verify"]
    assert len(verifying) >= 3
    for column in (1, 2, 3):
        stamps = [s[column] for s in verifying]
        assert len(set(stamps)) == len(stamps), column
    update_state = json.loads((ms.update_dir / "state.json").read_text())
    assert (update_state["phase"], update_state["id"]) == ("done", "u-1")


# ----------------------------------------------------------- the wiring --


def test_the_script_is_executable():
    assert SCRIPT.stat().st_mode & 0o111
