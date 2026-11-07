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

"""The migration from Home Assistant, step by step (design 2026-11-07,
sections 3 to 7).

Each public method is one step of the assistant, and each can be repeated:
the assistant may be closed at any point, and a bridge restart in the
middle of the Zigbee step is expected, not exceptional. What has to
survive that lives in `HomeAssistantStore`; everything else is read again
from Home Assistant.

**Nothing in Home Assistant changes before the Zigbee step.** Signing in,
the device list and the Thread network are reads. A Matter device gets a
second controller - Home Assistant keeps it and goes on working - which
is Matter's own multi-admin and needs nothing removed. Pausing ZHA is the
first change, and removing anything is the last step, offered only for
devices that verifiably arrived here.

The two long steps (Matter devices, Zigbee devices) run as one background
job at a time, which the web UI polls: commissioning a Thread device can
take a minute, and a request held open that long dies in a proxy.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from loxmatter import i18n
from loxmatter.commissioning.run import CommissionFailed, ThreadDatasetSource, commission
from loxmatter.homeassistant.client import (
    HomeAssistantApi,
    HomeAssistantAuthError,
    HomeAssistantError,
    HomeAssistantSession,
    authorize_url,
    exchange_code,
    needs_refresh,
    normalised_url,
    probe,
    refresh_tokens,
)
from loxmatter.homeassistant.inventory import Capabilities, Inventory, read_inventory
from loxmatter.matter.client import BridgeMatterClient
from loxmatter.matter.commissioning_progress import CommissioningTracker
from loxmatter.matter.thread_network import ThreadNetworkKeeper
from loxmatter.model.homeassistant_store import HomeAssistantConnection, MigrationResult
from loxmatter.model.store import Store
from loxmatter.sources import bounded_source_call
from loxmatter.zigbee.adopt import register_zigbee_device
from loxmatter.zigbee.migration import NetworkIdentity, backup_identity
from loxmatter.zigbee.runtime import ZigbeeRuntime

if TYPE_CHECKING:
    from loxmatter.api.devices import RuntimeValues

logger = logging.getLogger(__name__)

ApiFactory = Callable[[str, str], AbstractAsyncContextManager[HomeAssistantApi]]
JobKind = Literal["matter", "zigbee"]
ItemState = Literal["waiting", "running", "done", "failed"]

# How long an imported Zigbee device may take to appear in zigpy after
# `device_initialized` - quirks are applied synchronously, so this is a
# safety margin, not a wait for the radio.
_ZIGBEE_APPEAR_SECONDS = 5.0


class MigrationError(Exception):
    """A step that cannot go ahead, with a translated reason and the
    status the API answers with."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class JobItem:
    ha_device_id: str
    name: str
    state: ItemState = "waiting"
    detail: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "ha_device_id": self.ha_device_id,
            "name": self.name,
            "state": self.state,
            "detail": self.detail,
        }


@dataclass
class Job:
    kind: JobKind
    items: list[JobItem] = field(default_factory=list)
    running: bool = True

    def as_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "running": self.running,
            "items": [item.as_json() for item in self.items],
        }


@dataclass(frozen=True)
class _PendingSignIn:
    url: str
    client_id: str


def _default_api_factory(url: str, token: str) -> AbstractAsyncContextManager[HomeAssistantApi]:
    return HomeAssistantSession(url, token)


