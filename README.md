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
Netz. **Die `/api`-Routen verlangen deshalb eine Anmeldung.** Beim ersten
Öffnen von `http://<Host>:8080/` zeigt die Oberfläche eine Ersteinrichtung:
ein Passwort vergeben, fertig. Danach meldet man sich mit diesem Passwort
an, die Oberfläche hält die Anmeldung über ein Sitzungs-Cookie
(`loxmatter_session`, 30 Tage gültig, gleitend verlängert). **Bis das
Passwort vergeben ist, liefert keine `/api`-Route irgendetwas aus** — jede
Anfrage endet mit 401, und die Oberfläche zeigt nichts außer dem
Einrichtungsbildschirm. Das ist ein bewusster Bruch mit dem früheren
Verhalten (ein Dienst ohne Token lief bis dahin offen weiter, nur mit einer
Log-Warnung): der offene Zustand gibt es nicht mehr. Die Ersteinrichtung
verlangt dabei keinen weiteren Nachweis — wer zuerst kommt, vergibt das
Passwort. Das ist eine bewusste Abwägung (Trust on first use), damit sich
der Dienst ohne Shell-Zugriff auf dem Host einrichten lässt; der Preis ist
ein Zeitfenster zwischen dem Start des Dienstes und der ersten Anmeldung, in
dem jeder im Netz die Brücke übernehmen kann — es sollte deshalb Minuten
dauern, nicht Tage. Ein vergessenes Passwort setzt `uv run
loxmatter set-password` auf dem Host neu; das meldet dabei alle offenen
Sitzungen ab. Details und Begründung:
[Ergänzungs-Spec](docs/superpowers/specs/2026-09-03-webui-login-design.md).

`/cmd` und `/resync` bleiben davon *immer* unberührt — der Miniserver kann
keinen Header und kein Cookie mitschicken, das ist eine bewusste Grenze: wer
den Port erreicht, kann ein Gerät weiterhin schalten, aber nicht mehr
einlernen, entfernen oder die Fabric-Sicherung herunterladen. „Wer den Port
erreicht" ist dabei weiter zu verstehen, als es klingt: `/cmd/{key}/{value}`
ist ein GET ohne Ursprungsprüfung, den auch eine beliebige Webseite auslösen
kann, die jemand aus diesem Netz im Browser öffnet
(`<img src="http://…/cmd/…">`) — ein Fuß im LAN ist dafür nicht nötig.

**`LOXMATTER_API_TOKEN` gibt es weiterhin — aber nur noch für Skripte und
`curl`, nicht mehr für den Browser.** Gesetzt per `--api-token` bzw. der
Umgebungsvariable, akzeptiert `build_api_guard` es weiterhin als
`Authorization: Bearer <Token>` und, für den WebSocket-Handshake von
`/api/live`, als Subprotokoll (`Sec-WebSocket-Protocol: bearer, <Token>`) —
daraus folgt dieselbe Anforderung an das Token wie bisher: **keine
Leerzeichen, kein Komma, kein Nicht-ASCII**, `openssl rand -hex 32` liefert
nur `[0-9a-f]` und ist der empfohlene Weg dazu. Ein Token, das nur aus
Leerraum besteht (ein versehentlicher Zeilenumbruch in einer `.env`), gilt
als „nicht gesetzt". Die Browser-Oberfläche selbst setzt keinen
`Authorization`-Header mehr und legt kein Geheimnis mehr im `localStorage`
ab — das Sitzungs-Cookie übernimmt diese Rolle. Bestehende Automatisierungen
gegen `LOXMATTER_API_TOKEN` brechen durch dieses Update nicht ab, auch nicht
vor der Passwortvergabe: der Token-Pfad im Wächter existiert unabhängig vom
Passwort-Status.

**Kein TLS.** Der Dienst spricht weiterhin HTTP ohne Verschlüsselung; sowohl
das Token als auch das Passwort gehen bei jeder Übertragung im Klartext über
das Netz. Ein Passwort verwenden, das nirgendwo sonst benutzt wird.

Die Fabric-Sicherung (`GET /api/diagnostics/fabric-backup`) ist heute keine
Ausnahme mehr — sie war es früher: ohne konfiguriertes Token antwortete
diese eine Route mit 403, während alle übrigen `/api`-Routen ohne Token
offen blieben. Diesen Sonderfall gibt es nicht mehr, weil die Regel, von der
er eine Ausnahme war, selbst entfallen ist: **alle** `/api`-Routen — die
Fabric-Sicherung eingeschlossen — verlangen jetzt gleichermaßen eine gültige
Sitzung oder ein gültiges Token, sonst 401.

Ein lauffähiges Beispiel steht in
[`deploy/testhost/docker-compose.yml`](deploy/testhost/docker-compose.yml);
`deploy/testhost/README.md` führt `LOXMATTER_API_TOKEN` unter den Variablen
auf, die beim Einrichten optional gesetzt werden können.
