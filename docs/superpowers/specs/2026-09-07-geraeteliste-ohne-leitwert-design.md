# Gerätekachel ohne Leitwert: alle Signale gleichrangig

Entwurf, 7. September 2026. Ändert die Kopfzeile der Gerätekachel aus
[dem Geräte-Dashboard-Entwurf](2026-09-03-geraete-dashboard-und-export-design.md),
Abschnitt 6.2, und räumt damit einen Teil von
[dem Geräte-Tab-Entwurf](2026-09-05-geraete-tab-raeume-und-kachelraster-design.md)
wieder ab.

## 1. Das Problem

Die Kachel zeigt ein Signal anders als alle anderen. `leadSignalFor()`
greift das erste funktionale Signal heraus und stellt es in `1.35rem`
rechts in die Kopfzeile; sein Titel steht als `.lead-label` in `0.65rem`
Versalien links unter dem Gerätenamen. Die übrigen bis zu fünf Signale
stehen darunter im Werteraster, in `0.75rem`, Titel links und Wert rechts.

Dieselbe Sorte Ding, zwei Darstellungen. Bei einem Heizkörper steht
„Temperatur" oben links klein in Versalien und „Luftfeuchte" zwei Zeilen
tiefer normal gesetzt in der Titelspalte — obwohl beides Signaltitel sind
und beide Werte aus derselben Quelle kommen.

Das kostet zweierlei:

**Die Breite des Namens.** `.lead-value` trägt `max-width: 50%` und
`flex: 0 0 auto` — es schrumpft nie, das war eine bewusste Entscheidung
(die Begründung steht ausführlich an der Regel). Der Name bekommt damit an
der Grid-Untergrenze von 261 px noch 65 px. Praktisch jeder
Loxone-Gerätename ist dort abgeschnitten, und zwar an der falschen Stelle:
Namen dieser Art unterscheiden sich am Ende („… Nord" gegen „… Süd"),
gekappt wird das Ende.

**Die Lesbarkeit als Menge.** Wer prüfen will, ob eine Kachel plausibel
aussieht, liest sechs Signale. Fünf davon fluchten in einer Spalte, eines
steht woanders. Das Werteraster hat für genau diesen Zweck eine Begründung
im Stylesheet — „man scannt eine Spalte statt zwölf Bausteine" —, und der
Leitwert ist die Ausnahme, die sie unterläuft.

## 2. Die Entscheidung

Der Leitwert entfällt ersatzlos. Alle funktionalen Signale stehen
gleichrangig im Werteraster, in einer Schriftgröße, in einer Spalte.

Das ist ausdrücklich **kein** Verdichtungsentwurf. Die Kachel wird dadurch
nicht kleiner (siehe Abschnitt 7), und die Länge der Liste bei achtzig
Geräten bleibt, wie sie ist. Es geht um Gleichrangigkeit und um die Breite
des Namens.

Verworfen wurden dabei drei Entwürfe, die den Leitwert behalten und
stattdessen die Zeile verdichten wollten (Zeilenliste, Anomalie-Triage,
Dichteschalter): sie alle zeigen nur den Leitwert und schieben die übrigen
Signale hinter ein Aufklappen. Oft sind mehrere davon gleichzeitig wichtig.
Ebenfalls verworfen: Chipleisten statt des Werterasters — sie sparen Höhe,
zerstören aber genau die Spaltenflucht, für die das Raster existiert.

## 3. Was unverändert bleibt

- **Die Signaldaten.** `functionalSignalsFor()`, `firstSignalsFor()`,
  `remainingSignalCount()` und `FUNCTIONAL_PREVIEW_LIMIT: 6` bekommen kein
  Zeichen. Sechs Signale je Kachel bleiben sechs Signale je Kachel.
- **Die Reihenfolge der Signale.** Sie war schon bisher die der API; der
  Leitwert war nur der erste Eintrag daraus, nicht eine eigene Auswahl.
- **`wahr`/`falsch`.** Boolesche Werte bleiben technisch beschriftet.
  Dieser Entwurf rührt `formatValue()` nicht an.
- **Der Farbstreifen** links an der Karte, samt seiner drei Zustände, und
  die Geändert-Pille in der Fußzeile.
- **`value-fresh`** an frisch eingetroffenen Werten — künftig nur noch im
  Werteraster, weil es die einzige Stelle ist, an der Werte stehen.
- **Das Kachelmenü**, die Befehlsleiste, die Fußzeile, das Signale-Modal
  und der Hinweis „+ N weitere Signale".

## 4. Die Kopfzeile

Zwei Kinder statt drei:

