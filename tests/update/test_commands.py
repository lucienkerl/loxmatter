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

"""What the command layer actually asks of docker, git and the filesystem."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import loxmatter.update.commands as commands_module
from loxmatter.update.commands import DockerCommands


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, argv: list[str], **_kwargs: object) -> str:
        self.commands.append(argv)
        return ""


class RaisingRunner:
    """Every call fails, the way a real `curl`/`git` does when the bridge
    or the network is unreachable - see `test_healthy_...` below."""

    def __call__(self, argv: list[str], **_kwargs: object) -> str:
        raise RuntimeError(f"{argv[0]} is unreachable")


class CwdRecordingRunner:
    """Like `RecordingRunner`, but keeps the one thing that one throws
    away: `cwd`. Every other test in this file passes the same `tmp_path`
    for `repo_dir` and `stack_dir`, which cannot tell a caller who swapped
    the two constructor arguments from one who got them right - this
    runner is what makes that distinguishable (see
    `test_git_commands_run_in_repo_dir_and_compose_commands_in_stack_dir`
    below).

    `docker compose ps -q loxmatter` and `docker inspect` get canned
    answers rather than the blank `RecordingRunner` returns: a blank `ps`
    reads as "the container is not running" to `read_running_identity`,
    which raises before that call's `cwd` is ever recorded - the one
    thing this runner exists to check."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path | None]] = []

    def __call__(self, argv: list[str], *, cwd: Path | None = None, **_kwargs: object) -> str:
        self.calls.append((argv, cwd))
        if argv[:5] == ["docker", "compose", "ps", "-q", "loxmatter"]:
            return "container123"
        if argv[:2] == ["docker", "inspect"]:
            return json.dumps(["LOXMATTER_VERSION=0.2.0", "LOXMATTER_COMMIT=abc1234"])
        return ""


class TimingOutRunner:
    """Every call raises `subprocess.TimeoutExpired` - the way the real
    `_run_subprocess` behaves once a command outlives the `timeout` it was
    given, without this test ever having to let a real subprocess block
    for that long."""

    def __call__(self, argv: list[str], **_kwargs: object) -> str:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1.0)


