# Gerätekachel: Aktionen hinter ein Kebab-Menü

Entwurf, 5. September 2026. Ändert die Fußzeile der Kachel aus
[dem Geräte-Tab-Entwurf](2026-09-05-geraete-tab-raeume-und-kachelraster-design.md),
Abschnitt 6.2 — und macht dabei den Teil davon rückgängig, der die meiste
Nacharbeit gekostet hat.

## 1. Das Problem

Die Fußzeile trägt heute vier Dinge nebeneinander: ein Auswahlfeld für den
Raum, ein bei Bedarf eingeblendetes Textfeld für einen neuen Raum, den
Export-Hinweis und zwei Icon-Tasten. Auf einer 300 px breiten Kachel ist das
die vollste Zeile der ganzen Ansicht, und das Auswahlfeld ist darin das
lauteste Element — Rahmen, Pfeil, eigene Höhe —, obwohl es die am seltensten
gebrauchte Funktion ist: einen Raum weist man einmal zu, danach nie wieder.

Dazu kommt ein zweiter, schwerer wiegender Punkt. Dieses eine Auswahlfeld hat
sechs Review-Runden gebraucht, alle an derselben Ursache: `newRoomFor` ist
Modus-Zustand in JavaScript, und er steuerte, was ein **natives** `<select>`
anzeigt. Eine native Auswahlliste meldet nicht, dass jemand sie geöffnet und
ohne Auswahl wieder verlassen hat; und Alpine wendet die Direktiven eines
Elements an, bevor es dessen Kinder aufbaut, weshalb `:value` gegen ein per
`x-for` gefülltes `<select>` schlicht ins Leere läuft. Die heutige Lösung
(`x-model` gegen `roomSelectDrafts`, plus ein `focusout`-Wächter) ist
korrekt, aber sie besteht aus Gegengewichten zu einer Kopplung, die es nicht
geben müsste.

## 2. Was unverändert bleibt

- **Kopfzeile, Werteraster und Bedienleiste der Kachel.** Der ganze obere
  Teil bleibt, wie er ist.
- **Raumleiste, Gruppierung, Sortierung, Suche.** Das Menü ändert nur, wie
  ein Raum *zugewiesen* wird, nicht wie er wirkt.
- **Der Umbenennen-Stift** an der Gruppenüberschrift und am aktiven Chip.
- **Die API.** `PATCH /api/devices/{id}` mit `{room}` bleibt der einzige
  Schreibweg, `""` heißt weiterhin „Raum entfernen". Es entsteht keine neue
  Route und kein neues Feld.

## 3. Die Fußzeile wird eine Zeile

Links der Export-Hinweis (`exportHintFor`), rechts ein ⋮. Sonst nichts.

Der Raum verschwindet damit von der Kachel — bewusst: er ist dort ohnehin
redundant. Bei „Alle" nennt ihn die Gruppenüberschrift über den Kacheln, im
gefilterten Zustand der aktive Chip in der Raumleiste. Ein drittes Mal
dasselbe zu sagen kostet die vollste Zeile der Ansicht.

## 4. Das Menü

**Ein natives `<details>`** mit dem ⋮ als `<summary>`. Dasselbe Primitiv, das
die Projektdatei-Sync-Ansicht schon viermal nutzt (`index.html`,
`.projectsync-device` und `.projectsync-unchanged-disclosure`) — kein neues
Muster im Haus.

Der Gewinn ist nicht Bequemlichkeit, sondern die Vermeidung genau des
Fehlermusters aus Abschnitt 1: **der Auf-/Zu-Zustand lebt im DOM, nicht in
Alpine.** Es gibt keinen JavaScript-Zustand, der mit dem sichtbaren Zustand
eines nativen Bedienelements in Deckung gehalten werden müsste, also auch
keinen Pfad, auf dem beide auseinanderlaufen können.

**Inhalt, von oben nach unten:**

