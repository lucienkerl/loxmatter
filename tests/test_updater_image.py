# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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
"""The sidecar image brings along exactly the tools the script uses.

The failure this guards against is unpleasantly quiet: if `jq` is missing
from the image, the sidecar starts up, never writes a usable state, and
the web UI shows a button that does nothing. A comparison between the
`apk add` lines and the commands invoked in the script catches this here,
before someone discovers it on a Pi."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "deploy" / "updater" / "Dockerfile"

# What the script from task 2/3/4 invokes and what Alpine does NOT bring
# along by itself. `sh`, `mv`, `printf` are deliberately absent here - those
# are busybox built-ins and cannot be missing.
REQUIRED_PACKAGES = ("docker-cli", "docker-cli-compose", "git", "curl", "jq", "coreutils", "tar")


def test_das_image_bringt_jedes_benutzte_werkzeug_mit() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    for package in REQUIRED_PACKAGES:
        assert re.search(rf"\b{re.escape(package)}\b", source), package


def test_die_basis_ist_gepinnt() -> None:
    # A `FROM alpine:latest` would turn every rebuild of the sidecar into a
    # surprise - of all containers, the one that is root-equivalent on the
    # host.
    source = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^FROM alpine:3\.\d+", source, re.MULTILINE)
    assert "alpine:latest" not in source
