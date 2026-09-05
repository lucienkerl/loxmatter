# Geräte-Tab: Räume, Kategorien und ein mehrspaltiges Kachelraster

Entwurf, 5. September 2026. Führt
[den Geräte-Dashboard-Entwurf](2026-09-03-geraete-dashboard-und-export-design.md)
fort — dessen Kachel („kein Aufklappen mehr", Abschnitt 3) bleibt inhaltlich
unangetastet und wird hier nur neu angeordnet. Löst nebenbei dessen offenen
Punkt 1 (Zuordnung Gerätetyp → Icon) ein, weil die Kategorie, die dieser
Entwurf ohnehin braucht, genau diese Zuordnung ist.

## 1. Das Problem

Die Geräteansicht ist eine einspaltige Liste immer offener Kacheln
(`index.html:214-340`), sortiert nach `device.id`, also nach der Reihenfolge
des Einlernens. Das trägt bis etwa acht Geräte. Darüber hinaus hat die
Ansicht drei Schwächen, die sich gegenseitig verstärken:

1. **Eine Spalte verschenkt zwei Drittel der Breite.** Eine Kachel ist so
   hoch wie ihre Werte- und Bedienblöcke, aber so breit wie das Fenster.
   Zwölf Geräte sind zwölf Bildschirmhöhen.
2. **Es gibt keine Ordnung außer dem Einlerndatum.** Wer die Steckdose im
   Bad sucht, scrollt und liest Namen — die Ansicht hilft nicht mit.
3. **Ein Gerät trägt keinen Ort.** Der Raum steckt bestenfalls im
   selbstvergebenen Label („Steckdose Bad"), womit er nicht sortierbar,
   nicht filterbar und nicht korrigierbar ist, ohne den Namen umzuschreiben,
   der als `Title` im Loxone-Export landet.

Dazu kommt eine Lücke beim Einlernen: ein frisch eingelerntes Gerät heißt
zunächst so, wie der Hersteller es nennt, und liegt am Ende der Liste. Die
Zuordnung „das ist die Lampe im Wohnzimmer" passiert im Kopf des
Bedienenden und nirgends sonst.

## 2. Was unverändert bleibt

- **Der Inhalt der Kachel.** Werte, Bedienelemente, Export-Hinweis, Export-
  und Entfernen-Taste bleiben ohne Klick sichtbar. Der Verzicht auf den
  Aufklapp-Umschalter aus dem Dashboard-Entwurf gilt weiter; dieser Entwurf
  ordnet nur um.
- **Die Signalansicht** und der globale Export-Tab. Räume erscheinen dort
  nicht — die Signalansicht ist die vollständige Sicht auf *ein* Gerät, der
  Export-Tab die Sicht auf *alle*; beide brauchen keine Ortsordnung.
- **Der Loxone-Export.** Weder Raum noch Kategorie landen in einer
  Vorlagendatei. Das ist keine Sparsamkeit, sondern die Begründung für
  Abschnitt 3.3: was nicht exportiert wird, darf ein Gerät auch nicht als
  „seit dem Export geändert" markieren.
- **Die Statusfarben** (grün unauffällig, amber geändert, grau offline) und
  die Akzentfarbe Kupfer, samt der Begründung, warum beide getrennt bleiben
  (Dashboard-Entwurf, Abschnitt 3).

## 3. Datenhaltung

### 3.1 Migration v7: zwei Spalten

`_SCHEMA_VERSION` geht von 6 auf 7. `_migrate_to_v7` legt über das
bestehende `_add_column_if_missing` zwei Spalten an `device` an — dasselbe
Muster und derselbe Grund wie bei `_migrate_to_v2`, das ebenfalls zwei
Spalten in einem Schritt nachzieht:

```sql
room         TEXT   -- NULL = "Ohne Raum"
device_types TEXT   -- JSON, Endpunkt -> Liste der Matter-Typ-IDs; NULL = noch nicht nachgetragen
```

Kein Backfill für `room`: `NULL` bedeutet dort dasselbe wie bei einem frisch
eingelernten Gerät ohne Raumwahl, es gibt keinen Bestandswert, aus dem sich
ein Raum ableiten ließe. `device_types` wird dagegen sehr wohl nachgetragen,
nur nicht in der Migration — siehe 3.4.

### 3.2 Räume sind kein eigenes Objekt

Es gibt keine `room`-Tabelle, keine Raum-IDs und keine Raumverwaltung. Ein
Raum existiert genau so lange, wie mindestens ein aktives Gerät seinen Namen
trägt; die Raumliste ist das `DISTINCT` über `device.room`, das die
Oberfläche ohnehin aus `GET /api/devices` ableiten kann.

Der Preis dieser Entscheidung ist, dass „Raum umbenennen" keine Operation
auf einem Objekt ist, sondern ein Massenschreibvorgang über alle Geräte des
Raums — dafür gibt es die eine Route in 4.3. Der Gewinn: keine verwaisten
Räume, keine Aufräumregeln, keine zweite Entität, die mit den Geräten in
Deckung gehalten werden muss.

**Normalisierung.** Raumnamen werden beim Schreiben getrimmt; was nach dem
Trimmen leer ist, wird `NULL`. Vergleich und Sortierung laufen
case-sensitiv über den gespeicherten Namen — „Küche" und „küche" wären zwei
Räume. Das ist die schlichtere Regel, und weil die Oberfläche vorhandene
Räume immer als Auswahlliste anbietet und Freitext nur hinter „+ Neuer
Raum …" versteckt, entsteht ein Schreibweise-Zwilling nur, wenn man ihn
aktiv eintippt.

### 3.3 `set_room` fasst `updated_at` nicht an

`store.set_room(device_id, room)` schreibt ausschließlich `device.room`.
Das ist der einzige nicht offensichtliche Punkt der Datenhaltung und
verdient die Begründung:

`rename_device` setzt `updated_at` mit, und sein Docstring sagt auch warum
(`store.py:874-878`) — das Label landet im nächsten Export als `Title` in
der Vorlage, also führt `GET /api/export/status` das Gerät danach zu Recht
als „seither geändert". Der Raum landet nirgends im Export. Würde
`set_room` `updated_at` mitsetzen, bekäme beim ersten Aufräumen der
Raumzuordnung *jedes* Gerät eine amber „geändert seit Export"-Pille und die
Aufforderung zu einem Export, der Byte für Byte dieselben Dateien erzeugt
wie der letzte. Dieselbe Überlegung gilt für `backfill_device_types`.

### 3.4 `device_types`: gespeichert wird die Quelle, nicht die Ableitung

In die Datenbank geht die rohe Auskunft des Geräts — die Ausgabe von
`relevance.device_types_by_endpoint(snapshot)`, als JSON serialisiert. Die
Kategorie (Abschnitt 5) wird bei jedem Lesen daraus abgeleitet und **nicht**
gespeichert.

Der Grund steht schon in der Migrationsgeschichte dieses Projekts:
`signal.functional` und `signal.title` sind abgeleitete Werte, die
gespeichert wurden, und `_migrate_to_v3` musste sie für Bestandszeilen
nachträglich neu berechnen, als sich die Ableitungsregel verbesserte
(`store.py:78-83`). Eine Zuordnungstabelle Matter-Typ → Kategorie wird
wachsen, sobald ein Gerätetyp auftaucht, an den beim Schreiben niemand
gedacht hat. Wird nur die Quelle gespeichert, ist ein solcher Zuwachs ein
Codewechsel ohne Migration.

`StoredDevice` bekommt entsprechend `room: str | None` und
`device_types: dict[int, frozenset[int]] | None`; `_as_device` parst das
JSON, `None` heißt „noch nicht nachgetragen" (siehe 5.3).

**Befüllung an zwei Stellen:**

1. `register_device(snapshot, room=None)` schreibt die Typen bei der
   Registrierung mit — das Abbild liegt dort ohnehin vor.
2. `store.backfill_device_types(snapshots)`, aufgerufen beim Start der
   Brücke direkt neben dem bestehenden
   `await runtime.seed_from_snapshot(await client.snapshots())`
   (`cli.py:606`). Die Abbilder aller bekannten Knoten sind dort bereits
   geholt; ein zweiter Abruf wäre reine Verschwendung. Der Aufruf
   aktualisiert **ausschließlich Zeilen mit `device_types IS NULL`** — ein
   bereits nachgetragenes Gerät wird nicht bei jedem Start neu geschrieben,
   und ein Gerät, das gerade offline ist und deshalb in `snapshots()` fehlt,
   verliert seine Typen nicht.

## 4. API

### 4.1 Modelle (`api/models.py`)

- `DeviceOut` bekommt `room: str | None`, `category: str` (Kennung, siehe
  5.1) und `category_rank: int`. Der Rang kommt aus dem Backend statt aus
  einer zweiten Liste im Frontend — die Reihenfolge der Kategorien ist eine
  Eigenschaft der Kategorientabelle, nicht der Oberfläche.
- `DeviceRename` wird zu **`DevicePatch`** mit `label: str | None = None`
  und `room: str | None = None`. `None` heißt „unverändert" (dasselbe
  Prinzip wie bei `SignalPatch`), der Leerstring `""` heißt „Raum
  entfernen" → `NULL`. Der Klassenname zieht mit, weil „Rename" nicht mehr
  stimmt; Aufrufe mit nur `label` bleiben gültig.
- `CommissionRequest` bekommt `room: str | None = None`.
- Neu: `RoomRename` mit `from_room: str` und `to_room: str` (Feldnamen mit
  Suffix, weil `from` ein Python-Schlüsselwort ist; nach außen über
  `alias="from"`/`alias="to"`).

### 4.2 `PATCH /api/devices/{id}`

Erweitert, kein neuer Endpunkt. Setzt `label` über `rename_device` (mit
`updated_at`) und `room` über `set_room` (ohne). Ein Aufruf, der beides
mitbringt, macht beides.

### 4.3 `POST /api/rooms/rename`

Die einzige neue Route: ein `UPDATE device SET room = ? WHERE room = ? AND
active = 1`, Antwort `{"renamed": n}`.

Ohne sie müsste man einen Raum umbenennen, indem man an jedem Gerät einzeln
„+ Neuer Raum …" tippt — bei fünf Geräten fünf Gelegenheiten für einen
Tippfehler, der einen sechsten Raum erzeugt. `active = 1` in der Bedingung
aus demselben Grund, aus dem `store.devices()` danach filtert: ein
entferntes Gerät ist aus Sicht der Oberfläche nicht mehr da und soll auch
nicht stillschweigend mitwandern.

**Zusammenführen ist erlaubt.** Ein Zielname, den es schon gibt, führt beide
Räume zusammen — das ist die naheliegende Bedeutung von „nenne Küche jetzt
Essbereich", wenn es einen Essbereich schon gibt. Die Oberfläche fragt in
diesem Fall vorher nach (Abschnitt 6.3), weil der Vorgang nicht rückgängig
zu machen ist: nach dem Zusammenführen weiß niemand mehr, welches Gerät
vorher in welchem der beiden Räume stand.

## 5. Kategorie

### 5.1 Die Kategorien und ihr Rang

Ein neues Modul `profiles/categories.py`, neben `relevance.py`, weil es
dieselbe Quelle auswertet:

| Rang | Kennung | Deutsch | Englisch |
|---|---|---|---|
| 0 | `light` | Licht | Light |
| 1 | `socket` | Steckdose | Socket |
| 2 | `switch` | Taster | Switch |
| 3 | `covering` | Beschattung | Covering |
| 4 | `climate` | Klima | Climate |
| 5 | `sensor` | Sensor | Sensor |
| 6 | `lock` | Schloss | Lock |
| 7 | `other` | Sonstige | Other |

Der Rang ist fest verdrahtet und **nicht** die alphabetische Reihenfolge der
Namen: ein Sprachwechsel würde die Gruppen sonst umsortieren, und eine
Ansicht, die je nach Sprache anders aufgebaut ist, ist zweimal zu erklären.

Die vollständige Tabelle Matter-Typ-ID → Kategorie ist Sache des
Implementierungsplans, nicht dieser Spec — dieselbe Abgrenzung wie beim
Icon-Punkt des Dashboard-Entwurfs. Belegt sind hier die Kategorien selbst
und ihre Reihenfolge; die Zuordnung muss pro Typ-ID gegen
`matter_server.client.models.device_types` belegt werden, nicht geraten.

### 5.2 Der Primärtyp

Ein Matter-Knoten deklariert Typen pro Endpunkt. Maßgeblich ist:

1. Verwaltungs-Endpunkte fallen raus — dieselbe Menge, die `is_functional`
   schon kennt (`UTILITY_DEVICE_TYPES` plus `POWER_SOURCE_DEVICE_TYPE`), und
   aus demselben Grund: Root Node, OTA Requestor und PowerSource sagen nichts
   darüber, was das Gerät im Haus tut.
2. Vom Rest zählt der **niedrigste Endpunkt** — bei Matter üblicherweise
   Endpunkt 1, der Anwendungs-Endpunkt.
3. Deklariert dieser mehrere Typen, gewinnt der mit dem niedrigsten
   Kategorierang. Damit ist das Ergebnis unabhängig von der Reihenfolge, in
   der das Gerät seine Typen aufzählt.
4. Nichts Zuordenbares, oder `device_types IS NULL` → `other`.

### 5.3 Bis der Nachtrag greift

Ein Bestandsgerät steht bis zum nächsten Start der Brücke in „Sonstige" —
sichtbar, bedienbar, vollständig, nur unsortiert. Das ist die ehrlichere
Übergangslösung gegenüber einer Migration, die aus gespeicherten Signalen
eine Kategorie errät: eine solche Heuristik wäre eine zweite
Klassifikationsregel neben den Matter-Gerätetypen, also genau die
Doppelquelle, vor der `relevance.py` in seinem Kommentar zu
`UTILITY_ENDPOINT_KEEP_CLUSTERS` warnt.

## 6. Oberfläche

### 6.1 Raster

`grid-template-columns: repeat(auto-fill, minmax(260px, 1fr))` — vier
Spalten bei üblicher Desktopbreite, zwei auf dem Tablet, eine auf dem
Telefon, ohne eigene Breakpoints. Die 260 px sind die Untergrenze, ab der
Kopfzeile samt Leitwert und die Wertespalte nicht mehr umbrechen.

### 6.2 Die Kachel

Freigegeben nach interaktivem Entwurf („Mix 2"), von oben nach unten:

1. **Kopfzeile** — Kategorie-Icon in getöntem Feld · Name (editierbar wie
   heute) · rechts der **Leitwert** in Mono-Ziffern. Darunter, klein, das
   Label des Leitwerts („Zustand", „Temperatur").
   **Ist eine Status-Pille fällig (offline / geändert seit Export),
   verdrängt sie dieses Label.** Der Zustand der Kachel wiegt schwerer als
   die Beschriftung einer Zahl, die zwei Zentimeter daneben steht.
2. **Werteraster** — die restlichen funktionalen Signale als Label/Wert-
   Zeilen, Werte rechtsbündig in Mono. Das bestehende
   `FUNCTIONAL_PREVIEW_LIMIT = 6` bleibt und **zählt den Leitwert mit**:
   Leitwert plus bis zu fünf Zeilen. Der bisherige Hinweis „*n* weitere in
   der Signalansicht" wird zur letzten Rasterzeile („+ 7 weitere", verlinkt
   auf die Signalansicht) statt zu einem eigenen Absatz.
3. **Bedienleiste** — die Befehle als Textknöpfe, unverändert in Funktion.
   Der Hinweis auf unbenannte Rohbefehle hängt sich als gedimmtes „+3
   unbenannt" hinten an die Knopfreihe statt in eine eigene Zeile.
4. **Fußzeile** — links die **Raum-Auswahl** („🏠 Wohnzimmer ▾", öffnet die
   vorhandenen Räume plus „+ Neuer Raum …" plus „Ohne Raum"), daneben der
   Export-Hinweis, rechts die Icon-Tasten Exportieren und Entfernen.

**Leitwert-Regel:** das erste funktionale Signal in der Reihenfolge, die
`firstSignalsFor` heute schon liefert — also die Reihenfolge der
Profiltabelle. Steckdose → Zustand, Klimasensor → Temperatur, Rollo →
Position. Keine neue Spalte, kein Endpunkt, keine Konfiguration; ein Gerät
ohne funktionale Signale zeigt schlicht keinen Leitwert und die Kopfzeile
bleibt einzeilig.

Der Raum steht bewusst in der Fußzeile und nicht unter dem Namen: die
Kopfzeile trägt bereits Name, Leitwert-Label und im Ernstfall die
Status-Pille, ein vierter Bestandteil hätte einen davon verdrängt.

### 6.3 Raumleiste und Gruppierung

Über der Liste eine Chip-Reihe: „Alle · Wohnzimmer · Küche · … · Ohne Raum",
jeweils mit der Gerätezahl. Bei **Alle** erscheinen Gruppenüberschriften pro
Raum; bei einem gewählten Raum entfallen sie, weil es nur einen gibt.

**Die Leiste zeigt sich gar nicht, solange kein einziges Gerät einen Raum
trägt.** Bei drei Geräten und keinem Raum wäre sie eine Zeile Lärm über
einer Liste, die ohnehin auf einen Blick passt.

An jeder Gruppenüberschrift ein Stift, der den Raum umbenennt
(`POST /api/rooms/rename`). Trägt der Zielname bereits Geräte, fragt die
Oberfläche vorher nach und benennt das Zusammenführen beim Namen (siehe
4.3). Ist ein einzelner Raum gewählt, gibt es keine Überschrift, die den
Stift tragen könnte — er sitzt dann am aktiven Chip der Raumleiste. „Ohne
Raum" ist kein Raum und trägt in beiden Fällen keinen Stift: es ist die
Menge der Geräte ohne Zuordnung, und ein Name, den man ändern könnte, ist
gerade das, was diesen Geräten fehlt.

**Der Filterzustand wird nicht gespeichert** — kein `localStorage`, kein
Endpunkt. Nach einem Neuladen steht die Ansicht wieder auf „Alle". Ein
gemerkter Filter erzeugt sonst den Moment, in dem nach zwei Wochen drei von
zwölf Geräten dastehen und niemand mehr weiß, warum.

### 6.4 Sortierung

Räume alphabetisch, „Ohne Raum" immer zuletzt. Innerhalb eines Raums: nach
`category_rank`, darin alphabetisch nach Label über `localeCompare` (damit
„Ä" bei „A" einsortiert und nicht hinter „Z"). Alle Steckdosen eines Raums
stehen damit beieinander, dann die Taster, dann der Rest.

**Keine zweite Überschriftenebene.** Die Kategorien bekommen keine eigenen
Zwischenüberschriften unterhalb der Raumüberschrift: die Reihenfolge plus
die kategoriespezifischen Icons machen die Blöcke sichtbar, und zwei
Überschriftenebenen über vierspaltigen Kacheln wären mehr Struktur als
Inhalt.

### 6.5 Icons

Acht `<symbol>`-Einträge im bestehenden Inline-SVG-Block
(`index.html:64-86`), einer je Kategorie, `#i-device` bleibt als `other`.
Weiterhin inline und ohne Icon-Bibliothek, aus demselben Grund wie das
eingecheckte `vendor/alpine.min.js`: die Oberfläche läuft offline. Damit ist
offener Punkt 1 des Dashboard-Entwurfs erledigt.

### 6.6 Suche

Ein Feld rechts in der Raumleiste, rein clientseitig über die ohnehin
geladene Geräteliste — kein Endpunkt, keine Abfrage. Getroffen wird
case-insensitiv in **Name, Kategoriename und Raumname**. Weil der
Kategoriename der übersetzte ist, findet „Steckdose" auf Deutsch und
„socket" auf Englisch dieselben Geräte.

**Die Suche wirkt innerhalb des gewählten Raums**, Chip und Feld gelten
zusammen (UND). Bei „Alle" durchsucht sie alles und bleibt nach Raum
gruppiert.

Damit entsteht ein Fall, den die Ansicht auffangen muss: kein Treffer im
gewählten Raum, obwohl das gesuchte Gerät nebenan steht. Der Leerzustand
zeigt deshalb nicht nur „kein Treffer", sondern zählt die Treffer außerhalb
mit — „3 weitere Treffer in anderen Räumen — alle anzeigen", der Verweis
schaltet auf „Alle" um und behält den Suchbegriff.

### 6.7 Einlern-Karte

Drittes Feld neben Pairing-Code und Thread-Datensatz: ein Auswahlfeld mit
den vorhandenen Räumen, Vorgabe „Ohne Raum", letzter Eintrag „+ Neuer
Raum …" blendet ein Textfeld für den Namen ein.

**Der gewählte Raum bleibt nach erfolgreichem Einlernen stehen** — anders
als Code und Thread-Datensatz, die weiterhin geleert werden (`app.js:1093`).
Wer vier Geräte in der Küche einlernt, wählt den Raum einmal; ein
Pairing-Code dagegen ist nach Gebrauch wertlos und ein stehengebliebener
wäre eine Fehlerquelle.

## 7. Sprache

Jeder neue Text läuft über `i18n.t()` mit `en`/`de`-Paar in
`strings.yaml` — die WebUI ist seit i18n-Phase B durchgehend übersetzt,
hartkodierter deutscher Text wäre ein Rückschritt. Neue Schlüssel im
Namensraum `web.devices.*` (Raumleiste, Raumwahl, Suche, Leerzustände),
`web.devices.category.*` (die acht Kategorienamen aus 5.1) und
`api.devices.*` für die Fehlermeldungen aus Abschnitt 8.

## 8. Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| `PATCH /api/devices/{id}` mit `room: ""` | Raum wird entfernt (`NULL`), 200 — das ist die dokumentierte Bedeutung, kein Fehler |
| `PATCH` mit einem Raumnamen aus reinem Leerraum | wird getrimmt und damit zu `NULL`, wie oben — keine 422 für etwas, das eine eindeutige Bedeutung hat |
| `POST /api/rooms/rename`, Quellraum trägt kein aktives Gerät | 404, mit dem Namen in der Meldung — analog zu `GET /devices/{id}` für ein entferntes Gerät |
| `POST /api/rooms/rename` mit leerem Zielnamen | 422 — „Raum umbenennen" ist nicht der Weg, einen Raum aufzulösen; dafür gibt es die Raumwahl an der Kachel |
| `POST /api/devices/commission` mit `room` | Raum wird mitgeschrieben; scheitert das Einlernen, entsteht kein Gerät und damit auch kein Raum |
| Gerät offline | unverändert wie bisher: Werte zeigen den letzten Stand, Kachel gedimmt, Befehle deaktiviert. Raumwahl und Export bleiben bedienbar — beide brauchen das Gerät nicht |
| `device_types IS NULL` (noch nicht nachgetragen) | Kategorie `other`, Icon `#i-device`, einsortiert ans Ende des Raums. Kein Hinweis, keine Warnung — der Zustand behebt sich beim nächsten Start von selbst |

## 9. Prüfung

**Store und Migration**

- Eine Datenbank auf Version 6 bekommt beide Spalten und steht danach auf 7;
  eine frisch angelegte hat sie durch `_SCHEMA` bereits und übersteht
  `_migrate_to_v7` ohne „duplicate column".
- `set_room` verändert `updated_at` **nicht**: ein Gerät, das laut
  `GET /api/export/status` nicht ausstehend ist, ist es nach einer
  Raumzuweisung immer noch nicht. Dasselbe für `backfill_device_types`.
- `rename_device` setzt `updated_at` weiterhin — der bestehende Test dazu
  muss unverändert grün bleiben.
- `backfill_device_types` überschreibt eine bereits gefüllte Zeile nicht,
  und ein Gerät, das in den übergebenen Abbildern fehlt, behält seine Typen.
- `rename_room` fasst nur aktive Geräte an: ein über `forget_device`
  entferntes Gerät im selben Raum behält seinen alten Raumnamen in der
  Zeile.

**Kategorie**

- Ein Knoten mit Root Node auf Endpunkt 0 und On/Off Plug-in Unit auf
  Endpunkt 1 ergibt `socket` — Endpunkt 0 wird übersprungen.
- Ein Endpunkt mit zwei zuordenbaren Typen ergibt den mit dem niedrigeren
  Rang, unabhängig von der Reihenfolge in der Deklaration.
- Unbekannte Typ-ID und `device_types = NULL` ergeben beide `other`.

**API**

- `PATCH /api/devices/{id}` mit nur `room` lässt das Label unangetastet, mit
  nur `label` den Raum.
- `POST /api/rooms/rename` auf einen bestehenden Zielnamen führt zusammen
  und meldet die Gesamtzahl der geänderten Geräte.
- `POST /api/devices/commission` mit `room` liefert ein `DeviceOut`, dessen
  `room` gesetzt ist.

**Oberfläche** (im Stil der bestehenden `tests/api/test_web.py`)

- Raumleiste, Suchfeld und Raumwahl an der Kachel sind im ausgelieferten
  HTML vorhanden.
- Alle neuen sichtbaren Texte kommen über `t(...)` und haben in
  `strings.yaml` sowohl `en` als auch `de` — dafür gibt es in
  `tests/test_i18n.py` bereits die Vollständigkeitsprüfung.

## 10. Offene Punkte

1. Die vollständige Tabelle Matter-Typ-ID → Kategorie (5.1) gehört in den
   Implementierungsplan und muss pro Eintrag gegen
   `matter_server.client.models.device_types` belegt werden.
2. Ob ein Gerät, dessen Typen sich beim erneuten Interview ändern (etwa nach
   einem Firmware-Update), seine `device_types` aktualisiert bekommen soll,
   ist bewusst offen gelassen: `backfill_device_types` schreibt nur
   `NULL`-Zeilen. Der Fall ist bisher nie beobachtet worden und bekommt
   keine Mechanik auf Verdacht.
3. Die Suche greift nur auf das zu, was `GET /api/devices` liefert — Name,
   Kategorie, Raum. Ob sie später auch Signaltitel durchsuchen soll, ist
   eine eigene Frage; sie bräuchte die Signale aller Geräte im Frontend und
   damit einen anderen Ladeweg.
