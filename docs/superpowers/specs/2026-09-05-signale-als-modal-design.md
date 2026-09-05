# Signale: vom eigenen Reiter ins Modal der Gerätekachel

Entwurf, 5. September 2026. Löst die Ansicht „Signale" auf und bringt das
Bearbeiten einzelner Signale dorthin, wo das Gerät steht — erreichbar über
das Kebab-Menü aus
[dem Kebab-Entwurf](2026-09-05-kachel-kebab-menue-design.md).

## 1. Das Problem

Die Signalansicht ist ein zweites Verzeichnis derselben Geräte. Sie rendert
für jedes Gerät eine eigene Karte mit Überschrift, obwohl direkt daneben im
Reiter „Geräte" schon eine Kachel desselben Geräts steht — mit demselben
Namen, denselben Live-Werten, denselben Signalen in der Vorschau. Wer den
Titel eines Signals ändern will, verlässt also die Ansicht, in der das Gerät
sichtbar ist, sucht es in einer zweiten, anders sortierten Liste erneut und
kommt danach zurück.

Der `+ N weitere Signale`-Link auf der Kachel macht diesen Umweg sichtbar:
er verspricht die restlichen Signale *dieses* Geräts und liefert einen
Reiterwechsel in eine Liste **aller** Geräte, in der man das eigene wieder
suchen muss.

Dazu kommt der Schalter „Experte anzeigen". Er steht global über allen
Karten und schaltet die Expertengruppe in jeder davon zugleich um — eine
Einstellung ohne Gegenstand, denn interessant ist sie immer nur für das eine
Gerät, das man gerade ansieht.

## 2. Was unverändert bleibt

- **Die API.** `GET /api/devices/{id}/signals`, `PATCH /api/signals/{key}`
  und `POST /api/signals/{key}/write` bleiben unangetastet. Es entsteht keine
  neue Route, kein neues Feld.
- **Die Signalzeile selbst.** Key, Titelfeld, Loxone-Pfad, Live-Wert,
  Export- und Resend-Häkchen, Rohwert-Schreiben bei Attributen — alles zieht
  unverändert um. Kein Funktionsverlust.
- **`signalGroupsFor` (app.js).** Der Helfer bleibt, wie er ist; sein Feld
  `collapsible` unterscheidet schon heute die funktionale von der
  Expertengruppe. Das Modal liest es nur anders (Abschnitt 4).
- **Das Laden.** `startApp` holt die Signale jedes Geräts beim Start; die
  Kachel zeigt sie ohne Klick. Das Modal braucht deshalb keinen eigenen
  Ladeweg.
- **Kopfzeile, Werteraster, Bedienleiste und Fußzeile der Kachel.**

## 3. Was verschwindet

| Ort | Was |
| --- | --- |
| `index.html:242` | Nav-Knopf `t('web.nav.signals')` |
| `index.html:802–930` | die ganze `<section x-show="view === 'signals'">` |
| `app.js:761–770` | der `if (view === "signals")`-Zweig in `selectView` |
| `app.js:404` | `showExpertSignals` |
| `style.css:485–491` | `.signal-group-toggle` |
| `strings.yaml` | `web.nav.signals`, `web.signals.show_expert`, `web.signals.expert_collapsed_hint` |

Der `selectView`-Zweig lud die Signale der Geräte nach, für die noch kein
Eintrag vorlag. Er entfällt ersatzlos: für den Normalfall hat `startApp`
längst geladen, und für den Fehlerfall steht der Wiederholungsknopf
(`web.signals.load_button`) im Modal.

`expert_collapsed_hint` entfällt ersatzlos statt umzuziehen: „12
Expertensignale ausgeblendet" sagt dasselbe wie „Experte (12)" im
`<summary>` — nur nicht an der Stelle, an der man klickt.

### Der bewusste Verlust

Die geräteübergreifende Signalliste geht verloren. Heute kann man in einem
Rutsch über alle Geräte scrollen und Export-Häkchen vergleichen; künftig
sieht man Signale nur noch je Gerät. Die Export-Vorschau ersetzt das nur zur
Hälfte — sie zählt pro Gerät (`inputs`, `skipped`, `hidden_count`), listet
aber keine einzelnen Signale.

