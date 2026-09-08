# Umstieg auf matterjs-server — Umsetzungsplan

> **Für agentische Bearbeiter:** ERFORDERLICHE SUB-SKILL: `superpowers:subagent-driven-development` (empfohlen) oder `superpowers:executing-plans`, um diesen Plan Aufgabe für Aufgabe umzusetzen. Die Schritte tragen Kästchen (`- [ ]`) zum Abhaken.

**Ziel:** loxmatter von der archivierten Bibliothek `python-matter-server` (8.1.2, keine weiteren Updates) auf deren Nachfolger `matter-python-client` umstellen und den Testhost vom alten Server-Image auf `ghcr.io/matter-js/matterjs-server:stable` umziehen.

**Architektur:** Der Nachfolger liefert `matter_server*` und `chip*` unter **denselben Modulpfaden**. Der Umstieg ist deshalb ein Austausch der Abhängigkeit, kein Umbau von `src/`. Weil beide Clients `SCHEMA_VERSION = 11` tragen, redet der neue Client auch mit dem alten Server — Bibliothek und Image ziehen darum in getrennten Commits um, und der Testhost läuft nach dem ersten weiter.

**Tech-Stack:** Python 3.12+, `uv` für Abhängigkeiten und Lockfile, `pytest` (asyncio_mode=auto), `ruff`, `mypy --strict`, Docker Compose auf dem Test-Pi.

**Entwurf:** [docs/superpowers/specs/2026-09-08-matterjs-server-umstieg-design.md](../specs/2026-09-08-matterjs-server-umstieg-design.md)

## Globale Randbedingungen

- Alle Befehle laufen aus dem Wurzelverzeichnis des Worktrees, nie mit `cd` in das Hauptcheckout.
- Neue Abhängigkeit: `matter-python-client>=1.4.0` (PyPI, Apache-2.0). Ersetzt `python-matter-server>=8.1.2` **vollständig** — die alte Zeile bleibt nicht als Rückfall stehen.
- **Kein Codewechsel unter `src/loxmatter/`.** Zeigt eine Prüfung das Gegenteil, ist das ein Befund, der den Entwurf widerlegt: melden, nicht stillschweigend wegpatchen.
- Die volle Testsuite braucht rund **drei Minuten**. Das sieht aus wie ein Hänger, ist keiner — nicht abbrechen.
- Projektsprache im Quelltext und in Kommentaren ist Deutsch ohne Umlaute in neuen Kommentarblöcken dort, wo die Umgebung es bisher so hält; bestehende Umlaute bleiben, wie sie sind.
- Die drei Prüfläufe des Projekts (siehe `docs/DEVELOPMENT.md`): `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`.
- Aufgabe 3 ist **an dieser Maschine nicht verifizierbar** und muss im Commit und im README als ungeprüft gekennzeichnet sein.

---

### Aufgabe 1: Die Farbkommandos gegen das SDK absichern

**Warum zuerst:** Der Entwurf stützt sich in Abschnitt 2.3 darauf, dass die Kommandoklassen im neuen `chip`-Paket gleich heißen und dieselben Felder tragen. Für `LevelControl` prüft das bereits `test_send_command_passes_the_payload_as_command_fields`. Für **`ColorControl` prüft es nichts** — `tests/commands/test_translate.py` prüft nur die Nutzlast-Dicts, die `translate.py` baut, und läuft nie durch `chip.clusters.ClusterObjects.ALL_ACCEPTED_COMMANDS`.

Damit wäre genau die zuletzt gebaute Fähigkeit (Farbe und Farbtemperatur, Commits `8cf2358` und `fb0a81c`) diejenige, die ein Feldnamenwechsel im neuen SDK **still** brechen würde. Dieser Test wird vor dem Austausch geschrieben, damit er das alte Verhalten festhält und danach den Austausch bewacht.

**Dateien:**
- Ändern: `tests/matter/test_client.py` (anhängen nach `test_send_command_passes_the_payload_as_command_fields`, derzeit Zeile 674–692)

**Schnittstellen:**
- Nutzt: `make_connected_pair(nodes)` und `FakeNode` aus derselben Datei (Zeile 599 bzw. 31); `MatterCall` aus `loxmatter.commands.translate`; alle bereits in dieser Datei importiert.
- Liefert: nichts für spätere Aufgaben — reine Absicherung.

