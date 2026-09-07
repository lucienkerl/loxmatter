# Gerätekachel ohne Leitwert — Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der herausgehobene Leitwert in der Kopfzeile der Gerätekachel entfällt ersatzlos; alle funktionalen Signale stehen gleichrangig im Werteraster.

**Architecture:** Reine Wegnahme. Das `x-for` des Werterasters wechselt von `restSignalsFor` auf `firstSignalsFor` und zeigt damit die volle Kurzliste statt derselben Liste ohne ihren ersten Eintrag; der `.lead-value`-Block und das `.lead-label`-Span verschwinden aus der Kopfzeile, die Offline-Pille rückt auf den frei gewordenen Platz. Danach fallen zwei Alpine-Methoden und drei CSS-Regeln ersatzlos. Es entsteht kein neues Konzept, keine neue Farbe, kein neuer Übersetzungsschlüssel.

**Tech Stack:** Statisches HTML mit Alpine.js (vendort unter `web/vendor/`), handgeschriebenes CSS mit Custom Properties, Inline-SVG-Sprite. Tests: pytest + httpx gegen die ASGI-App, geprüft wird das **ausgelieferte** Markup und CSS; die drei Node-Tests laufen `app.js` in einem `node`-Prozess.

**Entwurf:** [2026-09-07-geraeteliste-ohne-leitwert-design.md](../specs/2026-09-07-geraeteliste-ohne-leitwert-design.md)

## Global Constraints

- **Kommentare im Quelltext ohne Umlaute** — `ae`, `oe`, `ue`, `ss`. Nur die Dokumentation unter `docs/` und die deutschen Texte in `strings.yaml` tragen echte Umlaute. (Durchgehend so im ganzen Repo.)
- **`functionalSignalsFor()`, `firstSignalsFor()`, `remainingSignalCount()` und `FUNCTIONAL_PREVIEW_LIMIT: 6` bleiben unangetastet.** Sechs Signale je Kachel bleiben sechs Signale je Kachel (Entwurf, Abschnitt 3).
- **`formatValue()` bleibt unangetastet.** `wahr`/`falsch` bleiben technisch beschriftet.
- **Keine neue Farbe, kein neuer Übersetzungsschlüssel, keine neue Datei.** Der Entwurf ist eine Wegnahme.
- **`.value-rows` bleibt bei `font-size: 0.75rem`.** Eine Anhebung auf `0.8rem` ist die *Korrektur für den Fall, dass die Browser-Prüfung in Task 4 sie nötig macht* — nicht Teil der geplanten Änderung.
- **Die Null-Duldsamkeit von `signalIsFresh`, `signalAgeTitle` und `liveValueOf` bleibt stehen** (Entwurf, Abschnitt 8). Nur der Aufhänger ihres Tests wechselt.
- **Tests prüfen Ausgeliefertes, nicht Gerendertes.** In dieser Suite läuft keine Engine, die CSS anwendet oder Alpine ausführt. Assertions gehen gegen `(await client.get("/")).text` und `(await client.get("/static/style.css")).text`.
- **Markup-Assertions laufen über `_without_comments()`** (Helfer oben in `tests/api/test_web.py`). Die Kommentare in `index.html` nennen Attribute beim Namen, teils um zu begründen, warum sie dort *nicht* stehen.
- **CI-Prüfungen:** `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest -v`.
- **Neue Tests werden an `tests/api/test_web.py` angehängt.**

---

### Task 1: Das Werteraster zeigt alle Signale, der Leitwert verlässt das Markup

**Files:**
- Modify: `src/loxmatter/web/index.html:601-648` (Kopfzeile), `:652` (`x-for` des Werterasters), `:678-688` (Hinweis auf fehlende Signale)
- Modify: `tests/api/test_web.py:2624-2630` (`test_the_page_offers_the_room_bar`), `:3040-3057` (löschen)
- Test: `tests/api/test_web.py` (anhängen)

**Interfaces:**
- Consumes: `firstSignalsFor(deviceId)` und `functionalSignalsFor(deviceId)` aus `app.js` — beide existieren bereits unverändert.
- Produces: ein Markup ohne die Zeichenketten `lead-value`, `lead-label`, `leadSignalFor(` und `restSignalsFor(`. Task 2 und Task 3 setzen darauf auf.

- [ ] **Step 1: Die vier neuen Tests schreiben**

An `tests/api/test_web.py` anhängen:

