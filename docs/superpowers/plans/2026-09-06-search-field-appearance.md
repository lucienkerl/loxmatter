# Suchfeld der Geräteansicht — Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das Suchfeld der Geräteansicht bekommt eine eigene Gestalt — Rahmen aus der Palette, Lupe, Treffer-Zähler und eigenes Löschkreuz — statt des browsereigenen Systemkastens.

**Architecture:** Der Rahmen wandert vom `<input>` an einen umschließenden Flex-Container; das Feld darin wird rand- und hintergrundlos, Lupe, Zähler und Kreuz sind seine Geschwister im selben Fluss. Der Fokusring hängt per `:focus-within` am Container und umschließt damit die ganze Gruppe. Die Suchlogik in `app.js` wird nicht angefasst — der Zähler liest nur `visibleDevices().length`.

**Tech Stack:** Statisches HTML mit Alpine.js (vendort unter `web/vendor/`), handgeschriebenes CSS mit Custom Properties, Inline-SVG-Sprite. Tests: pytest + httpx gegen die ASGI-App, geprüft wird das **ausgelieferte** Markup und CSS.

**Entwurf:** [2026-09-06-search-field-appearance-design.md](../specs/2026-09-06-search-field-appearance-design.md)

## Global Constraints

- **Kommentare im Quelltext ohne Umlaute** — `ae`, `oe`, `ue`, `ss`. Nur die Dokumentation unter `docs/` und die deutschen Texte in `strings.yaml` tragen echte Umlaute. (Durchgehend so im ganzen Repo.)
- **Keine Icon-Bibliothek, kein Netzverweis.** Neue Symbole kommen in den Inline-Sprite in `index.html`. Die Oberfläche läuft offline.
- **Keine neue Farbe.** Nur die vorhandenen Variablen `--bg`, `--surface`, `--border`, `--text`, `--text-muted`, `--accent`. Der Entwurf, Abschnitt 2: „Klarheit vor Wirkung".
- **`matchesSearch()`, `visibleDevices()`, `hitsOutsideRoom()` bleiben unangetastet.** Kein Zeichen in diesen drei Funktionen.
- **Tests prüfen Ausgeliefertes, nicht Gerendertes.** In dieser Suite läuft keine Engine, die CSS anwendet oder Alpine ausführt. Assertions gehen gegen `(await client.get("/")).text` und `(await client.get("/static/style.css")).text`.
- **Markup-Assertions laufen über `_without_comments()`** (Helfer oben in `tests/api/test_web.py`). Die Kommentare in `index.html` nennen Attribute beim Namen, teils um zu begründen, warum sie dort *nicht* stehen — eine Suche über die rohe Datei findet auch das.
- **CI-Prüfungen:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest -v`.
- **Neue Tests werden an `tests/api/test_web.py` angehängt** (die Datei ist chronologisch gewachsen, zuletzt Zeile 3447).

---

### Task 1: Die zwei Übersetzungsschlüssel

**Files:**
- Modify: `src/loxmatter/i18n/strings.yaml:815-817` (direkt nach `web.devices.search_show_all_rooms`, vor `web.devices.more_signals_short`)
- Test: `tests/api/test_web.py` (anhängen)

**Interfaces:**
- Consumes: nichts.
- Produces: die Schlüssel `web.devices.search_count` (mit Platzhalter `{count}`) und `web.devices.search_clear`. Task 3 bindet beide über `t()` im Markup.

- [ ] **Step 1: Write the failing test**

An das Ende von `tests/api/test_web.py` anhängen:

```python
# ---------------------------------------------------------------------------
# Suchfeld der Geraeteansicht (Entwurf vom 2026-09-06). Das Feld fiel durch
# das CSS-Raster - die Formularregel listet text, number, password und
# select, aber nicht search -, weshalb der Browser es selbst zeichnete.
# ---------------------------------------------------------------------------


