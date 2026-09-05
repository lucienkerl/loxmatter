# Kachel-Kebab-Menü — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Fußzeile der Gerätekachel wird zu einer Zeile — Export-Hinweis links, ein ⋮ rechts —, hinter dem Raumzuweisung, Exportieren und Entfernen liegen.

**Architecture:** Das Menü ist ein natives `<details>` mit dem ⋮ als `<summary>`; der Auf-/Zu-Zustand lebt damit im DOM statt in Alpine. Genau dadurch entfällt die Kopplung „JavaScript-Modus-Zustand steuert ein natives Bedienelement", die dem Raum-Auswahlfeld sechs Review-Runden gekostet hat — `roomSelectDrafts`, `syncRoomSelectDraft`, `onRoomSelectChange` und der `focusout`-Wächter verschwinden ersatzlos.

**Tech Stack:** Alpine.js 3.17.1 (vendored unter `src/loxmatter/web/vendor/`, kein Build-Schritt), natives `<details>`/`<summary>`, pytest über die ausgelieferten Dateien.

**Spec:** `docs/superpowers/specs/2026-09-05-kachel-kebab-menue-design.md` — bei jedem Zweifel gilt die Spec, nicht dieser Plan.

## Global Constraints

- **Entwickler-Prosa auf Deutsch.** Kommentare, Docstrings und Commit-Nachrichten in dichtem, begründendem Deutsch, das das *Warum* nennt. Ausnahme: der GPL-Kopf jeder Quelldatei bleibt im englischen FSF-Wortlaut.
- **Jeder nutzersichtbare Text läuft über `t()`** mit `en`- **und** `de`-Eintrag in `src/loxmatter/i18n/strings.yaml`. Schlüssel flach und punktiert. Ein Knopf, der nur ein Icon trägt, braucht einen `:title` aus `t()`.
- **Keine externe Frontend-Abhängigkeit.** Icons sind inline-SVG-`<symbol>`s im bestehenden Block in `index.html`. Keine Icon-Bibliothek, kein CDN, kein Alpine-Plugin.
- **Der Literal `localStorage` darf in keiner ausgelieferten Datei vorkommen** — ein bestehender Sicherheitstest verbietet ihn.
- **Kommandos laufen mit `uv`**: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`. Alle vier müssen grün sein.
- **Die WebUI-Tests belegen nur, DASS etwas ausgeliefert wird**, nie dass es tut, was es soll. Verhalten wird im Browser gegen das **vendorte** Alpine geprüft (Task 4), nicht durch Lesen.
- Commit-Nachrichten enden mit dem Trailer `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

## File Structure

**Geändert:**
- `src/loxmatter/web/index.html` — ein neues `<symbol id="i-kebab">`; die Fußzeile der Gerätekachel (`.device-foot`, aktuell Zeilen 527-618) wird ersetzt.
- `src/loxmatter/web/style.css` — neue Regeln für `.tile-menu`; die Regeln `.room-select`, `.room-new`, `.room-picker` entfallen.
- `src/loxmatter/web/app.js` — `roomSelectDrafts`, `syncRoomSelectDraft`, `onRoomSelectChange` und ihre Aufrufer entfallen; `newRoomFor` bleibt, in seiner ursprünglichen Rolle.
- `src/loxmatter/i18n/strings.yaml` — zwei neue Schlüssel.
- `tests/api/test_web.py` — vier Tests entfallen, einer wird angepasst, drei kommen dazu.
- `scripts/capture_screenshots.py` — nur, falls ein Selektor bricht (Task 4).

**Nicht angefasst:** API, Store, `profiles/categories.py`. Dieser Plan ändert ausschließlich die Oberfläche.

---

### Task 1: Kebab-Icon und Übersetzungsschlüssel

