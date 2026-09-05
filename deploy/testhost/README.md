# Test-Host: matter-server + OTBR

Testumgebung fuer Phase 1 (Task 6). **Nicht** der Produktions-Stack aus Spec 4.1 —
kein Hardening, kein Deployment-Guide, keine Diagnose-Seite. Das ist Phase 6. Dieses
Dokument haelt fest, was auf dem konkreten Host tatsaechlich funktioniert hat, als
Rohstoff dafuer.

**Aktueller Host:** `pi@10.0.1.56` — ein Raspberry Pi 4 Model B Rev 1.5 (Debian 13
"trixie", Raspberry Pi OS, aarch64, 8 GB RAM). Dieses Verzeichnis hiess ursprünglich
`deploy/testvm/` und lief auf einer Ubuntu-VM (`lucienkerl@10.0.1.215`). Es wurde auf
den Pi umgezogen, weil **die VM keinen Bluetooth-Adapter hatte** — Matter-Commissioning
läuft über BLE, und ohne Adapter kann kein Gerät je eingelernt werden (`ls
/sys/class/bluetooth` lieferte nichts, `bluetooth.service` war inaktiv). Der Pi hat
einen eingebauten Adapter (`hci0`). Die VM-Historie inklusive aller dort gefundenen
Probleme ist unten unter "Historie: die VM" festgehalten, weil die Ursachen (NAT64/
Firewall, OTBR-Image-Variante, Baudrate) unverändert für den Pi gelten — derselbe
Dongle, dieselbe Firmware, dasselbe Image.

## Stand: was laeuft

- `docker exec otbr ot-ctl state` → `leader`
- RCP verbunden mit `RADIO_BAUDRATE=460800` (wie auf der VM — derselbe SONOFF Dongle
  Plus MG24, dieselbe Firmware, unveraendert uebernommen und beim ersten Versuch
  erfolgreich). `ot-ctl version` liefert `OPENTHREAD/; POSIX; ...`; im Syslog laufen
  Spinel-Frames zwischen otbr-agent und dem RCP (z. B. `PROP_VALUE_GET, key:TIMESTAMP`)
  — der Dongle antwortet.
- `matter-server` lauscht auf `0.0.0.0:5580` und `[::]:5580`, **mit aktiviertem BLE**
  (siehe "BLE aktivieren" unten für den Beweis).
- Von einem Mac im selben LAN: `uv run loxmatter inspect --node 1 --url
  ws://10.0.1.56:5580/ws` verbindet sich und liefert auf stderr:
  ```
  Node 1 ist am matter-server (ws://10.0.1.56:5580/ws) nicht bekannt — kommissioniert?
  ```
  Exit-Code 1 — laut Definition of Done der Beweis, dass die Verbindung steht (kein
  kommissioniertes Geraet vorhanden, das ist Aufgabe von Task 7).

## Deployment

Der komplette Ablauf, von oben nach unten durchlaufbar. Kein `-it` verwenden — es
gibt kein interaktives TTY über SSH `BatchMode=yes`, `docker exec` reicht. **Kein
`sudo`** — der Pi verlangt dafür ein Passwort, das nie angefordert oder eingegeben
wird; alles unten ist als `pi`-User machbar.

**Zwei zusätzliche manuelle Schritte sind auf dem Pi nötig, die auf der VM nicht
nötig waren.** Sie stehen unten bereits an der richtigen Stelle in der Reihenfolge
(Begründung und Details in "Bluetooth-Adapter ist rfkill-soft-blocked" und
"start-stop-daemon haengt auf dem Pi-Kernel" weiter unten):

1. `hci0` per rfkill entsperren, **bevor** `matter-server` gestartet wird (Schritt 2).
2. `otbr-agent`/`otbr-web` nach `docker compose up -d` manuell nachstarten, weil
   `start-stop-daemon` im OTBR-Image auf diesem Kernel nie fertig wird (Schritt 4).

**Schritt 1 — Dateien kopieren und Konfiguration anlegen:**

