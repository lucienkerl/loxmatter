# loxmatter - bindet Matter-Geraete an einen Loxone Miniserver an.
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
    """Steht fuer matter_server.common.models.MatterNodeData.

    commission_with_code() liefert dieses Dataclass direkt zurueck - anders
    als get_nodes() (siehe FakeNode in test_client.py), das MatterNode-
    Wrapper mit node_data.attributes liefert. node_id und attributes liegen
    hier beide unmittelbar auf dem Objekt, keine Verschachtelung.
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
        # Steht fuer `MatterClient.server_info` - beim echten Client das
        # Abbild der `ServerInfoMessage`, die matter-server beim
        # Verbindungsaufbau schickt. `None`, solange kein Test etwas
        # anderes sagt, genau wie vor dem ersten `connect()`.
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
    """Steht fuer aiohttp.ClientSession — close() muss awaitbar sein, denn
    BridgeMatterClient.disconnect() ruft `await http_session.close()`
    (aiohttp.ClientSession.close() ist eine Coroutine)."""

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
    assert snapshot.node_id == 7
    assert snapshot.vendor_name == "IKEA of Sweden"
    await bridge.disconnect()


async def test_commissioning_without_connection_raises(client):
    bridge, _ = client
    with pytest.raises(Exception, match="not connected"):
        await bridge.commission_with_code("MT:ABC123")


async def test_commissioning_without_connection_raises_in_german(client):
    """Deutsches Gegenstueck zu `test_commissioning_without_connection_raises`
    oben."""
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
    """Deutsches Gegenstueck zu `test_a_failed_commissioning_says_so_clearly`
    oben."""
    i18n.set_language("de")
    bridge, upstream = client
    upstream.fail_with = RuntimeError("device not found")
    await bridge.connect()
    with pytest.raises(CommissioningError, match="Einlernen fehlgeschlagen"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_a_connection_loss_during_commissioning_says_so_in_german(client):
    """NotConnected & Co. betreffen die Verbindung zu matter-server, nicht das
    Geraet - sie muessen als MatterUnavailableError ankommen, nicht als
    CommissioningError, sonst sucht der Bedienende den Fehler faelschlich am
    Geraet statt an matter-server (siehe Spec 8.1/9)."""
    bridge, upstream = client
    upstream.fail_with = NotConnected("nicht mehr verbunden")
    await bridge.connect()
    with pytest.raises(MatterUnavailableError, match="matter-server"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_a_device_side_commissioning_failure_stays_a_commissioning_error(client):
    """Eine Ablehnung durch das Geraet selbst (z. B. falscher Code) bleibt
    ein CommissioningError - nur der Verbindungsverlust zu matter-server
    wird umgeleitet."""
    bridge, upstream = client
    upstream.fail_with = NodeCommissionFailed("Timeout during commissioning")
    await bridge.connect()
    with pytest.raises(CommissioningError, match="Commissioning failed"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_a_device_side_commissioning_failure_stays_a_commissioning_error_in_german(client):
    """Deutsches Gegenstueck zu
    `test_a_device_side_commissioning_failure_stays_a_commissioning_error` oben."""
    i18n.set_language("de")
    bridge, upstream = client
    upstream.fail_with = NodeCommissionFailed("Timeout during commissioning")
    await bridge.connect()
    with pytest.raises(CommissioningError, match="Einlernen fehlgeschlagen"):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_cancellation_during_commissioning_propagates_unwrapped(client):
    """asyncio.CancelledError ist eine BaseException, keine Exception - weder
    der Geraete- noch der Verbindungsverlust-Zweig duerfen sie abfangen."""
    bridge, upstream = client
    upstream.fail_with = asyncio.CancelledError()
    await bridge.connect()
    with pytest.raises(asyncio.CancelledError):
        await bridge.commission_with_code("MT:ABC123")
    await bridge.disconnect()


async def test_remove_node_reaches_upstream(client):
    bridge, upstream = client
    await bridge.connect()
    await bridge.remove_node(7)
    assert upstream.removed == [7]
    await bridge.disconnect()


async def test_thread_dataset_reaches_upstream(client):
    """Ohne Datensatz kann matter-server einem Thread-Geraet kein Netz nennen."""
    bridge, upstream = client
    await bridge.connect()
    await bridge.set_thread_dataset("0e08...")
    assert upstream.datasets == ["0e08..."]
    await bridge.disconnect()


# ---------------------------------------------------------------------------
# Ob matter-server die Thread-Zugangsdaten ueberhaupt hat
#
# Der Dienst haelt sie NUR im Arbeitsspeicher (`_thread_credentials_set: bool
# = False` im Konstruktor von `matter_server/server/device_controller.py`) und
# nennt ihren Zustand beim Verbindungsaufbau in
# `ServerInfoMessage.thread_credentials_set`. Sein Client aktualisiert dieses
# Abbild NIE wieder: der Server sendet zwar `SERVER_INFO_UPDATED`, aber
# `MatterClient._handle_event_message` kennt dafuer keinen Zweig (geprueft
# gegen die installierte Fassung, nicht vermutet). Das Abbild allein bliebe
# deshalb bis zum Verbindungsende `False` - auch unmittelbar nachdem diese
# Bruecke den Datensatz selbst gesetzt hat. `thread_dataset_set` fuehrt darum
# zusaetzlich Buch ueber die eigenen Aufrufe.
# ---------------------------------------------------------------------------


class FakeServerInfo:
    """Steht fuer `matter_server.common.models.ServerInfoMessage` - nur das
    eine Feld, das hier zaehlt."""

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
    """Ohne eigenes Buchfuehren wuerde die Bruecke den Datensatz vor JEDEM
    Einlernen erneut holen und setzen, obwohl sie ihn selbst gerade gesetzt
    hat - `server_info` bleibt `False` (siehe oben)."""
    bridge, upstream = client
    upstream.server_info = FakeServerInfo(thread_credentials_set=False)
    await bridge.connect()

    await bridge.set_thread_dataset("0e08")

    assert upstream.datasets == ["0e08"]
    assert bridge.thread_dataset_set is True

    await bridge.disconnect()


async def test_a_new_connection_forgets_what_the_previous_one_had_set(client):
    """Der entscheidende Fall: genau das Vergessen, das matter-server bei
    einem Neustart selbst vollzieht. Bliebe die Merkung ueber die Verbindung
    hinaus bestehen, hielte die Bruecke einen Datensatz fuer gesetzt, den es
    auf der anderen Seite nicht mehr gibt - und das Einlernen scheiterte
    wieder mit "Required network information not provided"."""
    bridge, upstream = client
    upstream.server_info = FakeServerInfo(thread_credentials_set=False)
    await bridge.connect()
    await bridge.set_thread_dataset("0e08")
    await bridge.disconnect()

    await bridge.connect()

    assert bridge.thread_dataset_set is False

    await bridge.disconnect()


async def test_a_client_without_a_connection_reports_no_thread_credentials(client):
    """Ohne Verbindung gibt es keine Zusicherung - und die Diagnose soll das
    sagen duerfen, ohne eine Ausnahme fangen zu muessen."""
    bridge, _ = client

    assert bridge.thread_dataset_set is False