class TimeoutRecordingRunner:
    """Like `CwdRecordingRunner`, but keeps `timeout` instead of `cwd` -
    proves timeouts are chosen per command (`fetch`/`pull` get a
    network-scale budget, `checkout`/`rev-parse`/`is_ancestor`/`docker
    inspect`/`docker compose ps` get a local-scale one, `recreate` and
    `backup` each get their own) rather than one number shared by every
    subprocess call in this module."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], float | None]] = []

    def __call__(self, argv: list[str], *, timeout: float | None = None, **_kwargs: object) -> str:
        self.calls.append((argv, timeout))
        if argv[:5] == ["docker", "compose", "ps", "-q", "loxmatter"]:
            return "container123"
        if argv[:2] == ["docker", "inspect"]:
            return json.dumps(["LOXMATTER_VERSION=0.2.0", "LOXMATTER_COMMIT=abc1234"])
        return ""


def make(tmp_path: Path) -> tuple[DockerCommands, RecordingRunner]:
    env = tmp_path / ".env"
    env.write_text("LOXMATTER_IMAGE_TAG=stable\nMINISERVER_IP=10.0.0.1\n", encoding="utf-8")
    runner = RecordingRunner()
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=env,
        health_url="http://host.docker.internal:8080/health",
        base=tmp_path / "update",
        run=runner,
    )
    return commands, runner


def test_recreate_never_touches_the_other_services(tmp_path):
    """`--no-deps` for two reasons now: the updater must not recreate
    itself mid-request, and matter-server must not be recreated at all -
    since 8 September that service points at matterjs-server, whose first
    start migrates the Fabric directory one way (addendum 16.2)."""
    commands, runner = make(tmp_path)
    commands.recreate()
    assert runner.commands == [
        ["docker", "compose", "up", "-d", "--no-deps", "loxmatter"],
    ]


def test_writing_the_tag_replaces_the_line_in_place(tmp_path):
    """In place, not appended: a second definition of the same variable
    would work (Compose takes the last), but whoever edited the file later
    would change the wrong line. `.env.example` says so already."""
    commands, _ = make(tmp_path)
    commands.write_image_tag("v0.3.0")
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LOXMATTER_IMAGE_TAG=v0.3.0" in text
    assert text.count("LOXMATTER_IMAGE_TAG") == 1
    # Everything else survives untouched - this file holds the Miniserver
    # address and the API token.
    assert "MINISERVER_IP=10.0.0.1" in text


def test_writing_the_tag_appends_it_when_the_key_is_absent(tmp_path):
    """`.env.example` ships without a trailing blank line and, before the
    first update, without `LOXMATTER_IMAGE_TAG` ever having been written
    at all - Compose's own `:-stable` default covers that case. Deleting
    this append branch and always falling through to "replace" would
    leave nothing written and `pull` fetching a version Compose never
    deploys, silently: the operator sees "done"."""
    env = tmp_path / ".env"
    env.write_text("MINISERVER_IP=10.0.0.1\n", encoding="utf-8")
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=env,
        health_url="http://x/health",
        base=tmp_path / "update",
        run=RecordingRunner(),
    )
    commands.write_image_tag("v0.3.0")
    text = env.read_text(encoding="utf-8")
    assert text == "MINISERVER_IP=10.0.0.1\nLOXMATTER_IMAGE_TAG=v0.3.0\n"


def test_writing_the_tag_adds_a_missing_trailing_newline_before_appending(tmp_path):
    """A `.env` saved without a final newline (common - many editors only
    add one on the next save) must not be corrupted into
    `MINISERVER_IP=10.0.0.1LOXMATTER_IMAGE_TAG=v0.3.0` on the same line."""
    env = tmp_path / ".env"
    env.write_text("MINISERVER_IP=10.0.0.1", encoding="utf-8")
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=env,
        health_url="http://x/health",
        base=tmp_path / "update",
        run=RecordingRunner(),
    )
    commands.write_image_tag("v0.3.0")
    assert env.read_text(encoding="utf-8") == "MINISERVER_IP=10.0.0.1\nLOXMATTER_IMAGE_TAG=v0.3.0\n"


def test_writing_the_tag_creates_a_missing_parent_directory(tmp_path):
    """`write_image_tag` already handles the file not existing (falls back
    to an empty starting text) - but without creating the parent
    directory first, the final `write_text` still raises when that
    directory is also missing, defeating the case the method exists to
    cover."""
    env = tmp_path / "nested" / "deeper" / ".env"
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=env,
        health_url="http://x/health",
        base=tmp_path / "update",
        run=RecordingRunner(),
    )
    commands.write_image_tag("v0.3.0")
    assert env.read_text(encoding="utf-8") == "LOXMATTER_IMAGE_TAG=v0.3.0\n"


def test_reading_the_tag_comes_back(tmp_path):
    commands, _ = make(tmp_path)
    commands.write_image_tag("v0.3.0")
    assert commands.read_image_tag() == "v0.3.0"


def test_a_missing_tag_line_reads_as_stable(tmp_path):
    """Compose's own default is `:-stable`; the reader must agree with it,
    or a rollback would write back a tag that was never running."""
    env = tmp_path / ".env"
    env.write_text("MINISERVER_IP=10.0.0.1\n", encoding="utf-8")
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=env,
        health_url="http://x/health",
        base=tmp_path / "update",
        run=RecordingRunner(),
    )
    assert commands.read_image_tag() == "stable"


def test_reading_the_tag_strips_surrounding_quotes(tmp_path):
    """`LOXMATTER_IMAGE_TAG="0.3.0"` is valid shell and a style some
    editors add on their own - the quotes are not part of the tag."""
    env = tmp_path / ".env"
    env.write_text('LOXMATTER_IMAGE_TAG="0.3.0"\n', encoding="utf-8")
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=env,
        health_url="http://x/health",
        base=tmp_path / "update",
        run=RecordingRunner(),
    )
    assert commands.read_image_tag() == "0.3.0"


def test_reading_the_tag_from_a_missing_file_reads_as_stable(tmp_path):
    """No `.env` at all (a fresh checkout, before the first `docker
    compose up`) is not an error - Compose's own default applies."""
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / "does-not-exist" / ".env",
        health_url="http://x/health",
        base=tmp_path / "update",
        run=RecordingRunner(),
    )
    assert commands.read_image_tag() == "stable"


