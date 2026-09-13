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

"""Behavioural tests for deploy/updater/radios-once.sh (design 2026-09-11
"Radios in the Web UI", section 6).

The same approach as tests/test_updater_script.py: a sealed PATH of fake
binaries, and what is checked is which commands the script chooses. The
rejection tests carry the security claim of section 6.6: a rejected request
leaves `.env` byte-identical and runs no Docker command that changes
anything - the only Docker call allowed is the read-only `ps` of the
per-pass report."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "updater" / "radios-once.sh"
SONOFF = "usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a7d9118f9ef118f7767135c2a50c9-if00-port0"
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
    "head",
    "tail",
    "cut",
    "wc",
    "readlink",
    "cksum",
    "timeout",
    "ls",
)

DOCKER_STUB = r"""#!/bin/sh
printf 'docker %s\n' "$*" >> "$STUB_LOG"
case "$1" in
  ps)
    [ -f "$FAKE/otbr_state" ] && cat "$FAKE/otbr_state"
    exit 0 ;;
  exec)
    case "$*" in
      *"ot-ctl state"*)
        mode="$(cat "$FAKE/thread_mode" 2>/dev/null || echo leader)"
        ups="$(cat "$FAKE/otbr_ups" 2>/dev/null || echo 0)"
        case "$mode" in
          leader) echo leader ;;
          needs_fix) if [ -f "$FAKE/pid_cleared" ]; then echo leader; else echo detached; fi ;;
          second_up) if [ "$ups" -ge 2 ]; then echo leader; else echo detached; fi ;;
          *) echo detached ;;
        esac ;;
      *"rm -f /run/otbr-agent.pid"*) : > "$FAKE/pid_cleared" ;;
    esac
    exit 0 ;;
  compose)
    case "$*" in *"$(cat "$FAKE/compose_fail" 2>/dev/null || echo __never__)"*) exit 1 ;; esac
    case "$*" in
      *" up "*otbr*)
        echo running > "$FAKE/otbr_state"
        echo $(( $(cat "$FAKE/otbr_ups" 2>/dev/null || echo 0) + 1 )) > "$FAKE/otbr_ups" ;;
      *" rm "*otbr*) rm -f "$FAKE/otbr_state" ;;
    esac
    exit 0 ;;
  logs)
    printf '2026-09-11T20:00:00.000000000Z [C] Platform------: HandleRcpTimeout()\n'
    exit "$(cat "$FAKE/logs_status" 2>/dev/null || echo 0)" ;;
esac
exit 0
"""

CURL_STUB = r"""#!/bin/sh
printf 'curl %s\n' "$*" >> "$STUB_LOG"
[ "$(cat "$FAKE/matter_up" 2>/dev/null || echo yes)" = yes ] && exit 0
exit 7
"""

# Logs every invocation (so a test can count how many times $REQUEST is
# actually opened), then delegates to the real `cat` so nothing else in the
# script - or in this stub itself, which still needs to read $FAKE files
# above - notices the difference. {real_cat} is filled in with the real
# binary's path at fixture setup, since this stub shadows it on PATH.
CAT_STUB_TEMPLATE = """#!/bin/sh
printf 'cat %s\\n' "$*" >> "$STUB_LOG"
exec {real_cat} "$@"
"""

# Same idea as the `cat` stub above, for `jq`: the script calls `jq` many
# times a pass (state bookkeeping, the schema check, the value reads), and
# logging every one of them - then delegating to the real binary - is what
# lets a test assert that $REQUEST's own path never reaches `jq` directly
# (only `cat`, exactly once, feeding the in-memory $REQUEST_BODY every other
# read is drawn from). A regression that fed some `jq ... "$REQUEST"` call
# straight from the file again would otherwise still pass a `cat`-only
# count, since it would not add a `cat` invocation at all.
JQ_STUB_TEMPLATE = """#!/bin/sh
printf 'jq %s\\n' "$*" >> "$STUB_LOG"
exec {real_jq} "$@"
"""


@pytest.fixture
def radios(tmp_path):
    """Returns run() -> (result, calls, state). Attributes expose the paths
    tests arrange: env_file, update_dir, fake (the stub control dir),
    host_dev, sys_bluetooth."""
    bindir, sysdir, fake = tmp_path / "bin", tmp_path / "sys-tools", tmp_path / "fake"
    for d in (bindir, sysdir, fake):
        d.mkdir()
    log = tmp_path / "stub.log"
    update_dir = tmp_path / "data" / "update"
    update_dir.mkdir(parents=True)
    stack = tmp_path / "repo" / "deploy" / "testhost"
    stack.mkdir(parents=True)
    env_file = stack / ".env"
    env_file.write_text(
        "LOXMATTER_IMAGE_TAG=0.3.10\nRADIO_DEVICE=/dev/ttyUSB0\nRADIO_BAUDRATE=460800\n"
        "BACKBONE_IF=wlan0\nBLUETOOTH_ADAPTER=0\n",
        encoding="utf-8",
    )
    host_dev = tmp_path / "host" / "dev"
    (host_dev / "serial" / "by-id").mkdir(parents=True)
    (host_dev / "ttyUSB0").write_text("", encoding="utf-8")
    (host_dev / "sda").write_text("", encoding="utf-8")
    (host_dev / "serial" / "by-id" / SONOFF).symlink_to(Path("../..") / "ttyUSB0")
    (host_dev / "serial" / "by-id" / "usb-Disk").symlink_to(Path("../..") / "sda")
    sys_bluetooth = tmp_path / "sysfs" / "class" / "bluetooth"
    (sys_bluetooth / "hci0").mkdir(parents=True)
    (fake / "otbr_state").write_text("running\n", encoding="utf-8")

    for name, body in (("docker", DOCKER_STUB), ("curl", CURL_STUB)):
        (bindir / name).write_text(body, encoding="utf-8")
        (bindir / name).chmod(0o755)
    (bindir / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bindir / "sleep").chmod(0o755)
    real_cat = subprocess.run(
        ["which", "cat"], capture_output=True, text=True, check=False
    ).stdout.strip()
    (bindir / "cat").write_text(CAT_STUB_TEMPLATE.format(real_cat=real_cat), encoding="utf-8")
    (bindir / "cat").chmod(0o755)
    real_jq = subprocess.run(
        ["which", "jq"], capture_output=True, text=True, check=False
    ).stdout.strip()
    (bindir / "jq").write_text(JQ_STUB_TEMPLATE.format(real_jq=real_jq), encoding="utf-8")
    (bindir / "jq").chmod(0o755)
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
            "LOXMATTER_UPDATE_DIR": str(update_dir),
            "LOXMATTER_STACK": str(stack),
            "LOXMATTER_STACK_HOST_PATH": str(stack),
            "LOXMATTER_HOST_DEV": str(host_dev),
            "LOXMATTER_SYS_BLUETOOTH": str(sys_bluetooth),
            "LOXMATTER_RADIOS_BLUETOOTH_TIMEOUT": "3",
            "LOXMATTER_RADIOS_THREAD_TIMEOUT": "4",
            "LOXMATTER_RADIOS_THREAD_FIX_AFTER": "2",
            "LOXMATTER_RADIOS_POLL_SECONDS": "1",
            **extra_env,
        }
        result = subprocess.run(
            [str(SCRIPT)], capture_output=True, text=True, env=env, check=False, timeout=timeout
        )
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
        state_file = update_dir / "radios-state.json"
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else None
        return result, calls, state

    run.env_file, run.update_dir, run.fake = env_file, update_dir, fake
    run.host_dev, run.sys_bluetooth, run.log = host_dev, sys_bluetooth, log
    run.bindir, run.real_cat = bindir, real_cat
    return run


def _request(radios, **overrides):
    body = {
        "id": "job-1",
        "thread": {"enabled": True, "device": f"/dev/serial/by-id/{SONOFF}"},
        "bluetooth": {"adapter": 0},
        "requested_at": "2026-09-11T20:00:00Z",
    }
    body.update(overrides)
    (radios.update_dir / "radios-request.json").write_text(json.dumps(body), encoding="utf-8")


# A clock that advances one second per call, so a pass whose `sleep` is a
# no-op still produces the advancing timestamps a real pass would - without
# it every `now()` in a sub-second test run returns the same string and no
# assertion could tell a refreshed heartbeat from a frozen one. Handles both
# formats the script asks `date` for: the ISO stamp of `now()` and the
# compact stamp in the `.env` backup filename.
CLOCK_DATE_STUB = r"""#!/bin/sh
n=$(cat "$FAKE/clock" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$FAKE/clock"
case "$*" in
  *%Y%m%d%H%M%S*) printf '202609112000%02d\n' $((n % 100)) ;;
  *) printf '2026-09-11T20:%02d:%02dZ\n' $((n / 60)) $((n % 60)) ;;
