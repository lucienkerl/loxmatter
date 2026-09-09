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

"""The four access routes: `/auth-info`, `/auth/setup`, `/auth/login`,
`/auth/logout` (Spec 8).

**These are the only routes that do NOT hang behind `build_api_guard`** -
they must be reachable while logged out, otherwise no one could log in.
`loxone.server.build_app` therefore deliberately wires them in without
`dependencies=api_guard`, alongside `/health`.

What they therefore do NOT deliver: anything about the state of the
bridge. `/auth-info` states exactly two booleans - whether a password is
set and whether THIS caller is logged in. A caller learns both anyway from
how `/api/devices` responds to it; it is stated here only so the UI does
not have to guess which screen to show.

**No secret leaves this module.** Neither password nor hash nor session id
appears in a response (the id travels exclusively in the `Set-Cookie`) or
in a log - in any branch.
"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from loxmatter import i18n
from loxmatter.auth.passwords import MIN_PASSWORD_LENGTH, hash_password, verify_password
from loxmatter.auth.sessions import (
    SESSION_COOKIE,
    SESSION_LIFETIME_SECONDS,
    open_session,
    session_is_valid,
)
from loxmatter.auth.throttle import LoginThrottle
from loxmatter.model.store import Store


class PasswordIn(BaseModel):
    password: str


class AuthInfoOut(BaseModel):
    password_set: bool
    authenticated: bool


class StatusOut(BaseModel):
    status: str


# A dedicated, small limiter instead of `starlette.concurrency.run_in_threadpool`
# (which uses anyio's default limiter of 40 concurrent threads and does not
# pass one of its own through - hence `anyio.to_thread.run_sync` directly
# here with `limiter=`). `hash_password`/`verify_password` compute scrypt
# with 16 MiB per running computation (`auth.passwords`); 4 threads therefore
# give 4 * 16 MiB = 64 MiB as the upper bound of what an UNAUTHENTICATED
# caller can tie up through concurrent login or setup attempts alone -
# versus 40 * 16 MiB = 640 MiB with the default value. Measured: an RSS
# peak of 476 MiB with 40 concurrent logins over the default thread pool,
# versus 28 MiB before (scrypt run serially in the event loop, see the
# `verify_password` call below). The target device is a Raspberry Pi, on
# which, per `deploy/testhost/docker-compose.yml`, `matter-server` and
# `otbr` also share the same memory - a repeated spike of this magnitude,
# triggered from outside without any login, is a realistic OOM trigger
# there. The upper bound deliberately applies independently of
# `LoginThrottle`: that only slows down REPEATED requests from ONE address,
# not many CONCURRENT requests from different addresses.
#
# Created here at module level, i.e. OUTSIDE any event loop - this only
# works because AnyIO provides an adapter for it that binds to whichever
# loop is running on first use and rebinds on a change (uvicorn has a
# single loop for this, the test suite has many - both work as a result).
# Anyone who later moves this constant into a function should know this,
# rather than wondering why it still works anyway.
_PASSWORD_HASH_LIMITER = anyio.CapacityLimiter(4)


# The 409 text in `api.auth.fail_already_set_up` (`i18n/strings.yaml`)
# deliberately names BOTH recovery paths, not just one: the reference
# deployment (`deploy/testhost/docker-compose.yml`) puts the database in a
# named Docker volume that is only reachable INSIDE the container under
# `LOXMATTER_STORE` - `uv run loxmatter set-password` on the host, lacking
# that environment variable, hits a different, newly created file there and
# falsely reports success without actually unlocking the bridge (escape-hatch
# finding, 2026-09-03). README.md and the release note describe the same
# path and must not drift apart from this text.
def build_auth_router(store: Store) -> APIRouter:
    router = APIRouter()
    # One instance per app, not per request - otherwise it would count nothing.
    throttle = LoginThrottle()

    def _require_length(password: str) -> None:
        """A dedicated check instead of `Field(min_length=...)` on the
        model: the message ends up in the UI and should be shown there in
        the configured language and say what to do - not as a pydantic
        error list."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=i18n.t("api.auth.fail_password_too_short", min_length=MIN_PASSWORD_LENGTH),
            )

    def _client_id(request: Request) -> str:
        """The connection's peer address, NOT `X-Forwarded-For`: that header
        is set by every caller itself, and the throttling could be bypassed
        by claiming a different address per attempt. Shared by `/auth/login`
        and `/auth/setup`, which have shared this id ever since both were
        wired to the same `LoginThrottle`."""
        return request.client.host if request.client is not None else "unknown"

    def _start_session(response: Response) -> None:
        """Creates a session and attaches the cookie to the response.

        `secure` is DELIBERATELY missing here and must not be added "for
        security's sake": this service speaks HTTP (Spec 14.1), a `Secure`
        cookie would be discarded by the browser and no one could get in
        any more. `samesite="strict"` is at the same time the CSRF
        protection - a foreign site cannot trigger a state-changing request
        in a logged-in session with it, which is why there is no separate
        CSRF token."""
        response.set_cookie(
            SESSION_COOKIE,
            open_session(store.auth),
            max_age=SESSION_LIFETIME_SECONDS,
            httponly=True,
            samesite="strict",
            path="/",
        )

    @router.get("/auth-info")
    async def auth_info(request: Request) -> AuthInfoOut:
        session_id = request.cookies.get(SESSION_COOKIE)
        return AuthInfoOut(
            password_set=store.auth.password_hash() is not None,
            authenticated=(session_id is not None and session_is_valid(store.auth, session_id)),
        )

    @router.post("/auth/setup")
    async def setup(body: PasswordIn, request: Request, response: Response) -> StatusOut:
        """Initial setup - without further proof as long as no password is
        set (Spec 5, trust on first use).

        This is a deliberately made trade-off, not a forgotten check:
        between the start without a password and this assignment, anyone
        who can reach the service can take it over. Decided on
        September 3, 2026 against a setup code in the log, a time window,
        and an initial password via CLI, so that setup remains possible
        headless via the UI - and explicitly also for an existing system
        with an already configured token, which is NOT additionally asked
        for here.

        This route hangs (like the three others in this module) WITHOUT a
        guard and without login - which is why the next three points
        matter more here than elsewhere:

        1. The cheap check `password_hash() is not None` sits BEFORE the
           hashing, not after. `hash_password` computes scrypt with 16 MiB
           of memory - a double-digit millisecond amount on a Raspberry
           Pi, AND SYNCHRONOUSLY in the event loop (see point 2). Anyone
           reaching this route could otherwise, on a bridge that has long
           since been set up, saturate the event loop with a single
           connection firing continuously and thereby also slow down
           `/cmd`/`/resync` - exactly the two routes this design explicitly
           wants to keep always reachable. `set_password_hash_if_unset`
           still stays in place regardless: it is the only safeguard
           against the tiny remaining race between this check and the
           actual write (two concurrent FIRST setups), but normally costs
           nothing more, because it is then never reached at all.
        2. `hash_password` runs via `anyio.to_thread.run_sync` in its own
           thread limiter bounded to 4 (`_PASSWORD_HASH_LIMITER` above) -
           not via `starlette.concurrency.run_in_threadpool` and its anyio
           default limiter of 40 threads, which does not pass a custom
           value through. `hashlib.scrypt` never returns control to the
           event loop; without a thread, EVERY concurrent request - even
           to `/cmd`, `/resync` and every other route of this process -
           would stall for the duration of the computation. The tight
           limiter additionally bounds how many of these 16 MiB
           computations can sit in memory at once (review finding,
           2026-09-03, see the comment on `_PASSWORD_HASH_LIMITER`).
        3. The failed attempt is booked optimistically HERE, BEFORE the
           `await anyio.to_thread.run_sync` - exactly as in `/auth/login`
           below, whose comment on the booking carries the full rationale
           and the measurement (60 instead of at most
           `FAILURES_BEFORE_THROTTLING` real rate-limited attempts without
           bringing it forward). The same `LoginThrottle` thereby bounds
           how often hashing can even be reached in a short time - the
           case point 1 does not cover: a bridge whose initial setup has
           never taken place.
        """
        client = _client_id(request)
        wait = throttle.retry_after(client)
        if wait:
            raise HTTPException(
                status_code=429,
                detail=i18n.t("api.auth.fail_too_many_attempts", wait=wait),
            )
        _require_length(body.password)
        if store.auth.password_hash() is not None:
            # NO `record_failure` here (review finding, 2026-09-03): since
            # the cheap check above in point 1, this branch is no longer a
            # failed attempt against a secret but merely an indexed SELECT
            # - there is nothing left to protect here. Before, it still
            # counted and shared the counter with `/auth/login`: a UI that
            # is incorrectly still showing the setup screen (e.g. because
            # `/auth-info` failed on load) would lock out an operator who
            # clicks "set password" five times, out of LOGIN as well - even
            # though they never entered a wrong password. The throttling
            # *check* above remains unchanged.
            raise HTTPException(status_code=409, detail=i18n.t("api.auth.fail_already_set_up"))
        throttle.record_failure(client)
        hashed = await anyio.to_thread.run_sync(
            hash_password, body.password, limiter=_PASSWORD_HASH_LIMITER
        )
        if not store.auth.set_password_hash_if_unset(hashed):
            # The race from point 1 above: between the check and this
            # write, another, concurrent setup won. NO second
            # `record_failure` here (review finding, 2026-09-03): the
            # failed attempt is already booked before the `await` above -
            # another one here would be a double booking for the same
            # attempt.
            raise HTTPException(status_code=409, detail=i18n.t("api.auth.fail_already_set_up"))
        throttle.record_success(client)
        _start_session(response)
        return StatusOut(status="ok")

    @router.post("/auth/login")
    async def login(body: PasswordIn, request: Request, response: Response) -> StatusOut:
        client = _client_id(request)
        wait = throttle.retry_after(client)
        if wait:
            raise HTTPException(
                status_code=429,
                detail=i18n.t("api.auth.fail_too_many_attempts", wait=wait),
            )
        stored = store.auth.password_hash()
        if stored is None:
            # 409, not 401: there is no password this call could succeed
            # against - a retry with credentials does not help (the same
            # distinction as in RFC 9110).
            raise HTTPException(
                status_code=409,
                detail=i18n.t("api.auth.fail_no_password_set"),
            )
        # Via the thread limiter like `hash_password` above in `setup`:
        # `hashlib.scrypt` blocks the event loop synchronously, and this
        # route hangs without a guard just like `/auth/setup` (see module
        # docstring) - a login must not slow down `/cmd`/`/resync` any more
        # than a setup attempt may.
        #
        # The failed attempt is booked optimistically HERE, BEFORE the
        # `await`, not only after its result. Reason: at this point
        # `anyio.to_thread.run_sync` returns control to the event loop, and
        # during that time an arbitrary number of concurrent requests from
        # the SAME address can likewise get past `throttle.retry_after`
        # above before even one of them has incremented the counter - a
        # check-then-act window that the earlier synchronous code did not
        # have (there the body ran atomically with respect to the loop).
        # Measured: 60 concurrent wrong logins from one address produced 60
        # instead of the intended `FAILURES_BEFORE_THROTTLING` real rate
        # attempts without this bringing-forward - the throttling could be
        # bypassed entirely through pure parallelism. On success,
        # `record_success` immediately brings the counter back down again,
        # so the sequential behaviour (five failed attempts, then a lock)
        # remains unchanged.
        #
        # Two properties of this advance booking are intentional but not
        # obvious: if the request is aborted during the `await` (connection
        # drop, cancelled task, a scrypt error under load), no one reverses
        # the booking - an operator can thereby move towards the lockout
        # without ever entering a wrong password. For a security counter
        # that is the right direction (when in doubt, count rather than
        # forget). And a SUCCESSFUL fifth attempt also briefly sets a lock
        # for the duration of the scrypt run, which `record_success` lifts
        # again itself right afterwards.
        throttle.record_failure(client)
        if not await anyio.to_thread.run_sync(
            verify_password, body.password, stored, limiter=_PASSWORD_HASH_LIMITER
        ):
            raise HTTPException(status_code=401, detail=i18n.t("api.auth.fail_wrong_password"))
        throttle.record_success(client)
        _start_session(response)
        return StatusOut(status="ok")

    @router.post("/auth/logout")
    async def logout(request: Request, response: Response) -> StatusOut:
        """Ends the session SERVER-SIDE and then clears the cookie.

        The order is the point: a logout that only deletes the cookie
        leaves an already-leaked id alive for thirty more days."""
        session_id = request.cookies.get(SESSION_COOKIE)
        if session_id is not None:
            store.auth.delete_session(session_id)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return StatusOut(status="ok")

    return router
