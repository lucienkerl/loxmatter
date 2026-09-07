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

"""Report German text left in tracked files.

The project is English-only (see
docs/superpowers/specs/2026-09-07-english-only-translation-design.md).
This script is the enforcer for that rule: without it the translation holds
only until the next feature branch.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

# German function words that no English sentence produces. Deliberately
# excludes words English shares or spells the same ("die", "war", "hat",
# "bald", "gift", "also", "an", "in", "so", "was") - a detector that cries
# wolf gets switched off, which is worse than one that misses a word.
_GERMAN_WORDS_TEXT = """
    aber auch aus bei beim dass dem den der des diese diesem diesen dieser dieses
    durch ein eine einem einen einer eines fuer gegen ihre kann kein keine muss
    nach nicht noch nur oder sich sind statt ueber und vom von vor werden wird wurde
    wurden zum zur zwischen ohne weil wenn damit dann schon immer jede jeder
    jedes alle allen etwa sowie bereits mehrere andere weitere
    """
GERMAN_WORDS = frozenset(_GERMAN_WORDS_TEXT.split())

# Words that only German produces. Generated once from the pre-translation
# tree (see Step 3a) and then frozen: transliterations like "Geraet" cannot
# be found by pattern, because `ae`, `oe` and `ue` are ordinary English
# ("does", "goes", "value", "true", "across"). A pattern for those would fire
# on almost every English file, and a detector that cries wolf gets switched
# off - which is worse than one that misses a word.
_GERMAN_STEMS_TEXT = """
    geraet geraete geraets uebersetzung uebersetzungen schluessel bruecke
    oberflaeche laeuft faellt haelt traegt ueber fuer koennen koennte muessen
    waere naechste naechsten zurueck aenderung loesung groesse gemaess
    schliesst heisst weiss draussen aussen ausserhalb spaeter spaeteren
    haengt haengen zaehler zaehlt erklaerung erklaert vollstaendig
    tatsaechlich urspruenglich zusaetzlich moeglich moeglichkeit

    aendern aendert aktuell aktuelle aktuellen anfuehrungszeichen anhaengen
    aufgeloest aufloesung aufraeumen ausdruecklich ausgaenge ausloesen bauen
    befuellt begruendung behaelt bekaeme bestaetigt bloecke braeuchte
    bruecken dafuer darueber dauerhaft duerfen
    eingaenge eintraege entfaellt enthaelt ergaenzt faelle faelschlich
    faende faengt feuert frueher fruehere frueheren fuegt fuehren fuehrt
    fuellt fuenf geaendert gegenstueck gehoeren gehoert geprueft
    geraetekarte geraeteliste geraeten geraetetyp geraetetypen geschuetzt
    geschuetzten geschuetztes gewaehlte gewaehlten gruen gueltig gueltige
    gueltiges haekchen haelfte haette haetten hoechstens hoeher kaeme
    kueche laedt laengst laesst loeschen loeschkreuz loescht loest luecke
    manuell manuelle menue menues muesste nachtraeglich netzwerkschluessel
    neue neuen neuer neues noetig oeffnen oeffnet praefix pruefen prueft
    pruefung quelle quellen quelltext raeume raeumt saeen saehe schlaegt
    schuetzt signalschluessel spaetere spaeterer steuert stuende
    tatsaechliche tatsaechlichen traefe ueberall ueberhaupt ueberlebt
    uebernehmen uebernimmt uebernommen ueberschreiben ueberschrift
    uebersetzt uebersetzten uebersetzungstabelle uebersprungen uebrig
    uebrigen unabhaengig unabhaengige unberuehrt ungeschuetzt unterstuetzt
    unveraendert urspruengliche veraendert verlaesst virtuell virtuelle
    virtuellen virtueller vollstaendige waechter waehlt waehrend waeren
    wuerde wuerden zaehlung zuerst zufaellig zugehoerige zuruecksetzen
    zuruecksetzt zusaetzliche zusammenfuehren zuverlaessig zwoelf
    """
GERMAN_STEMS = frozenset(_GERMAN_STEMS_TEXT.split())

# Literal umlauts never appear in English. This is the one pattern that can
# be trusted without a word list.
UMLAUT_CHARS = re.compile(r"[ÄÖÜäöüß]")

WORD = re.compile(r"[A-Za-zÄÖÜäöüß]+")

# Exemptions, each one a decision recorded in section 2.2 of the spec.
EXEMPT_PREFIXES = ("src/loxmatter/web/vendor/",)
EXEMPT_PATHS = frozenset(
    {
        "LICENSE",
        "uv.lock",
        # This script's own word lists, and the fixtures that prove they work,
        # are German by necessity - the detector cannot hold its vocabulary in
        # any other language. Same category of exemption as the de: values:
        # German as data, not German prose someone forgot to translate.
        "scripts/check_language.py",
        "tests/devtools/test_check_language.py",
    }
)
# The de: values in the string table are product content, not developer
# German - removing them would delete the language switcher. This matches
# both a single-line value ('de: "..."') and the opening line of a block
# scalar ('de: |'); DE_BLOCK_SCALAR below tells the two apart so the block
# form can also exempt its (indented) continuation lines.
DE_VALUE = re.compile(r"^\s*de:\s")
DE_BLOCK_SCALAR = re.compile(r"^(\s*)de:\s*[|>][+-]?\s*$")

# German that is data rather than untranslated prose. Each entry is a
# decision, not an oversight:
GERMAN_AS_DATA = (
    # export/documents.py: the umlaut transliteration table. Its keys are
    # German letters - that is the whole point of the table.
    "_UMLAUTS = {",
    # projectsync/schema.py: the titles Loxone Config itself gives caption
    # folders in a German installation (verified against a real reference
    # project). loxmatter has to write what Loxone Config writes.
    "Virtuelle Eing",
    "Virtuelle Ausg",
)


class Finding(NamedTuple):
    line: int
    word: str
    text: str


def _is_exempt(path: str) -> bool:
    return path in EXEMPT_PATHS or path.startswith(EXEMPT_PREFIXES)


def scan_text(text: str, path: str) -> list[Finding]:
    if _is_exempt(path):
        return []
    findings: list[Finding] = []
    # None outside a de: block scalar; otherwise the indentation of the
    # `de:` key that opened it - continuation lines indented deeper than
    # this are still that key's value, and stay exempt until a line at or
    # below this indentation ends the block (blank lines never end it).
    de_block_indent: int | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if de_block_indent is not None:
            if line.strip() == "":
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent > de_block_indent:
                continue
            de_block_indent = None
        if DE_VALUE.match(line):
            block_match = DE_BLOCK_SCALAR.match(line)
            if block_match:
                de_block_indent = len(block_match.group(1))
            continue
        if any(marker in line for marker in GERMAN_AS_DATA):
            continue
        for match in WORD.finditer(line):
            word = match.group(0)
            lowered = word.lower()
            if lowered in GERMAN_WORDS or lowered in GERMAN_STEMS:
                findings.append(Finding(number, word, line.strip()))
                break
            if UMLAUT_CHARS.search(word):
                findings.append(Finding(number, word, line.strip()))
                break
    return findings


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    paths = argv if argv else tracked_files()
    total = 0
    for path in paths:
        file = Path(path)
        try:
            text = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for finding in scan_text(text, path):
            print(f"{path}:{finding.line}: German word {finding.word!r}: {finding.text}")
            total += 1
    if total:
        print(f"\n{total} line(s) still contain German.", file=sys.stderr)
        return 1
    print("No German found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
