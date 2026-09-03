"""Der Teil des Stores, der den Zugang verwaltet - Passwort-Hash und
Sitzungen - statt Geraete, Signale und Kommandos.

Eigenes Modul und eigene Klasse, nicht weitere Methoden an `Store`: dort
liegen inzwischen ueber neunhundert Zeilen zum Geraetemodell, und der Zugang
hat damit fachlich nichts zu tun. Die Verbindung gehoert trotzdem weiterhin
`Store` - diese Klasse ist eine Sicht darauf, kein zweiter Verbindungsaufbau
auf dieselbe Datei (das waere eine zweite Sperrdomaene fuer dieselben Daten).

Was hier NICHT stattfindet: Kryptografie und HTTP. Diese Klasse legt einen
Hash ab und liest ihn wieder, ohne zu wissen, wie er entsteht (siehe
`loxmatter.auth.passwords`), und sie kennt weder Cookies noch Statuscodes
(siehe `loxmatter.auth.sessions` und `loxmatter.api.auth`). Wer das hier
vermischt, hat am Ende drei Stellen, an denen ein Geheimnis auftauchen kann,
statt einer.

Das Schema der beiden Tabellen steht in `store.py` bei `_SCHEMA` und
`_migrate_to_v4` - Schema-Definitionen bleiben an einem Ort, auch wenn der
Zugriff darauf hier liegt.
"""

from __future__ import annotations

import sqlite3

# Der einzige Schluessel, den `setting` bislang traegt. Die Tabelle ist
# trotzdem generisch (Schluessel/Wert) angelegt, weil die uebrige
# Konfiguration denselben Weg gehen soll (Spec 14.2) - eine Tabelle
# `password` mit einer Spalte waere in dem Moment wieder umzubauen.
_PASSWORD_KEY = "password_hash"


class AuthStore:
    """Zugriff auf `setting` und `session` ueber die Verbindung des Stores."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def password_hash(self) -> str | None:
        """Der abgelegte Hash - `None`, solange kein Passwort vergeben ist.

        `None` ist der Zustand, an dem der gesamte Zugang haengt: er
        bedeutet "Ersteinrichtung noch offen" und laesst nach
        `loxone.server.build_api_guard` keine einzige `/api`-Route zu."""
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_PASSWORD_KEY,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def set_password_hash_if_unset(self, value: str) -> bool:
        """Legt den Hash an, aber nur, wenn noch keiner da ist - `True`, wenn
        dieser Aufruf ihn gesetzt hat.

        `INSERT OR IGNORE` und nicht "erst pruefen, dann schreiben": SQLite
        entscheidet das in einer einzigen Anweisung, zwei gleichzeitige
        Einrichtungsversuche koennen sich also nicht gegenseitig
        ueberschreiben. Genau darauf verlaesst sich `POST /auth/setup`, um
        nach dem ersten Erfolg dauerhaft mit 409 zu antworten."""
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO setting (key, value) VALUES (?, ?)",
            (_PASSWORD_KEY, value),
        )
        self._db.commit()
        return cursor.rowcount == 1

    def set_password_hash(self, value: str) -> None:
        """Setzt den Hash und ueberschreibt einen vorhandenen, fuer sich
        allein committend.

        NICHT der Weg fuer `loxmatter set-password` (siehe `reset_password`
        unten, der diese Anweisung mit dem Abmelden aller Sitzungen zu EINER
        Transaktion zusammenfasst) - dieser Baustein bleibt oeffentlich, weil
        Testcode ihn nutzt, um in einer Fixture ein Passwort vorzugeben, ohne
        dabei Sitzungen anzufassen."""
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_PASSWORD_KEY, value),
        )
        self._db.commit()

    def reset_password(self, value: str) -> None:
        """Setzt einen neuen Hash und meldet alle Sitzungen ab - in EINER
        Transaktion, nicht als zwei fuer sich genommen committende Schritte.

        Der einzige Aufrufer ist `loxmatter set-password` (Spec 9,
        Notausgang). Getrennt committende Anweisungen liessen ein Fenster
        offen, in dem bereits das neue Passwort gilt, waehrend eine alte -
        eigentlich abzumeldende - Sitzung noch weiterlaeuft: scheitert der
        zweite Schritt (z. B. ein voller Datentraeger zwischen den beiden
        Commits), bleibt genau der Zustand stehen, gegen den dieser Befehl
        gebaut wurde. Ein gemeinsamer Commit macht das unmoeglich - entweder
        gilt hinterher beides oder keins von beidem."""
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_PASSWORD_KEY, value),
        )
        self._db.execute("DELETE FROM session")
        self._db.commit()

    def create_session(self, session_id: str, *, created_at: int, expires_at: int) -> None:
        self._db.execute(
            "INSERT INTO session (id, created_at, expires_at) VALUES (?, ?, ?)",
            (session_id, created_at, expires_at),
        )
        self._db.commit()

    def session_expires_at(self, session_id: str) -> int | None:
        """Ablaufzeitpunkt als Unix-Sekunden - `None`, wenn es die Sitzung
        nicht (mehr) gibt. Ob sie damit noch gilt, entscheidet
        `loxmatter.auth.sessions`, nicht diese Klasse."""
        row = self._db.execute(
            "SELECT expires_at FROM session WHERE id = ?", (session_id,)
        ).fetchone()
        return None if row is None else int(row["expires_at"])

    def extend_session(self, session_id: str, *, expires_at: int) -> None:
        self._db.execute("UPDATE session SET expires_at = ? WHERE id = ?", (expires_at, session_id))
        self._db.commit()

    def delete_session(self, session_id: str) -> None:
        self._db.execute("DELETE FROM session WHERE id = ?", (session_id,))
        self._db.commit()

    def delete_all_sessions(self) -> None:
        """Meldet jeden ab. Aufgerufen von `loxmatter set-password`: wer das
        Passwort zuruecksetzt, will nicht, dass eine alte Sitzung
        weiterlaeuft."""
        self._db.execute("DELETE FROM session")
        self._db.commit()

    def purge_expired_sessions(self, now: int) -> None:
        """Raeumt abgelaufene Zeilen weg. Aufgerufen beim Anlegen einer neuen
        Sitzung - kein Hintergrundjob fuer eine Tabelle, die im Normalfall
        eine Handvoll Zeilen haelt."""
        self._db.execute("DELETE FROM session WHERE expires_at <= ?", (now,))
        self._db.commit()