class HomeAssistantMigration:
    def __init__(
        self,
        store: Store,
        *,
        client: BridgeMatterClient | None,
        runtime: RuntimeValues,
        tracker: CommissioningTracker,
        fetch_dataset: ThreadDatasetSource,
        thread_network: ThreadNetworkKeeper | None,
        zigbee_runtime: ZigbeeRuntime | None,
        api_factory: ApiFactory | None = None,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._store = store
        self._client = client
        self._runtime = runtime
        self._tracker = tracker
        self._fetch_dataset = fetch_dataset
        self._thread_network = thread_network
        self._zigbee_runtime = zigbee_runtime
        self._api_factory = api_factory or _default_api_factory
        self._session_factory = session_factory
        self._pending: dict[str, _PendingSignIn] = {}
        self._inventory: Inventory | None = None
        self._job: Job | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ sign-in --

    def status(self) -> dict[str, object]:
        connection = self._store.homeassistant.connection()
        return {
            "signed_in": connection is not None,
            "url": connection.url if connection is not None else None,
            "job": self._job.as_json() if self._job is not None else None,
        }

    async def probe(self, url: str) -> str:
        base = normalised_url(url)
        if not await probe(base, session_factory=self._session_factory):
            raise MigrationError(i18n.t("api.homeassistant.fail_not_found", url=base), 404)
        return base

    def start_sign_in(self, url: str, origin: str) -> str:
        """The address the browser goes to. `origin` is this bridge's own
        address as the browser sees it - Home Assistant sends the browser
        back there, so it must be the one in the address bar."""
        base = normalised_url(url)
        client_id = normalised_url(origin) + "/"
        state = secrets.token_urlsafe(24)
        # One pending sign-in per browser tab at most; an old one is
        # useless the moment a new one starts.
        self._pending = {state: _PendingSignIn(url=base, client_id=client_id)}
        return authorize_url(
            base, client_id=client_id, redirect_uri=f"{client_id}?ha_auth=1", state=state
        )

    async def finish_sign_in(self, code: str, state: str) -> None:
        pending = self._pending.pop(state, None)
        if pending is None:
            raise MigrationError(i18n.t("api.homeassistant.fail_sign_in_expired"), 400)
        tokens = await exchange_code(
            pending.url,
            client_id=pending.client_id,
            code=code,
            session_factory=self._session_factory,
        )
        self._store.homeassistant.save_connection(
            HomeAssistantConnection(
                url=pending.url,
                client_id=pending.client_id,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at,
            )
        )
        self._inventory = None

    async def sign_in_with_token(self, url: str, token: str) -> None:
        """The fallback: a long-lived access token from the user's profile
        page in Home Assistant. Checked by connecting once."""
        base = normalised_url(url)
        async with self._api_factory(base, token.strip()) as api:
            await api.call("get_config")
        self._store.homeassistant.save_connection(
            HomeAssistantConnection(
                url=base,
                client_id=None,
                access_token=token.strip(),
                refresh_token=None,
                expires_at=None,
            )
        )
        self._inventory = None

    def sign_out(self) -> None:
        self._store.homeassistant.forget()
        self._inventory = None

    @contextlib.asynccontextmanager
    async def _api(self) -> AsyncIterator[HomeAssistantApi]:
        connection = self._store.homeassistant.connection()
        if connection is None:
            raise MigrationError(i18n.t("api.homeassistant.fail_not_signed_in"), 403)
        if (
            needs_refresh(connection.expires_at)
            and connection.refresh_token is not None
            and connection.client_id is not None
        ):
            try:
                tokens = await refresh_tokens(
                    connection.url,
                    client_id=connection.client_id,
                    refresh_token=connection.refresh_token,
                    session_factory=self._session_factory,
                )
            except HomeAssistantAuthError:
                # The user revoked it in Home Assistant, or it expired after
                # 90 days unused: the only way on is a new sign-in.
                self._store.homeassistant.forget()
                raise
            connection = replace(
                connection,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at,
            )
            self._store.homeassistant.save_connection(connection)
        async with self._api_factory(connection.url, connection.access_token) as api:
            yield api

    # ---------------------------------------------------------- inventory --

    def _capabilities(self) -> Capabilities:
        thread = self._thread_network is not None and (
            self._thread_network.status.state != "unknown"
        )
        zigbee = self._zigbee_runtime is not None and self._zigbee_runtime.current() is not None
        done = frozenset(
            key
            for key, result in self._store.homeassistant.results().items()
            if result.state == "done"
        )
        return Capabilities(thread=thread, zigbee=zigbee, done=done)

    async def inventory(self, *, fresh: bool = True) -> Inventory:
        if self._inventory is None or fresh:
            async with self._api() as api:
                self._inventory = await read_inventory(api, self._capabilities())
        return self._inventory

    # ------------------------------------------------------------- thread --

    async def adopt_thread_network(self) -> dict[str, object]:
        """Joins Home Assistant's preferred Thread network - what lets this
        bridge reach the Thread devices it takes over."""
        if self._thread_network is None:
            raise MigrationError(i18n.t("api.homeassistant.fail_no_thread"), 409)
        async with self._api() as api:
            answer = await api.call("thread/list_datasets")
            datasets = answer.get("datasets") if isinstance(answer, dict) else None
            chosen = None
            for item in datasets if isinstance(datasets, list) else []:
                if isinstance(item, dict) and (chosen is None or item.get("preferred")):
                    chosen = item
            if chosen is None:
                raise MigrationError(i18n.t("api.homeassistant.fail_no_thread_network"), 404)
            tlv = await api.call("thread/get_dataset_tlv", dataset_id=chosen["dataset_id"])
        dataset = tlv.get("tlv") if isinstance(tlv, dict) else None
        if not isinstance(dataset, str):
            raise MigrationError(i18n.t("api.homeassistant.fail_no_thread_network"), 404)
        outcome = await self._thread_network.adopt(dataset)
        return {
            "outcome": outcome,
            "network_name": chosen.get("network_name"),
            "channel": chosen.get("channel"),
        }

    # --------------------------------------------------------------- jobs --

    def job(self) -> dict[str, object] | None:
        return self._job.as_json() if self._job is not None else None

    def _start(self, kind: JobKind, items: list[JobItem], work: Callable[[Job], Any]) -> Job:
        if self._job is not None and self._job.running:
            raise MigrationError(i18n.t("api.homeassistant.fail_busy"), 409)
        job = Job(kind=kind, items=items)
        self._job = job

        async def _run() -> None:
            try:
                await work(job)
            except Exception as exc:
                logger.exception("Home Assistant migration step %s failed", kind)
                for item in job.items:
                    if item.state in ("waiting", "running"):
                        item.state = "failed"
                        item.detail = str(exc)
            finally:
                job.running = False

        self._task = asyncio.create_task(_run())
        return job

    async def wait(self) -> None:
        """For the tests: the running job's end."""
        if self._task is not None:
            await self._task

    async def aclose(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def _record(self, kind: str, item: JobItem, device_id: int | None) -> None:
        self._store.homeassistant.record(
            MigrationResult(
                ha_device_id=item.ha_device_id,
                kind=kind,
                state="done" if item.state == "done" else "failed",
                device_id=device_id,
                detail=item.detail,
            )
        )

    # ------------------------------------------------------------- matter --

    async def start_matter(self, ha_device_ids: list[str]) -> dict[str, object]:
        """Opens a commissioning window in Home Assistant for each chosen
        Matter device and commissions it here with the code that answers.
        Home Assistant keeps the device as well (Matter multi-admin)."""
        if self._client is None:
            raise MigrationError(i18n.t("api.devices.fail_no_matter_client"), 503)
        client = self._client
        inventory = await self.inventory()
        chosen = [
            device
            for ha_id in ha_device_ids
            if (device := inventory.device(ha_id)) is not None
            and device.kind in ("matter_wifi", "matter_thread", "matter_bridge")
            and device.availability == "ready"
        ]
        items = [JobItem(device.ha_device_id, device.name) for device in chosen]
        rooms = {device.ha_device_id: device.room for device in chosen}

        async def work(job: Job) -> None:
            for item in job.items:
                item.state = "running"
                device_id: int | None = None
                try:
                    async with self._api() as api:
                        window = await api.call(
                            "matter/open_commissioning_window", device_id=item.ha_device_id
                        )
                    code = window.get("setup_manual_code") if isinstance(window, dict) else None
                    if not isinstance(code, str) or not code:
                        raise HomeAssistantError(i18n.t("api.homeassistant.fail_no_code"))
                    result = await commission(
                        code=code,
                        room=rooms.get(item.ha_device_id),
                        discriminator=None,
                        client=client,
                        store=self._store,
                        runtime=self._runtime,
                        tracker=self._tracker,
                        fetch_dataset=self._fetch_dataset,
                    )
                    device_id = result.device_id
                    self._store.rename_device(device_id, item.name)
                    item.state = "done"
                except (HomeAssistantError, MigrationError) as exc:
                    item.state, item.detail = "failed", str(exc)
                except CommissionFailed as exc:
                    item.state, item.detail = "failed", exc.detail
                self._record("matter", item, device_id)

        return self._start("matter", items, work).as_json()

    # ------------------------------------------------------------- zigbee --

    def _zigbee_source(self) -> Any:
        source = self._zigbee_runtime.current() if self._zigbee_runtime is not None else None
        if source is None:
            raise MigrationError(i18n.t("api.homeassistant.fail_no_zigbee"), 409)
        return source

    def zigbee_status(self) -> dict[str, object]:
        """Where the Zigbee step stands: whether ZHA's network is saved,
        which network the stick runs, and whether the two are the same."""
        backup = self._store.homeassistant.zigbee_backup()
        wanted = backup_identity(backup) if backup is not None else None
        source = self._zigbee_runtime.current() if self._zigbee_runtime is not None else None
        current: NetworkIdentity | None = source.network_identity() if source is not None else None
        return {
            "backup": wanted.as_json() if wanted is not None else None,
            "stick": current.as_json() if current is not None else None,
            "stick_configured": source is not None,
            "same_network": bool(
                wanted is not None
                and current is not None
                and wanted.extended_pan_id is not None
                and wanted.extended_pan_id == current.extended_pan_id
            ),
            "same_coordinator": bool(
                wanted is not None
                and current is not None
                and wanted.coordinator_ieee is not None
                and wanted.coordinator_ieee == current.coordinator_ieee
            ),
        }

    async def pause_zha(self) -> dict[str, object]:
        """Saves ZHA's network, then disables ZHA - the first step that
        changes Home Assistant, so the web UI asks before it calls this.

        Both in this order: a disabled ZHA cannot make a backup any more.
        A repeated call finds ZHA already disabled and keeps the backup it
        saved the first time."""
        async with self._lock, self._api() as api:
            inventory = await read_inventory(api, self._capabilities())
            self._inventory = inventory
            if inventory.zha_entry_id is None:
                raise MigrationError(i18n.t("api.homeassistant.fail_no_zha"), 404)
            if not inventory.zha_disabled:
                answer = await api.call("zha/network/backups/create")
                backup = answer.get("backup") if isinstance(answer, dict) else None
                if not isinstance(backup, dict):
                    raise MigrationError(i18n.t("api.homeassistant.fail_backup"), 502)
                self._store.homeassistant.save_zigbee_backup(backup)
                await api.call(
                    "config_entries/disable",
                    entry_id=inventory.zha_entry_id,
                    disabled_by="user",
                )
                self._inventory = replace(inventory, zha_disabled=True)
            elif self._store.homeassistant.zigbee_backup() is None:
                raise MigrationError(i18n.t("api.homeassistant.fail_paused_without_backup"), 409)
        return self.zigbee_status()

    async def restore_zigbee_network(self, *, overwrite_ieee: bool) -> dict[str, object]:
        """Writes ZHA's network onto this bridge's own stick (path 3). The
        old stick must be unplugged by then: two coordinators with the same
        network fight over every device."""
        backup = self._store.homeassistant.zigbee_backup()
        if backup is None:
            raise MigrationError(i18n.t("api.homeassistant.fail_no_backup"), 409)
        source = self._zigbee_source()
        try:
            await source.restore_network(backup, overwrite_ieee=overwrite_ieee)
        except Exception as exc:
            logger.warning("Restoring the ZHA network failed: %s", exc)
            if overwrite_ieee:
                raise MigrationError(i18n.t("api.homeassistant.fail_restore_ieee"), 409) from exc
            raise MigrationError(i18n.t("api.homeassistant.fail_restore"), 502) from exc
        return self.zigbee_status()

    async def start_zigbee(self, ha_device_ids: list[str]) -> dict[str, object]:
        """Enters the chosen Zigbee devices into this bridge's network - no
        re-pairing, see `zigbee/migration.py`. Only once the stick runs
        ZHA's network: entered into any other network, a device would be
        listed and never answer."""
        status = self.zigbee_status()
        if not status["same_network"]:
            raise MigrationError(i18n.t("api.homeassistant.fail_network_differs"), 409)
        source = self._zigbee_source()
        inventory = await self.inventory(fresh=self._inventory is None)
        chosen = [
            device
            for ha_id in ha_device_ids
            if (device := inventory.device(ha_id)) is not None
            and device.zigbee is not None
            and device.availability in ("ready", "needs_zigbee")
        ]
        items = [JobItem(device.ha_device_id, device.name) for device in chosen]
        by_id = {device.ha_device_id: device for device in chosen}

        async def work(job: Job) -> None:
            imported = [device.zigbee for device in chosen if device.zigbee is not None]
            entered = set(source.import_devices(imported))
            for item in job.items:
                device = by_id[item.ha_device_id]
                assert device.zigbee is not None
                ieee = device.zigbee.ieee
                item.state = "running"
                device_id: int | None = None
                if ieee not in entered:
                    item.state = "failed"
                    item.detail = i18n.t("api.homeassistant.fail_zigbee_device")
                    self._record("zigbee", item, None)
                    continue
                snapshot = None
                deadline = asyncio.get_running_loop().time() + _ZIGBEE_APPEAR_SECONDS
                while snapshot is None:
                    try:
                        snapshot = await source.snapshot_for(ieee)
                    except Exception as exc:  # noqa: BLE001 - a device away right now
                        logger.info("Zigbee device %s gave no snapshot yet: %s", ieee, exc)
                    if snapshot is None:
                        if asyncio.get_running_loop().time() > deadline:
                            break
                        await asyncio.sleep(0.5)
                if snapshot is None:
                    item.state = "failed"
                    item.detail = i18n.t("api.homeassistant.fail_zigbee_device")
                else:
                    device_id = register_zigbee_device(
                        self._store, snapshot, name=device.name, room=device.room
                    )
                    try:
                        await bounded_source_call(
                            source.follow(ieee, seed_even_without_new_paths=True)
                        )
                    except Exception:  # noqa: BLE001 - follow-up work only
                        # A sleeping sensor answers at its next wake-up; the
                        # device is registered all the same.
                        logger.info("no first values yet for Zigbee device %s", ieee)
                    item.state = "done"
                self._record("zigbee", item, device_id)

        return self._start("zigbee", items, work).as_json()

    # ------------------------------------------------------------ cleanup --

    async def clean_up(self, ha_device_ids: list[str], *, remove_zha: bool) -> dict[str, object]:
        """Removes devices from Home Assistant that arrived here - only
        those, whatever the request lists - and on request ZHA itself.

        A Matter device is removed through its config entry, which makes
        Home Assistant's Matter integration take its own fabric off the
        device; this bridge's fabric stays. A Zigbee device needs nothing
        per device: ZHA is paused, and removing ZHA removes its devices."""
        results = self._store.homeassistant.results()
        removed: list[str] = []
        failed: list[str] = []
        async with self._api() as api:
            inventory = await read_inventory(api, self._capabilities())
            self._inventory = inventory
            for ha_id in ha_device_ids:
                result = results.get(ha_id)
                if result is None or result.state != "done" or result.kind != "matter":
                    continue
                if inventory.matter_entry_id is None:
                    break
                try:
                    await api.call(
                        "config/device_registry/remove_config_entry",
                        device_id=ha_id,
                        config_entry_id=inventory.matter_entry_id,
                    )
                except HomeAssistantError as exc:
                    logger.warning("Home Assistant kept Matter device %s: %s", ha_id, exc)
                    failed.append(ha_id)
                    continue
                self._store.homeassistant.record(replace(result, cleaned_up=True))
                removed.append(ha_id)
            zha_removed = False
            if remove_zha and inventory.zha_entry_id is not None:
                await api.delete(f"/api/config/config_entries/entry/{inventory.zha_entry_id}")
                zha_removed = True
                for result in results.values():
                    if result.kind == "zigbee" and result.state == "done":
                        self._store.homeassistant.record(replace(result, cleaned_up=True))
        return {"removed": removed, "failed": failed, "zha_removed": zha_removed}
