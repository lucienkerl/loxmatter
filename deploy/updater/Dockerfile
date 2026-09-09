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

# THIS CONTAINER IS ROOT-EQUIVALENT ON THE HOST THROUGH THE DOCKER SOCKET.
# Anyone who can run a command in it can run any command on the host as
# root. That is the price of the whole "update from the web interface"
# feature (design docs/superpowers/specs/2026-09-08-webui-updates-design.md,
# section 10): only the process holding the socket can start the container
# that replaces the running one, so something has to hold it. It is not
# secured by permissions inside the container - there are none worth
# having - but by tightness around it: no `ports:`, no
# `network_mode: host` (see deploy/testhost/docker-compose.yml), a single
# fixed work program (`loxmatter.update`), and the three rules in
# `loxmatter/update/rules.py` that let a request through the bridge's `/api`
# reach at most a published, newer loxmatter version - never an arbitrary
# image, never an arbitrary command. Do not add a port, do not add host
# networking, and do not let this image grow a second job.

FROM alpine:3.20

# docker-cli, docker-cli-compose: this is what the socket above is for -
# `docker compose pull` / `up -d` to replace the running bridge.
# git: the updater checks out the target commit/tag before switching (design
# section 8). curl: the post-switch health check against the bridge's
# `/health` (see extra_hosts in the Compose file). python3: the update loop
# itself - pure standard library, see below, so nothing else is installed.
RUN apk add --no-cache \
    docker-cli \
    docker-cli-compose \
    git \
    curl \
    python3

# `/app` holds this image's own code and must never be mounted over: the
# host checkout the updater runs `git fetch`/`checkout` against belongs at
# `/checkout` (see `DEFAULT_COMPOSE_DIR` in `loxmatter/update/__main__.py`
# and the mount in `deploy/testhost/docker-compose.yml`). A bind mount here
# shadows `loxmatter/` and the entrypoint dies with `ModuleNotFoundError`
# before running anything.
WORKDIR /app

# Exactly the modules `loxmatter.update.__main__` reaches by import, and
# nothing else - derived by grepping the `from loxmatter` / `import loxmatter`
# lines across src/loxmatter/update/*.py and following those imports in turn,
# not copied from an earlier draft of this list. `rules.py` carries its own
# `_CHANNELS` and does not import `UpdateChannel`, so the database layer
# (`loxmatter/model/`) is not part of this image - nor is FastAPI, nor the
# Matter client, both pulled in only by other parts of `loxmatter` that this
# sidecar never imports. A smaller import graph here is a smaller attack
# surface in the one container that holds the Docker socket.
COPY src/loxmatter/__init__.py loxmatter/__init__.py
COPY src/loxmatter/timestamps.py loxmatter/timestamps.py
COPY src/loxmatter/update/__init__.py loxmatter/update/__init__.py
COPY src/loxmatter/update/__main__.py loxmatter/update/__main__.py
COPY src/loxmatter/update/protocol.py loxmatter/update/protocol.py
COPY src/loxmatter/update/rules.py loxmatter/update/rules.py
COPY src/loxmatter/update/commands.py loxmatter/update/commands.py
COPY src/loxmatter/update/runner.py loxmatter/update/runner.py

ENTRYPOINT ["python3", "-m", "loxmatter.update"]
