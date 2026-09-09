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
"""The sidecar image brings along exactly the tools the script uses.

The failure this guards against is unpleasantly quiet: if `jq` is missing
from the image, the sidecar starts up, never writes a usable state, and
the web UI shows a button that does nothing. A comparison between the
`apk add` lines and the commands invoked in the script catches this here,
before someone discovers it on a Pi.

Review fix (Important #1, updater Stufe 2): the previous version of the
first test below searched the *whole Dockerfile text* for each package
name with `re.search(rf"\b{re.escape(package)}\b", source)`. That passes
for "docker-cli" as long as "docker-cli-compose" is anywhere in the file,
because `\b` matches at the hyphen - a word/non-word boundary - not just
at whitespace:

    >>> import re
    >>> bool(re.search(r"\bdocker-cli\b", "apk add docker-cli-compose git"))
    True

So deleting the standalone `docker-cli` line while keeping
`docker-cli-compose` left the old test green: exactly the quiet omission
this file's own module docstring says it exists to catch. The fix parses
the `apk add` argument list itself and compares it as a set against
REQUIRED_PACKAGES, rather than substring-searching the file text."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "deploy" / "updater" / "Dockerfile"

# What the script from task 2/3/4 invokes and what Alpine does NOT bring
# along by itself. `sh`, `mv`, `printf` are deliberately absent here - those
# are busybox built-ins and cannot be missing.
REQUIRED_PACKAGES = ("docker-cli", "docker-cli-compose", "git", "curl", "jq", "coreutils", "tar")


def _apk_add_packages(source: str) -> set[str]:
    """The package names actually passed to `apk add`, not just words that
    happen to appear somewhere in the Dockerfile.

    Walks the `RUN apk add ...` line and its backslash-continued
    successors, stripping line-continuation backslashes and flag-looking
    tokens (`--no-cache` etc.), and collects the remaining whitespace-
    separated words as the installed package set."""
    lines = source.splitlines()
    packages: list[str] = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if not in_block:
            if not re.match(r"RUN\s+apk\s+add\b", stripped):
                continue
            in_block = True
            stripped = re.sub(r"^RUN\s+apk\s+add\b", "", stripped).strip()
        continues = stripped.endswith("\\")
        content = stripped[:-1].strip() if continues else stripped
        packages.extend(word for word in content.split() if not word.startswith("-"))
        if not continues:
            break
    return set(packages)


def test_the_image_brings_every_tool_it_uses() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    installed = _apk_add_packages(source)
    missing = set(REQUIRED_PACKAGES) - installed
    assert not missing, f"apk add is missing: {sorted(missing)}"


def test_the_base_image_is_pinned() -> None:
    # A `FROM alpine:latest` would turn every rebuild of the sidecar into a
    # surprise - of all containers, the one that is root-equivalent on the
    # host.
    source = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^FROM alpine:3\.\d+", source, re.MULTILINE)
    assert "alpine:latest" not in source