def test_pulling_uses_the_full_image_reference_not_compose_pull(tmp_path):
    """The one method this task deliberately diverged from the plan on
    (`docker pull <image>`, not `docker compose pull loxmatter` - see the
    method's own docstring for why) had no test at all: replacing the
    body with `pass` left every other test green."""
    commands, runner = make(tmp_path)
    commands.pull("ghcr.io/lucienkerl/loxmatter:0.3.0")
    assert runner.commands == [["docker", "pull", "ghcr.io/lucienkerl/loxmatter:0.3.0"]]


def test_each_git_step_issues_its_own_command(tmp_path):
    """Named for what this actually checks: `fetch()` and `checkout()`
    each issue the one command they own. Nothing in `commands.py`
    sequences the two - `runner.run_update` does, and that sequencing is
    `test_runner.py`'s job, not this module's. The previous name
    ("fetches tags first") claimed a guarantee this test cannot make: it
    calls both methods itself, in the order the test wrote them, and
    would pass just as well if `checkout` ran before `fetch` inside
    `commands.py` for some other caller."""
    commands, runner = make(tmp_path)
    commands.fetch()
    commands.checkout("v0.3.0")
    assert runner.commands == [
        ["git", "fetch", "--tags", "--prune", "origin"],
        ["git", "checkout", "--detach", "v0.3.0"],
    ]


def test_git_commands_run_in_repo_dir_and_compose_commands_in_stack_dir(tmp_path):
    """The point of the `compose_dir` split (see the module docstring):
    `repo_dir` and `stack_dir` are two different directories in the real
    deployment - `repo_dir` the whole checkout, `stack_dir` `deploy/
    testhost` two levels below it - and every git call must run in the
    first, every `docker compose` call in the second. Every other test in
    this file passes the same `tmp_path` for both, which cannot catch a
    caller that swapped the two constructor arguments; this one uses two
    genuinely different directories and would fail if `fetch`/`checkout`/
    `is_ancestor`/`read_git_ref` ran in `stack_dir`, `recreate` ran in
    `repo_dir`, or `read_running_identity`'s `docker compose ps` ran in
    `repo_dir` - the last of which no test caught before this one: a
    `docker compose ps -q loxmatter` run from the repository root finds no
    compose file, the call raises, and no update would ever run, silently,
    on a change that every other test in this file still passed."""
    repo_dir = tmp_path / "repo"
    stack_dir = tmp_path / "repo" / "deploy" / "testhost"
    repo_dir.mkdir()
    stack_dir.mkdir(parents=True)
    runner = CwdRecordingRunner()
    commands = DockerCommands(
        repo_dir=repo_dir,
        stack_dir=stack_dir,
        env_path=stack_dir / ".env",
        health_url="http://x/health",
        base=tmp_path / "update",
        run=runner,
    )

    commands.fetch()
    commands.checkout("v0.3.0")
    commands.is_ancestor("abc1234", "def5678")
    commands.read_git_ref()
    commands.recreate()
    commands.read_running_identity()

    assert stack_dir != repo_dir
    git_calls = runner.calls[:4]
    compose_call = runner.calls[4]
    ps_call = runner.calls[5]
    assert [argv[0] for argv, _cwd in git_calls] == ["git", "git", "git", "git"]
    assert all(cwd == repo_dir for _argv, cwd in git_calls)
    assert compose_call[0][:2] == ["docker", "compose"]
    assert compose_call[1] == stack_dir
    assert ps_call[0][:3] == ["docker", "compose", "ps"]
    assert ps_call[1] == stack_dir


