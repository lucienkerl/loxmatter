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
Ansichten ab und legt die Bilder unter docs/screenshots/ ab. Jedes Bild
zeigt nur den Bereich, von dem seine Bildunterschrift im README spricht -
warum, steht bei `shoot()`.

REPRODUZIERBAR sind sieben der acht Bilder: die Demo-Datenbank faellt bei
jedem Start neu an, und alle gesaeten Zeitstempel stehen auf
`DEMO_TIMESTAMP` (siehe dev_web_server.py) statt auf der Wanduhr. Zweimal
aufgerufen entstehen dort byte-gleiche Dateien; ein Diff bedeutet also eine
echte Aenderung und darf nicht als Rauschen weggewinkt werden. `update.png`
gehoert zu den sieben: `_seed_update_dir()` unten schreibt sein eigenes
`state.json` in ein eigenes `--update-dir`, unabhaengig von
`dev_web_server.py`s eigener Demo-Saat, und begruendet dort auch, warum es
den Update-Knopf mitten im Lauf zeigt statt im Zustand "Update verfuegbar" -
Letzterer braeuchte einen echten GitHub-Abruf.

NICHT reproduzierbar ist `system.png`. Sein Kommando-Log zeigt die
HTTP-Anfragen dieses Laufs selbst, auf die Mikrosekunde genau - das
Protokoll der Aufnahme, waehrend sie stattfindet. Das liesse sich nur
festnageln, indem der Demo-Modus ein erfundenes Protokoll einsetzt, und ein
fingierter Log in der Dokumentation waere schlechter als ein rauschendes
Bild. Wer dieses Skript laufen laesst, ohne dass sich an der Oberflaeche
etwas geaendert hat, sollte `system.png` daher verwerfen und die uebrigen
sechs unveraendert vorfinden.

Die Selektoren unten sind aus dem tatsaechlichen Markup
(`src/loxmatter/web/index.html`) und den englischen Uebersetzungstexten
(`src/loxmatter/i18n/strings.yaml`) abgelesen, nicht geraten - die
Oberflaeche hat sich in einer Woche schon dreimal geaendert, ein geratener
Selektor waere beim naechsten Lauf schon wieder falsch. Playwright ist eine
Ad-hoc-Abhaengigkeit (siehe Aufruf oben) und bewusst NICHT in
pyproject.toml - sie wird nur zum Neuerzeugen dieser Bilder gebraucht.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "docs" / "screenshots"
PORT = 8420
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "loxmatter-demo"
# Eigenes Verzeichnis, getrennt von `dev_web_server.py`s eigenem
# `--demo`-Standard (der ein `state.json` fuer den Zustand "fertig" saet,
# siehe dort `_seed_demo_update_dir`) - dieses Skript will einen laufenden
# Job zeigen, siehe `_seed_update_dir()` unten.
UPDATE_DIR = Path(tempfile.gettempdir()) / "loxmatter-screenshot-update"

# `main` ist per CSS auf 960 px begrenzt, ein breiteres Fenster erzeugt also
# nur grauen Rand. 820 px lassen die Inhaltsspalte selbst die Breite bestimmen
# und legen das Kachelraster auf zwei Spalten - die Kacheln wachsen dabei von
# 300 auf 345 px, was im skalierten README-Bild direkt Lesbarkeit ist.
VIEWPORT_NARROW = 820
# Nur fuer die Export-Vorschau: ihre acht Spalten brauchen 895 px und bekaemen
# im schmalen Fenster einen waagerechten Bildlauf.
VIEWPORT_WIDE = 1440
VIEWPORT_HEIGHT = 1000
# Nur fuer das Signal-Modal (siehe dort): `.signals-modal` deckelt sich in
# style.css auf `max-height: 85vh`, alles darueber blaettert innerhalb des
# Dialogs statt das Fenster zu wachsen. Gemessen am tatsaechlichen Inhalt
# (drei Endpunkt-Gruppen der ersten Kachel, Hallway button: 8 + 8 + 1
# Zeilen samt Kopf- und Gruppenzeilen) sind das rund 1075 px - 85 % von
# 1350 px sind 1147 px, genug Luft, dass der Dialog sich auf seine
# tatsaechliche Inhaltshoehe schrumpft, statt selbst noch zu blaettern.
VIEWPORT_HEIGHT_SIGNALS = 1350

# Fuer den `from tests...`-Import der Beispiel-Projektdatei unten.
sys.path.insert(0, str(ROOT))


