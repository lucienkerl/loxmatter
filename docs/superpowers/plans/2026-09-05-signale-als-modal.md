# Signale als Modal im Geräte-Screen — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Ansicht „Signale" verschwindet als eigener Reiter; das Bearbeiten einzelner Signale passiert in einem Modal, das vom Kebab-Menü der Gerätekachel und vom `+ N weitere Signale`-Link geöffnet wird.

**Architecture:** Ein einziges `<dialog>` am Seitenende, außerhalb jeder `x-for`-Schleife. Alpine hält nur die Geräte-**ID** (`signalsModalDevice`); `@close` ist die einzige Stelle, die sie zurücksetzt. Die Signalzeile zieht unverändert aus der alten Ansicht um. Der globale Schalter „Experte anzeigen" weicht einem `<details>` je Gruppe — der Auf-/Zu-Zustand lebt im DOM statt in Alpine.

**Tech Stack:** Alpine.js 3 (vendort unter `web/vendor/alpine.min.js`), natives `<dialog>` und `<details>`, FastAPI liefert `index.html`/`app.js`/`style.css` statisch aus, Tests mit pytest gegen den ausgelieferten Text, Verhaltensprüfung im Browser gegen den Demo-Server.

**Entwurf:** [docs/superpowers/specs/2026-09-05-signale-als-modal-design.md](../specs/2026-09-05-signale-als-modal-design.md)

## Global Constraints

- **Die API bleibt unangetastet.** `GET /api/devices/{id}/signals`, `PATCH /api/signals/{key}`, `POST /api/signals/{key}/write` — keine neue Route, kein neues Feld, keine Änderung an `src/loxmatter/api/`.
- **Kommentare in `app.js`, `index.html`, `style.css` und in Python schreiben Umlaute als `ae`/`oe`/`ue`.** Nur `strings.yaml` (Nutzertexte) und die Dokumente unter `docs/` tragen echte Umlaute.
- **Kein Wert in `strings.yaml` darf als Ganzes von typografischen Anführungszeichen umschlossen sein** (`„…“`, `“…”`). YAML trennt Skalare nur an geraden ASCII-Anführungszeichen; typografische landen wörtlich im String. `tests/test_i18n.py::test_no_value_is_wrapped_in_typographic_quotes` fängt es ab — es ist schon zweimal passiert.
- **Jeder `web.*`-Schlüssel braucht mindestens `en`.** `de` fällt sonst auf `en` zurück, nie umgekehrt.
- **Die WebUI-Tests belegen nur, DASS etwas ausgeliefert wird**, nie dass es wirkt. Verhalten wird in Task 4 im Browser gegen das vendorte Alpine geprüft, nicht durch Lesen.
- **Vier Gates am Ende jeder Task:** `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`.
- **Kein `overflow: hidden` auf `.device-card`, `.device-foot` oder `.tile-menu`**, und keine neue `opacity`/`filter`/`transform`/`contain` auf diesen dreien — das kappt oder versenkt das Kachel-Menü wortlos (siehe die Kommentare in `style.css`).

## File Structure

| Datei | Rolle in diesem Umbau |
| --- | --- |
| `src/loxmatter/web/index.html` | Symbol `i-close`, Kebab-Eintrag, neues Ziel des `+ N`-Links, das `<dialog>` samt Inhalt; später weg: Nav-Knopf und die Signal-`<section>` |
| `src/loxmatter/web/app.js` | `signalsModalDevice`, `signalsModalDeviceObject()`, `openSignalsModal()`, `closeSignalsModal()`, Schließen in `removeDevice`; später weg: `showExpertSignals` und der `selectView`-Zweig |
| `src/loxmatter/web/style.css` | `.signals-modal*`, `.signal-group`; später weg: `.signal-group-toggle` |
| `src/loxmatter/i18n/strings.yaml` | drei neue Schlüssel; später weg: drei alte |
| `tests/api/test_web.py` | neue Zusicherungen; später: drei bestehende Tests nachziehen |
| `scripts/capture_screenshots.py` | `signals.png` entsteht künftig aus dem Modal statt aus dem Reiter |
| `README.md` | Bildunterschrift der Signals-Kachel |

**Reihenfolge und warum:** Task 1 und 2 bauen das Modal **neben** dem noch existierenden Reiter auf — die Anwendung ist zu keinem Zeitpunkt kaputt, und ein Reviewer kann das Modal beurteilen, bevor irgendetwas gelöscht wird. Erst Task 3 löst den Reiter auf.

---

### Task 1: Modal-Hülle, Zustand und die zwei Einstiege

