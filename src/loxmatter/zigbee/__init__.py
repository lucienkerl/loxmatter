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

"""Zigbee as the second device source (design 2026-09-12).

Everything Zigbee-specific lives in this package and nowhere else. The
boundary is `loxmatter/sources/`: `ZigbeeSource` satisfies `DeviceSource`
without an adapter, exactly as `BridgeMatterClient` does, and produces the
same `NodeSnapshot` with Matter attribute paths, Matter device type IDs and
a synthesised `AcceptedCommandList`. Nothing past `loxmatter/sources/`
learns that Zigbee exists, and no `except` clause outside this package may
name a zigpy exception type (boundary design 2026-09-11, section 2; design
2026-09-12, section 2.1)."""
