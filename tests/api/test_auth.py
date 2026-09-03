"""Tests fuer die vier Zugangs-Routen (Spec 8).

Sie haengen als einzige unter `/auth` ausserhalb des Waechters - sie muessen
unangemeldet erreichbar sein, sonst koennte sich niemand anmelden.

`httpx.AsyncClient` fuehrt einen eigenen Cookie-Speicher: was `POST
/auth/login` setzt, schickt jede weitere Anfrage desselben Clients von
selbst mit. Genau so verhaelt sich auch der Browser.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from conftest import load_snapshot

from loxmatter.auth.passwords import hash_password
from loxmatter.auth.throttle import FAILURES_BEFORE_THROTTLING
from loxmatter.loxone.runtime import Runtime
from loxmatter.loxone.server import build_app
from loxmatter.model.store import Store

PASSWORT = "ein-gutes-passwort"


class _NullSender:
    def send(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture
async def auth_client(
    tmp_path: Path, no_invoke: Any
) -> AsyncIterator[tuple[httpx.AsyncClient, Store]]:
    """Eine App ohne gesetztes Passwort - der Zustand der Ersteinrichtung."""
    store = Store(tmp_path / "t.sqlite")
    snapshot = load_snapshot("ikea_grillplats_plug.json")
    device_id = store.register_device(snapshot)
    store.register_signals(device_id, snapshot)
    runtime = Runtime(store, _NullSender())
    app = build_app(store, no_invoke, runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, store
    store.close()


async def test_auth_info_reports_an_unconfigured_service(auth_client):
    client, _ = auth_client
    response = await client.get("/auth-info")
    assert response.status_code == 200
    assert response.json() == {"password_set": False, "authenticated": False}


async def test_setup_sets_the_password_and_logs_in(auth_client):
    client, store = auth_client
    response = await client.post("/auth/setup", json={"password": PASSWORT})
    assert response.status_code == 200
    assert store.auth.password_hash() is not None
    assert (await client.get("/auth-info")).json() == {
        "password_set": True,
        "authenticated": True,
    }


async def test_setup_is_closed_for_good_once_a_password_is_set(auth_client):
    client, _ = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})
    second = await client.post("/auth/setup", json={"password": "ein-anderes-passwort"})
    assert second.status_code == 409


async def test_setup_rejects_a_short_password(auth_client):
    client, store = auth_client
    response = await client.post("/auth/setup", json={"password": "kurz"})
    assert response.status_code == 422
    assert store.auth.password_hash() is None


async def test_login_with_the_right_password_authenticates(auth_client):
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 200
    assert (await client.get("/auth-info")).json()["authenticated"] is True


async def test_login_with_a_wrong_password_is_rejected(auth_client):
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    response = await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    assert response.status_code == 401
    assert (await client.get("/auth-info")).json()["authenticated"] is False


async def test_login_before_setup_says_so(auth_client):
    """409 und nicht 401: es gibt kein Passwort, mit dem dieser Aufruf
    gelingen koennte - eine Wiederholung mit Zugangsdaten hilft nicht."""
    client, _ = auth_client
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 409


async def test_repeated_wrong_passwords_are_throttled(auth_client):
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    for _ in range(FAILURES_BEFORE_THROTTLING):
        await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 429


async def test_logout_ends_the_session_on_the_server(auth_client):
    """Nicht nur das Cookie loeschen: derselbe Wert darf danach nicht mehr
    gelten, sonst lebt eine gestohlene Kennung weiter."""
    client, _ = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})
    session_id = client.cookies.get("loxmatter_session")
    assert session_id is not None

    await client.post("/auth/logout")
    assert (await client.get("/auth-info")).json()["authenticated"] is False

    client.cookies.set("loxmatter_session", session_id)
    assert (await client.get("/auth-info")).json()["authenticated"] is False


async def test_no_response_ever_contains_the_password_or_its_hash(auth_client):
    client, store = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})
    stored = store.auth.password_hash()
    assert stored is not None
    for response in [
        await client.get("/auth-info"),
        await client.post("/auth/login", json={"password": PASSWORT}),
    ]:
        assert PASSWORT not in response.text
        assert stored not in response.text
