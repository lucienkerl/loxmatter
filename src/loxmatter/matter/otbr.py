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

**Correction (21 September 2026):** that was true of python-matter-server
8.1.2, the version this history paragraph describes; this bridge now runs
its successor, matterjs-server, which persists `threadDataset` under
`config/` in its own data directory - measured on `pi3-andi`. History is
not rewritten here (the incident below happened exactly as described,
against the old server), but `matter/thread_network.py`'s
`ThreadNetworkKeeper` hands the dataset over on every bridge start
regardless of what matter-server reports it already has, because a
persisted entry from an older installation is now the failure mode to
guard against, not a memory that a restart conveniently erased.

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

import json
import os
from collections.abc import Callable
from typing import Any, Final, Literal

from loxmatter import i18n

# OTBR's REST interface on the same host. Not configurable via a CLI flag,
# but via the environment (see `_base_url`): the common case needs no
# setting at all, and a flag would be one more translated help text for a
# setting nobody in this stack ever sets.
DEFAULT_OTBR_URL: Final = "http://127.0.0.1:8081"

_OTBR_URL_ENV: Final = "LOXMATTER_OTBR_URL"

# The paths are part of OTBR's REST API, not chosen by us.
_ACTIVE_DATASET_PATH: Final = "/node/dataset/active"
_NODE_STATE_PATH: Final = "/node/state"
_JSON: Final = {"Accept": "application/json", "Content-Type": "application/json"}
_NETWORK_NAME_TLV_TYPE: Final = 0x03

NetworkCreation = Literal["created", "exists", "busy"]

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


# MeshCoP TLV type for the Channel TLV (Thread 1.3 specification, "Network
# Management TLVs" table): one byte of channel page followed by the
# channel itself as a 2-byte big-endian integer - three value bytes in
# total. Channel page 0 is the 2.4 GHz band, the one Zigbee also uses.
_CHANNEL_TLV_TYPE: Final = 0x00
_CHANNEL_TLV_VALUE_LENGTH: Final = 3


def thread_channel_from_dataset(dataset: str) -> int | None:
    """The 2.4 GHz channel a hex TLV active dataset names, or `None`.

    `None` covers every shape this must tolerate without raising: no
    Channel TLV present, one with the wrong length, or a stream truncated
    partway through a type/length pair. This function is a courtesy - one
    Zigbee/Thread channel collision avoided - never a gate, so a dataset it
    cannot make sense of must read exactly like no border router at all
    (`channels_excluding(None)`), not like an error that stops a
    `ZigbeeSource` from being built at all.

    `dataset` is a credential-bearing blob (see `fetch_active_dataset`);
    this function never logs it or any slice of it, only the channel
    number it found.

    Two fail-safe gaps, both measured, both deliberately left as notes
    rather than code - an Active Operational Dataset fits in 254 bytes, so
    neither can arise from a real border router:

    - **The channel PAGE byte is skipped, not checked.** Page 0 is the
      2.4 GHz band Zigbee shares; page 23 is the 915 MHz band, whose
      channel numbers run from 0 and therefore overlap Zigbee's
      candidates. A page-23 dataset naming channel 11 (`"000317000b"`)
      returns 11 here, and `channels_excluding` then drops 2.4 GHz
      channel 11 to avoid a network that is not on it. The cost is one
      candidate needlessly removed from a list of four, never a wrong
      network: this function only ever shortens that list, and
      `channels_excluding` refuses nothing.
    - **Thread's extended-TLV escape derails the scan.** A length byte of
      `0xFF` means "two more bytes of extended length follow"; this parser
      reads it as a 255-byte value, walks past the Channel TLV, and
      returns `None` - the exclusion is lost, not wrong, which is the same
      answer as no border router at all.
    """
    try:
        raw = bytes.fromhex(dataset)
    except ValueError:
        return None
    index = 0
    while index + 2 <= len(raw):
        tlv_type = raw[index]
        length = raw[index + 1]
        value_start = index + 2
        value_end = value_start + length
        if value_end > len(raw):
            return None
        if tlv_type == _CHANNEL_TLV_TYPE and length == _CHANNEL_TLV_VALUE_LENGTH:
            return int.from_bytes(raw[value_start + 1 : value_end], "big")
        index = value_end
    return None


