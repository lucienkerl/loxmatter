# Bedienelemente für Lampen: Farbwähler, Regler und der Loxone-Farbweg

Entwurf, 7. September 2026. Gibt der Gerätekachel Bedienelemente, die zum
Werttyp eines Kommandos passen, statt jedem Wert dasselbe nackte Zahlenfeld
vorzusetzen — und schaltet dabei den seit Phase 4 offenen Farbweg
(`MoveToHueAndSaturation`) für WebUI **und** Loxone frei.

Anlass: zwei IKEA-Leuchten im Testaufbau, eine CCT- und eine RGBW-Lampe. Für
die Farbe der zweiten gibt es heute keinerlei Bedienmöglichkeit.

## 1. Das Problem

Die Bedienleiste der Kachel (`index.html`, Block `device-commands`) rendert
jedes benannte Kommando nach genau einem Muster: ohne Wert ein Knopf, mit
Wert ein `<input type="number">` plus „Senden“. Für `on`/`off`/`toggle` ist
das richtig. Für alles andere ist es die Abwesenheit einer Entscheidung:

- **Helligkeit** verlangt eine Prozentzahl, die man tippt, statt sie zu
  ziehen.
- **Farbtemperatur** verlangt eine Kelvinzahl ohne jeden Hinweis darauf,
  welchen Bereich die Lampe überhaupt beherrscht. Wer 6500 K eingibt, obwohl
  die Leuchte bei 4000 K endet, bekommt keine Fehlermeldung — das Gerät
  beschneidet still.
- **Farbe** gibt es gar nicht. `MoveToHueAndSaturation` (Cluster 768,
  Kommando 6) steht nicht in `profiles/clusters.yaml`, wird deshalb von
  `command_slug` nicht benannt, von der Controls-Route herausgefiltert und
  erscheint nur als anonymes „+1 weitere Befehle“.

Das letzte Loch ist das eigentliche. Eine RGBW-Lampe hängt am Bridge, meldet
ihren Farbzustand brav als Signal zurück — und lässt sich weder aus der
Oberfläche noch aus Loxone auf eine Farbe setzen.

### Der Widerspruch in der Begründung

`commands/translate.py` begründet im Moduldocstring, warum das Paar (768, 6)
fehlt: „weil die Loxone-seitige RGB-Zahl nicht verlässlich dokumentiert ist
(siehe `color.py`)“.

`commands/color.py` sagt an der genannten Stelle das Gegenteil. Dort steht
unter **„RGB — belegt“** die Formel

```
AQa = rot% + grün% * 1000 + blau% * 1_000_000
```

mit offizieller Quelle (Loxone Knowledge Base, „RGB Lighting Controller“,
Abschnitt Outputs) und einer zweiten, bestätigenden Community-Quelle. Nicht
belegt ist ausschließlich **Lumitech** — der kombinierte Helligkeits- und
Kelvin-Ausgang, um den es bei Hue/Saturation gar nicht geht.

Die Sperre beruht also auf einer Verwechslung zweier Loxone-Ausgabeformate.
Dieser Entwurf hebt sie auf und korrigiert den Docstring.

## 2. Zweck und Abgrenzung

Der Zweck bleibt **Diagnose** (Hauptspec 8.1): ein Klick trennt „Gerät
reagiert nicht“ von „Loxone-Verdrahtung oder Export ist falsch“. Dieser
Entwurf macht daraus kein Komfort-Bedienfeld. Konkret heißt das:

- **Keine laufende Zustandsspiegelung.** Die Oberfläche führt die Regler
  nicht nach, wenn sich das Gerät von außen ändert.
- **Aber ein Startwert.** Beim Öffnen werden die vorhandenen Werte einmalig
  gelesen (Abschnitt 6). Ohne das stünde jeder Regler auf einer erfundenen
  Position, und der erste Schubs risse die Lampe irgendwohin — der Klick
  bewiese dann nichts über den Zustand, den er verändert hat.
- **Keine Szenen, keine Favoriten, kein Zeitplan.**

## 3. Was unverändert bleibt

- **`POST /api/commands/{key}`** — Route, Statuscodes (404/400/502) und
  Semantik bleiben. Es entsteht kein zweiter Bedienweg.
- **`to_matter_call`** bleibt der einzige Übersetzer für WebUI *und*
  Loxone-Endpunkt (Hauptspec 4.2).