- [ ] **Schritt 1: Den fehlschlagenden Test schreiben**

An `tests/matter/test_client.py` anhängen, unmittelbar nach `test_send_command_passes_the_payload_as_command_fields`:

```python
async def test_send_command_builds_the_colour_temperature_command_from_the_sdk():
    """ColorControl (768) MoveToColorTemperature (10) durch `chip` hindurch.

    `tests/commands/test_translate.py` prueft nur das Nutzlast-Dict, das
    `translate.py` baut - nie, ob `chip.clusters.ClusterObjects.
    ALL_ACCEPTED_COMMANDS` daraus eine Klasse mit genau diesen Feldern
    macht. Ohne diesen Test braeche ein umbenanntes SDK-Feld die
    Farbtemperatur still: der Aufruf ginge hinaus, das Licht bliebe, wie
    es war.
    """
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()

    call = MatterCall(
        node_id=12,
        endpoint=1,
        cluster_id=768,
        command_id=10,
        payload={
            "colorTemperatureMireds": 370,
            "optionsMask": 1,
            "optionsOverride": 1,
        },
    )
    await bridge.send_command(call)

    _node_id, _endpoint_id, command = upstream.sent_commands[0]
    assert command.__class__.__name__ == "MoveToColorTemperature"
    assert command.colorTemperatureMireds == 370
    # Das Bit, ohne das ein Farbbefehl an einer ausgeschalteten Leuchte
    # verpufft (siehe `_EXECUTE_IF_OFF` in commands/translate.py).
    assert command.optionsMask == 1
    assert command.optionsOverride == 1


async def test_send_command_builds_the_hue_saturation_command_from_the_sdk():
    """ColorControl (768) MoveToHueAndSaturation (6), gleiche Begruendung."""
    bridge, upstream = make_connected_pair([FakeNode(12, {})])
    await bridge.connect()

    call = MatterCall(
        node_id=12,
        endpoint=1,
        cluster_id=768,
        command_id=6,
        payload={
            "hue": 85,
            "saturation": 254,
            "transitionTime": 0,
            "optionsMask": 1,
            "optionsOverride": 1,
        },
    )
    await bridge.send_command(call)

    _node_id, _endpoint_id, command = upstream.sent_commands[0]
    assert command.__class__.__name__ == "MoveToHueAndSaturation"
    assert command.hue == 85
    assert command.saturation == 254
```

- [ ] **Schritt 2: Prüfen, dass beide Tests grün laufen**

```bash
uv run pytest tests/matter/test_client.py -k "colour_temperature_command_from_the_sdk or hue_saturation_command_from_the_sdk" -v
```

Erwartung: **2 passed.** Anders als sonst bei TDD ist das hier richtig — der Test hält bestehendes Verhalten fest, bevor die Grundlage darunter ausgetauscht wird. Schlägt er hier fehl, stimmen die im Test genannten Feldnamen nicht mit dem **installierten alten** SDK überein; dann ist der Test falsch, nicht der Code.

- [ ] **Schritt 3: Beweisen, dass der Test wirklich durch das SDK läuft**

Ein grüner Test beweist noch nicht, dass er das prüft, was er zu prüfen vorgibt. Die Behauptung hier ist eine bestimmte: dass `bridge.send_command` die Nutzlast **an eine echte SDK-Klasse übergibt**, nicht als Dict weiterreicht. Wäre Letzteres der Fall, ginge ein umbenanntes Feld beim Bibliothekswechsel glatt durch — und der Test bliebe grün, obwohl die Farbtemperatur tot wäre.

Also den Fehler probeweise herstellen, **im Test selbst**: im Payload von `test_send_command_builds_the_colour_temperature_command_from_the_sdk` den Schlüssel verfälschen:

```python
            "colorTemperatureMiredsXXX": 370,
```

Dann:

```bash
uv run pytest tests/matter/test_client.py -k "colour_temperature_command_from_the_sdk" -v
```

