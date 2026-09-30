"""`BridgeMatterClient` as a `FirmwareSource` (design 2026-09-30, section 8).

The answers are the ones matter-server gave on the test Pi on 2026-09-30.
The fake upstream is local to this file on purpose: it needs only the
node cache and three commands."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from matter_server.common.models import MatterSoftwareVersion, UpdateSource

from loxmatter.matter.client import BridgeMatterClient, MatterUnavailableError
from loxmatter.sources.firmware import FirmwareSource, UpdateOffer

KAJPLATS_22 = {
    "0/40/9": 16842752,
    "0/40/10": "1.1.0",
    "0/40/21": 0x01040000,
    "0/42/0": [],
    "0/42/1": True,
    "0/42/2": 1,
    "0/42/3": None,
}
TASMOTA_23 = {"0/40/9": 1, "0/40/10": "13.3.0"}


class FirmwareNode:
    def __init__(self, node_id: int, attributes: dict[str, Any], available: bool = True):
        self.node_id = node_id
        self.available = available
        self.node_data = SimpleNamespace(attributes=attributes, available=available)


class FirmwareUpstream:
    def __init__(self, nodes: list[FirmwareNode], schema: int = 11):
        self._nodes = nodes
        self.server_info = SimpleNamespace(schema_version=schema)
        self.checked: list[int] = []
        self.updated: list[tuple[int, int | str]] = []
        self.read: list[tuple[int, Any]] = []
        self.offer: MatterSoftwareVersion | None = None

    async def start_listening(self, init_ready: asyncio.Event | None = None) -> None:
        if init_ready is not None:
            init_ready.set()
        await asyncio.Event().wait()

    async def disconnect(self) -> None:
        return None

    def get_nodes(self) -> list[FirmwareNode]:
        return self._nodes

    def subscribe_events(self, *args: Any, **kwargs: Any) -> Any:
        return lambda: None

    async def check_node_update(self, node_id: int) -> MatterSoftwareVersion | None:
        self.checked.append(node_id)
        return self.offer

    async def update_node(self, node_id: int, software_version: int | str) -> None:
        self.updated.append((node_id, software_version))

    async def read_attribute(self, node_id: int, attribute_path: Any) -> dict[str, Any]:
        self.read.append((node_id, attribute_path))
        return {"0/42/2": 4, "0/42/3": 43}


class _Session:
    async def close(self) -> None:
        return None


async def _connected(upstream: FirmwareUpstream) -> BridgeMatterClient:
    bridge = BridgeMatterClient(
        url="ws://test/ws",
        session_factory=lambda _session: upstream,
        http_session_factory=_Session,
    )
    await bridge.connect()
    return bridge


def _dcl_offer() -> MatterSoftwareVersion:
    return MatterSoftwareVersion(
        vid=4476,
        pid=36870,
        software_version=16908288,
        software_version_string="1.2.0",
        firmware_information="",
        min_applicable_software_version=0,
        max_applicable_software_version=16908287,
        release_notes_url="",
        update_source=UpdateSource.MAIN_NET_DCL,
    )


async def test_the_client_is_a_firmware_source():
    bridge = await _connected(FirmwareUpstream([]))
    try:
        assert isinstance(bridge, FirmwareSource)
    finally:
        await bridge.disconnect()


@pytest.mark.parametrize(("schema", "expected"), [(9, False), (10, True), (11, True)])
async def test_firmware_is_supported_from_schema_10(schema, expected):
    bridge = await _connected(FirmwareUpstream([], schema=schema))
    try:
        assert bridge.firmware_supported() is expected
    finally:
        await bridge.disconnect()


async def test_firmware_is_not_supported_while_disconnected():
    bridge = BridgeMatterClient(url="ws://test/ws")
    assert bridge.firmware_supported() is False


async def test_facts_come_from_the_node_cache():
    bridge = await _connected(FirmwareUpstream([FirmwareNode(22, KAJPLATS_22)]))
    try:
        facts = bridge.firmware_facts("22")
    finally:
        await bridge.disconnect()
    assert facts is not None
    assert facts.available is True
    assert facts.has_requestor is True
    assert facts.software_version == 16842752
    assert facts.software_version_string == "1.1.0"
    assert facts.spec_version == 0x01040000
    assert facts.update_state == 1
    assert facts.update_progress is None


async def test_a_tasmota_plug_has_no_requestor_and_no_spec_version():
    bridge = await _connected(FirmwareUpstream([FirmwareNode(23, TASMOTA_23)]))
    try:
        facts = bridge.firmware_facts("23")
    finally:
        await bridge.disconnect()
    assert facts is not None
    assert facts.has_requestor is False
    assert facts.spec_version is None


async def test_facts_for_an_unknown_node_are_none():
    bridge = await _connected(FirmwareUpstream([]))
    try:
        assert bridge.firmware_facts("99") is None
    finally:
        await bridge.disconnect()


async def test_check_update_maps_the_dcl_answer():
    upstream = FirmwareUpstream([FirmwareNode(22, KAJPLATS_22)])
    upstream.offer = _dcl_offer()
    bridge = await _connected(upstream)
    try:
        offer = await bridge.check_update("22")
    finally:
        await bridge.disconnect()
    assert upstream.checked == [22]
    assert offer == UpdateOffer(
        software_version=16908288,
        software_version_string="1.2.0",
        min_applicable=0,
        max_applicable=16908287,
        release_notes_url=None,  # the DCL's "" is no link
        source="main-net-dcl",
    )


async def test_check_update_passes_none_through():
    upstream = FirmwareUpstream([FirmwareNode(22, KAJPLATS_22)])
    bridge = await _connected(upstream)
    try:
        assert await bridge.check_update("22") is None
    finally:
        await bridge.disconnect()


async def test_start_update_sends_the_integer_version():
    upstream = FirmwareUpstream([FirmwareNode(22, KAJPLATS_22)])
    bridge = await _connected(upstream)
    try:
        await bridge.start_update("22", 16908288)
    finally:
        await bridge.disconnect()
    assert upstream.updated == [(22, 16908288)]


async def test_refresh_writes_the_read_values_into_the_cache():
    attributes = dict(KAJPLATS_22)
    upstream = FirmwareUpstream([FirmwareNode(22, attributes)])
    bridge = await _connected(upstream)
    try:
        await bridge.refresh_firmware_facts("22")
        facts = bridge.firmware_facts("22")
    finally:
        await bridge.disconnect()
    assert upstream.read == [(22, ["0/42/2", "0/42/3", "0/40/9", "0/40/10"])]
    assert facts is not None and (facts.update_state, facts.update_progress) == (4, 43)


async def test_check_update_without_connection_raises():
    bridge = BridgeMatterClient(url="ws://test/ws")
    with pytest.raises(MatterUnavailableError):
        await bridge.check_update("22")