- **`takes_value`** behält seine Bedeutung für den Export. Das neue
  `control:` ist ausschließlich ein Hinweis für die Oberfläche.
- **Die Erlaubnislisten-Haltung** aus `api/control.py`: freigeschaltet wird
  nur, was gegen ein echtes Gerät oder ausdrücklich gekennzeichnet gegen die
  Spezifikation belegt ist.
- **Der `+N weitere Befehle`-Hinweis** und `hidden_raw_commands`.
- **Die Knöpfe für wertlose Kommandos** bleiben direkt auf der Kachel.
- **`POST /api/signals/{key}/write`** bleibt bei seiner ehrlichen 501.

## 4. Belege statt Vermutungen

Die folgenden IDs sind gegen das installierte `chip`-SDK geprüft
(`chip.clusters.Objects.ColorControl`), nicht aus dem Gedächtnis notiert:

| Element | ID | Bemerkung |
| --- | --- | --- |
| `MoveToHueAndSaturation` | Kommando 6 | wird freigeschaltet |
| `MoveToColorTemperature` | Kommando 10 | bereits vorhanden |
| `MoveToHue` / `MoveToSaturation` | 0 / 3 | **nicht** freigeschaltet |
| `MoveToColor` (xy) | 7 | **nicht** freigeschaltet |
| `EnhancedMoveToHueAndSaturation` | 67 | **nicht** freigeschaltet |
| `ColorTempPhysicalMinMireds` | Attribut 16395 | neue Grenze |
| `ColorTempPhysicalMaxMireds` | Attribut 16396 | neue Grenze |
| `ColorMode` | Attribut 8 | 0 = Hue/Sat, 1 = xy, 2 = Mired |

`ColorModeEnum` ebenfalls gegen das SDK geprüft.

### 4.1 Gerätefixtures als Voraussetzung

`tests/fixtures/nodes/synthetic_color_light.json` ist ausdrücklich als
**synthetisch, kein echtes Gerät** gekennzeichnet. Es war der Platzhalter für
genau die Hardware, die jetzt vorliegt.

**Vor der Umsetzung** werden beide Leuchten als Fixture eingecheckt
(`tests/fixtures/nodes/`, Format `{"node_id", "available", "attributes"}`)
und ersetzen das synthetische Abbild in den Farb-Tests. Erst damit steht
jeder Whitelist-Eintrag auf demselben Beleg, den `api/control.py` und
`commands/color.py` für sich selbst einfordern.

Die Fixtures werden vor dem Einchecken auf Inhalte geprüft, die nicht ins
Repository gehören — dieselbe Sorgfalt, mit der `.gitignore` die
Loxone-Originalvorlagen fernhält.

**Diese Fixtures können den Entwurf noch ändern.** Advertisiert die
RGBW-Lampe Kommando 6 nicht, sondern nur 7 (xy), wird eine
xy-Farbraumumrechnung nötig, die es heute nirgends gibt; dieser Entwurf
deckt sie nicht ab und müsste dann fortgeschrieben werden.

## 5. Server

### 5.1 `profiles/clusters.yaml`

Neu unter Cluster 768, `commands`:

```yaml
6: {slug: color, takes_value: true, control: hue_sat}
```

Alle bestehenden Kommandoeinträge bekommen `control:`:

| Cluster/Kommando | Slug | `control` |
| --- | --- | --- |
| 6/0, 6/1, 6/2 | `off`, `on`, `toggle` | `none` |
| 8/0 | `level` | `percent` |
| 8/4 | `level_onoff` | `percent` |
| 768/10 | `colortemp` | `kelvin` |
| 768/6 | `color` | `hue_sat` |

Neu unter Cluster 768, `attributes`:

```yaml
16395: {slug: colortemp_phys_min_mireds, unit: mired, functional: false}
16396: {slug: colortemp_phys_max_mireds, unit: mired, functional: false}
```

### 5.2 Neues Tabellenfeld `functional: false`

`profiles/relevance.py`, `is_functional` Schicht 3, erklärt heute: hat ein
Cluster einen `attributes:`-Abschnitt, ist genau das dort Benannte gewollt.
Die beiden CT-Grenzen einfach zu benennen hieße also, aus zwei
unveränderlichen Gerätekonstanten zwei standardmäßig exportierte virtuelle
Loxone-Eingänge zu machen.

