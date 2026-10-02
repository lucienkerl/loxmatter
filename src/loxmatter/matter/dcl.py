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
"""Human-readable vendor and product names from the CSA DCL (design
2026-10-02, section 7).

Cache first: a found entry is kept for good, "not in the DCL" for seven
days, a network failure not at all. Test vendor ids are never asked."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from loxmatter import i18n
from loxmatter.matter.dcl_types import DclModel, DclVendor
from loxmatter.model.dcl_store import DclStore

__all__ = ["DclDirectory", "DclModel", "DclVendor", "ProductLabel", "fetch_json", "is_test_vendor"]

logger = logging.getLogger(__name__)

DCL_BASE: Final = "https://on.dcl.csa-iot.org"
_TIMEOUT_SECONDS: Final = 5.0
_RETRY_MISSING_AFTER: Final = timedelta(days=7)
_HINT_POWER_CYCLE: Final = 1 << 0

Fetch = Callable[[str], Awaitable["dict[str, Any] | None"]]


def is_test_vendor(vendor_id: int) -> bool:
    return 0xFFF1 <= vendor_id <= 0xFFF4


async def fetch_json(url: str) -> dict[str, Any] | None:
    """One GET; `None` for 404, raises on anything that is not an answer."""
    # Lazily imported, so tests never need to load aiohttp (same as the
    # project's other outbound calls).
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session, session.get(url) as response:
        if response.status == 404:
            return None
        response.raise_for_status()
        data = await response.json()
        return data if isinstance(data, dict) else None


@dataclass(frozen=True)
class ProductLabel:
    product: str
    detail: str
    pairing_hint: str


class DclDirectory:
    def __init__(
        self,
        store: DclStore,
        fetch: Fetch = fetch_json,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._fetch = fetch
        self._now = now

    def _fresh_missing(self, fetched_at: str) -> bool:
        try:
            moment = datetime.fromisoformat(fetched_at)
        except ValueError:
            return False
        return self._now() - moment < _RETRY_MISSING_AFTER

    async def vendor(self, vendor_id: int) -> DclVendor | None:
        if is_test_vendor(vendor_id):
            return None
        cached = self._store.vendor(vendor_id)
        if cached is not None and (
            cached.entry is not None or self._fresh_missing(cached.fetched_at)
        ):
            return cached.entry
        try:
            data = await self._fetch(f"{DCL_BASE}/dcl/vendorinfo/vendors/{vendor_id}")
        except Exception as exc:  # noqa: BLE001 - offline is a normal state here
            logger.info("DCL vendor %s not reachable: %s", vendor_id, exc)
            return None
        info = (data or {}).get("vendorInfo") or {}
        name = info.get("vendorName")
        entry = (
            DclVendor(vendor_id, name.strip()) if isinstance(name, str) and name.strip() else None
        )
        self._store.put_vendor(vendor_id, entry, self._now().isoformat())
        return entry

    async def model(self, vendor_id: int, product_id: int) -> DclModel | None:
        if is_test_vendor(vendor_id):
            return None
        cached = self._store.model(vendor_id, product_id)
        if cached is not None and (
            cached.entry is not None or self._fresh_missing(cached.fetched_at)
        ):
            return cached.entry
        try:
            data = await self._fetch(f"{DCL_BASE}/dcl/model/models/{vendor_id}/{product_id}")
        except Exception as exc:  # noqa: BLE001 - offline is a normal state here
            logger.info("DCL model %s/%s not reachable: %s", vendor_id, product_id, exc)
            return None
        model = (data or {}).get("model") or {}
        name = model.get("productName")
        entry = None
        if isinstance(name, str) and name.strip():
            instruction = model.get("commissioningModeInitialStepsInstruction")
            device_type = model.get("deviceTypeId")
            entry = DclModel(
                vendor_id=vendor_id,
                product_id=product_id,
                name=name.strip(),
                part_number=(model.get("partNumber") or None),
                device_type=device_type if isinstance(device_type, int) else None,
                initial_steps_hint=int(model.get("commissioningModeInitialStepsHint") or 0),
                initial_steps_instruction=(
                    instruction.strip()
                    if isinstance(instruction, str) and instruction.strip()
                    else None
                ),
            )
        self._store.put_model(vendor_id, product_id, entry, self._now().isoformat())
        return entry

    async def product_label(self, vendor_id: int | None, product_id: int | None) -> ProductLabel:
        if vendor_id is None or product_id is None:
            return ProductLabel(
                product=i18n.t("web.commissioning.numeric_code_device"),
                detail=i18n.t("web.commissioning.product_unknown_until_done"),
                pairing_hint=i18n.t("web.commissioning.hint_manual"),
            )
        if is_test_vendor(vendor_id):
            return ProductLabel(
                product=i18n.t("web.commissioning.test_device", vendor=f"0x{vendor_id:04X}"),
                detail="",
                pairing_hint=i18n.t("web.commissioning.hint_manual"),
            )
        vendor = await self.vendor(vendor_id)
        model = await self.model(vendor_id, product_id)
        if model is None:
            return ProductLabel(
                product=i18n.t(
                    "web.commissioning.unknown_product",
                    vendor=f"0x{vendor_id:04X}",
                    product=f"0x{product_id:04X}",
                ),
                detail=vendor.name if vendor is not None else "",
                pairing_hint=i18n.t("web.commissioning.hint_manual"),
            )
        detail = " · ".join(
            part for part in (vendor.name if vendor else None, model.part_number) if part
        )
        if model.initial_steps_instruction:
            hint = model.initial_steps_instruction
        elif model.initial_steps_hint & _HINT_POWER_CYCLE:
            hint = i18n.t("web.commissioning.hint_power_cycle")
        else:
            hint = i18n.t("web.commissioning.hint_manual")
        return ProductLabel(product=model.name, detail=detail, pairing_hint=hint)
