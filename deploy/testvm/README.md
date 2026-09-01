# Test-VM: matter-server + OTBR

Testumgebung fuer Phase 1 (Task 6). **Nicht** der Produktions-Stack aus Spec 4.1 —
kein Hardening, kein Deployment-Guide, keine Diagnose-Seite. Das ist Phase 6. Dieses
Dokument haelt fest, was auf der konkreten VM tatsaechlich funktioniert hat, als
Rohstoff dafuer.

Host: `lucienkerl@10.0.1.215` (Ubuntu 26.04 LTS). Umgebung siehe Task-Brief; hier nur
das, was von den erhobenen Werten abwich oder sich erst beim Betrieb zeigte.

## Stand: was laeuft

- `docker exec otbr ot-ctl state` → `leader`
- RCP verbunden mit `RADIO_BAUDRATE=460800` (Radio Co-Processor Version laut Log:
  `SL-OPENTHREAD/2.4.4.0_GitHub-7074a43e4; EFR32; Sep 3 2025 11:42:40`)
- `matter-server` lauscht auf `0.0.0.0:5580` und `[::]:5580`
- Von einem Mac im selben LAN: `uv run loxmatter inspect --node 1 --url ws://10.0.1.215:5580/ws`
  verbindet sich und liefert auf stderr:
  ```
  Node 1 ist am matter-server (ws://10.0.1.215:5580/ws) nicht bekannt — kommissioniert?
  ```
  — laut Definition of Done der Beweis, dass die Verbindung steht (kein commissioniertes
  Geraet vorhanden, das ist Aufgabe von Task 7).

## Deployment

```bash
# auf dem Mac, im Repo:
scp deploy/testvm/docker-compose.yml deploy/testvm/.env.example \
    lucienkerl@10.0.1.215:~/loxmatter-testvm/

# auf der VM:
cd ~/loxmatter-testvm
cp .env.example .env      # RADIO_DEVICE/RADIO_BAUDRATE/BACKBONE_IF ggf. anpassen
mkdir -p data
docker compose up -d
docker compose logs -f otbr    # bis "Starting thread border agent otbr-agent ... done."
```

Hinweis: `docker compose up -d` garantiert nur die Container-Startreihenfolge, nicht dass der
OTBR vor `matter-server` betriebsbereit ist — fuer eine Testumgebung ohne Healthchecks
akzeptabel.

Thread-Netz einmalig bilden:

```bash
docker exec otbr ot-ctl dataset init new
docker exec otbr ot-ctl dataset commit active
docker exec otbr ot-ctl ifconfig up
docker exec otbr ot-ctl thread start
docker exec otbr ot-ctl state          # erwartet: leader
docker exec otbr ot-ctl dataset active -x
```

Kein `-it` verwenden — es gibt kein interaktives TTY über SSH `BatchMode=yes`,
`docker exec` reicht.

## Baudrate

**460800 hat beim ersten Versuch funktioniert.** Der RCP (SONOFF Dongle Plus MG24,
CP210x-Bruecke) hat sich sofort verbunden; im `otbr-agent`-Log erscheint sofort die
Radio-Co-Processor-Version, kein Timeout, kein zweiter Versuch mit 115200 noetig.

## Abweichungen vom Brief

Der Brief war ein Ausgangspunkt, kein verifizierter Endzustand — beide im Brief
genannten Unklarheiten (OTBR-Aufruf, Baudrate) mussten tatsaechlich geprueft werden.

### 1. OTBR-Image ist die "test"-Variante, nicht "border-router"

`openthread/otbr:latest` (Docker Hub, Digest zum Zeitpunkt des Deployments
`sha256:ebebd9f643f0fadf60a9e46a1c81b4f4c9f320f04863e69ed95d8fde6b5de5a6`) hat als
Entrypoint `/app/etc/docker/test/docker_entrypoint.sh` — das ist die aeltere,
"test"-Docker-Variante aus dem `ot-br-posix`-Repo, nicht die neuere
s6-overlay-basierte `border-router`-Variante (die andere Umgebungsvariablen wie
`OT_RCP_DEVICE`/`OT_INFRA_IF` erwartet und `command:`-Overrides ignoriert). Das war
vorab nicht offensichtlich — der Quellcode auf `main` im GitHub-Repo zeigt die neuere
Variante; welche davon `:latest` auf Docker Hub tatsaechlich ist, war nur per
`docker inspect --format '{{.Config.Entrypoint}}'` am gezogenen Image zu klaeren.
Die im Brief vorgegebene Compose-Syntax (`RADIO_URL` als Env-Var,
`--backbone-interface` als Kommandozeilenargument) passt zu dieser Variante und
funktioniert unveraendert.

### 2. NAT64/legacy-Firewall-Setup schlaegt fehl → per Env-Var deaktiviert

Beim ersten Start crashte der `otbr`-Container:

```
iptables v1.6.1: can't initialize iptables table `mangle': Table does not exist ...
iptables v1.6.1: can't initialize iptables table `nat': Table does not exist ...
iptables v1.6.1: can't initialize iptables table `filter': Table does not exist ...
 *** ERROR:  Failed to start NAT44!
