# Signale nach Bedeutung ordnen — und das Signal-Modal lesbar machen

Entwurf, 7. September 2026. Betrifft die Gerätekachel aus
[dem Geräte-Tab-Entwurf](2026-09-05-geraete-tab-raeume-und-kachelraster-design.md)
und das Signal-Modal aus [Signale als Modal](2026-09-05-signale-als-modal-design.md).
Berührt außerdem `export/signals.py`, weil die Sortierung dort dieselbe
Quelle hat (Abschnitt 5).

## 1. Der Befund

Ein „Haupt-Signal" wird nirgends *gewählt*. Der Leitwert der Kachel ist das
erste Element einer Liste:

```js
leadSignalFor(deviceId) {
  return this.firstSignalsFor(deviceId)[0] || null;
}
```

`firstSignalsFor` schneidet `functionalSignalsFor` auf sechs zu, und die
kommt unverändert aus `GET /api/devices/<id>/signals`, das seinerseits
`Store.signals` liest — sortiert mit

```sql
ORDER BY endpoint, cluster_id, element_id, kind
```

Das ist eine rein technische Ordnung. Sie beantwortet „wo im Matter-Baum
steht das", nicht „was ist das hier für ein Gerät". Und weil Matter den
**PowerSource-Cluster (47) auf Endpunkt 0** trägt, während das eigentliche
Nutz-Cluster auf Endpunkt 1 oder 2 sitzt, gewinnt bei **jedem**
batteriebetriebenen Gerät automatisch der Batteriestand.

An den vier eingelernten Komponenten nachgerechnet (die Kacheln kommen aus
`scripts/dev_web_server.py` über die Abbilder in `tests/fixtures/nodes/`;
`~/.loxmatter/loxmatter.sqlite` ist leer und noch auf dem Schema vor
Raum/Kategorie):

| Gerät | Leitwert heute | Herkunft | Richtig wäre |
| --- | --- | --- | --- |
| Hallway button (IKEA BILRESA) | `battery` 12,4 % | `0/47/12` | `press` |
| Living room lamp | `VendorName` | `0/40/1` | `onoff` |
| Kitchen spots | `onoff` | `1/6/0` | — |
| Coffee machine (GRILLPLATS) | `onoff` | `1/6/0` | — |

Zwei von vier falsch, und beide aus demselben Grund: Endpunkt 0 sortiert vor
Endpunkt 1, und dort steht nur Verwaltung.

**Der `VendorName`-Fall ist zur Hälfte ein Abbild-Artefakt und darf nicht
als zweiter Fehler gezählt werden.** `example_light.json` trägt auf keinem
Endpunkt einen Descriptor (`<ep>/29/0` fehlt vollständig, gegen die Abbilder
geprüft) — damit greift die Verwaltungs-Endpunkt-Schicht in
`relevance.is_functional` nicht, und die BasicInformation-Attribute gelten
als funktional. Ein zertifiziertes Gerät deklariert dort `RootNode` und
fällt heraus. Die *Schwäche* ist trotzdem dieselbe und wird von diesem
Entwurf mit erschlagen: der Leitwert ist heute „was zuerst sortiert", nicht
„was zählt".

## 2. Der zweite Befund: das Modal

Das Signal-Modal (`index.html`, `<dialog class="signals-modal">`) zeigt pro
Signal eine `.row` mit sieben Bedienelementen — Schlüssel-Pille, Titelfeld,
Pfad, Wert, Kontrollkästchen „exportieren", Kontrollkästchen „periodisch
erneut senden", und bei jedem Attribut zusätzlich eine zweite `.row` über
die volle Breite mit Rohwert-Feld und Schaltfläche. Bei 17 funktionalen
Signalen des Tasters sind das über hundert Elemente ohne jede Hierarchie.

Fünf konkrete Ursachen, am Screenshot `docs/screenshots/signals.png`
ablesbar:

1. **Keine Spaltenköpfe.** `1/59/2` steht unkommentiert da.
2. **Nichts fluchtet.** Die `.row` ist ein `flex-wrap`-Container ohne
   Spaltenmaße; bei `multipress_ongoing` rutscht „periodisch erneut senden"
   allein in die nächste Zeile. Das Auge findet keine Spalte.
