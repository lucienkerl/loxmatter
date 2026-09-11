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

import asyncio

import pytest
from matter_server.client.exceptions import NotConnected
from matter_server.common.errors import NodeCommissionFailed

from loxmatter import i18n
from loxmatter.matter.client import BridgeMatterClient, CommissioningError, MatterUnavailableError


class FakeNodeData:
    """Stands in for matter_server.common.models.MatterNodeData.

    commission_with_code() returns this dataclass directly - unlike
    get_nodes() (see FakeNode in test_client.py), which returns MatterNode
    wrappers with node_data.attributes. node_id and attributes both sit
    directly on the object here, with no nesting.
    """

    def __init__(self, node_id: int, attributes: dict[str, object]):
        self.node_id = node_id
        self.attributes = attributes
        self.available = True


class FakeUpstream:
    def __init__(self) -> None:
        self.nodes: list[FakeNodeData] = []
        self.removed: list[int] = []
        self.datasets: list[str] = []
        self.fail_with: Exception | None = None
        # Stands in for `MatterClient.server_info` - in the real client the
        # image of the `ServerInfoMessage` that matter-server sends when the
        # connection is established. `None` as long as no test says
        # otherwise, exactly as before the first `connect()`.
        self.server_info: object | None = None

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    async def start_listening(self, ready=None) -> None:
        if ready is not None:
            ready.set()

    def get_nodes(self) -> list[FakeNodeData]:
        return self.nodes

    async def commission_with_code(self, code: str, network_only: bool = False) -> FakeNodeData:
        if self.fail_with is not None:
            raise self.fail_with
        node = FakeNodeData(7, {"0/40/1": "IKEA of Sweden", "1/6/0": True})
        self.nodes.append(node)
        return node

    async def remove_node(self, node_id: int) -> None:
        self.removed.append(node_id)

    async def set_thread_operational_dataset(self, dataset: str) -> None:
        self.datasets.append(dataset)


class FakeSession:
    """Stands in for aiohttp.ClientSession — close() must be awaitable, since
    BridgeMatterClient.disconnect() calls `await http_session.close()`
    (aiohttp.ClientSession.close() is a coroutine)."""

    async def close(self) -> None: ...


@pytest.fixture
def client() -> tuple[BridgeMatterClient, FakeUpstream]:
    upstream = FakeUpstream()
    return (
        BridgeMatterClient(
            "ws://test/ws",
            session_factory=lambda _session: upstream,
            http_session_factory=FakeSession,
        ),
        upstream,
    )


async def test_commissioning_returns_a_snapshot(client):
    bridge, _ = client
    await bridge.connect()
    snapshot = await bridge.commission_with_code("MT:ABC123")
    assert snapshot.address == "7"
    assert snapshot.vendor_name == "IKEA of Sweden"
    await bridge.disconnect()


async def test_commissioning_without_connection_raises(client):
    bridge, _ = client
    with pytest.raises(Exception, match="not connected"):
        await bridge.commission_with_code("MT:ABC123")


async def test_commissioning_without_connection_raises_in_german(client):
    """German counterpart to `test_commissioning_without_connection_raises`
    above."""
    i18n.set_language("de")
    bridge, _ = client
    with pytest.raises(Exception, match="nicht verbunden"):
        await bridge.commission_with_code("MT:ABC123")


