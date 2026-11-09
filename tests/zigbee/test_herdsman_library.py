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

"""The zigbee2mqtt device library against its real build output: the
matcher against the golden table, the engine with real definitions, and the
library deciding, tapping, placing and writing for zigpy-shaped devices
(design 2026-11-09, section 11).

The build output is not in git (`scripts/herdsman/build.mjs` makes it, CI
runs it before the tests); without it these tests are skipped, not failed,
so a checkout without Node still runs the rest of the suite."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import zigpy.device
import zigpy.zcl

from loxmatter.device_config import to_raw
from loxmatter.profiles.table import VENDOR_PROPERTIES_CLUSTER, vendor_property_ids
from loxmatter.zigbee.herdsman import library as library_module
from loxmatter.zigbee.herdsman.catalogue import (
    CATALOGUE_FILE,
    ENGINE_FILE,
    GOLDEN_FILE,
    Catalogue,
    DeviceKeys,
)
from loxmatter.zigbee.herdsman.engine import Engine
from loxmatter.zigbee.herdsman.library import Library, herdsman_ieee
from loxmatter.zigbee.translate import DeviceFacts, build_snapshot

needs_build = pytest.mark.skipif(
    not CATALOGUE_FILE.is_file() or not ENGINE_FILE.is_file(),
    reason="run `npm ci --prefix herdsman && node scripts/herdsman/build.mjs` first",
)
pytestmark = needs_build


@pytest.fixture(scope="module")
def catalogue() -> Catalogue:
    return library_module.shared_catalogue()


# ------------------------------------------------------------- the matcher --


def test_the_matcher_agrees_with_the_library_on_every_golden_case(catalogue: Catalogue) -> None:
    """Every fingerprint and every zigbeeModel in the library, with the
    definition the library's own `findDefinition` picks for it."""
    mismatches = []
    cases = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
    assert len(cases) > 5000
    for case in cases:
        d = case["device"]
        keys = DeviceKeys(
            model_id=d.get("modelID"),
            manufacturer_name=d.get("manufacturerName"),
            type=d.get("type", "EndDevice"),
            manufacturer_id=d.get("manufacturerID"),
            application_version=d.get("applicationVersion"),
            stack_version=d.get("stackVersion"),
            zcl_version=d.get("zclVersion"),
            hardware_version=d.get("hardwareVersion"),
            date_code=d.get("dateCode"),
            software_build_id=d.get("softwareBuildID"),
            power_source=d.get("powerSource"),
            ieee="0x0000000000000001",
            endpoints=tuple(
                (
                    e["ID"],
                    e.get("deviceID"),
                    e.get("profileID"),
                    tuple(e.get("inputClusters") or ()),
                    tuple(e.get("outputClusters") or ()),
                )
                for e in d.get("endpoints") or ()
            ),
        )
        got = catalogue.match(keys)
        if got != case["expect"]:
            mismatches.append((d, case["expect"], got))
    assert mismatches == []


# --------------------------------------------------- zigpy-shaped devices --


class _Cluster:
    def __init__(self, cluster_id: int, cached: dict[int, Any] | None = None) -> None:
        self.cluster_id = cluster_id
        self.attributes: dict[int, Any] = {}
        self._cached = dict(cached or {})
        self.raw_writes: list[tuple[list[Any], int | None]] = []
        self.raw_reads: list[tuple[list[int], int | None]] = []

    def get(self, key: int, default: Any = None) -> Any:
        return self._cached.get(key, default)

    async def write_attributes_raw(
        self, attributes: list[Any], manufacturer_code: int | None = None
    ) -> Any:
        self.raw_writes.append((attributes, manufacturer_code))
        return [[]]

    async def read_attributes_raw(
        self, attributes: list[int], manufacturer: int | None = None
    ) -> Any:
        self.raw_reads.append((attributes, manufacturer))
        return [{}, {}]


class _Endpoint:
    def __init__(
        self, endpoint_id: int, profile_id: int, device_type: int, clusters: list[_Cluster]
    ) -> None:
        self.endpoint_id = endpoint_id
        self.profile_id = profile_id
        self.device_type = device_type
        self.in_clusters = {c.cluster_id: c for c in clusters}
        self.out_clusters: dict[int, Any] = {}


class _NodeDescriptor:
    def __init__(
        self, *, mains: bool, logical_type: int = 2, manufacturer_code: int = 0x1141
    ) -> None:
        self.is_mains_powered = mains
        self.logical_type = logical_type
        self.manufacturer_code = manufacturer_code


