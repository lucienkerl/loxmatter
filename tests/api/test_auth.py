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

"""Tests for the four access routes (Spec 8).

They are the only ones hanging under `/auth` outside the guard - they must
be reachable while signed out, or nobody could ever sign in.

`httpx.AsyncClient` carries its own cookie store: whatever `POST
/auth/login` sets, every further request from the same client sends along
on its own. That's exactly how the browser behaves too.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx2 as httpx
import pytest
from conftest import load_snapshot

from loxmatter.auth.passwords import MIN_PASSWORD_LENGTH, hash_password
from loxmatter.auth.throttle import FAILURES_BEFORE_THROTTLING
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store

PASSWORT = "ein-gutes-passwort"


class _NullSender:
    def send(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture
async def auth_client(
    tmp_path: Path, no_invoke: Any
) -> AsyncIterator[tuple[httpx.AsyncClient, Store]]:
    """An app with no password set - the state of initial setup."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    runtime = Runtime(store, _NullSender())
    app = build_app(store, no_invoke, runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store
    store.close()


async def test_auth_info_reports_an_unconfigured_service(auth_client):
    client, _ = auth_client
    response = await client.get("/auth-info")
    assert response.status_code == 200
    assert response.json() == {"password_set": False, "authenticated": False}


async def test_setup_sets_the_password_and_logs_in(auth_client):
    client, store = auth_client
    response = await client.post("/auth/setup", json={"password": PASSWORT})
    assert response.status_code == 200
    assert store.auth.password_hash() is not None
    assert (await client.get("/auth-info")).json() == {
        "password_set": True,
        "authenticated": True,
    }


async def test_setup_is_closed_for_good_once_a_password_is_set(auth_client):
    client, _ = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})
    second = await client.post("/auth/setup", json={"password": "ein-anderes-passwort"})
    assert second.status_code == 409
    assert second.json()["detail"] == (
        "A password has already been set for this service – initial setup is "
        "therefore permanently complete. Forgot the password? In the reference "
        "deployment, `docker compose exec loxmatter loxmatter set-password` resets "
        "it; for a source install, `uv run loxmatter set-password`."
    )


async def test_setup_is_closed_for_good_once_a_password_is_set_in_german(auth_client):
    """German companion test to
    test_setup_is_closed_for_good_once_a_password_is_set."""
    client, store = auth_client
    store.locale.set_language("de")
    await client.post("/auth/setup", json={"password": PASSWORT})
    second = await client.post("/auth/setup", json={"password": "ein-anderes-passwort"})
    assert second.status_code == 409
    assert second.json()["detail"] == (
        "Für diesen Dienst ist bereits ein Passwort vergeben – die Ersteinrichtung "
        "ist damit dauerhaft abgeschlossen. Passwort vergessen? Im Referenz-"
        "Deployment setzt `docker compose exec loxmatter loxmatter set-password` "
        "es neu; bei einer Installation aus dem Quellcode `uv run loxmatter "
        "set-password`."
    )


async def test_setup_does_not_hash_once_a_password_is_already_set(auth_client, monkeypatch):
    """Regression finding: `hash_password(body.password)` stood there as an
    argument and was therefore ALWAYS evaluated, even on a bridge that had
    long since been set up - 16 MiB scrypt, synchronous in the event loop,
    on every call to this unprotected route. The cheap check
    `password_hash() is not None` must apply BEFORE hashing, not just
    before writing."""
    client, _ = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})

    spy = Mock(name="hash_password")
    monkeypatch.setattr("loxmatter.api.auth.hash_password", spy)

    second = await client.post("/auth/setup", json={"password": "ein-anderes-passwort"})

    assert second.status_code == 409
    spy.assert_not_called()


async def test_setup_rejects_a_short_password(auth_client):
    client, store = auth_client
    response = await client.post("/auth/setup", json={"password": "kurz"})
    assert response.status_code == 422
    assert store.auth.password_hash() is None
    assert response.json()["detail"] == (
        f"The password must be at least {MIN_PASSWORD_LENGTH} characters long."
    )


async def test_setup_rejects_a_short_password_in_german(auth_client):
    """German companion test to test_setup_rejects_a_short_password."""
    client, store = auth_client
    store.locale.set_language("de")
    response = await client.post("/auth/setup", json={"password": "kurz"})
    assert response.status_code == 422
    assert store.auth.password_hash() is None
    assert response.json()["detail"] == (
        f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben."
    )


async def test_login_with_the_right_password_authenticates(auth_client):
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 200
    assert (await client.get("/auth-info")).json()["authenticated"] is True