```bash
# auf dem Pi (seit 2026-09-03 ein Git-Checkout, vorher per scp kopierte
# Einzeldateien unter ~/loxmatter-testhost):
git clone https://github.com/lucienkerl/loxmatter.git ~/matter-loxone
cd ~/matter-loxone/deploy/testhost
cp .env.example .env      # RADIO_DEVICE/RADIO_BAUDRATE/BACKBONE_IF/BLUETOOTH_ADAPTER ggf. anpassen
mkdir -p data

# MINISERVER_IP muss gesetzt werden, leer ausgeliefert. LOXMATTER_API_TOKEN
# ist optional (siehe unten) - wer es trotzdem setzen will, ERSETZT die
# vorhandene Zeile, statt eine zweite anzuhaengen: eine zweite Definition
# derselben Variablen waere zwar wirksam (Compose nimmt die letzte), aber
# wer die Datei spaeter bearbeitet, aendert dann die falsche Zeile.
sed -i "s|^LOXMATTER_API_TOKEN=.*|LOXMATTER_API_TOKEN=$(openssl rand -hex 32)|" .env
sed -i "s|^MINISERVER_IP=.*|MINISERVER_IP=10.0.1.99|" .env   # eigene Adresse einsetzen
```

**Zugang zur Oberfläche: ein Passwort, nicht `LOXMATTER_API_TOKEN`.** Seit
dem WebUI-Login (Task 9/10, Phase 6) vergibt man beim ersten Öffnen von
`http://10.0.1.56:8080/` im Browser ein Passwort — bis das geschehen ist,
liefert **keine** `/api`-Route irgendetwas aus (HTTP 401), unabhängig davon,
ob `LOXMATTER_API_TOKEN` gesetzt ist. Das gilt auch für die
Fabric-Sicherung (`GET /api/diagnostics/fabric-backup`): dieser Stack läuft
mit `network_mode: host` und hängt das matter-server-Datenverzeichnis in den
loxmatter-Dienst ein, aber ein fehlendes Token ist dafür seit dem
WebUI-Login nicht mehr die entscheidende Bedingung — ohne vergebenes
Passwort antwortet ohnehin keine `/api`-Route, diese eingeschlossen; der
frühere eigene 403-Zweig für „kein Token gesetzt" ist damit entfallen.

`LOXMATTER_API_TOKEN` bleibt trotzdem sinnvoll gesetzt, wenn diese Instanz
auch per Skript oder `curl` angesprochen werden soll — für den Browser
selbst wird es nicht mehr gebraucht. `openssl rand -hex 32` ist der
empfohlene Weg dazu — es muss in einem HTTP-Header und in einem
WebSocket-Subprotokoll übertragbar sein, also keine Leerzeichen, kein
Komma, ASCII; `openssl rand -hex 32` liefert nur `[0-9a-f]`.

**Passwort vergessen — der Notausgang.** `loxmatter set-password` setzt es
neu und meldet dabei alle offenen Sitzungen ab. Auf DIESEM Stack **im
laufenden Container** ausführen, nicht auf dem Pi selbst:

```bash
docker compose exec loxmatter loxmatter set-password
```

Der Grund, warum `uv run loxmatter set-password` auf dem Pi hier NICHT
funktioniert: die Datenbank liegt im benannten Docker-Volume
`loxmatter-store` (siehe `docker-compose.yml`, `LOXMATTER_STORE:
/data/loxmatter.sqlite`) — dieser Pfad existiert nur *innerhalb* des
Containers. Auf dem Host fehlt diese Umgebungsvariable, `set-password`
träfe dort ersatzweise den Nutzer-Home-Standard
(`~/.loxmatter/loxmatter.sqlite`), also eine andere, leere Datenbank —
ohne diesen Notausgang-Fund hätte der Befehl das kommentarlos angelegt und
Erfolg gemeldet, während die eigentliche Brücke unverändert gesperrt
bliebe. Er bricht deshalb mit einem klaren Fehler ab, wenn die angegebene
Datenbank nicht existiert, statt eine neue anzulegen.

## WiFi/Ethernet-only (ohne Thread-Funkmodul)

Der `otbr`-Dienst steht seit dem 5. September 2026 hinter dem Compose-Profil
`thread`. Wer nur WLAN- oder Ethernet-Matter-Geräte anbinden will, lässt
`COMPOSE_PROFILES` in der `.env` leer — dann wird der Border Router gar nicht
erzeugt, und `RADIO_DEVICE`, `RADIO_BAUDRATE` und `BACKBONE_IF` bleiben
wirkungslos.