# Aufloeser fuer die Bereichsangaben unten. Eine Angabe ist entweder ein
# CSS-Selektor, `card:<Ueberschrift>` fuer die Karte um eine h2-Ueberschrift
# (Karten haben keine eigenen Klassen, ihre Ueberschrift ist das einzige
# stabile Merkmal) oder `nth:<Selektor>:<Index>` fuer den n-ten Treffer.
_RESOLVE_JS = """
  (spec) => {
    if (spec.startsWith('card:')) {
      const want = spec.slice(5);
      const h = [...document.querySelectorAll('.card h2')]
        .find((h) => h.textContent.trim() === want);
      return h ? h.closest('.card') : null;
    }
    if (spec.startsWith('nth:')) {
      const cut = spec.lastIndexOf(':');
      const all = document.querySelectorAll(spec.slice(4, cut));
      return all[Number(spec.slice(cut + 1))] ?? null;
    }
    return document.querySelector(spec);
  }
"""


def _seed_update_dir(update_dir: Path) -> None:
    """Writes the `state.json` behind `update.png` - a job partway
    through, not the "update available" state a first draft of this
    screenshot's caption assumed.

    That state needs `updateAvailable.target` set, which only ever comes
    from a real `GET /api/update/check` round trip to GitHub (see
    `api/update.py`) - `--demo` switches checking off precisely so every
    OTHER screenshot stays free of that dependency (see
    `dev_web_server.py`'s own `_seed_demo_update_dir`). Producing
    "available" here would mean either a live network call - this
    script's own reproducibility promise, gone - or a second, made-up
    code path standing in for what the check endpoint would have said.
    Neither is worth it for a screenshot whose only job is to show what
    pressing the button looks like.

    `phase: "health"` instead: the last of the four running steps, and
    the one the README's "unreachable for about a minute" sentence is
    about. It is also the more informative picture of the two - the step
    list, the version being installed and the restarting message all show
    at once, none of which "available" shows on its own.

    Written to its OWN `--update-dir` (see `UPDATE_DIR` above), not the
    directory `dev_web_server.py --demo` seeds by itself: passing
    `--update-dir` explicitly is exactly the "developer takes manual
    control" escape hatch that flag documents there, and it also keeps
    this seed off of `--demo`'s own rmtree-then-reseed cleanup, which
    would otherwise overwrite it with the "done" state on every run.

    Same shape and the same 0.2.0 -> 0.3.0 pair as
    `dev_web_server.py._seed_demo_update_dir` - and the same reasoning for
    a fresh `updater_seen_at` on every call: `update.updater_present`
    reads anything older than 30 seconds as absent, so a frozen timestamp
    would make the card fall back to "no updater" the moment it went
    stale. Nothing in the interface displays the value itself."""
    update_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "id": "screenshot-update-job",
        "phase": "health",
        "from": "0.2.0",
        "to": "0.3.0",
        "error": None,
        "rolled_back": False,
        "healthy": True,
        "updater_seen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (update_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def shoot(
    page: Page,
    name: str,
    top: str,
    bottom: str | None = None,
    *,
    fixed: bool = False,
    pad_top: int | None = None,
    pad_bottom: int | None = None,
) -> None:
    """Fotografiert den Bereich von `top` bis `bottom` statt des ganzen Fensters.

    Die Bilder stehen im README einspaltig und haben dort rund 896 px
    Inhaltsbreite - alles Breitere wird herunterskaliert. Entscheidend fuer
    die Lesbarkeit ist deshalb allein die CSS-Breite des Ausschnitts, nicht
    seine Hoehe: ein hohes schmales Bild bleibt lesbar, ein 1440 px breites
    Fenster kommt auf knapp der Haelfte an. Die Ausschnitte hier liegen
    zwischen 768 und 952 px und werden damit praktisch unskaliert angezeigt;
    wer sie breiter zieht, verschenkt genau diesen Gewinn.

    Ein Vollbild verschenkte gleich doppelt - `main` ist auf 960 px begrenzt,
    also war ein Drittel jedes Bildes leerer grauer Rand, und darueber hinaus
    zeigte jedes Bild Karten, die seine eigene Bildunterschrift gar nicht
    meint.

    `fixed=True` fuer den Dialog: der ist `position: fixed` und steht damit
    im Fenster, nicht im Dokument - Dokumentkoordinaten gingen daneben.
    """
    page.wait_for_timeout(600)  # Alpine rendert nach dem Laden nach
    rect = page.evaluate(
        """([topSpec, bottomSpec, fixed]) => {
          const resolve = """
        + _RESOLVE_JS
        + """;
          const a = resolve(topSpec);
          const b = bottomSpec ? resolve(bottomSpec) : a;
          if (!a || !b) return null;
          const ra = a.getBoundingClientRect();
          const rb = b.getBoundingClientRect();
          const ox = fixed ? 0 : window.scrollX;
          const oy = fixed ? 0 : window.scrollY;
          return {
            x: Math.min(ra.left, rb.left) + ox,
            y: Math.min(ra.top, rb.top) + oy,
            right: Math.max(ra.right, rb.right) + ox,
            bottom: Math.max(ra.bottom, rb.bottom) + oy,
          };
        }""",
        [top, bottom, fixed],
    )
    if rect is None:
        raise RuntimeError(f"{name}: Bereich '{top}' bis '{bottom}' nicht im Markup gefunden")
    # Der Rand ist oben und unten einzeln einstellbar, weil ein Ausschnitt
    # sonst ins Nachbarelement blutet: 16 px ueber der Zaehlerzeile reichen
    # genau in die Upload-Zeile darueber, und ein waagerecht halbierter
    # Bedienteil sieht nach kaputtem Bild aus, nicht nach Ausschnitt. Wo die
    # Nachbarschaft eng ist, schneidet `0` sauber auf der Kante.
    pad = 16
    top_pad = pad if pad_top is None else pad_top
    bottom_pad = pad if pad_bottom is None else pad_bottom
    clip = {
        "x": max(rect["x"] - pad, 0),
        "y": max(rect["y"] - top_pad, 0),
        "width": rect["right"] - rect["x"] + 2 * pad,
        "height": rect["bottom"] - rect["y"] + top_pad + bottom_pad,
    }
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), clip=clip, full_page=not fixed)
    print(f"  {name}.png  ({round(clip['width'])}x{round(clip['height'])} css px)")


