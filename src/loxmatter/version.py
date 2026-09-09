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

"""How the running version knows its identity - draft "Deploy updates via the UI"
(2026-09-08), section 4.

The values come from the ENVIRONMENT, not from the checkout on the host.
`Dockerfile` stores them at build time as `ENV`, sourced from build arguments
that CI sets. The reason for this direction: a checkout on the host
may have moved elsewhere, progressed further, or been relocated, without
that ever being deployed - the image, by contrast, IS what is running.

Outside an image - in the development checkout, where `uv run loxmatter`
starts directly - the variables are missing. This is not an error condition, but
the normal case for development: `version` then reads "dev",
`commit`/`built_at` are None. If someone made this an exception, the
bridge could no longer start outside of Docker.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from loxmatter.model.store import schema_version


@dataclass(frozen=True)
class BuildInfo:
    version: str
    commit: str | None
    built_at: str | None
    schema_version: int


def _clean(name: str) -> str | None:
    """Treat empty environment variables as missing.

    Docker Compose interpolates a variable missing from `.env` to an
    EMPTY string, not to "not set". This exact trap already caught
    `LOXMATTER_API_TOKEN` once (see the detailed explanation in deploy/testhost/docker-compose.yml);
    without this function the version on a host without a set value would be "" instead of "dev".
    """
    value = os.environ.get(name, "").strip()
    return value or None


def build_info() -> BuildInfo:
    return BuildInfo(
        version=_clean("LOXMATTER_VERSION") or "dev",
        commit=_clean("LOXMATTER_COMMIT"),
        built_at=_clean("LOXMATTER_BUILT_AT"),
        # Deliberately NOT from the environment: see docstring of
        # `test_schema_version_cannot_be_forged_from_the_environment`.
        schema_version=schema_version(),
    )