Erwartung: **1 failed**, und zwar mit einem `TypeError` der Art `__init__() got an unexpected keyword argument 'colorTemperatureMiredsXXX'` — nicht mit einem `AssertionError`. Genau das ist der Beleg: `send_command` ruft `command_cls(**payload)` auf einer aus `ALL_ACCEPTED_COMMANDS` geholten Dataclass auf, und die kennt ihre Felder. Ein `AssertionError` an dieser Stelle hieße, dass die Nutzlast irgendwo als Dict durchgereicht wird und der Test die SDK-Grenze gar nicht berührt — dann ist der Test wertlos und muss umgeschrieben werden, bevor Aufgabe 2 beginnt.

Die Verfälschung zurücknehmen und Schritt 2 erneut laufen lassen: **2 passed.**

- [ ] **Schritt 4: Prüfläufe**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Erwartung: alle drei ohne Befund.

- [ ] **Schritt 5: Commit**

```bash
git add tests/matter/test_client.py
git commit -m "$(cat <<'EOF'
test(matter): Farbkommandos durch das SDK hindurch pruefen

`tests/commands/test_translate.py` prueft nur die Nutzlast-Dicts, die
`translate.py` baut. Ob `chip.clusters.ClusterObjects.ALL_ACCEPTED_COMMANDS`
daraus eine Klasse mit genau diesen Feldern macht, prueft fuer LevelControl
schon `test_send_command_passes_the_payload_as_command_fields` - fuer
ColorControl bisher nichts.

Damit waere ein umbenanntes SDK-Feld beim anstehenden Bibliothekswechsel
still durchgegangen und haette die zuletzt gebaute Faehigkeit getroffen:
Farbe und Farbtemperatur wuerden abgeschickt und blieben wirkungslos.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Aufgabe 2: Die Abhängigkeit austauschen

**Dateien:**
- Ändern: `pyproject.toml` (Zeile 12 — Abhängigkeit; Zeile 90–92 — mypy-Override-Kommentar)
- Ändern: `uv.lock` (durch `uv lock` erzeugt, nicht von Hand)
- Ändern: `docs/LICENSING.md:20`
- Ändern: `README.md:249-250`

**Schnittstellen:**
- Nutzt: den Test aus Aufgabe 1 als Wächter.
- Liefert: ein `src/`, das gegen `matter-python-client` läuft. Aufgabe 3 setzt darauf auf, hängt aber nicht davon ab (der neue Client redet auch mit dem alten Server).

- [ ] **Schritt 1: Die Abhängigkeit tauschen**

In `pyproject.toml` Zeile 12:

```toml
    "python-matter-server>=8.1.2",
```

ersetzen durch:

```toml
    # matter-python-client statt python-matter-server (8. September 2026):
    # `python-matter-server` ist mit 8.1.2 ARCHIVIERT - das ist die letzte
    # Version, die je erscheint. Der Nachfolger `matterjs-server`
    # (matter.js, Matter 1.6.0) liefert unter `python_client/` dieses Paket,
    # das `matter_server*` UND `chip*` unter denselben Modulpfaden
    # bereitstellt. Deshalb wechselt hier nur die Zeile, kein Modul unter
    # src/ - siehe Entwurf 2026-09-08, Abschnitt 2.
    "matter-python-client>=1.4.0",
