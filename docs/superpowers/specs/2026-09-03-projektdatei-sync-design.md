# Projektdatei-Sync: virtuelle Ein-/Ausgänge automatisch anlegen und aktualisieren

Entwurf, 3. September 2026. Ergänzt
[das Hauptdokument](2026-09-01-matter-loxone-bridge-design.md), insbesondere
dessen Abschnitt 3.2 (Loxone-Import) und Abschnitt 6.1 der dort referenzierten
Vorlagen-Spec (Attributschema der Vorlagendateien, umgesetzt in
`export/documents.py`).

## 1. Das Problem

Heute exportiert `loxmatter` pro Gerät zwei Vorlagendateien
(`VirtualInUdp`/`VirtualOut`, siehe `export/documents.py`), die ein Anwender
in Loxone Config manuell importiert — pro Gerät, jedes Mal neu. Ändert sich
das Signal-Set eines Geräts (neue Firmware, ein neu freigeschaltetes Signal in
der Signalauswahl), muss der Anwender das erneut von Hand nachziehen und dabei
selbst herausfinden, was sich geändert hat, ohne bestehende Verdrahtung auf
Funktionsbausteine zu zerstören.

Ziel dieses Entwurfs: `loxmatter` nimmt eine echte Loxone-Config-Projektdatei
entgegen, gleicht sie gegen die gespeicherten Geräte/Signale ab und liefert
eine gepatchte Fassung zurück, in der bestehende virtuelle Ein-/Ausgänge
aktualisiert (nicht ersetzt) und fehlende neu angelegt sind — ohne dass der
Anwender einzelne Vorlagen mehr von Hand zusammensuchen muss.

## 2. Nicht-Ziele