class _Packet:
    """The fields of `zigpy.types.ZigbeePacket` the tap reads."""

    def __init__(self, endpoint: int, cluster: int, data: bytes, lqi: int = 120) -> None:
        self.src_ep = endpoint
        self.dst_ep = 1
        self.cluster_id = cluster
        self.data = data
        self.lqi = lqi


class _Device:
    """What the library reads off a zigpy device, and the two calls it makes."""

    def __init__(
        self,
        ieee: str,
        *,
        model: str,
        manufacturer: str,
        endpoints: list[_Endpoint],
        mains: bool = False,
        quirked: bool = False,
    ) -> None:
        self.ieee = ieee
        self.nwk = 0x1234
        self.model = model
        self.manufacturer = manufacturer
        self.node_desc = _NodeDescriptor(mains=mains)
        self.non_zdo_endpoints = endpoints
        self.endpoints = {e.endpoint_id: e for e in endpoints}
        self.sent: list[tuple[int, int, int, bytes]] = []
        self.answer: Any = None
        self.filtered: list[Any] = []
        if quirked:
            self._quirk_registry_entry = object()

    def _should_filter_packet(self, packet: Any) -> bool:
        self.filtered.append(packet)
        return False

    async def request(
        self,
        profile: int,
        cluster: int,
        src_ep: int,
        dst_ep: int,
        sequence: int,
        data: bytes,
        *,
        expect_reply: bool = True,
    ) -> None:
        self.sent.append((cluster, dst_ep, sequence, bytes(data)))
        if self.answer is not None:
            self.answer(self, cluster, dst_ep, bytes(data))

    def hear(self, endpoint: int, cluster: int, hex_data: str) -> None:
        """A frame from the device, the way zigpy's packet path first sees it."""
        self._should_filter_packet(_Packet(endpoint, cluster, bytes.fromhex(hex_data)))


def _zg_204zq(ieee: str = "a4:c1:38:00:00:00:00:01", *, quirked: bool = False) -> _Device:
    """HOBEIAN ZG-204ZQ: a Tuya radar the catalogue describes entirely as data."""
    basic = _Cluster(0x0000, {4: "_TZE200_p9zbdqgs", 5: "TS0601", 1: 0x46})
    return _Device(
        ieee,
        model="TS0601",
        manufacturer="_TZE200_p9zbdqgs",
        endpoints=[
            _Endpoint(
                1, 0x0104, 0x0051, [basic, _Cluster(0x0004), _Cluster(0x0005), _Cluster(0xEF00)]
            )
        ],
        quirked=quirked,
    )


def _zg_204z(ieee: str = "a4:c1:38:00:00:00:00:02") -> _Device:
    """HOBEIAN ZG-204Z: its definition is JavaScript, so the engine runs it."""
    basic = _Cluster(0x0000, {4: "HOBEIAN", 5: "ZG-204Z", 7: 3})
    return _Device(
        ieee,
        model="ZG-204Z",
        manufacturer="HOBEIAN",
        endpoints=[
            _Endpoint(
                1,
                0x0104,
                0x0402,
                [basic, _Cluster(0x0001), _Cluster(0x0003), _Cluster(0x0500), _Cluster(0xEF00)],
            )
        ],
    )


class _App:
    class state:
        class node_info:
            ieee = "00:12:4b:00:ff:ff:ff:ff"

    def __init__(self) -> None:
        self.sequence = 0

    def get_sequence(self) -> int:
        self.sequence = (self.sequence + 1) % 256
        return self.sequence


async def _library(tmp_path: Path, notified: list[str] | None = None) -> Library:
    seen = notified if notified is not None else []
    library = Library(
        state_file=tmp_path / "zigbee.db.herdsman.json",
        notify=seen.append,
        catalogue_loader=library_module.shared_catalogue,
    )
    library.use_application(_App())
    assert await library.load()
    return library


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def _facts(device: _Device, library: Library) -> DeviceFacts:
    contribution = library.contribution(device)
    return DeviceFacts(
        ieee=device.ieee,
        manufacturer=device.manufacturer,
        model=device.model,
        is_mains_powered=False,
        available=True,
        quirk_applied=False,
        endpoints=(),
        config_options=contribution.options,
        library_attributes=contribution.attributes,
        library_device_types=contribution.device_types,
    )


# ------------------------------------------------- stage 1: the catalogue --

# dataReport: temperature (dp 111) 21.5 degC, humidity (101) 48 %,
# presence (1, an enum data point) yes, fading time (102) 30 s,
# indicator (108) on, illuminance (106) 120 lx.
_ZQ_REPORT = (
    "09100204006f020004000000d765020004000000300104000101660200040000001e6c010001016a02000400000078"
)


