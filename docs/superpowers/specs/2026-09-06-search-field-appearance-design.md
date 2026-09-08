# Suchfeld der Geräteansicht: eigene Gestalt statt Systemkasten

Entwurf, 6. September 2026. Betrifft die Raumleiste aus
[dem Geräte-Tab-Entwurf](2026-09-05-devices-tab-rooms-and-tile-grid-design.md),
Abschnitt 6.3 — dort ist das Suchfeld eingeführt worden, aber nie gestaltet.

## 1. Das Problem

Das Suchfeld fällt durch das CSS-Raster. Die Formularregel in `style.css`
listet `input[type="text"]`, `[type="number"]`, `[type="password"]` und
`select` — `search` steht nicht darunter. `.device-search` setzt nur
`min-width` und `font-size`. Alles Übrige zeichnet der Browser selbst:
eckige Ecken, sein eigener Rahmen, seine eigene Schrift.

Im Dunkelmodus greift damit **keine** der Projektfarben. Der Kasten holt
sich Hintergrund und Textfarbe aus dem Systemstil, und `color-scheme: light
dark` rettet nur den groben Kontrast, nicht die Zugehörigkeit — das Feld
gehört sichtbar nicht zu der Oberfläche, in der es steht.

Auf dem Dashboard-Screenshot sieht man es unmittelbar: rechts oben steht
ein kantiger Systemkasten in einer Zeile, die sonst aus gerundeten Chips
besteht.

Dazu fehlt jedes Zeichen, *dass* es eine Suche ist. Keine Lupe. Kein eigenes
Löschkreuz — nur das browserabhängige, das WebKit einblendet und Firefox
nicht. Kein Fokusring aus der Palette. Und keine Rückmeldung, ob die Suche
überhaupt greift: bei vielen Geräten muss man scrollen, um zu sehen, dass
nichts übrig blieb.

## 2. Die Randbedingung

Der Kopfkommentar von `style.css` hält fest, dass diese Oberfläche **bewusst
schmucklos** ist: „Klarheit vor Wirkung — Farbe wird nur eingesetzt, wo sie
eine Bedeutung trägt, nicht als Dekoration."

Dieser Entwurf ändert daran nichts. „Schicker" heißt hier *sauber und
selbsterklärend*, nicht *dekoriert*. Es kommt keine Farbe hinzu, die nicht
schon einen Zustand bezeichnet: die Akzentfarbe markiert den Fokus, alles
andere ist Rahmen, Fläche und gedämpfter Text aus den vorhandenen Variablen.

## 3. Was unverändert bleibt

- **Die Suchlogik.** `matchesSearch()`, `visibleDevices()` und
  `hitsOutsideRoom()` bekommen kein Zeichen. Der Zähler liest nur, was
  ohnehin schon gerechnet wird.
- **Die Raum-Chips.** Ihre Zahlen bleiben Raumgrößen und folgen der Suche
  nicht. Ein Chip beantwortet „wie groß ist dieser Raum", der Zähler
  beantwortet „wie viele Treffer stehen unten" — zwei Fragen, zwei Zahlen,
  an zwei Orten.
- **Der Hinweis „N weitere Treffer in anderen Räumen"** unter der Leiste,
  samt seinem Link auf alle Räume.
- **`deviceSearch`** als das eine Feld, an dem alles hängt.

## 4. Der Aufbau

Ein Flex-Container mit vier Kindern in einer Reihe:

```
┌──────────────────────────────────────────────┐
│ 🔍  Name, Kategorie, Raum suchen   3 Treffer ✕│
└──────────────────────────────────────────────┘
```

**Der Rahmen sitzt am Container, nicht am Feld.** Das ist die tragende
Entscheidung dieses Entwurfs. Das `<input>` darin ist rand- und
hintergrundlos; Lupe, Zähler und Kreuz sind seine Geschwister im selben
Flex-Fluss. Daraus folgt dreierlei ohne weiteres Zutun:

- Keine der drei Beigaben braucht absolute Positionierung, also auch kein
  Polster, das zur Icongröße passen muss. Jede solche Zahl wäre eine
  Kopplung, die beim nächsten Schriftgrößen-Dreh bricht.
- Der Fokusring hängt per `:focus-within` am Container und umschließt damit
  die ganze Gruppe. Läge er am `input:focus`, umschlösse er nur deren Mitte
  — sichtbar falsch, sobald Lupe und Kreuz dabei sind.
- Die Gruppe wächst mit der Schriftgröße mit, weil alle Maße in `em`/`rem`
  stehen und `.icon` ohnehin `1.1em` misst.

**Ein `<div>`, kein `<label>`.** Ein `<button>` innerhalb eines Labels löst
dessen Weiterleitung an das gelabelte Bedienelement mit aus; das Löschen
soll ein Klick sein, nicht zwei Ereignisse. Der Preis ist ein schmaler
Streifen Polster, der nicht ins Feld fokussiert — vier Pixel, gegen eine
Ereignis-Doppelung eingetauscht.

Die Lupe bekommt `pointer-events: none`, damit sie kein Klickloch in die
linke Kante schlägt.

## 5. Die Symbole

Die Lupe kommt als neues `#i-search` in den bestehenden Inline-Sprite in
`index.html` — gleiche Strichtechnik wie `#i-rename` und die
Kategorie-Icons: nur Pfade, `fill: none`, `currentColor`. Weiterhin inline
und ohne Icon-Bibliothek, aus demselben Grund wie das eingecheckte
`vendor/alpine.min.js`: die Oberfläche läuft offline.

Das Löschkreuz braucht **kein** neues Symbol. `#i-close` gibt es schon, es
zeigt genau diese Form, und ein zweites Kreuz danebenzustellen hieße, sich
beim nächsten Strichstärken-Dreh an zwei Stellen zu erinnern.

