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
"""Matter commissioning advertisements read from BlueZ (design 2026-09-22,
section 6). The objects are shaped like `GetManagedObjects` after the reader
unwrapped D-Bus variants into plain values. The first device is the lamp
measured on pi3-andi on 21 September 2026 (`bluetoothctl info FB:73:82:07:E3:AD`)."""

from __future__ import annotations

import pytest

from loxmatter.radios.bluez import (
    MATTER_SERVICE_UUID,
    AdapterState,
    BluezReader,
    MatterAdvert,
    parse_matter_service_data,
    snapshot_from_objects,
)

LAMP_SERVICE_DATA = bytes.fromhex("0023047c11019000")

OBJECTS = {
    "/org/bluez/hci0": {
        "org.bluez.Adapter1": {
            "Address": "B8:27:EB:11:D8:9A",
            "Powered": True,
            "Discovering": True,
        },
    },
    "/org/bluez/hci0/dev_FB_73_82_07_E3_AD": {
        "org.bluez.Device1": {
            "Address": "FB:73:82:07:E3:AD",
            "Name": "LED Light0x07C2",
            "RSSI": -60,
            "Connected": False,
            "Adapter": "/org/bluez/hci0",
            "ServiceData": {MATTER_SERVICE_UUID: LAMP_SERVICE_DATA},
        },
    },
    # A phone: manufacturer data only, no Matter service data.
    "/org/bluez/hci0/dev_79_BF_58_D2_5B_80": {
        "org.bluez.Device1": {"Address": "79:BF:58:D2:5B:80", "RSSI": -64, "Connected": False},
    },
    # A Matter advertisement BlueZ has cached but no longer hears: no RSSI.
    "/org/bluez/hci0/dev_E0_55_02_06_D3_79": {
        "org.bluez.Device1": {
            "Address": "E0:55:02:06:D3:79",
            "Connected": False,
            "ServiceData": {MATTER_SERVICE_UUID: bytes.fromhex("0001017c11079000")},
        },
    },
}


def test_the_measured_lamp_advertisement_decodes() -> None:
    assert parse_matter_service_data(LAMP_SERVICE_DATA) == (1059, 4476, 36865)


@pytest.mark.parametrize("data", [b"", bytes(6), bytes.fromhex("0123047c110190")])
def test_short_or_foreign_service_data_reads_as_none(data: bytes) -> None:
    """Fewer than 7 bytes, or an opcode other than 0x00 (commissionable)."""
    assert parse_matter_service_data(data) is None


def test_only_heard_matter_devices_are_listed() -> None:
    snapshot = snapshot_from_objects(OBJECTS)
    assert snapshot.adverts == [
        MatterAdvert(
            address="FB:73:82:07:E3:AD",
            name="LED Light0x07C2",
            rssi=-60,
            discriminator=1059,
            vendor_id=4476,
            product_id=36865,
            connected=False,
            adapter="/org/bluez/hci0",
        )
    ]
    assert snapshot.adapter == AdapterState(name="hci0", powered=True, discovering=True)


def test_adverts_are_sorted_by_signal_strength() -> None:
    objects = dict(OBJECTS)
    objects["/org/bluez/hci0/dev_AA"] = {
        "org.bluez.Device1": {
            "Address": "AA:AA:AA:AA:AA:AA",
            "RSSI": -40,
            "Connected": True,
            "ServiceData": {MATTER_SERVICE_UUID: bytes.fromhex("00d00d7c11079000")},
        }
    }
    addresses = [advert.address for advert in snapshot_from_objects(objects).adverts]
    assert addresses == ["AA:AA:AA:AA:AA:AA", "FB:73:82:07:E3:AD"]


def test_a_zero_rssi_sorts_as_the_strong_signal_it_is() -> None:
    """Final review item 7: the sort key used to be `-(advert.rssi or
    -1000)` - `_advert()` already drops every device without an `RSSI` at
    all (see the "no longer heard" entry in `OBJECTS` above), so by the time
    a `MatterAdvert` reaches this sort its `rssi` is never `None` in
    practice; the only thing `or -1000` could still catch is a `rssi`
    of exactly 0, a legitimate (if unusually strong) signal - and `or`
    treats 0 as falsy, silently replacing it with -1000 dBm, the sort key
    of the WEAKEST possible signal. A device holding its RSSI at 0 would
    then have sorted last instead of first."""
    objects = dict(OBJECTS)
    objects["/org/bluez/hci0/dev_ZERO"] = {
        "org.bluez.Device1": {
            "Address": "00:00:00:00:00:00",
            "RSSI": 0,
            "Connected": False,
            "ServiceData": {MATTER_SERVICE_UUID: bytes.fromhex("00d00d7c11079000")},
        }
    }
    addresses = [advert.address for advert in snapshot_from_objects(objects).adverts]
    assert addresses[0] == "00:00:00:00:00:00"


def test_the_discovering_adapter_is_reported_when_there_are_several() -> None:
    objects = {
        "/org/bluez/hci0": {"org.bluez.Adapter1": {"Powered": True, "Discovering": False}},
        "/org/bluez/hci1": {"org.bluez.Adapter1": {"Powered": True, "Discovering": True}},
    }
    assert snapshot_from_objects(objects).adapter == AdapterState(
        name="hci1", powered=True, discovering=True
    )


async def test_the_reader_returns_the_snapshot() -> None:
    async def fetch():
        return OBJECTS

    snapshot = await BluezReader(fetch).snapshot()
    assert snapshot is not None
    assert [advert.discriminator for advert in snapshot.adverts] == [1059]


async def test_an_unreachable_bus_reads_as_none() -> None:
    """No /run/dbus socket, BlueZ not running, a denied call: all the same
    "not available" (design section 8)."""

    async def fetch():
        raise FileNotFoundError("/run/dbus/system_bus_socket")

    assert await BluezReader(fetch).snapshot() is None