def select_view(page: Page, label: str) -> None:
    """Klickt den Reiter ueber seinen (englischen) Beschriftungstext - klappt,
    weil die Demo-Datenbank ohne Sprachvorgabe startet und die Oberflaeche
    dann auf Englisch faellt (siehe dev_web_server.py --demo).

    Die Reiter sind `<a href="#/...">` und keine Knoepfe mehr (URL-Navigation,
    siehe `nav.tabs` in index.html)."""
    page.click(f'nav.tabs a:has-text("{label}")')
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

    # Raumleiste plus die ersten beiden Raumgruppen. Die Einlern-Karte
    # darueber bleibt bewusst draussen: sie ist das Motiv von
    # `commissioning.png`.
    #
    # Das Argument dafuer war urspruenglich ihre Groesse - mit ihren drei
    # Erklaerungsabsaetzen belegte sie die obere Haelfte eines Bildes, dessen
    # Bildunterschrift von der Geraeteliste spricht. Seit dem Umbau der Karte
    # (Entwurf "Code zuerst", 2026-09-07) ist sie weniger als halb so hoch,
    # das Argument also schwaecher; sie bleibt trotzdem draussen, denn zwei
    # Bilder nebeneinander, die dasselbe Motiv zeigen, sind ein Bild zu viel.
    #
    # Damit faellt auch die Reiterleiste aus diesem Bild - frueher stand hier
    # das Argument, gerade das eroeffnende Bild der Galerie muesse sie zeigen,
    # damit man die Anwendung als mehrseitig erkennt. Das Argument gilt
    # weiter, nur traegt es jetzt `commissioning.png` gleich daneben: dort
    # steht die Leiste vollstaendig im Bild. Zweimal dieselbe Leiste zu
    # zeigen war den halben Bildausschnitt nicht wert.
    shoot(page, "dashboard", ".room-bar", "nth:.device-grid:1")

    # Signale haben keinen eigenen Reiter mehr (Entwurf "Signale als Modal",
    # 2026-09-05) - das Bild entsteht jetzt aus dem Modal ueber dem
    # Geraeteraster. Der Weg dorthin ist derselbe wie fuer einen Nutzer:
    # Kebab der ersten Kachel, dann der Menuepunkt.
    page.click(".device-card .tile-menu > summary")
    page.click('.tile-menu-item:has-text("Edit signals")')
    page.wait_for_selector("dialog.signals-modal:not(.control-modal)[open]", timeout=5000)
    # Ein erster Anlauf klappte hier zusaetzlich die Expertengruppe auf, damit
    # mehr Zeilen im Bild stehen. Das Ergebnis war unbrauchbar: Playwright
    # scrollt zum Ziel eines `.click()`, und dieses Ziel liegt hinter 17
    # funktionalen Signalen - das Bild begann mitten in einer angeschnittenen
    # Zeile, ohne Ueberschrift, ohne erkennbar zu sein, WAS man da sieht.
    #
    # Der urspruengliche Grund, warum kein hoeheres Fenster noetig sei ("die
    # erste Kachel hat funktional genug Zeilen, um das Bild zu fuellen"),
    # stimmt seit der Endpunkt-Gruppierung (Entwurf 2026-09-07, Abschnitt
    # 7.4) nicht mehr: bei `VIEWPORT_HEIGHT` blieb der Dialog auf seinem
    # `max-height: 85vh` (style.css) geblaettert und das Bild endete mitten
    # in der zweiten Gruppe ("Button 2") - die dritte Gruppe ("Device", die
    # Batterie) war nie zu sehen, obwohl gerade die Gruppierung die
    # sichtbarste Aenderung dieses Umbaus ist. `VIEWPORT_HEIGHT_SIGNALS`
    # gibt dem Dialog genug Raum, sich auf seine tatsaechliche Hoehe zu
    # schrumpfen, statt selbst zu blaettern - danach zeigt ein einzelner
    # Bildlauf ab dem Seitenanfang alle drei Gruppen vollstaendig.
    page.set_viewport_size({"width": VIEWPORT_NARROW, "height": VIEWPORT_HEIGHT_SIGNALS})
    page.eval_on_selector("dialog.signals-modal:not(.control-modal)", "el => el.scrollTo(0, 0)")
    shoot(page, "signals", "dialog.signals-modal:not(.control-modal)", fixed=True)
    page.set_viewport_size({"width": VIEWPORT_NARROW, "height": VIEWPORT_HEIGHT})
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # Das Bedien-Modal (Entwurf "Bedienelemente fuer Lampen", 2026-09-07).
    # Motiv ist die Farbleuchte, nicht irgendeine Kachel: nur sie hat beide
    # Modus-Reiter und die Farbflaeche, und genau diese Abstufung ist der
    # Punkt des Bildes. Die Karte wird ueber ihren Namen gesucht statt ueber
    # eine feste Position - die Kachelreihenfolge haengt an Kategorie-Rang
    # und Raum (siehe DEMO_DEVICES in dev_web_server.py) und darf sich
    # aendern, ohne dieses Bild stillschweigend auf ein anderes Geraet zu
    # verschieben. Der Name steht in einem `<input>`, also ueber `.value`
    # gesucht: ein Textvergleich im Markup ginge daran vorbei.
    lamp = page.evaluate(
        """() => [...document.querySelectorAll('.device-card')]
             .findIndex((c) => c.querySelector('input')?.value === 'Kitchen spots')"""
    )
    if lamp < 0:
        raise SystemExit(
            "Kachel 'Kitchen spots' nicht gefunden - heisst das Demo-Geraet noch so? "
            "(DEMO_DEVICES in scripts/dev_web_server.py)"
        )
    card = page.locator(".device-card").nth(lamp)
    card.get_by_role("button", name="Control").click()
    page.wait_for_selector("dialog.control-modal[open]", timeout=5000)
    # Der Farbe-Reiter, nicht der voreingestellte Weiss-Reiter: die Leuchte
    # steht im Fixture auf ColorMode 2 (Farbtemperatur), das Modal oeffnet
    # deshalb auf "White" - und ein Bild vom Farbwaehler ohne Farbwaehler
    # waere sinnlos. Die Reiterleiste bleibt dabei im Bild und zeigt beide
    # Zustaende.
    page.click("dialog.control-modal .control-tabs button:has-text('Colour')")
    page.wait_for_selector("dialog.control-modal .colour-field", timeout=5000)
    page.wait_for_timeout(300)
    shoot(page, "controls", "dialog.control-modal", fixed=True)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    # Einziger Ausschnitt, der das breite Fenster braucht: die
    # Vorschautabelle ist 895 px breit (acht Spalten) und bekaeme im schmalen
    # Fenster einen waagerechten Bildlauf, also ein Bild mit abgeschnittener
    # letzter Spalte. Danach zurueck auf die schmale Breite.
    page.set_viewport_size({"width": VIEWPORT_WIDE, "height": VIEWPORT_HEIGHT})
    select_view(page, "Export")
    # Review-Fix: ohne Klick zeigte dieser Ausschnitt nur die beiden leeren
    # Karten "Project file sync" und "Export templates" - zwei Drittel Leerraum,
    # keine Vorschautabelle. "View preview" fuellt die dritte Karte darunter
    # mit echten Zeilen, noch bevor irgendetwas heruntergeladen wird.
    page.click('button:has-text("View preview")')
    page.wait_for_selector("table", timeout=5000)
    page.wait_for_timeout(300)
    shoot(page, "export", "card:Project file sync", "card:Preview")
    page.set_viewport_size({"width": VIEWPORT_NARROW, "height": VIEWPORT_HEIGHT})

    # Die "System check"-Karte ganz oben zeigt in dieser Demo (kein echter
    # matter-server-Client, kein echter UDP-Versand) zwei "Error"-Zeilen -
    # zurecht, aber fuer ein Screenshot-Wortmarke schlecht: sieht nach
    # kaputtem Produkt aus, ist aber nur die ehrliche Diagnose eines absichtlich
    # unvollstaendigen Demo-Aufbaus. Der Ausschnitt beginnt deshalb erst bei
    # "Live diagnostics" - was frueher ein Bildlauf mit Ausgleich fuer den
    # klebenden Kopf erledigen musste und jetzt einfach die Bereichsangabe
    # ist. Die Reiterleiste liegt damit ausserhalb; das ist dieselbe Abwaegung
    # wie bei `dashboard.png` und hat denselben Ausgleich.
    select_view(page, "System")
    # `_seed_update_dir()` above seeded a running job (phase "health")
    # before the server even started, so this is already on screen the
    # instant `loadSystem()`'s `loadUpdateStatus()` call resolves;
    # `.steps` only exists once that render happened, so waiting for it
    # replaces a guessed timeout with the actual state Alpine is in.
    page.wait_for_selector(".steps", timeout=5000)
    shoot(page, "update", "card:Version")
    shoot(page, "system", "card:Live diagnostics", "card:Command log")

    select_view(page, "Settings")
    shoot(page, "settings", "card:Miniserver connection", "card:Periodic resend")

    # Sonderfall 1: Einlern-Karte mit Beispielcode, aber NICHT abschicken -
    # ohne echten Matter-Server kaeme beim Absenden nur eine Fehlermeldung.
    # Dieses Bild traegt Kopf- und Reiterleiste fuer die ganze Galerie, hier
    # also ausdruecklich ab dem Seitenanfang.
    select_view(page, "Devices")
    # Der Selektor haengt am `id`, nicht mehr am Platzhaltertext: der lautete
    # frueher "Pairing-Code (11-stellig oder MT:…)" und ist seit dem Entwurf
    # vom 2026-09-07 die Ziffernfolge selbst - `input[placeholder*="MT:"]`
    # fand danach nichts mehr. Ein `id` aendert sich seltener als ein Text,
    # der uebersetzt wird.
    #
    # Eingesetzt wird jetzt der ZAHLENCODE statt eines MT:-Codes: er ist die
    # Bauform, die das Bild erklaeren soll, und nur an ihm sind Gruppierung
    # und Chip ueberhaupt zu sehen. `fill()` loest das `input`-Ereignis aus,
    # an dem `formatCommissionCode` haengt - im Bild steht die Zahl deshalb
    # gruppiert, so wie nach dem Tippen.
    page.fill("#commission-code", "34970112332")
    shoot(
        page,
        "commissioning",
        "header.app-header",
        "card:Commission a new device",
        pad_bottom=0,
    )

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
        # Zaehlerzeile plus der erste aufgeklappte Geraeteblock - genau das,
        # wovon die Bildunterschrift spricht. Der Erklaerungstext ueber den
        # Zaehlern gehoert zu `export.png`.
        shoot(
            page,
            "project-sync",
            ".projectsync-summary",
            "nth:.projectsync-device:0",
            pad_top=0,
        )


def main() -> int:
    _seed_update_dir(UPDATE_DIR)
    server = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "scripts" / "dev_web_server.py"),
            "--demo",
            "--port",
            str(PORT),
            "--update-dir",
            str(UPDATE_DIR),
        ]
    )
    try:
        time.sleep(4)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(
                viewport={"width": VIEWPORT_NARROW, "height": VIEWPORT_HEIGHT},
                device_scale_factor=2,
            )
            capture(page)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