esac
"""

# Records, at every sleep the script performs, what the two state files
# claim at that moment: the phase, `radios-state.json`'s own `seen_at`, and
# `state.json`'s `updater_seen_at`. A pass that refreshes its heartbeat
# produces advancing timestamps here; one that does not repeats the same
# two values for the whole of a long step - which is the difference between
# a job the web UI shows as working and one it reports as abandoned.
SLEEP_HEARTBEAT_STUB = r"""#!/bin/sh
printf '%s %s %s\n' \
  "$(jq -r '.phase' "$RADIOS_STATE" 2>/dev/null)" \
  "$(jq -r '.seen_at' "$RADIOS_STATE" 2>/dev/null)" \
  "$(jq -r '.updater_seen_at' "$UPDATE_STATE_FILE" 2>/dev/null)" \
  >> "$HEARTBEAT_LOG"
exit 0
"""


# What `update-once.sh` leaves in state.json after a finished update. The
# radios heartbeat has to move `updater_seen_at` inside this file and leave
# every other key exactly as it found it: this is the update job's own
# record, and the sidecar's reported version, which a heartbeat has no
# business rewriting (update-once.sh's `refresh_heartbeat()` observes the
# same restraint, and for the same reason).
UPDATE_STATE_SEED = {
    "phase": "done",
    "id": "u-1",
    "from": "0.3.9",
    "to": "0.3.10",
    "error": None,
    "rolled_back": False,
    "healthy": True,
    "updater_version": "0.3.10",
    "updater_seen_at": "2026-09-11T19:00:00Z",
}


def _heartbeat_run(radios, **extra_env):
    """Installs the advancing clock and the recording `sleep`, seeds a
    `state.json` whose `updater_seen_at` is already stale, and returns a
    `run()` wrapper plus the sample log. The seeded `state.json` is what
    `sidecar_status()` checks FIRST (`updater_present`), and only
    `update-once.sh` ever writes it - the whole point being that during a
    radios job that script cannot run at all."""
    (radios.bindir / "date").write_text(CLOCK_DATE_STUB, encoding="utf-8")
    (radios.bindir / "date").chmod(0o755)
    (radios.bindir / "sleep").write_text(SLEEP_HEARTBEAT_STUB, encoding="utf-8")
    (radios.bindir / "sleep").chmod(0o755)
    update_state = radios.update_dir / "state.json"
    update_state.write_text(json.dumps(UPDATE_STATE_SEED), encoding="utf-8")
    log = radios.fake / "heartbeat.log"
    return (
        lambda: radios(
            RADIOS_STATE=str(radios.update_dir / "radios-state.json"),
            UPDATE_STATE_FILE=str(update_state),
            HEARTBEAT_LOG=str(log),
            **extra_env,
        ),
        log,
    )


def _samples(log, phase: str) -> list[tuple[str, str]]:
    """The `(seen_at, updater_seen_at)` pairs recorded while the state file
    said `phase`."""
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return [(p[1], p[2]) for p in (line.split() for line in lines) if p and p[0] == phase]


def _strictly_advancing(stamps: list[str]) -> bool:
    return len(stamps) == len(set(stamps)) and stamps == sorted(stamps)


def _mutating_docker_calls(calls: str) -> list[str]:
    return [
        line
        for line in calls.splitlines()
        if line.startswith("docker ") and line.split()[1] != "ps"
    ]


def test_without_a_request_it_reports_the_current_configuration(radios):
    result, calls, state = radios()
    assert result.returncode == 0, result.stderr
    assert state["phase"] == "idle"
    assert state["current"] == {
        "thread_enabled": True,
        "thread_device": "/dev/ttyUSB0",
        "bluetooth_adapter": 0,
        "otbr_running": True,
    }
    assert state["capable"] is True and state["capable_reason"] is None
    assert state["seen_at"]
    assert _mutating_docker_calls(calls) == []


def test_thread_counts_as_enabled_from_the_profile_alone(radios):
    """Fault to prove it: derive thread_enabled only from the container."""
    (radios.fake / "otbr_state").unlink()
    radios.env_file.write_text(radios.env_file.read_text() + "COMPOSE_PROFILES=foo,thread\n")
    _, _, state = radios()
    assert state["current"]["thread_enabled"] is True
    assert state["current"]["otbr_running"] is False


def test_thread_counts_as_enabled_from_an_existing_container_without_a_profile(radios):
    """The test Pi: no COMPOSE_PROFILES line, otbr runs (design 12.1).
    Fault to prove it: derive thread_enabled only from the profile."""
    _, _, state = radios()
    assert state["current"]["thread_enabled"] is True


def test_no_profile_and_no_container_means_thread_disabled(radios):
    (radios.fake / "otbr_state").unlink()
    _, _, state = radios()
    assert state["current"]["thread_enabled"] is False


def test_a_missing_bluetooth_adapter_line_reads_as_zero(radios):
    radios.env_file.write_text("RADIO_DEVICE=/dev/ttyUSB0\n")
    _, _, state = radios()
    assert state["current"]["bluetooth_adapter"] == 0


def test_a_hand_quoted_env_value_reads_the_same_as_unquoted(radios):
    """Fault to prove it: stop stripping surrounding quotes in env_value."""
    radios.env_file.write_text(
        radios.env_file.read_text().replace(
            "RADIO_DEVICE=/dev/ttyUSB0", 'RADIO_DEVICE="/dev/ttyUSB0"'
        )
    )
    _, _, state = radios()
    assert state["current"]["thread_device"] == "/dev/ttyUSB0"


def test_a_zero_padded_bluetooth_adapter_does_not_break_the_report(radios):
    """Fault to prove it: remove the leading-zero strip. The installed jq
    (1.7.1) turns out to coerce "01" to 1 on its own for --argjson, so a
    bare report-only check of bluetooth_adapter cannot tell the two
    branches apart; what the strip actually guards is the later string
    comparison against a request's unpadded adapter number, so this test
    also submits a matching request and checks the request is recognised
    as changing nothing rather than misread as a change."""
    radios.env_file.write_text(
        radios.env_file.read_text().replace("BLUETOOTH_ADAPTER=0\n", "BLUETOOTH_ADAPTER=01\n")
    )
    radios.env_file.write_text(
        radios.env_file.read_text().replace("/dev/ttyUSB0", f"/dev/serial/by-id/{SONOFF}")
    )
    (radios.sys_bluetooth / "hci1").mkdir()
    _request(radios, bluetooth={"adapter": 1})
    before = radios.env_file.read_bytes()
    result, calls, state = radios()
    assert result.returncode == 0, result.stderr
    assert state["current"]["bluetooth_adapter"] == 1
    assert state["phase"] == "unchanged"
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def test_without_the_dev_mount_it_is_not_capable_and_rejects(radios, tmp_path):
    before = radios.env_file.read_bytes()
    _request(radios)
    _, calls, state = radios(LOXMATTER_HOST_DEV=str(tmp_path / "nowhere"))
    assert state["capable"] is False
    assert state["capable_reason"] == "host_dev_not_mounted"
    assert (state["phase"], state["error"]) == ("rejected", "host_dev_not_mounted")
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def test_without_a_known_stack_host_path_it_is_not_capable_and_rejects(radios):
    before = radios.env_file.read_bytes()
    _request(radios)
    _, calls, state = radios(LOXMATTER_STACK_HOST_PATH="")
    assert state["capable"] is False
    assert state["capable_reason"] == "stack_host_path_unknown"
    assert (state["phase"], state["error"]) == ("rejected", "stack_host_path_unknown")
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def test_a_change_with_no_env_file_is_rejected(radios):
    """Reach the changes section (thread disabled requested while otbr is
    running counts as a change) without a .env file to write into."""
    radios.env_file.unlink()
    _request(radios, thread={"enabled": False, "device": None}, bluetooth={"adapter": 0})
    _, calls, state = radios()
    assert (state["phase"], state["error"]) == ("rejected", "env_file_missing")
    assert not radios.env_file.exists()
    assert _mutating_docker_calls(calls) == []


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"thread": {"enabled": True, "device": "/dev/ttyUSB0"}}, "request_malformed"),
        (
            {"thread": {"enabled": True, "device": "/dev/serial/by-id/../../sda"}},
            "request_malformed",
        ),
        (
            {"thread": {"enabled": True, "device": f"/dev/serial/by-id/{SONOFF}\nx"}},
            "request_malformed",
        ),
        # Task 7d note: this stays a rejection, and it is no longer the
        # unplugged-stick user's case. A non-null Thread half is a request
        # to RUN Thread on that stick, and a stick that is not attached
        # cannot run anything - `otbr` binds it through Compose `devices:`
        # and would fail at `compose up`. A user whose configured stick
        # fell out and who only wants to change Bluetooth now sends
        # `"thread": null` instead, which is not validated against the
        # host at all - see
        # test_a_null_thread_half_leaves_thread_completely_alone below for
        # that half of the contract.
        (
            {"thread": {"enabled": True, "device": "/dev/serial/by-id/usb-Gone"}},
            "thread_device_not_found",
        ),
        # The half that IS present is still fully validated, `null`
        # elsewhere in the request or not.
        ({"thread": None, "bluetooth": {"adapter": 1}}, "bluetooth_adapter_not_found"),
        # The widened schema accepts `null` for a half - and nothing else
        # new. A half that is present must still be a complete, correct
        # object.
        ({"thread": 5}, "request_malformed"),
        ({"thread": {"enabled": True}}, "request_malformed"),
        ({"bluetooth": []}, "request_malformed"),
        ({"bluetooth": {"adapter": None}}, "request_malformed"),
        (
            {"thread": {"enabled": True, "device": "/dev/serial/by-id/usb-Disk"}},
            "thread_device_not_a_serial_port",
        ),
        ({"thread": {"enabled": True, "device": None}}, "thread_device_required"),
        ({"thread": {"enabled": "yes", "device": None}}, "request_malformed"),
        (
            {"thread": {"enabled": True, "device": f"/dev/serial/by-id/{SONOFF}", "baud": 1}},
            "request_malformed",
        ),
        ({"bluetooth": {"adapter": 16}}, "request_malformed"),
        ({"bluetooth": {"adapter": "0"}}, "request_malformed"),
        ({"bluetooth": {"adapter": 1.5}}, "request_malformed"),
        # A whole-number float renders as "1.0" via `jq -r`, which would
        # otherwise look for a nonexistent "hci1.0" instead of being caught
        # as malformed - see test_a_bad_request_is_rejected_without_any_effect
        # fault-proving notes.
        ({"bluetooth": {"adapter": 1.0}}, "request_malformed"),
        ({"bluetooth": {"adapter": 1}}, "bluetooth_adapter_not_found"),
        ({"command": "rm -rf /"}, "request_malformed"),
    ],
)
def test_a_bad_request_is_rejected_without_any_effect(radios, overrides, error):
    """Fault to prove it (run once, on the traversal case): replace the jq
    `test("\\A/dev/serial/by-id/[A-Za-z0-9._:+-]+\\z")` with
    `startswith("/dev/serial/by-id/")`."""
    before = radios.env_file.read_bytes()
    _request(radios, **overrides)
    _, calls, state = radios()
    assert (state["phase"], state["error"]) == ("rejected", error)
    assert state["id"] == "job-1"
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def test_an_unreadable_request_is_rejected_once(radios):
    (radios.update_dir / "radios-request.json").write_text("{not json", encoding="utf-8")
    _, _, first = radios()
    assert (first["phase"], first["error"]) == ("rejected", "request_malformed")
    log_lines = (radios.update_dir / "radios-log.txt").read_text().count("rejected")
    radios()
    assert (radios.update_dir / "radios-log.txt").read_text().count("rejected") == log_lines


def test_a_request_path_that_cannot_be_read_ends_terminal_not_stuck(radios):
    """Fault to prove it: read $REQUEST directly at every step instead of
    snapshotting it once. The old code's `[ -f "$REQUEST" ]` guard only
    accepts regular files, so a request path that is anything else (here: a
    directory left behind by something else, or the bridge itself losing a
    race while writing the real file) was silently treated as "no request
    yet" forever - phase stays whatever it last was, with no error, and no
    one is ever told the request could not be used. The fix widens that
    guard to "the path exists" and then fails the one guarded read with a
    clear, terminal, retryable-next-time state."""
    request_path = radios.update_dir / "radios-request.json"
    request_path.mkdir()
    before = radios.env_file.read_bytes()
    _, calls, state = radios()
    assert (state["phase"], state["error"]) == ("rejected", "request_malformed")
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def test_the_request_file_is_read_from_disk_exactly_once(radios):
    """Fault to prove it: feed the schema check and the three value reads
    from `jq ... "$REQUEST"` again (the original, pre-round-1 shape)
    instead of capturing $REQUEST's bytes into a shell variable up front and
    feeding every later `jq` from that variable via a pipe. In the fixed
    script, `cat` is the only command ever run against $REQUEST's own path
    - the stub below records every invocation of `cat`, so re-reading
    $REQUEST directly shows up as an extra (or, for a well-formed request
    whose id needs no cksum fallback, a missing) logged line.

    This test cannot by itself tell the fixed script apart from round 1's
    file-snapshot shape, which also called `cat` on $REQUEST exactly once
    (to make the snapshot) - see
    test_no_writable_path_is_used_to_stage_the_request below for that.

    Extended blind spot: counting only the stubbed `cat` would still pass a
    regression that kept that one `cat` snapshot AND additionally fed some
    `jq` call directly from `"$REQUEST"` (instead of from `$REQUEST_BODY` via
    a pipe) - that would add no `cat` invocation at all. `jq` is stubbed the
    same logging way as `cat` (see JQ_STUB_TEMPLATE) precisely so this can
    also assert that $REQUEST's own path never reaches any stub - `jq`
    included - except that single `cat` call.

    Remaining blind spot, not closed here: this only proves the ARGV shape
    - a read via shell redirection (`jq ... < "$REQUEST"`, which never puts
    the path on `jq`'s own argv at all) or through some other, unstubbed
    tool would leave no trace in this log either way. A later reader should
    not take this test as proving more than "no stubbed command's argument
    list ever names $REQUEST except that one `cat` call"."""
    radios.env_file.write_text(
        radios.env_file.read_text().replace("/dev/ttyUSB0", f"/dev/serial/by-id/{SONOFF}")
    )
    _request(radios)
    _, calls, state = radios()
    assert state["phase"] == "unchanged"
    request_path = str(radios.update_dir / "radios-request.json")
    lines_with_request_path = [line for line in calls.splitlines() if request_path in line]
    request_reads = [line for line in lines_with_request_path if line.startswith("cat ")]
    assert len(request_reads) == 1
    assert lines_with_request_path == request_reads


def test_no_writable_path_is_used_to_stage_the_request(radios):
    """IMPORTANT 1 (round 1's fix relocated the problem, did not close it):
    round 1 copied the request into radios-request.handling.json, a
    predictable path inside $UPDATE_DIR - but /data (the whole directory
    tree $UPDATE_DIR lives under) is mounted read-write into both the
    bridge and the updater (docker-compose.yml), so whoever can write
    radios-request.json can also write at that same predictable path.
    `cat "$REQUEST" > snapshot` follows a symlink there and truncates
    through it, so a symlink re-planted between round 1's own `rm -f` of
    that path and its `cat` redirects the write anywhere this process can
    write - .env included, breaking ".env byte-identical on rejection".

    Fault to prove it: stage the request through any file under
    $UPDATE_DIR at all. This test statically pre-plants a symlink there
    before the script even starts, which round 1's own `rm -f` of that
    same path unlinks unconditionally on every single pass - before the
    truncating write ever happens - so it does not reproduce the narrower,
    precisely-timed race the finding names (a genuine mid-pass replant is,
    like round 1's own live-file-swap race, not reliably reproducible from
    outside a sub-second subprocess). What it does prove, deterministically:
    round 1's script touches (removes, then recreates) a fixed, predictable,
    bridge-writable path on every pass regardless of timing, and the fixed
    script does not - it never creates, removes, or writes through any path
    under $UPDATE_DIR while handling a request, so a symlink planted there
    beforehand is left completely alone, race or no race."""
    trap = radios.update_dir / "radios-request.handling.json"
    trap.symlink_to(radios.env_file)
    radios.env_file.write_text(
        radios.env_file.read_text().replace("/dev/ttyUSB0", f"/dev/serial/by-id/{SONOFF}")
    )
    before = radios.env_file.read_bytes()
    _request(radios)
    _, calls, state = radios()
    assert state["phase"] == "unchanged"
    assert radios.env_file.read_bytes() == before
    assert trap.is_symlink() and trap.resolve() == radios.env_file.resolve()
    assert _mutating_docker_calls(calls) == []


def test_a_fifo_request_with_no_writer_does_not_hang(radios):
    """Fault to prove it: widen the presence check from `-f` to `-e` without
    also requiring `-f` before any read, so a FIFO with no writer blocks
    `cat` (and so the whole pass) forever - update-once.sh runs in the same
    entrypoint loop, so this would stall updates too. Gives the subprocess
    its own short timeout: a regression here must fail loudly and fast, not
    hang this whole test file."""
    request_path = radios.update_dir / "radios-request.json"
    os.mkfifo(request_path)
    before = radios.env_file.read_bytes()
    result, calls, state = radios(timeout=5)
    assert result.returncode == 0, result.stderr
    assert (state["phase"], state["error"]) == ("rejected", "request_malformed")
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def test_a_hanging_request_read_is_bounded_and_ends_rejected(radios):
    """The `[ -f "$REQUEST" ]` guard above only proves $REQUEST was a
    regular file at that instant - a FIFO swapped in at the same path
    strictly between that check and the read still opens as a blocking
    read with no writer, and update-once.sh runs in this same entrypoint
    loop, so an unbounded read here would stall updates too. That exact
    swap is a genuine race, not reproducible deterministically from outside
    a sub-second subprocess (like the similar race in
    test_no_writable_path_is_used_to_stage_the_request above), so this
    proves the bound itself instead: make `cat` redirect its read to a
    FIFO that genuinely has no writer - the same blocking open() a swapped-
    in FIFO would cause - rather than the race that can trigger it. A
    `sleep`-based stub would not prove anything here: the fixture's own
    `sleep` stub (used everywhere else to keep the timed polling loops fast)
    always returns immediately, so a stub that just slept would return
    before either a fault or a fix had a chance to matter.

    Fault to prove it: read the request with a bare `cat "$REQUEST"` and no
    `timeout` around it.

    The redirect below only fires for $REQUEST's own path - the docker
    stub's `ps` branch also shells out to `cat` (reading back the fake
    otbr state), with no `timeout` of its own, and pointing every `cat`
    call at the no-writer FIFO regardless of argument hangs that unrelated
    read too."""
    request_path = str(radios.update_dir / "radios-request.json")
    no_writer_fifo = radios.fake / "no-writer.fifo"
    os.mkfifo(no_writer_fifo)
    (radios.bindir / "cat").write_text(
        "#!/bin/sh\n"
        'printf \'cat %s\\n\' "$*" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        f'  *"{request_path}"*) exec {radios.real_cat} "{no_writer_fifo}" ;;\n'
        "esac\n"
        f'exec {radios.real_cat} "$@"\n',
        encoding="utf-8",
    )
    (radios.bindir / "cat").chmod(0o755)
    before = radios.env_file.read_bytes()
    _request(radios)
    result, calls, state = radios(timeout=10)
    assert result.returncode == 0, result.stderr
    assert (state["phase"], state["error"]) == ("rejected", "request_malformed")
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def test_enabling_thread_requires_a_backbone_interface(radios):
    radios.env_file.write_text("RADIO_DEVICE=/dev/ttyUSB0\nBLUETOOTH_ADAPTER=0\n")
    before = radios.env_file.read_bytes()
    _request(radios)
    _, calls, state = radios()
    assert (state["phase"], state["error"]) == ("rejected", "backbone_interface_missing")
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def test_the_same_job_is_not_handled_twice(radios):
    _request(radios, bluetooth={"adapter": 1})
    radios()
    radios.log.unlink()
    _, calls, state = radios()
    assert state["phase"] == "rejected"
    assert _mutating_docker_calls(calls) == []
    assert (radios.update_dir / "radios-log.txt").read_text().count("job-1") == 1