```python
async def test_the_value_grid_now_carries_every_functional_signal(api):
    """Entwurf 2026-09-07, Abschnitt 2: der herausgehobene Leitwert
    entfaellt, alle funktionalen Signale stehen gleichrangig im
    Werteraster. `restSignalsFor` lieferte die Kurzliste OHNE ihren ersten
    Eintrag - genau der stand oben in der Kopfzeile. Mit dem Wegfall der
    Kopfzeilen-Anzeige muss das Raster wieder ueber die volle Liste
    laufen, sonst verschwaende das erste Signal ersatzlos."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert 'x-for="signal in firstSignalsFor(device.id)"' in markup
    assert "restSignalsFor(" not in markup


async def test_the_tile_header_no_longer_carries_a_lead_value(api):
    """Weder die Klassen noch der Aufruf duerfen ausgeliefert werden. Der
    Test laeuft ueber `_without_comments`, weil die Begruendung im Markup
    den Leitwert weiterhin beim Namen nennt - und zwar gerade, um zu
    erklaeren, warum er dort nicht mehr steht."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert "lead-value" not in markup
    assert "lead-label" not in markup
    assert "leadSignalFor(" not in markup


async def test_the_offline_pill_sits_in_the_header_not_under_the_name(api):
    """Die Pille rueckt auf den Platz des Leitwerts: drittes Kind von
    `.device-head`, nicht mehr Kind von `.device-ident` unter dem Namen
    (Entwurf, Abschnitt 5). `margin-left: auto` an `.status-pill` schiebt
    sie dort ohne eigene Regel nach rechts.

    Belegt wird die Verschachtelung ueber die Reihenfolge im
    ausgelieferten Markup: zwischen dem Namensfeld und der Pille MUSS ein
    schliessendes `</span>` liegen - das von `.device-ident`. Steht die
    Pille noch drin, fehlt es."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    name_end = markup.index('@change="saveLabel(device)"')
    pill = markup.index('<span class="status-pill off"', name_end)
    assert "</span>" in markup[name_end:pill], (
        "die Offline-Pille steht noch innerhalb von `.device-ident`"
    )


async def test_the_missing_signals_hint_no_longer_asks_for_a_lead(api):
    """Der Hinweis unterscheidet "geladen, aber leer" von "laedt noch"
    (Spec 8.1). Sein Aufhaenger war `!leadSignalFor(device.id)`; ohne
    Leitwert fragt er die Liste direkt."""
    client, _, _ = api
    markup = _without_comments((await client.get("/")).text)
    assert (
        'x-show="signalsByDevice[device.id] '
        '&& functionalSignalsFor(device.id).length === 0"'
    ) in markup
```

- [ ] **Step 2: Die vier Tests laufen lassen, sie müssen fehlschlagen**

Run: `uv run pytest tests/api/test_web.py -k "value_grid_now_carries or no_longer_carries_a_lead or offline_pill_sits_in_the_header or no_longer_asks_for_a_lead" -v`

Expected: 4 FAILED. `test_the_value_grid_now_carries_every_functional_signal` scheitert an `assert 'x-for="signal in firstSignalsFor(device.id)"' in markup`, die übrigen an ihrer jeweils ersten Assertion.

- [ ] **Step 3: Die Kopfzeile in `index.html` ersetzen**

`src/loxmatter/web/index.html`, Zeilen 601–648. Ersetze den gesamten Block von `<div class="device-head">` bis zum zugehörigen `</div>` durch:

```html
                  <div class="device-head">
                    <span class="type-badge">
                      <svg class="icon"><use :href="'#i-cat-' + device.category"></use></svg>
                    </span>
                    <span class="device-ident">
                      <input
                        type="text"
                        class="device-name"
                        :value="device.label"
                        @input="labelDrafts[device.id] = $event.target.value"
                        @change="saveLabel(device)"
                      />
                    </span>
                    <!-- Die Offline-Pille steht seit dem Wegfall des
                         Leitwerts (Entwurf 2026-09-07) HIER, auf dessen
                         Platz, und nicht mehr in `.device-ident` unter dem
                         Namen. Sie verdraengt damit nichts mehr: das
                         `.lead-label`, mit dem sie sich die Zeile teilte,
                         gibt es nicht mehr - und mit ihm die Regel "nur die
                         Offline-Pille verdraengt das Leitwert-Label", die
                         hier frueher zwei Absaetze Begruendung brauchte.

                         Keine eigene Positionsregel noetig: `.status-pill`
                         traegt `margin-left: auto` (style.css), und das
                         schiebt sie in dieser Flex-Kopfzeile an den rechten
                         Rand. Solange `.device-ident` daneben `flex: 1 1
                         auto` hat, bleibt der Name der schrumpfende Teil -
                         die Pille ist kurz und soll nicht kuerzen.

                         Die Geaendert-seit-Export-Pille sitzt weiterhin in
                         der FUSSZEILE (Pille-in-die-Fusszeile-Umbau,
                         2026-09-06) und ausdruecklich nicht hier: sie
                         beantwortet dieselbe Frage wie `exportHintFor`
                         direkt daneben. Die Kopfzeile bleibt dem
                         Geraetezustand vorbehalten, nicht dem
                         Exportzustand. -->
                    <span class="status-pill off" x-show="!isOnline(device)">
                      <svg class="icon"><use href="#i-offline"></use></svg>
                      <span x-text="t('web.devices.offline')"></span>
                    </span>
                  </div>
```

