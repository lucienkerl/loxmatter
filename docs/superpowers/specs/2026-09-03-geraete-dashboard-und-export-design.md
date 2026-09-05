# Geräte-Dashboard: Werte ohne Aufklappen, Export pro Gerät

Entwurf, 3. September 2026. Ergänzt
[das Hauptdokument](2026-09-01-matter-loxone-bridge-design.md), Abschnitt 8
(Bedienoberfläche), sowie [den Login-Entwurf](2026-09-03-webui-login-design.md),
Abschnitt 14.2 (Restliche Konfiguration in der Oberfläche) — dessen
`setting`-Tabelle war genau für diesen Zweck vorgesehen, ist bisher aber nur
mit dem Passwort-Hash belegt.

## 1. Das Problem

Zwei getrennte Beschwerden, ein gemeinsamer Auslöser: die Gerätekachel in der
Ansicht „Geräte" zeigt im eingeklappten Zustand nur den Status-Punkt, den
Namen und einen Hinweistext auf die Export-Anzahl. Wer wissen will, ob eine
Steckdose gerade an ist oder wie warm ein Sensor misst, muss pro Gerät auf
„Details" klicken.

Zweitens: ein Export ist heute ausschließlich eine Alle-oder-Ausstehende-
Operation im eigenen Tab, mit eigenen Eingabefeldern für die Brücken-Adresse
und die Ports. Ein einzelnes, gerade fertig eingerichtetes Gerät zu
exportieren heißt: Tab wechseln, Adresse erneut eingeben (sie wird nirgends
gespeichert), Vorschau oder direkt herunterladen.

## 2. Was unverändert bleibt

Die Ansicht „Signale" (voller Baum, Funktional/Experte, einzeln umschaltbare
Exportierbarkeit) bleibt exakt wie sie ist — sie ist der Ort für die
vollständige Sicht auf ein Gerät, nicht die Gerätekachel. Der globale
Export-Tab bleibt ebenfalls bestehen; er ist weiterhin der einzige Weg,
mehrere oder alle Geräte auf einmal zu exportieren.

Ausdrücklich **nicht** Teil dieses Entwurfs: die übrige Konfiguration aus
Abschnitt 14.2 des Login-Entwurfs (Miniserver-Adresse, matter-server-Adresse,
Datenverzeichnis) — die kommt weiterhin aus `docker-compose.yml`/CLI-Optionen
und bekommt, wie dort angekündigt, ihre eigene Spec. Dieser Entwurf belegt
die `setting`-Tabelle mit genau drei weiteren Schlüsseln: den Feldern, die
heute im Export-Tab stehen (Brücken-IP, UDP-Port, HTTP-Port).

## 3. Gerätekachel: immer offen

**Kein Aufklappen mehr.** Der „Details"/„Einklappen"-Umschalter
(`toggleExpanded`, `expandedDeviceId`, `index.html:206-291`) entfällt. Jede
Kachel lädt und zeigt beim Betreten der Ansicht sofort, was heute erst nach
einem Klick da ist: die funktionalen Signale (`firstSignalsFor`,
`FUNCTIONAL_PREVIEW_LIMIT` bleibt bei 6) und die Bedienelemente
(`commandsFor`).

**Aufbau einer Kachel, von oben nach unten:**

1. **Kopfzeile** — Typ-Icon in getöntem Kreis, editierbarer Name, rechts eine
   Status-Pille.
2. **Werte** — die funktionalen Signale als Chips (Label über/neben dem
   Wert, Wert in Mono-Zahlen).
3. **Bedienung** — die bekannten Befehle als Tasten, wie bisher.
4. **Fußzeile** — Export-Hinweis (zuletzt exportiert / geändert seit Export)
   links, rechts die Tasten „Exportieren" und „Entfernen".

**Status-Pille, drei Zustände, konsistent farbcodiert:**

| Zustand | Bedingung | Farbe | Icon |
|---|---|---|---|
| Unauffällig | online, `!changed_since_export` | kein Pillentext, nur grüner Rand-Streifen an der Kachel | — |
| Geändert seit Export | online, `changed_since_export` (bereits vorhanden, `ExportStatusOut`, siehe Abschnitt 5) | Amber | Warndreieck |
| Offline | `!isOnline(device)` | Grau | „wifi-off" |

Der Rand-Streifen (4 px, linke Kante) trägt zusätzlich zur Pille dieselbe
Bedeutung — bewusst redundant, damit der Zustand einer Kachel auch beim
schnellen Scrollen über viele Geräte auffällt, ohne die Pille lesen zu
müssen.

`changed_since_export` kommt heute nur über `GET /api/export/status` an,
aufgerufen von `loadExportStatus()` (`app.js:896-908`), bislang ausschließlich
aus dem Export-Tab heraus. Die Geräte-Ansicht muss diesen Aufruf künftig
selbst mit auslösen (analog, keine neue Route), sonst hat die Status-Pille
dort keine Datengrundlage.

