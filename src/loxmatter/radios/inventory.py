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
"""Which USB serial sticks and Bluetooth adapters the host has.

Read fresh on every request - no background scan, no cache. The roots are
parameters so tests can hand in directory trees; in the container they are
`/host/dev` (the host's /dev, mounted read-only for exactly this, see
docker-compose.yml) and `/sys`.

Measured on the test Pi on 11 September 2026 (design section 3): the
container sees `/sys/class/tty/ttyUSB0` and `/sys/class/bluetooth/hci0`
without any extra mount, but not `/dev/serial/by-id`. The USB attributes
(`manufacturer`, `product`, `serial`, `idVendor`, `idProduct`) sit on the
USB device, a varying number of levels above the tty's `device` link - one
for `ttyUSB` (the link ends in `…/1-1.2:1.0/ttyUSB0`), none for `ttyACM`
(it ends in the interface) - so the lookup walks up until it finds
`idVendor`, at most `_MAX_WALK` levels.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISCHR
from typing import Final, Literal

_TTY_NAME: Final = re.compile(r"tty(USB|ACM)\d+")
_HCI_NAME: Final = re.compile(r"hci(\d+)")
_MAX_WALK: Final = 4


@dataclass(frozen=True)
class SerialRadio:
    path: str  # /dev/serial/by-id/… as the host sees it, never /host/dev/…
    tty: str  # ttyUSB0
    manufacturer: str | None
    product: str | None
    serial: str | None
    vid_pid: str | None  # "10c4:ea60"


@dataclass(frozen=True)
class BluetoothAdapter:
    index: int
    name: str  # hci0
    bus: Literal["uart", "usb", "other"]
    product: str | None
    rfkill_blocked: bool


def _read(directory: Path | None, name: str) -> str | None:
    if directory is None:
        return None
    try:
        value = (directory / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _usb_device(start: Path) -> Path | None:
    """The first directory at or above `start` that carries `idVendor`."""
    try:
        current = start.resolve(strict=True)
    except OSError:
        return None
    for _ in range(_MAX_WALK + 1):
        if (current / "idVendor").is_file():
            return current
        if current.parent == current:
            return None
        current = current.parent
    return None


def scan_serial(host_dev: Path, sys_root: Path) -> list[SerialRadio]:
    by_id = host_dev / "serial" / "by-id"
    if not by_id.is_dir():
        return []
    try:
        entries = sorted(by_id.iterdir())
    except OSError:
        return []
    radios: list[SerialRadio] = []
    for entry in entries:
        if not entry.is_symlink():
            continue
        tty = Path(os.readlink(entry)).name
        if not _TTY_NAME.fullmatch(tty):
            continue
        usb = _usb_device(sys_root / "class" / "tty" / tty / "device")
        vendor, product_id = _read(usb, "idVendor"), _read(usb, "idProduct")
        radios.append(
            SerialRadio(
                path=f"/dev/serial/by-id/{entry.name}",
                tty=tty,
                manufacturer=_read(usb, "manufacturer"),
                product=_read(usb, "product"),
                serial=_read(usb, "serial"),
                vid_pid=f"{vendor}:{product_id}" if vendor and product_id else None,
            )
        )
    return radios


def scan_bluetooth(sys_root: Path) -> list[BluetoothAdapter]:
    base = sys_root / "class" / "bluetooth"
    if not base.is_dir():
        return []
    try:
        entries = list(base.iterdir())
    except OSError:
        return []
    adapters: list[BluetoothAdapter] = []
    for entry in entries:
        match = _HCI_NAME.fullmatch(entry.name)
        if match is None:
            continue
        try:
            target = str((entry / "device").resolve(strict=True))
        except OSError:
            target = ""
        bus: Literal["uart", "usb", "other"]
        if "/usb" in target:
            bus = "usb"
        elif "serial" in target:
            bus = "uart"
        else:
            bus = "other"
        product = _read(_usb_device(entry / "device"), "product") if bus == "usb" else None
        blocked = any(_read(rfkill, "soft") == "1" for rfkill in entry.glob("rfkill*"))
        adapters.append(
            BluetoothAdapter(
                index=int(match.group(1)),
                name=entry.name,
                bus=bus,
                product=product,
                rfkill_blocked=blocked,
            )
        )
    return sorted(adapters, key=lambda adapter: adapter.index)


def device_identity(
    path: str,
    host_dev: Path,
    *,
    stat: Callable[[str], os.stat_result] = os.stat,
) -> tuple[int, int] | None:
    """The `(major, minor)` a host-visible `/dev/...` path resolves to.

    `path` is always a path as the HOST sees it - `/dev/ttyUSB0` from the
    installer's `.env`, or a `/dev/serial/by-id/...` entry from this card.
    The bridge's own container sees neither: it has the host's `/dev`
    bind-mounted at `host_dev`, so the prefix is rewritten exactly the way
    `deploy/updater/radios-once.sh` already does it
    (`"$HOST_DEV${WANT_DEVICE#/dev}"`).

    `stat` follows symlinks, which is the point: a by-id entry is a symlink
    to the `ttyUSB*` node, so both names of one stick resolve to the same
    node and therefore to the same `st_rdev`.

    `None` for everything that cannot be resolved to a character device -
    an absent path, a permission error, a stale by-id symlink, a plain
    file. It is deliberately NOT an error: a stick that is not there is the
    ordinary state of a card listing devices that come and go, and
    `is_same_device` turns this `None` into "cannot be proven to be the
    same stick", which is the safe half of the answer for every caller.

    `stat` is injectable because a test cannot create a device node without
    root. That seam is the honest way to test the resolution - the
    alternative is to compare path strings, which is the very bug this
    function exists to prevent.
    """
    mapped = str(host_dev) + path[len("/dev") :] if path.startswith("/dev") else path
    try:
        info = stat(mapped)
    except OSError:
        return None
    if not S_ISCHR(info.st_mode):
        return None
    return os.major(info.st_rdev), os.minor(info.st_rdev)


def is_same_device(
    left: str | None,
    right: str | None,
    host_dev: Path,
    *,
    stat: Callable[[str], os.stat_result] = os.stat,
) -> bool:
    """Whether two paths name the one physical stick.

    By RESOLVED major:minor, never by string compare. MEASURED on the
    maintainer's Pi (12 September 2026): the same stick is `/dev/ttyUSB0`
    in the installer's `.env`, a `/dev/serial/by-id/usb-SONOFF_..._MG24_...`
    entry on the settings card, and a third name under the container's
    `/host/dev` mount. Three strings, one piece of hardware - and both
    sticks attached to that machine report `10c4:ea60` and sit at major
    188, so nothing but the resolved minor separates them.

    A path that cannot be resolved answers `False`, and `None == None` is
    emphatically NOT a match: two absent sticks are not known to be the
    same one. Reading it the other way round would make every unresolvable
    path count as the Thread stick, and nothing would ever be selectable.
    """
    if left is None or right is None:
        return False
    left_identity = device_identity(left, host_dev, stat=stat)
    if left_identity is None:
        return False
    return left_identity == device_identity(right, host_dev, stat=stat)


def match_current_device(
    device: str | None, radios: Sequence[SerialRadio]
) -> tuple[str | None, bool]:
    """Maps the sidecar's `RADIO_DEVICE` onto what the card shows.

    A by-id value is present when a detected stick has that path. Any other
    value (the installer writes `/dev/ttyUSB0`) is matched by tty name, and
    shown as the stick's by-id path when one matches."""
    if device is None:
        return None, False
    if device.startswith("/dev/serial/by-id/"):
        return device, any(radio.path == device for radio in radios)
    tty = Path(device).name
    for radio in radios:
        if radio.tty == tty:
            return radio.path, True
    return device, False