Das neue optionale Feld `functional: false` trennt beides: **bekannt genug
zum Auslesen, nicht interessant genug zum Vorauswählen.** `is_functional`
liefert für solche Attribute `false`; im Expertenblock der Signalliste
bleiben sie sichtbar und von Hand wählbar, `exportable` ändert sich nicht.

Bewusst ein allgemeines Feld und kein Sonderfall für Cluster 768: jeder
weitere Cluster mit Kapazitätskonstanten (Min/Max-Bereiche, Auflösungen)
trifft dasselbe Problem, und die Alternative — die Werte an der Tabelle
vorbei direkt aus dem Snapshot zu greifen — schüfe eine zweite Stelle, an der
Attributwissen lebt. Genau das soll die Tabelle verhindern.

### 5.3 `commands/color.py`

Neu: der Gegenspieler zu `rgb_to_hue_saturation` — ein Entpacker für die
gepackte Loxone-Zahl.

```
loxone_rgb_to_rgb(value) -> (r, g, b)   # je 0–255
```

Zerlegt `r% + g%*1000 + b%*1000000` in drei Prozentwerte und skaliert sie auf
0–255. Die Formel ist im Moduldocstring bereits mit offizieller Quelle
belegt; sie wurde nur nie implementiert. Werte außerhalb des Gültigen (jeder
Kanal > 100 %, negative Zahlen, Nicht-Zahlen) führen zu `ValueError` — der
Aufrufer macht daraus 400, nicht eine erfundene Farbe.

Der Moduldocstring bekommt außerdem den Hinweis, dass die
RGB→Hue/Sat-Kette ab jetzt tatsächlich benutzt wird und an welcher Hardware
sie gegengeprüft wurde — die heutige Warnung „NICHT an Hardware validiert“
wird damit gegenstandslos und darf nicht stehenbleiben.

### 5.4 `commands/translate.py`

Neu: `_payload_hue_saturation`, eingetragen unter `(768, 6)`:

```
Zahl entpacken → RGB → rgb_to_hue_saturation → {"hue", "saturation", "transitionTime": 0}
```

Der bestehende Konsistenztest über `known_command_pairs()` erzwingt, dass
Tabelleneintrag und Payload-Builder gemeinsam wandern — genau die Falle aus
Review-Fix C2 (2026-09-02), bei der ein Builder ohne Tabelleneintrag ein
digitales Kommando mit analoger Nutzlast erzeugte.

**Der Moduldocstring wird korrigiert.** Die heutige Begründung für das
Fehlen von Kommando 6 ist sachlich falsch (Abschnitt 1). An ihre Stelle
tritt der Verweis auf die belegte RGB-Formel und der klare Hinweis, dass
**Lumitech** weiterhin offen ist.

### 5.5 API

`CommandOut` (`api/models.py`) bekommt zwei Felder:

| Feld | Typ | Bedeutung |
| --- | --- | --- |
| `control` | `str` | `none` \| `percent` \| `kelvin` \| `hue_sat` |
| `range` | `{min, max} \| None` | nur bei `kelvin`, aus 16395/16396 des Geräts |

`range` wird aus den zuletzt bekannten Attributwerten des Geräts gefüllt und
in **Kelvin** ausgeliefert, nicht in Mired — die Oberfläche soll nicht
rechnen müssen, und die Umrechnung ist ein Kehrwert, bei dem Min und Max
tauschen. Fehlt eines der beiden Attribute, ist `range` `None`.

Fehlt in der Tabelle ein `control`, liefert die API den ausdrücklichen Wert
`unknown` — **nicht** eine aus `takes_value` geratene Voreinstellung. Ein
Regler, dessen Skala niemand belegt hat, wäre schlimmer als ein Zahlenfeld:
er behauptet einen Wertebereich. `unknown` fällt in der Oberfläche auf das
heutige Zahlenfeld zurück (Abschnitt 6.2).

Heute ist dieser Fall unerreichbar — Abschnitt 5.1 versieht jeden
bestehenden Eintrag mit einem `control`, und die Controls-Route filtert
alles heraus, was gar nicht in der Tabelle steht (`command_slug is None`).
`unknown` existiert für den nächsten Tabelleneintrag, den jemand ohne
`control` hinzufügt, damit dieser nicht stillschweigend als Regler
erscheint.

## 6. Oberfläche

### 6.1 Kachel