async def current_thread_channel(
    base_url: str | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> int | None:
    """The channel Zigbee should avoid, or `None` when there is nothing to
    avoid.

    Every reason this can fail to answer - no border router, an
    unreachable one, a dataset with no Channel TLV - reads the same way to
    the caller: form the Zigbee network on the full channel list
    (`channels_excluding(None)`). A missing OPTIONAL border router must
    never stop Zigbee from forming, the same rule startup already applies
    to a missing Zigbee radio itself.
    """
    try:
        dataset = await fetch_active_dataset(base_url, session_factory=session_factory)
    except ThreadDatasetUnavailableError:
        return None
    return thread_channel_from_dataset(dataset)


async def _request(
    method: str,
    url: str,
    headers: dict[str, str],
    data: str | None,
    session_factory: Callable[[], Any] | None,
) -> tuple[int, str]:
    """One HTTP exchange with the border router, as `(status, body)`.

    An unreachable border router raises `ThreadDatasetUnavailableError` with
    the address and the exception, never with a body - the body of a dataset
    answer is a credential (see `fetch_active_dataset`)."""
    session = (session_factory or _default_session_factory)()
    try:
        try:
            if method == "GET":
                call = session.get(url, headers=headers)
            else:
                call = session.put(url, data=data or "", headers=headers)
            async with call as response:
                return response.status, await response.text()
        except Exception as exc:
            # `Exception` and not just `aiohttp.ClientError`: this module
            # deliberately does not import aiohttp itself (see
            # `_default_session_factory`).
            raise ThreadDatasetUnavailableError(
                i18n.t("api.errors.thread_dataset_unreachable", url=url, exc=exc)
            ) from exc
    finally:
        await session.close()


def _url(base_url: str | None, path: str) -> str:
    return (base_url or _base_url()).rstrip("/") + path


def _unexpected(
    url: str, status: int, *, key: str = "api.errors.thread_dataset_http_status"
) -> ThreadDatasetUnavailableError:
    """An HTTP status from the border router that the caller did not expect.

    Defaults to the dataset-reading message (`fetch_active_dataset`,
    `read_active_dataset`): "instead of a Thread dataset" is right there,
    since a non-200/204 status there really is what OTBR replies for as
    long as no active network exists. `border_router_role`,
    `create_network_if_absent` and `enable_thread` pass
    `api.errors.otbr_http_status` instead - a 409 on `enable_thread` means
    the agent is busy, nothing about a missing dataset."""
    return ThreadDatasetUnavailableError(i18n.t(key, url=url, status=status))


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
    url = _url(base_url, _ACTIVE_DATASET_PATH)
    status, body = await _request("GET", url, dict(_PLAIN_TEXT), None, session_factory)
    if status != 200:
        raise _unexpected(url, status)
    return validated_dataset(body, url)


async def read_active_dataset(
    base_url: str | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> str | None:
    """The active dataset, or `None` when the border router holds none.

    Unlike `fetch_active_dataset`, "no network" is an answer here, not an
    error: ot-br-posix replies 204 No Content to `GET /node/dataset/active`
    while no active dataset exists (design 2026-09-21, section 3)."""
    url = _url(base_url, _ACTIVE_DATASET_PATH)
    status, body = await _request("GET", url, dict(_PLAIN_TEXT), None, session_factory)
    if status == 204:
        return None
    if status != 200:
        raise _unexpected(url, status)
    return validated_dataset(body, url)


async def border_router_role(
    base_url: str | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> str:
    """The Thread role, e.g. `"disabled"`, `"detached"`, `"leader"`."""
    url = _url(base_url, _NODE_STATE_PATH)
    status, body = await _request("GET", url, {"Accept": "application/json"}, None, session_factory)
    if status != 200:
        raise _unexpected(url, status, key="api.errors.otbr_http_status")
    try:
        role = json.loads(body)
    except ValueError:
        raise _unexpected(url, status, key="api.errors.otbr_http_status") from None
    if not isinstance(role, str):
        raise _unexpected(url, status, key="api.errors.otbr_http_status")
    return role


async def create_network_if_absent(
    base_url: str | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> NetworkCreation:
    """Form a new Thread network with random values - only if none exists.

    `If-None-Match: *` makes ot-br-posix check "no active dataset" and write
    the new one in the same main-loop task, so a network that appeared a
    moment ago is never replaced. The empty JSON object leaves every field to
    `otDatasetCreateNewNetwork`. 412: a dataset exists now. 409: the agent is
    no longer `disabled`."""
    url = _url(base_url, _ACTIVE_DATASET_PATH)
    headers = {**_JSON, "If-None-Match": "*"}
    status, _ = await _request("PUT", url, headers, "{}", session_factory)
    if status == 201:
        return "created"
    if status == 412:
        return "exists"
    if status == 409:
        return "busy"
    raise _unexpected(url, status, key="api.errors.otbr_http_status")


async def enable_thread(
    base_url: str | None = None,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> None:
    """`ifconfig up` plus `thread start`, through the REST API."""
    url = _url(base_url, _NODE_STATE_PATH)
    status, _ = await _request("PUT", url, dict(_JSON), '"enable"', session_factory)
    if status != 200:
        raise _unexpected(url, status, key="api.errors.otbr_http_status")


def thread_network_name_from_dataset(dataset: str) -> str | None:
    """The Network Name TLV (type 3, UTF-8, at most 16 bytes), or `None`.

    Same tolerance as `thread_channel_from_dataset`: anything it cannot make
    sense of reads as "no name", never as an error. The name is not a secret;
    the rest of the dataset is, and is never returned or logged."""
    try:
        raw = bytes.fromhex(dataset)
    except ValueError:
        return None
    index = 0
    while index + 2 <= len(raw):
        tlv_type = raw[index]
        length = raw[index + 1]
        value_start = index + 2
        value_end = value_start + length
        if value_end > len(raw):
            return None
        if tlv_type == _NETWORK_NAME_TLV_TYPE:
            if not 0 < length <= 16:
                return None
            try:
                return raw[value_start:value_end].decode("utf-8")
            except UnicodeDecodeError:
                return None
        index = value_end
    return None