async def test_a_catalogue_tuya_device_reports_through_the_tap(tmp_path: Path) -> None:
    notified: list[str] = []
    library = await _library(tmp_path, notified)
    device = _zg_204zq()
    assert library.bind(device)
    assert library.mode(device.ieee) == "catalogue"

    device.hear(1, 0xEF00, _ZQ_REPORT)

    assert notified == [device.ieee]
    snapshot = build_snapshot(_facts(device, library))
    numbers = vendor_property_ids()
    vendor = f"1/{VENDOR_PROPERTIES_CLUSTER}"
    assert snapshot.attributes["1/1026/0"] == 2150
    assert snapshot.attributes["1/1029/0"] == 4800
    assert snapshot.attributes["1/1030/0"] == 1
    assert snapshot.attributes["1/1024/0"] == 20793  # 10000 * log10(120) + 1
    assert snapshot.attributes[f"{vendor}/{numbers['fading_time']}"] == 30.0
    assert snapshot.attributes[f"{vendor}/{numbers['indicator']}"] == 1.0
    # A Tuya radar declares a smart plug; what it reports says it is an
    # occupancy sensor.
    assert snapshot.attributes["1/29/0"] == [{"0": 0x0107, "1": 1}]
    names = {o.name for o in snapshot.config_options}
    assert {"fading_time", "indicator", "temperature_calibration"} <= names
    assert "presence" not in names and "temperature" not in names


async def test_a_catalogue_setting_is_written_as_a_tuya_data_point(tmp_path: Path) -> None:
    library = await _library(tmp_path)
    device = _zg_204zq()
    library.bind(device)
    options = {o.name: o for o in library.contribution(device).options}

    fading = options["fading_time"]
    await library.write(device, fading.attribute_id, to_raw(fading, 60))
    indicator = options["indicator"]
    await library.write(device, indicator.attribute_id, to_raw(indicator, 0))
    calibration = options["temperature_calibration"]
    await library.write(device, calibration.attribute_id, to_raw(calibration, -1.5))

    frames = [data.hex() for cluster, _ep, _tsn, data in device.sent if cluster == 0xEF00]
    # dataRequest, sequence 1, as `tz.datapoints` sends it.
    assert frames[0][6:] == "0100660200040000003c"
    assert frames[1][6:] == "01006c01000100"
    assert frames[2][6:] == "010069020004fffffff1"
    assert all(frame[:2] == "11" and frame[4:6] == "00" for frame in frames)
    # Optimistic, as in zigbee2mqtt: the new value shows before the device
    # echoes it.
    numbers = vendor_property_ids()
    attributes = library.contribution(device).attributes
    assert attributes[f"1/{VENDOR_PROPERTIES_CLUSTER}/{numbers['fading_time']}"] == 60.0


async def test_a_quirked_device_keeps_its_readings_and_gets_no_data_point_settings(
    tmp_path: Path,
) -> None:
    library = await _library(tmp_path)
    device = _zg_204zq(quirked=True)
    assert library.bind(device)
    assert library.mode(device.ieee) == "settings"
    device.hear(1, 0xEF00, _ZQ_REPORT)
    contribution = library.contribution(device)
    assert contribution.attributes == {}
    assert contribution.device_types == {}
    # The radar's settings are data points, which belong to its quirk.
    assert contribution.options == ()


async def test_values_survive_a_restart(tmp_path: Path) -> None:
    library = await _library(tmp_path)
    device = _zg_204zq()
    library.bind(device)
    device.hear(1, 0xEF00, _ZQ_REPORT)
    await library.close()

    again = await _library(tmp_path)
    fresh = _zg_204zq()
    again.bind(fresh)
    assert again.contribution(fresh).attributes["1/1026/0"] == 2150


async def test_a_device_the_library_does_not_know_is_left_alone(tmp_path: Path) -> None:
    library = await _library(tmp_path)
    device = _Device(
        "00:00:00:00:00:00:00:99", model="nothing-like-it", manufacturer="nobody", endpoints=[]
    )
    assert not library.bind(device)
    assert library.contribution(device).attributes == {}


# ----------------------------------------------------- stage 2: the engine --


def _answer_writes(device: _Device, cluster: int, endpoint: int, data: bytes) -> None:
    """A device that answers every Write Attributes with success."""
    if data[0] & 0x03 == 0 and data[2] == 0x02:
        device.hear(endpoint, cluster, f"18{data[1]:02x}0400")


