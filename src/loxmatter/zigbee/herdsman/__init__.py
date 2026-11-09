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

"""zigbee2mqtt's device library (zigbee-herdsman-converters) next to
zha-quirks (design 2026-11-09).

- `catalogue`: the generated catalogue and the matcher;
- `properties`: a library property to a snapshot path and an option;
- `tuya`, `zcl`: stage 1, the parts of a definition that are data;
- `engine`: stage 2, the library itself in quickjs-ng;
- `library`: what the Zigbee source talks to."""
