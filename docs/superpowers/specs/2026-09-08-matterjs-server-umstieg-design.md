# Umstieg auf matterjs-server: die Client-Bibliothek und das Server-Image

Entwurf, 8. September 2026. Loest loxmatter von `python-matter-server`, das
seit Version 8.1.2 archiviert ist, und setzt es auf dessen Nachfolger
`matterjs-server` (matter.js, TypeScript, Matter 1.6.0) samt dessen
Python-Client.

Anlass: die Recherche zu Matter-Gruppen am selben Tag (siehe Abschnitt 7).
Sie sollte klaeren, wie native Gruppen umzusetzen sind, und foerderte
stattdessen zutage, dass die Bibliothek, auf der dieses Projekt steht, keine
Updates mehr bekommt.

## 1. Das Problem

`python-matter-server` ist **archiviert**. 8.1.2 ist die letzte Version, die
je erscheinen wird. Home Assistant hat auf
[`matterjs-server`](https://github.com/matter-js/matterjs-server) umgestellt —
eine Neuimplementierung auf Basis von matter.js, die dieselbe
WebSocket-API spricht und als Drop-in-Ersatz gedacht ist.

loxmatter haengt an zwei Stellen an der eingefrorenen Software:

- `pyproject.toml` fordert `python-matter-server>=8.1.2` — von dort kommt die
  Client-Bibliothek `matter_server.client`, die `matter/client.py` benutzt,
  und mit ihr transitiv das `chip`-Paket, aus dem `matter/client.py` die
  Kommandoklassen zieht.
- `deploy/testhost/docker-compose.yml` zieht
  `ghcr.io/home-assistant-libs/python-matter-server:stable`.

Der `deploy/testhost/README.md` kennt einen Teil davon schon (Abschnitt „3.
matter-server-Image-Pfad"), benennt als Nachfolger aber
`ghcr.io/matter-js/python-matter-server`. Das ist nur der **Spiegel des alten
Repositories** unter der neuen Organisation, nicht das Nachfolgeprojekt. Wer
diesem Hinweis folgt, landet wieder bei 8.1.2.

Praktische Folge: jede kuenftige Matter-Faehigkeit — Matter 1.6, neue Cluster,
Groupcast — ist fuer dieses Projekt unerreichbar, solange es an 8.1.2 haengt.
Und keine dieser Faehigkeiten wird nachgereicht.

## 2. Was geprueft wurde

Der Nachfolger liefert unter `python_client/` ein eigenes Python-Paket:
**`matter-python-client`** (PyPI, aktuell 1.4.0, Apache-2.0). Es stellt die
Pakete `matter_server*` **und** `chip*` bereit — **dieselben Modulpfade**, die
loxmatter heute importiert. Der Umstieg der Bibliothek ist damit ein
Austausch der Abhaengigkeit, kein Umbau des Codes.

Geprueft wurde jede einzelne Beruehrstelle gegen den Quelltext des neuen
Pakets (Stand `main`, 8. September 2026), nicht gegen dessen Dokumentation:

### 2.1 Importe

| Importstelle | Symbol | im neuen Paket |
| --- | --- | --- |
| `matter/client.py:249` | `matter_server.client.client.MatterClient` | vorhanden |
| `matter/client.py:426`, `cli.py:33` | `matter_server.client.exceptions.{CannotConnect, ConnectionClosed, NotConnected}` | vorhanden |
| `matter/client.py:555,612` | `matter_server.common.models.EventType` | vorhanden |
| `matter/client.py:518` | `chip.clusters.ClusterObjects` | vorhanden |
| `tests/matter/test_client.py:22` | `matter_server.common.models.MatterNodeEvent` | vorhanden |
| `tests/matter/test_client_commissioning.py:21` | `matter_server.common.errors.NodeCommissionFailed` | vorhanden |
| `tests/profiles/test_{categories,endpoints}.py` | `matter_server.client.models.device_types.ALL_TYPES` | vorhanden |

`EventType` traegt alle vier von loxmatter benutzten Werte (`NODE_ADDED`,
`NODE_UPDATED`, `NODE_REMOVED`, `NODE_EVENT`) plus `ATTRIBUTE_UPDATED`, und
zusaetzlich neue, die uns nicht stoeren.

### 2.2 Methodensignaturen

> **Berichtigung (Schluss-Review, 8. September 2026).** Dieser Abschnitt sagte
> zuerst „genau sechs Methoden" und zaehlte `start_listening`, `disconnect`,
> `get_nodes`, `subscribe_events`, `send_device_command`, `commission_with_code`.
> Das war falsch: es sind **acht Methoden und zusaetzlich eine gelesene
> Eigenschaft**. Uebersehen waren `remove_node`, `set_thread_operational_dataset`
> und der Zugriff auf `upstream.server_info`. Die Auslassung ist nicht harmlos —
> sie trifft ausgerechnet den einen Aufruf, dessen Nutzlast sich aendert (siehe
> unten). Ein Entwurf, der seine eigene Berichtigung verschweigt, ist als Beleg
> weniger wert; deshalb steht die falsche Fassung hier und wird nicht getilgt.
> Der Commit `beee42f` wiederholt die Zahl sechs in seiner Nachricht — die ist
> Geschichte und bleibt stehen.

`matter/client.py` ruft acht Methoden des Upstream-Clients auf und liest
zusaetzlich eine Eigenschaft. `connect` gehoert **nicht** dazu —
`BridgeMatterClient.connect()` startet stattdessen `start_listening` als
Hintergrund-Task und wartet auf dessen Bereitschafts-Event, siehe
Moduldocstring.

| Beruehrstelle | Aufruf | im neuen Paket |
| --- | --- | --- |
| `matter/client.py:282` | `start_listening(ready)` | vorhanden |
| `matter/client.py:378` | `disconnect()` | vorhanden |
| `matter/client.py:413,676,777` | `get_nodes()` | vorhanden, liefert weiterhin `MatterNode` |
| `matter/client.py:444` | `commission_with_code(code)` | vorhanden, siehe unten |
| `matter/client.py:461` | `remove_node(node_id)` | vorhanden, zeichengleich |
| `matter/client.py:489` | `server_info` (gelesen) | vorhanden als `property -> ServerInfoMessage \| None` |
| `matter/client.py:522` | `set_thread_operational_dataset(dataset)` | vorhanden, **Nutzlast geaendert**, siehe unten |
| `matter/client.py:564` | `send_device_command(...)` | vorhanden, siehe unten |
| `matter/client.py:604,667` | `subscribe_events(...)` | vorhanden, siehe unten |

Die Zeilennummern sind der Stand **nach** den Kommentar-Berichtigungen der
Schluss-Review (Befund B6). Die Nummern in Abschnitt 2.1 stammen von davor
und sind deshalb um 15 bzw. 33 Zeilen kleiner als die heutigen — die
Ergaenzungen sind Docstring-Prosa, keine Anweisungen.

Weder `remove_node` noch `set_thread_operational_dataset` noch
`upstream.server_info` sind von der Testsuite gedeckt — die Tests benutzen
durchgehend eine Attrappe — und keiner von mypy, weil `_upstream` als `Any`
gefuehrt wird. Fuer diese drei ist der Quelltextvergleich unten die einzige
Absicherung, die es gibt.

Die drei heiklen sind zeichengleich:

- `subscribe_events(callback, event_filter, node_filter, attr_path_filter)` —
  identische Signatur, identisches Schluessel-Matching ueber
  `f"{event}/{node}/{path}"` mit Wildcard. Die gesamte Begruendung im
  Moduldocstring von `matter/client.py` (warum je eine Subscription pro
  (Node, Pfad)-Paar noetig ist, weil `data` bei `ATTRIBUTE_UPDATED` nur der
  Wert ist) bleibt unveraendert gueltig.
- `send_device_command(node_id, endpoint_id, command, ...)` — identisch,
  und leitet `command_name` weiterhin aus `command.__class__.__name__` ab.
- `commission_with_code(...) -> MatterNodeData` — gibt weiterhin das
  **flache** `MatterNodeData` zurueck, nicht `MatterNode`. Die Falle, ueber
  die Phase 5 gestolpert ist (`node_data.attributes` vs. `node.node_data.
  attributes`), bleibt also dieselbe Falle, und der Code, der sie umgeht,
  bleibt richtig.

`MatterNode` traegt weiterhin `node_data` als Attribut, `get_nodes()` liefert
weiterhin `MatterNode`. Der Unterschied zwischen beiden Typen, den
`matter/client.py` im Docstring festhaelt, besteht unveraendert fort.

#### `set_thread_operational_dataset` — der einzige Aufruf mit geaenderter Nutzlast

Das ist die Stelle, die die erste Fassung dieses Abschnitts uebersehen hatte,
und zugleich die einzige, an der sich etwas aendert:

| | Signatur | ueber den Draht |
| --- | --- | --- |
| `python-matter-server` 8.1.2 | `set_thread_operational_dataset(dataset)` | `dataset` |
| `matter-python-client` 1.4.0 | `set_thread_operational_dataset(dataset, entry_id="default")` | `dataset`, **`id`** |

loxmatter gibt `entry_id` nicht an (`matter/client.py:522`), bekommt also
`"default"` — und nur fuer `entry_id != "default"` verlangt der neue Client
ueberhaupt eine hoehere Schema-Version (`require_schema=12`, sonst `None`).
Der Aufruf faellt damit nicht unter die Schema-Pruefung aus Abschnitt 2.5.

**Warum das trotzdem traegt, auch gegen einen alten 8.1.2-Server:** dessen
Argument-Aufloesung laeuft mit `strict=False`
(`matter_server/common/helpers/api.py:51,57`) und verwirft unbekannte
Schluessel stillschweigend, statt den Aufruf abzulehnen. Das zusaetzliche `id`
kommt an und wird ignoriert.

**Warum das hier trotzdem als Befund steht, obwohl es harmlos ist:** die
gesamte Sicherheit der Zweiteilung dieses Umstiegs — erst die Bibliothek
tauschen, dann das Server-Image — beruht auf Drahtkompatibilitaet in beide
Richtungen. Der Entwurf hat diese Zweiteilung damit begruendet und
ausgerechnet den einen Aufruf nicht geprueft, bei dem sie haette scheitern
koennen. Dass sie haelt, ist jetzt belegt; vorher war es unbelegt und wurde
als belegt dargestellt. Derselbe Sachverhalt steht im Docstring von
`BridgeMatterClient.set_thread_dataset`.

### 2.3 Kommandoklassen

`matter/client.py` baut Kommandos ueber
`chip.clusters.ClusterObjects.ALL_ACCEPTED_COMMANDS[cluster_id][command_id]`
und ruft die gefundene Klasse mit den Feldnamen aus `commands/translate.py`
auf. Beides traegt:

- `ALL_ACCEPTED_COMMANDS` existiert und wird ueber denselben
  `__init_subclass__`-Mechanismus gefuellt — der explizite Import von
  `chip.clusters.Objects` allein wegen des Seiteneffekts bleibt also noetig
  und richtig.
- Die Klassen heissen gleich und tragen dieselben Felder:
  `MoveToLevelWithOnOff(level, transitionTime, optionsMask, optionsOverride)`,
  `MoveToColorTemperature(colorTemperatureMireds)`,
  `MoveToHueAndSaturation(hue, saturation)`.

`commands/translate.py` und `commands/color.py` bleiben damit unangetastet.

### 2.4 `attribute_subscriptions`

Die Kompatibilitaetsdoku des Nachfolgers nennt als Unterschied, dass
`MatterNode.attribute_subscriptions` bei matter.js **immer leer** ist, weil
der Server saemtliche Attribute von sich aus abonniert.

Fuer loxmatter folgenlos: eine Volltextsuche nach `attribute_subscriptions`
ueber `src/` und `tests/` liefert keinen Treffer. loxmatter registriert nie
Attribute beim Server, es registriert nur **Callbacks** ueber
`subscribe_events` mit `attr_path_filter`. Dass der Server ohnehin alles
abonniert, macht diesen Weg eher robuster: es kann kein Pfad mehr fehlen,
den `follow_node()` nachziehen muesste.

### 2.5 Schema-Version — die beiden Aenderungen sind unabhaengig

Der neue Client bricht die Verbindung ab, wenn der Server eine kleinere
`schema_version` meldet als der Client selbst traegt, oder eine groessere
`min_supported_schema_version` fordert.

- neuer Client: `SCHEMA_VERSION = 11`
- python-matter-server 8.1.2: `SCHEMA_VERSION = 11`,
  `MIN_SCHEMA_VERSION = 9`

Beide Bedingungen sind erfuellt (11 >= 11 und 9 <= 11). **Der neue Client
verbindet sich also mit dem alten Server.** Das ist der Grund, warum dieser
Entwurf in zwei getrennte Commits zerfaellt und nicht in einen: nach Commit 1
laeuft der Testhost unveraendert weiter, auch wenn Commit 2 dort noch nicht
angekommen ist.

Die von loxmatter benutzten Methoden fordern kein hoeheres Schema:
`commission_with_code` verlangt nur dann `require_schema=12`, wenn
`wifi_credentials_id`/`thread_dataset_id` mitgegeben werden — das tut
loxmatter nicht.

## 3. Commit 1 — die Abhaengigkeit

Betroffene Dateien: `pyproject.toml`, `uv.lock`, `docs/LICENSING.md`,
`README.md`.

- `python-matter-server>=8.1.2` → `matter-python-client>=1.4.0`.
- Der Kommentarblock in `pyproject.toml` bei `chip` (Zeile 90) nennt das Paket
  als „Teil des CHIP-SDK, das python-matter-server fuer Cluster-Kommandos
  benutzt" — die Herkunft aendert sich, die Aussage muss nachgezogen werden:
  `chip` kommt jetzt aus demselben Paket wie `matter_server` und ist aus den
  matter.js-Modellen erzeugt.
- `docs/LICENSING.md` Zeile 20: der Eintrag nennt `python-matter-server` und
  `chip SDK` als Apache-2.0. Der Nachfolger ist ebenfalls Apache-2.0, der
  Name aendert sich.
- `README.md` Zeile 250 verlinkt das alte Repository.

**Kein Codewechsel in `src/`.** Sollte `uv sync` das Gegenteil zeigen, ist
das ein Befund, der diesen Entwurf widerlegt, und gehoert gemeldet, nicht
stillschweigend weggepatcht.

**Verifikation:** `uv sync`, dann die volle Testsuite (`uv run pytest`, rund
drei Minuten). Sie deckt die Importe direkt ab und ueber den Fake-Upstream in
`tests/matter/` auch den Pfad durch `ALL_ACCEPTED_COMMANDS`. Damit ist
Commit 1 ohne Hardware vollstaendig belegt.

Zusaetzlich zur Testsuite: `uv run python -c "import chip.clusters.Objects;
from chip.clusters import ClusterObjects; print(len(ClusterObjects.
ALL_ACCEPTED_COMMANDS))"` — belegt, dass die Registrierungstabelle im neuen
Paket tatsaechlich gefuellt wird und nicht bloss existiert.

## 4. Commit 2 — Server-Image und Compose

Betroffene Dateien: `deploy/testhost/docker-compose.yml`,
`deploy/testhost/README.md`.

Vier Aenderungen, alle aus der Dokumentation des Nachfolgers belegt:

1. **Image** → `ghcr.io/matter-js/matterjs-server:stable`.

2. **`--paa-root-cert-dir` muss entfallen.** Die CLI-Doku des Nachfolgers
   fuehrt die Option unter „Deprecated Options (were used in Python Matter
   Server) … **not supported**" mit der Begruendung „Handled internally by
   matter.js DCL client". `--storage-path /data` bleibt unveraendert.

3. **BLE braucht `NOBLE_BINDINGS=dbus`.** Der neue Container laeuft
   **unprivilegiert**, und das Standard-Bluetooth-Backend (`NOBLE_BINDINGS=
   hci`) oeffnet einen rohen HCI-Socket, wozu dem Container-Nutzer die
   Rechte fehlen. Der D-Bus-Weg redet stattdessen mit dem BlueZ-Daemon des
   Hosts; der dafuer noetige Mount `/run/dbus:ro` steht bereits im Compose.
   `--bluetooth-adapter ${BLUETOOTH_ADAPTER}` bleibt als Argument bestehen.

   Die Alternative, die die Doku nennt — Container als root fahren
   (`user: 0:0`) — wird hier **nicht** gewaehlt: sie hebt die
   Container-Isolierung auf, und der Dienst faehrt ohnehin schon mit
   `network_mode: host`.

4. **`chown -R 1000:1000` auf `deploy/testhost/data`** vor dem ersten Start.
   Das Verzeichnis wurde bisher von einem als root laufenden Container
   beschrieben; der neue Nutzer (UID 1000) kaeme sonst nicht hinein. Ohne
   diesen Schritt startet der Container nicht.

Der Fabric-Sicherungspfad bleibt unberuehrt: `GET /api/diagnostics/
fabric-backup` (`api/diagnostics.py:703`) zippt das eingehaengte Verzeichnis
rekursiv per `rglob("*")` und liest kein Dateiformat. Ein geaendertes
Speicherformat geht ihn nichts an.

Ebenso unberuehrt: der Diagnosepunkt `thread-credentials`
(`_check_thread_credentials`) liest keine Dateien, sondern das
In-Memory-Flag `BridgeMatterClient.thread_dataset_set`.

Der README-Abschnitt „3. matter-server-Image-Pfad" wird korrigiert: nicht
`ghcr.io/matter-js/python-matter-server` (der Spiegel des alten Repos),
sondern `ghcr.io/matter-js/matterjs-server`.

## 5. Was Commit 2 nicht beweisen kann

Zwei Punkte bleiben offen, bis der Umzug am Test-Pi tatsaechlich gelaufen
ist. Beide gehoeren als solche in den README, nicht als erledigt.

### 5.1 Die Datenmigration ist einmalig und einseitig

Der erste Start des neuen Images migriert `./data` in sein eigenes Format.
Die Doku sagt zu, dasselbe Datenverzeichnis und dieselben Argumente
weiterverwenden zu koennen; ein Rueckweg auf das alte Image ist damit aber
nicht zugesagt.

Geht die Migration schief, ist die Fabric verloren und **jedes eingelernte
Geraet muss zurueckgesetzt und neu gepaart werden** — bei einem Sensor hinter
einer Schranktuer keine Kleinigkeit (dieselbe Ueberlegung, die im Compose
schon das `otbr-state`-Volume begruendet).

Deshalb: **vor dem ersten Start `GET /api/diagnostics/fabric-backup` ziehen
und das Archiv ausserhalb des Pi ablegen.** Genau dafuer existiert die
Route.

### 5.2 Gross- und Kleinschreibung der Kommandonamen

Die WebSocket-Doku des Nachfolgers zeigt Kommandonamen in camelCase
(`"command_name": "moveToLevelWithOnOff"`). Der mitgelieferte Python-Client
sendet jedoch `command.__class__.__name__` und damit PascalCase
(`MoveToLevelWithOnOff`) — dasselbe, was loxmatter heute schickt.

Der Server muss also beide Schreibweisen annehmen, sonst waere sein eigener
Python-Client kaputt. Das ist ein Schluss, keine Messung: belegt ist es erst
durch ein Licht, das am Pi tatsaechlich auf einen Klick reagiert.

## 6. Nicht Teil dieses Entwurfs

- **Matter-Gruppen / Groupcast.** Bewusst zurueckgestellt (Abschnitt 7).
- **Neue Faehigkeiten des Nachfolgers.** `get_network_topology`, die
  ICD-Kommandos, der OTA-Upload und die Thread-Diagnose sind vorhanden und
  koennten loxmatter spaeter nutzen. Sie hier mitzunehmen wuerde einen
  Austausch der Grundlage mit einem Funktionsausbau vermischen — und damit
  genau die Trennung aufheben, die Abschnitt 2.5 moeglich macht.
- **Die `--fabricid`-Vorgabe.** Python setzte 1, matter.js waehlt zufaellig.
  Das betrifft nur das Anlegen einer **neuen** Fabric; die migrierte behaelt
  ihre. Hier ist nichts zu tun, und ein vorsorgliches `--fabricid 1` waere
  ein Eingriff in bestehenden Zustand ohne Anlass.

## 7. Herkunft

Aufgefallen bei der Recherche zu Matter-Gruppen am 8. September 2026. Die
Frage war, wie natives Group Messaging (ein Multicast-Kommando fuer mehrere
Lampen statt N Einzelkommandos) umzusetzen ist. Antwort: gar nicht, derzeit —
weder `python-matter-server` noch `matterjs-server` exponieren Gruppen ueber
ihre WebSocket-API, und `send_device_command` prueft in beiden Faellen den
Node-Cache, sodass eine Group Node ID (`0xFFFF_FFFF_FFFF_0000 | groupId`)
nicht durchkommt.

Die Gruppenfunktion wartet deshalb, bis `matterjs-server` sie nativ anbietet.
matter.js selbst bringt die Controller-Seite bereits mit
(`packages/protocol/src/groups/FabricGroups.ts`, `KeySets.ts`,
`GroupSession.ts`); es fehlt allein die WS-Schicht. Zusaetzlich hat Matter
1.6 (17. Juni 2026) den **Groupcast-Cluster** freigegeben, der Groups
(`0x0004`) und GroupKeyManagement (`0x003F`) fuer die Gruppenverwaltung
ersetzt — in matter.js noch provisional und mit harten Throws abgeriegelt.

Dieser Entwurf ist die Voraussetzung dafuer: solange loxmatter an 8.1.2
haengt, kann es Groupcast nicht bekommen, wenn es denn kommt.
