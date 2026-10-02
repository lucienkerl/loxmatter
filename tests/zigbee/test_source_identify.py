"""`ZigbeeSource` as an `IdentifySource` (design 2026-10-02, section 9.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fakes import (
    FakeApplication,
    FakeCluster,
    FakeDevice,
    FakeEndpoint,
    command_def,
)

from loxmatter.radios.fingerprints import Fingerprint
from loxmatter.sources import IdentifySource, IdentifyUnsupportedError
from loxmatter.zigbee.source import ZigbeeSource

BLINKER = "00:12:4b:00:1c:a1:b2:c3"
MUTE = "00:12:4b:00:1c:a1:b2:c4"
IDENTIFY = 0x0003
ON_OFF = 0x0006


def _device(ieee: str, clusters: list[FakeCluster]) -> FakeDevice:
    return FakeDevice(
        ieee,
        endpoints=[FakeEndpoint(1, profile_id=0x0104, device_type=0x0100, in_clusters=clusters)],
    )


def _source(application: FakeApplication, tmp_path: Path) -> ZigbeeSource:
    async def factory(config: dict[str, Any]) -> FakeApplication:
        application.config = config
        return application

    return ZigbeeSource(
        path="/dev/ttyUSB0",
        fingerprint=Fingerprint(
            name="SONOFF ZBDongle-E V2",
            radio_type="ezsp",
            baudrate=115200,
            flow_control="software",
        ),
        database=tmp_path / "zigbee.sqlite",
        application_factory=factory,
    )


async def test_zigbee_identify_sends_identify_time(tmp_path):
    cluster = FakeCluster(IDENTIFY, commands={0: command_def(0, "identify", "identify_time")})
    source = _source(FakeApplication(devices=[_device(BLINKER, [cluster])]), tmp_path)
    await source.connect()
    try:
        assert isinstance(source, IdentifySource)
        assert source.supports_identify(BLINKER) is True
        await source.identify(BLINKER, 30)
        assert cluster.sent == [(0, {"identify_time": 30})]
    finally:
        await source.disconnect()


async def test_zigbee_without_identify_is_unsupported(tmp_path):
    device = _device(MUTE, [FakeCluster(ON_OFF)])
    source = _source(FakeApplication(devices=[device]), tmp_path)
    await source.connect()
    try:
        assert source.supports_identify(MUTE) is False
        with pytest.raises(IdentifyUnsupportedError):
            await source.identify(MUTE, 30)
    finally:
        await source.disconnect()