async def test_login_with_a_wrong_password_is_rejected(auth_client):
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    response = await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Wrong password."
    assert (await client.get("/auth-info")).json()["authenticated"] is False


async def test_login_with_a_wrong_password_is_rejected_in_german(auth_client):
    """German companion test to test_login_with_a_wrong_password_is_rejected."""
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    store.locale.set_language("de")
    response = await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Falsches Passwort."
    assert (await client.get("/auth-info")).json()["authenticated"] is False


async def test_login_before_setup_says_so(auth_client):
    """409, not 401: there is no password this call could ever succeed
    with - retrying with credentials doesn't help."""
    client, _ = auth_client
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "No password has been set for this service yet – please complete initial setup first."
    )


async def test_login_before_setup_says_so_in_german(auth_client):
    """German companion test to test_login_before_setup_says_so."""
    client, store = auth_client
    store.locale.set_language("de")
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Für diesen Dienst ist noch kein Passwort vergeben – bitte zuerst die "
        "Ersteinrichtung abschließen."
    )


async def test_repeated_wrong_passwords_are_throttled(auth_client):
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    for _ in range(FAILURES_BEFORE_THROTTLING):
        await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail.startswith("Too many failed attempts")
    assert detail.endswith("seconds.")


async def test_repeated_wrong_passwords_are_throttled_in_german(auth_client):
    """German companion test to test_repeated_wrong_passwords_are_throttled."""
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    store.locale.set_language("de")
    for _ in range(FAILURES_BEFORE_THROTTLING):
        await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail.startswith("Zu viele Fehlversuche")
    assert detail.endswith("wieder möglich.")


async def test_setup_is_also_throttled_after_repeated_login_failures(auth_client):
    """Setup and login share the same LoginThrottle (see the `_client_id`
    docstring) - this therefore also covers the second call site of the
    429 message, not just the one in `/auth/login`."""
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    for _ in range(FAILURES_BEFORE_THROTTLING):
        await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    response = await client.post("/auth/setup", json={"password": PASSWORT})
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail.startswith("Too many failed attempts")
    assert detail.endswith("seconds.")


async def test_setup_is_also_throttled_after_repeated_login_failures_in_german(auth_client):
    """German companion test to
    test_setup_is_also_throttled_after_repeated_login_failures."""
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    store.locale.set_language("de")
    for _ in range(FAILURES_BEFORE_THROTTLING):
        await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    response = await client.post("/auth/setup", json={"password": PASSWORT})
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail.startswith("Zu viele Fehlversuche")
    assert detail.endswith("wieder möglich.")


async def test_concurrent_wrong_passwords_are_still_throttled(auth_client):
    """Regression finding (review, 2026-09-03): the `await` in
    `anyio.to_thread.run_sync` (formerly `run_in_threadpool`) interrupts the
    body of `/auth/login` at a point the earlier synchronous code didn't
    have. If the route only books the failed attempt AFTER this `await`,
    concurrent requests all get past `throttle.retry_after` before even one
    of them increments the counter - the throttling could be bypassed
    entirely through pure parallelism (measured before the fix: 60
    concurrent requests from one address produced 60 real rate attempts
    instead of at most `FAILURES_BEFORE_THROTTLING`). Noticeably more
    requests than `FAILURES_BEFORE_THROTTLING`, so a random slip-through of
    individual requests doesn't mask the test. `asyncio.gather` is enough
    here with no real threads: `client.post(...)` immediately produces a
    coroutine on every call below, `gather` starts all of them as their own
    tasks nearly simultaneously - and the body of `/auth/login` runs
    synchronously up to the first `await` (including the `retry_after`
    check), with no chance for the event loop to hand control to another
    request in between."""
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))

    attempts = FAILURES_BEFORE_THROTTLING * 4
    responses = await asyncio.gather(
        *(
            client.post("/auth/login", json={"password": "falsch-aber-lang"})
            for _ in range(attempts)
        )
    )
    statuses = [response.status_code for response in responses]

    # Exactly `FAILURES_BEFORE_THROTTLING` real password checks - everything
    # after that must be caught by the throttle with 429, no matter how
    # many requests arrived at the same time. `<=` would be toothless here:
    # the statement would still hold even if the route answered 429
    # without exception (0 is also <= `FAILURES_BEFORE_THROTTLING`). The
    # value is deterministic because the body of `/auth/login` runs
    # synchronously with respect to the event loop up to the booking - no
    # `await` sits in between, see the comment above.
    assert statuses.count(401) == FAILURES_BEFORE_THROTTLING
    assert statuses.count(429) == attempts - statuses.count(401)


