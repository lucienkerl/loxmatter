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

"""Tests for /api/update/*.

The routes glue `loxmatter.update` and `loxmatter.update_check` to HTTP
and do nothing else - accordingly these tests check exactly the glue
points: which status code follows from which module state.

Two groups below go beyond the router's own request/response shape:

- `_fetch`'s own unit tests (using `FakeSession`/`FakeResponse`, the same
  seam and shape as `tests/matter/test_otbr.py`'s `FakeSession`) prove
  that the one place this feature touches the network turns a bad HTTP
  status or an unparseable body into an exception `update_check.check()`
  already knows how to fold into a calm `error` field - NOT into
  `aiohttp`'s own `ClientResponseError`/`ContentTypeError`, which do not
  inherit from `OSError` and would otherwise sail straight past `check()`'s
  `except (OSError, KeyError, ValueError, TypeError)` as an unhandled 500.
- `test_a_rate_limited_github_response_becomes_a_calm_error_not_a_crash`
  wires `_fetch` and `update_check.check()` together the same way the
  `/check` route does, without a network, to prove the actual path stays
  inside that catch end to end - not just each half in isolation."""

from __future__ import annotations

import functools
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Self

import aiohttp
import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter import update_check
from loxmatter.api.update import _default_session_factory, _fetch, _fetch_headers
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store


def _heartbeat(update_dir, phase="idle", **fields):
    """Writes a `state.json` as the sidecar would - a fresh heartbeat plus
    whatever `fields` a test wants to override."""
    body = {
        "id": None,
        "phase": phase,
        "updater_seen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    body.update(fields)
    (update_dir / "state.json").write_text(json.dumps(body), encoding="utf-8")


@pytest.fixture
async def api(tmp_path, no_invoke, fake_runtime) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), update_dir=update_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        yield client, update_dir
    store.close()


async def test_without_a_sidecar_the_status_says_so_honestly(api):
    client, _ = api
    body = (await client.get("/api/update/status")).json()
    assert body["updater_present"] is False
    assert body["state"] is None


async def test_with_a_sidecar_the_status_reports_it(api):
    client, update_dir = api
    _heartbeat(update_dir)
    body = (await client.get("/api/update/status")).json()
    assert body["updater_present"] is True
    assert body["state"]["phase"] == "idle"
    # No `updater_version` given to `_heartbeat` here - the fixture
    # mirrors a state.json with no such key at all, the bootstrapping
    # case (a sidecar built before this field existed). The route must
    # pass that through as `null`, not omit the key or invent a value -
    # see `test_the_status_route_reports_the_sidecars_own_version` below
    # for the field actually being present.
    assert body["state"]["updater_version"] is None


async def test_the_status_route_reports_the_sidecars_own_version(api):
    # `_status()` (api/update.py) must forward `updater_version` from
    # `update.read_state` verbatim - the web UI's `updaterVersionBehind()`
    # (app.js) reads it straight off this JSON body, and a route that
    # silently dropped the field would leave that comparison permanently
    # blind despite the sidecar actually reporting a value.
    client, update_dir = api
    _heartbeat(update_dir, updater_version="0.3.2")
    body = (await client.get("/api/update/status")).json()
    assert body["state"]["updater_version"] == "0.3.2"


async def test_the_status_route_reports_the_sidecars_own_digest_and_stack_host_path(api):
    # `_status()` must forward both new fields from `update.read_state`
    # verbatim, the same as it already does for `updater_version` - the
    # web UI's revised `updaterVersionBehind()` (app.js) reads
    # `updater_digest` to decide whether to warn at all, and the "refresh
    # the updater" command is printed against `updater_stack_host_path`.
    client, update_dir = api
    _heartbeat(
        update_dir,
        updater_digest="sha256:" + "a" * 64,
        updater_stack_host_path="/home/pi/matter-loxone/deploy/testhost",
    )
    body = (await client.get("/api/update/status")).json()
    assert body["state"]["updater_digest"] == "sha256:" + "a" * 64
    assert body["state"]["updater_stack_host_path"] == "/home/pi/matter-loxone/deploy/testhost"


