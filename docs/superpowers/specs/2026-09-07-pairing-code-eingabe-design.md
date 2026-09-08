# Pairing-Code: schreiben, wie er auf dem Gerät steht

Entwurf, 7. September 2026. Betrifft die Einlern-Karte der Geräteansicht —
zuletzt umgebaut im Entwurf „Code zuerst" (siehe den Kommentar über
`<div x-show="commissionStep === null">` in `web/index.html`) — sowie die
Route `POST /api/devices/commission`.

## 1. Das Problem

Auf jedem Matter-Gerät steht der Zahlencode gruppiert: `1234-567-8901`. So
steht er auf dem Aufkleber, so zeigen ihn Apple, Google und jede Anleitung.
Wer ihn abliest und abtippt, tippt die Bindestriche mit — es sieht ja aus
wie eine Telefonnummer.

Das Feld nimmt ihn so entgegen und reicht ihn ungefiltert weiter:

```
web/app.js:1665     body = { code: this.commissionCode.trim() }
api/models.py       CommissionRequest.code: str
api/devices.py:419  active_client.commission_with_code(request.code)
matter/client.py    upstream.commission_with_code(code)
```

Und weiter, geprüft gegen die installierte Fassung statt vermutet:
`MatterClient.commission_with_code` (`matter_server/client/client.py:140`)
setzt den String unverändert in den WebSocket-Befehl. **Auf dem ganzen Weg
von der Tastatur bis zum Matter-Stack schneidet niemand die Bindestriche
weg.**

Ob das CHIP-SDK im matter-server-Container sie am Ende doch schluckt, lässt
sich von hier aus nicht belegen — der Parser läuft dort, nicht hier. Genau
das ist der Punkt: die Brücke verlässt sich derzeit auf eine Zusage, die
niemand gegeben hat. Trifft sie nicht zu, scheitert das Einlernen mit
„Commission with code failed for node N", und der Grund — ein Bindestrich —
steht in keiner Meldung.

Dazu ein zweites, kleineres Problem: das Feld sagt nicht, was es erwartet.
Sein Platzhalter lautet „Pairing-Code (11-stellig oder MT:…)". Das ist die
Bauform, nicht das Aussehen. Wer den Aufkleber in der Hand hält, muss den
Klammerzusatz erst in „die untere der beiden Zahlen" übersetzen.

## 2. Was dieser Entwurf will

1. Das Feld **schreibt den Code so, wie er auf dem Gerät steht** — die
   Bindestriche erscheinen beim Tippen von selbst.
2. Das Feld **benennt, was es erkannt hat** — Zahlencode, QR-Code, oder wie
   viele Ziffern noch fehlen.
3. Die Brücke **schneidet die Trenner selbst weg**, an einer Stelle, für
   jeden Aufrufer.

## 3. Was unverändert bleibt

- **Die Einlern-Logik.** `commissionDevice()`, die Ablaufanzeige, die
  Fehlerbehandlung nach Status, das Nachladen von Signalen und Befehlen —
  kein Zeichen.
- **Die Karte als Ganzes.** Ein Pflichtfeld in der ersten Zeile, Raum und
  Klappen in der zweiten. Der Entwurf „Code zuerst" bleibt gültig; hier
  ändert sich nur, was *in* der ersten Zeile passiert.
- **Die beiden `<details>`-Klappen** samt ihren Texten.
- **Der gemeinsame Rahmen um Feld und Knopf** (`.code-field`) und die
  Monospace-Schrift darin. Beide sind für diesen Entwurf Voraussetzung, kein
  Beiwerk: in Proportionalschrift wandern die Ziffern beim Nachformatieren.

## 4. Die Formatier-Regel

Eine Funktion, ein Ort, zwei Fälle.

```js
// Etwas anderes als Ziffern, Leerzeichen, Bindestriche => QR-Inhalt.
function isQr(raw) { return /[^0-9\s-]/.test(raw); }
```