**Files:**
- Modify: `src/loxmatter/web/index.html` (Symbolblock bei `i-rename`; Kebab-Menü bei `exportDevice(device)`; `+ N`-Link; neues `<dialog>` vor `<div class="toasts"`)
- Modify: `src/loxmatter/web/app.js` (Zustandsblock „Signale"; neue Methoden vor `async loadSignals(deviceId)`; `removeDevice`)
- Modify: `src/loxmatter/web/style.css` (ans Dateiende)
- Modify: `src/loxmatter/i18n/strings.yaml` (Abschnitte `web.devices` und `web.signals`)
- Test: `tests/api/test_web.py` (ans Dateiende)

**Interfaces:**
- Consumes: `closeTileMenu(el)`, `t(key, values)`, `this.devices` (Liste von `{id, label, room, online, category, …}`) — alle vorhanden.
- Produces:
  - `signalsModalDevice: string | null` — Geräte-ID des offenen Modals.
  - `signalsModalDeviceObject(): object | null` — löst sie gegen `this.devices` auf.
  - `openSignalsModal(device): void` — setzt die ID, öffnet im `$nextTick`.
  - `closeSignalsModal(): void` — ruft `close()` auf dem `<dialog>`.
  - i18n: `web.devices.menu_signals`, `web.signals.modal_heading` (Platzhalter `{device}`), `web.signals.modal_close`.
  - CSS-Klassen: `.signals-modal`, `.signals-modal-body`, `.signals-modal-head`, `.signals-modal-close`.
  - SVG-Symbol `#i-close`.

- [ ] **Step 1: Die drei fehlschlagenden Tests schreiben**

Ans Ende von `tests/api/test_web.py` anfügen:

```python
async def test_exactly_one_signals_dialog_is_delivered(api):
    """Entwurf Abschnitt 4: EIN `<dialog>` fuer die ganze Seite, nicht eines
    je Kachel.

    Markup innerhalb `x-for` wird einmal PRO GERAET ausgeliefert - bei
    dreissig Geraeten laegen dreissig vollstaendige Signaltabellen im
    Dokument, und jede `id` darin dreissigfach (derselbe Fallstrick, den
    `aria-labelledby` im Kachel-Menue schon einmal umschiffen musste). Die
    Zaehlung auf 1 ist die einzige Zusicherung, die diesen Rueckfall
    ueberhaupt bemerken wuerde: ein `<dialog>` in der Kachel saehe im
    ausgelieferten Text sonst genauso aus wie eines am Seitenende.

    Die Ortspruefung (nach `</main>`) belegt zusaetzlich, dass es ausserhalb
    der Ansichts-Sections und damit ausserhalb jeder Geraeteschleife steht."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert markup.count("<dialog") == 1
    assert 'x-ref="signalsModal"' in markup
    assert markup.index("<dialog") > markup.index("</main>")


async def test_the_signals_modal_has_exactly_one_place_that_resets_its_state(api):
    """Entwurf Abschnitt 4: `@close` ist die EINZIGE Ruecksetzstelle.

    Das Ereignis feuert auf jedem Schliessweg - Escape, Schliessen-Knopf,
    Backdrop, `close()` aus JavaScript. Ein zweiter Ruecksetzer an einem
    einzelnen Schliessweg waere genau die Verteilung auf mehrere Handler,
    die beim Raum-Auswahlfeld sechs Reviewrunden gekostet hat; deshalb
    zaehlt dieser Test die Vorkommen, statt nur eines zu suchen.

    `@click.self` ist dazu Pflicht und kein Beiwerk: ein `<dialog>`
    schliesst bei einem Klick auf den Backdrop NICHT von selbst."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert '@close="signalsModalDevice = null"' in markup
    assert '@click.self="$el.close()"' in markup
    assert markup.count("signalsModalDevice = null") == 1


async def test_the_two_entry_points_open_the_signals_modal(api):
    """Entwurf Abschnitt 5: das Modal hat genau zwei Einstiege.

    Der Kebab-Eintrag ruft ERST `closeTileMenu($el)`, dann
    `openSignalsModal(device)` - diese Reihenfolge traegt den Fokus:
    `closeTileMenu` setzt ihn auf das `<summary>`, und das unmittelbar
    folgende `showModal()` merkt sich genau diesen Fokus als Rueckkehrpunkt.
    Umgedreht landete der Fokus nach dem Schliessen des Modals im Nichts.

    Der `+ N weitere Signale`-Link sprang bislang per `selectView('signals')`
    in eine Liste ALLER Geraete, in der man das eigene wieder suchen musste -
    er zeigt jetzt auf das Geraet, dessen Signale er verspricht."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert '@click="closeTileMenu($el); openSignalsModal(device)"' in markup
    assert "x-text=\"t('web.devices.menu_signals')\"" in markup
    assert '@click.prevent="openSignalsModal(device)"' in markup

    # Die Reihenfolge NUR innerhalb des Menues vergleichen: der
    # `+ N weitere Signale`-Link steht weiter oben in derselben Kachel und
    # ruft dieselbe Methode, ein `markup.index(...)` ueber die ganze Seite
    # traefe also ihn statt den Menueeintrag und waere immer wahr.
    menu_start = markup.index('<div class="tile-menu-items">')
    menu = markup[menu_start : markup.index("</details>", menu_start)]
    assert menu.index("openSignalsModal(device)") < menu.index("exportDevice(device)")


async def test_open_signals_modal_shows_the_dialog_only_after_alpine_rendered(api):
    """Entwurf Abschnitt 4: `showModal()` erst im `$nextTick`.

    `showModal()` setzt den Anfangsfokus auf das erste fokussierbare Element
    IM Dialog - und das gibt es erst, nachdem Alpine den `x-if`-Inhalt
    aufgebaut hat. Ohne `$nextTick` oeffnet der Dialog leer und der Fokus
    landet auf dem `<dialog>` selbst; die erste Tab-Taste faengt dann am
    Dokumentanfang an statt im Modal."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("openSignalsModal(device) {")
    end = script.index("\n    },", start)
    body = script[start:end]
    assert "this.signalsModalDevice = device.id;" in body
    assert "this.$nextTick(() => this.$refs.signalsModal.showModal());" in body


async def test_removing_a_device_closes_a_signals_modal_that_shows_it(api):
    """Entwurf Abschnitt 4, "Wenn das Geraet verschwindet".

    Ohne diesen Ruf bliebe ein Dialog ueber einem Geraet offen stehen, das
    es nicht mehr gibt - und der `x-if`-Waechter machte ihn zu einem leeren
    Kasten ohne erkennbaren Grund."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    start = script.index("async removeDevice(device) {")
    end = script.index("\n    },", start)
    body = script[start:end]
    assert "if (this.signalsModalDevice === device.id) {" in body
    assert "this.closeSignalsModal();" in body
```

- [ ] **Step 2: Tests laufen lassen und den Fehlschlag sehen**

```bash
uv run pytest tests/api/test_web.py -q -k "signals_dialog or resets_its_state or two_entry_points or only_after_alpine or closes_a_signals_modal"
```

Expected: 5 failed. Der erste scheitert an `assert 0 == 1` (kein `<dialog>` im Dokument), die JavaScript-Tests an `ValueError: substring not found` aus `script.index(...)`.

- [ ] **Step 3: Die drei Übersetzungsschlüssel eintragen**

In `src/loxmatter/i18n/strings.yaml`, direkt nach `web.devices.menu_room_heading` (der letzte Eintrag des `web.devices`-Blocks):

```yaml
web.devices.menu_signals:
  en: "Edit signals…"
  de: "Signale bearbeiten…"
```

Und im `web.signals`-Block, direkt nach `web.signals.write_success`:

```yaml
web.signals.modal_heading:
  en: "Signals — {device}"
  de: "Signale — {device}"
web.signals.modal_close:
  en: "Close"
  de: "Schließen"
```

Der Platzhalter `{device}` ist unbedenklich: `_web_strings()` (`src/loxmatter/api/language.py:56`) liefert unaufgelöste Vorlagen über `raw_template()`, gerade damit `web.*`-Schlüssel Platzhalter tragen dürfen. Gefüllt wird er im Browser in `t()`.

- [ ] **Step 4: Das Schließen-Symbol ergänzen**

In `src/loxmatter/web/index.html`, direkt nach dem `</symbol>` von `i-rename`:

```html
      <symbol id="i-close" viewBox="0 0 24 24">
        <path d="M6 6l12 12" />
        <path d="M18 6L6 18" />
      </symbol>
```

Zwei Linien, kein `fill` — `.icon` setzt `fill: none; stroke: currentColor`, ein gefülltes Pfad-Icon bliebe hier unsichtbar (siehe der Kommentar am `i-kebab`-Symbol).

- [ ] **Step 5: Zustand und Methoden in `app.js`**

In `src/loxmatter/web/app.js`, im Zustandsblock direkt nach `rawWriteMessages: {},`:

```js
    // Das Signal-Modal haelt die Geraete-ID, NICHT das Geraeteobjekt:
    // `loadDevices` ersetzt `devices` vollstaendig, ein festgehaltenes
    // Objekt waere danach eine Leiche mit veraltetem Namen und Raum.
    // `signalsModalDeviceObject()` loest die ID gegen die jeweils aktuelle
    // Liste auf. Zurueckgesetzt wird dieses Feld an GENAU EINER Stelle, dem
    // `@close` des `<dialog>` in index.html - siehe den Kommentar dort.
    signalsModalDevice: null,
```

Direkt vor `async loadSignals(deviceId) {` die drei Methoden:

```js
    signalsModalDeviceObject() {
      return this.devices.find((device) => device.id === this.signalsModalDevice) || null;
    },

    /**
     * Oeffnet das Signal-Modal fuer ein Geraet.
     *
     * Das `$nextTick` ist Pflicht, kein Stil: `showModal()` setzt den
     * Anfangsfokus auf das erste fokussierbare Element IM Dialog, und das
     * gibt es erst, nachdem Alpine den `x-if`-Inhalt aufgebaut hat. Ohne
     * das Warten oeffnet der Dialog leer, der Fokus bleibt auf dem
     * `<dialog>` selbst, und die erste Tab-Taste faengt wieder am
     * Dokumentanfang an.
     *
     * `$refs` ist hier unbedenklich, obwohl der Kommentar am Kachel-Menue
     * (index.html, Fund 3) ausdruecklich davon abraet: dessen Einwand
     * trifft eine Registrierung, die PRO KACHEL laeuft und sich selbst
     * ueberschreibt. Dieses `<dialog>` steht genau einmal im Dokument -
     * dieselbe Lage wie bei `pinLogListToTop`, das aus demselben Grund
     * schon heute `this.$refs` benutzt.
     */
    openSignalsModal(device) {
      this.signalsModalDevice = device.id;
      this.$nextTick(() => this.$refs.signalsModal.showModal());
    },

    /**
     * Schliesst das Modal ueber die native `close()`-Methode statt den
     * Zustand direkt zu leeren: `close()` loest das `close`-Ereignis aus,
     * und dessen Handler in index.html ist die eine Stelle, die
     * `signalsModalDevice` zuruecksetzt. Wer hier zusaetzlich
     * `this.signalsModalDevice = null` schriebe, haette wieder zwei
     * Wahrheiten ueber denselben Zustand.
     */
    closeSignalsModal() {
      this.$refs.signalsModal.close();
    },
```

In `removeDevice`, direkt nach `delete this.signalsByDevice[device.id];`:

```js
        // Ohne das bliebe ein Dialog ueber einem Geraet offen stehen, das
        // es nicht mehr gibt - und der `x-if`-Waechter im Modal machte ihn
        // zu einem leeren Kasten ohne erkennbaren Grund. `close()` ist ein
        // Nichtstun, wenn der Dialog gar nicht offen ist; die Abfrage steht
        // trotzdem davor, damit ein Modal ueber einem ANDEREN Geraet nicht
        // mit zugeht.
        if (this.signalsModalDevice === device.id) {
          this.closeSignalsModal();
        }
```

- [ ] **Step 6: Die zwei Einstiege in `index.html` verdrahten**

Im Kachel-Menü, **vor** dem „Exportieren"-Knopf und direkt nach `<hr class="tile-menu-sep" />`:

```html
                        <button
                          class="tile-menu-item"
                          @click="closeTileMenu($el); openSignalsModal(device)"
                          x-text="t('web.devices.menu_signals')"
                        ></button>
```

Die Reihenfolge im `@click` traegt den Fokus: `closeTileMenu` schließt das `<details>` und setzt den Fokus auf dessen `<summary>`; das unmittelbar folgende `showModal()` merkt sich genau diesen Fokus als Rückkehrpunkt. Dieselbe Reihenfolge wie bei „Exportieren" und „Entfernen" daneben.

Und beim `+ N weitere Signale`-Link im Werteraster: `@click.prevent="selectView('signals')"` wird zu

```html
                        @click.prevent="openSignalsModal(device)"
```

- [ ] **Step 7: Das `<dialog>` einsetzen**

In `src/loxmatter/web/index.html`, direkt **vor** `<div class="toasts" aria-live="polite">` — also außerhalb des `</template>`, das die angemeldete Ansicht umschließt, genau wie die Kurzmeldungen daneben:

```html
    <!--
      Signal-Modal (Entwurf "Signale als Modal", 2026-09-05). Genau EIN
      `<dialog>` fuer die ganze Seite, bewusst ausserhalb der `x-for`-
      Schleife der Geraetekacheln: Markup innerhalb `x-for` wird einmal PRO
      GERAET ausgeliefert - bei dreissig Geraeten laegen dreissig
      vollstaendige Signaltabellen im Dokument, und jede `id` darin
      dreissigfach (derselbe Fallstrick, den `aria-labelledby` im
      Kachel-Menue schon einmal umschiffen musste).

      Es steht zudem ausserhalb des Login-`<template>`, wie die
      Kurzmeldungen darunter: so ist `$refs.signalsModal` immer aufloesbar
      und nicht davon abhaengig, ob Alpine den angemeldeten Teilbaum gerade
      gerendert hat.

      `x-ref` ist hier zulaessig, obwohl der Kommentar am Kachel-Menue
      (Fund 3) ausdruecklich davon abraet. Der dortige Einwand trifft eine
      Registrierung, die PRO KACHEL laeuft: die Seite teilt sich ein
      einziges `x-data` am `<body>`, und der Eintrag der zuletzt
      gerenderten Kachel ueberschreibt jeden davor. Dieses `<dialog>` steht
      genau einmal im Dokument - niemand kann es ueberschreiben. Dieselbe
      Lage wie bei `x-ref="diagnosticsLogsList"` und `x-ref="datagramsList"`
      weiter oben, die aus demselben Grund unbedenklich sind.

      `@close` ist die EINZIGE Stelle, die `signalsModalDevice`
      zuruecksetzt. Das Ereignis feuert auf jedem Schliessweg - Escape,
      Schliessen-Knopf, Backdrop, `close()` aus JavaScript -, es gibt also
      keinen Pfad, auf dem der Alpine-Zustand und der sichtbare Zustand
      auseinanderlaufen koennen. Dieselbe Rolle, die `@toggle` beim
      `<details>` des Kachel-Menues spielt. KEINE weiteren Ruecksetzer an
      den einzelnen Schliesswegen ergaenzen - genau diese Verteilung auf
      mehrere Handler war beim Raum-Auswahlfeld die Ursache von sechs
      Reviewrunden.

      Ein `<dialog>` schliesst bei einem Klick auf den Backdrop NICHT von
      selbst (anders als es Escape tut). `@click.self` ergaenzt das: der
      Inhalt liegt vollstaendig in `.signals-modal-body`, ein
      Klick-Ereignis mit dem `<dialog>` SELBST als Ziel kann deshalb nur
      der Backdrop sein.
    -->
    <dialog
      x-ref="signalsModal"
      class="signals-modal"
      @close="signalsModalDevice = null"
      @click.self="$el.close()"
    >
      <template x-if="signalsModalDeviceObject()">
        <div class="signals-modal-body">
          <div class="signals-modal-head">
            <h2 x-text="t('web.signals.modal_heading', { device: signalsModalDeviceObject().label })"></h2>
            <span style="flex: 1 1 auto"></span>
            <button
              class="signals-modal-close"
              :title="t('web.signals.modal_close')"
              :aria-label="t('web.signals.modal_close')"
              @click="closeSignalsModal()"
            ><svg class="icon" aria-hidden="true"><use href="#i-close"></use></svg></button>
          </div>
        </div>
      </template>
    </dialog>
```

Der Rumpf bleibt in dieser Task absichtlich bei Kopfzeile und Schließen-Knopf — Task 2 füllt ihn. So lässt sich Öffnen, Schließen und Fokusrückgabe beurteilen, bevor Inhalt dazukommt.

- [ ] **Step 8: Aussehen**

Ans Ende von `src/loxmatter/web/style.css`:

```css
/* Signal-Modal (Entwurf "Signale als Modal", 2026-09-05).
 *
 * `max-height` plus `overflow: auto` statt einer festen Hoehe: ein Geraet
 * mit vierzig Attributen soll im Modal scrollen, nicht ueber den unteren
 * Bildschirmrand hinauswachsen, wo der Schliessen-Knopf dann unerreichbar
 * waere.
 *
 * Der `::backdrop` bekommt KEINE Farbe aus den Themenvariablen: er liegt
 * im Top-Layer, ausserhalb des Dokumentbaums, und erbt von dort keine
 * `:root`-Variablen zuverlaessig. Ein fester halbtransparenter Schwarzton
 * wirkt in beiden Themen. */
.signals-modal {
  width: min(46rem, 92vw);
  max-height: 85vh;
  overflow: auto;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
}

.signals-modal::backdrop {
  background: rgba(0, 0, 0, 0.45);
}

.signals-modal-body {
  padding: 1rem 1.2rem 1.2rem;
}

.signals-modal-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.signals-modal-head h2 {
  margin: 0;
}

.signals-modal-close {
  flex: none;
  background: none;
  border: 1px solid transparent;
  color: var(--text-muted);
  cursor: pointer;
  padding: 0.25rem;
  line-height: 0;
}

.signals-modal-close:hover {
  color: var(--text);
  border-color: var(--border);
}
```

- [ ] **Step 9: Tests laufen lassen**

```bash
uv run pytest tests/api/test_web.py -q -k "signals_dialog or resets_its_state or two_entry_points or only_after_alpine or closes_a_signals_modal"
```

Expected: 5 passed.

- [ ] **Step 10: Alle Gates**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

Expected: alle vier sauber. Der Reiter „Signale" existiert weiterhin und seine Tests laufen unverändert durch — das ist an dieser Stelle richtig, Task 3 räumt ihn ab.

- [ ] **Step 11: Commit**

```bash
git add src/loxmatter/web src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): Signal-Modal oeffnen und schliessen

Ein einziges <dialog> am Seitenende, erreichbar ueber einen neuen
Kebab-Eintrag und den "+ N weitere Signale"-Link. `@close` ist die
einzige Stelle, die `signalsModalDevice` zuruecksetzt; der Rumpf traegt
vorerst nur Kopfzeile und Schliessen-Knopf.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Der Inhalt des Modals

**Files:**
- Modify: `src/loxmatter/web/index.html` (`.signals-modal-body`, aus Task 1)
- Modify: `src/loxmatter/web/style.css` (ans Dateiende, an den Block aus Task 1 anschließend)
- Test: `tests/api/test_web.py` (ans Dateiende)

**Interfaces:**
- Consumes: `signalsModalDeviceObject()` und `signalsModalDevice` (Task 1); `signalGroupsFor(deviceId)` → `[{key, title, collapsible, signals}]`; `signalsByDevice`, `signalsError`, `titleDrafts`, `rawWriteDrafts`, `rawWriteBusyKey`, `rawWriteMessages`; `loadSignals`, `saveTitle`, `toggleExported`, `toggleResend`, `writeRaw`, `rawWriteMessageClass`, `liveValueOf`, `formatValue`, `signalIsFresh`, `signalAgeTitle` — alle unverändert vorhanden.
- Produces: CSS-Klasse `.signal-group`; sonst nichts, was eine spätere Task konsumiert.

- [ ] **Step 1: Die fehlschlagenden Tests schreiben**

Ans Ende von `tests/api/test_web.py`:

```python
def _signals_dialog(markup: str) -> str:
    """Der Inhalt des Signal-Modals, ohne den Rest der Seite.

    Ein blosses `in markup` wuerde die alte Signal-Section mitzaehlen,
    solange es sie noch gibt (Task 3 loescht sie erst danach) - und traefe
    danach immer noch die Geraetekacheln, die dieselben Helfer benutzen."""
    start = markup.index("<dialog")
    return markup[start : markup.index("</dialog>", start)]


async def test_the_signals_modal_carries_the_complete_signal_row(api):
    """Entwurf Abschnitt 2: die Signalzeile zieht 1:1 um, ohne
    Funktionsverlust - Titel, Export, Resend und Rohwert-Schreiben
    inbegriffen. Genau diese vier Schreibwege sind das, was die alte
    Ansicht als einzige konnte; faellt einer beim Umzug herunter, ist er
    nirgends mehr erreichbar."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    assert '@change="saveTitle(signal)"' in dialog
    assert '@change="toggleExported(signal)"' in dialog
    assert '@change="toggleResend(signal)"' in dialog
    assert '@click="writeRaw(signal)"' in dialog
    assert ":title=\"t('web.signals.key_tooltip')\"" in dialog
    assert "x-text=\"t('web.signals.key_hint')\"" in dialog
    assert "x-text=\"t('web.signals.load_button')\"" in dialog


async def test_the_signals_error_banner_lives_inside_the_modal(api):
    """Entwurf Abschnitt 4, Punkt 2: `signalsError` steht IM Modal.

    Ein `<dialog>` im Top-Layer verdeckt alles darunter samt Backdrop - ein
    Fehlerbanner ausserhalb waere waehrend der einzigen Aktion, die es
    ausloesen kann (Titel speichern, Haken setzen, Rohwert schreiben),
    unsichtbar. Ein unsichtbarer Fehler ist nach Spec 8.1 schlimmer als
    keiner."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    assert 'x-show="signalsError"' in dialog
    assert 'x-text="signalsError"' in dialog


async def test_both_signal_groups_share_one_details_template(api):
    """Entwurf Abschnitt 4, Punkt 5: EINE Vorlage fuer beide Gruppen.

    Zwei Formen (Block hier, `<details>` dort) hiessen zwei Zweige und in
    jedem eine eigene Kopie der Signalzeilen-Vorlage - genau die
    Verdopplung, die `signalGroupsFor` abgeschafft hat (51 doppelte Zeilen,
    siehe dessen Kommentar in app.js).

    Der Startzustand laeuft ueber `x-init` und NICHT ueber ein gebundenes
    `:open`: Alpine wertet Bindungen bei jeder Aenderung ihrer
    Abhaengigkeiten neu aus, und `signalGroupsFor` haengt an
    `signalsByDevice` - ein gespeicherter Signaltitel schriebe ein `:open`
    neu und klappte die gerade geoeffnete Expertengruppe wortlos wieder zu.
    Dieser Test ist die einzige Bremse gegen ein spaeteres, gut gemeintes
    Vereinfachen zu `:open`."""
    client, _, _ = api
    dialog = _signals_dialog(_without_comments((await client.get("/")).text))
    assert 'x-for="group in signalGroupsFor(signalsModalDevice)"' in dialog
    assert dialog.count("<details") == 1
    assert 'x-init="$el.open = !group.collapsible"' in dialog
    assert ":open=" not in dialog
    assert "x-text=\"t('web.signals.functional_vs_expert_explanation')\"" in dialog
    assert "x-text=\"t('web.signals.none_functional')\"" in dialog
```

- [ ] **Step 2: Tests laufen lassen und den Fehlschlag sehen**

```bash
uv run pytest tests/api/test_web.py -q -k "complete_signal_row or error_banner_lives_inside or share_one_details"
```

Expected: 3 failed — `assert '@change="saveTitle(signal)"' in dialog` schlägt fehl, der Modal-Rumpf trägt bislang nur die Kopfzeile.

- [ ] **Step 3: Den Rumpf füllen**

In `src/loxmatter/web/index.html` den Inhalt von `.signals-modal-body` direkt nach dem `</div>` der `.signals-modal-head` ergänzen:

```html
          <p x-show="signalsError" x-cloak class="banner danger" x-text="signalsError"></p>
          <p class="hint" x-text="t('web.signals.key_hint')"></p>

          <!-- Bleibt als Wiederholung fuer den Fehlerfall: im Normalfall hat
               `startApp` die Signale jedes Geraets laengst geladen, dieser
               Knopf zeigt sich also nur, wenn genau dieser eine Abruf
               gescheitert ist (der Grund steht im Banner darueber). -->
          <button
            x-show="!signalsByDevice[signalsModalDevice]"
            @click="loadSignals(signalsModalDevice)"
            x-text="t('web.signals.load_button')"
          ></button>

          <template x-if="signalsByDevice[signalsModalDevice]">
            <div>
              <!--
                EINE Vorlage fuer beide Gruppen. Der naheliegende Entwurf -
                funktional als schlichter Block, Experte als `<details>` -
                braeuchte zwei Zweige und in jedem eine eigene Kopie der
                Signalzeile darunter. Genau diese Verdopplung hat
                `signalGroupsFor` (app.js) abgeschafft: 51 byte-identische
                Zeilen, die bei jeder Aenderung an beiden Stellen
                nachgezogen werden mussten.

                Der Auf-/Zu-Zustand lebt im DOM, nicht in Alpine - dasselbe
                Muster wie beim Kachel-Menue, und der Grund, warum der alte
                globale Schalter `showExpertSignals` ersatzlos entfaellt.

                `x-init` statt `:open` ist Pflicht, kein Stil: Alpine wertet
                eine BINDUNG bei jeder Aenderung ihrer Abhaengigkeiten neu
                aus, und `signalGroupsFor` haengt an `signalsByDevice` - ein
                gespeicherter Signaltitel schriebe ein `:open` neu und
                klappte die gerade geoeffnete Expertengruppe wortlos wieder
                zu. `x-init` laeuft einmal je Element; da `:key` mit
                `group.key` stabil ist, baut Alpine den Knoten bei einem
                Re-Render nicht neu auf, und der Klick des Nutzers bleibt
                stehen. NICHT zu `:open` vereinfachen.
              -->
              <template x-for="group in signalGroupsFor(signalsModalDevice)" :key="group.key">
                <details class="signal-group" x-init="$el.open = !group.collapsible">
                  <summary>
                    <span x-text="group.title"></span>
                    <span class="muted" x-text="'(' + group.signals.length + ')'"></span>
                  </summary>
                  <p
                    class="hint"
                    x-show="group.collapsible"
                    x-text="t('web.signals.functional_vs_expert_explanation')"
                  ></p>
                  <p
                    class="hint"
                    x-show="!group.collapsible && group.signals.length === 0"
                    x-text="t('web.signals.none_functional')"
                  ></p>
                  <template x-for="signal in group.signals" :key="signal.key">
                    <div class="device-controls">
                      <div class="row">
                        <span
                          class="key"
                          :title="t('web.signals.key_tooltip')"
                          x-text="signal.key"
                        ></span>
                        <input
                          type="text"
                          :value="signal.title"
                          @input="titleDrafts[signal.key] = $event.target.value"
                          @change="saveTitle(signal)"
                        />
                        <span class="hint" x-text="signal.path"></span>
                        <!--
                          Wann der Wert zuletzt kam, steht im `title` - nicht
                          daneben im Textfluss (2026-09-03). Eine Angabe wie
                          "vor 7 s" aendert jede Sekunde ihre Breite und
                          schiebt damit die ganze Zeile; der Blick folgt dann
                          der Bewegung statt der Aenderung. Die zeigt
                          `value-fresh`: der Wert leuchtet kurz auf und
                          verblasst wieder, ohne dass sich am Aufbau etwas
                          bewegt.
                        -->
                        <span
                          class="value"
                          :class="{ 'value-fresh': signalIsFresh(signal) }"
                          :title="signalAgeTitle(signal)"
                          x-text="formatValue(liveValueOf(signal)) + (signal.unit ? ' ' + signal.unit : '')"
                        ></span>
                        <label x-show="signal.exportable">
                          <input
                            type="checkbox"
                            :checked="signal.exported"
                            @change="toggleExported(signal)"
                          />
                          <span x-text="t('web.signals.export_checkbox')"></span>
                        </label>
                        <label x-show="signal.exportable">
                          <input
                            type="checkbox"
                            :checked="signal.resend"
                            @change="toggleResend(signal)"
                          />
                          <span x-text="t('web.signals.resend_checkbox')"></span>
                        </label>
                        <span
                          class="badge warn"
                          x-show="!signal.exportable"
                          x-text="signal.reason"
                        ></span>
                      </div>
                      <div class="row" x-show="signal.kind === 'attribute'">
                        <input
                          type="text"
                          :placeholder="t('web.signals.raw_write_placeholder')"
                          @input="rawWriteDrafts[signal.key] = $event.target.value"
                        />
                        <button
                          @click="writeRaw(signal)"
                          :disabled="rawWriteBusyKey === signal.key"
                          x-text="t('web.signals.raw_write_submit')"
                        ></button>
                        <span
                          x-show="rawWriteMessages[signal.key]"
                          :class="rawWriteMessageClass(signal)"
                          x-text="rawWriteMessages[signal.key] ? rawWriteMessages[signal.key].text : ''"
                        ></span>
                      </div>
                    </div>
                  </template>
                </details>
              </template>
            </div>
          </template>
```

- [ ] **Step 4: Aussehen der Gruppen**

Ans Ende von `src/loxmatter/web/style.css`, direkt nach dem `.signals-modal-close:hover`-Block:

```css
/* Die beiden Signalgruppen im Modal. Der Standard-Marker eines `<summary>`
 * muss zweifach abgeschaltet werden: `list-style` greift in Firefox und
 * Chrome, das `::-webkit-details-marker`-Pseudoelement in aelteren
 * WebKit-Fassungen - dieselbe Doppelung wie bei `.tile-menu > summary`.
 * Hier bleibt der Marker aber ERWUENSCHT, weil beide Gruppen wirklich
 * auf- und zuklappbar sind: nur der eigene Chevron ersetzt ihn, damit er
 * in beiden Browserfamilien gleich aussieht. */
.signal-group {
  margin-top: 1rem;
}

.signal-group > summary {
  cursor: pointer;
  font-weight: 600;
  padding: 0.3rem 0;
}

.signal-group > summary .muted {
  color: var(--text-muted);
  font-weight: 400;
  margin-left: 0.35rem;
}
```

- [ ] **Step 5: Tests laufen lassen**

```bash
uv run pytest tests/api/test_web.py -q -k "complete_signal_row or error_banner_lives_inside or share_one_details"
```

Expected: 3 passed.

- [ ] **Step 6: Alle Gates**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

Expected: alle vier sauber.

Sollte `test_the_signal_view_static_text_is_translated` (die alte Signalansicht) hier fehlschlagen, ist das ein Hinweis auf einen Tippfehler im neuen Markup, **nicht** auf einen fälligen Umbau — dieser Test prüft mit `in markup` gegen die ganze Seite und wird von zusätzlichem, korrektem Markup nicht gestört. Task 3 zieht ihn nach.

- [ ] **Step 7: Commit**

```bash
git add src/loxmatter/web tests/api/test_web.py
git commit -m "$(cat <<'EOF'
feat(web): Signalzeilen und beide Gruppen ins Modal

Die Signalzeile zieht unveraendert um (Titel, Export, Resend,
Rohwert-Schreiben). Beide Gruppen teilen sich EIN <details>; der
Startzustand kommt aus x-init, nicht aus einem gebundenen :open - eine
Bindung wuerde die geoeffnete Expertengruppe bei jedem gespeicherten
Titel wieder zuklappen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Den Reiter auflösen

**Files:**
- Modify: `src/loxmatter/web/index.html` (Nav-Knopf; die `<section x-show="view === 'signals'">`)
- Modify: `src/loxmatter/web/app.js` (`showExpertSignals`; der `signals`-Zweig in `selectView`; die beiden Helfer-Kommentare, die den Schalter erwähnen)
- Modify: `src/loxmatter/web/style.css` (`.signal-group-toggle`)
- Modify: `src/loxmatter/i18n/strings.yaml` (drei Schlüssel)
- Modify: `tests/api/test_web.py` (drei bestehende Tests)

**Interfaces:**
- Consumes: alles aus Task 1 und 2.
- Produces: nichts Neues; entfernt `showExpertSignals` und den View-Wert `'signals'`.

- [ ] **Step 1: Die fehlschlagenden Tests schreiben**

Ans Ende von `tests/api/test_web.py`:

```python
async def test_the_signals_view_is_gone_from_navigation_and_markup(api):
    """Entwurf Abschnitt 3: der Reiter wird ersatzlos aufgeloest.

    Geprueft wird nicht nur der Nav-Knopf, sondern auch, dass NIRGENDWO
    mehr auf den Ansichtswert `'signals'` geschaltet wird - ein
    stehengebliebener `selectView('signals')` waere ein Klick, der die
    Anwendung in eine Ansicht schickt, die es nicht mehr gibt: alle
    Sections blieben ausgeblendet, die Seite waere leer, ohne
    Fehlermeldung.

    `showExpertSignals` faellt mit: der Auf-/Zu-Zustand lebt jetzt im DOM
    (`<details>` im Modal), ein globales Feld dafuer waere eine zweite
    Wahrheit ohne Leser."""
    client, _, _ = api
    page = (await client.get("/")).text
    script = (await client.get("/static/app.js")).text
    assert "t('web.nav.signals')" not in page
    assert "view === 'signals'" not in page
    assert "selectView('signals')" not in page
    assert 'view === "signals"' not in script
    assert "showExpertSignals" not in script
    assert "showExpertSignals" not in page


async def test_the_dropped_signal_keys_are_gone_from_the_translation_table(api):
    """Die drei Schluessel des alten Reiters haben keinen Leser mehr.

    `expert_collapsed_hint` faellt dabei ersatzlos statt umzuziehen: "12
    Expertensignale ausgeblendet" sagt dasselbe wie "Experte (12)" im
    `<summary>`, nur nicht an der Stelle, an der man klickt. Ein
    stehengelassener Schluessel waere nicht bloss tot - er verwiese in
    seinem eigenen Text auf einen Schalter, den es nicht mehr gibt."""
    client, _, _ = api
    strings = (await client.get("/api/i18n")).json()["strings"]
    for key in (
        "web.nav.signals",
        "web.signals.show_expert",
        "web.signals.expert_collapsed_hint",
    ):
        assert key not in strings
    assert "web.devices.menu_signals" in strings
    assert "web.signals.modal_heading" in strings
    assert "web.signals.modal_close" in strings
```

- [ ] **Step 2: Tests laufen lassen und den Fehlschlag sehen**

```bash
uv run pytest tests/api/test_web.py -q -k "view_is_gone or dropped_signal_keys"
```

Expected: 2 failed — `assert "t('web.nav.signals')" not in page` und `assert "web.nav.signals" not in strings`.

- [ ] **Step 3: Nav-Knopf und Section entfernen**

In `src/loxmatter/web/index.html` diese Zeile aus `<nav class="tabs">` löschen:

```html
      <button :class="{ active: view === 'signals' }" @click="selectView('signals')" x-text="t('web.nav.signals')"></button>
```

Und die vollständige Signal-Ansicht löschen: vom Kommentarblock

```html
      <!-- ================================================================
           Ansicht 2: Signale
           ================================================================ -->
```

bis einschließlich des zugehörigen `</section>` — heute die Zeilen 799–930. Der darauf folgende Kommentarblock „Ansicht 3: Export" wird zu „Ansicht 2: Export"; die Nummerierung der weiteren Ansichtsüberschriften (System, Einstellungen) entsprechend um eins herunterziehen.

- [ ] **Step 4: `app.js` aufräumen**

`showExpertSignals: false,` samt dem vierzeiligen Kommentar darüber („Experte-Block (Aufgabe 8): …") ersatzlos löschen.

In `selectView` den ganzen `signals`-Zweig löschen — also von

```js
      if (view === "signals") {
```

bis zum schließenden `} else if (view === "export") {`, das dabei zu `if (view === "export") {` wird. Der Kommentar im Zweig („Der vollstaendige Baum, nicht erst nach einem weiteren Klick pro Geraet …") geht mit.

Zuletzt die zwei Kommentare, die `showExpertSignals` beim Namen nennen. Ohne sie schlägt `test_the_signals_view_is_gone_from_navigation_and_markup` weiterhin fehl (`assert "showExpertSignals" not in script` liest die ganze Datei, Kommentare eingeschlossen) — und schlimmer: sie verwiesen auf ein Feld, das es nicht mehr gibt.

Über `expertSignalsFor` die dritte Zeile ersetzen. Bisher:

```js
    // Signale-Ansicht (Aufgabe 8): "Funktional" zeigt sofort, was
    // `is_functional` als gewollt einstuft; "Experte" bleibt zugeklappt,
    // bis `showExpertSignals` das global fuer alle Geraetekarten umschaltet
    // - dieselbe Datengrundlage wie oben, nur ungefiltert nach der
```

Neu:

```js
    // Signal-Modal: "Funktional" zeigt sofort, was `is_functional` als
    // gewollt einstuft; "Experte" bleibt zugeklappt, bis der Nutzer das
    // `<details>` im Modal aufklappt (bis 2026-09-05 tat das ein globaler
    // Schalter fuer alle Geraete zugleich)
    // - dieselbe Datengrundlage wie oben, nur ungefiltert nach der
```

Und über `signalGroupsFor` die vorletzte Aussage. Bisher:

```js
    // Vorlage, ob ein Block hinter `showExpertSignals` versteckt ist und
    // seine Anzahl in der Ueberschrift zeigt - der Rest (Zeilen-Markup,
    // leer-Hinweis) ist fuer beide Gruppen identisch.
```

Neu:

```js
    // Vorlage nur noch den Startzustand des `<details>` (funktional offen,
    // Experte zu, siehe `x-init` in index.html) - der Rest (Zeilen-Markup,
    // leer-Hinweis) ist fuer beide Gruppen identisch. Der erste Satz oben
    // gilt seit dem Modal-Umbau doppelt: dort teilen sich beide Gruppen
    // sogar dasselbe `<details>`-Markup, nicht nur dieselbe Zeilenvorlage.
```

Der Rumpf beider Funktionen bleibt unverändert.

- [ ] **Step 5: `style.css` und `strings.yaml` aufräumen**

`.signal-group-toggle` samt seinem Kommentarblock („Signale-Ansicht (Aufgabe 8): der globale Schalter …") löschen.

Aus `src/loxmatter/i18n/strings.yaml` löschen: `web.nav.signals`, `web.signals.show_expert`, `web.signals.expert_collapsed_hint` — jeweils mit beiden Sprachzeilen.

- [ ] **Step 6: Die drei bestehenden Tests nachziehen**

In `tests/api/test_web.py`:

**a)** In `test_the_tab_bar_labels_are_translated` und `test_every_tab_button_binds_both_its_handler_and_its_label` das Tupel `("devices", "signals", "export", "system", "settings")` zu `("devices", "export", "system", "settings")` machen — beide Stellen.

**b)** In `test_the_bridge_ip_hint_splits_prefix_link_suffix_without_collapsing_to_x_html` den Endanker des Ausschnitts nachziehen:

```python
    device_section_end = markup.index("x-show=\"view === 'export'\"")
```

Und im Docstring dieses Tests einen Satz ergänzen, warum der Anker gewandert ist:

```python
    Der Ausschnitt endet an der NAECHSTEN Ansicht, nicht an einem
    schliessenden Tag: `"view === 'signals'"` war dieser Anker, bis der
    Reiter aufgeloest wurde (2026-09-05) - jetzt ist es `'export'`. Ein
    Anker auf `</div>` oder `</section>` waere hier untauglich, davon gibt
    es in der Geraeteansicht Dutzende.
```

**c)** `test_the_signal_view_static_text_is_translated` richtet sich aufs Modal. Docstring und Rumpf ersetzen durch:

```python
async def test_the_signal_modal_static_text_is_translated(api):
    """Frueher `test_the_signal_view_static_text_is_translated`: dieselben
    Zusicherungen, jetzt gegen das Modal statt gegen den aufgeloesten
    Reiter (2026-09-05).

    Zwei davon sind ersatzlos entfallen: `show_expert` (der globale
    Schalter weicht einem `<details>` je Gruppe) und
    `expert_collapsed_hint` (dessen Text auf genau diesen Schalter
    verwies). Die uebrigen Hinweise, Beschriftungen und Platzhalter tragen
    unveraendert `t(...)` - keiner der frueheren deutschen Literale bleibt
    im Markup."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    dialog = _signals_dialog(markup)
    assert "x-text=\"t('web.signals.key_hint')\"" in dialog
    assert "ist die Verdrahtung in Loxone" not in markup
    assert "x-text=\"t('web.signals.functional_vs_expert_explanation')\"" in dialog
    assert "„Funktional“ sind die Signale" not in markup
    assert "x-text=\"t('web.signals.load_button')\"" in dialog
    assert ">Signale laden<" not in markup
    assert "x-text=\"t('web.signals.none_functional')\"" in dialog
    assert "Kein Signal dieses Geräts gilt als funktional." not in markup
    assert ":title=\"t('web.signals.key_tooltip')\"" in dialog
    assert "Verdrahtung in Loxone – nicht änderbar." not in markup
    assert "x-text=\"t('web.signals.export_checkbox')\"" in dialog
    assert ">exportieren<" not in markup
    assert ":placeholder=\"t('web.signals.raw_write_placeholder')\"" in dialog
    assert "Rohwert schreiben" not in markup
    assert "x-text=\"t('web.signals.raw_write_submit')\"" in dialog
    assert ">Schreiben<" not in markup
```

- [ ] **Step 7: Alle Gates**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

Expected: alle vier sauber.

Bricht `test_the_signal_group_titles_and_toggle_are_translated`, liegt es an seinem Namen, nicht an seinem Inhalt: er prüft `signalGroupsFor`'s Gruppentitel in `app.js`, und die bleiben. Dann nur seinen Docstring auf den neuen Stand bringen.

- [ ] **Step 8: Auf tote Übersetzungsschlüssel prüfen**

```bash
for key in $(grep -o '^web\.\(nav\|signals\|devices\)\.[a-z_.]*' src/loxmatter/i18n/strings.yaml | tr -d ':'); do
  grep -q "$key" src/loxmatter/web/index.html src/loxmatter/web/app.js || echo "UNBENUTZT: $key"
done
```

Expected: keine Ausgabe. Jeder Treffer wird geprüft und entfernt, wenn ihn wirklich nichts mehr verwendet.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web src/loxmatter/i18n/strings.yaml tests/api/test_web.py
git commit -m "$(cat <<'EOF'
refactor(web): Signale-Reiter aufloesen

Nav-Knopf, Section, der selectView-Zweig, showExpertSignals und
.signal-group-toggle entfallen; drei Uebersetzungsschluessel ohne Leser
gehen mit. Der Slice-Anker des Bruecken-IP-Tests wandert von 'signals'
auf 'export'.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Verhalten im Browser prüfen, Screenshots, Doku

**Files:**
- Modify: `scripts/capture_screenshots.py`
- Modify: `docs/screenshots/signals.png`, `docs/screenshots/dashboard.png`
- Modify: `README.md:115–117`

**Interfaces:**
- Consumes: alles.
- Produces: nichts.

**Warum diese Aufgabe existiert:** Die Tests aus Task 1–3 lesen ausgelieferten Text. Sie belegen, *dass* etwas ausgeliefert wird, nie dass es wirkt. Beim Vorgänger-Entwurf hat genau diese Lücke einen Fehler durchgelassen, bei dem *jede* Kachel dauerhaft den falschen Raum zeigte, obwohl drei Reviewrunden das Markup gelesen hatten.

- [ ] **Step 1: Den Demo-Server starten und das Modal durchspielen**

Den vorhandenen Demo-Server nehmen, nicht selbst einen bauen:

```bash
uv run python scripts/dev_web_server.py --demo --store-path /tmp/signals-modal-demo.sqlite --port 8423
```

Passwort `loxmatter-demo`. Im Browser öffnen und die DOM-Werte auslesen, nicht das Bild deuten.

Diese neun Punkte prüfen und die Ergebnisse in den Bericht schreiben:

1. Der Kebab-Eintrag „Edit signals…" öffnet das Modal; das Menü ist danach zu.
2. Escape schließt das Modal, ein Klick auf den Backdrop ebenfalls, der ×-Knopf ebenfalls. Nach jedem der drei Wege ist `Alpine.$data(document.body).signalsModalDevice === null`.
3. Nach dem Schließen steht der Fokus wieder auf dem `<summary>` der Kachel, von der aus geöffnet wurde (`document.activeElement.closest('.tile-menu')` ist nicht `null`).
4. Beim Öffnen liegt der Fokus **im** Modal (`document.querySelector('.signals-modal').contains(document.activeElement)`) — der Beleg dafür, dass das `$nextTick` wirkt.
5. Die funktionale Gruppe ist offen, die Experten-Gruppe zu.
6. **Die Regression, gegen die `x-init` antritt:** Expertengruppe aufklappen, dann im Modal einen Signaltitel ändern und das Feld verlassen (`change` feuert, `saveTitle` schreibt `signalsByDevice` neu). Die Expertengruppe muss **offen bleiben**. Klappt sie zu, ist irgendwo doch eine Bindung im Spiel.
7. Ein Export-Haken lässt sich setzen und der Wert überlebt Schließen und erneutes Öffnen.
8. Live-Werte laufen im offenen Modal weiter (ein `value-fresh`-Aufleuchten ist zu sehen).
9. Der `+ N weitere Signale`-Link öffnet dasselbe Modal für dasselbe Gerät.

Nur falls ein eigener Harness doch nötig wird: `/auth-info` und `/i18n` werden **ohne** `/api`-Präfix abgerufen, und `/api/devices/{id}/controls` liefert `{commands, hidden_raw_commands}`, keine Liste — beides hat schon zweimal Zeit gekostet.

- [ ] **Step 2: Das Screenshot-Skript auf das Modal umstellen**

In `scripts/capture_screenshots.py` den Signals-Abschnitt ersetzen. Bisher:

```python
    select_view(page, "Signals")
    shoot(page, "signals")
```

Neu — beim Einsetzen um eine Ebene einrücken, der Block steht im Rumpf von `capture()`:

```python
# Signale haben keinen eigenen Reiter mehr (Entwurf "Signale als Modal",
# 2026-09-05) - das Bild entsteht jetzt aus dem Modal ueber dem
# Geraeteraster. Der Weg dorthin ist derselbe wie fuer einen Nutzer:
# Kebab der ersten Kachel, dann der Menuepunkt.
page.click(".device-card .tile-menu > summary")
page.click('.tile-menu-item:has-text("Edit signals")')
page.wait_for_selector("dialog.signals-modal[open]", timeout=5000)
# Die Expertengruppe aufklappen: zugeklappt zeigt das Bild bei den
# Demo-Geraeten nur zwei, drei Zeilen und viel Leerraum - der Punkt
# dieses Bildes sind aber gerade die Loxone-Adressen und die
# Export-Haken nebeneinander.
page.click("dialog.signals-modal details:not([open]) > summary")
shoot(page, "signals")
page.keyboard.press("Escape")
page.wait_for_timeout(300)
```

Und im Kommentar über `shoot(page, "dashboard")` die Aufzählung `Devices/Signals/Export/System/Settings` auf `Devices/Export/System/Settings` korrigieren.

- [ ] **Step 3: Screenshots erneuern**

```bash
uv run --with playwright python scripts/capture_screenshots.py
```

Expected: alle sieben Bilder werden neu geschrieben, ohne Timeout. Danach `docs/screenshots/signals.png` und `docs/screenshots/dashboard.png` ansehen: das erste zeigt das Modal mit sichtbaren Loxone-Adressen und Export-Haken, das zweite eine Reiterleiste ohne „Signals".

- [ ] **Step 4: Die Bildunterschrift in der README nachziehen**

`README.md:115–117` ersetzen:

```markdown
<img src="docs/screenshots/signals.png" alt="Signal editor opened over the device grid, with Loxone addresses and export checkboxes" />

**Signals**<br>Open a device's signals from its tile menu: each signal with the Loxone address it will get and its own export checkbox; the administrative ones sit behind a collapsed expert section.
```

- [ ] **Step 5: Alle Gates**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src
```

Expected: alle vier sauber. Jeder Fehlschlag wird behoben, nicht unterdrückt.

- [ ] **Step 6: Commit**

```bash
git add -A docs/screenshots scripts README.md
git commit -m "$(cat <<'EOF'
docs: Screenshots und Produktseite auf das Signal-Modal nachziehen

Das Signals-Bild entsteht jetzt aus dem Modal ueber dem Geraeteraster
statt aus einem eigenen Reiter; das Skript klickt sich denselben Weg wie
ein Nutzer.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Was dieser Plan bewusst NICHT tut

- **Keine geräteübergreifende Signalübersicht als Ersatz.** Der Entwurf (Abschnitt 3, „Der bewusste Verlust") begründet das: Der Vergleich über alle Geräte hinweg ist die seltene Aufgabe. Sollte er sich melden, gehört er in die Export-Vorschau, nicht in einen neuen Reiter.
- **Keine Änderung an `signalGroupsFor`, `functionalSignalsFor`, `expertSignalsFor`.** Sie liefern schon genau das, was das Modal braucht.
- **Keine neue Route und kein neues API-Feld.**
- **Kein Merken des Auf-/Zu-Zustands der Expertengruppe über das Schließen hinaus.** Das wäre wieder ein globales Feld — genau das, was `showExpertSignals` war.
