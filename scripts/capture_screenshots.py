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

"""Fotografiert die Oberflaeche fuer die README ab.

Aufruf:  uv run --with playwright python scripts/capture_screenshots.py

Startet `dev_web_server.py --demo` selbst, meldet sich an, klappert die
Ansichten ab und legt die Bilder unter docs/screenshots/ ab. Zweimal
aufgerufen entstehen dieselben Bilder - die Demo-Datenbank faellt bei jedem
Start neu an (siehe dort).

Die Selektoren unten sind aus dem tatsaechlichen Markup
(`src/loxmatter/web/index.html`) und den englischen Uebersetzungstexten
(`src/loxmatter/i18n/strings.yaml`) abgelesen, nicht geraten - die
Oberflaeche hat sich in einer Woche schon dreimal geaendert, ein geratener
Selektor waere beim naechsten Lauf schon wieder falsch. Playwright ist eine
Ad-hoc-Abhaengigkeit (siehe Aufruf oben) und bewusst NICHT in
pyproject.toml - sie wird nur zum Neuerzeugen dieser Bilder gebraucht.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"
PORT = 8420
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "loxmatter-demo"

# Fuer den `from tests...`-Import der Beispiel-Projektdatei unten.
sys.path.insert(0, str(ROOT))


def shoot(page: Page, name: str) -> None:
    page.wait_for_timeout(600)  # Alpine rendert nach dem Laden nach
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))
    print(f"  {name}.png")


def select_view(page: Page, label: str) -> None:
    """Klickt den Reiter ueber seinen (englischen) Beschriftungstext - klappt,
    weil die Demo-Datenbank ohne Sprachvorgabe startet und die Oberflaeche
    dann auf Englisch faellt (siehe dev_web_server.py --demo)."""
    page.click(f'nav.tabs button:has-text("{label}")')
    page.wait_for_timeout(400)


def capture(page: Page) -> None:
    page.goto(BASE, wait_until="networkidle")

    # Die Demo-Bruecke hat bereits ein Passwort (siehe --demo), also zeigt
    # sich der Login-Bildschirm, nicht die Ersteinrichtung - dort gibt es nur
    # EIN Passwortfeld.
    page.fill('input[type="password"]', PASSWORD)
    page.click('button:has-text("Log in")')

    # startApp() laedt Geraete, Signale, Export- und Bruecken-Einstellungen
    # parallel (app.js) - auf die erste Geraetekarte warten statt auf eine
    # feste Wartezeit zu vertrauen.
    page.wait_for_selector(".device-card", timeout=15000)
    page.wait_for_timeout(800)  # Werte-Chips und Live-Verbindung ziehen nach

    # Bewusst OHNE Bildlauf, direkt vom Seitenanfang: ein frueherer Versuch
    # scrollte hier zur ersten Geraetekarte, damit sich dieses Bild staerker
    # von `commissioning.png` unterscheidet - aber der Bildlauf schob dabei
    # `nav.tabs` (Devices/Signals/Export/System/Settings) mit aus dem Bild,
    # und genau dieser Reiterleiste sieht man an, dass hier eine Anwendung
    # mit mehreren Ansichten laeuft, nicht nur eine einzelne Seite. Das
    # eroeffnende Bild der Galerie ohne Reiterleiste zu zeigen wog schwerer
    # als die Aehnlichkeit zu `commissioning.png` - die beiden Bilder
    # unterscheiden sich weiterhin darin, dass dort ein Einlern-Code
    # eingetragen ist und hier nicht.
    shoot(page, "dashboard")

    select_view(page, "Signals")
    shoot(page, "signals")

    select_view(page, "Export")
    # Review-Fix: ohne Klick zeigte dieser Ausschnitt nur die beiden leeren
    # Karten "Project file sync" und "Export templates" - zwei Drittel Leerraum,
    # keine Vorschautabelle. "View preview" fuellt die dritte Karte darunter
    # mit echten Zeilen, noch bevor irgendetwas heruntergeladen wird.
    page.click('button:has-text("View preview")')
    page.wait_for_selector("table", timeout=5000)
    page.wait_for_timeout(300)
    shoot(page, "export")

    # Die "System check"-Karte ganz oben zeigt in dieser Demo (kein echter
    # matter-server-Client, kein echter UDP-Versand) zwei "Error"-Zeilen -
    # zurecht, aber fuer ein Screenshot-Wortmarke schlecht: sieht nach
    # kaputtem Produkt aus, ist aber nur die ehrliche Diagnose eines absichtlich
    # unvollstaendigen Demo-Aufbaus. Deshalb zur naechsten Karte scrollen und
    # dort erst fotografieren - die Ansicht selbst (Reiter "System") bleibt
    # dieselbe, nur der sichtbare Ausschnitt aendert sich.
    #
    # Review-Fix: geprueft, ob sich das ohne die Fehlerkarte trotzdem mit
    # sichtbarem Reiterleiste (`nav.tabs`) machen liesse - geht nicht. Nur
    # `header.app-header` ist `position: sticky`, `nav.tabs` scrollt mit der
    # Seite mit, und im Markup steht die Reiterleiste VOR der "System
    # check"-Karte. Jeder Bildlauf, der die Fehlerkarte aus dem Bild
    # schiebt, hat die Reiterleiste (die weiter oben im Fluss steht) also
    # schon vorher aus dem Bild geschoben - beides gleichzeitig ist mit
    # diesem Markup nicht zu haben. Deshalb bleibt die Reiterleiste hier
    # bewusst draussen, statt einen fingierten "gesunden" Systemcheck zu
    # zeigen.
    select_view(page, "System")
    # `scroll_into_view_if_needed()` scrollt nur, wenn das Element noch NICHT
    # sichtbar ist - die "Live diagnostics"-Karte steht aber schon im
    # Sichtbereich (die Fehlerkarte darueber ist nicht so hoch, wie man
    # denkt), also erzwingt das hier den Bildlauf mit `block: "start"`.
    page.evaluate(
        """() => {
            const heading = [...document.querySelectorAll('h2')]
                .find((h) => h.textContent.trim() === 'Live diagnostics');
            const card = heading?.closest('.card');
            if (!card) return;
            card.scrollIntoView({ block: 'start' });
            // Der `header.app-header` ist `position: sticky` und ueberdeckt
            // sonst genau die Kartenueberschrift, die `scrollIntoView` gerade
            // an den (gedachten) Seitenanfang gelegt hat.
            const header = document.querySelector('header.app-header');
            window.scrollBy(0, -(header?.offsetHeight ?? 0));
        }"""
    )
    page.wait_for_timeout(200)
    shoot(page, "system")

    select_view(page, "Settings")
    shoot(page, "settings")

    # Sonderfall 1: Einlern-Karte mit Beispielcode, aber NICHT abschicken -
    # ohne echten Matter-Server kaeme beim Absenden nur eine Fehlermeldung.
    # Erst an den Seitenanfang zurueckscrollen: ohne das haette dieses Bild
    # den Bildlaufstand des vorigen Reiters geerbt, statt zuverlaessig die
    # Kommissionierungs-Karte zu zeigen.
    select_view(page, "Devices")
    page.evaluate("window.scrollTo(0, 0)")
    page.fill('input[placeholder*="MT:"]', "MT:Y.K9042C00KA0648G00")
    shoot(page, "commissioning")

    # Sonderfall 2: Beispiel-Projektdatei aus den projectsync-Tests
    # hochladen und den Diff-Plan abwarten. Zwei deutsche Wortmarken aus der
    # Fixture (sie bildet ein "vorher"-Projekt nach, das frueher auf
    # Deutsch angelegt wurde) werden vorher durch englische ersetzt - sonst
    # zeigte der Diff eine deutsche Alt-Bezeichnung neben der englischen
    # Neu-Bezeichnung, und die README-Bilder sollen durchgehend Englisch
    # sein.
    from tests.projectsync.conftest import SAMPLE_PROJECT

    sample_text = (
        SAMPLE_PROJECT.replace("Alter Titel", "Old label")
        .replace("Altes Geraet erreichbar", "Old device reachable")
        .replace("Matter — Altes Geraet", "Matter — Old Device")
        .replace("Verwaist", "Orphaned")
    )
    # Im Temp-Verzeichnis statt unter `docs/screenshots/` (Review-Fix): das
    # ist ein versioniertes Verzeichnis, ein abgebrochener Lauf liesse dort
    # sonst eine Karteileiche zurueck, die `git status` unnoetig verschmutzt.
    with tempfile.TemporaryDirectory() as tmp_dir:
        sample = Path(tmp_dir) / "_sample.Loxone"
        sample.write_text(sample_text, encoding="utf-8")
        select_view(page, "Export")
        page.set_input_files('input[type="file"]', str(sample))
        page.wait_for_timeout(2500)  # Upload plus Diff-Berechnung
        shoot(page, "project-sync")


def main() -> int:
    server = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "scripts" / "dev_web_server.py"),
            "--demo",
            "--port",
            str(PORT),
        ]
    )
    try:
        time.sleep(4)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 960}, device_scale_factor=2)
            capture(page)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