Das ist der Preis, und er ist bewusst bezahlt: Der Vergleich über alle
Geräte hinweg ist die seltene Aufgabe, das Bearbeiten eines einzelnen
Signals die häufige. Sollte sich das Bedürfnis nach einer Gesamtübersicht
später melden, gehört sie in die Export-Vorschau, wo der Vergleich der
Export-Häkchen ohnehin hingehört — nicht in einen eigenen Reiter.

## 4. Das Modal

**Ein einziges `<dialog>`** hinter `</main>`, außerhalb jedes `x-for`.

```
<dialog x-ref="signalsModal" class="signals-modal"
        @close="signalsModalDevice = null"
        @click.self="$el.close()">
```

Der Gewinn gegenüber einem `<dialog>` je Kachel ist nicht Sparsamkeit,
sondern dieselbe Sorgfalt, die der Kebab-Entwurf beim `aria-labelledby`
schon einmal aufwenden musste: Markup innerhalb `x-for` wird einmal **pro
Gerät** ausgeliefert. Bei dreißig Geräten lägen dreißig vollständige
Signaltabellen im Dokument, und jede `id` darin dreißigfach.

### Zustand und Öffnen

`signalsModalDevice` hält die Geräte-**ID**, nicht das Objekt: `loadDevices`
ersetzt die Liste vollständig, ein festgehaltenes Objekt wäre danach eine
Leiche mit veraltetem Namen und Raum. Ein Helfer
`signalsModalDeviceObject()` löst die ID gegen `devices` auf.

`openSignalsModal(device)` setzt die ID und ruft **erst im `$nextTick`**
`showModal()`. Die Reihenfolge ist Pflicht, kein Stil: `showModal()` setzt
den Anfangsfokus auf das erste fokussierbare Element im Dialog, und das gibt
es erst, nachdem Alpine den Inhalt gerendert hat.

`x-ref` ist hier zulässig — anders als im Kebab-Menü, dessen Fund 3
ausdrücklich davon abrät. Der dortige Einwand trifft eine Registrierung, die
*pro Kachel* läuft: alle teilen sich das eine `x-data` am `<body>`, und der
Eintrag der zuletzt gerenderten Kachel überschreibt jeden davor. Dieses
`<dialog>` steht genau einmal im Dokument, es gibt niemanden, der es
überschreiben könnte. **Der Kommentar an der Stelle muss diesen Unterschied
ausdrücklich benennen**, sonst liest ihn beim nächsten Anfassen jemand als
Verstoß gegen die bestehende Regel.

### Schließen

`@close` ist die **einzige** Reset-Stelle. Das Ereignis feuert auf jedem
Weg — Escape, Schließen-Knopf, Backdrop, `close()` aus JavaScript —, es gibt
also keinen Pfad, auf dem der Alpine-Zustand und der sichtbare Zustand
auseinanderlaufen können. Dieselbe Rolle, die `@toggle` beim Kebab-`<details>`
spielt.

Die Kopplung „JavaScript-Zustand ↔ nativer Zustand", vor der der
Kebab-Entwurf warnt, ist hier unvermeidbar: ein `<dialog>` **muss**
imperativ geöffnet werden, ein `open`-Attribut allein macht es nicht modal.
Sie wird aber auf diese eine Stelle eingeschnürt statt über vier Handler
verteilt.

Ein `<dialog>` schließt bei einem Klick auf den Backdrop **nicht** von
selbst. `@click.self="$el.close()"` ergänzt das: der Inhalt liegt in einem
Wrapper, ein Ereignis mit dem `<dialog>` selbst als Ziel ist zwingend der
Backdrop.

Der Fokus kehrt ohne Zutun zurück: `close()` gibt ihn dorthin, wo er vor
`showModal()` stand — auf das `<summary>` des Kebabs, weil `closeTileMenu`
ihn unmittelbar davor genau dahin gesetzt hat (siehe Abschnitt 5).