**Typ-Icon.** Ein kleines, eigenes Satz an Strich-Icons (kein Icon-Font,
keine externe Bibliothek — die Oberfläche läuft offline, `vendor/alpine.min.js`
ist aus genau diesem Grund eingecheckt statt von einem CDN geladen). Die
Zuordnung Gerätetyp → Icon kommt aus derselben Quelle, die heute schon
`device_types` für die Relevanz-Regel auswertet
([Signalauswahl-Entwurf](2026-09-03-signalauswahl-design.md) Abschnitt 4.1):
Steckdose/Relais → Stecker-Symbol, Sensor mit Bewegungs-Cluster →
Bewegungs-Symbol, Fenster/Beschattung → Lamellen-Symbol, alles nicht
zugeordnete → ein neutrales Platzhalter-Symbol. Eine vollständige Zuordnungs-
tabelle ist Sache des Implementierungsplans, nicht dieser Spec.

**Farbe.** Akzentfarbe wird von Grün auf Kupfer/Amber (`#a15a2c` hell,
`#e2915c` dunkel) umgestellt — für Primär-Tasten, Marke, Typ-Icon-Hintergrund.
Die Statusfarben (grün = unauffällig, amber = geändert, grau = offline)
bleiben davon getrennt und ändern sich nicht: eine amber Primär-Taste neben
einer amber Status-Pille wäre sonst missverständlich, deshalb bleibt „amber"
ausschließlich für den Status „geändert seit Export" reserviert und die
Akzentfarbe der Primär-Taste ist Kupfer, ein sichtbar anderer Ton. Rest der
Palette (Hintergrund, Fläche, Rahmen, Text) bleibt unverändert
(`style.css:27-61`).

Diese drei Punkte (Layout, Status-Pillen, Kupfer/Amber) sind mit dem
Auftraggeber an einem interaktiven HTML-Entwurf durchgesprochen und
freigegeben.

## 4. Neuer Tab „Einstellungen"

Fünfter Tab, gleichrangig zu Geräte/Signale/Export/System
(`nav.tabs`, `index.html`). Eine Karte „Verbindung zum Miniserver" (nicht
„Miniserver", um denselben Denkfehler zu vermeiden, vor dem der bestehende
Hinweistext im Export-Tab schon warnt — gemeint ist die Adresse **dieser
Brücke**, wie der Miniserver sie sieht, nicht die Adresse des Miniservers
selbst):

- IP dieser Brücke
- UDP-Port (virtueller Eingang)
- HTTP-Port (Befehle empfangen)
- Taste „Speichern", Hinweis „Zuletzt gespeichert vor …"

**Speicherung.** Die drei Werte gehen in die bestehende generische
`setting`-Tabelle (`store.py:128-131`, angelegt in `_migrate_to_v5` für den
Passwort-Hash, laut eigenem Docstring in `auth_store.py:42-45` genau für
diese Erweiterung gedacht). Neue Schlüssel `bridge_ip`, `bridge_udp_port`,
`bridge_listen_port`, gelesen/geschrieben über denselben
Upsert-Zugriff (`INSERT … ON CONFLICT DO UPDATE`), wie ihn `AuthStore` schon
für `password_hash` vormacht (`auth_store.py:55-90`) — eigene kleine Klasse
oder Erweiterung von `AuthStore`, das ist Sache des Implementierungsplans.
Zwei neue Endpunkte nach dem Muster von `GET`/`PATCH /devices/{id}`
(`api/devices.py:191-206`): `GET /api/settings` und `PATCH /api/settings`.

Server-seitig statt `localStorage`, weil die Brücken-Adresse eine
Eigenschaft der Installation ist, nicht des Browsers — mehrere Personen oder
Geräte, die dasselbe Dashboard öffnen, sollen dieselbe Adresse sehen, ohne
sie erneut einzutippen.

Eine zweite Karte „Weitere Einstellungen" mit einem Platzhaltersatz markiert
sichtbar, dass hier künftig mehr hinzukommt (vgl. Abschnitt 2 — nicht Teil
dieses Entwurfs).

## 5. Export-Tab: Felder werden schreibgeschützt, Vorschau/Download bleiben

Die drei Eingabefelder im bestehenden Export-Tab
(`exportBridgeIp`/`exportPort`/`exportListenPort`, `app.js:249-251`) werden
`readonly`, vorbelegt aus `GET /api/settings`, mit einem Verweis
„Wird in Einstellungen → Verbindung zum Miniserver verwaltet". Checkboxen,
„Vorschau ansehen", „ZIP herunterladen" bleiben unverändert
(`index.html:456-479`, `app.js:914-999`) — dieser Tab bleibt der Weg für
„alle" oder „alle ausstehenden" Geräte.

## 6. Export pro Gerät (neuer Button in der Fußzeile der Kachel)