async def test_an_absent_sidecar_digest_and_stack_host_path_read_as_none(api):
    # A sidecar built before this change (or one that could not resolve
    # either fact - see update-once.sh's/entrypoint.sh's own comments)
    # writes no such key at all. The route must pass that through as
    # `null`, not omit the key.
    client, update_dir = api
    _heartbeat(update_dir)
    body = (await client.get("/api/update/status")).json()
    assert body["state"]["updater_digest"] is None
    assert body["state"]["updater_stack_host_path"] is None


async def test_the_status_route_reports_the_published_updater_digest(api, monkeypatch):
    """The bridge's own half of the digest comparison: `_status()` must
    resolve what GHCR currently serves the updater image under `:stable`
    and hand it back as `published_updater_digest` - wired through the
    real `update_check.resolve_updater_digest` (only the transport,
    `_fetch`/`_fetch_headers`, is faked), the same "wire the real pieces
    together, fake only the network" shape as
    `test_a_rate_limited_github_response_becomes_a_calm_error_not_a_crash`
    below."""
    import loxmatter.api.update as update_api

    async def fake_fetch(url: str) -> dict[str, Any]:
        assert "token?scope=repository:lucienkerl/loxmatter-updater:pull" in url
        return {"token": "t"}

    async def fake_fetch_headers(url: str, headers: dict[str, str]) -> dict[str, str]:
        assert "manifests/stable" in url
        assert headers["Authorization"] == "Bearer t"
        return {"Docker-Content-Digest": "sha256:" + "b" * 64}

    monkeypatch.setattr(update_api, "_fetch", fake_fetch)
    monkeypatch.setattr(update_api, "_fetch_headers", fake_fetch_headers)
    client, update_dir = api
    _heartbeat(update_dir)

    body = (await client.get("/api/update/status")).json()

    assert body["published_updater_digest"] == "sha256:" + "b" * 64


async def test_the_published_digest_check_can_be_switched_off(api, monkeypatch):
    """Same "an outbound call must be refusable" doctrine `/check` already
    follows for its own GitHub call (see `test_the_check_can_be_switched_off`
    below) - while checking is disabled, `/status` must not query GHCR at
    all, not merely discard the answer."""
    import loxmatter.api.update as update_api

    async def must_not_be_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not query GHCR while checking is disabled")

    monkeypatch.setattr(update_api, "_fetch", must_not_be_called)
    monkeypatch.setattr(update_api, "_fetch_headers", must_not_be_called)
    client, update_dir = api
    _heartbeat(update_dir)
    await client.patch("/api/update/settings", json={"check_enabled": False})

    body = (await client.get("/api/update/status")).json()

    assert body["published_updater_digest"] is None


async def test_a_broken_connection_to_ghcr_leaves_the_published_digest_unknown(api, monkeypatch):
    """GHCR being unreachable must not turn `/status` - polled every two
    seconds during a running update - into an error response, and must
    not be read as a mismatch either: `published_updater_digest` simply
    stays unknown, the same "no internet is not an error state" doctrine
    `update_check.check()` already follows."""
    import loxmatter.api.update as update_api

    async def broken_fetch(url: str) -> dict[str, Any]:
        raise OSError("Name or service not known")

    monkeypatch.setattr(update_api, "_fetch", broken_fetch)
    client, update_dir = api
    _heartbeat(update_dir)

    response = await client.get("/api/update/status")

    assert response.status_code == 200
    assert response.json()["published_updater_digest"] is None


