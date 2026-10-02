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

"""The pairing code, decoded in the bridge (design 2026-10-02, section 5).

The browser has decoded it since the commissioning-feedback design
(`decodePairingCode` in app.js); the queue lives in the bridge, so the
bridge needs its own decoder. Pure functions, no I/O. The passcode is
read only as far as the format requires and never leaves this module:
`SetupPayload` has no field for it, and no error text quotes the code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from loxmatter.matter.commissioning_progress import Discriminator

_BASE38: Final = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-."
# Discovery capabilities (Matter core spec 5.1.3.1): bit 1 BLE, bit 2 on IP network.
_CAP_BLE: Final = 1 << 1
_CAP_ON_NETWORK: Final = 1 << 2

_VERHOEFF_D: Final = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P: Final = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


class UnreadableCodeError(ValueError):
    """Not a Matter pairing code. The message never quotes the input."""


class TypoError(UnreadableCodeError):
    """A manual code whose check digit does not match."""


@dataclass(frozen=True)
class SetupPayload:
    kind: Literal["long", "short"]
    discriminator: int
    vendor_id: int | None
    product_id: int | None
    ble: bool | None
    on_network: bool | None


def _base38_bytes(text: str) -> list[int]:
    out: list[int] = []
    for start in range(0, len(text), 5):
        chunk = text[start : start + 5]
        count = {5: 3, 4: 2, 2: 1}.get(len(chunk))
        if count is None:
            raise UnreadableCodeError("not a Matter QR payload")
        value = 0
        for char in reversed(chunk):
            digit = _BASE38.find(char)
            if digit < 0:
                raise UnreadableCodeError("not a Matter QR payload")
            value = value * 38 + digit
        for _ in range(count):
            out.append(value & 0xFF)
            value >>= 8
    return out


def _bits(data: list[int], start: int, length: int) -> int:
    value = 0
    for index in range(length):
        bit = start + index
        if (data[bit >> 3] >> (bit & 7)) & 1:
            value |= 1 << index
    return value


def _verhoeff_valid(digits: str) -> bool:
    check = 0
    for position, char in enumerate(reversed(digits)):
        check = _VERHOEFF_D[check][_VERHOEFF_P[position % 8][int(char)]]
    return check == 0


def decode(code: str) -> SetupPayload:
    text = code.strip()
    if text[:3].upper() == "MT:":
        data = _base38_bytes(text[3:].upper())
        if len(data) < 11:
            raise UnreadableCodeError("not a Matter QR payload")
        capabilities = _bits(data, 37, 8)
        return SetupPayload(
            kind="long",
            discriminator=_bits(data, 45, 12),
            vendor_id=_bits(data, 3, 16),
            product_id=_bits(data, 19, 16),
            ble=bool(capabilities & _CAP_BLE),
            on_network=bool(capabilities & _CAP_ON_NETWORK),
        )
    digits = "".join(char for char in text if char not in " -")
    if not digits.isdigit() or len(digits) not in (11, 21):
        raise UnreadableCodeError("not a Matter pairing code")
    if not _verhoeff_valid(digits):
        raise TypoError("check digit does not match")
    chunk1 = int(digits[0])
    chunk2 = int(digits[1:6])
    long_form = bool((chunk1 >> 2) & 1)
    if long_form != (len(digits) == 21):
        raise UnreadableCodeError("not a Matter pairing code")
    vendor_id = int(digits[10:15]) if long_form else None
    product_id = int(digits[15:20]) if long_form else None
    return SetupPayload(
        kind="short",
        discriminator=((chunk1 & 0x3) << 2) | (chunk2 >> 14),
        vendor_id=vendor_id,
        product_id=product_id,
        ble=None,
        on_network=None,
    )


def discriminator_for(payload: SetupPayload) -> Discriminator:
    return Discriminator(payload.discriminator, payload.kind)
