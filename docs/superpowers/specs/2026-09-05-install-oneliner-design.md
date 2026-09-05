# One-Liner-Installskript: `install.sh`

Entwurf, 5. September 2026. Beschreibt ein Skript, das eine loxmatter-Installation
auf einen einzigen Befehl reduziert:

```
curl -fsSL https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh | sh
```

Knüpft an [`deploy/testhost/README.md`](../../../deploy/testhost/README.md) an —
dort steht der manuelle Weg, den dieses Skript zusammenfasst — und an
[`scripts/update.sh`](../../../scripts/update.sh), dessen Stil und Aufgabenteilung
es übernimmt. Der Quickstart-Abschnitt aus
[dem README-Produktseiten-Entwurf](2026-09-05-readme-produktseite-design.md)
wird von diesem Entwurf beliefert, siehe Abschnitt 10.

## 1. Das Problem

Eine Installation sind heute acht Schritte über zwei Dokumente verteilt:
klonen, `.env` aus der Vorlage anlegen, vier Werte darin von Hand setzen,
`mkdir -p data`, `docker compose up -d --build` — und auf einem Raspberry Pi
danach noch der rfkill-Unblock und der `start-stop-daemon`-Workaround aus
[`deploy/testhost/README.md`](../../../deploy/testhost/README.md), ohne die
weder BLE-Einlernen noch das Thread-Netz funktionieren.

Wer den `MINISERVER_IP`-Schritt überspringt, bekommt einen laufenden Stack,
der nichts an den Miniserver schickt. Wer den Thread-Workaround nicht kennt,
bekommt einen Stack, der aussieht wie ein gesunder und keine Geräte findet.
Beide Fehler zeigen sich erst Stunden später.

## 2. Abgestimmte Entscheidungen

| Frage | Entscheidung | Grund |
|---|---|---|
| Installweg | **Nur der Docker-Stack** (`deploy/testhost/`) | Die Zielausgabe „Passwort vergeben, WebUI öffnen" ist nur mit laufendem Dienst erreichbar, und die WebUI braucht einen erreichbaren `matter-server`. Der reine CLI-Weg über `uv` erreicht diesen Zustand nicht und ist ohnehin drei Zeilen. |
| Host-Eingriff | **Installieren, prüfen, berichten** | Der rfkill-Fix braucht faktisch root, der otbr-Workaround ist kernelspezifisch und würde auf gesunden Hosts laufende Prozesse killen. Stilles Gelingen bei totem Thread-Netz wäre das schlechteste Ergebnis — deshalb wird geprüft und benannt, aber nicht heimlich repariert. |
| Konfiguration | **Interaktiv über `/dev/tty`**, Env-Variablen überschreiben | `stdin` ist im `curl \| sh`-Fall die Pipe. Erkennbare Werte werden vorgeschlagen, `MINISERVER_IP` ist nicht erkennbar und wird gefragt. |
| Fehlende Basiswerkzeuge | **`git`, `curl`, `openssl` werden nachinstalliert** | Sonst scheitert der One-Liner auf einem frischen Host an einer Kleinigkeit. |
| Fehlendes Docker | **Wird ungefragt nachinstalliert**, aber angekündigt | Bewusste Entscheidung des Auftraggebers. „Nicht fragen" heißt nicht „nicht sagen": das Skript nennt Schritt und Quelle, hält aber nicht an. |
| Paketverwaltung | **Nur `apt-get`** | Debian, Ubuntu, Raspberry Pi OS sind die dokumentierten Zielhosts. Ungetestete Paketmanager-Zweige sind genau der Abbruch mittendrin, den dieser Entwurf ausschließt. |
| Zweiter Lauf | **Prüfen und geradeziehen**, Update nur nach Zustimmung | `scripts/update.sh` sichert vorher die Signaldatenbank; ein Installskript, das nebenbei aktualisiert, umginge diese Sicherung. Also wird gefragt und an `update.sh` delegiert. |
| Sprache | **Durchgehend Englisch**, auch die Kommentare | Abweichung von der Projektkonvention (deutsche Code-Kommentare), bewusst: der One-Liner ist der erste Kontakt mit dem Projekt und steht in einer englischen README. |
| Absicherung | `shellcheck` in der CI, `--dry-run`, Tests mit gefälschten Binaries | Idempotenz und „sauberer Abbruch statt mittendrin" sollen geprüft sein, nicht behauptet. |

