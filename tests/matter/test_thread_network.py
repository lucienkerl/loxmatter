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

"""Forming a Thread network on a fresh installation (design 2026-09-21,
section 4) - one pass of `ThreadNetworkKeeper` per scenario.

The border router is `FakeOtbr`, a `session_factory` that answers by method
and path; matter-server is `FakeMatter`. Every test states what was written
to either, because "nothing was written" is the claim of half of them."""

from __future__ import annotations

import logging
from typing import Any, Self

import pytest

from loxmatter.matter.client import MatterUnavailableError
from loxmatter.matter.models import NodeSnapshot
from loxmatter.matter.thread_network import (
    ThreadNetworkKeeper,
    ThreadNetworkStatus,
    is_thread_node,
)

# Channel 24, name "OpenThread-07f0", no key-bearing TLV (see test_otbr.py).
DATASET = "0003000018" + "030f" + "4f70656e5468726561642d30376630"


class _Response:
    def __init__(self, status: int, body: str) -> None:
        self.status, self._body = status, body

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def text(self) -> str:
        return self._body


class FakeOtbr:
    """A border router: `dataset` None = no network, `role` its Thread role.
    A successful create stores `created_dataset`; enable sets `leader`."""

    def __init__(self, *, dataset: str | None = None, role: str = "disabled") -> None:
        self.dataset = dataset
        self.role = role
        self.created_dataset = DATASET
        self.create_status: int | None = None  # override the create PUT's answer
        self.enable_status: int | None = None  # override the enable PUT's answer
        self.unreachable = False
        self.puts: list[tuple[str, dict[str, str], str]] = []

    def __call__(self) -> FakeOtbr:
        return self

    def _path(self, url: str) -> str:
        return "/" + url.split("://", 1)[-1].split("/", 1)[-1]

    def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        if self.unreachable:
            raise OSError("connection refused")
        path = self._path(url)
        if path == "/node/dataset/active":
            return _Response(204, "") if self.dataset is None else _Response(200, self.dataset)
        if path == "/node/state":
            return _Response(200, f'"{self.role}"')
        return _Response(404, "")

    def put(self, url: str, data: str = "", headers: dict[str, str] | None = None) -> Any:
        if self.unreachable:
            raise OSError("connection refused")
        self.puts.append((self._path(url), headers or {}, data))
        path = self._path(url)
        if path == "/node/dataset/active":
            if self.create_status is not None:
                return _Response(self.create_status, "")
            if self.dataset is not None:
                return _Response(412, "")
            self.dataset = self.created_dataset
            return _Response(201, "")
        if path == "/node/state":
            if self.enable_status is not None:
                return _Response(self.enable_status, "")
            self.role = "leader"
            return _Response(200, "")
        return _Response(404, "")

    async def close(self) -> None:
        return None


class FakeMatter:
    def __init__(
        self,
        *,
        nodes: list[NodeSnapshot] | None = None,
        credentials_set: bool = False,
        unavailable: bool = False,
    ) -> None:
        self.nodes = nodes or []
        self.credentials_set = credentials_set
        self.unavailable = unavailable
        self.datasets_set: list[str] = []
        # A hand-over failure distinct from `unavailable`, which also blocks
        # `snapshots()`: this raises only from `set_thread_dataset`, so a
        # test can let the node guard pass and still fail the hand-over.
        self.hand_over_error: BaseException | None = None

    @property
    def thread_dataset_set(self) -> bool:
        return self.credentials_set

    async def set_thread_dataset(self, dataset: str) -> None:
        if self.unavailable:
            raise MatterUnavailableError("down")
        if self.hand_over_error is not None:
            raise self.hand_over_error
        self.datasets_set.append(dataset)
        self.credentials_set = True

    async def snapshots(self) -> list[NodeSnapshot]:
        if self.unavailable:
            raise MatterUnavailableError("down")
        return list(self.nodes)


def _node(address: str, attributes: dict[str, Any]) -> NodeSnapshot:
    return NodeSnapshot(
        technology="matter",
        address=address,
        vendor_name="",
        product_name="",
        unique_id=address,
        attributes=attributes,
    )


# The IKEA switch commissioned on pi3-andi: its root server list includes 53.
THREAD_NODE = _node("3", {"0/29/1": [29, 31, 40, 42, 47, 48, 49, 51, 53, 60]})
WIFI_NODE = _node("4", {"0/29/1": [29, 31, 40, 48, 49, 51, 54, 60]})
# A device without the optional Thread Network Diagnostics cluster (0x0035):
# only the mandatory Network Commissioning FeatureMap says Thread (bit 0x2).
FEATURE_MAP_THREAD_NODE = _node("7", {"0/49/65532": 0x2})
FEATURE_MAP_WIFI_NODE = _node("8", {"0/49/65532": 0x1})