Unverändert bis auf einen Zusatz: die wertlosen Kommandos bleiben als Knöpfe
direkt auf der Kachel, dazu kommt ein Knopf **„Steuern“**, sobald das Gerät
mindestens ein Kommando mit `control` ungleich `none` hat. Die heutigen
Zahlenfelder verschwinden von der Kachel.

Die drei bestehenden Zustandshinweise (`controls_loading`,
`no_known_commands`, `+N weitere Befehle`) bleiben Wort für Wort erhalten.

### 6.2 Das Modal

Nach dem Muster des Signal-Modals (Entwurf vom 5. September 2026):
gleicher Öffnungs- und Schließweg, gleiche Kopfzeile mit Gerätename.

Der Inhalt entsteht **ausschließlich** aus `control`. Kein Slug-Vergleich im
JavaScript:

| `control` | Widget |
| --- | --- |
| `none` | Knopf |
| `percent` | Regler 0–100 % |
| `kelvin` | Regler, begrenzt durch `range` |
| `hue_sat` | 2-D-Fläche: waagerecht Farbton, senkrecht Sättigung |
| `unknown` | heutiges Zahlenfeld mit „Senden“ |

Die letzte Zeile ist die Rückfallebene: ein Kommando ohne `control`-Eintrag
verliert nichts, es sieht aus wie heute. Kein Rückschritt für Geräte, die
dieser Entwurf nicht im Blick hat.

### 6.3 Die Modus-Tabs entstehen von selbst

Matter kennt keinen Zustand „Farbe und Farbtemperatur zugleich“: `ColorMode`
ist entweder Hue/Sat oder Mired. Das Modal bildet das ab, statt es zu
überdecken.

- Gerät hat `kelvin` **und** `hue_sat` → Tableiste **Weiß | Farbe**.
- Gerät hat nur eines von beiden → keine Tableiste, nur dieses Widget.

Die CCT-Lampe bekommt dadurch einen Kelvin-Regler ohne Tabs, die RGBW-Lampe
beide Tabs — **ohne eine einzige Abfrage auf Gerätetyp, Hersteller oder
Modell.** Das hält das README-Versprechen „keine kuratierte Liste
unterstützter Modelle“ auch für die Bedienelemente.

### 6.4 Startwert

Einmalig beim Öffnen, kein Nachführen. Quelle ist der bereits geladene
`signalsByDevice`-Eintrag — `GET /api/devices/{id}/signals` liefert **alle**
Signale des Geräts samt letztem Wert, nicht nur die exportierten. Es
entsteht also keine neue Route.

**Die API liefert bereits skaliert.** `SignalOut.value` kommt aus
`Runtime.last_values_for`, und dort landen die Werte über `to_loxone_value`,
das den `scale`-Faktor der Tabelle anwendet. Die Oberfläche rechnet deshalb
nur dort, wo die Tabelle es nicht kann:

| Widget | Slug | Umrechnung |
| --- | --- | --- |
| Helligkeit | `level` | keine — Tabelle skaliert bereits auf % |
| Farbton | `hue` | keine — Tabelle skaliert bereits auf Grad |
| Sättigung | `saturation` | keine — Tabelle skaliert bereits auf % |
| Farbtemperatur | `colortemp_mireds` | Mired → Kelvin, Kehrwert (`scale` kann nur multiplizieren) |
| aktiver Tab | `colormode` | 2 → Weiß, 0 → Farbe |

Dass auch die beiden `functional: false`-Grenzen einen Wert tragen, ist
geprüft und kein Zufall: `Runtime._cache_attribute` cacht jedes Signal, das
der Store kennt, ohne auf `exported` zu filtern. Ohne diese Eigenschaft wäre
`range` immer leer und der Kelvin-Regler nie begrenzt.

Fehlt ein Wert, startet das Widget mittig **und sagt es**: ein sichtbarer
Hinweis „Startwert unbekannt“ statt einer Position, die eine Kenntnis
vortäuscht, die nicht besteht.

### 6.5 Senden

Beim **Loslassen** (`change`), nicht während des Ziehens. Ein Zug = ein
Funkpaket. Thread ist langsam, und wenn ein Klick etwas beweisen soll, muss
die Zuordnung zwischen Eingabe und Reaktion eindeutig bleiben.

Sperre pro Kommando über den bestehenden `commandBusyKey`; ist das Gerät
offline, bleibt alles deaktiviert — beides wie heute.

