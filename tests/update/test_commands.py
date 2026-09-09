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


def make(tmp_path: Path) -> tuple[DockerCommands, RecordingRunner]:
    env = tmp_path / ".env"
    env.write_text("LOXMATTER_IMAGE_TAG=stable\nMINISERVER_IP=10.0.0.1\n", encoding="utf-8")
    runner = RecordingRunner()
    commands = DockerCommands(
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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
        compose_dir=tmp_path,
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


def test_cap_log_does_nothing_under_the_cap(tmp_path):
    base = tmp_path / "update"
    commands = DockerCommands(
        compose_dir=tmp_path,
        env_path=tmp_path / ".env",
        health_url="http://x/health",
        base=base,
        run=RecordingRunner(),
    )
    commands.log("short")
    commands.log("also short")
    text = (base / "log.txt").read_text(encoding="utf-8")
    assert len(text.splitlines()) == 2