### Inhalt

Von oben nach unten:

1. **Kopf** mit `t('web.signals.modal_heading', { device: label })` und
   einem Schließen-Knopf (`aria-label` aus `web.signals.modal_close`).
2. **`signalsError`-Banner im Modal**, nicht darüber: ein Fehlerbanner
   hinter dem Overlay ist ein unsichtbarer Fehler, und ein unsichtbarer
   Fehler ist nach Spec 8.1 schlimmer als keiner.
3. `t('web.signals.key_hint')` als Hinweiszeile.
4. Der Wiederholungsknopf `t('web.signals.load_button')`, sichtbar nur bei
   `!signalsByDevice[id]`.
5. Die Gruppen aus `signalGroupsFor(signalsModalDevice)`, **beide als
   `<details>`** mit `<summary>` „Funktional (3)" bzw. „Experte (12)". Der
   Auf-/Zu-Zustand lebt damit im DOM statt in Alpine — genau das Muster, mit
   dem der Kebab-Entwurf die sechs Review-Runden des Raum-Auswahlfelds
   vermieden hat, und der Grund, warum `showExpertSignals` ersatzlos
   entfällt.

   **Warum beide Gruppen dasselbe Element bekommen** und nicht, wie
   naheliegend, die funktionale ein schlichter Block bleibt: zwei Formen
   hießen zwei Zweige, und in jedem Zweig eine eigene Kopie der
   Signalzeilen-Vorlage. Genau diese Verdopplung hat `signalGroupsFor`
   abgeschafft (siehe dessen Kommentar in `app.js`: 51 doppelte Zeilen, die
   bei jeder Änderung an beiden Stellen nachgezogen werden mussten). Eine
   Form für beide Gruppen hält es bei einer Vorlage.

   Der Startzustand — funktional offen, Experte zu — wird **einmalig** über
   `x-init="$el.open = !group.collapsible"` gesetzt, nicht über ein
   gebundenes `:open`. Ein `:open` wäre wieder die Kopplung aus Abschnitt 1:
   Alpine wertet Bindungen bei jeder Änderung ihrer Abhängigkeiten neu aus,
   und `signalGroupsFor` hängt an `signalsByDevice` — ein gespeicherter
   Signaltitel schriebe die Bindung neu und klappte die gerade geöffnete
   Expertengruppe wortlos wieder zu. `x-init` läuft einmal je Element; da
   `:key="group.key"` stabil ist, überlebt ein Klick des Nutzers jeden
   Re-Render.

   Der Zustand überlebt das Schließen des Modals nicht; das ist hinnehmbar,
   weil die Expertengruppe je Gerät verschieden interessant ist.
   `functional_vs_expert_explanation` steht im Experten-`<details>` — dort,
   wo er gebraucht wird, statt global über allem. Der Leer-Hinweis
   `none_functional` bleibt an der funktionalen Gruppe.

6. Die Signalzeilen, unverändert die vorhandene `.device-controls`-Vorlage.

Der Inhalt hängt an `x-if="signalsModalDeviceObject()"`, damit ein
Zwischenrender nach dem Entfernen eines Geräts nicht gegen `undefined`
läuft.

Live-Werte laufen unverändert weiter: der Websocket kennt das Modal nicht,
`liveValueOf` und `signalIsFresh` binden wie zuvor. Ein offenes Modal
aktualisiert sich also von selbst.

### Wenn das Gerät verschwindet

`removeDevice` schließt das Modal, wenn es genau dieses Gerät zeigt. Ohne
das bliebe ein Dialog über einem Gerät offen stehen, das es nicht mehr gibt —
und der `x-if`-Wächter machte ihn zu einem leeren Kasten ohne erkennbaren
Grund.

## 5. Die zwei Einstiege

**Kebab-Menü.** Ein neuer Eintrag nach der Trennlinie, **über**
„Exportieren":

```
@click="closeTileMenu($el); openSignalsModal(device)"
```

