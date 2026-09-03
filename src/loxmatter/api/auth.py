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

import anyio
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


# Eigener, kleiner Limiter statt `starlette.concurrency.run_in_threadpool`
# (das anyios Standard-Limiter von 40 gleichzeitigen Threads verwendet und
# keinen eigenen durchreicht - deshalb hier direkt `anyio.to_thread.run_sync`
# mit `limiter=`). `hash_password`/`verify_password` rechnen scrypt mit
# 16 MiB je laufender Berechnung (`auth.passwords`); 4 Faeden sind damit
# 4 * 16 MiB = 64 MiB als Obergrenze dessen, was ein UNAUTHENTIFIZIERTER
# Aufrufer allein durch gleichzeitige Anmelde- oder Einrichtungsversuche
# binden kann - gegenueber 40 * 16 MiB = 640 MiB mit dem Standardwert.
# Gemessen: RSS-Spitze 476 MiB bei 40 gleichzeitigen Anmeldungen ueber den
# Standard-Threadpool, gegenueber 28 MiB zuvor (scrypt seriell im
# Event-Loop, siehe `verify_password`-Aufruf unten). Das Zielgeraet ist ein
# Raspberry Pi, auf dem laut `deploy/testhost/docker-compose.yml`
# zusaetzlich `matter-server` und `otbr` denselben Speicher teilen - ein
# wiederholter Ausschlag dieser Groessenordnung, ausgeloest von aussen ohne
# jede Anmeldung, ist dort ein realistischer OOM-Ausloeser. Die Obergrenze
# gilt bewusst unabhaengig von `LoginThrottle`: die bremst nur WIEDERHOLTE
# Anfragen EINER Adresse, nicht viele GLEICHZEITIGE Anfragen verschiedener
# Adressen.
#
# Entsteht hier auf Modulebene, also AUSSERHALB jedes Event-Loops - das
# traegt nur, weil AnyIO dafuer einen Adapter liefert, der sich beim ersten
# Gebrauch an den dann laufenden Loop bindet und bei einem Wechsel neu
# bindet (uvicorn hat dafuer einen einzigen Loop, die Testsuite viele -
# beides funktioniert deshalb). Wer diese Konstante spaeter in eine Funktion
# verschiebt, sollte das wissen, statt sich zu wundern, warum es trotzdem
# noch geht.
_PASSWORD_HASH_LIMITER = anyio.CapacityLimiter(4)