def test_a_request_waits_while_an_update_runs(radios):
    """Fault to prove it: drop the update-phase check."""
    (radios.update_dir / "state.json").write_text(json.dumps({"phase": "recreate"}))
    _request(radios)
    _, calls, state = radios()
    assert state["phase"] == "idle"
    assert not (radios.update_dir / "radios-handled" / "job-1").exists()
    assert _mutating_docker_calls(calls) == []


def test_a_request_that_changes_nothing_ends_unchanged(radios):
    # A legacy /dev/ttyUSB0 value cannot be requested (pattern), so .env
    # already holds the by-id path the request names.
    radios.env_file.write_text(
        radios.env_file.read_text().replace("/dev/ttyUSB0", f"/dev/serial/by-id/{SONOFF}")
    )
    _request(radios)
    before = radios.env_file.read_bytes()
    _, calls, state = radios()
    assert state["phase"] == "unchanged"
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def test_a_null_thread_half_leaves_thread_completely_alone(radios):
    """Task 7d, the defect this whole change exists for, in the shape the
    maintainer's own Pi has it: `.env` holds the installer's
    `RADIO_DEVICE=/dev/ttyUSB0` while the card and the API speak in mapped
    by-id paths, and here the stick has been unplugged on top of that, so
    the by-id link is gone from `/host/dev` as well.

    Before this change the same user's Bluetooth-only request carried a
    full Thread half and had two ways to go wrong, both of them bad: the
    host presence check rejected the entire job (`thread_device_not_found`,
    Bluetooth change silently dropped), and on a plugged-in stick the
    legacy `.env` value never compared equal to the by-id path, so
    `THREAD_ACTION` came out `up` and `otbr` was force-recreated with up to
    150 s of `verify_thread` - minutes of Thread downtime nobody
    asked for or announced.

    Two faults prove it, one at a time:

    (a) Drop the `[ "$HAS_THREAD" = true ]` guard from the changes
        section. `WANT_ENABLED` then defaults to `false` for the absent
        half, falls into the `elif [ "$CUR_ENABLED" = true ]` branch and
        reads as "Thread requested off": the job tears down a running
        border router for a user who only touched Bluetooth. Caught below
        by the step list (`apply_thread`/`verify_thread` appear) and by the
        otbr assertions at the end - NOT by the job's own outcome, which
        stays `done` either way. That is the difference between proving a
        half was skipped and proving the job happened to survive it.
    (b) Widen `.thread` back to a required object in the jq schema. The
        request is rejected `request_malformed`.

    One guard this test deliberately does NOT pin, measured rather than
    assumed: removing `[ "$HAS_THREAD" = true ]` from the host presence
    check alone leaves this test green, because `WANT_ENABLED` is only ever
    assigned inside the `HAS_THREAD` branch and its default is already
    `false`. That condition is redundant on purpose and kept as
    documentation at the exact check that used to reject this user's whole
    job over a device they had not asked to change.
    """
    (radios.host_dev / "serial" / "by-id" / SONOFF).unlink()  # the stick fell out
    (radios.sys_bluetooth / "hci1").mkdir()
    before = radios.env_file.read_text()
    _request(radios, thread=None, bluetooth={"adapter": 1})
    _, calls, state = radios()

    assert (state["phase"], state["healthy"], state["rolled_back"]) == ("done", True, False)
    # No apply_thread/verify_thread step is even offered to the card.
    assert state["steps"] == ["validate", "backup", "write", "apply_bluetooth", "verify_bluetooth"]

    after = radios.env_file.read_text()
    assert "BLUETOOTH_ADAPTER=1\n" in after

    # Every line except the one Bluetooth key is byte-identical: the legacy
    # RADIO_DEVICE was not normalised to a by-id path behind the user's
    # back, no COMPOSE_PROFILES line appeared, no RADIO_BAUDRATE default
    # was written.
    def _without_adapter(text: str) -> list[str]:
        return [line for line in text.splitlines() if not line.startswith("BLUETOOTH_ADAPTER=")]

    assert _without_adapter(after) == _without_adapter(before)
    assert "RADIO_DEVICE=/dev/ttyUSB0" in after

    # And otbr was never named in anything but the read-only report `ps`:
    # not recreated, not removed, not restarted, never asked for its
    # Thread state. A test that only checked the job's own outcome would
    # pass with fault (a) in place; these are what actually prove the half
    # was skipped rather than merely surviving.
    assert _compose(calls, "otbr") == []
    assert "otbr" not in "\n".join(_mutating_docker_calls(calls))
    assert "ot-ctl" not in calls