**Kein Vorschauschritt.** Ein Klick auf „Exportieren" an der Kachel lädt
sofort das ZIP für genau dieses eine Gerät — die Werte stehen ja bereits
offen auf der Kachel, eine zusätzliche Vorschau wäre doppelte Information.

**Backend-Änderung, keine neue Route.** `GET /api/export/download`
(`api/export.py:238-257`) bekommt einen optionalen Parameter `device_id`.
Ist er gesetzt, iteriert die Auswahl (heute `for device in store.devices()`,
`export.py:305`) nur über dieses eine Gerät, unabhängig vom
`only_pending`-Haken, und markiert nach erfolgreichem Bau des ZIP nur dieses
eine Gerät als exportiert (dieselbe deferred-`mark_exported`-Logik wie
heute, siehe Kommentare in `export.py`). `GET /api/export/preview` bleibt
unverändert — sie wird von der Kachel aus nicht aufgerufen (Abschnitt „Kein
Vorschauschritt" oben).

Frontend: `downloadUrl()`/`downloadExport()` (`app.js:956-999`) werden um
eine zweite, kachel-eigene Variante ergänzt, die denselben `download()`-Weg
nutzt (Fehlerantworten laufen über die Oberfläche, nicht als roher Text —
derselbe Grund, aus dem der globale Export schon kein `<a href>` ist,
`index.html:472-477`), aber `device_id` statt der Checkbox-Parameter
mitgibt.

## 7. Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| Einstellungen noch leer (kein `bridge_ip` gespeichert) | „Exportieren"-Taste an jeder Kachel deaktiviert, Hinweistext „Erst in Einstellungen → Verbindung zum Miniserver hinterlegen", mit Link auf den Tab |
| Gerät offline | Werte zeigen den letzten bekannten Stand (Kachel gedimmt, wie im Entwurf), Befehle-Tasten deaktiviert, „Exportieren" bleibt aktiv — die zuletzt bekannte Konfiguration ist weiterhin ein gültiger Export |
| `device_id` in `/api/export/download` unbekannt (Gerät zwischenzeitlich entfernt) | 404, wie es `GET /devices/{id}` heute schon für denselben Fall liefert |
| `PATCH /api/settings` mit leerer Brücken-IP | 422, analog zur bestehenden Pflichtfeld-Prüfung von `bridge_ip` in `export.py` |

## 8. Prüfung

- Ein Gerät ohne gespeicherte Einstellungen: „Exportieren"-Taste an der
  Kachel ist deaktiviert, kein Aufruf gegen `/api/export/download` möglich.
- `GET /api/export/download?device_id=…` liefert ein ZIP mit genau den
  Dateien dieses einen Geräts und markiert ausschließlich dieses eine Gerät
  als exportiert — ein zweites vorhandenes Gerät bleibt unangetastet
  (`exported_at` unverändert).
- `only_pending=true` zusammen mit `device_id` gesetzt: `device_id` gewinnt,
  das Gerät wird exportiert, auch wenn es laut `changed_since_export` nicht
  ausstehend wäre (Abschnitt 6).
- `GET`/`PATCH /api/settings` — Werte überstehen einen Neustart des
  Prozesses (Migration/Tabelle bereits vorhanden, kein Schema-Update nötig).
- Bestehende Tests für `toggleExpanded`/`expandedDeviceId` entfallen mit der
  Funktion; neue Tests decken ab, dass Werte und Bedienelemente ohne Klick
  sichtbar sind.

## 9. Offene Punkte

1. Die vollständige Zuordnungstabelle Gerätetyp → Icon (Abschnitt 3) ist
   Sache des Implementierungsplans, nicht dieser Spec — die drei im Entwurf
   gezeigten Typen (Stecker, Bewegung, Lamellen) sind ausreichend belegt,
   weitere Matter-Gerätetypen brauchen ein Platzhalter-Symbol, bis sie
   einzeln ergänzt werden.

   **Erledigt** durch den [Geräte-Tab-Entwurf vom 5. September 2026](2026-09-05-geraete-tab-raeume-und-kachelraster-design.md):
   die Zuordnung ist `profiles/categories.py`, und sie liefert nicht nur das
   Icon, sondern auch die Sortierung innerhalb eines Raums und den
   Suchbegriff.
2. Ob ein HTTP-Port-Konflikt (z. B. zwei Bridges auf demselben Host) beim
   Speichern der Einstellungen geprüft werden soll, ist offen — bis jemand
   danach fragt: nein, wie schon beim bestehenden Export-Tab.
3. Migration bestehender Installationen: wer heute schon Bridge-IP/Ports im
   Export-Tab eingetragen hat, verliert diese Eingabe beim ersten Start
   dieser Fassung (sie stand nie in der Datenbank, s. Abschnitt 1) und muss
   sie einmalig in Einstellungen neu eintragen. Keine automatische
   Übernahme möglich, weil der bisherige Wert nirgends abgelegt war.
