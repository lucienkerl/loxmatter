"""The bridge's own scan (design 2026-10-02, section 6.1). The call order
is the one measured on the test Pi on October 2, 2026."""

import pytest

from loxmatter.radios.bluez import MATTER_SERVICE_UUID, BluezScanError, BluezScanner


class Calls:
    def __init__(self, fail_on=None, fail_with_exception=None):
        self.made = []
        self.fail_on = fail_on
        self.fail_with_exception = fail_with_exception or BluezScanError

    async def __call__(self, path, interface, member, signature, body):
        self.made.append((path, interface, member, signature, body))
        if member == self.fail_on:
            raise self.fail_with_exception(f"{member} refused")


async def test_scan_filters_starts_waits_and_stops():
    calls = Calls()
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    await BluezScanner(calls, sleep=sleep).scan()
    members = [made[2] for made in calls.made]
    assert members == ["SetDiscoveryFilter", "StartDiscovery", "StopDiscovery"]
    filter_body = calls.made[0][4][0]
    assert filter_body["Transport"].value == "le"
    assert filter_body["UUIDs"].value == [MATTER_SERVICE_UUID]
    assert slept == [10.0]
    assert all(
        made[0] == "/org/bluez/hci0" and made[1] == "org.bluez.Adapter1" for made in calls.made
    )


async def test_stop_runs_even_when_the_wait_is_cancelled():
    import asyncio

    calls = Calls()

    async def sleep(seconds):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await BluezScanner(calls, sleep=sleep).scan()
    assert calls.made[-1][2] == "StopDiscovery"


async def test_a_refused_start_raises_and_does_not_stop():
    calls = Calls(fail_on="StartDiscovery")

    async def noop(s):
        pass

    with pytest.raises(BluezScanError):
        await BluezScanner(calls, sleep=noop).scan()
    assert [made[2] for made in calls.made] == ["SetDiscoveryFilter", "StartDiscovery"]


async def test_bluez_scan_error_on_stop_returns_normally():
    """With StopDiscovery raising BluezScanError, scan() returns normally."""
    calls = Calls(fail_on="StopDiscovery", fail_with_exception=BluezScanError)

    async def noop(s):
        pass

    # Should not raise; the exception from StopDiscovery is logged and suppressed
    await BluezScanner(calls, sleep=noop).scan()
    assert calls.made[-1][2] == "StopDiscovery"


async def test_os_error_on_stop_with_cancelled_sleep_raises_cancelled():
    """With StopDiscovery raising OSError and sleep raising CancelledError,
    scan() raises CancelledError (not OSError)."""
    import asyncio

    calls = Calls(fail_on="StopDiscovery", fail_with_exception=OSError)

    async def sleep(seconds):
        raise asyncio.CancelledError

    # CancelledError from sleep should be preserved and re-raised,
    # not replaced by OSError from StopDiscovery
    with pytest.raises(asyncio.CancelledError):
        await BluezScanner(calls, sleep=sleep).scan()
    assert calls.made[-1][2] == "StopDiscovery"


async def test_refused_set_discovery_filter_raises_and_does_not_start():
    """A refused SetDiscoveryFilter raises BluezScanError and
    StartDiscovery is never called."""
    calls = Calls(fail_on="SetDiscoveryFilter")

    async def noop(s):
        pass

    with pytest.raises(BluezScanError):
        await BluezScanner(calls, sleep=noop).scan()
    assert [made[2] for made in calls.made] == ["SetDiscoveryFilter"]
