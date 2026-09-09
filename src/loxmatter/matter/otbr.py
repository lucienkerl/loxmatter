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

"""The active Thread dataset, read from the border router.

**Why this module exists.** matter-server keeps the Thread credentials
exclusively in memory: `_thread_credentials_set: bool = False` in the
constructor of `matter_server/server/device_controller.py`, set to `True`
only by `set_thread_operational_dataset()`. None of it is ever written to
disk - the service's data directory (`vendor_info`/`last_node_id`/`nodes`)
holds no dataset. Every restart of matter-server therefore erases it,
without anything reporting so.

This became visible on 2026-09-04: matter-server had been restarted the
previous day at 12:55, and since then every commissioning of a Thread
device had failed. The service's log stated the cause in plain text -

    Required network information not provided in commissioning parameters
    Parameters supplied: wifi (no) thread (no)
    Device supports: wifi (no) thread(yes)

- the UI, by contrast, showed only "Commission with code failed for node 7".
The BLE connection, pairing code and the secured session to the device were
all fine; the only thing missing was the network the device should have
belonged to. The UI did have the dataset as an input field, but it was
optional and cleared again after every commissioning (`api/devices.py`
only sent it when something was entered there) - as long as matter-server
kept running, that was harmless, but not after a restart.

**Why from OTBR and not from a store of our own.** The border router is
the place where the dataset already lives anyway, and it is the only one
that has it correct on its own after a network change. A second dataset
stored in this bridge would become silently wrong from the next `docker
compose down` of OTBR without a volume (see the comment on `otbr-state`
in `deploy/testhost/docker-compose.yml`) - and a wrong dataset fails later
and less transparently than none at all.

**Why over HTTP and not over `ot-ctl`.** Exactly the same reasoning as for
`_check_thread()` in `api/diagnostics.py`: this service runs in its own
container and has no access to OTBR's. OTBR's REST interface, on the other
hand, listens on 127.0.0.1:8081 in the host's network namespace, which
both share via `network_mode: host` - measured from the running
loxmatter container (status 200, 222 hex characters), not assumed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Final

from loxmatter import i18n

# OTBR's REST interface on the same host. Not configurable via a CLI flag,
# but via the environment (see `_base_url`): the common case needs no
# setting at all, and a flag would be one more translated help text for a
# setting nobody in this stack ever sets.
DEFAULT_OTBR_URL: Final = "http://127.0.0.1:8081"

_OTBR_URL_ENV: Final = "LOXMATTER_OTBR_URL"

# The path is part of OTBR's REST API, not chosen by us.
_ACTIVE_DATASET_PATH: Final = "/node/dataset/active"

# Without this header OTBR answers with a JSON structure (channel, PAN ID,
# key as separate fields); `set_thread_operational_dataset` only accepts
# the hex TLV that `text/plain` delivers.
_PLAIN_TEXT: Final = {"Accept": "text/plain"}

# The border router sits in the same house, usually on the same machine -
# a response that takes longer than this is not coming.
_TIMEOUT_SECONDS: Final = 5.0

_HEX_DIGITS: Final = frozenset("0123456789abcdefABCDEF")


class ThreadDatasetUnavailableError(RuntimeError):
    """The border router could not name an active Thread dataset.

    No reason to abort commissioning: a WiFi device needs none at all (see
    `api/devices.py`, where this error becomes a note rather than an
    abort). For a Thread device, on the other hand, this is the cause that
    would otherwise only arrive 40 seconds later as "Commission with code
    failed" - which is why the exception carries the reason in plain text.
    """


def _base_url() -> str:
    """Read at call time, not at import: otherwise the address could only
    be influenced in tests by reloading the module."""
    return os.environ.get(_OTBR_URL_ENV) or DEFAULT_OTBR_URL


def _default_session_factory() -> Any:
    # Lazily imported like in `matter/client.py`: tests with their own
    # session should never need to load aiohttp.
    import aiohttp

    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS))


def validated_dataset(body: str, url: str) -> str:
    """Check a string for whether it could be a Thread dataset.

    Public, not module-private: two callers, one rule -
    `fetch_active_dataset` below with the border router's response, and
    `api/devices.py` with the dataset entered by hand into the UI. A
    second, merely similar check there would be exactly the duplication
    that drifts apart sooner or later.

    In the error messages, `url` names only the ORIGIN of the checked
    string and is therefore not necessarily an address; the route in
    `api/devices.py` composes its message to the operator itself anyway.
    The dataset itself appears in NONE of this function's messages - it
    contains the network key of the Thread network.
    """
    dataset = body.strip()
    if not dataset:
        raise ThreadDatasetUnavailableError(i18n.t("api.errors.thread_dataset_empty", url=url))
    if not set(dataset) <= _HEX_DIGITS:
        # Deliberately does NOT show the response itself: if it were in
        # fact a dataset, a credential would end up in the log. The
        # template in `strings.yaml` therefore names only the length, in
        # BOTH languages.
        raise ThreadDatasetUnavailableError(
            i18n.t("api.errors.thread_dataset_not_hex", url=url, length=len(dataset))
        )
    if len(dataset) % 2 != 0:
        # Here too, not the string itself, only its length: each character
        # on its own is hex, so it could well be a genuine dataset -
        # truncated by exactly one character.
        #
        # Without this check, `set_thread_dataset` would pass this through,
        # and matter-server's `bytes.fromhex` would fail there with
        # "odd-length string". That comes back as `UnknownError` - a
        # `MatterError`, but not a `MatterUnavailableError`, so it slips
        # past the `except` in `api/devices.py`: HTTP 500 instead of a
        # message that says what to do.
        raise ThreadDatasetUnavailableError(
            i18n.t("api.errors.thread_dataset_odd_length", url=url, length=len(dataset))
        )
    return dataset


async def fetch_active_dataset(
    base_url: str | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> str:
    """Fetch the active Thread dataset as a hex TLV from the border router.

    The return value is a credential - it contains the network key of the
    Thread network. It belongs in neither a log nor an error message (see
    `deploy/testhost/README.md`); this module's exceptions therefore name
    only address, status and length.
    """
    url = (base_url or _base_url()).rstrip("/") + _ACTIVE_DATASET_PATH
    session = (session_factory or _default_session_factory)()
    try:
        try:
            async with session.get(url, headers=dict(_PLAIN_TEXT)) as response:
                status = response.status
                body = await response.text()
        except Exception as exc:
            # `Exception` and not just `aiohttp.ClientError`: this module
            # deliberately does not import aiohttp itself (see
            # `_default_session_factory`), and an unreachable border
            # router is, for the caller, the same case as one that
            # responds without a network - a reason, not a crash.
            raise ThreadDatasetUnavailableError(
                i18n.t("api.errors.thread_dataset_unreachable", url=url, exc=exc)
            ) from exc
    finally:
        await session.close()

    if status != 200:
        raise ThreadDatasetUnavailableError(
            i18n.t("api.errors.thread_dataset_http_status", url=url, status=status)
        )
    return validated_dataset(body, url)
