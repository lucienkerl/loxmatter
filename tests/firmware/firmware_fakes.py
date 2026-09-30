"""A `FirmwareSource` without a server, for the firmware tests.

`facts` and `offers` are keyed by address and can be changed by a test
while a job runs; `fail_check`/`fail_start` make the next call raise."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.store import Store
from loxmatter.sources.firmware import FirmwareFacts, UpdateOffer

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nodes"

# Measured on the test Pi, 2026-09-30 (design section 3).
KAJPLATS_OFFER = UpdateOffer(16908288, "1.2.0", 0, 16908287, None, "main-net-dcl")
BILRESA_OFFER = UpdateOffer(17367055, "1.9.15", 17301509, 17367054, None, "main-net-dcl")


def idle_facts(software_version: int, text: str, *, available: bool = True) -> FirmwareFacts:
    return FirmwareFacts(
        available=available,
        has_requestor=True,
        software_version=software_version,
        software_version_string=text,
        spec_version=0x01040000,
        update_state=1,
        update_progress=None,
    )


def register(store: Store, fixture: str) -> tuple[int, str]:
    raw = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    snapshot = NodeSnapshot.from_raw(raw["node_id"], raw)
    return store.register_device(snapshot), snapshot.address


class FakeFirmwareSource:
    technology = "matter"

    def __init__(self) -> None:
        self.connected = True
        self.supported = True
        self.facts: dict[str, FirmwareFacts] = {}
        self.offers: dict[str, UpdateOffer | None] = {}
        self.fail_check: Exception | None = None
        self.fail_start: Exception | None = None
        self.hang_check = False
        self.checked: list[str] = []
        self.started: list[tuple[str, int]] = []
        self.refreshed: list[str] = []
        self.followed: list[str] = []

    def firmware_supported(self) -> bool:
        return self.supported

    def firmware_facts(self, address: str) -> FirmwareFacts | None:
        return self.facts.get(address)

    async def refresh_firmware_facts(self, address: str) -> None:
        self.refreshed.append(address)

    async def check_update(self, address: str) -> UpdateOffer | None:
        self.checked.append(address)
        if self.hang_check:
            await asyncio.Event().wait()
        if self.fail_check is not None:
            raise self.fail_check
        return self.offers.get(address)

    async def start_update(self, address: str, software_version: int) -> None:
        self.started.append((address, software_version))
        if self.fail_start is not None:
            raise self.fail_start

    async def follow(self, address: str, *, seed_even_without_new_paths: bool = False) -> None:
        self.followed.append(address)

    def set_state(self, address: str, update_state: int, progress: int | None = None) -> None:
        self.facts[address] = replace(
            self.facts[address], update_state=update_state, update_progress=progress
        )

    def finish(self, address: str, software_version: int, text: str) -> None:
        self.facts[address] = replace(
            self.facts[address],
            update_state=1,
            update_progress=None,
            software_version=software_version,
            software_version_string=text,
        )
