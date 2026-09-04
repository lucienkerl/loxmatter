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

"""Tests fuer die vier Zugangs-Routen (Spec 8).

Sie haengen als einzige unter `/auth` ausserhalb des Waechters - sie muessen
unangemeldet erreichbar sein, sonst koennte sich niemand anmelden.

`httpx.AsyncClient` fuehrt einen eigenen Cookie-Speicher: was `POST
/auth/login` setzt, schickt jede weitere Anfrage desselben Clients von
selbst mit. Genau so verhaelt sich auch der Browser.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx2 as httpx
import pytest
from conftest import load_snapshot

from loxmatter.auth.passwords import MIN_PASSWORD_LENGTH, hash_password
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
    assert second.json()["detail"] == (
        "A password has already been set for this service – initial setup is "
        "therefore permanently complete. Forgot the password? In the reference "
        "deployment, `docker compose exec loxmatter loxmatter set-password` resets "
        "it; for a source install, `uv run loxmatter set-password`."
    )


async def test_setup_is_closed_for_good_once_a_password_is_set_in_german(auth_client):
    """Deutscher Begleittest zu test_setup_is_closed_for_good_once_a_password_is_set."""
    client, store = auth_client
    store.locale.set_language("de")
    await client.post("/auth/setup", json={"password": PASSWORT})
    second = await client.post("/auth/setup", json={"password": "ein-anderes-passwort"})
    assert second.status_code == 409
    assert second.json()["detail"] == (
        "Für diesen Dienst ist bereits ein Passwort vergeben – die Ersteinrichtung "
        "ist damit dauerhaft abgeschlossen. Passwort vergessen? Im Referenz-"
        "Deployment setzt `docker compose exec loxmatter loxmatter set-password` "
        "es neu; bei einer Installation aus dem Quellcode `uv run loxmatter "
        "set-password`."
    )


async def test_setup_does_not_hash_once_a_password_is_already_set(auth_client, monkeypatch):
    """Regressionsfund: `hash_password(body.password)` stand als Argument da
    und wurde deshalb IMMER ausgewertet, auch auf einer laengst eingerichteten
    Bruecke - 16 MiB scrypt, synchron im Event-Loop, bei jedem Aufruf dieser
    ungeschuetzten Route. Die billige Pruefung `password_hash() is not None`
    muss VOR dem Hashen greifen, nicht nur VOR dem Schreiben."""
    client, _ = auth_client
    await client.post("/auth/setup", json={"password": PASSWORT})

    spy = Mock(name="hash_password")
    monkeypatch.setattr("loxmatter.api.auth.hash_password", spy)

    second = await client.post("/auth/setup", json={"password": "ein-anderes-passwort"})

    assert second.status_code == 409
    spy.assert_not_called()


async def test_setup_rejects_a_short_password(auth_client):
    client, store = auth_client
    response = await client.post("/auth/setup", json={"password": "kurz"})
    assert response.status_code == 422
    assert store.auth.password_hash() is None
    assert response.json()["detail"] == (
        f"The password must be at least {MIN_PASSWORD_LENGTH} characters long."
    )


async def test_setup_rejects_a_short_password_in_german(auth_client):
    """Deutscher Begleittest zu test_setup_rejects_a_short_password."""
    client, store = auth_client
    store.locale.set_language("de")
    response = await client.post("/auth/setup", json={"password": "kurz"})
    assert response.status_code == 422
    assert store.auth.password_hash() is None
    assert response.json()["detail"] == (
        f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben."
    )


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
    assert response.json()["detail"] == "Wrong password."
    assert (await client.get("/auth-info")).json()["authenticated"] is False


async def test_login_with_a_wrong_password_is_rejected_in_german(auth_client):
    """Deutscher Begleittest zu test_login_with_a_wrong_password_is_rejected."""
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    store.locale.set_language("de")
    response = await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Falsches Passwort."
    assert (await client.get("/auth-info")).json()["authenticated"] is False


async def test_login_before_setup_says_so(auth_client):
    """409 und nicht 401: es gibt kein Passwort, mit dem dieser Aufruf
    gelingen koennte - eine Wiederholung mit Zugangsdaten hilft nicht."""
    client, _ = auth_client
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "No password has been set for this service yet – please complete initial setup first."
    )


async def test_login_before_setup_says_so_in_german(auth_client):
    """Deutscher Begleittest zu test_login_before_setup_says_so."""
    client, store = auth_client
    store.locale.set_language("de")
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Für diesen Dienst ist noch kein Passwort vergeben – bitte zuerst die "
        "Ersteinrichtung abschließen."
    )


async def test_repeated_wrong_passwords_are_throttled(auth_client):
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    for _ in range(FAILURES_BEFORE_THROTTLING):
        await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail.startswith("Too many failed attempts")
    assert detail.endswith("seconds.")


async def test_repeated_wrong_passwords_are_throttled_in_german(auth_client):
    """Deutscher Begleittest zu test_repeated_wrong_passwords_are_throttled."""
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    store.locale.set_language("de")
    for _ in range(FAILURES_BEFORE_THROTTLING):
        await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    response = await client.post("/auth/login", json={"password": PASSWORT})
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail.startswith("Zu viele Fehlversuche")
    assert detail.endswith("wieder möglich.")


async def test_setup_is_also_throttled_after_repeated_login_failures(auth_client):
    """Setup und Login teilen sich dieselbe LoginThrottle (siehe `_client_id`-
    Docstring) - deckt damit auch den zweiten Aufrufort der 429-Meldung ab,
    nicht nur den in `/auth/login`."""
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    for _ in range(FAILURES_BEFORE_THROTTLING):
        await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    response = await client.post("/auth/setup", json={"password": PASSWORT})
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail.startswith("Too many failed attempts")
    assert detail.endswith("seconds.")


async def test_setup_is_also_throttled_after_repeated_login_failures_in_german(auth_client):
    """Deutscher Begleittest zu
    test_setup_is_also_throttled_after_repeated_login_failures."""
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))
    store.locale.set_language("de")
    for _ in range(FAILURES_BEFORE_THROTTLING):
        await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    response = await client.post("/auth/setup", json={"password": PASSWORT})
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail.startswith("Zu viele Fehlversuche")
    assert detail.endswith("wieder möglich.")


async def test_concurrent_wrong_passwords_are_still_throttled(auth_client):
    """Regressionsfund (Review, 2026-09-03): der `await` in
    `anyio.to_thread.run_sync` (vormals `run_in_threadpool`) unterbricht den
    Rumpf von `/auth/login` an einer Stelle, die der fruehere synchrone
    Code nicht hatte. Bucht die Route den Fehlversuch erst NACH diesem
    `await`, laufen gleichzeitige Anfragen alle an `throttle.retry_after`
    vorbei, BEVOR auch nur eine von ihnen den Zaehler erhoeht - die
    Drosselung liesse sich durch reine Parallelitaet vollstaendig umgehen
    (nachgemessen vor der Behebung: 60 gleichzeitige Anfragen einer
    Adresse ergaben 60 statt hoechstens `FAILURES_BEFORE_THROTTLING`
    echten Rateversuchen). Deutlich mehr Anfragen als
    `FAILURES_BEFORE_THROTTLING`, damit ein zufaelliges Durchrutschen
    einzelner Anfragen den Test nicht verdeckt. `asyncio.gather` reicht
    hier ohne echte Threads: `client.post(...)` erzeugt bei jedem Aufruf
    unten sofort eine Coroutine, `gather` startet alle nahezu gleichzeitig
    als eigene Tasks - und der Rumpf von `/auth/login` laeuft bis zum
    ersten `await` (das `retry_after`-Pruefen eingeschlossen) synchron,
    ohne dass der Event-Loop dazwischen an eine andere Anfrage abgeben
    kann."""
    client, store = auth_client
    store.auth.set_password_hash(hash_password(PASSWORT))

    attempts = FAILURES_BEFORE_THROTTLING * 4
    responses = await asyncio.gather(
        *(
            client.post("/auth/login", json={"password": "falsch-aber-lang"})
            for _ in range(attempts)
        )
    )
    statuses = [response.status_code for response in responses]

    # Genau `FAILURES_BEFORE_THROTTLING` echte Pruefungen des Passworts -
    # alles danach muss die Drosselung mit 429 abfangen, egal wie viele
    # Anfragen gleichzeitig ankamen. `<=` waere hier zahnlos: die Aussage
    # bliebe auch wahr, wenn die Route ausnahmslos 429 antwortete (0 ist
    # ebenfalls <= `FAILURES_BEFORE_THROTTLING`). Der Wert ist deterministisch,
    # weil der Rumpf von `/auth/login` bis zur Buchung synchron gegenueber dem
    # Event-Loop laeuft - kein `await` liegt dazwischen, siehe Kommentar oben.
    assert statuses.count(401) == FAILURES_BEFORE_THROTTLING
    assert statuses.count(429) == attempts - statuses.count(401)


async def test_concurrent_setup_attempts_are_still_throttled(auth_client, monkeypatch):
    """Regressionsfund (Fund 1, 2026-09-03): dieselbe Luecke wie im Test
    oben, nur in `/auth/setup` statt `/auth/login` - dort bucht vor dieser
    Behebung nichts VOR dem `await anyio.to_thread.run_sync(hash_password, ...)`,
    also kommen beliebig viele gleichzeitige Einrichtungsversuche an
    `throttle.retry_after` vorbei, bevor auch nur einer den Zaehler erhoeht
    (nachgemessen vor der Behebung: 20 von 20 gleichzeitigen Versuchen gegen
    eine noch nicht eingerichtete Bruecke drangen bis zum 16-MiB-Hashen vor).

    Statuscodes allein verraten das hier NICHT, anders als beim Login: der
    Verlierer eines Wettlaufs um `set_password_hash_if_unset` bekommt
    ebenfalls 409, obwohl er zuvor gehasht hat - ein 409 heisst also nicht
    "wurde gedrosselt". Deshalb zaehlt dieser Test direkt die Aufrufe von
    `hash_password` per Monkeypatch mit, statt sich auf die Statuscodes zu
    verlassen."""
    client, _ = auth_client

    # Eine Liste und kein Zaehler: `hash_password` laeuft seit dem
    # `CapacityLimiter` in bis zu vier Worker-Threads gleichzeitig, und
    # `int += 1` ist in CPython nicht atomar - ein verlorener Zaehlschritt
    # ergaebe ein sporadisch rotes `4 == 5` in der CI. `list.append` ist
    # atomar und hat dieses Fenster nicht.
    calls: list[None] = []
    original_hash_password = hash_password

    def counting_hash_password(password: str) -> str:
        calls.append(None)
        return original_hash_password(password)

    monkeypatch.setattr("loxmatter.api.auth.hash_password", counting_hash_password)

    attempts = FAILURES_BEFORE_THROTTLING * 4
    responses = await asyncio.gather(
        *(client.post("/auth/setup", json={"password": f"{PASSWORT}-{i}"}) for i in range(attempts))
    )

    # Genau `FAILURES_BEFORE_THROTTLING` Versuche drangen bis zum Hashen vor -
    # derselbe deterministische Grund wie beim Login: der Rumpf von
    # `/auth/setup` laeuft bis zur Buchung synchron.
    assert len(calls) == FAILURES_BEFORE_THROTTLING
    assert [response.status_code for response in responses].count(200) == 1


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
    """Deckt neben den erfolgreichen 200er-Antworten auch die vier
    Fehlerzweige ab (401, 409, 422, 429, Fund D) - genau dort wuerde ein
    spaeter versehentlich eingebauter Wert ("Falsches Passwort: <x>") am
    ehesten landen, weil ein Fehlertext haeufiger von Hand nachgebessert
    wird als ein schlichtes `{"status": "ok"}`."""
    client, store = auth_client
    responses = [await client.post("/auth/setup", json={"password": "kurz"})]  # 422
    assert responses[-1].status_code == 422

    setup_ok = await client.post("/auth/setup", json={"password": PASSWORT})
    assert setup_ok.status_code == 200
    responses.append(setup_ok)

    stored = store.auth.password_hash()
    assert stored is not None
    responses.append(await client.get("/auth-info"))

    # 409 auf einer laengst eingerichteten Bruecke - deckt den Fehlerzweig
    # zwar ab, zaehlt aber SEIT DEM FUND ZU 3 UNTEN nicht mehr als
    # Fehlversuch fuer die Drosselung (es gibt dort nichts mehr zu
    # schuetzen, siehe Kommentar am 409-Zweig in `api/auth.py`).
    already_set_up = await client.post("/auth/setup", json={"password": "ein-anderes-passwort"})
    assert already_set_up.status_code == 409
    responses.append(already_set_up)

    wrong = await client.post("/auth/login", json={"password": "falsch-aber-lang"})
    assert wrong.status_code == 401  # 1. (und einziger bisheriger) Fehlversuch
    responses.append(wrong)

    # /auth/setup und /auth/login teilen sich dieselbe LoginThrottle - ein
    # Fehlversuch steht aus der Anfrage oben bereits zu Buche, hier folgen
    # die restlichen bis zur Drosselung.
    for _ in range(FAILURES_BEFORE_THROTTLING - 1):
        responses.append(await client.post("/auth/login", json={"password": "falsch-aber-lang"}))

    throttled = await client.post("/auth/login", json={"password": PASSWORT})
    assert throttled.status_code == 429
    responses.append(throttled)

    for response in responses:
        assert PASSWORT not in response.text
        assert stored not in response.text