async def test_concurrent_setup_attempts_are_still_throttled(auth_client, monkeypatch):
    """Regression finding (Finding 1, 2026-09-03): the same gap as in the
    test above, just in `/auth/setup` instead of `/auth/login` - before
    this fix, nothing there booked anything BEFORE the `await
    anyio.to_thread.run_sync(hash_password, ...)`, so any number of
    concurrent setup attempts got past `throttle.retry_after` before even
    one of them incremented the counter (measured before the fix: 20 of 20
    concurrent attempts against a not-yet-set-up bridge made it through to
    the 16 MiB hashing).

    Status codes alone don't reveal this here, unlike with login: the
    loser of a race for `set_password_hash_if_unset` also gets a 409 even
    though it hashed beforehand - so a 409 doesn't mean "was throttled".
    This test therefore counts the calls to `hash_password` directly via a
    monkeypatch, instead of relying on the status codes."""
    client, _ = auth_client

    # A list, not a counter: since the `CapacityLimiter`, `hash_password`
    # runs in up to four worker threads concurrently, and `int += 1` isn't
    # atomic in CPython - a lost increment would produce a sporadically red
    # `4 == 5` in CI. `list.append` is atomic and doesn't have this window.
    calls: list[None] = []
    original_hash_password = hash_password

    def counting_hash_password(password: str) -> str:
        calls.append(None)
        return original_hash_password(password)

    monkeypatch.setattr("loxmatter.api.auth.hash_password", counting_hash_password)

    attempts = FAILURES_BEFORE_THROTTLING * 4
    responses = await asyncio.gather(
        *(client.post("/auth/setup", json={"password": f"{PASSWORT}-{i}"}) for i in range(attempts))
    )

    # Exactly `FAILURES_BEFORE_THROTTLING` attempts made it through to
    # hashing - the same deterministic reason as with login: the body of
    # `/auth/setup` runs synchronously up to the booking.
    assert len(calls) == FAILURES_BEFORE_THROTTLING
    assert [response.status_code for response in responses].count(200) == 1


async def test_logout_ends_the_session_on_the_server(auth_client):
    """Not just clearing the cookie: the same value must no longer be valid
    afterward, or a stolen identifier keeps living on."""
    client, _ = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})
    session_id = client.cookies.get("loxmatter_session")
    assert session_id is not None

    await client.post("/auth/logout")
    assert (await client.get("/auth-info")).json()["authenticated"] is False

    client.cookies.set("loxmatter_session", session_id)
    assert (await client.get("/auth-info")).json()["authenticated"] is False


async def test_no_response_ever_contains_the_password_or_its_hash(auth_client):
    """Covers, besides the successful 200 responses, the four error
    branches too (401, 409, 422, 429, Finding D) - exactly where a value
    accidentally built in later ("Wrong password: <x>") would most likely
    end up, because an error text gets touched up by hand more often than
    a plain `{"status": "ok"}`."""
    client, store = auth_client
    responses = [await client.post("/auth/setup", json={"password": "kurz"})]  # 422
    assert responses[-1].status_code == 422

    setup_ok = await client.post("/auth/setup", json={"password": PASSWORT})
    assert setup_ok.status_code == 200
    responses.append(setup_ok)

    stored = store.auth.password_hash()
    assert stored is not None
    responses.append(await client.get("/auth-info"))

    # 409 on a bridge that has long since been set up - covers the error
    # branch, but SINCE FINDING 3 BELOW no longer counts as a failed
    # attempt for the throttle (there's nothing left to protect there, see
    # the comment on the 409 branch in `api/auth.py`).
    already_set_up = await client.post("/auth/setup", json={"password": "ein-anderes-passwort"})
    assert already_set_up.status_code == 409
    responses.append(already_set_up)

    wrong = await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    assert wrong.status_code == 401  # 1st (and so far only) failed attempt
    responses.append(wrong)

    # /auth/setup and /auth/login share the same LoginThrottle - one failed
    # attempt is already booked from the request above, here follow the
    # rest up to the throttle.
    for _ in range(FAILURES_BEFORE_THROTTLING - 1):
        responses.append(await client.post("/auth/login", json={"password": "falsch-aber-lang"}))

    throttled = await client.post("/auth/login", json={"password": PASSWORT})
    assert throttled.status_code == 429
    responses.append(throttled)

    for response in responses:
        assert PASSWORT not in response.text
        assert stored not in response.text