async def test_the_search_field_ships_the_words_for_counter_and_clear_button(api):
    """Der Zaehler traegt Text, das Loeschkreuz traegt keinen und braucht
    deshalb einen zugaenglichen Namen - beide muessen uebersetzt beim
    Browser ankommen.

    `{count}` bleibt dabei UNAUFGELOEST: aufgeloest wird es in app.js
    (`t(key, values)`), wenn die Zahl feststeht. Der Server kennt sie nicht,
    und `GET /api/i18n` liefert deshalb die rohe Vorlage - genau das belegt
    der Vergleich auf die Zeichenkette samt geschweifter Klammern."""
    client, _, _ = api
    strings = (await client.get("/api/i18n")).json()["strings"]
    assert strings["web.devices.search_count"] == "{count} found"
    assert strings["web.devices.search_clear"] == "Clear search"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/api/test_web.py::test_the_search_field_ships_the_words_for_counter_and_clear_button -v
```

Expected: FAIL mit `KeyError: 'web.devices.search_count'`.

- [ ] **Step 3: Write minimal implementation**

In `src/loxmatter/i18n/strings.yaml` direkt nach dem Block `web.devices.search_show_all_rooms` (Zeile 815–817) einfügen:

```yaml
# Ohne Plural-Sonderfall: "1 found" und "1 Treffer" lesen sich beide
# richtig, und die Tabelle kennt an keiner Stelle eine Pluralform.
web.devices.search_count:
  en: "{count} found"
  de: "{count} Treffer"
# Das Loeschkreuz traegt kein Wort, also braucht es eines - der Schluessel
# beschriftet `title` UND `aria-label`.
web.devices.search_clear:
  en: "Clear search"
  de: "Suche leeren"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/api/test_web.py::test_the_search_field_ships_the_words_for_counter_and_clear_button -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "feat(i18n): Woerter fuer Treffer-Zaehler und Loeschkreuz der Suche"
```

---

### Task 2: Das Lupen-Symbol

**Files:**
- Modify: `src/loxmatter/web/index.html:149-153` (im Inline-Sprite, zwischen `#i-rename` und `#i-close`)
- Test: `tests/api/test_web.py` (anhängen)

**Interfaces:**
- Consumes: nichts.
- Produces: `#i-search` im Sprite. Task 3 verweist mit `<use href="#i-search">` darauf.

- [ ] **Step 1: Write the failing test**

An das Ende von `tests/api/test_web.py` anhängen:

```python
async def test_the_search_field_has_a_magnifier_of_its_own(api):
    """Die Lupe kommt aus dem Inline-Sprite wie jedes andere Symbol -
    dieselbe Begruendung wie beim eingecheckten vendor/alpine.min.js: die
    Oberflaeche laeuft offline.

    Das Loeschkreuz bekommt dagegen KEIN eigenes Symbol, es benutzt das
    vorhandene `#i-close`. Zwei gleiche Formen waeren zwei Orte, an die man
    sich beim naechsten Strichstaerken-Dreh erinnern muss - und an einen
    davon erinnert man sich nicht."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    assert 'id="i-search"' in page
    assert page.count('id="i-close"') == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/api/test_web.py::test_the_search_field_has_a_magnifier_of_its_own -v
```

Expected: FAIL bei `assert 'id="i-search"' in page`.

- [ ] **Step 3: Write minimal implementation**

In `src/loxmatter/web/index.html` direkt vor `<symbol id="i-close" ...>` einfügen:

```html
      <!-- Lupe fuer das Suchfeld der Raumleiste (Entwurf Suchfeld-Optik,
           2026-09-06). Gleiche Strichtechnik wie die Kategorie-Icons: nur
           Pfade, keine Fuellung, `currentColor` uebernimmt Farbe und
           Zustand von aussen. -->
      <symbol id="i-search" viewBox="0 0 24 24">
        <circle cx="11" cy="11" r="6.5" />
        <path d="M15.8 15.8 20.5 20.5" />
      </symbol>