- [ ] **Step 4: Das `x-for` des Werterasters umstellen**

Dieselbe Datei, in `<div class="value-rows" ...>`. Ersetze:

```html
                    <template x-for="signal in restSignalsFor(device.id)" :key="signal.key">
```

durch:

```html
                    <!-- `firstSignalsFor`, nicht `restSignalsFor` (Entwurf
                         2026-09-07): das Raster zeigt die volle Kurzliste.
                         `restSignalsFor` lieferte sie ohne ihren ersten
                         Eintrag, weil genau der oben in der Kopfzeile stand
                         - mit dem Wegfall der Kopfzeilen-Anzeige waere er
                         sonst nirgends mehr zu sehen. -->
                    <template x-for="signal in firstSignalsFor(device.id)" :key="signal.key">
```

- [ ] **Step 5: Den Hinweis auf fehlende Signale umhängen**

Dieselbe Datei, Zeilen 678–688. Ersetze Kommentar und Absatz durch:

```html
                  <!-- Fund 3: ohne diesen Hinweis blieb zwischen Kopfzeile
                       und Befehlsleiste stillschweigend eine Luecke, sobald
                       ein Geraet geladene, aber leere funktionale Signale
                       hat (die `value-rows` bleiben dann ohne Inhalt) -
                       nicht von einem noch ladenden Geraet zu
                       unterscheiden. Die Bedingung fragte frueher
                       `!leadSignalFor(device.id)`; ohne Leitwert fragt sie
                       die Liste direkt, was ohnehin die ehrlichere Frage
                       ist. -->
                  <p
                    class="hint"
                    x-show="signalsByDevice[device.id] && functionalSignalsFor(device.id).length === 0"
                    x-text="t('web.devices.no_functional_signals')"
                  ></p>
```

- [ ] **Step 6: Die zwei Alt-Tests anpassen**

In `tests/api/test_web.py`, `test_the_page_offers_the_room_bar` (~Zeile 2624): ersetze

```python
    assert "leadSignalFor(" in page
```

durch

```python
    assert "firstSignalsFor(" in page
```

Und lösche `test_the_lead_label_only_yields_to_the_offline_pill_now` (~Zeile 3040) vollständig, samt Docstring. Der Test belegte, dass das Leitwert-Label nur noch der Offline-Pille weicht — es gibt kein Label mehr, dem etwas weichen könnte.

- [ ] **Step 7: Die volle Suite laufen lassen**

Run: `uv run pytest tests/api/test_web.py -v`

Expected: PASS. Die vier neuen Tests aus Step 1 sind grün, `test_the_page_offers_the_room_bar` bleibt grün, `test_the_lead_label_only_yields_to_the_offline_pill_now` existiert nicht mehr. Die Node-Tests (`test_a_device_without_a_lead_signal_…`, `test_a_signal_that_exists_…`) und die Helferliste sind noch grün: `app.js` ist unverändert, `leadSignalFor` existiert dort weiter, nur ruft das Markup es nicht mehr auf.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/index.html tests/api/test_web.py
git commit -m "feat(web): Werteraster zeigt alle Signale, Leitwert entfaellt

Das x-for laeuft ueber firstSignalsFor statt restSignalsFor, der
.lead-value-Block und das .lead-label-Span verlassen die Kopfzeile, die
Offline-Pille rueckt auf deren Platz.

Damit bekommt der Geraetename die ganze Kopfzeile: .lead-value trug
max-width: 50% und flex: 0 0 auto, der Name blieb an der
Grid-Untergrenze bei 65 px.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `leadSignalFor` und `restSignalsFor` löschen

**Files:**
- Modify: `src/loxmatter/web/app.js:1248-1265` (löschen), `:1615-1620` (Kommentar)
- Modify: `tests/api/test_web.py:704-747` (Aufhänger wechseln), `:749-775` (eine Zeile), `:2595-2612` (Helferliste)

