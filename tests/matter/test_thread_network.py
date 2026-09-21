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
        self.create_status: int | None = None  # override the PUT's answer
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

    @property
    def thread_dataset_set(self) -> bool:
        return self.credentials_set

    async def set_thread_dataset(self, dataset: str) -> None:
        if self.unavailable:
            raise MatterUnavailableError("down")
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


def _keeper(otbr: FakeOtbr, matter: FakeMatter) -> ThreadNetworkKeeper:
    return ThreadNetworkKeeper(matter, base_url="http://otbr.test:8081", session_factory=otbr)


def test_a_node_is_a_thread_node_by_its_server_list_or_its_attributes() -> None:
    assert is_thread_node(THREAD_NODE)
    assert is_thread_node(_node("5", {"0/53/0": 24}))
    assert not is_thread_node(WIFI_NODE)
    assert not is_thread_node(_node("6", {}))


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


async def test_nothing_is_written_when_both_sides_already_know_the_network() -> None:
    otbr = FakeOtbr(dataset=DATASET, role="leader")
    matter = FakeMatter(credentials_set=True)

    assert await _keeper(otbr, matter).run_pass() is True

    assert otbr.puts == []
    assert matter.datasets_set == []


async def test_thread_devices_without_a_network_block_forming() -> None:
    """The guard, spec 4.2 step 3 / 4.3. Fault to prove it: drop the
    `is_thread_node` count from `run_pass`."""
    otbr, matter = FakeOtbr(), FakeMatter(nodes=[THREAD_NODE, WIFI_NODE])
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


@pytest.mark.parametrize(("status", "role"), [(412, "disabled"), (409, "detached")])
async def test_losing_the_race_is_not_an_error(status: int, role: str) -> None:
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