def _keeper(otbr: FakeOtbr, matter: FakeMatter) -> ThreadNetworkKeeper:
    return ThreadNetworkKeeper(matter, base_url="http://otbr.test:8081", session_factory=otbr)


def test_a_node_is_a_thread_node_by_its_server_list_or_its_attributes() -> None:
    assert is_thread_node(THREAD_NODE)
    assert is_thread_node(_node("5", {"0/53/0": 24}))
    assert not is_thread_node(WIFI_NODE)
    assert not is_thread_node(_node("6", {}))


def test_a_node_is_also_a_thread_node_by_its_network_commissioning_feature_map() -> None:
    """The Thread Network Diagnostics cluster (0x0035) is optional; the
    Network Commissioning FeatureMap is mandatory (`profiles/transport.py`,
    hardware-verified). Either classifier saying "thread" is enough."""
    assert is_thread_node(FEATURE_MAP_THREAD_NODE)
    assert not is_thread_node(FEATURE_MAP_WIFI_NODE)


async def test_a_fresh_border_router_gets_a_network_that_matter_server_learns() -> None:
    otbr, matter = FakeOtbr(), FakeMatter()
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is True

    create, enable = otbr.puts
    assert create[0] == "/node/dataset/active"
    assert create[1]["If-None-Match"] == "*"
    assert create[2] == "{}"
    assert enable == ("/node/state", enable[1], '"enable"')
    assert matter.datasets_set == [DATASET]
    assert keeper.status == ThreadNetworkStatus(state="formed", name="OpenThread-07f0", channel=24)


async def test_an_existing_network_is_handed_to_matter_server_without_writing() -> None:
    otbr, matter = FakeOtbr(dataset=DATASET, role="leader"), FakeMatter()
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is True

    assert otbr.puts == []
    assert matter.datasets_set == [DATASET]
    assert keeper.status.state == "formed"


async def test_a_stale_hand_over_is_forced_on_the_first_pass_of_a_bridge_start() -> None:
    """Finding 3: matter-server can report `thread_credentials_set: true`
    for an OLD dataset it kept in its data directory across a bridge
    restart after a failed hand-over. The first pass this keeper's
    lifetime sees an active dataset in the border router hands it over
    regardless of what matter-server reports - the OTBR side needs no PUT,
    the network already exists there."""
    otbr = FakeOtbr(dataset=DATASET, role="leader")
    matter = FakeMatter(credentials_set=True)

    assert await _keeper(otbr, matter).run_pass() is True

    assert otbr.puts == []
    assert matter.datasets_set == [DATASET]


async def test_the_forced_hand_over_happens_only_once_per_bridge_start() -> None:
    otbr = FakeOtbr(dataset=DATASET, role="leader")
    matter = FakeMatter(credentials_set=True)
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is True
    assert await keeper.run_pass() is True

    assert otbr.puts == []
    assert matter.datasets_set == [DATASET]


async def test_thread_devices_without_a_network_block_forming() -> None:
    """The guard, spec 4.2 step 3 / 4.3. Fault to prove it: drop the
    `is_thread_node` count from `run_pass`."""
    otbr, matter = FakeOtbr(), FakeMatter(nodes=[THREAD_NODE, WIFI_NODE])
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is False

    assert otbr.puts == []
    assert matter.datasets_set == []
    assert keeper.status == ThreadNetworkStatus(state="missing", thread_devices=1)


async def test_a_thread_device_known_only_by_its_feature_map_blocks_forming() -> None:
    otbr, matter = FakeOtbr(), FakeMatter(nodes=[FEATURE_MAP_THREAD_NODE])
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is False

    assert otbr.puts == []
    assert matter.datasets_set == []
    assert keeper.status == ThreadNetworkStatus(state="missing", thread_devices=1)


async def test_only_wifi_devices_do_not_block_forming() -> None:
    otbr, matter = FakeOtbr(), FakeMatter(nodes=[WIFI_NODE], credentials_set=True)

    assert await _keeper(otbr, matter).run_pass() is True

    assert len(otbr.puts) == 2
    # matter-server's old credentials strand nothing and are replaced.
    assert matter.datasets_set == [DATASET]


async def test_a_blocked_keeper_turns_formed_once_the_dataset_is_restored() -> None:
    otbr, matter = FakeOtbr(), FakeMatter(nodes=[THREAD_NODE])
    keeper = _keeper(otbr, matter)
    assert await keeper.run_pass() is False

    otbr.dataset, otbr.role = DATASET, "leader"  # restored by hand

    assert await keeper.run_pass() is True
    assert keeper.status.state == "formed"
    assert otbr.puts == []