**Interfaces:**
- Consumes: das Markup aus Task 1, das keine der beiden Methoden mehr aufruft.
- Produces: ein `app.js` ohne die Zeichenketten `leadSignalFor` und `restSignalsFor`.

- [ ] **Step 1: Den Test schreiben, der die Löschung festhält**

An `tests/api/test_web.py` anhängen:

```python
async def test_the_lead_helpers_are_gone_from_the_script(api):
    """Entwurf 2026-09-07, Abschnitt 10: beide Methoden entfallen
    ersatzlos, nachdem das Markup sie nicht mehr aufruft. Eine ungenutzte
    Methode in `app.js` ist kein harmloser Rest - sie laedt den naechsten
    Umbau dazu ein, den Leitwert wieder einzufuehren, ohne den Entwurf
    gelesen zu haben."""
    client, _, _ = api
    script = (await client.get("/static/app.js")).text
    assert "leadSignalFor" not in script
    assert "restSignalsFor" not in script
```

- [ ] **Step 2: Test laufen lassen, er muss fehlschlagen**

Run: `uv run pytest tests/api/test_web.py::test_the_lead_helpers_are_gone_from_the_script -v`

Expected: FAIL bei `assert "leadSignalFor" not in script`.

- [ ] **Step 3: Die beiden Methoden aus `app.js` löschen**

`src/loxmatter/web/app.js`, Zeilen 1248–1265. Lösche den kompletten Abschnitt — die Trennkommentarzeile `// --- Leitwert (Kachel-Kopfzeile) ---…`, beide Erklärkommentare und beide Methoden:

```javascript
    // --- Leitwert (Kachel-Kopfzeile) --------------------------------------

    // Das erste funktionale Signal in der Reihenfolge, die
    // `firstSignalsFor` ohnehin liefert - also die der Profiltabelle.
    // Steckdose -> Zustand, Klimasensor -> Temperatur, Rollo -> Position.
    // Keine eigene Datenhaltung, keine Konfiguration: ein Geraet ohne
    // funktionale Signale hat schlicht keinen Leitwert, und die Kopfzeile
    // bleibt einzeilig.
    leadSignalFor(deviceId) {
      return this.firstSignalsFor(deviceId)[0] || null;
    },

    // Der Rest der Kurzliste. `FUNCTIONAL_PREVIEW_LIMIT` zaehlt den
    // Leitwert MIT (Entwurf 6.2), deshalb hier kein zweites Abschneiden -
    // `firstSignalsFor` hat es bereits getan.
    restSignalsFor(deviceId) {
      return this.firstSignalsFor(deviceId).slice(1);
    },
```

Ersatzlos. Das `deviceGroups()` darüber und der nächste Abschnitt darunter rücken zusammen.

- [ ] **Step 4: Den Kommentar an `signalIsFresh` nachziehen**

Dieselbe Datei, ~Zeile 1615. Ersetze im Kommentarblock:

```javascript
      // `null` ist hier ein GUELTIGES Argument, kein Programmierfehler:
      // `leadSignalFor` liefert es fuer jedes Geraet, dessen Signale noch
      // nicht geladen sind - und das ist zwischen `GET /api/devices` und
      // `GET /api/devices/<id>/signals` jedes Geraet, mindestens einen
      // Rendering-Durchlauf lang (2026-09-06).
```

durch:

```javascript
      // `null` ist hier ein GUELTIGES Argument, kein Programmierfehler.
      // Der Aufrufer, der es lieferte, war `leadSignalFor` - fuer jedes
      // Geraet, dessen Signale noch nicht geladen waren, also zwischen
      // `GET /api/devices` und `GET /api/devices/<id>/signals` fuer JEDES
      // Geraet, mindestens einen Rendering-Durchlauf lang (2026-09-06).
      //
      // Diesen Aufrufer gibt es seit dem Wegfall des Leitwerts nicht mehr
      // (Entwurf 2026-09-07): das `x-for` des Werterasters laeuft ueber
      // eine leere Liste und wertet gar nichts aus. Die Duldsamkeit bleibt
      // trotzdem stehen. Sie zu entfernen, weil der eine BEKANNTE Aufrufer
      // weg ist, waere die Sorte Aufraeumen, die beim naechsten Aufrufer
      // zurueckschlaegt - und der naechste faende denselben Fehler wieder,
      // ohne den Kommentar unten zu kennen.
```

Die beiden folgenden Absätze des Kommentars (über `x-show` und darüber, warum die Absicherung hier und nicht im Markup liegt) bleiben unverändert stehen.

- [ ] **Step 5: Die Helferliste kürzen**

`tests/api/test_web.py`, ~Zeile 2595. Lösche aus dem `for name in (…)`-Tupel die beiden Zeilen:

```python
        "leadSignalFor(",
        "restSignalsFor(",
```

Die übrigen dreizehn Einträge bleiben unverändert.

- [ ] **Step 6: Den Null-Duldsamkeits-Test auf die Helfer selbst umhängen**

`tests/api/test_web.py`, ~Zeile 704. Ersetze `test_a_device_without_a_lead_signal_does_not_throw_in_any_binding` vollständig durch:

```python
@pytest.mark.skipif(NODE is None, reason="node wird fuer diesen Test gebraucht")
def test_the_signal_helpers_tolerate_null_without_throwing():
    """Frueher `test_a_device_without_a_lead_signal_does_not_throw_in_any_binding`.

    Der Aufhaenger war der Leitwert: zwischen `GET /api/devices` und
    `GET /api/devices/<id>/signals` liegt ein Rendering-Durchlauf, in dem
    `signalsByDevice` fuer das Geraet noch LEER ist - `leadSignalFor`
    lieferte dann `null`. Das `x-show` auf der Huelle half nicht: es setzt
    nur `display`, es haelt Alpine NICHT davon ab, die Ausdruecke der
    Kinder auszuwerten. Die drei Helfer lasen also `signal.key` auf `null`
    und warfen - dreimal pro Geraet, bei jedem Durchlauf.

    Diesen Aufrufer gibt es nicht mehr (Entwurf 2026-09-07): das `x-for`
    des Werterasters laeuft ueber eine leere Liste und wertet gar nichts
    aus. Die Duldsamkeit der Helfer bleibt trotzdem stehen und wird
    weiterhin belegt - der Test fragt sie jetzt direkt statt ueber einen
    Aufrufer, den es nicht mehr gibt.
    """
    values = _app_state(
        """
        state.signalsByDevice = {};
        const out = { calls: {} };
        for (const fn of ["signalIsFresh", "signalAgeTitle", "liveValueOf"]) {
          try {
            out.calls[fn] = { ok: true, value: state[fn](null) ?? null };
          } catch (error) {
            out.calls[fn] = { ok: false, error: error.message };
          }
        }
        out.formatted = state.formatValue(state.liveValueOf(null));
        console.log(JSON.stringify(out));
        """
    )

    for name, call in values["calls"].items():
        assert call["ok"], f"{name} warf: {call.get('error')}"

    # Was die Kachel in diesem Zustand zeigt: keine Hervorhebung, kein
    # Tooltip - und der Strich, den `formatValue` fuer "kein Wert" fuehrt.
    assert values["calls"]["signalIsFresh"]["value"] is False
    assert values["calls"]["signalAgeTitle"]["value"] in (None, "")
    assert values["calls"]["liveValueOf"]["value"] is None
    assert values["formatted"] == "-"
```

- [ ] **Step 7: Den Normalfall-Test vom Leitwert lösen**

Dieselbe Datei, `test_a_signal_that_exists_is_unaffected_by_the_guard` direkt darunter. Lösche im JavaScript-Block die Zeile

```javascript
          lead: state.leadSignalFor(1).key,
```

und weiter unten die zugehörige Assertion

```python
    assert values["lead"] == "d1_1_onoff"
```

Die drei übrigen Assertions (`live`, `fresh`, `title`) bleiben unverändert — sie sind der eigentliche Zweck des Tests.

- [ ] **Step 8: Suite laufen lassen**

Run: `uv run pytest tests/api/test_web.py -v`

Expected: PASS. Insbesondere grün: `test_the_lead_helpers_are_gone_from_the_script`, `test_the_signal_helpers_tolerate_null_without_throwing`, `test_a_signal_that_exists_is_unaffected_by_the_guard` und die gekürzte Helferliste.

- [ ] **Step 9: Commit**

