"""Tests fuer die beiden TLS-Routen (Entwurf Abschnitt 5).

Der entscheidende Unterschied zwischen ihnen ist der Zugang, nicht der
Inhalt: `/ca.crt` muss OHNE Token und ueber HTTP erreichbar sein, weil man
es genau dann laedt, wenn man dem HTTPS-Zugang noch nicht vertraut -
waehrend `/api/diagnostics/tls` wie jede `/api`-Route geschuetzt bleibt.
Genau deshalb liegt `/ca.crt` ausserhalb von `/api`: jeder der fuenf
`/api`-Router haengt am Waechter, und eine token-freie Ausnahme darunter
waere eine Falle fuer die naechste Person, die einen Router ergaenzt.
"""

from __future__ import annotations

import httpx2 as httpx
import pytest
from conftest import load_snapshot

from loxmatter.export.commands import extract_commands
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store
from loxmatter.tls import prepare_tls


@pytest.fixture
def tls_state(tmp_path):
    return prepare_tls(tmp_path / "tls", 8443)


@pytest.fixture
async def client_factory(tmp_path, no_invoke, fake_runtime, fake_client):
    """Baut eine App mit frei waehlbarem TLS-Zustand und Token."""
    stores: list[Store] = []

    def build(tls_state=None, api_token=None):
        store = Store(tmp_path / f"t{len(stores)}.sqlite")
        stores.append(store)
        snapshot = load_snapshot("ikea_grillplats_plug.json")
        device_id = store.register_device(snapshot)
        store.register_signals(device_id, snapshot)
        store.register_commands(device_id, extract_commands(snapshot), snapshot.node_id)
        app = build_app(
            store,
            no_invoke,
            fake_runtime(store),
            client=fake_client,
            api_token=api_token,
            tls_state=tls_state,
        )
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    yield build
    for store in stores:
        store.close()


async def test_the_ca_certificate_is_served_without_a_token(client_factory, tls_state):
    """Man laedt sie, BEVOR man HTTPS vertraut - ein Token-Zwang hier
    machte die Route nutzlos, und zwar genau im einzigen Moment, in dem sie
    gebraucht wird."""
    async with client_factory(tls_state=tls_state, api_token="geheim") as client:
        response = await client.get("/ca.crt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-x509-ca-cert")
    assert b"BEGIN CERTIFICATE" in response.content


async def test_the_ca_route_answers_503_when_https_is_off(client_factory):
    async with client_factory(tls_state=None) as client:
        response = await client.get("/ca.crt")

    assert response.status_code == 503


async def test_the_ca_route_serves_the_ca_not_the_server_certificate(client_factory, tls_state):
    """Wer das Server-Zertifikat installierte, muesste das nach jedem
    Adresswechsel wiederholen (Entwurf 4.4)."""
    async with client_factory(tls_state=tls_state) as client:
        served = (await client.get("/ca.crt")).content

    assert served == tls_state.material.ca_certificate.read_bytes()
    assert served != tls_state.material.certificate.read_bytes()


async def test_the_private_key_is_never_served(client_factory, tls_state):
    async with client_factory(tls_state=tls_state) as client:
        served = (await client.get("/ca.crt")).content

    assert b"PRIVATE KEY" not in served


async def test_the_status_route_needs_a_token(client_factory, tls_state):
    async with client_factory(tls_state=tls_state, api_token="geheim") as client:
        assert (await client.get("/api/diagnostics/tls")).status_code == 401
        response = await client.get(
            "/api/diagnostics/tls", headers={"Authorization": "Bearer geheim"}
        )

    assert response.status_code == 200


async def test_the_status_names_the_port_and_the_addresses(client_factory, tls_state):
    """`/api/diagnostics/tls` ist wie jede `/api`-Route geschuetzt (WebUI-Login,
    Spec 11) - ohne Bearer-Token bekaeme man hier nur 401 statt der Nutzlast,
    die dieser Test prueft."""
    headers = {"Authorization": "Bearer geheim"}
    async with client_factory(tls_state=tls_state, api_token="geheim") as client:
        payload = (await client.get("/api/diagnostics/tls", headers=headers)).json()

    assert payload["enabled"] is True
    assert payload["port"] == 8443
    assert payload["error"] is None
    assert payload["expires"] is not None
    for address in tls_state.material.addresses:
        assert address in payload["addresses"]


async def test_the_status_distinguishes_switched_off_from_broken(client_factory):
    from loxmatter.tls import TlsState

    headers = {"Authorization": "Bearer geheim"}
    async with client_factory(
        tls_state=TlsState(material=None, port=None, error=None), api_token="geheim"
    ) as client:
        switched_off = (await client.get("/api/diagnostics/tls", headers=headers)).json()
    broken = TlsState(material=None, port=None, error="kein Platz auf dem Datentraeger")
    async with client_factory(tls_state=broken, api_token="geheim") as client:
        failed = (await client.get("/api/diagnostics/tls", headers=headers)).json()

    assert switched_off["enabled"] is False
    assert switched_off["error"] is None
    assert failed["enabled"] is False
    assert failed["error"] == "kein Platz auf dem Datentraeger"
