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
"""Which stick is which, from the USB inventory alone (design 2026-09-12
section 7, research A.1/A.4).

Ported from Zigbee2MQTT's table, which is the only maintained one of its
kind. Every row below is a real product.

The two rows that matter most are the ones MEASURED on the maintainer's Pi
on 12 September 2026, because they are the whole argument for fingerprinting
by name. Both of his sticks report `10c4:ea60` - a bare Silicon Labs CP210x
UART bridge - and both sit at major 188. `lsusb` cannot tell them apart,
`/dev/ttyUSB*` cannot tell them apart, and only the by-id string can:

    usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf...-if00-port0  (ttyUSB1)
    usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a...-if00-port0              (ttyUSB0)

The first is his ZIGBEE coordinator. The second is his THREAD stick, and
`RADIO_DEVICE=/dev/ttyUSB0` in the live stack's `.env` confirms it. Note
what that means for this module: the MG24 row below is CORRECT and stays -
an MG24 genuinely is an EZSP coordinator, and is one for other people - so
this table will happily fingerprint the maintainer's Thread stick as a
usable Zigbee radio. That is not this module's bug to fix. Nothing here
knows what an installation is currently USING a stick for; keeping the
Thread stick out of the picker is Task 11's job, and it is done by resolved
major:minor against the configured Thread device, not by model name."""

from __future__ import annotations

import pytest

from loxmatter.radios.fingerprints import ambiguous_vid_pids, match_fingerprint, table
from loxmatter.radios.inventory import SerialRadio


def _stick(by_id: str, vid_pid: str | None, manufacturer: str | None = None) -> SerialRadio:
    return SerialRadio(
        path=f"/dev/serial/by-id/{by_id}",
        tty="ttyUSB0",
        manufacturer=manufacturer,
        product=None,
        serial=None,
        vid_pid=vid_pid,
    )


# MEASURED on the maintainer's Raspberry Pi, 12 September 2026, verbatim
# from `ls -l /dev/serial/by-id/`, which returns exactly these two entries.
# They are the ONLY by-id strings in this suite known to exist; every other
# row is shaped like a real one but was written for the plan.
REAL_ITEAD = (
    "usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_e8bf16ad5953ef11844a28e0174bec31-if00-port0"
)
REAL_MG24 = "usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a7d9118f9ef118f7767135c2a50c9-if00-port0"