## 6. Zähler und Kreuz

Beide hängen an `deviceSearch.trim()` und tragen `x-cloak`: bei leerem Feld
sind sie weg, und die Zeile bleibt beim ersten Zeichnen ruhig, bevor Alpine
initialisiert hat.

Der Zähler zeigt `visibleDevices().length` — also das, was tatsächlich unter
der Leiste steht, einschließlich eines aktiven Raumfilters. Er beantwortet
damit die Frage, die man beim Tippen hat („greift das?"), und nicht eine
allgemeinere, die schon der Hinweis aus Abschnitt 3 abdeckt.

Er bekommt `font-variant-numeric: tabular-nums`. Ohne das wackelt die rechte
Kante beim Wechsel von 9 auf 10, und dann zappelt das Kreuz daneben mit —
dasselbe Zappeln, das die Altersangabe an der Signalzeile schon einmal
gekostet hat.

**WebKits eigenes Löschkreuz muss weg.** Ein `input[type="search"]` bekommt
dort `::-webkit-search-cancel-button` eingeblendet; daneben stünde unseres
ein zweites Mal. Die Regel schaltet es ab, und zwar mit `-webkit-appearance`
*und* `appearance`, weil das Pseudoelement selbst herstellerspezifisch ist.

## 7. Breite und Lage

Eine Regel, drei Verhalten, ohne Sonderklassen:

```css
.search-field { flex: 1 1 12rem; max-width: 22rem; }
```

Der heute fest im Markup stehende Abstandhalter (`<span style="flex: 1 1
auto">`) wandert dafür in ein eigenes `<template x-if="hasAnyRoom()">`.

- **Mit Raum-Chips:** beide wachsen, das Feld ist bei 22rem gedeckelt, der
  Abstandhalter schluckt den Rest. Das Feld sitzt rechts wie bisher, aber
  deutlich großzügiger als mit den 12rem von heute.
- **Ohne Raum-Chips:** kein Abstandhalter, also rückt das Feld an die linke
  Kante — auf eine Sichtachse mit dem Kachelraster darunter.
- **Schmales Fenster:** die Leiste bricht wie bisher (`flex-wrap: wrap`),
  das Feld liegt allein auf seiner Zeile und füllt sie bei üblichen
  Telefonbreiten praktisch aus (375 px ≈ 23,4rem gegen 22rem Deckel).

Zur zweiten Zeile ausdrücklich: das Feld dehnt sich dort **nicht** über die
volle Zeilenbreite. Ein 1600 px breites Suchfeld über vier Kacheln wäre ein
schlechterer Anblick als der einsame Kasten, den es zu beseitigen gilt. Die
linke Ausrichtung löst das Problem — die Streckung wäre nur ein zweites.

## 8. Sprache

Zwei neue Schlüssel in `strings.yaml`:

```yaml
web.devices.search_count:
  en: "{count} found"
  de: "{count} Treffer"
web.devices.search_clear:
  en: "Clear search"
  de: "Suche leeren"
```

Ohne Plural-Sonderfall — „1 found" und „1 Treffer" lesen sich beide richtig,
und die Tabelle kennt an keiner Stelle eine Pluralform. `t()` in `app.js`
löst `{count}` über sein `values`-Argument auf; `GET /api/i18n` liefert die
Vorlage unaufgelöst aus, wie für jeden Platzhalter-Schlüssel.

`search_clear` beschriftet `title` **und** `aria-label` des Kreuzes: es trägt
kein Wort, also braucht es eines.

Das Eingabefeld bekommt zusätzlich ein `aria-label` aus dem vorhandenen
`search_placeholder`. Bisher trägt es nur den Platzhalter — und der
verschwindet genau dann, wenn jemand etwas eingegeben hat.

## 9. Prüfung

Vier Tests in `tests/api/test_web.py`, nach dem dort etablierten Muster:
geprüft wird das **ausgelieferte** Markup und CSS, nicht das Bild im
Browser — in dieser Suite läuft keine Engine, die CSS anwendet oder Alpine
ausführt.

1. **Ein Rahmen, nicht zwei.** Der Container trägt `border` und
   `border-radius`, das `input` darin trägt `border: none`. Stünden beide,
   läge ein Rahmen im anderen.
2. **Der Fokusring hängt an `:focus-within`.** Belegt, dass er die Gruppe
   umschließt und nicht nur das Feld in ihrer Mitte.
3. **WebKits Kreuz ist abgeschaltet, unseres ist da.** Die
   `::-webkit-search-cancel-button`-Regel steht im CSS, und das Markup
   verweist auf `#i-close`.
4. **Zähler und Kreuz sind an die Eingabe gebunden und getarnt.** Beide
   hängen an `deviceSearch` und tragen `x-cloak`.

Das neue `#i-search` deckt `test_the_inline_icon_symbols_are_well_formed_xml`
automatisch mit ab.

Nach der Umsetzung wird `docs/screenshots/dashboard.png` neu aufgenommen —
das Bild zeigt heute den Systemkasten und wäre sonst sofort veraltet. Der
Aufnahmeweg über `scripts/` steht fest, samt festgenageltem Demo-Zeitstempel.

## 10. Offene Punkte

Keine. Die Tastenkürzel zum Fokussieren, die Trefferhervorhebung in den
Kacheln und der gestaltete Leerzustand sind beim Zuschnitt bewusst
ausgeschieden worden: sie berühren `app.js` und die Kacheldarstellung, also
mehr als die Suchleiste. Sollten sie später kommen, ist dieser Entwurf ihre
Grundlage und kein Hindernis.
