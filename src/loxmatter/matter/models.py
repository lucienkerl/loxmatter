"""Unveränderliches Abbild dessen, was matter-server über ein Gerät weiß."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# BasicInformation-Cluster auf Endpoint 0.
_VENDOR_NAME_PATH = "0/40/1"
_PRODUCT_NAME_PATH = "0/40/3"
_UNIQUE_ID_PATH = "0/40/18"


class SignalKind(str, Enum):
    ATTRIBUTE = "attribute"
    EVENT = "event"


@dataclass(frozen=True, order=True)
class SignalRef:
    """Verweis auf genau eine Datenquelle eines Geräts.

    Attribut und Event können dieselben Zahlen tragen und sind trotzdem
    verschiedene Dinge — `kind` gehört deshalb zur Identität.
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
    # Erreichbarkeit des Nodes bei matter-server (`MatterNode.available`).
    # Default `True`: eine Fixture-Datei (siehe `_load_fixture` in cli.py)
    # traegt dieses Feld nicht, und ein aus einer Aufzeichnung geladenes
    # Geraet soll nicht faelschlich als unerreichbar gelten (Review-Fix C1,
    # 2026-09-02 - siehe `BridgeMatterClient.snapshots` und
    # `Runtime.seed_from_snapshot`).
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
