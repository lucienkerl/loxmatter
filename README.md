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
