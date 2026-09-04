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

"""Der Thread-Datensatz aus dem Border Router (`matter/otbr.py`).

Der aufgezeichnete Ernstfall: matter-server haelt die Thread-Zugangsdaten
NUR im Arbeitsspeicher (`_thread_credentials_set: bool = False` in
`matter_server/server/device_controller.py`, gesetzt allein durch
`set_thread_operational_dataset`). Nach jedem Neustart des Dienstes sind sie
weg, und jedes Thread-Geraet scheitert beim Einlernen mit "Required network
information not provided in commissioning parameters" - sichtbar in der
Oberflaeche nur als "Commission with code failed for node N".

Dieser Test deckt die Quelle ab, aus der die Bruecke sich den Datensatz
seither selbst holt, statt auf ein Einfuegen von Hand zu warten.
"""

from __future__ import annotations

from typing import Any, Self

import pytest

from loxmatter.matter.otbr import (
    DEFAULT_OTBR_URL,
    ThreadDatasetUnavailableError,
    fetch_active_dataset,
)

# Ein aufgezeichneter, aber unbrauchbarer Datensatz: dieselbe Gestalt wie ein
# echter (Hex-TLV), aber kein Netzwerkschluessel, der irgendwo existiert. Ein
# echter Datensatz ist ein Credential und gehoert weder ins Repository noch in
# ein Log (siehe deploy/testhost/README.md).
FAKE_DATASET = "0e080000000000010000" + "00" * 30


class FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def text(self) -> str:
        return self._body


class FakeSession:
    """Steht fuer `aiohttp.ClientSession` - nur `get()` und `close()`, mehr
    braucht `fetch_active_dataset` nicht (dasselbe Muster wie `FakeSession`
    in `test_client_commissioning.py`)."""

    def __init__(self, status: int = 200, body: str = FAKE_DATASET) -> None:
        self.status = status
        self.body = body
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.closed = False
        self.raise_on_get: Exception | None = None

    def get(self, url: str, headers: dict[str, str] | None = None) -> Any:
        self.requests.append((url, headers or {}))
        if self.raise_on_get is not None:
            raise self.raise_on_get
        return FakeResponse(self.status, self.body)

    async def close(self) -> None:
        self.closed = True


async def test_reads_the_active_dataset_from_the_border_router() -> None:
    session = FakeSession()

    dataset = await fetch_active_dataset(
        "http://otbr.example:8081", session_factory=lambda: session
    )

    assert dataset == FAKE_DATASET
    url, headers = session.requests[0]
    assert url == "http://otbr.example:8081/node/dataset/active"
    # Ohne diesen Header liefert OTBRs REST-Schnittstelle den Datensatz als
    # JSON-Struktur statt als Hex-TLV - und nur Letzteres nimmt
    # `set_thread_operational_dataset` entgegen.
    assert headers["Accept"] == "text/plain"


async def test_closes_the_session_even_when_the_request_fails() -> None:
    session = FakeSession()
    session.raise_on_get = OSError("Netz weg")

    with pytest.raises(ThreadDatasetUnavailableError):
        await fetch_active_dataset(session_factory=lambda: session)

    assert session.closed


async def test_a_border_router_without_a_thread_network_is_reported_as_such() -> None:
    """OTBR antwortet mit 409, solange kein aktiver Datensatz existiert -
    der Border Router laeuft dann zwar, hat aber kein Netz gebildet."""
    session = FakeSession(status=409, body="")

    with pytest.raises(ThreadDatasetUnavailableError) as excinfo:
        await fetch_active_dataset(session_factory=lambda: session)

    assert "409" in str(excinfo.value)


async def test_a_response_that_is_not_hex_is_refused() -> None:
    """Sonst landete eine HTML-Fehlerseite als "Datensatz" bei
    matter-server, das sie mangels `bytes.fromhex` erst viel spaeter und
    ohne Bezug zur Ursache abweist."""
    session = FakeSession(body="<html>Not Found</html>")

    with pytest.raises(ThreadDatasetUnavailableError):
        await fetch_active_dataset(session_factory=lambda: session)


async def test_an_empty_response_is_refused() -> None:
    session = FakeSession(body="   \n")

    with pytest.raises(ThreadDatasetUnavailableError):
        await fetch_active_dataset(session_factory=lambda: session)


async def test_falls_back_to_the_border_router_on_this_host() -> None:
    """Der Regelfall des Stacks aus `deploy/testhost/docker-compose.yml`:
    OTBR und diese Bruecke teilen sich mit `network_mode: host` denselben
    Netzwerk-Namensraum, OTBRs REST-Schnittstelle lauscht dort auf
    127.0.0.1:8081."""
    session = FakeSession()

    await fetch_active_dataset(session_factory=lambda: session)

    assert session.requests[0][0].startswith(DEFAULT_OTBR_URL)


async def test_an_explicit_address_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fuer Aufbauten mit einem Border Router auf einem anderen Host."""
    monkeypatch.setenv("LOXMATTER_OTBR_URL", "http://10.0.1.99:8081")
    session = FakeSession()

    await fetch_active_dataset(session_factory=lambda: session)

    assert session.requests[0][0] == "http://10.0.1.99:8081/node/dataset/active"