## 3. Form

**Ort:** `install.sh` im Wurzelverzeichnis des Repositories, passend zur Ziel-URL
`…/main/install.sh`. GPL-Kopf wie die übrigen Skripte.

**Reines POSIX `sh`, kein Bash.** Der One-Liner endet auf `| sh`, und auf
Raspberry Pi OS ist `/bin/sh` dash. Ein Bash-Skript, das per `sh` gepipet wird,
bricht an der ersten `[[`-Zeile ab. Das kostet Arrays und `set -o pipefail`;
dafür läuft es überall, wo der One-Liner hinzeigt. Geprüft wird mit
`shellcheck -s sh`, nicht mit dem Bash-Dialekt.

**Alles in Funktionen, `main "$@"` in der letzten Zeile.** Bricht die
Übertragung mitten im Download ab, führt `sh` ein halbes Skript aus. Mit diesem
Muster definiert ein abgeschnittenes Skript nur Funktionen und tut nichts —
ohne das ist eine unterbrochene Leitung ein halb installierter Host.

**Aufrufformen:**

```
curl -fsSL .../install.sh | sh                     # der One-Liner
curl -fsSL .../install.sh | sh -s -- --dry-run     # zeigt alles, ändert nichts
sh install.sh --dir /srv/loxmatter                 # heruntergeladen und gelesen
```

Flags: `--dry-run`, `--dir <pfad>`, `--help`. Umgebungsvariablen, die eine
Rückfrage überspringen: `LOXMATTER_DIR`, `MINISERVER_IP`, `RADIO_DEVICE`,
`RADIO_BAUDRATE`, `BACKBONE_IF`, `BLUETOOTH_ADAPTER`, `LOXMATTER_API_TOKEN`.

## 4. Ablauf

### Phase 1 — Prüfen, bevor irgendetwas verändert wird

Alles, was scheitern kann, scheitert hier. Diese Phase legt keine Datei an,
installiert kein Paket und startet keinen Container.

- **Linux?** Auf macOS/BSD sofortiger Abbruch mit Verweis auf den
  Entwicklerweg (`uv sync`). Begründung im Text: `network_mode: host`,
  `/dev/ttyUSB*`, `/run/dbus` und rfkill gibt es dort nicht.
- **Architektur** in `aarch64|arm64|x86_64|amd64`? Sonst Abbruch, mit dem
  tatsächlich erkannten Wert in der Meldung.
- **Fehlendes einsammeln, nicht beim ersten Treffer abbrechen:** `git`,
  `curl`, `openssl`, `docker`, `docker compose`. Das Ergebnis ist eine Liste.
- **Kann das Fehlende behoben werden?** Nur wenn es etwas zu installieren gibt:
  root oder `sudo` vorhanden, und `apt-get` vorhanden. Sonst Abbruch mit der
  **vollständigen** Paketliste, nicht nur dem ersten fehlenden Werkzeug.
- **Läuft das Skript als root?** Warnung, kein Abbruch: der Klon und
  `~/loxmatter-backups` gehören danach root, und `scripts/update.sh` läuft
  später nur noch als root.
- **Konfiguration beschaffbar?** Kein `/dev/tty` (nicht-interaktiver Lauf) und
  `MINISERVER_IP` weder gesetzt noch in einer vorhandenen `.env` — Abbruch,
  mit der Zeile zum Nachbessern (`curl … | MINISERVER_IP=10.0.1.99 sh`).
- **Zielverzeichnis** anlegbar bzw. vorhanden und beschreibbar.

### Phase 2 — Nachinstallieren, falls nötig

Zweistufig, in dieser Reihenfolge, weil `get.docker.com` selbst `curl` braucht:

1. Basispakete: `apt-get update`, dann
   `DEBIAN_FRONTEND=noninteractive apt-get install -y` mit **genau** den
   fehlenden aus `git curl openssl` — nichts darüber hinaus.