3. **Namensdopplung ohne Auflösung.** `press` steht zweimal in der Liste —
   `1/59/1` und `2/59/1`, also zwei verschiedene Tasten derselben
   Fernbedienung, gleich beschriftet. Nichts sagt, welche welche ist.
4. **Das Rohwert-Feld hat dasselbe Gewicht wie alles andere.** Ein
   Werkzeug zum Ausprobieren beansprucht bei jedem Attribut eine volle
   Zeile.
5. **Keine Gliederung.** 17 funktionale Signale als flache Liste, darunter
   156 unter „Experte".

## 3. Die Entscheidungen

Aus dem Entwurfsgespräch, alle vier bestätigt:

1. **Cluster-Rangliste**, nicht ein Leit-Cluster je Kategorie und nicht
   bloßes Abwerten von Endpunkt 0. Eine Rangliste trägt auch für
   Gerätetypen, die dieses Werkzeug nie gesehen hat — dieselbe Begründung,
   mit der `relevance.py` sich auf Matters eigenen Aufbau stützt statt auf
   eine Liste von Cluster-Nummern, die jemand für langweilig hält.
2. **Die Rangliste sortiert die ganze Kurzliste**, nicht nur den Leitwert.
   Eine Regel statt zweier; die Zeilen unter der Überschrift lesen sich
   dann genauso nach Wichtigkeit wie die Überschrift selbst.
3. **Sortiert wird an der Quelle.** WebUI *und* Loxone-Vorlage folgen
   derselben Ordnung (Abschnitt 5).
4. **Die Batterie bekommt eine eigene Zeile auf der Kachel** statt aus der
   Vorschau zu fallen (Abschnitt 6).
5. **Das Modal wird eine Tabelle mit Endpunkt-Gruppen** (Variante A aus dem
   Entwurfs-Canvas, Abschnitt 7).

## 4. Die Rangliste

Sie lebt als `rank:` je Cluster in **`profiles/clusters.yaml`** — derselben
Datei, die für diesen Cluster schon Titel, Einheit und Skalierung führt.
Kein zweiter Ort, an dem Cluster-Wissen steht, und kein Python-Wörterbuch
neben einer YAML-Tabelle, die dieselbe Frage schon halb beantwortet.

```yaml
clusters:
  6:            # OnOff
    rank: 10
  59:           # Switch
    rank: 10
  1026:         # TemperatureMeasurement
    rank: 10
  1029:         # RelativeHumidityMeasurement
    rank: 10
  8:            # LevelControl
    rank: 20
  768:          # ColorControl
    rank: 30
  144:          # ElectricalPowerMeasurement
    rank: 40
  145:          # ElectricalEnergyMeasurement
    rank: 40
  47:           # PowerSource
    rank: 90
  40:           # BasicInformation
    rank: 95
```

Drei Eigenschaften, die den Entwurf tragen:

- **Kleiner Rang zuerst.** Was ein Gerät im Haus *tut*, steht bei 10–40;
  was es über sich selbst aussagt, bei 90+.
- **Ein Cluster ohne `rank:` bekommt 50.** Damit landet Unbekanntes in der
  Mitte — hinter dem, was nachweislich zählt, aber vor Batterie und
  Geräteangaben. Das ist die konservative Antwort: ein neuer Gerätetyp
  bekommt nie versehentlich die Batterie als Leitwert, und sein
  tatsächliches Hauptmerkmal wird nicht hinter Bekanntes verbannt, nur
  weil noch niemand einen Rang nachgetragen hat.
- **Innerhalb eines Rangs bleibt die heutige Ordnung.** Endpunkt, Cluster,
  Element, Art — dort ist sie stabil und richtig; sie ordnet zwei Signale
  desselben Clusters, und das tut sie gut. Die Rangliste ordnet nur die
  Cluster zueinander.

Die Sortierung ist damit `(rank, endpoint, cluster_id, element_id, kind)`.
Sie ist total und deterministisch: `rank` ist eine Zahl je Cluster, der Rest
ist der bisherige, bereits eindeutige Schlüssel (UNIQUE-Bedingung auf
`signal`).

