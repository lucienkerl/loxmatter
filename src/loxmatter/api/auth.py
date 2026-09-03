"""Die vier Zugangs-Routen: `/auth-info`, `/auth/setup`, `/auth/login`,
`/auth/logout` (Spec 8).

**Diese Routen haengen als einzige NICHT hinter `build_api_guard`** - sie
muessen unangemeldet erreichbar sein, sonst koennte sich niemand anmelden.
Sie werden in `loxone.server.build_app` deshalb bewusst ohne
`dependencies=api_guard` eingebunden, neben `/health`.

Was sie deshalb NICHT ausliefern: irgendetwas ueber den Zustand der Bruecke.
`/auth-info` sagt genau zwei Wahrheitswerte - ob ein Passwort gesetzt ist und
ob DIESER Aufrufer angemeldet ist. Beides erfaehrt ein Aufrufer ohnehin
daran, wie `/api/devices` ihm antwortet; hier steht es nur so, dass die
Oberflaeche nicht raten muss, welchen Bildschirm sie zeigt.

**Kein Geheimnis verlaesst dieses Modul.** Weder Passwort noch Hash noch
Sitzungskennung erscheinen in einer Antwort (die Kennung reist
ausschliesslich im `Set-Cookie`) oder in einem Log - in keinem Zweig.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from loxmatter.auth.passwords import MIN_PASSWORD_LENGTH, hash_password, verify_password
from loxmatter.auth.sessions import (
    SESSION_COOKIE,
    SESSION_LIFETIME_SECONDS,
    open_session,
    session_is_valid,
)
from loxmatter.auth.throttle import LoginThrottle
from loxmatter.model.store import Store


class PasswordIn(BaseModel):
    password: str


class AuthInfoOut(BaseModel):
    password_set: bool
    authenticated: bool


class StatusOut(BaseModel):
    status: str


def build_auth_router(store: Store) -> APIRouter:
    router = APIRouter()
    # Eine Instanz je App, nicht je Anfrage - sonst zaehlte sie nichts.
    throttle = LoginThrottle()

    def _require_length(password: str) -> None:
        """Eigene Pruefung statt `Field(min_length=...)` am Modell: die
        Meldung landet in der Oberflaeche und soll dort auf Deutsch stehen
        und sagen, was zu tun ist - nicht als pydantic-Fehlerliste."""
        if len(password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben."
                ),
            )

    def _start_session(response: Response) -> None:
        """Legt eine Sitzung an und haengt das Cookie an die Antwort.

        `secure` fehlt hier ABSICHTLICH und darf nicht "der Sicherheit
        halber" ergaenzt werden: dieser Dienst spricht HTTP (Spec 14.1), ein
        `Secure`-Cookie wuerde vom Browser verworfen und niemand kaeme mehr
        hinein. `samesite="strict"` ist zugleich der CSRF-Schutz - eine
        fremde Seite kann damit keine zustandsaendernde Anfrage in einer
        angemeldeten Sitzung ausloesen, weshalb es kein eigenes CSRF-Token
        gibt."""
        response.set_cookie(
            SESSION_COOKIE,
            open_session(store.auth),
            max_age=SESSION_LIFETIME_SECONDS,
            httponly=True,
            samesite="strict",
            path="/",
        )

    @router.get("/auth-info")
    async def auth_info(request: Request) -> AuthInfoOut:
        session_id = request.cookies.get(SESSION_COOKIE)
        return AuthInfoOut(
            password_set=store.auth.password_hash() is not None,
            authenticated=(
                session_id is not None and session_is_valid(store.auth, session_id)
            ),
        )

    @router.post("/auth/setup")
    async def setup(body: PasswordIn, response: Response) -> StatusOut:
        """Ersteinrichtung - ohne weiteren Nachweis, solange kein Passwort
        gesetzt ist (Spec 5, Trust on first use).

        Das ist eine bewusst getroffene Abwaegung und keine vergessene
        Pruefung: zwischen dem Start ohne Passwort und dieser Vergabe kann
        jeder, der den Dienst erreicht, ihn uebernehmen. Entschieden am
        3. September 2026 gegen Einrichtungscode im Log, Zeitfenster und
        Erstpasswort per CLI, damit die Einrichtung headless ueber die
        Oberflaeche moeglich bleibt - und ausdruecklich auch fuer ein
        Bestandssystem mit bereits konfiguriertem Token, das hier NICHT
        zusaetzlich abgefragt wird.

        `set_password_hash_if_unset` entscheidet in einer einzigen
        SQL-Anweisung, ob dieser Aufruf der erste war - deshalb koennen zwei
        gleichzeitige Einrichtungen sich nicht ueberschreiben."""
        _require_length(body.password)
        if not store.auth.set_password_hash_if_unset(hash_password(body.password)):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Für diesen Dienst ist bereits ein Passwort vergeben – die "
                    "Ersteinrichtung ist damit dauerhaft abgeschlossen. Passwort "
                    "vergessen? `loxmatter set-password` auf dem Host setzt es neu."
                ),
            )
        _start_session(response)
        return StatusOut(status="ok")

    @router.post("/auth/login")
    async def login(body: PasswordIn, request: Request, response: Response) -> StatusOut:
        # Die Peer-Adresse der Verbindung, NICHT `X-Forwarded-For`: den
        # Header setzt jeder Aufrufer selbst, und die Drosselung liesse sich
        # damit umgehen, indem man je Versuch eine andere Adresse behauptet.
        client = request.client.host if request.client is not None else "unbekannt"
        wait = throttle.retry_after(client)
        if wait:
            raise HTTPException(
                status_code=429,
                detail=f"Zu viele Fehlversuche – in {wait} Sekunden wieder möglich.",
            )
        stored = store.auth.password_hash()
        if stored is None:
            # 409 und nicht 401: es gibt kein Passwort, mit dem dieser Aufruf
            # gelingen koennte - eine Wiederholung mit Zugangsdaten hilft
            # nicht (dieselbe Unterscheidung wie in RFC 9110).
            raise HTTPException(
                status_code=409,
                detail=(
                    "Für diesen Dienst ist noch kein Passwort vergeben – bitte zuerst "
                    "die Ersteinrichtung abschließen."
                ),
            )
        if not verify_password(body.password, stored):
            throttle.record_failure(client)
            raise HTTPException(status_code=401, detail="Falsches Passwort.")
        throttle.record_success(client)
        _start_session(response)
        return StatusOut(status="ok")

    @router.post("/auth/logout")
    async def logout(request: Request, response: Response) -> StatusOut:
        """Beendet die Sitzung SERVERSEITIG und raeumt danach das Cookie ab.

        Die Reihenfolge ist der Punkt: ein Logout, der nur das Cookie
        loescht, laesst eine bereits abgeflossene Kennung dreissig Tage
        weiterleben."""
        session_id = request.cookies.get(SESSION_COOKIE)
        if session_id is not None:
            store.auth.delete_session(session_id)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return StatusOut(status="ok")

    return router
