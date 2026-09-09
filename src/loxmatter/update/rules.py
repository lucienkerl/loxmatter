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

"""What a request must satisfy before anything is executed.

Design section 10. Three rules, and the reason they live here rather than
in the bridge's route: the bridge is reachable across the whole LAN with
`network_mode: host`, the sidecar is not. Whoever takes over the bridge
completely can still only make this container install a published, newer
loxmatter version.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from loxmatter.update.protocol import UpdateRequest

#: Hard-coded, never taken from the request (rule 2). The request may
#: contribute a tag, and only one that passed `_TARGET` below.
IMAGE_REPOSITORY = "ghcr.io/lucienkerl/loxmatter"

_CHANNELS = frozenset({"stable", "dev"})
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


class RequestRejected(Exception):
    """A request that will not be executed. The reason reaches the
    interface, so it is written for a person, not for a log grep."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def image_reference(target: str) -> str:
    return f"{IMAGE_REPOSITORY}:{target}"


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = _VERSION.match(value)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def check_request(
    request: UpdateRequest,
    *,
    running_version: str,
    running_commit: str,
    is_ancestor: Callable[[str, str], bool],
) -> None:
    """Raise `RequestRejected` unless all three rules hold.

    `is_ancestor(running, target)` is injected rather than shelling out to
    git here: the rule is logic and deserves a test, and the git call is
    the command layer's business (see `commands.py`).
    """
    if request.channel not in _CHANNELS:
        raise RequestRejected(f"unknown channel {request.channel!r}")

    target = request.target
    target_version = _version_tuple(target)
    if target_version is None and _SHA.match(target) is None:
        raise RequestRejected(f"target {target!r} is neither a version nor a commit")

    if request.channel == "stable":
        if target_version is None:
            raise RequestRejected(f"target {target!r} is not a version, but the channel is stable")
        current = _version_tuple(running_version)
        # An unparsable running version means we cannot prove the request
        # goes forward. Refusing is the only safe reading: the whole point
        # of the rule is that it never lets anything move backwards.
        if current is None or target_version <= current:
            raise RequestRejected(
                f"target {target!r} is not newer than the running {running_version!r} - "
                "this route never goes to an older version"
            )
        return

    # dev: no ordering over SHAs, so "forward" means "descendant".
    if target_version is not None:
        raise RequestRejected(f"target {target!r} is a version, but the channel is dev")
    if not is_ancestor(running_commit, target):
        raise RequestRejected(
            f"target {target!r} does not descend from the running commit {running_commit!r}"
        )