# Der 409-Text von `/auth/setup` unten - eigene Konstante statt inline, weil
# `README.md` und der Release-Hinweis denselben Weg beschreiben und alle drei
# Stellen nicht auseinanderlaufen sollen. Der Container-Weg zuerst: das
# Referenz-Deployment (`deploy/testhost/docker-compose.yml`) legt die
# Datenbank in einem benannten Docker-Volume ab, das nur INNERHALB des
# Containers unter `LOXMATTER_STORE` erreichbar ist - `uv run loxmatter
# set-password` auf dem Host trifft dort mangels dieser Umgebungsvariable
# eine andere, neu angelegte Datei und meldet fälschlich Erfolg, ohne die
# Bruecke tatsaechlich zu entsperren (Notausgang-Fund, 2026-09-03).
_ALREADY_SET_UP_DETAIL = (
    "Für diesen Dienst ist bereits ein Passwort vergeben – die Ersteinrichtung "
    "ist damit dauerhaft abgeschlossen. Passwort vergessen? Im Referenz-"
    "Deployment setzt `docker compose exec loxmatter loxmatter set-password` "
    "es neu; bei einer Installation aus dem Quellcode `uv run loxmatter "
    "set-password`."
)


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
                detail=(f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben."),
            )

    def _client_id(request: Request) -> str:
        """Die Peer-Adresse der Verbindung, NICHT `X-Forwarded-For`: den
        Header setzt jeder Aufrufer selbst, und die Drosselung liesse sich
        damit umgehen, indem man je Versuch eine andere Adresse behauptet.
        Gemeinsam fuer `/auth/login` und `/auth/setup`, die sich seit deren
        Anbindung an dieselbe `LoginThrottle` dieselbe Kennung teilen."""
        return request.client.host if request.client is not None else "unbekannt"

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
            authenticated=(session_id is not None and session_is_valid(store.auth, session_id)),
        )

    @router.post("/auth/setup")
    async def setup(body: PasswordIn, request: Request, response: Response) -> StatusOut:
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

        Diese Route haengt (wie die drei anderen in diesem Modul) OHNE
        Waechter und ohne Anmeldung - deshalb zaehlen die naechsten drei
        Zeilen mehr als anderswo:

        1. Die billige Pruefung `password_hash() is not None` steht VOR dem
           Hashen, nicht danach. `hash_password` rechnet scrypt mit 16 MiB
           Speicher - auf einem Raspberry Pi ein zweistelliger Millisekunden-
           betrag, UND SYNCHRON im Event-Loop (siehe Punkt 2). Wer diese
           Route erreicht, haette sonst auf einer laengst eingerichteten
           Bruecke mit einer einzigen Verbindung im Dauerfeuer den Event-Loop
           auslasten und damit `/cmd`/`/resync` mit ausbremsen koennen -
           genau die beiden Routen, die dieser Entwurf ausdruecklich immer
           erreichbar halten will. `set_password_hash_if_unset` bleibt
           trotzdem stehen: sie ist die einzige Absicherung gegen den
           winzigen Rest-Wettlauf zwischen dieser Pruefung und dem
           tatsaechlichen Schreiben (zwei gleichzeitige ERSTE Einrichtungen),
           kostet im Normalfall aber nichts mehr, weil sie dann gar nicht
           mehr erreicht wird.
        2. `hash_password` laeuft ueber `anyio.to_thread.run_sync` in einem
           eigenen, auf 4 begrenzten Thread-Limiter (`_PASSWORD_HASH_LIMITER`
           oben) - nicht ueber `starlette.concurrency.run_in_threadpool` und
           dessen anyio-Standardlimiter von 40 Threads, der keinen eigenen
           Wert durchreicht. `hashlib.scrypt` gibt die Kontrolle nie an den
           Event-Loop zurueck; ohne einen Thread wuerde JEDE gleichzeitige
           Anfrage - auch an `/cmd`, `/resync` und jede andere Route dieses
           Prozesses - fuer die Dauer der Berechnung stillstehen. Der enge
           Limiter begrenzt zusaetzlich, wieviele dieser 16-MiB-Berechnungen
           gleichzeitig im Speicher stehen koennen (Review-Fund, 2026-09-03,
           siehe Kommentar an `_PASSWORD_HASH_LIMITER`).
        3. Der Fehlversuch wird HIER, VOR dem `await anyio.to_thread.run_sync`,
           optimistisch gebucht - genau wie in `/auth/login` unten, dessen
           Kommentar an der Buchung die ausfuehrliche Begruendung und die
           Messung traegt (60 statt hoechstens `FAILURES_BEFORE_THROTTLING`
           echter Rateversuche ohne das Vorziehen). Dieselbe `LoginThrottle`
           begrenzt so, wie oft in kurzer Zeit ueberhaupt bis zum Hashen
           vorgedrungen werden kann - der Fall, den Punkt 1 nicht abdeckt:
           eine Bruecke, deren Ersteinrichtung noch nie stattgefunden hat.
        """
        client = _client_id(request)
        wait = throttle.retry_after(client)
        if wait:
            raise HTTPException(
                status_code=429,
                detail=f"Zu viele Fehlversuche – in {wait} Sekunden wieder möglich.",
            )
        _require_length(body.password)
        if store.auth.password_hash() is not None:
            # KEIN `record_failure` hier (Review-Fund, 2026-09-03): dieser
            # Zweig ist seit der billigen Pruefung oben in Punkt 1 kein
            # Fehlversuch gegen ein Geheimnis, sondern nur noch ein indizier-
            # tes SELECT - es gibt hier nichts mehr zu schuetzen. Vorher
            # zaehlte er dennoch mit und teilte sich den Zaehler mit
            # `/auth/login`: eine Oberflaeche, die faelschlich noch den
            # Einrichtungsbildschirm zeigt (z. B. weil `/auth-info` beim
            # Laden fehlschlug), sperrte einen Betreiber, der fuenfmal
            # "Passwort vergeben" klickt, damit auch aus dem LOGIN aus -
            # ohne dass er je ein falsches Passwort eingegeben haette. Die
            # Drosselungs-*Pruefung* oben bleibt unveraendert bestehen.
            raise HTTPException(status_code=409, detail=_ALREADY_SET_UP_DETAIL)
        throttle.record_failure(client)
        hashed = await anyio.to_thread.run_sync(
            hash_password, body.password, limiter=_PASSWORD_HASH_LIMITER
        )
        if not store.auth.set_password_hash_if_unset(hashed):
            # Der Wettlauf aus Punkt 1 oben: zwischen der Pruefung und diesem
            # Schreiben hat eine andere, gleichzeitige Einrichtung gewonnen.
            # KEIN zweites `record_failure` hier (Review-Fund, 2026-09-03):
            # der Fehlversuch steht bereits vor dem `await` oben zu Buche -
            # ein weiterer hier waere eine Doppelbuchung fuer denselben
            # Versuch.
            raise HTTPException(status_code=409, detail=_ALREADY_SET_UP_DETAIL)
        throttle.record_success(client)
        _start_session(response)
        return StatusOut(status="ok")

    @router.post("/auth/login")
    async def login(body: PasswordIn, request: Request, response: Response) -> StatusOut:
        client = _client_id(request)
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
        # Ueber den Thread-Limiter wie `hash_password` oben in `setup`:
        # `hashlib.scrypt` blockiert den Event-Loop synchron, und diese
        # Route haengt wie `/auth/setup` ohne Waechter (siehe Moduldocstring)
        # - eine Anmeldung darf `/cmd`/`/resync` genauso wenig ausbremsen wie
        # ein Einrichtungsversuch.
        #
        # Der Fehlversuch wird HIER, VOR dem `await`, optimistisch gebucht -
        # nicht erst nach dessen Ergebnis. Grund: `anyio.to_thread.run_sync`
        # gibt an dieser Stelle die Kontrolle an den Event-Loop zurueck, und
        # in der Zeit koennen beliebig viele gleichzeitige Anfragen DERSELBEN
        # Adresse ebenfalls an `throttle.retry_after` oben vorbeikommen,
        # bevor auch nur eine von ihnen den Zaehler erhoeht hat - ein
        # Pruefen-dann-Handeln-Fenster, das der fruehere synchrone Code nicht
        # hatte (dort lief der Rumpf gegenueber dem Loop atomar). Gemessen:
        # 60 gleichzeitige Falschanmeldungen von einer Adresse ergaben ohne
        # dieses Vorziehen 60 statt der vorgesehenen
        # `FAILURES_BEFORE_THROTTLING` echten Rateversuche - die Drosselung
        # liess sich durch reine Parallelitaet vollstaendig umgehen. Bei
        # Erfolg holt `record_success` den Zaehler sofort wieder herunter,
        # das sequenzielle Verhalten (fuenf Fehlversuche, danach eine Sperre)
        # bleibt dadurch unveraendert.
        #
        # Zwei Eigenschaften dieser vorgezogenen Buchung sind gewollt, aber
        # nicht offensichtlich: Bricht die Anfrage waehrend des `await` ab
        # (Verbindungsabbruch, abgebrochene Aufgabe, ein scrypt-Fehler unter
        # Last), nimmt niemand die Buchung zurueck - ein Betreiber kann sich
        # so, ohne je ein falsches Passwort einzugeben, in Richtung der
        # Sperre bewegen. Fuer einen Sicherheitszaehler ist das die richtige
        # Richtung (im Zweifel zaehlen statt vergessen). Und ein
        # ERFOLGREICHER fuenfter Versuch setzt fuer die Dauer des
        # scrypt-Laufs ebenfalls kurzzeitig eine Sperre, die `record_success`
        # gleich danach selbst wieder aufhebt.
        throttle.record_failure(client)
        if not await anyio.to_thread.run_sync(
            verify_password, body.password, stored, limiter=_PASSWORD_HASH_LIMITER
        ):
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