```

- [ ] **Step 4: Run test to verify it passes**

Der neue Test **und** die vorhandene XML-Prüfung, die das Symbol automatisch mit abdeckt:

```bash
uv run pytest tests/api/test_web.py::test_the_search_field_has_a_magnifier_of_its_own tests/api/test_web.py::test_the_inline_icon_symbols_are_well_formed_xml -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/loxmatter/web/index.html tests/api/test_web.py
git commit -m "feat(web): Lupensymbol fuer das Suchfeld in den Sprite"
```

---

### Task 3: Das Suchfeld

Markup und CSS in einem Zug: das Markup allein wäre ein unbrauchbarer
Zwischenstand — vier lose Elemente ohne Rahmen.

**Files:**
- Modify: `src/loxmatter/web/index.html:358-363` (das nackte `<input type="search">` ersetzen)
- Modify: `src/loxmatter/web/style.css:1099-1102` (die Regel `.device-search` ersetzen)
- Test: `tests/api/test_web.py` (anhängen)

**Interfaces:**
- Consumes: `#i-search` (Task 2), `#i-close` (vorhanden), `web.devices.search_count` und `web.devices.search_clear` (Task 1), `web.devices.search_placeholder` (vorhanden), `deviceSearch` und `visibleDevices()` aus `app.js` (vorhanden, unverändert).
- Produces: den Container `.search-field` mit den Kindern `.search-icon`, `input[type="search"]`, `.search-count`, `.search-clear`. Task 4 hängt seine Breitenregel an `.search-field`.

- [ ] **Step 1: Write the failing tests**

Alle vier an das Ende von `tests/api/test_web.py` anhängen:

```python
async def test_the_search_field_carries_the_frame_and_the_input_does_not(api):
    """Der Rahmen sitzt am Container, nicht am Feld.

    Traegen beide einen, liegt ein Rahmen im anderen - und der Fokusring
    (naechster Test) haette nichts, woran er sich festmachen koennte, das
    Lupe und Kreuz mit einschliesst."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    field = css.split(".search-field {", 1)[1].split("}", 1)[0]
    inner = css.split('.search-field input[type="search"] {', 1)[1].split("}", 1)[0]
    assert "border: 1px solid var(--border)" in field
    assert "border-radius" in field
    assert "border: none" in inner
    assert "background: none" in inner


async def test_the_search_focus_ring_wraps_the_whole_group(api):
    """`:focus-within` am Container statt `:focus` am Feld: der Ring soll
    Lupe, Zaehler und Kreuz mit einschliessen, nicht nur das Eingabefeld in
    ihrer Mitte. Der browsereigene Umriss am Feld muss dafuer weichen, sonst
    stuenden beide."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    ring = css.split(".search-field:focus-within {", 1)[1].split("}", 1)[0]
    assert "var(--accent)" in ring
    inner_focus = css.split('.search-field input[type="search"]:focus {', 1)[1].split("}", 1)[0]
    assert "outline: none" in inner_focus


async def test_the_browser_does_not_add_a_second_clear_cross(api):
    """WebKit legt in ein `input[type="search"]` sein eigenes Loeschkreuz -
    daneben stuende unseres ein zweites Mal.

    Abgeschaltet wird es mit `-webkit-appearance` UND `appearance`: das
    Pseudoelement ist herstellerspezifisch, und die Zusicherung auf die
    zweite Form braucht den Zeilenanfang - `"appearance: none"` ist eine
    Teilzeichenkette von `"-webkit-appearance: none"` und waere sonst schon
    von der ersten Zeile erfuellt."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    rule = css.split("::-webkit-search-cancel-button {", 1)[1].split("}", 1)[0]
    assert "-webkit-appearance: none" in rule
    assert re.search(r"^\s*appearance: none", rule, re.MULTILINE)


async def test_the_counter_and_the_cross_appear_only_with_a_query(api):
    """Beide haengen an `deviceSearch` und tragen `x-cloak`: bei leerem Feld
    sind sie weg, und beim ersten Zeichnen blitzen sie nicht auf, bevor
    Alpine initialisiert hat.

    Der Zaehler liest `visibleDevices().length` - also das, was tatsaechlich
    unter der Leiste steht, einschliesslich eines aktiven Raumfilters. Die
    Suchlogik selbst bleibt unberuehrt.

    Zwei `aria-label`: eines am Eingabefeld (es traegt nur einen Platzhalter,
    und der verschwindet genau dann, wenn jemand etwas eingegeben hat), eines
    am Kreuz (es traegt gar kein Wort)."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    field = page.split('<div class="search-field">', 1)[1].split("</div>", 1)[0]
    assert field.count('x-show="deviceSearch.trim()"') == 2
    assert field.count("x-cloak") == 2
    assert field.count("aria-label") == 2
    assert "visibleDevices().length" in field
    assert "deviceSearch = ''" in field
    assert "t('web.devices.search_clear')" in field
    assert 'href="#i-search"' in field
    assert 'href="#i-close"' in field
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/api/test_web.py -k "search_field_carries or focus_ring_wraps or second_clear_cross or counter_and_the_cross" -v
```

