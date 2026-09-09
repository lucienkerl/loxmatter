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

"""Immutable snapshot of what matter-server knows about a device."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# BasicInformation cluster on endpoint 0.
_VENDOR_NAME_PATH = "0/40/1"
_PRODUCT_NAME_PATH = "0/40/3"
_UNIQUE_ID_PATH = "0/40/18"


class SignalKind(str, Enum):
    ATTRIBUTE = "attribute"
    EVENT = "event"


@dataclass(frozen=True, order=True)
class SignalRef:
    """Reference to exactly one data source of a device.

    An attribute and an event can carry the same numbers and still be
    different things — `kind` is therefore part of the identity.
    """

    endpoint: int
    cluster_id: int
    element_id: int
    kind: SignalKind

    @property
    def path(self) -> str:
        return f"{self.endpoint}/{self.cluster_id}/{self.element_id}"


@dataclass(frozen=True)
class NodeSnapshot:
    node_id: int
    vendor_name: str
    product_name: str
    unique_id: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    # Reachability of the node at matter-server (`MatterNode.available`).
    # Default `True`: a fixture file (see `_load_fixture` in cli.py) does
    # not carry this field, and a device loaded from a recording should not
    # be falsely treated as unreachable (review fix C1, 2026-09-02 - see
    # `BridgeMatterClient.snapshots` and `Runtime.seed_from_snapshot`).
    available: bool = True

    @classmethod
    def from_raw(cls, node_id: int, raw: Mapping[str, Any]) -> NodeSnapshot:
        attributes: Mapping[str, Any] = raw.get("attributes") or {}

        def text(path: str) -> str:
            value = attributes.get(path)
            return value if isinstance(value, str) else ""

        return cls(
            node_id=node_id,
            vendor_name=text(_VENDOR_NAME_PATH),
            product_name=text(_PRODUCT_NAME_PATH),
            unique_id=text(_UNIQUE_ID_PATH),
            attributes=dict(attributes),
            available=bool(raw.get("available", True)),
        )