Die 2-D-Fläche rechnet den gepickten Punkt in RGB um, packt ihn zur
Loxone-Zahl und schickt **diese**. Damit durchläuft der Klick in der
Oberfläche exakt den Weg, den Loxone später nimmt — für ein Diagnosewerkzeug
der eigentliche Gewinn: klappt es hier, ist der Loxone-Pfad bewiesen.

## 7. Übersetzung

Alle neuen Zeichenketten kommen mit deutscher **und** englischer Fassung nach
`i18n/strings.yaml`, wie seit Phase B/C üblich: Modaltitel, Tabbeschriftungen
(Weiß/Farbe), Reglerbeschriftungen, Einheiten, der Hinweis „Startwert
unbekannt“ und die Fehlermeldung für eine ungültige Farbzahl.

## 8. Tests

| Ort | Was |
| --- | --- |
| `tests/commands/test_color.py` | Entpacken, Rundlauf RGB→gepackt→RGB, Randwerte (Schwarz, Weiß, reine Kanäle), Ablehnung von >100 %, negativ, NaN/inf |
| `tests/commands/test_translate.py` | Paar (768, 6) baut die erwartete Nutzlast; ungültige Zahl → `UnsupportedValueError`; bestehender Konsistenztest deckt Tabelle ↔ Builder |
| `tests/profiles/` | `functional: false` wirkt; die CT-Grenzen bleiben `exportable` und im Expertenblock wählbar; `control` wird korrekt gelesen |
| `tests/api/` | `CommandOut.control` und `range`; `range` ist `None`, wenn 16395/16396 fehlen; `unknown` für Kommandos ohne Tabelleneintrag |
| Fixtures | beide echten Leuchten ersetzen `synthetic_color_light.json` in den Farb-Tests |
| WebUI | Auslieferungstest belegt nur die Auslieferung — die Alpine-Ausdrücke des Modals laufen zusätzlich in einem Wegwerf-Harness im Browser (Tabs, Startwert, Rückfall auf das Zahlenfeld) |

## 9. Bewusst in Kauf genommen

1. **Genauigkeitsverlust.** Die gepackte Loxone-Zahl trägt pro Kanal nur
   0–100 %. Die gepickte Farbe wird also quantisiert, bevor sie zu Hue/Sat
   wird. Das ist der Preis dafür, dass Oberfläche und Loxone denselben
   Übersetzer benutzen — und für ein Diagnosewerkzeug der richtige Preis:
   eine verlustfreie, aber eigene Umrechnung würde genau die Aussage
   zerstören, die der Klick treffen soll.
2. **Kein CT-Bereich → kein Regler.** Liefert eine Lampe 16395/16396 nicht,
   fällt der Kelvin-Regler auf das Zahlenfeld zurück, statt erfundene
   Grenzen anzuzeigen. Ein Regler, der bei 6500 K endet, obwohl die Leuchte
   bei 4000 K aufhört, wäre der stille Fehlschlag, den Hauptspec 8.1
   verbietet.
3. **Nur ein Farbkommando.** MoveToHue (0), MoveToSaturation (3),
   MoveToColor (7) und Enhanced (67) bleiben gesperrt. Die 2-D-Fläche setzt
   Farbton und Sättigung in einem Kommando; alles Weitere wäre unbelegte
   Fläche.

## 10. Offene Punkte

1. **Lumitech bleibt ungelöst.** Der kombinierte Helligkeits- und
   Kelvin-Ausgang der Loxone-Lichtsteuerung hat weiterhin keine belegte
   Formel; `colortemp` nimmt deshalb nach wie vor eine bereits entpackte
   Kelvinzahl entgegen. Dieser Entwurf ändert daran nichts, er hört nur auf,
   RGB fälschlich mitzuverurteilen.
2. **xy-Farbraum.** Sollte sich an den Fixtures zeigen, dass Geräte
   `MoveToColor` (7) statt Kommando 6 erwarten, fehlt die
   xy-Umrechnung vollständig.
3. **Die Erlaubnisliste in `api/control.py`** bleibt handgepflegt; der in
   ihrem Docstring beschriebene Weg über die `writable`-Tabelle des
   chip-Pakets ist weiterhin versperrt.
4. **Keine Zustandsspiegelung.** Ändert jemand die Lampe von außen, während
   das Modal offen ist, veralten die Regler still. Bewusst so — der Ausbau
   zum echten Bedienfeld wäre ein eigener Entwurf mit eigener Begründung
   gegenüber Hauptspec 8.1.
