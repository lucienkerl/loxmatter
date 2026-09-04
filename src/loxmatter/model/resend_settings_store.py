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

"""Das Intervall des periodischen Resends - EINE Einstellung fuer die
gesamte Bruecke, zur Laufzeit ueber die WebUI/API aenderbar statt einer beim
Start fixierten Konstante. Siehe
docs/superpowers/specs/2026-09-04-periodischer-resend-design.md, Abschnitt 4.

Eigenes Modul und eigene Klasse, analog zu `locale_store.py`: die
`setting`-Tabelle ist generisch angelegt, genau damit weitere Konfiguration
wie diese hier denselben Weg gehen kann. Diese Klasse ist eine weitere Sicht
auf dieselbe Tabelle und dieselbe Verbindung, kein zweiter Verbindungsaufbau."""

from __future__ import annotations

import sqlite3

_INTERVAL_KEY = "resend_interval_seconds"

DEFAULT_RESEND_INTERVAL_SECONDS = 300.0
# Untergrenze (Entwurf, Abschnitt 5): schuetzt vor einem versehentlich zu
# kurzen Intervall, das bei vielen markierten Signalen genau den Burst
# erzeugen wuerde, den dieser Entwurf eigentlich vermeiden soll.
MIN_RESEND_INTERVAL_SECONDS = 10.0


class ResendSettingsStore:
    """Zugriff auf `setting` ueber die Verbindung des Stores - wie
    `LocaleStore`, nur fuer den Schluessel `"resend_interval_seconds"`."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def get_interval_seconds(self) -> float:
        """Der gespeicherte Wert - `DEFAULT_RESEND_INTERVAL_SECONDS`, solange
        nichts gespeichert ist ODER der gespeicherte Wert nicht mehr als Zahl
        lesbar ist (z. B. nach einer manuellen Aenderung der Datenbank von
        aussen). Dieser eine Fall faellt bewusst still auf den Vorgabewert
        zurueck, statt den Aufrufer (den periodischen Timer in
        `Runtime._resend_loop`) daran scheitern zu lassen. Ein echter
        Datenbankfehler (z. B. eine gesperrte Datei) wird davon NICHT
        abgefangen und wirft weiterhin - `_resend_loop` hat dafuer einen
        eigenen Fehlerpfad (siehe dort), der genau das erwartet."""
        row = self._db.execute(
            "SELECT value FROM setting WHERE key = ?", (_INTERVAL_KEY,)
        ).fetchone()
        if row is None:
            return DEFAULT_RESEND_INTERVAL_SECONDS
        try:
            return float(row["value"])
        except (ValueError, TypeError):
            return DEFAULT_RESEND_INTERVAL_SECONDS

    def set_interval_seconds(self, seconds: float) -> None:
        if seconds < MIN_RESEND_INTERVAL_SECONDS:
            raise ValueError(
                f"Resend-Intervall muss mindestens {MIN_RESEND_INTERVAL_SECONDS}s betragen, "
                f"bekommen: {seconds}"
            )
        self._db.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_INTERVAL_KEY, str(seconds)),
        )
        self._db.commit()