Expected: 4 failed — die drei CSS-Tests mit `IndexError: list index out of range` (der Split findet die Regel nicht), der Markup-Test ebenso.

- [ ] **Step 3: Write the markup**

In `src/loxmatter/web/index.html` das nackte Eingabefeld (Zeile 358–363, beginnend bei `<input` und endend bei `/>`) ersetzen durch:

```html
          <!-- Der Rahmen sitzt am Container, nicht am Feld: Lupe, Zaehler
               und Kreuz liegen mit IM Rahmen, und der Fokusring haengt per
               :focus-within an der ganzen Gruppe statt am Eingabefeld in
               ihrer Mitte.

               Ein <div>, kein <label>: ein <button> innerhalb eines Labels
               loest dessen Weiterleitung an das gelabelte Bedienelement mit
               aus - das Loeschen soll ein Klick sein, nicht zwei
               Ereignisse. Der Preis ist ein schmaler Streifen Polster, der
               nicht ins Feld fokussiert; die Lupe traegt dafuer
               `pointer-events: none` (style.css), damit wenigstens sie kein
               Klickloch in die linke Kante schlaegt. -->
          <div class="search-field">
            <svg class="icon search-icon" aria-hidden="true"><use href="#i-search"></use></svg>
            <input
              type="search"
              x-model="deviceSearch"
              :placeholder="t('web.devices.search_placeholder')"
              :aria-label="t('web.devices.search_placeholder')"
            />
            <span
              class="search-count"
              x-show="deviceSearch.trim()"
              x-cloak
              x-text="t('web.devices.search_count', { count: visibleDevices().length })"
            ></span>
            <button
              class="search-clear"
              x-show="deviceSearch.trim()"
              x-cloak
              @click="deviceSearch = ''"
              :title="t('web.devices.search_clear')"
              :aria-label="t('web.devices.search_clear')"
            ><svg class="icon" aria-hidden="true"><use href="#i-close"></use></svg></button>
          </div>
```

- [ ] **Step 4: Write the CSS**

In `src/loxmatter/web/style.css` die Regel `.device-search` (Zeile 1099–1102) **vollständig ersetzen** durch:

```css
/* Suchfeld der Raumleiste (Entwurf Suchfeld-Optik, 2026-09-06). Vorher war
 * das ein nacktes `input[type="search"]`: die Formularregel weiter oben in
 * dieser Datei listet text, number, password und select - `search` steht
 * nicht darunter, und der Browser zeichnete das Feld deshalb selbst. Im
 * Dunkelmodus griff dabei keine der Projektfarben.
 *
 * Der Rahmen sitzt HIER, am Container - das Eingabefeld darin ist rand- und
 * hintergrundlos (s.u.). So liegen Lupe, Zaehler und Kreuz mit im Rahmen,
 * ohne dass eines davon absolut positioniert werden muesste: jedes Polster,
 * das zur Icongroesse passen muss, waere eine Kopplung, die beim naechsten
 * Schriftgroessen-Dreh bricht. */
.search-field {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 0.15rem 0.4rem;
  /* Waechst in den freien Rest der Zeile, gedeckelt bei 22rem. Wer den
   * Deckel hebt, bekommt auf einem breiten Fenster ein Suchfeld ueber vier
   * Kacheln - schlechter als der einsame Kasten, den es zu beseitigen
   * gilt. */
  flex: 1 1 12rem;
  max-width: 22rem;
}

/* Der Ring gehoert an die Gruppe, nicht an ihre Mitte: am `input:focus`
 * umschloesse er nur das Eingabefeld und liesse Lupe und Kreuz draussen. */
.search-field:focus-within {
  border-color: var(--accent);
}

/* Ohne `pointer-events: none` faengt die Lupe Klicks ab, die dem Feld
 * gelten - sie sieht aus wie Teil des Feldes und soll sich auch so
 * verhalten. */
.search-icon {
  color: var(--text-muted);
  pointer-events: none;
}

.search-field input[type="search"] {
  appearance: none;
  -webkit-appearance: none;
  flex: 1 1 auto;
  min-width: 0;
  border: none;
  background: none;
  color: var(--text);
  font-size: 0.85rem;
  padding: 0.2rem 0;
}

.search-field input[type="search"]:focus {
  outline: none;
}

/* WebKit legt sein eigenes Loeschkreuz ins Feld - daneben stuende unseres
 * ein zweites Mal. Beide Schreibweisen, weil das Pseudoelement selbst
 * herstellerspezifisch ist. */
.search-field input[type="search"]::-webkit-search-cancel-button {
  -webkit-appearance: none;
  appearance: none;
}

/* `tabular-nums`: ohne das wechselt die Ziffernbreite von 9 auf 10 und
 * schiebt das Kreuz daneben mit - dasselbe Zappeln, das die Altersangabe an
 * der Signalzeile schon einmal gekostet hat (siehe `.value-fresh`). */
.search-count {
  font-size: 0.72rem;
  color: var(--text-muted);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

/* Knopf ohne Knopfoptik - der Rahmen der Gruppe steht schon. `line-height:
 * 0`, damit die Zeilenhoehe des leeren Knopftextes das Icon nicht nach
 * unten aus der Achse schiebt. */
.search-clear {
  border: none;
  background: none;
  color: var(--text-muted);
  padding: 0.1rem;
  line-height: 0;
}

.search-clear:hover {
  color: var(--text);
}
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/api/test_web.py -k "search_field_carries or focus_ring_wraps or second_clear_cross or counter_and_the_cross" -v
```