**Warum kein Rang je Element.** Es wäre möglich, `press` innerhalb von
Cluster 59 vor `positions` zu setzen. Das ist bewusst *nicht* Teil dieses
Entwurfs: die Elementordnung innerhalb eines Clusters folgt heute der
Element-ID, und die ist in der Matter-Spezifikation selbst schon grob nach
Wichtigkeit vergeben. Eine zweite Rangebene wäre Aufwand ohne belegten
Gewinn — sie kann nachgetragen werden, wenn ein konkretes Gerät sie
verlangt.

## 5. Sortiert wird an der Quelle

`Store.signals` bekommt die neue Ordnung. Das betrifft **beides**:

- **Die WebUI**, über `GET /api/devices/<id>/signals` — Kachel und Modal
  ohne eigene Sortierung im Frontend.
- **Die Loxone-Vorlage**, weil `to_inputs(signals, …)` in
  `api/export.py` (Zeilen 147 und 326) genau diese Reihenfolge in die
  VIU-Datei schreibt. Im Loxone-Baum steht danach der Tastendruck oben und
  die Batterie unten statt umgekehrt.

Der Preis ist eine neu geladene Vorlage, die ihre Eingänge anders auflistet
als die zuvor heruntergeladene. **Das ist geprüft und folgenlos:**

- **Der Projektdatei-Sync gleicht über den Schlüssel ab, nicht über die
  Position.** `_plan_inputs` in `projectsync/diff.py` schlägt jeden Eintrag
  mit `index.input_cmds.get(entry.key)` nach; `_orphaned_entries` läuft
  ebenfalls über Schlüssel. Ein Umsortieren erzeugt dort weder
  Scheinänderungen noch Dubletten.
- **„Geändert seit Export" hängt an `updated_at`, nicht am Dateiinhalt**
  (`_changed_since_export` in `api/export.py`). Kein Gerät springt durch
  diese Änderung auf „geändert".
- **Die Schlüssel selbst bleiben unangetastet.** Sie sind
  Schlüsselmaterial (Hauptdokument 6.2) und werden von der Sortierung nicht
  berührt — die Verdrahtung in Loxone überlebt.

Was daraus folgt und im Test festgehalten gehört: eine Vorlage, die ein
Anwender **vor** dieser Änderung importiert hat, bleibt über den
Schlüsselabgleich vollständig bedienbar. Die Reihenfolge ist Darstellung,
nicht Identität.

## 6. Die Kachel

**Der Leitwert** ist das erstplatzierte funktionale Signal. Am Taster ist
das `press`, nicht `battery`; an der Leuchte `onoff`, nicht `VendorName`.
`leadSignalFor`/`firstSignalsFor`/`restSignalsFor` bleiben unverändert — sie
lesen dieselbe Liste, die jetzt anders sortiert ankommt. **Kein Zeichen
Frontend-Code ändert sich für den Leitwert selbst.**

**Die Batterie bekommt eine eigene Fußzeile.** Das ist die Folge, die die
Rangliste erzwingt und die eigens entschieden wurde: mit Rang 90 steht die
Batterie hinter allen 16 anderen funktionalen Signalen des Tasters und
fiele damit aus den sechs Vorschauzeilen (`FUNCTIONAL_PREVIEW_LIMIT`)
heraus — sie wäre auf der Kachel gar nicht mehr zu sehen.

Sie erscheint deshalb **unterhalb** der Vorschauzeilen und **unterhalb** des
„+ N weitere"-Links, abgesetzt durch eine gestrichelte Linie, mit
Batteriesymbol, dem Wort „Batterie" und dem Prozentwert in `--warn`:

```
┌─────────────────────────────────┐
│ ▣  Hallway button       true    │
│    PRESS                        │
│                                 │
│ longpress                 true  │
│ shortrelease              true  │
│ longrelease               true  │
│ multipress                true  │
│ position                     1  │
│ + 10 weitere                    │
│ ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈ │
│ 🔋 Batterie             12,4 %  │
└─────────────────────────────────┘
```

Drei Regeln dazu:

- **Sie zählt nicht gegen `FUNCTIONAL_PREVIEW_LIMIT`.** Die sechs
  Vorschauplätze bleiben den Nutzsignalen. Der „+ N weitere"-Zähler muss
  die Batterie deshalb als gezeigt verbuchen — sonst zählt er sie doppelt.
  (Genau dieser Fehler stand im ersten Entwurfs-Canvas: „+ 11 weitere" auf
  einer Kachel, die sieben von 17 Signalen zeigt.)
