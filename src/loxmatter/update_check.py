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
from datetime import UTC, datetime, timedelta
from typing import Any

# The Fetch signature says "already-parsed JSON", not "raw response": the
# real fetcher (Task 8) is the one place that turns a non-2xx status or an
# HTML error page into an exception (or a decoded error body) before this
# module ever sees it. What lands here is either a JSON object or - should
# GitHub ever answer one of these two endpoints with an array - a JSON
# list, which `check()` treats as "not what was expected" rather than
# risking an `AttributeError` on `.get()`.
Fetch = Callable[[str], Awaitable["dict[str, Any] | list[Any]"]]

# A second, distinct callable for `resolve_updater_digest()` below: what
# that answer needs is a RESPONSE HEADER (GHCR's `Docker-Content-Digest`),
# not the parsed body `Fetch` promises - a container registry's manifest
# response is never read here as JSON to interpret, only as headers a
# caller supplies alongside the request headers this function needs to
# send (the bearer token, the manifest-list `Accept` negotiation). Takes
# the target URL and the request headers to send, returns the response
# headers (case-insensitive lookup is the implementation's job, the same
# way `aiohttp`'s own header mapping already behaves) - the body is
# discarded by whatever implements this, deliberately: a manifest can be
# sizable and nothing here reads it.
FetchHeaders = Callable[[str, "dict[str, str]"], Awaitable["dict[str, str]"]]

_RELEASE_URL = "https://api.github.com/repos/lucienkerl/loxmatter/releases/latest"
_COMPARE_URL = "https://api.github.com/repos/lucienkerl/loxmatter/compare/{base}...main"

# The updater sidecar's own image - a DIFFERENT GHCR repository from the
# bridge's own (see `_RELEASE_URL`/`_COMPARE_URL` above, which name
# `lucienkerl/loxmatter`, not `-updater`). `.github/workflows/ci.yml`'s
# `updater-image` job is the one publisher of this repository's `:stable`
# tag.
_UPDATER_GHCR_REPO = "lucienkerl/loxmatter-updater"
_GHCR_TOKEN_URL = "https://ghcr.io/token?scope=repository:{repo}:pull&service=ghcr.io"
_GHCR_MANIFEST_URL = "https://ghcr.io/v2/{repo}/manifests/{tag}"
# Both media types a multi-platform manifest list can come back as - GHCR
# has been observed to answer with either depending on how the image was
# pushed, and asking for both up front is what the verified-working
# invocation in this feature's own design brief specifies; a server that
# only recognises one of the two simply ignores the other.
_GHCR_MANIFEST_ACCEPT = (
    "application/vnd.oci.image.index.v1+json,"
    "application/vnd.docker.distribution.manifest.list.v2+json"
)


async def resolve_updater_digest(*, fetch: Fetch, fetch_headers: FetchHeaders) -> str | None:
    """What GHCR currently serves the updater sidecar's own image under
    the `:stable` tag, as a `repo@sha256:...`-shaped digest's bare
    `sha256:...` half - the same kind of value `update-once.sh`'s own
    `updater_digest()` reports for the sidecar CONTAINER actually
    running. The two are meant to be compared directly (see
    `api/update.py`'s `_status()`), never parsed further here.

    A two-step, unauthenticated flow - GHCR requires an anonymous pull
    token even for a public image before it will answer a manifest
    request at all:

      1. `GET https://ghcr.io/token?scope=repository:<repo>:pull&service=ghcr.io`
         -> `{"token": "..."}`
      2. `GET https://ghcr.io/v2/<repo>/manifests/stable`, bearing that
         token, asking for a manifest-list `Accept` - the
         `Docker-Content-Digest` response HEADER (not the body) is the
         answer.

    `None` for every failure shape alike - a network error, an
    unrecognised token response, a missing digest header - the same
    "unknown, not a claim either way" doctrine `check()` above follows
    for its own `error` field, except this function has no `error` field
    to carry one: the caller (`api/update.py`) shows nothing at all when
    this reads `None`, and a nagging false positive from a registry
    hiccup would be worse than a warning that occasionally stays silent
    one cycle longer than it strictly had to."""
    try:
        token_body = await fetch(_GHCR_TOKEN_URL.format(repo=_UPDATER_GHCR_REPO))
        if not isinstance(token_body, dict):
            return None
        token = token_body.get("token")
        if not isinstance(token, str) or not token:
            return None
        headers = await fetch_headers(
            _GHCR_MANIFEST_URL.format(repo=_UPDATER_GHCR_REPO, tag="stable"),
            {"Authorization": f"Bearer {token}", "Accept": _GHCR_MANIFEST_ACCEPT},
        )
    except (OSError, KeyError, ValueError, TypeError):
        return None
    # GHCR's own header casing is "Docker-Content-Digest", but HTTP header
    # names are case-insensitive by spec and nothing here should trust a
    # particular fetcher's mapping to preserve that casing - checked
    # lower-cased against a lower-cased lookup instead of relying on the
    # caller to hand back a case-insensitive mapping type.
    lowered = {key.lower(): value for key, value in headers.items()}
    digest = lowered.get("docker-content-digest")
    return digest if digest else None


class UpdaterDigestCache:
    """Caches `resolve_updater_digest()`'s answer for a few minutes -
    `/api/update/status` is polled every two seconds for the entire span
    of a running update (see `app.js`'s `startUpdateTimer`), and without
    this, every one of those polls would repeat the same GHCR round trip
    for a value that only ever changes when a new updater image is
    published, at most a few times a month. One instance is meant to live
    for the process's whole lifetime (`api/update.py` constructs exactly
    one, alongside the router), the same way `Store` is constructed once
    and threaded through rather than reopened per request.

    Deliberately NOT gated on `check_enabled` itself - that setting lives
    in `store.update_settings`, which this module has no access to (see
    `check()`'s own module docstring: update_check.py knows no HTTP or
    settings concepts). The caller is expected to skip calling `get()`
    entirely while checking is disabled, the same way `/api/update/check`
    already skips calling `check()` - see `_status()` in `api/update.py`."""

    # `:stable` moves only on a release; five minutes is generous headroom
    # against that cadence while still being short enough that a
    # maintainer who has just refreshed the sidecar by hand sees the
    # warning clear within a handful of System-tab polls, not an hour of
    # a stale "behind" reading.
    _TTL = timedelta(minutes=5)

    def __init__(self) -> None:
        self._digest: str | None = None
        self._checked_at: datetime | None = None

    async def get(self, *, fetch: Fetch, fetch_headers: FetchHeaders, now: datetime) -> str | None:
        if self._checked_at is not None and (now - self._checked_at) < self._TTL:
            return self._digest
        self._digest = await resolve_updater_digest(fetch=fetch, fetch_headers=fetch_headers)
        self._checked_at = now
        return self._digest


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