```
┌────────────────────────────────────────────┐
│ [◧]  Heizkörper Wohnen West                │
├────────────────────────────────────────────┤
│ Temperatur                        21.40 °C │
│ Luftfeuchte                         44.00 %│
│ Solltemperatur                    21.00 °C │
│ Ventilstellung                      34.00 %│
│ Batterie                            88.00 %│
│ Fenster offen                        falsch│
└────────────────────────────────────────────┘
```

`.device-ident` behält seine Rolle als schrumpfende Mitte, hat aber nur
noch ein Kind. `min-width: 0` bleibt an ihm **und** am Namen: der Grund
dafür war nie der Leitwert, sondern die intrinsische Mindestbreite eines
`<input>` beziehungsweise das automatische Minimum eines Flex-Kindes.

`.device-head` behält `align-items: flex-start`. Bei einer einzeiligen
Mitte ist der Unterschied zu `center` unsichtbar, solange keine Pille
danebensteht; mit Pille soll beides oben stehen, nicht mittig zum
Icon zentriert.

## 5. Die Offline-Pille

Sie rückt aus `.device-ident` heraus in die Kopfzeile selbst, an den
Platz des Leitwerts. `.status-pill` trägt bereits `margin-left: auto` —
in der Flex-Kopfzeile schiebt sie sich damit ohne weiteres Zutun nach
rechts.

Damit entfällt die Regel „nur die Offline-Pille verdrängt das
Leitwert-Label": es gibt kein Label mehr, das verdrängt werden könnte. Die
Bedingung schrumpft von
`x-show="isOnline(device) && leadSignalFor(device.id)"` auf ein schlichtes
`x-show="!isOnline(device)"` an der Pille — und die Kopplung an
`isOnline` verschwindet aus dem Label ersatzlos, weil es das Label nicht
mehr gibt.

## 6. Nicht in diesem Entwurf: der Name als Text

Naheliegend wäre, den Namen aus dem dauerhaft sichtbaren `<input>` in
reinen Text zu verwandeln und das Feld erst beim Umbenennen einzublenden.
Das war ursprünglich als zweiter Teil geplant, mit der Begründung, das
Textfeld koste über sein `size=20` rund 192 px Mindestbreite.

**Diese Begründung hält nicht.** `.device-head .device-name` trägt bereits
`min-width: 0`, und zwar mit ausführlichem Kommentar an der Regel — genau
diese intrinsische Mindestbreite ist dort schon abgeräumt worden. Das Feld
kostet heute keine Breite mehr, die der Text nicht auch kostete.

Übrig bleiben zwei kosmetische Argumente: ein Name, der immer wie ein
Formularfeld aussieht, lädt zum Verändern ein, obwohl man meistens nur
liest; und ein Feld mit `border: 1px solid transparent` ist ein Element,
das seinen Zustand über Hover verrät statt über seine Gestalt. Beides ist
wahr und beides ist klein.

Dagegen steht echter Aufwand: das Kachelmenü führt *Signale*,
*Exportieren* und *Entfernen*, aber **kein** *Umbenennen* — das läuft
ausschließlich über das sichtbare Feld. Der Einstieg müsste erst entstehen,
samt Menüeintrag, Sprachschlüssel in beiden Sprachen und einem Zustandsfeld
je offener Umbenennung.

Für einen kosmetischen Gewinn ist das zu viel. Der Name bleibt ein
`<input>`. Wer den Umbau später doch will, hat mit `renamingRoom` /
`renameDraft` an der Raumüberschrift die Vorlage.

## 7. Die Höhe

Ehrlichkeit vor Verkaufe: die Kachel wird **höher**, nicht niedriger.

Überschlagen, bei `font-size: 14px` und `line-height: 1.5`:

| | |
|---|---|
| Werteraster gewinnt eine Zeile (`0.75rem` × 1.5 + `0.05rem` Zeilenabstand) | **≈ +16 px** |
| Kopfzeile verliert die Label-Zeile (`0.65rem` × 1.5 plus `0.1rem` Abstand), soweit die Icon-Kachel mit ihren 33,6 px das nicht auffängt | **≈ −6 px** |
| **Netto je Kachel, überschlagen** | ≈ +10 px |
| **Netto je Kachel, gemessen** | **+14 px** |

Bei achtzig Geräten und drei Spalten sind das rund 380 px zusätzliche
Scrollstrecke.

Nachgetragen am 7. September 2026: im Browser gemessen (Chromium über
Playwright, Demo-Daten) ergaben alle vier Kacheln durchgängig **+14 px** —
284→298 und 265→279, je zweimal. Die Überschlagung lag 40 % darunter, weil
der zweite Posten unsicher war: ein `<input>` erbt `line-height` nicht
zuverlässig, die Kopfzeile verliert also weniger, als angenommen. Am
Vorzeichen ändert das nichts, und die Größenordnung stimmt.