Die Prüfung greift damit **beim ersten getippten `M`** von `MT:`, nicht erst
beim Doppelpunkt. Das ist Absicht: eine Regel, die auf `MT:` wartet, würde
die zwei Zeichen davor als Zifferneingabe behandeln und sie wegwerfen.

- **QR-Inhalt** bleibt Zeichen für Zeichen stehen. Er ist Base38-kodiert;
  jede Gruppierung wäre eine Erfindung.
- **Zahlencode**: Ziffern herausziehen, die ersten elf als `4-3-4`
  gruppieren, den Rest ungruppiert anhängen.

**Es wird nichts abgeschnitten.** Eine Kürzung auf 21 Ziffern läge nahe,
wäre hier aber falsch: sie ließe Zeichen still verschwinden, und der
„zu lang"-Zustand des Chips (Abschnitt 6) wäre unerreichbar — eine Regel,
die nie greift. Wer sich vertippt, soll das lesen können, statt es zu
erraten.

**Ab der zwölften Ziffer wird nicht weiter gruppiert.** Für den elfstelligen
Code ist `4-3-4` die Schreibweise, die auf den Geräten steht. Für den
21-stelligen habe ich keine belegte Gruppierung gefunden. Eine erfundene
sähe anders aus als der Aufdruck — das Feld formatierte den Code dann *weg*
vom Vorbild statt hin. Der Chip aus Abschnitt 6 sagt trotzdem, dass ein
langer Code erkannt wurde; er bleibt also nicht unkommentiert, nur
ungruppiert.

Nach außen geht immer:

```js
// Getrimmt; beim Zahlencode ohne Trenner. QR-Inhalt unangetastet.
function normalize(raw) {
  const v = raw.trim();
  return isQr(v) ? v : v.replace(/\D/g, "");
}
```

## 5. Der Cursor

Live-Formatierung steht und fällt damit, dass der Cursor nicht springt.

**Beim Neusetzen**: Ziffern links vom Cursor zählen, Wert neu formatieren,
Cursor hinter dieselbe Anzahl Ziffern setzen. Die Zählung geht über Ziffern,
nicht über Zeichenpositionen — sonst verschöbe jeder neu eingefügte
Bindestrich den Cursor um eins.

**Beim Rückschritt auf einem Bindestrich** wird stattdessen die Ziffer davor
gelöscht. Ohne diese Sonderbehandlung löscht der Tastendruck den Trenner,
den die Formatierung unmittelbar danach wieder setzt: der Wert ändert sich
nicht, der Cursor bleibt stehen, und die Taste wirkt tot. Das ist der eine
Punkt, an dem eine mitformatierende Eingabe üblicherweise scheitert.

## 6. Der Chip im Feld

Rechts im Feld, zwischen Eingabe und Knopf, mit `aria-live="polite"`:

| Eingabe | Chip | Farbe |
|---|---|---|
| leer | *(unsichtbar, hält den Platz)* | — |
| 1–10 Ziffern | `noch 4 Ziffern` | `--warn` |
| 11 Ziffern | `Zahlencode` | `--ok` |
| 12–20 Ziffern | `noch 9 Ziffern` | `--warn` |
| 21 Ziffern | `Zahlencode, lang` | `--ok` |
| über 21 Ziffern | `zu lang` | `--danger` |
| beginnt mit `MT:` | `QR-Code` | `--ok` |
| sonstige Buchstaben | `kein gültiger Code` | `--danger` |

Er zählt gegen die nächste gültige Länge — erst 11, danach 21. Bei leerem
Feld ist er `visibility: hidden` statt entfernt, damit das Feld beim ersten
Tastendruck nicht seine Breite ändert.

**Der Chip beschreibt, er verbietet nicht.** Auch bei `zu lang` und `kein
gültiger Code` bleibt der Einlern-Knopf bedienbar und der Wert geht
unverändert an die Route. Das ist dieselbe Haltung wie in Abschnitt 8: die
Brücke sagt, was sie sieht, und lässt den Matter-Stack entscheiden. Eine
Oberfläche, die eine Eingabe sperrt, die der Stack angenommen hätte, wäre
nicht zu umgehen.