def test_the_env_edit_leaves_comments_blank_lines_and_embedded_equals_alone(tmp_path):
    """A real `.env` also carries the Miniserver address, an API token and
    commentary - none of them can be collateral damage of an image-tag
    rewrite. `LOXMATTER_API_TOKEN` is exactly the kind of value that
    contains an `=` of its own (base64-shaped), so a naive `split("=")`
    parse would corrupt it; this test would catch a rewrite that assumed a
    single `=` per line or that rebuilt the file from scratch."""
    env = tmp_path / ".env"
    env.write_text(
        "# Which image docker-compose.yml pulls.\n"
        "LOXMATTER_IMAGE_TAG=stable\n"
        "\n"
        "# A token that happens to contain an equals sign of its own.\n"
        "LOXMATTER_API_TOKEN=YWJjZGVm==\n"
        "\n"
        "MINISERVER_IP=10.0.0.1\n",
        encoding="utf-8",
    )
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=env,
        health_url="http://x/health",
        base=tmp_path / "update",
        run=RecordingRunner(),
    )
    commands.write_image_tag("v0.4.0")
    text = env.read_text(encoding="utf-8")
    assert text == (
        "# Which image docker-compose.yml pulls.\n"
        "LOXMATTER_IMAGE_TAG=v0.4.0\n"
        "\n"
        "# A token that happens to contain an equals sign of its own.\n"
        "LOXMATTER_API_TOKEN=YWJjZGVm==\n"
        "\n"
        "MINISERVER_IP=10.0.0.1\n"
    )


def test_healthy_is_false_on_a_connection_failure_not_a_raised_exception(tmp_path):
    """`healthy()` is polled while the bridge is restarting, so it is
    called exactly when the connection fails. `run_update` already treats
    a raising poll as "not healthy" as a safety net, but the meaning
    belongs here: a reader looking for "what does an unreachable bridge
    report" should find the answer in `healthy()`, not have to know about
    a fallback three modules away."""
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://host.docker.internal:8080/health",
        base=tmp_path / "update",
        run=RaisingRunner(),
    )
    assert commands.healthy() is False


def test_read_git_ref_returns_the_stripped_commit(tmp_path):
    """`read_git_ref` is part of `Commands` (see `runner.py`) even though
    the brief's own command table never lists it - `run_update` reads it
    before the first command to know what to check out back to on a
    rollback."""

    class FixedRunner:
        def __call__(self, argv: list[str], **_kwargs: object) -> str:
            assert argv == ["git", "rev-parse", "HEAD"]
            return "abc1234\n"

    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://x/health",
        base=tmp_path / "update",
        run=FixedRunner(),
    )
    assert commands.read_git_ref() == "abc1234"


def test_is_ancestor_true_on_success_false_on_failure(tmp_path):
    """Not part of `Commands` - `run_update` takes `is_ancestor` as its own
    injected callable - but the sidecar's loop needs a real implementation
    of it to hand over, and `git merge-base --is-ancestor` is exactly the
    command the design names for it (section 10)."""
    commands, runner = make(tmp_path)
    assert commands.is_ancestor("abc1234", "def5678") is True
    assert runner.commands == [["git", "merge-base", "--is-ancestor", "abc1234", "def5678"]]

    failing = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://x/health",
        base=tmp_path / "update",
        run=RaisingRunner(),
    )
    assert failing.is_ancestor("abc1234", "def5678") is False


