"""Only one device blinks (design 2026-10-02, section 9)."""

import asyncio

import pytest

from loxmatter.commissioning.identify import IdentifyCoordinator
from loxmatter.sources import DeviceUnreachableError, IdentifyUnsupportedError


class Source:
    def __init__(self):
        self.calls = []
        self.hang = set()
        self.fail = set()

    def supports_identify(self, address):
        return address != "no-identify"

    async def identify(self, address, seconds):
        if address == "no-identify":
            raise IdentifyUnsupportedError("no Identify cluster")
        if address in self.hang:
            await asyncio.Event().wait()
        if (address, seconds) in self.fail:
            raise DeviceUnreachableError("asleep")
        self.calls.append((address, seconds))


def _coordinator(source, sleep=None):
    addresses = {1: ("matter", "11"), 2: ("matter", "12"), 3: ("matter", "no-identify")}
    kwargs = {"sleep": sleep} if sleep else {}
    return IdentifyCoordinator(
        lambda tech: source, lambda device_id: addresses[device_id], **kwargs
    )


async def test_start_blinks_thirty_seconds():
    source = Source()
    coordinator = _coordinator(source)
    await coordinator.start(1, renew=False)
    assert source.calls == [("11", 30)]
    assert coordinator.blinking == 1
    await coordinator.aclose()


async def test_starting_another_stops_the_first():
    source = Source()
    coordinator = _coordinator(source)
    await coordinator.start(1, renew=False)
    await coordinator.start(2, renew=False)
    assert source.calls == [("11", 30), ("11", 0), ("12", 30)]
    assert coordinator.blinking == 2
    await coordinator.aclose()


async def test_stop_sends_zero():
    source = Source()
    coordinator = _coordinator(source)
    await coordinator.start(1, renew=False)
    await coordinator.stop()
    assert source.calls[-1] == ("11", 0)
    assert coordinator.blinking is None


async def test_renewal_every_25_seconds_until_stopped():
    source = Source()
    ticks = asyncio.Queue()

    async def sleep(seconds):
        assert seconds == 25
        await ticks.get()

    coordinator = _coordinator(source, sleep=sleep)
    await coordinator.start(1, renew=True)
    for _ in range(2):
        ticks.put_nowait(None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    assert source.calls == [("11", 30), ("11", 30), ("11", 30)]
    await coordinator.stop()
    assert source.calls[-1] == ("11", 0)


async def test_without_renewal_the_blink_ends_by_itself():
    source = Source()

    async def sleep(seconds):
        assert seconds == 30

    coordinator = _coordinator(source, sleep=sleep)
    await coordinator.start(1, renew=False)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert coordinator.blinking is None
    assert source.calls == [("11", 30)]


async def test_an_unsupported_device_raises_and_blinks_nothing():
    source = Source()
    coordinator = _coordinator(source)
    with pytest.raises(IdentifyUnsupportedError):
        await coordinator.start(3, renew=False)
    assert coordinator.blinking is None


async def test_a_hanging_device_call_is_bounded(monkeypatch):
    monkeypatch.setattr("loxmatter.sources.SOURCE_CALL_TIMEOUT_SECONDS", 0.05)
    source = Source()
    source.hang.add("11")
    coordinator = _coordinator(source)
    with pytest.raises(DeviceUnreachableError):
        await asyncio.wait_for(coordinator.start(1, renew=False), 2)
    assert coordinator.blinking is None
    # The lock is free again.
    await asyncio.wait_for(coordinator.start(2, renew=False), 2)
    assert coordinator.blinking == 2
    await coordinator.aclose()


async def test_a_hanging_stop_is_bounded_and_lets_the_next_start(monkeypatch):
    monkeypatch.setattr("loxmatter.sources.SOURCE_CALL_TIMEOUT_SECONDS", 0.05)
    source = Source()
    coordinator = _coordinator(source)
    await coordinator.start(1, renew=False)
    source.hang.add("11")
    await asyncio.wait_for(coordinator.start(2, renew=False), 2)
    assert coordinator.blinking == 2
    await coordinator.aclose()


async def test_restarting_the_blinking_device_skips_the_stop():
    source = Source()
    coordinator = _coordinator(source)
    await coordinator.start(1, renew=False)
    await coordinator.start(1, renew=False)
    assert source.calls == [("11", 30), ("11", 30)]
    assert coordinator.blinking == 1
    await coordinator.aclose()


async def test_a_failing_stop_of_the_old_device_still_starts_the_new_one():
    source = Source()
    source.fail.add(("11", 0))
    coordinator = _coordinator(source)
    await coordinator.start(1, renew=False)
    await coordinator.start(2, renew=False)
    assert source.calls == [("11", 30), ("12", 30)]
    assert coordinator.blinking == 2
    await coordinator.aclose()


async def test_the_new_device_failing_leaves_nothing_blinking():
    source = Source()
    source.fail.add(("12", 30))
    coordinator = _coordinator(source)
    await coordinator.start(1, renew=False)
    with pytest.raises(DeviceUnreachableError):
        await coordinator.start(2, renew=False)
    assert coordinator.blinking is None


async def test_start_if_idle_blinks_when_nothing_does():
    source = Source()
    coordinator = _coordinator(source)
    assert await coordinator.start_if_idle(1, 3) is True
    assert source.calls == [("11", 3)]
    assert coordinator.blinking == 1
    await coordinator.aclose()


async def test_start_if_idle_leaves_a_running_blink_alone():
    source = Source()
    coordinator = _coordinator(source)
    await coordinator.start(1, renew=False)
    assert await coordinator.start_if_idle(2, 3) is False
    assert source.calls == [("11", 30)]
    assert coordinator.blinking == 1
    await coordinator.aclose()
