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
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Self

import aiohttp
import httpx2 as httpx
import pytest
from conftest import authenticate

from loxmatter import update_check
from loxmatter.api.update import _default_session_factory, _fetch
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
