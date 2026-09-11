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
from typing import Any, Final, Literal, cast, get_args

# BasicInformation cluster on endpoint 0.
_VENDOR_NAME_PATH = "0/40/1"
_PRODUCT_NAME_PATH = "0/40/3"
_UNIQUE_ID_PATH = "0/40/18"

# Which kind of source a device comes from (design 2026-09-11, section
# 3.1). Lives here, next to `NodeSnapshot`, and not in `loxmatter.sources`:
# `sources` imports this module, so the reverse import would be a cycle.
Technology = Literal["matter", "zigbee"]

_TECHNOLOGIES: Final = frozenset(get_args(Technology))


def parse_technology(value: str) -> Technology:
    """Narrows a stored string to `Technology`, loudly.

    A value the code does not know means the database was written by a
    newer loxmatter than the one reading it; reading on as if it were
    Matter would send Matter commands to a device that is not one."""
    if value not in _TECHNOLOGIES:
        raise ValueError(f"unknown device technology {value!r}")
    return cast(Technology, value)


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
    # Which source produced the snapshot and the device's address there
    # (design 2026-09-11, section 3.3). For Matter the address is the node
    # ID as text; `BridgeMatterClient` converts back where matter-server
    # needs an integer.
    technology: Technology
    address: str
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
            technology="matter",
            address=str(node_id),
            vendor_name=text(_VENDOR_NAME_PATH),
            product_name=text(_PRODUCT_NAME_PATH),
            unique_id=text(_UNIQUE_ID_PATH),
            attributes=dict(attributes),
            available=bool(raw.get("available", True)),
        )
