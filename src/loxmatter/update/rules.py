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
#: contribute a tag, and only one that passed `_VERSION` or `_SHA` below.
IMAGE_REPOSITORY = "ghcr.io/lucienkerl/loxmatter"

_CHANNELS = frozenset({"stable", "dev"})
# Releases are tagged `v`-prefixed (see docs/DEVELOPMENT.md); requiring
# the `v` here means a target like "0.3.0" is refused by this rule
# instead of passing all three checks and only then failing at
# `checkout`/`pull` - an avoidable trip through the rollback path.
_VERSION = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# `running_version` is not a target - it never goes near a command line -
# and it does not carry the `v` that release tags do: it comes from
# `LOXMATTER_VERSION` in the *running container's* environment
# (`commands.read_running_identity`, via `docker inspect` - see that
# method's docstring for why the checkout on disk, which can be moved or
# advanced without ever having been shipped, is the wrong source). CI
# passes it bare (`VERSION="0.3.0"`, not `v0.3.0` - see
# `.github/workflows/ci.yml`), so `_VERSION` above, which requires the
# `v` release tags carry, cannot also match it. `_VERSION` must stay
# strict for targets, so the running version gets its own, deliberately
# lenient pattern.
_RUNNING_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
# CI publishes `sha-<short>`, where `<short>` is `git rev-parse --short
# HEAD` - 7 characters at the time of writing (see
# .github/workflows/ci.yml). This pattern accepts 7 to 40 hex characters
# because a target is also a git ref and git will happily resolve a
# longer one; nothing here truncates a longer target down to what CI
# actually tagged. A target longer than the short SHA CI published for
# it will pass this check and `checkout` fine, then fail to resolve to a
# published image at `pull`. That is a constraint on whoever supplies
# the target - the web UI, today - not something to paper over with an
# invented truncation rule here.
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


class RequestRejected(Exception):
    """A request that will not be executed.

    Two representations of the one reason, not two reasons: `reason`
    stays English and unabbreviated - `runner._log` writes it straight
    into `log.txt`, which is never localised (see CLAUDE.md), and
    `tests/update/test_rules.py` matches against it directly.
    `i18n_key`/`i18n_values` are the same rejection for the operator:
    `api.update.apply_update` resolves them through `i18n.t()` for the
    `HTTPException` detail design section 12 requires an en/de pair for.
    Keeping both on the same exception is what stops the sidecar's log
    and the interface's dialog from drifting apart in wording while each
    still being written for its own reader."""

    def __init__(
        self, reason: str, *, i18n_key: str, i18n_values: dict[str, str] | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.i18n_key = i18n_key
        self.i18n_values: dict[str, str] = i18n_values or {}


def image_tag(target: str) -> str:
    """The Docker tag CI actually published for this target - not the
    target itself.

    `target` does two jobs it was never designed to share: `checkout`
    needs it as a git ref (`v0.3.0`, or a bare commit SHA), but
    `.github/workflows/ci.yml` never publishes an image under either of
    those strings. A release build strips the `v` (`ghcr.io/.../
    loxmatter:0.3.0`, computed as `${GITHUB_REF#refs/tags/v}` - never
    `:v0.3.0`); a dev build prefixes the commit with `sha-`
    (`:sha-<commit>` - never a bare SHA). Passing `target` straight
    through as the tag resolves to an image that was never pushed, so
    `pull` fails and every update goes through the rollback path - the
    one path in this system that cannot be rehearsed.

    This is the second time this feature has made this exact mistake:
    the first was the git ref versus the image tag around the rollback
    (`read_image_tag` and `read_git_ref` in `runner.py` are two
    different strings on purpose, for the same reason). If you are about
    to use `target`, or anything derived from a request, as if it were
    already a Docker tag anywhere else in this codebase, it almost
    certainly is not - go through this function instead.
    """
    version = _version_tuple(target)
    if version is not None:
        return target.removeprefix("v")
    if _SHA.match(target) is not None:
        return f"sha-{target}"
    # Reachable only if called on a target that never passed
    # `check_request` - every caller in this codebase does first.
    raise ValueError(f"target {target!r} is neither a version nor a commit")


def image_reference(target: str) -> str:
    return f"{IMAGE_REPOSITORY}:{image_tag(target)}"


def _version_tuple(value: str, pattern: re.Pattern[str] = _VERSION) -> tuple[int, int, int] | None:
    match = pattern.match(value)
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
        raise RequestRejected(
            f"unknown channel {request.channel!r}",
            i18n_key="api.update.rejected.unknown_channel",
            i18n_values={"channel": request.channel},
        )

    target = request.target
    target_version = _version_tuple(target)
    if target_version is None and _SHA.match(target) is None:
        raise RequestRejected(
            f"target {target!r} is neither a version nor a commit",
            i18n_key="api.update.rejected.target_not_version_or_commit",
            i18n_values={"target": target},
        )

    if request.channel == "stable":
        if target_version is None:
            raise RequestRejected(
                f"target {target!r} is not a version, but the channel is stable",
                i18n_key="api.update.rejected.target_not_version_for_stable",
                i18n_values={"target": target},
            )
        current = _version_tuple(running_version, _RUNNING_VERSION)
        # An unparsable running version means we cannot prove the request
        # goes forward. Refusing is the only safe reading: the whole point
        # of the rule is that it never lets anything move backwards.
        if current is None or target_version <= current:
            raise RequestRejected(
                f"target {target!r} is not newer than the running {running_version!r} - "
                "this route never goes to an older version",
                i18n_key="api.update.rejected.target_not_newer",
                i18n_values={"target": target, "running_version": running_version},
            )
        return

    # dev: no ordering over SHAs, so "forward" means "descendant".
    if target_version is not None:
        raise RequestRejected(
            f"target {target!r} is a version, but the channel is dev",
            i18n_key="api.update.rejected.target_is_version_for_dev",
            i18n_values={"target": target},
        )
    if not is_ancestor(running_commit, target):
        raise RequestRejected(
            f"target {target!r} does not descend from the running commit {running_commit!r}",
            i18n_key="api.update.rejected.target_not_descendant",
            i18n_values={"target": target, "running_commit": running_commit},
        )