1. Die Räume als Einträge: „Ohne Raum", dann jeder vorhandene Raum
   alphabetisch (`roomChips()` liefert die Liste bereits in dieser
   Reihenfolge). Der aktuelle Raum trägt ein Häkchen — bei einem Gerät ohne
   Raum steht es bei „Ohne Raum", das ist dort kein Sonderfall, sondern der
   Normalzustand. Ein Klick weist zu (`saveRoom`) und schließt das Menü.
2. „+ Neuer Raum …" — blendet ein Textfeld **innerhalb** des Menüs ein.
   Enter speichert und schließt, Escape bricht ab. Das Menü bleibt beim
   Tippen offen, weil das Feld sein Kind ist.
3. Ein Trenner.
4. „Exportieren" (`exportDevice`) — deaktiviert, solange keine Brücken-IP
   hinterlegt ist, mit demselben Hinweis und Verweis auf die Einstellungen
   wie heute.
5. „Entfernen" (`removeDevice`) in `--danger`, mit der bestehenden
   Rückfrage.

**Schließen.** Drei Wege, und der dritte ist der, den man leicht vergisst:

| Auslöser | Umsetzung |
|---|---|
| Klick auf einen Eintrag | der Eintrag setzt `open = false` |
| Klick daneben | `@click.outside` auf dem `<details>` — Alpine 3.17.1 im `vendor/`-Ordner bringt den Modifier mit (geprüft) |
| Escape | `@keydown.escape` — **`<details>` schließt von sich aus NICHT** bei Escape, anders als ein `<dialog>` |

„Immer nur ein Menü offen" ergibt sich daraus von selbst: ein Klick auf den
⋮ einer anderen Kachel liegt außerhalb des ersten `<details>` und schließt
es über denselben `@click.outside`.

## 5. Was ersatzlos entfällt

Der eigentliche Ertrag dieses Entwurfs. Aus `app.js`:

- `roomSelectDrafts` samt `syncRoomSelectDraft` — die zuweisbare Kopie von
  `device.room`, die es nur gab, weil `x-model` nicht auf einen Ausdruck
  zeigen kann.
- `onRoomSelectChange` — die Sonderbehandlung von „+ Neuer Raum …", damit
  die Auswahlliste nie `__new__` anzeigt.
- Die Kopplung von `newRoomFor` an ein `<select>`. Das Feld selbst bleibt,
  aber nur noch als Sichtbarkeitsschalter für das Textfeld im Menü — also
  in genau der Rolle, für die es ursprünglich gedacht war.
- Der `syncRoomSelectDraft`-Aufruf in `loadDevices` und in
  `commissionDevice`.

Aus `index.html`: der `.room-picker`-Wrapper mit seinem `focusout`-Wächter,
das `<select class="room-select">`, das Textfeld `.room-new` an dieser
Stelle — und der 50-Zeilen-Kommentar davor, der die Geschichte der Kopplung
erzählt. Aus `style.css`: `.room-select`, `.room-new`, `.room-picker`.

Was bleibt: `saveRoom`, `beginNewRoom`, `commitNewRoom`, `roomKeyOf`,
`roomChips`, `reconcileRoomFilter` — alle unverändert in ihrer Aufgabe,
`beginNewRoom`/`commitNewRoom` nur ohne den Umweg über die Auswahlliste.

**Fünf Tests in `tests/api/test_web.py` prüfen Konstrukte, die es danach
nicht mehr gibt** (`test_the_page_offers_the_room_bar_and_the_room_picker`
teilweise, `…room_select_uses_a_synced_draft…`,
`…new_room_option_resets_the_draft…`,
`…room_select_leaves_new_room_mode_when_a_normal_room_is_picked`,
`…room_picker_closes_new_room_mode_when_focus_leaves_it_entirely`). Sie
werden **gelöscht, nicht umgeschrieben**: ihre Aussagen betreffen eine
Mechanik, die ersatzlos verschwindet. Was von ihrer Absicht bleibt — „die
Kachel zeigt nie einen Raum, den das Gerät nicht hat" — ist danach keine
prüfbare Behauptung mehr, weil die Kachel den Raum überhaupt nicht mehr
zeigt.

