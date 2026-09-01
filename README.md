# loxmatter

Bindet Matter-Geräte (Thread und WiFi) an einen Loxone Miniserver an.

Design: [`docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md`](docs/superpowers/specs/2026-09-01-matter-loxone-bridge-design.md)

## Stand

Phase 1 von 6 (Matter-Adapter und Signal-Extraktion) läuft. Der Matter-Adapter
und das CLI-Kommando `loxmatter inspect` sind gebaut und getestet, aber noch
nicht gegen echte Geräte validiert — dafür fehlt noch die Testumgebung
(matter-server, OTBR) und die Aufnahme echter Signale von IKEA-Geräten.

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
`ws://localhost:5580/ws`, per `--url` änderbar) und ist noch nicht gegen
echte Hardware erprobt.