```

- [ ] **Schritt 2: Lockfile erneuern und installieren**

```bash
uv lock && uv sync
```

Erwartung: `uv` löst `matter-python-client` auf und entfernt `python-matter-server`.

- [ ] **Schritt 3: Prüfen, dass das alte SDK wirklich verschwunden ist**

Das ist der Schritt, den man am ehesten überspringt und der am meisten kostet: bis hierher lieferte `home-assistant-chip-clusters` (von `python-matter-server` mitgezogen) das Paket `chip`. Bleiben beide installiert, entscheidet die Reihenfolge im Pfad, welches `chip` gewinnt — und der Test aus Aufgabe 1 belegt dann das falsche.

```bash
grep -c "home-assistant-chip-clusters" uv.lock
grep -c "python-matter-server" uv.lock
grep -c "matter-python-client" uv.lock
```

Erwartung: `0`, `0`, und eine Zahl **größer als 0**.

```bash
uv run python -c "import matter_server, chip; print(matter_server.__file__); print(chip.__file__)"
```

Erwartung: **beide** Pfade liegen unterhalb desselben Distributionsverzeichnisses und keiner davon unter einem Verzeichnis, dessen Name `home_assistant_chip` oder `python_matter_server` enthält.

- [ ] **Schritt 4: Prüfen, dass die Kommandotabelle im neuen Paket gefüllt wird**

`ALL_ACCEPTED_COMMANDS` existiert im neuen Paket, wird aber erst durch den Import von `chip.clusters.Objects` gefüllt (Seiteneffekt der Klassendefinitionen). Dass es existiert, ist nicht dasselbe wie: es trägt etwas.

```bash
uv run python -c "
import chip.clusters.Objects
from chip.clusters import ClusterObjects
t = ClusterObjects.ALL_ACCEPTED_COMMANDS
print('cluster:', len(t))
print('onoff/1:', t[6][1].__name__)
print('level/4:', t[8][4].__name__)
print('color/10:', t[768][10].__name__)
print('color/6:', t[768][6].__name__)
"
```

Erwartung:

```
cluster: <eine Zahl deutlich ueber 40>
onoff/1: On
level/4: MoveToLevelWithOnOff
color/10: MoveToColorTemperature
color/6: MoveToHueAndSaturation
```

- [ ] **Schritt 5: Die volle Testsuite**

```bash
uv run pytest
```

Erwartung: **alle Tests bestehen.** Rund drei Minuten Laufzeit — das ist normal, nicht abbrechen.

Schlägt hier etwas fehl, ist das der Punkt, an dem der Entwurf sich als falsch erweist. Dann **nicht** anfangen, `src/` anzupassen, sondern den Fehlschlag melden: die Behauptung „kein Codewechsel in `src/`" gehört dann berichtigt, bevor Code sie umgeht.

- [ ] **Schritt 6: Den mypy-Override-Kommentar nachziehen**

In `pyproject.toml` Zeile 90–92 steht:

```toml
# chip (Teil des CHIP-SDK, das python-matter-server fuer Cluster-Kommandos
# nutzt) liefert keine py.typed-Markierung/Stubs - siehe
# BridgeMatterClient.send_command in matter/client.py.
```

ersetzen durch:

```toml
# chip liefert keine py.typed-Markierung/Stubs - siehe
# BridgeMatterClient.send_command in matter/client.py. Seit dem 8. September
# 2026 kommt das Paket aus derselben Distribution wie `matter_server`
# (matter-python-client) und ist aus den matter.js-Modellen erzeugt, nicht
# mehr aus dem CHIP-SDK; an der fehlenden Typmarkierung aendert das nichts.
```

- [ ] **Schritt 7: Die beiden Dokumentationsstellen nachziehen**

`docs/LICENSING.md` Zeile 20:

```markdown
| `python-matter-server`, chip SDK, `python-multipart` | Apache-2.0 |
```

ersetzen durch:

```markdown
| `matter-python-client` (enthält `matter_server` und `chip`), `python-multipart` | Apache-2.0 |
```

`README.md` Zeile 249–250: der Satz lautet derzeit

```markdown
Python 3.12+ with FastAPI and uvicorn for the HTTP service, Typer for the CLI,
[`python-matter-server`](https://github.com/home-assistant-libs/python-matter-server)
for the Matter side, SQLite for stored devices and settings.
```

ersetzen durch:

```markdown
Python 3.12+ with FastAPI and uvicorn for the HTTP service, Typer for the CLI,
[`matter-python-client`](https://github.com/matter-js/matterjs-server/tree/main/python_client)
for the Matter side, SQLite for stored devices and settings.
```

- [ ] **Schritt 8: Prüfen, dass kein Verweis auf das alte Paket übrig ist**

```bash
grep -rn "python-matter-server\|python_matter_server\|home-assistant-libs" README.md docs/LICENSING.md docs/SETUP.md docs/OPERATIONS.md docs/DEVELOPMENT.md pyproject.toml install.sh scripts/ src/
```

Erwartung: **keine Treffer** außer erklärenden Erwähnungen der Historie. Treffer in `deploy/` gehören zu Aufgabe 3 und bleiben hier stehen; Treffer unter `docs/superpowers/` sind Entwürfe und Pläne — die beschreiben, was damals galt, und werden nicht rückwirkend umgeschrieben.

- [ ] **Schritt 9: Prüfläufe**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Erwartung: alle vier ohne Befund.

- [ ] **Schritt 10: Commit**

```bash
git add pyproject.toml uv.lock docs/LICENSING.md README.md
git commit -m "$(cat <<'EOF'
build: von python-matter-server auf matter-python-client

python-matter-server ist mit 8.1.2 archiviert - das ist die letzte Version,
die je erscheint. Der Nachfolger matterjs-server (matter.js, Matter 1.6.0)
liefert unter python_client/ das Paket matter-python-client, das
`matter_server*` UND `chip*` unter denselben Modulpfaden bereitstellt.

Deshalb wechselt hier nur die Abhaengigkeitszeile: kein Modul unter src/
ist angefasst. Geprueft wurde jede der sieben Importstellen und alle sechs
benutzten Methodensignaturen (Entwurf 2026-09-08, Abschnitt 2), und die
volle Testsuite laeuft gegen das neue Paket durch - einschliesslich der
Farbkommandos, die der vorige Commit erst gegen das SDK abgesichert hat.

Mit `python-matter-server` faellt auch `home-assistant-chip-clusters` aus
dem Lockfile: `chip` kommt jetzt aus derselben Distribution wie
`matter_server`, sodass keine zwei Pakete um denselben Modulnamen streiten.

Der Server auf dem Testhost bleibt vorerst der alte - beide Clients tragen
SCHEMA_VERSION 11, der neue redet also mit dem alten Server. Der Umzug des
Images ist ein eigener Commit.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Aufgabe 3: Compose und Testhost-README auf matterjs-server

**Diese Aufgabe ist an der Entwicklungsmaschine nicht verifizierbar.** Hier steht kein Docker, kein Pi, kein Bluetooth-Adapter und kein Thread-Funkmodul. Was hier entsteht, ist ein begründeter Entwurf für den Umzug — genau in der Rolle, in der der erste `Dockerfile` dieses Projekts entstand, und mit demselben ausdrücklichen Hinweis im Kopf der Datei.

**Dateien:**
- Ändern: `deploy/testhost/docker-compose.yml` (Service `matter-server`, Zeile 108–140)
- Ändern: `deploy/testhost/README.md` (Abschnitt „BLE aktivieren" ab Zeile 176; Abschnitt „3. matter-server-Image-Pfad" ab Zeile 544)

**Schnittstellen:**
- Nutzt: nichts aus Aufgabe 1 oder 2. Die Zweiteilung ist gerade der Zweck (Entwurf, Abschnitt 2.5).
- Liefert: nichts für spätere Aufgaben.

- [ ] **Schritt 1: Den Service-Block umstellen**

In `deploy/testhost/docker-compose.yml` den Block ab `image: ghcr.io/home-assistant-libs/python-matter-server:stable` ersetzen. Der neue Block, einschließlich der Begründungen — die Datei erklärt jede ihrer Zeilen, und diese hier sind erklärungsbedürftiger als die meisten:

```yaml
  matter-server:
    # matterjs-server statt python-matter-server (8. September 2026,
    # UNGEPRUEFT - siehe README, Abschnitt "Umzug auf matterjs-server").
    # python-matter-server ist mit 8.1.2 archiviert; der Nachfolger spricht
    # dieselbe WebSocket-API und benutzt dasselbe Datenverzeichnis, migriert
    # es aber beim ERSTEN Start in sein eigenes Format. Diese Migration ist
    # einseitig: geht sie schief, ist die Fabric weg und jedes eingelernte
    # Geraet muss zurueckgesetzt und neu gepaart werden. Vorher
    # `GET /api/diagnostics/fabric-backup` ziehen und das Archiv vom Pi
    # herunterholen - genau dafuer gibt es die Route.
    image: ghcr.io/matter-js/matterjs-server:stable
    container_name: matter-server
    network_mode: host
    restart: unless-stopped
    security_opt:
      - apparmor=unconfined
    volumes:
      # Dasselbe Verzeichnis wie zuvor. WICHTIG: der neue Container laeuft
      # unprivilegiert als UID 1000, das alte Image lief als root. Vor dem
      # ersten Start deshalb einmalig auf dem Pi:
      #     sudo chown -R 1000:1000 deploy/testhost/data
      #     sudo chmod -R u+rwX,go+rX deploy/testhost/data
      # Ohne das startet der Container nicht - er kommt in sein eigenes
      # Datenverzeichnis nicht hinein.
      - ./data:/data
      - /run/dbus:/run/dbus:ro
    environment:
      # BLE ueber den BlueZ-Daemon des Hosts statt ueber einen rohen
      # HCI-Socket. Der Standard waere `hci`, und der verlangt Rechte, die
      # der unprivilegierte Container-Nutzer nicht hat - BLE-Commissioning
      # bliebe damit still aus. Der dafuer noetige /run/dbus-Mount steht
      # oben schon.
      #
      # Die von der Doku genannte Alternative - Container als root fahren
      # (`user: 0:0`) - wird hier BEWUSST NICHT gewaehlt: sie hebt die
      # Container-Isolierung auf, und dieser Dienst laeuft mit
      # `network_mode: host` ohnehin schon offen im Netz.
      NOBLE_BINDINGS: dbus
    command:
      - --storage-path
      - /data
      # `--paa-root-cert-dir` ist ERSATZLOS ENTFALLEN (nicht vergessen):
      # die CLI-Doku des Nachfolgers fuehrt die Option unter "Deprecated
      # Options (were used in Python Matter Server) - not supported", weil
      # matter.js die PAA-Wurzelzertifikate ueber seinen eigenen
      # DCL-Client bezieht.
      - --bluetooth-adapter
      - "${BLUETOOTH_ADAPTER:-0}"
```

Der Kommentarblock über `matter-server` am Kopf der Datei (Zeile 12–14) nennt das alte Image ebenfalls; dort `ghcr.io/home-assistant-libs/python-matter-server:stable` durch `ghcr.io/matter-js/matterjs-server:stable` ersetzen und `--bluetooth-adapter` als weiterhin gültig, `NOBLE_BINDINGS=dbus` als neu erwähnen.

- [ ] **Schritt 2: Prüfen, dass Compose die Datei noch versteht**

Falls `docker` auf der Maschine vorhanden ist:

```bash
docker compose -f deploy/testhost/docker-compose.yml config >/dev/null && echo "syntaktisch in Ordnung"
```

Erwartung: `syntaktisch in Ordnung`. Fehlt `docker` — der Regelfall an dieser Maschine —, ersatzweise die YAML-Syntax prüfen:

```bash
uv run python -c "import yaml,sys; yaml.safe_load(open('deploy/testhost/docker-compose.yml')); print('YAML in Ordnung')"
```

Erwartung: `YAML in Ordnung`. **Das ist ausdrücklich kein Beleg, dass der Stack startet** — nur, dass die Datei lesbar ist.

- [ ] **Schritt 3: Den falschen Nachfolger-Hinweis im README berichtigen**

`deploy/testhost/README.md`, Abschnitt „3. matter-server-Image-Pfad" (ab Zeile 544). Der Absatz nennt derzeit `ghcr.io/matter-js/python-matter-server` als Nachfolgeprojekt. Das ist falsch: das ist der **Spiegel des alten Repositories** unter der neuen Organisation, nicht das Nachfolgeprojekt. Wer dem Hinweis folgt, landet wieder bei 8.1.2.

Den Abschnitt ersetzen durch:

```markdown
### 3. matter-server-Image-Pfad

Bis zum 8. September 2026 lief hier `ghcr.io/home-assistant-libs/python-matter-server:stable`.
Der Hinweis, der an dieser Stelle stand, nannte `ghcr.io/matter-js/python-matter-server`
als Nachfolger — **das war falsch**: dieser Pfad ist nur der Spiegel des alten
Repositories unter der neuen Organisation und liefert dieselbe eingefrorene 8.1.2.

Das tatsächliche Nachfolgeprojekt ist
[`matterjs-server`](https://github.com/matter-js/matterjs-server) —
`ghcr.io/matter-js/matterjs-server:stable`, eine Neuimplementierung auf matter.js
mit derselben WebSocket-API. Der Umzug steht im nächsten Abschnitt.
```

- [ ] **Schritt 4: Die Umzugsanleitung schreiben**

Unmittelbar nach dem eben geänderten Abschnitt anfügen:

```markdown
## Umzug auf matterjs-server (UNGEPRÜFT)

`deploy/testhost/docker-compose.yml` zeigt seit dem 8. September 2026 auf
`ghcr.io/matter-js/matterjs-server:stable`. **Dieser Umzug ist noch an keinem Pi
gelaufen** — er ist aus der Dokumentation des Nachfolgers abgeleitet, nicht gemessen.
Was hier steht, ist die Reihenfolge, in der er durchzuführen ist, und die zwei
Stellen, an denen er scheitern kann.

### Vorher: die Fabric sichern

Der erste Start migriert `./data` in das Format des neuen Servers. Diese Migration
ist **einseitig** — ein Rückweg auf das alte Image ist nirgends zugesagt. Geht sie
schief, ist die Fabric verloren und jedes eingelernte Gerät muss zurückgesetzt und
neu gepaart werden.

```bash
curl -sf -H "Authorization: Bearer $LOXMATTER_API_TOKEN" \
  http://<Pi>:8080/api/diagnostics/fabric-backup -o matter-fabric-backup.zip
```

Das Archiv **vom Pi herunterholen**, nicht dort liegen lassen. Es enthält die
kompletten Fabric-Credentials und gehört weder ins Repository noch in ein Log.

### Der Umzug

```bash
docker compose stop matter-server
sudo chown -R 1000:1000 deploy/testhost/data
sudo chmod -R u+rwX,go+rX deploy/testhost/data
docker compose pull matter-server
docker compose up -d matter-server
docker compose logs -f matter-server
```

Der `chown` ist keine Vorsichtsmaßnahme, sondern Voraussetzung: das alte Image lief
als root und hat das Verzeichnis entsprechend beschrieben, der neue Container läuft
unprivilegiert als UID 1000. Ohne den Schritt startet er nicht.

Die Logzeilen des ersten Starts enthalten die Migration. Erst wenn dort kein Fehler
steht und `loxmatter` sich wieder verbindet (`GET /api/diagnostics` zeigt den Punkt
`matter-server` grün), ist der Umzug durch.

### Was danach zu prüfen ist

Zwei Punkte, die aus der Dokumentation nicht folgen und nur am Gerät zu klären sind.
Bis sie geprüft sind, bleibt dieser Abschnitt mit „UNGEPRÜFT" überschrieben.

1. **BLE-Commissioning.** Das Compose setzt `NOBLE_BINDINGS=dbus`, weil der
   unprivilegierte Container keinen rohen HCI-Socket öffnen darf. Der Weg über
   BlueZ setzt voraus, dass `bluetoothd` läuft und `hci0` `Powered` ist — auf
   diesem Pi war der Adapter schon einmal rfkill-soft-blockiert (siehe Abschnitt
   weiter oben, das ist unabhängig vom Server). Prüfen, indem ein Gerät über den
   Pairing-Code in der WebUI eingelernt wird.
2. **Groß- und Kleinschreibung der Kommandonamen.** Die WebSocket-Doku des
   Nachfolgers zeigt Kommandonamen in camelCase (`moveToLevelWithOnOff`); der
   Python-Client sendet PascalCase (`MoveToLevelWithOnOff`), weil er
   `command.__class__.__name__` weiterreicht. Der Server muss beides annehmen,
   sonst wäre sein eigener Client kaputt — das ist ein Schluss, keine Messung.
   Prüfen, indem in der WebUI eine Lampe geschaltet **und** ihre Helligkeit
   verstellt wird.
```

- [ ] **Schritt 5: Prüfen, dass die README-Verweise stimmen**

```bash
grep -n "home-assistant-libs/python-matter-server" deploy/testhost/docker-compose.yml
grep -n "matter-js/matterjs-server" deploy/testhost/docker-compose.yml deploy/testhost/README.md
```

Erwartung: der erste Befehl findet **nichts mehr** in der Compose-Datei. Der zweite findet den neuen Pfad in beiden Dateien.

Der Abschnitt „BLE aktivieren" (ab Zeile 176) zitiert weiterhin `docker run --rm ghcr.io/home-assistant-libs/python-matter-server:stable --help` als Beleg dafür, woher der Optionsname stammt. **Dieses Zitat bleibt stehen** — es belegt, was damals gemessen wurde, und ein Beleg, den man nachträglich umschreibt, ist keiner mehr. Stattdessen am Ende des Abschnitts anfügen:

```markdown
> **Seit dem 8. September 2026** läuft hier `matterjs-server`. `--bluetooth-adapter`
> heißt dort genauso, aber der Container ist unprivilegiert und braucht zusätzlich
> `NOBLE_BINDINGS=dbus` — siehe „Umzug auf matterjs-server". Das Zitat oben bleibt
> als Beleg für das alte Image stehen.
```

- [ ] **Schritt 6: Prüfläufe**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Erwartung: alle vier ohne Befund. Diese Aufgabe fasst keinen Python-Code an; die Läufe belegen nur, dass nichts nebenbei kaputtgegangen ist.

- [ ] **Schritt 7: Commit**

```bash
git add deploy/testhost/docker-compose.yml deploy/testhost/README.md
git commit -m "$(cat <<'EOF'
deploy: Testhost auf matterjs-server umstellen (UNGEPRUEFT)

Image auf ghcr.io/matter-js/matterjs-server:stable. Drei Aenderungen, die
nicht optional sind:

- `--paa-root-cert-dir` entfaellt ersatzlos: die CLI-Doku des Nachfolgers
  fuehrt die Option unter "not supported", matter.js bezieht die
  PAA-Wurzelzertifikate ueber seinen eigenen DCL-Client.
- `NOBLE_BINDINGS=dbus`, weil der neue Container unprivilegiert laeuft und
  keinen rohen HCI-Socket oeffnen darf - ohne das bliebe BLE still aus.
  Die Alternative (Container als root) haette die Isolierung aufgehoben.
- `chown -R 1000:1000` auf dem Datenverzeichnis vor dem ersten Start, weil
  das alte Image als root schrieb und der neue Nutzer sonst nicht hineinkommt.

NICHT AN EINEM PI GELAUFEN. Aus der Doku des Nachfolgers abgeleitet, nicht
gemessen - wie seinerzeit der erste Dockerfile-Entwurf. Der README nennt die
Reihenfolge des Umzugs, die einseitige Datenmigration (vorher Fabric sichern)
und die zwei Punkte, die nur am Geraet zu klaeren sind: BLE ueber D-Bus und
ob der Server PascalCase-Kommandonamen annimmt.

Nebenbei berichtigt: der README nannte `ghcr.io/matter-js/python-matter-server`
als Nachfolger. Das ist nur der Spiegel des alten Repos und liefert dieselbe
eingefrorene 8.1.2.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Abdeckung gegen den Entwurf

| Entwurf | Aufgabe |
| --- | --- |
| 2.1 Importe, 2.2 Methodensignaturen | Aufgabe 2, Schritte 3 und 5 (volle Suite) |
| 2.3 Kommandoklassen | Aufgabe 1 (Farbe) + Aufgabe 2, Schritt 4 |
| 2.4 `attribute_subscriptions` | kein Schritt nötig — loxmatter liest das Feld nirgends |
| 2.5 Schema-Version | begründet die Zweiteilung; belegt durch Aufgabe 3, die Aufgabe 2 nicht voraussetzt |
| 3. Commit 1 | Aufgabe 2 |
| 4. Commit 2, Punkte 1–4 | Aufgabe 3, Schritte 1 und 4 |
| 5.1 Datenmigration | Aufgabe 3, Schritt 4 (Sicherung vor dem Umzug) |
| 5.2 Schreibweise der Kommandonamen | Aufgabe 3, Schritt 4 („Was danach zu prüfen ist", Punkt 2) |
| 6. Nicht Teil dieses Entwurfs | keine Aufgabe — bewusst |

**Abweichung vom Entwurf:** Der Entwurf nennt zwei Commits, dieser Plan hat drei. Aufgabe 1 kam hinzu, weil beim Schreiben des Plans auffiel, dass Abschnitt 2.3 sich auf die Kommandoklassen stützt, für `ColorControl` aber kein Test durch `chip` läuft. Ohne diesen Test bewachte die Suite den Austausch für `LevelControl`, nicht für die zuletzt gebaute Fähigkeit.