def test_a_null_bluetooth_half_leaves_the_adapter_alone(radios):
    """The symmetric half of the contract: a Thread-only change must not
    recreate matter-server or touch `BLUETOOTH_ADAPTER`.

    Fault to prove it: drop the `[ "$HAS_BLUETOOTH" = true ]` guard from
    the changes section. `WANT_BLUETOOTH` is then the empty string, which
    differs from `.env`'s `0`, so `BLUETOOTH_CHANGE` comes out true and
    every Thread-only job additionally writes `BLUETOOTH_ADAPTER=` (an
    empty value docker-compose would carry into matter-server) and
    recreates matter-server."""
    _request(radios, bluetooth=None)
    before = radios.env_file.read_text()
    _, calls, state = radios()
    assert state["phase"] == "done"
    assert state["steps"] == ["validate", "backup", "write", "apply_thread", "verify_thread"]
    assert "BLUETOOTH_ADAPTER=0\n" in radios.env_file.read_text()
    assert "BLUETOOTH_ADAPTER=0\n" in before
    assert _compose(calls, "matter-server") == []
    # verify_bluetooth is the only thing in this script that runs curl.
    assert "curl " not in calls


def test_a_request_with_both_halves_null_changes_nothing(radios):
    """The degenerate case the widened schema makes expressible. It must
    end `unchanged` - the same terminal state a both-halves request that
    matches `.env` already ends in - not `rejected`, and not a job that
    recreates something for good measure."""
    _request(radios, thread=None, bluetooth=None)
    before = radios.env_file.read_bytes()
    _, calls, state = radios()
    assert state["phase"] == "unchanged"
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def _compose(calls: str, *words: str) -> list[str]:
    return [
        line
        for line in calls.splitlines()
        if line.startswith("docker compose") and all(word in line.split() for word in words)
    ]


