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

"""Convert raw Matter values into what the Miniserver expects.

Two rules from spec 7.3 shape this module:

The target unit is that of the Loxone block, not the SI unit. The energy
manager expects kW, so we deliver kW - even though Matter measures in
milliwatts.

And the number format follows from that: from mW to kW is six orders of
magnitude. Rounding to two decimal places here would make every consumer
under 10 W appear as 0 - and it is precisely the small continuous
consumers that are often the reason for installing a metering plug.
"""

from __future__ import annotations

from loxmatter.matter.models import SignalRef
from loxmatter.profiles.table import Exportability, classify, scale_factor, struct_member

MAX_DECIMALS = 6


def to_loxone_value(ref: SignalRef, raw: object) -> float | bool | None:
    """Scaled value, or None if Loxone cannot accept it."""
    raw = struct_member(ref, raw)
    kind = classify(raw)
    if kind is Exportability.DIGITAL:
        return bool(raw)
    if kind is not Exportability.ANALOG:
        return None
    assert isinstance(raw, (int, float))
    return float(raw) * scale_factor(ref)


def format_value(value: float | bool) -> str:
    """Text form for the datagram: up to six decimal places, no trailing zeros.

    A value that rounds to zero is always emitted as "0" - regardless of sign.
    Otherwise a negative rounding remainder like -1e-07 would let a "-0" through,
    which would simply be wrong in the Loxone visualization.
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    text = f"{value:.{MAX_DECIMALS}f}".rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0"
    return text


def datagram(key: str, value: float | bool) -> bytes:
    """A UDP datagram in the form that the exported template recognizes."""
    return f"{key}:{format_value(value)}".encode()