def test_read_running_identity_reads_version_and_commit_from_the_container_env(tmp_path):
    """The mandated source (design section 4): the running container's own
    `Config.Env`, not `pyproject.toml`/`git rev-parse HEAD` on the
    checkout - see `Dockerfile`'s `LOXMATTER_VERSION`/`LOXMATTER_COMMIT`
    `ENV` and the method's own docstring for why."""

    class FixedRunner:
        def __call__(self, argv: list[str], **_kwargs: object) -> str:
            if argv == ["docker", "compose", "ps", "-q", "loxmatter"]:
                return "abc123def456\n"
            assert argv == [
                "docker",
                "inspect",
                "--format",
                "{{json .Config.Env}}",
                "abc123def456",
            ]
            return json.dumps(
                ["PATH=/usr/bin", "LOXMATTER_VERSION=0.3.0", "LOXMATTER_COMMIT=abc1234"]
            )

    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://x/health",
        base=tmp_path / "update",
        run=FixedRunner(),
    )
    assert commands.read_running_identity() == ("0.3.0", "abc1234")


def test_read_running_identity_raises_when_the_container_is_not_running(tmp_path):
    """`docker compose ps -q loxmatter` prints nothing (exit 0) when the
    service is not up - that must not be read as an empty-but-valid
    container id. The loop's own guard (`__main__.run_once`) treats this
    raise as "cannot determine what is currently running", never as a
    permissive default."""

    class EmptyRunner:
        def __call__(self, argv: list[str], **_kwargs: object) -> str:
            assert argv == ["docker", "compose", "ps", "-q", "loxmatter"]
            return ""

    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://x/health",
        base=tmp_path / "update",
        run=EmptyRunner(),
    )
    with pytest.raises(RuntimeError, match="not running"):
        commands.read_running_identity()


def test_read_running_identity_falls_back_to_dev_when_the_env_keys_are_absent(tmp_path):
    """A foreign or very old image - one whose `Config.Env` never carried
    `LOXMATTER_VERSION`/`LOXMATTER_COMMIT` at all - is the case the
    method's own docstring names for this fallback, and the only one that
    actually reaches it: `env.get("LOXMATTER_VERSION") or "dev"` only
    falls back when the key is missing from the dict entirely. A
    hand-built image using the current `Dockerfile` is not that case - its
    `ARG LOXMATTER_VERSION=dev` default means the key IS present, already
    holding the string `"dev"`, so supplying it explicitly (as this test
    used to) exercises plain pass-through, not the fallback."""

    class FixedRunner:
        def __call__(self, argv: list[str], **_kwargs: object) -> str:
            if argv == ["docker", "compose", "ps", "-q", "loxmatter"]:
                return "abc123\n"
            return json.dumps(["PATH=/usr/bin"])

    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://x/health",
        base=tmp_path / "update",
        run=FixedRunner(),
    )
    assert commands.read_running_identity() == ("dev", "")


def test_read_running_identity_propagates_a_failing_docker_inspect(tmp_path):
    """A stale container id (removed between the two calls) or a broken
    Docker socket must reach the caller as an exception, not as a
    fabricated identity - the loop's guard is what turns this into
    "cannot determine what is currently running"."""

    class FailingInspect:
        def __call__(self, argv: list[str], **_kwargs: object) -> str:
            if argv == ["docker", "compose", "ps", "-q", "loxmatter"]:
                return "abc123\n"
            raise RuntimeError("docker inspect: no such object")

    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://x/health",
        base=tmp_path / "update",
        run=FailingInspect(),
    )
    with pytest.raises(RuntimeError, match="no such object"):
        commands.read_running_identity()


