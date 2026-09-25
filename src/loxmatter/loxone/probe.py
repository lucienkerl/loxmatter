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
"""Asks an address whether a Miniserver answers there - design "The
Miniserver's address is set in the web interface" (2026-09-25), section 8.

`/jdev/cfg/api` is the one request a Miniserver answers without signing in,
with its serial number and firmware version. The answer's `value` is not
JSON but a Python-style dict with single quotes, so the two fields are read
with the same patterns `install.sh`'s `check_miniserver` used on the same
shape (`tests/fixtures/miniserver/jdev_cfg_api.json`).

Never raises for a network failure: a Miniserver that is switched off right
now is a normal state, and the caller saves the address regardless."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import aiohttp

_SERIAL: Final = re.compile(r"'snr':\s*'([^']*)'")
_FIRMWARE: Final = re.compile(r"'version':\s*'([^']*)'")


class ProbeOutcome(StrEnum):
    FOUND = "found"
    TIMEOUT = "timeout"
    NO_CONNECTION = "no_connection"
    HTTP_ERROR = "http_error"
    NOT_A_MINISERVER = "not_a_miniserver"


@dataclass(frozen=True)
class MiniserverProbe:
    outcome: ProbeOutcome
    serial: str | None = None
    firmware: str | None = None
    http_status: int | None = None

    @property
    def found(self) -> bool:
        return self.outcome is ProbeOutcome.FOUND


async def probe_miniserver(ip: str, *, port: int = 80, timeout: float = 3.0) -> MiniserverProbe:
    """`port` exists for the tests; a Miniserver answers on 80."""
    url = f"http://{ip}:{port}/jdev/cfg/api"
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
            session.get(url) as response,
        ):
            if response.status >= 400:
                return MiniserverProbe(ProbeOutcome.HTTP_ERROR, http_status=response.status)
            body = await response.text(errors="replace")
    # TimeoutError first: aiohttp's ServerTimeoutError is also a
    # ClientConnectionError, and a timeout is the more useful thing to say.
    except TimeoutError:
        return MiniserverProbe(ProbeOutcome.TIMEOUT)
    except (aiohttp.ClientError, OSError):
        return MiniserverProbe(ProbeOutcome.NO_CONNECTION)

    serial = _first(_SERIAL, body)
    firmware = _first(_FIRMWARE, body)
    if serial is None and firmware is None:
        return MiniserverProbe(ProbeOutcome.NOT_A_MINISERVER)
    return MiniserverProbe(ProbeOutcome.FOUND, serial=serial, firmware=firmware)


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None