async def test_the_engine_runs_the_zg_204z_definition(tmp_path: Path) -> None:
    """Design section 9.2's probe, end to end through the library."""
    library = await _library(tmp_path)
    device = _zg_204z()
    assert library.bind(device)
    assert library.mode(device.ieee) == "engine"
    await library.attach(device)
    assert library.mode(device.ieee) == "engine"

    device.hear(1, 0x0500, "18050a01f0200213002001")
    for _ in range(100):
        await asyncio.sleep(0.01)
        if library.contribution(device).attributes:
            break
    numbers = vendor_property_ids()
    attributes = library.contribution(device).attributes
    assert attributes[f"1/{VENDOR_PROPERTIES_CLUSTER}/{numbers['keep_time']}"] == 120.0
    assert attributes[f"1/{VENDOR_PROPERTIES_CLUSTER}/{numbers['sensitivity']}"] == 1.0

    options = {o.name: o for o in library.contribution(device).options}
    assert {"keep_time", "sensitivity"} <= set(options)
    device.answer = _answer_writes
    keep_time = options["keep_time"]
    await asyncio.wait_for(library.write(device, keep_time.attribute_id, to_raw(keep_time, 60)), 5)
    cluster, endpoint, _tsn, frame = device.sent[-1]
    assert (cluster, endpoint) == (0x0500, 1)
    # Write Attributes, 0xF001, uint8, 1 - byte for byte PR #40's quirk.
    assert frame.hex()[:2] == "10" and frame.hex()[4:] == "0201f02001"
    await library.close()


async def test_engine_timers_run_on_the_event_loop(tmp_path: Path) -> None:
    engine = Engine(
        send=_never_send,
        bind=_never_bind,
        save=lambda _ieee, _record: None,
        publish=lambda _ieee, _payload: None,
        next_tsn=lambda: 1,
    )
    await engine.start("0x00124b00ffffffff")
    await engine._run(
        lambda context: context.eval(
            "globalThis.__fired = 0; setTimeout(() => { globalThis.__fired += 1; }, 20);"
        )
    )
    assert await engine._run(lambda context: context.eval("globalThis.__fired")) == 0
    await asyncio.sleep(0.1)
    assert await engine._run(lambda context: context.eval("globalThis.__fired")) == 1
    await engine.close()


async def test_a_converter_that_loops_costs_only_its_call(tmp_path: Path) -> None:
    engine = Engine(
        send=_never_send,
        bind=_never_bind,
        save=lambda _ieee, _record: None,
        publish=lambda _ieee, _payload: None,
        next_tsn=lambda: 1,
    )
    await engine.start("0x00124b00ffffffff")
    from loxmatter.zigbee.herdsman import engine as engine_module

    with pytest.raises(engine_module.EngineError):
        await engine._run(lambda context: context.eval("while (true) {}"))
    assert await engine._run(lambda context: context.eval("1 + 1")) == 2
    await engine.close()


async def _never_send(*_args: Any) -> None:
    raise AssertionError("nothing is sent here")


async def _never_bind(*_args: Any) -> None:
    raise AssertionError("nothing is bound here")


async def test_the_engine_and_the_catalogue_agree_on_a_tuya_report(tmp_path: Path) -> None:
    """The same frame through the library's own JavaScript and through
    stage 1's Python port must give the same properties."""
    published: list[dict[str, Any]] = []
    engine = Engine(
        send=_never_send,
        bind=_never_bind,
        save=lambda _ieee, _record: None,
        publish=lambda _ieee, payload: published.append(payload),
        next_tsn=lambda: 1,
    )
    await engine.start("0x00124b00ffffffff")
    device = _zg_204zq()
    keys = library_module.device_keys(device)
    attached = await engine.attach(library_module.herdsman_record(device, keys, None), {})
    assert attached is not None and attached.model == "ZG-204ZQ"
    await engine.frame(herdsman_ieee(device.ieee), 1, 0xEF00, bytes.fromhex(_ZQ_REPORT), 100, 1)
    await _settle()
    from_engine: dict[str, Any] = {}
    for payload in published:
        from_engine.update(payload)
    await engine.close()

    library = await _library(tmp_path)
    library.bind(device)
    device.hear(1, 0xEF00, _ZQ_REPORT)
    from_python = library._devices[device.ieee].values
    assert from_python
    for prop, value in from_python.items():
        assert from_engine.get(prop) == value, prop


# ----------------------------------------------------- the zigpy hooks used --


def test_the_zigpy_calls_the_library_makes_exist() -> None:
    assert callable(getattr(zigpy.device.Device, "_should_filter_packet", None))
    assert callable(zigpy.device.Device.request)
    assert callable(zigpy.zcl.Cluster.write_attributes_raw)
    assert callable(zigpy.zcl.Cluster.read_attributes_raw)
