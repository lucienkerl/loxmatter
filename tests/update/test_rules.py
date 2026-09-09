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

"""The three rules that decide whether a request is executed at all.

Design section 10. They are enforced in the updater, not at login: the
session that reaches this route already downloads the Fabric backup, so
another password prompt would claim a boundary that does not exist. The
boundary that does exist is here.
"""

from __future__ import annotations

import pytest

from loxmatter.update.protocol import UpdateRequest
from loxmatter.update.rules import IMAGE_REPOSITORY, RequestRejected, check_request, image_reference


def request(**overrides: str) -> UpdateRequest:
    base = {
        "id": "01J000000000000000000000",
        "channel": "stable",
        "target": "v0.3.0",
        "requested_at": "2026-09-09T10:00:00+00:00",
    }
    base.update(overrides)
    return UpdateRequest(**base)  # type: ignore[arg-type]


def never_ancestor(_running: str, _target: str) -> bool:
    return False


def always_ancestor(_running: str, _target: str) -> bool:
    return True


def test_a_stable_request_for_a_newer_version_passes():
    check_request(
        request(),
        running_version="0.2.0",
        running_commit="abc1234",
        is_ancestor=never_ancestor,
    )


def test_an_unknown_channel_is_rejected():
    with pytest.raises(RequestRejected, match="channel"):
        check_request(
            request(channel="nightly"),
            running_version="0.2.0",
            running_commit="abc1234",
            is_ancestor=always_ancestor,
        )


@pytest.mark.parametrize(
    "target",
    [
        "v0.3.0; rm -rf /",
        "../../etc/passwd",
        "latest",
        "",
        "v0.3",
        "$(whoami)",
        "abc",  # too short for a SHA
    ],
)
def test_a_target_that_is_not_a_version_or_a_sha_is_rejected(target):
    """The target reaches a command line. Anything that is not plainly a
    version or a commit is refused before it can get there - this is the
    rule that stops the route being a shell."""
    with pytest.raises(RequestRejected, match="target"):
        check_request(
            request(target=target),
            running_version="0.2.0",
            running_commit="abc1234",
            is_ancestor=always_ancestor,
        )


def test_stable_refuses_to_go_backwards():
    """Forward only, by semantic version. Going back is the updater's own
    business, from its own bookkeeping - never through this route."""
    with pytest.raises(RequestRejected, match="older"):
        check_request(
            request(target="v0.1.0"),
            running_version="0.2.0",
            running_commit="abc1234",
            is_ancestor=never_ancestor,
        )


def test_stable_refuses_the_version_already_running():
    with pytest.raises(RequestRejected, match="older"):
        check_request(
            request(target="v0.2.0"),
            running_version="0.2.0",
            running_commit="abc1234",
            is_ancestor=never_ancestor,
        )


def test_dev_requires_the_target_to_descend_from_the_running_commit():
    """There is no ordering over SHAs, so "forward" means "descendant".
    A commit that is not one is a sideways or backwards move."""
    with pytest.raises(RequestRejected, match="descend"):
        check_request(
            request(channel="dev", target="deadbee"),
            running_version="0.2.0",
            running_commit="abc1234",
            is_ancestor=never_ancestor,
        )


def test_dev_accepts_a_descendant():
    check_request(
        request(channel="dev", target="deadbee"),
        running_version="0.2.0",
        running_commit="abc1234",
        is_ancestor=always_ancestor,
    )


def test_the_image_name_never_comes_from_the_request():
    """Design section 10, rule 2. The repository is hard-coded here; the
    request contributes only the tag, and only after passing the pattern
    above. A request naming a foreign registry has nowhere to put it."""
    assert image_reference("v0.3.0") == f"{IMAGE_REPOSITORY}:v0.3.0"
