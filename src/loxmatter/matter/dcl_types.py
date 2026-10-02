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
"""What the DCL says about a vendor and a product (design 2026-10-02, 7).

Kept apart from `matter.dcl` so that `model/` can name these types without
importing the HTTP client."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DclVendor:
    vendor_id: int
    name: str


@dataclass(frozen=True)
class DclModel:
    vendor_id: int
    product_id: int
    name: str
    part_number: str | None
    device_type: int | None
    initial_steps_hint: int
    initial_steps_instruction: str | None
