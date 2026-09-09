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

"""What is new - design "Applying updates through the web UI"
(2026-09-08), section 9.

The network layer comes in as `fetch`, instead of being hardwired here.
That is not an end in itself: it means every test runs without a
network, and the "no internet" case becomes a test case instead of a
random occurrence in CI. It also keeps this module free of any HTTP
client - `api/update.py` (Task 8) supplies the real fetcher, built on
whatever library the rest of the API layer already uses.

A device without internet access is explicitly NOT an error here. This
bridge sits in a home, not a data center; anyone running it with no path
outward has chosen that. That is why `Available` carries an `error`
field instead of raising an exception - the web UI then shows a calm
notice ("last checked ..."), not a red banner.

GitHub's two endpoints here are read without a token, so their answers
are treated as untrusted input: a missing field, a rate-limit body, or a
value that does not parse as expected must produce a renderable
`Available` with `error` set, never an exception that reaches the web
UI. The single `try/except` at the bottom of `check()` is deliberately
wide for that reason - narrowing it invites a new failure mode of
GitHub's to slip through uncaught the next time this file is touched."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# The Fetch signature says "already-parsed JSON", not "raw response": the
# real fetcher (Task 8) is the one place that turns a non-2xx status or an
# HTML error page into an exception (or a decoded error body) before this
# module ever sees it. What lands here is either a JSON object or - should
# GitHub ever answer one of these two endpoints with an array - a JSON
# list, which `check()` treats as "not what was expected" rather than
# risking an `AttributeError` on `.get()`.
Fetch = Callable[[str], Awaitable["dict[str, Any] | list[Any]"]]

_RELEASE_URL = "https://api.github.com/repos/lucienkerl/loxmatter/releases/latest"
_COMPARE_URL = "https://api.github.com/repos/lucienkerl/loxmatter/compare/{base}...main"


@dataclass(frozen=True)
class Available:
    """What `check()` found, always - never raised, see the module
    docstring. `target` is the one field the web UI needs to decide
    whether an update button appears at all; the rest is presentation."""

    channel: str
    target: str | None
    title: str | None
    notes: str | None
    behind: int | None
    checked_at: str
    error: str | None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_tuple(version: str) -> tuple[int, ...] | None:
    """A release tag as a comparable tuple, or `None` if it is not a
    plain `x.y.z` (a pre-release suffix like `-rc1` counts as "not
    plain" here) - better to say "unknown version" than to guess an
    ordering for a shape this function has not seen before."""
    try:
        return tuple(int(part) for part in version.lstrip("v").split("."))
    except ValueError:
        return None


def _normalize_tuples(
    t1: tuple[int, ...], t2: tuple[int, ...]
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Zero-pad the shorter of two tuples to match the longer one's length.

    This defends against inconsistent tagging discipline: a release tagged
    v0.3.0 and a running version 0.3 are the same release written two ways,
    and must compare as equal. Python's tuple comparison would otherwise sort
    (0, 3) < (0, 3, 0), incorrectly flagging 0.3.0 as a newer update."""
    max_len = max(len(t1), len(t2))
    padded_t1 = t1 + (0,) * (max_len - len(t1))
    padded_t2 = t2 + (0,) * (max_len - len(t2))
    return padded_t1, padded_t2


async def check(
    channel: str,
    *,
    current_version: str,
    current_commit: str | None,
    fetch: Fetch,
) -> Available:
    if channel not in ("stable", "dev"):
        raise ValueError(channel)

    def unavailable(error: str | None = None) -> Available:
        return Available(channel, None, None, None, None, _now(), error)

    if channel == "dev" and not current_commit:
        # An image built by hand (or a development checkout) carries no
        # LOXMATTER_COMMIT (see `version.py`). The comparison has no
        # starting point then, and there is nothing to guess it from -
        # reporting "no update" here would look like a real answer
        # instead of the missing one it is.
        return unavailable(
            "This version does not state a commit - the development channel cannot compare."
        )

    try:
        if channel == "stable":
            body = await fetch(_RELEASE_URL)
            if not isinstance(body, dict):
                return unavailable("GitHub's response does not state a version.")
            tag = str(body.get("tag_name", "")).lstrip("v")
            latest, current = _as_tuple(tag), _as_tuple(current_version)
            if not tag or latest is None:
                return unavailable("GitHub's response does not state a version.")
            # Anyone running a development state newer than the latest
            # release deliberately gets no target here: "forward only"
            # is enforced again in the sidecar, but a button that gets
            # reliably rejected on every click is a broken button, not a
            # safeguard.
            if current is not None:
                # Normalize tuple lengths to handle versions with differing
                # component counts (e.g., "0.3" and "0.3.0" are the same
                # release written two ways, and must compare as equal).
                latest_norm, current_norm = _normalize_tuples(latest, current)
                if latest_norm <= current_norm:
                    return unavailable()
            return Available(
                channel,
                tag,
                str(body.get("name") or tag),
                str(body.get("body") or ""),
                None,
                _now(),
                None,
            )

        body = await fetch(_COMPARE_URL.format(base=current_commit))
        if not isinstance(body, dict):
            return unavailable("GitHub's response does not state how many commits are ahead.")
        # No default here on purpose: a `compare` response without
        # `ahead_by` (a rate-limit body, say) is missing information, not
        # evidence of "zero commits ahead" - the `KeyError` this raises is
        # caught below and turned into an honest `error`, not a
        # deceptively quiet "you are up to date".
        ahead = int(body["ahead_by"])
        if ahead <= 0:
            return unavailable()
        # Subject lines only: the body of a commit message in this
        # project is often half an essay, and the card should give an
        # overview, not a read. `split("\n", 1)` also copes with a
        # (theoretical) empty message, where `splitlines()[0]` would
        # raise `IndexError`.
        subjects = [str(c["commit"]["message"]).split("\n", 1)[0] for c in body.get("commits", [])]
        return Available(channel, "main", None, "\n".join(subjects), ahead, _now(), None)
    except (OSError, KeyError, ValueError, TypeError) as exc:
        return unavailable(str(exc))