def test_switching_the_bluetooth_adapter_recreates_only_matter_server(radios):
    (radios.sys_bluetooth / "hci1").mkdir()
    radios.env_file.write_text(
        radios.env_file.read_text().replace("/dev/ttyUSB0", f"/dev/serial/by-id/{SONOFF}")
    )
    _request(radios, bluetooth={"adapter": 1})
    _, calls, state = radios()
    assert (state["phase"], state["healthy"], state["rolled_back"]) == ("done", True, False)
    assert state["steps"] == ["validate", "backup", "write", "apply_bluetooth", "verify_bluetooth"]
    assert "BLUETOOTH_ADAPTER=1\n" in radios.env_file.read_text()
    assert len(_compose(calls, "up", "--force-recreate", "matter-server")) == 1
    assert _compose(calls, "otbr") == []
    assert "curl " in calls
    assert list(radios.env_file.parent.glob(".env.radios-*"))


def test_switching_the_thread_stick_writes_the_by_id_path_and_recreates_otbr(radios):
    radios.env_file.write_text(radios.env_file.read_text() + "COMPOSE_PROFILES=foo\n")
    _request(radios)
    _, calls, state = radios()
    text = radios.env_file.read_text()
    assert state["phase"] == "done"
    assert f"RADIO_DEVICE=/dev/serial/by-id/{SONOFF}\n" in text
    assert "COMPOSE_PROFILES=foo,thread\n" in text
    assert len(_compose(calls, "up", "--force-recreate", "otbr")) == 1
    assert _compose(calls, "matter-server") == []
    assert "ot-ctl state" in calls


def test_a_radios_job_keeps_the_opt_in_otbr_radio_url_extra(radios):
    """`&uart-exclusive` on otbr's radio URL is opt-in, through
    `OTBR_RADIO_URL_EXTRA` in `.env` (see
    `test_otbr_takes_the_exclusive_lock_only_when_the_installation_asks_for_it`
    in `tests/test_compose_profiles.py`). This script rewrites `.env` on
    every Thread change, and the design only works if that rewrite neither
    drops the line nor writes a second one - on a switch that succeeds and
    on one that is rolled back alike.

    A guard on this script's existing behaviour, not a test of new code:
    `env_set` replaces one named key and appends a missing one, and the
    rollback copies the whole backup back. The script itself is not to be
    modified, so no fault is injected into it."""
    line = "OTBR_RADIO_URL_EXTRA=&uart-exclusive\n"
    original = radios.env_file.read_text() + line
    radios.env_file.write_text(original)
    _request(radios)
    _, _, state = radios()
    assert state["phase"] == "done"
    assert radios.env_file.read_text().count("OTBR_RADIO_URL_EXTRA") == 1
    assert line in radios.env_file.read_text()

    radios.env_file.write_text(original)
    (radios.update_dir / "radios-state.json").unlink()
    (radios.fake / "thread_mode").write_text("never")
    _request(radios, id="job-2")
    _, _, state = radios()
    assert (state["phase"], state["rolled_back"]) == ("failed", True)
    assert radios.env_file.read_text().count("OTBR_RADIO_URL_EXTRA") == 1
    assert line in radios.env_file.read_text()


def test_enabling_thread_adds_a_missing_baud_rate_and_keeps_an_existing_one(radios):
    (radios.fake / "otbr_state").unlink()
    radios.env_file.write_text("RADIO_DEVICE=\nBACKBONE_IF=wlan0\nBLUETOOTH_ADAPTER=0\n")
    _request(radios)
    _, _, state = radios()
    assert state["phase"] == "done"
    assert "RADIO_BAUDRATE=460800\n" in radios.env_file.read_text()
    assert "COMPOSE_PROFILES=thread\n" in radios.env_file.read_text()


def test_disabling_thread_removes_otbr_and_only_the_thread_profile(radios):
    radios.env_file.write_text(radios.env_file.read_text() + "COMPOSE_PROFILES=foo,thread\n")
    _request(radios, thread={"enabled": False, "device": None})
    _, calls, state = radios()
    assert state["phase"] == "done"
    assert "COMPOSE_PROFILES=foo\n" in radios.env_file.read_text()
    assert len(_compose(calls, "rm", "otbr")) == 1
    assert state["current"]["otbr_running"] is False


def test_a_hanging_agent_gets_the_watchdog_fix_and_recovers(radios):
    (radios.fake / "thread_mode").write_text("needs_fix")
    _request(radios)
    _, calls, state = radios()
    assert state["phase"] == "done"
    assert calls.count("rm -f /run/otbr-agent.pid") == 1
    assert len(_compose(calls, "restart", "otbr")) == 1


def test_a_thread_stick_that_never_forms_the_network_is_rolled_back(radios):
    """Two faults to prove it, one at a time: comment out the line that
    copies the backup back over .env (the byte comparison fails); remove
    the `fixed=1` guard (the pid fix then repeats within one verification,
    and the count of two - one per verification, apply and rollback -
    fails)."""
    before = radios.env_file.read_bytes()
    (radios.fake / "thread_mode").write_text("never")
    _request(radios)
    _, calls, state = radios()
    assert (state["phase"], state["error"]) == ("failed", "verify_thread_failed")
    assert (state["rolled_back"], state["healthy"]) == (True, False)
    assert radios.env_file.read_bytes() == before
    assert len(_compose(calls, "up", "otbr")) == 2
    assert calls.count("rm -f /run/otbr-agent.pid") == 2


def test_a_rollback_that_brings_thread_back_reports_healthy(radios):
    (radios.fake / "thread_mode").write_text("second_up")
    _request(radios)
    _, _, state = radios()
    assert (state["phase"], state["error"]) == ("failed", "verify_thread_failed")
    assert (state["rolled_back"], state["healthy"]) == (True, True)


def test_enabling_thread_that_fails_rolls_back_to_disabled(radios):
    (radios.fake / "otbr_state").unlink()
    (radios.fake / "thread_mode").write_text("never")
    _request(radios)
    _, calls, state = radios()
    assert state["phase"] == "failed"
    assert len(_compose(calls, "rm", "otbr")) == 1
    assert state["current"]["otbr_running"] is False


def test_a_matter_server_that_does_not_come_back_is_rolled_back(radios):
    (radios.sys_bluetooth / "hci1").mkdir()
    radios.env_file.write_text(
        radios.env_file.read_text().replace("/dev/ttyUSB0", f"/dev/serial/by-id/{SONOFF}")
    )
    before = radios.env_file.read_bytes()
    (radios.fake / "matter_up").write_text("no")
    _request(radios, bluetooth={"adapter": 1})
    _, calls, state = radios()
    assert (state["phase"], state["error"], state["rolled_back"]) == (
        "failed",
        "verify_bluetooth_failed",
        True,
    )
    assert state["healthy"] is False
    assert radios.env_file.read_bytes() == before
    assert len(_compose(calls, "up", "matter-server")) == 2


def test_a_failing_compose_call_is_rolled_back_too(radios):
    (radios.fake / "compose_fail").write_text("--force-recreate otbr")
    before = radios.env_file.read_bytes()
    _request(radios)
    _, _, state = radios()
    assert (state["phase"], state["error"], state["rolled_back"]) == (
        "failed",
        "apply_thread_failed",
        True,
    )
    assert radios.env_file.read_bytes() == before


def test_an_unwritable_env_file_ends_the_write_step_terminal(radios):
    """Fix round 1, Important: a write failure used to die under `set -eu`
    right where it happened - env_set ran as a plain statement, not an `if`
    condition the way the backup's own `cp` already was. Reproduced by the
    reviewer: .env possibly already truncated by env_set's own
    `cat > "$target"`, the pass dead before any write_state call, phase
    left at "write" (not terminal), rolled_back still false, healthy null,
    a stale .radios-tmp file, and a handled marker blocking any retry.

    Fault to prove it: call `env_set` directly at the call sites instead of
    through `env_set_or_fail` (i.e. do not check its return value) - a
    write failure then has no guard to catch it."""
    if os.geteuid() == 0:
        pytest.skip("root ignores file permissions")
    os.chmod(radios.env_file, 0o444)
    try:
        _request(radios)
        _, calls, state = radios()
    finally:
        os.chmod(radios.env_file, 0o644)
    assert (state["phase"], state["error"]) == ("failed", "env_write_failed")
    assert state["rolled_back"] is False
    assert not list(radios.env_file.parent.glob("*.radios-tmp"))
    assert _mutating_docker_calls(calls) == []