**Files:**
- Modify: `src/loxmatter/web/index.html` (Symbolblock, nach `<symbol id="i-remove">` bei Zeile 151)
- Modify: `src/loxmatter/i18n/strings.yaml` (`web.devices.*`-Block)
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: nichts.
- Produces: das Symbol `#i-kebab` und die Schlüssel `web.devices.menu`, `web.devices.menu_room_heading`, die Task 2 und 3 verwenden.

- [ ] **Step 1: Write the failing test**

An `tests/api/test_web.py` anhängen. Die `api`-Fixture dieser Datei ist ein 3-Tupel `(client, store, device_id)`; die Seite wird über `/` geholt, `app.js`/`style.css` über `/static/…` — an den Nachbartests in derselben Datei ablesen und genauso schreiben.

```python
async def test_the_tile_menu_has_its_own_icon_symbol(api):
    """Das Kebab-Symbol wird wie alle anderen inline ausgeliefert - keine
    Icon-Bibliothek, kein CDN, weil die Oberflaeche offline laeuft. Ein
    `<use>` auf eine fehlende ID zeichnet stillschweigend nichts, deshalb
    faellt ein vergessenes Symbol hier auf und nicht erst im Browser."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    assert 'id="i-kebab"' in page
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web.py -k tile_menu_has_its_own_icon -v`
Expected: FAIL — `assert 'id="i-kebab"' in page`.

- [ ] **Step 3: Symbol ergänzen**

In `src/loxmatter/web/index.html`, im bestehenden Inline-SVG-Block direkt nach `<symbol id="i-remove">`:

```html
      <!-- Drei gefuellte Punkte statt Striche: `.icon` setzt
           `fill: none; stroke: currentColor`, was fuer Linien-Icons stimmt,
           einen Punkt aber unsichtbar machen wuerde. Dieselbe Ausnahme
           macht schon `#i-offline` fuer seinen Punkt. -->
      <symbol id="i-kebab" viewBox="0 0 24 24">
        <circle cx="12" cy="5" r="1.7" fill="currentColor" stroke="none" />
        <circle cx="12" cy="12" r="1.7" fill="currentColor" stroke="none" />
        <circle cx="12" cy="19" r="1.7" fill="currentColor" stroke="none" />
      </symbol>
```

- [ ] **Step 4: Übersetzungsschlüssel ergänzen**

In `src/loxmatter/i18n/strings.yaml`, ans Ende des `web.devices.*`-Blocks:

```yaml
# --- Kachel-Menue (Entwurf Kebab-Menue, 2026-09-05) ---
# `menu` ist der zugaengliche Name des Kebab-Knopfs: er traegt kein Wort,
# also braucht er einen. `menu_room_heading` beschriftet den Raum-Abschnitt
# im Menue, damit die Liste der Raumnamen nicht ohne Zusammenhang ueber
# "Exportieren" und "Entfernen" steht.
web.devices.menu:
  en: "Actions"
  de: "Aktionen"
web.devices.menu_room_heading:
  en: "Room"
  de: "Raum"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_web.py tests/test_i18n.py tests/api/test_language.py -q`
Expected: PASS. `test_language.py` prüft, dass jeder `web.*`-Schlüssel über `GET /api/i18n` auflöst; `test_i18n.py` prüft die `en`/`de`-Vollständigkeit.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): Kebab-Symbol und Schluessel fuer das Kachel-Menue

Drei gefuellte Punkte statt Striche - `.icon` setzt `fill: none`, was
fuer Linien-Icons richtig ist, einen Punkt aber unsichtbar machte;
dieselbe Ausnahme macht `#i-offline` fuer seinen Punkt bereits.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Das Menü mit Exportieren und Entfernen

**Files:**
- Modify: `src/loxmatter/web/index.html` (`.device-foot`, Zeilen 527-618)
- Modify: `src/loxmatter/web/style.css` (neue Regeln nach `.device-foot`, Zeile 1106)
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `#i-kebab`, `web.devices.menu` (Task 1); die bestehenden Methoden `exportDevice(device)`, `removeDevice(device)`, `exportHintFor(deviceId)` und das Zustandsfeld `bridgeSettings`.
- Produces: das Markup-Gerüst `<details class="tile-menu">` mit `.tile-menu-items`, in das Task 3 den Raum-Abschnitt einhängt.

