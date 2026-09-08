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

"""Accepts the HTTP calls of the virtual outputs - and, since task 2
(phase 5), also those of the WebUI.

The Miniserver does not evaluate a virtual output's response - it fires
and forgets. The status codes of the Loxone routes below are therefore
not for Loxone, but for the human who checks the log to see why a block
has no effect. Accordingly, they must be distinguishable: 404 for an
unknown key, 400 for an unsuitable value, 502 for a device that does not
respond.

`client` is new compared to phase 4: the WebUI routes under `/api` need
`BridgeMatterClient` for commissioning and removing devices (task 1), the
Loxone routes here do not need it. The parameter is therefore optional and
defaults to `None` - precisely so that the three existing phase-4 calls of
`build_app(store, invoke, runtime)` keep running unchanged. `None` does not
mean "WebUI missing"; it means "the bridge is running without a Matter
connection" - `build_device_router` then answers the two routes that need
`client` (commissioning, removal) with 503 instead of an `AttributeError`
on `None` (see there).

`sender` and `matter_data_dir` are new in task 6 (diagnostics, spec 10.5),
optional with default `None` for the same reason: the diagnostics routes
need them (recording of sent datagrams, backing up the fabric
credentials), the other routes in this file do not. `cli.py`'s `_run` now
passes both through; any older call without them keeps running unchanged,
just without these two diagnostic capabilities (see
`api.diagnostics.build_diagnostics_router`, which also explains there what
`None` concretely means for each of the two cases).

**`log_handler` is new in task 4 of this phase (diagnostics livestream,
spec 10.5).** Optional with default `None` for the same reason: not every
caller has already called
`diagnostics.logbuffer.install_log_buffer()`. `cli.py`'s `run()` now does
that (task 5, phase 5; since the task 7, fix 1 follow-up as its very first
instruction, BEFORE `_run()`) and passes the resulting handler through to
`_run()` as a parameter, which forwards it unchanged here to `build_app()`
- a caller that uses `build_app` directly (e.g. a test) still gets `None`
unless it calls `install_log_buffer()` itself and passes it through.
`None` here means "no log branch in the livestream", not "the livestream
as a whole is missing" - the WebSocket route `/api/diagnostics/live`
(below, `build_diagnostics_live_router`) still responds, just without log
lines in it (see there).

**`api_token` is new in task 8 (hardening, spec 9).** Up to this point,
this service offered only `/cmd` and `/resync` - being reachable meant, at
most, being able to switch a device. Since task 1 (commissioning) and task
2 (removal), it means more: whoever reaches the port can throw a device
out of the fabric, and since task 6 can additionally download the entire
fabric backup (`GET /api/diagnostics/fabric-backup`, spec 4.1).
`build_api_guard` (see there) therefore protects every route under `/api`
from here on - both reading AND writing, because a pure write lock would
have left read access to signal values and the fabric backup itself open,
and that is precisely the real risk. `/cmd` and `/resync` are deliberately
left out: the Miniserver calls virtual outputs without a header, and a
token there would simply switch off the Loxone integration. `api_token` is
therefore optional with default `None` - the same reason as for
`client`/`sender` above: every existing call without the argument keeps
running unchanged, just without the token path in the guard. Since the
WebUI login (docs/superpowers/specs/2026-09-03-webui-login-design.md,
section 4) there are two proofs instead of one, and no more open state:
`None` no longer means "this `/api` route is unguarded", only "no bearer
token accepted" - the logged-in session (password, cookie
`loxmatter_session`) remains the browser's path and covers normal
operation; if no password has been set at all, the route still answers
with 401, not openly (see `build_api_guard`, discussed there at length).
The warning in the log has since concerned the missing password, no
longer the missing token (see `cli._warn_if_no_password`).

**Command log (spec 10.5).** The `_record_command` middleware below
records EVERY incoming HTTP call on this app - method, path, status code,
timestamp - for `GET /api/diagnostics/commands`. Two deliberate
restrictions, both explained at greater length in `api.diagnostics`'s
module docstring:

- Calls under `/api/diagnostics/*` itself are NOT recorded - otherwise a
  diagnostics tab left open and polling would flood the limited ring
  buffer with itself instead of with the actually interesting `/cmd`
  calls.
- Only `request.url.path` is recorded, NEVER the query string - a
  `/cmd/{key}/{value}` call deliberately puts its value in the path (that
  is the purpose of this log), whereas a query string is not intended for
  any route today and is only carried along as a precaution: task 8's
  token deliberately does NOT travel as a query parameter, but as an
  `Authorization` header or (for the browser WebSocket) as a subprotocol -
  precisely so that it does not end up in this log (see
  `build_api_guard`).

The middleware wraps the append to the ring buffer itself
(`_append_command_log`) in its own try/except - a failure while recording
must never prevent the actual response, the same rule as for the datagram
recording in `loxone.sender.UdpSender._record_sent`. This is something
different from the call to `call_next`: that one HAS, since the review fix
important (2026-09-02), been inside a try/except, because an unhandled
exception from a route should still appear in the command log - noted with
`_CRASHED_STATUS` - before being re-raised unchanged (see
`_record_command`). Previously, a crashing route left `call_next` before
the try/except was ever reached - the very call that brings the service
down was therefore missing exactly where a diagnostician needs it most."""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import HTTPConnection
from starlette.responses import Response as StarletteResponse

