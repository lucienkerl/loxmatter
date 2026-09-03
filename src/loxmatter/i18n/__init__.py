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

"""Uebersetzungsmechanismus: eine flache YAML-Tabelle (`strings.yaml`) plus
eine prozessweite "aktuelle Sprache" - siehe
docs/superpowers/specs/2026-09-03-i18n-phase-a-sprachwahl-cli-design.md,
Abschnitt 3.

Eine einzige, gemeinsame Spracheinstellung fuer die ganze Installation
(nicht pro Anfrage, nicht pro Thread) - deshalb ein Modul-globaler Zustand
statt eines Objekts, das jeder Aufrufer selbst herumreichen muesste. Wer die
Sprache aendert (CLI-Bootstrap in cli.py, spaeter die WebUI in Phase B) ruft
`set_language()` genau einmal auf; jeder folgende `t()`-Aufruf im selben
Prozess sieht die neue Sprache sofort."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"en", "de"})
DEFAULT_LANGUAGE = "en"

_STRINGS_PATH = Path(__file__).with_name("strings.yaml")


def _load_strings() -> dict[str, dict[str, str]]:
    raw = yaml.safe_load(_STRINGS_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), f"{_STRINGS_PATH} muss eine Zuordnung auf oberster Ebene sein"
    return raw


# Einmal beim Import geladen, nicht bei jedem t()-Aufruf - strings.yaml
# aendert sich nie zur Laufzeit, nur zwischen Releases.
_STRINGS: dict[str, dict[str, str]] = _load_strings()

_current_language: str = DEFAULT_LANGUAGE


def set_language(language: str) -> None:
    """Setzt die prozessweite aktuelle Sprache.

    Wirft `ValueError` fuer alles ausser den Werten in `SUPPORTED_LANGUAGES`
    - und aendert die aktuelle Sprache in dem Fall NICHT (kein Teil-Erfolg)."""
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"nicht unterstuetzte Sprache {language!r}, erwartet eine von "
            f"{sorted(SUPPORTED_LANGUAGES)}"
        )
    global _current_language
    _current_language = language


def current_language() -> str:
    return _current_language


def t(key: str, **values: Any) -> str:
    """Liefert den uebersetzten Text zu `key` in der aktuellen Sprache,
    mit `values` in die Platzhalter eingesetzt (`str.format`).

    Fehlt `key` selbst in der Tabelle, ist das ein Programmierfehler -
    `KeyError` faellt durch, statt ihn zu verschlucken. Fehlt nur die
    Uebersetzung der aktuellen Sprache (z. B. noch kein "de" fuer einen
    neuen Eintrag), liefert diese Funktion die englische Fassung - nie
    einen Absturz wegen einer fehlenden Uebersetzung, siehe
    `test.english_only` in strings.yaml."""
    entry = _STRINGS[key]
    template = entry.get(_current_language, entry["en"])
    return template.format(**values)