@pytest.mark.parametrize(
    ("by_id", "vid_pid", "manufacturer", "radio_type", "baudrate", "flow_control"),
    [
        (
            "usb-Nabu_Casa_Home_Assistant_Connect_ZBT-1_1234-if00-port0",
            "10c4:ea60",
            "Nabu Casa",
            "ezsp",
            115200,
            "hardware",
        ),
        ("usb-Nabu_Casa_ZBT-2_abcd-if00", "303a:4001", "Nabu Casa", "ezsp", 460800, "hardware"),
        ("usb-Nabu_Casa_ZBT-2_abcd-if00", "303a:831a", "Nabu Casa", "ezsp", 460800, "hardware"),
        # MEASURED, 12 September 2026: the maintainer's Zigbee coordinator,
        # verbatim from `ls -l /dev/serial/by-id/`. `manufacturer` is passed
        # as None on both measured rows because the sysfs `manufacturer`
        # attribute was NOT captured - `lsusb` reported "Silicon Labs",
        # which is the bridge chip's descriptor, not the product's. The
        # ZBDongle-E V2 row carries no manufacturer constraint, so the value
        # cannot affect the result; do not add such a constraint on the
        # strength of an lsusb string that names the wrong vendor.
        (REAL_ITEAD, "10c4:ea60", None, "ezsp", 115200, "software"),
        # The CH9102 revision of the same product. Still invented.
        (
            "usb-Itead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_V2_9f1c2d-if00-port0",
            "1a86:55d4",
            None,
            "ezsp",
            115200,
            "software",
        ),
        # MEASURED, 12 September 2026: the maintainer's THREAD stick. It is
        # fingerprinted as a perfectly good EZSP coordinator, and that is
        # the RIGHT answer for this module - an MG24 is one. What must never
        # happen is offering it, and that is enforced in Task 11 against the
        # configured Thread device, not here. Do not "fix" this by deleting
        # the row: it would break the MG24 for every user who really does
        # run one as their Zigbee coordinator.
        (REAL_MG24, "10c4:ea60", None, "ezsp", 115200, "software"),
        (
            "usb-SONOFF_Zigbee_3.0_USB_Dongle_Max_MG24_77aabb-if00-port0",
            "10c4:ea60",
            "SONOFF",
            "ezsp",
            115200,
            "software",
        ),
        (
            "usb-ITead_Sonoff_Zigbee_3.0_USB_Dongle_Plus_ab12cd34-if00-port0",
            "10c4:ea60",
            "ITEAD",
            "znp",
            115200,
            "software",
        ),
        (
            "usb-SMLIGHT_SLZB-06M_1122-if00-port0",
            "10c4:ea60",
            "SMLIGHT",
            "ezsp",
            115200,
            "software",
        ),
        (
            "usb-SMLIGHT_SLZB-06p7_3344-if00-port0",
            "10c4:ea60",
            "SMLIGHT",
            "znp",
            115200,
            "software",
        ),
        # The one genuine ordering hazard among the SMLIGHT rows: `SLZB-07`
        # (EZSP) is tried BEFORE `SLZB-...07p7` (ZNP), and only the trailing
        # `_` in `.*slzb-07(mg24)?_.*` keeps the EZSP row off this stick.
        # Getting it wrong opens a ZNP stick as EZSP.
        (
            "usb-SMLIGHT_SLZB-07p7_5566-if00-port0",
            "10c4:ea60",
            "SMLIGHT",
            "znp",
            115200,
            "software",
        ),
        (
            "usb-SMLIGHT_SLZB-07_4455-if00-port0",
            "10c4:ea60",
            "SMLIGHT",
            "ezsp",
            115200,
            "hardware",
        ),
        (
            "usb-dresden_elektronik_ConBee_II_DE123-if00",
            "1cf1:0030",
            "dresden elektronik",
            "deconz",
            115200,
            "software",
        ),
        (
            "usb-dresden_elektronik_ConBee_III_DE456-if00",
            "0403:6015",
            "dresden elektronik",
            "deconz",
            115200,
            "software",
        ),
    ],
)
def test_every_row_of_the_table_matches_its_own_stick(
    by_id, vid_pid, manufacturer, radio_type, baudrate, flow_control
):
    """Fault to prove it: change the SONOFF ZBDongle-P row's radio type to
    `ezsp`. A ZNP stick is then opened as an EmberZNet NCP and answers
    nothing, and the user is told their coordinator is broken."""
    found = match_fingerprint(_stick(by_id, vid_pid, manufacturer))
    assert found is not None, by_id
    assert (found.radio_type, found.baudrate, found.flow_control) == (
        radio_type,
        baudrate,
        flow_control,
    )


def test_the_shared_vid_pid_never_matches_on_its_own():
    """`10c4:ea60` is a plain Silicon Labs CP210x and is shared by at least
    six sticks in this table. Z2M refuses a VID:PID-only match for it and so
    does this.

    This is no longer an argument from the Z2M source: it is MEASURED. On
    the maintainer's Pi, `lsusb` reports BOTH of his sticks as
    `ID 10c4:ea60 Silicon Labs CP210x UART Bridge` - his Zigbee coordinator
    and his live Thread border router - and both sit at major 188. On the
    only hardware this project has, VID:PID is provably incapable of telling
    a Zigbee coordinator from a Thread stick, so nothing may ever be decided
    from it alone.

    Fault to prove it: allow the VID:PID-only match. The bare CP210x below
    is then reported as whichever `10c4:ea60` row happens to come first -
    and if that row says ZNP, opening it toggles DTR/RTS and RESETS the
    chip (research A.2)."""
    assert match_fingerprint(_stick("usb-Some_Other_CP210x_Bridge-if00", "10c4:ea60")) is None


