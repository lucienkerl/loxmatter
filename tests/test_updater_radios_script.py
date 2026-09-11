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
    included - except that single `cat` call."""
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
