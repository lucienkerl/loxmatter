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

"""`radios.thread_lockout.open_refusal` against two spellings of one stick.

`tests/api/test_zigbee_api.py` measures the open guard on the maintainer's
two-stick tree, where the scan maps the sidecar's `/dev/ttyUSB0` onto the
Thread stick's by-id path before anything compares - so the stored path and
the Thread device arrive as the SAME string, and a guard that compared
strings passed there too.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from loxmatter.radios.thread_lockout import open_refusal

THREAD_BY_ID = "usb-SONOFF_SONOFF_Dongle_Plus_MG24_e26a7d9118f9ef118f7767135c2a50c9-if00-port0"


def test_the_stored_stick_is_refused_under_a_second_name_for_the_thread_device(
    tmp_path: Path,
) -> None:
    """The stored Zigbee setting names a `/dev/serial/by-id/...` link and
    the sidecar reports Thread on `/dev/ttyUSB0`: two strings for one node.
    `/dev/null` stands in for that node, because a test cannot create a
    device node, and the by-id link points at it directly rather than
    through `ttyUSB0` - so the scan, which lists only links whose target is
    named like a tty, cannot map `/dev/ttyUSB0` onto the by-id path, and the
    two names reach the comparison as different strings. Resolved through
    the host-dev mount they are one major:minor, and the stick stays closed.

    Fault to prove it: compare the path strings in `is_same_device` or
    `is_thread_stick` (the Thread stick is opened)."""
    host_dev, sys_root, update_dir = tmp_path / "dev", tmp_path / "sys", tmp_path / "update"
    (host_dev / "serial" / "by-id").mkdir(parents=True)
    sys_root.mkdir()
    update_dir.mkdir()
    (host_dev / "ttyUSB0").symlink_to("/dev/null")
    (host_dev / "serial" / "by-id" / THREAD_BY_ID).symlink_to("/dev/null")
    (update_dir / "radios-state.json").write_text(
        json.dumps(
            {
                "phase": "idle",
                "current": {
                    "thread_enabled": True,
                    "thread_device": "/dev/ttyUSB0",
                    "bluetooth_adapter": 0,
                    "otbr_running": True,
                },
                "capable": True,
                "seen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        ),
        encoding="utf-8",
    )

    refusal = open_refusal(
        f"/dev/serial/by-id/{THREAD_BY_ID}",
        update_dir=update_dir,
        host_dev=host_dev,
        sys_root=sys_root,
    )

    assert refusal is not None
    assert refusal.key == "api.errors.zigbee_open_is_thread_stick"