def test_backup_writes_a_timestamped_archive_and_prunes_old_ones(tmp_path, monkeypatch):
    """No committed test exercised `backup()` at all before this - the
    reviewer could delete its body and every other test would stay green.
    Pruning matches `scripts/update.sh`'s own rule: keep the most recent
    (here 2, to keep the fixture small - `_BACKUPS_TO_KEEP` is 10 in
    production), delete the rest, because the volume being backed up is
    the same one the backups are written into.

    `_DATA_DIR`/`_BACKUP_DIR` are module constants, not constructor
    arguments (see the module docstring for why) - `monkeypatch` is the
    only way to point them at `tmp_path` for a test."""
    data_dir = tmp_path / "data"
    backup_dir = data_dir / "backups"
    monkeypatch.setattr(commands_module, "_DATA_DIR", data_dir)
    monkeypatch.setattr(commands_module, "_BACKUP_DIR", backup_dir)
    monkeypatch.setattr(commands_module, "_BACKUPS_TO_KEEP", 2)

    # Three pre-existing backups, given distinct mtimes (the recording
    # runner never actually writes the archive `backup()` names, so
    # pruning has to be exercised against files already on disk) so "the
    # two most recent" has an unambiguous answer.
    backup_dir.mkdir(parents=True)
    now = os.stat(tmp_path).st_mtime
    old_files = [backup_dir / f"store-old-{index}.tgz" for index in range(3)]
    for index, stale in enumerate(old_files):
        stale.write_bytes(b"x")
        stamp = now - (100 - index)  # index 2 is the most recent of the three
        os.utime(stale, (stamp, stamp))

    commands, runner = make(tmp_path)
    path = commands.backup()

    assert path.startswith(str(backup_dir))
    assert runner.commands == [
        ["tar", "czf", path, "-C", str(data_dir), "loxmatter.sqlite"],
    ]
    # The two most recent of the three pre-existing backups survive;
    # `store-old-0.tgz`, the oldest, is pruned away.
    remaining = sorted(p.name for p in backup_dir.glob("store-*.tgz"))
    assert remaining == ["store-old-1.tgz", "store-old-2.tgz"]


def test_log_appends_a_timestamped_line_under_the_injected_base(tmp_path):
    """`log()` used to hard-code `protocol.UPDATE_DIR`, ignoring the
    `base` the rest of the sidecar honours - `LOXMATTER_UPDATE_BASE` could
    move `state.json` without moving `log.txt` to match. Injecting `base`
    through the constructor (like `env_path`/`health_url`) is also what
    makes this method testable at all; it had no coverage before."""
    base = tmp_path / "somewhere-else"
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://x/health",
        base=base,
        run=RecordingRunner(),
    )
    commands.log("hello")
    text = (base / "log.txt").read_text(encoding="utf-8")
    assert text.endswith(" hello\n")
    assert len(text.splitlines()) == 1