from loxmatter import i18n
from loxmatter.api.auth import build_auth_router
from loxmatter.api.control import build_control_router
from loxmatter.api.devices import RuntimeValues, ThreadDatasetSource, build_device_router
from loxmatter.api.diagnostics import (
    CommandLogEntry,
    RingBuffer,
    build_diagnostics_router,
)
from loxmatter.api.diagnostics_live import build_diagnostics_live_router
from loxmatter.api.export import build_export_router
from loxmatter.api.language import build_i18n_router, build_language_router
from loxmatter.api.live import BEARER_SUBPROTOCOL, ObservableRuntime, build_live_router
from loxmatter.api.project_sync import build_project_sync_router
from loxmatter.api.settings import build_settings_router
from loxmatter.auth.sessions import SESSION_COOKIE, session_is_valid
from loxmatter.commands.translate import MatterCall, UnsupportedValueError, to_matter_call
from loxmatter.diagnostics.logbuffer import LogBufferHandler
from loxmatter.loxone.sender import UdpSender
from loxmatter.matter.client import BridgeMatterClient
from loxmatter.model.store import Store
from loxmatter.timestamps import now_iso

Invoker = Callable[[MatterCall], Awaitable[None]]

logger = logging.getLogger(__name__)

COMMAND_LOG_SIZE = 500

# Calls under this prefix are not recorded into the command log - see the
# module docstring.
_DIAGNOSTICS_PREFIX = "/api/diagnostics"

# Not a real HTTP status code (those all lie between 100 and 599) - marks
# a command log entry for which the route itself crashed (an unhandled
# exception from `call_next`) rather than having actually answered with
# this code. Distinguishable from any real status code, see
# `_record_command` (review fix important, 2026-09-02).
_CRASHED_STATUS = 0

# Task 7, phase 5: the UI lives as a static directory next to this module,
# not in its own package - `src/loxmatter/web/`, one level above `loxone/`
# (hence `.parents[1]`). No build step, no bundler: `index.html`, `app.js`,
# `style.css` and the vendored Alpine.js under `web/vendor/` are served
# unchanged (see there for why Alpine is vendored rather than pulled from
# a CDN - `web/index.html`'s header comment).
_WEB_DIR = Path(__file__).parents[1] / "web"


def normalize_api_token(token: str | None) -> str | None:
    """The ONE spot where it is decided whether a token is set (review fix
    fix 2, 2026-09-03).

    `build_api_guard` asks here - until task 8 the startup warning also
    asked here (back then `cli._warn_if_missing_api_token`); since it
    concerns itself with the password rather than the token, it queries
    only the store and is accordingly named `cli._warn_if_no_password`.
    The originally reported bug nonetheless remains the reason for this
    function: a token consisting only of whitespace (a stray trailing
    newline from a copied `.env`, `--api-token " "`) counted as a genuine
    secret to the guard, but could not be sent correctly via an HTTP
    header at all - HTTP header values contain no newlines, and leading/
    trailing whitespace is discarded during parsing anyway (RFC 9110). The
    token path was therefore permanently unusable, without anything ever
    pointing that out, because the token was, after all, "not None".

    Hence two steps, both here and nowhere else:

    - Outer whitespace is stripped. A `LOXMATTER_API_TOKEN` with a trailing
      newline should be the secret as it stands without the newline -
      anything else would be a secret nobody could ever transmit.
    - If nothing remains, the result is `None` - i.e. exactly the same
      case as "no token set", with the same consequences: the token path
      in the guard (`build_api_guard`) stays closed, and the fabric backup
      stays locked (see `api.diagnostics`) - both regardless of whether a
      session still applies alongside it."""
    if token is None:
        return None
    stripped = token.strip()
    return stripped or None