- **Keine Live-Verbindung zum Miniserver für dieses Feature.** Der
  Miniserver liefert über seine Laufzeit-API (`/dev/sps/io/...`,
  `LoxAPP3.json`) nur eine vereinfachte, rein lesende Sicht auf bestehende
  IOs — nicht die volle Projektstruktur mit `ControlList`, `NextObj` usw. Die
  Projektdatei kommt ausschließlich aus Loxone Config selbst (Export oder
  „Programm vom Miniserver laden"), der Anwender lädt sie im WebUI hoch.
- **Kein automatisches Verdrahten auf Funktionsbausteine.** Bleibt wie in
  Abschnitt 3.2 des Hauptdokuments festgehalten Handarbeit — der Gewinn wäre
  gering, das Risiko hoch.
- **Kein automatisches Löschen.** Verwaiste Objekte (Signal nicht mehr im
  Export, Gerät entfernt) werden gemeldet, nicht angefasst. Löschen ist
  riskanter als Anlegen und war nicht verlangt.
- **Kein Nachbau des proprietären Miniserver-Upload-Protokolls.** Der
  Anwender öffnet die gepatchte Datei weiterhin selbst in Loxone Config und
  speichert von dort zum Miniserver — das ist der Teil, den nur Config selbst
  gefahrlos kann (Abschnitt 3.2 des Hauptdokuments).

## 3. Entscheidungen

### 3.1 Eingabeweg: Datei-Upload im WebUI, keine Live-Verbindung

Ursprünglich stand „automatisch mit dem Miniserver verbinden" im Raum. Die
Miniserver-API kann aber keine IOs anlegen und liefert auch nicht die volle
Projektstruktur — nur Loxone Config selbst hat beides. Ein Reverse-Engineering
von Configs proprietärem Projekt-Protokoll wurde im Hauptdokument bereits als
„für ein Tool, das in fremden Häusern läuft, disqualifizierend" verworfen und
bleibt es hier.

Gewählt: der Anwender lädt die `.Loxone`-Projektdatei im WebUI hoch, das Tool
liefert eine gepatchte Fassung zum Download zurück. Ein manueller Schritt
bleibt — Datei aus Config exportieren, gepatchte Fassung wieder öffnen und
zum Miniserver speichern —, aber der Aufwand *pro Gerät und Signal* entfällt,
und das ist der eigentliche Schmerzpunkt aus Abschnitt 1.

### 3.2 Schreiben: Text-Chirurgie statt XML-Neuaufbau

`export/xml.py` baut Vorlagendateien bewusst ohne XML-Bibliothek, weil ein
Serialisierer Attribute umsortieren oder die Deklaration anders schreiben
könnte, ohne dass sich das hier nachprüfen ließe. Für die Projektdatei gilt
dasselbe Argument mit größerem Gewicht: 3 MB, überwiegend Bausteintypen, die
dieses Projekt nicht kennt und nicht anfassen soll.

Deshalb: **Lesen** über einen Standard-XML-Parser (die reale Referenzdatei
parst klaglos mit `xml.etree.ElementTree` — die im Hauptdokument befürchteten
doppelten Attribute traten an keiner Stelle auf, betreffen also, falls sie
irgendwo existieren, andere Bausteintypen als die hier relevanten). **Schreiben**
ausschließlich als gezielte Textersetzung an exakt den Byte-Bereichen, die
sich ändern: ein geändertes Attribut wird innerhalb seines bestehenden
`<C .../>`-Tags ersetzt, ein neues Objekt wird als fertig gerenderter
XML-Text unmittelbar vor dem schließenden Tag seines Containers eingefügt.
Alles andere im Dokument bleibt byte-identisch — das ist die Eigenschaft, die
ein Round-Trip durch einen generischen Serialisierer nicht garantieren
könnte.

### 3.3 Abgleich über den vorhandenen Signal-Schlüssel

Jedes Signal trägt in der Projektdatei bereits den Schlüssel, den `loxmatter`
selbst vergibt: im `Check`-Attribut bei Eingängen (`Check="d3_1_onoff:\v"`,
vgl. `render_virtual_in_udp`) und im `CmdOn`-Pfad bei Ausgängen
(`CmdOn="/cmd/d3_1_onoff/1"`, vgl. `_command_path` in `export/outputs.py`).
Diese Schlüssel sind laut `model.store` bereits global eindeutig über alle
Geräte.

Der Abgleich sucht deshalb im gesamten Dokument nach `VirtualUdpInCmd`- bzw.
`VirtualOutCmd`-Elementen, deren `Check`/`CmdOn` mit einem bekannten Schlüssel
beginnt — unabhängig vom Titel (den der Anwender in Config umbenannt haben
kann) und unabhängig davon, unter welchem Container das Signal tatsächlich
hängt. Ein Abgleich über den Gerätecontainer (Titel) wäre fragiler und ist
nicht nötig.

### 3.4 Risikostufen: Update ist der Vorgabefall, Neuanlage ist opt-in

Die reale Referenzdatei parst zwar sauber, aber das `U`-ID-Schema für neue
Objekte ist proprietär und unverifiziert (Abschnitt 6). Ein Update eines
bestehenden Objekts ändert nur bekannte Attributwerte in einer bereits von
Config akzeptierten Struktur — risikoarm. Eine Neuanlage (neuer Container
oder neues Cmd-Objekt mit selbst erzeugter ID) hat dagegen ein echtes,
unbestätigtes Risiko: lehnt Config die Datei beim Öffnen ab oder verwirft sie
still Teile davon, hat der Anwender im schlimmsten Fall ein beschädigtes
Projekt.

Deshalb: der Diff-Plan zeigt **immer beides** (informativ), aber die zum
Download angebotene Datei enthält neu angelegte **Geräte-Container** nur,
wenn der Anwender das im WebUI explizit anhakt („Neue Geräte-Container
ebenfalls anlegen — experimentell, noch nicht gegen Loxone Config
validiert"). Ohne den Haken enthält die Datei Updates an bestehenden Objekten
plus neue Signale *innerhalb* bereits vorhandener Geräte-Container — beides
risikoärmer, weil kein neuer Container entsteht, sondern nur neue Blätter in
einer Struktur, die Config bereits akzeptiert hat.

Das ist kein globaler Schalter und keine Konfigdatei — sobald der Anwender
einmal erfolgreich eine Datei mit frisch angelegtem Container importiert hat,
ist der Haken für ihn einfach Alltag.

### 3.5 Datei-Struktur & Miniserver-Zuordnung (korrigiert nach echtem Praxistest)

**Die ursprüngliche Annahme in diesem Abschnitt (und in 3.3) war falsch.**
Beim ersten Test an einer gewachsenen, echten Projektdatei (nicht nur an der
kleinen Referenzdatei aus der Brainstorming-Phase) zeigte sich: `VirtualIn
Caption`/`VirtualOutCaption` liegen **nicht** direkt unter `<ControlList>`.
Der reale Aufbau ist:

```
<ControlList>
  <C Type="Document">                    -- genau EIN Kind von ControlList
    <C Type="LoxLIVE" IntAddr="…">       -- ein Block PRO konfiguriertem Miniserver
      <C Type="VirtualInCaption"> … </C>
      <C Type="VirtualOutCaption"> … </C>
    </C>
    <C Type="LoxLIVE" IntAddr="…"> … </C>  -- optional: weitere Miniserver
  </C>
</ControlList>
```

Der ursprüngliche, flache Suchalgorithmus parste solche Dateien fehlerfrei,
fand aber **keinen einzigen** vorhandenen virtuellen Ein-/Ausgang — jedes
bereits bestehende Gerät erschien dadurch fälschlich als `new_device`, statt
als `unchanged`/`updated`. Genau dieser Fehler wurde vom Anwender an seiner
echten Datei gemeldet und war der Auslöser für diese Korrektur.

**Konsequenz: Miniserver-Zuordnung ist ein eigener Schritt, VOR dem
eigentlichen Abgleich.** Eine Projektdatei kann mehrere `LoxLIVE`-Blöcke
enthalten (mehrere in Loxone Config konfigurierte Miniserver in einem
Projekt) — der Abgleich darf nur innerhalb EINES davon suchen, sonst könnte
ein Signal fälschlich im falschen Miniserver-Bereich landen. Auflösung
(`index._resolve_target_loxlive`):

- Kein `LoxLIVE`-Block gefunden → Fehler (kein Ort für virtuelle Ein-/Ausgänge).
- Genau ein Block → wird automatisch gewählt, unabhängig davon, ob eine IP
  mitgegeben wurde.
- Eine IP wurde mitgegeben → muss exakt einem `LoxLIVE.IntAddr` entsprechen
  (derselbe Wert wie bei `loxmatter run --miniserver <IP>`), sonst Fehler mit
  Auflistung der gefundenen Miniserver — auch wenn es nur einen Block gibt:
  eine nicht passende, aber explizit angegebene IP deutet eher auf die
  falsche Datei hin als auf einen Grund, sie zu ignorieren.
- Mehrere Blöcke, keine IP mitgegeben → Fehler, IP ist Pflicht.

Der Abgleich aus Abschnitt 3.3 ("sucht im gesamten Dokument") gilt seitdem
präzisiert als "sucht innerhalb des aufgelösten `LoxLIVE`-Blocks" — nicht
mehr über das gesamte `<ControlList>`. Neu angelegte Captions (Abschnitt 6,
Neuanlage-Pfad) hängen entsprechend am Ende dieses `LoxLIVE`-Blocks, nicht
am Ende von `<ControlList>`.

## 4. Architektur & Datenfluss

```
Nutzer lädt .Loxone-Datei im WebUI hoch
        │
        ▼
Parsen (nur lesend, ElementTree) + Abgleich gegen gespeicherte Geräte/Signale
        │
        ▼
Diff-Plan: neu (Signal) / neu (Gerät) / aktualisiert / unverändert / verwaist
        │
        ▼
WebUI zeigt Plan zur Bestätigung — nichts wurde bisher geschrieben
        │
        ▼
Nutzer setzt ggf. den Experimentell-Haken, bestätigt
        │
        ▼
Gepatchte Datei (Text-Chirurgie auf dem Original-Byte-Strom) zum Download
```

Ein Server-Request genügt: `POST /api/export/project-sync` liefert Diff-Plan
und beide Datei-Varianten (mit/ohne neue Geräte-Container) in einer Antwort.
Der „Bestätigen"-Schritt ist rein clientseitig — kein zweiter Roundtrip, kein
Server-seitiger Zwischenzustand.

Die gepatchte Datei ist immer eine **neue** Datei; das hochgeladene Original
wird nirgends überschrieben. Ein fehlgeschlagener Patch-Versuch ist damit
folgenlos — der Anwender lädt einfach erneut hoch.

## 5. Diff-Plan: Datenmodell und Fälle

Pro bekanntem Signal (aus `model.store`, gefiltert auf `exported`) einer von
vier Zuständen:

| Zustand | Bedingung | Wirkung in der gepatchten Datei |
|---|---|---|
| `unchanged` | passendes Objekt gefunden, alle relevanten Attribute stimmen überein | keine |
| `updated` | passendes Objekt gefunden, mindestens ein Attribut weicht ab | nur die abweichenden Attribute werden im bestehenden Tag ersetzt; `U` und alle `Co`/`In`-Kinder (Verdrahtung) bleiben unangetastet |
| `new_signal` | kein passendes Objekt, aber der Gerätecontainer existiert bereits | neues `VirtualUdpInCmd`/`VirtualOutCmd` wird ans Ende des bestehenden Containers gehängt |
| `new_device` | kein passendes Objekt, und für dieses Gerät existiert noch gar kein Container | neuer `VirtualUdpIn`/`VirtualOut`-Container wird unter `VirtualInCaption`/`VirtualOutCaption` angelegt, mit dem ersten Cmd-Kind |

Zusätzlich, unabhängig von obiger Tabelle: **`orphaned`** — ein
`VirtualUdpInCmd`/`VirtualOutCmd` in der Datei trägt einen Schlüssel, der
keinem aktuell bekannten, exportierten Signal mehr entspricht (Gerät entfernt,
Signal abgewählt). Wird gemeldet, nicht verändert (Abschnitt 2).

**`possible_duplicate`** (Nachtrag nach echtem Praxistest, 2026-09-05): kein
Objekt mit dem gewünschten Schlüssel gefunden, ABER ein bestehender Befehl im
selben Gerätecontainer trägt bereits genau den gewünschten Titel. Deutet eher
auf ein beschädigtes/veraltetes `Check`/`CmdOn` an einem einzelnen bestehenden
Objekt hin als auf ein wirklich neues Signal — beobachtet an einem
kombinierten Ausgangsbefehl "onoff", dessen `CmdOn` durch einen alten
Export-Bug ein Zeichen fehlte (`/cmd/d1_1_o/1` statt `/cmd/d1_1_onoff/1`).
Ohne diese Prüfung hätte der Sync einen zweiten "onoff"-Befehl im selben
Container angelegt, statt den beschädigten zu erkennen. Wird wie `orphaned`/
`conflict` nie automatisch angelegt — der Anwender muss den bestehenden
Befehl selbst in Loxone Config prüfen/reparieren.

`updated` trägt zusätzlich die konkrete Attribut-Differenz (alter Wert → neuer
Wert je Attribut), damit der Anwender im Plan sieht, *was* sich ändert, nicht
nur *dass*.

Sind alle bekannten Signale `unchanged` und keine `orphaned` vorhanden, sagt
der Plan das explizit („Alles aktuell, keine Änderungen nötig") statt eine
leere Liste zu zeigen.

## 6. ID-Vergabe für neue Objekte

Jedes neue Objekt (Container wie Cmd) braucht eine neue, in der Datei
eindeutige `U`-ID. Das Format ist proprietär (`<hex>-<hex>-<hex>-<hex-Suffix>`)
und nicht dokumentiert. Erzeugung: Zeitstempel-basiertes Präfix, kombiniert
mit dem Installations-Suffix (die letzten 16 Hex-Stellen), der von einem
beliebigen bestehenden Objekt *aus derselben hochgeladenen Datei* übernommen
wird, damit neue IDs zur selben Projekt-Familie gehören. Eindeutigkeit wird
gegen alle in der Datei gefundenen `U`-Werte geprüft, nicht nur gegen die neu
erzeugten.

Der Root-Knoten `ControlList` trägt Zähler (`NextObj`, `NextConst`, ...), die
Config beim eigenen Anlegen von Objekten hochzählt. Ihre genaue Bedeutung ist
nicht verifiziert; um auf der sicheren Seite zu sein, wird `NextObj`
konservativ über den höchsten in der Datei vorkommenden numerischen Anteil
angehoben, falls neue Objekte angelegt wurden.

**Das ist der unverifizierte Teil dieses Entwurfs.** Ob Config eine so
erzeugte Datei klaglos öffnet, weiß niemand, bevor es nicht ein einziges Mal
an einer echten Installation getestet wurde. Daher Abschnitt 3.4: Neuanlage
von Geräte-Containern ist bis zu einem erfolgreichen Test-Import
opt-in, nicht Vorgabe. Neuanlage von Cmd-Objekten *innerhalb* eines
bestehenden Containers trägt dasselbe ID-Risiko, aber ein deutlich kleineres
strukturelles Risiko (kein neuer Container, keine neue Elternstruktur) und
bleibt deshalb Vorgabe.

**Fehlendes `V`-Attribut (gefunden am echten Praxistest, 2026-09-05).** Der
erste reale Test zeigte: neu angelegte Geräte-Container erschienen zwar in
Loxone Config, ihre Kommando-Kinder blieben aber leer. Ursache: JEDES
`<C>`-Objekt in der echten Referenzdatei trägt ein `V`-Attribut (an allen
3710 vorkommenden Objekten geprüft, ausnahmslos — praktisch immer `"178"`,
nur das `Document`-Wurzelobjekt trägt die volle Config-Versionsnummer). Die
ursprüngliche Attributliste für neu angelegte Container/Cmds/Captions hatte
dieses Attribut schlicht nicht auf dem Schirm. Behoben: alle fünf
`new_*_open_tag`-Funktionen (`projectsync/schema.py`) schreiben jetzt
`V="178"`. Dieselbe Prüfung deckte auch auf, dass eine neu angelegte Caption
(`VirtualInCaption`/`VirtualOutCaption`, Abschnitt 8: Sonderfall der
kompletten Neuanlage) fälschlich ein `IName` trug, das echte Captions nicht
haben, und ein festes `Title` (`"Virtuelle Eingänge"`/`"Virtuelle
Ausgänge"`) fehlte — ebenfalls korrigiert.

## 7. API & WebUI

**Endpoint:** `POST /api/export/project-sync`, multipart mit der
hochgeladenen `.Loxone`-Datei, plus Query-Parameter `bridge_ip`/`port`/
`listen` (wie bei `/api/export/download`) und optional `miniserver_ip`
(Abschnitt 3.5) — nur nötig, wenn die Datei mehr als einen Miniserver
konfiguriert. Antwort: strukturierter Diff-Plan (Abschnitt 5) plus zwei
Datei-Varianten (`patched_conservative`, `patched_with_new_devices`).

**WebUI:** primärer Punkt im Export-Bereich (auf Nutzerwunsch, ursprünglich
unter „System" geplant). Optionales Feld „IP des Miniservers", darunter
Datei-Upload, danach der Plan als Liste — nach Geräten gruppiert (Eingänge/
Ausgänge je Gerät, analog zur späteren Loxone-Struktur), je Signal eine
Zeile mit Status-Badge (unverändert/aktualisiert/neu/verwaist) und bei
`updated` die Attribut-Differenz. Der Experimentell-Haken schaltet, welche
der beiden mitgelieferten Datei-Varianten der Download-Button anbietet. Der
Download-Button ist erst nach dem Hochladen (= der Plan wurde gesehen) aktiv.

## 8. Fehlerbehandlung

- Datei ist keine gültige Loxone-Projektdatei (kein `ControlList`-Root o. ä.)
  → klare Fehlermeldung, kein Absturz, kein Datei-Angebot.
- `VirtualInCaption`/`VirtualOutCaption` fehlt komplett (Projekt hatte noch
  nie einen virtuellen Ein-/Ausgang) → wird als Sonderfall der Neuanlage
  behandelt, ebenfalls hinter dem Experimentell-Haken.
- Ein gefundenes Objekt mit passendem Schlüssel sieht strukturell unerwartet
  aus (z. B. falscher Objekttyp für den Schlüssel) → als `conflict` markiert,
  wird übersprungen und explizit gemeldet statt still überschrieben oder
  übernommen.
- Kein Objekt mit passendem Schlüssel gefunden, aber ein bestehender Befehl im
  selben Container trägt bereits denselben Titel (Abschnitt 5, `possible_
  duplicate`) → wird übersprungen statt eine stille Dopplung anzulegen.
- Keine Änderungen nötig → Plan sagt das explizit (Abschnitt 5), kein leerer
  oder verwirrender Zustand.

## 9. Tests

Eine synthetische, klein gehaltene Fixture-Projektdatei, handgebaut nach dem
in Abschnitt 3–6 beobachteten Schema — analog zu den bestehenden
`tests/fixtures/loxone/*.xml`. **Nicht** die reale, vom Anwender gelieferte
Datei — die bleibt wegen personenbezogener Daten (Adressen, Gerätetitel)
außerhalb des Repos.

Abgedeckt werden soll mindestens:

- Update eines bestehenden `VirtualUdpInCmd`/`VirtualOutCmd`: nur die
  abweichenden Attribute ändern sich, `U` und `Co`/`In`-Kinder bleiben exakt
  erhalten.
- Neuanlage eines Cmd in einem bestehenden Container.
- Neuanlage eines kompletten Containers (nur wenn der Experimentell-Pfad
  getestet wird).
- Verwaistes Signal wird gemeldet, nicht verändert.
- Byte-Identität aller Dateiteile, die nicht zum Plan gehören (Diff der Datei
  vor/nach Patch, abzüglich der geplanten Änderungsstellen, muss leer sein).
- ID-Eindeutigkeit neu erzeugter `U`-Werte gegen alle vorhandenen.
- „Keine Änderungen nötig"-Fall liefert die explizite Meldung, keine leere
  Liste.

## 10. Offene Risiken

- **ID-Schema unverifiziert** (Abschnitt 6) — der zentrale Rest-Risiko-Punkt
  dieses Entwurfs. Mitigiert durch den Experimentell-Haken (Abschnitt 3.4),
  nicht gelöst.
- **`NextObj`/`NextConst`-Semantik unverifiziert** — konservative Anhebung
  (Abschnitt 6) ist eine Annahme, kein belegtes Verhalten.
- **Format-Abweichungen zwischen Config-Versionen** möglich — bislang an zwei
  echten Dateien geprüft (der ursprünglichen Referenzdatei und der Datei, an
  der die Struktur-Korrektur aus Abschnitt 3.5 gefunden wurde). Andere
  Versionen könnten trotzdem abweichen.
- **Erledigt (2026-09-04):** die in einer früheren Fassung dieses Abschnitts
  offene Frage nach der tatsächlichen Verschachtelung war der eigentliche
  erste reale Fehler — behoben und dokumentiert in Abschnitt 3.5.

Der erste reale Test bleibt: eine automatisch gepatchte Datei in Loxone
Config öffnen und auf Fehler prüfen, bevor ihr vertraut wird — insbesondere
für den Experimentell-Pfad (neue Geräte-Container). Das ID-Schema selbst
(Abschnitt 6) ist bislang nur gegen die in der Datei vorgefundenen `U`-Werte
geprüft, nicht durch einen erfolgreichen Import in Loxone Config bestätigt.