def test_cap_log_keeps_only_complete_lines_within_the_cap(tmp_path):
    """`_cap_log`'s seek-and-truncate is the most intricate logic in the
    file and had no test at all. The seek almost certainly lands inside a
    line; the trim must drop exactly that partial fragment and nothing
    else - every surviving line has to be one that was written whole, and
    the newest line (last written) must always be among them."""
    base = tmp_path / "update"
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://x/health",
        base=base,
        run=RecordingRunner(),
    )
    path = base / "log.txt"
    path.parent.mkdir(parents=True)
    line = b"0123456789\n"
    repeats = (commands_module._LOG_CAP_BYTES // len(line)) + 10
    path.write_bytes(line * repeats + b"LAST-LINE\n")

    commands._cap_log(path)

    capped = path.read_bytes()
    assert len(capped) <= commands_module._LOG_CAP_BYTES
    lines = capped.splitlines()
    assert lines[-1] == b"LAST-LINE"
    assert all(entry in (b"0123456789", b"LAST-LINE") for entry in lines)


def test_cap_log_trims_to_the_trim_target_not_just_under_the_cap(tmp_path):
    """The test above only asserts the result stays `<= _LOG_CAP_BYTES`
    - a `_cap_log` that seeked back `_LOG_CAP_BYTES` instead of
    `_LOG_TRIM_BYTES` (no headroom at all, the very next `log()` call
    would trip the cap again) would satisfy that just as well. This pins
    the actual seek target by recomputing, from the same known input and
    the module's own `_LOG_TRIM_BYTES`, exactly which bytes should
    survive."""
    base = tmp_path / "update"
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://x/health",
        base=base,
        run=RecordingRunner(),
    )
    path = base / "log.txt"
    path.parent.mkdir(parents=True)
    line = b"0123456789\n"
    repeats = (commands_module._LOG_CAP_BYTES // len(line)) + 10
    content = line * repeats + b"LAST-LINE\n"
    path.write_bytes(content)

    commands._cap_log(path)

    capped = path.read_bytes()
    seek_pos = len(content) - commands_module._LOG_TRIM_BYTES
    tail = content[seek_pos:]
    newline = tail.find(b"\n")
    expected = tail[newline + 1 :] if newline != -1 else tail
    assert capped == expected
    # Comfortably below the cap, not merely at or under it - the
    # headroom `_LOG_TRIM_BYTES` exists to buy.
    assert len(capped) < commands_module._LOG_CAP_BYTES


def test_cap_log_does_nothing_under_the_cap(tmp_path):
    base = tmp_path / "update"
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://x/health",
        base=base,
        run=RecordingRunner(),
    )
    commands.log("short")
    commands.log("also short")
    text = (base / "log.txt").read_text(encoding="utf-8")
    assert len(text.splitlines()) == 2


def test_run_subprocess_raises_when_the_command_exceeds_its_timeout():
    """The real implementation, not an injected fake: `_run_subprocess`
    used to call `subprocess.run` with no `timeout` at all, so a stalled
    command (a `docker pull` on a half-open connection) blocked forever -
    no rollback, no further log line, nothing but a wedged process until
    a human noticed. `sleep 5` outlives a `timeout=0.05` easily;
    `subprocess.run` kills the child and raises the moment its own
    timeout elapses, so this does not block for anywhere near 5 real
    seconds - the assertion is what proves the `timeout` argument is
    actually wired to `subprocess.run`, not merely accepted and ignored."""
    with pytest.raises(subprocess.TimeoutExpired):
        commands_module._run_subprocess(["sleep", "5"], timeout=0.05)


def test_a_timed_out_call_propagates_rather_than_being_swallowed(tmp_path):
    """Every happy-path step (`pull` here, standing in for the rest) lets
    whatever `run` raises propagate unchanged - `run_update` depends on
    that (see `runner.py`'s module docstring: "any failed step rolls
    back"). `subprocess.TimeoutExpired` is a new possibility now that
    every call carries a `timeout`; nothing in this method catches it
    specifically, so it must reach the caller exactly like any other
    failure - unlike `healthy()`/`is_ancestor()`, which deliberately turn
    any exception, timeouts included, into `False`."""
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://x/health",
        base=tmp_path / "update",
        run=TimingOutRunner(),
    )
    with pytest.raises(subprocess.TimeoutExpired):
        commands.pull("ghcr.io/lucienkerl/loxmatter:0.3.0")