@pytest.mark.parametrize("status", [412, 409])
async def test_losing_the_race_is_not_an_error(status: int) -> None:
    otbr, matter = FakeOtbr(), FakeMatter()
    otbr.create_status = status
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is False

    assert [put[0] for put in otbr.puts] == ["/node/dataset/active"]
    assert matter.datasets_set == []
    assert keeper.status.state == "forming"


async def test_a_busy_agent_is_left_alone() -> None:
    otbr, matter = FakeOtbr(role="detached"), FakeMatter()

    assert await _keeper(otbr, matter).run_pass() is False

    assert otbr.puts == []


async def test_an_unreachable_border_router_changes_nothing() -> None:
    otbr, matter = FakeOtbr(), FakeMatter()
    otbr.unreachable = True
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is False

    assert matter.datasets_set == []
    assert keeper.status == ThreadNetworkStatus()


async def test_an_unreachable_matter_server_means_no_network_is_formed() -> None:
    """ "The bridge never forms a network without having asked matter-server
    first" (spec 4.2). Fault to prove it: treat the exception as "no nodes"."""
    otbr, matter = FakeOtbr(), FakeMatter(unavailable=True)

    assert await _keeper(otbr, matter).run_pass() is False

    assert otbr.puts == []


async def test_a_hand_over_owed_after_forming_survives_a_failed_attempt() -> None:
    """Finding 1: matter-server's stale credentials from an earlier install
    must not stop the retry once a network has actually been formed - only
    a *successful* hand-over of the new dataset may end the owed state."""
    otbr = FakeOtbr()
    matter = FakeMatter(credentials_set=True)
    matter.hand_over_error = MatterUnavailableError("down")
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is False

    assert matter.datasets_set == []
    assert otbr.dataset == DATASET  # the network was formed regardless

    matter.hand_over_error = None
    assert await keeper.run_pass() is True

    assert matter.datasets_set == [DATASET]


async def test_a_hand_over_failure_other_than_unavailable_is_not_fatal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Finding 2: `set_thread_dataset` can raise more than
    `MatterUnavailableError` (a closed connection, a failed command); none
    of it may kill `run()`."""
    otbr, matter = FakeOtbr(dataset=DATASET, role="leader"), FakeMatter()
    matter.hand_over_error = RuntimeError(f"rejected {DATASET}")
    keeper = _keeper(otbr, matter)

    with caplog.at_level(logging.WARNING):
        assert await keeper.run_pass() is False

    assert matter.datasets_set == []
    assert "Could not hand the Thread network to matter-server" in caplog.text
    assert DATASET not in caplog.text

    matter.hand_over_error = None
    assert await keeper.run_pass() is True

    assert matter.datasets_set == [DATASET]


async def test_an_enable_failure_is_left_to_the_watchdog() -> None:
    """Finding 3, spec section 3: an agent with a dataset that stays
    `disabled` is a fault for scripts/otbr-watchdog.sh to restart, not this
    loop to repair - otbr-agent re-attaches to the saved dataset on its
    own restart. Once the agent is enabled, the dataset it already created
    is handed over without forming another one."""
    otbr, matter = FakeOtbr(), FakeMatter()
    otbr.enable_status = 409
    keeper = _keeper(otbr, matter)

    assert await keeper.run_pass() is False

    assert matter.datasets_set == []

    otbr.enable_status = None
    assert await keeper.run_pass() is True

    assert [put[0] for put in otbr.puts] == ["/node/dataset/active", "/node/state"]
    assert matter.datasets_set == [DATASET]


async def test_an_unexpected_create_status_is_logged_and_not_fatal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Finding 4: a 500 (or any status besides 201/412/409) from the create
    PUT is not silently dropped, and does not stop the loop."""
    otbr, matter = FakeOtbr(), FakeMatter()
    otbr.create_status = 500
    keeper = _keeper(otbr, matter)

    with caplog.at_level(logging.WARNING):
        assert await keeper.run_pass() is False

    assert "Could not form a Thread network" in caplog.text
    assert DATASET not in caplog.text
    assert matter.datasets_set == []


async def test_run_repeats_until_a_pass_is_done() -> None:
    otbr, matter = FakeOtbr(), FakeMatter(nodes=[THREAD_NODE])
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            otbr.dataset, otbr.role = DATASET, "leader"

    keeper = ThreadNetworkKeeper(matter, session_factory=otbr, interval=60.0, sleep=sleep)
    await keeper.run()

    assert sleeps == [60.0, 60.0]
    assert keeper.status.state == "formed"


def test_the_json_shape_carries_no_dataset() -> None:
    status = ThreadNetworkStatus(state="formed", name="OpenThread-07f0", channel=24)
    assert status.as_json() == {
        "state": "formed",
        "name": "OpenThread-07f0",
        "channel": 24,
        "thread_devices": 0,
    }