```

Der Container hat kein `modprobe` (der vorgelagerte `sudo modprobe ip6table_filter`
schlaegt schon mit "command not found" fehl, wird aber ignoriert), kann die
legacy-iptables-Kernel-Tabellen (`mangle`/`nat`/`filter`) auf dem VM-Kernel also nicht
selbst nachladen. Das Entrypoint-Skript (`/app/script/_nat64`, `_firewall`) prueft
vor dem NAT64/NAT44- bzw. Firewall-Setup jeweils die Env-Variablen `NAT64` bzw.
`FIREWALL` (Default beide `1` im Image). Beide auf `"0"` gesetzt (siehe
`docker-compose.yml`, Kommentar dort) übersprang diesen Teil vollständig, danach
startete `otbr-agent` sauber durch.

Für die Testumgebung unkritisch: NAT64/NAT44 übersetzt Thread-IPv6 auf IPv4-Hosts
im LAN — hier nicht gebraucht, weder `matter-server` noch `loxmatter` müssen aus dem
Thread-Netz heraus IPv4-Ziele erreichen. Die legacy-Firewall haette Ingress-Filterung
fuer `wpan0` eingerichtet; ohne sie ist der Container offener als in Phase 6
vertretbar waere — das gehoert dort ins Hardening.

Kleinere Randnotiz aus dem Log, ebenfalls durch `FIREWALL=0`/fehlendes `modprobe`
bedingt und ohne Auswirkung auf den Betrieb:

```
Platform------: Got an error when executing command `ipset flush otbr-ingress-allow-dst-swap`: Resource temporarily unavailable
Firewall - failed to update ipsets: Failed
```

Das ist otbr-agents eigene (in-process) Firewall-Komponente, die ipset-Regeln pflegen
will; ohne die passenden Kernel-Module bleibt das eine Warnung (`[W]`), kein
Fataler Fehler.

### 3. matter-server-Image-Pfad

`ghcr.io/home-assistant-libs/python-matter-server:stable` wie im Brief — laesst
sich weiterhin ziehen. Hinweis fuer spaeter: das Upstream-README verweist
mittlerweile auf `ghcr.io/matter-js/python-matter-server` als Nachfolgeprojekt
(`python-matter-server` selbst ist auf Version 8.1.2 eingefroren, keine weiteren
Updates). Da `pyproject.toml` bereits `python-matter-server>=8.1.2` fixiert, passt
das zusammen — nur relevant, falls das `home-assistant-libs`-Image irgendwann
verschwindet.

## Fabric-Volume sichern (`./data`)

`matter-server` legt Fabric-/Node-Zustand unter `~/loxmatter-testvm/data` ab
(`chip.json`, `chip_*.ini`, `credentials/`, sowie eine `<NodeID>.json` pro
committetem Node). Sicherung von der VM:

```bash
ssh lucienkerl@10.0.1.215 'tar czf - -C ~/loxmatter-testvm data' > matter-server-data-backup.tar.gz
```

Rueckspielen (Container vorher stoppen):

```bash
ssh lucienkerl@10.0.1.215 'cd ~/loxmatter-testvm && docker compose stop matter-server'
cat matter-server-data-backup.tar.gz | ssh lucienkerl@10.0.1.215 'tar xzf - -C ~/loxmatter-testvm'
ssh lucienkerl@10.0.1.215 'cd ~/loxmatter-testvm && docker compose start matter-server'
```

## Thread-Datensatz — NICHT ins Repository

`docker exec otbr ot-ctl dataset active -x` gibt den aktiven Thread-Operational-Dataset
aus (hex-kodiert). Das ist ein Netzwerk-Credential (enthaelt u.a. den Network Key) —
wer ihn hat, kann dem Thread-Netz beitreten. Er gehoert **nicht** ins Repository und
nicht unter `deploy/`.

Abgelegt auf der VM unter `~/loxmatter-testvm/thread-dataset.txt` (Modus `600`,
nur fuer den Betreiber lesbar). `deploy/testvm/.gitignore` schliesst zusaetzlich
`.env`, `data/` und alles, was wie ein Dataset benannt ist
(`*.dataset`, `thread-dataset*`), von Commits aus, falls jemand versehentlich in
diesem Verzeichnis arbeitet.

Erneut abrufen: `ssh lucienkerl@10.0.1.215 docker exec otbr ot-ctl dataset active -x`

Fuer Task 7 (Einlernen der IKEA-Geraete) wird dieser Datensatz gebraucht.

## Bekannte Einschraenkungen (bewusst, fuer eine Testumgebung)

- Keine legacy-Firewall auf `wpan0` (siehe Abweichung 2) — kein Hardening-Ziel dieser
  Task.
- Kein Bluetooth-Adapter auf der VM (`ls /sys/class/bluetooth` liefert nichts,
  `bluetooth.service` ist inaktiv). BLE-Commissioning ueber `matter-server` wird also
  nicht funktionieren; Task 7 muss dafuer einen anderen Weg finden (z. B. Discovery/
  Commissioning direkt ueber das bereits gebildete Thread-Netz, oder ein
  BLE-Dongle muesste noch ergaenzt werden). Nicht in Task 6 geloest.
- Kein globales IPv6 auf `ens18` (nur link-local) — laut Brief fuer Thread-Geraete
  unkritisch, da OTBR ein eigenes ULA-Praefix auf `wpan0` aufspannt und
  `matter-server` per `network_mode: host` mitroutet. Nur relevant, falls spaeter
  Matter-ueber-WLAN-Geraete dazukommen.

## Dateien in diesem Verzeichnis

- `docker-compose.yml` — Compose-Definition, wie auf der VM unter
  `~/loxmatter-testvm/docker-compose.yml` deployt (identisch).
- `.env.example` — Vorlage fuer `.env` auf der VM.
- `.gitignore` — verhindert versehentliches Commit von `.env`, `data/` und
  Dataset-Dateien, falls diese jemals lokal in diesem Repo-Pfad angelegt werden.