def test_an_unknown_stick_is_unknown_and_not_a_guess():
    """Fault to prove it: return `DEFAULT_UNKNOWN` from `match_fingerprint`
    instead of `None`. The card then presents a guess as a detection, and
    the Advanced disclosure that exists to let the user say what the stick
    really is never appears."""
    assert match_fingerprint(_stick("usb-Totally_Unknown_Thing-if00", "dead:beef")) is None
    assert match_fingerprint(_stick("usb-No_Vid_Pid_At_All-if00", None)) is None


def test_no_ambiguous_row_may_rely_on_the_vid_pid_alone():
    """A structural guard on the TABLE, not on one lookup: any row whose
    VID:PID is in the conflict-prone set must carry a path pattern, or the
    rule above is only as good as whoever adds the next row remembers.

    Fault to prove it: add a row for `10c4:ea60` with `path_pattern=None`."""
    for entry in table():
        if entry.vid_pids & ambiguous_vid_pids():
            assert entry.path_pattern is not None, entry.name


def test_the_matcher_never_opens_the_port():
    """The whole point of fingerprinting (research A.2/A.4). `SerialRadio`
    carries no handle and this module imports nothing that could open one -
    asserted structurally, because a test cannot easily observe an open that
    does not happen.

    Fault to prove it: import `serial`/`bellows` in fingerprints.py."""
    import loxmatter.radios.fingerprints as module

    source = __import__("inspect").getsource(module)
    for forbidden in ("import serial", "import bellows", "import zigpy", "open("):
        assert forbidden not in source, forbidden


def test_the_table_matches_whatever_case_the_kernel_used():
    """MEASURED difference, 12 September 2026: the real stick spells itself
    `Itead_Sonoff`, while every string invented for this plan spelled it
    `ITEAD_SONOFF`. The matcher survives that only because
    `match_fingerprint` lowercases the path before applying the patterns and
    every pattern is written lowercase - which NO test pinned. A later
    "simplification" that dropped the `.lower()` would have kept every
    invented row green while silently losing the one coordinator the
    maintainer actually owns.

    Fault to prove it: remove `.lower()` from `radio.path.lower()` in
    `match_fingerprint`. The mixed-case spelling below stops matching."""
    for spelling in (
        REAL_ITEAD,
        REAL_ITEAD.upper(),
        REAL_ITEAD.lower(),
    ):
        found = match_fingerprint(_stick(spelling, "10c4:ea60"))
        assert found is not None, spelling
        assert found.radio_type == "ezsp", spelling


def test_the_two_sticks_on_the_maintainers_pi_are_told_apart_by_name_alone():
    """The measurement this whole module exists for (12 September 2026).

    Both sticks report `10c4:ea60` and both sit at major 188, so VID:PID,
    `lsusb` and the device number are each incapable of separating them. A
    matcher keyed on any of those would be a coin flip between his Zigbee
    coordinator and his live Thread border router. Only the by-id name
    works, which is why this table is keyed on it.

    Both come back as EZSP coordinators, and that is the CORRECT answer
    here: an MG24 genuinely is one, for anybody who runs it as one. Refusing
    to OFFER the MG24 is Task 11's job and is decided against the configured
    Thread device, never against this table.

    Fault to prove it: key the matcher on `vid_pid` alone. Both sticks then
    return the same row and the names compare equal."""
    itead = match_fingerprint(_stick(REAL_ITEAD, "10c4:ea60"))
    mg24 = match_fingerprint(_stick(REAL_MG24, "10c4:ea60"))
    assert itead is not None and mg24 is not None
    assert itead.name != mg24.name
    assert (itead.radio_type, mg24.radio_type) == ("ezsp", "ezsp")