**Warum das nötig war:** `otbr` reicht mit `devices: -
${RADIO_DEVICE}:${RADIO_DEVICE}` ein echtes Gerät durch. Steckt kein Funkmodul,
scheitert `docker compose up` mit „error gathering device information" — und
zwar für den *gesamten* Stack, auch für die beiden Dienste, die das Modul nie
gebraucht hätten.

**BLE bleibt in beiden Betriebsarten nötig.** Auch ein WLAN-Matter-Gerät wird
über Bluetooth eingelernt; `BLUETOOTH_ADAPTER` und der rfkill-Abschnitt weiter
unten gelten unverändert.

**Nachrüsten:** Funkmodul stecken, in der `.env` `COMPOSE_PROFILES=thread`
setzen und `RADIO_DEVICE` auf den richtigen Pfad, dann `docker compose up -d`.
Der `start-stop-daemon`-Workaround weiter unten wird ab dann wieder gebraucht.

## Aktualisieren

Auf dem Rechner, auf dem die Brücke läuft:

```bash
cd ~/matter-loxone && ./scripts/update.sh
```

Holt den neuesten Stand, baut das Image, startet den Dienst neu. Nur bauen
und neu starten, ohne zu holen: `./scripts/update.sh --no-pull`.

Das Skript findet den Stack über seinen eigenen Pfad — es gibt nichts zu
konfigurieren, solange es aus dem Repository heraus läuft.

Was es zusichert:

- **Es sichert die Signaldatenbank, bevor es irgendetwas ändert.** Darin
  stehen die Signalschlüssel, und die sind die Verdrahtung in der
  Loxone-Konfiguration — das Einzige, was ein misslungenes Update nicht
  wiederherstellen könnte. Die Sicherungen liegen unter
  `~/loxmatter-backups/`, die letzten zehn bleiben.
- **matter-server und OTBR bleiben unangetastet** (`docker compose up
  --no-deps`). Ohne das erzeugt Compose sie mit neu, sobald sich die
  Projektkonfiguration geändert hat; OTBRs Thread-Zustand übersteht das zwar
  (er liegt im Volume `otbr-state`), aber ein Neustart des Thread-Netzes
  ohne Grund gehört nicht zu einem Update.
- **Es bricht ab, bevor es schadet.** Schlägt die Sicherung oder der Build
  fehl, läuft der alte Dienst unverändert weiter. Antwortet `/health` nach
  dem Neustart nicht innerhalb von 20 Sekunden, zeigt es die letzten
  Logzeilen und meldet einen Fehlschlag statt Erfolg.
- Am Ende sagt es, aus welchem Commit es ausgeliefert hat und wie viele
  Signale je Gerät exportiert werden.

## BLE aktivieren

Das ist der eigentliche Zweck des Umzugs. `python-matter-server` nutzt Bluetooth nur,
wenn es explizit angewiesen wird — ungefragt bleibt BLE-Commissioning aus, auch wenn
ein Adapter vorhanden ist. Der Options-Name kommt aus dem Image selbst, nicht aus
Vermutung:

```
$ docker run --rm ghcr.io/home-assistant-libs/python-matter-server:stable --help
  --bluetooth-adapter BLUETOOTH_ADAPTER
                        Optional bluetooth adapter (id) to enable direct
                        commisisoning support.
```

`hci0` ist der einzige Adapter auf dem Pi → `--bluetooth-adapter 0`. Das Image-Default-
`CMD` ist `--storage-path /data --paa-root-cert-dir /data/credentials` (per `docker
inspect --format '{{.Config.Cmd}}'` geprüft); `command:` in Compose überschreibt das
CMD vollständig, deshalb übernimmt die Compose-Datei diese beiden Argumente und ergänzt
`--bluetooth-adapter ${BLUETOOTH_ADAPTER}`. Die Adapter-ID ist wie `RADIO_DEVICE`,
`RADIO_BAUDRATE` und `BACKBONE_IF` über `.env` konfigurierbar (`BLUETOOTH_ADAPTER`,
Default `0` in `docker-compose.yml` falls `.env` die Variable nicht setzt) statt im
Compose-File hartkodiert — auf einem anderen Host mit mehreren Adaptern kann `hci0`
eine andere ID haben.

**Verifikation, dass der Adapter tatsächlich ankommt — nicht optional:** Ein Stack
ohne BLE sieht in den Logs und im WebSocket-Verhalten identisch aus wie einer mit BLE,
bis eine Kommissionierung fehlschlägt. Der Code
(`matter_server/server/stack.py`, `MatterStack.__init__`) loggt das explizit, aber nur
auf `DEBUG`:

```python
self.logger.debug(
    "Using storage file: %s - Bluetooth commissioning enabled: %s",
    storage_file,
    "NO" if bluetooth_adapter_id is None else f"YES (adapter {bluetooth_adapter_id})",
)
```

Der Standard-Log-Level ist `info` (`--log-level`, Default laut `--help`) — die Zeile
erscheint im normalen Betrieb **nicht**. Verifiziert per einmaligem Lauf mit
`--log-level debug` gegen dasselbe `./data`-Verzeichnis (Compose-Service vorher
gestoppt, danach normal mit `docker compose up -d matter-server` wieder gestartet —
kein `--log-level debug` im Dauerbetrieb, das wäre zu geschwätzig):

```
$ docker compose stop matter-server
$ docker run --rm --network host --security-opt apparmor=unconfined \
    -v $PWD/data:/data -v /run/dbus:/run/dbus:ro \
    ghcr.io/home-assistant-libs/python-matter-server:stable \
    --storage-path /data --paa-root-cert-dir /data/credentials \
    --bluetooth-adapter 0 --log-level debug
...
2026-09-01 21:14:05.275 (MainThread) DEBUG [matter_server.server.stack]
  Using storage file: /data/chip.json - Bluetooth commissioning enabled: YES (adapter 0)
```

Das ist der Beweis: `bluetooth_adapter_id` kommt als `0` im Stack an, nicht `None`
(`None` würde `chip.native.Init(999)` aufrufen — der Code kommentiert das selbst:
"give the fake adapter id of 999 to disable bluetooth"). Task 7 kann also tatsächlich
über BLE kommissionieren, sofern der Adapter zum Zeitpunkt des Verbindungsversuchs
`UP`/`Powered` ist (siehe naechster Abschnitt — das ist getrennt von dieser Prüfung).

## Bluetooth-Adapter ist rfkill-soft-blocked (neu gegenüber der VM)

`hci0` stand beim Erheben der Zielumgebung auf `DOWN`. Das war zunächst nicht weiter
beunruhigend — die Annahme war, `bluetoothd`/`matter-server` bringt den Adapter selbst
hoch. Das stimmt nur teilweise:

```
$ bluetoothctl show
Powered: no
PowerState: off-blocked
```

`off-blocked` heißt: rfkill hat den Adapter **soft-blockiert**, nicht nur
heruntergefahren. `bluetoothctl power on` ändert daran nichts — bluetoothd verweigert
das Power-on, solange der rfkill-Block steht. Das ist unabhängig von `matter-server`;
selbst wenn `python-matter-server`/`bleak` beim Start versucht, den Adapter per D-Bus
zu powern, träfe es auf dasselbe Verbot. **`matter-server` bringt `hci0` also nicht
selbst hoch, wenn es rfkill-blockiert ist — das musste vorher geklärt werden, nicht
nach einem fehlgeschlagenen Commissioning-Versuch entdeckt werden.**

```
$ cat /sys/class/rfkill/rfkill0/name /sys/class/rfkill/rfkill0/type /sys/class/rfkill/rfkill0/soft /sys/class/rfkill/rfkill0/hard
hci0
bluetooth
1        # soft-blockiert
0        # kein Hardware-Kill-Switch
```

`/dev/rfkill` und `/sys/class/rfkill/rfkill0/soft` gehören `root:root`, `pi` ist in
keiner Gruppe, die Schreibzugriff hätte (`id -nG` zeigt u. a. `netdev`, `gpio`,
`i2c`, `spi`, `docker` — keine reicht). Das Entsperren braucht also Root-Rechte, die
laut Auftrag nicht per `sudo` beschafft werden dürfen. Der Ausweg: `pi` ist in der
`docker`-Gruppe, und der Docker-Daemon läuft als root — ein privilegierter Container
kann `/sys/class/rfkill/rfkill0/soft` beschreiben, ohne dass am SSH-Prompt je `sudo`
aufgerufen wird:

```bash
docker run --rm --privileged -v /sys:/sys alpine \
  sh -c 'echo 0 > /sys/class/rfkill/rfkill0/soft'
```

Danach:

```
$ bluetoothctl show
Powered: yes
PowerState: on
$ hciconfig hci0
hci0: ... UP RUNNING
```