- **Sie erscheint nur, wenn das Gerät ein funktionales PowerSource-Signal
  hat.** Ein netzbetriebenes Gerät bekommt die Zeile nicht und wird nicht
  um eine leere Zeile höher.
- **Sie ist nie Leitwert**, auch nicht bei einem Gerät, dessen einziges
  funktionales Signal die Batterie ist. Dort bleibt der Leitwert leer, wie
  heute schon bei einem Gerät ohne funktionale Signale
  (`leadSignalFor` liefert `null`, die Hülle bleibt über `x-show` aus).

## 7. Das Modal

Variante A des Entwurfs-Canvas: dieselbe Zeile wie heute, aber mit
Spaltenköpfen, festen Spaltenmaßen und nach Endpunkt gruppiert.

### 7.1 Aufbau

```
Signale — Hallway button
IKEA of Sweden · BILRESA dual button · Hallway
┌───────────────────────────────────────────────────────┐
│ ▤ 12 von 17 Signalen gehen als Eingang nach Loxone    │
│                                    [Alle abwählen]    │
└───────────────────────────────────────────────────────┘
Der Schlüssel ist die Verdrahtung in Loxone … Periodisch heißt …

EXPORT │ SIGNAL      │ LOXONE-EINGANG │ WERT │ PERIODISCH │
────────────────────────────────────────────────────────────
▎Taste 1   Endpunkt 1 · Switch (59)              8 Signale
  ☑    ⏱ press          d4_1_press      true      ☐     ⋯
  ☑    ⏱ longpress      d4_1_longpress  true      ☐     ⋯
  ☑    ≡ position       d4_1_position      1      ☑     ⋯
       Endpunkt 1 · Cluster 59 Switch · Element 1 CurrentPosition
       [Rohwert schreiben            ] [Schreiben]
  ☐    ≡ positions      d4_1_positions     2      ☐     ⋯
  ⌄ 4 weitere Signale von Taste 1
▎Taste 2   Endpunkt 2 · Switch (59)              8 Signale
  …
▎Gerät     Endpunkt 0 · Power Source (47)        1 Signal
  ☑    🔋 battery       d4_0_battery   12,4 %     ☑     ⋯
────────────────────────────────────────────────────────────
⌄ Experte                              156 weitere Signale
```

### 7.2 Die sechs Spalten

`display: grid` mit `grid-template-columns: 58px minmax(0, 1fr) 150px 70px
76px 28px`, dieselbe Vorlage in Kopfzeile und jeder Datenzeile — das ist,
was heute fehlt und wodurch nichts fluchtet.

Der Spaltenkopf klebt beim Scrollen oben (`position: sticky`); ohne ihn
verlieren die Häkchenspalten bei 173 Signalen ihre Bedeutung, sobald der
Kopf aus dem Bild ist.

### 7.3 Beide Ja/Nein-Spalten sind Häkchen

Der erste Entwurf zeichnete „exportieren" als Kontrollkästchen und
„periodisch erneut senden" als Schiebeschalter. Dafür gab es keinen Grund,
der standhält: beide sind derselbe Fall — ein Ja/Nein je Signal, in
derselben Zeile.

**Die Regel, die stattdessen gilt: das Bedienelement folgt dem Behälter,
nicht der Bedeutung.**

- **In einer Tabelle Häkchen.** Sie fluchten in einer Spalte, bleiben
  kompakt und lesen sich als „diese Zeile gehört in diese Menge". Eine
  Spalte aus 17 Schiebeschaltern ist eine deutlich lautere Textur als 17
  Haken — und Lautstärke ist genau das Problem, das dieses Modal hat.
- **In einem Detailbereich Schalter**, je einer pro Zeile mit einem
  erklärenden Satz daneben. Das ist Variante B, die nicht gebaut wird;
  die Regel steht hier trotzdem, damit sie beim nächsten Detailbereich
  nicht neu erfunden wird.

Die Spaltenköpfe heißen **EXPORT** und **PERIODISCH** — beides einzelne
deutsche Wörter, die in 76 px passen. Was „periodisch" bedeutet, steht
**einmal** über der Tabelle statt siebzehnmal als Beschriftung neben einem
Kästchen. Die Übersetzungsschlüssel `web.signals.export_checkbox` und
`web.signals.resend_checkbox` bleiben als `title`/`aria-label` der
Kästchen erhalten — der Screenreader braucht die Beschriftung je Kästchen,
das Auge nicht.