async def test_a_failed_commissioning_says_so_clearly(client):
    bridge, upstream = client
    upstream.fail_with = RuntimeError("device not found")
    await bridge.connect()
    with pytest.raises(CommissioningError, match="Commissioning failed"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_a_failed_commissioning_says_so_in_german(client):
    """German counterpart to `test_a_failed_commissioning_says_so_clearly`
    above."""
    i18n.set_language("de")
    bridge, upstream = client
    upstream.fail_with = RuntimeError("device not found")
    await bridge.connect()
    with pytest.raises(CommissioningError, match="Einlernen fehlgeschlagen"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_a_connection_loss_during_commissioning_says_so_in_german(client):
    """NotConnected & co. concern the connection to matter-server, not the
    device - they must arrive as MatterUnavailableError, not as
    CommissioningError, or the operator would mistakenly look for the fault
    on the device instead of at matter-server (see spec 8.1/9)."""
    bridge, upstream = client
    upstream.fail_with = NotConnected("nicht mehr verbunden")
    await bridge.connect()
    with pytest.raises(MatterUnavailableError, match="matter-server"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_a_device_side_commissioning_failure_stays_a_commissioning_error(client):
    """A rejection by the device itself (e.g. a wrong code) stays a
    CommissioningError - only the connection loss to matter-server gets
    redirected."""
    bridge, upstream = client
    upstream.fail_with = NodeCommissionFailed("Timeout during commissioning")
    await bridge.connect()
    with pytest.raises(CommissioningError, match="Commissioning failed"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_a_device_side_commissioning_failure_stays_a_commissioning_error_in_german(client):
    """German counterpart to
    `test_a_device_side_commissioning_failure_stays_a_commissioning_error` above."""
    i18n.set_language("de")
    bridge, upstream = client
    upstream.fail_with = NodeCommissionFailed("Timeout during commissioning")
    await bridge.connect()
    with pytest.raises(CommissioningError, match="Einlernen fehlgeschlagen"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_cancellation_during_commissioning_propagates_unwrapped(client):
    """asyncio.CancelledError is a BaseException, not an Exception - neither
    the device branch nor the connection-loss branch may catch it."""
    bridge, upstream = client
    upstream.fail_with = asyncio.CancelledError()
    await bridge.connect()
    with pytest.raises(asyncio.CancelledError):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_remove_reaches_upstream(client):
    bridge, upstream = client
    await bridge.connect()
    await bridge.remove("7")
    assert upstream.removed == [7]
    await bridge.disconnect()


async def test_thread_dataset_reaches_upstream(client):
    """Without a dataset, matter-server cannot tell a Thread device about any network."""
    bridge, upstream = client
    await bridge.connect()
    await bridge.set_thread_dataset("0e08...")
    assert upstream.datasets == ["0e08..."]
    await bridge.disconnect()


# ---------------------------------------------------------------------------
# Whether matter-server has the Thread credentials at all
#
# The service keeps them ONLY in memory (`_thread_credentials_set: bool =
# False` in the constructor of `matter_server/server/device_controller.py`)
# and states their status when the connection is established, in
# `ServerInfoMessage.thread_credentials_set`. Its client NEVER updates this
# image again: the server does send `SERVER_INFO_UPDATED`, but
# `MatterClient._handle_event_message` has no branch for it (checked against
# the installed version, not assumed). The image alone would therefore stay
# `False` until the connection ends - even right after this bridge has set
# the dataset itself. `thread_dataset_set` therefore additionally keeps its
# own record of the calls it made.
# ---------------------------------------------------------------------------


class FakeServerInfo:
    """Stands in for `matter_server.common.models.ServerInfoMessage` - only
    the one field that matters here."""

    def __init__(self, thread_credentials_set: bool) -> None:
        self.thread_credentials_set = thread_credentials_set


async def test_reports_the_thread_state_matter_server_announced_on_connect(client):
    bridge, upstream = client
    upstream.server_info = FakeServerInfo(thread_credentials_set=True)
    await bridge.connect()

    assert bridge.thread_dataset_set is True

    await bridge.disconnect()


async def test_a_server_without_thread_credentials_is_reported_as_such(client):
    bridge, upstream = client
    upstream.server_info = FakeServerInfo(thread_credentials_set=False)
    await bridge.connect()

    assert bridge.thread_dataset_set is False

    await bridge.disconnect()


async def test_setting_the_thread_dataset_is_remembered_for_this_connection(client):
    """Without its own record-keeping, the bridge would fetch and set the
    dataset again before EVERY commissioning, even though it just set it
    itself - `server_info` stays `False` (see above)."""
    bridge, upstream = client
    upstream.server_info = FakeServerInfo(thread_credentials_set=False)
    await bridge.connect()

    await bridge.set_thread_dataset("0e08")

    assert upstream.datasets == ["0e08"]
    assert bridge.thread_dataset_set is True

    await bridge.disconnect()


async def test_a_new_connection_forgets_what_the_previous_one_had_set(client):
    """The decisive case: exactly the forgetting that matter-server itself
    performs on a restart. If the memory persisted beyond the connection,
    the bridge would consider a dataset set that no longer exists on the
    other side - and commissioning would fail again with "Required network
    information not provided"."""
    bridge, upstream = client
    upstream.server_info = FakeServerInfo(thread_credentials_set=False)
    await bridge.connect()
    await bridge.set_thread_dataset("0e08")
    await bridge.disconnect()

    await bridge.connect()

    assert bridge.thread_dataset_set is False

    await bridge.disconnect()


async def test_a_client_without_a_connection_reports_no_thread_credentials(client):
    """Without a connection there is no guarantee - and the diagnostic
    should be able to say so without having to catch an exception."""
    bridge, _ = client

    assert bridge.thread_dataset_set is False