Die Rechnung gilt für Geräte mit mindestens einem funktionalen Signal. Ein
Gerät ohne Signale wird niedriger, weil die Kopfzeile eine Zeile verliert
und nichts hinzukommt.

## 8. Was dabei von selbst wegfällt

Der Wegfall des Leitwerts löscht eine ganze Klasse von Fehlern mit.

`test_a_device_without_a_lead_signal_does_not_throw_in_any_binding`
beschreibt sie: zwischen `GET /api/devices` und
`GET /api/devices/<id>/signals` liegt ein Rendering-Durchlauf, in dem
`signalsByDevice` für das Gerät noch leer ist. `leadSignalFor()` liefert
dann `null`, und `x-show` auf der Hülle hält Alpine **nicht** davon ab, die
Ausdrücke der Kinder auszuwerten — `signalIsFresh(null)`,
`signalAgeTitle(null)` und `liveValueOf(null)` liefen dreimal je Gerät ins
Leere. Das traf nicht kaputte Daten, sondern jedes Gerät einmal.

Ein `x-for` über ein leeres Array wertet dagegen gar nichts aus. Die
Fehlerquelle verschwindet mit der Ursache, nicht mit einer weiteren
Absicherung.

**Die Absicherung bleibt trotzdem stehen.** Die drei Helfer behalten ihre
Null-Toleranz und der Test behält seinen Zweck; nur sein Aufhänger wechselt
vom Leitwert auf die Helfer selbst. Eine Duldsamkeit zu entfernen, weil der
eine bekannte Aufrufer weg ist, wäre die Sorte Aufräumen, die beim nächsten
Aufrufer zurückschlägt.

## 9. Was zu prüfen ist

- **Am Browser**, nicht am Markup: ob die Kachel ohne den großen Wert noch
  auf einen Blick liest. Die Vermutung ist ja — die Wertespalte wird als
  Spalte gescannt, nicht Zeile für Zeile gelesen —, aber sie ist eine
  Vermutung. Falls nein, ist die kleinste Korrektur eine Anhebung von
  `.value-rows` auf `0.8rem`, nicht die Rückkehr des Leitwerts.
  `.value-rows` bleibt in diesem Entwurf bei `0.75rem`.
- **Die Kachelhöhen in einer Reihe.** `align-items: stretch` und
  `.device-foot { margin-top: auto }` sollen weiter fluchtende Fußzeilen
  ergeben; das ist nach einer Änderung an der Kinderzahl der Karte erneut
  zu messen, nicht zu behaupten.
- **Die tatsächliche Höhendifferenz** je Kachel, gegen die Überschlagung
  in Abschnitt 7. Interessant ist nur, ob sie in der Größenordnung liegt —
  auf zwei Pixel kommt es nicht an.
- **Die Screenshots** in `docs/screenshots/`, soweit sie Gerätekacheln
  zeigen.

Sprachdateien sind nicht betroffen: der Leitwert-Titel kam aus
`signal.title`, also aus den Daten, nicht aus `strings.yaml`.
`web.devices.offline` und `web.devices.no_functional_signals` bleiben in
Gebrauch.

## 10. Betroffene Stellen

**Entfällt:**

| Stelle | |
|---|---|
| `app.js` | `leadSignalFor()`, `restSignalsFor()` |
| `style.css` | `.lead-label`, `.lead-value`, `.lead-value small` |
| `index.html` | `.lead-value`-Block und `.lead-label`-Span in `.device-head` |
| `test_web.py` | `test_the_lead_label_only_yields_to_the_offline_pill_now`, `test_lead_value_gets_padding_room_for_descenders` |

**Ändert sich:**

| Stelle | |
|---|---|
| `index.html` | `.value-rows` läuft über `firstSignalsFor()` statt `restSignalsFor()`; Offline-Pille wandert in `.device-head`; der Hinweis auf fehlende Signale hängt an `functionalSignalsFor(id).length === 0` statt an `!leadSignalFor(id)` |
| `style.css` | Kommentar an `.device-head .device-name` verliert seinen Verweis auf die 261-px-Rechnung mit `.lead-value` |
| `app.js` | Kommentar bei Zeile ~1616 verweist auf `leadSignalFor` |
| `test_web.py` | Helferliste (~Zeile 2600) verliert zwei Einträge; `test_a_device_without_a_lead_signal_…` wechselt den Aufhänger; die Docstrings zweier weiterer Tests verweisen auf den Leitwert |

Der Umfang ist damit klein und in sich geschlossen: eine Kopfzeile, ein
`x-for`-Aufruf, zwei gelöschte Alpine-Methoden, drei gelöschte CSS-Regeln
und die Tests, die daran hängen.
