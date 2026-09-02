# loxmatter

Bindet Matter-Geräte (Thread und WiFi) an einen Loxone Miniserver an.

Design: [`docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md`](docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md)

## Stand

Phase 1 von 6 (Matter-Adapter und Signal-Extraktion) läuft. Der Matter-Adapter
und das CLI-Kommando `loxmatter inspect` sind gebaut, getestet und gegen 2
reale IKEA-Geräte an einem laufenden matter-server validiert (Testumgebung:
[`deploy/testhost/`](deploy/testhost/)). Ergebnis: Für Attribute trägt die
generische Zerlegung uneingeschränkt — jeder Attributpfad war parsebar, kein
gelistetes Attribut fehlte. Für Events trug sie nicht: keins der beiden
Geräte führt die `EventList`, deshalb ist die Event-Erkennung jetzt
FeatureMap-basiert und Cluster-spezifisch (`discovery.FEATURE_MAP_EVENTS`).
Details, Zahlen und die Konsequenzen daraus stehen im Validierungsabschnitt
der Spec, [Abschnitt 3.5](docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md#35-abbildung-generisch-statt-kuratiert).

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

Der Dienst bindet standardmäßig auf `0.0.0.0` (`--host`), damit der
Miniserver ihn erreicht — dieselbe Erreichbarkeit gilt fürs restliche
Netz. **Die `/api`-Routen sind deshalb mit `--api-token` bzw. der
Umgebungsvariable `LOXMATTER_API_TOKEN` absicherbar**
(`Authorization: Bearer <Token>`). `/cmd` und `/resync` bleiben davon *immer*
unberührt — der Miniserver kann keinen Header mitschicken, das ist eine
bewusste Grenze: wer den Port erreicht, kann ein Gerät weiterhin schalten,
aber nicht mehr einlernen, entfernen oder die Fabric-Sicherung
herunterladen. Ohne gesetztes Token startet der Dienst trotzdem — mit einer
deutlichen Warnung im Log. Details und Begründung: [Spec, Abschnitt
9](docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md#9-fehlerbehandlung).

**Achtung, bevor Sie ein Token setzen:** die mitgelieferte Browser-Oberfläche
selbst (`app.js`) schickt heute keinen `Authorization`-Header mit — es gibt
weder ein Eingabefeld noch eine Speicherung dafür. Ein gesetztes Token
schützt den `/api`-Zugriff aus dem Netz, sperrt dabei aber auch die eigene
Oberfläche aus: jeder Klick, der `/api/*` aufruft, bekommt dieselbe
401-Antwort wie ein Angreifer ohne Header. Die Live-Werte-Route
(`/api/live`) lässt sich damit über einen Browser ohnehin nicht absichern,
weil dessen `WebSocket`-API gar keine eigenen Header unterstützt. Details
und der offene Punkt dazu: [Spec, Abschnitt
12](docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md#12-offene-punkte),
Punkt 8. Setzen Sie das Token also nur, wenn Ihnen die Fabric-Sicherung
wichtiger ist als der Browser-Zugriff auf die Oberfläche — beides
gleichzeitig geht heute nicht.

Ein lauffähiges Beispiel steht in
[`deploy/testhost/docker-compose.yml`](deploy/testhost/docker-compose.yml):
`LOXMATTER_API_TOKEN` in `.env` bleibt dort standardmäßig leer (Oberfläche
bleibt nutzbar), lässt sich aber bei Bedarf setzen.
