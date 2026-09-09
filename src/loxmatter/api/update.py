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

"""The update routes - design "Applying updates through the web UI"
(2026-09-08), sections 9 and 10.

This module glues `loxmatter.update` (files) and `loxmatter.update_check`
(network) to HTTP and does nothing else. In particular it does NOT check
the target - that is the sidecar's job (spec section 10), and a boundary
that relies on a precheck one layer up is no boundary at all.

All routes sit behind the same `api_guard` as every other `/api` route
(see `build_app`'s `dependencies=api_guard`). Deliberately NO repeated
password prompt: the same session already downloads the fabric backup
today, i.e. the irreplaceable credentials of the entire Matter network.
An update to a published version is the smaller prize by comparison, and
a second prompt here would claim a security that does not exist anywhere
else in this API.

`loxmatter.update.request_update` can refuse a request for two DISTINCT
reasons - not one, as an earlier draft of this router assumed. The first
is the obvious one: the sidecar's last known phase is still running
(`pull`, `health`, ...). The second, closed only after this router's own
brief was written (see `update.py`'s module docstring and
`_pending_job_id`), is a request that has already been written to
`request.json` but that the sidecar's two-second poll has not yet caught
up to - `state.json` still reports the previous end state in that window.
Both raise the same `UpdateBusyError`, and this router deliberately does
NOT try to tell them apart: either way, a second request right now would
only ever lose the first one, and "an update is already in flight, try
again shortly" is the one honest thing to say about both."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from loxmatter import i18n, update_check
from loxmatter import update as update_files
from loxmatter.model.store import Store
from loxmatter.version import build_info

# The sidecar refreshes state.json's heartbeat every two seconds
# (update.py's own `_MAX_SILENT_SECONDS`) - the web UI's System tab polls
# this route on the same cadence to follow it. Both reads below
# (`update_files.read_state`/`read_log`) are already best-effort, local
# filesystem reads that fold every failure mode (missing, truncated,
# mid-write) into an honest "nothing here" rather than raising - this
# route adds nothing on top of that guarantee, since the bridge itself is
# being replaced while this is being polled and cannot afford to add a
# new way to turn a momentary read hiccup into a 500.
_FETCH_TIMEOUT_SECONDS = 10


class ApplyIn(BaseModel):
    target: str


class ApplyOut(BaseModel):
    id: str


class UpdateSettingsIn(BaseModel):
    channel: str | None = None
    check_enabled: bool | None = None


def _default_session_factory() -> Any:
    # Lazily imported, like `matter/otbr.py`'s own `_default_session_factory`:
    # a test that hands in its own `session_factory` (see
    # tests/api/test_update_api.py's `FakeSession`) should never need
    # aiohttp loaded at all.
    import aiohttp

    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_SECONDS))


async def _fetch(
    url: str,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> dict[str, Any] | list[Any]:
    """The one place in this whole feature that reaches outside the
    machine - `update_check.check()` takes this in as its `fetch`
    parameter (see that module's docstring for why the network layer is
    injected rather than hardwired). The web UI's own request to `GET
    /api/update/check` waits synchronously on this call, so a GitHub that
    never answers must not be allowed to hang it - hence the short,
    fixed timeout.

    Deliberately does NOT lean on aiohttp's own `response.raise_for_status()`
    / `response.json()`. Both look convenient, and both are wrong here:
    `raise_for_status()` raises `ClientResponseError` on a non-2xx status
    (a GitHub rate limit, most likely - `403`), and `.json()` raises the
    `ContentTypeError` subclass of the same on an unexpected content type
    (an HTML error page, say) - and NEITHER inherits from `OSError`.
    `update_check.check()`'s own exception handling is `except (OSError,
    KeyError, ValueError, TypeError)`, written against the failure modes
    an untrusted network call ordinarily produces; a `ClientResponseError`
    would sail straight past that tuple and turn a rate-limited GitHub
    into an unhandled 500 on this bridge's own API - exactly the "a
    hanging call must not break the System tab" failure this feature
    exists to avoid, just arriving as a crash instead of a hang. Reading
    the status and the raw body by hand below, then parsing with the
    stdlib `json` module, keeps every failure inside a type `check()`
    already expects: a non-2xx status becomes a plain `ValueError`, and an
    unparseable body raises `json.JSONDecodeError` - itself a `ValueError`
    subclass.

    A connection that never gets a response at all (DNS failure, refused,
    the fixed timeout above expiring) is a different matter: aiohttp's own
    `ClientConnectorError`, and - since Python 3.11 - the timeout itself
    (`asyncio.TimeoutError` is now the builtin `TimeoutError`), both
    happen to inherit from `OSError` and so already fit `check()`'s catch
    tuple without any help from here.

    A THIRD category is neither "bad status/body" nor "never got a
    response": a response that STARTS and then dies mid-transfer, before
    `status`/`text()` above ever return - `ServerDisconnectedError` (the
    connection drops) or `ClientPayloadError` (a truncated chunked body).
    An earlier version of this docstring claimed aiohttp's exceptions
    "already inherit from OSError" and left every one of them to
    propagate as-is on that basis; checked against the pinned aiohttp
    3.14.3, that is true of `ClientConnectorError`/`TimeoutError` above
    but FALSE for these two - their MRO runs `ClientError` -> `Exception`,
    with no `OSError` anywhere in it - so before the `except` clause below
    existed, they sailed straight past `check()`'s tuple and reached the
    web UI as an unhandled 500, exactly like the rate-limit/bad-body cases
    this function already guards against. Caught as the whole `ClientError`
    family below, not enumerated by name: naming individual subtypes is
    what missed this category the first time, and aiohttp is free to add
    another one in a future release.

    `session_factory` mirrors `matter/otbr.fetch_active_dataset`'s own
    parameter of the same name and for the same reason: a seam for tests
    to hand in a `FakeSession` that never opens a socket, not
    speculative flexibility - see `tests/api/test_update_api.py`.
    """
    import aiohttp

    session = (session_factory or _default_session_factory)()
    try:
        async with session.get(url, headers={"Accept": "application/vnd.github+json"}) as response:
            status = response.status
            body = await response.text()
    except aiohttp.ClientError as exc:
        # See the docstring paragraph above: a response that started and
        # then died mid-transfer does not inherit `OSError`, so it must be
        # translated into a type `check()` already expects, the same way
        # the non-2xx/non-JSON cases below are - otherwise it would be an
        # unhandled 500 on a route the web UI polls every two seconds.
        raise ValueError(f"GitHub's response to {url} did not complete: {exc}") from exc
    finally:
        await session.close()
    if status != 200:
        # The body itself is not shown here - on a rate limit it is
        # GitHub's own JSON error object and safe to surface, but this
        # function has no way to know that is always the case for every
        # future non-200 response these two endpoints might ever produce.
        raise ValueError(f"GitHub answered {url} with HTTP {status}.")
    parsed = json.loads(body)
    if not isinstance(parsed, dict | list):
        # `Fetch`'s own contract (see update_check.py) is "a JSON object
        # or - should GitHub ever answer with one - a JSON list". A bare
        # number, string or `null` satisfies neither, and `check()`'s
        # `.get()`/`isinstance(body, dict)` calls would either crash or
        # silently misread it; better to say plainly that this response
        # was not what was promised.
        raise TypeError(f"GitHub's response to {url} is not a JSON object or array.")
    return parsed


def build_update_router(store: Store, update_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/update")

    def _status() -> dict[str, Any]:
        state = update_files.read_state(update_dir)
        return {
            "state": None
            if state is None
            else {
                "phase": state.phase,
                "id": state.id,
                "from": state.from_version,
                "to": state.to_version,
                "error": state.error,
                "rolled_back": state.rolled_back,
                "rolled_back_to": state.rolled_back_to,
                "healthy": state.healthy,
            },
            "updater_present": update_files.updater_present(state, now=datetime.now(UTC)),
            "log": update_files.read_log(update_dir),
            "channel": store.update_settings.get_channel(),
            "check_enabled": store.update_settings.get_check_enabled(),
        }

    @router.get("/status")
    async def status() -> dict[str, Any]:
        return _status()

    @router.patch("/settings")
    async def save_settings(patch: UpdateSettingsIn) -> dict[str, Any]:
        if patch.channel is not None:
            try:
                store.update_settings.set_channel(patch.channel)
            except ValueError as exc:
                # `str(exc)` unchanged, like `api/settings.py`'s own
                # `save_resend_interval`: the message is authored in
                # `update_settings_store.set_channel` itself, one message,
                # one place, not re-worded here.
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if patch.check_enabled is not None:
            store.update_settings.set_check_enabled(patch.check_enabled)
        return _status()

    @router.get("/check")
    async def check() -> dict[str, Any]:
        if not store.update_settings.get_check_enabled():
            # Not an error - a setting - but still delivered through the
            # `error` field, so the web UI has a sentence ready to show
            # instead of an empty card. `checked_at` stays `None`: no
            # check actually ran, and claiming a time for one would be
            # its own small lie.
            return {
                "channel": store.update_settings.get_channel(),
                "target": None,
                "title": None,
                "notes": None,
                "behind": None,
                "checked_at": None,
                "error": i18n.t("api.update.check_disabled"),
            }
        info = build_info()
        try:
            result = await update_check.check(
                store.update_settings.get_channel(),
                current_version=info.version,
                current_commit=info.commit,
                fetch=_fetch,
            )
        except ValueError:
            # `check()` deliberately raises `ValueError` for a channel
            # outside `("stable", "dev")` (see its own docstring and
            # `tests/test_update_check.py::test_an_unknown_channel_is_refused`)
            # rather than folding it into its own catch tuple - that is
            # right for callers passing a hardcoded/validated literal, a
            # genuine programming error worth a loud failure. This route
            # is not that caller: it hands `check()` whatever
            # `store.update_settings.get_channel()` currently returns, and
            # while `set_channel` is the only writer and already validates
            # against the same two channels, two hardcoded lists can drift,
            # and the `setting` row itself is one hand edit or migration
            # bug away from holding something else. Same shape as the
            # `check_disabled` branch above: a calm 200 with `error` set,
            # not a 500 for a value this request never supplied itself.
            return {
                "channel": store.update_settings.get_channel(),
                "target": None,
                "title": None,
                "notes": None,
                "behind": None,
                "checked_at": None,
                "error": i18n.t("api.update.fail_unknown_channel"),
            }
        return {
            "channel": result.channel,
            "target": result.target,
            "title": result.title,
            "notes": result.notes,
            "behind": result.behind,
            "checked_at": result.checked_at,
            "error": result.error,
        }

    @router.post("/apply")
    async def apply(body: ApplyIn) -> ApplyOut:
        state = update_files.read_state(update_dir)
        if not update_files.updater_present(state, now=datetime.now(UTC)):
            # 503, not 200: writing a request into a volume nobody reads
            # would leave the web UI showing progress that never begins -
            # see the module docstring of `update.updater_present`.
            raise HTTPException(status_code=503, detail=i18n.t("api.update.fail_no_updater"))
        try:
            job_id = update_files.request_update(
                update_dir,
                channel=store.update_settings.get_channel(),
                target=body.target,
            )
        except update_files.UpdateBusyError as exc:
            # Both reasons `UpdateBusyError` can be raised for (a running
            # phase, or a request the sidecar has not yet caught up to -
            # see the module docstring above) land on the same 409: either
            # way, writing another request right now would only lose this
            # one, and the client's own next poll of `/status` already
            # tells it what is actually running.
            raise HTTPException(status_code=409, detail=i18n.t("api.update.fail_busy")) from exc
        except OSError as exc:
            # `request_update` does `mkdir`/`write_text`/`os.replace` with
            # no handling of its own - correctly so, see its own
            # docstring: file handling is this feature's job, not the
            # store module's. A read-only remount after an SD-card fault,
            # or a full disk, are realistic failure modes on the
            # Raspberry Pi this bridge targets, and either raises a plain
            # `OSError` subclass (`PermissionError`, `OSError` for
            # `ENOSPC`) straight out of that call.
            #
            # 503, not 500: this is not a bug in this router's own code to
            # page over (a bare 500 would wrongly suggest one), and 500
            # gives the operator nothing to act on - not the client's
            # fault either (rules out 4xx), and it is not "an update is
            # already running" (rules out the 409 above). It is the same
            # "the update mechanism cannot currently do anything, go fix
            # the environment and retry" shape as the no-updater 503
            # above, just for a broken filesystem instead of a missing
            # sidecar.
            raise HTTPException(
                status_code=503, detail=i18n.t("api.update.fail_unwritable", exc=str(exc))
            ) from exc
        return ApplyOut(id=job_id)

    return router
