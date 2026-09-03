# loxmatter

Bindet Matter-Geräte (Thread und WiFi) an einen Loxone Miniserver an.

Design: [`docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md`](docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md)

## Stand

Phasen 1 und 3 bis 6 sind gebaut: Matter-Adapter und Signal-Extraktion,
Vorlagen-Export, die Laufzeitstrecke zwischen Matter und Loxone, die
Bedienoberfläche samt Zugangsschutz und die Signalauswahl. Phase 2 wurde
übersprungen. Validiert gegen zwei reale IKEA-Geräte an einem laufenden
matter-server (Testumgebung: [`deploy/testhost/`](deploy/testhost/)); der
Dienst läuft dort als Container.

**Noch offen:** der Durchstich gegen einen echten Loxone Miniserver — die
erzeugten Vorlagen sind bisher nur gegen einen nachgebauten Miniserver
geprüft, nicht in Loxone Config importiert.

Aus der Validierung von Phase 1: für Attribute trägt die generische
Zerlegung uneingeschränkt — jeder Attributpfad war parsebar, kein gelistetes
Attribut fehlte. Für Events trug sie nicht: keins der beiden Geräte führt die
`EventList`, deshalb ist die Event-Erkennung FeatureMap-basiert und
Cluster-spezifisch (`discovery.FEATURE_MAP_EVENTS`). Details, Zahlen und die
Konsequenzen stehen im Validierungsabschnitt der Spec,
[Abschnitt 3.5](docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md#35-abbildung-generisch-statt-kuratiert).

## Entwickeln

```bash
uv sync
uv run pytest
```

Die Testsuite läuft ohne Hardware und ohne Netzwerkzugriff.

## Ein Gerät ansehen

```bash
uv run loxmatter inspect --fixture tests/fixtures/nodes/example_light.json
uv run loxmatter inspect --node 12          # gegen laufenden matter-server
```

Der erste Aufruf funktioniert heute ohne weitere Vorbereitung. Der zweite
braucht einen erreichbaren matter-server (Standardadresse
`ws://localhost:5580/ws`, per `--url` änderbar) — läuft und wurde gegen
echte Hardware erprobt, siehe [`deploy/testhost/`](deploy/testhost/) für die
Testumgebung.

## Dauerhaft betreiben: `loxmatter run`

```bash
uv run loxmatter run --miniserver 192.168.1.10
```

Verbindet dauerhaft mit matter-server und Miniserver und startet einen
HTTP-Dienst (Standardport 8080, `--listen`), der zwei Dinge gleichzeitig
ausliefert:

- `/cmd` und `/resync` für den Miniserver (virtuelle Ausgänge) — unverändert
  seit Phase 4.
- `/` und `/api/*` für eine Bedienoberfläche im Browser: Geräte einlernen,
  ansehen, benennen, schalten, Vorlagen exportieren, Diagnose.

**Was eine exportierte Vorlage standardmäßig enthält.** Ein Gerät liefert oft
weit mehr Signale, als jemand in Loxone haben will — eine Steckdose etwa über
hundert, meist Thread-Funkzähler, Seriennummern und andere Verwaltungswerte.
Der Export nimmt deshalb standardmäßig nur die **funktionalen** Signale
mit — die, die zum erkannten Gerätetyp gehören (bei einer Steckdose: Ein/Aus,
Spannung, Strom, Leistung, Verbrauch). Alles andere bleibt technisch
exportierbar, ist aber nicht angehakt. In der Signalliste der WebUI stehen
diese übrigen Signale im zugeklappten Block „Experte" (mit Anzahl in der
Überschrift) — jedes davon trägt seinen eigenen Exportieren-Haken und lässt
sich dort einzeln aktivieren, etwa ein Thread-Zähler zur Fehlersuche.
Begründung und Auswahlregel: [Signalauswahl-Entwurf](docs/superpowers/specs/2026-09-03-signalauswahl-design.md).

**Achtung beim Update auf diese Fassung, wenn schon Geräte eingelernt
sind.** Der Einmal-Umzug der Datenbank auf dieses Schema setzt den
Exportieren-Haken **jedes** bereits gespeicherten Signals auf den neuen
Vorgabewert zurück – auch wenn er zuvor von Hand umgelegt wurde. Wer vor
diesem Update z. B. Thread-Zähler gezielt freigeschaltet oder ein Signal
abgeschaltet hat, verliert diese Auswahl beim ersten Start danach, ohne
Warnung, und muss sie in der Signalliste erneut setzen. Eine bereits in
Loxone importierte Vorlage bleibt davon unberührt – die Laufzeitstrecke
sendet ohnehin unabhängig vom Haken; betroffen ist nur eine **neu**
erzeugte Vorlage nach dem Update.