Damit trägt das Feld die Auskunft selbst, statt sie auf Platzhalter und
Hinweiszeilen zu verteilen. Und der Name des Codes wird an der Stelle
gelernt, an der man ihn eintippt — das war der Anlass für den Chip.

**Kein `inputmode="numeric"`.** Auf dem Telefon gäbe es die Zifferntastatur,
und dort steht man beim Ablesen. Es sperrte aber das Tippen eines QR-Textes
hinter eine Umschalttaste, und die Karte hat für beide Bauformen genau ein
Feld.

## 7. Die Aufkleber-Skizze

Ein Inline-SVG, rund 190×112, rechts neben dem Feld, dauerhaft sichtbar:

```
┌─────────────────────────────────────────────────────┐
│ PAIRING-CODE                                        │
│ ┌─────────────────────────────────────────────────┐ │
│ │ 1234-567-8901        [Zahlencode]  [Einlernen]  │ │
│ └─────────────────────────────────────────────────┘ │
│ Zahlencode 1234-567-8901   QR-Code MT:Y.K90SO527…   │
│                                                     │
│  ▓▒░ MATTER                                         │
│  ░▓▒ ┌───────────────┐   ← die Skizze steht         │
│  ▒░▓ │ 1234-567-8901 │      dauerhaft daneben       │
│      └───────────────┘                              │
└─────────────────────────────────────────────────────┘
```

Sie zeigt, wo auf dem Gerät die Zahl steht, die hier hingehört, und hebt sie
gegen den QR-Code daneben hervor. Wer den Aufkleber in der Hand hält, muss
dann keinen Satz lesen, um die richtige der beiden Zahlen zu finden.

**Zum Einwand, die Karte sei bewusst entrümpelt worden.** Sie ist es — der
Entwurf „Code zuerst" hat 78 Wörter dauerhaften Hilfetext in Klappen
verschoben. Eine Skizze fällt nicht darunter: sie kostet keinen Lesevorgang,
wer sie nicht braucht überliest sie, und sie ersetzt hier den Klammerzusatz
im Platzhalter. Der Text wird also nicht mehr, sondern weniger.