Expected: 4 passed.

- [ ] **Step 6: Verify nothing referenced the old class**

```bash
grep -rn "device-search" src tests scripts
```

Expected: keine Ausgabe. (`.device-search` stand nur an den zwei ersetzten Stellen.)

- [ ] **Step 7: Run the whole suite**

```bash
uv run pytest -q
```

Expected: alles grün, keine neuen Fehlschläge.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "feat(web): Suchfeld mit Lupe, Treffer-Zaehler und eigenem Loeschkreuz"
```

---

### Task 4: Breite und Lage ohne Räume

**Files:**
- Modify: `src/loxmatter/web/index.html:357` (der fest im Markup stehende Abstandhalter)
- Modify: `src/loxmatter/web/style.css` (Regel `.room-spacer` neben `.room-chips` ergänzen, Zeile ~1064)
- Test: `tests/api/test_web.py` (anhängen)

**Interfaces:**
- Consumes: `.search-field` (Task 3), `hasAnyRoom()` aus `app.js` (vorhanden, unverändert).
- Produces: nichts, worauf ein späterer Task aufbaut.

- [ ] **Step 1: Write the failing test**

An das Ende von `tests/api/test_web.py` anhängen:

```python
async def test_the_search_field_moves_left_when_there_are_no_rooms(api):
    """Der Abstandhalter, der das Feld nach rechts schiebt, existiert nur
    zusammen mit den Chips, an denen vorbeizuschieben waere.

    Ohne Raeume blendet sich die Chip-Leiste aus (`x-if="hasAnyRoom()"`).
    Stuende der Abstandhalter dann weiter im Markup - so war es -, bliebe
    ein einzelner Kasten rechts in einer sonst leeren Zeile stehen. Mit
    eigenem `x-if` verschwindet er mit den Chips, und das Feld rueckt an die
    linke Kante, auf eine Sichtachse mit dem Kachelraster darunter.

    Zwei `x-if="hasAnyRoom()"` in der Leiste sind also richtig und kein
    Versehen: eines fuer die Chips, eines fuer den Abstandhalter."""
    client, _, _ = api
    page = _without_comments((await client.get("/")).text)
    bar = page.split('<div class="room-bar"', 1)[1].split('<div class="search-field">', 1)[0]
    assert 'style="flex: 1 1 auto"' not in bar
    assert bar.count('x-if="hasAnyRoom()"') == 2
    assert '<span class="room-spacer"></span>' in bar
    css = (await client.get("/static/style.css")).text
    assert "flex: 1 1 auto" in css.split(".room-spacer {", 1)[1].split("}", 1)[0]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/api/test_web.py::test_the_search_field_moves_left_when_there_are_no_rooms -v