def _token_from_authorization(header: str | None) -> str | None:
    """Extracts the token from `Authorization: Bearer <Token>` - the main path."""
    if header is None:
        return None
    prefix = "Bearer "
    if not header.startswith(prefix):
        return None
    return header[len(prefix) :]


def _token_from_websocket_subprotocol(header: str | None) -> str | None:
    """Extracts the token from `Sec-WebSocket-Protocol: bearer, <Token>` -
    the exception path for the browser WebSocket (see `build_api_guard`).

    Accepts exclusively exactly two values, the first of which is
    `api.live.BEARER_SUBPROTOCOL` - the same constant that `api.live`
    returns in the accept, so that the reading and answering sides cannot
    drift apart. Anything else - a single value, three values, a different
    marker - yields `None` and thus a rejection: this form is the only one
    `app.js` sends, and a more generous interpretation would only leave
    additional, untested paths into the guard."""
    if header is None:
        return None
    values = [value.strip() for value in header.split(",")]
    if len(values) != 2 or values[0] != BEARER_SUBPROTOCOL:
        return None
    return values[1] or None


def _tokens_match(presented: str, expected: str) -> bool:
    """Constant-time comparison (review fix fix 2, 2026-09-03).

    Compares the UTF-8 bytes, not the `str` objects: `compare_digest`
    raises `TypeError` on `str` arguments as soon as even one of them
    contains a character outside ASCII. An attacker could otherwise
    trigger a 500 instead of a 401 with a single umlaut in the header -
    a self-built oracle ("a comparison is running here") and
    an unnecessary traceback in the log. On `bytes`, `compare_digest`
    knows no such restriction and compares every byte sequence in
    constant time, so the special case disappears with nothing to replace
    it, rather than being handled with a try/except."""
    return secrets.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def build_api_guard(token: str | None, store: Store) -> Callable[..., Awaitable[None]]:
    """Protects the `/api` routes, not the Miniserver's (task 8, phase 5).

    The Miniserver calls virtual outputs without a header - it cannot send
    a token along. `/cmd` and `/resync` must therefore stay open, and that
    is a deliberate boundary, not carelessness: whoever reaches the port
    can still switch devices. What the token prevents is commissioning,
    removal, and downloading the fabric backup - i.e. everything that
    changes the inventory (spec 9).

    **`Authorization: Bearer <Token>` is the main path** and is always
    read first. In addition, the token is accepted from the handshake
    header `Sec-WebSocket-Protocol`, in the form `bearer, <Token>` (see
    `_token_from_websocket_subprotocol`). This second path is intended
    solely for the browser WebSocket; technically, because this one guard
    serves all `/api` routers, it also sits on the ordinary HTTP routes.
    That is harmless and deliberately not excluded: `Sec-` is a forbidden
    header name, no browser script can set it, and a second guard split
    by router would be exactly the kind of duplication from which an
    unprotected router later emerges (review fix minor #2, 2026-09-03).
    Reason for the path at all: the browser `WebSocket` API has no
    parameter for custom headers, `Authorization` is simply impossible
    there. The only channel in the handshake a browser can influence is
    the subprotocol argument (`new WebSocket(url, ["bearer", token])`).
    That is preferable to a query parameter, because a query parameter
    ends up in server logs, proxy logs and the browser history, whereas a
    header does not (the same reasoning by which `api.diagnostics`
    deliberately does NOT write the query string into the command log).
    Consequence for the token itself: it must be transmittable as an HTTP
    token - no spaces, no comma, ASCII. The `openssl rand -hex 32`
    recommended by `.env.example` and the README yields only `[0-9a-f]`
    and satisfies that on its own.

    The WebSocket routes `/api/live` AND `/api/diagnostics/live` must
    return the chosen subprotocol in the accept, otherwise the browser
    aborts the handshake per RFC 6455 - the same function
    `api.streaming.accepted_subprotocol` handles this for both (see
    there; `"bearer"` is returned, never the token).

    **There is no more open state.** Up to this point, a service without
    a configured token let every `/api` route through and made do with a
    warning in the log - whoever missed the warning was running an open
    bridge without noticing. Since the WebUI login, the rule is: without
    a valid cookie and without a valid token, every request here ends with
    401, even if neither a password nor a token has been set up. The only
    way in is then the initial setup under `/auth/setup`, which hangs
    outside this guard (see `api/auth.py`).

    Applies equally to HTTP routes AND to the WebSocket routes `/api/live`
    and `/api/diagnostics/live`:
    `app.include_router(..., dependencies=[Depends(guard)])` resolves this
    dependency before EVERY route of the respective router, including
    before a WebSocket handshake - FastAPI/uvicorn reject an
    `HTTPException` from a WebSocket dependency via the ASGI "denial
    response" extension (an HTTP status code before the accept), rather
    than accepting the connection first and then closing it (verified in
    `tests/api/test_security.py`).

    **Since the WebUI login there are two proofs instead of one.** First
    the session cookie (`loxmatter_session`, see `auth.sessions`), then
    the bearer token. The cookie is the browser's path, the token that of
    scripts and `curl` - which is why the cookie is checked first: it is
    the more common case, and it costs one SELECT instead of a hash
    comparison.

    `HTTPConnection` instead of `Request`: it is the shared base type of
    `Request` and `WebSocket`, and the same dependency hangs off both
    kinds of routes - `/api/live` and `/api/diagnostics/live` are
    WebSocket routes, in which a `Request` parameter could not be resolved
    at all. The cookie travels along with the WebSocket handshake by
    itself (same origin), which is why the browser no longer needs a
    subprotocol there since the login."""
    # Normalized once at build time, not on every request: `None` and a
    # purely whitespace token are the same case - see `normalize_api_token`.
    expected = normalize_api_token(token)

    async def guard(
        conn: HTTPConnection,
        authorization: str | None = Header(default=None),
        sec_websocket_protocol: str | None = Header(default=None),
    ) -> None:
        session_id = conn.cookies.get(SESSION_COOKIE)
        if session_id is not None and session_is_valid(store.auth, session_id):
            return
        if expected is not None:
            presented = _token_from_authorization(authorization)
            if presented is None:
                presented = _token_from_websocket_subprotocol(sec_websocket_protocol)
            if presented is not None and _tokens_match(presented, expected):
                return
        raise HTTPException(
            status_code=401,
            detail=i18n.t("api.server.fail_login_required"),
        )

    return guard