## 6. Sprache

Weiterverwendet: `web.devices.room_none`, `room_new`,
`room_new_placeholder`, `export`, `remove`, `export_hint_prefix`/`_suffix`.

Neu, mit `en`- und `de`-Eintrag:

| Schlüssel | en | de |
|---|---|---|
| `web.devices.menu` | Actions | Aktionen |
| `web.devices.menu_room_heading` | Room | Raum |

`web.devices.menu` ist der zugängliche Name des ⋮-Knopfs — er trägt kein
Wort, also braucht er einen. `menu_room_heading` beschriftet den
Raum-Abschnitt im Menü, damit die Liste der Raumnamen nicht ohne Zusammenhang
über den beiden Aktionen steht.

## 7. Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| `saveRoom` scheitert (Netz, 4xx) | wie heute: `deviceActionError` als Banner, das Menü ist zu diesem Zeitpunkt bereits geschlossen — der Fehler steht über der Liste, nicht in einem verschwundenen Menü |
| „+ Neuer Raum …", Feld leer bestätigt | nichts wird gespeichert, Menü schließt; identisch zum heutigen `commitNewRoom` |
| Keine Brücken-IP hinterlegt | „Exportieren" deaktiviert, Hinweis mit Verweis auf die Einstellungen bleibt unter der Fußzeile stehen, nicht im Menü — er gilt für alle Kacheln, nicht für diese eine |
| Gerät wird entfernt, während sein Menü offen ist | die Kachel verschwindet mitsamt `<details>`; kein Zustand bleibt zurück, weil keiner außerhalb des DOM lag |

## 8. Prüfung

Die WebUI-Tests belegen nur, dass ein Konstrukt ausgeliefert wird — für
Verhalten braucht es den Browser (siehe die Erfahrung aus dem
Vorgänger-Entwurf). Deshalb beides:

**Ausgeliefert** (`tests/api/test_web.py`): das `<details class="tile-menu">`
mit `@click.outside` und `@keydown.escape`; die fünf Menüeinträge; **kein**
`room-select`, `roomSelectDrafts` oder `onRoomSelectChange` mehr im
ausgelieferten `app.js`.

**Verhalten** (Wegwerf-Harness gegen das vendorte Alpine, Ergebnisse in den
Umsetzungsbericht):

- Menü öffnet und schließt über den ⋮; Klick daneben schließt; Escape
  schließt.
- Ein Klick auf den ⋮ einer zweiten Kachel schließt das erste Menü.
- Der aktuelle Raum trägt das Häkchen; ein Klick auf einen anderen weist zu,
  die Kachel wandert in die richtige Gruppe, das Menü ist zu.
- „+ Neuer Raum …": Textfeld erscheint im Menü, Menü bleibt beim Tippen
  offen, Enter speichert und schließt, Escape bricht ab.
- Nach dem Zuweisen des letzten Geräts eines gefilterten Raums greift
  `reconcileRoomFilter` weiterhin (der Fall aus der Re-Review).

## 9. Offene Punkte

1. Ob die Menüeinträge zusätzlich Tastaturnavigation mit Pfeiltasten
   bekommen sollen, bleibt offen. `<details>` gibt Tab-Reihenfolge und
   Aktivierung mit Enter/Leertaste von sich aus; Pfeiltasten wären eine
   eigene Tastaturschleife und damit wieder eigener Zustand — genau das,
   wovon dieser Entwurf wegwill. Erst nachrüsten, wenn es jemandem fehlt.
2. Bei sehr vielen Räumen wird das Menü lang. Ab welcher Zahl es eine
   eigene Scrollfläche oder ein Untermenü braucht, ist heute nicht
   entscheidbar — die bisher belegte Größenordnung sind eine Handvoll Räume.
