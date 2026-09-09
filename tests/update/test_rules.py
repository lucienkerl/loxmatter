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
from loxmatter.update.rules import (
    IMAGE_REPOSITORY,
    RequestRejected,
    check_request,
    image_reference,
    image_tag,
)


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
    # Matching on "unknown channel" rather than just "channel": the dev
    # branch's own message ("target %r is a version, but the channel is
    # dev") also contains the word "channel", so a looser match would
    # still pass with this rule deleted entirely, as long as the target
    # looked like a version - see the sibling test below for the case
    # that actually requires this rule to be present.
    with pytest.raises(RequestRejected, match="unknown channel"):
        check_request(
            request(channel="nightly"),
            running_version="0.2.0",
            running_commit="abc1234",
            is_ancestor=always_ancestor,
        )


def test_an_unknown_channel_is_rejected_even_with_a_commit_target():
    """Bite check for rule 1: with a version-shaped target,
    the dev branch's own "the channel is dev" message happens to reject
    the request too, so `test_an_unknown_channel_is_rejected` alone would
    not notice the channel check being deleted if it matched loosely on
    "channel". A commit-SHA target does not trip that branch's own check
    at all: with the channel rule gone, `channel="nightly"` and a SHA
    target with `is_ancestor` always true is accepted outright - deleting
    rules.py:75-76 and running this reproduces exactly that.
    """
    with pytest.raises(RequestRejected, match="unknown channel"):
        check_request(
            request(channel="nightly", target="deadbeef"),
            running_version="0.2.0",
            running_commit="abc1234",
            is_ancestor=always_ancestor,
        )


@pytest.mark.parametrize("channel", ["stable", "dev"])
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
        "0.3.0",  # a real version, but missing the "v" that tags carry
    ],
)
def test_a_target_that_is_not_a_version_or_a_sha_is_rejected(channel, target):
    """The target reaches a command line. Anything that is not plainly a
    version or a commit is refused before it can get there - this is the
    rule that stops the route being a shell.

    Matching on the gate's own wording ("neither a version nor a commit")
    rather than the word "target" is what makes this test notice the gate
    being gone: the stable branch's own message (rules.py:85, for a
    target that parses but isn't a version) also contains "target", so a
    looser match kept passing with the shared gate deleted - because on
    `stable` every one of these targets still gets rejected by that
    branch's own check. Parametrising over `dev` too is what actually
    proves the gate matters: `dev` has no target-pattern check of its own
    to fall back on, so with the gate gone these same seven strings -
    `"$(whoami)"` and `"v0.3.0; rm -rf /"` among them - are accepted
    there outright.
    """
    with pytest.raises(RequestRejected, match="neither a version nor a commit"):
        check_request(
            request(channel=channel, target=target),
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
    assert image_reference("v0.3.0") == f"{IMAGE_REPOSITORY}:0.3.0"


def test_a_stable_target_becomes_the_tag_ci_publishes_for_a_release():
    """Finding 1: `.github/workflows/ci.yml` computes
    `version="${GITHUB_REF#refs/tags/v}"` from the tag `v0.3.0` and pushes
    `ghcr.io/lucienkerl/loxmatter:0.3.0` - never `:v0.3.0`. The `v` that
    makes a target a valid git ref is exactly the character that must not
    reach the Docker tag."""
    assert image_tag("v0.3.0") == "0.3.0"
    assert image_reference("v0.3.0") == f"{IMAGE_REPOSITORY}:0.3.0"


def test_a_dev_target_becomes_the_sha_prefixed_tag_ci_publishes():
    """Finding 1: a dev build pushes `:dev` and `:sha-<commit>` - the
    bare commit SHA that `checkout` uses as a git ref is never itself a
    published tag."""
    assert image_tag("deadbee") == "sha-deadbee"
    assert image_reference("deadbee") == f"{IMAGE_REPOSITORY}:sha-deadbee"