```

Expected: FAIL bei `assert 'style="flex: 1 1 auto"' not in bar`.

- [ ] **Step 3: Write the markup**

In `src/loxmatter/web/index.html` die Zeile

```html
          <span style="flex: 1 1 auto"></span>
```

ersetzen durch:

```html
          <!-- Der Abstandhalter existiert nur zusammen mit den Chips: er
               schiebt das Suchfeld an ihnen vorbei nach rechts. Ohne
               Raeume gibt es nichts, woran vorbeizuschieben waere - dann
               soll das Feld an die linke Kante ruecken statt allein rechts
               in einer leeren Zeile zu stehen. Deshalb ein eigenes `x-if`
               und keine Zeile, die immer da ist. -->
          <template x-if="hasAnyRoom()"><span class="room-spacer"></span></template>
```

- [ ] **Step 4: Write the CSS**

In `src/loxmatter/web/style.css` direkt nach der Regel `.room-chips` (endet auf Zeile ~1069) einfügen:

```css
/* Schluckt den freien Rest der Zeile und schiebt das Suchfeld damit nach
 * rechts an den Chips vorbei. Steht als eigene Klasse statt als
 * `style`-Attribut im Markup, weil das Element inzwischen unter einem
 * `x-if` haengt - dort gehoert die Begruendung ins Markup, das Mass ins
 * Stylesheet. */
.room-spacer {
  flex: 1 1 auto;
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/api/test_web.py::test_the_search_field_moves_left_when_there_are_no_rooms -v
```

Expected: PASS.

- [ ] **Step 6: Run the whole suite and the CI checks**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Expected: alles grün.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "fix(web): Abstandhalter der Raumleiste haengt an den Chips, nicht an der Zeile"
```

---

### Task 5: Den Screenshot nachziehen

`docs/screenshots/dashboard.png` ist an `.room-bar` verankert
(`scripts/capture_screenshots.py:208`) und zeigt damit genau den
Systemkasten, den die Tasks 1–4 beseitigt haben.

**Files:**
- Modify: `docs/screenshots/dashboard.png` (neu aufgenommen)

**Interfaces:**
- Consumes: die fertige Oberfläche aus Task 4.
- Produces: nichts.

- [ ] **Step 1: Playwright bereitstellen**

```bash
uv run --with playwright python -m playwright install chromium
```

Expected: Chromium ist installiert oder war es schon („is already installed").

- [ ] **Step 2: Aufnehmen**

```bash
uv run --with playwright python scripts/capture_screenshots.py
```

Das Skript startet `dev_web_server.py --demo` selbst, meldet sich an und
legt alle sieben Bilder ab.

- [ ] **Step 3: Prüfen, welche Bilder sich wirklich geändert haben**

```bash
git status --short docs/screenshots/
```

Expected: `dashboard.png` geändert, `system.png` geändert, die übrigen fünf
unverändert.

`system.png` ist **nicht** reproduzierbar — sein Kommando-Log zeigt die
HTTP-Anfragen des Aufnahmelaufs selbst, auf die Mikrosekunde genau (siehe
Kopfkommentar des Skripts). Es wird verworfen:

```bash
git checkout -- docs/screenshots/system.png
```

Meldet `git status` darüber hinaus eines der anderen fünf Bilder als
geändert, ist das **kein** Rauschen, sondern eine echte, ungeplante
Änderung an der Oberfläche — dann anhalten und nachsehen, statt sie
mitzucommitten.

- [ ] **Step 4: Das neue Bild ansehen**

`docs/screenshots/dashboard.png` öffnen und gegen den Entwurf halten:
Rahmen in Palettenfarbe statt Systemkasten, Lupe links, rechts das Kreuz
(der Zähler erscheint nur bei einer Eingabe, das Demo-Bild zeigt ihn also
nicht), Feld deutlich breiter als zuvor und rechtsbündig neben den
Raum-Chips.

- [ ] **Step 5: Commit**

```bash
git add docs/screenshots/dashboard.png
git commit -m "docs(screenshots): Dashboard mit dem neuen Suchfeld"
```

---

## Abschluss

Nach Task 5 ist der Entwurf vollständig umgesetzt. Zum Zusammenführen des
Zweigs: `superpowers:finishing-a-development-branch`.