def test_a_failed_env_restore_ends_terminal_with_its_own_error_key(radios):
    """Fix round 1, Important: the rollback's own restore
    (`cat "$BACKUP" > "$(env_target)"`) was just as unguarded as the write
    step above - a failure there died under `set -eu` with rolled_back
    still false (it was only ever set AFTER a successful restore) even
    though a rollback really was attempted, and no error key of its own to
    tell it apart from whatever originally failed forward.

    Fault to prove it: run the restore as a plain statement, with no
    `if !` around it, the way the write step's own fault above is proved -
    a failure there has no guard to catch it and no ROLLED=true recorded
    before the attempt.

    Makes `cat` fail specifically when reading a file shaped like a backup
    (`.radios-` followed by a digit - env_set's own `.radios-tmp` rewrite
    has a different, non-digit suffix and is left alone) so the backup
    genuinely exists on disk (an ordinary, unguarded `cp` made it) and only
    the restore's own read of it fails."""
    (radios.bindir / "cat").write_text(
        "#!/bin/sh\n"
        'printf \'cat %s\\n\' "$*" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        "  *.radios-[0-9]*) exit 1 ;;\n"
        "esac\n"
        f'exec {radios.real_cat} "$@"\n',
        encoding="utf-8",
    )
    (radios.bindir / "cat").chmod(0o755)
    (radios.fake / "thread_mode").write_text("never")
    _request(radios)
    _, _, state = radios()
    assert (state["phase"], state["error"]) == ("failed", "env_restore_failed")
    assert state["rolled_back"] is True
    assert state["healthy"] is False
    assert list(radios.env_file.parent.glob(".env.radios-*"))


def test_old_backups_are_pruned_to_the_newest_five(radios):
    """Fix round 1, cheap item 1: .env carries LOXMATTER_API_TOKEN (see
    deploy/testhost/.env.example) and nothing pruned its own backups
    before - every applying job left one behind forever.

    Fault to prove it: drop the `ls -1t ... | tail -n +6 | ...` prune line
    after the backup succeeds."""
    old_backups = []
    for i in range(7):
        p = radios.env_file.parent / f".env.radios-{i:014d}"
        p.write_text("old\n", encoding="utf-8")
        os.utime(p, (1_000_000 + i, 1_000_000 + i))
        old_backups.append(p)
    (radios.sys_bluetooth / "hci1").mkdir()
    radios.env_file.write_text(
        radios.env_file.read_text().replace("/dev/ttyUSB0", f"/dev/serial/by-id/{SONOFF}")
    )
    _request(radios, bluetooth={"adapter": 1})
    _, _, state = radios()
    assert state["phase"] == "done"
    remaining = set(radios.env_file.parent.glob(".env.radios-*"))
    assert len(remaining) == 5
    # The 3 oldest of the 7 pre-existing backups must be gone; the new
    # backup this run just made is always the newest and must survive.
    assert old_backups[0] not in remaining
    assert old_backups[1] not in remaining
    assert old_backups[2] not in remaining
    assert old_backups[6] in remaining


def test_the_heartbeat_advances_through_a_long_verify(radios):
    """The regression this file exists to keep out from now on: a healthy
    job reporting itself abandoned about 30 seconds in.

    `sidecar_status()` (src/loxmatter/radios/sidecar.py) asks
    `updater_present(update_state)` FIRST, off `state.json`'s
    `updater_seen_at` - and only `update-once.sh` writes that file, which
    cannot run while this script does (entrypoint.sh runs the two workers
    one after the other). `radios-state.json`'s own `seen_at` was written
    once per STEP, and `verify_thread` is a single step lasting up to 150 s.
    Both timestamps therefore froze for the whole of the flagship stick
    switch, `_MAX_SILENT_SECONDS` is 30, and the card declared a working
    job dead.

    Asserting the job merely SUCCEEDS proves nothing about this - it
    already did. What discriminates is that both timestamps keep moving
    WHILE the verify waits, which is what the recorded samples show.

    Fault to prove it: delete the `refresh_heartbeat` call from
    `verify_thread`'s loop in deploy/updater/radios-once.sh."""
    run, log = _heartbeat_run(radios)
    (radios.fake / "thread_mode").write_text("needs_fix\n", encoding="utf-8")
    _request(radios, bluetooth=None)
    result, _, state = run()

    assert result.returncode == 0, result.stderr
    assert state["phase"] == "done"
    samples = _samples(log, "verify_thread")
    assert len(samples) >= 2, samples
    assert _strictly_advancing([seen for seen, _ in samples]), samples
    assert _strictly_advancing([updater for _, updater in samples]), samples
    # The seeded value is what a sidecar that never refreshed would still
    # be showing - every sample has to have left it behind.
    assert all(updater > "2026-09-11T19:00:00Z" for _, updater in samples), samples


def test_the_heartbeat_advances_through_a_rollback(radios):
    """The same claim for the stretch that wrote nothing at all: `step()`
    is a no-op while $ROLLING (the phase must stay "rollback" for the whole
    recovery), so a rollback - which recreates containers and verifies them
    again, up to another two and a half minutes - used to be completely
    silent. The phase in every sample below is `rollback`, which is
    precisely the window that had no writes.

    Fault to prove it: restore `step()` to
    `if [ "$ROLLING" = false ]; then write_state "$1" ""; fi` and delete
    the `refresh_heartbeat` calls from `compose()` and `verify_bluetooth`."""
    (radios.sys_bluetooth / "hci1").mkdir()
    run, log = _heartbeat_run(radios)
    (radios.fake / "matter_up").write_text("no\n", encoding="utf-8")
    _request(radios, thread=None, bluetooth={"adapter": 1})
    result, _, state = run()

    assert result.returncode == 0, result.stderr
    assert state["phase"] == "failed"
    assert state["rolled_back"] is True
    samples = _samples(log, "rollback")
    assert len(samples) >= 2, samples
    assert _strictly_advancing([seen for seen, _ in samples]), samples
    assert _strictly_advancing([updater for _, updater in samples]), samples


def _seconds(stamp: str) -> int:
    """Turns one of this suite's synthetic `HH:MM:SSZ` stamps into a plain
    tick count, so a gap between two of them can be measured in ticks
    rather than merely ordered. `CLOCK_DATE_STUB` advances one simulated
    second per `date` call and never lets a run reach a full hour, so a
    bare minutes*60+seconds count is exact - no calendar arithmetic
    needed."""
    hh, mm, ss = stamp.rstrip("Z").split("T")[1].split(":")
    return int(hh) * 3600 + int(mm) * 60 + int(ss)


def test_step_itself_refreshes_the_heartbeat_across_a_rollback_transition(radios):
    """The test above proves the "rollback" phase never goes silent, but
    that claim survives on `compose()`'s own bracketing heartbeat and
    `verify_bluetooth`'s own per-iteration one alone - both fire whether or
    not `step()` refreshes anything. Proven experimentally: deleting only
    the `refresh_heartbeat` call from `step()`'s `$ROLLING` branch
    (deploy/updater/radios-once.sh:592) still leaves
    `test_the_heartbeat_advances_through_a_rollback` passing, because
    every sample it inspects is taken at a `sleep` inside
    `verify_bluetooth`'s own loop, which has already refreshed the
    heartbeat itself by the time that `sleep` runs - `step()`'s own two
    calls (`step apply_bluetooth`, `step verify_bluetooth`) are invisible
    to a check that only asks "did the timestamp move at all". So the
    commit's claim that the heartbeat also advances "at each rollback
    step" - as opposed to merely across it, via the calls it happens to
    wrap - was not actually pinned by any test.

    This test isolates `step()`'s own contribution using the fixed,
    one-simulated-second-per-`date`-call clock (`CLOCK_DATE_STUB`) rather
    than mere ordering: `step apply_bluetooth` and `step verify_bluetooth`
    are each one `refresh_heartbeat` call, and `refresh_heartbeat` touches
    two files, so together they are worth exactly 4 ticks of the shared
    clock - on top of whatever `compose()` and `verify_bluetooth`'s own
    loop already contribute between the same two points. Measured
    empirically against this exact scenario (`uv run pytest -k
    test_step_itself_refreshes_the_heartbeat_across_a_rollback_transition`,
    recorded in task-7e-report.md): the gap from the last forward-pass
    `verify_bluetooth` sample to the first `rollback` sample is 8 ticks
    with `step()`'s refresh removed and 12 ticks with it restored - a
    threshold of 10 sits cleanly between the two and asserts on the
    ticks(!) rather than on which one of two hardcoded runs happened to
    execute.

    Fault to prove it: delete the `refresh_heartbeat` call from `step()`'s
    `$ROLLING` branch (deploy/updater/radios-once.sh:592)."""
    (radios.sys_bluetooth / "hci1").mkdir()
    run, log = _heartbeat_run(radios)
    (radios.fake / "matter_up").write_text("no\n", encoding="utf-8")
    _request(radios, thread=None, bluetooth={"adapter": 1})
    result, _, state = run()

    assert result.returncode == 0, result.stderr
    assert state["phase"] == "failed"
    assert state["rolled_back"] is True
    forward_samples = _samples(log, "verify_bluetooth")
    rollback_samples = _samples(log, "rollback")
    assert forward_samples, forward_samples
    assert rollback_samples, rollback_samples
    last_forward_updater = forward_samples[-1][1]
    first_rollback_seen = rollback_samples[0][0]
    gap_ticks = _seconds(first_rollback_seen) - _seconds(last_forward_updater)
    assert gap_ticks >= 10, (gap_ticks, forward_samples, rollback_samples)