### 7.4 Die Gruppen lösen die Namensdopplung auf

Je Endpunkt eine Gruppenüberschrift mit sprechendem Namen, technischer
Herkunft und Anzahl:

```
▎Taste 1      Endpunkt 1 · Switch (59)      8 Signale
```

Der sprechende Name kommt aus dem Gerätetyp des Endpunkts, plus einem
laufenden Zähler, wenn derselbe Typ mehrfach vorkommt: zwei
`GenericSwitch`-Endpunkte ergeben „Taste 1" und „Taste 2". Ein Endpunkt mit
Verwaltungstyp heißt „Gerät".

**Die Datenlage dafür ist zu prüfen, nicht vorauszusetzen.** Die Zuordnung
Endpunkt → Gerätetypen liegt persistiert in der Spalte
`device.device_types` (`_migrate_to_v7`) und wird in `api/devices.py` schon
über `category_for(device.device_types)` gelesen — sie ist also verfügbar,
aber mit zwei Einschränkungen:

- **Sie kann `NULL` sein**, solange `backfill_device_types` für dieses Gerät
  nicht gelaufen ist. Dann gibt es keinen sprechenden Namen, und die Gruppe
  heißt schlicht „Endpunkt 1". Das ist der Rückfall, nicht ein Fehlerfall:
  dieselbe Behandlung, die `category_for(None)` schon mit `OTHER`
  bekommt.
- **Eine Tabelle Gerätetyp → sprechender Endpunktname existiert noch
  nicht.** `CATEGORY_BY_DEVICE_TYPE` in `categories.py` bildet auf
  Gerätekategorien ab („Schalter", „Leuchte") — das sind Namen für ein
  ganzes Gerät, nicht für einen Endpunkt darin. Eine Fernbedienung ist
  *ein* Schalter mit *zwei* Tasten; „Schalter 1"/„Schalter 2" wäre falsch.
  Es braucht also eine kleine, eigene Zuordnung neben der bestehenden, mit
  demselben Belegungsanspruch wie dort (Nummer aus
  `matter_server.client.models.device_types`, nicht aus dem Gedächtnis) —
  und mit „Endpunkt N" als Rückfall für jeden Typ, der nicht darin steht.
  Der Umfang dieser Tabelle gehört in den Plan, nicht in diesen Entwurf.

Damit steht `press` einmal unter *Taste 1* und einmal unter *Taste 2* — die
Dopplung ist kein Rätsel mehr, sondern die Auskunft, dass die Fernbedienung
zwei Tasten hat.

**Die Gruppen folgen der Rangliste**, nicht der Endpunktnummer: die Gruppe,
die das erstplatzierte Signal enthält, steht oben. Beim Taster kommt „Gerät"
(nur Batterie) deshalb zuletzt, obwohl es Endpunkt 0 ist.

### 7.5 Das Rohwert-Feld wandert in die Zeile

