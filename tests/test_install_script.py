"""Behavior tests for install.sh.

The script changes other people's machines: it installs packages, calls sudo,
clones and starts containers. What's checked is therefore WHICH commands it
chooses - not what they do. For this it runs against a sealed
PATH made of two directories:

  bin/  fake binaries (docker, git, sudo, apt-get, curl, uname, ip,
        hostname, usermod). Each logs its call to $STUB_LOG
        and exits successfully. A tool "is missing" simply because
        its stub isn't created - so the PATH must not contain anything
        that genuinely exists on the test machine.
  sys/  symlinks to exactly the real tools the script legitimately
        needs (sh, awk, sed, grep, ...). `id` is deliberately NOT among
        them, but is a stub instead - otherwise the behavior would depend
        on whether the test suite happens to run as root.

The stubs also sit unchanged in templates/. The apt-get stub
copies them from there into bin/, and the curl stub, for get.docker.com,
prints a script that does the same thing for `docker`. That way a run in
which a tool is missing and gets installed afterward behaves like on a real
host: afterward it's there.

The child processes run with start_new_session=True, i.e. without a
controlling terminal. That makes every attempt to open /dev/tty fail
and the non-interactive branch deterministic - independent of whether
pytest happens to run in a terminal or in CI.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "install.sh"

# Real tools the script is allowed to use. Everything else comes from
# a stub or counts as not installed.
SYSTEM_TOOLS = (
    "sh",
    "cat",
    "grep",
    "sed",
    "awk",
    "tr",
    "od",
    "mkdir",
    "rm",
    "mv",
    "sleep",
    "chmod",
    "cp",
    "printf",
    "true",
    "false",
    "env",
    "tail",
    "head",
    "mktemp",
)

_UNAME = """case "${1-}" in
  -m) echo x86_64 ;;
  *) echo Linux ;;
esac
"""

_IP = 'echo "default via 10.0.1.1 dev eth0 proto dhcp src 10.0.1.56"\n'

_HOSTNAME = 'echo "10.0.1.56"\n'

# Not a real tool: otherwise every root test would depend on who
# the test suite runs as. FAKE_UID=0 turns it into a root run.
_ID = """case "${1-}" in
  -un|-nu|-n) echo "tester" ;;
  *) echo "${FAKE_UID-1000}" ;;
esac
"""

_OPENSSL = """case "${1-} ${2-}" in
  "rand -hex") echo "aa11bb22cc33dd44ee55ff66aa77bb88cc99dd00ee11ff22aa33bb44cc55dd66" ;;
esac
exit 0
"""

# Fetches the "installed" packages from templates/ into bin/ - afterward they
# are really there, just like after a real apt-get.
_APT_GET = """for pkg in "$@"; do
  if [ -f "$STUB_TEMPLATES/$pkg" ]; then
    cp "$STUB_TEMPLATES/$pkg" "$STUB_BIN/$pkg"
    chmod 755 "$STUB_BIN/$pkg"
  fi
done
exit 0
"""

_SUDO = """cmd=$1
shift
exec "$cmd" "$@"
"""

_DOCKER = """if [ "${1-}" = "compose" ] && [ "${2-}" = "version" ]; then
  echo "Docker Compose version v2.30.0"
fi
if [ "${1-}" = "ps" ]; then
  for container in ${FAKE_CONTAINERS-}; do
    echo "$container"
  done
fi
if [ "${1-}" = "compose" ] && [ "${2-}" = "ps" ]; then
  for service in ${FAKE_SERVICES-otbr matter-server loxmatter}; do
    echo "$service"
  done
fi
exit 0
"""

# On `clone`, creates a checkout that contains the REAL stack files -
# that way the tests run against the actual docker-compose.yml and .env.example.
_GIT = """if [ "${1-}" = "-C" ]; then shift 2; fi
case "${1-}" in
  clone)
    for a in "$@"; do target="$a"; done
    mkdir -p "$target/deploy/testhost" "$target/scripts"
    : > "$target/Dockerfile"
    cp "$LOXMATTER_REPO/deploy/testhost/docker-compose.yml" "$target/deploy/testhost/"
    cp "$LOXMATTER_REPO/deploy/testhost/.env.example" "$target/deploy/testhost/"
    cp "$LOXMATTER_REPO/scripts/update.sh" "$target/scripts/"
    ;;
  rev-list) echo "${FAKE_BEHIND-0}" ;;
esac
exit 0
"""

# The script now downloads get.docker.com via `-o <file>` instead of
# piping it - the stub therefore has to evaluate `-o` itself and write the
# body there instead of just printing it. Without `-o` (e.g. during the
# health check), the output goes to stdout as before.
_CURL = """out=""
url=""
prev=""
for a in "$@"; do
  case "$prev" in
    -o)
      out="$a"
      prev=""
      continue
      ;;
  esac
  case "$a" in
    -o) prev="-o" ;;
    -*) ;;
    *) url="$a" ;;
  esac
done
body=""
case "$url" in
  *get.docker.com*)
    body="cp '$STUB_TEMPLATES/docker' '$STUB_BIN/docker' && chmod 755 '$STUB_BIN/docker'"
    ;;
  *health*) body='{"status":"ok"}' ;;
esac
if [ -n "$out" ]; then
  printf '%s\\n' "$body" > "$out"
else
  printf '%s\\n' "$body"