Der Soft-Block war ein einmaliger Werkszustand, kein wiederkehrender: Am 2026-09-01
wurde das mit einem echten Reboot geprüft. Nach dem Neustart meldete
`rfkill list bluetooth` weiterhin `Soft blocked: no`, und `hci0` war ohne Zutun
`UP RUNNING` — der Unblock hält über Reboots hinweg. Dieser Schritt gehört also nur in
die Ersteinrichtung, nicht vor jeden `docker compose up -d`; ob er nötig ist, zeigt
`rfkill list bluetooth` (siehe Schritt 2 oben).

## OTBR-Wächter einrichten

Der OTBR-Agent bricht ab, wenn das Funkmodul nicht mehr antwortet — ein
RCP-Timeout, meist USB-Aussetzer oder Stromversorgung. Der **Container** läuft
dabei weiter, weil sein Einstiegsskript nicht der Agent ist; `restart:
unless-stopped` greift deshalb nicht, und das Image bringt keinen Aufpasser
mit.

Am 3. September 2026 blieb ein solcher Ausfall sechseinhalb Stunden unbemerkt.
Kein Gerät war in dieser Zeit erreichbar. Die letzten Zeilen des Agenten vor
seinem Abbruch:

```
[W] P-RadioSpinel-: radio tx timeout
[C] P-RadioSpinel-: Failed to communicate with RCP - no response from RCP during initialization
[C] Platform------: HandleRcpTimeout() at radio_spinel.cpp:2054: RadioSpinelNoResponse
```

Einrichten mit `crontab -e` und dieser Zeile:

```
*/5 * * * * /home/pi/matter-loxone/scripts/otbr-watchdog.sh >> /home/pi/otbr-watchdog.log 2>&1
```

Das Skript prüft, ob eine Thread-Schnittstelle (`wpan*`) mit einer
Mesh-Adresse existiert — dieselbe Prüfung, die auch die Ansicht „System"
anzeigt. Fehlt sie, startet es den `otbr`-Dienst neu und wartet bis zu 60
Sekunden auf das Netz. Solange alles läuft, schreibt es nichts; die Logdatei
enthält also genau die Vorfälle.

**Es startet bewusst nicht in Schleife neu.** Hängt das Funkmodul selbst,
brächte ein Neustart im Minutentakt nichts und flutete nur das Log. Dann muss
jemand hinsehen — und findet im Log, was war, samt der letzten Zeilen aus dem
OTBR-Log.

Zusätzlich lohnt eine Meldung in Loxone: `d<n>_online` geht bei einem solchen
Ausfall auf 0, und dieser Wert liegt ohnehin schon im Miniserver.

## start-stop-daemon haengt auf dem Pi-Kernel (neu gegenüber der VM)

Das OTBR-"test"-Image (siehe "Historie: die VM" für die Image-Variante) startet
`otbr-agent`, `otbr-web` und `rsyslog` intern über sysvinit-Skripte, die
`start-stop-daemon --background --make-pidfile` benutzen. Auf dem Pi hängt dieser aus
2018 stammende Ubuntu-18.04-Unterbau (`dpkg`/`start-stop-daemon` 1.19.0.5, Image-Basis
laut `/etc/os-release`) gegen den sehr neuen Kernel (`6.18.34+rpt-rpi-v8`, PREEMPT,
Build vom 2026-06-09) endlos in seiner Fork-Erkennungsschleife:

```
$ docker exec otbr ps aux | grep otbr
root  84  99.1  ...  start-stop-daemon --start --quiet --pidfile /var/run/otbr-agent.pid \
                      --make-pidfile -b --exec /usr/sbin/otbr-agent -- -I wpan0 -B wlan0 ...
```

Der Log zeigt vorher brav `Starting thread border agent otbr-agent ... done.` — die
Meldung lügt: `/proc/84/comm` bleibt `start-stop-daem`, nie `otbr-agent`. Der
eigentliche Prozess wurde nie exec't; PID 84 (aus der Pidfile) ist der Wrapper selbst,
`R`-State, 0 Syscalls in Bearbeitung (`/proc/84/syscall` → `running`,
`/proc/84/wchan` → `0`) — ein reiner Busy-Loop, kein Warten auf I/O. `rsyslog` hängt
kurzzeitig im selben Muster, kommt aber irgendwann durch; `otbr-agent`/`otbr-web` nie,
in mehreren Minuten Wartezeit beobachtet. Ohne laufenden `otbr-agent` bleibt
`ot-ctl state` mit `connect session failed: No such file or directory` hängen — der
zugehörige Steuerkanal existiert schlicht nicht.