def test_each_command_gets_a_timeout_matched_to_what_it_actually_does(tmp_path, monkeypatch):
    """`_run_subprocess`'s `timeout` has no default (see its own
    docstring) - every call site in this module picks one of the module
    constants, chosen for what that particular command actually talks to
    (see each constant's own comment). This walks every method that
    shells out and checks it asked for the constant its own reasoning
    claims, not a single number shared by all of them: `git fetch`/
    `docker pull` (the operator's own internet connection) get
    `_NETWORK_TIMEOUT_SECONDS`; `git checkout`/`git rev-parse`/`git
    merge-base --is-ancestor`/`docker compose ps`/`docker inspect` (the
    local checkout or the local Docker socket) get
    `_LOCAL_TIMEOUT_SECONDS`; `recreate` and `backup` each get their own,
    distinct from both.

    `_DATA_DIR`/`_BACKUP_DIR` are module constants pointed at `tmp_path`
    the same way `test_backup_writes_a_timestamped_archive_and_prunes_old_ones`
    does - `backup()`'s real `/data/backups` is not writable from a test."""
    monkeypatch.setattr(commands_module, "_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(commands_module, "_BACKUP_DIR", tmp_path / "data" / "backups")
    recorder = TimeoutRecordingRunner()
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://x/health",
        base=tmp_path / "update",
        run=recorder,
    )

    commands.fetch()
    commands.pull("ghcr.io/lucienkerl/loxmatter:0.3.0")
    commands.checkout("v0.3.0")
    commands.read_git_ref()
    commands.is_ancestor("abc1234", "def5678")
    commands.read_running_identity()
    commands.recreate()
    commands.backup()

    def timeout_for(prefix: tuple[str, ...]) -> float | None:
        matches = [t for argv, t in recorder.calls if tuple(argv[: len(prefix)]) == prefix]
        assert matches, f"no recorded call starts with {prefix!r}"
        return matches[0]

    assert timeout_for(("git", "fetch")) == commands_module._NETWORK_TIMEOUT_SECONDS
    assert timeout_for(("docker", "pull")) == commands_module._NETWORK_TIMEOUT_SECONDS
    assert timeout_for(("git", "checkout")) == commands_module._LOCAL_TIMEOUT_SECONDS
    assert timeout_for(("git", "rev-parse")) == commands_module._LOCAL_TIMEOUT_SECONDS
    assert timeout_for(("git", "merge-base")) == commands_module._LOCAL_TIMEOUT_SECONDS
    assert timeout_for(("docker", "compose", "ps")) == commands_module._LOCAL_TIMEOUT_SECONDS
    assert timeout_for(("docker", "inspect")) == commands_module._LOCAL_TIMEOUT_SECONDS
    assert timeout_for(("docker", "compose", "up")) == commands_module._RECREATE_TIMEOUT_SECONDS
    assert timeout_for(("tar", "czf")) == commands_module._BACKUP_TIMEOUT_SECONDS

    # The network budget is strictly the largest - "docker pull on a slow
    # rural connection legitimately takes minutes" would not hold if a
    # local, no-network command shared it.
    assert commands_module._NETWORK_TIMEOUT_SECONDS > commands_module._RECREATE_TIMEOUT_SECONDS
    assert commands_module._RECREATE_TIMEOUT_SECONDS > commands_module._LOCAL_TIMEOUT_SECONDS


def test_healthy_bounds_both_curl_and_the_subprocess_call_around_it(tmp_path):
    """Two layers, both required (see `_HEALTH_CURL_MAX_TIME_SECONDS`'s
    own comment): `curl --max-time` bounds one connection attempt, and
    `_run_subprocess`'s own `timeout` is the backstop in case `curl`
    somehow does not honour it. This pins that `healthy()` actually asks
    for both, not just one of them."""
    recorder = TimeoutRecordingRunner()
    commands = DockerCommands(
        repo_dir=tmp_path,
        stack_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://host.docker.internal:8080/health",
        base=tmp_path / "update",
        run=recorder,
    )
    commands.healthy()

    argv, timeout = recorder.calls[0]
    assert argv[0] == "curl"
    assert "--max-time" in argv
    max_time_value = argv[argv.index("--max-time") + 1]
    assert max_time_value == str(commands_module._HEALTH_CURL_MAX_TIME_SECONDS)
    assert timeout == commands_module._HEALTH_TIMEOUT_SECONDS
    # The curl-level bound is the tighter one - the subprocess timeout is
    # only a backstop for a curl that ignores its own `--max-time`.
    assert commands_module._HEALTH_CURL_MAX_TIME_SECONDS < commands_module._HEALTH_TIMEOUT_SECONDS