**Farben aus den vorhandenen Variablen**: `--bg` als Fläche, `--border` als
Rand, `--text` für QR-Muster und Ziffern, `--text-muted` für die
Beschriftungen, `--warn`/`--warn-bg` für die Hervorhebung der Zahl. Damit
trägt sie in beiden Themes, ohne eine eigene Farbe einzuführen — und die
Hervorhebung benutzt eine Farbe, die schon eine Bedeutung hat („sieh hier
hin"), nicht eine neue.

Das QR-Quadrat ist eine Andeutung aus Rechtecken (drei Suchmuster plus
Rauschen), kein lesbarer Code. Ein echter QR im Bild wäre eine Einladung,
ihn zu scannen, und führte nirgendwohin.

**Umbruch unter 520px** über das Feld statt daneben — dieselbe Schwelle, an
der `style.css:501` heute schon Feld und Knopf umbricht, und aus demselben
Grund: dort steht man mit dem Telefon vor dem Gerät.

Ein `aria-label` beschreibt, was zu sehen ist. Die Skizze ist eine
Verdeutlichung, keine Informationsquelle — die Beispielzeile darüber trägt
denselben Inhalt als Text.

## 8. Die Normalisierung im Backend

Ein `field_validator` auf `CommissionRequest.code`. Eine Stelle, gültig für
jeden Aufrufer der Route, nicht nur für unsere Oberfläche — ein per Hand
abgesetzter `curl` mit dem abgetippten Code soll nicht an derselben Stelle
scheitern, an der die Oberfläche gerade repariert wurde.

Er tut dasselbe wie `normalize()` in Abschnitt 4, und **er normalisiert nur,
er validiert nicht.**

Das ist die zweite tragende Entscheidung dieses Entwurfs. Die Brücke soll
hier nicht klüger sein wollen als der Matter-Stack: lehnte sie einen Code
ab, den der Stack angenommen hätte, gäbe es keinen Weg daran vorbei — und
die Bauformen der Setup-Codes sind nichts, was diese Brücke verwaltet.
Trenner wegzuschneiden ist verlustfrei; eine eigene Längenregel wäre eine
Wette auf eine Spezifikation, die sich ohne uns weiterentwickelt.

Ein leerer Code bleibt damit ein leerer Code und scheitert dort, wo er heute
scheitert.

## 9. Texte

Neu in `i18n/strings.yaml`, alle in beiden Sprachen:

| Schlüssel | de |
|---|---|
| `web.devices.code_detect_manual` | Zahlencode |
| `web.devices.code_detect_manual_long` | Zahlencode, lang |
| `web.devices.code_detect_qr` | QR-Code |
| `web.devices.code_detect_remaining_one` | noch 1 Ziffer |
| `web.devices.code_detect_remaining_many` | noch {n} Ziffern |
| `web.devices.code_detect_too_long` | zu lang |
| `web.devices.code_detect_invalid` | kein gültiger Code |
| `web.devices.code_where_hint` | Steht auf dem Gerät, seiner Verpackung oder im Handbuch. |
| `web.devices.sticker_alt` | *(aria-label der Skizze)* |

Zwei Schlüssel für „noch N Ziffern", weil `i18n.t` keine Pluralformen kennt
und `noch 1 Ziffern` im Deutschen wie im Englischen falsch wäre.

Die Beschriftungen der Beispielzeile („Zahlencode", „QR-Code") sind
dieselben Schlüssel wie die des Chips — es ist derselbe Begriff, und dass
er an beiden Stellen gleich lautet, ist der Zweck der Übung.

**Geändert**: `web.devices.code_placeholder` wird von „Pairing-Code
(11-stellig oder MT:…)" zu `1234-567-8901`. Der Klammerzusatz ist durch
Skizze und Beispielzeile ersetzt, und ein Platzhalter, der die Form zeigt,
sagt mehr als einer, der sie beschreibt.

## 10. Was noch mitgeht

- **Die Ablaufanzeige.** `commissionRunCode` zeigt den formatierten Code,
  nicht den normalisierten. Wer zwanzig bis sechzig Sekunden auf das
  Einlernen wartet, soll den Code wiedererkennen, den er eingetippt hat.
- **`docs/screenshots/commissioning.png`** neu aufnehmen. Die Bilder sind
  byte-genau reproduzierbar (siehe Kopfkommentar von
  `scripts/capture_screenshots.py`); ein Diff dort ist keine Unschärfe,
  sondern genau diese Änderung.
- **Die Selektoren in `capture_screenshots.py`** prüfen, falls sie am
  Platzhaltertext hängen.

## 11. Prüfen

**Backend, mit pytest**, in `tests/api/`:

- `1234-567-8901` kommt als `12345678901` bei `commission_with_code` an
- `MT:Y.K90SO527JA0648G00` kommt unverändert an
- Leerzeichen und umschließender Leerraum werden ebenso entfernt
- die bestehenden Einlern-Tests bleiben grün (sie schicken `MT:ABC123`,
  einen QR-Code, und den rührt der Validator nicht an)

**Frontend.** Es gibt kein JS-Testframework im Repo. Die Formatierlogik wird
deshalb in einem Wegwerf-Harness durchgespielt, statt behauptet:

- Tippen von `12345678901`, Zeichen für Zeichen — Gruppierung und
  Cursorstand nach jedem Anschlag
- Einfügen von `1234-567-8901` in ein leeres und in ein gefülltes Feld
- Rückschritt direkt hinter einem Bindestrich
- Einfügen einer Ziffer in die Mitte
- Umschaltpunkt zum QR-Inhalt: `M`, dann `MT`, dann `MT:`
- 22 Ziffern (nichts verschwindet, Chip meldet `zu lang`, Knopf bleibt
  bedienbar)

Was das Harness **nicht** belegt, ist, dass die Bindung in der Anwendung
ankommt — dafür ein Blick auf die laufende Oberfläche.