**Workaround, verifiziert funktionsfähig:** die gehängten Wrapper killen und die
Binaries direkt starten, mit denselben Argumenten, die aus der Pidfile/`ps`-Ausgabe
des hängenden Wrappers ablesbar sind:

```bash
docker exec otbr sh -c 'kill -9 $(cat /var/run/otbr-agent.pid) $(cat /var/run/otbr-web.pid)'
docker exec -d otbr /usr/sbin/otbr-agent -I wpan0 -B wlan0 -d7 \
  --rest-listen-address 127.0.0.1 \
  spinel+hdlc+uart:///dev/ttyUSB0?uart-baudrate=460800
docker exec -d otbr /usr/sbin/otbr-web -I wpan0 -d7 -a 127.0.0.1 -p 80
```

Danach verbindet sich `ot-ctl` normal, das Thread-Netz lässt sich wie gewohnt bilden.

**Nach einem Reboot des Pi ist die Wiederherstellung länger — vier Schritte statt zwei.**
Am 2026-09-01 gemessen: der Container startet automatisch wieder, aber `otbr-agent` läuft
darin gar nicht, und es gibt nichts zu killen — nur verwaiste PID-Dateien von vor dem
Reboot. Der Thread-Datensatz selbst überlebt (Ext PAN ID unverändert), muss also nicht neu
angelegt werden. Die Schnittstelle ist aber `detached` und muss neu gestartet werden:

```bash
docker exec otbr sh -c 'rm -f /var/run/otbr-agent.pid /var/run/otbr-web.pid'
docker exec -d otbr /usr/sbin/otbr-agent -I wpan0 -B wlan0 -d7 \
  --rest-listen-address 127.0.0.1 \
  spinel+hdlc+uart:///dev/ttyUSB0?uart-baudrate=460800
docker exec otbr ot-ctl ifconfig up
docker exec otbr ot-ctl thread start
```

Der Zustand geht danach von `detached` nach `leader`, gemessen nach rund 15 Sekunden.
`docker exec otbr ot-ctl state` erst danach prüfen, sonst sieht man `detached` und hält
es für einen Fehler.
`otbr-web` (REST-API auf Port 80, intern) wird von `matter-server`/`ot-ctl` nicht
gebraucht — es läuft nur der Vollständigkeit halber mit, falls es später zum
Debuggen nützlich ist.

**Anders als der rfkill-Fix ist das kein dauerhafter Zustand** — er muss nach jedem
`docker compose up`/Neustart des `otbr`-Containers erneut angewendet werden, bis das
Image selbst ersetzt wird (z. B. durch die neuere s6-overlay-"border-router"-Variante,
die keinen sysvinit/`start-stop-daemon`-Unterbau hat — siehe Historie unten,
Abweichung 1). Für Phase 6 ist das der klare nächste Schritt, nicht diese Task, die
nur eine Testumgebung braucht, die zuverlässig genug für Task 7 läuft.

## `wlan0` statt `ens18`/Kabel-Interface

Der Pi hat kein Ethernet-Kabel gesteckt (`eth0` zeigt `NO-CARRIER`) — `wlan0` ist das
einzige Interface mit tatsächlicher Verbindung ins LAN und deshalb das
Backbone-Interface für OTBR (`BACKBONE_IF=wlan0` in `.env.example`, per
`--backbone-interface` an den OTBR-Container durchgereicht). Funktional identisch zur
Rolle von `ens18` auf der VM — der einzige Unterschied ist der Name und dass es WLAN
statt Kabel ist; für Thread-Routing über `wpan0` spielt das keine Rolle (siehe
"Bekannte Einschränkungen" unten, IPv6-Punkt, der unverändert von der VM gilt).

## Baudrate

**460800**, unverändert von der VM übernommen — derselbe SONOFF Dongle Plus MG24 mit
derselben Firmware wurde einfach umgesteckt. Funktionierte auf Anhieb: RCP antwortet
auf Spinel-Anfragen (siehe "Stand: was laeuft"), Thread-Netz bildet sich, `ot-ctl
state` liefert `leader`. Kein zweiter Versuch mit 115200 nötig.