def test_the_heartbeat_leaves_the_update_job_record_alone(radios):
    """The restraint half, the same one update-once.sh's own
    `refresh_heartbeat()` observes: a heartbeat may move a timestamp and
    touch nothing else. This script now writes into `state.json`, which
    belongs to the OTHER worker - the update job's record of what it did,
    and the sidecar's own reported version, which `api/update.py` reads.
    Rewriting that file from here rather than editing one field in it
    would quietly destroy all of it.

    Fault to prove it: make `refresh_heartbeat` copy the radios state over
    it (`cp "$STATE" "$UPDATE_STATE"`) instead of calling `touch_seen_at`."""
    run, _ = _heartbeat_run(radios)
    (radios.fake / "matter_up").write_text("no\n", encoding="utf-8")
    (radios.sys_bluetooth / "hci1").mkdir()
    _request(radios, thread=None, bluetooth={"adapter": 1})
    result, _, state = run()

    assert result.returncode == 0, result.stderr
    update_state = json.loads((radios.update_dir / "state.json").read_text(encoding="utf-8"))
    assert update_state["updater_seen_at"] > UPDATE_STATE_SEED["updater_seen_at"]
    assert {k: v for k, v in update_state.items() if k != "updater_seen_at"} == {
        k: v for k, v in UPDATE_STATE_SEED.items() if k != "updater_seen_at"
    }
    assert state["error"] == "verify_bluetooth_failed"


def test_a_both_radios_rollback_still_attempts_the_other_radio_when_one_fails(radios):
    """Fix round 1, cheap item 2: apply_and_verify (the forward pass)
    deliberately returns at the first failure, but reusing it for the
    rollback pass too meant a request changing BOTH radios, where Thread's
    forward verify failed, could leave otbr on the NEW stick forever if the
    Bluetooth rollback (checked first) also failed - the function returned
    before ever attempting Thread's own rollback.

    Fault to prove it: call `apply_and_verify` for the rollback dispatch
    instead of the best-effort `rollback_and_verify`.

    A static matter_up flag cannot tell the forward verify call apart from
    the rollback's own - both read the identical file - so this counts
    curl's own invocations instead: the first (the forward verify) reports
    matter-server up; every one after (the rollback's own re-verify)
    reports it down."""
    (radios.sys_bluetooth / "hci1").mkdir()
    (radios.fake / "thread_mode").write_text("never")
    (radios.bindir / "curl").write_text(
        "#!/bin/sh\n"
        'printf \'curl %s\\n\' "$*" >> "$STUB_LOG"\n'
        f'n="$(cat "{radios.fake}/curl_calls" 2>/dev/null || echo 0)"\n'
        f'echo $((n + 1)) > "{radios.fake}/curl_calls"\n'
        '[ "$n" -eq 0 ] && exit 0\n'
        "exit 7\n",
        encoding="utf-8",
    )
    (radios.bindir / "curl").chmod(0o755)
    _request(radios, bluetooth={"adapter": 1})
    _, calls, state = radios()
    assert (state["phase"], state["error"]) == ("failed", "verify_thread_failed")
    assert state["healthy"] is False
    assert len(_compose(calls, "up", "matter-server")) == 2
    assert len(_compose(calls, "up", "otbr")) == 2


# ---------------------------------------------------------------------------
# Review round 2 (2026-09-12): a pass that was killed rather than finished,
# and the anti-drift checks for the two lists this script shares with the
# bridge and the card.
# ---------------------------------------------------------------------------


