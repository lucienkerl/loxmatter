"""The bridge's own scan (design 2026-10-02, section 6.1). The call order
is the one measured on the test Pi on October 2, 2026.

Also measured there: BlueZ ties `SetDiscoveryFilter` and `StartDiscovery`
to the D-Bus connection that called them and ends that discovery when the
connection closes, and on 29 September 2026 that it clears every device's
`RSSI` once discovery stops. So every call of one scan goes through one
connection, and the objects are read before `StopDiscovery`."""

import asyncio

import pytest

from loxmatter.radios.bluez import (
    MATTER_SERVICE_UUID,
    BluezScanError,
    BluezScanner,
    BluezSnapshot,
)

LAMP_SERVICE_DATA = bytes.fromhex("0023047c11019000")


def _objects(*, rssi):
    device = {
        "Address": "AA:BB:CC:DD:EE:01",
        "ServiceData": {MATTER_SERVICE_UUID: LAMP_SERVICE_DATA},
        "Connected": False,
        "Adapter": "/org/bluez/hci0",
    }
    if rssi is not None:
        device["RSSI"] = rssi
    return {
        "/org/bluez/hci0": {"org.bluez.Adapter1": {"Powered": True, "Discovering": True}},
        "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01": {"org.bluez.Device1": device},
    }


class Connection:
    """One D-Bus connection. Like BlueZ, it forgets every RSSI once
    discovery stops - an object read after `StopDiscovery` has none."""

    def __init__(self, bus, number):
        self.bus = bus
        self.number = number
        self.closed = False
        self.discovering = False

    async def call(self, path, interface, member, signature, body):
        assert not self.closed, "a call on a closed connection"
        self.bus.made.append((self.number, path, interface, member, signature, body))
        if member == self.bus.fail_on:
            raise self.bus.fail_with(f"{member} refused")
        if member == "StartDiscovery":
            self.discovering = True
        elif member == "StopDiscovery":
            self.discovering = False
        elif member == "GetManagedObjects":
            return [_objects(rssi=-60 if self.discovering else None)]
        return []

    def disconnect(self):
        self.closed = True
        self.discovering = False


class Bus:
    def __init__(self, fail_on=None, fail_with=BluezScanError):
        self.made = []
        self.connections = []
        self.fail_on = fail_on
        self.fail_with = fail_with

    async def connect(self):
        connection = Connection(self, len(self.connections))
        self.connections.append(connection)
        return connection

    def members(self):
        return [made[3] for made in self.made]


async def noop(seconds):
    pass


async def test_one_connection_filters_starts_waits_reads_and_stops():
    bus = Bus()
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    await BluezScanner(bus.connect, sleep=sleep).scan()
    assert bus.members() == [
        "SetDiscoveryFilter",
        "StartDiscovery",
        "GetManagedObjects",
        "StopDiscovery",
    ]
    assert len(bus.connections) == 1
    assert {made[0] for made in bus.made} == {0}
    assert bus.connections[0].closed
    filter_body = bus.made[0][5][0]
    assert filter_body["Transport"].value == "le"
    assert filter_body["UUIDs"].value == [MATTER_SERVICE_UUID]
    assert slept == [10.0]
    adapter_calls = [made for made in bus.made if made[3] != "GetManagedObjects"]
    assert all(
        made[1] == "/org/bluez/hci0" and made[2] == "org.bluez.Adapter1" for made in adapter_calls
    )


async def test_the_result_is_read_while_discovery_still_runs():
    """BlueZ clears every RSSI when discovery stops, and the reader drops a
    device without one: read after the stop, the scan would find nothing."""
    snapshot = await BluezScanner(Bus().connect, sleep=noop).scan()
    assert isinstance(snapshot, BluezSnapshot)
    assert [advert.address for advert in snapshot.adverts] == ["AA:BB:CC:DD:EE:01"]
    assert snapshot.adverts[0].rssi == -60
    assert snapshot.adverts[0].discriminator == 1059


async def test_stop_runs_and_the_connection_closes_when_the_wait_is_cancelled():
    bus = Bus()

    async def sleep(seconds):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await BluezScanner(bus.connect, sleep=sleep).scan()
    assert bus.members()[-1] == "StopDiscovery"
    assert bus.connections[0].closed


async def test_a_refused_start_raises_does_not_stop_and_closes():
    bus = Bus(fail_on="StartDiscovery")
    with pytest.raises(BluezScanError):
        await BluezScanner(bus.connect, sleep=noop).scan()
    assert bus.members() == ["SetDiscoveryFilter", "StartDiscovery"]
    assert bus.connections[0].closed


async def test_a_refused_filter_raises_and_does_not_start():
    bus = Bus(fail_on="SetDiscoveryFilter")
    with pytest.raises(BluezScanError):
        await BluezScanner(bus.connect, sleep=noop).scan()
    assert bus.members() == ["SetDiscoveryFilter"]
    assert bus.connections[0].closed


async def test_a_refused_stop_still_returns_the_result():
    bus = Bus(fail_on="StopDiscovery")
    snapshot = await BluezScanner(bus.connect, sleep=noop).scan()
    assert bus.members()[-1] == "StopDiscovery"
    assert [advert.address for advert in snapshot.adverts] == ["AA:BB:CC:DD:EE:01"]
    assert bus.connections[0].closed


async def test_a_failed_stop_never_masks_a_cancellation():
    bus = Bus(fail_on="StopDiscovery", fail_with=OSError)

    async def sleep(seconds):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await BluezScanner(bus.connect, sleep=sleep).scan()
    assert bus.members()[-1] == "StopDiscovery"


async def test_a_failed_read_raises_after_stopping():
    bus = Bus(fail_on="GetManagedObjects")
    with pytest.raises(BluezScanError):
        await BluezScanner(bus.connect, sleep=noop).scan()
    assert bus.members()[-1] == "StopDiscovery"
    assert bus.connections[0].closed
