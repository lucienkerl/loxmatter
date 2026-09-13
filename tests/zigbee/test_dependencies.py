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

"""What the lock file must keep true about the Zigbee tree (research F.1,
F.5, F.6).

Not a test that the packages are "installed somewhere" - that would be true
of any environment. It reads `uv.lock`, which is the file an image build and
a Pi install actually resolve from."""

from __future__ import annotations

import tomllib
from pathlib import Path

LOCK = Path(__file__).resolve().parent.parent.parent / "uv.lock"


def _locked() -> dict[str, str]:
    raw = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    return {package["name"]: package["version"] for package in raw["package"]}


def test_the_whole_zigbee_tree_is_locked_not_just_zigpy():
    """Fault to prove it: drop `zha-quirks` from pyproject.toml and relock.
    `zigpy` and `bellows` alone still resolve, and the bridge then pairs a
    Tuya device that never sends anything - the failure research F.6
    describes, which no import error announces."""
    locked = _locked()
    # The five radio libraries arrive through `zha`, which pins each of them
    # exactly; none of them is a direct dependency of loxmatter.
    for name in (
        "zigpy",
        "bellows",
        "zha",
        "zha-quirks",
        "zigpy-znp",
        "zigpy-deconz",
        "zigpy-xbee",
        "zigpy-zigate",
        "gpiozero",
    ):
        assert name in locked, f"{name} is missing from uv.lock"


def test_no_existing_pin_moved_for_zigbee():
    """The versions loxmatter already depended on before the Zigbee tree
    landed. zigpy declares its own requirements unpinned, which is why
    adding it moved nothing (research F.1) - if a later zigpy release does
    pin one of these, this test is where that surfaces, rather than in a
    behaviour change nobody connects to a lock file.

    Fault to prove it: change one of the expected versions below."""
    locked = _locked()
    assert locked["aiohttp"] == "3.14.3"
    assert locked["attrs"] == "26.1.0"
    assert locked["typing-extensions"] == "4.16.0"
    assert locked["click"] == "8.5.0"
