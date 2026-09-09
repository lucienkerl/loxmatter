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

"""The `loxmatter-updater` service must stay exactly as narrow as the
design argues for (docs/superpowers/specs/2026-09-08-webui-updates-design.md,
section 6): it holds the Docker socket, which makes it root-equivalent on
the host, and the only thing that justifies that is that - unlike the
other three services in this file - it is unreachable from the LAN.
These tests pin down the properties that carry that argument, not
everything the service happens to contain.
"""

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parent.parent / "deploy" / "testhost" / "docker-compose.yml"


def _stack() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _updater() -> dict:
    return _stack()["services"]["loxmatter-updater"]


def test_the_updater_is_not_reachable_from_the_lan() -> None:
    """This is the whole reason the updater is allowed to hold the Docker
    socket. The other three services in this file run with
    `network_mode: host` or publish ports and are reachable across the
    LAN; this one sits on Compose's own network, reachable outward for
    `git fetch` and not inward. If either key is ever added back, the
    socket has to go with it - a LAN-reachable, root-equivalent container
    is exactly the shape of bridge the design rejected in section 6.
    """
    service = _updater()
    assert "network_mode" not in service
    assert "ports" not in service


def test_the_updater_mounts_the_socket_the_store_and_the_checkout() -> None:
    """Without the socket mount the updater cannot recreate the bridge at
    all - the button would run and then hang or fail on every request.
    Without `loxmatter-store:/data` it cannot see the request the bridge
    wrote or report state back to it, so the two sides would each wait on
    a file the other never sees. And the checkout must land at
    `/checkout`, never at `/app`: `/app` is where the sidecar image keeps
    its own code (`WORKDIR /app` in deploy/updater/Dockerfile), and a bind
    mount there shadows `loxmatter/` - the entrypoint dies with
    `ModuleNotFoundError` before running a line, invisible except as a
    restart loop and an update button that never works. This was verified
    against the real image, both ways.
    """
    mounts = set(_updater()["volumes"])
    assert "/var/run/docker.sock:/var/run/docker.sock" in mounts
    assert "loxmatter-store:/data" in mounts
    assert any(mount.endswith(":/checkout") for mount in mounts)
    assert not any(mount.endswith(":/app") for mount in mounts)


def test_the_updater_can_reach_the_bridges_health_check() -> None:
    """`healthy()` (src/loxmatter/update/commands.py) polls
    `http://host.docker.internal:8080/health` after every recreate. The
    bridge runs with `network_mode: host`, so it is not a resolvable
    Compose service name from this container's own network - only
    `host.docker.internal` reaches it, and on Linux only because this
    entry names the gateway explicitly. Without it, `healthy()` returns
    `False` for the whole rollback deadline and every update rolls back,
    on a perfectly healthy bridge.
    """
    assert _updater()["extra_hosts"] == ["host.docker.internal:host-gateway"]


def test_the_updater_restarts_itself_after_a_crash_or_reboot() -> None:
    """Without `restart: unless-stopped`, a sidecar crash or a Pi reboot
    leaves the update loop dead until someone notices and runs
    `docker compose up -d` by hand - the same silent-failure shape the
    other three services in this file are already guarded against.
    """
    assert _updater()["restart"] == "unless-stopped"


def test_the_updater_resolves_compose_commands_against_deploy_testhost() -> None:
    """`DockerCommands` (src/loxmatter/update/commands.py) runs every
    `git fetch`/`git checkout` AND every `docker compose ...` call, plus
    every `.env` read, in the one directory `compose_dir` names. `git`
    needs the repository's `.git`, which sits two directories above
    `deploy/testhost` - so the checkout mount below carries the whole
    repository, not just this directory. But `docker-compose.yml` and
    `.env` live in `deploy/testhost`, not at the repository root that
    `DEFAULT_COMPOSE_DIR` (`/checkout`) names by default. Without this
    override, `docker compose up -d --no-deps loxmatter` and the `.env`
    rewrite both run in the wrong directory and fail on every request -
    `git` would still work, `docker compose` would not.
    """
    assert _updater()["environment"]["LOXMATTER_COMPOSE_DIR"] == "/checkout/deploy/testhost"