**Am Ende dieser Aufgabe ist die Oberfläche vollständig benutzbar:** das Raum-Auswahlfeld steht noch an seinem Platz, Exportieren und Entfernen sind ins Menü gewandert. Das Auswahlfeld verschwindet erst in Task 3.

- [ ] **Step 1: Write the failing test**

An `tests/api/test_web.py` anhängen:

```python
async def test_the_tile_menu_is_a_native_details_that_closes_three_ways(api):
    """`<details>` haelt den Auf-/Zu-Zustand im DOM - der Grund, warum das
    Menue ueberhaupt so gebaut ist (siehe Entwurf, Abschnitt 4). Zwei der
    drei Schliesswege muessen dennoch von Hand kommen: `<details>` schliesst
    weder bei einem Klick daneben noch bei Escape von selbst. Der dritte,
    der Klick auf einen Eintrag, steht an den Eintraegen."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    assert 'class="tile-menu"' in page
    assert "@click.outside" in page
    assert "@keydown.escape" in page


async def test_export_and_remove_moved_into_the_tile_menu(api):
    """Beide Aktionen liegen jetzt im Menue und schliessen es beim Klick.
    Der Export-Knopf bleibt an `bridgeSettings.bridge_ip` gebunden - ohne
    hinterlegte Bruecken-IP gibt es nichts zu exportieren."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    menu = page.split('class="tile-menu"', 1)[1].split("</details>", 1)[0]
    assert "exportDevice(device)" in menu
    assert "removeDevice(device)" in menu
    assert "!bridgeSettings.bridge_ip" in menu
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web.py -k "tile_menu_is_a_native_details or export_and_remove_moved" -v`
Expected: FAIL — `assert 'class="tile-menu"' in page`.

- [ ] **Step 3: Fußzeile umbauen**

In `src/loxmatter/web/index.html` den Inhalt von `<div class="device-foot">` ersetzen. Der `.room-picker`-Block (Auswahlfeld und Neu-Raum-Textfeld) samt seinem großen Kommentar **bleibt vorerst unverändert stehen** — er entfällt in Task 3. Ersetzt werden nur die beiden Icon-Tasten am Ende, und der Abstandhalter davor:

```html
                    <span class="hint" x-text="exportHintFor(device.id)"></span>
                    <span style="flex: 1 1 auto"></span>
                    <!-- Der Auf-/Zu-Zustand liegt im DOM, nicht in Alpine:
                         ein `<details>` braucht kein `open`-Feld je Kachel,
                         das mit dem sichtbaren Zustand in Deckung gehalten
                         werden muesste. Genau diese Deckungspflicht hat das
                         Raum-Auswahlfeld sechs Reviewrunden gekostet.
                         `<details>` schliesst allerdings NICHT von selbst
                         bei Escape (anders als ein `<dialog>`) und auch
                         nicht bei einem Klick daneben - beides steht
                         deshalb hier. Dass immer nur ein Menue offen ist,
                         faellt dabei ab: der Klick auf den Kebab einer
                         anderen Kachel liegt ausserhalb dieses `<details>`
                         und schliesst es ueber denselben Wachposten. -->
                    <details
                      class="tile-menu"
                      @click.outside="$el.open = false"
                      @keydown.escape="$el.open = false"
                    >
                      <summary :title="t('web.devices.menu')">
                        <svg class="icon"><use href="#i-kebab"></use></svg>
                      </summary>
                      <div class="tile-menu-items">
                        <button
                          class="tile-menu-item"
                          @click="$el.closest('details').open = false; exportDevice(device)"
                          :disabled="!bridgeSettings.bridge_ip"
                          x-text="t('web.devices.export')"
                        ></button>
                        <button
                          class="tile-menu-item is-danger"
                          @click="$el.closest('details').open = false; removeDevice(device)"
                          x-text="t('web.devices.remove')"
                        ></button>
                      </div>
                    </details>
```