Der Dienst bindet standardmäßig auf `0.0.0.0` (`--host`), damit der
Miniserver ihn erreicht — dieselbe Erreichbarkeit gilt fürs restliche
Netz. **Die `/api`-Routen sind deshalb mit `--api-token` bzw. der
Umgebungsvariable `LOXMATTER_API_TOKEN` absicherbar**
(`Authorization: Bearer <Token>`). `/cmd` und `/resync` bleiben davon *immer*
unberührt — der Miniserver kann keinen Header mitschicken, das ist eine
bewusste Grenze: wer den Port erreicht, kann ein Gerät weiterhin schalten,
aber nicht mehr einlernen, entfernen oder die Fabric-Sicherung
herunterladen. „Wer den Port erreicht" ist dabei weiter zu verstehen, als es
klingt: `/cmd/{key}/{value}` ist ein GET ohne Ursprungsprüfung, den auch eine
beliebige Webseite auslösen kann, die jemand aus diesem Netz im Browser
öffnet (`<img src="http://…/cmd/…">`) — ein Fuß im LAN ist dafür nicht
nötig. Ohne gesetztes Token startet der Dienst trotzdem — mit einer
deutlichen Warnung im Log. Details und Begründung: [Spec, Abschnitt
9](docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md#9-fehlerbehandlung).

**So bedienen Sie das Token.** Die mitgelieferte Browser-Oberfläche kann es
mitschicken: oben rechts steht ein Feld dafür (Typ `password`, damit es
nicht über der Schulter mitlesbar ist). Eingetragen wird es im
`localStorage` des Browsers gehalten und bei jedem Aufruf als
`Authorization: Bearer <Token>` an denselben Ursprung geschickt, von dem die
Seite geladen wurde — nie in einer URL, nie als Query-Parameter (der stünde
in Server-Logs, Proxy-Logs und der Browser-History). Angezeigt wird es nach
dem Speichern nicht mehr, nur noch „Token gesetzt" mit einem Knopf zum
Ersetzen und einem zum Löschen. Antwortet ein Aufruf mit 401, sagt die
Oberfläche das im Klartext und klappt das Feld auf, statt einen rohen
Fehlertext zu zeigen.

Die Live-Werte-Route `/api/live` ist ein Sonderfall: ein Browser-`WebSocket`
kann keine eigenen Kopfzeilen setzen. Die Oberfläche schickt das Token dort
deshalb als Subprotokoll (`new WebSocket(url, ["bearer", token])`), was der
Browser als `Sec-WebSocket-Protocol: bearer, <Token>` überträgt; der
`Authorization`-Header bleibt der Hauptweg für alles andere. Daraus folgt
eine Anforderung an das Token selbst: **es muss in einem HTTP-Header und in
einem Subprotokoll übertragbar sein — keine Leerzeichen, kein Komma, kein
Nicht-ASCII.** `openssl rand -hex 32` liefert nur `[0-9a-f]` und ist der
empfohlene Weg zu einem Token. Ein Token, das nur aus Leerraum besteht (ein
versehentlicher Zeilenumbruch in einer `.env`), gilt als „nicht gesetzt".

**Ohne Token wird die Fabric-Sicherung nicht ausgeliefert.** `GET
/api/diagnostics/fabric-backup` antwortet dann mit 403 statt mit den
Fabric-Zugangsdaten — sie sind der einzige unersetzliche Zustand der
Installation, und wer sie herunterlädt, kann die Matter-Fabric übernehmen.
Alle übrigen `/api`-Routen bleiben ohne Token unverändert offen. Das ist
kein Ersatz für ein Token, sondern die Absicherung des einen Falls, dessen
Schaden nicht rückgängig zu machen ist.

Ein lauffähiges Beispiel steht in
[`deploy/testhost/docker-compose.yml`](deploy/testhost/docker-compose.yml);
`deploy/testhost/README.md` führt `LOXMATTER_API_TOKEN` unter den Variablen
auf, die beim Einrichten gesetzt werden.

## Lizenz

**GNU General Public License, Version 3 oder später** — der vollständige Text
steht in [`LICENSE`](LICENSE).

Das heißt in der Praxis: du darfst dieses Werkzeug benutzen, verändern und
weitergeben. Wer es in veränderter Form weitergibt, muss seine Änderungen
unter derselben Lizenz offenlegen. Ein geschlossenes Produkt darf daraus
nicht werden — das ist der Zweck dieser Wahl.

Die Angabe lautet `GPL-3.0-or-later`, nicht `-only`: eine spätere Fassung der
GPL darf ebenfalls verwendet werden. Das ist die von der Free Software
Foundation empfohlene Form.

### Fremdsoftware

Alle Abhängigkeiten sind permissiv lizenziert und mit der GPL-3.0 vereinbar:

| | |
|---|---|
| `python-matter-server`, chip-SDK | Apache-2.0 |
| FastAPI, Pydantic, Typer, PyYAML | MIT |
| Starlette, uvicorn, websockets | BSD-3-Clause |
| Alpine.js (mitgeliefert) | MIT |

Alpine.js liegt als unveränderte Kopie unter
[`src/loxmatter/web/vendor/`](src/loxmatter/web/vendor/) — mit seinem eigenen
Lizenztext daneben, wie die MIT-Lizenz es verlangt. Die GPL dieses Projekts
erstreckt sich nicht auf Alpine.js selbst.

Apache-2.0 ist einseitig mit der GPL-3.0 vereinbar: Apache-lizenzierter Code
darf in ein GPL-3.0-Werk aufgenommen werden, der umgekehrte Weg nicht.

### Hinweise in den Quelldateien

Jede Quelldatei trägt den GPL-Hinweis im Kopf, wie ihn der Abschnitt „How to
Apply These Terms" der GPL vorsieht — außer `src/loxmatter/web/vendor/`, das
unter MIT steht und seinen eigenen Hinweis behält.

Der Hinweis steht in der englischen Fassung der Free Software Foundation,
obwohl dieses Projekt sonst deutsche Prosa verwendet. Das ist Absicht: er ist
ein rechtlicher Verweis auf die `LICENSE`, und eine eigene Übersetzung wäre
eine Auslegung, über die man streiten kann.