Statt bei jedem Attribut eine zweite Zeile über die volle Breite zu
beanspruchen, öffnet die `⋯`-Schaltfläche am Zeilenende einen Bereich
**unter genau dieser Zeile**. Darin: die Herkunft im Klartext
(„Endpunkt 1 · Cluster 59 Switch · Element 1 CurrentPosition") und das
Rohwert-Feld mit seiner Schaltfläche.

Das erledigt zwei Dinge auf einmal — das Werkzeug bekommt das Gewicht, das
ihm zusteht, und der Pfad `1/59/1` bekommt endlich einen Ort, an dem
genug Platz ist, ihn auszuschreiben statt ihn als Rätsel danebenzustellen.

Höchstens ein Bereich ist gleichzeitig offen. Der Zustand lebt in Alpine
(`expandedSignalKey`), nicht im DOM: anders als beim Kachel-Menü und den
Signalgruppen gibt es hier genau **einen** Wert für das ganze Modal, kein
Auf/Zu je Element.

### 7.6 Der Kopf

Eine Zusammenfassung ersetzt den heutigen Hinweisabsatz als erstes
Element: „**12 von 17** Signalen gehen als Eingang nach Loxone", daneben
„Alle abwählen". Das ist die Zahl, wegen der man das Modal öffnet.

Der Schlüsselhinweis (`web.signals.key_hint`) bleibt, rutscht aber unter
die Zusammenfassung und nimmt den Satz über „periodisch" mit auf.

## 8. Was unverändert bleibt

- **`profiles/relevance.py`.** Welche Signale funktional sind, ist eine
  andere Frage als in welcher Reihenfolge sie stehen. `is_functional`
  bekommt kein Zeichen.
- **`profiles/categories.py`.** Die Gerätekategorie ordnet Geräte
  zueinander, die Rangliste ordnet Signale innerhalb eines Geräts. Zwei
  Fragen, zwei Tabellen.
- **Die Schlüssel.** `d4_1_press` bleibt `d4_1_press`.
- **`exported` und `exportability`.** Die Rangliste sagt nichts darüber,
  ob ein Signal exportiert wird — nur, wo es steht.
- **`FUNCTIONAL_PREVIEW_LIMIT` bleibt 6.**
- **Der Experte-Block** bleibt ein zugeklapptes `<details>` mit derselben
  `signalGroupsFor`-Vorlage; er bekommt die Endpunkt-Gliederung *nicht*,
  weil dort 156 Signale über alle Endpunkte liegen und die Gliederung nur
  mehr Überschriften erzeugte.

## 9. Tests

- **`rank` je Cluster ist gültig.** Jeder `rank:` in `clusters.yaml` ist
  eine Zahl; kein Cluster trägt zwei.
- **Ein Cluster ohne `rank:` bekommt 50.** Direkt gegen den Lader geprüft,
  nicht über ein Gerät.
- **Der Taster führt mit einem Switch-Signal, nicht mit der Batterie.**
  Gegen `ikea_bilresa_button.json`, das erste funktionale Signal.
- **Die Steckdose führt weiter mit `onoff`.** Gegen
  `ikea_grillplats_plug.json` — die Änderung darf die beiden heute
  richtigen Geräte nicht verstellen.
- **Die Batterie steht hinter allen Nutzsignalen**, aber vor nichts
  Unbekanntem: ein synthetisches Abbild mit einem Cluster ohne `rank:`
  belegt, dass dieser vor 47 steht.
- **Die Sortierung ist total.** Zwei Signale desselben Clusters behalten
  ihre bisherige relative Ordnung (Endpunkt, dann Element).
- **Der Export folgt derselben Ordnung.** `to_inputs` gegen den Taster:
  der Eingang zu `press` steht vor dem zu `battery`.
- **Eine vor der Änderung importierte Projektdatei bleibt abgleichbar.**
  `build_plan` gegen eine Projektdatei mit den Eingängen in der ALTEN
  Reihenfolge: kein Eintrag gilt als neu, keiner als verwaist.
- **Die Kachel zählt richtig.** Bei einem Gerät mit Batteriezeile nennt
  „+ N weitere" die Zahl der *nicht gezeigten* Signale — die Batterie
  zählt als gezeigt.
- **Ein netzbetriebenes Gerät hat keine Batteriezeile.**
- **Das Modal fluchtet.** Kopfzeile und Datenzeile tragen dieselbe
  `grid-template-columns`.
- **Beide Häkchenspalten sind `input[type="checkbox"]`** — kein
  Schiebeschalter im Modal.
- **Jedes Kästchen trägt eine Beschriftung** aus `strings.yaml`, auch
  wenn sie nur für Hilfstechnik sichtbar ist.

## 10. Offene Punkte

- **Der Umbruch unter etwa 640 px.** Sechs Spalten passen dort nicht. Die
  Tabelle muss in gestapelte Karten je Signal umbrechen; wie die aussehen,
  ist in diesem Entwurf nicht festgelegt und gehört in den Plan.
- **Die Ränge sind eine erste Belegung.** Sie stützen sich auf die neun
  Cluster, die `clusters.yaml` heute führt. Ein Cluster, der später
  dazukommt, braucht eine begründete Einordnung — nach demselben Maßstab
  wie `UTILITY_ENDPOINT_KEEP_CLUSTERS` in `relevance.py`: eine konkrete
  Belegung am Gerät oder in der Spezifikation, nicht die Annahme, die
  Tabelle sei von sich aus vollständig.
