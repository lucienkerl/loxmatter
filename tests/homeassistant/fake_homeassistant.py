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

"""A Home Assistant that answers the migration's calls from canned data.

The shapes are Home Assistant's own, as its WebSocket handlers send them
(device and area registry, `matter/node_diagnostics`, `zha/devices`,
`thread/list_datasets`) - trimmed to the fields the migration reads.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

from loxmatter.homeassistant.client import HomeAssistantError

ZHA_BACKUP: dict[str, Any] = {
    "version": 1,
    "backup_time": "2026-11-07T10:00:00+00:00",
    "network_info": {
        "extended_pan_id": "aa:bb:cc:dd:ee:ff:00:11",
        "pan_id": "1A2B",
        "nwk_update_id": 0,
        "nwk_manager_id": "0000",
        "channel": 15,
        "channel_mask": [15],
        "security_level": 5,
        "network_key": {
            "key": "01:02:03:04:05:06:07:08:09:0a:0b:0c:0d:0e:0f:10",
            "tx_counter": 1000,
            "rx_counter": 0,
            "seq": 0,
            "partner_ieee": "ff:ff:ff:ff:ff:ff:ff:ff",
        },
        "tc_link_key": {
            "key": "5a:69:67:42:65:65:41:6c:6c:69:61:6e:63:65:30:39",
            "tx_counter": 0,
            "rx_counter": 0,
            "seq": 0,
            "partner_ieee": "00:12:4b:00:aa:bb:cc:dd",
        },
        "key_table": [],
        "children": [],
        "nwk_addresses": {},
        "stack_specific": {},
        "metadata": {},
        "source": "bellows@0.40.0",
    },
    "node_info": {
        "nwk": "0000",
        "ieee": "00:12:4b:00:aa:bb:cc:dd",
        "logical_type": "coordinator",
        "model": None,
        "manufacturer": None,
        "version": None,
    },
}


def registry() -> list[dict[str, Any]]:
    return [
        {
            "id": "dev-plug",
            "name": "Smart Plug",
            "name_by_user": "Kaffeemaschine",
            "area_id": "kitchen",
            "manufacturer": "Eve",
            "model": "Energy",
            "identifiers": [["matter", "deviceid_1-0000000000000005-MatterNodeDevice"]],
            "via_device_id": None,
        },
        {
            "id": "dev-motion",
            "name": "Motion",
            "name_by_user": None,
            "area_id": "hall",
            "manufacturer": "Eve",
            "model": "Motion",
            "identifiers": [["matter", "deviceid_1-0000000000000006-MatterNodeDevice"]],
            "via_device_id": None,
        },
        {
            "id": "dev-bridge",
            "name": "Hue Bridge",
            "name_by_user": None,
            "area_id": None,
            "manufacturer": "Signify",
            "model": "BSB002",
            "identifiers": [["matter", "deviceid_1-0000000000000007-MatterNodeDevice"]],
            "via_device_id": None,
        },
        {
            "id": "dev-bridged",
            "name": "Hue lamp",
            "name_by_user": None,
            "area_id": "living",
            "manufacturer": "Signify",
            "model": "LCA001",
            "identifiers": [["matter", "deviceid_1-0000000000000007-2-MatterNodeDevice"]],
            "via_device_id": "dev-bridge",
        },
        {
            "id": "dev-door",
            "name": "LUMI lumi.sensor_magnet.aq2",
            "name_by_user": "Haustür",
            "area_id": "hall",
            "manufacturer": "LUMI",
            "model": "lumi.sensor_magnet.aq2",
            "identifiers": [["zha", "00:15:8d:00:01:02:03:04"]],
            "via_device_id": None,
        },
        {
            "id": "dev-remote",
            "name": "IKEA remote",
            "name_by_user": None,
            "area_id": "living",
            "manufacturer": "IKEA of Sweden",
            "model": "TRADFRI remote control",
            "identifiers": [["zha", "00:0b:57:ff:fe:01:02:03"]],
            "via_device_id": None,
        },
        {
            "id": "dev-sonos",
            "name": "Sonos One",
            "name_by_user": None,
            "area_id": "living",
            "manufacturer": "Sonos",
            "model": "One",
            "identifiers": [["sonos", "RINCON_1"]],
            "via_device_id": None,
        },
        {
            "id": "dev-sun",
            "name": "Sun",
            "entry_type": "service",
            "identifiers": [["sun", "x"]],
            "manufacturer": None,
            "model": None,
        },
    ]


AREAS = [
    {"area_id": "kitchen", "name": "Küche"},
    {"area_id": "hall", "name": "Flur"},
    {"area_id": "living", "name": "Wohnzimmer"},
]

DIAGNOSTICS = {
    "dev-plug": {"node_id": 5, "network_type": "wifi", "node_type": "end_device"},
    "dev-motion": {"node_id": 6, "network_type": "thread", "node_type": "sleepy_end_device"},
    "dev-bridge": {"node_id": 7, "network_type": "ethernet", "node_type": "bridge"},
    "dev-bridged": {"node_id": 7, "network_type": "ethernet", "node_type": "bridge"},
}

ZHA_DEVICES = [
    {
        "ieee": "00:12:4b:00:aa:bb:cc:dd",
        "nwk": "0x0000",
        "active_coordinator": True,
        "signature": {"endpoints": {"1": {"input_clusters": [], "output_clusters": []}}},
    },
    {
        "ieee": "00:15:8d:00:01:02:03:04",
        "nwk": "0x1a2b",
        "manufacturer": "LUMI",
        "model": "lumi.sensor_magnet.aq2",
        "name": "LUMI lumi.sensor_magnet.aq2",
        "user_given_name": "Haustür",
        "device_reg_id": "dev-door",
        "area_id": "hall",
        "signature": {
            "node_descriptor": None,
            "endpoints": {
                "1": {
                    "profile_id": "0x0104",
                    "device_type": "0x0104",
                    "input_clusters": ["0x0000", "0x0003", "0xffff", "0x0006"],
                    "output_clusters": ["0x0000", "0x0004", "0xffff"],
                }
            },
        },
    },
    {
        "ieee": "00:0b:57:ff:fe:01:02:03",
        "nwk": 4660,
        "manufacturer": "IKEA of Sweden",
        "model": "TRADFRI remote control",
        "name": "IKEA remote",
        "user_given_name": None,
        "device_reg_id": "dev-remote",
        "signature": {
            "endpoints": {
                "1": {
                    "profile_id": "0x0104",
                    "device_type": "0x0820",
                    "input_clusters": ["0x0000", "0x0001", "0x0003", "0x0b05", "0x1000"],
                    "output_clusters": ["0x0003", "0x0004", "0x0005", "0x0006", "0x0008"],
                }
            },
        },
    },
]


class FakeHomeAssistant:
    """Records every call; `fail` names commands that answer with an error."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.deleted: list[str] = []
        self.tokens: list[tuple[str, str]] = []
        self.fail: dict[str, Exception] = {}
        self.zha_disabled = False
        self.registry = registry()

    def factory(self, url: str, token: str) -> contextlib.AbstractAsyncContextManager[Any]:
        self.tokens.append((url, token))

        @contextlib.asynccontextmanager
        async def _session() -> AsyncIterator[FakeHomeAssistant]:
            yield self

        return _session()

    def commands(self) -> list[str]:
        return [command for command, _ in self.calls]

    async def call(self, command: str, **payload: Any) -> Any:
        self.calls.append((command, payload))
        if command in self.fail:
            raise self.fail[command]
        if command == "get_config":
            return {"version": "2026.11.0"}
        if command == "config/device_registry/list":
            return self.registry
        if command == "config/area_registry/list":
            return AREAS
        if command == "config_entries/get":
            domain = payload["domain"]
            entry = {"entry_id": f"entry-{domain}", "domain": domain, "disabled_by": None}
            if domain == "zha" and self.zha_disabled:
                entry["disabled_by"] = "user"
            return [entry]
        if command == "matter/node_diagnostics":
            found = DIAGNOSTICS.get(payload["device_id"])
            if found is None:
                raise HomeAssistantError("not a matter device")
            return found
        if command == "zha/devices":
            return ZHA_DEVICES
        if command == "thread/list_datasets":
            return {
                "datasets": [
                    {
                        "dataset_id": "ds1",
                        "preferred": True,
                        "network_name": "ha-thread-4f2a",
                        "channel": 15,
                        "extended_pan_id": "1111111122222222",
                    }
                ]
            }
        if command == "thread/get_dataset_tlv":
            return {"tlv": "0e080000000000010000"}
        if command == "matter/open_commissioning_window":
            return {
                "setup_pin_code": 20202021,
                "setup_manual_code": "3497-011-2332",
                "setup_qr_code": "MT:Y.K90AFN00KA0648G00",
            }
        if command == "zha/network/backups/create":
            return {"backup": ZHA_BACKUP, "is_complete": True}
        if command == "config_entries/disable":
            self.zha_disabled = True
            return {"require_restart": False}
        if command == "config/device_registry/remove_config_entry":
            return {}
        raise AssertionError(f"unexpected Home Assistant call {command}")

    async def delete(self, path: str) -> None:
        self.deleted.append(path)