## Fabric-Volume sichern (`./data`)

`matter-server` legt Fabric-/Node-Zustand unter `~/matter-loxone/deploy/testhost/data` ab
(`chip.json`, `chip_*.ini`, `credentials/`, sowie eine `<NodeID>.json` pro
committetem Node). Sicherung vom Pi:

```bash
ssh pi@10.0.1.56 'tar czf - -C ~/matter-loxone/deploy/testhost data' > matter-server-data-backup.tar.gz
```

Rueckspielen (Container vorher stoppen):

```bash
ssh pi@10.0.1.56 'cd ~/matter-loxone/deploy/testhost && docker compose stop matter-server'
cat matter-server-data-backup.tar.gz | ssh pi@10.0.1.56 'tar xzf - -C ~/matter-loxone/deploy/testhost'
ssh pi@10.0.1.56 'cd ~/matter-loxone/deploy/testhost && docker compose start matter-server'
```

## Thread-Datensatz — NICHT ins Repository

`docker exec otbr ot-ctl dataset active -x` gibt den aktiven Thread-Operational-Dataset
aus (hex-kodiert). Das ist ein Netzwerk-Credential (enthaelt u.a. den Network Key) —
wer ihn hat, kann dem Thread-Netz beitreten. Er gehoert **nicht** ins Repository und
nicht unter `deploy/`.

Abgelegt auf dem Pi unter `~/matter-loxone/deploy/testhost/thread-dataset.txt` (Modus `600`,
nur fuer den Betreiber lesbar). `deploy/testhost/.gitignore` schliesst zusaetzlich
`.env`, `data/` und alles, was wie ein Dataset benannt ist
(`*.dataset`, `thread-dataset*`), von Commits aus, falls jemand versehentlich in
diesem Verzeichnis arbeitet.

Erneut abrufen: `ssh pi@10.0.1.56 docker exec otbr ot-ctl dataset active -x`

Fuer Task 7 (Einlernen der IKEA-Geraete) wird dieser Datensatz gebraucht — jetzt
tatsächlich per BLE, nicht nur über das Thread-Netz.

## Bekannte Einschraenkungen (bewusst, fuer eine Testumgebung)

- Keine legacy-Firewall auf `wpan0` (siehe Historie, VM-Abweichung 2) — kein
  Hardening-Ziel dieser Task.
- Kein globales IPv6 auf `wlan0` (nur link-local) — für Thread-Geräte unkritisch, wie
  schon auf der VM: OTBR spannt auf `wpan0` ein eigenes ULA-Präfix auf, und
  `matter-server` läuft mit `network_mode: host` daneben und erreicht die Geräte über
  die Route dorthin. Erst Matter-über-WLAN-Geräte bräuchten globales IPv6 im LAN.
- Der `start-stop-daemon`-Workaround (siehe oben) ist
  **nicht persistent** — nach einem Neustart des Pi bzw. des `otbr`-Containers muss
  er erneut angewendet werden. Für eine Testumgebung akzeptabel, für Phase 6 nicht.

## Historie: die VM

Der Host lief ursprünglich auf `lucienkerl@10.0.1.215` (Ubuntu 26.04 LTS,
Backbone-Interface `ens18`). Abgebaut, weil dort **kein Bluetooth-Adapter** vorhanden
war (`ls /sys/class/bluetooth` lieferte nichts, `bluetooth.service` war inaktiv) —
Matter-Commissioning läuft über BLE, ohne Adapter war dort also nie ein Gerät
einlernbar. Die Container wurden mit `docker compose down` in `~/loxmatter-testvm/`
gestoppt; die Dateien und das gesicherte Dataset liegen dort unverändert, falls sie
später gebraucht werden.

Der Brief war ein Ausgangspunkt, kein verifizierter Endzustand — beide im Brief
genannten Unklarheiten (OTBR-Aufruf, Baudrate) mussten tatsaechlich geprueft werden.
Diese Funde gelten unveraendert fuer den Pi (derselbe Dongle, dieselbe Firmware,
dasselbe Image):

### 1. OTBR-Image ist die "test"-Variante, nicht "border-router"

