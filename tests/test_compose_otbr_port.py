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

"""The two ports .env can move: LOXMATTER_PORT (web interface, 8080) and
OTBR_REST_PORT (border router REST API, 8081).

Unset, both must render exactly as before - the border router's command
above all, because a changed command recreates `otbr` on the next
`compose up` and restarts the Thread network for nothing. Rendered by
`docker compose config` where Docker is installed; the raw strings are
checked everywhere."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

STACK = Path(__file__).resolve().parent.parent / "deploy" / "testhost"
COMPOSE = STACK / "docker-compose.yml"


def _stack() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_the_ports_come_from_the_env_file_with_the_old_defaults() -> None:
    stack = _stack()
    command = stack["services"]["loxmatter"]["command"]
    assert command[command.index("--listen") + 1] == "${LOXMATTER_PORT:-8080}"
    environment = stack["services"]["loxmatter"]["environment"]
    assert environment["LOXMATTER_OTBR_URL"] == "http://127.0.0.1:${OTBR_REST_PORT:-8081}"
    updater = stack["services"]["loxmatter-updater"]["environment"]
    assert (
        updater["LOXMATTER_HEALTH_URL"]
        == "http://host.docker.internal:${LOXMATTER_PORT:-8080}/health"
    )
    assert "${OTBR_REST_PORT:+" in stack["services"]["otbr"]["command"]


def _render(tmp_path: Path, extra: str) -> dict:
    env_file = tmp_path / ".env"
    env_file.write_text((STACK / ".env.example").read_text(encoding="utf-8") + extra)
    rendered = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "--env-file", str(env_file), "config"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return yaml.safe_load(rendered)


needs_compose = pytest.mark.skipif(shutil.which("docker") is None, reason="docker is required")


@needs_compose
def test_unset_ports_render_as_before(tmp_path: Path) -> None:
    services = _render(tmp_path, "")["services"]
    assert services["otbr"]["command"] == ["--backbone-interface", "wlan0"]
    command = services["loxmatter"]["command"]
    assert command[command.index("--listen") + 1] == "8080"
    assert services["loxmatter"]["environment"]["LOXMATTER_OTBR_URL"] == "http://127.0.0.1:8081"


@needs_compose
def test_moved_ports_reach_every_service(tmp_path: Path) -> None:
    services = _render(tmp_path, "\nLOXMATTER_PORT=8083\nOTBR_REST_PORT=8084\n")["services"]
    # `--trel-url`'s value is the slot the image's entrypoint passes to
    # otbr-agent as it stands; the shell splits it there into the option
    # and its value.
    assert services["otbr"]["command"] == [
        "--backbone-interface",
        "wlan0",
        "--trel-url",
        "--rest-listen-port 8084",
    ]
    command = services["loxmatter"]["command"]
    assert command[command.index("--listen") + 1] == "8083"
    assert services["loxmatter"]["environment"]["LOXMATTER_OTBR_URL"] == "http://127.0.0.1:8084"
    health = services["loxmatter-updater"]["environment"]["LOXMATTER_HEALTH_URL"]
    assert health == "http://host.docker.internal:8083/health"