Der Hinweisabsatz unter der Fußzeile (`x-show="!bridgeSettings.bridge_ip"`, mit dem Verweis auf die Einstellungen) bleibt unverändert stehen — er gilt für alle Kacheln, nicht für diese eine, und gehört deshalb nicht ins Menü.

- [ ] **Step 4: CSS ergänzen**

Ans Ende von `src/loxmatter/web/style.css`:

```css
/* Kachel-Menue (Entwurf Kebab-Menue, 2026-09-05).
 *
 * `position: relative` am `<details>`, `absolute` an der Liste: das Menue
 * darf die Kachel ueberragen, ohne die Fusszeile hoeher zu machen. Es
 * oeffnet nach OBEN (`bottom: 100%`), weil die Fusszeile am unteren Rand
 * der Kachel sitzt - nach unten wuerde es die naechste Kachelreihe
 * verdecken statt freien Platz zu nutzen. */
.tile-menu {
  position: relative;
  flex: none;
}

/* Der Standard-Marker eines `<summary>` (Dreieck bzw. Disclosure-Pfeil)
 * muss zweifach abgeschaltet werden: `list-style` greift in Firefox und
 * Chrome, das `::-webkit-details-marker`-Pseudoelement in aelteren
 * WebKit-Fassungen. */
.tile-menu > summary {
  list-style: none;
  cursor: pointer;
  border: 1px solid var(--border);
  border-radius: 5px;
  background: var(--surface);
  color: var(--text-muted);
  padding: 0.15rem 0.4rem;
  line-height: 1;
  display: inline-flex;
  align-items: center;
}

.tile-menu > summary::-webkit-details-marker {
  display: none;
}

.tile-menu[open] > summary {
  border-color: var(--accent);
  color: var(--accent);
}

.tile-menu-items {
  position: absolute;
  right: 0;
  bottom: 100%;
  margin-bottom: 0.25rem;
  z-index: 5;
  min-width: 11rem;
  max-height: 60vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 1px;
  padding: 0.25rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  box-shadow: 0 6px 20px rgb(0 0 0 / 18%);
}

.tile-menu-item {
  border: none;
  background: none;
  text-align: left;
  padding: 0.3rem 0.5rem;
  border-radius: 4px;
  font-size: 0.8rem;
  white-space: nowrap;
}

.tile-menu-item:hover:not(:disabled) {
  background: var(--bg);
}

.tile-menu-item.is-danger {
  color: var(--danger);
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/test_web.py -q && uv run ruff format --check .`
Expected: PASS. Bestehende Tests, die die alten Icon-Tasten in der Fußzeile prüfen, schlagen hier an — sie gehören auf das neue Markup angepasst, nicht das Markup auf sie zurückgedreht.

- [ ] **Step 6: Commit**