```bash
git add src/loxmatter/web/app.js tests/api/test_web.py
git commit -m "refactor(web): leadSignalFor und restSignalsFor loeschen

Beide hatten nach dem Umbau der Kopfzeile keinen Aufrufer mehr.

Der Test gegen die Null-Duldsamkeit der drei Signalhelfer haengt jetzt
an den Helfern selbst statt am Leitwert: die Duldsamkeit bleibt, nur ihr
bekannter Aufrufer ist weg.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `.lead-value` und `.lead-label` aus dem Stylesheet löschen

**Files:**
- Modify: `src/loxmatter/web/style.css:1659-1667` (`.lead-label`), `:1669-1745` (Kommentarblock, `.lead-value`, `.lead-value small`), `:1615-1637` (Kommentar an `.device-head .device-name`)
- Modify: `tests/api/test_web.py:3088-3140` (zwei Tests löschen), `:3143-3161` (Docstring)

**Interfaces:**
- Consumes: das Markup aus Task 1, das beide Klassen nicht mehr trägt.
- Produces: ein `style.css` ohne die Zeichenketten `.lead-value` und `.lead-label`.

- [ ] **Step 1: Den Test schreiben**

An `tests/api/test_web.py` anhängen:

```python
async def test_the_lead_rules_are_gone_from_the_stylesheet(api):
    """Entwurf 2026-09-07, Abschnitt 10. Beide Klassen stehen in keinem
    Markup mehr; ihre Regeln - samt der langen Begruendung zu
    `flex: 0 0 auto` gegen `flex: 0 1 auto` und zum `padding-block` fuer
    Unterlaengen - beschreiben ein Element, das es nicht mehr gibt."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    assert ".lead-value" not in css
    assert ".lead-label" not in css
```

- [ ] **Step 2: Test laufen lassen, er muss fehlschlagen**

Run: `uv run pytest tests/api/test_web.py::test_the_lead_rules_are_gone_from_the_stylesheet -v`

Expected: FAIL bei `assert ".lead-value" not in css`.

- [ ] **Step 3: Die drei Regeln löschen**

`src/loxmatter/web/style.css`. Lösche ersatzlos:

- die Regel `.lead-label { … }` (Zeilen 1659–1667),
- den davorstehenden Kommentarblock, der `flex: 0 0 auto` gegen `flex: 0 1 auto` begründet (beginnt bei `/* \`flex: 0 0 auto\` mit \`max-width\` statt \`flex: 0 1 auto\``, Zeile ~1669),
- die Regel `.lead-value { … }` (Zeilen 1726–1737),
- die Regel `.lead-value small { … }` (Zeilen 1739–1744).

Der nächste Kommentarblock danach (`/* Werteraster statt Chips: … */`) und die Regel `.value-rows` bleiben unverändert stehen — sie sind der Grund, aus dem der Leitwert weicht, nicht sein Anhang.

- [ ] **Step 4: Den Kommentar an `.device-head .device-name` nachziehen**

Dieselbe Datei, ~Zeile 1622. Ersetze im Kommentarblock über `.device-head .device-name` den Satzteil

```
 * anzeigt, dass Text fehlt. An der dokumentierten Grid-Untergrenze
 * (261 px, siehe `.lead-value` unten) bekommt dieses Feld nur noch 65 px
 * und MUSS kuerzen; die beiden Eigenschaften am Ende dieser Regel sorgen
```

durch

```
 * anzeigt, dass Text fehlt. An der dokumentierten Grid-Untergrenze
 * (261 px) bekam dieses Feld frueher nur 65 px, weil `.lead-value`
 * daneben die halbe Kopfzeile beanspruchte und nicht schrumpfte; seit
 * dessen Wegfall (Entwurf 2026-09-07) hat der Name die Kopfzeile
 * abzueglich Icon-Kachel und Abstaenden fuer sich. Kuerzen muss er damit
 * nur noch bei aussergewoehnlich langen Namen - die beiden Eigenschaften
 * am Ende dieser Regel sorgen
```

Der Rest des Blocks (über `min-width: 0` und die intrinsische `size=20`-Mindestbreite) bleibt **unverändert**: diese Begründung gilt weiter, das Feld bleibt ein `<input>`.

- [ ] **Step 5: Die zwei CSS-Tests löschen**

`tests/api/test_web.py`. Lösche vollständig, samt Docstrings:

- `test_lead_value_gets_padding_room_for_descenders` (~Zeile 3088)
- `test_lead_value_does_not_yield_to_the_device_name` (~Zeile 3111)

Beide lesen `css.split(".lead-value {", 1)[1]` und würden nach Step 3 mit `IndexError` scheitern statt mit einer Assertion — ein Test, der an einer gelöschten Regel hängt, ist kein Test mehr.

- [ ] **Step 6: Den Docstring des Namenstests korrigieren**

Dieselbe Datei, `test_device_name_truncates_with_an_ellipsis_instead_of_clipping` (~Zeile 3143). Der Test **bleibt** — das Kürzen mit Ellipse ist weiterhin richtig, nur seltener nötig. Ersetze den ersten Satz des Docstrings

```
    """Fund 1 (Review vom 2026-09-05): mit dem auf `50%` gesenkten Deckel an
    `.lead-value` (s.o.) traegt der Name bei der Grid-Untergrenze immer
    noch die Kuerzung - nur jetzt nicht mehr die vollstaendige. Ein
