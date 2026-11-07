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

"""The migration's steps against a fake Home Assistant, a fake Thread
keeper and a fake Zigbee source - what each step asks Home Assistant to
do, and what it leaves in the store."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fake_homeassistant import ZHA_BACKUP, FakeHomeAssistant

from loxmatter.commissioning.run import CommissionFailed, CommissionResult
from loxmatter.homeassistant import migration as migration_module
from loxmatter.homeassistant.client import Tokens
from loxmatter.homeassistant.migration import HomeAssistantMigration, MigrationError
from loxmatter.matter.commissioning_progress import CommissioningTracker
from loxmatter.matter.models import NodeSnapshot
from loxmatter.model.homeassistant_store import HomeAssistantConnection
from loxmatter.model.store import Store
from loxmatter.zigbee.migration import ImportedDevice, NetworkIdentity

DOOR_IEEE = "00:15:8d:00:01:02:03:04"


def matter_snapshot(node_id: int) -> NodeSnapshot:
    return NodeSnapshot(
        technology="matter",
        address=str(node_id),
        vendor_name="Eve",
        product_name="Energy",
        unique_id=f"u{node_id}",
        attributes={"1/6/0": True},
    )


def zigbee_snapshot(ieee: str) -> NodeSnapshot:
    return NodeSnapshot(
        technology="zigbee",
        address=ieee,
        vendor_name="LUMI",
        product_name="lumi.sensor_magnet.aq2",
        unique_id=ieee,
        attributes={"1/6/0": False},
    )


class FakeRuntime:
    def last_values_for(self, device_id: int) -> dict[str, Any]:
        return {}


class FakeKeeper:
    def __init__(self) -> None:
        self.adopted: list[str] = []

    class _Status:
        state = "formed"

    status = _Status()

    async def adopt(self, dataset: str) -> str:
        self.adopted.append(dataset)
        return "adopted"


class FakeSource:
    def __init__(self, identity: NetworkIdentity | None) -> None:
        self.identity = identity
        self.restored: list[tuple[dict[str, Any], bool]] = []
        self.imported: list[ImportedDevice] = []
        self.followed: list[str] = []

    def network_identity(self) -> NetworkIdentity | None:
        return self.identity

    async def restore_network(self, backup: dict[str, Any], *, overwrite_ieee: bool) -> None:
        self.restored.append((backup, overwrite_ieee))
        self.identity = NetworkIdentity(
            extended_pan_id="aa:bb:cc:dd:ee:ff:00:11",
            coordinator_ieee="00:12:4b:00:aa:bb:cc:dd",
            channel=15,
        )

    def import_devices(self, devices: list[ImportedDevice]) -> list[str]:
        self.imported.extend(devices)
        return [device.ieee for device in devices]

    async def snapshot_for(self, ieee: str) -> NodeSnapshot | None:
        return zigbee_snapshot(ieee)

    async def follow(self, ieee: str, *, seed_even_without_new_paths: bool = False) -> None:
        self.followed.append(ieee)


class FakeZigbeeRuntime:
    def __init__(self, source: FakeSource | None) -> None:
        self.source = source

    def current(self) -> FakeSource | None:
        return self.source


OTHER_NETWORK = NetworkIdentity(
    extended_pan_id="11:11:11:11:11:11:11:11",
    coordinator_ieee="00:00:00:00:00:00:00:01",
    channel=20,
)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "t.sqlite")


@pytest.fixture
def ha() -> FakeHomeAssistant:
    return FakeHomeAssistant()


def build(
    store: Store,
    ha: FakeHomeAssistant,
    *,
    source: FakeSource | None = None,
    keeper: FakeKeeper | None = None,
    signed_in: bool = True,
) -> HomeAssistantMigration:
    if signed_in:
        store.homeassistant.save_connection(
            HomeAssistantConnection(
                url="http://ha:8123",
                client_id=None,
                access_token="token",
                refresh_token=None,
                expires_at=None,
            )
        )

    async def no_dataset() -> str:
        raise AssertionError("not needed")

    return HomeAssistantMigration(
        store,
        client=object(),  # type: ignore[arg-type]
        runtime=FakeRuntime(),  # type: ignore[arg-type]
        tracker=CommissioningTracker(),
        fetch_dataset=no_dataset,
        thread_network=keeper,  # type: ignore[arg-type]
        zigbee_runtime=FakeZigbeeRuntime(source),  # type: ignore[arg-type]
        api_factory=ha.factory,
    )


# ------------------------------------------------------------------ sign-in --


async def test_the_sign_in_address_carries_this_bridge_as_client_and_a_state(store, ha):
    migration = build(store, ha, signed_in=False)

    url = migration.start_sign_in("homeassistant.local:8123", "http://192.168.1.20:8080")

    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "http://homeassistant.local:8123/auth/authorize"
    )
    assert query["client_id"] == ["http://192.168.1.20:8080/"]
    assert query["redirect_uri"] == ["http://192.168.1.20:8080/?ha_auth=1"]
    assert query["state"][0]


async def test_finishing_the_sign_in_stores_the_tokens(store, ha, monkeypatch):
    migration = build(store, ha, signed_in=False)
    url = migration.start_sign_in("http://ha:8123", "http://bridge:8080")
    state = parse_qs(urlsplit(url).query)["state"][0]
    exchanged: list[dict[str, Any]] = []

    async def fake_exchange(base_url: str, **kwargs: Any) -> Tokens:
        exchanged.append({"base_url": base_url, **kwargs})
        return Tokens(access_token="a", refresh_token="r", expires_at=123.0)

    monkeypatch.setattr(migration_module, "exchange_code", fake_exchange)

    await migration.finish_sign_in("the-code", state)

    assert exchanged[0]["code"] == "the-code"
    assert exchanged[0]["client_id"] == "http://bridge:8080/"
    connection = store.homeassistant.connection()
    assert connection is not None
    assert (connection.url, connection.access_token, connection.refresh_token) == (
        "http://ha:8123",
        "a",
        "r",
    )


async def test_a_sign_in_with_an_unknown_state_is_refused(store, ha):
    migration = build(store, ha, signed_in=False)

    with pytest.raises(MigrationError):
        await migration.finish_sign_in("code", "forged")
    assert store.homeassistant.connection() is None


async def test_a_token_is_checked_by_connecting_once_before_it_is_kept(store, ha):
    migration = build(store, ha, signed_in=False)

    await migration.sign_in_with_token("http://ha:8123", "  a-long-lived-access-token  ")

    assert ha.tokens == [("http://ha:8123", "a-long-lived-access-token")]
    assert ha.commands() == ["get_config"]
    assert migration.status()["signed_in"] is True


async def test_signing_out_forgets_the_tokens_and_the_zigbee_backup(store, ha):
    migration = build(store, ha)
    store.homeassistant.save_zigbee_backup(ZHA_BACKUP)

    migration.sign_out()

    assert store.homeassistant.connection() is None
    assert store.homeassistant.zigbee_backup() is None


async def test_without_a_sign_in_a_step_asks_for_one(store, ha):
    migration = build(store, ha, signed_in=False)

    with pytest.raises(MigrationError) as raised:
        await migration.inventory()
    assert raised.value.status == 403


# ------------------------------------------------------------------- thread --


async def test_the_thread_step_joins_home_assistants_preferred_network(store, ha):
    keeper = FakeKeeper()
    migration = build(store, ha, keeper=keeper)

    result = await migration.adopt_thread_network()

    assert keeper.adopted == ["0e080000000000010000"]
    assert result == {"outcome": "adopted", "network_name": "ha-thread-4f2a", "channel": 15}
    assert ("thread/get_dataset_tlv", {"dataset_id": "ds1"}) in ha.calls


async def test_the_thread_step_needs_a_border_router(store, ha):
    migration = build(store, ha, keeper=None)

    with pytest.raises(MigrationError):
        await migration.adopt_thread_network()


# ------------------------------------------------------------------- matter --


async def test_each_matter_device_is_commissioned_with_a_code_from_home_assistant(
    store, ha, monkeypatch
):
    codes: list[tuple[str, str | None]] = []

    async def fake_commission(*, code: str, room: str | None, **kwargs: Any) -> CommissionResult:
        codes.append((code, room))
        snapshot = matter_snapshot(10 + len(codes))
        device_id = kwargs["store"].register_device(snapshot, room=room)
        return CommissionResult(device_id=device_id, snapshot=snapshot)

    monkeypatch.setattr(migration_module, "commission", fake_commission)
    migration = build(store, ha, keeper=FakeKeeper())

    await migration.start_matter(["dev-plug", "dev-bridge", "dev-sonos"])
    await migration.wait()

    assert codes == [("3497-011-2332", "Küche"), ("3497-011-2332", None)]
    windows = [p for c, p in ha.calls if c == "matter/open_commissioning_window"]
    assert windows == [{"device_id": "dev-plug"}, {"device_id": "dev-bridge"}]
    labels = sorted(device.label for device in store.devices())
    assert labels == ["Hue Bridge", "Kaffeemaschine"]
    job = migration.job()
    assert job is not None
    assert job["running"] is False
    assert [item["state"] for item in job["items"]] == ["done", "done"]  # type: ignore[index]
    results = store.homeassistant.results()
    assert results["dev-plug"].state == "done"
    assert results["dev-plug"].kind == "matter"


async def test_a_failed_device_is_reported_and_the_next_one_still_runs(store, ha, monkeypatch):
    calls = 0

    async def fake_commission(*, code: str, room: str | None, **kwargs: Any) -> CommissionResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CommissionFailed("other", "too many fabrics", 422)
        snapshot = matter_snapshot(20)
        device_id = kwargs["store"].register_device(snapshot, room=room)
        return CommissionResult(device_id=device_id, snapshot=snapshot)

    monkeypatch.setattr(migration_module, "commission", fake_commission)
    migration = build(store, ha, keeper=FakeKeeper())

    await migration.start_matter(["dev-plug", "dev-bridge"])
    await migration.wait()

    job = migration.job()
    assert job is not None
    items = job["items"]
    assert [(i["state"], i["detail"]) for i in items] == [  # type: ignore[union-attr]
        ("failed", "too many fabrics"),
        ("done", None),
    ]
    assert store.homeassistant.results()["dev-plug"].state == "failed"


async def test_a_thread_device_does_not_start_without_a_thread_network(store, ha, monkeypatch):
    async def fake_commission(**kwargs: Any) -> CommissionResult:
        raise AssertionError("must not be called")

    monkeypatch.setattr(migration_module, "commission", fake_commission)
    migration = build(store, ha, keeper=None)

    job = await migration.start_matter(["dev-motion"])

    assert job["items"] == []


# ------------------------------------------------------------------- zigbee --


async def test_pausing_zha_saves_the_network_before_it_disables_zha(store, ha):
    migration = build(store, ha, source=FakeSource(OTHER_NETWORK))

    status = await migration.pause_zha()

    commands = ha.commands()
    assert commands.index("zha/network/backups/create") < commands.index("config_entries/disable")
    disable = next(p for c, p in ha.calls if c == "config_entries/disable")
    assert disable == {"entry_id": "entry-zha", "disabled_by": "user"}
    assert store.homeassistant.zigbee_backup() == ZHA_BACKUP
    assert status["backup"] == {
        "extended_pan_id": "aa:bb:cc:dd:ee:ff:00:11",
        "coordinator_ieee": "00:12:4b:00:aa:bb:cc:dd",
        "channel": 15,
    }
    assert status["same_network"] is False


async def test_pausing_again_keeps_the_first_backup(store, ha):
    migration = build(store, ha)
    await migration.pause_zha()
    ha.calls.clear()

    await migration.pause_zha()

    assert "zha/network/backups/create" not in ha.commands()
    assert "config_entries/disable" not in ha.commands()


async def test_zha_paused_elsewhere_without_a_backup_is_refused(store, ha):
    ha.zha_disabled = True
    migration = build(store, ha)

    with pytest.raises(MigrationError):
        await migration.pause_zha()


async def test_a_moved_stick_runs_the_same_network(store, ha):
    same = NetworkIdentity(
        extended_pan_id="aa:bb:cc:dd:ee:ff:00:11",
        coordinator_ieee="00:12:4b:00:aa:bb:cc:dd",
        channel=15,
    )
    migration = build(store, ha, source=FakeSource(same))
    store.homeassistant.save_zigbee_backup(ZHA_BACKUP)

    status = migration.zigbee_status()

    assert (status["same_network"], status["same_coordinator"]) == (True, True)


async def test_the_network_is_written_onto_an_own_stick(store, ha):
    source = FakeSource(OTHER_NETWORK)
    migration = build(store, ha, source=source)
    store.homeassistant.save_zigbee_backup(ZHA_BACKUP)

    status = await migration.restore_zigbee_network(overwrite_ieee=True)

    assert source.restored == [(ZHA_BACKUP, True)]
    assert status["same_network"] is True


async def test_devices_are_not_entered_into_a_different_network(store, ha):
    migration = build(store, ha, source=FakeSource(OTHER_NETWORK))
    store.homeassistant.save_zigbee_backup(ZHA_BACKUP)

    with pytest.raises(MigrationError):
        await migration.start_zigbee(["dev-door"])


async def test_zigbee_devices_are_entered_with_name_and_room(store, ha):
    source = FakeSource(OTHER_NETWORK)
    migration = build(store, ha, source=source)
    store.homeassistant.save_zigbee_backup(ZHA_BACKUP)
    await migration.restore_zigbee_network(overwrite_ieee=True)

    await migration.start_zigbee(["dev-door", "dev-remote", "dev-plug"])
    await migration.wait()

    assert [device.ieee for device in source.imported] == [DOOR_IEEE]
    assert source.followed == [DOOR_IEEE]
    [device] = store.devices()
    assert (device.label, device.room, device.technology) == ("Haustür", "Flur", "zigbee")
    assert store.homeassistant.results()["dev-door"].state == "done"


# ------------------------------------------------------------------ cleanup --


async def test_clean_up_removes_only_devices_that_arrived(store, ha, monkeypatch):
    async def fake_commission(*, code: str, room: str | None, **kwargs: Any) -> CommissionResult:
        snapshot = matter_snapshot(30)
        device_id = kwargs["store"].register_device(snapshot, room=room)
        return CommissionResult(device_id=device_id, snapshot=snapshot)

    monkeypatch.setattr(migration_module, "commission", fake_commission)
    migration = build(store, ha, keeper=FakeKeeper())
    await migration.start_matter(["dev-plug"])
    await migration.wait()

    result = await migration.clean_up(["dev-plug", "dev-motion", "dev-sonos"], remove_zha=False)

    removed = [p for c, p in ha.calls if c == "config/device_registry/remove_config_entry"]
    assert removed == [{"device_id": "dev-plug", "config_entry_id": "entry-matter"}]
    assert result == {"removed": ["dev-plug"], "failed": [], "zha_removed": False}
    assert store.homeassistant.results()["dev-plug"].cleaned_up is True
    assert ha.deleted == []


async def test_clean_up_removes_zha_only_when_asked(store, ha):
    migration = build(store, ha)

    result = await migration.clean_up([], remove_zha=True)

    assert ha.deleted == ["/api/config/config_entries/entry/entry-zha"]
    assert result["zha_removed"] is True