```bash
git add src/loxmatter/web/index.html src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): Exportieren und Entfernen in ein Kebab-Menue verlegen

Ein natives `<details>`: der Auf-/Zu-Zustand liegt im DOM, nicht in
Alpine, es gibt also kein `open`-Feld je Kachel, das mit dem sichtbaren
Zustand in Deckung gehalten werden muesste.

Zwei Schliesswege muessen dennoch von Hand kommen - `<details>`
schliesst weder bei Escape noch bei einem Klick daneben von selbst.
Dass immer nur ein Menue offen ist, faellt dabei ab.

Das Raum-Auswahlfeld steht noch an seinem Platz; es entfaellt im
naechsten Schritt.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Räume ins Menü, Auswahlfeld ersatzlos entfernen

**Files:**
- Modify: `src/loxmatter/web/index.html` (`.room-picker`-Block in `.device-foot` entfällt; Raum-Abschnitt kommt in `.tile-menu-items`)
- Modify: `src/loxmatter/web/style.css` (`.room-select`, `.room-new`, `.room-picker` entfallen; zwei Regeln kommen dazu)
- Modify: `src/loxmatter/web/app.js`
- Test: `tests/api/test_web.py`

**Interfaces:**
- Consumes: `.tile-menu-items` (Task 2), `web.devices.menu_room_heading` (Task 1); die bleibenden Methoden `roomKeyOf(device)`, `roomChips()`, `saveRoom(device, value)`, `beginNewRoom(device)`, `commitNewRoom(device)`, `reconcileRoomFilter()`.
- Produces: keine für spätere Aufgaben.

- [ ] **Step 1: Write the failing test**

An `tests/api/test_web.py` anhängen:

```python
async def test_the_rooms_are_menu_entries_and_the_select_is_gone(api):
    """Die Raumzuweisung ist jetzt eine Liste von Eintraegen im Menue. Das
    `<select>` und die gesamte Mechanik, die noetig war, um seinen
    angezeigten Wert mit `device.room` in Deckung zu halten, entfaellt
    ersatzlos - genau darum geht es bei diesem Umbau."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text

    assert "menu_room_heading" in page
    assert "saveRoom(device, chip.key)" in page
    assert "beginNewRoom(device)" in page

    assert "room-select" not in page
    assert "room-picker" not in page
    for gone in ("roomSelectDrafts", "syncRoomSelectDraft", "onRoomSelectChange"):
        assert gone not in script, gone


async def test_the_current_room_is_marked_for_assistive_tech_too(api):
    """Das Haekchen am aktuellen Raum ist rein grafisch. `aria-current`
    traegt dieselbe Auskunft fuer alles, was die Seite nicht sieht - ohne
    das waere der aktuelle Raum im Menue nur eine von mehreren gleich
    aussehenden Zeilen."""
    client, _store, _device_id = api
    page = (await client.get("/")).text
    assert "aria-current" in page
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/api/test_web.py -k "rooms_are_menu_entries or current_room_is_marked" -v`
Expected: FAIL — `assert "menu_room_heading" in page` bzw. `assert "room-select" not in page`.

- [ ] **Step 3: Den `.room-picker`-Block aus der Fußzeile entfernen**

In `src/loxmatter/web/index.html` das gesamte `<span class="room-picker">…</span>` samt dem davorstehenden mehrzeiligen Kommentar ersatzlos löschen — der Kommentar beginnt mit „Fund 1 (Review vom 2026-09-05" und endet unmittelbar vor dem `<span>`. **Nicht nach Zeilennummern suchen:** Task 2 hat die Fußzeile bereits umgebaut und alles darunter verschoben. Die Fußzeile besteht danach aus dem Hinweis, dem Abstandhalter und dem `<details>` aus Task 2.

- [ ] **Step 4: Raum-Abschnitt ins Menü einhängen**

In `src/loxmatter/web/index.html`, als erste Kinder von `<div class="tile-menu-items">`, **vor** den beiden Knöpfen aus Task 2:

```html
                        <p class="tile-menu-heading" x-text="t('web.devices.menu_room_heading')"></p>
                        <!-- "Ohne Raum" ist hier kein Sonderfall, sondern
                             der Normalzustand eines noch nicht zugeordneten
                             Geraets - es traegt das Haekchen wie jeder
                             andere Eintrag auch. Der Leerstring ist derselbe
                             Wert, den die API fuer "Raum entfernen"
                             erwartet, also dieselbe Kodierung auf beiden
                             Seiten und keine Umrechnung. -->
                        <button
                          class="tile-menu-item"
                          :class="{ 'is-current': roomKeyOf(device) === '' }"
                          :aria-current="roomKeyOf(device) === '' ? 'true' : null"
                          @click="$el.closest('details').open = false; saveRoom(device, '')"
                          x-text="t('web.devices.room_none')"
                        ></button>
                        <template x-for="chip in roomChips().filter((c) => c.key !== '')" :key="chip.key">
                          <button
                            class="tile-menu-item"
                            :class="{ 'is-current': roomKeyOf(device) === chip.key }"
                            :aria-current="roomKeyOf(device) === chip.key ? 'true' : null"
                            @click="$el.closest('details').open = false; saveRoom(device, chip.key)"
                            x-text="chip.key"
                          ></button>
                        </template>
                        <button
                          class="tile-menu-item"
                          x-show="newRoomFor !== device.id"
                          @click="beginNewRoom(device)"
                          x-text="t('web.devices.room_new')"
                        ></button>
                        <!-- Das Textfeld ist ein Kind des `<details>`, das
                             Menue bleibt beim Tippen also offen. Escape
                             bricht NUR den Neu-Raum-Modus ab und darf nicht
                             bis zum `@keydown.escape` des `<details>`
                             hochblubbern, sonst verschwaende ein Abbruch
                             gleich das ganze Menue - daher `.stop`. -->
                        <input
                          x-show="newRoomFor === device.id"
                          x-cloak
                          type="text"
                          class="tile-menu-input"
                          x-model="newRoomDraft"
                          :placeholder="t('web.devices.room_new_placeholder')"
                          @keydown.enter="$el.closest('details').open = false; commitNewRoom(device)"
                          @keydown.escape.stop="newRoomFor = null; newRoomDraft = ''"
                        />
                        <hr class="tile-menu-sep" />
```

- [ ] **Step 5: CSS für den Raum-Abschnitt, alte Regeln entfernen**

In `src/loxmatter/web/style.css` ergänzen (bei den `.tile-menu-*`-Regeln aus Task 2):

```css
.tile-menu-heading {
  margin: 0.1rem 0.5rem 0.2rem;
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
}

/* Das Haekchen steht in `::after`, nicht im Text: der Eintrag traegt
 * seine Bedeutung im Raumnamen, das Haekchen bestaetigt sie nur.
 * `aria-current` im Markup sagt dasselbe fuer alles, was die Seite nicht
 * sieht. */
.tile-menu-item.is-current {
  font-weight: 600;
  color: var(--accent);
}

.tile-menu-item.is-current::after {
  content: " ✓";
}

.tile-menu-input {
  margin: 0.15rem 0;
  font-size: 0.8rem;
}

.tile-menu-sep {
  border: none;
  border-top: 1px solid var(--border);
  margin: 0.25rem 0;
}
```

Und entfernen: die Regel `.room-rename, .room-select, .room-new { font-size: 0.75rem; }` wird zu `.room-rename { font-size: 0.75rem; }` (der Umbenennen-Stift bleibt), sowie der gesamte `.room-picker`-Block samt seinem Kommentar (er beginnt mit „Umschliesst `<select>` und das Neu-Raum-Textfeld nur als Fokus-Wache").

- [ ] **Step 6: `app.js` aufräumen**

Fünf Stellen, alle in `src/loxmatter/web/app.js`:

1. **Zustand:** `roomSelectDrafts: {},` samt dem mehrzeiligen Kommentar davor ersatzlos löschen — der Kommentar beginnt mit „Kachel zeigte dauerhaft \"Ohne Raum\"" und endet mit der Zeile `roomSelectDrafts: {},`.

2. **`newRoomFor`s Kommentar** anpassen — er beschreibt heute die Kopplung an die Auswahlliste. Neu:

```javascript
    // Welche Kachel gerade das Textfeld fuer einen neuen Raumnamen zeigt
    // (Geraete-ID oder null). Ein einzelner globaler Skalar, keine Menge je
    // Kachel: es kann darum immer nur EIN Textfeld offen sein. Waehlt man
    // "+ Neuer Raum ..." im Menue einer zweiten Kachel, schliesst das die
    // erste mit - gewollt, zwei gleichzeitig offene Textfelder waeren
    // ohnehin verwirrend.
    newRoomFor: null,
```

3. **`loadDevices`:** die Schleife samt Kommentar entfernen, sodass nur bleibt:

```javascript
    async loadDevices() {
      this.devicesError = null;
      try {
        this.devices = await this.request("GET", "/api/devices");
      } catch (error) {
        this.devicesError = t("web.devices.list_load_error", { message: error.message });
      }
    },
```

4. **`saveRoom`:** der `finally`-Block verliert `syncRoomSelectDraft`, behält `reconcileRoomFilter`:

```javascript
      } finally {
        // Auch im Fehlerfall: faellt durch den fehlgeschlagenen Schreibweg
        // ein Raum leer, darf der Filter nicht auf einem Raum stehen
        // bleiben, den es nicht mehr gibt.
        this.reconcileRoomFilter();
      }
```

5. **`syncRoomSelectDraft` und `onRoomSelectChange`** samt ihrer Kommentare ersatzlos löschen. **`removeDevice`** verliert seine Zeile `delete this.roomSelectDrafts[device.id];`, **`commissionDevice`** seinen Aufruf `this.syncRoomSelectDraft(device);` — in dessen Kommentar oberhalb ist der Satz über den `roomSelectDrafts`-Eintrag zu streichen, der Rest der Begründung (Objekt befüllen statt ersetzen, wegen der über `await` gehaltenen Referenz in `saveRoom`/`saveLabel`) bleibt richtig und wichtig.

- [ ] **Step 7: Obsolete Tests löschen**

In `tests/api/test_web.py` ersatzlos entfernen:

- `test_the_room_select_uses_a_synced_draft_instead_of_reading_device_room_directly`
- `test_the_new_room_option_resets_the_draft_before_the_mode_starts`
- `test_the_room_select_leaves_new_room_mode_when_a_normal_room_is_picked`
- `test_the_room_picker_closes_new_room_mode_when_focus_leaves_it_entirely`

Sie prüfen eine Mechanik, die es nicht mehr gibt. Nicht umschreiben: ihre gemeinsame Aussage — „die Auswahlliste zeigt nie einen Raum, den das Gerät nicht hat" — ist danach keine prüfbare Behauptung mehr, weil es keine Auswahlliste gibt.

`test_the_page_offers_the_room_bar_and_the_room_picker` prüft mehrere Dinge auf einmal; nur die Zusicherungen zur Raum-Auswahl entfernen, die zur Raumleiste behalten, und den Namen auf `test_the_page_offers_the_room_bar` ändern.

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web src/loxmatter/i18n tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): Raeume sind Menueeintraege, Auswahlfeld entfaellt ersatzlos

Ein Klick auf einen Raum weist zu und schliesst das Menue. Damit
verschwinden `roomSelectDrafts`, `syncRoomSelectDraft`,
`onRoomSelectChange` und der focusout-Waechter - alles Gegengewichte zu
einer Kopplung, die es ohne natives `<select>` nicht mehr gibt.

Vier Tests entfallen mit ihnen. Sie werden geloescht, nicht
umgeschrieben: ihre gemeinsame Aussage ("die Auswahlliste zeigt nie
einen Raum, den das Geraet nicht hat") ist keine pruefbare Behauptung
mehr, wenn es keine Auswahlliste gibt.

`newRoomFor` bleibt - jetzt in der Rolle, fuer die es urspruenglich
gedacht war: Sichtbarkeit des Textfelds, sonst nichts.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Verhalten im Browser prüfen, Screenshots, Abschluss

**Files:**
- Modify: `scripts/capture_screenshots.py` (nur falls ein Selektor bricht)
- Modify: `docs/screenshots/*.png`

**Interfaces:**
- Consumes: alles.
- Produces: nichts.

**Warum diese Aufgabe existiert:** Die WebUI-Tests belegen nur, dass ein Konstrukt ausgeliefert wird. Beim Vorgänger-Entwurf hat genau diese Lücke einen Fehler durchgelassen, bei dem *jede* Kachel dauerhaft den falschen Raum zeigte, obwohl drei Reviewrunden das Markup gelesen hatten. Der Harness unten ist die Gegenmaßnahme und kostet Minuten.

- [ ] **Step 1: Harness bauen und das Menü durchspielen**

Den vorhandenen Demo-Server nehmen, nicht selbst einen bauen:

```bash
uv run python scripts/dev_web_server.py --demo --store-path /tmp/kebab-demo.sqlite --port 8422
```

Passwort `loxmatter-demo`, vier Geräte mit Räumen (Küche zwei, damit die Sortierung innerhalb einer Gruppe ablesbar bleibt). Im Browser öffnen und die DOM-Werte auslesen, nicht das Bild deuten.

Nur falls ein eigener Harness doch nötig wird: `/auth-info` und `/i18n` werden **ohne** `/api`-Präfix abgerufen, und `/api/devices/{id}/controls` liefert `{commands, hidden_raw_commands}`, keine Liste — beides hat beim letzten Mal Zeit gekostet.

Diese sechs Punkte prüfen und die Ergebnisse in den Bericht schreiben:

1. Der ⋮ öffnet das Menü; ein Klick daneben schließt es; Escape schließt es.
2. Ein Klick auf den ⋮ einer zweiten Kachel schließt das Menü der ersten.
3. Der aktuelle Raum trägt das Häkchen — bei einem Gerät ohne Raum steht es bei „Ohne Raum".
4. Ein Klick auf einen anderen Raum weist zu, die Kachel wandert in die richtige Gruppe, das Menü ist zu.
5. „+ Neuer Raum …": Textfeld erscheint **im** Menü, das Menü bleibt beim Tippen offen, Enter speichert und schließt, Escape bricht nur den Neu-Raum-Modus ab und lässt das Menü offen.
6. Dem letzten Gerät eines gefilterten Raums einen anderen Raum geben: `reconcileRoomFilter` greift weiterhin, der Filter fällt auf „Alle" zurück.

- [ ] **Step 2: Screenshots erneuern**

```bash
uv run --with playwright python scripts/capture_screenshots.py
```

Das Skript klickt über `nav.tabs button:has-text(…)`, Passwortfeld und Knopftexte — keiner dieser Selektoren hängt am Inneren der Kachel, es sollte also unverändert durchlaufen. Bricht doch einer, den Selektor im Skript nachziehen, nicht das Markup.

Danach `docs/screenshots/dashboard.png` ansehen und prüfen, dass die Fußzeile wirklich nur noch Hinweis und ⋮ trägt.

- [ ] **Step 3: Alle vier Gates**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

Expected: alle vier sauber. Jeder Fehlschlag wird behoben, nicht unterdrückt.

- [ ] **Step 4: Ungenutzte Übersetzungsschlüssel entfernen**

```bash
for key in $(grep -o '^web\.devices\.[a-z_.]*' src/loxmatter/i18n/strings.yaml | tr -d ':'); do
  grep -q "$key" src/loxmatter/web/index.html src/loxmatter/web/app.js || echo "UNBENUTZT: $key"
done
```

Jeden Treffer prüfen und entfernen, wenn ihn wirklich nichts mehr verwendet. Erwartet wird hier nichts — der Umbau verwendet dieselben Schlüssel weiter —, aber die Prüfung kostet eine Zeile.

- [ ] **Step 5: Commit**

```bash
git add -A docs/screenshots scripts
git commit -m "$(cat <<'EOF'
docs: Screenshots auf das Kachel-Menue nachziehen

Die Fusszeile traegt jetzt nur noch Export-Hinweis und Kebab; die alten
Bilder zeigten das Raum-Auswahlfeld.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```
