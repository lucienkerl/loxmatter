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
import subprocess
import threading
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
    "jq",
    "cut",
    "wc",
    "readlink",
    "cksum",
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
esac
exit 0
"""

CURL_STUB = r"""#!/bin/sh
printf 'curl %s\n' "$*" >> "$STUB_LOG"
[ "$(cat "$FAKE/matter_up" 2>/dev/null || echo yes)" = yes ] && exit 0
exit 7
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
            [str(SCRIPT)], capture_output=True, text=True, env=env, check=False, timeout=30
        )
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
        state_file = update_dir / "radios-state.json"
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else None
        return result, calls, state

    run.env_file, run.update_dir, run.fake = env_file, update_dir, fake
    run.host_dev, run.sys_bluetooth, run.log = host_dev, sys_bluetooth, log
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
        (
            {"thread": {"enabled": True, "device": "/dev/serial/by-id/usb-Gone"}},
            "thread_device_not_found",
        ),
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


def test_the_value_used_comes_from_a_single_read_of_the_request(radios):
    """The request path is a FIFO, which can hand its bytes to only ONE
    reader; a second attempt to open it for reading would hang (no writer
    left) or see empty content. A script that re-reads $REQUEST for the
    schema check and again for the three value reads cannot pass this test
    reliably; a script that copies it once into a private snapshot and
    reads only the snapshot afterwards always can.

    A genuine mid-run swap of the live file (the attack this closes) is not
    reliably reproducible from outside the script in a fast, deterministic
    test - the whole pass completes in well under a second - so this proves
    the stronger property instead: every check and every value read that
    matters for the outcome is satisfiable from data obtained through
    exactly one read of $REQUEST."""
    radios.env_file.write_text(
        radios.env_file.read_text().replace("/dev/ttyUSB0", f"/dev/serial/by-id/{SONOFF}")
    )
    before = radios.env_file.read_bytes()
    request_path = radios.update_dir / "radios-request.json"
    os.mkfifo(request_path)
    body = json.dumps(
        {
            "id": "job-1",
            "thread": {"enabled": True, "device": f"/dev/serial/by-id/{SONOFF}"},
            "bluetooth": {"adapter": 0},
            "requested_at": "2026-09-11T20:00:00Z",
        }
    ).encode("utf-8")

    def feed() -> None:
        with open(request_path, "wb") as pipe:
            pipe.write(body)

    writer = threading.Thread(target=feed)
    writer.start()
    try:
        _, calls, state = radios()
    finally:
        writer.join(timeout=5)
    assert state["phase"] == "unchanged"
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


def test_a_changing_request_is_not_applied_yet(radios):
    """TRANSITIONAL (Task 4): replaced by the flow tests of Task 4."""
    _request(radios, bluetooth={"adapter": 0})
    _, calls, state = radios()
    assert (state["phase"], state["error"]) == ("failed", "apply_not_implemented")
    assert _mutating_docker_calls(calls) == []