class _RuntimeDependency(RuntimeValues, ObservableRuntime, Protocol):
    """What `build_app` itself needs from `runtime` - in addition to the
    narrower protocols of the individual routers (`RuntimeValues` for
    `build_device_router`, `ObservableRuntime` for `build_live_router`):
    `resend_all` for `/resync` further below (spec 6.4). `loxone.runtime.
    Runtime` already satisfies this unchanged; a double (see
    `scripts/dev_web_server.py`, `_SeededRuntime`) no longer needs to build
    a real `Runtime` for this."""

    async def resend_all(self) -> int: ...


def build_app(
    store: Store,
    invoke: Invoker,
    runtime: _RuntimeDependency,
    client: BridgeMatterClient | None = None,
    sender: UdpSender | None = None,
    matter_data_dir: Path | None = None,
    api_token: str | None = None,
    log_handler: LogBufferHandler | None = None,
    thread_dataset_source: ThreadDatasetSource | None = None,
) -> FastAPI:
    app = FastAPI(title="loxmatter", docs_url=None, redoc_url=None)
    command_log: RingBuffer[CommandLogEntry] = RingBuffer(maxlen=COMMAND_LOG_SIZE)
    api_guard = [Depends(build_api_guard(api_token, store))]

    def _append_command_log(*, method: str, path: str, status: int) -> None:
        """Appends an entry - wrapped in its own try/except, a failure
        while recording must prevent neither the response nor (in the
        crash branch below) the re-raised exception."""
        try:
            command_log.append(
                CommandLogEntry(method=method, path=path, status=status, timestamp=now_iso())
            )
        except Exception:
            logger.exception(
                "command recording for %s %s failed - response is delivered anyway",
                method,
                path,
            )

    @app.middleware("http")
    async def _record_command(
        request: Request, call_next: Callable[[Request], Awaitable[StarletteResponse]]
    ) -> StarletteResponse:
        """Records EVERY call outside `/api/diagnostics/*` - including the
        one that crashes the route itself (review fix important,
        2026-09-02).

        `call_next` used to sit UNPROTECTED in this function: an unhandled
        exception from a route (not an `HTTPException`, a genuine bug)
        left `call_next` BEFORE the try/except below was ever reached -
        precisely the call that brings the service down therefore never
        showed up in `GET /api/diagnostics/commands`. The `try` here
        therefore catches the crash itself, notes it with
        `_CRASHED_STATUS` (not a real status code, see there) and then
        re-raises the exception UNCHANGED (`raise` with no argument keeps
        the original traceback) - middleware must not swallow an
        exception, Starlette's own error handling (`ServerErrorMiddleware`)
        still needs to see it to, e.g., produce the 500 response.
        Recording itself costs nothing extra in the process."""
        record = not request.url.path.startswith(_DIAGNOSTICS_PREFIX)
        try:
            response = await call_next(request)
        except Exception:
            if record:
                _append_command_log(
                    method=request.method,
                    path=request.url.path,
                    status=_CRASHED_STATUS,
                )
            raise
        if record:
            _append_command_log(
                method=request.method,
                path=request.url.path,
                status=response.status_code,
            )
        return response

    @app.middleware("http")
    async def _sync_language(
        request: Request, call_next: Callable[[Request], Awaitable[StarletteResponse]]
    ) -> StarletteResponse:
        """Reads the stored language setting freshly on EVERY request (spec
        section 4) - registered as the VERY LAST middleware, so that it
        runs before `_record_command` and before every route (including
        the login guard, whose 401 text is also translated). Middleware
        registration order in Starlette (via `@app.middleware("http")`,
        equivalent to `add_middleware`): Starlette inserts every newly
        registered layer at the FRONT of `app.user_middleware`
        (`insert(0, ...)`) and then builds the actual stack from
        `reversed(user_middleware)` - the function registered LAST
        therefore lands at index 0 and becomes the OUTERMOST layer, i.e.
        sees a request first (passed through from outside to inside by
        `Middleware.__call__`). Verified via a `TestClient` probe (two
        middlewares, call order logged): the function decorated last via
        `@app.middleware("http")` ran first. A test below
        (`test_sync_language_is_the_outermost_middleware` in
        `tests/loxone/test_server.py`) pins down exactly this order - see
        also the corrected derivation in this task's implementation plan,
        section "Middleware registration order".

        `store.locale.get_language()` never throws (phase A) - no
        try/except needed, unlike `_append_command_log` further above,
        which catches a genuine failure while writing into a foreign ring
        buffer.

        Exception: if `LOXMATTER_LANG` is set (CLI override, see
        `cli.py`), this middleware does NOT overwrite the process language
        from the store - otherwise the very first incoming request would
        immediately discard the override set by the CLI bootstrap again
        (review fix important, 2026-09-04)."""
        if os.environ.get("LOXMATTER_LANG") is None:
            i18n.set_language(store.locale.get_language())
        return await call_next(request)

    # `dependencies=api_guard` on each of the nine `/api` routers (task 8,
    # phase 5, see `build_api_guard` above; the eighth since `POST
    # /api/export/project-sync`, task 11, phase 6, the ninth since
    # `build_language_router`, this task): this protects without
    # exception every route of these nine routers, including the
    # WebSocket routes `/api/live` and `/api/diagnostics/live` - and
    # explicitly NOT `/cmd`, `/resync`, `/health`, `/` and `/static`,
    # which are mounted further below without `dependencies`.
    app.include_router(
        build_device_router(store, client, runtime, thread_dataset_source),
        dependencies=api_guard,
    )
    app.include_router(build_export_router(store), dependencies=api_guard)
    app.include_router(build_project_sync_router(store), dependencies=api_guard)
    app.include_router(build_settings_router(store), dependencies=api_guard)
    app.include_router(build_language_router(store), dependencies=api_guard)
    app.include_router(build_live_router(runtime), dependencies=api_guard)
    # Derselbe `invoke` wie unten bei `/cmd/{key}/{value}` - siehe
    # api/control.py Moduldocstring: eine Uebersetzung, zwei Aufrufer, sonst
    # driften sie (Spec 4.2, test_the_same_translation_as_the_loxone_endpoint).
    app.include_router(build_control_router(store, invoke, runtime), dependencies=api_guard)
    app.include_router(
        build_diagnostics_router(
            store,
            command_log,
            client,
            sender,
            matter_data_dir,
            runtime,
        ),
        dependencies=api_guard,
    )
    # Task 4 of this phase: the ongoing diagnostics livestream alongside
    # the three one-off diagnostics routes above - see
    # `api.diagnostics_live`'s module docstring for why this is its OWN
    # router instead of another route on `build_diagnostics_router` (the
    # same `/api/diagnostics` prefix, two routers: FastAPI merges
    # identical prefixes without complaint).
    app.include_router(
        build_diagnostics_live_router(sender, command_log, log_handler),
        dependencies=api_guard,
    )

    # WITHOUT `dependencies=api_guard` - exactly like `/health`, `/cmd` and
    # `/resync` further below. Whoever has not logged in yet must be able
    # to reach these four routes, otherwise there is no way in (see
    # api/auth.py's module docstring).
    app.include_router(build_auth_router(store))
    # See api/language.py's module docstring: the initial setup/login page
    # needs these translations just to display itself at all, before
    # anyone can be logged in.
    app.include_router(build_i18n_router(store))

    # Task 7, phase 5: the WebUI itself. `StaticFiles` already rejects an
    # access that wants to escape `_WEB_DIR` (e.g.
    # `/static/../../../etc/passwd`) with 404 on its own - a check of our
    # own here would only be a second, drifting copy of the same check
    # (test_static_files_do_not_escape_their_directory).
    app.mount("/static", StaticFiles(directory=_WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        """Serves the UI. No build step, no CDN dependency."""
        return FileResponse(_WEB_DIR / "index.html")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/resync")
    async def resync() -> dict[str, int]:
        """Spec 6.4: hangs off the system-start block in the config project."""
        try:
            count = await runtime.resend_all()
        except Exception as exc:  # e.g. a UdpSender whose socket is already closed
            # logger.exception writes the full traceback into the server
            # log, NOT into the HTTP response - the same reasoning as at
            # /cmd above: the difference between a dead sender and a
            # genuine bug in resend_all should be preserved in the log,
            # even though both are, for the caller, now just "Full resend
            # failed: <message>" without a traceback.
            logger.exception("full resend via /resync failed")
            raise HTTPException(
                status_code=502, detail=i18n.t("api.server.fail_resync", exc=exc)
            ) from exc
        # English key in the wire format (review fix M9, 2026-09-02):
        # identifiers in responses are English like code identifiers, even
        # where prose/error messages stay German - "gesendet" used to be
        # the only German key in a JSON response.
        return {"sent": count}

    @app.get("/cmd/{key}/{value}")
    async def command(key: str, value: str) -> dict[str, str]:
        try:
            stored = store.resolve_command(key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            call = to_matter_call(stored, value)
        except UnsupportedValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            await invoke(call)
        except Exception as exc:  # every device problem becomes a 502
            # logger.exception writes the full traceback into the server
            # log, NOT into the HTTP response (see
            # test_a_failing_matter_call_yields_502_not_a_traceback).
            # Without that, a genuine bug in the invoker would look in the
            # log exactly like a device that just is not responding -
            # both would be nothing more than "device unreachable:
            # <message>" without a traceback, and the difference between
            # "Zigbee mesh gone" and "typo in the invoker" would be lost.
            logger.exception("Matter call for key %r failed", key)
            raise HTTPException(
                status_code=502, detail=i18n.t("api.errors.device_unreachable", exc=exc)
            ) from exc

        return {"status": "ok", "key": key}

    return app
