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

"""Reading ZHA's descriptions, and rebuilding a device from one in a real
zigpy application - no radio, zigpy's own database in a temporary file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import zigpy.application
import zigpy.config
import zigpy.device

from loxmatter.zigbee.migration import (
    backup_identity,
    build_device,
    imported_device_from_zha,
)

DOOR: dict[str, Any] = {
    "ieee": "00:15:8D:00:01:02:03:04",
    "nwk": "0x1a2b",
    "manufacturer": "LUMI",
    "model": "lumi.sensor_magnet.aq2",
    "signature": {
        "node_descriptor": {
            "logical_type": 2,
            "complex_descriptor_available": 0,
            "user_descriptor_available": 0,
            "reserved": 0,
            "aps_flags": 0,
            "frequency_band": 8,
            "mac_capability_flags": 128,
            "manufacturer_code": 4151,
            "maximum_buffer_size": 127,
            "maximum_incoming_transfer_size": 100,
            "server_mask": 0,
            "maximum_outgoing_transfer_size": 100,
            "descriptor_capability_field": 0,
        },
        "endpoints": {
            "1": {
                "profile_id": "0x0104",
                "device_type": "0x0104",
                "input_clusters": ["0x0000", "0x0003", "0xffff", "0x0006"],
                "output_clusters": ["0x0000", "0x0004", "0xffff"],
            }
        },
    },
}


def test_a_zha_entry_is_read_with_normalised_addresses():
    imported = imported_device_from_zha(DOOR)

    assert imported is not None
    assert imported.ieee == "00:15:8d:00:01:02:03:04"
    assert imported.nwk == 0x1A2B
    [endpoint] = imported.endpoints
    assert (endpoint.endpoint_id, endpoint.profile_id, endpoint.device_type) == (1, 260, 260)
    assert endpoint.input_clusters == (0x0000, 0x0003, 0xFFFF, 0x0006)
    assert imported.reports_values is True


def test_the_coordinator_and_entries_without_endpoints_are_skipped():
    assert imported_device_from_zha({**DOOR, "active_coordinator": True}) is None
    assert imported_device_from_zha({**DOOR, "signature": {"endpoints": {}}}) is None
    assert imported_device_from_zha({**DOOR, "ieee": "nonsense"}) is None


def test_a_remote_with_only_housekeeping_clusters_reports_no_values():
    remote = {
        **DOOR,
        "signature": {
            "endpoints": {
                "1": {
                    "profile_id": "0x0104",
                    "device_type": "0x0820",
                    "input_clusters": ["0x0000", "0x0001", "0x0003", "0x1000"],
                    "output_clusters": ["0x0006", "0x0008"],
                }
            }
        },
    }

    imported = imported_device_from_zha(remote)

    assert imported is not None
    assert imported.reports_values is False


def test_the_backup_identity_never_includes_the_key():
    backup = {
        "network_info": {
            "extended_pan_id": "AA:BB:CC:DD:EE:FF:00:11",
            "channel": 15,
            "network_key": {"key": "secret"},
        },
        "node_info": {"ieee": "00:12:4b:00:aa:bb:cc:dd"},
    }

    identity = backup_identity(backup)

    assert identity.as_json() == {
        "extended_pan_id": "aa:bb:cc:dd:ee:ff:00:11",
        "coordinator_ieee": "00:12:4b:00:aa:bb:cc:dd",
        "channel": 15,
    }


class _NoRadio(zigpy.application.ControllerApplication):
    """A zigpy application with every radio call stubbed out - enough for
    `add_device` and `device_initialized`, which never reach the radio."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def start_network(self) -> None: ...
    async def force_remove(self, dev: Any) -> None: ...
    async def add_endpoint(self, descriptor: Any) -> None: ...
    async def permit_ncp(self, time_s: int = 60) -> None: ...
    async def permit_with_link_key(self, node: Any, link_key: Any, time_s: int = 60) -> None: ...
    async def reset_network_info(self) -> None: ...
    async def write_network_info(self, *, network_info: Any, node_info: Any) -> None: ...
    async def load_network_info(self, *, load_devices: bool = False) -> None: ...
    async def send_packet(self, packet: Any) -> None: ...


async def test_a_rebuilt_device_is_initialised_and_survives_a_restart(tmp_path: Path):
    database = str(tmp_path / "zigbee.db")
    config = {
        zigpy.config.CONF_DATABASE: database,
        zigpy.config.CONF_DEVICE: {zigpy.config.CONF_DEVICE_PATH: "/dev/null"},
        zigpy.config.CONF_STARTUP_ENERGY_SCAN: False,
    }
    imported = imported_device_from_zha(DOOR)
    assert imported is not None

    app = _NoRadio(config)
    await app._load_db()
    device = build_device(app, imported)
    app.device_initialized(device)
    assert device.is_initialized
    await app.shutdown(db=True)

    reloaded = _NoRadio(config)
    await reloaded._load_db()
    [again] = [d for d in reloaded.devices.values() if str(d.ieee) == imported.ieee]
    await reloaded.shutdown(db=True)

    assert isinstance(again, zigpy.device.Device)
    assert (again.manufacturer, again.model) == ("LUMI", "lumi.sensor_magnet.aq2")
    assert again.is_initialized
    assert sorted(again.endpoints[1].in_clusters) == [0x0000, 0x0003, 0x0006, 0xFFFF]