async def test_the_published_digest_is_cached_across_polls(api, monkeypatch):
    """The whole point of `UpdaterDigestCache`: `/status` is polled every
    two seconds for the entire span of a running update - a second poll
    landing well inside the cache's TTL must not repeat the GHCR round
    trip."""
    import loxmatter.api.update as update_api

    calls: list[str] = []

    async def counting_fetch(url: str) -> dict[str, Any]:
        calls.append(url)
        return {"token": "t"}

    async def fake_fetch_headers(url: str, headers: dict[str, str]) -> dict[str, str]:
        return {"Docker-Content-Digest": "sha256:" + "c" * 64}

    monkeypatch.setattr(update_api, "_fetch", counting_fetch)
    monkeypatch.setattr(update_api, "_fetch_headers", fake_fetch_headers)
    client, update_dir = api
    _heartbeat(update_dir)

    await client.get("/api/update/status")
    await client.get("/api/update/status")

    assert len(calls) == 1, "the second poll within the cache TTL must not repeat the GHCR call"


async def test_a_corrupted_state_file_is_read_as_absent_not_as_a_crash(api):
    """The status route is polled every two seconds while the bridge
    itself is being replaced - `update.read_state` already folds a
    truncated file into `None` rather than raising (see its docstring);
    this proves the route does not add its own, more fragile handling on
    top of that guarantee."""
    client, update_dir = api
    (update_dir / "state.json").write_text("{not valid json", encoding="utf-8")
    response = await client.get("/api/update/status")
    assert response.status_code == 200
    assert response.json()["state"] is None
    assert response.json()["updater_present"] is False


async def test_a_state_directory_is_also_read_as_absent_not_as_a_crash(api):
    """The sibling case `test_a_corrupted_state_file_is_read_as_absent_not_as_a_crash`
    does not cover: `state.json` existing as a DIRECTORY instead of a file
    (mount weirdness, an operator mistake) raises `IsADirectoryError` from
    `Path.read_text()` - an `OSError` subclass already caught by
    `update.read_state`'s own `except (OSError, json.JSONDecodeError)` (see
    `tests/test_update_module.py::test_a_state_directory_instead_of_a_file_counts_as_no_state`
    for that unit-level pin). Nothing here was broken before this test was
    added - it closes the gap at the ROUTER boundary, proving `/status`
    itself does not layer its own, more fragile handling on top of
    `read_state`'s guarantee for this specific failure mode too."""
    client, update_dir = api
    (update_dir / "state.json").mkdir()
    response = await client.get("/api/update/status")
    assert response.status_code == 200
    assert response.json()["state"] is None
    assert response.json()["updater_present"] is False


async def test_without_a_sidecar_no_job_is_accepted(api):
    """503 and not 200: otherwise the bridge would write a job into a
    volume nobody reads, and the web UI would show progress that never
    begins."""
    client, _ = api
    response = await client.post("/api/update/apply", json={"target": "0.3.0"})
    assert response.status_code == 503


async def test_with_a_sidecar_a_job_is_accepted(api):
    client, update_dir = api
    _heartbeat(update_dir)
    response = await client.post("/api/update/apply", json={"target": "0.3.0"})
    assert response.status_code == 200
    assert response.json()["id"]
    assert json.loads((update_dir / "request.json").read_text(encoding="utf-8"))["target"] == (
        "0.3.0"
    )


async def test_the_channel_from_settings_lands_in_the_job(api):
    client, update_dir = api
    _heartbeat(update_dir)
    await client.patch("/api/update/settings", json={"channel": "dev"})
    await client.post("/api/update/apply", json={"target": "a3f91c2"})
    assert json.loads((update_dir / "request.json").read_text(encoding="utf-8"))["channel"] == (
        "dev"
    )


async def test_while_an_update_is_running_it_returns_409(api):
    client, update_dir = api
    _heartbeat(update_dir, phase="pull", id="laeuft")
    response = await client.post("/api/update/apply", json={"target": "0.3.0"})
    assert response.status_code == 409


