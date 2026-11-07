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

"""Home Assistant's sign-in and its WebSocket API (design 2026-11-07,
section 3).

**Sign-in without configuring anything in Home Assistant.** Home Assistant
is its own OAuth2 provider and accepts any web address as `client_id`, as
long as the `redirect_uri` has the same scheme and host (IndieAuth; local
addresses are allowed, see `homeassistant/components/auth/indieauth.py`).
The browser therefore goes to `/auth/authorize` with this bridge's own
address as `client_id`, the user signs in on Home Assistant's own page,
and Home Assistant sends the browser back with a code that `exchange_code`
trades for tokens. The user must be an administrator, because the calls
below that open a commissioning window or pause ZHA are admin-only. A
long-lived access token, pasted by hand, is the fallback for an
installation whose browser cannot reach this bridge under the address
Home Assistant would redirect to.

**One connection per step, not one per bridge.** The migration is a
handful of calls spread over minutes; a long-lived WebSocket would need
reconnect logic for nothing. `HomeAssistantSession` opens, authenticates,
answers the calls of one step and closes.

Tokens never appear in an exception message or a log line.
"""

from __future__ import annotations

import itertools
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Protocol, Self
from urllib.parse import urlencode, urlsplit

from loxmatter import i18n

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS: Final = 30.0
# Refresh a little before Home Assistant's 30 minutes run out, so a step
# that starts at minute 29 does not fail halfway.
_REFRESH_MARGIN_SECONDS: Final = 120.0


class HomeAssistantError(Exception):
    """A failure the web UI shows as it is - already translated."""


class HomeAssistantAuthError(HomeAssistantError):
    """The sign-in is no longer valid; the user has to sign in again."""


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_at: float | None


class HomeAssistantApi(Protocol):
    """What the migration needs from Home Assistant - a seam the tests
    fill with a fake."""

    async def call(self, command: str, **payload: Any) -> Any: ...

    async def delete(self, path: str) -> None: ...


def normalised_url(value: str) -> str:
    """`homeassistant.local:8123` and `http://ha:8123/` both become
    `http://…:8123`; anything that is not an http(s) address raises."""
    text = value.strip()
    if "://" not in text:
        text = f"http://{text}"
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HomeAssistantError(i18n.t("api.homeassistant.fail_url"))
    return f"{parts.scheme}://{parts.netloc}"


def authorize_url(base_url: str, *, client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{base_url}/auth/authorize?{query}"


def _default_session_factory() -> Any:
    import aiohttp

    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS))


def _unreachable(base_url: str) -> HomeAssistantError:
    return HomeAssistantError(i18n.t("api.homeassistant.fail_unreachable", url=base_url))


async def probe(base_url: str, *, session_factory: Callable[[], Any] | None = None) -> bool:
    """Whether a Home Assistant answers at `base_url`. `/auth/providers`
    is the one JSON route Home Assistant serves without a sign-in."""
    import aiohttp

    try:
        async with (
            (session_factory or _default_session_factory)() as session,
            session.get(f"{base_url}/auth/providers") as response,
        ):
            if response.status != 200:
                return False
            body = await response.json(content_type=None)
    except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
        return False
    providers = body.get("providers") if isinstance(body, dict) else body
    return isinstance(providers, list)


async def _token_request(
    base_url: str, form: dict[str, str], session_factory: Callable[[], Any] | None
) -> Tokens:
    import aiohttp

    try:
        async with (
            (session_factory or _default_session_factory)() as session,
            session.post(f"{base_url}/auth/token", data=form) as response,
        ):
            status = response.status
            body = await response.json(content_type=None) if status == 200 else None
    except (aiohttp.ClientError, OSError, TimeoutError, ValueError) as exc:
        raise _unreachable(base_url) from exc
    if status != 200 or not isinstance(body, dict) or "access_token" not in body:
        raise HomeAssistantAuthError(i18n.t("api.homeassistant.fail_sign_in"))
    expires_in = body.get("expires_in")
    refresh = body.get("refresh_token")
    return Tokens(
        access_token=str(body["access_token"]),
        refresh_token=refresh if isinstance(refresh, str) else form.get("refresh_token"),
        expires_at=time.time() + float(expires_in) if isinstance(expires_in, int | float) else None,
    )