fi
exit 0
"""

DEFAULT_STUBS = {
    "uname": _UNAME,
    "ip": _IP,
    "hostname": _HOSTNAME,
    "sudo": _SUDO,
    "docker": _DOCKER,
    "git": _GIT,
    "curl": _CURL,
    "openssl": _OPENSSL,
    "id": _ID,
    "apt-get": _APT_GET,
    "usermod": "exit 0\n",
}


class Result:
    def __init__(self, proc, home, log, tmpdir):
        self.returncode = proc.returncode
        self.output = proc.stdout + proc.stderr
        self.home = home
        self._log = log
        self._tmpdir = tmpdir

    @property
    def calls(self):
        if not self._log.exists():
            return []
        return [line for line in self._log.read_text().splitlines() if line]

    def called(self, prefix):
        return any(call.startswith(prefix) for call in self.calls)

    @property
    def env_file(self):
        return self.home / "loxmatter" / "deploy" / "testhost" / ".env"

    @property
    def tmpdir(self):
        return self._tmpdir


@pytest.fixture
def installer(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    sysdir = tmp_path / "sys"
    sysdir.mkdir()
    templates = tmp_path / "templates"
    templates.mkdir()
    log = tmp_path / "stub.log"
    # A dedicated TMPDIR, so `mktemp` in the script lands here instead of in
    # the real /tmp - only this way can it be checked whether a temporary
    # file is left behind.
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()

    for tool in SYSTEM_TOOLS:
        real = shutil.which(tool, path="/usr/bin:/bin:/usr/sbin:/sbin")
        if real is not None:
            (sysdir / tool).symlink_to(real)

    def build_env(env, omit, stubs, answers):
        active = dict(DEFAULT_STUBS)
        active.update(stubs or {})
        for name in omit:
            active.pop(name, None)
        for stale in list(bindir.iterdir()):
            stale.unlink()
        for stale in list(templates.iterdir()):
            stale.unlink()
        # A second installer() call in the same test (e.g. to run a second
        # time against an existing checkout) should only see ITS OWN calls
        # in result.calls - without this, the first run would remain in
        # the log and every "not called(...)" on the second run would
        # wrongly fail.
        if log.exists():
            log.unlink()

        def write(directory, name, body):
            path = directory / name
            path.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "{name} $*" >> "$STUB_LOG"\n{body}')
            path.chmod(0o755)

        # templates/ knows everything, bin/ only what's "there" on this host.
        for name, body in dict(DEFAULT_STUBS, **(stubs or {})).items():
            write(templates, name, body)
        for name, body in active.items():
            write(bindir, name, body)

        full_env = {
            "PATH": f"{bindir}:{sysdir}",
            "HOME": str(home),
            "STUB_LOG": str(log),
            "STUB_BIN": str(bindir),
            "STUB_TEMPLATES": str(templates),
            "TMPDIR": str(tmpdir),
            "LOXMATTER_REPO": str(REPO_ROOT),
            "LOXMATTER_DIR": str(home / "loxmatter"),
            # Written to .env as-is, without being contacted. Tests about
            # the no-address path set it to "".
            "MINISERVER_IP": "10.0.1.99",
            # /sys/class/rfkill doesn't exist on macOS and is nowhere
            # writable. This path doesn't exist by default -
            # check_rfkill then finds nothing, just like on a host without
            # rfkill. Tests for check_rfkill point this at a
            # prepared directory instead.
            "RFKILL_DIR": str(tmp_path / "no-rfkill-here"),
            # The installer lists USB sticks and Bluetooth adapters from
            # these. Pointing them nowhere by default keeps every test
            # independent of what the machine running pytest has plugged
            # in; tests about the menus build a directory and override them.
            "SERIAL_BY_ID_DIR": str(tmp_path / "no-by-id-here"),
            "SERIAL_DEV_DIR": str(tmp_path / "no-dev-here"),
            "BT_SYS_DIR": str(tmp_path / "no-bluetooth-here"),
        }
        # Every run has no controlling terminal (start_new_session=True), so
        # every question takes the non-interactive branch - except when a
        # test hands in answers. LOXMATTER_TTY points the installer at a
        # file instead of /dev/tty; one answer per line, an empty string
        # takes the default. An empty list is a terminal that answers
        # nothing, so any question at all aborts the run.
        if answers is not None:
            answer_file = tmp_path / "answers"
            answer_file.write_text("".join(f"{answer}\n" for answer in answers))
            full_env["LOXMATTER_TTY"] = str(answer_file)
        full_env.update(env or {})
        return full_env

    def run(*args, env=None, omit=(), stubs=None, answers=None):
        full_env = build_env(env, omit, stubs, answers)
        proc = subprocess.run(
            ["/bin/sh", str(INSTALLER), *args],
            env=full_env,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            timeout=120,
            check=False,
        )
        return Result(proc, home, log, tmpdir)

    # For tests that need to act on the RUNNING process (e.g. send a
    # signal) instead of just checking the finished result. Same
    # structure as `run`, just with Popen instead of subprocess.run, so `run`
    # itself stays unchanged.
    def start(*args, env=None, omit=(), stubs=None):
        full_env = build_env(env, omit, stubs, None)
        return subprocess.Popen(
            ["/bin/sh", str(INSTALLER), *args],
            env=full_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

    run.start = start
    run.log = log
    run.tmpdir = tmpdir
    return run


def test_help_exits_successfully(installer):
    result = installer("--help")
    assert result.returncode == 0
    assert "--dry-run" in result.output
    assert "/dev/serial/by-id" in result.output


def test_an_unknown_argument_aborts(installer):
    result = installer("--nope")
    assert result.returncode == 2
    assert "Unknown argument" in result.output


def test_dir_without_a_value_does_not_take_the_next_flag(installer):
    # --dir --dry-run used to take "--dry-run" uncomplainingly as the path,
    # DRY_RUN stayed 0, and the run went to work with a directory named
    # "--dry-run" instead of aborting.
    result = installer("--dir", "--dry-run")
    assert result.returncode == 2
    assert "--dir needs a path" in result.output
    assert not (result.home / "loxmatter").exists()


def test_macos_is_refused(installer):
    result = installer(stubs={"uname": "echo Darwin\n"})
    assert result.returncode == 2
    assert "needs Linux" in result.output
    assert not (result.home / "loxmatter").exists()


def test_an_unsupported_architecture_is_refused(installer):
    riscv = 'case "${1-}" in\n  -m) echo riscv64 ;;\n  *) echo Linux ;;\nesac\n'
    result = installer(stubs={"uname": riscv})
    assert result.returncode == 2
    assert "riscv64" in result.output


def test_without_sudo_and_without_root_it_aborts_before_cloning(installer):
    result = installer(omit=("docker", "sudo"))
    assert result.returncode == 2
    assert "docker" in result.output
    assert not result.called("git clone")


def test_all_missing_tools_are_named_at_once(installer):
    result = installer(omit=("git", "curl", "openssl", "docker", "sudo"))
    assert result.returncode == 2
    for tool in ("git", "curl", "openssl", "docker"):
        assert tool in result.output


def test_without_apt_get_it_names_the_packages_and_aborts(installer):
    result = installer(omit=("git", "apt-get"))
    assert result.returncode == 2
    assert "apt-get" in result.output
    assert "git" in result.output
    assert not result.called("git clone")


def test_root_is_warned_but_not_stopped(installer):
    result = installer(env={"FAKE_UID": "0"})
    assert result.returncode == 0
    assert "Running as root" in result.output


def test_without_a_radio_module_it_falls_back_to_wifi(installer):
    result = installer()
    assert result.returncode == 0
    assert "Operating mode: wifi" in result.output


def test_thread_without_a_device_and_without_a_terminal_aborts(installer):
    result = installer(env={"LOXMATTER_MODE": "thread"})
    assert result.returncode == 2
    assert "no USB stick" in result.output


def test_thread_with_a_device_from_the_environment(installer):
    result = installer(env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"})
    assert result.returncode == 0
    assert "Operating mode: thread" in result.output


def test_an_unknown_operating_mode_aborts(installer):
    result = installer(env={"LOXMATTER_MODE": "zigbee"})
    assert result.returncode == 2
    assert "thread" in result.output


def test_an_invalid_miniserver_ip_aborts_before_cloning(installer):
    result = installer(env={"MINISERVER_IP": "not.an.ip"})
    assert result.returncode == 2
    assert "not a valid IPv4" in result.output
    assert not result.called("git clone")


def test_an_over_long_octet_is_refused(installer):
    # `[ n -gt 255 ]` fails on a number beyond the integer range
    # with an error instead of with "false" - and a failed test in
    # `if` reads like "not greater". This address was therefore once
    # considered valid.
    result = installer(env={"MINISERVER_IP": "1.2.3.999999999999999999999"})
    assert result.returncode == 2
    assert "not a valid IPv4" in result.output


def test_leading_zeros_are_refused(installer):
    # Various consumers read 010 as octal, not as 10.
    result = installer(env={"MINISERVER_IP": "01.02.03.04"})
    assert result.returncode == 2
    assert "not a valid IPv4" in result.output


def test_only_the_missing_packages_are_installed(installer):
    result = installer(omit=("git", "curl"))
    assert result.returncode == 0
    assert result.called("apt-get install -y git curl")
    assert not result.called("apt-get install -y git curl openssl")


def test_docker_comes_after_the_base_packages(installer):
    # get.docker.com itself needs curl - the order isn't cosmetic.
    result = installer(omit=("git", "curl", "docker"))
    assert result.returncode == 0
    apt = next(i for i, c in enumerate(result.calls) if c.startswith("apt-get install"))
    docker_install = next(i for i, c in enumerate(result.calls) if "get.docker.com" in c)
    assert apt < docker_install


def test_after_installing_docker_itself_everything_runs_via_sudo(installer):
    result = installer(omit=("docker",))
    assert result.returncode == 0
    assert result.called("usermod -aG docker")
    assert result.called("sudo docker")
    assert "log out and back in" in result.output


def test_an_existing_docker_is_not_reinstalled(installer):
    result = installer()
    assert result.returncode == 0
    assert not any("get.docker.com" in call for call in result.calls)
    assert not result.called("sudo docker")


# `compose up` fails - on a real host for example because the SD card ran
# out of space while extracting an image.
_DOCKER_UP_FAILS = _DOCKER.replace(
    "exit 0\n",
    'if [ "${1-}" = "compose" ] && [ "${2-}" = "up" ]; then exit 1; fi\nexit 0\n',
)


def test_after_installing_docker_the_log_hint_uses_sudo(installer):
    # This session is not in the 'docker' group yet: the plain command
    # would answer "permission denied" on docker.sock.
    result = installer(omit=("docker",), stubs={"docker": _DOCKER_UP_FAILS})
    assert result.returncode == 2
    assert "Could not start the stack" in result.output
    assert "&& sudo docker compose logs" in result.output


def test_with_an_existing_docker_the_log_hint_has_no_sudo(installer):
    result = installer(stubs={"docker": _DOCKER_UP_FAILS})
    assert result.returncode == 2
    assert "&& docker compose logs" in result.output
    assert "sudo docker compose logs" not in result.output


def test_after_installing_docker_findings_use_sudo(installer):
    result = installer(omit=("docker",), env={"FAKE_SERVICES": "loxmatter"})
    assert result.returncode == 0
    assert "&& sudo docker compose logs matter-server" in result.output


# ------------------------------------------------------------- phase three --


def test_it_clones_into_the_target_dir(installer):
    result = installer()
    assert result.returncode == 0
    assert result.called("git clone --branch main https://github.com/lucienkerl/loxmatter.git")
    assert (result.home / "loxmatter" / "deploy" / "testhost").is_dir()


def test_a_second_run_does_not_clone_again(installer):
    first = installer()
    assert first.returncode == 0
    second = installer()
    assert second.returncode == 0
    assert not second.called("git clone")
    assert "existing checkout" in second.output


def test_a_foreign_directory_is_refused(installer, tmp_path):
    foreign = tmp_path / "home" / "loxmatter"
    foreign.mkdir(parents=True)
    (foreign / "something.txt").write_text("not loxmatter")
    result = installer()
    assert result.returncode == 2
    assert "does not look like a loxmatter checkout" in result.output


def test_a_second_run_offers_no_console_update(installer):
    """Updates go through the web interface only: it backs up the database
    and rolls back a version that does not come up healthy. A rerun of the
    installer on a checkout that is behind neither fetches, nor offers
    scripts/update.sh, nor runs it - it points at System -> Version.

    Fault to prove it: call the removed update offer again."""
    first = installer()
    assert first.returncode == 0
    second = installer(env={"FAKE_BEHIND": "3"})
    assert second.returncode == 0
    assert "update.sh" not in second.output
    assert "new commits" not in second.output
    assert not any(" fetch " in f" {call} " for call in second.calls), second.calls
    assert "System -> Version" in second.output


def test_a_dry_run_changes_nothing(installer):
    # git and docker are deliberately PRESENT here (default stubs, no
    # omit=(...)): "no clone, no docker call" should hinge on the fact that
    # a dry run doesn't call them - not on the fact that they don't
    # exist on the test machine at all.
    result = installer("--dry-run")
    assert result.returncode == 0
    assert not result.called("apt-get")
    assert not result.called("sudo")
    assert not any("get.docker.com" in call for call in result.calls)
    assert not result.called("git clone")
    assert not result.called("docker compose up")
    assert "would run" in result.output


def test_a_failed_docker_download_aborts(installer):
    # `curl ... | sh` used to report success here when the download failed:
    # without pipefail, the status of the LAST command counts, and an `sh`
    # with empty input exits with 0. The run then continued into usermod.
    result = installer(omit=("docker",), stubs={"curl": "exit 6\n"})
    assert result.returncode == 2
    assert "Could not download" in result.output
    assert not result.called("usermod")


def test_an_empty_docker_download_aborts(installer):
    # curl can exit with 0 and still deliver nothing (an empty body behind
    # a flaky proxy). An empty script then runs through without error,
    # and the run wrongly reported that the compose plugin was missing.
    result = installer(omit=("docker",), stubs={"curl": "exit 0\n"})
    assert result.returncode == 2
    assert "was empty" in result.output
    assert not result.called("usermod")


def test_an_incomplete_docker_download_is_detected(installer):
    # curl can exit with 0 and deliver a truncated but syntactically valid
    # file - a real Docker install script starts with a long comment
    # header. It runs through without error and installs nothing. That
    # must be reported as such, not as a missing compose plugin.
    truncated = (
        'out=""\n'
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in\n'
        '    -o) out="$2"; shift ;;\n'
        "  esac\n"
        "  shift\n"
        "done\n"
        'if [ -n "$out" ]; then printf \'#!/bin/sh\\n# truncated\\ntrue\\n\' > "$out"; fi\n'
        "exit 0\n"
    )
    result = installer(omit=("docker",), stubs={"curl": truncated})
    assert result.returncode == 2
    assert "left no 'docker'" in result.output
    assert not result.called("usermod")


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="mktemp on macOS ignores TMPDIR - the check would always be true here",
)
def test_no_temporary_script_is_left_behind(installer):
    # The download lands in a file, not in a pipe - it must disappear
    # again no matter what, even if the run aborts.
    result = installer(omit=("docker",), stubs={"curl": "exit 6\n"})
    assert result.returncode == 2
    assert list(result.tmpdir.iterdir()) == []


@pytest.mark.skipif(
    sys.platform == "darwin",
    reason="mktemp on macOS ignores TMPDIR - the check would always be true here",
)
def test_sigint_cleans_up_the_temporary_file(installer):
    # Previously only demonstrated once manually via SIGINT, here as a test:
    # the download hangs, the signal goes to the whole process group (the
    # child process is itself the group leader via start_new_session=True -
    # a signal to it alone would only be noticed by a shell waiting on its
    # foreground child once that child ends), and afterward nothing must
    # be left in the temporary directory.
    # Only the Docker download may hang, or the signal would land before any
    # temporary file exists and the test would prove nothing.
    hanging_download = 'case "$*" in *get.docker.com*) sleep 30 ;; esac\nexit 0\n'
    proc = installer.start(omit=("docker",), stubs={"curl": hanging_download})
    deadline = time.time() + 10
    while True:
        if installer.log.exists() and "get.docker.com" in installer.log.read_text():
            break
        if time.time() > deadline:
            proc.kill()
            proc.wait()
            pytest.fail("curl stub did not start in time")
        time.sleep(0.05)

    # start_new_session=True makes proc.pid the process group id.
    os.killpg(proc.pid, signal.SIGINT)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        pytest.fail("Installer did not exit after SIGINT")

    assert proc.returncode == 130
    assert list(installer.tmpdir.iterdir()) == []


def test_a_checkout_without_a_compose_file_is_refused(installer, tmp_path):
    # Old clone from before deploy/testhost/: Dockerfile present, stack
    # missing. Without this case, the suite only tests the Dockerfile half
    # of the condition.
    old = tmp_path / "home" / "loxmatter"
    old.mkdir(parents=True, exist_ok=True)
    (old / "Dockerfile").write_text("FROM python:3.12-slim\n")
    result = installer()
    assert result.returncode == 2
    assert "does not look like a loxmatter checkout" in result.output
    assert not result.called("git clone")


def _env(result):
    return dict(
        line.split("=", 1)
        for line in result.env_file.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )


def test_a_wifi_run_turns_off_the_thread_profile(installer):
    result = installer()
    assert result.returncode == 0
    assert _env(result)["COMPOSE_PROFILES"] == ""


def test_a_thread_run_sets_the_profile_device_and_interface(installer):
    result = installer(env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"})
    assert result.returncode == 0
    values = _env(result)
    assert values["COMPOSE_PROFILES"] == "thread"
    assert values["RADIO_DEVICE"] == "/dev/ttyUSB0"
    assert values["BACKBONE_IF"] == "eth0"


def test_the_miniserver_ip_comes_from_the_environment(installer):
    result = installer(env={"MINISERVER_IP": "10.0.1.77"})
    assert _env(result)["MINISERVER_IP"] == "10.0.1.77"


def test_without_a_miniserver_ip_it_installs_and_leaves_the_address_to_the_web_interface(
    installer,
):
    """Design 2026-09-25, section 10: the address is entered under
    Settings -> Miniserver connection, not asked for here."""
    result = installer(env={"MINISERVER_IP": ""})
    assert result.returncode == 0
    assert "the address of your Loxone Miniserver" not in result.output
    assert _env(result)["MINISERVER_IP"] == ""
    assert "Settings -> Miniserver connection" in result.output


def test_a_passed_miniserver_ip_is_written_without_contacting_it(installer):
    result = installer(env={"MINISERVER_IP": "10.0.1.77"})
    assert result.returncode == 0
    assert _env(result)["MINISERVER_IP"] == "10.0.1.77"
    assert not any("10.0.1.77" in call for call in result.calls)


def test_a_token_is_generated(installer):
    result = installer()
    token = _env(result)["LOXMATTER_API_TOKEN"]
    assert len(token) == 64
    assert set(token) <= set("0123456789abcdef")


def test_without_openssl_the_token_falls_back_to_urandom(installer):
    result = installer(omit=("openssl",))
    assert result.returncode == 0
    token = _env(result)["LOXMATTER_API_TOKEN"]
    assert len(token) == 64
    assert set(token) <= set("0123456789abcdef")


def test_an_empty_urandom_fallback_aborts_loudly(installer):
    # gen_tokens's fallback pipe (`od ... | tr -d ' \n'`) runs without
    # pipefail: a failing od goes unnoticed, tr on empty input still
    # reports success. An empty token used to be silently reported as
    # "generated". openssl is deliberately present here (otherwise the
    # apt-get stub would "install" it and the fallback would never be
    # reached) - its `rand` call itself fails.
    result = installer(stubs={"openssl": "exit 1\n", "od": "exit 1\n"})
    assert result.returncode == 2
    assert "Could not generate LOXMATTER_API_TOKEN" in result.output
    # COMPOSE_PROFILES and the rest are already in .env at that point; the
    # abort has to say so rather than look like a clean stop.
    assert "partially written" in result.output


def test_a_second_run_leaves_the_env_untouched(installer):
    first = installer()
    before = first.env_file.read_bytes()
    second = installer()
    assert second.returncode == 0
    assert second.env_file.read_bytes() == before


def test_only_the_missing_key_is_added(installer):
    first = installer()
    text = first.env_file.read_text().replace(
        f"LOXMATTER_API_TOKEN={_env(first)['LOXMATTER_API_TOKEN']}",
        "LOXMATTER_API_TOKEN=",
    )
    first.env_file.write_text(text)
    second = installer(env={"MINISERVER_IP": "10.0.1.55"})
    values = _env(second)
    assert len(values["LOXMATTER_API_TOKEN"]) == 64
    # The existing address stays, even though the environment names a different one.
    assert values["MINISERVER_IP"] == "10.0.1.99"


def test_a_key_is_replaced_not_appended(installer):
    result = installer()
    lines = [
        line
        for line in result.env_file.read_text().splitlines()
        if line.startswith("COMPOSE_PROFILES=")
    ]
    assert len(lines) == 1


def test_a_backslash_in_the_value_is_preserved(installer):
    # `awk -v value=...` processes escapes: a backslash used to be
    # silently swallowed, \t turned into a real tab character.
    result = installer(env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": r"/dev/serial/by-id/a\tb"})
    assert result.returncode == 0
    assert r"RADIO_DEVICE=/dev/serial/by-id/a\tb" in result.env_file.read_text()


def test_an_env_without_a_trailing_newline_stays_intact(installer):
    # A hand-edited .env often ends without a trailing newline. An
    # appended key then merged with the last line and
    # destroyed its value.
    first = installer()
    assert first.returncode == 0
    text = first.env_file.read_text()
    keep = "\n".join(line for line in text.splitlines() if not line.startswith("COMPOSE_PROFILES="))
    first.env_file.write_text(keep.rstrip("\n"))  # deliberately without a trailing newline
    second = installer()
    assert second.returncode == 0
    values = dict(
        line.split("=", 1)
        for line in second.env_file.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    assert values["MINISERVER_IP"] == "10.0.1.99"
    assert "COMPOSE_PROFILES" in values


def test_an_undetectable_backbone_without_a_terminal_aborts_before_cloning(installer):
    # This used to abort inside configure, with COMPOSE_PROFILES and the
    # radio already written to .env. Asked in phase one, it now stops
    # before a single file exists.
    result = installer(
        env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"},
        stubs={"ip": "echo\n"},
    )
    assert result.returncode == 2
    assert "BACKBONE_IF" in result.output
    assert not result.called("git clone")
    assert not result.env_file.exists()


def test_a_conflicting_mode_is_reported_loudly(installer):
    # LOXMATTER_MODE=thread meets an existing .env with
    # COMPOSE_PROFILES= (wifi). "Operating mode: thread" and, six lines
    # later, "mode: wifi" used to silently contradict each other.
    first = installer()
    assert first.returncode == 0
    second = installer(env={"LOXMATTER_MODE": "thread"})
    assert second.returncode == 0
    assert "Operating mode: thread" in second.output
    assert "COMPOSE_PROFILES kept, mode: wifi" in second.output
    assert "wins over the requested" in second.output


def test_an_old_installation_keeps_its_border_router(installer):
    # A .env from before the compose profiles doesn't know COMPOSE_PROFILES.
    # An empty value would take the otbr service away from the next
    # `compose up` - so the running container decides instead.
    first = installer(env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"})
    assert first.returncode == 0
    without = "\n".join(
        line
        for line in first.env_file.read_text().splitlines()
        if not line.startswith("COMPOSE_PROFILES=")
    )
    first.env_file.write_text(without + "\n")
    second = installer(env={"FAKE_CONTAINERS": "otbr matter-server loxmatter"})
    assert second.returncode == 0
    values = dict(
        line.split("=", 1)
        for line in second.env_file.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    assert values["COMPOSE_PROFILES"] == "thread"


# --------------------------------------------------------- phase five/six --


def test_stack_is_started(installer):
    result = installer()
    assert result.returncode == 0
    assert result.called("docker compose up -d")
    assert (result.home / "loxmatter" / "deploy" / "testhost" / "data").is_dir()


def test_no_local_build_during_installation(installer):
    # ghcr.io has provided finished images (arm64, amd64) since 0.2.0. A `--build`
    # would force compose to build locally AND tag the result under the service's `image:` name -
    # a fresh installation would then carry a
    # LOCAL image named ghcr.io/lucienkerl/loxmatter:stable, which reports
    # itself as `dev`, and that is exactly what triggers the working copy hint
    # in the UI on a freshly installed, published
    # version. Without --build, `up` only builds if compose cannot
    # get an image at all (see the comment about `image:` in
    # docker-compose.yml) - the desired fallback for a host without
    # GHCR access.
    result = installer()
    assert result.returncode == 0
    assert not any("--build" in call for call in result.calls)


def test_health_check_runs(installer):
    result = installer()
    assert any("/health" in call for call in result.calls)
    assert "answers" in result.output


def test_an_unhealthy_service_still_delivers_the_rest(installer):
    # If /health doesn't respond, the container list is the most likely
    # explanation - it must not be aborted along with it. Only the health
    # branch of the curl stub fails; get.docker.com would stay untouched,
    # should this path ever be needed in another test.
    curl_health_fails = _CURL.replace(
        '*health*) body=\'{"status":"ok"}\' ;;',
        "*health*) exit 1 ;;",
    )
    result = installer(stubs={"curl": curl_health_fails})
    assert result.returncode == 2
    assert "does not answer" in result.output
    # The container list comes ONLY AFTER the health check - that's
    # exactly what used to fail to run.
    assert any("compose ps --services" in call for call in result.calls)
    assert "Web interface" in result.output


def test_the_health_port_comes_from_the_compose_file(installer):
    # The real stack listens on 8080 - that must come from the file, not
    # from the fallback value.
    result = installer()
    assert result.returncode == 0
    assert any(":8080/health" in call for call in result.calls)
    assert "assuming 8080" not in result.output


def test_an_ambiguous_port_falls_back_audibly(installer):
    # Two --listen entries: better to give up loudly than to silently pick
    # the wrong number and blame the service for it afterward.
    first = installer()
    compose = first.home / "loxmatter" / "deploy" / "testhost" / "docker-compose.yml"
    compose.write_text(compose.read_text() + '\n      - --listen\n      - "9090"\n')
    second = installer()
    assert second.returncode == 0
    assert "assuming 8080" in second.output
    assert not any(":9090/" in call for call in second.calls)


def test_a_missing_service_becomes_a_finding(installer):
    result = installer(env={"FAKE_SERVICES": "loxmatter"})
    assert result.returncode == 0
    assert "matter-server" in result.output
    assert "not running" in result.output


def test_a_thread_run_without_wpan_notes_it_instead_of_a_manual_workaround(installer):
    # check_thread used to print `docker exec otbr ...` commands to bring
    # otbr-agent up by hand and turn them into a finding. The updater
    # service's watchdog now does the same recovery on its own every
    # minute, so this is a note, not something the reader has to act on.
    result = installer(env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"})
    assert result.returncode == 0
    assert "docker exec otbr" not in result.output
    assert "Findings" not in result.output
    assert "watchdog" in result.output


def test_a_wifi_run_does_not_mention_thread_as_a_problem_at_all(installer):
    result = installer()
    assert result.returncode == 0
    assert "start-stop-daemon" not in result.output


def test_rfkill_checks_every_bluetooth_adapter_not_just_the_first(installer, tmp_path):
    # check_rfkill used to abort after the FIRST entry of type "bluetooth"
    # (`return 0` always matched, blocked or not) - a second adapter
    # never came up. rfkill0 is unblocked here, rfkill1 blocked: the
    # alphabetical glob order ensures the unblocked adapter comes
    # first.
    rfkill_dir = tmp_path / "rfkill"
    unblocked = rfkill_dir / "rfkill0"
    blocked = rfkill_dir / "rfkill1"
    for entry, soft in ((unblocked, "0"), (blocked, "1")):
        entry.mkdir(parents=True)
        (entry / "type").write_text("bluetooth\n")
        (entry / "soft").write_text(f"{soft}\n")
    result = installer(env={"RFKILL_DIR": str(rfkill_dir)})
    assert result.returncode == 0
    assert "rfkill1" in result.output
    assert "rfkill0" not in result.output


# -------------------------------------------------------------- phase seven --


def test_the_report_names_the_web_interface_and_the_password(installer):
    result = installer()
    assert result.returncode == 0
    assert "http://10.0.1.56:8080/" in result.output
    assert "set a password" in result.output


def test_the_thread_report_names_the_updater_watchdog_not_a_crontab_line(installer):
    # Nothing to set up by hand any more: the updater service runs the
    # watchdog itself every minute (deploy/updater/watchdog-once.sh).
    # Fault to prove it: print the old cron line again - these fail.
    result = installer(env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"})
    assert result.returncode == 0
    assert "crontab" not in result.output
    assert "otbr-watchdog.sh" not in result.output
    assert "updater service" in result.output


def test_the_wifi_report_points_to_the_radios_card(installer):
    # Adding Thread later used to mean editing COMPOSE_PROFILES and
    # RADIO_DEVICE in .env by hand; that now happens on the Radios card.
    # (configure_mode's own "COMPOSE_PROFILES= (...)" note, printed earlier
    # while writing the configuration, is unrelated and stays.)
    result = installer()
    assert "otbr-watchdog.sh" not in result.output
    assert "set COMPOSE_PROFILES" not in result.output
    assert "RADIO_DEVICE in" not in result.output
    assert "Settings -> Radios" in result.output


def test_the_report_points_to_findings_without_repeating_them(installer):
    # check_containers already reports the missing service directly in
    # run_checks, right where it's noticed. report() must not print this
    # block a second time afterward - otherwise the same copyable commands
    # appear twice in the run, separated by only a few lines.
    result = installer(env={"FAKE_SERVICES": "loxmatter"})
    assert result.returncode == 0
    assert result.output.count("docker compose logs matter-server") == 1


def test_a_dry_run_report_does_not_invent_an_address(installer):
    # In a dry run, the stack was never started and PORT was never read
    # from the compose file - the report must therefore not claim any
    # web address. git and docker are deliberately PRESENT here (no
    # omit=(...)): the claim "nothing is cloned or started" should
    # hinge on the dry run's behavior, not on the tools being
    # missing on the test machine.
    result = installer("--dry-run")
    assert result.returncode == 0
    assert "Web interface" not in result.output
    # A "Web interface: http://..." line built from an unread PORT would be
    # fabricated - nothing else prints an http:// address during a dry run.
    assert "http://" not in result.output
    assert not result.called("git clone")
    assert not result.called("docker compose up")


def test_with_no_findings_there_is_no_reference_to_findings(installer):
    # A run in WiFi mode has nothing to complain about: no Thread network to
    # check, all services running. Then there must also be no reference at
    # the end to findings that don't exist.
    result = installer()
    assert result.returncode == 0
    assert "Findings" not in result.output
    assert "Some things above still need you" not in result.output


def test_a_wifi_run_writes_the_detected_backbone_for_a_later_thread_switch(installer):
    """Thread is switched on later from the Radios card, which never asks
    for BACKBONE_IF. Left at .env.example's wlan0, an Ethernet-only host
    would get a border router on the wrong interface.

    Fault to prove it: drop the WiFi-mode branch that writes BACKBONE_IF."""
    result = installer()
    assert result.returncode == 0
    assert _env(result)["COMPOSE_PROFILES"] == ""
    assert _env(result)["BACKBONE_IF"] == "eth0"


STICK_A = "usb-ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_1a2b3c-if00-port0"
STICK_B = "usb-SONOFF_SONOFF_Dongle_Plus_MG24_d86e1106-if00-port0"

# Keeps the backbone and Bluetooth questions out of a test about the Thread
# stick. Both are skipped when their environment variable is set.
_ONLY_THE_STICK = {"BACKBONE_IF": "eth0", "BLUETOOTH_ADAPTER": "0"}


def _serial(tmp_path, *names):
    """Fabricates /dev with one ttyUSB node per name and a serial/by-id link
    to each, and returns the env that points the installer at it."""
    dev = tmp_path / "hw" / "dev"
    by_id = dev / "serial" / "by-id"
    by_id.mkdir(parents=True)
    for index, name in enumerate(names):
        node = dev / f"ttyUSB{index}"
        node.write_text("")
        (by_id / name).symlink_to(node)
    return {"SERIAL_BY_ID_DIR": str(by_id), "SERIAL_DEV_DIR": str(dev)}


def _bluetooth(tmp_path, *adapters):
    """Fabricates /sys/class/bluetooth. Each adapter is (index, product):
    product None is a built-in UART adapter, a string a USB adapter of that
    name. The device links resolve below a directory called devices/, as in
    sysfs; the installer only looks at the path after it, so the pytest
    directory above - whose name may contain anything - cannot mislabel one.
    Measured on a Pi 3B: hci0 -> .../3f201000.serial/.../serial0/serial0-0."""
    sys_root = tmp_path / "hw" / "sys"
    klass = sys_root / "class" / "bluetooth"
    klass.mkdir(parents=True)
    soc = sys_root / "devices" / "platform" / "soc"
    for index, product in adapters:
        if product is None:
            device = soc / "3f201000.serial" / "serial0" / f"serial0-{index}"
            device.mkdir(parents=True)
        else:
            usb_device = soc / "3f980000.usb" / "usb1" / f"1-1.{index}"
            device = usb_device / f"1-1.{index}:1.0"
            device.mkdir(parents=True)
            (usb_device / "product").write_text(f"{product}\n")
        entry = klass / f"hci{index}"
        entry.mkdir()
        (entry / "device").symlink_to(device)
    return {"BT_SYS_DIR": str(klass)}


# ------------------------------------------------------------ questions --


def test_answers_are_read_one_after_another(installer, tmp_path):
    # ask() runs inside $(...). Reopening the terminal on every call would
    # read the first line of an answers file again and again; one
    # descriptor opened once is what makes the second answer arrive.
    hw = _bluetooth(tmp_path, (0, None), (1, "TP-Link UB500 Adapter"))
    result = installer(
        env={**hw, "LOXMATTER_MODE": "wifi"},
        answers=["not-a-number", "2"],
    )
    assert result.returncode == 0
    assert _env(result)["BLUETOOTH_ADAPTER"] == "1"


def test_running_out_of_answers_aborts_instead_of_looping(installer, tmp_path):
    # A question that rejects an out-of-range answer used to spin forever on
    # a closed terminal: read failed, ask() returned nothing, choose() took
    # that as another invalid answer, and asked again.
    hw = _bluetooth(tmp_path, (0, None), (1, "TP-Link UB500 Adapter"))
    result = installer(
        env={**hw, "LOXMATTER_MODE": "wifi"},
        answers=["not-a-number"],
    )
    assert result.returncode == 2
    assert "terminal closed" in result.output


def test_two_sticks_are_offered_by_their_by_id_names(installer, tmp_path):
    hw = _serial(tmp_path, STICK_A, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=["2"])
    assert result.returncode == 0
    assert f"1) {STICK_A}" in result.output
    assert f"2) {STICK_B}" in result.output
    assert "A Zigbee stick does not belong here" in result.output
    values = _env(result)
    assert values["COMPOSE_PROFILES"] == "thread"
    assert values["RADIO_DEVICE"] == f"{hw['SERIAL_BY_ID_DIR']}/{STICK_B}"


def test_with_two_sticks_the_default_is_none(installer, tmp_path):
    # Two candidates and no way to tell them apart by name: the default
    # must never be a silent pick of one of them.
    hw = _serial(tmp_path, STICK_A, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=[""])
    assert result.returncode == 0
    assert "Which one is the Thread stick? [0]" in result.output
    assert "Operating mode: wifi" in result.output
    assert _env(result)["COMPOSE_PROFILES"] == ""


def test_with_one_stick_the_default_is_that_stick(installer, tmp_path):
    hw = _serial(tmp_path, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=[""])
    assert result.returncode == 0
    assert "Which one is the Thread stick? [1]" in result.output
    assert _env(result)["RADIO_DEVICE"] == f"{hw['SERIAL_BY_ID_DIR']}/{STICK_B}"


def test_without_by_id_the_tty_nodes_are_offered(installer, tmp_path):
    dev = tmp_path / "hw" / "dev"
    dev.mkdir(parents=True)
    (dev / "ttyUSB0").write_text("")
    env = {
        "SERIAL_BY_ID_DIR": str(dev / "serial" / "by-id"),
        "SERIAL_DEV_DIR": str(dev),
        **_ONLY_THE_STICK,
    }
    result = installer(env=env, answers=["1"])
    assert result.returncode == 0
    assert f"1) {dev}/ttyUSB0" in result.output
    assert _env(result)["RADIO_DEVICE"] == f"{dev}/ttyUSB0"


def test_a_dangling_by_id_link_is_not_offered(installer, tmp_path):
    # A stick pulled after boot can leave its by-id link behind for a moment.
    hw = _serial(tmp_path, STICK_B)
    (Path(hw["SERIAL_BY_ID_DIR"]) / "usb-Gone_Stick-if00-port0").symlink_to(
        tmp_path / "hw" / "dev" / "ttyUSB9"
    )
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=[""])
    assert result.returncode == 0
    assert "usb-Gone_Stick" not in result.output
    assert "Which one is the Thread stick? [1]" in result.output


def test_an_invalid_answer_is_asked_again(installer, tmp_path):
    hw = _serial(tmp_path, STICK_A, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=["7", "1"])
    assert result.returncode == 0
    assert "one of the numbers shown" in result.output
    assert _env(result)["RADIO_DEVICE"] == f"{hw['SERIAL_BY_ID_DIR']}/{STICK_A}"


def test_the_baud_rate_is_not_asked(installer, tmp_path):
    # answers=["1"] and nothing more: a baud rate question would hit the
    # end of the answers and abort the run.
    hw = _serial(tmp_path, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=["1"])
    assert result.returncode == 0
    assert _env(result)["RADIO_BAUDRATE"] == "460800"


def test_the_baud_rate_from_the_environment_wins(installer, tmp_path):
    hw = _serial(tmp_path, STICK_B)
    env = {**hw, **_ONLY_THE_STICK, "RADIO_BAUDRATE": "115200"}
    result = installer(env=env, answers=["1"])
    assert result.returncode == 0
    assert _env(result)["RADIO_BAUDRATE"] == "115200"


def test_without_a_terminal_a_single_stick_is_taken(installer, tmp_path):
    hw = _serial(tmp_path, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK})
    assert result.returncode == 0
    assert "Taking 1" in result.output
    assert _env(result)["RADIO_DEVICE"] == f"{hw['SERIAL_BY_ID_DIR']}/{STICK_B}"


def test_without_a_terminal_two_sticks_mean_wifi(installer, tmp_path):
    hw = _serial(tmp_path, STICK_A, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK})
    assert result.returncode == 0
    assert _env(result)["COMPOSE_PROFILES"] == ""


def test_a_radio_device_from_the_environment_means_thread(installer, tmp_path):
    hw = _serial(tmp_path, STICK_A, STICK_B)
    env = {**hw, **_ONLY_THE_STICK, "RADIO_DEVICE": "/dev/ttyUSB3"}
    result = installer(env=env, answers=[])
    assert result.returncode == 0
    assert "Which one is the Thread stick" not in result.output
    values = _env(result)
    assert values["COMPOSE_PROFILES"] == "thread"
    assert values["RADIO_DEVICE"] == "/dev/ttyUSB3"


def test_thread_mode_from_the_environment_offers_no_none(installer, tmp_path):
    hw = _serial(tmp_path, STICK_A, STICK_B)
    env = {**hw, **_ONLY_THE_STICK, "LOXMATTER_MODE": "thread"}
    result = installer(env=env, answers=["0", "2"])
    assert result.returncode == 0
    assert "None - WiFi and Ethernet only" not in result.output
    assert _env(result)["RADIO_DEVICE"] == f"{hw['SERIAL_BY_ID_DIR']}/{STICK_B}"


def test_thread_mode_without_a_terminal_warns_about_the_guessed_stick(installer, tmp_path):
    # Thread was requested, so "None" is no option, and aborting would be a
    # new stop on the non-interactive path. Stick 1 is taken - by-id names
    # sort alphabetically, and on the maintainer's test Pi that is the
    # Zigbee stick. The guess has to be said out loud, with the way out.
    hw = _serial(tmp_path, STICK_A, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK, "LOXMATTER_MODE": "thread"})
    assert result.returncode == 0
    assert _env(result)["RADIO_DEVICE"] == f"{hw['SERIAL_BY_ID_DIR']}/{STICK_A}"
    assert f"{STICK_A} was taken as the Thread stick without asking" in result.output
    assert "RADIO_DEVICE=" in result.output


def test_a_chosen_stick_is_not_warned_about(installer, tmp_path):
    hw = _serial(tmp_path, STICK_A, STICK_B)
    env = {**hw, **_ONLY_THE_STICK, "LOXMATTER_MODE": "thread"}
    result = installer(env=env, answers=["1"])
    assert result.returncode == 0
    assert "without asking" not in result.output


def test_a_single_stick_in_thread_mode_is_not_warned_about(installer, tmp_path):
    # One candidate is no guess between two.
    hw = _serial(tmp_path, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK, "LOXMATTER_MODE": "thread"})
    assert result.returncode == 0
    assert "without asking" not in result.output


def test_a_second_run_does_not_show_the_thread_menu(installer, tmp_path):
    # configure_mode lets the existing .env win anyway; asking first and
    # overruling the answer with a warning asked a question for nothing.
    hw = {**_serial(tmp_path, STICK_A, STICK_B), **_ONLY_THE_STICK}
    first = installer(env=hw, answers=["2"])
    assert first.returncode == 0
    second = installer(env=hw, answers=[])
    assert second.returncode == 0
    assert "Which one is the Thread stick" not in second.output
    assert "kept from" in second.output
    assert "wins over the requested" not in second.output
    assert _env(second)["RADIO_DEVICE"] == _env(first)["RADIO_DEVICE"]


def test_a_closed_terminal_at_the_thread_menu_aborts_cleanly(installer, tmp_path):
    # The Thread stick menu is the first question ask_questions asks. An
    # empty terminal there has to abort through die() with exit code 2,
    # not with a bare `set -e` exit.
    hw = _serial(tmp_path, STICK_A, STICK_B)
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=[])
    assert result.returncode == 2
    assert "terminal closed" in result.output


def test_a_detected_backbone_is_not_asked(installer, tmp_path):
    # answers=["1"] only: a backbone question would hit the end of the
    # answers and abort.
    hw = _serial(tmp_path, STICK_B)
    result = installer(env={**hw, "BLUETOOTH_ADAPTER": "0"}, answers=["1"])
    assert result.returncode == 0
    assert "Border router network interface: eth0" in result.output
    assert _env(result)["BACKBONE_IF"] == "eth0"


def test_an_undetectable_backbone_is_asked_until_answered(installer, tmp_path):
    hw = _serial(tmp_path, STICK_B)
    result = installer(
        env={**hw, "BLUETOOTH_ADAPTER": "0"},
        stubs={"ip": "echo\n"},
        answers=["1", "", "eth1"],
    )
    assert result.returncode == 0
    assert _env(result)["BACKBONE_IF"] == "eth1"


def test_a_single_adapter_is_used_without_a_question(installer, tmp_path):
    result = installer(env=_bluetooth(tmp_path, (0, None)), answers=[])
    assert result.returncode == 0
    assert "Bluetooth: hci0 - built in (UART)" in result.output
    assert _env(result)["BLUETOOTH_ADAPTER"] == "0"


def test_two_adapters_are_offered_by_name(installer, tmp_path):
    hw = _bluetooth(tmp_path, (0, None), (1, "TP-Link UB500 Adapter"))
    result = installer(env=hw, answers=["2"])
    assert result.returncode == 0
    assert "1) hci0 - built in (UART)" in result.output
    assert "2) hci1 - USB: TP-Link UB500 Adapter" in result.output
    assert _env(result)["BLUETOOTH_ADAPTER"] == "1"


def test_the_adapter_index_is_written_not_the_menu_position(installer, tmp_path):
    hw = _bluetooth(tmp_path, (0, None), (3, "TP-Link UB500 Adapter"))
    result = installer(env=hw, answers=["2"])
    assert result.returncode == 0
    assert _env(result)["BLUETOOTH_ADAPTER"] == "3"


def test_no_adapter_is_a_warning_not_a_question(installer):
    result = installer(answers=[])
    assert result.returncode == 0
    assert "No Bluetooth adapter found" in result.output
    assert _env(result)["BLUETOOTH_ADAPTER"] == "0"


def test_without_a_terminal_the_first_adapter_is_taken(installer, tmp_path):
    hw = _bluetooth(tmp_path, (0, None), (1, "TP-Link UB500 Adapter"))
    result = installer(env=hw)
    assert result.returncode == 0
    assert _env(result)["BLUETOOTH_ADAPTER"] == "0"


def test_the_adapter_from_the_environment_skips_the_menu(installer, tmp_path):
    hw = _bluetooth(tmp_path, (0, None), (1, "TP-Link UB500 Adapter"))
    result = installer(env={**hw, "BLUETOOTH_ADAPTER": "5"}, answers=[])
    assert result.returncode == 0
    assert "Which adapter" not in result.output
    assert _env(result)["BLUETOOTH_ADAPTER"] == "5"


def test_the_questions_are_announced(installer, tmp_path):
    env = {
        **_serial(tmp_path, STICK_A, STICK_B),
        **_bluetooth(tmp_path, (0, None), (1, "TP-Link UB500 Adapter")),
    }
    result = installer(env=env, answers=["0", "1"])
    assert result.returncode == 0
    assert "Two questions follow: the Thread stick and the Bluetooth adapter." in result.output


def test_a_single_question_is_announced_as_one(installer, tmp_path):
    hw = _serial(tmp_path, STICK_A)
    result = installer(env={**hw, **_ONLY_THE_STICK}, answers=["1"])
    assert result.returncode == 0
    assert "One question follows: the Thread stick." in result.output


def test_without_a_terminal_nothing_is_announced(installer):
    result = installer()
    assert result.returncode == 0
    assert "question follows" not in result.output
    assert "questions follow" not in result.output


def test_every_question_comes_before_anything_is_installed(installer, tmp_path):
    # The Bluetooth menu is now the last question. Its heading, printed by
    # say("Bluetooth adapter") (install.sh ~956) as "\n\033[1mBluetooth
    # adapter\033[0m\n", is distinct from the plain "the Bluetooth adapter"
    # inside the "Two questions follow: ..." announcement above it - the
    # bold-on escape immediately before "Bluetooth" is what makes it so.
    # Result.output is proc.stdout + proc.stderr; every marker checked here
    # is a say() on stdout, and stdout keeps its own order among itself.
    # stderr (prompts via tty_prompt when LOXMATTER_TTY is set, and die())
    # is only appended after it in result.output, not interleaved, so
    # stdout's order still reflects execution order for the comparisons
    # below. This heading has to come before the first package and before
    # Docker - otherwise the user is called back to the keyboard minutes
    # into the installation.
    env = {
        **_serial(tmp_path, STICK_A, STICK_B),
        **_bluetooth(tmp_path, (0, None), (1, "TP-Link UB500 Adapter")),
        "MINISERVER_IP": "",
    }
    result = installer(env=env, omit=("git", "docker"), answers=["2", "2"])
    assert result.returncode == 0
    bluetooth_heading = result.output.index("\033[1mBluetooth adapter\033[0m")
    packages = result.output.index("Installing missing tools")
    docker = result.output.index("Docker is not installed")
    assert bluetooth_heading < packages
    assert bluetooth_heading < docker