async def test_a_request_not_yet_picked_up_by_the_sidecar_also_returns_409(api):
    """The SECOND, distinct busy condition (see `update.py`'s module
    docstring and `_pending_job_id`): between a request being written and
    the sidecar's next two-second poll, `state.json` still reports
    whatever end state preceded it - here, `idle`, as if nothing were
    running at all. A naive busy check that only reads `state.json`'s
    phase would wave a second request straight through and silently
    destroy the first one's `request.json`, with the first job's id -
    already handed back to whoever called first - never written anywhere
    else again. This must reach the client the same way the first
    condition does: 409, not 200."""
    client, update_dir = api
    _heartbeat(update_dir, phase="idle")
    (update_dir / "request.json").write_text(
        json.dumps({"id": "already-written", "channel": "stable", "target": "0.3.0"}),
        encoding="utf-8",
    )
    response = await client.post("/api/update/apply", json={"target": "0.4.0"})
    assert response.status_code == 409
    # The first request must survive untouched - overwriting it here would
    # be the exact data loss this test exists to catch.
    assert json.loads((update_dir / "request.json").read_text(encoding="utf-8"))["id"] == (
        "already-written"
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits do not apply on Windows")
async def test_an_unwritable_update_directory_returns_a_mapped_status_not_a_bare_500(api):
    """`update.request_update` does `mkdir`/`write_text`/`os.replace` with
    no handling of its own (that is correct - see its own docstring: file
    handling is Task 8's job, not this module's), and before this test the
    router caught only `UpdateBusyError` around that call. A read-only
    remount after an SD-card fault, or a full disk, are realistic failure
    modes on the Raspberry Pi this bridge targets - and unlike a busy
    sidecar or a missing one, this is neither the client's fault (409/503
    for those two already exist) nor recoverable by simply retrying the
    same request a moment later, so it must land on its own, distinct
    mapped status with a body the operator can act on, not the bare 500
    an unhandled `PermissionError` would otherwise produce."""
    client, update_dir = api
    _heartbeat(update_dir)
    os.chmod(update_dir, 0o500)  # read + execute only - no write, no create
    try:
        response = await client.post("/api/update/apply", json={"target": "0.3.0"})
    finally:
        os.chmod(update_dir, 0o700)  # tmp_path cleanup needs this back
    assert response.status_code == 503
    assert response.json()["detail"]


async def test_an_unknown_channel_is_refused(api):
    client, _ = api
    response = await client.patch("/api/update/settings", json={"channel": "beliebig"})
    assert response.status_code == 422


async def test_without_a_session_there_is_no_access(tmp_path, no_invoke, fake_runtime):
    store = Store(tmp_path / "t.sqlite")
    app = build_app(store, no_invoke, fake_runtime(store), update_dir=tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/update/status")).status_code == 401
        assert (await client.get("/api/update/check")).status_code == 401
        assert (await client.patch("/api/update/settings", json={})).status_code == 401
        assert (await client.post("/api/update/apply", json={"target": "0.3.0"})).status_code == (
            401
        )
    store.close()


async def test_the_check_can_be_switched_off(api):
    client, _ = api
    await client.patch("/api/update/settings", json={"check_enabled": False})
    body = (await client.get("/api/update/check")).json()
    assert body["target"] is None
    assert body["error"]
    assert body["checked_at"] is None


async def test_settings_can_be_read_back_through_status(api):
    """`PATCH /api/update/settings` answers with the same shape as
    `GET /api/update/status` (interface note in the brief) - this pins
    that both fields it can change actually land in THAT response, not
    just in some other, untested read path."""
    client, _ = api
    body = (await client.patch("/api/update/settings", json={"channel": "dev"})).json()
    assert body["channel"] == "dev"
    assert body["check_enabled"] is True  # default, untouched by this call


async def test_a_broken_connection_to_github_becomes_a_calm_error_not_a_crash(api, monkeypatch):
    """`update_check.check()` already turns a raised `OSError` into an
    `Available` with `error` set instead of propagating it. This proves
    the route's real fetcher is actually wired into that path - not
    bypassed by, say, an unguarded call sitting in front of it."""
    import loxmatter.api.update as update_api

    async def broken_fetch(url: str) -> dict[str, Any]:
        raise OSError("Name or service not known")

    monkeypatch.setattr(update_api, "_fetch", broken_fetch)
    client, _ = api

    response = await client.get("/api/update/check")

    assert response.status_code == 200
    body = response.json()
    assert body["target"] is None
    assert body["error"]


async def test_a_tampered_channel_row_returns_a_calm_error_not_a_crash(
    tmp_path, no_invoke, fake_runtime
):
    """Reproduces Minor 3: `update_check.check()` raises `ValueError(channel)`
    BEFORE its own `try` block (deliberately - see
    `tests/test_update_check.py::test_an_unknown_channel_is_refused`, which
    pins that a genuinely invalid channel is a caller bug `check()` itself
    should surface, not swallow), and `/check` calls it with nothing around
    it. Unreachable through the API today, because
    `UpdateSettingsStore.set_channel` is the only writer and already
    validates against the same two channels `check()` checks - but two
    hardcoded lists can drift, and the `setting` row itself is a plain
    SQLite value nothing stops a hand edit (or a future migration bug) from
    writing an unrecognized one into. This writes such a row directly,
    bypassing `set_channel`'s validation entirely, to prove the ROUTE - not
    `check()` - is what must not let that reach the web UI as a 500."""
    update_dir = tmp_path / "update"
    update_dir.mkdir()
    store = Store(tmp_path / "t.sqlite")
    store.update_settings._db.execute(
        "INSERT INTO setting (key, value) VALUES ('update.channel', 'nightly') "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    )
    store.update_settings._db.commit()
    app = build_app(store, no_invoke, fake_runtime(store), update_dir=update_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await authenticate(store, client)
        response = await client.get("/api/update/check")
    store.close()

    assert response.status_code == 200
    body = response.json()
    assert body["target"] is None
    assert body["error"]


class FakeResponse:
    """Stands in for `aiohttp.ClientResponse` - `status` and `text()`, the
    two members `_fetch` actually reads (same pattern as
    `tests/matter/test_otbr.py`'s `FakeResponse`). `text_exc`, if set, is
    raised from `text()` instead of returning `_body` - it stands in for a
    connection that answers with a status line and then dies before the
    body finishes arriving (`aiohttp.ClientPayloadError`, say)."""

    def __init__(self, status: int, body: str, text_exc: Exception | None = None) -> None:
        self.status = status
        self._body = body
        self._text_exc = text_exc

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def text(self) -> str:
        if self._text_exc is not None:
            raise self._text_exc
        return self._body


class FakeSession:
    """Stands in for `aiohttp.ClientSession` - only `get()` and `close()`,
    like `tests/matter/test_otbr.py`'s `FakeSession`."""

    def __init__(
        self, status: int = 200, body: str = "{}", text_exc: Exception | None = None
    ) -> None:
        self.status = status
        self.body = body
        self.text_exc = text_exc
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.closed = False
        self.raise_on_get: Exception | None = None

    def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        self.requests.append((url, headers or {}))
        if self.raise_on_get is not None:
            raise self.raise_on_get
        return FakeResponse(self.status, self.body, self.text_exc)

    async def close(self) -> None:
        self.closed = True


async def test_fetch_returns_the_parsed_body_on_a_normal_response():
    session = FakeSession(status=200, body='{"tag_name": "v0.3.0"}')

    result = await _fetch("https://api.github.com/x", session_factory=lambda: session)

    assert result == {"tag_name": "v0.3.0"}
    url, headers = session.requests[0]
    assert url == "https://api.github.com/x"
    assert headers["Accept"] == "application/vnd.github+json"


async def test_fetch_rejects_a_non_200_status_instead_of_returning_its_body():
    """A rate-limited GitHub answers 403 with a JSON body - if that body
    were returned as-is, `update_check.check()` would silently misread it
    as a malformed release/compare response instead of a network problem.
    Raising here (a plain `ValueError`, inside `check()`'s own `except`)
    is what lets the caller tell the two apart."""
    session = FakeSession(status=403, body='{"message": "API rate limit exceeded"}')

    with pytest.raises(ValueError, match="403"):
        await _fetch("https://api.github.com/x", session_factory=lambda: session)


async def test_fetch_rejects_a_body_that_is_not_json():
    """An HTML error page instead of JSON must not reach `check()` as a
    successful, garbage result."""
    session = FakeSession(status=200, body="<html>Not Found</html>")

    with pytest.raises(json.JSONDecodeError):
        await _fetch("https://api.github.com/x", session_factory=lambda: session)


async def test_fetch_rejects_json_that_is_not_an_object_or_a_list():
    """`Fetch`'s own contract is "a JSON object or a JSON list" - a bare
    number satisfies neither, and `check()`'s `isinstance(body, dict)`
    guards would otherwise silently treat it as "not what was expected"
    without this function ever having said why."""
    session = FakeSession(status=200, body="42")

    with pytest.raises(TypeError):
        await _fetch("https://api.github.com/x", session_factory=lambda: session)


async def test_fetch_closes_the_session_even_when_the_request_fails():
    session = FakeSession()
    session.raise_on_get = OSError("Netz weg")

    with pytest.raises(OSError):
        await _fetch("https://api.github.com/x", session_factory=lambda: session)

    assert session.closed


async def test_fetch_translates_a_dropped_connection_into_a_value_error():
    """Reproduces Important 1's first shape: `aiohttp.ServerDisconnectedError`
    (the connection drops after the request was sent, before a response
    ever comes back) is `ClientError` -> `ServerConnectionError` ->
    `ClientConnectionError` -> `ClientError` -> `Exception` - checked
    against the pinned aiohttp 3.14.3, NO `OSError` anywhere in that chain.
    Before the fix this sailed straight past `check()`'s
    `except (OSError, KeyError, ValueError, TypeError)`, unlike the plain
    `OSError` the sibling test above raises."""
    session = FakeSession()
    session.raise_on_get = aiohttp.ServerDisconnectedError("Server disconnected")

    with pytest.raises(ValueError, match="did not complete"):
        await _fetch("https://api.github.com/x", session_factory=lambda: session)

    assert session.closed


async def test_fetch_translates_a_truncated_body_into_a_value_error():
    """Reproduces Important 1's second shape: `aiohttp.ClientPayloadError`
    (the response starts - status and headers arrive fine - and then dies
    while the body is still being read, e.g. a truncated chunked
    transfer). Same `ClientError` -> `Exception` chain, same absence of
    `OSError`, but raised from `response.text()` instead of `session.get()`
    - proving the translation covers both places a mid-transfer death can
    surface, not just the one the sibling test above exercises."""
    session = FakeSession(status=200, body="", text_exc=aiohttp.ClientPayloadError("boom"))

    with pytest.raises(ValueError, match="did not complete"):
        await _fetch("https://api.github.com/x", session_factory=lambda: session)

    assert session.closed


class FakeHeaderResponse:
    """Stands in for `aiohttp.ClientResponse` for `_fetch_headers` tests -
    unlike `FakeResponse` above (which serves `_fetch`'s body-reading
    tests), what matters here is `.headers`, never a method call the way
    `.text()` is."""

    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeHeaderSession:
    """Stands in for `aiohttp.ClientSession` for `_fetch_headers` tests -
    same shape as `FakeSession` above, minus the body machinery
    `_fetch_headers` never touches."""

    def __init__(self, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.closed = False
        self.raise_on_get: Exception | None = None

    def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        self.requests.append((url, headers or {}))
        if self.raise_on_get is not None:
            raise self.raise_on_get
        return FakeHeaderResponse(self.status, self.headers)

    async def close(self) -> None:
        self.closed = True


async def test_fetch_headers_returns_the_response_headers():
    session = FakeHeaderSession(status=200, headers={"Docker-Content-Digest": "sha256:abc"})

    result = await _fetch_headers(
        "https://ghcr.io/v2/x/manifests/stable",
        {"Authorization": "Bearer t"},
        session_factory=lambda: session,
    )

    assert result["Docker-Content-Digest"] == "sha256:abc"
    url, headers = session.requests[0]
    assert url == "https://ghcr.io/v2/x/manifests/stable"
    assert headers["Authorization"] == "Bearer t"


async def test_fetch_headers_rejects_a_non_200_status():
    """A 401 (a bad/expired token) or 404 (no such tag) must not be read
    as "here are the headers of a manifest that answers for :stable"."""
    session = FakeHeaderSession(status=401)

    with pytest.raises(ValueError, match="401"):
        await _fetch_headers(
            "https://ghcr.io/v2/x/manifests/stable", {}, session_factory=lambda: session
        )


async def test_fetch_headers_closes_the_session_even_when_the_request_fails():
    session = FakeHeaderSession()
    session.raise_on_get = OSError("Netz weg")

    with pytest.raises(OSError):
        await _fetch_headers(
            "https://ghcr.io/v2/x/manifests/stable", {}, session_factory=lambda: session
        )

    assert session.closed


async def test_fetch_headers_translates_a_dropped_connection_into_a_value_error():
    """Same gap `_fetch`'s own sibling test closes - `ServerDisconnectedError`
    does not inherit from `OSError`, and `resolve_updater_digest()`'s catch
    tuple is written against the same shape `update_check.check()` uses."""
    session = FakeHeaderSession()
    session.raise_on_get = aiohttp.ServerDisconnectedError("Server disconnected")

    with pytest.raises(ValueError, match="did not complete"):
        await _fetch_headers(
            "https://ghcr.io/v2/x/manifests/stable", {}, session_factory=lambda: session
        )

    assert session.closed


async def test_the_default_session_factory_sets_a_short_timeout():
    """The web UI's own request to `/api/update/check` waits synchronously
    on this call - an unbounded timeout would let a hanging GitHub stall
    the whole System tab. `total <= 15` catches a mutation that widens or
    drops the timeout, without pinning the exact number."""
    session = _default_session_factory()
    try:
        assert session.timeout.total is not None
        assert session.timeout.total <= 15
    finally:
        # `aiohttp.ClientSession` needs a running event loop even to be
        # constructed (see the `RuntimeError` this test raised before it
        # became `async`) - and warns on an un-awaited close besides.
        await session.close()


async def test_a_rate_limited_github_response_becomes_a_calm_error_not_a_crash():
    """The scenario `_fetch`'s own docstring exists for, wired together the
    same way the `/check` route wires it: GitHub's real 403 rate-limit
    response carries a JSON body, but a status `raise_for_status()` would
    reject with `ClientResponseError` - which does NOT inherit from
    `OSError` and would otherwise fall straight through `check()`'s
    `except (OSError, KeyError, ValueError, TypeError)` as an unhandled
    500. This proves the combination stays inside that catch, end to
    end, without a network."""
    session = FakeSession(status=403, body='{"message": "API rate limit exceeded"}')
    fetch = functools.partial(_fetch, session_factory=lambda: session)

    result = await update_check.check(
        "stable", current_version="0.2.0", current_commit=None, fetch=fetch
    )

    assert result.target is None
    assert result.error


async def test_a_mid_transfer_failure_becomes_a_calm_error_not_a_crash():
    """The same end-to-end wiring as the rate-limit test above (real
    `_fetch`, real `update_check.check()`, only the HTTP transport faked),
    but for the failure Important 1 actually reports: a response that dies
    DURING the body read rather than arriving with a bad status. Before
    the fix, `_fetch` let `aiohttp.ClientPayloadError` propagate unchanged
    (see the module docstring's now-corrected claim that aiohttp's
    exceptions "already inherit from OSError") straight past `check()`'s
    catch tuple."""
    session = FakeSession(status=200, body="", text_exc=aiohttp.ClientPayloadError("boom"))
    fetch = functools.partial(_fetch, session_factory=lambda: session)

    result = await update_check.check(
        "stable", current_version="0.2.0", current_commit=None, fetch=fetch
    )

    assert result.target is None
    assert result.error


async def test_a_mid_transfer_failure_reaches_the_route_as_a_calm_error(api, monkeypatch):
    """Same scenario as `test_a_mid_transfer_failure_becomes_a_calm_error_not_a_crash`,
    but through the real `/api/update/check` route over HTTP - `_fetch` is
    monkeypatched only to inject the `FakeSession` in place of a real
    `aiohttp.ClientSession`, everything else (the route, `update_check.check()`,
    `_fetch`'s own translation logic) runs unmodified. Before the fix this
    is the exact request that returned a bare 500."""
    import loxmatter.api.update as update_api

    session = FakeSession(status=200, body="", text_exc=aiohttp.ClientPayloadError("boom"))
    monkeypatch.setattr(
        update_api, "_fetch", functools.partial(_fetch, session_factory=lambda: session)
    )
    client, _ = api

    response = await client.get("/api/update/check")

    assert response.status_code == 200
    body = response.json()
    assert body["target"] is None
    assert body["error"]


def test_the_interface_knows_every_text_of_the_update_card():
    """Task 9: the whole update card - all four states, the confirmation,
    and the disconnect banner's second text - reads exclusively through
    `t()`. This is the one check that the strings actually exist; whether
    each is wired to the right binding is covered by the markup/script
    tests in `tests/api/test_web.py` (`test_the_update_card_offers_its_
    four_states_and_the_confirmation`,
    `test_the_disconnect_banner_gets_a_different_text_during_an_update`,
    `test_the_update_polling_only_runs_while_a_job_is_in_progress`).

    `web.system.update_check_disabled` used to be listed here too, but was
    never referenced from anywhere in `app.js`/`index.html`: the disabled-
    check case already reaches the card through `updateAvailable.error`,
    itself carrying the backend's OWN `api.update.check_disabled` string
    (a different key, in a different namespace - set by `/api/update/check`
    in `api/update.py`). Listing the dead front-end key here would have
    kept it looking load-bearing forever; it has been dropped from
    `strings.yaml` along with this entry (Important/Minor review fix,
    2026-09-09).

    `web.system.update_confirm_schema` stays, unwired on purpose - see the
    comment at its definition in `strings.yaml` for why."""
    from loxmatter import i18n

    for key in (
        "web.system.update_available",
        "web.system.update_up_to_date",
        "web.system.update_apply",
        "web.system.update_cancel",
        "web.system.update_confirm_title",
        "web.system.update_confirm_downtime",
        "web.system.update_confirm_schema",
        "web.system.update_step_backup",
        "web.system.update_step_pull",
        "web.system.update_step_build",
        "web.system.update_step_recreate",
        "web.system.update_step_health",
        "web.system.update_restarting",
        "web.system.update_restarting_hint",
        "web.system.update_stalled",
        "web.system.update_not_collected",
        "web.system.update_done",
        "web.system.update_failed",
        "web.system.update_rolled_back",
        "web.system.update_rejected",
        "web.system.update_no_updater",
        "web.system.update_channel_stable",
        "web.system.update_channel_dev",
        "web.system.update_channel_dev_warning",
        "web.system.update_behind",
        "web.system.updater_behind",
        "web.system.updater_behind_unknown_path",
    ):
        assert i18n.raw_template(key), key