```

durch

```
    """Fund 1 (Review vom 2026-09-05), nachgezogen 2026-09-07: seit dem
    Wegfall von `.lead-value` hat der Name die Kopfzeile fuer sich und
    kuerzt nur noch bei aussergewoehnlich langen Namen. Dass er es dann
    SICHTBAR tut, bleibt die Zusicherung dieses Tests. Ein
```

Der Rest des Docstrings und beide Assertions bleiben unverändert.

- [ ] **Step 7: Die volle Suite und die Linter laufen lassen**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -v`

Expected: alle vier PASS. `test_the_lead_rules_are_gone_from_the_stylesheet` ist grün, die beiden gelöschten Tests tauchen nicht mehr auf, `test_device_name_truncates_with_an_ellipsis_instead_of_clipping` bleibt grün.

- [ ] **Step 8: Commit**

```bash
git add src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "style(web): .lead-value und .lead-label loeschen

Beide Klassen stehen in keinem Markup mehr. Mit ihnen entfallen die
Begruendung zu flex: 0 0 auto gegen flex: 0 1 auto und das
padding-block gegen abgeschnittene Unterlaengen - beides beschrieb ein
Element, das es nicht mehr gibt.

Der Kommentar an .device-head .device-name behaelt seinen Teil ueber
min-width: 0: das Feld bleibt ein <input>, die intrinsische
size=20-Mindestbreite bleibt abzuraeumen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Am Browser prüfen und die Screenshots nachziehen

Die Suite belegt, **dass** das geänderte Markup und CSS ausgeliefert werden — nicht, wie es aussieht. Die drei offenen Fragen aus Abschnitt 9 des Entwurfs lassen sich nur in einer Engine beantworten.

Kein selbstgebauter Harness: das Repo hat den Weg schon. `scripts/dev_web_server.py --demo` startet eine Oberfläche mit gesäten Demo-Daten und festen Zeitstempeln, und `scripts/capture_screenshots.py` fährt genau die mit Playwright an. Playwright ist eine Ad-hoc-Abhängigkeit und bewusst nicht in `pyproject.toml`.

**Files:**
- Modify: `docs/screenshots/dashboard.png` (zeigt `.room-bar` bis zum zweiten `.device-grid`, also die Gerätekacheln)
- Modify (nur falls Frage 1 es verlangt): `src/loxmatter/web/style.css`, `tests/api/test_web.py`

- [ ] **Step 1: Die Demo-Oberfläche starten**

```bash
uv run python scripts/dev_web_server.py --demo
```

Erwartet: ein Server auf `http://127.0.0.1:8420`, Passwort `loxmatter-demo`. Prüfe im Browser, dass die Demo-Daten mindestens ein Gerät mit mehreren funktionalen Signalen, eines ohne Signale und ein offline stehendes enthalten. Fehlt einer der Fälle, decke ihn in Step 2 über `evaluate` ab, indem du `state.signalsByDevice` im Browser direkt setzt — die Demo-Datenbank ist dafür nicht zu ändern.

- [ ] **Step 2: Die drei Fragen messen**

In der Browser-Konsole auf der Geräteansicht:

```javascript
// Frage 2: fluchten die Fusszeilen einer Reihe?
const feet = [...document.querySelectorAll('.device-grid .device-foot')]
  .map((el) => Math.round(el.getBoundingClientRect().top));
console.log('Fusszeilen-Oberkanten:', feet);

// Frage 3: wie hoch ist eine Kachel jetzt?
const cards = [...document.querySelectorAll('.device-grid .device-card')];
console.log('Kachelhoehen:', cards.map((el) => el.offsetHeight));

// Frage 3, Gegenprobe: dieselbe Messung auf dem Stand VOR dieser Aenderung
// (`git stash push -u -m "leitwert-messung"`, messen, `git stash apply <sha>`)
// - der Unterschied je Kachel gehoert gegen die rund +10 px aus Abschnitt 7
// des Entwurfs gehalten.
```

Auswertung:

1. **Liest die Kachel ohne den großen Wert noch auf einen Blick?** Urteilsfrage, keine Messung. Falls nein: `.value-rows` anheben — **nicht** den Leitwert zurückholen (Entwurf, Abschnitt 9). Weiter mit Step 3.
2. **Fluchten die Fußzeilen?** Alle Werte aus `feet` innerhalb einer Rasterzeile müssen gleich sein. `align-items: stretch` am Raster und `.device-foot { margin-top: auto }` sollen das weiter leisten; die Karte hat ein Kind weniger als vorher, deshalb ist es erneut zu messen und nicht zu behaupten.
3. **Höhendifferenz je Kachel** gegen die Überschlagung von rund +10 px. Es kommt nur auf die Größenordnung an. Weicht sie stark ab, gehört die Zahl in Abschnitt 7 des Entwurfs korrigiert — die Änderung selbst nicht.

Beim Stashen gilt die Worktree-Regel: `git stash push -u -m "leitwert-messung"`, SHA aus `git stash list --format='%H %gs'` merken, mit `git stash apply <sha>` zurückholen, danach den Eintrag gezielt verwerfen. Kein blankes `git stash pop`.

- [ ] **Step 3: Nur falls Frage 1 es verlangt — Schriftgröße anheben, mit Test**

Beide Rasterzellen zusammen, sonst stünde der Titel größer als sein Wert. In `src/loxmatter/web/style.css` `.value-key` und `.value-rows .value` von `font-size: 0.75rem` auf `0.8rem`, und der Test dazu, an `tests/api/test_web.py` angehängt:

```python
async def test_the_value_grid_type_was_raised_after_the_browser_check(api):
    """Task 4, Frage 1: ohne den grossen Leitwert las die Kachel nicht mehr
    auf einen Blick. Die Korrektur ist ein Schriftgroessenschritt im
    Werteraster, nicht die Rueckkehr des Leitwerts (Entwurf, Abschnitt 9).

    BEIDE Rasterzellen, nicht nur eine: Titel und Wert stehen in derselben
    Zeile, eine einseitige Anhebung setzte den Titel groesser als seinen
    Wert. Belegt wird die ausgelieferte Regel, nicht die Lesbarkeit - die
    Messung dazu steht im Aufgabenbericht."""
    client, _, _ = api
    css = (await client.get("/static/style.css")).text
    key_rule = css.split(".value-key {", 1)[1].split("}", 1)[0]
    value_rule = css.split(".value-rows .value {", 1)[1].split("}", 1)[0]
    assert "font-size: 0.8rem" in key_rule
    assert "font-size: 0.8rem" in value_rule
```

Fällt die Antwort auf Frage 1 positiv aus, entfällt dieser Step ersatzlos — dann ist nichts zu korrigieren und nichts zu belegen. Nach der Änderung: `uv run pytest tests/api/test_web.py -v`, erwartet PASS.

- [ ] **Step 4: Die Screenshots neu aufnehmen**

```bash
uv run --with playwright python scripts/capture_screenshots.py
```

Das Skript startet `dev_web_server.py --demo` selbst — den Server aus Step 1 vorher beenden, sonst ist Port 8420 belegt.

Erwartet: `docs/screenshots/dashboard.png` ändert sich (es zeigt `.room-bar` bis zum zweiten `.device-grid`, also die Gerätekacheln). `system.png` ändert sich bei jedem Lauf, weil sein Kommando-Log die HTTP-Anfragen der Aufnahme selbst zeigt — **verwerfen**, nicht mitcommitten. Die übrigen fünf Bilder müssen byte-gleich bleiben; ein Diff dort bedeutet eine echte, unbeabsichtigte Änderung an einer anderen Ansicht.

Run: `git status --short docs/screenshots/`
Expected: nur `dashboard.png` als geändert (nach dem Verwerfen von `system.png`).

- [ ] **Step 5: Commit**

```bash
git add docs/screenshots/dashboard.png
git commit -m "docs(screenshots): Geraetekacheln ohne Leitwert nachziehen

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

Kam aus Step 3 eine Korrektur dazu, gehört sie in einen eigenen Commit davor:

```bash
git add src/loxmatter/web/style.css tests/api/test_web.py
git commit -m "style(web): Werteraster auf 0.8rem anheben

Ohne den grossen Leitwert las die Kachel bei 0.75rem nicht mehr auf
einen Blick (Messung im Aufgabenbericht). Titel und Wert steigen
gemeinsam, sonst stuende der Titel groesser als sein Wert.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Was dieser Plan bewusst nicht tut

- **Den Gerätenamen zu Text machen.** Der Entwurf, Abschnitt 6, verwirft das: das Breitenargument hält nicht, weil `min-width: 0` an `.device-head .device-name` die intrinsische `size=20`-Mindestbreite längst abräumt. Übrig bliebe ein kosmetischer Gewinn gegen einen neuen Menüeintrag, einen Sprachschlüssel in beiden Sprachen und ein Zustandsfeld je offener Umbenennung.
- **Die Liste verdichten.** Die Kachel wird durch diese Änderung höher, nicht niedriger (Entwurf, Abschnitt 7). Das ist bekannt und bezahlt.
- **`FUNCTIONAL_PREVIEW_LIMIT` anfassen.** Sechs Signale bleiben sechs Signale.