async def exchange_code(
    base_url: str,
    *,
    client_id: str,
    code: str,
    session_factory: Callable[[], Any] | None = None,
) -> Tokens:
    return await _token_request(
        base_url,
        {"grant_type": "authorization_code", "code": code, "client_id": client_id},
        session_factory,
    )


async def refresh_tokens(
    base_url: str,
    *,
    client_id: str,
    refresh_token: str,
    session_factory: Callable[[], Any] | None = None,
) -> Tokens:
    return await _token_request(
        base_url,
        {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
        session_factory,
    )


def needs_refresh(expires_at: float | None, *, now: float | None = None) -> bool:
    if expires_at is None:
        return False
    return (now if now is not None else time.time()) >= expires_at - _REFRESH_MARGIN_SECONDS


class HomeAssistantSession:
    """One authenticated WebSocket connection, used as `async with`."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        *,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._base_url = base_url
        self._access_token = access_token
        self._session_factory = session_factory or _default_session_factory
        self._http: Any = None
        self._ws: Any = None
        self._ids = itertools.count(1)

    async def __aenter__(self) -> Self:
        import aiohttp

        ws_url = "ws" + self._base_url[len("http") :] + "/api/websocket"
        self._http = self._session_factory()
        try:
            self._ws = await self._http.ws_connect(ws_url, max_msg_size=0)
            hello = await self._ws.receive_json()
            if hello.get("type") != "auth_required":
                raise HomeAssistantError(i18n.t("api.homeassistant.fail_protocol"))
            await self._ws.send_json({"type": "auth", "access_token": self._access_token})
            answer = await self._ws.receive_json()
        except HomeAssistantError:
            await self._close()
            raise
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError, TypeError) as exc:
            await self._close()
            raise _unreachable(self._base_url) from exc
        if answer.get("type") != "auth_ok":
            await self._close()
            raise HomeAssistantAuthError(i18n.t("api.homeassistant.fail_sign_in"))
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._close()

    async def _close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # closing is best effort
                logger.debug("closing the Home Assistant WebSocket failed", exc_info=True)
            self._ws = None
        if self._http is not None:
            await self._http.close()
            self._http = None

    async def call(self, command: str, **payload: Any) -> Any:
        """Sends one command and waits for its result. Events and results
        for other ids are skipped - this session sends one command at a
        time, so nothing it waits for can be among them."""
        import aiohttp

        if self._ws is None:
            raise HomeAssistantError(i18n.t("api.homeassistant.fail_protocol"))
        message_id = next(self._ids)
        try:
            await self._ws.send_json({"id": message_id, "type": command, **payload})
            while True:
                answer = await self._ws.receive_json(timeout=_TIMEOUT_SECONDS)
                if answer.get("id") == message_id and answer.get("type") == "result":
                    break
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError, TypeError) as exc:
            raise _unreachable(self._base_url) from exc
        if not answer.get("success"):
            error = answer.get("error") or {}
            message = error.get("message") if isinstance(error, dict) else None
            logger.info("Home Assistant refused %s: %s", command, message)
            if isinstance(error, dict) and error.get("code") == "unauthorized":
                raise HomeAssistantAuthError(i18n.t("api.homeassistant.fail_not_admin"))
            raise HomeAssistantError(
                i18n.t("api.homeassistant.fail_command", command=command, message=message or "?")
            )
        return answer.get("result")

    async def delete(self, path: str) -> None:
        """One authenticated HTTP DELETE - removing a config entry has no
        WebSocket command."""
        import aiohttp

        if self._http is None:
            raise HomeAssistantError(i18n.t("api.homeassistant.fail_protocol"))
        headers = {"Authorization": f"Bearer {self._access_token}"}
        try:
            async with self._http.delete(f"{self._base_url}{path}", headers=headers) as response:
                status = response.status
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            raise _unreachable(self._base_url) from exc
        if status == 401:
            raise HomeAssistantAuthError(i18n.t("api.homeassistant.fail_sign_in"))
        if status >= 400:
            raise HomeAssistantError(
                i18n.t("api.homeassistant.fail_command", command=path, message=str(status))
            )
