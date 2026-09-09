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

from pathlib import Path

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
        compose_dir=tmp_path, env_path=env, health_url="http://x/health", run=RecordingRunner()
    )
    assert commands.read_image_tag() == "stable"


def test_checkout_fetches_tags_first(tmp_path):
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
        compose_dir=tmp_path, env_path=env, health_url="http://x/health", run=RecordingRunner()
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
        run=RaisingRunner(),
    )
    assert failing.is_ancestor("abc1234", "def5678") is False
