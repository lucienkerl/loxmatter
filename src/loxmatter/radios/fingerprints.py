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
"""Which radio a USB stick carries, decided from the inventory alone.

**Fingerprint, never probe on our own initiative** (design 2026-09-12
section 7, research A.2/A.4). The reasons come from other projects' source
and reports, not from a measurement made here:

- Home Assistant's full auto-probe chain costs about 34 s on a stick that
  answers nothing, which is also what a wrong-type stick looks like.
- zigpy-znp toggles DTR/RTS and then blasts 256 bootloader-skip bytes. On
  every CP2102N/CH9102 dongle those pins are wired to RESET/BOOT, so
  probing RESETS THE CHIP.
- Merely opening a tty on Linux asserts DTR/RTS, so even a probe that
  writes nothing can reset such a stick.

A stick that resets itself because somebody opened the settings card is not
acceptable, so nothing here opens a port. The table is ported from
Zigbee2MQTT's `adapterDiscovery.ts`, which is the only maintained table of
its kind; the columns loxmatter already collects per stick
(`radios.inventory.SerialRadio`) are exactly the ones it needs.

**`10c4:ea60` must never match on VID:PID alone.** It is a plain Silicon
Labs CP210x bridge, shared by at least six of the sticks below, and Z2M
explicitly refuses a VID:PID-only match for it. Only the by-id string tells
them apart; a `10c4:ea60` with no telling by-id string is UNKNOWN, not a
guess. `_AMBIGUOUS_VID_PIDS` plus the structural test in
`tests/radios/test_fingerprints.py` keep that true for rows nobody has
written yet.

**This is measured, not inherited from Z2M.** On the maintainer's Pi
(12 September 2026) `lsusb` reports BOTH attached sticks as
`ID 10c4:ea60 Silicon Labs CP210x UART Bridge`, and both sit at major 188 -
yet one is his Zigbee coordinator and the other is the radio his live Thread
border router is running on. On the only hardware this project has, the
USB vendor/product id cannot distinguish a Zigbee coordinator from a Thread
stick, a serial console or a 3D printer. So: **never key anything on
`vid_pid` alone, and never "improve" this matcher by doing so.** The
vid_pid column narrows a candidate set; the by-id name decides. The
`usb-Some_Other_CP210x_Bridge-if00` negative case in the test file is that
rule's guard, and `test_the_two_sticks_on_the_maintainers_pi_are_told_apart_by_name_alone`
is its measured witness.

Flow control comes from this table too, not from a probe: bellows maps
`None` to XON/XOFF and anything else to RTS/CTS, and ASH escapes 0x11/0x13,
so software flow control is safe (research A.4 item 5). Z2M's own column
spells the software case "none"; it is spelled `"software"` here because
that is what bellows actually does with it, and a third state bellows has no
concept of would be an invention.

An explicit, user-initiated "Test this stick" probe is deliberately NOT
here - it is 2b, and when it lands it must never run against the Thread
stick and never at startup.

This module answers "what radio does this chip speak", never "is this stick
free to use". A stick recognised here as a Zigbee coordinator may currently
be running as somebody's Thread border router - that is expected, and it is
not this module's concern. Whether a recognised stick should be OFFERED for
Zigbee pairing is decided elsewhere, against the installation's configured
Thread device, not against this table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from loxmatter.radios.inventory import SerialRadio

RadioType = Literal["ezsp", "znp", "deconz"]
FlowControl = Literal["hardware", "software"]


@dataclass(frozen=True)
class Fingerprint:
    """What a recognised stick is, and how to open it."""

    name: str
    radio_type: RadioType
    baudrate: int
    flow_control: FlowControl


@dataclass(frozen=True)
class _Entry:
    name: str
    vid_pids: frozenset[str]
    radio_type: RadioType
    baudrate: int
    flow_control: FlowControl
    # Matched case-insensitively against the by-id path. Mandatory for any
    # entry whose VID:PID is in `_AMBIGUOUS_VID_PIDS`.
    path_pattern: str | None = None
    manufacturer: str | None = None


# VID:PIDs that identify a generic USB-serial bridge rather than a product.
# An entry using one of these MUST carry a path pattern.
_AMBIGUOUS_VID_PIDS: Final[frozenset[str]] = frozenset({"10c4:ea60"})

# ORDER MATTERS, most specific first. Three of the SONOFF rows share
# `10c4:ea60` and overlapping names: `..._Plus_V2_...` (EZSP) and
# `..._Plus_MG24...` (EZSP) must both be tried before the plain
# `..._Plus_...` row (ZNP), which is why that last one also carries a
# negative lookahead. Getting this order wrong opens a ZNP stick as EZSP, or
# worse, an EZSP stick as ZNP - and a ZNP open resets the chip.
_TABLE: Final[tuple[_Entry, ...]] = (
    _Entry(
        name="Home Assistant Connect ZBT-1",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="hardware",
        path_pattern=r".*nabu_casa.*_zbt-1.*",
        manufacturer="Nabu Casa",
    ),
    _Entry(
        name="Home Assistant Connect ZBT-2",
        vid_pids=frozenset({"303a:4001", "303a:831a"}),
        radio_type="ezsp",
        baudrate=460800,
        flow_control="hardware",
        path_pattern=r".*nabu_casa_zbt-2.*",
        manufacturer="Nabu Casa",
    ),
    _Entry(
        name="SONOFF ZBDongle-E V2",
        vid_pids=frozenset({"1a86:55d4", "10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*sonoff.*plus_v2_.*",
    ),
    # MEASURED CAUTION: this row matches the maintainer's own stick, which
    # on his Pi is running THREAD, not Zigbee (`RADIO_DEVICE` points at it).
    # The row is still correct - an MG24 is a real EZSP coordinator and is
    # one for other users - and must NOT be deleted to protect him. This
    # module answers "what radio does this chip speak", never "is this stick
    # free to use". The second question belongs to a later task, decided
    # against the configured Thread device, by resolved major:minor.
    _Entry(
        name="SONOFF Zigbee Dongle Plus MG24",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*sonoff.*plus.*mg24.*",
    ),
    _Entry(
        name="SONOFF Zigbee Dongle Max MG24",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*sonoff.*max.*",
    ),
    _Entry(
        name="SONOFF Zigbee Dongle Lite MG21",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*lite.*mg21.*",
    ),
    _Entry(
        name="SLZB-06M",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*slzb-06m.*",
    ),
    _Entry(
        name="SLZB-07",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="ezsp",
        baudrate=115200,
        flow_control="hardware",
        path_pattern=r".*slzb-07(mg24)?_.*",
    ),
    _Entry(
        name="SLZB-06p7 / 06p10 / 07p7",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="znp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*slzb-0(6p7|6p10|7p7)_.*",
    ),
    # LAST of the SONOFF rows: the lookahead keeps it off the V2 stick,
    # and the MG24/Max/Lite rows above have already claimed theirs.
    _Entry(
        name="SONOFF ZBDongle-P",
        vid_pids=frozenset({"10c4:ea60"}),
        radio_type="znp",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*sonoff.*plus(?!_v2_)(?!.*mg24).*",
    ),
    _Entry(
        name="ConBee II",
        vid_pids=frozenset({"1cf1:0030"}),
        radio_type="deconz",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*conbee.*",
    ),
    _Entry(
        name="ConBee III",
        vid_pids=frozenset({"0403:6015"}),
        radio_type="deconz",
        baudrate=115200,
        flow_control="software",
        path_pattern=r".*conbee.*",
    ),
)

# What the Advanced disclosure pre-fills for a stick this table does not
# know (design section 7). EZSP at 115200 because that is what the great
# majority of current coordinators are - offered as an editable starting
# point the user can correct, never as a detection.
DEFAULT_UNKNOWN: Final = Fingerprint(
    name="", radio_type="ezsp", baudrate=115200, flow_control="software"
)


def table() -> tuple[_Entry, ...]:
    """The raw table, for the structural test that guards it."""
    return _TABLE


def ambiguous_vid_pids() -> frozenset[str]:
    return _AMBIGUOUS_VID_PIDS


def match_fingerprint(radio: SerialRadio) -> Fingerprint | None:
    """The stick's radio type, baud rate and flow control - or `None`.

    `None` means "loxmatter does not recognise this stick", which the UI
    says in plain words and follows with an Advanced disclosure. It never
    means "probably EZSP": a wrong guess here opens a port with the wrong
    driver, and for ZNP that resets the chip.
    """
    if radio.vid_pid is None:
        return None
    vid_pid = radio.vid_pid.lower()
    path = radio.path.lower()
    manufacturer = (radio.manufacturer or "").lower()
    for entry in _TABLE:
        if vid_pid not in entry.vid_pids:
            continue
        if entry.path_pattern is not None:
            if not re.fullmatch(entry.path_pattern, path):
                continue
        elif vid_pid in _AMBIGUOUS_VID_PIDS:
            # Unreachable while the structural test holds; kept as a
            # runtime guarantee rather than a comment, because the cost of
            # being wrong here is a reset coordinator.
            continue
        if entry.manufacturer is not None and entry.manufacturer.lower() not in manufacturer:
            continue
        return Fingerprint(
            name=entry.name,
            radio_type=entry.radio_type,
            baudrate=entry.baudrate,
            flow_control=entry.flow_control,
        )
    return None