Die Reihenfolge trägt den Fokus: `closeTileMenu` schließt das `<details>`
und setzt den Fokus auf dessen `<summary>`; das unmittelbar folgende
`showModal()` merkt sich genau diesen Fokus als Rückkehrpunkt. Dieselbe
Reihenfolge wie bei „Exportieren" und „Entfernen" daneben.

**Der `+ N weitere Signale`-Link** (`index.html:462`) wechselt sein Ziel von
`selectView('signals')` auf `openSignalsModal(device)`. Damit führt er
dorthin, wo die versprochenen Signale wirklich stehen — statt in eine Liste,
in der man das Gerät erneut suchen muss.

## 6. Übersetzung

Neu:

| Schlüssel | en | de |
| --- | --- | --- |
| `web.devices.menu_signals` | Edit signals… | Signale bearbeiten… |
| `web.signals.modal_heading` | Signals — {device} | Signale — {device} |
| `web.signals.modal_close` | Close | Schließen |

Der Platzhalter in `modal_heading` ist unbedenklich: `_web_strings()`
(`api/language.py:56`) liefert unaufgelöste Vorlagen über `raw_template()`,
gerade damit `web.*`-Schlüssel Platzhalter tragen dürfen. Der Browser füllt
sie in `t()`.

Entfallen: `web.nav.signals`, `web.signals.show_expert`,
`web.signals.expert_collapsed_hint`.

## 7. Aussehen

`.signals-modal` mit `width: min(46rem, 92vw)`, `max-height: 85vh` und
`overflow: auto` — ein Gerät mit vierzig Attributen darf scrollen, nicht
über den Rand wachsen. Dazu ein `::backdrop`.

Die Signalzeile erbt `.device-controls` unverändert; das Experten-`<details>`
das `<summary>`-Muster der Projektdatei-Sync-Aufklapper. Keine neue Farbe,
keine neue Schriftgröße, kein neues Primitiv.

## 8. Tests

**Anzupassen** in `tests/api/test_web.py`:

- `:108` und `:796` — die Fünfertupel der Ansichten werden Vierertupel.
- `:1051` — `device_section_end` ankert auf `x-show="view === 'signals'"`;
  dieser Anker verschwindet und wandert auf `'export'`.
- `:1249–1270` — der Übersetzungstest der Signalansicht richtet sich aufs
  Modal; die Zusicherungen zu `show_expert` und `expert_collapsed_hint`
  fallen weg.

**Neu:**

- Kein `view === 'signals'` mehr im Markup, kein `showExpertSignals` in
  `app.js`.
- **Genau ein** `<dialog` im ausgelieferten Dokument — der Beleg dafür, dass
  es bei einem Exemplar bleibt und nicht eines je Kachel wird.
- Der Kebab-Eintrag trägt `closeTileMenu($el); openSignalsModal(device)` und
  `t('web.devices.menu_signals')`.
- Der `+ N weitere Signale`-Link zeigt auf `openSignalsModal`, nicht mehr
  auf `selectView('signals')`.
- `@close` setzt `signalsModalDevice = null` (die eine Reset-Stelle).
- `openSignalsModal` ruft `showModal()` im `$nextTick`.
- `removeDevice` schließt ein Modal, das auf das entfernte Gerät zeigt.

**Browser-Verifikation zusätzlich, nicht ersatzweise.** Diese Tests lesen
ausgelieferten Text; sie belegen, *dass* etwas ausgeliefert wird, nicht dass
es wirkt. Ob `showModal()`, Fokusfang, Escape, Backdrop-Klick und der
`@close`-Reset im Zusammenspiel tatsächlich funktionieren, zeigt erst ein
Wegwerf-Harness mit laufendem Alpine. Das ist eine eigene Aufgabe im Plan,
keine Fußnote.

## 9. Dokumentation

`docs/screenshots/signals.png` zeigt einen Reiter, den es nicht mehr gibt.
Der Screenshot wird neu aufgenommen — das offene Modal über dem
Geräteraster —, und die Bildunterschrift in `README.md:115–117` nennt statt
des Reiters den Weg über das Kebab-Menü. Die Produktseite behält ihre sechs
Kacheln und ihr zweispaltiges Raster.
