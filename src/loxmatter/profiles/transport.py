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

"""How a device is connected: Thread, IP or Zigbee (design 2026-09-11,
section 5).

Sits next to `categories.py` for the reason `categories.py` exists: the
answer is derived when read, from a raw value the store keeps
(`device.network_features`), so a better rule later is a code change and
not a migration.

**The evidence**, read from the live matter-server on the test Pi on 11
September 2026:

| Nodes | Devices | Actual link | `0/49/65532` |
|---|---|---|---|
| 4, 8, 11-16, 21, 22 | IKEA BILRESA, GRILLPLATS, MYGGBETT, ALPSTUGA, MYGGSPRAY, KAJPLATS (x3), TIMMERFLOTTE, KLIPPBOK | Thread | 2 |
| 23 | Tasmota-Plug-4 | Wi-Fi | 4 (Ethernet bit) |
| 24 | Tasmota-Plug-6 | Wi-Fi | 5 (Wi-Fi and Ethernet bits) |

NetworkCommissioning's FeatureMap has bit 0 for Wi-Fi, bit 1 for Thread,
bit 2 for Ethernet. Both Tasmota plugs are on Wi-Fi and neither says so
correctly - so Wi-Fi and Ethernet are not told apart here, only Thread and
IP.

**Thread wins when both kinds of bit are set.** That is a judgement, not a
measurement: no device reporting the Thread bit together with an IP bit
has been seen, but the Tasmota plugs show IP bits set without meaning,
while no false Thread bit has been observed. Revisit the rule, not the
stored data, when a counterexample turns up.
"""

from __future__ import annotations

from typing import Final, Literal

from loxmatter.matter.models import NodeSnapshot

Transport = Literal["thread", "ip", "zigbee"]

# Endpoint 0, NetworkCommissioning (0x0031), FeatureMap (0xFFFC). Discovery
# never turns this into a signal - FeatureMap is one of the global
# attributes `extract_signals` skips - so it is read here directly.
NETWORK_FEATURES_PATH: Final = "0/49/65532"

_WIFI_BIT: Final = 0x1
_THREAD_BIT: Final = 0x2
_ETHERNET_BIT: Final = 0x4


def network_features_of(snapshot: NodeSnapshot) -> int | None:
    """The raw FeatureMap, or `None` when the device reports none.

    `bool` is excluded explicitly: it is a subclass of `int` in Python, and
    `True` would otherwise read as "Wi-Fi"."""
    value = snapshot.attributes.get(NETWORK_FEATURES_PATH)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def transport_for(technology: str, network_features: int | None) -> Transport | None:
    if technology == "zigbee":
        return "zigbee"
    if network_features is None:
        return None
    if network_features & _THREAD_BIT:
        return "thread"
    if network_features & (_WIFI_BIT | _ETHERNET_BIT):
        return "ip"
    return None