2. Docker: angekündigt („Docker is not installed. Installing it from
   https://get.docker.com — this requires sudo."), dann
   `curl -fsSL https://get.docker.com | sh`, dann `usermod -aG docker <user>`.

Hat das Skript Docker in diesem Lauf selbst installiert, benutzt es für den
Rest **dieses einen Laufs** `sudo docker` — die neue Gruppenmitgliedschaft
greift erst nach einer Neuanmeldung. Der Schlussbericht sagt das: einmal ab-
und wieder anmelden, danach geht `docker` ohne `sudo`, und `scripts/update.sh`
braucht das.

### Phase 3 — Klon

`git clone https://github.com/lucienkerl/loxmatter.git ~/loxmatter`, Branch
`main` (Tags gibt es nicht). Über HTTPS, nicht SSH — auf einem frischen Host
liegt kein Schlüssel.

Existiert das Verzeichnis bereits: **nicht klonen, nicht ziehen.** Es wird nur
geprüft, dass es ein loxmatter-Checkout ist (`Dockerfile` und
`deploy/testhost/docker-compose.yml` vorhanden, wie `update.sh` es prüft), sonst
Abbruch. Das Aktualisieren ist Sache von Phase 6.

### Phase 4 — Konfiguration

`.env` aus `.env.example` anlegen, falls sie fehlt. Dann jeder Wert einzeln:

| Variable | Erkennung | Rückfrage |
|---|---|---|
| `BACKBONE_IF` | `ip route show default` → Feld nach `dev` | mit Vorschlag |
| `RADIO_DEVICE` | erstes `/dev/ttyUSB*`, sonst erstes `/dev/ttyACM*` | mit Vorschlag; leer nicht erlaubt, siehe unten |
| `RADIO_BAUDRATE` | Vorgabe `460800` aus `.env.example` | keine |
| `BLUETOOTH_ADAPTER` | erstes `hci<N>` aus `/sys/class/bluetooth` → `<N>` | mit Vorschlag |
| `MINISERVER_IP` | nicht erkennbar (das Projekt kennt keine Miniserver-Suche) | Pflichtfrage, IPv4-Format wird geprüft |
| `LOXMATTER_API_TOKEN` | `openssl rand -hex 32`, Rückfall `od -An -tx1 -N32 /dev/urandom \| tr -d ' \n'` | keine |

Regeln, die für jeden Wert gelten:

- **Bestehende `.env`-Werte werden nie überschrieben.** Nur fehlende oder leere
  Schlüssel werden gefüllt. Wer beim zweiten Lauf eine angepasste `.env` hat,
  behält sie.
- **Zeilenersetzung, kein Anhängen.** Eine zweite Definition derselben Variablen
  wäre zwar wirksam (Compose nimmt die letzte), aber wer die Datei später
  bearbeitet, ändert dann die falsche Zeile — die Begründung steht so schon in
  [`deploy/testhost/README.md`](../../../deploy/testhost/README.md).
- Eine gesetzte Umgebungsvariable überspringt die zugehörige Rückfrage.
- Fragen laufen über `/dev/tty`, weil `stdin` die Pipe ist.

**`RADIO_DEVICE` darf nicht leer bleiben.** Die Compose-Datei reicht das Gerät
als `devices: - ${RADIO_DEVICE}:${RADIO_DEVICE}` durch; ein leerer Wert ergibt
`- :` und lässt `docker compose up` scheitern, ein nicht existierender Pfad
ebenso („error gathering device information"). Findet das Skript kein
`/dev/ttyUSB*` und kein `/dev/ttyACM*`, sagt es das deutlich — das Funkmodul
steckt nicht, oder es meldet sich unter anderem Namen — und fragt trotzdem nach
einem Pfad, statt stillschweigend die Vorgabe aus `.env.example` zu übernehmen.
Ein reiner WLAN-Betrieb ohne Thread-Funkmodul ist mit diesem Stack heute nicht
möglich; das ist eine Eigenschaft der Compose-Datei, nicht dieses Skripts, und
steht als bekannte Einschränkung in Abschnitt 11.

Der Rückfall auf `/dev/urandom` für das Token liefert dasselbe Format (64
Zeichen aus `[0-9a-f]`, keine Leerzeichen, ASCII — genau die Anforderung aus
`.env.example`). Er existiert, damit ausgerechnet die Token-Erzeugung eine
sonst gesunde Installation nicht kippen kann.

### Phase 5 — Start

`mkdir -p data` im Stack-Verzeichnis, dann `docker compose up -d --build`. Davor
die Ansage, dass der Bau auf einem Raspberry Pi mehrere Minuten dauert — ohne
sie sieht ein stiller Build wie ein Hänger aus.

### Phase 6 — Prüfen und berichten

Vier Prüfungen, die **nichts verändern**:

1. **Dienst gesund:** `http://127.0.0.1:<port>/health`, bis zu 20 Sekunden
   Geduld, Port aus der Compose-Datei gelesen — dasselbe Vorgehen wie in
   `update.sh`. Antwortet er nicht, werden die letzten 30 Zeilen aus
   `docker logs loxmatter` ausgegeben.
2. **Container:** laufen `otbr`, `matter-server` und `loxmatter`?
3. **Bluetooth:** ist der Adapter rfkill-soft-blockiert? Ermittelt über
   `/sys/class/rfkill/*/type` = `bluetooth` und die zugehörige `soft`-Datei —
   der Index wird gesucht, nicht als `rfkill0` angenommen. Ist er blockiert,
   wird der Befehl aus dem deploy-README **ausgegeben, nicht ausgeführt**,
   mit dem tatsächlich gefundenen Index.
4. **Thread:** gibt es eine `wpan*`-Schnittstelle mit Mesh-Adresse in
   `/proc/net/if_inet6`? Dieselbe Prüfung, die `scripts/otbr-watchdog.sh` und
   die Ansicht „System" benutzen. Fehlt sie, wird der
   `start-stop-daemon`-Workaround als Befehlsblock ausgegeben — mit den Werten
   aus der gerade geschriebenen `.env` eingesetzt — samt dem Hinweis, dass er
   nach **jedem** `compose up` erneut nötig ist, bis das OTBR-Image ersetzt wird.

Prüfung 3 und 4 sind Befunde, kein Abbruchgrund: ein Stack ohne Thread-Netz ist
für reine WLAN-Matter-Geräte vollständig brauchbar.

### Phase 7 — Schlussbericht

- LAN-Adresse wie in `update.sh` (`hostname -I | awk '{print $1}'`) und die
  WebUI-URL.
- „Open it and set a password — until you do, no `/api` route answers."
- Der Watchdog-Cron-Vorschlag, mit dem **tatsächlichen** Installationspfad, nicht
  dem `/home/pi/matter-loxone` aus dem deploy-README.
- `scripts/update.sh` als Weg für später.
- Falls Docker in diesem Lauf installiert wurde: der Hinweis auf die
  Neuanmeldung.
- Alle offenen Befunde aus Phase 6 noch einmal gesammelt, damit sie nicht
  zwischen den Build-Zeilen verschwinden.

## 5. Fehlerverhalten

`set -eu`. Kein `pipefail` — dash kennt es nicht; wo eine Pipeline zählt, wird
das Ergebnis ausdrücklich geprüft.

Eine Variable hält die Beschreibung des laufenden Schritts. Ein `EXIT`-Trap gibt
sie bei jedem unerwarteten Abbruch aus, zusammen mit dem, was bereits geschehen
ist und was nicht:

```
Failed while: writing .env
The checkout at /home/pi/loxmatter exists; nothing was started.
```

**Kein Rollback.** Ein Skript, das auf einem fremden Host aufräumt, richtet mehr
Schaden an als der halbe Zustand, den es beseitigen will. Stattdessen benennt
jede Abbruchmeldung den erreichten Punkt, und ein erneuter Lauf nimmt ihn auf.

## 6. Idempotenz

Ein zweiter Lauf:

- klont nicht erneut und zieht nicht,
- lässt bestehende `.env`-Werte unangetastet und ergänzt nur fehlende,
- ruft `docker compose up -d --build` erneut auf, was von sich aus idempotent
  ist,
- führt die Prüfungen aus Phase 6 erneut aus,
- und prüft zusätzlich per `git fetch`, ob `main` weiter ist. Wenn ja:
  Rückfrage über `/dev/tty` („N new commits available. Update now? [y/N]"). Bei
  Zustimmung läuft `scripts/update.sh`, das die Signaldatenbank vorher sichert.
  Ohne TTY entfällt die Frage, der Hinweis bleibt.

Damit ist der Wiederholungslauf sowohl die Reparatur eines abgebrochenen ersten
Laufs als auch der bequeme Weg zum Update — ohne die Sicherung zu umgehen, die
`update.sh` mitbringt.

## 7. Was das Skript ausdrücklich nicht tut

- Es entsperrt rfkill nicht selbst. Der Befehl braucht einen privilegierten
  Container mit `/sys`-Einhängung; das ungefragt zu tun ist genau die stille
  Root-Aktion, die dieser Entwurf ausschließt.
- Es wendet den otbr-Workaround nicht selbst an. Er ist kernelspezifisch und
  würde auf Hosts, die ihn nicht brauchen, laufende Prozesse killen.
- Es trägt den Watchdog-Cron nicht selbst ein.
- Es installiert kein TLS und ändert nichts an den Sicherheitseigenschaften des
  Stacks. Die Warnhinweise aus dem README-Entwurf gelten unverändert.
- Es fasst `deploy/testhost/README.md` und `README.md` nicht an (siehe
  Abschnitt 10).

## 8. Dateien

| Datei | Änderung |
|---|---|
| `install.sh` | neu — Wurzelverzeichnis, POSIX `sh`, GPL-Kopf, durchgehend englisch |
| `tests/test_install_script.py` | neu — Tests mit gefälschten Binaries, siehe Abschnitt 9 |
| `.github/workflows/ci.yml` | ein Schritt `shellcheck -s sh install.sh` |
| `docs/superpowers/specs/2026-09-05-install-oneliner-design.md` | dieses Dokument |

`shellcheck` läuft zunächst nur gegen `install.sh`. Die vorhandenen Bash-Skripte
(`scripts/update.sh`, `scripts/otbr-watchdog.sh`) mitzuprüfen fördert vermutlich
Altbefunde zutage — das wäre eine eigene Aufgabe.

## 9. Teststrategie

`tests/test_install_script.py` legt ein temporäres `HOME` an und einen `PATH`
mit Stubs für `docker`, `git`, `sudo`, `apt-get`, `ip`, `uname`, `curl`. Jeder
Stub schreibt seinen Aufruf in eine Protokolldatei und endet erfolgreich. Ohne
`/dev/tty` läuft der nicht-interaktive Zweig, die Werte kommen aus der Umgebung.

Geprüfte Fälle:

| Fall | Erwartung |
|---|---|
| `uname`-Stub meldet `Darwin` | Abbruch, kein Verzeichnis angelegt, Exit ≠ 0 |
| `git`/`curl`/`openssl` fehlen | genau diese drei im `apt-get`-Aufruf, keine weiteren |
| Docker fehlt | `get.docker.com` erst **nach** `apt-get`; danach `sudo docker` in allen Folgeaufrufen |
| kein `sudo`, kein root, Docker fehlt | Abbruch in Phase 1; `git clone` steht **nicht** im Protokoll |
| `MINISERVER_IP` fehlt, kein TTY | Abbruch vor dem Klon |
| zweiter Lauf bei vollständiger `.env` | kein `git clone`, `.env` byte-identisch, `compose up -d` erneut |
| `.env` mit `MINISERVER_IP`, ohne `LOXMATTER_API_TOKEN` | nur die fehlende Zeile kommt dazu, die vorhandene bleibt |
| `--dry-run` | kein einziger Stub wird aufgerufen |

**Was diese Tests nicht leisten:** Sie prüfen die *Auswahl* der Befehle, nicht
ihre Wirkung. Ob `docker compose up -d --build` auf einem Pi tatsächlich einen
gesunden Stack ergibt, zeigt nur ein Lauf auf einem Pi. Das steht hier, damit es
später niemand für mehr hält, als es ist — die Vorlage dafür ist der Fehler in
`scripts/update.sh`, das monatelang ein Image baute, das nirgends ankam, und
trotzdem „Fertig" meldete.

## 10. Übergabe an die README-Produktseite

Die README wird in einer eigenen Session zu einer englischen Produktseite
umgebaut (siehe
[2026-09-05-readme-produktseite-design.md](2026-09-05-readme-produktseite-design.md),
Abschnitt 7: „Das One-Liner-Installskript entsteht in einer eigenen Session […]
wer das Skript baut, zieht Schritt 1 nach"). Zum Zeitpunkt dieses Entwurfs liegt
jene Spec auf einem eigenen Branch, die README selbst ist unverändert.

**Diese Session fasst deshalb weder `README.md` noch den fremden Branch an.**
Stattdessen steht der fertige Wortlaut hier und wird von der README-Session in
Abschnitt 6 („🚀 Quickstart") der Produktseite übernommen.

### Wortlaut für den Quickstart (englisch, zum Übernehmen)

> ## 🚀 Quickstart
>
> One command on the machine that will run the bridge — a Raspberry Pi or any
> Debian-based Linux host:
>
> ```bash
> curl -fsSL https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh | sh
> ```
>
> It asks for your Miniserver's IP address, detects the rest (network
> interface, Thread radio, Bluetooth adapter), and starts the three
> containers. When it finishes it prints the address of the web interface.
>
> **Prefer to read it first?** Same script, three lines:
>
> ```bash
> curl -fsSLO https://raw.githubusercontent.com/lucienkerl/loxmatter/main/install.sh
> less install.sh
> sh install.sh
> ```
>
> The script installs `git`, `curl`, `openssl` and Docker if they are missing,
> which needs `sudo`. It tells you before it does, but it does not ask. Add
> `--dry-run` (`… | sh -s -- --dry-run`) to see every step without changing
> anything.
>
> **Then open `http://<host>:8080/` and set a password.** Until you do, no
> `/api` route answers — there is no open state.
>
> Running it again is safe: it keeps your configuration, re-checks the stack,
> and offers to update if there are new commits. The full manual path, and the
> two Raspberry-Pi-specific steps the script reports but does not perform, are
> in [docs/SETUP.md](docs/SETUP.md).

Die beiden Pi-Schritte, auf die der letzte Absatz verweist, stehen heute in
[`deploy/testhost/README.md`](../../../deploy/testhost/README.md) („Bluetooth-Adapter
ist rfkill-soft-blocked" und „start-stop-daemon haengt auf dem Pi-Kernel") und
wandern mit dem README-Umbau nach `docs/SETUP.md`.

## 11. Abgrenzung

- Kein veröffentlichtes Container-Image. Der Stack baut `loxmatter` weiterhin aus
  dem Klon (`context: ../..`); ein Image in einer Registry wäre eine eigene
  Aufgabe und würde den Klon überflüssig machen.
- Keine Änderung an Anwendungscode, an `docker-compose.yml` oder an `.env.example`.
- Keine Änderung an `scripts/update.sh` oder `scripts/otbr-watchdog.sh`.
- Kein `systemd`-Dienst, keine automatische Watchdog-Einrichtung.
- Keine Unterstützung für andere Paketverwaltungen als `apt-get`.
- **Kein Betrieb ohne Thread-Funkmodul.** Die Compose-Datei reicht `RADIO_DEVICE`
  hart als Gerät durch, ohne das `docker compose up` scheitert — auch für
  Installationen, die nur WLAN-Matter-Geräte anbinden wollen. Das zu ändern
  hieße, den `otbr`-Dienst optional zu machen (Compose-Profile), und gehört in
  eine eigene Aufgabe an der Compose-Datei, nicht in das Installskript.

## 12. Risiken

| Risiko | Umgang |
|---|---|
| `get.docker.com` ändert sein Verhalten oder ist nicht erreichbar | Fehler wird als eigener Schritt gemeldet („Failed while: installing Docker"), der Klon existiert dann noch nicht |
| Die erkannten Vorgaben sind falsch (mehrere USB-Geräte, mehrere Interfaces) | Jeder erkannte Wert ist ein Vorschlag in einer Rückfrage, keine stille Festlegung |
| Ein nicht-interaktiver Lauf trifft stillschweigend falsche Annahmen | Ohne TTY wird nichts geraten: fehlt `MINISERVER_IP`, bricht Phase 1 ab |
| Der Nutzer hält „Fertig" für „Thread läuft" | Phase 6 prüft `wpan*` ausdrücklich und wiederholt den Befund im Schlussbericht |
| Die Stub-Tests wiegen in falscher Sicherheit | Abschnitt 9 benennt die Grenze; Abnahme auf einem Pi bleibt Voraussetzung |
| `sudo docker` im selben Lauf verdeckt, dass die Gruppe noch nicht greift | Der Schlussbericht fordert die Neuanmeldung ausdrücklich ein |