`openthread/otbr:latest` (Docker Hub, Digest zum Zeitpunkt des VM-Deployments
`sha256:ebebd9f643f0fadf60a9e46a1c81b4f4c9f320f04863e69ed95d8fde6b5de5a6`) hat als
Entrypoint `/app/etc/docker/test/docker_entrypoint.sh` — das ist die aeltere,
"test"-Docker-Variante aus dem `ot-br-posix`-Repo, nicht die neuere
s6-overlay-basierte `border-router`-Variante (die andere Umgebungsvariablen wie
`OT_RCP_DEVICE`/`OT_INFRA_IF` erwartet und `command:`-Overrides ignoriert). Das war
vorab nicht offensichtlich — der Quellcode auf `main` im GitHub-Repo zeigt die neuere
Variante; welche davon `:latest` auf Docker Hub tatsaechlich ist, war nur per
`docker inspect --format '{{.Config.Entrypoint}}'` am gezogenen Image zu klaeren.
Die Compose-Syntax (`RADIO_URL` als Env-Var, `--backbone-interface` als
Kommandozeilenargument) passt zu dieser Variante und funktioniert unveraendert.

Auf dem Pi zeigte sich zusätzlich, dass genau diese "test"-Variante auf einem sehr
neuen Kernel unzuverlässig ist (`start-stop-daemon`, siehe oben) — ein weiterer Grund,
in Phase 6 zur `border-router`-Variante zu wechseln.

### 2. NAT64/legacy-Firewall-Setup schlaegt fehl → per Env-Var deaktiviert

Beim ersten Start auf der VM crashte der `otbr`-Container:

```
iptables v1.6.1: can't initialize iptables table `mangle': Table does not exist ...
iptables v1.6.1: can't initialize iptables table `nat': Table does not exist ...
iptables v1.6.1: can't initialize iptables table `filter': Table does not exist ...
 *** ERROR:  Failed to start NAT44!
```

Der Container hat kein `modprobe` (der vorgelagerte `sudo modprobe ip6table_filter`
schlaegt schon mit "command not found" fehl, wird aber ignoriert), kann die
legacy-iptables-Kernel-Tabellen (`mangle`/`nat`/`filter`) also nicht selbst
nachladen. Das Entrypoint-Skript (`/app/script/_nat64`, `_firewall`) prueft
vor dem NAT64/NAT44- bzw. Firewall-Setup jeweils die Env-Variablen `NAT64` bzw.
`FIREWALL` (Default beide `1` im Image). Beide auf `"0"` gesetzt (siehe
`docker-compose.yml`, Kommentar dort) übersprang diesen Teil vollständig, danach
startete `otbr-agent` sauber durch.

Auf dem Pi **derselbe Befund von Anfang an übernommen** (`NAT64: "0"`, `FIREWALL:
"0"` waren schon in der von der VM kopierten Compose-Datei) — dort trat der Crash
deshalb gar nicht erst auf; der Log zeigt lediglich denselben harmlosen
`sudo: modprobe: command not found`-Hinweis wie auf der VM. Der Pi hat wie die VM
keine geladenen iptables-Module (siehe Task-Vorgabe) — dieselbe Ursache, derselbe
Fix, präventiv angewendet statt erneut zum Absturz gebracht.

Für die Testumgebung unkritisch: NAT64/NAT44 übersetzt Thread-IPv6 auf IPv4-Hosts
im LAN — hier nicht gebraucht, weder `matter-server` noch `loxmatter` müssen aus dem
Thread-Netz heraus IPv4-Ziele erreichen. Die legacy-Firewall haette Ingress-Filterung
fuer `wpan0` eingerichtet; ohne sie ist der Container offener als in Phase 6
vertretbar waere — das gehoert dort ins Hardening.

Kleinere Randnotiz aus dem Log, ebenfalls durch `FIREWALL=0`/fehlendes `modprobe`
bedingt und ohne Auswirkung auf den Betrieb (auf dem Pi identisch beobachtet):

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

## Dateien in diesem Verzeichnis

- `docker-compose.yml` — Compose-Definition. Der Pi benutzt seit dem
  3. September 2026 **diese Datei selbst**, aus einem Git-Checkout unter
  `~/matter-loxone` — keine Kopie mehr, die auseinanderlaufen kann.
- `.env.example` — Vorlage fuer `.env` auf dem Pi.
- `.gitignore` — verhindert versehentliches Commit von `.env`, `data/` und
  Dataset-Dateien, falls diese jemals lokal in diesem Repo-Pfad angelegt werden.
