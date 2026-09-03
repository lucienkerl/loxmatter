"""Sitzungen: anlegen, pruefen, gleitend verlaengern (Spec 7).

**Warum in der Datenbank und nicht im Speicher:** der Dienst laeuft mit
`restart: unless-stopped` (siehe `deploy/testhost/docker-compose.yml`). Ein
Neustart - nach einem Update, nach einem Stromausfall, nach einem Absturz -
duerfte sonst jeden angemeldeten Browser abmelden, und der Betreiber saehe
statt seiner Bruecke einen Login-Bildschirm, ohne zu wissen warum.

**Warum ein serverseitiger Eintrag und kein signiertes Cookie:** ein
signiertes Cookie liesse sich nicht zurueckziehen. `POST /auth/logout` und
`loxmatter set-password` sollen eine Sitzung wirklich beenden koennen, nicht
nur den Browser bitten, sie zu vergessen.

`now` ist in beiden Funktionen ein optionaler Parameter (Unix-Sekunden).
Produktivcode uebergibt ihn nie; die Tests brauchen ihn, um dreissig Tage
vergehen zu lassen, ohne zu schlafen.
"""

from __future__ import annotations

import secrets
import time

from loxmatter.model.auth_store import AuthStore

# Der Cookie-Name. Steht hier und nicht in `api/auth.py`, weil ihn zwei
# Stellen brauchen: der Router setzt ihn, der Waechter in
# `loxone/server.py` liest ihn. Zwei Schreibweisen desselben Namens waeren
# ein Fehler, den niemand im Test bemerkt, weil beide Seiten fuer sich
# funktionieren.
SESSION_COOKIE = "loxmatter_session"

SESSION_LIFETIME_SECONDS = 30 * 24 * 60 * 60

# Verlaengert wird erst, wenn mehr als ein Tag der Laufzeit verbraucht ist -
# siehe `session_is_valid`.
_EXTEND_AFTER_SECONDS = 24 * 60 * 60


def open_session(auth: AuthStore, *, now: int | None = None) -> str:
    """Legt eine Sitzung an und gibt ihre Kennung zurueck.

    32 Byte aus `secrets.token_hex` - dieselbe Groessenordnung wie das
    empfohlene API-Token (`openssl rand -hex 32`), weil diese Kennung
    genau dasselbe wert ist: wer sie hat, ist angemeldet."""
    moment = int(time.time()) if now is None else now
    auth.purge_expired_sessions(moment)
    session_id = secrets.token_hex(32)
    auth.create_session(
        session_id, created_at=moment, expires_at=moment + SESSION_LIFETIME_SECONDS
    )
    return session_id


def session_is_valid(auth: AuthStore, session_id: str, *, now: int | None = None) -> bool:
    """Gilt diese Sitzung noch? Verlaengert sie dabei gleitend.

    Die Verlaengerung passiert hoechstens einmal je `_EXTEND_AFTER_SECONDS`
    und nicht bei jedem Aufruf: diese Funktion laeuft in JEDER Anfrage an
    `/api`, und die Oberflaeche stellt beim Bedienen mehrere je Sekunde. Ein
    `UPDATE` pro Anfrage waere eine SQLite-Schreiboperation fuer nichts.

    Eine abgelaufene Sitzung wird hier gleich geloescht - der Aufraeumpfad,
    der ohne einen neuen Login nie liefe."""
    moment = int(time.time()) if now is None else now
    expires_at = auth.session_expires_at(session_id)
    if expires_at is None:
        return False
    if expires_at <= moment:
        auth.delete_session(session_id)
        return False
    if expires_at - moment <= SESSION_LIFETIME_SECONDS - _EXTEND_AFTER_SECONDS:
        auth.extend_session(session_id, expires_at=moment + SESSION_LIFETIME_SECONDS)
    return True
