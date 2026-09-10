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

"""Tests for the GitHub query - design "Applying updates through the web
UI" (2026-09-08), section 9.

The network layer is passed in as `fetch`: that way every test runs
without a network, and the "no internet" case is a test case instead of
a random occurrence in CI. A device without internet access is explicitly
NOT an error here - the last test pins that down."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from loxmatter.update_check import UpdaterDigestCache, check, resolve_updater_digest


async def test_the_stable_channel_reports_a_newer_release():
    async def fetch(url):
        assert "releases/latest" in url
        return {"tag_name": "v0.3.0", "name": "0.3.0", "body": "Fixes the restart hang."}

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target == "0.3.0"
    assert result.notes == "Fixes the restart hang."
    assert result.error is None


async def test_up_to_date_there_is_no_target():
    async def fetch(url):
        return {"tag_name": "v0.2.0", "name": "0.2.0", "body": ""}

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target is None


async def test_an_older_release_does_not_count_as_an_update():
    # Anyone running a development state newer than the latest release
    # should not be invited to downgrade - "forward only" does catch that
    # in the sidecar, but a button that gets reliably rejected is a
    # broken button.
    async def fetch(url):
        return {"tag_name": "v0.1.0", "name": "0.1.0", "body": ""}

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target is None


async def test_the_development_channel_counts_the_commits():
    async def fetch(url):
        assert "compare" in url
        return {
            "ahead_by": 14,
            "commits": [
                {
                    "commit": {"message": "fix: one\n\nmore"},
                    "sha": "1111111111111111111111111111111111111111",
                },
                {
                    "commit": {"message": "feat: two"},
                    "sha": "2222222222222222222222222222222222222222",
                },
            ],
        }

    result = await check("dev", current_version="dev", current_commit="a3f91c2", fetch=fetch)
    assert result.behind == 14
    assert "fix: one" in result.notes
    assert "more" not in result.notes, "only the subject line, not the full body"


async def test_the_development_channel_targets_the_tip_commit_not_the_branch_name():
    # `target="main"` is what update-once.sh's Rule 1 (`^[0-9a-f]{7,40}$`)
    # rejects on every single click - this is the fix for that. GitHub's
    # `compare` endpoint lists commits oldest-first, so the actual tip of
    # `main` is the LAST element, not the first - and the target must be
    # the full 40-character SHA, not a truncated one (see the comment at
    # this fix in update_check.py for why the length matters).
    async def fetch(url):
        return {
            "ahead_by": 2,
            "commits": [
                {"commit": {"message": "older, not the target"}, "sha": "a" * 40},
                {"commit": {"message": "main's actual tip"}, "sha": "b" * 40},
            ],
        }

    result = await check("dev", current_version="dev", current_commit="a3f91c2", fetch=fetch)
    assert result.target == "b" * 40
    assert result.target != "main"


async def test_the_development_channel_reports_an_error_if_commits_is_empty_but_ahead_by_is_not():
    # A defensive case, not one GitHub is known to actually produce: if
    # `ahead_by` and `commits` ever disagreed, `commits[-1]` must not
    # raise past `check()` and reach the web UI as an unhandled exception -
    # it must come back as the same kind of honest `error` every other
    # malformed GitHub response gets.
    async def fetch(url):
        return {"ahead_by": 1, "commits": []}

    result = await check("dev", current_version="dev", current_commit="a3f91c2", fetch=fetch)
    assert result.target is None
    assert result.error


async def test_the_development_channel_without_a_known_commit_reports_nothing():
    async def fetch(url):
        raise AssertionError("must not be queried without a commit")

    result = await check("dev", current_version="dev", current_commit=None, fetch=fetch)
    assert result.target is None
    assert result.error


async def test_without_internet_it_is_not_an_error_state():
    async def fetch(url):
        raise OSError("Name or service not known")

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target is None
    assert result.error
    assert result.checked_at


async def test_an_unknown_channel_is_refused():
    async def fetch(url):
        raise AssertionError("must not be queried")

    with pytest.raises(ValueError):
        await check("beliebig", current_version="0.2.0", current_commit=None, fetch=fetch)


async def test_a_release_with_an_empty_body_still_renders():
    # An empty changelog is a valid GitHub answer (a maintainer publishes
    # a release before writing notes), not a malformed one - it must not
    # be treated the same as a missing/unparseable field.
    async def fetch(url):
        return {"tag_name": "v0.3.0", "name": "0.3.0", "body": ""}

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target == "0.3.0"
    assert result.notes == ""
    assert result.error is None


async def test_a_tag_that_is_not_a_plain_version_is_reported_not_crashed():
    async def fetch(url):
        return {"tag_name": "v0.3.0-rc1", "name": "0.3.0-rc1", "body": ""}

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target is None
    assert result.error


async def test_a_rate_limited_release_response_is_reported_not_crashed():
    # GitHub's rate-limit body carries neither `tag_name` nor any of the
    # other fields this module reads.
    async def fetch(url):
        return {
            "message": "API rate limit exceeded",
            "documentation_url": "https://docs.github.com/",
        }

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target is None
    assert result.error


async def test_a_compare_response_missing_ahead_by_is_reported_not_mistaken_for_up_to_date():
    async def fetch(url):
        return {"commits": []}

    result = await check("dev", current_version="dev", current_commit="a3f91c2", fetch=fetch)
    assert result.target is None
    assert result.error, "a missing field is not evidence of 'no commits ahead'"


async def test_an_array_response_is_reported_not_crashed():
    # Neither endpoint used here is documented to ever answer with a JSON
    # array, but `fetch`'s declared type allows one - this pins down that
    # `check()` does not blindly call dict methods on it.
    async def fetch(url):
        return []

    result = await check("stable", current_version="0.2.0", current_commit=None, fetch=fetch)
    assert result.target is None
    assert result.error


async def test_dev_channel_up_to_date_there_is_no_target():
    # The dev channel guard `if ahead <= 0: return unavailable()` enforces
    # the "forward only" safety property - anyone already on main, or ahead
    # of it on a merged branch, must never be offered main as an update.
    async def fetch(url):
        return {"ahead_by": 0, "commits": []}

    result = await check("dev", current_version="dev", current_commit="a3f91c2", fetch=fetch)
    assert result.target is None
    assert result.error is None


async def test_resolve_updater_digest_reads_the_docker_content_digest_header():
    # The exact, verified-working flow from this feature's own design
    # brief: an anonymous token exchange first, then the manifest request
    # bearing it - the answer is the RESPONSE HEADER, not anything in the
    # body.
    async def fetch(url):
        assert url == (
            "https://ghcr.io/token?scope=repository:lucienkerl/loxmatter-updater:pull"
            "&service=ghcr.io"
        )
        return {"token": "the-token"}

    async def fetch_headers(url, headers):
        assert url == "https://ghcr.io/v2/lucienkerl/loxmatter-updater/manifests/stable"
        assert headers["Authorization"] == "Bearer the-token"
        assert "oci.image.index" in headers["Accept"]
        return {"Docker-Content-Digest": "sha256:" + "a" * 64}

    digest = await resolve_updater_digest(fetch=fetch, fetch_headers=fetch_headers)
    assert digest == "sha256:" + "a" * 64


async def test_resolve_updater_digest_is_none_without_a_usable_token():
    # A token response missing its own "token" field (or answering
    # something other than a JSON object entirely) leaves nothing to bear
    # to the manifest request - that second call must never be attempted
    # on an empty/forged Authorization header.
    async def fetch(url):
        return {"message": "denied"}

    async def fetch_headers(url, headers):
        raise AssertionError("must not be queried without a real token")

    assert await resolve_updater_digest(fetch=fetch, fetch_headers=fetch_headers) is None


async def test_resolve_updater_digest_is_none_when_the_header_is_missing():
    # A 200 with no Docker-Content-Digest header at all (an unexpected
    # registry response shape) is exactly as unknown as a failed request -
    # never treated as "digest is the empty string" or similar.
    async def fetch(url):
        return {"token": "t"}

    async def fetch_headers(url, headers):
        return {}

    assert await resolve_updater_digest(fetch=fetch, fetch_headers=fetch_headers) is None


async def test_resolve_updater_digest_is_none_on_a_network_error():
    # No internet is not an error state here either - same doctrine as
    # `check()` above, just with `None` standing in for `Available.error`
    # since this function has no error field of its own to carry one (see
    # its own docstring: the caller shows nothing at all).
    async def fetch(url):
        raise OSError("Name or service not known")

    async def fetch_headers(url, headers):
        raise AssertionError("must not be reached without a token")

    assert await resolve_updater_digest(fetch=fetch, fetch_headers=fetch_headers) is None


async def test_resolve_updater_digest_is_none_when_the_manifest_call_fails():
    # The token call can succeed while the manifest call itself fails (a
    # rate limit, a transient GHCR error) - that failure must not
    # propagate either.
    async def fetch(url):
        return {"token": "t"}

    async def fetch_headers(url, headers):
        raise ValueError("GHCR answered https://ghcr.io/... with HTTP 401.")

    assert await resolve_updater_digest(fetch=fetch, fetch_headers=fetch_headers) is None


async def test_the_digest_cache_reuses_a_recent_answer():
    # `/api/update/status` is polled every two seconds for the entire span
    # of a running update - without this cache, every single poll would
    # repeat the GHCR round trip. Two calls within the TTL must reach the
    # network exactly once.
    calls = []

    async def fetch(url):
        calls.append(url)
        return {"token": "t"}

    async def fetch_headers(url, headers):
        return {"Docker-Content-Digest": "sha256:" + "b" * 64}

    cache = UpdaterDigestCache()
    now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
    first = await cache.get(fetch=fetch, fetch_headers=fetch_headers, now=now)
    second = await cache.get(
        fetch=fetch, fetch_headers=fetch_headers, now=now + timedelta(seconds=2)
    )

    assert first == second == "sha256:" + "b" * 64
    assert len(calls) == 1, "a poll inside the TTL must not repeat the GHCR call"


async def test_the_digest_cache_refreshes_after_the_ttl():
    calls = []

    async def fetch(url):
        calls.append(url)
        return {"token": "t"}

    async def fetch_headers(url, headers):
        return {"Docker-Content-Digest": "sha256:" + "c" * 64}

    cache = UpdaterDigestCache()
    now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
    await cache.get(fetch=fetch, fetch_headers=fetch_headers, now=now)
    await cache.get(fetch=fetch, fetch_headers=fetch_headers, now=now + timedelta(minutes=10))

    assert len(calls) == 2, "a poll past the TTL must refresh, not keep serving a stale answer"


async def test_equal_versions_under_different_component_counts():
    # A release tagged v0.3.0 and a running version 0.3 are the same release
    # written two ways. Zero-padding the shorter tuple before comparing
    # ensures they sort as equal, not as "0.3 < 0.3.0" - the padding
    # defends against tagging inconsistency that the maintainer might
    # introduce later, rather than resting on current discipline.
    async def fetch(url):
        return {"tag_name": "v0.3.0", "name": "0.3.0", "body": ""}

    result = await check("stable", current_version="0.3", current_commit=None, fetch=fetch)
    assert result.target is None, "v0.3.0 and 0.3 are the same release"
    assert result.error is None
