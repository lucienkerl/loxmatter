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

    def build_env(env, omit, stubs):
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
            # Without a terminal, the address must come from the environment.
            # Tests that check exactly this abort set it to "".
            "MINISERVER_IP": "10.0.1.99",
            # /sys/class/rfkill doesn't exist on macOS and is nowhere
            # writable. This path doesn't exist by default -
            # check_rfkill then finds nothing, just like on a host without
            # rfkill. Tests for check_rfkill point this at a
            # prepared directory instead.
            "RFKILL_DIR": str(tmp_path / "no-rfkill-here"),
        }
        full_env.update(env or {})
        return full_env

    def run(*args, env=None, omit=(), stubs=None):
        full_env = build_env(env, omit, stubs)
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
        full_env = build_env(env, omit, stubs)
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
    assert "no radio" in result.output


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


def test_without_a_miniserver_ip_and_without_a_terminal_it_aborts(installer):
    result = installer(env={"MINISERVER_IP": ""})
    assert result.returncode == 2
    assert "MINISERVER_IP" in result.output
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


def test_a_second_run_offers_the_update_without_doing_it(installer):
    # update.sh backs up the signal database beforehand. An install script
    # that updates on the side would bypass this backup - so it is only
    # offered.
    first = installer()
    assert first.returncode == 0
    second = installer(env={"FAKE_BEHIND": "3"})
    assert second.returncode == 0
    assert "3 new commits" in second.output
    assert "Apply them with" in second.output


def test_update_offer_comes_before_restart(installer):
    # main previously started first - with the OLD checkout - and offered the
    # update only after that. A rerun is the documented
    # update path, thus the common case: the old
    # version must not be restarted before the update is offered.
    first = installer()
    assert first.returncode == 0
    second = installer(env={"FAKE_BEHIND": "3"})
    assert second.returncode == 0
    fetch_index = next(i for i, c in enumerate(second.calls) if "fetch --quiet origin main" in c)
    up_index = next(i for i, c in enumerate(second.calls) if "compose up -d" in c)
    assert fetch_index < up_index


def test_the_first_run_does_not_check_for_updates(installer):
    # Freshly cloned - there's nothing to update.
    result = installer(env={"FAKE_BEHIND": "3"})
    assert result.returncode == 0
    assert "new commits" not in result.output


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
    proc = installer.start(omit=("docker",), stubs={"curl": "sleep 30\n"})
    deadline = time.time() + 10
    while True:
        if installer.log.exists() and "curl" in installer.log.read_text():
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


def test_no_update_offer_when_docker_was_just_installed(installer):
    # update.sh calls docker without sudo. If Docker was just installed
    # in this run, group membership only takes effect after logging back
    # in - the offer couldn't work at all.
    first = installer()
    assert first.returncode == 0
    second = installer(omit=("docker",), env={"FAKE_BEHIND": "3"})
    assert second.returncode == 0
    assert "Log out and back in first" in second.output
    assert "Apply them with" not in second.output


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


def test_a_nonsensical_commit_count_reports_no_update(installer):
    # rev-list normally returns a number. If it returns something else,
    # that must not turn into an update offer with a garbage value.
    first = installer()
    assert first.returncode == 0
    second = installer(env={"FAKE_BEHIND": "not-a-number"})
    assert second.returncode == 0
    assert "new commits" not in second.output


def test_not_a_git_repository_gets_its_own_message_instead_of_blaming_the_network(installer):
    # ensure_checkout only checks for Dockerfile and docker-compose.yml - a
    # checkout unpacked from a tarball passes that but has no .git.
    # `git fetch` fails on that just as it would on a network problem, and
    # "Could not reach GitHub" would be the wrong explanation for it.
    not_a_repo_git = _GIT.replace(
        'case "${1-}" in\n  clone)',
        'case "${1-}" in\n  rev-parse) exit 1 ;;\n  clone)',
    )
    first = installer()
    assert first.returncode == 0
    second = installer(stubs={"git": not_a_repo_git}, env={"FAKE_BEHIND": "3"})
    assert second.returncode == 0
    assert "is not a git repository" in second.output
    assert "Could not reach GitHub" not in second.output
    assert "new commits" not in second.output


# -------------------------------------------------------------- phase four --


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


def test_an_abort_in_configure_names_the_touched_env(installer):
    # COMPOSE_PROFILES, RADIO_DEVICE, and RADIO_BAUDRATE already sit in the
    # .env when BACKBONE_IF gets no value (no default-route entry,
    # no terminal) and the run aborts. The abort must say so, otherwise
    # it looks like a clean abort before any change.
    result = installer(
        env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"},
        stubs={"ip": "echo\n"},
    )
    assert result.returncode == 2
    assert "BACKBONE_IF" in result.output
    assert str(result.env_file) in result.output
    assert "partially written" in result.output
    values = dict(
        line.split("=", 1)
        for line in result.env_file.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    assert values["COMPOSE_PROFILES"] == "thread"


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


def test_a_thread_run_without_wpan_reports_the_workaround(installer):
    result = installer(env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"})
    assert result.returncode == 0
    assert "start-stop-daemon" in result.output
    assert "otbr-agent" in result.output


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


def test_the_thread_report_suggests_the_watchdog(installer):
    result = installer(env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"})
    assert "otbr-watchdog.sh" in result.output
    assert str(result.home / "loxmatter") in result.output


def test_the_wifi_report_suggests_no_watchdog(installer):
    result = installer()
    assert "otbr-watchdog.sh" not in result.output
    assert "COMPOSE_PROFILES=thread" in result.output  # that's how you upgrade later


def test_the_watchdog_log_sits_next_to_the_checkout_not_in_home(installer, tmp_path):
    # The cron line used to always write the log to $HOME, even when
    # --dir puts the checkout somewhere else entirely - then its own
    # line no longer matched itself.
    checkout = tmp_path / "elsewhere" / "loxmatter"
    result = installer(
        "--dir",
        str(checkout),
        env={"LOXMATTER_MODE": "thread", "RADIO_DEVICE": "/dev/ttyUSB0"},
    )
    assert result.returncode == 0
    assert f"{checkout.parent}/otbr-watchdog.log" in result.output
    assert f"{result.home}/otbr-watchdog.log" not in result.output


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