def _seed_dead_pass(radios, phase: str, job_id: str = "job-1") -> None:
    """Exactly what a killed pass leaves on disk: a non-terminal phase, the
    handled marker written when the request was accepted, and the request
    itself still sitting there unconsumed."""
    (radios.update_dir / "radios-state.json").write_text(
        json.dumps(
            {
                "id": job_id,
                "phase": phase,
                "steps": ["validate", "backup", "write", "apply_thread", "verify_thread"],
                "error": None,
                "rolled_back": False,
                "healthy": None,
                "current": {
                    "thread_enabled": True,
                    "thread_device": "/dev/ttyUSB0",
                    "bluetooth_adapter": 0,
                    "otbr_running": True,
                },
                "capable": True,
                "capable_reason": None,
                "seen_at": "2026-09-11T20:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (radios.update_dir / "radios-handled").mkdir(exist_ok=True)
    (radios.update_dir / "radios-handled" / job_id).write_text("", encoding="utf-8")
    _request(radios)


@pytest.mark.parametrize(
    "phase",
    [
        "validate",
        "backup",
        "write",
        "apply_bluetooth",
        "verify_bluetooth",
        "apply_thread",
        "verify_thread",
        "rollback",
    ],
)
def test_an_interrupted_pass_heals_into_a_terminal_phase(radios, phase):
    """One invocation is exactly one pass, so a non-terminal phase found at
    startup proves the previous pass died - `docker stop` (entrypoint.sh
    forwards the SIGTERM), a host power-off, the 600-second worker timeout,
    an OOM kill.

    Before the self-heal this state was PERMANENT, and it stranded the
    user: `load_previous_state` read the dead phase, `write_state`
    re-asserted it with a FRESH `seen_at`, and the handled marker sent the
    request check straight to `exit 0`. The card then saw a perfectly
    healthy heartbeat over a job that never advanced - so the stall
    detection never fired either - which left the step list frozen, both
    selects disabled, Apply hidden, and every POST /api/radios a 409 from
    `request_radios`. It survived a reload, a logout and a new browser,
    and its only exit was deleting radios-state.json over SSH.

    The discriminating assertion is the SECOND invocation, not the exit
    status: the unfixed script also exits 0 on both passes (the reviewer
    measured `rc=0 phase=apply_thread` twice, with the `seen_at` a second
    apart and no compose call at all). What it never does is reach a phase
    the bridge counts as terminal.

    Fault to prove it: delete the `case "$JOB_PHASE" in` self-heal arm at
    the end of `load_previous_state`."""
    from loxmatter.radios.sidecar import TERMINAL_PHASES

    _seed_dead_pass(radios, phase=phase)

    first_run, calls, first = radios()
    assert first_run.returncode == 0, first_run.stderr
    assert first["phase"] in TERMINAL_PHASES
    assert (first["phase"], first["error"]) == ("failed", "interrupted")
    # Nothing is invented about a job nobody watched finish: no claim that
    # the previous setting came back, and no health verdict.
    assert first["rolled_back"] is False
    assert first["healthy"] is None

    _, calls_after, second = radios()
    assert second["phase"] in TERMINAL_PHASES
    assert (second["phase"], second["error"]) == ("failed", "interrupted")

    # Healing is a report, not a resumption: the dead job is not picked up
    # and finished behind the user's back.
    assert _mutating_docker_calls(calls) == []
    assert _mutating_docker_calls(calls_after) == []


def test_a_failed_env_write_restores_the_backup(radios):
    """Round 2, carried Minor: `env_set_or_fail` wrote its state and exited
    without ever restoring `$BACKUP`, leaving a possibly-truncated .env
    beside a fresh, complete, unused copy of the good one - while the
    recovery it needed already existed three lines away in the rollback
    path.

    Makes env_set's own copy step fail while leaving everything else
    writable, so the restore can actually succeed: the stubbed `cat`
    refuses exactly the `.radios-tmp` file env_set writes and delegates
    every other read - the backup's included - to the real binary. (The
    read-only-.env test above is the other half of this path, where the
    restore cannot succeed because the file was never writable to begin
    with; that case still reports env_write_failed and rolled_back false.)

    Fault to prove it: drop the `cat "$BACKUP" > "$(env_target)"` branch
    from `env_set_or_fail`."""
    (radios.bindir / "cat").write_text(
        "#!/bin/sh\n"
        'printf \'cat %s\\n\' "$*" >> "$STUB_LOG"\n'
        'case "$*" in\n'
        "  *.radios-tmp) exit 1 ;;\n"
        "esac\n"
        f'exec {radios.real_cat} "$@"\n',
        encoding="utf-8",
    )
    (radios.bindir / "cat").chmod(0o755)
    before = radios.env_file.read_bytes()
    _request(radios, bluetooth={"adapter": 1})
    (radios.sys_bluetooth / "hci1").mkdir()

    _, calls, state = radios()
    assert (state["phase"], state["error"]) == ("failed", "env_write_failed")
    assert state["rolled_back"] is True
    assert radios.env_file.read_bytes() == before
    assert _mutating_docker_calls(calls) == []


def _script_error_keys() -> set[str]:
    """Every `web.radios.reason.*` key this script can put in the state
    file, read out of the script itself rather than listed by hand - a
    hand-kept list would only ever prove it agrees with itself, which is
    exactly how the two missing keys got in.

    Comment lines are dropped first, so prose like "reject it" cannot be
    mistaken for a call site."""
    script = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    keys = set()
    keys.update(re.findall(r"\breject ([a-z][a-z0-9_]*)", code))
    keys.update(re.findall(r"\bwrite_state failed ([a-z][a-z0-9_]*)", code))
    keys.update(re.findall(r"\bCAPABLE_REASON=([a-z][a-z0-9_]*)", code))
    keys.update(re.findall(r"\bJOB_ERROR=([a-z][a-z0-9_]*)", code))
    # `ERROR_KEY="${FAILED_STEP}_failed"` - the key is built from whichever
    # step set FAILED_STEP.
    keys.update(f"{step}_failed" for step in re.findall(r"\bFAILED_STEP=([a-z][a-z0-9_]*)", code))
    return keys


def test_every_error_key_the_script_can_write_has_a_reason_text():
    """The test that would have caught `env_write_failed` and
    `env_restore_failed` shipping with no text at all. The existing
    `test_the_radios_texts_exist_in_both_languages` iterates the keys that
    EXIST in strings.yaml, so a key the script writes and the table has
    never heard of is invisible to it - and `radiosReason()` then falls
    back, leaving the user with "Failed (an unknown reason), previous
    setting restored." for two disk failures, the place the cause matters
    most.

    Fault to prove it: delete `web.radios.reason.env_write_failed` from
    strings.yaml."""
    from loxmatter import i18n

    keys = _script_error_keys()
    # The extraction itself has to be shown to work: a regex that quietly
    # stopped matching would make every assertion below vacuously true.
    assert {
        "request_malformed",
        "host_dev_not_mounted",
        "env_backup_failed",
        "env_write_failed",
        "env_restore_failed",
        "interrupted",
        "verify_thread_failed",
    } <= keys
    assert len(keys) >= 15

    table = set(i18n.strings_with_prefix("web.radios.reason."))
    assert sorted(key for key in keys if f"web.radios.reason.{key}" not in table) == []


def test_the_terminal_phase_list_is_the_same_in_all_three_places():
    """The radios job's terminal phases are written out in full in three
    processes - this script's self-heal arm, `TERMINAL_PHASES` in
    radios/sidecar.py (which decides whether POST /api/radios is a 409),
    and the inline array in app.js's `radiosPhaseActive()` (which decides
    whether the card treats a job as running). Nothing at runtime ties
    them together, one of them being POSIX sh, so they are compared
    textually here.

    Drift is not cosmetic: a phase terminal in one place and not another
    means a job the bridge thinks is finished while the card still shows
    it running, or a pass that self-heals a phase the bridge still refuses
    new requests for.

    Fault to prove it: drop `unchanged` from the script's case arm."""
    from loxmatter.radios.sidecar import TERMINAL_PHASES

    script = SCRIPT.read_text(encoding="utf-8")
    arm = re.search(r'case "\$JOB_PHASE" in\n\s*([a-z|]+)\)', script)
    assert arm, "load_previous_state must decide the self-heal with a case arm on $JOB_PHASE"
    assert set(arm.group(1).split("|")) == set(TERMINAL_PHASES)

    app_js = (ROOT / "src" / "loxmatter" / "web" / "app.js").read_text(encoding="utf-8")
    inline = re.search(r"radiosPhaseActive\(\)\s*\{.*?\[([^\]]*)\]", app_js, flags=re.DOTALL)
    assert inline, "app.js's radiosPhaseActive() must test an inline phase array"
    assert set(json.loads(f"[{inline.group(1)}]")) == set(TERMINAL_PHASES)


def _lines_between(calls: str, first: str, second: str) -> list[str]:
    """The stub-log lines after the first line containing `first` and before
    the next line after it containing `second`."""
    lines = calls.splitlines()
    start = next(i for i, line in enumerate(lines) if first in line)
    end = next(i for i in range(start + 1, len(lines)) if second in lines[i])
    return lines[start + 1 : end]


def test_the_thread_verification_waits_60_s_before_its_fix_and_150_s_in_all(radios):
    """13 September 2026: a normal attach took 22-35 s on the Pi, the fix
    restarted otbr after 30 s, and three enable requests failed because the
    restarted agent did not come back within the rest of the 90 s. The
    defaults, run here with the default poll of 5 s against an agent that
    never attaches, are now 60 s and 150 s.

    Fault to prove it: put the defaults back to 30 and 90 - the log says
    "after 30s" and the verification polls 18 times, not 30."""
    (radios.fake / "thread_mode").write_text("never")
    _request(radios)
    _, calls, state = radios(
        LOXMATTER_RADIOS_THREAD_TIMEOUT="",
        LOXMATTER_RADIOS_THREAD_FIX_AFTER="",
        LOXMATTER_RADIOS_POLL_SECONDS="",
    )
    assert (state["phase"], state["error"]) == ("failed", "verify_thread_failed")
    log = (radios.update_dir / "radios-log.txt").read_text(encoding="utf-8")
    assert re.findall(r"no Thread state after (\d+)s", log) == ["60", "60"]
    forward = _lines_between(calls, "--force-recreate otbr", "--force-recreate otbr")
    polls = [line for line in forward if "ot-ctl state" in line]
    assert len(polls) == 30
    before_fix = _lines_between(calls, "--force-recreate otbr", "restart otbr")
    assert len([line for line in before_fix if "ot-ctl state" in line]) == 13


def test_a_rollback_saves_the_otbr_log_before_it_recreates_the_container(radios):
    """The recreate takes the failed agent's log with it, and on 13 September
    2026 nothing else had it: rsyslog inside the container had not run for
    two days. The last 400 lines are saved under the request id first, and
    the job's own log names the file.

    Fault to prove it: remove the block that saves the log - the file does
    not exist and no `docker logs` call precedes the rollback's recreate."""
    (radios.fake / "thread_mode").write_text("never")
    _request(radios)
    _, calls, state = radios()
    assert state["rolled_back"] is True
    saved = radios.update_dir / "radios-otbr-job-1.log"
    assert "HandleRcpTimeout()" in saved.read_text(encoding="utf-8")
    log = (radios.update_dir / "radios-log.txt").read_text(encoding="utf-8")
    assert "radios-otbr-job-1.log" in log
    lines = calls.splitlines()
    saving = next(
        i for i, line in enumerate(lines) if "docker logs --timestamps --tail 400 otbr" in line
    )
    recreates = [i for i, line in enumerate(lines) if "--force-recreate otbr" in line]
    assert len(recreates) == 2
    assert recreates[0] < saving < recreates[1]


def test_a_rollback_to_thread_off_saves_the_otbr_log_before_it_removes_the_container(radios):
    (radios.fake / "otbr_state").unlink()
    (radios.fake / "thread_mode").write_text("never")
    _request(radios)
    _, calls, state = radios()
    assert state["rolled_back"] is True
    lines = calls.splitlines()
    saving = next(i for i, line in enumerate(lines) if "docker logs" in line)
    removal = next(i for i, line in enumerate(lines) if line in _compose(calls, "rm", "otbr"))
    assert saving < removal
    assert (radios.update_dir / "radios-otbr-job-1.log").is_file()


def test_only_the_newest_five_saved_otbr_logs_are_kept(radios):
    """Fault to prove it: remove the prune - seven files remain."""
    for index in range(6):
        old = radios.update_dir / f"radios-otbr-old-{index}.log"
        old.write_text("old\n", encoding="utf-8")
        os.utime(old, (1_700_000_000 + index, 1_700_000_000 + index))
    (radios.fake / "thread_mode").write_text("never")
    _request(radios)
    radios()
    kept = sorted(path.name for path in radios.update_dir.glob("radios-otbr-*.log"))
    assert kept == [
        "radios-otbr-job-1.log",
        "radios-otbr-old-2.log",
        "radios-otbr-old-3.log",
        "radios-otbr-old-4.log",
        "radios-otbr-old-5.log",
    ]


def test_a_failure_to_save_the_otbr_log_does_not_stop_the_rollback(radios):
    (radios.fake / "thread_mode").write_text("never")
    (radios.fake / "logs_status").write_text("1")
    _request(radios)
    _, calls, state = radios()
    assert state["rolled_back"] is True
    assert len(_compose(calls, "up", "otbr")) == 2
    log = (radios.update_dir / "radios-log.txt").read_text(encoding="utf-8")
    assert "could not save the otbr log" in log


def test_a_job_that_succeeds_saves_no_otbr_log(radios):
    _request(radios)
    _, calls, state = radios()
    assert state["phase"] == "done"
    assert "docker logs" not in calls
    assert list(radios.update_dir.glob("radios-otbr-*.log")) == []
