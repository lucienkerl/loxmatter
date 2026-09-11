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
"""Detecting USB sticks and Bluetooth adapters from /dev and sysfs (design
2026-09-11 "Radios in the Web UI", section 4). The trees are built in
tmp_path the way the test Pi showed them on 11 September 2026."""

from __future__ import annotations

from pathlib import Path

from loxmatter.radios.inventory import (
    BluetoothAdapter,
    SerialRadio,
    match_current_device,
    scan_bluetooth,
    scan_serial,
)

SONOFF = "usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a7d9118f9ef118f7767135c2a50c9-if00-port0"


def _usb_serial(root: Path, tty: str, by_id: str, attrs: dict[str, str], interface_depth: int = 1):
    """host_dev/serial/by-id/<by_id> -> ../../<tty>, and sysfs with
    class/tty/<tty>/device pointing into a USB device whose attributes sit
    `interface_depth` levels above the resolved device path (1 for ttyUSB,
    whose device is `…/1-1.2:1.0/ttyUSB0`; 0 for ttyACM, whose device is the
    interface `…/1-1.2:1.0` itself)."""
    host_dev, sys_root = root / "dev", root / "sys"
    (host_dev / "serial" / "by-id").mkdir(parents=True, exist_ok=True)
    (host_dev / tty).write_text("", encoding="utf-8")
    (host_dev / "serial" / "by-id" / by_id).symlink_to(Path("../..") / tty)
    usb = sys_root / "devices" / "usb1" / f"1-{tty}"
    interface = usb / f"1-{tty}:1.0"
    device = interface / tty if interface_depth == 1 else interface
    device.mkdir(parents=True, exist_ok=True)
    for name, value in attrs.items():
        (usb / name).write_text(value + "\n", encoding="utf-8")
    cls = sys_root / "class" / "tty" / tty
    cls.mkdir(parents=True, exist_ok=True)
    (cls / "device").symlink_to(device)
    return host_dev, sys_root


def test_a_usb_serial_stick_is_found_with_its_details(tmp_path):
    host_dev, sys_root = _usb_serial(
        tmp_path,
        "ttyUSB0",
        SONOFF,
        {
            "manufacturer": "SONOFF",
            "product": "SONOFF Dongle Plus MG24",
            "serial": "e26a7d9118f9ef118f7767135c2a50c9",
            "idVendor": "10c4",
            "idProduct": "ea60",
        },
    )

    assert scan_serial(host_dev, sys_root) == [
        SerialRadio(
            path=f"/dev/serial/by-id/{SONOFF}",
            tty="ttyUSB0",
            manufacturer="SONOFF",
            product="SONOFF Dongle Plus MG24",
            serial="e26a7d9118f9ef118f7767135c2a50c9",
            vid_pid="10c4:ea60",
        )
    ]


def test_an_acm_stick_whose_device_is_the_interface_itself_is_found(tmp_path):
    """Fault to prove it: only look one level above the resolved device."""
    host_dev, sys_root = _usb_serial(
        tmp_path,
        "ttyACM0",
        "usb-Nabu_Casa_ZBT-1-if00",
        {"idVendor": "303a", "idProduct": "4001"},
        interface_depth=0,
    )
    (radio,) = scan_serial(host_dev, sys_root)
    assert radio.tty == "ttyACM0"
    assert radio.vid_pid == "303a:4001"
    assert radio.product is None


def test_missing_by_id_directory_means_no_sticks(tmp_path):
    assert scan_serial(tmp_path / "dev", tmp_path / "sys") == []


def test_a_by_id_entry_that_is_not_a_usb_or_acm_tty_is_ignored(tmp_path):
    host_dev = tmp_path / "dev"
    (host_dev / "serial" / "by-id").mkdir(parents=True)
    (host_dev / "sda").write_text("", encoding="utf-8")
    (host_dev / "serial" / "by-id" / "usb-Disk").symlink_to(Path("../..") / "sda")
    assert scan_serial(host_dev, tmp_path / "sys") == []


def _bluetooth(root: Path, name: str, device_target: str, rfkill_soft: str | None = None):
    sys_root = root / "sys"
    target = sys_root / device_target
    target.mkdir(parents=True, exist_ok=True)
    entry = sys_root / "class" / "bluetooth" / name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "device").symlink_to(target)
    if rfkill_soft is not None:
        (entry / "rfkill0").mkdir()
        (entry / "rfkill0" / "soft").write_text(rfkill_soft + "\n", encoding="utf-8")
    return sys_root, target


def test_the_built_in_uart_adapter_of_the_pi(tmp_path):
    sys_root, _ = _bluetooth(
        tmp_path, "hci0", "devices/platform/soc/fe201000.serial/serial0/serial0-0", rfkill_soft="0"
    )
    assert scan_bluetooth(sys_root) == [
        BluetoothAdapter(index=0, name="hci0", bus="uart", product=None, rfkill_blocked=False)
    ]


def test_a_usb_adapter_reports_its_product_and_a_soft_block(tmp_path):
    """Fault to prove it: read `soft` as blocked when it is "0"."""
    sys_root, target = _bluetooth(tmp_path, "hci1", "devices/usb1/1-1.3/1-1.3:1.0", rfkill_soft="1")
    (target.parent / "idVendor").write_text("0bda\n", encoding="utf-8")
    (target.parent / "product").write_text("Bluetooth Radio\n", encoding="utf-8")
    assert scan_bluetooth(sys_root) == [
        BluetoothAdapter(
            index=1, name="hci1", bus="usb", product="Bluetooth Radio", rfkill_blocked=True
        )
    ]


def test_adapters_are_sorted_by_number_not_by_name(tmp_path):
    for n in (10, 2):
        _bluetooth(tmp_path, f"hci{n}", f"devices/other/{n}")
    assert [a.index for a in scan_bluetooth(tmp_path / "sys")] == [2, 10]
    assert {a.bus for a in scan_bluetooth(tmp_path / "sys")} == {"other"}


def _radio(path: str, tty: str) -> SerialRadio:
    return SerialRadio(
        path=path, tty=tty, manufacturer=None, product=None, serial=None, vid_pid=None
    )


def test_a_legacy_tty_name_is_matched_to_its_by_id_entry():
    """The installer wrote `/dev/ttyUSB0`; the card must show it as the
    by-id stick. Fault to prove it: compare the whole path instead of the
    tty name for non-by-id values."""
    radios = [_radio(f"/dev/serial/by-id/{SONOFF}", "ttyUSB0")]
    assert match_current_device("/dev/ttyUSB0", radios) == (f"/dev/serial/by-id/{SONOFF}", True)


def test_a_by_id_value_that_is_attached_is_present():
    radios = [_radio(f"/dev/serial/by-id/{SONOFF}", "ttyUSB0")]
    assert match_current_device(f"/dev/serial/by-id/{SONOFF}", radios) == (
        f"/dev/serial/by-id/{SONOFF}",
        True,
    )


def test_a_configured_stick_that_is_gone_is_reported_missing():
    assert match_current_device("/dev/ttyUSB3", []) == ("/dev/ttyUSB3", False)
    assert match_current_device(None, []) == (None, False)
